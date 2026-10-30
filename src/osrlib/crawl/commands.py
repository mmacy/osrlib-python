"""The command set: typed requests, the union that parses them, and the result of executing one.

A command is an object that describes one action, like moving the party east. Commands are how
your code changes the game, so start with this module. Build a command, pass it to
[`GameSession.execute`][osrlib.crawl.session.GameSession.execute], and read the
[`CommandResult`][osrlib.crawl.commands.CommandResult] it returns. The result's `accepted`
field is `True` when the session accepted the command. A refused command comes back with
[`Rejection`][osrlib.core.validation.Rejection]s, and an accepted one comes back with the
events it caused. Show the player those events, with
[`format_message`][osrlib.messages.format_message] for a default English line or with your
own text. Read what to draw from
[`GameSession.view`][osrlib.crawl.session.GameSession.view] rather than from the session's
attributes, because the player view leaves out what the players mustn't see.

Every command is a frozen pydantic model with a `command_type` field that identifies its class,
so a command serializes to a JSON object with a `command_type` key.
[`AnyCommand`][osrlib.crawl.commands.AnyCommand] is the union of every command class,
discriminated on that key. [`parse_command`][osrlib.crawl.commands.parse_command] turns one
serialized mapping back into a command. It returns `None` for a `command_type` it doesn't
recognize, so an older engine can still read a log from a newer one.

Which commands the session accepts depends on where the party is. A
[`SessionMode`][osrlib.crawl.commands.SessionMode] is one of these states: `town`, `exploring`,
`encounter`, and `battle` while play goes on, and `game_over` or `victory` once the session
has ended. Each command class lists the modes it's legal in as its `allowed_modes` class
attribute. The session refuses a command sent in any other mode with
`session.command.wrong_mode`, before it runs any other check.

Referee commands are issued by game logic rather than by a player: a
[`Listener`][osrlib.crawl.session.Listener] you write, the interpreter that runs an
adventure's triggers, or an LLM referee. They grant items and XP, set flags, spawn
monsters, move the clock, and record quest progress. Each one's summary starts with
"Referee:". They're legal in every mode, and the session logs and replays them like any
other command. The exceptions are the ones that would restart play once the session has
ended: [`PlaceParty`][osrlib.crawl.commands.PlaceParty] is illegal in `victory`, and
[`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters] and
[`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty] are illegal in `victory` and
`game_over`.

Each command class's docstring states its contract in three sections. `Modes:` lists the session
modes that accept it, `Rejections:` lists the codes it can be refused with, and `Events:`
lists the events it emits when it's accepted. The
[command schema pages](https://mmacy.github.io/osrlib-python/reference/commands/) give each
command's JSON Schema, and `commands.json` there is the whole union as one file.

The commands, grouped by what the party is doing:

- Moving in a dungeon (`exploring`):
  [`MoveParty`][osrlib.crawl.commands.MoveParty],
  [`TurnParty`][osrlib.crawl.commands.TurnParty],
  [`UseStairs`][osrlib.crawl.commands.UseStairs], and
  [`TravelToTown`][osrlib.crawl.commands.TravelToTown] from the entrance cell.
- Doors (`exploring`): [`OpenDoor`][osrlib.crawl.commands.OpenDoor],
  [`CloseDoor`][osrlib.crawl.commands.CloseDoor],
  [`ForceDoor`][osrlib.crawl.commands.ForceDoor],
  [`PickLock`][osrlib.crawl.commands.PickLock],
  [`WedgeDoor`][osrlib.crawl.commands.WedgeDoor], and
  [`ListenAtDoor`][osrlib.crawl.commands.ListenAtDoor].
- Searching and treasure (`exploring`): [`Search`][osrlib.crawl.commands.Search],
  [`InspectTreasure`][osrlib.crawl.commands.InspectTreasure],
  [`RemoveTreasureTrap`][osrlib.crawl.commands.RemoveTreasureTrap], and
  [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure].
- Party, gear, and light (`town` and `exploring`):
  [`ReorderParty`][osrlib.crawl.commands.ReorderParty],
  [`GiveItems`][osrlib.crawl.commands.GiveItems],
  [`EquipItem`][osrlib.crawl.commands.EquipItem],
  [`UnequipItem`][osrlib.crawl.commands.UnequipItem],
  [`LightSource`][osrlib.crawl.commands.LightSource], and
  [`ExtinguishSource`][osrlib.crawl.commands.ExtinguishSource].
- Items in a dungeon or an encounter (`exploring` and `encounter`):
  [`DropItems`][osrlib.crawl.commands.DropItems] and
  [`UseItem`][osrlib.crawl.commands.UseItem].
- Rest and spells (`town` and `exploring`): [`Rest`][osrlib.crawl.commands.Rest],
  [`PrepareSpells`][osrlib.crawl.commands.PrepareSpells],
  [`LearnSpell`][osrlib.crawl.commands.LearnSpell], and
  [`CastSpell`][osrlib.crawl.commands.CastSpell].
- Town (`town`): [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon],
  [`PurchaseEquipment`][osrlib.crawl.commands.PurchaseEquipment],
  [`SellTreasure`][osrlib.crawl.commands.SellTreasure], and
  [`PurchaseHealing`][osrlib.crawl.commands.PurchaseHealing].
- Meeting monsters (`encounter`): [`Parley`][osrlib.crawl.commands.Parley],
  [`Evade`][osrlib.crawl.commands.Evade], [`Wait`][osrlib.crawl.commands.Wait],
  [`TurnUndead`][osrlib.crawl.commands.TurnUndead], and
  [`EngageBattle`][osrlib.crawl.commands.EngageBattle].
- Fighting (`battle`): [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound],
  with one [`BattleDeclaration`][osrlib.crawl.commands.BattleDeclaration] per member.
- Referee commands: [`GrantItem`][osrlib.crawl.commands.GrantItem],
  [`GrantCoins`][osrlib.crawl.commands.GrantCoins],
  [`AwardXP`][osrlib.crawl.commands.AwardXP],
  [`IdentifyItem`][osrlib.crawl.commands.IdentifyItem],
  [`SetFlag`][osrlib.crawl.commands.SetFlag],
  [`SetDoorState`][osrlib.crawl.commands.SetDoorState],
  [`PlaceParty`][osrlib.crawl.commands.PlaceParty],
  [`AdvanceTime`][osrlib.crawl.commands.AdvanceTime],
  [`RollDice`][osrlib.crawl.commands.RollDice],
  [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters],
  [`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty],
  [`RecordNote`][osrlib.crawl.commands.RecordNote],
  [`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry],
  [`MarkTriggerFired`][osrlib.crawl.commands.MarkTriggerFired], and the quest commands
  [`ActivateQuest`][osrlib.crawl.commands.ActivateQuest],
  [`RevealObjective`][osrlib.crawl.commands.RevealObjective],
  [`CompleteObjective`][osrlib.crawl.commands.CompleteObjective], and
  [`CompleteQuest`][osrlib.crawl.commands.CompleteQuest].

To work with the whole set, iterate
[`ALL_COMMAND_CLASSES`][osrlib.crawl.commands.ALL_COMMAND_CLASSES].
[`CONSEQUENCE_COMMAND_CLASSES`][osrlib.crawl.commands.CONSEQUENCE_COMMAND_CLASSES] and
[`ConsequenceCommand`][osrlib.crawl.commands.ConsequenceCommand] are the referee commands an
adventure document may contain.

Typical usage:

```python
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import EnterDungeon, MoveParty, parse_command
from osrlib.crawl.dungeon import Direction, DungeonSpec, Edge, EdgeKind, LevelSpec, edge_key
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession

rules = Ruleset()
stream = RngStreams(master_seed=11).get(CHARACTER_CREATION_STREAM)
hero = create_character(name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=rules, stream=stream)
corridor = LevelSpec(
    number=1,
    width=2,
    height=1,
    entrance=(0, 0),
    edges={edge_key((0, 0), Direction.EAST): Edge(kind=EdgeKind.OPEN)},
)
crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(corridor,))
adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))
session = GameSession.new(Party(members=[hero.character]), adventure, seed=11)

# In town a move is refused: the mode gate runs before any other validation.
refused = session.execute(MoveParty(direction=Direction.EAST))
assert not refused.accepted
assert [rejection.code for rejection in refused.rejections] == ["session.command.wrong_mode"]

# Enter the dungeon, then walk one cell east.
session.execute(EnterDungeon(dungeon_id="crypt"))
moved = session.execute(MoveParty(direction=Direction.EAST))
assert moved.accepted
assert [event.event_type for event in moved.events] == ["party_moved"]

# A command serializes to JSON and parses back through the discriminated union.
wire = MoveParty(direction=Direction.EAST).model_dump(mode="json")
assert wire == {"command_type": "move_party", "source": None, "direction": "east"}
assert parse_command(wire) == MoveParty(direction=Direction.EAST)
assert parse_command({"command_type": "fly_party"}) is None
```
"""

from collections.abc import Mapping
from enum import StrEnum
from functools import cache
from typing import Annotated, ClassVar, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator, model_validator

from osrlib.core.clock import TimeUnit
from osrlib.core.dice import parse
from osrlib.core.events import Event
from osrlib.core.items import Coins
from osrlib.core.spells import MemorizedSpell
from osrlib.core.validation import Rejection
from osrlib.crawl.dungeon import Direction, PartyLocation

__all__ = [
    "ALL_COMMAND_CLASSES",
    "CONSEQUENCE_COMMAND_CLASSES",
    "ActivateQuest",
    "AddJournalEntry",
    "AdvanceTime",
    "AnyCommand",
    "AwardXP",
    "BattleDeclaration",
    "CastSpell",
    "CloseDoor",
    "Command",
    "CommandResult",
    "CompleteObjective",
    "CompleteQuest",
    "ConsequenceCommand",
    "DropItems",
    "EngageBattle",
    "EnterDungeon",
    "EquipItem",
    "Evade",
    "ExtinguishSource",
    "ForceDoor",
    "GiveItems",
    "GrantCoins",
    "GrantItem",
    "HealingService",
    "IdentifyItem",
    "InspectTreasure",
    "LearnSpell",
    "LightSource",
    "ListenAtDoor",
    "MarkTriggerFired",
    "MoveParty",
    "OpenDoor",
    "Parley",
    "PickLock",
    "PlaceParty",
    "PrepareSpells",
    "PurchaseEquipment",
    "PurchaseHealing",
    "RecordNote",
    "RemoveTreasureTrap",
    "ReorderParty",
    "ResolveBattleRound",
    "Rest",
    "RevealObjective",
    "RollDice",
    "Search",
    "SessionMode",
    "SetDoorState",
    "SellTreasure",
    "SetFlag",
    "SpawnMonsters",
    "SpawnNpcParty",
    "TakeTreasure",
    "TravelToTown",
    "TurnParty",
    "TurnUndead",
    "UnequipItem",
    "UseItem",
    "UseStairs",
    "Wait",
    "WedgeDoor",
    "parse_command",
]


class SessionMode(StrEnum):
    """Where the party is in play, which determines the commands the session accepts.

    Read the current mode from
    [`GameSession.mode`][osrlib.crawl.session.GameSession.mode], and compare it with a
    command class's `allowed_modes` to find out whether the session accepts that command now.
    The session refuses a command sent in a mode outside its `allowed_modes` with
    `session.command.wrong_mode`.

    The values are lowercase strings, and saves contain them, so a value never changes
    within one `schema_version`.

    `game_over` and `victory` are the terminal modes, which mean the session has ended.
    Play commands are refused in both. Referee commands stay legal in both, except the ones
    that would restart play: [`PlaceParty`][osrlib.crawl.commands.PlaceParty] is illegal in
    `victory`, and [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters] and
    [`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty] are illegal in both. No play
    command leaves a terminal mode. A referee's `PlaceParty` is the only way out of
    `game_over`, and no command leaves `victory`.
    """

    TOWN = "town"
    """The party is in the base town, between trips into a dungeon. Here it buys gear, sells
    treasure, pays a temple for healing, rests, and prepares spells. A new session starts in
    this mode, and [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] leaves it."""
    EXPLORING = "exploring"
    """The party is standing on a cell of a dungeon level. Moving, doors, listening, searching,
    treasure, light, and stairs are legal here, and most of a session is spent in this mode."""
    ENCOUNTER = "encounter"
    """The party has met monsters, and nobody has attacked yet. The party can talk
    ([`Parley`][osrlib.crawl.commands.Parley]), run ([`Evade`][osrlib.crawl.commands.Evade]),
    hold ([`Wait`][osrlib.crawl.commands.Wait]), present a holy symbol
    ([`TurnUndead`][osrlib.crawl.commands.TurnUndead]), or attack
    ([`EngageBattle`][osrlib.crawl.commands.EngageBattle]). It can also drop or use items
    ([`DropItems`][osrlib.crawl.commands.DropItems], [`UseItem`][osrlib.crawl.commands.UseItem]).
    The session refuses every other play command until the encounter ends."""
    BATTLE = "battle"
    """A fight is underway.
    [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound] is the only play command
    the session accepts. Send one per round until the session leaves this mode."""
    GAME_OVER = "game_over"
    """Every party member is dead and the session has ended. The session refuses play commands,
    and referee commands still work. [`PlaceParty`][osrlib.crawl.commands.PlaceParty] is the only
    way out: placing the dead party in town lets you buy *raise dead* at the temple with
    [`PurchaseHealing`][osrlib.crawl.commands.PurchaseHealing]."""
    VICTORY = "victory"
    """A quest that concludes the adventure has completed, and the session has ended. The
    session refuses play commands and never leaves this mode, so show the game's final
    screen. [`CompleteQuest`][osrlib.crawl.commands.CompleteQuest] is the only command
    that enters it."""

    @property
    def terminal(self) -> bool:
        """Whether the session has ended, which is True in `game_over` and `victory`."""
        return self in (SessionMode.GAME_OVER, SessionMode.VICTORY)


_ALL_MODES = frozenset(SessionMode)
_FIELD_MODES = frozenset({SessionMode.TOWN, SessionMode.EXPLORING})


class Command(BaseModel):
    """The base class of every command.

    You don't construct this class. Construct one of the command classes, which all inherit
    `source` and `command_type` from it, and pass the command to
    [`GameSession.execute`][osrlib.crawl.session.GameSession.execute]. Annotate a parameter
    with `Command` when it takes any command object. Annotate it with
    [`AnyCommand`][osrlib.crawl.commands.AnyCommand] when pydantic has to build the command
    from serialized data.

    Commands are frozen: you can't change a field after construction. The session logs an
    accepted command exactly as it arrived, and replaying the log replays the game.
    Parsing a command ignores fields its class doesn't define, so a command from a newer
    engine still parses on an older one.

    Don't subclass this class to add your own actions. The session has no handler for a
    class defined outside osrlib, and
    [`GameSession.execute`][osrlib.crawl.session.GameSession.execute] raises `ValueError` for
    one. Issue referee commands, or register a
    [`Listener`][osrlib.crawl.session.Listener], instead.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    command_type: str
    """The name that identifies the command class in serialized data. Each command class fixes
    it to a distinct snake_case string, like `"move_party"` or `"open_door"`, and constructing
    the class sets it, so you never pass it yourself.
    [`parse_command`][osrlib.crawl.commands.parse_command] and the
    [`AnyCommand`][osrlib.crawl.commands.AnyCommand] union use it to rebuild the right class
    from a serialized mapping. A class's value never changes between releases."""
    source: str | None = Field(default=None, min_length=1)
    """Who or what issued the command on whose behalf: an authored trigger or quest id, or the
    name of one of your game's systems. The engine never reads it, so a command with a
    `source` does exactly what the same command without one does. The session logs and
    replays it with the command, so the log alone shows why something happened. Leave it
    `None` when there's nothing to record. An empty string fails validation when the command
    is constructed."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = _ALL_MODES
    """The session modes that accept this command.

    It's a class attribute rather than a field, because it belongs to the command class
    rather than to one request, so it never serializes. Read it as
    `MoveParty.allowed_modes`, and compare it with
    [`GameSession.mode`][osrlib.crawl.session.GameSession.mode] to offer only the commands
    the session accepts now.
    [`GameSession.execute`][osrlib.crawl.session.GameSession.execute] checks it before
    anything else and refuses a command sent in any other mode with
    `session.command.wrong_mode`. The base class allows every mode. Each play command narrows
    the set, and so do the referee commands
    [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters],
    [`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty], and
    [`PlaceParty`][osrlib.crawl.commands.PlaceParty]. Each command's `Modes:` section lists
    its set."""

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        """Reject subclasses that weaken the schema contract via `model_config`."""
        super().__pydantic_init_subclass__(**kwargs)
        if cls.model_config.get("extra") != "ignore":
            raise TypeError(f"{cls.__name__} must keep extra='ignore': the command schema grows additively")
        if not cls.model_config.get("frozen"):
            raise TypeError(f"{cls.__name__} must stay frozen: accepted commands are logged verbatim")


class CommandResult(BaseModel):
    """The outcome of one command: accepted with its events, or refused with its rejections.

    [`GameSession.execute`][osrlib.crawl.session.GameSession.execute] returns one for each
    command you pass it. Check `accepted` first. When it's `False`, show the player
    `rejections`. When it's `True`, render `events`. That check is your turn loop.

    A refused command draws no dice, spends no game time, changes nothing, and stays out of
    [`GameSession.command_log`][osrlib.crawl.session.GameSession.command_log]. Its result contains the rejections and no
    events. Because a refusal costs the party nothing, show it as the game telling the player "you can't do that", not
    as an error.

    An accepted command's `events` contains everything that happened, in the order the
    session logged it. That includes the events of every command a listener issued in
    reaction, so you can render the whole reaction from one result without reading
    [`GameSession.event_log`][osrlib.crawl.session.GameSession.event_log].
    """

    model_config = ConfigDict(frozen=True)

    accepted: bool
    """Whether the command ran. `False` means the session refused it before running any of it.
    No dice were drawn, no game time passed, nothing changed, and the command isn't in
    [`GameSession.command_log`][osrlib.crawl.session.GameSession.command_log]."""
    rejections: tuple[Rejection, ...] = ()
    """Why the command was refused. It's empty when the command was accepted. Each
    [`Rejection`][osrlib.core.validation.Rejection] has a dotted code and the structured facts
    behind it, never English text, so you choose the words to show. Each
    command's `Rejections:` section lists the codes that command can be refused with."""
    events: tuple[Event, ...] = ()
    """Everything the accepted command caused, in the order the session logged it. It's empty
    when the command was refused. The events of the command itself come first. Then, for each
    registered listener in turn, come the events of the commands that listener issued,
    however deeply nested, and then the events the listener emitted itself. Each event
    appears once. You can render the whole reaction from this tuple, without reading
    [`GameSession.event_log`][osrlib.crawl.session.GameSession.event_log]. Pass an event to
    [`format_message`][osrlib.messages.format_message] for a default English line."""


class MoveParty(Command):
    """Move the party one cell, turning to face the way it goes.

    The party must already be in a dungeon. A new session starts in town, and
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] places the party at the entrance
    and switches the session to `exploring`. A step onto a cell the party hasn't explored
    uses 10 feet of the party's movement, and a step onto an explored cell uses a third of
    that. Each time the movement adds up to the party's exploration rate, one turn passes on
    the game clock.

    A passage is a [`TransitionSpec`][osrlib.crawl.dungeon.TransitionSpec] whose `kind` is
    `"passage"`, standing on one side of a cell. A move toward that side crosses the passage
    instead of stopping at the wall there. The party arrives at the passage's destination,
    facing its `to_facing`, and the move costs what a step onto that destination cell costs.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` - the session is not exploring a dungeon.
        - `exploration.move.cannot_move` - the party cannot move: it is overloaded,
          or a living member is unable to walk.
        - `exploration.move.blocked` - a wall, a closed door, an undiscovered secret
          door, or the edge of the map is on that side, and no passage stands there.
        - `exploration.transition.gate_refused` - the move would cross a passage
          whose authored condition ([`GateSpec`][osrlib.crawl.gates.GateSpec]) the
          party doesn't meet. The rejection includes the gate's authored refusal text.
          Like every refusal, it costs no movement, no time, and no toll.

    Events:
        [`PartyMovedEvent`][osrlib.crawl.events.PartyMovedEvent] with the new position
        and facing. Arriving in the new cell can set off more, each with a
        separate event. A [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent]
        follows when the party crosses into a new area, level, or dungeon. A
        [`DoorEvent`][osrlib.crawl.events.DoorEvent] follows for each unwedged door the party
        opened that swings shut behind it. The area's treasure roll arrives as a
        [`HoardGeneratedEvent`][osrlib.crawl.events.HoardGeneratedEvent]. A room
        trap's spring check produces a
        [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent] and a
        [`TrapEvent`][osrlib.crawl.events.TrapEvent], and a trap that springs
        resolves at once.

        A pool of burning oil or a *web* on the cell affects the party as it arrives
        ([`DamageDealtEvent`][osrlib.core.events.DamageDealtEvent],
        [`HitPointsReportedEvent`][osrlib.core.events.HitPointsReportedEvent],
        [`DeathEvent`][osrlib.core.events.DeathEvent],
        [`EffectAttachedEvent`][osrlib.core.events.EffectAttachedEvent],
        [`ConditionGainedEvent`][osrlib.core.events.ConditionGainedEvent]). Monsters keyed to
        the area in the adventure (a keyed encounter) meet the party the first time it enters,
        with [`SurpriseRolledEvent`][osrlib.crawl.events.SurpriseRolledEvent]s,
        [`EncounterStartedEvent`][osrlib.crawl.events.EncounterStartedEvent],
        [`ReactionRolledEvent`][osrlib.core.events.ReactionRolledEvent], and
        [`StanceChangedEvent`][osrlib.crawl.events.StanceChangedEvent]. When their reaction is
        to attack, battle opens at once
        ([`BattleStartedEvent`][osrlib.crawl.events.BattleStartedEvent]).

        A move through a passage reports the crossing before the step, as
        [`UseStairs`][osrlib.crawl.commands.UseStairs] does. An
        [`ItemConsumedEvent`][osrlib.crawl.events.ItemConsumedEvent] comes first when
        the passage's gate takes an item as a toll. A `LocationEnteredEvent` follows when the
        level or dungeon changes, with `via` set to `"passage"` and `transition_ref`
        set to the passage's cell. Then come the `DoorEvent`s for the doors that swing shut
        on the level the party left, the `PartyMovedEvent` for the landing cell, and the
        landing cell's events.

        When the step's movement completes a turn on the game clock, the turn's
        bookkeeping follows:
        [`EffectExpiredEvent`][osrlib.core.events.EffectExpiredEvent] with a
        [`LightEvent`][osrlib.crawl.events.LightEvent] for a light burning out,
        [`FatigueEvent`][osrlib.crawl.events.FatigueEvent] when the fatigue penalty starts or ends,
        [`ProvisionsEvent`][osrlib.crawl.events.ProvisionsEvent] on a day boundary,
        and [`WanderingCheckEvent`][osrlib.crawl.events.WanderingCheckEvent] when a
        wandering-monster check is due.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["move_party"] = "move_party"
    direction: Direction
    """Which way to step. The party turns to face that way as it goes, so a move is also a turn.
    To change facing without moving, use [`TurnParty`][osrlib.crawl.commands.TurnParty]. A
    direction with a wall, a closed door, an undiscovered secret door, or the edge of the map
    on that side of the party's cell gets the refusal `exploration.move.blocked`, unless a
    passage stands on that side. Read
    [`ExploredLevelView.edges`][osrlib.crawl.views.ExploredLevelView.edges] to find which sides
    of the cell the party can leave by."""


class TurnParty(Command):
    """Turn the party in place to a new facing (zero time).

    The party must already be in a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]). A turn never fails once the
    session is `exploring`.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` - the session is not exploring a dungeon.

    Events:
        [`PartyMovedEvent`][osrlib.crawl.events.PartyMovedEvent] with the unchanged
        position and the new facing.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["turn_party"] = "turn_party"
    facing: Direction
    """The direction the party ends up facing. Its position doesn't change and no game time
    passes. The new facing is on the
    [`PartyMovedEvent`][osrlib.crawl.events.PartyMovedEvent], so you can redraw a first-person
    view from that event."""


class ReorderParty(Command):
    """Set the party's marching order.

    Marching order is the order the members walk in, front first. It determines who a door trap
    springs on, who reaches into a treasure cache, and who stands in the front rank when a
    fight starts. This command is the only way to change it. It's legal in town and while
    exploring, and the order is fixed once an encounter or battle has begun.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` - an encounter or battle is underway, or the
          session has ended.
        - `exploration.party.bad_order` - `order` doesn't list exactly the current
          members, each once.

    Events:
        None. Read the new order from
        [`PlayerView.party`][osrlib.crawl.views.PlayerView.party], which lists the members in
        marching order.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["reorder_party"] = "reorder_party"
    order: tuple[str, ...] = Field(min_length=1)
    """Every current member's id, dead members included, each exactly once, with the front of
    the marching order first. The ids are the
    [`MemberView.id`][osrlib.crawl.views.MemberView.id] values in
    [`PlayerView.party`][osrlib.crawl.views.PlayerView.party]. Marching order determines who a
    door trap springs on, who reaches into a cache, and who stands in the front rank when a
    fight starts. The session refuses any other list with `exploration.party.bad_order`."""


class OpenDoor(Command):
    """Open an unstuck, unlocked door on one side of the party's cell (zero time).

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]). A closed door blocks
    [`MoveParty`][osrlib.crawl.commands.MoveParty], so open it first and then step
    through. The session refuses a command at an undiscovered secret door exactly as it
    refuses one at a blank wall, so a refusal never reveals a hidden door. To open a stuck
    door, use [`ForceDoor`][osrlib.crawl.commands.ForceDoor]. To open a locked one, use
    [`PickLock`][osrlib.crawl.commands.PickLock] first.

    A room trap that triggers on `open` belongs to the area on either side of the door.
    Opening the door gives each such trap a 2-in-6 chance to spring on the first living
    member in marching order, unless the party has already found the trap.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` - the session is not exploring a dungeon.
        - `exploration.door.no_door` - no known door on that side of the cell
          (undiscovered secret doors included).
        - `exploration.door.already_open` - the door already stands open.
        - `exploration.door.locked` - the door is still locked. Pick the lock with
          [`PickLock`][osrlib.crawl.commands.PickLock] first.
        - `exploration.door.stuck` - the door is stuck. Force it with
          [`ForceDoor`][osrlib.crawl.commands.ForceDoor].
        - `exploration.door.gate_refused` - the door has an authored
          condition ([`GateSpec`][osrlib.crawl.gates.GateSpec]) the party doesn't
          meet. The rejection includes the gate's authored refusal text. The session checks
          the gate after every other check, so this code means the gate is the only obstacle.

    Events:
        [`ItemConsumedEvent`][osrlib.crawl.events.ItemConsumedEvent] first when
        the gate takes an item as a toll, then
        [`DoorEvent`][osrlib.crawl.events.DoorEvent] with code
        `exploration.door.opened`, which includes the gate's success text when its author
        wrote one. A door trap's spring check then produces a
        [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent] and a
        [`TrapEvent`][osrlib.crawl.events.TrapEvent], and a trap that springs
        resolves at once: a
        [`SavingThrowRolledEvent`][osrlib.core.events.SavingThrowRolledEvent] when
        it allows a save,
        [`DamageDealtEvent`][osrlib.core.events.DamageDealtEvent] and
        [`HitPointsReportedEvent`][osrlib.core.events.HitPointsReportedEvent] for
        damage, [`EffectAttachedEvent`][osrlib.core.events.EffectAttachedEvent] and
        [`ConditionGainedEvent`][osrlib.core.events.ConditionGainedEvent] for a
        condition it inflicts, and
        [`DeathEvent`][osrlib.core.events.DeathEvent] when it kills outright.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.commands import EnterDungeon, MoveParty, OpenDoor
        from osrlib.crawl.dungeon import Direction, DoorSpec, DungeonSpec, Edge, EdgeKind, LevelSpec, edge_key
        from osrlib.crawl.party import Party
        from osrlib.crawl.session import GameSession
        from osrlib.messages import format_message

        stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
        hero = create_character(
            name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=Ruleset(), stream=stream
        ).character
        door = Edge(kind=EdgeKind.DOOR, door=DoorSpec())
        level = LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={edge_key((0, 0), Direction.EAST): door})
        crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
        adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))
        session = GameSession.new(Party(members=[hero]), adventure, seed=7)
        session.execute(EnterDungeon(dungeon_id="crypt"))

        # The closed door blocks the way east until the party opens it.
        print(session.execute(MoveParty(direction=Direction.EAST)).rejections[0].code)
        # exploration.move.blocked

        result = session.execute(OpenDoor(direction=Direction.EAST))
        print([format_message(event) for event in result.events])
        # ['The door east of (0, 0) opens.']
        print(session.execute(MoveParty(direction=Direction.EAST)).accepted)
        # True
        ```
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["open_door"] = "open_door"
    direction: Direction
    """Which side of the party's cell the door is on. Read the cell's sides from
    [`ExploredLevelView.edges`][osrlib.crawl.views.ExploredLevelView.edges]: you can name any
    side whose edge has kind `door`. An undiscovered secret door shows as a wall until a
    `secret_doors` [`Search`][osrlib.crawl.commands.Search] finds it."""


class CloseDoor(Command):
    """Close an open door on one side of the party's cell (zero time).

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]). You rarely need this command,
    because a door the party opened swings shut by itself once the party walks away from it,
    unless it's wedged.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` - the session is not exploring a dungeon.
        - `exploration.door.no_door` - no known door on that side of the cell.
        - `exploration.door.already_closed` - the door is already closed.
        - `exploration.door.wedged` - a wedged door cannot swing.

    Events:
        [`DoorEvent`][osrlib.crawl.events.DoorEvent] with code
        `exploration.door.closed`.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["close_door"] = "close_door"
    direction: Direction
    """Which side of the party's cell the door is on. Only a door the party knows about counts,
    so this is one of the `door` edges in
    [`ExploredLevelView.edges`][osrlib.crawl.views.ExploredLevelView.edges]. A wedged door
    can't close, and the session refuses it with `exploration.door.wedged`."""


class ForceDoor(Command):
    """Force a stuck door open with one member's STR-based open-doors roll (zero time).

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]). Every attempt makes noise, so
    the next wandering-monster check gets the noise bonus. A failed attempt also alerts the
    area beyond the door, so its monsters can't be surprised when the party meets them
    there. A door that gives way counts as opened: each room trap that triggers on `open`
    in the area on either side gets its 2-in-6 chance to spring, unless the party has found
    it. The trap springs on the member who forced the door, or on the next member standing
    if an earlier spring killed them.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` - the session is not exploring a dungeon.
        - `session.command.unknown_member` - `character_id` matches no party member.
        - `session.command.member_incapacitated` - the member cannot act.
        - `exploration.door.no_door` - no known door on that side of the cell.
        - `exploration.door.already_open` - the door already stands open.
        - `exploration.door.locked` - the door is locked, and forcing doesn't open a
          lock. Pick it with [`PickLock`][osrlib.crawl.commands.PickLock] first.
        - `exploration.door.not_stuck` - the door isn't stuck. Open it with
          [`OpenDoor`][osrlib.crawl.commands.OpenDoor].
        - `exploration.door.gate_refused` - the door has an authored
          condition ([`GateSpec`][osrlib.crawl.gates.GateSpec]) the party doesn't
          meet. The session checks the gate before the attempt, so a refused attempt
          makes no noise, alerts nobody, and costs no roll.

    Events:
        [`ItemConsumedEvent`][osrlib.crawl.events.ItemConsumedEvent] first when
        the gate takes an item as a toll, then
        [`DoorEvent`][osrlib.crawl.events.DoorEvent] with code
        `exploration.door.forced` on success, which includes the gate's success text
        when its author wrote one, or `exploration.door.stuck` on failure. A door that
        gives way is followed by the trap events of each door trap's spring check, as
        for [`OpenDoor`][osrlib.crawl.commands.OpenDoor].
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["force_door"] = "force_door"
    direction: Direction
    """Which side of the party's cell the stuck door is on, from the `door` edges in
    [`ExploredLevelView.edges`][osrlib.crawl.views.ExploredLevelView.edges]. The session refuses a door
    that isn't stuck with `exploration.door.not_stuck`. Open that one with
    [`OpenDoor`][osrlib.crawl.commands.OpenDoor], which makes no noise."""
    character_id: str
    """The member who tries to force the door, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. The member's STR sets the open-doors
    chance, so send the strongest member. If the door gives way, a door trap the party hasn't
    found springs on this member."""


class WedgeDoor(Command):
    """Wedge a door with an iron spike so it can't swing shut or be closed (zero time).

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]). The spike comes from the first
    living member in marching order who carries iron spikes, and one spike is used up.
    No play command removes the spike. Only the referee's
    [`SetDoorState`][osrlib.crawl.commands.SetDoorState] with `wedged=False` does.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` - the session is not exploring a dungeon.
        - `exploration.door.no_door` - no known door on that side of the cell.
        - `exploration.door.wedged` - the door is already wedged.
        - `exploration.door.no_spike` - no living member carries iron spikes.

    Events:
        [`ItemConsumedEvent`][osrlib.crawl.events.ItemConsumedEvent] for the spike,
        then [`DoorEvent`][osrlib.crawl.events.DoorEvent] with code
        `exploration.door.wedged`.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["wedge_door"] = "wedge_door"
    direction: Direction
    """Which side of the party's cell the door is on, from the `door` edges in
    [`ExploredLevelView.edges`][osrlib.crawl.views.ExploredLevelView.edges]. Wedging holds the
    door as it stands, open or shut. To keep a way back, wedge an open door before the party
    walks through it."""


class ListenAtDoor(Command):
    """Listen at a door for noise from the area beyond it (zero time).

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]). The listener needs light, or
    infravision. Each member gets one attempt at each door for the rest of the game. A
    listener who hears the monsters beyond the door keeps them from surprising the party
    when it meets them there. Undead make no noise, so an area that contains only undead
    always sounds silent.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` - the session is not exploring a dungeon.
        - `session.command.unknown_member` - `character_id` matches no party member.
        - `session.command.member_incapacitated` - the member cannot act.
        - `exploration.door.no_door` - no known door on that side of the cell.
        - `exploration.action.requires_light` - the party is in the dark and the
          listener lacks infravision.
        - `exploration.listen.already_tried` - this character has already listened
          at this door.

    Events:
        [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent] with the
        roll, then [`ListenedEvent`][osrlib.crawl.events.ListenedEvent] with code
        `exploration.listen.heard` or `exploration.listen.silent`.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["listen_at_door"] = "listen_at_door"
    direction: Direction
    """Which side of the party's cell the door is on, from the `door` edges in
    [`ExploredLevelView.edges`][osrlib.crawl.views.ExploredLevelView.edges]. Listening reaches
    only the area directly beyond that door."""
    character_id: str
    """The member who listens, by [`MemberView.id`][osrlib.crawl.views.MemberView.id]. Each
    member gets one attempt at each door for the rest of the game, so send the best listener
    first. A thief hears on the hear-noise chance for their level. A dwarf, an elf, or a
    halfling hears on 2 in 6, and every other class on 1 in 6."""


class PickLock(Command):
    """Pick the lock of a locked door with a thief's skill and thieves' tools (one turn).

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]). Only a member with thief
    skills can pick a lock. That member needs thieves' tools in their pack and light,
    and infravision isn't enough. A member who fails can't try that lock again until they
    gain a level. A picked door is unlocked but still closed, so follow with
    [`OpenDoor`][osrlib.crawl.commands.OpenDoor].

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` - the session is not exploring a dungeon.
        - `session.command.unknown_member` - `character_id` matches no party member.
        - `session.command.member_incapacitated` - the member cannot act.
        - `exploration.lock.not_a_thief` - the member has no thief skills.
        - `exploration.lock.no_tools` - the member carries no thieves' tools.
        - `exploration.door.no_door` - no known door on that side of the cell.
        - `exploration.lock.not_locked` - the door has no lock left to pick.
        - `exploration.action.requires_light` - the party has no light. Infravision
          isn't enough to pick a lock.
        - `exploration.lock.locked_out` - this member already failed at this lock at
          their current level.

    Events:
        [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent] with the
        skill roll and, on success, a [`DoorEvent`][osrlib.crawl.events.DoorEvent]
        with code `exploration.door.unlocked`. The attempt costs one turn, and the turn's
        bookkeeping follows: light burning down, the fatigue rule, and the wandering-monster
        check, each with its usual events.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["pick_lock"] = "pick_lock"
    direction: Direction
    """Which side of the party's cell the locked door is on, from the `door` edges in
    [`ExploredLevelView.edges`][osrlib.crawl.views.ExploredLevelView.edges]. The session refuses a door
    that isn't locked with `exploration.lock.not_locked`."""
    character_id: str
    """The thief who picks, by [`MemberView.id`][osrlib.crawl.views.MemberView.id]. The member
    needs thief skills and thieves' tools in their pack. A member who fails can't try that lock
    again until they gain a level, so send the best lock picker first."""


class Search(Command):
    """Search the party's cell for one kind of hidden feature (one turn).

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]). The searcher needs light, or
    infravision. Each member gets one attempt for each kind on each cell, for the rest of
    the game. A trap the party has found never springs.

    A `room_traps` search also covers the cell's known doors. A room trap that triggers on
    `open`, in the area beyond a known door, can be found from this side of the door.

    An undiscovered secret door hides any trap behind it from the cells on both sides. So
    when a member discovers a secret door, every member gets their `room_traps` attempt back
    on both cells the door joins, and can search again for the trap behind it. The referee's
    [`SetDoorState`][osrlib.crawl.commands.SetDoorState] with `discovered=True` gives the
    attempts back the same way. Attempts of other kinds, and attempts on every other cell,
    stay used.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` - the session is not exploring a dungeon.
        - `session.command.unknown_member` - `character_id` matches no party member.
        - `session.command.member_incapacitated` - the member cannot act.
        - `exploration.action.requires_light` - the party is in the dark and the
          searcher lacks infravision.
        - `exploration.search.already_tried` - this member already searched this
          cell for this kind.

    Events:
        [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent] with the
        roll, a [`TrapEvent`][osrlib.crawl.events.TrapEvent] for each room trap
        found, then
        [`SearchCompletedEvent`][osrlib.crawl.events.SearchCompletedEvent] listing
        what turned up. For a trap found through a door, the events record that door
        twice: in the trap event's `direction`, and as a third part of the token in
        `found`, `"room_trap:<area id>:<direction>"`. For a trap found inside the
        party's area, they record no door. One turn passes, with the turn's usual events.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["search"] = "search"
    character_id: str
    """The member who searches, by [`MemberView.id`][osrlib.crawl.views.MemberView.id]. The
    chance depends on the member's class, so match the searcher to `kind`. An elf finds secret
    doors on 2 in 6. A dwarf finds room traps and construction tricks on 2 in 6. Every other
    class has 1 in 6 for secret doors and room traps, and no chance at construction tricks."""
    kind: Literal["secret_doors", "room_traps", "construction"]
    """Which kind of hidden feature to look for. `secret_doors` checks the four sides of the
    party's cell. `room_traps` checks the cell's area and the areas behind its known doors,
    so a trap that springs when a door opens can be found from the corridor first.
    `construction` checks the cell for authored construction tricks, like a sliding wall or a
    false floor. Each member gets one attempt for each kind on each cell, so a party covers a
    cell by sending different members, or by sending one member once for each kind."""


class InspectTreasure(Command):
    """Have a thief check a treasure cache on the party's cell for a trap (one turn).

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]), and it needs light: infravision
    isn't enough. Only a member with thief skills can find a treasure trap, and each member
    gets one attempt at each cache. Remove a trap found here with
    [`RemoveTreasureTrap`][osrlib.crawl.commands.RemoveTreasureTrap]. Until then, finding
    the trap doesn't disarm it, and it still gets its chance to spring on every
    [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure]. Only an authored cache can be
    trapped. Inspecting a cache the engine generated always finds nothing.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` - the session is not exploring a dungeon.
        - `session.command.unknown_member` - `character_id` matches no party member.
        - `session.command.member_incapacitated` - the member cannot act.
        - `exploration.trap.not_a_thief` - the member has no thief skills.
        - `exploration.feature.unknown` - `feature_id` matches no treasure cache on
          this cell.
        - `exploration.action.requires_light` - the party has no light. Infravision
          isn't enough to inspect a cache.
        - `exploration.search.already_tried` - this member already inspected
          this cache.

    Events:
        [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent] with the
        skill roll, then either a [`TrapEvent`][osrlib.crawl.events.TrapEvent] with code
        `exploration.trap.found` or a
        [`SearchCompletedEvent`][osrlib.crawl.events.SearchCompletedEvent]
        reporting nothing. One turn passes, with the turn's usual events.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["inspect_treasure"] = "inspect_treasure"
    character_id: str
    """The thief who inspects, by [`MemberView.id`][osrlib.crawl.views.MemberView.id]. Only a
    member with thief skills can find a treasure trap, and each one gets a single attempt per
    cache."""
    feature_id: str
    """Which cache to check for a trap. Name an authored cache by its
    [`FeatureSpec.id`][osrlib.crawl.dungeon.FeatureSpec.id] in the adventure document. Name a
    cache the engine generated by the
    [`HoardGeneratedEvent.cache_ref`][osrlib.crawl.events.HoardGeneratedEvent.cache_ref] the
    session emitted when it generated the cache. That event has referee visibility, and the
    player view doesn't list generated caches, so read the id from the events of the command
    that generated it. The cache has to be on the party's cell."""


class RemoveTreasureTrap(Command):
    """Have a thief disarm a treasure trap the party has already found (one turn).

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]), and it needs light: infravision
    isn't enough. A member must already have found the trap with
    [`InspectTreasure`][osrlib.crawl.commands.InspectTreasure]. Only a member with thief
    skills can try, and each member gets one attempt at each trap. A failed attempt springs
    the trap on the thief who tried. Once the trap is removed,
    [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure] empties the cache safely.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` - the session is not exploring a dungeon.
        - `session.command.unknown_member` - `character_id` matches no party member.
        - `session.command.member_incapacitated` - the member cannot act.
        - `exploration.trap.not_a_thief` - the member has no thief skills.
        - `exploration.feature.unknown` - `feature_id` matches no trapped cache on
          this cell.
        - `exploration.trap.not_found` - nobody has found the trap yet.
        - `exploration.trap.already_resolved` - the trap was already removed or has
          already sprung.
        - `exploration.action.requires_light` - the party has no light. Infravision
          isn't enough to remove a trap.
        - `exploration.search.already_tried` - this member already tried to remove
          this trap.

    Events:
        [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent] with the
        skill roll, then a [`TrapEvent`][osrlib.crawl.events.TrapEvent]:
        `exploration.trap.removed` on success, `exploration.trap.sprung` on failure.
        The sprung trap resolves at once against the thief, or against every living
        member when the trap catches the whole party. Each step is a separate event:
        a [`SavingThrowRolledEvent`][osrlib.core.events.SavingThrowRolledEvent] when
        the trap allows a save,
        [`DamageDealtEvent`][osrlib.core.events.DamageDealtEvent] and
        [`HitPointsReportedEvent`][osrlib.core.events.HitPointsReportedEvent] for
        damage, [`EffectAttachedEvent`][osrlib.core.events.EffectAttachedEvent] and
        [`ConditionGainedEvent`][osrlib.core.events.ConditionGainedEvent] for a
        condition, [`DeathEvent`][osrlib.core.events.DeathEvent] when it kills
        outright, and
        [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent], `via`
        `"trap"`, when it drops the party somewhere else. One turn passes.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["remove_treasure_trap"] = "remove_treasure_trap"
    character_id: str
    """The thief who works on the trap, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. A failed attempt springs the trap on
    this member."""
    feature_id: str
    """Which trapped cache to disarm, by its
    [`FeatureSpec.id`][osrlib.crawl.dungeon.FeatureSpec.id] in the adventure document. Only an
    authored cache can be trapped, so the session refuses the id of a cache the engine generated
    with `exploration.feature.unknown`. The trap has to have been found already, or the command
    comes back with `exploration.trap.not_found`."""


class TakeTreasure(Command):
    """Empty a treasure cache or a pile of dropped goods into the party's packs (one turn).

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]). `feature_id` is the id
    of an authored cache or a cache the engine generated, or the literal `"pile"` for goods
    dropped on the party's cell.

    By default the haul spreads across the living members. Each item goes to a member
    whose class can use it, so the fighter takes the plate mail and the magic-user takes
    the arcane scroll. Gems and jewellery divide by worth, and coins divide evenly,
    denomination by denomination. No member is loaded past the 1,600-coin maximum load, so
    a pickup can't stop the party moving. The party moves at its slowest member's rate,
    so a heavy load on one member slows everyone. Name `recipient_id` to have one member
    take the whole haul instead, up to their maximum load. Whatever the carriers can't
    carry stays in the pile on the cell, and the party can lighten its load and come back
    for it. To change who carries what afterwards, use
    [`GiveItems`][osrlib.crawl.commands.GiveItems]. Under the immediate XP timing, the
    treasure's XP divides evenly among the living members however the goods end up split,
    as the OSE SRD's rule for treasure XP says.

    The named recipient, or the first living member in marching order when nobody is named,
    is the one who reaches in. A cache whose trap hasn't been removed gets its chance to
    spring on that member first.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` - the session is not exploring a dungeon.
        - `session.command.no_living_members` - nobody is left alive to carry anything.
        - `session.command.unknown_member` - `recipient_id` matches no party member.
        - `session.command.member_incapacitated` - the recipient can't act.
        - `exploration.feature.unknown` - no cache by that id is on this cell, or there's
          no pile on it.
        - `exploration.feature.emptied` - the cache has already been emptied.

    Events:
        When the cache has a trap that hasn't been removed or sprung, its spring check comes
        first: a [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent], a
        [`TrapEvent`][osrlib.crawl.events.TrapEvent], and the trap's resolution
        when it springs. Then comes one
        [`ItemAcquiredEvent`][osrlib.crawl.events.ItemAcquiredEvent] for each member
        who took something, in marching order, listing their goods and coin value with
        `origin` `"treasure"`. An
        [`ItemsLeftBehindEvent`][osrlib.crawl.events.ItemsLeftBehindEvent] follows
        when the party couldn't carry it all. Under the immediate XP timing, an
        [`XpAwardedEvent`][osrlib.crawl.events.XpAwardedEvent] follows for each living member.
        One turn passes. A trap that kills the whole party leaves the cache full, and only
        the turn passes.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.events import Visibility
        from osrlib.core.items import Coins
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.commands import EnterDungeon, TakeTreasure
        from osrlib.crawl.dungeon import DungeonSpec, FeatureSpec, LevelSpec
        from osrlib.crawl.party import Party
        from osrlib.crawl.session import GameSession
        from osrlib.messages import format_message

        stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
        hero = create_character(
            name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=Ruleset(), stream=stream
        ).character
        chest = FeatureSpec(id="chest", kind="treasure_cache", cell=(0, 0), coins=Coins(gp=50), item_ids=("rope",))
        level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0), features=(chest,))
        crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
        adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))
        session = GameSession.new(Party(members=[hero]), adventure, seed=7)
        session.execute(EnterDungeon(dungeon_id="crypt"))

        result = session.execute(TakeTreasure(feature_id="chest"))
        print([format_message(event) for event in result.events if event.visibility is Visibility.PLAYER])
        # ['character-0001 acquires rope and 50 gp in coin.']

        # The chest is empty now, so a second try is refused.
        print(session.execute(TakeTreasure(feature_id="chest")).rejections[0].code)
        # exploration.feature.emptied
        ```
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["take_treasure"] = "take_treasure"
    feature_id: str
    """What to empty. Name an authored cache by its
    [`FeatureSpec.id`][osrlib.crawl.dungeon.FeatureSpec.id]. Name a cache the engine generated
    by the
    [`HoardGeneratedEvent.cache_ref`][osrlib.crawl.events.HoardGeneratedEvent.cache_ref] the
    session emitted when it generated the cache. The literal `"pile"` means the goods dropped
    on the party's cell, which
    [`PlayerView.piles`][osrlib.crawl.views.PlayerView.piles] lists by cell reference. The session
    refuses a cache that's already emptied with `exploration.feature.emptied`."""
    recipient_id: str | None = None
    """One member to take the whole haul, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id], or `None` to spread it across the
    living members. The named member fills their pack up to their maximum load, and the rest
    stays on the cell. Name a recipient to keep a specific item with a specific member. The
    named member is also the one who reaches in, so a trap that hasn't been removed springs
    on them. If that trap leaves them unable to act, the haul spreads across the living
    members instead."""


class DropItems(Command):
    """Drop items and coins from one member onto the party's cell, or behind a fleeing party.

    It's legal while exploring a dungeon and during an encounter. Goods dropped on the cell
    go into its pile, and [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure] with
    `feature_id="pile"` picks them back up. While exploring, a drop takes no time.

    During an encounter, the drop uses up the encounter round, and the monsters then act on
    their reaction to the party, as for a [`Wait`][osrlib.crawl.commands.Wait]. While
    monsters chase the party after an
    [`Evade`][osrlib.crawl.commands.Evade], dropped goods scatter behind the party instead of
    landing in a pile, and can't be picked back up. Dropped coins tempt intelligent pursuers
    and dropped rations tempt unintelligent ones, which can end the chase.

    Modes:
        `exploring`, `encounter`

    Rejections:
        - `session.command.wrong_mode` - the session is in town or in battle, or has
          ended.
        - `session.command.unknown_member` - `character_id` matches no party member.
        - `session.command.member_incapacitated` - the member can't act.
        - `items.curse.stuck` - a cursed item whose curse has been revealed can't be
          dropped.
        - `exploration.item.not_carried` - the member doesn't carry one of the items, or
          enough of the coins.
        - `encounter.none_active` - a second check behind the mode gate, which normal
          play never reaches.

    Events:
        [`ItemsDroppedEvent`][osrlib.crawl.events.ItemsDroppedEvent] with what
        was dropped. In an encounter, the encounter round's events follow. During a chase,
        a [`PursuitEvent`][osrlib.crawl.events.PursuitEvent] round follows instead, with
        the drop as bait.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING, SessionMode.ENCOUNTER})

    command_type: Literal["drop_items"] = "drop_items"
    character_id: str
    """The member whose pack and purse the goods leave, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]."""
    item_ids: tuple[str, ...] = ()
    """What to drop, one unit per entry, so repeat an id to drop more than one. Name a mundane
    item by its catalog id, like `torch` or `rope_hemp`. Name a magic item, a gem, or a piece
    of jewellery by the per-instance id it has in
    [`MemberView.inventory`][osrlib.crawl.views.MemberView.inventory]. The session refuses
    the same magic item named twice with `exploration.item.not_carried`, because the whole item
    leaves with the first entry. A cursed item whose curse has been revealed can't be dropped."""
    coins: Coins = Coins()
    """Coins to drop with the items, by denomination. The default drops none. Coins dropped on
    the cell go into its pile with everything else, and the party can pick them back up with
    [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure] naming `"pile"`."""


class GiveItems(Command):
    """Hand items and coins from one party member to another (zero time).

    Use it to spread the load across the party, or to put an item in the hands of the
    member who can use it. `character_id` is the giver, and `recipient_id` is the member
    who takes the goods. A magic item that was equipped releases its effects and arrives
    unequipped in the recipient's pack. It's legal in town and while exploring, but not
    during an encounter or a battle. Both members must be alive and able to act.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` - an encounter or battle is underway, or the
          session has ended.
        - `session.command.unknown_member` - `character_id` or `recipient_id` matches
          no party member.
        - `session.command.member_incapacitated` - the giver or the recipient can't
          act.
        - `exploration.give.same_member` - the giver and the recipient are the same member.
        - `items.curse.stuck` - a cursed item whose curse has been revealed can't be
          handed over.
        - `exploration.item.not_carried` - the giver doesn't carry one of the items, or
          enough of the coins.

    Events:
        [`EffectReleasedEvent`][osrlib.core.events.EffectReleasedEvent]s for the effects of
        an equipped magic item, then an
        [`ItemsGivenEvent`][osrlib.crawl.events.ItemsGivenEvent] with what changed hands.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["give_items"] = "give_items"
    character_id: str
    """The member handing the goods over, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. The session refuses the recipient's id
    here with `exploration.give.same_member`."""
    recipient_id: str
    """The member taking the goods, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. Both members have to be alive and able
    to act."""
    item_ids: tuple[str, ...] = ()
    """What changes hands, one unit per entry, so repeat an id to give more than one. Name items
    the same way as in [`DropItems.item_ids`][osrlib.crawl.commands.DropItems.item_ids]: a
    catalog id for a mundane item, and a per-instance id for a magic item, a gem, or a piece
    of jewellery. A magic item that was equipped releases its effects and arrives unequipped
    in the recipient's pack."""
    coins: Coins = Coins()
    """Coins to hand over, by denomination. The default hands over none. Coins move from the
    giver's purse to the recipient's, which is how a party evens out the weight after a big
    haul."""


class LightSource(Command):
    """Light a torch or a lantern, or set fire to a dropped flask of oil (one round).

    It's legal in town and while exploring. When no living member already has a flame
    burning, the member needs a tinder box, and each strike lights on a 2 in 6. Each
    attempt takes a round, whether or not it lights, as the OSE SRD's tinder box entry says.
    Lighting `oil_flask` sets fire to a flask already dropped on the party's cell, which
    makes a pool of burning oil there.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` - an encounter or battle is underway, or the
          session has ended.
        - `session.command.unknown_member` - `character_id` matches no party member.
        - `session.command.member_incapacitated` - the member can't act.
        - `exploration.light.not_a_source` - `item_id` isn't `torch`, `lantern`,
          or `oil_flask`.
        - `exploration.item.not_carried` - the member doesn't carry the torch or the
          lantern, or carries a lantern and no oil flask. For `oil_flask`, no dropped
          flask is on the cell, which is always so in town.
        - `exploration.light.no_flame` - no flame is burning in the party, and the member
          has no tinder box.

    Events:
        An [`EffectAttachedEvent`][osrlib.core.events.EffectAttachedEvent] for the light's
        burn time, then a [`LightEvent`][osrlib.crawl.events.LightEvent] with code
        `exploration.light.lit`. A strike that doesn't light produces only a `LightEvent` with
        code `exploration.light.failed`. One round passes either way.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["light_source"] = "light_source"
    character_id: str
    """The member who strikes the light and then carries it, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. The light goes with this member, so the
    party loses it when this member dies."""
    item_id: str
    """Which source to light: `torch`, `lantern`, or `oil_flask`. Lighting a torch uses it up,
    and it burns for a torch's full duration. A lantern burns one oil flask from the member's
    pack, and the member keeps the lantern. `oil_flask` sets fire to a flask already dropped on
    the party's cell, not one in the pack, so drop it with
    [`DropItems`][osrlib.crawl.commands.DropItems] first. The session refuses any other id with
    `exploration.light.not_a_source`."""


class ExtinguishSource(Command):
    """Put out every torch and lantern one member has burning (zero time).

    It's legal in town and while exploring. The remaining burn time is lost. A doused torch
    is gone, and a doused lantern's oil is gone too, so lighting it again with
    [`LightSource`][osrlib.crawl.commands.LightSource] burns a new flask. A light from a
    spell or a magic sword isn't affected.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` - an encounter or battle is underway, or the
          session has ended.
        - `session.command.unknown_member` - `character_id` matches no party member.
        - `session.command.member_incapacitated` - the member can't act.
        - `exploration.light.not_burning` - the member has no torch or lantern burning.

    Events:
        For each doused source, an
        [`EffectReleasedEvent`][osrlib.core.events.EffectReleasedEvent] and a
        [`LightEvent`][osrlib.crawl.events.LightEvent] with code
        `exploration.light.extinguished`.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["extinguish_source"] = "extinguish_source"
    character_id: str
    """The member whose light goes out, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. Every torch and lantern this member has
    burning goes out, and the remaining burn time is lost rather than saved for later."""


class EquipItem(Command):
    """Equip an item from a member's pack: wear armour or a ring, or wield a weapon (zero time).

    It's legal in town and while exploring. A weapon has to be equipped before a member can
    attack with it in battle. The session checks the member's class rules for armour and
    weapons before anything changes. Name a magic item by its instance id, and a mundane
    item by its catalog id, because mundane items have no per-instance id. A mundane id is
    either a built-in one from [`load_equipment`][osrlib.data.load_equipment] (see the
    [equipment id index](https://mmacy.github.io/osrlib-python/reference/content-ids/equipment/))
    or one the adventure bundles, which isn't in that index.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` - an encounter or battle is underway, or the
          session has ended.
        - `session.command.unknown_member` - `character_id` matches no party member.
        - `session.command.member_incapacitated` - the member can't act.
        - `exploration.item.not_carried` - nothing by that id is in the member's pack.
        - `items.equip.armour_forbidden`, `items.equip.armour_not_allowed`,
          `items.equip.shield_forbidden`, `items.equip.weapon_not_allowed`,
          `items.equip.weapon_forbidden` - the member's class may not use it.
        - `items.equip.two_handed_with_shield` - the member would hold a two-handed
          weapon and a shield together.
        - `items.equip.not_equippable` - potions, scrolls, ammunition, and gear with no
          combat use can't be equipped.
        - `items.equip.not_usable` - the member's class can't use the magic item.
        - `items.ring.hands_full` - the member already wears two rings.

    Events:
        Usually none. Equipping a magic item that works while it's worn can attach its
        effects ([`EffectAttachedEvent`][osrlib.core.events.EffectAttachedEvent]). Putting
        on a cursed ring identifies it and reveals its curse
        ([`ItemIdentifiedEvent`][osrlib.crawl.events.ItemIdentifiedEvent],
        [`CurseRevealedEvent`][osrlib.crawl.events.CurseRevealedEvent]).
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["equip_item"] = "equip_item"
    character_id: str
    """The member doing the equipping, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. What they may wear and
    wield depends on their class."""
    item_id: str
    """What to equip, which has to be in this member's pack already. Name a magic item by its
    per-instance id, because each one is a distinct object. Name a mundane item by its catalog
    id, because mundane items stack and have no per-instance id. Both ids are in
    [`MemberView.inventory`][osrlib.crawl.views.MemberView.inventory]."""


class UnequipItem(Command):
    """Take off or put away an equipped item, returning it to the member's pack (zero time).

    It's legal in town and while exploring. A cursed item whose curse has been revealed
    can't be taken off until a *remove curse* lifts the curse, from a caster or from the
    temple's [`PurchaseHealing`][osrlib.crawl.commands.PurchaseHealing].

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` - an encounter or battle is underway, or the
          session has ended.
        - `session.command.unknown_member` - `character_id` matches no party member.
        - `session.command.member_incapacitated` - the member can't act.
        - `exploration.item.not_equipped` - the member has nothing by that id equipped.
        - `items.curse.stuck` - a cursed item whose curse has been revealed can't be
          taken off.

    Events:
        Usually none. Taking off a magic item that works while it's worn releases its effects
        ([`EffectReleasedEvent`][osrlib.core.events.EffectReleasedEvent]).
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["unequip_item"] = "unequip_item"
    character_id: str
    """The member doing the unequipping, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]."""
    item_id: str
    """What to take off or put away. Name it the same way as in
    [`EquipItem.item_id`][osrlib.crawl.commands.EquipItem.item_id]. The item has to be equipped
    now, not in the pack. A cursed item whose curse has been revealed stays equipped until a
    *remove curse* lifts the curse."""


class Rest(Command):
    """Rest the party for one turn, a night (48 turns), or a full day (144 turns).

    It's legal in town and while exploring. A rest clears the party's fatigue, and a night or
    a day of rest counts as sleep, which casters need before
    [`PrepareSpells`][osrlib.crawl.commands.PrepareSpells]. A full day of rest also applies
    natural healing to every living member. In a dungeon, a wandering-monster check that
    turns up monsters interrupts the rest and opens an encounter. An interrupted rest still
    counts the full turns the party rested before the monsters came. One full turn clears
    fatigue, and each full turn counts toward the three that clear exhaustion. A rest
    interrupted during its first turn clears nothing. An interrupted rest isn't sleep and
    heals nobody.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` - an encounter or battle is underway, or the
          session has ended.

    Events:
        The elapsed turns' bookkeeping comes first: light burning down, provisions, and
        wandering-monster checks. Then comes a [`RestedEvent`][osrlib.crawl.events.RestedEvent]
        with code `exploration.rest.rested`. An interrupted rest ends with the encounter's
        opening events and a `RestedEvent` with code `exploration.rest.interrupted`
        instead. A rest that clears fatigue or exhaustion, interrupted or not, adds a
        [`FatigueEvent`][osrlib.crawl.events.FatigueEvent] or an
        [`ExhaustionEvent`][osrlib.crawl.events.ExhaustionEvent], and a full day's natural
        healing adds a [`HealingAppliedEvent`][osrlib.core.events.HealingAppliedEvent]
        for each member it heals.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["rest"] = "rest"
    kind: Literal["turn", "night", "day"]
    """How long to rest. `turn` is a single turn, the rest a party in a dungeon needs once every
    six turns to avoid fatigue. `night` is 48 turns and counts as sleep. `day` is 144 turns,
    counts as sleep, and applies natural healing to every living member. Sleep is what
    [`PrepareSpells`][osrlib.crawl.commands.PrepareSpells] needs, so a caster's day starts with
    a `night` or a `day` rest. Exhaustion from a chase clears after three turns of rest in
    total. In a dungeon, a wandering encounter can cut any of the three short."""


class PrepareSpells(Command):
    """Choose the spells a caster memorizes, replacing what they had memorized (six turns).

    It's legal in town and while exploring. The caster must have slept since their last
    preparation: a `night` or `day` [`Rest`][osrlib.crawl.commands.Rest] that a
    wandering encounter didn't interrupt. In a new session the party hasn't slept yet, so a
    caster's first preparation comes after a rest. The selections replace the memorized list rather
    than adding to it. Cast what the caster memorized with
    [`CastSpell`][osrlib.crawl.commands.CastSpell] outside battle, or with a `cast`
    [`BattleDeclaration`][osrlib.crawl.commands.BattleDeclaration] in one.
    The `CastSpell` example shows a caster resting, preparing, and casting.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` - an encounter or battle is underway, or the
          session has ended.
        - `session.command.unknown_member` - `character_id` matches no party member.
        - `session.command.member_incapacitated` - the member can't act.
        - `magic.memorize.needs_sleep` - the party hasn't slept since this caster's last
          preparation.
        - `magic.memorize.not_a_caster` - the member's class casts no spells.
        - `magic.memorize.unknown_spell` - a selection matches no known spell.
        - `magic.memorize.wrong_list` - a selection isn't on the caster's spell list.
        - `magic.memorize.divine_reverses_at_cast` - a selection for a divine caster is
          reversed. A divine caster chooses the reversed form when casting instead.
        - `magic.memorize.not_in_book` - an arcane caster's selection isn't in their
          spell book.
        - `magic.memorize.not_reversible` - a reversed selection has no reversed
          form.
        - `magic.memorize.slots_exceeded` - the selections at some spell level outnumber
          the caster's slots at that level.

    Events:
        [`SpellsMemorizedEvent`][osrlib.core.events.SpellsMemorizedEvent] with the
        memorized list. Six turns pass, with each turn's usual events.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["prepare_spells"] = "prepare_spells"
    character_id: str
    """The caster, by [`MemberView.id`][osrlib.crawl.views.MemberView.id]. The party has to
    have slept since this caster's last preparation, so send a `night` or `day`
    [`Rest`][osrlib.crawl.commands.Rest] first."""
    selections: tuple[MemorizedSpell, ...] = ()
    """The spells to memorize. They replace the caster's current list rather than adding to it.
    Each [`MemorizedSpell`][osrlib.core.spells.MemorizedSpell] has a spell id and, for an
    arcane caster, whether the copy is the reversed form of a reversible spell. Repeat a spell
    to memorize more than one copy of it. The number at each spell level has to fit the
    caster's slots at that level, and an empty tuple clears the list. The caster's current
    list is in
    [`MemberView.memorized_spells`][osrlib.crawl.views.MemberView.memorized_spells]."""


class LearnSpell(Command):
    """Add a spell to an arcane caster's spell book, when they gain a level or a mentor teaches them.

    It's legal in town and while exploring. At each spell level, no more spells fit in the book
    than the caster has slots at that level. The book never shrinks, so a caster
    who lost a level can have more spells in it than their slots, and can't learn more at
    that level until they regain the slots. No game time passes. What the learning costs in
    the story, like a mentor's week or the price of copying a scroll, is up to you.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` - an encounter or battle is underway, or the
          session has ended.
        - `session.command.unknown_member` - `character_id` matches no party member.
        - `session.command.member_incapacitated` - the member can't act.
        - `magic.book.not_arcane` - the member's class isn't an arcane caster.
        - `magic.book.unknown_spell` - `spell_id` matches no spell.
        - `magic.book.wrong_list` - the spell isn't on the caster's spell list.
        - `magic.book.duplicate` - the book already contains the spell.
        - `magic.book.capacity_exceeded` - the book is full at the spell's level.

    Events:
        [`SpellBookUpdatedEvent`][osrlib.core.events.SpellBookUpdatedEvent] with
        the added spell.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["learn_spell"] = "learn_spell"
    character_id: str
    """The arcane caster whose book gets the spell, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. The session refuses a member whose class
    isn't an arcane caster with `magic.book.not_arcane`."""
    spell_id: str
    """The spell to write into the book, from [`load_spells`][osrlib.data.load_spells] (see the
    [spell id index](https://mmacy.github.io/osrlib-python/reference/content-ids/spells/)). It
    has to be on the spell list of the caster's class. At each spell level, no more spells fit in
    the book than the caster has slots at that level."""


class CastSpell(Command):
    """Cast a memorized spell outside battle (one round).

    The caster needs a memorized copy of the spell, which
    [`PrepareSpells`][osrlib.crawl.commands.PrepareSpells] gives them, and casting uses the
    copy up. Each target is an entity id, or a `cell:` reference for a spell that affects a
    place. This command isn't legal during an encounter or a battle. To cast at monsters the
    party has met, open battle with
    [`EngageBattle`][osrlib.crawl.commands.EngageBattle] and declare a `cast`
    [`BattleDeclaration`][osrlib.crawl.commands.BattleDeclaration] in the round.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` - an encounter or battle is underway, or the
          session has ended.
        - `session.command.unknown_member` - `character_id` matches no party member.
        - `session.command.member_incapacitated` - the member can't act.
        - `magic.cast.unknown_spell` - `spell_id` matches no spell.
        - `magic.cast.silenced_area` - a *silence* effect covers the party's cell.
        - `magic.cast.unknown_target` - a target id matches no entity in the session.
        - `magic.cast.not_memorized` - the caster has no memorized copy. A member who
          can't cast spells at all gets this code too.
        - `magic.cast.caster_incapacitated`, `magic.cast.caster_restrained`,
          `magic.cast.anti_magic_shell` - the caster can't cast right now.
        - `magic.cast.not_reversible` - `reversed` is set on a spell with no reversed
          form.
        - `magic.cast.unknown_mode` - `mode` matches no mode of the spell.
        - `magic.cast.target_count` - the number of targets is wrong for the mode.
        - `magic.cast.out_of_range` - a target is beyond the spell's range.

    Events:
        [`SpellCastEvent`][osrlib.core.events.SpellCastEvent], then the events of the
        spell's resolution: saving throws, damage, healing, and effects attached, each a
        separate event. One round passes.

    Examples:
        A magic-user rests in town, prepares *light*, and casts it on themselves in the
        dungeon so the party can see:

        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.core.spells import MemorizedSpell
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.commands import CastSpell, EnterDungeon, PrepareSpells, Rest
        from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
        from osrlib.crawl.party import Party
        from osrlib.crawl.session import GameSession

        stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
        mage = create_character(
            name="Wenna",
            class_id="magic_user",
            alignment=Alignment.NEUTRAL,
            ruleset=Ruleset(),
            stream=stream,
            starting_spell_ids=["light_mu"],
        ).character
        level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
        crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
        adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))
        session = GameSession.new(Party(members=[mage]), adventure, seed=7)

        prepare = PrepareSpells(character_id=mage.id, selections=(MemorizedSpell(spell_id="light_mu"),))
        print(session.execute(prepare).rejections[0].code)  # no sleep yet
        # magic.memorize.needs_sleep
        session.execute(Rest(kind="night"))
        print(session.execute(prepare).accepted)
        # True

        session.execute(EnterDungeon(dungeon_id="crypt"))
        light = CastSpell(character_id=mage.id, spell_id="light_mu", mode="illuminate", targets=(mage.id,))
        result = session.execute(light)
        print([event.code for event in result.events])
        # ['magic.cast.cast', 'effects.effect.attached']
        print(session.party_light())
        # (True, True)
        ```
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["cast_spell"] = "cast_spell"
    character_id: str
    """The caster, by [`MemberView.id`][osrlib.crawl.views.MemberView.id]. The caster needs a
    memorized copy of the spell, and casting uses it up."""
    spell_id: str
    """The spell to cast, from [`load_spells`][osrlib.data.load_spells] (see the
    [spell id index](https://mmacy.github.io/osrlib-python/reference/content-ids/spells/)). The
    session refuses a caster with no memorized copy with `magic.cast.not_memorized`, and refuses a
    member who can't cast spells at all the same way."""
    mode: str
    """Which of the spell's usages to cast, by that usage's
    [`SpellMode.key`][osrlib.core.spells.SpellMode] on the form you're casting. A spell with
    several usages, like *light*, has one mode for each. For a spell with one usage, you still name
    its one key. The session refuses a key the form doesn't have with `magic.cast.unknown_mode`."""
    reversed: bool = False
    """Cast the reversed form, like *cause light wounds* for *cure light wounds*. An arcane
    caster fixes the form when memorizing, so a reversed cast needs a reversed copy memorized.
    A divine caster memorizes the normal form and chooses the form here, when casting. The session
    refuses `reversed` on a spell with no reversed form, with `magic.cast.not_reversible`."""
    targets: tuple[str, ...] = ()
    """Who or what the spell targets, as entity ids: a party member's
    [`MemberView.id`][osrlib.crawl.views.MemberView.id], a monster's id, or, for a spell that
    affects a place rather than a creature, a cell reference in the
    `cell:{dungeon}:{level}:{x},{y}` form that [`cell_ref`][osrlib.crawl.dungeon.cell_ref]
    builds. The mode's targeting sets how many targets it takes, and the session refuses the
    wrong number with `magic.cast.target_count`."""


class UseItem(Command):
    """Use a magic item: drink a potion, read a scroll, or activate a wand, staff, or rod (one round).

    It's legal while exploring and during an encounter. In battle, use an item with a
    `use_item` [`BattleDeclaration`][osrlib.crawl.commands.BattleDeclaration] instead. A
    magic sword that sheds light on command turns its light on or off. Using an item
    identifies it and reveals any curse.

    `target_id` is the id of whoever the item is used on: a party member for a staff of healing's
    touch, or an encounter group for a device that covers an area. When the member reads a
    scroll with several spells on it, choose the spell and its targets with `spell_id`,
    `mode`, and `targets`. They work like the same fields of
    [`CastSpell`][osrlib.crawl.commands.CastSpell].

    Modes:
        `exploring`, `encounter`

    Rejections:
        - `session.command.wrong_mode` - the session is in town or in battle, or has
          ended.
        - `session.command.unknown_member` - `character_id` matches no party member.
        - `session.command.member_incapacitated` - the member can't act.
        - `exploration.item.not_carried` - the member carries no magic item with
          that instance id.
        - `items.use.not_usable` - the item has no use, or the member's class can't
          use the device.
        - Scrolls: `exploration.action.requires_light` (reading needs light, and
          infravision isn't enough), `items.scroll.spent`, `items.scroll.no_such_spell`,
          `items.scroll.wrong_caster`, and the cast checks
          `magic.cast.unknown_target`, `magic.cast.unknown_mode`,
          `magic.cast.target_count`, `magic.cast.out_of_range`,
          `magic.cast.caster_incapacitated`, `magic.cast.caster_restrained`, and
          `magic.cast.anti_magic_shell`. The session checks a read as a cast by a
          caster at the scroll's level, which is the level
          [`cast_from_scroll`][osrlib.core.spells.cast_from_scroll] casts it at. So a
          6th-level reader of a *magic missile* scroll names one target, and the session
          refuses three. The session refuses the read before the scroll is
          used up.
        - Devices: `items.device.inert` (no charges left),
          `items.use.target_required` (an area device used in an encounter needs a
          `target_id`), `items.use.unknown_target`, and `items.use.battle_only` (a device
          that strikes works only as a battle declaration).
        - Potions: `items.use.target_not_allowed` (`target_id` names anyone but the member
          using the potion).

    Events:
        [`ItemIdentifiedEvent`][osrlib.crawl.events.ItemIdentifiedEvent], and
        [`CurseRevealedEvent`][osrlib.crawl.events.CurseRevealedEvent] for a cursed item,
        the first time the item is used. An
        [`ItemUsedEvent`][osrlib.crawl.events.ItemUsedEvent] records what happened:
        a potion drunk, potions mixed, a scroll read, a device activated, or a cursed
        scroll's curse. The events of the item's effect follow: healing, saving throws,
        damage, effects attached, and a scroll's
        [`SpellCastEvent`][osrlib.core.events.SpellCastEvent], each a separate event. While
        exploring, one round passes. During an encounter, the encounter round's events
        follow instead.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING, SessionMode.ENCOUNTER})

    command_type: Literal["use_item"] = "use_item"
    character_id: str
    """The member using the item, by [`MemberView.id`][osrlib.crawl.views.MemberView.id]. The
    member has to carry the item, and for a wand, staff, or rod, the member's class has to be
    able to use it."""
    item_id: str
    """The magic item to use, by its per-instance id from
    [`MemberView.inventory`][osrlib.crawl.views.MemberView.inventory]. Only magic items can be
    used with this command, so the session refuses a mundane item's id with
    `exploration.item.not_carried`."""
    target_id: str | None = None
    """Who the item is used on: a party member's id for a device that heals by touch, or an
    encounter group's
    [`EncounterGroupView.id`][osrlib.crawl.views.EncounterGroupView.id] for a device that
    covers an area. Leave it `None` for a potion, which the member drinks. The session refuses a
    potion with a `target_id` other than `character_id`, because the member using a potion is
    the one who drinks it. A healing device with `None` heals the member using it."""
    spell_id: str | None = None
    """Which spell to read off a scroll that has more than one, from
    [`load_spells`][osrlib.data.load_spells] (see the
    [spell id index](https://mmacy.github.io/osrlib-python/reference/content-ids/spells/)).
    `None` reads the first spell left on the scroll. Leave it `None` for anything that isn't a
    scroll."""
    mode: str | None = None
    """Which of the read spell's usages to cast, by that usage's
    [`SpellMode.key`][osrlib.core.spells.SpellMode]. `None` casts the spell's first usage,
    which is right for a spell with one usage."""
    targets: tuple[str, ...] = ()
    """The read spell's targets, named as in
    [`CastSpell.targets`][osrlib.crawl.commands.CastSpell.targets]. When it's empty, `target_id`
    is the single target, so a scroll spell with one target needs only `target_id`."""


class IdentifyItem(Command):
    """Referee: identify a magic item without the party having to use it.

    Use it when you identify an item some other way, like a sage's fee or a
    *detect magic* that osrlib doesn't resolve. Using an item with
    [`UseItem`][osrlib.crawl.commands.UseItem] identifies it as well. Referee commands are
    legal in every mode, terminal modes included, and the session logs and replays them like
    any other command.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        - `session.command.unknown_member` - `character_id` matches no party member.
        - `session.command.unknown_item` - the member carries no magic item with
          that instance id.

    Events:
        [`ItemIdentifiedEvent`][osrlib.crawl.events.ItemIdentifiedEvent]. For a cursed
        item, a
        [`CurseRevealedEvent`][osrlib.crawl.events.CurseRevealedEvent] follows.
    """

    command_type: Literal["identify_item"] = "identify_item"
    character_id: str
    """The member carrying the item, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. A dead member's items can be
    identified too."""
    item_id: str
    """The magic item to identify, by its per-instance id. The id is in
    [`MemberView.inventory`][osrlib.crawl.views.MemberView.inventory], which shows the player
    a masked name until the item is identified. The referee view has the true name. A mundane
    item has nothing to identify, and the session refuses it with `session.command.unknown_item`."""


class UseStairs(Command):
    """Take the stairs, trapdoor, chute, or other transition on the party's cell.

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]) and standing on a cell
    with an authored transition, a [`TransitionSpec`][osrlib.crawl.dungeon.TransitionSpec].
    The move uses as much movement as a step onto an unexplored cell. A passage doesn't
    count, because the party walks through a passage with
    [`MoveParty`][osrlib.crawl.commands.MoveParty]. On a cell with both a passage and
    stairs, this command takes the stairs.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` - the session is not exploring a dungeon.
        - `exploration.stairs.none` - the party's cell has no transition other than a
          passage.
        - `exploration.transition.gate_refused` - the transition has an
          authored condition ([`GateSpec`][osrlib.crawl.gates.GateSpec]) the party
          doesn't meet. The rejection includes the gate's authored refusal text. Like every
          refusal, it costs no movement, no time, and no toll.

    Events:
        [`ItemConsumedEvent`][osrlib.crawl.events.ItemConsumedEvent] first when
        the gate takes an item as a toll, then
        [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent] when the
        level or dungeon changes, with the gate's success text when its author
        wrote one. That arrival event records how the party crossed: `via` is the
        transition's `kind`, and `transition_ref` is the cell the transition stands
        on, which is the cell the party left. Leaving a level shuts the unwedged doors the
        party opened on it, with a [`DoorEvent`][osrlib.crawl.events.DoorEvent] for each.

        The session then runs the destination cell's entry checks, the same ones as for
        [`MoveParty`][osrlib.crawl.commands.MoveParty]: a
        [`HoardGeneratedEvent`][osrlib.crawl.events.HoardGeneratedEvent] for area
        treasure, a
        [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent] and a
        [`TrapEvent`][osrlib.crawl.events.TrapEvent] for a room trap with its
        resolution, and the opening of an encounter with monsters keyed to that area in the adventure
        ([`SurpriseRolledEvent`][osrlib.crawl.events.SurpriseRolledEvent]s,
        [`EncounterStartedEvent`][osrlib.crawl.events.EncounterStartedEvent],
        [`ReactionRolledEvent`][osrlib.core.events.ReactionRolledEvent],
        [`StanceChangedEvent`][osrlib.crawl.events.StanceChangedEvent], and
        [`BattleStartedEvent`][osrlib.crawl.events.BattleStartedEvent] when the monsters
        attack). When the movement completes a turn on the game clock, the turn's
        bookkeeping follows, as it does for a step.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["use_stairs"] = "use_stairs"


class EnterDungeon(Command):
    """Travel from town to a dungeon's entrance and start exploring.

    The party must be in town. The trip takes the number of turns in the town's `travel_turns`
    for that dungeon, or no time when that dungeon has no entry there. On arrival the
    party stands on the entrance cell facing north, and the session switches to
    `exploring`. Leaving town also records what the party's treasure is worth. Under the
    on-return XP timing, the treasure XP awarded back in town is the gain over that value.

    Modes:
        `town`

    Rejections:
        - `session.command.wrong_mode` - the party is not in town.
        - `session.command.unknown_location` - `dungeon_id` matches no dungeon, or
          the dungeon has no level with an entrance.

    Events:
        First, the bookkeeping for the travel time:
        [`EffectExpiredEvent`][osrlib.core.events.EffectExpiredEvent] with a
        [`LightEvent`][osrlib.crawl.events.LightEvent] for a light burning out, and
        [`ProvisionsEvent`][osrlib.crawl.events.ProvisionsEvent] on a day boundary.
        The engine makes no wandering-monster checks during travel, and travel doesn't count toward the party's rest,
        so no [`WanderingCheckEvent`][osrlib.crawl.events.WanderingCheckEvent] or
        [`FatigueEvent`][osrlib.crawl.events.FatigueEvent] appears during travel.

        Then [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent] for
        the dungeon, `via` `"entrance"`, and the entrance cell's entry checks: a
        [`HoardGeneratedEvent`][osrlib.crawl.events.HoardGeneratedEvent] for area
        treasure, a
        [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent] and a
        [`TrapEvent`][osrlib.crawl.events.TrapEvent] for a room trap with its
        resolution, and the opening of an encounter with monsters keyed to that area in the adventure
        ([`SurpriseRolledEvent`][osrlib.crawl.events.SurpriseRolledEvent]s,
        [`EncounterStartedEvent`][osrlib.crawl.events.EncounterStartedEvent],
        [`ReactionRolledEvent`][osrlib.core.events.ReactionRolledEvent],
        [`StanceChangedEvent`][osrlib.crawl.events.StanceChangedEvent], and
        [`BattleStartedEvent`][osrlib.crawl.events.BattleStartedEvent] when the monsters
        attack).
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.TOWN})

    command_type: Literal["enter_dungeon"] = "enter_dungeon"
    dungeon_id: str
    """Which dungeon to set out for, by
    [`DungeonSpec.id`][osrlib.crawl.dungeon.DungeonSpec] from the adventure's `dungeons`.
    The party arrives at the entrance cell, on the first of that dungeon's levels that has
    an entrance. The session refuses an id the adventure doesn't have, or a dungeon with no
    entrance, with `session.command.unknown_location`."""


class TravelToTown(Command):
    """Travel from a dungeon's entrance back to town.

    The party must be exploring and standing on the entrance cell it arrived on. The trip
    takes as many turns as the trip out, with no wandering-monster checks. The unwedged
    doors the party opened on the level swing shut behind it. Under the on-return XP
    timing, the session awards the party's XP for the trip when it arrives in town.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` - the session is not exploring a dungeon.
        - `exploration.travel.not_at_entrance` - the party is not on the entrance
          cell.

    Events:
        [`DoorEvent`][osrlib.crawl.events.DoorEvent]s for the doors swinging shut, the
        bookkeeping for the travel time, then
        [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent] for
        town. Under the on-return XP timing, an
        [`AdventureXpAwardEvent`][osrlib.crawl.events.AdventureXpAwardEvent] and an
        [`XpAwardedEvent`][osrlib.crawl.events.XpAwardedEvent] for each living member follow,
        with a [`CharacterLeveledUpEvent`][osrlib.crawl.events.CharacterLeveledUpEvent] for
        each member who gains a level.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["travel_to_town"] = "travel_to_town"


class PurchaseEquipment(Command):
    """Buy equipment in town for one member, one purchase lot for each `item_ids` entry (zero time).

    The party must be in town. The session adds up the price of every entry first, and if
    the member can't afford the total, nothing is bought. The shop stocks only the built-in
    equipment from [`load_equipment`][osrlib.data.load_equipment] (see the
    [equipment id index](https://mmacy.github.io/osrlib-python/reference/content-ids/equipment/)).
    An item the adventure bundles isn't for sale, even when the party has seen its id, and
    the session refuses it as not stocked rather than as unknown.

    Modes:
        `town`

    Rejections:
        - `session.command.wrong_mode` - the party is not in town.
        - `session.command.unknown_member` - `character_id` matches no party member.
        - `session.command.member_incapacitated` - the member can't act.
        - `session.command.unknown_item` - an entry matches no equipment item.
        - `items.purchase.not_stocked` - an entry is an item the adventure
          bundles, which the shop doesn't sell.
        - `items.purchase.insufficient_funds` - the member's purse can't cover the total.

    Events:
        [`ItemAcquiredEvent`][osrlib.crawl.events.ItemAcquiredEvent] listing the
        purchases, `origin` `"purchase"`.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.events import Visibility
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.commands import PurchaseEquipment
        from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
        from osrlib.crawl.party import Party
        from osrlib.crawl.session import GameSession

        stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
        hero = create_character(
            name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=Ruleset(), stream=stream
        ).character
        level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
        crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
        adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))
        session = GameSession.new(Party(members=[hero]), adventure, seed=7)

        # One lot of torches (1 gp) and a tinder box (3 gp).
        result = session.execute(PurchaseEquipment(character_id=hero.id, item_ids=("torch", "tinder_box")))
        print(result.accepted)
        # True

        inventory = session.view(Visibility.PLAYER).party[0].inventory
        print([(item["template"]["id"], item["quantity"]) for item in inventory["items"]])
        # [('torch', 6), ('tinder_box', 1)]
        print(inventory["purse"]["gp"])
        # 96
        ```
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.TOWN})

    command_type: Literal["purchase_equipment"] = "purchase_equipment"
    character_id: str
    """The buyer, by [`MemberView.id`][osrlib.crawl.views.MemberView.id]. This member's purse
    pays for every entry, and the goods go into this member's pack."""
    item_ids: tuple[str, ...] = Field(min_length=1)
    """What to buy, one purchase lot per entry, so repeat an id to buy more lots. A purchase lot
    is what one purchase at the item's listed price delivers: a lot of torches costs 1 gp and
    arrives as six torches, while a weapon or a suit of armour is a single item. The ids come
    from [`load_equipment`][osrlib.data.load_equipment] (see the
    [equipment id index](https://mmacy.github.io/osrlib-python/reference/content-ids/equipment/)).
    The session refuses an id the adventure bundles with `items.purchase.not_stocked`, because
    it isn't for sale.
    An empty tuple fails validation when the command is constructed."""


class SellTreasure(Command):
    """Sell gems and jewellery in town at their full value (zero time).

    The party must be in town. Each entry is a valuable's instance id, and the
    gold goes into the purse of the member who carries it. A valuable sells for its full
    `value_gp`, so its sale price equals its XP value (1 gp for 1 XP). The OSE SRD prices
    treasure but gives no rate at which a town buys it, so osrlib uses full value. The SRD
    gives magic items no fixed sale value, so the session refuses to sell them.

    Modes:
        `town`

    Rejections:
        - `session.command.wrong_mode` - the party is not in town.
        - `town.sell.no_fixed_value` - an entry is a magic item, which has no fixed
          price.
        - `exploration.item.not_carried` - no member carries a valuable with that
          instance id.

    Events:
        One [`TreasureSoldEvent`][osrlib.crawl.events.TreasureSoldEvent] for each member
        whose valuables sold, with the value in gold pieces added to their purse.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.TOWN})

    command_type: Literal["sell_treasure"] = "sell_treasure"
    item_ids: tuple[str, ...] = Field(min_length=1)
    """The gems and jewellery to sell, by the per-instance ids they have in
    [`MemberView.inventory`][osrlib.crawl.views.MemberView.inventory]. The value of each one goes
    to the member who carries it, so one command can pay several purses. Magic items have no
    fixed price, and the session refuses them with `town.sell.no_fixed_value`."""


HealingService = Literal[
    "cure_light_wounds",
    "cure_serious_wounds",
    "cure_disease",
    "neutralize_poison",
    "remove_curse",
    "raise_dead",
]
"""The names of the temple's services, which is what
[`PurchaseHealing.service`][osrlib.crawl.commands.PurchaseHealing] accepts.

The set is closed: constructing a `PurchaseHealing` with any other name fails validation.
[`HEALING_SERVICES`][osrlib.crawl.exploration.HEALING_SERVICES] uses this same type for the
keys of its price list, so the services you offer from that list are always the names the
command accepts. Annotate a service parameter in your code with this type, and your type
checker catches a wrong name."""


class PurchaseHealing(Command):
    """Buy a temple healing service in town (zero time).

    The party must be in town. The OSE SRD describes the base town in prose and lists no
    temple prices, so osrlib sets these: *cure light wounds* 25 gp, *cure serious
    wounds* 100 gp, *cure disease* 150 gp, *neutralize poison* 150 gp, *remove
    curse* 200 gp, and *raise dead* 1,500 gp. You can read the same list from
    [`HEALING_SERVICES`][osrlib.crawl.exploration.HEALING_SERVICES]. Each service works as
    the matching spell, cast on the member by a temple cleric of the lowest level that can
    cast it.

    The whole party pays the fee, not only the patient. The temple takes it from the
    patient's purse first, then from the other members' purses in marching order, dead
    members included, until the fee is paid. Each purse pays in whole gold pieces, so the
    most a purse can pay is its total value rounded down to a whole gold piece. Any value
    below a gold piece stays in its purse, and the purses after the one that finishes the
    payment pay nothing. So two members with 12 gp and 5 sp each are worth 25 gp together,
    but they can pay only 24 gp, and the session refuses a 25 gp service.

    Because the party pays, a dead member can be raised even when that member's purse is
    empty, and the party doesn't have to move coins between purses first.

    Modes:
        `town`

    Rejections:
        - `session.command.wrong_mode` - the party is not in town.
        - `session.command.unknown_member` - `character_id` matches no party member.
        - `items.purchase.insufficient_funds` - the whole gold pieces in the party's
          purses add up to less than the fee. Value below a gold piece in a purse
          doesn't count, so the session can refuse a party worth the price in mixed
          coins.

    Events:
        A [`HealingPurchasedEvent`][osrlib.crawl.events.HealingPurchasedEvent] listing
        which purses paid and how much each one paid, then the events of the service's
        spell, like healing, effects released, or the result of a *raise dead*.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.TOWN})

    command_type: Literal["purchase_healing"] = "purchase_healing"
    character_id: str
    """Who is treated, by [`MemberView.id`][osrlib.crawl.views.MemberView.id]. This member's
    purse pays first, and the rest of the party pays whatever is left. The member can be dead,
    and `raise_dead` is the service for a dead member."""
    service: HealingService
    """Which service to buy. The prices are fixed: `cure_light_wounds` 25 gp,
    `cure_serious_wounds` 100 gp, `cure_disease` 150 gp, `neutralize_poison` 150 gp,
    `remove_curse` 200 gp, and `raise_dead` 1,500 gp. Each works as the matching spell, cast by
    a temple cleric of the lowest level that can cast it. `remove_curse` also frees the member
    from cursed items whose curse has been revealed, so they can take them off."""


class Parley(Command):
    """Talk to the monsters: roll their reaction again, with the speaker's CHA modifier.

    An encounter must be open. An encounter opens when a wandering-monster check turns up
    monsters, when the party enters an area with monsters keyed to it in the adventure, or when
    the referee spawns them. The monsters' reaction to the party is their stance:
    `attacks`, `hostile`, `uncertain`, `indifferent`, or `friendly`. The party can parley
    any number of times. A result of `attacks` opens battle at once, and a `hostile` result
    can open battle when the round ends.

    Modes:
        `encounter`

    Rejections:
        - `session.command.wrong_mode` - no encounter is open.
        - `encounter.none_active` - a second check behind the mode gate, which normal
          play never reaches.
        - `encounter.parley.mid_pursuit` - the monsters are chasing the party, and
          nobody can talk during a chase.
        - `session.command.unknown_member` - `character_id` matches no party member.
        - `session.command.member_incapacitated` - the speaker can't act.

    Events:
        [`ReactionRolledEvent`][osrlib.core.events.ReactionRolledEvent], and a
        [`StanceChangedEvent`][osrlib.crawl.events.StanceChangedEvent] when the
        stance changes. A result of `attacks` opens battle at once
        ([`BattleStartedEvent`][osrlib.crawl.events.BattleStartedEvent] and what
        follows). Otherwise the encounter round ends, and the monsters act on their
        stance, as for [`Wait`][osrlib.crawl.commands.Wait].
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.ENCOUNTER})

    command_type: Literal["parley"] = "parley"
    character_id: str
    """The member who does the talking, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. The member's CHA modifier applies to
    the new reaction roll, so send the member with the best CHA."""


class Evade(Command):
    """Run from the encounter before a battle starts.

    An encounter must be open. Monsters whose stance is `attacks` or `hostile` chase the
    party, if any of them can move. The party gets away at once when no monster chases it,
    or when it runs faster than the slowest monster that chases it. Otherwise a chase
    starts, and each [`Wait`][osrlib.crawl.commands.Wait] or
    [`DropItems`][osrlib.crawl.commands.DropItems] runs one round of it. `drop` scatters
    bait as the party runs: coins tempt intelligent monsters, and food tempts unintelligent
    ones. Once battle has started, the party can't evade. The OSE SRD allows evasion only
    before combat.

    Modes:
        `encounter`

    Rejections:
        - `session.command.wrong_mode` - no encounter is open.
        - `encounter.none_active` - a second check behind the mode gate, which normal
          play never reaches.
        - `encounter.evade.already_evading` - a chase is already running.
        - `encounter.evade.nothing_to_drop` - no living member has coins (for
          `treasure`) or rations (for `food`) to scatter.

    Events:
        [`ItemsDroppedEvent`][osrlib.crawl.events.ItemsDroppedEvent]s for the scattered
        bait, then an [`EvasionEvent`][osrlib.crawl.events.EvasionEvent] with code
        `encounter.evasion.succeeded`, which ends the encounter, or
        `encounter.evasion.pursuit`, which starts a chase, and the chase's first
        [`PursuitEvent`][osrlib.crawl.events.PursuitEvent] round. A chase ends when the
        party escapes, when it exhausts the party at the round limit
        ([`ExhaustionEvent`][osrlib.crawl.events.ExhaustionEvent], with an
        [`EffectAttachedEvent`][osrlib.core.events.EffectAttachedEvent] and a
        [`ConditionGainedEvent`][osrlib.core.events.ConditionGainedEvent] for the
        exhaustion), or when the monsters catch the party and battle starts
        ([`BattleStartedEvent`][osrlib.crawl.events.BattleStartedEvent]).

        When the encounter ends, the session emits an
        [`EncounterEndedEvent`][osrlib.crawl.events.EncounterEndedEvent], a
        [`MonsterDefeatedEvent`][osrlib.crawl.events.MonsterDefeatedEvent] for each
        monster killed or routed,
        [`EffectReleasedEvent`][osrlib.core.events.EffectReleasedEvent]s for the
        effects that end with it, and, under the immediate XP timing,
        [`XpAwardedEvent`][osrlib.crawl.events.XpAwardedEvent] and
        [`CharacterLeveledUpEvent`][osrlib.crawl.events.CharacterLeveledUpEvent].
        The clock then advances by the time the encounter took, with the usual
        [`LightEvent`][osrlib.crawl.events.LightEvent] and
        [`ProvisionsEvent`][osrlib.crawl.events.ProvisionsEvent] bookkeeping.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.ENCOUNTER})

    command_type: Literal["evade"] = "evade"
    drop: Literal["none", "treasure", "food"] = "none"
    """What to scatter behind the party as it runs. `treasure` empties every living member's
    purse onto the trail, where the coins are lost, and tempts intelligent monsters to stop
    for them. `food` drops one ration from each living member who carries one, and tempts
    unintelligent monsters. `none`, the default, drops nothing. The bait is dropped even when
    the party gets away at once, but it has an effect only during a chase. When nobody has anything
    of that kind to drop, the session refuses the command with
    `encounter.evade.nothing_to_drop`, and the party doesn't run."""


class EngageBattle(Command):
    """Start a battle with the monsters the party has met.

    An encounter must be open. Attacks, spells, and an item's striking effects
    happen only in battle, so this is how the party starts a fight.
    [`TurnUndead`][osrlib.crawl.commands.TurnUndead] is the one aggressive act the
    party can take in an encounter without starting a battle first. When the monsters were
    surprised at the start of the encounter, the party gets a free first round. During a
    chase, the party turns and fights at the current distance. Once the battle has
    started, send one [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound] for
    each round.

    Modes:
        `encounter`

    Rejections:
        - `session.command.wrong_mode` - no encounter is open.
        - `encounter.none_active` - a second check behind the mode gate, which normal
          play never reaches.

    Events:
        [`BattleStartedEvent`][osrlib.crawl.events.BattleStartedEvent]. A monster group
        whose morale score is 2 flees at once
        ([`MonsterFledEvent`][osrlib.crawl.events.MonsterFledEvent]). When every group
        flees, the battle ends at once, with a
        [`BattleEndedEvent`][osrlib.crawl.events.BattleEndedEvent] and the events of the
        encounter's end.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.ENCOUNTER})

    command_type: Literal["engage_battle"] = "engage_battle"


class Wait(Command):
    """Hold for one encounter round, and let the monsters act on their stance.

    An encounter must be open. The monsters' stance is their reaction to the party:
    `attacks`, `hostile`, `uncertain`, `indifferent`, or `friendly`. Waiting spends a round
    to see what the monsters do. Monsters whose stance is `uncertain` roll a new reaction,
    and `hostile` monsters attack once their patience runs out. During a chase after an
    [`Evade`][osrlib.crawl.commands.Evade], waiting runs one round of the chase instead.

    Modes:
        `encounter`

    Rejections:
        - `session.command.wrong_mode` - no encounter is open.
        - `encounter.none_active` - a second check behind the mode gate, which normal
          play never reaches.

    Events:
        The encounter round's events. Monsters whose stance is `uncertain` roll a new
        reaction ([`ReactionRolledEvent`][osrlib.core.events.ReactionRolledEvent], and a
        [`StanceChangedEvent`][osrlib.crawl.events.StanceChangedEvent] when it changes).
        Battle opens when the monsters attack, or when `hostile` monsters run out of
        patience ([`BattleStartedEvent`][osrlib.crawl.events.BattleStartedEvent]). During a
        chase, a [`PursuitEvent`][osrlib.crawl.events.PursuitEvent] round runs
        instead, which can end in an
        [`ExhaustionEvent`][osrlib.crawl.events.ExhaustionEvent] at the round limit
        (with an [`EffectAttachedEvent`][osrlib.core.events.EffectAttachedEvent]
        and a [`ConditionGainedEvent`][osrlib.core.events.ConditionGainedEvent] for the
        exhaustion) or in battle.

        When the encounter ends, the session emits
        [`EncounterEndedEvent`][osrlib.crawl.events.EncounterEndedEvent], a
        [`MonsterDefeatedEvent`][osrlib.crawl.events.MonsterDefeatedEvent] for each
        monster killed or routed,
        [`EffectReleasedEvent`][osrlib.core.events.EffectReleasedEvent]s, and, under
        the immediate XP timing,
        [`XpAwardedEvent`][osrlib.crawl.events.XpAwardedEvent] and
        [`CharacterLeveledUpEvent`][osrlib.crawl.events.CharacterLeveledUpEvent].
        The round also brings the usual
        [`EffectExpiredEvent`][osrlib.core.events.EffectExpiredEvent],
        [`LightEvent`][osrlib.crawl.events.LightEvent], and
        [`ProvisionsEvent`][osrlib.crawl.events.ProvisionsEvent] bookkeeping.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.ENCOUNTER})

    command_type: Literal["wait"] = "wait"


class TurnUndead(Command):
    """Present a holy symbol to turn undead monsters before a battle starts (one round).

    An encounter must be open, because outside one there are no monsters to turn. In
    battle, declare a `turn_undead`
    [`BattleDeclaration`][osrlib.crawl.commands.BattleDeclaration] instead. Presenting the
    symbol is an aggressive act: if any monster is left neither turned nor destroyed, the
    monsters attack at once and battle starts.

    Modes:
        `encounter`

    Rejections:
        - `session.command.wrong_mode` - no encounter is open.
        - `encounter.none_active` - a second check behind the mode gate, which normal
          play never reaches.
        - `encounter.turning.mid_pursuit` - the monsters are chasing the party, and
          nobody can turn undead during a chase.
        - `session.command.unknown_member` - `character_id` matches no party member.
        - `session.command.member_incapacitated` - the member can't act.
        - `magic.turning.not_a_turner` - the member's class can't turn undead.
        - `magic.turning.caster_incapacitated` - a condition on the member prevents
          the attempt.

    Events:
        [`UndeadTurnedEvent`][osrlib.core.events.UndeadTurnedEvent] with the roll
        and the affected monsters, with each condition it causes as a separate event.
        One round passes. When every monster is turned or destroyed, the encounter ends
        ([`EncounterEndedEvent`][osrlib.crawl.events.EncounterEndedEvent]). Otherwise a
        [`StanceChangedEvent`][osrlib.crawl.events.StanceChangedEvent] to `attacks`
        comes before the round, and battle starts after it
        ([`BattleStartedEvent`][osrlib.crawl.events.BattleStartedEvent]).
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.ENCOUNTER})

    command_type: Literal["turn_undead"] = "turn_undead"
    character_id: str
    """The member who presents the holy symbol, usually a cleric, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. The session refuses a member whose
    class can't turn undead with `magic.turning.not_a_turner`."""


class BattleDeclaration(BaseModel):
    """One party member's declared action for a battle round.

    You construct these yourself, one for each member who has to declare, and pass them all
    in one [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound].
    [`EncounterView.declarers`][osrlib.crawl.views.EncounterView.declarers] lists the
    members who have to declare this round. Three more lists on the same view, `front_rank`,
    `immobile`, and `reloading`, show who can attack in melee, who can't move, and who
    can't fire a reloading weapon this round. Read all four to offer only choices the
    session accepts.

    `action` determines which other fields apply. Leave the others at their defaults.

    - `attack` takes `target_group_id` and, optionally, `weapon_id`. `None` means bare
      hands.
    - `cast` takes `spell_id`, `spell_mode`, `reversed`, and `targets`.
    - `move` takes `move`, and `target_group_id` for a `close`. It changes the distance
      between the party and the monsters.
    - `use_item` takes `item_id`. The member drinks a potion, reads a scroll, or uses a
      wand, staff, or rod, or throws a flask of oil or a similar item at `target_group_id`.
    - `turn_undead` needs nothing else. Turning resolves with the spells in the round, but
      a hit can't disrupt it, because it's a class ability rather than a spell.
    - `hold` does nothing. A member with no legal action declares it, because every member
      in `declarers` needs a declaration.

    For a fight that opens and runs with declared attacks until it ends, see the
    [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound] example.
    """

    model_config = ConfigDict(frozen=True)

    character_id: str
    """Whose declaration this is, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. It has to be one of the ids in
    [`EncounterView.declarers`][osrlib.crawl.views.EncounterView.declarers], and each of those
    members needs a separate declaration in the same round."""
    action: Literal["attack", "cast", "turn_undead", "move", "use_item", "hold"]
    """What this member does. `attack` strikes or shoots at a group, `cast` casts a spell,
    `turn_undead` presents a holy symbol, `move` changes the distance to the monsters,
    `use_item` uses a magic item or throws an item, and `hold` does nothing. This field determines
    which other fields apply, and `hold` needs none of them."""
    target_group_id: str | None = None
    """Which monster group the action is aimed at, by
    [`EncounterGroupView.id`][osrlib.crawl.views.EncounterGroupView.id] from
    [`EncounterView.groups`][osrlib.crawl.views.EncounterView.groups]. An `attack` needs it, and
    so do a `close` move, a `cast` of a spell that covers an area, a thrown item, and a wand,
    staff, or rod that strikes or covers an area. The session refuses a group that isn't in the
    encounter with `battle.declaration.unknown_group`, and refuses a group that has fled the same
    way when the action is an attack or an item."""
    weapon_id: str | None = None
    """Which wielded weapon to attack with: a mundane weapon's catalog id, a magic weapon's
    per-instance id, or `None` to strike unarmed. The weapon has to be equipped already, so
    equip it with [`EquipItem`][osrlib.crawl.commands.EquipItem] before the fight. The session
    refuses a weapon that's only carried with `battle.declaration.weapon_not_wielded`. Whether the
    attack is melee or missile depends on the weapon's qualities and the group's distance."""
    spell_id: str | None = None
    """Which spell a `cast` declaration casts, from [`load_spells`][osrlib.data.load_spells]
    (see the
    [spell id index](https://mmacy.github.io/osrlib-python/reference/content-ids/spells/)).
    For a `use_item` that reads a scroll, it's which spell to read off the scroll, and `None`
    reads the first one left. The session refuses a `cast` with no spell named
    with `battle.declaration.missing_spell`."""
    spell_mode: str | None = None
    """Which of the spell's usages to cast, by that usage's
    [`SpellMode.key`][osrlib.core.spells.SpellMode]. A `cast` declaration has to name one, or
    the session refuses it with `battle.declaration.missing_spell`. A scroll read can leave it `None` to
    take the spell's first usage."""
    reversed: bool = False
    """Cast the reversed form of the spell, on the same terms as
    [`CastSpell.reversed`][osrlib.crawl.commands.CastSpell.reversed]: an arcane caster needs a
    reversed copy memorized, and a divine caster chooses the form here."""
    targets: tuple[str, ...] = ()
    """The spell's targets, named as in
    [`CastSpell.targets`][osrlib.crawl.commands.CastSpell.targets]: member ids, monster ids, or
    a `cell:` reference. Leave it empty for a spell that covers an area, which takes
    `target_group_id` instead. The session refuses an invisible target with
    `battle.declaration.invisible_target`."""
    move: Literal["close", "fighting_withdrawal", "retreat"] | None = None
    """Which movement a `move` declaration makes.

    `close` moves the party toward `target_group_id` at the encounter rate of its slowest
    living member, and stops at melee range. When members declare `close` on different
    groups, the party closes on the group of the first `close` in
    [`ResolveBattleRound.declarations`][osrlib.crawl.commands.ResolveBattleRound.declarations].

    `fighting_withdrawal` backs the party away from every group at half that rate. A member
    who declares it makes no attack that round, because a member declares one action per
    round.

    `retreat` backs the party away at the full rate. A round in which every member retreats
    ends the battle. It becomes a chase, or a clean escape when no monster can chase.

    The party moves as one formation, and no member can leave it. So `fighting_withdrawal`
    and `retreat` are legal only when every member in `declarers` declares the same one.
    When some members declare one of them and others don't, a member who declares `close`
    included, the session refuses the whole round with `battle.declaration.formation_split`.
    The result contains one rejection for each of the two moves the round splits on, and each
    one lists who declared that move and who didn't.
    [The adaptations register](https://mmacy.github.io/osrlib-python/adaptations/)
    describes this rule in full."""
    item_id: str | None = None
    """Which item a `use_item` declaration uses: a magic item's per-instance id for a potion, a
    scroll, a wand, a staff, or a rod, or a mundane item's catalog id for an item thrown at a
    group, like a flask of oil. The session refuses a mundane item with no combat use, or a
    magic item that can't be used in battle, with `battle.declaration.item_unusable`."""


class ResolveBattleRound(Command):
    """Resolve one battle round from one declaration per party member able to act.

    A battle must be underway (see
    [`EngageBattle`][osrlib.crawl.commands.EngageBattle]). Each round, get a new player
    view from [`GameSession.view`][osrlib.crawl.session.GameSession.view] and read who has
    to declare from
    [`EncounterView.declarers`][osrlib.crawl.views.EncounterView.declarers]. Construct a
    [`BattleDeclaration`][osrlib.crawl.commands.BattleDeclaration] for each of them, and
    send them all in one command. Repeat until the session leaves `battle`. It moves to
    `exploring` when the battle and its encounter end, to `encounter` when a retreat turns
    into a chase, and to `game_over` when the whole party dies.

    The session accepts or refuses a round as a whole. It checks every declaration before
    it resolves any of them. If a single declaration is bad, the session refuses the
    command, and the result lists every rejection found, so fix the declarations and send the round again.

    Modes:
        `battle`

    Rejections:
        - `session.command.wrong_mode` - no battle is underway.
        - `battle.none_active` - a second check behind the mode gate, which normal
          play never reaches.
        - `battle.declaration.roster_mismatch` - the declarations don't name exactly the
          members in `EncounterView.declarers`, each once. The session checks this first
          and reports nothing else when it fails.
        - `battle.declaration.unknown_action` - an `action` the session doesn't
          recognize. A `BattleDeclaration` with one already fails validation when it's
          constructed, so normal play never reaches this check.
        - Move declarations: `battle.declaration.missing_move`,
          `battle.declaration.unknown_group`, `battle.declaration.cannot_move`, and
          `battle.declaration.formation_split` when some declarers make a
          `fighting_withdrawal` or a `retreat` and others don't. There is one
          `formation_split` for each of the two moves the round splits on. Its `others`
          lists every other declarer of the round, including any who chose the other
          move. So a round that splits on both moves comes back with two, the fighting
          withdrawal's first and the retreat's second.
        - Attack declarations: `battle.declaration.unknown_group`,
          `battle.declaration.no_target`, `battle.declaration.weapon_not_wielded`,
          `battle.declaration.not_in_front_rank`, and the kernel attack checks
          `combat.attack.out_of_reach`, `combat.attack.out_of_range`,
          `combat.attack.reload`, `combat.attack.attacker_incapacitated`,
          `combat.attack.attacker_blind`.
        - Cast declarations: `battle.declaration.missing_spell`,
          `battle.declaration.unknown_group`,
          `battle.declaration.invisible_target`, and the cast checks
          `magic.cast.unknown_spell`, `magic.cast.silenced_area`,
          `magic.cast.unknown_mode`, `magic.cast.unknown_target`,
          `magic.cast.not_memorized`, `magic.cast.caster_incapacitated`,
          `magic.cast.caster_restrained`, `magic.cast.anti_magic_shell`,
          `magic.cast.not_reversible`, `magic.cast.target_count`,
          `magic.cast.out_of_range`.
        - Turn-undead declarations: `magic.turning.not_a_turner`,
          `magic.turning.caster_incapacitated`.
        - Item declarations: `battle.declaration.item_unusable`,
          `battle.declaration.unknown_group`, `battle.declaration.no_target`,
          `items.use.not_usable`, `items.device.inert`, `items.scroll.spent`,
          `items.scroll.no_such_spell`, `items.scroll.wrong_caster`,
          `exploration.action.requires_light`, and `combat.attack.out_of_reach`. A
          scroll read can also get the cast checks on its spell and targets
          (`magic.cast.unknown_mode`, `magic.cast.unknown_target`,
          `battle.declaration.invisible_target`, `magic.cast.target_count`,
          `magic.cast.out_of_range`, `magic.cast.caster_incapacitated`,
          `magic.cast.caster_restrained`, `magic.cast.anti_magic_shell`). A thrown item
          can also get the kernel attack checks listed for attack declarations.

    Events:
        Opening the round:
        [`BattleRoundEvent`][osrlib.crawl.events.BattleRoundEvent], a
        [`SpellDeclaredEvent`][osrlib.crawl.events.SpellDeclaredEvent] per declared
        cast, and
        [`InitiativeRolledEvent`][osrlib.core.events.InitiativeRolledEvent] for the
        side order.

        Movement: a [`GroupMovedEvent`][osrlib.crawl.events.GroupMovedEvent] each time the
        distance to a group changes.

        Missiles and melee:
        [`AttackRolledEvent`][osrlib.core.events.AttackRolledEvent],
        [`DamageDealtEvent`][osrlib.core.events.DamageDealtEvent],
        [`DamageAbsorbedEvent`][osrlib.core.events.DamageAbsorbedEvent],
        [`HitPointsReportedEvent`][osrlib.core.events.HitPointsReportedEvent],
        [`SavingThrowRolledEvent`][osrlib.core.events.SavingThrowRolledEvent],
        [`EquipmentDestroyedEvent`][osrlib.core.events.EquipmentDestroyedEvent],
        [`LevelDrainedEvent`][osrlib.core.events.LevelDrainedEvent] with
        [`SpellForgottenEvent`][osrlib.core.events.SpellForgottenEvent] when a drain
        costs a caster prepared spells, and
        [`DeathEvent`][osrlib.core.events.DeathEvent].

        Magic: [`SpellCastEvent`][osrlib.core.events.SpellCastEvent],
        [`TargetsSelectedEvent`][osrlib.core.events.TargetsSelectedEvent] when the
        engine picks the spell's targets,
        [`SpellDisruptedEvent`][osrlib.core.events.SpellDisruptedEvent],
        [`MagicDispelledEvent`][osrlib.core.events.MagicDispelledEvent],
        [`UndeadTurnedEvent`][osrlib.core.events.UndeadTurnedEvent], and the effects
        a spell leaves behind
        ([`EffectAttachedEvent`][osrlib.core.events.EffectAttachedEvent],
        [`EffectReleasedEvent`][osrlib.core.events.EffectReleasedEvent],
        [`ConditionGainedEvent`][osrlib.core.events.ConditionGainedEvent],
        [`ConditionRemovedEvent`][osrlib.core.events.ConditionRemovedEvent],
        [`HealingAppliedEvent`][osrlib.core.events.HealingAppliedEvent]).

        Items: [`ItemUsedEvent`][osrlib.crawl.events.ItemUsedEvent], with
        [`ItemIdentifiedEvent`][osrlib.crawl.events.ItemIdentifiedEvent] and
        [`CurseRevealedEvent`][osrlib.crawl.events.CurseRevealedEvent] the first time an
        item is used.

        Morale: [`MoraleCheckedEvent`][osrlib.core.events.MoraleCheckedEvent],
        [`MonsterFledEvent`][osrlib.crawl.events.MonsterFledEvent] for a group that
        breaks and flees, and
        [`MonstersLeftBehindEvent`][osrlib.crawl.events.MonstersLeftBehindEvent]
        when a fleeing group leaves behind members who can't move.

        Closing the round, the clock's bookkeeping:
        [`EffectExpiredEvent`][osrlib.core.events.EffectExpiredEvent],
        [`EffectTickedEvent`][osrlib.core.events.EffectTickedEvent],
        [`MonsterRevivedEvent`][osrlib.core.events.MonsterRevivedEvent],
        [`LightEvent`][osrlib.crawl.events.LightEvent], and
        [`ProvisionsEvent`][osrlib.crawl.events.ProvisionsEvent].

        A round that ends the battle adds a
        [`BattleEndedEvent`][osrlib.crawl.events.BattleEndedEvent] and the events of the
        encounter's end
        ([`EncounterEndedEvent`][osrlib.crawl.events.EncounterEndedEvent],
        [`MonsterDefeatedEvent`][osrlib.crawl.events.MonsterDefeatedEvent]s, and,
        under the immediate XP timing,
        [`XpAwardedEvent`][osrlib.crawl.events.XpAwardedEvent] and
        [`CharacterLeveledUpEvent`][osrlib.crawl.events.CharacterLeveledUpEvent]),
        or [`GameOverEvent`][osrlib.crawl.events.GameOverEvent] on a party wipe.

    Examples:
        A fighter and a cleric meet three goblins 30 feet away. Each round, every member
        in `declarers` closes the distance until the nearest group is in melee reach.
        Then the front rank attacks with the weapon it wields and the rest hold:

        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.events import Visibility
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.battle import MELEE_RANGE_FEET
        from osrlib.crawl.commands import (
            BattleDeclaration,
            EngageBattle,
            EnterDungeon,
            ResolveBattleRound,
            SessionMode,
            SpawnMonsters,
        )
        from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
        from osrlib.crawl.party import Party
        from osrlib.crawl.session import GameSession

        stream = RngStreams(master_seed=1).get(CHARACTER_CREATION_STREAM)
        members = [
            create_character(
                name=name,
                class_id=class_id,
                alignment=Alignment.LAWFUL,
                ruleset=Ruleset(),
                stream=stream,
                purchases=[(weapon, 1)],
                equip_ids=[weapon],
            ).character
            for name, class_id, weapon in (("Hild", "fighter", "sword"), ("Osric", "cleric", "mace"))
        ]
        level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
        crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
        adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))
        session = GameSession.new(Party(members=members), adventure, seed=1)
        session.execute(EnterDungeon(dungeon_id="crypt"))
        session.execute(SpawnMonsters(template_id="goblin", count_fixed=3, distance_feet=30))
        if session.mode is SessionMode.ENCOUNTER:  # the goblins didn't attack first
            session.execute(EngageBattle())

        def wielded_weapon_id(member):
            # A magic weapon is named by its instance id, a mundane one by its catalog id.
            wielded = member.inventory["wielded"]
            if not wielded:
                return None
            return wielded[0].get("instance_id") or wielded[0]["template"]["id"]

        rounds = 0
        while session.mode is SessionMode.BATTLE:
            view = session.view(Visibility.PLAYER)
            encounter = view.encounter
            weapons = {member.id: wielded_weapon_id(member) for member in view.party}
            target = min((group for group in encounter.groups if group.count), key=lambda group: group.distance_feet)
            declarations = []
            for member_id in encounter.declarers:
                if target.distance_feet > MELEE_RANGE_FEET:
                    declaration = BattleDeclaration(
                        character_id=member_id, action="move", move="close", target_group_id=target.id
                    )
                elif member_id in encounter.front_rank:
                    declaration = BattleDeclaration(
                        character_id=member_id, action="attack", target_group_id=target.id, weapon_id=weapons[member_id]
                    )
                else:
                    declaration = BattleDeclaration(character_id=member_id, action="hold")
                declarations.append(declaration)
            result = session.execute(ResolveBattleRound(declarations=tuple(declarations)))
            if not result.accepted:
                # Nothing happened. A front end shows the reasons and asks for new declarations.
                print([rejection.code for rejection in result.rejections])
                break
            rounds += 1

        print(rounds, session.mode)
        # 9 exploring
        ```
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.BATTLE})

    command_type: Literal["resolve_battle_round"] = "resolve_battle_round"
    declarations: tuple[BattleDeclaration, ...] = ()
    """One [`BattleDeclaration`][osrlib.crawl.commands.BattleDeclaration] for each member in
    [`EncounterView.declarers`][osrlib.crawl.views.EncounterView.declarers], and none for
    anyone else. The session refuses any other set of members
    with `battle.declaration.roster_mismatch`. If a single declaration is bad, the session
    refuses the whole command, not only that member's part of the round. The order of the declarations doesn't change
    who acts first, because initiative and the order of the round's phases do. When members
    declare `close` on different groups, though, the party closes on the group of the first
    `close` in this tuple."""


class GrantItem(Command):
    """Referee: put an item straight into a member's pack.

    Use it to hand out a reward, a found item, or a starting kit. Nobody pays, and no shop
    is involved. Referee commands are legal in every mode, terminal modes included, and the
    session logs and replays them like any other command.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        - `session.command.unknown_member` - `character_id` matches no party member.
        - `session.command.unknown_item` - `item_id` matches no item in the session's
          [`effective_equipment`][osrlib.crawl.session.GameSession.effective_equipment]
          catalog: neither a built-in id nor one the adventure bundles.

    Events:
        [`ItemAcquiredEvent`][osrlib.crawl.events.ItemAcquiredEvent] with the
        granted items and `origin` `"grant"`.
    """

    command_type: Literal["grant_item"] = "grant_item"
    character_id: str
    """The member who receives the item, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. A command you issue yourself has a
    member id here, and the session refuses any other value with `session.command.unknown_member`.
    A consequence or a reward in an adventure document has a party selector instead,
    `"@party"` for every living member or `"@first"` for the first living member, because the
    session assigns member ids when it starts. The trigger and quest interpreter replaces the selector
    with member ids before it issues the command."""
    item_id: str
    """Which item to create, from the session's
    [`effective_equipment`][osrlib.crawl.session.GameSession.effective_equipment] catalog:
    either a built-in id from [`load_equipment`][osrlib.data.load_equipment] (see the
    [equipment id index](https://mmacy.github.io/osrlib-python/reference/content-ids/equipment/))
    or one the adventure bundles."""
    quantity: int = Field(default=1, ge=1)
    """How many to place, counted as single items rather than as purchase lots.
    `quantity=6` grants six torches, the same number one lot of torches from
    [`PurchaseEquipment`][osrlib.crawl.commands.PurchaseEquipment] delivers."""


class GrantCoins(Command):
    """Referee: put coins straight into a member's purse.

    Use it to pay out a reward. Referee commands are legal in every mode, terminal modes
    included.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        - `session.command.unknown_member` - `character_id` matches no party member.

    Events:
        [`ItemAcquiredEvent`][osrlib.crawl.events.ItemAcquiredEvent] with the coins' value
        and `origin` `"grant"`, so you can tell a granted reward from treasure the
        party took.
    """

    command_type: Literal["grant_coins"] = "grant_coins"
    character_id: str
    """The member whose purse receives the coins, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. A command you issue yourself has a
    member id here, and the session refuses any other value with `session.command.unknown_member`.
    A consequence or a reward in an adventure document has a party selector instead,
    `"@party"` for every living member or `"@first"` for the first living member, because the
    session assigns member ids when it starts. The trigger and quest interpreter replaces the selector
    with member ids before it issues the command."""
    coins: Coins
    """The coins to add to the member's purse, by denomination. The session doesn't check
    weight, so a large grant can overload the member. An overloaded member slows the whole
    party, or stops it moving, and then the session refuses
    [`MoveParty`][osrlib.crawl.commands.MoveParty] with `exploration.move.cannot_move`."""


class AwardXP(Command):
    """Referee: give one character experience points, apart from the XP the session awards.

    Use it for a quest reward or for any other XP you grant. Referee commands are legal in
    every mode, terminal modes included, so you can grant an adventure's rewards after the
    session has ended. The class's prime-requisite bonus or penalty applies to the award,
    and the award can raise the character a level.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        - `session.command.unknown_member` - `character_id` matches no party member.

    Events:
        [`XpAwardedEvent`][osrlib.crawl.events.XpAwardedEvent] with the award, the
        adjusted award, and the level after. When the award reaches the next level, a
        [`CharacterLeveledUpEvent`][osrlib.crawl.events.CharacterLeveledUpEvent]
        follows with the levels, the hit points gained, and the new title.
    """

    command_type: Literal["award_xp"] = "award_xp"
    character_id: str
    """The member who receives the experience, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. A command you issue yourself has a
    member id here, and the session refuses any other value with `session.command.unknown_member`.
    A consequence or a reward in an adventure document has a party selector instead,
    `"@party"` for every living member or `"@first"` for the first living member, because the
    session assigns member ids when it starts. The trigger and quest interpreter replaces the selector
    with member ids before it issues the command."""
    amount: int = Field(ge=0)
    """The award before the class's prime-requisite adjustment. B/X grants at most one level
    per award. XP that would raise the character two levels stops one point short of the
    second level, and the rest is lost. Send another award to raise the character further."""


class SetFlag(Command):
    """Referee: set a session flag, like one that records a lever pulled to open a portcullis.

    A flag is a named value you store in the session. Referee commands are legal in
    every mode, terminal modes included. Flags are saved with the session. Your code and
    listeners read them from the referee view. To test one in an adventure document, use a
    [`FlagEqualsCondition`][osrlib.crawl.gates.FlagEqualsCondition], like the condition on
    a door that opens once the lever has been pulled.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        None.

    Events:
        [`FlagSetEvent`][osrlib.crawl.events.FlagSetEvent] with the key and value.
    """

    command_type: Literal["set_flag"] = "set_flag"
    key: str = Field(min_length=1)
    """The flag's name: any non-empty string you choose. Keep a name the same between releases
    of your game, because saves contain the flags and adventure documents refer to them by name."""
    value: str | int | bool
    """What the flag is set to: a string, an integer, or a boolean. Setting a flag that's
    already set replaces its value. To compare this value in an adventure document, use a
    [`FlagEqualsCondition`][osrlib.crawl.gates.FlagEqualsCondition]. So a door that opens
    once a lever has been pulled is a flag set here, and a gate on the door that tests it."""


class SpawnMonsters(Command):
    """Referee: create monsters at a distance from the party, in a new encounter or the open one.

    Use it for an encounter you decide on, like monsters an LLM referee brings in or
    a scripted ambush. Use it also for monsters that arrive during a fight, like the
    animals a lycanthrope summons. The party must be standing in a dungeon, because
    encounters take place on the dungeon grid. Spawning isn't legal once the session has
    ended, because it would start new play. Set exactly one of `count_dice` and
    `count_fixed`, or constructing the command fails validation.

    When no encounter is open, the command opens one with the monsters. When an encounter
    or a battle is open, the monsters join it as one new group at `distance_feet`. The
    group rolls no surprise or reaction, and it shares the encounter's stance. The command
    takes no time and runs no round, so the group first acts in the next round, on the
    monster side.

    Modes:
        `town`, `exploring`, `encounter`, `battle`

    Rejections:
        - `session.command.wrong_mode` - the session has ended.
        - `session.command.unknown_monster` - `template_id` matches no monster.
        - `session.command.not_in_dungeon` - the party is not on a dungeon cell.

    Events:
        [`MonstersSpawnedEvent`][osrlib.crawl.events.MonstersSpawnedEvent]. When the
        command opens an encounter, the encounter's opening follows: an
        [`EffectAttachedEvent`][osrlib.core.events.EffectAttachedEvent] for each monster that
        regenerates, like a troll,
        [`SurpriseRolledEvent`][osrlib.crawl.events.SurpriseRolledEvent]s,
        [`EncounterStartedEvent`][osrlib.crawl.events.EncounterStartedEvent], the
        reaction roll, and a
        [`StanceChangedEvent`][osrlib.crawl.events.StanceChangedEvent]. When the monsters'
        reaction is to attack, battle opens at once. When the group joins a battle and its
        morale score is 2, a [`MonsterFledEvent`][osrlib.crawl.events.MonsterFledEvent]
        follows, because a monster with that score won't fight.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _ALL_MODES - frozenset(
        {SessionMode.GAME_OVER, SessionMode.VICTORY}
    )

    command_type: Literal["spawn_monsters"] = "spawn_monsters"
    template_id: str
    """Which monster to spawn, from the session's
    [`effective_monsters`][osrlib.crawl.session.GameSession.effective_monsters] catalog: either
    a built-in id from [`load_monsters`][osrlib.data.load_monsters] (see the
    [monster id index](https://mmacy.github.io/osrlib-python/reference/content-ids/monsters/))
    or one the adventure bundles."""
    count_dice: str | None = None
    """How many to spawn, as a dice expression like `"2d6"`, rolled on the session's
    [`ENCOUNTER_STREAM`][osrlib.crawl.session.ENCOUNTER_STREAM]. Use it when the number should
    depend on the seed, and use `count_fixed` when it shouldn't. Set exactly one of the two. A
    malformed expression raises when the command is constructed, so the session never sees
    it. A roll below 1 counts as 1."""
    count_fixed: int | None = Field(default=None, ge=1)
    """How many to spawn, as an exact number. Use it when the author chose the number. Set
    exactly one of this and `count_dice`."""
    distance_feet: int = Field(ge=0)
    """How far away the monsters appear, in feet. A new encounter opens at this distance, and
    a group that joins an open encounter starts at it. The distance determines whether
    missiles reach, how long closing in takes, and whether the party can outrun the monsters.
    B/X uses 2d6 times 10 feet for a wandering encounter, so a number in that range is
    typical. `0` puts them right next to the party."""

    @field_validator("count_dice")
    @classmethod
    def _dice_must_parse(cls, value: str | None) -> str | None:
        if value is not None:
            parse(value)
        return value

    @model_validator(mode="after")
    def _dice_or_fixed(self) -> SpawnMonsters:
        if (self.count_dice is None) == (self.count_fixed is None):
            raise ValueError("exactly one of count_dice or count_fixed is required")
        return self


class SpawnNpcParty(Command):
    """Referee: generate a party of NPC adventurers and open an encounter with them.

    An NPC adventurer is a character the engine generates, with a class, a level, and gear,
    rather than a monster from the catalog. The party must be standing in a dungeon with no
    encounter already open. Like [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters], the
    command isn't legal once the session has ended, because it would start new play.

    Modes:
        `town`, `exploring`, `encounter`, `battle`

    Rejections:
        - `session.command.wrong_mode` - the session has ended.
        - `session.command.encounter_in_progress` - an encounter or battle is
          already open.
        - `session.command.not_in_dungeon` - the party is not on a dungeon cell.

    Events:
        [`NpcPartySpawnedEvent`][osrlib.crawl.events.NpcPartySpawnedEvent], which lists the
        NPC party at referee visibility, then the encounter's opening, as for
        [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters].
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _ALL_MODES - frozenset(
        {SessionMode.GAME_OVER, SessionMode.VICTORY}
    )

    command_type: Literal["spawn_npc_party"] = "spawn_npc_party"
    party_kind: Literal["basic", "expert"]
    """Which table to build the party from: `basic` for low-level adventurers, or `expert` for
    higher-level ones. It also sets the group's name in the encounter, "Basic Adventurers" or
    "Expert Adventurers"."""
    count_dice: str | None = None
    """How many adventurers, as a dice expression rolled on the session's
    [`ENCOUNTER_STREAM`][osrlib.crawl.session.ENCOUNTER_STREAM]. With `None`, the default, the
    engine rolls the B/X number for `party_kind`: 1d4+4 for `basic` and 1d6+3 for `expert`. A malformed
    expression raises when the command is constructed. A roll below 1 counts as 1."""
    distance_feet: int = Field(ge=0)
    """How far away the NPC party appears, in feet, on the same terms as
    [`SpawnMonsters.distance_feet`][osrlib.crawl.commands.SpawnMonsters.distance_feet]."""

    @field_validator("count_dice")
    @classmethod
    def _dice_must_parse(cls, value: str | None) -> str | None:
        if value is not None:
            parse(value)
        return value


class SetDoorState(Command):
    """Referee: change the state of a door on any level of any dungeon.

    Use it when your game logic opens, closes, reveals, or unlocks a door, like a
    lever that opens a portcullis. The door in the adventure document never changes. What
    changes is its state in play: whether it stands open, is wedged, has been found, and has
    been unlocked. A field you leave `None` leaves the current value unchanged. The door doesn't have
    to be next to the party. Referee commands are legal in every mode, terminal modes
    included.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        - `session.command.unknown_location` - `dungeon_id` matches no dungeon, or
          `level_number` matches no level in it.
        - `session.command.no_door` - the edge on that side of that cell isn't a door,
          or the cell is off the level's grid.

    Events:
        A [`DoorEvent`][osrlib.crawl.events.DoorEvent] at referee visibility when the
        door opens or closes, and otherwise none.
    """

    command_type: Literal["set_door_state"] = "set_door_state"
    dungeon_id: str
    """Which dungeon the door is in, by
    [`DungeonSpec.id`][osrlib.crawl.dungeon.DungeonSpec]. It doesn't have to be the dungeon the
    party is in."""
    level_number: int = Field(ge=1)
    """Which level of that dungeon, counting from 1."""
    x: int
    """The cell's x coordinate on that level's grid."""
    y: int
    """The cell's y coordinate on that level's grid."""
    direction: Direction
    """Which of the cell's four sides the door is on. Two cells share each edge, so naming a
    cell's north side and naming its northern neighbour's south side set the same door. The
    session refuses an edge with no door with `session.command.no_door`."""
    open: bool | None = None
    """Whether the door stands open. `None`, the default, leaves it as it is. A change emits a
    [`DoorEvent`][osrlib.crawl.events.DoorEvent] at referee visibility, and setting the value
    the door already has emits nothing."""
    wedged: bool | None = None
    """Whether a spike holds the door. `None` leaves it as it is. A wedged door doesn't swing
    shut behind the party, and [`CloseDoor`][osrlib.crawl.commands.CloseDoor] can't close it."""
    discovered: bool | None = None
    """Whether the party has found the door. `None` leaves it as it is. Setting it to `True`
    makes a secret door visible without a search, and `False` hides it again. Revealing a
    secret door gives every member their `room_traps` search back on both cells the door
    joins, as finding it with [`Search`][osrlib.crawl.commands.Search] does."""
    unlocked: bool | None = None
    """Whether the lock has been undone. `None` leaves it as it is. Setting it to `True` lets
    [`OpenDoor`][osrlib.crawl.commands.OpenDoor] open a locked door without a thief picking the
    lock."""


class PlaceParty(Command):
    """Referee: move the party straight to a dungeon cell or to town.

    Use it for an authored teleport, a trapdoor you handle yourself, or a rescue. The
    party can't be moved out of an open encounter or battle. Placing the party in a dungeon
    marks the cell explored and switches the session to `exploring`. Placing it in town
    switches the session to `town`. The party arrives without the entry checks of a step, so
    no treasure roll, room trap, or encounter happens on the destination cell, and no game
    time passes.

    Because the switch restarts play, the command isn't legal in `victory`. It's legal in
    `game_over`, where it's the only way out: placing the dead party in town lets you buy
    *raise dead* with [`PurchaseHealing`][osrlib.crawl.commands.PurchaseHealing].

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`

    Rejections:
        - `session.command.wrong_mode` - the session is in `victory`.
        - `session.command.encounter_in_progress` - an encounter or battle is
          open.
        - `session.command.unknown_location` - the location matches no dungeon, or no
          level in it.
        - `session.command.out_of_bounds` - the position is off the level's grid.

    Events:
        [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent] for the
        destination. A dungeon destination has `via` set to `"placed"`, so the log
        shows that the referee moved the party rather than that it took the stairs.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _ALL_MODES - frozenset({SessionMode.VICTORY})

    command_type: Literal["place_party"] = "place_party"
    location: PartyLocation
    """Where the party ends up. A [`PartyLocation`][osrlib.crawl.dungeon.PartyLocation] with
    `kind="town"` has no other fields. One with `kind="dungeon"` needs the dungeon id, the
    level number, the cell, and the facing, all four together, or constructing it fails
    validation. The session refuses a position off the level's grid with
    `session.command.out_of_bounds`."""


class AdvanceTime(Command):
    """Referee: move the game clock forward.

    Use it for time that passes outside the commands, like a week of travel or a
    referee's "an hour later". Referee commands are legal in every mode, terminal modes
    included, so the clock keeps running after the party dies. The time limits on spells
    that bring the dead back are measured on this clock. The time passes with its
    bookkeeping, like effects expiring and provisions eaten each day. The engine makes no
    wandering-monster checks for it, because the referee decides on encounters, and the time
    doesn't count toward the party's rest.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        None.

    Events:
        The time's bookkeeping events: effects that expire, a
        [`LightEvent`][osrlib.crawl.events.LightEvent] for each light that burns out, and
        [`ProvisionsEvent`][osrlib.crawl.events.ProvisionsEvent]s. Then a
        [`TimeAdvancedEvent`][osrlib.crawl.events.TimeAdvancedEvent] with the clock's new
        total.
    """

    command_type: Literal["advance_time"] = "advance_time"
    n: int = Field(ge=0)
    """How many of `unit` to advance. `0` is legal, advances nothing, and still emits the
    `TimeAdvancedEvent`."""
    unit: TimeUnit
    """Which unit `n` counts: rounds, turns, or days. A B/X turn is 10 minutes, or 60 rounds,
    and a day is 144 turns. Effects expire across the whole time whichever unit you pick, and
    the party eats provisions at each day boundary the time crosses."""


class RollDice(Command):
    """Referee: roll dice through the session, so the roll is seeded, logged, and replayed.

    Use it when the referee decides a chance outcome that osrlib doesn't model, like a
    puzzle, a bluff, or whether a frayed rope holds. A roll made through the session is
    logged, replayed, and reported as an event, unlike a roll you make yourself.
    Referee commands are legal in every mode, terminal modes included. The session draws the roll
    from the separate
    [`ADJUDICATION_STREAM`][osrlib.crawl.session.ADJUDICATION_STREAM], so a referee's roll
    never changes what any other stream draws. A malformed `expression` raises when the
    command is constructed, as a malformed `count_dice` on
    [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters] does, so it never reaches the
    session and draws nothing.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        None.

    Events:
        [`DiceRolledEvent`][osrlib.crawl.events.DiceRolledEvent] with the
        expression, the total, and each die's result. The event has referee visibility.
    """

    command_type: Literal["roll_dice"] = "roll_dice"
    expression: str
    """The roll, in the notation [`parse`][osrlib.core.dice.parse] accepts, like `"1d20"`,
    `"3d6+2"`, or `"2d6+1x10"`. A malformed expression raises when the command is constructed
    rather than coming back as a rejection, so it never reaches the session and draws
    nothing."""

    @field_validator("expression")
    @classmethod
    def _expression_must_parse(cls, value: str) -> str:
        parse(value)
        return value


class MarkTriggerFired(Command):
    """Referee: record that a trigger has fired.

    A trigger is an authored rule for commands to issue when something happens in play.
    The trigger and quest interpreter
    ([`Interpreter`][osrlib.crawl.interpreter.Interpreter]) issues this command for each
    trigger it fires, and a listener you write or an LLM referee can issue it too. The
    interpreter marks a trigger before it issues the trigger's consequences, so it reads the
    mark and doesn't fire a once-only trigger again. The mark survives save,
    load, and replay like the rest of the session's state. Referee commands are legal in
    every mode, terminal modes included.

    Marking a trigger that's already marked is accepted and changes no state, but it's
    still logged. The session's state records whether a trigger has fired, and the command
    log records each firing.

    Any `trigger_id` is accepted, because a mark needs no authored trigger behind it, so you
    can mark ids from any of your game's systems. The quest commands are different:
    [`ActivateQuest`][osrlib.crawl.commands.ActivateQuest] and the other quest commands
    accept only the ids of quests the adventure defines.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        None.

    Events:
        A [`TriggerFiredEvent`][osrlib.crawl.events.TriggerFiredEvent] with the
        trigger id and the `narrative` text, for every mark. The event has referee
        visibility.
    """

    command_type: Literal["mark_trigger_fired"] = "mark_trigger_fired"
    trigger_id: str = Field(min_length=1)
    """Which trigger fired. Any non-empty id is accepted, so you can mark firings from your
    systems with ids you choose. The session's state records each id once, which is what
    the interpreter checks for a trigger that fires once, and the command log records every mark."""
    narrative: str | None = Field(default=None, min_length=1)
    """The referee's text about this firing, included on the
    [`TriggerFiredEvent`][osrlib.crawl.events.TriggerFiredEvent], which has referee
    visibility. Players don't see it. To give the players a line about what happened, issue
    an [`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry]. Leave it `None` when there's
    nothing to say. An empty string fails validation."""


class AddJournalEntry(Command):
    """Referee: add a line of story text to the session's journal.

    The journal is the party's record of the adventure, in the order the party discovered
    things. Entries are only ever added: none is rewritten, and none is rebuilt from other
    state, so an entry stays after whatever caused it is gone. Each entry is stamped with
    the game clock's time when it's added, and
    [`PlayerView.journal`][osrlib.crawl.views.PlayerView.journal] returns the entries as they
    were written. Referee commands are legal in every mode, terminal modes included, so you
    can add a closing line after the adventure has ended.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        None.

    Events:
        [`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent] with
        the text and the clock time.
    """

    command_type: Literal["add_journal_entry"] = "add_journal_entry"
    text: str = Field(min_length=1)
    """The line to add, as the players will read it. The session stamps it with the clock time,
    adds it to the end of the journal, and returns it unchanged in
    [`PlayerView.journal`][osrlib.crawl.views.PlayerView.journal]. Entries are never rewritten,
    so write the line you want kept."""


class RecordNote(Command):
    """Referee: write a note into the logs, with no effect on the game.

    The note becomes an event at referee visibility and changes nothing else. Use it for a
    record from your code, like an authored consequence that was dropped, or for a
    referee's notes. The trigger and quest interpreter issues it to record what it dropped
    and why. Referee commands are legal in every mode, terminal modes included.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        None.

    Events:
        [`NoteRecordedEvent`][osrlib.crawl.events.NoteRecordedEvent] with the text.
    """

    command_type: Literal["record_note"] = "record_note"
    text: str = Field(min_length=1)
    """The note to record. It goes into the log as an event at referee visibility and changes no
    state. To give the players a line to read, issue an
    [`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry] instead."""


class ActivateQuest(Command):
    """Referee: put one of the adventure's quests into play.

    This is the first of the quest commands, which change an adventure's quest state:
    [`ActivateQuest`][osrlib.crawl.commands.ActivateQuest],
    [`RevealObjective`][osrlib.crawl.commands.RevealObjective],
    [`CompleteObjective`][osrlib.crawl.commands.CompleteObjective], and
    [`CompleteQuest`][osrlib.crawl.commands.CompleteQuest]. Each quest has a status that
    moves from `inactive` to `active` to `completed` and never back. Each of its objectives
    is either hidden or revealed, and either complete or not. The session stores this state,
    and only the quest commands change it, so a replay of the command log rebuilds it.

    The quest commands accept only the quest and objective ids that the adventure's
    [`QuestSpec`][osrlib.crawl.quests.QuestSpec]s define, and refuse any other id, because
    the player view gets an active quest's name, offer, and objectives from its spec.
    [`MarkTriggerFired`][osrlib.crawl.commands.MarkTriggerFired] is different and
    accepts any trigger id.

    Issue the quest commands yourself when you run the quests in your code. To have the
    quests run from the rules in the adventure instead, register an
    [`Interpreter`][osrlib.crawl.interpreter.Interpreter].

    When the quest's author wrote an `offer` text in its narrative, an accepted activation
    adds that text to the journal, and the event includes it. No
    [`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent] follows, because
    the quest's event already includes the text. Only an `inactive` quest can be activated.
    Referee commands are legal in every mode, terminal modes included.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        - `session.command.unknown_quest` - `quest_id` matches no quest of the
          adventure.
        - `session.command.quest_state` - the quest is already active or already
          completed. The rejection includes the quest id and its current state.

    Events:
        [`QuestActivatedEvent`][osrlib.crawl.events.QuestActivatedEvent] with the
        quest id, the quest's name, and the offer text.

    Examples:
        A game runs one quest from start to finish itself: it activates the quest,
        completes its objective, and completes the quest, which ends the adventure.

        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.events import Visibility
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.commands import ActivateQuest, CompleteObjective, CompleteQuest
        from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
        from osrlib.crawl.narrative import NarrativeBlock
        from osrlib.crawl.party import Party
        from osrlib.crawl.quests import ObjectiveSpec, QuestSpec, TriggerClause
        from osrlib.crawl.session import GameSession
        from osrlib.crawl.triggers import DungeonEnteredPattern, ItemAcquiredPattern
        from osrlib.messages import format_message

        stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
        hero = create_character(
            name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=Ruleset(), stream=stream
        ).character
        level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
        crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
        find = ObjectiveSpec(
            id="find", name="Find the relic", when=TriggerClause(pattern=ItemAcquiredPattern(item_id="holy_water"))
        )
        relic = QuestSpec(
            id="relic",
            name="The Lost Relic",
            activation=TriggerClause(pattern=DungeonEnteredPattern(dungeon_id="crypt")),
            objectives=(find,),
            concludes_adventure=True,
            narrative=NarrativeBlock(offer="The abbot wants the relic back.", completion="The relic is home."),
        )
        adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,), quests=(relic,))
        session = GameSession.new(Party(members=[hero]), adventure, seed=7)

        result = session.execute(ActivateQuest(quest_id="relic"))
        print([format_message(event) for event in result.events])
        # ['A new quest: The Lost Relic. The abbot wants the relic back.']
        print([objective.state for objective in session.view(Visibility.PLAYER).quests[0].objectives])
        # ['incomplete']

        session.execute(CompleteObjective(quest_id="relic", objective_id="find"))
        result = session.execute(CompleteQuest(quest_id="relic"))
        print([event.code for event in result.events], session.mode)
        # ['session.quest.completed', 'session.adventure.completed'] victory
        print([entry.text for entry in session.view(Visibility.PLAYER).journal])
        # ['The abbot wants the relic back.', 'The relic is home.']

        # Quest state only moves forward, so a second activation is refused.
        print(session.execute(ActivateQuest(quest_id="relic")).rejections[0].code)
        # session.command.quest_state
        ```
    """

    command_type: Literal["activate_quest"] = "activate_quest"
    quest_id: str = Field(min_length=1)
    """Which quest to put into play, by
    [`QuestSpec.id`][osrlib.crawl.quests.QuestSpec] from the adventure's quests. The session
    refuses an id that matches no quest spec with `session.command.unknown_quest`."""


class RevealObjective(Command):
    """Referee: show the players a hidden objective of an active quest.

    A hidden objective is missing from the player view until it's revealed or completed.
    Completing an objective reveals it, so an objective the party finishes before anyone
    announces it needs no reveal. The command accepts only the ids the adventure's quest
    specs define, as [`ActivateQuest`][osrlib.crawl.commands.ActivateQuest] describes, and
    the `ActivateQuest` example shows a quest from start to finish.

    When the objective's author wrote an `offer` text in its narrative, an accepted reveal
    adds that text to the journal, and the event includes it. No
    [`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent] follows,
    because the objective's event already includes the text. Referee commands are legal in
    every mode, terminal modes included.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        - `session.command.unknown_quest` - `quest_id` matches no quest of the
          adventure.
        - `session.command.unknown_objective` - `objective_id` matches no objective of
          that quest.
        - `session.command.quest_state` - the quest isn't active, or the objective
          is already revealed or already complete. The rejection includes the quest
          id and the state that caused the refusal.

    Events:
        [`ObjectiveRevealedEvent`][osrlib.crawl.events.ObjectiveRevealedEvent] with
        the quest id, the objective id, and the objective's offer text.
    """

    command_type: Literal["reveal_objective"] = "reveal_objective"
    quest_id: str = Field(min_length=1)
    """Which quest the objective belongs to, by
    [`QuestSpec.id`][osrlib.crawl.quests.QuestSpec]. The quest has to be active already."""
    objective_id: str = Field(min_length=1)
    """Which objective to show the players, by
    [`ObjectiveSpec.id`][osrlib.crawl.quests.ObjectiveSpec] within that quest. The session
    refuses an objective that's already revealed or complete with `session.command.quest_state`."""


class CompleteObjective(Command):
    """Referee: mark one objective of an active quest complete.

    Completing a hidden objective also reveals it, in one step, with no separate
    [`RevealObjective`][osrlib.crawl.commands.RevealObjective]. The command accepts only the
    ids the adventure's quest specs define, as
    [`ActivateQuest`][osrlib.crawl.commands.ActivateQuest] describes, and the `ActivateQuest`
    example shows a quest from start to finish.

    Completing the last objective a quest needs doesn't complete the quest. Send
    [`CompleteQuest`][osrlib.crawl.commands.CompleteQuest] for that, so whoever runs the
    quests decides when the quest is done. When the objective's author wrote a `progress`
    text in its narrative, an accepted completion adds that text to the journal, and the
    event includes it. No
    [`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent] follows.
    Referee commands are legal in every mode, terminal modes included.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        - `session.command.unknown_quest` - `quest_id` matches no quest of the
          adventure.
        - `session.command.unknown_objective` - `objective_id` matches no objective of
          that quest.
        - `session.command.quest_state` - the quest isn't active, or the objective
          is already complete. The rejection includes the quest id and the state
          that caused the refusal.

    Events:
        [`ObjectiveCompletedEvent`][osrlib.crawl.events.ObjectiveCompletedEvent] with
        the quest id, the objective id, and the objective's progress text.
    """

    command_type: Literal["complete_objective"] = "complete_objective"
    quest_id: str = Field(min_length=1)
    """Which quest the objective belongs to, by
    [`QuestSpec.id`][osrlib.crawl.quests.QuestSpec]. The quest has to be active."""
    objective_id: str = Field(min_length=1)
    """Which objective is complete, by
    [`ObjectiveSpec.id`][osrlib.crawl.quests.ObjectiveSpec] within that quest. A hidden
    objective is revealed as it completes, so it needs no separate
    [`RevealObjective`][osrlib.crawl.commands.RevealObjective]."""


class CompleteQuest(Command):
    """Referee: complete an active quest, and the adventure when the quest concludes it.

    The quest must be active, and that's the only check. The session doesn't check the
    quest's objectives or its completion rule, because ruling a quest done is the
    referee's call. The trigger and quest interpreter checks the rule before it issues this
    command. The command accepts only the quest ids the adventure defines, as
    [`ActivateQuest`][osrlib.crawl.commands.ActivateQuest] describes, and the `ActivateQuest`
    example shows a quest from start to finish.

    This command grants no rewards. Whoever completes the quest issues the quest's rewards
    afterwards as separate commands, so a completion you send yourself grants nothing
    unless you send the rewards too, and every reward granted is in the log. When the
    quest's author wrote a `completion` text in its narrative, the completion adds that text
    to the journal, and the events include it. No
    [`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent] follows.

    Completing a quest whose spec sets `concludes_adventure` ends the adventure. The session
    closes any open encounter and battle, and switches to `victory`. No other command enters
    `victory`. When the session has already ended, in `game_over` or `victory`, the quest
    still completes and its text still goes in the journal, but the mode doesn't change and
    the session emits no `AdventureCompletedEvent`. Referee commands are legal in every mode,
    terminal modes included.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        - `session.command.unknown_quest` - `quest_id` matches no quest of the
          adventure.
        - `session.command.quest_state` - the quest is inactive or already
          completed. The rejection includes the quest id and its current state.

    Events:
        [`QuestCompletedEvent`][osrlib.crawl.events.QuestCompletedEvent] with the
        quest id, the quest's name, and the completion text. An
        [`AdventureCompletedEvent`][osrlib.crawl.events.AdventureCompletedEvent]
        with the same text follows when the quest concludes the adventure and the
        session hadn't already ended.
    """

    command_type: Literal["complete_quest"] = "complete_quest"
    quest_id: str = Field(min_length=1)
    """Which quest to complete, by
    [`QuestSpec.id`][osrlib.crawl.quests.QuestSpec]. It has to be active, and that's the only
    check: whoever issues the command decides whether the objectives are done."""


ALL_COMMAND_CLASSES: tuple[type[Command], ...] = (
    MoveParty,
    TurnParty,
    ReorderParty,
    OpenDoor,
    CloseDoor,
    ForceDoor,
    WedgeDoor,
    ListenAtDoor,
    PickLock,
    Search,
    InspectTreasure,
    RemoveTreasureTrap,
    TakeTreasure,
    DropItems,
    GiveItems,
    LightSource,
    ExtinguishSource,
    EquipItem,
    UnequipItem,
    Rest,
    PrepareSpells,
    LearnSpell,
    CastSpell,
    UseItem,
    UseStairs,
    EnterDungeon,
    TravelToTown,
    PurchaseEquipment,
    SellTreasure,
    PurchaseHealing,
    Parley,
    Evade,
    EngageBattle,
    Wait,
    TurnUndead,
    ResolveBattleRound,
    GrantItem,
    GrantCoins,
    AwardXP,
    SetFlag,
    SpawnMonsters,
    SpawnNpcParty,
    SetDoorState,
    PlaceParty,
    AdvanceTime,
    IdentifyItem,
    RollDice,
    MarkTriggerFired,
    AddJournalEntry,
    RecordNote,
    ActivateQuest,
    RevealObjective,
    CompleteObjective,
    CompleteQuest,
)
"""Every command class, in a fixed order. These are the members of the
[`AnyCommand`][osrlib.crawl.commands.AnyCommand] union.

Iterate it to build something that covers every command without naming each class: a
tool definition for an agent framework, a request router, a table of what the current
mode accepts (read each class's `allowed_modes`), or a test that walks every command.
The order stays the same between releases, and new commands are added at the end, so an
index into this tuple always refers to the same class.

To turn a serialized mapping back into a command, call
[`parse_command`][osrlib.crawl.commands.parse_command] rather than searching this tuple
yourself."""

AnyCommand = Annotated[
    Union[*ALL_COMMAND_CLASSES],
    Field(discriminator="command_type"),
]
"""The type of any command: a union of every command class, discriminated by `command_type`.

Annotate a field, a parameter, or a request body with it when the value arrives as data
and could be any command. Pydantic reads `command_type` and validates the data against
that one class, so a web handler annotated `command: AnyCommand` gets the right model,
and an error that lists the right class's fields, with no dispatch table in your handler.
A `command_type` that matches no class fails validation. To skip an unknown
`command_type` instead, call [`parse_command`][osrlib.crawl.commands.parse_command] on the
mapping.

The union's members, in order, are
[`ALL_COMMAND_CLASSES`][osrlib.crawl.commands.ALL_COMMAND_CLASSES]. The union's JSON
Schema is published as
[`commands.json`](https://mmacy.github.io/osrlib-python/reference/commands/commands.json),
which an agent framework can load as tool definitions."""

CONSEQUENCE_COMMAND_CLASSES: tuple[type[Command], ...] = (
    GrantItem,
    GrantCoins,
    AwardXP,
    SetFlag,
    SpawnMonsters,
    SpawnNpcParty,
    SetDoorState,
    PlaceParty,
    AdvanceTime,
)
"""The referee commands an adventure document may contain as authored consequences, in a
fixed order.

An authored consequence is a command listed in the adventure for the interpreter to issue
when a trigger fires, or to grant as a quest reward. A document that contains any other
command type there fails to load, and [`ConsequenceCommand`][osrlib.crawl.commands.ConsequenceCommand]
is the type that enforces that.

The other referee commands are left out for these reasons:

- [`MarkTriggerFired`][osrlib.crawl.commands.MarkTriggerFired],
  [`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry],
  [`RecordNote`][osrlib.crawl.commands.RecordNote],
  [`ActivateQuest`][osrlib.crawl.commands.ActivateQuest],
  [`RevealObjective`][osrlib.crawl.commands.RevealObjective],
  [`CompleteObjective`][osrlib.crawl.commands.CompleteObjective], and
  [`CompleteQuest`][osrlib.crawl.commands.CompleteQuest] are the commands the trigger and
  quest interpreter issues to record what it does. It marks triggers, adds journal entries,
  records notes, and changes quest state for the author, so an authored copy would record
  the same thing twice.
- [`IdentifyItem`][osrlib.crawl.commands.IdentifyItem] refers to a magic item by its instance
  id, which the session assigns during play, so no document can contain one.
- An adventure document can't use the result of [`RollDice`][osrlib.crawl.commands.RollDice],
  so an authored roll would only use up draws from the adjudication stream.

In an authored consequence, the `character_id` of a grant or an award is a party
selector, `"@party"` or `"@first"`, never a member id, because the session assigns
member ids when it starts. See [`osrlib.crawl.triggers`][osrlib.crawl.triggers] for the selectors."""

ConsequenceCommand = Annotated[
    GrantItem
    | GrantCoins
    | AwardXP
    | SetFlag
    | SpawnMonsters
    | SpawnNpcParty
    | SetDoorState
    | PlaceParty
    | AdvanceTime,
    Field(discriminator="command_type"),
]
"""The type of an authored consequence: a union of the classes in
[`CONSEQUENCE_COMMAND_CLASSES`][osrlib.crawl.commands.CONSEQUENCE_COMMAND_CLASSES],
discriminated by `command_type`.

The union is written out class by class, rather than built from the tuple, so a static
type checker can read it. A field annotated with it accepts only those commands: data
that names any other `command_type` fails validation, with no extra validator needed."""


@cache
def _any_command_adapter() -> TypeAdapter:
    return TypeAdapter(AnyCommand)


@cache
def _known_command_types() -> frozenset[str]:
    return frozenset(variant.model_fields["command_type"].default for variant in ALL_COMMAND_CLASSES)


def parse_command(data: Mapping[str, object]) -> Command | None:
    """Parse one serialized command, skipping unknown command types.

    Call this on a command that arrived as data rather than as a Python object: an entry
    of a saved command log, a request body, or a queue message. Pass the command it
    returns to [`GameSession.execute`][osrlib.crawl.session.GameSession.execute].

    An unknown `command_type` comes back as `None` rather than an exception, so an older
    engine can read a log from a newer one: skip the `None`s and replay the rest. A
    malformed payload with a `command_type` this engine has a class for raises instead, because the
    sender meant a command this engine has and got it wrong.

    To have pydantic parse commands inside a model or a web framework, annotate the value
    with [`AnyCommand`][osrlib.crawl.commands.AnyCommand] instead. Validation through that
    type fails on an unknown `command_type` rather than skipping it.

    Args:
        data: One serialized command, like the dict a command's `model_dump(mode="json")`
            returns or a decoded JSON object. A mapping with no `command_type` key counts as
            unknown.

    Returns:
        The command, or `None` when its `command_type` is unknown.

    Raises:
        osrlib.errors.ContentValidationError: If the command type is known but the payload is
            malformed. The message includes pydantic's description of each bad field. Fix the
            payload. Parsing the same payload again fails the same way.

    Examples:
        ```python
        from osrlib.crawl.commands import SetFlag, parse_command

        wire = SetFlag(key="portcullis_open", value=True).model_dump(mode="json")
        assert wire == {
            "command_type": "set_flag",
            "source": None,
            "key": "portcullis_open",
            "value": True,
        }

        command = parse_command(wire)
        assert command == SetFlag(key="portcullis_open", value=True)

        # A command type this engine doesn't know is skipped, not an error.
        assert parse_command({"command_type": "teleport_party"}) is None
        ```
    """
    from osrlib.errors import ContentValidationError

    if data.get("command_type") not in _known_command_types():
        return None
    try:
        return _any_command_adapter().validate_python(data)
    except ValidationError as error:
        raise ContentValidationError(f"malformed command: {error}") from error
