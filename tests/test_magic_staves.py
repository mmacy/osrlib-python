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
from osrlib.crawl.commands import EquipItem, GrantItem, SpawnMonsters, UseItem
from osrlib.crawl.session import GameSession
from osrlib.data import load_magic_items

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
    assert [rejection.code for rejection in refused.rejections] == ["items.equip.weapon_not_allowed"]


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


# ---------------------------------------------------------------------- a staff is a staff in every other way


@pytest.mark.parametrize("staff_id", STAVES)
def test_only_the_staves_with_charges_have_them(staff_id):
    """The pages of the staff of healing and the staff of snakes both say "Does not use charges"."""
    charges = None if staff_id in ("staff_of_healing", "staff_of_snakes") else "3d10"
    assert load_magic_items().get(staff_id).charges_dice == charges


def order_of_attacks(roster, item_id: str) -> list[str]:
    """Who attacked in what order, by name, when the first member swings `item_id` and the second a sword."""
    game, first, item = armed(roster, item_id)
    second = game.party.members[1].id
    events = pf.play(game, pf.strike(game, first, weapon_id=item.instance_id), pf.strike(game, second))
    names = {member.id: member.name for member in game.party.members}
    return [names[roll.attacker_id] for roll in events if isinstance(roll, AttackRolledEvent)]


@pytest.mark.parametrize("staff_id", ["staff_of_healing", "staff_of_striking"])
def test_a_magic_staff_is_slow_so_its_swing_comes_last(staff_id):
    assert order_of_attacks((PRIEST, pf.HILD), staff_id) == ["Hild", "Ald"]


def test_an_enchanted_battle_axe_is_slow_too():
    assert order_of_attacks((pf.OSRIC, pf.HILD), "axe_plus_1") == ["Hild", "Osric"]


def carrying(roster, *item_ids: str) -> tuple[GameSession, str, list[str]]:
    """A session whose first member carries each item, with the id to name each one by in a command."""
    game = pf.session(0, roster)
    member = game.party.members[0].id
    ids = []
    for item_id in item_ids:
        before = {
            item.instance_id for item in game.member(member).inventory.items if isinstance(item, MagicItemInstance)
        }
        assert game.execute(GrantItem(character_id=member, item_id=item_id)).accepted
        new = [
            item
            for item in game.member(member).inventory.items
            if isinstance(item, MagicItemInstance) and item.instance_id not in before
        ]
        for item in new:
            item.charges_remaining = 5 if item.charges_remaining is not None else None
        ids.append(new[0].instance_id if new else item_id)
    return game, member, ids


def test_a_magic_staff_takes_both_hands():
    game, priest, (shield, staff) = carrying((PRIEST, pf.HILD), "shield", "staff_of_healing")
    assert game.execute(EquipItem(character_id=priest, item_id=shield)).accepted
    refused = game.execute(EquipItem(character_id=priest, item_id=staff))
    assert [rejection.code for rejection in refused.rejections] == ["items.equip.two_handed_with_shield"]

    game, priest, (shield, staff) = carrying((PRIEST, pf.HILD), "shield", "staff_of_healing")
    assert game.execute(EquipItem(character_id=priest, item_id=staff)).accepted
    refused = game.execute(EquipItem(character_id=priest, item_id=shield))
    assert [rejection.code for rejection in refused.rejections] == ["items.equip.two_handed_with_shield"]


def test_a_swing_identifies_the_staff():
    game, priest, staff = armed((PRIEST, pf.HILD), "staff_of_commanding")
    assert not staff.identified
    pf.play(game, pf.strike(game, priest, weapon_id=staff.instance_id))
    assert staff.identified


def test_the_strikes_damage_is_the_2d6_alone():
    """The staff of wizardry adds its +1 to the strike's attack roll and nothing to the 2d6."""
    for seed in range(200):
        game, mage, staff = armed((MAGE, pf.HILD), "staff_of_wizardry", identified=True, first_seed=seed)
        strike = pf.declare(mage, "use_item", item_id=staff.instance_id, target_group_id=pf.group_id(game))
        events = pf.play(game, strike)
        dealt = [event for event in events if event.code == "combat.damage.dealt"]
        if dealt:
            assert len(dealt[0].rolls) == 2
            assert dealt[0].amount == sum(dealt[0].rolls)
            return
    raise AssertionError("precondition: no strike hit")


# ---------------------------------------------------------------------- the other powers of two staves

OTHER_POWERS = ("staff_of_power", "staff_of_wizardry")
"""The staves that strike and also have powers osrlib leaves to the referee, like the cone of cold."""


def activations(events) -> list:
    return [event for event in events if event.code == "items.device.activated"]


@pytest.mark.parametrize("staff_id", OTHER_POWERS)
def test_a_use_outside_a_battle_spends_a_charge_and_shows_the_staffs_text(staff_id):
    game, mage, (staff_instance,) = carrying((MAGE, pf.HILD), staff_id)
    staff = game.member(mage).inventory.magic_item(staff_instance)
    used = game.execute(UseItem(character_id=mage, item_id=staff_instance))
    assert used.accepted, [rejection.code for rejection in used.rejections]
    (activated,) = activations(used.events)
    assert activated.manual == load_magic_items().get(staff_id).manual
    assert staff.charges_remaining == 4


def test_the_staff_of_striking_has_no_use_outside_a_battle():
    game, priest, (staff_instance,) = carrying((PRIEST, pf.HILD), "staff_of_striking")
    refused = game.execute(UseItem(character_id=priest, item_id=staff_instance))
    assert [rejection.code for rejection in refused.rejections] == ["items.use.battle_only"]
    assert game.member(priest).inventory.magic_item(staff_instance).charges_remaining == 5


@pytest.mark.parametrize("staff_id", OTHER_POWERS)
def test_a_use_in_a_battle_that_names_no_group_is_one_of_the_other_powers(staff_id):
    game, mage, staff = armed((MAGE, pf.HILD), staff_id, identified=True)
    events = pf.play(game, pf.declare(mage, "use_item", item_id=staff.instance_id))
    assert pf.attacks_by(events, mage) == []
    (activated,) = activations(events)
    assert activated.manual == load_magic_items().get(staff_id).manual
    assert staff.charges_remaining == 4


def test_a_strike_shows_no_staff_text():
    game, mage, staff = armed((MAGE, pf.HILD), "staff_of_power", identified=True)
    events = pf.play(game, pf.declare(mage, "use_item", item_id=staff.instance_id, target_group_id=pf.group_id(game)))
    (activated,) = activations(events)
    assert activated.manual == ()


def test_the_staff_of_striking_needs_a_group_to_strike():
    game, priest, staff = armed((PRIEST, pf.HILD), "staff_of_striking", identified=True)
    refused = pf.round_(game, pf.declare(priest, "use_item", item_id=staff.instance_id))
    assert [rejection.code for rejection in refused.rejections] == ["battle.declaration.unknown_group"]
    assert staff.charges_remaining == 5


# ---------------------------------------------------------------------- the strike is a blow with a slow weapon

SLOW_STRIKE = pytest.mark.xfail(reason="chunk: review-fixes-two")
"""The staff of striking is "Used as a magical melee weapon", and a staff is slow, so its strike comes last."""


@SLOW_STRIKE
@pytest.mark.parametrize(
    ("roster", "staff_id"), [((PRIEST, pf.HILD), "staff_of_striking"), ((MAGE, pf.HILD), "staff_of_power")]
)
def test_a_strike_comes_after_a_quicker_members_attack(roster, staff_id):
    game, wielder, staff = armed(roster, staff_id, identified=True)
    hild = game.party.members[1].id
    strike = pf.declare(wielder, "use_item", item_id=staff.instance_id, target_group_id=pf.group_id(game))
    events = pf.play(game, strike, pf.strike(game, hild))
    assert [roll.attacker_id for roll in events if isinstance(roll, AttackRolledEvent)] == [hild, wielder]


@SLOW_STRIKE
def test_a_strike_comes_after_the_monsters_attacks():
    """Whoever wins initiative, in every one of the first ten battles in which the goblins attack."""
    checked = 0
    seed = 0
    while checked < 10:
        game, priest, staff = armed((PRIEST, pf.HILD), "staff_of_striking", identified=True, first_seed=seed)
        seed = game.master_seed + 1
        pf.release_monsters(game)
        strike = pf.declare(priest, "use_item", item_id=staff.instance_id, target_group_id=pf.group_id(game))
        rolls = [roll for roll in pf.play(game, strike) if isinstance(roll, AttackRolledEvent)]
        if len(rolls) > 1:
            assert rolls[-1].attacker_id == priest, f"seed {game.master_seed}"
            checked += 1
