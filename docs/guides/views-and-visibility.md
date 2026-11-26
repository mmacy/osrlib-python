# Views and visibility

B/X hides monster hit points, morale and reaction rolls, undiscovered secret doors, and quest triggers from players. osrlib separates player-visible data from referee data in two places: whole-session snapshots returned by [`GameSession.view`][osrlib.crawl.session.GameSession.view] and the [`Visibility`][osrlib.core.events.Visibility] field on individual events.

## Build player and referee views

Pass `Visibility.PLAYER` or `Visibility.REFEREE` to [`GameSession.view`][osrlib.crawl.session.GameSession.view] after any state change to build a frozen snapshot for that audience:

```{.python .no-run}
session.execute(SpawnMonsters(template_id="goblin", count_fixed=1, distance_feet=30))

player_view = session.view(Visibility.PLAYER)
referee_view = session.view(Visibility.REFEREE)
```

[`PlayerView`][osrlib.crawl.views.PlayerView] builds its fields directly from current session state rather than from `session.event_log`, so referee-only events cannot leak hidden numbers into the snapshot. [`RefereeView`][osrlib.crawl.views.RefereeView] exposes the state that [`session_state`][osrlib.persistence.session_state] serializes into a save (including [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] objects with current hit points, `flags`, `npcs`, `exploration` counters, and the unfiltered `event_log`), omitting the master seed, RNG stream states, and player-handle key. In place of the key, its `feature_handles` field maps each feature's save reference to the player handle the party sees, so a referee tool can show what the players see and send the same `feature_id` they do. Use `RefereeView` for LLM referees, test assertions, or debugging tools, and never send it to a player client:

```{.python .no-run}
# The referee sees the goblin's hit points; the player view never contains them.
referee_monster = referee_view.monsters[0]
assert referee_monster.current_hp > 0

player_group = player_view.encounter.groups[0]
assert (player_group.label, player_group.family_id, player_group.categories) == ("goblin", "goblin", ("person",))
assert player_group.count == 1
assert "current_hp" not in player_group.model_dump()
```

## Read character sheets and masked magic items

`PlayerView` includes the adventure and town names and descriptions, available town services and temple healing prices, the party's position and facing, the elapsed clock, fatigue and deprivation status, and the current `SessionMode`.

Each party member appears in `PlayerView.party` as a [`MemberView`][osrlib.crawl.views.MemberView] containing every stat needed to render a B/X character sheet: ID, name, race, class id and class name (`class_name`, which is right for a custom class too), level, title, alignment, XP and next-level XP threshold, current and maximum hit points, conditions, ability scores and modifiers, THAC0 and attack bonus, descending and ascending AC, saving throws, movement rate, languages, inventory, spell book, memorized spells, and thief skill chances.

A member's spell book and memorized spells name each spell by id, and so does the `kind` of a spell's effect in `PlayerView.effects`. Show the names in [`PlayerView.spell_names`][osrlib.crawl.views.PlayerView.spell_names], keyed by those ids, and the names of reversed forms, like *darkness* for *light*, in [`PlayerView.reversed_spell_names`][osrlib.crawl.views.PlayerView.reversed_spell_names]. The view reads both from the session's catalog, so a spell the adventure bundles has its name there too. Neither includes a spell that is absent from the rest of the view, like one on a scroll.

Derived combat and exploration stats include bonuses from worn equipment (such as armour, a shield, or a ring of protection), even before the party identifies a magic item:

- **Armour class**: `armour_class` and `armour_class_ascending` reflect worn magic armour or shields immediately, because a magic item works when it's worn, while `inventory` still displays a masked description such as `"a shield with a faint aura"`. A cursed shield or suit of armour is the exception. The OSE SRD says every test shows it as enchanted with a +1 bonus until it's used in deadly combat, so until the session reveals the curse, the player view counts it as the +1 item it tests as. An unarmoured fighter with an unidentified cursed shield -2 reads AC 7, as with a shield +1, while the monsters roll against the real AC 10. The real number appears when the curse is revealed, the first time the member is attacked in a battle or when a referee sends `IdentifyItem`. [`Character.armour_class`][osrlib.core.character.Character.armour_class] and the referee view always have the real number.
- **Strength overrides**: Items that set Strength (such as gauntlets of ogre power setting STR 18, or a cursed ring of weakness setting STR 3) update [`effective_strength`][osrlib.crawl.views.MemberView.effective_strength], `melee_modifier`, and `open_doors_chance`, while `scores["str"]` preserves the rolled score. Display both `scores["str"]` and `effective_strength` if you want players to see the item's effect.
- **Timed effects**: Temporary spell and potion modifiers appear in [`PlayerView.effects`][osrlib.crawl.views.PlayerView.effects] rather than in `MemberView` stat totals. Each entry includes its remaining duration, except for potions, whose duration a B/X referee tracks secretly, so `PlayerView` reports it as unknown.

In `MemberView.inventory`, unidentified magic items use a category description from [`MagicItemCategory`][osrlib.core.items.MagicItemCategory] instead of their true name, bonus, curse state, or template ID. When an unidentified item's display string names a weapon, such as an enchanted blade (`"a sword with a faint aura"`) or a magic staff (`"a staff"`), its inventory dict also includes the base weapon's `qualities` and (when applicable) `missile_ranges` so your UI can distinguish melee from missile attacks without revealing the enchantment. Non-weapon categories, such as `"a wand"`, omit both keys.

## Render maps, encounters, and battle positions

`PlayerView` exposes explored dungeon geometry through [`ExploredLevelView`][osrlib.crawl.views.ExploredLevelView] and [`EdgeView`][osrlib.crawl.views.EdgeView]: every cell the party has visited, every cell previously illuminated by the party's light (stored in [`DungeonState.seen`][osrlib.crawl.dungeon.DungeonState.seen] for automapping), and cells currently lit from the party's position. Undiscovered secret doors appear as plain walls.

Each level also reports the authored areas the party entered, with only walked area cells. An entered area's `trap_state` is `"found"` or `"sprung"` once the party has found or sprung the room's trap, and `None` before, so a room whose trap the party hasn't met looks like a room without one. When areas overlap, the first area in the level's list owns the shared cell and only that owner can enter through it. A known custom feature or treasure cache appears as a [`MapFeatureView`][osrlib.crawl.views.MapFeatureView] on its visible cell. An area-bound feature appears after the party enters that area, and a construction trick appears only after search marks it in `found_tricks`. An explicit feature on a shared cell belongs to the owning area only. Use the feature's opaque `id` in `feature_id`; the feature list never exposes the authored or generated raw id. The fields list the safe kind, description, location, area id after entry, trap state (`"found"`, `"sprung"`, or `"removed"`), and `emptied`, which is true for an authored treasure cache the party has emptied. They never include cache contents, trap or trigger mechanics, destinations, gates, or an unannounced secret. An emptied cache stays in the list under the same `id`, so you can draw it opened, but `TakeTreasure` refuses it with `exploration.feature.emptied`.

Generated area caches appear after their cells become known. The session removes a generated cache when the party takes it, so the cache leaves the list. A generated cache's `area_id` names the area that owns its cell once the party has entered that area. A generated lair cache stays out of the view and refuses feature commands until the party resolves the area's keyed encounter. [`TransitionView`][osrlib.crawl.views.TransitionView] exposes the kind and safe position of each transition on a known cell, but not its destination or gate. `ExploredLevelView.entrance` appears only when the party knows the entrance cell, and `PlayerView.can_travel_to_town` is true only when the party can issue [`TravelToTown`][osrlib.crawl.commands.TravelToTown] from there. Known dropped piles stay in `PlayerView.piles`.

`PlayerView.features_here` lists the handles that [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure], [`InspectTreasure`][osrlib.crawl.commands.InspectTreasure], and [`RemoveTreasureTrap`][osrlib.crawl.commands.RemoveTreasureTrap] accept from where the party stands, in the order of the level's `features`. The session resolves a command's `feature_id` against the same caches, so offer those handles rather than working out reach yourself. The list is empty unless the session is exploring. To tell whether a listed cache is emptied, read its `MapFeatureView.emptied`.

During an active encounter or battle, `PlayerView.encounter` provides an [`EncounterView`][osrlib.crawl.views.EncounterView] with [`EncounterGroupView`][osrlib.crawl.views.EncounterGroupView] entries for each monster group:

- **Group identity and status**: Each group includes its ID, living `count`, distance, visible conditions with counts, whether it's running from the battle ([`fleeing`][osrlib.crawl.views.EncounterGroupView.fleeing]), and whether it has got away ([`fled`][osrlib.crawl.views.EncounterGroupView.fled]), without exposing hit points. A group that's `fleeing` and not `fled` is still a legal target. The session emits a [`MonsterEscapedEvent`][osrlib.crawl.events.MonsterEscapedEvent] for a group in the command result in which its `fled` becomes true, and in no other result, so you can show the group getting away from the event stream alone. Display [`label`][osrlib.crawl.views.EncounterGroupView.label] for one creature (such as `"goblin"`) and [`label_plural`][osrlib.crawl.views.EncounterGroupView.label_plural] for multiple creatures (`"goblins"`). Because custom encounters can set arbitrary display labels, branch on [`family_id`][osrlib.crawl.views.EncounterGroupView.family_id] (for example, `"hellhound"` across Hit Dice variants) and [`categories`][osrlib.crawl.views.EncounterGroupView.categories] (for example, `"undead"`) in code rather than matching display text.
- **Individual targets**: [`members`][osrlib.crawl.views.EncounterGroupView.members] lists every monster or NPC adventurer in the group's existing order, including dead and fled members. Each frozen [`EncounterMemberView`][osrlib.crawl.views.EncounterMemberView] contains only `id` and `status`. The status is `"defeated"` when dead, `"fled"` when alive in a group that has left, and `"alive"` otherwise. Individual conditions and private statistics don't appear. IDs match events and stay the same after save/load or a group split, so you can map an event's creature ID to its current group. Select an `"alive"` ID for a spell or scroll through [`BattleDeclaration.targets`][osrlib.crawl.commands.BattleDeclaration.targets] before receiving a combat event. Range, touch, visibility, and the spell mode's checks still apply. A running group stays targetable until it leaves. An unknown, out-of-encounter, dead, or fled enemy ID gets `magic.cast.unknown_target` without spending time or changing state. Refresh the view before choosing another target. Group-targeted area and Hit Dice budget spells still use `target_group_id`.
- **Round eligibility**: `EncounterView` lists which party members must submit declarations (`declarers`), which members cannot move (`immobile`), and which members are reloading (`reloading`). [`party_free_round`][osrlib.crawl.views.EncounterView.party_free_round] indicates whether any thief's back-stab is allowed on the current round.
- **Hiding**: [`can_hide`][osrlib.crawl.views.EncounterView.can_hide] lists the members who can declare `hide` this round, and [`hiding`][osrlib.crawl.views.EncounterView.hiding] lists the members who are hiding, each of whom can declare a back-stab in any round. Neither list says whether a hide worked, because the session rolls the thief's skills in secret. The players learn the outcome from the back-stab's [`AttackRolledEvent.back_stab`][osrlib.core.events.AttackRolledEvent.back_stab], and [`HideAttemptedEvent`][osrlib.crawl.events.HideAttemptedEvent] reports each attempt without its roll.
- **Melee reach and range track**: Check [`EncounterGroupView.front_rank`][osrlib.crawl.views.EncounterGroupView.front_rank] to see which party members can melee that group on the current round without moving (do not use `EncounterView.front_rank` for melee eligibility, because `EncounterView.front_rank` lists the party's leading row toward the nearest group regardless of distance). On each group, [`member_distance_feet`][osrlib.crawl.views.EncounterGroupView.member_distance_feet] gives every member's distance to that group, while [`EncounterView.advance_feet`][osrlib.crawl.views.EncounterView.advance_feet] gives each member's distance forward of the starting party line. Two members must be within 5 feet of each other on `advance_feet` to hand over items, administer a potion, or use a touch spell or staff of healing.
- **Movement, frontage, and sight**: A member can close in and make a melee attack in the same round when their distance to the group is at most [`MemberView.encounter_rate_feet`][osrlib.crawl.views.MemberView.encounter_rate_feet] plus 5 feet. [`formation_width`][osrlib.crawl.views.EncounterView.formation_width] caps how many combatants fit side by side in a front rank (limiting a monster group's melee attackers to the smaller of `count` and `formation_width`). Each group's [`lit`][osrlib.crawl.views.EncounterGroupView.lit] and [`seen_by`][osrlib.crawl.views.EncounterGroupView.seen_by] fields report whether the party's light reaches the group and which members can see it.

To validate battle choices interactively as a player builds a round's orders, pass the declarations chosen so far to [`preview_battle_round`][osrlib.crawl.battle.preview_battle_round]. It runs the same checks as [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound] without advancing combat, returning per-declaration rejections, projected member positions, and updated `front_rank` lists so your UI can disable invalid actions. Every rejection of a declaration, from the preview or from `ResolveBattleRound`, has the declaring member's id as its `character` param, so a client with several players can tell whose choice to change.

## Filter events, quests, and the journal

If your front end streams or narrates events from `CommandResult.events` or `session.event_log`, filter on `event.visibility == Visibility.PLAYER` before showing anything to players. Most events (such as movement, opening doors, and attack damage) use `Visibility.PLAYER`. Referee-only rolls (such as morale checks, reaction rolls, wandering-monster checks, secret detection rolls, and [`HitPointsReportedEvent`][osrlib.core.events.HitPointsReportedEvent]) use `Visibility.REFEREE`.

Authored triggers, notes, and quests follow the same visibility boundary:

- **Journal vs. triggers and notes**: [`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent] uses `Visibility.PLAYER` and appends to `PlayerView.journal` ([`JournalEntry`][osrlib.crawl.session.JournalEntry] items ordered by discovery with their clock timestamps). [`TriggerFiredEvent`][osrlib.crawl.events.TriggerFiredEvent], [`NoteRecordedEvent`][osrlib.crawl.events.NoteRecordedEvent], and flag updates use `Visibility.REFEREE` and appear only on `RefereeView`:

```{.python .no-run}
session.execute(MarkTriggerFired(trigger_id="lever-east"))
session.execute(AddJournalEntry(text="The lever grinds.", source="trigger:lever-east"))
session.execute(RecordNote(text="The east lever is the only one that answers."))

# Views are frozen snapshots, so call view() again to read the updated state.
journal_view = session.view(Visibility.PLAYER)
referee_after = session.view(Visibility.REFEREE)

assert [entry.text for entry in journal_view.journal][-1] == "The lever grinds."
assert "lever-east" not in journal_view.model_dump_json()
assert referee_after.fired_triggers == ("lever-east",)
```

- **Gate refusals**: When a gated door or transition blocks the party, `CommandResult.rejections` includes the authored `refusal` text in `params` (see [Gates, triggers, and quests](gates-triggers-quests.md)).
- **Quests**: `PlayerView.quests` includes [`QuestView`][osrlib.crawl.views.QuestView] entries only for **active** quests in document order, omitting inactive quests (whose activation conditions remain hidden) and completed quests (whose history remains in `PlayerView.journal`). Within an active quest, only **revealed** objectives appear, with `ObjectiveView.state` set to `"incomplete"` or `"complete"`. Hidden objectives, trigger clauses, rewards, and narrator `guidance` fields never appear in `PlayerView`:

```{.python .no-run}
# Active quests only, revealed objectives only, and none of the setup behind them.
quest_view = journal_view.quests[0]
assert (quest_view.id, quest_view.speaker) == ("the-lamps", "Sister Halda")
assert [entry.id for entry in quest_view.objectives] == ["find-the-lever"]
assert quest_view.objectives[0].name == "Find the lever"  # falls back to id when name is omitted
assert "name-the-dead" not in journal_view.model_dump_json()
```

When a quest activates, reveals an objective, completes an objective, or finishes, the session appends that beat to `session.journal` and reports it on the quest lifecycle event without emitting a separate [`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent]. If you update a journal panel incrementally from events, watch `session.journal.entry_added`, `session.quest.activated`, `session.quest.objective_revealed`, `session.quest.objective_completed`, and `session.quest.completed`. Otherwise, render `PlayerView.journal` directly after each command.

## Enforce visibility on the server

In a networked game, treat the player/referee split as an authority boundary. Keep `GameSession` and `RefereeView` on the server: accept a command per request, call `session.execute(command)` on the server, and return only `session.view(Visibility.PLAYER)` along with events filtered to `Visibility.PLAYER`. Give each new session an independent random `player_handle_key` of at least 32 bytes, and persist it in the server-side save so restored sessions resolve the same handles. Sending `RefereeView`, a save, or running `GameSession` on an untrusted client exposes referee state, the master seed, and the key. See [The FastAPI pattern](../front-ends/fastapi-pattern.md) for a reference HTTP server layout.

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
            name="Find the lever",
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
assert (player_group.label, player_group.family_id, player_group.categories) == ("goblin", "goblin", ("person",))
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
assert [(entry.id, entry.name, entry.state) for entry in quest_view.objectives] == [
    ("find-the-lever", "Find the lever", "incomplete")
]
blob = journal_view.model_dump_json()
assert "name-the-dead" not in blob  # a hidden objective has no view at all
assert "pattern_type" not in blob and "crypt.lever" not in blob

# The flag that objective watches: the quest completes it, journals its beat, and
# reports the beat through its own event, so no journal event follows.
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

- [Sessions, commands, and events](sessions-commands-events.md) - executing commands and reading `CommandResult`.
- [Listeners and flags](listeners-and-flags.md) - referee-only flags and listener state.
- [Gates, triggers, and quests](gates-triggers-quests.md) - authoring journal beats, quest objectives, and gate refusal messages.
- [The FastAPI pattern](../front-ends/fastapi-pattern.md) - serving `PlayerView` over HTTP while keeping `GameSession` on the server.
- [LLM referees](../front-ends/llm-referees.md) - driving narration from `RefereeView` and the full event log.
