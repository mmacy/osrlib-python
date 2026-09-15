"""The command set: typed requests, the union that parses them, and the result envelope.

Commands are osrlib's write API, and this module is where a front end starts. Build
a command, hand it to
[`GameSession.execute`][osrlib.crawl.session.GameSession.execute], and read the
[`CommandResult`][osrlib.crawl.commands.CommandResult] it returns: whether the
command was accepted, the [`Rejection`][osrlib.core.validation.Rejection]s if it
wasn't, and the events it caused if it was. Render those events, with
[`format_message`][osrlib.messages.format_message] or your own text, and the player
has seen what happened. Read state back through
[`GameSession.view`][osrlib.crawl.session.GameSession.view] rather than from the
session's attributes.

Every command is a frozen pydantic model with a single-valued `command_type`
discriminator, so a command is also a JSON object with a `command_type` key.
[`AnyCommand`][osrlib.crawl.commands.AnyCommand] is the union over every command
class, discriminated on that key, and
[`parse_command`][osrlib.crawl.commands.parse_command] turns one serialized mapping
back into a command, answering `None` for a `command_type` it doesn't know so an
older engine still reads a newer log.

Which commands the session accepts depends on where the party is. A
[`SessionMode`][osrlib.crawl.commands.SessionMode] is that state: `town`,
`exploring`, `encounter`, `battle`, and the two the session ends in. Each command
class declares the modes it's legal in as an `allowed_modes` class attribute, and a
command sent in the wrong mode comes back refused with `session.command.wrong_mode`
before any other check runs.

Referee commands, the ones a game's own systems issue rather than a player, are
legal in every mode, and they're logged and replayed like any other command. Three
are the exception, because they would restart play in a session that has already
ended: [`PlaceParty`][osrlib.crawl.commands.PlaceParty] is illegal in `victory`, and
[`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters] and
[`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty] are illegal in `victory` and
`game_over` alike.

Every command class documents its contract in three sections: `Modes:` for the
session modes that accept it, `Rejections:` for the codes it can come back with, and
`Events:` for what it emits when it's accepted. The generated command pages state
the same contract as JSON Schema.

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
    """The session modes gating command legality.

    The wire values are lowercase and they serialize into saves, so changing one is a
    `schema_version` bump.

    `game_over` and `victory` are the terminal modes: the session has ended. Play
    commands are illegal in both, referee commands remain legal except the ones
    that would resume play ([`PlaceParty`][osrlib.crawl.commands.PlaceParty] in
    `victory`, [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters] and
    [`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty] in both), and no play
    ever leaves either one. The referee's way out of `game_over` is `PlaceParty`,
    documented there.
    """

    TOWN = "town"
    """The party is in the base town, between delves: buying gear, selling treasure, paying a
    temple for healing, resting, and preparing spells. A new session starts here, and
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] is the way out of it."""
    EXPLORING = "exploring"
    """The party is standing on a dungeon grid. Moving, doors, listening, searching, treasure,
    light, and stairs are all legal here, and this is the mode most of a session is spent in."""
    ENCOUNTER = "encounter"
    """Monsters have been met and no blow has been struck yet. The party can talk
    ([`Parley`][osrlib.crawl.commands.Parley]), run ([`Evade`][osrlib.crawl.commands.Evade]),
    hold ([`Wait`][osrlib.crawl.commands.Wait]), present a holy symbol
    ([`TurnUndead`][osrlib.crawl.commands.TurnUndead]), or attack
    ([`EngageBattle`][osrlib.crawl.commands.EngageBattle]). Most exploration commands are
    refused until the encounter resolves."""
    BATTLE = "battle"
    """A fight is underway.
    [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound] is the only play command
    the session accepts, one round of declarations at a time, until a side breaks."""
    GAME_OVER = "game_over"
    """Every party member is dead and the session has ended. Play commands are refused and
    referee commands still work. [`PlaceParty`][osrlib.crawl.commands.PlaceParty] is the way
    back out: carrying the fallen to town is the first step of a revival."""
    VICTORY = "victory"
    """A quest that concludes the adventure has completed and the session has ended. Play
    commands are refused and nothing leaves this mode, so a front end treats it as the final
    screen. [`CompleteQuest`][osrlib.crawl.commands.CompleteQuest] is the only entrance."""

    @property
    def terminal(self) -> bool:
        """Whether the session has ended: `game_over` or `victory`."""
        return self in (SessionMode.GAME_OVER, SessionMode.VICTORY)


_ALL_MODES = frozenset(SessionMode)
_FIELD_MODES = frozenset({SessionMode.TOWN, SessionMode.EXPLORING})


class Command(BaseModel):
    """Base class for all commands.

    You never construct this directly. Construct one of the command classes, which
    all inherit `source` and the `command_type` discriminator from here, and pass it
    to [`GameSession.execute`][osrlib.crawl.session.GameSession.execute]. Type a
    parameter as `Command` when it takes any command, and as
    [`AnyCommand`][osrlib.crawl.commands.AnyCommand] when it has to parse one off the
    wire.

    Commands are frozen, because a command is a request rather than a working
    object: an accepted one is logged exactly as it arrived, and replaying the log
    replays the game. They also ignore fields they don't know, so a command written
    by a newer engine still parses on an older one. Subclassing this outside osrlib
    isn't the way to add a game's own actions. Issue the referee commands, or
    register a listener, instead.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    command_type: str
    """The wire discriminator. Each subclass fixes it to its own snake_case literal, like
    `"move_party"` or `"open_door"`, so you never set it yourself: constructing the subclass
    does. It's what [`parse_command`][osrlib.crawl.commands.parse_command] and the
    [`AnyCommand`][osrlib.crawl.commands.AnyCommand] union read to rebuild the right class
    from a serialized mapping, and it stays the same across releases."""
    source: str | None = Field(default=None, min_length=1)
    """An annotation naming the authored object (a trigger or quest id) or the game
    system on whose behalf the command was issued. Execution never reads it: a stamped
    command does exactly what the same command unstamped does. It is logged and replayed
    with the command, so the log alone answers "why did this happen". Absent is `None`,
    and the empty string is not a value."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = _ALL_MODES
    """The session modes that accept this command. It's a class attribute rather than a field,
    because it belongs to the command kind rather than to one request, so it never
    serializes. [`GameSession.execute`][osrlib.crawl.session.GameSession.execute] checks it
    before anything else and refuses a wrong-mode command with `session.command.wrong_mode`.
    The base value is every mode, which the referee commands keep. Each play command narrows
    it, and the class's `Modes:` section names the same set in prose."""

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        """Reject subclasses that weaken the schema contract via `model_config`."""
        super().__pydantic_init_subclass__(**kwargs)
        if cls.model_config.get("extra") != "ignore":
            raise TypeError(f"{cls.__name__} must keep extra='ignore': the command schema grows additively")
        if not cls.model_config.get("frozen"):
            raise TypeError(f"{cls.__name__} must stay frozen: accepted commands are logged verbatim")


class CommandResult(BaseModel):
    """The `execute` envelope: accepted or rejected, with the events either way.

    [`GameSession.execute`][osrlib.crawl.session.GameSession.execute] returns one
    per command, and that's where you get one. Check `accepted` first, show
    `rejections` when it's `False` and render `events` when it's `True`. That pair
    is a front end's turn loop.

    A rejected command consumes no RNG draws, no clock time, mutates nothing, and
    is excluded from the command log. Its result contains the rejections and no
    events. A refusal costs the party nothing, so it reads as an in-fiction "you
    cannot do that" rather than as an error.

    An accepted command's `events` contains the complete chain: the handler's own
    events, plus everything the nested commands a listener issued logged while it
    ran, each event exactly once, in log order, so a front end renders the whole
    reaction from one envelope without reading `session.event_log`.
    """

    model_config = ConfigDict(frozen=True)

    accepted: bool
    """Whether the command ran. `False` means the pure validation pre-phase refused it: no dice
    were drawn, no clock time passed, nothing changed, and the command is absent from
    `GameSession.command_log`."""
    rejections: tuple[Rejection, ...] = ()
    """Why the command was refused, and empty when it was accepted. Each
    [`Rejection`][osrlib.core.validation.Rejection] contains a dotted code and the structured
    facts behind it, never English prose, so a front end renders the refusal in its own voice.
    The codes a given command can come back with are listed in that command's `Rejections:`
    section."""
    events: tuple[Event, ...] = ()
    """Everything the accepted command caused, in event-log order, and empty when it was refused.
    The handler's own events come first, then the events of every command a registered
    listener issued while it ran, however deeply nested, each event appearing once. A front
    end can render the whole reaction from this tuple, without reading
    `GameSession.event_log`. Pass an event to
    [`format_message`][osrlib.messages.format_message] for a default English line."""


class MoveParty(Command):
    """Move the party one cell, turning to face the way it goes.

    The party must already be inside a dungeon: a fresh session starts in town, and
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] is what places the party at
    the entrance and switches the session to `exploring`.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` - the session is not exploring a dungeon.
        - `exploration.move.cannot_move` - the party cannot move: it is overloaded,
          or a living member is unable to walk.
        - `exploration.move.blocked` - a wall, a closed or secret door, or the map
          edge blocks that direction.

    Events:
        [`PartyMovedEvent`][osrlib.crawl.events.PartyMovedEvent] with the new position
        and facing. Arriving in the new cell can set off more, each with its own
        event. A [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent]
        lands when the party crosses into a new area, level, or dungeon, and a
        [`DoorEvent`][osrlib.crawl.events.DoorEvent] for each door the party opened
        swinging shut behind it. The area's treasure rolls as a
        [`HoardGeneratedEvent`][osrlib.crawl.events.HoardGeneratedEvent]. A room
        trap's spring check posts a
        [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent] and a
        [`TrapEvent`][osrlib.crawl.events.TrapEvent], and a trap that springs
        resolves at once.

        A cell under a burning-oil pool or a *web* catches the party as it arrives
        ([`DamageDealtEvent`][osrlib.core.events.DamageDealtEvent],
        [`HitPointsReportedEvent`][osrlib.core.events.HitPointsReportedEvent],
        [`DeathEvent`][osrlib.core.events.DeathEvent],
        [`EffectAttachedEvent`][osrlib.core.events.EffectAttachedEvent],
        [`ConditionGainedEvent`][osrlib.core.events.ConditionGainedEvent]). A keyed
        encounter opens on arrival with
        [`SurpriseRolledEvent`][osrlib.crawl.events.SurpriseRolledEvent]s,
        [`EncounterStartedEvent`][osrlib.crawl.events.EncounterStartedEvent],
        [`ReactionRolledEvent`][osrlib.core.events.ReactionRolledEvent], and
        [`StanceChangedEvent`][osrlib.crawl.events.StanceChangedEvent], and an
        attacks stance opens battle at once
        ([`BattleStartedEvent`][osrlib.crawl.events.BattleStartedEvent]).

        The step accrues toward the turn clock, and crossing a turn boundary brings
        its bookkeeping:
        [`EffectExpiredEvent`][osrlib.core.events.EffectExpiredEvent] with a
        [`LightEvent`][osrlib.crawl.events.LightEvent] for a light burning out,
        [`FatigueEvent`][osrlib.crawl.events.FatigueEvent] on the rest cadence,
        [`ProvisionsEvent`][osrlib.crawl.events.ProvisionsEvent] on a day boundary,
        and [`WanderingCheckEvent`][osrlib.crawl.events.WanderingCheckEvent] on the
        wandering cadence.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["move_party"] = "move_party"
    direction: Direction
    """Which way to step. The party turns to face that way as it goes, so a move is also a turn.
    To change facing without spending the step, use
    [`TurnParty`][osrlib.crawl.commands.TurnParty]. A direction with a wall, a closed door, an
    undiscovered secret door, or the map edge behind it is refused with
    `exploration.move.blocked`, and
    [`ExploredLevelView.edges`][osrlib.crawl.views.ExploredLevelView.edges] is where a front
    end reads which sides of the cell the party can leave by."""


class TurnParty(Command):
    """Turn the party in place to a new facing (zero time).

    The party must already be inside a dungeon. See
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon].

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
    passes. The resulting facing rides
    [`PartyMovedEvent`][osrlib.crawl.events.PartyMovedEvent], which is what a first-person
    front end redraws from."""


class ReorderParty(Command):
    """Rewrite the marching order, the only command that changes it.

    Legal in town and while exploring. The order is locked once an encounter or
    battle has begun.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` - an encounter or battle is underway, or the
          game is over.
        - `exploration.party.bad_order` - `order` does not name exactly the current
          members, each once.

    Events:
        None. An accepted reorder changes state silently.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["reorder_party"] = "reorder_party"
    order: tuple[str, ...] = Field(min_length=1)
    """Every current member's id, each exactly once, front of the marching order first. The ids
    are [`MemberView.id`][osrlib.crawl.views.MemberView.id] values read off
    [`PlayerView.party`][osrlib.crawl.views.PlayerView.party]. Marching order decides who a
    door trap springs on, who reaches into a cache, and who stands in the front rank when a
    fight starts. Any other list is refused with `exploration.party.bad_order`."""


class OpenDoor(Command):
    """Open an unstuck, unlocked door on one side of the party's cell (zero time).

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]). An undiscovered secret
    door is refused exactly like blank wall, so commands never leak hidden geometry.
    Opening a door of an area whose room trap triggers on `open` is the trap's
    springing action: 2-in-6 to spring on the first living member in marching
    order, unless the trap has already been found.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` - the session is not exploring a dungeon.
        - `exploration.door.no_door` - no known door on that side of the cell
          (undiscovered secret doors included).
        - `exploration.door.already_open` - the door already stands open.
        - `exploration.door.locked` - the lock has not been picked or otherwise
          undone.
        - `exploration.door.stuck` - a stuck door needs
          [`ForceDoor`][osrlib.crawl.commands.ForceDoor].
        - `exploration.door.gate_refused` - the door has an authored
          condition ([`GateSpec`][osrlib.crawl.gates.GateSpec]) the party does not
          satisfy. The refusal includes the author's own text. Checked last, after
          every other refusal, so it fires only when the gate alone bars the way.

    Events:
        [`ItemConsumedEvent`][osrlib.crawl.events.ItemConsumedEvent] first when the
        gate's condition consumes what it asks for, then
        [`DoorEvent`][osrlib.crawl.events.DoorEvent] with code
        `exploration.door.opened` (with the gate's success text when its author
        wrote one). A door trap's spring check then posts a
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
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["open_door"] = "open_door"
    direction: Direction
    """Which side of the party's cell the door is on. Read the cell's sides from
    [`ExploredLevelView.edges`][osrlib.crawl.views.ExploredLevelView.edges]: an edge of kind
    `door` is one you can name here. An undiscovered secret door shows as a wall until a
    `secret_doors` [`Search`][osrlib.crawl.commands.Search] finds it."""


class CloseDoor(Command):
    """Close an open door on one side of the party's cell (zero time).

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]).

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
    """Which side of the party's cell the door is on. Only a door the party can see counts, so
    this is one of the `door` edges in
    [`ExploredLevelView.edges`][osrlib.crawl.views.ExploredLevelView.edges]. A wedged door
    won't swing, so pull the spike before you close it."""


class ForceDoor(Command):
    """Force a stuck door with the character's STR open-doors check, at the cost of noise.

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]). Any attempt bangs on the
    door, so the next wandering check takes the noise bonus, and a failed attempt
    alerts the room beyond, denying the party surprise there. A successful force
    is an opening: an unfound `open`-trigger room trap on either adjoining area
    gets its 2-in-6 spring check, and the forcing character is the one it lands
    on, or the next member standing if an earlier spring felled them.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` - the session is not exploring a dungeon.
        - `session.command.unknown_member` - `character_id` names no party member.
        - `session.command.member_incapacitated` - the member cannot act.
        - `exploration.door.no_door` - no known door on that side of the cell.
        - `exploration.door.already_open` - the door already stands open.
        - `exploration.door.locked` - locked doors need
          [`PickLock`][osrlib.crawl.commands.PickLock], not muscle.
        - `exploration.door.not_stuck` - an unstuck door opens with
          [`OpenDoor`][osrlib.crawl.commands.OpenDoor].
        - `exploration.door.gate_refused` - the door has an authored
          condition ([`GateSpec`][osrlib.crawl.gates.GateSpec]) the party does not
          satisfy. Checked before the shoulder ever hits the door: a gate-refused
          forcing makes no noise, denies no surprise, and rolls nothing.

    Events:
        [`ItemConsumedEvent`][osrlib.crawl.events.ItemConsumedEvent] first when the
        gate's condition consumes what it asks for, then
        [`DoorEvent`][osrlib.crawl.events.DoorEvent] with code
        `exploration.door.forced` on success (with the gate's success text when
        its author wrote one) or `exploration.door.stuck` on failure, then the trap
        events when a door trap's spring check runs.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["force_door"] = "force_door"
    direction: Direction
    """Which side of the party's cell the stuck door is on, from the `door` edges in
    [`ExploredLevelView.edges`][osrlib.crawl.views.ExploredLevelView.edges]. An unstuck door
    is refused with `exploration.door.not_stuck`. Open that one with
    [`OpenDoor`][osrlib.crawl.commands.OpenDoor] instead and make no noise."""
    character_id: str
    """The member who puts a shoulder to the door, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. Their STR sets the open-doors chance,
    so send the strongest member. The same member is the one an unfound door trap springs on
    when the door gives way."""


class WedgeDoor(Command):
    """Wedge a door with an iron spike so it cannot swing shut (zero time).

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]). Any living member's
    spike serves, and one iron spike is consumed.

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
    door as it stands, open or shut, so wedge an open door before you walk through it if you
    want a way back."""


class ListenAtDoor(Command):
    """Listen at a door: once per character per door, ever (zero time).

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]), and the listener needs
    light (infravision suffices). Hearing occupants marks the party aware for the
    room's eventual encounter.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` - the session is not exploring a dungeon.
        - `session.command.unknown_member` - `character_id` names no party member.
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
    member gets one attempt at each door for the rest of the game, so spend them in order of
    chance. A thief listens on their class's hear-noise row, dwarves, elves, and halflings on
    2 in 6, and everyone else on 1 in 6."""


class PickLock(Command):
    """Pick a locked door's lock: thief-only, needs thieves' tools, one turn.

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]). A failed attempt locks
    that character out of that lock until the next level gain.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` - the session is not exploring a dungeon.
        - `session.command.unknown_member` - `character_id` names no party member.
        - `session.command.member_incapacitated` - the member cannot act.
        - `exploration.lock.not_a_thief` - the member has no thief skills.
        - `exploration.lock.no_tools` - the member carries no thieves' tools.
        - `exploration.door.no_door` - no known door on that side of the cell.
        - `exploration.lock.not_locked` - the door has no lock left to pick.
        - `exploration.action.requires_light` - picking needs real light, and
          infravision does not suffice.
        - `exploration.lock.locked_out` - this character already failed here at
          their current level.

    Events:
        [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent] with the
        skill roll and, on success, a [`DoorEvent`][osrlib.crawl.events.DoorEvent]
        with code `exploration.door.unlocked`. The attempt costs one turn, whose
        bookkeeping (light burn-down, the rest cadence, wandering checks) reports
        through its own events.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["pick_lock"] = "pick_lock"
    direction: Direction
    """Which side of the party's cell the locked door is on, from the `door` edges in
    [`ExploredLevelView.edges`][osrlib.crawl.views.ExploredLevelView.edges]. A door that isn't
    locked is refused with `exploration.lock.not_locked`."""
    character_id: str
    """The thief who picks, by [`MemberView.id`][osrlib.crawl.views.MemberView.id]. The member
    needs thief skills and thieves' tools in their pack. A failed attempt locks that character
    out of that lock until they gain a level, so it's worth sending the best picker first."""


class Search(Command):
    """Search the party's cell for one hidden-feature kind (one turn).

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]), with light (infravision
    suffices). Each character gets one attempt per cell per kind, ever. A
    `room_traps` search covers the cell's door edges too: an `open`-trigger trap
    in an area beyond a known door is findable from this side of it, and a found
    trap never springs. An undiscovered secret door hides its trap along with
    itself, so discovering one clears that cell's `room_traps` attempts and every
    member may search the cell again for the trap the door was hiding. The
    referee's [`SetDoorState`][osrlib.crawl.commands.SetDoorState] with
    `discovered=True` refunds them the same way. Attempts of other kinds, and
    attempts on other cells, stand.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` - the session is not exploring a dungeon.
        - `session.command.unknown_member` - `character_id` names no party member.
        - `session.command.member_incapacitated` - the member cannot act.
        - `exploration.action.requires_light` - the party is in the dark and the
          searcher lacks infravision.
        - `exploration.search.already_tried` - this character already searched this
          cell for this kind.

    Events:
        [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent] with the
        roll, a [`TrapEvent`][osrlib.crawl.events.TrapEvent] when a room trap is
        found, then
        [`SearchCompletedEvent`][osrlib.crawl.events.SearchCompletedEvent] naming
        what turned up. A trap found through a door names that door on both: the
        trap event's `direction` and a third segment on the search token,
        `"room_trap:<area id>:<direction>"`. A trap found inside its own area names
        no door. One turn passes with its usual follow-on events.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["search"] = "search"
    character_id: str
    """The member who searches, by [`MemberView.id`][osrlib.crawl.views.MemberView.id]. The
    chance depends on class and race, so match the searcher to `kind`. Elves find secret doors
    on 2 in 6, dwarves find room traps and construction tricks on 2 in 6, and everyone else
    has 1 in 6 for doors and traps and no chance at tricks."""
    kind: Literal["secret_doors", "room_traps", "construction"]
    """Which kind of hidden thing to look for. `secret_doors` checks the four edges of the
    party's cell. `room_traps` checks the cell's own area and the areas behind its known doors,
    so a trap that springs when a door opens can be found from the corridor first.
    `construction` checks the cell's authored tricks, like a sliding wall or a false floor.
    Each character gets one attempt per cell per kind, so a party covers a room by sending
    different members, or the same member for each kind in turn."""


class InspectTreasure(Command):
    """Search a treasure feature for a treasure trap: thief-only, one turn.

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]), with light. One attempt
    per character per feature.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` - the session is not exploring a dungeon.
        - `session.command.unknown_member` - `character_id` names no party member.
        - `session.command.member_incapacitated` - the member cannot act.
        - `exploration.trap.not_a_thief` - the member has no thief skills.
        - `exploration.feature.unknown` - `feature_id` names no treasure cache on
          this cell.
        - `exploration.action.requires_light` - inspecting needs real light.
        - `exploration.search.already_tried` - this character already inspected
          this feature.

    Events:
        [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent] with the
        skill roll, then a [`TrapEvent`][osrlib.crawl.events.TrapEvent] with code
        `exploration.trap.found` or a
        [`SearchCompletedEvent`][osrlib.crawl.events.SearchCompletedEvent]
        reporting nothing. One turn passes with its usual follow-on events.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["inspect_treasure"] = "inspect_treasure"
    character_id: str
    """The thief who inspects, by [`MemberView.id`][osrlib.crawl.views.MemberView.id]. Only a
    member with thief skills can find a treasure trap, and each one gets a single attempt per
    cache."""
    feature_id: str
    """Which cache to check for a trap. An authored cache is named by its
    [`FeatureSpec.id`][osrlib.crawl.dungeon.FeatureSpec.id] from the adventure document. A
    cache the engine rolled is named by the id on
    [`HoardGeneratedEvent.cache_ref`][osrlib.crawl.events.HoardGeneratedEvent.cache_ref],
    which is a referee-visibility event. The cache has to be on the party's own cell."""


class RemoveTreasureTrap(Command):
    """Remove a found treasure trap: thief-only, one turn, and failure springs it.

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]), with light, and the trap
    must already have been found by
    [`InspectTreasure`][osrlib.crawl.commands.InspectTreasure].

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` - the session is not exploring a dungeon.
        - `session.command.unknown_member` - `character_id` names no party member.
        - `session.command.member_incapacitated` - the member cannot act.
        - `exploration.trap.not_a_thief` - the member has no thief skills.
        - `exploration.feature.unknown` - `feature_id` names no trapped feature on
          this cell.
        - `exploration.trap.not_found` - the trap has not been found yet.
        - `exploration.trap.already_resolved` - the trap was already removed or has
          already sprung.
        - `exploration.action.requires_light` - removal needs real light.
        - `exploration.search.already_tried` - this character already attempted the
          removal.

    Events:
        [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent] with the
        skill roll, then a [`TrapEvent`][osrlib.crawl.events.TrapEvent]:
        `exploration.trap.removed` on success, `exploration.trap.sprung` on failure.
        The sprung trap resolves at once against the thief, each step its own event:
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
    this member, so the party's best thief is also the one most at risk."""
    feature_id: str
    """Which trapped cache to disarm, named the same way as for
    [`InspectTreasure`][osrlib.crawl.commands.InspectTreasure]. The trap has to have been found
    already, or the command comes back with `exploration.trap.not_found`."""


class TakeTreasure(Command):
    """Empty a cache or pile into the party's packs (one turn, RAW).

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]). `feature_id` names an
    authored cache, an engine-generated cache, or the literal `pile` for goods
    dropped on the cell.

    By default the haul spreads across the living members: items go to a
    character whose class can use them (the fighter takes the plate mail, the
    magic-user the arcane scroll), gems and jewellery divide by worth, and coins
    divide evenly denomination by denomination. Nothing is ever loaded past the
    1,600-coin maximum load, so a pickup cannot immobilise the party. The group
    moves at its slowest member's rate, so overloading one member slows everyone.
    Name `recipient_id` to override: that member alone fills their pack, up to their
    own maximum load. Whatever exceeds the carriers' capacity stays in the drop pile on
    the cell. Nothing is destroyed, and the party can lighten up and come back for
    it. This first pass is automatic bookkeeping, not a ruling: rearrange it freely
    with [`GiveItems`][osrlib.crawl.commands.GiveItems], and note that XP divides
    evenly however the goods end up split, per RAW.

    The named recipient, or the leading living member when none is named, is the
    one who reaches in, so taking a trapped cache with its trap unresolved risks
    springing it on them.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` - the session is not exploring a dungeon.
        - `session.command.no_living_members` - no one is left to carry.
        - `session.command.unknown_member` - `recipient_id` names no party member.
        - `session.command.member_incapacitated` - the recipient cannot act.
        - `exploration.feature.unknown` - nothing by that id on this cell.
        - `exploration.feature.emptied` - the cache has already been emptied.

    Events:
        One [`ItemAcquiredEvent`][osrlib.crawl.events.ItemAcquiredEvent] per member
        who took something, listing their goods and coin value with `origin`
        `"treasure"`, in marching order,
        and an [`ItemsLeftBehindEvent`][osrlib.crawl.events.ItemsLeftBehindEvent]
        when the party could not carry it all. An unresolved treasure trap rolls first
        ([`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent], a
        [`TrapEvent`][osrlib.crawl.events.TrapEvent], and the trap's resolution
        when it springs). Under the immediate XP timing an
        [`XpAwardedEvent`][osrlib.crawl.events.XpAwardedEvent] follows per member.
        One turn passes.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["take_treasure"] = "take_treasure"
    feature_id: str
    """What to empty. An authored cache is named by its
    [`FeatureSpec.id`][osrlib.crawl.dungeon.FeatureSpec.id]. A cache the engine rolled is named
    by the id on
    [`HoardGeneratedEvent.cache_ref`][osrlib.crawl.events.HoardGeneratedEvent.cache_ref]. The
    literal `"pile"` names the loose goods lying on the party's cell, which
    [`PlayerView.piles`][osrlib.crawl.views.PlayerView.piles] lists by cell reference. A cache
    already emptied comes back with `exploration.feature.emptied`."""
    recipient_id: str | None = None
    """One member to take the whole haul, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id], or `None` to spread it across the
    party. The named member fills their own pack up to their own maximum load and the rest
    stays on the cell, so naming a recipient is the way to keep a specific item with a
    specific character. Whoever is named is also the one who reaches in, and so the one an
    unresolved treasure trap springs on."""


class DropItems(Command):
    """Drop items and coins onto the party's cell (or the pursuit trail).

    Each `item_ids` entry drops one unit (repeat an id for more). Legal while
    exploring a dungeon and during an encounter, where dropping treasure or food is
    the pursuit-distraction move.

    Modes:
        `exploring`, `encounter`

    Rejections:
        - `session.command.wrong_mode` - the session is in town, in battle, or
          over.
        - `session.command.unknown_member` - `character_id` names no party member.
        - `session.command.member_incapacitated` - the member cannot act.
        - `items.curse.stuck` - a revealed cursed item cannot be discarded.
        - `exploration.item.not_carried` - the member lacks an item or the coins.
        - `encounter.none_active` - a second check behind the mode gate, not
          reachable through normal play.

    Events:
        [`ItemsDroppedEvent`][osrlib.crawl.events.ItemsDroppedEvent] with what
        fell. In an encounter the round then closes and the monsters act per their
        stance. Mid-pursuit, a
        [`PursuitEvent`][osrlib.crawl.events.PursuitEvent] round resolves with the
        drop as bait instead.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING, SessionMode.ENCOUNTER})

    command_type: Literal["drop_items"] = "drop_items"
    character_id: str
    """The member whose pack and purse the goods leave, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]."""
    item_ids: tuple[str, ...] = ()
    """What to drop, one unit per entry, so repeat an id to drop more than one. A mundane item is
    named by its catalog id, like `torch` or `rope_hemp`. A magic item or a gem is named by
    the per-instance id it has in
    [`MemberView.inventory`][osrlib.crawl.views.MemberView.inventory]. Naming the same magic
    instance twice is refused, because the whole instance leaves on the first naming. A
    revealed cursed item can't be dropped."""
    coins: Coins = Coins()
    """Coins to drop alongside the items, by denomination. The default drops none. Dropped coins
    land in the cell's pile with everything else, and the party can pick them back up with
    [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure] naming `"pile"`."""


class GiveItems(Command):
    """Hand items and coins from one party member to another (zero time).

    The distribute-the-load move: `character_id` is the giver, `recipient_id` the
    companion who takes the goods. Each `item_ids` entry gives one unit (repeat an
    id for more). A given magic item releases any worn effects first and lands
    unequipped in the recipient's pack. Legal in town and while exploring, not
    mid-encounter or in battle. Both members must be able-bodied.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` - an encounter or battle is underway, or the
          game is over.
        - `session.command.unknown_member` - `character_id` or `recipient_id` names
          no party member.
        - `session.command.member_incapacitated` - the giver or recipient cannot
          act.
        - `exploration.give.same_member` - giver and recipient are the same member.
        - `items.curse.stuck` - a revealed cursed item cannot be handed off.
        - `exploration.item.not_carried` - the giver lacks an item or the coins.

    Events:
        [`ItemsGivenEvent`][osrlib.crawl.events.ItemsGivenEvent] with what changed
        hands. A worn magic item's effects release
        ([`EffectReleasedEvent`][osrlib.core.events.EffectReleasedEvent]).
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["give_items"] = "give_items"
    character_id: str
    """The member handing the goods over, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. Naming the recipient here as well is
    refused with `exploration.give.same_member`."""
    recipient_id: str
    """The member taking the goods, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. Both members have to be alive and able
    to act."""
    item_ids: tuple[str, ...] = ()
    """What changes hands, one unit per entry, named the same way as in
    [`DropItems.item_ids`][osrlib.crawl.commands.DropItems.item_ids]: a catalog id for a
    mundane item, a per-instance id for a magic item or a gem. A magic item that was worn
    releases its effects first and lands unequipped in the recipient's pack."""
    coins: Coins = Coins()
    """Coins to hand over, by denomination. The default hands over none. Coins move purse to
    purse, which is how a party evens out the weight after a big haul."""


class LightSource(Command):
    """Light a torch or lantern, or ignite dropped oil (one round).

    Legal in town and while exploring. Without an open flame already burning in
    the party, the bearer needs a tinder box, and striking it is a 2-in-6 chance.
    The round is spent per attempt (RAW). Lighting an `oil_flask` ignites a flask
    previously dropped on the party's cell as a burning pool.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` - an encounter or battle is underway, or the
          game is over.
        - `session.command.unknown_member` - `character_id` names no party member.
        - `session.command.member_incapacitated` - the member cannot act.
        - `exploration.light.not_a_source` - `item_id` is not `torch`, `lantern`,
          or `oil_flask`.
        - `exploration.item.not_carried` - the member lacks the source (or oil for
          the lantern), or no dropped flask lies on the cell.
        - `exploration.light.no_flame` - no open flame and no tinder box.

    Events:
        [`LightEvent`][osrlib.crawl.events.LightEvent] with code
        `exploration.light.lit`, with an
        [`EffectAttachedEvent`][osrlib.core.events.EffectAttachedEvent] for the
        burn-down effect, or `exploration.light.failed` when the tinder does not
        catch. One round passes.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["light_source"] = "light_source"
    character_id: str
    """The member who strikes the light and then bears it, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. The light travels with this member, so
    a torch bearer who goes down takes the light with them."""
    item_id: str
    """Which source to light: `torch`, `lantern`, or `oil_flask`. A torch is consumed and burns
    for its own span. A lantern burns one carried oil flask and keeps the lantern.
    `oil_flask` ignites a flask already dropped on the party's cell as a burning pool, not one
    in the pack, so drop it with [`DropItems`][osrlib.crawl.commands.DropItems] first. Any
    other id comes back with `exploration.light.not_a_source`."""


class ExtinguishSource(Command):
    """Extinguish the bearer's burning source, forfeiting the remainder (zero time).

    Legal in town and while exploring. A doused torch or lantern is spent, and the
    remaining burn time does not bank.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` - an encounter or battle is underway, or the
          game is over.
        - `session.command.unknown_member` - `character_id` names no party member.
        - `session.command.member_incapacitated` - the member cannot act.
        - `exploration.light.not_burning` - the member carries no burning torch or
          lantern.

    Events:
        An [`EffectReleasedEvent`][osrlib.core.events.EffectReleasedEvent] and a
        [`LightEvent`][osrlib.crawl.events.LightEvent] with code
        `exploration.light.extinguished` per doused source.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["extinguish_source"] = "extinguish_source"
    character_id: str
    """The bearer whose light goes out, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. Every burning source this member
    carries is doused, and the remaining burn time is lost rather than banked."""


class EquipItem(Command):
    """Equip an item from a member's item list (zero time).

    Legal in town and while exploring. Class armour and weapon policies validate
    before anything changes. `item_id` is the magic item's instance id for a magic
    item, or the catalog id for a mundane one, which has no per-instance id. A
    mundane id is either a shipped one (from
    [`load_equipment`][osrlib.data.load_equipment], see [the equipment id
    index][equipment-index]) or one the adventure bundles, which no index
    documents.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` - an encounter or battle is underway, or the
          game is over.
        - `session.command.unknown_member` - `character_id` names no party member.
        - `session.command.member_incapacitated` - the member cannot act.
        - `exploration.item.not_carried` - nothing by that id in the item list.
        - `items.equip.armour_forbidden`, `items.equip.armour_not_allowed`,
          `items.equip.shield_forbidden`, `items.equip.weapon_not_allowed`,
          `items.equip.weapon_forbidden` - the class policy forbids it.
        - `items.equip.two_handed_with_shield` - a two-handed weapon and a shield
          cannot pair.
        - `items.equip.not_equippable` - potions, scrolls, ammunition, and plain
          gear without a combat use do not equip.
        - `items.equip.not_usable` - the magic device is not usable by this class.
        - `items.ring.hands_full` - two rings are already worn.

    Events:
        Usually none. Equipping a worn magic item can attach its effects
        ([`EffectAttachedEvent`][osrlib.core.events.EffectAttachedEvent]). A cursed
        ring identifies and reveals at wearing
        ([`ItemIdentifiedEvent`][osrlib.crawl.events.ItemIdentifiedEvent],
        [`CurseRevealedEvent`][osrlib.crawl.events.CurseRevealedEvent]).
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["equip_item"] = "equip_item"
    character_id: str
    """The member doing the equipping, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. Their class decides what they may wear
    and wield, and the class policy is checked before anything moves."""
    item_id: str
    """What to equip, which has to be in this member's item list already. A magic item is named by
    its per-instance id, because each one is a distinct object. A mundane item is named by its
    catalog id, because mundane items stack and have no per-instance id. Both are visible in
    [`MemberView.inventory`][osrlib.crawl.views.MemberView.inventory]."""


class UnequipItem(Command):
    """Return an equipped item to the member's item list (zero time).

    Legal in town and while exploring. A revealed cursed item stays put until
    *remove curse*.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` - an encounter or battle is underway, or the
          game is over.
        - `session.command.unknown_member` - `character_id` names no party member.
        - `session.command.member_incapacitated` - the member cannot act.
        - `exploration.item.not_equipped` - nothing by that id is equipped.
        - `items.curse.stuck` - a revealed cursed item cannot be removed.

    Events:
        Usually none. A worn magic item's effects release
        ([`EffectReleasedEvent`][osrlib.core.events.EffectReleasedEvent]).
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["unequip_item"] = "unequip_item"
    character_id: str
    """The member doing the unequipping, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]."""
    item_id: str
    """What to take off or put away, named the same way as in
    [`EquipItem.item_id`][osrlib.crawl.commands.EquipItem.item_id] and currently in a slot
    rather than in the pack. A cursed item whose curse has been revealed stays where it is
    until a *remove curse*."""


class Rest(Command):
    """Rest: one turn (the cadence rest), a night (48 turns), or a full day (144).

    Legal in town and while exploring. In the dungeon a wandering encounter can
    interrupt the rest, and a full day of rest also applies natural healing.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` - an encounter or battle is underway, or the
          game is over.

    Events:
        [`RestedEvent`][osrlib.crawl.events.RestedEvent] with code
        `exploration.rest.rested`, or `exploration.rest.interrupted` when a
        wandering encounter breaks the rest. Clearing fatigue or exhaustion reports
        a [`FatigueEvent`][osrlib.crawl.events.FatigueEvent] or
        [`ExhaustionEvent`][osrlib.crawl.events.ExhaustionEvent], and a full day's
        natural healing reports an
        [`HealingAppliedEvent`][osrlib.core.events.HealingAppliedEvent]. The
        elapsed turns report their own bookkeeping (light burn-down, provisions,
        wandering checks).
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["rest"] = "rest"
    kind: Literal["turn", "night", "day"]
    """How long to rest. `turn` is a single turn, the breather the exploration cadence calls for
    every sixth turn. `night` is 48 turns and counts as sleep. `day` is 144 turns, counts as
    sleep, and applies natural healing to every living member. Sleep is what
    [`PrepareSpells`][osrlib.crawl.commands.PrepareSpells] needs, so a caster's day starts with
    a `night` or a `day` rest. In a dungeon a wandering encounter can cut any of the three
    short."""


class PrepareSpells(Command):
    """Prepare a caster's daily spells: once per sleep, after an uninterrupted night, six turns.

    Legal in town and while exploring. The caster must have slept (a night or day
    [`Rest`][osrlib.crawl.commands.Rest]) since the last preparation, and the
    selections replace the memorized list wholesale.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` - an encounter or battle is underway, or the
          game is over.
        - `session.command.unknown_member` - `character_id` names no party member.
        - `session.command.member_incapacitated` - the member cannot act.
        - `magic.memorize.needs_sleep` - no sleep since the last preparation.
        - `magic.memorize.not_a_caster` - the class casts no spells.
        - `magic.memorize.unknown_spell` - a selection names no known spell.
        - `magic.memorize.wrong_list` - a selection is off the caster's spell list.
        - `magic.memorize.divine_reverses_at_cast` - divine casters choose the
          reversed form at casting, not at prayer.
        - `magic.memorize.not_in_book` - an arcane selection is missing from the
          spell book.
        - `magic.memorize.not_reversible` - a reversed selection has no reversed
          form.
        - `magic.memorize.slots_exceeded` - more selections at some spell level
          than the caster has slots.

    Events:
        [`SpellsMemorizedEvent`][osrlib.core.events.SpellsMemorizedEvent] with the
        prepared list. Six turns pass with their usual follow-on events.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["prepare_spells"] = "prepare_spells"
    character_id: str
    """The caster preparing, by [`MemberView.id`][osrlib.crawl.views.MemberView.id]. The member
    has to have slept since their last preparation, so pair this with a `night` or `day`
    [`Rest`][osrlib.crawl.commands.Rest]."""
    selections: tuple[MemorizedSpell, ...] = ()
    """The spells to hold ready, replacing the caster's current list outright rather than adding
    to it. Each [`MemorizedSpell`][osrlib.core.spells.MemorizedSpell] names a spell id and, for
    an arcane caster, which form of a reversible spell the copy takes. Repeat a spell to hold
    more than one copy of it. The count at each spell level has to fit the caster's slots at
    that level, and an empty tuple clears the list. The caster's current list is on
    [`MemberView.memorized_spells`][osrlib.crawl.views.MemberView.memorized_spells]."""


class LearnSpell(Command):
    """Add a spell to an arcane caster's spell book, for a level gain or a mentor's lessons.

    Legal in town and while exploring. The book fits, per spell level, at most
    the caster's current slot count at that level, and it never shrinks, so a
    drained caster's book may sit over capacity, taking nothing more until
    capacity catches up. No game time passes: the fiction around the learning
    (the mentor's week, a copied scroll's costs) belongs to the game.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` - an encounter or battle is underway, or the
          game is over.
        - `session.command.unknown_member` - `character_id` names no party member.
        - `session.command.member_incapacitated` - the member cannot act.
        - `magic.book.not_arcane` - the class keeps no spell book.
        - `magic.book.unknown_spell` - `spell_id` names no spell.
        - `magic.book.wrong_list` - the spell is off the caster's spell list.
        - `magic.book.duplicate` - the book already contains the spell.
        - `magic.book.capacity_exceeded` - no open slot at the spell's level.

    Events:
        [`SpellBookUpdatedEvent`][osrlib.core.events.SpellBookUpdatedEvent] with
        the added spell. No game time passes.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["learn_spell"] = "learn_spell"
    character_id: str
    """The arcane caster whose book grows, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. A class that keeps no spell book is
    refused with `magic.book.not_arcane`."""
    spell_id: str
    """The spell to write into the book, from
    [`load_spells`][osrlib.data.load_spells] (see [the spell id index][spells-index]). It has
    to be on the caster's own spell list, and the book fits at most as many spells at each
    level as the caster has slots at that level."""


class CastSpell(Command):
    """Cast a memorized spell outside battle (one round).

    `targets` are entity ids, or `cell:` references for location-bound casts. In
    encounter mode a hostile cast is opened through
    [`EngageBattle`][osrlib.crawl.commands.EngageBattle] and the first round's
    declarations instead, and in battle casting is a declaration kind.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` - an encounter or battle is underway, or the
          game is over.
        - `session.command.unknown_member` - `character_id` names no party member.
        - `session.command.member_incapacitated` - the member cannot act.
        - `magic.cast.unknown_spell` - `spell_id` names no spell.
        - `magic.cast.silenced_area` - a *silence* effect covers the party's cell.
        - `magic.cast.unknown_target` - a target reference resolves to nothing.
        - `magic.cast.not_memorized` - no memorized copy (non-casters included).
        - `magic.cast.caster_incapacitated`, `magic.cast.caster_restrained`,
          `magic.cast.anti_magic_shell` - the caster cannot cast right now.
        - `magic.cast.not_reversible` - `reversed` on a spell with no reversed
          form.
        - `magic.cast.unknown_mode` - `mode` names no mode of the spell.
        - `magic.cast.target_count` - the wrong number of targets for the mode.
        - `magic.cast.out_of_range` - a target lies beyond the spell's range.

    Events:
        [`SpellCastEvent`][osrlib.core.events.SpellCastEvent] plus the spell's own
        resolution: saving throws, damage, healing, and effect attachments, each
        its own event. One round passes.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["cast_spell"] = "cast_spell"
    character_id: str
    """The caster, by [`MemberView.id`][osrlib.crawl.views.MemberView.id]. They need a memorized
    copy of the spell, which casting spends."""
    spell_id: str
    """The spell to cast, from [`load_spells`][osrlib.data.load_spells] (see [the spell id
    index][spells-index]). A caster with no memorized copy is refused with
    `magic.cast.not_memorized`, which is also what a non-caster gets."""
    mode: str
    """Which of the spell's numbered usages to cast, by that usage's
    [`SpellMode.key`][osrlib.core.spells.SpellMode] on the form you're casting. Spells whose
    page lists several usages, like *cure light wounds* and *light*, have one mode each, and a
    single-usage spell still needs its one key named. A key the form doesn't have comes back as
    `magic.cast.unknown_mode`."""
    reversed: bool = False
    """Cast the reversed form, like *cause light wounds* for *cure light wounds*. An arcane
    caster fixes the form when memorizing, so a reversed cast needs a reversed copy in hand. A
    divine caster memorizes the normal form and chooses here at the altar. A spell with no
    reversed form is refused with `magic.cast.not_reversible`."""
    targets: tuple[str, ...] = ()
    """Who or what the spell lands on, as entity ids: a party member's
    [`MemberView.id`][osrlib.crawl.views.MemberView.id], a monster's id, or a cell reference in
    the `cell:{dungeon}:{level}:{x},{y}` form that
    [`cell_ref`][osrlib.crawl.dungeon.cell_ref] builds, for a spell that anchors to a place
    rather than a creature. How many the mode takes is the mode's own targeting, and the wrong
    count is refused with `magic.cast.target_count`."""


class UseItem(Command):
    """Use a magic item: drink a potion, read a scroll, activate a device (one round).

    One round is the RAW activation cost (drinking is one round). `target_id`
    names a character (the staff of healing's touch) or an encounter group (a
    device's area). `spell_id`, `mode`, and `targets` select the inscribed spell
    and its targets when reading a multi-spell scroll (the
    [`CastSpell`][osrlib.crawl.commands.CastSpell] surface). In battle, item use
    is the `use_item` declaration instead. First meaningful use identifies the
    item, and reveals its curse.

    Modes:
        `exploring`, `encounter`

    Rejections:
        - `session.command.wrong_mode` - the session is in town, in battle, or
          over.
        - `session.command.unknown_member` - `character_id` names no party member.
        - `session.command.member_incapacitated` - the member cannot act.
        - `exploration.item.not_carried` - the member carries no magic item with
          that instance id.
        - `items.use.not_usable` - the item has no usable action, or the class
          cannot use the device.
        - Scrolls: `exploration.action.requires_light` (reading needs real light),
          `items.scroll.spent`, `items.scroll.no_such_spell`,
          `items.scroll.wrong_caster`, and the cast validation codes
          (`magic.cast.unknown_target`, `magic.cast.unknown_mode`,
          `magic.cast.target_count`, `magic.cast.out_of_range`,
          `magic.cast.caster_incapacitated`, `magic.cast.caster_restrained`,
          `magic.cast.anti_magic_shell`). A read is judged against a caster at
          the scroll's own level, the level
          [`cast_from_scroll`][osrlib.core.spells.cast_from_scroll] resolves it
          at, so a 6th-level reader of a *magic missile* scroll supplies one
          target and is refused three, and the refusal comes before the scroll is
          spent.
        - Devices: `items.device.inert` (no charges left),
          `items.use.target_required`, `items.use.unknown_target`, and
          `items.use.battle_only` (a striking effect is a battle declaration).

    Events:
        [`ItemUsedEvent`][osrlib.crawl.events.ItemUsedEvent] naming what happened
        (drunk, read, activated, or mixed potions, or a cursed scroll), with
        [`ItemIdentifiedEvent`][osrlib.crawl.events.ItemIdentifiedEvent] and
        [`CurseRevealedEvent`][osrlib.crawl.events.CurseRevealedEvent] at first
        meaningful use, then the item's own resolution: healing, saving throws,
        damage, effect attachments, and a scroll's
        [`SpellCastEvent`][osrlib.core.events.SpellCastEvent], each its own
        event. One round passes (in an encounter, the round beat follows instead).
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING, SessionMode.ENCOUNTER})

    command_type: Literal["use_item"] = "use_item"
    character_id: str
    """The member using the item, by [`MemberView.id`][osrlib.crawl.views.MemberView.id]. They
    have to be carrying it, and for a device their class has to be able to use it."""
    item_id: str
    """The magic item to use, by its per-instance id from
    [`MemberView.inventory`][osrlib.crawl.views.MemberView.inventory]. Only magic items are
    usable here, so a mundane item comes back as `exploration.item.not_carried`: the member
    carries no magic item with that id."""
    target_id: str | None = None
    """Who the item is used on, for an item that reaches somebody: a party member's id for a
    touch, or an encounter group's
    [`EncounterGroupView.id`][osrlib.crawl.views.EncounterGroupView.id] for a device that
    covers an area. Leave it `None` for a potion the user drinks."""
    spell_id: str | None = None
    """Which spell to read off a scroll that has more than one, from
    [`load_spells`][osrlib.data.load_spells] (see [the spell id index][spells-index]). Leave it
    `None` for anything that isn't a scroll."""
    mode: str | None = None
    """Which of the read spell's usages to cast, by that usage's
    [`SpellMode.key`][osrlib.core.spells.SpellMode]. `None` casts the spell's first usage,
    which is what a single-usage scroll wants."""
    targets: tuple[str, ...] = ()
    """The read spell's targets, named exactly as in
    [`CastSpell.targets`][osrlib.crawl.commands.CastSpell.targets]. When it's empty `target_id`
    stands in as the single target, so a one-target scroll needs only the one field."""


class IdentifyItem(Command):
    """Referee: identify a magic item outright, the game-driven identification path.

    Referee commands are legal in every mode, terminal modes included, and are
    logged and replayed like any other.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        - `session.command.unknown_member` - `character_id` names no party member.
        - `session.command.unknown_item` - the member carries no magic item with
          that instance id.

    Events:
        [`ItemIdentifiedEvent`][osrlib.crawl.events.ItemIdentifiedEvent]. A cursed
        item also reveals with a
        [`CurseRevealedEvent`][osrlib.crawl.events.CurseRevealedEvent].
    """

    command_type: Literal["identify_item"] = "identify_item"
    character_id: str
    """The member holding the item, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]."""
    item_id: str
    """The magic item to identify, by its per-instance id. The masked name a player sees is on
    [`MemberView.inventory`][osrlib.crawl.views.MemberView.inventory], and the true one is in
    the referee view. A mundane item has nothing to identify and comes back as
    `session.command.unknown_item`."""


class UseStairs(Command):
    """Take the stair, ladder, or other transition on the party's cell.

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]) and standing on a cell
    with an authored transition. The move costs one unexplored-cell step of
    movement.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` - the session is not exploring a dungeon.
        - `exploration.stairs.none` - no transition on the party's cell.
        - `exploration.transition.gate_refused` - the transition has an
          authored condition ([`GateSpec`][osrlib.crawl.gates.GateSpec]) the party
          does not satisfy. The refusal includes the author's own text, and costs no
          movement, no time, and no toll.

    Events:
        [`ItemConsumedEvent`][osrlib.crawl.events.ItemConsumedEvent] first when the
        gate's condition consumes what it asks for, since the toll is paid at the
        threshold, then
        [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent] when the
        level or dungeon changes, with the gate's success text when its author
        wrote one. That arrival names the crossing it rode: `via` is the
        transition's `kind`, and `transition_ref` is the cell the transition stands
        on, which is the cell the party left. Leaving a level shuts the doors the
        party opened on it
        ([`DoorEvent`][osrlib.crawl.events.DoorEvent]s).

        Arrival then runs the destination cell's entry checks, the same ones
        [`MoveParty`][osrlib.crawl.commands.MoveParty] runs: a
        [`HoardGeneratedEvent`][osrlib.crawl.events.HoardGeneratedEvent] for area
        treasure, a
        [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent] and a
        [`TrapEvent`][osrlib.crawl.events.TrapEvent] for a room trap with its
        resolution, and a keyed encounter's opening
        ([`SurpriseRolledEvent`][osrlib.crawl.events.SurpriseRolledEvent]s,
        [`EncounterStartedEvent`][osrlib.crawl.events.EncounterStartedEvent],
        [`ReactionRolledEvent`][osrlib.core.events.ReactionRolledEvent],
        [`StanceChangedEvent`][osrlib.crawl.events.StanceChangedEvent], and
        [`BattleStartedEvent`][osrlib.crawl.events.BattleStartedEvent] on an attacks
        stance). The movement cost accrues toward the turn clock, and a turn
        boundary brings the same bookkeeping a step does.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["use_stairs"] = "use_stairs"


class EnterDungeon(Command):
    """Travel from town to a dungeon's entrance and start exploring.

    The party must be in town. Travel takes the adventure's authored cost in
    turns, and arrival places the party at the entrance and switches the session to
    `exploring`. Departure also snapshots the party's treasure valuation, because
    the end-of-adventure XP award is the delta against it.

    Modes:
        `town`

    Rejections:
        - `session.command.wrong_mode` - the party is not in town.
        - `session.command.unknown_location` - `dungeon_id` names no dungeon, or
          the dungeon has no entrance level.

    Events:
        The travel time's own bookkeeping first:
        [`EffectExpiredEvent`][osrlib.core.events.EffectExpiredEvent] with a
        [`LightEvent`][osrlib.crawl.events.LightEvent] for a light burning out, and
        [`ProvisionsEvent`][osrlib.crawl.events.ProvisionsEvent] on a day boundary.
        Travel runs no wandering cadence and no rest cadence, so no
        [`WanderingCheckEvent`][osrlib.crawl.events.WanderingCheckEvent] or
        [`FatigueEvent`][osrlib.crawl.events.FatigueEvent] lands on the road.

        Then [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent] for
        the dungeon, `via` `"entrance"`, and the entrance cell's entry checks: a
        [`HoardGeneratedEvent`][osrlib.crawl.events.HoardGeneratedEvent] for area
        treasure, a
        [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent] and a
        [`TrapEvent`][osrlib.crawl.events.TrapEvent] for a room trap with its
        resolution, and a keyed encounter's opening
        ([`SurpriseRolledEvent`][osrlib.crawl.events.SurpriseRolledEvent]s,
        [`EncounterStartedEvent`][osrlib.crawl.events.EncounterStartedEvent],
        [`ReactionRolledEvent`][osrlib.core.events.ReactionRolledEvent],
        [`StanceChangedEvent`][osrlib.crawl.events.StanceChangedEvent], and
        [`BattleStartedEvent`][osrlib.crawl.events.BattleStartedEvent] on an attacks
        stance).
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.TOWN})

    command_type: Literal["enter_dungeon"] = "enter_dungeon"
    dungeon_id: str
    """Which dungeon to set out for, by
    [`DungeonSpec.id`][osrlib.crawl.dungeon.DungeonSpec] from the adventure's own `dungeons`.
    The party arrives on that dungeon's entrance level, at the entrance cell. An id the
    adventure doesn't have, or a dungeon with no entrance level, comes back as
    `session.command.unknown_location`."""


class TravelToTown(Command):
    """Travel from the dungeon entrance back to town (the same travel cost).

    The party must be exploring and standing on the entrance cell. Doors the party
    opened swing shut behind it, and under the on-return XP timing the adventure
    award pays out on arrival.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` - the session is not exploring a dungeon.
        - `exploration.travel.not_at_entrance` - the party is not on the entrance
          cell.

    Events:
        [`DoorEvent`][osrlib.crawl.events.DoorEvent]s for doors swinging shut,
        travel-time bookkeeping, then
        [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent] for
        town. Under the on-return XP timing an
        [`AdventureXpAwardEvent`][osrlib.crawl.events.AdventureXpAwardEvent] and
        per-member [`XpAwardedEvent`][osrlib.crawl.events.XpAwardedEvent]s follow.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["travel_to_town"] = "travel_to_town"


class PurchaseEquipment(Command):
    """Buy equipment in town: each `item_ids` entry buys one purchase lot (zero time).

    The party must be in town. The whole basket prices first, and if the member
    cannot afford the total, nothing is bought. The shop stocks only the shipped
    equipment lists ([`load_equipment`][osrlib.data.load_equipment], see [the
    equipment id index][equipment-index]): an item the adventure bundles is
    not for sale, however the party came by the id, and rejects as unstocked
    rather than as unknown.

    Modes:
        `town`

    Rejections:
        - `session.command.wrong_mode` - the party is not in town.
        - `session.command.unknown_member` - `character_id` names no party member.
        - `session.command.member_incapacitated` - the member cannot act.
        - `session.command.unknown_item` - an entry names no equipment item.
        - `items.purchase.not_stocked` - an entry names an item the adventure
          bundles, which the shop does not carry.
        - `items.purchase.insufficient_funds` - the purse cannot cover the total.

    Events:
        [`ItemAcquiredEvent`][osrlib.crawl.events.ItemAcquiredEvent] listing the
        purchases, `origin` `"purchase"`.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.TOWN})

    command_type: Literal["purchase_equipment"] = "purchase_equipment"
    character_id: str
    """The buyer, by [`MemberView.id`][osrlib.crawl.views.MemberView.id]. The whole basket is
    paid for out of this member's purse, and the goods land in their pack."""
    item_ids: tuple[str, ...] = Field(min_length=1)
    """What to buy, one purchase lot per entry, so repeat an id to buy more lots. A purchase lot
    is what one purchase at the item's listed price delivers: a lot of torches costs 1 gp and
    arrives as six torches, while a weapon or a suit of armour is a single item. Ids come from
    [`load_equipment`][osrlib.data.load_equipment] (see [the equipment id
    index][equipment-index]). An id the adventure bundles is not for sale and comes back as
    `items.purchase.not_stocked`."""


class SellTreasure(Command):
    """Sell valuables in town at full value (zero time).

    The party must be in town. Each entry names a carried valuable's instance id,
    and the coins credit its carrier's purse. osrlib adopts full `value_gp` as the
    sale price: the OSE SRD prices treasure but names no exchange spread, and full
    value keeps the 1-gp-1-XP identity clean. Magic items have no fixed sale value
    (RAW's own words) and are refused, and revealed curses stick.

    Modes:
        `town`

    Rejections:
        - `session.command.wrong_mode` - the party is not in town.
        - `town.sell.no_fixed_value` - magic items cannot be sold for a fixed
          price.
        - `exploration.item.not_carried` - no member carries a valuable with that
          instance id.

    Events:
        [`TreasureSoldEvent`][osrlib.crawl.events.TreasureSoldEvent] per selling
        member, with the credited value.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.TOWN})

    command_type: Literal["sell_treasure"] = "sell_treasure"
    item_ids: tuple[str, ...] = Field(min_length=1)
    """The gems and jewellery to sell, by the per-instance ids they have in
    [`MemberView.inventory`][osrlib.crawl.views.MemberView.inventory]. Each one is credited to
    whichever member is carrying it, so a mixed basket pays several purses. Magic items have no
    fixed price and come back as `town.sell.no_fixed_value`."""


HealingService = Literal[
    "cure_light_wounds",
    "cure_serious_wounds",
    "cure_disease",
    "neutralize_poison",
    "remove_curse",
    "raise_dead",
]
"""The temple's services, as the names
[`PurchaseHealing.service`][osrlib.crawl.commands.PurchaseHealing] accepts.

This is the closed set: the command rejects any other name, and
[`HEALING_SERVICES`][osrlib.crawl.exploration.HEALING_SERVICES] keys its price list by this
same type, so the list a town screen offers and the names the session accepts cannot drift
apart. Annotate a front end's own service parameter with it to get the same check from your
type checker."""


class PurchaseHealing(Command):
    """Buy a temple healing service in town (zero time).

    The party must be in town. The service list and prices are a documented
    adaptation, because the OSE SRD's base-town material is prose: *cure light
    wounds* 25 gp, *cure serious wounds* 100 gp, *cure disease* 150 gp,
    *neutralize poison* 150 gp, *remove curse* 200 gp, *raise dead* 1,500 gp. Each
    resolves through the kernel spell path with an abstract temple cleric at the
    minimum level able to cast the spell.

    The temple charges the party, not the patient. The fee is drawn from the treated
    member's purse first and then from the other members' purses in marching order,
    dead members included. Each purse pays in whole gold pieces, as much of what is
    still owed as its gold covers, so a purse that cannot cover the rest hands over
    all of its gold and keeps only what it is worth below a gold piece, and the last
    purse charged pays the outstanding remainder alone. Because the coin below a
    gold piece in a purse can never go toward the fee, what the party can spend is
    the whole gold pieces in its purses, not their total worth: two members holding
    12 gp and 5 sp each are worth 25 gp between them and are still refused a 25 gp
    service, keeping every coin.

    Charging the party is what makes *raise dead* buyable at all: the patient is
    dead, nothing can hand a corpse coin, and a party that splits its treasure never
    has 1,500 gp in one purse.

    Modes:
        `town`

    Rejections:
        - `session.command.wrong_mode` - the party is not in town.
        - `session.command.unknown_member` - `character_id` names no party member.
        - `items.purchase.insufficient_funds` - the whole gold pieces in the party's
          purses together fall short of the fee. Coin below a gold piece in a purse
          cannot go toward it, so a party worth the price in mixed coin can still be
          refused.

    Events:
        [`HealingPurchasedEvent`][osrlib.crawl.events.HealingPurchasedEvent], naming
        which purses paid and how much each one paid, then the service spell's own
        resolution events (healing, effect releases, a revival's outcome).
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.TOWN})

    command_type: Literal["purchase_healing"] = "purchase_healing"
    character_id: str
    """Who is treated, by [`MemberView.id`][osrlib.crawl.views.MemberView.id]. This member's
    purse is charged first and the rest of the party covers whatever is left, so there is no
    need to move coin around before buying. A dead member is a legal target, and a corpse with
    an empty purse is still treatable: `raise_dead` is what the temple is for."""
    service: HealingService
    """Which service to buy. The prices are fixed: `cure_light_wounds` 25 gp,
    `cure_serious_wounds` 100 gp, `cure_disease` 150 gp, `neutralize_poison` 150 gp,
    `remove_curse` 200 gp, and `raise_dead` 1,500 gp. Each resolves as the matching spell cast
    by a temple cleric at the lowest level able to cast it."""


class Parley(Command):
    """Speak with the monsters: a fresh reaction roll with the speaker's CHA modifier.

    An encounter must be open. Encounters begin from wandering checks, keyed
    areas, or the referee spawn commands. Any number of re-rolls is legal, and a
    hostile turn cuts the conversation short.

    Modes:
        `encounter`

    Rejections:
        - `session.command.wrong_mode` - no encounter is open.
        - `encounter.none_active` - a second check behind the mode gate, not
          reachable through normal play.
        - `encounter.parley.mid_pursuit` - no talking while being chased.
        - `session.command.unknown_member` - `character_id` names no party member.
        - `session.command.member_incapacitated` - the speaker cannot act.

    Events:
        [`ReactionRolledEvent`][osrlib.core.events.ReactionRolledEvent], and a
        [`StanceChangedEvent`][osrlib.crawl.events.StanceChangedEvent] when the
        stance shifts. An attacks result opens battle at once
        ([`BattleStartedEvent`][osrlib.crawl.events.BattleStartedEvent] and what
        follows). Otherwise the encounter round closes with the monsters' beat.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.ENCOUNTER})

    command_type: Literal["parley"] = "parley"
    character_id: str
    """The member who does the talking, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. Their CHA modifier applies to the
    fresh reaction roll, so the party's face is worth sending. Any number of attempts is legal,
    but a roll that comes up attacks opens battle at once and ends the conversation."""


class Evade(Command):
    """Flee the encounter (legal only before battle begins, RAW).

    An encounter must be open. `drop` scatters distraction bait as the party runs:
    treasure tempts intelligent monsters, food unintelligent ones. Only attacking
    or hostile monsters pursue, and outrunning them ends the encounter cleanly.

    Modes:
        `encounter`

    Rejections:
        - `session.command.wrong_mode` - no encounter is open.
        - `encounter.none_active` - a second check behind the mode gate, not
          reachable through normal play.
        - `encounter.evade.already_evading` - the pursuit is already running.
        - `encounter.evade.nothing_to_drop` - no coins (for `treasure`) or rations
          (for `food`) to scatter.

    Events:
        [`ItemsDroppedEvent`][osrlib.crawl.events.ItemsDroppedEvent]s for scattered
        bait, then [`EvasionEvent`][osrlib.crawl.events.EvasionEvent] with code
        `encounter.evasion.succeeded`, which ends the encounter, or
        `encounter.evasion.pursuit`, after which
        [`PursuitEvent`][osrlib.crawl.events.PursuitEvent] rounds follow. A pursuit
        ends in escape, in exhaustion at the round cap
        ([`ExhaustionEvent`][osrlib.crawl.events.ExhaustionEvent] with the
        [`EffectAttachedEvent`][osrlib.core.events.EffectAttachedEvent] and
        [`ConditionGainedEvent`][osrlib.core.events.ConditionGainedEvent] behind
        it), or in battle at the party's heels
        ([`BattleStartedEvent`][osrlib.crawl.events.BattleStartedEvent]).

        A concluded encounter posts
        [`EncounterEndedEvent`][osrlib.crawl.events.EncounterEndedEvent], a
        [`MonsterDefeatedEvent`][osrlib.crawl.events.MonsterDefeatedEvent] per
        monster slain, routed, or surrendered,
        [`EffectReleasedEvent`][osrlib.core.events.EffectReleasedEvent]s for the
        effects it releases, and, under the immediate XP timing,
        [`XpAwardedEvent`][osrlib.crawl.events.XpAwardedEvent] and
        [`CharacterLeveledUpEvent`][osrlib.crawl.events.CharacterLeveledUpEvent].
        The clock it owes runs with the usual
        [`LightEvent`][osrlib.crawl.events.LightEvent] and
        [`ProvisionsEvent`][osrlib.crawl.events.ProvisionsEvent] bookkeeping.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.ENCOUNTER})

    command_type: Literal["evade"] = "evade"
    drop: Literal["none", "treasure", "food"] = "none"
    """What to scatter behind the party as it runs. `treasure` empties every living member's purse
    onto the trail, where it stays, and tempts intelligent monsters into stopping for it.
    `food` costs each member one ration and tempts unintelligent ones. `none`, the default,
    throws nothing. Bait matters once a pursuit is actually running, and with nothing to
    scatter the command is refused with `encounter.evade.nothing_to_drop` rather than running
    the escape."""


class EngageBattle(Command):
    """Open battle: every offensive action goes through here (except turn undead).

    An encounter must be open. Monsters surprised at the encounter's start grant
    the party a free opening round, and engaging mid-pursuit turns the party to
    fight at the current gap.

    Modes:
        `encounter`

    Rejections:
        - `session.command.wrong_mode` - no encounter is open.
        - `encounter.none_active` - a second check behind the mode gate, not
          reachable through normal play.

    Events:
        [`BattleStartedEvent`][osrlib.crawl.events.BattleStartedEvent]. Groups at
        morale 2 rout at once
        ([`MonsterFledEvent`][osrlib.crawl.events.MonsterFledEvent]), and a battle
        whose every group routs ends immediately
        ([`BattleEndedEvent`][osrlib.crawl.events.BattleEndedEvent] and the
        encounter's conclusion).
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.ENCOUNTER})

    command_type: Literal["engage_battle"] = "engage_battle"


class Wait(Command):
    """Hold for one encounter round, and the monsters act per their stance.

    An encounter must be open. Waiting burns a round to see what the monsters do:
    an uncertain stance re-rolls its reaction, and a hostile one runs out its
    patience.

    Modes:
        `encounter`

    Rejections:
        - `session.command.wrong_mode` - no encounter is open.
        - `encounter.none_active` - a second check behind the mode gate, not
          reachable through normal play.

    Events:
        The round beat's events: an uncertain stance re-rolls
        ([`ReactionRolledEvent`][osrlib.core.events.ReactionRolledEvent], possibly
        a [`StanceChangedEvent`][osrlib.crawl.events.StanceChangedEvent]), and an
        attacking or expired-patience hostile stance opens battle
        ([`BattleStartedEvent`][osrlib.crawl.events.BattleStartedEvent]). During a
        pursuit a [`PursuitEvent`][osrlib.crawl.events.PursuitEvent] round resolves
        instead, which can end in
        [`ExhaustionEvent`][osrlib.crawl.events.ExhaustionEvent] at the round cap
        (with the [`EffectAttachedEvent`][osrlib.core.events.EffectAttachedEvent]
        and [`ConditionGainedEvent`][osrlib.core.events.ConditionGainedEvent] behind
        it) or in battle.

        An encounter that concludes posts
        [`EncounterEndedEvent`][osrlib.crawl.events.EncounterEndedEvent], a
        [`MonsterDefeatedEvent`][osrlib.crawl.events.MonsterDefeatedEvent] per
        monster slain, routed, or surrendered,
        [`EffectReleasedEvent`][osrlib.core.events.EffectReleasedEvent]s, and, under
        the immediate XP timing,
        [`XpAwardedEvent`][osrlib.crawl.events.XpAwardedEvent] and
        [`CharacterLeveledUpEvent`][osrlib.crawl.events.CharacterLeveledUpEvent].
        The round itself runs the usual
        [`EffectExpiredEvent`][osrlib.core.events.EffectExpiredEvent],
        [`LightEvent`][osrlib.crawl.events.LightEvent], and
        [`ProvisionsEvent`][osrlib.crawl.events.ProvisionsEvent] bookkeeping.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.ENCOUNTER})

    command_type: Literal["wait"] = "wait"


class TurnUndead(Command):
    """Present the holy symbol, the one aggressive act with a pre-battle procedure.

    An encounter must be open: exploration offers no candidates by definition, and
    in battle turning is a declaration kind. If any monster stands unturned, the
    survivors attack at once.

    Modes:
        `encounter`

    Rejections:
        - `session.command.wrong_mode` - no encounter is open.
        - `encounter.none_active` - a second check behind the mode gate, not
          reachable through normal play.
        - `encounter.turning.mid_pursuit` - no turning while being chased.
        - `session.command.unknown_member` - `character_id` names no party member.
        - `session.command.member_incapacitated` - the cleric cannot act.
        - `magic.turning.not_a_turner` - the class has no turn-undead ability.
        - `magic.turning.caster_incapacitated` - a condition prevents the attempt.

    Events:
        [`UndeadTurnedEvent`][osrlib.core.events.UndeadTurnedEvent] with the roll
        and the affected monsters (their conditions each their own event). When
        every monster is turned or destroyed the encounter ends
        ([`EncounterEndedEvent`][osrlib.crawl.events.EncounterEndedEvent]).
        Otherwise a [`StanceChangedEvent`][osrlib.crawl.events.StanceChangedEvent]
        to attacks and battle opens
        ([`BattleStartedEvent`][osrlib.crawl.events.BattleStartedEvent]).
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.ENCOUNTER})

    command_type: Literal["turn_undead"] = "turn_undead"
    character_id: str
    """The cleric who presents the holy symbol, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. A class with no turning ability comes
    back as `magic.turning.not_a_turner`."""


class BattleDeclaration(BaseModel):
    """One party member's declared action for a battle round.

    You build these yourself, one per member the round expects, and hand the whole
    set to [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound]. Which
    members a round expects is
    [`EncounterView.declarers`][osrlib.crawl.views.EncounterView.declarers]. The
    three id tuples beside it (`front_rank`, `immobile`, and `reloading`) say which
    declarations the engine will take, so a front end that reads all four offers only
    legal choices.

    `action` decides which other fields matter, and the rest stay `None`. `attack`
    names a target group and, optionally, a wielded weapon, with `None` meaning bare
    hands. `cast` names the spell, its mode, its form, and its targets. `move` is a
    range-track intent for the whole formation. `use_item` covers a wand, staff, or
    rod, and a flask thrown at a group. `turn_undead` needs nothing else: turning
    resolves in the magic phase but is never disrupted, because it's a class ability
    rather than a spell. `hold` does nothing, which is how a member with no legal
    action still fills their slot on the roster.
    """

    model_config = ConfigDict(frozen=True)

    character_id: str
    """Whose declaration this is, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. It has to be one of the ids in
    [`EncounterView.declarers`][osrlib.crawl.views.EncounterView.declarers], and every one of
    those needs a declaration of its own in the same round."""
    action: Literal["attack", "cast", "turn_undead", "move", "use_item", "hold"]
    """What this member does. `attack` swings or shoots at a group, `cast` casts a spell,
    `turn_undead` presents a holy symbol, `move` changes the distance to the monsters,
    `use_item` throws or triggers something, and `hold` does nothing. Which other fields matter
    follows from this one, and `hold` needs none of them."""
    target_group_id: str | None = None
    """Which monster group the action is aimed at, by
    [`EncounterGroupView.id`][osrlib.crawl.views.EncounterGroupView.id] off
    [`EncounterView.groups`][osrlib.crawl.views.EncounterView.groups]. An `attack` needs it, so
    does a `use_item` with a thrown item, and so does a `close` move. A group that has fled or
    surrendered is refused with `battle.declaration.unknown_group`."""
    weapon_id: str | None = None
    """Which wielded weapon to attack with: a mundane weapon's catalog id, a magic weapon's
    per-instance id, or `None` to strike unarmed. The weapon has to be wielded already, so
    equip it with [`EquipItem`][osrlib.crawl.commands.EquipItem] before the fight. A weapon
    merely carried is refused with `battle.declaration.weapon_not_wielded`. Whether the
    declaration counts as melee or missile follows from the weapon's own qualities and the
    group's distance."""
    spell_id: str | None = None
    """Which spell a `cast` declaration casts, from
    [`load_spells`][osrlib.data.load_spells] (see [the spell id index][spells-index]), or,
    for a `use_item` scroll read, which spell to read off the scroll. A `cast` with no spell
    named is refused with `battle.declaration.missing_spell`."""
    spell_mode: str | None = None
    """Which of the spell's usages to cast, by that usage's
    [`SpellMode.key`][osrlib.core.spells.SpellMode]. A `cast` declaration has to name one. A
    scroll read may leave it `None` and take the spell's first usage."""
    reversed: bool = False
    """Cast the reversed form of the spell, on the same terms as
    [`CastSpell.reversed`][osrlib.crawl.commands.CastSpell.reversed]: an arcane caster needs a
    reversed copy memorized, and a divine caster chooses here."""
    targets: tuple[str, ...] = ()
    """The spell's targets, named as in
    [`CastSpell.targets`][osrlib.crawl.commands.CastSpell.targets]: member ids, monster ids, or
    a `cell:` reference. A target the party can't see is refused with
    `battle.declaration.invisible_target`."""
    move: Literal["close", "fighting_withdrawal", "retreat"] | None = None
    """Which movement a `move` declaration makes. `close` advances the whole formation on
    `target_group_id` at the party's slowest encounter rate, stopping at melee range.
    `fighting_withdrawal` backs the formation off at half that rate. `retreat` breaks off at
    full rate, and a round in which every member retreats ends the battle and turns it into a
    pursuit, or into a clean escape when nothing can chase. The party moves as one formation
    and a single member can't leave it, so a move resolves when everyone declares the same one,
    apart from `close`. A fighting withdrawal is a move and nothing else here: the member who
    declares it makes no attack that round, the adaptation the register records under the
    battle round."""
    item_id: str | None = None
    """Which item a `use_item` declaration uses: a magic item's per-instance id for a wand, staff,
    or rod, or a mundane item's catalog id for something thrown at a group, like a flask of
    oil. An item with no combat use of its own is refused with
    `battle.declaration.item_unusable`."""


class ResolveBattleRound(Command):
    """Resolve one battle round: one declaration per living, able party member.

    A battle must be underway (see
    [`EngageBattle`][osrlib.crawl.commands.EngageBattle]). Validation is the pure
    pre-phase: every declaration validates or the whole command rejects listing
    every rejection, because partial acceptance would tangle the replay contract.

    Modes:
        `battle`

    Rejections:
        - `session.command.wrong_mode` - no battle is underway.
        - `battle.none_active` - a second check behind the mode gate, not
          reachable through normal play.
        - `battle.declaration.roster_mismatch` - the declarations do not name
          exactly the living, able members.
        - `battle.declaration.unknown_action` - an unrecognized `action`.
        - Move declarations: `battle.declaration.missing_move`,
          `battle.declaration.unknown_group`, `battle.declaration.cannot_move`.
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
          `exploration.action.requires_light`, `combat.attack.out_of_reach`.

    Events:
        Opening the round:
        [`BattleRoundEvent`][osrlib.crawl.events.BattleRoundEvent], a
        [`SpellDeclaredEvent`][osrlib.crawl.events.SpellDeclaredEvent] per declared
        cast, and
        [`InitiativeRolledEvent`][osrlib.core.events.InitiativeRolledEvent] for the
        side order.

        Movement: [`GroupMovedEvent`][osrlib.crawl.events.GroupMovedEvent] as the
        gap changes.

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
        spell picks its own targets,
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
        [`CurseRevealedEvent`][osrlib.crawl.events.CurseRevealedEvent] at first
        meaningful use.

        Morale: [`MoraleCheckedEvent`][osrlib.core.events.MoraleCheckedEvent],
        [`MonsterFledEvent`][osrlib.crawl.events.MonsterFledEvent] for a group that
        breaks, and
        [`MonstersLeftBehindEvent`][osrlib.crawl.events.MonstersLeftBehindEvent]
        when the runners leave members who cannot move behind them.

        Closing the round, the clock's own bookkeeping:
        [`EffectExpiredEvent`][osrlib.core.events.EffectExpiredEvent],
        [`EffectTickedEvent`][osrlib.core.events.EffectTickedEvent],
        [`MonsterRevivedEvent`][osrlib.core.events.MonsterRevivedEvent],
        [`LightEvent`][osrlib.crawl.events.LightEvent], and
        [`ProvisionsEvent`][osrlib.crawl.events.ProvisionsEvent].

        A terminal round appends
        [`BattleEndedEvent`][osrlib.crawl.events.BattleEndedEvent] and the
        encounter's conclusion
        ([`EncounterEndedEvent`][osrlib.crawl.events.EncounterEndedEvent],
        [`MonsterDefeatedEvent`][osrlib.crawl.events.MonsterDefeatedEvent]s, and,
        under the immediate XP timing,
        [`XpAwardedEvent`][osrlib.crawl.events.XpAwardedEvent] and
        [`CharacterLeveledUpEvent`][osrlib.crawl.events.CharacterLeveledUpEvent]),
        or [`GameOverEvent`][osrlib.crawl.events.GameOverEvent] on a party wipe.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.BATTLE})

    command_type: Literal["resolve_battle_round"] = "resolve_battle_round"
    declarations: tuple[BattleDeclaration, ...] = ()
    """One [`BattleDeclaration`][osrlib.crawl.commands.BattleDeclaration] per member listed in
    [`EncounterView.declarers`][osrlib.crawl.views.EncounterView.declarers], and none for
    anyone else. Any other roster is refused with `battle.declaration.roster_mismatch`, and a
    single bad declaration rejects the whole command rather than half the round. Order doesn't
    decide who acts, since initiative and the phase order do that, but when more than one
    member declares `close` on different groups the first entry is the one the formation
    follows."""


class GrantItem(Command):
    """Referee: place an item directly into a member's inventory.

    Referee commands are legal in every mode, terminal modes included, and are
    logged and replayed like any other.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        - `session.command.unknown_member` - `character_id` names no party member.
        - `session.command.unknown_item` - `item_id` names no item in the session's
          [`effective_equipment`][osrlib.crawl.session.GameSession.effective_equipment]
          catalog: neither a shipped id nor one the adventure bundles.

    Events:
        [`ItemAcquiredEvent`][osrlib.crawl.events.ItemAcquiredEvent] with the
        granted items, `origin` `"grant"`.
    """

    command_type: Literal["grant_item"] = "grant_item"
    character_id: str
    """In an authored consequence or reward, this field takes the party selectors
    (`"@party"`, `"@first"`), expanded to literal member ids by the interpreter
    before issue. Issued directly, it must be a literal member id or the command
    rejects."""
    item_id: str
    """Which item to create, from the session's
    [`effective_equipment`][osrlib.crawl.session.GameSession.effective_equipment] catalog:
    either a shipped id from [`load_equipment`][osrlib.data.load_equipment] (see [the
    equipment id index][equipment-index]) or one the adventure bundles. There's no purse check
    and no shop, so this is how a game hands out a reward, a found item, or a starting kit."""
    quantity: int = Field(default=1, ge=1)
    """How many units to place, counted as individual items rather than as purchase lots:
    `quantity=6` grants six torches, which is the same count one purchase lot of torches
    delivers through [`PurchaseEquipment`][osrlib.crawl.commands.PurchaseEquipment]."""


class GrantCoins(Command):
    """Referee: place coins directly into a member's purse.

    Referee commands are legal in every mode, terminal modes included.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        - `session.command.unknown_member` - `character_id` names no party member.

    Events:
        [`ItemAcquiredEvent`][osrlib.crawl.events.ItemAcquiredEvent] with the coin
        value, `origin` `"grant"`, so a reward counted out per member never reads as
        a haul split across the party.
    """

    command_type: Literal["grant_coins"] = "grant_coins"
    character_id: str
    """In an authored consequence or reward, this field takes the party selectors
    (`"@party"`, `"@first"`), expanded to literal member ids by the interpreter
    before issue. Issued directly, it must be a literal member id or the command
    rejects."""
    coins: Coins
    """The coins to add to the member's purse, by denomination. Nothing is charged and no weight
    check refuses the grant, so a large award can leave the member overloaded. Check the
    resulting load before you send the party on."""


class AwardXP(Command):
    """Referee: apply an XP award to one character, outside the adventure award.

    Referee commands are legal in every mode, terminal modes included, so an
    adventure's rewards land after the session has concluded. The award applies
    the prime-requisite modifier and can trigger level gains.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        - `session.command.unknown_member` - `character_id` names no party member.

    Events:
        [`XpAwardedEvent`][osrlib.crawl.events.XpAwardedEvent] with the award, the
        modified award, and the level after. When the award crosses a level
        threshold, a
        [`CharacterLeveledUpEvent`][osrlib.crawl.events.CharacterLeveledUpEvent]
        follows with the levels, the hit points gained, and the new title.
    """

    command_type: Literal["award_xp"] = "award_xp"
    character_id: str
    """In an authored consequence or reward, this field takes the party selectors
    (`"@party"`, `"@first"`), expanded to literal member ids by the interpreter
    before issue. Issued directly, it must be a literal member id or the command
    rejects."""
    amount: int = Field(ge=0)
    """The raw award, before the class's prime-requisite percentage applies to it. B/X grants at
    most one level per award, so XP that would carry the character two levels up is held one
    point below the second threshold and the character gains a single level. Award again to
    carry them further."""


class SetFlag(Command):
    """Referee: set a session flag (content wiring: the lever opens the portcullis).

    Referee commands are legal in every mode, terminal modes included. Flags
    serialize into saves. Game code and listeners read them back, and authored
    content reads them through
    [`FlagEqualsCondition`][osrlib.crawl.gates.FlagEqualsCondition], the gate on a
    door or stair that opens when the lever has been pulled.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        None.

    Events:
        [`FlagSetEvent`][osrlib.crawl.events.FlagSetEvent] with the key and value.
    """

    command_type: Literal["set_flag"] = "set_flag"
    key: str = Field(min_length=1)
    """The flag's name, which is an open domain: any non-empty string a game wants, so a game's
    own systems set flags with their own names. Keep them stable, because the flag store
    serializes into saves and authored content reads these names back."""
    value: str | int | bool
    """What the flag is set to: a string, an integer, or a boolean. Setting a key that is already
    set replaces the old value. Authored content compares against this value through
    [`FlagEqualsCondition`][osrlib.crawl.gates.FlagEqualsCondition], so the door that opens
    when the lever has been pulled is a flag set here and a gate reading it there."""


class SpawnMonsters(Command):
    """Referee: spawn monsters and open an encounter at a distance.

    The party must be standing in a dungeon with no encounter already open, because
    encounters live on the dungeon grid. Spawning is the one referee power a
    terminal session withholds: an encounter is play, and a session that has
    ended opens no new play state, so this is illegal in `game_over` and
    `victory` alike. Exactly one of `count_dice` or `count_fixed` is required.

    Modes:
        `town`, `exploring`, `encounter`, `battle`

    Rejections:
        - `session.command.unknown_monster` - `template_id` names no monster.
        - `session.command.encounter_in_progress` - an encounter or battle is
          already open.
        - `session.command.not_in_dungeon` - the party is not on a dungeon cell.

    Events:
        [`MonstersSpawnedEvent`][osrlib.crawl.events.MonstersSpawnedEvent], then
        the encounter opening:
        [`SurpriseRolledEvent`][osrlib.crawl.events.SurpriseRolledEvent]s,
        [`EncounterStartedEvent`][osrlib.crawl.events.EncounterStartedEvent], the
        reaction roll, and
        [`StanceChangedEvent`][osrlib.crawl.events.StanceChangedEvent]. An attacks
        stance opens battle at once.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _ALL_MODES - frozenset(
        {SessionMode.GAME_OVER, SessionMode.VICTORY}
    )

    command_type: Literal["spawn_monsters"] = "spawn_monsters"
    template_id: str
    """Which monster to spawn, from the session's
    [`effective_monsters`][osrlib.crawl.session.GameSession.effective_monsters] catalog: either
    a shipped id from [`load_monsters`][osrlib.data.load_monsters] (see [the monster id
    index][monsters-index]) or one the adventure bundles."""
    count_dice: str | None = None
    """How many to spawn, as a dice expression like `"2d6"` rolled on the encounter stream. Use
    this when the count should vary with the seed, and use `count_fixed` when it shouldn't.
    Exactly one of the two is required, and a malformed expression is refused when the command
    is constructed, before the session ever sees it. A roll below 1 is treated as 1."""
    count_fixed: int | None = Field(default=None, ge=1)
    """How many to spawn, as an exact number. Use this for keyed content whose count the author
    chose. Exactly one of this and `count_dice` is required."""
    distance_feet: int = Field(ge=0)
    """How far away the monsters appear, in feet. This is the distance the encounter opens at,
    which decides whether missiles reach, how long closing takes, and whether the party can
    outrun a pursuit. B/X rolls 2d6 x 10 feet for a wandering encounter, so a number in that
    range reads as ordinary. `0` puts them in the party's face."""

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
    """Referee: generate an NPC adventuring party and open an encounter.

    `count_dice=None` rolls the compiled composition dice (Basic 1d4+4, Expert
    1d6+3), which is what keyed content, quest listeners, and tests want. The party
    must be standing in a dungeon with no encounter already open, and, like
    [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters], the command is illegal
    in a terminal mode: a concluded session opens no new encounter.

    Modes:
        `town`, `exploring`, `encounter`, `battle`

    Rejections:
        - `session.command.encounter_in_progress` - an encounter or battle is
          already open.
        - `session.command.not_in_dungeon` - the party is not on a dungeon cell.

    Events:
        [`NpcPartySpawnedEvent`][osrlib.crawl.events.NpcPartySpawnedEvent] (the
        referee-visibility roster), then the encounter opening as with
        [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters].
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _ALL_MODES - frozenset(
        {SessionMode.GAME_OVER, SessionMode.VICTORY}
    )

    command_type: Literal["spawn_npc_party"] = "spawn_npc_party"
    party_kind: Literal["basic", "expert"]
    """Which composition table to build from: `basic` for a low-level band, `expert` for a
    higher-level one. It also decides the group's label in the encounter."""
    count_dice: str | None = None
    """How many adventurers, as a dice expression rolled on the encounter stream. `None`, the
    default, rolls the compiled composition dice for `party_kind`, which is what keyed content
    and quest listeners usually want. A malformed expression is refused when the command is
    constructed. A roll below 1 is treated as 1."""
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
    """Referee: rewrite a door's overlay anywhere (`None` fields stay unchanged).

    Referee commands are legal in every mode, terminal modes included. The door
    may be on any level of any dungeon, not just under the party.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        - `session.command.unknown_location` - `dungeon_id` or `level_number`
          resolves to nothing.
        - `session.command.no_door` - no door edge at that cell and direction.

    Events:
        A referee-visibility [`DoorEvent`][osrlib.crawl.events.DoorEvent] when the
        open state actually changes, and otherwise none.
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
    """Which of the cell's four sides the door is on. Two cells share each physical edge, so
    naming a cell's north side and naming its northern neighbour's south side write the same
    door. An edge with no door is refused with `session.command.no_door`."""
    open: bool | None = None
    """Whether the door stands open. `None`, the default, leaves it as it is. Writing a change
    here emits a referee-visibility [`DoorEvent`][osrlib.crawl.events.DoorEvent], and writing
    the value it already has emits nothing."""
    wedged: bool | None = None
    """Whether a spike holds the door. `None` leaves it as it is. A wedged door can't swing shut
    behind the party."""
    discovered: bool | None = None
    """Whether the party has found the door. `None` leaves it as it is. This is what makes a
    secret door visible without a search, and clearing it hides one again."""
    unlocked: bool | None = None
    """Whether the lock has been undone. `None` leaves it as it is. Setting it opens a locked door
    to [`OpenDoor`][osrlib.crawl.commands.OpenDoor] without a thief."""


class PlaceParty(Command):
    """Referee: teleport the party to a location.

    The party cannot be teleported out of an open encounter or battle. Placing
    into a dungeon marks the cell explored and switches the session to
    `exploring`, and placing in town switches it to `town`. That switch is play
    resuming, which is why this is the one referee command a concluded adventure
    withholds: it is illegal in `victory`. It stays legal in `game_over`, where it
    is the way out, because carrying the fallen party to town is the first
    step of the revival flow that ends at
    [`PurchaseHealing`][osrlib.crawl.commands.PurchaseHealing]'s `raise_dead`.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`

    Rejections:
        - `session.command.encounter_in_progress` - an encounter or battle is
          open.
        - `session.command.unknown_location` - the location names no dungeon
          level.
        - `session.command.out_of_bounds` - the position is off the level's grid.

    Events:
        [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent] for the
        destination, a dungeon one carrying `via` `"placed"`, so a log never reads
        a referee's teleport as a walk down the stairs.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _ALL_MODES - frozenset({SessionMode.VICTORY})

    command_type: Literal["place_party"] = "place_party"
    location: PartyLocation
    """Where the party ends up. A [`PartyLocation`][osrlib.crawl.dungeon.PartyLocation] with
    `kind="town"` has no other fields. One with `kind="dungeon"` needs the dungeon id, the
    level number, the cell, and the facing, all four together. A dungeon placement marks the
    cell explored and switches the session to `exploring`, and a town placement switches it to
    `town`. A position off the level's grid is refused with
    `session.command.out_of_bounds`."""


class AdvanceTime(Command):
    """Referee: advance the clock directly.

    Referee commands are legal in every mode, terminal modes included, so the clock
    a revival window is measured in keeps running after the party falls. Time
    passes with full bookkeeping (effect expiries, provisions on day boundaries)
    but no wandering cadence, because the referee controls encounters.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        None.

    Events:
        The span's bookkeeping events (effect expiries and their player-facing
        light translations, provisions), then
        [`TimeAdvancedEvent`][osrlib.crawl.events.TimeAdvancedEvent] with the
        total.
    """

    command_type: Literal["advance_time"] = "advance_time"
    n: int = Field(ge=0)
    """How many units to advance. `0` is legal and advances nothing."""
    unit: TimeUnit
    """Which unit `n` counts: rounds, turns, or days. A B/X turn is 10 minutes and a day is 144
    turns, so the three differ by orders of magnitude. Effect expiries run across the whole
    span whichever unit you pick, and provisions are charged at each day boundary it
    crosses."""


class RollDice(Command):
    """Referee: roll a dice expression through the seeded session.

    An authorial roll for freeform adjudication. The referee resolves a *chance*
    outcome the content model can't express (a puzzle, a bluff, "does the frayed
    rope hold?") by rolling through the engine rather than inventing a number, so
    the result is logged, replayable, and grounded in a typed event. Referee
    commands are legal in every mode, terminal modes included. The roll draws from
    the dedicated
    [`ADJUDICATION_STREAM`][osrlib.crawl.session.ADJUDICATION_STREAM], so an ad-hoc
    referee roll never perturbs the draw sequence of keyed mechanics. A malformed
    `expression` is rejected at construction, exactly as
    [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters]'s `count_dice` is, so it
    never reaches the session and consumes no draw.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        None.

    Events:
        [`DiceRolledEvent`][osrlib.crawl.events.DiceRolledEvent] with the
        expression, the total, and the individual die results.
    """

    command_type: Literal["roll_dice"] = "roll_dice"
    expression: str
    """The roll, in the notation [`parse`][osrlib.core.dice.parse] accepts, like `"1d20"`,
    `"3d6+2"`, or `"2d6+1x10"`. A malformed expression raises when the command is constructed
    rather than coming back as a rejection, so a bad expression never reaches the session and
    never moves the stream."""

    @field_validator("expression")
    @classmethod
    def _expression_must_parse(cls, value: str) -> str:
        parse(value)
        return value


class MarkTriggerFired(Command):
    """Referee: record that an authored trigger has fired.

    One of the lifecycle commands a trigger-and-quest interpreter, a game's own
    listener, or an LLM referee drives: the mark goes in before the trigger's
    consequences issue, so fired-state is what answers once-only semantics, and it
    survives save, load, and replay like any other session state. Referee commands
    are legal in every mode, terminal modes included. Marking an already-marked
    trigger is accepted and changes nothing: session state records that a trigger
    *has* fired, while each mark in the command log records *one* firing.

    `trigger_id` is an **open domain**: a mark records that something fired, needs no
    authored trigger behind it, and a game drives it with ids from its own systems.
    The quest lifecycle commands invert that deliberately:
    [`ActivateQuest`][osrlib.crawl.commands.ActivateQuest] and its three siblings
    resolve their ids against the adventure's quest specs, because the state they
    advance is projected into the player view and an id with no spec behind it has
    nothing to show.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        None.

    Events:
        [`TriggerFiredEvent`][osrlib.crawl.events.TriggerFiredEvent] with the
        trigger id and the beat, for every mark.
    """

    command_type: Literal["mark_trigger_fired"] = "mark_trigger_fired"
    trigger_id: str = Field(min_length=1)
    """Which trigger fired. The domain is open: the id needs no authored trigger behind it, so a
    game marks its own systems' events with its own ids. Session state records a mark once,
    which is what answers once-only questions, and the command log records every mark as its
    own line."""
    narrative: str | None = Field(default=None, min_length=1)
    """The authored beat for the firing, included on the event at referee
    visibility. Trigger internals are the game's secret, so this is the referee's
    line about the wiring. The players' line is a journal entry
    ([`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry]). Authored text on a
    command is content data in a structured field, and the command still has
    its type and its facts."""


class AddJournalEntry(Command):
    """Referee: append an authored beat to the session journal.

    The journal is the party's record of the adventure in order of discovery:
    entries append, are never rewritten, and are never derived from other state, so
    a beat outlives whatever produced it. Each entry is stamped with the clock
    position it landed at, and
    [`PlayerView.journal`][osrlib.crawl.views.PlayerView.journal] ships the entries
    as they were written. Referee commands are legal in every mode, terminal modes included,
    so a closing beat lands after the adventure has concluded.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        None.

    Events:
        [`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent] with
        the text and the clock position.
    """

    command_type: Literal["add_journal_entry"] = "add_journal_entry"
    text: str = Field(min_length=1)
    """The beat to write, as the players will read it. It's stamped with the clock position it
    lands at, appended to the journal, and shipped verbatim on
    [`PlayerView.journal`][osrlib.crawl.views.PlayerView.journal]. Entries are never rewritten
    or derived from other state, so write the line you want kept."""


class RecordNote(Command):
    """Referee: record an annotation in the logs, with no effect on game state.

    The note lands as a referee-visibility event and touches nothing. It is the
    mechanism for machine-issued records, like a consequence that was dropped or a
    cascade that was cut short, and for a referee's own margin notes alike.
    Referee commands are legal in every mode, terminal modes included.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        None.

    Events:
        [`NoteRecordedEvent`][osrlib.crawl.events.NoteRecordedEvent] with the text.
    """

    command_type: Literal["record_note"] = "record_note"
    text: str = Field(min_length=1)
    """The annotation to record. It reaches the log as a referee-visibility event and changes no
    state, which makes it the place for machine-issued records, like a consequence that was
    dropped, and for a referee's own margin notes. A line the players should read is an
    [`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry] instead."""


class ActivateQuest(Command):
    """Referee: put an authored quest into play.

    The first of the four commands that drive an adventure's quest state: a
    per-quest status that runs `inactive`, then `active`, then `completed`, and,
    under it, a revealed/complete pair per objective. That state is engine-owned
    session state beside the flag store, and these four are its only writers, so a
    replay rebuilds it by re-executing the log.

    Quest and objective ids are a **closed domain**: they resolve against the
    adventure's [`QuestSpec`][osrlib.crawl.quests.QuestSpec]s, and an id no quest
    spec defines is rejected. That is the deliberate opposite of
    [`MarkTriggerFired`][osrlib.crawl.commands.MarkTriggerFired]'s open trigger id:
    a mark is bookkeeping a game may drive with ids from its own systems, while an
    activated quest is projected into the player view with a name, an offer, and an
    objective list, and an id with no quest behind it has none of them.

    An accepted activation appends the quest's `offer` beat to the journal when its
    author wrote one, and the event includes the same line. The append emits no
    [`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent], because
    the lifecycle event *is* that beat's event. Quest state is monotonic, so only an
    `inactive` quest activates. Referee commands are legal in every mode, terminal
    modes included.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        - `session.command.unknown_quest` - `quest_id` names no quest of the
          adventure.
        - `session.command.quest_state` - the quest is already active or already
          completed. The rejection names the quest and the state that refused it.

    Events:
        [`QuestActivatedEvent`][osrlib.crawl.events.QuestActivatedEvent] with the
        quest id, the quest's name, and the offer beat.
    """

    command_type: Literal["activate_quest"] = "activate_quest"
    quest_id: str = Field(min_length=1)
    """Which quest to put into play, by
    [`QuestSpec.id`][osrlib.crawl.quests.QuestSpec] from the adventure's own quests. An id no
    quest spec has is refused with `session.command.unknown_quest`."""


class RevealObjective(Command):
    """Referee: show the players a hidden objective of an active quest.

    A hidden objective is absent from the player view until it is revealed or until
    it completes. Completing an objective reveals it, so a quest whose hidden
    objective is finished before anyone announces it needs no reveal. Ids are the closed domain
    [`ActivateQuest`][osrlib.crawl.commands.ActivateQuest] documents.

    An accepted reveal appends the objective's `offer` beat to the journal when its
    author wrote one, and the event includes the same line. No
    [`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent] follows,
    because the lifecycle event is that beat's event. Referee commands are legal in
    every mode, terminal modes included.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        - `session.command.unknown_quest` - `quest_id` names no quest of the
          adventure.
        - `session.command.unknown_objective` - `objective_id` names no objective of
          that quest.
        - `session.command.quest_state` - the quest is not active, or the objective
          is already visible or already complete. The rejection names the quest and
          the state that refused it.

    Events:
        [`ObjectiveRevealedEvent`][osrlib.crawl.events.ObjectiveRevealedEvent] with
        the quest id, the objective id, and the objective's offer beat.
    """

    command_type: Literal["reveal_objective"] = "reveal_objective"
    quest_id: str = Field(min_length=1)
    """Which quest the objective belongs to, by
    [`QuestSpec.id`][osrlib.crawl.quests.QuestSpec]. The quest has to be active already."""
    objective_id: str = Field(min_length=1)
    """Which objective to show the players, by
    [`ObjectiveSpec.id`][osrlib.crawl.quests.ObjectiveSpec] within that quest. An objective
    already visible, or already complete, is refused with `session.command.quest_state`."""


class CompleteObjective(Command):
    """Referee: mark one objective of an active quest done.

    Completing reveals a hidden objective on the way: an objective the party
    finished before it was ever announced is revealed and complete in one step, with
    no separate [`RevealObjective`][osrlib.crawl.commands.RevealObjective]. Ids are
    the closed domain [`ActivateQuest`][osrlib.crawl.commands.ActivateQuest]
    documents.

    Completing the last objective a quest's completion rule needs does *not*
    complete the quest: [`CompleteQuest`][osrlib.crawl.commands.CompleteQuest] is its
    own command, so whoever drives the quest layer decides when the rule is
    satisfied. An accepted completion appends the objective's `progress` beat to the
    journal when its author wrote one, and the event includes the same line. No
    [`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent] follows.
    Referee commands are legal in every mode, terminal modes included.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        - `session.command.unknown_quest` - `quest_id` names no quest of the
          adventure.
        - `session.command.unknown_objective` - `objective_id` names no objective of
          that quest.
        - `session.command.quest_state` - the quest is not active, or the objective
          is already complete. The rejection names the quest and the state that
          refused it.

    Events:
        [`ObjectiveCompletedEvent`][osrlib.crawl.events.ObjectiveCompletedEvent] with
        the quest id, the objective id, and the objective's progress beat.
    """

    command_type: Literal["complete_objective"] = "complete_objective"
    quest_id: str = Field(min_length=1)
    """Which quest the objective belongs to, by
    [`QuestSpec.id`][osrlib.crawl.quests.QuestSpec]. The quest has to be active."""
    objective_id: str = Field(min_length=1)
    """Which objective is done, by
    [`ObjectiveSpec.id`][osrlib.crawl.quests.ObjectiveSpec] within that quest. A hidden
    objective is revealed on the way, so a party that finished it before hearing of it needs no
    separate [`RevealObjective`][osrlib.crawl.commands.RevealObjective]."""


class CompleteQuest(Command):
    """Referee: finish an active quest, and on the concluding quest, the adventure.

    The quest must be active, and that is the only test: the completion rule is
    *not* checked here. Ruling a quest done is the referee's call, and an authored
    quest layer is a disciplined issuer that checks the rule before issuing.
    Ids are the closed domain
    [`ActivateQuest`][osrlib.crawl.commands.ActivateQuest] documents.

    Rewards are not this command's business: whoever completes the quest issues the
    authored rewards afterwards as ordinary commands of their own, so a completion
    driven by hand grants nothing and every reward that does land is a line in the
    log. The completion appends the quest's `completion` beat to the journal when
    its author wrote one, and the events include the same line. No
    [`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent] follows.

    **The victory transition.** Completing a quest whose spec sets
    `concludes_adventure` from a non-terminal mode clears any open encounter and
    battle, because a concluded session has no live play state, and switches the
    session to `victory`. This is the one entrance to that mode. From a terminal mode
    (`game_over` or `victory`) the quest still completes and still journals, but
    nothing transitions and no adventure-completed event lands: an ended session
    never ends again, so the record shows a fallen party finishing the job without
    resurrecting the adventure around it. Referee commands are legal in every mode,
    terminal modes included.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        - `session.command.unknown_quest` - `quest_id` names no quest of the
          adventure.
        - `session.command.quest_state` - the quest is inactive or already
          completed. The rejection names the quest and the state that refused it.

    Events:
        [`QuestCompletedEvent`][osrlib.crawl.events.QuestCompletedEvent] with the
        quest id, the quest's name, and the completion beat, followed by
        [`AdventureCompletedEvent`][osrlib.crawl.events.AdventureCompletedEvent]
        with the same beat when the quest concludes the adventure and the
        session had not already ended.
    """

    command_type: Literal["complete_quest"] = "complete_quest"
    quest_id: str = Field(min_length=1)
    """Which quest to finish, by
    [`QuestSpec.id`][osrlib.crawl.quests.QuestSpec]. It has to be active, and that's the only
    test: whether its objectives are done is the issuer's judgement rather than the engine's."""


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
"""Every command class: the discriminated union's members, in a stable wire order.

Iterate it to build something that covers the whole command surface without naming
each class: a tool definition for an agent framework, a request router, a table of
what the current mode accepts (read each class's `allowed_modes`), or a test that
walks every command. The order stays the same across releases and new commands are
appended, so an index into this tuple keeps its meaning.

To go the other way, from a serialized mapping to a command, use
[`parse_command`][osrlib.crawl.commands.parse_command] rather than searching this
tuple by hand."""

AnyCommand = Annotated[
    Union[*ALL_COMMAND_CLASSES],
    Field(discriminator="command_type"),
]
"""Any command, discriminated by `command_type`.

Type a field, a parameter, or a request body with this when the value arrives as
data and could be any command: pydantic reads `command_type` and validates against
that one class, so a web handler annotated `command: AnyCommand` gets the right
model and the right error message without a dispatch table. For a mapping you
already have in Python, call
[`parse_command`][osrlib.crawl.commands.parse_command] instead, which skips an
unknown `command_type` rather than raising on it.

The union's members and their order are
[`ALL_COMMAND_CLASSES`][osrlib.crawl.commands.ALL_COMMAND_CLASSES]. The generated
command reference publishes this union's JSON Schema as a single downloadable file,
which is the form an agent framework loads."""

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
stable wire order.

Referee commands sit outside the surface for one of three reasons:

- [`MarkTriggerFired`][osrlib.crawl.commands.MarkTriggerFired],
  [`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry],
  [`RecordNote`][osrlib.crawl.commands.RecordNote],
  [`ActivateQuest`][osrlib.crawl.commands.ActivateQuest],
  [`RevealObjective`][osrlib.crawl.commands.RevealObjective],
  [`CompleteObjective`][osrlib.crawl.commands.CompleteObjective], and
  [`CompleteQuest`][osrlib.crawl.commands.CompleteQuest] are the vocabulary the
  trigger and quest interpreter writes its own bookkeeping in. It marks, journals,
  annotates, and advances quest state on the author's behalf, so an authored copy
  would double the record.
- [`IdentifyItem`][osrlib.crawl.commands.IdentifyItem] addresses a magic item by its
  session-scoped instance id, which no document can know.
- [`RollDice`][osrlib.crawl.commands.RollDice] produces a result no authored construct
  reads, so an authored roll would be a no-op that moved the adjudication stream.

The `character_id` of a grant or an award is a party selector in an authored
consequence, never a literal id, for the same unknowability reason `IdentifyItem` is
excluded. See [`osrlib.crawl.triggers`][osrlib.crawl.triggers] for the selector
vocabulary."""

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
"""An authored consequence, discriminated by `command_type`: the sub-union over
[`CONSEQUENCE_COMMAND_CLASSES`][osrlib.crawl.commands.CONSEQUENCE_COMMAND_CLASSES],
spelled out so a static type checker can read it. A document naming any other command
type fails to parse, which is what enforces it: typing a field with this union
needs no validator behind it."""


@cache
def _any_command_adapter() -> TypeAdapter:
    return TypeAdapter(AnyCommand)


@cache
def _known_command_types() -> frozenset[str]:
    return frozenset(variant.model_fields["command_type"].default for variant in ALL_COMMAND_CLASSES)


def parse_command(data: Mapping[str, object]) -> Command | None:
    """Parse one serialized command, skipping unknown command types.

    Call this on anything that arrived as data rather than as a Python object: a
    saved command log, a request body, a queue message. The command it returns is
    ready for
    [`GameSession.execute`][osrlib.crawl.session.GameSession.execute].

    An unknown `command_type` comes back as `None` rather than an exception, which
    is what lets an older engine read a log a newer one wrote: skip the `None`s and
    replay the rest. A malformed payload under a `command_type` the engine does know
    raises instead, because the sender meant a command this engine has and got it
    wrong.

    To have pydantic do the parsing inside a model or a web framework, annotate the
    value with [`AnyCommand`][osrlib.crawl.commands.AnyCommand] instead. That path
    raises on an unknown type rather than skipping it.

    Args:
        data: A mapping previously produced by a command's `model_dump`.

    Returns:
        The command, or `None` when its `command_type` is unknown.

    Raises:
        ContentValidationError: If the command type is known but the payload is
            malformed.

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
