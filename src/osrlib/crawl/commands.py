"""The command set: typed models, the discriminated union, and the result envelope.

Commands mirror the event conventions exactly: frozen pydantic models, a
single-valued `command_type` Literal discriminator (snake_case, schema-stable,
additive-only), an [`AnyCommand`][osrlib.crawl.commands.AnyCommand] discriminated
union, and [`parse_command`][osrlib.crawl.commands.parse_command] returning `None`
on unknown types. [`CommandResult`][osrlib.crawl.commands.CommandResult] is the
envelope `GameSession.execute` returns: `accepted`, the Phase 1
[`Rejection`][osrlib.core.validation.Rejection] model verbatim, and the events.

Each command declares its legal session modes as a `allowed_modes` class attribute
— the census's mode gating as data; the session rejects a wrong-mode command with
`session.command.wrong_mode`. Referee commands are legal in every mode and are
logged and replayed like any other, per the spec.
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
    """Move the party one cell; facing follows the movement direction."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["move_party"] = "move_party"
    direction: Direction


class TurnParty(Command):
    """Turn the party in place (zero time)."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["turn_party"] = "turn_party"
    facing: Direction


class ReorderParty(Command):
    """Rewrite the marching order — its only mutation path (marching order is state)."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["reorder_party"] = "reorder_party"
    order: tuple[str, ...] = Field(min_length=1)


class OpenDoor(Command):
    """Open an unstuck, unlocked door on one side of the party's cell (zero time)."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["open_door"] = "open_door"
    direction: Direction


class CloseDoor(Command):
    """Close an open door (zero time)."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["close_door"] = "close_door"
    direction: Direction


class ForceDoor(Command):
    """Force a stuck door: the STR open-doors check; the noise is the cost."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["force_door"] = "force_door"
    direction: Direction
    character_id: str


class WedgeDoor(Command):
    """Wedge a door with an iron spike so it cannot swing shut (zero time)."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["wedge_door"] = "wedge_door"
    direction: Direction


class ListenAtDoor(Command):
    """Listen at a door: once per character per door, ever (zero time)."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["listen_at_door"] = "listen_at_door"
    direction: Direction
    character_id: str


class PickLock(Command):
    """Pick a locked door's lock: thief-only, needs thieves' tools, one turn."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["pick_lock"] = "pick_lock"
    direction: Direction
    character_id: str


class Search(Command):
    """Search the party's cell for one hidden-feature kind (one turn)."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["search"] = "search"
    character_id: str
    kind: Literal["secret_doors", "room_traps", "construction"]


class InspectTreasure(Command):
    """Search a treasure feature for a treasure trap: thief-only, one turn."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["inspect_treasure"] = "inspect_treasure"
    character_id: str
    feature_id: str


class RemoveTreasureTrap(Command):
    """Remove a found treasure trap: thief-only, one turn; failure springs it."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["remove_treasure_trap"] = "remove_treasure_trap"
    character_id: str
    feature_id: str


class TakeTreasure(Command):
    """Empty a cache or pile into the party's packs (one turn, RAW)."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["take_treasure"] = "take_treasure"
    feature_id: str


class DropItems(Command):
    """Drop items and coins onto the party's cell (or the pursuit trail).

    Each `item_ids` entry drops one unit (repeat an id for more). Legal during an
    encounter too — dropping treasure or food is the pursuit-distraction move.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING, SessionMode.ENCOUNTER})

    command_type: Literal["drop_items"] = "drop_items"
    character_id: str
    item_ids: tuple[str, ...] = ()
    coins: Coins = Coins()


class LightSource(Command):
    """Light a torch or lantern (one round; per tinder attempt too, RAW)."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["light_source"] = "light_source"
    character_id: str
    item_id: str


class ExtinguishSource(Command):
    """Extinguish the bearer's burning source, forfeiting the remainder (zero time)."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["extinguish_source"] = "extinguish_source"
    character_id: str


class EquipItem(Command):
    """Equip an item from a member's item list (zero time)."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["equip_item"] = "equip_item"
    character_id: str
    item_id: str


class UnequipItem(Command):
    """Return an equipped item to the member's item list (zero time)."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["unequip_item"] = "unequip_item"
    character_id: str
    item_id: str


class Rest(Command):
    """Rest: one turn (the cadence rest), a night (48 turns), or a full day (144)."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["rest"] = "rest"
    kind: Literal["turn", "night", "day"]


class PrepareSpells(Command):
    """Prepare a caster's daily spells: once per sleep, after an uninterrupted night, six turns."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = _FIELD_MODES

    command_type: Literal["prepare_spells"] = "prepare_spells"
    character_id: str
    selections: tuple[MemorizedSpell, ...] = ()


class CastSpell(Command):
    """Cast a memorized spell outside battle (one round).

    `targets` are entity ids, or `cell:` references for location-bound casts.
    In encounter mode a hostile cast is opened through `EngageBattle` and the first
    round's declarations instead; in battle, casting is a declaration kind.
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

    One round is the RAW activation cost (drinking is one round). `target_id` names
    a character (the staff of healing's touch) or an encounter group (a device's
    area); `spell_id`, `mode`, and `targets` select the inscribed spell and its
    targets when reading a multi-spell scroll (the `CastSpell` surface). In battle,
    item use is the `use_item` declaration instead.
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
    """Referee: identify a magic item outright — game-driven identification."""

    command_type: Literal["identify_item"] = "identify_item"
    character_id: str
    item_id: str


class UseStairs(Command):
    """Take the transition on the party's cell (one unexplored-cell cost, pinned)."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["use_stairs"] = "use_stairs"


class EnterDungeon(Command):
    """Travel from town to a dungeon's entrance (the content-authored travel cost)."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.TOWN})

    command_type: Literal["enter_dungeon"] = "enter_dungeon"
    dungeon_id: str


class TravelToTown(Command):
    """Travel from the dungeon entrance back to town (the same travel cost)."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.EXPLORING})

    command_type: Literal["travel_to_town"] = "travel_to_town"


class PurchaseEquipment(Command):
    """Buy equipment in town: each `item_ids` entry buys one purchase lot (zero time)."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.TOWN})

    command_type: Literal["purchase_equipment"] = "purchase_equipment"
    character_id: str
    item_ids: tuple[str, ...] = Field(min_length=1)


class SellTreasure(Command):
    """Sell valuables in town at full value (zero time).

    Each entry names a carried valuable's instance id; the coins credit its
    carrier's purse. Full `value_gp` is pinned and registered: the SRD prices
    treasure but names no exchange spread, and full value keeps the 1-gp-1-XP
    identity clean. Magic items reject with `town.sell.no_fixed_value` (RAW's own
    words) and revealed curses stick.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.TOWN})

    command_type: Literal["sell_treasure"] = "sell_treasure"
    item_ids: tuple[str, ...] = Field(min_length=1)


class PurchaseHealing(Command):
    """Buy a temple healing service in town (zero time).

    The service list and prices are invented and registered (the SRD's base-town
    page is prose): *cure light wounds* 25 gp, *cure serious wounds* 100 gp,
    *cure disease* 150 gp, *neutralize poison* 150 gp, *remove curse* 200 gp,
    *raise dead* 1,500 gp. Each resolves through the kernel spell path with an
    abstract temple cleric at the minimum level able to cast the spell; the named
    character is the target and pays from their own purse.
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
    """Speak with the monsters: a fresh reaction roll with the speaker's CHA modifier."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.ENCOUNTER})

    command_type: Literal["parley"] = "parley"
    character_id: str


class Evade(Command):
    """Flee the encounter (legal only before battle begins, RAW).

    `drop` scatters distraction bait as the party runs: treasure tempts
    intelligent monsters, food unintelligent ones.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.ENCOUNTER})

    command_type: Literal["evade"] = "evade"
    drop: Literal["none", "treasure", "food"] = "none"


class EngageBattle(Command):
    """Open battle: every offensive action goes through here (except turn undead)."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.ENCOUNTER})

    command_type: Literal["engage_battle"] = "engage_battle"


class Wait(Command):
    """Hold for one encounter round; the monsters act per their stance."""

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.ENCOUNTER})

    command_type: Literal["wait"] = "wait"


class TurnUndead(Command):
    """Present the holy symbol — the one aggressive act with a pre-battle procedure.

    Encounter-mode only, pinned: exploration has no candidates by definition, and
    in battle turning is a declaration kind.
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.ENCOUNTER})

    command_type: Literal["turn_undead"] = "turn_undead"
    character_id: str


class BattleDeclaration(BaseModel):
    """One party member's declared action for a battle round.

    `attack` names a target group and optionally a wielded weapon (`None` is
    unarmed); `cast` names the spell, mode, form, and targets; `move` is the
    range-track intent; `use_item` covers thrown splash items against a group.
    Turn undead resolves in the magic phase but is never disruptable — turning is a
    class ability, not a spell (pinned).
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

    Validation is the pure pre-phase: every declaration validates or the whole
    command rejects listing every rejection — partial acceptance would tangle the
    replay contract (pinned).
    """

    allowed_modes: ClassVar[frozenset[SessionMode]] = frozenset({SessionMode.BATTLE})

    command_type: Literal["resolve_battle_round"] = "resolve_battle_round"
    declarations: tuple[BattleDeclaration, ...] = ()


class GrantItem(Command):
    """Referee: place an item directly into a member's inventory."""

    command_type: Literal["grant_item"] = "grant_item"
    character_id: str
    item_id: str
    quantity: int = Field(default=1, ge=1)


class GrantCoins(Command):
    """Referee: place coins directly into a member's purse."""

    command_type: Literal["grant_coins"] = "grant_coins"
    character_id: str
    coins: Coins


class AwardXP(Command):
    """Referee: apply an XP award to one character — Phase 5's award will drive it."""

    command_type: Literal["award_xp"] = "award_xp"
    character_id: str
    amount: int = Field(ge=0)


class SetFlag(Command):
    """Referee: set a session flag (content wiring: the lever opens the portcullis)."""

    command_type: Literal["set_flag"] = "set_flag"
    key: str = Field(min_length=1)
    value: str | int | bool


class SpawnMonsters(Command):
    """Referee: spawn monsters and open an encounter at a distance."""

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
    1d6+3) — the surface for keyed content, quest listeners, and tests.
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
    """Referee: rewrite a door's overlay anywhere (`None` fields stay unchanged)."""

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
    """Referee: teleport the party to a location."""

    command_type: Literal["place_party"] = "place_party"
    location: PartyLocation


class AdvanceTime(Command):
    """Referee: advance the clock directly."""

    command_type: Literal["advance_time"] = "advance_time"
    n: int = Field(ge=0)
    unit: TimeUnit


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
)
"""Every command class, in census order — the discriminated union's members."""

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
