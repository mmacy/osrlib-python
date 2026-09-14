"""What a character's six ability scores are worth, and what you can do with them.

Start with [`load_ability_tables`][osrlib.data.load_ability_tables], which gives you an
[`AbilityTables`][osrlib.core.abilities.AbilityTables] with the SRD's printed tables.
Hand any of its accessors a score from 3 to 18 and get the number the rules apply: the
melee modifier for strength, the armour class modifier for dexterity, the hit points per
Hit Die for constitution, and so on. A [`Character`][osrlib.core.character.Character]
exposes the ones it needs as properties, so you call the accessors yourself when you're
working outside a character.

Two dice functions live here as well.
[`ability_check`][osrlib.core.abilities.ability_check] rolls the SRD's generic check for
a task the rules don't otherwise cover, and
[`open_doors_check`][osrlib.core.abilities.open_doors_check] rolls a strength-based
attempt to force a stuck door. Both take an
[`RngStream`][osrlib.core.rng.RngStream].

The rest of the module is character creation's third step, where a player trades points
between abilities before play.
[`validate_adjustment`][osrlib.core.abilities.validate_adjustment] says whether a
proposed trade is legal and [`apply_adjustment`][osrlib.core.abilities.apply_adjustment]
carries it out. [`create_character`][osrlib.core.character.create_character] runs the
whole creation sequence, so reach for these two when you're building a character step by
step and letting a player choose.

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
Below it there's no rule to apply, so the accessors and the check functions raise
`ValueError` rather than guess. Use it to bound a slider, or to check a score your own
code produced.
"""

MAX_SCORE = 18
"""The highest an ability score can be, which is 18.

Eighteen is what three dice showing 6 add up to, the top row of every table here, and the
ceiling the creation-time trade may not push a score past. The accessors raise
`ValueError` above it, because the tables print no row to read.
"""

ADJUSTMENT_FLOOR = 9
"""The lowest a score may be traded down to during character creation, which is 9.

The floor stops a player from emptying one ability to buy up another. It applies only to
the trade in [`validate_adjustment`][osrlib.core.abilities.validate_adjustment]. A score
rolled below 9 is legal, and it **cannot** be lowered further.
"""


class AbilityScore(StrEnum):
    """Which of the six abilities a score belongs to.

    Use these as the keys of a score dictionary, which is how every function here and in
    [`osrlib.core.character`][osrlib.core.character] passes a character's abilities
    around. All six keys are expected to be present.

    The lowercase values serialize into characters and saved games. Changing one is a
    `schema_version` bump, the version stamp that marks a serialized model's shape.
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
    """Charisma: how monsters and NPCs react, and how many retainers will follow the character, how loyally."""


class Literacy(StrEnum):
    """How well a character reads and writes, which intelligence decides.

    Read it off [`AbilityTables.literacy`][osrlib.core.abilities.AbilityTables.literacy]
    or [`Character.literacy`][osrlib.core.character.Character.literacy]. It matters
    whenever the party finds something written, a scroll or a map or an inscription.
    """

    ILLITERATE = "illiterate"
    """Cannot read or write, at intelligence 5 or below."""

    BASIC = "basic"
    """Partial literacy, at intelligence 6 to 8: the step the SRD prints between illiterate and literate."""

    LITERATE = "literate"
    """Can read and write the character's native languages, at intelligence 9 or above."""


class ScoreBand(BaseModel):
    """The run of scores one table row covers, such as 4 to 5.

    The SRD prints its modifier tables in bands rather than one row per score, and osrlib
    keeps them that way. Every row model below is a band with the row's own columns added.
    You'll meet these when you read a whole table off
    [`AbilityTables`][osrlib.core.abilities.AbilityTables], for instance to draw the table
    on screen. To look up a single score, call an accessor instead and skip the rows
    entirely.
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
    """One row of the strength table, with the two things strength grants."""

    melee: int
    """What to add to a melee attack roll and to melee damage. Negative at a low score."""

    open_doors: int = Field(ge=0, le=6)
    """The chance in 6 of forcing a stuck door open.

    Pass it to [`open_doors_check`][osrlib.core.abilities.open_doors_check], which rolls
    the die against it.
    """


class IntelligenceRow(ScoreBand):
    """One row of the intelligence table, covering language and literacy."""

    additional_languages: int = Field(ge=0)
    """How many languages beyond the character's native ones they may choose at creation."""

    literacy: Literacy
    """How well the character reads and writes. See [`Literacy`][osrlib.core.abilities.Literacy]."""

    broken_speech: bool = False
    """True only at intelligence 3, where the character speaks their native language brokenly."""


class WisdomRow(ScoreBand):
    """One row of the wisdom table. Wisdom grants a saving throw modifier and no other bonus."""

    magic_saves: int
    """What to add to a saving throw against a magical effect. Negative at a low score."""


class DexterityRow(ScoreBand):
    """One row of the dexterity table, with the three things dexterity grants."""

    ac: int
    """What to add to armour class. A positive number here makes a descending armour class better, so it lowers it."""

    missile: int
    """What to add to a missile attack roll. It doesn't touch missile damage."""

    initiative: int
    """What to add to an individual initiative roll.

    Read only when the `individual_initiative` flag on
    [`Ruleset`][osrlib.core.ruleset.Ruleset] is on, since initiative is otherwise rolled
    once for a whole side.
    """


class ConstitutionRow(ScoreBand):
    """One row of the constitution table. Constitution grants hit points and no other bonus."""

    hit_points: int
    """What to add to every Hit Die rolled, whether at creation or on gaining a level.

    A die never yields fewer than 1 hit point however negative this is.
    """


class CharismaRow(ScoreBand):
    """One row of the charisma table, with the three things charisma grants."""

    npc_reactions: int
    """What to add to a monster or NPC reaction roll, which decides how a meeting starts."""

    max_retainers: int = Field(ge=0)
    """How many hired followers the character may have at once."""

    retainer_loyalty: int = Field(ge=0)
    """The loyalty a retainer of this character starts with, rolled against when the retainer's nerve is tested."""


class PrimeRequisiteRow(ScoreBand):
    """One row of the prime requisite table, which sets how fast a character earns experience.

    A prime requisite is the ability a class is built around: wisdom for a cleric,
    strength for a fighter. This table applies to a class with a single prime requisite. A
    class with more than one has its own tiers on its class definition.
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
    tables that ship with the package and caches them, so calling it repeatedly costs
    nothing. Then call the accessor for the column you want, passing a score from
    [`MIN_SCORE`][osrlib.core.abilities.MIN_SCORE] to
    [`MAX_SCORE`][osrlib.core.abilities.MAX_SCORE]. A score outside that range raises
    `ValueError`, because a score outside it is a mistake in your code rather than an
    outcome the rules allow.

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
        """
        return self._row(self.dexterity, score).initiative

    def hit_point_modifier(self, score: int) -> int:
        """Return what a constitution score adds to every Hit Die a character rolls.

        It applies at creation and again at each level gained. However negative it is, a
        die never ends up granting fewer than 1 hit point.

        Args:
            score: A constitution score from 3 to 18.

        Returns:
            The modifier, from −3 at a score of 3 to +3 at 18.

        Raises:
            ValueError: If `score` is outside 3 to 18.
        """
        return self._row(self.constitution, score).hit_points

    def npc_reaction_modifier(self, score: int) -> int:
        """Return what a charisma score adds to a monster or NPC reaction roll.

        Add it to the 2d6 total before reading the result with
        [`reaction_result`][osrlib.core.tables.reaction_result], which clamps a modified
        total into the printed outer bands.

        Args:
            score: A charisma score from 3 to 18.

        Returns:
            The modifier, from −2 at a score of 3 to +2 at 18.

        Raises:
            ValueError: If `score` is outside 3 to 18.
        """
        return self._row(self.charisma, score).npc_reactions

    def max_retainers(self, score: int) -> int:
        """Return how many hired followers a charisma score lets a character keep at once.

        Args:
            score: A charisma score from 3 to 18.

        Returns:
            The count, from 1 at a score of 3 to 7 at 18.

        Raises:
            ValueError: If `score` is outside 3 to 18.
        """
        return self._row(self.charisma, score).max_retainers

    def retainer_loyalty(self, score: int) -> int:
        """Return the loyalty a retainer of a character with this charisma score starts with.

        Loyalty is the number a retainer's nerve is tested against when the party asks
        something risky of them.

        Args:
            score: A charisma score from 3 to 18.

        Returns:
            The loyalty score, from 4 at a charisma of 3 to 10 at 18.

        Raises:
            ValueError: If `score` is outside 3 to 18.
        """
        return self._row(self.charisma, score).retainer_loyalty

    def prime_requisite_xp_modifier_pct(self, score: int) -> int:
        """Return the percentage a single prime requisite score changes earned experience by.

        A prime requisite is the ability a class is built around. This table covers a
        class with exactly one. A class with more than one has its own tiers on its
        class definition, and
        [`xp_modifier_pct`][osrlib.core.classes.xp_modifier_pct] picks the right source
        for you.

        Args:
            score: A prime requisite score from 3 to 18.

        Returns:
            The percentage, from −20 at a score of 3 to +10 at 16 or above.

        Raises:
            ValueError: If `score` is outside 3 to 18.
        """
        return self._row(self.prime_requisite, score).xp_modifier_pct


class AbilityCheckResult(BaseModel):
    """How an ability check turned out, with the die kept so you can show it.

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

    True when the modified roll came out at or under the score, and on a natural 1
    whatever the score.
    """


class OpenDoorsResult(BaseModel):
    """How an attempt to force a door turned out, with the die kept so you can show it.

    [`open_doors_check`][osrlib.core.abilities.open_doors_check] returns one.
    """

    model_config = ConfigDict(frozen=True)

    roll: int
    """What the d6 came up, from 1 to 6."""

    chance: int
    """The chance in 6 that was rolled against."""

    success: bool
    """Whether the door opened, which it did when the roll came out at or under the chance."""


def ability_check(score: int, stream: RngStream, modifier: int = 0) -> AbilityCheckResult:
    """Roll an ability check: 1d20, equal-or-under the score succeeds.

    Use this for a task the rules don't cover with a procedure of their own: shoving a
    boulder, spotting a change in the stonework, holding a rope. Pick the ability that
    fits and set the difficulty with `modifier`. For the things the rules do cover, call
    the function that covers them:
    [`open_doors_check`][osrlib.core.abilities.open_doors_check] for a stuck door, the
    saving throws in [`osrlib.core.combat`][osrlib.core.combat] for magic and traps, and
    the thief skills on the class definition for a thief's own work.

    The roll is a d20, and the check succeeds on a modified roll at or under the score, so
    a higher score succeeds more often. The SRD suggests −4 for an easy task and +4 for a
    difficult one. A natural 1 always succeeds and a natural 20 always fails, which is the
    opposite way round from an attack roll.

    Args:
        score: The ability score to check against, from 3 to 18.
        stream: The stream the d20 draws from.
        modifier: The difficulty, added to the roll. Positive makes the check harder.

    Returns:
        The outcome, with the raw d20 kept for display.

    Raises:
        ValueError: If `score` is outside 3 to 18.

    Examples:
        ```python
        from osrlib.core.abilities import ability_check
        from osrlib.core.rng import RngStreams

        stream = RngStreams(master_seed=3).get("exploration")

        # An 18 against a score of 13 fails, as a natural 20 would have.
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
    rolls and reports, and nothing else: whether the door then opens, how much noise the
    attempt made, and how much time it cost are yours to apply.

    In a running game [`ForceDoor`][osrlib.crawl.commands.ForceDoor] does all of that for
    you, with the events to match, so call this function when you're working outside a
    session.

    Args:
        chance: The chance in 6, from 0 to 6.
        stream: The stream the d6 draws from.

    Returns:
        The outcome, with the raw d6 kept for display.

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
    with [`apply_adjustment`][osrlib.core.abilities.apply_adjustment]. The exchange rate
    is two points down for one point up, and the points bought can only go into the
    class's prime requisites, the abilities the class is built around.

    An adjustment with nothing in it is legal and changes nothing, which is what you build
    for a player who keeps the scores as rolled.

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
    the player what came back. An empty list means the trade is legal. The two functions
    take the same four arguments, so you can pass the same ones straight on.

    The rules it enforces, from the SRD's third character-creation step and osrlib's
    readings of it in
    [the adaptations register](https://mmacy.github.io/osrlib-python/adaptations/), the
    page that lists every place osrlib commits to one reading of the rules:

    - Only strength, intelligence, and wisdom may be lowered.
    - A prime requisite of the chosen class may not be lowered, and neither may an ability
      the class forbids lowering, which is why a thief may not lower strength.
    - Each score comes down by an even amount, since the two-for-one trade is worked out
      per score and an odd amount would strand half a point.
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

    Call [`validate_adjustment`][osrlib.core.abilities.validate_adjustment] first and pass
    the same arguments on. This function validates again and refuses rather than produce a
    score set the rules forbid, so an exception here means your code let an illegal trade
    through, not that the player chose badly. It either applies the whole trade or changes
    nothing.

    Feed the result to [`create_character`][osrlib.core.character.create_character] as the
    scores to build the character from.

    Args:
        scores: The rolled scores. Left as they are, because the adjusted set comes back
            as a new dictionary.
        adjustment: The trade to carry out.
        prime_requisites: The chosen class's prime requisites.
        may_not_lower: Abilities this class forbids lowering.

    Returns:
        A new score dictionary with the points moved. All six abilities are present, and
        the ones the trade didn't touch keep their rolled values.

    Raises:
        ValueError: If the trade breaks any rule
            [`validate_adjustment`][osrlib.core.abilities.validate_adjustment] checks. The
            message names the rejection codes.

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
