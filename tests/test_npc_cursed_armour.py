"""Acceptance tests for the cursed shields and suits of armour a generated NPC adventurer rolls (issue 298).

The OSE SRD's Armour and Shields page says: "All types of tests indicate that a cursed shield or suit of armour is
enchanted with a +1 bonus. It is only when the armour is used in deadly combat that the curse is revealed."

The contract these tests check:

- An NPC knows no more than a player does. When a generated NPC weighs a rolled shield or suit against what it
  wears, it counts an unidentified cursed one, whose curse isn't revealed, as the +1 item it tests as: its base item
  with a +1 enchantment. So it wears a cursed shield or suit exactly when it would wear that +1 item, and leaves it in
  the pack exactly when it would leave the +1 item there.
- The rule has one home in `osrlib.core`, the private `_armour_as_tested` in `osrlib.core.character`, which the player
  view reads too. Every test in `tests/test_unrevealed_curse_view.py` keeps passing unedited.
- The NPC fights with its real armour class. The monsters' and the party's attack rolls read the real number, and the
  players' attacks reveal nothing about the item, because identifying an item is what the party learns and the item
  isn't theirs. Its curse stays hidden on the NPC, so it drops as loot still unidentified.
- The fix changes which items some generated NPCs wear and nothing else: the same draws, the same members, the same
  treasure.

Printed armour classes, from the OSE SRD's Armour and Shields page: unarmoured 9 [10], leather 7 [12], chainmail 5
[14], plate mail 3 [16], and a shield +1. A suit adds its enchantment to its base item, a shield adds its enchantment
to the plain shield's +1, and the cursed AC 9 [10] forms set the base outright. The DEX modifier counts on top, from
the OSE SRD's ability score table: 3 gives +3 [-3], 4 to 5 +2 [-2], 6 to 8 +1 [-1], 9 to 12 none, 13 to 15 -1 [+1],
16 to 17 -2 [+2], and 18 -3 [+3].
"""

import json

import pytest

from osrlib.core.abilities import AbilityScore
from osrlib.core.alignment import Alignment
from osrlib.core.character import Character, _armour_as_tested
from osrlib.core.events import AttackRolledEvent, Visibility
from osrlib.core.items import Inventory, MagicItemInstance
from osrlib.core.monsters import IdAllocator
from osrlib.core.npc import NPC_PARTY_STREAM, _grant_kit, _maybe_equip_upgrade, generate_npc_party
from osrlib.core.rng import RngStream
from osrlib.core.treasure import TREASURE_STREAM
from osrlib.crawl.commands import BattleDeclaration, ResolveBattleRound, SpawnNpcParty
from osrlib.crawl.events import CurseRevealedEvent, ItemIdentifiedEvent
from osrlib.crawl.views import PlayerView, RefereeView
from osrlib.data import load_classes
from positions_fixtures import HILD, _opened, named
from positions_fixtures import session as dungeon_session

LOOKALIKE = {
    "cursed_shield_minus_2": "shield_plus_1",
    "cursed_shield_ac_9": "shield_plus_1",
    "cursed_armour_minus_1": "armour_plus_1",
    "cursed_armour_minus_2": "armour_plus_1",
    "cursed_armour_ac_9": "armour_plus_1",
}
"""Each cursed template beside the +1 item every test shows it as."""


def _slot(instance: MagicItemInstance) -> str:
    return "shield" if instance.base_item_id == "shield" else "worn_armour"


def _name(instance) -> str | None:
    if instance is None:
        return None
    return instance.template_id if isinstance(instance, MagicItemInstance) else instance.template.id


# ---------------------------------------------------------------------- the kernel rule


def _wearing(*worn: tuple[str, str | None], dex: int = 10) -> Character:
    """A fighter with DEX `dex` who wears each `(template_id, base_item_id)`, all unidentified with curses hidden."""
    member = Character(
        id="character-0001",
        name="Tester",
        class_id="fighter",
        race="human",
        level=1,
        xp=0,
        scores={ability: (dex if ability is AbilityScore.DEX else 10) for ability in AbilityScore},
        alignment=Alignment.NEUTRAL,
        max_hp=8,
        current_hp=8,
    )
    slots = {}
    for index, (template_id, base) in enumerate(worn, start=1):
        instance = MagicItemInstance(instance_id=f"magic-item-{index:04d}", template_id=template_id, base_item_id=base)
        slots[_slot(instance)] = instance
    member.inventory = Inventory(**slots)
    return member


TESTED = [
    # A shield +1 gives 2: 9 - 2 = 7 [10 + 2 = 12]. Real -2: 9 - (1 - 2) = 10 [9].
    pytest.param(("cursed_shield_minus_2", "shield"), (7, 12), (10, 9), id="shield_minus_2"),
    # Real: the shield sets 9 [10] and adds nothing.
    pytest.param(("cursed_shield_ac_9", "shield"), (7, 12), (9, 10), id="shield_ac_9"),
    # Plate +1: 3 - 1 = 2 [17]. Real -1: 4 [15].
    pytest.param(("cursed_armour_minus_1", "plate_mail"), (2, 17), (4, 15), id="armour_minus_1_plate"),
    # Leather +1: 6 [13]. Real -2: 9 [10].
    pytest.param(("cursed_armour_minus_2", "leather"), (6, 13), (9, 10), id="armour_minus_2_leather"),
    # Chainmail +1: 4 [15]. Real: 9 [10] outright.
    pytest.param(("cursed_armour_ac_9", "chainmail"), (4, 15), (9, 10), id="armour_ac_9_chainmail"),
]


@pytest.mark.parametrize(("worn", "tested", "real"), TESTED)
def test_the_kernel_counts_an_unrevealed_cursed_item_as_its_plus_1_lookalike(worn, tested, real):
    member = _wearing(worn)
    before = member.model_dump(mode="json")

    shown = _armour_as_tested(member)

    assert (shown.armour_class, shown.armour_class_ascending) == tested
    assert (member.armour_class, member.armour_class_ascending) == real, "the member keeps the real armour class"
    assert member.model_dump(mode="json") == before, "the member never changes"


def test_the_kernel_masks_only_the_cursed_item_and_keeps_the_dex_modifier():
    """The SRD's row "Cursed Armour -2 with Shield +1", in chainmail, on a fighter with DEX 16, -2 [+2].

    Tested: chainmail +1 is 4, the shield +1 gives 2, and DEX 2 more, so 0 [19]. Real: chainmail -2 is 7, so 3 [16].
    """
    member = _wearing(("cursed_armour_minus_2", "chainmail"), ("shield_plus_1", "shield"), dex=16)

    shown = _armour_as_tested(member)

    assert (shown.armour_class, shown.armour_class_ascending) == (0, 19)
    assert (member.armour_class, member.armour_class_ascending) == (3, 16)


@pytest.mark.parametrize("flag", ["identified", "cursed_revealed"])
def test_the_kernel_shows_the_real_number_once_the_item_is_known(flag):
    """Identified, or with its curse revealed, a cursed shield -2 tests as what it is: 10 [9]."""
    member = _wearing(("cursed_shield_minus_2", "shield"))
    assert member.inventory.shield is not None
    setattr(member.inventory.shield, flag, True)

    shown = _armour_as_tested(member)

    assert (shown.armour_class, shown.armour_class_ascending) == (10, 9)


def test_the_kernel_returns_the_member_itself_with_nothing_to_mask():
    member = _wearing(("armour_plus_1", "chainmail"), ("cursed_shield_minus_2", "shield"))
    assert member.inventory.shield is not None
    member.inventory.shield.identified = True

    assert _armour_as_tested(member) is member


# ---------------------------------------------------------------------- the equipment choice, item by item


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


def _rolls(member: Character, template_id: str, base: str) -> MagicItemInstance:
    """Hand `member` a rolled item, as generation does, and let them choose whether to wear it."""
    instance = MagicItemInstance(instance_id="magic-item-0001", template_id=template_id, base_item_id=base)
    member.inventory.items.append(instance)
    _maybe_equip_upgrade(member, member.definition, instance)
    return instance


def _outcome(class_id: str, kind: str, template_id: str, base: str, shield_plus_2: bool) -> tuple:
    """Whether the rolled item went on, the member's real armour class, and what they wear in each slot."""
    member = _adventurer(class_id, kind)
    if shield_plus_2:
        worn = MagicItemInstance(instance_id="magic-item-0009", template_id="shield_plus_2", base_item_id="shield")
        member.inventory.items.append(worn)
        _maybe_equip_upgrade(member, member.definition, worn)
        assert member.inventory.shield is worn, "precondition: the fighter wears a shield +2"
    instance = _rolls(member, template_id, base)
    worn_it = instance is getattr(member.inventory, _slot(instance))
    in_pack = any(existing is instance for existing in member.inventory.items)
    assert worn_it != in_pack, "the item is in exactly one place"
    inventory = member.inventory
    return (
        worn_it,
        (member.armour_class, member.armour_class_ascending),
        _name(inventory.worn_armour),
        _name(inventory.shield),
    )


CHOICES = [
    # A basic fighter wears chainmail and a shield: 5 - 1 = 4 [15]. Each lookalike tests as 3 [16], an upgrade.
    # Real -2 shield: 5 - (1 - 2) = 6 [13].
    pytest.param("fighter", "basic", "cursed_shield_minus_2", "shield", False, True, (6, 13), id="sh-2"),
    # Real: the shield sets 9 [10].
    pytest.param("fighter", "basic", "cursed_shield_ac_9", "shield", False, True, (9, 10), id="sh9"),
    # Chainmail -1 is 6, less the shield: 5 [14].
    pytest.param("fighter", "basic", "cursed_armour_minus_1", "chainmail", False, True, (5, 14), id="a-1"),
    # Chainmail -2 is 7, less the shield: 6 [13].
    pytest.param("fighter", "basic", "cursed_armour_minus_2", "chainmail", False, True, (6, 13), id="a-2"),
    # The suit sets 9, less the shield: 8 [11].
    pytest.param("fighter", "basic", "cursed_armour_ac_9", "chainmail", False, True, (8, 11), id="a9"),
    # A thief wears leather, 7 [12]. Leather +1 tests as 6 [13], an upgrade. Real leather -1: 8 [11].
    pytest.param("thief", "basic", "cursed_armour_minus_1", "leather", False, True, (8, 11), id="thief"),
    # An expert fighter wears plate and a shield, 2 [17]. Chainmail +1 tests as 4 - 1 = 3 [16], no upgrade.
    pytest.param("fighter", "expert", "cursed_armour_minus_1", "chainmail", False, False, (2, 17), id="plate_keeps"),
    # Leather +1 tests as 6 - 1 = 5 [14], no upgrade over plate either.
    pytest.param("fighter", "expert", "cursed_armour_ac_9", "leather", False, False, (2, 17), id="plate_keeps_ac_9"),
    # Chainmail with a shield +2 is 5 - 3 = 2 [17]. A shield +1 tests as 3 [16], no upgrade.
    pytest.param("fighter", "basic", "cursed_shield_minus_2", "shield", True, False, (2, 17), id="shield_plus_2_keeps"),
    # A magic-user can't use a shield, and stays unarmoured, 9 [10].
    pytest.param("magic_user", "basic", "cursed_shield_minus_2", "shield", False, False, (9, 10), id="mu_cannot"),
    # A thief can't wear chainmail, and keeps leather, 7 [12].
    pytest.param("thief", "basic", "cursed_armour_minus_2", "chainmail", False, False, (7, 12), id="thief_cannot"),
    # A dwarf's battle axe is two-handed, so no shield goes on. Chainmail: 5 [14].
    pytest.param("dwarf", "basic", "cursed_shield_ac_9", "shield", False, False, (5, 14), id="dwarf_cannot"),
]


@pytest.mark.parametrize(("class_id", "kind", "template_id", "base", "shield_plus_2", "worn", "real"), CHOICES)
def test_an_npc_treats_a_rolled_cursed_item_as_the_plus_1_item_it_tests_as(
    class_id, kind, template_id, base, shield_plus_2, worn, real
):
    """The NPC wears the cursed item exactly when it would wear the +1 lookalike, and fights with the real number.

    The cases with no upgrade, or with an item the class can't use, pass before the chunk too: they pin what the fix
    must not change.
    """
    worn_cursed, real_cursed, *slots_cursed = _outcome(class_id, kind, template_id, base, shield_plus_2)
    worn_lookalike, _, *slots_lookalike = _outcome(class_id, kind, LOOKALIKE[template_id], base, shield_plus_2)

    assert worn_cursed is worn_lookalike is worn
    assert real_cursed == real, "the member's armour class is the real one"
    # The same slots change: where the lookalike went on, the cursed item goes on, and the rest is the same.
    assert slots_cursed == [template_id if name == LOOKALIKE[template_id] else name for name in slots_lookalike]


# ---------------------------------------------------------------------- generated parties, by seed


def _expert_party(seed: int):
    return generate_npc_party(
        "expert",
        count=6,
        npc_stream=RngStream.from_seed_material(seed, NPC_PARTY_STREAM),
        treasure_stream=RngStream.from_seed_material(seed, TREASURE_STREAM),
        allocator=IdAllocator(),
    )


SEEDED = [
    # A cleric, DEX 13, in plate and a shield: 3 - 1 - 1 = 1. A shield +1 tests as 0, so the cursed shield -2 goes on.
    # Real: 3 - (1 - 2) - 1 = 3 [16 - 1 + 1 = 16].
    pytest.param(0, "npc-0001", "cursed_shield_minus_2", "shield", "shield", (3, 16), id="sh-2"),
    # A cleric, DEX 15, in plate and a shield, 1. Real: the shield sets 9, less 1 for DEX: 8 [11].
    pytest.param(108, "npc-0005", "cursed_shield_ac_9", "shield", "shield", (8, 11), id="sh9"),
    # A halfling, DEX 10, in leather and a shield, 6. Leather +1 tests as 5. Real: 7 + 1 - 1 = 7 [12].
    pytest.param(15, "npc-0005", "cursed_armour_minus_1", "leather", "leather", (7, 12), id="a-1"),
    # A thief, DEX 8, +1 [-1], in leather, 8. Leather +1 tests as 7. Real: 7 + 2 + 1 = 10 [9].
    pytest.param(24, "npc-0004", "cursed_armour_minus_2", "leather", "leather", (10, 9), id="a-2"),
    # A cleric, DEX 9, in plate and a shield, 2. Plate +1 tests as 1. Real: the suit sets 9, less the shield: 8 [11].
    pytest.param(149, "npc-0001", "cursed_armour_ac_9", "plate_mail", "plate_mail", (8, 11), id="a9"),
    # A cleric, DEX 16, in plate and a shield, 0. Chainmail +1 would test as 1, no upgrade, so the plate stays.
    pytest.param(18, "npc-0001", "cursed_armour_minus_2", "chainmail", None, (0, 19), id="cleric_keeps_plate"),
    # A fighter, DEX 14, in plate and a shield, 1. Chainmail +1 would test as 2, so the plate stays.
    pytest.param(28, "npc-0002", "cursed_armour_ac_9", "chainmail", None, (1, 18), id="fighter_keeps_plate"),
    # An elf, DEX 13, in chainmail, 4. Leather +1 would test as 5, so the chainmail stays.
    pytest.param(25, "npc-0004", "cursed_armour_minus_2", "leather", None, (4, 15), id="elf_keeps_chainmail"),
]


@pytest.mark.parametrize(("seed", "member_id", "template_id", "base", "displaced", "real"), SEEDED)
def test_a_generated_npc_wears_a_cursed_item_when_its_lookalike_is_an_upgrade(
    seed, member_id, template_id, base, displaced, real
):
    """`displaced` is the kit item the cursed one replaces, or None when the cursed one stays in the pack.

    The pack cases pass before the chunk too: they pin what the fix must not change.
    """
    member = next(member for member in _expert_party(seed).members if member.id == member_id)
    inventory = member.inventory
    (cursed,) = [
        instance
        for instance in inventory.all_instances()
        if isinstance(instance, MagicItemInstance) and instance.template_id == template_id
    ]
    assert cursed.base_item_id == base
    assert not cursed.identified and not cursed.cursed_revealed, "generation reveals nothing"

    in_pack = [_name(existing) for existing in inventory.items]
    if displaced is None:
        assert template_id in in_pack
    else:
        assert cursed is (inventory.shield if base == "shield" else inventory.worn_armour)
        assert displaced in in_pack, "the kit item it replaced is in the pack"
    assert (member.armour_class, member.armour_class_ascending) == real


def test_the_paired_row_puts_on_the_cursed_suit_and_the_shield_plus_1():
    """Seed 141: a halfling, DEX 7, +1 [-1], rolls the SRD's "Cursed Armour -2 with Shield +1" on leather.

    Leather and a shield: 7 - 1 + 1 = 7. Leather +1 tests as 6, so the suit goes on. Then the shield +1 tests as 5.
    Real: leather -2 is 9, the shield +1 gives 2, DEX adds 1: 8 [12 - 2 + 2 - 1 = 11].
    """
    member = next(member for member in _expert_party(141).members if member.id == "npc-0006")

    assert _name(member.inventory.worn_armour) == "cursed_armour_minus_2"
    assert _name(member.inventory.shield) == "shield_plus_1"
    assert sorted(_name(item) for item in member.inventory.items if _name(item) in ("leather", "shield")) == [
        "leather",
        "shield",
    ]
    assert (member.armour_class, member.armour_class_ascending) == (8, 11)


def test_the_golden_roster_halfling_wears_the_cursed_shield_and_nothing_else_changes():
    """The roster in `phase5_npc_parties.json`, built as its generator builds it.

    The expert halfling `npc-0007`, DEX 9, in leather and a shield, 6, rolled a cursed shield -2 that a shield +1
    would beat at 5. Real: 7 - (1 - 2) = 8 [12 + (1 - 2) = 11]. Every other member, the treasure, and both streams'
    final states are the ones the golden records, because the fix changes no draw.
    """
    from generate_phase5_goldens import NPC_PATH, build_npc_golden

    recorded = json.loads(NPC_PATH.read_text(encoding="utf-8"))
    built = build_npc_golden()

    for key in ("final_npc_stream_state", "final_treasure_stream_state", "master_seed", "basic"):
        assert built[key] == recorded[key]
    assert built["expert"]["treasure"] == recorded["expert"]["treasure"]
    for member, before in zip(built["expert"]["members"], recorded["expert"]["members"], strict=True):
        if member["id"] == "npc-0007":
            # Before the golden is regenerated, only this member's inventory differs from it.
            assert {key: value for key, value in member.items() if key != "inventory"} == {
                key: value for key, value in before.items() if key != "inventory"
            }
        else:
            assert member == before
    halfling = Character.model_validate(next(m for m in built["expert"]["members"] if m["id"] == "npc-0007"))
    assert _name(halfling.inventory.shield) == "cursed_shield_minus_2"
    assert "shield" in [_name(item) for item in halfling.inventory.items], "the plain shield is in the pack"
    assert (halfling.armour_class, halfling.armour_class_ascending) == (8, 11)


# ---------------------------------------------------------------------- in a session


def _spawned_cleric():
    """Seed 0: Hild alone in plate with a sword, facing one expert NPC 5' away, a cleric of level 4, unsurprised.

    The cleric, DEX 13, rolled a cursed shield -2. Plate and a shield are 1, and a shield +1 tests as 0, so it wears
    the cursed shield. Real: 3 - (1 - 2) - 1 = 3 [16]. The shield it took off is in its pack.
    """
    game = dungeon_session(0, (HILD,))
    result = game.execute(SpawnNpcParty(party_kind="expert", count_fixed=1, distance_feet=5))
    assert _opened(game, result, surprised=False, engage=True), "precondition: the battle opened at round 0"
    (npc,) = game.npcs.values()
    return game, result, npc


def test_a_spawned_npc_wears_the_cursed_shield_and_the_referee_sees_the_real_number():
    game, result, npc = _spawned_cleric()

    view = game.view(Visibility.REFEREE)
    assert isinstance(view, RefereeView)
    (sheet,) = [member for member in view.npcs if member.id == npc.id]
    assert _name(sheet.inventory.shield) == "cursed_shield_minus_2"
    assert sheet.inventory.shield is not None and not sheet.inventory.shield.identified
    assert (sheet.armour_class, sheet.armour_class_ascending) == (3, 16)
    shield_id = sheet.inventory.shield.instance_id

    player = game.view(Visibility.PLAYER)
    assert isinstance(player, PlayerView)
    seen = json.dumps(
        [
            player.model_dump(mode="json"),
            [event.model_dump(mode="json") for event in result.events if event.visibility is Visibility.PLAYER],
        ]
    )
    assert "cursed_shield_minus_2" not in seen and shield_id not in seen, "the players see nothing of the NPC's gear"


def test_the_party_strikes_the_npc_at_its_real_armour_class_and_learns_nothing_of_the_curse():
    """Hild attacks the cleric. The roll needs the real 3: the shield +1 it tests as would make it 0.

    No event identifies the item or reveals its curse, since the item isn't the party's, and the NPC's shield keeps
    both flags clear, so it drops as loot the way any unidentified item does.
    """
    game, _, npc = _spawned_cleric()
    hild = named(game, "Hild")
    group = game.encounter.groups[0].id
    shield = npc.inventory.shield
    assert isinstance(shield, MagicItemInstance) and shield.template_id == "cursed_shield_minus_2"

    swing = BattleDeclaration(character_id=hild, action="attack", target_group_id=group, weapon_id="sword")
    result = game.execute(ResolveBattleRound(declarations=(swing,)))

    assert result.accepted, [rejection.code for rejection in result.rejections]
    struck = [event for event in result.events if isinstance(event, AttackRolledEvent) and event.defender_id == npc.id]
    assert [event.defender_ac for event in struck] == [3]
    assert not [event for event in result.events if isinstance(event, (ItemIdentifiedEvent, CurseRevealedEvent))]
    assert not shield.identified and not shield.cursed_revealed
