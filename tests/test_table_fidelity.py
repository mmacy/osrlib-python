"""Table fidelity: compiled data asserted against the SRD values, transcribed verbatim.

Every row below is transcribed directly from the SRD class and ability pages; these
tests are the compiler's ground truth. Progression rows are
(xp, hd_count, hd_die, hd_bonus, con_applies, thac0, attack_bonus,
(death, wands, paralysis, breath, spells), spell_slots).
"""

import pytest

from osrlib.core.abilities import Literacy
from osrlib.data import load_ability_tables, load_classes

# fmt: off
CLERIC_ROWS = [
    (0,        1, 6, 0, True,  19, 0, (11, 12, 14, 16, 15), (0, 0, 0, 0, 0)),
    (1_500,    2, 6, 0, True,  19, 0, (11, 12, 14, 16, 15), (1, 0, 0, 0, 0)),
    (3_000,    3, 6, 0, True,  19, 0, (11, 12, 14, 16, 15), (2, 0, 0, 0, 0)),
    (6_000,    4, 6, 0, True,  19, 0, (11, 12, 14, 16, 15), (2, 1, 0, 0, 0)),
    (12_000,   5, 6, 0, True,  17, 2, (9, 10, 12, 14, 12),  (2, 2, 0, 0, 0)),
    (25_000,   6, 6, 0, True,  17, 2, (9, 10, 12, 14, 12),  (2, 2, 1, 1, 0)),
    (50_000,   7, 6, 0, True,  17, 2, (9, 10, 12, 14, 12),  (2, 2, 2, 1, 1)),
    (100_000,  8, 6, 0, True,  17, 2, (9, 10, 12, 14, 12),  (3, 3, 2, 2, 1)),
    (200_000,  9, 6, 0, True,  14, 5, (6, 7, 9, 11, 9),     (3, 3, 3, 2, 2)),
    (300_000,  9, 6, 1, False, 14, 5, (6, 7, 9, 11, 9),     (4, 4, 3, 3, 2)),
    (400_000,  9, 6, 2, False, 14, 5, (6, 7, 9, 11, 9),     (4, 4, 4, 3, 3)),
    (500_000,  9, 6, 3, False, 14, 5, (6, 7, 9, 11, 9),     (5, 5, 4, 4, 3)),
    (600_000,  9, 6, 4, False, 12, 7, (3, 5, 7, 8, 7),      (5, 5, 5, 4, 4)),
    (700_000,  9, 6, 5, False, 12, 7, (3, 5, 7, 8, 7),      (6, 5, 5, 5, 4)),
]

DWARF_ROWS = [
    (0,        1, 8, 0, True,  19, 0, (8, 9, 10, 13, 12), ()),
    (2_200,    2, 8, 0, True,  19, 0, (8, 9, 10, 13, 12), ()),
    (4_400,    3, 8, 0, True,  19, 0, (8, 9, 10, 13, 12), ()),
    (8_800,    4, 8, 0, True,  17, 2, (6, 7, 8, 10, 10),  ()),
    (17_000,   5, 8, 0, True,  17, 2, (6, 7, 8, 10, 10),  ()),
    (35_000,   6, 8, 0, True,  17, 2, (6, 7, 8, 10, 10),  ()),
    (70_000,   7, 8, 0, True,  14, 5, (4, 5, 6, 7, 8),    ()),
    (140_000,  8, 8, 0, True,  14, 5, (4, 5, 6, 7, 8),    ()),
    (270_000,  9, 8, 0, True,  14, 5, (4, 5, 6, 7, 8),    ()),
    (400_000,  9, 8, 3, False, 12, 7, (2, 3, 4, 4, 6),    ()),
    (530_000,  9, 8, 6, False, 12, 7, (2, 3, 4, 4, 6),    ()),
    (660_000,  9, 8, 9, False, 12, 7, (2, 3, 4, 4, 6),    ()),
]

ELF_ROWS = [
    (0,        1, 6, 0, True,  19, 0, (12, 13, 13, 15, 15), (1, 0, 0, 0, 0)),
    (4_000,    2, 6, 0, True,  19, 0, (12, 13, 13, 15, 15), (2, 0, 0, 0, 0)),
    (8_000,    3, 6, 0, True,  19, 0, (12, 13, 13, 15, 15), (2, 1, 0, 0, 0)),
    (16_000,   4, 6, 0, True,  17, 2, (10, 11, 11, 13, 12), (2, 2, 0, 0, 0)),
    (32_000,   5, 6, 0, True,  17, 2, (10, 11, 11, 13, 12), (2, 2, 1, 0, 0)),
    (64_000,   6, 6, 0, True,  17, 2, (10, 11, 11, 13, 12), (2, 2, 2, 0, 0)),
    (120_000,  7, 6, 0, True,  14, 5, (8, 9, 9, 10, 10),    (3, 2, 2, 1, 0)),
    (250_000,  8, 6, 0, True,  14, 5, (8, 9, 9, 10, 10),    (3, 3, 2, 2, 0)),
    (400_000,  9, 6, 0, True,  14, 5, (8, 9, 9, 10, 10),    (3, 3, 3, 2, 1)),
    (600_000,  9, 6, 2, False, 12, 7, (6, 7, 8, 8, 8),      (3, 3, 3, 3, 2)),
]

FIGHTER_ROWS = [
    (0,        1, 8, 0,  True,  19, 0, (12, 13, 14, 15, 16), ()),
    (2_000,    2, 8, 0,  True,  19, 0, (12, 13, 14, 15, 16), ()),
    (4_000,    3, 8, 0,  True,  19, 0, (12, 13, 14, 15, 16), ()),
    (8_000,    4, 8, 0,  True,  17, 2, (10, 11, 12, 13, 14), ()),
    (16_000,   5, 8, 0,  True,  17, 2, (10, 11, 12, 13, 14), ()),
    (32_000,   6, 8, 0,  True,  17, 2, (10, 11, 12, 13, 14), ()),
    (64_000,   7, 8, 0,  True,  14, 5, (8, 9, 10, 10, 12),   ()),
    (120_000,  8, 8, 0,  True,  14, 5, (8, 9, 10, 10, 12),   ()),
    (240_000,  9, 8, 0,  True,  14, 5, (8, 9, 10, 10, 12),   ()),
    (360_000,  9, 8, 2,  False, 12, 7, (6, 7, 8, 8, 10),     ()),
    (480_000,  9, 8, 4,  False, 12, 7, (6, 7, 8, 8, 10),     ()),
    (600_000,  9, 8, 6,  False, 12, 7, (6, 7, 8, 8, 10),     ()),
    (720_000,  9, 8, 8,  False, 10, 9, (4, 5, 6, 5, 8),      ()),
    (840_000,  9, 8, 10, False, 10, 9, (4, 5, 6, 5, 8),      ()),
]

HALFLING_ROWS = [
    (0,       1, 6, 0, True, 19, 0, (8, 9, 10, 13, 12), ()),
    (2_000,   2, 6, 0, True, 19, 0, (8, 9, 10, 13, 12), ()),
    (4_000,   3, 6, 0, True, 19, 0, (8, 9, 10, 13, 12), ()),
    (8_000,   4, 6, 0, True, 17, 2, (6, 7, 8, 10, 10),  ()),
    (16_000,  5, 6, 0, True, 17, 2, (6, 7, 8, 10, 10),  ()),
    (32_000,  6, 6, 0, True, 17, 2, (6, 7, 8, 10, 10),  ()),
    (64_000,  7, 6, 0, True, 14, 5, (4, 5, 6, 7, 8),    ()),
    (120_000, 8, 6, 0, True, 14, 5, (4, 5, 6, 7, 8),    ()),
]

MAGIC_USER_ROWS = [
    (0,          1, 4, 0, True,  19, 0, (13, 14, 13, 16, 15), (1, 0, 0, 0, 0, 0)),
    (2_500,      2, 4, 0, True,  19, 0, (13, 14, 13, 16, 15), (2, 0, 0, 0, 0, 0)),
    (5_000,      3, 4, 0, True,  19, 0, (13, 14, 13, 16, 15), (2, 1, 0, 0, 0, 0)),
    (10_000,     4, 4, 0, True,  19, 0, (13, 14, 13, 16, 15), (2, 2, 0, 0, 0, 0)),
    (20_000,     5, 4, 0, True,  19, 0, (13, 14, 13, 16, 15), (2, 2, 1, 0, 0, 0)),
    (40_000,     6, 4, 0, True,  17, 2, (11, 12, 11, 14, 12), (2, 2, 2, 0, 0, 0)),
    (80_000,     7, 4, 0, True,  17, 2, (11, 12, 11, 14, 12), (3, 2, 2, 1, 0, 0)),
    (150_000,    8, 4, 0, True,  17, 2, (11, 12, 11, 14, 12), (3, 3, 2, 2, 0, 0)),
    (300_000,    9, 4, 0, True,  17, 2, (11, 12, 11, 14, 12), (3, 3, 3, 2, 1, 0)),
    (450_000,    9, 4, 1, False, 17, 2, (11, 12, 11, 14, 12), (3, 3, 3, 3, 2, 0)),
    (600_000,    9, 4, 2, False, 14, 5, (8, 9, 8, 11, 8),     (4, 3, 3, 3, 2, 1)),
    (750_000,    9, 4, 3, False, 14, 5, (8, 9, 8, 11, 8),     (4, 4, 3, 3, 3, 2)),
    (900_000,    9, 4, 4, False, 14, 5, (8, 9, 8, 11, 8),     (4, 4, 4, 3, 3, 3)),
    (1_050_000,  9, 4, 5, False, 14, 5, (8, 9, 8, 11, 8),     (4, 4, 4, 4, 3, 3)),
]

THIEF_ROWS = [
    (0,        1, 4, 0,  True,  19, 0, (13, 14, 13, 16, 15), ()),
    (1_200,    2, 4, 0,  True,  19, 0, (13, 14, 13, 16, 15), ()),
    (2_400,    3, 4, 0,  True,  19, 0, (13, 14, 13, 16, 15), ()),
    (4_800,    4, 4, 0,  True,  19, 0, (13, 14, 13, 16, 15), ()),
    (9_600,    5, 4, 0,  True,  17, 2, (12, 13, 11, 14, 13), ()),
    (20_000,   6, 4, 0,  True,  17, 2, (12, 13, 11, 14, 13), ()),
    (40_000,   7, 4, 0,  True,  17, 2, (12, 13, 11, 14, 13), ()),
    (80_000,   8, 4, 0,  True,  17, 2, (12, 13, 11, 14, 13), ()),
    (160_000,  9, 4, 0,  True,  14, 5, (10, 11, 9, 12, 10),  ()),
    (280_000,  9, 4, 2,  False, 14, 5, (10, 11, 9, 12, 10),  ()),
    (400_000,  9, 4, 4,  False, 14, 5, (10, 11, 9, 12, 10),  ()),
    (520_000,  9, 4, 6,  False, 14, 5, (10, 11, 9, 12, 10),  ()),
    (640_000,  9, 4, 8,  False, 12, 7, (8, 9, 7, 10, 8),     ()),
    (760_000,  9, 4, 10, False, 12, 7, (8, 9, 7, 10, 8),     ()),
]

# (level, CS, TR, HN upper bound, HS, MS, OL, PP)
THIEF_SKILLS = [
    (1, 87, 10, 2, 10, 20, 15, 20),
    (2, 88, 15, 2, 15, 25, 20, 25),
    (3, 89, 20, 3, 20, 30, 25, 30),
    (4, 90, 25, 3, 25, 35, 30, 35),
    (5, 91, 30, 3, 30, 40, 35, 40),
    (6, 92, 40, 3, 36, 45, 45, 45),
    (7, 93, 50, 4, 45, 55, 55, 55),
    (8, 94, 60, 4, 55, 65, 65, 65),
    (9, 95, 70, 4, 65, 75, 75, 75),
    (10, 96, 80, 4, 75, 85, 85, 85),
    (11, 97, 90, 5, 85, 95, 95, 95),
    (12, 98, 95, 5, 90, 96, 96, 105),
    (13, 99, 97, 5, 95, 98, 97, 115),
    (14, 99, 99, 5, 99, 99, 99, 125),
]
# fmt: on

PROGRESSIONS = {
    "cleric": CLERIC_ROWS,
    "dwarf": DWARF_ROWS,
    "elf": ELF_ROWS,
    "fighter": FIGHTER_ROWS,
    "halfling": HALFLING_ROWS,
    "magic_user": MAGIC_USER_ROWS,
    "thief": THIEF_ROWS,
}


@pytest.mark.parametrize("class_id", sorted(PROGRESSIONS))
def test_full_progression_table(class_id: str):
    definition = load_classes().get(class_id)
    expected = PROGRESSIONS[class_id]
    assert definition.max_level == len(expected)
    for level, (xp, count, die, bonus, con_applies, thac0, attack_bonus, saves, slots) in enumerate(expected, 1):
        row = definition.row(level)
        assert row.xp == xp, (class_id, level)
        assert (row.hit_dice.count, row.hit_dice.die, row.hit_dice.bonus) == (count, die, bonus), (class_id, level)
        assert row.hit_dice.con_applies is con_applies, (class_id, level)
        assert (row.thac0, row.attack_bonus) == (thac0, attack_bonus), (class_id, level)
        assert (row.saves.death, row.saves.wands, row.saves.paralysis, row.saves.breath, row.saves.spells) == saves
        assert row.spell_slots == slots, (class_id, level)


def test_thief_skill_table():
    thief = load_classes().get("thief")
    assert len(thief.thief_skills) == 14
    for expected, row in zip(THIEF_SKILLS, thief.thief_skills, strict=True):
        actual = (
            row.level,
            row.climb_sheer_surfaces,
            row.find_remove_treasure_traps,
            row.hear_noise,
            row.hide_in_shadows,
            row.move_silently,
            row.open_locks,
            row.pick_pockets,
        )
        assert actual == expected


def test_only_the_thief_has_skill_rows():
    for definition in load_classes().classes:
        assert bool(definition.thief_skills) == (definition.id == "thief")


class TestClassStatBlocks:
    def test_requirements(self):
        classes = load_classes()
        assert classes.get("cleric").requirements == {}
        assert classes.get("dwarf").requirements == {"con": 9}
        assert classes.get("elf").requirements == {"int": 9}
        assert classes.get("halfling").requirements == {"con": 9, "dex": 9}

    def test_prime_requisites(self):
        classes = load_classes()
        assert classes.get("cleric").prime_requisites == ("wis",)
        assert classes.get("dwarf").prime_requisites == ("str",)
        assert classes.get("elf").prime_requisites == ("int", "str")
        assert classes.get("fighter").prime_requisites == ("str",)
        assert classes.get("halfling").prime_requisites == ("dex", "str")
        assert classes.get("magic_user").prime_requisites == ("int",)
        assert classes.get("thief").prime_requisites == ("dex",)

    def test_hit_dice_and_max_levels(self):
        classes = load_classes()
        expected = {
            "cleric": (6, 14),
            "dwarf": (8, 12),
            "elf": (6, 10),
            "fighter": (8, 14),
            "halfling": (6, 8),
            "magic_user": (4, 14),
            "thief": (4, 14),
        }
        for class_id, (die, max_level) in expected.items():
            definition = classes.get(class_id)
            assert (definition.hit_die, definition.max_level) == (die, max_level), class_id

    def test_armour_policies(self):
        classes = load_classes()
        assert (classes.get("fighter").armour.kind, classes.get("fighter").armour.shields_allowed) == ("any", True)
        assert (classes.get("thief").armour.kind, classes.get("thief").armour.shields_allowed) == (
            "leather_only",
            False,
        )
        assert (classes.get("magic_user").armour.kind, classes.get("magic_user").armour.shields_allowed) == (
            "none",
            False,
        )

    def test_weapon_policies(self):
        classes = load_classes()
        assert classes.get("fighter").weapons.kind == "any"
        assert classes.get("cleric").weapons.weapon_ids == ("club", "mace", "sling", "staff", "war_hammer")
        assert classes.get("magic_user").weapons.weapon_ids == ("dagger",)
        assert classes.get("dwarf").weapons.kind == "forbidden"
        assert classes.get("dwarf").weapons.weapon_ids == ("long_bow", "two_handed_sword")
        assert classes.get("halfling").weapons.weapon_ids == ("long_bow", "two_handed_sword")

    def test_native_languages(self):
        classes = load_classes()
        assert classes.get("cleric").languages == ("common",)
        assert classes.get("dwarf").languages == ("common", "dwarvish", "gnomish", "goblin", "kobold")
        assert classes.get("elf").languages == ("common", "elvish", "gnoll", "hobgoblin", "orcish")
        assert classes.get("halfling").languages == ("common", "halfling")

    def test_thief_may_not_lower_str(self):
        classes = load_classes()
        assert classes.get("thief").may_not_lower == ("str",)
        assert classes.get("fighter").may_not_lower == ()

    def test_level_titles(self):
        classes = load_classes()
        assert classes.get("fighter").level_titles[0] == "Veteran"
        assert classes.get("fighter").level_titles[-1] == "Lord (Lady)"
        assert classes.get("halfling").level_titles[-1] == "Sheriff"
        assert classes.get("elf").level_titles[0] == "Medium/Veteran"
        assert len(classes.get("cleric").level_titles) == 9

    def test_ability_tags(self):
        classes = load_classes()
        dwarf_tags = {ability.tag: ability for ability in classes.get("dwarf").abilities}
        assert dwarf_tags["infravision"].params == {"range_feet": 60}
        assert dwarf_tags["detect_construction_tricks"].params == {"chance_in_six": 2}
        elf_tags = {ability.tag for ability in classes.get("elf").abilities}
        assert "ghoul_paralysis_immunity" in elf_tags
        halfling_tags = {ability.tag: ability for ability in classes.get("halfling").abilities}
        assert halfling_tags["defensive_bonus"].params["ac_bonus"] == 2
        assert halfling_tags["missile_attack_bonus"].params == {"bonus": 1}
        thief_tags = {ability.tag: ability for ability in classes.get("thief").abilities}
        assert thief_tags["back_stab"].params == {"attack_bonus": 4, "damage_multiplier": 2}
        assert thief_tags["read_languages"].params == {"pct": 80, "min_level": 4}
        assert thief_tags["scroll_use"].params == {"error_pct": 10, "min_level": 10}
        for definition in classes.classes:
            for ability in definition.abilities:
                assert ability.prose


# Ability tables, transcribed from Ability_Scores.md: (min, max, *values).
# fmt: off
STRENGTH = [(3, 3, -3, 1), (4, 5, -2, 1), (6, 8, -1, 1), (9, 12, 0, 2), (13, 15, 1, 3), (16, 17, 2, 4), (18, 18, 3, 5)]
WISDOM = [(3, 3, -3), (4, 5, -2), (6, 8, -1), (9, 12, 0), (13, 15, 1), (16, 17, 2), (18, 18, 3)]
DEXTERITY = [
    (3, 3, -3, -3, -2), (4, 5, -2, -2, -1), (6, 8, -1, -1, -1), (9, 12, 0, 0, 0),
    (13, 15, 1, 1, 1), (16, 17, 2, 2, 1), (18, 18, 3, 3, 2),
]
CONSTITUTION = [(3, 3, -3), (4, 5, -2), (6, 8, -1), (9, 12, 0), (13, 15, 1), (16, 17, 2), (18, 18, 3)]
CHARISMA = [
    (3, 3, -2, 1, 4), (4, 5, -1, 2, 5), (6, 8, -1, 3, 6), (9, 12, 0, 4, 7),
    (13, 15, 1, 5, 8), (16, 17, 1, 6, 9), (18, 18, 2, 7, 10),
]
PRIME_REQUISITE = [(3, 5, -20), (6, 8, -10), (9, 12, 0), (13, 15, 5), (16, 18, 10)]
# fmt: on


class TestAbilityTables:
    def test_strength(self):
        tables = load_ability_tables()
        for low, high, melee, open_doors in STRENGTH:
            for score in range(low, high + 1):
                assert tables.melee_modifier(score) == melee
                assert tables.open_doors_chance(score) == open_doors

    def test_intelligence(self):
        tables = load_ability_tables()
        expected = [
            (3, 3, 0, Literacy.ILLITERATE, True),
            (4, 5, 0, Literacy.ILLITERATE, False),
            (6, 8, 0, Literacy.BASIC, False),
            (9, 12, 0, Literacy.LITERATE, False),
            (13, 15, 1, Literacy.LITERATE, False),
            (16, 17, 2, Literacy.LITERATE, False),
            (18, 18, 3, Literacy.LITERATE, False),
        ]
        for low, high, additional, literacy, _broken in expected:
            for score in range(low, high + 1):
                assert tables.additional_languages(score) == additional
                assert tables.literacy(score) == literacy
        rows = tables.intelligence
        assert [row.broken_speech for row in rows] == [broken for _, _, _, _, broken in expected]

    def test_wisdom(self):
        tables = load_ability_tables()
        for low, high, modifier in WISDOM:
            for score in range(low, high + 1):
                assert tables.magic_save_modifier(score) == modifier

    def test_dexterity(self):
        tables = load_ability_tables()
        for low, high, ac, missile, initiative in DEXTERITY:
            for score in range(low, high + 1):
                assert tables.ac_modifier(score) == ac
                assert tables.missile_modifier(score) == missile
                assert tables.initiative_modifier(score) == initiative

    def test_constitution(self):
        tables = load_ability_tables()
        for low, high, modifier in CONSTITUTION:
            for score in range(low, high + 1):
                assert tables.hit_point_modifier(score) == modifier

    def test_charisma_two_row_header_table(self):
        tables = load_ability_tables()
        for low, high, reactions, retainers, loyalty in CHARISMA:
            for score in range(low, high + 1):
                assert tables.npc_reaction_modifier(score) == reactions
                assert tables.max_retainers(score) == retainers
                assert tables.retainer_loyalty(score) == loyalty

    def test_prime_requisite(self):
        tables = load_ability_tables()
        for low, high, pct in PRIME_REQUISITE:
            for score in range(low, high + 1):
                assert tables.prime_requisite_xp_modifier_pct(score) == pct

    def test_standard_xp_tiers_derive_from_prime_requisite_table(self):
        fighter = load_classes().get("fighter")
        assert [(tier.modifier_pct, dict(tier.minimums)) for tier in fighter.xp_tiers] == [
            (10, {"str": 16}),
            (5, {"str": 13}),
            (0, {"str": 9}),
            (-10, {"str": 6}),
            (-20, {"str": 3}),
        ]


# The turning table, transcribed verbatim from Cleric.md: 11 cleric-level rows
# (1-10, 11+) x 8 monster-HD columns (1, 2, 2*, 3, 4, 5, 6, 7-9).
# fmt: off
TURNING_TABLE = [
    ("1",   "7", "9", "11", "—", "—", "—", "—", "—"),
    ("2",   "T", "7", "9", "11", "—", "—", "—", "—"),
    ("3",   "T", "T", "7", "9", "11", "—", "—", "—"),
    ("4",   "D", "T", "T", "7", "9", "11", "—", "—"),
    ("5",   "D", "D", "T", "T", "7", "9", "11", "—"),
    ("6",   "D", "D", "D", "T", "T", "7", "9", "11"),
    ("7",   "D", "D", "D", "D", "T", "T", "7", "9"),
    ("8",   "D", "D", "D", "D", "D", "T", "T", "7"),
    ("9",   "D", "D", "D", "D", "D", "D", "T", "T"),
    ("10",  "D", "D", "D", "D", "D", "D", "D", "T"),
    ("11+", "D", "D", "D", "D", "D", "D", "D", "D"),
]
# fmt: on


class TestTurningTableFidelity:
    def test_verbatim_against_cleric_page(self):
        from osrlib.core.tables import TURNING_COLUMNS
        from osrlib.data import load_combat_tables

        turning = load_combat_tables().turning
        assert len(turning.rows) == 11
        for row, (label, *cells) in zip(turning.rows, TURNING_TABLE, strict=True):
            assert row.label == label
            assert tuple(row.cells[column] for column in TURNING_COLUMNS) == tuple(cells)

    def test_legend_semantics(self):
        from osrlib.data import load_combat_tables

        turning = load_combat_tables().turning
        assert turning.result(1, "1").outcome == "number" and turning.result(1, "1").threshold == 7
        assert turning.result(1, "3").outcome == "fail"
        assert turning.result(2, "1").outcome == "turn"
        assert turning.result(4, "1").outcome == "destroy"
        # Levels above 10 clamp to the 11+ row.
        assert turning.result(11, "7-9").outcome == "destroy"
        assert turning.result(14, "7-9").outcome == "destroy"


class TestCasterTagParams:
    def test_divine_and_arcane_tags_name_their_spell_lists(self):
        classes = load_classes()
        cleric = next(a for a in classes.get("cleric").abilities if a.tag == "divine_magic")
        assert cleric.params == {"spell_list": "cleric"}
        for class_id in ("magic_user", "elf"):
            arcane = next(a for a in classes.get(class_id).abilities if a.tag == "arcane_magic")
            assert arcane.params == {"spell_list": "magic_user"}

    def test_cleric_level_1_has_no_casting(self):
        # "Once a cleric has proven their faith (from 2nd level)": level 1 carries
        # all-zero slots and level 2 exactly one first-level slot.
        cleric = load_classes().get("cleric")
        assert cleric.row(1).spell_slots == (0, 0, 0, 0, 0)
        assert cleric.row(2).spell_slots == (1, 0, 0, 0, 0)
