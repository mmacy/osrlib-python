"""The crawl event types, the combined registry, and the any-event parser.

Crawl events subclass the core [`Event`][osrlib.core.events.Event] base, inheriting
the emission contract (frozen, `extra="ignore"`, dotted snake_case codes, declared
outcome-bearing code sets, visibility). `CRAWL_EVENT_CLASSES` joins the kernel tuple
in [`ALL_EVENT_CLASSES`][osrlib.crawl.events.ALL_EVENT_CLASSES] and the
[`AnyEvent`][osrlib.crawl.events.AnyEvent] union;
[`parse_any_event`][osrlib.crawl.events.parse_any_event] covers both and the session
log uses it.

Visibility follows B/X's hidden-roll doctrine: referee-rolled dice (detection,
surprise, reaction, wandering checks) are referee events, and the player-facing
events carry behavior and outcomes only — a silent listen is genuinely ambiguous.
"""

from collections.abc import Mapping
from functools import cache
from typing import Annotated, ClassVar, Literal, Union

from pydantic import Field, TypeAdapter, ValidationError

from osrlib.core.events import KERNEL_EVENT_CLASSES, Event, Visibility

__all__ = [
    "ALL_EVENT_CLASSES",
    "AdventureXpAwardEvent",
    "AnyEvent",
    "BattleEndedEvent",
    "BattleRoundEvent",
    "BattleStartedEvent",
    "CRAWL_EVENT_CLASSES",
    "CurseRevealedEvent",
    "DetectionRolledEvent",
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
    "ItemIdentifiedEvent",
    "ItemUsedEvent",
    "ItemsDroppedEvent",
    "LightEvent",
    "ListenedEvent",
    "LocationEnteredEvent",
    "MonsterDefeatedEvent",
    "MonsterFledEvent",
    "MonstersSpawnedEvent",
    "NpcPartySpawnedEvent",
    "PartyMovedEvent",
    "ProvisionsEvent",
    "PursuitEvent",
    "RestedEvent",
    "SearchCompletedEvent",
    "SpellDeclaredEvent",
    "StanceChangedEvent",
    "SurpriseRolledEvent",
    "TimeAdvancedEvent",
    "TrapEvent",
    "TreasureSoldEvent",
    "WanderingCheckEvent",
    "XpAwardedEvent",
    "parse_any_event",
]


class PartyMovedEvent(Event):
    """The party moved or turned; `x`/`y`/`facing` are the resulting pose.

    A blocked move is a rejection (`exploration.move.blocked`), never an event —
    moving into a wall is an in-fiction invalid command (pinned).
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.party.moved", "exploration.party.turned"})

    event_type: Literal["party_moved"] = "party_moved"
    visibility: Visibility = Visibility.PLAYER
    x: int
    y: int
    facing: str


class LocationEnteredEvent(Event):
    """The party crossed a location boundary — the spec's listener example event.

    `location_kind` is `area`, `level`, `dungeon`, or `town`; `location_id` is the
    area or dungeon id (`"town"` for town). `level_number` rides level and dungeon
    entries.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.location.entered"})

    event_type: Literal["location_entered"] = "location_entered"
    code: str = "exploration.location.entered"
    visibility: Visibility = Visibility.PLAYER
    location_kind: str
    location_id: str
    level_number: int | None = None


class DoorEvent(Event):
    """A door changed state; the edge is named by its cell and direction."""

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

    event_type: Literal["door"] = "door"
    visibility: Visibility = Visibility.PLAYER
    x: int
    y: int
    direction: str
    character_id: str | None = None


class ListenedEvent(Event):
    """What the listener heard — heard-something or silence, genuinely ambiguous.

    Undead make no noise, so the referee-side roll (which rides
    [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent]) happens
    whether or not anything is there; silence never says which.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.listen.heard", "exploration.listen.silent"})

    event_type: Literal["listened"] = "listened"
    visibility: Visibility = Visibility.PLAYER
    character_id: str
    direction: str


class DetectionRolledEvent(Event):
    """A referee-rolled detection die: search, listen, and trap-spring checks.

    Rolled whether or not anything is there (the no-leak convention); `roll` is
    `None` when a zero chance consumed no die.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.detection.rolled"})

    event_type: Literal["detection_rolled"] = "detection_rolled"
    code: str = "exploration.detection.rolled"
    visibility: Visibility = Visibility.REFEREE
    character_id: str | None = None
    kind: str
    chance: int
    roll: int | None = None
    passed: bool


class SearchCompletedEvent(Event):
    """A search finished: what it revealed, or nothing (which is ambiguous)."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.search.found", "exploration.search.nothing"})

    event_type: Literal["search_completed"] = "search_completed"
    visibility: Visibility = Visibility.PLAYER
    character_id: str
    kind: str
    found: tuple[str, ...] = ()


class TrapEvent(Event):
    """A trap outcome the party perceives.

    `.sprung` when a trap goes off, `.found` when a search or inspection reveals
    one, `.removed` on a successful removal, `.safe` when a *known* trap's trigger
    resolved without springing — never emitted for unknown traps (the spring die
    rides the referee-visibility
    [`DetectionRolledEvent`][osrlib.crawl.events.DetectionRolledEvent], no-leak).
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {"exploration.trap.sprung", "exploration.trap.safe", "exploration.trap.found", "exploration.trap.removed"}
    )

    event_type: Literal["trap"] = "trap"
    visibility: Visibility = Visibility.PLAYER
    trap_ref: str
    character_id: str | None = None


class ItemAcquiredEvent(Event):
    """Items or coins entered a character's inventory — the spec's listener example."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.item.acquired"})

    event_type: Literal["item_acquired"] = "item_acquired"
    code: str = "exploration.item.acquired"
    visibility: Visibility = Visibility.PLAYER
    character_id: str
    item_ids: tuple[str, ...] = ()
    coins_gp_value: int = 0


class ItemsDroppedEvent(Event):
    """Items or coins dropped onto the party's cell (or the pursuit trail)."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.item.dropped"})

    event_type: Literal["items_dropped"] = "items_dropped"
    code: str = "exploration.item.dropped"
    visibility: Visibility = Visibility.PLAYER
    character_id: str
    item_ids: tuple[str, ...] = ()
    coins_gp_value: int = 0


class LightEvent(Event):
    """A light source changed state.

    `source` is the item or effect kind (`torch`, `lantern`, `light`); `.failed`
    is a failed tinder-box attempt; `.expired` is the session's player-facing
    translation of the ledger's referee-visibility expiry (the pinned mechanism).
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {
            "exploration.light.lit",
            "exploration.light.extinguished",
            "exploration.light.failed",
            "exploration.light.expired",
        }
    )

    event_type: Literal["light"] = "light"
    visibility: Visibility = Visibility.PLAYER
    character_id: str | None = None
    source: str


class RestedEvent(Event):
    """A rest completed or was interrupted; `kind` is `turn`, `night`, or `day`."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.rest.rested", "exploration.rest.interrupted"})

    event_type: Literal["rested"] = "rested"
    visibility: Visibility = Visibility.PLAYER
    kind: str


class FatigueEvent(Event):
    """The party gained or recovered from the unrested-fatigue penalty."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.fatigue.gained", "exploration.fatigue.recovered"})

    event_type: Literal["fatigue"] = "fatigue"
    visibility: Visibility = Visibility.PLAYER


class ProvisionsEvent(Event):
    """A day-boundary provision outcome: consumed, or short (food or water)."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {"exploration.provisions.consumed", "exploration.provisions.short"}
    )

    event_type: Literal["provisions"] = "provisions"
    visibility: Visibility = Visibility.PLAYER
    character_id: str
    kind: str


class WanderingCheckEvent(Event):
    """A wandering-monster check fired (referee bookkeeping).

    `roll` is `None` when the clamped chance was 0 and the roll was skipped.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"exploration.wandering.checked"})

    event_type: Literal["wandering_check"] = "wandering_check"
    code: str = "exploration.wandering.checked"
    visibility: Visibility = Visibility.REFEREE
    chance: int
    roll: int | None = None
    encounter: bool


class EncounterStartedEvent(Event):
    """An encounter opened: visible monster names and counts only.

    The surprise *rolls* are referee events; the outcomes ride here — being
    surprised is felt in the fiction.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"encounter.started"})

    event_type: Literal["encounter_started"] = "encounter_started"
    code: str = "encounter.started"
    visibility: Visibility = Visibility.PLAYER
    monster_name: str
    count: int
    distance_feet: int
    party_surprised: bool = False
    monsters_surprised: bool = False


class SurpriseRolledEvent(Event):
    """One side's surprise die (referee); `roll` is `None` when the side never rolls."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"encounter.surprise.rolled"})

    event_type: Literal["surprise_rolled"] = "surprise_rolled"
    code: str = "encounter.surprise.rolled"
    visibility: Visibility = Visibility.REFEREE
    side: str
    threshold: int
    roll: int | None = None
    surprised: bool


class StanceChangedEvent(Event):
    """The monsters' stance, as behavior — the reaction roll itself is referee."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"encounter.stance.changed"})

    event_type: Literal["stance_changed"] = "stance_changed"
    code: str = "encounter.stance.changed"
    visibility: Visibility = Visibility.PLAYER
    stance: str


class EvasionEvent(Event):
    """An evasion attempt resolved: immediate success, or a pursuit begins."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"encounter.evasion.succeeded", "encounter.evasion.pursuit"})

    event_type: Literal["evasion"] = "evasion"
    visibility: Visibility = Visibility.PLAYER


class PursuitEvent(Event):
    """One pursuit beat: the round's gap, a distraction, escape, or capture."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {
            "encounter.pursuit.round",
            "encounter.pursuit.distracted",
            "encounter.pursuit.escaped",
            "encounter.pursuit.caught",
        }
    )

    event_type: Literal["pursuit"] = "pursuit"
    visibility: Visibility = Visibility.PLAYER
    round: int
    gap_feet: int


class ExhaustionEvent(Event):
    """The party gained or recovered from running exhaustion (30 rounds, −2s)."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {"encounter.exhaustion.gained", "encounter.exhaustion.recovered"}
    )

    event_type: Literal["exhaustion"] = "exhaustion"
    visibility: Visibility = Visibility.PLAYER


class EncounterEndedEvent(Event):
    """The encounter concluded; the clock owes at least one full turn."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"encounter.ended"})

    event_type: Literal["encounter_ended"] = "encounter_ended"
    code: str = "encounter.ended"
    visibility: Visibility = Visibility.PLAYER
    outcome: str


class BattleStartedEvent(Event):
    """Battle began: the range-track machine takes over."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"battle.started"})

    event_type: Literal["battle_started"] = "battle_started"
    code: str = "battle.started"
    visibility: Visibility = Visibility.PLAYER


class BattleRoundEvent(Event):
    """A battle round began."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"battle.round.started"})

    event_type: Literal["battle_round"] = "battle_round"
    code: str = "battle.round.started"
    visibility: Visibility = Visibility.PLAYER
    round: int


class SpellDeclaredEvent(Event):
    """A spell declaration posted — table-visible per RAW."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"battle.spell.declared"})

    event_type: Literal["spell_declared"] = "spell_declared"
    code: str = "battle.spell.declared"
    visibility: Visibility = Visibility.PLAYER
    caster_id: str
    spell_id: str
    reversed: bool = False


class GroupMovedEvent(Event):
    """A group's range-track distance changed."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"battle.group.moved"})

    event_type: Literal["group_moved"] = "group_moved"
    code: str = "battle.group.moved"
    visibility: Visibility = Visibility.PLAYER
    group_id: str
    distance_feet: int


class MonsterFledEvent(Event):
    """A monster group broke: fled the battle or surrendered."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"battle.side.fled", "battle.side.surrendered"})

    event_type: Literal["monster_fled"] = "monster_fled"
    visibility: Visibility = Visibility.PLAYER
    group_id: str


class MonsterDefeatedEvent(Event):
    """One monster defeated — the spec's listener example and the Phase 5 XP input.

    Emitted per monster at battle end with `outcome` `slain`, `routed`, or
    `surrendered`; `xp` is the template's printed award.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"battle.monster.defeated"})

    event_type: Literal["monster_defeated"] = "monster_defeated"
    code: str = "battle.monster.defeated"
    visibility: Visibility = Visibility.PLAYER
    monster_id: str
    template_id: str
    outcome: str
    xp: int


class BattleEndedEvent(Event):
    """The battle ended: victory, the party fled, or defeat."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {"battle.ended.victory", "battle.ended.fled", "battle.ended.defeat"}
    )

    event_type: Literal["battle_ended"] = "battle_ended"
    visibility: Visibility = Visibility.PLAYER


class HoardGeneratedEvent(Event):
    """A lair hoard, carried bundle, or area treasure generated (referee).

    Referee visibility — contents are itemized here and players learn by finding.
    `cache_ref` is the engine-created cache's state reference (or the group id for
    carried bundles); value and counts summarize the generation.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"treasure.hoard.generated"})

    event_type: Literal["hoard_generated"] = "hoard_generated"
    code: str = "treasure.hoard.generated"
    visibility: Visibility = Visibility.REFEREE
    cache_ref: str
    treasure_types: tuple[str, ...] = ()
    coins_gp_value: int = 0
    valuable_ids: tuple[str, ...] = ()
    magic_item_ids: tuple[str, ...] = ()


class ItemUsedEvent(Event):
    """A magic item used: a potion drunk (or mixed), a scroll read, a device activated.

    `items.device.inert` is a rejection code, not an event — activating an
    exhausted device costs nothing (the Phase 4 blocked-move precedent). Charges
    never appear here: they are referee-only forever (RAW, undiscoverable).
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

    event_type: Literal["item_used"] = "item_used"
    visibility: Visibility = Visibility.PLAYER
    character_id: str
    instance_id: str
    manual: tuple[str, ...] = ()


class ItemIdentifiedEvent(Event):
    """A magic item identified — first meaningful use is the trigger (pinned)."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"items.item.identified"})

    event_type: Literal["item_identified"] = "item_identified"
    code: str = "items.item.identified"
    visibility: Visibility = Visibility.PLAYER
    instance_id: str
    template_id: str


class CurseRevealedEvent(Event):
    """A cursed item revealed its true nature — and pinned itself to its bearer."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"items.curse.revealed"})

    event_type: Literal["curse_revealed"] = "curse_revealed"
    code: str = "items.curse.revealed"
    visibility: Visibility = Visibility.PLAYER
    character_id: str
    instance_id: str
    template_id: str


class NpcPartySpawnedEvent(Event):
    """An NPC adventuring party generated and fielded (referee — the full roster).

    The player-facing `EncounterStartedEvent` names "adventurers" and the count;
    the roster, classes, and levels are the referee's.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"encounter.npc_party.spawned"})

    event_type: Literal["npc_party_spawned"] = "npc_party_spawned"
    code: str = "encounter.npc_party.spawned"
    visibility: Visibility = Visibility.REFEREE
    party_kind: str
    npc_ids: tuple[str, ...]
    class_ids: tuple[str, ...]
    levels: tuple[int, ...]
    alignment: str


class AdventureXpAwardEvent(Event):
    """The end-of-adventure XP award: the totals and the per-head share."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.xp.adventure_award"})

    event_type: Literal["adventure_xp_award"] = "adventure_xp_award"
    code: str = "session.xp.adventure_award"
    visibility: Visibility = Visibility.PLAYER
    monster_xp: int
    treasure_xp: int
    share: int
    survivors: tuple[str, ...]


class TreasureSoldEvent(Event):
    """Valuables sold in town at full value (the 1-gp-1-XP identity kept clean)."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"town.treasure.sold"})

    event_type: Literal["treasure_sold"] = "treasure_sold"
    code: str = "town.treasure.sold"
    visibility: Visibility = Visibility.PLAYER
    character_id: str
    instance_ids: tuple[str, ...]
    gp_value: int


class HealingPurchasedEvent(Event):
    """A temple healing service purchased and cast."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"town.healing.purchased"})

    event_type: Literal["healing_purchased"] = "healing_purchased"
    code: str = "town.healing.purchased"
    visibility: Visibility = Visibility.PLAYER
    character_id: str
    service: str
    cost_gp: int


class FlagSetEvent(Event):
    """A session flag changed (referee — content wiring is the game's secret)."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.flag.set"})

    event_type: Literal["flag_set"] = "flag_set"
    code: str = "session.flag.set"
    visibility: Visibility = Visibility.REFEREE
    key: str
    value: str | int | bool


class MonstersSpawnedEvent(Event):
    """Monsters spawned into the session registry (referee bookkeeping)."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.monsters.spawned"})

    event_type: Literal["monsters_spawned"] = "monsters_spawned"
    code: str = "session.monsters.spawned"
    visibility: Visibility = Visibility.REFEREE
    template_id: str
    monster_ids: tuple[str, ...]


class XpAwardedEvent(Event):
    """An XP award applied to one character."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.xp.awarded"})

    event_type: Literal["xp_awarded"] = "xp_awarded"
    code: str = "session.xp.awarded"
    visibility: Visibility = Visibility.PLAYER
    character_id: str
    award: int
    modified_award: int
    level_after: int


class TimeAdvancedEvent(Event):
    """The clock advanced (referee bookkeeping); `rounds_total` is the new position."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.time.advanced"})

    event_type: Literal["time_advanced"] = "time_advanced"
    code: str = "session.time.advanced"
    visibility: Visibility = Visibility.REFEREE
    n: int
    unit: str
    rounds_total: int


class GameOverEvent(Event):
    """The session reached its terminal state (TPK)."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"session.game_over"})

    event_type: Literal["game_over"] = "game_over"
    code: str = "session.game_over"
    visibility: Visibility = Visibility.PLAYER
    reason: str


CRAWL_EVENT_CLASSES: tuple[type[Event], ...] = (
    PartyMovedEvent,
    LocationEnteredEvent,
    DoorEvent,
    ListenedEvent,
    DetectionRolledEvent,
    SearchCompletedEvent,
    TrapEvent,
    ItemAcquiredEvent,
    ItemsDroppedEvent,
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
    TimeAdvancedEvent,
    GameOverEvent,
)
"""Every crawl event class, in declaration order."""

ALL_EVENT_CLASSES: tuple[type[Event], ...] = (*KERNEL_EVENT_CLASSES, *CRAWL_EVENT_CLASSES)
"""Every event class the library emits — kernel then crawl, in declaration order."""

AnyEvent = Annotated[
    Union[*ALL_EVENT_CLASSES],
    Field(discriminator="event_type"),
]
"""Any library event, discriminated by `event_type`."""


@cache
def _any_event_adapter() -> TypeAdapter:
    return TypeAdapter(AnyEvent)


@cache
def _known_event_types() -> frozenset[str]:
    return frozenset(variant.model_fields["event_type"].default for variant in ALL_EVENT_CLASSES)


def parse_any_event(data: Mapping[str, object]) -> Event | None:
    """Parse one serialized event, kernel or crawl, skipping unknown event types.

    The session log's parser: an `event_type` this library doesn't know returns
    `None` instead of raising, so a newer producer's log loads under an older
    consumer (the session preserves the raw record).

    Args:
        data: A mapping previously produced by an event's `model_dump`.

    Returns:
        The event, or `None` when its `event_type` is unknown.

    Raises:
        ContentValidationError: If the event type is known but the payload is
            malformed.
    """
    from osrlib.errors import ContentValidationError

    if data.get("event_type") not in _known_event_types():
        return None
    try:
        return _any_event_adapter().validate_python(data)
    except ValidationError as error:
        raise ContentValidationError(f"malformed event: {error}") from error
