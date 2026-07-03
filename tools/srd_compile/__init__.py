"""The SRD markdown → JSON compiler.

Parses the scraped OSE SRD in `srd/` into the generated JSON in `src/osrlib/data/`.
Dev-time only, never shipped; run as `uv run python -m tools.srd_compile` from the
repo root. Stdlib plus pydantic only — the compiler imports osrlib models to validate
its own output at build time.

Bad or ambiguous parses are corrected by patch files in `overrides/`, merged after
parsing with provenance recorded in the output; `srd/` itself is never edited.
"""
