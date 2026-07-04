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
from typing import Any

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


# The registry dispatches on the event's `code`, so each template statically knows
# its concrete event class — `Any` is the honest typing of code-keyed dispatch.
_TEMPLATES: dict[str, Callable[[Any], str]] = {
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
    "exploration.party.moved": lambda event: f"The party moves to ({event.x}, {event.y}), facing {event.facing}.",
    "exploration.party.turned": lambda event: f"The party turns to face {event.facing}.",
    "exploration.location.entered": lambda event: (
        f"The party enters {event.location_kind} {event.location_id}"
        + (f" (level {event.level_number})" if event.level_number is not None else "")
        + "."
    ),
    "exploration.door.opened": lambda event: f"The door {event.direction} of ({event.x}, {event.y}) opens.",
    "exploration.door.closed": lambda event: f"The door {event.direction} of ({event.x}, {event.y}) closes.",
    "exploration.door.forced": lambda event: (
        f"{event.character_id} forces the door {event.direction} of ({event.x}, {event.y}) open."
    ),
    "exploration.door.stuck": lambda event: (
        f"The door {event.direction} of ({event.x}, {event.y}) won't budge"
        + (f" — {event.character_id} strains in vain" if event.character_id else "")
        + "."
    ),
    "exploration.door.wedged": lambda event: (
        f"An iron spike wedges the door {event.direction} of ({event.x}, {event.y})."
    ),
    "exploration.door.swung_shut": lambda event: (
        f"The door {event.direction} of ({event.x}, {event.y}) swings shut behind the party."
    ),
    "exploration.door.unlocked": lambda event: (
        f"{event.character_id} unlocks the door {event.direction} of ({event.x}, {event.y})."
    ),
    "exploration.listen.heard": lambda event: (
        f"{event.character_id} hears something beyond the {event.direction} door."
    ),
    "exploration.listen.silent": lambda event: f"{event.character_id} hears nothing beyond the {event.direction} door.",
    "exploration.detection.rolled": lambda event: (
        f"Detection roll ({event.kind}"
        + (f", {event.character_id}" if event.character_id else "")
        + f"): {event.roll if event.roll is not None else 'no die'} vs {event.chance}-in-6 — "
        + ("success." if event.passed else "failure.")
    ),
    "exploration.search.found": lambda event: (
        f"{event.character_id} searches ({event.kind}) and finds: {', '.join(event.found)}."
    ),
    "exploration.search.nothing": lambda event: f"{event.character_id} searches ({event.kind}) and finds nothing.",
    "exploration.trap.sprung": lambda event: (
        f"A trap springs ({event.trap_ref})" + (f" on {event.character_id}" if event.character_id else "") + "!"
    ),
    "exploration.trap.safe": lambda event: f"The known trap ({event.trap_ref}) does not go off.",
    "exploration.trap.found": lambda event: (event.character_id or "The party") + f" finds a trap ({event.trap_ref}).",
    "exploration.trap.removed": lambda event: (
        (event.character_id or "The party") + f" removes the trap ({event.trap_ref})."
    ),
    "exploration.item.acquired": lambda event: (
        f"{event.character_id} acquires "
        + (", ".join(event.item_ids) if event.item_ids else "")
        + (" and " if event.item_ids and event.coins_gp_value else "")
        + (f"{event.coins_gp_value} gp in coin" if event.coins_gp_value else "")
        + "."
    ),
    "exploration.item.dropped": lambda event: (
        f"{event.character_id} drops "
        + (", ".join(event.item_ids) if event.item_ids else "")
        + (" and " if event.item_ids and event.coins_gp_value else "")
        + (f"{event.coins_gp_value} gp in coin" if event.coins_gp_value else "")
        + "."
    ),
    "exploration.light.lit": lambda event: f"{event.character_id} lights a {event.source}.",
    "exploration.light.extinguished": lambda event: f"{event.character_id} extinguishes the {event.source}.",
    "exploration.light.failed": lambda event: f"{event.character_id} fumbles with the tinder box — no flame.",
    "exploration.light.expired": lambda event: (
        f"The {event.source}" + (f" carried by {event.character_id}" if event.character_id else "") + " gutters out."
    ),
    "exploration.rest.rested": lambda event: f"The party rests ({event.kind}).",
    "exploration.rest.interrupted": lambda event: f"The party's {event.kind} rest is interrupted!",
    "exploration.fatigue.gained": lambda event: "The party is fatigued: -1 to attack and damage until they rest.",
    "exploration.fatigue.recovered": lambda event: "The party recovers from fatigue.",
    "exploration.provisions.consumed": lambda event: f"{event.character_id} consumes the day's {event.kind}.",
    "exploration.provisions.short": lambda event: f"{event.character_id} has no {event.kind} for the day.",
    "exploration.wandering.checked": lambda event: (
        f"Wandering check: {event.roll if event.roll is not None else 'skipped'} vs {event.chance}-in-6 — "
        + ("an encounter!" if event.encounter else "nothing comes.")
    ),
    "encounter.started": lambda event: (
        f"Encounter: {event.count} × {event.monster_name} at {event.distance_feet}'"
        + (" — the party is surprised" if event.party_surprised else "")
        + (" — the monsters are surprised" if event.monsters_surprised else "")
        + "."
    ),
    "encounter.surprise.rolled": lambda event: (
        f"Surprise ({event.side}): "
        + (f"rolled {event.roll}" if event.roll is not None else "no roll")
        + f", surprised on 1-{event.threshold} — "
        + ("surprised." if event.surprised else "not surprised.")
    ),
    "encounter.stance.changed": lambda event: f"The monsters' bearing: {event.stance}.",
    "encounter.evasion.succeeded": lambda event: "The party slips away — the encounter is evaded.",
    "encounter.evasion.pursuit": lambda event: "The monsters give chase!",
    "encounter.pursuit.round": lambda event: f"Pursuit round {event.round}: the gap is {event.gap_feet}'.",
    "encounter.pursuit.distracted": lambda event: (
        f"Pursuit round {event.round}: the monsters stop for the dropped bait."
    ),
    "encounter.pursuit.escaped": lambda event: f"Pursuit round {event.round}: the party escapes.",
    "encounter.pursuit.caught": lambda event: f"Pursuit round {event.round}: the monsters catch the party!",
    "encounter.exhaustion.gained": lambda event: "The party is exhausted from running: -2 to attacks, damage, and AC.",
    "encounter.exhaustion.recovered": lambda event: "The party catches its breath — exhaustion fades.",
    "encounter.ended": lambda event: f"The encounter ends ({event.outcome}).",
    "battle.started": lambda event: "Battle is joined!",
    "battle.round.started": lambda event: f"Battle round {event.round} begins.",
    "battle.spell.declared": lambda event: (
        f"{event.caster_id} begins casting {event.spell_id}" + (" (reversed)" if event.reversed else "") + "."
    ),
    "battle.group.moved": lambda event: f"{event.group_id} is now {event.distance_feet}' away.",
    "battle.side.fled": lambda event: f"{event.group_id} flees the battle!",
    "battle.side.surrendered": lambda event: f"{event.group_id} surrenders.",
    "battle.monster.defeated": lambda event: f"{event.monster_id} ({event.template_id}) is {event.outcome}.",
    "battle.ended.victory": lambda event: "The battle is won.",
    "battle.ended.fled": lambda event: "The party flees the battle.",
    "battle.ended.defeat": lambda event: "The party is defeated.",
    "treasure.hoard.generated": lambda event: (
        f"Treasure generated at {event.cache_ref}"
        + (f" ({', '.join(event.treasure_types)})" if event.treasure_types else "")
        + f": {event.coins_gp_value} gp in coin, {len(event.valuable_ids)} valuable(s), "
        + f"{len(event.magic_item_ids)} magic item(s)."
    ),
    "items.potion.drunk": lambda event: f"{event.character_id} drinks {event.instance_id}.",
    "items.potion.mixed": lambda event: (
        f"{event.character_id} mixes potions — both effects cancel and sickness takes hold!"
    ),
    "items.scroll.read": lambda event: f"{event.character_id} reads {event.instance_id} — the words disappear.",
    "items.scroll.cursed": lambda event: f"{event.character_id} looks upon {event.instance_id} — a curse takes hold!",
    "items.device.activated": lambda event: f"{event.character_id} activates {event.instance_id}.",
    "items.item.identified": lambda event: f"{event.instance_id} is identified: {event.template_id}.",
    "items.curse.revealed": lambda event: (
        f"{event.instance_id} reveals its curse ({event.template_id}) — {event.character_id} cannot be rid of it."
    ),
    "encounter.npc_party.spawned": lambda event: (
        f"An NPC party ({event.party_kind}, {event.alignment}) takes the field: "
        + ", ".join(
            f"{npc} ({cls} {level})"
            for npc, cls, level in zip(event.npc_ids, event.class_ids, event.levels, strict=True)
        )
        + "."
    ),
    "session.xp.adventure_award": lambda event: (
        f"The adventure ends: {event.monster_xp} XP from monsters and {event.treasure_xp} XP from treasure — "
        f"{event.share} XP to each of {len(event.survivors)} survivor(s)."
    ),
    "town.treasure.sold": lambda event: (
        f"{event.character_id} sells {len(event.instance_ids)} valuable(s) for {event.gp_value} gp."
    ),
    "town.healing.purchased": lambda event: (
        f"{event.character_id} purchases {event.service} at the temple for {event.cost_gp} gp."
    ),
    "session.flag.set": lambda event: f"Flag {event.key} = {event.value!r}.",
    "session.monsters.spawned": lambda event: (
        f"Spawned {len(event.monster_ids)} × {event.template_id}: {', '.join(event.monster_ids)}."
    ),
    "session.xp.awarded": lambda event: (
        f"{event.character_id} gains {event.modified_award} XP (base {event.award}), now level {event.level_after}."
    ),
    "session.time.advanced": lambda event: f"Time advances {event.n} {event.unit}(s), to round {event.rounds_total}.",
    "session.game_over": lambda event: f"The game is over: {event.reason}.",
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
