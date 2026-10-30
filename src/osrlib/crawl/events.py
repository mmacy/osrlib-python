"""The crawl event catalog: typed records of everything a session does.

Every command you run through
[`GameSession.execute`][osrlib.crawl.session.GameSession.execute] comes back with a
[`CommandResult`][osrlib.crawl.commands.CommandResult] whose `events` tuple contains
instances of the classes here and of the kernel classes in
[`osrlib.core.events`][osrlib.core.events]. Read them in order. Each event's `visibility`
specifies whether the players may see it or only the referee may, so leave out the referee-only
ones when you show them to a player. Turn each one into a line with
[`format_message`][osrlib.messages.format_message] or with a renderer of your own that
switches on the event's `code`. The session also appends the same objects to
`GameSession.event_log`, and [`save_game`][osrlib.persistence.save_game] writes that log
into a save.

Every event has a `code`, an `event_type`, and a `visibility`:

- The code identifies what happened, like `exploration.door.opened`: two or more snake_case
  parts separated by dots, where the first is the part of the rules the event comes from. A
  renderer picks its message by the code.
- The event type identifies the class in serialized form, so a stored event rebuilds into the
  right class.
- The visibility is who may see the event. `player` is for what the players learn.
  `referee` is for the rolls and bookkeeping the referee keeps hidden from them, like a
  detection die or a wandering-monster check.

A class that can report more than one outcome lists all its codes in `allowed_codes`,
and each instance has one of them.

An event never contains English prose written by the engine. It contains facts and a
code, so you can translate the line, and a narrator can write a different line from the
same facts. The exception is a `narrative` field, which contains text the adventure's
author wrote.

[`CRAWL_EVENT_CLASSES`][osrlib.crawl.events.CRAWL_EVENT_CLASSES] lists the classes in
this module, and [`ALL_EVENT_CLASSES`][osrlib.crawl.events.ALL_EVENT_CLASSES] puts the
kernel classes in front of them. [`AnyEvent`][osrlib.crawl.events.AnyEvent] is the union
of every event class, for type hints and JSON Schema.
[`parse_any_event`][osrlib.crawl.events.parse_any_event] turns a serialized record back
into an event.

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
print([event.code for event in result.events])
# ['exploration.party.moved']
table = [format_message(event) for event in result.events if event.visibility is Visibility.PLAYER]
print(table)
# ['The party moves to (1, 0), facing east.']
```
"""

from collections.abc import Mapping
from functools import cache
from typing import Annotated, ClassVar, Literal, Union

from pydantic import Field, TypeAdapter, ValidationError

from osrlib.core.events import KERNEL_EVENT_CLASSES, Event, Visibility

__all__ = [
    "ALL_EVENT_CLASSES",
    "AdventureCompletedEvent",
    "AdventureXpAwardEvent",
    "AnyEvent",
    "BattleEndedEvent",
    "BattleRoundEvent",
    "BattleStartedEvent",
    "CRAWL_EVENT_CLASSES",
    "CharacterLeveledUpEvent",
    "CurseRevealedEvent",
    "DetectionRolledEvent",
    "DiceRolledEvent",
    "DoorEvent",
    "EncounterEndedEvent",
    "EncounterGroupJoinedEvent",
    "EncounterStartedEvent",
    "EvasionEvent",
    "ExhaustionEvent",
    "FatigueEvent",
    "FlagSetEvent",
    "GameOverEvent",
    "GroupMovedEvent",
    "HealingPurchasedEvent",
    "HoardGeneratedEvent",
    "ItemAcquiredEvent",
    "ItemConsumedEvent",
    "ItemIdentifiedEvent",
    "ItemUsedEvent",
    "ItemsDroppedEvent",
    "ItemsGivenEvent",
    "ItemsLeftBehindEvent",
    "ItemsRemovedEvent",
    "JournalEntryAddedEvent",
    "LightEvent",
    "ListenedEvent",
    "LocationEnteredEvent",
    "MonsterDefeatedEvent",
    "MonsterFledEvent",
    "MonstersLeftBehindEvent",
    "MonstersSpawnedEvent",
    "NoteRecordedEvent",
    "NpcPartySpawnedEvent",
    "ObjectiveCompletedEvent",
    "ObjectiveRevealedEvent",
    "PartyMovedEvent",
    "ProvisionsEvent",
    "PursuitEvent",
    "QuestActivatedEvent",
    "QuestCompletedEvent",
    "RestedEvent",
    "SearchCompletedEvent",
    "SpellDeclaredEvent",
    "StanceChangedEvent",
    "SurpriseRolledEvent",
    "TimeAdvancedEvent",
    "TrapEvent",
    "TreasureSoldEvent",
    "TriggerFiredEvent",
    "WanderingCheckEvent",
    "XpAwardedEvent",
    "parse_any_event",
]


class PartyMovedEvent(Event):
    """The party moved a cell or turned in place, with its new cell and facing.

    Emitted by [`MoveParty`][osrlib.crawl.commands.MoveParty] with the cell it
    stepped into, and by [`TurnParty`][osrlib.crawl.commands.TurnParty] with the
    unchanged cell and the new facing. For a `MoveParty` through a passage, the event has
    the cell the party arrives on, and it comes after the
    [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent] when the passage
    leads to another level. A [`UseStairs`][osrlib.crawl.commands.UseStairs] that takes the
    party to another cell of the same level emits one for the landing cell only when the
    transition's gate has success text, which the event then contains in `narrative`.
    Redraw a first-person view from this event.

    A step that a wall, a closed door, or the edge of the map blocks emits no event. The
    session refuses the command with the rejection code `exploration.move.blocked`, and
    nothing in the game changes.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.party.moved", "exploration.party.turned"})
    """`exploration.party.moved` for a step into a new cell, `exploration.party.turned` for a
    turn on the spot."""

    event_type: Literal["party_moved"] = "party_moved"
    """The event type, `party_moved`, which identifies this class in serialized form."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party knows where it stands."""
    x: int
    """The column the party is in after the step or turn, counting from zero at the level's west edge."""
    y: int
    """The row the party is in after the step or turn, counting from zero at the level's north edge."""
    facing: str
    """The direction the party now faces, as the lowercase value of a
    [`Direction`][osrlib.crawl.dungeon.Direction] (`"north"`, `"east"`, `"south"`, `"west"`). After
    a step, the party faces the way it went, so this changes on a step as well as on a turn. After
    a step through a passage, the party faces the way the adventure's author set for the passage's
    far side."""
    narrative: str | None = None
    """The success text the adventure's author wrote on the gate of a transition that took the party to
    another cell of the same level, or `None`. For a transition to another level, the text is on the
    [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent] instead.
    [`format_message`][osrlib.messages.format_message] adds it after the line it builds from the
    code."""


class LocationEnteredEvent(Event):
    """The party crossed into a new area, level, dungeon, or town.

    An area is a room or other region of a level that the adventure's author keyed with
    content. The session emits the event whenever the party's location changes at one of
    those four scales:

    - [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] reports a dungeon entry.
    - [`UseStairs`][osrlib.crawl.commands.UseStairs], a passage crossed with
      [`MoveParty`][osrlib.crawl.commands.MoveParty], and a trap that moves the party
      report a level entry when they change the level, or a dungeon entry when they change
      the dungeon.
    - Each of those also reports an area entry when the cell the party arrives on is in
      an area, and so does a `MoveParty` step into a different area.
    - [`TravelToTown`][osrlib.crawl.commands.TravelToTown] reports a town entry.
    - [`PlaceParty`][osrlib.crawl.commands.PlaceParty], when a referee puts the party
      somewhere, reports a dungeon entry or a town entry.

    Which fields are filled depends on the scale, because an area id is unique only
    within its level. An area entry has the area, its level number, and its dungeon. A
    level or dungeon entry has the dungeon in `location_id` and the level number beside
    it. A town entry has neither. A level or dungeon entry also records how the party got
    there, in `via` and, for a transition it took, `transition_ref`, so you can tell from
    the event alone whether the party climbed or descended. Use the event to
    change the screen's header, and read the text the party can see from
    [`GameSession.view`][osrlib.crawl.session.GameSession.view].
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.location.entered"})
    """The only message code this event uses."""

    event_type: Literal["location_entered"] = "location_entered"
    """The event type, `location_entered`, which identifies this class in serialized form."""
    code: str = "exploration.location.entered"
    """The message code, always `exploration.location.entered`. The scale is in `location_kind`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: arriving somewhere is the first thing the players are told."""
    location_kind: str
    """Which scale was crossed: `"area"`, `"level"`, `"dungeon"`, or `"town"`."""
    location_id: str
    """What was entered: the area id for an area entry, the dungeon id for a level or dungeon
    entry, and `"town"` for the town."""
    level_number: int | None = None
    """The level the party is on, for area, level, and dungeon entries, and `None` for town."""
    dungeon_id: str | None = None
    """The dungeon the area belongs to, filled on area entries only. On level and dungeon entries,
    `location_id` is already the dungeon id."""
    narrative: str | None = None
    """The success text the adventure's author wrote on the gate the party passed, or `None` when
    there was no gate or no text. A gate is a condition the author puts on a door or a transition,
    like a key the party must have. The author wrote this text, not the engine, and
    [`format_message`][osrlib.messages.format_message] adds it after the line it builds from the
    code. The session reports no level entry for a transition to another cell of the same level, so
    that transition's gate text is on the [`PartyMovedEvent`][osrlib.crawl.events.PartyMovedEvent]
    for the landing cell."""
    via: str | None = None
    """How the party got there, on level and dungeon entries: the kind of the transition it took
    (`"stairs_down"`, `"stairs_up"`, `"trapdoor"`, or `"chute"`), `"passage"` for a passage it
    walked through with [`MoveParty`][osrlib.crawl.commands.MoveParty], `"trap"` for a trap that
    moved the party, like a chute, `"entrance"` for
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon], and `"placed"` for
    [`PlaceParty`][osrlib.crawl.commands.PlaceParty]. It is `None` on area and town entries, and on
    an event loaded from a save written before the field existed."""
    transition_ref: str | None = None
    """The cell of the authored transition the party took, as
    [`cell_ref`][osrlib.crawl.dungeon.cell_ref] gives it, so you can find the
    [`TransitionSpec`][osrlib.crawl.dungeon.TransitionSpec] and the gate on it. Filled on a level or
    dungeon entry made through [`UseStairs`][osrlib.crawl.commands.UseStairs] or through a passage,
    `None` otherwise. For a passage crossing, it refers to the passage's cell. That cell can also have
    stairs, and passages on its other sides, so find the passage with
    [`LevelSpec.passage_at`][osrlib.crawl.dungeon.LevelSpec.passage_at] and the `direction` of the
    [`MoveParty`][osrlib.crawl.commands.MoveParty] that crossed it."""


class DoorEvent(Event):
    """A door changed state. The event identifies the door by a cell and the side of the cell it's on.

    Emitted by the door commands,
    [`OpenDoor`][osrlib.crawl.commands.OpenDoor],
    [`CloseDoor`][osrlib.crawl.commands.CloseDoor],
    [`ForceDoor`][osrlib.crawl.commands.ForceDoor],
    [`PickLock`][osrlib.crawl.commands.PickLock], and
    [`WedgeDoor`][osrlib.crawl.commands.WedgeDoor]. A door the party opened swings shut
    once the party walks away from it, unless it's wedged, so a command that moves the
    party, like [`MoveParty`][osrlib.crawl.commands.MoveParty],
    [`UseStairs`][osrlib.crawl.commands.UseStairs], or
    [`TravelToTown`][osrlib.crawl.commands.TravelToTown], can emit it too. A referee's
    [`SetDoorState`][osrlib.crawl.commands.SetDoorState] emits it at referee visibility
    when it opens or closes a door, because the party didn't see that happen.

    A door belongs to the edge between two cells, so you can refer to the same door from
    either side. Redraw the door from `x`, `y`, and `direction` rather than keeping your
    own record of which door is which.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {
            "exploration.door.opened",
            "exploration.door.closed",
            "exploration.door.forced",
            "exploration.door.stuck",
            "exploration.door.wedged",
            "exploration.door.swung_shut",
            "exploration.door.unlocked",
        }
    )
    """`exploration.door.opened` and `.closed` for the plain cases, `.forced` for a door shouldered
    open and `.stuck` for the attempt that failed, `.unlocked` for a lock picked, `.wedged` for a
    door spiked in place, and `.swung_shut` for a door the party opened closing behind it."""

    event_type: Literal["door"] = "door"
    """The event type, `door`, which identifies this class in serialized form."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility, except on an event from the referee's `SetDoorState`, which is referee."""
    x: int
    """The column of the cell the door is named from. For a door command it's the party's cell,
    for `SetDoorState` it's the cell given in the command, and for a door swinging shut it's the cell
    on the door's south or east side."""
    y: int
    """The row of the cell the door is named from, chosen the same way as `x`."""
    direction: str
    """Which side of that cell the door is on, as a lowercase
    [`Direction`][osrlib.crawl.dungeon.Direction] value."""
    character_id: str | None = None
    """The member who forced the door, failed to force it, or picked its lock. `None` for the other
    codes, where the party acts as one or nobody acts."""
    narrative: str | None = None
    """The success text the author wrote on the door's gate, when opening the door passed one, or
    `None`. The author wrote this text, not the engine, and
    [`format_message`][osrlib.messages.format_message] adds it after the line it builds from the
    code."""


class ListenedEvent(Event):
    """Someone listened at a door, and either heard something or heard nothing.

    Emitted by [`ListenAtDoor`][osrlib.crawl.commands.ListenAtDoor], after the
    referee-visibility [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent]
    that reports the die.

    `exploration.listen.heard` means the listener heard monsters in the area past the
    door. When the party later meets those monsters, it can't be surprised.

    `exploration.listen.silent` gives the party no information about what is there. Undead
    make no noise, and the session rolls the die whether or not anything is on the other
    side. Show it as "nothing heard", not as a sign that the way is clear.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.listen.heard", "exploration.listen.silent"})
    """`exploration.listen.heard` when noise came through, `exploration.listen.silent` when
    none did."""

    event_type: Literal["listened"] = "listened"
    """The event type, `listened`, which identifies this class in serialized form."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: what a character heard is theirs to know, while the die behind it is
    not."""
    character_id: str
    """The member who listened."""
    direction: str
    """The side of the party's cell that was listened at, as a lowercase
    [`Direction`][osrlib.crawl.dungeon.Direction] value."""


class DetectionRolledEvent(Event):
    """A hidden die roll for a search, a listen, a thief skill, or a trap's chance to spring.

    Emitted by [`Search`][osrlib.crawl.commands.Search],
    [`ListenAtDoor`][osrlib.crawl.commands.ListenAtDoor],
    [`PickLock`][osrlib.crawl.commands.PickLock],
    [`InspectTreasure`][osrlib.crawl.commands.InspectTreasure], and
    [`RemoveTreasureTrap`][osrlib.crawl.commands.RemoveTreasureTrap], ahead of any
    player-visible result, and whenever a trap gets its chance to spring.

    The session rolls the die for a search or a listen whether or not there's anything to
    find, so a failed roll and an empty cell look the same to the players. That is why this
    event has referee visibility: showing it to players would tell them whether the roll
    failed. A referee front end, or an LLM running the game, reads it to know what happened.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.detection.rolled"})
    """The only message code this event uses."""

    event_type: Literal["detection_rolled"] = "detection_rolled"
    """The event type, `detection_rolled`, which identifies this class in serialized form."""
    code: str = "exploration.detection.rolled"
    """The message code, always `exploration.detection.rolled`."""
    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: the roll is the part the referee keeps hidden from the players."""
    character_id: str | None = None
    """The member who rolled, or `None` for a check that isn't a member's, like a trap's chance to
    go off."""
    kind: str
    """What was being checked: `"listening"`, one of the search kinds
    (`"secret_doors"`, `"room_traps"`, `"construction"`), `"open_locks"`, `"treasure_traps"`, or
    `"trap_spring"` for a trap's chance to fire."""
    chance: int
    """The number the roll had to come in at or under. The listening, search, and trap-spring
    kinds are X-in-6 chances rolled on a d6. The thief skills `open_locks` and `treasure_traps`
    are percentages rolled on d100."""
    roll: int | None = None
    """What came up, or `None` when the chance was zero and no die was rolled, as for a character
    with no chance at all of noticing construction tricks."""
    passed: bool
    """Whether the check succeeded. From the player's side, a failed check and a cell with nothing
    in it look the same."""


class SearchCompletedEvent(Event):
    """A search of the party's cell finished, with whatever it turned up.

    Emitted by [`Search`][osrlib.crawl.commands.Search], after the referee-visibility
    [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent] that reports the
    die. [`InspectTreasure`][osrlib.crawl.commands.InspectTreasure] emits it with
    `exploration.search.nothing` when the thief finds no trap. When the thief finds a trap,
    the session emits a [`TrapEvent`][osrlib.crawl.events.TrapEvent] instead.

    An empty result means the searcher found nothing, which doesn't mean nothing is
    there. Each character gets one attempt per cell per kind of search, so another
    character may still find it.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.search.found", "exploration.search.nothing"})
    """`exploration.search.found` when `found` is non-empty, `exploration.search.nothing`
    otherwise."""

    event_type: Literal["search_completed"] = "search_completed"
    """The event type, `search_completed`, which identifies this class in serialized form."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: what the search turned up is the party's to act on."""
    character_id: str
    """The member who searched."""
    kind: str
    """What was searched for: `"secret_doors"`, `"room_traps"`, `"construction"`, or
    `"treasure_traps"` for a treasure feature inspected by a thief."""
    found: tuple[str, ...] = ()
    """What turned up, as references like `"secret_door:north"`, `"room_trap:<area id>"`, or
    `"construction:<feature id>"`, and empty when nothing did. A room trap found through a door
    from the searched cell has the door's direction in a third segment,
    `"room_trap:<area id>:<direction>"`, and one found inside the searched area has none. A found
    secret door becomes an ordinary door the party can see and use, and a found room trap no longer
    springs on the party."""


class TrapEvent(Event):
    """A trap sprang, was found, was removed, or was known and didn't spring.

    Emitted by the commands that can set a trap off or look for one:
    [`MoveParty`][osrlib.crawl.commands.MoveParty],
    [`UseStairs`][osrlib.crawl.commands.UseStairs] and
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon], which run the arrival cell's
    entry checks the same way a step does,
    [`OpenDoor`][osrlib.crawl.commands.OpenDoor] and
    [`ForceDoor`][osrlib.crawl.commands.ForceDoor],
    [`Search`][osrlib.crawl.commands.Search],
    [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure],
    [`InspectTreasure`][osrlib.crawl.commands.InspectTreasure], and
    [`RemoveTreasureTrap`][osrlib.crawl.commands.RemoveTreasureTrap]. A trap that
    fires resolves at once, so its saves and damage follow in the same result as
    kernel events.

    A trap the party doesn't know about that fails to fire emits no `TrapEvent`. Its die
    goes only into the referee-visibility
    [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent], so to the players
    an uneventful step looks like a step on safe ground.

    What finding a trap does depends on the kind of trap. A found room trap never springs,
    whether the party enters its area or opens one of its doors: the party walks around
    the known pit and stands clear of the known blade, so the engine rolls no die for it
    and emits nothing more. A found treasure trap still gets its 2-in-6 roll when the
    party takes the treasure with [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure],
    unless a thief has removed it with
    [`RemoveTreasureTrap`][osrlib.crawl.commands.RemoveTreasureTrap], because finding a
    treasure trap doesn't disarm it. That is why `exploration.trap.safe`, a known trap
    getting its chance and not springing, appears only for a treasure cache.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {"exploration.trap.sprung", "exploration.trap.safe", "exploration.trap.found", "exploration.trap.removed"}
    )
    """`exploration.trap.sprung` when a trap goes off, `.found` when a search or inspection
    reveals one, `.removed` when a thief disarms one, and `.safe` when a trap the party already
    knows about got its chance and didn't fire, which only a treasure trap does."""

    event_type: Literal["trap"] = "trap"
    """The event type, `trap`, which identifies this class in serialized form."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party feels the trap go off, or sees the one it found."""
    trap_ref: str
    """Which trap this is, as `"<dungeon id>:<level number>:<area or feature id>"`. The session
    stores the same reference in its lists of found, sprung, and removed traps, so a trap that has
    sprung or been removed stays that way."""
    character_id: str | None = None
    """The member the event is about. For `exploration.trap.sprung` and `exploration.trap.safe`,
    it's the member who set the trap off: the one who forced the door, reached into the cache, or
    tried to remove the trap. For a trap that springs when the party enters its area or opens its
    door with [`OpenDoor`][osrlib.crawl.commands.OpenDoor], it's the first living member in
    marching order. It's set even when the trap hits the whole party. For
    `exploration.trap.removed`, and for a treasure trap found with
    [`InspectTreasure`][osrlib.crawl.commands.InspectTreasure], it's the thief."""
    direction: str | None = None
    """The direction of the door a room trap was found through, from the searched cell, as a
    [`Direction`][osrlib.crawl.dungeon.Direction] value. Set for that find alone: it is `None` on a
    room trap found inside the searched area, on a treasure trap found on a cache, and on every code but
    `exploration.trap.found`."""


class ItemAcquiredEvent(Event):
    """Items or coins arrived in a character's inventory.

    Emitted by [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure] once per carrier
    who took a share, by
    [`PurchaseEquipment`][osrlib.crawl.commands.PurchaseEquipment] in town, and by
    the referee's [`GrantItem`][osrlib.crawl.commands.GrantItem] and
    [`GrantCoins`][osrlib.crawl.commands.GrantCoins].

    It reports what changed hands, not what the character now carries. Read the
    inventory itself from [`GameSession.view`][osrlib.crawl.session.GameSession.view]
    when you need the full sheet.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.item.acquired"})
    """The only message code this event uses."""

    event_type: Literal["item_acquired"] = "item_acquired"
    """The event type, `item_acquired`, which identifies this class in serialized form."""
    code: str = "exploration.item.acquired"
    """The message code, always `exploration.item.acquired`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party knows what it picked up."""
    character_id: str
    """The member whose pack the goods went into."""
    item_ids: tuple[str, ...] = ()
    """What was acquired: a catalog id for mundane gear, and a session-scoped instance id for a
    valuable or a magic item, so an unidentified item's true nature stays hidden."""
    coins_gp_value: int = 0
    """The coins acquired, converted to their value in gold pieces, and zero when only items
    changed hands."""
    origin: str | None = None
    """Where the goods came from: `"treasure"` for a share of a haul taken with
    [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure], `"purchase"` for gear bought with
    [`PurchaseEquipment`][osrlib.crawl.commands.PurchaseEquipment], and `"grant"` for a referee's
    [`GrantItem`][osrlib.crawl.commands.GrantItem] or [`GrantCoins`][osrlib.crawl.commands.GrantCoins].
    Every command that emits this event fills it, so it is `None` only on an event loaded from a save
    written before the field existed."""


class ItemConsumedEvent(Event):
    """An item a member carried was used up, like a key a gate takes or an iron spike.

    Emitted when the party passes a gate that requires an item and takes it, which can happen
    on [`OpenDoor`][osrlib.crawl.commands.OpenDoor],
    [`ForceDoor`][osrlib.crawl.commands.ForceDoor],
    [`UseStairs`][osrlib.crawl.commands.UseStairs], and a
    [`MoveParty`][osrlib.crawl.commands.MoveParty] through a passage. A gate is a condition
    the adventure's author puts on a door or a transition. Also emitted by
    [`WedgeDoor`][osrlib.crawl.commands.WedgeDoor] for the iron spike it drives.

    It reports that the item is gone. A potion drunk or a scroll read is an
    [`ItemUsedEvent`][osrlib.crawl.events.ItemUsedEvent] instead.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.item.consumed"})
    """The only message code this event uses."""

    event_type: Literal["item_consumed"] = "item_consumed"
    """The event type, `item_consumed`, which identifies this class in serialized form."""
    code: str = "exploration.item.consumed"
    """The message code, always `exploration.item.consumed`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party sees what it spent."""
    character_id: str
    """The member whose pack the item came out of."""
    item_id: str
    """What was consumed: the catalog id for mundane gear, and the session-scoped instance id for
    a magic item, never its template id, so an unidentified item's identity never reaches a
    player-visible event."""


class ItemsDroppedEvent(Event):
    """Items or coins were dropped, onto the party's cell or behind it as bait.

    Emitted by [`DropItems`][osrlib.crawl.commands.DropItems], and by
    [`Evade`][osrlib.crawl.commands.Evade] when the party drops its coins or its food to
    slow the monsters chasing it.

    What is dropped goes into the drop pile on the party's cell, which
    [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure] with `feature_id="pile"` picks
    back up. The exception is a drop made while fleeing: the drop of an `Evade`, or a
    `DropItems` during a chase. Those goods are left on the trail and are gone.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.item.dropped"})
    """The only message code this event uses."""

    event_type: Literal["items_dropped"] = "items_dropped"
    """The event type, `items_dropped`, which identifies this class in serialized form."""
    code: str = "exploration.item.dropped"
    """The message code, always `exploration.item.dropped`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party knows what it let go of."""
    character_id: str
    """The member who dropped them."""
    item_ids: tuple[str, ...] = ()
    """What was dropped, in the same id form the acquisition used: catalog ids for mundane gear,
    instance ids for valuables and magic items."""
    coins_gp_value: int = 0
    """The coins dropped, as their value in gold pieces."""


class ItemsLeftBehindEvent(Event):
    """Treasure the party could not carry, left lying where it was found.

    Emitted by [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure] when the haul is
    heavier than the carriers' remaining capacity.

    Nothing is destroyed. The remainder goes into the drop pile on the party's cell,
    and a party that has made room can get the rest with a `TakeTreasure` whose
    `feature_id` is `"pile"`.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.item.left_behind"})
    """The only message code this event uses."""

    event_type: Literal["items_left_behind"] = "items_left_behind"
    """The event type, `items_left_behind`, which identifies this class in serialized form."""
    code: str = "exploration.item.left_behind"
    """The message code, always `exploration.item.left_behind`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party can see the pile it's walking away from."""
    item_ids: tuple[str, ...] = ()
    """What stayed behind, as catalog ids for mundane gear and instance ids for valuables and
    magic items."""
    coins_gp_value: int = 0
    """The coins left behind, as their value in gold pieces."""


class ItemsGivenEvent(Event):
    """Items or coins passed from one party member to another.

    Emitted by [`GiveItems`][osrlib.crawl.commands.GiveItems]. Update both members'
    inventories on screen when it arrives. Nothing enters or leaves the party, so
    the party's total is unchanged.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.item.given"})
    """The only message code this event uses."""

    event_type: Literal["items_given"] = "items_given"
    """The event type, `items_given`, which identifies this class in serialized form."""
    code: str = "exploration.item.given"
    """The message code, always `exploration.item.given`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party arranged the handover."""
    character_id: str
    """The member who handed the goods over."""
    recipient_id: str
    """The member who took them."""
    item_ids: tuple[str, ...] = ()
    """What was handed over, as catalog ids for mundane gear and instance ids for valuables and
    magic items."""
    coins_gp_value: int = 0
    """The coins handed over, as their value in gold pieces."""


class LightEvent(Event):
    """A light source was lit, went out, failed to catch, or burned away.

    Emitted by [`LightSource`][osrlib.crawl.commands.LightSource],
    [`ExtinguishSource`][osrlib.crawl.commands.ExtinguishSource], and
    [`UseItem`][osrlib.crawl.commands.UseItem] for an item that glows. The session
    also emits the expiry form whenever a light runs out as time passes, which can happen
    inside any command that passes time.

    Without light, the session refuses several commands with
    `exploration.action.requires_light`. Searching and listening at a door also work for
    a character with infravision. Picking a lock, handling a treasure trap, and reading a
    scroll need light. Read whether the party has light from
    [`GameSession.party_light`][osrlib.crawl.session.GameSession.party_light] rather
    than working it out from these events.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {
            "exploration.light.lit",
            "exploration.light.extinguished",
            "exploration.light.failed",
            "exploration.light.expired",
        }
    )
    """`exploration.light.lit` when a source catches, `.extinguished` when it's put out on
    purpose, `.failed` when a tinder box doesn't catch, and `.expired` when a burning source runs
    out on the clock."""

    event_type: Literal["light"] = "light"
    """The event type, `light`, which identifies this class in serialized form."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party sees the light change. When a light burns out, the
    [`EffectExpiredEvent`][osrlib.core.events.EffectExpiredEvent] for it has referee visibility, and
    the session adds this event after it so the players learn of it."""
    character_id: str | None = None
    """The member carrying the source, or `None` when the light belongs to no one in the party."""
    source: str
    """What is burning: `"torch"`, `"lantern"`, `"oil"` for a lit pool, `"sword"` for a blade that
    glows, or the effect kind for a light cast as a spell."""


class RestedEvent(Event):
    """A rest finished, or was interrupted before it could.

    Emitted by [`Rest`][osrlib.crawl.commands.Rest]. A completed rest clears the
    penalty for going without rest, counts toward recovering from exhaustion after a
    chase, and, for a full day, heals the party. A wandering monster that arrives during
    the rest interrupts it. An interrupted rest heals nobody, but each full turn rested
    before the monsters came still clears that penalty and counts toward recovery.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.rest.rested", "exploration.rest.interrupted"})
    """`exploration.rest.rested` when the rest ran to its end, `exploration.rest.interrupted` when
    a wandering encounter cut it short."""

    event_type: Literal["rested"] = "rested"
    """The event type, `rested`, which identifies this class in serialized form."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party knows whether it got its rest."""
    kind: str
    """How long the party tried to rest: `"turn"` for the one-turn breather the dungeon rule
    calls for, `"night"`, or `"day"`."""


class FatigueEvent(Event):
    """The party gained the penalty for going without rest, or recovered from it.

    The B/X rules require a party to rest one turn in every six while it's in a dungeon. A
    party that doesn't takes −1 to attack and damage rolls until it does, and these two
    codes mark when the penalty starts and when it lifts.

    `exploration.fatigue.gained` comes from any command that passes a turn while the
    session is in `exploring` mode, once the party has gone too long without resting
    (see [`check_fatigue`][osrlib.crawl.exploration.check_fatigue]).
    `exploration.fatigue.recovered` comes from a [`Rest`][osrlib.crawl.commands.Rest] once the
    party has rested one full turn, even if a wandering monster interrupts it later.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.fatigue.gained", "exploration.fatigue.recovered"})
    """`exploration.fatigue.gained` when the party misses its rest,
    `exploration.fatigue.recovered` when a rest clears the penalty."""

    event_type: Literal["fatigue"] = "fatigue"
    """The event type, `fatigue`, which identifies this class in serialized form."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party feels it, and the penalty is on their sheets."""


class ProvisionsEvent(Event):
    """A day passed, and a character either ate and drank or went without.

    Emitted once per living member per kind whenever a day boundary passes, which can
    happen inside any command that passes time, and most often inside a
    [`Rest`][osrlib.crawl.commands.Rest].

    Going short adds a day to a count the session keeps for that member (see
    [`consume_provisions`][osrlib.crawl.exploration.consume_provisions]). Whether the
    count brings a penalty depends on
    [`Ruleset.deprivation_penalties`][osrlib.core.ruleset.Ruleset.deprivation_penalties],
    which is off by default. [The adaptations
    register](https://mmacy.github.io/osrlib-python/adaptations/), the page that lists
    where osrlib settles an ambiguous rule or supplies a default, describes the
    penalties. In town nobody runs short.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {"exploration.provisions.consumed", "exploration.provisions.short"}
    )
    """`exploration.provisions.consumed` when the day's food or water was there,
    `exploration.provisions.short` when it was not."""

    event_type: Literal["provisions"] = "provisions"
    """The event type, `provisions`, which identifies this class in serialized form."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party knows whether it ate and drank."""
    character_id: str
    """The member whose rations or water this was."""
    kind: str
    """Which supply the event is about: `"food"` or `"water"`. Each member gets one of each per
    day."""


class WanderingCheckEvent(Event):
    """A wandering-monster check came due and the referee rolled for it.

    Emitted by [`wandering_check`][osrlib.crawl.exploration.wandering_check], which the
    session runs every few turns while it's in `exploring` mode (see
    [`wandering_interval`][osrlib.crawl.exploration.wandering_interval]). Any command that
    passes those turns can emit it, most often
    [`MoveParty`][osrlib.crawl.commands.MoveParty] or
    [`Rest`][osrlib.crawl.commands.Rest]. When the check hits, the encounter opens in
    the same result, and the command passes no more time.

    It has referee visibility because the party can't know a check was made. It knows
    only what it meets.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.wandering.checked"})
    """The only message code this event uses."""

    event_type: Literal["wandering_check"] = "wandering_check"
    """The event type, `wandering_check`, which identifies this class in serialized form."""
    code: str = "exploration.wandering.checked"
    """The message code, always `exploration.wandering.checked`."""
    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: the referee makes the check in secret, and the party sees only what it produces."""
    chance: int
    """The chance in six that the check needed: the level's chance, adjusted for noise, bright
    light, and a hidden rest, and limited to the range 0 to 6."""
    roll: int | None = None
    """The d6 that was rolled, or `None` when the chance came out at zero and no die was
    rolled."""
    encounter: bool
    """Whether the check produced an encounter. When it did, the encounter's events follow in
    the same result."""


class EncounterStartedEvent(Event):
    """The party met monsters, with what the party can see of them.

    Emitted when an encounter opens, whichever way it opened: the party stepping into an
    area whose author placed monsters there, with
    [`MoveParty`][osrlib.crawl.commands.MoveParty], or arriving in one with
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] or
    [`UseStairs`][osrlib.crawl.commands.UseStairs], a wandering-monster check that hits,
    or a referee's [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters] or
    [`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty]. The session mode becomes
    `encounter`, where the party can talk, run, wait, or fight.

    It contains only what the party can see: a name, a count, and a distance. The dice
    behind the meeting are on the
    [`SurpriseRolledEvent`][osrlib.crawl.events.SurpriseRolledEvent]s and the reaction
    roll's kernel event, both at referee visibility. The two surprise outcomes are here,
    because the party knows when it's caught off guard.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"encounter.started"})
    """The only message code this event uses."""

    event_type: Literal["encounter_started"] = "encounter_started"
    """The event type, `encounter_started`, which identifies this class in serialized form."""
    code: str = "encounter.started"
    """The message code, always `encounter.started`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: this is the moment the players are told what they have met."""
    monster_name: str
    """The label of the first group in the encounter, as the party would name it. An NPC
    adventuring party appears under a label like `"Basic Adventurers"`, and its roster stays
    hidden from the players."""
    count: int
    """How many creatures there are across every group in the encounter."""
    distance_feet: int
    """How far away they are, in feet, when the encounter opens. Battle starts from this distance
    and closes from there."""
    party_surprised: bool = False
    """Whether the party was caught off guard, which can give the monsters a free first round."""
    monsters_surprised: bool = False
    """Whether the monsters were caught off guard, which gives the party a free round if the fight
    starts."""


class EncounterGroupJoinedEvent(Event):
    """A new monster group joined the encounter or battle already open, with what the party can see of it.

    Emitted by [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters] when an encounter or a
    battle is open, after the [`MonstersSpawnedEvent`][osrlib.crawl.events.MonstersSpawnedEvent]
    for the same monsters. The players learn of the new group from this event, as they learn of
    the first groups from [`EncounterStartedEvent`][osrlib.crawl.events.EncounterStartedEvent].
    The session rolls no surprise or reaction for the group, which acts from the next round.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"encounter.group.joined"})
    """The only message code this event uses."""

    event_type: Literal["encounter_group_joined"] = "encounter_group_joined"
    """The event type, `encounter_group_joined`, which identifies this class in serialized form."""
    code: str = "encounter.group.joined"
    """The message code, always `encounter.group.joined`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party sees the new monsters arrive."""
    group_id: str
    """The new group's id, the same as its
    [`EncounterGroupView.id`][osrlib.crawl.views.EncounterGroupView.id] in the player view. To
    attack the group, put this id in a battle declaration's `target_group_id`."""
    label: str
    """The group's label: the `template_id` from the `SpawnMonsters` command, like `"goblin"`."""
    count: int
    """How many creatures are in the group."""
    distance_feet: int
    """How far away the group is, in feet, when it joins."""


class SurpriseRolledEvent(Event):
    """One side's surprise die, rolled in secret.

    Emitted twice when an encounter opens, once for each side, before
    [`EncounterStartedEvent`][osrlib.crawl.events.EncounterStartedEvent] reports the
    outcomes to the players.

    A side that can't be surprised doesn't roll, and its event has `roll` set to `None`.
    The party doesn't roll when it already heard the monsters through the door. The
    monsters don't roll when they know the party is coming, when they can see its light,
    or when they're wandering monsters, which know the dungeon. An NPC party met on a
    wandering check still rolls.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"encounter.surprise.rolled"})
    """The only message code this event uses."""

    event_type: Literal["surprise_rolled"] = "surprise_rolled"
    """The event type, `surprise_rolled`, which identifies this class in serialized form."""
    code: str = "encounter.surprise.rolled"
    """The message code, always `encounter.surprise.rolled`."""
    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: the die is the referee's, and the result reaches the players through the
    encounter event."""
    side: str
    """Which side rolled: `"party"` or `"monsters"`."""
    threshold: int
    """The number on a d6 at or under which that side is surprised. It's 2, except for a party
    without light that has a living member without infravision, where it's 3."""
    roll: int | None = None
    """The d6 that came up, or `None` when this side never had to roll."""
    surprised: bool
    """Whether this side was surprised."""


class StanceChangedEvent(Event):
    """The monsters' attitude toward the party changed.

    Emitted when an encounter opens, with the first stance. Also emitted when
    [`Parley`][osrlib.crawl.commands.Parley] changes the monsters' mood, when an
    uncertain stance is rolled again on a later encounter round and changes, and when
    undead that [`TurnUndead`][osrlib.crawl.commands.TurnUndead] failed to turn attack.

    The 2d6 reaction roll behind a stance is a kernel event at referee visibility. An
    encounter whose stance the adventure's author fixed has no roll. What reaches the
    party is how the creatures are acting.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"encounter.stance.changed"})
    """The only message code this event uses."""

    event_type: Literal["stance_changed"] = "stance_changed"
    """The event type, `stance_changed`, which identifies this class in serialized form."""
    code: str = "encounter.stance.changed"
    """The message code, always `encounter.stance.changed`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: behavior is visible even when the roll behind it isn't."""
    stance: str
    """How the monsters are acting now, as a
    [`ReactionResult`][osrlib.core.combat.ReactionResult] value: `"attacks"`, `"hostile"`,
    `"uncertain"`, `"indifferent"`, or `"friendly"`. An attacking stance opens battle in the same
    result."""


class EvasionEvent(Event):
    """The party tried to get away, and either got away or is being chased.

    Emitted by [`Evade`][osrlib.crawl.commands.Evade]. When the party gets clear at
    once, the encounter ends in the same result. Otherwise a chase begins, and its
    rounds arrive as [`PursuitEvent`][osrlib.crawl.events.PursuitEvent]s, the first in
    this same result.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"encounter.evasion.succeeded", "encounter.evasion.pursuit"})
    """`encounter.evasion.succeeded` when the party is away clean, `encounter.evasion.pursuit`
    when something gives chase."""

    event_type: Literal["evasion"] = "evasion"
    """The event type, `evasion`, which identifies this class in serialized form."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party knows whether it's being followed."""


class PursuitEvent(Event):
    """One round of a chase: the gap, a distraction taken, an escape, or a capture.

    Emitted by [`Evade`][osrlib.crawl.commands.Evade] for the first round of the chase.
    Each further round comes from [`Wait`][osrlib.crawl.commands.Wait], to keep running,
    or [`DropItems`][osrlib.crawl.commands.DropItems], to drop something behind the party
    as it runs.

    A capture opens battle at once. An escape or a distraction ends the encounter. A
    chase that lasts [`PURSUIT_ROUND_CAP`][osrlib.crawl.encounter.PURSUIT_ROUND_CAP] rounds
    ends in an escape and tires the party out, which arrives as
    [`ExhaustionEvent`][osrlib.crawl.events.ExhaustionEvent].
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {
            "encounter.pursuit.round",
            "encounter.pursuit.distracted",
            "encounter.pursuit.escaped",
            "encounter.pursuit.caught",
        }
    )
    """`encounter.pursuit.round` for a round where the chase goes on, `.distracted` when dropped
    treasure or food stops the pursuers, `.escaped` when the party gets clear, and `.caught` when
    the pursuers close to arm's length and battle opens."""

    event_type: Literal["pursuit"] = "pursuit"
    """The event type, `pursuit`, which identifies this class in serialized form."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party can see how close the chase is."""
    round: int
    """Which round of the chase this is, counting from one."""
    gap_feet: int
    """How far ahead the party is, in feet, at the end of this round. It never goes below zero, and
    at five feet or less the pursuers have caught up."""


class ExhaustionEvent(Event):
    """The party became exhausted from a long chase, or rested long enough to recover.

    Running flat out for a long chase gives a party −2 to its attack and damage rolls
    and makes it 2 easier to hit, until it rests. `encounter.exhaustion.gained` comes
    from the [`Evade`][osrlib.crawl.commands.Evade], [`Wait`][osrlib.crawl.commands.Wait],
    or [`DropItems`][osrlib.crawl.commands.DropItems] whose round ends a chase that lasted
    [`PURSUIT_ROUND_CAP`][osrlib.crawl.encounter.PURSUIT_ROUND_CAP] rounds.
    `encounter.exhaustion.recovered` comes from the
    [`Rest`][osrlib.crawl.commands.Rest] that completes three turns of rest since the
    chase.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {"encounter.exhaustion.gained", "encounter.exhaustion.recovered"}
    )
    """`encounter.exhaustion.gained` when the chase exhausts the party,
    `encounter.exhaustion.recovered` when enough rest clears it."""

    event_type: Literal["exhaustion"] = "exhaustion"
    """The event type, `exhaustion`, which identifies this class in serialized form."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the penalty is on the party's character sheets."""


class EncounterEndedEvent(Event):
    """The encounter is over, however it went.

    Emitted when the monsters are beaten or turned, or when the party gets away from them.
    The session goes back to `exploring`. The session then advances the clock to the start
    of the next turn, and to at least one full turn after the encounter opened, so time
    passes after this event even when the fight was short. The
    [`MonsterDefeatedEvent`][osrlib.crawl.events.MonsterDefeatedEvent]s come just before it.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"encounter.ended"})
    """The only message code this event uses."""

    event_type: Literal["encounter_ended"] = "encounter_ended"
    """The event type, `encounter_ended`, which identifies this class in serialized form."""
    code: str = "encounter.ended"
    """The message code, always `encounter.ended`. The ending is in `outcome`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party knows the encounter is behind it."""
    outcome: str
    """How it ended: `"victory"` when the monsters were beaten, `"evaded"` when the party got away
    before or during a fight, `"escaped"` when the party got away at the end of a chase,
    `"turned"` when undead were driven off, or the outcome of an
    [`EndEncounter`][osrlib.crawl.commands.EndEncounter]: `"parley"` when the party talked or paid its
    way past, or `"departed"` when it walked away from monsters that let it go."""


class BattleStartedEvent(Event):
    """The encounter became a battle.

    Emitted when a fight opens: by [`EngageBattle`][osrlib.crawl.commands.EngageBattle]
    when the party attacks, and by whichever command is running when the monsters attack.
    The monsters can attack the moment an encounter opens, when a parley goes wrong, when
    a hostile or uncertain stance turns to attack on a later round, when undead are
    presented with a holy symbol and not turned, or when a chase ends in capture.

    The session mode becomes `battle`, where
    [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound] is the only play
    command the session accepts. In battle, the session doesn't place monsters on cells. Each
    monster group has a distance from the party, and closing in or pulling back changes it.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"battle.started"})
    """The only message code this event uses."""

    event_type: Literal["battle_started"] = "battle_started"
    """The event type, `battle_started`, which identifies this class in serialized form."""
    code: str = "battle.started"
    """The message code, always `battle.started`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party is in it."""


class BattleRoundEvent(Event):
    """A battle round began.

    Emitted first by every
    [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound], before the
    declared spells and the initiative roll. In a transcript, group the rest of the
    round's events under it.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"battle.round.started"})
    """The only message code this event uses."""

    event_type: Literal["battle_round"] = "battle_round"
    """The event type, `battle_round`, which identifies this class in serialized form."""
    code: str = "battle.round.started"
    """The message code, always `battle.round.started`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: everyone knows which round it is."""
    round: int
    """Which round of this battle is starting, counting from one."""


class SpellDeclaredEvent(Event):
    """Somebody declared a spell, before anyone knows who acts first.

    Emitted by [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound] for
    each caster who declared one, party member or NPC alike, at the start of the round.

    In B/X, spells are declared before initiative is rolled. A caster who takes damage
    before their turn loses the spell, and the other side knows what is coming. A lost
    spell arrives later in the round as a
    [`SpellDisruptedEvent`][osrlib.core.events.SpellDisruptedEvent].
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"battle.spell.declared"})
    """The only message code this event uses."""

    event_type: Literal["spell_declared"] = "spell_declared"
    """The event type, `spell_declared`, which identifies this class in serialized form."""
    code: str = "battle.spell.declared"
    """The message code, always `battle.spell.declared`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: a declaration is made out loud."""
    caster_id: str
    """Who is casting: a party member's id, or an NPC adventurer's."""
    spell_id: str
    """Which spell was declared, as its catalog id."""
    reversed: bool = False
    """Whether the reversed form was declared, for a spell that has one."""


class GroupMovedEvent(Event):
    """A monster group's distance from the party changed.

    Emitted by [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound]
    whenever a group's distance changes: the party closing on a group or backing away
    from every group, monsters closing to strike, and a broken group running away.

    Distance decides who can reach whom, so redraw a battle screen's ranks from this
    event.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"battle.group.moved"})
    """The only message code this event uses."""

    event_type: Literal["group_moved"] = "group_moved"
    """The event type, `group_moved`, which identifies this class in serialized form."""
    code: str = "battle.group.moved"
    """The message code, always `battle.group.moved`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party watches them come on or draw off."""
    group_id: str
    """Which group moved, as its encounter group id."""
    distance_feet: int
    """How far that group now is from the party, in feet, after the move. A group that closes in
    stops at [`MELEE_RANGE_FEET`][osrlib.crawl.battle.MELEE_RANGE_FEET], where the two sides fight
    hand to hand."""


class MonsterFledEvent(Event):
    """A monster group broke and ran.

    Emitted by [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound] when
    a group fails a morale check. When a battle opens, it's also emitted for a group
    whose morale is so low that it never fights at all. When a cleric turns only some of
    a group, the turned members break away as a new group, and `group_id` is the new
    group's id. The split happens as the battle opens after a
    [`TurnUndead`][osrlib.crawl.commands.TurnUndead], or in the round a `turn_undead`
    declaration resolves. If the rest of the group was afraid at the time, the split
    happens in the round the fear ends instead. A running group moves away each
    round and is gone once it's more than
    [`FLEE_EXIT_FEET`][osrlib.crawl.battle.FLEE_EXIT_FEET] from the party. Its members
    still count as defeated for the experience award.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"battle.side.fled"})
    """The only message code this event uses."""

    event_type: Literal["monster_fled"] = "monster_fled"
    """The event type, `monster_fled`, which identifies this class in serialized form."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party sees them break."""
    group_id: str
    """Which group broke, as its encounter group id."""


class MonstersLeftBehindEvent(Event):
    """A group that ran left its helpless members lying where they were.

    Emitted by [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound] when
    a broken group has members who can't move, like those asleep, paralysed, or held.

    The runners stay in the original group and go on fleeing. The ones left behind become
    a new group at the distance where the side broke, so the party can finish them,
    take what they carry, or walk past.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"battle.group.left_behind"})
    """The only message code this event uses."""

    event_type: Literal["monsters_left_behind"] = "monsters_left_behind"
    """The event type, `monsters_left_behind`, which identifies this class in serialized form."""
    code: str = "battle.group.left_behind"
    """The message code, always `battle.group.left_behind`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party can see who was abandoned."""
    group_id: str
    """The new group the helpless members were put into."""
    source_group_id: str
    """The group that ran off without them."""
    count: int
    """How many were left behind."""


class MonsterDefeatedEvent(Event):
    """One monster was defeated, with the experience it's worth.

    Emitted when the encounter ends, once for each creature that was slain, ran away, or
    was turned, before the [`EncounterEndedEvent`][osrlib.crawl.events.EncounterEndedEvent].
    A creature the party got away from without defeating has no event.

    The session adds up their `xp` for the experience award. Under the default ruleset the
    award arrives as an [`AdventureXpAwardEvent`][osrlib.crawl.events.AdventureXpAwardEvent]
    when the party travels back to town. When
    [`Ruleset.xp_award_timing`][osrlib.core.ruleset.Ruleset.xp_award_timing] is set to award
    experience immediately, each survivor's
    [`XpAwardedEvent`][osrlib.crawl.events.XpAwardedEvent] arrives at the end of the
    encounter instead, before the `EncounterEndedEvent`.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"battle.monster.defeated"})
    """The only message code this event uses."""

    event_type: Literal["monster_defeated"] = "monster_defeated"
    """The event type, `monster_defeated`, which identifies this class in serialized form."""
    code: str = "battle.monster.defeated"
    """The message code, always `battle.monster.defeated`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party sees them fall or flee."""
    monster_id: str
    """The session id of the creature that was defeated."""
    template_id: str
    """What it was: a monster catalog id, or `"npc:<class id>"` for a defeated NPC adventurer."""
    outcome: str
    """How it was defeated: `"slain"`, or `"routed"` when it ran away or was turned."""
    xp: int
    """What it's worth: the monster catalog's printed award, or the level-based award for an NPC
    adventurer."""


class BattleEndedEvent(Event):
    """The battle is over: won, quit, or lost.

    Emitted by [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound]. A
    battle can also end in the command that opened it, before any
    `ResolveBattleRound`: every group's morale is too low to fight, or the monsters'
    free round leaves no party member standing.

    A victory ends the encounter in the same result. After a retreat, monsters able to
    give chase start a chase, and the session goes back to `encounter` mode. Otherwise
    the encounter ends. A defeat means nobody is left standing, and a
    [`GameOverEvent`][osrlib.crawl.events.GameOverEvent] follows in the same result.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {"battle.ended.victory", "battle.ended.fled", "battle.ended.defeat"}
    )
    """`battle.ended.victory` when no opposition is left fighting, `battle.ended.fled` when the
    party pulled out, and `battle.ended.defeat` when the party fell."""

    event_type: Literal["battle_ended"] = "battle_ended"
    """The event type, `battle_ended`, which identifies this class in serialized form."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party is in the fight and sees how it ends."""


class HoardGeneratedEvent(Event):
    """The engine rolled treasure and put it in a cache, before the party has found it.

    Emitted when the party first enters an area whose author declared treasure, on the
    cell it enters, and when monsters the author placed in an area appear with their lair
    hoard, on the area's first cell. The goods go into a cache on that cell, which the
    party empties with [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure], passing the
    cache's id as `feature_id` while standing on the cell.

    It has referee visibility, and it lists everything: telling the players would be
    telling them what is in the room. A referee front end, or an LLM running the game,
    reads it to know what is there.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"treasure.hoard.generated"})
    """The only message code this event uses."""

    event_type: Literal["hoard_generated"] = "hoard_generated"
    """The event type, `hoard_generated`, which identifies this class in serialized form."""
    code: str = "treasure.hoard.generated"
    """The message code, always `treasure.hoard.generated`."""
    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: the party learns what the cache contains when it takes the treasure."""
    cache_ref: str
    """The id of the cache the treasure went into, like `"cache-0001"`. Pass it as `feature_id` to
    [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure] from the cache's cell."""
    treasure_types: tuple[str, ...] = ()
    """The treasure-type letters that were rolled, one entry per roll, so a hoard rolled from two
    letters lists both. Treasure rolled on the unguarded-treasure table has `("unguarded",)`."""
    coins_gp_value: int = 0
    """The coins in the hoard, as their value in gold pieces."""
    valuable_ids: tuple[str, ...] = ()
    """The session-scoped instance ids of the gems and jewellery in the hoard."""
    magic_item_ids: tuple[str, ...] = ()
    """The session-scoped instance ids of the magic items in the hoard."""


class ItemUsedEvent(Event):
    """A magic item was used: a potion drunk, a scroll read, a device fired.

    Emitted by [`UseItem`][osrlib.crawl.commands.UseItem] outside battle and by
    [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound] for an item used
    in a battle. Whatever the item does follows in the same result as kernel events.

    Using an item for the first time identifies it, so an
    [`ItemIdentifiedEvent`][osrlib.crawl.events.ItemIdentifiedEvent], and a
    [`CurseRevealedEvent`][osrlib.crawl.events.CurseRevealedEvent] for a cursed item,
    come just before this one. Using a device that has no charges left emits no event:
    the session refuses the command with `items.device.inert`, and the party loses
    nothing. Charges never appear on any event, because only the referee knows how many
    uses an item has left.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {
            "items.potion.drunk",
            "items.potion.mixed",
            "items.scroll.read",
            "items.scroll.cursed",
            "items.device.activated",
        }
    )
    """`items.potion.drunk` for a potion taken on its own, `items.potion.mixed` when it meets
    another still in effect, which ends both and paralyses the drinker for three turns,
    `items.scroll.read` for a scroll, `items.scroll.cursed` for a cursed scroll, which curses its
    reader, and `items.device.activated` for a rod, staff, or wand."""

    event_type: Literal["item_used"] = "item_used"
    """The event type, `item_used`, which identifies this class in serialized form."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party watched it happen."""
    character_id: str
    """The member who used the item."""
    instance_id: str
    """The session-scoped id of the item used, never its catalog id, so an event about an item the
    party hasn't identified doesn't reveal what the item is."""
    manual: tuple[str, ...] = ()
    """The item's printed text, for an item whose effect the engine doesn't resolve, like a treasure
    map, or the curse's text for a cursed scroll whose curse the engine doesn't apply. Empty when
    the engine resolved the effect itself. Show these lines to the players and rule on them
    yourself."""


class ItemIdentifiedEvent(Event):
    """A magic item was identified, and the party now knows what it is.

    Emitted the first time an item is used in a way that reveals it, which can happen
    in [`UseItem`][osrlib.crawl.commands.UseItem],
    [`EquipItem`][osrlib.crawl.commands.EquipItem], and
    [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound], and when the
    referee identifies it with [`IdentifyItem`][osrlib.crawl.commands.IdentifyItem].

    Before this, player-visible events contain only the item's `instance_id`. After it,
    you can show the party the name of the item whose catalog id is `template_id`.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"items.item.identified"})
    """The only message code this event uses."""

    event_type: Literal["item_identified"] = "item_identified"
    """The event type, `item_identified`, which identifies this class in serialized form."""
    code: str = "items.item.identified"
    """The message code, always `items.item.identified`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: this is the moment the party learns what it has."""
    instance_id: str
    """The session-scoped id of the item, the same id the earlier events contained."""
    template_id: str
    """What it turned out to be, as its magic item catalog id."""


class CurseRevealedEvent(Event):
    """The curse on a cursed item is now known, and the item can't be taken off.

    Emitted when the party learns of a cursed item's curse: when the item is used, or worn
    so that its curse takes hold, or when the referee identifies it with
    [`IdentifyItem`][osrlib.crawl.commands.IdentifyItem]. It comes right after the
    [`ItemIdentifiedEvent`][osrlib.crawl.events.ItemIdentifiedEvent] when the item wasn't
    identified yet. From then on the member can't drop, give away, or unequip the item until
    a *remove curse* lifts the curse.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"items.curse.revealed"})
    """The only message code this event uses."""

    event_type: Literal["curse_revealed"] = "curse_revealed"
    """The event type, `curse_revealed`, which identifies this class in serialized form."""
    code: str = "items.curse.revealed"
    """The message code, always `items.curse.revealed`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the bearer learns about the curse when it takes hold."""
    character_id: str
    """The member who has the cursed item."""
    instance_id: str
    """The session-scoped id of the cursed item."""
    template_id: str
    """What the item is, as its magic item catalog id."""


class NpcPartySpawnedEvent(Event):
    """The engine generated an NPC adventuring party and added it to the session.

    Emitted by the referee's
    [`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty] and by a wandering roll
    that comes up adventurers, before the encounter opens.

    It has referee visibility and contains the whole roster. What the party sees is the
    [`EncounterStartedEvent`][osrlib.crawl.events.EncounterStartedEvent], which labels
    them as adventurers and contains a count. The party learns their classes and levels by
    talking or by fighting.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"encounter.npc_party.spawned"})
    """The only message code this event uses."""

    event_type: Literal["npc_party_spawned"] = "npc_party_spawned"
    """The event type, `npc_party_spawned`, which identifies this class in serialized form."""
    code: str = "encounter.npc_party.spawned"
    """The message code, always `encounter.npc_party.spawned`."""
    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: the players see only the label and the count on the encounter event."""
    party_kind: str
    """Which table the party was rolled from: `"basic"` or `"expert"`."""
    npc_ids: tuple[str, ...]
    """The session ids of its members, in roster order. The other three tuples line up with this
    one."""
    class_ids: tuple[str, ...]
    """Each member's class, as a class catalog id."""
    levels: tuple[int, ...]
    """Each member's level."""
    alignment: str
    """The party's alignment, as a lowercase [`Alignment`][osrlib.core.alignment.Alignment] value."""


class AdventureXpAwardEvent(Event):
    """The experience award for a trip to the dungeon: what the party earned and what each survivor gets.

    Emitted by [`TravelToTown`][osrlib.crawl.commands.TravelToTown] under the default
    ruleset, where experience is awarded for making it back. Each survivor's
    [`XpAwardedEvent`][osrlib.crawl.events.XpAwardedEvent] follows it, each followed by
    a [`CharacterLeveledUpEvent`][osrlib.crawl.events.CharacterLeveledUpEvent] when that
    member gains a level.

    Treasure counts as the gain in the value of the party's coins, gems, and jewellery
    since it left town, so goods still lying in the dungeon are worth nothing yet. The session
    emits no event when nobody came back alive or when the delve earned nothing.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.xp.adventure_award"})
    """The only message code this event uses."""

    event_type: Literal["adventure_xp_award"] = "adventure_xp_award"
    """The event type, `adventure_xp_award`, which identifies this class in serialized form."""
    code: str = "session.xp.adventure_award"
    """The message code, always `session.xp.adventure_award`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party sees what it earned."""
    monster_xp: int
    """What the defeated creatures were worth, added up across the whole delve."""
    treasure_xp: int
    """What the recovered treasure was worth, one experience point per gold piece of value gained
    since the party left town, and never less than zero."""
    share: int
    """What each survivor receives: the total divided by the number of survivors, rounded down.
    When it rounds down to zero, no `XpAwardedEvent` follows."""
    survivors: tuple[str, ...]
    """The members who made it back, in marching order. The dead count toward the treasure that
    came home but take no share."""


class TreasureSoldEvent(Event):
    """Valuables were sold in town, and the coins are in the purse.

    Emitted by [`SellTreasure`][osrlib.crawl.commands.SellTreasure], one event for each
    member whose valuables were sold. Gems and jewellery sell for their full listed
    value, so selling them neither adds to nor takes from the treasure the experience
    award counts.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"town.treasure.sold"})
    """The only message code this event uses."""

    event_type: Literal["treasure_sold"] = "treasure_sold"
    """The event type, `treasure_sold`, which identifies this class in serialized form."""
    code: str = "town.treasure.sold"
    """The message code, always `town.treasure.sold`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party made the sale."""
    character_id: str
    """The member who sold them and now has the coins."""
    instance_ids: tuple[str, ...]
    """The session-scoped ids of the valuables that were sold."""
    gp_value: int
    """What they fetched, in gold pieces."""


class HealingPurchasedEvent(Event):
    """The party paid for a temple service, and the temple cast it.

    Emitted by [`PurchaseHealing`][osrlib.crawl.commands.PurchaseHealing] in town,
    followed by the kernel events of the spell the temple casts. The fee can come from
    more than one member, so `payers` and `payments_gp` show which purses paid and how
    much each one paid.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"town.healing.purchased"})
    """The only message code this event uses."""

    event_type: Literal["healing_purchased"] = "healing_purchased"
    """The event type, `healing_purchased`, which identifies this class in serialized form."""
    code: str = "town.healing.purchased"
    """The message code, always `town.healing.purchased`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party bought it."""
    character_id: str
    """The member the service was cast on. Their purse is charged first, but the party covers
    whatever is left, so read `payers` for who actually paid."""
    service: str
    """Which service was bought, as its key in
    [`HEALING_SERVICES`][osrlib.crawl.exploration.HEALING_SERVICES], like `"cure_light_wounds"`."""
    cost_gp: int
    """What it cost, in gold pieces."""
    payers: tuple[str, ...] = ()
    """The ids of the members whose purses paid, in the order they were charged: the treated
    member first, then the rest of the party in marching order, dead members included. Each purse
    pays as much of the fee still owed as its coins' value covers, in whole gold pieces, until the
    fee is paid. A member whose purse paid nothing isn't listed. It's empty on an event loaded from
    a save written before the field existed."""
    payments_gp: tuple[int, ...] = ()
    """What each purse in `payers` paid, in gold pieces and in the same order. The entries sum to
    `cost_gp`."""


class FlagSetEvent(Event):
    """A session flag was written.

    Emitted by the referee's [`SetFlag`][osrlib.crawl.commands.SetFlag]. Flags are
    where the session records state. An adventure's triggers and gates can depend on a flag
    through a [`FlagEqualsCondition`][osrlib.crawl.gates.FlagEqualsCondition], and you can
    store anything else you need there.

    It has referee visibility, because flags record the game's state, not events in the
    story the players see.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.flag.set"})
    """The only message code this event uses."""

    event_type: Literal["flag_set"] = "flag_set"
    """The event type, `flag_set`, which identifies this class in serialized form."""
    code: str = "session.flag.set"
    """The message code, always `session.flag.set`."""
    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: flags are bookkeeping that the players don't see."""
    key: str
    """Which flag was written."""
    value: str | int | bool
    """What it was set to. Writing an existing key replaces its value."""


class MonstersSpawnedEvent(Event):
    """Monsters were put into the session by the referee.

    Emitted by [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters], first in its
    result. When no encounter is open, the events of the encounter's opening follow. When
    one is open, the monsters join it as a new group.

    It's referee visibility and contains ids rather than a description. The party
    learns what walked in from
    [`EncounterStartedEvent`][osrlib.crawl.events.EncounterStartedEvent] when the
    encounter opens, or from
    [`EncounterGroupJoinedEvent`][osrlib.crawl.events.EncounterGroupJoinedEvent] when the
    monsters join an open one.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.monsters.spawned"})
    """The only message code this event uses."""

    event_type: Literal["monsters_spawned"] = "monsters_spawned"
    """The event type, `monsters_spawned`, which identifies this class in serialized form."""
    code: str = "session.monsters.spawned"
    """The message code, always `session.monsters.spawned`."""
    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: this is bookkeeping, not a moment in the fiction."""
    template_id: str
    """What was spawned, as a monster catalog id."""
    monster_ids: tuple[str, ...]
    """The session ids of the new instances, in spawn order. Every later event about
    those creatures refers to them by these ids."""


class XpAwardedEvent(Event):
    """One character was awarded experience.

    Emitted wherever an award is made: after the
    [`AdventureXpAwardEvent`][osrlib.crawl.events.AdventureXpAwardEvent] on
    [`TravelToTown`][osrlib.crawl.commands.TravelToTown], and by the referee's
    [`AwardXP`][osrlib.crawl.commands.AwardXP]. When the ruleset is set to award experience
    immediately, it's also emitted at the end of an encounter in which monsters were
    defeated, and by a [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure] that takes
    coins, gems, or jewellery. When the award crosses a threshold, a
    [`CharacterLeveledUpEvent`][osrlib.crawl.events.CharacterLeveledUpEvent] for the same
    member follows it at once.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.xp.awarded"})
    """The only message code this event uses."""

    event_type: Literal["xp_awarded"] = "xp_awarded"
    """The event type, `xp_awarded`, which identifies this class in serialized form."""
    code: str = "session.xp.awarded"
    """The message code, always `session.xp.awarded`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: it goes on the character's sheet."""
    character_id: str
    """The member who received it."""
    award: int
    """The award as the session handed it over, before the character's adjustment."""
    modified_award: int
    """The award after the class's prime-requisite adjustment, rounded down. The character gains
    this much, except when it would raise them two levels: one award raises a character at most one
    level, so their total stops one point short of the level after next and the rest is lost."""
    level_after: int
    """The member's level once the award was applied."""


class CharacterLeveledUpEvent(Event):
    """A character crossed a threshold and gained a level.

    Emitted right after that member's
    [`XpAwardedEvent`][osrlib.crawl.events.XpAwardedEvent], for whichever award crossed
    the threshold. A character gains at most one level per award.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.level.gained"})
    """The only message code this event uses."""

    event_type: Literal["leveled_up"] = "leveled_up"
    """The event type, `leveled_up`, which identifies this class in serialized form."""
    code: str = "session.level.gained"
    """The message code, always `session.level.gained`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the new level goes on the character's sheet."""
    character_id: str
    """The member who gained the level."""
    level_before: int
    """The member's level before the award."""
    level_after: int
    """The member's level after the award, one higher."""
    hp_gained: int
    """How many hit points were added, the die and the constitution adjustment together, or the
    flat bonus past the class's last Hit Die."""
    hp_roll: int | None
    """The hit die that was rolled, or `None` past the class's last Hit Die, where levels bring a
    flat bonus and no die."""
    con_applied: bool
    """Whether the constitution adjustment was applied, which happens only when a die was rolled."""
    title: str | None
    """The class's title for the new level, or `None` past the printed list of titles."""


class TimeAdvancedEvent(Event):
    """The referee moved the clock.

    Emitted by [`AdvanceTime`][osrlib.crawl.commands.AdvanceTime], after the events of the
    time that passed. Effects run out, lights burn out, and the session uses up provisions
    for each day crossed, and those events arrive in the same result. No wandering-monster
    check and no fatigue check runs: a referee moving the clock decides what happens.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.time.advanced"})
    """The only message code this event uses."""

    event_type: Literal["time_advanced"] = "time_advanced"
    """The event type, `time_advanced`, which identifies this class in serialized form."""
    code: str = "session.time.advanced"
    """The message code, always `session.time.advanced`."""
    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: the players see what the time passing does, not the command that passed it."""
    n: int
    """How many units were asked for."""
    unit: str
    """Which unit, as a lowercase [`TimeUnit`][osrlib.core.clock.TimeUnit] value: `"round"`,
    `"turn"`, or `"day"`."""
    rounds_total: int
    """Where the clock now stands, in rounds since the session began. It's the same number as
    `GameSession.clock.rounds`."""


class GameOverEvent(Event):
    """Every party member is dead and the session has ended.

    Emitted by the command during which the last member died, whatever killed them: a
    lost battle, a trap, a fall down a chute, starvation, or a poison that took effect
    while the referee moved the clock. It comes after that command's other events, and
    the session mode becomes `game_over`.

    The session refuses play commands from then on. Referee commands still work, and
    [`PlaceParty`][osrlib.crawl.commands.PlaceParty] takes the session out of
    `game_over`. Placing the fallen party in town lets the temple raise the dead with
    [`PurchaseHealing`][osrlib.crawl.commands.PurchaseHealing].
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.game_over"})
    """The only message code this event uses."""

    event_type: Literal["game_over"] = "game_over"
    """The event type, `game_over`, which identifies this class in serialized form."""
    code: str = "session.game_over"
    """The message code, always `session.game_over`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: it's the party's ending."""
    reason: str
    """Why the session ended, as a short phrase. Every ending has the same reason, that the party
    has fallen. What killed them is in the events just before it."""


class DiceRolledEvent(Event):
    """The referee rolled dice for something the rules don't cover.

    Emitted by [`RollDice`][osrlib.crawl.commands.RollDice]. The dice come from
    [`ADJUDICATION_STREAM`][osrlib.crawl.session.ADJUDICATION_STREAM], which the session
    uses for no other roll, so a referee rolling for weather or a rumour never changes the
    dice for a later attack or save.

    It has referee visibility, because a referee's roll may be secret. Show it to the
    players yourself when you roll in the open.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"adjudication.dice_rolled"})
    """The only message code this event uses."""

    event_type: Literal["dice_rolled"] = "dice_rolled"
    """The event type, `dice_rolled`, which identifies this class in serialized form."""
    code: str = "adjudication.dice_rolled"
    """The message code, always `adjudication.dice_rolled`."""
    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: the referee decides what to share."""
    expression: str
    """What was rolled, as the dice expression that was asked for, like `"2d6+1"`."""
    total: int
    """The result, dice and modifier together."""
    rolls: tuple[int, ...]
    """Each die's result, in roll order, so you can show the dice in a transcript rather than
    only the sum."""


class TriggerFiredEvent(Event):
    """A trigger the adventure's author wrote fired.

    A trigger is a rule the adventure's author wrote: when a given event happens, run
    given commands. Emitted by [`MarkTriggerFired`][osrlib.crawl.commands.MarkTriggerFired]
    every time it runs, including for a trigger that has fired before. The session keeps
    only whether a trigger has fired, and these events are the record of each firing.

    It has referee visibility, because which trigger fired is part of how the adventure
    works, not part of the story the players see. Text the author wrote for the players
    goes in the journal and arrives as a
    [`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent] or as one of
    the quest events.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.trigger.fired"})
    """The only message code this event uses."""

    event_type: Literal["trigger_fired"] = "trigger_fired"
    """The event type, `trigger_fired`, which identifies this class in serialized form."""
    code: str = "session.trigger.fired"
    """The message code, always `session.trigger.fired`."""
    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: which triggers an adventure has is hidden from the players."""
    trigger_id: str
    """Which trigger fired, as the id the adventure gave it."""
    narrative: str | None = None
    """The text the author wrote for this firing, or `None`. The author wrote it, not the engine,
    and [`format_message`][osrlib.messages.format_message] adds it after the line it builds from the
    code. This event has referee visibility, so the players don't see this text. Text meant for
    them belongs in the journal."""


class JournalEntryAddedEvent(Event):
    """A line of text was added to the session journal.

    Emitted by [`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry]. The
    journal is the party's record of the adventure. Lines are added in order and never
    changed, and the journal is part of what
    [`GameSession.view`][osrlib.crawl.session.GameSession.view] shows a player.

    Quest text also goes into the journal, but its quest event reports it, like
    [`QuestActivatedEvent`][osrlib.crawl.events.QuestActivatedEvent], with no
    `JournalEntryAddedEvent`, so the players aren't shown the same line twice. Read the
    whole journal from the view, and read these events to know when a line arrived.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.journal.entry_added"})
    """The only message code this event uses."""

    event_type: Literal["journal_entry_added"] = "journal_entry_added"
    """The event type, `journal_entry_added`, which identifies this class in serialized form."""
    code: str = "session.journal.entry_added"
    """The message code, always `session.journal.entry_added`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the journal is written for the players."""
    text: str
    """The line as it was written. It came from your code or from the adventure, not from the
    engine."""
    rounds: int
    """Where the clock stood when the line was added, in rounds since the session began. The stored
    journal entry has the same number."""


class NoteRecordedEvent(Event):
    """A referee note was recorded, and no game state changed.

    Emitted by [`RecordNote`][osrlib.crawl.commands.RecordNote]. The
    [`Interpreter`][osrlib.crawl.interpreter.Interpreter] uses it to leave a
    machine-written note in the log when a consequence can't be applied, and referees
    use it for their margin notes.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.note.recorded"})
    """The only message code this event uses."""

    event_type: Literal["note_recorded"] = "note_recorded"
    """The event type, `note_recorded`, which identifies this class in serialized form."""
    code: str = "session.note.recorded"
    """The message code, always `session.note.recorded`."""
    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: a note is for the person running the game."""
    text: str
    """The note as it was written."""


class QuestActivatedEvent(Event):
    """A quest became active.

    Emitted by [`ActivateQuest`][osrlib.crawl.commands.ActivateQuest]. A quest whose
    `activate_when` is `None` is active from the start of the session, with no command
    and no event.

    It has player visibility, because the party knows the job it has taken on. The
    trigger that activated the quest stays hidden, like the trigger and flag events.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.quest.activated"})
    """The only message code this event uses."""

    event_type: Literal["quest_activated"] = "quest_activated"
    """The event type, `quest_activated`, which identifies this class in serialized form."""
    code: str = "session.quest.activated"
    """The message code, always `session.quest.activated`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party is being given the job."""
    quest_id: str
    """Which quest, as the id the adventure gave it."""
    name: str
    """The quest's display name, so you don't need the adventure to show it."""
    narrative: str | None = None
    """The offer text the author wrote, or `None` when there's none. The session adds the same line
    to the journal with no separate journal event, so the players see it once."""


class ObjectiveRevealedEvent(Event):
    """A hidden objective was revealed, so the party can now be told what it's asked to do.

    Emitted by [`RevealObjective`][osrlib.crawl.commands.RevealObjective]. An
    objective the author didn't mark hidden is visible from the start and never
    gets this event.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.quest.objective_revealed"})
    """The only message code this event uses."""

    event_type: Literal["objective_revealed"] = "objective_revealed"
    """The event type, `objective_revealed`, which identifies this class in serialized form."""
    code: str = "session.quest.objective_revealed"
    """The message code, always `session.quest.objective_revealed`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party is being told what to do."""
    quest_id: str
    """The quest the objective belongs to."""
    quest_name: str = ""
    """The quest's display name, so you don't need the adventure to look it up. The engine
    always fills it. It's empty only on an event loaded from a save written before the field
    existed."""
    objective_id: str
    """Which objective, as the id the adventure gave it."""
    name: str = ""
    """The objective's display label: the name its author wrote, or its id when the author wrote
    none. Like `quest_name`, it's empty only on an event loaded from a save written before the field
    existed."""
    narrative: str | None = None
    """The offer text the author wrote for this objective, or `None`. The journal has the same
    line."""


class ObjectiveCompletedEvent(Event):
    """One objective of a quest is done.

    Emitted by [`CompleteObjective`][osrlib.crawl.commands.CompleteObjective].
    Completing a hidden objective also reveals it, with no
    [`ObjectiveRevealedEvent`][osrlib.crawl.events.ObjectiveRevealedEvent], so the
    players may learn of an objective first from this event.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.quest.objective_completed"})
    """The only message code this event uses."""

    event_type: Literal["objective_completed"] = "objective_completed"
    """The event type, `objective_completed`, which identifies this class in serialized form."""
    code: str = "session.quest.objective_completed"
    """The message code, always `session.quest.objective_completed`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party sees its progress."""
    quest_id: str
    """The quest the objective belongs to."""
    quest_name: str = ""
    """The quest's display name. The engine always fills it. It's empty only on an event loaded from
    a save written before the field existed."""
    objective_id: str
    """Which objective was completed."""
    name: str = ""
    """The objective's display label: the name its author wrote, or its id when the author wrote
    none."""
    narrative: str | None = None
    """The progress text the author wrote, or `None`. The journal has the same line."""


class QuestCompletedEvent(Event):
    """A quest is finished.

    Emitted by [`CompleteQuest`][osrlib.crawl.commands.CompleteQuest]. Whether the
    quest is done is the referee's ruling: the engine checks that the quest is
    active, not that every objective was completed.

    A quest's rewards arrive after this event, as the events of the commands that pay
    them. When the quest concludes the adventure, an
    [`AdventureCompletedEvent`][osrlib.crawl.events.AdventureCompletedEvent] follows
    in the same result.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.quest.completed"})
    """The only message code this event uses."""

    event_type: Literal["quest_completed"] = "quest_completed"
    """The event type, `quest_completed`, which identifies this class in serialized form."""
    code: str = "session.quest.completed"
    """The message code, always `session.quest.completed`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party learns the quest is done."""
    quest_id: str
    """Which quest was completed."""
    name: str
    """The quest's display name."""
    narrative: str | None = None
    """The completion text the author wrote, or `None`. The journal has the same line."""


class AdventureCompletedEvent(Event):
    """The adventure is over and the party won.

    Emitted by [`CompleteQuest`][osrlib.crawl.commands.CompleteQuest] when the quest
    that concludes the adventure completes, right after that quest's
    [`QuestCompletedEvent`][osrlib.crawl.events.QuestCompletedEvent], with the same
    text. The session mode becomes `victory`, which is final: the session refuses play
    commands and never leaves that mode, so you can show a closing screen.

    A session that has already ended doesn't end twice. When a party that has already
    fallen completes the quest, the quest completes with no `AdventureCompletedEvent`,
    and the session stays in `game_over`.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.adventure.completed"})
    """The only message code this event uses."""

    event_type: Literal["adventure_completed"] = "adventure_completed"
    """The event type, `adventure_completed`, which identifies this class in serialized form."""
    code: str = "session.adventure.completed"
    """The message code, always `session.adventure.completed`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: it's the party's ending."""
    quest_id: str
    """The quest that concluded the adventure."""
    name: str = ""
    """That quest's display name. The engine always fills it. It's empty only on an event loaded
    from a save written before the field existed."""
    narrative: str | None = None
    """The quest's completion text, the same line as on its
    [`QuestCompletedEvent`][osrlib.crawl.events.QuestCompletedEvent], or `None`."""


class ItemsRemovedEvent(Event):
    """A referee took items or coins away from a party member.

    Emitted by [`RemoveItem`][osrlib.crawl.commands.RemoveItem] and
    [`RemoveCoins`][osrlib.crawl.commands.RemoveCoins]. What was removed has left the
    game, so update the member's inventory on screen. To find out why, read the `source` of
    the command that emitted it.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.items.removed"})
    """The only message code this event uses."""

    event_type: Literal["items_removed"] = "items_removed"
    """The event type, `items_removed`, which identifies this class in serialized form."""
    code: str = "session.items.removed"
    """The message code, always `session.items.removed`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party knows what it no longer has."""
    character_id: str
    """The member who lost the items or coins."""
    item_ids: tuple[str, ...] = ()
    """What was removed, in the same id form the acquisition used: a mundane item's catalog id once
    for each unit, and the instance id of a magic item or a valuable. Empty for a removal of coins."""
    coins_gp_value: int = 0
    """The coins removed, as their value in gold pieces. 0 for a removal of items."""


CRAWL_EVENT_CLASSES: tuple[type[Event], ...] = (
    PartyMovedEvent,
    LocationEnteredEvent,
    DoorEvent,
    ListenedEvent,
    DetectionRolledEvent,
    SearchCompletedEvent,
    TrapEvent,
    ItemAcquiredEvent,
    ItemConsumedEvent,
    ItemsDroppedEvent,
    ItemsLeftBehindEvent,
    ItemsGivenEvent,
    LightEvent,
    RestedEvent,
    FatigueEvent,
    ProvisionsEvent,
    WanderingCheckEvent,
    EncounterStartedEvent,
    EncounterGroupJoinedEvent,
    SurpriseRolledEvent,
    StanceChangedEvent,
    EvasionEvent,
    PursuitEvent,
    ExhaustionEvent,
    EncounterEndedEvent,
    BattleStartedEvent,
    BattleRoundEvent,
    SpellDeclaredEvent,
    GroupMovedEvent,
    MonsterFledEvent,
    MonstersLeftBehindEvent,
    MonsterDefeatedEvent,
    BattleEndedEvent,
    HoardGeneratedEvent,
    ItemUsedEvent,
    ItemIdentifiedEvent,
    CurseRevealedEvent,
    NpcPartySpawnedEvent,
    AdventureXpAwardEvent,
    TreasureSoldEvent,
    HealingPurchasedEvent,
    FlagSetEvent,
    MonstersSpawnedEvent,
    XpAwardedEvent,
    CharacterLeveledUpEvent,
    TimeAdvancedEvent,
    GameOverEvent,
    DiceRolledEvent,
    TriggerFiredEvent,
    JournalEntryAddedEvent,
    NoteRecordedEvent,
    QuestActivatedEvent,
    ObjectiveRevealedEvent,
    ObjectiveCompletedEvent,
    QuestCompletedEvent,
    AdventureCompletedEvent,
    ItemsRemovedEvent,
)
"""The event classes defined in this module, in the order they're declared.

Walk it to build a table of the crawl events, or to generate client types from their JSON
Schemas. For every event class, including the kernel events from
[`osrlib.core.events`][osrlib.core.events], use
[`ALL_EVENT_CLASSES`][osrlib.crawl.events.ALL_EVENT_CLASSES].
"""

ALL_EVENT_CLASSES: tuple[type[Event], ...] = (*KERNEL_EVENT_CLASSES, *CRAWL_EVENT_CLASSES)
"""Every event class the library can emit: the kernel classes first, then the ones in this module.

Walk it when you generate something from every event, like client types, a documentation table,
or a schema bundle. Each class has its event type in `model_fields["event_type"].default` and its
codes in `allowed_codes`.
"""

AnyEvent = Annotated[
    Union[*ALL_EVENT_CLASSES],
    Field(discriminator="event_type"),
]
"""Any event the library can emit, as a union pydantic discriminates on `event_type`.

Use it to type a value that can be an event of any class, and hand it to a
[`TypeAdapter`][pydantic.type_adapter.TypeAdapter] to get a tagged-union JSON Schema for a client
in another language. To parse one record, call
[`parse_any_event`][osrlib.crawl.events.parse_any_event] instead: it skips an event type this
version has no class for rather than raising on it.

```python
from pydantic import TypeAdapter

from osrlib.crawl.events import AnyEvent

schema = TypeAdapter(AnyEvent).json_schema()
print(schema["discriminator"]["propertyName"])
# event_type
```
"""


@cache
def _any_event_adapter() -> TypeAdapter:
    return TypeAdapter(AnyEvent)


@cache
def _known_event_types() -> frozenset[str]:
    return frozenset(variant.model_fields["event_type"].default for variant in ALL_EVENT_CLASSES)


def parse_any_event(data: Mapping[str, object]) -> Event | None:
    """Rebuild one serialized event, kernel or crawl, skipping types this version has no class for.

    Call it on records that came out of an event's `model_dump` or out of a save's event log, like
    a log you are replaying, a stream you received over a network, or a file you are analyzing.
    [`load_game`][osrlib.persistence.load_game] calls it for each entry in a save's event log, and
    keeps the raw record for an entry that returns `None`.

    For an `event_type` this version has no class for, it returns `None` rather than raising, so a
    log that has event types added in a newer osrlib still loads. Fields the class doesn't define are
    ignored. What you get back is an instance of the matching class, which you can pass to
    [`format_message`][osrlib.messages.format_message] like any other event.

    Args:
        data: One event as a mapping, from `model_dump` (in either Python or JSON mode) or from
            parsed JSON.

    Returns:
        The event, or `None` when its `event_type` belongs to no class in
        [`ALL_EVENT_CLASSES`][osrlib.crawl.events.ALL_EVENT_CLASSES].

    Raises:
        osrlib.errors.ContentValidationError: If the event type is known but the payload doesn't fit it, like
            a record missing a required field. The message includes pydantic's report.

    Examples:
        ```python
        from osrlib.crawl.events import PartyMovedEvent, parse_any_event

        event = PartyMovedEvent(code="exploration.party.moved", x=1, y=0, facing="east")
        record = event.model_dump()
        print(parse_any_event(record) == event)
        # True

        print(parse_any_event({"event_type": "teleported", "code": "exploration.party.teleported"}))
        # None
        ```
    """
    from osrlib.errors import ContentValidationError

    if data.get("event_type") not in _known_event_types():
        return None
    try:
        return _any_event_adapter().validate_python(data)
    except ValidationError as error:
        raise ContentValidationError(f"malformed event: {error}") from error
