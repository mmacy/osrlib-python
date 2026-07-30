# Changelog

All notable changes to osrlib are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The package version is the public API promise; `schema_version`, the integer stamped into saves, commands, and events, is the separate serialization axis defined by [the specification](docs/spec.md).

## [Unreleased]

### Added

- `CharacterLeveledUpEvent` (`session.level.gained`, player visibility) — fires immediately after a member's `XpAwardedEvent` whenever an XP award crosses a level threshold, on every award surface: the end-of-adventure award, the immediate timing, and the referee's `AwardXP`. It carries the levels before and after, the hit points gained, the raw hit die roll (`None` past name level, where the gain is the flat-bonus delta), whether the CON modifier applied, and the class's level title at the new level (`None` past the printed title list). Previously a level gain surfaced only as the `level_after` field on the XP award, so a front end had no event to announce the moment on — and no hit-point or title facts to announce it with.
- `LearnSpell`, a command that adds a spell to an arcane caster's spell book in-session — leveling's new capacity or a mentor's teaching made concrete, driving the core `add_spell_to_book` through the command loop so the addition is validated, logged, and replayed like any other command. Legal in town and while exploring, and no game time passes: the fiction around the learning (the mentor's week, a copied scroll's costs) belongs to the game. The per-spell-level capacity, the duplicate/wrong-list/non-arcane gates, and the book-never-shrinks rule are exactly the core function's, and a successful learning emits the existing `SpellBookUpdatedEvent`.
- `open_book_capacity` — the per-spell-level count of open spell-book slots: the progression row's capacity minus the spells held at that level, floored at zero (a drained caster's book may sit over capacity and simply reads as no openings), `()` for a class with no arcane book. `add_spell_to_book`'s capacity check now consults it, so the rule has one home a front end can share when rendering what a caster may still learn. Alongside it, `level_title` answers a class's level title at a given level, `None` beyond the printed list — the level event reads it.

## [1.4.0] - 2026-07-28

### Added

- `osrlib.crawl.content_pack` — content packs, the portable form of finished keyed content. A `ContentPack` holds sections of geometry-free entries (`AreaSpec` minus its cells: prose, keyed encounter, room trap, treasure declaration, features), each section optionally carrying a level's `WanderingSpec`, plus the bundled `MonsterTemplate`s the entries' encounters and the sections' wandering tables reference — the pack's closure. Section ids, pack-wide entry ids, and bundled monster ids are unique by construction; dangling references stay legal, and `validate_content_pack` reports them as structured `PackFinding`s (dotted snake_case codes, the `Rejection` discipline) instead of raising. Packs serialize as stamped `"content_pack"` documents via `ContentPack.to_document`/`from_document` with the documented acceptance rules: older schema versions load, newer ones fail with `SaveVersionError`, and any write re-stamps at current versions.
- `TakeTreasure.recipient_id` (optional) — names the single party member who fills their pack from the cache or pile, in place of the default spread. The named member is also the one who reaches in, so they are the character an unresolved treasure trap springs on. Goods beyond that member's own maximum load stay on the cell.
- `ItemsLeftBehindEvent` (`exploration.item.left_behind`, player visibility) — reports the goods and coin a haul left in the drop pile on the party's cell because the carriers had no capacity for them.

### Fixed

- A recovered haul now spreads across the living party instead of landing entirely on the first member in marching order. The old behavior was not merely unfair, it was party-stopping: one pickup of 600 sp and 900 cp on an already-laden character crossed the SRD's 1,600-coin maximum load, dropped that character's movement rate to 0, and — since "the movement rate of the party as a whole is determined by the speed of the slowest member" — froze the entire party until a player unwound it by hand with `GiveItems`. `TakeTreasure` now assigns items to a character whose class may use them (the same class policies `validate_equip` enforces, plus a magic item's own `usable_by`, ignoring equipped state), divides gems and jewellery by worth rather than by count, and divides coins evenly denomination by denomination — every coin weighing 1, an even split of a denomination is even in wealth and in weight at once. Nothing is ever loaded past the maximum load, so a pickup cannot immobilise a carrier that some other arrangement would have kept moving; whatever the party cannot carry stays in the cell's drop pile with an `ItemsLeftBehindEvent` rather than being destroyed, goods packing best-first so a party out of capacity keeps the platinum and leaves the copper. The pass is deterministic and spends no RNG draw — ties break on marching order — and the XP award is unchanged, since `party_valuation_cp` sums the whole party however the goods are split. `TakeTreasure` now emits one `ItemAcquiredEvent` per member who took something, which moved the Phase 4 and Phase 5 scenario goldens.
- Encounter distance is now bounded by the space the encounter happens in. The 2d6 × 10' dungeon roll ignored the party's surroundings entirely, so an encounter keyed to a 20' room could open at 120' — six times the room's width — and a rolled distance routinely exceeded the whole dungeon level. The SRD rolls only "if there is uncertainty", since "the situation in which the encounter occurs often determines how far away the monster is"; the walls around the party's cell are that situation, so a rolled distance now caps at the longest unobstructed straight sight line from that cell across the four grid directions, floored at one cell (10'). A corridor still affords its full sight line and the printed 20'–120' band; a room affords no more than the room; shut and undiscovered-secret doors stop the line where they stop the party. Darkness never shortens it — light governs surprise, not distance — and a caller-supplied `distance_feet` (every referee spawn: `SpawnMonsters`, `SpawnNpcParty`) is never capped. The cap reads the rolled result and never the RNG stream, so no draw sequence changes; rolled distances in cramped spaces do change, which moved the Phase 4 and Phase 5 scenario goldens.

## [1.3.0] - 2026-07-24

### Added

- `osrlib.crawl.stocking.stock_area` — the SRD dungeon-stocking procedure that consumes the shipped stocking tables. Given a dungeon level, an effective monster catalog, and one `RngStream`, it rolls a single keyed area's contents (the stocking d6, then the treasure d6 when the row calls for it, then, on a monster room, the encounter table's d20 row, its count, and the variant or per-individual pool picks) and answers a frozen `StockedArea` — content models an author can review, place, and edit. A monster room's rolled individuals group by template into `KeyedMonster` lines with concrete counts; an empty or trap room that rolls treasure gets an unguarded `AreaTreasureSpec`; an NPC-party row reports its rolled kind and count as a `StockedNpcParty` and stops (a party has no keyed content model). Every draw comes from the passed stream in a fixed order, so a stocked area is reproducible from the stream's state alone. Traps and specials produce no models — the procedure ends where the referee's design begins.
- `KeyedEncounter.hoard` (default `True`) — gates whether the engine generates the keyed monsters' lair hoard when the encounter first spawns. The default preserves every existing document's play semantics; `hoard=False` expresses the treasure-absent keyed room (a monster room the SRD stocking roll gave no treasure), which an unconditional lair hoard could not otherwise represent.
- `GiveItems`, a command that hands items and coins from one party member to another in zero game time — the distribute-the-load move for shifting weight off an overloaded companion so the party's marching rate recovers. Legal in town and while exploring (not mid-encounter or in battle); both members must be able-bodied and distinct. A given magic item releases its worn effects and lands unequipped in the recipient's pack, mundane items merge into a like stack, valuables and coins move across, and the transfer emits a player-visible `ItemsGivenEvent`. The giver must actually carry what's named, and a revealed cursed item cannot be handed off.

## [1.2.1] - 2026-07-20

### Fixed

- The player view's explored cells now include what the party sees by its own light from its current cell — the lit room it stands in and open passages out to the light source's radius (a torch's 30 feet, the *light* spell's 15) — rather than only the cells it has physically entered. A front end drawing `PlayerView.explored` renders the torchlit room at once instead of leaving it dark until the party steps onto each square. The reveal is sight, not exploration: it never enters the persisted explored set (so movement cost and map memory are unchanged), it stops at walls and shut or undiscovered doors, and it never reaches the referee view.

## [1.2.0] - 2026-07-17

### Added

- `Adventure.monsters` — an adventure document can bundle its own custom `MonsterTemplate`s, which join the shipped catalog for that adventure's sessions everywhere the engine resolves template ids: keyed encounters, `SpawnMonsters`, inline wandering tables, listen checks, and `GameSession.spawn`. Downstream of spawn nothing changes — combat, XP, treasure, persistence, and replay carry bundled monsters unmodified. Bundled ids must not collide with the shipped catalog or each other; collisions fail `validate_adventure` (and, for doctored saves, `load_game`) with `ContentValidationError`. The session exposes the union as the read-only `GameSession.effective_monsters` property.

### Changed

- `validate_adventure` now checks inline wandering-table monster ids: an adventure whose level wandering table names a dangling monster id — previously accepted by the gate and left to crash at play time — fails validation up front.

## [1.1.0] - 2026-07-05

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

[Unreleased]: https://github.com/mmacy/osrlib-python/compare/v1.4.0...HEAD
[1.4.0]: https://github.com/mmacy/osrlib-python/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/mmacy/osrlib-python/compare/v1.2.1...v1.3.0
[1.2.1]: https://github.com/mmacy/osrlib-python/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/mmacy/osrlib-python/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/mmacy/osrlib-python/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/mmacy/osrlib-python/releases/tag/v1.0.0
