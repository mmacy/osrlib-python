# Phase 9 plan — adventure-bundled monsters

Implementation plan for phase 9 of [the osrlib spec](spec.md): an `Adventure` document carries its own `MonsterTemplate`s, and the engine resolves them everywhere it resolves template ids. This is the consumer-demonstrated need phase 6 pinned as the reopening condition — its out-of-scope entry deferred a content-injection surface "post-1.0 if a consumer ever demonstrates the need," and osr-forge's phase 7 (custom monster emission: bespoke printed creatures converted to playable templates instead of flagged stand-ins) is that demonstration. Document-carried data is not a runtime registration API, so the phase 6 boundary stands unmoved: no games registering classes with a live session, no registration surface — an adventure *document* gains a content field, exactly as adventures already carry dungeons and towns. The milestone: **a bundled custom template is validated, spawned, fought, persisted, and replayed through an unmodified consumer call, released to PyPI as 1.2.0.** The roadmap entry is a spec impact applied with the implementation PR.

Three facts shape the design:

- **Every engine site that resolves a template id already has the session in scope.** Command handlers dispatch as `handler(self, command)`; `wandering_check` and `_keyed_encounter_check` receive `session` explicitly. Of the five bare `load_monsters()` engine call sites, the four resolution sites (`GameSession.spawn`, the `SpawnMonsters` existence check, keyed-encounter resolution, listen checks) call the module-level loader by habit, not necessity — four one-line redirects, not a threading project — while the fifth, validation in `GameSession.new`, keeps passing the base catalog by contract (validation unions internally, work item 3).
- **Downstream of spawn, nothing changes by construction.** `MonsterInstance` embeds its full template; combat, morale, XP, treasure, persistence, and replay carry custom monsters with zero further work. And no golden embeds a serialized `Adventure` — the transcript goldens record command/event logs with the adventure built in code — so the additive field re-blesses nothing.
- **The consumer contract is already pinned on the other side.** osr-forge's merged phase 7 plan pins `validate_adventure` keeping its `(adventure, monsters, equipment)` signature and unioning internally, so its existing call `validate_adventure(adventure, load_monsters(), load_equipment())` is correct unchanged. This plan honors that contract; the internals are this repo's to shape.

## Scope

In scope:

- `Adventure.monsters: tuple[MonsterTemplate, ...] = ()` — bundled custom templates carried by the document
- The effective catalog: base `load_monsters()` ∪ `adventure.monsters`, held by `GameSession`, resolving at every engine site upstream of spawn
- `validate_adventure` bundled checks: bundled-id uniqueness, base-catalog collision, keyed references and alignment pins against the union
- Inline wandering-table `monster_ids` validation against the union — closing a deferred play-time `ValueError` surface that predates this phase
- Docs and spec impacts: the adventure-model section, roadmap entry 9, the authoring guide's monsters section
- Release 1.2.0 — an additive, semver-minor public-surface change under the phase 8 discipline

Out of scope (deferred to the phase or track that picks each up):

- **A runtime content-registration API** — rejected, pinned: the phase 6 boundary stands; content arrives as document data or compiled data, never live registration. This phase is the document path.
- **Bundled equipment, classes, or spells** — the same document-carried shape, deferred until a consumer demonstrates the need exactly as monsters just did; the seam this phase builds is the template to copy, and whichever future phase picks one up copies it.
- **Catalog shadowing or override semantics** — rejected, pinned: a bundled id colliding with a base id is a validation error, never an override. Silently re-statting `orc` would change every keyed reference, wandering row, and alias resolution that names it; an adventure that wants a variant names a variant id.
- **Bundled ids in compiled encounter tables** — the shipped level-band tables reference shipped ids by construction; an adventure that wants bundled monsters wandering supplies an inline `WanderingSpec.table`, which this phase validates. Compiling adventure content into band tables is a design question for a phase with evidence it matters.

## Work items

### 1. The field — `crawl/adventure.py`

- `Adventure.monsters: tuple[MonsterTemplate, ...] = ()`, docstring naming the contract: bundled templates join the shipped catalog for this adventure's sessions; ids must not collide with the shipped catalog; an empty tuple is the universal default. `MonsterTemplate` already imports cleanly (`adventure.py` imports `MonsterCatalog` today; core never imports crawl, and this direction is crawl → core — the layering invariant holds).
- The schema consequence, pinned: additive and defaulted, so every existing save and document loads unchanged with the default applied — no `SCHEMA_VERSION` bump under the stated additive rule, no migration entry. New saves serialize `"monsters": []` inside the adventure block; no golden or pinned-bytes test embeds an adventure, so nothing re-blesses (verified against `tests/goldens/` and `test_persistence.py` — the phase 4/5 transcript goldens carry command and event logs, not adventure documents).
- No `__all__` change and no `test_public_surface.py` edit: a field is a class attribute, not a module-level definition.

### 2. The effective catalog — `crawl/session.py`

- `GameSession` builds the union once in `__init__` via work item 3's `_effective_monsters` helper, stores it as `self._monster_catalog`, and exposes it as a read-only `effective_monsters` property — named after the field it derives from (`Adventure.monsters`), leaving `effective_equipment` and siblings free for whichever future phase copies the seam, and distinct from both the helper's name and the existing `self.monsters` instance dict. Building in `__init__`, pinned over lazy: `load_game` restores sessions without re-running `validate_adventure`, and a doctored save with a colliding bundled id must fail at restore with a typed `ContentValidationError`, not at first spawn with a bare `ValueError` — `__init__` raises it when the helper reports collisions, naming the colliding ids.
- The redirects, each one line: `GameSession.spawn`'s `load_monsters().get(template_id)` → `self.effective_monsters.get(template_id)`; the `SpawnMonsters` handler's existence check → `session.effective_monsters.get(...)`; keyed-encounter resolution in `_keyed_encounter_check` → `session.effective_monsters.get(keyed.template_id)`; the listen check's `load_monsters()` → `session.effective_monsters`. The wandering spawn path needs no edit — its rows funnel through `session.spawn`, which now resolves the union.
- `GameSession.new` keeps calling `validate_adventure(adventure, load_monsters(), load_equipment())` — the base catalog, unchanged, because validation unions internally (work item 3). The consumer call shape is therefore identical inside and outside this repo.
- `effective_monsters` is a new public property on an existing class: no new top-level symbol, no `__all__` edit.

### 3. Validation — `crawl/adventure.py`

- One private module-level helper, `_effective_monsters(adventure, base) -> tuple[MonsterCatalog, tuple[str, ...]]`, shared by validation and the session. It always returns a usable catalog plus the colliding ids: the catalog is the first-occurrence-wins dedup union (base templates, then bundled templates whose ids collide with neither the base nor an earlier bundled entry), built by filtering *before* construction — `MonsterCatalog`'s own uniqueness validator raises a bare `ValueError`, the wrong failure shape for content problems — and the second element lists every skipped colliding id (empty means clean). An empty bundle returns `(base, ())` — the base catalog object itself, the `@cache`d singleton: no copy, no behavior change for every adventure that bundles nothing. Returning a catalog even on collision is the point: both callers get a total answer, and each turns a non-empty collision list into its own typed failure.
- `validate_adventure` keeps its exact signature; `monsters` remains the *base* catalog. It calls `_effective_monsters` first and folds each colliding id into the accumulated error list ("bundled monster id {id!r} collides with the catalog" — one line per id, through the standard "adventure validation failed" report, one gate, one failure shape). Every remaining check resolves against the returned dedup union — pinned so the failure path is total: a keyed reference to a colliding id resolves to the first occurrence and reports nothing extra, so the error report names the root cause (the collisions) without a spurious "unknown monster" echo per reference. Keyed-encounter template ids and alignment pins against `template.alignment.options` both resolve the union — a bundled template with alignment options validates its pins exactly like a shipped one.
- New check, closing the pre-existing gap: for each level whose `wandering.table` is set, every `MonsterEncounterEntry.monster_ids` entry resolves against the union (`NpcPartyEncounterEntry` rows are untouched). Today those ids are validated nowhere and fail at play time with a raw `ValueError` from `catalog.get` — a deferred failure surface that predates this phase, recorded here because bundled monsters in wandering tables are exactly what a converted module will want.
- `replay_game` re-validates by construction (it rebuilds through `GameSession.new`), so replayed adventures with bundled monsters pass the same gate; `load_game` continues to trust saved content, with the effective-catalog construction in `__init__` as its typed failure backstop.

### 4. Docs and spec impacts — applied with the implementation PR

- **`docs/spec.md` § The adventure model**: keyed areas' content bindings sentence gains the bundled-templates clause — an adventure may carry custom `MonsterTemplate`s that join the shipped catalog for its sessions, colliding ids rejected. **§ SRD data pipeline / frozen-data invariant**: one sentence noting bundled templates are adventure data, not SRD data — the shipped catalog stays frozen and generated. **§ Roadmap** gains entry 9: *Phase 9 — adventure-bundled monsters.* Adventure documents carry custom monster templates, resolved everywhere the engine resolves template ids; released as 1.2.0.
- **The id-domain docstring sweep** — the redirects change what a template id is, and the shipped docstrings must not go stale: `GameSession.spawn`'s "a monster template id from `load_monsters`", `KeyedMonster`'s "`template_id` is any id from `load_monsters`", `MonsterEncounterEntry`'s "monster ids from `load_monsters`", and `validate_adventure`'s `monsters` Args line all update to name the effective domain — any id in the session's effective catalog, shipped or bundled (validation's line: the *base* catalog, unioned internally with the adventure's bundle).
- **`docs/guides/authoring-custom-content.md`** retitles to "Authoring custom classes, spells, and monsters" (nav label follows) and gains the monsters section: building a `MonsterTemplate` with `model_validate`, the table helpers for derivation (`thac0_for_hd`, `monster_save_band_label`, `monster_xp`), bundling via `Adventure.monsters`, the collision rule, and the note that the generated monsters index documents the shipped catalog only — bundled ids live in the adventure that carries them.
- **`CHANGELOG.md`** `[Unreleased]` gains the Added bullet, plus a Changed bullet for the validation tightening: an adventure whose inline wandering table names a dangling monster id — previously accepted by `validate_adventure` and left to crash at play time — now fails the gate up front. Observable, deliberate, and visible in the record.
- **`docs/adaptations.md` gains no entries** — this phase pins engineering seams, not rules readings; recorded so the register's silence is deliberate.

### 5. Tests — `tests/test_adventure_monsters.py` (new), following the one-file-per-area convention

- The field: default round-trips; a bundled template survives `save_game` → `load_game` and `session_state` equality; a version-2 save without the key loads with the default.
- Validation: duplicate bundled ids and a base-catalog collision each report `ContentValidationError` through the standard gate (never a bare `ValueError`), with every colliding id named in the message; an adventure carrying a collision *plus* otherwise-valid keyed references reports exactly the collision lines — no spurious "unknown monster" echo (the dedup-union failure path, pinned); a keyed encounter referencing a bundled id validates; a dangling bundled-ish id still fails; an alignment pin inside and outside a bundled template's options; inline wandering rows with a bundled id validate and with a dangling id fail; `NpcPartyEncounterEntry` rows pass untouched.
- The engine: a keyed encounter spawns a bundled template through arrival processing; `SpawnMonsters` spawns one by id; a listen check reads a bundled template's categories; a wandering row naming a bundled id spawns through `session.spawn`; XP award and treasure generation read the embedded template (downstream-unchanged, pinned by one end-to-end fight).
- Persistence and replay: a session with spawned bundled monsters saves, loads, and replays to equal state; a doctored save whose adventure carries a colliding id fails `load_game` with `ContentValidationError`.
- The effective catalog: an empty bundle returns the cached base catalog object (identity assertion); a non-empty bundle resolves both base and bundled ids through `effective_monsters`; the property is stable across calls.
- The full gate green: `uv sync && uv run ruff format --check && uv run ruff check && uv run pyright && uv run pytest && uv run mkdocs build --strict`.

### 6. Release — 1.2.0

- Additive public surface under the phase 8 discipline: semver-minor. Bump `pyproject.toml` to 1.2.0, `uv lock` (lockfile diff touches only the `osrlib` entry), rename `[Unreleased]` with the date, add compare links; local dry run (`uv build`, `check_dist.py`, wheel smoke in a fresh venv); tag `v1.2.0` on the merge commit and let `release.yml` carry it to PyPI.
- The versioned-docs adoption trigger, evaluated and pinned: the trigger's substance is divergence — published docs that must describe behavior *different from* `main` — and a release cut from `main` creates none; the 1.1.0 precedent (a behavior-adding minor shipped with no mike adoption) read it the same way. The "first minor or major with behavior or API changes" gloss is rebutted, not read past: under that letter no cut could ever be the non-firing case, which would make the trigger a dead sentence. Not fired; recorded here so the evaluation is visible.
- Goldens keep their stamps: nothing in this phase regenerates a golden, and a version bump alone must not (the phase 8 rule).

## Sequencing

1. Work item 1 (the field) with its persistence tests — the document shape lands first and everything else consumes it.
2. Work item 3 (validation) with the `_effective_monsters` helper — the gate exists before any engine path can reach a bundled id.
3. Work item 2 (the session catalog and redirects) with the engine tests — spawn, keyed, wandering, listen, replay.
4. Work items 4 and 5 remainder (docs, spec impacts, changelog; the full gate on both OSes).
5. Work item 6 (release) after merge, per the phase 8 checklist.

## Definition of done

- `uv sync && uv run ruff format --check && uv run ruff check && uv run pyright && uv run pytest && uv run mkdocs build --strict` green on both OSes.
- A bundled custom template is validated, spawned through every engine path (keyed, command, wandering), fought to XP and treasure, persisted, and replayed — pinned by the phase's tests, with the consumer call `validate_adventure(adventure, load_monsters(), load_equipment())` unchanged.
- Colliding and dangling bundled ids fail the standard validation gate with `ContentValidationError`; the doctored-save path fails typed at `load_game`.
- No `SCHEMA_VERSION` bump, no golden regeneration, no `__all__` change; `test_public_surface.py` passes unedited.
- Spec impacts (adventure model, frozen-data note, roadmap entry 9), the retitled authoring guide with its monsters section, and the changelog bullets land with the implementation PR; every deferred item above names its pickup.
- Version 1.2.0 is tagged, published to PyPI by `release.yml`, and installable — the release checklist run and recorded.
