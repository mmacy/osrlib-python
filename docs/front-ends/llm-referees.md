# LLM referees

An LLM-driven referee — a model that reads the game and decides what happens next — is a first-class consumer of osrlib, not an afterthought. The engine's shape is already the agent loop's shape: typed commands in, typed events out, a full-knowledge view to observe, and a deterministic core that makes every run reproducible. The pieces such an agent needs all ship today; a complete example agent is on the roadmap. This page assembles the pieces: [the complete program](#the-complete-program) at the end runs as written, and every fragment along the way is an excerpt of it.

## The schemas are the tool definitions

The reference section ships two raw artifacts alongside its pages: [commands.json](../reference/commands/commands.json) and [events.json](../reference/events/events.json) — the complete command and event surfaces as discriminated-union JSON Schemas, keyed on `command_type` and `event_type`. They are generated from the same registries the engine executes, so they cannot drift from what a session will actually accept and emit; [the command schema reference](../reference/commands/index.md) and [the event schema reference](../reference/events/index.md) render the same schemas page by page for human readers.

The same unions are importable — [`AnyCommand`][osrlib.crawl.commands.AnyCommand] and [`AnyEvent`][osrlib.crawl.events.AnyEvent] — so a Python agent can build its tool definitions in-process instead of shipping files around:

```{.python .no-run}
# The whole command surface as one discriminated union: a ready-made tool definition.
tools = TypeAdapter(AnyCommand).json_schema()
assert tools["discriminator"]["propertyName"] == "command_type"
assert len(tools["oneOf"]) == len(ALL_COMMAND_CLASSES)

# The event surface is the matching observation schema, keyed on event_type.
observations = TypeAdapter(AnyEvent).json_schema()
assert observations["discriminator"]["propertyName"] == "event_type"
assert len(observations["oneOf"]) == len(ALL_EVENT_CLASSES)
assert json.loads(json.dumps(tools)) == tools  # plain JSON Schema, ready for a tool registry
```

Forty-four commands, sixty-eight events, one discriminator field each — an agent framework that accepts JSON Schema tool definitions can load the command union as-is and let the model emit any command in the game, with validation for free. The loop such an agent runs is short (this is a sketch, not a framework):

```{.python .no-run}
# Sketch: the agent loop, framework left to the reader.
while session.mode is not SessionMode.GAME_OVER:
    observation = session.view(Visibility.REFEREE)
    payload = model.decide(observation, tools)  # the model emits one JSON command
    command = parse_command(payload)
    result = session.execute(command)  # rejected? that's feedback — the model reads why and retries
```

## The referee sees everything

The observation side is [`GameSession.view`][osrlib.crawl.session.GameSession.view] with [`Visibility.REFEREE`][osrlib.core.events.Visibility], which returns a [`RefereeView`][osrlib.crawl.views.RefereeView]: the full session state — party internals, monster hit points, session flags, door states, the complete event log — with exactly two things withheld, the RNG internals and the master seed (those live only in the save document). The player view is the opposite discipline, an enumerated whitelist; [Views and visibility](../guides/views-and-visibility.md) draws the line precisely.

```{.python .no-run}
# The referee view is full state — flags, monster internals — minus RNG state and the seed.
view = session.view(Visibility.REFEREE)
assert view.state["flags"] == {"ambush_sprung": True}
assert all(monster["current_hp"] >= 0 for monster in view.state["monsters"])
assert "master_seed" not in view.state and "rng_streams" not in view.state
```

The event stream carries the same privilege. [`GameSession.execute`][osrlib.crawl.session.GameSession.execute] returns its events unfiltered, and each event is stamped with a visibility: referee-visibility events carry the hidden rolls — surprise, reaction, secret-door detection — that a player-facing front end must strip at its wire (as [the FastAPI pattern](fastapi-pattern.md) does). An in-process referee agent reads them all; they are its perception of what the dice just did.

```{.python .no-run}
# The unfiltered event stream is the observation: referee events carry the hidden rolls.
codes = [event.code for event in result.events]
assert "session.monsters.spawned" in codes
assert any(event.visibility is Visibility.REFEREE for event in result.events)
```

## The authorial surface

Player commands let the model drive the party's turn; referee commands let it *run the table*. They ride the same envelope and the same rejection discipline as everything else — no separate API, just more entries in the union:

- [`SetFlag`][osrlib.crawl.commands.SetFlag] — record a durable fact (the lever was pulled, the alarm was raised) that listeners and later narration can react to; see [Listeners and flags](../guides/listeners-and-flags.md)
- [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters] and [`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty] — open an encounter at a chosen distance, by fixed count or dice
- [`GrantItem`][osrlib.crawl.commands.GrantItem], [`GrantCoins`][osrlib.crawl.commands.GrantCoins], [`AwardXP`][osrlib.crawl.commands.AwardXP] — place rewards directly
- [`SetDoorState`][osrlib.crawl.commands.SetDoorState] — rewrite any door's state anywhere: lock it, wedge it, reveal it
- [`PlaceParty`][osrlib.crawl.commands.PlaceParty] and [`AdvanceTime`][osrlib.crawl.commands.AdvanceTime] — teleport the party, advance the clock

```{.python .no-run}
# Referee commands are the authorial surface: record a fact, then spring an ambush.
session.execute(SetFlag(key="ambush_sprung", value=True))
result = session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=30))
assert result.accepted
```

The rejection contract matters as much here as it does for players: a rejected command changes nothing and explains itself with a machine-readable code (see [the rejection code reference](../reference/rejection-codes.md)), so a model that asks for something illegal gets structured feedback to correct against instead of a stack trace.

## Narrate from codes, not prose

Events never carry baked prose. Each carries a stable message `code` — a compact fact like `session.monsters.spawned` or `encounter.surprise.rolled` — plus typed fields; [the message code reference](../reference/message-codes.md) lists every shipped code with its emitting event class and default template, and each event's fields are on [its schema page](../reference/events/index.md). That is exactly what a narrator model wants: ground truth it can render freely without parsing English back into facts. When a plain default line is enough, [`format_message`][osrlib.messages.format_message] renders one for any event:

```{.python .no-run}
# Every event also renders to a default English line the model can lean on.
lines = [format_message(event) for event in result.events]
assert all(lines)
```

A practical narrator prompt sends the structured events (or their codes and fields) as the facts to narrate, and keeps the model's creativity in the telling — the dice already decided what happened.

## Determinism is the eval story

Every random draw in osrlib comes from a named stream forked from the master seed, so the same seed plus the same command sequence produces the same game, bit for bit. For agent work this is the property that makes everything else tractable: a trajectory — the seed and the list of commands the model chose — is a complete, reproducible record of a run. Re-execute it offline and you get the same events to score; change a prompt and replay the same seeds to regression-test the change; diff two models on identical dungeons. [Determinism, saves, and replay](../guides/determinism-saves-replay.md) covers the exact guarantee and its boundary (identical replays are promised only under an identical engine version).

```{.python .no-run}
# Determinism is the eval story: same seed, same commands, same trajectory.
replay = new_session(seed=7)
replay.execute(EnterDungeon(dungeon_id="crypt"))
replay.execute(SetFlag(key="ambush_sprung", value=True))
rerun = replay.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=30))
assert [e.model_dump(mode="json") for e in rerun.events] == [e.model_dump(mode="json") for e in result.events]
```

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
    EnterDungeon,
    SetFlag,
    SpawnMonsters,
)
from osrlib.crawl.dungeon import DungeonSpec, Edge, EdgeKind, LevelSpec
from osrlib.crawl.events import ALL_EVENT_CLASSES, AnyEvent
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.messages import format_message

# The whole command surface as one discriminated union: a ready-made tool definition.
tools = TypeAdapter(AnyCommand).json_schema()
assert tools["discriminator"]["propertyName"] == "command_type"
assert len(tools["oneOf"]) == len(ALL_COMMAND_CLASSES)

# The event surface is the matching observation schema, keyed on event_type.
observations = TypeAdapter(AnyEvent).json_schema()
assert observations["discriminator"]["propertyName"] == "event_type"
assert len(observations["oneOf"]) == len(ALL_EVENT_CLASSES)
assert json.loads(json.dumps(tools)) == tools  # plain JSON Schema, ready for a tool registry


def new_session(seed: int) -> GameSession:
    """One tiny two-cell dungeon and one fighter: enough engine to referee."""
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

# Referee commands are the authorial surface: record a fact, then spring an ambush.
session.execute(SetFlag(key="ambush_sprung", value=True))
result = session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=30))
assert result.accepted

# The unfiltered event stream is the observation: referee events carry the hidden rolls.
codes = [event.code for event in result.events]
assert "session.monsters.spawned" in codes
assert any(event.visibility is Visibility.REFEREE for event in result.events)

# Every event also renders to a default English line the model can lean on.
lines = [format_message(event) for event in result.events]
assert all(lines)

# The referee view is full state — flags, monster internals — minus RNG state and the seed.
view = session.view(Visibility.REFEREE)
assert view.state["flags"] == {"ambush_sprung": True}
assert all(monster["current_hp"] >= 0 for monster in view.state["monsters"])
assert "master_seed" not in view.state and "rng_streams" not in view.state

# Determinism is the eval story: same seed, same commands, same trajectory.
replay = new_session(seed=7)
replay.execute(EnterDungeon(dungeon_id="crypt"))
replay.execute(SetFlag(key="ambush_sprung", value=True))
rerun = replay.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=30))
assert [e.model_dump(mode="json") for e in rerun.events] == [e.model_dump(mode="json") for e in result.events]
```

## Where next

- [Views and visibility](../guides/views-and-visibility.md) — the referee/player projection line this page builds on.
- [Determinism, saves, and replay](../guides/determinism-saves-replay.md) — the reproducibility guarantee behind the eval story.
- [The FastAPI pattern](fastapi-pattern.md) — the other side of the doctrine: serving players who must *not* see what the referee sees.
- [The message code reference](../reference/message-codes.md) — every code an event can carry, with its default English template.
