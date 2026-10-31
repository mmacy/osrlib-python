"""Acceptance tests for a door write that keeps the door's authored state, and `end_encounter` with nothing open.

A door's stored state starts from the door as the adventure wrote it. Before the fix,
the first write through [`DungeonState.door`][osrlib.crawl.dungeon.DungeonState.door]
stored a state with every field `False`. Play and the player view read the stored
`open` before the authored `starts_open`, so writing an unrelated field, such as
`discovered`, closed a door that starts open and stopped the party from walking through.
Issue #159 fixed the same problem for reads.

The contract these tests check:

- A new state from `DungeonState.door` starts from the door's `DoorSpec`. A door that
  starts open reads open, before and after a write to any other field, and the write
  changes only the field it sets.
- The party walks through such a door, and the player view shows it open.
- A referee's `SetDoorState` still closes it, and so does writing `open = False`.
- A save and a load keep it open, and a loaded session's `DungeonState.door` starts new
  states from the same authored defaults.
- A door that starts closed stays closed after a write to another field.
- `end_encounter` with no open encounter raises `ValueError` before it changes anything
  or takes a draw, as the kernel-argument rule in `AGENTS.md` requires.

Tests marked `xfail` fail until the chunk lands. Unmarked tests pass today and guard
behavior the fix must keep.
"""

import pytest

from crawl_fixtures import build_open_door_adventure, build_party
from osrlib.core.events import Visibility
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import EnterDungeon, MoveParty, SessionMode, SetDoorState
from osrlib.crawl.dungeon import (
    Direction,
    DoorSpec,
    DoorState,
    DungeonSpec,
    DungeonState,
    Edge,
    EdgeKind,
    LevelSpec,
    WanderingSpec,
    edge_key,
)
from osrlib.crawl.encounter import end_encounter
from osrlib.crawl.session import GameSession
from osrlib.persistence import load_game, save_game

CHUNK = pytest.mark.xfail(reason="chunk: door-and-end", strict=True)

DOOR_REF = "vestibule:1:1,0:west"
"""The only door in both fixtures, east of the entrance cell (0, 0)."""

SET_DOOR = {"dungeon_id": "vestibule", "level_number": 1, "x": 1, "y": 0, "direction": Direction.WEST}
"""The `SetDoorState` address of `DOOR_REF`."""

OTHER_FIELDS = ["discovered", "unlocked", "wedged"]
"""Fields a caller can write without meaning to open or close the door."""


def build_closed_door_adventure() -> Adventure:
    """Build the same two cells as `build_open_door_adventure`, with a door that starts closed."""
    edges = {edge_key((0, 0), Direction.EAST): Edge(kind=EdgeKind.DOOR, door=DoorSpec())}
    level = LevelSpec(
        number=1, width=2, height=1, edges=edges, entrance=(0, 0), wandering=WanderingSpec(chance_in_six=0)
    )
    return Adventure(
        name="The Shut Door",
        description="Two cells and a door that starts closed.",
        town=TownSpec(name="Threshold", travel_turns={"vestibule": 1}),
        dungeons=(DungeonSpec(id="vestibule", name="Vestibule", levels=(level,)),),
    )


def session_at_door(adventure: Adventure) -> GameSession:
    """The party at (0, 0), facing the untouched door to the east."""
    session = GameSession.new(build_party(), adventure, seed=5)
    assert session.execute(EnterDungeon(dungeon_id="vestibule")).accepted
    assert session.dungeon_state.location.position == (0, 0)
    assert DOOR_REF not in session.dungeon_state.doors, "the door must be untouched for the case to mean anything"
    return session


def open_door_session() -> GameSession:
    """The party beside a door whose `DoorSpec` sets `starts_open`."""
    session = session_at_door(build_open_door_adventure())
    edge = session.adventure.dungeon("vestibule").level(1).edge((0, 0), Direction.EAST)
    assert edge.door is not None and edge.door.starts_open
    return session


def closed_door_session() -> GameSession:
    """The party beside a door that starts closed."""
    session = session_at_door(build_closed_door_adventure())
    edge = session.adventure.dungeon("vestibule").level(1).edge((0, 0), Direction.EAST)
    assert edge.door is not None and not edge.door.starts_open
    return session


def door_view_open(session: GameSession) -> bool | None:
    """The player view's `door_open` for the vestibule door."""
    view = session.view(Visibility.PLAYER)
    (level,) = [level for level in view.explored if level.dungeon_id == "vestibule"]
    doors = [edge for edge in level.edges.values() if edge.kind == "door"]
    assert len(doors) == 1
    return doors[0].door_open


def expected_state(**fields: bool) -> dict[str, bool]:
    """A door state's fields as `model_dump` gives them: `False` except those named."""
    return {**DoorState().model_dump(), **fields}


def walk_east(session: GameSession) -> list[str]:
    """Move the party east, and return the rejection codes, which are empty when it moved."""
    result = session.execute(MoveParty(direction=Direction.EAST))
    return [rejection.code for rejection in result.rejections]


# ---------------------------------------------------------------------- a door that starts open


class TestReadingADoorThatStartsOpen:
    """A new state from `DungeonState.door` reads the door as the adventure wrote it."""

    @CHUNK
    def test_a_read_reads_it_open(self):
        session = open_door_session()
        assert session.dungeon_state.door(DOOR_REF).model_dump() == expected_state(open=True)

    def test_a_read_stores_nothing(self):
        session = open_door_session()
        _ = session.dungeon_state.door(DOOR_REF).open
        assert DOOR_REF not in session.dungeon_state.doors


class TestWritingAnotherFieldKeepsItOpen:
    """Writing one field of a door that starts open stores an entry that is still open."""

    @CHUNK
    @pytest.mark.parametrize("field", OTHER_FIELDS)
    def test_the_write_changes_only_that_field(self, field):
        session = open_door_session()
        setattr(session.dungeon_state.door(DOOR_REF), field, True)
        assert DOOR_REF in session.dungeon_state.doors
        assert session.dungeon_state.doors[DOOR_REF].model_dump() == expected_state(open=True, **{field: True})

    @CHUNK
    @pytest.mark.parametrize("field", OTHER_FIELDS)
    def test_the_party_walks_through(self, field):
        session = open_door_session()
        setattr(session.dungeon_state.door(DOOR_REF), field, True)
        assert walk_east(session) == []
        assert session.dungeon_state.location.position == (1, 0)

    @CHUNK
    @pytest.mark.parametrize("field", OTHER_FIELDS)
    def test_the_player_view_shows_it_open(self, field):
        session = open_door_session()
        assert door_view_open(session) is True
        setattr(session.dungeon_state.door(DOOR_REF), field, True)
        assert door_view_open(session) is True

    @CHUNK
    def test_a_write_from_another_read_goes_to_the_same_entry(self):
        session = open_door_session()
        first = session.dungeon_state.door(DOOR_REF)
        second = session.dungeon_state.door(DOOR_REF)
        first.discovered = True
        second.unlocked = True
        assert session.dungeon_state.doors[DOOR_REF].model_dump() == expected_state(
            open=True, discovered=True, unlocked=True
        )


class TestClosingADoorThatStartsOpen:
    """The fix keeps every way to close the door."""

    def test_set_door_state_closes_an_untouched_door(self):
        session = open_door_session()
        result = session.execute(SetDoorState(**SET_DOOR, open=False))
        assert result.accepted
        assert [event.code for event in result.events] == ["exploration.door.closed"]
        assert session.dungeon_state.door(DOOR_REF).open is False
        assert door_view_open(session) is False
        assert walk_east(session) == ["exploration.move.blocked"]

    @CHUNK
    def test_set_door_state_closes_it_after_a_write_of_another_field(self):
        session = open_door_session()
        session.dungeon_state.door(DOOR_REF).discovered = True
        result = session.execute(SetDoorState(**SET_DOOR, open=False))
        assert result.accepted
        assert [event.code for event in result.events] == ["exploration.door.closed"]
        assert session.dungeon_state.doors[DOOR_REF].model_dump() == expected_state(discovered=True)
        assert door_view_open(session) is False
        assert walk_east(session) == ["exploration.move.blocked"]

    def test_writing_open_false_closes_it(self):
        session = open_door_session()
        session.dungeon_state.door(DOOR_REF).open = False
        assert session.dungeon_state.doors[DOOR_REF].open is False
        assert door_view_open(session) is False
        assert walk_east(session) == ["exploration.move.blocked"]


class TestSavingADoorThatStartsOpen:
    """A save and a load keep the door open, and a loaded session seeds new states the same way."""

    @CHUNK
    def test_a_written_door_is_open_after_a_load(self):
        session = open_door_session()
        session.dungeon_state.door(DOOR_REF).discovered = True
        restored = load_game(save_game(session))
        assert restored.dungeon_state.doors[DOOR_REF].model_dump() == expected_state(open=True, discovered=True)
        assert door_view_open(restored) is True
        assert walk_east(restored) == []
        assert restored.dungeon_state.location.position == (1, 0)

    @CHUNK
    def test_a_loaded_session_seeds_a_new_state_from_the_door(self):
        session = open_door_session()
        restored = load_game(save_game(session))
        assert DOOR_REF not in restored.dungeon_state.doors
        assert restored.dungeon_state.door(DOOR_REF).open is True
        restored.dungeon_state.door(DOOR_REF).discovered = True
        assert restored.dungeon_state.doors[DOOR_REF].model_dump() == expected_state(open=True, discovered=True)
        assert walk_east(restored) == []


# ---------------------------------------------------------------------- a door that starts closed


class TestADoorThatStartsClosed:
    """A door that starts closed stays closed after a write to another field."""

    def test_a_read_reads_it_closed(self):
        session = closed_door_session()
        assert session.dungeon_state.door(DOOR_REF).model_dump() == expected_state()
        assert DOOR_REF not in session.dungeon_state.doors

    @pytest.mark.parametrize("field", OTHER_FIELDS)
    def test_a_write_of_another_field_keeps_it_closed(self, field):
        session = closed_door_session()
        setattr(session.dungeon_state.door(DOOR_REF), field, True)
        assert session.dungeon_state.doors[DOOR_REF].model_dump() == expected_state(**{field: True})
        assert door_view_open(session) is False
        assert walk_east(session) == ["exploration.move.blocked"]

    def test_it_stays_closed_after_a_load(self):
        session = closed_door_session()
        session.dungeon_state.door(DOOR_REF).discovered = True
        restored = load_game(save_game(session))
        assert restored.dungeon_state.door(DOOR_REF).open is False
        assert walk_east(restored) == ["exploration.move.blocked"]


class TestADungeonStateWithNoSession:
    """A `DungeonState` made on its own has no adventure, so a new state reads `False` in every field."""

    def test_a_new_state_is_all_false(self):
        state = DungeonState()
        assert state.door(DOOR_REF).model_dump() == expected_state()
        state.door(DOOR_REF).discovered = True
        assert state.doors[DOOR_REF].model_dump() == expected_state(discovered=True)


# ---------------------------------------------------------------------- end_encounter with nothing open


class TestEndEncounterWithNothingOpen:
    """`end_encounter` refuses a session with no open encounter before it changes or draws anything."""

    @CHUNK
    def test_it_raises_value_error_and_changes_nothing(self):
        session = open_door_session()
        assert session.encounter is None
        assert session.mode is SessionMode.EXPLORING
        before = save_game(session)
        streams_before = session.streams.export_states()
        with pytest.raises(ValueError, match="encounter"):
            end_encounter(session, "departed")
        assert session.streams.export_states() == streams_before
        assert save_game(session) == before
        assert session.encounter is None
        assert session.mode is SessionMode.EXPLORING

    @CHUNK
    def test_it_raises_value_error_in_town(self):
        session = GameSession.new(build_party(), build_open_door_adventure(), seed=5)
        assert session.encounter is None
        assert session.dungeon_state.location.kind == "town"
        before = save_game(session)
        with pytest.raises(ValueError, match="encounter"):
            end_encounter(session, "victory")
        assert save_game(session) == before
