"""Tests for the dungeon grid, adventure validation, party, and the state overlay."""

import pytest

from crawl_fixtures import build_adventure, build_party
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import validate_adventure
from osrlib.crawl.dungeon import (
    Direction,
    DungeonState,
    EdgeKind,
    PartyLocation,
    cell_ref,
    edge_key,
    edge_ref,
    step,
)
from osrlib.data import load_equipment, load_monsters
from osrlib.errors import ContentValidationError


class TestGeometry:
    def test_direction_vectors(self):
        # x increases east, y increases south, from the northwest corner (pinned).
        assert step((2, 2), Direction.NORTH) == (2, 1)
        assert step((2, 2), Direction.EAST) == (3, 2)
        assert step((2, 2), Direction.SOUTH) == (2, 3)
        assert step((2, 2), Direction.WEST) == (1, 2)

    def test_opposites(self):
        assert Direction.NORTH.opposite is Direction.SOUTH
        assert Direction.EAST.opposite is Direction.WEST

    def test_edge_canonicalization_one_entry_per_physical_edge(self):
        # A cell's south edge is its southern neighbour's north edge; east is the
        # eastern neighbour's west.
        assert edge_key((2, 0), Direction.SOUTH) == edge_key((2, 1), Direction.NORTH) == "2,1:north"
        assert edge_key((3, 1), Direction.EAST) == edge_key((4, 1), Direction.WEST) == "4,1:west"

    def test_boundary_is_implicitly_wall(self):
        level = build_adventure().dungeon("delve").level(1)
        assert level.edge((0, 0), Direction.NORTH).kind is EdgeKind.WALL
        assert level.edge((0, 0), Direction.WEST).kind is EdgeKind.WALL
        assert level.edge((4, 3), Direction.SOUTH).kind is EdgeKind.WALL

    def test_absent_edges_are_walls_and_authored_edges_resolve_from_both_sides(self):
        level = build_adventure().dungeon("delve").level(1)
        assert level.edge((0, 0), Direction.SOUTH).kind is EdgeKind.WALL
        assert level.edge((0, 0), Direction.EAST).kind is EdgeKind.OPEN
        assert level.edge((1, 0), Direction.WEST).kind is EdgeKind.OPEN
        door = level.edge((2, 0), Direction.SOUTH)
        assert door.kind is EdgeKind.DOOR and door.door.stuck
        assert level.edge((2, 1), Direction.NORTH) == door

    def test_area_and_transition_binding(self):
        level = build_adventure().dungeon("delve").level(1)
        assert level.area_at((2, 1)).id == "room_a"
        assert level.area_at((0, 0)) is None  # corridor
        assert level.transition_at((4, 1)).kind == "stairs_down"
        assert level.transition_at((0, 0)) is None


class TestValidateAdventure:
    def test_fixture_validates(self):
        validate_adventure(build_adventure(), load_monsters(), load_equipment())

    def test_failure_census(self):
        adventure = build_adventure()
        # Break several references at once and assert each is reported.
        broken = adventure.model_copy(
            update={
                "town": adventure.town.model_copy(update={"travel_turns": {"nowhere": 3}}),
            }
        )
        with pytest.raises(ContentValidationError, match="nowhere"):
            validate_adventure(broken, load_monsters(), load_equipment())

    def test_out_of_bounds_area_cell(self):
        adventure = build_adventure()
        dungeon = adventure.dungeon("delve")
        level = dungeon.level(1)
        bad_area = level.areas[0].model_copy(update={"cells": ((99, 99),)})
        bad_level = level.model_copy(update={"areas": (bad_area, *level.areas[1:])})
        bad = adventure.model_copy(
            update={"dungeons": (dungeon.model_copy(update={"levels": (bad_level, dungeon.levels[1])}),)}
        )
        with pytest.raises(ContentValidationError, match="out of bounds"):
            validate_adventure(bad, load_monsters(), load_equipment())

    def test_unknown_monster_and_item(self):
        adventure = build_adventure()
        dungeon = adventure.dungeon("delve")
        level = dungeon.level(1)
        room = next(area for area in level.areas if area.id == "room_a")
        bad_encounter = room.encounter.model_copy(
            update={"monsters": (room.encounter.monsters[0].model_copy(update={"template_id": "gazebo"}),)}
        )
        bad_chest = room.features[0].model_copy(update={"item_ids": ("vorpal_sword",)})
        bad_room = room.model_copy(update={"encounter": bad_encounter, "features": (bad_chest,)})
        bad_level = level.model_copy(update={"areas": (level.areas[0], bad_room)})
        bad = adventure.model_copy(
            update={"dungeons": (dungeon.model_copy(update={"levels": (bad_level, dungeon.levels[1])}),)}
        )
        with pytest.raises(ContentValidationError) as excinfo:
            validate_adventure(bad, load_monsters(), load_equipment())
        assert "gazebo" in str(excinfo.value)
        assert "vorpal_sword" in str(excinfo.value)

    def test_dangling_transition_target(self):
        adventure = build_adventure()
        dungeon = adventure.dungeon("delve")
        level = dungeon.level(1)
        bad_transition = level.transitions[0].model_copy(update={"to_level_number": 9})
        bad_level = level.model_copy(update={"transitions": (bad_transition,)})
        bad = adventure.model_copy(
            update={"dungeons": (dungeon.model_copy(update={"levels": (bad_level, dungeon.levels[1])}),)}
        )
        with pytest.raises(ContentValidationError, match="level 9"):
            validate_adventure(bad, load_monsters(), load_equipment())

    def test_missing_entrance(self):
        adventure = build_adventure()
        dungeon = adventure.dungeon("delve")
        levels = tuple(level.model_copy(update={"entrance": None}) for level in dungeon.levels)
        bad = adventure.model_copy(update={"dungeons": (dungeon.model_copy(update={"levels": levels}),)})
        with pytest.raises(ContentValidationError, match="entrance"):
            validate_adventure(bad, load_monsters(), load_equipment())


class TestDungeonState:
    def test_explored_marking_is_idempotent(self):
        state = DungeonState()
        state.mark_explored("delve", 1, (0, 0))
        state.mark_explored("delve", 1, (0, 0))
        state.mark_explored("delve", 1, (1, 0))
        assert state.explored["delve:1"] == [(0, 0), (1, 0)]
        assert state.is_explored("delve", 1, (1, 0))
        assert not state.is_explored("delve", 2, (0, 0))

    def test_door_state_created_on_first_touch(self):
        state = DungeonState()
        ref = edge_ref("delve", 1, (2, 0), Direction.SOUTH)
        assert ref == "delve:1:2,1:north"
        door = state.door(ref)
        door.open = True
        assert state.door(ref).open

    def test_state_overlay_round_trips(self):
        state = DungeonState(
            location=PartyLocation(kind="dungeon", dungeon_id="delve", level_number=1, position=(2, 1), facing="south")
        )
        state.mark_explored("delve", 1, (0, 0))
        state.door("delve:1:2,1:north").open = True
        state.sprung_traps.append("delve:1:pit_room")
        state.lock_failures["delve:1:2,1:north"] = {"pc-2": 1}
        restored = DungeonState.model_validate(state.model_dump(mode="json"))
        assert restored == state

    def test_cell_ref_format(self):
        # The pinned location-bound effect anchor format.
        assert cell_ref("delve", 1, (3, 2)) == "cell:delve:1:3,2"


class TestParty:
    def test_marching_order_is_the_member_list(self):
        party = build_party()
        assert [member.name for member in party.members] == ["Brakk", "Sable", "Wynn", "Elara"]

    def test_movement_rate_is_the_slowest_living(self):
        party = build_party()
        assert party.movement_rate(Ruleset()) == 120

    def test_ranks_chunk_living_members(self):
        party = build_party()
        for index, member in enumerate(party.members):
            member.id = f"character-{index + 1:04d}"
        assert [[m.name for m in rank] for rank in party.ranks(2)] == [["Brakk", "Sable"], ["Wynn", "Elara"]]
        assert [[m.name for m in rank] for rank in party.ranks(3)] == [["Brakk", "Sable", "Wynn"], ["Elara"]]

    def test_dead_members_collapse_out_of_ranks_but_stay_in_the_party(self):
        from osrlib.core.effects import kill

        party = build_party()
        for index, member in enumerate(party.members):
            member.id = f"character-{index + 1:04d}"
        kill(party.members[1])
        assert [[m.name for m in rank] for rank in party.ranks(2)] == [["Brakk", "Wynn"], ["Elara"]]
        assert len(party.members) == 4
        assert party.movement_rate(Ruleset()) == 120

    def test_reorder_requires_a_permutation(self):
        party = build_party()
        for index, member in enumerate(party.members):
            member.id = f"character-{index + 1:04d}"
        party.reorder(["character-0004", "character-0003", "character-0002", "character-0001"])
        assert [member.name for member in party.members] == ["Elara", "Wynn", "Sable", "Brakk"]
        with pytest.raises(ValueError):
            party.reorder(["character-0001"])
