"""Tests for osrlib.core.items — purse, weights, movement rates, and legality."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from osrlib.core.items import (
    COIN_VALUES_CP,
    MAX_LOAD_COINS,
    CoinPurse,
    Inventory,
    ItemInstance,
    encounter_movement_rate,
    equip,
    equipment_weight_coins,
    movement_rate_feet,
    purchase,
    tracked_weight_coins,
    treasure_weight_coins,
    unequip,
    validate_equip,
    validate_purchase,
)
from osrlib.core.ruleset import EncumbranceMode, Ruleset
from osrlib.data import load_classes, load_equipment

NONE = Ruleset(encumbrance=EncumbranceMode.NONE)
BASIC = Ruleset(encumbrance=EncumbranceMode.BASIC)
DETAILED = Ruleset(encumbrance=EncumbranceMode.DETAILED)


def instance(item_id: str, quantity: int = 1) -> ItemInstance:
    return ItemInstance(template=load_equipment().get(item_id), quantity=quantity)


class TestCoinPurse:
    def test_value_and_weight(self):
        purse = CoinPurse(pp=1, gp=2, ep=3, sp=4, cp=5)
        assert purse.value_cp == 500 + 200 + 150 + 40 + 5
        assert purse.total_coins == 15

    def test_spend_exact_gold(self):
        purse = CoinPurse(gp=30)
        purse.spend(12)
        assert purse.gp == 18
        assert purse.value_cp == 1800

    def test_spend_makes_change_smallest_first_largest_back(self):
        # Pay smallest-first (cp, sp, ep, gp, pp); change back in the fewest coins.
        purse = CoinPurse(cp=250, gp=3)
        purse.spend(3)
        # 250 cp + 1 gp pays 350 cp for a 300 cp cost; 50 cp change is one ep.
        assert (purse.cp, purse.sp, purse.ep, purse.gp, purse.pp) == (0, 0, 1, 2, 0)
        assert purse.value_cp == 250 + 300 - 300

    def test_spend_zero_is_a_noop(self):
        purse = CoinPurse(gp=5)
        purse.spend(0)
        assert purse.gp == 5

    def test_overspending_raises(self):
        purse = CoinPurse(gp=5)
        assert not purse.can_afford(6)
        with pytest.raises(ValueError):
            purse.spend(6)

    def test_negative_cost_raises(self):
        with pytest.raises(ValueError):
            CoinPurse(gp=5).can_afford(-1)


@given(
    coins=st.tuples(*[st.integers(min_value=0, max_value=40) for _ in range(5)]),
    cost_gp=st.integers(min_value=0, max_value=300),
)
def test_purse_spend_preserves_value_and_never_goes_negative(coins: tuple[int, ...], cost_gp: int):
    purse = CoinPurse(pp=coins[0], gp=coins[1], ep=coins[2], sp=coins[3], cp=coins[4])
    value_before = purse.value_cp
    if purse.can_afford(cost_gp):
        purse.spend(cost_gp)
        assert purse.value_cp == value_before - cost_gp * 100
        assert all(getattr(purse, denomination) >= 0 for denomination in COIN_VALUES_CP)
    else:
        with pytest.raises(ValueError):
            purse.spend(cost_gp)
        assert purse.value_cp == value_before


def test_coin_conversions_round_trip():
    # 1 pp = 5 gp = 10 ep = 50 sp = 500 cp, per the SRD's Wealth conversion table.
    assert COIN_VALUES_CP == {"pp": 500, "gp": 100, "ep": 50, "sp": 10, "cp": 1}
    assert COIN_VALUES_CP["pp"] == 5 * COIN_VALUES_CP["gp"] == 10 * COIN_VALUES_CP["ep"]
    assert COIN_VALUES_CP["gp"] == 10 * COIN_VALUES_CP["sp"] == 100 * COIN_VALUES_CP["cp"]


class TestWeights:
    def test_coins_weigh_one_each(self):
        inventory = Inventory(purse=CoinPurse(pp=10, gp=20, cp=30))
        assert treasure_weight_coins(inventory) == 60

    def test_equipment_weight_sums_weapons_and_armour(self):
        inventory = Inventory(items=[instance("sword"), instance("plate_mail")])
        assert equipment_weight_coins(inventory) == 60 + 500

    def test_equipped_items_still_weigh(self):
        fighter = load_classes().get("fighter")
        inventory = Inventory(items=[instance("sword"), instance("plate_mail")])
        equip(inventory, fighter, inventory.items[0])
        equip(inventory, fighter, inventory.items[0])
        assert equipment_weight_coins(inventory) == 560

    def test_any_gear_adds_the_flat_80(self):
        # Pinned: miscellaneous gear is 80 coins once, however much is carried.
        one = Inventory(items=[instance("rope")])
        many = Inventory(items=[instance("rope"), instance("backpack"), instance("torch", 6)])
        assert equipment_weight_coins(one) == 80
        assert equipment_weight_coins(many) == 80

    def test_ammunition_weighs_nothing(self):
        inventory = Inventory(items=[instance("arrows", 40)])
        assert equipment_weight_coins(inventory) == 0

    def test_tracked_weight_by_mode(self):
        inventory = Inventory(items=[instance("sword")], purse=CoinPurse(gp=100))
        assert tracked_weight_coins(inventory, EncumbranceMode.NONE) == 0
        assert tracked_weight_coins(inventory, EncumbranceMode.BASIC) == 100
        assert tracked_weight_coins(inventory, EncumbranceMode.DETAILED) == 160


class TestMovementRates:
    def test_none_mode_is_always_base(self):
        inventory = Inventory(items=[instance("plate_mail")], purse=CoinPurse(gp=5000))
        assert movement_rate_feet(inventory, NONE) == 120

    def test_basic_mode_armour_and_treasure_matrix(self):
        fighter = load_classes().get("fighter")
        unarmoured = Inventory()
        assert movement_rate_feet(unarmoured, BASIC) == 120
        assert movement_rate_feet(unarmoured, BASIC, carrying_treasure=True) == 90
        light = Inventory(items=[instance("leather")])
        equip(light, fighter, light.items[0])
        assert movement_rate_feet(light, BASIC) == 90
        assert movement_rate_feet(light, BASIC, carrying_treasure=True) == 60
        heavy = Inventory(items=[instance("plate_mail")])
        equip(heavy, fighter, heavy.items[0])
        assert movement_rate_feet(heavy, BASIC) == 60
        assert movement_rate_feet(heavy, BASIC, carrying_treasure=True) == 30

    def test_detailed_mode_thresholds_are_inclusive(self):
        # "Up to" 400/600/800/1,600 coins.
        for weight, expected in ((400, 120), (401, 90), (600, 90), (601, 60), (800, 60), (801, 30), (1600, 30)):
            inventory = Inventory(purse=CoinPurse(gp=weight))
            assert movement_rate_feet(inventory, DETAILED) == expected, weight

    def test_over_max_load_immobilizes_in_both_tracking_modes(self):
        # Pinned: the 1,600-coin maximum load is general, not a detailed-mode extra.
        overloaded = Inventory(purse=CoinPurse(gp=MAX_LOAD_COINS + 1))
        assert movement_rate_feet(overloaded, BASIC) == 0
        assert movement_rate_feet(overloaded, DETAILED) == 0

    def test_encounter_rate_is_a_third(self):
        assert encounter_movement_rate(120) == 40
        assert encounter_movement_rate(90) == 30
        assert encounter_movement_rate(0) == 0


@given(gold=st.integers(min_value=0, max_value=2200), extra=st.integers(min_value=0, max_value=400))
def test_movement_is_monotonically_non_increasing_in_carried_weight(gold: int, extra: int):
    lighter = Inventory(purse=CoinPurse(gp=gold))
    heavier = Inventory(purse=CoinPurse(gp=gold + extra))
    for ruleset in (NONE, BASIC, DETAILED):
        assert movement_rate_feet(heavier, ruleset) <= movement_rate_feet(lighter, ruleset)


@given(
    weapon_ids=st.lists(st.sampled_from(["sword", "dagger", "pole_arm", "crossbow"]), max_size=4),
    gear_ids=st.lists(st.sampled_from(["rope", "backpack", "lantern"]), max_size=3),
    coins=st.integers(min_value=0, max_value=2000),
)
def test_total_weight_is_the_sum_of_its_parts(weapon_ids: list[str], gear_ids: list[str], coins: int):
    equipment = load_equipment()
    inventory = Inventory(
        items=[ItemInstance(template=equipment.get(item_id)) for item_id in weapon_ids + gear_ids],
        purse=CoinPurse(gp=coins),
    )
    expected_equipment = sum(equipment.get(item_id).weight_coins for item_id in weapon_ids)
    if gear_ids:
        expected_equipment += 80
    assert equipment_weight_coins(inventory) == expected_equipment
    assert tracked_weight_coins(inventory, EncumbranceMode.DETAILED) == expected_equipment + coins


class TestPurchases:
    def test_purchase_appends_lot_sized_instance_and_pays(self):
        inventory = Inventory(purse=CoinPurse(gp=30))
        bought = purchase(inventory, load_equipment().get("torch"), lots=2)
        assert bought.quantity == 12
        assert inventory.purse.gp == 28

    def test_weapons_have_no_lot_size(self):
        inventory = Inventory(purse=CoinPurse(gp=30))
        bought = purchase(inventory, load_equipment().get("dagger"), lots=2)
        assert bought.quantity == 2
        assert inventory.purse.gp == 24

    def test_insufficient_funds_is_a_structured_rejection(self):
        purse = CoinPurse(gp=5)
        rejections = validate_purchase(purse, load_equipment().get("plate_mail"))
        assert [rejection.code for rejection in rejections] == ["items.purchase.insufficient_funds"]
        assert rejections[0].params["item"] == "plate_mail"

    def test_purchase_with_insufficient_funds_raises(self):
        inventory = Inventory(purse=CoinPurse(gp=5))
        with pytest.raises(ValueError):
            purchase(inventory, load_equipment().get("plate_mail"))

    def test_nonpositive_lots_raise(self):
        with pytest.raises(ValueError):
            validate_purchase(CoinPurse(gp=100), load_equipment().get("torch"), lots=0)


class TestEquipLegality:
    def test_magic_user_in_plate_rejected(self):
        magic_user = load_classes().get("magic_user")
        rejections = validate_equip(magic_user, instance("plate_mail"))
        assert [rejection.code for rejection in rejections] == ["items.equip.armour_forbidden"]

    def test_cleric_with_sword_rejected(self):
        cleric = load_classes().get("cleric")
        rejections = validate_equip(cleric, instance("sword"))
        assert [rejection.code for rejection in rejections] == ["items.equip.weapon_not_allowed"]

    def test_cleric_with_mace_allowed(self):
        cleric = load_classes().get("cleric")
        assert validate_equip(cleric, instance("mace")) == []

    def test_thief_armour_policy(self):
        thief = load_classes().get("thief")
        assert validate_equip(thief, instance("leather")) == []
        assert [rejection.code for rejection in validate_equip(thief, instance("chainmail"))] == [
            "items.equip.armour_not_allowed"
        ]
        assert [rejection.code for rejection in validate_equip(thief, instance("shield"))] == [
            "items.equip.shield_forbidden"
        ]

    def test_dwarf_forbidden_list(self):
        dwarf = load_classes().get("dwarf")
        assert [rejection.code for rejection in validate_equip(dwarf, instance("two_handed_sword"))] == [
            "items.equip.weapon_forbidden"
        ]
        assert [rejection.code for rejection in validate_equip(dwarf, instance("long_bow"))] == [
            "items.equip.weapon_forbidden"
        ]
        assert validate_equip(dwarf, instance("battle_axe")) == []

    def test_gear_combat_facets_are_exempt_from_weapon_policy(self):
        # Pinned: a cleric may buy, hold, and use holy water, a torch, and burning
        # oil — and the rule deliberately over-grants: so may a magic-user.
        cleric = load_classes().get("cleric")
        magic_user = load_classes().get("magic_user")
        for item_id in ("holy_water", "torch", "oil_flask"):
            assert validate_equip(cleric, instance(item_id)) == []
            assert validate_equip(magic_user, instance(item_id)) == []

    def test_facetless_gear_and_ammunition_not_equippable(self):
        fighter = load_classes().get("fighter")
        assert [rejection.code for rejection in validate_equip(fighter, instance("backpack"))] == [
            "items.equip.not_equippable"
        ]
        assert [rejection.code for rejection in validate_equip(fighter, instance("arrows", 20))] == [
            "items.equip.not_equippable"
        ]


class TestEquipMechanics:
    def test_equip_moves_between_list_and_slots(self):
        fighter = load_classes().get("fighter")
        inventory = Inventory(items=[instance("sword"), instance("leather"), instance("shield")])
        sword, leather, shield = inventory.items
        equip(inventory, fighter, sword)
        equip(inventory, fighter, leather)
        equip(inventory, fighter, shield)
        assert inventory.items == []
        assert inventory.worn_armour is leather
        assert inventory.shield is shield
        assert inventory.wielded == [sword]
        unequip(inventory, leather)
        assert inventory.worn_armour is None
        assert leather in inventory.items

    def test_replacing_worn_armour_returns_the_old_suit(self):
        fighter = load_classes().get("fighter")
        inventory = Inventory(items=[instance("leather"), instance("plate_mail")])
        leather, plate = inventory.items
        equip(inventory, fighter, leather)
        equip(inventory, fighter, plate)
        assert inventory.worn_armour is plate
        assert inventory.items == [leather]

    def test_equipping_an_uncarried_instance_raises(self):
        fighter = load_classes().get("fighter")
        with pytest.raises(ValueError):
            equip(Inventory(), fighter, instance("sword"))

    def test_illegal_equip_raises(self):
        thief = load_classes().get("thief")
        inventory = Inventory(items=[instance("plate_mail")])
        with pytest.raises(ValueError):
            equip(inventory, thief, inventory.items[0])

    def test_unequip_requires_an_equipped_instance(self):
        with pytest.raises(ValueError):
            unequip(Inventory(), instance("sword"))
