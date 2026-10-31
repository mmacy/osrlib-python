"""`GameSession`, the running game, which you drive one command at a time.

Build a session from a [`Party`][osrlib.crawl.party.Party] and an
[`Adventure`][osrlib.crawl.adventure.Adventure] with
[`GameSession.new`][osrlib.crawl.session.GameSession.new], or restore one with
[`load_game`][osrlib.persistence.load_game]. From there the loop is the same every
time: build a command from [`osrlib.crawl.commands`][osrlib.crawl.commands], pass it
to [`GameSession.execute`][osrlib.crawl.session.GameSession.execute], and render the
[`CommandResult`][osrlib.crawl.commands.CommandResult] that comes back. A refused
command comes back with its reasons and changed nothing. An accepted one comes back
with the events it caused, which you turn into lines with
[`format_message`][osrlib.messages.format_message] or with a renderer of your own.
Draw each screen from [`GameSession.view`][osrlib.crawl.session.GameSession.view]
rather than from the session's attributes, because the player view leaves out what the
players mustn't see. Save the game with
[`save_game`][osrlib.persistence.save_game].

The rules functions in [`osrlib.core`][osrlib.core] act on objects you pass them, and they
store nothing between calls. The session contains those objects for a whole game: the seeded
random streams, the id allocator, the active effects, the clock, the characters and live
monsters, the session flags (a key-value store for facts you track), the record
of fired triggers, the journal, the quest states, the listeners and their state, the
command and event logs, the session mode, and what play has changed on the map.
Because the session contains all of it, one save restores a game, and replaying the
command log from the same seed reaches the same game.

The other members of this module are records the session stores, which you read from a
view rather than build: [`JournalEntry`][osrlib.crawl.session.JournalEntry],
[`QuestState`][osrlib.crawl.session.QuestState] and
[`ObjectiveState`][osrlib.crawl.session.ObjectiveState],
[`DeathRecord`][osrlib.crawl.session.DeathRecord],
[`DefeatedMonsterRecord`][osrlib.crawl.session.DefeatedMonsterRecord], and
[`DeprivationState`][osrlib.crawl.session.DeprivationState]. The `*_STREAM` constants
are the names of the random streams the crawl procedures draw from, and
[`LIGHT_EFFECT_KINDS`][osrlib.crawl.session.LIGHT_EFFECT_KINDS] and
[`DARKNESS_EFFECT_KINDS`][osrlib.crawl.session.DARKNESS_EFFECT_KINDS] are the effect
kinds the session counts as light and darkness.

Which commands the session will accept depends on its
[`SessionMode`][osrlib.crawl.commands.SessionMode]: `town` between delves,
`exploring` on a dungeon grid, `encounter` when the party has met monsters and nobody
has attacked yet, and `battle` once a fight starts. In `game_over` and `victory` the
session has ended. The session refuses a command that doesn't belong to the current mode
with `session.command.wrong_mode`, and each command class lists the modes it's legal in.

To add behavior without changing osrlib, register a
[`Listener`][osrlib.crawl.session.Listener]. A listener sees each accepted command's
events and reacts by executing ordinary commands, so the command log records everything it
does, and a replay repeats it like any other command. A listener can keep facts in the session flags by
executing [`SetFlag`][osrlib.crawl.commands.SetFlag].

Typical usage:

```python
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.events import Visibility
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import EnterDungeon, MoveParty
from osrlib.crawl.dungeon import Direction, DungeonSpec, Edge, EdgeKind, LevelSpec
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.messages import format_message
from osrlib.persistence import load_game, save_game

rules = Ruleset()
stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
hero = create_character(
    name="Hild",
    class_id="fighter",
    alignment=Alignment.LAWFUL,
    ruleset=rules,
    stream=stream,
).character
corridor = LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)})
crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(corridor,))
adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))

# The session makes its own streams from this seed, separate from the creation stream above.
session = GameSession.new(Party(members=[hero]), adventure, seed=7)
session.execute(EnterDungeon(dungeon_id="crypt"))

result = session.execute(MoveParty(direction=Direction.EAST))
print([format_message(event) for event in result.events if event.visibility is Visibility.PLAYER])
# ['The party moves to (1, 0), facing east.']

view = session.view(Visibility.PLAYER)
print(view.mode, view.location.position)
# exploring (1, 0)

restored = load_game(save_game(session))
print(restored.view(Visibility.PLAYER) == view)
# True
```
"""
# The play commands are handled in osrlib.crawl.exploration, osrlib.crawl.encounter,
# and osrlib.crawl.battle. Each handler is one function that takes (session, command),
# returns (rejections, events), and validates before it does anything: it makes no draw,
# changes nothing, and spends no time until the last rejection check has passed. The referee and quest
# command handlers are at the bottom of this module.

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from osrlib.core.alignment import Alignment
from osrlib.core.character import ADVANCEMENT_STREAM, Character
from osrlib.core.classes import XpAwardResult, apply_xp, level_title
from osrlib.core.clock import ROUNDS_PER_DAY, ROUNDS_PER_TURN, GameClock, TimeUnit
from osrlib.core.effects import EFFECTS_STREAM, Condition, EffectsLedger
from osrlib.core.events import (
    ConditionGainedEvent,
    DamageDealtEvent,
    DeathEvent,
    EffectExpiredEvent,
    EquipmentDestroyedEvent,
    Event,
    SavingThrowRolledEvent,
    Visibility,
)
from osrlib.core.items import EquipmentCatalog, ItemInstance, MagicItemInstance
from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, MonsterCatalog, MonsterInstance, spawn_monster
from osrlib.core.rng import RngStreams, StreamName
from osrlib.core.ruleset import Ruleset
from osrlib.core.treasure import TREASURE_STREAM, instantiate_magic_item
from osrlib.core.validation import Rejection
from osrlib.crawl.adventure import Adventure, _effective_equipment, _effective_monsters, validate_adventure
from osrlib.crawl.commands import (
    ActivateQuest,
    AddJournalEntry,
    AddMember,
    AdvanceTime,
    AwardXP,
    Command,
    CommandResult,
    CompleteObjective,
    CompleteQuest,
    EndEncounter,
    GrantCoins,
    GrantItem,
    IdentifyItem,
    MarkTriggerFired,
    PlaceParty,
    RecordNote,
    RemoveCoins,
    RemoveItem,
    RevealObjective,
    RollDice,
    SessionMode,
    SetDoorState,
    SetFlag,
    SpawnMonsters,
    SpawnNpcParty,
)
from osrlib.crawl.dungeon import DungeonState, edge_ref
from osrlib.crawl.events import (
    AdventureCompletedEvent,
    CharacterLeveledUpEvent,
    DiceRolledEvent,
    DoorEvent,
    FlagSetEvent,
    GameOverEvent,
    ItemAcquiredEvent,
    ItemsRemovedEvent,
    JournalEntryAddedEvent,
    LightEvent,
    LocationEnteredEvent,
    MemberAddedEvent,
    MonstersSpawnedEvent,
    NoteRecordedEvent,
    ObjectiveCompletedEvent,
    ObjectiveRevealedEvent,
    QuestActivatedEvent,
    QuestCompletedEvent,
    TimeAdvancedEvent,
    TriggerFiredEvent,
    XpAwardedEvent,
)
from osrlib.crawl.party import Party, _anyone_standing
from osrlib.crawl.quests import ObjectiveSpec, QuestSpec
from osrlib.data import load_equipment, load_magic_items, load_monsters
from osrlib.errors import ContentValidationError
from osrlib.versioning import SCHEMA_VERSION, engine_version

if TYPE_CHECKING:
    from osrlib.crawl.battle import BattleState
    from osrlib.crawl.encounter import EncounterState
    from osrlib.crawl.views import PlayerView, RefereeView

__all__ = [
    "ADJUDICATION_STREAM",
    "DARKNESS_EFFECT_KINDS",
    "DeathRecord",
    "DefeatedMonsterRecord",
    "DeprivationState",
    "ENCOUNTER_STREAM",
    "EXPLORATION_STREAM",
    "GameSession",
    "JournalEntry",
    "LIGHT_EFFECT_KINDS",
    "Listener",
    "MONSTER_ACTION_STREAM",
    "ObjectiveState",
    "QuestState",
    "WANDERING_STREAM",
]

WANDERING_STREAM = StreamName.WANDERING
"""The name of the stream the engine draws from for the wandering-monster check.

The engine draws from it for the check die, the encounter-table roll, the number of monsters, and
the choice between variants of a table entry. Every draw in osrlib comes from a named stream, so
adding a roll to one procedure can't shift the dice another procedure would have drawn.

You rarely need this name. It's the stream's key in a session's `streams` and in a save, so pass it
to [`RngStreams.get`][osrlib.core.rng.RngStreams.get] when you want to look the stream up, for
example to compare its state in a test. Don't draw from it during play. The engine's next wandering
rolls would change, and a replay of the command log would no longer reach the same game.
"""

ENCOUNTER_STREAM = StreamName.ENCOUNTER
"""The name of the stream the encounter procedure draws from.

The engine draws from it for surprise, encounter distance, reaction rolls, and the distraction
check during a chase. It also draws from it when it rolls the size of a group for
[`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters] and
[`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty]. See
[`WANDERING_STREAM`][osrlib.crawl.session.WANDERING_STREAM] for how stream names are used.
"""

EXPLORATION_STREAM = StreamName.EXPLORATION
"""The name of the stream the exploration procedures draw from.

The engine draws from it for forcing doors, listening, searching, springing traps, lighting a tinder
box, and thief skill checks. See [`WANDERING_STREAM`][osrlib.crawl.session.WANDERING_STREAM] for how
stream names are used.
"""

MONSTER_ACTION_STREAM = StreamName.MONSTER_ACTION
"""The name of the stream a monster action policy draws from.

An [`ActionPolicy`][osrlib.crawl.battle.ActionPolicy] chooses what a monster group does in each
battle round, and at whom. Its stream is separate from the combat stream, so changing how monsters
choose their actions, or registering a policy of your own, never shifts the combat dice.
"""

ADJUDICATION_STREAM = StreamName.ADJUDICATION
"""The name of the stream for the referee's dice rolls.

[`RollDice`][osrlib.crawl.commands.RollDice] draws from it. It's separate from the streams the rules
use, so a roll for weather or a rumour never shifts a later attack or save.
"""

LIGHT_EFFECT_KINDS = frozenset({"light", "continual_light"})
"""The effect kinds that give the party light: a lit torch or lantern, and the light spells.

[`GameSession.party_light`][osrlib.crawl.session.GameSession.party_light] checks each effect's kind
against this set. To make a light source of your own count as light, give its effect one of these
kinds.
"""

DARKNESS_EFFECT_KINDS = frozenset({"darkness", "continual_darkness"})
"""The effect kinds that put out the party's light while they last.

A darkness effect on any living member puts out all the party's light, because the spell's printed
radius covers a whole marching party. An effect whose params set `blocks_infravision`, like
*continual darkness*, stops infravision as well.
"""


class DeathRecord(BaseModel):
    """When and how one party member died, recorded for the spells that bring the dead back.

    The session writes one into
    [`GameSession.death_records`][osrlib.crawl.session.GameSession.death_records], keyed by
    character id, as soon as a party member dies. The engine reads it when a character casts a
    revival spell. *Neutralize poison* works only when poison was the cause, and only within a window
    measured in rounds. *Raise dead* counts the days since the death.

    The record is frozen. A member who dies again gets a new record, which replaces the old one.
    """

    model_config = ConfigDict(frozen=True)

    round: int
    """Where the clock stood at the death, in rounds since the session began. Both revival windows
    are measured from here."""
    cause: str
    """What killed the character: `"poison"`, `"damage"`, or `"unknown"`. The session reads the
    events of the command that killed the character, up to the death, and records the last cause
    among them. A failed save versus death ray or poison, or a poison effect that ran its course,
    gives `"poison"`. Damage dealt gives `"damage"`. With neither, the cause is `"unknown"`. Only
    *neutralize poison* treats `"poison"` differently from the other causes."""


class JournalEntry(BaseModel):
    """One entry in the party's journal: a line of the adventure's story, with the time it was written.

    [`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry] writes entries. So do the quest
    commands, like [`ActivateQuest`][osrlib.crawl.commands.ActivateQuest], which add the
    adventure's text for each step of a quest. Read the journal from
    [`PlayerView.journal`][osrlib.crawl.views.PlayerView.journal], a tuple of these in the order
    they were written.

    Entries are appended and never changed. Each one records the clock at the moment it's written,
    so you can show when it happened from the view alone, and a save made without its
    event log still has the time of every entry.
    """

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1)
    """The entry's text, from your code or from the adventure document. osrlib never writes it,
    and it's never empty."""
    rounds: int = Field(ge=0)
    """Where the clock stood when the entry was appended, in rounds since the session began."""


class ObjectiveState(BaseModel):
    """One objective's state in play: whether the party may see it, and whether it's done.

    The session creates one for each objective in the adventure and stores them in
    [`QuestState.objectives`][osrlib.crawl.session.QuestState.objectives]. A player view includes
    the revealed objectives of active quests, and the referee view includes them all.

    Both flags change only from false to true, because no quest command turns either one off.
    Completing an objective reveals it too, so the players see an objective they finished
    before anyone told them about it.
    """

    model_config = ConfigDict(validate_assignment=True)

    revealed: bool
    """Whether the party may be shown this objective. It starts true unless the objective's
    [`ObjectiveSpec.hidden`][osrlib.crawl.quests.ObjectiveSpec.hidden] is true, and
    [`RevealObjective`][osrlib.crawl.commands.RevealObjective] turns it on."""
    complete: bool
    """Whether the objective is done. [`CompleteObjective`][osrlib.crawl.commands.CompleteObjective]
    turns it on, and turns `revealed` on with it."""


class QuestState(BaseModel):
    """One quest's state in play: where it stands, and where each of its objectives stands.

    The session creates one for each quest in the adventure and stores them in
    [`GameSession.quests`][osrlib.crawl.session.GameSession.quests], keyed by quest id. Only the
    quest commands change them: [`ActivateQuest`][osrlib.crawl.commands.ActivateQuest],
    [`RevealObjective`][osrlib.crawl.commands.RevealObjective],
    [`CompleteObjective`][osrlib.crawl.commands.CompleteObjective], and
    [`CompleteQuest`][osrlib.crawl.commands.CompleteQuest]. So a replay of the command log
    rebuilds them exactly.

    A quest with no activation clause, meaning its
    [`QuestSpec.activation`][osrlib.crawl.quests.QuestSpec.activation] is `None`, starts `active`,
    because the party has it from the start. The rest start `inactive` and wait for
    `ActivateQuest`.
    """

    model_config = ConfigDict(validate_assignment=True)

    status: Literal["inactive", "active", "completed"]
    """Where the quest stands. It moves from `inactive` to `active` to `completed` and never
    goes back."""
    objectives: dict[str, ObjectiveState]
    """The state of each objective, keyed by objective id, in the order the adventure lists them
    in [`QuestSpec.objectives`][osrlib.crawl.quests.QuestSpec.objectives]."""


class DefeatedMonsterRecord(BaseModel):
    """One defeated creature, kept until the session awards its experience.

    When an encounter ends, the session appends one for each defeated creature to
    [`GameSession.defeated_monsters`][osrlib.crawl.session.GameSession.defeated_monsters].
    [`GameSession.award_adventure_xp`][osrlib.crawl.session.GameSession.award_adventure_xp]
    adds up their `xp` and clears the list when the party returns to town. Under a ruleset that
    awards experience immediately, the session awards it and clears the list at the end of each
    encounter instead.

    Its fields contain the same facts as
    [`MonsterDefeatedEvent`][osrlib.crawl.events.MonsterDefeatedEvent].
    """

    model_config = ConfigDict(frozen=True)

    monster_id: str
    """The session id of the creature that was defeated."""
    template_id: str
    """What it was: a monster catalog id, or `"npc:<class id>"` for an NPC adventurer."""
    outcome: str
    """How it went out: `"slain"` or `"routed"`. Both count as defeated for the award."""
    xp: int
    """What it's worth in experience points."""


class DeprivationState(BaseModel):
    """How long one member has gone without food and without water.

    The session stores these in
    [`GameSession.deprivation`][osrlib.crawl.session.GameSession.deprivation]. Each time the clock
    passes into a new game day, the session updates one for each living member, and creates it on that
    member's first day. A day the member eats resets `food_days` to zero, and a day without food
    adds one. Water works the same way for `water_days`. Whether the counts bring a
    penalty depends on the ruleset option `deprivation_penalties`, described in
    [the adaptations register](https://mmacy.github.io/osrlib-python/adaptations/),
    the page that lists where osrlib settles an ambiguous rule or supplies a default.
    """

    model_config = ConfigDict(validate_assignment=True)

    food_days: int = 0
    """Consecutive days this member has gone without food."""
    water_days: int = 0
    """Consecutive days this member has gone without water."""

    @property
    def worst(self) -> int:
        """Return the worse of the two counts, which is the count the penalties depend on.

        The two counts don't stack: going without both food and water is as bad as going without the
        worse of them, not twice as bad.

        Returns:
            The larger of `food_days` and `water_days`.
        """
        return max(self.food_days, self.water_days)


def _member_id(member: Character) -> str:
    """Return a member's session id, which every member in a session has."""
    if member.id is None:
        raise ValueError(f"{member.name} has no session-assigned id")
    return member.id


def _xp_award_events(member: Character, result: XpAwardResult) -> list[Event]:
    """Build the award event and, when the award crossed a threshold, the level event.

    Every `apply_xp` call site reports through here, so the level event always comes right after the
    same member's award event, and no call site has to repeat that ordering.
    """
    events: list[Event] = [
        XpAwardedEvent(
            character_id=_member_id(member),
            award=result.award,
            modified_award=result.modified_award,
            level_after=result.level_after,
        )
    ]
    if result.level_up is not None:
        events.append(
            CharacterLeveledUpEvent(
                character_id=_member_id(member),
                level_before=result.level_before,
                level_after=result.level_after,
                hp_gained=result.level_up.hp_gained,
                hp_roll=result.level_up.hp_roll,
                con_applied=result.level_up.con_applied,
                title=level_title(member.definition, result.level_after),
            )
        )
    return events


class Listener(Protocol):
    """An object you register on a session to react to what happens in play.

    A listener adds behavior to a game without changing osrlib, like a trap that teleports the
    party, a cursed item that speaks, or a score. Write a class with a `key` attribute and a
    `handle` method, and register an instance with
    [`GameSession.register_listener`][osrlib.crawl.session.GameSession.register_listener].
    After every accepted command, the session calls each listener's `handle` in the order they
    were registered. It passes the command's events and the listener's saved state. To play the
    triggers and quests an adventure defines, register
    [`Interpreter`][osrlib.crawl.interpreter.Interpreter], a listener osrlib provides.

    A listener doesn't change game state directly. It reacts by executing ordinary commands on the session. `handle`
    doesn't receive the session, so pass the session to your listener when you construct it, as
    [`Interpreter`][osrlib.crawl.interpreter.Interpreter] takes it. The command log records those commands, so a replay
    from the seed produces the same game. The session logs the events of those commands too, so `handle` doesn't return
    them. Returning them would put them in the log twice. The list `handle` returns is for events the listener builds
    itself, which the session would not otherwise log.

    A listener sees the events of every accepted command once, including the commands that
    other listeners execute, because the session calls the listeners for each of those too. It
    sees an event another listener returned only when that listener was registered before it.

    The session saves each listener's state under its `key` and passes it back on the next
    call, so a listener needs no separate storage. A listener is code and isn't saved, so
    register it again after [`load_game`][osrlib.persistence.load_game].

    Examples:
        ```python
        from collections.abc import Sequence

        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.events import Event
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.commands import EnterDungeon, MoveParty
        from osrlib.crawl.dungeon import Direction, DungeonSpec, Edge, EdgeKind, LevelSpec
        from osrlib.crawl.party import Party
        from osrlib.crawl.session import GameSession

        rules = Ruleset()
        stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
        hero = create_character(
            name="Hild",
            class_id="fighter",
            alignment=Alignment.LAWFUL,
            ruleset=rules,
            stream=stream,
        ).character
        corridor = LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)})
        crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(corridor,))
        adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))

        class StepCounter:
            key = "step_counter"

            def handle(self, events: Sequence[Event], state: dict) -> tuple[list[Event], dict]:
                steps = state.get("steps", 0)
                steps += sum(1 for event in events if event.code == "exploration.party.moved")
                return [], {"steps": steps}

        session = GameSession.new(Party(members=[hero]), adventure, seed=7)
        session.register_listener(StepCounter())
        session.execute(EnterDungeon(dungeon_id="crypt"))
        session.execute(MoveParty(direction=Direction.EAST))
        print(session.listener_state["step_counter"])
        # {'steps': 1}
        ```
    """

    key: str
    """The listener's name, unique within the session. Its state is saved and restored under this
    key, so keep it stable across releases of your game."""

    def handle(self, events: Sequence[Event], state: dict) -> tuple[list[Event], dict]:
        """React to one command's events.

        Args:
            events: The command's events, in the order they happened, followed by the events that
                listeners registered before this one returned.
            state: The state this listener returned last time, or the state a save restored. It's
                an empty dict on the first call.

        Returns:
            The events this listener built itself, which the session appends to the command's
                result and to the event log, and the state for the session to save. Return an empty list when the
                listener reacted by executing commands, because their events are already logged. The
                state must be a JSON-serializable dict, because saves include it.
        """
        ...


class GameSession:
    """A game in progress: the one object you execute commands against and read state from.

    Make one with [`GameSession.new`][osrlib.crawl.session.GameSession.new], or get
    one back from [`load_game`][osrlib.persistence.load_game] or
    [`replay_game`][osrlib.persistence.replay_game]. Then run the loop: build a
    command, pass it to [`execute`][osrlib.crawl.session.GameSession.execute], and render
    the result. Draw each screen from [`view`][osrlib.crawl.session.GameSession.view]
    rather than from the session's attributes, because a view contains only what its
    audience may see. To add behavior of your own, register a
    [`Listener`][osrlib.crawl.session.Listener] with
    [`register_listener`][osrlib.crawl.session.GameSession.register_listener].

    [`save_game`][osrlib.persistence.save_game] and `load_game` round-trip a session.
    Every accepted command is on `command_log` and every event on `event_log`, and
    replaying the command log from the same seed reaches the same game.

    The attributes are public so that a referee screen and the save functions can read the
    whole state. Don't write to them. A change you make yourself isn't in the command log, so
    a replay won't match the session.

    The same is true of the methods that change state, like
    [`spawn`][osrlib.crawl.session.GameSession.spawn],
    [`advance_turns`][osrlib.crawl.session.GameSession.advance_turns], and the experience
    awards. The command handlers call them, and they aren't commands themselves, so the
    command log doesn't record a call you make. If you call one during play, the game still
    saves and loads, because [`load_game`][osrlib.persistence.load_game] restores the
    saved state, but [`replay_game`][osrlib.persistence.replay_game] won't reproduce
    it. Where a command does the same job, execute the command instead.
    """

    def __init__(
        self,
        *,
        party: Party,
        adventure: Adventure,
        ruleset: Ruleset,
        streams: RngStreams,
        master_seed: int,
    ) -> None:
        """Build a session from parts you already have.

        You rarely call this yourself. Call [`GameSession.new`][osrlib.crawl.session.GameSession.new]
        to start a game, or [`load_game`][osrlib.persistence.load_game] to restore one, and both
        call this constructor. It doesn't check the adventure's references, and it doesn't assign
        character ids.

        Args:
            party: The party, in marching order, with ids already assigned.
            adventure: The adventure content.
            ruleset: The ruleset in play.
            streams: The seeded random streams.
            master_seed: The seed those streams came from, kept so that `load_game` can rebuild them.

        Raises:
            ContentValidationError: If the adventure bundles monster or item ids that collide with
                the built-in catalogs or with each other. This check still runs for `load_game`,
                which doesn't validate the rest of a saved adventure.
        """
        self.party = party
        """The party, in marching order. The order determines who is in the front rank in a fight.
        Outside a fight, [`ReorderParty`][osrlib.crawl.commands.ReorderParty] sets the order. In a
        battle, a member who withdraws alone trades places with the member named in
        [`BattleDeclaration.replacement_id`][osrlib.crawl.commands.BattleDeclaration.replacement_id].
        [`AddMember`][osrlib.crawl.commands.AddMember] is the only command that adds a member. It adds
        the member at the back."""
        self.adventure = adventure
        """The adventure being played: its town, dungeons, quests, and any content it bundles. It's
        frozen, and a save contains a copy of it, so a saved game needs no other file to
        load."""
        catalog, colliding = _effective_monsters(adventure, load_monsters())
        if colliding:
            raise ContentValidationError(
                "adventure bundles colliding monster ids: " + ", ".join(repr(monster_id) for monster_id in colliding)
            )
        self._monster_catalog = catalog
        equipment, colliding_items = _effective_equipment(adventure, load_equipment())
        if colliding_items:
            raise ContentValidationError(
                "adventure bundles colliding item ids: " + ", ".join(repr(item_id) for item_id in colliding_items)
            )
        self._equipment_catalog = equipment
        self.ruleset = ruleset
        """The ruleset in play. Its options settle the rules that B/X leaves open, like when
        experience is awarded. See
        [the adaptations register](https://mmacy.github.io/osrlib-python/adaptations/), the page
        that lists where osrlib settles an ambiguous rule or supplies a default."""
        self.streams = streams
        """The session's named random streams. Everything the engine rolls comes from one of them,
        and a save contains their positions, so a restored game continues the same way."""
        self.master_seed = master_seed
        """The seed the streams were built from. A save contains it and no view does, because
        knowing it would let a player predict every roll to come."""
        self.allocator = IdAllocator()
        """The source of session ids. Every id the session hands out, for characters, monsters, NPC
        adventurers, effects, encounter groups, and treasure, comes from here as `<kind>-NNNN`,
        counting up for each kind. No id is random, so two runs of the same commands assign the
        same ids."""
        self.ledger = EffectsLedger()
        """The active effects: spells running, conditions, a torch burning down. Advancing the clock
        advances the ledger, which reports each effect that ticks or expires as an event."""
        self.clock = GameClock()
        """The game clock. `clock.rounds` is how much time has passed since the session began, in
        rounds. Each turn the party spends exploring a dungeon counts toward its next required rest
        and the next wandering-monster check, and each new day uses up food and water."""
        self.mode = SessionMode.TOWN
        """Which [`SessionMode`][osrlib.crawl.commands.SessionMode] the session is in, and so which
        commands it will accept. A new session starts in `town`."""
        self.dungeon_state = DungeonState()
        self.monsters: dict[str, MonsterInstance] = {}
        """The live monsters, keyed by session id. Spawning adds to it, and a defeated monster is
        never removed, so a later event can still refer to it."""
        self.npcs: dict[str, Character] = {}
        """The live NPC adventurers, keyed by session id. They are characters rather than monsters,
        and they fight under the same rules as the party."""
        self.flags: dict[str, str | int | bool] = {}
        """The session flags: a key-value store that
        [`SetFlag`][osrlib.crawl.commands.SetFlag] writes. The engine reads it to test the conditions
        an adventure puts on triggers, quests, doors, and level exits. You choose the keys and
        what they mean."""
        self.fired_triggers: list[str] = []
        """The ids of the triggers that have fired, in the order they first fired. Read it to find
        out whether a trigger has fired before. Each firing, including a repeat, is in the event
        log as a [`TriggerFiredEvent`][osrlib.crawl.events.TriggerFiredEvent]."""
        self.journal: list[JournalEntry] = []
        """The journal entries, in the order they were written. A player view contains the same
        list, and that's where you should read it from."""
        # One state block per authored quest, in document order, seeded here so that
        # every path that builds a session, new or load or replay, starts from the
        # same block. A quest with no activation clause is active from round 0
        # because no command can activate it before play begins. The rest wait
        # to be activated. Objectives start visible unless the author hid them.
        self.quests: dict[str, QuestState] = {
            quest.id: QuestState(
                status="active" if quest.activation is None else "inactive",
                objectives={
                    objective.id: ObjectiveState(revealed=not objective.hidden, complete=False)
                    for objective in quest.objectives
                },
            )
            for quest in adventure.quests
        }
        """The state of every quest in the adventure, keyed by quest id, in the order the adventure
        lists them. See [`QuestState`][osrlib.crawl.session.QuestState]."""
        self.listener_state: dict[str, dict] = {}
        """Each registered listener's state, keyed by its `key`. It's saved and restored with the
        session, so a listener re-registered after a load picks up where it left off."""
        self.listeners: list[Listener] = []
        """The registered listeners, in the order they run. Listeners are code, so they aren't
        saved: register them again after a load."""
        self.command_log: list[Command] = []
        """Every accepted command, in order. Refused commands are absent, because they changed
        nothing. [`replay_game`][osrlib.persistence.replay_game] re-executes this list from the
        master seed to rebuild the session."""
        self.event_log: list[Event | dict] = []
        """Everything that has happened, in order. Entries are events. A session restored from a
        save may also contain a raw mapping for an event this version of the library has no class
        for. The session keeps such a mapping rather than dropping it."""
        self.death_records: dict[str, DeathRecord] = {}
        """When and how each dead party member died, keyed by character id. See
        [`DeathRecord`][osrlib.crawl.session.DeathRecord]."""
        self.defeated_monsters: list[DefeatedMonsterRecord] = []
        """The creatures defeated since the last award, which is what the experience award adds
        up. See [`DefeatedMonsterRecord`][osrlib.crawl.session.DefeatedMonsterRecord]."""
        self.deprivation: dict[str, DeprivationState] = {}
        """Each member's food and water counts, keyed by character id. See
        [`DeprivationState`][osrlib.crawl.session.DeprivationState]."""
        self.treasure_snapshot_cp: int | None = None
        """What the party's treasure was worth, in copper pieces, when it left town, or `None`
        when no delve is under way. The award pays for the difference between this and what comes
        back."""
        # Exploration bookkeeping (all serialized into saves).
        self.odometer_thirds = 0
        """How far the party has walked during the current turn, in thirds of a foot. When it
        reaches three times the party's exploration rate, the clock advances one turn and this
        resets to zero."""
        self.turns_since_rest = 0
        """Turns since the party last rested. The engine counts these for the fatigue rule, and a
        rest resets them."""
        self.wandering_counter = 0
        """Turns since the last wandering check. When it reaches the level's interval, the engine
        makes the check and resets this."""
        self.noise_since_check = False
        """Whether the party has made noise since the last wandering check. Every attempt to force
        a door makes noise, whether or not the door opens. Noise raises the next check's chance by
        1 in 6, and the check clears it. A failed attempt also alerts the area beyond the door, so
        the party can't surprise the monsters there."""
        self.sleep_count = 0
        """How many nights or days the party has slept through. Preparing spells needs a sleep the
        caster hasn't already prepared from."""
        self.last_prepared_sleep: dict[str, int] = {}
        """The `sleep_count` at which each caster last prepared spells, keyed by character id. The
        session uses it to enforce one preparation per sleep."""
        self.alerted_areas: list[str] = []
        """The keyed areas whose monsters have been alerted, as `"{dungeon}:{level}:{area}"`
        references. Monsters that heard the party coming aren't surprised when it walks in."""
        self.heard_areas: list[str] = []
        """The keyed areas the party has heard something in, as `"{dungeon}:{level}:{area}"`
        references. A party that knows what is behind the door isn't surprised by it."""
        self.encounter: EncounterState | None = None
        """The encounter under way, as an [`EncounterState`][osrlib.crawl.encounter.EncounterState],
        or `None`. It contains the monster groups, their distances, the monsters' reaction to the
        party, and any chase in progress."""
        self.battle: BattleState | None = None
        """The battle under way, as a [`BattleState`][osrlib.crawl.battle.BattleState], or `None`.
        It contains the round number and the rest of the battle's state from one round to the next."""
        self._provisions_day = 0
        self._handlers: dict[type[Command], Any] | None = None
        # Runtime extension points that you register again after a load, like listeners.
        # Policies are code, so they are never serialized.
        self.action_policies: dict[str, object] = {}
        """Action policies for monster groups, keyed by encounter group id. To choose a group's
        actions yourself, write an [`ActionPolicy`][osrlib.crawl.battle.ActionPolicy] and set
        `session.action_policies[group.id]` to it, where `group` is an
        [`EncounterGroup`][osrlib.crawl.encounter.EncounterGroup] from `session.encounter.groups`.
        Without an entry, a group uses the built-in policy for its kind. Policies are code, so they
        aren't saved: register them again after a load."""

    @classmethod
    def new(cls, party: Party, adventure: Adventure, *, seed: int, ruleset: Ruleset | None = None) -> GameSession:
        """Start a new game: validate the adventure, assign character ids, and open in town.

        Start every new game here. Build characters with
        [`create_character`][osrlib.core.character.create_character], put them in a
        [`Party`][osrlib.crawl.party.Party] in marching order, load or build an
        [`Adventure`][osrlib.crawl.adventure.Adventure], and call this. The session comes back in
        `town`, at round 0, ready for the first
        [`execute`][osrlib.crawl.session.GameSession.execute]. To continue an existing game, use
        [`load_game`][osrlib.persistence.load_game] instead.

        The session keeps the party you pass, not a copy, so from then on your character objects
        are the session's live party members. To add a member once play has begun, execute
        [`AddMember`][osrlib.crawl.commands.AddMember]. It gives the new member the next
        `character-NNNN` id from the session's [`IdAllocator`][osrlib.core.monsters.IdAllocator].
        Don't append to `party.members` yourself, because the command log wouldn't record the
        change, and a replay wouldn't have the member.

        This method checks the whole adventure before it builds anything, so an unknown monster id
        or a stair to a level that doesn't exist fails here, not partway through a delve.

        The same seed and the same commands produce the same game, which is what makes a bug
        reproducible and a replay possible. Use a fresh seed for each game, and record it.

        Args:
            party: The party, in marching order. Members that have no id get one here, as
                `character-NNNN`. Members that already have one, like a party loaded from an
                earlier session, keep it.
            adventure: The adventure content to play.
            seed: The master seed every random draw in the session comes from.
            ruleset: The ruleset options in play. Defaults to a stock
                [`Ruleset`][osrlib.core.ruleset.Ruleset].

        Returns:
            The session, in town, at round 0, with an empty command log.

        Raises:
            ContentValidationError: If the adventure refers to something that doesn't exist, like
                an unknown monster or item id or a transition with no destination, or if it
                bundles a monster or item whose id is already a built-in id or another bundled
                entry's id. The message lists every problem found, and no member has been given
                an id. Fix the adventure and call again. The same adventure fails the same way
                every time.

        Examples:
            ```python
            from osrlib.core.alignment import Alignment
            from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
            from osrlib.core.rng import RngStreams
            from osrlib.core.ruleset import Ruleset
            from osrlib.crawl.adventure import Adventure, TownSpec
            from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
            from osrlib.crawl.party import Party
            from osrlib.crawl.session import GameSession

            rules = Ruleset()
            stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
            hero = create_character(
                name="Hild",
                class_id="fighter",
                alignment=Alignment.LAWFUL,
                ruleset=rules,
                stream=stream,
            ).character
            level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
            crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
            adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))

            session = GameSession.new(Party(members=[hero]), adventure, seed=7)
            print(session.mode.value, session.clock.rounds, hero.id)
            # town 0 character-0001
            ```
        """
        validate_adventure(adventure, load_monsters(), load_equipment())
        session = cls(
            party=party,
            adventure=adventure,
            ruleset=ruleset if ruleset is not None else Ruleset(),
            streams=RngStreams(master_seed=seed),
            master_seed=seed,
        )
        for member in party.members:
            if member.id is None:
                member.id = session.allocator.allocate("character")
        return session

    @property
    def dungeon_state(self) -> DungeonState:
        """Everything play has changed on top of the adventure's map.

        It holds where the party is, which cells it has walked and seen, door state, found and sprung
        traps, drop piles, and generated caches. The dungeons in the adventure never change.

        When you set it, the session links the new state to this session's adventure. Then
        [`DungeonState.door`][osrlib.crawl.dungeon.DungeonState.door] starts a door with no entry from
        the door's [`DoorSpec`][osrlib.crawl.dungeon.DoorSpec].
        """
        return self._dungeon_state

    @dungeon_state.setter
    def dungeon_state(self, state: DungeonState) -> None:
        from osrlib.crawl import exploration

        state._start_doors_from(lambda ref: exploration._new_door_state(self.adventure, ref))
        self._dungeon_state = state

    @property
    def metadata(self) -> dict[str, object]:
        """Return the versions a client checks to confirm it's talking to a compatible engine.

        Send it in a handshake, or show it on a debug screen. A save includes the same two values.
        When you pass a save's `engine_version` to [`replay_game`][osrlib.persistence.replay_game],
        it refuses to replay the command log under a different engine version.

        Returns:
            A dict with `schema_version`, the version of the format that saves, commands, and
                events share, and `engine_version`, the installed osrlib version.

        Examples:
            ```python
            from osrlib.core.alignment import Alignment
            from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
            from osrlib.core.rng import RngStreams
            from osrlib.core.ruleset import Ruleset
            from osrlib.crawl.adventure import Adventure, TownSpec
            from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
            from osrlib.crawl.party import Party
            from osrlib.crawl.session import GameSession
            from osrlib.versioning import SCHEMA_VERSION

            rules = Ruleset()
            stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
            hero = create_character(
                name="Hild",
                class_id="fighter",
                alignment=Alignment.LAWFUL,
                ruleset=rules,
                stream=stream,
            ).character
            level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
            crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
            adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))
            session = GameSession.new(Party(members=[hero]), adventure, seed=7)

            handshake = session.metadata
            print(sorted(handshake), handshake["schema_version"] == SCHEMA_VERSION)
            # ['engine_version', 'schema_version'] True
            ```
        """
        return {"schema_version": SCHEMA_VERSION, "engine_version": engine_version()}

    @property
    def effective_monsters(self) -> MonsterCatalog:
        """Return the monster catalog this session resolves template ids against.

        It's the built-in catalog plus whatever monsters the adventure bundles. Every part of the
        engine that turns a template id into a creature reads it: spawning, keyed encounters,
        wandering rows, listen checks. Use it when you want to look a template up the way the
        session does, rather than calling [`load_monsters`][osrlib.data.load_monsters] and missing
        the monsters the adventure bundles.

        Returns:
            The catalog. For an adventure that bundles nothing, it's the built-in catalog.

        Examples:
            ```python
            from osrlib.core.alignment import Alignment
            from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
            from osrlib.core.rng import RngStreams
            from osrlib.core.ruleset import Ruleset
            from osrlib.crawl.adventure import Adventure, TownSpec
            from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
            from osrlib.crawl.party import Party
            from osrlib.crawl.session import GameSession
            from osrlib.data import load_monsters

            rules = Ruleset()
            stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
            hero = create_character(
                name="Hild",
                class_id="fighter",
                alignment=Alignment.LAWFUL,
                ruleset=rules,
                stream=stream,
            ).character
            level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
            crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))

            # The adventure bundles a monster of its own, built from the goblin's entry.
            cave_goblin = load_monsters().get("goblin").model_copy(update={"id": "cave_goblin", "name": "Cave Goblin"})
            adventure = Adventure(
                name="A First Delve",
                town=TownSpec(name="Threshold"),
                dungeons=(crypt,),
                monsters=(cave_goblin,),
            )
            session = GameSession.new(Party(members=[hero]), adventure, seed=7)

            print(session.effective_monsters.get("cave_goblin").name, session.effective_monsters.get("goblin").name)
            # Cave Goblin Goblin
            ```
        """
        return self._monster_catalog

    @property
    def effective_equipment(self) -> EquipmentCatalog:
        """Return the equipment catalog this session resolves item ids against.

        It's the built-in catalog plus whatever items the adventure bundles, and every part of the
        engine that turns an item id into an item reads it: treasure caches,
        [`GrantItem`][osrlib.crawl.commands.GrantItem], picking a drop pile back up. The town shop
        is the exception: it sells from the built-in equipment lists, so a bundled item is never on
        the shelf.

        Returns:
            The catalog. For an adventure that bundles nothing, it's the built-in catalog.

        Examples:
            ```python
            from osrlib.core.alignment import Alignment
            from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
            from osrlib.core.rng import RngStreams
            from osrlib.core.ruleset import Ruleset
            from osrlib.crawl.adventure import Adventure, TownSpec
            from osrlib.crawl.commands import GrantItem
            from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
            from osrlib.crawl.party import Party
            from osrlib.crawl.session import GameSession

            rules = Ruleset()
            stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
            hero = create_character(
                name="Hild",
                class_id="fighter",
                alignment=Alignment.LAWFUL,
                ruleset=rules,
                stream=stream,
            ).character
            level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
            crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
            adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))
            session = GameSession.new(Party(members=[hero]), adventure, seed=7)

            result = session.execute(GrantItem(character_id=hero.id, item_id="lantern", quantity=1))
            acquired = result.events[0]
            print([session.effective_equipment.get(item_id).name for item_id in acquired.item_ids])
            # ['Lantern']
            ```
        """
        return self._equipment_catalog

    # ------------------------------------------------------------------ dispatch

    def _handler_table(self) -> Mapping[type[Command], Any]:
        """Return this session's command-to-handler table, built once and cached on the instance.

        Built the first time this session dispatches a command, by merging the private handler tables of
        the exploration, encounter, and battle modules with the referee handlers this module defines. The
        import happens inside the call rather than at the top of this module because those three
        modules import [`GameSession`][osrlib.crawl.session.GameSession] from here at their top
        level: importing them before this module has finished defining the class would fail. Each
        session builds a separate table, so replacing a handler on one session never affects
        another.
        """
        if self._handlers is None:
            from osrlib.crawl import battle, encounter, exploration

            self._handlers = {
                **_REFEREE_HANDLERS,
                **exploration._HANDLERS,
                **encounter._HANDLERS,
                **battle._HANDLERS,
            }
        return self._handlers

    def execute(self, command: Command) -> CommandResult:
        """Execute one command and return everything it caused.

        This is the loop you run: build a command from
        [`osrlib.crawl.commands`][osrlib.crawl.commands], pass it here, check `accepted`, and
        render either the rejections or the events. The command log records only what this method
        executes, so if you change state only through it, you can always replay the game.

        Validation runs first and changes nothing: a refused command draws no dice, spends no game
        time, changes no state, and stays out of the command log. A rejection is the game refusing
        the action, not an error. Show it to the player in your own words, from its `code` and
        `params`. The
        [rejection codes reference](https://mmacy.github.io/osrlib-python/reference/rejection-codes/)
        lists every code.

        After an accepted command applies, the session records each party member's death with what
        killed them. If the command's events killed or petrified a member and left no member
        standing, which means neither dead nor turned to stone, the session ends in `game_over`,
        and a [`GameOverEvent`][osrlib.crawl.events.GameOverEvent] closes the result. A session
        that has already ended stays in its mode.

        The result contains the whole chain in log order: the handler's events, then, for each
        registered listener in turn, the events of the commands that listener executed, however
        deeply nested, followed by the events it returned itself. So one result is enough to
        render the full reaction, and you don't have to read `event_log` to catch the rest.

        Args:
            command: The command to execute.

        Returns:
            The result. A refused command contains rejections and no events. An accepted
                one contains events and no rejections.

        Raises:
            ValueError: If the command class has no handler, which means it was defined outside
                osrlib rather than built from
                [`osrlib.crawl.commands`][osrlib.crawl.commands].
            Exception: Whatever a registered listener's `handle` raises, unchanged. By then the
                command has applied and is in the command log.

        Examples:
            ```python
            from osrlib.core.alignment import Alignment
            from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
            from osrlib.core.rng import RngStreams
            from osrlib.core.ruleset import Ruleset
            from osrlib.crawl.adventure import Adventure, TownSpec
            from osrlib.crawl.commands import EnterDungeon
            from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
            from osrlib.crawl.party import Party
            from osrlib.crawl.session import GameSession

            rules = Ruleset()
            stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
            hero = create_character(
                name="Hild",
                class_id="fighter",
                alignment=Alignment.LAWFUL,
                ruleset=rules,
                stream=stream,
            ).character
            level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
            crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
            adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))
            session = GameSession.new(Party(members=[hero]), adventure, seed=7)

            result = session.execute(EnterDungeon(dungeon_id="crypt"))
            print(result.accepted, [event.code for event in result.events])
            # True ['exploration.location.entered']

            again = session.execute(EnterDungeon(dungeon_id="crypt"))  # already inside
            print(again.accepted, again.rejections[0].code)
            # False session.command.wrong_mode
            ```
        """
        if self.mode not in type(command).allowed_modes:
            return CommandResult(
                accepted=False,
                rejections=(
                    Rejection(
                        code="session.command.wrong_mode",
                        params={"command": command.command_type, "mode": self.mode.value},
                    ),
                ),
            )
        handler = self._handler_table().get(type(command))
        if handler is None:
            raise ValueError(f"no handler for command type {command.command_type!r}")
        rejections, events = handler(self, command)
        if rejections:
            return CommandResult(accepted=False, rejections=tuple(rejections))
        self.command_log.append(command)
        self._pile_saved_items(events)
        self._record_deaths(events)
        if self._felled_a_member(events):
            events.extend(self._end_on_party_wipe())
        self.event_log.extend(events)
        # The two lists do different things. `accumulated` is what the listeners are dispatched
        # over: this command's events plus what earlier listeners authored. A
        # listener's nested commands ran the whole listener loop themselves, so every
        # listener has already seen those events at the nested level. Putting them in
        # here would deliver them to later listeners a second time. `envelope` is what
        # the caller gets back, and it does include them, because the caller reads the
        # result once and needs the whole chain.
        accumulated = list(events)
        envelope = list(events)
        self._persist_sight()
        for listener in self.listeners:
            mark = len(self.event_log)
            emitted, state = listener.handle(tuple(accumulated), self.listener_state.get(listener.key, {}))
            self.listener_state[listener.key] = state
            # Every event logged by the commands this listener executed while it ran,
            # their events and any deeper listener reactions, each already in the log
            # exactly once and in log order, then the events it authored itself.
            # (The log contains serialized entries only for a session restored from a
            # save. Executing a command never appends one.)
            envelope.extend(entry for entry in self.event_log[mark:] if isinstance(entry, Event))
            envelope.extend(emitted)
            accumulated.extend(emitted)
            self.event_log.extend(emitted)
        return CommandResult(accepted=True, events=tuple(envelope))

    def _persist_sight(self) -> None:
        """Mark the cells the party's light shows right now as seen on the party's map.

        Runs after every accepted command, the one place that covers every action which can change
        what the party can see (entering, moving, stairs, doors, lighting, placement, discovery,
        relocation after a fight), and calls
        [`mark_seen`][osrlib.crawl.dungeon.DungeonState.mark_seen] with the cells `_light_reveal`
        shows from where the party stands. A refused command changes nothing, so it never gets
        here.

        It runs before the listeners so that the cells a live session records as seen are the cells a
        replay rebuilds. A listener that moves the party, an authored teleport for instance, executes a
        command, which marks the cells seen at that destination. Marking this command's cells
        afterwards would read the light from the destination and lose the cells seen where the move
        ended, while a replay, running the same commands with no listeners, marks both in order.
        """
        from osrlib.crawl.exploration import _light_reveal

        key, cells = _light_reveal(self)
        if key is None or not cells:
            return
        dungeon_id, level_text = key.rsplit(":", 1)
        self.dungeon_state.mark_seen(dungeon_id, int(level_text), cells)

    def register_listener(self, listener: Listener) -> None:
        """Register a listener, which then runs after every accepted command.

        Listeners run in the order they were registered, after the command's handler. This is
        how you add behavior without changing the engine. See
        [`Listener`][osrlib.crawl.session.Listener] for what one looks like and what it may do.

        A listener is code, so a save doesn't include it, but a save does include its state.
        Register your listeners again after [`load_game`][osrlib.persistence.load_game] or
        [`replay_game`][osrlib.persistence.replay_game]. After a load, each one gets its saved state
        back under its key. A replay runs with no listeners registered, because the commands they
        executed are already in the log.

        Args:
            listener: The listener to register. The session saves its state under its `key`, so
                give each listener a different key, and keep it the same across releases of your game.

        Examples:
            ```python
            from collections.abc import Sequence

            from osrlib.core.alignment import Alignment
            from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
            from osrlib.core.clock import TimeUnit
            from osrlib.core.events import Event
            from osrlib.core.rng import RngStreams
            from osrlib.core.ruleset import Ruleset
            from osrlib.crawl.adventure import Adventure, TownSpec
            from osrlib.crawl.commands import AdvanceTime
            from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
            from osrlib.crawl.party import Party
            from osrlib.crawl.session import GameSession
            from osrlib.persistence import load_game, save_game

            rules = Ruleset()
            stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
            hero = create_character(
                name="Hild",
                class_id="fighter",
                alignment=Alignment.LAWFUL,
                ruleset=rules,
                stream=stream,
            ).character
            level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
            crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
            adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))
            session = GameSession.new(Party(members=[hero]), adventure, seed=7)

            class CommandCounter:
                key = "command_counter"

                def handle(self, events: Sequence[Event], state: dict) -> tuple[list[Event], dict]:
                    return [], {"commands": state.get("commands", 0) + 1}

            session.register_listener(CommandCounter())
            session.execute(AdvanceTime(n=1, unit=TimeUnit.TURN))

            # A save keeps the listener's state but not the listener, so register it again after a load.
            restored = load_game(save_game(session))
            restored.register_listener(CommandCounter())
            restored.execute(AdvanceTime(n=1, unit=TimeUnit.TURN))
            print(restored.listener_state["command_counter"])
            # {'commands': 2}
            ```
        """
        self.listeners.append(listener)
        self.listener_state.setdefault(listener.key, {})

    # ------------------------------------------------------------------ registry

    def registry(self) -> dict[str, Any]:
        """Return every live entity in the session, keyed by id.

        Party members come first in marching order, then monsters, then NPC adventurers. The
        engine passes this to the rules functions that look a target up by id. Use it when you
        resolve a rule yourself. For anything you draw on screen, read a view instead.

        Returns:
            A fresh dict from entity id to the live object: [`Character`][osrlib.core.character.Character]
                for members and NPCs, [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] for
                monsters. Editing the dict doesn't change the session. Editing the objects in it
                does.

        Examples:
            ```python
            from osrlib.core.alignment import Alignment
            from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
            from osrlib.core.rng import RngStreams
            from osrlib.core.ruleset import Ruleset
            from osrlib.crawl.adventure import Adventure, TownSpec
            from osrlib.crawl.commands import EnterDungeon
            from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
            from osrlib.crawl.party import Party
            from osrlib.crawl.session import GameSession

            rules = Ruleset()
            stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
            hero = create_character(
                name="Hild",
                class_id="fighter",
                alignment=Alignment.LAWFUL,
                ruleset=rules,
                stream=stream,
            ).character
            level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
            crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
            adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))
            session = GameSession.new(Party(members=[hero]), adventure, seed=7)
            session.execute(EnterDungeon(dungeon_id="crypt"))
            session.spawn("goblin", 1)

            for entity_id, entity in session.registry().items():
                print(entity_id, entity.current_hp)
            # character-0001 3
            # monster-0001 1
            ```
        """
        entities: dict[str, Any] = {_member_id(member): member for member in self.party.members}
        entities.update(self.monsters)
        entities.update(self.npcs)
        return entities

    def combatant(self, combatant_id: str) -> MonsterInstance | Character | None:
        """Return the monster or NPC adventurer with this id, or `None`.

        An [`EncounterGroup`][osrlib.crawl.encounter.EncounterGroup] contains ids that can be either,
        and this resolves both without you having to know which. For a party member, call
        [`member`][osrlib.crawl.session.GameSession.member]. For everything at once, call
        [`registry`][osrlib.crawl.session.GameSession.registry].

        Args:
            combatant_id: The entity id, as it appears on an encounter group or an event.

        Returns:
            The live [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] or
                [`Character`][osrlib.core.character.Character], or `None` when no monster or NPC
                adventurer has that id.

        Examples:
            ```python
            from osrlib.core.alignment import Alignment
            from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
            from osrlib.core.rng import RngStreams
            from osrlib.core.ruleset import Ruleset
            from osrlib.crawl.adventure import Adventure, TownSpec
            from osrlib.crawl.commands import EnterDungeon, SpawnMonsters
            from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
            from osrlib.crawl.party import Party
            from osrlib.crawl.session import GameSession

            rules = Ruleset()
            stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
            hero = create_character(
                name="Hild",
                class_id="fighter",
                alignment=Alignment.LAWFUL,
                ruleset=rules,
                stream=stream,
            ).character
            level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
            crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
            adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))
            session = GameSession.new(Party(members=[hero]), adventure, seed=7)
            session.execute(EnterDungeon(dungeon_id="crypt"))
            session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=30))

            for group in session.encounter.groups:
                for monster_id in group.monster_ids:
                    monster = session.combatant(monster_id)
                    print(monster_id, monster.template.name, monster.current_hp)
            # monster-0001 Goblin 1
            # monster-0002 Goblin 3
            ```
        """
        found = self.monsters.get(combatant_id)
        if found is not None:
            return found
        return self.npcs.get(combatant_id)

    def member(self, character_id: str) -> Character:
        """Return the party member with this id.

        The ids are the ones events use, so this is how you get from an event to the character
        it's about. It is [`Party.member`][osrlib.crawl.party.Party.member] with the session's
        party filled in.

        Args:
            character_id: The member's session id, like `"character-0001"`.

        Returns:
            The member, living or dead.

        Raises:
            ValueError: If no member of the party has that id.

        Examples:
            ```python
            from osrlib.core.alignment import Alignment
            from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
            from osrlib.core.items import Coins
            from osrlib.core.rng import RngStreams
            from osrlib.core.ruleset import Ruleset
            from osrlib.crawl.adventure import Adventure, TownSpec
            from osrlib.crawl.commands import GrantCoins
            from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
            from osrlib.crawl.party import Party
            from osrlib.crawl.session import GameSession

            rules = Ruleset()
            stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
            hero = create_character(
                name="Hild",
                class_id="fighter",
                alignment=Alignment.LAWFUL,
                ruleset=rules,
                stream=stream,
            ).character
            level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
            crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
            adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))
            session = GameSession.new(Party(members=[hero]), adventure, seed=7)

            result = session.execute(GrantCoins(character_id=hero.id, coins=Coins(gp=25)))
            recipient = session.member(result.events[0].character_id)
            print(recipient.name, recipient.inventory.purse.gp)
            # Hild 125
            ```
        """
        return self.party.member(character_id)

    def spawn(self, template_id: str, count: int, *, alignment: Alignment | None = None) -> list[MonsterInstance]:
        """Spawn monsters into the session and return them.

        Each instance gets hit points rolled from the seeded spawn stream and an id from the session
        allocator, and is added to `monsters`, where the rest of the engine can find it.
        Spawning alone puts nothing in front of the party, and the session mode doesn't change.
        To field the monsters, pass them to
        [`start_encounter`][osrlib.crawl.encounter.start_encounter]. A monster that regenerates,
        like a troll, starts to regenerate when its encounter opens, not when it spawns.

        To put monsters in front of the party as the referee, execute
        [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters] instead. In one command, it spawns
        them and opens an encounter with them, or adds them to the open one. This method isn't a
        command, so the command log doesn't record it, and
        [`replay_game`][osrlib.persistence.replay_game] won't reproduce a game if you call it
        during play.

        Args:
            template_id: Any id in the session's
                [`effective_monsters`][osrlib.crawl.session.GameSession.effective_monsters]
                catalog: a built-in id (see
                [the monster id index](https://mmacy.github.io/osrlib-python/reference/content-ids/monsters/))
                or one the adventure bundles. The catalog's `monsters` tuple lists every template, each with
                its `id`.
            count: How many to spawn.
            alignment: An alignment to give them instead of the template's, for keyed content
                whose author wants, say, lawful goblins.

        Returns:
            The new instances, in spawn order.

        Raises:
            ValueError: If the catalog has no such template id.

        Examples:
            ```python
            from osrlib.core.alignment import Alignment
            from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
            from osrlib.core.rng import RngStreams
            from osrlib.core.ruleset import Ruleset
            from osrlib.crawl.adventure import Adventure, TownSpec
            from osrlib.crawl.commands import EnterDungeon
            from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
            from osrlib.crawl.party import Party
            from osrlib.crawl.session import GameSession

            rules = Ruleset()
            stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
            hero = create_character(
                name="Hild",
                class_id="fighter",
                alignment=Alignment.LAWFUL,
                ruleset=rules,
                stream=stream,
            ).character
            level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
            crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
            adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))
            session = GameSession.new(Party(members=[hero]), adventure, seed=7)
            session.execute(EnterDungeon(dungeon_id="crypt"))

            goblins = session.spawn("goblin", 2)
            print([(goblin.id, goblin.current_hp) for goblin in goblins])
            # [('monster-0001', 1), ('monster-0002', 3)]

            # Spawning opens no encounter: the session is still exploring.
            print(session.combatant("monster-0002").template.name, session.mode.value)
            # Goblin exploring
            ```
        """
        template = self.effective_monsters.get(template_id)
        spawned = []
        for _ in range(count):
            instance = spawn_monster(
                template,
                id=self.allocator.allocate("monster"),
                stream=self.streams.get(MONSTER_SPAWN_STREAM),
                alignment=alignment,
            )
            self.monsters[instance.id] = instance
            spawned.append(instance)
        return spawned

    # ------------------------------------------------------------------ time

    def advance_rounds(self, n: int) -> list[Event]:
        """Advance the clock by rounds and return what happened while it moved.

        The commands advance time themselves, so you call this only when you are resolving
        something outside the command set. To move the clock as the referee, execute
        [`AdvanceTime`][osrlib.crawl.commands.AdvanceTime] instead. For rounds, its handler calls
        this method, and the command log records the command.

        As time passes, effects tick and expire, a light burning out leaves the party in the dark,
        and each new day uses up food and water. The ledger reports an expiring effect as an event
        only the referee sees, so for an expiring light the session adds a
        [`LightEvent`][osrlib.crawl.events.LightEvent], which the players see and whose `source` is
        the light that went out.

        To advance whole turns, with the required rests and the wandering-monster checks, call
        [`advance_turns`][osrlib.crawl.session.GameSession.advance_turns] instead. This method runs
        neither.

        Args:
            n: How many rounds to advance.

        Returns:
            The ledger's events, each expired light's event followed by its `LightEvent`, and then
                the food and water events for each new day.

        Raises:
            ValueError: If `n` is negative. The clock never runs backwards.
        """
        member_ids = {member.id for member in self.party.members}
        light_sources = {
            effect.effect_id: (str(effect.definition.params.get("source", effect.definition.kind)), effect.target_ref)
            for effect in self.ledger.effects
            if effect.definition.kind in LIGHT_EFFECT_KINDS
        }
        events = self.ledger.advance(
            self.clock,
            n,
            TimeUnit.ROUND,
            self.registry(),
            stream=self.streams.get(EFFECTS_STREAM),
            allocator=self.allocator,
        )
        out: list[Event] = []
        for event in events:
            out.append(event)
            if isinstance(event, EffectExpiredEvent) and event.effect_id in light_sources:
                source, bearer = light_sources[event.effect_id]
                out.append(
                    LightEvent(
                        code="exploration.light.expired",
                        character_id=bearer if bearer in member_ids else None,
                        source=source,
                    )
                )
        while self.clock.rounds // ROUNDS_PER_DAY > self._provisions_day:
            from osrlib.crawl import exploration

            self._provisions_day += 1
            out.extend(exploration.consume_provisions(self))
        return out

    def advance_turns(
        self, turns: int, *, resting: bool = False, hidden: bool = False, field: bool | None = None
    ) -> tuple[list[Event], bool]:
        """Advance whole turns, one at a time, applying the rules that run each turn.

        This is the time path the exploration commands use, and the one to call when you are
        resolving elapsed time yourself. If the clock is partway through a turn, this method first
        advances it to the next turn boundary, so an action that costs a turn includes the part of
        the turn the party had already spent walking.

        For each turn, the clock and the active effects advance, and a new day uses up food and
        water.

        The `field` argument says whether the dungeon rules for rest and wandering monsters
        apply. By default they apply while the session is in `exploring` mode, that is, while
        the party explores a dungeon. When they apply:

        - Each turn counts toward the party's next required rest, unless the party is resting.
        - Each turn counts toward the next wandering-monster check, which runs when it's due.
        - A check that produces an encounter stops the advance at that turn, because the party
          now has something else to deal with. The second return value is then True.
        - The advance stops as soon as no party member is alive, because these rules apply only
          while someone is.

        When they don't apply, as in town or on the road, the advance keeps going even with every
        member dead. The time limit on bringing a dead character back has to keep running while
        the party lies dead.

        To move the clock during play, execute
        [`AdvanceTime`][osrlib.crawl.commands.AdvanceTime] or [`Rest`][osrlib.crawl.commands.Rest]
        instead, which the command log records.

        Args:
            turns: How many turns to advance.
            resting: True while the party is resting. Resting turns don't count toward the next
                required rest. Resting alone doesn't change the wandering chance.
            hidden: True while the party hides, resting quietly in an out-of-the-way place. It
                lowers the chance of each wandering check in the span by 1 in 6. A
                [`Rest`][osrlib.crawl.commands.Rest] with `hidden` set passes both `resting`
                and `hidden`.
            field: Whether the dungeon rules for rest and wandering monsters apply. Defaults to
                whether the session is in `exploring` mode, because the engine rolls for wandering
                monsters only in a dungeon. Time in town and on the road doesn't count.

        Returns:
            The events, and True when a wandering encounter interrupted the span before it ran
                out.

        Examples:
            ```python
            from osrlib.core.alignment import Alignment
            from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
            from osrlib.core.rng import RngStreams
            from osrlib.core.ruleset import Ruleset
            from osrlib.crawl.adventure import Adventure, TownSpec
            from osrlib.crawl.commands import EnterDungeon
            from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
            from osrlib.crawl.party import Party
            from osrlib.crawl.session import GameSession

            rules = Ruleset()
            stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
            hero = create_character(
                name="Hild",
                class_id="fighter",
                alignment=Alignment.LAWFUL,
                ruleset=rules,
                stream=stream,
            ).character
            level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
            crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
            adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))
            session = GameSession.new(Party(members=[hero]), adventure, seed=7)
            session.execute(EnterDungeon(dungeon_id="crypt"))

            # Three turns in the dungeon: this level's wandering check runs after the second.
            events, interrupted = session.advance_turns(3)
            print([event.code for event in events], interrupted, session.clock.rounds)
            # ['exploration.wandering.checked'] False 180
            ```
        """
        from osrlib.crawl import exploration

        events: list[Event] = []
        for _ in range(turns):
            events.extend(self.advance_rounds(ROUNDS_PER_TURN - self.clock.rounds % ROUNDS_PER_TURN))
            in_field = field if field is not None else self.mode is SessionMode.EXPLORING
            if in_field and not self.party.living_members():
                # The rest and wandering rules apply only to a living party: a span that kills the last
                # member, a rest that starves the party out, stops at the turn it
                # happened. Out of the field there is nothing to stop, and a revival
                # window measured in elapsed time has to keep elapsing.
                break
            if in_field and not resting:
                # The rest cadence is a dungeon rule ("must rest for one turn every
                # hour in the dungeon"). Time in town and on overland travel doesn't count toward it.
                self.turns_since_rest += 1
                events.extend(exploration.check_fatigue(self))
            if in_field:
                self.wandering_counter += 1
                if self.wandering_counter >= exploration.wandering_interval(self):
                    self.wandering_counter = 0
                    check_events, encountered = exploration.wandering_check(self, resting=hidden)
                    events.extend(check_events)
                    if encountered:
                        return events, True
        return events, False

    # ------------------------------------------------------------------ light queries

    def party_light(self) -> tuple[bool, bool]:
        """Return whether the party has light, and whether infravision works.

        Most of exploration needs light. Call this to decide whether to dim the screen or disable a
        search button. The engine calls it before it lets the party read, search, or see an
        encounter coming.

        The party has light when any living member has an active effect whose kind is in
        [`LIGHT_EFFECT_KINDS`][osrlib.crawl.session.LIGHT_EFFECT_KINDS]. An effect whose kind is in
        [`DARKNESS_EFFECT_KINDS`][osrlib.crawl.session.DARKNESS_EFFECT_KINDS], on any living member,
        puts that light out while it lasts, because the spell's printed radius covers a whole
        marching party. Some darkness stops infravision as well.

        Returns:
            A pair: whether the party is lit, and whether infravision works. Infravision is
                allowed unless a darkness effect blocks it. Whether a member has infravision at all
                is [`member_has_infravision`][osrlib.crawl.session.GameSession.member_has_infravision].

        Examples:
            ```python
            from osrlib.core.alignment import Alignment
            from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
            from osrlib.core.rng import RngStreams
            from osrlib.core.ruleset import Ruleset
            from osrlib.crawl.adventure import Adventure, TownSpec
            from osrlib.crawl.commands import EnterDungeon
            from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
            from osrlib.crawl.party import Party
            from osrlib.crawl.session import GameSession

            rules = Ruleset()
            stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
            hero = create_character(
                name="Hild",
                class_id="fighter",
                alignment=Alignment.LAWFUL,
                ruleset=rules,
                stream=stream,
            ).character
            level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
            crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
            adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))
            session = GameSession.new(Party(members=[hero]), adventure, seed=7)
            session.execute(EnterDungeon(dungeon_id="crypt"))

            # Nobody has lit a torch yet, so the party stands in the dark.
            lit, infravision_works = session.party_light()
            print(lit, infravision_works, session.member_has_infravision(hero))
            # False True False
            ```
        """
        living_ids = [member.id for member in self.party.living_members()]
        darkness = [
            effect
            for member_id in living_ids
            for effect in self.ledger.effects
            if effect.target_ref == member_id and effect.definition.kind in DARKNESS_EFFECT_KINDS
        ]
        if darkness:
            blocks = any(bool(effect.definition.params.get("blocks_infravision")) for effect in darkness)
            return False, not blocks
        lit = any(
            effect.target_ref in living_ids and effect.definition.kind in LIGHT_EFFECT_KINDS
            for effect in self.ledger.effects
        )
        return lit, True

    def bright_light(self) -> bool:
        """Return whether the party has light as bright as daylight.

        The wandering-monster chance goes up by 1 in 6 for a party that can be seen coming. osrlib treats the
        flame of a torch or lantern as the baseline for the printed chance, so only a
        light whose data gives its brightness as daylight counts here, which in the built-in catalog
        means *continual light*. See
        [the adaptations register](https://mmacy.github.io/osrlib-python/adaptations/), the page
        that lists where osrlib settles an ambiguous rule or supplies a default.

        Returns:
            True when a living member has a light effect whose brightness is daylight.
        """
        living_ids = {member.id for member in self.party.living_members()}
        return any(
            effect.target_ref in living_ids
            and effect.definition.kind in LIGHT_EFFECT_KINDS
            and effect.definition.params.get("brightness") == "daylight"
            for effect in self.ledger.effects
        )

    def member_has_infravision(self, member: Character) -> bool:
        """Return whether one member can see in the dark.

        Either the class has it, as the dwarf and the elf do, or an effect has granted it. The
        engine calls this when it decides whether a character can act in the dark, and when it sets
        the party's chance of being surprised.

        Args:
            member: The member to test.

        Returns:
            True when that member has infravision.
        """
        if any(ability.tag == "infravision" for ability in member.definition.abilities):
            return True
        return any(
            effect.target_ref == member.id and effect.definition.kind == "infravision" for effect in self.ledger.effects
        )

    # ------------------------------------------------------------------ the XP award

    def party_valuation_cp(self) -> int:
        """Return what the party's treasure is worth right now, in copper pieces.

        The value is in copper so that no rounding is lost along the way. The award converts it to
        gold once, at the end. The session measures the value when the party leaves town and again
        when it comes back, and the difference is the treasure experience.

        Every member counts, the dead included, because treasure carried out on a body still came
        home. Magic items and mundane gear count for nothing: magical treasure grants no experience,
        and selling off used gear is below the level of detail osrlib simulates.

        Returns:
            The coins, in copper, plus every valuable's listed value, converted to copper.

        Examples:
            ```python
            from osrlib.core.alignment import Alignment
            from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
            from osrlib.core.items import Coins
            from osrlib.core.rng import RngStreams
            from osrlib.core.ruleset import Ruleset
            from osrlib.crawl.adventure import Adventure, TownSpec
            from osrlib.crawl.commands import GrantCoins
            from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
            from osrlib.crawl.party import Party
            from osrlib.crawl.session import GameSession

            rules = Ruleset()
            stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
            hero = create_character(
                name="Hild",
                class_id="fighter",
                alignment=Alignment.LAWFUL,
                ruleset=rules,
                stream=stream,
            ).character
            level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
            crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
            adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))
            session = GameSession.new(Party(members=[hero]), adventure, seed=7)

            before = session.party_valuation_cp()
            session.execute(GrantCoins(character_id=hero.id, coins=Coins(gp=25, sp=5)))
            print(session.party_valuation_cp() - before)
            # 2550
            ```
        """
        total = 0
        for member in self.party.members:
            total += member.inventory.purse.value_cp
            total += sum(valuable.value_gp * 100 for valuable in member.inventory.valuables)
        return total

    def snapshot_treasure(self) -> None:
        """Record what the party's treasure is worth as it leaves town, for the return award.

        The session stores the value in
        [`treasure_snapshot_cp`][osrlib.crawl.session.GameSession.treasure_snapshot_cp].
        The [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] handler calls this method, so you
        don't have to. Call it yourself only when you start a delve some other way.
        """
        self.treasure_snapshot_cp = self.party_valuation_cp()

    def award_adventure_xp(self) -> list[Event]:
        """Pay the end-of-adventure experience award and return its events.

        [`TravelToTown`][osrlib.crawl.commands.TravelToTown] calls it under the default ruleset,
        where experience is awarded for making it back alive, so you don't call it yourself.

        The award is what the defeated creatures were worth, plus one experience point for each
        gold piece of treasure the party gained since it left town. A loss of treasure counts as
        zero. The total divides evenly among the survivors, rounded down, and each survivor gets
        one share. The dead count toward the treasure that came home and take no share, and a
        party that lost everyone is awarded nothing.

        Whatever happens, the method clears
        [`defeated_monsters`][osrlib.crawl.session.GameSession.defeated_monsters] and resets
        [`treasure_snapshot_cp`][osrlib.crawl.session.GameSession.treasure_snapshot_cp], so the next
        delve starts from zero.

        Returns:
            The [`AdventureXpAwardEvent`][osrlib.crawl.events.AdventureXpAwardEvent], then each
                survivor's award event and, where one gained a level, the level event. When the share
                rounds to zero, only the `AdventureXpAwardEvent`. An empty list when nobody survived or
                the total is zero.
        """
        from osrlib.crawl.events import AdventureXpAwardEvent

        survivors = self.party.living_members()
        events: list[Event] = []
        monster_xp = sum(record.xp for record in self.defeated_monsters)
        current = self.party_valuation_cp()
        baseline = self.treasure_snapshot_cp if self.treasure_snapshot_cp is not None else current
        treasure_xp = max(0, (current - baseline) // 100)
        self.defeated_monsters = []
        self.treasure_snapshot_cp = None
        if not survivors:
            return events
        total = monster_xp + treasure_xp
        if total <= 0:
            return events
        share = total // len(survivors)
        events.append(
            AdventureXpAwardEvent(
                monster_xp=monster_xp,
                treasure_xp=treasure_xp,
                share=share,
                survivors=tuple(_member_id(member) for member in survivors),
            )
        )
        if share > 0:
            for member in survivors:
                result = apply_xp(member, member.definition, share, self.streams.get(ADVANCEMENT_STREAM))
                events.extend(_xp_award_events(member, result))
        return events

    def award_immediate_xp(self, amount: int) -> list[Event]:
        """Divide one pool of experience among the survivors now, and return its events.

        Under a ruleset that awards experience immediately, the session calls this at the end of
        each encounter and each time the party takes treasure, so you don't call it. It
        divides the pool the way the return award does: evenly among the living, rounded down, with
        the remainder dropped. To award one character an amount, execute
        [`AwardXP`][osrlib.crawl.commands.AwardXP] instead, which the command log records and a replay
        repeats.

        Args:
            amount: The pool to divide. Zero or less awards nothing.

        Returns:
            Each survivor's award event and, where one gained a level, the level event. An empty
                list when nobody is alive or the share rounds to zero.
        """
        survivors = self.party.living_members()
        if not survivors or amount <= 0:
            return []
        share = amount // len(survivors)
        if share <= 0:
            return []
        events: list[Event] = []
        for member in survivors:
            result = apply_xp(member, member.definition, share, self.streams.get(ADVANCEMENT_STREAM))
            events.extend(_xp_award_events(member, result))
        return events

    # ------------------------------------------------------------------ death records

    def _pile_saved_items(self, events: Sequence[Event]) -> None:
        """Move a dead member's magic items that made their save to a drop pile on the party's cell.

        [`destroy_equipment`][osrlib.core.combat.destroy_equipment] leaves those items in the dead member's
        inventory. No command can reach them there, because a dead member can't give or drop anything. The
        session moves them to the pile in the same command that killed the member, so the party picks them up
        with [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure] and the feature id `"pile"`. This happens
        only in a dungeon. A monster's saved items stay where they are, because a slain NPC adventurer's whole
        inventory goes to the pile when the encounter ends.

        Args:
            events: The just-executed command's events, in order.
        """
        from osrlib.crawl import exploration
        from osrlib.crawl.dungeon import DropPile

        if self.dungeon_state.location.kind != "dungeon":
            return
        members = {member.id: member for member in self.party.members}
        for event in events:
            if not isinstance(event, EquipmentDestroyedEvent) or not event.saved_items:
                continue
            member = members.get(event.target_id)
            if member is None:
                continue
            pile = self.dungeon_state.piles.setdefault(exploration._cell_ref(self), DropPile())
            for instance_id in event.saved_items:
                instance = member.inventory.magic_item(instance_id)
                if instance is not None:
                    exploration._remove_instance(member, instance)
                    pile.magic_items.append(instance)

    def _record_deaths(self, events: Sequence[Event]) -> None:
        """Record party deaths with the clock round and the cause just resolved.

        The cause is `poison` when the killing resolution was a poison save (a failed
        death-category save immediately before the death) or a poison effect running its course,
        and otherwise the kind of the nearest preceding cause-bearing event. Only *neutralize
        poison* uses the difference between poison and other causes.

        Args:
            events: The just-executed command's events, in order. Monsters and NPC adventurers have
                ids that aren't members', so their deaths are never recorded.
        """
        member_ids = {member.id for member in self.party.members}
        cause = "unknown"
        for event in events:
            if isinstance(event, SavingThrowRolledEvent) and event.category == "death":
                if event.code == "combat.save.failed":
                    cause = "poison"
            elif isinstance(event, EffectExpiredEvent) and "poison" in event.kind:
                cause = "poison"
            elif isinstance(event, DamageDealtEvent):
                cause = "damage"
            if isinstance(event, DeathEvent) and event.target_id in member_ids:
                self.death_records[event.target_id] = DeathRecord(round=self.clock.rounds, cause=cause)

    def _felled_a_member(self, events: Sequence[Event]) -> bool:
        """Whether these events killed or petrified a party member, which is when the session checks for a wipe.

        Monsters and NPC adventurers have ids that aren't members', so their deaths and statues never count.
        """
        member_ids = {member.id for member in self.party.members}
        return any(
            (isinstance(event, DeathEvent) and event.target_id in member_ids)
            or (
                isinstance(event, ConditionGainedEvent)
                and event.condition == Condition.PETRIFIED.value
                and event.target_id in member_ids
            )
            for event in events
        )

    def _end_on_party_wipe(self) -> list[Event]:
        """End the session when the death or petrification just resolved left nobody standing.

        A member stands while they are neither dead nor petrified. A party turned wholly to stone
        has fallen, as a dead party has, though a statue still counts among
        [`Party.living_members`][osrlib.crawl.party.Party.living_members] for every other rule.
        This is the only path into `game_over`, whatever felled the party: a lost battle, a trap, a fall,
        starvation, a poison that finished the last member while the referee moved the clock, a
        basilisk's gaze, a cockatrice's beak. Any open encounter or battle clears, because a session
        that has ended has no live play state,
        and the session reports the ending as one
        [`GameOverEvent`][osrlib.crawl.events.GameOverEvent].

        This runs because of an event, not because of the party's state: it runs only for a command whose
        events killed or petrified a member, so carrying an already-fallen party to town, which is the first
        step of a revival, never re-enters game over. That holds for statues as it does for the dead,
        so a stone party placed in town stays in `town`, where *stone to flesh* from a magic-user who
        joins with [`AddMember`][osrlib.crawl.commands.AddMember] can bring it back. A session already
        in a terminal mode is left alone, so a party that dies after the adventure concluded stays in `victory`, and a
        later death in a session that has already ended changes nothing.

        Returns:
            The ending event, or nothing when a member still stands.
        """
        if self.mode.terminal or _anyone_standing(self.party):
            return []
        self.battle = None
        self.encounter = None
        self.mode = SessionMode.GAME_OVER
        return [GameOverEvent(reason="the party has fallen")]

    # ------------------------------------------------------------------ views

    def view(self, visibility: Visibility) -> PlayerView | RefereeView:
        """Return a snapshot of the session for one audience: the players or the referee.

        Draw from a view rather than from the session's attributes. The player view has a fixed
        list of fields, each one something a player may be shown, so a screen built on it can't
        reveal the map the party hasn't explored, the monsters' hit points, or the referee's rolls.
        The referee view contains the rest, for a referee screen, an LLM running the game, or a
        test. It has one typed field for each group of data the save contains, so you read
        `view.monsters[0].current_hp` and `view.flags["key"]` with the models this reference documents.

        In a networked game, keep the session and the referee view on the server, and send the
        client the player view or the player-visibility events. Neither view contains the master seed.
        Only the save contains it.

        Views are frozen and built fresh from the current state each time, never from the event
        log, so call this again after each command rather than keeping one.

        Args:
            visibility: `Visibility.PLAYER` for what a player may see, `Visibility.REFEREE` for
                everything except the master seed and the random streams' positions.

        Returns:
            A [`PlayerView`][osrlib.crawl.views.PlayerView] or a
                [`RefereeView`][osrlib.crawl.views.RefereeView], to match the level asked for.

        Examples:
            ```python
            from osrlib.core.alignment import Alignment
            from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
            from osrlib.core.events import Visibility
            from osrlib.core.rng import RngStreams
            from osrlib.core.ruleset import Ruleset
            from osrlib.crawl.adventure import Adventure, TownSpec
            from osrlib.crawl.commands import EnterDungeon
            from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
            from osrlib.crawl.party import Party
            from osrlib.crawl.session import GameSession

            rules = Ruleset()
            stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
            hero = create_character(
                name="Hild",
                class_id="fighter",
                alignment=Alignment.LAWFUL,
                ruleset=rules,
                stream=stream,
            ).character
            level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
            crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
            adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))
            session = GameSession.new(Party(members=[hero]), adventure, seed=7)
            session.execute(EnterDungeon(dungeon_id="crypt"))

            player = session.view(Visibility.PLAYER)
            referee = session.view(Visibility.REFEREE)
            print(player.mode, player.party[0].name)
            # exploring Hild

            # The referee sees the session flags; the player whitelist has no such field.
            print(referee.flags, "flags" in player.model_dump())
            # {} False

            # Neither view carries the master seed.
            print("master_seed" in referee.model_dump())
            # False
            ```
        """
        from osrlib.crawl.views import build_player_view, build_referee_view

        if visibility is Visibility.PLAYER:
            return build_player_view(self)
        return build_referee_view(self)


# ---------------------------------------------------------------------- referee handlers


def _handle_grant_item(session: GameSession, command: GrantItem) -> tuple[list[Rejection], list[Event]]:
    try:
        member = session.member(command.character_id)
    except ValueError:
        return [Rejection(code="session.command.unknown_member", params={"character": command.character_id})], []
    try:
        template = session.effective_equipment.get(command.item_id)
    except ValueError:
        template = None
    if template is not None:
        member.inventory.items.append(ItemInstance(template=template, quantity=command.quantity))
        item_ids = (command.item_id,) * command.quantity
    else:
        try:
            load_magic_items().get(command.item_id)
        except ValueError:
            return [Rejection(code="session.command.unknown_item", params={"item": command.item_id})], []
        from osrlib.crawl.exploration import _treasure_tier

        # A granted magic item is made exactly as a placed one is when its cache is
        # emptied: its rolls come off the treasure stream at the tier in force now.
        tier = _treasure_tier(session)
        granted = [
            instantiate_magic_item(
                command.item_id, tier=tier, stream=session.streams.get(TREASURE_STREAM), allocator=session.allocator
            )
            for _ in range(command.quantity)
        ]
        member.inventory.items.extend(granted)
        item_ids = tuple(instance.instance_id for instance in granted)
    return [], [ItemAcquiredEvent(character_id=_member_id(member), item_ids=item_ids, origin="grant")]


def _handle_grant_coins(session: GameSession, command: GrantCoins) -> tuple[list[Rejection], list[Event]]:
    try:
        member = session.member(command.character_id)
    except ValueError:
        return [Rejection(code="session.command.unknown_member", params={"character": command.character_id})], []
    purse = member.inventory.purse
    for denomination in ("pp", "gp", "ep", "sp", "cp"):
        setattr(purse, denomination, getattr(purse, denomination) + getattr(command.coins, denomination))
    events: list[Event] = [
        ItemAcquiredEvent(character_id=_member_id(member), coins_gp_value=command.coins.value_gp, origin="grant")
    ]
    return [], events


def _removal_rejection(member: Character, item_id: str, quantity: int) -> Rejection | None:
    """Return why a removal of `quantity` of `item_id` from the member would be refused, or `None`.

    The session's removal handler and the interpreter's carrier selector both call this, so the
    session never refuses a removal the selector expanded. An instance id belongs to one magic item
    or valuable, so its removal passes only with a quantity of 1. Any other id is a catalog id. The
    function counts the member's mundane units with that id, carried and equipped. It counts no
    magic items, so a removal by a magic item's catalog id is refused.
    """
    inventory = member.inventory
    named = inventory.magic_item(item_id) is not None or any(
        valuable.instance_id == item_id for valuable in inventory.valuables
    )
    if named:
        carried = quantity == 1
    else:
        units = sum(
            instance.quantity
            for instance in inventory.all_instances()
            if isinstance(instance, ItemInstance) and instance.template.id == item_id
        )
        carried = units >= quantity
    if carried:
        return None
    return Rejection(code="items.remove.not_carried", params={"character": _member_id(member), "item": item_id})


def _handle_remove_item(session: GameSession, command: RemoveItem) -> tuple[list[Rejection], list[Event]]:
    from osrlib.crawl import exploration

    try:
        member = session.member(command.character_id)
    except ValueError:
        return [Rejection(code="session.command.unknown_member", params={"character": command.character_id})], []
    rejection = _removal_rejection(member, command.item_id, command.quantity)
    if rejection is not None:
        return [rejection], []
    inventory = member.inventory
    events: list[Event] = []
    magic = inventory.magic_item(command.item_id)
    valuable = next((piece for piece in inventory.valuables if piece.instance_id == command.item_id), None)
    if magic is not None:
        # Removal releases the item's effects, as taking it off would. It skips the curse
        # check, because the referee takes the item and the bearer doesn't let go of it.
        events.extend(exploration._release_instance_effects(session, magic))
        exploration._remove_instance(member, magic)
        item_ids: tuple[str, ...] = (command.item_id,)
    elif valuable is not None:
        inventory.valuables.remove(valuable)
        item_ids = (command.item_id,)
    else:
        # Units come out of the pack before anything the member has in use, so a removal
        # takes the spare sword before the one in hand.
        remaining = command.quantity
        for instance in [*inventory.items, *inventory.equipped_instances()]:
            if remaining == 0:
                break
            if not isinstance(instance, ItemInstance) or instance.template.id != command.item_id:
                continue
            taken = min(remaining, instance.quantity)
            if taken == instance.quantity:
                exploration._remove_instance(member, instance)
            else:
                instance.quantity -= taken
            remaining -= taken
        item_ids = (command.item_id,) * command.quantity
    events.append(ItemsRemovedEvent(character_id=_member_id(member), item_ids=item_ids))
    return [], events


def _handle_remove_coins(session: GameSession, command: RemoveCoins) -> tuple[list[Rejection], list[Event]]:
    try:
        member = session.member(command.character_id)
    except ValueError:
        return [Rejection(code="session.command.unknown_member", params={"character": command.character_id})], []
    purse = member.inventory.purse
    denominations = ("pp", "gp", "ep", "sp", "cp")
    for denomination in denominations:
        if getattr(command.coins, denomination) > getattr(purse, denomination):
            # The purse pays in the coins named or not at all: no change is made from
            # another denomination.
            return [
                Rejection(
                    code="items.coins.insufficient",
                    params={"character": command.character_id, "coin": denomination},
                )
            ], []
    for denomination in denominations:
        setattr(purse, denomination, getattr(purse, denomination) - getattr(command.coins, denomination))
    return [], [ItemsRemovedEvent(character_id=_member_id(member), coins_gp_value=command.coins.value_gp)]


def _handle_add_member(session: GameSession, command: AddMember) -> tuple[list[Rejection], list[Event]]:
    character = command.character
    if character.id is not None:
        return [Rejection(code="party.add.id_assigned", params={"character": character.id})], []
    if character.conditions or character.stat_modifiers:
        return [Rejection(code="party.add.under_effects", params={"name": character.name})], []
    inventory = character.inventory
    carried = [
        *(instance.instance_id for instance in inventory.all_instances() if isinstance(instance, MagicItemInstance)),
        *(valuable.instance_id for valuable in inventory.valuables),
    ]
    if carried:
        return [Rejection(code="party.add.carries_instances", params={"name": character.name, "item": carried[0]})], []
    # The command keeps the character as it arrived, so the log replays the same addition.
    member = character.model_copy(deep=True)
    taken = {existing.id for existing in session.party.members}
    member.id = session.allocator.allocate("character")
    while member.id in taken:
        member.id = session.allocator.allocate("character")
    session.party.members.append(member)
    if session.treasure_snapshot_cp is not None:
        # Coins carried in from outside the dungeon aren't treasure the party found.
        session.treasure_snapshot_cp += member.inventory.purse.value_cp
    return [], [
        MemberAddedEvent(character_id=member.id, name=member.name, class_id=member.class_id, level=member.level)
    ]


def _handle_award_xp(session: GameSession, command: AwardXP) -> tuple[list[Rejection], list[Event]]:
    try:
        member = session.member(command.character_id)
    except ValueError:
        return [Rejection(code="session.command.unknown_member", params={"character": command.character_id})], []
    result = apply_xp(member, member.definition, command.amount, session.streams.get(ADVANCEMENT_STREAM))
    return [], _xp_award_events(member, result)


def _handle_set_flag(session: GameSession, command: SetFlag) -> tuple[list[Rejection], list[Event]]:
    session.flags[command.key] = command.value
    return [], [FlagSetEvent(key=command.key, value=command.value)]


def _handle_mark_trigger_fired(session: GameSession, command: MarkTriggerFired) -> tuple[list[Rejection], list[Event]]:
    # The fired marks record whether a trigger has fired, so a repeat mark adds
    # nothing. The event is emitted every time, because a repeatable trigger's every
    # firing is marked and the log is the record of each one.
    if command.trigger_id not in session.fired_triggers:
        session.fired_triggers.append(command.trigger_id)
    return [], [TriggerFiredEvent(trigger_id=command.trigger_id, narrative=command.narrative)]


def _handle_add_journal_entry(session: GameSession, command: AddJournalEntry) -> tuple[list[Rejection], list[Event]]:
    entry = JournalEntry(text=command.text, rounds=session.clock.rounds)
    session.journal.append(entry)
    return [], [JournalEntryAddedEvent(text=entry.text, rounds=entry.rounds)]


def _handle_record_note(session: GameSession, command: RecordNote) -> tuple[list[Rejection], list[Event]]:
    return [], [NoteRecordedEvent(text=command.text)]


# ---------------------------------------------------------------------- quest handlers
#
# Pure bookkeeping, all four: no draw, no clock, no interaction with the wipe check.
# Ids resolve against the adventure's quest specs and the state block seeded from
# them, and every guard is a rejection, so the accepted log contains only commands
# that were valid for the state they found, and a replay never gets a refusal.


def _quest_pair(session: GameSession, quest_id: str) -> tuple[QuestSpec, QuestState] | None:
    """Return the quest's authored spec and its live state, or `None` when no quest has that id."""
    try:
        spec = session.adventure.quest(quest_id)
    except ValueError:
        return None
    state = session.quests.get(quest_id)
    return None if state is None else (spec, state)


def _objective_pair(
    spec: QuestSpec, state: QuestState, objective_id: str
) -> tuple[ObjectiveSpec, ObjectiveState] | None:
    """Return the objective's authored spec and its live state, or `None` when the quest has none."""
    objective = next((entry for entry in spec.objectives if entry.id == objective_id), None)
    objective_state = state.objectives.get(objective_id)
    return None if objective is None or objective_state is None else (objective, objective_state)


def _unknown_quest(quest_id: str) -> tuple[list[Rejection], list[Event]]:
    """Return the rejection for a quest id the adventure doesn't have."""
    return [Rejection(code="session.command.unknown_quest", params={"quest": quest_id})], []


def _unknown_objective(quest_id: str, objective_id: str) -> tuple[list[Rejection], list[Event]]:
    """Return the rejection for an objective id the quest doesn't have, when the quest exists."""
    return [
        Rejection(code="session.command.unknown_objective", params={"quest": quest_id, "objective": objective_id})
    ], []


def _quest_state_refused(params: dict[str, int | str | tuple[int | str, ...]]) -> tuple[list[Rejection], list[Event]]:
    """Return the rejection for a lifecycle command that contradicts the quest's state, given in `params`."""
    return [Rejection(code="session.command.quest_state", params=params)], []


def _append_quest_beat(session: GameSession, text: str) -> None:
    """Append a quest's journal line to the journal, stamped with the current clock round.

    It builds the same [`JournalEntry`][osrlib.crawl.session.JournalEntry] that
    [`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry] builds, without a
    [`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent]. The quest's
    lifecycle event is the event for this line, and emitting both would show the players
    the same line twice. When the author didn't write the line, this appends nothing.
    """
    if text:
        session.journal.append(JournalEntry(text=text, rounds=session.clock.rounds))


def _handle_activate_quest(session: GameSession, command: ActivateQuest) -> tuple[list[Rejection], list[Event]]:
    pair = _quest_pair(session, command.quest_id)
    if pair is None:
        return _unknown_quest(command.quest_id)
    spec, state = pair
    if state.status != "inactive":
        return _quest_state_refused({"quest": command.quest_id, "state": state.status})
    state.status = "active"
    beat = spec.narrative.offer if spec.narrative is not None else ""
    _append_quest_beat(session, beat)
    return [], [QuestActivatedEvent(quest_id=spec.id, name=spec.name, narrative=beat or None)]


def _handle_reveal_objective(session: GameSession, command: RevealObjective) -> tuple[list[Rejection], list[Event]]:
    pair = _quest_pair(session, command.quest_id)
    if pair is None:
        return _unknown_quest(command.quest_id)
    spec, state = pair
    found = _objective_pair(spec, state, command.objective_id)
    if found is None:
        return _unknown_objective(command.quest_id, command.objective_id)
    objective, objective_state = found
    if state.status != "active":
        return _quest_state_refused({"quest": command.quest_id, "state": state.status})
    # A completed objective is a revealed one, so the refusal reports the more specific state first.
    if objective_state.complete or objective_state.revealed:
        return _quest_state_refused(
            {
                "quest": command.quest_id,
                "objective": command.objective_id,
                "state": "complete" if objective_state.complete else "revealed",
            }
        )
    objective_state.revealed = True
    beat = objective.narrative.offer if objective.narrative is not None else ""
    _append_quest_beat(session, beat)
    return [], [
        ObjectiveRevealedEvent(
            quest_id=spec.id,
            quest_name=spec.name,
            objective_id=objective.id,
            name=objective.name or objective.id,
            narrative=beat or None,
        )
    ]


def _handle_complete_objective(session: GameSession, command: CompleteObjective) -> tuple[list[Rejection], list[Event]]:
    pair = _quest_pair(session, command.quest_id)
    if pair is None:
        return _unknown_quest(command.quest_id)
    spec, state = pair
    found = _objective_pair(spec, state, command.objective_id)
    if found is None:
        return _unknown_objective(command.quest_id, command.objective_id)
    objective, objective_state = found
    if state.status != "active":
        return _quest_state_refused({"quest": command.quest_id, "state": state.status})
    if objective_state.complete:
        return _quest_state_refused({"quest": command.quest_id, "objective": command.objective_id, "state": "complete"})
    objective_state.complete = True
    # Completing an objective reveals it if it was hidden, with no separate reveal, so the player
    # view never shows a quest that finished an objective the players never saw.
    objective_state.revealed = True
    beat = objective.narrative.progress if objective.narrative is not None else ""
    _append_quest_beat(session, beat)
    return [], [
        ObjectiveCompletedEvent(
            quest_id=spec.id,
            quest_name=spec.name,
            objective_id=objective.id,
            name=objective.name or objective.id,
            narrative=beat or None,
        )
    ]


def _handle_complete_quest(session: GameSession, command: CompleteQuest) -> tuple[list[Rejection], list[Event]]:
    pair = _quest_pair(session, command.quest_id)
    if pair is None:
        return _unknown_quest(command.quest_id)
    spec, state = pair
    # The handler checks only that the quest is active. Whoever issues the command
    # decides whether the quest's completion rule is met, because ruling a quest done
    # is the referee's call.
    if state.status != "active":
        return _quest_state_refused({"quest": command.quest_id, "state": state.status})
    state.status = "completed"
    beat = spec.narrative.completion if spec.narrative is not None else ""
    _append_quest_beat(session, beat)
    events: list[Event] = [QuestCompletedEvent(quest_id=spec.id, name=spec.name, narrative=beat or None)]
    if spec.concludes_adventure and not session.mode.terminal:
        # This is the only path into victory. A concluded session has no live play state,
        # the same as after a party wipe. In a session that has already ended, the handler
        # advances the quest and leaves the mode unchanged.
        session.encounter = None
        session.battle = None
        session.mode = SessionMode.VICTORY
        events.append(AdventureCompletedEvent(quest_id=spec.id, name=spec.name, narrative=beat or None))
    return [], events


def _handle_spawn_monsters(session: GameSession, command: SpawnMonsters) -> tuple[list[Rejection], list[Event]]:
    from osrlib.core.dice import roll
    from osrlib.crawl import encounter as encounter_module

    try:
        session.effective_monsters.get(command.template_id)
    except ValueError:
        return [Rejection(code="session.command.unknown_monster", params={"template": command.template_id})], []
    if session.dungeon_state.location.kind != "dungeon":
        # Encounters live on the dungeon grid: the combat space, silence cells,
        # and formation widths all need a party cell to stand on.
        return [Rejection(code="session.command.not_in_dungeon")], []
    if session.encounter is not None and session.encounter.pursuit is not None:
        # A chase runs at the pace of its slowest group, so a group that joined it would
        # change who gets away.
        return [Rejection(code="encounter.spawn.mid_pursuit")], []
    if command.count_fixed is not None:
        count = command.count_fixed
    elif command.count_dice is not None:
        count = roll(command.count_dice, session.streams.get(ENCOUNTER_STREAM)).total
    else:  # unreachable: the command model requires exactly one of the two
        raise ValueError("SpawnMonsters carries neither count_dice nor count_fixed")
    count = max(1, count)
    instances = session.spawn(command.template_id, count)
    events: list[Event] = [
        MonstersSpawnedEvent(template_id=command.template_id, monster_ids=tuple(instance.id for instance in instances))
    ]
    if session.encounter is not None:
        # Monsters that arrive mid-fight, like a lycanthrope's summoned animals, join the
        # encounter or battle already open instead of opening a new one.
        events.extend(
            encounter_module._join_encounter(
                session, label=command.template_id, instances=instances, distance_feet=command.distance_feet
            )
        )
        return [], events
    events.extend(
        encounter_module.start_encounter(
            session,
            groups=[(command.template_id, instances)],
            kind="spawned",
            distance_feet=command.distance_feet,
        )
    )
    return [], events


def _handle_spawn_npc_party(session: GameSession, command: SpawnNpcParty) -> tuple[list[Rejection], list[Event]]:
    from osrlib.core.dice import roll
    from osrlib.crawl import encounter as encounter_module
    from osrlib.crawl import exploration
    from osrlib.data import load_encounter_tables

    if session.encounter is not None or session.battle is not None:
        return [Rejection(code="session.command.encounter_in_progress")], []
    if session.dungeon_state.location.kind != "dungeon":
        return [Rejection(code="session.command.not_in_dungeon")], []
    if command.count_fixed is not None:
        count = command.count_fixed
    else:
        count_dice = command.count_dice or next(
            composition.count_dice
            for composition in load_encounter_tables().npc_compositions
            if composition.kind == command.party_kind
        )
        count = max(1, roll(count_dice, session.streams.get(ENCOUNTER_STREAM)).total)
    party, bundle, npc_events = exploration._field_npc_party(session, command.party_kind, count)
    events: list[Event] = list(npc_events)
    label = "Basic Adventurers" if command.party_kind == "basic" else "Expert Adventurers"
    events.extend(
        encounter_module.start_encounter(
            session,
            groups=[(label, party.members)],
            kind="spawned",
            distance_feet=command.distance_feet,
        )
    )
    exploration._assign_carried(session, [({}, bundle)])
    return [], events


def _handle_identify_item(session: GameSession, command: IdentifyItem) -> tuple[list[Rejection], list[Event]]:
    from osrlib.crawl import exploration

    try:
        member = session.member(command.character_id)
    except ValueError:
        return [Rejection(code="session.command.unknown_member", params={"character": command.character_id})], []
    instance = member.inventory.magic_item(command.item_id)
    if instance is None:
        return [Rejection(code="session.command.unknown_item", params={"item": command.item_id})], []
    return [], exploration._identify_item_events(session, member, instance)


def _handle_set_door_state(session: GameSession, command: SetDoorState) -> tuple[list[Rejection], list[Event]]:
    try:
        dungeon = session.adventure.dungeon(command.dungeon_id)
        level = dungeon.level(command.level_number)
    except ValueError:
        return [Rejection(code="session.command.unknown_location", params={"dungeon": command.dungeon_id})], []
    from osrlib.crawl import exploration
    from osrlib.crawl.dungeon import EdgeKind

    edge = level.edge((command.x, command.y), command.direction)
    if edge.kind is not EdgeKind.DOOR:
        return [Rejection(code="session.command.no_door", params={"x": command.x, "y": command.y})], []
    if command.open is command.wedged is command.discovered is command.unlocked is None:
        # A write with nothing to write is legal and does nothing at all: it must
        # not leave an overlay entry behind for an untouched door.
        return [], []
    ref = edge_ref(command.dungeon_id, command.level_number, (command.x, command.y), command.direction)
    # The referee writes through the same seeded materializer the play handlers
    # use, so a first write to an authored-open door does not store it shut.
    state = exploration._store_door_state(session, edge, ref)
    events: list[Event] = []
    if command.open is not None and command.open != state.open:
        state.open = command.open
        code = "exploration.door.opened" if command.open else "exploration.door.closed"
        events.append(
            DoorEvent(
                code=code, x=command.x, y=command.y, direction=command.direction.value, visibility=Visibility.REFEREE
            )
        )
    if command.wedged is not None:
        state.wedged = command.wedged
    if command.discovered is not None:
        # A door the referee reveals is discovered the same way a search reveals it, so both
        # cells the edge joins get their room-trap searches back.
        secret = edge.door is not None and edge.door.kind == "secret"
        discovering = secret and command.discovered and not state.discovered
        state.discovered = command.discovered
        if discovering:
            exploration._refund_trap_search(
                session, command.dungeon_id, command.level_number, (command.x, command.y), command.direction
            )
    if command.unlocked is not None:
        state.unlocked = command.unlocked
    return [], events


def _handle_place_party(session: GameSession, command: PlaceParty) -> tuple[list[Rejection], list[Event]]:
    if session.encounter is not None or session.battle is not None:
        return [Rejection(code="session.command.encounter_in_progress")], []
    location = command.location
    events: list[Event] = []
    if location.kind == "dungeon":
        dungeon_id, level_number, position = location.dungeon_id, location.level_number, location.position
        if dungeon_id is None or level_number is None or position is None:
            # Unreachable: the location model validates that the dungeon fields are set together.
            raise ValueError("a dungeon location carries dungeon_id, level_number, and position")
        try:
            level = session.adventure.dungeon(dungeon_id).level(level_number)
        except ValueError:
            return [Rejection(code="session.command.unknown_location", params={"dungeon": dungeon_id})], []
        if not level.in_bounds(position):
            return [Rejection(code="session.command.out_of_bounds")], []
        session.dungeon_state.location = location
        session.dungeon_state.mark_explored(dungeon_id, level_number, position)
        session.mode = SessionMode.EXPLORING
        events.append(
            LocationEnteredEvent(
                location_kind="dungeon", location_id=dungeon_id, level_number=level_number, via="placed"
            )
        )
    else:
        session.dungeon_state.location = location
        session.mode = SessionMode.TOWN
        events.append(LocationEnteredEvent(location_kind="town", location_id="town"))
    return [], events


def _handle_advance_time(session: GameSession, command: AdvanceTime) -> tuple[list[Rejection], list[Event]]:
    if command.unit is TimeUnit.ROUND:
        events = session.advance_rounds(command.n)
    else:
        turns = command.n * (1 if command.unit is TimeUnit.TURN else 144)
        # Referee time passes with full bookkeeping but no wandering-monster checks,
        # because the referee decides what walks in.
        events, _ = session.advance_turns(turns, field=False)
    events.append(TimeAdvancedEvent(n=command.n, unit=command.unit.value, rounds_total=session.clock.rounds))
    return [], events


def _handle_roll_dice(session: GameSession, command: RollDice) -> tuple[list[Rejection], list[Event]]:
    from osrlib.core.dice import roll

    # The command's field validator already guaranteed the expression parses, so the
    # draw happens unconditionally here: validation is the pure pre-phase, the roll is
    # the only side effect, and it draws from a separate stream so the rules' draws are unchanged.
    result = roll(command.expression, session.streams.get(ADJUDICATION_STREAM))
    return [], [DiceRolledEvent(expression=command.expression, total=result.total, rolls=result.rolls)]


def _handle_end_encounter(session: GameSession, command: EndEncounter) -> tuple[list[Rejection], list[Event]]:
    from osrlib.crawl import encounter as encounter_module

    state = session.encounter
    if state is None:  # unreachable: the mode gate admits only encounter and battle, which have one open
        raise ValueError("EndEncounter reached the handler with no open encounter")
    area_ref = state.area_ref
    # The battle closes first, as it does when the session ends the encounter of a won
    # battle. The encounter's closing event reports the end of both.
    session.battle = None
    events = encounter_module.end_encounter(session, command.outcome)
    if command.resolve_area and area_ref is not None and area_ref not in session.dungeon_state.resolved_encounters:
        session.dungeon_state.resolved_encounters.append(area_ref)
    return [], events


_REFEREE_HANDLERS = {
    GrantItem: _handle_grant_item,
    GrantCoins: _handle_grant_coins,
    AwardXP: _handle_award_xp,
    SetFlag: _handle_set_flag,
    MarkTriggerFired: _handle_mark_trigger_fired,
    AddJournalEntry: _handle_add_journal_entry,
    RecordNote: _handle_record_note,
    SpawnMonsters: _handle_spawn_monsters,
    SpawnNpcParty: _handle_spawn_npc_party,
    SetDoorState: _handle_set_door_state,
    IdentifyItem: _handle_identify_item,
    PlaceParty: _handle_place_party,
    AdvanceTime: _handle_advance_time,
    RollDice: _handle_roll_dice,
    ActivateQuest: _handle_activate_quest,
    RevealObjective: _handle_reveal_objective,
    CompleteObjective: _handle_complete_objective,
    CompleteQuest: _handle_complete_quest,
    EndEncounter: _handle_end_encounter,
    RemoveItem: _handle_remove_item,
    RemoveCoins: _handle_remove_coins,
    AddMember: _handle_add_member,
}
