"""What a character's six ability scores are worth, and what you can do with them.

Start with [`load_ability_tables`][osrlib.data.load_ability_tables], which gives you an
[`AbilityTables`][osrlib.core.abilities.AbilityTables] with the SRD's printed tables.
Pass any of its accessors a score from 3 to 18 and get the number the rules apply: the
melee modifier for strength, the armour class modifier for dexterity, the hit points per
Hit Die for constitution, and so on. A [`Character`][osrlib.core.character.Character]
offers the results it needs as properties, so call the accessors yourself only when you
have a bare score and no character.

The module also has two functions that roll dice.
[`ability_check`][osrlib.core.abilities.ability_check] rolls the SRD's general check for
a task the rules don't otherwise cover, and
[`open_doors_check`][osrlib.core.abilities.open_doors_check] rolls a strength-based
attempt to force a stuck door. Both take an
[`RngStream`][osrlib.core.rng.RngStream].

The rest of the module is the third step of character creation, where a player trades
points between abilities before play.
[`validate_adjustment`][osrlib.core.abilities.validate_adjustment] reports whether a
proposed trade is legal, and [`apply_adjustment`][osrlib.core.abilities.apply_adjustment]
carries it out. [`create_character`][osrlib.core.character.create_character] runs the
whole creation sequence, so call these two only when you build a character step by step
and let a player choose.

The members, in the order you use them:

- [`AbilityScore`][osrlib.core.abilities.AbilityScore]: the six abilities, used as the
  keys of a score dictionary.
- [`AbilityTables`][osrlib.core.abilities.AbilityTables]: takes a score in each accessor
  and returns its modifier or allowance. The table rows are
  [`StrengthRow`][osrlib.core.abilities.StrengthRow] and its siblings.
- [`ability_check`][osrlib.core.abilities.ability_check]: takes a score and a stream, and
  returns an [`AbilityCheckResult`][osrlib.core.abilities.AbilityCheckResult].
- [`open_doors_check`][osrlib.core.abilities.open_doors_check]: takes a chance in 6 and a
  stream, and returns an [`OpenDoorsResult`][osrlib.core.abilities.OpenDoorsResult].
- [`AbilityAdjustment`][osrlib.core.abilities.AbilityAdjustment]: a proposed trade, which
  [`validate_adjustment`][osrlib.core.abilities.validate_adjustment] checks and
  [`apply_adjustment`][osrlib.core.abilities.apply_adjustment] carries out.
- [`MIN_SCORE`][osrlib.core.abilities.MIN_SCORE],
  [`MAX_SCORE`][osrlib.core.abilities.MAX_SCORE], and
  [`ADJUSTMENT_FLOOR`][osrlib.core.abilities.ADJUSTMENT_FLOOR]: the limits on a score.

Scores run 3 to 18. That's what 3d6 can roll, what the SRD's tables list, and what the
adjustment step has to stay inside.

Typical usage:

```python
from osrlib.core.abilities import ability_check
from osrlib.core.rng import RngStreams
from osrlib.data import load_ability_tables

tables = load_ability_tables()

# What a strength of 16 is worth.
assert tables.melee_modifier(16) == 2
assert tables.open_doors_chance(16) == 4

# A check against a dexterity of 13, on a stream you supply.
check = ability_check(13, RngStreams(master_seed=3).get("exploration"))
assert (check.roll, check.success) == (18, False)
```
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from osrlib.core.rng import RngStream
from osrlib.core.validation import Rejection

__all__ = [
    "ADJUSTMENT_FLOOR",
    "MAX_SCORE",
    "MIN_SCORE",
    "AbilityAdjustment",
    "AbilityCheckResult",
    "AbilityScore",
    "AbilityTables",
    "CharismaRow",
    "ConstitutionRow",
    "DexterityRow",
    "IntelligenceRow",
    "Literacy",
    "OpenDoorsResult",
    "PrimeRequisiteRow",
    "ScoreBand",
    "StrengthRow",
    "WisdomRow",
    "ability_check",
    "apply_adjustment",
    "open_doors_check",
    "validate_adjustment",
]

MIN_SCORE = 3
"""The lowest an ability score can be, which is 3.

Three is what three dice showing 1 add up to, and the lowest row of every table here.
Below it there's no rule to apply, so the accessors and
[`ability_check`][osrlib.core.abilities.ability_check] raise `ValueError` instead of
guessing. Use it to bound a slider, or to check a score your own code produced.
"""

MAX_SCORE = 18
"""The highest an ability score can be, which is 18.

Eighteen is what three dice showing 6 add up to, and the top row of every table here.
The points trade at character creation can't raise a score past it. The accessors and
[`ability_check`][osrlib.core.abilities.ability_check] raise `ValueError` above it,
because the tables have no row for a higher score.
"""

ADJUSTMENT_FLOOR = 9
"""The lowest a score may be traded down to during character creation, which is 9.

The floor stops a player from emptying one ability to buy up another. It applies only to
the trade in [`validate_adjustment`][osrlib.core.abilities.validate_adjustment]. A score
rolled below 9 is legal, and the trade can't lower it further.
"""


class AbilityScore(StrEnum):
    """Which of the six abilities a score belongs to.

    Use these as the keys of a score dictionary, which is how every function here and in
    [`osrlib.core.character`][osrlib.core.character] passes a character's abilities
    around. Include all six keys: a [`Character`][osrlib.core.character.Character] refuses a
    score dictionary that's missing one.

    The lowercase values are what characters and saved games store, and they stay fixed
    within a `schema_version`, the version stamp that marks a serialized model's format.
    """

    STR = "str"
    """Strength: melee attack and damage, and the chance to force a stuck door open."""

    INT = "int"
    """Intelligence: how many extra languages a character speaks, and whether they can read and write."""

    WIS = "wis"
    """Wisdom: the modifier on saving throws against magical effects."""

    DEX = "dex"
    """Dexterity: armour class, missile attacks, and initiative under the individual-initiative rule."""

    CON = "con"
    """Constitution: the hit points added to each Hit Die rolled."""

    CHA = "cha"
    """Charisma: how monsters and NPCs react, and how many retainers the character can keep, and how loyal they are."""


class Literacy(StrEnum):
    """How well a character reads and writes, which their intelligence score sets.

    Read it off [`AbilityTables.literacy`][osrlib.core.abilities.AbilityTables.literacy], passing the
    character's intelligence score. No rule in osrlib reads it. What a character who can't read is
    kept from doing, like reading a scroll, a map, or an inscription, is your game's decision.
    """

    ILLITERATE = "illiterate"
    """Cannot read or write, at intelligence 5 or below."""

    BASIC = "basic"
    """Basic literacy, at intelligence 6 to 8: the step the SRD prints between illiterate and literate."""

    LITERATE = "literate"
    """Can read and write the character's native languages, at intelligence 9 or above."""


class ScoreBand(BaseModel):
    """The run of scores one table row covers, like 4 to 5.

    The SRD prints its modifier tables in bands rather than one row per score, and osrlib
    keeps them that way. Each table's row model, like
    [`StrengthRow`][osrlib.core.abilities.StrengthRow], is a band with that row's columns
    added. You read these when you read a whole table off
    [`AbilityTables`][osrlib.core.abilities.AbilityTables], for instance to draw the table
    on screen. To look up a single score, call an accessor instead.
    """

    model_config = ConfigDict(frozen=True)

    min_score: int = Field(ge=MIN_SCORE, le=MAX_SCORE)
    """The lowest score this row covers."""

    max_score: int = Field(ge=MIN_SCORE, le=MAX_SCORE)
    """The highest score this row covers. Equal to `min_score` on a one-score row."""

    @model_validator(mode="after")
    def _band_must_be_ordered(self) -> ScoreBand:
        if self.min_score > self.max_score:
            raise ValueError(f"band minimum {self.min_score} exceeds maximum {self.max_score}")
        return self


class StrengthRow(ScoreBand):
    """One row of the strength table: a band of scores, with the melee modifier and open-doors chance it grants.

    Read the rows from [`AbilityTables.strength`][osrlib.core.abilities.AbilityTables.strength] to draw
    the table. To look up one score, call
    [`melee_modifier`][osrlib.core.abilities.AbilityTables.melee_modifier] or
    [`open_doors_chance`][osrlib.core.abilities.AbilityTables.open_doors_chance] instead.
    """

    melee: int
    """What to add to a melee attack roll and to melee damage. Negative at a low score."""

    open_doors: int = Field(ge=0, le=6)
    """The chance in 6 of forcing a stuck door open.

    Pass it to [`open_doors_check`][osrlib.core.abilities.open_doors_check], which rolls
    the die against it.
    """


class IntelligenceRow(ScoreBand):
    """One row of the intelligence table: a band of scores, with the languages and literacy it grants.

    Read the rows from [`AbilityTables.intelligence`][osrlib.core.abilities.AbilityTables.intelligence] to
    draw the table. To look up one score, call
    [`additional_languages`][osrlib.core.abilities.AbilityTables.additional_languages] or
    [`literacy`][osrlib.core.abilities.AbilityTables.literacy] instead.
    """

    additional_languages: int = Field(ge=0)
    """How many languages beyond the character's native ones they may choose at creation."""

    literacy: Literacy
    """How well the character reads and writes. See [`Literacy`][osrlib.core.abilities.Literacy]."""

    broken_speech: bool = False
    """True only at intelligence 3, where the character speaks their native language brokenly."""


class WisdomRow(ScoreBand):
    """One row of the wisdom table: a band of scores, with the saving throw modifier it grants.

    Wisdom grants no other bonus. Read the rows from
    [`AbilityTables.wisdom`][osrlib.core.abilities.AbilityTables.wisdom] to draw the table. To look up
    one score, call
    [`magic_save_modifier`][osrlib.core.abilities.AbilityTables.magic_save_modifier] instead.
    """

    magic_saves: int
    """What to add to a saving throw against a magical effect. Negative at a low score."""


class DexterityRow(ScoreBand):
    """One row of the dexterity table: a band of scores, with its armour class, missile, and initiative modifiers.

    Read the rows from [`AbilityTables.dexterity`][osrlib.core.abilities.AbilityTables.dexterity] to draw
    the table. To look up one score, call
    [`ac_modifier`][osrlib.core.abilities.AbilityTables.ac_modifier],
    [`missile_modifier`][osrlib.core.abilities.AbilityTables.missile_modifier], or
    [`initiative_modifier`][osrlib.core.abilities.AbilityTables.initiative_modifier] instead.
    """

    ac: int
    """The armour class bonus. Subtract it from a descending armour class, or add it to an ascending one."""

    missile: int
    """What to add to a missile attack roll. It doesn't touch missile damage."""

    initiative: int
    """What to add to an individual initiative roll.

    It applies only when the `individual_initiative` flag on
    [`Ruleset`][osrlib.core.ruleset.Ruleset] is on. Otherwise initiative is rolled once
    for a whole side.
    """


class ConstitutionRow(ScoreBand):
    """One row of the constitution table: a band of scores, with the hit point modifier it grants.

    Constitution grants no other bonus. Read the rows from
    [`AbilityTables.constitution`][osrlib.core.abilities.AbilityTables.constitution] to draw the table. To
    look up one score, call
    [`hit_point_modifier`][osrlib.core.abilities.AbilityTables.hit_point_modifier] instead.
    """

    hit_points: int
    """What to add to each Hit Die rolled, at creation and when a new level adds a die.

    However negative this is, a die gives at least 1 hit point. Levels past name level
    add a fixed number of hit points instead of a die, and this modifier doesn't apply to
    them.
    """


class CharismaRow(ScoreBand):
    """One row of the charisma table: a band of scores, with its reaction modifier and retainer limits.

    Read the rows from [`AbilityTables.charisma`][osrlib.core.abilities.AbilityTables.charisma] to draw
    the table. To look up one score, call
    [`npc_reaction_modifier`][osrlib.core.abilities.AbilityTables.npc_reaction_modifier],
    [`max_retainers`][osrlib.core.abilities.AbilityTables.max_retainers], or
    [`retainer_loyalty`][osrlib.core.abilities.AbilityTables.retainer_loyalty] instead.
    """

    npc_reactions: int
    """What to add to the reaction roll that sets how monsters or NPCs respond when they meet the party."""

    max_retainers: int = Field(ge=0)
    """How many retainers, hired followers, the character may have at once. No rule in osrlib reads it."""

    retainer_loyalty: int = Field(ge=0)
    """The loyalty score a retainer of this character starts with. No rule in osrlib reads it."""


class PrimeRequisiteRow(ScoreBand):
    """One row of the prime requisite table, which sets how fast a character earns experience.

    A prime requisite is the ability a class is built around: wisdom for a cleric,
    strength for a fighter. This table applies to a class with a single prime requisite. A
    class with more than one has its own tiers on its class definition, and
    [`xp_modifier_pct`][osrlib.core.classes.xp_modifier_pct] reads the right one for any
    class.
    """

    xp_modifier_pct: int
    """The percentage added to or taken off experience earned, from −20 at a score of 3 to +10 at 16 or above."""


def _validate_coverage(rows: tuple[ScoreBand, ...], table: str) -> None:
    expected = MIN_SCORE
    for row in rows:
        if row.min_score != expected:
            raise ValueError(f"{table} table bands must cover 3-18 contiguously; expected band start {expected}")
        expected = row.max_score + 1
    if expected != MAX_SCORE + 1:
        raise ValueError(f"{table} table bands must end at {MAX_SCORE}")


class AbilityTables(BaseModel):
    """The SRD's ability tables, and the accessors that read a score out of them.

    Get one from [`load_ability_tables`][osrlib.data.load_ability_tables], which loads the
    tables that come with the package and caches them, so calling it again costs nothing.
    Then call the accessor for the column you want, passing a score from
    [`MIN_SCORE`][osrlib.core.abilities.MIN_SCORE] to
    [`MAX_SCORE`][osrlib.core.abilities.MAX_SCORE]. A score outside that range raises
    `ValueError`, because it's a mistake in your code, not an outcome the rules allow.

    A [`Character`][osrlib.core.character.Character] reads these tables for you and offers
    the results as properties, so use the accessors when you have a bare score and no
    character.

    The fields contain the raw rows. Read them to draw a table, and use the accessors to
    play.

    Examples:
        ```python
        from osrlib.data import load_ability_tables

        tables = load_ability_tables()
        assert tables.melee_modifier(18) == 3
        assert tables.hit_point_modifier(3) == -3

        # The rows are there when you want to show the whole table.
        assert (tables.strength[1].min_score, tables.strength[1].max_score) == (4, 5)
        ```
    """

    model_config = ConfigDict(frozen=True)

    strength: tuple[StrengthRow, ...]
    """The strength table's rows, lowest score first, together covering 3 to 18 with no gaps."""

    intelligence: tuple[IntelligenceRow, ...]
    """The intelligence table's rows, lowest score first, together covering 3 to 18 with no gaps."""

    wisdom: tuple[WisdomRow, ...]
    """The wisdom table's rows, lowest score first, together covering 3 to 18 with no gaps."""

    dexterity: tuple[DexterityRow, ...]
    """The dexterity table's rows, lowest score first, together covering 3 to 18 with no gaps."""

    constitution: tuple[ConstitutionRow, ...]
    """The constitution table's rows, lowest score first, together covering 3 to 18 with no gaps."""

    charisma: tuple[CharismaRow, ...]
    """The charisma table's rows, lowest score first, together covering 3 to 18 with no gaps."""

    prime_requisite: tuple[PrimeRequisiteRow, ...]
    """The prime requisite experience table's rows, lowest score first, together covering 3 to 18 with no gaps."""

    @model_validator(mode="after")
    def _tables_must_cover_all_scores(self) -> AbilityTables:
        for table in ("strength", "intelligence", "wisdom", "dexterity", "constitution", "charisma", "prime_requisite"):
            _validate_coverage(getattr(self, table), table)
        return self

    def _row[RowT: ScoreBand](self, rows: tuple[RowT, ...], score: int) -> RowT:
        if not MIN_SCORE <= score <= MAX_SCORE:
            raise ValueError(f"ability score must be in {MIN_SCORE}-{MAX_SCORE}, got {score}")
        for row in rows:
            if row.min_score <= score <= row.max_score:
                return row
        raise ValueError(f"no band covers score {score}")  # unreachable given coverage validation

    def melee_modifier(self, score: int) -> int:
        """Return what a strength score adds to melee attack rolls and melee damage.

        Missile attacks take the dexterity modifier instead. See
        [`missile_modifier`][osrlib.core.abilities.AbilityTables.missile_modifier].

        Args:
            score: A strength score from 3 to 18.

        Returns:
            The modifier, from −3 at a score of 3 to +3 at 18.

        Raises:
            ValueError: If `score` is outside 3 to 18.

        Examples:
            ```python
            from osrlib.data import load_ability_tables

            tables = load_ability_tables()

            # A strength of 16 adds 2 to a melee attack roll and to its damage.
            assert tables.melee_modifier(16) == 2
            assert tables.melee_modifier(5) == -2
            ```
        """
        return self._row(self.strength, score).melee

    def open_doors_chance(self, score: int) -> int:
        """Return the chance in 6 that a strength score forces a stuck door open.

        Pass the result to
        [`open_doors_check`][osrlib.core.abilities.open_doors_check], which rolls against
        it.

        Args:
            score: A strength score from 3 to 18.

        Returns:
            The chance in 6, from 1 at a score of 3 to 5 at 18.

        Raises:
            ValueError: If `score` is outside 3 to 18.

        Examples:
            ```python
            from osrlib.data import load_ability_tables

            tables = load_ability_tables()

            # A strength of 16 forces a stuck door on a 1 to 4 on a d6.
            assert tables.open_doors_chance(16) == 4
            assert tables.open_doors_chance(9) == 2
            ```
        """
        return self._row(self.strength, score).open_doors

    def additional_languages(self, score: int) -> int:
        """Return how many languages beyond the native ones an intelligence score grants.

        The choices themselves are checked by
        [`validate_extra_languages`][osrlib.core.character.validate_extra_languages],
        which reads this same allowance.

        Args:
            score: An intelligence score from 3 to 18.

        Returns:
            The count, 0 below a score of 13 and up to 3 at 18.

        Raises:
            ValueError: If `score` is outside 3 to 18.

        Examples:
            ```python
            from osrlib.data import load_ability_tables

            tables = load_ability_tables()

            # An intelligence of 16 lets the character pick two languages at creation.
            assert tables.additional_languages(16) == 2
            assert tables.additional_languages(12) == 0
            ```
        """
        return self._row(self.intelligence, score).additional_languages

    def literacy(self, score: int) -> Literacy:
        """Return how well an intelligence score lets a character read and write.

        Args:
            score: An intelligence score from 3 to 18.

        Returns:
            The [`Literacy`][osrlib.core.abilities.Literacy] band for that score.

        Raises:
            ValueError: If `score` is outside 3 to 18.

        Examples:
            ```python
            from osrlib.core.abilities import Literacy
            from osrlib.data import load_ability_tables

            tables = load_ability_tables()
            assert tables.literacy(7) is Literacy.BASIC
            assert tables.literacy(9) is Literacy.LITERATE
            ```
        """
        return self._row(self.intelligence, score).literacy

    def magic_save_modifier(self, score: int) -> int:
        """Return what a wisdom score adds to saving throws against magical effects.

        It applies to magical effects only, not to a saving throw against a trap or a
        dragon's breath.

        Args:
            score: A wisdom score from 3 to 18.

        Returns:
            The modifier, from −3 at a score of 3 to +3 at 18.

        Raises:
            ValueError: If `score` is outside 3 to 18.

        Examples:
            ```python
            from osrlib.data import load_ability_tables

            tables = load_ability_tables()

            # A wisdom of 13 adds 1 to a save against a spell.
            assert tables.magic_save_modifier(13) == 1
            assert tables.magic_save_modifier(3) == -3
            ```
        """
        return self._row(self.wisdom, score).magic_saves

    def ac_modifier(self, score: int) -> int:
        """Return what a dexterity score is worth to armour class.

        The number is a bonus: it's subtracted from a descending armour class, where
        lower is better, and added to an ascending one.
        [`Character.armour_class`][osrlib.core.character.Character.armour_class] applies
        it for you.

        Args:
            score: A dexterity score from 3 to 18.

        Returns:
            The bonus, from −3 at a score of 3 to +3 at 18.

        Raises:
            ValueError: If `score` is outside 3 to 18.

        Examples:
            ```python
            from osrlib.data import load_ability_tables

            tables = load_ability_tables()

            # A dexterity of 17 improves a descending armour class of 7 to 5.
            assert 7 - tables.ac_modifier(17) == 5
            ```
        """
        return self._row(self.dexterity, score).ac

    def missile_modifier(self, score: int) -> int:
        """Return what a dexterity score adds to missile attack rolls.

        It changes the attack roll only. Missile damage takes no ability modifier.

        Args:
            score: A dexterity score from 3 to 18.

        Returns:
            The modifier, from −3 at a score of 3 to +3 at 18.

        Raises:
            ValueError: If `score` is outside 3 to 18.

        Examples:
            ```python
            from osrlib.data import load_ability_tables

            tables = load_ability_tables()
            assert tables.missile_modifier(13) == 1
            assert tables.missile_modifier(6) == -1
            ```
        """
        return self._row(self.dexterity, score).missile

    def initiative_modifier(self, score: int) -> int:
        """Return what a dexterity score adds to an individual initiative roll.

        It is read only when the `individual_initiative` flag on
        [`Ruleset`][osrlib.core.ruleset.Ruleset] is on. With the flag off, initiative is
        rolled once for a whole side and no ability modifier applies.

        Args:
            score: A dexterity score from 3 to 18.

        Returns:
            The modifier, from −2 at a score of 3 to +2 at 18.

        Raises:
            ValueError: If `score` is outside 3 to 18.

        Examples:
            ```python
            from osrlib.data import load_ability_tables

            tables = load_ability_tables()

            # Under individual initiative, a dexterity of 18 adds 2 to the d6.
            assert tables.initiative_modifier(18) == 2
            assert tables.initiative_modifier(16) == 1
            ```
        """
        return self._row(self.dexterity, score).initiative

    def hit_point_modifier(self, score: int) -> int:
        """Return what a constitution score adds to every Hit Die a character rolls.

        It applies to the first-level die and to each die a new level adds. However
        negative it is, a die gives at least 1 hit point. Levels past name level add a fixed
        number of hit points instead of a die, and the modifier doesn't apply to them.

        Args:
            score: A constitution score from 3 to 18.

        Returns:
            The modifier, from −3 at a score of 3 to +3 at 18.

        Raises:
            ValueError: If `score` is outside 3 to 18.

        Examples:
            ```python
            from osrlib.data import load_ability_tables

            tables = load_ability_tables()

            # A constitution of 14 adds 1 to each Hit Die.
            assert tables.hit_point_modifier(14) == 1
            assert tables.hit_point_modifier(4) == -2
            ```
        """
        return self._row(self.constitution, score).hit_points

    def npc_reaction_modifier(self, score: int) -> int:
        """Return what a charisma score adds to a monster or NPC reaction roll.

        Add it to the 2d6 total before reading the result with
        [`reaction_result`][osrlib.core.tables.reaction_result], which reads a total below 2
        as the lowest band and a total above 12 as the highest.

        Args:
            score: A charisma score from 3 to 18.

        Returns:
            The modifier, from −2 at a score of 3 to +2 at 18.

        Raises:
            ValueError: If `score` is outside 3 to 18.

        Examples:
            ```python
            from osrlib.data import load_ability_tables

            tables = load_ability_tables()
            assert tables.npc_reaction_modifier(18) == 2
            assert tables.npc_reaction_modifier(10) == 0
            ```
        """
        return self._row(self.charisma, score).npc_reactions

    def max_retainers(self, score: int) -> int:
        """Return how many hired followers a charisma score lets a character keep at once.

        osrlib has no rules for retainers, so this number is for your own game to apply.

        Args:
            score: A charisma score from 3 to 18.

        Returns:
            The count, from 1 at a score of 3 to 7 at 18.

        Raises:
            ValueError: If `score` is outside 3 to 18.

        Examples:
            ```python
            from osrlib.data import load_ability_tables

            tables = load_ability_tables()
            assert tables.max_retainers(10) == 4
            assert tables.max_retainers(18) == 7
            ```
        """
        return self._row(self.charisma, score).max_retainers

    def retainer_loyalty(self, score: int) -> int:
        """Return the loyalty a retainer of a character with this charisma score starts with.

        In the SRD, a loyalty check rolls against this score when a retainer is asked to do
        something risky. osrlib has no rules for retainers, so the number is for your own
        game to apply.

        Args:
            score: A charisma score from 3 to 18.

        Returns:
            The loyalty score, from 4 at a charisma of 3 to 10 at 18.

        Raises:
            ValueError: If `score` is outside 3 to 18.

        Examples:
            ```python
            from osrlib.data import load_ability_tables

            tables = load_ability_tables()
            assert tables.retainer_loyalty(10) == 7
            assert tables.retainer_loyalty(18) == 10
            ```
        """
        return self._row(self.charisma, score).retainer_loyalty

    def prime_requisite_xp_modifier_pct(self, score: int) -> int:
        """Return the percentage a single prime requisite score changes earned experience by.

        A prime requisite is the ability a class is built around. This table covers a
        class with exactly one. A class with more than one has separate tiers on its
        class definition, and
        [`xp_modifier_pct`][osrlib.core.classes.xp_modifier_pct] picks the right source
        for you.

        Args:
            score: A prime requisite score from 3 to 18.

        Returns:
            The percentage, from −20 at a score of 3 to +10 at 16 or above.

        Raises:
            ValueError: If `score` is outside 3 to 18.

        Examples:
            ```python
            from osrlib.data import load_ability_tables

            tables = load_ability_tables()

            # A fighter with strength 13 earns 5% more experience: 105 points for 100.
            pct = tables.prime_requisite_xp_modifier_pct(13)
            assert pct == 5
            assert 100 * (100 + pct) // 100 == 105
            ```
        """
        return self._row(self.prime_requisite, score).xp_modifier_pct


class AbilityCheckResult(BaseModel):
    """How an ability check turned out, including the die roll so you can show it.

    [`ability_check`][osrlib.core.abilities.ability_check] returns one.
    """

    model_config = ConfigDict(frozen=True)

    roll: int
    """What the d20 came up, from 1 to 20, before the modifier."""

    score: int
    """The ability score the roll was checked against."""

    modifier: int
    """The difficulty modifier that was applied to the roll. Positive made it harder."""

    success: bool
    """Whether the check succeeded.

    A natural 1 always succeeds and a natural 20 always fails. Otherwise the check
    succeeds when the roll plus the modifier is at or under the score.
    """


class OpenDoorsResult(BaseModel):
    """How an attempt to force a door turned out, including the die roll so you can show it.

    [`open_doors_check`][osrlib.core.abilities.open_doors_check] returns one.
    """

    model_config = ConfigDict(frozen=True)

    roll: int
    """What the d6 came up, from 1 to 6."""

    chance: int
    """The chance in 6 that was rolled against."""

    success: bool
    """Whether the attempt succeeded, which it does when the roll is at or under the chance."""


def ability_check(score: int, stream: RngStream, modifier: int = 0) -> AbilityCheckResult:
    """Roll an ability check: 1d20, equal-or-under the score succeeds.

    Use this for a task the rules don't cover with a specific procedure: shoving a
    boulder, spotting a change in the stonework, holding a rope. Pick the ability that
    fits and set the difficulty with `modifier`. For the things the rules do cover, call
    the function that covers them:
    [`open_doors_check`][osrlib.core.abilities.open_doors_check] for a stuck door,
    [`saving_throw`][osrlib.core.combat.saving_throw] for magic and traps, and
    [`thief_skill_check`][osrlib.core.classes.thief_skill_check] for a thief's skills.

    The roll is a d20, and the check succeeds when the roll plus the modifier is at or
    under the score, so a higher score succeeds more often. The SRD leaves the difficulty
    to the referee and suggests "a –4 modifier for an easy task or +4 for a difficult
    task". A natural 1 always succeeds and a natural 20 always fails, the opposite of an
    attack roll.

    Args:
        score: The ability score to check against, from 3 to 18.
        stream: The stream the d20 draws from.
        modifier: The difficulty, added to the roll. Positive makes the check harder.

    Returns:
        The outcome, including the unmodified d20 roll for display.

    Raises:
        ValueError: If `score` is outside 3 to 18.

    Examples:
        ```python
        from osrlib.core.abilities import ability_check
        from osrlib.core.rng import RngStreams

        stream = RngStreams(master_seed=3).get("exploration")

        # A roll of 18 is over the score of 13, so the check fails.
        check = ability_check(13, stream)
        assert (check.roll, check.success) == (18, False)

        # A difficult task: +4 on the roll, so a 5 is still under 13.
        harder = ability_check(13, stream, modifier=4)
        assert (harder.roll, harder.success) == (5, True)
        ```
    """
    if not MIN_SCORE <= score <= MAX_SCORE:
        raise ValueError(f"ability score must be in {MIN_SCORE}-{MAX_SCORE}, got {score}")
    roll = stream.randbelow(20) + 1
    if roll == 1:
        success = True
    elif roll == 20:
        success = False
    else:
        success = roll + modifier <= score
    return AbilityCheckResult(roll=roll, score=score, modifier=modifier, success=success)


def open_doors_check(chance: int, stream: RngStream) -> OpenDoorsResult:
    """Roll one character's attempt to force a stuck door open.

    Get the chance from
    [`AbilityTables.open_doors_chance`][osrlib.core.abilities.AbilityTables.open_doors_chance]
    for the character's strength. A d6 at or under the chance opens the door. The function
    only rolls and reports the result. Opening the door, and whatever else the attempt
    costs the party, are yours to apply.

    In a session, execute [`ForceDoor`][osrlib.crawl.commands.ForceDoor] instead. It
    applies those consequences for you: the party makes noise, a success opens the door
    and springs any trap on it, and a failure alerts the area beyond, each with its
    events. Call this function when you work without a session.

    Args:
        chance: The chance in 6, from 0 to 6.
        stream: The stream the d6 draws from.

    Returns:
        The outcome, including the d6 roll for display.

    Raises:
        ValueError: If `chance` is outside 0 to 6.

    Examples:
        ```python
        from osrlib.core.abilities import open_doors_check
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_ability_tables

        chance = load_ability_tables().open_doors_chance(16)
        assert chance == 4

        result = open_doors_check(chance, RngStreams(master_seed=3).get("exploration"))
        assert (result.roll, result.success) == (5, False)
        ```
    """
    if not 0 <= chance <= 6:
        raise ValueError(f"open-doors chance must be in 0-6, got {chance}")
    roll = stream.randbelow(6) + 1
    return OpenDoorsResult(roll=roll, chance=chance, success=roll <= chance)


_LOWERABLE = (AbilityScore.STR, AbilityScore.INT, AbilityScore.WIS)


class AbilityAdjustment(BaseModel):
    """A proposed trade of ability points, made once while a character is being created.

    Build one from what the player chose, check it with
    [`validate_adjustment`][osrlib.core.abilities.validate_adjustment], and carry it out
    with [`apply_adjustment`][osrlib.core.abilities.apply_adjustment], or pass it to
    [`create_character`][osrlib.core.character.create_character] as `adjustment`. The
    exchange rate is two points down for one point up. The points bought can go only into
    the class's prime requisites, the abilities the class is built around. An amount of
    zero or less raises a pydantic `ValidationError`.

    An empty adjustment is legal and changes nothing. Build one for a player who keeps the
    scores as rolled.

    Examples:
        ```python
        from osrlib.core.abilities import AbilityAdjustment, AbilityScore

        # Four points out of intelligence and wisdom buys two points of strength.
        adjustment = AbilityAdjustment(
            lowered={AbilityScore.INT: 2, AbilityScore.WIS: 2},
            raised={AbilityScore.STR: 2},
        )
        assert sum(adjustment.raised.values()) == sum(adjustment.lowered.values()) // 2
        ```
    """

    model_config = ConfigDict(frozen=True)

    lowered: dict[AbilityScore, int] = {}
    """How much to take off each ability, as a positive number.

    Only strength, intelligence, and wisdom may appear, each amount must be even, and no
    score may end below [`ADJUSTMENT_FLOOR`][osrlib.core.abilities.ADJUSTMENT_FLOOR].
    """

    raised: dict[AbilityScore, int] = {}
    """How much to add to each ability, as a positive number.

    Only the class's prime requisites may appear, the amounts must add up to half the
    points taken off, and no score may end above
    [`MAX_SCORE`][osrlib.core.abilities.MAX_SCORE].
    """

    @model_validator(mode="after")
    def _amounts_must_be_positive(self) -> AbilityAdjustment:
        for name, amounts in (("lowered", self.lowered), ("raised", self.raised)):
            for ability, amount in amounts.items():
                if amount <= 0:
                    raise ValueError(f"{name}[{ability}] must be positive, got {amount}")
        return self


def validate_adjustment(
    scores: dict[AbilityScore, int],
    adjustment: AbilityAdjustment,
    prime_requisites: tuple[AbilityScore, ...],
    may_not_lower: tuple[AbilityScore, ...] = (),
) -> list[Rejection]:
    """Check a proposed points trade against the rules, and say what is wrong with it.

    Call this before [`apply_adjustment`][osrlib.core.abilities.apply_adjustment] and show
    the player what comes back. An empty list means the trade is legal. The two functions
    take the same four arguments, so you can pass the same ones to both.

    The rules it enforces come from the SRD's third character-creation step, as osrlib
    reads it in
    [the adaptations register](https://mmacy.github.io/osrlib-python/adaptations/), the
    page that lists every place osrlib commits to one reading of the rules:

    - Only strength, intelligence, and wisdom may be lowered.
    - A prime requisite of the chosen class may not be lowered, and neither may an ability
      the class forbids lowering, which is why a thief may not lower strength.
    - Each score comes down by an even amount, because the two-for-one trade is worked out
      per score, and an odd amount would leave half a point.
    - The points bought add up to half the points sold, and go only into the class's prime
      requisites.
    - No score is lowered below [`ADJUSTMENT_FLOOR`][osrlib.core.abilities.ADJUSTMENT_FLOOR]
      or raised above [`MAX_SCORE`][osrlib.core.abilities.MAX_SCORE].

    Args:
        scores: The rolled scores, with all six abilities present.
        adjustment: The trade the player proposed.
        prime_requisites: The chosen class's prime requisites, from
            [`ClassDefinition`][osrlib.core.classes.ClassDefinition].
        may_not_lower: Abilities this class forbids lowering, also from the class
            definition.

    Returns:
        One [`Rejection`][osrlib.core.validation.Rejection] per rule broken, empty when the
        trade is legal. A single proposal can break several rules at once, so expect more
        than one.

    Examples:
        ```python
        from osrlib.core.abilities import AbilityAdjustment, AbilityScore, validate_adjustment

        scores = {
            AbilityScore.STR: 9,
            AbilityScore.INT: 13,
            AbilityScore.WIS: 12,
            AbilityScore.DEX: 11,
            AbilityScore.CON: 14,
            AbilityScore.CHA: 10,
        }

        # Four points down, two points up, into the fighter's prime requisite.
        legal = AbilityAdjustment(
            lowered={AbilityScore.INT: 2, AbilityScore.WIS: 2},
            raised={AbilityScore.STR: 2},
        )
        assert validate_adjustment(scores, legal, (AbilityScore.STR,)) == []

        # Buying two points for two is not the rate.
        greedy = AbilityAdjustment(lowered={AbilityScore.INT: 2}, raised={AbilityScore.STR: 2})
        assert [rejection.code for rejection in validate_adjustment(scores, greedy, (AbilityScore.STR,))] == [
            "creation.adjustment.points_mismatch"
        ]
        ```
    """
    rejections: list[Rejection] = []
    for ability, amount in adjustment.lowered.items():
        if ability not in _LOWERABLE:
            rejections.append(Rejection(code="creation.adjustment.not_lowerable", params={"ability": ability}))
        if ability in prime_requisites:
            rejections.append(
                Rejection(code="creation.adjustment.prime_requisite_lowered", params={"ability": ability})
            )
        if ability in may_not_lower:
            rejections.append(Rejection(code="creation.adjustment.class_restriction", params={"ability": ability}))
        if amount % 2 != 0:
            rejections.append(
                Rejection(code="creation.adjustment.reduction_not_even", params={"ability": ability, "amount": amount})
            )
        if scores[ability] - amount < ADJUSTMENT_FLOOR:
            rejections.append(
                Rejection(
                    code="creation.adjustment.below_floor",
                    params={"ability": ability, "score": scores[ability], "amount": amount},
                )
            )
    for ability, amount in adjustment.raised.items():
        if ability not in prime_requisites:
            rejections.append(
                Rejection(code="creation.adjustment.raise_not_prime_requisite", params={"ability": ability})
            )
        elif scores[ability] + amount > MAX_SCORE:
            rejections.append(
                Rejection(
                    code="creation.adjustment.above_cap",
                    params={"ability": ability, "score": scores[ability], "amount": amount},
                )
            )
    total_lowered = sum(adjustment.lowered.values())
    total_raised = sum(adjustment.raised.values())
    if total_raised != total_lowered // 2:
        rejections.append(
            Rejection(
                code="creation.adjustment.points_mismatch",
                params={"points_available": total_lowered // 2, "points_spent": total_raised},
            )
        )
    return rejections


def apply_adjustment(
    scores: dict[AbilityScore, int],
    adjustment: AbilityAdjustment,
    prime_requisites: tuple[AbilityScore, ...],
    may_not_lower: tuple[AbilityScore, ...] = (),
) -> dict[AbilityScore, int]:
    """Carry out a points trade and return the adjusted scores.

    Call [`validate_adjustment`][osrlib.core.abilities.validate_adjustment] first, with the
    same arguments. This function validates again and refuses to produce scores the rules
    forbid, so an exception here means your code let an illegal trade through, not that
    the player chose badly. It applies the whole trade or nothing.

    Pass the result as `scores` when you construct the
    [`Character`][osrlib.core.character.Character] yourself. You don't call this when you
    use [`create_character`][osrlib.core.character.create_character]: it takes the
    `AbilityAdjustment` as its `adjustment` argument and applies it for you.

    Args:
        scores: The rolled scores, with all six abilities present. This dictionary isn't
            changed. The adjusted scores come back in a new one.
        adjustment: The trade to carry out.
        prime_requisites: The chosen class's prime requisites.
        may_not_lower: Abilities this class forbids lowering.

    Returns:
        A new score dictionary with the points moved. It has the same keys as `scores`, and
        the abilities the trade didn't touch keep their rolled values.

    Raises:
        ValueError: If the trade breaks any rule
            [`validate_adjustment`][osrlib.core.abilities.validate_adjustment] checks. The
            message includes the rejection codes.

    Examples:
        ```python
        from osrlib.core.abilities import AbilityAdjustment, AbilityScore, apply_adjustment

        scores = {
            AbilityScore.STR: 9,
            AbilityScore.INT: 13,
            AbilityScore.WIS: 12,
            AbilityScore.DEX: 11,
            AbilityScore.CON: 14,
            AbilityScore.CHA: 10,
        }
        adjustment = AbilityAdjustment(
            lowered={AbilityScore.INT: 2, AbilityScore.WIS: 2},
            raised={AbilityScore.STR: 2},
        )

        adjusted = apply_adjustment(scores, adjustment, (AbilityScore.STR,))
        assert adjusted[AbilityScore.STR] == 11
        assert adjusted[AbilityScore.INT] == 11
        assert adjusted[AbilityScore.CON] == 14

        # The rolled scores are untouched.
        assert scores[AbilityScore.STR] == 9
        ```
    """
    rejections = validate_adjustment(scores, adjustment, prime_requisites, may_not_lower)
    if rejections:
        codes = [rejection.code for rejection in rejections]
        raise ValueError(f"illegal ability adjustment: {codes}")
    adjusted = dict(scores)
    for ability, amount in adjustment.lowered.items():
        adjusted[ability] -= amount
    for ability, amount in adjustment.raised.items():
        adjusted[ability] += amount
    return adjusted
