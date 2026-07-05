# Changelog

All notable changes to osrlib are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The package version is the public API promise; `schema_version`, the integer stamped into saves, commands, and events, is the separate serialization axis defined by [the specification](docs/spec.md).

## [Unreleased]

### Added

- `RollDice`, an authorial command that rolls an arbitrary dice expression through the seeded session for freeform referee adjudication. It draws from a dedicated `adjudication` RNG stream and emits a referee-visibility `DiceRolledEvent`, so an ad-hoc roll is accepted, logged, and replayable without ever perturbing a keyed mechanic's draw sequence.

## [1.0.0] - 2026-07-05

### Added

- The determinism contract: every random draw flows through named PCG64 streams forked from a master seed, so the same seed and the same commands always replay the same game — a public API guarantee.
- Character creation, the seven B/X classes, equipment and encumbrance, and XP-driven leveling, all sourced from the compiled OSE SRD data that ships inside the package.
- The combat kernel: initiative, attacks, damage, saving throws, morale, and death, resolved as pure functions over explicit state.
- Magic: arcane and divine spell books, memorization, casting with disruption, spell effects, and turning undead.
- The crawl: town, travel, and turn-based dungeon exploration — movement, doors, light, listening, searching, traps, rest, and wandering monsters — plus the battle state machine for declared rounds.
- The `GameSession` command/event API: typed commands in, structured events with message codes out, player/referee visibility, views, listeners, and session flags.
- Treasure types A–V, magic items with identification, NPC adventurer parties, and the end-of-adventure XP award.
- Save, load, and replay: stamped JSON documents with schema versioning and forward migrations; a loaded game is bit-for-bit the game you saved.
- Two example front ends — a terminal TUI crawler and a FastAPI HTTP service — proving the engine presentation-agnostic.
- The documentation site: quickstart, guides, front-end walk-throughs, and a full reference for every command, event, rejection code, message code, RNG stream, and content id.
- The typed surface: complete type hints under `py.typed`, checked in CI.

[Unreleased]: https://github.com/mmacy/osrlib-python/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/mmacy/osrlib-python/releases/tag/v1.0.0
