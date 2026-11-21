# Building an adventure

Build an adventure by defining a dungeon's map and content, combining it with a base town, and passing it to a game session. You need osrlib installed. Follow the [quickstart](quickstart.md) if you haven't created a session before.

You write the content as frozen [pydantic](https://docs.pydantic.dev/) models. The session reads these models without changing them and stores changes from play separately. You can construct the models in Python or convert data from a file format your application reads.

Start with a level, add a room, then put the level in a dungeon and the dungeon in an adventure. The example below builds a four-cell corridor with a door and a goblin guard post at the far end. The snippets are parts of [the complete program](#the-complete-program), which includes the imports and a party to play it.

## The grid and its edges

A [`LevelSpec`][osrlib.crawl.dungeon.LevelSpec] defines a `width × height` grid of 10-foot cells. Address each cell as `(x, y)`, starting at `(0, 0)` in the northwest corner. Increase `x` to move east and `y` to move south.

Every edge is a wall unless you declare an opening or door in `edges`. The level boundary is always a wall. In this corridor, the first two connections are open and the third has a door:

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

Each connection has one key, even though you can approach it from either cell. `"1,0:west"` identifies the west side of `(1, 0)`, also the east side of `(0, 0)`. Use [`edge_key`][osrlib.crawl.dungeon.edge_key] to calculate keys: `edge_key((0, 0), Direction.EAST)` returns `"1,0:west"`. Don't write `"0,0:east"` instead. The engine doesn't look up that form, and adventure validation reports it.

An [`Edge`][osrlib.crawl.dungeon.Edge] can be `open`, `wall`, or `door`. For a door, provide a [`DoorSpec`][osrlib.crawl.dungeon.DoorSpec] to specify whether it's secret, stuck, or locked. To restrict a door or stair with a condition, add a gate. See [Gates, triggers, and quests](../guides/gates-triggers-quests.md).

Set `entrance` to the cell where [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] places the party. Here the party starts at the west end, `(0, 0)`.

## Keyed areas

To give a room a name, description, and content, define an [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] over its cells. This is a *keyed area*, corresponding to a numbered room or cave in an adventure module. Cells outside all areas are corridors.

Add the guard post to the level's `areas`. It occupies the final cell and contains an encounter with two goblins:

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

When the party enters an area's cells, the session resolves the content attached to that area. Your interface decides how to display its description. If areas overlap, the session treats the shared cells as part of the first area in the level's `areas`. Each area needs at least one cell that isn't already assigned to an earlier area, or the party can't enter it. An area's feature with an explicit `cell` must be on a cell assigned to that area.

A [`KeyedEncounter`][osrlib.crawl.dungeon.KeyedEncounter] contains [`KeyedMonster`][osrlib.crawl.dungeon.KeyedMonster] entries, each with a monster template id and either a fixed count or dice for the count. Find built-in ids in [`load_monsters`][osrlib.data.load_monsters] or [the monster id index][monsters-index]. You can also reference a template bundled with the adventure; see [Bundling custom monsters with an adventure](../guides/authoring-custom-content.md#bundling-custom-monsters-with-an-adventure). The encounter can specify awareness, stance, and alignment as well.

### Add other content

Attach each kind of content to the model that uses it:

- **Features:** Put a [`FeatureSpec`][osrlib.crawl.dungeon.FeatureSpec] in an area's or level's `features` for a treasure cache, construction trick, or custom content your interface handles. A feature outside an area needs an explicit `cell`. A cache can contain hand-placed items, magic items such as `sword_plus_1` from [the magic item id index][magic-items-index], coins, and named valuables defined with [`ValuableSpec`][osrlib.crawl.dungeon.ValuableSpec].
- **Traps:** Put a room [`TrapSpec`][osrlib.crawl.dungeon.TrapSpec] in an area's `trap`, or a treasure trap in a cache's `trap`. A room trap can spring when the party enters or, with `trigger="open"`, opens a door on the area's edge.
- **Generated treasure:** Put an [`AreaTreasureSpec`][osrlib.crawl.dungeon.AreaTreasureSpec] in an area's `treasure`. Choose letters from [the treasure type index][treasure-types-index], or use the SRD's unguarded treasure table, which scales with the dungeon level.
- **Connections between locations:** Put a [`TransitionSpec`][osrlib.crawl.dungeon.TransitionSpec] in the level's `transitions`, even when it's inside an area. The party takes stairs, trapdoors, and chutes with [`UseStairs`][osrlib.crawl.commands.UseStairs], and walks through passages with [`MoveParty`][osrlib.crawl.commands.MoveParty]. Use a passage when locations are adjacent in the adventure but aren't neighboring grid cells, such as where the floors of two levels meet.
- **Wandering monsters:** Set the level's `wandering` to a [`WanderingSpec`][osrlib.crawl.dungeon.WanderingSpec] to change the check interval, base chance, or encounter table. By default, the session checks every two exploration turns with a base 1-in-6 chance.

## The dungeon, the town, and the root

Put the level in a [`DungeonSpec`][osrlib.crawl.dungeon.DungeonSpec]. A dungeon has an id and one or more levels. Then create an [`Adventure`][osrlib.crawl.adventure.Adventure] with the dungeon and a [`TownSpec`][osrlib.crawl.adventure.TownSpec] for the base town.

The town is where the party starts, rests, buys equipment, and sells treasure. Set `travel_turns` to map each dungeon id to the travel time from town, in exploration turns. Here the trip to the barrow takes two turns:

```{.python .no-run}
barrow = DungeonSpec(id="barrow", name="The Barrow", levels=(level,))
town = TownSpec(name="Threshold", travel_turns={"barrow": 2})
adventure = Adventure(
    name="The Barrow of the Knucklebone Goblins",
    town=town,
    dungeons=(barrow,),
)
```

You can add item templates in the adventure's `items`, event-driven actions in `triggers`, and objectives and rewards in `quests`. Register [`Interpreter`][osrlib.crawl.interpreter.Interpreter] on the session to execute triggers and update quests. See [Gates, triggers, and quests](../guides/gates-triggers-quests.md) to add these to the adventure.

## Validate before play

Call [`validate_adventure`][osrlib.crawl.adventure.validate_adventure] while you author the content. Pass the adventure and the built-in monster and equipment catalogs:

```{.python .no-run}
# Validation catches unknown ids and broken geometry before play ever starts.
validate_adventure(adventure, load_monsters(), load_equipment())
```

The validator checks references and geometry throughout the adventure. It reports unknown monster and item ids, invalid treasure type letters, out-of-bounds cells, incorrect edge keys, areas with no assigned cells, misplaced features, transitions with no destination (including trap transitions), and missing entrances. If it finds problems, it raises one [`ContentValidationError`][osrlib.errors.ContentValidationError] listing them.

[`GameSession.new`][osrlib.crawl.session.GameSession.new] runs the same validation before creating a session. Calling it yourself lets you correct content without starting a game.

## The complete program

Save this program as `adventure.py` in the project where you installed osrlib. It creates a dwarf, starts a session, and sends the party east through the corridor. Opening the door and entering the guard post starts the goblin encounter:

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

# Stepping into the keyed area creates the goblins and starts an encounter.
assert session.mode is SessionMode.ENCOUNTER
assert len(session.monsters) == 2
```

Run it with uv:

```sh
uv run adventure.py
```

If you installed with pip, run it with that environment's Python:

```sh
python adventure.py
```

The program should exit without output. Its assertions check that opening the door and moving into the room succeed, that the session enters encounter mode, and that the encounter contains two monsters.

## Where next

- Add conditional doors and actions that respond to play with [Gates, triggers, and quests](../guides/gates-triggers-quests.md).
- Play a two-level barrow with a fetch quest, custom wandering table, and hand-placed quest item in [the TUI crawler](../front-ends/tui-crawler.md), or serve the same adventure over HTTP with [the FastAPI pattern](../front-ends/fastapi-pattern.md).
- Continue the encounter and handle rejected commands with [Sessions, commands, and events](../guides/sessions-commands-events.md).
- Extend the catalogs with [Authoring custom classes, spells, monsters, and items](../guides/authoring-custom-content.md).
