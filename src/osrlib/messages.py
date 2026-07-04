"""The default English message formatter.

[`format_message`][osrlib.messages.format_message] renders any event to a plain
English line — pure string templating keyed by the event's outcome-bearing `code`, no
I/O. It is a *total* function, pinned: unknown codes format to the code string itself
rather than raising, honoring "consumers must ignore unknown event types" for forward
compatibility (a test asserts every shipped code has a real template).

Templates reference entity IDs, not names — events carry structured facts and IDs
only. Front ends and narrators that want prose with names resolve IDs themselves and
localize freely; this formatter exists so a bare kernel transcript is readable.
"""

from collections.abc import Callable

from osrlib.core.events import (
    AttackRolledEvent,
    DamageAbsorbedEvent,
    DamageDealtEvent,
    Event,
    InitiativeRolledEvent,
    MoraleCheckedEvent,
    SavingThrowRolledEvent,
    SpellCastEvent,
    SpellsMemorizedEvent,
    UndeadTurnedEvent,
)

__all__ = [
    "format_message",
]


def _initiative(event: InitiativeRolledEvent) -> str:
    rolled = ", ".join(f"{entry.key} {entry.total}" for entry in event.entries)
    return f"Initiative ({event.mode}): {rolled}. Order: {', '.join(event.order)}."


def _attack_hit(event: AttackRolledEvent) -> str:
    natural = " (natural 20)" if event.natural == 20 else ""
    return (
        f"{event.attacker_id} hits {event.defender_id} with {event.attack_name}: "
        f"rolled {event.roll}{event.modifier:+d} = {event.total}, needing {event.required}{natural}."
    )


def _attack_missed(event: AttackRolledEvent) -> str:
    natural = " (natural 1)" if event.natural == 1 else ""
    return (
        f"{event.attacker_id} misses {event.defender_id} with {event.attack_name}: "
        f"rolled {event.roll}{event.modifier:+d} = {event.total}, needing {event.required}{natural}."
    )


def _attack_auto(event: AttackRolledEvent) -> str:
    return f"{event.attacker_id} strikes the helpless {event.defender_id} with {event.attack_name} — no roll needed."


def _damage(event: DamageDealtEvent) -> str:
    by = f" from {event.attacker_id}" if event.attacker_id else ""
    return f"{event.target_id} takes {event.amount} damage{by}."


def _absorbed(event: DamageAbsorbedEvent) -> str:
    by = f"{event.attacker_id}'s attack" if event.attacker_id else "The attack"
    return f"{by} has no effect on {event.target_id}."


def _save(event: SavingThrowRolledEvent, outcome: str) -> str:
    return (
        f"{event.target_id} {outcome} a save versus {event.category}: "
        f"rolled {event.roll}{event.modifier:+d}, needing {event.required}."
    )


def _morale(event: MoraleCheckedEvent, outcome: str) -> str:
    return f"Morale check for {event.subject} (ML {event.score}): rolled {event.roll}{event.modifier:+d} — {outcome}."


def _memorized(event: SpellsMemorizedEvent) -> str:
    prepared = ", ".join(f"{copy.spell_id} (reversed)" if copy.reversed else copy.spell_id for copy in event.prepared)
    return f"{event.caster_id} memorizes: {prepared or 'nothing'}."


def _cast(event: SpellCastEvent) -> str:
    spell = f"{event.spell_id} (reversed)" if event.reversed else event.spell_id
    at = f" at {', '.join(event.target_ids)}" if event.target_ids else ""
    manual = " — the effect is narrated, not automated" if event.manual else ""
    return f"{event.caster_id} casts {spell} [{event.mode}]{at}{manual}."


def _cast_no_effect(event: SpellCastEvent) -> str:
    spell = f"{event.spell_id} (reversed)" if event.reversed else event.spell_id
    return f"{event.caster_id} casts {spell} [{event.mode}] — it has no effect."


def _turning(event: UndeadTurnedEvent, outcome: str) -> str:
    pool = f", {event.hd_pool} HD affected" if event.hd_pool is not None else ""
    return f"{event.caster_id} presents the holy symbol (rolled {event.roll}{pool}) — {outcome}."


_TEMPLATES: dict[str, Callable[[Event], str]] = {
    "combat.initiative.rolled": _initiative,
    "combat.attack.hit": _attack_hit,
    "combat.attack.missed": _attack_missed,
    "combat.attack.auto_hit": _attack_auto,
    "combat.damage.dealt": _damage,
    "combat.damage.absorbed": _absorbed,
    "combat.save.passed": lambda event: _save(event, "passes"),
    "combat.save.failed": lambda event: _save(event, "fails"),
    "combat.save.auto": lambda event: f"{event.target_id} automatically saves versus {event.category}.",
    "combat.morale.held": lambda event: _morale(event, "they fight on"),
    "combat.morale.broke": lambda event: _morale(event, "they flee or surrender"),
    "combat.morale.exempt": lambda event: (
        f"Morale check for {event.subject} (ML {event.score}): no roll — "
        + ("they never check morale." if event.score == 12 else "they never fight.")
    ),
    "encounter.reaction.rolled": lambda event: (
        f"Reaction roll: {event.roll}{event.modifier:+d} = {event.total} — {event.result}."
    ),
    "effects.condition.gained": lambda event: f"{event.target_id} is {event.condition}.",
    "effects.condition.removed": lambda event: f"{event.target_id} is no longer {event.condition}.",
    "effects.effect.attached": lambda event: f"Effect {event.effect_id} ({event.kind}) attached to {event.target_ref}.",
    "effects.effect.ticked": lambda event: f"Effect {event.effect_id} ({event.kind}) ticks on {event.target_ref}.",
    "effects.effect.expired": lambda event: f"Effect {event.effect_id} ({event.kind}) on {event.target_ref} expires.",
    "effects.effect.released": lambda event: (
        f"Effect {event.effect_id} ({event.kind}) on {event.target_ref} is released."
    ),
    "combat.healing.applied": lambda event: f"{event.target_id} regains {event.amount} hit points ({event.source}).",
    "combat.healing.blocked": lambda event: f"{event.target_id} cannot be healed by {event.source} means.",
    "combat.death.died": lambda event: f"{event.target_id} is killed.",
    "combat.death.permanent": lambda event: f"{event.target_id} is permanently destroyed.",
    "combat.equipment.destroyed": lambda event: (
        f"{event.target_id}'s equipment is destroyed: " + ", ".join(event.item_names) + "."
    ),
    "combat.drain.drained": lambda event: (
        f"{event.target_id} loses {event.levels_lost} level(s) "
        f"({event.hp_lost} hit points), now level {event.new_level}."
    ),
    "combat.drain.slain": lambda event: f"{event.target_id} is drained of all levels and dies.",
    "effects.regeneration.revived": lambda event: f"{event.target_id} regenerates and rises to fight again!",
    "combat.state.hit_points": lambda event: f"{event.target_id} is at {event.current_hp}/{event.max_hp} hit points.",
    "combat.targeting.selected": lambda event: f"Targets ({event.mode}): {', '.join(event.target_ids) or 'none'}.",
    "magic.memorize.prepared": _memorized,
    "magic.cast.cast": _cast,
    "magic.cast.no_effect": _cast_no_effect,
    "magic.cast.disrupted": lambda event: (
        f"{event.caster_id}'s casting of {event.spell_id} is disrupted — the spell is lost."
    ),
    "magic.memory.forgotten": lambda event: f"{event.caster_id} forgets {event.spell_id}.",
    "magic.book.added": lambda event: f"{event.caster_id} adds {event.spell_id} to their spell book.",
    "magic.turning.turned": lambda event: _turning(event, "the undead are turned"),
    "magic.turning.destroyed": lambda event: _turning(event, "undead are destroyed"),
    "magic.turning.failed": lambda event: _turning(event, "the turning fails"),
    "magic.dispel.resolved": lambda event: (
        f"{event.caster_id} dispels {len(event.released_effect_ids)} effect(s)"
        + (f"; {len(event.surviving_effect_ids)} survive(s)" if event.surviving_effect_ids else "")
        + "."
    ),
}


def format_message(event: Event) -> str:
    """Format an event as a default English message.

    Total, pinned: an event whose code has no template formats to the code string
    itself — never raises — so logs from newer engine versions stay printable.

    Args:
        event: The event to format.

    Returns:
        The formatted English line, or the event's code when no template exists.
    """
    template = _TEMPLATES.get(event.code)
    if template is None:
        return event.code
    return template(event)
