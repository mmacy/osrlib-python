"""SRD dungeon stocking tests: `stock_area` goldens and the keyed-hoard gate.

`stock_area` is deterministic from its stream, so the goldens pin a seed's exact
answer — a change in the pinned draw order (contents d6, treasure d6, d20, count,
then variant/pool) breaks them. The monster-branch cases (variant, pool, NPC
party, count-pinning, catalog resolution) drive a *uniform* table — twenty d20
rows carrying the same entry — so whatever the d20 rolls, the row under test is
selected. The gate tests drive a real session into a keyed area and compare the
same seed with `hoard=True` and `hoard=False`.
"""

import pytest

from crawl_fixtures import build_party
from osrlib.core.rng import RngStream
from osrlib.core.tables import (
    EncounterTable,
    EncounterTableRow,
    MonsterEncounterEntry,
    NpcPartyEncounterEntry,
    select_encounter_individuals,
)
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import EnterDungeon, MoveParty
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
    edge_key,
)
from osrlib.crawl.session import GameSession
from osrlib.crawl.stocking import StockedNpcParty, stock_area
from osrlib.data import load_monsters

CATALOG = load_monsters()
KEY = "stock:test"


def fresh(seed: int) -> RngStream:
    return RngStream.from_seed_material(seed, KEY)


def stock(seed: int, table: EncounterTable | None = None):
    return stock_area(1, catalog=CATALOG, stream=fresh(seed), table=table)


def uniform(entry, *, count_fixed=None, count_dice=None) -> EncounterTable:
    """A 20-row d20 table carrying the same entry at every roll — the d20 is fixed out."""
    rows = tuple(
        EncounterTableRow(roll=i, name=f"row{i}", entry=entry, count_fixed=count_fixed, count_dice=count_dice)
        for i in range(1, 21)
    )
    return EncounterTable(id="custom", label="Custom", min_level=1, max_level=1, rows=rows)


def find_monster(table: EncounterTable, limit: int = 400):
    """The first seed whose stocking d6 lands a monster room over the given table."""
    for seed in range(limit):
        area = stock(seed, table)
        if area.contents == "monster":
            return seed
    raise AssertionError("no monster room rolled within the seed budget")


class TestStockAreaGoldens:
    """Pinned per-contents-kind answers over the shipped level-1 band table."""

    def test_monster_without_treasure(self):
        area = stock(0)
        assert area.contents == "monster" and area.treasure_present is False
        assert area.treasure is None and area.npc_party is None
        assert area.encounter == KeyedEncounter(monsters=(KeyedMonster(template_id="orc", count_fixed=2),), hoard=False)

    def test_monster_with_treasure_sets_hoard(self):
        area = stock(2)
        assert area.contents == "monster" and area.treasure_present is True
        # The encounter is the monster room's treasure declaration; no AreaTreasureSpec.
        assert area.treasure is None
        assert area.encounter == KeyedEncounter(
            monsters=(KeyedMonster(template_id="spitting_cobra", count_fixed=5),), hoard=True
        )

    def test_empty_with_treasure_is_unguarded(self):
        area = stock(7)
        assert area.contents == "empty" and area.treasure_present is True
        assert area.treasure is not None and area.treasure.unguarded is True and area.treasure.letters == ()
        assert area.encounter is None and area.npc_party is None

    def test_trap_with_treasure_is_unguarded(self):
        area = stock(18)
        assert area.contents == "trap" and area.treasure_present is True
        assert area.treasure is not None and area.treasure.unguarded is True
        assert area.encounter is None

    def test_special_carries_no_model(self):
        area = stock(1)
        assert area.contents == "special" and area.treasure_present is False
        assert area.encounter is None and area.npc_party is None and area.treasure is None


class TestMonsterBranch:
    """The d20 resolution mirrored on the crawl's wandering conventions."""

    def test_counts_are_pinned_as_fixed(self):
        table = uniform(MonsterEncounterEntry(monster_ids=("goblin",)), count_dice="2d4")
        area = stock(find_monster(table), table)
        assert area.encounter is not None
        line = area.encounter.monsters[0]
        assert line.count_fixed is not None and line.count_dice is None

    def test_variant_row_selects_one_template(self):
        # 1d3 spans three ids; the roll selects one, and all individuals are it.
        table = uniform(
            MonsterEncounterEntry(monster_ids=("goblin", "orc", "kobold"), variant_dice="1d3"), count_fixed=3
        )
        area = stock(find_monster(table), table)
        assert area.encounter == KeyedEncounter(
            monsters=(KeyedMonster(template_id="goblin", count_fixed=3),), hoard=area.treasure_present
        )

    def test_pool_row_mixes_individuals_and_groups_by_template(self):
        table = uniform(MonsterEncounterEntry(monster_ids=("goblin", "orc", "kobold")), count_dice="2d4")
        area = stock(find_monster(table), table)
        assert area.encounter is not None
        lines = area.encounter.monsters
        # Every id comes from the pool, and the per-template counts sum to the roll.
        assert all(line.template_id in ("goblin", "orc", "kobold") for line in lines)
        assert all(line.count_fixed is not None for line in lines)
        total = sum(line.count_fixed for line in lines)
        assert 2 <= total <= 8

    def test_pool_seed_zero_is_pinned(self):
        table = uniform(MonsterEncounterEntry(monster_ids=("goblin", "orc", "kobold")), count_dice="2d4")
        area = stock(0, table)
        assert area.contents == "monster"
        assert area.encounter == KeyedEncounter(
            monsters=(
                KeyedMonster(template_id="kobold", count_fixed=1),
                KeyedMonster(template_id="orc", count_fixed=1),
            ),
            hoard=area.treasure_present,
        )

    def test_npc_party_row_reports_kind_and_count(self):
        table = uniform(NpcPartyEncounterEntry(party_kind="basic"), count_fixed=4)
        area = stock(find_monster(table), table)
        assert area.encounter is None and area.treasure is None
        assert area.npc_party == StockedNpcParty(kind="basic", count=4)

    def test_unknown_monster_id_raises(self):
        good = uniform(MonsterEncounterEntry(monster_ids=("goblin",)), count_fixed=1)
        seed = find_monster(good)
        bad = uniform(MonsterEncounterEntry(monster_ids=("not_a_real_monster",)), count_fixed=1)
        with pytest.raises(ValueError, match="unknown monster id"):
            stock(seed, bad)


class TestDeterminism:
    def test_same_seed_same_area(self):
        assert stock(42).model_dump() == stock(42).model_dump()

    def test_stream_advances_deterministically(self):
        first, second = fresh(42), fresh(42)
        stock_area(1, catalog=CATALOG, stream=first)
        stock_area(1, catalog=CATALOG, stream=second)
        assert first.export_state() == second.export_state()

    def test_per_address_streams_are_independent(self):
        # Two addresses off one master seed roll independently — re-rolling one
        # never disturbs the other, which is what order-independent re-rolls need.
        room_7 = RngStream.from_seed_material(99, "stock:d/1/7")
        room_9 = RngStream.from_seed_material(99, "stock:d/1/9")
        area_7a = stock_area(1, catalog=CATALOG, stream=room_7).model_dump()
        area_9 = stock_area(1, catalog=CATALOG, stream=room_9).model_dump()
        # Re-derive room 7 from scratch; room 9 was never touched by it.
        area_7b = stock_area(1, catalog=CATALOG, stream=RngStream.from_seed_material(99, "stock:d/1/7")).model_dump()
        assert area_7a == area_7b
        assert (
            area_9
            == stock_area(1, catalog=CATALOG, stream=RngStream.from_seed_material(99, "stock:d/1/9")).model_dump()
        )


def _hoard_adventure(hoard: bool) -> Adventure:
    """A one-corridor dungeon: entrance at (0,0), a keyed goblin den (lair type C) east at (1,0)."""
    edges: dict[str, Edge] = {edge_key((0, 0), Direction.EAST): Edge(kind=EdgeKind.OPEN)}
    level = LevelSpec(
        number=1,
        width=3,
        height=1,
        edges=edges,
        areas=(
            AreaSpec(
                id="1",
                name="Goblin den",
                cells=((1, 0),),
                encounter=KeyedEncounter(monsters=(KeyedMonster(template_id="goblin", count_fixed=2),), hoard=hoard),
            ),
        ),
        entrance=(0, 0),
        wandering=WanderingSpec(chance_in_six=0),
    )
    return Adventure(
        name="Hoard test",
        description="One keyed room.",
        town=TownSpec(name="Threshold", services=("inn",), travel_turns={"d": 1}),
        dungeons=(DungeonSpec(id="d", name="Den", levels=(level,)),),
    )


def _run_keyed(hoard: bool, seed: int) -> GameSession:
    # Walking into the keyed area (not teleporting there) runs arrival processing,
    # which is what fires the keyed-encounter check and its gated lair hoard.
    session = GameSession.new(build_party(), _hoard_adventure(hoard), seed=seed)
    session.execute(EnterDungeon(dungeon_id="d"))
    session.execute(MoveParty(direction=Direction.EAST))
    return session


class TestHoardGate:
    def test_hoard_flag_gates_the_lair_cache(self):
        # Find a seed whose goblin lair (type C) actually yields a cache with hoard on.
        seed = next(
            (s for s in range(200) if _run_keyed(True, s).dungeon_state.generated_caches),
            None,
        )
        assert seed is not None, "no non-empty goblin lair hoard within the seed budget"
        on = _run_keyed(True, seed)
        # Exactly one cache: the lair. There is no AreaTreasureSpec, so the first-entry
        # treasure path contributes nothing — treasure generates exactly once.
        assert len(on.dungeon_state.generated_caches) == 1
        # The same seed with the gate closed generates nothing at all.
        off = _run_keyed(False, seed)
        assert off.dungeon_state.generated_caches == {}

    def test_hoard_false_emits_no_hoard_event(self):
        result = _run_keyed(False, 3)
        codes = {event.code for event in result.event_log}
        assert "treasure.hoard.generated" not in codes


class TestSharedResolution:
    """`select_encounter_individuals` is the one home both wandering_check and stock_area
    consume, so a stocked row can never drift from a wandering roll on that row. These pin
    the resolver's own draw so a change to it is a deliberate, visible edit."""

    def test_variant_row_selects_one_template_for_the_group(self):
        entry = MonsterEncounterEntry(monster_ids=("a", "b", "c"), variant_dice="1d3")
        ids = select_encounter_individuals(entry, 4, fresh(0))
        assert len(ids) == 4 and len(set(ids)) == 1 and ids[0] in ("a", "b", "c")

    def test_pool_row_picks_uniformly_per_individual(self):
        entry = MonsterEncounterEntry(monster_ids=("a", "b", "c"))
        ids = select_encounter_individuals(entry, 6, fresh(0))
        assert len(ids) == 6 and all(pick in ("a", "b", "c") for pick in ids)

    def test_single_id_row_repeats(self):
        entry = MonsterEncounterEntry(monster_ids=("a",))
        assert select_encounter_individuals(entry, 3, fresh(0)) == ["a", "a", "a"]

    def test_resolver_is_deterministic(self):
        entry = MonsterEncounterEntry(monster_ids=("a", "b", "c"))
        assert select_encounter_individuals(entry, 5, fresh(0)) == select_encounter_individuals(entry, 5, fresh(0))
