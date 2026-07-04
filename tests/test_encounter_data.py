"""Dungeon encounter tables and reaction table: census, fidelity, and lookup tests.

The fidelity tables below are transcribed verbatim from `Dungeon_Encounters.md` and
`Encounters.md` — these tests are the compiler's ground truth. Each row is
(printed cell name, printed count, expected entry), where the count is the
parenthesized dice string or the plain integer 1, and the expected entry is built by
the `monster`/`pool`/`hydra`/`npc` helpers. The two override-normalized names
("Basic Adventures" → "Basic Adventurers", singular "Expert Adventurer" →
"Expert Adventurers") appear here in their normalized forms; their provenance is
asserted separately.
"""

import pytest

from osrlib.core.tables import (
    EncounterTableRow,
    MonsterEncounterEntry,
    NpcPartyEncounterEntry,
    ReactionResult,
    reaction_result,
)
from osrlib.data import load_combat_tables, load_encounter_tables, load_monsters


def monster(monster_id: str) -> MonsterEncounterEntry:
    return MonsterEncounterEntry(monster_ids=(monster_id,))


def pool(*monster_ids: str) -> MonsterEncounterEntry:
    return MonsterEncounterEntry(monster_ids=monster_ids)


def hydra(dice: str, *monster_ids: str) -> MonsterEncounterEntry:
    return MonsterEncounterEntry(monster_ids=monster_ids, variant_dice=dice)


def npc(kind: str) -> NpcPartyEncounterEntry:
    return NpcPartyEncounterEntry(party_kind=kind)


# fmt: off
LEVEL_1 = [
    ("Acolyte", "1d8", monster("acolyte")),
    ("Bandit", "1d8", monster("bandit")),
    ("Beetle, Fire", "1d8", monster("fire_beetle")),
    ("Dwarf", "1d6", monster("dwarf_monster")),
    ("Gnome", "1d6", monster("gnome")),
    ("Goblin", "2d4", monster("goblin")),
    ("Green Slime", "1d4", monster("green_slime")),
    ("Halfling", "3d6", monster("halfling_monster")),
    ("Killer Bee", "1d10", monster("killer_bee")),
    ("Kobold", "4d4", monster("kobold")),
    ("Lizard, Gecko", "1d3", monster("gecko")),
    ("Orc", "2d4", monster("orc")),
    ("Shrew, Giant", "1d10", monster("shrew_giant")),
    ("Skeleton", "3d4", monster("skeleton")),
    ("Snake, Cobra", "1d6", monster("spitting_cobra")),
    ("Spider, Crab", "1d4", monster("crab_spider")),
    ("Sprite", "3d6", monster("sprite")),
    ("Stirge", "1d10", monster("stirge")),
    ("Trader", "1d8", monster("trader")),
    ("Wolf", "2d6", monster("normal_wolf")),
]

LEVEL_2 = [
    ("Beetle, Oil", "1d8", monster("oil_beetle")),
    ("Berserker", "1d6", monster("berserker")),
    ("Cat, Mountain Lion", "1d4", monster("mountain_lion")),
    ("Elf", "1d4", monster("elf_monster")),
    ("Ghoul", "1d6", monster("ghoul")),
    ("Gnoll", "1d6", monster("gnoll")),
    ("Grey Ooze", 1, monster("grey_ooze")),
    ("Hobgoblin", "1d6", monster("hobgoblin")),
    ("Lizard, Draco", "1d4", monster("draco")),
    ("Lizard Man", "2d4", monster("lizard_man")),
    ("Neanderthal", "1d10", monster("neanderthal_caveman")),
    ("Noble", "2d6", monster("noble")),
    ("Pixie", "2d4", monster("pixie")),
    ("Robber Fly", "1d6", monster("robber_fly")),
    ("Rock Baboon", "2d6", monster("rock_baboon")),
    ("Snake, Pit Viper", "1d8", monster("pit_viper")),
    ("Spider, Black Widow", "1d3", monster("black_widow")),
    ("Troglodyte", "1d8", monster("troglodyte")),
    ("Veteran", "2d4", pool("veteran_1", "veteran_2", "veteran_3")),
    ("Zombie", "2d4", monster("zombie")),
]

LEVEL_3 = [
    ("Ape, White", "1d6", monster("ape_white")),
    ("Basic Adventurers", "1d4+4", npc("basic")),
    ("Beetle, Tiger", "1d6", monster("tiger_beetle")),
    ("Bugbear", "2d4", monster("bugbear")),
    ("Carcass Crawler", "1d3", monster("carcass_crawler")),
    ("Doppelgänger", "1d6", monster("doppelganger")),
    ("Driver Ant", "2d4", monster("driver_ant")),
    ("Gargoyle", "1d6", monster("gargoyle")),
    ("Gelatinous Cube", 1, monster("gelatinous_cube")),
    ("Harpy", "1d6", monster("harpy")),
    ("Living Statue, Crystal", "1d6", monster("crystal_living_statue")),
    ("Lycanthrope, Wererat", "1d8", monster("wererat")),
    ("Medium", "1d4", monster("medium")),
    ("Medusa", "1d3", monster("medusa")),
    ("Ochre Jelly", 1, monster("ochre_jelly")),
    ("Ogre", "1d6", monster("ogre")),
    ("Shadow", "1d8", monster("shadow")),
    ("Spider, Tarantella", "1d3", monster("tarantella")),
    ("Thoul", "1d6", monster("thoul")),
    ("Wight", "1d6", monster("wight")),
]

LEVEL_4_5 = [
    ("Bear, Cave", "1d2", monster("cave_bear")),
    ("Blink Dog", "1d6", monster("blink_dog")),
    ("Caecilia", "1d3", monster("caecilia")),
    ("Cockatrice", "1d4", monster("cockatrice")),
    ("Doppelgänger", "1d6", monster("doppelganger")),
    ("Expert Adventurers", "1d6+3", npc("expert")),
    ("Grey Ooze", 1, monster("grey_ooze")),
    ("Hellhound", "2d4", pool("hellhound_3", "hellhound_4", "hellhound_5", "hellhound_6", "hellhound_7")),
    ("Lizard, Tuatara", "1d2", monster("tuatara")),
    ("Lycanthrope, Wereboar", "1d4", monster("wereboar")),
    ("Lycanthrope, Werewolf", "1d6", monster("werewolf")),
    ("Minotaur", "1d6", monster("minotaur")),
    ("Ochre Jelly", 1, monster("ochre_jelly")),
    ("Owl Bear", "1d4", monster("owl_bear")),
    ("Rhagodessa", "1d4", monster("rhagodessa")),
    ("Rust Monster", "1d4", monster("rust_monster")),
    ("Spectre", "1d4", monster("spectre")),
    ("Troll", "1d8", monster("troll")),
    ("Weasel, Giant", "1d4", monster("weasel_giant")),
    ("Wraith", "1d4", monster("wraith")),
]

LEVEL_6_7 = [
    ("Basilisk", "1d6", monster("basilisk")),
    ("Bear, Cave", "1d2", monster("cave_bear")),
    ("Black Pudding", 1, monster("black_pudding")),
    ("Caecilia", "1d3", monster("caecilia")),
    ("Dragon, White", "1d4", monster("white_dragon")),
    ("Expert Adventurers", "1d6+3", npc("expert")),
    ("Gorgon", "1d2", monster("gorgon")),
    ("Hellhound", "2d4", pool("hellhound_3", "hellhound_4", "hellhound_5", "hellhound_6", "hellhound_7")),
    ("Hydra 1d4+4HD", 1, hydra("1d4+4", "hydra_5", "hydra_6", "hydra_7", "hydra_8")),
    ("Lycanthrope, Weretiger", "1d4", monster("weretiger")),
    ("Minotaur", "1d6", monster("minotaur")),
    ("Mummy", "1d4", monster("mummy")),
    ("Ochre Jelly", 1, monster("ochre_jelly")),
    ("Owl Bear", "1d4", monster("owl_bear")),
    ("Rust Monster", "1d4", monster("rust_monster")),
    ("Salamander, Flame", "1d4+1", monster("flame_salamander")),
    ("Scorpion, Giant", "1d6", monster("scorpion_giant")),
    ("Spectre", "1d4", monster("spectre")),
    ("Troll", "1d8", monster("troll")),
    ("Warp Beast", "1d4", monster("warp_beast")),
]

LEVEL_8_PLUS = [
    ("Black Pudding", 1, monster("black_pudding")),
    ("Chimera", "1d2", monster("chimera")),
    ("Dragon, Black", "1d4", monster("black_dragon")),
    ("Dragon, Blue", "1d4", monster("blue_dragon")),
    ("Dragon, Gold", "1d4", monster("gold_dragon")),
    ("Dragon, Green", "1d4", monster("green_dragon")),
    ("Dragon, Red", "1d4", monster("red_dragon")),
    ("Expert Adventurers", "1d6+3", npc("expert")),
    ("Giant, Hill", "1d4", monster("hill_giant")),
    ("Giant, Stone", "1d2", monster("stone_giant")),
    ("Golem, Amber", 1, monster("amber_golem")),
    ("Golem, Bone", 1, monster("bone_golem")),
    ("Hydra 1d4+8HD", 1, hydra("1d4+8", "hydra_9", "hydra_10", "hydra_11", "hydra_12")),
    ("Lycanthrope, Devil Swine", "1d3", monster("devil_swine")),
    ("Lycanthrope, Werebear", "1d4", monster("werebear")),
    ("Manticore", "1d2", monster("manticore")),
    ("Purple Worm", "1d2", monster("purple_worm")),
    ("Salamander, Flame", "1d4+1", monster("flame_salamander")),
    ("Salamander, Frost", "1d3", monster("frost_salamander")),
    ("Vampire", "1d4", pool("vampire_7", "vampire_8", "vampire_9")),
]

FIDELITY = [
    ("level_1", "Level 1", 1, 1, LEVEL_1),
    ("level_2", "Level 2", 2, 2, LEVEL_2),
    ("level_3", "Level 3", 3, 3, LEVEL_3),
    ("level_4_5", "Level 4–5", 4, 5, LEVEL_4_5),
    ("level_6_7", "Level 6-7", 6, 7, LEVEL_6_7),
    ("level_8_plus", "Level 8+", 8, None, LEVEL_8_PLUS),
]

REACTION_BANDS = [
    ("2 or less", "Attacks", None, 2, ReactionResult.ATTACKS),
    ("3–5", "Hostile, may attack", 3, 5, ReactionResult.HOSTILE),
    ("6–8", "Uncertain, confused", 6, 8, ReactionResult.UNCERTAIN),
    ("9–11", "Indifferent, may negotiate", 9, 11, ReactionResult.INDIFFERENT),
    ("12 or more", "Eager, friendly", 12, None, ReactionResult.FRIENDLY),
]
# fmt: on


class TestEncounterCensus:
    def test_six_tables_of_twenty_rows(self):
        tables = load_encounter_tables().tables
        assert [table.id for table in tables] == [table_id for table_id, *_ in FIDELITY]
        for table in tables:
            assert len(table.rows) == 20

    def test_every_monster_reference_resolves(self):
        catalog = load_monsters()
        for table in load_encounter_tables().tables:
            for row in table.rows:
                if row.entry.kind == "monster":
                    for monster_id in row.entry.monster_ids:
                        assert catalog.get(monster_id).id == monster_id

    def test_override_provenance(self):
        tables = {table.id: table for table in load_encounter_tables().tables}
        assert tables["level_3"].overrides_applied == ("rows.1.name",)
        assert tables["level_4_5"].overrides_applied == ("rows.5.name",)
        assert tables["level_6_7"].overrides_applied == ("rows.5.name",)
        assert tables["level_8_plus"].overrides_applied == ("rows.7.name",)
        assert tables["level_1"].overrides_applied == ()
        assert tables["level_2"].overrides_applied == ()


class TestEncounterFidelity:
    @pytest.mark.parametrize(("table_id", "label", "min_level", "max_level", "rows"), FIDELITY)
    def test_table_matches_srd(self, table_id, label, min_level, max_level, rows):
        tables = {table.id: table for table in load_encounter_tables().tables}
        table = tables[table_id]
        assert table.label == label
        assert table.min_level == min_level
        assert table.max_level == max_level
        for roll, (name, count, entry) in enumerate(rows, start=1):
            expected = EncounterTableRow(
                roll=roll,
                name=name,
                entry=entry,
                count_dice=count if isinstance(count, str) else None,
                count_fixed=count if isinstance(count, int) else None,
            )
            assert table.rows[roll - 1] == expected


class TestLevelBands:
    def test_levels_clamp_into_printed_bands(self):
        tables = load_encounter_tables()
        assert tables.for_level(1).id == "level_1"
        assert tables.for_level(2).id == "level_2"
        assert tables.for_level(3).id == "level_3"
        assert tables.for_level(4).id == "level_4_5"
        assert tables.for_level(5).id == "level_4_5"
        assert tables.for_level(6).id == "level_6_7"
        assert tables.for_level(7).id == "level_6_7"
        assert tables.for_level(8).id == "level_8_plus"
        assert tables.for_level(20).id == "level_8_plus"

    def test_level_zero_rejects(self):
        with pytest.raises(ValueError):
            load_encounter_tables().for_level(0)


class TestReactionTable:
    def test_bands_match_srd(self):
        bands = load_combat_tables().reaction.bands
        assert len(bands) == len(REACTION_BANDS)
        for band, (label, text, min_total, max_total, result) in zip(bands, REACTION_BANDS, strict=True):
            assert band.label == label
            assert band.text == text
            assert band.min_total == min_total
            assert band.max_total == max_total
            assert band.result is result

    def test_totals_resolve_per_band(self):
        table = load_combat_tables().reaction
        assert reaction_result(table, 2) is ReactionResult.ATTACKS
        assert reaction_result(table, 3) is ReactionResult.HOSTILE
        assert reaction_result(table, 5) is ReactionResult.HOSTILE
        assert reaction_result(table, 6) is ReactionResult.UNCERTAIN
        assert reaction_result(table, 8) is ReactionResult.UNCERTAIN
        assert reaction_result(table, 9) is ReactionResult.INDIFFERENT
        assert reaction_result(table, 11) is ReactionResult.INDIFFERENT
        assert reaction_result(table, 12) is ReactionResult.FRIENDLY

    def test_totals_clamp_into_outer_bands(self):
        # A CHA-modified total below 2 or above 12 lands in the table's own
        # "2 or less" / "12 or more" bands.
        table = load_combat_tables().reaction
        assert reaction_result(table, 0) is ReactionResult.ATTACKS
        assert reaction_result(table, 1) is ReactionResult.ATTACKS
        assert reaction_result(table, 13) is ReactionResult.FRIENDLY
        assert reaction_result(table, 15) is ReactionResult.FRIENDLY
