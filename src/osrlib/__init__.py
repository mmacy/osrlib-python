"""Old-School Essentials (B/X) rules engine for turn-based dungeon crawlers.

osrlib is the rules authority and game-state engine; the game supplies presentation,
input, and content. The library is headless and sans-I/O: it never renders, prompts,
sleeps, or touches the network, and all randomness flows through named deterministic
streams (see [`osrlib.core.rng`][osrlib.core.rng]).

Every symbol has exactly one import home: the kernel under `osrlib.core`, the crawl
framework under `osrlib.crawl`, and the shared services at the top level —
[`osrlib.data`][osrlib.data] (compiled SRD catalogs), [`osrlib.errors`][osrlib.errors]
(the typed exception hierarchy), [`osrlib.messages`][osrlib.messages] (message-code
formatting), [`osrlib.persistence`][osrlib.persistence] (saves and replay), and
[`osrlib.versioning`][osrlib.versioning] (schema and engine version stamping). The
package root re-exports nothing.

The quickstart below crosses the whole loop — characters, party, adventure, session,
commands, events, save, and load. Full documentation, including a stepwise version of
this example: https://mmacy.github.io/osrlib-python/

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
"""
