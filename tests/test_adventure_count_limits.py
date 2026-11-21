"""Upper limits on the monster counts an adventure document sets (issue #274).

Issue #272 limited the referee commands that spawn monsters to 100 at a time. An adventure
document sets monster counts of its own, in a keyed encounter's lines and in the rows of a
level's own wandering-monster table, and an unlimited count there spawns as many monsters as
it says when play reaches it. The models refuse a count that can create more than 100
monsters when they are constructed, so a document that holds one fails to load:

- A [`KeyedMonster`][osrlib.crawl.dungeon.KeyedMonster] line and an
  [`EncounterTableRow`][osrlib.core.tables.EncounterTableRow] take a `count_fixed` from 1 to
  100, or a `count_dice` whose highest total is at most 100.
- A [`KeyedEncounter`][osrlib.crawl.dungeon.KeyedEncounter] creates at most 100 monsters
  across all its lines, counting each line's `count_fixed` or highest `count_dice` total.

100 is the largest number appearing in the B/X monster data, and the spawn commands share
the limit, so an adventure can't spawn more monsters at once than a referee command can.
"""

import pytest
from pydantic import ValidationError

from osrlib.core.tables import EncounterTableRow, MonsterEncounterEntry, NpcPartyEncounterEntry
from osrlib.crawl.commands import SpawnMonsters
from osrlib.crawl.dungeon import KeyedEncounter, KeyedMonster
from osrlib.data import load_encounter_tables

LIMIT = 100
GOBLINS = MonsterEncounterEntry(monster_ids=("goblin",))


def row(**count) -> EncounterTableRow:
    return EncounterTableRow(roll=1, name="Goblin", entry=GOBLINS, **count)


# KeyedMonster


@pytest.mark.parametrize("count", [1, LIMIT])
def test_keyed_monster_accepts_a_fixed_count_in_range(count: int) -> None:
    assert KeyedMonster(template_id="goblin", count_fixed=count).count_fixed == count


@pytest.mark.parametrize("count", [LIMIT + 1, 100_000, 0, -3])
def test_keyed_monster_refuses_a_fixed_count_out_of_range(count: int) -> None:
    with pytest.raises(ValidationError):
        KeyedMonster(template_id="goblin", count_fixed=count)


@pytest.mark.parametrize("dice", ["1d100", "1d10x10", "6d10", "2d6+88", "d%"])
def test_keyed_monster_accepts_dice_that_reach_the_limit(dice: str) -> None:
    assert KeyedMonster(template_id="goblin", count_dice=dice).count_dice == dice


@pytest.mark.parametrize("dice", ["999d100", "1d6x999999", "2d6+89", "1d100+1", "11d10", "d%+1"])
def test_keyed_monster_refuses_dice_that_can_roll_past_the_limit(dice: str) -> None:
    with pytest.raises(ValidationError):
        KeyedMonster(template_id="goblin", count_dice=dice)


def test_keyed_monster_schema_states_the_limits() -> None:
    count_fixed = KeyedMonster.model_json_schema()["properties"]["count_fixed"]
    branches = count_fixed.get("anyOf", [count_fixed])
    assert any(branch.get("minimum") == 1 and branch.get("maximum") == LIMIT for branch in branches)


# KeyedEncounter


def test_keyed_encounter_accepts_lines_that_total_the_limit() -> None:
    encounter = KeyedEncounter(
        monsters=(
            KeyedMonster(template_id="goblin", count_fixed=60),
            KeyedMonster(template_id="hobgoblin", count_dice="2d20"),
        )
    )
    assert len(encounter.monsters) == 2


def test_keyed_encounter_refuses_fixed_lines_that_total_past_the_limit() -> None:
    with pytest.raises(ValidationError):
        KeyedEncounter(
            monsters=(
                KeyedMonster(template_id="goblin", count_fixed=60),
                KeyedMonster(template_id="hobgoblin", count_fixed=41),
            )
        )


def test_keyed_encounter_counts_each_dice_line_at_its_highest_total() -> None:
    with pytest.raises(ValidationError):
        KeyedEncounter(
            monsters=(
                KeyedMonster(template_id="goblin", count_fixed=60),
                KeyedMonster(template_id="hobgoblin", count_dice="1d6x7"),
            )
        )


def test_keyed_encounter_refuses_many_lines_under_the_line_limit() -> None:
    with pytest.raises(ValidationError):
        KeyedEncounter(monsters=tuple(KeyedMonster(template_id="goblin", count_fixed=LIMIT) for _ in range(1_000)))


def test_an_adventure_document_with_an_over_limit_line_fails_to_load() -> None:
    document = {"monsters": [{"template_id": "goblin", "count_fixed": 100_000}]}
    with pytest.raises(ValidationError):
        KeyedEncounter.model_validate(document)


# EncounterTableRow


@pytest.mark.parametrize("count", [{"count_fixed": 1}, {"count_fixed": LIMIT}, {"count_dice": "1d100"}])
def test_table_row_accepts_counts_in_range(count: dict) -> None:
    assert row(**count).roll == 1


@pytest.mark.parametrize(
    "count",
    [{"count_fixed": LIMIT + 1}, {"count_fixed": 0}, {"count_dice": "999d100"}, {"count_dice": "1d100+1"}],
)
def test_table_row_refuses_counts_out_of_range(count: dict) -> None:
    with pytest.raises(ValidationError):
        row(**count)


def test_npc_party_row_refuses_dice_that_can_roll_past_the_limit() -> None:
    with pytest.raises(ValidationError):
        EncounterTableRow(
            roll=1, name="Adventurers", entry=NpcPartyEncounterEntry(party_kind="basic"), count_dice="2d100"
        )


def test_the_compiled_tables_fit_the_limit() -> None:
    for table in load_encounter_tables().tables:
        for table_row in table.rows:
            assert table_row.count_fixed is not None or table_row.count_dice is not None


# The commands share the limit


@pytest.mark.parametrize("dice", ["1d100", "d%"])
def test_spawn_monsters_accepts_what_a_keyed_line_accepts(dice: str) -> None:
    assert SpawnMonsters(template_id="goblin", count_dice=dice, distance_feet=30).count_dice == dice


@pytest.mark.parametrize("dice", ["1d100+1", "d%+1"])
def test_spawn_monsters_refuses_what_a_keyed_line_refuses(dice: str) -> None:
    with pytest.raises(ValidationError):
        SpawnMonsters(template_id="goblin", count_dice=dice, distance_feet=30)
