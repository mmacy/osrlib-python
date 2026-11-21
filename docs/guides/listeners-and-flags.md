# Listeners and flags

[`GameSession`][osrlib.crawl.session.GameSession] resolves B/X movement, combat, exploration, and spellcasting rules, while leaving game-specific mechanics (such as custom quest trackers, puzzle switches, or achievement counters) to your application. You extend session behavior through two mechanisms: **listeners**, which inspect events after each accepted command and execute follow-up commands, and **flags**, which store referee-only key-value pairs in `session.flags`.

## Register a listener and react to events

Implement the [`Listener`][osrlib.crawl.session.Listener] protocol by defining a unique `key` string and a `handle(events, state)` method, then register an instance with [`GameSession.register_listener`][osrlib.crawl.session.GameSession.register_listener]:

```{.python .no-run}
class MoveCounter:
    key = "move_counter"

    def handle(self, events: Sequence[Event], state: dict) -> tuple[list[Event], dict]:
        state = dict(state)
        moved = sum(1 for event in events if isinstance(event, PartyMovedEvent))
        state["moves"] = state.get("moves", 0) + moved
        return [], state


session.register_listener(MoveCounter())
```

[`GameSession.execute`][osrlib.crawl.session.GameSession.execute] invokes registered listeners in registration order after accepting, applying, and logging a command. Rejected commands never invoke listeners. On each call, `handle` receives two arguments:

- `events`: the events produced so far for the current command, starting with the command handler's events and followed by any events returned directly by earlier-registered listeners.
- `state`: the JSON-compatible dict stored under `listener.key` in `session.listener_state` (initialized to `{}` on registration). `GameSession` stores this dict without interpreting its contents.

`handle` returns `(emitted_events, new_state)`. Follow the pattern that matches whether your listener calls `session.execute`:

- **Observer listeners** (such as `MoveCounter` above, which never call `session.execute`): may construct and return a new `state` dict, and may return standalone annotation events in `emitted_events`.
- **Command-issuing listeners** (listeners that react by calling `session.execute(...)`): calling `session.execute(...)` inside `handle` re-enters the full dispatch loop, running all registered listeners (including the current listener) against the nested command's events with the same `state` dict stored under `listener.key`. Mutate `state` **in place** *before* calling `execute` and return that same `state` dict so inner calls observe your guard flag and don't trigger a loop (returning a different dict instance after a nested call raises `RuntimeError` so `execute` never overwrites inner updates). Return `[]` for `emitted_events`, because nested `execute` calls already append their events to `session.event_log` and the outer `execute` call collects them into `CommandResult.events` in log order without duplicates.

### Persist and re-register listeners across saves

`register_listener` checks `session.listeners` and raises `ValueError` if an active listener instance with the same `key` is already registered on that session. [`save_game`][osrlib.persistence.save_game] and [`load_game`][osrlib.persistence.load_game] serialize and restore `session.listener_state` as JSON data while leaving `session.listeners` empty on the loaded session. Calling `register_listener` again in the same order after `load_game` succeeds and reconnects each listener to its restored `listener_state[listener.key]` dict (see [Determinism, saves, and replay](determinism-saves-replay.md)).

## Example: track a custom fetch quest

When a quest belongs to a specific adventure, author it as a [`QuestSpec`][osrlib.crawl.quests.QuestSpec] and let [`Interpreter`][osrlib.crawl.interpreter.Interpreter] run it (see [Gates, triggers, and quests](gates-triggers-quests.md#authoring-a-quest) and [The TUI crawler](../front-ends/tui-crawler.md)). When you want game-wide logic that lives in code rather than adventure data, write a custom listener that tracks progress in `state` and applies rewards through referee commands:

```{.python .no-run}
class FetchQuestListener:
    """Recover an item and bring it home - a quest tracker as a listener."""

    key = "fetch_quest"

    def __init__(self, session) -> None:
        self._session = session

    def _carrier(self):
        for member in self._session.party.members:
            if member.inventory.carried_item("jade-idol") is not None:
                return member
        return None

    def handle(self, events: Sequence[Event], state: dict) -> tuple[list[Event], dict]:
        acquired = any(isinstance(event, ItemAcquiredEvent) for event in events)
        if acquired and not state.get("recovered") and self._carrier() is not None:
            state["recovered"] = True
        home = any(isinstance(event, LocationEnteredEvent) and event.location_kind == "town" for event in events)
        if home and state.get("recovered") and not state.get("completed") and self._carrier() is not None:
            state["completed"] = True
            self._session.execute(SetFlag(key="quest.idol", value="recovered"))
            for member in self._session.party.living_members():
                self._session.execute(AwardXP(character_id=member.id, amount=1200))
        return [], state
```

Notice how `FetchQuestListener` follows the re-entrancy and determinism rules:

- It re-checks `self._carrier() is not None` when the party enters `town`, so dropping the idol in the dungeon and returning empty-handed does not complete the quest.
- It mutates `state` in place and sets `state["completed"] = True` *before* calling `self._session.execute(...)`, preventing the nested `SetFlag` and `AwardXP` calls from triggering the completion block again.
- It returns `[], state` because `self._session.execute(...)` already logs the `SetFlag` and `AwardXP` events.
- It modifies party XP and flags exclusively through commands (`SetFlag` and `AwardXP`) rather than mutating `session.party` or `session.flags` directly, ensuring saves, loads, and replays stay consistent.

## Store referee-only state in flags

Use [`SetFlag`][osrlib.crawl.commands.SetFlag] to store a `str`, `int`, or `bool` value under a string key in `session.flags` (for example, recording that the party pulled a lever):

```{.python .no-run}
session.execute(SetFlag(key="crypt.lever_pulled", value=True))
```

`SetFlag` is legal in every `SessionMode`, updates `session.flags`, and emits [`FlagSetEvent`][osrlib.crawl.events.FlagSetEvent]. Flags persist in saves and rebuild during replays, but remain hidden from [`PlayerView`][osrlib.crawl.views.PlayerView] (see [Views and visibility](views-and-visibility.md)). Read flag values from `session.flags` directly or from `session.view(Visibility.REFEREE).flags`.

## Lifecycle commands: fired-marks, the journal, and notes

Alongside `SetFlag`, listeners and authored content use referee lifecycle commands that work in every mode, record to `command_log`, and rebuild state automatically during [`replay_game`][osrlib.persistence.replay_game] even though replay runs without listeners:

- [`MarkTriggerFired`][osrlib.crawl.commands.MarkTriggerFired]: appends `trigger_id` to `session.fired_triggers` so once-only triggers don't fire again. Marking an already-recorded ID leaves `session.fired_triggers` unchanged (preserving first-fired order) while still emitting [`TriggerFiredEvent`][osrlib.crawl.events.TriggerFiredEvent] for repeatable triggers.
- [`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry]: appends a player-visible entry with the current clock round to `session.journal`, exposes it on `PlayerView.journal`, and emits [`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent].
- [`RecordNote`][osrlib.crawl.commands.RecordNote]: logs a referee-only [`NoteRecordedEvent`][osrlib.crawl.events.NoteRecordedEvent] without changing session state, useful for referee annotations or recording skipped trigger consequences.

Pass `source` on commands issued by a listener (such as `source="trigger:crypt.lever"`) to record which trigger, quest, or subsystem caused each log entry (see [Sessions, commands, and events](sessions-commands-events.md)).

## Run authored triggers and quests with `Interpreter`

Register [`Interpreter`][osrlib.crawl.interpreter.Interpreter] after creating a session (and again after `load_game`) to evaluate an adventure's authored [triggers](gates-triggers-quests.md#scripting-the-dungeon-with-triggers) and [`QuestSpec`][osrlib.crawl.quests.QuestSpec] definitions:

```{.python .no-run}
session.register_listener(Interpreter(session))
```

`Interpreter` implements the same `Listener` protocol as custom listeners:

- **Returns `[], state`**: Every state change goes through referee commands stamped `source="trigger:{id}"` or `source="quest:{id}"`.
- **Stores nothing in `listener_state`**: `session.listener_state["osrlib.interpreter"]` remains `{}` because trigger marks, quest progress, and journal entries live in engine-managed fields (`fired_triggers`, `quests`, and `journal`) that replay rebuilds from `command_log`.
- **Marks before acting**: `Interpreter` executes `MarkTriggerFired` before running a trigger's consequences so nested events cannot re-fire a once-only trigger, and enforces a maximum reaction depth to halt loops (see [When a consequence fails](gates-triggers-quests.md#when-a-consequence-fails)).

## The complete program

```python
from collections.abc import Sequence

from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.events import Event, Visibility
from osrlib.core.items import GearTemplate
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import (
    AddJournalEntry,
    AwardXP,
    EnterDungeon,
    GrantItem,
    MarkTriggerFired,
    MoveParty,
    RecordNote,
    SetFlag,
    TravelToTown,
)
from osrlib.crawl.dungeon import Direction, DungeonSpec, Edge, EdgeKind, LevelSpec
from osrlib.crawl.events import ItemAcquiredEvent, LocationEnteredEvent, PartyMovedEvent
from osrlib.crawl.interpreter import Interpreter
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession


class MoveCounter:
    """Counts accepted party moves into its listener_state, keyed "move_counter"."""

    key = "move_counter"

    def handle(self, events: Sequence[Event], state: dict) -> tuple[list[Event], dict]:
        state = dict(state)
        moved = sum(1 for event in events if isinstance(event, PartyMovedEvent))
        state["moves"] = state.get("moves", 0) + moved
        return [], state


class FetchQuestListener:
    """Recover an item and bring it home — a quest tracker as a listener."""

    key = "fetch_quest"

    def __init__(self, session) -> None:
        self._session = session

    def _carrier(self):
        for member in self._session.party.members:
            if member.inventory.carried_item("jade-idol") is not None:
                return member
        return None

    def handle(self, events: Sequence[Event], state: dict) -> tuple[list[Event], dict]:
        acquired = any(isinstance(event, ItemAcquiredEvent) for event in events)
        if acquired and not state.get("recovered") and self._carrier() is not None:
            state["recovered"] = True
        home = any(isinstance(event, LocationEnteredEvent) and event.location_kind == "town" for event in events)
        if home and state.get("recovered") and not state.get("completed") and self._carrier() is not None:
            state["completed"] = True
            self._session.execute(SetFlag(key="quest.idol", value="recovered"))
            for member in self._session.party.living_members():
                self._session.execute(AwardXP(character_id=member.id, amount=1200))
        return [], state


# The quickstart's one-corridor crypt, plus the idol the fetch quest wants: a
# bundled item, so acquiring it reports a catalog id the listener can look for.
crypt = DungeonSpec(
    id="crypt",
    name="The Old Crypt",
    levels=(LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)}),),
)
town = TownSpec(name="Threshold", travel_turns={"crypt": 1})
adventure = Adventure(
    name="A First Delve",
    town=town,
    dungeons=(crypt,),
    items=(GearTemplate(id="jade-idol", name="Jade idol", cost_gp=0),),
)

rules = Ruleset()
creation = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
fighter = create_character(name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=rules, stream=creation)
party = Party(members=[fighter.character])

session = GameSession.new(party, adventure, seed=7)
session.register_listener(MoveCounter())
session.register_listener(FetchQuestListener(session))
session.register_listener(Interpreter(session))

session.execute(EnterDungeon(dungeon_id="crypt"))
session.execute(MoveParty(direction=Direction.EAST))
blocked = session.execute(MoveParty(direction=Direction.EAST))  # the corridor ends here
assert not blocked.accepted  # a rejection never reaches a listener
session.execute(MoveParty(direction=Direction.WEST))

# The listener's own state survived three commands, one of them rejected.
assert session.listener_state["move_counter"] == {"moves": 2}

# Flags are plain session state: referee-only, set by command, read directly.
assert session.flags == {}
session.execute(SetFlag(key="crypt.lever_pulled", value=True))
assert session.flags == {"crypt.lever_pulled": True}

# A front end working from views alone reads flags off the referee view instead.
referee_view = session.view(Visibility.REFEREE)
assert referee_view.flags == {"crypt.lever_pulled": True}

# The lifecycle vocabulary: mark the trigger, write the beat, annotate the margin. The
# source stamp says on whose behalf each command was issued.
session.execute(MarkTriggerFired(trigger_id="crypt.lever", source="trigger:crypt.lever"))
session.execute(AddJournalEntry(text="The lever grinds.", source="trigger:crypt.lever"))
session.execute(MarkTriggerFired(trigger_id="crypt.lever", source="trigger:crypt.lever"))
session.execute(RecordNote(text="The portcullis consequence had nothing to open."))

# A re-mark appends nothing; the journal is player-visible state, the marks are not.
assert session.fired_triggers == ["crypt.lever"]
assert [entry.text for entry in session.journal] == ["The lever grinds."]
assert session.view(Visibility.PLAYER).journal == tuple(session.journal)
assert session.command_log[-1].source is None  # the note was the referee's own

# The fetch quest, end to end: the idol lands in a pack, and the walk home
# completes the errand — the flag and the XP both landing as ordinary commands.
hero = session.party.members[0]
granted = session.execute(GrantItem(character_id=hero.id, item_id="jade-idol"))
assert granted.accepted
assert session.listener_state["fetch_quest"] == {"recovered": True}

home = session.execute(TravelToTown())
assert home.accepted
assert session.listener_state["fetch_quest"] == {"recovered": True, "completed": True}
assert session.flags["quest.idol"] == "recovered"
assert hero.xp > 0  # the award applied, prime-requisite modifier and all
```

## Where next

- [The TUI crawler](../front-ends/tui-crawler.md) - an adventure-authored fetch quest alongside a custom wandering-monster table in a two-level barrow.
- [Ruleset options](ruleset-options.md) - engine rule toggles on `Ruleset`, as distinct from game-defined `session.flags`.
- [Determinism, saves, and replay](determinism-saves-replay.md) - how `save_game`, `load_game`, and `replay_game` handle `listener_state`.
- [Views and visibility](views-and-visibility.md) - why `RefereeView` exposes flags while `PlayerView` omits them.
