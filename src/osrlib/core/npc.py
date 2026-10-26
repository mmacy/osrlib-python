"""NPC adventuring parties: rival groups of adventurers, generated with the SRD's procedure.

An NPC (non-player character) is a person in the game who isn't one of the players' characters. An
NPC party is a group of NPC adventurers with classes, levels, gear, and treasure, like the
players' party. A row of a wandering monster table can be an NPC party, or you can place one in
an adventure as rivals.

[`generate_npc_party`][osrlib.core.npc.generate_npc_party] is the entry point. Pass it the kind of
party, `"basic"` or `"expert"`, and how many members it has. Also pass two seeded streams and an
[`IdAllocator`][osrlib.core.monsters.IdAllocator] for the members' ids. It returns an
[`NpcParty`][osrlib.core.npc.NpcParty]: the members with their gear and memorized spells, the
alignment they share, and the treasure they carry. In a session, the engine generates a party
and opens an encounter with it for you when it resolves a
[`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty] command.

The members are [`Character`][osrlib.core.character.Character] models, the same model as the
players' characters, so every function that takes a character takes them. They fight with the
functions in [`osrlib.core.combat`][osrlib.core.combat] and cast with the functions in
[`osrlib.core.spells`][osrlib.core.spells]. When the players defeat one,
[`npc_defeat_xp`][osrlib.core.npc.npc_defeat_xp] returns the experience points it's worth.

You set how many adventurers appear. When a wandering monster table row is an NPC party, roll the
row's `count_dice` and pass the total as `count`.

The members' rolls come from the stream keyed by
[`NPC_PARTY_STREAM`][osrlib.core.npc.NPC_PARTY_STREAM]: the shared alignment, and each member's
class, level, ability scores, hit points, and spells. The party's treasure and the expert members'
magic items come from the stream keyed by
[`TREASURE_STREAM`][osrlib.core.treasure.TREASURE_STREAM] instead, with the rest of a game's
treasure rolls.

In these places osrlib settles a rule the SRD leaves open, and
[the adaptations register](https://mmacy.github.io/osrlib-python/adaptations/), the site page that
lists every place osrlib commits to one reading of the rules, records each one:

- [`generate_npc_party`][osrlib.core.npc.generate_npc_party] doesn't check NPC adventurers
  against their class's minimum ability scores, because the SRD's procedure rolls the class
  before the scores and gives no re-roll.
- The equipment kits are osrlib's, in place of the SRD's "normal adventuring gear".
- A caster's spells are rolled at random from their class's list. The SRD lets the referee choose
  or roll, and only rolling gives the same party from the same seed.
- An expert member rolls for magic items at 5% per level for each kind of item they could use. An
  item the member can't use is dropped, not rolled again.

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
"""The stream key for generating NPC adventurers, `"npc_party"`.

A stream key is the name of one independent random-number sequence in an
[`RngStreams`][osrlib.core.rng.RngStreams] set. Pass `streams.get(NPC_PARTY_STREAM)` as the
`npc_stream` argument of [`generate_npc_party`][osrlib.core.npc.generate_npc_party]. It rolls the
party's alignment from this stream, and then each member's class, level, ability scores, hit
points, and spells. A [`GameSession`][osrlib.crawl.session.GameSession] uses this key when it
generates an NPC party.

The party's treasure and magic items come from
[`TREASURE_STREAM`][osrlib.core.treasure.TREASURE_STREAM] instead. Because the streams are
separate, a change to how NPC parties are generated doesn't change the treasure rolls in a save
you've already made.
"""

# The kits are osrlib's, in place of the SRD's "normal adventuring gear": weapons and armour for
# each class, worn and wielded at generation. Every member also gets one lot of standard rations,
# a waterskin, and one lot of torches, the supplies the survival rules read.
_KITS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # class_id: (item ids granted, item ids equipped)
    "cleric": (("mace", "chainmail", "shield"), ("mace", "chainmail", "shield")),
    # The battle axe is two-handed, so the dwarf carries the shield without using it. The equip
    # validator refuses a shield with a two-handed weapon, so the kit grants the shield but doesn't
    # equip it.
    "dwarf": (("battle_axe", "chainmail", "shield"), ("battle_axe", "chainmail")),
    "elf": (("sword", "long_bow", "arrows", "chainmail"), ("sword", "long_bow", "chainmail")),
    "fighter": (("sword", "chainmail", "shield"), ("sword", "chainmail", "shield")),
    "halfling": (("sword", "sling", "sling_stones", "leather", "shield"), ("sword", "sling", "leather", "shield")),
    "magic_user": (("dagger",), ("dagger",)),
    "thief": (("sword", "leather", "thieves_tools"), ("sword", "leather")),
}

# In an expert party, clerics, dwarves, and fighters wear plate mail instead of chainmail.
_EXPERT_PLATE_CLASSES = ("cleric", "dwarf", "fighter")

_SUPPLIES = ("rations_standard", "waterskin", "torch")

# The magic item sub-tables, in the order they appear in the master table. An expert member's magic
# item rolls go through them in this order.
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
    """A party of NPC adventurers, with the alignment they share and the treasure they carry.

    [`generate_npc_party`][osrlib.core.npc.generate_npc_party] returns one. Run the members as an
    encounter: roll the monster reaction with [`roll_reaction`][osrlib.core.combat.roll_reaction],
    fight them with the functions in [`osrlib.core.combat`][osrlib.core.combat], and, if the
    players win, award [`npc_defeat_xp`][osrlib.core.npc.npc_defeat_xp] for each member defeated.
    In a session, execute a [`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty] command
    instead. The engine generates the party and opens the encounter for you.
    """

    model_config = ConfigDict(validate_assignment=True)

    kind: Literal["basic", "expert"]
    """`"basic"` for a party of low-level adventurers, or `"expert"` for a higher-level one.

    The dice rolled for each member's level, the armour the members wear, and whether they roll for magic items all
    depend on it.
    """

    alignment: Alignment
    """The alignment every member shares, from one roll for the whole party. A spell that works differently against
    another alignment, like *protection from evil*, treats every member the same way.
    """

    members: list[Character]
    """The adventurers, as [`Character`][osrlib.core.character.Character] models.

    Each member's `id` comes from the allocator you passed, like `"npc-0001"`. Each member's `name` is the class name
    and the member's position, like `"Thief adventurer 2"`. Every function that takes a character takes these.
    """

    treasure: GeneratedTreasure
    """The coins, valuables, and magic items the party carries, rolled once for the whole party.

    In a session, the treasure drops where the players' party stands once every member is slain. If any member fled
    or routed, the party takes the treasure away with it.
    """


def npc_defeat_xp(level: int) -> int:
    """Return the experience points a party earns for defeating one NPC adventurer of this level.

    Call it once for each defeated member of an [`NpcParty`][osrlib.core.npc.NpcParty], and add
    the results to the rest of the party's experience. Divide the total among the surviving
    characters and award each share with [`apply_xp`][osrlib.core.classes.apply_xp]. In a
    session, the session counts defeated NPCs for you.

    The SRD gives experience for monsters by Hit Dice, and gives no rule for NPC adventurers. osrlib
    counts an NPC adventurer as a monster with as many Hit Dice as the NPC has levels, and no
    special abilities. [The adaptations register](https://mmacy.github.io/osrlib-python/adaptations/),
    the site page that lists every place osrlib commits to one reading of the rules, records this
    reading.

    Args:
        level: The NPC's level, 1 or higher.

    Returns:
        The experience points for defeating the NPC.

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
    mundane weapon. A cursed item tests as though it were a +1, which is what it appears to be, so
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
                continue  # An item the member can't use is dropped, with no re-roll, as written.
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
    """Generate a party of NPC adventurers, with their gear, spells, and treasure.

    Use it when you need other adventurers, like a wandering encounter or a rival party in a
    room of an adventure. You pass the number of members, because the encounter table or the adventure
    sets it. Everything else is rolled here. In a session, execute a
    [`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty] command instead. The engine calls this
    function and opens an encounter with the party.

    The party shares one alignment, rolled once. Then each member gets, in turn:

    - A class and a level, from the SRD's NPC party table.
    - Ability scores, 3d6 for each ability in order.
    - Hit points for that level.
    - Experience points equal to the threshold for that level.
    - An equipment kit their class can use, with rations, a waterskin, and torches.
    - If they cast spells, memorized spells rolled at random from their class's list.

    In an expert party, clerics, dwarves, and fighters wear plate mail instead of chainmail. Each
    expert member also has a 5% chance per level of a magic item from each kind of item they could
    use. The party's treasure is treasure types U and V, rolled once for the whole party and added
    together.

    The first level's hit die is rolled directly, with the CON modifier added, and gives at least
    1 hit point. Each level after the first is added with
    [`level_up`][osrlib.core.classes.level_up], which rolls a die only when that level adds a hit
    die. The expert level dice can put a dwarf, fighter, or thief above name level, the level at
    which a character may build a stronghold, and no die is rolled for a level past name level.

    This function doesn't check members against their class's minimum ability scores, because the
    SRD's procedure rolls the class before the scores. An elf here can have an INT too low for a
    player character elf.

    The rolls come from the two streams in a fixed order, so the same seeds give the same party.
    The alignment and then each member's rolls, in member order, come from `npc_stream`. Each
    member's magic items, and then the party's treasure, come from `treasure_stream`.

    Args:
        kind: `"basic"` for a party of levels 1 to 3, or `"expert"` for a higher-level party,
            whose level dice depend on the class rolled.
        count: How many adventurers appear. Roll it from the `count_dice` of the encounter table
            row for the party.
        npc_stream: The stream for the members, usually
            `streams.get(`[`NPC_PARTY_STREAM`][osrlib.core.npc.NPC_PARTY_STREAM]`)`.
        treasure_stream: The stream for the magic items and the party's treasure, usually
            `streams.get(`[`TREASURE_STREAM`][osrlib.core.treasure.TREASURE_STREAM]`)`.
        allocator: The [`IdAllocator`][osrlib.core.monsters.IdAllocator] that assigns ids to the
            members and to the magic items and valuables they carry. In a session, pass
            [`GameSession.allocator`][osrlib.crawl.session.GameSession.allocator], so the new ids
            don't repeat ids already in the game.

    Returns:
        The party: its members, their shared alignment, and their treasure.

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
