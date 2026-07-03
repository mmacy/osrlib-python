# Phase 0 plan — scaffolding and contracts

Implementation plan for Phase 0 of [the osrlib spec](spec.md). Phase 0 delivers the project skeleton (uv, ruff, pytest, CI, licensing) and the first real code — `dice`, `rng`, and `clock` — while locking in the contracts every later phase must obey: determinism, event-emission rules, and `schema_version`/engine-version stamping.

## Scope

In scope:

- uv project layout, ruff and pytest configuration, GitHub Actions CI
- MIT license for code, OGL 1.0a text and Section 15 notice for SRD-derived content
- `errors.py` exception hierarchy root (`OsrlibError`, `ContentValidationError`)
- `core/rng.py` — named PCG64 streams forked from a master seed
- `core/dice.py` — the dice expression grammar from the spec
- `core/clock.py` — time units and the `GameClock`
- `core/events.py` — the event base class that locks the event-emission contract
- `SCHEMA_VERSION` and engine-version stamping mechanism
- Tests for all of the above, green in CI

Out of scope (later phases): the SRD compiler, all rules content, `Ruleset`, sessions, commands, persistence. Phase 0 creates no `osrlib/data/` and no `tools/srd_compile/`. The default English message formatter ships with the first real events (Phase 2), and the effect-scheduling machinery that consumes clock boundaries belongs to `core/effects.py` (Phase 2).

## Work items

### 1. Project scaffolding

- `pyproject.toml`: package `osrlib`, `requires-python = ">=3.14"`, runtime dependency on `pydantic>=2` only. Dev group: `pytest`, `ruff`, `hypothesis` (the spec calls for property tests on the dice parser).
- src layout: `src/osrlib/` with `core/` subpackage. The spec's architecture diagram draws a flat `osrlib/` at repo root, but src layout is uv's library default, prevents accidentally importing the checkout instead of the installed package, and honors the spec's "supporting directories outside the package" intent. This is a layout choice, not a contract change; spec references like `osrlib/data/` map to `src/osrlib/data/`.
- `py.typed` marker in the package from day one — this is a typed library.
- `uv.lock` committed. `.python-version` pinned to 3.14. `.gitignore` for Python/uv artifacts.
- ruff configuration: line length 120, Google docstring convention, import ordering per the house style (stdlib, third-party, local).
- `tests/` at repo root with pytest configured in `pyproject.toml`.
- Minimal `README.md`: what osrlib is, development status, licensing split, dev quickstart (`uv sync`, `uv run pytest`).

### 2. Licensing

- `LICENSE`: MIT, covering library code.
- `LICENSE-OGL.md`: the OGL 1.0a text with the complete Section 15 copyright notice, sourced from the scraped SRD license page (`srd/%25E2%25A7%25BCOpen_Game_License%25E2%25A7%25BD.md` — the URL-encoded filename renders as ⧼Open_Game_License⧽), which carries the full chain from Wizards of the Coast through Necrotic Gnome. Applies to `srd/` now and to `osrlib/data/` when it exists; an osrlib entry is added to Section 15 when we first distribute compiled Open Game Content.
- Phase 1 seam, noted now so it isn't missed: the spec requires the OGL text to ship *alongside the generated data*, and a repo-root license file does not automatically land in the built wheel. When `osrlib/data/` arrives, the OGL text must be packaged with it.
- README licensing section states the split explicitly (code MIT, SRD-derived data OGL) and avoids compatibility claims that would trip the OSE trademark terms.

### 3. Continuous integration

- GitHub Actions workflow on push and pull request: install via `astral-sh/setup-uv` with the committed lockfile, then `ruff format --check`, `ruff check`, `uv run pytest`.
- Python 3.14; ubuntu plus macos matrix. The second OS is nearly free and turns the determinism contract's implicit platform-independence claim into a tested property.
- The SRD-regeneration diff check the spec requires arrives with the compiler in Phase 1; the workflow is structured so that job slots in beside the existing ones.

### 4. Errors and versioning

- `src/osrlib/errors.py`: `OsrlibError` base plus `ContentValidationError` (needed by `dice`). Other exception types are added by the phases that need them — the hierarchy grows additively. Programmer misuse (bad argument types, out-of-range seeds) raises stdlib `ValueError`/`TypeError`, not `OsrlibError`: the spec reserves the typed hierarchy for out-of-fiction failures like corrupt saves and malformed content.
- `src/osrlib/versioning.py`: `SCHEMA_VERSION = 1` (the single monotonically increasing integer shared by saves, commands, and events) and `engine_version()` read from package metadata via `importlib.metadata`. Persistence (Phase 4) and session metadata consume these; defining them now means every serialized model can stamp itself from birth.

### 5. RNG — `core/rng.py`

The determinism contract made concrete. This module is the most contract-laden deliverable in Phase 0 because draw sequences become part of the public compatibility guarantee. Every algorithmic choice below is frozen once golden vectors land.

- Pure-Python PCG64 — the `pcg_setseq_128_xsl_rr_64` variant (128-bit LCG state, XSL-RR output to 64 bits), the same generator behind numpy's `PCG64` (not `PCG64DXSM`, which is a different algorithm). No numpy at runtime, per the spec's dependency budget; Python's native big ints make this straightforward, and performance is a non-issue for a turn-based engine.
- Step order pinned: each `next_uint64()` advances the LCG first, then applies XSL-RR to the *new* state. This is the pcg-c 128-bit convention numpy follows — and the opposite of the widely tutorialized pcg32 pattern (output old state, then step). An implementer working from the PCG paper's headline example will produce an offset sequence; this sentence exists to prevent that.
- Stream derivation exactly as specified: seed material is `SHA-256(master_seed_bytes + b":" + stream_key_utf8)`. The master seed is an int in `[0, 2**128)`; its canonical byte encoding is fixed-width 16-byte big-endian (out-of-range raises `ValueError`). The 32-byte digest splits into the PCG64 init pair, both halves read big-endian: bytes 0–15 are `initstate`, bytes 16–31 are `initseq`. The pair feeds the canonical PCG init (`state = 0; inc = (initseq << 1) | 1; step; state += initstate; step`). The canonical init discards the top bit of `initseq` — expected behavior, not a bug to fix.
- `RngStreams(master_seed)` container: `streams.get("combat")` returns the named `RngStream`, creating it on first use; the same key always returns the same stream object. Stream identity depends only on master seed and key string.
- `RngStream` API: `next_uint64()`, and a bounded draw `randbelow(n)` frozen as top-bits rejection sampling: let `k = (n - 1).bit_length()`, then `candidate = next_uint64() >> (64 - k)`; reject and redraw while `candidate >= n`. No masking of low bits. `randbelow(1)` has `k = 0`, always yields 0, and still consumes one draw — no special case. `n <= 0` raises `ValueError`. Rejection means the raw-draw count per bounded draw is variable: power-of-two bounds (2, 4, 8) never reject; 3, 6, 10, 12, 20, and 100 can.
- Die-value mapping pinned here for `dice.py` to build on: one die of size S is `randbelow(S) + 1`, and multi-die rolls draw in left-to-right die order.
- Dice convenience lives in `dice.py`, not here; `rng.py` knows nothing about dice.
- Stream state (128-bit state, increment) is exportable and restorable from day one, so Phase 4 saves can serialize in-progress streams without a redesign.
- Validation via embedded golden vectors, generated once with provenance documented in the test file, covering three independent anchors:
    - Output function: numpy's `PCG64` accepts raw `(state, inc)` through its `.state` property and exposes raw 64-bit outputs via `random_raw()`, giving canonical step/output vectors.
    - Init procedure: numpy cannot be handed `(initstate, initseq)` directly (its seeding runs through `SeedSequence`), so init is validated against numpy's embedded canonical seeding: call `SeedSequence.generate_state(4, uint64)` ourselves, assemble `initstate = (w0 << 64) | w1` and `initseq = (w2 << 64) | w3`, run *our* init on that pair, and assert the resulting `(state, inc)` equals numpy's post-seed `.state`. This exercises numpy's real `pcg64_srandom_r` rather than re-deriving the init in the generator script (which would be circular).
    - Independent anchor: known-answer values from O'Neill's pcg-c reference test output for `setseq-128-xsl-rr-64` (seed 42, seq 54), embedded alongside the numpy-derived vectors so no single oracle is load-bearing.
    - The generator script lives under `tests/` and runs via `uv run --with numpy` (PEP 723 inline metadata); numpy is not a project dependency, dev or otherwise.

### 6. Dice — `core/dice.py`

The grammar from the spec, no more:

- `NdS` with optional `+M`/`-M` modifier and optional `×K` multiplier (`x` and `*` accepted as ASCII aliases). `N` defaults to 1. `d%` is an alias for `d100`. Die sizes `S ∈ {2, 3, 4, 6, 8, 10, 12, 20, 100}` — a closed set; anything else raises `ContentValidationError`.
- Grammar edges pinned here since the spec is silent, and `parse` acceptance is contract:
    - Parsing is case-insensitive (`2D6`, `1d6X10`); surrounding whitespace is stripped; internal whitespace is rejected.
    - `N ≥ 1`, `K ≥ 1`, `M` any integer.
    - The `%` alias composes like any die size: `Nd%` ≡ `Nd100`, with modifiers and multipliers allowed.
    - Component order is fixed: dice, then modifier, then multiplier. `2d6+1×10` parses; `2d6×10+1` raises `ContentValidationError`.
    - Evaluation order is `(sum of dice + M) × K`. This is *not* ordinary arithmetic precedence (`2d6+1×10` means `(2d6+1)×10`, not `2d6+10`) — the module docstring calls this out and a test asserts the value.
    - Results are not clamped: `1d4-1` can total 0 and `1d4-2` can total −1. Minimum-1-damage is combat's rule (Phase 2), not the dice module's.
- API: `parse(expr) -> DiceExpression` (frozen pydantic model: count, sides, modifier, multiplier) and `roll(expr, stream) -> RollResult` carrying the individual die results, modifier, multiplier, and total. Per-die results are kept because Phase 2 events want to show the rolls, not just totals. Dies roll left to right via `randbelow(S) + 1` per the mapping pinned in the rng section.
- Every roll draws from an explicitly passed `RngStream` — there is no default stream, no module-level RNG, by API construction.

### 7. Clock — `core/clock.py`

- Units: the round (10 seconds), the turn (10 minutes = 60 rounds), the day (144 turns). Internally time is a single integer count of rounds — the finest unit — so arithmetic is exact and serialization is one field.
- `GameClock` (pydantic model): current time, `advance(n, unit)`, and unit-boundary reporting — advancing returns which turn and day boundaries were crossed, in order, so the Phase 2 effects engine can resolve expirations and ticks at each boundary per the canonical tick order.
- Boundary semantics pinned now because they become load-bearing in Phase 2: an advance that lands exactly on a boundary reports that boundary (a torch lit at turn 0 expires when the clock reaches turn 6, not turn 7). Negative advances raise `ValueError`; zero advances are legal no-ops that cross nothing.

### 8. Event contract — `core/events.py`

Phase 0 locks the emission rules with a concrete base class, even though no kernel events exist yet:

- `Visibility` is a str-valued enum with wire values `"player"` and `"referee"` — lowercase, pinned by test, because these serialize into every event and changing them later is a `schema_version` bump.
- A frozen pydantic `Event` base carrying `code` and `visibility`, with `model_config = ConfigDict(frozen=True, extra="ignore")`. The `extra="ignore"` pin is the additive-schema contract made mechanical: the spec requires consumers to ignore unknown fields, and a later model quietly setting `extra="forbid"` would break that guarantee. A test deserializes an event payload containing unknown fields and asserts success.
- `code` is validated dotted snake_case: two or more dot-separated segments, each matching `[a-z][a-z0-9_]*` (spec examples like `combat.attack.hit` are three-part; two-part codes are legal).
- The base class docstring states the contract: structured fields and message codes only, never baked English prose; consumers must tolerate unknown event types and fields; additive-only within a `schema_version`.
- Deferred, explicitly: whether serialized events carry a type discriminator beyond `code` (the "ignore unknown event types" rule presupposes consumers can identify types). That decision lands with the first real event emissions and the command/event envelope in Phase 2, before any event crosses a serialization boundary.

### 9. Tests

- **rng**: SHA-256 derivation vectors; PCG64 output goldens (numpy `random_raw` with matching raw state); init-procedure vectors via numpy's `SeedSequence` seeding path; pcg-c reference known-answer values (seed 42, seq 54); same seed + same key → two independently derived streams produce identical sequences; different keys → independent streams (drawing from one never shifts another); `randbelow` determinism goldens including at least one vector constructed to hit the rejection branch (the algorithm's most fragile path must not ship untested); `randbelow(1)` consumes exactly one draw; `randbelow(0)` raises; state export/restore round-trip; fixed-seed chi-square uniformity check on bounded draws (fixed seed so CI never flakes — it catches masking bugs, not cosmic rays).
- **dice**: acceptance/rejection table for the grammar — accepts `d6`, `d%`, `2d%`, `2d6×10`, `2D6X10`, `2d6*10`, `1d4-1`, `2d6+1×10`; rejects `3d7`, `0d6`, `2d6×0`, `2d6×10+1`, `2d6 + 1`, empty string; the precedence assertion (`2d6+1×10` evaluates as `(2d6+1)×10`); negative totals permitted; hypothesis property tests (any parsed expression rolls within computed bounds; total is consistent with per-die results, modifier, and multiplier); determinism goldens with a fixed stream.
- **clock**: unit conversions; boundary-crossing order across multi-unit advances; exact-landing advances report the boundary; negative advance raises; zero advance crosses nothing; serialization round-trip.
- **events/versioning**: code-format validation accepts/rejects correctly; `Event` is frozen; unknown-field deserialization succeeds; `Visibility` wire values are `"player"`/`"referee"`; `SCHEMA_VERSION` importable; `engine_version()` matches package metadata.
- All green under `uv run pytest` locally and in CI.

## Sequencing

1. Scaffolding, licensing, CI — CI goes green on a trivial test before any real code lands.
2. `errors.py` and `versioning.py` — no dependencies, everything else needs them.
3. `core/rng.py` — the contract-heavy module, validated against reference vectors.
4. `core/dice.py` — depends on rng and errors.
5. `core/clock.py` and `core/events.py` — independent of each other and of dice/rng.
6. README polish and a final pass verifying every spec contract quoted above is implemented or explicitly deferred with its phase noted.

## Definition of done

- `uv sync && uv run ruff format --check && uv run ruff check && uv run pytest` passes locally and in CI on both OSes.
- PCG64 goldens (output, init, and pcg-c reference anchors) and the `randbelow` algorithm are locked in tests with provenance notes.
- Two `RngStream`s independently derived from the same master seed and the same key produce identical `2d6×10` roll sequences — demonstrated in a test, with the equivalent snippet in the README. (Successive rolls on one stream differ, of course; reproducibility across derivations is the contract.)
- OGL text, Section 15 notice, and MIT license are in the repo with the split documented.
- Every Phase 0 contract in the spec (determinism, event emission, schema/engine stamping, dice grammar) is traceable to code and tests.
