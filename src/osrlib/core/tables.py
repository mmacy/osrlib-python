"""The rules tables printed in the SRD, and the functions that look up a result in them.

Two loaders return the tables. [`load_combat_tables`][osrlib.data.load_combat_tables]
returns a [`CombatTables`][osrlib.core.tables.CombatTables] with the attack matrix, the
monster saving throws, the experience awards for monsters, the table for clerics turning
undead, and the monster reaction table.
[`load_encounter_tables`][osrlib.data.load_encounter_tables] returns an
[`EncounterTables`][osrlib.core.tables.EncounterTables] with the wandering monster table for
each dungeon level and the tables for generating an NPC adventuring party. Both loaders cache
what they load, so calling them again costs almost nothing.

The lookups:

- [`to_hit_ac`][osrlib.core.tables.to_hit_ac] returns the d20 roll an attacker needs
  against an armour class.
- [`reaction_result`][osrlib.core.tables.reaction_result] turns a 2d6 reaction total into
  how monsters respond to the party.
- [`EncounterTables.for_level`][osrlib.core.tables.EncounterTables.for_level] returns the
  wandering monster table for a dungeon level, and
  [`select_encounter_individuals`][osrlib.core.tables.select_encounter_individuals] turns a
  row of it into the monster ids that appear.
- [`turning_column`][osrlib.core.tables.turning_column] and
  [`TurningTable.result`][osrlib.core.tables.TurningTable.result] return whether a cleric
  can turn undead.
- [`thac0_for_hd`][osrlib.core.tables.thac0_for_hd],
  [`monster_save_band_label`][osrlib.core.tables.monster_save_band_label], and
  [`monster_xp`][osrlib.core.tables.monster_xp] return a monster's THAC0 (the d20 roll it
  needs to hit armour class 0), saving throw band, and experience value from its Hit Dice.

The monster lookups take a [`MonsterHitDice`][osrlib.core.monsters.MonsterHitDice] rather
than a whole monster, so they work for a monster you write yourself. A monster that comes
with osrlib already has its THAC0 and saving throws, so you need them only to write a
monster or to check one.

In a game run by a [`GameSession`][osrlib.crawl.session.GameSession], you rarely call any
of these, because the session reads the tables for you in combat, encounters, and
experience awards. Use them when you build a tool, check content you wrote, or run the
rules without a session.

The attack matrix that comes with osrlib matches the SRD cell for cell. Every cell is
`THAC0 − AC`, limited to 2 to 20. The printed columns run from −3 to 9, and an armour class
outside them follows the same arithmetic. With the `thac0_arithmetic` flag on
[`Ruleset`][osrlib.core.ruleset.Ruleset] set, the attack functions use `THAC0 − AC` without the
2-to-20 limit. A natural 20 always hits and a natural 1 always misses under both, so the two
give different results only when an attack roll has a modifier and `THAC0 − AC` is below 2 or
above 20.
"""

# The encounter-table models are defined here in core rather than in crawl/. The osrlib/data/
# loaders import the modules that define their models, and core modules import the loaders, so
# models defined in crawl/ would make core import crawl through the loaders. The crawl layer uses
# these models and doesn't define them. The tables are compiled from the SRD's Markdown pages into
# combat_tables.json and encounter_tables.json at build time.

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
"""The column labels of the table for turning undead, in the order the SRD prints them.

Each label is the Hit Dice of the undead being turned. `2*` is for undead of 2 Hit Dice
with a special ability, and `7-9` covers 7, 8, and 9 Hit Dice. Undead above 9 Hit Dice
have no column and can't be turned.

To find the column for a monster, call
[`turning_column`][osrlib.core.tables.turning_column]. Read this tuple to display the table.
"""

# The attack matrix's HD rows as (highest effective HD, THAC0). Effective HD is the count, plus
# 1 when the hit-point modifier is positive, the "attack as 1 HD higher" rule. A negative modifier
# leaves the monster on the row for its count, so the goblin's 1-1 stays on 19 [0].
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
    """One row of the attack matrix: the roll one kind of attacker needs against each armour class.

    Read the rows to display the matrix. To find the roll an attacker needs, call
    [`to_hit_ac`][osrlib.core.tables.to_hit_ac] instead, which works for any armour class,
    not only the printed ones.
    """

    model_config = ConfigDict(frozen=True)

    hd_label: str
    """The attacker the row is for, as the SRD labels it: `"NH"` for a normal human, then ranges of Hit Dice like
    `"1+ to 2"`.
    """

    thac0: int = Field(ge=2, le=20)
    """The roll this attacker needs to hit armour class 0. Every cell in `by_ac` is computed from it."""

    attack_bonus: int = Field(ge=-1)
    """The attacker's bonus for ascending armour class, which is 19 minus `thac0`."""

    by_ac: dict[int, int]
    """The roll needed against each descending armour class from −3 to 9, keyed by armour class, as printed.

    Every value is `thac0` minus the armour class, limited to 2 to 20.
    """

    @model_validator(mode="after")
    def _cells_cover_printed_columns(self) -> AttackMatrixRow:
        if sorted(self.by_ac) != list(range(-3, 10)):
            raise ValueError("attack matrix cells must cover AC -3..9")
        return self


class AttackMatrix(BaseModel):
    """The SRD's attack matrix: the roll each kind of attacker needs against each armour class.

    Read it from [`CombatTables.attack_matrix`][osrlib.core.tables.CombatTables.attack_matrix]
    to display the table. To find a roll, call [`to_hit_ac`][osrlib.core.tables.to_hit_ac],
    which gives the same result for any THAC0 and any armour class.
    """

    model_config = ConfigDict(frozen=True)

    rows: tuple[AttackMatrixRow, ...]
    """The rows, weakest attacker first. Each row's THAC0 is lower than the row before it."""

    @model_validator(mode="after")
    def _rows_descend_by_thac0(self) -> AttackMatrix:
        thac0s = [row.thac0 for row in self.rows]
        if thac0s != sorted(thac0s, reverse=True) or len(set(thac0s)) != len(thac0s):
            raise ValueError("attack matrix rows must have strictly descending THAC0")
        return self


class MonsterSaveBand(BaseModel):
    """One band of the monster saving throw table: the saving throws for a range of Hit Dice.

    Find the band for a monster with
    [`monster_save_band_label`][osrlib.core.tables.monster_save_band_label] and
    [`CombatTables.save_band`][osrlib.core.tables.CombatTables.save_band]. A monster that
    comes with osrlib already has its saving throws, so you need this to write a monster or
    to check one.
    """

    model_config = ConfigDict(frozen=True)

    label: str
    """The band as the SRD prints it: `"NH"` for a normal human, then `"1–3"` and so on up to `"22 or more"`.

    The ranges use an en dash.
    """

    min_hd: int | None = None
    """The lowest Hit Dice count in the band, or `None` on the `"NH"` band, which is below 1 Hit Die."""

    max_hd: int | None = None
    """The highest Hit Dice count in the band, or `None` on the `"22 or more"` band, which has no upper limit."""

    saves: SavingThrows
    """The saving throw targets for a monster in this band."""


class TurningResult(BaseModel):
    """What happens when a cleric of a given level tries to turn undead of a given Hit Dice.

    [`TurningTable.result`][osrlib.core.tables.TurningTable.result] returns one. Act on
    `outcome`. With `"number"`, roll 2d6 and compare the total with `threshold`. The other three
    outcomes have no roll.
    """

    model_config = ConfigDict(frozen=True)

    outcome: str
    """What happens, as one of four strings.

    `"fail"` means the cleric can't turn these undead. `"number"` means the cleric turns them
    on a 2d6 total equal to or higher than `threshold`. `"turn"` means the undead are turned
    with no roll. `"destroy"` means they're destroyed with no roll.
    """

    threshold: int | None = None
    """The 2d6 total the cleric must reach when `outcome` is `"number"`, and `None` for the other outcomes."""

    @model_validator(mode="after")
    def _threshold_only_on_number(self) -> TurningResult:
        if self.outcome not in ("fail", "number", "turn", "destroy"):
            raise ValueError(f"unknown turning outcome {self.outcome!r}")
        if (self.outcome == "number") != (self.threshold is not None):
            raise ValueError("exactly the 'number' outcome carries a threshold")
        return self


class TurningRow(BaseModel):
    """One cleric level's row of the table for turning undead, as printed.

    Read the rows to display the table. To look up an attempt, call
    [`TurningTable.result`][osrlib.core.tables.TurningTable.result], which reads the cell
    and returns a [`TurningResult`][osrlib.core.tables.TurningResult].
    """

    model_config = ConfigDict(frozen=True)

    label: str
    """The cleric level the row is for: `"1"` to `"10"`, or `"11+"` for level 11 and higher."""

    cells: dict[str, str]
    """The row's cells, keyed by the column labels in [`TURNING_COLUMNS`][osrlib.core.tables.TURNING_COLUMNS].

    Each value is what the SRD prints: an em dash (`"—"`) when the cleric can't turn the
    undead, a number for the 2d6 total needed, `"T"` when the undead are turned with no
    roll, or `"D"` when they're destroyed with no roll.
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
    """The table for turning undead: what a cleric of each level can do to undead of each Hit Dice.

    Turning undead is a cleric's power to drive undead away or destroy them. Get the table from
    [`CombatTables.turning`][osrlib.core.tables.CombatTables.turning] and call
    [`result`][osrlib.core.tables.TurningTable.result].
    """

    model_config = ConfigDict(frozen=True)

    rows: tuple[TurningRow, ...]
    """One row for each cleric level from 1 to 10, then the `"11+"` row."""

    @model_validator(mode="after")
    def _rows_cover_printed_levels(self) -> TurningTable:
        labels = [row.label for row in self.rows]
        if labels != [*(str(level) for level in range(1, 11)), "11+"]:
            raise ValueError(f"turning rows must cover levels 1-10 and 11+, got {labels}")
        return self

    def result(self, cleric_level: int, column: str) -> TurningResult:
        """Return what happens when a cleric of this level tries to turn undead in this column.

        Get the column from [`turning_column`][osrlib.core.tables.turning_column]. When it
        returns `None`, the undead have too many Hit Dice for the table, and the attempt fails
        with nothing to look up. For a cleric above level 10, it reads the `"11+"` row.

        This only reads the table. [`turn_undead`][osrlib.core.spells.turn_undead] makes the
        whole attempt: it rolls the 2d6 for a `"number"` outcome and works out how many
        undead are affected.

        Args:
            cleric_level: The cleric's level, 1 or higher.
            column: A column label from
                [`TURNING_COLUMNS`][osrlib.core.tables.TURNING_COLUMNS].

        Returns:
            The result in the cell.

        Raises:
            ValueError: If `cleric_level` is below 1, or `column` isn't a label in
                `TURNING_COLUMNS`.

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
    """One row of the table of experience awards for monsters: what a monster of some Hit Dice is worth.

    [`monster_xp`][osrlib.core.tables.monster_xp] does the whole calculation, including the
    special abilities, so read a row directly only to display the table.
    """

    model_config = ConfigDict(frozen=True)

    label: str
    """The Hit Dice as the SRD prints them, from `"Less than 1"` to `"21–21+"`, like `"2"`, `"2+"`, or `"9–10+"`.

    A `+` after a count is for a monster with a bonus to its hit points, like 2+1, which is
    worth more than the same count with no bonus. The ranges use an en dash.
    """

    base: int = Field(ge=0)
    """The experience points a monster in this row is worth, before its special abilities."""

    bonus: int = Field(ge=0)
    """The extra experience points for each special ability, shown as an asterisk in a stat block, as in 3*."""


class ReactionResult(StrEnum):
    """How monsters respond to the party when they meet, from the SRD's monster reaction table.

    [`reaction_result`][osrlib.core.tables.reaction_result] returns one from a 2d6 total.
    What the party does next, like fighting, talking, or fleeing, is up to the players.

    The values are lowercase strings, like `"hostile"`. They appear in events and saved
    games, so you can store them and compare with them.
    """

    ATTACKS = "attacks"
    """On a total of 2 or less, the monsters attack at once."""

    HOSTILE = "hostile"
    """On a total of 3 to 5, the monsters are hostile and may attack."""

    UNCERTAIN = "uncertain"
    """On a total of 6 to 8, the monsters are uncertain and confused."""

    INDIFFERENT = "indifferent"
    """On a total of 9 to 11, the monsters are indifferent and may talk."""

    FRIENDLY = "friendly"
    """On a total of 12 or more, the monsters are eager and friendly."""


class ReactionBand(BaseModel):
    """One band of the monster reaction table: a range of 2d6 totals and the reaction it gives.

    Read the bands to display the table. To look up a total, call
    [`reaction_result`][osrlib.core.tables.reaction_result].
    """

    model_config = ConfigDict(frozen=True)

    label: str
    """The range as the SRD prints it, like `"2 or less"`, `"3–5"`, or `"12 or more"`. The ranges use an en dash."""

    text: str
    """The SRD's English text for the band, like `"Hostile, may attack"`.

    Show it to a referee. For text that players see, write your own wording for each
    `result` value.
    """

    min_total: int | None = None
    """The lowest 2d6 total in the band, or `None` on the lowest band, which has no lower limit.

    A CHA modifier can take a total below 2, and that total falls in the lowest band.
    """

    max_total: int | None = None
    """The highest 2d6 total in the band, or `None` on the highest band, which has no upper limit."""

    result: ReactionResult
    """The reaction for a total in this band. Act on this value."""

    @model_validator(mode="after")
    def _band_must_be_bounded_or_open(self) -> ReactionBand:
        if self.min_total is None and self.max_total is None:
            raise ValueError("a reaction band needs at least one bound")
        if self.min_total is not None and self.max_total is not None and self.min_total > self.max_total:
            raise ValueError(f"reaction band minimum {self.min_total} exceeds maximum {self.max_total}")
        return self


class ReactionTable(BaseModel):
    """The monster reaction table, which turns a 2d6 total into how monsters respond to the party.

    Get it from [`CombatTables.reaction`][osrlib.core.tables.CombatTables.reaction] and pass
    it to [`reaction_result`][osrlib.core.tables.reaction_result] with a rolled total.
    """

    model_config = ConfigDict(frozen=True)

    bands: tuple[ReactionBand, ...]
    """The bands, from the most hostile reaction to the most friendly. Together they cover every total, with no gaps."""

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
    """Return the reaction on the table for a 2d6 total.

    Roll 2d6. When a character talks to the monsters, add that character's
    [`npc_reaction_modifier`][osrlib.core.character.Character.npc_reaction_modifier]. Pass the
    total here. A total below 2 or above 12 is valid, because the lowest and highest bands
    have no limit. [`roll_reaction`][osrlib.core.combat.roll_reaction] rolls the dice and
    calls this for you.

    Args:
        table: The reaction table, from
            [`CombatTables.reaction`][osrlib.core.tables.CombatTables.reaction].
        total: The 2d6 total with any modifier already added.

    Returns:
        How the monsters respond.

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
    """The entry of an encounter table row for monsters: which monsters can appear.

    Pass it with the number that appear to
    [`select_encounter_individuals`][osrlib.core.tables.select_encounter_individuals], which
    returns one monster id for each. Then create each monster with
    [`spawn_monster`][osrlib.core.monsters.spawn_monster].
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["monster"] = "monster"
    """Always `"monster"`. Check it to tell this entry from an
    [`NpcPartyEncounterEntry`][osrlib.core.tables.NpcPartyEncounterEntry] when you read a row."""

    monster_ids: tuple[str, ...] = Field(min_length=1)
    """The ids of the monster templates this row can give, at least one.

    Each id refers to a monster template: one that comes with osrlib, from
    [`load_monsters`][osrlib.data.load_monsters] and listed in
    [the monster id index](https://mmacy.github.io/osrlib-python/reference/content-ids/monsters/), or one an adventure
    includes.

    Usually there's one id. Several ids mean the printed row covers several forms of one
    monster, like a veteran at three different levels. Unless `variant_dice` is set, osrlib
    chooses the form of each individual separately. The tabletop rules leave that choice to the
    referee. osrlib rolls it instead, so the same seed gives the same encounter.
    """

    variant_dice: str | None = None
    """A dice expression rolled once to choose one form for the whole group, or `None` when osrlib chooses each
    individual's form separately.

    The hydra's row has one. Its Hit Dice are rolled once, and every hydra in the group has that many heads. The
    expression's lowest total gives the first id, the next total the second, and so on.
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
    """The entry of an encounter table row for a party of NPC adventurers instead of monsters.

    Roll the party's size from the row's `count_dice`, which are the same dice as the
    matching entry in
    [`EncounterTables.npc_compositions`][osrlib.core.tables.EncounterTables.npc_compositions].
    Then pass `party_kind` and the size to
    [`generate_npc_party`][osrlib.core.npc.generate_npc_party], which rolls each member's
    class, level, scores, and gear. In a session, the engine does both steps when it resolves a
    [`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty] command.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["npc_party"] = "npc_party"
    """Always `"npc_party"`. Check it to tell this entry from a
    [`MonsterEncounterEntry`][osrlib.core.tables.MonsterEncounterEntry] when you read a row."""

    party_kind: Literal["basic", "expert"]
    """The kind of party to generate: `"basic"` for low-level adventurers, or `"expert"` for higher-level ones."""


EncounterEntry = Annotated[
    MonsterEncounterEntry | NpcPartyEncounterEntry,
    Field(discriminator="kind"),
]
"""The entry of an encounter table row, which is for either monsters or a party of NPC adventurers.

It's a [`MonsterEncounterEntry`][osrlib.core.tables.MonsterEncounterEntry] or an
[`NpcPartyEncounterEntry`][osrlib.core.tables.NpcPartyEncounterEntry]. Check `kind`, which is `"monster"` or
`"npc_party"`, to tell which you have, or test the type:

```python
from osrlib.core.tables import MonsterEncounterEntry
from osrlib.data import load_encounter_tables

entry = load_encounter_tables().for_level(1).rows[0].entry
assert entry.kind == "monster"
assert isinstance(entry, MonsterEncounterEntry)
```
"""


class EncounterTableRow(BaseModel):
    """One d20 result on a wandering monster table: what appears, and how many.

    Roll a d20 and take `rows[roll - 1]`. Roll `count_dice`, or take `count_fixed`, for how
    many appear. For a [`MonsterEncounterEntry`][osrlib.core.tables.MonsterEncounterEntry],
    pass the entry and the count to
    [`select_encounter_individuals`][osrlib.core.tables.select_encounter_individuals]. For an
    [`NpcPartyEncounterEntry`][osrlib.core.tables.NpcPartyEncounterEntry], pass them to
    [`generate_npc_party`][osrlib.core.npc.generate_npc_party].
    """

    model_config = ConfigDict(frozen=True)

    roll: int = Field(ge=1, le=20)
    """The d20 result this row is for, from 1 to 20. A table's rows are in this order."""

    name: str = Field(min_length=1)
    """The name the SRD prints in the row, like `"Acolyte"`, for display."""

    entry: EncounterEntry
    """What appears: monsters, or a party of NPC adventurers. Check its `kind`."""

    count_dice: str | None = None
    """A dice expression for how many appear, like `"1d6"`, or `None` when the row has a fixed number.

    Roll it with [`roll`][osrlib.core.dice.roll]. Use this count rather than the number appearing in the monster's
    description, as the SRD's note on the dungeon tables says. Exactly one of this and `count_fixed` is set.
    """

    count_fixed: int | None = None
    """A fixed number of individuals, or `None` when the row has a dice expression instead.

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
    """The wandering monster table for one dungeon level, or for a range of levels.

    Get the table for a level with
    [`EncounterTables.for_level`][osrlib.core.tables.EncounterTables.for_level], then roll a
    d20 and read `rows[roll - 1]`. The tables for deeper levels have more dangerous monsters.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """The table's id, like `"level_1"`, `"level_4_5"`, or `"level_8_plus"`."""

    label: str
    """The table's printed heading, like `"Level 1"` or `"Level 8+"`, for display."""

    min_level: int = Field(ge=1)
    """The shallowest dungeon level this table is for."""

    max_level: int | None = None
    """The deepest dungeon level this table is for, or `None` on the last table, which is for every deeper level."""

    rows: tuple[EncounterTableRow, ...]
    """A row for each d20 result, in order, so `rows[roll - 1]` is the row for a roll."""

    overrides_applied: tuple[str, ...] = ()
    """The fields osrlib corrected when it compiled this table from the SRD, as dotted paths. Empty when none were.

    `"rows.1.name"` means the name in `rows[1]`, the row for a d20 roll of 2, was corrected. Read it when you check
    osrlib's data against the SRD.
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
    """Return one monster template id for each individual that appears from an encounter table row.

    Call it once you know how many appear: roll the row's `count_dice` with
    [`roll`][osrlib.core.dice.roll], or take its `count_fixed`. Then create each monster
    with [`spawn_monster`][osrlib.core.monsters.spawn_monster].

    The ids depend on the entry. For an entry with one id, the list repeats it. For an entry
    with several ids, this function chooses one for each individual separately, so a group of
    veterans can be of mixed levels. For an entry with `variant_dice`, it rolls once and gives
    every individual the same form, so every hydra in a group has the same number of heads.

    Both the session's wandering monster checks and the dungeon stocking functions call
    this, so a row gives the same monsters from the same stream position either way.

    Args:
        entry: The row's monster entry.
        count: How many individuals appear.
        stream: The stream to draw from, like
            `streams.get(`[`WANDERING_STREAM`][osrlib.crawl.session.WANDERING_STREAM]`)`. It
            advances once for each individual of an entry with several ids, once for the
            `variant_dice` roll, and not at all for an entry with one id.

    Returns:
        One template id for each individual, in the order they were chosen. The list has
        exactly `count` ids.

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
    # A list rather than the model's variadic tuple, so indexing doesn't depend on the type
    # checker narrowing the tuple's length. The entry model guarantees at least one id.
    ids = list(entry.monster_ids)
    if entry.variant_dice is not None:
        total = roll(entry.variant_dice, stream).total
        return [ids[total - _dice_minimum(entry.variant_dice)]] * count
    if len(ids) > 1:
        return [ids[stream.randbelow(len(ids))] for _ in range(count)]
    return [ids[0]] * count


class NpcClassLevelRow(BaseModel):
    """One d8 result on the table that gives an NPC adventurer a class and a level.

    [`generate_npc_party`][osrlib.core.npc.generate_npc_party] rolls on this table for each
    member, so read the rows only to display the table or to generate a party some other
    way.
    """

    model_config = ConfigDict(frozen=True)

    roll: int = Field(ge=1, le=8)
    """The d8 result this row is for. Two results give a fighter, with different level dice for an expert party."""

    class_id: str
    """The id of the class this result gives, like `"cleric"` or `"fighter"`.

    Look the class up with [`load_classes`][osrlib.data.load_classes]`().get(class_id)`, or see
    [the class id index](https://mmacy.github.io/osrlib-python/reference/content-ids/classes/).
    """

    basic_dice: str
    """The dice to roll for the NPC's level in a basic party. It's `"1d3"` on every row."""

    expert_dice: str
    """The dice to roll for the NPC's level in an expert party, like `"1d6+3"`. It depends on the row."""

    @field_validator("basic_dice", "expert_dice")
    @classmethod
    def _dice_must_parse(cls, value: str) -> str:
        parse(value)
        return value


class NpcAlignmentBand(BaseModel):
    """One band of the d6 table that gives an NPC adventuring party its alignment.

    The bands are in
    [`EncounterTables.npc_alignment`][osrlib.core.tables.EncounterTables.npc_alignment]. Roll
    a d6 and take the band whose range from `roll_min` to `roll_max` includes the result.
    The whole party has that alignment.
    [`generate_npc_party`][osrlib.core.npc.generate_npc_party] rolls this for you, so read the
    bands only to display the table or to generate a party some other way.
    """

    model_config = ConfigDict(frozen=True)

    roll_min: int = Field(ge=1, le=6)
    """The lowest d6 result in this band."""

    roll_max: int = Field(ge=1, le=6)
    """The highest d6 result in this band."""

    alignment: str
    """The alignment this band gives, as the value of an
    [`Alignment`][osrlib.core.alignment.Alignment], like `"lawful"`. Pass it to `Alignment` to get
    the enum member: `Alignment("lawful")`."""


class NpcPartyComposition(BaseModel):
    """The dice that give the number of adventurers in an NPC party of one kind.

    Use it when you place an NPC party yourself, rather than from an encounter table row. Find
    the entry for the kind of party in
    [`EncounterTables.npc_compositions`][osrlib.core.tables.EncounterTables.npc_compositions],
    roll its `count_dice` with [`roll`][osrlib.core.dice.roll], and pass the total as `count`
    to [`generate_npc_party`][osrlib.core.npc.generate_npc_party].

    ```python
    from osrlib.core.dice import roll
    from osrlib.core.rng import RngStreams
    from osrlib.data import load_encounter_tables

    stream = RngStreams(master_seed=3).get("encounter")
    composition = next(entry for entry in load_encounter_tables().npc_compositions if entry.kind == "basic")
    size = roll(composition.count_dice, stream).total
    assert 5 <= size <= 8
    ```
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["basic", "expert"]
    """The kind of party, `"basic"` or `"expert"`."""

    count_dice: str
    """The dice to roll for the party's size: `"1d4+4"` for a basic party, and `"1d6+3"` for an expert party."""

    @field_validator("count_dice")
    @classmethod
    def _dice_must_parse(cls, value: str) -> str:
        parse(value)
        return value


class EncounterTables(BaseModel):
    """Every wandering monster table, and the tables for generating an NPC adventuring party.

    Get it from [`load_encounter_tables`][osrlib.data.load_encounter_tables]. Start with
    [`for_level`][osrlib.core.tables.EncounterTables.for_level], which returns the table for
    a dungeon level.

    A [`GameSession`][osrlib.crawl.session.GameSession] rolls wandering monsters for you, and
    an adventure's dungeon level can replace these tables with a different table in
    [`WanderingSpec.table`][osrlib.crawl.dungeon.WanderingSpec.table]. Use this model
    yourself to stock rooms or roll wandering monsters outside a session.
    """

    model_config = ConfigDict(frozen=True)

    tables: tuple[EncounterTable, ...]
    """The wandering monster tables, shallowest first. Together they cover every dungeon level from 1 down."""

    npc_class_levels: tuple[NpcClassLevelRow, ...] = ()
    """The rows of the d8 table that gives each NPC adventurer a class and a level, in d8 order."""

    npc_alignment: tuple[NpcAlignmentBand, ...] = ()
    """The bands of the d6 table that gives an NPC adventuring party its alignment."""

    npc_compositions: tuple[NpcPartyComposition, ...] = ()
    """The dice for how many adventurers a party has: one entry for `"basic"` parties and one for `"expert"`."""

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
        """Return the wandering monster table for this dungeon level.

        Levels 1, 2, and 3 each have a separate table. Levels 4 and 5 share one, and 6 and 7
        share another. Level 8 and every deeper level use the last table.

        Args:
            level: The dungeon level, counting from 1 at the top.

        Returns:
            The table for that level. Roll a d20 and read `rows[roll - 1]` for the
            [`EncounterTableRow`][osrlib.core.tables.EncounterTableRow] that appears. From the row, you get
            how many appear and which function to call next.

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
    """The tables the combat rules read, loaded together.

    Get it from [`load_combat_tables`][osrlib.data.load_combat_tables]. Read the table you
    want from its field, or pass it to [`monster_xp`][osrlib.core.tables.monster_xp]. Pass
    `reaction` to [`reaction_result`][osrlib.core.tables.reaction_result], and call
    [`turning.result`][osrlib.core.tables.TurningTable.result] to look up turning undead.
    """

    model_config = ConfigDict(frozen=True)

    attack_matrix: AttackMatrix
    """The attack matrix: the roll each kind of attacker needs against each armour class."""

    monster_saves: tuple[MonsterSaveBand, ...]
    """The monster saving throw bands, from the fewest Hit Dice to the most. Find one by label with
    [`save_band`][osrlib.core.tables.CombatTables.save_band]."""

    xp_awards: tuple[XpAwardRow, ...]
    """The experience points a defeated monster is worth, one row for each Hit Dice label. Find one by label with
    [`xp_row`][osrlib.core.tables.CombatTables.xp_row]."""

    turning: TurningTable
    """The table for turning undead: what a cleric can do to undead, by the cleric's level and the undead's Hit Dice."""

    reaction: ReactionTable
    """The monster reaction table, which turns a 2d6 total into how monsters respond to the party."""

    def save_band(self, label: str) -> MonsterSaveBand:
        """Return the monster saving throw band with this label.

        Get the label from
        [`monster_save_band_label`][osrlib.core.tables.monster_save_band_label] rather than
        typing it, because the labels use en dashes.

        Args:
            label: A band label like `"NH"` or `"4–6"`.

        Returns:
            The band, with its saving throw targets.

        Raises:
            ValueError: If no band has that label. The message includes the label.

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
        """Return the row of the experience award table with this label.

        Get the label from [`xp_band_label`][osrlib.core.tables.xp_band_label]. For the
        award itself, call [`monster_xp`][osrlib.core.tables.monster_xp], which reads the
        row and counts the monster's special abilities for you.

        Args:
            label: A row label like `"2+"` or `"7–7+"`.

        Returns:
            The row, with its base award and its award for each special ability.

        Raises:
            ValueError: If no row has that label. The message includes the label.

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
    """Return the d20 roll an attacker needs to hit this armour class.

    This is the attack matrix as arithmetic: `thac0 - ac`, limited to 2 to 20. It gives the
    printed value for every printed cell. An armour class outside the printed columns follows
    the same arithmetic, so a defender at armour class −7 has a value too.

    You rarely call this in play, because
    [`resolve_attack`][osrlib.core.combat.resolve_attack] rolls the attack, adds every
    modifier, and works out whether it hit. Call this to show a player the roll they need,
    or to check the matrix.

    With `thac0_arithmetic` on in [`Ruleset`][osrlib.core.ruleset.Ruleset], the attack
    functions use `thac0 - ac` without the 2-to-20 limit instead of this function. A natural
    20 always hits and a natural 1 always misses either way.

    Args:
        thac0: The attacker's THAC0, the roll it needs to hit armour class 0.
        ac: The defender's descending armour class, where lower is better.

    Returns:
        The roll needed, from 2 to 20.

    Examples:
        ```python
        from osrlib.core.tables import to_hit_ac

        # A first-level fighter, THAC0 19, against an unarmoured target.
        assert to_hit_ac(19, 9) == 10

        # Against plate and shield, and against armour class -7, past the printed columns.
        assert to_hit_ac(19, 2) == 17
        assert to_hit_ac(19, -7) == 20
        ```
    """
    return max(2, min(20, thac0 - ac))


def thac0_for_hd(count: int, *, bonus_modifier: bool = False) -> tuple[int, int]:
    """Return the THAC0 and attack bonus of a monster with this many Hit Dice.

    A monster that comes with osrlib already has its THAC0. Use this to write a monster, to
    check a stat block, or to find how well a monster attacks after it loses Hit Dice.

    A monster with bonus hit points, like the troll's 6+3, attacks as though it had one more
    Hit Die. Pass `bonus_modifier=True` for one. A negative modifier changes nothing, so the
    goblin's 1-1 still attacks as 1 Hit Die. A count below 1 attacks as 1 Hit Die.

    Args:
        count: The monster's Hit Dice count.
        bonus_modifier: True when the Hit Dice have a positive hit point modifier, like the
            `+3` in 6+3.

    Returns:
        The THAC0, and the attack bonus for ascending armour class, in that order.

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
    """Return the label of the saving throw band for a monster with these Hit Dice.

    Pass the label to [`CombatTables.save_band`][osrlib.core.tables.CombatTables.save_band]
    for the saving throw targets. Every monster that comes with osrlib already has its
    saving throws from its stat block, so use this to write a monster or to check one.

    A hit point modifier doesn't move a monster to another band, because the bands count
    whole Hit Dice: a troll at 6+3 saves on the band for 4 to 6. A monster saves as a normal
    human, `"NH"`, when its Hit Dice count is below 1, or when its hit die is a d4, which is how
    osrlib's monster data represents half a Hit Die. A monster with a fixed number of hit points
    per Hit Die, like a hydra, saves on the band for its count.

    Args:
        hit_dice: The monster's Hit Dice.

    Returns:
        A band label: `"NH"`, `"1–3"`, and so on up to `"22 or more"`.

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
    """Return the column of the table for turning undead that matches undead with these Hit Dice.

    Pass the label to [`TurningTable.result`][osrlib.core.tables.TurningTable.result]. `None`
    means the undead have too many Hit Dice for the table and can't be turned, so there's
    nothing to look up and nothing to roll.

    The column is the Hit Dice count, with three exceptions in the printed table. Undead of 2
    Hit Dice with a special ability use `2*`. Counts 7, 8, and 9 share `7-9`. A count above 9
    has no column. A count below 1 uses column `1`. A hit point modifier never changes the
    column, so a mummy's 5+1 uses column `5`. A special ability changes the column only at a
    count of 2, so a wight's 3* uses column `3`.

    Args:
        hit_dice: The undead monster's Hit Dice.

    Returns:
        A column label from [`TURNING_COLUMNS`][osrlib.core.tables.TURNING_COLUMNS], or `None`
        when the count is above 9.

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
    """Return the label of the experience award row for a monster with these Hit Dice.

    For the award itself, call [`monster_xp`][osrlib.core.tables.monster_xp] instead, which
    uses this to find the row. Call this to show which row a monster uses, or to pass to
    [`CombatTables.xp_row`][osrlib.core.tables.CombatTables.xp_row].

    A positive hit point modifier moves a monster to the `+` row for its count, which is
    worth more: a 2+1 monster uses `"2+"`. A negative modifier moves it to the `+` row for
    one Hit Die fewer: a 3-1 monster uses `"2+"`, and a goblin at 1-1 uses `"Less than 1"`.
    A monster whose hit die is a d4, which is how osrlib's monster data represents half a Hit
    Die, uses `"Less than 1"` whatever its count, and so does a count below 1. A monster with a
    fixed number of hit points per Hit Die, like a hydra, uses the row for its count. A count of
    21 or more uses the last row, `"21–21+"`, and `monster_xp` adds to its award for each Hit
    Die above 21.

    Args:
        hit_dice: The monster's Hit Dice.

    Returns:
        A row label like `"Less than 1"`, `"2+"`, or `"9–10+"`.

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
    """Return the experience points a party earns for defeating one monster with these Hit Dice.

    Multiply by the number of monsters of that kind the party defeated. Add the value of the
    treasure the party brought back, at one experience point per gold piece. Divide the total
    among the surviving characters and award each share with
    [`apply_xp`][osrlib.core.classes.apply_xp]. A session counts each defeated monster for you
    and pays the experience at the time the `xp_award_timing` flag on
    [`Ruleset`][osrlib.core.ruleset.Ruleset] sets. Call this when you count up a fight
    yourself, or to find what an encounter you write is worth.

    The award is the row's `base`, plus its `bonus` for each special ability the monster has,
    which is the number of asterisks in `hit_dice`. Above 21 Hit Dice, both amounts rise by
    250 for each Hit Die above 21, so a dragon turtle at 30 Hit Dice with one special ability
    is worth 9,000.

    Args:
        tables: The combat tables, from
            [`load_combat_tables`][osrlib.data.load_combat_tables].
        hit_dice: The monster's Hit Dice. Its `asterisks` is the number of special abilities.

    Returns:
        The experience points for one monster.

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
