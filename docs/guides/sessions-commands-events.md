# Sessions, commands, and events

A [`GameSession`][osrlib.crawl.session.GameSession] is a running game. It owns every
piece of mutable state (the party, the dungeon map as explored so far, the RNG streams,
the clock, the live monster registry, the mode), and it exposes exactly one way to change
any of it: [`execute`][osrlib.crawl.session.GameSession.execute]. Hand it a command, get
back a [`CommandResult`][osrlib.crawl.commands.CommandResult]. Nothing else in the public
API mutates a session. [The complete program](#the-complete-program) is at the end of the
page, and every snippet along the way comes from it.

## The command loop

[`GameSession.new`][osrlib.crawl.session.GameSession.new] validates the adventure,
assigns each party member a session id, and returns a session in town at round 0.
From there every turn is the same shape: build a [`Command`][osrlib.crawl.commands.Command],
pass it to `execute`, read the result.

`execute` runs two phases. First, a mode check: if the session's current mode isn't one
of the command's declared `allowed_modes`, `execute` rejects the command immediately with
no further work. Otherwise the command's handler runs. Every handler is
validate-then-mutate, checking every precondition before drawing a single die or changing
a single field. If the handler finds a problem, it returns rejections and the session is
untouched: no command-log entry, no event-log entries, no RNG draws, no clock time. Only
when a command clears every check does anything change. The session appends the command to
the command log and its events to the event log, then runs any registered listeners in
registration order, each one seeing the events so far and appending its own reactions to
both the result and the log. The `CommandResult` you get back from an accepted command
contains the *complete* chain: the handler's events and every listener's events, in the order
they happened.

That includes what a listener causes by executing further commands. Those nested
commands log their own events, and `execute` folds everything logged while a listener
ran into the result: each event exactly once, in log order. So one `MoveParty` can come
back with the move, the portcullis a trigger opened in response, and the journal entry
that recorded it. Your front end renders all of it from one envelope without ever
reading `session.event_log`.

Listeners are how your game adds its own reactive rules (a quest tracker, an achievement
log) without touching the kernel. For the extension point itself, see
[Listeners and flags](listeners-and-flags.md).

Every command also has an optional `source`: a string naming the authored object (a
trigger or quest id) or the game system on whose behalf the command was issued. Execution
never reads it, so a stamped command does exactly what the unstamped one does. The stamp
goes into the log with the command and survives a save, a load, and a replay. The log
therefore records not just *who* acted but *on whose behalf*, which is what makes "why did
the party get that item?" answerable from the log alone:

```{.python .no-run}
# The source stamp annotates the log and changes nothing about execution.
session.execute(AddJournalEntry(text="The lever grinds.", source="trigger:lever-east"))
assert session.command_log[-1].source == "trigger:lever-east"
assert session.view(Visibility.PLAYER).journal[-1].text == "The lever grinds."
```

The library's [`Interpreter`][osrlib.crawl.interpreter.Interpreter] stamps every command
it issues this way: `trigger:{id}` for a trigger's firing, `quest:{id}` for everything a
quest causes (see [Gates, triggers, and quests](gates-triggers-quests.md) for how you
author triggers and quests). A log left behind by authored content then reads as a
transcript with attributions: this grant came from `trigger:idol-lifted`, that door opened
for `trigger:portcullis-rises`, the coins came from `quest:the-idol`, and the
`record_note` beside them says which consequence was dropped and why.

## Session modes and mode gating

[`SessionMode`][osrlib.crawl.commands.SessionMode] is a small, closed set: `town`,
`exploring`, `encounter`, `battle`, `game_over`, and `victory`. Every
[`Command`][osrlib.crawl.commands.Command] subclass declares which of these modes it's
legal in as an `allowed_modes` class attribute. It's data, not a side effect, so you can
inspect it directly:

```{.python .no-run}
# Each command declares its legal modes as data - MoveParty works only while exploring.
assert MoveParty.allowed_modes == frozenset({SessionMode.EXPLORING})
```

Most dungeon-movement commands ([`MoveParty`][osrlib.crawl.commands.MoveParty],
`TurnParty`, `OpenDoor`, `Search`, and the rest) are legal only while `exploring`.
Commands that make sense both at rest and on the move (`ReorderParty`, `LightSource`,
`Rest`, `CastSpell`) are legal in `town` or `exploring`. Encounter-only commands
(`Parley`, `Evade`, `EngageBattle`, `Wait`, `TurnUndead`) require `encounter`, and
`ResolveBattleRound` requires `battle`. A handful, like `DropItems`, span two modes on
purpose: dropping treasure to distract pursuers works whether the party is still
exploring or already in an encounter. Referee commands (`GrantItem`, `SetFlag`,
`AwardXP`, `AdvanceTime`, the [lifecycle commands](#the-lifecycle-commands), and the rest
of the session-owned surface) are legal in every mode, the two terminal ones included. A
referee correcting the world doesn't stop just because the party fell, and an adventure's
rewards can land after it ends. Three referee commands are the exception, each because it
would resume play in a session that is over.
[`PlaceParty`][osrlib.crawl.commands.PlaceParty] teleports the party into `exploring` or
`town`, and `SpawnMonsters` and `SpawnNpcParty` open an encounter. The two spawn commands
are illegal in both terminal modes. `PlaceParty` is illegal in `victory` alone and stays
legal in `game_over`, because carrying the fallen party to town is the first step of the
documented revival flow: `PlaceParty(town)` then
[`PurchaseHealing`][osrlib.crawl.commands.PurchaseHealing] with `service="raise_dead"`,
with the clock still running on the revival window.

The modes form a loop with two ways out.
[`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] moves the party from `town` to the
dungeon entrance and switches the session to `exploring`. Stepping into a keyed area's
cells, a wandering-monster check, or a referee's `SpawnMonsters` or
`SpawnNpcParty` opens an encounter and switches to `encounter`. `EngageBattle` opens
full combat and switches to `battle`. A battle ends back in `encounter` (the party
broke off and a pursuit begins), in `exploring` (the monsters are beaten, the
encounter closes, and play continues), or, if the whole party falls, in the terminal
`game_over`. `TravelToTown` is the return trip, switching `exploring` back to `town`.

A lost battle is not the only way a session ends. Any command whose events leave the party
with nobody standing routes to `game_over` and reports it with the same
[`GameOverEvent`][osrlib.crawl.events.GameOverEvent]: a save-or-die trap sprung by a step
into the wrong room, a fall, starvation on a long delve, a poison that resolves under a
referee's `AdvanceTime`. `victory` is the other terminal mode, and it has **exactly one**
entrance. [`CompleteQuest`](#the-lifecycle-commands) on a quest authored
`concludes_adventure=True` is how the interpreter ends an adventure whose concluding quest
completes (see
[the completion rule and the ending](gates-triggers-quests.md#the-completion-rule-and-the-ending)).
[`SessionMode.terminal`][osrlib.crawl.commands.SessionMode] answers "has this session
ended?" for both terminal modes.

## The lifecycle commands

The authored layer keeps its own books with seven referee commands. Three
of them are the trigger and journal vocabulary:
[`MarkTriggerFired`][osrlib.crawl.commands.MarkTriggerFired],
[`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry], and
[`RecordNote`][osrlib.crawl.commands.RecordNote]. The other four advance quest state:

- [`ActivateQuest`][osrlib.crawl.commands.ActivateQuest] puts a quest in play.
- [`RevealObjective`][osrlib.crawl.commands.RevealObjective] surfaces a hidden objective.
- [`CompleteObjective`][osrlib.crawl.commands.CompleteObjective] marks an objective done,
  and reveals it on the way, because an objective the party finished is one the party can
  be told about.
- [`CompleteQuest`][osrlib.crawl.commands.CompleteQuest] finishes the quest, and on a
  quest marked as concluding the adventure, ends the session in `victory`.

They're ordinary commands: legal in every mode, logged, replayed, and stamped like any
other. What they write (per-quest status and per-objective flags in `session.quests`) is
engine-owned session state, so a replay with no listeners registered rebuilds it by
re-executing the log.

Their ids are the one place the lifecycle family isn't uniform.
`MarkTriggerFired.trigger_id` is an **open domain**: a mark records that something fired,
needs no authored trigger behind it, and your game drives it with ids from its own
systems.
The four quest commands invert that. They resolve `quest_id` and `objective_id` against
the adventure's own [`QuestSpec`][osrlib.crawl.quests.QuestSpec]s, a **closed domain**,
and reject an id that matches no spec (`session.command.unknown_quest`,
`session.command.unknown_objective`). The state they advance is projected into the player
view, and an id with no spec behind it has no name, no offer, and no objective list to
show. A command that contradicts the state it finds (activating a quest already active,
completing an objective already complete) rejects with `session.command.quest_state`,
naming the quest and the state the handler found.

One asymmetry is deliberate: `CompleteQuest` requires the quest to be active and does
*not* check its completion rule. Ruling a quest done is the referee's call, and the
interpreter checks the completion rule before it issues the command. For the same reason,
the command grants no rewards. Whoever completes a quest issues its rewards afterwards, which
is why a hand-driven completion grants nothing.

## Rejections versus exceptions

A rejected command is a normal outcome, not a failure. Validation is a pure pre-phase:
it never draws randomness, never advances the clock, never mutates anything, and a
rejected command never enters the command log. [`Rejection`][osrlib.core.validation.Rejection]
is a small structured model (a dotted snake_case `code` plus `params`) that contains exactly
the facts your front end needs to render the refusal, never baked English prose.
Moving into a wall, trying to pick a lock without thieves' tools, casting a spell in
the wrong mode: these are all rejections, and `CommandResult.rejections` is where they
land.

One rejection family includes authored player-facing text on top of its code. A gate
refusal (`exploration.door.gate_refused`, `exploration.transition.gate_refused`)
puts the author's `refusal` beat in its `params`, content data in a structured
field rather than engine-baked English, so a front end that renders rejections should
show that line to the player. [Gates, triggers, and quests](gates-triggers-quests.md)
shows how you author that refusal on a gate.

```{.python .no-run}
# The party starts in town: MoveParty is out of mode and comes back rejected, not raised.
result = session.execute(MoveParty(direction=Direction.EAST))
assert result.accepted is False
assert result.rejections[0].code == "session.command.wrong_mode"
assert session.mode is SessionMode.TOWN
assert session.clock.rounds == 0
assert session.event_log == []
```

Once the mode is right, the same command mutates and reports what happened as
[`Event`][osrlib.core.events.Event] instances, never English text:

```{.python .no-run}
# EnterDungeon switches the session to exploring; the same move is now legal and mutates.
session.execute(EnterDungeon(dungeon_id="crypt"))
assert session.mode is SessionMode.EXPLORING
result = session.execute(MoveParty(direction=Direction.EAST))
assert result.accepted
lines = [format_message(event) for event in result.events]
assert lines  # every accepted command's events format to a default English line
```

A raised exception means something different: you broke the API contract, or the content
you handed the library is malformed. The `osrlib.errors` hierarchy, rooted at
[`OsrlibError`][osrlib.errors.OsrlibError], is reserved for exactly that.
[`ContentValidationError`][osrlib.errors.ContentValidationError] covers malformed content
(an adventure with a dangling monster id, a serialized command or event whose *known* type
doesn't match its payload). [`SaveVersionError`][osrlib.errors.SaveVersionError] covers a
save written by a newer library than the one loading it, and
[`ReplayVersionError`][osrlib.errors.ReplayVersionError] covers replaying a command log
under a different engine version than the one that recorded it. None of these are
in-fiction outcomes. They tell you that what you handed the library doesn't make sense as
input at all. Plain `ValueError` and `TypeError` cover a third case, ordinary programmer
misuse (a bad argument type, an out-of-range seed) that isn't even worth a typed
exception.

The three categories stay separate at the command boundary. A referee command that names
an unknown character or item id (`GrantItem` with a stale `character_id`, say) doesn't
raise. The handler catches the lookup failure and turns it into an ordinary rejection
(`session.command.unknown_member`, `session.command.unknown_item`), because a referee typo
naming a character who left the party is exactly the kind of thing a front end needs to
handle, not crash on. A front end serving osrlib over a network boundary needs to make
this same three-way split at its edge. For how one example does it, see
[the FastAPI pattern](../front-ends/fastapi-pattern.md).

## The event log and message codes

Every accepted command's events land in `session.event_log`, in the order they
happened: the handler's own events first, then each registered listener's, in
registration order. Every event has a `code` and a `visibility`. The code is two or more
dot-separated snake_case segments namespaced by subsystem, like `exploration.party.moved`
or `combat.attack.hit`, and [Views and visibility](views-and-visibility.md) covers what
the visibility field means. [`format_message`][osrlib.messages.format_message] turns any
event into a default English line by dispatching on its code. It's total and pure, so an
event whose code it doesn't recognize (from a newer engine version) still formats to
something printable, the code string itself, rather than raising.

The full catalog of shipped event classes and message codes lives in
[the events reference](../reference/events/index.md) and
[the message code reference](../reference/message-codes.md). Every rejection code the
engine can emit is in [the rejection code reference](../reference/rejection-codes.md).

## The wire discriminators

Commands and events share the same discriminated-union shape: a frozen pydantic
model with `extra="ignore"` and a single-valued string field that names its concrete
class on the wire (`command_type` on `Command`, `event_type` on `Event`).
[`parse_command`][osrlib.crawl.commands.parse_command] and
[`parse_any_event`][osrlib.crawl.events.parse_any_event] parse a previously-dumped
payload back into the right concrete type, and both are deliberately tolerant of
*unknown* types: a `command_type` or `event_type` this version of the library has never
heard of parses to `None` instead of raising. That's the additive-schema guarantee in
practice. A save or a network payload produced by a newer engine version can include
command and event kinds an older consumer has never seen, and the older consumer skips
them instead of crashing.

```{.python .no-run}
# Commands and events round-trip through their wire discriminator; unknown types parse to None.
move_payload = MoveParty(direction=Direction.EAST).model_dump(mode="json")
assert parse_command(move_payload) == MoveParty(direction=Direction.EAST)
assert parse_command({"command_type": "some_future_command"}) is None

event_payload = result.events[0].model_dump(mode="json")
assert parse_any_event(event_payload) == result.events[0]
assert parse_any_event({"event_type": "some_future_event", "code": "x.y"}) is None
```

The tolerance extends **only** to types the parser has never seen. A payload whose
`command_type` or `event_type` *is* recognized but whose fields don't validate (a
required field missing, a value of the wrong shape) is malformed data, not a forward
compatibility case, and raises `ContentValidationError` instead of returning `None`:

```{.python .no-run}
# A malformed payload of a *known* type is a broken API contract: it raises, never rejects.
try:
    parse_command({"command_type": "move_party"})  # missing the required 'direction'
except ContentValidationError:
    pass
else:
    raise AssertionError("expected ContentValidationError")
```

`osrlib.core.events` has the narrower [`parse_event`][osrlib.core.events.parse_event],
which recognizes only the kernel event classes. `parse_any_event` covers kernel and
crawl events together, and it's what the session's own log uses.

## The complete program

```python
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.events import Visibility
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import AddJournalEntry, EnterDungeon, MoveParty, SessionMode, parse_command
from osrlib.crawl.dungeon import Direction, DungeonSpec, Edge, EdgeKind, LevelSpec
from osrlib.crawl.events import parse_any_event
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.errors import ContentValidationError
from osrlib.messages import format_message

# Each command declares its legal modes as data - MoveParty works only while exploring.
assert MoveParty.allowed_modes == frozenset({SessionMode.EXPLORING})

rules = Ruleset()
creation = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
hero = create_character(
    name="Hild",
    class_id="fighter",
    alignment=Alignment.LAWFUL,
    ruleset=rules,
    stream=creation,
)
party = Party(members=[hero.character])

crypt = DungeonSpec(
    id="crypt",
    name="The Old Crypt",
    levels=(
        LevelSpec(
            number=1,
            width=2,
            height=1,
            entrance=(0, 0),
            edges={"1,0:west": Edge(kind=EdgeKind.OPEN)},
        ),
    ),
)
town = TownSpec(name="Threshold", travel_turns={"crypt": 1})
adventure = Adventure(name="A First Delve", town=town, dungeons=(crypt,))
session = GameSession.new(party, adventure, seed=7)

# The party starts in town: MoveParty is out of mode and comes back rejected, not raised.
result = session.execute(MoveParty(direction=Direction.EAST))
assert result.accepted is False
assert result.rejections[0].code == "session.command.wrong_mode"
assert session.mode is SessionMode.TOWN
assert session.clock.rounds == 0
assert session.event_log == []

# EnterDungeon switches the session to exploring; the same move is now legal and mutates.
session.execute(EnterDungeon(dungeon_id="crypt"))
assert session.mode is SessionMode.EXPLORING
result = session.execute(MoveParty(direction=Direction.EAST))
assert result.accepted
lines = [format_message(event) for event in result.events]
assert lines  # every accepted command's events format to a default English line

# The source stamp annotates the log and changes nothing about execution.
session.execute(AddJournalEntry(text="The lever grinds.", source="trigger:lever-east"))
assert session.command_log[-1].source == "trigger:lever-east"
assert session.view(Visibility.PLAYER).journal[-1].text == "The lever grinds."

# Commands and events round-trip through their wire discriminator; unknown types parse to None.
move_payload = MoveParty(direction=Direction.EAST).model_dump(mode="json")
assert parse_command(move_payload) == MoveParty(direction=Direction.EAST)
assert parse_command({"command_type": "some_future_command"}) is None

event_payload = result.events[0].model_dump(mode="json")
assert parse_any_event(event_payload) == result.events[0]
assert parse_any_event({"event_type": "some_future_event", "code": "x.y"}) is None

# A malformed payload of a *known* type is a broken API contract: it raises, never rejects.
try:
    parse_command({"command_type": "move_party"})  # missing the required 'direction'
except ContentValidationError:
    pass
else:
    raise AssertionError("expected ContentValidationError")
```

## Where next

- [Views and visibility](views-and-visibility.md) - the projections that hide what
  B/X hides, built from this same session state.
- [Listeners and flags](listeners-and-flags.md) - the extension point `execute`
  runs after every command.
- [Determinism, saves, and replay](determinism-saves-replay.md) - how the command
  log becomes a save and a replay.
- [The FastAPI pattern](../front-ends/fastapi-pattern.md) - turning rejections and
  the `osrlib.errors` hierarchy into HTTP responses at a service boundary.
