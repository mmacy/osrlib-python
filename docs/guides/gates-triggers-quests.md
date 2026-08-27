# Gates, triggers, and quests

You want a door that needs a key, a lever that opens a portcullis across the map, an errand that ends the adventure when the party finishes it. You author all three as data: a **gate** guards an attempt, a **trigger** reacts to an event, and a **quest** keeps score toward an ending. All three live in the adventure document beside the dungeons they wire. Nothing plays them until your game registers the library's [`Interpreter`][osrlib.crawl.interpreter.Interpreter], a listener like the ones in [Listeners and flags](listeners-and-flags.md). The library ships it because you need one for every authored adventure. [The complete program](#the-complete-program) at the end runs as written, and every fragment along the way is an excerpt of it. Where a fragment comes from [the TUI crawler's](../front-ends/tui-crawler.md) authored adventure instead, the text says so.

The door itself, the edge and its [`DoorSpec`][osrlib.crawl.dungeon.DoorSpec], is dungeon geometry. [Building an adventure](../getting-started/building-an-adventure.md#the-grid-and-its-edges) covers it, along with the keyed areas and transitions you hang these conditions on.

## Gating a door or a stair

A door or a level transition can hold a [`GateSpec`][osrlib.crawl.gates.GateSpec] on its `requires` field: an authored condition the party must satisfy for the attempt to be legal. The engine evaluates the condition live, at the moment the party tries the door, and stores nothing, so a key that gets dropped or sold stops opening it:

- [`HasItemCondition`][osrlib.crawl.gates.HasItemCondition] - some member's carried inventory holds an item with that catalog id. Any member's pack counts, equipped slots included. The id must resolve against the equipment catalog (bundled items included, see [authoring custom content](authoring-custom-content.md)) or the magic-item catalog. A gate naming an unknown id fails validation.
- [`FlagEqualsCondition`][osrlib.crawl.gates.FlagEqualsCondition] - a session flag holds a value. Your game sets flags with [`SetFlag`][osrlib.crawl.commands.SetFlag], so this is the lever-opens-the-portcullis wiring. The comparison is strict: an absent key matches nothing, not even `False`, and a stored `True` never satisfies an authored `1`.
- [`EffectActiveCondition`][osrlib.crawl.gates.EffectActiveCondition] - an active effect of that kind is attached to a party member. Use it when the talisman has to be invoked rather than merely carried.

A refused attempt is an ordinary rejection, `exploration.door.gate_refused` or `exploration.transition.gate_refused`, with the gate's authored refusal text in it. It costs nothing: no dice, no game time, no items, and no change to the door.

```{.python .no-run}
sentinel = GateSpec(
    condition=HasItemCondition(item_id="brass_key"),
    narrative=NarrativeBlock(
        refusal="The bronze sentinel folds its arms. Brass, it says. Brass or nothing.",
        success="The brass key turns in the sentinel's palm and the door swings wide.",
    ),
)
```

Locks and gates are separate layers, and a door with both requires both. The engine checks the lock first (`exploration.door.locked`), and once a thief has picked the lock ([`PickLock`][osrlib.crawl.commands.PickLock] addresses the lock and nothing else), the gate still applies. A door standing open lets the party through unchecked. Set a gated door open with [`SetDoorState`][osrlib.crawl.commands.SetDoorState] and the party passes freely until the door closes again, at which point the gate applies once more. For a one-time unlock that flips a door's state for good, like the lever thrown once that leaves the portcullis up, use a [trigger](#wiring-the-dungeon-with-triggers): a `SetDoorState` consequence fired on the lever's flag.

`consumes=True` turns a `has_item` condition into a toll: each time the gated command succeeds, one instance leaves the first holder in marching order, reported by [`ItemConsumedEvent`][osrlib.crawl.events.ItemConsumedEvent] just before the door or arrival event. Every success charges again, so a consumed key-door that swings shut takes another key. Coins are not items and can't be tolled. To charge one, mint a token as a bundled item and gate on that.

A [`NarrativeBlock`][osrlib.crawl.narrative.NarrativeBlock] holds the authored text for the mechanical object it hangs on. Gates read two of its beats: `refusal`, returned in the rejection, and `success`, which goes on the successful command's event, the [`DoorEvent`][osrlib.crawl.events.DoorEvent] for a door and the [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent] for a transition that crosses into a new level or dungeon. [`format_message`][osrlib.messages.format_message] appends the beat verbatim, so it shows up in a bare transcript. A transition whose destination is its own level crosses no boundary and emits no arrival event, so a success beat there has nowhere to display. The block's other fields are `journal`, `guidance` for an LLM narrator, and `speaker`, each read by the surface that consumes it. A gate's `journal` beat has no consumer at all: journaling a door is [a trigger's](#wiring-the-dungeon-with-triggers) job. No part of a gate's narrative block reaches the player view, which holds no gate wiring at all.

## Wiring the dungeon with triggers

A gate governs an attempt, and the engine checks it every time the party makes one. A trigger works the other way: it reacts to something that already happened. A [`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec] binds an observable event pattern to referee-command consequences, like the lever that opens the portcullis, the idol whose theft wakes the temple, or the room whose first crossing writes a line in the party's journal:

```{.python .no-run}
sentinel_wakes = TriggerSpec(
    id="sentinel-wakes",
    when=ItemAcquiredPattern(item_id="brass_key"),
    consequences=(SetFlag(key="barrow.key_found", value=True),),
    narrative=NarrativeBlock(
        fired="The sentinel's head turns a few degrees, and stops.",
        journal="The brass key is ours. Something in the barrow noticed.",
    ),
)
```

Triggers live in the adventure document alongside the content they wire. `Adventure.items` holds the adventure's own item templates: the brass key the sentinel wants is content, not shipped equipment (see [authoring custom content](authoring-custom-content.md) for the whole bundling contract). `Adventure.triggers` holds the wiring, and the tuple is document order. When two triggers match the same event, they fire in the order you wrote them.

Triggers are inert content on their own. They play once your game registers the library's [`Interpreter`][osrlib.crawl.interpreter.Interpreter] on the session. Register it right after you build the session, and again after you load a save, because listeners are code and a save holds only data:

```{.python .no-run}
session = GameSession.new(Party(members=[hero.character]), adventure, seed=11)
session.register_listener(Interpreter(session))
```

### What a trigger watches

`when` is one pattern from a small union. Each pattern names an event the engine already emits:

- [`AreaEnteredPattern`][osrlib.crawl.triggers.AreaEnteredPattern] - the party stepped into a keyed area. Area ids are scoped to their level, so the pattern names the whole triple: `dungeon_id`, `level_number`, `area_id`.
- [`LevelEnteredPattern`][osrlib.crawl.triggers.LevelEnteredPattern] - the party arrived on a level, by stair or by walking in from town.
- [`DungeonEnteredPattern`][osrlib.crawl.triggers.DungeonEnteredPattern] and [`TownEnteredPattern`][osrlib.crawl.triggers.TownEnteredPattern] - the coarser crossings. The town pattern needs no fields, because an adventure has one town.
- [`ItemAcquiredPattern`][osrlib.crawl.triggers.ItemAcquiredPattern] - a member acquired an item with that catalog id, from a cache, a grant, or another member's hands.
- [`MonsterDefeatedPattern`][osrlib.crawl.triggers.MonsterDefeatedPattern] - a monster of that template was defeated: slain, routed, and surrendered all count. The engine reports defeats when the battle ends, so you can't author "the portcullis opens the instant the boss falls". It opens when the fighting stops.
- [`FlagSetPattern`][osrlib.crawl.triggers.FlagSetPattern] - a flag was written. This is the lever: your game (or another trigger) executes [`SetFlag`][osrlib.crawl.commands.SetFlag], and the trigger watching that key fires. The match is on the value the write set, and `value=None` matches any value at all.

`conditions` narrows the match further, with the same [condition union the gates use](#gating-a-door-or-a-stair). Every one of the conditions must hold, evaluated live at the moment of the match, so you can add "…and only if somebody is still carrying the talisman". One difference from a gate: a trigger's condition can't set `consumes=True`. A trigger reacts to something that already happened, and there's no attempt of its own to charge a toll against.

By default a trigger fires once ever, and the fired-mark is session state that survives a save, a load, and a replay. Set `repeatable=True` to fire it every time the pattern matches.

### What a firing does

The interpreter issues ordinary referee commands, and stamps every one `source="trigger:{id}"` so you can read from the command log alone why a command ran:

1. [`MarkTriggerFired`][osrlib.crawl.commands.MarkTriggerFired], first, with the `fired` beat.
2. Your `consequences`, in the order you wrote them.
3. [`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry], last, when the narrative block has a `journal` form.

A consequence is one of the [`ConsequenceCommand`][osrlib.crawl.commands.ConsequenceCommand] types: `GrantItem`, `GrantCoins`, `AwardXP`, `SetFlag`, `SpawnMonsters`, `SpawnNpcParty`, `SetDoorState`, `PlaceParty`, and `AdvanceTime`. Anything else fails to parse. A consequence that hands something to a character names the character with a party selector rather than an id: [`PARTY_SELECTOR`][osrlib.crawl.triggers.PARTY_SELECTOR] (`"@party"`) becomes one command per living member in marching order, and [`FIRST_LIVING_SELECTOR`][osrlib.crawl.triggers.FIRST_LIVING_SELECTOR] (`"@first"`) becomes one command for the lead survivor. Character ids are allocated per session, so a document naming one would name something that doesn't exist when the engine reads the document. Validation rejects it.

The two beats have two different audiences: **`fired` is the referee's line and `journal` is the players'.** The `fired` text goes on a referee-visibility event, because content wiring is your game's secret. The journal entry is player-visible and ships verbatim in the [`PlayerView`][osrlib.crawl.views.PlayerView]. If you want the table to read something when a trigger fires, write the journal form.

### When something doesn't land

A trigger firing is not all-or-nothing. When the session rejects one consequence, like a spawn that arrives to find an encounter already open or a grant naming an item the catalog lost, that consequence alone is dropped and the consequences after it still run. A [`RecordNote`][osrlib.crawl.commands.RecordNote] records the trigger, the consequence's position and type, and the rejection code. There is no retry and no queue, because a consequence that fired later, out of order, would be impossible to debug.

Cascades are bounded. A trigger's own events are one level deeper than the event that fired it, and matching stops below depth five. A firing the bound suppresses is recorded as a note rather than a mark, so a once-only trigger cut short there can still fire later. Chaining flags is a normal thing to author, and the bound is what guarantees a chain that loops still ends.

## Authoring a quest

A trigger fires once and it's done. A quest keeps score: it has a state the engine owns, objectives that complete in any order, and an ending. You author a [`QuestSpec`][osrlib.crawl.quests.QuestSpec] beside the triggers, in the same adventure document, and the same [`Interpreter`][osrlib.crawl.interpreter.Interpreter] plays it, so you register nothing extra. `Adventure.quests` is document order too, and the interpreter walks an event's triggers before its quests, so a trigger's consequences have already landed by the time the interpreter checks a quest's clauses against the same event.

Nothing in the vocabulary is new. Wherever a quest tests whether something happened, it tests with a [`TriggerClause`][osrlib.crawl.quests.TriggerClause]: one of the patterns above, plus the conditions that must hold when the pattern matches. The field is `pattern` rather than `when`, so an objective's completion clause reads `objective.when.pattern`.

Here's a whole quest, the TUI crawler's fetch errand, verbatim from the example:

```{.python .no-run}
--8<-- "examples/tui_crawler/content.py:fetch-quest"
```

### Matching on a thing the party carries

The idol in that quest is a bundled item, not a named valuable, and that's deliberate: an acquisition reports mundane items by catalog id, so [`ItemAcquiredPattern`][osrlib.crawl.triggers.ItemAcquiredPattern] can match a bundled id and [`HasItemCondition`][osrlib.crawl.gates.HasItemCondition] can test it.

```{.python .no-run}
--8<-- "examples/tui_crawler/content.py:bundled-idol"
```

The `cost_gp=0` on the idol, and on the brass key in [the complete program](#the-complete-program), isn't a bargain. A bundled item's price is moot, because the town shop stocks the shipped lists **only** and refuses a bundled id with `items.purchase.not_stocked` (see [the bundling contract](authoring-custom-content.md#bundling-custom-items-with-an-adventure)).

Drop the idol into a cache by id (`item_ids=("jade-idol",)`) and add its template to `Adventure.items`, and the whole errand becomes matchable: *took it* is a pattern, *still carrying it* is a condition. A `town_entered` clause narrowed by `has_item` is the walked-home-with-it test, and walking home without the idol doesn't fire it.

### Activation, and the quest that needs none

`activation` is a clause like any other. When it matches, the quest becomes active, its `offer` beat displays and lands in the journal, and the interpreter starts matching its objectives. Omit it and the quest is active from session start, a standing charge from round 0. A quest with no `activation` has no activation event and no offer entry in the journal, because there's no command channel before the first command. Its offer stands in the first player view instead.

### Hidden objectives and reveals

`objectives` holds at least one [`ObjectiveSpec`][osrlib.crawl.quests.ObjectiveSpec], in the order you write them, and each one has a completion clause of its own. `hidden=True` keeps an objective out of the player view until either its own `reveal_when` clause matches or the party completes it, because completing an objective reveals it. A hidden objective with no reveal clause is a normal shape: the party learns about it by doing it. A `reveal_when` on an objective that was never hidden is rejected at parse, because nothing would read that wiring.

### The completion rule and the ending

`completion` is `"all"` (the default: every objective) or `"any"` (the first one to land, leaving the rest incomplete). The interpreter checks the rule the moment an objective completes, and a satisfied rule completes the quest.

`concludes_adventure=True` marks the quest whose completion ends the adventure. The session clears any open encounter or battle and enters `victory`, a terminal mode where the session refuses play commands and still accepts referee commands. That's the **only** entrance to victory, so author it once, on the quest that is the point of the module. [`AdventureCompletedEvent`][osrlib.crawl.events.AdventureCompletedEvent] (`session.adventure.completed`) reports the transition and includes the concluding quest's completion beat. A front end watches for that event to show its victory screen.

### Rewards

`rewards` use the same [`ConsequenceCommand`][osrlib.crawl.commands.ConsequenceCommand] surface a trigger's consequences do. The interpreter issues them in authored order *after* the quest completes, each stamped `source="quest:{id}"`. They address characters through the same selectors, `@party` and `@first`, and validation rejects a literal character id for the same reason it does on a trigger.

Two consequences of that ordering are worth authoring around. On a concluding quest the session is already in `victory` when the rewards issue, so it refuses a reward that would resume play (`SpawnMonsters`, `SpawnNpcParty`, `PlaceParty`) and drops it with a note. Grants, awards, and flags land fine. Coin paid on the doorstep earns no treasure XP, because the end-of-adventure award has already fired by then. Put the story's thanks in `AwardXP` rather than in a purse of coin.

### Which beat goes where

The session reads four display beats from a quest's narrative blocks:

| Moment | Block | Field |
|---|---|---|
| The quest activates | quest | `offer` |
| A hidden objective is revealed | objective | `offer` |
| An objective completes | objective | `progress` |
| The quest completes | quest | `completion` |

Each of those beats goes on its own player-visible event *and* appends to the journal, as itself. A quest's journal is the transcript of what the table was shown, so quest blocks leave the `journal` field to the blocks whose display beat the players never see, like a trigger's `fired`. Nothing reads a quest block's `progress` or an objective block's `completion`. They're silently unread, not rejected.

### Steering a narrator

`guidance` on any narrative block is text a narrating front end may steer by, and no renderer ever prints it. A level gets a `guidance` field of its own, for the ambience that hangs on no object at all:

```{.python .no-run}
--8<-- "examples/tui_crawler/content.py:level-guidance"
```

[`LevelSpec.guidance`][osrlib.crawl.dungeon.LevelSpec] is inert authored data. The engine never reads it, no event includes it, and no rule turns on it. A narrating front end reads it out of the adventure document instead, and steers by it for as long as the party is on that level. Like every other level internal, it's referee-side by construction: the player view ships no part of it.

## The complete program

One corridor uses all three mechanisms. Entering the dungeon activates the quest. The brass key's arrival fires the trigger and completes the quest. The gated door stays shut for the keyless party and opens once they have the key:

```python
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.items import GearTemplate
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec, validate_adventure
from osrlib.crawl.commands import EnterDungeon, GrantItem, MoveParty, OpenDoor, SessionMode, SetFlag
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
from osrlib.crawl.interpreter import Interpreter
from osrlib.crawl.narrative import NarrativeBlock
from osrlib.crawl.party import Party
from osrlib.crawl.quests import ObjectiveSpec, QuestSpec, TriggerClause
from osrlib.crawl.session import GameSession
from osrlib.crawl.triggers import DungeonEnteredPattern, ItemAcquiredPattern, TriggerSpec
from osrlib.data import load_equipment, load_monsters

sentinel = GateSpec(
    condition=HasItemCondition(item_id="brass_key"),
    narrative=NarrativeBlock(
        refusal="The bronze sentinel folds its arms. Brass, it says. Brass or nothing.",
        success="The brass key turns in the sentinel's palm and the door swings wide.",
    ),
)

# The level: a 4x1 corridor, entered at the west end, with the gated door at the far end.
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

sentinel_wakes = TriggerSpec(
    id="sentinel-wakes",
    when=ItemAcquiredPattern(item_id="brass_key"),
    consequences=(SetFlag(key="barrow.key_found", value=True),),
    narrative=NarrativeBlock(
        fired="The sentinel's head turns a few degrees, and stops.",
        journal="The brass key is ours. Something in the barrow noticed.",
    ),
)

recover_the_key = QuestSpec(
    id="the-key",
    name="The Brass Key",
    activation=TriggerClause(pattern=DungeonEnteredPattern(dungeon_id="barrow")),
    objectives=(
        ObjectiveSpec(
            id="find-the-key",
            when=TriggerClause(pattern=ItemAcquiredPattern(item_id="brass_key")),
            narrative=NarrativeBlock(progress="The key came out of the spoil heap, green with age."),
        ),
    ),
    rewards=(SetFlag(key="barrow.errand", value="done"),),
    narrative=NarrativeBlock(
        offer="Bring the brass key back up, and the sentinel's door is somebody else's problem.",
        completion="The key is out of the barrow. The errand is closed.",
    ),
)

barrow = DungeonSpec(id="barrow", name="The Barrow", levels=(level,))
town = TownSpec(name="Threshold", travel_turns={"barrow": 2})
adventure = Adventure(
    name="The Barrow of the Knucklebone Goblins",
    town=town,
    dungeons=(barrow,),
    items=(GearTemplate(id="brass_key", name="Brass key", cost_gp=0),),
    triggers=(sentinel_wakes,),
    quests=(recover_the_key,),
)

# Validation catches unknown ids and broken geometry before play ever starts.
validate_adventure(adventure, load_monsters(), load_equipment())

rules = Ruleset()
creation = RngStreams(master_seed=11).get(CHARACTER_CREATION_STREAM)
hero = create_character(name="Brakka", class_id="dwarf", alignment=Alignment.LAWFUL, ruleset=rules, stream=creation)
session = GameSession.new(Party(members=[hero.character]), adventure, seed=11)
session.register_listener(Interpreter(session))

session.execute(EnterDungeon(dungeon_id="barrow"))
# Crossing the threshold activated the quest, and its offer opened the journal.
assert session.quests["the-key"].status == "active"
assert session.journal[0].text.startswith("Bring the brass key back up")

session.execute(MoveParty(direction=Direction.EAST))
session.execute(MoveParty(direction=Direction.EAST))

# Keyless, the sentinel's door is an illegal command — and the refusal costs nothing.
refused = session.execute(OpenDoor(direction=Direction.EAST))
assert not refused.accepted
assert refused.rejections[0].code == "exploration.door.gate_refused"
assert refused.rejections[0].params["refusal"].startswith("The bronze sentinel")

# The key lands, and everything watching for it reacts inside the same command:
# the trigger first, then the quest, then the quest's reward.
granted = session.execute(GrantItem(character_id="character-0001", item_id="brass_key"))
assert [event.code for event in granted.events] == [
    "exploration.item.acquired",
    "session.trigger.fired",
    "session.flag.set",
    "session.journal.entry_added",
    "session.quest.objective_completed",
    "session.quest.completed",
    "session.flag.set",
]
assert session.fired_triggers == ["sentinel-wakes"]
assert session.flags["barrow.key_found"] is True
assert session.journal[1].text == "The brass key is ours. Something in the barrow noticed."
# One objective, the `all` rule: finishing it finished the quest, and the reward
# landed after the completion.
assert session.quests["the-key"].status == "completed"
assert session.flags["barrow.errand"] == "done"
# Every command a trigger or a quest issued says whose idea it was.
assert {command.source for command in session.command_log if command.source} == {
    "trigger:sentinel-wakes",
    "quest:the-key",
}

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

- [The TUI crawler](../front-ends/tui-crawler.md) - the fetch quest this page excerpts, in its full adventure context: a two-level barrow, a concluding quest, and the victory ending.
- [Sessions, commands, and events](sessions-commands-events.md) - the lifecycle commands the interpreter issues, and the victory mode a concluding quest enters.
- [Determinism, saves, and replay](determinism-saves-replay.md) - how fired-marks, the journal, and quest state survive a save and rebuild under replay.
- [Views and visibility](views-and-visibility.md) - what a quest projects into the player view, and what stays the game's secret.
