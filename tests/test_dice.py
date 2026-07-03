"""Tests for osrlib.core.dice — the expression grammar and roll mechanics.

The determinism goldens were generated from this implementation with master seed 42,
key "dice", after the RNG layer was validated against its external anchors (see
test_rng.py); they lock roll behavior against accidental change.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from osrlib.core.dice import ALLOWED_SIDES, DiceExpression, parse, roll
from osrlib.core.rng import RngStream, RngStreams
from osrlib.errors import ContentValidationError, OsrlibError

# expression -> (count, sides, modifier, multiplier)
ACCEPTED = [
    ("d6", (1, 6, 0, 1)),
    ("3d6", (3, 6, 0, 1)),
    ("d%", (1, 100, 0, 1)),
    ("2d%", (2, 100, 0, 1)),
    ("2d%+5×2", (2, 100, 5, 2)),
    ("2d6×10", (2, 6, 0, 10)),
    ("2D6X10", (2, 6, 0, 10)),
    ("2d6*10", (2, 6, 0, 10)),
    ("1d4-1", (1, 4, -1, 1)),
    ("2d6+1×10", (2, 6, 1, 10)),
    ("1d6X10", (1, 6, 0, 10)),
    ("2D6", (2, 6, 0, 1)),
    ("1d20+5", (1, 20, 5, 1)),
    ("  3d6  ", (3, 6, 0, 1)),
    ("\t2d6\n", (2, 6, 0, 1)),
    ("12d8-13", (12, 8, -13, 1)),
    ("999d6", (999, 6, 0, 1)),
    ("2d6+0", (2, 6, 0, 1)),
    ("2d6+999999", (2, 6, 999999, 1)),
    ("2d6×999999", (2, 6, 0, 999999)),
]

REJECTED = [
    "3d7",  # die size outside the closed set
    "d101",
    "0d6",  # N >= 1
    "2d6×0",  # K >= 1
    "2d6×10+1",  # component order is fixed: dice, modifier, multiplier
    "2d6 + 1",  # internal whitespace
    "2 d6",
    "",
    "   ",
    "d",
    "2d",
    "6",
    "3x6",
    "-1d6",
    "2d6+",
    "2d6x",
    "2d6+1.5",
    "2dd6",
    "d%%",
    "2d6×10×10",
    "1000d6",  # dice count capped at 999
    "2d6+1000000",  # modifier magnitude capped at 999999
    "2d6×1000000",  # multiplier capped at 999999
    "1d" + "9" * 5000,  # must reject, not hit CPython's int-conversion digit limit
    "02d6",  # leading zeros are non-canonical
    "2d06",
    "2d6+01",
    "2d6×01",
    "３d６",  # numerals are ASCII digits only, though \d would match these
    "٣d6",
    "๓d6",
    "2d6+١",
]


class TestGrammar:
    @pytest.mark.parametrize(("expression", "expected"), ACCEPTED)
    def test_accepted(self, expression, expected):
        parsed = parse(expression)
        assert (parsed.count, parsed.sides, parsed.modifier, parsed.multiplier) == expected

    @pytest.mark.parametrize("expression", REJECTED)
    def test_rejected(self, expression):
        with pytest.raises(ContentValidationError):
            parse(expression)

    def test_rejection_is_an_osrlib_error(self):
        with pytest.raises(OsrlibError):
            parse("3d7")

    @pytest.mark.parametrize("not_a_string", [None, 6, 3.5, ["3d6"]])
    def test_non_string_raises_type_error(self, not_a_string):
        with pytest.raises(TypeError):
            parse(not_a_string)

    @pytest.mark.parametrize("not_an_expression", [None, 6, ["3d6"]])
    def test_roll_rejects_non_expression_types(self, not_an_expression):
        with pytest.raises(TypeError):
            roll(not_an_expression, RngStream.from_seed_material(42, "types"))


class TestRoll:
    def test_determinism_goldens(self):
        # Master seed 42, key "dice": successive rolls on one stream, locked.
        stream = RngStream.from_seed_material(42, "dice")
        expected = [
            ("3d6", (2, 4, 3), 9),
            ("3d6", (1, 5, 6), 12),
            ("2d6×10", (2, 3), 50),
            ("1d4-1", (3,), 2),
            ("2d6+1×10", (2, 2), 50),
            ("d%", (35,), 35),
        ]
        for expression, rolls, total in expected:
            result = roll(expression, stream)
            assert (result.rolls, result.total) == (rolls, total), expression

    def test_precedence_modifier_before_multiplier(self):
        # 2d6+1×10 means (2d6 + 1) × 10, not 2d6 + 10 — pinned by the spec's grammar,
        # not ordinary arithmetic precedence.
        stream = RngStream.from_seed_material(42, "precedence")
        result = roll("2d6+1×10", stream)
        assert result.total == (sum(result.rolls) + 1) * 10
        assert result.total != sum(result.rolls) + 10

    def test_negative_and_zero_totals_permitted(self):
        # Results are not clamped; minimum-1-damage is combat's rule (Phase 2).
        stream = RngStream.from_seed_material(42, "negative")
        totals = {roll("1d4-2", stream).total for _ in range(50)}
        assert totals == {-1, 0, 1, 2}

    def test_roll_accepts_parsed_expression(self):
        expression = parse("2d6+1")
        result = roll(expression, RngStream.from_seed_material(42, "parsed"))
        assert result.total == sum(result.rolls) + 1

    def test_rolls_draw_left_to_right(self):
        # One die of size S is randbelow(S) + 1, dice drawn in left-to-right order.
        stream = RngStream.from_seed_material(42, "order")
        reference = RngStream.from_seed_material(42, "order")
        result = roll("4d8", stream)
        assert result.rolls == tuple(reference.randbelow(8) + 1 for _ in range(4))

    def test_models_are_frozen(self):
        expression = parse("3d6")
        with pytest.raises(ValidationError):
            expression.count = 4
        result = roll("3d6", RngStream.from_seed_material(42, "frozen"))
        with pytest.raises(ValidationError):
            result.total = 0


def expression_components():
    return st.tuples(
        st.one_of(st.none(), st.integers(min_value=1, max_value=20)),
        st.sampled_from(sorted(ALLOWED_SIDES)),
        st.one_of(st.none(), st.integers(min_value=-50, max_value=50)),
        st.one_of(st.none(), st.integers(min_value=1, max_value=20)),
        st.sampled_from(["d", "D"]),
        st.sampled_from(["x", "X", "×", "*"]),
    )


def build_expression(components) -> tuple[str, int, int, int, int]:
    count, sides, modifier, multiplier, die_char, mult_char = components
    text = f"{count if count is not None else ''}{die_char}{sides}"
    if modifier is not None:
        text += f"{modifier:+d}"
    if multiplier is not None:
        text += f"{mult_char}{multiplier}"
    return text, count or 1, sides, modifier or 0, multiplier or 1


class TestProperties:
    @given(expression_components())
    def test_parse_matches_components(self, components):
        text, count, sides, modifier, multiplier = build_expression(components)
        parsed = parse(text)
        assert parsed == DiceExpression(count=count, sides=sides, modifier=modifier, multiplier=multiplier)

    @given(expression_components(), st.integers(min_value=0, max_value=(1 << 128) - 1))
    def test_roll_within_bounds_and_consistent(self, components, master_seed):
        text, count, sides, modifier, multiplier = build_expression(components)
        result = roll(text, RngStreams(master_seed).get("hypothesis"))
        assert len(result.rolls) == count
        assert all(1 <= die <= sides for die in result.rolls)
        assert result.total == (sum(result.rolls) + modifier) * multiplier
        assert (count + modifier) * multiplier <= result.total <= (count * sides + modifier) * multiplier
