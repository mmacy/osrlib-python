# Gates, triggers, and quests

Author conditional doors, reactive dungeon events, and quests as declarative data on an [`Adventure`][osrlib.crawl.adventure.Adventure]:

- **Gates** ([`GateSpec`][osrlib.crawl.gates.GateSpec]) block a door or level transition until a condition is met.
- **Triggers** ([`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec]) match emitted events and execute referee commands in response.
- **Quests** ([`QuestSpec`][osrlib.crawl.quests.QuestSpec]) track objectives, issue rewards, and optionally conclude the adventure in `victory`.

Gates run automatically when the party opens a door or uses a transition (see [Building an adventure](../getting-started/building-an-adventure.md#the-grid-and-its-edges) for dungeon geometry). Triggers and quests run only when you register [`Interpreter`][osrlib.crawl.interpreter.Interpreter] on the session (see [Listeners and flags](listeners-and-flags.md)).

## Gating a door or a stair

Set `requires` on a [`DoorSpec`][osrlib.crawl.dungeon.DoorSpec] or [`TransitionSpec`][osrlib.crawl.dungeon.TransitionSpec] to a [`GateSpec`][osrlib.crawl.gates.GateSpec] to guard that passage with a condition and optional [`NarrativeBlock`][osrlib.crawl.narrative.NarrativeBlock] text:

```{.python .no-run}
sentinel = GateSpec(
    condition=HasItemCondition(item_id="brass_key"),
    narrative=NarrativeBlock(
        refusal="The bronze sentinel folds its arms. Brass, it says. Brass or nothing.",
        success="The brass key turns in the sentinel's palm and the door swings wide.",
    ),
)
gated_door = Edge(kind=EdgeKind.DOOR, door=DoorSpec(requires=sentinel))
```

The engine evaluates `GateSpec.condition` against live session state each time the party attempts to open a closed door (`OpenDoor`) or cross a transition. If the party drops or sells a required key, the gate blocks them again the next time the door is closed and opened. Each gate uses one of these condition types:

- [`HasItemCondition`][osrlib.crawl.gates.HasItemCondition]: checks whether any party member carries or equips an item matching `item_id` (resolved against the equipment catalog, bundled `Adventure.items`, or the magic-item catalog, see [authoring custom content](authoring-custom-content.md)). Setting `consumes=True` turns the gate into a toll: each time `OpenDoor` opens the closed door (or the party crosses the gated transition), the engine removes one matching item from the first carrying member in marching order and emits [`ItemConsumedEvent`][osrlib.crawl.events.ItemConsumedEvent] before the door or arrival event (walking through an already-open door consumes nothing, whereas closing and reopening the door consumes another item). Because coins are not catalog items, represent a coin toll with a bundled token item instead.
- [`FlagEqualsCondition`][osrlib.crawl.gates.FlagEqualsCondition]: checks whether `session.flags[key]` strictly equals `value` (set via [`SetFlag`][osrlib.crawl.commands.SetFlag]). Missing keys never match `False`, and `True` does not match `1`.
- [`EffectActiveCondition`][osrlib.crawl.gates.EffectActiveCondition]: checks whether any party member currently has an active effect of the specified kind (useful when an item or spell must be active rather than in a pack).

When a gate condition fails, `execute` rejects the command with `exploration.door.gate_refused` or `exploration.transition.gate_refused`, includes `narrative.refusal` in `Rejection.params`, and consumes no RNG draws, time, or items.

Locks and gates operate independently on a door:

- On a door that is both locked and gated, the engine checks the lock first (`exploration.door.locked`). Picking the lock with [`PickLock`][osrlib.crawl.commands.PickLock] clears only the lock, leaving the gate check active.
- Gates are checked only while a door is closed. Opening a gated door with [`SetDoorState`][osrlib.crawl.commands.SetDoorState] lets the party pass freely until the door closes again. To unlock a portcullis permanently when a lever flag changes, fire a [trigger](#scripting-the-dungeon-with-triggers) with a `SetDoorState` consequence.

### Gate narrative beats and narrator lookup

A [`NarrativeBlock`][osrlib.crawl.narrative.NarrativeBlock] stores authored text strings called *beats*. On a gate, the engine uses `refusal` (returned in `Rejection.params`) and `success` (attached to [`DoorEvent`][osrlib.crawl.events.DoorEvent], [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent] for cross-level or cross-dungeon transitions, or [`PartyMovedEvent`][osrlib.crawl.events.PartyMovedEvent] for same-level transitions). [`format_message`][osrlib.messages.format_message] appends `success` after its default English message. osrlib ignores `journal` on a gate's `NarrativeBlock` (use [a trigger](#scripting-the-dungeon-with-triggers) to add a journal entry when a door opens), and `PlayerView` never exposes gate definitions or their narrative blocks.

If you build an LLM narrator that reads `NarrativeBlock.guidance` alongside a gate's `success` event, map the event back to the gated spec in `session.adventure`:

- **Doors**: read the cell and direction directly from `DoorEvent`.
- **Cross-level or cross-dungeon transitions**: read the origin cell from `LocationEnteredEvent.transition_ref`.
- **Same-level transitions**: `PartyMovedEvent` has no `transition_ref`, so look up the transition on the cell the party occupied *before* the command ran.
- **Passages**: pass the pre-command cell and the [`MoveParty`][osrlib.crawl.commands.MoveParty] direction to [`LevelSpec.passage_at`][osrlib.crawl.dungeon.LevelSpec.passage_at].

## Scripting the dungeon with triggers

Add [`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec] entries to `Adventure.triggers` to run referee commands automatically when an event matches (for example, setting a flag and writing a journal entry when the party picks up a key):

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

Register [`Interpreter`][osrlib.crawl.interpreter.Interpreter] after creating the session (and again after calling `load_game`) so triggers and quests evaluate after each accepted command:

```{.python .no-run}
session = GameSession.new(Party(members=[hero.character]), adventure, seed=11)
session.register_listener(Interpreter(session))
```

When multiple triggers in `Adventure.triggers` match the same event, `Interpreter` fires them in document order. By default, each trigger fires at most once per session (tracked in `session.fired_triggers` across saves and replays). Set `repeatable=True` to fire on every match.

### Patterns and conditions

Set `TriggerSpec.when` to one of the event patterns:

- [`AreaEnteredPattern`][osrlib.crawl.triggers.AreaEnteredPattern]: matches when the party enters a keyed area (`dungeon_id`, `level_number`, and `area_id`).
- [`LevelEnteredPattern`][osrlib.crawl.triggers.LevelEnteredPattern]: matches when the party arrives on a dungeon level via stairs or from town.
- [`DungeonEnteredPattern`][osrlib.crawl.triggers.DungeonEnteredPattern] and [`TownEnteredPattern`][osrlib.crawl.triggers.TownEnteredPattern]: match dungeon entry (`dungeon_id`) or town entry (no fields required).
- [`ItemAcquiredPattern`][osrlib.crawl.triggers.ItemAcquiredPattern]: matches when a member acquires an item matching `item_id` from a cache, `GrantItem`, or another party member.
- [`MonsterDefeatedPattern`][osrlib.crawl.triggers.MonsterDefeatedPattern]: matches when a monster of `template_id` is slain or routed. Because the engine emits defeat events when combat ends, this pattern fires at the end of the battle rather than mid-round.
- [`FlagSetPattern`][osrlib.crawl.triggers.FlagSetPattern]: matches [`SetFlag`][osrlib.crawl.commands.SetFlag] for `key` (and `value`, or any value when `value=None`).

Add optional `conditions` using the same condition classes as [gates](#gating-a-door-or-a-stair) (`HasItemCondition`, `FlagEqualsCondition`, `EffectActiveCondition`) to require live state checks at the moment `when` matches. Trigger conditions cannot set `consumes=True`.

### Consequences, selectors, and visibility

When a trigger fires, `Interpreter` executes referee commands stamped with `source="trigger:{id}"` in this order:

1. [`MarkTriggerFired`][osrlib.crawl.commands.MarkTriggerFired] with `narrative.fired` (if set).
2. Each command in `consequences`, in authored order.
3. [`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry] with `narrative.journal` (if set).

`consequences` accepts any [`ConsequenceCommand`][osrlib.crawl.commands.ConsequenceCommand]: `GrantItem` (for mundane equipment or unidentified magic items), `GrantCoins`, `RemoveItem`, `RemoveCoins`, `AwardXP`, `SetFlag`, `SpawnMonsters` (which adds reinforcements when an encounter is already open), `SpawnNpcParty`, `SetDoorState`, `PlaceParty`, `AdvanceTime`, and `EndEncounter`.

Because character IDs are assigned per session, consequence commands that target a character must use a selector string rather than a literal character ID (literal IDs fail adventure validation):

- [`PARTY_SELECTOR`][osrlib.crawl.triggers.PARTY_SELECTOR] (`"@party"`): expands to one command per living party member in marching order.
- [`FIRST_LIVING_SELECTOR`][osrlib.crawl.triggers.FIRST_LIVING_SELECTOR] (`"@first"`): targets the first living party member in marching order.
- [`CARRIER_SELECTOR`][osrlib.crawl.triggers.CARRIER_SELECTOR] (`"@carrier"`, valid only on `RemoveItem`): targets the first living member in marching order carrying that item.

The two narrative beats target different audiences: `narrative.fired` attaches to `TriggerFiredEvent` (`Visibility.REFEREE`), whereas `narrative.journal` creates a player-visible journal entry exposed in [`PlayerView`][osrlib.crawl.views.PlayerView].

### When a consequence fails

If the session rejects an individual consequence (for example, `SpawnNpcParty` during an open encounter, `RemoveCoins` when the target lacks enough coins, or `RemoveItem` with `"@carrier"` when nobody carries the item), `Interpreter` skips only that consequence, logs a [`RecordNote`][osrlib.crawl.commands.RecordNote] naming the trigger, consequence index, command type, and rejection code or reason, and continues running the remaining consequences without retrying later.

To prevent infinite trigger loops, `Interpreter` bounds reaction depth. Player commands execute at depth `0`, and each triggered or quest-driven command runs at `depth + 1`. At depth `5` or deeper, `Interpreter` suppresses further firings and logs a `RecordNote` instead of executing `MarkTriggerFired`, so a once-only trigger halted by the depth cap can still fire on a later turn.

## Authoring a quest

Add [`QuestSpec`][osrlib.crawl.quests.QuestSpec] entries to `Adventure.quests` to track multi-step objectives and rewards. The same registered [`Interpreter`][osrlib.crawl.interpreter.Interpreter] evaluates `Adventure.quests` in document order *after* checking all triggers for an event, so flag or item changes from a trigger are already in place when quest clauses run.

Quest rules use [`TriggerClause`][osrlib.crawl.quests.TriggerClause], which pairs `pattern` (any trigger pattern above) with optional `conditions`:

```{.python .no-run}
--8<-- "examples/tui_crawler/content.py:fetch-quest"
```

### Bundled quest items

Represent unique quest items (such as the Jade Idol above or the brass key in [the complete program](#the-complete-program)) as bundled [`GearTemplate`][osrlib.core.items.GearTemplate] entries in `Adventure.items` rather than named valuables, because acquisition events report mundane items by catalog ID for [`ItemAcquiredPattern`][osrlib.crawl.triggers.ItemAcquiredPattern] and [`HasItemCondition`][osrlib.crawl.gates.HasItemCondition] to match:

```{.python .no-run}
--8<-- "examples/tui_crawler/content.py:bundled-idol"
```

Place the item in a cache with `item_ids=("jade-idol",)`, match pickup with `ItemAcquiredPattern`, and guard the return objective with `TownEnteredPattern` plus `HasItemCondition(item_id="jade-idol")` so returning empty-handed does not complete the quest. Setting `cost_gp=0` has no shop side effects because the town shop stocks only built-in SRD equipment and rejects bundled IDs with `items.purchase.not_stocked` (see [bundling custom items](authoring-custom-content.md#bundling-custom-items-with-an-adventure)).

### Activation and hidden objectives

Set `activation` to a `TriggerClause` to start the quest when an event matches (recording `narrative.offer` on the activation event and in `session.journal`), or omit `activation` to make the quest active as soon as `GameSession.new` creates the session (in which case `PlayerView.quests` displays `offer` immediately, with no activation event or initial journal entry).

Each quest defines at least one [`ObjectiveSpec`][osrlib.crawl.quests.ObjectiveSpec] in `objectives`. Set `hidden=True` to omit an objective from `PlayerView.quests` until either its optional `reveal_when` clause matches or its `when` completion clause matches (completing a hidden objective reveals it automatically). Validation rejects `reveal_when` when `hidden=False`.

### The completion rule and the ending

Set `completion` to `"all"` (the default, requiring every objective) or `"any"` (completing the quest as soon as the first objective finishes).

Set `concludes_adventure=True` on the main quest whose completion should end the adventure. When that quest finishes, the session closes any open encounter or battle, transitions to the terminal `victory` mode (rejecting further play commands while still accepting referee commands), and emits [`AdventureCompletedEvent`][osrlib.crawl.events.AdventureCompletedEvent] (`session.adventure.completed`) with the quest's completion beat. Completing a quest with `concludes_adventure=True` is the only way a session enters `victory`.

### Rewards

`QuestSpec.rewards` accepts the same [`ConsequenceCommand`][osrlib.crawl.commands.ConsequenceCommand] types and character selectors (`"@party"`, `"@first"`, `"@carrier"`) as trigger consequences, executed in authored order *after* the quest completes with `source="quest:{id}"`. To remove a fetched quest item upon delivery, place `RemoveItem(character_id="@carrier", item_id=...)` first in `rewards`.

Keep reward ordering in mind for concluding quests:

- Because the session enters `victory` *before* `Interpreter` runs `rewards`, commands illegal in `victory` (`SpawnMonsters`, `SpawnNpcParty`, and `PlaceParty`) are rejected and logged via `RecordNote`, whereas item grants, coin grants, XP awards, and flags succeed.
- Under the default `on_return` XP rule, returning to town resolves treasure XP *before* quest rewards execute, so coins granted in `rewards` give spendable gold without awarding treasure XP. Grant quest XP explicitly with `AwardXP`.

### Which beat goes where

Quest and objective `NarrativeBlock` instances supply text for each player-visible lifecycle step:

| Moment | Block | Field |
| --- | --- | --- |
| Quest activates | `QuestSpec.narrative` | `offer` |
| Hidden objective reveals | `ObjectiveSpec.narrative` | `offer` |
| Objective completes | `ObjectiveSpec.narrative` | `progress` |
| Quest completes | `QuestSpec.narrative` | `completion` |

Each beat appears on the corresponding quest event and is appended to `session.journal` automatically. Quest and objective blocks do not use `NarrativeBlock.journal` (and osrlib ignores `QuestSpec.narrative.progress` and `ObjectiveSpec.narrative.completion`).

### Narrator guidance

Set `guidance` on any `NarrativeBlock`, or on [`LevelSpec.guidance`][osrlib.crawl.dungeon.LevelSpec] for level-wide atmosphere, to supply referee-only notes for an LLM or human narrator:

```{.python .no-run}
--8<-- "examples/tui_crawler/content.py:level-guidance"
```

osrlib never prints `guidance`, includes it in events, or exposes it on `PlayerView`. A narrating front end reads it directly from `session.adventure`.

## The complete program

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
        offer="Find the brass key lost in the barrow to get past the bronze sentinel.",
        completion="The brass key is in hand. The errand is closed.",
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
assert session.journal[0].text.startswith("Find the brass key")

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

- [The TUI crawler](../front-ends/tui-crawler.md) - the Jade Idol quest in a playable two-level barrow ending in `victory`.
- [Sessions, commands, and events](sessions-commands-events.md) - lifecycle commands and terminal session modes.
- [Determinism, saves, and replay](determinism-saves-replay.md) - how `fired_triggers`, `journal`, and `quests` persist across saves and replays.
- [Views and visibility](views-and-visibility.md) - how active quests and revealed objectives appear on `PlayerView`.
