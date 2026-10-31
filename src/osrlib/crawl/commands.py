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

Every command is a pydantic model. It's frozen, which means you can't change its fields after
you build it. A `command_type` field identifies its class, so a command serializes to a JSON
object with a `command_type` key. [`AnyCommand`][osrlib.crawl.commands.AnyCommand] is the union
of every command class, and pydantic uses the `command_type` key to pick the class when it parses
one. [`parse_command`][osrlib.crawl.commands.parse_command] turns one serialized mapping back
into a command. It returns `None` for a `command_type` it doesn't recognize.

Which commands the session accepts depends on where the party is. A
[`SessionMode`][osrlib.crawl.commands.SessionMode] is one of these states: `town`, `exploring`,
`encounter`, and `battle` while play goes on, and `game_over` or `victory` once the session
has ended. Each command class lists the modes it's legal in as its `allowed_modes` class
attribute. The session refuses a command sent in any other mode with
`session.command.wrong_mode`, before it runs any other check.

There are two kinds of command. A play command is an action the players choose, like moving
the party or attacking. A referee command is an action the game's referee takes, like granting
an item or spawning monsters.

Game logic issues referee commands, not the players. The issuer can be a
[`Listener`][osrlib.crawl.session.Listener] you write, the interpreter that runs an
adventure's triggers, or an LLM referee. Referee commands grant and take away items and coins, award
XP, add and remove party members, set flags, spawn monsters, end encounters, move the clock, and record
quest progress. Each one's summary starts with "Referee:". The session logs and replays them
like any other command. Most referee commands are legal in every mode. These are the
exceptions:

- [`PlaceParty`][osrlib.crawl.commands.PlaceParty] is illegal in `victory`, because it would
  restart play.
- [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters] and
  [`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty] are illegal in `game_over` and
  `victory`, for the same reason.
- [`EndEncounter`][osrlib.crawl.commands.EndEncounter] is legal only in `encounter` and
  `battle`, because it closes the open encounter.
- [`AddMember`][osrlib.crawl.commands.AddMember] and
  [`RemoveMember`][osrlib.crawl.commands.RemoveMember] are legal only in `town` and `exploring`.

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
  with one [`BattleDeclaration`][osrlib.crawl.commands.BattleDeclaration] per member. It's
  the only play command in `battle`. Most referee commands, including
  [`EndEncounter`][osrlib.crawl.commands.EndEncounter], work in `battle` too.
- Referee commands: [`GrantItem`][osrlib.crawl.commands.GrantItem],
  [`GrantCoins`][osrlib.crawl.commands.GrantCoins],
  [`RemoveItem`][osrlib.crawl.commands.RemoveItem],
  [`RemoveCoins`][osrlib.crawl.commands.RemoveCoins],
  [`AddMember`][osrlib.crawl.commands.AddMember],
  [`RemoveMember`][osrlib.crawl.commands.RemoveMember],
  [`AwardXP`][osrlib.crawl.commands.AwardXP],
  [`IdentifyItem`][osrlib.crawl.commands.IdentifyItem],
  [`SetFlag`][osrlib.crawl.commands.SetFlag],
  [`SetDoorState`][osrlib.crawl.commands.SetDoorState],
  [`PlaceParty`][osrlib.crawl.commands.PlaceParty],
  [`AdvanceTime`][osrlib.crawl.commands.AdvanceTime],
  [`RollDice`][osrlib.crawl.commands.RollDice],
  [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters],
  [`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty],
  [`EndEncounter`][osrlib.crawl.commands.EndEncounter],
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

from osrlib.core.character import Character
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
    "AddMember",
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
    "EndEncounter",
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
    "RemoveCoins",
    "RemoveItem",
    "RemoveMember",
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
    within one `schema_version`. `town`, `exploring`, `encounter`, and `battle` are the modes of
    a game in progress.

    A play command is an action the players choose, like
    [`MoveParty`][osrlib.crawl.commands.MoveParty]. A referee command is an action game logic
    takes, like [`GrantItem`][osrlib.crawl.commands.GrantItem], and its summary starts with
    "Referee:".

    `game_over` and `victory` are the terminal modes, which mean the session has ended.
    The session refuses every play command in both. It accepts most referee commands in both,
    with these exceptions:

    - [`PlaceParty`][osrlib.crawl.commands.PlaceParty] is illegal in `victory`.
    - [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters],
      [`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty], and
      [`AddMember`][osrlib.crawl.commands.AddMember] are illegal in both, because they would
      restart play.
    - [`EndEncounter`][osrlib.crawl.commands.EndEncounter] is illegal in both, because it
      needs an open encounter.
    - [`RemoveMember`][osrlib.crawl.commands.RemoveMember] is illegal in both, because the
      party changes only between encounters in a game that is still going.

    No play command leaves a terminal mode. A referee's `PlaceParty` is the only way out of
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
    """No party member is left standing, each one dead or turned to stone, and the session has
    ended. The session refuses play commands, and referee commands still work.
    [`PlaceParty`][osrlib.crawl.commands.PlaceParty] is the only way out: placing the fallen
    party in town lets you buy *raise dead* at the temple with
    [`PurchaseHealing`][osrlib.crawl.commands.PurchaseHealing], or bring in a magic-user with
    [`AddMember`][osrlib.crawl.commands.AddMember] to cast *stone to flesh* on a statue."""
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

    Read it from the class, like `MoveParty.allowed_modes`, and compare it with
    [`GameSession.mode`][osrlib.crawl.session.GameSession.mode] to offer only the commands
    the session accepts now. It's a class attribute, not a field, so it never serializes.
    [`GameSession.execute`][osrlib.crawl.session.GameSession.execute] checks it before
    anything else. The session refuses a command sent in any other mode with
    `session.command.wrong_mode`.

    The base class allows every mode. Every play command limits its modes. Most referee
    commands allow all of them, but these referee commands limit theirs too:
    [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters],
    [`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty],
    [`PlaceParty`][osrlib.crawl.commands.PlaceParty],
    [`EndEncounter`][osrlib.crawl.commands.EndEncounter],
    [`AddMember`][osrlib.crawl.commands.AddMember], and
    [`RemoveMember`][osrlib.crawl.commands.RemoveMember]. Each command's `Modes:` section lists
    its modes."""

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

        When the step's movement completes a turn on the game clock, the events of that turn
        follow:
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
    """Turn the party in place to a new facing (takes no game time).

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
    fight starts. Pass the new order in `order`, a tuple of every member's id, front first.
    Send this command in town or while exploring. The session refuses it once an encounter or
    a battle has begun.

    During a battle, the order changes only when a member withdraws or retreats alone. That
    member trades places with the member named in
    [`BattleDeclaration.replacement_id`][osrlib.crawl.commands.BattleDeclaration.replacement_id].
    The new order stays after the battle, until you send this command again.

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
    """Open an unstuck, unlocked door on one side of the party's cell (takes no game time).

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
    """Close an open door on one side of the party's cell (takes no game time).

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
    """Force a stuck door open with one member's STR-based open-doors roll (takes no game time).

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
    """Wedge a door with an iron spike so it can't swing shut or be closed (takes no game time).

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
    """Listen at a door for noise from the area beyond it (takes no game time).

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
        with code `exploration.door.unlocked`. The attempt costs one turn, and the events of
        that turn follow: light burning down, the fatigue rule, and the wandering-monster
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

    By default the goods spread across the living members:

    - Each item goes to a member whose class can use it, so the fighter takes the plate mail
      and the magic-user takes the arcane scroll.
    - Gems and jewellery divide by worth.
    - Coins divide evenly, one denomination at a time.

    A member who carries more than their maximum load can't move, and then the party can't
    move either. So the session never gives a member more than their maximum load. The maximum
    load is a weight of 1,600 coins. Under detailed encumbrance, a member who wears gauntlets of
    ogre power can carry 2,600. The party moves at its slowest member's rate, so a heavy load on
    one member slows everyone.

    Name `recipient_id` to have one member take all the goods instead, up to their maximum
    load. Whatever the carriers can't carry stays in the pile on the cell. The party can
    lighten its load and come back for it. To change who carries what afterwards, use
    [`GiveItems`][osrlib.crawl.commands.GiveItems].

    Under the immediate XP timing, the treasure's XP divides evenly among the living
    members, however the goods end up split, as the OSE SRD's rule for treasure XP says.

    The named recipient, or the first living member in marching order when nobody is named,
    reaches into the cache. A cache whose trap hasn't been removed gets its chance to spring
    on that member first.

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
    """One member to take all the goods, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id], or `None` to spread it across the
    living members. The named member fills their pack up to their maximum load, and the rest
    stays on the cell. Name a recipient to keep a specific item with a specific member. The
    named member is also the one who reaches into the cache, so a trap that hasn't been
    removed springs on them. If that trap leaves them unable to act, the goods spread across
    the living members instead."""


class DropItems(Command):
    """Drop items and coins from one member onto the party's cell, or behind a fleeing party.

    It's legal while exploring a dungeon and during an encounter. Goods dropped on the cell
    go into its pile, and [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure] with
    `feature_id="pile"` picks them back up. While exploring, a drop takes no time.

    During an encounter, the drop uses up the encounter round, and the monsters then act on
    their reaction to the party, as for a [`Wait`][osrlib.crawl.commands.Wait]. While
    monsters pursue the party after an
    [`Evade`][osrlib.crawl.commands.Evade], dropped goods scatter behind the party instead of
    landing in a pile, and can't be picked back up. Dropped coins tempt intelligent pursuers
    and dropped rations tempt unintelligent ones, which can end the pursuit.

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
        was dropped. In an encounter, the encounter round's events follow. During a pursuit,
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
    """Hand items and coins from one party member to another (takes no game time).

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
    giver's purse to the recipient's, which is how a party evens out the weight after it takes
    a lot of treasure."""


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
    """Put out every torch and lantern one member has burning (takes no game time).

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
    """Equip an item from a member's pack: wear armour or a ring, or wield a weapon (takes no game time).

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
    """Take off or put away an equipped item, returning it to the member's pack (takes no game time).

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

    Send it in town or while exploring. Set `kind` to `"turn"`, `"night"`, or `"day"` to choose
    the length. A turn is ten minutes of game time. A rest clears the party's fatigue, the −1
    penalty a party in a dungeon gets when it goes six turns without a turn of rest
    ([`FATIGUE_KIND`][osrlib.crawl.exploration.FATIGUE_KIND]). Three turns of rest also clear
    exhaustion, the heavier penalty a party gets for running from a long pursuit
    ([`EXHAUSTED_KIND`][osrlib.crawl.exploration.EXHAUSTED_KIND]).

    A night or a day of rest counts as sleep, which casters need before
    [`PrepareSpells`][osrlib.crawl.commands.PrepareSpells]. A full day of rest also applies
    natural healing to every living member.

    In a dungeon, the wandering-monster checks go on while the party rests. A check that
    turns up monsters interrupts the rest and opens an encounter. To lower the chance of each
    check by 1, set `hidden` when the party rests quietly in an out-of-the-way place.

    An interrupted rest isn't sleep, and it heals nobody. It still counts the full turns the
    party rested before the monsters came. One full turn clears fatigue, and each full turn
    counts toward the three turns of rest that clear exhaustion. A rest interrupted during
    its first turn clears nothing.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` - an encounter or battle is underway, or the
          session has ended.

    Events:
        The events of the time that passes come first: light burning down, provisions, and
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
    a `night` or a `day` rest. Exhaustion from a pursuit clears after three turns of rest in
    total. In a dungeon, a wandering encounter can cut any of the three short."""
    hidden: bool = False
    """Whether the party rests quietly in an out-of-the-way place, which the OSE SRD calls hiding.

    On a level with the usual 1-in-6 chance of wandering monsters, a hidden rest makes no
    wandering-monster rolls at all, as long as the party has made no noise and has no bright
    light. That's because `True` lowers the chance of each check during the rest by 1, after
    noise and bright light have raised it, and the session makes no roll for a chance of 0 in 6.
    In town no checks run, so the flag changes nothing there.

    Your game or the referee decides whether the place qualifies. The session accepts the flag
    as given. With `False`, the default, the checks run at the level's chance with noise and
    light added, as they do while the party explores."""


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
    `use_item` [`BattleDeclaration`][osrlib.crawl.commands.BattleDeclaration] instead. Name the
    member in `character_id` and the item in `item_id`. Using an item identifies it and reveals
    any curse. A magic sword that can light up when its wielder says so turns its light on or off.

    A device is a wand, a staff, or a rod. `target_id` is the id of whoever the item is used on:
    a party member for a staff of healing's touch, or an encounter group for a device that covers
    an area. When the member reads a scroll with several spells on it, choose the spell and its
    targets with `spell_id`, `mode`, and `targets`. They work like the same fields of
    [`CastSpell`][osrlib.crawl.commands.CastSpell].

    A scroll casts its spell at the lowest level that can cast it, whatever the reader's level.
    The session checks the read at that level. For example, a *magic missile* scroll fires one
    missile, so it takes one target, even when a 6th-level magic-user reads it. The session
    refuses a read before it uses up the scroll.

    Modes:
        `exploring`, `encounter`

    Rejections:
        Any item:

        - `session.command.wrong_mode` - the session is in town or in battle, or has
          ended.
        - `session.command.unknown_member` - `character_id` matches no party member.
        - `session.command.member_incapacitated` - the member can't act.
        - `exploration.item.not_carried` - the member carries no magic item with
          that instance id.
        - `items.use.not_usable` - the item has no use, or the member's class can't
          use the device.

        By kind of item:

        - Potions: `items.use.target_not_allowed` when `target_id` is set to someone other
          than the member who drinks the potion.
        - Scrolls: `exploration.action.requires_light` (reading needs light, and
          infravision isn't enough), `items.scroll.spent`, `items.scroll.no_such_spell`,
          and `items.scroll.wrong_caster`. A read also gets the checks of a cast, at the
          scroll's level: `magic.cast.unknown_target`, `magic.cast.unknown_mode`,
          `magic.cast.target_count`, `magic.cast.out_of_range`,
          `magic.cast.caster_incapacitated`, `magic.cast.caster_restrained`, and
          `magic.cast.anti_magic_shell`.
        - Devices: `items.device.inert` (no charges left),
          `items.use.target_required` (an area device used in an encounter needs a
          `target_id`), `items.use.unknown_target`, and `items.use.battle_only` (a device
          that strikes works only as a battle declaration).

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
    """Who the item is used on, for a device: a wand, a staff, or a rod.

    Pass a party member's id for a device that heals by touch, or an encounter group's
    [`EncounterGroupView.id`][osrlib.crawl.views.EncounterGroupView.id] for a device that covers
    an area. A healing device with `None` heals the member using it.

    Leave it `None` for a potion, because the member who uses a potion drinks it. The session
    also accepts the drinker's own id, the same as `character_id`. It refuses any other id with
    `items.use.target_not_allowed`."""
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
    [`UseItem`][osrlib.crawl.commands.UseItem] identifies it as well. This command is
    legal in every mode, `game_over` and `victory` included, and the session logs and replays it
    like any other command.

    Identifying an item tells the party what it is. After it, player-visible events can show the
    item's name, not only its instance id. A cursed item also has its curse revealed, and the
    bearer can't take it off until *remove curse*. An item that's already identified, with any
    curse already revealed, emits no event, and the command still succeeds.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        - `session.command.unknown_member` - `character_id` matches no party member.
        - `session.command.unknown_item` - the member carries no magic item with
          that instance id.

    Events:
        [`ItemIdentifiedEvent`][osrlib.crawl.events.ItemIdentifiedEvent] the first time. For a
        cursed item, a [`CurseRevealedEvent`][osrlib.crawl.events.CurseRevealedEvent] follows.
        None for an item that's already identified and revealed.
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
        attack). When the movement completes a turn on the game clock, the events of that
        turn follow, as they do for a step.
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
        First, the events of the travel time passing:
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
        events of the travel time passing, then
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
    """Buy equipment in town for one member, one purchase lot for each `item_ids` entry (takes no game time).

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
    """Sell gems and jewellery in town at their full value (takes no game time).

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
    """Buy a temple healing service in town (takes no game time).

    The party must be in town. Name the patient in `character_id` and the service in `service`.
    The OSE SRD describes the base town in prose and lists no temple prices, so osrlib sets
    these, by `service` value: `cure_light_wounds` 25 gp, `cure_serious_wounds` 100 gp,
    `cure_disease` 150 gp, `neutralize_poison` 150 gp, `remove_curse` 200 gp, and `raise_dead`
    1,500 gp. You can read the same list from [`HEALING_SERVICES`][osrlib.crawl.exploration.HEALING_SERVICES]. Each
    service works as the matching spell, cast on the member by a temple cleric of the lowest
    level that can cast it.

    The whole party pays the fee, not only the patient. The temple takes it from the patient's
    purse first, then from the other members' purses in marching order, dead members included,
    until the fee is paid.

    Each purse pays only in whole gold pieces, and any value below a gold piece stays in the
    purse. For example, two members who each have 12 gp and
    5 sp are worth 25 gp together. Each purse can pay only 12 gp, so together they can pay 24
    gp, and the session refuses a 25 gp service.

    Because the party pays, a dead member can be raised even when that member's purse is
    empty, and the party doesn't have to move coins between purses first.

    Modes:
        `town`

    Rejections:
        - `session.command.wrong_mode` - the party is not in town.
        - `session.command.unknown_member` - `character_id` matches no party member.
        - `items.purchase.insufficient_funds` - the whole gold pieces in the party's
          purses add up to less than the fee.
        - `town.healing.no_effect` - the service would do nothing for the member. That's
          `raise_dead` for a living member, `cure_disease` for a member who isn't diseased,
          and `neutralize_poison` for a member who isn't poisoned and didn't die of poison
          in the last 10 rounds. The session checks the party's funds first, so a party
          that can't pay gets `items.purchase.insufficient_funds` instead.

    Events:
        A [`HealingPurchasedEvent`][osrlib.crawl.events.HealingPurchasedEvent] listing
        which purses paid and how much each one paid, then the events of the service's
        spell, like healing, the end of a poison or a disease, or the result of a *raise dead*.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.TOWN})

    command_type: Literal["purchase_healing"] = "purchase_healing"
    character_id: str
    """Who is treated, by [`MemberView.id`][osrlib.crawl.views.MemberView.id]. This member's
    purse pays first, and the rest of the party pays whatever is left. The member can be dead.
    `raise_dead` is the service for a dead member, and the session refuses it for a living
    one."""
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
        - `encounter.parley.mid_pursuit` - the monsters are pursuing the party, and
          nobody can talk during a pursuit.
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

    An encounter must be open. Monsters whose stance is `attacks` or `hostile` pursue the
    party, if any of them can move. The party gets away at once when no monster pursues it,
    or when it runs faster than the slowest monster that pursues it. Otherwise a pursuit
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
        - `encounter.evade.already_evading` - a pursuit is already running.
        - `encounter.evade.nothing_to_drop` - no living member has coins (for
          `treasure`) or rations (for `food`) to scatter.

    Events:
        [`ItemsDroppedEvent`][osrlib.crawl.events.ItemsDroppedEvent]s for the scattered
        bait, then an [`EvasionEvent`][osrlib.crawl.events.EvasionEvent] with code
        `encounter.evasion.succeeded`, which ends the encounter, or
        `encounter.evasion.pursuit`, which starts a pursuit, and the pursuit's first
        [`PursuitEvent`][osrlib.crawl.events.PursuitEvent] round. A pursuit ends when the
        party escapes, when it exhausts the party at the round limit
        ([`ExhaustionEvent`][osrlib.crawl.events.ExhaustionEvent], with an
        [`EffectAttachedEvent`][osrlib.core.events.EffectAttachedEvent] and a
        [`ConditionGainedEvent`][osrlib.core.events.ConditionGainedEvent] for the
        exhaustion), or when the monsters catch the party and battle starts
        ([`BattleStartedEvent`][osrlib.crawl.events.BattleStartedEvent]).

        When the encounter ends, the session emits an
        [`EncounterEndedEvent`][osrlib.crawl.events.EncounterEndedEvent], a
        [`MonsterDefeatedEvent`][osrlib.crawl.events.MonsterDefeatedEvent] for each
        monster killed or driven off,
        [`EffectReleasedEvent`][osrlib.core.events.EffectReleasedEvent]s for the
        effects that end with it, and, under the immediate XP timing,
        [`XpAwardedEvent`][osrlib.crawl.events.XpAwardedEvent] and
        [`CharacterLeveledUpEvent`][osrlib.crawl.events.CharacterLeveledUpEvent].
        The clock then advances by the time the encounter took, with the usual
        [`LightEvent`][osrlib.crawl.events.LightEvent] and
        [`ProvisionsEvent`][osrlib.crawl.events.ProvisionsEvent] events for that time.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.ENCOUNTER})

    command_type: Literal["evade"] = "evade"
    drop: Literal["none", "treasure", "food"] = "none"
    """What to scatter behind the party as it runs. `treasure` empties every living member's
    purse onto the trail, where the coins are lost, and tempts intelligent monsters to stop
    for them. `food` drops one ration from each living member who carries one, and tempts
    unintelligent monsters. `none`, the default, drops nothing. The bait is dropped even when
    the party gets away at once, but it has an effect only during a pursuit. When nobody has anything
    of that kind to drop, the session refuses the command with
    `encounter.evade.nothing_to_drop`, and the party doesn't run."""


class EngageBattle(Command):
    """Start a battle with the monsters the party has met.

    An encounter must be open. Attacks, spells, and an item's striking effects
    happen only in battle, so this is how the party starts a fight.
    [`TurnUndead`][osrlib.crawl.commands.TurnUndead] is the one aggressive act the
    party can take in an encounter without starting a battle first. When the monsters were
    surprised at the start of the encounter, the party gets a free first round. During a
    pursuit, the party turns and fights at the current distance. Once the battle has
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
    and `hostile` monsters attack once their patience runs out. During a pursuit after an
    [`Evade`][osrlib.crawl.commands.Evade], waiting runs one round of the pursuit instead.

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
        pursuit, a [`PursuitEvent`][osrlib.crawl.events.PursuitEvent] round runs
        instead, which can end in an
        [`ExhaustionEvent`][osrlib.crawl.events.ExhaustionEvent] at the round limit
        (with an [`EffectAttachedEvent`][osrlib.core.events.EffectAttachedEvent]
        and a [`ConditionGainedEvent`][osrlib.core.events.ConditionGainedEvent] for the
        exhaustion) or in battle.

        When the encounter ends, the session emits
        [`EncounterEndedEvent`][osrlib.crawl.events.EncounterEndedEvent], a
        [`MonsterDefeatedEvent`][osrlib.crawl.events.MonsterDefeatedEvent] for each
        monster killed or driven off,
        [`EffectReleasedEvent`][osrlib.core.events.EffectReleasedEvent]s, and, under
        the immediate XP timing,
        [`XpAwardedEvent`][osrlib.crawl.events.XpAwardedEvent] and
        [`CharacterLeveledUpEvent`][osrlib.crawl.events.CharacterLeveledUpEvent].
        The round also brings the usual
        [`EffectExpiredEvent`][osrlib.core.events.EffectExpiredEvent],
        [`LightEvent`][osrlib.crawl.events.LightEvent], and
        [`ProvisionsEvent`][osrlib.crawl.events.ProvisionsEvent] events for the time that
        passes.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.ENCOUNTER})

    command_type: Literal["wait"] = "wait"


class TurnUndead(Command):
    """Present a holy symbol to turn undead monsters before a battle starts (one round).

    An encounter must be open, because outside one there are no monsters to turn. In
    battle, declare a `turn_undead`
    [`BattleDeclaration`][osrlib.crawl.commands.BattleDeclaration] instead.

    The turning reaches every living monster in the encounter's groups that haven't fled. It
    skips a monster that died earlier in the encounter, from a wand for example.

    A turned monster gains the `turned` condition. It flees from the cleric and attacks no one.
    A destroyed monster dies. The
    [`UndeadTurnedEvent`][osrlib.core.events.UndeadTurnedEvent] reports the outcome, and a
    separate condition or death event follows for each monster it affects.

    Presenting the symbol is an aggressive act. If any monster is left neither turned nor
    destroyed, the monsters that weren't turned attack at once, and battle starts. In a group
    that was only partly turned, the turned monsters split off as a new group and flee.

    Modes:
        `encounter`

    Rejections:
        - `session.command.wrong_mode` or `encounter.none_active` - no encounter is open.
        - `encounter.turning.mid_pursuit` - the monsters are pursuing the party, and
          nobody can turn undead during a pursuit.
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
        ([`BattleStartedEvent`][osrlib.crawl.events.BattleStartedEvent]). A
        [`MonsterFledEvent`][osrlib.crawl.events.MonsterFledEvent] follows for each new group
        of turned members.
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
    members who have to declare this round. To offer only choices the session accepts, read
    these from the same player view:

    - Each group's
      [`front_rank`][osrlib.crawl.views.EncounterGroupView.front_rank], the members who can
      attack it in melee this round without moving, and its
      [`member_distance_feet`][osrlib.crawl.views.EncounterGroupView.member_distance_feet],
      each member's distance to it.
    - Each member's
      [`encounter_rate_feet`][osrlib.crawl.views.MemberView.encounter_rate_feet], how far
      they can move this round. A member can close on a group and attack it in the same round
      when their distance to it is at most that rate plus 5'.
    - `immobile` and `reloading` on the encounter view: who can't move, and who can't fire a
      reloading weapon this round.

    To check one member's choice against the choices already made for the round, call
    [`preview_battle_round`][osrlib.crawl.battle.preview_battle_round] with them.

    `action` decides which other fields apply. Leave the others at their defaults.

    - `attack` uses `target_group_id` and, optionally, `weapon_id`, `move`, and `back_stab`.
      With no `weapon_id`, the member attacks with the wielded weapon that suits the attack,
      or with bare hands when they wield none. Set `move="close"` to close on the group first
      and attack it in the same round. Set `back_stab` for a thief's melee attack in the free
      round the party gets when it surprises the monsters.
    - `cast` uses `spell_id`, `spell_mode`, `reversed`, and `targets`. `spell_id` is a spell
      id, like `"sleep"`. `spell_mode` is the key of one of the spell's modes, and `reversed`
      picks the spell's reversed form. A caster can't move in the round they cast, so leave
      `move` at `None`.
    - `move` uses the `move` field, which is `close`, `fighting_withdrawal`, or `retreat`. A
      `close` moves this member toward the group in `target_group_id`, and nobody else. A
      `fighting_withdrawal` backs this member away slowly, facing the monsters, and a
      `retreat` turns and runs. When every member in `declarers` makes the same
      `fighting_withdrawal` or `retreat`, it's a formation move instead: the whole party moves
      as one body, and every group's distance changes.
    - `use_item` uses `item_id`. The member drinks a potion, reads a scroll, uses a wand,
      staff, or rod, or throws a flask of oil or a similar item at `target_group_id`. A
      staff of striking is a melee weapon, so only a member in the group's `front_rank` can
      strike with it.
    - `turn_undead` uses nothing else. It reaches every living monster in the groups that
      haven't fled when it resolves. Turning resolves with the spells in the round. A hit
      can't disrupt it, because it's a class ability, not a spell.
    - `hold` does nothing. A member with no legal action declares it, because every member
      in `declarers` needs a declaration.

    Only `attack` and `move` take a `move`. The session refuses a `cast` that sets one with
    `battle.declaration.cast_cannot_move`, and a `turn_undead`, `use_item`, or `hold` that sets
    one with `battle.declaration.move_not_combinable`.

    `gaze_defense` goes with any action. It guards the member against a petrifying gaze, like
    a basilisk's or a medusa's, for this round only. Its values are `avert_eyes` and `mirror`.

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
    """What this member does. `attack` strikes or shoots at a group, after a `close` when the
    declaration sets one, `cast` casts a spell, `turn_undead` presents a holy symbol, `move` moves
    the member toward the monsters or away from them, `use_item` uses a magic item or throws an
    item, and `hold` does nothing. This field determines which other fields apply, and `hold`
    needs none of them."""
    target_group_id: str | None = None
    """Which monster group the action is aimed at, by
    [`EncounterGroupView.id`][osrlib.crawl.views.EncounterGroupView.id] from
    [`EncounterView.groups`][osrlib.crawl.views.EncounterView.groups].

    These declarations need it: an `attack`, a `close`, a thrown item, a wand, staff, or rod
    that strikes or covers an area, and a `cast` of a spell whose area covers creatures. An area
    spell aimed at a group isn't placed at a point, because the range track has no positions
    inside a group. It covers the group's living members in the order they were spawned, as many
    as its size and the party's rank width allow.

    Some spell modes affect a total number of Hit Dice, like *sleep*'s. Their targeting mode is
    `hd_budget`. A `cast` in such a mode can set either this field or `targets`. With this field,
    the session collects the group's living members in spawn order. When the mode also has an
    area, like *death spell*'s 60' cube, it collects only the members inside the area, as for any
    area spell. The spell then spends its Hit Dice budget on the members it collected, fewest Hit
    Dice first.

    The session refuses a group that isn't in the encounter, or one whose
    [`EncounterGroupView.fled`][osrlib.crawl.views.EncounterGroupView.fled] is true, with
    `battle.declaration.unknown_group`."""
    weapon_id: str | None = None
    """Which wielded weapon to attack with, or `None` to have the session choose one.

    Name a mundane weapon by its catalog id, or a magic weapon by its per-instance id. The weapon
    has to be equipped already, so equip it with [`EquipItem`][osrlib.crawl.commands.EquipItem]
    before the fight. The session refuses a weapon that's only carried with
    `battle.declaration.weapon_not_wielded`. Whether the attack is melee or missile depends on the
    weapon's qualities and the group's distance.

    With `None`, the member attacks with the wielded weapon that suits the attack. The member's
    distance to the group decides the attack: melee at 5' or less, and missile beyond 5'. It's the
    distance when the session accepts the round, after the member's own `close` when the
    declaration sets one. So a member who closes into reach with a dagger strikes with it, and a
    member who stays 20' away throws it. The session chooses the weapon this way:

    - It looks through the member's wielded items in the order they were equipped. It takes the
      first one with the `melee` quality for a melee attack, or the `missile` quality for a
      missile attack.
    - A real weapon comes before an improvised one, whatever order they were equipped in. An
      improvised weapon is a gear item, like a torch, that has the `melee` quality. So a member
      who lit a torch and then drew a sword swings the sword. The torch counts only when nothing
      else suits the attack.
    - For a missile attack, it takes the first weapon whose long range reaches the group. When
      none reaches, it takes the first missile weapon, and the session refuses the round with
      `combat.attack.out_of_range` before any of it resolves.
    - A splash weapon, like a flask of oil or a vial of holy water, never counts, because a
      `use_item` declaration throws it. A wand, or another magic item with no base weapon, has
      no weapon stats, so it never counts either.
    - A member with no weapon that suits the attack strikes unarmed.

    The session makes this choice once, when it accepts the round. The attack resolves with that
    weapon even when the distance changes before the attack resolves. The attack's
    [`AttackRolledEvent`][osrlib.core.events.AttackRolledEvent] reports the choice in
    `attack_name`: the weapon's name, `"improvised"` for a gear item like a torch, or
    `"unarmed"`."""
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
    [`CastSpell.targets`][osrlib.crawl.commands.CastSpell.targets]. Each one is a member id, a
    monster id, or, for a spell that affects a place, a cell reference like
    `cell:crypt:1:3,4` that [`cell_ref`][osrlib.crawl.dungeon.cell_ref] builds. Leave it empty
    for a spell whose area covers creatures, which takes `target_group_id` instead.

    For a mode that affects a total number of Hit Dice (`hd_budget`), you can leave this empty
    and set `target_group_id` instead. The session then collects the targets from the group, as
    [`target_group_id`][osrlib.crawl.commands.BattleDeclaration.target_group_id] describes. When
    you set both fields, the session uses this one.

    The session refuses an invisible target with `battle.declaration.invisible_target`."""
    move: Literal["close", "fighting_withdrawal", "retreat"] | None = None
    """Which movement this member makes this round, with a `move` action or, for `close` only, with an
    `attack`.

    A battle measures distance on the range track. Each group stands at a distance from the party
    line, where the party stood when the encounter opened. Each member stands at an advance forward
    of that line, in [`EncounterView.advance_feet`][osrlib.crawl.views.EncounterView.advance_feet],
    and starts the battle on it. A member's distance to a group is the group's distance minus their
    advance. A member moves at their own encounter movement rate,
    [`MemberView.encounter_rate_feet`][osrlib.crawl.views.MemberView.encounter_rate_feet]: a third of
    their movement rate after encumbrance, doubled by *haste* on that member.

    `close` moves this member toward `target_group_id` at their rate, and nobody else. The member
    stops 5' from that group, and stops sooner rather than come within 5' of another group that's
    still in the fight. A member already within 5' of the group doesn't move.

    - With a `move` action, the member only moves.
    - With an `attack`, the member moves in the party's movement step and attacks in the missile
      or the melee step of the same round. The session judges the attack as if the move had
      already happened. The member's distance after the move decides whether it's a melee or a
      missile attack, and a melee attack needs the member to be within 5' of the group and in its
      front rank after every move the round declares. A member who can't get that close this round
      gets `combat.attack.out_of_reach`, with that distance as `distance_feet`, and a member who
      gets close but finds no place left in the front rank gets
      `battle.declaration.not_in_front_rank`.

    The monsters can act before the party. A melee attack resolves only if, at the party's melee
    step, the member stands in the group's front rank within 5' of it. The one exception is a group
    that has started to flee. A member who stood within 5' of it when the round was accepted still
    gets the attack, with the bonus against a retreating combatant. Otherwise the member makes no
    attack, and the session reports a
    [`DeclarationFizzledEvent`][osrlib.core.events.DeclarationFizzledEvent] at
    `battle.melee.fizzled`, with the group's id as `group_id`. Its `reason` is
    `combat.attack.out_of_reach` when the member is farther than 5' from the group, and
    `battle.declaration.not_in_front_rank` when the member is within 5' but not in its front
    rank.

    `fighting_withdrawal` and `retreat` take a member away from the monsters, with a `move` action.
    A `fighting_withdrawal` backs away at half the member's encounter rate, facing the monsters, and
    a `retreat` turns and runs at the full rate. The member makes no attack that round. Each one is
    either a formation move or a move the member makes alone:

    - When every member in `declarers` declares the same one, it's a formation move: the whole
      party moves as one body. That includes a round with only one declarer. The party moves at the
      slowest living member's encounter rate, halved for a `fighting_withdrawal`. Every group's
      distance grows by that much, and no member's advance changes, so members who stand apart
      stay apart. A formation `retreat` ends the battle. If no monster can pursue the party, the
      party gets away and the encounter closes. Otherwise the session goes back to `encounter` mode
      with a pursuit, which [`PursuitState`][osrlib.crawl.encounter.PursuitState] describes. The
      pursuit's starting gap is the smallest distance from any living member to any group that can
      follow, after the move. So a member who stood forward of the others starts the chase nearest
      the pursuers. Send
      [`Wait`][osrlib.crawl.commands.Wait] to run each round of it, or
      [`EngageBattle`][osrlib.crawl.commands.EngageBattle] to turn and fight.
    - Otherwise, the member moves back alone, and their advance goes down. It can go below zero,
      behind the line, because the way the party came is clear. When that leaves every member the
      monsters can see behind the line, the line moves back to the foremost of them at the end of
      the movement step. Every advance and every group's distance grow by the same amount, so no
      distance between a member and a group changes, as
      [`BattleState.advance_feet`][osrlib.crawl.battle.BattleState.advance_feet] describes. Any
      member can fall back alone, wherever they stand, and nobody else moves. It doesn't end the
      battle, even when it's a `retreat`. Once the member has moved, the members who are now
      nearest each group form its front rank.

    A member who retreats, alone or with the formation, is easier to hit for the whole round,
    whichever side acts first. Attackers get +2 against them and ignore the retreating member's
    shield.

    The session refuses a `move` action with no `move` with `battle.declaration.missing_move`, a
    `close` at a group that isn't in the encounter or has fled with
    `battle.declaration.unknown_group`, and any move by a member in
    [`EncounterView.immobile`][osrlib.crawl.views.EncounterView.immobile] with
    `battle.declaration.cannot_move`. An `attack` takes only `close`, and gets
    `battle.declaration.move_not_combinable` with a `fighting_withdrawal` or a `retreat`. A `cast`
    that sets any move gets `battle.declaration.cast_cannot_move`, because the OSE SRD says "The
    spell caster cannot move and cast a spell in the same round". A `turn_undead`, a `use_item`, or
    a `hold` that sets any move gets `battle.declaration.move_not_combinable`. Each of these two
    codes has the member's id as its `character` param. The session checks for them after the
    back-stab checks and before the action's own checks.
    [The adaptations register](https://mmacy.github.io/osrlib-python/adaptations/)
    describes these rules in full."""
    replacement_id: str | None = None
    """A member to trade places with in the marching order, for a `fighting_withdrawal` or a
    `retreat` this member makes alone, by [`MemberView.id`][osrlib.crawl.views.MemberView.id].

    Leave it `None` for every other declaration, a formation move included. A member who falls
    back alone doesn't need to name anyone. Nobody else moves, and the members who are then nearest
    each group form its front rank.

    The trade changes only the marching order. It happens in the party's movement step, right
    after the withdrawing member's move, and a
    [`MemberWithdrewEvent`][osrlib.crawl.events.MemberWithdrewEvent] reports it. Marching order
    decides who stands in front among members at the same distance from a group who got there in
    the same round. So when the withdrawing member steps out of a front rank, naming a member who
    stands at the same distance brings that member to the front. The trade doesn't move the named
    member. A member who stands farther back reaches the front rank only with a `close` of their
    own. The new marching order stays after the battle.

    The named member has to be in
    [`EncounterView.declarers`][osrlib.crawl.views.EncounterView.declarers], can't be the
    withdrawing member, can't be making a `fighting_withdrawal` or a `retreat` of their own, and
    can't already be named by a withdrawing member earlier in marching order. Otherwise the session
    refuses the declaration with `battle.declaration.replacement_unavailable`, with the withdrawing
    member's id as the `character` param and the named id as `replacement`. A declaration in a
    formation move that sets this field gets the same code, because every member is moving.

    The trade happens only when the withdrawing member's move happens, and only while both members
    are alive. A withdrawing member who can no longer move when the movement step comes stays where
    they are, and the two keep their places in the marching order."""
    item_id: str | None = None
    """Which item a `use_item` declaration uses: a magic item's per-instance id for a potion, a
    scroll, a wand, a staff, or a rod, or a mundane item's catalog id for an item thrown at a
    group, like a flask of oil. The session refuses a mundane item with no combat use, or a
    magic item that can't be used in battle, with `battle.declaration.item_unusable`."""
    gaze_defense: Literal["avert_eyes", "mirror"] | None = None
    """How this member guards against a petrifying gaze this round, or `None` for no guard.

    A petrifying gaze, like a basilisk's or a medusa's, reaches each member who stands in the
    gazer's group's [`front_rank`][osrlib.crawl.views.EncounterGroupView.front_rank], within 5' of
    it, once a round. The session rolls a saving throw
    versus paralysis for each member it reaches, with no declaration needed. A member who fails
    turns to stone. The OSE SRD's basilisk entry says "Unless averting eyes or using
    a mirror, characters in melee are affected each round". This field picks one of those two
    guards:

    - `avert_eyes`: the gaze passes the member by, and the session gives them the
      `averted_eyes` condition for the round. The basilisk's and the medusa's entries set the
      cost: "-4 penalty to-hit; the basilisk gains a +2 bonus to attack". Every attack roll the
      member makes this round takes −4, whatever it's aimed at. Each attack a gazing monster
      makes on the member this round gains +2. Other monsters' attacks gain nothing, because
      the OSE SRD gives the bonus only to the gazer.
    - `mirror`: the member fights by watching a mirror, and the gaze passes them by. The
      basilisk's entry says "The reflection of a basilisk is harmless. Fighting by looking in a
      mirror incurs a -1 penalty to attack." Every attack roll the member makes this round takes
      −1, whatever it's aimed at, and no monster gains a bonus. The member has to carry a
      `mirror` from the equipment catalog, or the session refuses the declaration with
      `battle.declaration.no_mirror`. The OSE SRD also says that a gazer that sees its own
      reflection saves or turns to stone. osrlib doesn't apply that rule. If your game uses it,
      your game or the referee decides when it applies and resolves the gazer's save.

    You can set a guard with any `action`. It lasts from when the session accepts the round
    until the round ends. It makes no random roll and emits no event. The command log records it
    with the declaration.

    A member outside the gazer's group's front rank doesn't need a guard, because the gaze
    doesn't reach them.
    Neither does a member who can't act and so doesn't declare, because the gaze passes them by.

    A basilisk that surprises the party turns its gaze on every member once as the encounter
    opens, before the party can declare anything, as the OSE SRD's "Characters surprised by a
    basilisk meet its gaze" says. No guard protects against that gaze.
    [The adaptations register](https://mmacy.github.io/osrlib-python/adaptations/) describes the
    gaze in full."""
    back_stab: bool = False
    """Make this `attack` a back-stab, or leave it `False` for an ordinary attack.

    The OSE SRD's thief entry says "When attacking an unaware opponent from behind, a thief
    receives a +4 bonus to hit and doubles any damage dealt." osrlib has no facing in battle, so
    the session reads an unaware opponent from behind as a monster the party surprised, in the
    round it can't act. Set this field only when all of these are true:

    - The action is `attack`.
    - The member's class has the `back_stab` ability, as a thief's does.
    - The monsters can't act this round because the party surprised them. That's the party's
      free round, while
      [`BattleState.monsters_hold_rounds`][osrlib.crawl.battle.BattleState.monsters_hold_rounds]
      is above zero. The session reads it when it accepts the round.
    - The attack is a melee attack. The weapon and the member's distance to the group after their
      `close`, if the declaration sets one, decide that, as
      [`weapon_id`][osrlib.crawl.commands.BattleDeclaration.weapon_id] describes.

    A thief can close on a group and back-stab in the same round: declare `attack` with
    `move="close"` and `back_stab=True`. The session judges the back-stab at the distance the move
    will reach. So a thief reaches a group up to their encounter rate plus 5' away. That's 35' for a
    thief in leather armour under the ruleset's default `basic` encumbrance, and 45' for one in no
    armour. *Haste* doubles the rate. A group farther away can't be back-stabbed in the free round,
    because the free round is the only round the monsters are unaware.

    An accepted back-stab hits the same monster an ordinary melee attack would. It gets +4 to
    hit, the defender's shield doesn't count, and its damage is doubled. The bonus and the
    doubling come from the class's `back_stab` ability. Every attack roll the declaration makes
    in the round is a back-stab, even when the monsters win initiative, and the attack's
    [`AttackRolledEvent`][osrlib.core.events.AttackRolledEvent] has `back_stab` set to true, so
    your game can say so.

    The session refuses a back-stab with the first of these that applies, in this order. Each
    of the four back-stab codes has the member's id as its `character` param.

    1. `battle.declaration.back_stab_not_attack`: the action isn't `attack`. The session checks
       this before any other check on the declaration.
    2. `battle.declaration.back_stab_no_ability`: the member's class has no `back_stab` ability.
    3. `battle.declaration.back_stab_target_aware`: the monsters aren't surprised this round.
       That's every round of a battle the party didn't open by surprising the monsters, and
       every round after the free round.
    4. The checks of an ordinary attack on its group and weapon:
       `battle.declaration.unknown_group`, `battle.declaration.no_target`, and
       `battle.declaration.weapon_not_wielded`.
    5. `battle.declaration.back_stab_not_melee`: the attack the session would resolve is a
       missile attack. That's a missile weapon like a bow, or a weapon that's both melee and
       missile, like a dagger, aimed at a group more than 5' from the member after their
       `close`.
    6. The rest of an ordinary melee attack's checks, like
       `battle.declaration.not_in_front_rank` and `combat.attack.out_of_reach`, judged after the
       round's declared moves.

    A declaration that leaves this field `False` resolves exactly as it would without it.
    [The adaptations register](https://mmacy.github.io/osrlib-python/adaptations/) describes the
    back-stab in full."""


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
    into a pursuit, and to `game_over` when every party member is dead or turned to stone.

    The session accepts or refuses a round as a whole. It checks every declaration before
    it resolves any of them, against where the members will stand once the moves the round
    declares are made. If a single declaration is bad, the session refuses the command, and
    the result lists every rejection found. A refused round costs nothing: no random number,
    no game time, and no change to the session. Fix the declarations and send the round
    again. To check declarations before you send them, call
    [`preview_battle_round`][osrlib.crawl.battle.preview_battle_round], which runs the same
    checks without playing the round.

    Modes:
        `battle`

    Rejections:
        The round as a whole:

        - `session.command.wrong_mode` or `battle.none_active` - no battle is underway.
        - `battle.declaration.roster_mismatch` - the declarations don't name exactly the
          members in `EncounterView.declarers`, each once. The session checks this first
          and reports nothing else when it fails.
        - `battle.declaration.unknown_action` - an `action` the session doesn't
          recognize. Constructing a `BattleDeclaration` with one already fails validation.

        Each declaration, by its `action`:

        - Any action: `battle.declaration.no_mirror` when the member declares a
          `gaze_defense` of `mirror` and carries no mirror. The rejection has the member's
          id as its `character` param.
        - Move declarations: `battle.declaration.missing_move`,
          `battle.declaration.unknown_group`, and `battle.declaration.cannot_move`.
        - A `move` set on an action that can't take it: `battle.declaration.cast_cannot_move`
          on a `cast`, and `battle.declaration.move_not_combinable` on a `turn_undead`, a
          `use_item`, or a `hold`, and on an `attack` with a `fighting_withdrawal` or a
          `retreat`. Each has the member's id as its `character` param. The session checks
          for them after the back-stab checks and before the action's own checks.
        - `battle.declaration.replacement_unavailable` when a member who makes a
          `fighting_withdrawal` or a `retreat` alone names a member in `replacement_id` who
          isn't a declarer, is the withdrawing member, is withdrawing too, or was named by a
          withdrawing member earlier in marching order. It also comes back for a declaration
          in a formation move that sets `replacement_id`, because every declarer moves with
          the formation. It has the withdrawing member's id as its `character` param and the
          named id as `replacement`.
        - Attack declarations: `battle.declaration.unknown_group`,
          `battle.declaration.no_target`, `battle.declaration.weapon_not_wielded`,
          `battle.declaration.cannot_move` for an attack that sets `move="close"` by a member
          who can't move, `battle.declaration.not_in_front_rank` for a melee attack by a
          member who won't stand in the group's front rank once the round's moves are made,
          and the checks of [`validate_attack`][osrlib.core.combat.validate_attack]:
          `combat.attack.out_of_reach`, `combat.attack.out_of_range`,
          `combat.attack.reload`, `combat.attack.attacker_incapacitated`, and
          `combat.attack.attacker_blind`. The session judges an attack at the member's own
          distance to the group, after the member's `close` if the declaration sets one, and
          `combat.attack.out_of_reach` has that distance as its `distance_feet` param.
        - A declaration that sets `back_stab`: `battle.declaration.back_stab_not_attack` on
          any action but `attack`, before any other check. On an attack,
          `battle.declaration.back_stab_no_ability` and
          `battle.declaration.back_stab_target_aware` before the attack checks, and
          `battle.declaration.back_stab_not_melee` after the group and weapon checks and
          before the melee checks. A back-stab gets only the first of these that applies,
          and each has the member's id as its `character` param.
          [`BattleDeclaration.back_stab`][osrlib.crawl.commands.BattleDeclaration.back_stab]
          gives the order in full.
        - Cast declarations: `battle.declaration.missing_spell`,
          `battle.declaration.unknown_group`,
          `battle.declaration.invisible_target`, and the cast checks
          `magic.cast.unknown_spell`, `magic.cast.silenced_area`,
          `magic.cast.unknown_mode`, `magic.cast.unknown_target`,
          `magic.cast.not_memorized`, `magic.cast.caster_incapacitated`,
          `magic.cast.caster_restrained`, `magic.cast.anti_magic_shell`,
          `magic.cast.not_reversible`, `magic.cast.target_count`,
          `magic.cast.out_of_range`.
        - Turn-undead declarations: `magic.turning.not_a_turner` and
          `magic.turning.caster_incapacitated`. A turning also gets
          `battle.declaration.no_target` when no living monster is left in any group that
          hasn't fled. That never refuses a round, because a battle always has a living
          monster when the round starts. The session checks the turning again just before it
          resolves, and a turning that gets `no_target` then fails and has no effect.
        - Item declarations: `battle.declaration.item_unusable`,
          `battle.declaration.unknown_group`, `battle.declaration.no_target`,
          `items.use.not_usable`, `items.device.inert`, `items.scroll.spent`,
          `items.scroll.no_such_spell`, `items.scroll.wrong_caster`,
          `exploration.action.requires_light`, and `combat.attack.out_of_reach`. A staff of
          striking is a melee weapon, so its use also gets
          `battle.declaration.not_in_front_rank` when the member won't stand in the group's
          front rank once the round's moves are made. A scroll read can also get the cast
          checks on its spell and targets (`magic.cast.unknown_mode`, `magic.cast.unknown_target`,
          `battle.declaration.invisible_target`, `magic.cast.target_count`,
          `magic.cast.out_of_range`, `magic.cast.caster_incapacitated`,
          `magic.cast.caster_restrained`, `magic.cast.anti_magic_shell`). A thrown item
          can also get the `validate_attack` checks listed for attack declarations.

    Events:
        Opening the round:
        [`BattleRoundEvent`][osrlib.crawl.events.BattleRoundEvent], a
        [`SpellDeclaredEvent`][osrlib.crawl.events.SpellDeclaredEvent] per declared
        cast, and
        [`InitiativeRolledEvent`][osrlib.core.events.InitiativeRolledEvent], which reports
        which side acts first.

        Movement: a [`MemberMovedEvent`][osrlib.crawl.events.MemberMovedEvent] each time a
        member moves by themselves, a
        [`MemberWithdrewEvent`][osrlib.crawl.events.MemberWithdrewEvent] each time a member
        who falls back alone trades places in the marching order with the member they named,
        and a [`GroupMovedEvent`][osrlib.crawl.events.GroupMovedEvent] each time a group's
        distance from the party line changes, when monsters close or run, and when the whole
        party moves in a formation move.

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
        [`DeclarationFizzledEvent`][osrlib.core.events.DeclarationFizzledEvent] when a
        device use or a turning fails the check the session makes just before it resolves,
        and at `battle.melee.fizzled` when a melee attack can't resolve because of where the
        member stands when it comes up,
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
        fails its morale check and flees, and
        [`MonstersLeftBehindEvent`][osrlib.crawl.events.MonstersLeftBehindEvent]
        when a fleeing group leaves behind members who can't move.

        Closing the round: the clock advances one round, and these events report what
        that changes:
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
        or [`GameOverEvent`][osrlib.crawl.events.GameOverEvent] when every party member is
        dead or turned to stone.

    Examples:
        A fighter and a cleric meet three goblins 30 feet away. Each round, each member in
        `declarers` plans an attack with a `close`. A member out of reach closes first, and a
        member already in reach doesn't move and attacks where they stand. When the preview
        refuses that plan, because the member can't reach the goblins this round or the front
        rank toward them is full, the member closes without attacking:

        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.events import Visibility
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.battle import preview_battle_round
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

        while session.mode is SessionMode.BATTLE:
            encounter = session.view(Visibility.PLAYER).encounter
            target = next(group for group in encounter.groups if group.count and not group.fled)
            planned = []
            for member_id in encounter.declarers:
                # With no weapon_id, the member attacks with the wielded weapon that suits the attack.
                declaration = BattleDeclaration(
                    character_id=member_id, action="attack", move="close", target_group_id=target.id
                )
                if preview_battle_round(session, [*planned, declaration]).rejections[member_id]:
                    declaration = BattleDeclaration(
                        character_id=member_id, action="move", move="close", target_group_id=target.id
                    )
                planned.append(declaration)
            result = session.execute(ResolveBattleRound(declarations=tuple(planned)))
            if not result.accepted:
                # Nothing happened. A front end shows the reasons and asks for new declarations.
                print([rejection.code for rejection in result.rejections])
                break

        assert session.battle is None  # the fight is over, won or lost
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

    Use it to hand out a reward, a found item, a starting kit, or a potion the party buys
    from a shop your game runs. The command charges nothing. To charge a price, also send
    [`RemoveCoins`][osrlib.crawl.commands.RemoveCoins]. This command is legal in every mode,
    `game_over` and `victory` included.

    The session creates a magic item the same way it creates treasure. The item arrives
    unidentified.
    [`instantiate_magic_item`][osrlib.core.treasure.instantiate_magic_item] rolls whatever the
    item's page leaves to chance, like a wand's charges, on
    [`TREASURE_STREAM`][osrlib.core.treasure.TREASURE_STREAM]. A mundane grant makes no draws.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        - `session.command.unknown_member` - `character_id` matches no party member.
        - `session.command.unknown_item` - `item_id` matches no item in the session's
          [`effective_equipment`][osrlib.crawl.session.GameSession.effective_equipment]
          catalog or the magic item catalog.

    Events:
        [`ItemAcquiredEvent`][osrlib.crawl.events.ItemAcquiredEvent] with `origin`
        `"grant"`. Its `item_ids` list a mundane item's catalog id once for each unit, and
        each new magic item's instance id.
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
    """Which item to create, by id. The session looks first in its
    [`effective_equipment`][osrlib.crawl.session.GameSession.effective_equipment] catalog. That
    catalog has the built-in ids from [`load_equipment`][osrlib.data.load_equipment] (see the
    [equipment id index](https://mmacy.github.io/osrlib-python/reference/content-ids/equipment/))
    and the ids the adventure bundles. Then it looks in the magic item catalog from
    [`load_magic_items`][osrlib.data.load_magic_items] (see the
    [magic item id index](https://mmacy.github.io/osrlib-python/reference/content-ids/magic-items/))."""
    quantity: int = Field(default=1, ge=1)
    """How many to place, counted as single items rather than as purchase lots.
    `quantity=6` grants six torches, the same number one lot of torches from
    [`PurchaseEquipment`][osrlib.crawl.commands.PurchaseEquipment] delivers. For a magic
    item, it's the number of separate magic items to create, each with its own instance id
    and its own rolls. `quantity=2` of a bundle of arrows +1 grants two bundles."""


class GrantCoins(Command):
    """Referee: put coins straight into a member's purse.

    Use it to pay out a reward. This command is legal in every mode, `game_over` and
    `victory` included.

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


class RemoveItem(Command):
    """Referee: take an item away from a member, the inverse of [`GrantItem`][osrlib.crawl.commands.GrantItem].

    Use it when your game takes something the party has: armour a shop buys back, a quest
    item used up when the quest completes, or an offering left on an altar. The item is
    destroyed, not dropped, so the party can't pick it back up. When your game pays for the
    item, also send [`GrantCoins`][osrlib.crawl.commands.GrantCoins]. This command is legal in
    every mode, `game_over` and `victory` included.

    Name the item in `item_id`, by one of two kinds of id. A mundane item, like a torch, has a
    catalog id, and a member can carry several units of it. Set `quantity` to the number of units
    to take. A magic item or a valuable has an instance id, which names one item, so `quantity`
    stays 1.

    The session takes an equipped item off before it removes it. Any effect the item attached
    ends, as it would with [`UnequipItem`][osrlib.crawl.commands.UnequipItem]. A revealed
    curse doesn't stop the removal, because a curse stops only the bearer from taking the item
    off. The command makes no draws and takes no time.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        - `session.command.unknown_member` - `character_id` matches no party member.
        - `items.remove.not_carried` - the member doesn't carry `quantity` units of
          `item_id`, or `item_id` is an instance id and `quantity` isn't 1.

    Events:
        [`ItemsRemovedEvent`][osrlib.crawl.events.ItemsRemovedEvent] with the removed
        items. When an equipped magic item's effects end, their events come first.
    """

    command_type: Literal["remove_item"] = "remove_item"
    character_id: str
    """The member who loses the item, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. A command you issue yourself has a
    member id here, and the session refuses any other value with `session.command.unknown_member`.
    A consequence or a reward in an adventure document has a selector instead, because the
    session assigns member ids when it starts. `"@party"` takes the item from every living
    member, `"@first"` from the first living member, and `"@carrier"` from the first living
    member in marching order who carries it. The trigger and quest interpreter replaces the
    selector with member ids before it issues the command."""
    item_id: str
    """Which item to take. A mundane item's catalog id, like `"torch"`, takes units from the
    member's pack first and then from what the member has equipped. A magic item's or a
    valuable's instance id, like `"magic-item-0003"` or `"valuable-0002"`, takes that one
    item. The session refuses a magic item's catalog id with `items.remove.not_carried`,
    because two copies of a magic item can differ. Read instance ids from the member's
    inventory in the referee view."""
    quantity: int = Field(default=1, ge=1)
    """How many units of a mundane item to take. It has to be 1 for an instance id."""


class RemoveCoins(Command):
    """Referee: take coins out of a member's purse, the inverse of [`GrantCoins`][osrlib.crawl.commands.GrantCoins].

    Use it to charge a price or a toll. The session takes exactly the coins you name. It
    doesn't make change between denominations, so a member who has gold but no silver can't
    pay a toll in silver. Convert the price to coins the member has before you send the
    command. This command is legal in every mode, `game_over` and `victory` included. It makes no draws
    and takes no time.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        - `session.command.unknown_member` - `character_id` matches no party member.
        - `items.coins.insufficient` - the purse has fewer coins of some denomination than
          `coins` asks for.

    Events:
        [`ItemsRemovedEvent`][osrlib.crawl.events.ItemsRemovedEvent] with the coins' value.
    """

    command_type: Literal["remove_coins"] = "remove_coins"
    character_id: str
    """The member whose purse pays, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. A command you issue yourself has a
    member id here, and the session refuses any other value with `session.command.unknown_member`.
    A consequence or a reward in an adventure document has a party selector instead,
    `"@party"` for every living member or `"@first"` for the first living member, because the
    session assigns member ids when it starts. The trigger and quest interpreter replaces the selector
    with member ids before it issues the command."""
    coins: Coins
    """The coins to take, by denomination. The purse must have at least this many coins of each
    denomination."""


class AddMember(Command):
    """Referee: add a character to the party during play.

    Use it to bring a new character into a running game: a replacement for a character who
    died, a prisoner the party set free, or an adventurer the party meets in town. Build the
    character with [`create_character`][osrlib.core.character.create_character], the way you
    build a starting party member, and send it here in `town` or `exploring`.

    The session gives the new member an id and puts them at the back of the marching order, so
    the front rank doesn't change. To move them, send
    [`ReorderParty`][osrlib.crawl.commands.ReorderParty], which is legal in the same modes. The
    session logs the command, so a saved game and a replay both have the new member.

    The new id has the form `character-NNNN` and is unique in the party. It's never the id of a
    member who left with [`RemoveMember`][osrlib.crawl.commands.RemoveMember]. Read it from the
    event, and pass it as the `character_id` of later commands. The session adds a copy of
    `character`, not the object you pass. Your object stays as it was, with no id. Read the new
    member with [`GameSession.member`][osrlib.crawl.session.GameSession.member].

    The character must be fresh:

    - The character can't have a condition or a stat modifier, `dead` included. Each one comes
      from an effect in the game that applied it, and this session can't end an effect it
      doesn't track.
    - The character can't carry a magic item or a valuable. Each has an instance id from the
      game that created it, which could match an id this session hands out. Mundane gear and
      coins are fine. Give magic items afterwards with
      [`GrantItem`][osrlib.crawl.commands.GrantItem], which creates them with this session's
      ids.

    The coins the new member brings earn no experience, because the party didn't find them as
    treasure. Adding a member makes no draws and takes no time.

    The session refuses the command during an encounter or a battle, because the fight is
    already set up for the party it started with. It also refuses the command once the session
    has ended, because a new member would restart play. To replace a party whose members are
    all dead or turned to stone, first send [`PlaceParty`][osrlib.crawl.commands.PlaceParty] to move it to town, which
    takes the session out of `game_over`. Then add the new character.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` - an encounter or a battle is open, or the session
          has ended.
        - `party.add.id_assigned` - the character's `id` isn't `None`. The character has an
          id from another game, or is already a member.
        - `party.add.under_effects` - the character has a condition, like `dead` or
          `asleep`, or a stat modifier.
        - `party.add.carries_instances` - the character carries a magic item or a valuable,
          equipped or not.

        The session checks in this order and reports the first that fails.

    Events:
        [`MemberAddedEvent`][osrlib.crawl.events.MemberAddedEvent] with the new member's id,
        name, class, and level.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["add_member"] = "add_member"
    character: Character
    """The character who joins. Build it with
    [`create_character`][osrlib.core.character.create_character] and pass its `character`. It
    needs `id` set to `None`, no conditions or stat modifiers, and no magic items or valuables.
    Constructing the command copies the character. A change you make
    to your object afterwards doesn't reach the command, and the command log records the
    character as you sent it."""

    @field_validator("character", mode="after")
    @classmethod
    def _own_the_character(cls, value: Character) -> Character:
        return value.model_copy(deep=True)


class RemoveMember(Command):
    """Referee: take a member out of the party for good.

    Use it when a character leaves play: one who died too long ago for *raise dead*, a hireling
    who quits, or a character the player retires. To bring in a replacement, send
    [`AddMember`][osrlib.crawl.commands.AddMember] as well. The member can be in any state:
    alive, dead, turned to stone, or asleep. The other members keep their marching order.

    Name another member in `give_to` to keep what the leaving member carries in the party.
    Every item and all the coins go to that member: mundane gear, magic items, gems, and
    jewellery. Equipped items arrive unequipped in their pack, and an equipped magic item's
    effects end, as with [`GiveItems`][osrlib.crawl.commands.GiveItems]. A cursed item moves
    too, even when its curse is revealed, because the curse holds only its wearer, and the wearer
    has left. It arrives with its curse hidden again, as *remove curse* leaves an item, so the
    new owner can drop it or give it away. If they put it on, the curse is revealed again.
    Without `give_to`, everything the member carries leaves the game with them.

    Without `give_to`, the session refuses to remove a member who carries an item the adventure
    depends on, because the party might then never open a door or finish a quest. The adventure
    depends on an item when its catalog id appears in a
    [`HasItemCondition`][osrlib.crawl.gates.HasItemCondition] or an
    [`ItemAcquiredPattern`][osrlib.crawl.triggers.ItemAcquiredPattern] anywhere in the adventure.
    That means the gate of a door, a stair, or a passage, a trigger, or any clause of a quest. The
    session matches ids the way a `HasItemCondition` does. Mundane items, magic items, and the
    adventure's own items all count, equipped or not. An item counts even when another member
    carries the same item too. When the session refuses, send the command again and name a
    member in `give_to` to take everything, the item included.

    Some parts of an adventure can't matter again, and they stop protecting their items. A quest
    that [`GameSession.quests`][osrlib.crawl.session.GameSession.quests] shows as `completed`
    protects nothing. A trigger protects nothing once it has fired, which means its id is in
    `GameSession.fired_triggers`, unless it's `repeatable`. A quest that is still open protects
    the items in all its clauses, even an objective that is already complete. A gate always
    protects its item, because the party can come back to it.

    The session also drops what it tracks about the member. The effects on them end without
    events, and their death record, their food and water counts, and the record of when they
    last prepared spells are deleted. An effect the member cast on another member lasts its full
    duration. A light the member carried goes out with them. A burning torch leaves with its
    bearer, and a lantern given to `give_to` arrives unlit. Read
    [`GameSession.party_light`][osrlib.crawl.session.GameSession.party_light] afterwards to find
    out whether the party still has light.

    The member's id is retired. The session never gives it to another member, so an old event
    that names it can't be mistaken for a new member. A replacement that joins with `AddMember`
    gets a new id. The session refuses the old id in later commands with
    `session.command.unknown_member`.

    In a dungeon, the coins and valuables that leave with the member come off
    [`treasure_snapshot_cp`][osrlib.crawl.session.GameSession.treasure_snapshot_cp], so the
    departure doesn't change the experience the party earns for treasure when it comes back to
    town. With `give_to`, nothing leaves the party, and the snapshot stays as it was.

    Removing a member makes no draws and takes no time. The command is legal only in `town` and
    `exploring`, like `AddMember`. The session never removes the last member who stands, which
    means one who is neither dead nor turned to stone, because a party with nobody standing has
    lost the game. To replace a player's last standing character, add the new character with
    `AddMember` first, and then remove the old one.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` - an encounter or a battle is open, or the session
          has ended.
        - `session.command.unknown_member` - `character_id` or `give_to` matches no party
          member.
        - `party.remove.same_member` - `give_to` names the member who leaves.
        - `session.command.member_incapacitated` - the `give_to` member can't act, because
          they're dead, asleep, paralysed, or turned to stone.
        - `party.remove.last_standing` - no member would be left standing. That includes
          removing the only member.
        - `party.remove.carries_quest_item` - `give_to` is `None`, and the member carries an
          item the adventure depends on. The rejection includes the member's id as `character`
          and the item's catalog id as `item`. When the member carries more than one such item,
          it names the first in the order that
          [`Inventory.all_instances`][osrlib.core.items.Inventory.all_instances] lists them.

        The session checks in this order and reports the first that fails.

    Events:
        With `give_to`, an [`EffectReleasedEvent`][osrlib.core.events.EffectReleasedEvent]
        for each effect of an equipped magic item, then an
        [`ItemsGivenEvent`][osrlib.crawl.events.ItemsGivenEvent] with what moved, when
        anything did. Then a [`MemberRemovedEvent`][osrlib.crawl.events.MemberRemovedEvent]
        with the member's id and name.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["remove_member"] = "remove_member"
    character_id: str
    """The member who leaves, by [`MemberView.id`][osrlib.crawl.views.MemberView.id]. They can
    be alive, dead, asleep, or turned to stone."""
    give_to: str | None = None
    """The member who takes everything the leaving member carries, by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id]. Leave it `None` to let it all leave the
    game with the member. The session refuses `None` when the member carries an item the
    adventure depends on. The member named here has to be able to act."""


class AwardXP(Command):
    """Referee: give one character experience points, apart from the XP the session awards.

    Use it for a quest reward or for any other XP you grant. This command is legal in
    every mode, `game_over` and `victory` included, so you can grant an adventure's rewards after the
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

    A flag is a named value you store in the session. This command is legal in
    every mode, `game_over` and `victory` included. Flags are saved with the session. Your code and
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
    encounters take place on the dungeon grid. The mode check accepts `town`, but the session
    then refuses the command with `session.command.not_in_dungeon`. The command isn't legal
    once the session has ended, because it would start new play. Set exactly one of `count_dice` and
    `count_fixed`, or constructing the command fails validation.

    When no encounter is open, the command opens one with the monsters. When an encounter
    or a battle is open, the monsters join it as one new group at `distance_feet`:

    - The group rolls no surprise and no reaction. It shares the encounter's surprise and its
      stance, which is the monsters' reaction to the party. So when the party surprised the
      monsters already there, and they can't act for a round, the new group can't act either.
    - The command takes no time and runs no round. The group first acts in the next round,
      when the monster side acts.
    - The session refuses the command during a pursuit, because a pursuit runs at the pace of
      its slowest group, and a new group could change that pace.

    Modes:
        `town`, `exploring`, `encounter`, `battle`

    Rejections:
        - `session.command.wrong_mode` - the session has ended.
        - `session.command.unknown_monster` - `template_id` matches no monster.
        - `session.command.not_in_dungeon` - the party is not on a dungeon cell.
        - `encounter.spawn.mid_pursuit` - the monsters are pursuing the party, and no
          new group can join during a pursuit.

    Events:
        [`MonstersSpawnedEvent`][osrlib.crawl.events.MonstersSpawnedEvent] comes first.

        When the command opens an encounter, the encounter's opening follows: an
        [`EffectAttachedEvent`][osrlib.core.events.EffectAttachedEvent] for each monster that
        regenerates, like a troll,
        [`SurpriseRolledEvent`][osrlib.crawl.events.SurpriseRolledEvent]s,
        [`EncounterStartedEvent`][osrlib.crawl.events.EncounterStartedEvent], the
        reaction roll, and a
        [`StanceChangedEvent`][osrlib.crawl.events.StanceChangedEvent]. When the monsters'
        reaction is to attack, battle opens at once.

        When the monsters join an open encounter, an `EffectAttachedEvent` for each monster
        that regenerates follows instead, then an
        [`EncounterGroupJoinedEvent`][osrlib.crawl.events.EncounterGroupJoinedEvent], which
        the players see. When the group joins a battle and its morale score is 2, a
        [`MonsterFledEvent`][osrlib.crawl.events.MonsterFledEvent] comes last, because a
        monster with that score won't fight.
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

    Set `count_fixed` for a band of an exact size, `count_dice` to roll the size, or neither
    to roll the B/X number for `party_kind`. Setting both fails validation.

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
    [`ENCOUNTER_STREAM`][osrlib.crawl.session.ENCOUNTER_STREAM]. A malformed expression raises
    when the command is constructed. A roll below 1 counts as 1. Don't set it together with
    `count_fixed`.

    When both this and `count_fixed` are `None`, the default, the engine rolls the B/X number
    for `party_kind`: 1d4+4 for `basic` and 1d6+3 for `expert`."""
    count_fixed: int | None = Field(default=None, ge=1)
    """How many adventurers, as an exact number. Use it when the adventure's author chose the
    size of the band. The session rolls nothing for the size. Don't set it together with
    `count_dice`."""
    distance_feet: int = Field(ge=0)
    """How far away the NPC party appears, in feet, on the same terms as
    [`SpawnMonsters.distance_feet`][osrlib.crawl.commands.SpawnMonsters.distance_feet]."""

    @field_validator("count_dice")
    @classmethod
    def _dice_must_parse(cls, value: str | None) -> str | None:
        if value is not None:
            parse(value)
        return value

    @model_validator(mode="after")
    def _dice_or_fixed(self) -> SpawnNpcParty:
        if self.count_dice is not None and self.count_fixed is not None:
            raise ValueError("set at most one of count_dice or count_fixed")
        return self


class EndEncounter(Command):
    """Referee: close the open encounter when the party talks its way past or walks away.

    Use it when the party pays a toll, bluffs the guards, or walks away from monsters that let
    it go. The session closes the encounter the same way it does when the party wins or gets
    away, and then switches to `exploring`:

    - Monsters that were slain earlier in the encounter, or that fled, are still fleeing, or
      were turned, count as defeated and are worth experience.
    - Slain monsters drop their treasure. Monsters that fled or were turned take theirs with
      them.
    - Monsters still standing aren't defeated and are worth no experience.
    - Effects on the encounter's monsters end.
    - The clock advances, so the encounter costs at least one turn.

    In `battle`, the command closes the battle too. It emits no
    [`BattleEndedEvent`][osrlib.crawl.events.BattleEndedEvent], because the
    `EncounterEndedEvent` closes both. To know the fight is over, check the session's mode.

    The monsters keyed to an area of the adventure come back each time the party walks into
    the area, until every one of them is slain or driven off. To stop them coming back, set
    `resolve_area` to `True`, which marks the area resolved. The command is legal only while
    an encounter is open, so you can't use it to resolve an area after its encounter has
    closed.

    Set `outcome` to `"parley"` when the two sides came to terms, or `"departed"` when one side
    left the other. It has no default.

    An adventure document can contain this command as an authored consequence, so a trigger
    can close the encounter. For example, a trigger can close the encounter and resolve the
    area when a flag records that the party paid the toll.

    Modes:
        `encounter`, `battle`

    Rejections:
        - `session.command.wrong_mode` - no encounter is open.

    Events:
        A [`MonsterDefeatedEvent`][osrlib.crawl.events.MonsterDefeatedEvent] for each monster
        that counts as defeated, the events of effects ending on the
        encounter's monsters, and the experience award when the ruleset awards it immediately.
        Then the [`EncounterEndedEvent`][osrlib.crawl.events.EncounterEndedEvent] with the
        command's `outcome`, and the events of the clock advance.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.ENCOUNTER, SessionMode.BATTLE})

    command_type: Literal["end_encounter"] = "end_encounter"
    outcome: Literal["parley", "departed"]
    """How the encounter ended, as the `outcome` of the
    [`EncounterEndedEvent`][osrlib.crawl.events.EncounterEndedEvent]. Use `"parley"` when the
    party and the monsters came to terms, like a toll paid or a bluff believed. Use
    `"departed"` when one side left the other, like a party that walks away from monsters
    that let it go, or a pursuit that you call off."""
    resolve_area: bool = False
    """Whether to mark the area the encounter came from as resolved, so its keyed monsters don't
    appear when the party next walks in. With `False`, the default, the area stays unresolved,
    and its monsters are back the next time the party enters it. A wandering or spawned
    encounter didn't come from an area, so the flag does nothing for it."""


class SetDoorState(Command):
    """Referee: change the state of a door on any level of any dungeon.

    Use it when your game logic opens, closes, reveals, or unlocks a door, like a
    lever that opens a portcullis. The door in the adventure document never changes. What
    changes is its state in play: whether it stands open, is wedged, has been found, and has
    been unlocked. A field you leave `None` leaves the current value unchanged. The door doesn't have
    to be next to the party. This command is legal in every mode, `game_over` and
    `victory` included.

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
    referee's "an hour later". This command is legal in every mode, `game_over` and
    `victory` included, so the clock keeps running after the party dies. The time limits on spells
    that bring the dead back are measured on this clock. Everything that happens with time
    happens as usual, like effects expiring and provisions eaten each day. The engine makes no
    wandering-monster checks for it, because the referee decides on encounters, and the time
    doesn't count toward the party's rest.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`, `victory`

    Rejections:
        None.

    Events:
        The events of the time passing: effects that expire, a
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
    This command is legal in every mode, `game_over` and `victory` included. The session draws the roll
    from the separate
    [`ADJUDICATION_STREAM`][osrlib.crawl.session.ADJUDICATION_STREAM], so a referee's roll
    never changes what any other stream draws. A malformed `expression` raises when the
    command is constructed, as a malformed `count_dice` on
    [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters] does, so it never reaches the
    session and makes no random roll.

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
    load, and replay like the rest of the session's state. This command is legal in
    every mode, `game_over` and `victory` included.

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
    were written. This command is legal in every mode, `game_over` and `victory` included, so you
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
    and why. This command is legal in every mode, `game_over` and `victory` included.

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
    This command is legal in every mode, `game_over` and `victory` included.

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
    because the objective's event already includes the text. This command is legal in
    every mode, `game_over` and `victory` included.

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
    This command is legal in every mode, `game_over` and `victory` included.

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
    the session emits no `AdventureCompletedEvent`. This command is legal in every mode,
    `game_over` and `victory` included.

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
    EndEncounter,
    RemoveItem,
    RemoveCoins,
    AddMember,
    RemoveMember,
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
    EndEncounter,
    RemoveItem,
    RemoveCoins,
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
- [`AddMember`][osrlib.crawl.commands.AddMember] takes a whole character, and an adventure
  document doesn't contain characters.
- [`RemoveMember`][osrlib.crawl.commands.RemoveMember] takes a player's character out of the
  game, which is a decision for your game, not for an adventure. It can also name a member to
  take the leaving member's goods, and no selector can name that member.

In an authored consequence, the `character_id` of a grant, a removal, or an award is a
selector, never a member id, because the session assigns member ids when it starts. The
selectors are `"@party"` and `"@first"`, and a [`RemoveItem`][osrlib.crawl.commands.RemoveItem]
can also use `"@carrier"`. See [`osrlib.crawl.triggers`][osrlib.crawl.triggers] for the
selectors."""

ConsequenceCommand = Annotated[
    GrantItem
    | GrantCoins
    | AwardXP
    | SetFlag
    | SpawnMonsters
    | SpawnNpcParty
    | SetDoorState
    | PlaceParty
    | AdvanceTime
    | EndEncounter
    | RemoveItem
    | RemoveCoins,
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
