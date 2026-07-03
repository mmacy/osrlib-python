"""Tests for the monster and combat-table data: counts, expansions, fidelity, spot checks."""

import pytest

from osrlib.core.monsters import DamageKey, Element, MonsterHitDice
from osrlib.core.tables import monster_xp, thac0_for_hd, to_hit_ac, xp_band_label
from osrlib.data import load_combat_tables, load_monsters

# Every packed-variant expansion, enumerated: 207 printed stat blocks plus these 26
# expansion extras give the pinned 233 templates.
EXPANSIONS = {
    "air_elemental_lesser",
    "air_elemental_intermediate",
    "air_elemental_greater",
    "earth_elemental_lesser",
    "earth_elemental_intermediate",
    "earth_elemental_greater",
    "fire_elemental_lesser",
    "fire_elemental_intermediate",
    "fire_elemental_greater",
    "water_elemental_lesser",
    "water_elemental_intermediate",
    "water_elemental_greater",
    "vampire_7",
    "vampire_8",
    "vampire_9",
    "hellhound_3",
    "hellhound_4",
    "hellhound_5",
    "hellhound_6",
    "hellhound_7",
    "hydra_5",
    "hydra_6",
    "hydra_7",
    "hydra_8",
    "hydra_9",
    "hydra_10",
    "hydra_11",
    "hydra_12",
    "insect_swarm_2",
    "insect_swarm_3",
    "insect_swarm_4",
    "small_herd_animal_1",
    "small_herd_animal_2",
    "veteran_1",
    "veteran_2",
    "veteran_3",
}

PRINTED_STAT_BLOCKS = 207
PACKED_BLOCKS = 10  # 4 elemental variants, vampire, hellhound, hydra, insect swarm, small herd animal, veteran

UNDEAD_PAGES = {"Ghoul.md", "Mummy.md", "Skeleton.md", "Spectre.md", "Vampire.md", "Wight.md", "Wraith.md", "Zombie.md"}

MONSTER_SAVE_BANDS = {
    "NH": (14, 15, 16, 17, 18),
    "1–3": (12, 13, 14, 15, 16),
    "4–6": (10, 11, 12, 13, 14),
    "7–9": (8, 9, 10, 10, 12),
    "10–12": (6, 7, 8, 8, 10),
    "13–15": (4, 5, 6, 5, 8),
    "16–18": (2, 3, 4, 3, 6),
    "19–21": (2, 2, 2, 2, 4),
    "22 or more": (2, 2, 2, 2, 2),
}

XP_AWARD_ROWS = [
    ("Less than 1", 5, 1),
    ("1", 10, 3),
    ("1+", 15, 4),
    ("2", 20, 5),
    ("2+", 25, 10),
    ("3", 35, 15),
    ("3+", 50, 25),
    ("4", 75, 50),
    ("4+", 125, 75),
    ("5", 175, 125),
    ("5+", 225, 175),
    ("6", 275, 225),
    ("6+", 350, 300),
    ("7–7+", 450, 400),
    ("8–8+", 650, 550),
    ("9–10+", 900, 700),
    ("11–12+", 1_100, 800),
    ("13–16+", 1_350, 950),
    ("17–20+", 2_000, 1_150),
    ("21–21+", 2_500, 2_000),
]

MATRIX_LABELS = [
    ("NH", 20, -1),
    ("Up to 1", 19, 0),
    ("1+ to 2", 18, 1),
    ("2+ to 3", 17, 2),
    ("3+ to 4", 16, 3),
    ("4+ to 5", 15, 4),
    ("5+ to 6", 14, 5),
    ("6+ to 7", 13, 6),
    ("7+ to 9", 12, 7),
    ("9+ to 11", 11, 8),
    ("11+ to 13", 10, 9),
    ("13+ to 15", 9, 10),
    ("15+ to 17", 8, 11),
    ("17+ to 19", 7, 12),
    ("19+ to 21", 6, 13),
    ("21+ or more", 5, 14),
]


class TestMonsterCatalog:
    def test_entry_and_page_counts(self):
        catalog = load_monsters()
        assert len({template.page for template in catalog.monsters}) == 138
        assert len(catalog.monsters) == PRINTED_STAT_BLOCKS - PACKED_BLOCKS + len(EXPANSIONS)
        assert len(catalog.monsters) == 233

    def test_every_expansion_exists(self):
        catalog = load_monsters()
        ids = {template.id for template in catalog.monsters}
        assert EXPANSIONS <= ids

    def test_troll_spot_values(self):
        troll = load_monsters().get("troll")
        assert (troll.ac, troll.ac_ascending) == (4, 15)
        dice = troll.hit_dice
        assert (dice.count, dice.modifier, dice.asterisks, dice.average_hp) == (6, 3, 1, 30)
        assert troll.xp == 650
        assert troll.morale == 10
        assert troll.morale_alternates[0].score == 8
        assert "fire" in troll.morale_alternates[0].condition
        regen = troll.ability("regeneration").params
        assert regen["delay_rounds"] == 3
        assert regen["per_round"] == 3
        assert regen["blocked_by"] == ("fire", "acid")
        assert regen["revive"] == "2d6"
        assert (troll.thac0, troll.attack_bonus) == (13, 6)

    def test_wight_gate_and_drain(self):
        wight = load_monsters().get("wight")
        assert wight.defenses.harmed_only_by == (DamageKey.SILVER, DamageKey.MAGIC)
        assert wight.ability("energy_drain").params == {"levels": 1, "xp_policy": "halfway"}
        assert "undead" in wight.categories

    def test_wraith_reduction_and_policy(self):
        wraith = load_monsters().get("wraith")
        assert wraith.defenses.harmed_only_by == (DamageKey.SILVER, DamageKey.MAGIC)
        assert len(wraith.defenses.reductions) == 1
        assert wraith.defenses.reductions[0].keys == (DamageKey.SILVER,)
        assert wraith.ability("energy_drain").params["xp_policy"] == "level_minimum"

    def test_spectre_drains_two(self):
        spectre = load_monsters().get("spectre")
        assert spectre.defenses.harmed_only_by == (DamageKey.MAGIC,)
        assert spectre.ability("energy_drain").params["levels"] == 2

    def test_red_dragon(self):
        dragon = load_monsters().get("red_dragon")
        assert (dragon.thac0, dragon.attack_bonus) == (11, 8)
        breath = dragon.ability("breath_weapon").params
        assert breath["shape"] == "cone"
        assert breath["length_feet"] == 90
        assert breath["element"] == "fire"
        assert breath["uses_per_day"] == 3
        assert breath["damage"] == "current_hp"
        energy = dragon.defenses.energy[Element.FIRE]
        assert (energy.immunity, energy.auto_save_magical) == ("nonmagical", True)
        assert dragon.ability("uses_fire") is not None

    def test_air_elemental_expansion_triple(self):
        catalog = load_monsters()
        lesser = catalog.get("air_elemental_lesser")
        intermediate = catalog.get("air_elemental_intermediate")
        greater = catalog.get("air_elemental_greater")
        assert [entry.ac for entry in (lesser, intermediate, greater)] == [2, 0, -2]
        assert [entry.hit_dice.count for entry in (lesser, intermediate, greater)] == [8, 12, 16]
        assert [entry.xp for entry in (lesser, intermediate, greater)] == [1_200, 1_900, 2_300]
        assert [entry.thac0 for entry in (lesser, intermediate, greater)] == [12, 10, 8]
        # The See-main-entry save resolution agrees with the printed main-entry values.
        assert lesser.saves.values.death == 8
        assert intermediate.saves.values.death == 6
        assert greater.saves.values.death == 2
        assert all(entry.defenses.harmed_only_by == (DamageKey.MAGIC,) for entry in (lesser, intermediate, greater))
        assert all("enchanted" in entry.categories for entry in (lesser, intermediate, greater))

    def test_hydra_7_fixed_hit_points(self):
        hydra = load_monsters().get("hydra_7")
        assert hydra.hit_dice.fixed_hp == 56
        assert hydra.hit_dice.count == 7
        assert hydra.xp == 450
        assert hydra.attacks[0].attacks[0].count == 7
        assert (hydra.thac0, hydra.attack_bonus) == (13, 6)

    def test_hellhound_expansion(self):
        hound = load_monsters().get("hellhound_5")
        breath = hound.ability("breath_weapon").params
        assert breath["damage"] == "5d6"
        assert breath["targeting"] == "single"
        assert breath.get("uses_per_day") is None
        assert breath["per_round_chance_in_six"] == 2
        assert hound.defenses.energy[Element.FIRE].immunity == "nonmagical"

    def test_undead_census_is_exactly_the_eight_pages(self):
        catalog = load_monsters()
        undead_pages = {template.page for template in catalog.monsters if "undead" in template.categories}
        assert undead_pages == UNDEAD_PAGES

    def test_shadow_is_not_undead(self):
        shadow = load_monsters().get("shadow")
        assert "undead" not in shadow.categories
        assert shadow.defenses.harmed_only_by == (DamageKey.MAGIC,)

    def test_person_tag_covers_the_thirty_three_listed_pages(self):
        catalog = load_monsters()
        person_pages = {template.page for template in catalog.monsters if "person" in template.categories}
        assert len(person_pages) == 33
        # The veteran page expands, so templates exceed pages by its two extras.
        assert sum(1 for template in catalog.monsters if "person" in template.categories) == 35

    def test_bone_golem_override_provenance(self):
        bone = load_monsters().get("bone_golem")
        assert bone.overrides_applied == ("attacks", "categories")
        assert [routine.attacks[0].count for routine in bone.attacks] == [2, 4]
        assert all(routine.attacks[0].by_weapon for routine in bone.attacks)

    def test_tricky_defense_census(self):
        catalog = load_monsters()
        assert catalog.get("mummy").defenses.harmed_only_by == (DamageKey.FIRE, DamageKey.MAGIC)
        assert catalog.get("mummy").defenses.reductions[0].keys == ()
        assert catalog.get("black_pudding").defenses.harmed_only_by == (DamageKey.FIRE,)
        assert set(catalog.get("green_slime").defenses.harmed_only_by) == {DamageKey.FIRE, DamageKey.COLD}
        assert catalog.get("yellow_mould").defenses.harmed_only_by == (DamageKey.FIRE,)
        assert catalog.get("gargoyle").defenses.harmed_only_by == (DamageKey.MAGIC,)
        cube = catalog.get("gelatinous_cube")
        assert cube.defenses.energy[Element.COLD].immunity == "all"
        assert cube.defenses.energy[Element.LIGHTNING].immunity == "all"

    def test_no_hit_roll_required_pages(self):
        catalog = load_monsters()
        for monster_id in ("green_slime", "yellow_mould"):
            template = catalog.get(monster_id)
            assert not template.attack_roll_required
            assert template.ac is None and template.ac_ascending is None

    def test_varies_morale_compiles_to_none(self):
        assert load_monsters().get("merchant").morale is None

    def test_lycanthrope_alternate_form_ac(self):
        werewolf = load_monsters().get("werewolf")
        assert werewolf.ac_alternates and werewolf.ac_alternates[0].condition == "in human form"
        assert werewolf.defenses.harmed_only_by == (DamageKey.SILVER, DamageKey.MAGIC)

    def test_wight_touch_is_effect_only(self):
        wight = load_monsters().get("wight")
        touch = wight.attacks[0].attacks[0]
        assert touch.damage is None and touch.fixed_damage is None
        assert touch.effects == ("energy_drain",)

    def test_every_monster_spawnable_and_consistent(self):
        for template in load_monsters().monsters:
            dice = template.hit_dice
            assert dice.count > 0 or dice.fixed_hp is not None
            if template.attack_roll_required:
                assert template.ac is not None and template.ac_ascending is not None


class TestCombatTables:
    def test_matrix_rows_verbatim(self):
        rows = load_combat_tables().attack_matrix.rows
        assert [(row.hd_label, row.thac0, row.attack_bonus) for row in rows] == MATRIX_LABELS

    def test_every_cell_is_the_clamp_formula(self):
        # The survey's structural finding, locked as a property: every printed cell
        # equals clamp(THAC0 − AC, 2, 20).
        for row in load_combat_tables().attack_matrix.rows:
            for ac, required in row.by_ac.items():
                assert required == max(2, min(20, row.thac0 - ac)), (row.hd_label, ac)

    def test_lookup_extends_beyond_printed_columns(self):
        assert to_hit_ac(19, -10) == 20
        assert to_hit_ac(5, 30) == 2
        assert to_hit_ac(13, 4) == 9

    def test_monster_save_bands_verbatim(self):
        bands = load_combat_tables().monster_saves
        got = {
            band.label: (band.saves.death, band.saves.wands, band.saves.paralysis, band.saves.breath, band.saves.spells)
            for band in bands
        }
        assert got == MONSTER_SAVE_BANDS

    def test_xp_awards_verbatim(self):
        rows = load_combat_tables().xp_awards
        assert [(row.label, row.base, row.bonus) for row in rows] == XP_AWARD_ROWS

    def test_every_printed_monster_xp_rederives(self):
        tables = load_combat_tables()
        for template in load_monsters().monsters:
            assert template.xp == monster_xp(tables, template.hit_dice), template.id

    def test_dragon_turtle_proves_above_21_inflation(self):
        # (2,500 + 9×250) + 1 × (2,000 + 9×250) = 9,000.
        turtle = load_monsters().get("dragon_turtle")
        assert turtle.hit_dice.count == 30
        assert turtle.hit_dice.asterisks == 1
        assert turtle.xp == 9_000

    def test_goblin_negative_modifier_maps_to_the_lower_band(self):
        goblin = load_monsters().get("goblin")
        assert goblin.hit_dice.modifier == -1
        assert xp_band_label(goblin.hit_dice) == "Less than 1"
        assert goblin.xp == 5
        assert (goblin.thac0, goblin.attack_bonus) == (19, 0)

    @pytest.mark.parametrize(
        ("count", "bonus", "expected"),
        [(1, False, 19), (2, False, 18), (2, True, 17), (6, True, 13), (12, False, 10), (30, False, 5)],
    )
    def test_thac0_for_hd(self, count, bonus, expected):
        assert thac0_for_hd(count, bonus_modifier=bonus)[0] == expected

    def test_matrix_total_order(self):
        # Lower AC is never easier to hit.
        for row in load_combat_tables().attack_matrix.rows:
            cells = [row.by_ac[ac] for ac in range(-3, 10)]
            assert cells == sorted(cells, reverse=True)

    def test_xp_band_labels(self):
        assert xp_band_label(MonsterHitDice(count=1, die=4, average_hp=2)) == "Less than 1"
        assert xp_band_label(MonsterHitDice(count=2, modifier=2)) == "2+"
        assert xp_band_label(MonsterHitDice(count=7)) == "7–7+"
        assert xp_band_label(MonsterHitDice(count=10, modifier=1)) == "9–10+"
        assert xp_band_label(MonsterHitDice(count=22)) == "21–21+"
