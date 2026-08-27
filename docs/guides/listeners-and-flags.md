# Listeners and flags

Command handlers implement the SRD's rules: movement, combat, searching, spellcasting, and
everything else a [`GameSession`][osrlib.crawl.session.GameSession] resolves on its own. They
don't know what a fetch quest is, what a lever in a guard room does, or what your game's win
condition looks like. That logic belongs to your game, not the engine, and two mechanisms let you
add it without forking the library: **listeners**, which watch every command's events and
react by executing more commands, and **flags**, a small piece of session state your game reads
and writes directly.

If you'd like to jump right to the code, [the complete program](#the-complete-program) at the end
is self-contained and runnable, and every snippet along the way comes from it.

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
in registration order, immediately after a command is accepted, applied, and logged. A rejected
command never reaches a listener at all, because rejection mutates nothing and appends nothing to
the log. Each listener's `handle` receives two things:

- `events` - the accumulated events for that one command: the command handler's own events, plus
  whatever any earlier-registered listener already returned. A listener registered second sees a
  first listener's authored events alongside the handler's.
- `state` - that listener's own return value from the last time `handle` ran, or `{}` the first
  time (and after a fresh registration). The session never inspects this dict. It's the
  listener's private bookkeeping.

`handle` returns a pair: a list of events to append to the command's result and to the session's
event log, and the state to keep for next time.

That returned-events list is for events a listener **authors** directly. A listener that reacts
by executing its own commands must return an empty list. A nested `session.execute(...)` call
already appends that command's events to the session's event log. Returning them again from
`handle` would log the same event twice.

Returning an empty list hides nothing from the caller. `execute` notes where the event log ends
before it calls each listener, then folds everything logged while that listener ran into the
result it hands back. That's the nested commands' events, however deeply they nest, each exactly
once and in log order, followed by whatever the listener authored. So the `CommandResult` from a
player's `MoveParty` includes the events for the portcullis opening and the journal entry that
recorded it, and your front end renders the whole chain from one envelope.

The nested-`execute` call matters for a second reason: it re-enters the entire dispatch pipeline,
listener loop included. If a listener issues a command from inside `handle`, every registered
listener, itself included, runs again against *that* command's events, with whatever `state`
happens to be stored in `session.listener_state` at that moment. The outer `handle`
call's own state update hasn't landed yet: `execute` only writes `listener_state[key] = state`
after `handle` returns, and the outer call is still running. A listener whose trigger condition
could look "not yet handled" from that stale perspective needs a re-entrancy guard, or it fires
its own reaction over and over. The fetch quest below includes exactly that guard.

## listener_state: what survives, what doesn't

A listener's state dict is the only part of it a save file contains. `register_listener` reserves
an empty slot for the listener's key on registration, and every save and load round-trips
`listener_state` verbatim as plain JSON-compatible data. The listener *object* itself never
serializes, because it's code and not data, so after loading a saved game you must call
`register_listener` again, with the same listeners in the same order, before any of them will see
another event. For more information about how loading and replay work, see
[Determinism, saves, and replay](determinism-saves-replay.md).

## Flags: referee-only session state

Flags solve a smaller version of the same problem: content wiring that isn't a rule the engine
enforces, like "pulling the lever in the guard room opens the portcullis in the crypt." A flag
is one string key mapped to a `str`, `int`, or `bool` value. The referee command
[`SetFlag`][osrlib.crawl.commands.SetFlag] sets one:

```{.python .no-run}
session.execute(SetFlag(key="crypt.lever_pulled", value=True))
```

`SetFlag` is accepted in every session mode and always succeeds. Its handler writes the value into
`session.flags` and emits a [`FlagSetEvent`][osrlib.crawl.events.FlagSetEvent] with the key and the
value. Flags are referee-only state: like listener state, they round-trip through saves (under
`session.flags`), but neither flags nor listener state appear in the whitelisted
[`PlayerView`][osrlib.crawl.views.PlayerView] a player-facing front end reads. For more
information, see [Views and visibility](views-and-visibility.md). A front end that needs a flag's
value back (to decide whether to narrate the portcullis creaking open, for example) reads
`session.flags` directly when it has the session, or
`session.view(Visibility.REFEREE).state["flags"]` when it works from views alone.

## Lifecycle commands: fired-marks, the journal, and notes

Flags are one vocabulary a reactive listener writes with. Three more referee commands cover the
bookkeeping an authored trigger or quest layer needs, and all three behave exactly like `SetFlag`:
legal in every mode, never rejected, issued through `execute`, and logged and replayed like any
other command.

- [`MarkTriggerFired`][osrlib.crawl.commands.MarkTriggerFired] records that an authored trigger has
  fired, appending its id to `session.fired_triggers`. That's the state behind once-only
  semantics. Marking a trigger that has already fired is accepted, appends nothing, and still
  emits its [`TriggerFiredEvent`][osrlib.crawl.events.TriggerFiredEvent], so every firing of a
  repeatable trigger shows up in the log while the state stays a list of ids in first-fired
  order.
- [`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry] appends a beat to `session.journal`,
  stamped with the clock position it landed at. The journal is the one part of this vocabulary the
  players see: it ships verbatim in the [`PlayerView`][osrlib.crawl.views.PlayerView], and its
  [`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent] is player-visible.
- [`RecordNote`][osrlib.crawl.commands.RecordNote] records an annotation with no state effect at
  all. It's the mechanism for machine-issued records (a consequence that was dropped, a cascade
  cut short) and for a referee's own margin notes alike. Its event is referee-visibility, like the
  fired-mark's.

`session.fired_triggers` and `session.journal` are both engine-owned session state. They persist
in saves, and because only commands write them, a replay rebuilds them exactly by re-executing the
log, even though a replay runs with no listeners registered. That's also why a listener must act
by issuing commands instead of remembering things itself.

The optional `source` stamp (see
[Sessions, commands, and events](sessions-commands-events.md)) is what ties the vocabulary
together: a listener that stamps the commands it issues with its own quest or trigger id leaves a
log that shows *why* every entry is there. The library's own interpreter is built on
exactly this surface, and a listener you write uses it the same way.

## The interpreter: this pattern, shipped

[`Interpreter`][osrlib.crawl.interpreter.Interpreter] is a listener like any other, and it's the
worked reference for everything above. Register one, once, after the session exists, and again
after loading a save, because listeners are code and a save contains only data:

```{.python .no-run}
session.register_listener(Interpreter(session))
```

From then on it watches every command's events, matches them against the adventure's authored
[triggers](gates-triggers-quests.md#wiring-the-dungeon-with-triggers) and its
[`QuestSpec`][osrlib.crawl.quests.QuestSpec]s, and reacts the only way a listener may: by
executing referee commands, each stamped `source="trigger:{id}"` or `source="quest:{id}"`. Three
properties are worth copying into your own listeners:

- **It returns no events.** Each event it causes comes from a command it executed, and the
  result envelope picks those up from the log. `handle` returns `[], {}` unconditionally.
- **It keeps no state.** Its `listener_state` slot exists, because `register_listener` creates one,
  and stays the empty dict for the life of the session. Fired-marks live in
  `session.fired_triggers`, beats in `session.journal`, and everything else in the world state the
  commands changed. That's what makes a triggered game replay exactly: a replay runs with no
  listeners at all, and re-executing the log rebuilds all of that state.
- **It has no re-entrancy guard, on purpose.** The fetch quest below needs one because its trigger
  condition can look unsatisfied from inside its own reaction. The interpreter instead records the
  fired-mark *before* running a trigger's consequences, so the trigger is already marked when one
  of its own consequences re-matches it. Re-entrant self-invocation is how one trigger's
  consequences fire the next, and a depth bound rather than a latch is what stops a cascade. For
  more information, see
  [When something doesn't land](gates-triggers-quests.md#when-something-doesnt-land).

## A fetch quest, worked

Most fetch quests belong in the adventure document, where
[`QuestSpec`][osrlib.crawl.quests.QuestSpec] defines what to fetch and the interpreter above runs
it. [Gates, triggers, and quests](gates-triggers-quests.md#authoring-a-quest) covers that
surface, and the TUI crawler's Jade Idol is authored exactly that way (see
[the complete front end](../front-ends/tui-crawler.md)). The same errand also works as an
example of the game-owned pattern, because everything a quest needs is on this page's surface: a
listener that watches events, keeps its own objective state, and acts through commands. The
[complete program](#the-complete-program) below includes this listener whole and runs it.

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

A few points about the listener above:

- `state["recovered"]` and `state["completed"]` are the quest's own objective tracking, kept
  entirely inside `session.listener_state["fetch_quest"]`. The session never interprets these
  keys. It stores whatever dict `handle` returns.
- `self._reacting` is the re-entrancy guard from the previous section. The commands this listener
  issues emit events of their own, and `AwardXP` on the last member would otherwise re-enter
  `handle` while the state slot still held its pre-completion value.
- The `handle` method returns `[], state` unconditionally. Every event this listener causes
  travels through `self._session.execute(...)`, which already logs it, so there's nothing left for
  the returned-events list to contain.
- Nothing here reaches into party state to *change* it. The flag and the XP both land as ordinary
  commands, which is why a save, a load, and a replay all agree about what happened.

The interpreter does all of this for you when the quest is adventure data instead: the objective
state lives in `session.quests`, the reward commands are stamped `source="quest:{id}"`, and the
listener slot stays empty. Use a listener like the one above when your own systems own the
objective, and [`QuestSpec`][osrlib.crawl.quests.QuestSpec] when the adventure does.

## The complete program

Three listeners on one small session: the move counter from the top of the page, the fetch
quest worked above (exercised end to end, from acquiring the idol to the walk home to the flag and
the XP landing as commands), and the library's interpreter, registered beside them. The
interpreter is legal and inert here, because this adventure authors no triggers or quests. The
program also sets a flag and reads it back two ways, and it runs the lifecycle commands:

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

- [The TUI crawler](../front-ends/tui-crawler.md) - the fetch quest in its full adventure context,
  alongside a custom wandering table and a two-level barrow.
- [Ruleset options](ruleset-options.md) - the flags the engine itself reads, as opposed to the
  ones you define.
- [Determinism, saves, and replay](determinism-saves-replay.md) - what a save file contains and
  what it doesn't.
- [Views and visibility](views-and-visibility.md) - the player and referee projections, and why
  flags live only in the referee one.
