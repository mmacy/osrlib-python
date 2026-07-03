"""Tests for osrlib.core.abilities — checks, and the adjustment step's rule table."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from osrlib.core.abilities import (
    AbilityAdjustment,
    AbilityScore,
    ability_check,
    apply_adjustment,
    open_doors_check,
    validate_adjustment,
)
from osrlib.core.rng import RngStream
from osrlib.data import load_ability_tables

STR = AbilityScore.STR
INT = AbilityScore.INT
WIS = AbilityScore.WIS
DEX = AbilityScore.DEX
CON = AbilityScore.CON
CHA = AbilityScore.CHA


def stream(seed: int = 0) -> RngStream:
    return RngStream.from_seed_material(seed, "test")


def scores(**overrides: int) -> dict[AbilityScore, int]:
    base = {STR: 13, INT: 13, WIS: 13, DEX: 13, CON: 13, CHA: 13}
    base.update({AbilityScore(key): value for key, value in overrides.items()})
    return base


class TestAbilityCheck:
    def test_roll_at_or_under_score_succeeds(self):
        for seed in range(40):
            result = ability_check(10, stream(seed))
            if result.roll not in (1, 20):
                assert result.success == (result.roll <= 10)

    def test_modifier_applies_to_the_roll(self):
        for seed in range(40):
            plain = ability_check(10, stream(seed))
            hard = ability_check(10, stream(seed), modifier=4)
            if plain.roll not in (1, 20):
                assert hard.success == (plain.roll + 4 <= 10)

    def test_natural_1_always_succeeds_and_20_always_fails(self):
        # Inverted from attack rolls, where a natural 20 always hits.
        seen = set()
        for seed in range(600):
            result = ability_check(3, stream(seed), modifier=-4)
            if result.roll == 1:
                assert result.success
                seen.add(1)
        for seed in range(600):
            result = ability_check(18, stream(seed), modifier=-4)
            if result.roll == 20:
                assert not result.success
                seen.add(20)
        assert seen == {1, 20}

    def test_out_of_range_score_raises(self):
        with pytest.raises(ValueError):
            ability_check(2, stream())
        with pytest.raises(ValueError):
            ability_check(19, stream())


class TestOpenDoors:
    def test_roll_at_or_under_chance_succeeds(self):
        for seed in range(40):
            result = open_doors_check(2, stream(seed))
            assert result.success == (result.roll <= 2)

    def test_chances_match_strength_bands(self):
        tables = load_ability_tables()
        assert tables.open_doors_chance(3) == 1
        assert tables.open_doors_chance(9) == 2
        assert tables.open_doors_chance(13) == 3
        assert tables.open_doors_chance(16) == 4
        assert tables.open_doors_chance(18) == 5

    def test_out_of_range_chance_raises(self):
        with pytest.raises(ValueError):
            open_doors_check(7, stream())


class TestAdjustment:
    def test_legal_two_for_one_trade(self):
        adjustment = AbilityAdjustment(lowered={INT: 2}, raised={STR: 1})
        assert validate_adjustment(scores(), adjustment, (STR,)) == []
        adjusted = apply_adjustment(scores(), adjustment, (STR,))
        assert adjusted[INT] == 11
        assert adjusted[STR] == 14

    def test_empty_adjustment_is_a_legal_noop(self):
        adjustment = AbilityAdjustment()
        assert validate_adjustment(scores(), adjustment, (STR,)) == []
        assert apply_adjustment(scores(), adjustment, (STR,)) == scores()

    def test_only_str_int_wis_may_be_lowered(self):
        adjustment = AbilityAdjustment(lowered={DEX: 2}, raised={STR: 1})
        codes = [rejection.code for rejection in validate_adjustment(scores(), adjustment, (STR,))]
        assert "creation.adjustment.not_lowerable" in codes

    def test_prime_requisite_may_not_be_lowered(self):
        adjustment = AbilityAdjustment(lowered={INT: 2}, raised={STR: 1})
        codes = [rejection.code for rejection in validate_adjustment(scores(), adjustment, (STR, INT))]
        assert "creation.adjustment.prime_requisite_lowered" in codes

    def test_class_restriction_thief_str(self):
        adjustment = AbilityAdjustment(lowered={STR: 2}, raised={DEX: 1})
        codes = [
            rejection.code for rejection in validate_adjustment(scores(), adjustment, (DEX,), may_not_lower=(STR,))
        ]
        assert "creation.adjustment.class_restriction" in codes

    def test_elf_may_lower_wis_only(self):
        # The elf's prime requisites are INT and STR, so of the three lowerable
        # abilities only WIS remains legal.
        legal = AbilityAdjustment(lowered={WIS: 2}, raised={INT: 1})
        assert validate_adjustment(scores(), legal, (INT, STR)) == []
        illegal = AbilityAdjustment(lowered={INT: 2}, raised={STR: 1})
        assert validate_adjustment(scores(), illegal, (INT, STR)) != []

    def test_reduction_must_be_even_per_score(self):
        adjustment = AbilityAdjustment(lowered={INT: 1, WIS: 1}, raised={STR: 1})
        codes = [rejection.code for rejection in validate_adjustment(scores(), adjustment, (STR,))]
        assert codes.count("creation.adjustment.reduction_not_even") == 2

    def test_raise_must_target_a_prime_requisite(self):
        adjustment = AbilityAdjustment(lowered={INT: 2}, raised={CHA: 1})
        codes = [rejection.code for rejection in validate_adjustment(scores(), adjustment, (STR,))]
        assert "creation.adjustment.raise_not_prime_requisite" in codes

    def test_no_score_below_nine(self):
        adjustment = AbilityAdjustment(lowered={INT: 4}, raised={STR: 2})
        codes = [rejection.code for rejection in validate_adjustment(scores(int=12), adjustment, (STR,))]
        assert "creation.adjustment.below_floor" in codes

    def test_no_score_above_eighteen(self):
        adjustment = AbilityAdjustment(lowered={INT: 4}, raised={STR: 2})
        codes = [rejection.code for rejection in validate_adjustment(scores(str=17), adjustment, (STR,))]
        assert "creation.adjustment.above_cap" in codes

    def test_points_must_balance_exactly(self):
        undersspent = AbilityAdjustment(lowered={INT: 4}, raised={STR: 1})
        codes = [rejection.code for rejection in validate_adjustment(scores(), undersspent, (STR,))]
        assert "creation.adjustment.points_mismatch" in codes
        overspent = AbilityAdjustment(lowered={INT: 2}, raised={STR: 2})
        codes = [rejection.code for rejection in validate_adjustment(scores(), overspent, (STR,))]
        assert "creation.adjustment.points_mismatch" in codes

    def test_raise_distributes_freely_among_prime_requisites(self):
        adjustment = AbilityAdjustment(lowered={WIS: 4}, raised={INT: 1, STR: 1})
        assert validate_adjustment(scores(), adjustment, (INT, STR)) == []

    def test_apply_rejects_illegal_adjustment(self):
        adjustment = AbilityAdjustment(lowered={DEX: 2}, raised={STR: 1})
        with pytest.raises(ValueError):
            apply_adjustment(scores(), adjustment, (STR,))

    def test_nonpositive_amounts_rejected_by_the_model(self):
        with pytest.raises(ValueError):
            AbilityAdjustment(lowered={INT: 0}, raised={})
        with pytest.raises(ValueError):
            AbilityAdjustment(lowered={}, raised={STR: -1})


@given(
    lowered=st.dictionaries(st.sampled_from(list(AbilityScore)), st.integers(min_value=1, max_value=8), max_size=3),
    raised=st.dictionaries(st.sampled_from(list(AbilityScore)), st.integers(min_value=1, max_value=8), max_size=3),
    base=st.dictionaries(
        st.sampled_from(list(AbilityScore)),
        st.integers(min_value=3, max_value=18),
        min_size=6,
        max_size=6,
    ),
)
def test_random_adjustments_are_rejected_never_raised(
    lowered: dict[AbilityScore, int], raised: dict[AbilityScore, int], base: dict[AbilityScore, int]
):
    """Any structurally well-formed adjustment yields rejections or applies cleanly."""
    adjustment = AbilityAdjustment(lowered=lowered, raised=raised)
    rejections = validate_adjustment(base, adjustment, (STR, INT), may_not_lower=(WIS,))
    if not rejections:
        adjusted = apply_adjustment(base, adjustment, (STR, INT), may_not_lower=(WIS,))
        assert all(3 <= value <= 18 for value in adjusted.values())
        assert sum(adjusted.values()) == sum(base.values()) - sum(lowered.values()) + sum(raised.values())
