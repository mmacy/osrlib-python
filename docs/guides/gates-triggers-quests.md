# Gates, triggers, and quests

You want a door that opens only with the right key, a lever that opens a portcullis across the map, an errand that ends the adventure when the party finishes it. You author all three as data: a **gate** guards an attempt, a **trigger** reacts to an event, and a **quest** tracks progress toward an ending. All three live in the adventure document beside the dungeons they connect. They have no effect until your game registers the library's [`Interpreter`][osrlib.crawl.interpreter.Interpreter], a listener like the ones in [Listeners and flags](listeners-and-flags.md). The library includes it because every authored adventure needs one. [The complete program](#the-complete-program) at the end runs as written, and every snippet along the way comes from it. Where a snippet comes from [the TUI crawler's](../front-ends/tui-crawler.md) authored adventure instead, the text says so.

The door (the edge and its [`DoorSpec`][osrlib.crawl.dungeon.DoorSpec]) is dungeon geometry. [Building an adventure](../getting-started/building-an-adventure.md#the-grid-and-its-edges) covers it, along with the keyed areas and transitions you attach these conditions to.

## Gating a door or a stair

A door or a level transition can have a [`GateSpec`][osrlib.crawl.gates.GateSpec] on its `requires` field: an authored condition the party must satisfy for the attempt to be legal. The engine evaluates the condition live, at the moment the party tries the door, and stores nothing, so once the party drops or sells the key, the door no longer opens for them:

- [`HasItemCondition`][osrlib.crawl.gates.HasItemCondition] - some member's carried inventory contains an item with that catalog id. Any member's pack counts, equipped slots included. The id must resolve against the equipment catalog (bundled items included, see [authoring custom content](authoring-custom-content.md)) or the magic-item catalog. A gate with an unknown id fails validation.
- [`FlagEqualsCondition`][osrlib.crawl.gates.FlagEqualsCondition] - a session flag equals a value. Your game sets flags with [`SetFlag`][osrlib.crawl.commands.SetFlag], so use this condition for a lever that opens a portcullis. The comparison is strict: an absent key matches nothing, not even `False`, and a stored `True` doesn't satisfy an authored `1`.
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

Locks and gates are separate layers: on a door with both, the party must open the lock and satisfy the gate. The engine checks the lock first (`exploration.door.locked`), and once a thief has picked the lock ([`PickLock`][osrlib.crawl.commands.PickLock] affects only the lock), the gate still applies. The engine doesn't check the gate on a door that's open. Set a gated door open with [`SetDoorState`][osrlib.crawl.commands.SetDoorState] and the party passes freely until the door closes again, at which point the gate applies once more. For a one-time unlock that changes a door's state permanently, like the lever thrown once that leaves the portcullis up, use a [trigger](#scripting-the-dungeon-with-triggers): a `SetDoorState` consequence fired on the lever's flag.

`consumes=True` turns a `has_item` condition into a toll: each time the gated command succeeds, the engine removes one instance from the first member in marching order who has one, and reports it with [`ItemConsumedEvent`][osrlib.crawl.events.ItemConsumedEvent] just before the door or arrival event. Every success charges again, so when a door that consumes keys swings shut, opening it again takes another key. Coins are not items, so a gate can't charge them. To charge a fee, define a token as a bundled item and gate on that.

A [`NarrativeBlock`][osrlib.crawl.narrative.NarrativeBlock] contains the authored text for the mechanical object it's attached to. Each text field in the block is a *beat*. The engine uses two of them for a gate: `refusal`, returned in the rejection, and `success`, which goes on the successful command's event, the [`DoorEvent`][osrlib.crawl.events.DoorEvent] for a door and the [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent] for a transition that crosses into a new level or dungeon. [`format_message`][osrlib.messages.format_message] appends the beat verbatim after the event's default English line, so a front end that prints only those lines still shows it. A transition whose destination is on the same level crosses no boundary, and the engine emits no arrival event for it, so a success beat there is never displayed.

To show the gate's `guidance` alongside its success text, a narrator maps the event back to the object the gate is attached to. A door event includes the cell and the side. A transition's arrival event includes, in `transition_ref`, the cell the transition stands on. The narrator doesn't have to work out either object from where the party is standing now. A passage stands on one side of its cell, so for a passage crossing the narrator also needs the direction of the [`MoveParty`][osrlib.crawl.commands.MoveParty], and [`LevelSpec.passage_at`][osrlib.crawl.dungeon.LevelSpec.passage_at] finds the passage from the cell and that direction. The block's other fields are `journal`, `guidance` for an LLM narrator, and `speaker`. osrlib never reads a gate's `journal` beat. To add a journal entry for a door, use [a trigger](#scripting-the-dungeon-with-triggers). The player view contains no part of a gate's narrative block and no gate setup.

## Scripting the dungeon with triggers

A gate governs an attempt, and the engine checks it every time the party makes one. A trigger works the other way: it reacts to something that already happened. A [`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec] connects a pattern that matches an event to consequences, which are referee commands. Examples are the lever that opens the portcullis, the idol whose theft wakes the temple, and the room that adds a line to the party's journal the first time the party crosses it:

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

Triggers live in the adventure document alongside the content they connect. `Adventure.items` contains the item templates the adventure defines: the brass key the sentinel gate requires is content, not equipment that comes with osrlib (see [authoring custom content](authoring-custom-content.md) for the rules on bundling items). `Adventure.triggers` contains the connections, in document order. When two triggers match the same event, they fire in the order you wrote them.

Triggers have no effect until your game registers the library's [`Interpreter`][osrlib.crawl.interpreter.Interpreter] on the session. Register it right after you build the session, and again after you load a save, because listeners are code and a save contains only data:

```{.python .no-run}
session = GameSession.new(Party(members=[hero.character]), adventure, seed=11)
session.register_listener(Interpreter(session))
```

### What a trigger watches

`when` is one pattern from a small union. Each pattern matches an event the engine already emits:

- [`AreaEnteredPattern`][osrlib.crawl.triggers.AreaEnteredPattern] - the party stepped into a keyed area. Area ids are scoped to their level, so the pattern includes all three: `dungeon_id`, `level_number`, `area_id`.
- [`LevelEnteredPattern`][osrlib.crawl.triggers.LevelEnteredPattern] - the party arrived on a level, by stair or by walking in from town.
- [`DungeonEnteredPattern`][osrlib.crawl.triggers.DungeonEnteredPattern] and [`TownEnteredPattern`][osrlib.crawl.triggers.TownEnteredPattern] - the coarser crossings. The town pattern needs no fields, because an adventure has one town.
- [`ItemAcquiredPattern`][osrlib.crawl.triggers.ItemAcquiredPattern] - a member acquired an item with that catalog id, from a cache, a grant, or another member's hands.
- [`MonsterDefeatedPattern`][osrlib.crawl.triggers.MonsterDefeatedPattern] - a monster of that template was defeated: slain and routed both count. The engine reports defeats when the battle ends, so you can't author "the portcullis opens the instant the boss falls". It opens when the fighting stops.
- [`FlagSetPattern`][osrlib.crawl.triggers.FlagSetPattern] - a flag was written. Use it for the lever: your game (or another trigger) executes [`SetFlag`][osrlib.crawl.commands.SetFlag], and the trigger watching that key fires. The pattern matches the value that was written, and `value=None` matches any value.

`conditions` narrows the match further, with the same [condition union the gates use](#gating-a-door-or-a-stair). Every one of the conditions must hold, evaluated live at the moment of the match, so you can add "…and only if somebody is still carrying the talisman". One difference from a gate: a trigger's condition can't set `consumes=True`. A trigger reacts to something that already happened, and there's no attempt to charge a toll against.

By default a trigger fires only once, and the mark that records the firing is session state that survives a save, a load, and a replay. Set `repeatable=True` to fire it every time the pattern matches.

### What a firing does

The interpreter issues ordinary referee commands, and stamps every one `source="trigger:{id}"` so you can read from the command log alone why a command ran:

1. [`MarkTriggerFired`][osrlib.crawl.commands.MarkTriggerFired], first, with the `fired` beat.
2. Your `consequences`, in the order you wrote them.
3. [`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry], last, when the narrative block has a `journal` form.

A consequence is one of the [`ConsequenceCommand`][osrlib.crawl.commands.ConsequenceCommand] types: `GrantItem`, `GrantCoins`, `AwardXP`, `SetFlag`, `SpawnMonsters`, `SpawnNpcParty`, `SetDoorState`, `PlaceParty`, and `AdvanceTime`. Anything else fails to parse. A consequence that hands something to a character identifies the character with a party selector instead of an id: [`PARTY_SELECTOR`][osrlib.crawl.triggers.PARTY_SELECTOR] (`"@party"`) becomes one command per living member in marching order, and [`FIRST_LIVING_SELECTOR`][osrlib.crawl.triggers.FIRST_LIVING_SELECTOR] (`"@first"`) becomes one command for the lead survivor. Character ids are allocated per session, so an id in a document would refer to a character that doesn't exist when the engine reads the document. Validation rejects it. A `SpawnMonsters` consequence issued while an encounter is open adds its monsters to that encounter, so a trigger on a keyed area can bring reinforcements to the monsters already there.

The two beats have two different audiences: **`fired` is the referee's line and `journal` is the players'.** The `fired` text goes on a referee-visibility event, because the players aren't meant to see these connections. The journal entry is player-visible and appears verbatim in the [`PlayerView`][osrlib.crawl.views.PlayerView]. If you want the table to read something when a trigger fires, write the journal form.

### When a consequence fails

A trigger firing is not all-or-nothing. When the session rejects one consequence, like a `SpawnNpcParty` refused because an encounter is already open, or a grant of an item the catalog no longer has, that consequence alone is dropped and the consequences after it still run. A [`RecordNote`][osrlib.crawl.commands.RecordNote] records the trigger, the consequence's position and type, and the rejection code. There is no retry and no queue, because a consequence that fired later, out of order, would be impossible to debug.

Cascades are bounded. The events a trigger causes are one level deeper than the event that fired it, and matching stops below depth five. When the depth limit stops a firing, the interpreter records a note rather than a mark, so a once-only trigger cut short there can still fire later. Chaining flags is a normal thing to author, and the depth limit makes sure a chain that loops still ends.

## Authoring a quest

A trigger reacts to an event and has no progress to track. A quest tracks progress: it has a state the engine manages, objectives that complete in any order, and an ending. You author a [`QuestSpec`][osrlib.crawl.quests.QuestSpec] beside the triggers, in the same adventure document, and the same [`Interpreter`][osrlib.crawl.interpreter.Interpreter] runs it, so you register nothing extra. `Adventure.quests` is document order too, and the interpreter walks an event's triggers before its quests, so a trigger's consequences have already run by the time the interpreter checks a quest's clauses against the same event.

Quests use the same patterns and conditions as triggers. Wherever a quest tests whether something happened, it tests with a [`TriggerClause`][osrlib.crawl.quests.TriggerClause]: one of the patterns above, plus the conditions that must hold when the pattern matches. The field is `pattern` rather than `when`, so an objective's completion clause reads `objective.when.pattern`.

Here's a whole quest, the TUI crawler's fetch errand, verbatim from the example:

```{.python .no-run}
--8<-- "examples/tui_crawler/content.py:fetch-quest"
```

### Matching on a thing the party carries

The idol in that quest is a bundled item, not a named valuable, and that's deliberate: the event for an acquisition lists mundane items by catalog id, so [`ItemAcquiredPattern`][osrlib.crawl.triggers.ItemAcquiredPattern] can match a bundled id and [`HasItemCondition`][osrlib.crawl.gates.HasItemCondition] can test it.

```{.python .no-run}
--8<-- "examples/tui_crawler/content.py:bundled-idol"
```

The `cost_gp=0` on the idol, and on the brass key in [the complete program](#the-complete-program), has no effect. The town shop stocks **only** the item lists that come with osrlib, and it refuses a bundled id with `items.purchase.not_stocked` (see [the bundling contract](authoring-custom-content.md#bundling-custom-items-with-an-adventure)).

Drop the idol into a cache by id (`item_ids=("jade-idol",)`) and add its template to `Adventure.items`, and every step of the errand can be matched: *took it* is a pattern, and *still carrying it* is a condition. A `town_entered` clause narrowed by `has_item` tests whether the party walked home with the idol, and walking home without the idol doesn't fire it.

### Activation, and the quest that needs none

`activation` is a clause like any other. When it matches, the quest becomes active, its `offer` beat goes on the activation event and into the journal, and the interpreter starts matching its objectives. Omit it and the quest is active from the start of the session. A quest with no `activation` has no activation event and no offer entry in the journal, because the quest is active as soon as the session exists, before any command runs, and only commands produce events. The first player view shows its offer instead.

### Hidden objectives and reveals

`objectives` contains at least one [`ObjectiveSpec`][osrlib.crawl.quests.ObjectiveSpec], in the order you write them, and each one has a separate completion clause. `hidden=True` keeps an objective out of the player view until either its `reveal_when` clause matches or the party completes it, because completing an objective reveals it. A hidden objective with no reveal clause is normal: the party learns about it by doing it. A `reveal_when` on an objective that was never hidden is rejected when the document is parsed, because the engine would never read it.

### The completion rule and the ending

`completion` is `"all"` (the default: every objective) or `"any"` (the first one to complete, leaving the rest incomplete). The interpreter checks the rule the moment an objective completes, and a satisfied rule completes the quest.

`concludes_adventure=True` marks the quest whose completion ends the adventure. The session clears any open encounter or battle and enters `victory`, a terminal mode where the session refuses play commands and still accepts referee commands. That's the **only** way a session enters `victory`, so author it once, on the quest that is the point of the module. [`AdventureCompletedEvent`][osrlib.crawl.events.AdventureCompletedEvent] (`session.adventure.completed`) reports the transition and includes the concluding quest's completion beat. A front end watches for that event to show its victory screen.

### Rewards

`rewards` use the same [`ConsequenceCommand`][osrlib.crawl.commands.ConsequenceCommand] types a trigger's consequences do. The interpreter issues them in authored order *after* the quest completes, each stamped `source="quest:{id}"`. They address characters through the same selectors, `@party` and `@first`, and validation rejects a literal character id for the same reason it does on a trigger.

That ordering has two effects to plan for. On a concluding quest the session is already in `victory` when the interpreter issues the rewards, so it refuses a reward that would resume play (`SpawnMonsters`, `SpawnNpcParty`, `PlaceParty`) and drops it with a note. Grants, awards, and flags work as usual. Coins granted as a reward don't count toward treasure XP, because the end-of-adventure award has already been paid by then. Give the reward as `AwardXP` instead of coins.

### Which beat goes where

The session reads four display beats from a quest's narrative blocks:

| Moment | Block | Field |
|---|---|---|
| The quest activates | quest | `offer` |
| A hidden objective is revealed | objective | `offer` |
| An objective completes | objective | `progress` |
| The quest completes | quest | `completion` |

Each of those beats goes on a separate player-visible event *and* into the journal, as written. A quest's journal is the transcript of what the table was shown, so quest blocks don't use the `journal` field. That field is for blocks whose display beat the players never see, like a trigger's `fired`. osrlib never reads a quest block's `progress` or an objective block's `completion`, and it ignores them without rejecting them.

### Steering a narrator

`guidance` on any narrative block is text that a narrating front end can use to steer its narration. No renderer prints it. A level has a `guidance` field too, for atmosphere that isn't attached to any object:

```{.python .no-run}
--8<-- "examples/tui_crawler/content.py:level-guidance"
```

[`LevelSpec.guidance`][osrlib.crawl.dungeon.LevelSpec] is authored data that the engine never reads. No event includes it, and no rule depends on it. A narrating front end reads it out of the adventure document instead, and uses it for as long as the party is on that level. Like the rest of a level's internal data, it's for the referee only: the player view includes no part of it.

## The complete program

One corridor uses all three mechanisms. Entering the dungeon activates the quest. Acquiring the brass key fires the trigger and completes the quest. The gated door stays shut for the keyless party and opens once they have the key:

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

# Stepping into the keyed area creates the goblins and starts an encounter.
assert session.mode is SessionMode.ENCOUNTER
assert len(session.monsters) == 2
```

## Where next

- [The TUI crawler](../front-ends/tui-crawler.md) - the fetch quest this page shows, in its full adventure context: a two-level barrow, a concluding quest, and the victory ending.
- [Sessions, commands, and events](sessions-commands-events.md) - the lifecycle commands the interpreter issues, and the victory mode a concluding quest enters.
- [Determinism, saves, and replay](determinism-saves-replay.md) - how trigger marks, the journal, and quest state survive a save and rebuild under replay.
- [Views and visibility](views-and-visibility.md) - what a quest projects into the player view, and what stays hidden from the players.
