"""Named deterministic RNG streams backed by pure-Python PCG64.

This module is the determinism contract made concrete: draw sequences are part of the
public compatibility guarantee, so every algorithmic choice here is frozen by golden
vectors in the test suite. Do not change any of it without bumping expectations
consciously — an "equivalent" reimplementation that shifts a single draw breaks replays
and golden files.

The generator is PCG64 — specifically the `pcg_setseq_128_xsl_rr_64` variant (128-bit
LCG state, XSL-RR output to 64 bits), the same generator behind numpy's `PCG64` (not
`PCG64DXSM`, which is a different algorithm). Each `next_uint64()` advances the LCG
first, then applies XSL-RR to the *new* state; this is the pcg-c 128-bit convention
numpy follows, and the opposite of the widely tutorialized pcg32 pattern.

Streams are forked from a master seed by stable string keys: seed material is
`SHA-256(master_seed_bytes + b":" + stream_key_utf8)` with the master seed encoded as
16 bytes big-endian, so stream identity depends only on the master seed and the key
string. Adding draws to one subsystem's stream never shifts results in another.

Randomness in the library must always come from an explicitly passed
[`RngStream`][osrlib.core.rng.RngStream] — never the stdlib `random` module, and never
a module-level default.
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
    """Derive a PCG64 `(initstate, initseq)` pair for a named stream.

    Seed material is `SHA-256(master_seed_bytes + b":" + stream_key_utf8)` with the
    master seed encoded as fixed-width 16-byte big-endian. The 32-byte digest splits
    into the init pair, both halves read big-endian: bytes 0-15 are `initstate`,
    bytes 16-31 are `initseq`.

    Args:
        master_seed: The session's master seed, in `[0, 2**128)`.
        key: The stream's name, e.g. `"combat"` or `"treasure"`.

    Returns:
        The `(initstate, initseq)` pair for the canonical PCG64 init.

    Raises:
        ValueError: If `master_seed` is out of range.
    """
    if not 0 <= master_seed < _SEED_BOUND:
        raise ValueError(f"master_seed must be in [0, 2**128), got {master_seed}")
    material = master_seed.to_bytes(_SEED_BYTES, "big") + b":" + key.encode("utf-8")
    digest = hashlib.sha256(material).digest()
    initstate = int.from_bytes(digest[:16], "big")
    initseq = int.from_bytes(digest[16:], "big")
    return initstate, initseq


class RngStreamState(BaseModel):
    """A serializable snapshot of an in-progress stream.

    Captures the raw PCG64 internals — the 128-bit LCG state and the stream increment —
    so saves can restore mid-sequence streams exactly via
    [`RngStream.restore`][osrlib.core.rng.RngStream.restore].
    """

    model_config = ConfigDict(frozen=True)

    state: int = Field(ge=0, lt=_SEED_BOUND)
    inc: int = Field(ge=0, lt=_SEED_BOUND)

    @field_validator("inc")
    @classmethod
    def _inc_must_be_odd(cls, value: int) -> int:
        if value % 2 == 0:
            raise ValueError("inc must be odd (PCG increments are always odd by construction)")
        return value


class RngStream:
    """A single PCG64 stream.

    Construct via [`RngStreams.get`][osrlib.core.rng.RngStreams.get] in normal play;
    direct construction from an `(initstate, initseq)` pair runs the canonical PCG64
    init and exists for tests and à la carte use.
    """

    __slots__ = ("_inc", "_state")

    def __init__(self, initstate: int, initseq: int) -> None:
        """Initialize the stream with the canonical PCG64 init procedure.

        The init is `state = 0; inc = (initseq << 1) | 1; step; state += initstate;
        step`, all mod 2**128. It discards the top bit of `initseq` — expected
        behavior, not a bug to fix.

        Args:
            initstate: The 128-bit init state, in `[0, 2**128)`.
            initseq: The 128-bit stream-selection constant, in `[0, 2**128)`.

        Raises:
            ValueError: If either argument is out of range.
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
        """Derive and initialize the named stream for a master seed.

        Args:
            master_seed: The session's master seed, in `[0, 2**128)`.
            key: The stream's name.

        Returns:
            A freshly initialized stream; the same arguments always produce a stream
            that yields the identical draw sequence.
        """
        return cls(*derive_init_pair(master_seed, key))

    @classmethod
    def restore(cls, snapshot: RngStreamState) -> RngStream:
        """Restore a stream from an exported snapshot.

        Args:
            snapshot: A state previously returned by
                [`export_state`][osrlib.core.rng.RngStream.export_state].

        Returns:
            A stream that continues the draw sequence exactly where the exporting
            stream left off.
        """
        stream = cls.__new__(cls)
        stream._state = snapshot.state
        stream._inc = snapshot.inc
        return stream

    def export_state(self) -> RngStreamState:
        """Export the stream's exact position for serialization.

        Returns:
            A frozen snapshot of the raw PCG64 state and increment.
        """
        return RngStreamState(state=self._state, inc=self._inc)

    def _step(self) -> None:
        self._state = (self._state * _PCG_MULTIPLIER + self._inc) & _MASK128

    def next_uint64(self) -> int:
        """Draw the next raw 64-bit output.

        Advances the LCG first, then applies XSL-RR to the new state (the pcg-c
        128-bit convention).

        Returns:
            A uniformly distributed integer in `[0, 2**64)`.
        """
        self._step()
        state = self._state
        xored = ((state >> 64) ^ state) & _MASK64
        rot = state >> 122
        return ((xored >> rot) | (xored << ((64 - rot) & 63))) & _MASK64

    def randbelow(self, n: int) -> int:
        """Draw a uniformly distributed integer in `[0, n)`.

        The algorithm is frozen as top-bits rejection sampling: with
        `k = (n - 1).bit_length()`, each candidate is `next_uint64() >> (64 - k)`,
        rejected and redrawn while `candidate >= n`. No masking of low bits.
        Rejection means the raw-draw count per bounded draw is variable: power-of-two
        bounds never reject; others (3, 6, 10, 12, 20, 100) can. `randbelow(1)` has
        `k = 0`, always yields 0, and still consumes one draw.

        Args:
            n: The exclusive upper bound. Must be positive.

        Returns:
            A uniformly distributed integer in `[0, n)`.

        Raises:
            ValueError: If `n <= 0`.
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
    """The named-stream container forked from a session's master seed.

    Streams are created lazily on first access and cached: the same key always returns
    the same stream object, and stream identity depends only on the master seed and the
    key string.

    Examples:
        ```python
        from osrlib.core.rng import RngStreams

        streams = RngStreams(master_seed=42)
        combat = streams.get("combat")
        d20 = combat.randbelow(20) + 1
        assert 1 <= d20 <= 20
        ```
    """

    __slots__ = ("_master_seed", "_streams")

    def __init__(self, master_seed: int) -> None:
        """Create the container for a master seed.

        Args:
            master_seed: The session's master seed, in `[0, 2**128)`.

        Raises:
            ValueError: If `master_seed` is out of range.
        """
        if not 0 <= master_seed < _SEED_BOUND:
            raise ValueError(f"master_seed must be in [0, 2**128), got {master_seed}")
        self._master_seed = master_seed
        self._streams: dict[str, RngStream] = {}

    @property
    def master_seed(self) -> int:
        """The master seed this container forks streams from."""
        return self._master_seed

    def get(self, key: str) -> RngStream:
        """Return the named stream, creating it on first use.

        Args:
            key: The stream's name, e.g. `"combat"` or `"treasure"`.

        Returns:
            The stream for `key`; repeated calls return the same object.
        """
        stream = self._streams.get(key)
        if stream is None:
            stream = RngStream.from_seed_material(self._master_seed, key)
            self._streams[key] = stream
        return stream

    def export_states(self) -> dict[str, RngStreamState]:
        """Export every touched stream's exact position, keyed by stream name.

        Untouched streams need no snapshot — they re-derive from the master seed
        on first use. Keys are sorted so serialization is deterministic.

        Returns:
            The stream snapshots for saves.
        """
        return {key: self._streams[key].export_state() for key in sorted(self._streams)}

    def restore_states(self, states: dict[str, RngStreamState]) -> None:
        """Restore previously exported stream positions.

        Args:
            states: Snapshots from
                [`export_states`][osrlib.core.rng.RngStreams.export_states].
        """
        for key, snapshot in states.items():
            self._streams[key] = RngStream.restore(snapshot)
