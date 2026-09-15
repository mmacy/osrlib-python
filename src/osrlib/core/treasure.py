"""Treasure tables and treasure generation: the types monsters carry, gems, jewellery, and magic items.

This module turns a treasure type into loot. It takes the compiled tables that
[`load_treasure_tables`][osrlib.data.load_treasure_tables] returns and produces a
[`GeneratedTreasure`][osrlib.core.items.GeneratedTreasure]: coins, gems and jewellery,
and magic item instances ready for an
[`Inventory`][osrlib.core.items.Inventory] or a chest in a dungeon.

Which entry point you want depends on what you are stocking:

- [`generate_treasure`][osrlib.core.treasure.generate_treasure] for a treasure type,
  the letter a monster's stat block names.
- [`generate_unguarded_treasure`][osrlib.core.treasure.generate_unguarded_treasure] for
  a cache nothing is guarding, by dungeon level.
- [`generate_magic_item`][osrlib.core.treasure.generate_magic_item] for one magic item
  rolled at random, and
  [`instantiate_magic_item`][osrlib.core.treasure.instantiate_magic_item] for a
  particular item you have already chosen.
- [`plan_treasure_ref`][osrlib.core.treasure.plan_treasure_ref] to work out what a
  monster's treasure entry means: what it carries and what waits in its lair.
- [`roll_room_contents`][osrlib.core.treasure.roll_room_contents] to stock a room at
  all, which is the roll that settles whether there is treasure to generate.

Under a running game, treasure is generated for you when a monster is placed or a cache
is stocked, and reaches the party through the
[`TakeTreasure`][osrlib.crawl.commands.TakeTreasure] command. Call these functions
directly when you are using the rules without a session, or building an adventure ahead
of play.

Every entry point takes a tier, either `"basic"` or `"expert"`. The rules print two
probability columns for magic items, B and X, one for each half of the game, and the
tier picks the column: a game chooses by how experienced the party is, and the crawl
uses Basic while the party's highest living level is 1 to 3 and Expert from 4 up.

Every printed entry of a treasure type parses to one fixed shape: an optional percentage
chance that the entry is there at all, then either a quantity of coins, a count of gems
or jewellery, or a magic item allotment. Allotments are structured rather than free text,
so "any 3 magic items" and "1 potion" and "a sword, suit of armour, or weapon" are all
[`MagicAllotment`][osrlib.core.treasure.MagicAllotment]s a program can read.

Generation draws in printed order: the presence roll for each entry, then the quantity
dice, then each item resolved completely before the next one starts. That order is the
contract, so the same seed and the same table always produce the same hoard.

Typical usage:

```python
from osrlib.core.monsters import IdAllocator
from osrlib.core.rng import RngStreams
from osrlib.core.treasure import TREASURE_STREAM, generate_treasure

stream = RngStreams(master_seed=1).get(TREASURE_STREAM)
hoard = generate_treasure("B", tier="basic", stream=stream, allocator=IdAllocator())

print(hoard.coins.value_gp, len(hoard.valuables), len(hoard.magic_items))
# 3000 4 0
```
"""

# The treasure models live in this module rather than in core/tables.py: the
# osrlib/data loaders import their model homes and every core module imports the
# loaders, so a crawl-owned model home would give load_treasure_tables() a
# core -> data -> crawl transitive import.

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from osrlib.core.dice import parse, roll
from osrlib.core.rng import RngStream

if TYPE_CHECKING:
    from osrlib.core.items import (
        GeneratedTreasure,
        MagicItemInstance,
        SentientSwordTables,
        SwordSentience,
        SwordTableBand,
        ValuableInstance,
    )
    from osrlib.core.monsters import TreasureRef

__all__ = [
    "TREASURE_STREAM",
    "CoinDenomination",
    "CoinQuantity",
    "GemValueBand",
    "GemValueTable",
    "MagicAllotment",
    "MagicItemType",
    "MagicItemTypeRow",
    "MagicItemTypeTable",
    "RoomContentsResult",
    "StockingRow",
    "StockingTable",
    "TreasureEntry",
    "TreasureRefPlan",
    "TreasureSection",
    "TreasureTables",
    "TreasureTypeTable",
    "UnguardedTreasureBand",
    "UnguardedTreasureTable",
    "generate_magic_item",
    "generate_treasure",
    "generate_treasure_entries",
    "generate_unguarded_treasure",
    "instantiate_magic_item",
    "plan_treasure_ref",
    "roll_room_contents",
]

TREASURE_STREAM = "treasure"
"""The name of the RNG stream treasure generation draws from.

Pass `streams.get(TREASURE_STREAM)` to any generation function, where `streams` is the
[`RngStreams`][osrlib.core.rng.RngStreams] of the session or of your own master seed.
Using the named stream is what makes a hoard reproducible: every stream advances
independently, so the treasure a party finds does not change because a fight went
differently.

Nothing forces the choice, and the generation functions take whatever stream you hand
them. Use this one unless you have a reason not to.
"""


class TreasureSection(StrEnum):
    """Which section of the treasure tables a letter belongs to, which is when you generate it.

    A monster's stat block names treasure types by letter, and when you generate a letter
    depends on its section: once for the lair, once per monster, or once for the whole
    group.
    [`plan_treasure_ref`][osrlib.core.treasure.plan_treasure_ref] sorts a stat block's
    letters into those three piles for you.

    The wire values are lowercase and serialize into the compiled treasure data. Changing
    them is a `schema_version` bump.
    """

    HOARD = "hoard"
    """Types A to O: the treasure in a monster's lair, generated once for the lair."""
    INDIVIDUAL = "individual"
    """Types P to T: what one monster carries, generated once per monster."""
    GROUP = "group"
    """Types U and V: what a group carries between them, generated once for the group."""


class CoinDenomination(StrEnum):
    """The five coin denominations, as the treasure tables print them.

    A coin entry names one of these, and generation adds the rolled amount to the matching
    field of [`Coins`][osrlib.core.items.Coins]. The values are the same strings those models
    use for their fields, so a denomination can be used as a key.
    """

    PP = "pp"
    """Platinum, worth 5 gp."""
    GP = "gp"
    """Gold."""
    EP = "ep"
    """Electrum, worth half a gold piece."""
    SP = "sp"
    """Silver, ten to the gold piece."""
    CP = "cp"
    """Copper, a hundred to the gold piece."""


class MagicItemType(StrEnum):
    """The types of the master *Magic Item Type* table, which is the first roll in generating a magic item.

    Roll the master table to get one of these, then roll that type's own table for the item:
    [`generate_magic_item`][osrlib.core.treasure.generate_magic_item] does both. A magic
    allotment for one kind of item names its type here.

    These are the table's types, not the catalog's categories. Rods, staves, and wands share
    a single printed row and a single table, while the catalog keeps them apart. See
    [`MagicItemCategory`][osrlib.core.items.MagicItemCategory].

    The wire values are lowercase and serialize into the compiled treasure data. Changing
    them is a `schema_version` bump.
    """

    ARMOUR = "armour"
    """Enchanted armour and shields."""
    MISC = "misc"
    """Miscellaneous magic items."""
    POTION = "potion"
    """Potions."""
    RING = "ring"
    """Rings."""
    ROD_STAFF_WAND = "rod_staff_wand"
    """The rod, staff, and wand row, which covers all three."""
    SCROLL = "scroll"
    """The scroll row, which covers treasure maps as well as spell scrolls."""
    SWORD = "sword"
    """Enchanted swords."""
    WEAPON = "weapon"
    """Every other enchanted weapon."""


class CoinQuantity(BaseModel):
    """How many coins of one denomination a treasure entry yields.

    The printed multiplier is folded into the dice expression, so an entry that reads
    `1d6 × 1,000 gp` arrives here as dice of `1d6×1000` and a denomination of gold. Roll it
    with [`roll`][osrlib.core.dice.roll] if you are generating by hand. The generation
    functions do it for you.
    """

    model_config = ConfigDict(frozen=True)

    denomination: CoinDenomination
    """Which coins. See [`CoinDenomination`][osrlib.core.treasure.CoinDenomination]."""
    dice: str
    """How many, as a dice expression."""

    @field_validator("dice")
    @classmethod
    def _dice_must_parse(cls, value: str) -> str:
        parse(value)
        return value


class MagicAllotment(BaseModel):
    """One magic item clause of a treasure entry: how many items, and how free the choice is.

    Read `kind` to know what the clause means:

    - `any`: roll the master *Magic Item Type* table for each item, re-rolling anything in
      `exclude`. This is how "any 3 magic items" and "any 1 magic item, no weapons" are
      stored.
    - `category`: roll the one table named in `categories`, as in "1 potion".
    - `pool`: choose evenly among the types in `categories`, then roll that type's table, as
      in "a magic sword, suit of armour, or weapon".

    Exactly one of `count` and `count_dice` sizes the clause. Only an `any` clause has
    exclusions, because a clause that names its type has nothing to exclude.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["any", "category", "pool"]
    """`"any"`, `"category"`, or `"pool"`."""
    categories: tuple[MagicItemType, ...] = ()
    """The types the clause names. See [`MagicItemType`][osrlib.core.treasure.MagicItemType]. Empty for `any`, one for
    `category`, two or more for `pool`.
    """
    count: int | None = None
    """A fixed number of items, or `None` when the count is rolled."""
    count_dice: str | None = None
    """The number of items as a dice expression, or `None` when it is fixed."""
    exclude: tuple[MagicItemType, ...] = ()
    """Types an `any` clause re-rolls. Empty on the other kinds."""

    @field_validator("count_dice")
    @classmethod
    def _dice_must_parse(cls, value: str | None) -> str | None:
        if value is not None:
            parse(value)
        return value

    @model_validator(mode="after")
    def _shape_must_match_kind(self) -> MagicAllotment:
        if (self.count is None) == (self.count_dice is None):
            raise ValueError("exactly one of count or count_dice is required")
        if self.kind == "any" and self.categories:
            raise ValueError("an 'any' allotment names no categories")
        if self.kind == "category" and len(self.categories) != 1:
            raise ValueError("a 'category' allotment names exactly one category")
        if self.kind == "pool" and len(self.categories) < 2:
            raise ValueError("a 'pool' allotment names at least two categories")
        if self.kind != "any" and self.exclude:
            raise ValueError("only an 'any' allotment carries exclusions")
        return self


class TreasureEntry(BaseModel):
    """One printed line of a treasure type: one thing that might be in the hoard.

    A treasure type is a list of these, and generation walks them in order. Each has exactly
    one payload, so an entry is coins, or gems, or jewellery, or magic items, never a
    mixture. Generate a list of them with
    [`generate_treasure_entries`][osrlib.core.treasure.generate_treasure_entries], which is
    also how you generate the hoard a treasure map leads to.
    """

    model_config = ConfigDict(frozen=True)

    chance_pct: int = Field(default=0, ge=0, le=100)
    """The percentage chance the entry is present at all. 0 means it always is, which is how the entries printed without
    a chance are stored.
    """
    coins: CoinQuantity | None = None
    """The coins this entry yields, or `None`. See [`CoinQuantity`][osrlib.core.treasure.CoinQuantity]."""
    gems_dice: str | None = None
    """How many gems, as a dice expression, or `None`. Each gem's value is rolled separately on the gem table."""
    jewellery_dice: str | None = None
    """How many pieces of jewellery, as a dice expression, or `None`. Each piece's value is rolled separately."""
    magic: tuple[MagicAllotment, ...] = ()
    """The magic item clauses, or empty. See [`MagicAllotment`][osrlib.core.treasure.MagicAllotment]."""

    @field_validator("gems_dice", "jewellery_dice")
    @classmethod
    def _dice_must_parse(cls, value: str | None) -> str | None:
        if value is not None:
            parse(value)
        return value

    @model_validator(mode="after")
    def _exactly_one_payload(self) -> TreasureEntry:
        payloads = sum(
            (
                self.coins is not None,
                self.gems_dice is not None,
                self.jewellery_dice is not None,
                bool(self.magic),
            )
        )
        if payloads != 1:
            raise ValueError("a treasure entry carries exactly one payload")
        return self


class TreasureTypeTable(BaseModel):
    """One treasure type, A through V: everything a hoard of that letter can contain.

    Get one with
    [`TreasureTables.treasure_type`][osrlib.core.treasure.TreasureTables.treasure_type], or
    skip straight to the result with
    [`generate_treasure`][osrlib.core.treasure.generate_treasure]. Read the entries when you
    want to show a referee what a letter can produce before rolling it.
    """

    model_config = ConfigDict(frozen=True)

    letter: str = Field(min_length=1, max_length=1)
    """The type letter, for example `"A"`. See [the treasure type index][treasure-types-index]."""
    kind: TreasureSection
    """Whether the letter is lair treasure, carried by one monster, or carried by a group. See
    [`TreasureSection`][osrlib.core.treasure.TreasureSection].
    """
    average_gp: float = Field(ge=0)
    """The average value of a hoard of this type in gold pieces, as the rules print it. A planning figure for an
    adventure author, not something generation aims at.
    """
    entries: tuple[TreasureEntry, ...] = Field(min_length=1)
    """The printed lines, in order. See [`TreasureEntry`][osrlib.core.treasure.TreasureEntry]."""


class GemValueBand(BaseModel):
    """One band of the gem value table: the d20 rolls that make a gem worth a given amount."""

    model_config = ConfigDict(frozen=True)

    roll_min: int = Field(ge=1, le=20)
    """The lowest d20 result in this band."""
    roll_max: int = Field(ge=1, le=20)
    """The highest d20 result in this band."""
    value_gp: int = Field(ge=1)
    """What a gem rolled in this band is worth, in gold pieces."""

    @model_validator(mode="after")
    def _band_must_be_ordered(self) -> GemValueBand:
        if self.roll_min > self.roll_max:
            raise ValueError("gem band minimum exceeds maximum")
        return self


class GemValueTable(BaseModel):
    """What a gem is worth, and how much a piece of jewellery is worth.

    Generation rolls a gem's value on the d20 bands and a piece of jewellery on the dice,
    once per piece, and stamps the result onto a
    [`ValuableInstance`][osrlib.core.items.ValuableInstance] whose value never changes
    afterwards. Roll a gem yourself with
    [`value_for_roll`][osrlib.core.treasure.GemValueTable.value_for_roll].
    """

    model_config = ConfigDict(frozen=True)

    bands: tuple[GemValueBand, ...] = Field(min_length=1)
    """The gem value bands, covering the whole d20. See [`GemValueBand`][osrlib.core.treasure.GemValueBand]."""
    jewellery_dice: str
    """What one piece of jewellery is worth, as a dice expression."""
    manual_notes: tuple[str, ...] = ()
    """The rules the referee applies by hand, as printed: that rough treatment can halve a piece of jewellery's value,
    and that several gems may be combined into fewer, larger ones. osrlib does neither. Show these to the referee if
    your game applies them.
    """

    @field_validator("jewellery_dice")
    @classmethod
    def _dice_must_parse(cls, value: str) -> str:
        parse(value)
        return value

    @model_validator(mode="after")
    def _bands_cover_the_d20(self) -> GemValueTable:
        expected = 1
        for band in self.bands:
            if band.roll_min != expected:
                raise ValueError("gem bands must be contiguous from 1")
            expected = band.roll_max + 1
        if expected != 21:
            raise ValueError("gem bands must cover the whole d20")
        return self

    def value_for_roll(self, roll: int) -> int:
        """Return what a gem is worth for a d20 roll.

        Args:
            roll: The d20 result, 1 to 20.

        Returns:
            The value in gold pieces.

        Raises:
            ValueError: If the roll is outside 1 to 20.

        Examples:
            ```python
            from osrlib.data import load_treasure_tables

            gems = load_treasure_tables().gems
            print(gems.value_for_roll(1), gems.value_for_roll(20))
            # 10 1000
            ```
        """
        for band in self.bands:
            if band.roll_min <= roll <= band.roll_max:
                return band.value_gp
        raise ValueError(f"gem value roll must be 1-20, got {roll}")


class MagicItemTypeRow(BaseModel):
    """One row of the master *Magic Item Type* table: a type and the d% rolls that select it, in both columns.

    The rules print two columns, B for Basic play and X for Expert, and a type's odds differ
    between them: scrolls and swords get likelier in Expert play, potions less likely. A
    printed `00` is read as 100, so both columns close at 100.
    """

    model_config = ConfigDict(frozen=True)

    category: MagicItemType
    """The type this row selects. See [`MagicItemType`][osrlib.core.treasure.MagicItemType]."""
    basic_min: int = Field(ge=1, le=100)
    """The lowest d% roll that selects it in the B column."""
    basic_max: int = Field(ge=1, le=100)
    """The highest d% roll that selects it in the B column."""
    expert_min: int = Field(ge=1, le=100)
    """The lowest d% roll that selects it in the X column."""
    expert_max: int = Field(ge=1, le=100)
    """The highest d% roll that selects it in the X column."""


class MagicItemTypeTable(BaseModel):
    """The master *Magic Item Type* table: which kind of magic item a random roll produces.

    The first of the two rolls that generate a magic item. Roll it with
    [`category_for_roll`][osrlib.core.treasure.MagicItemTypeTable.category_for_roll], then
    roll the type's own table, which
    [`MagicItemCatalog.sub_table`][osrlib.core.items.MagicItemCatalog.sub_table] returns. Or
    let [`generate_magic_item`][osrlib.core.treasure.generate_magic_item] do both and
    instantiate the item.
    """

    model_config = ConfigDict(frozen=True)

    rows: tuple[MagicItemTypeRow, ...] = Field(min_length=1)
    """The rows, in printed order, each column covering the whole d%. See
    [`MagicItemTypeRow`][osrlib.core.treasure.MagicItemTypeRow].
    """

    @model_validator(mode="after")
    def _columns_cover_the_d100(self) -> MagicItemTypeTable:
        for tier in ("basic", "expert"):
            expected = 1
            for row in self.rows:
                if getattr(row, f"{tier}_min") != expected:
                    raise ValueError(f"{tier} bands must be contiguous from 01")
                expected = getattr(row, f"{tier}_max") + 1
            if expected != 101:
                raise ValueError(f"{tier} bands must cover the whole d%")
        return self

    def category_for_roll(self, roll: int, *, tier: str) -> MagicItemType:
        """Return the type of magic item a d% roll produces, under one tier's column.

        Args:
            roll: The d% result, 1 to 100, with a rolled `00` passed as 100.
            tier: `"basic"` or `"expert"`, choosing the printed B or X column.

        Returns:
            The selected type.

        Raises:
            ValueError: If the tier is not one of the two, or the roll is outside 1 to 100.

        Examples:
            ```python
            from osrlib.data import load_treasure_tables

            table = load_treasure_tables().magic_item_types
            print(table.category_for_roll(50, tier="basic"), table.category_for_roll(50, tier="expert"))
            # rod_staff_wand scroll
            ```
        """
        if tier not in ("basic", "expert"):
            raise ValueError(f"tier must be 'basic' or 'expert', got {tier!r}")
        for row in self.rows:
            if getattr(row, f"{tier}_min") <= roll <= getattr(row, f"{tier}_max"):
                return row.category
        raise ValueError(f"magic item type roll must be 1-100, got {roll}")


class StockingRow(BaseModel):
    """One row of the room stocking table: what is in a room, and how likely treasure is with it."""

    model_config = ConfigDict(frozen=True)

    roll_min: int = Field(ge=1, le=6)
    """The lowest d6 result in this row."""
    roll_max: int = Field(ge=1, le=6)
    """The highest d6 result in this row."""
    contents: Literal["empty", "monster", "special", "trap"]
    """What is in the room: `"empty"`, `"monster"`, `"special"`, or `"trap"`."""
    treasure_chance_in_six: int = Field(ge=0, le=6)
    """How many faces of a d6 mean treasure as well, for example 3 for a 3-in-6 chance. 0 where the table prints no
    chance, and no die is rolled then.
    """


class StockingTable(BaseModel):
    """The *Random Dungeon Room Contents* table: what to put in a room when you are stocking a dungeon.

    Rolling it is a two-step procedure, so call
    [`roll_room_contents`][osrlib.core.treasure.roll_room_contents] rather than this table
    directly unless you are rolling by hand: it rolls the contents, then the treasure chance
    that goes with them, and reports both.

    This is an authoring tool, not something play calls: it is how you fill a dungeon level
    before anyone explores it.
    """

    model_config = ConfigDict(frozen=True)

    rows: tuple[StockingRow, ...] = Field(min_length=1)
    """The rows, covering the whole d6. See [`StockingRow`][osrlib.core.treasure.StockingRow]."""

    @model_validator(mode="after")
    def _rows_cover_the_d6(self) -> StockingTable:
        expected = 1
        for row in self.rows:
            if row.roll_min != expected:
                raise ValueError("stocking rows must be contiguous from 1")
            expected = row.roll_max + 1
        if expected != 7:
            raise ValueError("stocking rows must cover the whole d6")
        return self

    def row_for_roll(self, roll: int) -> StockingRow:
        """Return the stocking row a d6 roll selects.

        Args:
            roll: The d6 result, 1 to 6.

        Returns:
            The selected row.

        Raises:
            ValueError: If the roll is outside 1 to 6.
        """
        for row in self.rows:
            if row.roll_min <= roll <= row.roll_max:
                return row
        raise ValueError(f"stocking roll must be 1-6, got {roll}")


class UnguardedTreasureBand(BaseModel):
    """One dungeon-level band of the unguarded treasure table.

    Deeper levels have more, so the table is banded by level. The entries have the same
    shape as a treasure type's.
    """

    model_config = ConfigDict(frozen=True)

    label: str = Field(min_length=1)
    """The band as the table prints it, for example `"Level 2–3"`."""
    min_level: int = Field(ge=1)
    """The shallowest dungeon level in the band."""
    max_level: int = Field(ge=1)
    """The deepest dungeon level in the band."""
    entries: tuple[TreasureEntry, ...] = Field(min_length=1)
    """The printed lines, in order. See [`TreasureEntry`][osrlib.core.treasure.TreasureEntry]."""

    @model_validator(mode="after")
    def _band_must_be_ordered(self) -> UnguardedTreasureBand:
        if self.min_level > self.max_level:
            raise ValueError("unguarded band minimum exceeds maximum")
        return self


class UnguardedTreasureTable(BaseModel):
    """The unguarded treasure table: what a cache nobody is guarding contains, by dungeon level.

    Use it through
    [`generate_unguarded_treasure`][osrlib.core.treasure.generate_unguarded_treasure], which
    picks the band and generates from it. Levels past the deepest printed band use that band,
    so a level 12 cache is as rich as a level 9 one and no richer.
    """

    model_config = ConfigDict(frozen=True)

    bands: tuple[UnguardedTreasureBand, ...] = Field(min_length=1)
    """The bands, in level order and contiguous from level 1. See
    [`UnguardedTreasureBand`][osrlib.core.treasure.UnguardedTreasureBand].
    """

    @model_validator(mode="after")
    def _bands_must_be_contiguous_from_one(self) -> UnguardedTreasureTable:
        expected = 1
        for band in self.bands:
            if band.min_level != expected:
                raise ValueError("unguarded bands must be contiguous from level 1")
            expected = band.max_level + 1
        return self

    def band_for_level(self, level: int) -> UnguardedTreasureBand:
        """Return the band for a dungeon level.

        Args:
            level: The dungeon level, counting from 1.

        Returns:
            The band covering that level. Levels past the deepest band get the deepest band,
            the same clamp the encounter tables use.

        Raises:
            ValueError: If `level` is below 1.
        """
        if level < 1:
            raise ValueError(f"dungeon levels are 1-based, got {level}")
        # Same clamp-into-the-last-band convention the encounter tables use for
        # dungeon levels past their printed range.
        for band in self.bands:
            if level <= band.max_level:
                return band
        return self.bands[-1]


class TreasureTables(BaseModel):
    """Every treasure table there is, loaded and ready to generate from.

    Call [`load_treasure_tables`][osrlib.data.load_treasure_tables] to get the shipped set.
    It is frozen, cached, and shared. The generation functions load it themselves, so you
    need this only to read a table: to show a referee what a letter can produce, or to roll
    a table by hand.
    """

    model_config = ConfigDict(frozen=True)

    treasure_types: tuple[TreasureTypeTable, ...]
    """Every treasure type, A through V. See [`TreasureTypeTable`][osrlib.core.treasure.TreasureTypeTable]. Reach one by
    letter with [`treasure_type`][osrlib.core.treasure.TreasureTables.treasure_type].
    """
    gems: GemValueTable
    """What gems and jewellery are worth. See [`GemValueTable`][osrlib.core.treasure.GemValueTable]."""
    magic_item_types: MagicItemTypeTable
    """Which kind of magic item a roll produces. See [`MagicItemTypeTable`][osrlib.core.treasure.MagicItemTypeTable]."""
    stocking: StockingTable
    """What is in a room when you stock a dungeon. See [`StockingTable`][osrlib.core.treasure.StockingTable]."""
    unguarded: UnguardedTreasureTable
    """What an unguarded cache contains, by level. See
    [`UnguardedTreasureTable`][osrlib.core.treasure.UnguardedTreasureTable].
    """

    @model_validator(mode="after")
    def _letters_must_be_unique(self) -> TreasureTables:
        letters = [table.letter for table in self.treasure_types]
        if len(set(letters)) != len(letters):
            raise ValueError("treasure type letters must be unique")
        return self

    def treasure_type(self, letter: str) -> TreasureTypeTable:
        """Return the treasure type for `letter`.

        Args:
            letter: The type letter, for example `"A"`, as a monster's stat block names it. See
                [the treasure type index][treasure-types-index]. Case-sensitive: the letters
                are uppercase.

        Returns:
            The treasure type.

        Raises:
            ValueError: If no type has that letter.

        Examples:
            ```python
            from osrlib.data import load_treasure_tables

            hoard = load_treasure_tables().treasure_type("A")
            print(hoard.kind, hoard.average_gp)
            # hoard 18000.0
            ```
        """
        for table in self.treasure_types:
            if table.letter == letter:
                return table
        raise ValueError(f"unknown treasure type {letter!r}")


class RoomContentsResult(BaseModel):
    """What one roll on the room stocking table produced.

    Returned by [`roll_room_contents`][osrlib.core.treasure.roll_room_contents]. It reports
    both rolls so an adventure author can see how a room was decided, and so a tool that
    stocks a level can log it.
    """

    model_config = ConfigDict(frozen=True)

    roll: int
    """The d6 that chose the contents."""
    row: StockingRow
    """The row it selected. See [`StockingRow`][osrlib.core.treasure.StockingRow]."""
    treasure_roll: int | None = None
    """The d6 rolled for treasure, or `None` when the row gives no chance of treasure and no die was rolled."""
    treasure_present: bool = False
    """True when the room has treasure as well as its contents. Which treasure is yours to decide: generate it with
    [`generate_unguarded_treasure`][osrlib.core.treasure.generate_unguarded_treasure] for an empty or trapped room, or
    from the monster's own type when a monster is there.
    """


class TreasureRefPlan(BaseModel):
    """A monster's treasure entry, worked out into what to generate and when.

    [`plan_treasure_ref`][osrlib.core.treasure.plan_treasure_ref] produces one of these from
    a stat block's treasure reference. The three letter lists say when each letter is
    generated: the lair ones once when you stock the lair, the individual ones once per
    monster, and the group ones once for the whole group. A letter in parentheses on the stat
    block is lair treasure whatever section it belongs to, which is how the bandit's `U (A)`
    means each bandit carries type U and the camp has a type A hoard.

    Some references generate nothing at all. Where a stat block marks the treasure special
    or describes it below, the referee writes it, and those parts are left out of the plan.
    So are the two adjustments the rules leave to the referee: reducing a hoard for a small
    lair, and changing a hoard's value by hand.
    """

    model_config = ConfigDict(frozen=True)

    lair: tuple[str, ...] = ()
    """Letters to generate once for the lair, in the order the stat block lists them."""
    individual: tuple[str, ...] = ()
    """Letters to generate once per monster."""
    group: tuple[str, ...] = ()
    """Letters to generate once for the group."""
    extra_gp: int = 0
    """Flat gold pieces the entry adds to the lair hoard."""
    multiplier: int = 1
    """How many times to run the whole generation, for an entry like the noble's `V × 3`. 1 for everything else."""


def plan_treasure_ref(ref: TreasureRef) -> TreasureRefPlan:
    """Work out what a monster's treasure entry means.

    Call this before generating a monster's treasure: the letters on a stat block are not all
    generated at the same moment, and this sorts them into the ones you roll for the lair, the
    ones you roll per monster, and the ones you roll for the group. Then call
    [`generate_treasure`][osrlib.core.treasure.generate_treasure] once per letter, repeated
    `multiplier` times.

    Args:
        ref: The treasure reference from a monster template's `treasure` field. See
            [`TreasureRef`][osrlib.core.monsters.TreasureRef].

    Returns:
        The plan. See [`TreasureRefPlan`][osrlib.core.treasure.TreasureRefPlan].

    Raises:
        ValueError: If the reference names a letter that is not a treasure type.

    Examples:
        ```python
        from osrlib.core.treasure import plan_treasure_ref
        from osrlib.data import load_monsters

        plan = plan_treasure_ref(load_monsters().get("bandit").treasure)
        print(plan.lair, plan.group, plan.individual)
        # ('A',) ('U',) ()
        ```
    """
    from osrlib.data import load_treasure_tables

    tables = load_treasure_tables()
    lair: list[str] = []
    individual: list[str] = []
    group: list[str] = []
    for letter in ref.letters:
        kind = tables.treasure_type(letter).kind
        if kind is TreasureSection.HOARD:
            lair.append(letter)
        elif kind is TreasureSection.INDIVIDUAL:
            individual.append(letter)
        else:
            group.append(letter)
    for letter in ref.parenthetical:
        tables.treasure_type(letter)
        lair.append(letter)
    return TreasureRefPlan(
        lair=tuple(lair),
        individual=tuple(individual),
        group=tuple(group),
        extra_gp=ref.extra_gp,
        multiplier=ref.multiplier,
    )


def roll_room_contents(stream: RngStream) -> RoomContentsResult:
    """Roll what is in a room and whether there is treasure with it.

    The stocking roll, for filling a dungeon level before play: it rolls the d6 for contents,
    then, when the row gives a chance of treasure, a second d6 for that. A row printing no
    chance of treasure consumes no second die, which keeps the draw sequence exact.

    Generating the treasure is a separate step, because which table to use depends on what is
    in the room: the monster's own treasure type when a monster is there, and
    [`generate_unguarded_treasure`][osrlib.core.treasure.generate_unguarded_treasure]
    otherwise.

    Args:
        stream: The RNG stream to draw from, conventionally
            [`TREASURE_STREAM`][osrlib.core.treasure.TREASURE_STREAM].

    Returns:
        Both rolls and the row they selected. See
        [`RoomContentsResult`][osrlib.core.treasure.RoomContentsResult].

    Examples:
        ```python
        from osrlib.core.rng import RngStreams
        from osrlib.core.treasure import TREASURE_STREAM, roll_room_contents

        result = roll_room_contents(RngStreams(master_seed=5).get(TREASURE_STREAM))
        print(result.roll, result.row.contents, result.treasure_present)
        # 6 trap False
        ```
    """
    from osrlib.data import load_treasure_tables

    table = load_treasure_tables().stocking
    contents_roll = stream.randbelow(6) + 1
    row = table.row_for_roll(contents_roll)
    if row.treasure_chance_in_six == 0:
        return RoomContentsResult(roll=contents_roll, row=row)
    treasure_roll = stream.randbelow(6) + 1
    return RoomContentsResult(
        roll=contents_roll,
        row=row,
        treasure_roll=treasure_roll,
        treasure_present=treasure_roll <= row.treasure_chance_in_six,
    )


def _int_param(params: Mapping[str, Any], key: str, default: int = 0) -> int:
    """Read an integer param from schema-validated data the type checker cannot key by name."""
    return int(params.get(key, default))


def _generate_valuable(kind: Literal["gem", "jewellery"], *, stream: RngStream, allocator: Any) -> ValuableInstance:
    """Roll one gem's or one piece of jewellery's value and return it as an owned instance."""
    from osrlib.core.items import ValuableInstance
    from osrlib.data import load_equipment, load_treasure_tables

    weights = {row.id: row.weight_coins for row in load_equipment().treasure_weights}
    if kind == "gem":
        value = load_treasure_tables().gems.value_for_roll(stream.randbelow(20) + 1)
        name = "Gem"
    else:
        value = roll(load_treasure_tables().gems.jewellery_dice, stream).total
        name = "Jewellery"
    return ValuableInstance(
        instance_id=allocator.allocate("valuable"),
        kind=kind,
        name=name,
        value_gp=value,
        weight_coins=weights[kind],
    )


def _generate_sentience(*, stream: RngStream) -> SwordSentience | None:
    """Roll a magic sword's sentience in the SRD's own procedure order.

    The special-purpose 1-in-20 rolls first for each magic sword. A special sword is always
    sentient at INT 12 and Ego 12. Otherwise the 30% sentience roll decides. Then come the
    printed steps: INT 1d6+6, communication by INT, languages (rolled only for swords that
    speak, since an empathic sword speaks nothing), alignment, powers by INT (sensory
    duplicates re-rolled with draws consumed, extraordinary duplicates re-rolled unless the
    power allows them), and Ego 1d12.
    """
    from osrlib.core.items import SwordSentience
    from osrlib.data import load_magic_items

    tables = load_magic_items().sentient_swords
    special_purpose: str | None = None
    if stream.randbelow(20) + 1 == 1:
        purpose_roll = stream.randbelow(6) + 1
        special_purpose = next(
            band.result for band in tables.special_purposes if band.roll_min <= purpose_roll <= band.roll_max
        )
        intelligence = 12
    else:
        if stream.randbelow(100) + 1 > 30:
            return None
        intelligence = roll("1d6+6", stream).total
    communication_row = next(row for row in tables.communication if row.int_score == intelligence)
    languages = 0
    if communication_row.communication == "speech":
        languages = _roll_language_count(tables.languages, stream)
    alignment_roll = stream.randbelow(20) + 1
    alignment = next(band.result for band in tables.alignment if band.roll_min <= alignment_roll <= band.roll_max)
    powers_row = next(row for row in tables.powers if row.int_score == intelligence)
    sensory: list[str] = []
    extraordinary: list[str] = []
    _roll_powers(tables, powers_row.sensory, sensory, extraordinary, stream, extraordinary_table=False)
    _roll_powers(tables, powers_row.extraordinary, sensory, extraordinary, stream, extraordinary_table=True)
    ego = 12 if special_purpose is not None else stream.randbelow(12) + 1
    return SwordSentience(
        intelligence=intelligence,
        ego=ego,
        communication=communication_row.communication,
        reading=communication_row.reading,
        alignment=alignment,
        languages=languages,
        sensory_powers=tuple(sensory),
        extraordinary_powers=tuple(extraordinary),
        special_purpose=special_purpose,
    )


def _roll_language_count(bands: Sequence[SwordTableBand], stream: RngStream) -> int:
    """Roll how many languages a speaking sword knows, resolving the roll-twice instruction."""
    total = 0
    pending = 1
    while pending:
        pending -= 1
        language_roll = stream.randbelow(100) + 1
        result = next(band.result for band in bands if band.roll_min <= language_roll <= band.roll_max)
        if result == "roll_twice":
            pending += 2
        else:
            total += int(result)
    return total


def _roll_powers(
    tables: SentientSwordTables,
    count: int,
    sensory: list[str],
    extraordinary: list[str],
    stream: RngStream,
    *,
    extraordinary_table: bool,
) -> None:
    """Roll `count` powers on one table, resolving instructions and duplicates.

    Duplicates re-roll with draws consumed, and extraordinary powers whose pages allow
    duplicates count each extra roll. The sensory table's `roll_extraordinary` instruction
    grants an extraordinary power, and `roll_twice` and `roll_thrice` add rolls on the same
    table.
    """
    bands = tables.extraordinary_bands if extraordinary_table else tables.sensory_bands
    pending = count
    while pending:
        pending -= 1
        power_roll = stream.randbelow(100) + 1
        result = next(band.result for band in bands if band.roll_min <= power_roll <= band.roll_max)
        if result == "roll_twice":
            pending += 2
            continue
        if result == "roll_thrice":
            pending += 3
            continue
        if result == "roll_extraordinary":
            _roll_powers(tables, 1, sensory, extraordinary, stream, extraordinary_table=True)
            continue
        bucket = extraordinary if extraordinary_table else sensory
        if result in bucket and not tables.power(result).duplicates_allowed:
            pending += 1  # duplicate re-rolled, draw consumed
            continue
        bucket.append(result)


def _require_tier(tier: str) -> None:
    """Refuse an unknown tier before anything is drawn.

    Every generation entry point calls this first, because a kernel function checks its
    arguments before its first draw: a bad tier costs no draws and returns no hoard.
    """
    if tier not in ("basic", "expert"):
        raise ValueError(f"tier must be 'basic' or 'expert', got {tier!r}")


def _generate_scroll_spells(template: Any, *, tier: str, stream: RngStream) -> dict[str, Any]:
    """Roll a spell scroll's contents: the 1-in-4 chance of a divine scroll, then each spell.

    Each inscribed spell rolls its level on the scroll spell-level table, under the tier's
    column, and then picks evenly among the class list's spells of that level. The rules let
    the referee choose the spells or roll for them, and osrlib always rolls, so a scroll is
    reproducible from the stream.
    """
    from osrlib.data import load_magic_items, load_spells

    levels_table = load_magic_items().scroll_spell_levels
    divine = stream.randbelow(4) + 1 == 1
    spell_list = "cleric" if divine else "magic_user"
    count = int(template.params["spell_count"])
    spell_ids: list[str] = []
    for _ in range(count):
        if tier == "basic":
            level = levels_table.level_for_basic(stream.randbelow(6) + 1, divine=divine)
        else:
            level = levels_table.level_for_expert(stream.randbelow(100) + 1, divine=divine)
        candidates = load_spells().by_list(spell_list, level)
        spell_ids.append(candidates[stream.randbelow(len(candidates))].id)
    return {"spell_list": spell_list, "spells": tuple(spell_ids)}


def instantiate_magic_item(
    item_id: str,
    *,
    tier: str,
    stream: RngStream,
    allocator: Any,
    params: Mapping[str, Any] | None = None,
) -> MagicItemInstance:
    """Create one copy of a magic item you have already chosen, rolling the details that differ between copies.

    This is how a hand-placed magic item becomes something a character can carry. An
    adventure names the item ([`FeatureSpec.magic_item_ids`][osrlib.crawl.dungeon.FeatureSpec]
    does exactly this), and this rolls what the item's own page leaves to chance: what a
    generic suit of enchanted armour is made of, how many charges a wand has, how many
    arrows are in the bundle, how many wishes the ring has, which spells are on the scroll,
    how many levels the sword can drain, and last of all whether a sword is sentient. Nothing
    is left unrolled: the instance that comes back is ready to use.

    When you want the item chosen at random too, call
    [`generate_magic_item`][osrlib.core.treasure.generate_magic_item], which rolls the tables
    and then calls this.

    The copy comes back unidentified. The party learns what it is by using it.

    Args:
        item_id: The item to create. See [the magic item id index][magic-items-index].
        tier: `"basic"` or `"expert"`, the printed B or X column. It matters only where a
            detail differs between the two, like the level of a scroll's spells.
        stream: The RNG stream to draw from, conventionally
            [`TREASURE_STREAM`][osrlib.core.treasure.TREASURE_STREAM].
        allocator: The id source for the new instance's id, which takes the `magic-item`
            prefix. An [`IdAllocator`][osrlib.core.monsters.IdAllocator]. A session hands
            you its own, and a standalone caller makes one.
        params: Overrides from a generation table row, when this is being called from one:
            the quantity dice a printed band gives, a fixed Basic-tier quantity, a wish
            count. Leave it `None` when placing an item by hand, and the template's own
            values are used.

    Returns:
        The new instance, unidentified. See
        [`MagicItemInstance`][osrlib.core.items.MagicItemInstance].

    Raises:
        ValueError: If `tier` is neither `"basic"` nor `"expert"`, or the catalog has no
            item with that id.

    Examples:
        ```python
        from osrlib.core.monsters import IdAllocator
        from osrlib.core.rng import RngStreams
        from osrlib.core.treasure import TREASURE_STREAM, instantiate_magic_item

        stream = RngStreams(master_seed=11).get(TREASURE_STREAM)
        staff = instantiate_magic_item("staff_of_striking", tier="expert", stream=stream, allocator=IdAllocator())

        print(staff.instance_id, staff.charges_remaining, staff.identified)
        # magic-item-0001 18 False
        ```
    """
    from osrlib.core.items import MagicItemCategory, MagicItemInstance
    from osrlib.data import load_magic_items

    _require_tier(tier)
    row_params: Mapping[str, Any] = params if params is not None else {}
    catalog = load_magic_items()
    template = catalog.get(item_id)
    instance = MagicItemInstance(
        instance_id=allocator.allocate("magic-item"),
        template_id=item_id,
        base_item_id=template.base_item_id,
    )
    if template.category is MagicItemCategory.ARMOUR and template.base_item_id is None:
        instance.base_item_id = catalog.armour_type.base_for_roll(stream.randbelow(8) + 1)
    if template.charges_dice is not None:
        instance.charges_remaining = roll(template.charges_dice, stream).total
    quantity_dice = row_params.get("quantity_dice", template.quantity_dice)
    if quantity_dice is not None:
        if tier == "basic" and "basic_quantity_fixed" in row_params:
            instance.quantity = _int_param(row_params, "basic_quantity_fixed")
        else:
            instance.quantity = roll(str(quantity_dice), stream).total
    wish_dice = row_params.get("wish_count_dice", template.params.get("wish_count_dice"))
    if wish_dice is not None:
        instance.state = {**instance.state, "wishes_remaining": roll(str(wish_dice), stream).total}
    if "spell_count" in template.params:
        instance.state = {**instance.state, **_generate_scroll_spells(template, tier=tier, stream=stream)}
    if template.effect is not None and template.effect.kind == "on_hit_drain":
        drains = roll(str(template.effect.params["total_drains_dice"]), stream).total
        instance.state = {**instance.state, "drains_remaining": drains}
    if template.category is MagicItemCategory.SWORD:
        instance.sentience = _generate_sentience(stream=stream)
    return instance


def generate_magic_item(
    category: MagicItemType | None,
    *,
    tier: str,
    stream: RngStream,
    allocator: Any,
    exclude: tuple[MagicItemType, ...] = (),
) -> list[MagicItemInstance]:
    """Roll one magic item at random: which kind, which item, and all its details.

    Three steps in one call. When `category` is `None` it rolls the master *Magic Item Type*
    table for the kind, re-rolling anything you excluded. Then it rolls that kind's own table
    for the item. Then
    [`instantiate_magic_item`][osrlib.core.treasure.instantiate_magic_item] rolls the item's
    details. Pass a `category` when the kind is already decided, as it is for an allotment
    for a potion.

    Use it when you want a single random item: a reward, a gift, a hand-placed surprise. For
    a monster's whole hoard call
    [`generate_treasure`][osrlib.core.treasure.generate_treasure], which rolls allotments as
    well as coins and gems.

    Args:
        category: The kind of item to roll, or `None` to roll the kind too. See
            [`MagicItemType`][osrlib.core.treasure.MagicItemType].
        tier: `"basic"` or `"expert"`, the printed B or X column. It changes the odds of
            each kind and which items a table can reach.
        stream: The RNG stream to draw from, conventionally
            [`TREASURE_STREAM`][osrlib.core.treasure.TREASURE_STREAM].
        allocator: The id source for the new instances, which take the `magic-item` prefix.
            An [`IdAllocator`][osrlib.core.monsters.IdAllocator].
        exclude: Kinds to re-roll when the kind is being rolled. Excluded rolls consume
            draws, as re-rolling at the table does.

    Returns:
        The new instances, unidentified. Usually one. Two when the table's row is a suit of
        armour that comes with a shield.

    Raises:
        ValueError: If `tier` is neither `"basic"` nor `"expert"`, or `category` is one of
            the kinds you excluded.

    Examples:
        ```python
        from osrlib.core.monsters import IdAllocator
        from osrlib.core.rng import RngStreams
        from osrlib.core.treasure import TREASURE_STREAM, generate_magic_item

        stream = RngStreams(master_seed=9).get(TREASURE_STREAM)
        items = generate_magic_item(None, tier="basic", stream=stream, allocator=IdAllocator())

        print([(item.instance_id, item.template_id) for item in items])
        # [('magic-item-0001', 'rope_of_climbing')]
        ```
    """
    from osrlib.data import load_magic_items, load_treasure_tables

    _require_tier(tier)
    tables = load_treasure_tables()
    catalog = load_magic_items()
    if category is None:
        while True:
            category = tables.magic_item_types.category_for_roll(stream.randbelow(100) + 1, tier=tier)
            if category not in exclude:
                break
    elif category in exclude:
        raise ValueError(f"category {category} is excluded")
    sub_table = catalog.sub_table(category)
    if tier == "basic":
        row = sub_table.row_for_basic(stream.randbelow(sub_table.basic_die) + 1)
    else:
        row = sub_table.row_for_expert(stream.randbelow(100) + 1)
    return [
        instantiate_magic_item(item_id, tier=tier, stream=stream, allocator=allocator, params=row.params)
        for item_id in row.item_ids
    ]


def generate_treasure_entries(
    entries: Sequence[TreasureEntry],
    *,
    tier: str,
    stream: RngStream,
    allocator: Any,
) -> GeneratedTreasure:
    """Generate treasure from a list of printed entries.

    The engine the other generation functions share, and the one to call when you have
    entries in hand rather than a letter or a level: the hoard a treasure map leads to is a
    list of entries on the map's own template, and this is how you turn it into loot.

    Entries are resolved in printed order: for each one, the presence roll if it is gated,
    then the quantity dice, then each gem, piece of jewellery, or magic item resolved
    completely before the next begins. That order is the contract behind reproducibility, so
    the same seed and the same entries always give the same result.

    Args:
        entries: The printed entries. See
            [`TreasureEntry`][osrlib.core.treasure.TreasureEntry].
        tier: `"basic"` or `"expert"`, the printed B or X column for magic items.
        stream: The RNG stream to draw from, conventionally
            [`TREASURE_STREAM`][osrlib.core.treasure.TREASURE_STREAM].
        allocator: The id source for generated gems, jewellery, and magic items, which take
            the `valuable` and `magic-item` prefixes. An
            [`IdAllocator`][osrlib.core.monsters.IdAllocator].

    Returns:
        The coins, valuables, and magic items. See
        [`GeneratedTreasure`][osrlib.core.items.GeneratedTreasure]. Any of the three can be
        empty, and entries that failed their presence roll contribute nothing.

    Raises:
        ValueError: If `tier` is neither `"basic"` nor `"expert"`. The tier is checked
            before the first draw, so a refused call costs no draws and returns nothing,
            whether or not the entries would have reached a magic item.

    Examples:
        ```python
        from osrlib.core.monsters import IdAllocator
        from osrlib.core.rng import RngStreams
        from osrlib.core.treasure import TREASURE_STREAM, generate_treasure_entries
        from osrlib.data import load_magic_items

        recipe = load_magic_items().get("treasure_map_i").hoard_recipe
        stream = RngStreams(master_seed=2).get(TREASURE_STREAM)
        hoard = generate_treasure_entries(recipe, tier="basic", stream=stream, allocator=IdAllocator())

        print([item.template_id for item in hoard.magic_items])
        # ['mace_plus_1']
        ```
    """
    from osrlib.core.items import Coins, GeneratedTreasure

    _require_tier(tier)
    coin_totals: dict[str, int] = {}
    valuables = []
    magic_items = []
    for entry in entries:
        if entry.chance_pct and stream.randbelow(100) + 1 > entry.chance_pct:
            continue
        if entry.coins is not None:
            amount = roll(entry.coins.dice, stream).total
            key = entry.coins.denomination.value
            coin_totals[key] = coin_totals.get(key, 0) + amount
        elif entry.gems_dice is not None:
            for _ in range(roll(entry.gems_dice, stream).total):
                valuables.append(_generate_valuable("gem", stream=stream, allocator=allocator))
        elif entry.jewellery_dice is not None:
            for _ in range(roll(entry.jewellery_dice, stream).total):
                valuables.append(_generate_valuable("jewellery", stream=stream, allocator=allocator))
        else:
            for allotment in entry.magic:
                if allotment.count is not None:
                    count = allotment.count
                elif allotment.count_dice is not None:
                    count = roll(allotment.count_dice, stream).total
                else:  # unreachable: the allotment model requires one of the two
                    raise ValueError("magic allotment carries neither count nor count_dice")
                for _ in range(count):
                    if allotment.kind == "any":
                        picked = None
                    elif allotment.kind == "pool":
                        picked = allotment.categories[stream.randbelow(len(allotment.categories))]
                    else:
                        picked = allotment.categories[0]
                    magic_items.extend(
                        generate_magic_item(
                            picked, tier=tier, stream=stream, allocator=allocator, exclude=allotment.exclude
                        )
                    )
    return GeneratedTreasure(coins=Coins(**coin_totals), valuables=tuple(valuables), magic_items=tuple(magic_items))


def generate_treasure(
    treasure_type: str,
    *,
    tier: str,
    stream: RngStream,
    allocator: Any,
) -> GeneratedTreasure:
    """Generate one treasure type's contents.

    The entry point for a monster's treasure: a stat block names a letter, and this rolls
    everything that letter can contain. Which letters to roll, and whether each belongs to the
    lair, one monster, or the group, is what
    [`plan_treasure_ref`][osrlib.core.treasure.plan_treasure_ref] works out from the stat
    block.

    Nothing is placed: what comes back is yours to put in a chest, hand to a monster, or add
    to an inventory.

    Args:
        treasure_type: The type letter, `"A"` through `"V"`. See
            [the treasure type index][treasure-types-index].
        tier: `"basic"` or `"expert"`, the printed B or X column for magic items. A game
            chooses by how experienced the party is. The crawl uses Basic while the party's
            highest living level is 1 to 3 and Expert from 4 up, decided when the treasure
            is generated.
        stream: The RNG stream to draw from, conventionally
            [`TREASURE_STREAM`][osrlib.core.treasure.TREASURE_STREAM].
        allocator: The id source for generated gems, jewellery, and magic items, which take
            the `valuable` and `magic-item` prefixes. An
            [`IdAllocator`][osrlib.core.monsters.IdAllocator].

    Returns:
        The coins, valuables, and magic items. See
        [`GeneratedTreasure`][osrlib.core.items.GeneratedTreasure]. An unlucky hoard can be
        empty.

    Raises:
        ValueError: If no treasure type has that letter, or if `tier` is neither
            `"basic"` nor `"expert"`. Both are checked before the first draw, so a refused
            call costs no draws and returns no hoard.

    Examples:
        ```python
        from osrlib.core.monsters import IdAllocator
        from osrlib.core.rng import RngStreams
        from osrlib.core.treasure import TREASURE_STREAM, generate_treasure

        stream = RngStreams(master_seed=1).get(TREASURE_STREAM)
        hoard = generate_treasure("A", tier="expert", stream=stream, allocator=IdAllocator())
        assert hoard.coins.total_coins == 7000
        assert len(hoard.valuables) == 19
        assert {item.template_id for item in hoard.magic_items} == {
            "sword_plus_1_plus_3_vs_dragons",
            "ring_of_protection",
            "potion_of_poison",
        }
        ```
    """
    from osrlib.data import load_treasure_tables

    table = load_treasure_tables().treasure_type(treasure_type)
    return generate_treasure_entries(table.entries, tier=tier, stream=stream, allocator=allocator)


def generate_unguarded_treasure(
    dungeon_level: int,
    *,
    tier: str,
    stream: RngStream,
    allocator: Any,
) -> GeneratedTreasure:
    """Generate a cache of treasure nobody is guarding, for a dungeon level.

    The counterpart to [`generate_treasure`][osrlib.core.treasure.generate_treasure]: use it
    when the treasure belongs to the room rather than to a monster, which is what
    [`roll_room_contents`][osrlib.core.treasure.roll_room_contents] reports when an empty or
    trapped room turns out to have something.

    Deeper levels have more. Levels past the deepest printed band use that band, so treasure
    stops getting richer below level 9.

    Args:
        dungeon_level: The dungeon level, counting from 1.
        tier: `"basic"` or `"expert"`, the printed B or X column for magic items.
        stream: The RNG stream to draw from, conventionally
            [`TREASURE_STREAM`][osrlib.core.treasure.TREASURE_STREAM].
        allocator: The id source for generated gems, jewellery, and magic items. An
            [`IdAllocator`][osrlib.core.monsters.IdAllocator].

    Returns:
        The coins, valuables, and magic items. See
        [`GeneratedTreasure`][osrlib.core.items.GeneratedTreasure]. Most caches are coins
        alone.

    Raises:
        ValueError: If `dungeon_level` is below 1, or if `tier` is neither `"basic"` nor
            `"expert"`. Both are checked before the first draw, so a refused call costs no
            draws and returns no cache.

    Examples:
        ```python
        from osrlib.core.monsters import IdAllocator
        from osrlib.core.rng import RngStreams
        from osrlib.core.treasure import TREASURE_STREAM, generate_unguarded_treasure

        stream = RngStreams(master_seed=3).get(TREASURE_STREAM)
        cache = generate_unguarded_treasure(1, tier="basic", stream=stream, allocator=IdAllocator())

        print(cache.coins.sp, cache.coins.value_gp, len(cache.magic_items))
        # 300 30 0
        ```
    """
    from osrlib.data import load_treasure_tables

    band = load_treasure_tables().unguarded.band_for_level(dungeon_level)
    return generate_treasure_entries(band.entries, tier=tier, stream=stream, allocator=allocator)
