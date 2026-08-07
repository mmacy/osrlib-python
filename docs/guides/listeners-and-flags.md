# Listeners and flags

Command handlers implement the SRD's rules: movement, combat, searching, spellcasting, and
everything else a [`GameSession`][osrlib.crawl.session.GameSession] resolves on its own. They
don't know what a fetch quest is, what a lever in a guard room does, or what your game's win
condition looks like — that logic belongs to your game, not the engine. Two small mechanisms
carry it without forking the library: **listeners**, which watch every command's events and
react by executing more commands, and **flags**, a small piece of session state your game reads
and writes directly.

This page works through both mechanics as the code implements them, then works a fetch quest as a
game-owned listener — the example both mechanisms exist for, and the shape the library's own
interpreter takes when the quest is adventure data instead. [The complete program](#the-complete-program)
at the end is a self-contained, runnable illustration you can read start to finish.

## Listeners: reacting to committed events

A listener satisfies the [`Listener`][osrlib.crawl.session.Listener] protocol: a `key` string
that names its slot in the session's state, and a `handle` method with this shape:

```{.python .no-run}
def handle(self, events: Sequence[Event], state: dict) -> tuple[list[Event], dict]:
    ...
```

Register an instance with [`GameSession.register_listener`][osrlib.crawl.session.GameSession.register_listener]:

```{.python .no-run}
session.register_listener(MoveCounter())
```

[`GameSession.execute`][osrlib.crawl.session.GameSession.execute] runs every registered listener,
in registration order, immediately after a command is accepted, applied, and logged — a rejected
command never reaches a listener at all, since rejection mutates nothing and appends nothing to
the log. Each listener's `handle` receives two things:

- `events` — the accumulated events for that one command: the command handler's own events, plus
  whatever any earlier-registered listener already returned. A listener registered second sees a
  first listener's authored events alongside the handler's.
- `state` — that listener's own return value from the last time `handle` ran, or `{}` the first
  time (and after a fresh registration). The session never inspects this dict; it's the
  listener's private bookkeeping.

`handle` returns a pair: a list of events to append to the command's result and to the session's
event log, and the state to keep for next time.

That returned-events list is for events a listener **authors** directly — a listener that reacts
by executing its own commands must return an empty list. A nested `session.execute(...)` call
already appends that command's events to the session's event log itself; returning them again
from `handle` would log the same event twice.

Returning nothing costs the caller nothing. `execute` notes where the event log ends before it
calls each listener and folds everything logged while that listener ran into the result it hands
back — the nested commands' events, however deeply they nest, each exactly once and in log order,
followed by whatever the listener authored. So the `CommandResult` from a player's `MoveParty`
carries the portcullis grinding open and the journal entry that recorded it, and a front end
renders the whole chain from one envelope.

The nested-`execute` call matters for a second reason: it re-enters the entire dispatch pipeline,
listener loop included. If a listener issues a command from inside `handle`, every registered
listener — itself included — runs again against *that* command's events, with whatever `state`
happens to be stored in `session.listener_state` at that moment. Critically, the outer `handle`
call's own state update hasn't landed yet: `execute` only writes `listener_state[key] = state`
after `handle` returns, and the outer call is still running. A listener whose trigger condition
could look "not yet handled" from that stale perspective needs a re-entrancy guard, or it fires
its own reaction over and over. The fetch quest below carries exactly this guard, for exactly this
reason.

## listener_state: what survives, what doesn't

A listener's state dict is the only part of it a save file carries. `register_listener` reserves
an empty slot for the listener's key on registration, and every save and load round-trips
`listener_state` verbatim as plain JSON-compatible data. The listener *object* itself never
serializes — it's code, not data — so after loading a saved game your game must call
`register_listener` again, with the same listeners in the same order, before any of them will see
another event. See [Determinism, saves, and replay](determinism-saves-replay.md) for how loading
and replay work.

## Flags: referee-only session state

Flags solve a smaller version of the same problem: content wiring that isn't a rule the engine
enforces, such as "pulling the lever in the guard room opens the portcullis in the crypt." A flag
is one string key mapped to a `str`, `int`, or `bool` value. The referee command
[`SetFlag`][osrlib.crawl.commands.SetFlag] sets one:

```{.python .no-run}
session.execute(SetFlag(key="crypt.lever_pulled", value=True))
```

`SetFlag` is accepted in every session mode and always succeeds; its handler writes the value into
`session.flags` and emits a [`FlagSetEvent`][osrlib.crawl.events.FlagSetEvent] carrying the key and
value. Flags are referee-only state: like listener state, they round-trip through saves (under
`session.flags`), but neither one appears in the whitelisted
[`PlayerView`][osrlib.crawl.views.PlayerView] a player-facing front end reads — see
[Views and visibility](views-and-visibility.md). A front end that needs a flag's value back —
to decide whether to narrate the portcullis creaking open, say — reads `session.flags` directly
when it holds the session, or `session.view(Visibility.REFEREE).state["flags"]` when it works
from views alone.

## Lifecycle commands: fired-marks, the journal, and notes

Flags are one vocabulary a reactive listener writes with. Three more referee commands cover the
bookkeeping an authored trigger or quest layer needs, and they behave exactly like `SetFlag` —
legal in every mode, never rejected, issued through `execute`, and logged and replayed like any
other command:

- [`MarkTriggerFired`][osrlib.crawl.commands.MarkTriggerFired] records that an authored trigger has
  fired, appending its id to `session.fired_triggers` — the state that answers once-only
  semantics. Marking a trigger that has already fired is accepted, appends nothing, and still
  emits its [`TriggerFiredEvent`][osrlib.crawl.events.TriggerFiredEvent], so a repeatable
  trigger's every firing shows up in the log while the state stays a list of ids in first-fired
  order.
- [`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry] appends a beat to `session.journal`,
  stamped with the clock position it landed at. The journal is the one part of this vocabulary the
  players see: it ships verbatim in the [`PlayerView`][osrlib.crawl.views.PlayerView], and its
  [`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent] is player-visible.
- [`RecordNote`][osrlib.crawl.commands.RecordNote] records an annotation with no state effect at
  all — the mechanism for machine-issued records (a consequence that was dropped, a cascade cut
  short) and for a referee's own margin notes alike. Its event is referee-visibility, like the
  fired-mark's.

Both blocks are engine-owned session state: they persist in saves, and because these commands are
their only writers, a replay — which runs with no listeners registered — rebuilds them exactly by
re-executing the log. That is also why a listener must act by issuing commands rather than by
remembering things itself, the discipline this page opened with.

The optional `source` stamp (see
[Sessions, commands, and events](sessions-commands-events.md)) is what ties the vocabulary
together: a listener that stamps the commands it issues with its own quest or trigger id leaves a
log that answers *why* every entry is there. The library's own interpreter is built on
exactly this surface, and a game's own listener drives it the same way.

## The interpreter: this pattern, shipped

[`Interpreter`][osrlib.crawl.interpreter.Interpreter] is a listener like any other, and it is the
worked reference for everything above. Register one, once, after the session exists — and again
after loading a save, because listeners are code and a save carries data:

```{.python .no-run}
session.register_listener(Interpreter(session))
```

From then on it watches every command's events, matches them against the adventure's authored
[triggers](gates-triggers-quests.md#wiring-the-dungeon-with-triggers) and its
[`QuestSpec`][osrlib.crawl.quests.QuestSpec]s, and reacts the only way a listener may: by
executing referee commands, each stamped `source="trigger:{id}"` or `source="quest:{id}"`. Three
properties are worth copying into your own listeners:

- **It returns no events.** Every event it causes was logged by a command it executed, and the
  result envelope picks those up from the log. `handle` returns `[], {}` unconditionally.
- **It keeps no state.** Its `listener_state` slot exists — `register_listener` creates one — and
  stays the empty dict for the life of the session. Fired-marks live in `session.fired_triggers`,
  beats in `session.journal`, everything else in the world the commands changed. That is what
  makes a triggered game replay exactly: a replay runs with no listeners at all, and re-executing
  the log rebuilds every one of those blocks.
- **It has no re-entrancy guard, on purpose.** The fetch quest below needs one because its trigger
  condition can look unsatisfied from inside its own reaction. The interpreter instead records the
  fired-mark *before* running a trigger's consequences, so a consequence that re-matches its own
  trigger finds it already fired; re-entrant self-invocation is how one trigger's consequences fire
  the next, and a depth bound rather than a latch is what stops a cascade (see
  [When something doesn't land](gates-triggers-quests.md#when-something-doesnt-land)).

## A fetch quest, worked

Most fetch quests belong in the adventure document, where
[`QuestSpec`][osrlib.crawl.quests.QuestSpec] says what to fetch and the interpreter above plays
it — [Gates, triggers, and quests](gates-triggers-quests.md#authoring-a-quest) teaches that
surface, and the TUI crawler's Jade Idol is authored exactly that way (see
[the complete front end](../front-ends/tui-crawler.md)). But the same errand is a fair worked
example of the game-owned pattern, because everything a quest needs is on this page's surface: a
listener that watches events, keeps its own objective state, and acts through commands. The
[complete program](#the-complete-program) below carries this listener whole and runs it.

```{.python .no-run}
class FetchQuestListener:
    """Recover an item and bring it home — a quest tracker as a listener."""

    key = "fetch_quest"

    def __init__(self, session) -> None:
        self._session = session
        self._reacting = False

    def _carrier(self):
        for member in self._session.party.members:
            if member.inventory.carried_item("jade-idol") is not None:
                return member
        return None

    def handle(self, events: Sequence[Event], state: dict) -> tuple[list[Event], dict]:
        if self._reacting:
            return [], state
        state = dict(state)
        acquired = any(isinstance(event, ItemAcquiredEvent) for event in events)
        if acquired and not state.get("recovered") and self._carrier() is not None:
            state["recovered"] = True
        home = any(isinstance(event, LocationEnteredEvent) and event.location_kind == "town" for event in events)
        if home and state.get("recovered") and not state.get("completed"):
            state["completed"] = True
            self._reacting = True
            try:
                self._session.execute(SetFlag(key="quest.idol", value="recovered"))
                for member in self._session.party.living_members():
                    self._session.execute(AwardXP(character_id=member.id, amount=1200))
            finally:
                self._reacting = False
        return [], state
```

A few things worth calling out:

- `state["recovered"]` and `state["completed"]` are the quest's own objective tracking, kept
  entirely inside `session.listener_state["fetch_quest"]`. The session has no idea this is a
  quest; it just stores whatever dict `handle` hands back.
- `self._reacting` is the re-entrancy guard from the previous section, earned honestly: the
  commands this listener issues emit events of their own, and `AwardXP` on the last member would
  otherwise re-enter `handle` while the state slot still held its pre-completion value.
- The `handle` method returns `[], state` unconditionally. Every event this listener causes
  travels through `self._session.execute(...)`, which already logs it; there is nothing left for
  the returned-events list to carry.
- Nothing here reaches into party state to *change* it. The flag and the XP both land as ordinary
  commands, which is why a save, a load, and a replay all agree about what happened.

The interpreter does all of this for you when the quest is adventure data instead — the objective
state lives in `session.quests`, the reward commands carry a `source="quest:{id}"` stamp, and the
listener slot stays empty. Reach for a listener like the one above when a game's own systems own
the objective, and for [`QuestSpec`][osrlib.crawl.quests.QuestSpec] when the adventure does.

## The complete program

Three listeners on one small session: the move counter from the top of the page, the fetch
quest worked above (exercised end to end — the idol acquired, the walk home, the flag and the
XP landing as commands), and the library's interpreter, registered beside them — legal and
inert here, since this adventure authors no triggers or quests. Plus a flag set and read back
two ways, and the lifecycle vocabulary:

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
        self._reacting = False

    def _carrier(self):
        for member in self._session.party.members:
            if member.inventory.carried_item("jade-idol") is not None:
                return member
        return None

    def handle(self, events: Sequence[Event], state: dict) -> tuple[list[Event], dict]:
        if self._reacting:
            return [], state
        state = dict(state)
        acquired = any(isinstance(event, ItemAcquiredEvent) for event in events)
        if acquired and not state.get("recovered") and self._carrier() is not None:
            state["recovered"] = True
        home = any(isinstance(event, LocationEnteredEvent) and event.location_kind == "town" for event in events)
        if home and state.get("recovered") and not state.get("completed"):
            state["completed"] = True
            self._reacting = True
            try:
                self._session.execute(SetFlag(key="quest.idol", value="recovered"))
                for member in self._session.party.living_members():
                    self._session.execute(AwardXP(character_id=member.id, amount=1200))
            finally:
                self._reacting = False
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
referee_state = session.view(Visibility.REFEREE).state
assert referee_state["flags"] == {"crypt.lever_pulled": True}

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

- [The TUI crawler](../front-ends/tui-crawler.md) — the fetch quest in its full adventure context,
  alongside a custom wandering table and a two-level barrow.
- [Ruleset options](ruleset-options.md) — the flags the engine itself reads, as opposed to the
  ones your game defines.
- [Determinism, saves, and replay](determinism-saves-replay.md) — what a save file carries and
  what it doesn't.
- [Views and visibility](views-and-visibility.md) — the player and referee projections, and why
  flags live only in the referee one.
