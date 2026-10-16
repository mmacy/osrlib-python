"""Passages: the transition the party walks through.

The fixture is one dungeon of two levels with three kinds of crossing: a passage pair between the
levels, a passage pair within level 1, and stairs that share a cell with a passage. The tests cover
the model, the two level lookups, each way a passage fires, each move that fires nothing, a gated
passage, the player view, the refusals of `validate_adventure`, and a saved game.
"""

import json

import pytest
from pydantic import ValidationError

from crawl_fixtures import GATE_TOKEN, build_party
from osrlib.core.character import party_to_document
from osrlib.core.effects import Condition, EffectDefinition
from osrlib.core.events import Visibility
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec, validate_adventure
from osrlib.crawl.commands import (
    EnterDungeon,
    GrantItem,
    LightSource,
    MoveParty,
    OpenDoor,
    PlaceParty,
    UseStairs,
)
from osrlib.crawl.dungeon import (
    AreaSpec,
    Direction,
    DoorSpec,
    DungeonSpec,
    Edge,
    EdgeKind,
    FeatureSpec,
    LevelSpec,
    PartyLocation,
    TransitionSpec,
    TrapEffect,
    TrapSpec,
    WanderingSpec,
    edge_key,
)
from osrlib.crawl.events import LocationEnteredEvent, PartyMovedEvent
from osrlib.crawl.gates import GateSpec, HasItemCondition
from osrlib.crawl.narrative import NarrativeBlock
from osrlib.crawl.session import GameSession
from osrlib.data import load_equipment, load_monsters
from osrlib.errors import ContentValidationError
from osrlib.persistence import load_game, replay_game, save_game, session_state

SEED = 17

EAST, WEST, NORTH, SOUTH = Direction.EAST, Direction.WEST, Direction.NORTH, Direction.SOUTH

TOLL = GateSpec(
    condition=HasItemCondition(item_id="toll_token", consumes=True),
    narrative=NarrativeBlock(refusal="The arch stays dark.", success="The arch flares and lets you through."),
)


def passage(position, side, level_number, to_position, to_facing, *, requires=None) -> TransitionSpec:
    return TransitionSpec(
        kind="passage",
        position=position,
        side=side,
        to_dungeon_id="keep",
        to_level_number=level_number,
        to_position=to_position,
        to_facing=to_facing,
        requires=requires,
    )


def passage_adventure(gate: GateSpec | None = None) -> Adventure:
    """Build the two-level keep.

    Level 1 is 7 by 3 cells, entered at (0, 1):

    - (0, 1), (1, 1), and (2, 1) are floor. The edge between (0, 1) and (1, 1) is open, and a
      door stands between (1, 1) and (2, 1).
    - The passage on the east side of (2, 1) leads to level 2's (1, 1), facing east. Its far cell
      is (3, 1). It takes `gate`, when one is given.
    - The passage on the north side of (1, 1) leads to (5, 1) on level 1, facing east. Its far cell
      is (1, 0).
    - (5, 1) and (6, 1) are floor, joined, and form the area `annex`.
    - The passage on the west side of (5, 1) leads back to (1, 1), facing south. Its far cell is
      (4, 1).

    Level 2 is 4 by 3 cells:

    - (1, 1), (2, 1), and (3, 1) are floor, joined in a row. (1, 1) and (2, 1) form the area
      `gallery`.
    - The passage on the west side of (1, 1) leads back to level 1's (2, 1), facing west. Its far
      cell is (0, 1).
    - Stairs up on (1, 1), listed after the passage, lead to level 1's (0, 1), facing east.
    """
    level_1 = LevelSpec(
        number=1,
        width=7,
        height=3,
        entrance=(0, 1),
        edges={
            edge_key((0, 1), EAST): Edge(kind=EdgeKind.OPEN),
            edge_key((1, 1), EAST): Edge(kind=EdgeKind.DOOR, door=DoorSpec()),
            edge_key((5, 1), EAST): Edge(kind=EdgeKind.OPEN),
        },
        areas=(AreaSpec(id="annex", cells=((5, 1), (6, 1))),),
        transitions=(
            passage((2, 1), EAST, 2, (1, 1), EAST, requires=gate),
            passage((1, 1), NORTH, 1, (5, 1), EAST),
            passage((5, 1), WEST, 1, (1, 1), SOUTH),
        ),
        wandering=WanderingSpec(chance_in_six=0),
    )
    level_2 = LevelSpec(
        number=2,
        width=4,
        height=3,
        edges={
            edge_key((1, 1), EAST): Edge(kind=EdgeKind.OPEN),
            edge_key((2, 1), EAST): Edge(kind=EdgeKind.OPEN),
        },
        areas=(AreaSpec(id="gallery", cells=((1, 1), (2, 1))),),
        transitions=(
            passage((1, 1), WEST, 1, (2, 1), WEST),
            TransitionSpec(
                kind="stairs_up",
                position=(1, 1),
                to_dungeon_id="keep",
                to_level_number=1,
                to_position=(0, 1),
                to_facing=EAST,
            ),
        ),
        wandering=WanderingSpec(chance_in_six=0),
    )
    return Adventure(
        name="The Keep",
        town=TownSpec(name="Threshold", travel_turns={"keep": 0}),
        dungeons=(DungeonSpec(id="keep", name="Keep", levels=(level_1, level_2)),),
        items=(GATE_TOKEN,),
    )


def keep_session(gate: GateSpec | None = None) -> GameSession:
    session = GameSession.new(build_party(), passage_adventure(gate), seed=SEED)
    assert session.execute(EnterDungeon(dungeon_id="keep")).accepted
    return session


def at_east_passage(session: GameSession) -> None:
    """Walk from the entrance to (2, 1), opening the door on the way. The door stays open behind the party."""
    assert session.execute(MoveParty(direction=EAST)).accepted
    assert session.execute(OpenDoor(direction=EAST)).accepted
    assert session.execute(MoveParty(direction=EAST)).accepted


def location(session: GameSession) -> tuple:
    where = session.dungeon_state.location
    return (where.level_number, where.position, where.facing)


def state_probe(session: GameSession) -> tuple:
    """The streams, the clock, and the door overlay, which a refused command leaves untouched."""
    return (
        {key: value.model_dump() for key, value in session.streams.export_states().items()},
        session.clock.rounds,
        {ref: state.model_dump() for ref, state in session.dungeon_state.doors.items()},
    )


def with_level(adventure: Adventure, number: int, **update) -> Adventure:
    """Return the adventure with one level's fields replaced, unvalidated, for `validate_adventure` to judge."""
    dungeon = adventure.dungeons[0]
    levels = tuple(level.model_copy(update=update) if level.number == number else level for level in dungeon.levels)
    return adventure.model_copy(update={"dungeons": (dungeon.model_copy(update={"levels": levels}),)})


def refusals(adventure: Adventure) -> str:
    try:
        validate_adventure(adventure, load_monsters(), load_equipment())
    except ContentValidationError as error:
        return str(error)
    return ""


class TestPassageModel:
    def test_a_passage_needs_a_side(self):
        with pytest.raises(ValidationError):
            TransitionSpec(
                kind="passage",
                position=(2, 1),
                to_dungeon_id="keep",
                to_level_number=2,
                to_position=(1, 1),
                to_facing=EAST,
            )

    def test_no_other_kind_takes_a_side(self):
        for kind in ("stairs_up", "stairs_down", "trapdoor", "chute"):
            with pytest.raises(ValidationError):
                TransitionSpec(
                    kind=kind,
                    position=(2, 1),
                    side=EAST,
                    to_dungeon_id="keep",
                    to_level_number=2,
                    to_position=(1, 1),
                    to_facing=EAST,
                )

    def test_a_passage_round_trips_through_json(self):
        spec = passage((2, 1), EAST, 2, (1, 1), EAST)
        dumped = spec.model_dump(mode="json")
        assert dumped["kind"] == "passage"
        assert dumped["side"] == "east"
        assert TransitionSpec.model_validate_json(spec.model_dump_json()) == spec


class TestLookups:
    def test_passage_at_finds_a_passage_by_cell_and_side(self):
        level_1, level_2 = passage_adventure().dungeons[0].levels
        found = level_1.passage_at((2, 1), EAST)
        assert found is not None
        assert (found.kind, found.position, found.side) == ("passage", (2, 1), EAST)
        assert level_1.passage_at((2, 1), WEST) is None
        assert level_1.passage_at((0, 1), EAST) is None
        assert level_2.passage_at((1, 1), WEST) is not None
        assert level_2.passage_at((1, 1), EAST) is None

    def test_passage_at_never_returns_stairs(self):
        level_2 = passage_adventure().dungeons[0].levels[1]
        for side in Direction:
            found = level_2.passage_at((1, 1), side)
            assert found is None or found.kind == "passage"

    def test_transition_at_ignores_passages(self):
        level_1, level_2 = passage_adventure().dungeons[0].levels
        assert level_1.transition_at((2, 1)) is None
        assert level_1.transition_at((1, 1)) is None
        stairs = level_2.transition_at((1, 1))
        assert stairs is not None
        assert stairs.kind == "stairs_up"


class TestCrossing:
    def test_a_move_across_the_side_lands_on_the_destination(self):
        session = keep_session()
        at_east_passage(session)
        result = session.execute(MoveParty(direction=EAST))
        assert result.accepted
        assert location(session) == (2, (1, 1), EAST)
        assert session.dungeon_state.location == PartyLocation(
            kind="dungeon", dungeon_id="keep", level_number=2, position=(1, 1), facing=EAST
        )

    def test_a_crossing_to_another_level_emits_the_stair_events_then_the_move(self):
        session = keep_session()
        at_east_passage(session)
        result = session.execute(MoveParty(direction=EAST))
        entered, shut, moved, area = result.events[:4]
        assert isinstance(entered, LocationEnteredEvent)
        assert (entered.location_kind, entered.location_id, entered.level_number) == ("level", "keep", 2)
        assert (entered.via, entered.transition_ref) == ("passage", "cell:keep:1:2,1")
        assert (shut.code, shut.x, shut.y, shut.direction) == ("exploration.door.swung_shut", 2, 1, "west")
        assert isinstance(moved, PartyMovedEvent)
        assert (moved.code, moved.x, moved.y, moved.facing) == ("exploration.party.moved", 1, 1, "east")
        assert isinstance(area, LocationEnteredEvent)
        assert (area.location_kind, area.location_id, area.level_number) == ("area", "gallery", 2)
        assert not any(isinstance(event, PartyMovedEvent) for event in result.events[4:])

    def test_the_landing_cell_is_explored(self):
        session = keep_session()
        at_east_passage(session)
        session.execute(MoveParty(direction=EAST))
        assert session.dungeon_state.is_explored("keep", 2, (1, 1))
        assert not session.dungeon_state.is_explored("keep", 1, (3, 1))

    def test_a_crossing_costs_a_step_onto_the_landing_cell(self):
        session = keep_session()
        at_east_passage(session)
        before = session.odometer_thirds
        session.execute(MoveParty(direction=EAST))
        assert session.odometer_thirds == before + 30, "the landing cell was unexplored"
        before = session.odometer_thirds
        session.execute(MoveParty(direction=WEST))
        assert session.odometer_thirds == before + 10, "the landing cell was explored"

    def test_the_partner_leads_back(self):
        session = keep_session()
        at_east_passage(session)
        session.execute(MoveParty(direction=EAST))
        result = session.execute(MoveParty(direction=WEST))
        assert result.accepted
        assert location(session) == (1, (2, 1), WEST)
        entered = result.events[0]
        assert isinstance(entered, LocationEnteredEvent)
        assert (entered.location_kind, entered.level_number) == ("level", 1)
        assert (entered.via, entered.transition_ref) == ("passage", "cell:keep:2:1,1")

    def test_a_crossing_within_a_level_moves_the_party_and_shuts_the_doors_it_left(self):
        session = keep_session()
        assert session.execute(MoveParty(direction=EAST)).accepted
        assert session.execute(OpenDoor(direction=EAST)).accepted
        result = session.execute(MoveParty(direction=NORTH))
        assert result.accepted
        assert location(session) == (1, (5, 1), EAST)
        moved = result.events[0]
        assert isinstance(moved, PartyMovedEvent)
        assert (moved.x, moved.y, moved.facing) == (5, 1, "east")
        assert not any(
            isinstance(event, LocationEnteredEvent) and event.location_kind in ("level", "dungeon")
            for event in result.events
        ), "a crossing within a level crosses no level boundary"
        shut = [event for event in result.events if event.code == "exploration.door.swung_shut"]
        assert [(event.x, event.y, event.direction) for event in shut] == [(2, 1, "west")]
        areas = [event for event in result.events if isinstance(event, LocationEnteredEvent)]
        assert [(event.location_kind, event.location_id) for event in areas] == [("area", "annex")]

    def test_the_pair_within_a_level_crosses_both_ways(self):
        session = keep_session()
        assert session.execute(MoveParty(direction=EAST)).accepted
        assert session.execute(MoveParty(direction=NORTH)).accepted
        assert location(session) == (1, (5, 1), EAST)
        assert session.execute(MoveParty(direction=WEST)).accepted
        assert location(session) == (1, (1, 1), SOUTH)


class TestNoCrossing:
    def test_walking_onto_a_passage_cell_fires_nothing(self):
        session = keep_session()
        at_east_passage(session)
        assert location(session) == (1, (2, 1), EAST)

    def test_landing_on_a_passage_cell_fires_nothing(self):
        session = keep_session()
        at_east_passage(session)
        session.execute(MoveParty(direction=EAST))
        assert location(session) == (2, (1, 1), EAST), "the party lands on the partner's cell and stays"

    def test_placing_the_party_on_a_passage_cell_fires_nothing(self):
        session = keep_session()
        result = session.execute(
            PlaceParty(
                location=PartyLocation(kind="dungeon", dungeon_id="keep", level_number=2, position=(1, 1), facing=WEST)
            )
        )
        assert result.accepted
        assert location(session) == (2, (1, 1), WEST)

    def test_a_move_in_another_direction_is_an_ordinary_move(self):
        session = keep_session()
        at_east_passage(session)
        session.execute(MoveParty(direction=EAST))
        result = session.execute(MoveParty(direction=EAST))
        assert result.accepted
        assert location(session) == (2, (2, 1), EAST)
        moved = result.events[0]
        assert isinstance(moved, PartyMovedEvent)
        assert (moved.x, moved.y) == (2, 1)

    def test_a_wall_beside_a_passage_still_blocks(self):
        session = keep_session()
        at_east_passage(session)
        session.execute(MoveParty(direction=EAST))
        result = session.execute(MoveParty(direction=NORTH))
        assert not result.accepted
        assert result.rejections[0].code == "exploration.move.blocked"
        assert location(session) == (2, (1, 1), EAST)

    def test_a_party_that_cannot_move_cannot_cross(self):
        session = keep_session()
        at_east_passage(session)
        definition = EffectDefinition(kind="web", condition=Condition.ENTANGLED)
        session.ledger.attach(
            definition, "character-0002", clock=session.clock, allocator=session.allocator, registry=session.registry()
        )
        result = session.execute(MoveParty(direction=EAST))
        assert not result.accepted
        assert result.rejections[0].code == "exploration.move.cannot_move"
        assert location(session) == (1, (2, 1), EAST)

    def test_use_stairs_refuses_a_cell_with_only_a_passage(self):
        session = keep_session()
        at_east_passage(session)
        result = session.execute(UseStairs())
        assert not result.accepted
        assert result.rejections[0].code == "exploration.stairs.none"
        assert location(session) == (1, (2, 1), EAST)

    def test_use_stairs_takes_the_stairs_on_a_cell_with_a_passage(self):
        session = keep_session()
        at_east_passage(session)
        session.execute(MoveParty(direction=EAST))
        result = session.execute(UseStairs())
        assert result.accepted
        assert location(session) == (1, (0, 1), EAST)
        entered = result.events[0]
        assert isinstance(entered, LocationEnteredEvent)
        assert entered.via == "stairs_up"


class TestGate:
    def test_a_refused_gate_rejects_the_move_and_costs_nothing(self):
        session = keep_session(TOLL)
        at_east_passage(session)
        before = state_probe(session)
        odometer = session.odometer_thirds
        result = session.execute(MoveParty(direction=EAST))
        assert not result.accepted
        assert result.rejections[0].code == "exploration.transition.gate_refused"
        assert result.rejections[0].params == {"refusal": "The arch stays dark."}
        assert state_probe(session) == before
        assert session.odometer_thirds == odometer
        assert location(session) == (1, (2, 1), EAST)

    def test_a_passed_gate_takes_its_toll_before_the_crossing(self):
        session = keep_session(TOLL)
        at_east_passage(session)
        session.execute(GrantItem(character_id="character-0001", item_id="toll_token"))
        result = session.execute(MoveParty(direction=EAST))
        assert result.accepted
        consumed, entered = result.events[0], result.events[1]
        assert consumed.code == "exploration.item.consumed"
        assert consumed.character_id == "character-0001"
        assert isinstance(entered, LocationEnteredEvent)
        assert entered.narrative == "The arch flares and lets you through."
        assert location(session) == (2, (1, 1), EAST)
        assert not any(
            instance.template.id == "toll_token" for instance in session.member("character-0001").inventory.items
        )


class TestView:
    def _level_view(self, session: GameSession, level_number: int):
        view = session.view(Visibility.PLAYER)
        return next(
            level for level in view.explored if (level.dungeon_id, level.level_number) == ("keep", level_number)
        )

    def test_the_player_view_shows_a_passage_side_as_open(self):
        session = keep_session()
        at_east_passage(session)
        level = self._level_view(session, 1)
        assert level.edges[edge_key((2, 1), EAST)].kind == "open"
        assert (3, 1) not in level.cells

    def test_light_stops_at_a_passage_side(self):
        session = keep_session()
        session.execute(GrantItem(character_id="character-0001", item_id="torch"))
        session.execute(GrantItem(character_id="character-0001", item_id="tinder_box"))
        assert session.execute(LightSource(character_id="character-0001", item_id="torch")).accepted
        at_east_passage(session)
        level = self._level_view(session, 1)
        assert level.edges[edge_key((2, 1), EAST)].kind == "open"
        assert (3, 1) not in level.cells, "the far cell stays dark"


class TestValidation:
    def test_the_fixture_is_valid(self):
        assert refusals(passage_adventure()) == ""

    def test_coordinates_shared_across_levels_are_valid(self):
        # Level 1's (3, 1) is a far cell and level 2's (3, 1) is floor, so a passage may land on
        # level 2's (3, 1). A passage on level 2's (2, 1) may land on level 1's (2, 1), which is
        # another level's cell with the same coordinates, not its own.
        adventure = passage_adventure()
        level_1, level_2 = adventure.dungeons[0].levels
        adventure = with_level(
            adventure, 1, transitions=(*level_1.transitions, passage((6, 1), NORTH, 2, (3, 1), WEST))
        )
        adventure = with_level(
            adventure, 2, transitions=(*level_2.transitions, passage((2, 1), SOUTH, 1, (2, 1), WEST))
        )
        assert refusals(adventure) == ""

    def test_a_passage_on_a_side_that_is_not_a_wall(self):
        adventure = passage_adventure()
        level_1 = adventure.dungeons[0].levels[0]
        edges = {**level_1.edges, edge_key((2, 1), EAST): Edge(kind=EdgeKind.OPEN)}
        assert "keep level 1: passage at (2, 1) east stands on an edge that is not a wall" in refusals(
            with_level(adventure, 1, edges=edges)
        )

    def test_a_far_cell_off_the_grid(self):
        adventure = passage_adventure()
        level_2 = adventure.dungeons[0].levels[1]
        transitions = (*level_2.transitions, passage((3, 1), EAST, 1, (0, 1), WEST))
        assert "keep level 2: passage at (3, 1) east has its far cell (4, 1) off the grid" in refusals(
            with_level(adventure, 2, transitions=transitions)
        )

    def test_a_far_cell_in_an_area(self):
        adventure = passage_adventure()
        level_1 = adventure.dungeons[0].levels[0]
        areas = (*level_1.areas, AreaSpec(id="closet", cells=((3, 1),)))
        assert "keep level 1: passage at (2, 1) east has its far cell (3, 1) in area 'closet'" in refusals(
            with_level(adventure, 1, areas=areas)
        )

    def test_a_far_cell_with_an_edge_that_is_not_a_wall(self):
        adventure = passage_adventure()
        level_1 = adventure.dungeons[0].levels[0]
        edges = {**level_1.edges, edge_key((3, 1), SOUTH): Edge(kind=EdgeKind.OPEN)}
        message = refusals(with_level(adventure, 1, edges=edges))
        assert "keep level 1: passage at (2, 1) east has a south edge on its far cell (3, 1) that is not a wall" in (
            message
        )
        assert "east stands on an edge that is not a wall" not in message

    def test_a_passage_that_lands_on_a_far_cell(self):
        adventure = passage_adventure()
        level_1 = adventure.dungeons[0].levels[0]
        transitions = (passage((2, 1), EAST, 2, (0, 1), EAST), *level_1.transitions[1:])
        assert "keep level 1: passage at (2, 1) east lands on keep level 2 (0, 1), the far cell of a passage" in (
            refusals(with_level(adventure, 1, transitions=transitions))
        )

    def test_two_passages_on_one_cell_and_side(self):
        adventure = passage_adventure()
        level_1 = adventure.dungeons[0].levels[0]
        transitions = (*level_1.transitions, passage((2, 1), EAST, 1, (6, 1), WEST))
        message = refusals(with_level(adventure, 1, transitions=transitions))
        assert message.count("keep level 1: two passages stand at (2, 1) east") == 1

    def test_a_passage_that_lands_on_its_own_cell(self):
        adventure = passage_adventure()
        level_1 = adventure.dungeons[0].levels[0]
        transitions = (level_1.transitions[0], passage((1, 1), NORTH, 1, (1, 1), SOUTH), level_1.transitions[2])
        assert "keep level 1: passage at (1, 1) north lands on its own cell" in refusals(
            with_level(adventure, 1, transitions=transitions)
        )

    def test_an_area_trap_whose_transition_is_a_passage(self):
        adventure = passage_adventure()
        level_2 = adventure.dungeons[0].levels[1]
        trap = TrapSpec(
            kind="room", trigger="enter", effect=TrapEffect(transition=passage((2, 1), EAST, 1, (6, 1), WEST))
        )
        areas = (level_2.areas[0].model_copy(update={"trap": trap}),)
        assert "keep level 2: area 'gallery' has a trap whose transition is a passage" in refusals(
            with_level(adventure, 2, areas=areas)
        )

    def test_a_feature_trap_whose_transition_is_a_passage(self):
        adventure = passage_adventure()
        trap = TrapSpec(
            kind="treasure", trigger="open", effect=TrapEffect(transition=passage((0, 1), SOUTH, 1, (6, 1), WEST))
        )
        features = (FeatureSpec(id="coffer", kind="custom", cell=(0, 1), trap=trap),)
        assert "keep level 1: feature 'coffer' has a trap whose transition is a passage" in refusals(
            with_level(adventure, 1, features=features)
        )


class TestSaves:
    def _crossed(self) -> tuple[GameSession, list]:
        session = keep_session()
        commands = [
            MoveParty(direction=EAST),
            OpenDoor(direction=EAST),
            MoveParty(direction=EAST),
            MoveParty(direction=EAST),
        ]
        for command in commands:
            assert session.execute(command).accepted
        return session, [EnterDungeon(dungeon_id="keep"), *commands]

    def test_a_saved_game_with_passages_loads_back(self):
        session, _ = self._crossed()
        restored = load_game(json.loads(json.dumps(save_game(session))))
        assert session_state(restored) == session_state(session)
        assert restored.adventure.dungeons[0].levels[0].passage_at((2, 1), EAST) is not None
        assert restored.execute(MoveParty(direction=WEST)).accepted
        assert location(restored) == (1, (2, 1), WEST)

    def test_a_replay_through_a_passage_matches_the_session(self):
        session, accepted = self._crossed()
        party_document = party_to_document(build_party().members)
        replayed = replay_game(SEED, party_document, passage_adventure(), Ruleset(), accepted)
        assert session_state(replayed) == session_state(session)
