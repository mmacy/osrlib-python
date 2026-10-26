"""The crawl framework: adventure content, the game session, and the procedures the session runs.

`osrlib.crawl` is the layer you build a dungeon crawler on. It contains the content models you author an
adventure with, the [`GameSession`][osrlib.crawl.session.GameSession] that runs one, and the
procedures the session runs while the party explores, meets monsters, and fights. It sits on top of
the `osrlib.core` kernel, which has the characters, items, dice, the clock, and the rules that don't
depend on a dungeon. The dependency runs one way: the kernel never imports from here, so you can
use the core rules on their own and add this package when you want a running game.

The crawl framework has these modules:

- [`osrlib.crawl.dungeon`][osrlib.crawl.dungeon]: build dungeon levels from cells, the edges
  between them (walls, doors, and openings), keyed areas, features, traps, and transitions like
  stairs. [`DungeonState`][osrlib.crawl.dungeon.DungeonState] is where the session records how
  play changes that content.
- [`osrlib.crawl.adventure`][osrlib.crawl.adventure]: combine dungeons and a town into an
  [`Adventure`][osrlib.crawl.adventure.Adventure], the document a session plays, and check it with
  [`validate_adventure`][osrlib.crawl.adventure.validate_adventure].
- [`osrlib.crawl.party`][osrlib.crawl.party]: put characters in a
  [`Party`][osrlib.crawl.party.Party], in marching order. The session moves the party as one group.
- [`osrlib.crawl.gates`][osrlib.crawl.gates], [`osrlib.crawl.triggers`][osrlib.crawl.triggers], and
  [`osrlib.crawl.quests`][osrlib.crawl.quests]: write the rules an adventure adds, like what the
  party needs before a door opens, what happens when the party enters an area, and what the party
  is trying to accomplish. [`osrlib.crawl.narrative`][osrlib.crawl.narrative] is the text you
  attach to any of them.
- [`osrlib.crawl.stocking`][osrlib.crawl.stocking]: roll a room's monsters and treasure from the
  B/X tables instead of choosing them by hand.
- [`osrlib.crawl.content_pack`][osrlib.crawl.content_pack]: move finished room contents from one
  adventure to another, without the map.
- [`osrlib.crawl.session`][osrlib.crawl.session]: start a game from a party and an adventure, and
  run it with one [`execute`][osrlib.crawl.session.GameSession.execute] call per player action.
- [`osrlib.crawl.commands`][osrlib.crawl.commands] has what you pass to `execute`, and
  [`osrlib.crawl.events`][osrlib.crawl.events] has what comes back.
  [`osrlib.crawl.views`][osrlib.crawl.views] has the state you draw, as the players may see it or
  as the referee (the game master) sees it.
- [`osrlib.crawl.interpreter`][osrlib.crawl.interpreter]: run an adventure's triggers and quests.
  If the adventure has any, register an interpreter on the session with
  `session.register_listener(Interpreter(session))`.
- [`osrlib.crawl.exploration`][osrlib.crawl.exploration],
  [`osrlib.crawl.encounter`][osrlib.crawl.encounter], and
  [`osrlib.crawl.battle`][osrlib.crawl.battle]: the procedures the session runs for you. Read them
  to learn what a command does. You rarely call them yourself.

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
