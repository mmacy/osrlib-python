"""Tests for osrlib.core.clock — units, the game clock, and boundary reporting."""

import pytest

from osrlib.core.clock import (
    ROUNDS_PER_DAY,
    ROUNDS_PER_TURN,
    SECONDS_PER_ROUND,
    TURNS_PER_DAY,
    BoundaryCrossing,
    GameClock,
    TimeUnit,
)


class TestUnits:
    def test_unit_constants(self):
        assert SECONDS_PER_ROUND == 10
        assert ROUNDS_PER_TURN == 60  # a 10-minute turn of 10-second rounds
        assert TURNS_PER_DAY == 144
        assert ROUNDS_PER_DAY == 8640

    def test_unit_conversions(self):
        clock = GameClock(rounds=ROUNDS_PER_DAY + 3 * ROUNDS_PER_TURN + 7)
        assert clock.rounds == 8827
        assert clock.turns == TURNS_PER_DAY + 3
        assert clock.days == 1

    def test_advance_unit_arithmetic(self):
        clock = GameClock()
        clock.advance(2, TimeUnit.DAY)
        clock.advance(3, TimeUnit.TURN)
        clock.advance(5, TimeUnit.ROUND)
        assert clock.rounds == 2 * ROUNDS_PER_DAY + 3 * ROUNDS_PER_TURN + 5

    def test_advance_default_unit_is_rounds(self):
        clock = GameClock()
        clock.advance(7)
        assert clock.rounds == 7

    def test_advance_accepts_unit_string(self):
        clock = GameClock()
        clock.advance(1, "turn")
        assert clock.rounds == ROUNDS_PER_TURN


class TestBoundaryReporting:
    def test_no_boundary_within_a_turn(self):
        clock = GameClock()
        assert clock.advance(59) == []

    def test_crossing_a_turn_boundary(self):
        clock = GameClock(rounds=59)
        assert clock.advance(2) == [BoundaryCrossing(unit=TimeUnit.TURN, index=1, round=60)]

    def test_exact_landing_reports_the_boundary(self):
        # A torch lit at turn 0 expires when the clock reaches turn 6, not turn 7.
        clock = GameClock()
        crossings = clock.advance(6, TimeUnit.TURN)
        assert crossings[-1] == BoundaryCrossing(unit=TimeUnit.TURN, index=6, round=360)
        assert [c.index for c in crossings] == [1, 2, 3, 4, 5, 6]

    def test_starting_position_is_not_re_reported(self):
        clock = GameClock()
        clock.advance(6, TimeUnit.TURN)
        crossings = clock.advance(1, TimeUnit.TURN)
        assert crossings == [BoundaryCrossing(unit=TimeUnit.TURN, index=7, round=420)]

    def test_multi_unit_advance_reports_in_chronological_order(self):
        clock = GameClock()
        crossings = clock.advance(2, TimeUnit.DAY)
        turns = [c for c in crossings if c.unit is TimeUnit.TURN]
        days = [c for c in crossings if c.unit is TimeUnit.DAY]
        assert [c.index for c in turns] == list(range(1, 2 * TURNS_PER_DAY + 1))
        assert [c.index for c in days] == [1, 2]
        assert [c.round for c in crossings] == sorted(c.round for c in crossings)

    def test_coinciding_boundaries_report_turn_before_day(self):
        clock = GameClock(rounds=ROUNDS_PER_DAY - 1)
        crossings = clock.advance(1)
        assert crossings == [
            BoundaryCrossing(unit=TimeUnit.TURN, index=TURNS_PER_DAY, round=ROUNDS_PER_DAY),
            BoundaryCrossing(unit=TimeUnit.DAY, index=1, round=ROUNDS_PER_DAY),
        ]

    def test_mid_stream_advance_spanning_boundaries(self):
        clock = GameClock(rounds=90)  # mid-turn 2
        crossings = clock.advance(3, TimeUnit.TURN)  # to round 270, mid-turn 5
        assert [(c.unit, c.index) for c in crossings] == [
            (TimeUnit.TURN, 2),
            (TimeUnit.TURN, 3),
            (TimeUnit.TURN, 4),
        ]

    def test_negative_advance_raises(self):
        clock = GameClock(rounds=100)
        with pytest.raises(ValueError):
            clock.advance(-1)
        assert clock.rounds == 100

    def test_zero_advance_is_a_legal_noop(self):
        clock = GameClock(rounds=60)  # sitting exactly on a boundary
        assert clock.advance(0) == []
        assert clock.rounds == 60


class TestSerialization:
    def test_json_roundtrip(self):
        clock = GameClock(rounds=12345)
        revived = GameClock.model_validate_json(clock.model_dump_json())
        assert revived == clock
        assert revived.rounds == 12345

    def test_serializes_to_a_single_field(self):
        assert GameClock(rounds=42).model_dump() == {"rounds": 42}

    def test_negative_rounds_rejected(self):
        with pytest.raises(ValueError):
            GameClock(rounds=-1)
