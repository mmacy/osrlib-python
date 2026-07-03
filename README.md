# osrlib

A Python library implementing the classic 1981 B/X (Basic/Expert) fantasy adventure game rules for turn-based, grid-based dungeon crawlers in the style of the original Bard's Tale. The rules are sourced from the Old-School Essentials System Reference Document, an Open Game Content restatement of the B/X rules. osrlib is the rules authority and game-state engine; the game supplies presentation, input, and content.

See [the specification](docs/spec.md) for the full design: architecture, contracts, rules scope, and the phased roadmap.

## Development status

Early development — Phase 2 (the combat kernel) is complete: the monster compiler (all 138 SRD monster pages, with packed-variant expansion, structured defenses, and ability tags), the combat tables as data (attack matrix, monster saving-throw bands, XP awards), attack resolution with weapon-material and graded-immunity damage, the effects engine with the core condition set (regeneration, poison, paralysis, petrification, mummy rot), initiative, morale, saving throws, energy drain, the shared targeting model, the first kernel events with the serialized type discriminator, and the default English message formatter. Phase 1 delivered characters (classes, ability scores, creation, inventory and encumbrance, XP and leveling) and Phase 0 the contracts underneath (deterministic named RNG streams, the dice grammar, the game clock, the event-emission rules). The battle state machine, magic, and the dungeon crawl arrive in later phases.

## SRD data pipeline

The game data in `src/osrlib/data/` is generated from the scraped SRD markdown in `srd/` and is never hand-edited. Regenerate it with:

```sh
uv run python -m tools.srd_compile
```

CI regenerates the data and fails on any diff, so `srd/`, the compiler, and the generated data cannot silently drift. Parser corrections belong in `tools/srd_compile/overrides/`, never in the output; every override carries a reason and is recorded in the output entry's `overrides_applied` provenance list. Pinned rules interpretations are registered in [docs/adaptations.md](docs/adaptations.md).

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
- **SRD-derived content** — the scraped SRD text in `srd/` and the compiled game data in `src/osrlib/data/` — is Open Game Content used under the [Open Game License 1.0a](LICENSE-OGL.md), which includes the complete Section 15 copyright notice. The data package ships its own copy of the license, with the osrlib Section 15 entry, inside the built wheel.

osrlib is an independent project, not affiliated with or endorsed by Necrotic Gnome. "Old-School Essentials" is a trademark of Necrotic Gnome, used here only to identify the source document; no claim of compatibility is made.
