# Sessions, commands, and events

[`GameSession`][osrlib.crawl.session.GameSession] manages the mutable state of a running game: the party, the explored dungeon map, the RNG streams, the clock, live monsters, and the session mode. You advance play through [`GameSession.execute`][osrlib.crawl.session.GameSession.execute], which accepts a typed [`Command`][osrlib.crawl.commands.Command] and returns a [`CommandResult`][osrlib.crawl.commands.CommandResult].

## Execute commands and read events

Call [`GameSession.new`][osrlib.crawl.session.GameSession.new] to validate the adventure, assign each party member a session ID, and start the party in `town` at round 0. Each player or referee action is one `session.execute(command)` call (movement, searches, and combat rounds advance the game clock, whereas town or inspection actions consume no in-game time).

When a command is legal, `execute` updates session state, appends the command to `session.command_log`, appends the resulting [`Event`][osrlib.core.events.Event] instances to `session.event_log`, and runs registered listeners in registration order. Events contain structured fields, a `visibility`, and a message code rather than rendered English prose. Filter to `Visibility.PLAYER` before rendering output for players, and pass each event to [`format_message`][osrlib.messages.format_message] when you want a default English line:

```{.python .no-run}
session = GameSession.new(party, adventure, seed=7)
session.execute(EnterDungeon(dungeon_id="crypt"))
assert session.mode is SessionMode.EXPLORING

moved = session.execute(MoveParty(direction=Direction.EAST))
assert moved.accepted
assert moved.events[0].code == "exploration.party.moved"
lines = [format_message(event) for event in moved.events if event.visibility is Visibility.PLAYER]
assert lines == ["The party moves to (1, 0), facing east."]
```

`moved.events` contains the full reaction chain for that `execute` call in log order: the command handler's events first, followed by any events produced by listeners (including nested `session.execute` calls) without duplicates. For example, a single `MoveParty` result can include the movement event, a portcullis opening from a trigger, and the journal entry recording that change. You can render the entire chain from `CommandResult.events` without reading `session.event_log`. To write listeners that react to events, see [Listeners and flags](listeners-and-flags.md).

Every event includes a `code` and a `visibility`. Event codes use dot-separated `snake_case` segments grouped by subsystem, such as `exploration.party.moved` or `combat.attack.hit`. `format_message` dispatches on `event.code` and returns the code string itself if it encounters an unfamiliar code from a newer engine release, so formatting never raises an exception. See [Views and visibility](views-and-visibility.md) for event visibility filtering, [the events reference](../reference/events/index.md) for event payloads, and [the message code reference](../reference/message-codes.md) for default message templates.

## Handle rejections and exceptions

`execute` validates the session mode and all command preconditions before rolling dice, advancing the clock, or mutating state. When a game rule blocks an action (such as walking into a wall, picking a lock without thieves' tools, or calling `EnterDungeon` when the party is already inside), `execute` returns `CommandResult(accepted=False)` with structured [`Rejection`][osrlib.core.validation.Rejection] entries in `result.rejections` instead of raising an exception. Rejected commands leave the clock, `session.command_log`, and `session.event_log` unchanged:

```{.python .no-run}
rounds_before = session.clock.rounds
log_len = len(session.event_log)

blocked = session.execute(EnterDungeon(dungeon_id="crypt"))  # already exploring
assert blocked.accepted is False
assert blocked.rejections[0].code == "session.command.wrong_mode"
assert session.clock.rounds == rounds_before
assert len(session.event_log) == log_len
```

Each `Rejection` specifies a dotted `code` and a `params` dict so your front end can format player feedback (see [the rejection code reference](../reference/rejection-codes.md)). Gate refusals (`exploration.door.gate_refused` and `exploration.transition.gate_refused`) also include the adventure author's `refusal` string in `params`, which your front end should display to the player (see [Gates, triggers, and quests](gates-triggers-quests.md)). When [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound] refuses a round, every rejection of a declaration has the declaring member's id as its `character` param, so you can show each player the rejections of their own declaration.

Exceptions signal broken API contracts or invalid data rather than illegal player moves:

- [`ContentValidationError`][osrlib.errors.ContentValidationError] (a subclass of [`OsrlibError`][osrlib.errors.OsrlibError]): invalid adventure content (such as an unknown monster ID) or a serialized command or event with a recognized type whose fields fail validation.
- [`SaveVersionError`][osrlib.errors.SaveVersionError]: a save document written by a newer `schema_version` than the installed library supports.
- [`ReplayVersionError`][osrlib.errors.ReplayVersionError]: a command log replayed under an engine version different from the recorded version.
- `ValueError` and `TypeError`: invalid Python arguments, such as an unrecognized command class or an out-of-range RNG seed.

Lookup failures inside referee commands (for example, `GrantItem` with a stale `character_id` or unknown `item_id`) return rejections (`session.command.unknown_member` or `session.command.unknown_item`) instead of raising exceptions. If you expose `GameSession` over HTTP, map rejections and exception classes to separate status codes, as shown in [the FastAPI pattern](../front-ends/fastapi-pattern.md).

## Session modes and allowed commands

[`SessionMode`][osrlib.crawl.commands.SessionMode] defines the game phases: `town`, `exploring`, `encounter`, `battle`, `game_over`, and `victory`. Every [`Command`][osrlib.crawl.commands.Command] subclass declares the modes in which it is legal on its `allowed_modes` class attribute, which you can inspect directly:

```{.python .no-run}
# Each command declares its legal modes as data - MoveParty works only while exploring.
assert MoveParty.allowed_modes == frozenset({SessionMode.EXPLORING})
```

Play commands follow the party's current phase:

- **Dungeon exploration** (`MoveParty`, `TurnParty`, `OpenDoor`, `Search`, and related exploration actions): legal in `exploring`.
- **Camp and preparation** (`ReorderParty`, `LightSource`, `Rest`, `CastSpell`): legal in both `town` and `exploring`.
- **Encounter actions** (`Parley`, `Evade`, `EngageBattle`, `Wait`, `TurnUndead`): legal in `encounter`.
- **Combat rounds** (`ResolveBattleRound`): legal in `battle`.
- **Item transfers**: `DropItems` works in `exploring` and `encounter` (allowing a fleeing party to drop treasure to distract pursuers). `GiveItems` works in `town`, `exploring`, and `encounter` (where handing over an item consumes the encounter round), whereas in `battle` a character transfers an item by submitting a [`BattleDeclaration`][osrlib.crawl.commands.BattleDeclaration] with `action="give_item"`.

Commands move the session between modes as play progresses:

- [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] moves the party from `town` to the dungeon entrance (`exploring`), and `TravelToTown` returns the party from `exploring` to `town`.
- Stepping into an area with a keyed encounter, rolling a wandering monster, or executing `SpawnMonsters` or `SpawnNpcParty` from `exploring` starts an `encounter`. Executing `SpawnMonsters` during an active `encounter` or `battle` adds a monster group without changing the mode.
- `EngageBattle` transitions from `encounter` to `battle`. When combat ends, the session transitions to `encounter` (if the party retreats into pursuit), `exploring` (if all foes are defeated), or `game_over` (if the entire party is incapacitated).
- [`EndEncounter`][osrlib.crawl.commands.EndEncounter] closes an active `encounter` or `battle` and returns the session to `exploring` (for example, after a successful parley). Pass `resolve_area=True` if a keyed area's monsters should not reappear when the party re-enters.
- Any accepted command that leaves every party member dead or petrified (whether from combat, a trap, a fall, starvation, or poison ticking under `AdvanceTime`) switches the session to `game_over` and emits [`GameOverEvent`][osrlib.crawl.events.GameOverEvent].
- [`CompleteQuest`](#attribution-stamps-and-lifecycle-commands) on a quest configured with `concludes_adventure=True` switches the session to `victory` (see [the completion rule and the ending](gates-triggers-quests.md#the-completion-rule-and-the-ending)). Check [`SessionMode.terminal`][osrlib.crawl.commands.SessionMode] to test whether the session has reached either `game_over` or `victory`.

Most referee commands (such as `GrantItem`, `SetFlag`, `AwardXP`, `AdvanceTime`, and the [lifecycle commands](#attribution-stamps-and-lifecycle-commands)) declare every mode in `allowed_modes`, including `game_over` and `victory`. `allowed_modes` checks only game-phase legality, not caller permissions, so a networked server must restrict player requests to player command classes rather than accepting arbitrary referee commands from clients. The referee commands that open or close an encounter, move the party, or change who is in it restrict `allowed_modes`, because each one depends on whether an encounter is open and whether the game has ended. An encounter happens in a dungeon, and an open one is set up for the party it started with, where that party stands. A game that ends in `victory` doesn't restart, and one that ends in `game_over` restarts only through `PlaceParty`:

- `EndEncounter` is legal only in `encounter` and `battle`, because it closes the open encounter.
- `SpawnMonsters` is legal in `exploring`, `encounter`, and `battle`, because it opens an encounter or adds a group to the open one. `SpawnNpcParty` is legal only in `exploring`, because it opens an encounter. Neither works in `town`, `game_over`, or `victory`.
- [`PlaceParty`][osrlib.crawl.commands.PlaceParty] is legal in `town`, `exploring`, and `game_over`, but not in `encounter`, `battle`, or `victory`. The party can't be moved out of an open encounter or battle, and moving it restarts play, so it isn't legal once the party has won. Keeping `PlaceParty` legal in `game_over` supports party recovery: you can move the fallen party to `town` and execute [`PurchaseHealing`][osrlib.crawl.commands.PurchaseHealing] with `service="raise_dead"` while the dead characters are still within the spell's day limit.
- [`AddMember`][osrlib.crawl.commands.AddMember] and [`RemoveMember`][osrlib.crawl.commands.RemoveMember] are legal only in `town` and `exploring`. An encounter or a battle is already set up for the party it started with, so neither command is legal while one is open, and neither is legal after the game has ended. To replace a party whose members are all dead or turned to stone, first send `PlaceParty` to move the party to town, which takes the session out of `game_over`, and then add the new characters.

## Parse commands and events from JSON

Commands and events are frozen Pydantic models configured with `extra="ignore"` and discriminated by `command_type` (on `Command`) or `event_type` (on `Event`). Use [`parse_command`][osrlib.crawl.commands.parse_command] and [`parse_any_event`][osrlib.crawl.events.parse_any_event] to deserialize JSON dicts back into concrete models.

Both parsers return `None` when given a `command_type` or `event_type` not defined in the installed version of osrlib, and `parse_any_event` also returns `None` when a known event class receives a well-formed `code` it doesn't list. That behavior lets older clients and tools skip unfamiliar records from newer engine versions without crashing:

```{.python .no-run}
# Commands and events round-trip through their wire discriminator; unknown types parse to None.
move_payload = MoveParty(direction=Direction.EAST).model_dump(mode="json")
assert parse_command(move_payload) == MoveParty(direction=Direction.EAST)
assert parse_command({"command_type": "some_future_command"}) is None

event_payload = moved.events[0].model_dump(mode="json")
assert parse_any_event(event_payload) == moved.events[0]
assert parse_any_event({"event_type": "some_future_event", "code": "x.y"}) is None
```

If `command_type` or `event_type` (and event `code`) matches a known class, but required fields are missing or have invalid types, the parser raises [`ContentValidationError`][osrlib.errors.ContentValidationError]:

```{.python .no-run}
# A malformed payload of a *known* type is a broken API contract: it raises, never rejects.
try:
    parse_command({"command_type": "move_party"})  # missing the required 'direction'
except ContentValidationError:
    pass
else:
    raise AssertionError("expected ContentValidationError")
```

`parse_any_event` handles both kernel and crawl events (and [`load_game`][osrlib.persistence.load_game] uses it when reading saved event logs). If your code uses only `osrlib.core`, call [`parse_event`][osrlib.core.events.parse_event] to parse kernel events.

## Attribution stamps and lifecycle commands

Every command accepts an optional `source` string that records which trigger, quest, or game subsystem issued it. `session.execute` ignores `source` during execution, but preserves it on the command in `session.command_log` across saves and replays so you can audit why a state change happened:

```{.python .no-run}
# The source stamp annotates the log and changes nothing about execution.
session.execute(AddJournalEntry(text="The lever grinds.", source="trigger:lever-east"))
assert session.command_log[-1].source == "trigger:lever-east"
assert session.view(Visibility.PLAYER).journal[-1].text == "The lever grinds."
```

The built-in [`Interpreter`][osrlib.crawl.interpreter.Interpreter] sets `source="trigger:{id}"` or `source="quest:{id}"` on every referee command it executes (see [Gates, triggers, and quests](gates-triggers-quests.md)), and records a referee note when a consequence is dropped.

Authored content and custom listeners also use referee lifecycle commands to record trigger firings, journal entries, notes, and quest state in `GameSession` (so [`replay_game`][osrlib.persistence.replay_game] can rebuild that state from `command_log` without running listeners):

- [`MarkTriggerFired`][osrlib.crawl.commands.MarkTriggerFired], [`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry], and [`RecordNote`][osrlib.crawl.commands.RecordNote]: record fired trigger IDs, player-visible journal entries, and referee notes. `MarkTriggerFired.trigger_id` accepts any string ID, even if no authored trigger uses it.
- [`ActivateQuest`][osrlib.crawl.commands.ActivateQuest], [`RevealObjective`][osrlib.crawl.commands.RevealObjective], [`CompleteObjective`][osrlib.crawl.commands.CompleteObjective], and [`CompleteQuest`][osrlib.crawl.commands.CompleteQuest]: advance quest progress in `session.quests`. Because [`PlayerView`][osrlib.crawl.views.PlayerView] reads display names and offer text from the adventure's [`QuestSpec`][osrlib.crawl.quests.QuestSpec] definitions, these commands validate `quest_id` and `objective_id` against the adventure (`session.command.unknown_quest`, `session.command.unknown_objective`) and reject out-of-order state transitions (`session.command.quest_state`). `CompleteQuest` requires the quest to be active, but does not verify the quest's completion rule or issue quest rewards. The caller (such as `Interpreter`) checks the completion rule first and issues reward commands afterward.

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
session.execute(EnterDungeon(dungeon_id="crypt"))
assert session.mode is SessionMode.EXPLORING

moved = session.execute(MoveParty(direction=Direction.EAST))
assert moved.accepted
assert moved.events[0].code == "exploration.party.moved"
lines = [format_message(event) for event in moved.events if event.visibility is Visibility.PLAYER]
assert lines == ["The party moves to (1, 0), facing east."]

rounds_before = session.clock.rounds
log_len = len(session.event_log)

blocked = session.execute(EnterDungeon(dungeon_id="crypt"))  # already exploring
assert blocked.accepted is False
assert blocked.rejections[0].code == "session.command.wrong_mode"
assert session.clock.rounds == rounds_before
assert len(session.event_log) == log_len

# Each command declares its legal modes as data - MoveParty works only while exploring.
assert MoveParty.allowed_modes == frozenset({SessionMode.EXPLORING})

# Commands and events round-trip through their wire discriminator; unknown types parse to None.
move_payload = MoveParty(direction=Direction.EAST).model_dump(mode="json")
assert parse_command(move_payload) == MoveParty(direction=Direction.EAST)
assert parse_command({"command_type": "some_future_command"}) is None

event_payload = moved.events[0].model_dump(mode="json")
assert parse_any_event(event_payload) == moved.events[0]
assert parse_any_event({"event_type": "some_future_event", "code": "x.y"}) is None

# A malformed payload of a *known* type is a broken API contract: it raises, never rejects.
try:
    parse_command({"command_type": "move_party"})  # missing the required 'direction'
except ContentValidationError:
    pass
else:
    raise AssertionError("expected ContentValidationError")

# The source stamp annotates the log and changes nothing about execution.
session.execute(AddJournalEntry(text="The lever grinds.", source="trigger:lever-east"))
assert session.command_log[-1].source == "trigger:lever-east"
assert session.view(Visibility.PLAYER).journal[-1].text == "The lever grinds."
```

## Where next

- [Views and visibility](views-and-visibility.md) - player and referee snapshots built from session state.
- [Listeners and flags](listeners-and-flags.md) - registering listeners that run after accepted commands.
- [Determinism, saves, and replay](determinism-saves-replay.md) - saving sessions and replaying `command_log` from a master seed.
- [The FastAPI pattern](../front-ends/fastapi-pattern.md) - mapping rejections and `osrlib.errors` exceptions to HTTP responses.
