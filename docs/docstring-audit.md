# Docstring audit — input to the Phase 7 plan

> **Executed in Phase 7.** This audit was the charter for the Phase 7 docstring overhaul, which is now complete: the development-history vocabulary is purged (a mechanical tripwire enforces it), the entry points carry runnable examples, every command documents its modes, rejection codes, and emitted events, the duck-typed combatant parameters are named with cross-references, the id parameters link their loaders and the generated content-id indexes, and the module docstrings orient. The published site renders the result. This document is kept as the record of the pre-overhaul state.

A shippability audit of every public docstring in `src/osrlib/`, conducted 2026-07-04 on the post-Phase-5 codebase (branch `phase-6-plan`), wearing one hat: "do these look like shippable docstrings appropriate for new library users to effectively use the library?" Three independent passes — the `core/` kernel, the `crawl/` layer plus top-level modules, and a blind new-user test that attempted five tasks from docstrings alone. This document is evidence for the Phase 7 plan, not the plan; the Phase 7 planning pass should verify counts and line references against the code it ships against, since Phase 6's rename sweep will move some of them.

## Verdict

The reference layer is strong: Args/Returns/Raises sections are consistently present and accurate across the package, the mkdocstrings cross-reference syntax is used correctly where it is used, and the determinism, visibility, and error-boundary semantics are documented with real precision. The audience is the problem. The prose is written for this repo's reviewers — decision-log vocabulary, phase references, design rationale — not for a developer who just installed the package. Triage across both sweeps: roughly 12–15% of public docstrings need a full rewrite, ~60% need a targeted touch-up, and ~25% are fine as-is.

The blind new-user test sharpened it: of five basic tasks, only three (à la carte dice, rendering events, save/load) were achievable from docstrings alone. "Create a fighter with gear" fails on undefined jargon (`purchases`, "lots") and undiscoverable ids; "start a session and move the party" fails hard — hand-assembling an `Adventure` from ~10 nested frozen models has no worked example anywhere, and the load-bearing fact that a fresh session starts in `TOWN` and `MoveParty` is rejected until `EnterDungeon` flips the mode exists only in an undocumented handler.

## Systemic problems, ranked

1. **Development-history leakage (near-universal).** ~272 occurrences of "pinned", ~97 of "Phase N", plus "registered", "census", "seam", "precedent", "survey" in user-facing docstrings. Examples: "the Phase 5 wired census" (`items.py`), "the Phase 1 creation seam closed" (`character.py`), "what the kernel has been promising since Phase 2" (`session.py`), "exactly the accommodation the project bans" (`versioning.py`'s `SCHEMA_VERSION`). Worst concentrations: `combat.py` (62), `spells.py` (58), `items.py` (27), `session.py`, `commands.py`, `ruleset.py` (the `Ruleset` attributes block is the single most user-hostile section).

2. **Reviewer-directed rationale in module docstrings.** Import-cycle reasoning (`alignment.py`'s module docstring is 100% import-graph justification; `spells.py` and `tables.py` carry paragraphs of it), decision defenses, and compilation provenance ("compiled from `Awarding_XP.md`") where a newcomer needs orientation — what's here, what to call first.

3. **No runnable examples.** Two `Examples:` blocks exist in the entire package (`GameClock`, `RngStreams`) — both container classes. Zero entry-point functions have one: not `create_character`, `resolve_attack`, `cast_spell`, `generate_treasure`, `GameSession.execute`, `format_message`, `save_game`/`load_game`. Not one example crosses a module boundary. Nothing in any docstring points at `examples/tui_crawler/`, which demonstrates the whole golden path.

4. **The command surface doesn't serve front-end developers.** No command documents its rejection codes (~106 distinct codes ship package-wide — 73 in `crawl/`, 36 in `core/` — with no table anywhere), its emitted events (nothing says `MoveParty` yields `PartyMovedEvent`), or its required session mode in prose (`allowed_modes` is a bare ClassVar). Roughly 30 command handler functions in `exploration.py` carry no docstring at all.

5. **Duck-typed `object` parameters on flagship functions.** ~23 in `combat.py`, ~39 in `spells.py`, ~18 in `effects.py`: `resolve_attack(attacker: object, ...)` renders as `object` in generated docs, prose says "the attacking combatant" without naming or linking `Character`/`MonsterInstance`, and no combatant protocol or union type exists to link to.

6. **Ids and concepts without a path to values.** `class_id`, `item_id`, `template_id` parameters never cross-reference the loaders that enumerate valid values; "purchase lot" is never defined; the twelve RNG stream-key conventions are scattered as `*_STREAM` constants across eight modules with no canonical list; internal repo files (`docs/adaptations.md`, `srd/*.md`) are cited as authority a PyPI user doesn't have.

## What's already good (keep, don't churn)

`dice.py`, `clock.py`, `abilities.py`, `errors.py`, `data/__init__.py`, `party.py`, and the `dungeon.py` geometry orientation are at or near shippable. The Args/Returns/Raises scaffolding package-wide can largely stand — this overhaul is prose, examples, and linkage, not structure.

## Highest-leverage fixes (the Phase 7 charter's spine)

1. One end-to-end runnable quickstart crossing every seam — character → party → adventure → session → `execute` → events → `format_message` → save/load — in the top-level package docstring and README, cross-linked from the entry points it touches.
2. Kill the adventure-construction wall: a worked minimal-adventure example (a two-cell corridor with its `edges`/`entrance` assembly) where `Adventure`/`DungeonSpec`/`LevelSpec` are documented, and the `TOWN → EnterDungeon → EXPLORING` prerequisite stated on the commands it gates.
3. Purge the development-history vocabulary and reviewer rationale from every public docstring; rationale that matters to maintainers moves to comments or design docs.
4. Commands document modes, rejection codes, and emitted events; events document when they fire and enumerate their closed value sets (stances, outcomes); a central rejection-code reference backs it.
5. Name the duck types: a documented combatant/caster protocol (or explicit unions) so `attacker: object` becomes a linkable, honest type in the generated reference, and id-typed parameters cross-reference their loaders.
