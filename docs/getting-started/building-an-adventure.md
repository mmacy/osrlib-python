# Building an adventure

Adventures are plain data: frozen [pydantic](https://docs.pydantic.dev/) models you assemble in code (or load from your own file format) and hand to the session. Nothing here is random and nothing is hidden — if it validates, it plays. This page builds a small dungeon one model at a time; [the complete program](#the-complete-program) at the end runs as written, and every fragment is an excerpt of it.

The shape of the tree:

- [`Adventure`][osrlib.crawl.adventure.Adventure] — the root: a name, a [`TownSpec`][osrlib.crawl.adventure.TownSpec], and one or more dungeons
- [`DungeonSpec`][osrlib.crawl.dungeon.DungeonSpec] — one dungeon: an id and one or more levels
- [`LevelSpec`][osrlib.crawl.dungeon.LevelSpec] — a grid of 10-foot cells with edges, keyed areas, features, and transitions
- [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] — a keyed room or cave over some cells, with its encounter, trap, and treasure bindings

## The grid and its edges

A level is a `width × height` grid. Cells are addressed `(x, y)` with `x` increasing east and `y` increasing south from `(0, 0)` at the northwest corner.

Walls are the default. The `edges` map declares the exceptions — passages and doors — and everything absent from it is solid wall, including the level boundary. Each physical edge between two cells has exactly one entry, keyed on the cell that lies south or east of it: the key `"1,0:west"` is the west side of cell `(1, 0)`, which is the same edge as the east side of `(0, 0)`. The [`edge_key`][osrlib.crawl.dungeon.edge_key] helper computes the canonical key for any cell and direction, so you never have to think about which cell owns an edge:

```{.python .no-run}
# The level: a 3x1 corridor, entered at the west end, both interior edges open.
level = LevelSpec(
    number=1,
    width=3,
    height=1,
    entrance=(0, 0),
    edges={
        "1,0:west": Edge(kind=EdgeKind.OPEN),
        "2,0:west": Edge(kind=EdgeKind.OPEN),
    },
```

An [`Edge`][osrlib.crawl.dungeon.Edge] is `open`, `wall`, or `door`; a door edge carries a [`DoorSpec`][osrlib.crawl.dungeon.DoorSpec] — normal or secret, optionally stuck or locked, optionally starting open. `entrance` is the cell where [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] lands the party.

## Keyed areas

Cells not covered by any area are corridor. An [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] names a region — a room, a cave, a shrine — and binds content to it: descriptive prose for your front end, an encounter, a trap, treasure. The party triggers an area's content by stepping into any of its cells:

```{.python .no-run}
    areas=(
        AreaSpec(
            id="guard_post",
            name="Guard post",
            description="Two goblins crouch over a game of knucklebones.",
            cells=((2, 0),),
            encounter=KeyedEncounter(monsters=(KeyedMonster(template_id="goblin", count_fixed=2),)),
        ),
    ),
)
```

A [`KeyedEncounter`][osrlib.crawl.dungeon.KeyedEncounter] lists its monsters by template id — any id from [`load_monsters`][osrlib.data.load_monsters], see [the monster id index][monsters-index] — each with a fixed count or count dice. It can also pin the monsters' awareness, stance, or alignment; left unpinned, surprise and reactions roll normally when the party walks in.

Beyond encounters, an area (or the level itself) can carry:

- [`FeatureSpec`][osrlib.crawl.dungeon.FeatureSpec] — treasure caches with hand-placed items, coins, and named valuables ([`ValuableSpec`][osrlib.crawl.dungeon.ValuableSpec]), construction tricks, or custom content for your front end
- [`TrapSpec`][osrlib.crawl.dungeon.TrapSpec] — room traps on areas, treasure traps on caches
- [`AreaTreasureSpec`][osrlib.crawl.dungeon.AreaTreasureSpec] — generated treasure: explicit treasure type letters (see [the treasure type index][treasure-types-index]) or the level's unguarded band
- [`TransitionSpec`][osrlib.crawl.dungeon.TransitionSpec] — stairs, trapdoors, and chutes between levels (these live on the level, not the area)
- [`WanderingSpec`][osrlib.crawl.dungeon.WanderingSpec] — the level's wandering-monster check: 1-in-6 every two turns by default, with an optional custom table

## The dungeon, the town, and the root

The level slots into a [`DungeonSpec`][osrlib.crawl.dungeon.DungeonSpec], and the dungeon into an [`Adventure`][osrlib.crawl.adventure.Adventure] beside the [`TownSpec`][osrlib.crawl.adventure.TownSpec] — the safe base where the party rests, buys equipment, and sells treasure. `travel_turns` maps each dungeon id to the town-to-entrance travel cost in exploration turns:

```{.python .no-run}
barrow = DungeonSpec(id="barrow", name="The Barrow", levels=(level,))
town = TownSpec(name="Threshold", travel_turns={"barrow": 2})
adventure = Adventure(name="The Barrow of the Knucklebone Goblins", town=town, dungeons=(barrow,))
```

## Validate before play

[`validate_adventure`][osrlib.crawl.adventure.validate_adventure] checks the whole tree against the compiled catalogs — unknown monster or item ids, out-of-bounds cells, transitions to nowhere, missing entrances — and raises [`ContentValidationError`][osrlib.errors.ContentValidationError] naming every problem at once. [`GameSession.new`][osrlib.crawl.session.GameSession.new] runs the same validation, so a session can never start on broken content; calling it yourself just fails faster while you author:

```{.python .no-run}
# Validation catches unknown ids and broken geometry before play ever starts.
validate_adventure(adventure, load_monsters(), load_equipment())
```

## The complete program

Entering the dungeon and walking two cells east lands the party in the guard post — the goblins spawn, surprise and reaction roll, and the session switches to the encounter:

```python
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec, validate_adventure
from osrlib.crawl.commands import EnterDungeon, MoveParty, SessionMode
from osrlib.crawl.dungeon import (
    AreaSpec,
    Direction,
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

# The level: a 3x1 corridor, entered at the west end, both interior edges open.
level = LevelSpec(
    number=1,
    width=3,
    height=1,
    entrance=(0, 0),
    edges={
        "1,0:west": Edge(kind=EdgeKind.OPEN),
        "2,0:west": Edge(kind=EdgeKind.OPEN),
    },
    areas=(
        AreaSpec(
            id="guard_post",
            name="Guard post",
            description="Two goblins crouch over a game of knucklebones.",
            cells=((2, 0),),
            encounter=KeyedEncounter(monsters=(KeyedMonster(template_id="goblin", count_fixed=2),)),
        ),
    ),
)

barrow = DungeonSpec(id="barrow", name="The Barrow", levels=(level,))
town = TownSpec(name="Threshold", travel_turns={"barrow": 2})
adventure = Adventure(name="The Barrow of the Knucklebone Goblins", town=town, dungeons=(barrow,))

# Validation catches unknown ids and broken geometry before play ever starts.
validate_adventure(adventure, load_monsters(), load_equipment())

rules = Ruleset()
creation = RngStreams(master_seed=11).get(CHARACTER_CREATION_STREAM)
hero = create_character(name="Brakka", class_id="dwarf", alignment=Alignment.LAWFUL, ruleset=rules, stream=creation)
session = GameSession.new(Party(members=[hero.character]), adventure, seed=11)

session.execute(EnterDungeon(dungeon_id="barrow"))
session.execute(MoveParty(direction=Direction.EAST))
result = session.execute(MoveParty(direction=Direction.EAST))
assert result.accepted

# Stepping into the keyed area spawns the goblins and starts an encounter.
assert session.mode is SessionMode.ENCOUNTER
assert len(session.monsters) == 2
```

## Where next

- The example games ship complete authored adventures worth reading: [the TUI crawler](../front-ends/tui-crawler.md) builds a two-level barrow with a fetch quest, custom wandering tables, and a hand-placed MacGuffin.
- [Sessions, commands, and events](../guides/sessions-commands-events.md) — what happens after the encounter starts.
- [Authoring custom classes and spells](../guides/authoring-custom-content.md) — extending the content catalogs themselves.
