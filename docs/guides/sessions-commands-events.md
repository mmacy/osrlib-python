# Sessions, commands, and events

A [`GameSession`][osrlib.crawl.session.GameSession] is a running game. It owns every
piece of mutable state — the party, the dungeon map as explored so far, the RNG
streams, the clock, the live monster registry, the mode — and it exposes exactly one
way to change any of it: [`execute`][osrlib.crawl.session.GameSession.execute]. Hand it
a command, get back a [`CommandResult`][osrlib.crawl.commands.CommandResult]. Nothing
else in the public API mutates a session. This page walks the loop in depth: the modes
that gate which commands are legal, the difference between a rejected command and a
raised exception, and the event log those accepted commands leave behind. The complete
program appears [at the end of the page](#the-complete-program); every fragment along
the way is an excerpt of it.

## The command loop

[`GameSession.new`][osrlib.crawl.session.GameSession.new] validates the adventure,
assigns each party member a session id, and returns a session in town at round 0.
From there every turn is the same shape: build a [`Command`][osrlib.crawl.commands.Command],
pass it to `execute`, read the result.

`execute` runs a strict two-phase discipline. First, a mode check: if the session's
current mode isn't one of the command's declared `allowed_modes`, the command is
rejected immediately with no further work. Otherwise the command's handler runs — every
handler is validate-then-mutate, checking every precondition before drawing a single
die or changing a single field. If the handler finds a problem, it returns rejections
and the session is untouched: no command-log entry, no event-log entries, no RNG draws,
no clock time. Only when a command clears every check does it actually change anything:
the command is appended to the command log, its events are appended to the event log,
and any registered listeners run in registration order, each one seeing the events so
far and appending its own reactions to both the result and the log. The
`CommandResult` a caller receives after an accepted command carries the *complete*
chain — the handler's events and every listener's events, in the order they happened.

Listeners are how a game adds its own reactive rules (a quest tracker, an achievement
log) without touching the kernel; see
[Listeners and flags](listeners-and-flags.md) for the extension point itself.

## Session modes and mode gating

[`SessionMode`][osrlib.crawl.commands.SessionMode] is a small, closed set: `town`,
`exploring`, `encounter`, `battle`, and `game_over`. Every
[`Command`][osrlib.crawl.commands.Command] subclass declares which of these modes it's
legal in as an `allowed_modes` class attribute — data, not a side effect, so it can be
inspected directly:

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
purpose — dropping treasure to distract pursuers works whether the party is still
exploring or already in an encounter. Referee commands (`GrantItem`, `SetFlag`,
`SpawnMonsters`, `PlaceParty`, `AdvanceTime`, and the rest of the session-owned
surface) declare no restriction at all, so they run in every mode, `game_over`
included — a referee correcting the world doesn't stop just because the party fell.

The modes form a loop, not a line:
[`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] moves the party from `town` to the
dungeon entrance and switches the session to `exploring`; stepping into a keyed area's
cells, a wandering-monster check, or a referee's `SpawnMonsters` /
`SpawnNpcParty` opens an encounter and switches to `encounter`; `EngageBattle` opens
full combat and switches to `battle`; a battle ends back in `encounter` (the party
broke off and a pursuit begins), in `exploring` (victory — the encounter closes and
play continues), or, if the whole party falls, in the terminal `game_over`.
`TravelToTown` is the return trip, switching `exploring` back to `town`.

## Rejections versus exceptions

A rejected command is a normal outcome, not a failure. Validation is a pure pre-phase:
it never draws randomness, never advances the clock, never mutates anything, and a
rejected command never enters the command log. [`Rejection`][osrlib.core.validation.Rejection]
is a small structured model — a dotted snake_case `code` plus `params` — carrying
exactly the facts a front end needs to render the refusal, never baked English prose.
Moving into a wall, trying to pick a lock without thieves' tools, casting a spell in
the wrong mode: these are all rejections, and `CommandResult.rejections` is where they
land.

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
[`Event`][osrlib.core.events.Event] instances — never English text:

```{.python .no-run}
# EnterDungeon switches the session to exploring; the same move is now legal and mutates.
session.execute(EnterDungeon(dungeon_id="crypt"))
assert session.mode is SessionMode.EXPLORING
result = session.execute(MoveParty(direction=Direction.EAST))
assert result.accepted
lines = [format_message(event) for event in result.events]
assert lines  # every accepted command's events format to a default English line
```

A raised exception means something different: the caller broke the API contract, or
the content handed to the library is malformed. The `osrlib.errors` hierarchy, rooted
at [`OsrlibError`][osrlib.errors.OsrlibError], is reserved for exactly
that: [`ContentValidationError`][osrlib.errors.ContentValidationError] for malformed
content (an adventure with a dangling monster id, a serialized command or event whose
*known* type doesn't match its payload), [`SaveVersionError`][osrlib.errors.SaveVersionError]
for a save written by a newer library than the one loading it, and
[`ReplayVersionError`][osrlib.errors.ReplayVersionError] for replaying a command log
under a different engine version than the one that recorded it. None of these are
in-fiction outcomes — they're the library telling a caller that what it was handed
doesn't make sense as input at all. Plain `ValueError` and `TypeError` cover a third
case, ordinary programmer misuse (a bad argument type, an out-of-range seed) that isn't
even worth a typed exception.

The three categories stay cleanly separated at the command boundary. A referee command
that names an unknown character or item id — `GrantItem` with a stale `character_id`,
say — doesn't raise: the handler catches the lookup failure and turns it into an
ordinary rejection (`session.command.unknown_member`, `session.command.unknown_item`),
because a referee typo naming a character who left the party is exactly the kind of
thing a front end needs to handle gracefully, not crash on. A front end serving osrlib
over a network boundary needs to make this same three-way split at its edge — see
[the FastAPI pattern](../front-ends/fastapi-pattern.md) for how one example does it.

## The event log and message codes

Every accepted command's events land in `session.event_log`, in the order they
happened: the handler's own events first, then each registered listener's, in
registration order. Every event carries a `code` — two or more dot-separated
snake_case segments namespaced by subsystem, like `exploration.party.moved` or
`combat.attack.hit` — and a `visibility` (see [Views and visibility](views-and-visibility.md)
for what that second field means). [`format_message`][osrlib.messages.format_message]
turns any event into a default English line by dispatching on its code; it's total and
pure, so an event whose code it doesn't recognize (from a newer engine version) still
formats to something printable — the code string itself — rather than raising.

The full catalog of shipped event classes and message codes lives in
[the events reference](../reference/events/index.md) and
[the message code reference](../reference/message-codes.md); every rejection code the
engine can emit is in [the rejection code reference](../reference/rejection-codes.md).

## The wire discriminators

Commands and events both mirror the same discriminated-union shape: a frozen pydantic
model with `extra="ignore"` and a single-valued string field — `command_type` on
`Command`, `event_type` on `Event` — that names its concrete class on the wire.
[`parse_command`][osrlib.crawl.commands.parse_command] and
[`parse_any_event`][osrlib.crawl.events.parse_any_event] parse a previously-dumped
payload back into the right concrete type, and both are deliberately tolerant of
*unknown* types: a `command_type` or `event_type` this version of the library has never
heard of parses to `None` instead of raising. That's the additive-schema guarantee in
practice — a save or a network payload produced by a newer engine version can carry
command and event kinds an older consumer has never seen, and the older consumer skips
them cleanly instead of crashing.

```{.python .no-run}
# Commands and events round-trip through their wire discriminator; unknown types parse to None.
move_payload = MoveParty(direction=Direction.EAST).model_dump(mode="json")
assert parse_command(move_payload) == MoveParty(direction=Direction.EAST)
assert parse_command({"command_type": "some_future_command"}) is None

event_payload = result.events[0].model_dump(mode="json")
assert parse_any_event(event_payload) == result.events[0]
assert parse_any_event({"event_type": "some_future_event", "code": "x.y"}) is None
```

Tolerance only extends to types the parser has never seen. A payload whose
`command_type` or `event_type` *is* recognized but whose fields don't validate — a
required field missing, a value of the wrong shape — is malformed data, not a forward
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

`osrlib.core.events` ships the narrower [`parse_event`][osrlib.core.events.parse_event],
which only recognizes the kernel event classes; `parse_any_event` covers kernel and
crawl events together and is what the session's own log uses.

## The complete program

```python
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import EnterDungeon, MoveParty, SessionMode, parse_command
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

- [Views and visibility](views-and-visibility.md) — the projections that hide what
  B/X hides, built from this same session state.
- [Listeners and flags](listeners-and-flags.md) — the extension point `execute`
  runs after every command.
- [Determinism, saves, and replay](determinism-saves-replay.md) — how the command
  log this page builds becomes a save and a replay.
- [The FastAPI pattern](../front-ends/fastapi-pattern.md) — turning rejections and
  the `osrlib.errors` hierarchy into HTTP responses at a service boundary.
