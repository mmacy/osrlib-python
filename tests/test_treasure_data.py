"""Tests for the compiled treasure and magic item data: census, fidelity, spot checks."""

import pytest

from osrlib.core.items import MagicItemCategory
from osrlib.core.treasure import MagicItemType, TreasureSection
from osrlib.data import load_magic_items, load_treasure_tables

# Treasure_Types.md, verbatim: (letter, kind, average gp, entry count).
_TYPE_CENSUS = (
    ("A", TreasureSection.HOARD, 18000, 8),
    ("B", TreasureSection.HOARD, 2000, 7),
    ("C", TreasureSection.HOARD, 1000, 6),
    ("D", TreasureSection.HOARD, 3900, 6),
    ("E", TreasureSection.HOARD, 2300, 7),
    ("F", TreasureSection.HOARD, 7700, 7),
    ("G", TreasureSection.HOARD, 23000, 5),
    ("H", TreasureSection.HOARD, 60000, 8),
    ("I", TreasureSection.HOARD, 11000, 4),
    ("J", TreasureSection.HOARD, 25, 2),
    ("K", TreasureSection.HOARD, 180, 2),
    ("L", TreasureSection.HOARD, 240, 1),
    ("M", TreasureSection.HOARD, 50000, 4),
    ("N", TreasureSection.HOARD, 0, 1),
    ("O", TreasureSection.HOARD, 0, 1),
    ("P", TreasureSection.INDIVIDUAL, 0.1, 1),
    ("Q", TreasureSection.INDIVIDUAL, 1, 1),
    ("R", TreasureSection.INDIVIDUAL, 3, 1),
    ("S", TreasureSection.INDIVIDUAL, 5, 1),
    ("T", TreasureSection.INDIVIDUAL, 17, 1),
    ("U", TreasureSection.GROUP, 160, 6),
    ("V", TreasureSection.GROUP, 330, 7),
)


class TestTreasureTypes:
    def test_census(self):
        tables = load_treasure_tables()
        assert [(t.letter, t.kind, t.average_gp, len(t.entries)) for t in tables.treasure_types] == [
            (letter, kind, pytest.approx(average), count) for letter, kind, average, count in _TYPE_CENSUS
        ]

    def test_type_a_verbatim(self):
        entries = load_treasure_tables().treasure_type("A").entries
        printed = [
            (25, "cp", "1d6×1000"),
            (30, "sp", "1d6×1000"),
            (20, "ep", "1d4×1000"),
            (35, "gp", "2d6×1000"),
            (25, "pp", "1d2×1000"),
        ]
        for entry, (chance, denomination, dice) in zip(entries[:5], printed, strict=True):
            assert entry.chance_pct == chance
            assert entry.coins.denomination.value == denomination
            assert entry.coins.dice == dice
        assert (entries[5].chance_pct, entries[5].gems_dice) == (50, "6d6")
        assert (entries[6].chance_pct, entries[6].jewellery_dice) == (50, "6d6")
        magic = entries[7]
        assert magic.chance_pct == 30
        assert magic.magic[0].kind == "any" and magic.magic[0].count == 3

    def test_type_b_pool(self):
        allotment = load_treasure_tables().treasure_type("B").entries[-1].magic[0]
        assert allotment.kind == "pool"
        assert allotment.categories == (MagicItemType.SWORD, MagicItemType.ARMOUR, MagicItemType.WEAPON)
        assert allotment.count == 1

    def test_type_f_exclusion_plus_extras(self):
        magic = load_treasure_tables().treasure_type("F").entries[-1].magic
        assert magic[0].kind == "any" and magic[0].count == 3 and magic[0].exclude == (MagicItemType.WEAPON,)
        assert magic[1].kind == "category" and magic[1].categories == (MagicItemType.POTION,) and magic[1].count == 1
        assert magic[2].kind == "category" and magic[2].categories == (MagicItemType.SCROLL,) and magic[2].count == 1

    def test_types_n_and_o_diced_categories(self):
        n = load_treasure_tables().treasure_type("N").entries[0]
        assert n.chance_pct == 40 and n.magic[0].count_dice == "2d4"
        assert n.magic[0].categories == (MagicItemType.POTION,)
        o = load_treasure_tables().treasure_type("O").entries[0]
        assert o.chance_pct == 50 and o.magic[0].count_dice == "1d4"
        assert o.magic[0].categories == (MagicItemType.SCROLL,)

    def test_individual_types_are_ungated(self):
        tables = load_treasure_tables()
        printed = {"P": ("cp", "3d8"), "Q": ("sp", "3d6"), "R": ("ep", "2d6"), "S": ("gp", "2d4"), "T": ("pp", "1d6")}
        for letter, (denomination, dice) in printed.items():
            entry = tables.treasure_type(letter).entries[0]
            assert entry.chance_pct == 0
            assert (entry.coins.denomination.value, entry.coins.dice) == (denomination, dice)

    def test_type_h_multiplied_forms(self):
        entries = load_treasure_tables().treasure_type("H").entries
        assert entries[1].coins.dice == "1d100×1000"  # 1d100 × 1,000sp
        assert entries[5].gems_dice == "1d100"
        assert entries[6].jewellery_dice == "1d4×10"


class TestGemsAndJewellery:
    def test_gem_bands_verbatim(self):
        bands = load_treasure_tables().gems.bands
        assert [(band.roll_min, band.roll_max, band.value_gp) for band in bands] == [
            (1, 4, 10),
            (5, 9, 50),
            (10, 15, 100),
            (16, 19, 500),
            (20, 1000),
        ][:4] + [(20, 20, 1000)]

    def test_jewellery_dice(self):
        assert load_treasure_tables().gems.jewellery_dice == "3d6×100"

    def test_manual_notes_keep_referee_prose(self):
        notes = load_treasure_tables().gems.manual_notes
        assert any("50%" in note for note in notes)  # damaged jewellery
        assert any("combine the values" in note for note in notes)


class TestMagicItemTypeTable:
    def test_both_columns_verbatim(self):
        rows = load_treasure_tables().magic_item_types.rows
        printed = [
            ("armour", 1, 10, 1, 10),
            ("misc", 11, 15, 11, 15),
            ("potion", 16, 40, 16, 35),
            ("ring", 41, 45, 36, 40),
            ("rod_staff_wand", 46, 50, 41, 45),
            ("scroll", 51, 70, 46, 75),
            ("sword", 71, 90, 76, 95),
            ("weapon", 91, 100, 96, 100),
        ]
        assert [(r.category.value, r.basic_min, r.basic_max, r.expert_min, r.expert_max) for r in rows] == printed


class TestStockingAndUnguarded:
    def test_stocking_rows_verbatim(self):
        rows = load_treasure_tables().stocking.rows
        assert [(r.roll_min, r.roll_max, r.contents, r.treasure_chance_in_six) for r in rows] == [
            (1, 2, "empty", 1),
            (3, 4, "monster", 3),
            (5, 5, "special", 0),
            (6, 6, "trap", 2),
        ]

    def test_unguarded_bands_cover_levels(self):
        bands = load_treasure_tables().unguarded.bands
        assert [(band.min_level, band.max_level) for band in bands] == [(1, 1), (2, 3), (4, 5), (6, 7), (8, 9)]

    def test_sp_ungated_and_gp_gating_asymmetry(self):
        bands = load_treasure_tables().unguarded.bands
        for band in bands:
            sp = band.entries[0]
            assert sp.coins.denomination.value == "sp" and sp.chance_pct == 0
        # The gp entries are 50%-gated on the first two bands only.
        gp_gates = [band.entries[1].chance_pct for band in bands]
        assert gp_gates == [50, 50, 0, 0, 0]

    def test_levels_past_the_bands_clamp(self):
        table = load_treasure_tables().unguarded
        assert table.band_for_level(12) is table.bands[-1]


class TestMagicItemCatalogCensus:
    def test_sub_table_shapes(self):
        catalog = load_magic_items()
        shapes = {t.category.value: (t.basic_die, len(t.rows)) for t in catalog.sub_tables}
        assert shapes == {
            "armour": (4, 21),
            "misc": (10, 31),
            "potion": (8, 26),
            "ring": (6, 19),
            "rod_staff_wand": (6, 21),
            "scroll": (8, 22),
            "sword": (8, 17),
            "weapon": (4, 21),
        }

    def test_potion_ids(self):
        catalog = load_magic_items()
        potions = [item for item in catalog.items if item.category is MagicItemCategory.POTION]
        assert len(potions) == 26  # 26 outcomes to 26 ids over 21 pages
        controls = [item.id for item in potions if item.id.startswith("potion_of_control")]
        assert len(controls) == 6

    def test_ring_ids_and_wish_bands(self):
        catalog = load_magic_items()
        rings = [item for item in catalog.items if item.category is MagicItemCategory.RING]
        assert len(rings) == 17  # 19 rows to 17 ids over 16 pages
        wish_rows = [
            row
            for table in catalog.sub_tables
            if table.category is MagicItemType.RING
            for row in table.rows
            if row.item_ids == ("ring_of_wishes",)
        ]
        assert [row.params["wish_count_dice"] for row in wish_rows] == ["1d2", "1d3", "1d3+1"]

    def test_device_counts(self):
        catalog = load_magic_items()
        by_category = {}
        for item in catalog.items:
            by_category.setdefault(item.category.value, []).append(item.id)
        assert len(by_category["wand"]) == 13
        assert len(by_category["staff"]) == 7
        assert by_category["rod"] == ["rod_of_cancellation"]

    def test_misc_hyphen_ranges_and_94_94(self):
        table = load_magic_items().sub_table(MagicItemType.MISC)
        mirror = next(row for row in table.rows if row.item_ids == ("mirror_of_life_trapping",))
        assert (mirror.expert_min, mirror.expert_max) == (94, 94)

    def test_charges_dice_per_category(self):
        catalog = load_magic_items()
        for item in catalog.items:
            if item.category is MagicItemCategory.WAND:
                assert item.charges_dice == "2d10"
            elif item.category is MagicItemCategory.ROD:
                assert item.charges_dice == "1d10"
            elif item.category is MagicItemCategory.STAFF:
                expected = None if item.id == "staff_of_healing" else "3d10"
                assert item.charges_dice == expected

    def test_armour_type_d8(self):
        table = load_magic_items().armour_type
        assert [(row.roll_min, row.roll_max, row.base_item_id) for row in table.rows] == [
            (1, 2, "leather"),
            (3, 6, "chainmail"),
            (7, 8, "plate_mail"),
        ]

    def test_sword_grammar_including_versus(self):
        catalog = load_magic_items()
        dragons = catalog.get("sword_plus_1_plus_3_vs_dragons")
        assert dragons.attack_bonus == 1 and dragons.damage_bonus == 1
        assert dragons.versus[0].bonus == 3
        assert "red_dragon" in dragons.versus[0].template_ids
        cursed = catalog.get("sword_minus_2_cursed")
        assert cursed.cursed and cursed.attack_bonus == -2 and cursed.damage_bonus == -2
        undead = catalog.get("sword_plus_1_plus_3_vs_undead")
        assert undead.versus[0].categories == ("undead",)

    def test_armour_bundles_and_cursed_forms(self):
        catalog = load_magic_items()
        table = catalog.sub_table(MagicItemType.ARMOUR)
        bundle = next(row for row in table.rows if len(row.item_ids) == 2 and row.expert_min == 16)
        assert bundle.item_ids == ("armour_plus_1", "shield_plus_1")
        with_shield = next(
            row for row in table.rows if row.item_ids[0] == "cursed_armour_minus_2" and len(row.item_ids) == 2
        )
        assert with_shield.item_ids == ("cursed_armour_minus_2", "shield_plus_1")
        ac_set = catalog.get("cursed_armour_ac_9")
        assert ac_set.ac_set == 9 and ac_set.ac_set_ascending == 10 and ac_set.cursed

    def test_weapon_quantity_bands(self):
        catalog = load_magic_items()
        table = catalog.sub_table(MagicItemType.WEAPON)
        arrow_rows = [row for row in table.rows if row.item_ids == ("arrows_plus_1",)]
        assert len(arrow_rows) == 2
        assert {str(row.params.get("quantity_dice")) for row in arrow_rows} == {"3d10", "2d6"}
        tiered = next(row for row in arrow_rows if "basic_quantity_fixed" in row.params)
        assert tiered.params["basic_quantity_fixed"] == 10
        dagger = catalog.get("dagger_plus_2_plus_3_vs_orcs_goblins_and_kobolds")
        assert dagger.versus[0].template_ids == ("orc", "goblin", "kobold")

    def test_every_outcome_resolves(self):
        catalog = load_magic_items()
        known = {item.id for item in catalog.items}
        for table in catalog.sub_tables:
            for row in table.rows:
                assert set(row.item_ids) <= known

    def test_truncated_helm_override(self):
        helm = load_magic_items().get("helm_of_telepathy")
        assert helm.overrides_applied == ("manual.4",)
        assert any(line.endswith("not compelled to respond.") for line in helm.manual)

    def test_scroll_structures(self):
        catalog = load_magic_items()
        assert catalog.get("spell_scroll_7").params["spell_count"] == 7
        cursed = catalog.get("cursed_scroll")
        assert [curse.id for curse in cursed.curses] == [
            "transformation",
            "summoning",
            "lost_item",
            "energy_drain",
            "ability_score_re_roll",
            "slow_healing",
        ]
        assert [curse.wired for curse in cursed.curses] == [False, False, False, True, False, True]
        lycanthropes = catalog.get("scroll_of_protection_from_lycanthropes")
        assert lycanthropes.effect.kind == "ward" and lycanthropes.effect.duration_amount == 6
        elementals = catalog.get("scroll_of_protection_from_elementals")
        assert elementals.effect.duration_amount == 2
        assert catalog.get("scroll_of_protection_from_magic").effect is None  # manual

    def test_treasure_map_recipes(self):
        catalog = load_magic_items()
        map_iv = catalog.get("treasure_map_iv")
        assert map_iv.hoard_recipe[0].magic[0].exclude == (MagicItemType.SWORD,)
        map_xi = catalog.get("treasure_map_xi")
        assert map_xi.hoard_recipe[0].coins.dice == "5d6×1000"
        assert map_xi.hoard_recipe[1].gems_dice == "5d6"

    def test_scroll_spell_level_table(self):
        table = load_magic_items().scroll_spell_levels
        assert table.level_for_basic(1, divine=False) == 1
        assert table.level_for_basic(6, divine=True) == 3
        assert table.level_for_expert(100, divine=False) == 6
        assert table.level_for_expert(100, divine=True) == 5

    def test_sentient_sword_tables(self):
        tables = load_magic_items().sentient_swords
        assert [row.int_score for row in tables.communication] == [7, 8, 9, 10, 11, 12]
        assert tables.communication[3].communication == "speech" and not tables.communication[3].reading
        twelve = next(row for row in tables.powers if row.int_score == 12)
        assert (twelve.sensory, twelve.extraordinary) == (3, 1)
        assert tables.languages[-1].result == "roll_twice"
        assert tables.extraordinary_bands[-1].result == "roll_thrice"
        assert tables.power("extra_damage").duplicates_allowed
        assert tables.power("healing").duplicates_allowed
        assert not tables.power("telekinesis").duplicates_allowed
        assert [band.result for band in tables.special_purposes] == [
            "arcane_spell_casters",
            "divine_spell_casters",
            "warriors",
            "specific_monster",
            "lawful_creatures",
            "chaotic_creatures",
        ]

    def test_versus_derived_sets(self):
        catalog = load_magic_items()
        spell_users = catalog.get("sword_plus_1_plus_2_vs_spell_users").versus[0]
        assert "medium" in spell_users.template_ids and "red_dragon" in spell_users.template_ids
        regenerating = catalog.get("sword_plus_1_plus_3_vs_regenerating_creatures").versus[0]
        assert "troll" in regenerating.template_ids
        lycanthropes = catalog.get("sword_plus_1_plus_2_vs_lycanthropes").versus[0]
        assert set(lycanthropes.template_ids) == {
            "devil_swine",
            "werebear",
            "wereboar",
            "wererat",
            "weretiger",
            "werewolf",
        }

    def test_weights_from_treasure_weight_rows(self):
        catalog = load_magic_items()
        assert catalog.get("potion_of_healing").weight_coins == 10
        assert catalog.get("spell_scroll_1").weight_coins == 1
        assert catalog.get("rod_of_cancellation").weight_coins == 20
        assert catalog.get("staff_of_healing").weight_coins == 40
        assert catalog.get("wand_of_cold").weight_coins == 10
        assert catalog.get("ring_of_protection").weight_coins == 0
        assert catalog.get("bag_of_holding").weight_coins == 0
        assert catalog.get("bag_of_holding").params["loaded_weight_coins"] == 600
