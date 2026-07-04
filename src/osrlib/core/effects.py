"""Named conditions and the effect lifecycle engine.

This module ships in two layers. The condition layer —
[`Condition`][osrlib.core.effects.Condition] and
[`ActiveCondition`][osrlib.core.effects.ActiveCondition] — is pure vocabulary:
creatures carry a tuple of active conditions so a serialized creature is honest on its
own. The engine layer — [`EffectsLedger`][osrlib.core.effects.EffectsLedger] — owns
durations, periodic ticks, expiry, and stacking, and is the *single writer* of
creature conditions (pinned): combat reads conditions locally, only the engine's
helpers (and the kernel's death routine, for `dead`) mutate them, and an invariant
test asserts ledger and creature state never desync.

The canonical tick order is locked by test: at each round boundary, expirations
resolve before ticks; simultaneous effects resolve in attachment order, tie-broken by
effect id. While a target is petrified its other attached effects suspend — no ticks,
durations frozen (pinned): a poisoned, petrified adventurer is a problem for after
*stone to flesh*.

Effect-internal randomness (revival delays, onset dice, duration dice) draws from the
[`EFFECTS_STREAM`][osrlib.core.effects.EFFECTS_STREAM] stream, so battle-resolution
draws never shift effect draws and vice versa.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from osrlib.core.clock import ROUNDS_PER_DAY, ROUNDS_PER_TURN, GameClock, TimeUnit
from osrlib.core.dice import parse, roll
from osrlib.core.events import (
    ConditionGainedEvent,
    ConditionRemovedEvent,
    DeathEvent,
    EffectAttachedEvent,
    EffectExpiredEvent,
    EffectReleasedEvent,
    EffectTickedEvent,
    Event,
    HealingAppliedEvent,
    HitPointsReportedEvent,
    MonsterRevivedEvent,
)
from osrlib.core.rng import RngStream

__all__ = [
    "EFFECTS_STREAM",
    "MODIFIER_KINDS",
    "ActiveCondition",
    "ActiveEffect",
    "ActiveModifier",
    "Condition",
    "EffectDefinition",
    "EffectsLedger",
    "ModifierSpec",
    "grant_condition",
    "has_condition",
    "has_modifier",
    "kill",
    "modifier_dice",
    "modifier_total",
    "modifier_values",
    "regeneration_definition",
    "remove_condition",
]

EFFECTS_STREAM = "effects"
"""Stream key convention for effect-internal draws: durations, onsets, revivals."""

_ROUNDS_PER_UNIT: dict[TimeUnit, int] = {
    TimeUnit.ROUND: 1,
    TimeUnit.TURN: ROUNDS_PER_TURN,
    TimeUnit.DAY: ROUNDS_PER_DAY,
}


class Condition(StrEnum):
    """The named conditions.

    The wire values are lowercase — they serialize into creatures and saves; changing
    them is a `schema_version` bump. Combat hooks exist for the subset the kernel
    consumes (paralysed, asleep, blind, averted_eyes, petrified, poisoned, diseased,
    dead; Phase 3 adds silenced/feebleminded/weakened casting gates, the weakened
    attack gate, and the entangled movement predicate); the rest are additive-safe
    vocabulary — `afraid`, `turned`, `confused`, and `invisible` are marker states
    consumed by Phase 4's battle machine and by games.
    """

    PARALYSED = "paralysed"
    ASLEEP = "asleep"
    BLIND = "blind"
    CHARMED = "charmed"
    PETRIFIED = "petrified"
    DISEASED = "diseased"
    EXHAUSTED = "exhausted"
    LYCANTHROPY_INCUBATION = "lycanthropy_incubation"
    AVERTED_EYES = "averted_eyes"
    POISONED = "poisoned"
    DEAD = "dead"
    SILENCED = "silenced"
    ENTANGLED = "entangled"
    AFRAID = "afraid"
    FEEBLEMINDED = "feebleminded"
    INVISIBLE = "invisible"
    TURNED = "turned"
    CONFUSED = "confused"
    WEAKENED = "weakened"


class ActiveCondition(BaseModel):
    """A condition a creature currently has, with the effect that owns it.

    `effect_id` is `None` only for conditions no ledger effect owns: `dead`, written by
    the kernel's death routine (pinned — death is a kernel outcome, not a timed
    effect).
    """

    model_config = ConfigDict(frozen=True)

    condition: Condition
    effect_id: str | None = None


def has_condition(target: object, condition: Condition) -> bool:
    """Return whether a creature currently has `condition`.

    Args:
        target: A creature carrying a `conditions` tuple.
        condition: The condition to look for.

    Returns:
        True when any active condition matches.
    """
    return any(active.condition is condition for active in getattr(target, "conditions", ()))


def _entity_id(target: object) -> str:
    identifier = getattr(target, "id", None)
    return identifier if identifier is not None else getattr(target, "name", "unknown")


def grant_condition(target: object, condition: Condition, effect_id: str | None) -> list[Event]:
    """Grant a condition to a creature — the single-writer mutation point.

    A condition the creature is immune to (its defenses' `condition_immunities`) is
    not granted and nothing is emitted; duplicate grants from the same effect are
    no-ops.

    Args:
        target: The creature; its `conditions` tuple is replaced.
        condition: The condition to grant.
        effect_id: The owning effect, or `None` for `dead`.

    Returns:
        The condition-gained event, or nothing when immune or duplicate.
    """
    defenses = getattr(getattr(target, "template", None), "defenses", None)
    if defenses is not None and condition in defenses.condition_immunities:
        return []
    active = ActiveCondition(condition=condition, effect_id=effect_id)
    if active in target.conditions:
        return []
    target.conditions = (*target.conditions, active)
    return [ConditionGainedEvent(target_id=_entity_id(target), condition=condition.value, effect_id=effect_id)]


def remove_condition(target: object, condition: Condition, effect_id: str | None) -> list[Event]:
    """Remove the condition owned by `effect_id` from a creature.

    Args:
        target: The creature; its `conditions` tuple is replaced.
        condition: The condition to remove.
        effect_id: The owning effect (`None` for `dead`).

    Returns:
        The condition-removed event, or nothing when the creature didn't have it.
    """
    active = ActiveCondition(condition=condition, effect_id=effect_id)
    if active not in target.conditions:
        return []
    target.conditions = tuple(existing for existing in target.conditions if existing != active)
    return [ConditionRemovedEvent(target_id=_entity_id(target), condition=condition.value, effect_id=effect_id)]


def _grant_modifiers(target: object, specs: tuple[ModifierSpec, ...], effect_id: str) -> None:
    """Grant an effect's stat modifiers — the single-writer mutation point."""
    if not hasattr(target, "stat_modifiers"):
        return
    granted = tuple(ActiveModifier(**spec.model_dump(), effect_id=effect_id) for spec in specs)
    target.stat_modifiers = (*target.stat_modifiers, *granted)


def _remove_modifiers(target: object, effect_id: str) -> None:
    """Remove the stat modifiers owned by `effect_id`."""
    if not hasattr(target, "stat_modifiers"):
        return
    remaining = tuple(modifier for modifier in target.stat_modifiers if modifier.effect_id != effect_id)
    if len(remaining) != len(target.stat_modifiers):
        target.stat_modifiers = remaining


def kill(target: object, *, permanent: bool = False) -> list[Event]:
    """Kill a creature: hit points to 0, the `dead` condition, and the death event.

    "A character or monster reduced to 0 hit points or less is killed." Idempotent —
    a creature already dead emits nothing.

    Args:
        target: The creature to kill.
        permanent: True for a regenerating creature's permanent death (the troll's
            non-regenerable ledger reaching max HP).

    Returns:
        The death, condition, and referee hit-point events.
    """
    if has_condition(target, Condition.DEAD):
        return []
    target.current_hp = 0
    events: list[Event] = []
    events.extend(grant_condition(target, Condition.DEAD, None))
    code = "combat.death.permanent" if permanent else "combat.death.died"
    events.append(DeathEvent(code=code, target_id=_entity_id(target)))
    events.append(
        HitPointsReportedEvent(target_id=_entity_id(target), current_hp=0, max_hp=getattr(target, "max_hp", 0))
    )
    return events


MODIFIER_KINDS = frozenset(
    {
        "attack_bonus",
        "damage_bonus",
        "morale_bonus",
        "save_bonus",
        "ac_bonus",
        "ac_set",
        "ac_set_vs_missile",
        "attack_penalty_of_attackers",
        "damage_reduction_per_die",
        "damage_multiplier",
        "melee_damage_multiplier",
        "missile_immunity_nonmagical",
        "strength_set",
        "weapon_damage_dice_bonus",
        "counts_as_magical",
    }
)
"""The closed vocabulary of stat-modifier kinds combat consults.

`ac_bonus`, `strength_set`, and the damage multipliers arrive with Phase 5's magic
items: `ac_bonus` improves AC by its value (descending down, ascending up), `ac_set`
sets it outright, `strength_set` replaces the STR score combat modifiers derive from
(Gauntlets of Ogre Power's 18, the Ring of Weakness's 3), and the multipliers double
weapon damage (giant strength) or melee damage only (growth) after the roll.
"""


class ModifierSpec(BaseModel):
    """One stat modifier an effect grants while active.

    `value` is the signed adjustment (*bless*'s +1, *protection from evil*'s −1 to
    attackers, *shield*'s AC-set values); `dice` carries dice-valued bonuses
    (*striking*'s +1d6 weapon damage). Scopes: `element` restricts save bonuses and
    per-die reductions to one element (*resist fire*), `versus_other_alignment`
    restricts save bonuses to attacks from creatures of another alignment
    (*protection from evil*), `save_categories` restricts save bonuses to named
    categories (the Displacer Cloak's petrification/rods/spells/staves/wands list),
    and `melee_only` restricts an attacker penalty to melee attacks (the cloak's −2
    leaves missiles unaffected, RAW). `from_item` marks item-sourced modifiers
    (potion effects, ward scrolls): they are exempt from the cumulative
    largest-bonus cap — RAW's carve-out covers magic items generally, not just
    worn ones (pinned).
    """

    model_config = ConfigDict(frozen=True)

    kind: str
    value: int = 0
    dice: str | None = None
    element: str | None = None
    versus_other_alignment: bool = False
    save_categories: tuple[str, ...] = ()
    melee_only: bool = False
    from_item: bool = False

    @field_validator("kind")
    @classmethod
    def _kind_must_be_known(cls, value: str) -> str:
        if value not in MODIFIER_KINDS:
            raise ValueError(f"modifier kind must be one of {sorted(MODIFIER_KINDS)}, got {value!r}")
        return value

    @field_validator("dice")
    @classmethod
    def _dice_must_parse(cls, value: str | None) -> str | None:
        if value is not None:
            parse(value)
        return value


class ActiveModifier(ModifierSpec):
    """A live stat modifier on a creature, with the effect that owns it.

    Creatures carry a `stat_modifiers` tuple so a serialized creature is honest on
    its own, mirroring conditions. **Only the effects engine writes it** (attach
    grants, expiry and release remove) — the single-writer rule extends; combat
    reads it locally through the `modifier_*` helpers below.
    """

    effect_id: str


def modifier_values(
    target: object,
    kind: str,
    *,
    element: str | None = None,
    versus_differs: bool = False,
    save_category: str | None = None,
    melee: bool = False,
) -> list[int]:
    """Return the matching modifier values on a creature, scope-filtered.

    Element-scoped modifiers match only their element; alignment-scoped modifiers
    match only when the caller attests the source's alignment differs
    (`versus_differs`); category-scoped save bonuses match only their categories;
    melee-only modifiers match only when the caller attests a melee attack.

    Args:
        target: A creature carrying a `stat_modifiers` tuple.
        kind: The modifier kind to look for.
        element: The damage or save element in play, if any.
        versus_differs: True when the source creature's alignment differs from the
            target's.
        save_category: The saving throw category in play, if any.
        melee: True when the attack in play is melee.

    Returns:
        The matching values, in attachment order.
    """
    matching = _matching_modifiers(target, kind, element, versus_differs, save_category, melee)
    return [modifier.value for modifier in matching]


def _matching_modifiers(
    target: object,
    kind: str,
    element: str | None,
    versus_differs: bool,
    save_category: str | None,
    melee: bool,
) -> list[ModifierSpec]:
    return [
        modifier
        for modifier in getattr(target, "stat_modifiers", ())
        if modifier.kind == kind
        and (modifier.element is None or modifier.element == element)
        and (not modifier.versus_other_alignment or versus_differs)
        and (not modifier.save_categories or save_category in modifier.save_categories)
        and (not modifier.melee_only or melee)
    ]


def modifier_total(
    target: object,
    kind: str,
    *,
    element: str | None = None,
    versus_differs: bool = False,
    save_category: str | None = None,
    melee: bool = False,
) -> int:
    """Return a creature's cumulative modifier for one statistic.

    The cumulative-effects rule, pinned from `Spells.md` ("Multiple spells affecting
    the same game statistic do not combine"): only the single largest bonus and the
    single largest penalty apply — a *bless* and a *blight* offset; two *blesses*
    don't stack. Spell modifiers combine freely with non-spell modifiers (the RAW
    carve-out for magic items — item-sourced modifiers ride equipped-item queries
    and item-kind effects, both outside this cap; see
    [`osrlib.core.combat`][osrlib.core.combat]).

    Args:
        target: A creature carrying a `stat_modifiers` tuple.
        kind: The modifier kind to total.
        element: The damage or save element in play, if any.
        versus_differs: True when the source creature's alignment differs.
        save_category: The saving throw category in play, if any.
        melee: True when the attack in play is melee.

    Returns:
        The signed cumulative modifier.
    """
    matching = _matching_modifiers(target, kind, element, versus_differs, save_category, melee)
    spell_values = [modifier.value for modifier in matching if not modifier.from_item]
    item_values = [modifier.value for modifier in matching if modifier.from_item]
    bonus = max((value for value in spell_values if value > 0), default=0)
    penalty = min((value for value in spell_values if value < 0), default=0)
    return bonus + penalty + sum(item_values)


def modifier_dice(target: object, kind: str) -> str | None:
    """Return the dice of the first matching dice-valued modifier (*striking*'s +1d6).

    First-only is the cumulative rule for dice bonuses: two *strikings* don't
    combine.

    Args:
        target: A creature carrying a `stat_modifiers` tuple.
        kind: The modifier kind to look for.

    Returns:
        The dice expression, or `None` when no matching modifier is active.
    """
    for modifier in getattr(target, "stat_modifiers", ()):
        if modifier.kind == kind and modifier.dice is not None:
            return modifier.dice
    return None


def has_modifier(target: object, kind: str) -> bool:
    """Return whether a creature carries any modifier of `kind` (the flag kinds).

    Args:
        target: A creature carrying a `stat_modifiers` tuple.
        kind: The modifier kind to look for.

    Returns:
        True when any active modifier matches.
    """
    return any(modifier.kind == kind for modifier in getattr(target, "stat_modifiers", ()))


class EffectDefinition(BaseModel):
    """A frozen effect blueprint: duration, ticks, stacking, expiry, and condition.

    Durations are `duration_amount` (fixed) or `duration_dice` (rolled at attach from
    the effects stream) counts of `duration_unit`; both `None` means indefinite (until
    released) and `permanent=True` marks effects only magic removes (petrification —
    stone is not dead). `tick` names a periodic behavior the ledger executes every
    `tick_interval_rounds`; `expiry` names an outcome resolved when the duration runs
    out (`death` for delayed poison, `splash_damage` for the douse's second
    application). `condition` is granted at attach and removed at expiry or release;
    `modifiers` are granted and removed the same way. `dispellable=True` marks
    spell-attached effects *dispel magic* can end — pinned: every effect
    [`cast_spell`][osrlib.core.spells.cast_spell] attaches is dispellable, including
    permanent ones (`permanent=True` means "no duration expiry", not
    "undispellable"), while monster-inflicted effects stay non-dispellable.
    """

    model_config = ConfigDict(frozen=True)

    kind: str = Field(min_length=1)
    duration_unit: TimeUnit | None = None
    duration_amount: int | None = None
    duration_dice: str | None = None
    permanent: bool = False
    tick: str | None = None
    tick_interval_rounds: int = Field(default=1, ge=1)
    stacking: Literal["stack", "refresh", "ignore"] = "stack"
    expiry: str | None = None
    condition: Condition | None = None
    modifiers: tuple[ModifierSpec, ...] = ()
    dispellable: bool = False
    params: dict[str, int | str | bool | tuple[int | str, ...]] = {}

    @field_validator("duration_dice")
    @classmethod
    def _dice_must_parse(cls, value: str | None) -> str | None:
        if value is not None:
            parse(value)
        return value


class ActiveEffect(BaseModel):
    """A live effect on a creature, item, or location.

    `target_ref` is an entity id or a location string (a burning oil pool attaches to
    a location now; cells arrive in Phase 4). `expires_round` is the absolute round
    the effect expires on (`None` for indefinite and permanent effects); petrification
    suspension pushes it forward. `state` is the effect's own bookkeeping (revival
    round, counted rest days). `caster_level` records the casting caster's level on
    spell-attached effects — *dispel magic*'s survival roll compares against it.
    """

    model_config = ConfigDict(validate_assignment=True)

    effect_id: str
    definition: EffectDefinition
    target_ref: str
    attached_round: int = Field(ge=0)
    expires_round: int | None = None
    caster_level: int | None = None
    state: dict[str, int] = {}


class EffectsLedger(BaseModel):
    """The serializable effect engine: attach, release, and clock-driven advance."""

    model_config = ConfigDict(validate_assignment=True)

    effects: list[ActiveEffect] = []

    def active_on(self, target_ref: str, kind: str | None = None) -> list[ActiveEffect]:
        """Return the live effects on a target, optionally filtered by kind.

        Args:
            target_ref: The entity id or location string.
            kind: An effect kind to filter by.

        Returns:
            The matching effects, in attachment order.
        """
        return [
            effect
            for effect in self.effects
            if effect.target_ref == target_ref and (kind is None or effect.definition.kind == kind)
        ]

    def attach(
        self,
        definition: EffectDefinition,
        target_ref: str,
        *,
        clock: GameClock,
        allocator: object,
        registry: Mapping[str, object] | None = None,
        stream: RngStream | None = None,
        caster_level: int | None = None,
    ) -> tuple[ActiveEffect | None, list[Event]]:
        """Attach an effect, resolving stacking, duration dice, conditions, and modifiers.

        Args:
            definition: The effect blueprint.
            target_ref: The entity id or location string to attach to.
            clock: The game clock (the attach round anchors the duration).
            allocator: The [`IdAllocator`][osrlib.core.monsters.IdAllocator] granting
                effect ids.
            registry: Live objects by entity id, for condition and modifier grants;
                a location ref simply isn't in it.
            stream: The effects stream; required when the definition rolls duration
                dice.
            caster_level: The casting caster's level, recorded on spell-attached
                effects for *dispel magic*'s survival roll.

        Returns:
            The attached effect and its events — or `(None, [])` when stacking says
            `ignore` and the kind is already present, or the target is immune to the
            effect's condition.
        """
        existing = self.active_on(target_ref, definition.kind)
        if existing and definition.stacking == "ignore":
            return None, []
        if existing and definition.stacking == "refresh":
            effect = existing[0]
            effect.expires_round = self._expiry_round(definition, clock, stream)
            return effect, []
        target = registry.get(target_ref) if registry is not None else None
        if definition.condition is not None and target is not None:
            defenses = getattr(getattr(target, "template", None), "defenses", None)
            if defenses is not None and definition.condition in defenses.condition_immunities:
                return None, []
        effect = ActiveEffect(
            effect_id=allocator.allocate("effect"),
            definition=definition,
            target_ref=target_ref,
            attached_round=clock.rounds,
            expires_round=self._expiry_round(definition, clock, stream),
            caster_level=caster_level,
        )
        self.effects.append(effect)
        events: list[Event] = [
            EffectAttachedEvent(
                effect_id=effect.effect_id,
                kind=definition.kind,
                target_ref=target_ref,
                expires_round=effect.expires_round,
            )
        ]
        if definition.condition is not None and target is not None:
            events.extend(grant_condition(target, definition.condition, effect.effect_id))
        if definition.modifiers and target is not None:
            _grant_modifiers(target, definition.modifiers, effect.effect_id)
        return effect, events

    def release(self, effect_id: str, registry: Mapping[str, object] | None = None) -> list[Event]:
        """Release an effect before expiry, removing its condition.

        Args:
            effect_id: The effect to release.
            registry: Live objects by entity id, for condition removal.

        Returns:
            The released and condition-removed events.

        Raises:
            ValueError: If no live effect has that id.
        """
        effect = next((candidate for candidate in self.effects if candidate.effect_id == effect_id), None)
        if effect is None:
            raise ValueError(f"unknown effect id {effect_id!r}")
        self.effects.remove(effect)
        events: list[Event] = [
            EffectReleasedEvent(effect_id=effect.effect_id, kind=effect.definition.kind, target_ref=effect.target_ref)
        ]
        target = registry.get(effect.target_ref) if registry is not None else None
        if effect.definition.condition is not None and target is not None:
            events.extend(remove_condition(target, effect.definition.condition, effect.effect_id))
        if target is not None:
            _remove_modifiers(target, effect.effect_id)
        return events

    def advance(
        self,
        clock: GameClock,
        n: int,
        unit: TimeUnit,
        registry: Mapping[str, object],
        *,
        stream: RngStream,
        allocator: object | None = None,
    ) -> list[Event]:
        """Advance the clock and resolve every round boundary in the span.

        The canonical tick order, locked by test: at each boundary, expirations
        resolve before ticks; simultaneous effects resolve in attachment order,
        tie-broken by effect id. Suspended effects (target petrified by another
        effect) neither tick nor age — their expiry pushes forward one round per
        suspended round.

        Args:
            clock: The game clock; advanced in place.
            n: How many units to advance.
            unit: The unit to advance in.
            registry: Live objects by entity id (the Phase 4 session will own one;
                tests pass a dict).
            stream: The effects stream for effect-internal draws.
            allocator: Reserved for behaviors that attach follow-on effects.

        Returns:
            Every event the advance produced, in resolution order.
        """
        start = clock.rounds
        clock.advance(n, unit)
        events: list[Event] = []
        for current_round in range(start + 1, clock.rounds + 1):
            events.extend(self._resolve_round(current_round, registry, stream))
        return events

    def _expiry_round(self, definition: EffectDefinition, clock: GameClock, stream: RngStream | None) -> int | None:
        if definition.permanent or definition.duration_unit is None:
            return None
        if definition.duration_dice is not None:
            if stream is None:
                raise ValueError(f"effect kind {definition.kind!r} rolls its duration; pass the effects stream")
            amount = roll(definition.duration_dice, stream).total
        else:
            if definition.duration_amount is None:
                raise ValueError(f"effect kind {definition.kind!r} has a duration unit but no amount or dice")
            amount = definition.duration_amount
        return clock.rounds + amount * _ROUNDS_PER_UNIT[definition.duration_unit]

    def _ordered(self) -> list[ActiveEffect]:
        return sorted(self.effects, key=lambda effect: (effect.attached_round, effect.effect_id))

    def _suspended(self, effect: ActiveEffect, registry: Mapping[str, object]) -> bool:
        target = registry.get(effect.target_ref)
        if target is None:
            return False
        return any(
            active.condition is Condition.PETRIFIED and active.effect_id != effect.effect_id
            for active in getattr(target, "conditions", ())
        )

    def _resolve_round(self, current_round: int, registry: Mapping[str, object], stream: RngStream) -> list[Event]:
        events: list[Event] = []
        # Suspension first: a suspended effect neither expires nor ticks this round,
        # and its remaining duration is preserved by pushing expiry forward.
        suspended_ids = set()
        for effect in self._ordered():
            if self._suspended(effect, registry):
                suspended_ids.add(effect.effect_id)
                if effect.expires_round is not None:
                    effect.expires_round += 1
        for effect in self._ordered():
            if effect.effect_id in suspended_ids:
                continue
            if effect.expires_round is not None and effect.expires_round <= current_round:
                events.extend(self._expire(effect, current_round, registry, stream))
        for effect in self._ordered():
            if effect.effect_id in suspended_ids or effect.definition.tick is None:
                continue
            if (current_round - effect.attached_round) % effect.definition.tick_interval_rounds == 0:
                events.extend(self._tick(effect, current_round, registry, stream))
        return events

    def _expire(
        self, effect: ActiveEffect, current_round: int, registry: Mapping[str, object], stream: RngStream
    ) -> list[Event]:
        self.effects.remove(effect)
        definition = effect.definition
        events: list[Event] = [
            EffectExpiredEvent(
                effect_id=effect.effect_id, kind=definition.kind, target_ref=effect.target_ref, round=current_round
            )
        ]
        target = registry.get(effect.target_ref)
        if definition.condition is not None and target is not None:
            events.extend(remove_condition(target, definition.condition, effect.effect_id))
        if target is not None:
            _remove_modifiers(target, effect.effect_id)
        if target is None or definition.expiry is None:
            return events
        if definition.expiry == "death":
            if not has_condition(target, Condition.DEAD):
                events.extend(kill(target))
        elif definition.expiry == "splash_damage":
            from osrlib.core.combat import DamageSource, deal_damage

            dice = definition.params.get("dice")
            keys = definition.params.get("keys", ())
            element = definition.params.get("element")
            result = roll(str(dice), stream)
            source = DamageSource(
                keys=tuple(str(key) for key in keys),
                element=str(element) if element is not None else None,
                kind="splash",
            )
            events.extend(deal_damage(target, result.total, rolls=result.rolls, source=source))
        else:
            raise ValueError(f"unknown expiry outcome {definition.expiry!r} on effect kind {definition.kind!r}")
        return events

    def _tick(
        self, effect: ActiveEffect, current_round: int, registry: Mapping[str, object], stream: RngStream
    ) -> list[Event]:
        definition = effect.definition
        target = registry.get(effect.target_ref)
        if target is None:
            return []
        if definition.tick == "regeneration":
            return self._tick_regeneration(effect, current_round, target, stream)
        if definition.tick == "charm_resave":
            return self._tick_charm_resave(effect, current_round, target, registry, stream)
        raise ValueError(f"unknown tick behavior {definition.tick!r} on effect kind {definition.kind!r}")

    def _tick_charm_resave(
        self,
        effect: ActiveEffect,
        current_round: int,
        target: object,
        registry: Mapping[str, object],
        stream: RngStream,
    ) -> list[Event]:
        """The charm's periodic saving throw: a passed save releases the charm.

        The re-save is a tick-time draw, so it comes from the effects stream per the
        Phase 2 convention; the interval was fixed at attach from the subject's INT
        band (`tick_interval_rounds`).
        """
        from osrlib.core.combat import SaveCategory, saving_throw

        save = saving_throw(target, SaveCategory.SPELLS, magical=True, stream=stream)
        events = list(save.events)
        events.append(
            EffectTickedEvent(
                effect_id=effect.effect_id,
                kind=effect.definition.kind,
                target_ref=effect.target_ref,
                round=current_round,
            )
        )
        if save.passed:
            events.extend(self.release(effect.effect_id, registry))
        return events

    def _tick_regeneration(
        self, effect: ActiveEffect, current_round: int, target: object, stream: RngStream
    ) -> list[Event]:
        definition = effect.definition
        params = definition.params
        per_round = int(params.get("per_round", 0))
        delay = int(params.get("delay_rounds", 0))
        while_alive = bool(params.get("while_alive", False))
        revive_dice = params.get("revive")
        target_id = _entity_id(target)
        regenerable_max = target.max_hp - getattr(target, "nonregen_damage", 0)
        events: list[Event] = []
        if has_condition(target, Condition.DEAD):
            if while_alive or revive_dice is None or regenerable_max < 1:
                return []
            if "revive_at" not in effect.state:
                # Pinned: the 2d6-round countdown anchors to the round the killing
                # damage landed (the instance's damage ledger), falling back to this
                # boundary when no clocked damage was recorded.
                base = getattr(target, "last_damaged_round", None)
                anchor = base if base is not None else current_round
                effect.state["revive_at"] = anchor + roll(str(revive_dice), stream).total
            if current_round >= effect.state["revive_at"]:
                del effect.state["revive_at"]
                target.current_hp = 1
                events.extend(remove_condition(target, Condition.DEAD, None))
                events.append(MonsterRevivedEvent(target_id=target_id))
                events.append(HitPointsReportedEvent(target_id=target_id, current_hp=1, max_hp=target.max_hp))
            return events
        if delay:
            last_damaged = getattr(target, "last_damaged_round", None)
            if last_damaged is not None and current_round < last_damaged + delay:
                return []
        if target.current_hp >= regenerable_max:
            return []
        healed = min(per_round, regenerable_max - target.current_hp)
        target.current_hp += healed
        events.append(
            EffectTickedEvent(
                effect_id=effect.effect_id, kind=definition.kind, target_ref=effect.target_ref, round=current_round
            )
        )
        events.append(
            HealingAppliedEvent(
                code="combat.healing.applied", target_id=target_id, amount=healed, source="regeneration"
            )
        )
        events.append(HitPointsReportedEvent(target_id=target_id, current_hp=target.current_hp, max_hp=target.max_hp))
        return events


def regeneration_definition(params: Mapping[str, object]) -> EffectDefinition:
    """Build a regeneration effect from a monster's `regeneration` ability params.

    Args:
        params: The compiled tag params — `per_round`, `delay_rounds`, `blocked_by`,
            `revive`, `while_alive`.

    Returns:
        An indefinite per-round regeneration effect definition.
    """
    return EffectDefinition(
        kind="regeneration",
        tick="regeneration",
        stacking="ignore",
        params={key: value if not isinstance(value, list) else tuple(value) for key, value in params.items()},
    )
