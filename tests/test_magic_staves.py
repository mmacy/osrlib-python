"""Magic staves are staves (issue #254).

The SRD's Rods, Staves, Wands page says a magic-user can "use the *staff of power*, *staff of
striking*, and *staff of wizardry* in melee". The staff of wizardry and the staff of snakes are
"Treated as a staff with a +1 magic bonus", and the staves of power and wizardry inflict 2d6 damage for
a charge, as the staff of striking does. Every magic staff is 6' long and 2" thick, so each one can be
swung as a staff, and an unidentified staff doesn't give itself away by whether it can attack.
"""

import pytest

import positions_fixtures as pf
from osrlib.core.events import AttackRolledEvent
from osrlib.core.items import MagicItemInstance
from osrlib.crawl.commands import EquipItem, GrantItem, SpawnMonsters
from osrlib.crawl.session import GameSession
from osrlib.data import load_magic_items

pytestmark = pytest.mark.xfail(reason="chunk: magic-staves")

STAVES = (
    "staff_of_commanding",
    "staff_of_healing",
    "staff_of_power",
    "staff_of_snakes",
    "staff_of_striking",
    "staff_of_withering",
    "staff_of_wizardry",
)
STRIKING = ("staff_of_power", "staff_of_striking", "staff_of_wizardry")
"""The staves that inflict 2d6 damage for a charge, and that a magic-user can use in melee."""
PLUS_ONE = ("staff_of_snakes", "staff_of_wizardry")

PRIEST = ("Ald", "cleric")
MAGE = ("Ysolde", "magic_user")


def test_the_catalog_has_these_staves():
    staves = sorted(template.id for template in load_magic_items().items if template.id.startswith("staff_of_"))
    assert staves == sorted(STAVES)


@pytest.mark.parametrize("staff_id", STAVES)
def test_every_magic_staff_is_a_staff(staff_id):
    assert load_magic_items().get(staff_id).base_item_id == "staff"


@pytest.mark.parametrize("staff_id", STAVES)
def test_only_the_staves_the_srd_names_have_a_bonus(staff_id):
    template = load_magic_items().get(staff_id)
    bonus = 1 if staff_id in PLUS_ONE else 0
    assert (template.attack_bonus, template.damage_bonus) == (bonus, bonus)


@pytest.mark.parametrize("staff_id", STRIKING)
def test_three_staves_strike_for_2d6_at_the_cost_of_a_charge(staff_id):
    effect = load_magic_items().get(staff_id).effect
    assert effect is not None
    assert (effect.kind, effect.damage_dice) == ("striking", "2d6")


def armed(
    roster, staff_id: str, *, identified: bool = False, first_seed: int = 0
) -> tuple[GameSession, str, MagicItemInstance]:
    """A battle with two goblins in reach, and the first member wielding the staff."""
    for seed in range(first_seed, 200):
        game = pf.session(seed, roster)
        wielder = game.party.members[0].id
        assert game.execute(GrantItem(character_id=wielder, item_id=staff_id)).accepted
        staff = next(item for item in game.member(wielder).inventory.items if isinstance(item, MagicItemInstance))
        staff.identified = identified
        staff.charges_remaining = 5
        equipped = game.execute(EquipItem(character_id=wielder, item_id=staff.instance_id))
        assert equipped.accepted, [rejection.code for rejection in equipped.rejections]
        if pf._opened(
            game,
            game.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=5)),
            surprised=False,
            engage=True,
        ):
            pf.hold_monsters(game)
            return game, wielder, staff
    raise AssertionError("no seed opened the battle")


@pytest.mark.parametrize("staff_id", STAVES)
def test_a_cleric_swings_any_staff_without_knowing_what_it_is(staff_id):
    game, priest, staff = armed((PRIEST, pf.HILD), staff_id)
    events = pf.play(game, pf.strike(game, priest, weapon_id=staff.instance_id))
    assert [roll.attacker_id for roll in pf.attacks_by(events, priest)] == [priest]


@pytest.mark.parametrize("staff_id", STRIKING)
def test_a_magic_user_wields_the_three_staves_the_srd_names(staff_id):
    game, mage, staff = armed((MAGE, pf.HILD), staff_id)
    events = pf.play(game, pf.strike(game, mage, weapon_id=staff.instance_id))
    assert [roll.attacker_id for roll in pf.attacks_by(events, mage)] == [mage]


@pytest.mark.parametrize("item_id", ["staff", "staff_of_healing", "staff_of_snakes"])
def test_a_magic_user_still_cant_wield_any_other_staff(item_id):
    game = pf.session(0, (MAGE, pf.HILD))
    mage = game.party.members[0].id
    assert game.execute(GrantItem(character_id=mage, item_id=item_id)).accepted
    magic = [item for item in game.member(mage).inventory.items if isinstance(item, MagicItemInstance)]
    refused = game.execute(EquipItem(character_id=mage, item_id=magic[0].instance_id if magic else item_id))
    assert [rejection.code for rejection in refused.rejections] == ["items.equip.weapon_forbidden"]


@pytest.mark.parametrize("staff_id", STRIKING)
def test_a_striking_staff_spends_a_charge_for_its_2d6(staff_id):
    game, mage, staff = armed((MAGE, pf.HILD), staff_id, identified=True)
    events = pf.play(game, pf.declare(mage, "use_item", item_id=staff.instance_id, target_group_id=pf.group_id(game)))
    assert [roll.attacker_id for roll in pf.attacks_by(events, mage)] == [mage]
    assert staff.charges_remaining == 4


def modifier_with(staff_id: str) -> int:
    game, priest, staff = armed((PRIEST, pf.HILD), staff_id)
    events = pf.play(game, pf.strike(game, priest, weapon_id=staff.instance_id))
    roll = next(event for event in events if isinstance(event, AttackRolledEvent) and event.attacker_id == priest)
    return roll.modifier


def test_the_plus_one_staves_add_one_to_the_attack_roll():
    plain = modifier_with("staff_of_healing")
    assert modifier_with("staff_of_power") == plain
    assert modifier_with("staff_of_wizardry") == plain + 1
    assert modifier_with("staff_of_snakes") == plain + 1
