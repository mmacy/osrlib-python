"""NPC adventuring parties: a rival band of adventurers, rolled up from the SRD's procedure.

[`generate_npc_party`][osrlib.core.npc.generate_npc_party] is the entry point. Tell it how many
members and whether they are the Basic or the Expert kind, hand it two seeded streams and an
[`IdAllocator`][osrlib.core.monsters.IdAllocator], and you get an
[`NpcParty`][osrlib.core.npc.NpcParty]: a band of classed characters with gear, memorized spells,
and treasure to take off them. Use it when a wandering monster roll turns up other adventurers,
or when you want a rival party for an encounter you are writing.

Its members are ordinary [`Character`][osrlib.core.character.Character] models, the same ones the
players use, so everything else in the library takes them as they are: they fight through
[`osrlib.core.combat`][osrlib.core.combat], cast through
[`osrlib.core.spells`][osrlib.core.spells], and can be put into a
[`Party`][osrlib.crawl.party.Party] if you want to run them as one.
[`npc_defeat_xp`][osrlib.core.npc.npc_defeat_xp] gives the experience a party earns for defeating
one of them.

How many adventurers appear is not decided here. Roll the count from the wandering monster table
that produced the encounter, then pass it in.

Every member's own draws come from the
[`NPC_PARTY_STREAM`][osrlib.core.npc.NPC_PARTY_STREAM] stream: the class and level, the ability
scores, the hit points, the spells they have prepared. The party's treasure and the Expert band's
magic items come from the [`TREASURE_STREAM`][osrlib.core.treasure.TREASURE_STREAM] stream instead,
because they are treasure rolls and belong with the rest of a game's treasure statistics.

Four things here are osrlib's reading rather than the SRD's letter, and all four appear in
[the adaptations register](https://mmacy.github.io/osrlib-python/adaptations/), the site page that
collects the places where osrlib settles an ambiguous rule one way or supplies a default the
tabletop game leaves to a referee. NPC adventurers are not checked against their class's ability
requirements, because the SRD's procedure rolls the class before the scores and offers no re-roll.
The equipment kits are osrlib's, standing in for the SRD's "normal adventuring gear". Casters get
spells rolled at random from the ones their class may cast, since the SRD lets the referee choose
or roll and only rolling is repeatable. An Expert band's magic items are rolled at 5% per level
against each kind of item the member could use, and an item nobody can use is dropped rather than
re-rolled.

Typical usage:

```python
from osrlib.core.monsters import IdAllocator
from osrlib.core.npc import NPC_PARTY_STREAM, generate_npc_party
from osrlib.core.rng import RngStreams
from osrlib.core.treasure import TREASURE_STREAM

streams = RngStreams(master_seed=5)
party = generate_npc_party(
    "basic",
    count=3,
    npc_stream=streams.get(NPC_PARTY_STREAM),
    treasure_stream=streams.get(TREASURE_STREAM),
    allocator=IdAllocator(),
)
print(party.alignment.value)
# neutral
for member in party.members:
    print(member.id, member.class_id, member.level, member.max_hp)
# npc-0001 halfling 2 4
# npc-0002 thief 3 14
# npc-0003 fighter 1 6
```
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
from osrlib.core.rng import RngStream, StreamName
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

NPC_PARTY_STREAM = StreamName.NPC_PARTY
"""The stream key every session uses for rolling up NPC adventurers.

A stream key names one independent random-number sequence inside an
[`RngStreams`][osrlib.core.rng.RngStreams] set. Pass `streams.get(NPC_PARTY_STREAM)` as the
`npc_stream` argument of [`generate_npc_party`][osrlib.core.npc.generate_npc_party], which draws
the party's alignment and then each member's class, level, ability scores, hit points, and spells
from it.

The party's treasure and its magic items do not come from this stream. They are treasure rolls, and
they draw from [`TREASURE_STREAM`][osrlib.core.treasure.TREASURE_STREAM] so that a change to how NPC
parties are built does not shift the treasure a game has already recorded.
"""

# The kits are osrlib's, standing in for the SRD's "normal adventuring gear": weapons and
# armour per class, worn and wielded at generation. Every member also gets a lot of standard
# rations, a waterskin, and a lot of torches, which are the supplies the survival procedures read.
_KITS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # class_id: (item ids granted, item ids equipped)
    "cleric": (("mace", "chainmail", "shield"), ("mace", "chainmail", "shield")),
    # The battle axe is two-handed, so the dwarf carries the shield without wielding it. The
    # equip validator refuses the combination, so the kit lists the shield as granted but not
    # equipped.
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

# The order the master table prints its sub-tables in. The Expert magic item rolls walk it.
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
    """A band of NPC adventurers: who they are, what they believe, and what they carry between them.

    Returned by [`generate_npc_party`][osrlib.core.npc.generate_npc_party]. Run them as an
    encounter: roll reaction with [`osrlib.crawl.encounter`][osrlib.crawl.encounter], fight them
    through [`osrlib.core.combat`][osrlib.core.combat], and award
    [`npc_defeat_xp`][osrlib.core.npc.npc_defeat_xp] per member if the players win.
    """

    model_config = ConfigDict(validate_assignment=True)

    kind: Literal["basic", "expert"]
    """`"basic"` for a band of low-level adventurers or `"expert"` for a seasoned one. It decided the level dice, the
    armour the members wear, and whether they carry magic items.
    """

    alignment: Alignment
    """The alignment the whole band shares. One roll covers everyone, so reactions, parleys, and the wards that turn on
    alignment all have a single answer.
    """

    members: list[Character]
    """The adventurers, as ordinary [`Character`][osrlib.core.character.Character] models with ids from the allocator
    you passed. Everything in the library that takes a character takes these.
    """

    treasure: GeneratedTreasure
    """What the band carries between them, rolled once for the group rather than per member. In a crawl the band
    carries it as one bundle: the members who are slain drop it on the party's cell as a pile, and a band that
    runs away takes it with them.
    """


def npc_defeat_xp(level: int) -> int:
    """Return the experience a party earns for defeating one NPC adventurer of this level.

    Call it once per defeated member of an [`NpcParty`][osrlib.core.npc.NpcParty], add the results
    together with whatever else the party overcame, and hand the total to
    [`apply_xp`][osrlib.core.classes.apply_xp] for each surviving character.

    The SRD prices monsters by Hit Dice and says nothing about classed NPCs, so osrlib prices an
    NPC adventurer as a monster of as many Hit Dice as they have levels, with no bonus for special
    abilities. [The adaptations register](https://mmacy.github.io/osrlib-python/adaptations/), the
    site page that collects the places where osrlib settles an ambiguous rule one way, records the
    reading.

    Args:
        level: The NPC's class level.

    Returns:
        The experience for defeating them.

    Examples:
        ```python
        from osrlib.core.npc import npc_defeat_xp

        print(npc_defeat_xp(1), npc_defeat_xp(3), npc_defeat_xp(5))
        # 10 35 175
        ```
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
    """Fill each of a caster's memorization slots with a spell drawn at random from its level."""
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
        # An arcane NPC's spell book contains exactly the spells they have memorized.
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
    """Equip a rolled weapon or piece of armour when it beats the one from the kit.

    Better means a higher armour class for armour and shields, or any enchantment at all over a
    mundane weapon. A cursed item tests as though it were a +1, which is what it claims to be, so
    it gets equipped like any other, and the curse comes out in play.
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
    """Roll an Expert band member's magic items: 5% per level against each sub-table they could use."""
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
                continue  # An item nobody can use is dropped, with no re-roll, as written.
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
    """Roll up a band of NPC adventurers, complete with gear, spells, and treasure.

    Use it when your game needs other adventurers: a wandering encounter, a rival party in a keyed
    room, a patrol. You supply the size, because the table that produced the encounter sets how many
    appear. Everything else is rolled here.

    The band shares one alignment, rolled once, so their reaction to the players and their
    vulnerability to alignment-gated wards have a single answer. Then each member in turn gets a
    class and a level from the SRD's table, ability scores rolled 3d6 in order, hit points,
    experience set to the threshold for their level, an equipment kit their class can use, and, if
    they cast, spells prepared at random from their class's list. An Expert band wears heavier
    armour and each member gets a 5% chance per level at each kind of magic item they could use.

    Hit points come in two parts, which matters if you are counting draws. The first level's hit
    die is rolled here, directly, with the CON modifier added and the total floored at 1. Every
    level after the first goes through [`level_up`][osrlib.core.classes.level_up], one call per
    level, and each of those calls takes a draw only when that level's row adds a hit die. An
    Expert dwarf rolled at level 11 or 12 passes name level, so its top levels take no draw.

    Members are not checked against their class's ability requirements, because the SRD rolls their
    class before their scores. An elf here may have an INT a player character would not be allowed.

    The draws come off the two streams in a fixed order, which is what makes a seeded encounter
    repeatable: the alignment and every member's own rolls from `npc_stream` in member order, then
    each member's magic items and finally the shared treasure from `treasure_stream`.

    Args:
        kind: `"basic"` for a band of levels 1 to 3, or `"expert"` for a seasoned one whose level
            dice depend on the class rolled.
        count: How many adventurers appear. Roll it from the encounter table that sent them.
        npc_stream: The stream for the members themselves, conventionally
            `streams.get(`[`NPC_PARTY_STREAM`][osrlib.core.npc.NPC_PARTY_STREAM]`)`.
        treasure_stream: The stream for their magic items and their shared treasure, conventionally
            `streams.get(`[`TREASURE_STREAM`][osrlib.core.treasure.TREASURE_STREAM]`)`, so those
            rolls land in the treasure statistics with every other treasure roll.
        allocator: The [`IdAllocator`][osrlib.core.monsters.IdAllocator] that names the members and
            the items and valuables they carry. Pass the session's own allocator so nothing
            collides with ids already in play.

    Returns:
        The band, its shared alignment, and its treasure.

    Examples:
        ```python
        from osrlib.core.monsters import IdAllocator
        from osrlib.core.npc import NPC_PARTY_STREAM, generate_npc_party, npc_defeat_xp
        from osrlib.core.rng import RngStreams
        from osrlib.core.treasure import TREASURE_STREAM

        streams = RngStreams(master_seed=5)
        party = generate_npc_party(
            "basic",
            count=2,
            npc_stream=streams.get(NPC_PARTY_STREAM),
            treasure_stream=streams.get(TREASURE_STREAM),
            allocator=IdAllocator(),
        )
        print(party.kind, party.alignment.value)
        # basic neutral
        for member in party.members:
            print(member.name, member.level, member.max_hp, member.armour_class)
        # Halfling adventurer 1 2 4 6
        # Thief adventurer 2 3 14 7
        print(sum(npc_defeat_xp(member.level) for member in party.members))
        # 55
        ```
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
