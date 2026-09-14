"""Rolling dice from the strings the rules are written in.

[`roll`][osrlib.core.dice.roll] is the entry point: hand it a dice expression such as
`"3d6"` and an [`RngStream`][osrlib.core.rng.RngStream] from
[`RngStreams.get`][osrlib.core.rng.RngStreams.get], and it returns a
[`RollResult`][osrlib.core.dice.RollResult] with each die and the total. Call
[`parse`][osrlib.core.dice.parse] first only when you want to check an expression
without rolling it, or to inspect its parts. Every dice field in the compiled SRD data
is written in this grammar, so monster damage, treasure quantities, and spell effects all
go straight to `roll`.

The grammar is `NdS` with an optional `+M` or `-M` modifier and an optional `×K`
multiplier, with `x` and `*` accepted as ASCII aliases for `×`: `3d6`, `1d6+1`, `1d4-1`,
`2d6×10`. `N` defaults to 1, `d%` means `d100`, and the die sizes are the closed set
{2, 3, 4, 6, 8, 10, 12, 20, 100}. Parsing ignores case and surrounding whitespace,
rejects whitespace inside the expression, and fixes the order as dice, then modifier,
then multiplier. Numerals are plain ASCII digits with no leading zeros: the dice count
runs 1 to 999, the modifier's magnitude reaches 999999, and the multiplier runs 1 to
999999. Anything else raises
[`ContentValidationError`][osrlib.errors.ContentValidationError].

A total is `(sum of the dice + M) × K`, so the modifier applies before the multiplier.
That isn't ordinary arithmetic precedence: `2d6+1×10` means `(2d6 + 1) × 10`, the
convention B/X treasure rolls are printed in, not `2d6 + 10`.

Totals aren't clamped, so `1d4-1` can come out 0 and `1d4-2` can come out −1. If you're
rolling damage and want the floor of 1 hit point, that floor belongs to combat, and
[`resolve_attack`][osrlib.core.combat.resolve_attack] applies it for you.

There's no default stream and no module-level random number generator. You pass the
stream on every call, which is what makes a game replayable.
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
"""Every die size an expression may name: 2, 3, 4, 6, 8, 10, 12, 20, and 100.

These are the dice the B/X rules roll and the only sizes the compiled SRD data contains.
Read the set to offer a picker or to check a size before you build an expression.
[`parse`][osrlib.core.dice.parse] checks it for you and raises
[`ContentValidationError`][osrlib.errors.ContentValidationError] on anything else, so a
d7 never reaches a rules function.

The set is closed on purpose, so adding to it isn't the way to roll an unusual die. Draw
that one yourself with
[`RngStream.randbelow`][osrlib.core.rng.RngStream.randbelow], which takes any positive
bound.
"""

# Canonical ASCII digits only, no leading zeros, bounded lengths. What parse accepts is
# the grammar, so the things \d would quietly admit (Unicode digits, 5000-digit numerals)
# are rejected here rather than found later to be part of the contract.
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
    """A dice expression taken apart: how many dice, of what size, plus what, times what.

    [`parse`][osrlib.core.dice.parse] returns one. Read its fields to show a roll before
    it happens ("2d6+1, ×10"), to work out a range, or to check what a piece of content
    will roll. Pass it back to [`roll`][osrlib.core.dice.roll] when you want the result.
    Handing `roll` the same expression object repeatedly saves reparsing the string each
    time.

    Building one directly is legal and validated, so you can assemble an expression from
    parts instead of formatting a string. The model is frozen, so make a changed copy with
    `expression.model_copy(update={"count": 4})`.

    Examples:
        ```python
        from osrlib.core.dice import parse

        expression = parse("2d6+1×10")
        assert (expression.count, expression.sides) == (2, 6)
        assert (expression.modifier, expression.multiplier) == (1, 10)

        # The lowest and highest totals the expression can produce.
        lowest = (expression.count + expression.modifier) * expression.multiplier
        highest = (expression.count * expression.sides + expression.modifier) * expression.multiplier
        assert (lowest, highest) == (30, 130)
        ```
    """

    model_config = ConfigDict(frozen=True)

    count: int = Field(ge=1)
    """How many dice to roll. At least 1, and at most 999 from a parsed string."""

    sides: int
    """How many sides each die has.

    One of [`ALLOWED_SIDES`][osrlib.core.dice.ALLOWED_SIDES]. Any other value raises a
    pydantic `ValidationError`. A `d%` in the source string arrives here as 100.
    """

    modifier: int = 0
    """What to add to the sum of the dice, before the multiplier. Negative subtracts, and 0 means none."""

    multiplier: int = Field(default=1, ge=1)
    """What to multiply the modified sum by, after the modifier. 1 means none.

    B/X treasure rolls are printed this way, as in `2d6×1000` gold pieces.
    """

    @field_validator("sides")
    @classmethod
    def _sides_must_be_allowed(cls, value: int) -> int:
        if value not in ALLOWED_SIDES:
            raise ValueError(f"die size must be one of {sorted(ALLOWED_SIDES)}, got {value}")
        return value


class RollResult(BaseModel):
    """What a dice roll produced: each die, and the total they add up to.

    [`roll`][osrlib.core.dice.roll] returns one. Take `total` for the number the rules
    call for, and show `rolls` to the player. The game's events include the individual
    dice for the same reason: so an interface can put them on the screen.

    Attack and damage resolution in [`osrlib.core.combat`][osrlib.core.combat] hands back
    results of this shape too, so one piece of display code covers both.

    Examples:
        ```python
        from osrlib.core.dice import roll
        from osrlib.core.rng import RngStreams

        result = roll("2d6+1×10", RngStreams(master_seed=42).get("treasure"))
        assert result.rolls == (4, 2)
        assert result.total == (sum(result.rolls) + result.modifier) * result.multiplier
        assert result.total == 70
        ```
    """

    model_config = ConfigDict(frozen=True)

    rolls: tuple[int, ...]
    """What each die came up, in the order they were rolled. One entry per die."""

    modifier: int
    """The modifier that was added to the sum of the dice, copied from the expression."""

    multiplier: int
    """The multiplier that was applied after the modifier, copied from the expression."""

    total: int
    """The number the rules use: the sum of the dice, plus the modifier, times the multiplier.

    Not clamped, so a `1d4-1` roll can total 0.
    """


def parse(expression: str) -> DiceExpression:
    """Take a dice expression string apart into its dice, modifier, and multiplier.

    Call this to check an expression a player or an adventure file supplied before you
    trust it, or to read its parts. To roll instead, call
    [`roll`][osrlib.core.dice.roll], which parses the string itself. Parsing first saves
    nothing unless you roll the same expression many times.

    Args:
        expression: A dice expression such as `"3d6"`, `"d%"`, or `"2d6+1×10"`. Case doesn't
            matter and surrounding whitespace is ignored.

    Returns:
        The expression taken apart, frozen.

    Raises:
        ContentValidationError: If the string doesn't match the grammar: a die size
            outside [`ALLOWED_SIDES`][osrlib.core.dice.ALLOWED_SIDES], zero dice, a zero
            multiplier, a numeral with a leading zero or a non-ASCII digit, whitespace
            inside the expression, or the parts out of the fixed dice, modifier,
            multiplier order.
        TypeError: If `expression` isn't a string.

    Examples:
        ```python
        from osrlib.core.dice import parse
        from osrlib.errors import ContentValidationError

        assert parse("3d6").count == 3

        # A bare d means one die, and d% means d100.
        assert parse("d%").count == 1
        assert parse("d%").sides == 100

        # A die the rules never roll is refused.
        try:
            parse("2d7")
        except ContentValidationError as error:
            assert "die size must be one of" in str(error)
        ```
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
    """Roll a dice expression, drawing from the stream you pass.

    This is the one way dice are rolled in osrlib. Get a stream from
    [`RngStreams.get`][osrlib.core.rng.RngStreams.get] and reuse it: the same seed and
    the same sequence of calls give the same rolls, which is what makes a saved game
    replay. Take the number out of `total` and show `rolls` if your interface displays
    dice.

    Dice are rolled left to right, and one die of size S draws
    [`randbelow(S)`][osrlib.core.rng.RngStream.randbelow] plus 1. That mapping is fixed:
    changing it would change every roll in every existing saved game.

    Args:
        expression: What to roll, as a string this parses for you or as a
            [`DiceExpression`][osrlib.core.dice.DiceExpression] you already parsed.
        stream: The stream the dice draw from. It advances, so passing the same stream
            again gives different dice.

    Returns:
        The dice and the total. The total isn't clamped, so it can be zero or negative
        when the expression has a negative modifier.

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

        # Modifier then multiplier: (2d6 + 1) × 10, in that order.
        gold = roll("2d6+1×10", stream)
        assert gold.total == (sum(gold.rolls) + 1) * 10

        # A fresh stream on the same seed repeats the sequence exactly.
        again = roll("3d6", RngStreams(master_seed=42).get("treasure"))
        assert again.rolls == scores.rolls
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
