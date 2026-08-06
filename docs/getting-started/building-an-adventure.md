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
# The level: a 4x1 corridor, entered at the west end, with a door at the far end.
level = LevelSpec(
    number=1,
    width=4,
    height=1,
    entrance=(0, 0),
    edges={
        "1,0:west": Edge(kind=EdgeKind.OPEN),
        "2,0:west": Edge(kind=EdgeKind.OPEN),
        "3,0:west": Edge(kind=EdgeKind.DOOR, door=DoorSpec(requires=sentinel)),
    },
```

An [`Edge`][osrlib.crawl.dungeon.Edge] is `open`, `wall`, or `door`; a door edge carries a [`DoorSpec`][osrlib.crawl.dungeon.DoorSpec] — normal or secret, optionally stuck or locked, optionally starting open, and optionally gated by an authored condition (the `sentinel` above, built in [Gating a door or a stair](#gating-a-door-or-a-stair)). `entrance` is the cell where [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] lands the party.

## Keyed areas

Cells not covered by any area are corridor. An [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] names a region — a room, a cave, a shrine — and binds content to it: descriptive prose for your front end, an encounter, a trap, treasure. The party triggers an area's content by stepping into any of its cells:

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

A [`KeyedEncounter`][osrlib.crawl.dungeon.KeyedEncounter] lists its monsters by template id — any id from [`load_monsters`][osrlib.data.load_monsters], see [the monster id index][monsters-index] — each with a fixed count or count dice. It can also pin the monsters' awareness, stance, or alignment; left unpinned, surprise and reactions roll normally when the party walks in.

Beyond encounters, an area (or the level itself) can carry:

- [`FeatureSpec`][osrlib.crawl.dungeon.FeatureSpec] — treasure caches with hand-placed items, magic items (any id from [the magic item id index][magic-items-index] — a `sword_plus_1` in a chest), coins, and named valuables ([`ValuableSpec`][osrlib.crawl.dungeon.ValuableSpec]), construction tricks, or custom content for your front end
- [`TrapSpec`][osrlib.crawl.dungeon.TrapSpec] — room traps on areas, treasure traps on caches
- [`AreaTreasureSpec`][osrlib.crawl.dungeon.AreaTreasureSpec] — generated treasure: explicit treasure type letters (see [the treasure type index][treasure-types-index]) or the level's unguarded band
- [`TransitionSpec`][osrlib.crawl.dungeon.TransitionSpec] — stairs, trapdoors, and chutes between levels (these live on the level, not the area)
- [`WanderingSpec`][osrlib.crawl.dungeon.WanderingSpec] — the level's wandering-monster check: 1-in-6 every two turns by default, with an optional custom table

## Gating a door or a stair

A door or a level transition can carry a [`GateSpec`][osrlib.crawl.gates.GateSpec] on its `requires` field: an authored condition the party must satisfy for the attempt to be legal. The condition is evaluated live, at the moment the party tries the door — never remembered — so a key that gets dropped or sold stops opening it:

- [`HasItemCondition`][osrlib.crawl.gates.HasItemCondition] — some member's carried inventory holds an item with that catalog id. Any member's pack counts, equipped slots included. The id must resolve against the equipment catalog (bundled items included, see [authoring custom content](../guides/authoring-custom-content.md)) or the magic-item catalog; a gate naming an unknown id fails validation.
- [`FlagEqualsCondition`][osrlib.crawl.gates.FlagEqualsCondition] — a session flag holds a value. Your game sets flags with [`SetFlag`][osrlib.crawl.commands.SetFlag], so this is the lever-opens-the-portcullis wiring. The comparison is strict: an absent key matches nothing, not even `False`, and a stored `True` never satisfies an authored `1`.
- [`EffectActiveCondition`][osrlib.crawl.gates.EffectActiveCondition] — an active effect of that kind is attached to a party member, for a door that wants the talisman invoked rather than merely carried.

A refused attempt is an ordinary rejection — `exploration.door.gate_refused` or `exploration.transition.gate_refused` — carrying the gate's authored refusal text. It costs nothing: no dice, no game time, no items, and no change to the door.

```{.python .no-run}
sentinel = GateSpec(
    condition=HasItemCondition(item_id="brass_key"),
    narrative=NarrativeBlock(
        refusal="The bronze sentinel folds its arms. Brass, it says. Brass or nothing.",
        success="The brass key turns in the sentinel's palm and the door swings wide.",
    ),
)
```

Locks and gates are separate layers, and a door that carries both requires both: the lock answers first (`exploration.door.locked`), and once a thief has picked it — [`PickLock`][osrlib.crawl.commands.PickLock] addresses the lock and nothing else — the gate still has its say. A door standing open admits passage unchecked, so setting a gated door open with [`SetDoorState`][osrlib.crawl.commands.SetDoorState] lets the party through until the door closes again, at which point the gate applies once more. Trigger-driven one-time unlocks of that shape arrive in a later release.

`consumes=True` turns a `has_item` condition into a toll: one instance leaves the first holder in marching order each time the gated command succeeds, reported by [`ItemConsumedEvent`][osrlib.crawl.events.ItemConsumedEvent] just before the door or arrival event. Every success charges again — a consumed key-door that swings shut wants another key. Coins are not items and cannot be tolled; mint a token as a bundled item and gate on that.

A [`NarrativeBlock`][osrlib.crawl.narrative.NarrativeBlock] holds the authored text for the mechanical object it hangs on. Gates read two of its beats: `refusal`, returned in the rejection, and `success`, which rides the successful command's event — the [`DoorEvent`][osrlib.crawl.events.DoorEvent] for a door, the [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent] for a transition that crosses into a new level or dungeon. [`format_message`][osrlib.messages.format_message] appends the beat verbatim, so it shows up in a bare transcript. A transition whose destination is its own level crosses no boundary and emits no arrival event, so a success beat there has nowhere to display. The block's other fields — `journal`, `guidance` for an LLM narrator, `speaker` — are read by the surfaces that consume them; none of them ever reach the player view, which carries no gate wiring at all.

## The dungeon, the town, and the root

The level slots into a [`DungeonSpec`][osrlib.crawl.dungeon.DungeonSpec], and the dungeon into an [`Adventure`][osrlib.crawl.adventure.Adventure] beside the [`TownSpec`][osrlib.crawl.adventure.TownSpec] — the safe base where the party rests, buys equipment, and sells treasure. `travel_turns` maps each dungeon id to the town-to-entrance travel cost in exploration turns:

```{.python .no-run}
barrow = DungeonSpec(id="barrow", name="The Barrow", levels=(level,))
town = TownSpec(name="Threshold", travel_turns={"barrow": 2})
adventure = Adventure(
    name="The Barrow of the Knucklebone Goblins",
    town=town,
    dungeons=(barrow,),
    items=(GearTemplate(id="brass_key", name="Brass key", cost_gp=0),),
)
```

`items` bundles the adventure's own item templates — the brass key the sentinel wants is content, not shipped equipment. See [authoring custom content](../guides/authoring-custom-content.md) for the whole bundling contract.

## Validate before play

[`validate_adventure`][osrlib.crawl.adventure.validate_adventure] checks the whole tree against the compiled catalogs — unknown monster or item ids, out-of-bounds cells, transitions to nowhere, missing entrances — and raises [`ContentValidationError`][osrlib.errors.ContentValidationError] naming every problem at once. [`GameSession.new`][osrlib.crawl.session.GameSession.new] runs the same validation, so a session can never start on broken content; calling it yourself just fails faster while you author:

```{.python .no-run}
# Validation catches unknown ids and broken geometry before play ever starts.
validate_adventure(adventure, load_monsters(), load_equipment())
```

## The complete program

Entering the dungeon and walking east brings the party to the sentinel's door; the brass key opens it, and the cell beyond is the guard post — the goblins spawn, surprise and reaction roll, and the session switches to the encounter:

```python
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.items import GearTemplate
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec, validate_adventure
from osrlib.crawl.commands import EnterDungeon, GrantItem, MoveParty, OpenDoor, SessionMode
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
from osrlib.crawl.gates import GateSpec, HasItemCondition
from osrlib.crawl.narrative import NarrativeBlock
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.data import load_equipment, load_monsters

sentinel = GateSpec(
    condition=HasItemCondition(item_id="brass_key"),
    narrative=NarrativeBlock(
        refusal="The bronze sentinel folds its arms. Brass, it says. Brass or nothing.",
        success="The brass key turns in the sentinel's palm and the door swings wide.",
    ),
)

# The level: a 4x1 corridor, entered at the west end, with a door at the far end.
level = LevelSpec(
    number=1,
    width=4,
    height=1,
    entrance=(0, 0),
    edges={
        "1,0:west": Edge(kind=EdgeKind.OPEN),
        "2,0:west": Edge(kind=EdgeKind.OPEN),
        "3,0:west": Edge(kind=EdgeKind.DOOR, door=DoorSpec(requires=sentinel)),
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
    items=(GearTemplate(id="brass_key", name="Brass key", cost_gp=0),),
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

# Keyless, the sentinel's door is an illegal command — and the refusal costs nothing.
refused = session.execute(OpenDoor(direction=Direction.EAST))
assert not refused.accepted
assert refused.rejections[0].code == "exploration.door.gate_refused"
assert refused.rejections[0].params["refusal"].startswith("The bronze sentinel")

session.execute(GrantItem(character_id="character-0001", item_id="brass_key"))
opened = session.execute(OpenDoor(direction=Direction.EAST))
assert opened.accepted
assert opened.events[0].narrative == "The brass key turns in the sentinel's palm and the door swings wide."

result = session.execute(MoveParty(direction=Direction.EAST))
assert result.accepted

# Stepping into the keyed area spawns the goblins and starts an encounter.
assert session.mode is SessionMode.ENCOUNTER
assert len(session.monsters) == 2
```

## Where next

- The example games ship complete authored adventures worth reading: [the TUI crawler](../front-ends/tui-crawler.md) builds a two-level barrow with a fetch quest, custom wandering tables, and a hand-placed MacGuffin.
- [Sessions, commands, and events](../guides/sessions-commands-events.md) — what happens after the encounter starts.
- [Authoring custom classes, spells, monsters, and items](../guides/authoring-custom-content.md) — extending the content catalogs themselves.
