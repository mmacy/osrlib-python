"""Dice expression parsing and rolling.

The grammar is `NdS` with an optional `+M`/`-M` modifier and an optional `×K`
multiplier (`x` and `*` accepted as ASCII aliases): `3d6`, `1d6+1`, `1d4-1`, `2d6×10`.
`N` defaults to 1, `d%` is an alias for `d100`, and die sizes are the closed set
{2, 3, 4, 6, 8, 10, 12, 20, 100}. Parsing is case-insensitive, surrounding whitespace
is stripped, internal whitespace is rejected, and component order is fixed: dice, then
modifier, then multiplier. Numerals are canonical ASCII digits — no Unicode digits, no
leading zeros — with the dice count in 1–999, the modifier magnitude at most 999999,
and the multiplier in 1–999999. Anything else raises
[`ContentValidationError`][osrlib.errors.ContentValidationError].

Evaluation order is `(sum of dice + M) × K` — the modifier applies before the
multiplier, which is *not* ordinary arithmetic precedence: `2d6+1×10` means
`(2d6 + 1) × 10`, the B/X treasure-roll convention, not `2d6 + 10`.

Results are not clamped: `1d4-1` can total 0 and `1d4-2` can total −1. Minimum-1-damage
is combat's rule, not the dice module's.

Every roll draws from an explicitly passed [`RngStream`][osrlib.core.rng.RngStream];
there is no default stream and no module-level RNG.
"""

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from osrlib.core.rng import RngStream
from osrlib.errors import ContentValidationError

__all__ = [
    "ALLOWED_SIDES",
    "DiceExpression",
    "RollResult",
    "parse",
    "roll",
]

ALLOWED_SIDES = frozenset({2, 3, 4, 6, 8, 10, 12, 20, 100})
"""The closed set of legal die sizes."""

# Canonical ASCII digits only, no leading zeros, bounded lengths: the grammar freezes
# with parse acceptance, so what \d would quietly admit (Unicode digits, 5000-digit
# numerals) must be rejected here, not discovered as contract later.
_EXPRESSION_PATTERN = re.compile(
    r"""
    (?P<count>[1-9][0-9]{0,2})?
    d
    (?P<sides>%|[1-9][0-9]{0,2})
    (?P<modifier>[+-](?:0|[1-9][0-9]{0,5}))?
    (?:[x×*](?P<multiplier>[1-9][0-9]{0,5}))?
    """,
    re.IGNORECASE | re.VERBOSE,
)


class DiceExpression(BaseModel):
    """A parsed dice expression: `count` dice of `sides` sides, `+modifier`, `×multiplier`."""

    model_config = ConfigDict(frozen=True)

    count: int = Field(ge=1)
    sides: int
    modifier: int = 0
    multiplier: int = Field(default=1, ge=1)

    @field_validator("sides")
    @classmethod
    def _sides_must_be_allowed(cls, value: int) -> int:
        if value not in ALLOWED_SIDES:
            raise ValueError(f"die size must be one of {sorted(ALLOWED_SIDES)}, got {value}")
        return value


class RollResult(BaseModel):
    """The outcome of rolling a dice expression.

    Individual die results are kept — not just the total — because events want to show
    the rolls. `total` is `(sum(rolls) + modifier) × multiplier`.
    """

    model_config = ConfigDict(frozen=True)

    rolls: tuple[int, ...]
    modifier: int
    multiplier: int
    total: int


def parse(expression: str) -> DiceExpression:
    """Parse a dice expression string.

    Args:
        expression: A dice expression such as `"3d6"`, `"d%"`, or `"2d6+1×10"`.
            Case-insensitive; surrounding whitespace is ignored.

    Returns:
        The parsed, frozen expression model.

    Raises:
        ContentValidationError: If the expression doesn't match the grammar: unknown die
            size, zero dice, zero multiplier, non-canonical numerals, internal
            whitespace, or components out of the fixed dice-modifier-multiplier order.
        TypeError: If `expression` is not a string.
    """
    if not isinstance(expression, str):
        raise TypeError(f"expression must be a str, got {type(expression).__name__}")
    text = expression.strip()
    match = _EXPRESSION_PATTERN.fullmatch(text)
    if match is None:
        raise ContentValidationError(f"invalid dice expression: {expression!r}")
    count = int(match["count"]) if match["count"] is not None else 1
    sides = 100 if match["sides"] == "%" else int(match["sides"])
    if sides not in ALLOWED_SIDES:
        raise ContentValidationError(f"die size must be one of {sorted(ALLOWED_SIDES)}: {expression!r}")
    modifier = int(match["modifier"]) if match["modifier"] is not None else 0
    multiplier = int(match["multiplier"]) if match["multiplier"] is not None else 1
    return DiceExpression(count=count, sides=sides, modifier=modifier, multiplier=multiplier)


def roll(expression: str | DiceExpression, stream: RngStream) -> RollResult:
    """Roll a dice expression, drawing from the given stream.

    Dice roll left to right, one die of size S drawing `randbelow(S) + 1` — this
    mapping is part of the determinism contract.

    Args:
        expression: The expression to roll, as a string (parsed first) or an
            already-parsed [`DiceExpression`][osrlib.core.dice.DiceExpression].
        stream: The RNG stream to draw from.

    Returns:
        The roll outcome, including each individual die result.

    Raises:
        ContentValidationError: If a string expression doesn't match the grammar.
        TypeError: If `expression` is neither a string nor a `DiceExpression`.

    Examples:

        ```python
        from osrlib.core.dice import roll
        from osrlib.core.rng import RngStreams

        stream = RngStreams(master_seed=42).get("treasure")

        # A plain 3d6: three dice, summed.
        scores = roll("3d6", stream)
        assert scores.rolls == (4, 2, 3)
        assert scores.total == 9

        # Modifier then multiplier: (2d6 + 1) × 10, evaluated in that order.
        gold = roll("2d6+1×10", stream)
        assert gold.total == (sum(gold.rolls) + 1) * 10
        ```
    """
    if isinstance(expression, str):
        expression = parse(expression)
    elif not isinstance(expression, DiceExpression):
        raise TypeError(f"expression must be a str or DiceExpression, got {type(expression).__name__}")
    rolls = tuple(stream.randbelow(expression.sides) + 1 for _ in range(expression.count))
    total = (sum(rolls) + expression.modifier) * expression.multiplier
    return RollResult(
        rolls=rolls,
        modifier=expression.modifier,
        multiplier=expression.multiplier,
        total=total,
    )
