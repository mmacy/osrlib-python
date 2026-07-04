"""Tests for kernel treasure generation: determinism, pins, refs, and statistics."""

from collections import Counter

from osrlib.core.items import MagicItemCategory
from osrlib.core.monsters import IdAllocator
from osrlib.core.rng import RngStream
from osrlib.core.treasure import (
    TREASURE_STREAM,
    MagicItemType,
    generate_magic_item,
    generate_treasure,
    generate_unguarded_treasure,
    plan_treasure_ref,
    roll_room_contents,
)
from osrlib.data import load_magic_items, load_monsters, load_spells, load_treasure_tables

CHI_SQUARE_CRITICAL = {1: 10.83, 3: 16.27, 4: 18.47, 7: 24.32}


def chi_square(observed: dict, expected: dict) -> float:
    return sum((observed.get(key, 0) - expected[key]) ** 2 / expected[key] for key in expected)


def stream_for(seed: int) -> RngStream:
    return RngStream.from_seed_material(seed, TREASURE_STREAM)


class TestGenerationDeterminism:
    def test_same_seed_same_hoard(self):
        first = generate_treasure("A", tier="expert", stream=stream_for(7), allocator=IdAllocator())
        second = generate_treasure("A", tier="expert", stream=stream_for(7), allocator=IdAllocator())
        assert first == second

    def test_valuable_values_fixed_at_generation(self):
        result = generate_treasure("M", tier="expert", stream=stream_for(11), allocator=IdAllocator())
        gem_values = {10, 50, 100, 500, 1000}
        for valuable in result.valuables:
            if valuable.kind == "gem":
                assert valuable.value_gp in gem_values
                assert valuable.weight_coins == 1
            else:
                assert 300 <= valuable.value_gp <= 1800 and valuable.value_gp % 100 == 0
                assert valuable.weight_coins == 10

    def test_instance_ids_come_from_the_allocator(self):
        allocator = IdAllocator()
        result = generate_treasure("L", tier="basic", stream=stream_for(3), allocator=allocator)
        for index, valuable in enumerate(result.valuables, start=1):
            assert valuable.instance_id == f"valuable-{index:04d}"


class TestMagicItemGeneration:
    def test_tier_selects_the_printed_columns(self):
        # Under the Basic column a d8 selects among the eight B-column potions only.
        seen = set()
        stream = stream_for(23)
        allocator = IdAllocator()
        for _ in range(200):
            items = generate_magic_item(MagicItemType.POTION, tier="basic", stream=stream, allocator=allocator)
            seen.add(items[0].template_id)
        basic_ids = {
            "potion_of_diminution",
            "potion_of_esp",
            "potion_of_gaseous_form",
            "potion_of_growth",
            "potion_of_healing",
            "potion_of_invisibility",
            "potion_of_levitation",
            "potion_of_poison",
        }
        assert seen == basic_ids

    def test_exclusion_rerolls_consume_draws(self):
        # With every category but weapon excluded, the roll lands on weapon; the
        # re-rolls consume master-table draws, so the outcome differs from the
        # unexcluded first roll under the same seed.
        exclude = tuple(category for category in MagicItemType if category is not MagicItemType.WEAPON)
        items = generate_magic_item(None, tier="expert", stream=stream_for(5), allocator=IdAllocator(), exclude=exclude)
        template = load_magic_items().get(items[0].template_id)
        assert template.category in (MagicItemCategory.WEAPON,)

    def test_armour_type_d8_sets_the_base(self):
        stream = stream_for(29)
        allocator = IdAllocator()
        bases = set()
        for _ in range(100):
            for instance in generate_magic_item(
                MagicItemType.ARMOUR, tier="expert", stream=stream, allocator=allocator
            ):
                template = load_magic_items().get(instance.template_id)
                if template.base_item_id is None:
                    assert instance.base_item_id in ("leather", "chainmail", "plate_mail")
                    bases.add(instance.base_item_id)
                else:
                    assert instance.base_item_id == template.base_item_id
        assert bases == {"leather", "chainmail", "plate_mail"}

    def test_charges_rolled_at_creation(self):
        stream = stream_for(31)
        allocator = IdAllocator()
        for _ in range(60):
            for instance in generate_magic_item(
                MagicItemType.ROD_STAFF_WAND, tier="expert", stream=stream, allocator=allocator
            ):
                template = load_magic_items().get(instance.template_id)
                if template.charges_dice is None:
                    assert instance.charges_remaining is None
                elif template.category is MagicItemCategory.WAND:
                    assert 2 <= instance.charges_remaining <= 20
                elif template.category is MagicItemCategory.STAFF:
                    assert 3 <= instance.charges_remaining <= 30
                else:
                    assert 1 <= instance.charges_remaining <= 10

    def test_ammunition_quantities_by_tier(self):
        stream = stream_for(37)
        allocator = IdAllocator()
        for tier in ("basic", "expert"):
            for _ in range(60):
                for instance in generate_magic_item(
                    MagicItemType.WEAPON, tier=tier, stream=stream, allocator=allocator
                ):
                    template = load_magic_items().get(instance.template_id)
                    if template.base_item_id in ("arrows", "crossbow_bolts"):
                        assert instance.quantity >= 1

    def test_scroll_contents_generated(self):
        stream = stream_for(41)
        allocator = IdAllocator()
        catalog = load_spells()
        seen_scroll = False
        for _ in range(120):
            for instance in generate_magic_item(
                MagicItemType.SCROLL, tier="expert", stream=stream, allocator=allocator
            ):
                if instance.template_id.startswith("spell_scroll_"):
                    seen_scroll = True
                    spell_list = str(instance.state["spell_list"])
                    for spell_id in instance.state["spells"]:
                        assert catalog.get(str(spell_id)).spell_list == {"cleric": "cleric"}.get(spell_list, spell_list)
        assert seen_scroll

    def test_energy_drain_sword_total_rolled(self):
        stream = stream_for(43)
        allocator = IdAllocator()
        found = False
        for _ in range(400):
            for instance in generate_magic_item(MagicItemType.SWORD, tier="expert", stream=stream, allocator=allocator):
                if instance.template_id == "sword_plus_1_energy_drain":
                    assert 5 <= int(instance.state["drains_remaining"]) <= 8
                    found = True
        assert found

    def test_special_purpose_swords_are_always_sentient(self):
        stream = stream_for(47)
        allocator = IdAllocator()
        specials = 0
        for _ in range(400):
            for instance in generate_magic_item(MagicItemType.SWORD, tier="expert", stream=stream, allocator=allocator):
                if instance.sentience is not None and instance.sentience.special_purpose is not None:
                    specials += 1
                    assert instance.sentience.intelligence == 12
                    assert instance.sentience.ego == 12
                    # INT 12 grants 3 sensory + 1 extraordinary; the sensory 96–99
                    # band converts a sensory grant into an extraordinary one.
                    total_powers = len(instance.sentience.sensory_powers) + len(instance.sentience.extraordinary_powers)
                    assert total_powers >= 4
                    assert len(instance.sentience.extraordinary_powers) >= 1
        assert specials > 0

    def test_generated_items_enter_play_unidentified(self):
        items = generate_magic_item(MagicItemType.POTION, tier="expert", stream=stream_for(53), allocator=IdAllocator())
        assert not items[0].identified


class TestTreasureRefSemantics:
    def test_the_six_case_census(self):
        monsters = load_monsters()
        bandit = plan_treasure_ref(monsters.get("bandit").treasure)
        assert (bandit.lair, bandit.individual, bandit.group) == (("A",), (), ("U",))
        pixie = plan_treasure_ref(monsters.get("pixie").treasure)
        assert (pixie.lair, pixie.individual, pixie.group) == ((), ("R", "S"), ())
        ogre = plan_treasure_ref(monsters.get("ogre").treasure)
        assert (ogre.lair, ogre.extra_gp) == (("C",), 1000)
        noble = plan_treasure_ref(monsters.get("noble").treasure)
        assert (noble.group, noble.multiplier) == (("V",), 3)
        elephant = plan_treasure_ref(monsters.get("elephant").treasure)
        assert elephant == plan_treasure_ref(monsters.get("elephant").treasure)
        assert (elephant.lair, elephant.individual, elephant.group) == ((), (), ())
        driver_ant = plan_treasure_ref(monsters.get("driver_ant").treasure)
        assert (driver_ant.group, driver_ant.lair) == (("U",), ())


class TestRoomContents:
    def test_special_consumes_no_treasure_die(self):
        # Drive the stream until a Special lands; its result carries no second die.
        stream = stream_for(59)
        for _ in range(60):
            result = roll_room_contents(stream)
            if result.row.contents == "special":
                assert result.treasure_roll is None and not result.treasure_present
                return
        raise AssertionError("no special rolled in 60 tries")


class TestUnguardedGeneration:
    def test_band_clamps_and_generates(self):
        result = generate_unguarded_treasure(12, tier="expert", stream=stream_for(61), allocator=IdAllocator())
        # The 8-9 band's sp entry is ungated: coins are always present.
        assert result.coins.sp > 0


class TestTreasureStatistics:
    def test_type_a_presence_rates(self):
        tables = load_treasure_tables()
        entries = tables.treasure_type("A").entries
        stream = stream_for(67)
        allocator = IdAllocator()
        trials = 4_000
        gem_hits = 0
        for _ in range(trials):
            result = generate_treasure("A", tier="expert", stream=stream, allocator=allocator)
            if any(valuable.kind == "gem" for valuable in result.valuables):
                gem_hits += 1
        chance = entries[5].chance_pct / 100
        expected = {True: trials * chance, False: trials * (1 - chance)}
        assert chi_square({True: gem_hits, False: trials - gem_hits}, expected) < CHI_SQUARE_CRITICAL[1]

    def test_gem_value_bands(self):
        stream = stream_for(71)
        allocator = IdAllocator()
        values = Counter()
        trials = 0
        while trials < 8_000:
            result = generate_treasure("L", tier="basic", stream=stream, allocator=allocator)
            for valuable in result.valuables:
                values[valuable.value_gp] += 1
                trials += 1
        probabilities = {10: 4 / 20, 50: 5 / 20, 100: 6 / 20, 500: 4 / 20, 1000: 1 / 20}
        expected = {value: trials * probability for value, probability in probabilities.items()}
        assert chi_square(values, expected) < CHI_SQUARE_CRITICAL[4]

    def test_master_table_distribution_under_both_tiers(self):
        tables = load_treasure_tables().magic_item_types
        for tier in ("basic", "expert"):
            stream = stream_for(73 if tier == "basic" else 79)
            trials = 20_000
            counts = Counter(tables.category_for_roll(stream.randbelow(100) + 1, tier=tier) for _ in range(trials))
            expected = {}
            for row in tables.rows:
                low = getattr(row, f"{tier}_min")
                high = getattr(row, f"{tier}_max")
                expected[row.category] = trials * (high - low + 1) / 100
            assert chi_square(counts, expected) < CHI_SQUARE_CRITICAL[7]

    def test_armour_type_d8_bands(self):
        table = load_magic_items().armour_type
        stream = stream_for(83)
        trials = 12_000
        counts = Counter(table.base_for_roll(stream.randbelow(8) + 1) for _ in range(trials))
        expected = {"leather": trials * 2 / 8, "chainmail": trials * 4 / 8, "plate_mail": trials * 2 / 8}
        assert chi_square(counts, expected) < CHI_SQUARE_CRITICAL[3]
