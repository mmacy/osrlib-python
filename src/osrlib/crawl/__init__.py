"""The crawl framework: adventure content, the game session, and the dungeon crawl loop.

`osrlib.crawl` is the layer you build a dungeon crawler on. It holds the content models you author an
adventure with, the [`GameSession`][osrlib.crawl.session.GameSession] that runs one, and the
procedures the session dispatches to while the party explores, talks, and fights. It sits on top of
the `osrlib.core` kernel, which has the characters, items, dice, the clock, and the rules that need
no dungeon around them. The dependency runs one way: the kernel never imports from here, so you can
use the core rules on their own and reach for this package when you want a running game.

The modules, in the order you meet them:

- [`osrlib.crawl.dungeon`][osrlib.crawl.dungeon] takes cell coordinates and gives you the geometry:
  levels, the edges that make walls and doors, keyed areas, features, traps, and transitions, plus
  [`DungeonState`][osrlib.crawl.dungeon.DungeonState], the overlay play writes over that frozen content.
- [`osrlib.crawl.adventure`][osrlib.crawl.adventure] takes dungeons and a town and gives you an
  [`Adventure`][osrlib.crawl.adventure.Adventure], the root document a session runs, with
  [`validate_adventure`][osrlib.crawl.adventure.validate_adventure] to check every id in it resolves.
- [`osrlib.crawl.party`][osrlib.crawl.party] takes characters and gives you a
  [`Party`][osrlib.crawl.party.Party] in marching order, which the session moves as one body.
- [`osrlib.crawl.gates`][osrlib.crawl.gates], [`osrlib.crawl.triggers`][osrlib.crawl.triggers], and
  [`osrlib.crawl.quests`][osrlib.crawl.quests] take event patterns and conditions and give you the
  authored behavior an adventure contains: what a door requires before it opens, what fires when the
  party walks in, and what the party is trying to accomplish.
  [`osrlib.crawl.narrative`][osrlib.crawl.narrative] is the prose block you attach to any of them.
- [`osrlib.crawl.stocking`][osrlib.crawl.stocking] takes a level number and an RNG stream and gives
  you one keyed area's rolled contents, so you can fill rooms from the B/X tables instead of by hand.
- [`osrlib.crawl.content_pack`][osrlib.crawl.content_pack] takes finished room content and gives you a
  portable document with the geometry left out, for moving stocked rooms between adventures.
- [`osrlib.crawl.session`][osrlib.crawl.session] takes a party and an adventure and gives you the
  running game: one [`execute`][osrlib.crawl.session.GameSession.execute] call per player action.
- [`osrlib.crawl.commands`][osrlib.crawl.commands] is what you hand `execute`, and
  [`osrlib.crawl.events`][osrlib.crawl.events] is what comes back.
  [`osrlib.crawl.views`][osrlib.crawl.views] turns the session into the state you draw, in either the
  player's or the referee's visibility.
- [`osrlib.crawl.interpreter`][osrlib.crawl.interpreter] is the listener that plays an adventure's
  triggers and quests, and
  [`osrlib.crawl.exploration`][osrlib.crawl.exploration],
  [`osrlib.crawl.encounter`][osrlib.crawl.encounter], and
  [`osrlib.crawl.battle`][osrlib.crawl.battle] are the procedures the session runs for you. You read
  them to learn what a command does. You rarely call into them yourself.

Typical usage:

```python
from osrlib.core.abilities import AbilityScore
from osrlib.core.alignment import Alignment
from osrlib.core.character import Character
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import EnterDungeon, MoveParty
from osrlib.crawl.dungeon import Direction, DungeonSpec, Edge, EdgeKind, LevelSpec
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession

hero = Character(
    name="Hild",
    class_id="fighter",
    race="human",
    level=1,
    xp=0,
    scores={ability: 12 for ability in AbilityScore},
    alignment=Alignment.LAWFUL,
    max_hp=8,
    current_hp=8,
)
crypt = DungeonSpec(
    id="crypt",
    name="The Old Crypt",
    levels=(LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)}),),
)
adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))

session = GameSession.new(Party(members=[hero]), adventure, seed=7)
session.execute(EnterDungeon(dungeon_id="crypt"))
result = session.execute(MoveParty(direction=Direction.EAST))

print([event.code for event in result.events])
# ['exploration.party.moved']
```
"""
