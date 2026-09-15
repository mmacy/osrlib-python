"""Attach timed effects to creatures, items, and locations, and run them against the game clock.

Three kinds of caller reach this module. A [`GameSession`][osrlib.crawl.session.GameSession] calls
[`EffectsLedger.advance`][osrlib.core.effects.EffectsLedger.advance] every time it moves the game clock, which is
what makes durations run out and periodic effects fire. [`cast_spell`][osrlib.core.spells.cast_spell] calls
[`EffectsLedger.attach`][osrlib.core.effects.EffectsLedger.attach] when a cast lands, so a spell's printed
duration becomes a live effect. You call both yourself when you run the rules without a session: you keep the
ledger, the clock, and the registry, and you advance them in your own loop.

The module has two layers. The condition layer is vocabulary. A condition is a named state a creature is in,
like `asleep` or `petrified`, and [`Condition`][osrlib.core.effects.Condition] is the closed set of them. Each
creature has its own tuple of [`ActiveCondition`][osrlib.core.effects.ActiveCondition] records and its own tuple
of [`ActiveModifier`][osrlib.core.effects.ActiveModifier] records, so a serialized creature says what is wrong
with it without the ledger beside it, and combat reads both tuples directly through
[`has_condition`][osrlib.core.effects.has_condition] and the `modifier_` helpers below.

The engine layer is [`EffectsLedger`][osrlib.core.effects.EffectsLedger], which runs durations, periodic ticks,
expiry, and stacking. It is the only writer of a creature's conditions and modifiers, apart from
[`grant_condition`][osrlib.core.effects.grant_condition],
[`remove_condition`][osrlib.core.effects.remove_condition], and [`kill`][osrlib.core.effects.kill], which handle
the states no timed effect owns. Go through those helpers rather than assigning to `creature.conditions`
yourself, or a creature ends up with a condition that nothing will ever take away.

Every round boundary resolves in a fixed order: effects suspend first, then expirations, then ticks, and within
each phase effects resolve in attachment order, tie-broken by effect id. A creature petrified by one effect
suspends its other effects, which neither tick nor age while the stone lasts, so an adventurer who was poisoned
before being turned to stone is still poisoned after *stone to flesh*.

Effect-internal randomness (rolled durations, onset delays, a troll's revival countdown) draws from the stream
named by [`EFFECTS_STREAM`][osrlib.core.effects.EFFECTS_STREAM], so adding a draw to combat never shifts an
effect's roll.

The `target`, `combatant`, and `registry` parameters below are duck-typed: any object with the attributes the
call reads works, and in play that means a [`Character`][osrlib.core.character.Character] or a
[`MonsterInstance`][osrlib.core.monsters.MonsterInstance].

Typical usage:

```python
from osrlib.core.clock import GameClock, TimeUnit
from osrlib.core.effects import EFFECTS_STREAM, Condition, EffectDefinition, EffectsLedger, has_condition
from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.data import load_monsters

streams = RngStreams(master_seed=3)
goblin = spawn_monster(load_monsters().get("goblin"), id="monster-0001", stream=streams.get(MONSTER_SPAWN_STREAM))
registry = {"monster-0001": goblin}

ledger = EffectsLedger()
clock = GameClock()
sleep = EffectDefinition(
    kind="sleep",
    duration_unit=TimeUnit.TURN,
    duration_amount=4,
    condition=Condition.ASLEEP,
    dispellable=True,
)
effect, events = ledger.attach(sleep, "monster-0001", clock=clock, allocator=IdAllocator(), registry=registry)
assert [event.code for event in events] == ["effects.effect.attached", "effects.condition.gained"]
assert has_condition(goblin, Condition.ASLEEP)

# Four turns later the duration runs out and the ledger takes the condition back.
expiry = ledger.advance(clock, 4, TimeUnit.TURN, registry, stream=streams.get(EFFECTS_STREAM))
assert [event.code for event in expiry] == ["effects.effect.expired", "effects.condition.removed"]
assert not has_condition(goblin, Condition.ASLEEP)
assert ledger.effects == []
```
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Literal

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
from osrlib.core.rng import RngStream, StreamName

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

EFFECTS_STREAM = StreamName.EFFECTS
"""The random-number stream name for effect-internal draws: rolled durations, onsets, and revival countdowns.

Build an [`RngStreams`][osrlib.core.rng.RngStreams] from your session's master seed and pass
`streams.get(EFFECTS_STREAM)` wherever [`EffectsLedger.attach`][osrlib.core.effects.EffectsLedger.attach] and
[`EffectsLedger.advance`][osrlib.core.effects.EffectsLedger.advance] ask for a stream. Each subsystem draws from
its own named stream, so an extra attack roll never shifts the round on which a charmed creature saves itself
free.
"""

_ROUNDS_PER_UNIT: dict[TimeUnit, int] = {
    TimeUnit.ROUND: 1,
    TimeUnit.TURN: ROUNDS_PER_TURN,
    TimeUnit.DAY: ROUNDS_PER_DAY,
}


class Condition(StrEnum):
    """The closed set of named states a creature can be in.

    Read a creature's conditions with [`has_condition`][osrlib.core.effects.has_condition]. Put one on a creature
    by attaching an effect that brings it, through
    [`EffectsLedger.attach`][osrlib.core.effects.EffectsLedger.attach], so the ledger takes it away again when the
    duration runs out. Reach for [`grant_condition`][osrlib.core.effects.grant_condition] only for a state no
    timed effect owns.

    Some members drive rules in [`osrlib.core.combat`][osrlib.core.combat] and
    [`osrlib.core.spells`][osrlib.core.spells], and the rest are states the rest of the game acts on. The member
    docstrings below say which is which, so you know whether granting one changes a roll or only tells your
    interface what to show.

    One rule covers every member. A creature whose template lists a condition in its defenses'
    `condition_immunities` never takes that condition, whether you call
    [`grant_condition`][osrlib.core.effects.grant_condition] or attach an effect that brings it, so the member
    that looks inert to the rest of the core rules still decides which monsters a spell can touch.

    The values are the lowercase strings, and they serialize into creatures and saved games. A renamed value is a
    `schema_version` bump, not an edit.
    """

    PARALYSED = "paralysed"
    """Frozen in place. The creature cannot attack, cast, or move, it counts toward a side's morale check for
    half the side being incapacitated, and a melee attack against it hits automatically. *Cure light wounds*
    cures it."""

    ASLEEP = "asleep"
    """Unconscious. Everything `paralysed` does, and one more rule of its own: a melee hit with a bladed weapon
    kills the sleeper outright, with no damage roll."""

    BLIND = "blind"
    """Unable to see. [`validate_attack`][osrlib.core.combat.validate_attack] rejects the creature's attacks with
    `combat.attack.attacker_blind`."""

    CHARMED = "charmed"
    """Under a charm. The monsters that cannot be charmed, the undead and the golems among them, list it in
    their `condition_immunities`, so a charm aimed at one of those takes hold of nothing. Past that, the charmed
    creature's obedience is yours to play out, and the recurring save that can end the charm rides the effect's
    `charm_resave` tick."""

    PETRIFIED = "petrified"
    """Turned to stone. Everything `paralysed` does, and it suspends the creature's other effects, which neither
    tick nor age until the stone is undone. Stone is not dead: *stone to flesh* cures it and the creature picks
    up where it left off."""

    DISEASED = "diseased"
    """Sick with a disease. Magical healing is refused outright, and natural rest heals on the slower cadence the
    effect names, or not at all when you pass no ledger to
    [`natural_healing`][osrlib.core.combat.natural_healing]. *Cure disease* cures it."""

    EXHAUSTED = "exhausted"
    """Spent from a forced march or a night without rest. The penalties ride the effect's modifiers rather than
    the condition, so past the immunity rule nothing in the core rules turns on it and your interface can show
    it."""

    LYCANTHROPY_INCUBATION = "lycanthropy_incubation"
    """Infected by a lycanthrope's bite and not yet transformed. Vocabulary only: nothing in the core rules
    grants it, nothing past the immunity rule turns on it, and the transformation is yours to run."""

    AVERTED_EYES = "averted_eyes"
    """Fighting with eyes turned away from a gaze attack. [`resolve_gaze`][osrlib.core.combat.resolve_gaze] skips
    the creature, and the attack penalty for fighting blind is yours to pass in the attack context."""

    POISONED = "poisoned"
    """Poisoned. The monsters that cannot be poisoned, the undead and the cave locust among them, list it in
    their `condition_immunities`, so a poison aimed at one of those takes hold of nothing, and *neutralize
    poison* cures it and can bring back a character who died of poison within the last ten rounds. The killing
    is the effect's rather than the condition's: a poison that kills carries an `expiry` of `death`, which fires
    when the onset runs out."""

    DEAD = "dead"
    """Killed. Granted by [`kill`][osrlib.core.effects.kill] rather than by any effect, so its `effect_id` is
    `None`. It blocks acting and healing, the battle machine passes the creature over when it picks targets, and
    only a spell that removes the condition brings the creature back."""

    SILENCED = "silenced"
    """Unable to speak. [`validate_cast`][osrlib.core.spells.validate_cast] rejects the creature's casting with
    `magic.cast.caster_incapacitated`."""

    ENTANGLED = "entangled"
    """Held fast, as by a *web*. [`cannot_move`][osrlib.core.combat.cannot_move] reports True, and the creature
    can still attack and cast."""

    AFRAID = "afraid"
    """Panicked by a fear effect. *Remove fear* cures it, and when the fear was magical the subject first saves
    versus spells at +1 for each level of the curing caster, keeping the fear on a failure. The battle machine in
    [`osrlib.crawl.battle`][osrlib.crawl.battle] treats the creature as routed."""

    FEEBLEMINDED = "feebleminded"
    """Robbed of the wit to cast. [`validate_cast`][osrlib.core.spells.validate_cast] rejects the creature's
    casting with `magic.cast.caster_incapacitated`."""

    INVISIBLE = "invisible"
    """Unseen. Past the immunity rule nothing in the core rules turns on it. The battle machine leaves the
    creature out of the ranks an enemy picks targets from."""

    TURNED = "turned"
    """Driven off by a cleric's turning, which is where it comes from:
    [`turn_undead`][osrlib.core.spells.turn_undead] attaches the effect that grants it. Past the immunity rule
    nothing in the core rules turns on it, and the battle and encounter machines treat the creature as
    fleeing."""

    CONFUSED = "confused"
    """Acting at random. Past the immunity rule nothing in the core rules turns on it. The battle machine
    chooses the creature's action instead of letting you choose."""

    WEAKENED = "weakened"
    """Drained of strength. It blocks attacking, blocks casting, and blocks all healing."""


class ActiveCondition(BaseModel):
    """One condition a creature currently has, paired with the effect that owns it.

    You read these off a creature's `conditions` tuple rather than building them:
    [`grant_condition`][osrlib.core.effects.grant_condition] and
    [`EffectsLedger.attach`][osrlib.core.effects.EffectsLedger.attach] put them there. To ask whether a creature
    has a condition without caring which effect granted it, call
    [`has_condition`][osrlib.core.effects.has_condition] instead of scanning the tuple.

    The pairing is what lets two effects grant the same condition and each take back only its own: a creature
    charmed twice has two records, and releasing one leaves the other standing. Records compare by value, so
    the same condition from the same effect is never stored twice.

    Examples:
        ```python
        from osrlib.core.effects import ActiveCondition, Condition

        active = ActiveCondition(condition=Condition.ASLEEP, effect_id="effect-0001")
        assert active.condition is Condition.ASLEEP
        assert active.model_dump(mode="json") == {"condition": "asleep", "effect_id": "effect-0001"}
        ```
    """

    model_config = ConfigDict(frozen=True)

    condition: Condition
    """The condition the creature has."""

    effect_id: str | None = None
    """The id of the [`ActiveEffect`][osrlib.core.effects.ActiveEffect] that granted the condition and will take
    it back. `None` marks a condition no timed effect owns, which in the core rules means `dead`."""


def _int_param(params: Mapping[str, Any], key: str, default: int = 0) -> int:
    """Read an integer param. The params are schema-validated, but the checker can't key their union by name."""
    return int(params.get(key, default))


def has_condition(target: Any, condition: Condition) -> bool:
    """Return whether a creature currently has a condition.

    This is the read side of the condition layer, and the call combat itself makes. Use it wherever your code
    asks "is this creature asleep", instead of scanning the creature's `conditions` tuple, so a creature with
    the same condition from two effects still reads as having it once.

    It doesn't care which effect granted the condition. When you need that, read the creature's `conditions`
    tuple of [`ActiveCondition`][osrlib.core.effects.ActiveCondition] records directly.

    Args:
        target: The creature to check. Any object with a `conditions` tuple works, and an object without one
            reads as having no conditions.
        condition: The condition to look for.

    Returns:
        True when the creature has that condition from any source.

    Examples:
        ```python
        from osrlib.core.effects import Condition, grant_condition, has_condition
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        streams = RngStreams(master_seed=5)
        spawn = streams.get(MONSTER_SPAWN_STREAM)
        goblin = spawn_monster(load_monsters().get("goblin"), id="monster-0001", stream=spawn)
        assert not has_condition(goblin, Condition.AFRAID)

        grant_condition(goblin, Condition.AFRAID, "effect-0001")
        assert has_condition(goblin, Condition.AFRAID)
        ```
    """
    return any(active.condition is condition for active in getattr(target, "conditions", ()))


def _entity_id(target: Any) -> str:
    identifier = getattr(target, "id", None)
    return identifier if identifier is not None else getattr(target, "name", "unknown")


def grant_condition(target: Any, condition: Condition, effect_id: str | None) -> list[Event]:
    """Put a condition on a creature and return the event that says so.

    Call this for a state no timed effect owns, the way [`kill`][osrlib.core.effects.kill] does for `dead`. When
    the state has a duration, put the condition on an
    [`EffectDefinition`][osrlib.core.effects.EffectDefinition] and attach that with
    [`EffectsLedger.attach`][osrlib.core.effects.EffectsLedger.attach] instead, and the ledger takes the
    condition back on its own when the duration runs out. A condition granted here stays until you call
    [`remove_condition`][osrlib.core.effects.remove_condition] with the same `effect_id`.

    The call replaces the creature's `conditions` tuple, so pass a live creature rather than a copy, and append
    the returned events to whatever log your caller is building.

    Two cases grant nothing and return no events. A creature whose template lists the condition in its
    defenses' `condition_immunities` is never affected, which is how a skeleton shrugs off *sleep*. A second
    grant of the same condition from the same effect changes nothing, because the creature already has that
    record.

    Args:
        target: The creature to affect. Its `conditions` tuple is replaced in place.
        condition: The condition to grant.
        effect_id: The id of the effect that owns the condition and will take it back, or `None` for a state no
            effect owns.

    Returns:
        A list of one [`ConditionGainedEvent`][osrlib.core.events.ConditionGainedEvent], or an empty list when
        the creature is immune or already has the same record.

    Examples:
        ```python
        from osrlib.core.effects import Condition, grant_condition, has_condition
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        streams = RngStreams(master_seed=5)
        spawn = streams.get(MONSTER_SPAWN_STREAM)
        goblin = spawn_monster(load_monsters().get("goblin"), id="monster-0001", stream=spawn)

        events = grant_condition(goblin, Condition.AFRAID, "effect-0001")
        assert [event.code for event in events] == ["effects.condition.gained"]
        assert has_condition(goblin, Condition.AFRAID)

        # The same grant a second time changes nothing and says nothing.
        assert grant_condition(goblin, Condition.AFRAID, "effect-0001") == []
        ```
    """
    defenses = getattr(getattr(target, "template", None), "defenses", None)
    if defenses is not None and condition in defenses.condition_immunities:
        return []
    active = ActiveCondition(condition=condition, effect_id=effect_id)
    if active in target.conditions:
        return []
    target.conditions = (*target.conditions, active)
    return [ConditionGainedEvent(target_id=_entity_id(target), condition=condition.value, effect_id=effect_id)]


def remove_condition(target: Any, condition: Condition, effect_id: str | None) -> list[Event]:
    """Take back the condition one effect granted, and return the event that says so.

    This is the other half of [`grant_condition`][osrlib.core.effects.grant_condition], and it matches on the
    pair: the condition and the `effect_id` you granted it under. Pass the same `effect_id` you granted with, or
    nothing is removed. A creature charmed by two effects keeps the second charm after you remove the first,
    which is the point of recording the owner.

    You call this for conditions you granted yourself. A condition that came from an attached effect is taken
    back for you when the effect expires or you release it through
    [`EffectsLedger.release`][osrlib.core.effects.EffectsLedger.release].

    Args:
        target: The creature to affect. Its `conditions` tuple is replaced in place.
        condition: The condition to take back.
        effect_id: The id the condition was granted under, or `None` for a state no effect owns.

    Returns:
        A list of one [`ConditionRemovedEvent`][osrlib.core.events.ConditionRemovedEvent], or an empty list when
        the creature has no matching record.

    Examples:
        ```python
        from osrlib.core.effects import Condition, grant_condition, has_condition, remove_condition
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        streams = RngStreams(master_seed=5)
        spawn = streams.get(MONSTER_SPAWN_STREAM)
        goblin = spawn_monster(load_monsters().get("goblin"), id="monster-0001", stream=spawn)
        grant_condition(goblin, Condition.AFRAID, "effect-0001")

        # A different owner removes nothing.
        assert remove_condition(goblin, Condition.AFRAID, "effect-0002") == []
        assert has_condition(goblin, Condition.AFRAID)

        events = remove_condition(goblin, Condition.AFRAID, "effect-0001")
        assert [event.code for event in events] == ["effects.condition.removed"]
        assert not has_condition(goblin, Condition.AFRAID)
        ```
    """
    active = ActiveCondition(condition=condition, effect_id=effect_id)
    if active not in target.conditions:
        return []
    target.conditions = tuple(existing for existing in target.conditions if existing != active)
    return [ConditionRemovedEvent(target_id=_entity_id(target), condition=condition.value, effect_id=effect_id)]


def _grant_modifiers(target: Any, specs: tuple[ModifierSpec, ...], effect_id: str) -> None:
    """Grant an effect's stat modifiers. This is the one place they're written."""
    if not hasattr(target, "stat_modifiers"):
        return
    granted = tuple(ActiveModifier(**spec.model_dump(), effect_id=effect_id) for spec in specs)
    target.stat_modifiers = (*target.stat_modifiers, *granted)


def _remove_modifiers(target: Any, effect_id: str) -> None:
    """Remove the stat modifiers owned by `effect_id`."""
    if not hasattr(target, "stat_modifiers"):
        return
    remaining = tuple(modifier for modifier in target.stat_modifiers if modifier.effect_id != effect_id)
    if len(remaining) != len(target.stat_modifiers):
        target.stat_modifiers = remaining


def kill(target: Any, *, permanent: bool = False) -> list[Event]:
    """Kill a creature outright: hit points to zero, the `dead` condition, and the death events.

    B/X kills a creature the moment it is reduced to zero hit points or fewer, and
    [`deal_damage`][osrlib.core.combat.deal_damage] calls this for you when damage takes a creature that far.
    Call it yourself for a death that skips the damage pipeline: a failed save against *finger of death*, a
    delayed poison whose onset ran out, a creature you're removing from play by fiat.

    Death is granted here rather than through an effect, so the `dead` condition has no `effect_id`. Calling
    twice is safe: a creature that's already dead returns no events and isn't killed again.

    Args:
        target: The creature to kill. Its `current_hp` and `conditions` are written in place.
        permanent: True when a regenerating creature can no longer come back, which for a troll means its
            non-regenerable damage has reached its maximum hit points. It changes the death event's code, not the
            outcome.

    Returns:
        The [`ConditionGainedEvent`][osrlib.core.events.ConditionGainedEvent] for `dead`, the
        [`DeathEvent`][osrlib.core.events.DeathEvent], and the referee-visible
        [`HitPointsReportedEvent`][osrlib.core.events.HitPointsReportedEvent], in that order. An empty list when
        the creature was already dead.

    Examples:
        ```python
        from osrlib.core.effects import Condition, has_condition, kill
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        streams = RngStreams(master_seed=5)
        spawn = streams.get(MONSTER_SPAWN_STREAM)
        goblin = spawn_monster(load_monsters().get("goblin"), id="monster-0001", stream=spawn)

        events = kill(goblin)
        assert [event.code for event in events] == [
            "effects.condition.gained",
            "combat.death.died",
            "combat.state.hit_points",
        ]
        assert goblin.current_hp == 0
        assert has_condition(goblin, Condition.DEAD)
        assert kill(goblin) == []  # already dead
        ```
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
        "magical_healing_half",
    }
)
"""The closed set of statistic names a modifier can adjust.

These are the only values a [`ModifierSpec`][osrlib.core.effects.ModifierSpec] accepts for its `kind`.
Combat looks each of these up by name while it resolves a roll, so a kind nothing reads changes nothing.
Constructing a `ModifierSpec` with a name outside this set raises a validation error rather than failing
silently, which is why the set is closed. Adding a kind means teaching combat to read it, so when you're
authoring your own content, express what you want with a kind already here.

What each one does:

- `attack_bonus` adjusts the bearer's own attack rolls, and `damage_bonus` its damage rolls.
- `attack_penalty_of_attackers` adjusts the rolls of anyone attacking the bearer, which is how a ward works.
- `save_bonus` adjusts the bearer's saving throws, narrowed by `element`, `save_categories`, or
  `versus_other_alignment`.
- `morale_bonus` adjusts the bearer's side's morale checks through
  [`morale_modifier`][osrlib.core.combat.morale_modifier].
- `ac_bonus` improves the bearer's armour class by its value, `ac_set` replaces the armour class outright when
  the set value is better, and `ac_set_vs_missile` does the same against missile attacks only.
- `damage_reduction_per_die` takes points off incoming damage, one per die rolled, for the named `element`.
- `damage_multiplier` multiplies the bearer's weapon damage and `melee_damage_multiplier` its melee damage,
  after the flat bonuses are added.
- `weapon_damage_dice_bonus` adds its own `dice` to the bearer's weapon damage.
- `strength_set` replaces the strength score the bearer's melee modifiers derive from, which is how Gauntlets of
  Ogre Power grant a fixed 18 and a Ring of Weakness a fixed 3.
- `counts_as_magical` makes the bearer's attacks count as magical, and `missile_immunity_nonmagical` absorbs
  non-magical missiles aimed at the bearer.
- `magical_healing_half` halves the hit points magical healing restores to the bearer.
"""


class ModifierSpec(BaseModel):
    """One adjustment to a combat statistic that an effect grants for as long as it lasts.

    You write these when you author a spell, a magic item, or an effect of your own, and put them in an
    [`EffectDefinition`][osrlib.core.effects.EffectDefinition]'s `modifiers`. Attaching that definition turns
    each spec into an [`ActiveModifier`][osrlib.core.effects.ActiveModifier] on the creature, and combat reads
    them back through [`modifier_total`][osrlib.core.effects.modifier_total] and its siblings. Nothing takes a
    bare spec: attaching an effect is the only way one reaches a creature. A spec is frozen, so the same one can
    sit in several definitions.

    The scope fields narrow when the modifier counts. Leave them at their defaults and the modifier applies to
    every roll of its kind.

    Examples:
        ```python
        from osrlib.core.effects import ModifierSpec

        bless = ModifierSpec(kind="attack_bonus", value=1)
        resist_fire = ModifierSpec(kind="save_bonus", value=2, element="fire")
        assert bless.dice is None and not bless.from_item
        assert resist_fire.element == "fire"
        ```
    """

    model_config = ConfigDict(frozen=True)

    kind: str
    """Which statistic the modifier adjusts. Must be one of [`MODIFIER_KINDS`][osrlib.core.effects.MODIFIER_KINDS]
    or construction raises a validation error."""

    value: int = 0
    """The signed adjustment: *bless*'s +1 attack bonus, *protection from evil*'s -1 on attackers, the armour
    class a `ac_set` kind sets. Leave it at 0 for a kind that uses dice or acts as a flag."""

    dice: str | None = None
    """A dice expression rolled instead of adding `value`, for the kinds that grant dice: *striking*'s `"1d6"` of
    extra weapon damage. Parsed at construction by [`parse`][osrlib.core.dice.parse], so a malformed expression
    raises a validation error rather than failing at the table."""

    element: str | None = None
    """Narrows the modifier to one damage or save element, like `"fire"` for *resist fire*. A modifier scoped
    to an element counts only when the caller names that element in the roll."""

    versus_other_alignment: bool = False
    """True narrows the modifier to rolls against creatures of a different alignment, which is how *protection
    from evil* works. It counts only when the caller attests that the alignments differ."""

    save_categories: tuple[str, ...] = ()
    """Narrows a save bonus to the named saving throw categories, as a Displacer Cloak covers petrification,
    rods, spells, staves, and wands but nothing else. Empty means every category."""

    melee_only: bool = False
    """True narrows the modifier to melee attacks, which is how the Displacer Cloak's -2 on attackers leaves
    missile attacks alone. It counts only when the caller attests the attack is melee."""

    from_item: bool = False
    """True marks the modifier as coming from a magic item rather than a spell, which exempts it from the rule
    that only the largest spell bonus counts. Item modifiers add up on top of the capped spell total. Set it on
    potion effects and ward scrolls as well as worn items: the rule covers magic items in general."""

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
    """One live modifier on a creature, paired with the effect that granted it.

    You read these off a creature's `stat_modifiers` tuple. Attaching an effect turns each of its
    [`ModifierSpec`][osrlib.core.effects.ModifierSpec] entries into one of these, and expiry or release takes
    them back, the same way conditions work. Nothing else writes the tuple, so a creature's modifiers always
    trace to a live effect.

    Read them through [`modifier_total`][osrlib.core.effects.modifier_total],
    [`modifier_values`][osrlib.core.effects.modifier_values], [`modifier_dice`][osrlib.core.effects.modifier_dice],
    and [`has_modifier`][osrlib.core.effects.has_modifier] rather than scanning the tuple: those helpers apply
    the scope filters and the rule that spell bonuses don't add up.
    """

    effect_id: str
    """The id of the [`ActiveEffect`][osrlib.core.effects.ActiveEffect] that granted the modifier and will take
    it back."""


def modifier_values(
    target: Any,
    kind: str,
    *,
    element: str | None = None,
    versus_differs: bool = False,
    save_category: str | None = None,
    melee: bool = False,
) -> list[int]:
    """Return every modifier value of one kind that applies to the situation you describe.

    Use this when you need the individual values rather than a single number: the armour class rules read the
    `ac_set` values one at a time and keep the best. For the ordinary case, where you want one number to add to a
    roll, call [`modifier_total`][osrlib.core.effects.modifier_total], which also applies the rule that spell
    bonuses don't add up.

    The keyword arguments describe the roll in play, and a modifier narrowed to something you don't name is
    left out. An element-scoped modifier counts only when you pass its `element`, an alignment-scoped one only
    when you pass `versus_differs=True`, a category-scoped save bonus only when you pass one of its categories,
    and a melee-only modifier only when you pass `melee=True`.

    Args:
        target: The creature to read modifiers from. An object with no `stat_modifiers` tuple reads as having
            none.
        kind: The statistic to look for, one of [`MODIFIER_KINDS`][osrlib.core.effects.MODIFIER_KINDS].
        element: The damage or save element in play, like `"fire"`. Leave it None outside an elemental roll.
        versus_differs: True when the other creature in the roll has a different alignment from the target.
        save_category: The saving throw category in play. Leave it None outside a saving throw.
        melee: True when the attack in play is melee.

    Returns:
        The signed values of the modifiers that apply, in the order their effects were attached. Empty when none
        apply.

    Examples:
        ```python
        from osrlib.core.clock import GameClock, TimeUnit
        from osrlib.core.effects import EffectDefinition, EffectsLedger, ModifierSpec, modifier_values
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        streams = RngStreams(master_seed=5)
        spawn = streams.get(MONSTER_SPAWN_STREAM)
        goblin = spawn_monster(load_monsters().get("goblin"), id="monster-0001", stream=spawn)
        registry = {"monster-0001": goblin}

        resist_fire = EffectDefinition(
            kind="resist_fire",
            duration_unit=TimeUnit.TURN,
            duration_amount=12,
            modifiers=(ModifierSpec(kind="save_bonus", value=2, element="fire"),),
        )
        ledger = EffectsLedger()
        ledger.attach(resist_fire, "monster-0001", clock=GameClock(), allocator=IdAllocator(), registry=registry)

        assert modifier_values(goblin, "save_bonus", element="fire") == [2]
        assert modifier_values(goblin, "save_bonus", element="cold") == []
        ```
    """
    matching = _matching_modifiers(target, kind, element, versus_differs, save_category, melee)
    return [modifier.value for modifier in matching]


def _matching_modifiers(
    target: Any,
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
    target: Any,
    kind: str,
    *,
    element: str | None = None,
    versus_differs: bool = False,
    save_category: str | None = None,
    melee: bool = False,
) -> int:
    """Return the one number to add to a roll for a creature's modifiers of one kind.

    This is the call combat makes, and the one you want when you're resolving a roll of your own. It reads the
    same modifiers [`modifier_values`][osrlib.core.effects.modifier_values] returns and folds them into a single
    signed adjustment, applying the rule that spells affecting the same statistic don't combine: only the
    largest bonus and the largest penalty count. Two *blesses* give +1, not +2, while a *bless* and a *blight*
    cancel out.

    Modifiers marked `from_item` sit outside that rule and are added on top, all of them, because the
    no-stacking rule covers spells rather than magic items. The scope arguments work exactly as they do for
    [`modifier_values`][osrlib.core.effects.modifier_values].

    Args:
        target: The creature to total modifiers for.
        kind: The statistic to total, one of [`MODIFIER_KINDS`][osrlib.core.effects.MODIFIER_KINDS].
        element: The damage or save element in play, like `"fire"`. Leave it None outside an elemental roll.
        versus_differs: True when the other creature in the roll has a different alignment from the target.
        save_category: The saving throw category in play. Leave it None outside a saving throw.
        melee: True when the attack in play is melee.

    Returns:
        The signed adjustment to add to the roll, and 0 when nothing applies.

    Examples:
        Two blessings and one blight, all on the same goblin, come to a single point of bonus and a single point
        of penalty:

        ```python
        from osrlib.core.clock import GameClock, TimeUnit
        from osrlib.core.effects import EffectDefinition, EffectsLedger, ModifierSpec, modifier_total, modifier_values
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        streams = RngStreams(master_seed=5)
        spawn = streams.get(MONSTER_SPAWN_STREAM)
        goblin = spawn_monster(load_monsters().get("goblin"), id="monster-0001", stream=spawn)
        registry = {"monster-0001": goblin}

        ledger, clock, allocator = EffectsLedger(), GameClock(), IdAllocator()
        bless = EffectDefinition(
            kind="bless",
            duration_unit=TimeUnit.TURN,
            duration_amount=6,
            modifiers=(ModifierSpec(kind="attack_bonus", value=1),),
        )
        blight = EffectDefinition(
            kind="blight",
            duration_unit=TimeUnit.TURN,
            duration_amount=6,
            modifiers=(ModifierSpec(kind="attack_bonus", value=-1),),
        )
        for definition in (bless, bless, blight):
            ledger.attach(definition, "monster-0001", clock=clock, allocator=allocator, registry=registry)

        assert modifier_values(goblin, "attack_bonus") == [1, 1, -1]
        assert modifier_total(goblin, "attack_bonus") == 0
        ```
    """
    matching = _matching_modifiers(target, kind, element, versus_differs, save_category, melee)
    spell_values = [modifier.value for modifier in matching if not modifier.from_item]
    item_values = [modifier.value for modifier in matching if modifier.from_item]
    bonus = max((value for value in spell_values if value > 0), default=0)
    penalty = min((value for value in spell_values if value < 0), default=0)
    return bonus + penalty + sum(item_values)


def modifier_dice(target: Any, kind: str) -> str | None:
    """Return the dice expression of a creature's dice-valued modifier of one kind.

    A few modifiers grant dice instead of a flat number, *striking*'s extra `"1d6"` of weapon damage among them.
    Call this to find them, then roll the expression yourself with [`roll`][osrlib.core.dice.roll]. For flat
    adjustments, call [`modifier_total`][osrlib.core.effects.modifier_total] instead.

    Only the first matching modifier is returned, which is the no-stacking rule applied to dice: a creature under
    two *strikings* rolls one extra die, not two.

    Args:
        target: The creature to read modifiers from.
        kind: The statistic to look for, one of [`MODIFIER_KINDS`][osrlib.core.effects.MODIFIER_KINDS].

    Returns:
        The dice expression, in the notation [`parse`][osrlib.core.dice.parse] accepts, or `None` when the
        creature has no dice-valued modifier of that kind.

    Examples:
        ```python
        from osrlib.core.clock import GameClock, TimeUnit
        from osrlib.core.effects import EffectDefinition, EffectsLedger, ModifierSpec, modifier_dice
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        streams = RngStreams(master_seed=5)
        spawn = streams.get(MONSTER_SPAWN_STREAM)
        goblin = spawn_monster(load_monsters().get("goblin"), id="monster-0001", stream=spawn)
        registry = {"monster-0001": goblin}

        striking = EffectDefinition(
            kind="striking",
            duration_unit=TimeUnit.TURN,
            duration_amount=6,
            modifiers=(ModifierSpec(kind="weapon_damage_dice_bonus", dice="1d6"),),
        )
        ledger = EffectsLedger()
        ledger.attach(striking, "monster-0001", clock=GameClock(), allocator=IdAllocator(), registry=registry)

        assert modifier_dice(goblin, "weapon_damage_dice_bonus") == "1d6"
        assert modifier_dice(goblin, "damage_bonus") is None
        ```
    """
    for modifier in getattr(target, "stat_modifiers", ()):
        if modifier.kind == kind and modifier.dice is not None:
            return modifier.dice
    return None


def has_modifier(target: Any, kind: str) -> bool:
    """Return whether a creature has any modifier of one kind.

    Use this for the kinds that act as flags rather than numbers, where the presence of the modifier is the whole
    rule: `counts_as_magical`, `missile_immunity_nonmagical`, and `magical_healing_half`. For a kind that uses
    a number, call [`modifier_total`][osrlib.core.effects.modifier_total], whose 0 means "no adjustment" rather
    than "not present".

    It ignores the scope fields, so a modifier narrowed to one element still reports True here. Where the scope
    matters, go through [`modifier_values`][osrlib.core.effects.modifier_values].

    Args:
        target: The creature to read modifiers from.
        kind: The statistic to look for, one of [`MODIFIER_KINDS`][osrlib.core.effects.MODIFIER_KINDS].

    Returns:
        True when the creature has at least one modifier of that kind.

    Examples:
        ```python
        from osrlib.core.clock import GameClock
        from osrlib.core.effects import EffectDefinition, EffectsLedger, ModifierSpec, has_modifier
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        streams = RngStreams(master_seed=5)
        spawn = streams.get(MONSTER_SPAWN_STREAM)
        goblin = spawn_monster(load_monsters().get("goblin"), id="monster-0001", stream=spawn)
        registry = {"monster-0001": goblin}

        enchanted = EffectDefinition(
            kind="striking",
            modifiers=(ModifierSpec(kind="counts_as_magical", value=1),),
        )
        ledger = EffectsLedger()
        ledger.attach(enchanted, "monster-0001", clock=GameClock(), allocator=IdAllocator(), registry=registry)

        assert has_modifier(goblin, "counts_as_magical")
        assert not has_modifier(goblin, "missile_immunity_nonmagical")
        ```
    """
    return any(modifier.kind == kind for modifier in getattr(target, "stat_modifiers", ()))


class EffectDefinition(BaseModel):
    """The blueprint for an effect: how long it lasts, what it does while it lasts, and what happens when it ends.

    Write one of these for each spell, ability, or hazard you want to put on a creature, then hand it to
    [`EffectsLedger.attach`][osrlib.core.effects.EffectsLedger.attach] with the entity id or location it applies
    to. Attaching turns the blueprint into a live [`ActiveEffect`][osrlib.core.effects.ActiveEffect]. The
    blueprint itself is frozen, so one definition serves every creature you attach it to. The compiled spell and
    magic item data already includes definitions for the published content, so you write your own only when you're
    authoring something new.

    Give the effect a duration through `duration_unit` with either `duration_amount` or `duration_dice`. Leave
    the unit out and the effect runs until you release it. Set `permanent` for something only magic undoes.

    Examples:
        A four-turn sleep that grants a condition, and an indefinite +1 to attacks that grants a modifier:

        ```python
        from osrlib.core.clock import TimeUnit
        from osrlib.core.effects import Condition, EffectDefinition, ModifierSpec

        sleep = EffectDefinition(
            kind="sleep",
            duration_unit=TimeUnit.TURN,
            duration_amount=4,
            condition=Condition.ASLEEP,
            dispellable=True,
        )
        bless = EffectDefinition(kind="bless", modifiers=(ModifierSpec(kind="attack_bonus", value=1),))

        assert sleep.stacking == "stack"  # the default: a second sleep is a second effect
        assert bless.duration_unit is None  # no unit means it runs until released
        ```
    """

    model_config = ConfigDict(frozen=True)

    kind: str = Field(min_length=1)
    """The effect's name, like `"sleep"` or `"regeneration"`. Stacking compares kinds, and
    [`EffectsLedger.active_on`][osrlib.core.effects.EffectsLedger.active_on] filters on it, so pick one name per
    thing and use it everywhere. Any non-empty string is accepted."""

    duration_unit: TimeUnit | None = None
    """The unit the duration is counted in: rounds, turns, or days. `None` means the effect has no duration and
    runs until you release it."""

    duration_amount: int | None = None
    """A fixed duration, counted in `duration_unit`. Use this or `duration_dice`, not both."""

    duration_dice: str | None = None
    """A dice expression rolled once at attach time to set the duration, like `"2d6"`. Rolling one needs the
    effects stream, so attaching a definition that uses dice without passing `stream` raises `ValueError`.
    Parsed at construction by [`parse`][osrlib.core.dice.parse]."""

    permanent: bool = False
    """True means the effect never expires on its own, which is how petrification lasts until someone casts
    *stone to flesh*. It says nothing about whether *dispel magic* can end it: that is `dispellable`."""

    tick: str | None = None
    """The name of a periodic behavior the ledger runs while the effect lasts. There are two.
    `"regeneration"` heals the bearer and can bring a troll back from death. `"charm_resave"` rolls a saving
    throw that ends the effect when it passes. Any other name raises `ValueError` at the first tick."""

    tick_interval_rounds: int = Field(default=1, ge=1)
    """How many rounds pass between ticks. The default of 1 ticks every round. A charm sets this from the
    subject's intelligence, so the dull re-save monthly and the bright daily."""

    stacking: Literal["stack", "refresh", "ignore"] = "stack"
    """What happens when the same kind is attached to a target that already has one. `"stack"` adds a second
    effect. `"refresh"` restarts the existing effect's duration and attaches nothing new. `"ignore"` does
    nothing at all, and the attach returns no effect."""

    expiry: str | None = None
    """The name of an outcome the ledger resolves when the duration runs out, on top of the ordinary ending
    rather than in place of it. The effect is dropped, the
    [`EffectExpiredEvent`][osrlib.core.events.EffectExpiredEvent] goes out, the condition and modifiers come
    off, and the outcome runs last. There are three. `"death"` kills the bearer, which is how a delayed poison
    works. `"splash_damage"` deals the second application of burning oil or holy water.
    `"weakness_strength_set"` replaces the finished onset with the curse itself. Any other name raises
    `ValueError` at expiry."""

    condition: Condition | None = None
    """A [`Condition`][osrlib.core.effects.Condition] granted when the effect attaches and taken back when it
    expires or is released. A target immune to the condition is never affected, and the attach returns no
    effect."""

    modifiers: tuple[ModifierSpec, ...] = ()
    """The [`ModifierSpec`][osrlib.core.effects.ModifierSpec] adjustments granted while the effect lasts and
    taken back when it ends."""

    dispellable: bool = False
    """True marks the effect as something *dispel magic* can end. Everything
    [`cast_spell`][osrlib.core.spells.cast_spell] attaches is dispellable, permanent effects included, while what
    a monster inflicts is not."""

    params: dict[str, int | str | bool | tuple[int | str, ...]] = {}
    """Whatever else the effect's tick or expiry behavior needs to read: regeneration's `per_round`,
    `delay_rounds`, and `revive`, a splash douse's `dice` and `element`, a slowed-healing effect's
    `healing_rest_days`. Each behavior documents the keys it reads, and keys it doesn't recognize are left
    untouched."""

    @field_validator("duration_dice")
    @classmethod
    def _dice_must_parse(cls, value: str | None) -> str | None:
        if value is not None:
            parse(value)
        return value


class ActiveEffect(BaseModel):
    """One effect currently running on a creature, item, or location.

    [`EffectsLedger.attach`][osrlib.core.effects.EffectsLedger.attach] returns one of these and keeps it in the
    ledger's `effects` list, and [`EffectsLedger.active_on`][osrlib.core.effects.EffectsLedger.active_on] finds
    them again. You read one to ask how long an effect has left or what it's tracking, and you pass its
    `effect_id` to [`EffectsLedger.release`][osrlib.core.effects.EffectsLedger.release] to end it early. Build
    one yourself only when you're restoring a saved game. In play, attaching is what creates them.
    """

    model_config = ConfigDict(validate_assignment=True)

    effect_id: str
    """The effect's id, allocated at attach time by the
    [`IdAllocator`][osrlib.core.monsters.IdAllocator] you passed, in the form `effect-0001`. The conditions and
    modifiers the effect granted record this id, which is how they are matched back when it ends."""

    definition: EffectDefinition
    """The [`EffectDefinition`][osrlib.core.effects.EffectDefinition] this effect was attached from, kept here
    so the ledger can tick and expire it without looking anything up."""

    target_ref: str
    """What the effect is on: an entity id for a creature, or a location string for something that sits in a
    place, as a burning oil pool or a stationary *silence* does. A location reference is not a key in the
    registry, so an effect on a location grants no conditions or modifiers."""

    attached_round: int = Field(ge=0)
    """The absolute round the effect was attached on, counted from the start of the game clock. Ticks are
    counted from here, and it is the first key effects are ordered by when several resolve in one round."""

    expires_round: int | None = None
    """The absolute round the effect expires on, or `None` when it has no duration or is permanent. Suspension
    pushes it forward one round for each round the bearer spends petrified, so a suspended effect keeps the time
    it had left."""

    caster_level: int | None = None
    """The level of the caster whose spell attached this effect, recorded when the attach passed one.
    *Dispel magic* rolls against it to decide whether the effect survives."""

    state: dict[str, int] = {}
    """The effect's own running bookkeeping, written by its tick and expiry behaviors: the round a troll revives
    on, the number of consecutive rest days a slowed-healing effect has counted. Read it if you want to show a
    countdown, and leave the writing to the ledger."""


# An invariant test asserts that ledger effects and the conditions and stat modifiers
# they grant never fall out of step: every mutation must flow through this class's
# helpers, or through kill() for `dead`. Keep it that way when extending the engine.
class EffectsLedger(BaseModel):
    """The engine that contains every live effect and runs it against the game clock.

    One ledger covers a whole game: a [`GameSession`][osrlib.crawl.session.GameSession] creates one and keeps it
    for the life of the session, and a caller running the rules without a session creates one and keeps it
    alongside the [`GameClock`][osrlib.core.clock.GameClock]. It is a pydantic model, so saving a game is saving
    the ledger along with the clock and the creatures.

    Four calls are the whole interface. [`attach`][osrlib.core.effects.EffectsLedger.attach] puts an effect on a
    target, [`active_on`][osrlib.core.effects.EffectsLedger.active_on] asks what is on one,
    [`release`][osrlib.core.effects.EffectsLedger.release] ends an effect early, and
    [`advance`][osrlib.core.effects.EffectsLedger.advance] moves the clock and resolves everything the passing
    time triggers. Nothing happens without `advance`: an effect with a duration sits there until the clock
    reaches its expiry round, so advance the clock through the ledger rather than writing to the clock directly.

    Examples:
        ```python
        from osrlib.core.clock import GameClock, TimeUnit
        from osrlib.core.effects import EFFECTS_STREAM, Condition, EffectDefinition, EffectsLedger, has_condition
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        streams = RngStreams(master_seed=3)
        spawn = streams.get(MONSTER_SPAWN_STREAM)
        goblin = spawn_monster(load_monsters().get("goblin"), id="monster-0001", stream=spawn)
        registry = {"monster-0001": goblin}

        ledger = EffectsLedger()
        clock = GameClock()
        web = EffectDefinition(
            kind="web",
            duration_unit=TimeUnit.TURN,
            duration_amount=2,
            condition=Condition.ENTANGLED,
        )
        effect, _ = ledger.attach(web, "monster-0001", clock=clock, allocator=IdAllocator(), registry=registry)
        assert effect is not None and effect.expires_round == 120  # two turns of sixty rounds

        # One turn on, the web still holds.
        ledger.advance(clock, 1, TimeUnit.TURN, registry, stream=streams.get(EFFECTS_STREAM))
        assert has_condition(goblin, Condition.ENTANGLED)

        # One more, and it lets go.
        ledger.advance(clock, 1, TimeUnit.TURN, registry, stream=streams.get(EFFECTS_STREAM))
        assert not has_condition(goblin, Condition.ENTANGLED)
        ```
    """

    model_config = ConfigDict(validate_assignment=True)

    effects: list[ActiveEffect] = []
    """Every live [`ActiveEffect`][osrlib.core.effects.ActiveEffect], in the order they were attached. Read it to
    see everything running at once. To find the effects on one target, call
    [`active_on`][osrlib.core.effects.EffectsLedger.active_on]. Attaching, releasing, and expiry maintain the
    list, so leave the writing to them."""

    def active_on(self, target_ref: str, kind: str | None = None) -> list[ActiveEffect]:
        """Return the effects currently running on one target.

        Use this to answer questions about a creature's situation that the condition and modifier helpers cannot:
        whether a *mirror image* is still up, how many rounds a light source has left, whether an anti-magic
        shell is blocking a cast. Pass `kind` when you know which effect you're after, and you get either an
        empty list or the ones that match.

        Args:
            target_ref: The entity id or location string the effects are attached to.
            kind: An [`EffectDefinition`][osrlib.core.effects.EffectDefinition] `kind` to narrow to. Leave it out
                for everything on the target.

        Returns:
            The matching effects, in the order they were attached. The list is new, but the effects in it are the
            ledger's own, so a change to one changes what the ledger runs.
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
        allocator: Any,
        registry: Mapping[str, Any] | None = None,
        stream: RngStream | None = None,
        caster_level: int | None = None,
    ) -> tuple[ActiveEffect | None, list[Event]]:
        """Put an effect on a target and return it with the events the attach produced.

        This is the way an effect starts. Build an [`EffectDefinition`][osrlib.core.effects.EffectDefinition],
        call this with the target's entity id, and the ledger works out when the effect expires, grants the
        condition and modifiers it brings, and starts counting its ticks. Afterwards, keep the clock moving
        through [`advance`][osrlib.core.effects.EffectsLedger.advance] or nothing further happens.

        When a spell is what attaches the effect, [`cast_spell`][osrlib.core.spells.cast_spell] makes this call
        for you and hands back the same events.

        The call can hand back `None` instead of an effect, so check before you use it. Two cases produce it:
        the definition's `stacking` is `"ignore"` and the target already has that kind, or the target's template
        lists the definition's condition among its `condition_immunities`. A `stacking` of `"refresh"` is
        different again: you get the existing effect back with its duration restarted, and no events.

        Args:
            definition: The blueprint to attach.
            target_ref: The entity id of the creature, or the location string of the place, to attach to. An id
                that is not a key in `registry` attaches the effect but grants nothing.
            clock: The game clock. The current round anchors the duration and the tick count. The clock is
                read, not advanced.
            allocator: The [`IdAllocator`][osrlib.core.monsters.IdAllocator] that grants the effect its id. A
                session keeps one. Create your own otherwise.
            registry: The live creatures by entity id, so the attach can grant conditions and modifiers. Pass
                None, or leave the target out of it, and the effect runs with nothing to write to.
            stream: The effects stream from [`EFFECTS_STREAM`][osrlib.core.effects.EFFECTS_STREAM]. Needed only
                when the definition has `duration_dice`.
            caster_level: The casting caster's level, recorded on the effect for *dispel magic* to roll against.

        Returns:
            A pair of the attached effect and its events. The events are the
            [`EffectAttachedEvent`][osrlib.core.events.EffectAttachedEvent] and, when the definition brings a
            condition the target takes, the
            [`ConditionGainedEvent`][osrlib.core.events.ConditionGainedEvent]. The pair is `(None, [])` when
            nothing was attached, and on a refresh it is the existing effect with an empty event list.

        Raises:
            ValueError: If the definition has `duration_dice` and no `stream` was passed, or if it names a
                `duration_unit` with neither an amount nor dice.
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

    def release(self, effect_id: str, registry: Mapping[str, Any] | None = None) -> list[Event]:
        """End an effect before its duration runs out.

        Call this when something in the game cuts an effect short: a *dispel magic*, a charmed creature making
        its save, a light source put out, an invisible creature attacking and losing the invisibility. The
        condition and the modifiers the effect granted come off with it.

        Find the id first with [`active_on`][osrlib.core.effects.EffectsLedger.active_on], and copy the list
        before you release from it, since releasing changes the ledger's own list as you go. To end an effect
        because time ran out, do nothing: [`advance`][osrlib.core.effects.EffectsLedger.advance] expires it for
        you and emits [`EffectExpiredEvent`][osrlib.core.events.EffectExpiredEvent] instead.

        Args:
            effect_id: The id of the effect to end, from its
                [`ActiveEffect`][osrlib.core.effects.ActiveEffect].
            registry: The live creatures by entity id, so the condition and modifiers can be taken back. Leave it
                out and the effect is dropped from the ledger with the creature still under them.

        Returns:
            The [`EffectReleasedEvent`][osrlib.core.events.EffectReleasedEvent] and, when the effect granted a
            condition to a creature in the registry, the
            [`ConditionRemovedEvent`][osrlib.core.events.ConditionRemovedEvent].

        Raises:
            ValueError: If the ledger has no effect with that id, which means it already expired or was
                already released.
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
        registry: Mapping[str, Any],
        *,
        stream: RngStream,
        allocator: Any | None = None,
    ) -> list[Event]:
        """Move the game clock forward and resolve everything the passing time triggers.

        This is what makes an effect with a duration actually end, and a regenerating troll actually heal.
        Advance the clock through this call rather than writing to
        [`GameClock.rounds`][osrlib.core.clock.GameClock] yourself: the clock records elapsed time and nothing
        about what is attached to whom, so time you add behind the ledger's back resolves no effects at all. A
        [`GameSession`][osrlib.crawl.session.GameSession] makes this call for you inside
        [`advance_rounds`][osrlib.crawl.session.GameSession.advance_rounds] and
        [`advance_turns`][osrlib.crawl.session.GameSession.advance_turns].

        Every round in the span is resolved, one at a time, in the same order. Effects whose bearer is petrified
        by another effect suspend first, and a suspended effect neither ticks nor ages: its expiry moves forward
        one round for each round it spends suspended. Then expirations resolve, then ticks. Within each of those
        phases, effects go in attachment order, tie-broken by effect id, so the same span always produces the
        same events in the same order.

        Args:
            clock: The game clock. It is advanced in place, so it shows the new time when the call returns.
            n: How many units to advance. Advancing a long span resolves every round in it, so a day is
                thousands of rounds of work.
            unit: The unit `n` counts: rounds, turns, or days.
            registry: The live creatures by entity id, so conditions, modifiers, and hit points can be written. A
                session keeps one across play, and a plain dict works.
            stream: The effects stream from [`EFFECTS_STREAM`][osrlib.core.effects.EFFECTS_STREAM], for the draws
                that ticks and expiries make.
            allocator: The [`IdAllocator`][osrlib.core.monsters.IdAllocator]. Needed only by the expiry behaviors
                that attach a follow-on effect, which raise `ValueError` without one.

        Returns:
            Every event the span produced, in the order it was produced, ready to append to your log.

        Raises:
            ValueError: If a tick or expiry behavior names something osrlib doesn't define, or if a follow-on
                attach needed an `allocator` and none was passed.

        Examples:
            A troll that took ten points of damage regenerates three of them a round:

            ```python
            from osrlib.core.clock import GameClock, TimeUnit
            from osrlib.core.effects import EFFECTS_STREAM, EffectsLedger, regeneration_definition
            from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, spawn_monster
            from osrlib.core.rng import RngStreams
            from osrlib.data import load_monsters

            streams = RngStreams(master_seed=9)
            template = load_monsters().get("troll")
            troll = spawn_monster(template, id="monster-0001", stream=streams.get(MONSTER_SPAWN_STREAM))
            registry = {"monster-0001": troll}
            troll.current_hp -= 10

            ledger = EffectsLedger()
            clock = GameClock()
            definition = regeneration_definition(template.abilities[0].params)
            ledger.attach(definition, "monster-0001", clock=clock, allocator=IdAllocator(), registry=registry)

            events = ledger.advance(clock, 2, TimeUnit.ROUND, registry, stream=streams.get(EFFECTS_STREAM))
            assert [event.code for event in events] == [
                "effects.effect.ticked",
                "combat.healing.applied",
                "combat.state.hit_points",
                "effects.effect.ticked",
                "combat.healing.applied",
                "combat.state.hit_points",
            ]
            assert troll.current_hp == troll.max_hp - 4  # six of the ten points back
            ```
        """
        start = clock.rounds
        clock.advance(n, unit)
        events: list[Event] = []
        for current_round in range(start + 1, clock.rounds + 1):
            events.extend(self._resolve_round(current_round, registry, stream, allocator))
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

    def _suspended(self, effect: ActiveEffect, registry: Mapping[str, Any]) -> bool:
        target = registry.get(effect.target_ref)
        if target is None:
            return False
        return any(
            active.condition is Condition.PETRIFIED and active.effect_id != effect.effect_id
            for active in getattr(target, "conditions", ())
        )

    def _resolve_round(
        self, current_round: int, registry: Mapping[str, Any], stream: RngStream, allocator: Any | None = None
    ) -> list[Event]:
        events: list[Event] = []
        # An invariant test asserts the round-resolution order: suspension, then
        # expiry, then ticks, tie-broken by effect id within each phase. Changing it
        # is a rules decision, not a refactor.
        # Suspension first: a suspended effect neither expires nor ticks this round,
        # and pushing its expiry forward preserves the duration it has left.
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
                events.extend(self._expire(effect, current_round, registry, stream, allocator))
        for effect in self._ordered():
            if effect.effect_id in suspended_ids or effect.definition.tick is None:
                continue
            if (current_round - effect.attached_round) % effect.definition.tick_interval_rounds == 0:
                events.extend(self._tick(effect, current_round, registry, stream))
        return events

    def _expire(
        self,
        effect: ActiveEffect,
        current_round: int,
        registry: Mapping[str, Any],
        stream: RngStream,
        allocator: Any | None = None,
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
        elif definition.expiry == "weakness_strength_set":
            # The Ring of Weakness's onset ran out: the curse takes hold as an
            # indefinite item-kind effect setting STR to the printed value.
            if allocator is None:
                raise ValueError("the weakness onset attaches a follow-on effect; advance with an allocator")
            follow_on = EffectDefinition(
                kind="ring_of_weakness",
                stacking="ignore",
                modifiers=(
                    ModifierSpec(
                        kind="strength_set", value=_int_param(definition.params, "strength_set"), from_item=True
                    ),
                ),
            )
            clock_now = GameClock(rounds=current_round)
            _, attach_events = self.attach(
                follow_on, effect.target_ref, clock=clock_now, allocator=allocator, registry=registry
            )
            events.extend(attach_events)
        elif definition.expiry == "splash_damage":
            # Deferred import: combat.py imports from this module, so importing it at
            # module scope would create a cycle.
            from osrlib.core.combat import DamageSource, deal_damage

            dice = definition.params.get("dice")
            keys = definition.params.get("keys", ())
            element = definition.params.get("element")
            result = roll(str(dice), stream)
            source = DamageSource(
                keys=tuple(str(key) for key in keys) if isinstance(keys, tuple) else (),
                element=str(element) if element is not None else None,
                kind="splash",
            )
            events.extend(deal_damage(target, result.total, rolls=result.rolls, source=source))
        else:
            raise ValueError(f"unknown expiry outcome {definition.expiry!r} on effect kind {definition.kind!r}")
        return events

    def _tick(
        self, effect: ActiveEffect, current_round: int, registry: Mapping[str, Any], stream: RngStream
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
        target: Any,
        registry: Mapping[str, Any],
        stream: RngStream,
    ) -> list[Event]:
        """Roll the charm's periodic saving throw, releasing the charm when it passes.

        The re-save is a tick-time draw, so it comes from the effects stream. The interval was fixed at attach
        time from the subject's INT band, and rides `tick_interval_rounds`.
        """
        # Deferred import: combat.py imports from this module, so importing it at
        # module scope would create a cycle.
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
        self, effect: ActiveEffect, current_round: int, target: Any, stream: RngStream
    ) -> list[Event]:
        definition = effect.definition
        params = definition.params
        per_round = _int_param(params, "per_round")
        delay = _int_param(params, "delay_rounds")
        while_alive = bool(params.get("while_alive", False))
        revive_dice = params.get("revive")
        target_id = _entity_id(target)
        regenerable_max = target.max_hp - getattr(target, "nonregen_damage", 0)
        events: list[Event] = []
        if has_condition(target, Condition.DEAD):
            if while_alive or revive_dice is None or regenerable_max < 1:
                return []
            if "revive_at" not in effect.state:
                # The 2d6-round countdown anchors to the round the killing damage
                # landed, which the instance records, and falls back to this
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


def regeneration_definition(params: Mapping[str, Any]) -> EffectDefinition:
    """Build the effect definition for a monster that regenerates.

    A regenerating monster like a troll has a `regeneration` ability whose params say how fast it heals
    and whether it comes back from death. Pass those params here and attach the result with
    [`EffectsLedger.attach`][osrlib.core.effects.EffectsLedger.attach] when the monster enters play, and every
    [`advance`][osrlib.core.effects.EffectsLedger.advance] heals it on its own. Find the params on the template's
    ability with the tag `regeneration`.

    The definition it builds has no duration, so the regeneration runs until you release it, and its `stacking`
    is `"ignore"`, so attaching it twice to the same monster is harmless.

    Args:
        params: The ability's params. The tick reads four keys. `per_round` is how many hit points come back
            each round. `delay_rounds` is how many rounds of quiet the monster needs after being damaged before
            healing resumes. `revive` is a dice expression for how long the monster lies dead before getting
            back up. `while_alive` is True for a monster that heals only while living. Anything else is left
            untouched, and any list becomes a tuple so the definition stays hashable.

    Returns:
        An [`EffectDefinition`][osrlib.core.effects.EffectDefinition] of kind `"regeneration"` with the
        `"regeneration"` tick.

    Examples:
        ```python
        from osrlib.core.effects import regeneration_definition
        from osrlib.data import load_monsters

        ability = next(a for a in load_monsters().get("troll").abilities if a.tag == "regeneration")
        definition = regeneration_definition(ability.params)

        assert definition.kind == "regeneration" and definition.tick == "regeneration"
        assert definition.duration_unit is None  # it runs until released
        assert definition.params["per_round"] == 3
        ```
    """
    return EffectDefinition(
        kind="regeneration",
        tick="regeneration",
        stacking="ignore",
        params={key: value if not isinstance(value, list) else tuple(value) for key, value in params.items()},
    )
