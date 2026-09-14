"""The running game: `GameSession`, the one object a front end drives.

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
Draw your screens from [`GameSession.view`][osrlib.crawl.session.GameSession.view]
rather than from the session's own attributes, and save the game with
[`save_game`][osrlib.persistence.save_game].

The session keeps what the rules engine underneath leaves to its caller: the
seeded random streams, the id allocator, the effects ledger, the clock, the registry
of characters and live monsters, the flag store, the trigger marks, the journal, the
quest states, the listeners and their state, the command and event logs, the session
mode, and the dungeon state. That is why a save is one object and a replay from the
same seed reaches the same game.

Which commands the session will accept depends on its
[`SessionMode`][osrlib.crawl.commands.SessionMode]: `town` between delves,
`exploring` on a dungeon grid, `encounter` when something has been met, `battle`
once blows are struck, and the two endings, `game_over` and `victory`. A command
that doesn't belong to the current mode is refused with
`session.command.wrong_mode`, and each command class documents the modes it's legal
in.

To extend the game without changing the engine, register a listener (see
[`Listener`][osrlib.crawl.session.Listener]) and use session flags. A listener sees
each command's events and reacts by issuing ordinary commands, so everything it does
is logged and replayed like anything else.

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
# and osrlib.crawl.battle; each handler is one function (session, command) ->
# (rejections, events) that validates before it does anything: no draw, no mutation,
# no time until the last rejection check has passed. The session's own referee and
# town handlers are at the bottom of this module.

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from osrlib.core.alignment import Alignment
from osrlib.core.character import ADVANCEMENT_STREAM, Character
from osrlib.core.classes import XpAwardResult, apply_xp, level_title
from osrlib.core.clock import ROUNDS_PER_DAY, ROUNDS_PER_TURN, GameClock, TimeUnit
from osrlib.core.effects import EFFECTS_STREAM, EffectsLedger
from osrlib.core.events import (
    DamageDealtEvent,
    DeathEvent,
    EffectExpiredEvent,
    Event,
    SavingThrowRolledEvent,
    Visibility,
)
from osrlib.core.items import EquipmentCatalog, ItemInstance
from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, MonsterCatalog, MonsterInstance, spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.validation import Rejection
from osrlib.crawl.adventure import Adventure, _effective_equipment, _effective_monsters, validate_adventure
from osrlib.crawl.commands import (
    ActivateQuest,
    AddJournalEntry,
    AdvanceTime,
    AwardXP,
    Command,
    CommandResult,
    CompleteObjective,
    CompleteQuest,
    GrantCoins,
    GrantItem,
    IdentifyItem,
    MarkTriggerFired,
    PlaceParty,
    RecordNote,
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
    JournalEntryAddedEvent,
    LightEvent,
    LocationEnteredEvent,
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
from osrlib.crawl.party import Party
from osrlib.crawl.quests import ObjectiveSpec, QuestSpec
from osrlib.data import load_equipment, load_monsters
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

WANDERING_STREAM = "wandering"
"""The name of the stream the wandering-monster procedure draws from.

Pass it to [`RngStreams.get`][osrlib.core.rng.RngStreams.get] on a session's `streams` to get the
same generator the engine uses for the check die, the encounter-table roll, monster counts, and
variant picks. Every draw in osrlib comes from a named stream so that adding a roll in one
procedure cannot shift the dice another procedure would have drawn. You rarely need this yourself:
the engine draws from it while it runs the cadence.
"""

ENCOUNTER_STREAM = "encounter"
"""The name of the stream the encounter procedure draws from.

Covers surprise, encounter distance, reaction rolls, and the distraction check during a chase. See
[`WANDERING_STREAM`][osrlib.crawl.session.WANDERING_STREAM] for how stream names are used.
"""

EXPLORATION_STREAM = "exploration"
"""The name of the stream the exploration procedures draw from.

Covers forcing doors, listening, searching, trap springs, lighting a tinder box, and thief skill
checks. See [`WANDERING_STREAM`][osrlib.crawl.session.WANDERING_STREAM] for how stream names are
used.
"""

MONSTER_ACTION_STREAM = "monster_action"
"""The name of the stream a monster action policy draws from.

It's kept apart from the combat stream so that changing how monsters choose their actions, or
registering a policy of your own, never shifts the dice a fight would have rolled.
"""

ADJUDICATION_STREAM = "adjudication"
"""The name of the stream a referee's own dice roll draws from.

[`RollDice`][osrlib.crawl.commands.RollDice] uses it. It's kept off the streams the rules use, so
a roll for weather or a rumour never shifts a later attack or save.
"""

LIGHT_EFFECT_KINDS = frozenset({"light", "continual_light"})
"""The effect kinds that count as the party carrying light: a torch or lantern, and the light
spells.

[`GameSession.party_light`][osrlib.crawl.session.GameSession.party_light] tests an effect's kind
against this set. Read it when you are writing content that attaches a light of its own and you
want the engine to treat it as light.
"""

DARKNESS_EFFECT_KINDS = frozenset({"darkness", "continual_darkness"})
"""The effect kinds that put a party's light out while they run.

A darkness effect on any living member suppresses the party's light entirely, because the printed
radius of the spell swallows a marching party. Some of them block infravision too.
"""


class DeathRecord(BaseModel):
    """When and how one character died, kept for the spells that care.

    The session writes one per dead party member into
    `GameSession.death_records`, keyed by character id, as soon as the death
    happens. Revival reads it: *neutralize poison* has a window measured in rounds
    and needs to know whether poison was the killer, and *raise dead* counts the
    days since.

    The record is frozen, and a member who dies again gets a new one.
    """

    model_config = ConfigDict(frozen=True)

    round: int
    """Where the clock stood at the death, in rounds since the session began. Both revival windows
    are measured from here."""
    cause: str
    """What did it: `"poison"` when the killing blow was a failed poison save or a poison running
    its course, otherwise the kind of the resolution that killed them, like `"damage"`. Only
    the poison and non-poison distinction changes what the rules allow."""


class JournalEntry(BaseModel):
    """One beat of the adventure's story, with the moment it landed.

    The journal is the party's own record, and
    [`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry] and the quest
    lifecycle commands are what write it. Read the whole journal from a player
    view, where it appears as a tuple of these in the order they were written.

    Entries are appended and never rewritten, and each one is stamped as it is
    written, because that is the only moment the time can be captured: a front end
    renders "when" from the view alone, and a save whose event log was left out still
    says when every beat landed.
    """

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1)
    """The beat as it was written. It's content the game or the adventure supplied, never prose
    the engine wrote, and it's never empty."""
    rounds: int = Field(ge=0)
    """Where the clock stood when the entry was appended, in rounds since the session began."""


class ObjectiveState(BaseModel):
    """One objective's live state: whether the party can see it, and whether it's done.

    The session seeds one per authored objective and keeps them in
    [`QuestState.objectives`][osrlib.crawl.session.QuestState]. A player view shows
    the revealed objectives of active quests, and the referee view shows them all.

    Both flags only ever go one way, from hidden to revealed and from incomplete to
    complete, because the quest vocabulary has no word for undoing either.
    Completing an objective reveals it too, so an objective the party finished before
    anyone announced it is something they can now be told about.
    """

    model_config = ConfigDict(validate_assignment=True)

    revealed: bool
    """Whether the party may be shown this objective. It starts true unless the adventure marked
    the objective hidden, and [`RevealObjective`][osrlib.crawl.commands.RevealObjective] turns it
    on."""
    complete: bool
    """Whether the objective is done. [`CompleteObjective`][osrlib.crawl.commands.CompleteObjective]
    turns it on, and turns `revealed` on with it."""


class QuestState(BaseModel):
    """One quest's live state: where it stands, and where each of its objectives stands.

    The session builds one per quest the adventure authored and keeps them in
    `GameSession.quests`, keyed by quest id. The four quest commands are their only
    writers, so a replay of the command log rebuilds them exactly.

    A quest whose author wrote no activation clause starts `active`, because it's a
    standing charge and there's no command channel before the first command. The
    rest wait for [`ActivateQuest`][osrlib.crawl.commands.ActivateQuest].
    """

    model_config = ConfigDict(validate_assignment=True)

    status: Literal["inactive", "active", "completed"]
    """Where the quest stands. It runs `inactive` to `active` to `completed` and never goes
    back."""
    objectives: dict[str, ObjectiveState]
    """The state of each objective, keyed by objective id, in the order
    [`QuestSpec.objectives`][osrlib.crawl.quests.QuestSpec] authored them, so a walk over them is
    the same every run."""


class DefeatedMonsterRecord(BaseModel):
    """One defeated creature, kept until the experience award is paid.

    The encounter's conclusion appends one per defeated creature to
    `GameSession.defeated_monsters`, and
    [`GameSession.award_adventure_xp`][osrlib.crawl.session.GameSession.award_adventure_xp]
    adds up their `xp` and clears the list. Under a ruleset that awards immediately,
    the list is cleared at each encounter's end instead.

    Its fields are the same facts
    [`MonsterDefeatedEvent`][osrlib.crawl.events.MonsterDefeatedEvent] reports.
    """

    model_config = ConfigDict(frozen=True)

    monster_id: str
    """The session id of the creature that was defeated."""
    template_id: str
    """What it was: a monster catalog id, or `"npc:<class id>"` for an NPC adventurer."""
    outcome: str
    """How it went out: `"slain"`, `"routed"`, or `"surrendered"`. All three count as defeated for
    the award."""
    xp: int
    """What it's worth in experience."""


class DeprivationState(BaseModel):
    """How long one member has gone without food and without water.

    The session keeps one per member in `GameSession.deprivation`, and the day
    boundary updates it: a day with the supply resets that track to zero, a day
    without it adds one. Whether the count brings a penalty depends on the ruleset
    option `deprivation_penalties`, described in
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
        """Return the worse of the two counts, which is the one the schedule reads.

        The tracks don't stack: going without both food and water is as bad as going without the
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

    Every `apply_xp` call site reports through here, so the ordering, the level event immediately
    after the same member's award event, holds everywhere without each surface repeating it.
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
    """The extension point: an object a game registers to react to what happens.

    Write a class with a `key` and a `handle` method, and register an instance with
    [`GameSession.register_listener`][osrlib.crawl.session.GameSession.register_listener].
    After every accepted command, each listener is handed that command's events and
    its own state, in registration order. This is how a game adds behavior of its own
    (an authored trap that teleports, a curse that speaks up, a score) without
    touching the engine.

    A listener never mutates game state directly. It reacts by executing ordinary
    commands on the session, which keeps everything it does inside the command log,
    so a replay from the seed produces the same game. Because those nested commands
    log their own events, a listener that reacts that way returns no events of its
    own: returning them too would put them in the log twice. The list it returns is
    for events it authors itself, which nothing else would have logged.

    Every listener sees every event exactly once. A nested command runs the whole
    listener loop itself, so the events it produced reach each listener there and are
    not handed round again at the outer level.

    Listener state is snapshotted into saves under `key` and handed back on the next
    call, so a listener needs no storage of its own. Listeners themselves are code and
    aren't saved, so register them again after
    [`load_game`][osrlib.persistence.load_game].

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
            events: The command's events so far, in order, including the ones listeners registered
                before this one authored. Treat it as read-only.
            state: This listener's state as it was left last time, and an empty dict on the first
                call. It must be JSON-serializable, because it goes into saves.

        Returns:
            The events this listener authored itself, which the session appends to the result and
            the log, and the state to keep. Return an empty list when the listener reacted by
            executing commands: their events are already logged.
        """
        ...


class GameSession:
    """A game in progress: the one object you execute commands against and read state from.

    Make one with [`GameSession.new`][osrlib.crawl.session.GameSession.new], or get
    one back from [`load_game`][osrlib.persistence.load_game] or
    [`replay_game`][osrlib.persistence.replay_game]. Then run the loop: build a
    command, hand it to [`execute`][osrlib.crawl.session.GameSession.execute], render
    the result, and draw from
    [`view`][osrlib.crawl.session.GameSession.view] rather than from the attributes
    below, because a view is the projection that knows what a player may see. Extend
    the game with [`register_listener`][osrlib.crawl.session.GameSession.register_listener]
    and session flags.

    Everything that happened is on `event_log` and every accepted command on
    `command_log`, so [`save_game`][osrlib.persistence.save_game] and `load_game`
    round-trip a session, and replaying the log from the same seed reaches the same
    game.

    The attributes are public because a referee front end and the persistence layer
    read them, and they are documented for that reader. Writing to them yourself puts
    the session out of step with its own logs, and a replay won't match it.
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
        """Build a session from parts that are already in hand.

        This constructor does no validation of the adventure's references and assigns no character
        ids. Call [`GameSession.new`][osrlib.crawl.session.GameSession.new] to start a game, or
        [`load_game`][osrlib.persistence.load_game] to restore one. Both come through here.

        Args:
            party: The party, in marching order, with ids already assigned.
            adventure: The adventure content.
            ruleset: The ruleset in play.
            streams: The seeded random streams.
            master_seed: The seed those streams came from, kept so a save can rebuild them.

        Raises:
            ContentValidationError: If the adventure bundles monster or item ids that collide with
                the shipped catalogs or with each other. This is the check that still runs for
                `load_game`, which trusts the rest of a saved adventure.
        """
        self.party = party
        """The party, in marching order. Order decides who is in the front rank in a fight, and
        [`ReorderParty`][osrlib.crawl.commands.ReorderParty] is the only command that changes
        it."""
        self.adventure = adventure
        """The adventure being played: its town, dungeons, quests, and any content it bundles. It
        is frozen, and a save contains a copy of it, so a saved game needs no other file to
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
        """The ruleset in play: the options that decide the readings osrlib leaves open, like
        when experience is awarded. See
        [the adaptations register](https://mmacy.github.io/osrlib-python/adaptations/), the page
        that lists where osrlib settles an ambiguous rule or supplies a default."""
        self.streams = streams
        """The session's named random streams. Everything the engine rolls comes from one of them,
        and a save exports their positions, which is what makes a restored game continue the same
        way."""
        self.master_seed = master_seed
        """The seed the streams were built from. It's in the save and in no view, because knowing
        it would let a player predict every roll to come."""
        self.allocator = IdAllocator()
        """The source of session ids. Characters, monsters, effects, and generated caches are
        numbered from here as `<kind>-NNNN`, in order, never as random ids, so two runs of the
        same commands name things identically."""
        self.ledger = EffectsLedger()
        """The live effects: spells running, conditions, a torch burning down. The clock advances
        it, and its expiries and ticks arrive as kernel events."""
        self.clock = GameClock()
        """The game clock. `clock.rounds` is how much time has passed since the session began, and
        the turn and day boundaries it crosses drive the rest, wandering, and provision
        cadences."""
        self.mode = SessionMode.TOWN
        """Which [`SessionMode`][osrlib.crawl.commands.SessionMode] the session is in, and so which
        commands it will accept. A new session starts in `town`."""
        self.dungeon_state = DungeonState()
        """Everything the play has written over the authored map: where the party is, which cells
        it has walked and seen, door state, found and sprung traps, drop piles, and generated
        caches. The authored dungeon itself never changes."""
        self.monsters: dict[str, MonsterInstance] = {}
        """The live monsters, keyed by session id. Spawning adds to it, and nothing removes a
        defeated monster, so a later event can still name what it was."""
        self.npcs: dict[str, Character] = {}
        """The live NPC adventurers, keyed by session id. They are characters rather than monsters,
        and they fight with the party's own rules."""
        self.flags: dict[str, str | int | bool] = {}
        """The session flag store: the game's own memory, written by
        [`SetFlag`][osrlib.crawl.commands.SetFlag] and read by an adventure's gates and triggers.
        Keys and meanings are yours to choose."""
        self.fired_triggers: list[str] = []
        """The ids of the triggers that have fired, in the order they first fired. It answers
        "has this fired before". The log is where each firing is recorded."""
        self.journal: list[JournalEntry] = []
        """The journal beats, in the order they were written. A player view contains the same
        list, which is where a front end should read it from."""
        # One state block per authored quest, in document order, seeded here so that
        # every path that builds a session, new or load or replay, starts from the
        # same block. A quest with no activation clause is active from round 0
        # because there is no command channel before the first command; the rest wait
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
        """The live state of every quest the adventure authored, keyed by quest id, in the order it
        authored them. See [`QuestState`][osrlib.crawl.session.QuestState]."""
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
        for, which it keeps rather than dropping."""
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
        """How much of the current turn the party's steps have used up, in thirds of its movement
        rate. A full turn's worth advances the clock and resets this."""
        self.turns_since_rest = 0
        """Turns since the party last rested, which is what the fatigue cadence counts. A rest
        resets it."""
        self.wandering_counter = 0
        """Turns since the last wandering check. Reaching the level's interval fires the check and
        resets this."""
        self.noise_since_check = False
        """Whether the party has made noise since the last wandering check, which any attempt to
        force a door does, whether or not the door opens. Noise raises the next check's chance by
        one and then clears. A failed attempt also alerts the area beyond the door, which is what
        denies the party surprise there."""
        self.sleep_count = 0
        """How many nights or days the party has slept through. Preparing spells needs a sleep the
        caster hasn't already prepared from."""
        self.last_prepared_sleep: dict[str, int] = {}
        """The `sleep_count` at which each caster last prepared spells, keyed by character id. It
        is what enforces one preparation per sleep."""
        self.alerted_areas: list[str] = []
        """The keyed areas whose occupants have been alerted, as area references. Monsters that
        heard the party coming aren't surprised when it walks in."""
        self.heard_areas: list[str] = []
        """The keyed areas the party has heard something in, as area references. A party that knows
        what is behind the door isn't surprised by it."""
        self.encounter: EncounterState | None = None
        """The encounter under way, or `None`. It contains the groups, their distances, the
        stance, and any chase in progress."""
        self.battle: BattleState | None = None
        """The battle under way, or `None`. It contains the round number and the per-battle
        trackers."""
        self._provisions_day = 0
        # Runtime extension points a game re-registers like listeners. Policies are
        # code, so they are never serialized.
        self.action_policies: dict[str, object] = {}
        """Action policies for monster groups, keyed by encounter group id, for a game that wants
        to choose a group's actions itself. Without an entry, a group uses the built-in policy for
        its kind. Policies are code, so they aren't saved: register them again after a load."""

    @classmethod
    def new(cls, party: Party, adventure: Adventure, *, seed: int, ruleset: Ruleset | None = None) -> GameSession:
        """Start a new game: validate the adventure, assign character ids, and open in town.

        This is where a front end begins. Build characters with
        [`create_character`][osrlib.core.character.create_character], put them in a
        [`Party`][osrlib.crawl.party.Party] in marching order, load or build an
        [`Adventure`][osrlib.crawl.adventure.Adventure], and call this. The session comes back in
        `town`, at round 0, ready for the first
        [`execute`][osrlib.crawl.session.GameSession.execute]. To continue an existing game, use
        [`load_game`][osrlib.persistence.load_game] instead.

        The adventure is checked here rather than later, so a dangling monster id or a transition
        to a level that doesn't exist is an error at the start rather than a surprise mid-delve.

        The same seed and the same commands produce the same game, which is what makes a bug
        reproducible and a replay possible. Use a fresh seed per game, and record it.

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
            ContentValidationError: If the adventure refers to something that doesn't exist, such
                as an unknown monster or item id or a transition with no destination.

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
    def metadata(self) -> dict[str, object]:
        """Return the versions a client needs to know it's talking to a compatible engine.

        Send it in a handshake, or show it on a debug screen. A save contains the same two stamps,
        and [`replay_game`][osrlib.persistence.replay_game] refuses a log that a different engine
        version recorded.

        Returns:
            A dict with `schema_version`, the serialized-format version that saves, commands, and
            events share, and `engine_version`, the installed library's version.
        """
        return {"schema_version": SCHEMA_VERSION, "engine_version": engine_version()}

    @property
    def effective_monsters(self) -> MonsterCatalog:
        """Return the monster catalog this session resolves template ids against.

        It's the shipped catalog plus whatever monsters the adventure bundles. Every part of the
        engine that turns a template id into a creature reads it: spawning, keyed encounters,
        wandering rows, listen checks. Use it when you want to look a template up the way the
        session does, rather than calling [`load_monsters`][osrlib.data.load_monsters] and missing
        the adventure's own.

        Returns:
            The catalog. For an adventure that bundles nothing, it's the shipped catalog itself.
        """
        return self._monster_catalog

    @property
    def effective_equipment(self) -> EquipmentCatalog:
        """Return the equipment catalog this session resolves item ids against.

        It's the shipped catalog plus whatever items the adventure bundles, and every part of the
        engine that turns an item id into an item reads it: treasure caches,
        [`GrantItem`][osrlib.crawl.commands.GrantItem], picking a drop pile back up. The town shop
        is the exception: it sells from the shipped equipment lists, so a bundled item is never on
        the shelf.

        Returns:
            The catalog. For an adventure that bundles nothing, it's the shipped catalog itself.
        """
        return self._equipment_catalog

    # ------------------------------------------------------------------ dispatch

    def execute(self, command: Command) -> CommandResult:
        """Execute one command and return everything it caused.

        This is the loop a front end runs: build a command from
        [`osrlib.crawl.commands`][osrlib.crawl.commands], pass it here, check `accepted`, and
        render either the rejections or the events. Nothing else advances the game, and nothing
        else is logged, so a game built on this method can always be replayed.

        Validation runs first and changes nothing: a refused command draws no dice, spends no game
        time, mutates no state, and stays out of the command log. Treat a rejection as the fiction
        saying no rather than as an error, and show it to the player in your own words from its
        code and fields.

        An accepted command applies, and then its own bookkeeping runs before anything reaches the
        log: a party member's death is recorded with what killed them, and a command whose events
        left nobody standing ends the session in `game_over` with a
        [`GameOverEvent`][osrlib.crawl.events.GameOverEvent] closing its result. A session that has
        already ended is left where it is.

        The result contains the whole chain in log order: the handler's own events, then, for each
        registered listener in turn, the events of the commands that listener executed, however
        deeply nested, followed by the events it authored itself. So one result is enough to
        render the full reaction, and you don't have to read `event_log` to catch the rest.

        Args:
            command: The command to execute.

        Returns:
            The result envelope. A refused command contains rejections and no events. An accepted
            one contains events and no rejections.

        Raises:
            ValueError: If the command class has no handler, which means it was defined outside
                osrlib rather than built from
                [`osrlib.crawl.commands`][osrlib.crawl.commands].

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
        handler = _handlers().get(type(command))
        if handler is None:
            raise ValueError(f"no handler for command type {command.command_type!r}")
        rejections, events = handler(self, command)
        if rejections:
            return CommandResult(accepted=False, rejections=tuple(rejections))
        self.command_log.append(command)
        if self._record_deaths(events):
            events.extend(self._end_on_party_wipe())
        self.event_log.extend(events)
        # Two lists with two jobs. `accumulated` is what the listeners are dispatched
        # over: this command's own events plus what earlier listeners authored. A
        # listener's nested commands ran the whole listener loop themselves, so every
        # listener has already seen those events at the nested level; putting them in
        # here would deliver them to later listeners a second time. `envelope` is what
        # the caller gets back, and it does take them, because a front end reads the
        # result once and wants the whole chain.
        accumulated = list(events)
        envelope = list(events)
        self._persist_sight()
        for listener in self.listeners:
            mark = len(self.event_log)
            emitted, state = listener.handle(tuple(accumulated), self.listener_state.get(listener.key, {}))
            self.listener_state[listener.key] = state
            # Everything the listener's own commands logged while it ran, their
            # events and any deeper listener reactions, each already in the log
            # exactly once and in log order, then the events it authored itself.
            # (The log holds serialized entries only for a session restored from a
            # save; nothing executing appends one.)
            envelope.extend(entry for entry in self.event_log[mark:] if isinstance(entry, Event))
            envelope.extend(emitted)
            accumulated.extend(emitted)
            self.event_log.extend(emitted)
        return CommandResult(accepted=True, events=tuple(envelope))

    def _persist_sight(self) -> None:
        """Fold what the party's light shows right now into the map it remembers.

        Runs after every accepted command, the one place that covers every action which can change
        what the party can see (entering, moving, stairs, doors, lighting, placement, discovery,
        relocation after a fight), and calls
        [`mark_seen`][osrlib.crawl.dungeon.DungeonState.mark_seen] with the cells `_light_reveal`
        shows from where the party stands. A refused command changes nothing, so it never gets
        here.

        It runs before the listeners so that the map a live session remembers is the map a replay
        rebuilds. A listener that moves the party, an authored teleport for instance, executes its own
        command, which folds in that destination. Folding this command's view afterwards would
        record the destination over the move the party actually made, while a replay, running the
        same commands with no listeners, folds both in order.
        """
        from osrlib.crawl.exploration import _light_reveal

        key, cells = _light_reveal(self)
        if key is None or not cells:
            return
        dungeon_id, level_text = key.rsplit(":", 1)
        self.dungeon_state.mark_seen(dungeon_id, int(level_text), cells)

    def register_listener(self, listener: Listener) -> None:
        """Register a listener, which then runs after every accepted command.

        Listeners run in the order they were registered, after the command's own handler. This is
        how a game adds behavior without changing the engine. See
        [`Listener`][osrlib.crawl.session.Listener] for what one looks like and what it may do.

        Register them again after [`load_game`][osrlib.persistence.load_game] or
        [`replay_game`][osrlib.persistence.replay_game]: a listener is code and isn't saved,
        though its state is, and comes back under its key. A replay runs with no listeners
        registered, since the commands they issued are already in the log.

        Args:
            listener: The listener to register. Its state is snapshotted into saves under its
                `key`, so use a key that stays the same across releases of your game.
        """
        self.listeners.append(listener)
        self.listener_state.setdefault(listener.key, {})

    # ------------------------------------------------------------------ registry

    def registry(self) -> dict[str, Any]:
        """Return every live entity in the session, keyed by id.

        Party members come first in marching order, then monsters, then NPC adventurers. The
        engine hands this to the rules resolutions that need to look a target up by id. Use it
        when you are resolving something yourself. For anything you are drawing, read a view
        instead.

        Returns:
            A fresh dict from entity id to the live object: [`Character`][osrlib.core.character.Character]
            for members and NPCs, [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] for
            monsters. Editing the dict doesn't change the session. Editing the objects in it
            does.
        """
        entities: dict[str, Any] = {_member_id(member): member for member in self.party.members}
        entities.update(self.monsters)
        entities.update(self.npcs)
        return entities

    def combatant(self, combatant_id: str) -> object | None:
        """Return the monster or NPC adventurer with this id, or `None`.

        An [`EncounterGroup`][osrlib.crawl.encounter.EncounterGroup] holds ids that can be either,
        and this resolves both without you having to know which. For a party member, call
        [`member`][osrlib.crawl.session.GameSession.member]. For everything at once, call
        [`registry`][osrlib.crawl.session.GameSession.registry].

        Args:
            combatant_id: The entity id, as it appears on an encounter group or an event.

        Returns:
            The live [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] or
            [`Character`][osrlib.core.character.Character], or `None` when no live entity has that
            id.
        """
        found = self.monsters.get(combatant_id)
        if found is not None:
            return found
        return self.npcs.get(combatant_id)

    def member(self, character_id: str) -> Character:
        """Return the party member with this id.

        The ids are the ones events use, so this is how you get from an event to the character
        it's about. It is [`Party.member`][osrlib.crawl.party.Party.member] with the session's
        own party filled in.

        Args:
            character_id: The member's session id, like `"character-0001"`.

        Returns:
            The member, living or dead.

        Raises:
            ValueError: If no member of the party has that id.
        """
        return self.party.member(character_id)

    def spawn(self, template_id: str, count: int, *, alignment: Alignment | None = None) -> list[MonsterInstance]:
        """Spawn monsters into the session and return them.

        Each instance rolls its own hit points from the seeded spawn stream and takes an id from
        the session allocator, and lands in `monsters` where the rest of the engine can find it.
        Spawning alone puts nothing in front of the party: the encounter procedure is what fields
        them. A referee wanting both at once should execute
        [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters], which spawns and opens the
        encounter in one logged command.

        Args:
            template_id: Any id in the session's
                [`effective_monsters`][osrlib.crawl.session.GameSession.effective_monsters]
                catalog, shipped (see [the monster id index][monsters-index]) or bundled by the
                adventure.
            count: How many to spawn.
            alignment: An alignment to give them instead of the template's, for keyed content
                whose author wants, say, lawful goblins.

        Returns:
            The new instances, in spawn order.

        Raises:
            ValueError: If the catalog has no such template id.
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
        something outside the command set. A referee moving the clock from a front end should
        execute [`AdvanceTime`][osrlib.crawl.commands.AdvanceTime], which comes through here and
        is logged.

        Time passing isn't nothing: effects tick and expire, a light burning out puts the party in
        the dark, and each day boundary crossed consumes rations and water. A light expiring is a
        referee-visibility record in the ledger, so the session adds the player-facing
        [`LightEvent`][osrlib.crawl.events.LightEvent] beside it, naming what went out.

        For whole turns with the exploration cadences (rest, wandering), call
        [`advance_turns`][osrlib.crawl.session.GameSession.advance_turns] instead. Rounds alone
        run no cadence.

        Args:
            n: How many rounds to advance.

        Returns:
            The ledger's own events plus the light translations and any provisions events, in the
            order they happened.
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
        self, turns: int, *, resting: bool = False, field: bool | None = None
    ) -> tuple[list[Event], bool]:
        """Advance whole turns, one at a time, running the per-turn bookkeeping.

        This is the time path the exploration commands use, and the one to call when you are
        resolving elapsed time yourself. A clock standing part way through a turn snaps to the next
        turn boundary first, so an action that costs a turn absorbs the part-turn the party had
        already walked off.

        Each turn: the ledger advances, a day boundary consumes provisions, the rest cadence
        counts unless the party is resting, and, in the field, the wandering cadence may fire a
        check. A check that produces an encounter stops the advance where it is, because the party
        now has something else to deal with, and the second return value says so.

        A span in the field also stops the moment nobody is left standing, since the cadences
        belong to the living. Out of the field it keeps going, because a revival window measured
        in elapsed time has to keep elapsing while the party lies dead.

        Args:
            turns: How many turns to advance.
            resting: True while the party is resting, which keeps the rest cadence from counting
                and lowers the wandering chance by one.
            field: Whether the wandering cadence runs. Defaults to "the party is exploring a
                dungeon", which is the only place wandering monsters are rolled for. Town time
                and travel are abstract.

        Returns:
            The events, and True when a wandering encounter interrupted the span before it ran
            out.
        """
        from osrlib.crawl import exploration

        events: list[Event] = []
        for _ in range(turns):
            events.extend(self.advance_rounds(ROUNDS_PER_TURN - self.clock.rounds % ROUNDS_PER_TURN))
            in_field = field if field is not None else self.mode is SessionMode.EXPLORING
            if in_field and not self.party.living_members():
                # The cadences belong to the living: a span that kills the last
                # member, a rest that starves the party out, stops at the turn it
                # happened. Out of the field there is nothing to stop, and a revival
                # window measured in elapsed time has to keep elapsing.
                break
            if in_field and not resting:
                # The rest cadence is a dungeon rule ("must rest for one turn every
                # hour in the dungeon"); town time and overland travel don't accrue.
                self.turns_since_rest += 1
                events.extend(exploration.check_fatigue(self))
            if in_field:
                self.wandering_counter += 1
                if self.wandering_counter >= exploration.wandering_interval(self):
                    self.wandering_counter = 0
                    check_events, encountered = exploration.wandering_check(self, resting=resting)
                    events.extend(check_events)
                    if encountered:
                        return events, True
        return events, False

    # ------------------------------------------------------------------ light queries

    def party_light(self) -> tuple[bool, bool]:
        """Return whether the party has light, and whether infravision works.

        Light gates most of exploration, so this is what a front end asks before it dims the
        screen or greys out a search button, and what the engine asks before it lets the party
        read, search, or see an encounter coming.

        The party has light when any living member carries an active light-family effect. A
        darkness-family effect on any living member puts that out while it runs, because the
        printed radius of the spell swallows a marching party, and some darkness blocks infravision
        as well.

        Returns:
            A pair: whether the party is lit, and whether infravision is allowed.
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
        """Return whether the party is carrying daylight-bright light.

        The wandering-monster chance goes up for a party that can be seen coming. osrlib reads the
        flame of a torch or lantern as the baseline the printed chance already assumes, so only a
        light whose data says its brightness is daylight counts here, which in the shipped catalog
        means *continual light*. See
        [the adaptations register](https://mmacy.github.io/osrlib-python/adaptations/), the page
        that lists where osrlib settles an ambiguous rule or supplies a default.

        Returns:
            True when a living member carries a light effect whose brightness is daylight.
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

        Either the class has it, as the demi-human classes do, or a spell has granted it. The
        engine asks this when it decides whether a character can act in the dark and when it sets
        the party's surprise threshold.

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

        The award is measured in copper so that no rounding is lost on the way, and converted to
        gold once at the end. The session takes one of these when the party leaves town and
        another when it comes back, and the difference is the treasure experience.

        Every member counts, the dead included, because treasure carried out on a body still came
        home. Magic items and mundane gear count nothing: magical treasure grants no experience,
        and selling off used gear is below the level of detail osrlib simulates.

        Returns:
            The coins, in copper, plus every valuable's listed value, converted to copper.
        """
        total = 0
        for member in self.party.members:
            total += member.inventory.purse.value_cp
            total += sum(valuable.value_gp * 100 for valuable in member.inventory.valuables)
        return total

    def snapshot_treasure(self) -> None:
        """Record what the party is worth as it leaves town, for the return award.

        [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] calls it, so a front end doesn't have
        to. Call it yourself only when your game starts a delve some other way.
        """
        self.treasure_snapshot_cp = self.party_valuation_cp()

    def award_adventure_xp(self) -> list[Event]:
        """Pay the end-of-adventure experience award and return its events.

        [`TravelToTown`][osrlib.crawl.commands.TravelToTown] calls it under the default ruleset,
        where experience is awarded for making it back alive, so a front end doesn't call it
        itself.

        The award is what the defeated creatures were worth plus what the treasure gained since
        the party left town is worth, one experience point per gold piece, never less than zero: a
        party that came home poorer learned nothing from it. The total divides evenly among the
        survivors, rounded down, and applies to each of them. The dead count toward the treasure
        that came home and take no share, and a party that lost everyone is awarded nothing,
        because nobody returned to tell it.

        Whatever happens, the defeated-creature list is cleared and the departure snapshot reset,
        so the next delve starts from scratch.

        Returns:
            The [`AdventureXpAwardEvent`][osrlib.crawl.events.AdventureXpAwardEvent] and then each
            survivor's own award and level events, or nothing at all when there's no award to
            make.
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

        This is the path a ruleset set to award immediately takes at the end of each encounter and
        on each haul taken. It divides the same way the return award does: evenly among the living,
        rounded down, remainder dropped. To award a specific character a specific amount, execute
        [`AwardXP`][osrlib.crawl.commands.AwardXP] instead, which is logged and replayed.

        Args:
            amount: The pool to divide. Zero or less awards nothing.

        Returns:
            Each survivor's award event and, where one levelled, the level event, or nothing when
            there's nobody alive or the share rounds to zero.
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

    def _record_deaths(self, events: Sequence[Event]) -> bool:
        """Record party deaths with the clock round and the cause just resolved.

        The cause is `poison` when the killing resolution was a poison save (a failed
        death-category save immediately before the death) or a poison effect running its course,
        and otherwise the kind of the nearest preceding cause-bearing event. Only the poison and
        non-poison distinction is consumed, by *neutralize poison*.

        Args:
            events: The just-executed command's events, in order.

        Returns:
            True when a party member died in them, which is the edge the party-wipe check triggers
            on, identified by this same walk. Monsters and NPC adventurers have ids that aren't
            members' and never count.
        """
        member_ids = {member.id for member in self.party.members}
        cause = "unknown"
        died = False
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
                died = True
        return died

    def _end_on_party_wipe(self) -> list[Event]:
        """End the session when the death just recorded left nobody standing.

        The one entrance to `game_over`, whatever killed the party: a lost battle, a trap, a fall,
        starvation, a poison that finished the last member while the referee moved the clock. Any
        open encounter or battle clears, because a session that has ended holds no live play state,
        and the ending is reported as one
        [`GameOverEvent`][osrlib.crawl.events.GameOverEvent].

        The trigger is the death rather than the state: this runs only for a command whose own
        events killed a member, so carrying an already-fallen party to town, which is the first
        step of a revival, never re-enters game over. A session already in a terminal mode is left
        alone, so a party that dies after the adventure concluded stays in `victory` and a second
        death among the fallen ends nothing twice.

        Returns:
            The ending event, or nothing when a member still lives.
        """
        if self.mode.terminal or self.party.living_members():
            return []
        self.battle = None
        self.encounter = None
        self.mode = SessionMode.GAME_OVER
        return [GameOverEvent(reason="the party has fallen")]

    # ------------------------------------------------------------------ views

    def view(self, visibility: Visibility) -> PlayerView | RefereeView:
        """Return a projection of the session at one visibility level.

        Draw from a view rather than from the session's attributes. The player view is an
        enumerated whitelist of exactly what a player may be shown, so a front end built on it
        cannot leak the map it hasn't explored, the monster hit points, or the referee's rolls.
        The referee view contains the rest, for a referee screen, an LLM running the game, or a
        test.

        A networked game keeps the session and the referee view on the server and sends the client
        the player view, or the player-visibility events. Neither view contains the master seed,
        which lives only in the save.

        Views are frozen and built fresh from the current state each time, never from the event
        log, so call this again after each command rather than holding one.

        Args:
            visibility: `PLAYER` for the safe whitelist, `REFEREE` for everything but the random
                streams' internals.

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
            print("flags" in referee.state, "flags" in player.model_dump())
            # True False
            # Neither view carries the master seed.
            print("master_seed" in referee.state)
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
        return [Rejection(code="session.command.unknown_item", params={"item": command.item_id})], []
    member.inventory.items.append(ItemInstance(template=template, quantity=command.quantity))
    events: list[Event] = [
        ItemAcquiredEvent(character_id=_member_id(member), item_ids=(command.item_id,) * command.quantity)
    ]
    return [], events


def _handle_grant_coins(session: GameSession, command: GrantCoins) -> tuple[list[Rejection], list[Event]]:
    try:
        member = session.member(command.character_id)
    except ValueError:
        return [Rejection(code="session.command.unknown_member", params={"character": command.character_id})], []
    purse = member.inventory.purse
    for denomination in ("pp", "gp", "ep", "sp", "cp"):
        setattr(purse, denomination, getattr(purse, denomination) + getattr(command.coins, denomination))
    events: list[Event] = [ItemAcquiredEvent(character_id=_member_id(member), coins_gp_value=command.coins.value_gp)]
    return [], events


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
    # Fired-marks answer "has this trigger fired", so a repeat mark adds nothing;
    # the event fires every time, because a repeatable trigger's every firing is
    # marked and the log is the record of each one.
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
# them, and every guard is a rejection, so the accepted log holds a state-consistent
# sequence and a replay never meets a refusal.


def _quest_pair(session: GameSession, quest_id: str) -> tuple[QuestSpec, QuestState] | None:
    """The quest's authored spec and its live state, or `None` when the id names neither."""
    try:
        spec = session.adventure.quest(quest_id)
    except ValueError:
        return None
    state = session.quests.get(quest_id)
    return None if state is None else (spec, state)


def _objective_pair(
    spec: QuestSpec, state: QuestState, objective_id: str
) -> tuple[ObjectiveSpec, ObjectiveState] | None:
    """The objective's authored spec and its live state, or `None` when the quest has none."""
    objective = next((entry for entry in spec.objectives if entry.id == objective_id), None)
    objective_state = state.objectives.get(objective_id)
    return None if objective is None or objective_state is None else (objective, objective_state)


def _unknown_quest(quest_id: str) -> tuple[list[Rejection], list[Event]]:
    """The closed domain's answer to an id no quest of the adventure holds."""
    return [Rejection(code="session.command.unknown_quest", params={"quest": quest_id})], []


def _unknown_objective(quest_id: str, objective_id: str) -> tuple[list[Rejection], list[Event]]:
    """The same answer one level down: the quest is real, this objective of it isn't."""
    return [
        Rejection(code="session.command.unknown_objective", params={"quest": quest_id, "objective": objective_id})
    ], []


def _quest_state_refused(params: dict[str, int | str | tuple[int | str, ...]]) -> tuple[list[Rejection], list[Event]]:
    """A lifecycle command that contradicts the state it found, which names it."""
    return [Rejection(code="session.command.quest_state", params=params)], []


def _append_quest_beat(session: GameSession, text: str) -> None:
    """Append a quest beat to the journal, stamped with the clock it landed at.

    The same [`JournalEntry`][osrlib.crawl.session.JournalEntry] construction
    [`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry] makes, and no
    [`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent] behind it:
    the quest's own lifecycle event is this beat's event, and emitting both would show
    the table one line twice. A beat the author didn't write appends nothing.
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
    # A completed objective is a revealed one, so the more specific state answers first.
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
    # Completing surfaces a hidden objective: no separate reveal, and the player view
    # never has to explain a quest that finished something it never mentioned.
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
    # The quest must be active, and that is the whole test: the completion rule is
    # the issuer's discipline, not the handler's, because ruling a quest done is the
    # referee's call.
    if state.status != "active":
        return _quest_state_refused({"quest": command.quest_id, "state": state.status})
    state.status = "completed"
    beat = spec.narrative.completion if spec.narrative is not None else ""
    _append_quest_beat(session, beat)
    events: list[Event] = [QuestCompletedEvent(quest_id=spec.id, name=spec.name, narrative=beat or None)]
    if spec.concludes_adventure and not session.mode.terminal:
        # The one entrance to victory. A concluded session holds no live play state,
        # the same rule a party wipe applies; a session that has already ended advances
        # the quest and changes no mode.
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
    if session.encounter is not None or session.battle is not None:
        return [Rejection(code="session.command.encounter_in_progress")], []
    if session.dungeon_state.location.kind != "dungeon":
        # Encounters live on the dungeon grid: the combat space, silence cells,
        # and formation widths all need a party cell to stand on.
        return [Rejection(code="session.command.not_in_dungeon")], []
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
    if command.count_dice is not None:
        count_dice = command.count_dice
    else:
        count_dice = next(
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
        # not leave an overlay entry behind for a door nobody has touched.
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
        state.discovered = command.discovered
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
            # Unreachable: the location model validates that dungeon fields travel together.
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
        events.append(LocationEnteredEvent(location_kind="dungeon", location_id=dungeon_id, level_number=level_number))
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
        # Referee time passes with full bookkeeping but no wandering cadence,
        # because the referee decides what walks in.
        events, _ = session.advance_turns(turns, field=False)
    events.append(TimeAdvancedEvent(n=command.n, unit=command.unit.value, rounds_total=session.clock.rounds))
    return [], events


def _handle_roll_dice(session: GameSession, command: RollDice) -> tuple[list[Rejection], list[Event]]:
    from osrlib.core.dice import roll

    # The command's field validator already guaranteed the expression parses, so the
    # draw happens unconditionally here: validation is the pure pre-phase, the roll is
    # the only side effect, and it lands on its own stream to leave keyed draws untouched.
    result = roll(command.expression, session.streams.get(ADJUDICATION_STREAM))
    return [], [DiceRolledEvent(expression=command.expression, total=result.total, rolls=result.rolls)]


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
}

_HANDLERS_CACHE: dict | None = None


def _handlers() -> Mapping[type[Command], Any]:
    """The command-type to handler map, assembled lazily to avoid import cycles."""
    global _HANDLERS_CACHE
    if _HANDLERS_CACHE is None:
        from osrlib.crawl import battle, encounter, exploration

        _HANDLERS_CACHE = {
            **_REFEREE_HANDLERS,
            **exploration.HANDLERS,
            **encounter.HANDLERS,
            **battle.HANDLERS,
        }
    return _HANDLERS_CACHE
