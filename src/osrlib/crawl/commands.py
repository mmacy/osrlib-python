"""The command set: typed models, the discriminated union, and the result envelope.

Commands are the write API: build one and pass it to
[`GameSession.execute`][osrlib.crawl.session.GameSession.execute].
[`CommandResult`][osrlib.crawl.commands.CommandResult] is the envelope `execute`
returns: `accepted`, the kernel's [`Rejection`][osrlib.core.validation.Rejection]
models verbatim, and the events. Commands mirror the event conventions exactly:
frozen pydantic models, a single-valued `command_type` Literal discriminator
(snake_case, schema-stable, additive-only), an
[`AnyCommand`][osrlib.crawl.commands.AnyCommand] discriminated union, and
[`parse_command`][osrlib.crawl.commands.parse_command] returning `None` on unknown
types.

Each command declares its legal session modes as an `allowed_modes` class
attribute; the session rejects a wrong-mode command with
`session.command.wrong_mode`. Referee commands are legal in every mode and are
logged and replayed like any other. Every command class documents its contract in
three sections: `Modes:` (the legal session modes), `Rejections:` (the rejection
codes it can return), and `Events:` (what it emits when accepted).
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
    "AdvanceTime",
    "AnyCommand",
    "AwardXP",
    "BattleDeclaration",
    "CastSpell",
    "CloseDoor",
    "Command",
    "CommandResult",
    "DropItems",
    "EngageBattle",
    "EnterDungeon",
    "EquipItem",
    "Evade",
    "ExtinguishSource",
    "ForceDoor",
    "GrantCoins",
    "GrantItem",
    "IdentifyItem",
    "InspectTreasure",
    "LightSource",
    "ListenAtDoor",
    "MoveParty",
    "OpenDoor",
    "Parley",
    "PickLock",
    "PlaceParty",
    "PrepareSpells",
    "PurchaseEquipment",
    "PurchaseHealing",
    "RemoveTreasureTrap",
    "ReorderParty",
    "ResolveBattleRound",
    "Rest",
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

    The wire values are lowercase — they serialize into saves; changing them is a
    `schema_version` bump.
    """

    TOWN = "town"
    EXPLORING = "exploring"
    ENCOUNTER = "encounter"
    BATTLE = "battle"
    GAME_OVER = "game_over"


_ALL_MODES = frozenset(SessionMode)
_FIELD_MODES = frozenset({SessionMode.TOWN, SessionMode.EXPLORING})


class Command(BaseModel):
    """Base class for all commands.

    Commands are frozen: they are requests, logged verbatim when accepted, never
    mutated. Subclasses must keep `extra="ignore"` (the additive-schema contract)
    and declare a single-valued `command_type` Literal plus their legal session
    modes.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    command_type: str

    allowed_modes: ClassVar[frozenset[SessionMode]] = _ALL_MODES

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

    A rejected command consumes no RNG draws, no clock time, mutates nothing, and
    is excluded from the command log — its result carries the rejections and no
    events.
    """

    model_config = ConfigDict(frozen=True)

    accepted: bool
    rejections: tuple[Rejection, ...] = ()
    events: tuple[Event, ...] = ()


class MoveParty(Command):
    """Move the party one cell; facing follows the movement direction.

    The party must already be inside a dungeon: a fresh session starts in town, and
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] is what places the party at
    the entrance and switches the session to `exploring`.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` — the session is not exploring a dungeon.
        - `exploration.move.cannot_move` — the party cannot move: it is overloaded,
          or a living member is unable to walk.
        - `exploration.move.blocked` — a wall, a closed or secret door, or the map
          edge blocks that direction.

    Events:
        [`PartyMovedEvent`][osrlib.crawl.events.PartyMovedEvent] with the new position
        and facing. Entering a new cell can also trigger area descriptions, keyed
        encounters, traps, treasure discovery, wandering-monster checks, light
        burn-down, and doors swinging shut, each reported by its own event.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["move_party"] = "move_party"
    direction: Direction


class TurnParty(Command):
    """Turn the party in place to a new facing (zero time).

    The party must already be inside a dungeon — see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon].

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` — the session is not exploring a dungeon.

    Events:
        [`PartyMovedEvent`][osrlib.crawl.events.PartyMovedEvent] with the unchanged
        position and the new facing.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["turn_party"] = "turn_party"
    facing: Direction


class ReorderParty(Command):
    """Rewrite the marching order — the only way marching order changes.

    Legal in town and while exploring; the order is locked once an encounter or
    battle has begun.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` — an encounter or battle is underway, or the
          game is over.
        - `exploration.party.bad_order` — `order` does not name exactly the current
          members, each once.

    Events:
        None. An accepted reorder changes state silently.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["reorder_party"] = "reorder_party"
    order: tuple[str, ...] = Field(min_length=1)


class OpenDoor(Command):
    """Open an unstuck, unlocked door on one side of the party's cell (zero time).

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]). An undiscovered secret
    door rejects exactly like blank wall — commands never leak hidden geometry.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` — the session is not exploring a dungeon.
        - `exploration.door.no_door` — no known door on that side of the cell
          (undiscovered secret doors included).
        - `exploration.door.already_open` — the door already stands open.
        - `exploration.door.locked` — the lock has not been picked or otherwise
          undone.
        - `exploration.door.stuck` — a stuck door needs
          [`ForceDoor`][osrlib.crawl.commands.ForceDoor].

    Events:
        [`DoorEvent`][osrlib.crawl.events.DoorEvent] with code
        `exploration.door.opened`.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["open_door"] = "open_door"
    direction: Direction


class CloseDoor(Command):
    """Close an open door on one side of the party's cell (zero time).

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]).

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` — the session is not exploring a dungeon.
        - `exploration.door.no_door` — no known door on that side of the cell.
        - `exploration.door.already_closed` — the door is already closed.
        - `exploration.door.wedged` — a wedged door cannot swing.

    Events:
        [`DoorEvent`][osrlib.crawl.events.DoorEvent] with code
        `exploration.door.closed`.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["close_door"] = "close_door"
    direction: Direction


class ForceDoor(Command):
    """Force a stuck door: the character's STR open-doors check; noise is the cost.

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]). Any attempt bangs on the
    door — the next wandering check takes the noise bonus — and a failed attempt
    alerts the room beyond, denying the party surprise there.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` — the session is not exploring a dungeon.
        - `session.command.unknown_member` — `character_id` names no party member.
        - `session.command.member_incapacitated` — the member cannot act.
        - `exploration.door.no_door` — no known door on that side of the cell.
        - `exploration.door.already_open` — the door already stands open.
        - `exploration.door.locked` — locked doors need
          [`PickLock`][osrlib.crawl.commands.PickLock], not muscle.
        - `exploration.door.not_stuck` — an unstuck door opens with
          [`OpenDoor`][osrlib.crawl.commands.OpenDoor].

    Events:
        [`DoorEvent`][osrlib.crawl.events.DoorEvent] with code
        `exploration.door.forced` on success or `exploration.door.stuck` on
        failure.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["force_door"] = "force_door"
    direction: Direction
    character_id: str


class WedgeDoor(Command):
    """Wedge a door with an iron spike so it cannot swing shut (zero time).

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]). Any living member's
    spike serves; one iron spike is consumed.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` — the session is not exploring a dungeon.
        - `exploration.door.no_door` — no known door on that side of the cell.
        - `exploration.door.wedged` — the door is already wedged.
        - `exploration.door.no_spike` — no living member carries iron spikes.

    Events:
        [`DoorEvent`][osrlib.crawl.events.DoorEvent] with code
        `exploration.door.wedged`.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["wedge_door"] = "wedge_door"
    direction: Direction


class ListenAtDoor(Command):
    """Listen at a door: once per character per door, ever (zero time).

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]), and the listener needs
    light (infravision suffices). Hearing occupants marks the party aware for the
    room's eventual encounter.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` — the session is not exploring a dungeon.
        - `session.command.unknown_member` — `character_id` names no party member.
        - `session.command.member_incapacitated` — the member cannot act.
        - `exploration.door.no_door` — no known door on that side of the cell.
        - `exploration.action.requires_light` — the party is in the dark and the
          listener lacks infravision.
        - `exploration.listen.already_tried` — this character has already listened
          at this door.

    Events:
        [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent] with the
        roll, then [`ListenedEvent`][osrlib.crawl.events.ListenedEvent] with code
        `exploration.listen.heard` or `exploration.listen.silent`.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["listen_at_door"] = "listen_at_door"
    direction: Direction
    character_id: str


class PickLock(Command):
    """Pick a locked door's lock: thief-only, needs thieves' tools, one turn.

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]). A failed attempt locks
    that character out of that lock until the next level gain.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` — the session is not exploring a dungeon.
        - `session.command.unknown_member` — `character_id` names no party member.
        - `session.command.member_incapacitated` — the member cannot act.
        - `exploration.lock.not_a_thief` — the member has no thief skills.
        - `exploration.lock.no_tools` — the member carries no thieves' tools.
        - `exploration.door.no_door` — no known door on that side of the cell.
        - `exploration.lock.not_locked` — the door has no lock left to pick.
        - `exploration.action.requires_light` — picking needs real light;
          infravision does not suffice.
        - `exploration.lock.locked_out` — this character already failed here at
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
    character_id: str


class Search(Command):
    """Search the party's cell for one hidden-feature kind (one turn).

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]), with light (infravision
    suffices). Each character gets one attempt per cell per kind, ever.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` — the session is not exploring a dungeon.
        - `session.command.unknown_member` — `character_id` names no party member.
        - `session.command.member_incapacitated` — the member cannot act.
        - `exploration.action.requires_light` — the party is in the dark and the
          searcher lacks infravision.
        - `exploration.search.already_tried` — this character already searched this
          cell for this kind.

    Events:
        [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent] with the
        roll, a [`TrapEvent`][osrlib.crawl.events.TrapEvent] when a room trap is
        found, then
        [`SearchCompletedEvent`][osrlib.crawl.events.SearchCompletedEvent] naming
        what turned up. One turn passes with its usual follow-on events.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["search"] = "search"
    character_id: str
    kind: Literal["secret_doors", "room_traps", "construction"]


class InspectTreasure(Command):
    """Search a treasure feature for a treasure trap: thief-only, one turn.

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]), with light. One attempt
    per character per feature.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` — the session is not exploring a dungeon.
        - `session.command.unknown_member` — `character_id` names no party member.
        - `session.command.member_incapacitated` — the member cannot act.
        - `exploration.trap.not_a_thief` — the member has no thief skills.
        - `exploration.feature.unknown` — `feature_id` names no treasure cache on
          this cell.
        - `exploration.action.requires_light` — inspecting needs real light.
        - `exploration.search.already_tried` — this character already inspected
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
    feature_id: str


class RemoveTreasureTrap(Command):
    """Remove a found treasure trap: thief-only, one turn; failure springs it.

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]), with light, and the trap
    must already have been found by
    [`InspectTreasure`][osrlib.crawl.commands.InspectTreasure].

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` — the session is not exploring a dungeon.
        - `session.command.unknown_member` — `character_id` names no party member.
        - `session.command.member_incapacitated` — the member cannot act.
        - `exploration.trap.not_a_thief` — the member has no thief skills.
        - `exploration.feature.unknown` — `feature_id` names no trapped feature on
          this cell.
        - `exploration.trap.not_found` — the trap has not been found yet.
        - `exploration.trap.already_resolved` — the trap was already removed or has
          already sprung.
        - `exploration.action.requires_light` — removal needs real light.
        - `exploration.search.already_tried` — this character already attempted the
          removal.

    Events:
        [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent] with the
        skill roll, then a [`TrapEvent`][osrlib.crawl.events.TrapEvent]:
        `exploration.trap.removed` on success, `exploration.trap.sprung` on failure
        — the sprung trap resolves at once against the thief (saving throws,
        damage, conditions, each its own event). One turn passes.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["remove_treasure_trap"] = "remove_treasure_trap"
    character_id: str
    feature_id: str


class TakeTreasure(Command):
    """Empty a cache or pile into the party's packs (one turn, RAW).

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]). `feature_id` names an
    authored cache, an engine-generated cache, or the literal `pile` for goods
    dropped on the cell. The leading living member carries everything; taking a
    trapped cache with its trap unresolved risks springing it.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` — the session is not exploring a dungeon.
        - `session.command.no_living_members` — no one is left to carry.
        - `exploration.feature.unknown` — nothing by that id on this cell.
        - `exploration.feature.emptied` — the cache has already been emptied.

    Events:
        [`ItemAcquiredEvent`][osrlib.crawl.events.ItemAcquiredEvent] listing the
        goods and coin value. An unresolved treasure trap rolls first
        ([`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent], a
        [`TrapEvent`][osrlib.crawl.events.TrapEvent], and the trap's resolution
        when it springs). Under the immediate XP timing an
        [`XpAwardedEvent`][osrlib.crawl.events.XpAwardedEvent] follows per member.
        One turn passes.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["take_treasure"] = "take_treasure"
    feature_id: str


class DropItems(Command):
    """Drop items and coins onto the party's cell (or the pursuit trail).

    Each `item_ids` entry drops one unit (repeat an id for more). Legal while
    exploring a dungeon and during an encounter — dropping treasure or food is the
    pursuit-distraction move.

    Modes:
        `exploring`, `encounter`

    Rejections:
        - `session.command.wrong_mode` — the session is in town, in battle, or
          over.
        - `session.command.unknown_member` — `character_id` names no party member.
        - `session.command.member_incapacitated` — the member cannot act.
        - `items.curse.stuck` — a revealed cursed item cannot be discarded.
        - `exploration.item.not_carried` — the member lacks an item or the coins.
        - `encounter.none_active` — defensive twin of the mode gate; not reachable
          through normal play.

    Events:
        [`ItemsDroppedEvent`][osrlib.crawl.events.ItemsDroppedEvent] with what
        fell. In an encounter the round then closes — the monsters act per their
        stance — or, mid-pursuit, a
        [`PursuitEvent`][osrlib.crawl.events.PursuitEvent] round resolves with the
        drop as bait.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING, SessionMode.ENCOUNTER})

    command_type: Literal["drop_items"] = "drop_items"
    character_id: str
    item_ids: tuple[str, ...] = ()
    coins: Coins = Coins()


class LightSource(Command):
    """Light a torch or lantern, or ignite dropped oil (one round).

    Legal in town and while exploring. Without an open flame already burning in
    the party, the bearer needs a tinder box, and striking it is a 2-in-6 chance —
    the round is spent per attempt (RAW). Lighting an `oil_flask` ignites a flask
    previously dropped on the party's cell as a burning pool.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` — an encounter or battle is underway, or the
          game is over.
        - `session.command.unknown_member` — `character_id` names no party member.
        - `session.command.member_incapacitated` — the member cannot act.
        - `exploration.light.not_a_source` — `item_id` is not `torch`, `lantern`,
          or `oil_flask`.
        - `exploration.item.not_carried` — the member lacks the source (or oil for
          the lantern), or no dropped flask lies on the cell.
        - `exploration.light.no_flame` — no open flame and no tinder box.

    Events:
        [`LightEvent`][osrlib.crawl.events.LightEvent] with code
        `exploration.light.lit` — an
        [`EffectAttachedEvent`][osrlib.core.events.EffectAttachedEvent] carries the
        burn-down effect — or `exploration.light.failed` when the tinder does not
        catch. One round passes.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["light_source"] = "light_source"
    character_id: str
    item_id: str


class ExtinguishSource(Command):
    """Extinguish the bearer's burning source, forfeiting the remainder (zero time).

    Legal in town and while exploring. A doused torch or lantern is spent — the
    remaining burn time does not bank.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` — an encounter or battle is underway, or the
          game is over.
        - `session.command.unknown_member` — `character_id` names no party member.
        - `session.command.member_incapacitated` — the member cannot act.
        - `exploration.light.not_burning` — the member carries no burning torch or
          lantern.

    Events:
        An [`EffectReleasedEvent`][osrlib.core.events.EffectReleasedEvent] and a
        [`LightEvent`][osrlib.crawl.events.LightEvent] with code
        `exploration.light.extinguished` per doused source.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["extinguish_source"] = "extinguish_source"
    character_id: str


class EquipItem(Command):
    """Equip an item from a member's item list (zero time).

    Legal in town and while exploring. Class armour and weapon policies validate
    before anything changes. `item_id` is the magic item's instance id for a magic
    item, or the catalog id (from [`load_equipment`][osrlib.data.load_equipment] —
    see [the equipment id index][equipment-index]) for a mundane one, which has no
    per-instance id.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` — an encounter or battle is underway, or the
          game is over.
        - `session.command.unknown_member` — `character_id` names no party member.
        - `session.command.member_incapacitated` — the member cannot act.
        - `exploration.item.not_carried` — nothing by that id in the item list.
        - `items.equip.armour_forbidden`, `items.equip.armour_not_allowed`,
          `items.equip.shield_forbidden`, `items.equip.weapon_not_allowed`,
          `items.equip.weapon_forbidden` — the class policy forbids it.
        - `items.equip.two_handed_with_shield` — a two-handed weapon and a shield
          cannot pair.
        - `items.equip.not_equippable` — potions, scrolls, ammunition, and plain
          gear without a combat use do not equip.
        - `items.equip.not_usable` — the magic device is not usable by this class.
        - `items.ring.hands_full` — two rings are already worn.

    Events:
        Usually none. Equipping a worn magic item can attach its effects
        ([`EffectAttachedEvent`][osrlib.core.events.EffectAttachedEvent]); a cursed
        ring identifies and reveals at wearing
        ([`ItemIdentifiedEvent`][osrlib.crawl.events.ItemIdentifiedEvent],
        [`CurseRevealedEvent`][osrlib.crawl.events.CurseRevealedEvent]).
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["equip_item"] = "equip_item"
    character_id: str
    item_id: str


class UnequipItem(Command):
    """Return an equipped item to the member's item list (zero time).

    Legal in town and while exploring. A revealed cursed item stays put until
    *remove curse*.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` — an encounter or battle is underway, or the
          game is over.
        - `session.command.unknown_member` — `character_id` names no party member.
        - `session.command.member_incapacitated` — the member cannot act.
        - `exploration.item.not_equipped` — nothing by that id is equipped.
        - `items.curse.stuck` — a revealed cursed item cannot be removed.

    Events:
        Usually none; a worn magic item's effects release
        ([`EffectReleasedEvent`][osrlib.core.events.EffectReleasedEvent]).
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["unequip_item"] = "unequip_item"
    character_id: str
    item_id: str


class Rest(Command):
    """Rest: one turn (the cadence rest), a night (48 turns), or a full day (144).

    Legal in town and while exploring. In the dungeon a wandering encounter can
    interrupt the rest; a full day of rest also applies natural healing.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` — an encounter or battle is underway, or the
          game is over.

    Events:
        [`RestedEvent`][osrlib.crawl.events.RestedEvent] with code
        `exploration.rest.rested`, or `exploration.rest.interrupted` when a
        wandering encounter breaks the rest. Clearing fatigue or exhaustion reports
        a [`FatigueEvent`][osrlib.crawl.events.FatigueEvent] or
        [`ExhaustionEvent`][osrlib.crawl.events.ExhaustionEvent]; a full day's
        natural healing an
        [`HealingAppliedEvent`][osrlib.core.events.HealingAppliedEvent]. The
        elapsed turns report their own bookkeeping (light burn-down, provisions,
        wandering checks).
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["rest"] = "rest"
    kind: Literal["turn", "night", "day"]


class PrepareSpells(Command):
    """Prepare a caster's daily spells: once per sleep, after an uninterrupted night, six turns.

    Legal in town and while exploring. The caster must have slept (a night or day
    [`Rest`][osrlib.crawl.commands.Rest]) since the last preparation; the
    selections replace the memorized list wholesale.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` — an encounter or battle is underway, or the
          game is over.
        - `session.command.unknown_member` — `character_id` names no party member.
        - `session.command.member_incapacitated` — the member cannot act.
        - `magic.memorize.needs_sleep` — no sleep since the last preparation.
        - `magic.memorize.not_a_caster` — the class casts no spells.
        - `magic.memorize.unknown_spell` — a selection names no known spell.
        - `magic.memorize.wrong_list` — a selection is off the caster's spell list.
        - `magic.memorize.divine_reverses_at_cast` — divine casters choose the
          reversed form at casting, not at prayer.
        - `magic.memorize.not_in_book` — an arcane selection is missing from the
          spell book.
        - `magic.memorize.not_reversible` — a reversed selection has no reversed
          form.
        - `magic.memorize.slots_exceeded` — more selections at some spell level
          than the caster has slots.

    Events:
        [`SpellsMemorizedEvent`][osrlib.core.events.SpellsMemorizedEvent] with the
        prepared list. Six turns pass with their usual follow-on events.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["prepare_spells"] = "prepare_spells"
    character_id: str
    selections: tuple[MemorizedSpell, ...] = ()


class CastSpell(Command):
    """Cast a memorized spell outside battle (one round).

    `targets` are entity ids, or `cell:` references for location-bound casts. In
    encounter mode a hostile cast is opened through
    [`EngageBattle`][osrlib.crawl.commands.EngageBattle] and the first round's
    declarations instead; in battle, casting is a declaration kind.

    Modes:
        `town`, `exploring`

    Rejections:
        - `session.command.wrong_mode` — an encounter or battle is underway, or the
          game is over.
        - `session.command.unknown_member` — `character_id` names no party member.
        - `session.command.member_incapacitated` — the member cannot act.
        - `magic.cast.unknown_spell` — `spell_id` names no spell.
        - `magic.cast.silenced_area` — a *silence* effect covers the party's cell.
        - `magic.cast.unknown_target` — a target reference resolves to nothing.
        - `magic.cast.not_memorized` — no memorized copy (non-casters included).
        - `magic.cast.caster_incapacitated`, `magic.cast.caster_restrained`,
          `magic.cast.anti_magic_shell` — the caster cannot cast right now.
        - `magic.cast.not_reversible` — `reversed` on a spell with no reversed
          form.
        - `magic.cast.unknown_mode` — `mode` names no mode of the spell.
        - `magic.cast.target_count` — the wrong number of targets for the mode.
        - `magic.cast.out_of_range` — a target lies beyond the spell's range.

    Events:
        [`SpellCastEvent`][osrlib.core.events.SpellCastEvent] plus the spell's own
        resolution — saving throws, damage, healing, effect attachments — each its
        own event. One round passes.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["cast_spell"] = "cast_spell"
    character_id: str
    spell_id: str
    mode: str
    reversed: bool = False
    targets: tuple[str, ...] = ()


class UseItem(Command):
    """Use a magic item: drink a potion, read a scroll, activate a device (one round).

    One round is the RAW activation cost (drinking is one round). `target_id`
    names a character (the staff of healing's touch) or an encounter group (a
    device's area); `spell_id`, `mode`, and `targets` select the inscribed spell
    and its targets when reading a multi-spell scroll (the
    [`CastSpell`][osrlib.crawl.commands.CastSpell] surface). In battle, item use
    is the `use_item` declaration instead. First meaningful use identifies the
    item — and reveals its curse.

    Modes:
        `exploring`, `encounter`

    Rejections:
        - `session.command.wrong_mode` — the session is in town, in battle, or
          over.
        - `session.command.unknown_member` — `character_id` names no party member.
        - `session.command.member_incapacitated` — the member cannot act.
        - `exploration.item.not_carried` — the member carries no magic item with
          that instance id.
        - `items.use.not_usable` — the item has no usable action, or the class
          cannot use the device.
        - Scrolls: `exploration.action.requires_light` (reading needs real light),
          `items.scroll.spent`, `items.scroll.no_such_spell`,
          `items.scroll.wrong_caster`, and the cast validation codes
          (`magic.cast.unknown_target`, `magic.cast.unknown_mode`,
          `magic.cast.target_count`, `magic.cast.out_of_range`,
          `magic.cast.caster_incapacitated`, `magic.cast.caster_restrained`,
          `magic.cast.anti_magic_shell`).
        - Devices: `items.device.inert` (no charges left),
          `items.use.target_required`, `items.use.unknown_target`, and
          `items.use.battle_only` (a striking effect is a battle declaration).

    Events:
        [`ItemUsedEvent`][osrlib.crawl.events.ItemUsedEvent] naming what happened
        (drunk, read, activated — or mixed potions, or a cursed scroll), with
        [`ItemIdentifiedEvent`][osrlib.crawl.events.ItemIdentifiedEvent] and
        [`CurseRevealedEvent`][osrlib.crawl.events.CurseRevealedEvent] at first
        meaningful use, then the item's own resolution — healing, saving throws,
        damage, effect attachments, a scroll's
        [`SpellCastEvent`][osrlib.core.events.SpellCastEvent] — each its own
        event. One round passes (in an encounter, the round beat follows instead).
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING, SessionMode.ENCOUNTER})

    command_type: Literal["use_item"] = "use_item"
    character_id: str
    item_id: str
    target_id: str | None = None
    spell_id: str | None = None
    mode: str | None = None
    targets: tuple[str, ...] = ()


class IdentifyItem(Command):
    """Referee: identify a magic item outright — game-driven identification.

    Referee commands are legal in every mode and are logged and replayed like any
    other.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`

    Rejections:
        - `session.command.unknown_member` — `character_id` names no party member.
        - `session.command.unknown_item` — the member carries no magic item with
          that instance id.

    Events:
        [`ItemIdentifiedEvent`][osrlib.crawl.events.ItemIdentifiedEvent]; a cursed
        item also reveals with a
        [`CurseRevealedEvent`][osrlib.crawl.events.CurseRevealedEvent].
    """

    command_type: Literal["identify_item"] = "identify_item"
    character_id: str
    item_id: str


class UseStairs(Command):
    """Take the stair, ladder, or other transition on the party's cell.

    The party must be exploring a dungeon (see
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon]) and standing on a cell
    with an authored transition. The move costs one unexplored-cell step of
    movement.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` — the session is not exploring a dungeon.
        - `exploration.stairs.none` — no transition on the party's cell.

    Events:
        [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent] when the
        level or dungeon changes. Arrival then runs the cell's entry checks — area
        treasure, room traps, keyed encounters — each reporting its own events, and
        the movement cost accrues toward the turn clock.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["use_stairs"] = "use_stairs"


class EnterDungeon(Command):
    """Travel from town to a dungeon's entrance and start exploring.

    The party must be in town. Travel takes the adventure's authored cost in
    turns; arrival places the party at the entrance and switches the session to
    `exploring`. Departure also snapshots the party's treasure valuation — the
    end-of-adventure XP award is the delta against it.

    Modes:
        `town`

    Rejections:
        - `session.command.wrong_mode` — the party is not in town.
        - `session.command.unknown_location` — `dungeon_id` names no dungeon, or
          the dungeon has no entrance level.

    Events:
        [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent] for the
        dungeon, after the travel time's own events. Arrival runs the entrance
        cell's entry checks (area treasure, room traps, keyed encounters), each
        reporting its own events.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.TOWN})

    command_type: Literal["enter_dungeon"] = "enter_dungeon"
    dungeon_id: str


class TravelToTown(Command):
    """Travel from the dungeon entrance back to town (the same travel cost).

    The party must be exploring and standing on the entrance cell. Doors the party
    opened swing shut behind it, and under the on-return XP timing the adventure
    award pays out on arrival.

    Modes:
        `exploring`

    Rejections:
        - `session.command.wrong_mode` — the session is not exploring a dungeon.
        - `exploration.travel.not_at_entrance` — the party is not on the entrance
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

    The party must be in town. The whole basket prices first; if the member cannot
    afford the total, nothing is bought.

    Modes:
        `town`

    Rejections:
        - `session.command.wrong_mode` — the party is not in town.
        - `session.command.unknown_member` — `character_id` names no party member.
        - `session.command.member_incapacitated` — the member cannot act.
        - `session.command.unknown_item` — an entry names no equipment item.
        - `items.purchase.insufficient_funds` — the purse cannot cover the total.

    Events:
        [`ItemAcquiredEvent`][osrlib.crawl.events.ItemAcquiredEvent] listing the
        purchases.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.TOWN})

    command_type: Literal["purchase_equipment"] = "purchase_equipment"
    character_id: str
    item_ids: tuple[str, ...] = Field(min_length=1)


class SellTreasure(Command):
    """Sell valuables in town at full value (zero time).

    The party must be in town. Each entry names a carried valuable's instance id;
    the coins credit its carrier's purse. osrlib adopts full `value_gp` as the
    sale price: the OSE SRD prices treasure but names no exchange spread, and full
    value keeps the 1-gp-1-XP identity clean. Magic items have no fixed sale value
    (RAW's own words) and reject; revealed curses stick.

    Modes:
        `town`

    Rejections:
        - `session.command.wrong_mode` — the party is not in town.
        - `town.sell.no_fixed_value` — magic items cannot be sold for a fixed
          price.
        - `exploration.item.not_carried` — no member carries a valuable with that
          instance id.

    Events:
        [`TreasureSoldEvent`][osrlib.crawl.events.TreasureSoldEvent] per selling
        member, with the credited value.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.TOWN})

    command_type: Literal["sell_treasure"] = "sell_treasure"
    item_ids: tuple[str, ...] = Field(min_length=1)


class PurchaseHealing(Command):
    """Buy a temple healing service in town (zero time).

    The party must be in town. The service list and prices are a documented
    adaptation — the OSE SRD's base-town material is prose: *cure light wounds*
    25 gp, *cure serious wounds* 100 gp, *cure disease* 150 gp, *neutralize
    poison* 150 gp, *remove curse* 200 gp, *raise dead* 1,500 gp. Each resolves
    through the kernel spell path with an abstract temple cleric at the minimum
    level able to cast the spell; the named character is the target and pays from
    their own purse.

    Modes:
        `town`

    Rejections:
        - `session.command.wrong_mode` — the party is not in town.
        - `session.command.unknown_member` — `character_id` names no party member.
        - `items.purchase.insufficient_funds` — the character's purse cannot cover
          the service.

    Events:
        [`HealingPurchasedEvent`][osrlib.crawl.events.HealingPurchasedEvent], then
        the service spell's own resolution events (healing, effect releases, a
        revival's outcome).
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.TOWN})

    command_type: Literal["purchase_healing"] = "purchase_healing"
    character_id: str
    service: Literal[
        "cure_light_wounds",
        "cure_serious_wounds",
        "cure_disease",
        "neutralize_poison",
        "remove_curse",
        "raise_dead",
    ]


class Parley(Command):
    """Speak with the monsters: a fresh reaction roll with the speaker's CHA modifier.

    An encounter must be open — encounters begin from wandering checks, keyed
    areas, or the referee spawn commands. Any number of re-rolls is legal; a
    hostile turn self-limits the conversation.

    Modes:
        `encounter`

    Rejections:
        - `session.command.wrong_mode` — no encounter is open.
        - `encounter.none_active` — defensive twin of the mode gate; not reachable
          through normal play.
        - `encounter.parley.mid_pursuit` — no talking while being chased.
        - `session.command.unknown_member` — `character_id` names no party member.
        - `session.command.member_incapacitated` — the speaker cannot act.

    Events:
        [`ReactionRolledEvent`][osrlib.core.events.ReactionRolledEvent], and a
        [`StanceChangedEvent`][osrlib.crawl.events.StanceChangedEvent] when the
        stance shifts. An attacks result opens battle at once
        ([`BattleStartedEvent`][osrlib.crawl.events.BattleStartedEvent] and what
        follows); otherwise the encounter round closes with the monsters' beat.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.ENCOUNTER})

    command_type: Literal["parley"] = "parley"
    character_id: str


class Evade(Command):
    """Flee the encounter (legal only before battle begins, RAW).

    An encounter must be open. `drop` scatters distraction bait as the party runs:
    treasure tempts intelligent monsters, food unintelligent ones. Only attacking
    or hostile monsters pursue; outrunning them ends the encounter cleanly.

    Modes:
        `encounter`

    Rejections:
        - `session.command.wrong_mode` — no encounter is open.
        - `encounter.none_active` — defensive twin of the mode gate; not reachable
          through normal play.
        - `encounter.evade.already_evading` — the pursuit is already running.
        - `encounter.evade.nothing_to_drop` — no coins (for `treasure`) or rations
          (for `food`) to scatter.

    Events:
        [`ItemsDroppedEvent`][osrlib.crawl.events.ItemsDroppedEvent]s for scattered
        bait, then [`EvasionEvent`][osrlib.crawl.events.EvasionEvent] with code
        `encounter.evasion.succeeded` — the encounter ends
        ([`EncounterEndedEvent`][osrlib.crawl.events.EncounterEndedEvent]) — or
        `encounter.evasion.pursuit`, and
        [`PursuitEvent`][osrlib.crawl.events.PursuitEvent] rounds follow: escape,
        exhaustion at the round cap, or battle at the party's heels.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.ENCOUNTER})

    command_type: Literal["evade"] = "evade"
    drop: Literal["none", "treasure", "food"] = "none"


class EngageBattle(Command):
    """Open battle: every offensive action goes through here (except turn undead).

    An encounter must be open. Monsters surprised at the encounter's start grant
    the party a free opening round; engaging mid-pursuit turns the party to fight
    at the current gap.

    Modes:
        `encounter`

    Rejections:
        - `session.command.wrong_mode` — no encounter is open.
        - `encounter.none_active` — defensive twin of the mode gate; not reachable
          through normal play.

    Events:
        [`BattleStartedEvent`][osrlib.crawl.events.BattleStartedEvent]; groups at
        morale 2 rout at once
        ([`MonsterFledEvent`][osrlib.crawl.events.MonsterFledEvent]), and a battle
        whose every group routs ends immediately
        ([`BattleEndedEvent`][osrlib.crawl.events.BattleEndedEvent] and the
        encounter's conclusion).
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.ENCOUNTER})

    command_type: Literal["engage_battle"] = "engage_battle"


class Wait(Command):
    """Hold for one encounter round; the monsters act per their stance.

    An encounter must be open. Waiting burns a round to see what the monsters do —
    an uncertain stance re-rolls its reaction, a hostile one runs out its patience.

    Modes:
        `encounter`

    Rejections:
        - `session.command.wrong_mode` — no encounter is open.
        - `encounter.none_active` — defensive twin of the mode gate; not reachable
          through normal play.

    Events:
        The round beat's events: an uncertain stance re-rolls
        ([`ReactionRolledEvent`][osrlib.core.events.ReactionRolledEvent], possibly
        a [`StanceChangedEvent`][osrlib.crawl.events.StanceChangedEvent]), and an
        attacking or expired-patience hostile stance opens battle
        ([`BattleStartedEvent`][osrlib.crawl.events.BattleStartedEvent]). During a
        pursuit a [`PursuitEvent`][osrlib.crawl.events.PursuitEvent] round resolves
        instead.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.ENCOUNTER})

    command_type: Literal["wait"] = "wait"


class TurnUndead(Command):
    """Present the holy symbol — the one aggressive act with a pre-battle procedure.

    An encounter must be open: exploration offers no candidates by definition, and
    in battle turning is a declaration kind. If any monster stands unturned, the
    survivors attack at once.

    Modes:
        `encounter`

    Rejections:
        - `session.command.wrong_mode` — no encounter is open.
        - `encounter.none_active` — defensive twin of the mode gate; not reachable
          through normal play.
        - `encounter.turning.mid_pursuit` — no turning while being chased.
        - `session.command.unknown_member` — `character_id` names no party member.
        - `session.command.member_incapacitated` — the cleric cannot act.
        - `magic.turning.not_a_turner` — the class has no turn-undead ability.
        - `magic.turning.caster_incapacitated` — a condition prevents the attempt.

    Events:
        [`UndeadTurnedEvent`][osrlib.core.events.UndeadTurnedEvent] with the roll
        and the affected monsters (their conditions each their own event). When
        every monster is turned or destroyed the encounter ends
        ([`EncounterEndedEvent`][osrlib.crawl.events.EncounterEndedEvent]);
        otherwise a [`StanceChangedEvent`][osrlib.crawl.events.StanceChangedEvent]
        to attacks and battle opens
        ([`BattleStartedEvent`][osrlib.crawl.events.BattleStartedEvent]).
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.ENCOUNTER})

    command_type: Literal["turn_undead"] = "turn_undead"
    character_id: str


class BattleDeclaration(BaseModel):
    """One party member's declared action for a battle round.

    `attack` names a target group and optionally a wielded weapon (`None` is
    unarmed); `cast` names the spell, mode, form, and targets; `move` is the
    range-track intent; `use_item` covers thrown splash items against a group.
    Turn undead resolves in the magic phase but is never disruptable — turning is
    a class ability, not a spell.
    """

    model_config = ConfigDict(frozen=True)

    character_id: str
    action: Literal["attack", "cast", "turn_undead", "move", "use_item", "hold"]
    target_group_id: str | None = None
    weapon_id: str | None = None
    spell_id: str | None = None
    spell_mode: str | None = None
    reversed: bool = False
    targets: tuple[str, ...] = ()
    move: Literal["close", "withdraw", "fighting_withdrawal", "retreat"] | None = None
    item_id: str | None = None


class ResolveBattleRound(Command):
    """Resolve one battle round: one declaration per living, able party member.

    A battle must be underway (see
    [`EngageBattle`][osrlib.crawl.commands.EngageBattle]). Validation is the pure
    pre-phase: every declaration validates or the whole command rejects listing
    every rejection — partial acceptance would tangle the replay contract.

    Modes:
        `battle`

    Rejections:
        - `session.command.wrong_mode` — no battle is underway.
        - `battle.none_active` — defensive twin of the mode gate; not reachable
          through normal play.
        - `battle.declaration.roster_mismatch` — the declarations do not name
          exactly the living, able members.
        - `battle.declaration.unknown_action` — an unrecognized `action`.
        - Move declarations: `battle.declaration.missing_move`,
          `battle.declaration.unknown_group`, `battle.declaration.cannot_move`.
        - Attack declarations: `battle.declaration.unknown_group`,
          `battle.declaration.no_target`, `battle.declaration.weapon_not_wielded`,
          `battle.declaration.not_in_front_rank`, and the kernel attack checks —
          `combat.attack.out_of_reach`, `combat.attack.out_of_range`,
          `combat.attack.reload`, `combat.attack.attacker_incapacitated`,
          `combat.attack.attacker_blind`.
        - Cast declarations: `battle.declaration.missing_spell`,
          `battle.declaration.unknown_group`,
          `battle.declaration.invisible_target`, and the cast checks —
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
        [`BattleRoundEvent`][osrlib.crawl.events.BattleRoundEvent] opens the round;
        declared casts post as
        [`SpellDeclaredEvent`][osrlib.crawl.events.SpellDeclaredEvent]s;
        [`InitiativeRolledEvent`][osrlib.core.events.InitiativeRolledEvent] orders
        the sides. The phases then report themselves — movement, missiles, magic,
        melee: attack and damage rolls, saving throws, casts and disruptions,
        morale checks, routs and defeats — each its own event. A terminal round
        appends [`BattleEndedEvent`][osrlib.crawl.events.BattleEndedEvent] and the
        encounter's conclusion, or
        [`GameOverEvent`][osrlib.crawl.events.GameOverEvent] on a party wipe.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.BATTLE})

    command_type: Literal["resolve_battle_round"] = "resolve_battle_round"
    declarations: tuple[BattleDeclaration, ...] = ()


class GrantItem(Command):
    """Referee: place an item directly into a member's inventory.

    Referee commands are legal in every mode and are logged and replayed like any
    other.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`

    Rejections:
        - `session.command.unknown_member` — `character_id` names no party member.
        - `session.command.unknown_item` — `item_id` names no equipment item.

    Events:
        [`ItemAcquiredEvent`][osrlib.crawl.events.ItemAcquiredEvent] with the
        granted items.
    """

    command_type: Literal["grant_item"] = "grant_item"
    character_id: str
    item_id: str
    quantity: int = Field(default=1, ge=1)


class GrantCoins(Command):
    """Referee: place coins directly into a member's purse.

    Referee commands are legal in every mode.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`

    Rejections:
        - `session.command.unknown_member` — `character_id` names no party member.

    Events:
        [`ItemAcquiredEvent`][osrlib.crawl.events.ItemAcquiredEvent] with the coin
        value.
    """

    command_type: Literal["grant_coins"] = "grant_coins"
    character_id: str
    coins: Coins


class AwardXP(Command):
    """Referee: apply an XP award to one character, outside the adventure award.

    Referee commands are legal in every mode. The award applies the
    prime-requisite modifier and can trigger level gains.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`

    Rejections:
        - `session.command.unknown_member` — `character_id` names no party member.

    Events:
        [`XpAwardedEvent`][osrlib.crawl.events.XpAwardedEvent] with the award, the
        modified award, and the level after.
    """

    command_type: Literal["award_xp"] = "award_xp"
    character_id: str
    amount: int = Field(ge=0)


class SetFlag(Command):
    """Referee: set a session flag (content wiring: the lever opens the portcullis).

    Referee commands are legal in every mode. Flags serialize into saves; game
    code and listeners read them back.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`

    Rejections:
        None.

    Events:
        [`FlagSetEvent`][osrlib.crawl.events.FlagSetEvent] with the key and value.
    """

    command_type: Literal["set_flag"] = "set_flag"
    key: str = Field(min_length=1)
    value: str | int | bool


class SpawnMonsters(Command):
    """Referee: spawn monsters and open an encounter at a distance.

    Referee commands are legal in every mode, but the party must be standing in a
    dungeon with no encounter already open — encounters live on the dungeon grid.
    Exactly one of `count_dice` or `count_fixed` is required.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`

    Rejections:
        - `session.command.unknown_monster` — `template_id` names no monster.
        - `session.command.encounter_in_progress` — an encounter or battle is
          already open.
        - `session.command.not_in_dungeon` — the party is not on a dungeon cell.

    Events:
        [`MonstersSpawnedEvent`][osrlib.crawl.events.MonstersSpawnedEvent], then
        the encounter opening —
        [`SurpriseRolledEvent`][osrlib.crawl.events.SurpriseRolledEvent]s,
        [`EncounterStartedEvent`][osrlib.crawl.events.EncounterStartedEvent], the
        reaction roll and
        [`StanceChangedEvent`][osrlib.crawl.events.StanceChangedEvent]; an attacks
        stance opens battle at once.
    """

    command_type: Literal["spawn_monsters"] = "spawn_monsters"
    template_id: str
    count_dice: str | None = None
    count_fixed: int | None = Field(default=None, ge=1)
    distance_feet: int = Field(ge=0)

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
    1d6+3) — the surface for keyed content, quest listeners, and tests. Referee
    commands are legal in every mode, but the party must be standing in a dungeon
    with no encounter already open.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`

    Rejections:
        - `session.command.encounter_in_progress` — an encounter or battle is
          already open.
        - `session.command.not_in_dungeon` — the party is not on a dungeon cell.

    Events:
        [`NpcPartySpawnedEvent`][osrlib.crawl.events.NpcPartySpawnedEvent] (the
        referee-visibility roster), then the encounter opening as with
        [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters].
    """

    command_type: Literal["spawn_npc_party"] = "spawn_npc_party"
    party_kind: Literal["basic", "expert"]
    count_dice: str | None = None
    distance_feet: int = Field(ge=0)

    @field_validator("count_dice")
    @classmethod
    def _dice_must_parse(cls, value: str | None) -> str | None:
        if value is not None:
            parse(value)
        return value


class SetDoorState(Command):
    """Referee: rewrite a door's overlay anywhere (`None` fields stay unchanged).

    Referee commands are legal in every mode; the door may be on any level of any
    dungeon, not just under the party.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`

    Rejections:
        - `session.command.unknown_location` — `dungeon_id` or `level_number`
          resolves to nothing.
        - `session.command.no_door` — no door edge at that cell and direction.

    Events:
        A referee-visibility [`DoorEvent`][osrlib.crawl.events.DoorEvent] when the
        open state actually changes; otherwise none.
    """

    command_type: Literal["set_door_state"] = "set_door_state"
    dungeon_id: str
    level_number: int = Field(ge=1)
    x: int
    y: int
    direction: Direction
    open: bool | None = None
    wedged: bool | None = None
    discovered: bool | None = None
    unlocked: bool | None = None


class PlaceParty(Command):
    """Referee: teleport the party to a location.

    Referee commands are legal in every mode, except that the party cannot be
    teleported out of an open encounter or battle. Placing into a dungeon marks
    the cell explored and switches the session to `exploring`; placing in town
    switches it to `town`.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`

    Rejections:
        - `session.command.encounter_in_progress` — an encounter or battle is
          open.
        - `session.command.unknown_location` — the location names no dungeon
          level.
        - `session.command.out_of_bounds` — the position is off the level's grid.

    Events:
        [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent] for the
        destination.
    """

    command_type: Literal["place_party"] = "place_party"
    location: PartyLocation


class AdvanceTime(Command):
    """Referee: advance the clock directly.

    Referee commands are legal in every mode. Time passes with full bookkeeping —
    effect expiries, provisions on day boundaries — but no wandering cadence: the
    referee controls encounters.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`

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
    unit: TimeUnit


class RollDice(Command):
    """Referee: roll a dice expression through the seeded session.

    An authorial roll for freeform adjudication — the referee resolves a *chance*
    outcome the content model can't express (a puzzle, a bluff, "does the frayed
    rope hold?") by rolling through the engine rather than inventing a number, so
    the result is logged, replayable, and grounded in a typed event. Referee
    commands are legal in every mode. The roll draws from the dedicated
    [`ADJUDICATION_STREAM`][osrlib.crawl.session.ADJUDICATION_STREAM], so an ad-hoc
    referee roll never perturbs the draw sequence of keyed mechanics. A malformed
    `expression` is rejected at construction, exactly as
    [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters]'s `count_dice` is, so it
    never reaches the session and consumes no draw.

    Modes:
        `town`, `exploring`, `encounter`, `battle`, `game_over`

    Rejections:
        None.

    Events:
        [`DiceRolledEvent`][osrlib.crawl.events.DiceRolledEvent] with the
        expression, the total, and the individual die results.
    """

    command_type: Literal["roll_dice"] = "roll_dice"
    expression: str

    @field_validator("expression")
    @classmethod
    def _expression_must_parse(cls, value: str) -> str:
        parse(value)
        return value


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
    LightSource,
    ExtinguishSource,
    EquipItem,
    UnequipItem,
    Rest,
    PrepareSpells,
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
)
"""Every command class — the discriminated union's members, in a stable wire order."""

AnyCommand = Annotated[
    Union[*ALL_COMMAND_CLASSES],
    Field(discriminator="command_type"),
]
"""Any command, discriminated by `command_type`."""


@cache
def _any_command_adapter() -> TypeAdapter:
    return TypeAdapter(AnyCommand)


@cache
def _known_command_types() -> frozenset[str]:
    return frozenset(variant.model_fields["command_type"].default for variant in ALL_COMMAND_CLASSES)


def parse_command(data: Mapping[str, object]) -> Command | None:
    """Parse one serialized command, skipping unknown command types.

    Args:
        data: A mapping previously produced by a command's `model_dump`.

    Returns:
        The command, or `None` when its `command_type` is unknown.

    Raises:
        ContentValidationError: If the command type is known but the payload is
            malformed.
    """
    from osrlib.errors import ContentValidationError

    if data.get("command_type") not in _known_command_types():
        return None
    try:
        return _any_command_adapter().validate_python(data)
    except ValidationError as error:
        raise ContentValidationError(f"malformed command: {error}") from error
