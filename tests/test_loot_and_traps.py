"""Loot, traps, gates, and doors: six gameplay defects a player can hit (issue #167 items 15-18 and 22, issue #159).

- Item 15. A trap's saving throw adds the trap's `SaveSpec.modifier`, as a spell's save does. The modifier changes
  the outcome and never the draws.
- Item 16. When a destructive kill (a breath weapon) destroys a member's gear, the magic items that make their save
  land in a drop pile on the victim's cell, as `docs/adaptations.md` and the phase 5 plan pin. The party picks them
  up with `TakeTreasure` and the feature id `"pile"`, the way it recovers what a slain NPC adventurer leaves. A dead
  member's inventory can't be reached by any command, so the items can't stay there.
- Item 17. A keyed encounter's lair hoard is rolled once per lair. A later visit after an evade finds the same cache,
  or none at all once the party took it, and spends no treasure draws.
- Item 18. Lighting the last oil flask from a floor pile removes the pile only when nothing at all is left in it.
- Item 22. A gate's `success` text reaches the players on a transition within one level, as it does on a transition
  to another level: on a player-visible event's `narrative`, which `format_message` renders.
- Issue #159. Reading a door through `DungeonState.door` never changes how play sees it: an untouched door whose
  spec sets `starts_open` still stands open afterwards.
"""

import copy

import pytest

from crawl_fixtures import build_adventure, build_open_door_adventure, build_party
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.combat import SaveCategory
from osrlib.core.events import DeathEvent, EquipmentDestroyedEvent, Visibility
from osrlib.core.items import Coins, MagicItemInstance, ValuableInstance
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.spells import SaveSpec
from osrlib.core.tables import ReactionResult
from osrlib.core.treasure import TREASURE_STREAM
from osrlib.crawl import encounter as encounter_module
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import (
    BattleDeclaration,
    EngageBattle,
    EnterDungeon,
    GrantItem,
    LightSource,
    MoveParty,
    ResolveBattleRound,
    SessionMode,
    TakeTreasure,
    UseStairs,
)
from osrlib.crawl.dungeon import (
    AreaSpec,
    Direction,
    DroppedItem,
    DropPile,
    DungeonSpec,
    DungeonState,
    Edge,
    EdgeKind,
    KeyedEncounter,
    KeyedMonster,
    LevelSpec,
    TransitionSpec,
    TrapEffect,
    TrapSpec,
    cell_ref,
)
from osrlib.crawl.events import LocationEnteredEvent
from osrlib.crawl.gates import GateSpec, HasItemCondition
from osrlib.crawl.narrative import NarrativeBlock
from osrlib.crawl.party import Party
from osrlib.crawl.session import EXPLORATION_STREAM, GameSession
from osrlib.messages import format_message
from osrlib.persistence import load_game, save_game

CHUNK = "chunk: loot-and-traps"


# ---------------------------------------------------------------------- shared helpers


def hero(name: str = "Hild", seed: int = 7):
    """Roll a first-level fighter."""
    stream = RngStreams(master_seed=seed).get(CHARACTER_CREATION_STREAM)
    return create_character(
        name=name, class_id="fighter", alignment=Alignment.LAWFUL, ruleset=Ruleset(), stream=stream
    ).character


def corridor(areas=(), width: int = 2, number: int = 1, transitions=()) -> LevelSpec:
    """A one-row level whose cells are joined by open edges, entered at (0, 0) when it is level 1."""
    edges = {f"{x},0:west": Edge(kind=EdgeKind.OPEN) for x in range(1, width)}
    return LevelSpec(
        number=number,
        width=width,
        height=1,
        entrance=(0, 0) if number == 1 else None,
        edges=edges,
        areas=tuple(areas),
        transitions=tuple(transitions),
    )


def crypt_session(levels, *, seed: int = 7, members=None) -> GameSession:
    """A session on a one-dungeon adventure (`crypt`), standing at the entrance."""
    crypt = DungeonSpec(id="crypt", name="Crypt", levels=tuple(levels))
    adventure = Adventure(name="A", town=TownSpec(name="Threshold", travel_turns={"crypt": 1}), dungeons=(crypt,))
    session = GameSession.new(Party(members=list(members or [hero()])), adventure, seed=seed)
    assert session.execute(EnterDungeon(dungeon_id="crypt")).accepted
    return session


def here(session: GameSession) -> str:
    """The cell reference of the party's cell, which keys its drop pile."""
    location = session.dungeon_state.location
    return cell_ref(location.dungeon_id, location.level_number, location.position)


# ---------------------------------------------------------------------- item 15


def gas_trap(modifier: int) -> TrapSpec:
    """A room trap that kills on a failed save versus death, with `modifier` on the save."""
    return TrapSpec(
        kind="room",
        trigger="enter",
        effect=TrapEffect(kills=True, save=SaveSpec(category=SaveCategory.DEATH, modifier=modifier)),
    )


def spring_gas(seed: int, modifier: int):
    """Walk one fighter into the gas trap's cell, and return the session and the step's save events."""
    session = crypt_session([corridor([AreaSpec(id="gas", cells=((1, 0),), trap=gas_trap(modifier))])], seed=seed)
    result = session.execute(MoveParty(direction=Direction.EAST))
    assert result.accepted
    saves = [event for event in result.events if event.code.startswith("combat.save.")]
    return session, saves


class TestTrapSaveModifier:
    """Item 15: the trap's save adds `effect.save.modifier`."""

    @pytest.mark.xfail(reason=CHUNK, strict=True)
    def test_a_large_bonus_makes_every_save_pass(self):
        sprung = 0
        for seed in range(1, 60):
            session, saves = spring_gas(seed, modifier=50)
            if not saves:
                continue  # the trap didn't spring this time (2-in-6)
            sprung += 1
            (save,) = saves
            assert save.modifier == 50, "the fighter has no other save bonus, so the total is the trap's +50"
            assert save.code == "combat.save.passed"
            assert session.party.members[0].current_hp > 0
            assert session.mode is SessionMode.EXPLORING
        assert sprung >= 5, "the scenario must spring the trap often enough to mean something"

    @pytest.mark.xfail(reason=CHUNK, strict=True)
    def test_a_large_penalty_makes_every_save_fail(self):
        sprung = 0
        for seed in range(1, 60):
            session, saves = spring_gas(seed, modifier=-50)
            if not saves:
                continue
            sprung += 1
            (save,) = saves
            assert save.modifier == -50
            assert save.code == "combat.save.failed"
            assert session.mode is SessionMode.GAME_OVER
        assert sprung >= 5

    def test_with_no_modifier_the_save_adds_nothing(self):
        sprung = 0
        for seed in range(1, 30):
            _, saves = spring_gas(seed, modifier=0)
            if saves:
                sprung += 1
                assert saves[0].modifier == 0
        assert sprung >= 3

    def test_the_modifier_never_changes_the_draws(self):
        compared = 0
        for seed in range(1, 40):
            plain, plain_saves = spring_gas(seed, modifier=0)
            boosted, boosted_saves = spring_gas(seed, modifier=50)
            assert len(plain_saves) == len(boosted_saves)
            for low, high in zip(plain_saves, boosted_saves, strict=True):
                assert low.roll == high.roll, "the same seed must roll the same d20 whatever the modifier"
                compared += 1
            assert (
                plain.streams.get(EXPLORATION_STREAM).export_state()
                == boosted.streams.get(EXPLORATION_STREAM).export_state()
            )
        assert compared >= 5


# ---------------------------------------------------------------------- item 16


def hellhound_battle(seed: int, ruleset: Ruleset | None = None) -> GameSession:
    """The stock party in battle with one hellhound, every member carrying a Sword +1 of their own."""
    session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=seed, ruleset=ruleset)
    for index, member in enumerate(session.party.members, start=1):
        member.inventory.items.append(
            MagicItemInstance(instance_id=f"magic-item-900{index}", template_id="sword_plus_1")
        )
    assert session.execute(EnterDungeon(dungeon_id="delve")).accepted
    instances = session.spawn("hellhound_3", 1)
    encounter_module.start_encounter(
        session,
        groups=[("hellhound_3", instances)],
        kind="spawned",
        distance_feet=30,
        pinned_stance=ReactionResult.HOSTILE,
        party_aware=True,
    )
    assert session.execute(EngageBattle()).accepted
    assert session.mode is SessionMode.BATTLE
    return session


def breath_kill(ruleset: Ruleset | None = None, *, need_saved: bool):
    """Find a seed where the hellhound's breath kills a member and someone is left standing.

    The party holds every round. Returns the session, the victim, and the destruction event of the round the victim
    died in, or fails the test when no seed in the range gives one.
    """
    for seed in range(1, 80):
        session = hellhound_battle(seed, ruleset)
        for _ in range(8):
            if session.mode is not SessionMode.BATTLE:
                break
            declarers = session.view(Visibility.PLAYER).encounter.declarers
            result = session.execute(
                ResolveBattleRound(
                    declarations=tuple(BattleDeclaration(character_id=member, action="hold") for member in declarers)
                )
            )
            assert result.accepted
            member_ids = {member.id for member in session.party.members}
            destroyed = [
                event
                for event in result.events
                if isinstance(event, EquipmentDestroyedEvent)
                and event.target_id in member_ids
                and (event.saved_items or not need_saved)
            ]
            if not destroyed:
                continue
            event = destroyed[0]
            died = {death.target_id for death in result.events if isinstance(death, DeathEvent)}
            if event.target_id in died and session.party.living_members() and session.mode is SessionMode.BATTLE:
                return session, session.member(event.target_id), event
            break
    pytest.fail("no seed gave a breath kill with a survivor")


class TestBreathSavedItems:
    """Item 16: the magic items that survive a breath kill land in a pile on the victim's cell."""

    @pytest.mark.xfail(reason=CHUNK, strict=True)
    def test_saved_items_leave_the_body_for_a_pile_on_the_cell(self):
        session, victim, event = breath_kill(need_saved=True)
        saved = set(event.saved_items)
        assert saved and victim.current_hp <= 0
        assert not saved & {getattr(item, "instance_id", None) for item in victim.inventory.items}
        pile = session.dungeon_state.piles.get(here(session))
        assert pile is not None, "the saved items must lie on the victim's cell"
        assert saved <= {item.instance_id for item in pile.magic_items}
        assert all(item.template_id == "sword_plus_1" for item in pile.magic_items if item.instance_id in saved)

    @pytest.mark.xfail(reason=CHUNK, strict=True)
    def test_the_party_takes_the_saved_items_from_the_pile(self):
        session, victim, event = breath_kill(need_saved=True)
        saved = set(event.saved_items)
        encounter_module.end_encounter(session, "evaded")
        assert session.mode is SessionMode.EXPLORING
        survivor = session.party.living_members()[0]
        result = session.execute(TakeTreasure(feature_id="pile", recipient_id=survivor.id))
        assert result.accepted, [rejection.code for rejection in result.rejections]
        assert saved <= {getattr(item, "instance_id", None) for item in survivor.inventory.items}
        assert not saved & {getattr(item, "instance_id", None) for item in victim.inventory.items}

    def test_with_the_death_save_off_nothing_survives_to_pile(self):
        session, victim, event = breath_kill(Ruleset(magic_item_death_save=False), need_saved=False)
        assert event.saved_items == ()
        assert not any(isinstance(item, MagicItemInstance) for item in victim.inventory.items)
        pile = session.dungeon_state.piles.get(here(session))
        assert pile is None or not pile.magic_items


# ---------------------------------------------------------------------- item 17


def lair_session(seed: int = 7) -> GameSession:
    """A corridor whose second cell is an orc lair with a keyed hoard (treasure type D)."""
    lair = AreaSpec(
        id="lair",
        cells=((1, 0),),
        encounter=KeyedEncounter(monsters=(KeyedMonster(template_id="orc", count_fixed=2),)),
    )
    return crypt_session([corridor([lair])], seed=seed)


def visit_and_evade(session: GameSession):
    """Step into the lair, slip away from whatever is there, and return the step's events."""
    result = session.execute(MoveParty(direction=Direction.EAST))
    assert result.accepted
    assert session.dungeon_state.location.position == (1, 0)
    if session.encounter is not None:
        encounter_module.end_encounter(session, "evaded")
    assert session.mode is SessionMode.EXPLORING
    return result.events


def leave(session: GameSession) -> None:
    assert session.execute(MoveParty(direction=Direction.WEST)).accepted
    assert session.dungeon_state.location.position == (0, 0)


def first_visit(seed: int = 7):
    """The first visit, which rolls the hoard, then the step back out."""
    session = lair_session(seed)
    events = visit_and_evade(session)
    hoards = [event for event in events if event.code == "treasure.hoard.generated"]
    assert len(hoards) == 1, "the scenario needs a lair whose first visit rolls a hoard"
    assert any(event.code == "encounter.started" for event in events)
    leave(session)
    return session, hoards[0]


class TestLairHoardOnce:
    """Item 17: a keyed encounter's hoard is rolled once per lair."""

    def test_the_first_visit_rolls_one_hoard_on_the_lair_cell(self):
        session, hoard = first_visit()
        caches = session.dungeon_state.generated_caches
        assert list(caches) == [hoard.cache_ref]
        assert caches[hoard.cache_ref].cell_ref == cell_ref("crypt", 1, (1, 0))

    @pytest.mark.xfail(reason=CHUNK, strict=True)
    def test_a_second_visit_finds_the_same_cache(self):
        session, hoard = first_visit()
        before = {key: cache.model_copy(deep=True) for key, cache in session.dungeon_state.generated_caches.items()}
        events = visit_and_evade(session)
        assert not [event for event in events if event.code == "treasure.hoard.generated"]
        assert session.dungeon_state.generated_caches == before

    @pytest.mark.xfail(reason=CHUNK, strict=True)
    def test_a_second_visit_spends_no_treasure_draws(self):
        session, _ = first_visit()
        stream_before = session.streams.get(TREASURE_STREAM).export_state()
        visit_and_evade(session)
        assert session.streams.get(TREASURE_STREAM).export_state() == stream_before

    @pytest.mark.xfail(reason=CHUNK, strict=True)
    def test_a_taken_hoard_is_not_rolled_again(self):
        session = lair_session()
        events = visit_and_evade(session)
        (hoard,) = [event for event in events if event.code == "treasure.hoard.generated"]
        taken = session.execute(TakeTreasure(feature_id=hoard.cache_ref))
        assert taken.accepted, [rejection.code for rejection in taken.rejections]
        assert session.dungeon_state.generated_caches == {}
        leave(session)
        events = visit_and_evade(session)
        assert not [event for event in events if event.code == "treasure.hoard.generated"]
        assert session.dungeon_state.generated_caches == {}

    @pytest.mark.xfail(reason=CHUNK, strict=True)
    def test_the_record_survives_a_save_and_load(self):
        session, hoard = first_visit()
        restored = load_game(save_game(session))
        events = visit_and_evade(restored)
        assert not [event for event in events if event.code == "treasure.hoard.generated"]
        assert list(restored.dungeon_state.generated_caches) == [hoard.cache_ref]


# ---------------------------------------------------------------------- item 18


def oil_pile_session(pile: DropPile, seed: int = 4) -> tuple[GameSession, str]:
    """One fighter with a tinder box on a one-cell level, standing on `pile`."""
    session = crypt_session([LevelSpec(number=1, width=1, height=1, entrance=(0, 0))], seed=seed)
    member = session.party.members[0]
    assert session.execute(GrantItem(character_id=member.id, item_id="tinder_box")).accepted
    ref = cell_ref("crypt", 1, (0, 0))
    session.dungeon_state.piles[ref] = pile
    return session, ref


def light_the_oil(session: GameSession) -> None:
    """Light the pile's oil, retrying the tinder box until it catches."""
    member = session.party.members[0]
    for _ in range(30):
        result = session.execute(LightSource(character_id=member.id, item_id="oil_flask"))
        assert result.accepted, [rejection.code for rejection in result.rejections]
        if any(event.code == "exploration.light.lit" for event in result.events):
            return
    pytest.fail("the tinder box never caught")


def ruby() -> ValuableInstance:
    return ValuableInstance(instance_id="valuable-0099", kind="gem", name="Ruby", value_gp=500)


class TestOilPileKeepsTreasure:
    """Item 18: lighting the last oil flask in a pile removes the pile only when nothing is left in it."""

    @pytest.mark.xfail(reason=CHUNK, strict=True)
    def test_a_valuable_keeps_the_pile(self):
        session, ref = oil_pile_session(
            DropPile(items=[DroppedItem(item_id="oil_flask", quantity=1)], valuables=[ruby()])
        )
        light_the_oil(session)
        pile = session.dungeon_state.piles.get(ref)
        assert pile is not None, "the ruby must still lie on the floor"
        assert pile.items == []
        assert [valuable.instance_id for valuable in pile.valuables] == ["valuable-0099"]

    @pytest.mark.xfail(reason=CHUNK, strict=True)
    def test_a_magic_item_keeps_the_pile(self):
        sword = MagicItemInstance(instance_id="magic-item-0099", template_id="sword_plus_1")
        session, ref = oil_pile_session(
            DropPile(items=[DroppedItem(item_id="oil_flask", quantity=1)], magic_items=[sword])
        )
        light_the_oil(session)
        pile = session.dungeon_state.piles.get(ref)
        assert pile is not None, "the sword must still lie on the floor"
        assert pile.items == []
        assert [item.instance_id for item in pile.magic_items] == ["magic-item-0099"]

    @pytest.mark.xfail(reason=CHUNK, strict=True)
    def test_the_party_can_still_take_the_valuable(self):
        session, _ = oil_pile_session(
            DropPile(items=[DroppedItem(item_id="oil_flask", quantity=1)], valuables=[ruby()])
        )
        light_the_oil(session)
        member = session.party.members[0]
        result = session.execute(TakeTreasure(feature_id="pile"))
        assert result.accepted, [rejection.code for rejection in result.rejections]
        assert "valuable-0099" in [valuable.instance_id for valuable in member.inventory.valuables]

    def test_a_pile_of_only_the_last_flask_goes(self):
        session, ref = oil_pile_session(DropPile(items=[DroppedItem(item_id="oil_flask", quantity=1)]))
        light_the_oil(session)
        assert ref not in session.dungeon_state.piles

    def test_coins_keep_the_pile(self):
        session, ref = oil_pile_session(
            DropPile(items=[DroppedItem(item_id="oil_flask", quantity=1)], coins=Coins(gp=12))
        )
        light_the_oil(session)
        pile = session.dungeon_state.piles.get(ref)
        assert pile is not None and pile.items == [] and pile.coins.gp == 12

    def test_a_second_flask_stays_in_the_pile(self):
        session, ref = oil_pile_session(DropPile(items=[DroppedItem(item_id="oil_flask", quantity=2)]))
        light_the_oil(session)
        pile = session.dungeon_state.piles.get(ref)
        assert pile is not None
        assert [(entry.item_id, entry.quantity) for entry in pile.items] == [("oil_flask", 1)]


# ---------------------------------------------------------------------- item 22

SUCCESS = "The archway flares and lets you through."


def torch_gate() -> GateSpec:
    return GateSpec(condition=HasItemCondition(item_id="torch"), narrative=NarrativeBlock(success=SUCCESS))


def gated_session(transition: TransitionSpec, levels=()) -> GameSession:
    """A three-cell level 1 with `transition` at (0, 0), and a fighter who carries a torch."""
    level = LevelSpec(number=1, width=3, height=1, entrance=(0, 0), transitions=(transition,))
    session = crypt_session([level, *levels])
    member = session.party.members[0]
    assert session.execute(GrantItem(character_id=member.id, item_id="torch")).accepted
    return session


def narrated(events) -> list:
    """The player-visible events that carry the gate's success text as their narrative."""
    return [
        event
        for event in events
        if getattr(event, "narrative", None) == SUCCESS and event.visibility is Visibility.PLAYER
    ]


class TestGateSuccessWithinOneLevel:
    """Item 22: a gated transition's success text reaches the players whatever the destination."""

    @pytest.mark.xfail(reason=CHUNK, strict=True)
    def test_a_passage_within_the_level_shows_the_success_text(self):
        passage = TransitionSpec(
            kind="passage",
            position=(0, 0),
            side=Direction.EAST,
            to_dungeon_id="crypt",
            to_level_number=1,
            to_position=(2, 0),
            to_facing=Direction.EAST,
            requires=torch_gate(),
        )
        session = gated_session(passage)
        result = session.execute(MoveParty(direction=Direction.EAST))
        assert result.accepted
        assert session.dungeon_state.location.position == (2, 0)
        carriers = narrated(result.events)
        assert len(carriers) == 1
        assert SUCCESS in format_message(carriers[0])

    @pytest.mark.xfail(reason=CHUNK, strict=True)
    def test_stairs_within_the_level_show_the_success_text(self):
        stairs = TransitionSpec(
            kind="stairs_down",
            position=(0, 0),
            to_dungeon_id="crypt",
            to_level_number=1,
            to_position=(2, 0),
            to_facing=Direction.EAST,
            requires=torch_gate(),
        )
        session = gated_session(stairs)
        result = session.execute(UseStairs())
        assert result.accepted
        assert session.dungeon_state.location.position == (2, 0)
        carriers = narrated(result.events)
        assert len(carriers) == 1
        assert SUCCESS in format_message(carriers[0])

    def test_stairs_to_another_level_show_it_on_the_arrival(self):
        stairs = TransitionSpec(
            kind="stairs_down",
            position=(0, 0),
            to_dungeon_id="crypt",
            to_level_number=2,
            to_position=(0, 0),
            to_facing=Direction.EAST,
            requires=torch_gate(),
        )
        session = gated_session(stairs, levels=(corridor(number=2),))
        result = session.execute(UseStairs())
        assert result.accepted
        assert session.dungeon_state.location.level_number == 2
        carriers = narrated(result.events)
        assert len(carriers) == 1
        assert isinstance(carriers[0], LocationEnteredEvent)
        assert SUCCESS in format_message(carriers[0])


# ---------------------------------------------------------------------- issue 159

DOOR_REF = "vestibule:1:1,0:west"


def vestibule_session() -> GameSession:
    """The party at (0, 0) of the open-door fixture, the door east of it untouched."""
    session = GameSession.new(build_party(), build_open_door_adventure(), seed=5)
    assert session.execute(EnterDungeon(dungeon_id="vestibule")).accepted
    assert session.dungeon_state.location.position == (0, 0)
    assert DOOR_REF not in session.dungeon_state.doors, "the door must be untouched for the case to mean anything"
    return session


def front_end_reads_the_door(session: GameSession) -> None:
    """What a front end does to draw the door: read its state through `DungeonState.door`."""
    state = session.dungeon_state.door(DOOR_REF)
    _ = (state.open, state.wedged, state.discovered, state.unlocked)


def door_view_open(session: GameSession):
    """The player view's `door_open` for the vestibule door."""
    view = session.view(Visibility.PLAYER)
    (level,) = [level for level in view.explored if level.dungeon_id == "vestibule"]
    doors = [edge for edge in level.edges.values() if edge.kind == "door"]
    assert len(doors) == 1
    return doors[0].door_open


class TestReadingADoorChangesNothing:
    """Issue #159: reading a door's state never changes it, so an authored-open door stays open."""

    def test_an_untouched_authored_open_door_lets_the_party_through(self):
        session = vestibule_session()
        assert door_view_open(session) is True
        result = session.execute(MoveParty(direction=Direction.EAST))
        assert result.accepted
        assert session.dungeon_state.location.position == (1, 0)

    @pytest.mark.xfail(reason=CHUNK, strict=True)
    def test_after_a_read_the_party_still_walks_through(self):
        session = vestibule_session()
        front_end_reads_the_door(session)
        result = session.execute(MoveParty(direction=Direction.EAST))
        assert result.accepted, [rejection.code for rejection in result.rejections]
        assert session.dungeon_state.location.position == (1, 0)

    @pytest.mark.xfail(reason=CHUNK, strict=True)
    def test_after_a_read_the_view_still_shows_it_open(self):
        session = vestibule_session()
        front_end_reads_the_door(session)
        assert door_view_open(session) is True

    @pytest.mark.xfail(reason=CHUNK, strict=True)
    def test_after_a_read_a_saved_game_still_has_it_open(self):
        session = vestibule_session()
        front_end_reads_the_door(session)
        restored = load_game(save_game(session))
        assert door_view_open(restored) is True
        assert restored.execute(MoveParty(direction=Direction.EAST)).accepted
        assert restored.dungeon_state.location.position == (1, 0)


class TestACopiedDoorStateIsASnapshot:
    """A copy of a door state is the caller's own. Writing to it never changes the session's doors."""

    REF = "crypt:1:0,0:east"

    @pytest.mark.xfail(reason=CHUNK, strict=True)
    @pytest.mark.parametrize(
        "copier", [lambda state: state.model_copy(), copy.copy, copy.deepcopy], ids=["model_copy", "copy", "deepcopy"]
    )
    def test_writing_to_a_copy_of_an_unread_door_stores_nothing(self, copier):
        doors = DungeonState()
        snapshot = copier(doors.door(self.REF))
        snapshot.open = True
        assert self.REF not in doors.doors
        assert doors.door(self.REF).open is False

    @pytest.mark.parametrize(
        "copier", [lambda state: state.model_copy(), copy.copy, copy.deepcopy], ids=["model_copy", "copy", "deepcopy"]
    )
    def test_writing_to_a_copy_of_a_stored_door_leaves_the_stored_one(self, copier):
        doors = DungeonState()
        doors.door(self.REF).open = True
        snapshot = copier(doors.door(self.REF))
        snapshot.open = False
        assert doors.door(self.REF).open is True
