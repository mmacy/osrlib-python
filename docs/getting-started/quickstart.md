# Quickstart

This page runs the whole loop once: roll characters, form a party, build the smallest possible adventure, start a session, execute commands, read the events, and round-trip the game through a save. The complete program appears [at the end of the page](#the-complete-program) — every fragment along the way is an excerpt of it.

Install [osrlib from PyPI](https://pypi.org/project/osrlib/) with [uv](https://docs.astral.sh/uv/) or pip. The library requires Python ≥ 3.14.

```sh
uv add osrlib
```

or, with pip:

```sh
pip install osrlib
```

## Roll the party

Character creation follows the SRD's procedure — roll ability scores, choose a class, roll hit points and starting gold — and [`create_character`][osrlib.core.character.create_character] runs it in one call. Every random draw in osrlib comes from a named stream forked from a master seed, so the same seed always rolls the same characters:

```{.python .no-run}
# Roll two 1st-level characters; every random draw comes from a named, seeded stream.
rules = Ruleset()
creation = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
fighter = create_character(name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=rules, stream=creation)
cleric = create_character(name="Osric", class_id="cleric", alignment=Alignment.LAWFUL, ruleset=rules, stream=creation)
party = Party(members=[fighter.character, cleric.character])
```

`class_id` takes any id from [`load_classes`][osrlib.data.load_classes] — see [the class id index][classes-index]. The result bundles the created [`Character`][osrlib.core.character.Character] with the raw rolls, which is why the party is built from `fighter.character`.

## Build the smallest adventure

An [`Adventure`][osrlib.crawl.adventure.Adventure] is a town plus one or more dungeons. A dungeon level is a grid of 10-foot cells; edges between cells are walls unless declared open or a door. This one is a single corridor — two cells joined west–east:

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

The edge key `"1,0:west"` names the west side of cell `(1, 0)` — the boundary between the two cells. [Building an adventure](building-an-adventure.md) walks the geometry and the content models in full.

## Start the session and move

A [`GameSession`][osrlib.crawl.session.GameSession] starts in town. [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] places the party at the entrance and switches the session to `exploring` — most dungeon commands are rejected until that happens:

```{.python .no-run}
# A session starts in town; entering the dungeon switches it to exploring.
session = GameSession.new(party, adventure, seed=7)
session.execute(EnterDungeon(dungeon_id="crypt"))
assert session.mode is SessionMode.EXPLORING
```

Game state changes only through commands, and every rules resolution comes back as typed events. A rejected command changes nothing — rejection is a normal in-fiction outcome, not an exception (see [the rejection code reference](../reference/rejection-codes.md)):

```{.python .no-run}
# Commands in, events out: every rules resolution is a typed event with a message code.
result = session.execute(MoveParty(direction=Direction.EAST))
assert result.accepted
lines = [format_message(event) for event in result.events]
assert lines  # every event formats to a default English line
```

Events carry structured fields and a message code, never baked prose — [`format_message`][osrlib.messages.format_message] is the default English formatter, and front ends can supply their own (see [the message code reference](../reference/message-codes.md)).

## Save and load

The whole session serializes to a JSON-compatible dict. Loading replays the command log against the same seed, so a loaded game is bit-for-bit the game you saved:

```{.python .no-run}
# The whole session round-trips through JSON: same seed, same commands, same game.
document = save_game(session)
restored = load_game(document)
assert save_game(restored) == document
```

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

- [Building an adventure](building-an-adventure.md) — the dungeon geometry and content models, one at a time.
- [Sessions, commands, and events](../guides/sessions-commands-events.md) — the command loop in depth: modes, rejections, the event log.
- [Determinism, saves, and replay](../guides/determinism-saves-replay.md) — what the seed guarantees and how loading works.
- [The TUI crawler](../front-ends/tui-crawler.md) — a complete example game built on everything above.
