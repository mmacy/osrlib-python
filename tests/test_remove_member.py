"""Acceptance tests for `RemoveMember`, the referee command that takes a member out of a running party.

A game built on osrlib needs to drop a character from the party, for example one who died
too long ago for *raise dead*, and bring in a replacement with `AddMember`. No command
removed a member, so a dead character stayed in the party for the rest of the game.

The contract these tests state:

- `RemoveMember(character_id=..., give_to=None)` is a referee command, legal in `town`
  and `exploring` only. In `encounter`, `battle`, `game_over`, and `victory` the session
  refuses it with `session.command.wrong_mode`.
- The member can be alive, dead, turned to stone, or asleep.
- The refusals, in order: `session.command.unknown_member` (`character_id` or `give_to`
  matches no member), `party.remove.same_member` (`give_to` is the leaving member),
  `session.command.member_incapacitated` (the `give_to` member can't act), and
  `party.remove.last_standing` (nobody would be left who is neither dead nor turned to
  stone, which includes removing the only member). Every refusal costs nothing: no draws,
  no time, no log entry, and no change.
- An accepted command makes no draws and takes no time. The member leaves the party, and
  the others keep their marching order. The member's id is never handed out again.
- With `give_to`, everything the member carries and all their coins move to that member.
  Equipped items arrive unequipped in the pack, an equipped magic item releases its
  effects, and a revealed cursed item moves too. Without `give_to`, it all leaves the game.
- The session drops the member's ledger effects (with no event), death record, deprivation
  state, and prepared-sleep record. Effects the member cast on others last their full
  duration. A light the member carried goes with them.
- In a dungeon, a removal without `give_to` takes what leaves off the treasure snapshot, so
  the departure doesn't change the treasure experience. With `give_to` the snapshot stays.
- Events, in order: the equipped magic items' `EffectReleasedEvent`s (with `give_to` only),
  one `ItemsGivenEvent` when anything moved, then one player-visible `MemberRemovedEvent`.
- Saves, both views, and a replay agree on the removal, and `AddMember` afterwards gives a
  fresh id.

The tests in `TestTheContract` check the command and event models, and the mode tests in
`TestRefusals` check the session's mode gate. All of these exist before the handler does, so
they pass on the stubs and carry no marker. Every other test is marked for the chunk.
"""

import json

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import TypeAdapter

from crawl_fixtures import build_adventure, build_party
from osrlib.core.character import party_to_document
from osrlib.core.clock import TimeUnit
from osrlib.core.effects import Condition, EffectDefinition, has_condition
from osrlib.core.events import EffectExpiredEvent, EffectReleasedEvent, Visibility
from osrlib.core.items import Coins, MagicItemInstance, ValuableInstance
from osrlib.core.ruleset import Ruleset
from osrlib.core.spells import MemorizedSpell
from osrlib.crawl.commands import (
    ALL_COMMAND_CLASSES,
    CONSEQUENCE_COMMAND_CLASSES,
    AddMember,
    AdvanceTime,
    AnyCommand,
    CastSpell,
    Command,
    EquipItem,
    GrantCoins,
    GrantItem,
    LightSource,
    MoveParty,
    PlaceParty,
    PrepareSpells,
    RemoveMember,
    ReorderParty,
    Rest,
    SessionMode,
    TravelToTown,
    parse_command,
)
from osrlib.crawl.dungeon import Direction
from osrlib.crawl.events import (
    CRAWL_EVENT_CLASSES,
    AdventureXpAwardEvent,
    ItemsGivenEvent,
    MemberAddedEvent,
    MemberRemovedEvent,
    parse_any_event,
)
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.messages import format_message
from osrlib.persistence import load_game, replay_game, save_game, session_state
from test_add_member import (
    NEW,
    SEED,
    STOCK_IDS,
    enter,
    frozen_state,
    member_ids,
    new_session,
    quiet_adventure,
    recruit,
    replayed,
    session_in,
    stream_states,
)
from test_crawl_properties import command_strategy
from test_terminal_modes import POISON_ONSET

CHUNK = pytest.mark.xfail(reason="chunk: remove-member", strict=True)

LEAD, THIEF, CLERIC, MAGE = STOCK_IDS
"""The stock party's ids in marching order: Brakk the fighter, Sable the thief, Wynn the
cleric, and Elara the magic-user."""

FIELD_MODES = ("town", "exploring")
REFUSED_MODES = ("encounter", "battle", "game_over", "victory")

ASLEEP = EffectDefinition(kind="sleep", duration_unit=TimeUnit.TURN, duration_amount=4, condition=Condition.ASLEEP)
"""A *sleep* that lasts long enough to outlast every test that uses it."""

PETRIFIED = EffectDefinition(kind="test_petrification", condition=Condition.PETRIFIED, permanent=True)
"""A permanent turning to stone, as a gaze or a breath leaves it."""


# ---------------------------------------------------------------------- fixtures


def attach(session: GameSession, character_id: str, definition: EffectDefinition) -> None:
    """Put an effect on a member through the session's ledger."""
    session.ledger.attach(
        definition, character_id, clock=session.clock, allocator=session.allocator, registry=session.registry()
    )


def die(session: GameSession, character_id: str) -> None:
    """Poison one member and let a round pass, so they die with a death record."""
    attach(session, character_id, POISON_ONSET)
    assert session.execute(AdvanceTime(n=1, unit=TimeUnit.ROUND)).accepted
    assert has_condition(session.member(character_id), Condition.DEAD)
    assert character_id in session.death_records


def put_to_sleep(session: GameSession, character_id: str) -> None:
    attach(session, character_id, ASLEEP)
    assert has_condition(session.member(character_id), Condition.ASLEEP)


def petrify(session: GameSession, character_id: str) -> None:
    attach(session, character_id, PETRIFIED)
    assert has_condition(session.member(character_id), Condition.PETRIFIED)


def put_in_state(session: GameSession, character_id: str, state: str) -> None:
    """Leave the member `alive`, `dead`, `petrified`, or `asleep`."""
    if state == "dead":
        die(session, character_id)
    elif state == "petrified":
        petrify(session, character_id)
    elif state == "asleep":
        put_to_sleep(session, character_id)


def solo_session() -> GameSession:
    """A session in town whose party is Brakk alone."""
    session = GameSession.new(build_party((("Brakk", "fighter"),)), quiet_adventure(), seed=SEED)
    assert member_ids(session) == (LEAD,)
    return session


def mage_session() -> GameSession:
    """A session in the dungeon whose magic-user has slept and prepared *light*."""
    party = build_party()
    party.members[3].spell_book = ("sleep", "light_mu")
    session = GameSession.new(party, quiet_adventure(), seed=SEED)
    assert session.execute(Rest(kind="night")).accepted
    prepare = PrepareSpells(character_id=MAGE, selections=(MemorizedSpell(spell_id="light_mu"),))
    assert session.execute(prepare).accepted
    enter(session)
    return session


def granted_magic(session: GameSession, character_id: str, item_id: str) -> str:
    """Grant one magic item and return its instance id."""
    result = session.execute(GrantItem(character_id=character_id, item_id=item_id))
    assert result.accepted
    (instance_id,) = result.events[0].item_ids
    return instance_id


def light(session: GameSession, character_id: str, source: str) -> None:
    """Give the member a `torch` or a `lantern` and strike tinder until it lights."""
    session.execute(GrantItem(character_id=character_id, item_id="tinder_box"))
    session.execute(GrantItem(character_id=character_id, item_id=source))
    if source == "lantern":
        session.execute(GrantItem(character_id=character_id, item_id="oil_flask"))
    for _ in range(30):
        result = session.execute(LightSource(character_id=character_id, item_id=source))
        assert result.accepted, result.rejections
        if any(event.code == "exploration.light.lit" for event in result.events):
            break
    assert session.ledger.active_on(character_id, "light"), f"{character_id}'s {source} never lit"


def units(session: GameSession, character_id: str, item_id: str) -> int:
    """How many units of a mundane item the member has, in the pack and in use."""
    return sum(
        instance.quantity
        for instance in session.member(character_id).inventory.all_instances()
        if not isinstance(instance, MagicItemInstance) and instance.template.id == item_id
    )


def pack_ids(session: GameSession, character_id: str) -> list[str]:
    """The ids of what the member carries unequipped in the pack."""
    return [
        instance.instance_id if isinstance(instance, MagicItemInstance) else instance.template.id
        for instance in session.member(character_id).inventory.items
    ]


def equipped_ids(session: GameSession, character_id: str) -> list[str]:
    return [
        instance.instance_id if isinstance(instance, MagicItemInstance) else instance.template.id
        for instance in session.member(character_id).inventory.equipped_instances()
    ]


def load_the_thief(session: GameSession, *, valuables: bool = True) -> dict[str, str]:
    """Kit Sable out with something of every kind, and return the magic items' instance ids.

    Sable carries three torches, wears leather armour, wields a sword, carries a potion of
    healing, wears a ring of regeneration, and carries a gem and a piece of jewellery. Her
    purse holds platinum, gold, and silver.

    Args:
        session: The session.
        valuables: Whether to give Sable the gem and the jewellery. No command grants a
            valuable, so they go straight into her inventory, and a replay doesn't have them.
    """
    for item_id, quantity in (("torch", 3), ("leather", 1), ("sword", 1)):
        assert session.execute(GrantItem(character_id=THIEF, item_id=item_id, quantity=quantity)).accepted
    assert session.execute(EquipItem(character_id=THIEF, item_id="leather")).accepted
    assert session.execute(EquipItem(character_id=THIEF, item_id="sword")).accepted
    potion = granted_magic(session, THIEF, "potion_of_healing")
    ring = granted_magic(session, THIEF, "ring_of_regeneration")
    assert session.execute(EquipItem(character_id=THIEF, item_id=ring)).accepted
    assert session.ledger.active_on(THIEF, "regeneration"), "precondition: the worn ring runs an effect"
    if valuables:
        session.member(THIEF).inventory.valuables.extend(
            [
                ValuableInstance(instance_id="valuable-9001", kind="gem", value_gp=50),
                ValuableInstance(instance_id="valuable-9002", kind="jewellery", value_gp=120),
            ]
        )
    assert session.execute(GrantCoins(character_id=THIEF, coins=Coins(pp=2, gp=40, sp=15))).accepted
    return {"potion": potion, "ring": ring}


THIEF_PURSE = Coins(pp=2, gp=40, sp=15)
"""The coins `load_the_thief` puts in Sable's purse."""


def assert_refused(session: GameSession, command: Command, code: str) -> None:
    """The command is refused with `code` alone, at no cost: no draws, no time, no change."""
    state = frozen_state(session)
    streams = stream_states(session)
    rounds = session.clock.rounds
    logged = len(session.command_log)
    members = member_ids(session)
    result = session.execute(command)
    assert not result.accepted
    assert [rejection.code for rejection in result.rejections] == [code]
    assert list(result.events) == []
    assert stream_states(session) == streams, "a refusal draws nothing"
    assert session.clock.rounds == rounds, "a refusal spends no time"
    assert len(session.command_log) == logged, "a refusal is not logged"
    assert member_ids(session) == members, "a refusal removes nobody"
    assert frozen_state(session) == state, "a refusal changes nothing"


def remove(session: GameSession, character_id: str, give_to: str | None = None) -> list:
    """Remove a member, check the closing event, and return every event the command emitted."""
    name = session.member(character_id).name
    result = session.execute(RemoveMember(character_id=character_id, give_to=give_to))
    assert result.accepted, result.rejections
    assert result.events[-1] == MemberRemovedEvent(character_id=character_id, name=name, given_to=give_to)
    return list(result.events)


def snapshot(session: GameSession) -> dict:
    """The session's whole state as JSON-ready data."""
    return json.loads(json.dumps(session_state(session)))


# ---------------------------------------------------------------------- the models


class TestTheContract:
    """The command and event models. These exist before the handler, so they carry no marker."""

    def test_it_is_appended_after_add_member(self):
        assert ALL_COMMAND_CLASSES.index(RemoveMember) == ALL_COMMAND_CLASSES.index(AddMember) + 1
        assert RemoveMember.model_fields["command_type"].default == "remove_member"

    def test_it_is_legal_in_town_and_exploring_only(self):
        assert RemoveMember.allowed_modes == frozenset({SessionMode.TOWN, SessionMode.EXPLORING})

    def test_it_is_not_an_authored_consequence(self):
        assert RemoveMember not in CONSEQUENCE_COMMAND_CLASSES

    def test_give_to_defaults_to_none(self):
        assert RemoveMember(character_id=THIEF).give_to is None

    @pytest.mark.parametrize("give_to", [None, LEAD])
    def test_it_round_trips_through_json(self, give_to):
        command = RemoveMember(character_id=THIEF, give_to=give_to, source="game:retire")
        payload = json.loads(json.dumps(command.model_dump(mode="json")))
        assert payload == {
            "command_type": "remove_member",
            "source": "game:retire",
            "character_id": THIEF,
            "give_to": give_to,
        }
        parsed = parse_command(payload)
        assert parsed == command
        assert type(parsed) is RemoveMember
        assert TypeAdapter(AnyCommand).validate_python(payload) == command

    def test_the_event_is_registered_after_member_added(self):
        assert CRAWL_EVENT_CLASSES.index(MemberRemovedEvent) == CRAWL_EVENT_CLASSES.index(MemberAddedEvent) + 1

    @pytest.mark.parametrize(
        ("given_to", "line"),
        [
            (None, "Sable leaves the party."),
            (LEAD, "Sable leaves the party, and character-0001 takes their belongings."),
        ],
    )
    def test_the_event_round_trips_and_renders(self, given_to, line):
        event = MemberRemovedEvent(character_id=THIEF, name="Sable", given_to=given_to)
        assert event.event_type == "member_removed"
        assert event.code == "session.member.removed"
        assert MemberRemovedEvent.allowed_codes == frozenset({"session.member.removed"})
        assert event.visibility is Visibility.PLAYER
        assert parse_any_event(json.loads(json.dumps(event.model_dump(mode="json")))) == event
        assert format_message(event) == line

    def test_the_event_defaults_given_to_to_none(self):
        assert MemberRemovedEvent(character_id=THIEF, name="Sable").given_to is None


# ---------------------------------------------------------------------- acceptance


class TestAccepted:
    @CHUNK
    @pytest.mark.parametrize("mode", FIELD_MODES)
    def test_the_member_leaves_and_the_rest_keep_their_order(self, mode):
        session = session_in(mode)
        location = session.dungeon_state.location.model_dump(mode="json")
        events = remove(session, THIEF)
        assert events == [MemberRemovedEvent(character_id=THIEF, name="Sable", given_to=None)]
        assert member_ids(session) == (LEAD, CLERIC, MAGE)
        assert session.mode.value == mode, "removing a member changes no mode"
        assert session.dungeon_state.location.model_dump(mode="json") == location, "and moves nobody"

    @CHUNK
    @pytest.mark.parametrize("mode", FIELD_MODES)
    def test_it_draws_nothing_takes_no_time_and_is_logged(self, mode):
        session = session_in(mode)
        streams = stream_states(session)
        rounds = session.clock.rounds
        command = RemoveMember(character_id=MAGE, give_to=LEAD)
        assert session.execute(command).accepted
        assert stream_states(session) == streams
        assert session.clock.rounds == rounds
        assert session.command_log[-1] is command

    @CHUNK
    def test_the_event_is_player_visible_and_logged(self):
        session = new_session()
        (event,) = remove(session, CLERIC)
        assert event.visibility is Visibility.PLAYER
        assert session.event_log[-1] == event
        assert format_message(event) == "Wynn leaves the party."

    @CHUNK
    def test_the_front_member_can_leave(self):
        session = new_session()
        remove(session, LEAD)
        assert member_ids(session) == (THIEF, CLERIC, MAGE)
        assert [member.id for member in session.party.ranks(2)[0]] == [THIEF, CLERIC]

    @CHUNK
    @pytest.mark.parametrize("state", ["alive", "dead", "petrified", "asleep"])
    def test_a_member_in_any_state_can_leave(self, state):
        session = new_session()
        enter(session)
        put_in_state(session, MAGE, state)
        events = remove(session, MAGE)
        assert events == [MemberRemovedEvent(character_id=MAGE, name="Elara", given_to=None)]
        assert member_ids(session) == (LEAD, THIEF, CLERIC)

    @CHUNK
    def test_the_old_id_is_refused_afterwards(self):
        session = new_session()
        remove(session, THIEF)
        with pytest.raises(ValueError):
            session.member(THIEF)
        assert_refused(session, GrantCoins(character_id=THIEF, coins=Coins(gp=5)), "session.command.unknown_member")
        assert_refused(session, RemoveMember(character_id=THIEF), "session.command.unknown_member")
        assert_refused(session, RemoveMember(character_id=CLERIC, give_to=THIEF), "session.command.unknown_member")

    @CHUNK
    def test_reorder_party_takes_the_new_roster(self):
        session = new_session()
        remove(session, THIEF)
        assert session.execute(ReorderParty(order=(MAGE, CLERIC, LEAD))).accepted
        assert member_ids(session) == (MAGE, CLERIC, LEAD)


# ---------------------------------------------------------------------- goods


class TestGiveTo:
    @CHUNK
    def test_everything_the_member_carries_moves_to_give_to(self):
        session = new_session()
        session.execute(GrantItem(character_id=LEAD, item_id="torch", quantity=2))
        magic = load_the_thief(session)
        lead_purse = session.member(LEAD).inventory.purse.model_copy()
        valuation = session.party_valuation_cp()
        remove(session, THIEF, give_to=LEAD)
        lead = session.member(LEAD)
        assert units(session, LEAD, "torch") == 5, "torches join the stack Brakk already has"
        for item_id in ("leather", "sword", magic["potion"], magic["ring"]):
            assert item_id in pack_ids(session, LEAD), item_id
        assert sorted(valuable.instance_id for valuable in lead.inventory.valuables) == [
            "valuable-9001",
            "valuable-9002",
        ]
        for denomination in ("pp", "gp", "ep", "sp", "cp"):
            assert getattr(lead.inventory.purse, denomination) == getattr(lead_purse, denomination) + getattr(
                THIEF_PURSE, denomination
            ), denomination
        assert session.party_valuation_cp() == valuation, "nothing leaves the party"

    @CHUNK
    def test_equipped_items_arrive_unequipped(self):
        session = new_session()
        magic = load_the_thief(session)
        equipped_before = equipped_ids(session, LEAD)
        remove(session, THIEF, give_to=LEAD)
        assert equipped_ids(session, LEAD) == equipped_before, "Brakk's own gear stays as it was"
        assert session.member(LEAD).inventory.rings == []
        assert magic["ring"] in pack_ids(session, LEAD)

    @CHUNK
    def test_the_events_are_the_releases_then_the_handover_then_the_removal(self):
        session = new_session()
        magic = load_the_thief(session)
        ring = session.member(THIEF).inventory.magic_item(magic["ring"])
        assert ring is not None
        (effect_id,) = ring.state["effect_ids"]
        events = remove(session, THIEF, give_to=LEAD)
        assert len(events) == 3
        released, given, removed = events
        assert isinstance(released, EffectReleasedEvent)
        assert released.effect_id == effect_id
        assert all(effect.effect_id != effect_id for effect in session.ledger.effects)
        assert isinstance(given, ItemsGivenEvent)
        assert (given.character_id, given.recipient_id) == (THIEF, LEAD)
        assert sorted(given.item_ids) == sorted(
            ["torch"] * 3 + ["leather", "sword", magic["potion"], magic["ring"], "valuable-9001", "valuable-9002"]
        )
        assert given.coins_gp_value == THIEF_PURSE.value_gp
        assert removed == MemberRemovedEvent(character_id=THIEF, name="Sable", given_to=LEAD)

    @CHUNK
    def test_the_released_ring_no_longer_works_for_anyone(self):
        session = new_session()
        load_the_thief(session)
        remove(session, THIEF, give_to=LEAD)
        assert not session.ledger.active_on(LEAD, "regeneration"), "a ring in the pack does nothing"
        assert not [effect for effect in session.ledger.effects if effect.definition.kind == "regeneration"]

    @CHUNK
    def test_a_revealed_cursed_ring_moves_too(self):
        session = new_session()
        ring_id = granted_magic(session, THIEF, "ring_of_weakness")
        assert session.execute(EquipItem(character_id=THIEF, item_id=ring_id)).accepted
        ring = session.member(THIEF).inventory.magic_item(ring_id)
        assert ring is not None and ring.cursed_revealed, "precondition: the curse is revealed"
        events = remove(session, THIEF, give_to=LEAD)
        assert isinstance(events[0], EffectReleasedEvent), "the wearer's curse ends as the ring leaves"
        assert [event for event in events if isinstance(event, ItemsGivenEvent)] == [
            ItemsGivenEvent(character_id=THIEF, recipient_id=LEAD, item_ids=(ring_id,), coins_gp_value=0)
        ]
        assert ring_id in pack_ids(session, LEAD)
        assert session.member(LEAD).inventory.rings == []
        assert not session.ledger.active_on(LEAD), "the curse doesn't pass to Brakk"

    @CHUNK
    def test_coins_alone_make_a_handover_with_no_items(self):
        session = new_session()
        assert session.execute(GrantCoins(character_id=THIEF, coins=Coins(gp=30))).accepted
        events = remove(session, THIEF, give_to=LEAD)
        assert events[:-1] == [ItemsGivenEvent(character_id=THIEF, recipient_id=LEAD, item_ids=(), coins_gp_value=30)]
        assert session.member(LEAD).inventory.purse.gp == 30

    @CHUNK
    def test_an_empty_handed_member_makes_no_handover(self):
        session = new_session()
        events = remove(session, THIEF, give_to=LEAD)
        assert events == [MemberRemovedEvent(character_id=THIEF, name="Sable", given_to=LEAD)]

    @CHUNK
    def test_a_dead_member_s_goods_go_to_give_to(self):
        session = new_session()
        enter(session)
        magic = load_the_thief(session)
        die(session, THIEF)
        remove(session, THIEF, give_to=CLERIC)
        assert magic["potion"] in pack_ids(session, CLERIC)
        assert session.member(CLERIC).inventory.purse.pp == THIEF_PURSE.pp


class TestWithoutGiveTo:
    @CHUNK
    def test_everything_the_member_carries_leaves_the_game(self):
        session = new_session()
        magic = load_the_thief(session)
        valuation = session.party_valuation_cp()
        carried = session.member(THIEF).inventory.purse.value_cp + 170 * 100
        events = remove(session, THIEF)
        assert events == [MemberRemovedEvent(character_id=THIEF, name="Sable", given_to=None)], "no event per effect"
        assert session.party_valuation_cp() == valuation - carried
        for member in session.party.members:
            inventory = member.inventory
            assert inventory.magic_item(magic["potion"]) is None
            assert inventory.magic_item(magic["ring"]) is None
            assert inventory.valuables == []
            assert units(session, member.id, "torch") == 0
        assert not [effect for effect in session.ledger.effects if effect.definition.kind == "regeneration"]


# ---------------------------------------------------------------------- what the session tracks


class TestTheSessionForgetsTheMember:
    @CHUNK
    @pytest.mark.parametrize("give_to", [None, LEAD])
    def test_effects_on_the_member_end_with_no_event(self, give_to):
        session = mage_session()
        cast = CastSpell(character_id=MAGE, spell_id="light_mu", mode="illuminate", targets=(MAGE,))
        assert session.execute(cast).accepted
        put_to_sleep(session, MAGE)
        assert len(session.ledger.active_on(MAGE)) == 2, "precondition: the light and the sleep"
        events = remove(session, MAGE, give_to=give_to)
        assert events == [MemberRemovedEvent(character_id=MAGE, name="Elara", given_to=give_to)]
        assert not session.ledger.active_on(MAGE)
        assert not [effect for effect in session.view(Visibility.PLAYER).effects if effect.character_id == MAGE]

    @CHUNK
    def test_an_effect_the_member_cast_on_another_lasts_its_full_duration(self):
        session = mage_session()
        cast = CastSpell(character_id=MAGE, spell_id="light_mu", mode="illuminate", targets=(LEAD,))
        assert session.execute(cast).accepted
        (effect,) = session.ledger.active_on(LEAD, "light")
        expires = effect.expires_round
        assert expires is not None
        remove(session, MAGE)
        (still,) = session.ledger.active_on(LEAD, "light")
        assert (still.effect_id, still.expires_round) == (effect.effect_id, expires)
        assert session.party_light()[0], "Brakk's light still shines"
        left = expires - session.clock.rounds
        assert session.execute(AdvanceTime(n=left - 1, unit=TimeUnit.ROUND)).accepted
        assert session.ledger.active_on(LEAD, "light"), "it runs right up to its last round"
        result = session.execute(AdvanceTime(n=1, unit=TimeUnit.ROUND))
        assert any(
            isinstance(event, EffectExpiredEvent) and event.effect_id == effect.effect_id for event in result.events
        )
        assert not session.ledger.active_on(LEAD, "light")

    @CHUNK
    def test_the_death_record_is_dropped(self):
        session = new_session()
        enter(session)
        die(session, MAGE)
        remove(session, MAGE)
        assert MAGE not in session.death_records
        assert MAGE not in session.view(Visibility.REFEREE).death_records

    @CHUNK
    def test_the_deprivation_state_is_dropped(self):
        session = new_session()
        enter(session)
        assert session.execute(AdvanceTime(n=1, unit=TimeUnit.DAY)).accepted
        assert MAGE in session.view(Visibility.PLAYER).deprivation, "precondition: a day without food"
        remove(session, MAGE)
        assert MAGE not in session.deprivation
        assert MAGE not in session.view(Visibility.PLAYER).deprivation
        assert MAGE not in session.view(Visibility.REFEREE).deprivation
        assert set(session.deprivation) == {LEAD, THIEF, CLERIC}, "the others keep theirs"

    @CHUNK
    def test_the_prepared_sleep_record_is_dropped(self):
        session = mage_session()
        assert MAGE in session.last_prepared_sleep
        remove(session, MAGE)
        assert MAGE not in session.last_prepared_sleep
        assert MAGE not in session.view(Visibility.REFEREE).exploration.last_prepared_sleep


class TestLight:
    @CHUNK
    def test_a_burning_torch_leaves_with_its_bearer(self):
        session = new_session()
        enter(session)
        light(session, THIEF, "torch")
        assert session.party_light()[0], "precondition: Sable's torch lights the party"
        events = remove(session, THIEF)
        assert events == [MemberRemovedEvent(character_id=THIEF, name="Sable", given_to=None)]
        assert not session.party_light()[0]
        assert not [effect for effect in session.view(Visibility.PLAYER).effects if effect.kind == "light"]

    @CHUNK
    def test_a_lantern_given_away_arrives_unlit(self):
        session = new_session()
        enter(session)
        light(session, THIEF, "lantern")
        assert session.party_light()[0], "precondition: Sable's lantern lights the party"
        remove(session, THIEF, give_to=LEAD)
        assert units(session, LEAD, "lantern") == 1
        assert not session.ledger.active_on(LEAD, "light")
        assert not session.party_light()[0]
        assert not [effect for effect in session.view(Visibility.PLAYER).effects if effect.kind == "light"]

    @CHUNK
    def test_another_member_s_light_keeps_burning(self):
        session = new_session()
        enter(session)
        light(session, LEAD, "torch")
        light(session, THIEF, "lantern")
        remove(session, THIEF, give_to=CLERIC)
        assert session.party_light()[0]
        assert [
            effect.character_id for effect in session.view(Visibility.PLAYER).effects if effect.kind == "light"
        ] == [LEAD]


# ---------------------------------------------------------------------- refusals


class TestRefusals:
    # The session's mode gate runs before any handler, so the mode tests hold on the stub and
    # carry no marker.

    @pytest.mark.parametrize("mode", REFUSED_MODES)
    def test_every_other_mode_refuses_it(self, mode):
        session = session_in(mode)
        assert_refused(session, RemoveMember(character_id=THIEF), "session.command.wrong_mode")

    def test_the_mode_is_checked_before_the_members(self):
        session = session_in("encounter")
        assert_refused(
            session, RemoveMember(character_id="character-0099", give_to=THIEF), "session.command.wrong_mode"
        )

    @CHUNK
    def test_an_unknown_member_is_refused(self):
        session = new_session()
        assert_refused(session, RemoveMember(character_id="character-0099"), "session.command.unknown_member")

    @CHUNK
    def test_an_unknown_give_to_is_refused(self):
        session = new_session()
        assert_refused(
            session, RemoveMember(character_id=THIEF, give_to="character-0099"), "session.command.unknown_member"
        )

    @CHUNK
    def test_giving_to_the_leaving_member_is_refused(self):
        session = new_session()
        assert_refused(session, RemoveMember(character_id=THIEF, give_to=THIEF), "party.remove.same_member")

    @CHUNK
    @pytest.mark.parametrize("state", ["dead", "petrified", "asleep"])
    def test_a_give_to_who_cannot_act_is_refused(self, state):
        session = new_session()
        enter(session)
        put_in_state(session, CLERIC, state)
        assert_refused(
            session, RemoveMember(character_id=THIEF, give_to=CLERIC), "session.command.member_incapacitated"
        )

    @CHUNK
    def test_the_only_member_is_refused(self):
        session = solo_session()
        assert_refused(session, RemoveMember(character_id=LEAD), "party.remove.last_standing")

    @CHUNK
    @pytest.mark.parametrize("fallen", ["dead", "petrified"])
    def test_the_last_member_standing_is_refused(self, fallen):
        session = new_session()
        enter(session)
        for character_id in (THIEF, CLERIC, MAGE):
            put_in_state(session, character_id, fallen)
        assert_refused(session, RemoveMember(character_id=LEAD), "party.remove.last_standing")

    @CHUNK
    def test_the_last_standing_member_is_refused_between_the_dead_and_the_stone(self):
        session = new_session()
        enter(session)
        die(session, THIEF)
        die(session, CLERIC)
        petrify(session, MAGE)
        assert_refused(session, RemoveMember(character_id=LEAD), "party.remove.last_standing")

    @CHUNK
    def test_a_fallen_member_can_leave_while_another_stands(self):
        session = new_session()
        enter(session)
        for character_id in (THIEF, CLERIC):
            die(session, character_id)
        petrify(session, MAGE)
        remove(session, MAGE)
        remove(session, THIEF)
        assert member_ids(session) == (LEAD, CLERIC)

    @CHUNK
    def test_an_asleep_member_counts_as_standing(self):
        session = new_session()
        enter(session)
        die(session, CLERIC)
        die(session, MAGE)
        put_to_sleep(session, THIEF)
        remove(session, LEAD)
        assert member_ids(session) == (THIEF, CLERIC, MAGE)

    @CHUNK
    def test_an_unknown_member_is_reported_before_the_same_member(self):
        session = new_session()
        assert_refused(
            session,
            RemoveMember(character_id="character-0099", give_to="character-0099"),
            "session.command.unknown_member",
        )

    @CHUNK
    def test_the_same_member_is_reported_before_an_incapacitated_give_to(self):
        session = new_session()
        enter(session)
        die(session, THIEF)
        assert_refused(session, RemoveMember(character_id=THIEF, give_to=THIEF), "party.remove.same_member")

    @CHUNK
    def test_an_incapacitated_give_to_is_reported_before_the_last_standing(self):
        session = new_session()
        enter(session)
        for character_id in (THIEF, CLERIC, MAGE):
            die(session, character_id)
        assert_refused(session, RemoveMember(character_id=LEAD, give_to=THIEF), "session.command.member_incapacitated")

    @CHUNK
    def test_an_unknown_give_to_is_reported_before_the_last_standing(self):
        session = solo_session()
        assert_refused(
            session, RemoveMember(character_id=LEAD, give_to="character-0099"), "session.command.unknown_member"
        )


# ---------------------------------------------------------------------- treasure


def delve_award(removal: RemoveMember | None, *, sable_dies: bool = False) -> tuple[AdventureXpAwardEvent, int, int]:
    """Play one delve and return the return award, the snapshot before the removal, and after it.

    Sable leaves town with 500 gp and a 50 gp gem. In the dungeon Brakk finds 100 gp and Sable
    finds 300 gp, so the party comes back with 400 gp of treasure. `removal` runs just before
    the party heads back to town.
    """
    session = new_session()
    assert session.execute(GrantCoins(character_id=THIEF, coins=Coins(gp=500))).accepted
    session.member(THIEF).inventory.valuables.append(
        ValuableInstance(instance_id="valuable-9001", kind="gem", value_gp=50)
    )
    enter(session)
    assert session.execute(GrantCoins(character_id=LEAD, coins=Coins(gp=100))).accepted
    assert session.execute(GrantCoins(character_id=THIEF, coins=Coins(gp=300))).accepted
    if sable_dies:
        die(session, THIEF)
    before = session.treasure_snapshot_cp
    assert before is not None
    if removal is not None:
        assert session.execute(removal).accepted
    after = session.treasure_snapshot_cp
    assert after is not None
    result = session.execute(TravelToTown())
    assert result.accepted
    (award,) = [event for event in result.events if isinstance(event, AdventureXpAwardEvent)]
    return award, before, after


class TestTreasure:
    def test_the_delve_without_a_removal_earns_400(self):
        award, _, _ = delve_award(None)
        assert award.treasure_xp == 400

    @CHUNK
    def test_a_removal_without_give_to_leaves_the_treasure_experience_as_it_was(self):
        award, before, after = delve_award(RemoveMember(character_id=THIEF))
        assert after == before - (500 + 300 + 50) * 100
        assert award.treasure_xp == 400
        assert THIEF not in award.survivors

    @CHUNK
    def test_a_removal_with_give_to_leaves_the_snapshot_alone(self):
        award, before, after = delve_award(RemoveMember(character_id=THIEF, give_to=CLERIC))
        assert after == before
        assert award.treasure_xp == 400

    @CHUNK
    @pytest.mark.parametrize("give_to", [None, CLERIC])
    def test_a_dead_member_s_removal_leaves_the_treasure_experience_as_it_was(self, give_to):
        award, _, _ = delve_award(RemoveMember(character_id=THIEF, give_to=give_to), sable_dies=True)
        assert award.treasure_xp == 400

    @CHUNK
    def test_a_removal_in_town_leaves_no_snapshot(self):
        session = new_session()
        assert session.execute(GrantCoins(character_id=THIEF, coins=Coins(gp=500))).accepted
        remove(session, THIEF)
        assert session.treasure_snapshot_cp is None
        enter(session)
        assert session.treasure_snapshot_cp == session.party_valuation_cp() == 0


# ---------------------------------------------------------------------- ids


class TestIds:
    @CHUNK
    def test_a_member_added_afterwards_gets_a_fresh_id(self):
        session = new_session()
        remove(session, MAGE)
        result = session.execute(AddMember(character=recruit()))
        assert result.accepted
        assert result.events[0].character_id == NEW
        assert member_ids(session) == (LEAD, THIEF, CLERIC, NEW)

    @CHUNK
    def test_a_retired_id_is_passed_over_even_when_the_allocator_never_issued_it(self):
        # `GameSession.new` keeps ids members already have and allocates nothing for them, so
        # the allocator's next ids can be ones a member had. A removed member's id stays retired.
        members = build_party().members[:3]
        for number, member in enumerate(members, start=1):
            member.id = f"character-{number:04d}"
        session = GameSession.new(Party(members=members), quiet_adventure(), seed=SEED)
        assert session.allocator.counters.get("character", 0) == 0
        remove(session, THIEF)
        result = session.execute(AddMember(character=recruit()))
        assert result.accepted
        assert result.events[0].character_id == "character-0004"

    @CHUNK
    def test_a_retired_id_stays_retired_through_a_save_and_a_replay(self):
        members = build_party().members[:3]
        for number, member in enumerate(members, start=1):
            member.id = f"character-{number:04d}"
        document = party_to_document(members)
        session = GameSession.new(Party(members=members), quiet_adventure(), seed=SEED)
        remove(session, THIEF)
        restored = load_game(json.loads(json.dumps(save_game(session))))
        again = replay_game(
            SEED,
            document,
            quiet_adventure(),
            Ruleset(),
            [json.loads(json.dumps(command.model_dump(mode="json"))) for command in session.command_log],
        )
        for copy in (session, restored, again):
            result = copy.execute(AddMember(character=recruit()))
            assert result.accepted
            assert result.events[0].character_id == "character-0004"

    @CHUNK
    def test_a_wiped_party_is_replaced_and_the_fallen_retired(self):
        session = session_in("game_over")
        assert session.execute(PlaceParty(location={"kind": "town"})).accepted
        assert_refused(session, RemoveMember(character_id=LEAD), "party.remove.last_standing")
        assert session.execute(AddMember(character=recruit())).accepted
        for character_id in STOCK_IDS:
            remove(session, character_id)
        assert member_ids(session) == (NEW,)
        assert session.death_records == {}
        enter(session)
        assert session.execute(MoveParty(direction=Direction.EAST)).accepted


# ---------------------------------------------------------------------- views, saves, replay


class TestViewsSavesAndReplay:
    @CHUNK
    def test_both_views_leave_the_member_out(self):
        session = new_session()
        remove(session, THIEF, give_to=LEAD)
        player = session.view(Visibility.PLAYER)
        assert tuple(sheet.id for sheet in player.party) == (LEAD, CLERIC, MAGE)
        referee = session.view(Visibility.REFEREE)
        assert tuple(member.id for member in referee.party.members) == (LEAD, CLERIC, MAGE)

    @CHUNK
    @pytest.mark.parametrize("mode", FIELD_MODES)
    def test_a_save_round_trips_the_removal(self, mode):
        session = session_in(mode)
        load_the_thief(session)
        remove(session, THIEF, give_to=LEAD)
        restored = load_game(json.loads(json.dumps(save_game(session))))
        assert session_state(restored) == session_state(session)
        assert member_ids(restored) == (LEAD, CLERIC, MAGE)
        result = restored.execute(AddMember(character=recruit()))
        assert result.accepted
        assert result.events[0].character_id == NEW

    @CHUNK
    def test_a_replay_reaches_the_same_party(self):
        # Every step is a command, so the replay has all of it.
        session = new_session()
        enter(session)
        load_the_thief(session, valuables=False)
        remove(session, THIEF, give_to=LEAD)
        assert session.execute(MoveParty(direction=Direction.EAST)).accepted
        assert session.execute(AddMember(character=recruit())).accepted
        remove(session, MAGE)
        again = replayed(session, quiet_adventure())
        assert member_ids(again) == (LEAD, CLERIC, NEW)
        assert snapshot(again) == snapshot(session)


# ---------------------------------------------------------------------- the fuzz contract


@CHUNK
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    seed=st.integers(min_value=0, max_value=2**32),
    commands=st.lists(command_strategy(include_pending=True), min_size=1, max_size=25),
)
def test_fuzzed_sequences_with_removals_never_raise_and_round_trip(seed, commands):
    """Schema-valid commands, `RemoveMember` among them, reject and never throw.

    A removal never empties the party, a removed id never comes back, and nothing the
    session tracks about a removed member survives. A fresh member joins in town first,
    so the fuzz can also address a member who joined during play.
    """
    session = GameSession.new(build_party(), build_adventure(), seed=seed)
    assert session.execute(AddMember(character=recruit())).accepted
    removed: set[str] = set()
    for command in commands:
        result = session.execute(command)  # must never raise
        removed.update(event.character_id for event in result.events if isinstance(event, MemberRemovedEvent))
        ids = [member.id for member in session.party.members]
        assert ids, "a removal never empties the party"
        assert None not in ids
        assert len(set(ids)) == len(ids)
        assert not removed & set(ids), "a removed id never comes back"
        assert not removed & {effect.target_ref for effect in session.ledger.effects}
        assert not removed & set(session.death_records)
        assert not removed & set(session.deprivation)
        assert not removed & set(session.last_prepared_sleep)
    restored = load_game(json.loads(json.dumps(save_game(session))))
    assert session_state(restored) == session_state(session)
