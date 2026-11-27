"""Tests for osrlib.core.abilities — checks, and the adjustment step's rule table."""

import functools
import itertools

import pytest
from hypothesis import given
from hypothesis import strategies as st

from osrlib.core.abilities import (
    ADJUSTMENT_FLOOR,
    MAX_SCORE,
    AbilityAdjustment,
    AbilityAdjustmentLimit,
    AbilityScore,
    AdjustmentLimits,
    ability_check,
    adjustment_limits,
    apply_adjustment,
    open_doors_check,
    validate_adjustment,
)
from osrlib.core.rng import RngStream
from osrlib.core.validation import Rejection
from osrlib.data import load_ability_tables, load_classes

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


# ---------------------------------------------------------------------- adjustment_limits (#358)


def _code(name: str, **params: int | str) -> Rejection:
    return Rejection(code=f"creation.adjustment.{name}", params=params)


def _limit(score: int, lowest: int, lowest_reason: Rejection, highest: int, highest_reason: Rejection):
    return AbilityAdjustmentLimit(
        can_lower=lowest < score,
        can_raise=highest > score,
        lowest=lowest,
        lowest_reason=lowest_reason,
        highest=highest,
        highest_reason=highest_reason,
    )


def _fighter_scores(**overrides: int) -> dict[AbilityScore, int]:
    base = {STR: 16, INT: 13, WIS: 12, DEX: 11, CON: 14, CHA: 9}
    base.update({AbilityScore(key): value for key, value in overrides.items()})
    return base


class TestAdjustmentLimitsExamples:
    def test_the_fighter_in_the_issue(self):
        room = _code("no_room_to_raise", max_points=2)
        assert adjustment_limits(_fighter_scores(), (STR,)) == AdjustmentLimits(
            lower_step=2,
            raise_step=1,
            can_trade=True,
            max_points=2,
            max_points_reason=room,
            abilities={
                STR: _limit(
                    16,
                    16,
                    _code("prime_requisite_lowered", ability="str"),
                    18,
                    _code("above_cap", ability="str", score=16, amount=3),
                ),
                INT: _limit(
                    13,
                    9,
                    _code("below_floor", ability="int", score=13, amount=6),
                    13,
                    _code("raise_not_prime_requisite", ability="int"),
                ),
                WIS: _limit(
                    12,
                    10,
                    _code("below_floor", ability="wis", score=12, amount=4),
                    12,
                    _code("raise_not_prime_requisite", ability="wis"),
                ),
                DEX: _limit(
                    11, 11, _code("not_lowerable", ability="dex"), 11, _code("raise_not_prime_requisite", ability="dex")
                ),
                CON: _limit(
                    14, 14, _code("not_lowerable", ability="con"), 14, _code("raise_not_prime_requisite", ability="con")
                ),
                CHA: _limit(
                    9, 9, _code("not_lowerable", ability="cha"), 9, _code("raise_not_prime_requisite", ability="cha")
                ),
            },
        )

    def test_a_fighter_with_str_18_can_buy_nothing(self):
        room = _code("no_room_to_raise", max_points=0)
        assert adjustment_limits(_fighter_scores(str=18), (STR,)) == AdjustmentLimits(
            lower_step=2,
            raise_step=1,
            can_trade=False,
            max_points=0,
            max_points_reason=room,
            abilities={
                STR: _limit(
                    18,
                    18,
                    _code("prime_requisite_lowered", ability="str"),
                    18,
                    _code("above_cap", ability="str", score=18, amount=1),
                ),
                INT: _limit(13, 13, room, 13, _code("raise_not_prime_requisite", ability="int")),
                WIS: _limit(12, 12, room, 12, _code("raise_not_prime_requisite", ability="wis")),
                DEX: _limit(
                    11, 11, _code("not_lowerable", ability="dex"), 11, _code("raise_not_prime_requisite", ability="dex")
                ),
                CON: _limit(
                    14, 14, _code("not_lowerable", ability="con"), 14, _code("raise_not_prime_requisite", ability="con")
                ),
                CHA: _limit(
                    9, 9, _code("not_lowerable", ability="cha"), 9, _code("raise_not_prime_requisite", ability="cha")
                ),
            },
        )

    def test_a_fighter_with_str_17_can_lower_int_by_two_only(self):
        limits = adjustment_limits(_fighter_scores(str=17), (STR,))
        assert limits.max_points == 1
        assert limits.abilities[INT] == _limit(
            13, 11, _code("no_room_to_raise", max_points=1), 13, _code("raise_not_prime_requisite", ability="int")
        )

    def test_a_tie_reports_no_room_to_raise(self):
        limits = adjustment_limits(_fighter_scores(int=11, wis=11), (STR,))
        reason = _code("no_room_to_raise", max_points=2)
        assert (limits.max_points, limits.max_points_reason) == (2, reason)
        assert limits.abilities[STR].highest_reason == _code("above_cap", ability="str", score=16, amount=3)

    def test_a_prime_requisite_with_more_room_than_points_reports_no_room_to_lower(self):
        limits = adjustment_limits(_fighter_scores(str=9, int=11, wis=10), (STR,))
        reason = _code("no_room_to_lower", max_points=1)
        assert (limits.max_points, limits.max_points_reason) == (1, reason)
        assert limits.abilities[STR].highest == 10
        assert limits.abilities[STR].highest_reason == reason
        assert limits.abilities[INT].lowest_reason == _code("below_floor", ability="int", score=11, amount=4)

    def test_the_abilities_come_in_ability_score_order(self):
        assert list(adjustment_limits(_fighter_scores(), (STR,)).abilities) == list(AbilityScore)

    def test_limits_round_trip_through_json(self):
        limits = adjustment_limits(_fighter_scores(), (DEX,), may_not_lower=(STR,))
        assert AdjustmentLimits.model_validate_json(limits.model_dump_json()) == limits


# The brute-force grid. Each score set is (STR, INT, WIS, DEX, CON, CHA). Together they put STR, INT, WIS,
# and DEX at 3, 8, 9, 10, 11, 17, and 18, and give each class cases where the points the lowerable abilities
# can pay for are fewer than, equal to, and more than the points the prime requisites have room for.
_SCORE_SETS = (
    (16, 13, 12, 11, 14, 9),
    (18, 13, 12, 11, 14, 9),
    (17, 13, 12, 11, 14, 9),
    (16, 11, 11, 16, 10, 10),
    (15, 13, 13, 14, 10, 10),
    (12, 11, 10, 15, 13, 8),
    (3, 18, 17, 3, 10, 10),
    (18, 3, 8, 18, 10, 10),
    (17, 8, 18, 8, 18, 3),
    (8, 17, 10, 17, 3, 18),
    (10, 18, 3, 10, 10, 10),
    (11, 10, 18, 9, 11, 17),
    (9, 9, 9, 9, 9, 9),
    (10, 10, 10, 10, 10, 10),
    (11, 11, 11, 11, 11, 11),
    (13, 15, 9, 16, 12, 12),
    (3, 3, 3, 3, 3, 3),
    (18, 18, 18, 18, 18, 18),
)

_SYNTHETIC_CLASSES = (
    ((STR, WIS), ()),
    ((DEX,), (INT,)),
    ((INT, WIS), (STR,)),
    ((CON,), (WIS, INT)),
    ((), ()),
)


def _class_arguments() -> list[tuple[tuple[AbilityScore, ...], tuple[AbilityScore, ...]]]:
    built_in = [(definition.prime_requisites, definition.may_not_lower) for definition in load_classes().classes]
    return built_in + list(_SYNTHETIC_CLASSES)


_CASES = [
    (dict(zip(AbilityScore, score_set, strict=True)), prime_requisites, may_not_lower)
    for score_set in _SCORE_SETS
    for prime_requisites, may_not_lower in _class_arguments()
]


def _lowering_amounts(score: int) -> range:
    """Every even amount to lower an ability by, from 0 to one step past the floor."""
    return range(0, max(0, score - ADJUSTMENT_FLOOR) + 3, 2)


@functools.cache
def _candidates(case_index: int) -> list[tuple[AbilityAdjustment, bool]]:
    """Every candidate trade for one case, each with whether `validate_adjustment` accepts it.

    Lowered amounts are every even amount to one step past the floor for STR, INT, and WIS. Each of those sets is
    also tried with its first amount made odd, and with one step off DEX, CON, or CHA, which no rule lets a trade
    lower, taken in turn. Raised amounts run one point past the cap for every prime requisite, and the first ability
    that isn't a prime requisite is tried at one point. Each set of lowered amounts is paired with the raises whose
    total is within one point of half the lowered total. A raise further off breaks the balance rule, and leaving it
    out keeps the enumeration to a few seconds.
    """
    case_scores, prime_requisites, may_not_lower = _CASES[case_index]
    lowerable = (STR, INT, WIS)
    lowered_options: list[dict[AbilityScore, int]] = []
    evens = itertools.product(*(_lowering_amounts(case_scores[a]) for a in lowerable))
    for index, amounts in enumerate(evens):
        base = {a: amount for a, amount in zip(lowerable, amounts, strict=True) if amount}
        lowered_options.append(base)
        lowered_options.append({**base, (DEX, CON, CHA)[index % 3]: 2})
        if base:
            first = next(iter(base))
            lowered_options.append({**base, first: base[first] - 1})
    most_points = max(sum(option.values()) for option in lowered_options) // 2
    raise_targets = [a for a in AbilityScore if a in prime_requisites]
    outsider = next(a for a in AbilityScore if a not in prime_requisites)
    raise_ranges = [range(min(MAX_SCORE - case_scores[a] + 1, most_points + 1) + 1) for a in raise_targets]
    raised_by_total: dict[int, list[dict[AbilityScore, int]]] = {}
    for amounts in itertools.product(*raise_ranges, (0, 1)):
        raised = {a: amount for a, amount in zip([*raise_targets, outsider], amounts, strict=True) if amount}
        raised_by_total.setdefault(sum(amounts), []).append(raised)
    candidates = []
    for lowered in lowered_options:
        points = sum(lowered.values()) // 2
        for raised in itertools.chain.from_iterable(
            raised_by_total.get(t, []) for t in (points - 1, points, points + 1)
        ):
            trade = AbilityAdjustment(lowered=lowered, raised=raised)
            legal = validate_adjustment(case_scores, trade, prime_requisites, may_not_lower) == []
            candidates.append((trade, legal))
    return candidates


_CASE_IDS = range(len(_CASES))


class TestAdjustmentLimitsAgainstValidateAdjustment:
    @pytest.mark.parametrize("case_index", _CASE_IDS)
    def test_each_limit_is_the_extreme_over_the_legal_trades(self, case_index):
        case_scores, prime_requisites, may_not_lower = _CASES[case_index]
        limits = adjustment_limits(case_scores, prime_requisites, may_not_lower)
        legal = [trade for trade, ok in _candidates(case_index) if ok]
        assert AbilityAdjustment() in legal
        bought = [sum(trade.raised.values()) for trade in legal]
        assert limits.max_points == max(bought)
        assert limits.can_trade == (max(bought) > 0)
        # The prime requisites are the limit exactly when some legal trade takes every one of them to the cap.
        every_cap_reached = any(
            all(case_scores[a] + trade.raised.get(a, 0) == MAX_SCORE for a in prime_requisites) for trade in legal
        )
        code = "no_room_to_raise" if every_cap_reached else "no_room_to_lower"
        assert limits.max_points_reason == _code(code, max_points=max(bought))
        for ability in AbilityScore:
            score = case_scores[ability]
            limit = limits.abilities[ability]
            lowest = min(score - trade.lowered.get(ability, 0) for trade in legal)
            highest = max(score + trade.raised.get(ability, 0) for trade in legal)
            assert (limit.lowest, limit.highest) == (lowest, highest), ability
            assert limit.can_lower == (lowest < score)
            assert limit.can_raise == (highest > score)

    @pytest.mark.parametrize("case_index", _CASE_IDS)
    def test_each_reason_is_what_validate_adjustment_reports_one_step_past_the_limit(self, case_index):
        case_scores, prime_requisites, may_not_lower = _CASES[case_index]
        limits = adjustment_limits(case_scores, prime_requisites, may_not_lower)
        for ability in AbilityScore:
            score = case_scores[ability]
            limit = limits.abilities[ability]
            if limit.lowest_reason != limits.max_points_reason:
                trade = AbilityAdjustment(lowered={ability: score - limit.lowest + limits.lower_step})
                assert (
                    validate_adjustment(case_scores, trade, prime_requisites, may_not_lower)[0] == limit.lowest_reason
                )
            if limit.highest_reason != limits.max_points_reason:
                trade = AbilityAdjustment(raised={ability: limit.highest - score + limits.raise_step})
                assert (
                    validate_adjustment(case_scores, trade, prime_requisites, may_not_lower)[0] == limit.highest_reason
                )

    @pytest.mark.parametrize("case_index", _CASE_IDS)
    def test_the_client_procedure_accepts_exactly_the_legal_trades(self, case_index):
        case_scores, prime_requisites, may_not_lower = _CASES[case_index]
        limits = adjustment_limits(case_scores, prime_requisites, may_not_lower)
        for trade, legal in _candidates(case_index):
            total_lowered = sum(trade.lowered.values())
            total_raised = sum(trade.raised.values())
            accepted = (
                all(
                    amount % limits.lower_step == 0 and amount <= case_scores[a] - limits.abilities[a].lowest
                    for a, amount in trade.lowered.items()
                )
                and all(amount <= limits.abilities[a].highest - case_scores[a] for a, amount in trade.raised.items())
                and total_raised == total_lowered // limits.lower_step * limits.raise_step
                and total_raised <= limits.max_points
            )
            assert accepted == legal, trade

    def test_the_grid_covers_every_kind_of_limit(self):
        seen: set[str] = set()
        for case_scores, prime_requisites, may_not_lower in _CASES:
            limits = adjustment_limits(case_scores, prime_requisites, may_not_lower)
            seen.add(limits.max_points_reason.code)
            if limits.can_trade:
                seen.add(f"trade:{limits.max_points_reason.code}")
            for limit in limits.abilities.values():
                seen.add(limit.lowest_reason.code)
                seen.add(limit.highest_reason.code)
        codes = {
            "not_lowerable",
            "prime_requisite_lowered",
            "class_restriction",
            "below_floor",
            "above_cap",
            "raise_not_prime_requisite",
            "no_room_to_raise",
            "no_room_to_lower",
        }
        assert {f"creation.adjustment.{code}" for code in codes} <= seen
        assert {"trade:creation.adjustment.no_room_to_raise", "trade:creation.adjustment.no_room_to_lower"} <= seen

    def test_the_grid_has_ties_and_both_inequalities(self):
        relations = set()
        for case_scores, prime_requisites, may_not_lower in _CASES:
            payable = sum(
                max(0, (case_scores[a] - ADJUSTMENT_FLOOR) // 2)
                for a in (STR, INT, WIS)
                if a not in prime_requisites and a not in may_not_lower
            )
            room = sum(MAX_SCORE - case_scores[a] for a in AbilityScore if a in prime_requisites)
            if payable and room:
                relations.add((payable > room) - (payable < room))
        assert relations == {-1, 0, 1}
