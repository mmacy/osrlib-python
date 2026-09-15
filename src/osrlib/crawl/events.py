"""The crawl event catalog: typed records of everything a session does.

Every command you run through
[`GameSession.execute`][osrlib.crawl.session.GameSession.execute] comes back with a
[`CommandResult`][osrlib.crawl.commands.CommandResult] whose `events` tuple contains
instances of the classes here and of the kernel classes in
[`osrlib.core.events`][osrlib.core.events]. Read them in order, drop the ones your
reader may not see, and turn each one into a line with
[`format_message`][osrlib.messages.format_message] or with a renderer of your own
keyed on the event's `code`. The same objects accumulate on `GameSession.event_log`,
and [`save_game`][osrlib.persistence.save_game] writes them into a save.

Every event has a `code`, an `event_type`, and a `visibility`. The code is a message
code, dot-separated snake_case namespaced by subsystem (`exploration.door.opened`),
and it's what a renderer keys on. The event type is the wire discriminator that names
the class, so a serialized event rebuilds into the right one. The visibility says who
may see the event: `player` for what the table learns, `referee` for the rolls and
bookkeeping B/X keeps behind the screen, like a detection die or a wandering check. A
class that can report more than one outcome declares its whole code set in
`allowed_codes`, and an instance uses one of them.

An event never contains English prose written by the engine. It contains facts and a
code, so a front end can localize, and a narrator can write its own line from the
same facts. The exception is a `narrative` field: that is text the adventure's author
wrote, passed through as content.

[`CRAWL_EVENT_CLASSES`][osrlib.crawl.events.CRAWL_EVENT_CLASSES] is the registry of
the classes in this module, and
[`ALL_EVENT_CLASSES`][osrlib.crawl.events.ALL_EVENT_CLASSES] adds the kernel ones in
front of it. [`AnyEvent`][osrlib.crawl.events.AnyEvent] is the union of all of them
for typing and JSON Schema, and
[`parse_any_event`][osrlib.crawl.events.parse_any_event] turns a serialized record
back into an event.

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
    """The party moved a cell or turned in place, and here is where it now stands.

    Emitted by [`MoveParty`][osrlib.crawl.commands.MoveParty] with the cell it
    stepped into, and by [`TurnParty`][osrlib.crawl.commands.TurnParty] with the
    unchanged cell and the new facing. It's what a first-person front end redraws
    from.

    A move that a wall, a closed door, or the edge of the map stops is a rejection
    (`exploration.move.blocked`) rather than an event: walking into a wall changes
    nothing about the game, so nothing is reported.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.party.moved", "exploration.party.turned"})
    """`exploration.party.moved` for a step into a new cell, `exploration.party.turned` for a
    turn on the spot."""

    event_type: Literal["party_moved"] = "party_moved"
    """The wire discriminator, `party_moved`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: where the party stands is the party's own business."""
    x: int
    """The column the party is in after the command, counting from zero at the level's west edge."""
    y: int
    """The row the party is in after the command, counting from zero at the level's north edge."""
    facing: str
    """The direction the party now faces, as the lowercase value of a
    [`Direction`][osrlib.crawl.dungeon.Direction] (`"north"`, `"east"`, `"south"`, `"west"`). A
    move faces the way it went, so this changes on a step as well as on a turn."""


class LocationEnteredEvent(Event):
    """The party crossed into a new area, level, dungeon, or town.

    Emitted whenever the party's location changes at one of those four scales:
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] on arrival at a dungeon,
    [`UseStairs`][osrlib.crawl.commands.UseStairs] on a level or dungeon change,
    [`MoveParty`][osrlib.crawl.commands.MoveParty] on stepping into a keyed area,
    [`TravelToTown`][osrlib.crawl.commands.TravelToTown] on arriving back in town,
    and [`PlaceParty`][osrlib.crawl.commands.PlaceParty] when a referee puts the
    party somewhere.

    Which fields are filled depends on the scale, because an area id is unique only
    within its level: an area entry names the area, its level number, and its dungeon,
    a level or dungeon entry names the dungeon in `location_id` with the level number
    beside it, and a town entry names neither. A level or dungeon entry also says how the
    party got there, in `via` and, for a transition it took, `transition_ref`, so a line
    written from the event alone can say the party climbed rather than descended. Use it
    to swap the screen's header, and read the text the party can see from
    [`GameSession.view`][osrlib.crawl.session.GameSession.view].
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.location.entered"})
    """The only message code this event uses."""

    event_type: Literal["location_entered"] = "location_entered"
    """The wire discriminator, `location_entered`."""
    code: str = "exploration.location.entered"
    """The message code, always `exploration.location.entered`. The scale is in `location_kind`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: arriving somewhere is the first thing the table is told."""
    location_kind: str
    """Which scale was crossed: `"area"`, `"level"`, `"dungeon"`, or `"town"`."""
    location_id: str
    """What was entered: the area id for an area entry, the dungeon id for a level or dungeon
    entry, and `"town"` for the town."""
    level_number: int | None = None
    """The level the party is on, for area, level, and dungeon entries, and `None` for town."""
    dungeon_id: str | None = None
    """The dungeon the area belongs to, filled on area entries only. The other kinds already name
    the dungeon in `location_id`."""
    narrative: str | None = None
    """The success text the adventure's author wrote on the gate that was crossed, when there was
    one, else `None`. A gate is the condition an author puts on a transition, like a door that
    opens only for a key. This is content rather than prose the engine wrote: the event still has
    its code and its facts, and [`format_message`][osrlib.messages.format_message] appends this line
    after the templated one."""
    via: str | None = None
    """How the party got there, on level and dungeon entries: the kind of the transition it took
    (`"stairs_down"`, `"stairs_up"`, `"trapdoor"`, or `"chute"`), `"trap"` for a trap that dropped
    the party through the floor, `"entrance"` for [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon],
    and `"placed"` for [`PlaceParty`][osrlib.crawl.commands.PlaceParty]. It is `None` on area and
    town entries, and on an event loaded from a save written before the field existed."""
    transition_ref: str | None = None
    """The cell of the authored transition the party took, as
    [`cell_ref`][osrlib.crawl.dungeon.cell_ref] gives it, so a consumer can find the
    [`TransitionSpec`][osrlib.crawl.dungeon.TransitionSpec] and the gate on it. Filled on a level or
    dungeon entry made through [`UseStairs`][osrlib.crawl.commands.UseStairs], `None` otherwise."""


class DoorEvent(Event):
    """A door changed state, named by the cell it borders and the side it sits on.

    Emitted by the door commands,
    [`OpenDoor`][osrlib.crawl.commands.OpenDoor],
    [`CloseDoor`][osrlib.crawl.commands.CloseDoor],
    [`ForceDoor`][osrlib.crawl.commands.ForceDoor],
    [`PickLock`][osrlib.crawl.commands.PickLock], and
    [`WedgeDoor`][osrlib.crawl.commands.WedgeDoor], and by the commands that leave a
    cell or a level, because doors the party opened swing shut behind it. A referee's
    [`SetDoorState`][osrlib.crawl.commands.SetDoorState] emits it too, at referee
    visibility, since a door set open from behind the screen isn't something the
    party watched happen.

    A door belongs to the edge between two cells, so the same door can be named from
    either side. Redraw from `x`, `y`, and `direction` rather than tracking door
    identity yourself.
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
    """The wire discriminator, `door`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility by default. The referee's `SetDoorState` overrides it to referee."""
    x: int
    """The column of the cell the door edge is named from."""
    y: int
    """The row of the cell the door edge is named from."""
    direction: str
    """Which side of that cell the door is on, as a lowercase
    [`Direction`][osrlib.crawl.dungeon.Direction] value."""
    character_id: str | None = None
    """The member who acted, for a force, a stuck attempt, or a picked lock, and `None` when the party
    acted as one or when nobody did, as with a door swinging shut."""
    narrative: str | None = None
    """The success text the author wrote on the door's gate, when opening it satisfied one, else
    `None`. Content rather than engine prose: the event still has its code and its facts, and the
    default formatter appends this line after the templated one."""


class ListenedEvent(Event):
    """Someone listened at a door, and either heard something or heard nothing.

    Emitted by [`ListenAtDoor`][osrlib.crawl.commands.ListenAtDoor], after the
    referee-visibility [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent]
    that reports the die.

    Silence is ambiguous, and it's meant to stay that way. Undead make no
    noise, and the roll happens whether or not anything is on the other side, so
    `exploration.listen.silent` tells the party nothing about what is there. Render
    it as an empty result, not as an all-clear.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.listen.heard", "exploration.listen.silent"})
    """`exploration.listen.heard` when noise came through, `exploration.listen.silent` when
    none did."""

    event_type: Literal["listened"] = "listened"
    """The wire discriminator, `listened`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: what a character heard is theirs to know, while the die behind it is
    not."""
    character_id: str
    """The member who listened."""
    direction: str
    """The side of the party's cell that was listened at, as a lowercase
    [`Direction`][osrlib.crawl.dungeon.Direction] value."""


class DetectionRolledEvent(Event):
    """A referee-rolled detection die: a search, a listen, a lock, or a trap trigger.

    Emitted alongside the player-facing result of
    [`Search`][osrlib.crawl.commands.Search],
    [`ListenAtDoor`][osrlib.crawl.commands.ListenAtDoor],
    [`PickLock`][osrlib.crawl.commands.PickLock],
    [`InspectTreasure`][osrlib.crawl.commands.InspectTreasure], and
    [`RemoveTreasureTrap`][osrlib.crawl.commands.RemoveTreasureTrap], and whenever a
    trap gets its chance to spring.

    The die is rolled whether or not there's anything to find, so that a failure and
    an empty cell look the same from the table. That is why this event is referee
    visibility: showing it to players would leak the answer the roll was hiding. A
    referee front end, or an LLM running the game, reads it to know what actually
    happened.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.detection.rolled"})
    """The only message code this event uses."""

    event_type: Literal["detection_rolled"] = "detection_rolled"
    """The wire discriminator, `detection_rolled`."""
    code: str = "exploration.detection.rolled"
    """The message code, always `exploration.detection.rolled`."""
    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: the roll is the part B/X keeps behind the screen."""
    character_id: str | None = None
    """The member who rolled, or `None` for a check nobody made, like a trap's own chance to
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
    """Whether the check succeeded. A failed check and a nothing-there cell are deliberately
    indistinguishable from the player's side."""


class SearchCompletedEvent(Event):
    """A search of the party's cell finished, naming whatever it turned up.

    Emitted by [`Search`][osrlib.crawl.commands.Search] and by
    [`InspectTreasure`][osrlib.crawl.commands.InspectTreasure] once the roll has been
    made, after the referee-visibility
    [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent].

    An empty result means the searcher found nothing, which isn't the same as there
    being nothing: each character gets one attempt per cell per kind, and another
    character may still find it.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.search.found", "exploration.search.nothing"})
    """`exploration.search.found` when `found` is non-empty, `exploration.search.nothing`
    otherwise."""

    event_type: Literal["search_completed"] = "search_completed"
    """The wire discriminator, `search_completed`."""
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
    from the searched cell names the door's direction in a third segment,
    `"room_trap:<area id>:<direction>"`, and one found inside its own area has none. A found
    secret door becomes passable, and a found trap no longer springs on the party."""


class TrapEvent(Event):
    """A trap did something the party can perceive: it fired, or was found, or was dealt with.

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
    fires resolves at once, so its damage and saves follow in the same result as
    kernel events.

    A trap the party doesn't know about that fails to fire produces no event here.
    Only its die goes into the referee-visibility
    [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent], so an
    uneventful step looks like a step on safe ground.

    What a find is worth depends on the kind of trap. A found room trap never springs,
    at its area's edge or at one of its doors: the party walks around the known pit and
    stands clear of the known blade, so it rolls no die and emits nothing further. A
    found treasure trap still rolls its 2-in-6 on every
    [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure] until a thief takes it out
    with [`RemoveTreasureTrap`][osrlib.crawl.commands.RemoveTreasureTrap], because
    finding a treasure trap is not defeating it. That is why `exploration.trap.safe`, a
    known trap's trigger resolving without springing, appears on a cache alone.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {"exploration.trap.sprung", "exploration.trap.safe", "exploration.trap.found", "exploration.trap.removed"}
    )
    """`exploration.trap.sprung` when a trap goes off, `.found` when a search or inspection
    reveals one, `.removed` when a thief disarms one, and `.safe` when a trap the party already
    knows about got its chance and didn't fire, which only a treasure trap does."""

    event_type: Literal["trap"] = "trap"
    """The wire discriminator, `trap`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party feels the trap go off, or sees the one it found."""
    trap_ref: str
    """Which trap this is, as `"<dungeon id>:<level number>:<area or feature id>"`. The session
    records the same reference as found, sprung, or removed, so a trap is reported once and
    stays dealt with."""
    character_id: str | None = None
    """The member who set it off, found it, or removed it, or `None` when the trap fired on the
    party as a whole."""
    direction: str | None = None
    """The direction of the door a room trap was found through, from the searched cell, as a
    [`Direction`][osrlib.crawl.dungeon.Direction] value. Set for that find alone: it is `None` on a
    room trap found inside its own area, on a treasure trap found on a cache, and on every code but
    `exploration.trap.found`."""


class ItemAcquiredEvent(Event):
    """Items or coins landed in a character's inventory.

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
    """The wire discriminator, `item_acquired`."""
    code: str = "exploration.item.acquired"
    """The message code, always `exploration.item.acquired`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party knows what it picked up."""
    character_id: str
    """The member whose pack the goods went into."""
    item_ids: tuple[str, ...] = ()
    """What was acquired, one entry per item: a catalog id for mundane gear, repeated when several
    of the same thing arrived, and a session-scoped instance id for a valuable or a magic item, so
    an unidentified item's true nature stays hidden."""
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
    """One carried item was used up: a toll paid, a spike driven home.

    Emitted when a gate's condition takes the item it names, which happens on
    [`OpenDoor`][osrlib.crawl.commands.OpenDoor],
    [`ForceDoor`][osrlib.crawl.commands.ForceDoor], and
    [`UseStairs`][osrlib.crawl.commands.UseStairs], and by
    [`WedgeDoor`][osrlib.crawl.commands.WedgeDoor] for the iron spike it drives.

    It says the item is gone. It isn't the event for a potion drunk or a scroll
    read, which are [`ItemUsedEvent`][osrlib.crawl.events.ItemUsedEvent].
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.item.consumed"})
    """The only message code this event uses."""

    event_type: Literal["item_consumed"] = "item_consumed"
    """The wire discriminator, `item_consumed`."""
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
    [`Evade`][osrlib.crawl.commands.Evade] when the party throws treasure or food to
    a pursuer to buy time.

    What lands on a cell goes into that cell's drop pile, which
    [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure] can pick back up. What is
    scattered during a flight is gone.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.item.dropped"})
    """The only message code this event uses."""

    event_type: Literal["items_dropped"] = "items_dropped"
    """The wire discriminator, `items_dropped`."""
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
    so a party that comes back lighter can take another `TakeTreasure` and get the
    rest.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.item.left_behind"})
    """The only message code this event uses."""

    event_type: Literal["items_left_behind"] = "items_left_behind"
    """The wire discriminator, `items_left_behind`."""
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

    Emitted by [`GiveItems`][osrlib.crawl.commands.GiveItems]. Update both characters
    on your inventory screen when it arrives: nothing enters or leaves the party, so
    the party's total is unchanged.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.item.given"})
    """The only message code this event uses."""

    event_type: Literal["items_given"] = "items_given"
    """The wire discriminator, `items_given`."""
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
    also emits the expiry form whenever the clock runs a light out, which can happen
    inside any command that passes time.

    Light gates most of exploration: searching, reading, and seeing at all need it
    unless a character has infravision. Read the party's current state from
    [`GameSession.party_light`][osrlib.crawl.session.GameSession.party_light] rather
    than adding these events up.
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
    """The wire discriminator, `light`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party watches the light go. The ledger's own expiry record behind it
    is referee visibility, and the session translates it into this player-facing form."""
    character_id: str | None = None
    """The member carrying the source, or `None` when the light belongs to no one in the party."""
    source: str
    """What is burning: `"torch"`, `"lantern"`, `"oil"` for a lit pool, `"sword"` for a blade that
    glows, or the effect kind for a light cast as a spell."""


class RestedEvent(Event):
    """A rest finished, or was interrupted before it could.

    Emitted by [`Rest`][osrlib.crawl.commands.Rest]. A completed rest clears the
    unrested-fatigue penalty, credits running exhaustion, and, for a full day, heals
    naturally. An interrupted one does none of that, because something wandered in.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.rest.rested", "exploration.rest.interrupted"})
    """`exploration.rest.rested` when the rest ran to its end, `exploration.rest.interrupted` when
    a wandering encounter cut it short."""

    event_type: Literal["rested"] = "rested"
    """The wire discriminator, `rested`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party knows whether it got its rest."""
    kind: str
    """How long the party tried to rest: `"turn"` for the one-turn breather the dungeon rule
    calls for, `"night"`, or `"day"`."""


class FatigueEvent(Event):
    """The party picked up the unrested penalty, or shook it off.

    B/X asks a party to rest one turn in every six while it's in a dungeon. A party
    that doesn't gets a penalty until it does, and these two codes are when the
    penalty lands and when it lifts.

    Emitted while exploring, by any command that crosses a turn boundary, and
    recovered by [`Rest`][osrlib.crawl.commands.Rest].
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.fatigue.gained", "exploration.fatigue.recovered"})
    """`exploration.fatigue.gained` when the party misses its rest,
    `exploration.fatigue.recovered` when a rest clears it."""

    event_type: Literal["fatigue"] = "fatigue"
    """The wire discriminator, `fatigue`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party feels it, and the penalty is on their sheets."""


class ProvisionsEvent(Event):
    """A day passed, and a character either ate and drank or went without.

    Emitted once per living member per kind whenever the clock crosses a day
    boundary, which can happen inside any command that passes time, and most often
    inside a [`Rest`][osrlib.crawl.commands.Rest].

    Going short starts a deprivation count on that member. Whether that count brings
    a penalty depends on the ruleset option `deprivation_penalties`, described in
    [the adaptations register](https://mmacy.github.io/osrlib-python/adaptations/),
    the page that lists where osrlib settles an ambiguous rule or supplies a default.
    In town nobody ever runs short.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {"exploration.provisions.consumed", "exploration.provisions.short"}
    )
    """`exploration.provisions.consumed` when the day's food or water was there,
    `exploration.provisions.short` when it was not."""

    event_type: Literal["provisions"] = "provisions"
    """The wire discriminator, `provisions`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: an empty pack is the party's problem to solve."""
    character_id: str
    """The member whose rations or water this was."""
    kind: str
    """Which supply the event is about: `"food"` or `"water"`. Each member gets one of each per
    day."""


class WanderingCheckEvent(Event):
    """The wandering-monster cadence came round and the referee rolled for it.

    Emitted while the party is in a dungeon, by any command that crosses the turn
    the cadence lands on, most often [`MoveParty`][osrlib.crawl.commands.MoveParty]
    or [`Rest`][osrlib.crawl.commands.Rest]. When the check hits, the encounter
    opens in the same result and the command that was spending time stops there.

    It's referee visibility because the party has no way of knowing a check was
    made, only of meeting what it produced.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.wandering.checked"})
    """The only message code this event uses."""

    event_type: Literal["wandering_check"] = "wandering_check"
    """The wire discriminator, `wandering_check`."""
    code: str = "exploration.wandering.checked"
    """The message code, always `exploration.wandering.checked`."""
    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: the check is made behind the screen, and only its result walks in."""
    chance: int
    """The X-in-6 chance the check needed, after the level's own rate and any adjustment like
    the lower chance while resting."""
    roll: int | None = None
    """The d6 that was rolled, or `None` when the chance came out at zero and no die was
    rolled."""
    encounter: bool
    """Whether the check produced an encounter. When it did, the encounter's own events follow in
    the same result."""


class EncounterStartedEvent(Event):
    """The party has met something, and here is what it sees.

    Emitted when an encounter opens, whichever way it did: walking into a keyed area
    with [`MoveParty`][osrlib.crawl.commands.MoveParty],
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] or
    [`UseStairs`][osrlib.crawl.commands.UseStairs] arriving on one, a wandering
    check, or a referee's [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters] or
    [`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty]. The session mode becomes
    `encounter`, where the party can talk, run, wait, or fight.

    It contains only what the party can see: a name, a count, a distance.
    The dice behind the meeting are reported on
    [`SurpriseRolledEvent`][osrlib.crawl.events.SurpriseRolledEvent] and on the
    reaction roll, both at referee visibility. The two surprise outcomes are here,
    because being caught off guard is something the party lives through.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"encounter.started"})
    """The only message code this event uses."""

    event_type: Literal["encounter_started"] = "encounter_started"
    """The wire discriminator, `encounter_started`."""
    code: str = "encounter.started"
    """The message code, always `encounter.started`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: this is the moment the table is told what walked in."""
    monster_name: str
    """The label of the first group in the encounter, as the party would name it. An NPC
    adventuring party appears under a label like `"Basic Adventurers"`, with its roster kept
    behind the screen."""
    count: int
    """How many creatures there are across every group in the encounter."""
    distance_feet: int
    """How far away they are, in feet, when the encounter opens. Battle starts from this distance
    and closes from there."""
    party_surprised: bool = False
    """Whether the party was caught off guard, which costs it the first beat of the fight."""
    monsters_surprised: bool = False
    """Whether the monsters were caught off guard, which gives the party a free round if the fight
    starts."""


class SurpriseRolledEvent(Event):
    """One side's surprise die, rolled behind the screen.

    Emitted twice when an encounter opens, once for each side, before
    [`EncounterStartedEvent`][osrlib.crawl.events.EncounterStartedEvent] reports the
    outcomes to the table.

    A side that cannot be surprised doesn't roll: a party that already knows what is
    in the room, or monsters that heard the party coming or can see its light.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"encounter.surprise.rolled"})
    """The only message code this event uses."""

    event_type: Literal["surprise_rolled"] = "surprise_rolled"
    """The wire discriminator, `surprise_rolled`."""
    code: str = "encounter.surprise.rolled"
    """The message code, always `encounter.surprise.rolled`."""
    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: the die is the referee's, and the result reaches the table through the
    encounter event."""
    side: str
    """Which side rolled: `"party"` or `"monsters"`."""
    threshold: int
    """The number on a d6 at or under which that side is surprised. It is 2 as a rule, and 3 for a
    party moving in the dark without infravision."""
    roll: int | None = None
    """The d6 that came up, or `None` when this side never had to roll."""
    surprised: bool
    """Whether this side was surprised."""


class StanceChangedEvent(Event):
    """The monsters' attitude toward the party changed, as behavior the party can read.

    Emitted when an encounter opens with its first reaction, when
    [`Parley`][osrlib.crawl.commands.Parley] talks the monsters into a different
    mood, when an uncertain stance resolves on the next beat, and when
    [`TurnUndead`][osrlib.crawl.commands.TurnUndead] settles the matter by making the
    survivors hostile.

    The 2d6 reaction roll behind it is a kernel event at referee visibility. What
    reaches the party is how the creatures are acting.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"encounter.stance.changed"})
    """The only message code this event uses."""

    event_type: Literal["stance_changed"] = "stance_changed"
    """The wire discriminator, `stance_changed`."""
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
    """The party tried to get away, and either did or has a pursuit on its hands.

    Emitted by [`Evade`][osrlib.crawl.commands.Evade]. Getting clear at once ends the
    encounter there. Otherwise a chase begins, and its beats arrive as
    [`PursuitEvent`][osrlib.crawl.events.PursuitEvent]s, starting in this same
    result.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"encounter.evasion.succeeded", "encounter.evasion.pursuit"})
    """`encounter.evasion.succeeded` when the party is away clean, `encounter.evasion.pursuit`
    when something gives chase."""

    event_type: Literal["evasion"] = "evasion"
    """The wire discriminator, `evasion`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party knows whether it's being followed."""


class PursuitEvent(Event):
    """One beat of a chase: the gap, a distraction taken, an escape, or a capture.

    Emitted by [`Evade`][osrlib.crawl.commands.Evade] once the chase is on, and by
    [`Wait`][osrlib.crawl.commands.Wait] and
    [`DropItems`][osrlib.crawl.commands.DropItems] for each further beat, which is
    how the party keeps running or throws something behind it.

    A capture opens battle at once, and an escape ends the encounter. A chase that
    runs long enough tires the party out, which arrives as
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
    """`encounter.pursuit.round` for a beat where the chase goes on, `.distracted` when dropped
    treasure or food stops the pursuers, `.escaped` when the party gets clear, and `.caught` when
    the pursuers close to arm's length and battle opens."""

    event_type: Literal["pursuit"] = "pursuit"
    """The wire discriminator, `pursuit`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party can see how close the chase is."""
    round: int
    """Which beat of the chase this is, counting from one."""
    gap_feet: int
    """How far ahead the party is, in feet, at the end of this beat. It never goes below zero, and
    at five feet or less the pursuers have caught up."""


class ExhaustionEvent(Event):
    """The party ran itself ragged, or has rested long enough to recover.

    Running flat out for a long chase costs a party 2 on its attack and damage rolls
    and makes it 2 easier to hit, until it rests. Emitted by
    [`Evade`][osrlib.crawl.commands.Evade] and [`Wait`][osrlib.crawl.commands.Wait]
    when a chase runs its full length, and recovered by
    [`Rest`][osrlib.crawl.commands.Rest] once three turns of rest have been
    credited.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {"encounter.exhaustion.gained", "encounter.exhaustion.recovered"}
    )
    """`encounter.exhaustion.gained` when the running catches up with the party,
    `encounter.exhaustion.recovered` when enough rest clears it."""

    event_type: Literal["exhaustion"] = "exhaustion"
    """The wire discriminator, `exhaustion`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the penalty is on the party's own sheets."""


class EncounterEndedEvent(Event):
    """The encounter is over, however it went.

    Emitted once the last group has been dealt with: beaten, evaded, escaped from, or
    driven off. The session goes back to `exploring`, and the clock owes at least one
    full turn, so time passes with this event even when the fight was short.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"encounter.ended"})
    """The only message code this event uses."""

    event_type: Literal["encounter_ended"] = "encounter_ended"
    """The wire discriminator, `encounter_ended`."""
    code: str = "encounter.ended"
    """The message code, always `encounter.ended`. The ending is in `outcome`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party knows the encounter is behind it."""
    outcome: str
    """How it ended: `"victory"` when the monsters were beaten, `"evaded"` when the party got away
    before or during a fight, `"escaped"` when a chase ran out, or `"turned"` when undead were
    driven off."""


class BattleStartedEvent(Event):
    """Blows have been struck: the encounter became a battle.

    Emitted when a fight opens, by [`EngageBattle`][osrlib.crawl.commands.EngageBattle]
    when the party attacks, and on its own when the monsters do, which can happen
    the moment an encounter opens, when a parley goes wrong, when undead are
    presented with a holy symbol, or when a chase ends in capture.

    The session mode becomes `battle`, where
    [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound] is the only play
    command the session accepts. Positions are no longer cells: each monster group
    has a distance from the party, and closing or pulling back moves that number.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"battle.started"})
    """The only message code this event uses."""

    event_type: Literal["battle_started"] = "battle_started"
    """The wire discriminator, `battle_started`."""
    code: str = "battle.started"
    """The message code, always `battle.started`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party is in it."""


class BattleRoundEvent(Event):
    """A battle round began.

    Emitted at the top of every
    [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound], before the
    declarations post and initiative is rolled. It's the marker a transcript can
    group the rest of the round's events under.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"battle.round.started"})
    """The only message code this event uses."""

    event_type: Literal["battle_round"] = "battle_round"
    """The wire discriminator, `battle_round`."""
    code: str = "battle.round.started"
    """The message code, always `battle.round.started`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: everyone at the table knows which round it is."""
    round: int
    """Which round of this battle is starting, counting from one."""


class SpellDeclaredEvent(Event):
    """Somebody declared a spell, before anyone knows who acts first.

    Emitted by [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound] for
    each caster who declared one, party member or NPC alike, at the top of the round.

    B/X has declarations posted before initiative on purpose: a caster who takes
    damage before their turn loses the spell, and the other side can act on knowing
    what is coming. The disruption itself arrives later in the round as a kernel
    event.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"battle.spell.declared"})
    """The only message code this event uses."""

    event_type: Literal["spell_declared"] = "spell_declared"
    """The wire discriminator, `spell_declared`."""
    code: str = "battle.spell.declared"
    """The message code, always `battle.spell.declared`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: a declaration is made out loud at the table."""
    caster_id: str
    """Who is casting: a party member's id, or an NPC adventurer's."""
    spell_id: str
    """Which spell was declared, as its catalog id."""
    reversed: bool = False
    """Whether the reversed form was declared, for a spell that has one."""


class GroupMovedEvent(Event):
    """A monster group's distance from the party changed.

    Emitted by [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound]
    whenever the range track moves: the party closing on a group or backing away
    from every group, monsters closing to strike, and a broken group running for the
    exit.

    Distance decides what can reach what, so this is the event a battle screen
    redraws its ranks from.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"battle.group.moved"})
    """The only message code this event uses."""

    event_type: Literal["group_moved"] = "group_moved"
    """The wire discriminator, `group_moved`."""
    code: str = "battle.group.moved"
    """The message code, always `battle.group.moved`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party watches them come on or draw off."""
    group_id: str
    """Which group moved, as its encounter group id."""
    distance_feet: int
    """How far that group now stands from the party, in feet, after the move. Melee happens at the
    track's shortest step."""


class MonsterFledEvent(Event):
    """A monster group broke and ran.

    Emitted by [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound] when
    a group fails a morale check, and at the opening of a battle for a group whose
    morale is so low it never fights at all. A running group keeps moving away each
    round and is gone once it's far enough out. Its members still count as defeated
    for the adventure's experience award.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"battle.side.fled"})
    """The only message code this event uses."""

    event_type: Literal["monster_fled"] = "monster_fled"
    """The wire discriminator, `monster_fled`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party sees them break."""
    group_id: str
    """Which group broke, as its encounter group id."""


class MonstersLeftBehindEvent(Event):
    """A group that ran left its helpless members lying where they were.

    Emitted by [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound] when
    a broken group has members who cannot run, because they are asleep, paralysed, or
    held.

    The runners keep the original group and go on fleeing, and the ones left behind
    become a new group at the distance the side broke from, so the party can finish
    them, take what they carry, or walk past.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"battle.group.left_behind"})
    """The only message code this event uses."""

    event_type: Literal["monsters_left_behind"] = "monsters_left_behind"
    """The wire discriminator, `monsters_left_behind`."""
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
    """One monster is out of the fight, and here is what it was worth.

    Emitted once per defeated creature when the encounter concludes, which follows
    the last [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound] or an
    escape that leaves the fight behind.

    These are the entries the adventure's experience award adds up, and the award
    itself arrives later, as
    [`AdventureXpAwardEvent`][osrlib.crawl.events.AdventureXpAwardEvent] on the trip
    back to town, or at once when the ruleset awards immediately.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"battle.monster.defeated"})
    """The only message code this event uses."""

    event_type: Literal["monster_defeated"] = "monster_defeated"
    """The wire discriminator, `monster_defeated`."""
    code: str = "battle.monster.defeated"
    """The message code, always `battle.monster.defeated`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party sees them fall or flee."""
    monster_id: str
    """The session id of the creature that was defeated."""
    template_id: str
    """What it was: a monster catalog id, or `"npc:<class id>"` for a defeated NPC adventurer."""
    outcome: str
    """How it went out: `"slain"`, or `"routed"` when it fled or was turned."""
    xp: int
    """What it's worth: the monster catalog's printed award, or the level-based award for an NPC
    adventurer."""


class BattleEndedEvent(Event):
    """The battle is over: won, quit, or lost.

    Emitted by [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound], and
    by [`EngageBattle`][osrlib.crawl.commands.EngageBattle] when the opposition
    breaks before the first exchange.

    A victory ends the encounter with it. A retreat may leave the party in a chase
    rather than clear of the fight. A defeat means nobody is left standing, and a
    [`GameOverEvent`][osrlib.crawl.events.GameOverEvent] closes the same result.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {"battle.ended.victory", "battle.ended.fled", "battle.ended.defeat"}
    )
    """`battle.ended.victory` when no opposition is left fighting, `battle.ended.fled` when the
    party pulled out, and `battle.ended.defeat` when the party fell."""

    event_type: Literal["battle_ended"] = "battle_ended"
    """The wire discriminator, `battle_ended`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the fight is the party's own."""


class HoardGeneratedEvent(Event):
    """Treasure was rolled up and placed, before anyone has found it.

    Emitted when the party first enters an area whose author declared treasure, and
    when a keyed encounter's monsters bring their lair hoard with them. The goods go
    into a cache the party has to find and open with
    [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure].

    It's referee visibility, and it itemizes everything: telling the players would
    be telling them what is in the room. A referee front end, or an LLM running the
    game, reads it to know what is there.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"treasure.hoard.generated"})
    """The only message code this event uses."""

    event_type: Literal["hoard_generated"] = "hoard_generated"
    """The wire discriminator, `hoard_generated`."""
    code: str = "treasure.hoard.generated"
    """The message code, always `treasure.hoard.generated`."""
    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: the contents are the answer to a question the party hasn't asked
    yet."""
    cache_ref: str
    """The id of the cache the treasure went into, allocated by the session as `cache-NNNN`. The
    party reaches it through the cell it sits on, not through this id."""
    treasure_types: tuple[str, ...] = ()
    """The treasure-type letters that were rolled, one entry per roll, so a hoard rolled from two
    letters lists both."""
    coins_gp_value: int = 0
    """The coins in the hoard, as their value in gold pieces."""
    valuable_ids: tuple[str, ...] = ()
    """The session-scoped instance ids of the gems and jewellery in the hoard."""
    magic_item_ids: tuple[str, ...] = ()
    """The session-scoped instance ids of the magic items in the hoard."""


class ItemUsedEvent(Event):
    """A magic item was used: a potion drunk, a scroll read, a device fired.

    Emitted by [`UseItem`][osrlib.crawl.commands.UseItem] out of combat and by
    [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound] for an item used
    in a fight. Whatever the item does follows in the same result as kernel events.

    Using an item for the first time is what identifies it, so an
    [`ItemIdentifiedEvent`][osrlib.crawl.events.ItemIdentifiedEvent] and possibly a
    [`CurseRevealedEvent`][osrlib.crawl.events.CurseRevealedEvent] come just before
    this one. Firing a device that has nothing left in it is a rejection
    (`items.device.inert`) rather than an event, because it costs the party nothing.
    Charges never appear on any event: how many uses an item has left is the
    referee's to know.
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
    another still running, which loses both and lays the drinker out for three turns,
    `items.scroll.read` for a scroll, `items.scroll.cursed` for one whose script was baneful, and
    `items.device.activated` for a rod, staff, wand, or other device."""

    event_type: Literal["item_used"] = "item_used"
    """The wire discriminator, `item_used`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party watched it happen."""
    character_id: str
    """The member who used the item."""
    instance_id: str
    """The session-scoped id of the item used, never its template id, so an item the party hasn't
    identified keeps its secret."""
    manual: tuple[str, ...] = ()
    """The item's printed text, for the items whose effect the engine doesn't resolve, like a
    treasure map or a curse the game narrates. Empty when the engine resolved the effect itself.
    Show these lines to the table and adjudicate them yourself."""


class ItemIdentifiedEvent(Event):
    """A magic item gave itself away, and the party now knows what it is.

    Emitted the first time an item is used in a way that reveals it, which happens
    inside [`UseItem`][osrlib.crawl.commands.UseItem],
    [`EquipItem`][osrlib.crawl.commands.EquipItem],
    [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound], and the
    referee's [`IdentifyItem`][osrlib.crawl.commands.IdentifyItem].

    Before this, the item's `instance_id` is all any player-visible event named.
    After it, the party can be shown the template's name.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"items.item.identified"})
    """The only message code this event uses."""

    event_type: Literal["item_identified"] = "item_identified"
    """The wire discriminator, `item_identified`."""
    code: str = "items.item.identified"
    """The message code, always `items.item.identified`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: this is the moment the party learns what it has."""
    instance_id: str
    """The session-scoped id of the item, the same id the earlier events used."""
    template_id: str
    """What it turned out to be, as its magic item catalog id."""


class CurseRevealedEvent(Event):
    """A cursed item showed its true nature, and won't let go.

    Emitted alongside [`ItemIdentifiedEvent`][osrlib.crawl.events.ItemIdentifiedEvent]
    the first time a cursed item is used or worn. From here the bearer is stuck with
    it until something removes the curse.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"items.curse.revealed"})
    """The only message code this event uses."""

    event_type: Literal["curse_revealed"] = "curse_revealed"
    """The wire discriminator, `curse_revealed`."""
    code: str = "items.curse.revealed"
    """The message code, always `items.curse.revealed`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the bearer finds out the hard way."""
    character_id: str
    """The member the curse has attached itself to."""
    instance_id: str
    """The session-scoped id of the cursed item."""
    template_id: str
    """What the item is, as its magic item catalog id."""


class NpcPartySpawnedEvent(Event):
    """An NPC adventuring party was rolled up and put on the board.

    Emitted by the referee's
    [`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty] and by a wandering roll
    that comes up adventurers, before the encounter opens.

    It's referee visibility and contains the whole roster. What the party sees is the
    [`EncounterStartedEvent`][osrlib.crawl.events.EncounterStartedEvent], which names
    them as adventurers and gives a count. Their classes and levels are something to
    find out by talking or by fighting.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"encounter.npc_party.spawned"})
    """The only message code this event uses."""

    event_type: Literal["npc_party_spawned"] = "npc_party_spawned"
    """The wire discriminator, `npc_party_spawned`."""
    code: str = "encounter.npc_party.spawned"
    """The message code, always `encounter.npc_party.spawned`."""
    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: the roster is what the party doesn't get to read off a sheet."""
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
    """The party's alignment, as a lowercase [`Alignment`][osrlib.core.alignment.Alignment] value.
    It decides how they are played more than how they roll."""


class AdventureXpAwardEvent(Event):
    """The delve paid out: what the party earned and what each survivor takes.

    Emitted by [`TravelToTown`][osrlib.crawl.commands.TravelToTown] under the default
    ruleset, where experience is awarded for making it back. Each survivor's own
    [`XpAwardedEvent`][osrlib.crawl.events.XpAwardedEvent] follows it, and a level
    gained follows that.

    Treasure counts by what the party carried out compared with what it carried in,
    so goods still lying in the dungeon are worth nothing yet. A party that lost
    everyone awards nothing, because nobody came back to spend it.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.xp.adventure_award"})
    """The only message code this event uses."""

    event_type: Literal["adventure_xp_award"] = "adventure_xp_award"
    """The wire discriminator, `adventure_xp_award`."""
    code: str = "session.xp.adventure_award"
    """The message code, always `session.xp.adventure_award`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the award is the point of coming back."""
    monster_xp: int
    """What the defeated creatures were worth, added up across the whole delve."""
    treasure_xp: int
    """What the recovered treasure was worth, one experience point per gold piece of value gained
    since the party left town, and never less than zero."""
    share: int
    """What each survivor receives: the total divided by the number of survivors, rounded down."""
    survivors: tuple[str, ...]
    """The members who made it back, in marching order. The dead count toward the treasure that
    came home but take no share."""


class TreasureSoldEvent(Event):
    """Valuables were sold in town, and the coins are in the purse.

    Emitted by [`SellTreasure`][osrlib.crawl.commands.SellTreasure]. Gems and
    jewellery sell for their full listed value, which keeps one gold piece worth one
    experience point however treasure is converted.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"town.treasure.sold"})
    """The only message code this event uses."""

    event_type: Literal["treasure_sold"] = "treasure_sold"
    """The wire discriminator, `treasure_sold`."""
    code: str = "town.treasure.sold"
    """The message code, always `town.treasure.sold`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party made the sale."""
    character_id: str
    """The member who sold them and now holds the coins."""
    instance_ids: tuple[str, ...]
    """The session-scoped ids of the valuables that were sold."""
    gp_value: int
    """What they fetched, in gold pieces."""


class HealingPurchasedEvent(Event):
    """A temple service was paid for and cast.

    Emitted by [`PurchaseHealing`][osrlib.crawl.commands.PurchaseHealing] in town,
    followed by the kernel events of the spell itself. The temple charges the party,
    so `payers` and `payments_gp` say which purses covered the fee and what each one
    put in.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"town.healing.purchased"})
    """The only message code this event uses."""

    event_type: Literal["healing_purchased"] = "healing_purchased"
    """The wire discriminator, `healing_purchased`."""
    code: str = "town.healing.purchased"
    """The message code, always `town.healing.purchased`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party bought it."""
    character_id: str
    """The member the service was cast on. Their purse is charged first, but the party covers
    whatever is left, so read `payers` for who actually paid."""
    service: str
    """Which service was bought, as the key the town's price list uses."""
    cost_gp: int
    """What it cost, in gold pieces."""
    payers: tuple[str, ...] = ()
    """Whose purses paid, in the order they were charged: the treated member first, then the rest
    of the party in marching order, dead members included. A purse pays in whole gold pieces, as
    much of the outstanding fee as its gold covers, so the last purse charged pays what is left and
    the purses behind it are never opened. A member whose purse stayed shut is absent. It is empty
    on an event loaded from a save written before the field existed."""
    payments_gp: tuple[int, ...] = ()
    """What each purse in `payers` paid, in gold pieces and in the same order. The entries sum to
    `cost_gp`."""


class FlagSetEvent(Event):
    """A session flag was written.

    Emitted by the referee's [`SetFlag`][osrlib.crawl.commands.SetFlag]. Flags are
    the game's own memory: an adventure's triggers and gates read them through a
    [`FlagEqualsCondition`][osrlib.crawl.gates.FlagEqualsCondition], and a game can
    keep whatever else it wants there.

    It's referee visibility, because what the game is keeping track of isn't part
    of the fiction the party is in.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.flag.set"})
    """The only message code this event uses."""

    event_type: Literal["flag_set"] = "flag_set"
    """The wire discriminator, `flag_set`."""
    code: str = "session.flag.set"
    """The message code, always `session.flag.set`."""
    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: the wiring behind the game stays behind the screen."""
    key: str
    """Which flag was written."""
    value: str | int | bool
    """What it was set to. Writing an existing key replaces its value."""


class MonstersSpawnedEvent(Event):
    """Monsters were put into the session by the referee.

    Emitted by [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters], before the
    encounter that fields them opens in the same result.

    It's referee visibility and contains ids rather than a description. The party
    learns what walked in from
    [`EncounterStartedEvent`][osrlib.crawl.events.EncounterStartedEvent].
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.monsters.spawned"})
    """The only message code this event uses."""

    event_type: Literal["monsters_spawned"] = "monsters_spawned"
    """The wire discriminator, `monsters_spawned`."""
    code: str = "session.monsters.spawned"
    """The message code, always `session.monsters.spawned`."""
    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: this is bookkeeping, not a moment in the fiction."""
    template_id: str
    """What was spawned, as a monster catalog id."""
    monster_ids: tuple[str, ...]
    """The session ids of the new instances, in spawn order. They are what every later event about
    those creatures names."""


class XpAwardedEvent(Event):
    """One character was awarded experience.

    Emitted wherever an award lands: inside the end-of-adventure award on
    [`TravelToTown`][osrlib.crawl.commands.TravelToTown], at each encounter's end
    when the ruleset awards immediately, and from the referee's
    [`AwardXP`][osrlib.crawl.commands.AwardXP]. When the award crosses a threshold, a
    [`CharacterLeveledUpEvent`][osrlib.crawl.events.CharacterLeveledUpEvent] for the
    same member follows it at once.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.xp.awarded"})
    """The only message code this event uses."""

    event_type: Literal["xp_awarded"] = "xp_awarded"
    """The wire discriminator, `xp_awarded`."""
    code: str = "session.xp.awarded"
    """The message code, always `session.xp.awarded`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: it goes on the character's sheet."""
    character_id: str
    """The member who received it."""
    award: int
    """The award as the session handed it over, before the character's own adjustment."""
    modified_award: int
    """What was actually added, after the class's prime-requisite percentage, rounded down. This
    is the number to show beside the character."""
    level_after: int
    """The member's level once the award was applied."""


class CharacterLeveledUpEvent(Event):
    """A character crossed a threshold and gained a level.

    Emitted immediately after that member's own
    [`XpAwardedEvent`][osrlib.crawl.events.XpAwardedEvent], whichever award crossed
    the threshold. A character gains at most one level per award.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.level.gained"})
    """The only message code this event uses."""

    event_type: Literal["leveled_up"] = "leveled_up"
    """The wire discriminator, `leveled_up`."""
    code: str = "session.level.gained"
    """The message code, always `session.level.gained`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the table has been waiting for this one."""
    character_id: str
    """The member who levelled."""
    level_before: int
    """The level held before the award."""
    level_after: int
    """The level held after it, one higher."""
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

    Emitted by [`AdvanceTime`][osrlib.crawl.commands.AdvanceTime]. The time passes
    with all its usual bookkeeping, so effect expiries, light burning out, and
    provisions for a day crossed all arrive in the same result, but no wandering
    check runs: a referee moving the clock decides for themselves what walks in.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.time.advanced"})
    """The only message code this event uses."""

    event_type: Literal["time_advanced"] = "time_advanced"
    """The wire discriminator, `time_advanced`."""
    code: str = "session.time.advanced"
    """The message code, always `session.time.advanced`."""
    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: the clock is the referee's instrument."""
    n: int
    """How many units were asked for."""
    unit: str
    """Which unit, as a lowercase [`TimeUnit`][osrlib.core.clock.TimeUnit] value: `"round"`,
    `"turn"`, or `"day"`."""
    rounds_total: int
    """Where the clock now stands, in rounds since the session began. It's the same number
    `GameSession.clock.rounds` holds."""


class GameOverEvent(Event):
    """Every party member is dead and the session has ended.

    Emitted by whatever command's events killed the last member: a lost battle, a
    trap, a fall down a chute, starvation, a poison that finished someone while the
    referee was moving the clock. It closes that command's result, and the session
    mode becomes `game_over`.

    Play commands are refused from there. A referee can still act, and
    [`PlaceParty`][osrlib.crawl.commands.PlaceParty] is the way out, because carrying
    the fallen back to town is the first step of a revival.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.game_over"})
    """The only message code this event uses."""

    event_type: Literal["game_over"] = "game_over"
    """The wire discriminator, `game_over`."""
    code: str = "session.game_over"
    """The message code, always `session.game_over`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: it's the party's ending."""
    reason: str
    """Why the session ended, as a short phrase. Every ending reports the party falling. What
    killed them is in the events just before it."""


class DiceRolledEvent(Event):
    """The referee rolled dice for something the rules don't cover.

    Emitted by [`RollDice`][osrlib.crawl.commands.RollDice]. The roll comes off the
    session's own adjudication stream, kept apart from the streams the rules use, so
    a referee rolling for weather or a rumour never shifts the dice a later attack or
    save would have drawn.

    It's referee visibility: a hidden adjudication isn't automatically the table's
    to see. Show it to the players yourself when the ruling was made in the open.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"adjudication.dice_rolled"})
    """The only message code this event uses."""

    event_type: Literal["dice_rolled"] = "dice_rolled"
    """The wire discriminator, `dice_rolled`."""
    code: str = "adjudication.dice_rolled"
    """The message code, always `adjudication.dice_rolled`."""
    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: the referee decides what to share."""
    expression: str
    """What was rolled, as the dice expression that was asked for, like `"2d6+1"`."""
    total: int
    """The result, dice and modifier together."""
    rolls: tuple[int, ...]
    """Each die's own result, in roll order, so a transcript can show the dice rather than only
    the sum."""


class TriggerFiredEvent(Event):
    """An authored trigger fired.

    Emitted by [`MarkTriggerFired`][osrlib.crawl.commands.MarkTriggerFired], every
    time, including a repeat of a trigger that has fired before: the session records
    that a trigger has fired at all, and these events are the record of each firing.

    It's referee visibility, because which clause fired is the wiring behind the
    game. A beat written for the table goes in the journal, and arrives as
    [`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent] or as one
    of the quest events.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.trigger.fired"})
    """The only message code this event uses."""

    event_type: Literal["trigger_fired"] = "trigger_fired"
    """The wire discriminator, `trigger_fired`."""
    code: str = "session.trigger.fired"
    """The message code, always `session.trigger.fired`."""
    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: trigger wiring is the game's own."""
    trigger_id: str
    """Which trigger fired, as the id the adventure gave it."""
    narrative: str | None = None
    """The beat the author wrote for this firing, or `None`. Content rather than engine prose: the
    default formatter appends it after the templated line. It reaches a referee-visibility event,
    so put anything meant for the table in the journal instead."""


class JournalEntryAddedEvent(Event):
    """A beat was written into the session journal.

    Emitted by [`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry]. The
    journal is the party's own record of the adventure, appended in order and never
    rewritten, and it's part of what
    [`GameSession.view`][osrlib.crawl.session.GameSession.view] shows a player.

    It isn't the only event a growing journal produces. A quest beat appends its
    entry and reports itself through its own lifecycle event instead, so the table
    isn't told the same line twice. Read the whole journal from the view, and read
    these events to know when a line arrived.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.journal.entry_added"})
    """The only message code this event uses."""

    event_type: Literal["journal_entry_added"] = "journal_entry_added"
    """The wire discriminator, `journal_entry_added`."""
    code: str = "session.journal.entry_added"
    """The message code, always `session.journal.entry_added`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the journal is written for the table."""
    text: str
    """The beat as it was written. Content the game or the adventure supplied, not prose the
    engine wrote."""
    rounds: int
    """Where the clock stood when the beat landed, in rounds since the session began. The stored
    entry has the same stamp."""


class NoteRecordedEvent(Event):
    """A referee note was recorded, and no game state changed.

    Emitted by [`RecordNote`][osrlib.crawl.commands.RecordNote]. Games use it to leave
    a machine-written note in the log, like a consequence that could not be applied,
    and referees use it for their own margin notes.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.note.recorded"})
    """The only message code this event uses."""

    event_type: Literal["note_recorded"] = "note_recorded"
    """The wire discriminator, `note_recorded`."""
    code: str = "session.note.recorded"
    """The message code, always `session.note.recorded`."""
    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: a note is for the person running the game."""
    text: str
    """The note as it was written."""


class QuestActivatedEvent(Event):
    """A quest came into play.

    Emitted by [`ActivateQuest`][osrlib.crawl.commands.ActivateQuest]. A quest the
    adventure marked as standing from the start needs no activation and no event: it
    is active from the first command.

    It's player visibility, because a job the party has taken on is theirs to know,
    while the clause that set it off stays behind the screen with the trigger and
    flag events.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.quest.activated"})
    """The only message code this event uses."""

    event_type: Literal["quest_activated"] = "quest_activated"
    """The wire discriminator, `quest_activated`."""
    code: str = "session.quest.activated"
    """The message code, always `session.quest.activated`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party is being given the job."""
    quest_id: str
    """Which quest, as the id the adventure gave it."""
    name: str
    """The quest's display name, so a renderer needs no copy of the adventure to show it."""
    narrative: str | None = None
    """The offer beat the author wrote, or `None` when there's none. The same line is appended to
    the journal, so this event and that entry report one moment once."""


class ObjectiveRevealedEvent(Event):
    """A hidden objective surfaced: the party can now be told what it's being asked for.

    Emitted by [`RevealObjective`][osrlib.crawl.commands.RevealObjective]. An
    objective the adventure didn't mark hidden is visible from the start and is
    never revealed.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.quest.objective_revealed"})
    """The only message code this event uses."""

    event_type: Literal["objective_revealed"] = "objective_revealed"
    """The wire discriminator, `objective_revealed`."""
    code: str = "session.quest.objective_revealed"
    """The message code, always `session.quest.objective_revealed`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the party is being told what to do."""
    quest_id: str
    """The quest the objective belongs to."""
    quest_name: str = ""
    """The quest's display name, resolved when the event is made so a renderer holds no adventure
    to look it up in. It defaults empty only so an event written by an older version still parses.
    The engine always fills it."""
    objective_id: str
    """Which objective, as the id the adventure gave it."""
    name: str = ""
    """The objective's display label: the name its author wrote, or its id when the adventure
    wrote none. It defaults empty for the same parsing reason as `quest_name`."""
    narrative: str | None = None
    """The offer beat the author wrote for this objective, or `None`. The journal contains the same
    line."""


class ObjectiveCompletedEvent(Event):
    """One objective of a quest is done.

    Emitted by [`CompleteObjective`][osrlib.crawl.commands.CompleteObjective].
    Completing an objective also reveals it, so an objective the party finished
    before anyone announced it arrives here first and needs no separate reveal.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.quest.objective_completed"})
    """The only message code this event uses."""

    event_type: Literal["objective_completed"] = "objective_completed"
    """The wire discriminator, `objective_completed`."""
    code: str = "session.quest.objective_completed"
    """The message code, always `session.quest.objective_completed`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: progress belongs to the party."""
    quest_id: str
    """The quest the objective belongs to."""
    quest_name: str = ""
    """The quest's display name, resolved when the event is made. It defaults empty only so an
    event written by an older version still parses. The engine always fills it."""
    objective_id: str
    """Which objective was completed."""
    name: str = ""
    """The objective's display label: the name its author wrote, or its id when the adventure
    wrote none."""
    narrative: str | None = None
    """The progress beat the author wrote, or `None`. The journal contains the same line."""


class QuestCompletedEvent(Event):
    """A quest is finished.

    Emitted by [`CompleteQuest`][osrlib.crawl.commands.CompleteQuest]. Whether the
    quest is done is the referee's ruling: the engine checks that the quest is
    active, not that every objective was completed.

    Rewards a quest pays out arrive after this event, as the commands the game issues
    for them and their own events. When the quest is the one that concludes the
    adventure, an
    [`AdventureCompletedEvent`][osrlib.crawl.events.AdventureCompletedEvent] follows
    in the same result.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.quest.completed"})
    """The only message code this event uses."""

    event_type: Literal["quest_completed"] = "quest_completed"
    """The wire discriminator, `quest_completed`."""
    code: str = "session.quest.completed"
    """The message code, always `session.quest.completed`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: finishing the job is the party's news."""
    quest_id: str
    """Which quest was completed."""
    name: str
    """The quest's display name."""
    narrative: str | None = None
    """The completion beat the author wrote, or `None`. The journal contains the same line."""


class AdventureCompletedEvent(Event):
    """The adventure is over and the party won.

    Emitted by [`CompleteQuest`][osrlib.crawl.commands.CompleteQuest] when the quest
    that concludes the adventure completes, right after that quest's own
    [`QuestCompletedEvent`][osrlib.crawl.events.QuestCompletedEvent], with the same
    beat. The session mode becomes `victory`, which is final: play commands
    are refused and nothing leaves it, so a front end can treat this as its closing
    screen.

    A session that has already ended doesn't get an ending twice: a party that
    finishes the job after it has already fallen completes the quest and stays in
    `game_over`.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.adventure.completed"})
    """The only message code this event uses."""

    event_type: Literal["adventure_completed"] = "adventure_completed"
    """The wire discriminator, `adventure_completed`."""
    code: str = "session.adventure.completed"
    """The message code, always `session.adventure.completed`."""
    visibility: Visibility = Visibility.PLAYER
    """Player visibility: it's the party's ending."""
    quest_id: str
    """The quest that concluded the adventure."""
    name: str = ""
    """That quest's display name. It defaults empty only so an event written by an older version
    still parses. The engine always fills it."""
    narrative: str | None = None
    """The quest's completion beat, the same line its
    [`QuestCompletedEvent`][osrlib.crawl.events.QuestCompletedEvent] carried, or `None`."""


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
)
"""The event classes a session's own framework emits, in declaration order.

Walk it to build a table of the crawl events, or to generate client types from their JSON
Schemas. For the whole surface, including the rules resolutions underneath, use
[`ALL_EVENT_CLASSES`][osrlib.crawl.events.ALL_EVENT_CLASSES].
"""

ALL_EVENT_CLASSES: tuple[type[Event], ...] = (*KERNEL_EVENT_CLASSES, *CRAWL_EVENT_CLASSES)
"""Every event class the library can emit: the kernel ones first, then the crawl ones.

This is the registry to walk when you are generating something from the whole event surface, such
as client types, a documentation table, or a schema bundle. Each class carries its wire name in
`model_fields["event_type"].default` and its code set in `allowed_codes`.
"""

AnyEvent = Annotated[
    Union[*ALL_EVENT_CLASSES],
    Field(discriminator="event_type"),
]
"""Any event the library can emit, as a union pydantic discriminates on `event_type`.

Use it to type a value that holds one event of no particular class, and hand it to a
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
    """Rebuild one serialized event, kernel or crawl, skipping types this version doesn't know.

    Call it on records that came out of an event's `model_dump` or out of a save's event log, such
    as a log you are replaying, a stream you received over a network, or a file you are analyzing.
    A session restored by [`load_game`][osrlib.persistence.load_game] uses it for the event log it
    reads, keeping the raw record for anything it could not parse.

    An `event_type` this version has no class for returns `None` rather than raising, so a log
    written by a newer engine still loads under an older one. Unknown fields on a known type are ignored
    for the same reason. What you get back is an instance of the matching class, which you can
    hand to [`format_message`][osrlib.messages.format_message] like any other event.

    Args:
        data: One event as a mapping, from `model_dump` (in either Python or JSON mode) or from
            parsed JSON.

    Returns:
        The event, or `None` when its `event_type` belongs to no class in
        [`ALL_EVENT_CLASSES`][osrlib.crawl.events.ALL_EVENT_CLASSES].

    Raises:
        ContentValidationError: If the event type is known but the payload doesn't fit it, like
            a record missing a required field. The message carries pydantic's own report.

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
