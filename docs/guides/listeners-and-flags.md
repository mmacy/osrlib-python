# Listeners and flags

Command handlers implement the SRD's rules: movement, combat, searching, spellcasting, and
everything else a [`GameSession`][osrlib.crawl.session.GameSession] resolves on its own. They
don't know what a fetch quest is, what a lever in a guard room does, or what your game's win
condition looks like — that logic belongs to your game, not the engine. Two small mechanisms
carry it without forking the library: **listeners**, which watch every command's events and
react by executing more commands, and **flags**, a small piece of session state your game reads
and writes directly.

This page works through both mechanics as the code implements them, then retells the TUI
crawler's fetch quest — the worked example both mechanisms exist for. [The complete program](#the-complete-program)
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

## The fetch quest, worked

The TUI crawler (see [the complete front end](../front-ends/tui-crawler.md)) hides a named
valuable, the Jade Idol, in a hand-placed treasure cache and tracks its recovery with a listener
registered once, right after the session is created:

```{.python .no-run}
session = GameSession.new(party, adventure, seed=arguments.seed, ruleset=ruleset)
session.register_listener(FetchQuestListener(session))
```

Here is the listener in full, from `examples/tui_crawler/quest.py`:

```{.python .no-run}
--8<-- "examples/tui_crawler/quest.py:fetch-quest-listener"
```

A few things worth calling out:

- `state["reward_granted"]` and `state["completed"]` are the quest's own objective tracking, kept
  entirely inside `session.listener_state["fetch_quest"]`. The session has no idea this is a
  quest; it just stores whatever dict `handle` hands back.
- The reward fires the instant an `ItemAcquiredEvent` shows up in `events` — whenever the idol
  lands in a party member's pack, not when the party gets back to town. That timing is
  deliberate: under the default `on_return` XP timing (see [Ruleset options](ruleset-options.md)),
  the adventure's award is the delta between the party's treasure valuation at the moment of
  return and at departure. Coin granted while still in the dungeon counts toward that delta;
  coin granted at the town-return event would arrive after the award already fired.
- `self._reacting` is the re-entrancy guard from the previous section, earned honestly:
  `GrantCoins`'s handler emits its own `ItemAcquiredEvent` (a coin grant is an acquisition too),
  which matches this same listener's trigger condition. Without the guard, the nested `execute`
  call would run `handle` again while `session.listener_state["fetch_quest"]` still held its
  pre-reward value, see an apparently ungranted reward, and call `GrantCoins` again — and again,
  without ever returning.
- The `handle` method returns `[], state` unconditionally. Every event this listener causes
  travels through `self._session.execute(...)`, which already logs it; there is nothing left for
  the returned-events list to carry.

## The complete program

A minimal listener that counts party moves, exercised against a couple of commands (one of them
rejected), plus a flag set and read back two ways:

```python
from collections.abc import Sequence

from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.events import Event, Visibility
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import EnterDungeon, MoveParty, SetFlag
from osrlib.crawl.dungeon import Direction, DungeonSpec, Edge, EdgeKind, LevelSpec
from osrlib.crawl.events import PartyMovedEvent
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


# The quickstart's one-corridor crypt: two cells joined west-east.
crypt = DungeonSpec(
    id="crypt",
    name="The Old Crypt",
    levels=(LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)}),),
)
town = TownSpec(name="Threshold", travel_turns={"crypt": 1})
adventure = Adventure(name="A First Delve", town=town, dungeons=(crypt,))

rules = Ruleset()
creation = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
fighter = create_character(name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=rules, stream=creation)
party = Party(members=[fighter.character])

session = GameSession.new(party, adventure, seed=7)
session.register_listener(MoveCounter())

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
