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

from collections.abc import Sequence
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

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
    "plan_treasure_ref",
    "roll_room_contents",
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


class RoomContentsResult(BaseModel):
    """One à la carte stocking roll: the d6, its row, and the treasure-chance pair.

    `treasure_roll` is `None` when the row's chance is 0 (`None` printed) — no die
    is consumed.
    """

    model_config = ConfigDict(frozen=True)

    roll: int
    row: StockingRow
    treasure_roll: int | None = None
    treasure_present: bool = False


class TreasureRefPlan(BaseModel):
    """A monster's `TreasureRef`, resolved to generation instructions — pinned.

    Letters resolve each by its own section: hoard letters (A–O) are lair treasure,
    individual letters (P–T) generate once per monster, group letters (U–V) once per
    group. Parenthetical letters are lair treasure regardless of section (the
    Bandit's `U (A)`: U carried by the group, A in the lair). `extra_gp` adds flat
    gp to the lair hoard; `multiplier` repeats the whole listed generation that many
    times (the Noble's `V × 3`). `special` labels and `see_below` generate nothing —
    they are content prose for keyed areas (registered), as are the small-lair
    reduction and the referee's manual value adjustment.
    """

    model_config = ConfigDict(frozen=True)

    lair: tuple[str, ...] = ()
    individual: tuple[str, ...] = ()
    group: tuple[str, ...] = ()
    extra_gp: int = 0
    multiplier: int = 1


def plan_treasure_ref(ref: TreasureRef) -> TreasureRefPlan:
    """Resolve a stat block's treasure reference to its generation plan.

    Args:
        ref: The monster template's treasure reference.

    Returns:
        The pinned plan (see [`TreasureRefPlan`][osrlib.core.treasure.TreasureRefPlan]).

    Raises:
        ValueError: If a referenced letter is not a compiled treasure type.
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
    """Roll the stocking d6 and its treasure-chance pair, à la carte.

    Args:
        stream: The RNG stream, conventionally the treasure stream.

    Returns:
        The stocking roll, the row it selected, and the treasure-presence pair
        (no die is consumed for a printed `None` chance).
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


def _generate_valuable(kind: str, *, stream: RngStream, allocator: object) -> ValuableInstance:
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
        weight_coins=weights[kind if kind == "gem" else "jewellery"],
    )


def _generate_sentience(template: object, *, stream: RngStream) -> SwordSentience | None:
    """Roll a magic sword's sentience in the page's pinned procedure order.

    The special-purpose 1-in-20 rolls first per magic sword (a special sword is
    always sentient at INT 12/Ego 12, RAW), otherwise the 30% sentience roll, then
    the printed steps: INT 1d6+6, communication by INT, languages (rolled only for
    speech-capable swords — empathic swords speak nothing, pinned), alignment,
    powers by INT (sensory duplicates re-rolled with draws consumed; extraordinary
    duplicates re-rolled unless the power allows them), and Ego 1d12.
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
    """Roll `count` powers on one table, resolving directives and duplicates.

    Duplicates re-roll with draws consumed; extraordinary powers whose pages allow
    duplicates count each extra roll. The sensory table's `roll_extraordinary`
    directive grants an extraordinary power; `roll_twice`/`roll_thrice` add rolls
    on the same table.
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


def _generate_scroll_spells(template: object, *, tier: str, stream: RngStream) -> dict[str, object]:
    """Roll a spell scroll's contents: the 1-in-4 divine gate, then per-spell levels.

    Each inscribed spell rolls its level on the scroll spell-level table (the tier's
    column) and then picks uniformly among the class list's spells of that level
    (pinned — RAW says "the referee may choose the spells or may roll for them
    randomly", and rolling is the deterministic branch).
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


def generate_magic_item(
    category: MagicItemType | None,
    *,
    tier: str,
    stream: RngStream,
    allocator: object,
    exclude: tuple[MagicItemType, ...] = (),
) -> list[MagicItemInstance]:
    """Generate one magic item allotment: the type roll, the sub-table, the details.

    When `category` is `None`, the master *Magic Item Type* table rolls under the
    tier's d% column, re-rolling excluded categories with draws consumed (the
    wandering-re-roll precedent). `tier` selects the printed B or X probability
    columns (`basic` rolls the sub-table's small die, `expert` its d%). Instance
    details resolve depth-first in a pinned order: the *Magic Armour Type* d8 for
    generic armour outcomes, charges (rolled at creation, referee-only forever
    after — RAW "undiscoverable"), ammunition quantities, wish counts, scroll
    contents, the energy-drain sword's total, and sword sentience last.

    Args:
        category: The master-table type, or `None` to roll it.
        tier: `"basic"` or `"expert"` — the printed B or X columns.
        stream: The treasure stream.
        allocator: The id allocator (`magic-item` prefix).
        exclude: Master-table types an unspecified roll re-rolls.

    Returns:
        The generated instances — one, or two for a paired armour bundle.

    Raises:
        ValueError: If `tier` is unknown or `category` is excluded.
    """
    from osrlib.core.items import MagicItemCategory, MagicItemInstance
    from osrlib.data import load_magic_items, load_treasure_tables

    if tier not in ("basic", "expert"):
        raise ValueError(f"tier must be 'basic' or 'expert', got {tier!r}")
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
    instances: list[MagicItemInstance] = []
    for item_id in row.item_ids:
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
        quantity_dice = row.params.get("quantity_dice", template.quantity_dice)
        if quantity_dice is not None:
            basic_fixed = row.params.get("basic_quantity_fixed")
            if tier == "basic" and basic_fixed is not None:
                instance.quantity = int(basic_fixed)
            else:
                instance.quantity = roll(str(quantity_dice), stream).total
        wish_dice = row.params.get("wish_count_dice", template.params.get("wish_count_dice"))
        if wish_dice is not None:
            instance.state = {**instance.state, "wishes_remaining": roll(str(wish_dice), stream).total}
        if "spell_count" in template.params:
            instance.state = {**instance.state, **_generate_scroll_spells(template, tier=tier, stream=stream)}
        if template.effect is not None and template.effect.kind == "on_hit_drain":
            drains = roll(str(template.effect.params["total_drains_dice"]), stream).total
            instance.state = {**instance.state, "drains_remaining": drains}
        if template.category is MagicItemCategory.SWORD:
            instance.sentience = _generate_sentience(template, stream=stream)
        instances.append(instance)
    return instances


def generate_treasure_entries(
    entries: Sequence[TreasureEntry],
    *,
    tier: str,
    stream: RngStream,
    allocator: object,
) -> GeneratedTreasure:
    """Generate treasure from printed entries, in printed order — the pinned core.

    Per entry: the presence roll (when gated), then the quantity dice, then per-item
    resolution (each gem's value, each jewellery's value, each magic item fully
    depth-first), so results are reproducible from the stream alone. A `pool`
    allotment picks uniformly among its categories before the sub-table roll.

    Args:
        entries: The printed entries (a treasure type's, an unguarded band's, or a
            treasure map's recipe).
        tier: `"basic"` or `"expert"`.
        stream: The treasure stream.
        allocator: The id allocator (`valuable` and `magic-item` prefixes).

    Returns:
        The generated coins, valuables, and magic items.
    """
    from osrlib.core.items import Coins, GeneratedTreasure

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
                count = allotment.count if allotment.count is not None else roll(allotment.count_dice, stream).total
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
    allocator: object,
) -> GeneratedTreasure:
    """Generate one treasure type's contents, à la carte.

    Args:
        treasure_type: The type letter, `"A"` through `"V"`.
        tier: `"basic"` or `"expert"` — the printed B or X magic item columns; the
            crawl passes `basic` while the party's highest living level is 1–3 and
            `expert` at 4+, evaluated at generation time. À la carte callers choose
            explicitly.
        stream: The treasure stream, conventionally
            [`TREASURE_STREAM`][osrlib.core.treasure.TREASURE_STREAM].
        allocator: The id allocator (`valuable` and `magic-item` prefixes).

    Returns:
        The generated coins, valuables, and magic items.

    Raises:
        ValueError: If the letter or tier is unknown.
    """
    from osrlib.data import load_treasure_tables

    table = load_treasure_tables().treasure_type(treasure_type)
    return generate_treasure_entries(table.entries, tier=tier, stream=stream, allocator=allocator)


def generate_unguarded_treasure(
    dungeon_level: int,
    *,
    tier: str,
    stream: RngStream,
    allocator: object,
) -> GeneratedTreasure:
    """Generate an unguarded-treasure cache for a dungeon level.

    The level clamps into the printed bands (the encounter-table precedent, pinned):
    levels 10 and deeper use the 8–9 band.

    Args:
        dungeon_level: The dungeon level number, 1-based.
        tier: `"basic"` or `"expert"`.
        stream: The treasure stream.
        allocator: The id allocator.

    Returns:
        The generated coins, valuables, and magic items.
    """
    from osrlib.data import load_treasure_tables

    band = load_treasure_tables().unguarded.band_for_level(dungeon_level)
    return generate_treasure_entries(band.entries, tier=tier, stream=stream, allocator=allocator)
