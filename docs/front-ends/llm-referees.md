# LLM referees

You can connect a language model to [`GameSession`][osrlib.crawl.session.GameSession] as an in-process referee or narrator: expose the command JSON Schema in a tool definition, pass [`RefereeView`][osrlib.crawl.views.RefereeView] and unfiltered [`Event`][osrlib.core.events.Event] payloads as observations, execute the JSON commands the model returns, and replay recorded command sequences from the same master seed. To run every snippet on this page together on Python 3.14+ with `osrlib` installed (see the [Quickstart](../getting-started/quickstart.md); no LLM SDK or API key is required), copy [the complete program](#the-complete-program).

## Define tools from the JSON Schemas

The documentation build publishes discriminated-union JSON Schemas for the full command and event registries at [commands.json](../reference/commands/commands.json) (discriminated on `command_type`) and [events.json](../reference/events/events.json) (discriminated on `event_type`), alongside the human-readable [command schema reference](../reference/commands/index.md) and [event schema reference](../reference/events/index.md).

In Python, generate those identical JSON Schemas from [`AnyCommand`][osrlib.crawl.commands.AnyCommand] and [`AnyEvent`][osrlib.crawl.events.AnyEvent] with Pydantic's [`TypeAdapter`][pydantic.type_adapter.TypeAdapter]. Because `TypeAdapter(AnyCommand).json_schema()` emits a top-level `oneOf` union, wrap it under a `"command"` property when registering a single tool with LLM APIs that require `parameters` to have `"type": "object"` (or register each class in [`ALL_COMMAND_CLASSES`][osrlib.crawl.commands.ALL_COMMAND_CLASSES] via `cls.model_json_schema()` as its own tool):

```{.python .no-run}
command_schema = TypeAdapter(AnyCommand).json_schema()
assert command_schema["discriminator"]["propertyName"] == "command_type"
assert len(command_schema["oneOf"]) == len(ALL_COMMAND_CLASSES)

tool_definition = {
    "name": "execute_command",
    "description": "Execute one player or referee command against the session.",
    "parameters": {
        "type": "object",
        "properties": {"command": command_schema},
        "required": ["command"],
    },
}

observations = TypeAdapter(AnyEvent).json_schema()
assert observations["discriminator"]["propertyName"] == "event_type"
assert len(observations["oneOf"]) == len(ALL_EVENT_CLASSES)
assert json.loads(json.dumps(tool_definition)) == tool_definition
```

Every model property in `command_schema` and `observations` includes a `description` generated from its attribute docstring (with mkdocstrings cross-references such as ``[`MoveParty`][osrlib.crawl.commands.MoveParty]`` reduced to plain `` `MoveParty` `` labels), so an LLM reading `tool_definition` sees what each parameter controls and emits arguments such as `{"command": {"command_type": "move_party", "direction": "east"}}`.

Each command, event, and enum definition has a `description` too, written as CommonMark. It is the class docstring's text before its first section, followed by the command's `Modes:`, `Rejections:`, and `Events:` sections when it has them, so an LLM reads which session modes accept the command, the codes that refuse it, and the events it emits. The docstring's `Attributes:`, `Raises:`, and `Examples:` sections are left out of the description, because each property has its own `description` and the other two describe Python use.

On each step of your agent loop, stop once [`SessionMode.terminal`][osrlib.crawl.commands.SessionMode] becomes `True` (`game_over` or `victory`) or a `max_steps` retry cap is reached, validate the model's `command` dict with [`parse_command`][osrlib.crawl.commands.parse_command], and pass structured validation errors, [`Rejection`][osrlib.core.validation.Rejection] codes (see [the rejection code reference](../reference/rejection-codes.md)), or emitted events into the next `decide(observation, feedback)` call:

```{.python .no-run}
def run_agent_steps(session: GameSession, decide, *, max_steps: int = 10) -> CommandResult | None:
    feedback: dict | None = None
    last_result: CommandResult | None = None
    for _ in range(max_steps):
        if session.mode.terminal:
            break
        observation = session.view(Visibility.REFEREE).model_dump(mode="json")
        call = decide(observation, feedback)
        if call is None:
            break
        payload = call.get("command")
        if not isinstance(payload, dict):
            feedback = {"error": "invalid_payload", "detail": "expected a command object"}
            continue
        try:
            command = parse_command(payload)
        except ContentValidationError as error:
            feedback = {"error": "invalid_payload", "detail": str(error)}
            continue
        if command is None:
            feedback = {"error": "unknown_command", "command_type": payload.get("command_type")}
            continue
        last_result = session.execute(command)
        if not last_result.accepted:
            feedback = {"rejections": [r.model_dump(mode="json") for r in last_result.rejections]}
            continue
        feedback = {"events": [e.model_dump(mode="json") for e in last_result.events]}
    return last_result
```

`parse_command` returns `None` for an unknown `command_type` and raises [`ContentValidationError`][osrlib.errors.ContentValidationError] when a known command has invalid or missing fields. Once `command` parses, `session.execute(command)` checks game-rule preconditions and returns `CommandResult(accepted=False)` without consuming RNG draws, advancing the clock, or mutating state, so feeding `feedback` back to the model lets it self-correct without corrupting the session.

## Modify the world with referee commands

Player commands resolve the party's actions, whereas referee commands let your agent adjudicate rulings, spawn encounters, grant or remove items, and update world state. Both belong to `AnyCommand` and return the same [`CommandResult`][osrlib.crawl.commands.CommandResult] envelope:

- **Flags and seeded rolls**: [`SetFlag`][osrlib.crawl.commands.SetFlag] writes a `str`, `int`, or `bool` entry to `session.flags` for gates, triggers, quests, or later prompts to inspect (see [Listeners and flags](../guides/listeners-and-flags.md)). [`RollDice`][osrlib.crawl.commands.RollDice] rolls a dice expression (such as `"1d20"` or `"3d6+2"`) on [`ADJUDICATION_STREAM`][osrlib.crawl.session.ADJUDICATION_STREAM] so ad-hoc referee rulings stay deterministic, logged, and isolated from combat and exploration RNG streams.
- **Encounters**: [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters] and [`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty] start an encounter at `distance_feet` by fixed count or dice expression (up to 100 creatures). Executing `SpawnMonsters` during an active `encounter` or `battle` adds a monster group to the ongoing fight instead (for example, reinforcements summoned mid-battle). [`EndEncounter`][osrlib.crawl.commands.EndEncounter] closes an `encounter` or `battle` with `outcome="parley"` or `outcome="departed"` and returns to `exploring`; pass `resolve_area=True` so a keyed area's monsters do not respawn on re-entry.
- **Rewards, tolls, and identification**: [`GrantItem`][osrlib.crawl.commands.GrantItem], [`GrantCoins`][osrlib.crawl.commands.GrantCoins], and [`AwardXP`][osrlib.crawl.commands.AwardXP] grant mundane or magic items, coins, or XP to a party member. [`RemoveItem`][osrlib.crawl.commands.RemoveItem] and [`RemoveCoins`][osrlib.crawl.commands.RemoveCoins] deduct items or coins (such as for a bribe or custom toll). [`IdentifyItem`][osrlib.crawl.commands.IdentifyItem] reveals a carried magic item's true identity (and any curse) without requiring the character to use it.
- **Party roster**: [`AddMember`][osrlib.crawl.commands.AddMember] appends a fresh character (`id=None`, with no active conditions, magic items, or valuables) to the back of the marching order in `town` or `exploring` and reports the assigned `character-NNNN` ID on [`MemberAddedEvent`][osrlib.crawl.events.MemberAddedEvent]. [`RemoveMember`][osrlib.crawl.commands.RemoveMember] permanently retires a member's ID in `town` or `exploring` (at least one standing member must remain). Pass another active member in `give_to` to transfer carried gear and coins; omitting `give_to` rejects with `party.remove.carries_quest_item` whenever the departing member carries an item referenced by an uncompleted quest, unfired trigger, or gate.
- **Doors, movement, and time**: [`SetDoorState`][osrlib.crawl.commands.SetDoorState] updates a door's `open`, `wedged`, `discovered`, or `unlocked` state anywhere in the adventure. [`PlaceParty`][osrlib.crawl.commands.PlaceParty] teleports the party to town or a dungeon cell without triggering entry checks (legal in `town`, `exploring`, and `game_over`). [`AdvanceTime`][osrlib.crawl.commands.AdvanceTime] advances the clock by rounds, turns, or days (up to 28 days per command), expiring timed effects and charging daily provisions without rolling wandering checks.
- **Journal, notes, and triggers**: [`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry] appends player-visible story text to `PlayerView.journal`, [`RecordNote`][osrlib.crawl.commands.RecordNote] logs a referee-only [`NoteRecordedEvent`][osrlib.crawl.events.NoteRecordedEvent], and [`MarkTriggerFired`][osrlib.crawl.commands.MarkTriggerFired] records a trigger ID in `session.fired_triggers`.
- **Quest state**: [`ActivateQuest`][osrlib.crawl.commands.ActivateQuest], [`RevealObjective`][osrlib.crawl.commands.RevealObjective], [`CompleteObjective`][osrlib.crawl.commands.CompleteObjective], and [`CompleteQuest`][osrlib.crawl.commands.CompleteQuest] advance quests defined on `Adventure.quests`. `CompleteQuest` marks an active quest completed (and enters `victory` if `concludes_adventure=True`), but never issues `QuestSpec.rewards`; if your agent completes a quest manually rather than through [`Interpreter`][osrlib.crawl.interpreter.Interpreter], issue reward commands separately.

After starting `session = new_session(seed=7)` and entering `"crypt"` (see [the complete program](#the-complete-program)), the referee sets a flag and starts an encounter:

```{.python .no-run}
# Set a session flag, then spawn an encounter at 30 feet.
session.execute(SetFlag(key="ambush_sprung", value=True))
result = session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=30))
assert result.accepted
```

## Observe full state and hidden rolls

[`GameSession.execute`][osrlib.crawl.session.GameSession.execute] returns all emitted events in `result.events` without filtering by [`Visibility`][osrlib.core.events.Visibility]. Unlike a player-facing server (which strips `Visibility.REFEREE` events as shown in [the FastAPI pattern](fastapi-pattern.md)), an in-process referee agent reads the full stream, including hidden surprise, reaction, morale, and secret-door detection rolls:

```{.python .no-run}
# Unfiltered events include referee-only rolls alongside player events.
codes = [event.code for event in result.events]
assert "session.monsters.spawned" in codes
assert any(event.visibility is Visibility.REFEREE for event in result.events)
```

To inspect current session state between commands, call [`session.view(Visibility.REFEREE)`][osrlib.crawl.session.GameSession.view]. The returned [`RefereeView`][osrlib.crawl.views.RefereeView] exposes typed fields for the full party, `monsters` (including `current_hp`), `npcs`, `flags`, `fired_triggers`, `quests` (including inactive quests and hidden objectives), `dungeon_state`, `ledger`, `command_log`, and `event_log`, omitting only `master_seed` and `rng_streams` (which appear only in save documents). Serialize the snapshot for a referee prompt with `view.model_dump(mode="json")` (see [Views and visibility](../guides/views-and-visibility.md)):

```{.python .no-run}
# RefereeView includes flags and monster hit points, omitting RNG state and master_seed.
view = session.view(Visibility.REFEREE)
assert view.flags == {"ambush_sprung": True}
assert all(monster.current_hp >= 0 for monster in view.monsters)
dumped = view.model_dump()
assert "master_seed" not in dumped and "rng_streams" not in dumped
```

## Narrate from event codes

Events never contain engine-generated English prose. Instead, every event provides a dotted message `code` (such as `encounter.started` or `encounter.surprise.rolled`) and typed fields (see [the event schema reference](../reference/events/index.md) and [the message code reference](../reference/message-codes.md)).

Separate the context you pass to private referee adjudication from the context you pass to player-facing narration:

- **Referee adjudication**: Pass `session.view(Visibility.REFEREE)` and unfiltered `result.events` so the referee model sees hidden rolls, monster hit points, and flags.
- **Player-facing narration**: Filter events to `event.visibility is Visibility.PLAYER` and pass `session.view(Visibility.PLAYER)` so hidden surprise totals, reaction rolls, and monster hit points cannot leak into prose shown to players.

When you want a default English summary alongside (or instead of) the raw event dicts, call [`format_message`][osrlib.messages.format_message] on the player-visible events, which formats each event and appends any authored narrative beat attached to it:

```{.python .no-run}
# Filter to Visibility.PLAYER before generating player-facing narration.
player_lines = [format_message(event) for event in result.events if event.visibility is Visibility.PLAYER]
print("\n".join(player_lines))
# Encounter: 2 goblins at 30'.
# The monsters' bearing: indifferent.
```

## Narrate authored content

When refereeing an adventure that uses gates, triggers, and quests (see [Gates, triggers, and quests](../guides/gates-triggers-quests.md)), coordinate your agent with the adventure's authored mechanics and prose:

- **Register `Interpreter` for authored mechanics**: Call `session.register_listener(Interpreter(session))` after creating or loading the session so [`Interpreter`][osrlib.crawl.interpreter.Interpreter] evaluates triggers, advances quests, records `fired_triggers`, and issues quest rewards automatically as logged commands without prompt-side bookkeeping.
- **Deliver authored beats verbatim**: Quest `offer` and `completion` beats, objective `progress` beats, and gate `success` and `refusal` beats arrive on player-visible events or in `rejections[].params.refusal`, along with optional `speaker` attribution (such as `"the temple almoner"`). Present those beats verbatim in the speaker's voice and write the model's narration around them rather than paraphrasing them.
- **Separate agent commands with `Command.source`**: Set [`Command.source`][osrlib.crawl.commands.Command] on referee commands the model issues (for example, `source="agent:referee"`). Because `Interpreter` stamps its commands with `trigger:{id}` or `quest:{id}`, inspecting `command.source` in `session.command_log` distinguishes the model's choices from authored consequences during evaluation.
- **Read `guidance` as narrator direction, never player text**: Read `guidance` from `session.adventure` (or `RefereeView.adventure`) as staging notes rather than text to quote to players. Authors attach object-specific direction on [`NarrativeBlock.guidance`][osrlib.crawl.narrative.NarrativeBlock] and level-wide atmosphere on [`LevelSpec.guidance`][osrlib.crawl.dungeon.LevelSpec] (such as level 1 of [the TUI crawler's](tui-crawler.md) barrow), neither of which appears in events or `PlayerView`:

```{.python .no-run}
--8<-- "examples/tui_crawler/content.py:level-guidance"
```

## Replay trajectories for evaluation

Every random draw in `osrlib` comes from a named stream forked from the session's master seed, so executing the same accepted commands from the same starting party, adventure, `Ruleset`, and seed under the same `engine_version` produces identical events and state (see [Determinism, saves, and replay](../guides/determinism-saves-replay.md)):

```{.python .no-run}
# Repeat the same commands from the same seed and compare serialized events.
replay = new_session(seed=7)
replay.execute(EnterDungeon(dungeon_id="crypt"))
replay.execute(SetFlag(key="ambush_sprung", value=True))
rerun = replay.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=30))
assert [e.model_dump(mode="json") for e in rerun.events] == [e.model_dump(mode="json") for e in result.events]
```

Keep two distinctions in mind when designing an evaluation harness:

- **Replaying a recorded trajectory vs. comparing prompts**: Re-executing a saved seed and command list reproduces the exact events offline without calling an LLM, which is useful for regression-testing your scoring or narration pipeline. To compare two prompts or models, start each candidate on a fresh `new_session(seed=...)` with the same seed, party, and adventure and let each model choose its own commands against identical initial rolls (once a model makes a different draw on an RNG stream, subsequent rolls on that stream diverge while independent streams remain aligned).
- **Recording rejected or malformed tool calls**: `session.command_log` stores only accepted commands. If your benchmark scores invalid-JSON rates or rule rejections, record each raw model tool call and its `feedback` dict in your harness log alongside `session.command_log`.

## The complete program

```python
import json

from pydantic import TypeAdapter

from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.events import Visibility
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import (
    ALL_COMMAND_CLASSES,
    AnyCommand,
    CommandResult,
    EnterDungeon,
    SetFlag,
    SpawnMonsters,
    parse_command,
)
from osrlib.crawl.dungeon import DungeonSpec, Edge, EdgeKind, LevelSpec
from osrlib.crawl.events import ALL_EVENT_CLASSES, AnyEvent
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.errors import ContentValidationError
from osrlib.messages import format_message

command_schema = TypeAdapter(AnyCommand).json_schema()
assert command_schema["discriminator"]["propertyName"] == "command_type"
assert len(command_schema["oneOf"]) == len(ALL_COMMAND_CLASSES)

tool_definition = {
    "name": "execute_command",
    "description": "Execute one player or referee command against the session.",
    "parameters": {
        "type": "object",
        "properties": {"command": command_schema},
        "required": ["command"],
    },
}

observations = TypeAdapter(AnyEvent).json_schema()
assert observations["discriminator"]["propertyName"] == "event_type"
assert len(observations["oneOf"]) == len(ALL_EVENT_CLASSES)
assert json.loads(json.dumps(tool_definition)) == tool_definition


def run_agent_steps(session: GameSession, decide, *, max_steps: int = 10) -> CommandResult | None:
    feedback: dict | None = None
    last_result: CommandResult | None = None
    for _ in range(max_steps):
        if session.mode.terminal:
            break
        observation = session.view(Visibility.REFEREE).model_dump(mode="json")
        call = decide(observation, feedback)
        if call is None:
            break
        payload = call.get("command")
        if not isinstance(payload, dict):
            feedback = {"error": "invalid_payload", "detail": "expected a command object"}
            continue
        try:
            command = parse_command(payload)
        except ContentValidationError as error:
            feedback = {"error": "invalid_payload", "detail": str(error)}
            continue
        if command is None:
            feedback = {"error": "unknown_command", "command_type": payload.get("command_type")}
            continue
        last_result = session.execute(command)
        if not last_result.accepted:
            feedback = {"rejections": [r.model_dump(mode="json") for r in last_result.rejections]}
            continue
        feedback = {"events": [e.model_dump(mode="json") for e in last_result.events]}
    return last_result


def new_session(seed: int) -> GameSession:
    """Build a two-cell dungeon and a 1st-level fighter."""
    rules = Ruleset()
    stream = RngStreams(master_seed=seed).get(CHARACTER_CREATION_STREAM)
    hero = create_character(
        name="Hild",
        class_id="fighter",
        alignment=Alignment.LAWFUL,
        ruleset=rules,
        stream=stream,
    )
    level = LevelSpec(
        number=1,
        width=2,
        height=1,
        entrance=(0, 0),
        edges={"1,0:west": Edge(kind=EdgeKind.OPEN)},
    )
    crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
    town = TownSpec(name="Threshold", travel_turns={"crypt": 1})
    adventure = Adventure(name="A First Delve", town=town, dungeons=(crypt,))
    return GameSession.new(Party(members=[hero.character]), adventure, seed=seed)


session = new_session(seed=7)
session.execute(EnterDungeon(dungeon_id="crypt"))

# Exercise the validation and rejection feedback loop with a deterministic stub model:
# 1) unknown command -> 2) invalid payload -> 3) blocked wall move -> 4) flag -> 5) ambush -> 6) stop.
scripted_calls = iter(
    [
        {"command": {"command_type": "unknown_move"}},
        {"command": {"command_type": "move_party", "direction": "up"}},
        {"command": {"command_type": "move_party", "direction": "north"}},
        {"command": {"command_type": "set_flag", "key": "ambush_sprung", "value": True}},
        {"command": {"command_type": "spawn_monsters", "template_id": "goblin", "count_fixed": 2, "distance_feet": 30}},
        None,
    ]
)
seen_feedback: list[dict | None] = []


def stub_decide(_observation: dict, feedback: dict | None) -> dict | None:
    seen_feedback.append(feedback)
    return next(scripted_calls)


result = run_agent_steps(session, stub_decide)
assert result is not None and result.accepted
assert seen_feedback[1] == {"error": "unknown_command", "command_type": "unknown_move"}
assert seen_feedback[2] is not None and seen_feedback[2]["error"] == "invalid_payload"
assert seen_feedback[3] is not None and seen_feedback[3]["rejections"][0]["code"] == "exploration.move.blocked"

# Unfiltered events include referee-only rolls alongside player events.
codes = [event.code for event in result.events]
assert "session.monsters.spawned" in codes
assert any(event.visibility is Visibility.REFEREE for event in result.events)

# Filter to Visibility.PLAYER before generating player-facing narration.
player_lines = [format_message(event) for event in result.events if event.visibility is Visibility.PLAYER]
print("\n".join(player_lines))
# Encounter: 2 goblins at 30'.
# The monsters' bearing: indifferent.

# RefereeView includes flags and monster hit points, omitting RNG state and master_seed.
view = session.view(Visibility.REFEREE)
assert view.flags == {"ambush_sprung": True}
assert all(monster.current_hp >= 0 for monster in view.monsters)
dumped = view.model_dump()
assert "master_seed" not in dumped and "rng_streams" not in dumped

# Repeat the same commands from the same seed and compare serialized events.
replay = new_session(seed=7)
replay.execute(EnterDungeon(dungeon_id="crypt"))
replay.execute(SetFlag(key="ambush_sprung", value=True))
rerun = replay.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=30))
assert [e.model_dump(mode="json") for e in rerun.events] == [e.model_dump(mode="json") for e in result.events]
```

## Where next

- [Gates, triggers, and quests](../guides/gates-triggers-quests.md) - authoring quests, triggers, narrative beats, and narrator `guidance`.
- [Views and visibility](../guides/views-and-visibility.md) - comparing `RefereeView` and `PlayerView` fields.
- [Determinism, saves, and replay](../guides/determinism-saves-replay.md) - saving sessions and replaying `command_log` under a fixed seed.
- [The FastAPI pattern](fastapi-pattern.md) - serving untrusted player clients with `Visibility.PLAYER` filtering.
- [The message code reference](../reference/message-codes.md) - event message codes and default English templates.
