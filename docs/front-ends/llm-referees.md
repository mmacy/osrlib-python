# LLM referees

osrlib is designed to work with an LLM-driven referee (a model that reads the game and decides what happens next). The engine fits an agent loop: the agent sends typed commands, reads typed events, and observes a full-knowledge view, and the deterministic core makes every run reproducible. The library includes everything such an agent uses: the schemas, the referee commands and view, authored content, and the determinism guarantee. [The complete program](#the-complete-program) at the end runs as written, and every snippet along the way comes from it.

## The schemas are the tool definitions

The reference section includes two raw schema files beside its pages: [commands.json](../reference/commands/commands.json) and [events.json](../reference/events/events.json). Each is a JSON Schema for a discriminated union. The command schema uses `command_type` as its discriminator, and the event schema uses `event_type`. The docs build generates both from the same registries the engine uses, so they always match what a session accepts and emits. [The command schema reference](../reference/commands/index.md) and [the event schema reference](../reference/events/index.md) render the same schemas page by page for human readers.

The same unions are importable as [`AnyCommand`][osrlib.crawl.commands.AnyCommand] and [`AnyEvent`][osrlib.crawl.events.AnyEvent], so a Python agent can build its tool definitions in-process instead of copying files:

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

The whole command surface and the whole event surface have one discriminator field each. An agent framework that accepts JSON Schema tool definitions can load the command union as-is and let the model emit any command in the game, and [`parse_command`][osrlib.crawl.commands.parse_command] validates each one. The loop such an agent runs is short (this is a sketch, not a framework):

```{.python .no-run}
# Sketch: the agent loop, framework left to the reader.
while not session.mode.terminal:  # the party fell, or the adventure is won
    observation = session.view(Visibility.REFEREE)
    payload = model.decide(observation, tools)  # the model emits one JSON command
    command = parse_command(payload)
    result = session.execute(command)  # rejected? that's feedback — the model reads why and retries
```

## The referee sees everything

The observation side is [`GameSession.view`][osrlib.crawl.session.GameSession.view] with [`Visibility.REFEREE`][osrlib.core.events.Visibility], which returns a [`RefereeView`][osrlib.crawl.views.RefereeView]. The referee view contains the full session state (party internals, monster hit points, session flags, door states, the complete event log), except the RNG internals and the master seed. Only the save document contains those two. Each group is a separate field, typed with the model the session uses, so an agent reads `view.monsters[0].current_hp` and `view.flags["key"]` off it and serializes the whole observation with `view.model_dump(mode="json")`. The player view works the opposite way: it's an enumerated whitelist. [Views and visibility](../guides/views-and-visibility.md) describes exactly what each view includes.

```{.python .no-run}
# The referee view is full state — flags, monster internals — minus RNG state and the seed.
view = session.view(Visibility.REFEREE)
assert view.flags == {"ambush_sprung": True}
assert all(monster.current_hp >= 0 for monster in view.monsters)
dumped = view.model_dump()
assert "master_seed" not in dumped and "rng_streams" not in dumped
```

[`GameSession.execute`][osrlib.crawl.session.GameSession.execute] returns its events unfiltered too, and each event has a visibility. Referee-visibility events include the hidden rolls (surprise, reaction, secret-door detection) that a player-facing front end must remove before it sends events to the player, as [the FastAPI pattern](fastapi-pattern.md) does. An in-process referee agent reads them all, because they show the agent what the dice just did.

```{.python .no-run}
# The unfiltered event stream is the observation: referee events carry the hidden rolls.
codes = [event.code for event in result.events]
assert "session.monsters.spawned" in codes
assert any(event.visibility is Visibility.REFEREE for event in result.events)
```

## The authorial surface

Player commands let the model drive the party's turn. Referee commands let it *run the table*. They use the same result envelope and the same rejection rules as player commands. They aren't a separate API, only more entries in the union:

- [`SetFlag`][osrlib.crawl.commands.SetFlag] - record a durable fact (the lever was pulled, the alarm was raised) that listeners and later narration can react to. See [Listeners and flags](../guides/listeners-and-flags.md)
- [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters] and [`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty] - open an encounter at a chosen distance, by fixed count or dice. During an encounter or a battle, `SpawnMonsters` adds a group to the fight instead, like the animals a lycanthrope summons
- [`EndEncounter`][osrlib.crawl.commands.EndEncounter] - close the encounter when the party talks its way past or walks away, and optionally mark a keyed area resolved so its monsters don't come back
- [`GrantItem`][osrlib.crawl.commands.GrantItem], [`GrantCoins`][osrlib.crawl.commands.GrantCoins], [`AwardXP`][osrlib.crawl.commands.AwardXP] - place rewards directly, magic items included
- [`RemoveItem`][osrlib.crawl.commands.RemoveItem], [`RemoveCoins`][osrlib.crawl.commands.RemoveCoins] - take items or coins away: a price, a toll, armour a shop buys back
- [`AddMember`][osrlib.crawl.commands.AddMember] - bring a new character into the party in town or while exploring, like a replacement for a fallen member. The new member joins at the back of the marching order, and the event gives their id
- [`RemoveMember`][osrlib.crawl.commands.RemoveMember] - take a character out of the party for good in town or while exploring, like one who died too long ago for *raise dead*. Name another member in `give_to` to keep what the character carries in the party. The removed id is never used again
- [`SetDoorState`][osrlib.crawl.commands.SetDoorState] - rewrite any door's state anywhere: lock it, wedge it, reveal it
- [`PlaceParty`][osrlib.crawl.commands.PlaceParty] and [`AdvanceTime`][osrlib.crawl.commands.AdvanceTime] - teleport the party, advance the clock
- [`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry] and [`RecordNote`][osrlib.crawl.commands.RecordNote] - what the agent records in the game world: a journal entry is written for the players and appears in their view, and a note is for the referee only and stays out of the player view
- [`ActivateQuest`][osrlib.crawl.commands.ActivateQuest], [`RevealObjective`][osrlib.crawl.commands.RevealObjective], [`CompleteObjective`][osrlib.crawl.commands.CompleteObjective], [`CompleteQuest`][osrlib.crawl.commands.CompleteQuest] - advance authored quest state by hand. `CompleteQuest` pays nothing: whoever completes a quest issues its rewards afterwards, as the interpreter does, so a hand-issued completion leaves the party unpaid unless the agent issues the rewards too

```{.python .no-run}
# Referee commands are the authorial surface: record a fact, then spring an ambush.
session.execute(SetFlag(key="ambush_sprung", value=True))
result = session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=30))
assert result.accepted
```

The rejection rules work the same way for referee commands: a rejected command changes nothing, and the result includes a machine-readable rejection code (see [the rejection code reference](../reference/rejection-codes.md)), so a model that asks for something illegal gets structured feedback to correct against instead of a stack trace.

## Narrate from codes, not prose

The engine never writes English text into events. Each has a stable message `code` (a compact fact like `session.monsters.spawned` or `encounter.surprise.rolled`) plus typed fields. [The message code reference](../reference/message-codes.md) lists every code with the event class that emits it and its default template, and each event's fields are on [its schema page](../reference/events/index.md). A narrator model gets facts it can render freely, without parsing English back into facts. The one kind of English an event does include is *authored* narrative: a beat the adventure's author wrote in a structured field. [Narrating authored content](#narrating-authored-content) below covers authored beats separately from the text the model writes. When a plain default line is enough, [`format_message`][osrlib.messages.format_message] renders one for any event, appending any authored beat verbatim:

```{.python .no-run}
# Every event also renders to a default English line the model can lean on.
lines = [format_message(event) for event in result.events]
assert all(lines)
```

In a practical narrator prompt, send the structured events (or their codes and fields) as the facts to narrate, and leave the model to choose how to tell them. The engine has already rolled the dice and resolved what happened.

## Narrating authored content

An adventure written for the authored layer ([Gates, triggers, and quests](../guides/gates-triggers-quests.md)) includes material written for a narrating referee, and an agent that referees such an adventure should use all of it.

**Register the library's [`Interpreter`][osrlib.crawl.interpreter.Interpreter] and do no bookkeeping.** Call `session.register_listener(Interpreter(session))` once after you build the session, and again after a load. The interpreter then fires the triggers, advances the quests, records the fired-marks, and issues the rewards, all as ordinary logged commands. The agent referees, and the interpreter runs what the adventure's author set up.

**`guidance` is direction for the narrator, not text to read out.** [`NarrativeBlock`][osrlib.crawl.narrative.NarrativeBlock] has a `guidance` field on any authored object, and [`LevelSpec.guidance`][osrlib.crawl.dungeon.LevelSpec] contains ambience for the whole level that isn't attached to any object. The guidance for the TUI barrow's first level is:

```{.python .no-run}
--8<-- "examples/tui_crawler/content.py:level-guidance"
```

A referee-side narrator reads guidance from the adventure document it's refereeing and never prints it verbatim. The narrator trusts guidance as it trusts an area's description, which already goes into narration. No event or view includes guidance. It's the author talking to the narrator.

**Deliver authored beats as written.** A quest's offer and completion, an objective's progress, a gate's success and refusal arrive as structured fields on player-visible events and rejections, with `speaker` attribution when the author wrote one ("the temple almoner"). Deliver them as written, in the speaker's voice, and write the model's narration around them instead of replacing them.

**[`Command.source`][osrlib.crawl.commands.Command] shows which commands came from the agent.** The interpreter stamps every command it issues with `trigger:{id}` or `quest:{id}`. If the agent stamps its referee commands differently, or leaves them unstamped, you can tell from the log which commands the agent chose and which the adventure caused. When you replay a trajectory, you can tell which grants were the model's ideas.

## Reproducible runs for evaluation

Every random draw in osrlib comes from a named stream forked from the master seed, so the same seed plus the same command sequence produces the same game, bit for bit. For agent work, that means a trajectory (the seed and the list of commands the model chose) is a complete, reproducible record of a run. Re-execute it offline and you get the same events to score. Change a prompt and replay the same seeds to regression-test the change. Compare two models on identical dungeons. [Determinism, saves, and replay](../guides/determinism-saves-replay.md) covers the guarantee and its limit: replays are identical only under the same engine version.

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
assert view.flags == {"ambush_sprung": True}
assert all(monster.current_hp >= 0 for monster in view.monsters)
dumped = view.model_dump()
assert "master_seed" not in dumped and "rng_streams" not in dumped

# Determinism is the eval story: same seed, same commands, same trajectory.
replay = new_session(seed=7)
replay.execute(EnterDungeon(dungeon_id="crypt"))
replay.execute(SetFlag(key="ambush_sprung", value=True))
rerun = replay.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=30))
assert [e.model_dump(mode="json") for e in rerun.events] == [e.model_dump(mode="json") for e in result.events]
```

## Where next

- [Gates, triggers, and quests](../guides/gates-triggers-quests.md) - how to author the guidance and beats described on this page.
- [Views and visibility](../guides/views-and-visibility.md) - what the referee view and the player view each include.
- [Determinism, saves, and replay](../guides/determinism-saves-replay.md) - the guarantee that makes a run reproducible.
- [The FastAPI pattern](fastapi-pattern.md) - the opposite case: serving players who must *not* see what the referee sees.
- [The message code reference](../reference/message-codes.md) - every code an event can have, with its default English template.
