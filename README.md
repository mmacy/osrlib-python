# osrlib

A Python library implementing the Old-School Essentials (OSE) SRD rules — a restatement of the 1981 B/X D&D rules — for turn-based, grid-based dungeon crawlers in the style of the original Bard's Tale. osrlib is the rules authority and game-state engine; the game supplies presentation, input, and content.

See [the specification](docs/spec.md) for the full design: architecture, contracts, rules scope, and the phased roadmap.

## Development status

Early development — Phase 0 (scaffolding and contracts) is complete: deterministic named RNG streams, the dice expression grammar, the game clock, and the event-emission contract. No rules content ships yet; characters, combat, magic, and the dungeon crawl arrive in later phases.

## Determinism

Determinism is a public API guarantee. All randomness flows through named PCG64 streams forked from a master seed, so the same seed and the same key always produce the same stream — independently of any other stream:

```python
from osrlib.core.dice import roll
from osrlib.core.rng import RngStreams

streams_a = RngStreams(master_seed=42)
streams_b = RngStreams(master_seed=42)

rolls_a = [roll("2d6×10", streams_a.get("treasure")).total for _ in range(3)]
rolls_b = [roll("2d6×10", streams_b.get("treasure")).total for _ in range(3)]
assert rolls_a == rolls_b  # same seed + same key → identical sequences
```

Successive rolls on one stream differ, of course; reproducibility across derivations is the contract.

## Development quickstart

Requires Python ≥ 3.14 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run pytest
```

Format and lint with ruff:

```sh
uv run ruff format
uv run ruff check
```

## Licensing

This repository contains two kinds of material under two licenses:

- **Library code** is licensed under the [MIT license](LICENSE).
- **SRD-derived content** — the scraped OSE SRD text in `srd/` and, in later phases, the compiled game data — is Open Game Content used under the [Open Game License 1.0a](LICENSE-OGL.md), which includes the complete Section 15 copyright notice.
