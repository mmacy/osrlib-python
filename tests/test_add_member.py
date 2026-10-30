"""Acceptance tests for `AddMember`, the referee command that adds a character to a running party.

Issue #172. A running session had no way to add a party member, because only
`GameSession.new` assigned member ids. So a game couldn't bring in a replacement for a
fallen character, and the retainer rules had nothing to build on. This chunk is the
command alone. The retainer rules come later, on top of it.

The contract these tests pin:

- `AddMember(character=...)` is a referee command, legal in `town` and `exploring`
  only. In `encounter`, `battle`, `game_over`, and `victory` the session refuses it with
  `session.command.wrong_mode`.
- The character must arrive fresh: no id (`party.add.id_assigned`), no condition or stat
  modifier (`party.add.under_effects`), and no magic item or valuable
  (`party.add.carries_instances`). The session reports the first of these that fails.
  Every refusal costs nothing: no draws, no time, no log entry, and no change.
- An accepted command adds a copy of the character at the back of the marching order,
  with the next `character-NNNN` id from the session's allocator, passing over ids that
  members already have. It makes no draws and takes no time, and it emits one
  player-visible `MemberAddedEvent` with the code `session.member.added`.
- Coins the new member brings into a dungeon aren't treasure the party found.
- Saves, the player view, and the referee view include the new member, and a replay of
  the command log adds the same member with the same id.
- The new member plays: later commands address them by id, they slow the party when they
  are slower, and they declare in battle.

The tests in `TestTheContract` check the command and event models, and the two mode tests
in `TestRefusals` check the session's mode gate. All of these exist before the handler
does, so they pass on the stubs and carry no marker. Every other test is marked for the
chunk.
"""

import json

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import TypeAdapter

from crawl_fixtures import build_adventure, build_party
from osrlib.core.abilities import AbilityScore
from osrlib.core.character import Character, party_to_document
from osrlib.core.clock import TimeUnit
from osrlib.core.effects import ActiveCondition, ActiveModifier, Condition
from osrlib.core.events import Visibility
from osrlib.core.items import CoinPurse, Inventory, MagicItemInstance, ValuableInstance, equip, purchase
from osrlib.core.ruleset import Ruleset
from osrlib.core.tables import ReactionResult
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import (
    ALL_COMMAND_CLASSES,
    CONSEQUENCE_COMMAND_CLASSES,
    AddMember,
    AdvanceTime,
    AnyCommand,
    AwardXP,
    BattleDeclaration,
    EngageBattle,
    EnterDungeon,
    GrantCoins,
    GrantItem,
    MoveParty,
    PlaceParty,
    RemoveCoins,
    ReorderParty,
    ResolveBattleRound,
    SessionMode,
    TravelToTown,
    parse_command,
)
from osrlib.crawl.dungeon import (
    AreaSpec,
    Direction,
    DungeonSpec,
    Edge,
    EdgeKind,
    KeyedEncounter,
    KeyedMonster,
    LevelSpec,
    WanderingSpec,
)
from osrlib.crawl.events import AdventureXpAwardEvent, MemberAddedEvent, parse_any_event
from osrlib.crawl.exploration import exploration_rate
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.data import load_classes, load_equipment
from osrlib.messages import format_message
from osrlib.persistence import load_game, replay_game, save_game, session_state
from test_crawl_properties import command_strategy
from test_terminal_modes import poison_everyone

CHUNK = pytest.mark.xfail(reason="chunk: add-member", strict=True)

SEED = 41
STOCK_IDS = ("character-0001", "character-0002", "character-0003", "character-0004")
"""The ids `GameSession.new` gives the stock four-member party, in marching order."""
NEW = "character-0005"
"""The id the first added member gets in a session with the stock party."""
NEXT = "character-0006"
"""The id the second added member gets."""

FIELD_MODES = ("town", "exploring")
REFUSED_MODES = ("encounter", "battle", "game_over", "victory")


# ---------------------------------------------------------------------- fixtures


def recruit(name: str = "Ylva", class_id: str = "fighter", *, gp: int = 0, armour: str | None = None) -> Character:
    """A fresh level 1 character with no id, the shape `AddMember` takes.

    Args:
        name: The character's name.
        class_id: The character's class.
        gp: Gold pieces left in the purse after any armour is bought.
        armour: A catalog id of body armour to buy and wear, or `None` for none.
    """
    definition = load_classes().get(class_id)
    inventory = Inventory()
    if armour is not None:
        template = load_equipment().get(armour)
        inventory.purse = CoinPurse(gp=template.cost_gp)
        equip(inventory, definition, purchase(inventory, template))
    inventory.purse = CoinPurse(gp=gp)
    return Character(
        name=name,
        class_id=class_id,
        race=definition.race,
        level=1,
        xp=0,
        scores={ability: 12 for ability in AbilityScore},
        alignment="neutral",
        max_hp=7,
        current_hp=7,
        inventory=inventory,
    )


def quiet_adventure() -> Adventure:
    """The shared two-level delve, with no wandering monsters."""
    return build_adventure(wandering_chance=0)


def guard_adventure() -> Adventure:
    """One level, a corridor three cells long, entered at (0, 0), with two goblins at (2, 0).

    The goblins expect the party and are pinned indifferent, so walking in always opens an
    encounter and never a battle. They keep no hoard, and the level has no wandering monsters.
    """
    edges = {f"{x},0:west": Edge(kind=EdgeKind.OPEN) for x in range(1, 3)}
    guardroom = AreaSpec(
        id="guardroom",
        name="Guardroom",
        cells=((2, 0),),
        encounter=KeyedEncounter(
            monsters=(KeyedMonster(template_id="goblin", count_fixed=2),),
            aware=True,
            stance=ReactionResult.INDIFFERENT,
            hoard=False,
        ),
    )
    level = LevelSpec(
        number=1,
        width=3,
        height=1,
        entrance=(0, 0),
        edges=edges,
        areas=(guardroom,),
        wandering=WanderingSpec(chance_in_six=0),
    )
    return Adventure(
        name="The Guardroom",
        town=TownSpec(name="Threshold", travel_turns={"crypt": 1}),
        dungeons=(DungeonSpec(id="crypt", name="Crypt", levels=(level,)),),
    )


def new_session(adventure: Adventure | None = None) -> GameSession:
    """A session in town with the stock party and no listener."""
    session = GameSession.new(build_party(), adventure if adventure is not None else quiet_adventure(), seed=SEED)
    assert tuple(member.id for member in session.party.members) == STOCK_IDS
    return session


def enter(session: GameSession, dungeon_id: str = "delve") -> None:
    assert session.execute(EnterDungeon(dungeon_id=dungeon_id)).accepted
    assert session.mode is SessionMode.EXPLORING


def into_guardroom(session: GameSession) -> None:
    """From the entrance, walk into the goblins' room and open the encounter."""
    for _ in range(2):
        assert session.execute(MoveParty(direction=Direction.EAST)).accepted
    assert session.mode is SessionMode.ENCOUNTER


def engage(session: GameSession) -> None:
    assert session.execute(EngageBattle()).accepted
    assert session.mode is SessionMode.BATTLE


def wipe(session: GameSession) -> None:
    """Poison every member, and let one turn pass so the session ends in `game_over`."""
    poison_everyone(session)
    assert session.execute(AdvanceTime(n=1, unit=TimeUnit.TURN)).accepted
    assert session.mode is SessionMode.GAME_OVER
    assert not session.party.living_members()


def session_in(mode: str) -> GameSession:
    """A session with the stock party in `mode`, reached by play where play can reach it."""
    if mode == "town":
        session = new_session()
    elif mode == "exploring":
        session = new_session()
        enter(session)
    elif mode in ("encounter", "battle"):
        session = new_session(guard_adventure())
        enter(session, "crypt")
        into_guardroom(session)
        if mode == "battle":
            engage(session)
    elif mode == "game_over":
        session = new_session()
        wipe(session)
    else:
        # Only a concluding quest enters `victory`, so the tests assign the mode directly,
        # as the terminal-mode tests do.
        session = new_session()
        session.mode = SessionMode.VICTORY
    assert session.mode.value == mode
    return session


def stream_states(session: GameSession) -> dict:
    return {key: state.model_dump(mode="json") for key, state in session.streams.export_states().items()}


def frozen_state(session: GameSession) -> str:
    return json.dumps(session_state(session), sort_keys=True)


def assert_refused(session: GameSession, command: AddMember, code: str) -> None:
    """The command is refused with `code` alone, at no cost: no draws, no time, no change."""
    state = frozen_state(session)
    streams = stream_states(session)
    rounds = session.clock.rounds
    logged = len(session.command_log)
    members = len(session.party.members)
    result = session.execute(command)
    assert not result.accepted
    assert [rejection.code for rejection in result.rejections] == [code]
    assert list(result.events) == []
    assert stream_states(session) == streams, "a refusal draws nothing"
    assert session.clock.rounds == rounds, "a refusal spends no time"
    assert len(session.command_log) == logged, "a refusal is not logged"
    assert len(session.party.members) == members, "a refusal adds nobody"
    assert frozen_state(session) == state, "a refusal changes nothing"


def add(session: GameSession, character: Character | None = None) -> MemberAddedEvent:
    """Add a member and return the one event the command emits."""
    result = session.execute(AddMember(character=character if character is not None else recruit()))
    assert result.accepted, result.rejections
    assert len(result.events) == 1
    event = result.events[0]
    assert isinstance(event, MemberAddedEvent)
    return event


def member_ids(session: GameSession) -> tuple[str | None, ...]:
    return tuple(member.id for member in session.party.members)


def replayed(session: GameSession, adventure: Adventure) -> GameSession:
    """A listener-free replay of the session's log, from serialized commands."""
    return replay_game(
        SEED,
        party_to_document(build_party().members),
        adventure,
        Ruleset(),
        [json.loads(json.dumps(command.model_dump(mode="json"))) for command in session.command_log],
    )


# ---------------------------------------------------------------------- the models


class TestTheContract:
    """The command and event models. These exist before the handler, so they carry no marker."""

    def test_it_is_appended_after_the_removal_commands(self):
        assert ALL_COMMAND_CLASSES.index(AddMember) == ALL_COMMAND_CLASSES.index(RemoveCoins) + 1
        assert AddMember.model_fields["command_type"].default == "add_member"

    def test_it_is_legal_in_town_and_exploring_only(self):
        assert AddMember.allowed_modes == frozenset({SessionMode.TOWN, SessionMode.EXPLORING})

    def test_it_is_not_an_authored_consequence(self):
        assert AddMember not in CONSEQUENCE_COMMAND_CLASSES

    def test_it_round_trips_through_json(self):
        command = AddMember(character=recruit(gp=12, armour="leather"), source="game:tavern")
        payload = json.loads(json.dumps(command.model_dump(mode="json")))
        assert payload["command_type"] == "add_member"
        assert payload["source"] == "game:tavern"
        assert payload["character"]["id"] is None
        assert payload["character"]["name"] == "Ylva"
        parsed = parse_command(payload)
        assert parsed == command
        assert type(parsed) is AddMember
        assert TypeAdapter(AnyCommand).validate_python(payload) == command

    def test_constructing_the_command_copies_the_character(self):
        hire = recruit()
        command = AddMember(character=hire)
        assert command.character is not hire
        hire.current_hp = 2
        hire.name = "Somebody else"
        assert command.character.current_hp == 7
        assert command.character.name == "Ylva"

    def test_the_event_round_trips_and_renders(self):
        event = MemberAddedEvent(character_id=NEW, name="Ylva", class_id="fighter", level=1)
        assert event.event_type == "member_added"
        assert event.code == "session.member.added"
        assert MemberAddedEvent.allowed_codes == frozenset({"session.member.added"})
        assert event.visibility is Visibility.PLAYER
        assert parse_any_event(json.loads(json.dumps(event.model_dump(mode="json")))) == event
        assert format_message(event) == "Ylva, a level 1 fighter, joins the party as character-0005."


# ---------------------------------------------------------------------- acceptance


class TestAccepted:
    @CHUNK
    @pytest.mark.parametrize("mode", FIELD_MODES)
    def test_the_member_joins_at_the_back_of_the_marching_order(self, mode):
        session = session_in(mode)
        location = session.dungeon_state.location.model_dump(mode="json")
        event = add(session, recruit("Ylva", "cleric"))
        assert event == MemberAddedEvent(character_id=NEW, name="Ylva", class_id="cleric", level=1)
        assert member_ids(session) == (*STOCK_IDS, NEW)
        assert session.member(NEW).name == "Ylva"
        assert session.mode.value == mode, "adding a member changes no mode"
        assert session.dungeon_state.location.model_dump(mode="json") == location, "and moves nobody"

    @CHUNK
    @pytest.mark.parametrize("mode", FIELD_MODES)
    def test_it_draws_nothing_takes_no_time_and_is_logged(self, mode):
        session = session_in(mode)
        streams = stream_states(session)
        rounds = session.clock.rounds
        command = AddMember(character=recruit())
        assert session.execute(command).accepted
        assert stream_states(session) == streams
        assert session.clock.rounds == rounds
        assert session.command_log[-1] is command

    @CHUNK
    def test_the_event_is_player_visible_and_logged(self):
        session = new_session()
        event = add(session)
        assert event.visibility is Visibility.PLAYER
        assert session.event_log[-1] == event
        assert format_message(event) == "Ylva, a level 1 fighter, joins the party as character-0005."

    @CHUNK
    def test_the_session_adds_a_copy_and_leaves_the_command_as_it_arrived(self):
        session = new_session()
        hire = recruit(gp=30, armour="chainmail")
        command = AddMember(character=hire)
        assert session.execute(command).accepted
        member = session.member(NEW)
        assert member is not hire
        assert member is not command.character
        assert hire.id is None, "the caller's object gets no id"
        assert command.character.id is None, "the logged command keeps the character as it arrived"
        assert member.model_dump(mode="json", exclude={"id"}) == hire.model_dump(mode="json", exclude={"id"})
        member.current_hp = 1
        member.inventory.purse.gp = 0
        assert command.character.current_hp == 7
        assert command.character.inventory.purse.gp == 30

    @CHUNK
    def test_mundane_gear_and_coins_come_along(self):
        session = new_session()
        hire = recruit(gp=25, armour="plate_mail")
        add(session, hire)
        member = session.member(NEW)
        assert member.inventory.worn_armour is not None
        assert member.inventory.worn_armour.template.id == "plate_mail"
        assert member.inventory.purse.gp == 25
        assert member.armour_class == hire.armour_class


class TestIds:
    @CHUNK
    def test_the_new_id_is_the_next_character_id(self):
        session = new_session()
        assert session.allocator.counters["character"] == len(STOCK_IDS)
        assert add(session).character_id == NEW
        assert session.allocator.counters["character"] == len(STOCK_IDS) + 1

    @CHUNK
    def test_two_members_take_consecutive_ids(self):
        session = new_session()
        assert add(session, recruit("Ylva")).character_id == NEW
        assert add(session, recruit("Orm")).character_id == NEXT
        assert member_ids(session) == (*STOCK_IDS, NEW, NEXT)
        assert [member.name for member in session.party.members[-2:]] == ["Ylva", "Orm"]

    @CHUNK
    def test_the_id_passes_over_ids_members_already_have(self):
        # `GameSession.new` keeps an id a member already has, such as one from an earlier
        # session, and allocates nothing for it. So the allocator's next id can be taken.
        members = build_party().members[:2]
        members[0].id = "character-0001"
        members[1].id = "character-0002"
        session = GameSession.new(Party(members=members), quiet_adventure(), seed=SEED)
        assert session.allocator.counters.get("character", 0) == 0
        event = add(session)
        assert event.character_id == "character-0003"
        assert member_ids(session) == ("character-0001", "character-0002", "character-0003")

    @CHUNK
    def test_no_entity_of_another_kind_takes_an_id(self):
        session = new_session()
        counters = dict(session.allocator.counters)
        add(session)
        counters["character"] += 1
        assert session.allocator.counters == counters


class TestMarchingOrder:
    @CHUNK
    def test_the_front_rank_stays_as_it_was(self):
        session = new_session()
        front = [member.id for member in session.party.ranks(2)[0]]
        add(session)
        ranks = session.party.ranks(2)
        assert [member.id for member in ranks[0]] == front
        assert ranks[-1][-1].id == NEW

    @CHUNK
    def test_a_dead_member_ahead_stays_in_place(self):
        from osrlib.core.effects import kill

        session = new_session()
        kill(session.member(STOCK_IDS[3]))
        add(session)
        assert member_ids(session) == (*STOCK_IDS, NEW)
        assert [member.id for member in session.party.living_members()] == [*STOCK_IDS[:3], NEW]

    @CHUNK
    @pytest.mark.parametrize("mode", FIELD_MODES)
    def test_reorder_party_can_move_the_new_member_to_the_front(self, mode):
        session = session_in(mode)
        add(session)
        order = (NEW, *STOCK_IDS)
        assert session.execute(ReorderParty(order=order)).accepted
        assert member_ids(session) == order
        assert session.party.ranks(2)[0][0].id == NEW


# ---------------------------------------------------------------------- refusals


class TestRefusals:
    # The session's mode gate runs before any handler, so the two mode tests hold on the stub
    # and carry no marker.

    @pytest.mark.parametrize("mode", REFUSED_MODES)
    def test_every_other_mode_refuses_it(self, mode):
        session = session_in(mode)
        assert_refused(session, AddMember(character=recruit()), "session.command.wrong_mode")

    @CHUNK
    def test_a_character_with_an_id_is_refused(self):
        session = new_session()
        hire = recruit()
        hire.id = "character-0099"
        assert_refused(session, AddMember(character=hire), "party.add.id_assigned")

    @CHUNK
    def test_a_current_member_is_refused(self):
        session = new_session()
        assert_refused(session, AddMember(character=session.member(STOCK_IDS[0])), "party.add.id_assigned")

    @CHUNK
    def test_a_dead_character_is_refused(self):
        session = new_session()
        hire = recruit()
        hire.conditions = (ActiveCondition(condition=Condition.DEAD),)
        assert_refused(session, AddMember(character=hire), "party.add.under_effects")

    @CHUNK
    def test_a_character_under_a_timed_condition_is_refused(self):
        session = new_session()
        hire = recruit()
        hire.conditions = (ActiveCondition(condition=Condition.ASLEEP, effect_id="effect-0001"),)
        assert_refused(session, AddMember(character=hire), "party.add.under_effects")

    @CHUNK
    def test_a_character_with_a_stat_modifier_is_refused(self):
        session = new_session()
        hire = recruit()
        hire.stat_modifiers = (ActiveModifier(kind="attack_bonus", value=1, effect_id="effect-0001"),)
        assert_refused(session, AddMember(character=hire), "party.add.under_effects")

    @CHUNK
    def test_a_carried_magic_item_is_refused(self):
        session = new_session()
        hire = recruit()
        hire.inventory.items.append(MagicItemInstance(instance_id="magic-item-0001", template_id="potion_of_healing"))
        assert_refused(session, AddMember(character=hire), "party.add.carries_instances")

    @CHUNK
    def test_an_equipped_magic_item_is_refused(self):
        session = new_session()
        hire = recruit()
        hire.inventory.rings = [MagicItemInstance(instance_id="magic-item-0001", template_id="ring_of_protection")]
        assert_refused(session, AddMember(character=hire), "party.add.carries_instances")

    @CHUNK
    def test_a_valuable_is_refused(self):
        session = new_session()
        hire = recruit()
        hire.inventory.valuables = [ValuableInstance(instance_id="valuable-0001", kind="gem", value_gp=50)]
        assert_refused(session, AddMember(character=hire), "party.add.carries_instances")

    @CHUNK
    def test_the_first_failing_check_is_the_one_reported(self):
        session = new_session()
        hire = recruit()
        hire.inventory.valuables = [ValuableInstance(instance_id="valuable-0001", kind="gem", value_gp=50)]
        hire.conditions = (ActiveCondition(condition=Condition.DEAD),)
        assert_refused(session, AddMember(character=hire), "party.add.under_effects")
        hire.id = "character-0099"
        assert_refused(session, AddMember(character=hire), "party.add.id_assigned")

    def test_the_mode_is_checked_before_the_character(self):
        session = session_in("encounter")
        hire = recruit()
        hire.id = "character-0099"
        assert_refused(session, AddMember(character=hire), "session.command.wrong_mode")


# ---------------------------------------------------------------------- treasure


class TestTreasure:
    @CHUNK
    def test_coins_brought_into_a_dungeon_earn_no_experience(self):
        session = new_session()
        enter(session)
        snapshot = session.treasure_snapshot_cp
        assert snapshot is not None
        add(session, recruit(gp=500))
        assert session.treasure_snapshot_cp == snapshot + 500 * 100
        result = session.execute(TravelToTown())
        assert result.accepted
        assert not [event for event in result.events if isinstance(event, AdventureXpAwardEvent)]
        assert session.member(NEW).xp == 0

    @CHUNK
    def test_coins_brought_in_town_leave_the_snapshot_alone(self):
        session = new_session()
        assert session.treasure_snapshot_cp is None
        add(session, recruit(gp=500))
        assert session.treasure_snapshot_cp is None
        enter(session)
        assert session.treasure_snapshot_cp == session.party_valuation_cp()


# ---------------------------------------------------------------------- views, saves, replay


class TestViewsSavesAndReplay:
    @CHUNK
    def test_the_player_view_lists_the_new_member_last(self):
        session = new_session()
        add(session)
        view = session.view(Visibility.PLAYER)
        assert tuple(sheet.id for sheet in view.party) == (*STOCK_IDS, NEW)
        sheet = view.party[-1]
        assert (sheet.name, sheet.class_id, sheet.level, sheet.current_hp, sheet.max_hp) == ("Ylva", "fighter", 1, 7, 7)

    @CHUNK
    def test_the_referee_view_has_the_whole_sheet(self):
        session = new_session()
        add(session, recruit(gp=10, armour="leather"))
        view = session.view(Visibility.REFEREE)
        assert view.party.members[-1] == session.member(NEW)
        assert view.party.members[-1] is not session.member(NEW)

    @CHUNK
    @pytest.mark.parametrize("mode", FIELD_MODES)
    def test_a_save_round_trips_the_new_member(self, mode):
        session = session_in(mode)
        add(session, recruit(gp=10, armour="leather"))
        restored = load_game(json.loads(json.dumps(save_game(session))))
        assert session_state(restored) == session_state(session)
        assert restored.member(NEW) == session.member(NEW)
        assert add(restored, recruit("Orm")).character_id == NEXT, "the restored allocator continues the count"

    @CHUNK
    def test_a_replay_adds_the_same_member_with_the_same_id(self):
        session = new_session()
        enter(session)
        add(session, recruit(gp=10, armour="leather"))
        assert session.execute(AwardXP(character_id=NEW, amount=250)).accepted
        assert session.execute(MoveParty(direction=Direction.EAST)).accepted
        add(session, recruit("Orm", "thief"))
        again = replayed(session, quiet_adventure())
        assert member_ids(again) == (*STOCK_IDS, NEW, NEXT)
        assert session_state(again) == session_state(session)

    @CHUNK
    def test_a_replay_from_the_live_log_shares_no_character_with_the_original(self):
        session = new_session()
        add(session)
        again = replay_game(
            SEED, party_to_document(build_party().members), quiet_adventure(), Ruleset(), session.command_log
        )
        assert again.member(NEW) is not session.member(NEW)
        session.member(NEW).current_hp = 1
        assert again.member(NEW).current_hp == 7


# ---------------------------------------------------------------------- the new member plays


class TestTheNewMemberPlays:
    @CHUNK
    def test_later_referee_commands_address_the_new_member(self):
        session = new_session()
        add(session)
        for command in (
            GrantItem(character_id=NEW, item_id="torch", quantity=6),
            GrantCoins(character_id=NEW, coins={"gp": 40}),
            RemoveCoins(character_id=NEW, coins={"gp": 15}),
            AwardXP(character_id=NEW, amount=100),
        ):
            assert session.execute(command).accepted, command.command_type
        member = session.member(NEW)
        assert member.inventory.purse.gp == 25
        assert member.xp == 100
        assert any(instance.template.id == "torch" for instance in member.inventory.all_instances())

    @CHUNK
    def test_the_new_member_moves_with_the_party(self):
        session = new_session()
        enter(session)
        assert exploration_rate(session) == 120
        add(session, recruit(armour="plate_mail"))
        assert exploration_rate(session) == 60, "the party walks at the pace of its slowest member"
        result = session.execute(MoveParty(direction=Direction.EAST))
        assert result.accepted
        assert session.dungeon_state.location.position == (1, 0)
        assert member_ids(session) == (*STOCK_IDS, NEW)

    @CHUNK
    def test_the_new_member_declares_in_battle(self):
        session = new_session(guard_adventure())
        enter(session, "crypt")
        add(session)
        into_guardroom(session)
        engage(session)
        declarers = session.view(Visibility.PLAYER).encounter.declarers
        assert NEW in declarers
        assert declarers[-1] == NEW
        holds = tuple(BattleDeclaration(character_id=member_id, action="hold") for member_id in declarers)
        assert session.execute(ResolveBattleRound(declarations=holds)).accepted

    @CHUNK
    def test_a_wiped_party_is_replaced_in_town_and_plays_on(self):
        session = session_in("game_over")
        assert session.execute(PlaceParty(location={"kind": "town"})).accepted
        assert session.mode is SessionMode.TOWN
        add(session, recruit("Ylva"))
        assert [member.id for member in session.party.living_members()] == [NEW]
        enter(session)
        assert session.execute(MoveParty(direction=Direction.EAST)).accepted


# ---------------------------------------------------------------------- the fuzz contract


@CHUNK
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    seed=st.integers(min_value=0, max_value=2**32),
    commands=st.lists(command_strategy(include_pending=True), min_size=1, max_size=25),
)
def test_fuzzed_sequences_with_added_members_never_raise_and_round_trip(seed, commands):
    """Schema-valid commands, `AddMember` among them, reject and never throw. Ids stay unique.

    A fresh member joins in town first, so every example has one, and the fuzz can address
    them as `character-0005`.
    """
    session = GameSession.new(build_party(), build_adventure(), seed=seed)
    add(session)
    for command in commands:
        session.execute(command)  # must never raise
        ids = [member.id for member in session.party.members]
        assert None not in ids
        assert len(set(ids)) == len(ids)
    restored = load_game(json.loads(json.dumps(save_game(session))))
    assert session_state(restored) == session_state(session)
