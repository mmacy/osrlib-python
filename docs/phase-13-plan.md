# Phase 13 plan — journal and lifecycle commands

Implementation plan for phase 13 of [the osrlib spec](spec.md): the engine substrate for authored triggers and quests, with no interpreter — the optional `source` field on the base command model, session-state blocks for trigger fired-marks and the journal, the `MarkTriggerFired`, `AddJournalEntry`, and `RecordNote` referee commands with their events, the journal in the player view, and persistence of the new blocks. In the dependency chain (10 → 11 → 14 → 15, with 12 and 13 order-independent before their consumers), this phase must land before phase 14, whose interpreter issues these commands and reads this state; nothing in this phase consumes them itself. The milestone: **save/load and replay rebuild journal and fired-marks exactly, with no listeners registered, and `source` survives the command log.**

Five facts shape the design:

- **The command surface is the `SetFlag` pattern applied three times.** Each lifecycle command is pure session bookkeeping: no RNG draws, no clock time, no rejections, and a handler of a few lines beside `_handle_set_flag` at the bottom of `session.py`. All three inherit the base `_ALL_MODES` legality — they are legal in both terminal modes because none resumes play (they mutate bookkeeping, not the world), which is also load-bearing forward: phase 15's rewards land after the victory transition, and the interpreter's marks and notes must land beside them. The census machinery absorbs them mechanically: `REFEREE_COMMANDS` gains three members, `RESUME_PLAY_CARVE_OUTS` gains none, and the docs gates enforce the three-section docstrings.
- **`source` is one field on one base class, and a four-golden regeneration.** `Command` gains `source: str | None = None`; every subclass inherits it, no handler reads it, and `parse_command` and the frozen-model contract need no change. Because `session_state` serializes commands with `model_dump(mode="json")`, every stored golden whose payload carries a command log — `phase4_delve`, `phase5_milestone`, `phase11_gates`, `phase12_wipe`; the kernel goldens carry none — gains a `"source": null` key per logged command and regenerates, a mechanical pure-additive diff explained in the commit message per the standing golden rule (the phase 11 `narrative` precedent). "Ignored by execution" is pinned by test, not prose: two sessions executing the same command stamped and unstamped reach identical state except the log's own `source` values.
- **Replay must accept every logged mark, so re-marking is idempotent.** Under phase 14's semantics a repeatable trigger issues `MarkTriggerFired` before each firing, and a replay re-executes every logged command — so marking an already-marked trigger must be accepted, not rejected. The pin: a re-mark appends nothing (fired-marks answer "has this trigger ever fired", and nothing consumes a count) but still emits `TriggerFiredEvent` — the event is the log's record of *each* firing, state is the record of the first. Fired-marks are a `list[str]` in first-fired order: ordered and deterministic where a set is banned, and membership at authored-trigger scale needs no index.
- **The journal is state, never derived, and the entry model is the view.** `JournalEntry` is a frozen model beside `DeathRecord` in `session.py`, and `session.journal` is the appended list the spec names. An entry carries its `text` and the clock position (`rounds`) at append — pinned here because append-time is capturable only at append: the view API promises front ends never replay the event log, and a compacted save sheds the log entirely, so a journal without its own timestamps would strand "when" unrecoverable. The player view ships the entries verbatim (`tuple[JournalEntry, ...]`), exactly the spec's word — a mirror view-model would be a second home for a shape that is already safe by construction. No import cycle: `session.py` reaches `views.py` lazily inside `view()`, so `views.py` importing `JournalEntry` at module level is clean.
- **Visibility and persistence follow the flag precedent exactly.** `JournalEntryAddedEvent` is player-visible carrying the authored text — content data in a structured field, the spec's explicit carve-out, with message codes intact; `TriggerFiredEvent` and `NoteRecordedEvent` are referee-visibility, as `FlagSetEvent` is, because marks and notes are content wiring. The player-view whitelist admits the `journal` block and nothing else new — fired-marks never appear, pinned by the leak test. Persistence is two new payload keys (`fired_triggers`, `journal`) loaded with empty defaults: additive within `schema_version` 3, no bump, no migration; a pre-phase save loads with an empty journal and starts remembering, and an older engine loading a newer save drops the unknown keys — the documented additive-risk posture.

## Scope

In scope:

- `source: str | None` on the base `Command`, with the planned regeneration of the four goldens that store command logs
- `MarkTriggerFired`, `AddJournalEntry`, and `RecordNote` in `crawl/commands.py`, with handlers in `crawl/session.py`
- `TriggerFiredEvent`, `JournalEntryAddedEvent`, and `NoteRecordedEvent` in `crawl/events.py`, registered and templated
- `JournalEntry` and the `fired_triggers`/`journal` session-state blocks, persisted in `persistence.py`
- `PlayerView.journal`
- The phase golden (`tests/goldens/phase13_journal.json`), `tests/test_journal_lifecycle.py`, docs, and the changelog

Out of scope (deferred to the phase that picks each up):

- **Trigger specs, the interpreter listener, document-order matching, the cascade depth bound, and dropped-consequence/truncation recording** — phase 14. `RecordNote` ships the recording *mechanism*; phase 14 ships its uses. Nothing in the library issues any of the three commands this phase — they are the documented referee surface, issuable by game code today, and shipping them unissued is the roadmap's explicit instruction ("the engine substrate with no interpreter"), not dead accommodation code.
- **`validate_adventure` trigger reference checks** — phase 14, with the trigger spec they resolve against.
- **Quest lifecycle commands, quest state, active quests in the player view, and journal appends at quest beats** — phase 15, which reuses this phase's journal block unchanged.
- **In-library stamping of `source`** — the interpreter (phase 14) and quest rewards (phase 15) are the stampers; this phase pins only that the field survives the log.
- **Validation of `trigger_id` against anything** — pinned open: no trigger spec exists to resolve against, and the open-domain posture is `SetFlag`'s key precedent. Phase 14 decides whether the command tightens when specs exist.
- **New rejection codes** — none anywhere in the phase: all three commands are `_ALL_MODES` referee commands (`wrong_mode` cannot fire) with total handlers, so `Rejections: None` like `SetFlag`, and `tools/docs/rejection_codes.json` is untouched.
- **A `Ruleset` flag and an adaptations entry** — nothing here reads the SRD: B/X has no journal, no triggers, and no session bookkeeping, so every pin is a spec-design decision and the register's silence is the phase 11/12 precedent.

## Work items

### 1. `source` on the base command — `crawl/commands.py`

- `source: str | None = Field(default=None, min_length=1)` on `Command`, beneath `command_type`. The attribute docstring states the contract in the present tense: an annotation naming the authored object (trigger or quest id) or game system on whose behalf the command was issued; execution never reads it; it is logged and replayed with the command so the log alone answers "why did this happen". `min_length=1` makes the empty string unrepresentable — absent is `None`, never `""`.
- No subclass changes: the field inherits everywhere, `extra="ignore"` and frozen are untouched, and the discriminated union parses it wherever it appears. The command JSON Schema reference regenerates automatically (`tools/docs/gen_schema_reference.py` walks `ALL_COMMAND_CLASSES`).
- The golden consequence, planned: regenerate exactly `phase4_delve.json`, `phase5_milestone.json`, `phase11_gates.json`, and `phase12_wipe.json` — the four whose payloads store command logs — with the added `"source": null` keys as the entire diff, explained in the commit message. The creation, RNG-vector, and kernel battle goldens store no commands and do not change.

### 2. The three commands — `crawl/commands.py`

- `MarkTriggerFired`: `command_type: Literal["mark_trigger_fired"]`, `trigger_id: str` (min length 1). Docstring: records that an authored trigger has fired, before its consequences issue; fired-state answers once-only semantics and survives save, load, and replay; marking an already-marked trigger is accepted, changes nothing, and still emits the event — each mark in the log is one firing. Modes: all six. Rejections: none. Events: `TriggerFiredEvent`.
- `AddJournalEntry`: `command_type: Literal["add_journal_entry"]`, `text: str` (min length 1). Docstring: appends an authored beat to the session journal — the voice of a trigger or quest moment; entries append in order of discovery and are never rewritten or derived. Modes: all six. Rejections: none. Events: `JournalEntryAddedEvent`.
- `RecordNote`: `command_type: Literal["record_note"]`, `text: str` (min length 1). Docstring: records a referee-visibility annotation in the logs with no state effect — the mechanism for machine-issued records (a dropped consequence, a truncated cascade) and freeform referee margin notes alike. Modes: all six. Rejections: none. Events: `NoteRecordedEvent`.
- All three append to `ALL_COMMAND_CLASSES` after `RollDice` (the stable wire order grows at the tail) and join `__all__`. Docstring language stays present-tense contract — the decision-log vocabulary gate reads every docstring, so no "will arrive in a later phase" phrasing; the interpreter relationship is stated as "the surface a library-shipped trigger/quest interpreter or a game's own listener drives", matching the module docstring's existing framing.

### 3. The three events — `crawl/events.py`, `messages.py`

- `TriggerFiredEvent`: `event_type: Literal["trigger_fired"]`, code `session.trigger.fired`, `visibility: REFEREE`, field `trigger_id: str`. Referee-visibility by spec: trigger internals are content wiring, the `FlagSetEvent` posture.
- `JournalEntryAddedEvent`: `event_type: Literal["journal_entry_added"]`, code `session.journal.entry_added`, `visibility: PLAYER`, fields `text: str` and `rounds: int` — the appended entry, whole. Player-visible authored text is content data in a structured field (the spec's carve-out); the code is still the machine surface.
- `NoteRecordedEvent`: `event_type: Literal["note_recorded"]`, code `session.note.recorded`, `visibility: REFEREE`, field `text: str`.
- The `session.` namespace is pinned deliberately: these are session-lifecycle bookkeeping beside `session.flag.set`, not adjudication rolls — `adjudication.` stays the dice namespace.
- All three register in `CRAWL_EVENT_CLASSES` (tail order, like the commands) and `messages.py` gains three templates (`Trigger {trigger_id} fired.`, `Journal: {text}`, `Referee note: {text}` — exact strings settled in implementation); the message-codes reference regenerates from the registry.

### 4. Session state and handlers — `crawl/session.py`

- `JournalEntry`, frozen, beside `DeathRecord`: `text: str` (min length 1), `rounds: int` (ge 0) — the clock position when the entry landed. Joins `__all__`; the docstring states the append-only contract and why the stamp lives on the entry (a view renders "when" without the event log).
- `GameSession.__init__` gains `self.fired_triggers: list[str] = []` and `self.journal: list[JournalEntry] = []`, documented with the other engine-owned state in the class docstring.
- Handlers at the module bottom, registered in `_handlers()`:
    - `_handle_mark_trigger_fired`: append `trigger_id` to `fired_triggers` only if absent; return `[TriggerFiredEvent(trigger_id=...)]` unconditionally.
    - `_handle_add_journal_entry`: build `JournalEntry(text=..., rounds=session.clock.rounds)`, append, return `[JournalEntryAddedEvent(text=..., rounds=...)]`.
    - `_handle_record_note`: return `[NoteRecordedEvent(text=...)]` — no mutation at all.
- No draws, no time, no interaction with the wipe check (no deaths in these events) — the handlers are total, so the command fuzzer can never make them raise.

### 5. Persistence — `persistence.py`

- `session_state` gains `"fired_triggers": list(session.fired_triggers)` and `"journal": [entry.model_dump(mode="json") for entry in session.journal]`; the module docstring's save-contents enumeration gains both blocks. The referee view carries them for free (it is `session_state` minus RNG and seed).
- `load_game` restores both with empty-default `get`s — `payload.get("fired_triggers", [])`, `payload.get("journal", [])` — so every pre-phase save loads unchanged. No migration, no `SCHEMA_VERSION` bump: new payload keys with defaults are the additive case the versioning rules name. Replay needs no code at all — the handlers rebuild both blocks by re-execution, which is the milestone.

### 6. The journal in the player view — `crawl/views.py`

- `PlayerView.journal: tuple[JournalEntry, ...]`, built as `tuple(session.journal)`; `views.py` imports `JournalEntry` from `crawl.session` (cycle-free, argued in the facts). The module docstring's whitelist enumeration gains the journal.
- What does not join the view, pinned by test: `fired_triggers` (trigger wiring), note texts (referee-visibility events, and the view never carries events anyway). The referee view's coverage of both blocks comes with work item 5.

### 7. Docs and spec impacts — applied with the implementation PR

- **`docs/spec.md` needs zero edits**: the interpreter-and-command-log and journal sections state this phase's whole contract in the present tense, and the roadmap entry exists. The `rounds` stamp on entries is below spec altitude — an implementation pin recorded here and in the `JournalEntry` docstring.
- **`docs/adaptations.md` gains no entries** — no SRD text is touched or reinterpreted anywhere in the phase; the silence is deliberate and precedented.
- **`docs/guides/sessions-commands-events.md`**: the referee-command paragraph gains the three lifecycle commands and the `source` field — the log now carries who acted *and on whose behalf*. **`docs/guides/views-and-visibility.md`**: the player-view whitelist gains the journal; the visibility examples gain the journal-entry/trigger-fired split. **`docs/guides/listeners-and-flags.md`**: the listener pattern's command vocabulary gains the three commands — a quest-tracking listener can mark, journal, and annotate through the log today, which is exactly how the library-shipped interpreter will behave when it arrives (guide prose, not docstrings, so the forward reference is fine there).
- **`CHANGELOG.md`** `[Unreleased]`, one Added bullet covering the phase: the `source` annotation on every command, the three lifecycle commands with their events and visibility, the journal and fired-marks as engine session state, the journal in the player view, and the additive persistence contract. No release work item — the bullet rides `[Unreleased]` per the cadence.

### 8. Tests — `tests/test_journal_lifecycle.py` (new) and the goldens

- **Command semantics**: a mark appends once and in order; a re-mark leaves `fired_triggers` unchanged and still emits; journal entries append in order with the correct `rounds` stamps (one entry before and one after an `AdvanceTime` shows different stamps); `RecordNote` leaves every state block equal (full `session_state` comparison minus the two logs); empty `text`/`trigger_id`/`source` rejected at construction.
- **Legality**: all three accepted in `game_over` and `victory` (direct mode assignment, the phase 12 posture); `REFEREE_COMMANDS` gains the three members and the resume-play census test passes with no new carve-outs; `sample_command` in `test_commands.py` and the fuzzer strategies in `test_crawl_properties.py` gain the three shapes.
- **Visibility**: `TriggerFiredEvent` and `NoteRecordedEvent` referee, `JournalEntryAddedEvent` player, asserted at the executed seam through a player-visibility filter.
- **`source`**: a stamped command round-trips through `save_game`/`load_game` with the stamp intact; execution-ignorance pinned via the twin-session state-equality test from the facts; an unstamped pre-phase logged command parses with `source=None`.
- **Views**: the whitelist test gains `journal`; the leak pins — the serialized player view contains no `fired_triggers` key and no note text after a session that used all three commands.
- **Persistence**: round-trip with populated blocks; a pre-phase save fixture (payload without the new keys) loads with empty blocks; `load(save)` equals `replay(seed, commands)` with both blocks populated — the standing equivalence guarantee now covering them.
- **The golden** — `tests/generate_phase13_goldens.py`, `tests/goldens/phase13_journal.json`, `tests/test_phase13_goldens.py`, on the phase 12 pattern (the test imports the generator's build/run). A small delve scripted the way phase 14's interpreter will drive it, with no listeners registered: a lever-pull stands in as `SetFlag` stamped `source="trigger:lever-east"`, preceded by its `MarkTriggerFired` and followed by its `AddJournalEntry`; a second mark of the same trigger id later in the run pins idempotence in the stored artifact; a `RecordNote` records a referee margin; a `GrantItem` stamped with a quest-shaped source shows the "why did the party get this" answer sitting in the log. Asserts: final `fired_triggers` and `journal` exact (stamps included), replay of the accepted log reaches identical state, `load(save)` equals `replay(seed, commands)`, and the reloaded command log carries every `source` verbatim — the milestone, verbatim.
- The full gate green: `uv sync && uv run ruff format --check && uv run ruff check && uv run pyright && uv run pytest && uv run mkdocs build --strict`.

## Sequencing

1. Work item 1 (`source` on the base command) with its tests and the four golden regenerations — the wire shape settles first, in one commit whose message explains the diff.
2. Work items 2–4 (commands, events, session state, handlers) with the semantics, legality, and visibility tests.
3. Work items 5 and 6 (persistence, the player view) with the round-trip, pre-phase-save, and leak tests.
4. Work items 7 and 8 remainder (docs sweep, changelog, the phase golden; the full gate on both OSes).

## Definition of done

- `uv sync && uv run ruff format --check && uv run ruff check && uv run pyright && uv run pytest && uv run mkdocs build --strict` green on both OSes.
- The milestone runs in the phase golden: with no listeners registered, save/load and replay rebuild `fired_triggers` and the journal exactly, and every `source` stamp survives the command log through save, load, and replay.
- No `SCHEMA_VERSION` bump and no migration; pre-phase saves load with empty blocks; the only changes to existing goldens are the four planned `"source": null` regenerations, explained in their commit message.
- The three commands clear every census and docs gate — referee legality with no new carve-outs, three-section docstrings with documented modes equal to `allowed_modes`, registered events with templates — and `rejection_codes.json` is untouched.
- The player view ships the journal verbatim and leaks neither fired-marks nor notes, pinned by test; trigger-fired and note events are referee-visibility, journal-entry events player-visible.
- No spec edit and no adaptations entry lands — both silences argued in this plan.
