# Views and visibility

B/X hides information from players by design. A fighter doesn't know the goblin has 4 hit points left, only that it's bleeding. The referee alone sees the monsters' morale roll and the reaction roll that decides whether they attack or flee. osrlib builds that asymmetry into the API instead of trusting the front end to enforce it.

Visibility shows up in two places: on individual events, and on the two whole-state projections a [`GameSession`][osrlib.crawl.session.GameSession] can build. Over a network that split becomes a boundary you enforce on the server, because a client must never see more than the player is meant to. [The complete example](#the-complete-example) is at the end of the page, and every snippet along the way comes from it.

## Visibility on events

Every [`Event`][osrlib.core.events.Event] has a [`Visibility`][osrlib.core.events.Visibility]: `PLAYER` or `REFEREE`. Most events default to `PLAYER`, like a party move, a door opening, or damage dealt. A specific set default to `REFEREE` because B/X keeps them behind the screen: morale checks, reaction rolls, wandering-monster checks, detection rolls, and the event that reports a creature's current and maximum hit points ([`HitPointsReportedEvent`][osrlib.core.events.HitPointsReportedEvent]). If your front end streams or narrates the raw event log as it happens, like an LLM referee doing turn-by-turn narration, check `.visibility` yourself before you show an event to a player, the same way you'd filter a database query.

Authored content splits the same way. A journal entry is written for the table, so [`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent] is player-visible and includes the authored text as content data in a structured field, alongside the event's message code. The text isn't English the engine wrote. The setup that produced the entry is referee-only. A fired trigger ([`TriggerFiredEvent`][osrlib.crawl.events.TriggerFiredEvent]) and a referee note ([`NoteRecordedEvent`][osrlib.crawl.events.NoteRecordedEvent]) are referee-only, like a flag write, because these connections are the game's secret. Player-visible events and the player view are two of the three routes authored words take to a player. The third is a gate's `refusal` text, which the engine returns in an ordinary rejection. Render it like any other refusal (see [Gates, triggers, and quests](gates-triggers-quests.md)).

Most front ends don't filter by hand, because osrlib also provides two projections of the *whole session*, one per audience, each already filtered for that audience.

## The two views

Pass a `Visibility` to [`GameSession.view`][osrlib.crawl.session.GameSession.view] and you get the matching projection:

```{.python .no-run}
player_view = session.view(Visibility.PLAYER)
referee_view = session.view(Visibility.REFEREE)
```

[`PlayerView`][osrlib.crawl.views.PlayerView] is an explicit list of fields, built straight from session state and never from the event log, so it can't leak a referee-visibility event that happened to mention a hidden number.

You get the adventure's and town's public names and descriptions, the town's list of services, the party's location and facing, the elapsed clock, and the session mode. Each party member has a public sheet ([`MemberView`][osrlib.crawl.views.MemberView]) with an id, name, class, level, current and max hit points, conditions, inventory, and memorized spells, because a player always sees their characters in full. Unidentified magic items are masked to a category-level description rather than their true name (see [`MagicItemCategory`][osrlib.core.items.MagicItemCategory]), so even in a character's inventory, the view shows an item's name only after the party identifies it. An unidentified enchanted arm is the one exception to "name only": when its display string is built from the base weapon ("a sword with a faint aura"), the view also includes that weapon's `qualities` and `missile_ranges`, which are enough for a front end to tell a melee declaration from a missile one. The bonus, any curse, and the template id stay hidden until identification. A weapon-backed item whose display string comes from its category instead, like a staff of striking that still displays as "a staff", includes neither field. The display string doesn't mention the weapon, and the fields would set that item apart from the others in its category.

For the map, you get the mapped cells with their edges ([`ExploredLevelView`][osrlib.crawl.views.ExploredLevelView] and [`EdgeView`][osrlib.crawl.views.EdgeView]): every cell the party has walked, every cell the party's light has shown, and whatever that light reveals from where the party stands right now. The cells the light has shown persist as map memory in [`DungeonState.seen`][osrlib.crawl.dungeon.DungeonState.seen], so the automap you draw can still show a torchlit room after the party walks on. The view shows an undiscovered secret door as a plain wall. Known dropped piles and emptied treasure caches in that explored space are in the view too.

Active effects on party members come with their remaining duration, except a potion's: by the rules as written, the referee tracks a potion's duration secretly, so the view reports it as unknown. The view also reports fatigue, exhaustion, and deprivation status, and the session journal as written ([`JournalEntry`][osrlib.crawl.session.JournalEntry]): the entries in order of discovery, each with the clock position when it was added. The view doesn't include the records of which triggers fired to add those entries.

The quests in play appear as [`QuestView`][osrlib.crawl.views.QuestView] values with an id, a name, the offer text and its speaker, and the revealed objectives with their ids, display names, and states. When an encounter or battle is running, the view also contains its public state ([`EncounterView`][osrlib.crawl.views.EncounterView] and [`EncounterGroupView`][osrlib.crawl.views.EncounterGroupView]): a monster group's id, label, living count, distance, and visible conditions, but never its hit points. The view also reports what the table knows about the current round: who must declare, who stands in the front rank, who is held fast, and who is still reloading.

[`RefereeView`][osrlib.crawl.views.RefereeView] is the opposite: everything except the RNG stream states and the master seed. It has one field for each group that [`session_state`][osrlib.persistence.session_state] writes for a save, and each field uses the session's model, so `view.monsters` contains full [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] values with real hit points, and `view.flags`, `view.npcs`, `view.exploration`, and `view.event_log` are the flag store, the NPC roster, the session counters, and the complete event log with referee-visibility events in it. Dumping the view to JSON gives that save payload back without the two withheld keys. Use it for LLM referees and tools that need the truth rather than a player's approximation of it. Never send it to a client over the network.

## The split in practice

A spawned monster shows the split. The referee view contains the monster's live hit points. The player-facing encounter group contains only what the party could plausibly perceive: how many are still standing, how far away they are, and what conditions show.

```{.python .no-run}
# The referee sees the goblin's hit points; the player view never contains them.
referee_monster = referee_view.monsters[0]
assert referee_monster.current_hp > 0

player_group = player_view.encounter.groups[0]
assert player_group.count == 1
assert "current_hp" not in player_group.model_dump()
```

Authored content shows the same split from the other side. The player view contains the whole journal and none of the triggers behind its entries.

```{.python .no-run}
# The beat is for the table; the trigger that produced it is referee-only setup.
assert [entry.text for entry in journal_view.journal][-1] == "The lever grinds."
assert "lever-east" not in journal_view.model_dump_json()
assert referee_after.fired_triggers == ("lever-east",)
```

Quests split the same way, in more detail. `PlayerView.quests` contains the **active** quests only, in document order. A quest the party hasn't been given yet is absent, because an activation clause is a connection like any other, and a finished quest is no longer in `PlayerView.quests`, because its record is the journal. Under each quest, only the **revealed** objectives appear. A hidden objective's id is not in the projection at all until its `reveal_when` clause fires or the objective completes, which is why `ObjectiveView.state` needs only `"incomplete"` and `"complete"`. The player view contains nothing else about a quest: no clause, no pattern, no condition, no reward, and no `guidance` from any narrative block or level.

```{.python .no-run}
# Active quests only, revealed objectives only, and none of the setup behind them.
quest_view = player_view.quests[0]
assert (quest_view.id, quest_view.speaker) == ("the-lamps", "Sister Halda")
assert [entry.id for entry in quest_view.objectives] == ["find-the-lever"]
assert quest_view.objectives[0].name == "Find the lever"  # the authored name, or the id when unauthored
assert "name-the-dead" not in player_view.model_dump_json()
```

## How a client knows the journal grew

[`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent] is not the only event the engine emits when the journal grows. A quest's journal entry *is* the line that the quest's lifecycle event already reports, so the engine emits no journal event after it. Emitting both would show the table one line twice. If you render the journal incrementally, watch these codes rather than one: `session.journal.entry_added`, `session.quest.activated`, `session.quest.objective_revealed`, `session.quest.objective_completed`, and `session.quest.completed`. If you'd rather not track any of them, read `PlayerView.journal`, which is always the whole record.

## Never trust the client

The moment a game goes over a network, this split becomes a security boundary and not just a courtesy. Keep the session, with its full referee-visible state, on the server. A client never runs `execute` itself and never receives the referee view. Each request sends a command, the server calls `session.execute(command)`, and the response sends back only `session.view(Visibility.PLAYER)`, or a rendering of the accepted result's events filtered the same way. A client that could see the referee view, or run commands against a local copy of the session, could read monster hit points straight off the wire or replay commands the server never accepted. B/X reserves that information and control for the person running the table. [The FastAPI pattern](../front-ends/fastapi-pattern.md) walks through this boundary end to end. You keep one session per game on the server and pass every response through the player view before it leaves the process.

## The complete example

```python
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.events import Visibility
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import (
    AddJournalEntry,
    EnterDungeon,
    MarkTriggerFired,
    RecordNote,
    SessionMode,
    SetFlag,
    SpawnMonsters,
)
from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
from osrlib.crawl.interpreter import Interpreter
from osrlib.crawl.narrative import NarrativeBlock
from osrlib.crawl.party import Party
from osrlib.crawl.quests import ObjectiveSpec, QuestSpec, TriggerClause
from osrlib.crawl.session import GameSession
from osrlib.crawl.triggers import DungeonEnteredPattern, FlagSetPattern

rules = Ruleset()
creation = RngStreams(master_seed=13).get(CHARACTER_CREATION_STREAM)
hero = create_character(
    name="Rurik",
    class_id="fighter",
    alignment=Alignment.LAWFUL,
    ruleset=rules,
    stream=creation,
)
party = Party(members=[hero.character])

level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
town = TownSpec(name="Threshold", travel_turns={"crypt": 1})

# One errand, offered at the threshold: one objective the party is told about, and
# one it is not.
errand = QuestSpec(
    id="the-lamps",
    name="The Unlit Lamps",
    activation=TriggerClause(pattern=DungeonEnteredPattern(dungeon_id="crypt")),
    objectives=(
        ObjectiveSpec(
            id="find-the-lever",
            when=TriggerClause(pattern=FlagSetPattern(key="crypt.lever")),
            narrative=NarrativeBlock(progress="The lamps come up one by one."),
        ),
        ObjectiveSpec(id="name-the-dead", when=TriggerClause(pattern=FlagSetPattern(key="crypt.name")), hidden=True),
    ),
    narrative=NarrativeBlock(
        offer="Light the crypt's lamps before the moon sets.",
        speaker="Sister Halda",
    ),
)
adventure = Adventure(name="A First Delve", town=town, dungeons=(crypt,), quests=(errand,))
session = GameSession.new(party, adventure, seed=13)
session.register_listener(Interpreter(session))

session.execute(EnterDungeon(dungeon_id="crypt"))

# The referee creates a lone goblin and opens an encounter at 30 feet.
result = session.execute(SpawnMonsters(template_id="goblin", count_fixed=1, distance_feet=30))
assert result.accepted
assert session.mode is SessionMode.ENCOUNTER

player_view = session.view(Visibility.PLAYER)
referee_view = session.view(Visibility.REFEREE)

# The referee sees the goblin's hit points; the player view never contains them.
referee_monster = referee_view.monsters[0]
assert referee_monster.current_hp > 0

player_group = player_view.encounter.groups[0]
assert player_group.count == 1
assert "current_hp" not in player_group.model_dump()

# A trigger fires: it is marked, it writes a journal beat, and the referee annotates it.
session.execute(MarkTriggerFired(trigger_id="lever-east"))
session.execute(AddJournalEntry(text="The lever grinds.", source="trigger:lever-east"))
session.execute(RecordNote(text="The east lever is the only one that answers."))

journal_view = session.view(Visibility.PLAYER)
referee_after = session.view(Visibility.REFEREE)

# The beat is for the table; the trigger that produced it is referee-only setup.
assert [entry.text for entry in journal_view.journal][-1] == "The lever grinds."
assert "lever-east" not in journal_view.model_dump_json()
assert referee_after.fired_triggers == ("lever-east",)

# The quest activated at the threshold, and its offer opened the journal.
quest_view = journal_view.quests[0]
assert (quest_view.id, quest_view.name) == ("the-lamps", "The Unlit Lamps")
assert quest_view.narrative == "Light the crypt's lamps before the moon sets."
assert quest_view.speaker == "Sister Halda"
assert journal_view.journal[0].text == quest_view.narrative

# Only the revealed objective is projected, and none of the setup behind it.
assert [(entry.id, entry.state) for entry in quest_view.objectives] == [("find-the-lever", "incomplete")]
blob = journal_view.model_dump_json()
assert "name-the-dead" not in blob  # a hidden objective has no view at all
assert "pattern_type" not in blob and "crypt.lever" not in blob

# The flag that objective watches: the quest completes it, journals its beat, and
# reports the beat through its own event — no journal event follows.
lit = session.execute(SetFlag(key="crypt.lever", value=True))
codes = [event.code for event in lit.events]
assert "session.quest.objective_completed" in codes
assert "session.journal.entry_added" not in codes
assert session.journal[-1].text == "The lamps come up one by one."

after = session.view(Visibility.PLAYER)
assert [(entry.id, entry.state) for entry in after.quests[0].objectives] == [("find-the-lever", "complete")]
assert session.quests["the-lamps"].status == "active"  # the hidden objective is still open
```

## Where next

- [Sessions, commands, and events](sessions-commands-events.md) - the command loop that produces the state these views project.
- [Listeners and flags](listeners-and-flags.md) - the flag store and listener state the player view leaves out, and where each one lives.
- [Gates, triggers, and quests](gates-triggers-quests.md) - the authored layer behind the journal, the quest projections, and the refusal beat.
- [The FastAPI pattern](../front-ends/fastapi-pattern.md) - the player view as the wire contract, end to end.
- [LLM referees](../front-ends/llm-referees.md) - a narrator built on the referee view and the raw event log.
