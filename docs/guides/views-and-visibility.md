# Views and visibility

B/X hides information from players by design. A fighter doesn't know the goblin has 4
hit points left, only that it's bleeding; the referee alone sees the monsters' morale
roll and the reaction roll that decided whether they attack or flee. That asymmetry is
the whole point of having a referee, and osrlib encodes it directly instead of leaving
it to a front end's discipline. This page covers the two places visibility shows up —
on individual events, and on the two whole-state projections a
[`GameSession`][osrlib.crawl.session.GameSession] can build — and why a networked front
end must never let the client see more than the player is meant to. The complete
example appears [at the end of the page](#the-complete-example); the fragments along
the way are excerpts of it.

## Visibility on events

Every [`Event`][osrlib.core.events.Event] carries a
[`Visibility`][osrlib.core.events.Visibility]: `PLAYER` or `REFEREE`. Most events
default to `PLAYER` — a party moved, a door opened, damage was dealt. A specific set
default to `REFEREE` because B/X keeps them behind the screen: morale checks, reaction
rolls, wandering-monster checks, detection rolls, and the event that carries a
creature's actual hit-point numbers
([`HitPointsReportedEvent`][osrlib.core.events.HitPointsReportedEvent]). A front end
that streams or narrates the raw event log as it happens — an LLM referee doing
turn-by-turn narration, say — is responsible for checking `.visibility` itself before
showing an event to a player, the same way it would filter a database query.

The authored layer splits the same way. A journal beat is written for the table, so
[`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent] is
player-visible and carries the authored text itself — content data in a structured
field, alongside the event's message code, never engine-baked English. The wiring that
produced the beat is not: a fired trigger
([`TriggerFiredEvent`][osrlib.crawl.events.TriggerFiredEvent]) and a referee note
([`NoteRecordedEvent`][osrlib.crawl.events.NoteRecordedEvent]) are referee-visibility,
exactly as a flag write is, because content wiring is the game's secret. Player-visible
events and the player view are two of the three channels authored words reach a player
by; the third is a gate's `refusal` beat riding an ordinary rejection, which a front
end should render like any other refusal (see
[Gates, triggers, and quests](gates-triggers-quests.md)).

Most front ends never need to do that filtering by hand, though, because osrlib also
ships two ready-made projections of the *whole session*, one per audience, and either
one already applies this filtering for its consumer.

## The two views

[`GameSession.view`][osrlib.crawl.session.GameSession.view] takes a `Visibility` and
returns the matching projection:

```{.python .no-run}
player_view = session.view(Visibility.PLAYER)
referee_view = session.view(Visibility.REFEREE)
```

[`PlayerView`][osrlib.crawl.views.PlayerView] is an enumerated whitelist, built
straight from session state — never from the event log, so it can't accidentally leak
a referee-visibility event that happened to mention a hidden number. It carries: the
adventure's and town's public names and descriptions; each party member's own public
sheet ([`MemberView`][osrlib.crawl.views.MemberView] — id, name, class, level, current
and max hit points, conditions, inventory, memorized spells — a player always sees
their own characters in full); the party's location and facing; the elapsed clock; the
session mode; the mapped cells with their edges — every cell the party has walked,
every cell its own light has shown it (persisted as map memory in
[`DungeonState.seen`][osrlib.crawl.dungeon.DungeonState.seen], so a front end's
automap remembers a torchlit room after the party walks on), and whatever its light
reveals from where it stands right now, while an undiscovered secret door renders as
a plain wall throughout ([`ExploredLevelView`][osrlib.crawl.views.ExploredLevelView]
and [`EdgeView`][osrlib.crawl.views.EdgeView]); known dropped piles and emptied
treasure caches in that explored space; active effects on party members with their
remaining duration (except a potion's — RAW has the referee track that secretly, so
the view reports it as unknown); fatigue, exhaustion, and deprivation status; the
session journal as written ([`JournalEntry`][osrlib.crawl.session.JournalEntry] — the
beats in order of discovery, each carrying the clock position it landed at, while the
trigger fired-marks behind them stay out of the view entirely); the quests in play
([`QuestView`][osrlib.crawl.views.QuestView] — id, name, the offer beat and its speaker
attribution, and the revealed objectives with their ids, display names, and states); and, when
one is running, the current encounter or battle's public shape
([`EncounterView`][osrlib.crawl.views.EncounterView] and
[`EncounterGroupView`][osrlib.crawl.views.EncounterGroupView] — a monster group's id,
label, living count, distance, and visible conditions, but never its hit points).
Unidentified magic items are masked to a category-level description rather than their
true name — see [`MagicItemCategory`][osrlib.core.items.MagicItemCategory] — so even a
character's own inventory doesn't leak what a `detect magic` hasn't earned them yet.

[`RefereeView`][osrlib.crawl.views.RefereeView] is the opposite instinct: everything,
minus the RNG stream states and the master seed. Its single `state` field is the same
serialized shape [`session_state`][osrlib.persistence.session_state] produces for a
save — full monster instances with real hit points, the flag store, the NPC roster,
session counters, and the complete event log, referee-visibility events included. It
exists for LLM referees and tools that need the truth, not a player's approximation of
it; a wire client should never receive it.

## The stable difference

The clearest way to see the split is a spawned monster. The referee view's state
carries the monster's live hit points; the player-facing encounter group carries only
what the party could plausibly perceive — how many are still standing, how far away,
what conditions show:

```{.python .no-run}
# The referee sees the goblin's hit points; the player view never carries them.
referee_monster = referee_view.state["monsters"][0]
assert "current_hp" in referee_monster

player_group = player_view.encounter.groups[0]
assert player_group.count == 1
assert "current_hp" not in player_group.model_dump()
```

The authored layer shows the same shape from the other side: the journal reaches the
player view whole, while the trigger that wrote it does not reach it at all.

```{.python .no-run}
# The beat is for the table; the trigger that produced it is referee-only wiring.
assert [entry.text for entry in journal_view.journal][-1] == "The lever grinds."
assert "lever-east" not in journal_view.model_dump_json()
assert referee_state["fired_triggers"] == ["lever-east"]
```

Quests draw the same line, one level finer. `PlayerView.quests` carries the **active**
quests only, in document order: a quest nobody has been given yet is absent, because an
activation clause is wiring like any other, and a finished one leaves the list, because
its record is the journal. Under each, only the **revealed** objectives appear — a hidden
objective's id is not in the projection at all until something surfaces it, which is why
`ObjectiveView.state` needs only `"incomplete"` and `"complete"`. Nothing else about a
quest crosses: no clause, no pattern, no condition, no reward, and no `guidance` from any
narrative block or level.

```{.python .no-run}
# Active quests only, revealed objectives only, and none of the wiring behind them.
quest_view = player_view.quests[0]
assert (quest_view.id, quest_view.speaker) == ("the-lamps", "Sister Halda")
assert [entry.id for entry in quest_view.objectives] == ["find-the-lever"]
assert quest_view.objectives[0].name == "Find the lever"  # the authored name, or the id when unauthored
assert "name-the-dead" not in player_view.model_dump_json()
```

## What tells a client the journal grew

[`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent] is not the only
event a growing journal emits. A quest beat's entry *is* the line the quest displayed, so
it reports itself through its own lifecycle event and no journal event follows — emitting
both would show the table one line twice. A client that renders incrementally therefore
watches five codes rather than one: `session.journal.entry_added`,
`session.quest.activated`, `session.quest.objective_revealed`,
`session.quest.objective_completed`, and `session.quest.completed`. A client that would
rather not track any of them reads `PlayerView.journal`, which is always the whole record.

## Never trust the client

The moment a game goes over a network, this split becomes a security boundary, not
just a courtesy. The session — with its full referee-visible state — stays on the
server; a client never runs `execute` itself and never receives the referee view. Each
request sends a command, the server calls `session.execute(command)`, and the response
carries only `session.view(Visibility.PLAYER)` (or a rendering of the accepted
result's events, filtered the same way) back over the wire. A client that could see
the referee view, or execute commands against a local copy of the session, could read
monster hit points directly off the wire or replay commands the real game state never
sanctioned — exactly the kind of information and control B/X reserves for the person
running the table. [The FastAPI pattern](../front-ends/fastapi-pattern.md) walks
through this boundary end to end: one session per game, held server-side, with every
response passed through the player view before it leaves the process.

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

# The referee spawns a lone goblin and opens an encounter at 30 feet.
result = session.execute(SpawnMonsters(template_id="goblin", count_fixed=1, distance_feet=30))
assert result.accepted
assert session.mode is SessionMode.ENCOUNTER

player_view = session.view(Visibility.PLAYER)
referee_view = session.view(Visibility.REFEREE)

# The referee sees the goblin's hit points; the player view never carries them.
referee_monster = referee_view.state["monsters"][0]
assert "current_hp" in referee_monster

player_group = player_view.encounter.groups[0]
assert player_group.count == 1
assert "current_hp" not in player_group.model_dump()

# A trigger fires: it is marked, it writes a journal beat, and the referee annotates it.
session.execute(MarkTriggerFired(trigger_id="lever-east"))
session.execute(AddJournalEntry(text="The lever grinds.", source="trigger:lever-east"))
session.execute(RecordNote(text="The east lever is the only one that answers."))

journal_view = session.view(Visibility.PLAYER)
referee_state = session.view(Visibility.REFEREE).state

# The beat is for the table; the trigger that produced it is referee-only wiring.
assert [entry.text for entry in journal_view.journal][-1] == "The lever grinds."
assert "lever-east" not in journal_view.model_dump_json()
assert referee_state["fired_triggers"] == ["lever-east"]

# The quest activated at the threshold, and its offer opened the journal.
quest_view = journal_view.quests[0]
assert (quest_view.id, quest_view.name) == ("the-lamps", "The Unlit Lamps")
assert quest_view.narrative == "Light the crypt's lamps before the moon sets."
assert quest_view.speaker == "Sister Halda"
assert journal_view.journal[0].text == quest_view.narrative

# Only the revealed objective is projected, and none of the wiring behind it.
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

- [Sessions, commands, and events](sessions-commands-events.md) — the command loop
  that produces the state these views project.
- [Listeners and flags](listeners-and-flags.md) — the flag store and listener state
  this page keeps out of the player view, and where each one lives.
- [Gates, triggers, and quests](gates-triggers-quests.md) — the authored layer behind
  the journal, the quest projections, and the refusal beat.
- [The FastAPI pattern](../front-ends/fastapi-pattern.md) — the player view as the
  wire contract, end to end.
- [LLM referees](../front-ends/llm-referees.md) — a narrator built on the referee
  view and the raw event log.
