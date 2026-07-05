"""NPC adventuring parties: the SRD generation procedure from the character model.

Basic and Expert Adventurers generate through the same character kernel PCs use —
composition (the caller rolls the count: the wandering table's printed dice or the
compiled composition dice), one alignment for the whole party (RAW offers either; a
single alignment drives reaction, parley, and ward interactions coherently), then per
member in order: the d8 class-and-level row, the level dice by kind, 3d6-in-order
ability scores, hit points by rolling the class hit die per level through
[`level_up`][osrlib.core.classes.level_up] (CON applied, minimum 1 per level), XP at
the class's threshold for the rolled level, the equipment kit, and rolled spell
picks. All of those draw from the
[`NPC_PARTY_STREAM`][osrlib.core.npc.NPC_PARTY_STREAM] stream; the party's treasure
and Expert magic items draw from the treasure stream instead, since they are treasure
procedures and belong to its statistics.

osrlib adopts several documented adaptations here (see the adaptations register): NPC
adventurers skip class ability-score requirements (RAW's procedure rolls class before
scores and names no re-roll); the equipment kits are invented over RAW's "normal
adventuring gear"; casters roll each open slot uniformly from the class-legal spells
of that level ("choose or roll" — rolling is the deterministic branch), with arcane
spell books equal to exactly the memorized picks; Expert magic items roll at 5% per
level per suitable sub-table in the master table's printed order, unusable rolls
ignored with no re-roll, and rolled wearable or wieldable items are equipped when
better than the kit piece (higher effective AC, or any enchantment over a mundane
arm).

Part of the core kernel. Call
[`generate_npc_party`][osrlib.core.npc.generate_npc_party] to run the whole
procedure; it builds on [`osrlib.core.character`][osrlib.core.character], whose model
and creation functions it reuses for each party member.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from osrlib.core.abilities import AbilityScore
from osrlib.core.alignment import Alignment
from osrlib.core.character import Character, roll_ability_scores
from osrlib.core.classes import ClassDefinition, level_up
from osrlib.core.dice import roll
from osrlib.core.items import (
    GeneratedTreasure,
    ItemInstance,
    MagicItemInstance,
    equip,
    magic_item_template,
    usable_by_class,
    validate_equip,
)
from osrlib.core.monsters import MonsterHitDice
from osrlib.core.rng import RngStream
from osrlib.core.spells import MemorizedSpell, caster_profile
from osrlib.core.tables import xp_band_label
from osrlib.core.treasure import MagicItemType, generate_magic_item, generate_treasure
from osrlib.data import load_classes, load_combat_tables, load_encounter_tables, load_equipment, load_spells

__all__ = [
    "NPC_PARTY_STREAM",
    "NpcParty",
    "generate_npc_party",
    "npc_defeat_xp",
]

NPC_PARTY_STREAM = "npc_party"
"""Stream key for NPC-party generation: composition, class, level, scores, hp, spells."""

# The pinned kits (registered — RAW says only "normal adventuring gear"): weapons and
# armour per class, equipped on generation; every member also carries a
# standard-rations lot, a waterskin, and a torch lot (the gear the survival
# procedures read).
_KITS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # class_id: (item ids granted, item ids equipped)
    "cleric": (("mace", "chainmail", "shield"), ("mace", "chainmail", "shield")),
    # The battle axe is two-handed, so the dwarf carries the shield unwielded —
    # the equip conflict is enforced at equip time (pinned).
    "dwarf": (("battle_axe", "chainmail", "shield"), ("battle_axe", "chainmail")),
    "elf": (("sword", "long_bow", "arrows", "chainmail"), ("sword", "long_bow", "chainmail")),
    "fighter": (("sword", "chainmail", "shield"), ("sword", "chainmail", "shield")),
    "halfling": (("sword", "sling", "sling_stones", "leather", "shield"), ("sword", "sling", "leather", "shield")),
    "magic_user": (("dagger",), ("dagger",)),
    "thief": (("sword", "leather", "thieves_tools"), ("sword", "leather")),
}

# Expert-tier Clerics, Dwarves, and Fighters upgrade body armour to plate mail.
_EXPERT_PLATE_CLASSES = ("cleric", "dwarf", "fighter")

_SUPPLIES = ("rations_standard", "waterskin", "torch")

# The master table's printed sub-table order — the Expert magic item rolls walk it.
_SUB_TABLE_ORDER = (
    MagicItemType.ARMOUR,
    MagicItemType.MISC,
    MagicItemType.POTION,
    MagicItemType.RING,
    MagicItemType.ROD_STAFF_WAND,
    MagicItemType.SCROLL,
    MagicItemType.SWORD,
    MagicItemType.WEAPON,
)


class NpcParty(BaseModel):
    """A generated NPC adventuring party: the members, their alignment, the loot.

    `treasure` is the group's shared U + V bundle (rolled once), carried as a group
    bundle that drops with the loot flow — slain or surrendered; a routed party
    keeps it.
    """

    model_config = ConfigDict(validate_assignment=True)

    kind: Literal["basic", "expert"]
    alignment: Alignment
    members: list[Character]
    treasure: GeneratedTreasure


def npc_defeat_xp(level: int) -> int:
    """Return the XP award for defeating an NPC adventurer of `level`.

    osrlib adopts the reading that an NPC adventurer's XP award is the OSE SRD's XP
    awards table value for HD equal to the NPC's level, no plus-category, no ability
    bonuses — RAW prices monsters, not classed NPCs, and level-as-HD is the straight
    reading.

    Args:
        level: The NPC's class level.

    Returns:
        The base XP award.
    """
    label = xp_band_label(MonsterHitDice(count=level, die=8))
    return load_combat_tables().xp_row(label).base


def _grant_kit(member: Character, definition: ClassDefinition, kind: str) -> None:
    equipment = load_equipment()
    granted, equipped = _KITS[definition.id]
    if kind == "expert" and definition.id in _EXPERT_PLATE_CLASSES:
        granted = tuple("plate_mail" if item_id == "chainmail" else item_id for item_id in granted)
        equipped = tuple("plate_mail" if item_id == "chainmail" else item_id for item_id in equipped)
    for item_id in (*granted, *_SUPPLIES):
        template = equipment.get(item_id)
        lot_size = getattr(template, "lot_size", 1)
        member.inventory.items.append(ItemInstance(template=template, quantity=lot_size))
    for item_id in equipped:
        instance = next(
            candidate
            for candidate in member.inventory.items
            if not isinstance(candidate, MagicItemInstance) and candidate.template.id == item_id
        )
        equip(member.inventory, definition, instance)


def _roll_spells(member: Character, definition: ClassDefinition, stream: RngStream) -> None:
    """Roll each open slot uniformly from the class-legal spells of its level."""
    profile = caster_profile(definition)
    if profile is None:
        return
    slots = definition.row(member.level).spell_slots
    catalog = load_spells()
    picks: list[MemorizedSpell] = []
    for spell_level, count in enumerate(slots, start=1):
        candidates = catalog.by_list(profile.spell_list, spell_level)
        for _ in range(count):
            picks.append(MemorizedSpell(spell_id=candidates[stream.randbelow(len(candidates))].id))
    member.memorized_spells = tuple(picks)
    if profile.kind == "arcane":
        # Normal forms: the spell book equals exactly the memorized picks (pinned).
        book: list[str] = []
        for pick in picks:
            if pick.spell_id not in book:
                book.append(pick.spell_id)
        member.spell_book = tuple(book)


def _item_usable(member: Character, definition: ClassDefinition, instance: MagicItemInstance) -> bool:
    template = magic_item_template(instance)
    if template.category in ("sword", "weapon", "armour"):
        return not validate_equip(definition, instance, member.inventory)
    return usable_by_class(template, definition)


def _maybe_equip_upgrade(member: Character, definition: ClassDefinition, instance: MagicItemInstance) -> None:
    """Equip a rolled arm when it is better than the kit piece.

    Better means higher effective AC for armour and shields, or any enchantment
    over a mundane arm for swords and weapons; cursed forms test as +1 (their
    printed deception) and are equipped like any other — the curse reveals in
    play.
    """
    template = magic_item_template(instance)
    inventory = member.inventory
    if template.category in ("sword", "weapon"):
        wielded_magic = any(isinstance(existing, MagicItemInstance) for existing in inventory.wielded)
        if not wielded_magic and not validate_equip(definition, instance, inventory):
            equip(inventory, definition, instance)
        return
    if template.category != "armour":
        return
    before = member.armour_class
    slot = "shield" if (instance.base_item_id or template.base_item_id) == "shield" else "worn_armour"
    previous = getattr(inventory, slot)
    if validate_equip(definition, instance, inventory):
        return
    equip(inventory, definition, instance)
    if member.armour_class >= before:
        # Not better: put things back the way they were.
        from osrlib.core.items import unequip

        unequip(inventory, instance)
        if previous is not None and any(existing is previous for existing in inventory.items):
            equip(inventory, definition, previous)


def _roll_expert_items(
    member: Character, definition: ClassDefinition, kind: str, treasure_stream: RngStream, allocator: Any
) -> None:
    """The Expert parties' magic items: 5% per level per suitable sub-table (RAW)."""
    if kind != "expert":
        return
    profile = caster_profile(definition)
    for category in _SUB_TABLE_ORDER:
        if category is MagicItemType.ARMOUR and definition.armour.kind.value == "none":
            continue
        if category is MagicItemType.SWORD and validate_equip(
            definition, ItemInstance(template=load_equipment().get("sword")), None
        ):
            continue
        if category in (MagicItemType.SCROLL, MagicItemType.ROD_STAFF_WAND) and profile is None:
            continue
        if treasure_stream.randbelow(100) + 1 > 5 * member.level:
            continue
        instances = generate_magic_item(category, tier="expert", stream=treasure_stream, allocator=allocator)
        for instance in instances:
            if not _item_usable(member, definition, instance):
                continue  # unusable rolls are ignored, no re-roll (RAW)
            member.inventory.items.append(instance)
            _maybe_equip_upgrade(member, definition, instance)


def generate_npc_party(
    kind: Literal["basic", "expert"],
    *,
    count: int,
    npc_stream: RngStream,
    treasure_stream: RngStream,
    allocator: Any,
) -> NpcParty:
    """Generate an NPC adventuring party by the SRD procedure.

    Draw order: one d6 alignment roll for the whole party, then per member — the d8
    class-and-level row, the level dice by `kind`, 3d6-in-order scores, the
    first-level hit die, one `level_up` roll per level above first, and the spell
    picks — all on `npc_stream`; then each member's Expert magic items and finally
    the shared U + V group treasure on `treasure_stream`.

    Args:
        kind: `"basic"` (levels 1d3) or `"expert"` (per-row level dice).
        count: The party size; the caller rolls it (the wandering row's printed
            dice, or the compiled composition dice).
        npc_stream: The `npc_party` stream.
        treasure_stream: The treasure stream — items and the group bundle are
            treasure procedures and belong to its statistics, not the NPC stream's.
        allocator: The id allocator (`npc`, `magic-item`, and `valuable` prefixes).

    Returns:
        The generated party.
    """
    tables = load_encounter_tables()
    classes = load_classes()
    alignment_roll = npc_stream.randbelow(6) + 1
    alignment_band = next(band for band in tables.npc_alignment if band.roll_min <= alignment_roll <= band.roll_max)
    alignment = Alignment(alignment_band.alignment)
    members: list[Character] = []
    for index in range(count):
        class_roll = npc_stream.randbelow(8) + 1
        row = next(entry for entry in tables.npc_class_levels if entry.roll == class_roll)
        definition = classes.get(row.class_id)
        level = roll(row.basic_dice if kind == "basic" else row.expert_dice, npc_stream).total
        scores = roll_ability_scores(npc_stream).scores
        con_modifier = _hit_point_modifier(scores)
        first_die = npc_stream.randbelow(definition.hit_die) + 1
        member = Character(
            id=allocator.allocate("npc"),
            name=f"{definition.name} adventurer {index + 1}",
            class_id=definition.id,
            race=definition.race,
            level=1,
            xp=0,
            scores=scores,
            alignment=alignment,
            max_hp=max(1, first_die + con_modifier),
            current_hp=max(1, first_die + con_modifier),
        )
        for _ in range(level - 1):
            level_up(member, definition, npc_stream)
        member.xp = definition.row(level).xp
        _grant_kit(member, definition, kind)
        _roll_spells(member, definition, npc_stream)
        members.append(member)
    for member in members:
        _roll_expert_items(member, classes.get(member.class_id), kind, treasure_stream, allocator)
    tier = "expert" if kind == "expert" else "basic"
    bundle_u = generate_treasure("U", tier=tier, stream=treasure_stream, allocator=allocator)
    bundle_v = generate_treasure("V", tier=tier, stream=treasure_stream, allocator=allocator)
    from osrlib.core.items import Coins

    combined = GeneratedTreasure(
        coins=Coins(
            **{
                denomination: getattr(bundle_u.coins, denomination) + getattr(bundle_v.coins, denomination)
                for denomination in ("pp", "gp", "ep", "sp", "cp")
            }
        ),
        valuables=(*bundle_u.valuables, *bundle_v.valuables),
        magic_items=(*bundle_u.magic_items, *bundle_v.magic_items),
    )
    return NpcParty(kind=kind, alignment=alignment, members=members, treasure=combined)


def _hit_point_modifier(scores: dict[AbilityScore, int]) -> int:
    from osrlib.data import load_ability_tables

    return load_ability_tables().hit_point_modifier(scores[AbilityScore.CON])
