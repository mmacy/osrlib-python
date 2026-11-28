"""Acceptance tests for the magic weapon a generated NPC adventurer rolls (issue 305).

The contract these tests check:

- The SRD's Adventuring Parties page says "Rolled items that cannot be used by the NPC should be ignored (no re-roll)".
  That's about the member's class. A rolled item the class can use is kept, and is worn, wielded, or carried.
- A rolled magic weapon is compared with the first wielded weapon that makes the same kind of attack, melee or
  missile. It beats a mundane weapon, and it beats an enchanted one with a higher attack bonus, or with an equal
  bonus and a higher average damage. When it beats that weapon, it takes the weapon's place, and the weapon it
  replaced is carried. Otherwise the rolled weapon is carried. A wielded weapon of the other kind stays wielded.
- The rolled weapon goes on only where a player's would. A two-handed weapon stays in the pack of a member who wears
  a shield. A one-handed weapon that replaces a two-handed one lets the member take up the shield they carry.
- In a battle, an NPC attacks with an enchanted weapon over a mundane one that makes the same attack, cursed swords
  too. The OSE SRD's Swords page says a character under a cursed sword's curse "will favour the sword and use it
  whenever possible". The players never identify an NPC's items, so the attack event names an unidentified magic
  weapon by the mundane weapon underneath.
- An NPC knows no more than a player does, so a cursed sword whose curse it hasn't discovered is compared as the sword
  +1 it tests as, the reading the cursed shields and suits of armour take. In a battle it still fights at its real
  penalty.
- Generation draws what it drew before: the same members and the same treasure, from the same seeds.
"""

import pytest

import osrlib.core.npc as npc_module
from osrlib.core.abilities import AbilityScore
from osrlib.core.alignment import Alignment
from osrlib.core.character import Character
from osrlib.core.events import AttackRolledEvent
from osrlib.core.items import ItemInstance, MagicItemInstance
from osrlib.core.monsters import IdAllocator
from osrlib.core.npc import NPC_PARTY_STREAM, _grant_kit, _item_usable, _take_rolled_item, generate_npc_party
from osrlib.core.rng import RngStream
from osrlib.core.treasure import TREASURE_STREAM
from osrlib.crawl.battle import _npc_wielded
from osrlib.crawl.commands import BattleDeclaration, ResolveBattleRound, SpawnNpcParty
from osrlib.data import load_classes, load_equipment
from positions_fixtures import HILD, _opened, named
from positions_fixtures import session as dungeon_session


def _name(instance) -> str | None:
    if instance is None:
        return None
    return instance.template_id if isinstance(instance, MagicItemInstance) else instance.template.id


def _adventurer(class_id: str, kind: str = "basic") -> Character:
    """A level 1 NPC of `class_id` with scores of 10 and the `kind` kit, worn and wielded as generation leaves it."""
    definition = load_classes().get(class_id)
    member = Character(
        id="npc-0001",
        name=f"{definition.name} adventurer 1",
        class_id=class_id,
        race=definition.race,
        level=1,
        xp=0,
        scores={ability: 10 for ability in AbilityScore},
        alignment=Alignment.NEUTRAL,
        max_hp=8,
        current_hp=8,
    )
    _grant_kit(member, definition, kind)
    return member


def _rolls(member: Character, template_id: str, base: str | None, index: int = 1) -> MagicItemInstance:
    """Hand `member` a rolled item through the path generation takes, which keeps it when the class can use it."""
    instance = MagicItemInstance(instance_id=f"magic-item-{index:04d}", template_id=template_id, base_item_id=base)
    _take_rolled_item(member, member.definition, instance)
    return instance


def _possessions(member: Character) -> list[int]:
    return sorted(id(instance) for instance in member.inventory.all_instances())


CHOICES = [
    # A fighter's sword gives way to the sword +1.
    pytest.param("fighter", "sword_plus_1", "sword", ["sword_plus_1"], "shield", ["sword"], id="fighter_sword"),
    # A cursed sword is an enchanted sword, so it goes on the same way.
    pytest.param(
        "fighter", "sword_minus_1_cursed", "sword", ["sword_minus_1_cursed"], "shield", ["sword"], id="cursed_sword"
    ),
    # The halfling keeps the sling beside the sword +2, in the sword's place.
    pytest.param("halfling", "sword_plus_2", "sword", ["sword_plus_2", "sling"], "shield", ["sword"], id="halfling"),
    # A sling +1 replaces the sling and leaves the sword.
    pytest.param("halfling", "sling_plus_1", "sling", ["sword", "sling_plus_1"], "shield", ["sling"], id="sling"),
    # An elf's bow +1 replaces the long bow.
    pytest.param("elf", "bow_plus_1", "long_bow", ["sword", "bow_plus_1"], None, ["long_bow"], id="elf_bow"),
    # A dagger +1 throws and stabs, and replaces the first weapon that makes either attack, the sword.
    pytest.param("thief", "dagger_plus_1", "dagger", ["dagger_plus_1"], None, ["sword"], id="thief_dagger"),
    # A cleric's mace gives way to a war hammer +1, another melee weapon.
    pytest.param("cleric", "war_hammer_plus_1", "war_hammer", ["war_hammer_plus_1"], "shield", ["mace"], id="cleric"),
    # A battle axe is two-handed, so a fighter with a shield keeps the sword and carries the axe +1.
    pytest.param("fighter", "axe_plus_1", "battle_axe", ["sword"], "shield", ["axe_plus_1"], id="two_handed_waits"),
    # A long bow is two-handed too, so the fighter carries the bow +1.
    pytest.param("fighter", "bow_plus_1", "long_bow", ["sword"], "shield", ["bow_plus_1"], id="bow_waits"),
    # The dwarf's sword +1 replaces the two-handed battle axe, so the dwarf takes up the shield the kit carries.
    pytest.param("dwarf", "sword_plus_1", "sword", ["sword_plus_1"], "shield", ["battle_axe"], id="dwarf_shield"),
    # A dwarf's axe +1 replaces the battle axe, and the shield stays in the pack.
    pytest.param("dwarf", "axe_plus_1", "battle_axe", ["axe_plus_1"], None, ["battle_axe", "shield"], id="dwarf_axe"),
]


@pytest.mark.parametrize(("class_id", "template_id", "base", "wielded", "shield", "carried"), CHOICES)
def test_a_rolled_magic_weapon_takes_the_place_of_the_kit_weapon_it_beats(
    class_id, template_id, base, wielded, shield, carried
):
    member = _adventurer(class_id)
    before = _possessions(member)

    instance = _rolls(member, template_id, base)

    inventory = member.inventory
    assert [_name(existing) for existing in inventory.wielded] == wielded
    assert _name(inventory.shield) == shield
    pack = [_name(existing) for existing in inventory.items]
    assert all(name in pack for name in carried), pack
    assert _possessions(member) == sorted([*before, id(instance)]), "the member owns the same things, and the roll"


def test_a_dwarf_with_a_shield_keeps_a_later_two_handed_axe():
    """Swords roll before weapons. The sword +1 frees the dwarf's hand for the shield, and the axe +1 is carried."""
    member = _adventurer("dwarf")
    _rolls(member, "sword_plus_1", "sword", index=1)
    axe = _rolls(member, "axe_plus_1", "battle_axe", index=2)

    assert [_name(existing) for existing in member.inventory.wielded] == ["sword_plus_1"]
    assert _name(member.inventory.shield) == "shield"
    assert any(existing is axe for existing in member.inventory.items)


@pytest.mark.parametrize(
    ("class_id", "first", "second", "wielded", "carried"),
    [
        # The mace +2's attack bonus is higher.
        pytest.param(
            "cleric", ("mace_plus_1", "mace"), ("mace_plus_2", "mace"), "mace_plus_2", "mace_plus_1", id="bonus"
        ),
        # A war hammer +1 and a sword +1 have the same bonus. The sword's 1d8 averages 4.5, the hammer's 1d6 3.5.
        pytest.param(
            "fighter",
            ("war_hammer_plus_1", "war_hammer"),
            ("sword_plus_1", "sword"),
            "sword_plus_1",
            "war_hammer_plus_1",
            id="damage",
        ),
        # The staff of wizardry swings a 1d4 staff at +1, and the sword +1 a 1d8 sword, so the sword replaces it.
        pytest.param(
            "elf",
            ("staff_of_wizardry", "staff"),
            ("sword_plus_1", "sword"),
            "sword_plus_1",
            "staff_of_wizardry",
            id="staff",
        ),
        # The mace +2 has the sword +2's bonus and a smaller die, so the sword stays.
        pytest.param(
            "fighter",
            ("sword_plus_2", "sword"),
            ("mace_plus_2", "mace"),
            "sword_plus_2",
            "mace_plus_2",
            id="equal_loses",
        ),
        # The undiscovered cursed sword -1 tests as a sword +1. The dagger +1's bonus is the same, and its 1d4
        # averages 2.5 to the sword's 4.5, so the cursed sword keeps its place.
        pytest.param(
            "thief",
            ("sword_minus_1_cursed", "sword"),
            ("dagger_plus_1", "dagger"),
            "sword_minus_1_cursed",
            "dagger_plus_1",
            id="cursed_keeps_its_place",
        ),
        # A war hammer +1 gives way to a rolled cursed sword -2, which tests as a sword +1 with the larger die.
        pytest.param(
            "fighter",
            ("war_hammer_plus_1", "war_hammer"),
            ("sword_minus_2_cursed", "sword"),
            "sword_minus_2_cursed",
            "war_hammer_plus_1",
            id="cursed_replaces_enchanted",
        ),
    ],
)
def test_a_rolled_weapon_replaces_a_weaker_enchanted_one(class_id, first, second, wielded, carried):
    member = _adventurer(class_id)
    _rolls(member, *first, index=1)
    _rolls(member, *second, index=2)

    assert wielded in [_name(existing) for existing in member.inventory.wielded]
    assert carried in [_name(existing) for existing in member.inventory.items]


def test_an_enchanted_missile_weapon_goes_beside_an_enchanted_sword():
    """The sword +1 holds the melee place, and the bow +1 still beats the mundane long bow."""
    member = _adventurer("elf")
    _rolls(member, "sword_plus_1", "sword", index=1)
    _rolls(member, "bow_plus_1", "long_bow", index=2)

    assert [_name(existing) for existing in member.inventory.wielded] == ["sword_plus_1", "bow_plus_1"]


def test_every_rolled_item_the_class_can_use_is_kept(monkeypatch):
    """Across seeds, each item a member rolls is in their inventory exactly once when the class can use it.

    An item the class can't use is ignored, as the SRD says, and isn't anywhere in the inventory. So no member owns
    less than the rolls give them, whatever they wield.
    """
    rolled: list[tuple[Character, MagicItemInstance]] = []
    current: dict[str, Character] = {}
    roll_items = npc_module._roll_expert_items
    roll_magic = npc_module.generate_magic_item

    def spy_member(member, *args, **kwargs):
        current["member"] = member
        return roll_items(member, *args, **kwargs)

    def spy_items(*args, **kwargs):
        instances = roll_magic(*args, **kwargs)
        rolled.extend((current["member"], instance) for instance in instances)
        return instances

    monkeypatch.setattr(npc_module, "_roll_expert_items", spy_member)
    monkeypatch.setattr(npc_module, "generate_magic_item", spy_items)
    for seed in range(120):
        generate_npc_party(
            "expert",
            count=9,
            npc_stream=RngStream.from_seed_material(seed, NPC_PARTY_STREAM),
            treasure_stream=RngStream.from_seed_material(seed, TREASURE_STREAM),
            allocator=IdAllocator(),
        )
    assert any(_name(instance) in ("axe_plus_1", "bow_plus_1") for _, instance in rolled), "precondition"
    for member, instance in rolled:
        places = sum(1 for existing in member.inventory.all_instances() if existing is instance)
        assert places == (1 if _item_usable(member.definition, instance) else 0), (member.id, _name(instance))


# ---------------------------------------------------------------------- the battle's choice


def _wielding(*names: str) -> Character:
    """A fighter who wields each item in `names` in order. A name with `plus` or `minus` in it is a magic sword."""
    member = _adventurer("fighter")
    inventory = member.inventory
    inventory.items.extend(inventory.wielded)
    inventory.wielded.clear()
    equipment = load_equipment()
    for index, item_name in enumerate(names, start=1):
        if "plus" in item_name or "minus" in item_name or item_name.startswith("wand"):
            base = "sword" if item_name.startswith("sword") else None
            inventory.wielded.append(
                MagicItemInstance(instance_id=f"magic-item-{index:04d}", template_id=item_name, base_item_id=base)
            )
        else:
            inventory.wielded.append(ItemInstance(template=equipment.get(item_name)))
    return member


@pytest.mark.parametrize(
    ("wielded", "missile", "chosen"),
    [
        pytest.param(("sword", "sword_plus_1"), False, "sword_plus_1", id="magic_over_mundane"),
        pytest.param(("sword", "sword_minus_2_cursed"), False, "sword_minus_2_cursed", id="cursed_favoured"),
        pytest.param(("sword", "sling", "sword_plus_1"), True, "sling", id="missile_ignores_the_sword"),
        pytest.param(("sword", "mace"), False, "sword", id="first_mundane"),
        pytest.param(("wand_of_cold", "sword"), False, "sword", id="wand_is_no_weapon"),
    ],
)
def test_a_battle_picks_the_enchanted_weapon_that_makes_the_attack(wielded, missile, chosen):
    member = _wielding(*wielded)

    attack = _npc_wielded(member, missile=missile)

    assert (attack.template_id if isinstance(attack, MagicItemInstance) else attack.id) == chosen


def _expert_alone():
    """The issue's repro: one expert NPC who rolled a sword +1, 5' from Hild, in a battle at round 0."""
    game = dungeon_session(4, (HILD,))
    result = game.execute(SpawnNpcParty(party_kind="expert", count_fixed=1, distance_feet=5))
    (npc,) = game.npcs.values()
    assert _opened(game, result, surprised=False, engage=True)
    return game, npc


def _npc_attacks(game, npc, rounds: int = 3) -> list[AttackRolledEvent]:
    hild = named(game, "Hild")
    rolls = []
    for _ in range(rounds):
        out = game.execute(ResolveBattleRound(declarations=(BattleDeclaration(character_id=hild, action="hold"),)))
        rolls.extend(e for e in out.events if isinstance(e, AttackRolledEvent) and e.attacker_id == npc.id)
    return rolls


def test_the_issues_npc_swings_the_sword_plus_1():
    """The NPC's melee modifier is -2 from STR. With the sword +1 the attack takes -1.

    The sword +1 is unidentified, so the player-visible attack event names it "Sword".
    """
    game, npc = _expert_alone()
    assert [_name(existing) for existing in npc.inventory.wielded] == ["sword_plus_1"]
    assert "sword" in [_name(existing) for existing in npc.inventory.items], "the kit sword is carried"
    assert npc.melee_modifier == -2

    rolls = _npc_attacks(game, npc)

    assert rolls
    assert {(roll.attack_name, roll.modifier) for roll in rolls} == {("Sword", -1)}


def test_an_npc_that_wields_both_swords_swings_the_magic_one():
    """A save made before the fix has the kit sword first in the wielded list. The NPC still swings the sword +1."""
    game, npc = _expert_alone()
    inventory = npc.inventory
    (sword,) = [existing for existing in inventory.items if _name(existing) == "sword"]
    inventory.items.remove(sword)
    inventory.wielded.insert(0, sword)

    rolls = _npc_attacks(game, npc)

    assert rolls
    assert {(roll.attack_name, roll.modifier) for roll in rolls} == {("Sword", -1)}


def test_an_npcs_cursed_sword_attacks_as_a_sword():
    """The attack event shows neither the bonus nor the curse of a weapon the players haven't identified."""
    game, npc = _expert_alone()
    inventory = npc.inventory
    inventory.items.extend(inventory.wielded)
    inventory.wielded[:] = [
        MagicItemInstance(instance_id="magic-item-0900", template_id="sword_minus_1_cursed", base_item_id="sword")
    ]

    rolls = _npc_attacks(game, npc)

    assert rolls
    assert {(roll.attack_name, roll.modifier) for roll in rolls} == {("Sword", -3)}


def test_an_identified_magic_weapon_keeps_its_name():
    game, npc = _expert_alone()
    (sword,) = npc.inventory.wielded
    assert isinstance(sword, MagicItemInstance)
    sword.identified = True

    rolls = _npc_attacks(game, npc)

    assert rolls
    assert {roll.attack_name for roll in rolls} == {"Sword +1"}


# ---------------------------------------------------------------------- magic staves (issue 257)

STAVES = [
    # The Rods, Staves, Wands page: "magic-users ... are able to use the staff of power, staff of striking, and staff
    # of wizardry in melee". The staff of wizardry swings at +1, so it replaces the dagger.
    pytest.param("magic_user", "staff_of_wizardry", ["staff_of_wizardry"], ["dagger"], id="mu_wizardry"),
    # The staff of power swings as a mundane staff, with no bonus, so the magic-user keeps the dagger.
    pytest.param("magic_user", "staff_of_power", ["dagger"], ["staff_of_power"], id="mu_power"),
    # An elf may wield any weapon, and the staff of wizardry replaces the sword. The long bow stays.
    pytest.param("elf", "staff_of_wizardry", ["staff_of_wizardry", "long_bow"], ["sword"], id="elf_wizardry"),
    # The staff of snakes swings at +1, but it's two-handed, and a cleric wears a shield.
    pytest.param("cleric", "staff_of_snakes", ["mace"], ["staff_of_snakes"], id="cleric_shield"),
    # The staff of healing swings as a mundane staff.
    pytest.param("cleric", "staff_of_healing", ["mace"], ["staff_of_healing"], id="cleric_healing"),
]


@pytest.mark.parametrize(("class_id", "template_id", "wielded", "carried"), STAVES)
def test_an_npc_wields_a_rolled_staff_whose_swing_takes_a_bonus(class_id, template_id, wielded, carried):
    member = _adventurer(class_id)

    _rolls(member, template_id, "staff")

    assert [_name(existing) for existing in member.inventory.wielded] == wielded
    pack = [_name(existing) for existing in member.inventory.items]
    assert all(name in pack for name in carried), pack


def test_an_npc_swings_the_staff_of_wizardry_at_plus_1():
    member = _adventurer("magic_user")
    _rolls(member, "staff_of_wizardry", "staff")

    attack = _npc_wielded(member, missile=False)

    assert isinstance(attack, MagicItemInstance) and attack.template_id == "staff_of_wizardry"
