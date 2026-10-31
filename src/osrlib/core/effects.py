"""Attach timed effects to creatures and locations, and run them against the game clock.

An effect is anything in play that lasts a while or repeats: a *sleep* spell, a poison, a burning torch, a troll's
regeneration. You describe one with an [`EffectDefinition`][osrlib.core.effects.EffectDefinition], attach it to a target
through an [`EffectsLedger`][osrlib.core.effects.EffectsLedger], and move time forward by passing a
[`GameClock`][osrlib.core.clock.GameClock] to the ledger's `advance` method. The ledger ends each effect when its
duration runs out, and runs its periodic behavior along the way.

Three kinds of code call this module. A [`GameSession`][osrlib.crawl.session.GameSession] calls
[`EffectsLedger.advance`][osrlib.core.effects.EffectsLedger.advance] every time it moves the game clock.
[`cast_spell`][osrlib.core.spells.cast_spell] calls [`EffectsLedger.attach`][osrlib.core.effects.EffectsLedger.attach]
when a spell takes effect, so the spell's duration becomes a live effect. You call both yourself when you run the
rules without a session. You then keep the ledger, the [`GameClock`][osrlib.core.clock.GameClock], and the
registry, and you advance them in your own loop.

The members, in the order you use them:

- [`EffectDefinition`][osrlib.core.effects.EffectDefinition]: the blueprint you write, with a duration, a
  condition, modifiers, a periodic tick, and an outcome at expiry.
- [`ModifierSpec`][osrlib.core.effects.ModifierSpec]: one stat adjustment a definition grants. Its `kind` is one
  of [`MODIFIER_KINDS`][osrlib.core.effects.MODIFIER_KINDS].
- [`regeneration_definition`][osrlib.core.effects.regeneration_definition]: takes a regenerating monster's
  ability params and returns a ready-made definition.
- [`EffectsLedger`][osrlib.core.effects.EffectsLedger]: the object that tracks and runs effects. Its `attach` method
  takes a definition and returns an [`ActiveEffect`][osrlib.core.effects.ActiveEffect]. Its `advance` method moves the
  clock and returns the events of everything that expired or ticked. Its `release` method ends an effect early, and its
  `active_on` method lists the effects on one target.
- [`EFFECTS_STREAM`][osrlib.core.effects.EFFECTS_STREAM]: the name of the random-number stream that `attach` and
  `advance` draw from.
- [`Condition`][osrlib.core.effects.Condition] and [`has_condition`][osrlib.core.effects.has_condition]: the named
  states a creature can be in, and the check for one.
- [`grant_condition`][osrlib.core.effects.grant_condition], [`remove_condition`][osrlib.core.effects.remove_condition],
  and [`kill`][osrlib.core.effects.kill]: put on or take off a condition that doesn't come from a timed effect.
- [`ActiveCondition`][osrlib.core.effects.ActiveCondition] and [`ActiveModifier`][osrlib.core.effects.ActiveModifier]:
  the records of a condition and a modifier on a creature.
- [`modifier_total`][osrlib.core.effects.modifier_total], [`modifier_values`][osrlib.core.effects.modifier_values],
  [`modifier_dice`][osrlib.core.effects.modifier_dice], and [`has_modifier`][osrlib.core.effects.has_modifier]: take
  a creature and a modifier kind, and return the adjustment that applies.

A condition is a named state a creature is in, like `asleep` or `petrified`. Each creature has a `conditions`
tuple of [`ActiveCondition`][osrlib.core.effects.ActiveCondition] records and a `stat_modifiers` tuple of
[`ActiveModifier`][osrlib.core.effects.ActiveModifier] records. A serialized creature therefore shows what is
wrong with it without the ledger beside it. The combat functions read both tuples through
[`has_condition`][osrlib.core.effects.has_condition] and the modifier functions.

The ledger writes a creature's conditions and modifiers when an effect attaches, and takes them back when the
effect ends. Only [`grant_condition`][osrlib.core.effects.grant_condition],
[`remove_condition`][osrlib.core.effects.remove_condition], and [`kill`][osrlib.core.effects.kill] write them
apart from the ledger, for the states that don't come from a timed effect. Use those functions rather than
assigning to `creature.conditions`. A condition you assign yourself has no effect behind it, so the ledger never
takes it back.

Each round that [`EffectsLedger.advance`][osrlib.core.effects.EffectsLedger.advance] passes through resolves in
a fixed order. First, the ledger suspends the effects on a creature that another effect has petrified. Then
expirations resolve, and then ticks. Within each phase, effects resolve in attachment order, and effect id breaks
a tie. A suspended effect neither ticks nor ages while the stone lasts, so an adventurer who was poisoned before
being turned to stone is still poisoned after *stone to flesh*.

The random draws for effects, like rolled durations, charm saves, splash damage, and a troll's revival
countdown, come from the stream named by [`EFFECTS_STREAM`][osrlib.core.effects.EFFECTS_STREAM]. An extra
draw in combat therefore never shifts an effect's roll.

The `target` parameters in this module take a [`Creature`][osrlib.core.creature.Creature], the protocol for the
hit points, conditions, and stat modifiers these calls read. A [`Character`][osrlib.core.character.Character]
and a [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy it. A `registry` is a mapping from
entity id to those creatures, like `{"monster-0001": goblin}`. A session builds one with
[`GameSession.registry`][osrlib.crawl.session.GameSession.registry].

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
from osrlib.core.creature import Creature
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
"""The name of the random-number stream that effects draw from, for rolled durations, ticks, and expiry outcomes.

Build an [`RngStreams`][osrlib.core.rng.RngStreams] from your master seed and pass `streams.get(EFFECTS_STREAM)`
wherever [`EffectsLedger.attach`][osrlib.core.effects.EffectsLedger.attach] and
[`EffectsLedger.advance`][osrlib.core.effects.EffectsLedger.advance] take a `stream`. A
[`GameSession`][osrlib.crawl.session.GameSession] does this for you. Each subsystem draws from a separate named
stream, so an extra attack roll never changes the round on which a charmed creature saves and breaks free.
"""

_ROUNDS_PER_UNIT: dict[TimeUnit, int] = {
    TimeUnit.ROUND: 1,
    TimeUnit.TURN: ROUNDS_PER_TURN,
    TimeUnit.DAY: ROUNDS_PER_DAY,
}


class Condition(StrEnum):
    """The closed set of named states a creature can be in, like asleep, charmed, or dead.

    Check whether a creature has a condition with [`has_condition`][osrlib.core.effects.has_condition]. To put
    one on a creature for a while, set it as an [`EffectDefinition`][osrlib.core.effects.EffectDefinition]'s
    `condition` and attach the definition with [`EffectsLedger.attach`][osrlib.core.effects.EffectsLedger.attach].
    The ledger then takes the condition away when the duration runs out. Use
    [`grant_condition`][osrlib.core.effects.grant_condition] only for a state that doesn't come from a timed effect.

    Some members change rolls and actions in [`osrlib.core.combat`][osrlib.core.combat] and
    [`osrlib.core.spells`][osrlib.core.spells]. Others matter only to the crawl procedures in
    [`osrlib.crawl`][osrlib.crawl] or to your own code. Each member's description lists where osrlib reads it, so
    you know whether granting it changes a roll or only what your interface shows.

    Immunity applies to every member. A monster whose template lists a condition in its defenses'
    `condition_immunities` never takes that condition, whether you call
    [`grant_condition`][osrlib.core.effects.grant_condition] or attach an effect that brings it. So even when no
    other core rule reads a member, immunity limits which monsters a spell can affect.

    The values are the lowercase strings, like `"asleep"`, and creatures and saved games contain them. A value
    changes only with a `schema_version` bump.
    """

    PARALYSED = "paralysed"
    """Frozen in place. The creature can't attack, cast, or move. It counts as incapacitated for the morale check
    that [`morale_triggers`][osrlib.core.combat.morale_triggers] raises when half a side is out of the fight. A
    melee attack against it hits automatically. *Cure light wounds* cures it."""

    ASLEEP = "asleep"
    """Unconscious. Like `paralysed`, the creature can't attack, cast, or move, it counts as incapacitated for
    morale, and a melee attack against it hits automatically. A melee hit with a bladed weapon also kills the
    sleeper outright, with no damage roll. *Cure light wounds* doesn't cure it."""

    BLIND = "blind"
    """Unable to see. [`validate_attack`][osrlib.core.combat.validate_attack] rejects the creature's attacks with
    `combat.attack.attacker_blind`. [`resolve_gaze`][osrlib.core.combat.resolve_gaze] skips the creature, because it
    can't see a gaze. In a battle in a [`GameSession`][osrlib.crawl.session.GameSession], a creature that a spitting
    cobra's spit hits saves versus death, and on a failed save it's blind permanently."""

    CHARMED = "charmed"
    """Under a charm. The monsters that can't be charmed, the undead and the golems among them, have it in
    their `condition_immunities`, so a charm aimed at one of those has no effect. You decide how the charmed
    creature obeys. The recurring save that can end the charm is the effect's `charm_resave` tick."""

    PETRIFIED = "petrified"
    """Turned to stone. The creature can't attack, cast, or move. A melee attack on it hits automatically, and it
    counts as incapacitated for morale. The [`EffectsLedger`][osrlib.core.effects.EffectsLedger] suspends its other
    effects, so they neither tick nor age until the stone is undone. Stone is not death. *Stone to flesh* cures it,
    and the creature continues from where it was."""

    DISEASED = "diseased"
    """Sick with a disease. Magical healing has no effect on it.
    [`natural_healing`][osrlib.core.combat.natural_healing] heals it on the slower schedule the effect sets, or not
    at all when you pass no ledger. *Cure disease* cures it."""

    EXHAUSTED = "exhausted"
    """Spent from running a whole pursuit. The encounter procedure in [`osrlib.crawl.encounter`][osrlib.crawl.encounter]
    attaches it to every living party member who escapes a pursuit that ran its full length. The penalties
    come from the effect's modifiers, not from the condition. Apart from immunity, no core rule reads it, and
    your interface can show it."""

    LYCANTHROPY_INCUBATION = "lycanthropy_incubation"
    """Infected by a lycanthrope's bite and not yet transformed. Nothing in osrlib grants it, and apart from
    immunity no core rule reads it. You run the transformation yourself."""

    AVERTED_EYES = "averted_eyes"
    """Fighting with eyes turned away from a gaze attack. [`resolve_gaze`][osrlib.core.combat.resolve_gaze] skips
    the creature. Pass the attack penalty for fighting blind yourself, in the attack context. A battle in a
    [`GameSession`][osrlib.crawl.session.GameSession] does this for you. It grants the condition for one round to a
    member whose [`gaze_defense`][osrlib.crawl.commands.BattleDeclaration.gaze_defense] is `avert_eyes`, and applies
    the penalty and the gazer's bonus."""

    POISONED = "poisoned"
    """Poisoned. The monsters that can't be poisoned, the undead and the cave locust among them, have it in
    their `condition_immunities`, so a poison aimed at one of those has no effect. *Neutralize poison* cures
    it, and can bring back a character who died of poison within the last ten rounds. The effect does the
    killing, not the condition. A poison that kills has an `expiry` of `"death"`, which kills the bearer when
    the effect's duration runs out."""

    DEAD = "dead"
    """Killed. [`kill`][osrlib.core.effects.kill] grants it, not an effect, so its `effect_id` is `None`. A dead
    creature can't act or be healed, and the battle procedure in [`osrlib.crawl.battle`][osrlib.crawl.battle]
    leaves it out when it picks targets. A spell that removes the condition, like *raise dead*, brings the
    creature back, and so does a troll's regeneration."""

    SILENCED = "silenced"
    """Unable to speak. [`validate_cast`][osrlib.core.spells.validate_cast] rejects the creature's casting with
    `magic.cast.caster_incapacitated`."""

    ENTANGLED = "entangled"
    """Held fast, as by a *web*. [`cannot_move`][osrlib.core.combat.cannot_move] reports True, and the creature
    can still attack and cast."""

    AFRAID = "afraid"
    """Panicked by a fear effect. *Remove fear* cures it. When the fear is magical, the subject first saves versus
    spells at +1 for each level of the curing caster, and keeps the fear if the save fails. The battle procedure in
    [`osrlib.crawl.battle`][osrlib.crawl.battle] counts a monster group as routed when every living member is
    afraid or `turned`."""

    FEEBLEMINDED = "feebleminded"
    """Unable to think clearly enough to cast. [`validate_cast`][osrlib.core.spells.validate_cast] rejects the
    creature's casting with `magic.cast.caster_incapacitated`."""

    INVISIBLE = "invisible"
    """Unseen. Apart from immunity, no core rule reads it. The battle procedure in
    [`osrlib.crawl.battle`][osrlib.crawl.battle] leaves the creature out of the ranks an enemy picks targets
    from, and rejects a spell declared at it with `battle.declaration.invisible_target`."""

    TURNED = "turned"
    """Driven off by a cleric's turning. [`turn_undead`][osrlib.core.spells.turn_undead] attaches the effect
    that grants it. Apart from immunity, no core rule reads it. The battle and encounter procedures in
    [`osrlib.crawl.battle`][osrlib.crawl.battle] and [`osrlib.crawl.encounter`][osrlib.crawl.encounter] treat
    the creature as fleeing."""

    CONFUSED = "confused"
    """Acting at random. Apart from immunity, no core rule reads it. The battle procedure in
    [`osrlib.crawl.battle`][osrlib.crawl.battle] chooses the creature's action, so you don't."""

    WEAKENED = "weakened"
    """Drained of strength. The creature can't attack or cast, and no healing restores its hit points. *Raise
    dead* leaves the raised character weakened for two weeks."""


class ActiveCondition(BaseModel):
    """One condition a creature currently has, paired with the effect that granted it.

    You read these from a creature's `conditions` tuple rather than building them.
    [`EffectsLedger.attach`][osrlib.core.effects.EffectsLedger.attach] and
    [`grant_condition`][osrlib.core.effects.grant_condition] add them. To ask whether a creature has a condition
    from any effect, call [`has_condition`][osrlib.core.effects.has_condition] instead of scanning the tuple.

    Each record contains the id of the effect that granted it, so two effects can grant the same condition, and
    ending one effect removes only the record that effect granted. A creature charmed twice has two records, and
    releasing one charm leaves the other in place. Records compare by value, and a creature never has the same
    condition from the same effect twice.

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
    it back. `None` marks a condition that doesn't come from a timed effect. In osrlib, that's `dead`."""


def _int_param(params: Mapping[str, Any], key: str, default: int = 0) -> int:
    """Read an integer param. The params are schema-validated, but the type checker can't narrow them by key."""
    return int(params.get(key, default))


def has_condition(target: Creature, condition: Condition) -> bool:
    """Return whether a creature currently has a condition.

    The combat functions make the same call. Use it wherever your code asks "is this creature asleep" instead of
    scanning the creature's `conditions` tuple, which can contain the same condition more than once, from different
    effects.

    It doesn't report which effect granted the condition. When you need that, read the creature's `conditions`
    tuple of [`ActiveCondition`][osrlib.core.effects.ActiveCondition] records directly.

    Args:
        target: The [`Creature`][osrlib.core.creature.Creature] to check, which a
            [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy.
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


def grant_condition(target: Creature, condition: Condition, effect_id: str | None) -> list[Event]:
    """Put a condition on a creature and return the event that reports it.

    Call this for a state that doesn't come from a timed effect, the way [`kill`][osrlib.core.effects.kill] does
    for `dead`. When the state has a duration, set the condition on an
    [`EffectDefinition`][osrlib.core.effects.EffectDefinition] and attach it with
    [`EffectsLedger.attach`][osrlib.core.effects.EffectsLedger.attach] instead. The ledger then takes the
    condition back when the duration runs out. A condition granted here stays until you call
    [`remove_condition`][osrlib.core.effects.remove_condition] with the same `effect_id`.

    The call replaces the creature's `conditions` tuple on the object you pass, so pass the creature that is in
    play, not a copy. Add the returned events to your event log.

    In two cases, the call grants nothing and returns no events. A monster whose template lists the condition in its
    defenses' `condition_immunities` is never affected, which is why a skeleton can't be put to sleep. A second
    grant of the same condition with the same `effect_id` changes nothing, because the creature already has that
    record.

    Args:
        target: The [`Creature`][osrlib.core.creature.Creature] to affect, which a
            [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy. The call replaces its
            `conditions` tuple in place.
        condition: The condition to grant.
        effect_id: The id of the effect that grants the condition and will take it back, or `None` for a state
            that doesn't come from a timed effect.

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


def remove_condition(target: Creature, condition: Condition, effect_id: str | None) -> list[Event]:
    """Take back the condition one effect granted, and return the event that reports it.

    This undoes [`grant_condition`][osrlib.core.effects.grant_condition]. It removes only the record that matches
    both the condition and the `effect_id`, so pass the `effect_id` you granted the condition with, or the call
    removes nothing. A creature charmed by two effects keeps the second charm after you remove the first.

    Call this for conditions you granted yourself. A condition from an attached effect comes off when the effect
    expires, or when you end the effect with [`EffectsLedger.release`][osrlib.core.effects.EffectsLedger.release].

    Args:
        target: The [`Creature`][osrlib.core.creature.Creature] to affect, which a
            [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy. The call replaces its
            `conditions` tuple in place.
        condition: The condition to take back.
        effect_id: The `effect_id` the condition was granted with, or `None` for a state that doesn't come from an
            effect.

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
    """Remove the stat modifiers that the effect `effect_id` granted."""
    if not hasattr(target, "stat_modifiers"):
        return
    remaining = tuple(modifier for modifier in target.stat_modifiers if modifier.effect_id != effect_id)
    if len(remaining) != len(target.stat_modifiers):
        target.stat_modifiers = remaining


def kill(target: Creature, *, permanent: bool = False) -> list[Event]:
    """Kill a creature outright: hit points to zero, the `dead` condition, and the death events.

    In B/X a creature dies the moment it drops to zero hit points or fewer.
    [`deal_damage`][osrlib.core.combat.deal_damage] calls this for you when damage does that. Call it yourself
    for a death that doesn't come from damage, like a failed save against *finger of death*, or a creature you
    remove from play by decision.

    The `dead` condition granted here has no `effect_id`, because no effect grants it. Calling `kill` on a creature
    that is already dead returns no events and changes nothing.

    Args:
        target: The [`Creature`][osrlib.core.creature.Creature] to kill, which a
            [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy. `kill` writes its
            `current_hp` and `conditions` in place.
        permanent: True when a regenerating creature can't come back, which for a troll means its
            non-regenerable damage has reached its maximum hit points. It changes the death event's code from
            `combat.death.died` to `combat.death.permanent`, not the outcome.

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

These are the only values a [`ModifierSpec`][osrlib.core.effects.ModifierSpec] accepts for its `kind`. A
`ModifierSpec` with any other name raises pydantic's `ValidationError` when you construct it. The combat code
looks up each kind by name while it resolves a roll, and a kind it didn't look up would change nothing. When you author
content of your own, express each adjustment with a kind in this set.

In the list below, the bearer is the creature the modifier is on. What each kind does:

- `attack_bonus` adjusts the bearer's attack rolls, and `damage_bonus` its damage rolls.
- `attack_penalty_of_attackers` adds its value to the attack rolls of anyone attacking the bearer. A ward like
  *protection from evil* uses a negative value.
- `save_bonus` adjusts the bearer's saving throws, narrowed by `element`, `save_categories`, or
  `versus_other_alignment`.
- `morale_bonus` adjusts the bearer's side's morale checks through
  [`morale_modifier`][osrlib.core.combat.morale_modifier].
- `ac_bonus` improves the bearer's armour class by its value.
- `ac_set` replaces the bearer's armour class against attacks that aren't missiles, when the set value is
  better. `ac_set_vs_missile` does the same against missile attacks, and against a missile only it counts.
  That's how *shield* gives one armour class against missiles and another against everything else.
- `damage_reduction_per_die` takes points off incoming damage, one per die rolled, for the named `element`.
- `damage_multiplier` multiplies the bearer's weapon damage, and `melee_damage_multiplier` its melee damage,
  after the flat bonuses are added. A value of 1 or less changes nothing.
- `weapon_damage_dice_bonus` adds the modifier's `dice` to the bearer's weapon damage.
- `strength_set` replaces the strength score that the bearer's melee modifier comes from. That's how
  Gauntlets of Ogre Power give a fixed 18 and a Ring of Weakness a fixed 3.
- `counts_as_magical` makes the bearer's attacks count as magical, so they can harm a monster that only magic
  can hurt. `missile_immunity_nonmagical` makes the bearer immune to non-magical missiles.
- `magical_healing_half` halves the hit points magical healing restores to the bearer.
"""


class ModifierSpec(BaseModel):
    """One adjustment to a combat statistic, granted by an effect or by an equipped magic item.

    You write these when you author a spell, a magic item, or an effect of your own. Put them in an
    [`EffectDefinition`][osrlib.core.effects.EffectDefinition]'s `modifiers`. Attaching that definition turns
    each spec into an [`ActiveModifier`][osrlib.core.effects.ActiveModifier] in the creature's `stat_modifiers`,
    and the combat functions read them back through [`modifier_total`][osrlib.core.effects.modifier_total] and the
    other modifier functions. A magic item template lists specs too. The combat functions read an equipped item's
    specs through
    [`equipped_item_modifiers`][osrlib.core.items.equipped_item_modifiers] instead, and they never appear in
    `stat_modifiers`. A spec is frozen, so the same one can be in several definitions.

    The scope fields, `element`, `versus_other_alignment`, `save_categories`, and `melee_only`, narrow when the
    modifier counts. Leave them at their defaults and the modifier applies to every roll of its kind.

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
    """Which statistic the modifier adjusts, one of [`MODIFIER_KINDS`][osrlib.core.effects.MODIFIER_KINDS]. Any
    other value raises pydantic's `ValidationError`."""

    value: int = 0
    """The signed adjustment, like +1 for *bless*'s attack bonus or -1 for *protection from evil*'s penalty on
    attackers. For `ac_set` and `ac_set_vs_missile` it's the armour class to set, and for `strength_set` the
    strength score. Leave it at 0 for a kind that uses `dice` or acts as a flag."""

    dice: str | None = None
    """A dice expression rolled instead of adding `value`, for a kind that grants dice, like *striking*'s `"1d6"`
    of extra weapon damage. osrlib checks it with [`parse`][osrlib.core.dice.parse], so a malformed
    expression raises [`ContentValidationError`][osrlib.errors.ContentValidationError] when you build the
    `ModifierSpec`, not when the combat code rolls it."""

    element: str | None = None
    """Narrows the modifier to one damage or save element, like `"fire"` for *resist fire*. The modifier counts
    only for a roll with that same element. `None` means every element."""

    versus_other_alignment: bool = False
    """True narrows the modifier to rolls against creatures of a different alignment, which is how *protection
    from evil* works. The modifier then counts only when the call that reads it passes `versus_differs=True`."""

    save_categories: tuple[str, ...] = ()
    """Narrows a save bonus to the named saving throw categories. A Displacer Cloak, for example, covers
    `"paralysis"`, `"wands"`, and `"spells"` but not the others. Empty means every category."""

    melee_only: bool = False
    """True narrows the modifier to melee attacks. That's how the Displacer Cloak's -2 on attackers leaves
    missile attacks alone. The modifier then counts only when the call that reads it passes `melee=True`."""

    from_item: bool = False
    """True marks the modifier as coming from a magic item rather than a spell. Such a modifier is exempt from
    the rule that only the largest spell bonus and the largest spell penalty count:
    [`modifier_total`][osrlib.core.effects.modifier_total] adds every item modifier to the capped spell total.
    Set it on the effects of potions and scrolls as well as worn items, because the rule covers magic items in
    general."""

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

    These are the entries of a creature's `stat_modifiers` tuple. Attaching an effect turns each
    [`ModifierSpec`][osrlib.core.effects.ModifierSpec] in its definition into one of these. When the effect
    expires or is released, the ledger removes them again, the same way it removes conditions. Nothing else in
    osrlib writes the tuple, so each modifier on a creature belongs to an effect on the ledger.

    Read them through [`modifier_total`][osrlib.core.effects.modifier_total] rather than scanning the tuple,
    because it applies the scope fields and the rule that spell bonuses don't add up.
    [`modifier_values`][osrlib.core.effects.modifier_values], [`modifier_dice`][osrlib.core.effects.modifier_dice],
    and [`has_modifier`][osrlib.core.effects.has_modifier] cover the other ways the combat code reads them.
    """

    effect_id: str
    """The id of the [`ActiveEffect`][osrlib.core.effects.ActiveEffect] that granted the modifier and will take
    it back."""


def modifier_values(
    target: Creature,
    kind: str,
    *,
    element: str | None = None,
    versus_differs: bool = False,
    save_category: str | None = None,
    melee: bool = False,
) -> list[int]:
    """Return every modifier value of one kind that applies to the situation you describe.

    Use this when you need the separate values rather than one number. The armour class code, for example, reads
    the `ac_set` values one at a time and uses the best. When you want one number to add to a roll, call
    [`modifier_total`][osrlib.core.effects.modifier_total] instead, which also applies the rule that spell bonuses
    don't add up.

    The keyword arguments describe the roll, and the function leaves out a modifier narrowed to something you
    don't pass.
    A modifier with an `element` counts only when you pass that element. One with `versus_other_alignment`
    counts only when you pass `versus_differs=True`. A save bonus with `save_categories` counts only when you pass
    one of its categories, and a `melee_only` modifier only when you pass `melee=True`.

    It reads only the creature's `stat_modifiers`, which come from attached effects. An
    equipped magic item's modifiers aren't there: get those from
    [`equipped_item_modifiers`][osrlib.core.items.equipped_item_modifiers] and add them yourself.

    Args:
        target: The [`Creature`][osrlib.core.creature.Creature] to read modifiers from, which a
            [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy.
        kind: The statistic to look for, one of [`MODIFIER_KINDS`][osrlib.core.effects.MODIFIER_KINDS].
        element: The damage or save element of the roll, like `"fire"`. Leave it None for a roll with no element.
        versus_differs: True when the other creature in the roll has a different alignment from the target.
        save_category: The saving throw category of the roll, a
            [`SaveCategory`][osrlib.core.combat.SaveCategory] value like `"spells"`. Leave it None outside a
            saving throw.
        melee: True when the roll is for a melee attack.

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
    target: Creature,
    kind: str,
    *,
    element: str | None = None,
    versus_differs: bool = False,
    save_category: str | None = None,
    melee: bool = False,
) -> int:
    """Return the one number to add to a roll for a creature's modifiers of one kind.

    The combat functions make this call, and it's the one to use when you resolve a roll of your own. It reads the same
    modifiers that [`modifier_values`][osrlib.core.effects.modifier_values] returns and combines them into one
    signed adjustment. Spells that affect the same statistic don't add up: only the largest bonus and the largest
    penalty count. Two *blesses* give +1, not +2, while a *bless* and a *blight* cancel out.

    The function adds every modifier marked `from_item` on top, outside that rule, because the rule covers spells and
    not magic items. The scope arguments work as they do for [`modifier_values`][osrlib.core.effects.modifier_values].

    It reads only the creature's `stat_modifiers`, which come from attached effects. An
    equipped magic item's modifiers aren't there: get those from
    [`equipped_item_modifiers`][osrlib.core.items.equipped_item_modifiers] and add them yourself.

    Args:
        target: The [`Creature`][osrlib.core.creature.Creature] to total modifiers for, which a
            [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy.
        kind: The statistic to total, one of [`MODIFIER_KINDS`][osrlib.core.effects.MODIFIER_KINDS].
        element: The damage or save element of the roll, like `"fire"`. Leave it None for a roll with no element.
        versus_differs: True when the other creature in the roll has a different alignment from the target.
        save_category: The saving throw category of the roll, a
            [`SaveCategory`][osrlib.core.combat.SaveCategory] value like `"spells"`. Leave it None outside a
            saving throw.
        melee: True when the roll is for a melee attack.

    Returns:
        The signed adjustment to add to the roll, or 0 when nothing applies.

    Examples:
        Two blessings and one blight on the same goblin count one point of bonus and one point of penalty, which
        cancel out:

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


def modifier_dice(target: Creature, kind: str) -> str | None:
    """Return the dice expression of a creature's dice-valued modifier of one kind.

    A few modifiers grant dice instead of a flat number, like *striking*'s extra `"1d6"` of weapon damage.
    Call this to find them, then roll the expression yourself with [`roll`][osrlib.core.dice.roll]. For a flat
    adjustment, call [`modifier_total`][osrlib.core.effects.modifier_total] instead.

    It returns only the first matching modifier, in the order the effects were attached. That's the rule that
    spells don't add up, applied to dice: a creature under two *strikings* rolls one extra die, not two. It
    ignores the scope fields and modifiers that have no `dice`, and it reads only the creature's `stat_modifiers`.

    Args:
        target: The [`Creature`][osrlib.core.creature.Creature] to read modifiers from, which a
            [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy.
        kind: The statistic to look for, one of [`MODIFIER_KINDS`][osrlib.core.effects.MODIFIER_KINDS].

    Returns:
        The dice expression, in the notation [`parse`][osrlib.core.dice.parse] accepts, or `None` when the
        creature has no modifier of that kind with `dice`.

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


def has_modifier(target: Creature, kind: str) -> bool:
    """Return whether a creature has any modifier of one kind.

    Use this for the kinds that act as flags rather than numbers, where having the modifier at all is the
    rule: `counts_as_magical`, `missile_immunity_nonmagical`, and `magical_healing_half`. For a kind that uses
    a number, call [`modifier_total`][osrlib.core.effects.modifier_total]. A total of 0 from it can mean either
    that the creature has no modifier of the kind or that its modifiers cancel out.

    It ignores the scope fields, so a modifier narrowed to one element still counts here. When you need the
    scope fields applied, call [`modifier_values`][osrlib.core.effects.modifier_values]. It reads only the
    creature's `stat_modifiers`, not an equipped magic item's modifiers.

    Args:
        target: The [`Creature`][osrlib.core.creature.Creature] to read modifiers from, which a
            [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy.
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

    Write one of these for each spell, ability, or hazard you want to put on a creature or a place. Then pass it
    to [`EffectsLedger.attach`][osrlib.core.effects.EffectsLedger.attach] with the entity id or location it applies
    to. Attaching creates a running [`ActiveEffect`][osrlib.core.effects.ActiveEffect] from the blueprint. The
    blueprint is frozen, so one definition serves every target you attach it to. You don't write definitions
    for the spells in [`load_spells`][osrlib.data.load_spells]:
    [`cast_spell`][osrlib.core.spells.cast_spell] builds and attaches those for you. Write your own when you
    author something new, or run the rules without a session.

    Give the effect a duration with `duration_unit` and either `duration_amount` or `duration_dice`. Leave the
    unit out and the effect runs until you release it. Set `permanent` for an effect that only magic undoes.

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
    """The effect's name, like `"sleep"` or `"regeneration"`. The model accepts any non-empty string. The ledger
    compares kinds on the same target for `stacking`, and
    [`EffectsLedger.active_on`][osrlib.core.effects.EffectsLedger.active_on] filters on the kind, so give each kind of
    effect one name and use it everywhere."""

    duration_unit: TimeUnit | None = None
    """The unit the duration is counted in: rounds, turns, or days. `None` means the effect has no duration and
    runs until you release it."""

    duration_amount: int | None = None
    """A fixed duration, counted in `duration_unit`. Use this or `duration_dice`, not both."""

    duration_dice: str | None = None
    """A dice expression rolled once at attach time to set the duration, like `"2d6"`. The roll comes from the
    effects stream, so attaching a definition with dice and no `stream` raises `ValueError`. osrlib checks the
    expression with [`parse`][osrlib.core.dice.parse] when you construct the definition, and a malformed one raises
    [`ContentValidationError`][osrlib.errors.ContentValidationError]."""

    permanent: bool = False
    """True means the effect never expires on its own, and the ledger ignores any duration. That's how
    petrification lasts until someone casts *stone to flesh*. Set `dispellable` to choose whether *dispel magic*
    can end the effect."""

    tick: str | None = None
    """The name of a periodic behavior the ledger runs while the effect lasts, or `None` for none.
    `"regeneration"` heals the bearer and can bring a troll back from death. `"charm_resave"` rolls the bearer's
    saving throw versus spells and releases the effect when it passes. Any other name raises `ValueError` at the
    first tick."""

    tick_interval_rounds: int = Field(default=1, ge=1)
    """How many rounds pass between ticks. The default of 1 ticks every round. For a charm,
    [`cast_spell`][osrlib.core.spells.cast_spell] sets it from the subject's INT: every 30 days for INT 8 or
    lower, every 7 days for INT 9 to 12, and every day for INT 13 or higher. A monster has no INT score and
    saves every 7 days."""

    stacking: Literal["stack", "refresh", "ignore"] = "stack"
    """What happens when you attach this kind to a target that already has an effect of the same kind.
    `"stack"` adds a second effect. `"refresh"` restarts the existing effect's duration and adds nothing.
    `"ignore"` changes nothing, and the attach returns no effect."""

    expiry: str | None = None
    """The name of an outcome the ledger resolves when the duration runs out, or `None` for none.

    The outcome adds to the ordinary ending and doesn't replace it. The ledger removes the effect, emits the
    [`EffectExpiredEvent`][osrlib.core.events.EffectExpiredEvent], and takes off the condition and modifiers.
    Then it runs the outcome on the bearer:

    - `"death"` kills the bearer, which is how a delayed poison works.
    - `"splash_damage"` deals the second round of damage from burning oil or holy water.
    - `"weakness_strength_set"` attaches the Ring of Weakness curse when its onset ends. Pass an `allocator` to
      the ledger's `advance` for it.

    Any other name raises `ValueError` at expiry. An effect on a location has no bearer, so its outcome never
    runs."""

    condition: Condition | None = None
    """A [`Condition`][osrlib.core.effects.Condition] granted when the effect attaches and taken back when it
    expires or is released. When the target is immune to the condition, nothing attaches, and the attach
    returns no effect."""

    modifiers: tuple[ModifierSpec, ...] = ()
    """The [`ModifierSpec`][osrlib.core.effects.ModifierSpec] adjustments granted while the effect lasts and
    taken back when it ends."""

    dispellable: bool = False
    """True marks the effect as one *dispel magic* can end. Everything
    [`cast_spell`][osrlib.core.spells.cast_spell] attaches is dispellable, permanent effects included. What a
    monster inflicts isn't."""

    params: dict[str, int | str | bool | tuple[int | str, ...]] = {}
    """Any other values the effect's tick, its expiry outcome, or other code that reads the effect needs. The keys
    osrlib reads include these:

    - For the `"regeneration"` tick: `per_round`, `delay_rounds`, `revive`, and `while_alive`, as
      [`regeneration_definition`][osrlib.core.effects.regeneration_definition] describes them.
    - For the `"splash_damage"` outcome: `dice`, the damage roll, `element`, and `keys`, the damage keys.
    - For the `"weakness_strength_set"` outcome: `strength_set`, the strength score the curse sets.
    - For [`natural_healing`][osrlib.core.combat.natural_healing]: `healing_rest_days`, the number of rest days
      for each natural heal.

    osrlib ignores any key it doesn't read, so you can store your own values here."""

    @field_validator("duration_dice")
    @classmethod
    def _dice_must_parse(cls, value: str | None) -> str | None:
        if value is not None:
            parse(value)
        return value


class ActiveEffect(BaseModel):
    """One effect currently running on a creature or a location.

    [`EffectsLedger.attach`][osrlib.core.effects.EffectsLedger.attach] creates one, adds it to the ledger's
    `effects` list, and returns it. [`EffectsLedger.active_on`][osrlib.core.effects.EffectsLedger.active_on] finds
    it again. Read it to see how long the effect has left or what it is tracking. Pass its `effect_id` to
    [`EffectsLedger.release`][osrlib.core.effects.EffectsLedger.release] to end it early. You don't build these
    yourself. When [`load_game`][osrlib.persistence.load_game] restores a ledger from a save, it rebuilds them.
    """

    model_config = ConfigDict(validate_assignment=True)

    effect_id: str
    """The effect's id, like `effect-0001`, from the [`IdAllocator`][osrlib.core.monsters.IdAllocator] you
    passed to `attach`. The conditions and modifiers the effect granted contain this id, which is how the ledger
    finds them when the effect ends."""

    definition: EffectDefinition
    """The [`EffectDefinition`][osrlib.core.effects.EffectDefinition] the effect was attached from. The ledger
    ticks and expires the effect from this copy, so a saved ledger needs nothing else."""

    target_ref: str
    """What the effect is on. It's an entity id for a creature, or a location string, like one from
    [`cell_ref`][osrlib.crawl.dungeon.cell_ref], for an effect that stays in a place, like a pool of burning oil.
    A location string isn't a key in the registry, so an effect on a location grants no conditions or
    modifiers."""

    attached_round: int = Field(ge=0)
    """The round the effect was attached on, counted from the start of the game clock. The effect ticks every
    `tick_interval_rounds` from this round. When several effects resolve in one round, they go in order of this
    value first, then of `effect_id`."""

    expires_round: int | None = None
    """The round the effect expires on, counted from the start of the game clock, or `None` when it has no
    duration or is permanent. While the bearer is petrified by another effect, this moves forward one round for
    each round that passes. A suspended effect therefore has the same time left when the stone is undone."""

    caster_level: int | None = None
    """The level of the caster whose spell attached this effect, or `None` when `attach` got no `caster_level`.
    When *dispel magic* tries to end the effect, osrlib rolls against it to decide whether the effect survives."""

    state: dict[str, int] = {}
    """The effect's running counters, like `revive_at`, the round a dead troll gets up on, or `rest_days`, the
    rest days counted so far for a slowed-healing effect. The ledger and
    [`natural_healing`][osrlib.core.combat.natural_healing] write it. Read it to show a countdown."""


# An invariant test asserts that ledger effects and the conditions and stat modifiers
# they grant never fall out of step: every mutation must flow through this class's
# helpers, or through kill() for `dead`. Keep it that way when extending the engine.
class EffectsLedger(BaseModel):
    """The object that contains every running effect and resolves each one as the game clock moves.

    One ledger covers a whole game. A [`GameSession`][osrlib.crawl.session.GameSession] creates one and keeps it
    as `session.ledger` for the life of the session. When you run the rules without a session, construct one with
    `EffectsLedger()` and keep it beside your [`GameClock`][osrlib.core.clock.GameClock]. It's a pydantic model,
    so you save it with `model_dump` and restore it with `model_validate`, along with the clock and the creatures.

    Its methods:

    - [`attach`][osrlib.core.effects.EffectsLedger.attach] puts an effect on a target.
    - [`advance`][osrlib.core.effects.EffectsLedger.advance] moves the clock and resolves every expiry and tick
      in the time that passes.
    - [`release`][osrlib.core.effects.EffectsLedger.release] ends an effect early.
    - [`active_on`][osrlib.core.effects.EffectsLedger.active_on] lists the effects on one target.

    Only `advance` ends an effect when its time runs out. If you move the clock any other way, effects that should
    have expired stay in place, so always move the clock through the ledger.

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
    """Every running [`ActiveEffect`][osrlib.core.effects.ActiveEffect], in the order they were attached. Read it
    to see everything running at once. To find the effects on one target, call
    [`active_on`][osrlib.core.effects.EffectsLedger.active_on]. `attach`, `release`, and `advance` update the
    list."""

    def active_on(self, target_ref: str, kind: str | None = None) -> list[ActiveEffect]:
        """Return the effects currently running on one target.

        Use this for questions that [`has_condition`][osrlib.core.effects.has_condition] and the modifier
        functions can't answer, like whether a *mirror image* is still up or how many rounds a light source has
        left. Pass `kind` to get only the effects of that kind. The list is empty when none match.

        Args:
            target_ref: The entity id or location string the effects are attached to.
            kind: An [`EffectDefinition`][osrlib.core.effects.EffectDefinition] `kind` to narrow to. Leave it out
                for everything on the target.

        Returns:
            The matching effects, in the order they were attached. The list is new, but the effects in it are the
            ledger's objects, so a change to one changes what the ledger runs.

        Examples:
            ```python
            from osrlib.core.clock import GameClock, TimeUnit
            from osrlib.core.effects import Condition, EffectDefinition, EffectsLedger
            from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, spawn_monster
            from osrlib.core.rng import RngStreams
            from osrlib.data import load_monsters

            spawn = RngStreams(master_seed=3).get(MONSTER_SPAWN_STREAM)
            goblin = spawn_monster(load_monsters().get("goblin"), id="monster-0001", stream=spawn)
            registry = {"monster-0001": goblin}
            ledger, clock = EffectsLedger(), GameClock()
            sleep = EffectDefinition(
                kind="sleep",
                duration_unit=TimeUnit.TURN,
                duration_amount=4,
                condition=Condition.ASLEEP,
            )
            ledger.attach(sleep, "monster-0001", clock=clock, allocator=IdAllocator(), registry=registry)

            for effect in ledger.active_on("monster-0001", "sleep"):
                print(effect.effect_id, effect.expires_round - clock.rounds)
            # effect-0001 240
            ```
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

        Every effect starts here. Build an [`EffectDefinition`][osrlib.core.effects.EffectDefinition] and call
        this with the target's entity id. The ledger works out when the effect expires, grants the condition and
        modifiers it brings, and starts counting its ticks. Afterwards, move the clock with
        [`advance`][osrlib.core.effects.EffectsLedger.advance], which expires and ticks the effect.

        When a spell attaches the effect, [`cast_spell`][osrlib.core.spells.cast_spell] makes this call for you
        and returns the same events.

        The call can return `None` instead of an effect, so check before you use it. That happens in two cases.
        The definition's `stacking` is `"ignore"` and the target already has an effect of that kind, or the
        target's template lists the definition's condition in its `condition_immunities`. When `stacking` is
        `"refresh"` and the target already has an effect of that kind, you get the existing effect back with its
        duration restarted, and no events.

        Args:
            definition: The blueprint to attach.
            target_ref: The entity id of the creature, or the location string of the place, to attach to. For an
                id that isn't a key in `registry`, the call attaches the effect but grants no condition or
                modifiers.
            clock: The game clock. The duration and the tick count start from its current round. The call reads
                the clock and doesn't advance it.
            allocator: The [`IdAllocator`][osrlib.core.monsters.IdAllocator] that gives the effect its id. A session
                keeps one as `session.allocator`. Without a session, construct one with `IdAllocator()` and reuse it.
            registry: The creatures in play by entity id, so the attach can grant conditions and modifiers. Pass
                None, or leave the target out of it, and the effect attaches without granting anything.
            stream: The effects stream, `streams.get(EFFECTS_STREAM)`. Needed only when the definition has
                `duration_dice`.
            caster_level: The level of the caster, recorded on the effect for *dispel magic* to roll against.
                Leave it None for an effect that doesn't come from a caster.

        Returns:
            A pair of the attached effect and its events. The events are the
            [`EffectAttachedEvent`][osrlib.core.events.EffectAttachedEvent] and, when the definition brings a
            condition the target takes, the
            [`ConditionGainedEvent`][osrlib.core.events.ConditionGainedEvent]. The pair is `(None, [])` when
            nothing was attached, and on a refresh it's the existing effect with an empty event list.

        Raises:
            ValueError: If the definition has `duration_dice` and you passed no `stream`, or if it has a
                `duration_unit` with neither an amount nor dice.

        Examples:
            A *sleep* takes hold on a goblin, and a skeleton, which is immune to sleep, gets `None`:

            ```python
            from osrlib.core.clock import GameClock, TimeUnit
            from osrlib.core.effects import Condition, EffectDefinition, EffectsLedger
            from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, spawn_monster
            from osrlib.core.rng import RngStreams
            from osrlib.data import load_monsters

            spawn = RngStreams(master_seed=3).get(MONSTER_SPAWN_STREAM)
            monsters = load_monsters()
            goblin = spawn_monster(monsters.get("goblin"), id="monster-0001", stream=spawn)
            skeleton = spawn_monster(monsters.get("skeleton"), id="monster-0002", stream=spawn)
            registry = {"monster-0001": goblin, "monster-0002": skeleton}
            ledger, clock, allocator = EffectsLedger(), GameClock(), IdAllocator()
            sleep = EffectDefinition(
                kind="sleep",
                duration_unit=TimeUnit.TURN,
                duration_amount=4,
                condition=Condition.ASLEEP,
            )

            effect, events = ledger.attach(sleep, "monster-0001", clock=clock, allocator=allocator, registry=registry)
            print(effect.effect_id, effect.expires_round, [event.code for event in events])
            # effect-0001 240 ['effects.effect.attached', 'effects.condition.gained']

            print(ledger.attach(sleep, "monster-0002", clock=clock, allocator=allocator, registry=registry))
            # (None, [])
            ```
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

        Call this when something in the game cuts an effect short, like a *dispel magic*, a light source put out,
        or an invisible creature that attacks and loses its invisibility. The condition and the modifiers the
        effect granted come off with it.

        Find the id first with [`active_on`][osrlib.core.effects.EffectsLedger.active_on]. It returns a new list,
        so you can release each effect in it as you loop. Don't loop over `effects` itself while you release,
        because each release removes an entry from that list. You don't call this when time runs out:
        [`advance`][osrlib.core.effects.EffectsLedger.advance] expires the effect and emits an
        [`EffectExpiredEvent`][osrlib.core.events.EffectExpiredEvent] instead.

        Args:
            effect_id: The `effect_id` of the [`ActiveEffect`][osrlib.core.effects.ActiveEffect] to end.
            registry: The creatures in play by entity id, so the ledger can take back the condition and modifiers.
                Leave it out and the ledger drops the effect, but the condition and modifiers stay on the creature.

        Returns:
            The [`EffectReleasedEvent`][osrlib.core.events.EffectReleasedEvent] and, when the effect granted a
            condition to a creature in the registry, the
            [`ConditionRemovedEvent`][osrlib.core.events.ConditionRemovedEvent].

        Raises:
            ValueError: If the ledger has no effect with that id, usually because it already expired or was
                released. Nothing changes.

        Examples:
            A *dispel* that ends every effect on a goblin:

            ```python
            from osrlib.core.clock import GameClock, TimeUnit
            from osrlib.core.effects import Condition, EffectDefinition, EffectsLedger, has_condition
            from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, spawn_monster
            from osrlib.core.rng import RngStreams
            from osrlib.data import load_monsters

            spawn = RngStreams(master_seed=3).get(MONSTER_SPAWN_STREAM)
            goblin = spawn_monster(load_monsters().get("goblin"), id="monster-0001", stream=spawn)
            registry = {"monster-0001": goblin}
            ledger = EffectsLedger()
            sleep = EffectDefinition(
                kind="sleep",
                duration_unit=TimeUnit.TURN,
                duration_amount=4,
                condition=Condition.ASLEEP,
            )
            ledger.attach(sleep, "monster-0001", clock=GameClock(), allocator=IdAllocator(), registry=registry)

            for effect in ledger.active_on("monster-0001"):
                print([event.code for event in ledger.release(effect.effect_id, registry)])
            # ['effects.effect.released', 'effects.condition.removed']

            print(has_condition(goblin, Condition.ASLEEP), ledger.effects)
            # False []
            ```
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

        This call ends effects whose duration runs out and runs periodic ticks, like a troll's regeneration.
        Move the clock with this call rather than with [`GameClock.advance`][osrlib.core.clock.GameClock.advance]
        or by setting [`GameClock.rounds`][osrlib.core.clock.GameClock.rounds]. The clock records only the time, so
        moving it directly resolves no effects. A [`GameSession`][osrlib.crawl.session.GameSession] makes this call
        for you in [`advance_rounds`][osrlib.crawl.session.GameSession.advance_rounds] and
        [`advance_turns`][osrlib.crawl.session.GameSession.advance_turns].

        The call resolves every round in the span, one at a time, in the same order. First, it suspends the
        effects on a creature that another effect has petrified. A suspended effect neither ticks nor ages: its
        expiry moves forward one round for each round it is suspended. Then expirations resolve, and then ticks.
        Within each phase, effects go in attachment order, and effect id breaks a tie. The same span therefore
        always produces the same events in the same order.

        Args:
            clock: The game clock. The call advances it in place, so it shows the new time when the call returns.
            n: How many units to advance. The call resolves every round in the span, so a long span costs more:
                a day is thousands of rounds.
            unit: The unit `n` counts: rounds, turns, or days.
            registry: The creatures in play by entity id, so the call can write conditions, modifiers, and hit points.
                A plain dict works.
            stream: The effects stream, `streams.get(EFFECTS_STREAM)`, for the draws of ticks and expiry
                outcomes.
            allocator: The [`IdAllocator`][osrlib.core.monsters.IdAllocator] you pass to `attach`. Needed only for
                the `"weakness_strength_set"` expiry outcome, which attaches a follow-on effect.

        Returns:
            Every event the span produced, in the order it was produced. Add them to your event log.

        Raises:
            ValueError: If `n` is negative, if a tick or expiry name isn't one osrlib defines, or if the
                `"weakness_strength_set"` outcome runs and you passed no `allocator`.

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
            definition = regeneration_definition(template.ability("regeneration").params)
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

        The ledger rolls the re-save when the effect ticks, so the roll comes from the effects stream. `cast_spell`
        set the interval at attach time from the subject's INT band, in `tick_interval_rounds`.
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
                # The 2d6-round countdown starts from the round the killing damage
                # was dealt, which the instance records, and falls back to this
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

    A regenerating monster like a troll has a `regeneration` ability. Its params set how fast the monster heals
    and whether it comes back from death. Get them with `template.ability("regeneration").params`, pass them here,
    and attach the result to the monster with [`EffectsLedger.attach`][osrlib.core.effects.EffectsLedger.attach]
    when it enters play. From then on, [`advance`][osrlib.core.effects.EffectsLedger.advance] heals the monster in
    each round it resolves, while the monster is hurt.

    The definition has no duration, so the regeneration runs until you release it. Its `stacking` is
    `"ignore"`, so a second attach to the same monster changes nothing.

    Args:
        params: The ability's params. The tick reads these keys. `per_round` is how many hit points come back
            each round. `delay_rounds` is how many rounds must pass after the monster last took damage before it
            heals again. `revive` is a dice expression for how many rounds the monster lies dead before it gets
            back up. `while_alive` is True for a monster that heals only while it lives. The function copies
            other keys to the definition's `params`, and the tick doesn't read them.

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
