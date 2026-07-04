"""The rules tables as data: attack matrix, saves, XP, turning, reaction, encounters.

The combat tables compile from `Combat_Tables.md` and `Awarding_XP.md` (with the
reaction table from `Encounters.md`) into `combat_tables.json` and load as frozen
models via [`load_combat_tables`][osrlib.data.load_combat_tables]. The shipped matrix
is asserted verbatim against the SRD, and its structure is locked as a property: every
printed cell equals `clamp(THAC0 − AC, 2, 20)`. Pinned: AC values outside the printed
−3..9 columns extend by the same formula — the printed bounds are page layout, not a
rules cliff. The clamping is exactly what distinguishes matrix mode from the
`thac0_arithmetic` ruleset flag once modifiers push totals past the plateaus.

The dungeon encounter tables compile from `Dungeon_Encounters.md` into
`encounter_tables.json` and load via
[`load_encounter_tables`][osrlib.data.load_encounter_tables]. Their models live here —
pinned for layering: `osrlib/data/` loaders import their model homes and `core`
modules import the loaders, so a `crawl/` model home would give the loader a
core → data → crawl transitive import. The crawl layer consumes the models; it
doesn't define them.

Monster stat blocks carry explicit THAC0 and save values (already reflecting the
"bonus hit points attack as 1 HD higher" rule), so the HD-keyed lookups here serve
validation, custom monsters, and the save-as resolutions from packed-variant
expansion.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from osrlib.core.classes import SavingThrows
from osrlib.core.dice import parse
from osrlib.core.monsters import MonsterHitDice

__all__ = [
    "TURNING_COLUMNS",
    "AttackMatrix",
    "AttackMatrixRow",
    "CombatTables",
    "EncounterEntry",
    "EncounterTable",
    "EncounterTableRow",
    "EncounterTables",
    "MonsterEncounterEntry",
    "MonsterSaveBand",
    "NpcAlignmentBand",
    "NpcClassLevelRow",
    "NpcPartyComposition",
    "NpcPartyEncounterEntry",
    "ReactionBand",
    "ReactionResult",
    "ReactionTable",
    "TurningResult",
    "TurningRow",
    "TurningTable",
    "XpAwardRow",
    "monster_save_band_label",
    "monster_xp",
    "reaction_result",
    "thac0_for_hd",
    "to_hit_ac",
    "turning_column",
    "xp_band_label",
]

TURNING_COLUMNS = ("1", "2", "2*", "3", "4", "5", "6", "7-9")
"""The turning table's monster-HD column labels, exactly as `Cleric.md` prints them."""

# The attack matrix's HD rows as (max effective HD, THAC0). Effective HD is the count
# plus 1 for a bonus hit-point modifier ("attack as 1 HD higher"); negative modifiers
# keep the unmodified row (pinned — the goblin's 1-1 keeps 19 [0]).
_MATRIX_HD_ROWS = (
    (1, 19),
    (2, 18),
    (3, 17),
    (4, 16),
    (5, 15),
    (6, 14),
    (7, 13),
    (9, 12),
    (11, 11),
    (13, 10),
    (15, 9),
    (17, 8),
    (19, 7),
    (21, 6),
)

_XP_INFLATION_PER_HD_ABOVE_21 = 250


class AttackMatrixRow(BaseModel):
    """One attack matrix row: the monster-HD label, THAC0 both ways, and the cells.

    `by_ac` maps AC −3..9 to the attack roll required to hit it, exactly as printed.
    """

    model_config = ConfigDict(frozen=True)

    hd_label: str
    thac0: int = Field(ge=2, le=20)
    attack_bonus: int = Field(ge=-1)
    by_ac: dict[int, int]

    @model_validator(mode="after")
    def _cells_cover_printed_columns(self) -> AttackMatrixRow:
        if sorted(self.by_ac) != list(range(-3, 10)):
            raise ValueError("attack matrix cells must cover AC -3..9")
        return self


class AttackMatrix(BaseModel):
    """The attack matrix: 16 THAC0 rows from `20 [-1]`/NH to `5 [+14]`."""

    model_config = ConfigDict(frozen=True)

    rows: tuple[AttackMatrixRow, ...]

    @model_validator(mode="after")
    def _rows_descend_by_thac0(self) -> AttackMatrix:
        thac0s = [row.thac0 for row in self.rows]
        if thac0s != sorted(thac0s, reverse=True) or len(set(thac0s)) != len(thac0s):
            raise ValueError("attack matrix rows must have strictly descending THAC0")
        return self


class MonsterSaveBand(BaseModel):
    """One monster saving-throw band.

    `min_hd`/`max_hd` bound the band's Hit Dice; NH is `min_hd=None` (normal humans
    are "less than 1 Hit Die" with their own row) and 22+ is `max_hd=None`.
    """

    model_config = ConfigDict(frozen=True)

    label: str
    min_hd: int | None = None
    max_hd: int | None = None
    saves: SavingThrows


class TurningResult(BaseModel):
    """One turning-table cell, resolved.

    `outcome` follows the table legend: `fail` (—), `number` (succeeds when the 2d6
    turn roll meets `threshold`), `turn` (T — automatic), `destroy` (D — automatic,
    and affected monsters are annihilated rather than turned).
    """

    model_config = ConfigDict(frozen=True)

    outcome: str
    threshold: int | None = None

    @model_validator(mode="after")
    def _threshold_only_on_number(self) -> TurningResult:
        if self.outcome not in ("fail", "number", "turn", "destroy"):
            raise ValueError(f"unknown turning outcome {self.outcome!r}")
        if (self.outcome == "number") != (self.threshold is not None):
            raise ValueError("exactly the 'number' outcome carries a threshold")
        return self


class TurningRow(BaseModel):
    """One cleric level's turning row: cells keyed by the monster-HD column label.

    Cell values are exactly as printed: `—`, a threshold number, `T`, or `D`.
    """

    model_config = ConfigDict(frozen=True)

    label: str
    cells: dict[str, str]

    @model_validator(mode="after")
    def _cells_cover_printed_columns(self) -> TurningRow:
        if tuple(self.cells) != TURNING_COLUMNS:
            raise ValueError(f"turning row {self.label!r} must cover columns {TURNING_COLUMNS} in order")
        for column, cell in self.cells.items():
            if cell not in ("—", "T", "D") and not cell.isdigit():
                raise ValueError(f"unparseable turning cell {cell!r} in column {column!r}")
        return self


class TurningTable(BaseModel):
    """The turning-undead table: 11 cleric-level rows (1–10, `11+`) × 8 HD columns."""

    model_config = ConfigDict(frozen=True)

    rows: tuple[TurningRow, ...]

    @model_validator(mode="after")
    def _rows_cover_printed_levels(self) -> TurningTable:
        labels = [row.label for row in self.rows]
        if labels != [*(str(level) for level in range(1, 11)), "11+"]:
            raise ValueError(f"turning rows must cover levels 1-10 and 11+, got {labels}")
        return self

    def result(self, cleric_level: int, column: str) -> TurningResult:
        """Resolve the turning cell for a cleric level and monster-HD column.

        Levels above 10 clamp to the `11+` row (the printed table's own semantics).

        Args:
            cleric_level: The turning cleric's level, 1 or higher.
            column: The monster-HD column label, from
                [`turning_column`][osrlib.core.tables.turning_column].

        Returns:
            The resolved cell.

        Raises:
            ValueError: If the level is below 1 or the column label is unknown.
        """
        if cleric_level < 1:
            raise ValueError(f"cleric level must be positive, got {cleric_level}")
        if column not in TURNING_COLUMNS:
            raise ValueError(f"unknown turning column {column!r}")
        row = self.rows[min(cleric_level, 11) - 1]
        cell = row.cells[column]
        if cell == "—":
            return TurningResult(outcome="fail")
        if cell == "T":
            return TurningResult(outcome="turn")
        if cell == "D":
            return TurningResult(outcome="destroy")
        return TurningResult(outcome="number", threshold=int(cell))


class XpAwardRow(BaseModel):
    """One XP-awards row: the printed HD label, base XP, and bonus XP per ability."""

    model_config = ConfigDict(frozen=True)

    label: str
    base: int = Field(ge=0)
    bonus: int = Field(ge=0)


class ReactionResult(StrEnum):
    """The five monster reaction bands, from `Encounters.md`.

    The wire values are lowercase — they serialize into events and saves; changing
    them is a `schema_version` bump.
    """

    ATTACKS = "attacks"
    HOSTILE = "hostile"
    UNCERTAIN = "uncertain"
    INDIFFERENT = "indifferent"
    FRIENDLY = "friendly"


class ReactionBand(BaseModel):
    """One reaction-table band: the printed 2d6 range and its result.

    `min_total` is `None` for the open "2 or less" band and `max_total` is `None`
    for "12 or more" — the table's own clamping semantics: totals outside the
    printed 2..12 span land in the outer bands.
    """

    model_config = ConfigDict(frozen=True)

    label: str
    text: str
    min_total: int | None = None
    max_total: int | None = None
    result: ReactionResult

    @model_validator(mode="after")
    def _band_must_be_bounded_or_open(self) -> ReactionBand:
        if self.min_total is None and self.max_total is None:
            raise ValueError("a reaction band needs at least one bound")
        if self.min_total is not None and self.max_total is not None and self.min_total > self.max_total:
            raise ValueError(f"reaction band minimum {self.min_total} exceeds maximum {self.max_total}")
        return self


class ReactionTable(BaseModel):
    """The monster reaction table: five contiguous 2d6 bands."""

    model_config = ConfigDict(frozen=True)

    bands: tuple[ReactionBand, ...]

    @model_validator(mode="after")
    def _bands_must_be_contiguous(self) -> ReactionTable:
        if len(self.bands) != 5:
            raise ValueError(f"expected 5 reaction bands, got {len(self.bands)}")
        if self.bands[0].min_total is not None or self.bands[-1].max_total is not None:
            raise ValueError("the outer reaction bands must be open (2 or less / 12 or more)")
        for previous, band in zip(self.bands, self.bands[1:], strict=False):
            if previous.max_total is None or band.min_total != previous.max_total + 1:
                raise ValueError("reaction bands must be contiguous in 2d6 order")
        return self


def reaction_result(table: ReactionTable, total: int) -> ReactionResult:
    """Resolve a 2d6 reaction total against the table.

    Totals below 2 and above 12 clamp into the outer bands — the table's own
    "2 or less" / "12 or more" semantics, so a CHA-modified total of 1 or 14 is
    never out of range.

    Args:
        table: The loaded reaction table.
        total: The modified 2d6 total.

    Returns:
        The reaction result.
    """
    for band in table.bands:
        if band.min_total is not None and total < band.min_total:
            continue
        if band.max_total is not None and total > band.max_total:
            continue
        return band.result
    raise ValueError(f"no reaction band covers total {total}")  # unreachable on a valid table


class MonsterEncounterEntry(BaseModel):
    """An encounter-table cell resolving to monster template ids.

    A single id is the common case. Multiple ids are a packed-variant pool
    (`Veteran` over `veteran_1..3`): at spawn time each individual picks uniformly
    from the pool on the wandering stream — pinned (RAW leaves the pick to the
    referee; per-individual uniform is deterministic and matches the pages' printed
    spreads). `variant_dice` marks the hydra form: the printed HD dice roll once on
    the wandering stream and the total selects the template — `monster_ids` are
    ordered so the dice minimum maps to index 0.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["monster"] = "monster"
    monster_ids: tuple[str, ...] = Field(min_length=1)
    variant_dice: str | None = None

    @model_validator(mode="after")
    def _variant_dice_span_matches_pool(self) -> MonsterEncounterEntry:
        if self.variant_dice is None:
            return self
        dice = parse(self.variant_dice)
        if dice.multiplier != 1:
            raise ValueError("variant dice may not carry a multiplier")
        span = dice.count * (dice.sides - 1) + 1
        if span != len(self.monster_ids):
            raise ValueError(f"variant dice {self.variant_dice!r} spans {span} values for {len(self.monster_ids)} ids")
        return self


class NpcPartyEncounterEntry(BaseModel):
    """An NPC adventuring party cell (Basic/Expert Adventurers).

    Compiled faithfully as structured entries; the parties themselves are built in
    Phase 5 (they need treasure types and magic items) — until then the wandering
    procedure re-rolls these rows, draws consumed.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["npc_party"] = "npc_party"
    party_kind: Literal["basic", "expert"]


EncounterEntry = Annotated[
    MonsterEncounterEntry | NpcPartyEncounterEntry,
    Field(discriminator="kind"),
]
"""Any encounter-table entry, discriminated by `kind`."""


class EncounterTableRow(BaseModel):
    """One d20 row of a dungeon encounter table.

    `name` is the printed cell name (post-override normalization). The count is the
    table's own parenthesized value and overrides the monster description's
    number-appearing — pinned per the SRD's note and the spec: `count_dice` carries
    dice forms and `count_fixed` the printed plain `1`, exactly one of the two.
    """

    model_config = ConfigDict(frozen=True)

    roll: int = Field(ge=1, le=20)
    name: str = Field(min_length=1)
    entry: EncounterEntry
    count_dice: str | None = None
    count_fixed: int | None = None

    @field_validator("count_dice")
    @classmethod
    def _dice_must_parse(cls, value: str | None) -> str | None:
        if value is not None:
            parse(value)
        return value

    @model_validator(mode="after")
    def _dice_or_fixed(self) -> EncounterTableRow:
        if (self.count_dice is None) == (self.count_fixed is None):
            raise ValueError("exactly one of count_dice or count_fixed is required")
        return self


class EncounterTable(BaseModel):
    """One dungeon level column: twenty d20 rows.

    `min_level`/`max_level` bound the printed band (`max_level=None` is the open
    `8+` column).
    """

    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    min_level: int = Field(ge=1)
    max_level: int | None = None
    rows: tuple[EncounterTableRow, ...]
    overrides_applied: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _rows_cover_the_d20(self) -> EncounterTable:
        if [row.roll for row in self.rows] != list(range(1, 21)):
            raise ValueError(f"table {self.id!r} rows must cover d20 rolls 1-20 in order")
        if self.max_level is not None and self.max_level < self.min_level:
            raise ValueError(f"table {self.id!r} level band is inverted")
        return self


class NpcClassLevelRow(BaseModel):
    """One d8 row of the *NPC Adventurer Class and Level* table.

    Rows are keyed by die result — results 4 and 5 are both Fighter with different
    Expert level dice, per the survey.
    """

    model_config = ConfigDict(frozen=True)

    roll: int = Field(ge=1, le=8)
    class_id: str
    basic_dice: str
    expert_dice: str

    @field_validator("basic_dice", "expert_dice")
    @classmethod
    def _dice_must_parse(cls, value: str) -> str:
        parse(value)
        return value


class NpcAlignmentBand(BaseModel):
    """One d6 band of the *NPC Adventurer Alignment* table."""

    model_config = ConfigDict(frozen=True)

    roll_min: int = Field(ge=1, le=6)
    roll_max: int = Field(ge=1, le=6)
    alignment: str


class NpcPartyComposition(BaseModel):
    """One party kind's printed composition dice (Basic 1d4+4, Expert 1d6+3)."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["basic", "expert"]
    count_dice: str

    @field_validator("count_dice")
    @classmethod
    def _dice_must_parse(cls, value: str) -> str:
        parse(value)
        return value


class EncounterTables(BaseModel):
    """The six dungeon encounter tables plus the NPC adventurer generation tables."""

    model_config = ConfigDict(frozen=True)

    tables: tuple[EncounterTable, ...]
    npc_class_levels: tuple[NpcClassLevelRow, ...] = ()
    npc_alignment: tuple[NpcAlignmentBand, ...] = ()
    npc_compositions: tuple[NpcPartyComposition, ...] = ()

    @model_validator(mode="after")
    def _bands_must_be_contiguous_from_one(self) -> EncounterTables:
        expected_min = 1
        for table in self.tables[:-1]:
            if table.min_level != expected_min or table.max_level is None:
                raise ValueError("encounter table level bands must be contiguous from 1")
            expected_min = table.max_level + 1
        last = self.tables[-1]
        if last.min_level != expected_min or last.max_level is not None:
            raise ValueError("the last encounter table band must be open-ended")
        return self

    def for_level(self, level: int) -> EncounterTable:
        """Return the table for a dungeon level, clamped into the printed bands.

        Pinned: a dungeon level's table is its level number clamped into the printed
        bands — 1, 2, 3, 4–5, 6–7, and everything 8 or deeper on `8+`.

        Args:
            level: The dungeon level number, 1-based.

        Returns:
            The table for that level.

        Raises:
            ValueError: If `level` is below 1.
        """
        if level < 1:
            raise ValueError(f"dungeon levels are 1-based, got {level}")
        for table in self.tables:
            if table.max_level is None or level <= table.max_level:
                return table
        raise ValueError(f"no encounter table covers level {level}")  # unreachable on a valid catalog


class CombatTables(BaseModel):
    """The loaded combat tables."""

    model_config = ConfigDict(frozen=True)

    attack_matrix: AttackMatrix
    monster_saves: tuple[MonsterSaveBand, ...]
    xp_awards: tuple[XpAwardRow, ...]
    turning: TurningTable
    reaction: ReactionTable

    def save_band(self, label: str) -> MonsterSaveBand:
        """Return the monster save band with `label`.

        Args:
            label: The band label, e.g. `"NH"` or `"4–6"`.

        Returns:
            The band.

        Raises:
            ValueError: If no band has that label.
        """
        for band in self.monster_saves:
            if band.label == label:
                return band
        raise ValueError(f"unknown monster save band {label!r}")

    def xp_row(self, label: str) -> XpAwardRow:
        """Return the XP-awards row with `label`.

        Args:
            label: The row label, e.g. `"2+"` or `"7–7+"`.

        Returns:
            The row.

        Raises:
            ValueError: If no row has that label.
        """
        for row in self.xp_awards:
            if row.label == label:
                return row
        raise ValueError(f"unknown XP award row {label!r}")


def to_hit_ac(thac0: int, ac: int) -> int:
    """Return the attack roll required to hit `ac` under the attack matrix.

    Pinned: every printed matrix cell equals `clamp(THAC0 − AC, 2, 20)` (locked by a
    property test against the shipped table), and AC values outside the printed −3..9
    columns extend by the same formula — the printed bounds are page layout, not a
    rules cliff.

    Args:
        thac0: The attacker's THAC0.
        ac: The defender's descending armour class.

    Returns:
        The required roll, clamped to 2..20.
    """
    return max(2, min(20, thac0 - ac))


def thac0_for_hd(count: int, *, bonus_modifier: bool = False) -> tuple[int, int]:
    """Return the attack-matrix THAC0 (and attack bonus) for a monster's Hit Dice.

    Bonus hit-point modifiers attack as 1 HD higher; negative modifiers keep the
    unmodified row (pinned). Fractional and sub-1 HD use the "Up to 1" row (19 [0]).
    Monster stat blocks carry printed THAC0 — this lookup serves validation, custom
    monsters, and drained instances re-deriving from reduced HD.

    Args:
        count: The Hit Dice count.
        bonus_modifier: True when the HD carry a positive hit-point modifier.

    Returns:
        The `(thac0, attack_bonus)` pair.
    """
    effective = max(1, count) + (1 if bonus_modifier else 0)
    for max_hd, thac0 in _MATRIX_HD_ROWS:
        if effective <= max_hd:
            return thac0, 19 - thac0
    return 5, 14


def monster_save_band_label(hit_dice: MonsterHitDice) -> str:
    """Return the monster saving-throw band label for a monster's Hit Dice.

    Bonus hit-point modifiers round the effective HD up (a 6+3 troll saves as 6 —
    the printed bands are whole numbers and the SRD's stat blocks agree); fractional
    and fixed-hp forms save as NH... except that the SRD prints per-block save-as
    notes, which ship on every stat block — this lookup serves custom monsters and
    expansion resolutions.

    Args:
        hit_dice: The monster's Hit Dice.

    Returns:
        The band label: `"NH"`, `"1–3"`, ... `"22 or more"`.
    """
    if hit_dice.count < 1 or hit_dice.die == 4:
        return "NH"
    hd = hit_dice.count
    if hd <= 3:
        return "1–3"
    if hd <= 6:
        return "4–6"
    if hd <= 9:
        return "7–9"
    if hd <= 12:
        return "10–12"
    if hd <= 15:
        return "13–15"
    if hd <= 18:
        return "16–18"
    if hd <= 21:
        return "19–21"
    return "22 or more"


def turning_column(hit_dice: MonsterHitDice) -> str | None:
    """Return the turning-table column for a monster's Hit Dice, or `None` above 9.

    Pinned: the column is the HD *count* as a string, except count 2 with a special
    ability (`asterisks > 0`) maps to `2*` (the table's own footnote), counts 7–9
    share `7-9`, and counts above 9 have no column — turning fails against them (the
    printed table is the rule; the "referee may expand" footnote is game data
    territory). Modifiers don't shift columns (the mummy's 5+1 turns on column 5),
    and the asterisk matters only at count 2 (the wight's `3*` is column 3).

    Args:
        hit_dice: The monster's Hit Dice.

    Returns:
        The column label, or `None` when the monster is beyond the printed table.
    """
    count = max(1, hit_dice.count)
    if count == 2 and hit_dice.asterisks > 0:
        return "2*"
    if count <= 6:
        return str(count)
    if count <= 9:
        return "7-9"
    return None


def xp_band_label(hit_dice: MonsterHitDice) -> str:
    """Return the XP-awards row label for a monster's Hit Dice.

    Pinned: negative hit-point modifiers map to the *lower* band — the goblin's 1-1
    HD awards from the "Less than 1" row (the "attack as 1 HD higher" rule is for
    bonus modifiers only). Fractional and fixed-hp forms are "Less than 1". Above 21
    HD every monster lands on the "21–21+" row and inflation applies (see
    [`monster_xp`][osrlib.core.tables.monster_xp]).

    Args:
        hit_dice: The monster's Hit Dice.

    Returns:
        The row label, e.g. `"Less than 1"`, `"2+"`, or `"9–10+"`.
    """
    if hit_dice.die == 4:
        return "Less than 1"
    count = hit_dice.count
    plus = hit_dice.modifier > 0
    if hit_dice.modifier < 0:
        count -= 1
        plus = True
    if count < 1:
        return "Less than 1"
    if count >= 21:
        return "21–21+"
    if count <= 6:
        return f"{count}+" if plus else str(count)
    if count <= 7:
        return "7–7+"
    if count <= 8:
        return "8–8+"
    if count <= 10:
        return "9–10+"
    if count <= 12:
        return "11–12+"
    if count <= 16:
        return "13–16+"
    return "17–20+"


def monster_xp(tables: CombatTables, hit_dice: MonsterHitDice) -> int:
    """Return the XP award for defeating a monster with the given Hit Dice.

    Base XP by HD row plus the asterisk count times the bonus column. Above 21 HD,
    *both* the base and the bonus amounts first gain 250 per HD above 21 ("add 250 XP
    to the Base and Bonus amounts") — the dragon turtle (HD 30*, XP 9,000) proves the
    reading: (2,500 + 9×250) + 1 × (2,000 + 9×250) = 9,000.

    Args:
        tables: The loaded combat tables.
        hit_dice: The monster's Hit Dice, including the asterisk count.

    Returns:
        The XP award.
    """
    row = tables.xp_row(xp_band_label(hit_dice))
    base, bonus = row.base, row.bonus
    if hit_dice.count > 21:
        inflation = (hit_dice.count - 21) * _XP_INFLATION_PER_HD_ABOVE_21
        base += inflation
        bonus += inflation
    return base + hit_dice.asterisks * bonus
