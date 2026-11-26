# Building an adventure

You define an adventure as a hierarchy of frozen [pydantic](https://docs.pydantic.dev/) models that you construct in Python (or deserialize from JSON) and pass to [`GameSession.new`][osrlib.crawl.session.GameSession.new]. Because the models are immutable, a session never modifies your authored content. Door states, cleared encounters, and looted caches are tracked separately in session state. To run the finished barrow example with imports included, copy [the complete program](#the-complete-program).

The layout hierarchy uses these core models:

- [`Adventure`][osrlib.crawl.adventure.Adventure]: Contains the adventure title, one [`TownSpec`][osrlib.crawl.adventure.TownSpec], one or more dungeons, and optional custom content, triggers, and quests.
- [`DungeonSpec`][osrlib.crawl.dungeon.DungeonSpec]: Groups one or more levels under a unique `id`.
- [`LevelSpec`][osrlib.crawl.dungeon.LevelSpec]: Defines a grid of 10-foot cells along with its open edges, doors, keyed areas, features, transitions, and wandering-monster settings.
- [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec]: Groups one or more grid cells into a named room or region with optional encounters, room traps, features, and area treasure.

## The grid and its edges

Each [`LevelSpec`][osrlib.crawl.dungeon.LevelSpec] is a `width × height` grid of 10-foot cells addressed as `(x, y)`, where `(0, 0)` is the northwest corner, `x` increases east (`0` to `width - 1`), and `y` increases south (`0` to `height - 1`). Set `entrance` on at least one level in each dungeon to specify the `(x, y)` cell where [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] places the party.

The outer perimeter of the grid is always solid wall, and every interior boundary between two in-bounds cells defaults to a wall unless listed in `edges`. Declare open passages and doors between adjacent in-bounds cells in `edges` using canonical `"x,y:north"` or `"x,y:west"` keys (a cell's east boundary is the `west` boundary of `(x + 1, y)`, and its south boundary is the `north` boundary of `(x, y + 1)`). Call [`edge_key`][osrlib.crawl.dungeon.edge_key] to compute the canonical key from any cell and [`Direction`][osrlib.crawl.dungeon.Direction]. [`LevelSpec.edge`][osrlib.crawl.dungeon.LevelSpec.edge] looks up only canonical in-bounds keys, and [`validate_adventure`][osrlib.crawl.adventure.validate_adventure] rejects non-canonical keys (such as `"0,0:east"`) and perimeter edges:

```{.python .no-run}
# A 4x1 corridor, entered at the west end, with a door at the far end.
corridor_edges = {
    "1,0:west": Edge(kind=EdgeKind.OPEN),
    "2,0:west": Edge(kind=EdgeKind.OPEN),
    "3,0:west": Edge(kind=EdgeKind.DOOR, door=DoorSpec()),
}
level = LevelSpec(number=1, width=4, height=1, entrance=(0, 0), edges=corridor_edges)
```

Set [`Edge.kind`][osrlib.crawl.dungeon.Edge.kind] to `EdgeKind.OPEN`, `EdgeKind.WALL`, or `EdgeKind.DOOR`. When `kind` is `EdgeKind.DOOR`, pass a [`DoorSpec`][osrlib.crawl.dungeon.DoorSpec] to configure whether the door is `"normal"` or `"secret"`, `stuck`, `locked`, `starts_open`, or guarded by a `requires` gate (see [Gates, triggers, and quests](../guides/gates-triggers-quests.md)).

## Keyed areas

Grid cells outside every [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] are unkeyed corridors. Add an `AreaSpec` to group one or more cells into a named room and attach a `description` for your UI, an `encounter`, `features`, a room `trap`, or area `treasure`. When the party steps into any cell of an area, the session rolls `AreaSpec.treasure` (on first entry), checks any `"enter"` room trap, and spawns any undefeated keyed encounter. If two areas include the same cell, that cell belongs to whichever area appears first in `level.areas`, and every area must include at least one cell not claimed by an earlier area:

```{.python .no-run}
guard_post = AreaSpec(
    id="guard_post",
    name="Guard post",
    description="Two goblins crouch over a game of knucklebones.",
    cells=((3, 0),),
    encounter=KeyedEncounter(monsters=(KeyedMonster(template_id="goblin", count_fixed=2),)),
)
level = LevelSpec(
    number=1,
    width=4,
    height=1,
    entrance=(0, 0),
    edges=corridor_edges,
    areas=(guard_post,),
)
```

In [`KeyedEncounter`][osrlib.crawl.dungeon.KeyedEncounter], pass one or more [`KeyedMonster`][osrlib.crawl.dungeon.KeyedMonster] entries (up to 100 monsters total across the encounter), each specifying a `template_id` and either `count_fixed` or `count_dice`. Pass any ID from [`load_monsters`][osrlib.data.load_monsters] (listed in [the monster id index][monsters-index]) or a monster ID bundled on `Adventure.monsters` (see [Bundling custom monsters with an adventure](../guides/authoring-custom-content.md#bundling-custom-monsters-with-an-adventure)). You can also override `aware` (`True` prevents the monsters from being surprised), `stance` (fixing the reaction instead of rolling), `alignment`, and `hoard` (`True` by default, which rolls the monsters' lair treasure onto the area's first cell).

An `AreaSpec` also accepts features, traps, and area treasure:

- [`FeatureSpec`][osrlib.crawl.dungeon.FeatureSpec]: Defines a `"treasure_cache"` (looted with [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure]), a `"construction_trick"`, or a `"custom"` feature. A cache can contain mundane `item_ids`, `magic_item_ids` (from [the magic item id index][magic-items-index], such as `"sword_plus_1"`), `coins`, named `valuables` ([`ValuableSpec`][osrlib.crawl.dungeon.ValuableSpec]), and a treasure `trap`. On an `AreaSpec`, `cell` is optional: omit `cell` to make the feature reachable from every cell owned by that area, or set `cell` to one specific cell belonging to that area. You can also place corridor features directly on `LevelSpec.features`, where `cell` is required. The players read a feature's `description`, so write what the party sees and put what only the referee reads in `referee_notes`, as [Describe a treasure cache for the players](../guides/authoring-custom-content.md#describe-a-treasure-cache-for-the-players) shows.
- [`TrapSpec`][osrlib.crawl.dungeon.TrapSpec]: Specifies a room trap (`kind="room"` on `AreaSpec.trap`) or a treasure trap (`kind="treasure"` on `FeatureSpec.trap`). Room traps have a 2-in-6 chance to spring when the party steps into the area (`trigger="enter"`) or opens a door on one of the area's cells (`trigger="open"`).
- [`AreaTreasureSpec`][osrlib.crawl.dungeon.AreaTreasureSpec]: Specifies area treasure rolled on first entry, using either explicit B/X treasure type `letters` (see [the treasure type index][treasure-types-index]) or `unguarded=True` for the level-scaled unguarded treasure table. The session rolls `AreaSpec.treasure` independently of `AreaSpec.encounter`, so use it for rooms without monsters (or set `KeyedEncounter(hoard=False)` if you don't want both caches).

Connections between levels and wandering-monster checks belong on `LevelSpec` rather than `AreaSpec`:

- [`TransitionSpec`][osrlib.crawl.dungeon.TransitionSpec]: Connects cells across levels or dungeons via `"stairs_up"`, `"stairs_down"`, `"trapdoor"`, or `"chute"` (used with [`UseStairs`][osrlib.crawl.commands.UseStairs]), or `"passage"` (crossed by walking across a walled `side` with [`MoveParty`][osrlib.crawl.commands.MoveParty], useful when two levels meet horizontally). Transitions are one-way, so add a reverse `TransitionSpec` on the destination level when you want two-way travel.
- [`WanderingSpec`][osrlib.crawl.dungeon.WanderingSpec]: Specifies the level's wandering-monster check parameters (`chance_in_six=1` every `interval_turns=2` exploration turns by default) and an optional custom encounter `table`.

## Assemble the dungeon, town, and adventure

Group your levels into a [`DungeonSpec`][osrlib.crawl.dungeon.DungeonSpec], define the base [`TownSpec`][osrlib.crawl.adventure.TownSpec], and pass both to [`Adventure`][osrlib.crawl.adventure.Adventure]. Sessions start in town, where the party rests, buys equipment, sells treasure, and pays for temple healing. `TownSpec.travel_turns` maps each dungeon `id` to the exploration turns elapsed on each trip between town and that dungeon (`EnterDungeon`, and [`TravelToTown`][osrlib.crawl.commands.TravelToTown] when the party stands on the level's `entrance` cell):

```{.python .no-run}
barrow = DungeonSpec(id="barrow", name="The Barrow", levels=(level,))
town = TownSpec(name="Threshold", travel_turns={"barrow": 2})
adventure = Adventure(
    name="The Barrow of the Knucklebone Goblins",
    town=town,
    dungeons=(barrow,),
)
```

`Adventure` also accepts bundled content templates on `monsters`, `items`, and `spells` (see [Authoring custom classes, spells, monsters, and items](../guides/authoring-custom-content.md)) and authored event logic on `triggers` and `quests` (see [Gates, triggers, and quests](../guides/gates-triggers-quests.md)).

## Validate before play

Call [`validate_adventure`][osrlib.crawl.adventure.validate_adventure] with the base monster and equipment catalogs to check cross-references and local grid rules across the entire adventure. If any check fails, `validate_adventure` raises a single [`ContentValidationError`][osrlib.errors.ContentValidationError] listing every problem found, including unknown monster, item, or treasure-type IDs, out-of-bounds positions, non-canonical or perimeter edge keys, areas whose cells are all claimed by earlier areas, misplaced features, invalid transitions (on levels or inside trap effects), and dungeons with no level `entrance`. [`GameSession.new`][osrlib.crawl.session.GameSession.new] runs the same validation automatically, so calling `validate_adventure` in your authoring scripts or tests catches errors before starting a session:

```{.python .no-run}
# Validation catches unknown ids and broken geometry before play ever starts.
validate_adventure(adventure, load_monsters(), load_equipment())
```

`validate_adventure` does not pathfind from `entrance` to verify that every room can be reached on foot. Drive a test session through your doors and corridors, as in the complete program, to verify traversal.

## The complete program

The party enters the barrow, walks east to the door, opens it, and steps into the guard post, where the encounter begins:

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

# A 4x1 corridor, entered at the west end, with a door at the far end.
corridor_edges = {
    "1,0:west": Edge(kind=EdgeKind.OPEN),
    "2,0:west": Edge(kind=EdgeKind.OPEN),
    "3,0:west": Edge(kind=EdgeKind.DOOR, door=DoorSpec()),
}
guard_post = AreaSpec(
    id="guard_post",
    name="Guard post",
    description="Two goblins crouch over a game of knucklebones.",
    cells=((3, 0),),
    encounter=KeyedEncounter(monsters=(KeyedMonster(template_id="goblin", count_fixed=2),)),
)
level = LevelSpec(
    number=1,
    width=4,
    height=1,
    entrance=(0, 0),
    edges=corridor_edges,
    areas=(guard_post,),
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
session = GameSession.new(Party(members=[hero.character]), adventure, seed=11, ruleset=rules)

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

## Where next

- [Gates, triggers, and quests](../guides/gates-triggers-quests.md) - gating doors and transitions, scripting triggers, and defining quests.
- [The TUI crawler](../front-ends/tui-crawler.md) - inspecting a two-level barrow with a fetch quest, custom wandering table, and bundled quest item, plus [The FastAPI pattern](../front-ends/fastapi-pattern.md), which serves the same adventure over HTTP.
- [Sessions, commands, and events](../guides/sessions-commands-events.md) - executing commands, handling rejections, and reading the event log.
- [Authoring custom classes, spells, monsters, and items](../guides/authoring-custom-content.md) - bundling custom monsters, items, and spells or extending class catalogs.
