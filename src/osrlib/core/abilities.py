"""Ability scores: modifier tables, checks, prime requisites, and the adjustment step.

The six modifier tables (and the prime requisite XP table) are compiled from the SRD's
Ability Scores page into `abilities.json` and load as one frozen
[`AbilityTables`][osrlib.core.abilities.AbilityTables] model, which carries one accessor
per SRD column. Tables are stored as score bands exactly as the SRD prints them (`4–5`,
`13–15`), validated to cover 3–18 contiguously.

Scores range 3–18: 3d6 can roll nothing else, the SRD's tables list nothing else, and
the creation-time adjustment step may not raise a score above 18 nor lower one below 9.

Ability checks per the SRD: roll 1d20, equal-or-under the score succeeds, with a
caller-supplied difficulty modifier (±4 easy/hard) applied to the roll. A natural 1
always succeeds and a natural 20 always fails — *inverted* from attack rolls, where a
natural 20 always hits. Open-doors checks are d6 ≤ the STR-derived chance.

The adjustment step is pure validation plus atomic application over a rolled score set
and a chosen class's prime requisites: lower STR/INT/WIS two-for-one into prime
requisites, floor 9, cap 18, with class-specific restrictions (a thief may not lower
STR) carried as data on the class definition.
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
"""The lowest possible ability score (3d6 minimum; also the tables' floor)."""

MAX_SCORE = 18
"""The highest possible ability score (3d6 maximum; also the adjustment raise cap)."""

ADJUSTMENT_FLOOR = 9
"""No score may be lowered below this value during the adjustment step."""


class AbilityScore(StrEnum):
    """The six ability scores.

    The wire values are lowercase (`"str"`, `"int"`, ...) — they serialize into
    characters and saves; changing them is a `schema_version` bump.
    """

    STR = "str"
    INT = "int"
    WIS = "wis"
    DEX = "dex"
    CON = "con"
    CHA = "cha"


class Literacy(StrEnum):
    """The INT table's literacy column."""

    ILLITERATE = "illiterate"
    BASIC = "basic"
    LITERATE = "literate"


class ScoreBand(BaseModel):
    """A contiguous score range in a modifier table, as the SRD prints it (`4–5`)."""

    model_config = ConfigDict(frozen=True)

    min_score: int = Field(ge=MIN_SCORE, le=MAX_SCORE)
    max_score: int = Field(ge=MIN_SCORE, le=MAX_SCORE)

    @model_validator(mode="after")
    def _band_must_be_ordered(self) -> ScoreBand:
        if self.min_score > self.max_score:
            raise ValueError(f"band minimum {self.min_score} exceeds maximum {self.max_score}")
        return self


class StrengthRow(ScoreBand):
    """One STR band: melee modifier and open-doors chance (X-in-6)."""

    melee: int
    open_doors: int = Field(ge=0, le=6)


class IntelligenceRow(ScoreBand):
    """One INT band: additional spoken languages, literacy, and broken speech at INT 3."""

    additional_languages: int = Field(ge=0)
    literacy: Literacy
    broken_speech: bool = False


class WisdomRow(ScoreBand):
    """One WIS band: saving throw modifier versus magical effects."""

    magic_saves: int


class DexterityRow(ScoreBand):
    """One DEX band: AC modifier, missile attack modifier, and optional-rule initiative modifier."""

    ac: int
    missile: int
    initiative: int


class ConstitutionRow(ScoreBand):
    """One CON band: hit point modifier per Hit Die."""

    hit_points: int


class CharismaRow(ScoreBand):
    """One CHA band: NPC reaction modifier, retainer maximum, and retainer loyalty."""

    npc_reactions: int
    max_retainers: int = Field(ge=0)
    retainer_loyalty: int = Field(ge=0)


class PrimeRequisiteRow(ScoreBand):
    """One prime requisite band: XP modifier percentage for single-prime-requisite classes."""

    xp_modifier_pct: int


def _validate_coverage(rows: tuple[ScoreBand, ...], table: str) -> None:
    expected = MIN_SCORE
    for row in rows:
        if row.min_score != expected:
            raise ValueError(f"{table} table bands must cover 3-18 contiguously; expected band start {expected}")
        expected = row.max_score + 1
    if expected != MAX_SCORE + 1:
        raise ValueError(f"{table} table bands must end at {MAX_SCORE}")


class AbilityTables(BaseModel):
    """The six ability modifier tables plus the prime requisite XP table.

    Loaded from `abilities.json` via
    [`load_ability_tables`][osrlib.data.load_ability_tables]. Accessors take a score in
    3–18 and raise stdlib `ValueError` outside that range (programmer misuse).
    """

    model_config = ConfigDict(frozen=True)

    strength: tuple[StrengthRow, ...]
    intelligence: tuple[IntelligenceRow, ...]
    wisdom: tuple[WisdomRow, ...]
    dexterity: tuple[DexterityRow, ...]
    constitution: tuple[ConstitutionRow, ...]
    charisma: tuple[CharismaRow, ...]
    prime_requisite: tuple[PrimeRequisiteRow, ...]

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
        """Return the STR modifier to melee attack and damage rolls."""
        return self._row(self.strength, score).melee

    def open_doors_chance(self, score: int) -> int:
        """Return the STR-derived X-in-6 chance to force open a stuck door."""
        return self._row(self.strength, score).open_doors

    def additional_languages(self, score: int) -> int:
        """Return the number of additional spoken languages granted by INT."""
        return self._row(self.intelligence, score).additional_languages

    def literacy(self, score: int) -> Literacy:
        """Return the INT-derived literacy in the character's native languages."""
        return self._row(self.intelligence, score).literacy

    def magic_save_modifier(self, score: int) -> int:
        """Return the WIS modifier to saving throws versus magical effects."""
        return self._row(self.wisdom, score).magic_saves

    def ac_modifier(self, score: int) -> int:
        """Return the DEX modifier to AC (a bonus lowers descending AC)."""
        return self._row(self.dexterity, score).ac

    def missile_modifier(self, score: int) -> int:
        """Return the DEX modifier to missile attack rolls (not damage)."""
        return self._row(self.dexterity, score).missile

    def initiative_modifier(self, score: int) -> int:
        """Return the DEX modifier to individual initiative (optional rule)."""
        return self._row(self.dexterity, score).initiative

    def hit_point_modifier(self, score: int) -> int:
        """Return the CON modifier applied per Hit Die rolled (minimum 1 hp per die)."""
        return self._row(self.constitution, score).hit_points

    def npc_reaction_modifier(self, score: int) -> int:
        """Return the CHA modifier to NPC reactions."""
        return self._row(self.charisma, score).npc_reactions

    def max_retainers(self, score: int) -> int:
        """Return the CHA-derived maximum number of retainers."""
        return self._row(self.charisma, score).max_retainers

    def retainer_loyalty(self, score: int) -> int:
        """Return the CHA-derived retainer loyalty rating."""
        return self._row(self.charisma, score).retainer_loyalty

    def prime_requisite_xp_modifier_pct(self, score: int) -> int:
        """Return the XP modifier percentage for a single prime requisite at `score`."""
        return self._row(self.prime_requisite, score).xp_modifier_pct


class AbilityCheckResult(BaseModel):
    """The outcome of an ability check, with the raw roll kept for display."""

    model_config = ConfigDict(frozen=True)

    roll: int
    score: int
    modifier: int
    success: bool


class OpenDoorsResult(BaseModel):
    """The outcome of an open-doors check, with the raw roll kept for display."""

    model_config = ConfigDict(frozen=True)

    roll: int
    chance: int
    success: bool


def ability_check(score: int, stream: RngStream, modifier: int = 0) -> AbilityCheckResult:
    """Roll an ability check: 1d20, equal-or-under the score succeeds.

    The caller-supplied difficulty modifier is added to the roll (the SRD suggests −4
    for an easy task, +4 for a difficult one). A natural 1 always succeeds and a
    natural 20 always fails — inverted from attack rolls, where a natural 20 always
    hits and a natural 1 always misses.

    Args:
        score: The ability score to check against, in 3–18.
        stream: The RNG stream to draw from.
        modifier: Difficulty modifier added to the roll; positive makes it harder.

    Returns:
        The check outcome, including the raw d20 roll.

    Raises:
        ValueError: If `score` is outside 3–18.
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
    """Roll an open-doors check: d6, equal-or-under the X-in-6 chance succeeds.

    Args:
        chance: The X-in-6 chance, from
            [`AbilityTables.open_doors_chance`][osrlib.core.abilities.AbilityTables.open_doors_chance].
        stream: The RNG stream to draw from.

    Returns:
        The check outcome, including the raw d6 roll.

    Raises:
        ValueError: If `chance` is outside 0–6.
    """
    if not 0 <= chance <= 6:
        raise ValueError(f"open-doors chance must be in 0-6, got {chance}")
    roll = stream.randbelow(6) + 1
    return OpenDoorsResult(roll=roll, chance=chance, success=roll <= chance)


_LOWERABLE = (AbilityScore.STR, AbilityScore.INT, AbilityScore.WIS)


class AbilityAdjustment(BaseModel):
    """The creation-time adjustment: even reductions traded two-for-one into prime requisite raises.

    `lowered` maps abilities to the (positive) amount subtracted; `raised` maps
    abilities to the (positive) amount added. An empty adjustment is a legal no-op.
    """

    model_config = ConfigDict(frozen=True)

    lowered: dict[AbilityScore, int] = {}
    raised: dict[AbilityScore, int] = {}

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
    """Validate an adjustment against the SRD's rules, returning structured rejections.

    The rules, per the SRD's Creating a Character step 3 and the pinned
    interpretations in `docs/adaptations.md`:

    - Only STR, INT, and WIS may be lowered.
    - A prime requisite of the chosen class may not be lowered, nor may an ability in
      the class's `may_not_lower` restrictions (the thief's STR).
    - Each lowered score drops by an even amount (pinned: the two-for-one trade is
      per-score, so an odd reduction would strand half a point).
    - The total raise equals the sum of reductions divided by two, distributed freely
      among the class's prime requisites and nowhere else.
    - No lowered score drops below 9; no raised score rises above 18.

    Args:
        scores: The rolled scores, all six abilities present.
        adjustment: The proposed adjustment.
        prime_requisites: The chosen class's prime requisites.
        may_not_lower: Class-specific lowering restrictions, from the class definition.

    Returns:
        Structured rejections; empty when the adjustment is legal.
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
    """Apply a validated adjustment atomically, returning the new score set.

    Args:
        scores: The rolled scores; not mutated.
        adjustment: The adjustment to apply.
        prime_requisites: The chosen class's prime requisites.
        may_not_lower: Class-specific lowering restrictions.

    Returns:
        A new score dict with reductions and raises applied.

    Raises:
        ValueError: If the adjustment fails
            [`validate_adjustment`][osrlib.core.abilities.validate_adjustment] —
            applying an illegal adjustment is programmer misuse.
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
