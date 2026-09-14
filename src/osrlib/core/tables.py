"""The printed rules tables, and the lookups that read an answer out of them.

Two loaders get you the data. [`load_combat_tables`][osrlib.data.load_combat_tables]
gives you a [`CombatTables`][osrlib.core.tables.CombatTables] with the attack matrix, the
monster saving throws, the experience awards, the turning-undead table, and the monster
reaction table. [`load_encounter_tables`][osrlib.data.load_encounter_tables] gives you an
[`EncounterTables`][osrlib.core.tables.EncounterTables] with the wandering-monster table
for each dungeon level, along with the tables that generate an NPC adventuring party.
Both cache, so calling them repeatedly costs nothing.

Then call the lookup you want. [`to_hit_ac`][osrlib.core.tables.to_hit_ac] says what an
attacker must roll, [`reaction_result`][osrlib.core.tables.reaction_result] turns a 2d6
total into how a meeting starts, [`monster_xp`][osrlib.core.tables.monster_xp] says what
a monster is worth, and [`turning_column`][osrlib.core.tables.turning_column] with
[`TurningTable.result`][osrlib.core.tables.TurningTable.result] says whether a cleric
drives the undead off. Most take a monster's
[`MonsterHitDice`][osrlib.core.monsters.MonsterHitDice] rather than a whole monster, so
they work on a stat block you assembled yourself.

In a game run by a [`GameSession`][osrlib.crawl.session.GameSession] you rarely call any
of this, because combat, encounters, and experience awards read the tables for you. Reach
for the module when you're building a tool, checking custom content, or running the rules
without a session.

The attack matrix as shipped matches the SRD cell for cell, and every cell works out to
`THAC0 − AC` kept within 2 to 20. The printed columns run −3 to 9, and an armour class
outside them follows the same arithmetic, so an armour class the page never prints still
has an answer. That clamping is what separates the matrix from the `thac0_arithmetic`
flag on [`Ruleset`][osrlib.core.ruleset.Ruleset], and it shows only once modifiers push a
total past the ends.

A monster's stat block includes its own THAC0 and saving throws, already reflecting the
rule that bonus hit points make a monster attack as though it had one more Hit Die. The
Hit Dice lookups here are therefore for checking a stat block, for a monster you wrote
yourself, and for the forms the shipped data expands into several templates.
"""

# The encounter-table models live here in core rather than in crawl/ because the
# osrlib/data/ loaders import their model homes and core modules import the loaders: a
# crawl/ home would give the loaders a core to data to crawl import chain. The crawl layer
# consumes these models and doesn't define them. The tables themselves compile from the
# SRD markdown sources into combat_tables.json and encounter_tables.json at build time.

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from osrlib.core.classes import SavingThrows
from osrlib.core.dice import parse, roll
from osrlib.core.monsters import MonsterHitDice
from osrlib.core.rng import RngStream

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
    "select_encounter_individuals",
    "thac0_for_hd",
    "to_hit_ac",
    "turning_column",
    "xp_band_label",
]

TURNING_COLUMNS = ("1", "2", "2*", "3", "4", "5", "6", "7-9")
"""The column labels of the turning-undead table, in the order the SRD prints them.

Each label names the Hit Dice of the undead being turned, with `2*` for a two-Hit-Dice
monster that has a special ability and `7-9` covering three counts at once. Undead above 9
Hit Dice have no column and cannot be turned.

[`turning_column`][osrlib.core.tables.turning_column] picks the right label from a
monster's Hit Dice, so read the tuple when you're drawing the table rather than to choose
a column.
"""

# The attack matrix's HD rows as (max effective HD, THAC0). Effective HD is the count plus
# 1 for a bonus hit-point modifier, the "attack as 1 HD higher" rule. A negative modifier
# keeps the unmodified row, so the goblin's 1-1 stays on 19 [0].
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
    """One row of the attack matrix: everything one class of attacker needs to hit.

    Read these to draw the matrix. To find a number to roll, call
    [`to_hit_ac`][osrlib.core.tables.to_hit_ac] instead, which works for any armour class
    rather than only the printed ones.
    """

    model_config = ConfigDict(frozen=True)

    hd_label: str
    """Which attacker the row is for, as the SRD labels it: `"NH"` for a normal human, then Hit Dice bands."""

    thac0: int = Field(ge=2, le=20)
    """The roll this attacker needs to hit armour class 0, which is the number the whole row is derived from."""

    attack_bonus: int = Field(ge=-1)
    """The same attacker written as an ascending-armour-class bonus, which is 19 minus the THAC0."""

    by_ac: dict[int, int]
    """The roll needed against each descending armour class from −3 to 9, exactly as printed.

    Every value is the THAC0 minus the armour class, kept within 2 to 20.
    """

    @model_validator(mode="after")
    def _cells_cover_printed_columns(self) -> AttackMatrixRow:
        if sorted(self.by_ac) != list(range(-3, 10)):
            raise ValueError("attack matrix cells must cover AC -3..9")
        return self


class AttackMatrix(BaseModel):
    """The SRD's attack matrix: what every class of attacker needs to roll against every armour class.

    Read it off [`CombatTables.attack_matrix`][osrlib.core.tables.CombatTables] to show the
    table. The rest of the time call [`to_hit_ac`][osrlib.core.tables.to_hit_ac], which
    gives the same answer for any THAC0 and any armour class.
    """

    model_config = ConfigDict(frozen=True)

    rows: tuple[AttackMatrixRow, ...]
    """The rows, worst attacker first, each one better than the last."""

    @model_validator(mode="after")
    def _rows_descend_by_thac0(self) -> AttackMatrix:
        thac0s = [row.thac0 for row in self.rows]
        if thac0s != sorted(thac0s, reverse=True) or len(set(thac0s)) != len(thac0s):
            raise ValueError("attack matrix rows must have strictly descending THAC0")
        return self


class MonsterSaveBand(BaseModel):
    """One band of the monster saving-throw table, covering a run of Hit Dice.

    Find the band for a monster with
    [`monster_save_band_label`][osrlib.core.tables.monster_save_band_label] and
    [`CombatTables.save_band`][osrlib.core.tables.CombatTables.save_band]. A shipped
    monster already has its own saving throws, so you need this for a monster you
    wrote yourself or to check one you were given.
    """

    model_config = ConfigDict(frozen=True)

    label: str
    """The band as the SRD prints it: `"NH"` for a normal human, then `"1–3"` up to `"22 or more"`."""

    min_hd: int | None = None
    """The lowest Hit Dice count in the band, or None on the normal-human row, which sits below 1 Hit Die."""

    max_hd: int | None = None
    """The highest Hit Dice count in the band, or None on the open top row, which has no ceiling."""

    saves: SavingThrows
    """The five saving throw numbers a monster in this band rolls against."""


class TurningResult(BaseModel):
    """What the turning table says when a cleric of a given level faces undead of a given kind.

    [`TurningTable.result`][osrlib.core.tables.TurningTable.result] returns one. Act on
    `outcome`: with `"number"` you roll 2d6 and compare it with `threshold`, and the other
    three settle the attempt with no roll.
    """

    model_config = ConfigDict(frozen=True)

    outcome: str
    """What happens, as one of four words.

    `"fail"` means the cleric cannot touch these undead. `"number"` means roll 2d6 and
    meet `threshold`. `"turn"` means the undead flee with no roll. `"destroy"` means they
    are annihilated outright rather than driven off.
    """

    threshold: int | None = None
    """The 2d6 total the cleric must reach, on a `"number"` outcome only, and None on the other three."""

    @model_validator(mode="after")
    def _threshold_only_on_number(self) -> TurningResult:
        if self.outcome not in ("fail", "number", "turn", "destroy"):
            raise ValueError(f"unknown turning outcome {self.outcome!r}")
        if (self.outcome == "number") != (self.threshold is not None):
            raise ValueError("exactly the 'number' outcome carries a threshold")
        return self


class TurningRow(BaseModel):
    """One cleric level's row of the turning table, as printed.

    Read these to draw the table. To resolve an attempt, call
    [`TurningTable.result`][osrlib.core.tables.TurningTable.result], which reads the cell
    and hands back something you can act on.
    """

    model_config = ConfigDict(frozen=True)

    label: str
    """The cleric level the row is for, as `"1"` through `"10"`, and `"11+"` for the open top row."""

    cells: dict[str, str]
    """The row's cells, keyed by the column labels in [`TURNING_COLUMNS`][osrlib.core.tables.TURNING_COLUMNS].

    Each value is exactly what the page prints: an em dash for no effect, a number to roll
    against, `T` for an automatic turn, or `D` for automatic destruction.
    """

    @model_validator(mode="after")
    def _cells_cover_printed_columns(self) -> TurningRow:
        if tuple(self.cells) != TURNING_COLUMNS:
            raise ValueError(f"turning row {self.label!r} must cover columns {TURNING_COLUMNS} in order")
        for column, cell in self.cells.items():
            if cell not in ("—", "T", "D") and not cell.isdigit():
                raise ValueError(f"unparseable turning cell {cell!r} in column {column!r}")
        return self


class TurningTable(BaseModel):
    """The turning-undead table: what a cleric of each level can do to undead of each kind.

    Get it from [`CombatTables.turning`][osrlib.core.tables.CombatTables] and call
    [`result`][osrlib.core.tables.TurningTable.result]. Turning is a cleric's own ability,
    so a magic-user or a fighter never reads this table.
    """

    model_config = ConfigDict(frozen=True)

    rows: tuple[TurningRow, ...]
    """One row per cleric level, from level 1 up to the open `11+` row."""

    @model_validator(mode="after")
    def _rows_cover_printed_levels(self) -> TurningTable:
        labels = [row.label for row in self.rows]
        if labels != [*(str(level) for level in range(1, 11)), "11+"]:
            raise ValueError(f"turning rows must cover levels 1-10 and 11+, got {labels}")
        return self

    def result(self, cleric_level: int, column: str) -> TurningResult:
        """Say what happens when this cleric tries to turn these undead.

        Get the column from [`turning_column`][osrlib.core.tables.turning_column], which
        returns None for undead too powerful to be turned at all. There's nothing to look
        up in that case. A cleric above level 10 reads the `11+` row, which is what the
        printed table intends.

        This is the lookup alone. [`turn_undead`][osrlib.core.spells.turn_undead] rolls
        the 2d6 a `"number"` outcome calls for and works out how many undead are
        affected.

        Args:
            cleric_level: The cleric's level, 1 or higher.
            column: A column label from
                [`TURNING_COLUMNS`][osrlib.core.tables.TURNING_COLUMNS].

        Returns:
            What the cell says, ready to act on.

        Raises:
            ValueError: If the level is below 1, or the column label isn't one the table
                prints.

        Examples:
            ```python
            from osrlib.data import load_combat_tables

            turning = load_combat_tables().turning

            # A first-level cleric needs a 7 against one-Hit-Die undead.
            attempt = turning.result(1, "1")
            assert (attempt.outcome, attempt.threshold) == ("number", 7)

            # The same cleric cannot touch three-Hit-Dice undead.
            assert turning.result(1, "3").outcome == "fail"

            # At level 12 the weakest undead are destroyed outright.
            assert turning.result(12, "1").outcome == "destroy"
            ```
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
    """One row of the experience-award table, saying what a monster of that size is worth.

    [`monster_xp`][osrlib.core.tables.monster_xp] does the whole calculation, including the
    special abilities, so read a row directly only to show the table.
    """

    model_config = ConfigDict(frozen=True)

    label: str
    """The Hit Dice band as the SRD prints it, from `"Less than 1"` up to `"21–21+"`.

    A trailing `+` marks a monster whose Hit Dice have a bonus, which is worth more than
    the same count without one.
    """

    base: int = Field(ge=0)
    """The experience a monster in this band is worth before its special abilities are counted."""

    bonus: int = Field(ge=0)
    """The extra experience for each special ability the monster has, which its stat block marks with an asterisk."""


class ReactionResult(StrEnum):
    """How a meeting with a monster starts, from the SRD's encounter rules.

    [`reaction_result`][osrlib.core.tables.reaction_result] returns one from a 2d6 total.
    The result says what the monsters do about the party, and nothing more: fighting,
    talking, and buying them off are what you do next.

    The lowercase values serialize into events and saved games. Changing one is a
    `schema_version` bump, the version stamp that marks a serialized model's shape.
    """

    ATTACKS = "attacks"
    """The monsters attack at once, on a total of 2 or less."""

    HOSTILE = "hostile"
    """The monsters are hostile and may attack, on a total of 3 to 5."""

    UNCERTAIN = "uncertain"
    """The monsters are uncertain and confused, on a total of 6 to 8."""

    INDIFFERENT = "indifferent"
    """The monsters are indifferent and may negotiate, on a total of 9 to 11."""

    FRIENDLY = "friendly"
    """The monsters are eager and friendly, on a total of 12 or more."""


class ReactionBand(BaseModel):
    """One band of the reaction table: a range of 2d6 totals and what it means.

    Read these to show the table. To resolve a roll, call
    [`reaction_result`][osrlib.core.tables.reaction_result].
    """

    model_config = ConfigDict(frozen=True)

    label: str
    """The range as the SRD prints it, such as `"2 or less"`, `"3–5"`, or `"12 or more"`."""

    text: str
    """The SRD's own wording for the band, such as `"Hostile, may attack"`.

    It's the printed English, so use it for a referee's display rather than as text for
    players. Key your own player-facing wording off `result` instead.
    """

    min_total: int | None = None
    """The lowest 2d6 total in the band, or None on the bottom band, which has no floor.

    A charisma modifier can take a total below 2, and such a total still lands here.
    """

    max_total: int | None = None
    """The highest 2d6 total in the band, or None on the top band, which has no ceiling."""

    result: ReactionResult
    """What the band means, as the value to switch on."""

    @model_validator(mode="after")
    def _band_must_be_bounded_or_open(self) -> ReactionBand:
        if self.min_total is None and self.max_total is None:
            raise ValueError("a reaction band needs at least one bound")
        if self.min_total is not None and self.max_total is not None and self.min_total > self.max_total:
            raise ValueError(f"reaction band minimum {self.min_total} exceeds maximum {self.max_total}")
        return self


class ReactionTable(BaseModel):
    """The monster reaction table: how a 2d6 total decides the way a meeting starts.

    Get it from [`CombatTables.reaction`][osrlib.core.tables.CombatTables] and pass it to
    [`reaction_result`][osrlib.core.tables.reaction_result] with your rolled total.
    """

    model_config = ConfigDict(frozen=True)

    bands: tuple[ReactionBand, ...]
    """The five bands, worst reaction first, covering every total with no gap between them."""

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
    """Read a rolled reaction total off the table.

    Roll 2d6, add the party spokesman's charisma modifier from
    [`AbilityTables.npc_reaction_modifier`][osrlib.core.abilities.AbilityTables.npc_reaction_modifier],
    and pass the total here. A modified total below 2 or above 12 is fine: the outer bands
    are open, so nothing falls off the ends of the table.

    Args:
        table: The reaction table, from
            [`CombatTables.reaction`][osrlib.core.tables.CombatTables].
        total: The 2d6 total with any modifier already added.

    Returns:
        How the monsters react.

    Examples:
        ```python
        from osrlib.core.tables import ReactionResult, reaction_result
        from osrlib.data import load_combat_tables

        table = load_combat_tables().reaction

        assert reaction_result(table, 7) is ReactionResult.UNCERTAIN

        # A charismatic spokesman can push the total past the printed top.
        assert reaction_result(table, 14) is ReactionResult.FRIENDLY
        ```
    """
    for band in table.bands:
        if band.min_total is not None and total < band.min_total:
            continue
        if band.max_total is not None and total > band.max_total:
            continue
        return band.result
    raise ValueError(f"no reaction band covers total {total}")  # unreachable on a valid table


class MonsterEncounterEntry(BaseModel):
    """The part of an encounter-table row that says which monsters turn up.

    Turn it into the actual monsters with
    [`select_encounter_individuals`][osrlib.core.tables.select_encounter_individuals],
    which handles all three shapes below, then spawn each id with
    [`spawn_monster`][osrlib.core.monsters.spawn_monster].
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["monster"] = "monster"
    """Always `"monster"`. It tells this entry apart from
    [`NpcPartyEncounterEntry`][osrlib.core.tables.NpcPartyEncounterEntry] when you read a row."""

    monster_ids: tuple[str, ...] = Field(min_length=1)
    """The monster template ids this row can produce, at least one.

    An id names a template in the catalog the game is playing with: one that ships with
    osrlib, from [`load_monsters`][osrlib.data.load_monsters] and listed in
    [the monster id index][monsters-index], or one an adventure brings with it.

    One id is the ordinary case. Several means the printed row covers a spread of one
    monster's forms, such as a veteran at three different levels, and each individual is
    picked from that pool separately. The tabletop game leaves the pick to the referee.
    osrlib rolls it instead, so an encounter comes out the same way on a replay.
    """

    variant_dice: str | None = None
    """A dice expression that picks one form for the whole group, or None when each individual is picked separately.

    The hydra is what this exists for: the printed row rolls its Hit Dice once, and every
    hydra in the group has that many heads. The ids are ordered so the expression's lowest
    total names the first one.
    """

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
    """The part of an encounter-table row that says a rival adventuring party turns up.

    A few rows of the printed tables call for other adventurers rather than monsters.
    Generate the party with [`osrlib.core.npc`][osrlib.core.npc], which rolls its size,
    each member's class and level, and their scores and gear.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["npc_party"] = "npc_party"
    """Always `"npc_party"`. It tells this entry apart from
    [`MonsterEncounterEntry`][osrlib.core.tables.MonsterEncounterEntry] when you read a row."""

    party_kind: Literal["basic", "expert"]
    """Which of the two printed party kinds to generate: `"basic"` for low levels, `"expert"` for high."""


EncounterEntry = Annotated[
    MonsterEncounterEntry | NpcPartyEncounterEntry,
    Field(discriminator="kind"),
]
"""What an encounter-table row produces: either monsters or a rival adventuring party.

Check `kind` to tell which you have, or test the type:

```python
from osrlib.core.tables import MonsterEncounterEntry
from osrlib.data import load_encounter_tables

entry = load_encounter_tables().for_level(1).rows[0].entry
assert entry.kind == "monster"
assert isinstance(entry, MonsterEncounterEntry)
```
"""


class EncounterTableRow(BaseModel):
    """One d20 result on a dungeon encounter table: what appears, and how many.

    Roll a d20, take `rows[roll - 1]`, roll the count, and turn the entry into monsters
    with
    [`select_encounter_individuals`][osrlib.core.tables.select_encounter_individuals].
    """

    model_config = ConfigDict(frozen=True)

    roll: int = Field(ge=1, le=20)
    """The d20 result this row is for, from 1 to 20. Rows are stored in this order."""

    name: str = Field(min_length=1)
    """The name printed in the table's cell, which is what to show the referee."""

    entry: EncounterEntry
    """What turns up: monsters, or a rival adventuring party."""

    count_dice: str | None = None
    """A dice expression for how many appear, or None when the row prints a flat number instead.

    The table's own count wins over the number a monster's description gives, which is
    what the SRD's note about the dungeon tables says to do. Exactly one of this and
    `count_fixed` is set.
    """

    count_fixed: int | None = None
    """A flat number of individuals, or None when the row rolls dice instead.

    Exactly one of this and `count_dice` is set.
    """

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
    """The wandering-monster table for one band of dungeon levels.

    Get the one that fits a level with
    [`EncounterTables.for_level`][osrlib.core.tables.EncounterTables.for_level], then roll
    a d20 and read `rows[roll - 1]`. Deeper levels have nastier tables, which is how the
    rules make depth dangerous.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """The table's identifier, such as `"level_1"` or `"level_8_plus"`."""

    label: str
    """The table's printed heading, such as `"Level 4–5"`."""

    min_level: int = Field(ge=1)
    """The shallowest dungeon level this table covers."""

    max_level: int | None = None
    """The deepest dungeon level this table covers, or None on the last table, which covers everything below."""

    rows: tuple[EncounterTableRow, ...]
    """The twenty rows, in d20 order, so `rows[roll - 1]` is the row for a roll."""

    overrides_applied: tuple[str, ...] = ()
    """Which fields were corrected when this table was compiled from the SRD text, as dotted paths.

    `"rows.1.name"` means the second row's name needed fixing. Empty when nothing did.
    Read it when you're checking osrlib's data against the book.
    """

    @model_validator(mode="after")
    def _rows_cover_the_d20(self) -> EncounterTable:
        if [row.roll for row in self.rows] != list(range(1, 21)):
            raise ValueError(f"table {self.id!r} rows must cover d20 rolls 1-20 in order")
        if self.max_level is not None and self.max_level < self.min_level:
            raise ValueError(f"table {self.id!r} level band is inverted")
        return self


def _dice_minimum(expression: str) -> int:
    """Return the lowest total a dice expression can roll, which is the offset of a variant row's first id."""
    parsed = parse(expression)
    return parsed.count + parsed.modifier


def select_encounter_individuals(entry: MonsterEncounterEntry, count: int, stream: RngStream) -> list[str]:
    """Turn an encounter row into one monster template id per individual that appears.

    Call it once you know how many appear: roll the row's `count_dice` with
    [`roll`][osrlib.core.dice.roll], or take its `count_fixed`. Then spawn each id with
    [`spawn_monster`][osrlib.core.monsters.spawn_monster] to get monsters you can fight.

    Which ids come back depends on the row. A row with one id repeats it. A row with
    several picks for each individual separately, so a group of veterans can come out
    mixed. A row with `variant_dice` rolls once and gives every individual the same form,
    which is how every hydra in a group ends up with the same number of heads.

    Stocking a dungeon and rolling a wandering encounter both come through here, drawing
    in the same order from the same stream, so a room stocked from a row contains what a
    wander onto that row would have produced.

    Args:
        entry: The row's monster entry.
        count: How many individuals appear. Roll it before you call.
        stream: The stream the picks draw from, which advances.

    Returns:
        One template id per individual, in the order they were picked. The list is exactly
        `count` long.

    Examples:
        ```python
        from osrlib.core.rng import RngStreams
        from osrlib.core.tables import select_encounter_individuals
        from osrlib.data import load_encounter_tables

        stream = RngStreams(master_seed=5).get("wandering")

        # A row with one monster repeats it.
        acolytes = load_encounter_tables().for_level(1).rows[0].entry
        assert select_encounter_individuals(acolytes, 3, stream) == ["acolyte", "acolyte", "acolyte"]
        ```
    """
    # A list (never the model's variadic tuple) so index access is unconditioned by
    # length narrowing. The entry model guarantees at least one id.
    ids = list(entry.monster_ids)
    if entry.variant_dice is not None:
        total = roll(entry.variant_dice, stream).total
        return [ids[total - _dice_minimum(entry.variant_dice)]] * count
    if len(ids) > 1:
        return [ids[stream.randbelow(len(ids))] for _ in range(count)]
    return [ids[0]] * count


class NpcClassLevelRow(BaseModel):
    """One d8 result on the table that decides an NPC adventurer's class and level.

    [`osrlib.core.npc`][osrlib.core.npc] rolls on this table for you when it builds a
    party, so read the rows only to show the table or to generate a party your own way.
    """

    model_config = ConfigDict(frozen=True)

    roll: int = Field(ge=1, le=8)
    """The d8 result this row is for. Two results give the same class with different level dice."""

    class_id: str
    """The class this result generates, as an id such as `"cleric"` or `"fighter"`.

    See [the class id index][classes-index].
    """

    basic_dice: str
    """The dice to roll for the NPC's level in a basic party, such as `"1d3"`."""

    expert_dice: str
    """The dice to roll for the NPC's level in an expert party, such as `"1d6+3"`."""

    @field_validator("basic_dice", "expert_dice")
    @classmethod
    def _dice_must_parse(cls, value: str) -> str:
        parse(value)
        return value


class NpcAlignmentBand(BaseModel):
    """One d6 band of the table that decides an NPC adventuring party's alignment."""

    model_config = ConfigDict(frozen=True)

    roll_min: int = Field(ge=1, le=6)
    """The lowest d6 result in this band."""

    roll_max: int = Field(ge=1, le=6)
    """The highest d6 result in this band."""

    alignment: str
    """The alignment this band gives, as the wire value of an
    [`Alignment`][osrlib.core.alignment.Alignment], such as `"lawful"`."""


class NpcPartyComposition(BaseModel):
    """How many adventurers an NPC party of one kind has."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["basic", "expert"]
    """Which kind of party this is, `"basic"` or `"expert"`."""

    count_dice: str
    """The dice to roll for the party's size: `"1d4+4"` for a basic party, `"1d6+3"` for an expert one."""

    @field_validator("count_dice")
    @classmethod
    def _dice_must_parse(cls, value: str) -> str:
        parse(value)
        return value


class EncounterTables(BaseModel):
    """Every dungeon encounter table, and the tables that build a rival adventuring party.

    Get it from [`load_encounter_tables`][osrlib.data.load_encounter_tables]. The way in is
    [`for_level`][osrlib.core.tables.EncounterTables.for_level], which picks the table for
    the dungeon level the party is on.

    A [`GameSession`][osrlib.crawl.session.GameSession] rolls wandering monsters for you,
    and an adventure can bring its own table instead of these, so you reach for this
    directly when you're stocking or wandering outside a session.
    """

    model_config = ConfigDict(frozen=True)

    tables: tuple[EncounterTable, ...]
    """The level tables, shallowest first, together covering every level from 1 down with no gaps."""

    npc_class_levels: tuple[NpcClassLevelRow, ...] = ()
    """The d8 table that gives each NPC adventurer a class and a level."""

    npc_alignment: tuple[NpcAlignmentBand, ...] = ()
    """The d6 table that gives an NPC adventuring party its alignment."""

    npc_compositions: tuple[NpcPartyComposition, ...] = ()
    """How many adventurers a party has, one entry for the basic kind and one for the expert kind."""

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
        """Return the wandering-monster table to roll on at this dungeon level.

        Levels 1, 2, and 3 each have their own table. Levels 4 and 5 share one, 6 and 7
        share another, and everything 8 or deeper rolls on the last, so no level is too
        deep to have a table.

        Args:
            level: The dungeon level, counting from 1 at the top.

        Returns:
            The table for that level.

        Raises:
            ValueError: If `level` is below 1.

        Examples:
            ```python
            from osrlib.data import load_encounter_tables

            tables = load_encounter_tables()
            assert tables.for_level(1).id == "level_1"
            assert tables.for_level(5).id == "level_4_5"

            # Nothing is too deep: the last table has no floor.
            assert tables.for_level(99).id == "level_8_plus"
            ```
        """
        if level < 1:
            raise ValueError(f"dungeon levels are 1-based, got {level}")
        for table in self.tables:
            if table.max_level is None or level <= table.max_level:
                return table
        raise ValueError(f"no encounter table covers level {level}")  # unreachable on a valid catalog


class CombatTables(BaseModel):
    """The five tables combat resolution reads, loaded together.

    Get it from [`load_combat_tables`][osrlib.data.load_combat_tables], then reach into the
    field you want or use one of the lookups in this module, most of which take these
    tables as their first argument.
    """

    model_config = ConfigDict(frozen=True)

    attack_matrix: AttackMatrix
    """What every attacker needs to roll against every armour class."""

    monster_saves: tuple[MonsterSaveBand, ...]
    """The monster saving-throw bands, weakest first. Find one by label with
    [`save_band`][osrlib.core.tables.CombatTables.save_band]."""

    xp_awards: tuple[XpAwardRow, ...]
    """What a defeated monster is worth, by Hit Dice band. Find one by label with
    [`xp_row`][osrlib.core.tables.CombatTables.xp_row]."""

    turning: TurningTable
    """What a cleric can do to undead, by the cleric's level and the undead's Hit Dice."""

    reaction: ReactionTable
    """How a 2d6 total decides the way a meeting with monsters starts."""

    def save_band(self, label: str) -> MonsterSaveBand:
        """Return the monster saving-throw band with this label.

        Get the label from
        [`monster_save_band_label`][osrlib.core.tables.monster_save_band_label] rather than
        writing it out, since the labels use en dashes.

        Args:
            label: A band label such as `"NH"` or `"4–6"`.

        Returns:
            The band, with its five saving throw numbers.

        Raises:
            ValueError: If no band has that label.

        Examples:
            ```python
            from osrlib.core.monsters import MonsterHitDice
            from osrlib.core.tables import monster_save_band_label
            from osrlib.data import load_combat_tables

            tables = load_combat_tables()

            # A troll, at 6+3 Hit Dice, saves on the 4 to 6 band.
            troll = MonsterHitDice(count=6, modifier=3, asterisks=1)
            band = tables.save_band(monster_save_band_label(troll))
            assert band.label == "4–6"
            assert band.saves.death == 10
            ```
        """
        for band in self.monster_saves:
            if band.label == label:
                return band
        raise ValueError(f"unknown monster save band {label!r}")

    def xp_row(self, label: str) -> XpAwardRow:
        """Return the experience-award row with this label.

        Get the label from [`xp_band_label`][osrlib.core.tables.xp_band_label]. To get the
        award itself, call [`monster_xp`][osrlib.core.tables.monster_xp], which reads the
        row and counts the monster's special abilities for you.

        Args:
            label: A row label such as `"2+"` or `"7–7+"`.

        Returns:
            The row, with its base and per-ability amounts.

        Raises:
            ValueError: If no row has that label.

        Examples:
            ```python
            from osrlib.data import load_combat_tables

            row = load_combat_tables().xp_row("2+")
            assert (row.base, row.bonus) == (25, 10)
            ```
        """
        for row in self.xp_awards:
            if row.label == label:
                return row
        raise ValueError(f"unknown XP award row {label!r}")


def to_hit_ac(thac0: int, ac: int) -> int:
    """Return the d20 result an attacker needs to hit this armour class.

    This is the attack matrix as arithmetic, and it gives the printed answer for every
    printed cell. Armour classes past the printed columns follow the same arithmetic, so a
    defender at −7 is handled like any other.

    You rarely call this in play, because
    [`resolve_attack`][osrlib.core.combat.resolve_attack] rolls the attack, applies every
    modifier, and works out whether it hit. Call this to show a player the number they
    need, or to check the matrix.

    The answer is kept within 2 to 20, which is what makes a natural 1 always miss and a
    natural 20 always hit. Turning on `thac0_arithmetic` in
    [`Ruleset`][osrlib.core.ruleset.Ruleset] drops that clamping and uses the plain
    subtraction instead.

    Args:
        thac0: The attacker's THAC0, the roll it needs to hit armour class 0.
        ac: The defender's descending armour class, where lower is better armoured.

    Returns:
        The roll needed, from 2 to 20.

    Examples:
        ```python
        from osrlib.core.tables import to_hit_ac

        # A first-level fighter, THAC0 19, against an unarmoured target.
        assert to_hit_ac(19, 9) == 10

        # Against plate and shield, and against armour the page never prints.
        assert to_hit_ac(19, 2) == 17
        assert to_hit_ac(19, -3) == 20
        ```
    """
    return max(2, min(20, thac0 - ac))


def thac0_for_hd(count: int, *, bonus_modifier: bool = False) -> tuple[int, int]:
    """Return how well a monster of this many Hit Dice attacks.

    A monster that ships with osrlib already has its THAC0, so use this for a monster
    you wrote yourself, to check a stat block you were given, or to work out how well a
    monster attacks after something drained its Hit Dice.

    Bonus hit points make a monster attack as though it had one more Hit Die, which is
    what `bonus_modifier` is for. A negative modifier changes nothing, so the goblin's 1-1
    still attacks as one Hit Die. Anything below one Hit Die attacks on the lowest row.

    Args:
        count: The monster's Hit Dice count.
        bonus_modifier: True when the Hit Dice have a positive hit-point modifier, as the
            troll's 6+3 does.

    Returns:
        The THAC0 and the same thing as an ascending-armour-class bonus, in that order.

    Examples:
        ```python
        from osrlib.core.tables import thac0_for_hd

        assert thac0_for_hd(1) == (19, 0)

        # A 4+1 monster attacks as a 5 Hit Dice one.
        assert thac0_for_hd(4, bonus_modifier=True) == (15, 4)

        # The table tops out, so nothing attacks better than this.
        assert thac0_for_hd(30) == (5, 14)
        ```
    """
    effective = max(1, count) + (1 if bonus_modifier else 0)
    for max_hd, thac0 in _MATRIX_HD_ROWS:
        if effective <= max_hd:
            return thac0, 19 - thac0
    return 5, 14


def monster_save_band_label(hit_dice: MonsterHitDice) -> str:
    """Return which saving-throw band a monster of these Hit Dice belongs to.

    Pass the label to [`CombatTables.save_band`][osrlib.core.tables.CombatTables.save_band]
    to get the numbers. Every monster that ships with osrlib already carries its own saving
    throws, taken from its stat block, so use this for a monster you wrote yourself or to
    check one you were given.

    A bonus hit-point modifier doesn't move a monster up a band, because the bands are
    counted in whole Hit Dice: a troll at 6+3 saves on the band for 4 to 6. A monster below
    one Hit Die, or one with a flat hit-point total, saves as a normal human.

    Args:
        hit_dice: The monster's Hit Dice.

    Returns:
        A band label, from `"NH"` through `"1–3"` up to `"22 or more"`.

    Examples:
        ```python
        from osrlib.core.monsters import MonsterHitDice
        from osrlib.core.tables import monster_save_band_label

        assert monster_save_band_label(MonsterHitDice(count=6, modifier=3)) == "4–6"
        assert monster_save_band_label(MonsterHitDice(count=1, modifier=-1)) == "1–3"

        # Half a Hit Die is a d4, and saves as a normal human.
        assert monster_save_band_label(MonsterHitDice(count=1, die=4)) == "NH"
        ```
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
    """Return which turning-table column undead of these Hit Dice sit in.

    Pass the label to [`TurningTable.result`][osrlib.core.tables.TurningTable.result]. None
    back means the undead are past the end of the printed table and cannot be turned at
    all, so there's nothing to look up and nothing to roll.

    The column is the Hit Dice count, with three exceptions the table prints: a two-Hit-Dice
    monster with a special ability sits in `2*`, counts 7 through 9 share one column, and
    anything above 9 has no column. A hit-point modifier never moves a monster between
    columns, so a mummy's 5+1 turns on column 5, and a special ability matters only at count
    2, so a wight's 3* turns on column 3.

    Args:
        hit_dice: The undead monster's Hit Dice.

    Returns:
        A column label from [`TURNING_COLUMNS`][osrlib.core.tables.TURNING_COLUMNS], or None
        when the monster is beyond the table.

    Examples:
        ```python
        from osrlib.core.monsters import MonsterHitDice
        from osrlib.core.tables import turning_column

        # A skeleton, at 1 Hit Die.
        assert turning_column(MonsterHitDice(count=1)) == "1"

        # A ghoul, at 2 Hit Dice with a special ability, has a column of its own.
        assert turning_column(MonsterHitDice(count=2, asterisks=1)) == "2*"

        # Anything above 9 Hit Dice is past the table and cannot be turned.
        assert turning_column(MonsterHitDice(count=10)) is None
        ```
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
    """Return which experience-award row a monster of these Hit Dice belongs to.

    Call [`monster_xp`][osrlib.core.tables.monster_xp] instead when you want the award
    itself. This is the row lookup behind it, which is what you want when you're drawing
    the table.

    A bonus modifier moves a monster to the `+` version of its row, which is worth more. A
    negative modifier drops it to the row below, so a goblin at 1-1 Hit Dice is awarded from
    the "Less than 1" row. The rule about attacking as one Hit Die higher is for bonuses
    only. Anything below one Hit Die, or with a flat hit-point total, is also "Less than
    1". Above 21 Hit Dice everything lands on the last row, and `monster_xp` adds to it from
    there.

    Args:
        hit_dice: The monster's Hit Dice.

    Returns:
        A row label such as `"Less than 1"`, `"2+"`, or `"9–10+"`.

    Examples:
        ```python
        from osrlib.core.monsters import MonsterHitDice
        from osrlib.core.tables import xp_band_label

        assert xp_band_label(MonsterHitDice(count=2)) == "2"

        # A bonus moves the monster up a row, a penalty down one.
        assert xp_band_label(MonsterHitDice(count=6, modifier=3)) == "6+"
        assert xp_band_label(MonsterHitDice(count=1, modifier=-1)) == "Less than 1"
        ```
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
    """Return the experience a party earns for defeating one monster of these Hit Dice.

    Multiply by how many the party defeated, add the treasure they carried off, and
    divide among the survivors. A session awards experience for you when a battle ends, on
    the schedule the `xp_award_timing` flag on [`Ruleset`][osrlib.core.ruleset.Ruleset]
    sets, so call this when you're tallying a fight yourself or costing out an encounter
    you're designing.

    The award is the row's base amount, plus its per-ability amount for each special
    ability the monster has. Past 21 Hit Dice both amounts grow by 250 for each Hit Die
    above 21, which is what makes a dragon turtle at 30 Hit Dice and one special ability
    worth 9,000.

    Args:
        tables: The combat tables, from
            [`load_combat_tables`][osrlib.data.load_combat_tables].
        hit_dice: The monster's Hit Dice, with its count of special abilities.

    Returns:
        The experience for one monster.

    Examples:
        ```python
        from osrlib.core.monsters import MonsterHitDice
        from osrlib.core.tables import monster_xp
        from osrlib.data import load_combat_tables

        tables = load_combat_tables()

        # A goblin at 1-1 Hit Dice with no special abilities.
        assert monster_xp(tables, MonsterHitDice(count=1, modifier=-1)) == 5

        # A troll at 6+3 with one special ability.
        assert monster_xp(tables, MonsterHitDice(count=6, modifier=3, asterisks=1)) == 650

        # A dragon turtle at 30, where the amounts grow past the end of the table.
        assert monster_xp(tables, MonsterHitDice(count=30, asterisks=1)) == 9000
        ```
    """
    row = tables.xp_row(xp_band_label(hit_dice))
    base, bonus = row.base, row.bonus
    if hit_dice.count > 21:
        inflation = (hit_dice.count - 21) * _XP_INFLATION_PER_HD_ABOVE_21
        base += inflation
        bonus += inflation
    return base + hit_dice.asterisks * bonus
