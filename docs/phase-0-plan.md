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

Out of scope (later phases): the SRD compiler, all rules content, `Ruleset`, sessions, commands, persistence. Phase 0 creates no `osrlib/data/` and no `tools/srd_compile/`.

## Work items

### 1. Project scaffolding

- `pyproject.toml`: package `osrlib`, `requires-python = ">=3.14"`, runtime dependency on `pydantic>=2` only. Dev group: `pytest`, `ruff`, `hypothesis` (the spec calls for property tests on the dice parser).
- src layout: `src/osrlib/` with `core/` subpackage. The spec's architecture diagram draws a flat `osrlib/` at repo root, but src layout is uv's library default, prevents accidentally importing the checkout instead of the installed package, and honors the spec's "supporting directories outside the package" intent. This is a layout choice, not a contract change.
- `uv.lock` committed. `.python-version` pinned to 3.14. `.gitignore` for Python/uv artifacts.
- ruff configuration: line length 120, Google docstring convention, import ordering per the house style (stdlib, third-party, local).
- `tests/` at repo root with pytest configured in `pyproject.toml`.
- Minimal `README.md`: what osrlib is, development status, licensing split, dev quickstart (`uv sync`, `uv run pytest`).

### 2. Licensing

- `LICENSE`: MIT, covering library code.
- `LICENSE-OGL.md`: the OGL 1.0a text with the complete Section 15 copyright notice, sourced from the scraped SRD license page (`srd/⧼Open_Game_License⧽.md`), which carries the full chain from Wizards of the Coast through Necrotic Gnome. Applies to `srd/` now and to `osrlib/data/` when it exists (Phase 1); an osrlib entry is added to Section 15 when we first distribute compiled Open Game Content.
- README licensing section states the split explicitly (code MIT, SRD-derived data OGL) and avoids compatibility claims that would trip the OSE trademark terms.

### 3. Continuous integration

- GitHub Actions workflow on push and pull request: install via `astral-sh/setup-uv` with the committed lockfile, then `ruff format --check`, `ruff check`, `uv run pytest`.
- Python 3.14 only, matching `requires-python`.
- The SRD-regeneration diff check the spec requires arrives with the compiler in Phase 1; the workflow is structured so that job slots in beside the existing ones.

### 4. Errors and versioning

- `src/osrlib/errors.py`: `OsrlibError` base plus `ContentValidationError` (needed by `dice`). Other exception types are added by the phases that need them — the hierarchy grows additively.
- `src/osrlib/versioning.py`: `SCHEMA_VERSION = 1` (the single monotonically increasing integer shared by saves, commands, and events) and `engine_version()` read from package metadata via `importlib.metadata`. Persistence (Phase 4) and session metadata consume these; defining them now means every serialized model can stamp itself from birth.

### 5. RNG — `core/rng.py`

The determinism contract made concrete. This module is the most contract-laden deliverable in Phase 0 because draw sequences become part of the public compatibility guarantee.

- Pure-Python PCG64 (the `pcg_setseq_128_xsl_rr_64` variant — 128-bit LCG state, XSL-RR output to 64 bits). No numpy at runtime, per the spec's dependency budget; Python's native big ints make this straightforward, and performance is a non-issue for a turn-based engine.
- Stream derivation exactly as specified: seed material is `SHA-256(master_seed_bytes + b":" + stream_key_utf8)`. The master seed is a non-negative int; its canonical byte encoding is fixed-width 16-byte big-endian (seeds in `[0, 2**128)`), pinned in Phase 0 because it is contract. The 32-byte digest splits into the PCG64 init pair: first 16 bytes the state seed, last 16 the sequence selector, fed through the canonical PCG init.
- `RngStreams(master_seed)` container: `streams.get("combat")` returns the named `RngStream`, creating it on first use; the same key always returns the same stream object. Stream identity depends only on master seed and key string.
- `RngStream` API: `next_uint64()`, and a bounded draw `randbelow(n)` implemented as top-bits masked rejection sampling. The exact algorithm is frozen and golden-tested — changing it would silently shift every draw sequence.
- Dice convenience lives in `dice.py`, not here; `rng.py` knows nothing about dice.
- Stream state (128-bit state, increment) is exportable and restorable from day one, so Phase 4 saves can serialize in-progress streams without a redesign.
- Validation: golden test vectors embedded in the test suite, generated once from the PCG reference implementation (numpy's PCG64 initialized with matching raw state), with provenance documented in the test file. The generator script is kept under `tests/` so vectors can be re-derived, but numpy is not a dependency, dev or otherwise.

### 6. Dice — `core/dice.py`

The grammar from the spec, no more:

- `NdS` with optional `+M`/`-M` modifier and optional `×K` multiplier (`x` and `*` accepted as ASCII aliases). `N` defaults to 1. `d%` is an alias for `d100`. Die sizes `S ∈ {2, 3, 4, 6, 8, 10, 12, 20, 100}` — a closed set; anything else raises `ContentValidationError`.
- Grammar edges pinned here since the spec is silent: parsing is case-insensitive (`2D6`, `1d6X10`), surrounding whitespace is stripped, internal whitespace is rejected, `N ≥ 1`, `K ≥ 1`, `M` any integer. Evaluation order is `(sum of dice + M) × K`. Modifier and multiplier may combine (`2d6+1×10`) since the grammar makes both optional and independent. Results are not clamped — `1d4-1` can total 0; minimum-1-damage is combat's rule (Phase 2), not the dice module's.
- API: `parse(expr) -> DiceExpression` (frozen pydantic model: count, sides, modifier, multiplier) and `roll(expr, stream) -> RollResult` carrying the individual die results, modifier, multiplier, and total. Per-die results are kept because Phase 2 events want to show the rolls, not just totals.
- Every roll draws from an explicitly passed `RngStream` — there is no default stream, no module-level RNG, by API construction.

### 7. Clock — `core/clock.py`

- Units: the round (10 seconds), the turn (10 minutes = 60 rounds), the day (144 turns). Internally time is a single integer count of rounds — the finest unit — so arithmetic is exact and serialization is one field.
- `GameClock` (pydantic model): current time, `advance(n, unit)`, and unit-boundary reporting — advancing returns which turn and day boundaries were crossed, in order, so the Phase 2 effects engine can resolve expirations and ticks at each boundary per the canonical tick order.
- Phase 0 delivers time representation, arithmetic, and boundary iteration. The effect-scheduling machinery that consumes boundaries belongs to `core/effects.py` (Phase 2); building scheduler hooks now would be speculation about an API with no consumer.

### 8. Event contract — `core/events.py`

Phase 0 locks the emission rules with a concrete base class, even though no kernel events exist yet:

- `Visibility` enum (`PLAYER`, `REFEREE`) and a frozen pydantic `Event` base carrying `code` (validated dotted snake_case, e.g. `combat.attack.hit`) and `visibility`.
- The base class docstring states the contract: structured fields and message codes only, never baked English prose; consumers must tolerate unknown event types and fields; additive-only within a `schema_version`.
- No message formatter yet — that ships with the first real events (Phase 2). Locking the base class now means Phase 1–2 models inherit the rules instead of retrofitting them.

### 9. Tests

- **rng**: SHA-256 derivation vectors; PCG64 output golden vectors against the reference implementation; same seed + same key → identical sequences; different keys → independent streams (drawing from one never shifts another); `randbelow` determinism goldens; state export/restore round-trip; chi-square uniformity sanity check on bounded draws (catches masking bugs).
- **dice**: acceptance/rejection table for the grammar (spec examples plus edge cases: `d6`, `d%`, `2d6×10`, `2D6X10`, `1d4-1`, rejects like `3d7`, `0d6`, `2d6 + 1`, empty string); hypothesis property tests (any parsed expression rolls within computed bounds; total is consistent with per-die results); determinism goldens with a fixed stream.
- **clock**: unit conversions; boundary-crossing order across multi-unit advances; serialization round-trip.
- **events/versioning**: code-format validation accepts/rejects correctly; `Event` is frozen; `SCHEMA_VERSION` importable; `engine_version()` matches package metadata.
- All green under `uv run pytest` locally and in CI.

## Sequencing

1. Scaffolding, licensing, CI — CI goes green on a trivial test before any real code lands.
2. `errors.py` and `versioning.py` — no dependencies, everything else needs them.
3. `core/rng.py` — the contract-heavy module, validated against reference vectors.
4. `core/dice.py` — depends on rng and errors.
5. `core/clock.py` and `core/events.py` — independent of each other and of dice/rng.
6. README polish and a final pass verifying every spec contract quoted above is implemented or explicitly deferred with its phase noted.

## Definition of done

- `uv sync && uv run ruff format --check && uv run ruff check && uv run pytest` passes locally and in CI.
- PCG64 golden vectors and `randbelow` algorithm are locked in tests with provenance notes.
- Rolling `2d6×10` twice from the same seeded stream produces identical, correct results — demonstrated in a test, and the README shows the equivalent snippet.
- OGL text, Section 15 notice, and MIT license are in the repo with the split documented.
- Every Phase 0 contract in the spec (determinism, event emission, schema/engine stamping, dice grammar) is traceable to code and tests.
