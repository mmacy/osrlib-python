# Quickstart

Create a party, send it through a short dungeon corridor, then save and restore the game. The example uses a fixed seed so you can run it again with the same results.

## Install osrlib

You need Python 3.14 or later. In a Python project, install [osrlib from PyPI](https://pypi.org/project/osrlib/) with uv:

```sh
uv add osrlib
```

If you use pip instead:

```sh
pip install osrlib
```

The snippets below are parts of one program, not separate scripts. Copy [the complete program](#the-complete-program), including its imports, to run the example.

## Roll the party

Choose a name, class, and alignment for each character. [`create_character`][osrlib.core.character.create_character] rolls the ability scores, hit points, and starting gold using the options in a [`Ruleset`][osrlib.core.ruleset.Ruleset].

To make the rolls reproducible, create a [`RngStreams`][osrlib.core.rng.RngStreams] set with a master seed, then get the named character-creation stream. A stream is a sequence of random draws. Pass the same stream to both calls so the cleric's rolls continue after the fighter's:

```{.python .no-run}
# Roll two 1st-level characters; every random draw comes from a named, seeded stream.
rules = Ruleset()
creation = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
fighter = create_character(name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=rules, stream=creation)
cleric = create_character(name="Osric", class_id="cleric", alignment=Alignment.LAWFUL, ruleset=rules, stream=creation)
party = Party(members=[fighter.character, cleric.character])
```

Use an id from [`load_classes`][osrlib.data.load_classes] for `class_id`. Some classes require more choices. A magic-user or elf, for example, needs one first-level spell in `starting_spell_ids`. `create_character` returns both the character and the rolls that created it. Put `fighter.character` and `cleric.character` in a [`Party`][osrlib.crawl.party.Party], in marching order.

## Build the adventure

An [`Adventure`][osrlib.crawl.adventure.Adventure] contains a base town and one or more dungeons. Build a dungeon from levels, then combine it with the town. Here the dungeon has one level: two cells connected from west to east.

```{.python .no-run}
# The smallest adventure: a town and a one-corridor dungeon, two cells joined west-east.
crypt = DungeonSpec(
    id="crypt",
    name="The Old Crypt",
    levels=(LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)}),),
)
town = TownSpec(name="Threshold", travel_turns={"crypt": 1})
adventure = Adventure(name="A First Delve", town=town, dungeons=(crypt,))
```

The `edges` entry opens the connection between the cells. Its key, `"1,0:west"`, identifies the west side of cell `(1, 0)`, which is also the east side of `(0, 0)`. Unlisted edges are walls. The party arrives at `entrance=(0, 0)`, and the trip from town takes one exploration turn. See [Building an adventure](building-an-adventure.md) to add rooms, doors, and encounters.

## Start the session and move

Pass the party, adventure, and a seed to [`GameSession.new`][osrlib.crawl.session.GameSession.new] to create a [`GameSession`][osrlib.crawl.session.GameSession]. The session starts in town. Its seed controls the random draws during play, separately from the stream you used to create the characters.

Send an [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] command to place the party at the entrance. The session's mode changes to `exploring`, allowing movement commands:

```{.python .no-run}
# A session starts in town; entering the dungeon switches it to exploring.
session = GameSession.new(party, adventure, seed=7)
session.execute(EnterDungeon(dungeon_id="crypt"))
assert session.mode is SessionMode.EXPLORING
```

Call `session.execute` for each action. It validates the command and returns a result with an `accepted` flag, any rejections, and the events the action caused. Move east into the second cell and format all returned events for inspection:

```{.python .no-run}
# Commands in, events out: every rules resolution is a typed event with a message code.
result = session.execute(MoveParty(direction=Direction.EAST))
assert result.accepted
lines = [format_message(event) for event in result.events]
assert lines  # every event formats to a default English line
```

Check `result.accepted` before treating an action as successful. A rejected command consumes no random draws or game time and changes no state. Read `result.rejections` to find out why it was refused.

Each event describes what happened through typed fields and a message code. [`format_message`][osrlib.messages.format_message] turns those fields into a default English message, or you can render different text from the same event. This example stores the messages in `lines` without printing them.

`lines` is unfiltered output for inspection, not player-facing text. Commands can return referee-only events. Before displaying events to players, filter on `event.visibility == Visibility.PLAYER`. See [Views and visibility](../guides/views-and-visibility.md#filter-events-quests-and-the-journal) for the player/referee distinction.

## Save and load

Call [`save_game`][osrlib.persistence.save_game] to get a JSON-compatible dict containing the session state, adventure, and random-stream positions. Pass it to [`load_game`][osrlib.persistence.load_game] to resume from the same point:

```{.python .no-run}
# The whole session round-trips through JSON: same seed, same commands, same game.
document = save_game(session)
restored = load_game(document)
assert save_game(restored) == document
```

The dict remains in memory here. Your application decides where to store its JSON representation. Loading restores saved state, whereas replay executes the recorded commands from the starting party and seed. See [Determinism, saves, and replay](../guides/determinism-saves-replay.md) for the inputs and version requirements.

## The complete program

Save this program as `quickstart.py` in the project where you installed osrlib:

```python
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import EnterDungeon, MoveParty, SessionMode
from osrlib.crawl.dungeon import Direction, DungeonSpec, Edge, EdgeKind, LevelSpec
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.messages import format_message
from osrlib.persistence import load_game, save_game

# Roll two 1st-level characters; every random draw comes from a named, seeded stream.
rules = Ruleset()
creation = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
fighter = create_character(name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=rules, stream=creation)
cleric = create_character(name="Osric", class_id="cleric", alignment=Alignment.LAWFUL, ruleset=rules, stream=creation)
party = Party(members=[fighter.character, cleric.character])

# The smallest adventure: a town and a one-corridor dungeon, two cells joined west-east.
crypt = DungeonSpec(
    id="crypt",
    name="The Old Crypt",
    levels=(LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)}),),
)
town = TownSpec(name="Threshold", travel_turns={"crypt": 1})
adventure = Adventure(name="A First Delve", town=town, dungeons=(crypt,))

# A session starts in town; entering the dungeon switches it to exploring.
session = GameSession.new(party, adventure, seed=7)
session.execute(EnterDungeon(dungeon_id="crypt"))
assert session.mode is SessionMode.EXPLORING

# Commands in, events out: every rules resolution is a typed event with a message code.
result = session.execute(MoveParty(direction=Direction.EAST))
assert result.accepted
lines = [format_message(event) for event in result.events]
assert lines  # every event formats to a default English line

# The whole session round-trips through JSON: same seed, same commands, same game.
document = save_game(session)
restored = load_game(document)
assert save_game(restored) == document
```

Run it with uv:

```sh
uv run quickstart.py
```

If you installed with pip, run it with that environment's Python:

```sh
python quickstart.py
```

The program should exit without output. Its assertions check that the party enters the dungeon, the move succeeds and produces messages, and the restored session saves identically to the original.

## Where next

- Add rooms, doors, and monsters with [Building an adventure](building-an-adventure.md).
- Add conditional doors, levers, and quest objectives with [Gates, triggers, and quests](../guides/gates-triggers-quests.md).
- Handle session modes, rejected actions, and the event log with [Sessions, commands, and events](../guides/sessions-commands-events.md).
- Save games and reproduce command sequences with [Determinism, saves, and replay](../guides/determinism-saves-replay.md).
- Run a complete terminal game with [the TUI crawler](../front-ends/tui-crawler.md).
