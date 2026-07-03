# Phase 1 plan — characters

Implementation plan for Phase 1 of [the osrlib spec](spec.md). Phase 1 delivers the first slice of the SRD data pipeline (classes, ability tables, equipment) and the character kernel: ability scores with the creation-time adjustment step, class definitions, character creation, inventory and encumbrance, and XP/leveling. Milestone from the spec: roll and equip a legal 1st-level party of all seven classes, serialize it with version stamps, level it up.

## Scope

In scope:

- `tools/srd_compile/` — the SRD markdown → JSON compiler with its overrides mechanism, plus the CI regeneration diff check (the seam Phase 0 left open)
- `src/osrlib/data/` — generated JSON for classes, ability tables, equipment, and languages, with typed loaders; the OGL text packaged alongside the data (the licensing seam noted in the Phase 0 plan)
- `core/abilities.py` — ability score modifiers, ability checks, open-doors chance, prime requisite rules, the adjustment step
- `core/classes.py` — class definition and progression models, XP awards, leveling up
- `core/items.py` — weapon/armour/gear/ammunition models, inventory, coin purse, encumbrance, movement rates
- `core/character.py` — the PC model, the creation procedure, stamped serialization
- `core/ruleset.py` — the `Ruleset` model, carrying only the flags Phase 1 consumes
- `errors.py` grows `SaveVersionError`; `versioning.py` gains document-stamp helpers
- Tests: table fidelity against the SRD, golden-seed creation and leveling, property tests, data validation

Out of scope (deferred to the phase that consumes them):

- Monster, spell, and treasure compilers and data (Phases 2, 3, 5); magic items, mounts, vehicles (5+)
- Combat, saving-throw *resolution*, energy drain, conditions (2) — progression tables ship save *values* now
- Spell memorization, casting, spell books (3) — progression tables ship spell *slots* now; the magic-user's and elf's starting spell book entry is Phase 3, noted here so character creation's Phase 3 seam is explicit
- Sessions, commands, events, views, full persistence, `crawl/party.py` (4) — the milestone's "party" is a stamped collection of seven characters, not the crawl-layer party model
- Thief skill checks and demihuman detection checks (2/4), turning undead (3) — the class compiler ships the thief skill table and structured ability tags now; the procedures land with their consumers
- Events for character creation: none. Creation is out-of-fiction and pre-session; kernel functions return structured results (including the raw rolls, so front ends can show them). The first real events arrive with Phase 2 combat, per the Phase 0 plan.

## Work items

### 1. SRD compiler — `tools/srd_compile/`

Dev-time only, never shipped; runs as `uv run python -m tools.srd_compile` from the repo root. Stdlib + pydantic only — it imports osrlib models to validate its own output at build time.

- Structure: one parser module per content domain (`classes.py`, `abilities.py`, `equipment.py`, `languages.py`), a small shared pipe-table parser, an overrides loader, and a `__main__.py` that writes all outputs. The SRD's tables are regular enough that a hand-rolled parser beats a markdown dependency.
- Output is deterministic and diff-reviewable: JSON with sorted keys, 2-space indent, `\n` line endings, trailing newline, entries sorted by id, no timestamps. Regenerating from an unchanged `srd/` is byte-identical.
- IDs: lowercase ASCII snake_case derived from the SRD name (`fighter`, `magic_user`, `two_handed_sword`, `rations_iron`). IDs appear in saves and are stable forever once shipped. Purchase lots normalize to the item plus a lot size (`torch` with purchase quantity 6 for 1 gp, `arrows` with quantity 20 for 5 gp) rather than encoding counts in ids.
- Overrides: JSON patch files in `tools/srd_compile/overrides/`, merged after parsing, keyed by output file and entry id; every override carries a `reason`. Overridden entries record the touched field paths in an `overrides_applied` list in the output, per the spec's provenance requirement. First known customers: the multi-prime-requisite XP tiers (elf, halfling), which are prose, not tables, and are hand-authored as overrides rather than brittle prose parsing.
- Parse hazards found during survey, recorded so the implementer doesn't rediscover them:
    - `srd/Weapons.md` and `srd/Armour_and_Shields.md` are the *magic item* pages. Mundane weapon/armour tables live in `srd/Weapons_and_Armour.md`; gear in `srd/Adventuring_Gear.md`; encumbrance and treasure weights in `srd/Time%2C_Weight%2C_Movement.md` (URL-encoded filename).
    - Numbers use commas (`1,050,000`); ranges use en-dashes (`4–5`); prose uses typographic apostrophes, including inside missile range bands (`5’–80’ / 81’–160’ / 161’–240’`).
    - THAC0 and AC are dual-format (`19 [0]`, `7 [12]`) — both values are parsed and shipped.
    - HD entries above name level are flat bonuses with a footnote asterisk (`9d8+2*`) meaning CON modifiers no longer apply; the asterisk is data (a `con_applies` flag), not noise.
    - Progression tables have multi-row spanned headers (`Saving Throws`, `Spells`); the CHA table has a two-row header; class stat blocks are two-column key/value tables.
    - Torch, holy water, and burning oil appear both in the 22-row Weapon Combat Stats table and the 24-row Adventuring Gear list. Pinned: one entry per physical item — they compile as *gear* carrying an embedded combat facet (damage, range bands, qualities), so the weapons list holds the 19 pure weapons and no item ever has two ids.
    - Oddities with decided resolutions: torch cost `1 (for 6)` normalizes to lot size 6; holy water and burning oil weight `-` means no tracked weight (they are gear, covered by the detailed-encumbrance flat 80 coins); sling stones cost `Free` compiles to cost 0, lot size 1; `Stakes (3) and mallet` is one kit item; the ammunition table has no weight column and compiles to weight 0 — the SRD: "The listed weight of missile weapons already includes the weight of the ammunition and its container."

### 2. Generated data, loaders, and OGL packaging — `src/osrlib/data/`

- Output files: `classes.json`, `abilities.json`, `equipment.json`, `languages.json`. Each carries a `_meta` block naming the source SRD pages. Never hand-edited; CI enforces (see work item 8).
- Typed loaders in `osrlib/data/__init__.py`: `load_classes()`, `load_ability_tables()`, `load_equipment()`, `load_languages()`, each reading package resources via `importlib.resources`, validating into frozen models, and caching (`functools.cache`). Validation failure raises `ContentValidationError` — generated data that doesn't validate is malformed content, exactly what the typed hierarchy is for.
- Frozen-data contract: everything the loaders return is a frozen pydantic model (spec: play spawns mutable instances from templates; templates are immutable).
- Licensing seam from Phase 0, now due: the OGL 1.0a text and Section 15 notice ship inside the package next to the data (`src/osrlib/data/LICENSE-OGL.md`), so the built wheel carries the license alongside the Open Game Content. Section 15 gains the osrlib entry now — this is our first distribution of compiled Open Game Content. The repo-root `LICENSE-OGL.md` stays for the `srd/` scrape.

### 3. Ruleset — `core/ruleset.py`

- Frozen pydantic `Ruleset` model, `extra="forbid"` so a typo'd flag errors instead of silently doing nothing. Participates in saves later; additive flag growth is schema-legal.
- Phase 1 defines only the flags Phase 1 reads: `hp_reroll_at_first_level` (default off) and `encumbrance` (`none` / `basic` / `detailed`, default `basic`). The spec's remaining 1.0 flags are added by the phases that implement their behavior — shipping a flag whose behavior doesn't exist yet would be a lie in the API.

### 4. Abilities — `core/abilities.py`

- Score domain: integers 3–18 (the SRD's stated range; also the adjustment-step raise cap).
- Modifier lookups backed by `abilities.json`, one accessor per SRD column: melee, open doors (X-in-6), spoken languages, literacy, magic saves, AC, missile, initiative, hit points, NPC reactions, retainer max/loyalty (data completeness; retainers themselves are out of 1.0 scope), and the prime requisite XP table.
- Ability checks per the SRD: roll 1d20 ≤ score succeeds; caller-supplied modifier (±4 easy/hard); natural 1 always succeeds, natural 20 always fails — inverted from attack rolls, called out in the docstring. Open-doors check: d6 ≤ the STR-derived chance. Both take an explicit `RngStream`, like everything random.
- The adjustment step, as pure validation + application over a rolled score set and a chosen class:
    - Only STR, INT, WIS may be lowered; only scores that are not prime requisites of the chosen class may be lowered; class-specific restrictions apply (thief: may not lower STR — carried as data on the class definition).
    - Each lowered score must drop by an even amount; total raise = sum of reductions ÷ 2, distributed freely among the class's prime requisites.
    - No score below 9; no score above 18. Applied atomically: one adjustment object validated as a whole, then applied.

### 5. Classes — `core/classes.py`

- `ClassDefinition` (frozen): id, name, race implied by class (spec: Advanced-ready — `race` is a field, populated from class in Classic play), requirements (minimum scores), prime requisites, XP-modifier tiers, hit die, maximum level, armour policy (any / leather only / none; shields allowed flag), weapon policy (any / explicit allow list / forbidden list — cleric is the explicit five blunt weapons; dwarf and halfling forbid `long_bow` and `two_handed_sword`, with both classes' stature prose — the dwarf's "small or normal sized", the halfling's "appropriate to stature" referee judgment — kept as `manual`-tagged notes), native languages, adjustment restrictions, structured class-ability tags plus prose (infravision 60', 2-in-6 detection checks, ghoul-paralysis immunity, halfling AC/missile/initiative bonuses, back-stab, read languages, scroll use — tags now, procedures in Phases 2/4), the thief skill table, and level titles (non-mechanical flavor, cheap to carry).
- Progression rows: level, XP threshold, HD formula (count, die, flat bonus, `con_applies`), THAC0 and attack bonus (both, dual-format from the SRD), the five save values, spell slots by spell level.
- XP-modifier tiers: a uniform representation for all classes — ordered tiers of `{modifier_pct, minimum scores}`, evaluated best-first, first tier whose conditions all hold wins. Single-prime-requisite classes get the standard table expressed the same way (+10% at PR ≥ 16, +5% at ≥ 13, none at ≥ 9, −10% at ≥ 6, −20% at ≥ 3 — the penalty rows only work under first-match-wins, hence the pinned evaluation rule); elf and halfling get exactly their stated tiers, which per RAW carry no penalties (the SRD applies the standard table "to characters with a single prime requisite" and the multi-PR class descriptions note only bonuses) — interpretation pinned here and in a test.
- XP awards: `apply_xp(character, award)` applies the class XP-modifier percentage (result floored — rounding pinned), then the one-level-per-award rule exactly as written: XP that would reach two or more levels above the starting level is clamped to 1 XP below the second level's threshold, and the character gains one level.
- Leveling up: new max HP = old + (new hit die roll + CON modifier, minimum 1 per die) while HD count still grows, or + the flat-bonus delta (no roll, no CON) above name level. Saves, THAC0, and spell slots are read from the progression row, never stored derivations. Level is capped at the class maximum; XP keeps accumulating past it. Recompute-from-level is a pure function so Phase 2's energy drain is its inverse, not a redesign.
- Demihuman requirements enforced at class choice: dwarf CON ≥ 9, elf INT ≥ 9, halfling CON ≥ 9 and DEX ≥ 9.

### 6. Items and encumbrance — `core/items.py`

- Frozen templates from `equipment.json`: `WeaponTemplate` (cost, weight in coins, damage as a dice expression, qualities, missile range bands in feet, material — `standard` / `silver`, extensible), `ArmourTemplate` (descending and ascending AC, cost, weight, basic-encumbrance category light/heavy), `GearTemplate` (cost, container capacity where the SRD gives one, purchase lot size, and an optional combat facet — damage, range bands, qualities — for torch, holy water, and burning oil), ammunition. Mutable `ItemInstance`s spawn from templates and carry quantity.
- Inventory: ordered container of item instances plus a coin purse by denomination (pp/gp/ep/sp/cp, conversion rates from the SRD's Wealth page); coins weigh 1 each. Equipped state: worn armour, shield, wielded weapon(s).
- Legality checks as pure validators: purchase (sufficient funds), equip (class armour/weapon policy). Class weapon policies govern the weapons list only; gear combat facets (torch, holy water, burning oil) are exempt — a strict quality-tag reading would forbid a cleric holy water or a torch, which is absurd. One uniform rule, pinned here and tested: a cleric may buy, hold, and use all three.
- Encumbrance per the `Ruleset` flag:
    - `none`: movement is always the 120' (40') default; nothing is tracked and no load cap applies.
    - `basic`: treasure weight — coins included — is still tracked; the SRD's max-load rule is general, not a detailed-mode extra ("The weight of treasure carried is tracked to make sure that the character's maximum load is not exceeded"). Equipment weight is not tracked. The movement-rate row is chosen by armour category (unarmoured/light/heavy) and whether the character is carrying significant treasure — that part is a referee judgment in RAW, so it stays one: `carrying_treasure` is a plain boolean on the character that the game sets. No invented threshold, no new flag.
    - `detailed`: movement from total weight — treasure, weapons, armour by listed weight, plus a flat 80 coins when any miscellaneous gear is carried (the SRD gives gear no per-item weights; pinned interpretation). Thresholds 400/600/800/1,600 are inclusive ("up to").
    - In both tracking modes, tracked weight above the 1,600-coin maximum load means the character cannot move (movement 0); inventory itself is never capped.
    - Encounter movement rate is base ÷ 3, computed not stored. Treasure weights (coin, gem, jewellery, potion, scroll, ...) ship in `equipment.json` now; treasure itself arrives in Phase 5.

### 7. Character model and creation — `core/character.py`

- `Character` (mutable pydantic model): optional `id` (default `None` — entity IDs are session-scoped and assigned when sessions exist in Phase 4), name, class id, race, level, XP, final ability scores, alignment (`lawful` / `neutral` / `chaotic` str enum, wire values pinned), chosen extra languages, max/current HP, inventory, equipped state, `carrying_treasure`. Spells and conditions are Phase 2/3 additive fields. Derived properties, never stored: modifiers, AC (armour base 9 unarmoured, shield −1, DEX; descending and ascending both exposed), movement rates, literacy, languages (class natives + alignment tongue derived from alignment so it can never desync + INT-granted choices). `languages.json` holds Common plus the twenty Other Languages — 21 ids referenced by class natives and choices; alignment tongues are not data entries, they derive from the alignment enum. INT-granted extras must come from the Other Languages table and may not duplicate a class native.
- Model validation is structural only (score ranges, level within class bounds, HP ≥ 0). Procedure legality — was the adjustment legal, were requirements met — is enforced by the creation functions at the time of the step; a finished character cannot re-derive its own history.
- Creation is pure functions the game drives stepwise (sans-I/O: the game owns prompting and choice), mirroring the SRD's 13 steps where they're mechanical: `roll_ability_scores(stream)` (3d6 per score, drawn in SRD order STR INT WIS DEX CON CHA — draw order pinned), class choice validation, the adjustment step (work item 4), `roll_hit_points(class, con_mod, ruleset, stream)` (max of die + CON mod and 1; with `hp_reroll_at_first_level` on, re-roll while the raw die shows 1–2, each re-roll consuming a draw — pinned interpretation of "re-rolling 1s and 2s"), alignment and language choices, `roll_starting_gold(stream)` (3d6×10 gp, dogfooding the dice grammar), equipment purchase against the compiled price lists. A `create_character(...)` convenience takes all decisions upfront for scripts and tests and calls the same steps in SRD order.
- Validation failures return structured reasons from the pure validators (code + params, dotted snake_case codes like `creation.class.requirements_not_met`) — shaped so Phase 4 command rejections can carry them verbatim. Calling an apply-step with input its validator rejects is programmer misuse and raises stdlib `ValueError`, per the Phase 0 errors convention.
- RNG stream keys pinned as module-level conventions (sessions adopt them in Phase 4): `"character_creation"` for creation draws (scores, HP at level 1, starting gold), `"advancement"` for level-up HP rolls — separated so a creation-rules change never shifts in-play advancement draws in a golden scenario.

### 8. Document stamps, errors, CI

- `versioning.py` gains the stamped-document helpers persistence will later build on: `stamp_document(kind, payload)` wrapping a payload with `schema_version`, `engine_version`, and `kind`; `check_document(doc)` raising on unknown kinds and on `schema_version` newer than the library understands. `errors.py` grows `SaveVersionError` for the latter, per the spec's hierarchy.
- `Character` (and the milestone's party-as-collection) round-trips through these: `to_document()` / `from_document()` satisfy "serialize it with version stamps" without pre-building Phase 4 persistence.
- CI regeneration check, slotted into the workflow beside the existing jobs as Phase 0 planned: run the compiler, then fail on any drift — including brand-new untracked outputs, which plain `git diff` misses — by requiring `git status --porcelain -- src/osrlib/data` to be empty. README gains the one-command regeneration instruction.
- `docs/adaptations.md` is created with its first content: a pinned-interpretations register — each Phase 1 interpretation (multi-PR tiers carry no penalties, HP re-roll until ≥ 3, gear combat-facet exemption from class weapon policy, misc-gear flat 80 coins, ammunition weight 0, XP flooring, adjustment evenness) recorded with the SRD ambiguity and the chosen reading, so narrators and reviewers have one place to look instead of grepping docstrings. Documented adaptations proper (`Ruleset`-flagged deviations) join the same file in later phases.

### 9. Tests

- **Data pipeline**: compiler output is byte-identical across two runs; every generated file validates through the loaders; entry counts asserted (7 classes, 19 weapons, 4 armour rows, 24 gear items of which exactly 3 carry combat facets, 4 ammunition entries, 21 languages); spot checks on known values (fighter level 9 = 240,000 XP, THAC0 14 [+5]; plate mail AC 3 [16], 500 coins; elf languages include Gnoll; silver dagger material). Override provenance appears in output for overridden entries.
- **Table fidelity**: full progression tables for all seven classes asserted against the SRD pages directly — XP thresholds, HD formulas and `con_applies`, THAC0/attack bonus, all five saves, spell slots; the thief skill table; all six ability modifier tables including the CHA two-row header and prime requisite tiers.
- **Abilities**: adjustment-step acceptance/rejection table (two-for-one arithmetic, floor 9, cap 18, prime-requisite-only raises, STR/INT/WIS-only lowers, thief STR restriction, elf lowering WIS only); ability check natural 1/20 inversion; open-doors chances per STR band.
- **Creation goldens**: fixed seed → all seven classes created with canonical purchases → serialized documents match golden files (assertions scoped to the `character_creation` stream); HP reroll flag on/off draw-count accounting; demihuman requirement rejections; illegal purchases and equips rejected with structured reasons (magic-user in plate, cleric with sword, insufficient funds); a cleric buying and using holy water and a torch succeeds.
- **Leveling**: award crossing one threshold levels up once with correct HP/saves/THAC0/slot changes; award that would cross two clamps to threshold − 1 XP; prime requisite ±% applied and floored; demihuman level caps hold (halfling stops at 8); name-level flat HP bonuses ignore CON.
- **Property tests** (hypothesis): encumbrance math — total weight is the sum of parts, movement is monotonically non-increasing in carried weight, boundary values land on the SRD thresholds, and the 1,600-coin cap immobilizes under both tracking modes; purse arithmetic never goes negative and conversions round-trip; any legal creation input sequence yields a structurally valid character; random illegal inputs are rejected, never raise from validators.
- **Documents**: stamp round-trip; loading a document with `schema_version` + 1 raises `SaveVersionError`; unknown extra fields in a document are ignored (additive-schema contract).

## Sequencing

1. Data models for classes, ability tables, and equipment (frozen pydantic, in their `core/` homes) — the compiler validates against them, so models come first.
2. Compiler, overrides, generated data, loaders, OGL-in-package, CI regeneration check — data green before logic consumes it.
3. `core/ruleset.py`, then `core/abilities.py` (modifiers, checks, adjustment step).
4. `core/items.py` (inventory, purse, encumbrance, legality validators).
5. `core/classes.py` logic (XP awards, leveling).
6. `core/character.py` (model, creation steps, documents) with `versioning.py`/`errors.py` additions.
7. Golden milestone test — the seven-class party — plus the remaining suites; README data-pipeline note; final pass verifying every spec contract above is implemented or explicitly deferred with its phase noted.

## Definition of done

- `uv sync && uv run ruff format --check && uv run ruff check && uv run pytest` passes locally and in CI on both OSes, including the new SRD regeneration diff job.
- The milestone runs as a test: from one master seed, roll and equip a legal 1st-level party of all seven classes, serialize with schema and engine version stamps, reload, and level a character up — deterministic across runs and platforms.
- All seven class progression tables, six ability tables, and the equipment lists are asserted against the SRD verbatim; compiled data ships with the OGL text and updated Section 15 inside the package.
- Every pinned interpretation in this plan (multi-PR tiers carry no penalties, HP re-roll repeats until ≥ 3, gear combat-facet exemption, misc-gear flat 80 coins, ammunition weight 0, XP flooring, adjustment evenness) is recorded in the `docs/adaptations.md` interpretations register, stated in a docstring, and locked by a test.
