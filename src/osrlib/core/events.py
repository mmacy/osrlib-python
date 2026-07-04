"""The event base class, the emission contract, and the first kernel events.

Every rules resolution emits typed events, and this module locks the rules all of them
obey:

- Events carry structured fields and a message code — dotted snake_case namespaced by
  subsystem (`combat.attack.hit`, `exploration.torch.expired`) — never baked English
  prose. The default English message formatter ships outside the event models (in
  [`osrlib.messages`][osrlib.messages]), so front ends can localize and LLM narrators
  get facts rather than canned text.
- Events carry a visibility level, because B/X hides some rolls by design: monster hit
  points and morale rolls are the referee's. Front ends filter on it; an LLM referee
  sees everything.
- Consumers must tolerate unknown event types and unknown fields: within a
  `schema_version`, the event schema grows additively only. The base class enforces
  `extra="ignore"` and `frozen=True` on every subclass at class-definition time, so no
  subclass can silently break that guarantee with `extra="forbid"` or a mutable config.

The serialized type discriminator — the decision Phase 0 deferred — is pinned here:
every kernel event class declares a single-valued `event_type: Literal[...]` wire
field (snake_case, schema-stable, additive-only). Pydantic discriminates the
[`KernelEvent`][osrlib.core.events.KernelEvent] union on it, giving native tagged-union
JSON Schema for API consumers and mechanical "ignore unknown event types" — see
[`parse_event`][osrlib.core.events.parse_event]. Message codes stay free to be
*outcome-bearing*: one event class may emit several codes from its declared closed set
(`combat.attack.hit` / `combat.attack.missed` on the attack event), so formatters and
narration key off codes while consumers discriminate on `event_type`.
"""

import re
from collections.abc import Mapping
from enum import StrEnum
from functools import cache
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator, model_validator

__all__ = [
    "AttackRolledEvent",
    "ConditionGainedEvent",
    "ConditionRemovedEvent",
    "DamageAbsorbedEvent",
    "DamageDealtEvent",
    "DeathEvent",
    "EffectAttachedEvent",
    "EffectExpiredEvent",
    "EffectReleasedEvent",
    "EffectTickedEvent",
    "EquipmentDestroyedEvent",
    "Event",
    "HealingAppliedEvent",
    "HitPointsReportedEvent",
    "InitiativeRoll",
    "InitiativeRolledEvent",
    "KERNEL_EVENT_CLASSES",
    "KernelEvent",
    "LevelDrainedEvent",
    "MagicDispelledEvent",
    "MonsterRevivedEvent",
    "MoraleCheckedEvent",
    "PreparedSpell",
    "ReactionRolledEvent",
    "SavingThrowRolledEvent",
    "SpellBookUpdatedEvent",
    "SpellCastEvent",
    "SpellDisruptedEvent",
    "SpellForgottenEvent",
    "SpellsMemorizedEvent",
    "TargetsSelectedEvent",
    "TurningTypeOutcome",
    "UndeadTurnedEvent",
    "Visibility",
    "parse_event",
]

_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")


class Visibility(StrEnum):
    """Who may see an event.

    The wire values are `"player"` and `"referee"` — lowercase, serialized into every
    event; changing them is a `schema_version` bump.
    """

    PLAYER = "player"
    REFEREE = "referee"


class Event(BaseModel):
    """Base class for all osrlib events.

    Events are frozen: they are records of what happened, appended to the session log,
    never mutated. Subclasses add structured fields only — entity IDs, roll results,
    quantities — and must never bake in English prose.

    `code` is the event's message code: two or more dot-separated segments, each
    matching `[a-z][a-z0-9_]*`, namespaced by subsystem (`combat.attack.hit`). A
    subclass declaring an `allowed_codes` class attribute pins its outcome-bearing
    code set: instances must carry one of them.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    allowed_codes: ClassVar[frozenset[str]] = frozenset()

    code: str
    visibility: Visibility

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        """Reject subclasses that weaken the emission contract via `model_config`."""
        super().__pydantic_init_subclass__(**kwargs)
        if cls.model_config.get("extra") != "ignore":
            raise TypeError(
                f"{cls.__name__} must keep extra='ignore': consumers ignore unknown fields, "
                "and the event schema grows additively within a schema_version"
            )
        if not cls.model_config.get("frozen"):
            raise TypeError(f"{cls.__name__} must stay frozen: events are immutable records of what happened")

    @field_validator("code")
    @classmethod
    def _code_must_be_dotted_snake_case(cls, value: str) -> str:
        if _CODE_PATTERN.fullmatch(value) is None:
            raise ValueError(
                "event code must be two or more dot-separated snake_case segments "
                f"(like 'combat.attack.hit'), got {value!r}"
            )
        return value

    @model_validator(mode="after")
    def _code_within_declared_set(self) -> Event:
        if self.allowed_codes and self.code not in self.allowed_codes:
            raise ValueError(f"{type(self).__name__} emits {sorted(self.allowed_codes)}, got {self.code!r}")
        return self


class InitiativeRoll(BaseModel):
    """One participant's (or side's) initiative rolls: re-rolls included, ties re-roll."""

    model_config = ConfigDict(frozen=True)

    key: str
    rolls: tuple[int, ...]
    modifier: int = 0
    total: int


class InitiativeRolledEvent(Event):
    """Initiative rolled for a round: every roll (re-rolls included) and the acting order."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"combat.initiative.rolled"})

    event_type: Literal["initiative_rolled"] = "initiative_rolled"
    code: str = "combat.initiative.rolled"
    visibility: Visibility = Visibility.PLAYER
    mode: Literal["side", "individual"]
    entries: tuple[InitiativeRoll, ...]
    order: tuple[str, ...]


class AttackRolledEvent(Event):
    """An attack roll resolved: the die, the modifiers, and what it needed.

    `roll`, `total`, and `required` are `None` for the helpless auto-hit (no roll is
    consumed, pinned); `natural` carries 1 or 20 when the natural roll overrode the
    modified total.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {"combat.attack.hit", "combat.attack.missed", "combat.attack.auto_hit"}
    )

    event_type: Literal["attack_rolled"] = "attack_rolled"
    visibility: Visibility = Visibility.PLAYER
    attacker_id: str
    defender_id: str
    attack_name: str
    roll: int | None = None
    modifier: int = 0
    total: int | None = None
    required: int | None = None
    defender_ac: int | None = None
    natural: int | None = None


class DamageDealtEvent(Event):
    """Damage applied to a creature.

    Carries the amount only — never the target's remaining hit points: monster HP is
    hidden by design, and the referee-visibility
    [`HitPointsReportedEvent`][osrlib.core.events.HitPointsReportedEvent] carries it.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"combat.damage.dealt"})

    event_type: Literal["damage_dealt"] = "damage_dealt"
    code: str = "combat.damage.dealt"
    visibility: Visibility = Visibility.PLAYER
    target_id: str
    attacker_id: str | None = None
    amount: int
    rolls: tuple[int, ...] = ()
    keys: tuple[str, ...] = ()
    non_regenerable: bool = False


class DamageAbsorbedEvent(Event):
    """A hit absorbed by an immunity gate: no damage was rolled."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"combat.damage.absorbed"})

    event_type: Literal["damage_absorbed"] = "damage_absorbed"
    code: str = "combat.damage.absorbed"
    visibility: Visibility = Visibility.PLAYER
    target_id: str
    attacker_id: str | None = None
    keys: tuple[str, ...] = ()


class SavingThrowRolledEvent(Event):
    """A saving throw resolved; `roll` and `required` are `None` for auto-save defenses."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {"combat.save.passed", "combat.save.failed", "combat.save.auto"}
    )

    event_type: Literal["saving_throw_rolled"] = "saving_throw_rolled"
    visibility: Visibility = Visibility.PLAYER
    target_id: str
    category: str
    roll: int | None = None
    modifier: int = 0
    required: int | None = None


class MoraleCheckedEvent(Event):
    """A morale check: referee visibility — players learn the outcome from behavior."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {"combat.morale.held", "combat.morale.broke", "combat.morale.exempt"}
    )

    event_type: Literal["morale_checked"] = "morale_checked"
    visibility: Visibility = Visibility.REFEREE
    subject: str
    score: int
    roll: int | None = None
    modifier: int = 0


class ReactionRolledEvent(Event):
    """A monster reaction roll: referee visibility — players learn reactions from behavior."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"encounter.reaction.rolled"})

    event_type: Literal["reaction_rolled"] = "reaction_rolled"
    code: str = "encounter.reaction.rolled"
    visibility: Visibility = Visibility.REFEREE
    roll: int
    modifier: int = 0
    total: int
    result: str


class ConditionGainedEvent(Event):
    """A creature gained a condition."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"effects.condition.gained"})

    event_type: Literal["condition_gained"] = "condition_gained"
    code: str = "effects.condition.gained"
    visibility: Visibility = Visibility.PLAYER
    target_id: str
    condition: str
    effect_id: str | None = None


class ConditionRemovedEvent(Event):
    """A creature lost a condition."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"effects.condition.removed"})

    event_type: Literal["condition_removed"] = "condition_removed"
    code: str = "effects.condition.removed"
    visibility: Visibility = Visibility.PLAYER
    target_id: str
    condition: str
    effect_id: str | None = None


class EffectAttachedEvent(Event):
    """An effect attached to a creature, item, or location (referee bookkeeping)."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"effects.effect.attached"})

    event_type: Literal["effect_attached"] = "effect_attached"
    code: str = "effects.effect.attached"
    visibility: Visibility = Visibility.REFEREE
    effect_id: str
    kind: str
    target_ref: str
    expires_round: int | None = None


class EffectTickedEvent(Event):
    """An effect's periodic tick resolved (referee bookkeeping)."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"effects.effect.ticked"})

    event_type: Literal["effect_ticked"] = "effect_ticked"
    code: str = "effects.effect.ticked"
    visibility: Visibility = Visibility.REFEREE
    effect_id: str
    kind: str
    target_ref: str
    round: int


class EffectExpiredEvent(Event):
    """An effect's duration ran out (referee bookkeeping)."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"effects.effect.expired"})

    event_type: Literal["effect_expired"] = "effect_expired"
    code: str = "effects.effect.expired"
    visibility: Visibility = Visibility.REFEREE
    effect_id: str
    kind: str
    target_ref: str
    round: int


class EffectReleasedEvent(Event):
    """An effect explicitly released before expiry (referee bookkeeping)."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"effects.effect.released"})

    event_type: Literal["effect_released"] = "effect_released"
    code: str = "effects.effect.released"
    visibility: Visibility = Visibility.REFEREE
    effect_id: str
    kind: str
    target_ref: str


class HealingAppliedEvent(Event):
    """Healing applied — or blocked (mummy rot renders magical healing ineffective)."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"combat.healing.applied", "combat.healing.blocked"})

    event_type: Literal["healing_applied"] = "healing_applied"
    visibility: Visibility = Visibility.PLAYER
    target_id: str
    amount: int
    source: str


class DeathEvent(Event):
    """A creature reduced to 0 hit points or less is killed.

    `combat.death.permanent` marks a regenerating creature's permanent death (the
    troll's non-regenerable ledger reaching max HP).
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"combat.death.died", "combat.death.permanent"})

    event_type: Literal["death"] = "death"
    visibility: Visibility = Visibility.PLAYER
    target_id: str


class EquipmentDestroyedEvent(Event):
    """A victim's equipment destroyed by a destructive death (dragon breath)."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"combat.equipment.destroyed"})

    event_type: Literal["equipment_destroyed"] = "equipment_destroyed"
    code: str = "combat.equipment.destroyed"
    visibility: Visibility = Visibility.PLAYER
    target_id: str
    item_names: tuple[str, ...]


class LevelDrainedEvent(Event):
    """Energy drain resolved: levels lost, with the terminal case as its own code.

    `spawn_consequence` is the structured-but-manual field carrying the SRD's spawn
    prose ("becomes a wight in 1d4 days, under the control of the wight that killed
    them") — the kernel kills, the game narrates.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"combat.drain.drained", "combat.drain.slain"})

    event_type: Literal["level_drained"] = "level_drained"
    visibility: Visibility = Visibility.PLAYER
    target_id: str
    levels_lost: int
    new_level: int
    hp_lost: int
    xp_after: int | None = None
    spawn_consequence: str | None = None


class MonsterRevivedEvent(Event):
    """A regenerating monster returned from death (the troll's 2d6-round revival)."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"effects.regeneration.revived"})

    event_type: Literal["monster_revived"] = "monster_revived"
    code: str = "effects.regeneration.revived"
    visibility: Visibility = Visibility.PLAYER
    target_id: str


class HitPointsReportedEvent(Event):
    """A creature's hit point state — referee visibility: monster HP is hidden by design."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"combat.state.hit_points"})

    event_type: Literal["hit_points_reported"] = "hit_points_reported"
    code: str = "combat.state.hit_points"
    visibility: Visibility = Visibility.REFEREE
    target_id: str
    current_hp: int
    max_hp: int


class TargetsSelectedEvent(Event):
    """The targeting model's resolution: which candidates an effect selected."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"combat.targeting.selected"})

    event_type: Literal["targets_selected"] = "targets_selected"
    code: str = "combat.targeting.selected"
    visibility: Visibility = Visibility.REFEREE
    mode: str
    target_ids: tuple[str, ...]


class PreparedSpell(BaseModel):
    """One prepared copy in a memorization event: the spell and its fixed form."""

    model_config = ConfigDict(frozen=True)

    spell_id: str
    reversed: bool = False


class SpellsMemorizedEvent(Event):
    """A caster's daily preparation resolved: the full prepared list.

    One event per preparation — memorization is a full replacement, so the list is
    the caster's complete new memory.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"magic.memorize.prepared"})

    event_type: Literal["spells_memorized"] = "spells_memorized"
    code: str = "magic.memorize.prepared"
    visibility: Visibility = Visibility.PLAYER
    caster_id: str
    prepared: tuple[PreparedSpell, ...]


class SpellCastEvent(Event):
    """A spell cast: the memorized copy was consumed.

    `manual=True` marks modes the kernel doesn't execute — the game narrates the
    effect from the spell's prose. Resolution consequences ride the existing event
    types (saves, damage, conditions, effects, healing, deaths), exactly as breath
    weapons do. `magic.cast.no_effect` reports a cast whose every target was
    ineligible or unaffected — the copy is still spent (rejections are free and
    would leak hidden state, pinned).
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"magic.cast.cast", "magic.cast.no_effect"})

    event_type: Literal["spell_cast"] = "spell_cast"
    visibility: Visibility = Visibility.PLAYER
    caster_id: str
    spell_id: str
    mode: str
    reversed: bool = False
    target_ids: tuple[str, ...] = ()
    manual: bool = False


class SpellDisruptedEvent(Event):
    """A declared casting disrupted: the copy is lost as if it had been cast."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"magic.cast.disrupted"})

    event_type: Literal["spell_disrupted"] = "spell_disrupted"
    code: str = "magic.cast.disrupted"
    visibility: Visibility = Visibility.PLAYER
    caster_id: str
    spell_id: str
    reversed: bool = False


class SpellForgottenEvent(Event):
    """A memorized copy forgotten because level drain shrank the caster's slots."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"magic.memory.forgotten"})

    event_type: Literal["spell_forgotten"] = "spell_forgotten"
    code: str = "magic.memory.forgotten"
    visibility: Visibility = Visibility.PLAYER
    caster_id: str
    spell_id: str
    reversed: bool = False


class SpellBookUpdatedEvent(Event):
    """A spell added to an arcane caster's spell book."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"magic.book.added"})

    event_type: Literal["spell_book_updated"] = "spell_book_updated"
    code: str = "magic.book.added"
    visibility: Visibility = Visibility.PLAYER
    caster_id: str
    spell_id: str


class TurningTypeOutcome(BaseModel):
    """One undead type's turning verdict: its column, the cell, and the threshold."""

    model_config = ConfigDict(frozen=True)

    template_id: str
    column: str | None = None
    outcome: str
    threshold: int | None = None


class UndeadTurnedEvent(Event):
    """A turning attempt resolved — player visibility: the player rolls turning dice.

    Carries the 2d6 turn roll, the 2d6 HD pool when one was rolled (some type
    succeeded), the per-type verdicts, and the affected monsters. Per-monster
    consequences ride `ConditionGainedEvent`/`DeathEvent`. The code is `failed` when
    no type succeeded, `destroyed` when any affected monster was destroyed, and
    `turned` otherwise.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {"magic.turning.turned", "magic.turning.destroyed", "magic.turning.failed"}
    )

    event_type: Literal["undead_turned"] = "undead_turned"
    visibility: Visibility = Visibility.PLAYER
    caster_id: str
    roll: int
    hd_pool: int | None = None
    types: tuple[TurningTypeOutcome, ...] = ()
    affected_ids: tuple[str, ...] = ()


class MagicDispelledEvent(Event):
    """A *dispel magic* resolved: which effects were released and which survived."""

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"magic.dispel.resolved"})

    event_type: Literal["magic_dispelled"] = "magic_dispelled"
    code: str = "magic.dispel.resolved"
    visibility: Visibility = Visibility.PLAYER
    caster_id: str
    released_effect_ids: tuple[str, ...] = ()
    surviving_effect_ids: tuple[str, ...] = ()


KERNEL_EVENT_CLASSES: tuple[type[Event], ...] = (
    InitiativeRolledEvent,
    AttackRolledEvent,
    DamageDealtEvent,
    DamageAbsorbedEvent,
    SavingThrowRolledEvent,
    MoraleCheckedEvent,
    ReactionRolledEvent,
    ConditionGainedEvent,
    ConditionRemovedEvent,
    EffectAttachedEvent,
    EffectTickedEvent,
    EffectExpiredEvent,
    EffectReleasedEvent,
    HealingAppliedEvent,
    DeathEvent,
    EquipmentDestroyedEvent,
    LevelDrainedEvent,
    MonsterRevivedEvent,
    HitPointsReportedEvent,
    TargetsSelectedEvent,
    SpellsMemorizedEvent,
    SpellCastEvent,
    SpellDisruptedEvent,
    SpellForgottenEvent,
    SpellBookUpdatedEvent,
    UndeadTurnedEvent,
    MagicDispelledEvent,
)
"""Every kernel event class, in declaration order — the discriminated union's members."""

KernelEvent = Annotated[
    InitiativeRolledEvent
    | AttackRolledEvent
    | DamageDealtEvent
    | DamageAbsorbedEvent
    | SavingThrowRolledEvent
    | MoraleCheckedEvent
    | ReactionRolledEvent
    | ConditionGainedEvent
    | ConditionRemovedEvent
    | EffectAttachedEvent
    | EffectTickedEvent
    | EffectExpiredEvent
    | EffectReleasedEvent
    | HealingAppliedEvent
    | DeathEvent
    | EquipmentDestroyedEvent
    | LevelDrainedEvent
    | MonsterRevivedEvent
    | HitPointsReportedEvent
    | TargetsSelectedEvent
    | SpellsMemorizedEvent
    | SpellCastEvent
    | SpellDisruptedEvent
    | SpellForgottenEvent
    | SpellBookUpdatedEvent
    | UndeadTurnedEvent
    | MagicDispelledEvent,
    Field(discriminator="event_type"),
]
"""Any kernel event, discriminated by `event_type`."""


@cache
def _kernel_event_adapter() -> TypeAdapter:
    return TypeAdapter(KernelEvent)


@cache
def _known_event_types() -> frozenset[str]:
    return frozenset(variant.model_fields["event_type"].default for variant in KERNEL_EVENT_CLASSES)


def parse_event(data: Mapping[str, object]) -> Event | None:
    """Parse one serialized kernel event, skipping unknown event types.

    The mechanical half of "consumers must ignore unknown event types": an
    `event_type` this library doesn't know returns `None` instead of raising, so a
    newer producer's log replays under an older consumer.

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
        return _kernel_event_adapter().validate_python(data)
    except ValidationError as error:
        raise ContentValidationError(f"malformed kernel event: {error}") from error
