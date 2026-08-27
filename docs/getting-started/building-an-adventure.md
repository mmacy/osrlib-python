# Building an adventure

Adventures are plain data: frozen [pydantic](https://docs.pydantic.dev/) models you assemble in code, or load from your own file format, and hand to the session. [The complete program](#the-complete-program) at the end runs as written, and every code snippet before it comes from that program.

The shape of the tree:

- [`Adventure`][osrlib.crawl.adventure.Adventure] - the root: a name, a [`TownSpec`][osrlib.crawl.adventure.TownSpec], and one or more dungeons
- [`DungeonSpec`][osrlib.crawl.dungeon.DungeonSpec] - one dungeon: an id and one or more levels
- [`LevelSpec`][osrlib.crawl.dungeon.LevelSpec] - a grid of 10-foot cells with edges, keyed areas, features, and transitions
- [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] - a keyed room or cave over some cells, with its encounter, trap, and treasure bindings

## The grid and its edges

A level is a `width × height` grid. Cells are addressed `(x, y)` with `x` increasing east and `y` increasing south from `(0, 0)` at the northwest corner.

Walls are the default. You declare the exceptions (passages and doors) in the `edges` map. Everything absent from the map is solid wall, including the level boundary. Each physical edge between two cells has exactly one entry, keyed on the cell that lies south or east of it. The key `"1,0:west"` is the west side of cell `(1, 0)`, which is the same edge as the east side of `(0, 0)`. The [`edge_key`][osrlib.crawl.dungeon.edge_key] helper computes the canonical key for any cell and direction, so you don't have to work out which of the two cells keys the edge:

```{.python .no-run}
# The level: a 4x1 corridor, entered at the west end, with a door at the far end.
level = LevelSpec(
    number=1,
    width=4,
    height=1,
    entrance=(0, 0),
    edges={
        "1,0:west": Edge(kind=EdgeKind.OPEN),
        "2,0:west": Edge(kind=EdgeKind.OPEN),
        "3,0:west": Edge(kind=EdgeKind.DOOR, door=DoorSpec()),
    },
```

An [`Edge`][osrlib.crawl.dungeon.Edge] is `open`, `wall`, or `door`. A door edge has a [`DoorSpec`][osrlib.crawl.dungeon.DoorSpec]: normal or secret, optionally stuck or locked, optionally starting open, and optionally gated by an authored condition. To learn about the gate you'd put on this door's `requires` field, see [Gates, triggers, and quests](../guides/gates-triggers-quests.md). `entrance` is the cell where [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] lands the party.

## Keyed areas

Cells not covered by any area are corridor. An [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] names a region (a room, a cave, a shrine) and binds content to it: descriptive prose for your front end, an encounter, a trap, treasure. The party triggers an area's content by stepping into any of its cells:

```{.python .no-run}
    areas=(
        AreaSpec(
            id="guard_post",
            name="Guard post",
            description="Two goblins crouch over a game of knucklebones.",
            cells=((3, 0),),
            encounter=KeyedEncounter(monsters=(KeyedMonster(template_id="goblin", count_fixed=2),)),
        ),
    ),
)
```

A [`KeyedEncounter`][osrlib.crawl.dungeon.KeyedEncounter] lists its monsters by template id, each with a fixed count or count dice. A template id is any id from [`load_monsters`][osrlib.data.load_monsters], listed in [the monster id index][monsters-index], or the id of a monster the adventure bundles (see [Bundling custom monsters with an adventure](../guides/authoring-custom-content.md#bundling-custom-monsters-with-an-adventure)). You can also pin the monsters' awareness, stance, or alignment. Left unpinned, surprise and reactions roll normally when the party walks in.

Beyond encounters, an area (or the level itself) can contain:

- [`FeatureSpec`][osrlib.crawl.dungeon.FeatureSpec] - a treasure cache, a construction trick, or custom content for your front end. A cache contains hand-placed items, magic items (any id from [the magic item id index][magic-items-index], like a `sword_plus_1` in a chest), coins, and named valuables ([`ValuableSpec`][osrlib.crawl.dungeon.ValuableSpec]).
- [`TrapSpec`][osrlib.crawl.dungeon.TrapSpec] - room traps on areas, treasure traps on caches. A room trap springs when the party steps in or, with `trigger="open"`, when the party opens one of the area's doors.
- [`AreaTreasureSpec`][osrlib.crawl.dungeon.AreaTreasureSpec] - generated treasure: explicit treasure type letters (see [the treasure type index][treasure-types-index]) or the level's unguarded-treasure band.
- [`TransitionSpec`][osrlib.crawl.dungeon.TransitionSpec] - stairs, trapdoors, and chutes between levels. Transitions live on the level, not the area.
- [`WanderingSpec`][osrlib.crawl.dungeon.WanderingSpec] - the level's wandering-monster check: 1-in-6 every two turns by default, with an optional custom table.

## The dungeon, the town, and the root

You slot the level into a [`DungeonSpec`][osrlib.crawl.dungeon.DungeonSpec], and the dungeon into an [`Adventure`][osrlib.crawl.adventure.Adventure] beside the [`TownSpec`][osrlib.crawl.adventure.TownSpec]. The town is the safe base where the party rests, buys equipment, and sells treasure. `travel_turns` maps each dungeon id to the town-to-entrance travel cost in exploration turns:

```{.python .no-run}
barrow = DungeonSpec(id="barrow", name="The Barrow", levels=(level,))
town = TownSpec(name="Threshold", travel_turns={"barrow": 2})
adventure = Adventure(
    name="The Barrow of the Knucklebone Goblins",
    town=town,
    dungeons=(barrow,),
)
```

The root also contains the adventure's *behavior* on the `items`, `triggers`, and `quests` fields: its own item templates, its triggers, and its quests. To learn about all three, see [Gates, triggers, and quests](../guides/gates-triggers-quests.md).

## Validate before play

[`validate_adventure`][osrlib.crawl.adventure.validate_adventure] checks the whole tree against the compiled catalogs and raises [`ContentValidationError`][osrlib.errors.ContentValidationError] naming every problem at once: unknown monster or item ids, out-of-bounds cells, transitions to nowhere, missing entrances. [`GameSession.new`][osrlib.crawl.session.GameSession.new] runs the same validation, so a session can never start on broken content. Calling `validate_adventure` yourself fails faster while you author:

```{.python .no-run}
# Validation catches unknown ids and broken geometry before play ever starts.
validate_adventure(adventure, load_monsters(), load_equipment())
```

## The complete program

Entering the dungeon and walking east brings the party to the door at the corridor's end. The guard post is beyond the door. Stepping in spawns the goblins, surprise and reaction roll, and the session switches to the encounter:

```python
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec, validate_adventure
from osrlib.crawl.commands import EnterDungeon, MoveParty, OpenDoor, SessionMode
from osrlib.crawl.dungeon import (
    AreaSpec,
    Direction,
    DoorSpec,
    DungeonSpec,
    Edge,
    EdgeKind,
    KeyedEncounter,
    KeyedMonster,
    LevelSpec,
)
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.data import load_equipment, load_monsters

# The level: a 4x1 corridor, entered at the west end, with a door at the far end.
level = LevelSpec(
    number=1,
    width=4,
    height=1,
    entrance=(0, 0),
    edges={
        "1,0:west": Edge(kind=EdgeKind.OPEN),
        "2,0:west": Edge(kind=EdgeKind.OPEN),
        "3,0:west": Edge(kind=EdgeKind.DOOR, door=DoorSpec()),
    },
    areas=(
        AreaSpec(
            id="guard_post",
            name="Guard post",
            description="Two goblins crouch over a game of knucklebones.",
            cells=((3, 0),),
            encounter=KeyedEncounter(monsters=(KeyedMonster(template_id="goblin", count_fixed=2),)),
        ),
    ),
)

barrow = DungeonSpec(id="barrow", name="The Barrow", levels=(level,))
town = TownSpec(name="Threshold", travel_turns={"barrow": 2})
adventure = Adventure(
    name="The Barrow of the Knucklebone Goblins",
    town=town,
    dungeons=(barrow,),
)

# Validation catches unknown ids and broken geometry before play ever starts.
validate_adventure(adventure, load_monsters(), load_equipment())

rules = Ruleset()
creation = RngStreams(master_seed=11).get(CHARACTER_CREATION_STREAM)
hero = create_character(name="Brakka", class_id="dwarf", alignment=Alignment.LAWFUL, ruleset=rules, stream=creation)
session = GameSession.new(Party(members=[hero.character]), adventure, seed=11)

session.execute(EnterDungeon(dungeon_id="barrow"))
session.execute(MoveParty(direction=Direction.EAST))
session.execute(MoveParty(direction=Direction.EAST))

opened = session.execute(OpenDoor(direction=Direction.EAST))
assert opened.accepted

result = session.execute(MoveParty(direction=Direction.EAST))
assert result.accepted

# Stepping into the keyed area spawns the goblins and starts an encounter.
assert session.mode is SessionMode.ENCOUNTER
assert len(session.monsters) == 2
```

## Where next

- [Gates, triggers, and quests](../guides/gates-triggers-quests.md) - the authored behavior this dungeon's data can contain: the gated door, the trigger wiring, and the quest that ends the adventure.
- [The TUI crawler](../front-ends/tui-crawler.md) builds a complete authored adventure: a two-level barrow with a fetch quest, a custom wandering table, and a hand-placed MacGuffin. [The FastAPI pattern](../front-ends/fastapi-pattern.md) serves the same barrow over HTTP.
- [Sessions, commands, and events](../guides/sessions-commands-events.md) - what happens after the encounter starts.
- [Authoring custom classes, spells, monsters, and items](../guides/authoring-custom-content.md) - extending the content catalogs themselves.
