"""Treasure tables and generation: types A–V, gems, jewellery, and magic item rolls.

The treasure tables compile from `Treasure_Types.md`, `Gems_and_Jewellery.md`,
`Magic_Items_%28General%29.md`, and `Designing_a_Dungeon.md` into `treasure.json` and
load as frozen models via [`load_treasure_tables`][osrlib.data.load_treasure_tables].
The models live here — the `core/tables.py` layering precedent: loaders import their
model homes, `core` imports the loaders, `crawl` consumes.

Every treasure-type entry parses on one fixed grammar: an optional `NN%: ` presence
gate, then a coin quantity (dice with an optional `× K` multiplier folded into the
dice expression and a glued denomination suffix), a gem or jewellery count, or a
structured magic-item clause. Magic clauses are never free text —
[`MagicAllotment`][osrlib.core.treasure.MagicAllotment] carries the any/category/pool
kinds, exclusions, and fixed or diced counts.

Generation (Phase 5 work item 3) draws on the
[`TREASURE_STREAM`][osrlib.core.treasure.TREASURE_STREAM] stream in printed-entry
order: presence roll, then quantity dice, then per-item resolution depth-first, so
results are reproducible from the stream alone.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from osrlib.core.dice import parse

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
    "StockingRow",
    "StockingTable",
    "TreasureEntry",
    "TreasureSection",
    "TreasureTables",
    "TreasureTypeTable",
    "UnguardedTreasureBand",
    "UnguardedTreasureTable",
]

TREASURE_STREAM = "treasure"
"""Stream key convention for treasure-generation draws: presence, quantities, items."""


class TreasureSection(StrEnum):
    """Which section of the treasure-type tables a letter belongs to.

    The wire values are lowercase — they serialize into `treasure.json`; changing
    them is a `schema_version` bump. Hoards (A–O) are lair treasure, individual
    letters (P–T) generate once per monster, and group letters (U–V) once per group.
    """

    HOARD = "hoard"
    INDIVIDUAL = "individual"
    GROUP = "group"


class CoinDenomination(StrEnum):
    """The five coin denominations, as the treasure tables print them."""

    PP = "pp"
    GP = "gp"
    EP = "ep"
    SP = "sp"
    CP = "cp"


class MagicItemType(StrEnum):
    """The eight magic item types of the master *Magic Item Type* table.

    `rod_staff_wand` and `scroll` each cover one printed row ("Rod / Staff / Wand",
    "Scroll or Map") and one generation sub-table.
    """

    ARMOUR = "armour"
    MISC = "misc"
    POTION = "potion"
    RING = "ring"
    ROD_STAFF_WAND = "rod_staff_wand"
    SCROLL = "scroll"
    SWORD = "sword"
    WEAPON = "weapon"


class CoinQuantity(BaseModel):
    """A coin entry's quantity: the dice (multiplier folded in) and the denomination."""

    model_config = ConfigDict(frozen=True)

    denomination: CoinDenomination
    dice: str

    @field_validator("dice")
    @classmethod
    def _dice_must_parse(cls, value: str) -> str:
        parse(value)
        return value


class MagicAllotment(BaseModel):
    """One structured magic-item clause of a treasure entry.

    `kind="any"` rolls the master type table per item (re-rolling `exclude`d
    categories, draws consumed); `kind="category"` rolls one named sub-table;
    `kind="pool"` picks uniformly among `categories` before the sub-table roll
    (Type B's "magic sword, suit of armour, or weapon"). Exactly one of `count`
    and `count_dice` sizes the allotment.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["any", "category", "pool"]
    categories: tuple[MagicItemType, ...] = ()
    count: int | None = None
    count_dice: str | None = None
    exclude: tuple[MagicItemType, ...] = ()

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
    """One printed entry (bullet) of a treasure type or unguarded-treasure band.

    `chance_pct` is the presence gate (0 = always present). Exactly one payload is
    set: `coins`, `gems_dice`, `jewellery_dice`, or `magic`.
    """

    model_config = ConfigDict(frozen=True)

    chance_pct: int = Field(default=0, ge=0, le=100)
    coins: CoinQuantity | None = None
    gems_dice: str | None = None
    jewellery_dice: str | None = None
    magic: tuple[MagicAllotment, ...] = ()

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
    """One treasure type (A–V): its section, printed average, and entries in order."""

    model_config = ConfigDict(frozen=True)

    letter: str = Field(min_length=1, max_length=1)
    kind: TreasureSection
    average_gp: float = Field(ge=0)
    entries: tuple[TreasureEntry, ...] = Field(min_length=1)


class GemValueBand(BaseModel):
    """One d20 band of the gem value table."""

    model_config = ConfigDict(frozen=True)

    roll_min: int = Field(ge=1, le=20)
    roll_max: int = Field(ge=1, le=20)
    value_gp: int = Field(ge=1)

    @model_validator(mode="after")
    def _band_must_be_ordered(self) -> GemValueBand:
        if self.roll_min > self.roll_max:
            raise ValueError("gem band minimum exceeds maximum")
        return self


class GemValueTable(BaseModel):
    """The d20 gem value table plus the jewellery dice.

    `manual_notes` keeps the referee-discretion prose (damaged jewellery −50%, the
    combining-values option) — nothing in the engine crushes jewellery in 1.0, and
    combining is presentation (registered).
    """

    model_config = ConfigDict(frozen=True)

    bands: tuple[GemValueBand, ...] = Field(min_length=1)
    jewellery_dice: str
    manual_notes: tuple[str, ...] = ()

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
        """Return the gem value for a d20 roll.

        Args:
            roll: The d20 result, 1–20.

        Returns:
            The gem value in gold pieces.

        Raises:
            ValueError: If the roll is outside 1–20.
        """
        for band in self.bands:
            if band.roll_min <= roll <= band.roll_max:
                return band.value_gp
        raise ValueError(f"gem value roll must be 1-20, got {roll}")


class MagicItemTypeRow(BaseModel):
    """One master-table row: the type and its printed d% bands, both tiers.

    `00` reads as 100, so the bands close at 100 exactly.
    """

    model_config = ConfigDict(frozen=True)

    category: MagicItemType
    basic_min: int = Field(ge=1, le=100)
    basic_max: int = Field(ge=1, le=100)
    expert_min: int = Field(ge=1, le=100)
    expert_max: int = Field(ge=1, le=100)


class MagicItemTypeTable(BaseModel):
    """The master *Magic Item Type* table with its Basic and Expert d% columns."""

    model_config = ConfigDict(frozen=True)

    rows: tuple[MagicItemTypeRow, ...] = Field(min_length=1)

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
        """Return the item type a d% roll selects under one tier's column.

        Args:
            roll: The d% result, 1–100.
            tier: `"basic"` or `"expert"`.

        Returns:
            The selected type.

        Raises:
            ValueError: If the tier is unknown or the roll is outside 1–100.
        """
        if tier not in ("basic", "expert"):
            raise ValueError(f"tier must be 'basic' or 'expert', got {tier!r}")
        for row in self.rows:
            if getattr(row, f"{tier}_min") <= roll <= getattr(row, f"{tier}_max"):
                return row.category
        raise ValueError(f"magic item type roll must be 1-100, got {roll}")


class StockingRow(BaseModel):
    """One d6 row of the room-contents stocking table.

    `treasure_chance_in_six` is 0 for the printed `None`.
    """

    model_config = ConfigDict(frozen=True)

    roll_min: int = Field(ge=1, le=6)
    roll_max: int = Field(ge=1, le=6)
    contents: Literal["empty", "monster", "special", "trap"]
    treasure_chance_in_six: int = Field(ge=0, le=6)


class StockingTable(BaseModel):
    """The *Random Dungeon Room Contents* d6 table."""

    model_config = ConfigDict(frozen=True)

    rows: tuple[StockingRow, ...] = Field(min_length=1)

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
            roll: The d6 result, 1–6.

        Returns:
            The selected row.

        Raises:
            ValueError: If the roll is outside 1–6.
        """
        for row in self.rows:
            if row.roll_min <= roll <= row.roll_max:
                return row
        raise ValueError(f"stocking roll must be 1-6, got {roll}")


class UnguardedTreasureBand(BaseModel):
    """One dungeon-level band of the unguarded-treasure table."""

    model_config = ConfigDict(frozen=True)

    label: str = Field(min_length=1)
    min_level: int = Field(ge=1)
    max_level: int = Field(ge=1)
    entries: tuple[TreasureEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _band_must_be_ordered(self) -> UnguardedTreasureBand:
        if self.min_level > self.max_level:
            raise ValueError("unguarded band minimum exceeds maximum")
        return self


class UnguardedTreasureTable(BaseModel):
    """The unguarded-treasure bands, in level order.

    Levels beyond the last printed band clamp into it (the encounter-table
    precedent, pinned): levels 10+ use the 8–9 band.
    """

    model_config = ConfigDict(frozen=True)

    bands: tuple[UnguardedTreasureBand, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _bands_must_be_contiguous_from_one(self) -> UnguardedTreasureTable:
        expected = 1
        for band in self.bands:
            if band.min_level != expected:
                raise ValueError("unguarded bands must be contiguous from level 1")
            expected = band.max_level + 1
        return self

    def band_for_level(self, level: int) -> UnguardedTreasureBand:
        """Return the band for a dungeon level, clamped into the printed bands.

        Args:
            level: The dungeon level number, 1-based.

        Returns:
            The band; levels past the last band use the last band.

        Raises:
            ValueError: If `level` is below 1.
        """
        if level < 1:
            raise ValueError(f"dungeon levels are 1-based, got {level}")
        for band in self.bands:
            if level <= band.max_level:
                return band
        return self.bands[-1]


class TreasureTables(BaseModel):
    """The loaded treasure tables, with treasure-type lookup by letter."""

    model_config = ConfigDict(frozen=True)

    treasure_types: tuple[TreasureTypeTable, ...]
    gems: GemValueTable
    magic_item_types: MagicItemTypeTable
    stocking: StockingTable
    unguarded: UnguardedTreasureTable

    @model_validator(mode="after")
    def _letters_must_be_unique(self) -> TreasureTables:
        letters = [table.letter for table in self.treasure_types]
        if len(set(letters)) != len(letters):
            raise ValueError("treasure type letters must be unique")
        return self

    def treasure_type(self, letter: str) -> TreasureTypeTable:
        """Return the treasure type for `letter`.

        Args:
            letter: The treasure-type letter, e.g. `"A"`.

        Returns:
            The treasure type table.

        Raises:
            ValueError: If no type has that letter.
        """
        for table in self.treasure_types:
            if table.letter == letter:
                return table
        raise ValueError(f"unknown treasure type {letter!r}")
