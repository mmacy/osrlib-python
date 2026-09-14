"""Where every random number in osrlib comes from: named, seeded, repeatable streams.

[`RngStreams`][osrlib.core.rng.RngStreams] is the entry point. Make one from a master
seed, the single integer a whole game's randomness is derived from, ask it for a stream
by name with [`get`][osrlib.core.rng.RngStreams.get], and pass that stream to whatever
you call: [`roll`][osrlib.core.dice.roll], the combat and treasure functions, character
creation. A [`GameSession`][osrlib.crawl.session.GameSession] builds its own container
from the seed you give it and hands out the right stream for each rule, so during
ordinary play you never touch this module.

A stream is named by a plain string, and each name draws its own independent sequence.
That's the point: rolling a hundred treasure hoards doesn't change what the next attack
rolls. Add a new draw to one subsystem and no other subsystem's results move, which is
what lets a saved game replay and a bug reproduce. [The RNG streams
reference][rng-streams] lists the names a running session uses and what each one covers.
Standalone code isn't bound to those names. A name is a label, and all that matters is
that you ask for the same one each time.

Two draws made with the same master seed and the same stream name come out the same, in
this release and in every later one. That promise fixes every choice here. The generator
is PCG64, in the `pcg_setseq_128_xsl_rr_64` form with 128 bits of state and a 64-bit
output, the same generator numpy calls `PCG64` rather than its `PCG64DXSM`. Each
[`next_uint64`][osrlib.core.rng.RngStream.next_uint64] advances the state first and then
takes the output from the new state, following the C implementation numpy follows.
Streams are forked from the master seed as
`SHA-256(master_seed_bytes + b":" + stream_key_utf8)`, with the master seed written as 16
bytes, most significant first. Reimplementing any of that, even in a way that looks
equivalent, shifts draws and breaks saved games.

Nothing in osrlib reaches for Python's `random` module or keeps a generator of its own.
If you're writing a rule of your own to run beside osrlib's, take a stream as an
argument the same way.

Typical usage:

```python
from osrlib.core.dice import roll
from osrlib.core.rng import RngStreams

streams = RngStreams(master_seed=42)

# Each name is its own sequence, so one subsystem's draws never move another's.
attack = roll("1d20", streams.get("combat"))
gold = roll("2d6×100", streams.get("treasure"))
assert attack.total == 14
assert gold.total == 600

# Same seed, same name, same draws.
assert roll("1d20", RngStreams(master_seed=42).get("combat")).total == 14
```
"""

import hashlib

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "RngStream",
    "RngStreamState",
    "RngStreams",
    "derive_init_pair",
]

_MASK64 = (1 << 64) - 1
_MASK128 = (1 << 128) - 1

# PCG_DEFAULT_MULTIPLIER_128 from pcg-c: the LCG multiplier for all 128-bit PCG variants.
_PCG_MULTIPLIER = 0x2360ED051FC65DA44385DF649FCCF645

_SEED_BYTES = 16
_SEED_BOUND = 1 << 128


def derive_init_pair(master_seed: int, key: str) -> tuple[int, int]:
    """Work out the two numbers PCG64 needs to start a named stream.

    You rarely call this. [`RngStreams.get`][osrlib.core.rng.RngStreams.get] calls it for
    you, and [`RngStream.from_seed_material`][osrlib.core.rng.RngStream.from_seed_material]
    wraps it in one step. Reach for it when you're checking osrlib's forking against
    another implementation, or building the same stream in another language.

    The seed material is `SHA-256(master_seed_bytes + b":" + stream_key_utf8)`, with the
    master seed written as exactly 16 bytes, most significant first. The digest's first
    16 bytes become the init state and its last 16 the sequence selector, each read most
    significant byte first.

    Args:
        master_seed: The game's master seed, from 0 up to but not including 2**128.
        key: The stream's name, such as `"combat"` or `"treasure"`.

    Returns:
        The `(initstate, initseq)` pair, ready for
        [`RngStream`][osrlib.core.rng.RngStream].

    Raises:
        ValueError: If `master_seed` is outside the allowed range.

    Examples:
        ```python
        from osrlib.core.rng import RngStream, derive_init_pair

        initstate, initseq = derive_init_pair(42, "combat")

        # The pair is what RngStreams.get builds its stream from.
        assert RngStream(initstate, initseq).next_uint64() == 12816652903456971652
        ```
    """
    if not 0 <= master_seed < _SEED_BOUND:
        raise ValueError(f"master_seed must be in [0, 2**128), got {master_seed}")
    material = master_seed.to_bytes(_SEED_BYTES, "big") + b":" + key.encode("utf-8")
    digest = hashlib.sha256(material).digest()
    initstate = int.from_bytes(digest[:16], "big")
    initseq = int.from_bytes(digest[16:], "big")
    return initstate, initseq


class RngStreamState(BaseModel):
    """Where a stream had got to, in a form you can write to disk.

    [`RngStream.export_state`][osrlib.core.rng.RngStream.export_state] returns one and
    [`RngStream.restore`][osrlib.core.rng.RngStream.restore] takes it back, so a game
    saved halfway through a dungeon resumes on the very next draw rather than starting
    the sequence over. [`save_game`][osrlib.persistence.save_game] and
    [`load_game`][osrlib.persistence.load_game] do this for every stream a session has
    touched, so you only build one of these yourself when you're saving a game without a
    session.

    The two numbers are the generator's internals. Read them if you're comparing
    implementations. Don't compute them.

    Examples:
        ```python
        from osrlib.core.rng import RngStream, RngStreams

        stream = RngStreams(master_seed=42).get("combat")
        stream.randbelow(20)

        # Save the position, draw on, and a restored copy continues from the save.
        snapshot = stream.export_state()
        expected = stream.randbelow(20)
        assert RngStream.restore(snapshot).randbelow(20) == expected
        ```
    """

    model_config = ConfigDict(frozen=True)

    state: int = Field(ge=0, lt=_SEED_BOUND)
    """The generator's 128-bit state: how far along the sequence the stream has got."""

    inc: int = Field(ge=0, lt=_SEED_BOUND)
    """The generator's increment, which is what makes one stream's sequence differ from another's.

    Always odd, by the way PCG builds it, and a pydantic `ValidationError` says so if you
    pass an even number.
    """

    @field_validator("inc")
    @classmethod
    def _inc_must_be_odd(cls, value: int) -> int:
        if value % 2 == 0:
            raise ValueError("inc must be odd (PCG increments are always odd by construction)")
        return value


class RngStream:
    """One named sequence of random numbers, which you pass to whatever needs to roll.

    Get one from [`RngStreams.get`][osrlib.core.rng.RngStreams.get] rather than building
    it, unless you're writing a test or using a single stream on its own. Every function
    in the kernel that rolls anything takes one of these, and it's the only source of
    randomness in the library.

    Drawing advances the stream, so two calls give two different results and the order of
    your calls is part of what the seed determines. Pass the stream itself, never a copy.
    Draw with [`randbelow`][osrlib.core.rng.RngStream.randbelow] for a bounded number, or
    let [`roll`][osrlib.core.dice.roll] do it from a dice expression.

    Constructing one directly runs PCG64's own initialization from an
    `(initstate, initseq)` pair, which is what
    [`derive_init_pair`][osrlib.core.rng.derive_init_pair] produces from a master seed and
    a name.

    Examples:
        ```python
        from osrlib.core.rng import RngStreams

        stream = RngStreams(master_seed=42).get("combat")
        assert stream.randbelow(20) + 1 == 14
        ```
    """

    __slots__ = ("_inc", "_state")

    def __init__(self, initstate: int, initseq: int) -> None:
        """Start the stream from a PCG64 init pair.

        The initialization is PCG's own: set the state to 0, set the increment to
        `(initseq << 1) | 1`, step, add `initstate`, step, everything modulo 2**128. It
        drops the top bit of `initseq`, which is what the reference implementation does
        and not a defect to work around.

        Args:
            initstate: The 128-bit init state, from 0 up to but not including 2**128.
            initseq: The 128-bit sequence selector, in the same range. Two streams with
                the same init state and different selectors draw different sequences.

        Raises:
            ValueError: If either argument is outside the allowed range.
        """
        if not 0 <= initstate < _SEED_BOUND:
            raise ValueError(f"initstate must be in [0, 2**128), got {initstate}")
        if not 0 <= initseq < _SEED_BOUND:
            raise ValueError(f"initseq must be in [0, 2**128), got {initseq}")
        self._state = 0
        self._inc = ((initseq << 1) | 1) & _MASK128
        self._step()
        self._state = (self._state + initstate) & _MASK128
        self._step()

    @classmethod
    def from_seed_material(cls, master_seed: int, key: str) -> RngStream:
        """Build the named stream for a master seed, in one step.

        Use this when you want a single stream and no container.
        [`RngStreams`][osrlib.core.rng.RngStreams] is the better choice when you want
        several, because it remembers each one and can save them all together.

        Args:
            master_seed: The game's master seed, from 0 up to but not including 2**128.
            key: The stream's name.

        Returns:
            A stream at the start of its sequence. The same seed and name always give a
            stream that draws the same numbers.

        Examples:
            ```python
            from osrlib.core.rng import RngStream

            stream = RngStream.from_seed_material(42, "combat")
            assert stream.randbelow(20) + 1 == 14
            ```
        """
        return cls(*derive_init_pair(master_seed, key))

    @classmethod
    def restore(cls, snapshot: RngStreamState) -> RngStream:
        """Rebuild a stream at the position a snapshot recorded.

        Use it when you're loading a game you saved yourself.
        [`load_game`][osrlib.persistence.load_game] restores a session's streams for you.

        Args:
            snapshot: A position from
                [`export_state`][osrlib.core.rng.RngStream.export_state].

        Returns:
            A stream whose next draw is the one the saved stream would have made.
        """
        stream = cls.__new__(cls)
        stream._state = snapshot.state
        stream._inc = snapshot.inc
        return stream

    def export_state(self) -> RngStreamState:
        """Record where the stream has reached, so you can come back to it.

        Pair it with [`restore`][osrlib.core.rng.RngStream.restore]. To save a whole
        game's worth of streams at once, call
        [`RngStreams.export_states`][osrlib.core.rng.RngStreams.export_states] instead.
        Exporting draws nothing and leaves the stream where it was.

        Returns:
            A frozen record of the position, ready to serialize.
        """
        return RngStreamState(state=self._state, inc=self._inc)

    def _step(self) -> None:
        self._state = (self._state * _PCG_MULTIPLIER + self._inc) & _MASK128

    def next_uint64(self) -> int:
        """Draw the next raw 64-bit number from the stream.

        This is the generator's own output, with no bound applied. For a die or any
        bounded value, call [`randbelow`][osrlib.core.rng.RngStream.randbelow] instead:
        taking a remainder of this number yourself biases the result, and it draws a
        different count of raw numbers than osrlib does, which puts the stream out of step
        with a replay.

        The stream advances first and then produces the output from its new state,
        following the reference C implementation.

        Returns:
            A number from 0 up to but not including 2**64, each equally likely.
        """
        self._step()
        state = self._state
        xored = ((state >> 64) ^ state) & _MASK64
        rot = state >> 122
        return ((xored >> rot) | (xored << ((64 - rot) & 63))) & _MASK64

    def randbelow(self, n: int) -> int:
        """Draw a number from 0 up to but not including `n`, each equally likely.

        This is the bounded draw everything in osrlib is built on. For a die, add 1:
        `stream.randbelow(6) + 1` is a d6. For a dice expression, call
        [`roll`][osrlib.core.dice.roll] instead and let it do the arithmetic.

        How many raw numbers a single call consumes varies. The method takes the top bits
        of a raw draw and throws the candidate away if it lands at or above `n`, which is
        what keeps every result equally likely. A bound that's a power of two never throws
        anything away, and bounds of 3, 6, 10, 12, 20, and 100 sometimes do, so a stream's
        position after a roll depends on which values came up rather than on how many
        times you called. `randbelow(1)` returns 0 and still uses a draw.

        Args:
            n: The bound, which the result stays below. Must be positive.

        Returns:
            A number from 0 up to but not including `n`.

        Raises:
            ValueError: If `n` is zero or negative.

        Examples:
            ```python
            from osrlib.core.rng import RngStreams

            stream = RngStreams(master_seed=42).get("combat")

            # A d20 is a draw below 20, plus one.
            assert stream.randbelow(20) + 1 == 14
            ```
        """
        if n <= 0:
            raise ValueError(f"n must be positive, got {n}")
        k = (n - 1).bit_length()
        shift = 64 - k
        while True:
            candidate = self.next_uint64() >> shift
            if candidate < n:
                return candidate


class RngStreams:
    """All of a game's random number streams, forked from one master seed.

    Make one with the seed you want the game to run on, then call
    [`get`][osrlib.core.rng.RngStreams.get] for each stream you need. A stream is built
    the first time you ask for it and kept, so asking again gives you the same stream at
    the position you left it. Which streams exist is up to you, and a name you've never
    used gets a stream of its own the first time you ask.

    Keep one container for the whole game and pass streams out of it. Two containers on
    the same seed draw the same numbers as each other, which means handing out streams
    from a second container quietly repeats rolls the first one already made.

    A [`GameSession`][osrlib.crawl.session.GameSession] keeps one of these, so you only
    build your own when you're running the kernel without a session. Save a game's worth
    of positions with [`export_states`][osrlib.core.rng.RngStreams.export_states] and put
    them back with [`restore_states`][osrlib.core.rng.RngStreams.restore_states]. [The RNG
    streams reference][rng-streams] lists the names a session uses.

    Examples:
        ```python
        from osrlib.core.rng import RngStreams

        streams = RngStreams(master_seed=42)
        combat = streams.get("combat")

        # The same name gives back the same stream, mid-sequence.
        assert streams.get("combat") is combat
        assert combat.randbelow(20) + 1 == 14
        ```
    """

    __slots__ = ("_master_seed", "_streams")

    def __init__(self, master_seed: int) -> None:
        """Create the container for a master seed.

        Args:
            master_seed: The game's master seed, from 0 up to but not including 2**128.
                Any integer in range works. Record the one you used if you want to
                replay the game.

        Raises:
            ValueError: If `master_seed` is outside the allowed range.
        """
        if not 0 <= master_seed < _SEED_BOUND:
            raise ValueError(f"master_seed must be in [0, 2**128), got {master_seed}")
        self._master_seed = master_seed
        self._streams: dict[str, RngStream] = {}

    @property
    def master_seed(self) -> int:
        """Return the master seed every stream in this container is forked from.

        Read it to record what a game was seeded with, so you can rebuild the same
        container later. It cannot be changed: a container's seed is fixed when you make
        it.
        """
        return self._master_seed

    def get(self, key: str) -> RngStream:
        """Return the stream with this name, making it the first time you ask.

        Pass what comes back to whatever draws.

        Args:
            key: The stream's name, such as `"combat"` or `"treasure"`. Any string works;
                [the RNG streams reference][rng-streams] lists the ones a session uses.

        Returns:
            The stream for that name, at whatever position it has reached. Asking twice
            gives the same stream, not a copy.

        Examples:
            ```python
            from osrlib.core.rng import RngStreams

            streams = RngStreams(master_seed=42)

            # Two names, two independent sequences.
            assert streams.get("combat").randbelow(20) + 1 == 14
            assert streams.get("treasure").randbelow(6) + 1 == 4
            ```
        """
        stream = self._streams.get(key)
        if stream is None:
            stream = RngStream.from_seed_material(self._master_seed, key)
            self._streams[key] = stream
        return stream

    def export_states(self) -> dict[str, RngStreamState]:
        """Record where every stream you've used has reached, keyed by name.

        Write the result into your save file alongside the master seed, and put it back
        with [`restore_states`][osrlib.core.rng.RngStreams.restore_states] when you load.
        [`save_game`][osrlib.persistence.save_game] does this for a session, so call it
        yourself only when you're saving a game you built without one.

        A name you've never asked for is left out. A stream like that has drawn nothing,
        and it rebuilds itself from the master seed the first time you use it.

        Returns:
            One position per stream that has been used, in sorted name order so two saves
            of the same game are byte for byte the same.

        Examples:
            ```python
            from osrlib.core.rng import RngStreams

            streams = RngStreams(master_seed=42)
            streams.get("combat").randbelow(20)

            # Only the stream that was used is recorded.
            assert sorted(streams.export_states()) == ["combat"]
            ```
        """
        return {key: self._streams[key].export_state() for key in sorted(self._streams)}

    def restore_states(self, states: dict[str, RngStreamState]) -> None:
        """Put saved stream positions back, so a loaded game draws on from where it stopped.

        Call it on a container built with the same master seed the game was saved under.
        A stream named in `states` is replaced. One that isn't is left alone, and a stream
        that has never been used rebuilds itself from the seed.

        Args:
            states: Positions from
                [`export_states`][osrlib.core.rng.RngStreams.export_states].

        Examples:
            ```python
            from osrlib.core.rng import RngStreams

            streams = RngStreams(master_seed=42)
            streams.get("combat").randbelow(20)
            saved = streams.export_states()
            expected = streams.get("combat").randbelow(20)

            # A fresh container on the same seed picks up the next draw, not the first.
            loaded = RngStreams(master_seed=42)
            loaded.restore_states(saved)
            assert loaded.get("combat").randbelow(20) == expected
            ```
        """
        for key, snapshot in states.items():
            self._streams[key] = RngStream.restore(snapshot)
