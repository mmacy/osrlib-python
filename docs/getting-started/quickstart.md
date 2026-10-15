# Quickstart

Install [osrlib from PyPI](https://pypi.org/project/osrlib/). Python ≥ 3.14 required.

```sh
uv add osrlib
```

or, with pip:

```sh
pip install osrlib
```

## Roll the party

[`create_character`][osrlib.core.character.create_character] runs the B/X procedure: ability scores, class, hit points, starting gold. Randomness flows through named streams forked from a master seed — same seed, same rolls.

```{.python .no-run}
# Roll two 1st-level characters; every random draw comes from a named, seeded stream.
rules = Ruleset()
creation = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
fighter = create_character(name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=rules, stream=creation)
cleric = create_character(name="Osric", class_id="cleric", alignment=Alignment.LAWFUL, ruleset=rules, stream=creation)
party = Party(members=[fighter.character, cleric.character])
```

`class_id` takes any id from [`load_classes`][osrlib.data.load_classes]. The result wraps the character with its rolls; the party takes `fighter.character`.

## Build the adventure

An [`Adventure`][osrlib.crawl.adventure.Adventure] is a town plus dungeons. This one is a two-cell corridor.

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

`"1,0:west"` keys the west side of cell `(1, 0)`. See [Building an adventure](building-an-adventure.md) for the full geometry.

## Start the session and move

A [`GameSession`][osrlib.crawl.session.GameSession] starts in town. [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] places the party at the entrance and switches the session to `exploring`.

```{.python .no-run}
# A session starts in town; entering the dungeon switches it to exploring.
session = GameSession.new(party, adventure, seed=7)
session.execute(EnterDungeon(dungeon_id="crypt"))
assert session.mode is SessionMode.EXPLORING
```

Commands change the game state. Each returns typed events:

```{.python .no-run}
# Commands in, events out: every rules resolution is a typed event with a message code.
result = session.execute(MoveParty(direction=Direction.EAST))
assert result.accepted
lines = [format_message(event) for event in result.events]
assert lines  # every event formats to a default English line
```

A rejected command changes nothing. Events carry structured fields and a message code, never baked prose. [`format_message`][osrlib.messages.format_message] renders the default English line; your front end can supply its own.

## Save and load

The session serializes to a JSON-compatible dict. Loading restores it from that state alone:

```{.python .no-run}
# The whole session round-trips through JSON: same seed, same commands, same game.
document = save_game(session)
restored = load_game(document)
assert save_game(restored) == document
```

For replay — rebuilding the same session by re-executing the command log — see [Determinism, saves, and replay](../guides/determinism-saves-replay.md).

## The complete program

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

## Where next

- [Building an adventure](building-an-adventure.md) - the dungeon grid, edges, keyed areas, and content.
- [Gates, triggers, and quests](../guides/gates-triggers-quests.md) - keyed doors, levers, quests.
- [Sessions, commands, and events](../guides/sessions-commands-events.md) - the command loop: modes, rejections, the event log.
- [Determinism, saves, and replay](../guides/determinism-saves-replay.md) - the seed guarantee and how saves and replay agree.
- [The TUI crawler](../front-ends/tui-crawler.md) - a complete game built on this.
