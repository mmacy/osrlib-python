"""How much game time has passed, counted in rounds.

[`GameClock`][osrlib.core.clock.GameClock] is the entry point: you create one, call
[`advance`][osrlib.core.clock.GameClock.advance] whenever the party spends time, and
read `rounds`, `turns`, and `days` off it. Each advance returns the turn and day
boundaries it crossed, as [`BoundaryCrossing`][osrlib.core.clock.BoundaryCrossing]
values, so you can run whatever your game does at the end of a turn or a day: burn a
torch down, check for wandering monsters, eat a day's rations.

In an ordinary game you never build a clock yourself. A
[`GameSession`][osrlib.crawl.session.GameSession] keeps one and advances it as commands
consume time, and
[`EffectsLedger.advance`][osrlib.core.effects.EffectsLedger.advance] takes the session's
clock when it ages poison, light, and the rest. Build your own when you use the kernel
without a session and still want time to pass.

B/X measures time in three units: the round of 10 seconds, the turn of 10 minutes, and
the day. The clock keeps one integer count of rounds, the finest unit, so the arithmetic
is exact and the whole clock saves as one number. An advance that lands exactly on a
boundary counts as crossing it: a torch lit at turn 0 burns out when the clock reaches
turn 6, not turn 7.

Typical usage:

```python
from osrlib.core.clock import GameClock, TimeUnit

clock = GameClock()

# Six turns of searching: one crossing per turn, none for a day.
crossings = clock.advance(6, TimeUnit.TURN)
assert [(crossing.unit, crossing.index) for crossing in crossings] == [
    (TimeUnit.TURN, 1),
    (TimeUnit.TURN, 2),
    (TimeUnit.TURN, 3),
    (TimeUnit.TURN, 4),
    (TimeUnit.TURN, 5),
    (TimeUnit.TURN, 6),
]
assert (clock.rounds, clock.turns, clock.days) == (360, 6, 0)

# Ten rounds of combat cross nothing: the clock is mid-turn.
assert clock.advance(10) == []
assert clock.rounds == 370
```
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
"""How many seconds of story time one combat round takes.

Nothing in the rules reads this. It's here so you can put a wall-clock duration on the
screen, or pace an animation, without writing 10 into your own code. The B/X rules give
no unit finer than the round, so there's nothing below it to count.
"""

ROUNDS_PER_TURN = 60
"""How many rounds make up one exploration turn, which is ten minutes of story time.

The round is the combat unit and the turn is the exploration unit, and this is the
conversion between them. Use it to say how long something lasts in the other unit: a
torch that burns for six turns burns for `6 * ROUNDS_PER_TURN` rounds.

Don't reassign it to play at a different scale. Every duration the SRD prints is quoted
in turns or rounds at this ratio, so changing it rescales them all at once, with nothing
to show for it. To work in a unit of your own, advance the clock by rounds and convert
the result yourself.
"""

TURNS_PER_DAY = 144
"""How many exploration turns make up one day.

That's a full 24 hours of turns, not only the ones spent underground. The day boundary
is where the rules put daily events, such as eating and preparing spells.
"""

ROUNDS_PER_DAY = ROUNDS_PER_TURN * TURNS_PER_DAY
"""How many rounds make up one day, which is 8640.

It's the product of the two ratios above rather than a separate number, so it can never
disagree with them.
"""


class TimeUnit(StrEnum):
    """The unit an amount of game time is counted in.

    Pass one to [`advance`][osrlib.core.clock.GameClock.advance] to say what your number
    means, and read one off a [`BoundaryCrossing`][osrlib.core.clock.BoundaryCrossing] to
    see which kind of boundary you crossed. The rounds each unit is worth are
    [`ROUNDS_PER_TURN`][osrlib.core.clock.ROUNDS_PER_TURN] and
    [`ROUNDS_PER_DAY`][osrlib.core.clock.ROUNDS_PER_DAY].
    """

    ROUND = "round"
    """The combat unit, ten seconds of story time.

    One attack, one spell, or one move in a fight takes a round.
    """

    TURN = "turn"
    """The exploration unit, ten minutes of story time.

    One move through the dungeon, one search of a room, or one attempt to listen at a
    door takes a turn.
    """

    DAY = "day"
    """The supply unit, a full day of story time.

    A session consumes the party's rations and water on each day boundary, and a caster
    prepares spells once a day.
    """


_ROUNDS_PER_UNIT: dict[TimeUnit, int] = {
    TimeUnit.ROUND: 1,
    TimeUnit.TURN: ROUNDS_PER_TURN,
    TimeUnit.DAY: ROUNDS_PER_DAY,
}


class BoundaryCrossing(BaseModel):
    """One turn or day boundary that a clock advance passed.

    [`advance`][osrlib.core.clock.GameClock.advance] returns a list of these, oldest
    first. Walk the list and do whatever your game owes the end of a turn or the end of
    a day, in the order the boundaries arrived. There are no round crossings: every
    round is a round boundary, so the count of them is the advance itself.

    The model is frozen, so you can keep a crossing as a record of when something
    happened.

    Examples:
        ```python
        from osrlib.core.clock import GameClock, TimeUnit

        # A day boundary is also a turn boundary, and the turn is reported first.
        clock = GameClock(rounds=8580)
        crossings = clock.advance(1, TimeUnit.TURN)
        assert [(crossing.unit, crossing.index, crossing.round) for crossing in crossings] == [
            (TimeUnit.TURN, 144, 8640),
            (TimeUnit.DAY, 1, 8640),
        ]
        ```
    """

    model_config = ConfigDict(frozen=True)

    unit: TimeUnit
    """Which kind of boundary this is, `TimeUnit.TURN` or `TimeUnit.DAY`.

    A crossing is never `TimeUnit.ROUND`. An advance of `n` rounds crosses `n` round
    boundaries, so listing them would only repeat the number you passed in.
    """

    index: int = Field(ge=1)
    """Which boundary of that kind, counted from the start of the game.

    The first turn of play is 1 and the sixth is 6, so the ordinal matches the way the
    rules count durations ("burns for six turns").
    """

    round: int = Field(ge=1)
    """The clock reading at which the boundary sits, in rounds since the start of the game.

    Turn 6 sits at round 360. Use it to stamp whatever you record at the boundary, so the
    record and the clock agree.
    """


class GameClock(BaseModel):
    """How much time has passed in the game, as a count of rounds.

    Call `GameClock()` to start a game at time zero, or `GameClock(rounds=n)` to resume
    one. A [`GameSession`][osrlib.crawl.session.GameSession] makes its own and keeps it
    on `session.clock`, so reach for the constructor only when you're running the kernel
    without a session.

    Unlike most models in the kernel this one is mutable:
    [`advance`][osrlib.core.clock.GameClock.advance] moves the same clock forward rather
    than returning a new one, because everything that spends time shares one clock. Pass
    the clock itself to anything that consumes time, not a copy, or their views of the
    game's time drift apart. Assigning `rounds` directly works and is validated, but
    skips the boundary report, so prefer `advance`.

    The clock saves as one integer, and `GameClock.model_validate(document)` reads it
    back.

    Examples:
        ```python
        from osrlib.core.clock import GameClock, TimeUnit

        clock = GameClock()
        crossings = clock.advance(2, TimeUnit.TURN)
        assert clock.turns == 2
        assert [crossing.index for crossing in crossings if crossing.unit is TimeUnit.TURN] == [1, 2]

        # The whole clock round-trips as one field.
        assert GameClock.model_validate(clock.model_dump()).rounds == 120
        ```
    """

    model_config = ConfigDict(validate_assignment=True)

    rounds: int = Field(default=0, ge=0)
    """Rounds elapsed since the start of the game.

    The clock's only stored value. `turns` and `days` are read off it. Advancing is what
    normally changes it, and it can never go below zero.
    """

    @property
    def turns(self) -> int:
        """Return the number of whole exploration turns elapsed, rounding down.

        Read it for a "how long have we been down here" display. A turn in progress
        doesn't count until it finishes, so a clock at round 59 still reads 0 turns.
        """
        return self.rounds // ROUNDS_PER_TURN

    @property
    def days(self) -> int:
        """Return the number of whole days elapsed, rounding down.

        Read it to tell how many days of rations the party has eaten. A day in progress
        doesn't count until it finishes.
        """
        return self.rounds // ROUNDS_PER_DAY

    def advance(self, n: int, unit: TimeUnit = TimeUnit.ROUND) -> list[BoundaryCrossing]:
        """Advance the clock and report the turn and day boundaries crossed.

        Call this whenever the party spends time, then act on the crossings you get
        back. The clock moves in place, and the return value is the report rather than a new
        clock.

        If you're aging effects as well as counting time, call
        [`EffectsLedger.advance`][osrlib.core.effects.EffectsLedger.advance] instead and
        hand it this clock: it advances the clock for you and returns the events that
        expiring and ticking effects produced. Advancing the clock here does nothing to
        effects on its own.

        Args:
            n: How many units to advance. Must be non-negative. Zero is legal and crosses
                nothing.
            unit: The unit to advance in.

        Returns:
            Every turn and day boundary in the advanced span, in chronological order,
            with a coinciding turn boundary before its day boundary. A boundary the
            advance lands on exactly is included. The position you started from isn't,
            because the advance that reached it already reported it.

        Raises:
            ValueError: If `n` is negative. Time never runs backwards, so to rewind a
                game, restore it from a save.

        Examples:
            ```python
            from osrlib.core.clock import GameClock, TimeUnit

            clock = GameClock()

            # A round of combat crosses nothing.
            assert clock.advance(1) == []

            # Searching a room takes a turn, and the turn boundary comes back.
            crossings = clock.advance(1, TimeUnit.TURN)
            assert [(crossing.unit, crossing.index) for crossing in crossings] == [(TimeUnit.TURN, 1)]
            assert clock.rounds == 61
            ```
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
