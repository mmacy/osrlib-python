"""Tests for the dungeon grid, adventure validation, party, and the state overlay."""

import pytest

from crawl_fixtures import build_adventure, build_party
from osrlib.core.items import Coins
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec, validate_adventure
from osrlib.crawl.dungeon import (
    AreaSpec,
    Direction,
    DungeonSpec,
    DungeonState,
    EdgeKind,
    FeatureSpec,
    GeneratedCache,
    LevelSpec,
    PartyLocation,
    cell_ref,
    edge_key,
    edge_ref,
    step,
)
from osrlib.crawl.session import GameSession
from osrlib.data import load_equipment, load_monsters
from osrlib.errors import ContentValidationError
from osrlib.persistence import load_game, save_game


def canonical_ref_collision_adventure(kind: str) -> Adventure:
    """Build two dungeons whose different colon-bearing tuples produce one ref."""
    first_feature = FeatureSpec(id="cache", kind="treasure_cache", cell=(0, 0), coins=Coins(gp=1))
    second_feature = FeatureSpec(id="1:cache", kind="treasure_cache", cell=(0, 0), coins=Coins(gp=2))
    first_area = AreaSpec(id="cache", cells=((0, 0),))
    second_area = AreaSpec(id="1:cache", cells=((0, 0),))

    def dungeon(dungeon_id: str, feature: FeatureSpec, area: AreaSpec) -> DungeonSpec:
        return DungeonSpec(
            id=dungeon_id,
            levels=(
                LevelSpec(
                    number=1,
                    width=1,
                    height=1,
                    entrance=(0, 0),
                    areas=() if kind == "feature" else (area,),
                    features=() if kind == "area" else (feature,),
                ),
            ),
        )

    return Adventure(
        name="Ambiguous refs",
        town=TownSpec(name="Threshold"),
        dungeons=(
            dungeon("vault:1", first_feature, first_area),
            dungeon("vault", second_feature, second_area),
        ),
    )


def duplicate_definition_adventure(kind: str) -> Adventure:
    """Build one level with two features or areas that have the same id."""
    feature = FeatureSpec(id="repeat", kind="treasure_cache", cell=(0, 0), coins=Coins(gp=1))
    area = AreaSpec(id="repeat", cells=((0, 0),))
    level = LevelSpec(
        number=1,
        width=1,
        height=1,
        entrance=(0, 0),
        areas=(area, area.model_copy()) if kind == "area" else (),
        features=(feature, feature.model_copy()) if kind == "feature" else (),
    )
    return Adventure(
        name="Repeated definitions",
        town=TownSpec(name="Threshold"),
        dungeons=(DungeonSpec(id="duplicate", levels=(level,)),),
    )


def construct_session_without_validation(adventure: Adventure) -> GameSession:
    """Call the direct constructor, which checks canonical refs but not all content."""
    seed = 7
    return GameSession(
        party=build_party(),
        adventure=adventure,
        ruleset=Ruleset(),
        streams=RngStreams(master_seed=seed),
        master_seed=seed,
    )


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

    @pytest.mark.parametrize("kind", ["area", "feature"])
    def test_canonical_area_and_feature_refs_reject_component_collisions(self, kind: str):
        adventure = canonical_ref_collision_adventure(kind)

        with pytest.raises(ContentValidationError) as excinfo:
            validate_adventure(adventure, load_monsters(), load_equipment())

        message = str(excinfo.value)
        assert "canonical reference 'vault:1:1:cache' is repeated" in message
        assert "dungeon 'vault:1', level 1, id 'cache', " in message
        assert "dungeon 'vault', level 1, id '1:cache', " in message

    def test_canonical_feature_collision_fails_before_session_state_exists(self):
        party = build_party()
        adventure = canonical_ref_collision_adventure("feature")

        with pytest.raises(ContentValidationError, match="canonical reference .* is repeated"):
            GameSession.new(party, adventure, seed=7)

        assert all(member.id is None for member in party.members)

    def test_load_rejects_canonical_feature_collision_before_commands_can_alias(self):
        source = GameSession.new(build_party(), build_adventure(), seed=7)
        document = save_game(source)
        document["payload"]["adventure"] = canonical_ref_collision_adventure("feature").model_dump(mode="json")

        with pytest.raises(ContentValidationError, match="canonical reference .* is repeated"):
            load_game(document)

    @pytest.mark.parametrize("kind", ["area", "feature"])
    def test_direct_constructor_rejects_duplicate_same_id_definitions(self, kind: str):
        with pytest.raises(ContentValidationError) as excinfo:
            construct_session_without_validation(duplicate_definition_adventure(kind))

        message = str(excinfo.value)
        assert "canonical reference 'duplicate:1:repeat' is repeated" in message
        assert "occurrence 1" in message
        assert "occurrence 2" in message

    @pytest.mark.parametrize(
        ("kind", "established_error"),
        [
            ("area", "area ids are not unique"),
            ("feature", "feature ids are not unique"),
        ],
    )
    def test_new_keeps_established_duplicate_id_errors(self, kind: str, established_error: str):
        with pytest.raises(ContentValidationError) as excinfo:
            GameSession.new(build_party(), duplicate_definition_adventure(kind), seed=7)

        message = str(excinfo.value)
        assert established_error in message
        assert message.count("canonical reference 'duplicate:1:repeat' is repeated") == 1

    @pytest.mark.parametrize("kind", ["area", "feature"])
    def test_load_rejects_duplicate_same_id_definitions_before_state_validation(self, kind: str):
        document = save_game(GameSession.new(build_party(), build_adventure(), seed=7))
        document["payload"]["adventure"] = duplicate_definition_adventure(kind).model_dump(mode="json")
        document["payload"]["dungeon_state"]["generated_caches"] = {
            "cache-defective": {
                "cell_ref": "malformed-on-purpose",
                "treasure_types": [],
            }
        }

        with pytest.raises(ContentValidationError) as excinfo:
            load_game(document)

        message = str(excinfo.value)
        assert "canonical reference 'duplicate:1:repeat' is repeated" in message
        assert "occurrence 1" in message
        assert "occurrence 2" in message
        assert "save payload failed validation" not in message

    def test_unique_colon_ids_construct_and_load_without_collisions(self):
        adventure = Adventure(
            name="Unique colon ids",
            town=TownSpec(name="Threshold"),
            dungeons=(
                DungeonSpec(
                    id="vault:outer",
                    levels=(
                        LevelSpec(
                            number=1,
                            width=2,
                            height=1,
                            entrance=(0, 0),
                            areas=(AreaSpec(id="room:inner", cells=((0, 0),)),),
                            features=(
                                FeatureSpec(
                                    id="cache:outer",
                                    kind="treasure_cache",
                                    cell=(1, 0),
                                    coins=Coins(gp=1),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )

        validate_adventure(adventure, load_monsters(), load_equipment())
        session = construct_session_without_validation(adventure)
        restored = load_game(save_game(session))

        assert restored.adventure == adventure

    def test_load_rejects_colliding_area_ref_with_legacy_cache_state(self):
        source = GameSession.new(build_party(), build_adventure(), seed=7)
        document = save_game(source)
        document["payload"]["adventure"] = canonical_ref_collision_adventure("area").model_dump(mode="json")
        ambiguous_ref = "vault:1:1:cache"
        document["payload"]["dungeon_state"]["resolved_encounters"] = [ambiguous_ref]
        document["payload"]["dungeon_state"]["generated_caches"] = {
            "cache-legacy": {
                "cell_ref": cell_ref("vault:1", 1, (0, 0)),
                "treasure_types": [],
                "coins": {"pp": 0, "gp": 10, "ep": 0, "sp": 0, "cp": 0},
                "valuables": [],
                "magic_items": [],
            }
        }

        with pytest.raises(ContentValidationError) as excinfo:
            load_game(document)

        assert "canonical reference 'vault:1:1:cache' is repeated" in str(excinfo.value)

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

    def test_placed_magic_item_ids_resolve_against_the_shipped_catalog(self):
        adventure = build_adventure()
        dungeon = adventure.dungeon("delve")
        level = dungeon.level(1)
        room = next(area for area in level.areas if area.id == "room_a")
        chest = room.features[0].model_copy(update={"magic_item_ids": ("sword_plus_1", "wand_of_fear")})
        good_room = room.model_copy(update={"features": (chest,)})
        good_level = level.model_copy(update={"areas": (level.areas[0], good_room)})
        good = adventure.model_copy(
            update={"dungeons": (dungeon.model_copy(update={"levels": (good_level, dungeon.levels[1])}),)}
        )
        validate_adventure(good, load_monsters(), load_equipment())

    def test_unknown_magic_item(self):
        adventure = build_adventure()
        dungeon = adventure.dungeon("delve")
        level = dungeon.level(1)
        room = next(area for area in level.areas if area.id == "room_a")
        bad_chest = room.features[0].model_copy(update={"magic_item_ids": ("sword_plus_6",)})
        bad_room = room.model_copy(update={"features": (bad_chest,)})
        bad_level = level.model_copy(update={"areas": (level.areas[0], bad_room)})
        bad = adventure.model_copy(
            update={"dungeons": (dungeon.model_copy(update={"levels": (bad_level, dungeon.levels[1])}),)}
        )
        with pytest.raises(ContentValidationError, match="unknown magic item 'sword_plus_6'"):
            validate_adventure(bad, load_monsters(), load_equipment())

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

    def test_seen_marking_is_idempotent_and_deterministically_ordered(self):
        state = DungeonState()
        state.mark_seen("delve", 1, {(2, 0), (0, 0), (1, 0)})  # a set: input order must not matter
        assert state.seen["delve:1"] == [(0, 0), (1, 0), (2, 0)]
        state.mark_seen("delve", 1, [(1, 0), (3, 0), (3, 0)])
        assert state.seen["delve:1"] == [(0, 0), (1, 0), (2, 0), (3, 0)]
        state.mark_seen("delve", 1, [(0, 0)])
        assert state.seen["delve:1"] == [(0, 0), (1, 0), (2, 0), (3, 0)]
        assert "delve:2" not in state.seen
        # Seeing is not exploring: the footprint stays empty.
        assert not state.is_explored("delve", 1, (0, 0))

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
        state.mark_seen("delve", 1, [(0, 0), (1, 0), (0, 1)])
        state.door("delve:1:2,1:north").open = True
        state.sprung_traps.append("delve:1:pit_room")
        state.lock_failures["delve:1:2,1:north"] = {"pc-2": 1}
        restored = DungeonState.model_validate(state.model_dump(mode="json"))
        assert restored == state

    def test_old_generated_cache_without_lair_area_ref_loads(self):
        cache = GeneratedCache.model_validate(
            {
                "cell_ref": "cell:delve:1:3,2",
                "treasure_types": ["C"],
                "coins": {"pp": 0, "gp": 12, "ep": 0, "sp": 0, "cp": 0},
                "valuables": [],
                "magic_items": [],
            }
        )
        assert cache.origin == "legacy"
        assert cache.lair_area_ref is None

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


class TestTrapContentChecks:
    """Two authorable trap configurations `validate_adventure` reports before play.

    Area ids and feature ids share the trap-reference namespace (`"<dungeon>:<level>:<id>"`), so an
    area and a feature with the same id on one level would cross-contaminate found, sprung, and
    removed state; the check rejects the collision. And an `open`-trigger room trap springs only
    when a door on the area's boundary is opened, so an area with no door edge at all can never
    fire it; the check rejects that too. A door that `starts_open` still counts, because a referee
    command or a trigger can close it.
    """

    @staticmethod
    def _with_level_1(adventure, level):
        dungeon = adventure.dungeon("delve")
        return adventure.model_copy(
            update={"dungeons": (dungeon.model_copy(update={"levels": (level, dungeon.levels[1])}),)}
        )

    def test_an_area_and_a_feature_cannot_share_an_id(self):
        adventure = build_adventure()
        level = adventure.dungeon("delve").level(1)
        twin = FeatureSpec(
            id="pit_room", kind="treasure_cache", description="A coffer.", cell=(0, 0), coins=Coins(gp=1)
        )
        bad = self._with_level_1(adventure, level.model_copy(update={"features": (twin,)}))
        with pytest.raises(ContentValidationError, match="id 'pit_room' names both an area and a feature"):
            validate_adventure(bad, load_monsters(), load_equipment())

    def test_an_open_trigger_room_trap_needs_a_door_on_its_boundary(self):
        adventure = build_adventure()
        level = adventure.dungeon("delve").level(1)
        pit_room = level.areas[0]
        assert pit_room.id == "pit_room"  # one cell, (1,1), reached through an open edge and nothing else
        doorless = pit_room.model_copy(update={"trap": pit_room.trap.model_copy(update={"trigger": "open"})})
        bad = self._with_level_1(adventure, level.model_copy(update={"areas": (doorless, *level.areas[1:])}))
        with pytest.raises(ContentValidationError, match="area 'pit_room' has an open-trigger trap and no door"):
            validate_adventure(bad, load_monsters(), load_equipment())

    def test_an_enter_trigger_trap_needs_no_door(self):
        validate_adventure(build_adventure(), load_monsters(), load_equipment())

    def test_a_door_that_starts_open_still_counts_as_a_door(self):
        adventure = build_adventure()
        level = adventure.dungeon("delve").level(1)
        room = next(area for area in level.areas if area.id == "room_a")
        trapped = room.model_copy(update={"trap": level.areas[0].trap.model_copy(update={"trigger": "open"})})
        key = edge_key((2, 0), Direction.SOUTH)  # the stuck door into room_a
        door = level.edges[key]
        edges = {
            **level.edges,
            key: door.model_copy(update={"door": door.door.model_copy(update={"starts_open": True})}),
        }
        good_level = level.model_copy(update={"areas": (level.areas[0], trapped), "edges": edges})
        validate_adventure(self._with_level_1(adventure, good_level), load_monsters(), load_equipment())
