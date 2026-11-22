# Quickstart

osrlib requires Python 3.14 or later. Install [osrlib from PyPI](https://pypi.org/project/osrlib/) with `uv`:

```sh
uv add osrlib
```

Or install with `pip`:

```sh
pip install osrlib
```

To run every step together with imports included, copy [the complete program](#the-complete-program).

## Roll the party

Call [`create_character`][osrlib.core.character.create_character] with a `class_id` from [`load_classes`][osrlib.data.load_classes] (see [the class id index][classes-index]) and a named stream from [`RngStreams`][osrlib.core.rng.RngStreams], so using the same master seed reproduces the same rolls. `create_character` rolls 1st-level ability scores, checks the class requirements, rolls hit points and starting gold, and returns a [`CharacterCreationResult`][osrlib.core.character.CharacterCreationResult] whose `.character` attribute you pass to [`Party`][osrlib.crawl.party.Party]. If the rolled scores miss the class minimums, `create_character` raises `ValueError` after advancing `stream`, so retry with that same `stream` instance rather than creating a new `RngStreams`:

```{.python .no-run}
# Roll two 1st-level characters; every random draw comes from a named, seeded stream.
rules = Ruleset()
creation = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
fighter = create_character(name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=rules, stream=creation)
cleric = create_character(name="Osric", class_id="cleric", alignment=Alignment.LAWFUL, ruleset=rules, stream=creation)
party = Party(members=[fighter.character, cleric.character])
```

## Build the adventure

Combine a [`TownSpec`][osrlib.crawl.adventure.TownSpec] and one or more [`DungeonSpec`][osrlib.crawl.dungeon.DungeonSpec] instances into an [`Adventure`][osrlib.crawl.adventure.Adventure]. In [`LevelSpec`][osrlib.crawl.dungeon.LevelSpec], cell boundaries default to solid walls unless listed in `edges`: setting `"1,0:west"` to `Edge(kind=EdgeKind.OPEN)` opens the west boundary of cell `(1, 0)`, connecting `entrance=(0, 0)` to `(1, 0)` (see [Building an adventure](building-an-adventure.md)):

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

## Start the session and move

Call [`GameSession.new`][osrlib.crawl.session.GameSession.new] with your party, adventure, master seed, and [`Ruleset`][osrlib.core.ruleset.Ruleset] to start a [`GameSession`][osrlib.crawl.session.GameSession] in `town`. Execute [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] to travel to the dungeon entrance and switch `session.mode` to `SessionMode.EXPLORING`:

```{.python .no-run}
# A session starts in town; entering the dungeon switches it to exploring.
session = GameSession.new(party, adventure, seed=7, ruleset=rules)
session.execute(EnterDungeon(dungeon_id="crypt"))
assert session.mode is SessionMode.EXPLORING
```

Advance play by passing commands to [`GameSession.execute`][osrlib.crawl.session.GameSession.execute]. When a command is legal, `result.accepted` is `True`, the session updates state, and `result.events` lists the typed events for that action. Events contain structured fields, a `visibility`, and a message `code` rather than English prose. Filter to `Visibility.PLAYER` before showing events to players (see [Views and visibility](../guides/views-and-visibility.md)), and call [`format_message`][osrlib.messages.format_message] for a default English sentence or render the event fields directly in your UI:

```{.python .no-run}
# Commands in, events out: filter to player-visible events before rendering.
result = session.execute(MoveParty(direction=Direction.EAST))
assert result.accepted
lines = [format_message(event) for event in result.events if event.visibility is Visibility.PLAYER]
print(lines[0])
# The party moves to (1, 0), facing east.
```

If a rule blocks the command (such as moving into a wall), `result.accepted` is `False`, `result.rejections` lists the structured [`Rejection`][osrlib.core.validation.Rejection] codes, and session state, RNG streams, and the clock remain unchanged.

## Save and load

Call [`save_game`][osrlib.persistence.save_game] to serialize the session (including the adventure, party, explored dungeon state, and RNG stream positions) to a JSON-compatible dict, and call [`load_game`][osrlib.persistence.load_game] to restore a running session from the decoded JSON:

```{.python .no-run}
# The whole session round-trips through JSON.
document = save_game(session)
restored = load_game(json.loads(json.dumps(document)))
assert save_game(restored) == document
```

To audit or test a game by re-executing `session.command_log` from scratch, call [`replay_game`][osrlib.persistence.replay_game] with the master seed, a pre-session party document captured with [`party_to_document`][osrlib.core.character.party_to_document] before `GameSession.new`, the adventure, and the ruleset. Saves do not store the starting party, so see [Determinism, saves, and replay](../guides/determinism-saves-replay.md) for the full replay setup.

## The complete program

```python
import json

from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.events import Visibility
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
session = GameSession.new(party, adventure, seed=7, ruleset=rules)
session.execute(EnterDungeon(dungeon_id="crypt"))
assert session.mode is SessionMode.EXPLORING

# Commands in, events out: filter to player-visible events before rendering.
result = session.execute(MoveParty(direction=Direction.EAST))
assert result.accepted
lines = [format_message(event) for event in result.events if event.visibility is Visibility.PLAYER]
print(lines[0])
# The party moves to (1, 0), facing east.

# The whole session round-trips through JSON.
document = save_game(session)
restored = load_game(json.loads(json.dumps(document)))
assert save_game(restored) == document
```

## Where next

- [Building an adventure](building-an-adventure.md) - laying out dungeon grids, edges, doors, keyed areas, traps, and treasure.
- [Gates, triggers, and quests](../guides/gates-triggers-quests.md) - gating doors and stairs, reacting to events with triggers, and defining quests.
- [Sessions, commands, and events](../guides/sessions-commands-events.md) - handling session modes, command rejections, JSON parsing, and the event log.
- [Determinism, saves, and replay](../guides/determinism-saves-replay.md) - saving sessions, migrating older saves, and replaying command logs from a seed.
- [The TUI crawler](../front-ends/tui-crawler.md) - walking through a complete terminal dungeon crawler built on osrlib.
