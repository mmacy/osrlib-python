"""Time units and the game clock.

B/X time comes in three units: the round (10 seconds), the turn (10 minutes = 60
rounds), and the day (144 turns). Internally the clock is a single integer count of
rounds — the finest unit — so arithmetic is exact and serialization is one field.

Advancing the clock reports which turn and day boundaries were crossed, in order, so
the effects engine can resolve expirations and ticks at each boundary per the
canonical tick order. An advance that lands exactly on a boundary reports that
boundary: a torch lit at turn 0 expires when the clock reaches turn 6, not turn 7.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ROUNDS_PER_DAY",
    "ROUNDS_PER_TURN",
    "SECONDS_PER_ROUND",
    "TURNS_PER_DAY",
    "BoundaryCrossing",
    "GameClock",
    "TimeUnit",
]

SECONDS_PER_ROUND = 10
"""Length of a combat round in seconds."""

ROUNDS_PER_TURN = 60
"""Rounds per exploration turn (10 minutes)."""

TURNS_PER_DAY = 144
"""Exploration turns per day."""

ROUNDS_PER_DAY = ROUNDS_PER_TURN * TURNS_PER_DAY
"""Rounds per day (8640)."""


class TimeUnit(StrEnum):
    """The B/X time units."""

    ROUND = "round"
    TURN = "turn"
    DAY = "day"


_ROUNDS_PER_UNIT: dict[TimeUnit, int] = {
    TimeUnit.ROUND: 1,
    TimeUnit.TURN: ROUNDS_PER_TURN,
    TimeUnit.DAY: ROUNDS_PER_DAY,
}


class BoundaryCrossing(BaseModel):
    """A turn or day boundary crossed by a clock advance.

    `index` is the ordinal of the boundary in its own unit: crossing into turn 6 has
    `unit=TimeUnit.TURN, index=6`, and lies at absolute round `round` (here 360).
    When a day boundary coincides with a turn boundary (every day boundary does), the
    turn crossing is reported first — finer units before coarser.
    """

    model_config = ConfigDict(frozen=True)

    unit: TimeUnit
    index: int = Field(ge=1)
    round: int = Field(ge=1)


class GameClock(BaseModel):
    """The game clock: elapsed time as a single count of rounds.

    Examples:

        ```python
        clock = GameClock()
        crossings = clock.advance(2, TimeUnit.TURN)
        assert clock.turns == 2
        assert [c.index for c in crossings if c.unit is TimeUnit.TURN] == [1, 2]
        ```
    """

    model_config = ConfigDict(validate_assignment=True)

    rounds: int = Field(default=0, ge=0)

    @property
    def turns(self) -> int:
        """Whole turns elapsed."""
        return self.rounds // ROUNDS_PER_TURN

    @property
    def days(self) -> int:
        """Whole days elapsed."""
        return self.rounds // ROUNDS_PER_DAY

    def advance(self, n: int, unit: TimeUnit = TimeUnit.ROUND) -> list[BoundaryCrossing]:
        """Advance the clock and report the turn and day boundaries crossed.

        Args:
            n: How many units to advance. Must be non-negative; zero is a legal no-op
                that crosses nothing.
            unit: The unit to advance in.

        Returns:
            Every turn and day boundary in the advanced span, in chronological order,
            with a coinciding turn boundary before its day boundary. A boundary landed
            on exactly is included; the starting position is not (it was reported by
            the advance that reached it).

        Raises:
            ValueError: If `n` is negative.
        """
        if n < 0:
            raise ValueError(f"cannot advance the clock backwards, got n={n}")
        start = self.rounds
        end = start + n * _ROUNDS_PER_UNIT[TimeUnit(unit)]
        self.rounds = end
        crossings: list[BoundaryCrossing] = []
        first_turn = start // ROUNDS_PER_TURN + 1
        last_turn = end // ROUNDS_PER_TURN
        for turn in range(first_turn, last_turn + 1):
            at_round = turn * ROUNDS_PER_TURN
            crossings.append(BoundaryCrossing(unit=TimeUnit.TURN, index=turn, round=at_round))
            if at_round % ROUNDS_PER_DAY == 0:
                crossings.append(BoundaryCrossing(unit=TimeUnit.DAY, index=at_round // ROUNDS_PER_DAY, round=at_round))
        return crossings
