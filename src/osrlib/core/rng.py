"""Where every random number in osrlib comes from: named, seeded, repeatable streams.

[`RngStreams`][osrlib.core.rng.RngStreams] is the entry point. Make one from a master
seed, the single integer that all of a game's randomness is derived from. Ask it for a
stream by name with [`get`][osrlib.core.rng.RngStreams.get], and pass that stream to
whatever you call: [`roll`][osrlib.core.dice.roll], the combat and treasure functions,
character creation. A [`GameSession`][osrlib.crawl.session.GameSession] builds a
container from the seed you give it and passes the right stream to each rule, so during
ordinary play you never touch this module.

The members, in the order you use them:

- [`RngStreams`][osrlib.core.rng.RngStreams]: takes a master seed and keeps a game's
  streams.
- [`RngStreams.get`][osrlib.core.rng.RngStreams.get]: takes a stream name and returns
  the [`RngStream`][osrlib.core.rng.RngStream] for it.
- [`StreamName`][osrlib.core.rng.StreamName]: the names a session draws from.
- [`RngStream.randbelow`][osrlib.core.rng.RngStream.randbelow]: takes a bound and
  returns a random number below it. [`roll`][osrlib.core.dice.roll] calls it for you.
- [`RngStreams.export_states`][osrlib.core.rng.RngStreams.export_states] and
  [`RngStreams.restore_states`][osrlib.core.rng.RngStreams.restore_states]: save every
  stream's position as an [`RngStreamState`][osrlib.core.rng.RngStreamState], and put
  the positions back.
- [`derive_init_pair`][osrlib.core.rng.derive_init_pair]: takes a seed and a name and
  returns the numbers that start the stream, for checking another implementation.

Each stream name has a separate sequence. Rolling a hundred treasure hoards doesn't
change the next attack roll. A new draw added to one subsystem changes no other
subsystem's results, so a saved game replays and a bug reproduces.
[`StreamName`][osrlib.core.rng.StreamName] lists every stream a running session draws
from, and each `*_STREAM` constant in the library is one of its members. Each member's
docstring and
[the RNG streams reference](https://mmacy.github.io/osrlib-python/reference/rng-streams/)
describe what that stream covers. Code you run without a session can use any name. Ask
for the same name again to get the same stream back. It continues from where it stopped, and
doesn't start the sequence over.

The same master seed and the same stream name give the same draws, in this release and
in every later one. The generator is PCG64, in the `pcg_setseq_128_xsl_rr_64` form with
128 bits of state and a 64-bit output. numpy calls this generator `PCG64`, not
`PCG64DXSM`. Each [`next_uint64`][osrlib.core.rng.RngStream.next_uint64] advances the
state first and then takes the output from the new state, as the C reference
implementation does. A stream's starting numbers come from
`SHA-256(master_seed_bytes + b":" + stream_key_utf8)`, with the master seed written as 16
bytes, most significant first. An implementation that differs in any of these steps
draws different numbers, so it can't replay an osrlib game.

osrlib never uses Python's `random` module and keeps no generator of its own. If you
write a rule of your own to run beside osrlib's, take a stream as an argument the same
way.

Typical usage:

```python
from osrlib.core.dice import roll
from osrlib.core.rng import RngStreams, StreamName

streams = RngStreams(master_seed=42)

# Each name is its own sequence, so one subsystem's draws never move another's.
attack = roll("1d20", streams.get(StreamName.COMBAT))
gold = roll("2d6×100", streams.get(StreamName.TREASURE))
assert attack.total == 14
assert gold.total == 600

# Same seed, same name, same draws.
assert roll("1d20", RngStreams(master_seed=42).get(StreamName.COMBAT)).total == 14
```
"""

import hashlib
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "RngStream",
    "RngStreamState",
    "RngStreams",
    "StreamName",
    "derive_init_pair",
]

_MASK64 = (1 << 64) - 1
_MASK128 = (1 << 128) - 1

# PCG_DEFAULT_MULTIPLIER_128 from pcg-c: the LCG multiplier for all 128-bit PCG variants.
_PCG_MULTIPLIER = 0x2360ED051FC65DA44385DF649FCCF645

_SEED_BYTES = 16
_SEED_BOUND = 1 << 128


class StreamName(StrEnum):
    """Every stream name a running session draws from.

    Each member is a string, so `StreamName.COMBAT` and `"combat"` are the same stream to
    [`RngStreams.get`][osrlib.core.rng.RngStreams.get] and the same key in a save file. Draw with a
    member rather than a string you type out. A mistyped name raises no error:
    [`get`][osrlib.core.rng.RngStreams.get] makes a new stream for it, and you get plausible
    numbers from that stream instead of the one you meant.

    Each `*_STREAM` constant in the library, like
    [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM], is one of these members, and each member's
    docstring gives its constant. The member docstrings and
    [the RNG streams reference](https://mmacy.github.io/osrlib-python/reference/rng-streams/) say what
    each stream covers.

    A rule you write yourself isn't limited to these names.
    [`RngStreams.get`][osrlib.core.rng.RngStreams.get] makes a stream for any string you pass it.

    Examples:
        ```python
        from osrlib.core.rng import RngStreams, StreamName

        streams = RngStreams(master_seed=42)

        # A member is its own string, so the member and the written-out key are one stream.
        assert StreamName.COMBAT == "combat"
        assert streams.get(StreamName.COMBAT) is streams.get("combat")
        ```
    """

    CHARACTER_CREATION = "character_creation"
    """Creation draws: ability scores, the first-level hit die, and starting gold. Its constant is
    [`CHARACTER_CREATION_STREAM`][osrlib.core.character.CHARACTER_CREATION_STREAM]."""

    ADVANCEMENT = "advancement"
    """Hit dice rolled when a character gains or loses a level. Its constant is
    [`ADVANCEMENT_STREAM`][osrlib.core.character.ADVANCEMENT_STREAM]."""

    COMBAT = "combat"
    """Battle resolution: attacks, damage, saves, morale, initiative, reactions. Its constant is
    [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM]."""

    EFFECTS = "effects"
    """Draws inside the effects engine: onsets, rolled durations, revival countdowns. Its constant is
    [`EFFECTS_STREAM`][osrlib.core.effects.EFFECTS_STREAM]."""

    MONSTER_SPAWN = "monster_spawn"
    """Hit points rolled as a monster instance is spawned from its template. Its constant is
    [`MONSTER_SPAWN_STREAM`][osrlib.core.monsters.MONSTER_SPAWN_STREAM]."""

    NPC_PARTY = "npc_party"
    """NPC adventuring parties: composition, class and level, scores, hit points, spells. Its constant is
    [`NPC_PARTY_STREAM`][osrlib.core.npc.NPC_PARTY_STREAM]."""

    MAGIC = "magic"
    """Spell resolution: targeting, damage, forced saves, dispel survival, turning undead. Its constant is
    [`MAGIC_STREAM`][osrlib.core.spells.MAGIC_STREAM]."""

    TREASURE = "treasure"
    """Treasure generation, from the presence roll to each coin, gem, and magic item. Its constant is
    [`TREASURE_STREAM`][osrlib.core.treasure.TREASURE_STREAM]."""

    WANDERING = "wandering"
    """Wandering monsters: the check die, the table roll, group counts, variant picks. Its constant is
    [`WANDERING_STREAM`][osrlib.crawl.session.WANDERING_STREAM]."""

    ENCOUNTER = "encounter"
    """Encounter setup: surprise, distance, reaction, distraction, commanded group counts. Its constant is
    [`ENCOUNTER_STREAM`][osrlib.crawl.session.ENCOUNTER_STREAM]."""

    EXPLORATION = "exploration"
    """Exploration: forcing doors, listening, searching, traps, tinder, thief skills. Its constant is
    [`EXPLORATION_STREAM`][osrlib.crawl.session.EXPLORATION_STREAM]."""

    MONSTER_ACTION = "monster_action"
    """A monster action policy's draws: which action a group takes and at whom. Its constant is
    [`MONSTER_ACTION_STREAM`][osrlib.crawl.session.MONSTER_ACTION_STREAM]."""

    ADJUDICATION = "adjudication"
    """The referee's freeform dice rolls, kept apart from the streams the rules draw from. Its constant is
    [`ADJUDICATION_STREAM`][osrlib.crawl.session.ADJUDICATION_STREAM]."""


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
        key: The stream's name, like `"combat"` or `"treasure"`.

    Returns:
        The `(initstate, initseq)` pair. Pass the two numbers to
        [`RngStream`][osrlib.core.rng.RngStream] to start the stream.

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
    """A stream's position in its sequence, as a frozen model you can write to disk.

    [`RngStream.export_state`][osrlib.core.rng.RngStream.export_state] returns one and
    [`RngStream.restore`][osrlib.core.rng.RngStream.restore] takes it back, so a game
    saved halfway through a dungeon resumes on the next draw instead of starting the
    sequence over. [`save_game`][osrlib.persistence.save_game] and
    [`load_game`][osrlib.persistence.load_game] do this for every stream a session has
    used, so you handle these yourself only when you save a game without a session.

    The two numbers are the generator's internal state. Get them from `export_state` or
    from a saved copy of its result, not by computing them. Read them when you compare
    osrlib with another implementation.

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
    """The generator's 128-bit state, from 0 up to but not including 2**128. It changes with every draw."""

    inc: int = Field(ge=0, lt=_SEED_BOUND)
    """The generator's 128-bit increment, which selects the stream's sequence. It never changes.

    PCG always makes it odd, so an even value raises a pydantic `ValidationError`.
    """

    @field_validator("inc")
    @classmethod
    def _inc_must_be_odd(cls, value: int) -> int:
        if value % 2 == 0:
            raise ValueError("inc must be odd (PCG increments are always odd by construction)")
        return value


class RngStream:
    """One named sequence of random numbers, which you pass to whatever needs to roll.

    Get one from [`RngStreams.get`][osrlib.core.rng.RngStreams.get]. For a single stream
    with no container, as in a test, call
    [`from_seed_material`][osrlib.core.rng.RngStream.from_seed_material]. Every function in
    the kernel that rolls anything takes one of these, and streams are the only source of
    randomness in the library.

    Each draw advances the stream, so two calls give two different results, and the order
    of your calls is part of what the seed determines. Pass the stream itself, never a
    copy. Draw with [`randbelow`][osrlib.core.rng.RngStream.randbelow] for a bounded
    number, or call [`roll`][osrlib.core.dice.roll] with a dice expression.

    The constructor runs PCG64's standard initialization from an `(initstate, initseq)`
    pair. [`derive_init_pair`][osrlib.core.rng.derive_init_pair] makes that pair from a
    master seed and a name.

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

        The initialization is PCG's reference procedure: set the state to 0, set the increment to
        `(initseq << 1) | 1`, step, add `initstate`, step, everything modulo 2**128. The top
        bit of `initseq` has no effect, as in the reference implementation, so two values
        that differ only in that bit start the same stream.

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

        Use this when you want a single stream and no container. When you want several,
        use [`RngStreams`][osrlib.core.rng.RngStreams] instead: it keeps each stream and
        saves them all together.

        Args:
            master_seed: The game's master seed, from 0 up to but not including 2**128.
            key: The stream's name.

        Returns:
            A stream at the start of its sequence. The same seed and name always give a
            stream that draws the same numbers.

        Raises:
            ValueError: If `master_seed` is outside the allowed range.

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

        Use it when you load a game you saved yourself.
        [`load_game`][osrlib.persistence.load_game] restores a session's streams for you.

        Args:
            snapshot: A position from
                [`export_state`][osrlib.core.rng.RngStream.export_state].

        Returns:
            A stream whose next draw is the one the saved stream would have made.

        Examples:
            ```python
            import json

            from osrlib.core.rng import RngStream, RngStreams, RngStreamState

            stream = RngStreams(master_seed=42).get("combat")
            stream.randbelow(20)
            saved = stream.export_state().model_dump_json()

            # Later, read the position back from disk and draw on from it.
            restored = RngStream.restore(RngStreamState.model_validate(json.loads(saved)))
            assert restored.randbelow(20) + 1 == 11
            ```
        """
        stream = cls.__new__(cls)
        stream._state = snapshot.state
        stream._inc = snapshot.inc
        return stream

    def export_state(self) -> RngStreamState:
        """Record the stream's position, so you can come back to it.

        Pass the result to [`restore`][osrlib.core.rng.RngStream.restore] to get a stream
        at this position. To save all of a game's streams at once, call
        [`RngStreams.export_states`][osrlib.core.rng.RngStreams.export_states] instead.
        Exporting draws nothing and leaves the stream where it was.

        Returns:
            The position. Write it to disk with `model_dump_json()`.

        Examples:
            ```python
            from osrlib.core.rng import RngStreams

            stream = RngStreams(master_seed=42).get("combat")
            stream.randbelow(20)

            # Exporting twice gives the same record, because exporting draws nothing.
            snapshot = stream.export_state()
            assert stream.export_state() == snapshot
            document = snapshot.model_dump()
            assert sorted(document) == ["inc", "state"]
            ```
        """
        return RngStreamState(state=self._state, inc=self._inc)

    def _step(self) -> None:
        self._state = (self._state * _PCG_MULTIPLIER + self._inc) & _MASK128

    def next_uint64(self) -> int:
        """Draw the next raw 64-bit number from the stream.

        This is the generator's raw output, with no bound applied. For a die or any
        bounded value, call [`randbelow`][osrlib.core.rng.RngStream.randbelow] instead.
        A remainder of this number biases the result. Computing one yourself also uses a
        different number of raw draws than osrlib does, so the stream falls out of step
        with a replay.

        The stream advances first and then produces the output from its new state,
        following the reference C implementation.

        Returns:
            A number from 0 up to but not including 2**64, each equally likely.

        Examples:
            ```python
            from osrlib.core.rng import RngStreams

            # The first raw draw of the "combat" stream under seed 42, as another
            # implementation of osrlib's forking should reproduce it.
            stream = RngStreams(master_seed=42).get("combat")
            assert stream.next_uint64() == 12816652903456971652
            ```
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
        of a raw draw and throws the candidate away if it's at or above `n`, which is
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
    [`get`][osrlib.core.rng.RngStreams.get] for each stream you need. The container makes
    a stream the first time you ask for its name and keeps it, so asking again gives you
    the same stream at the position you left it.

    Keep one container for the whole game and pass streams out of it. Two containers on
    the same seed draw the same numbers, so streams from a second container repeat the
    rolls the first one already made.

    A [`GameSession`][osrlib.crawl.session.GameSession] keeps one of these, so you build
    your own only when you run the kernel, osrlib's rules functions, without a session.
    Save the position of every stream with
    [`export_states`][osrlib.core.rng.RngStreams.export_states] and put them back with
    [`restore_states`][osrlib.core.rng.RngStreams.restore_states]. The names a session
    uses are the members of [`StreamName`][osrlib.core.rng.StreamName], also listed in
    [the RNG streams reference](https://mmacy.github.io/osrlib-python/reference/rng-streams/).

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
        container later. You can't change it. To play on another seed, make a new
        container.
        """
        return self._master_seed

    def get(self, key: str) -> RngStream:
        """Return the stream with this name, making it the first time you ask.

        Pass the stream to the function that rolls, like [`roll`][osrlib.core.dice.roll].

        Args:
            key: The stream's name, like `StreamName.COMBAT` or `"combat"`. Any string
                works. The names a session uses are the members of
                [`StreamName`][osrlib.core.rng.StreamName], also listed in
                [the RNG streams reference](https://mmacy.github.io/osrlib-python/reference/rng-streams/).

        Returns:
            The stream for that name, at whatever position it has reached. Asking twice
            gives the same stream, not a copy.

        Examples:
            ```python
            from osrlib.core.rng import RngStreams, StreamName

            streams = RngStreams(master_seed=42)

            # Two names, two independent sequences.
            assert streams.get(StreamName.COMBAT).randbelow(20) + 1 == 14
            assert streams.get(StreamName.TREASURE).randbelow(6) + 1 == 4
            ```
        """
        stream = self._streams.get(key)
        if stream is None:
            stream = RngStream.from_seed_material(self._master_seed, key)
            self._streams[key] = stream
        return stream

    def export_states(self) -> dict[str, RngStreamState]:
        """Record the position of every stream in the container, keyed by name.

        Write the result into your save file beside the master seed, and put it back with
        [`restore_states`][osrlib.core.rng.RngStreams.restore_states] when you load.
        [`save_game`][osrlib.persistence.save_game] does this for a session, so call it
        yourself only when you save a game you built without one.

        A name you've never passed to [`get`][osrlib.core.rng.RngStreams.get] is left out.
        That stream has drawn nothing, and `get` makes it from the master seed the first
        time you ask for it.

        Returns:
            One position per name you've passed to `get` or restored, in sorted name order,
            so two saves of the same game are byte for byte the same.

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

        Call it on a container made with the master seed the game was saved under. Each
        stream named in `states` is replaced with a new stream at the saved position. A
        stream object you got from [`get`][osrlib.core.rng.RngStreams.get] before this call
        keeps its old position, so call `get` again afterwards. A stream not named in
        `states` is left as it is.

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
