# osrlib specification

A Python library implementing the Old-School Essentials (OSE) SRD rules — a restatement of the 1981 B/X (Basic/Expert) D&D rules — for turn-based, grid-based dungeon crawlers in the style of the original Bard's Tale. Games built on osrlib are primarily text-based, but the library itself is presentation-agnostic.

## Vision

osrlib is the rules authority and game-state engine; the game supplies presentation, input, and content. A developer should be able to build a complete B/X dungeon crawler by writing only UI code and adventure content, while a developer who wants just the math (an attack roll, a reaction table, a treasure hoard) can use the kernel à la carte.

Target consumers, all first-class:

- A FastAPI backend serving a web or mobile crawler
- A local TUI (e.g. Textual) or terminal game
- An LLM-driven referee or narrator that consumes structured events and drives the engine with commands
- Scripts and simulations (balance testing, mass combat statistics, content validation)

## Design principles

**Headless, sans-I/O core.** The library never renders, prompts, sleeps, or touches the network. Every interaction is a synchronous function call. Async, HTTP, and terminal front ends are thin wrappers the game owns.

**Deterministic by construction.** All randomness flows through named RNG streams forked from the session's master seed (see the determinism contract). The same seed plus the same command sequence on the same engine version always produces the same game, which enables golden-file testing, replays, and bug reproduction.

**Commands in, events out.** Game state changes only via commands (`MoveParty`, `Attack`, `CastSpell`, `SearchForTraps`). Every rules resolution emits typed events (`AttackRolled`, `MoraleFailed`, `TrapSprung`, `TorchExpired`). Events are the single feed for UI text, LLM narration, logging, and replays.

**Fully serializable.** The entire game state round-trips to JSON. Save/load, FastAPI responses, and mid-session snapshots are the same mechanism.

**Data-driven rules content.** Monsters, spells, classes, equipment, and treasure tables are compiled from the SRD markdown in `srd/` into validated, typed data shipped with the package. Rules *procedures* are code; rules *content* is data.

**Layered.** `osrlib.core` (kernel) has no dependency on `osrlib.crawl` (framework). Games may use either layer.

**Faithful with documented adaptations.** Mechanics follow OSE rules-as-written. Where the tabletop game assumes a human referee or doesn't map to a CRPG, the library provides a default adaptation behind a `Ruleset` configuration flag, and every deviation is documented in `docs/adaptations.md`.

## OSE flavor strategy

Classic Fantasy first: the seven B/X classes (Cleric, Dwarf, Elf, Fighter, Halfling, Magic-User, Thief), where race-as-class is the model. The data model is designed Advanced-ready from day one:

- A character has a `character_class` and a `race` field; in Classic play, race is implied by class (Dwarf class ⇒ dwarf race) and populated automatically.
- Class definitions are pure data (requirements, prime requisites, hit dice, progression table, allowed armour/weapons, class abilities), so Advanced classes are additive data rather than a redesign.
- Rules variants that differ between Classic and Advanced hang off the `Ruleset` config.

## Architecture

```text
osrlib/
├── core/                # rules kernel — pure mechanics, no game loop
│   ├── dice.py          # dice expression parser/roller (see dice expressions)
│   ├── rng.py           # named PCG64 streams forked from a master seed
│   ├── clock.py         # time units (round, turn, day), game clock, tick scheduling
│   ├── events.py        # event base class + kernel event types
│   ├── ruleset.py       # Ruleset model: optional-rule and adaptation flags
│   ├── abilities.py     # ability scores, modifiers, checks, prime requisite rules
│   ├── classes.py       # class definitions, level progression, XP, leveling & drain
│   ├── character.py     # PC model: scores, class, HP, inventory, spells, conditions
│   ├── combat.py        # attack matrix, damage & immunities, initiative, morale,
│   │                    # saving throws, range bands, targeting, healing & death
│   ├── spells.py        # spell data model, memorization, casting, disruption
│   ├── effects.py       # timed effects and conditions: spell durations, potions,
│   │                    # poison, disease, light sources, regeneration,
│   │                    # petrification, location-bound areas (oil pools, webs)
│   ├── monsters.py      # monster stat blocks, special-ability & category tags,
│   │                    # NA/treasure refs
│   ├── items.py         # weapons (qualities, materials), armour, gear, magic items,
│   │                    # encumbrance
│   ├── treasure.py      # treasure types A–V, hoard generation, coin/gem/magic rolls
│   └── tables.py        # attack matrix, turning undead, reaction, other lookups
├── data/                # generated SRD data (JSON) + typed loaders
├── errors.py            # exception hierarchy: OsrlibError → SaveVersionError, ...
├── crawl/               # framework layer — the dungeon crawl game loop
│   ├── events.py        # crawl event types (subclass the core base)
│   ├── party.py         # marching order, shared resources, party-level actions
│   ├── adventure.py     # adventure container: dungeons, base town, scenario metadata
│   ├── dungeon.py       # multi-level grid model: cells, walls, doors, stairs,
│   │                    # keyed areas, traps, features, explored flags, Direction
│   ├── exploration.py   # exploration turn loop: movement, light, rest, searching,
│   │                    # wandering monster checks
│   ├── encounter.py     # encounter procedure: surprise, distance, reaction,
│   │                    # evasion & pursuit
│   ├── battle.py        # combat state machine: side initiative, round sequence,
│   │                    # range track, spell disruption, monster action policy,
│   │                    # morale, flee/TPK outcomes
│   └── session.py       # GameSession: command dispatch, event log, listeners,
│                        # flags, RNG, clock
└── persistence.py       # save/load: JSON schema, versioning, migrations
```

Supporting directories outside the package:

```text
srd/                     # the scraped OSE SRD markdown (input to the compiler)
tools/srd_compile/       # SRD markdown → JSON compiler (dev-time only, not shipped)
tests/                   # pytest suites, golden-seed scenario tests
docs/                    # specs, adaptations, developer docs
examples/                # TUI crawler (Phase 5) and FastAPI wrapper (Phase 6)
```

## The command/event model

`GameSession` is the single entry point for a running game:

```python
session = GameSession.new(party=party, adventure=adventure, seed=42, ruleset=Ruleset())
result = session.execute(MoveParty(direction=Direction.NORTH))
for event in result.events:
    ...  # render text, feed an LLM narrator, send over the wire
```

- Commands and events are typed models with stable JSON schemas, so a FastAPI layer can expose them directly and an LLM can be given the schemas as tool definitions.
- The command set spans in-fiction commands (`MoveParty`, `Attack`, `CastSpell`) and referee-level commands (`GrantItem`, `AwardXP`, `SetFlag`, `SpawnMonsters`) — the surface a game's own logic, module scripting, or an LLM referee drives. Referee commands are logged and replayed like any other.
- `execute()` returns a `CommandResult` envelope: `accepted`, a structured `rejection` reason when refused, and the events. In-fiction invalid commands (moving through a wall, casting an unmemorized spell) are rejected, not raised, so front ends can surface them as in-fiction feedback.
- Command validation is a pure pre-phase: a rejected command consumes no RNG draws, no clock time, and mutates nothing, and it is excluded from the command log — replays contain accepted commands only.
- Out-of-fiction failures — corrupt saves, unknown schema versions, malformed content, programmer misuse — raise typed exceptions from `errors.py` (`OsrlibError` → `SaveVersionError`, `ContentValidationError`, ...), which wrappers map to HTTP statuses or crash reports.
- Event emission is return-based, not a bus: kernel functions return event lists, and the session appends them to its log. Registered listeners (e.g. a game's quest tracker) then run in registration order; events a listener emits are appended to the end of the same `CommandResult`, so log order is deterministic. Listeners observe and annotate only — they never mutate game state directly; a game reacts to listener events by executing ordinary (logged) commands. Listener state is part of session state — snapshotted into saves, never re-derived from the log.
- Events carry structured fields, stable entity IDs, and a message code — dotted snake_case namespaced by subsystem (`combat.attack.hit`, `exploration.torch.expired`) — never baked English prose. A default English message formatter (pure string templating, no I/O) ships outside the event models, so front ends can localize and LLM narrators get facts rather than canned text.
- Events carry a visibility level (`player` or `referee`), because B/X hides some rolls by design — the referee rolls hide in shadows, move silently, and hear noise on the player's behalf, and the player shouldn't know the outcome until it matters. Front ends filter on it; an LLM referee sees everything while player-facing narration doesn't.
- The kernel is also callable without a session for à la carte use (`combat.attack_roll(...)`, `treasure.generate(TreasureType.D, rng)`).

## Reading state

`execute()` mutates; a projection API reads. Front ends must be able to render without replaying the event log — a client connecting mid-session or a TUI redrawing after load asks the session directly:

```python
view = session.view(Visibility.PLAYER)
```

The player view is a safe projection: party status, explored map cells, and known active effects. It never contains unexplored geometry, trap locations, monster HP, referee-only roll outcomes, or the seed. `Visibility.REFEREE` returns everything, for LLM referees, debugging, and tests.

Full game state, referee-visibility events, and the master seed are server-side secrets: a backend forwards views and player-visible events to clients, never raw state.

## Determinism contract

Determinism is a public API guarantee, not an implementation detail:

- Randomness comes from named streams forked from the master seed by stable string keys (`"combat"`, `"treasure"`, `"wandering"`, ...). Adding draws to one subsystem never shifts results in another, so a spell-rules fix doesn't invalidate treasure golden files.
- Streams are PCG64 generators implemented in `core/rng.py` — not `random.Random`, whose distribution methods aren't guaranteed stable across Python versions. A stream's seed material is `SHA-256(master_seed_bytes + b":" + stream_key_utf8)`, so stream identity depends only on the master seed and the key string. Draw order within a stream is part of the compatibility contract.
- Entity instance IDs (each of the 1d8 trolls) are session-scoped and monotonic (`monster-0007`), assigned in spawn order — never UUIDs, because IDs appear in events and saves.
- Any iteration on an event-emitting path happens in a defined order; sets and other order-unstable structures are banned there.
- Simultaneous effect resolution follows the canonical tick order (see time, resources, and effects).

The exact engine version (package version) is stamped into saves and replays; identical outcomes are guaranteed only under an identical engine version.

## Dice expressions

`dice.py` accepts `NdS` with an optional `+M`/`-M` modifier and an optional `×K` multiplier (`x` and `*` accepted as ASCII aliases): `3d6`, `1d6+1`, `1d4-1`, `2d6×10`. Die sizes S ∈ {2, 3, 4, 6, 8, 10, 12, 20, 100}, `N` defaults to 1 when omitted, and `d%` is an alias for `d100`. Anything else raises `ContentValidationError`. Every roll draws from an explicitly passed RNG stream.

## Time, resources, and effects

The kernel owns time as a first-class concept; the framework decides when it passes.

- `core.clock` defines the units — the round (10 seconds), the turn (10 minutes), the day — and a `GameClock` that advances in those units and drives everything time-dependent.
- `core.effects` is a lifecycle engine for anything with a duration or a periodic tick: spell effects, potion effects, poison and disease, burning light sources, and ongoing monster abilities like troll regeneration. An effect declares its duration, tick behavior, stacking rule, and expiry outcome; as the clock advances, the engine resolves ticks and expirations and emits events (`SpellExpired`, `TorchExpired`, `PoisonTick`).
- Tick order is canonical: at each time-step boundary, expirations resolve first, then periodic ticks; simultaneous effects resolve in attachment order, tie-broken by effect ID. Whether troll regeneration or poison resolves first on the same round is defined, not incidental.
- The engine also owns named conditions, including indefinite and permanent states: paralysed/helpless, asleep, blind, charmed, petrified (recoverable — stone is not dead), diseased, exhausted, lycanthropy incubation, and the averted-eyes stance against gaze attacks (−4 to hit, +2 to be hit by the gazer; mirror counterplay stays `manual`-tagged prose). Conditions expose combat hooks (a helpless target is hit automatically and takes rolled damage; a sleeping creature dies to a blade) rather than being mere labels.
- Effects attach to creatures, items, or locations: a burning oil pool or a *web* occupies cells and ticks on whatever enters or stands in them.
- Consumables deplete through the same mechanism: lighting a torch attaches a 6-turn effect, a lantern flask burns for 24 turns, and rations and water are consumed daily. B/X leaves starvation and thirst to the referee, so consumption is tracked by default while deprivation penalties are a documented adaptation behind a `Ruleset` option.
- The crawl layer advances the clock (movement, searching, and resting cost exploration turns; battles cost rounds). Kernel-only users drive the clock directly.

This split keeps spell durations, poison ticks, and torch timers testable without a dungeon, while guaranteeing the exploration loop and combat rounds share one consistent timeline.

## The adventure model

A session runs an adventure, not a dungeon: the party, clock, active effects, and event log outlive any one location, so the container hierarchy is explicit.

- An **adventure** is one or more dungeons plus a base town and scenario metadata (name, description, hooks). The base town anchors the XP rule's "survive and return to safety" and safe day-level rest — the rules source is the XP-awarding procedure; the SRD's base-town page is a referee checklist, not mechanics. In 1.0 the town is a marker offering the services the SRD names — selling treasure (recovered gp becomes XP) and healing — plus the equipment lists; it is not a simulated town.
- A **dungeon** is one or more **levels** — the SRD's term for the deeper and deeper floors joined by stairs, trapdoors, and chutes. The level number is rules-visible: it keys the wandering encounter tables, scales unguarded treasure, and sets the danger expectation (1 HD monsters on level 1, 2 HD on level 2, and so on).
- A **level** is a grid of 10' cells — the SRD's typical mapping scale, fixed as the cell size here — with wall and door edges.
- A **keyed area** (a room or cave) is a named region over cells, matching the SRD's numbered-area convention. Areas carry content bindings — monsters, treasure, traps, specials, description IDs — and area-oriented procedures (searching, room vs. treasure traps, keyed encounters) resolve against them. Areas annotate the grid; cells remain the single source of spatial truth.

Movement between levels and dungeons happens through commands like any other movement; the session persists across all of it.

## Extension points

The library implements B/X; game-design systems with no SRD basis — quests, achievements, story progression, dialogue — belong to the game. What the library owes them is deterministic, serializable hooks:

- **Event listeners.** Games register listeners on the session; they run in registration order after each command, may emit their own events (appended to the same `CommandResult`), and persist their state through the session's listener-state store, which is snapshotted into saves. Saves containing game-registered listener state are portable only to that game, which owns its migration.
- **Session flags.** A string-keyed, serialized flag store on the session, mutated through commands and readable by dungeon content (a lever in room 3 opens the portcullis in room 12) and game logic alike. Flag changes emit events like everything else.

A quest tracker is the canonical example, not a library feature: subscribe to `MonsterDefeated`, `LocationEntered`, and `ItemAcquired` events, keep objective state, and react by executing ordinary referee-level commands (`AwardXP`, `GrantItem`, `SetFlag`). The Phase 5 example crawler implements its fetch quest exactly this way, in game code, as proof that the extension surface is sufficient.

## Persistence, replay, and versioning

A save file contains the full game state (including registered listener state and session flags), the accepted-command log, the event log, the master seed, the `schema_version`, and the exact engine version.

- A saved game restores from state alone; the logs are records, not dependencies. The event log may therefore be compacted (save = state + optional log tail) without affecting correctness.
- A replay is seed + accepted-command log, and is valid only under the same engine version — any rules change may legitimately alter outcomes, and replaying under a different engine version is an explicit, detectable error rather than silent divergence.
- Replays reproduce engine state exactly with or without listeners, because listeners never mutate game state: their reactions were issued as ordinary commands and are already in the log. Re-registering the same listeners during a replay reproduces their state too.
- A standing test guarantees that `load(save)` and `replay(seed, commands)` produce identical state for every golden scenario.

Schema versioning: saves, commands, and events share a single monotonically increasing integer `schema_version`, independent of the package version.

- Within a version, changes are additive only: new event types and new optional fields. Renames, removals, and semantic changes bump the version.
- Loading an older save runs ordered migrations (chained n → n+1). Loading a save newer than the library understands fails fast with a clear error.
- Consumers must ignore unknown event types and fields, so additive growth never breaks a front end or narrator built against an older schema.
- Session metadata exposes the library's `schema_version`, so a front end can handshake and detect a mismatch it can't tolerate.

## SRD data pipeline

`tools/srd_compile/` parses the markdown files in `srd/` into JSON checked into `osrlib/data/`. The scrape is consistently structured, which makes this tractable:

- **Monsters** (~200 stat blocks): stat-block tables (AC, HD, attacks, THAC0, movement, saves, morale, alignment, XP, number appearing, treasure type) plus bulleted special abilities. Numeric fields are fully parsed (including forms like `6+3*` HD and dual `4 [15]` AC); special abilities are captured as structured tags plus original prose. Breath weapons carry their shape, area, and effect — including non-damage forms like the sea dragon's save-or-die spittle. Counts come from stat blocks, not files: variant monsters (Beetle, Fire; Snake, Pit Viper) live as sections inside combined pages, and every stat block gets its own resolvable ID so encounter tables can reference variants directly.
- **Spells** (~130 Classic): level, class list, duration, range, targeting (self, single, up-to-N, HD budget, area shape and size, gaze), and effect prose; mechanical effects get structured tags where automatable.
- **Classes** (7): requirement tables, full level progression tables (XP, HD, THAC0, five save categories, spell slots), the thief skill progression table (d% skills plus hear noise), and allowed armour/weapons and class abilities.
- **Equipment and magic items**: weapons (cost, weight, damage, qualities, material — silver matters), armour, adventuring gear, mounts, vehicles; magic item generation tables plus per-item mechanics from the individual item pages.
- **Tables**: attack matrix, turning undead, reaction, morale, treasure types, ability modifiers, the language list, encounter tables by dungeon level (whose number-appearing values override the monster description's), dungeon stocking and unguarded-treasure tables by dungeon level, NPC adventurer class/level and alignment tables.

Pipeline rules:

- Output is deterministic and diff-reviewable; regeneration is a one-command `uv run` task, and CI regenerates and fails on any diff so `srd/`, the compiler, and `osrlib/data/` cannot silently drift.
- Bad or ambiguous parses are corrected by patch files in `tools/srd_compile/overrides/`, merged after parsing with provenance recorded in the output (e.g. the dungeon encounter table's "Basic Adventures" typo for Basic Adventurers). `osrlib/data/` is never hand-edited.
- Every generated file validates against the typed models at build time; tests assert entry counts and spot-check known values (e.g. Troll is AC 4 [15], HD 6+3*).
- Where prose can't be mechanized (e.g. referee-judgment abilities), the data keeps the prose and a `manual` tag so games and narrators can still present it.

## Rules scope

The kernel implements B/X procedures rules-as-written:

- Ability scores 3d6, modifiers, prime requisite XP bonuses/penalties; the creation-time adjustment step (lower STR/INT/WIS two-for-one into a prime requisite, floor 9, class restrictions such as thieves keeping STR); ability checks (1d20, equal-or-under succeeds, ±4 easy/hard modifiers; natural 1 succeeds, natural 20 fails — inverted from attack rolls) and STR-based open-doors checks
- Character creation, starting gold and equipment, alignment, languages; optional re-roll of 1–2 starting hp as a `Ruleset` flag
- The attack matrix shipped as data (its clamping differs from plain THAC0 arithmetic, which becomes the `Ruleset` variant); natural 20 always hits, natural 1 always misses, minimum 1 damage on a hit; descending and ascending AC both supported
- Combat round sequence: declaration, side initiative (individual as option), movement, missile, magic, melee; slow weapons act last; weapon qualities executed in resolution — two-handed (no shield), brace and charge doubling, optional reload, and splash weapons: burning oil (must be lit; creatures with fire attacks are immune) and holy water (harms only undead), dousing the target for two rounds as a timed effect; unarmed attacks (1d2)
- Range bands with missile modifiers (+1 short, −1 long, impossible beyond long); melee only at 5' or less; fighting withdrawal (half speed) and retreat (attacker gains +2 and ignores the shield)
- A shared targeting model for spells, breath weapons, and thrown weapons: self; single target; up to N targets, where N may be rolled (*hold person* takes 1d4 of a group) and a mode may carry its own save modifier (its single-target form imposes −2); HD budgets (sleep's 2d8 HD — weakest first, sub-1 HD rounded up to 1, fixed hit-point bonuses dropped); geometric areas (radius, line, cone, cloud); and gaze — per-round exposure to each engaged combatant unless averting eyes. Each affected target resolves its own save when the effect allows one (sleep grants none), with half-damage-on-save carried in the effect's data
- Damage-type and weapon-material resolution: silver-or-magic-only, magic-only, and fire/acid-only immunities as monster tags checked at damage time, with weapon material and enchantment on items; immunities are graded — immune outright, or automatic save against similar attack forms (a red dragon ignores burning oil and auto-saves against *fire ball*); the optional 5+ HD counts-as-magical rule as a `Ruleset` flag
- Saving throws (death, wands, paralysis/petrification, breath, spells)
- Damage, healing, and death: death at 0 hp or less, natural healing (1d3 hp per full day of uninterrupted rest — an interrupted day heals nothing), instantaneous magical healing that combines with natural, and slowed-healing modifiers (mummy curse, disease); falling damage (1d6 per 10')
- Equipment destruction when death comes from a destructive attack (lightning bolt, dragon breath), with magic items saving to survive on the owner's save values plus the item's own combat bonus (the SRD makes this save referee-optional; it defaults on behind a `Ruleset` flag)
- Energy drain: level loss as the symmetric counterpart of leveling — HP, saves, THAC0, and spell slots recomputed, XP set halfway between the old and new levels, and the drained-to-zero terminal state
- Morale (2d6 vs ML at first death and half-side casualties; ML 2 never fights, ML 12 never checks, two passed checks end checking), NPC/monster reaction (2d6, CHA-modified)
- Spell memorization and casting for divine and arcane casters; spell disruption (a declared caster who is hit or fails a save before acting loses the spell as if cast); turning undead
- Thief skills as a percentile subsystem (climb sheer surfaces, find/remove treasure traps, hide in shadows, move silently, open locks, pick pockets; hear noise on d6), plus back-stab, read languages, and scroll use
- Demi-human class abilities: infravision, detection checks (secret doors, construction tricks, room traps), ghoul-paralysis immunity, halfling hiding (2-in-6 in dungeons, 90% outdoors) and missile/AC/initiative bonuses
- Monster category tags consumed by targeting and effects: person (human-like, up to 4+1 HD — the *charm person*/*hold person* domain), undead (turning target, mind-effect immunities, exempt from *sleep*), enchanted. *Sleep* itself is not person-limited: it takes any living creature within its HD bounds
- XP: 1 gp = 1 XP for recovered treasure; monsters by HD plus special-ability asterisk bonuses, where "defeated" includes routed, outsmarted, or captured; awards divided evenly among survivors; a single award raises a character at most one level (excess XP stops 1 shy of the second)
- Encumbrance (none/basic/detailed as `Ruleset` options), movement rates
- Game clock and timed effects: spell durations, poison, disease, potion effects, light sources, conditions
- Treasure generation, magic items, gems and jewellery

The crawl framework implements the dungeon adventuring procedures:

- Exploration turns (10 minutes) and combat rounds (10 seconds) on a shared clock
- Adventure structure: adventures containing multi-level dungeons with keyed areas, and the base town anchoring return-to-safety and safe rest
- Grid dungeon model: 10' square cells with wall/door edges, stairs and level transitions, party position and facing (Bard's Tale first-person convention), explored/visible flags for the front end; movement rates convert from feet (120'/turn exploring = 12 cells)
- Movement rates, light sources and durations (torch 6 turns, lantern 24), rest requirements, rations and water consumption
- Searching for secret doors and traps and listening at doors (X-in-6, race/class-modified); forcing stuck doors on the STR open-doors chance; thief skills plug into the same procedures (open locks, hear noise), preserving the B/X split between room traps (anyone searches) and treasure traps (thief-only find/remove)
- Trap triggering: 2-in-6 chance when the triggering action occurs, damage automatic (no attack roll)
- NPC adventuring parties as encounter-table entries (Basic/Expert Adventurers): generated by the SRD procedure — composition dice, class/level and alignment tables, treasure types U+V shared among the group, and (Expert parties only) a 5%-per-NPC-level chance per suitable magic-item sub-table with unusable rolls discarded — from the same character model as PCs, then handled as an encounter side like any monster group. High-Level party types appear only on wilderness tables and defer with wilderness; employing NPCs (retainers) remains out of scope
- Wandering monster checks with noise, light, and resting modifiers, rolled on the encounter tables keyed by dungeon level; encounter procedure (surprise — denied to a party carrying light in darkness or after a failed door forcing; 2d6×10' encounter distance = 2d6 cells; reaction; parley)
- Combat space as an abstract per-group range track (the Bard's Tale convention) built on the kernel's range bands; party combat ranks derive from marching order, and corridor width caps combatants fighting abreast (2–3 in a 10' passage) — documented adaptations behind `Ruleset` knobs
- Area-of-effect resolution against that combat space: geometric shapes map deterministically to groups and party ranks (how many of a group a 20'-radius *fire ball* catches, which ranks a breath weapon reaches), including friendly fire when an area overlaps a melee — a documented adaptation with `Ruleset` knobs
- Monster actions resolve through a pluggable action policy: the default follows scripted patterns where the SRD defines them (a dragon opens with breath, then breath or melee with equal chance, three breaths per day) and otherwise picks attacks by range; games and LLM referees can substitute a policy per encounter side
- Evasion and pursuit: evasion only before combat begins, speed comparison, pursuit in rounds at running speed, dropped-treasure and food distractions (3-in-6 for intelligent monsters), running exhaustion after 30 rounds (−2 to attacks, damage, and AC until rested 3 turns)
- Battle state machine wrapping kernel combat: declared spells tracked for disruption, morale checks, fleeing and pursuit outcomes, victory/TPK
- XP awarded at adventure end per RAW (survive and return to safety), with an immediate-award `Ruleset` adaptation for continuous CRPG play

Out of scope for 1.0 (tracked for later): wilderness and sea adventuring, strongholds and domain play, hirelings/retainers as full NPCs, magical research, procedural dungeon generation (the SRD stocking tables ship as data; the generator that consumes them comes later), Advanced Fantasy content.

## The Ruleset

`core/ruleset.py` defines the `Ruleset` model: every SRD optional rule and every documented adaptation is a named flag with a default. The 1.0 flag set:

- `variable_weapon_damage` (default on) — SRD optional rule; off means every weapon deals 1d6
- `individual_initiative` (default off) — DEX-modified individual initiative instead of side-based
- `thac0_arithmetic` (default off) — replace the attack-matrix lookup with THAC0 subtraction
- `encumbrance` (default `basic`) — `none` / `basic` / `detailed`
- `weapon_reload` (default off) — SRD optional rule: crossbows fire every other round
- `hp_reroll_at_first_level` (default off) — re-roll starting hp results of 1–2
- `hd5_counts_as_magical` (default off) — SRD optional rule: 5+ HD monsters bypass magic-only immunity
- `magic_item_death_save` (default on) — the referee-optional save for magic items on destructive death
- `deprivation_penalties` (default off) — adaptation: penalties for going without food/water
- `xp_award_timing` (default `on_return`) — `on_return` per RAW, or `immediate` for continuous CRPG play
- `aoe_friendly_fire` (default on) — adaptation knob: areas overlapping a melee catch friends
- `formation_width_limit` (default on) — adaptation knob: corridor width caps combatants fighting abreast

Flags are read at resolution time, so a `Ruleset` is fixed for the life of a session (it participates in saves and replays).

## Technology decisions

- **Python ≥ 3.14**, packaged with `uv`, linted/formatted with `ruff`, tested with `pytest`.
- **Pydantic v2** for all models (commands, events, game state, SRD data). Rationale: validation at the data pipeline boundary, native JSON Schema for the FastAPI/LLM consumers, fast serialization. The kernel stays import-light otherwise.
- **SRD data models are frozen.** Play spawns mutable instances from templates (a per-encounter monster from its stat block), so shared data can never be damaged by combat.
- **`GameSession` is not thread-safe by contract.** Wrappers serialize commands per session; the Phase 6 FastAPI example demonstrates a per-session lock.
- **Hot-path discipline.** À la carte kernel functions return plain values; full event-model construction happens under a session. If profiling shows event construction dominating mass simulations, `model_construct` is the sanctioned fast lane.
- **Synchronous API only.** No async facade in-package; async front ends (FastAPI, etc.) wrap the sync `GameSession`, and example usage will be documented.
- No runtime dependencies beyond pydantic if we can help it.

## Testing strategy

- **Golden-seed scenarios**: scripted command sequences with fixed seeds asserting event streams. Assertions are scoped per RNG stream (enabled by the determinism contract) so a spell change doesn't invalidate treasure goldens, and the comparator applies the schema rule — unknown event types and fields are ignored, so additive changes don't break goldens.
- **Save/replay equivalence**: for every golden scenario, `load(state + logs)` equals `replay(seed, commands)`.
- **Migration round-trips**: a save written at version n, migrated to n+1, equals a natively created n+1 save.
- **Statistical tests**: chi-square checks over large N for treasure types, reaction rolls, and wandering encounters, so generator bugs that preserve types but skew distributions get caught.
- **Property and invariant tests**: dice parser, encumbrance math, combat invariants (HP within bounds, dead creatures don't act), and fuzzed command sequences — schema-valid random commands must never raise, only reject.
- **Data validation**: generated SRD data validates against models; count and spot-check assertions guard the compiler.
- **Table fidelity tests**: attack matrix, saves, turning, thief skills, and progression tables asserted against the SRD values directly.
- **Example game as integration test**: the `examples/` TUI crawler doubles as an end-to-end smoke test.

## Licensing

The OSE SRD is Open Game Content under the Open Game License 1.0a (Necrotic Gnome). The repository must:

- Ship the OGL 1.0a text and a correct Section 15 copyright notice alongside the generated data
- Keep library code under its own license (MIT proposed) with the OGL applying to the SRD-derived data
- Avoid implying endorsement; "Old-School Essentials" is a Necrotic Gnome trademark, so compatibility statements must follow their license terms

## Roadmap

Each phase ends with working, tested, documented code.

**Phase 0 — scaffolding and contracts.** uv project layout, ruff/pytest/CI, OGL licensing files; `dice` (the expression grammar above), `rng` (PCG64 named streams), and `clock` as the first real code. The determinism contract, event-emission rules, and `schema_version`/engine-version stamping are locked in this phase so every later model obeys them from birth.

**Phase 1 — characters.** SRD compiler for classes, ability tables, and equipment; ability scores with the adjustment step, class definitions, character creation, inventory and encumbrance, XP/leveling. Milestone: roll and equip a legal 1st-level party of all seven classes, serialize it with version stamps, level it up.

**Phase 2 — combat kernel.** Monster compiler with variant stat blocks and category/immunity tags; attack matrix as data, damage with weapon-material and graded-immunity resolution, range bands, the targeting model (single, multi, HD budget, area, gaze), initiative, saves, morale, damage/healing/death, energy drain, and the effects engine with the core condition set (poison, paralysis, petrification). Milestone: deterministic scripted battles — a party versus 1d8 trolls with regeneration ticking, and a wight fight that drains a level — resolved entirely through kernel calls with full event streams.

**Phase 3 — magic.** Spell compiler; memorization, casting, disruption, turning undead; spell durations ride the effects engine; the subset of spells with automatable mechanics wired to real effects, the rest exposed as tagged prose. Milestone: cleric and magic-user play through combat using spells, including a disrupted casting, a *sleep* resolved by HD budget, and a *fire ball* resolved per-target against an explicitly supplied target list (geometry-to-target mapping arrives with Phase 4's combat space).

**Phase 4 — the crawl.** Adventure container, multi-level dungeon grid with keyed areas, party, exploration turn loop, light, provisions, and time, searching, trap triggering, wandering monsters with noise/light modifiers (encounter tables re-roll NPC-adventurer entries until Phase 5), encounter procedure, evasion/pursuit/exhaustion, the range-track battle state machine with the default monster action policy, `GameSession` command/event API with views, save/load with migrations. Milestone: a scripted delve — enter from town, explore two dungeon levels, spring a trap, fight, flee with dropped treasure (hand-placed via keyed-area content; generated hoards arrive in Phase 5), rest, return to safety — replayable from seed + commands and restorable from a save.

**Phase 5 — treasure and the loop closed.** Treasure generation, magic items, NPC adventuring parties for the encounter tables (they need treasure types and magic items, hence this phase), end-of-adventure XP awards, the `examples/` TUI crawler. Milestone: a playable minimal dungeon crawl from character creation to leveling up, including a simple fetch quest implemented in the example's own game code on the listener/flags extension surface — proving games don't need library changes for game-design systems.

**Phase 6 — API freeze.** API polish — the last free-rename window: the public-surface census, the naming and signature sweep, the import contract, a typing gate — the FastAPI wrapper example beside the Phase 5 TUI as the second front-end proof, and Advanced Fantasy groundwork validated against the data model. After this phase the public surface is final.

**Phase 7 — documentation.** The docstring overhaul to shippable new-user quality: development-history language and reviewer-directed rationale purged, runnable examples on the entry points and one cross-seam quickstart (character → adventure → session → events → save), every command documenting its modes, rejection codes, and emitted events, named types with cross-references replacing duck-typed `object` prose, and module docstrings that orient. On top of it, the documentation site (mkdocs-material + mkdocstrings, strict builds) with guides and walk-throughs for both example front ends, the command/event JSON Schema reference, and the README rewrite.

**Phase 8 — release.** Release engineering — version 1.0.0, the changelog, packaging audits, the tag-driven trusted-publishing workflow — and publication to PyPI as `osrlib`.
