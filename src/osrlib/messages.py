"""Turn an event into a line of English.

osrlib puts no prose in an event. A [`GameSession`][osrlib.crawl.session.GameSession]
responds to a command with typed events made of structured facts, like who attacked, what they
rolled, and how much damage they dealt, and you decide what a player reads. This
module is the default formatter that comes with osrlib.
[`format_message`][osrlib.messages.format_message] takes an event and returns a plain
English sentence, so you can print a session's output before you write a renderer of your
own.

The event's `code` determines the line you get. A code is a dotted name like
`combat.attack.hit` that identifies the outcome the event records, and this module has one
template per code. Every event class declares the codes it can use in `allowed_codes`, and
[`ALL_EVENT_CLASSES`][osrlib.crawl.events.ALL_EVENT_CLASSES] lists every event class, so
`{code for cls in ALL_EVENT_CLASSES for code in cls.allowed_codes}` is every code osrlib
defines.
[The message code reference](https://mmacy.github.io/osrlib-python/reference/message-codes/)
lists the same codes with the event class of each one, its default visibility, and its
template. Read it when you decide which codes to handle differently.

Use this formatter for a log, a transcript, a debugging view, or the first working version
of a game. When you want your own wording, replace the whole formatter: read the event's
fields and write your own line, or pass the event to a narrator. There's nothing here to
configure, no way to register a template, and no translation. You have the same structured
fields this formatter reads, so you can write any wording you like. Entity ids
come out as ids, like `orc-1`, because an event refers to creatures by id. The quest and
objective events are the exception: they include a display name the adventure's author
wrote, and the line uses it.

`format_message` only builds a string. It reads no files, makes no calls, and changes nothing,
so you can call it on any event from any source.

Typical usage:

```python
from osrlib.core.events import DamageDealtEvent
from osrlib.messages import format_message

event = DamageDealtEvent(target_id="orc-1", attacker_id="hild", amount=5)
print(format_message(event))
# orc-1 takes 5 damage from hild.
```
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
from osrlib.crawl.events import HealingPurchasedEvent

__all__ = [
    "format_message",
]


def _entered(event: Any) -> str:
    """The arrival line, which describes a stairs crossing as the climb or descent it was.

    The event's `via` is the only source of the direction. No other field gives the direction
    the party went, and the party's current position doesn't help when a line is rendered from the log
    later. The kinds without a direction (a trapdoor, a chute, a passage, a
    trap, the entrance, a referee's placement) get the plain arrival line. A stairs crossing into
    another dungeon still includes the dungeon's name, because the level number alone wouldn't
    show the change of dungeon.

    The line names the town or the dungeon by the event's `display_name`. An event without one, from
    a save written before the field existed or for a dungeon with no authored name, names a dungeon
    by its id and the town by the word alone. A name that is only spaces counts as none, and a line
    that ends with a name ending in its own `.`, `!`, or `?` gets no second stop.
    """
    name = event.display_name.strip()
    if event.location_kind == "town":
        return _sentence(f"The party enters {name}") if name else "The party enters town."
    place = name or event.location_id
    verb = {"stairs_up": "climbs", "stairs_down": "descends"}.get(event.via or "")
    if verb is not None and event.level_number is not None:
        if event.location_kind == "dungeon":
            return f"The party {verb} into {place}, level {event.level_number}."
        return _sentence(f"The party {verb} to level {event.level_number} of {place}")
    if event.location_kind == "level" and event.level_number is not None:
        return _sentence(f"The party enters level {event.level_number} of {place}")
    kind = "" if name else f"{event.location_kind} "
    return (
        f"The party enters {kind}{place}"
        + (f" (level {event.level_number})" if event.level_number is not None else "")
        + "."
    )


def _sentence(text: str) -> str:
    """End `text` with a full stop, unless it already ends with one, an exclamation mark, or a question mark."""
    return text if text.endswith((".", "!", "?")) else f"{text}."


def _detection(event: Any) -> str:
    """Format a hidden roll, with a thief skill's chance as a percentage and any other chance as a chance in 6.

    The thief skills roll d%, and the listening, search, and trap-spring checks roll a d6.
    """
    percentile = event.kind in ("open_locks", "treasure_traps", "hide_in_shadows", "move_silently")
    chance = f"{event.chance}%" if percentile else f"{event.chance}-in-6"
    who = f", {event.character_id}" if event.character_id else ""
    rolled = event.roll if event.roll is not None else "no die"
    outcome = "Success." if event.passed else "Failure."
    return f"Detection roll ({event.kind}{who}): {rolled} vs {chance}. {outcome}"


def _initiative(event: InitiativeRolledEvent) -> str:
    rolled = ", ".join(f"{entry.key} {entry.total}" for entry in event.entries)
    return f"Initiative ({event.mode}): {rolled}. Order: {', '.join(event.order)}."


def _attack_hit(event: AttackRolledEvent) -> str:
    natural = " (natural 20)" if event.natural == 20 else ""
    verb = "back-stabs" if event.back_stab else "hits"
    return (
        f"{event.attacker_id} {verb} {event.defender_id} with {event.attack_name}: "
        f"rolled {event.roll}{event.modifier:+d} = {event.total}, needing {event.required}{natural}."
    )


def _attack_missed(event: AttackRolledEvent) -> str:
    natural = " (natural 1)" if event.natural == 1 else ""
    attempt = f"a back-stab on {event.defender_id}" if event.back_stab else event.defender_id
    return (
        f"{event.attacker_id} misses {attempt} with {event.attack_name}: "
        f"rolled {event.roll}{event.modifier:+d} = {event.total}, needing {event.required}{natural}."
    )


def _attack_auto(event: AttackRolledEvent) -> str:
    if event.back_stab:
        return (
            f"{event.attacker_id} back-stabs the helpless {event.defender_id} with {event.attack_name}. No roll needed."
        )
    return f"{event.attacker_id} strikes the helpless {event.defender_id} with {event.attack_name}. No roll is needed."


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
    return f"Morale check for {event.subject} (ML {event.score}): rolled {event.roll}{event.modifier:+d}, so {outcome}."


def _morale_held(event: MoraleCheckedEvent) -> bool:
    """Whether the side keeps fighting: `held`, or the score for an event from a save made before `held` existed."""
    return event.score >= 12 if event.held is None else event.held


def _memorized(event: SpellsMemorizedEvent) -> str:
    prepared = [f"{copy.spell_id} (reversed)" if copy.reversed else copy.spell_id for copy in event.prepared]
    return f"{event.caster_id} memorizes: {_listed(prepared) or 'nothing'}."


def _cast_mode(event: SpellCastEvent) -> str:
    """The cast mode's label, or its key for an event recorded before the event contained the label."""
    return event.mode_display_name or event.mode


def _cast(event: SpellCastEvent) -> str:
    spell = f"{event.spell_id} (reversed)" if event.reversed else event.spell_id
    at = f" at {_listed(list(event.target_ids))}" if event.target_ids else ""
    manual = ". The effect is narrated, not automated" if event.manual else ""
    return f"{event.caster_id} casts {spell} [{_cast_mode(event)}]{at}{manual}."


def _cast_no_effect(event: SpellCastEvent) -> str:
    spell = f"{event.spell_id} (reversed)" if event.reversed else event.spell_id
    return f"{event.caster_id} casts {spell} [{_cast_mode(event)}]. It has no effect."


def _healing_purchased(event: HealingPurchasedEvent) -> str:
    """The temple line, which lists the other payers when the rest of the party contributed."""
    line = f"{event.character_id} purchases {event.service} at the temple for {event.cost_gp} gp"
    if len(event.payers) > 1:
        paid = zip(event.payers, event.payments_gp, strict=True)
        line += ", paid by " + _listed([f"{payer} {amount} gp" for payer, amount in paid])
    return line + "."


def _listed(parts: list[str]) -> str:
    """Parts joined as an English list, like `"a and b"` or `"a, b, and c"`, with "and" before the last part.

    A list of three or more parts has a comma before its "and", the serial comma.
    """
    if len(parts) <= 2:
        return " and ".join(parts)
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


def _encounter_started(event: Any) -> str:
    """The encounter line, which names each group, like `"Encounter: 2 goblins and 3 orcs at 40'."`.

    An event recorded before osrlib 2.7.0 has no group fields, so its line names the first group and counts the
    creatures of every group.
    """
    if event.group_labels:
        groups = zip(event.group_counts, event.group_labels, event.group_labels_plural, strict=True)
        seen = _listed([f"{count} {label if count == 1 else plural}" for count, label, plural in groups])
    else:
        seen = f"{event.count} {event.monster_name if event.count == 1 else event.monster_name_plural}"
    return (
        f"Encounter: {seen} at {event.distance_feet}'"
        + ("; the party is surprised" if event.party_surprised else "")
        + ("; the monsters are surprised" if event.monsters_surprised else "")
        + "."
    )


def _monsters_spawned(event: Any) -> str:
    """The referee line for a spawn, like `"Spawned 2 goblins: monster-0001 and monster-0002."`.

    An event recorded before osrlib 2.7.0 has no display name, so its line names the template by its id.
    """
    count = len(event.monster_ids)
    if event.display_name:
        what = f"{count} {event.display_name if count == 1 else event.display_name_plural}"
    else:
        what = f"{count} {'monster' if count == 1 else 'monsters'} from the {event.template_id} template"
    return f"Spawned {what}: {_listed(list(event.monster_ids))}."


def _coin_parts(event: Any) -> list[str]:
    """The coins an event moved, one part per denomination, like `["3 gp", "4 sp"]`, or `[]` for none.

    An event recorded before osrlib 2.7.0 has no `coins`, so its one part comes from its
    `coins_gp_value`.
    """
    parts = [
        f"{count} {denomination}"
        for denomination in ("pp", "gp", "ep", "sp", "cp")
        if (count := getattr(event.coins, denomination))
    ]
    if not parts and event.coins_gp_value:
        parts = [f"{event.coins_gp_value} gp"]
    return parts


def _coins(event: Any) -> str:
    """The coins an event moved, by denomination, like `"3 gp and 4 sp in coin"`, or `""` for none."""
    parts = _coin_parts(event)
    return f"{_listed(parts)} in coin" if parts else ""


def _goods(event: Any) -> str:
    """The items and coins an event moved, like `"jade-idol, 3 pp, and 40 cp in coin"`.

    With coins, the items and the denominations make one list, with "and" before the last part. Items
    alone make the same kind of list, like `"brass_key and toll_token"`.
    """
    coins = _coin_parts(event)
    if not coins:
        return _listed(list(event.item_ids)) if event.item_ids else ""
    return _listed([*event.item_ids, *coins]) + " in coin"


def _xp_awarded(event: Any) -> str:
    """The award line, which says the rest was lost when the one-level limit cut the award."""
    gained = event.modified_award if event.gained is None else event.gained
    line = f"{event.character_id} gains {gained} XP (base {event.award}), now level {event.level_after}."
    if event.clamped:
        line += " The rest is lost to the one-level limit."
    return line


def _turning(event: UndeadTurnedEvent, outcome: str) -> str:
    pool = f", {event.hd_pool} HD affected" if event.hd_pool is not None else ""
    return f"{event.caster_id} presents the holy symbol (rolled {event.roll}{pool}): {outcome}."


# The registry dispatches on the event's `code`, so each template can assume its concrete
# event class. `Any` is the accurate annotation for dispatch keyed on a string.
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
        f"Morale check for {event.subject} (ML {event.score}): no roll, because "
        + ("they never check morale." if _morale_held(event) else "they never fight.")
    ),
    "encounter.reaction.rolled": lambda event: (
        f"Reaction roll: {event.roll}{event.modifier:+d} = {event.total} ({event.result})."
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
    "combat.death.died": lambda event: (
        f"{event.target_id} is killed by poison." if event.poison else f"{event.target_id} is killed."
    ),
    "combat.death.permanent": lambda event: f"{event.target_id} is permanently destroyed.",
    "combat.equipment.destroyed": lambda event: (
        f"{event.target_id}'s equipment is destroyed: " + _listed(list(event.item_names)) + "."
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
        f"{event.caster_id}'s casting of {event.spell_id} is disrupted, and the spell is lost."
    ),
    "magic.cast.fizzled": lambda event: (
        f"{event.caster_id}'s casting of {event.spell_id} fizzles, and the spell is lost."
    ),
    "magic.device.fizzled": lambda event: (
        f"{event.actor_id}'s use of {event.item_id} fizzles. The device keeps its charge."
    ),
    "magic.potion.fizzled": lambda event: (
        f"{event.actor_id} can't give {event.item_id} to {event.target_id or 'another member'} to drink, "
        "and keeps the potion."
    ),
    "battle.give.fizzled": lambda event: (
        f"{event.actor_id} can't hand {event.item_id} to {event.target_id or 'another member'}, and keeps it."
    ),
    "magic.turning.fizzled": lambda event: f"{event.actor_id}'s attempt to turn undead fizzles.",
    "battle.melee.fizzled": lambda event: (
        f"{event.actor_id} "
        + {
            "battle.declaration.no_target": "finds nothing left to strike in",
            "battle.declaration.not_in_front_rank": "can't get into the front rank against",
        }.get(event.reason or "", "can't reach")
        + f" {event.group_id or 'the group'}, and makes no attack."
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
    "exploration.party.reordered": lambda event: f"The party's marching order is now {', '.join(event.order)}.",
    "exploration.location.entered": _entered,
    "exploration.door.opened": lambda event: f"The door {event.direction} of ({event.x}, {event.y}) opens.",
    "exploration.door.closed": lambda event: f"The door {event.direction} of ({event.x}, {event.y}) closes.",
    "exploration.door.forced": lambda event: (
        f"{event.character_id} forces the door {event.direction} of ({event.x}, {event.y}) open."
    ),
    "exploration.door.stuck": lambda event: (
        f"The door {event.direction} of ({event.x}, {event.y}) won't budge"
        + (f". {event.character_id} strains in vain" if event.character_id else "")
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
    "exploration.detection.rolled": _detection,
    "exploration.search.found": lambda event: (
        f"{event.character_id} searches ({event.kind}) and finds: {_listed(list(event.found))}."
    ),
    "exploration.search.nothing": lambda event: f"{event.character_id} searches ({event.kind}) and finds nothing.",
    "exploration.trap.sprung": lambda event: (
        f"A trap springs ({event.trap_ref})" + (f" on {event.character_id}" if event.character_id else "") + "!"
    ),
    "exploration.trap.safe": lambda event: f"The known trap ({event.trap_ref}) does not go off.",
    "exploration.trap.found": lambda event: (
        (event.character_id or "The party")
        + f" finds a trap ({event.trap_ref})"
        + (f" beyond the {event.direction} door" if event.direction else "")
        + "."
    ),
    "exploration.trap.removed": lambda event: (
        (event.character_id or "The party") + f" removes the trap ({event.trap_ref})."
    ),
    "exploration.trap.removal_failed": lambda event: (
        (event.character_id or "The party") + f" fails to remove the trap ({event.trap_ref})."
    ),
    "exploration.item.acquired": lambda event: f"{event.character_id} acquires " + _goods(event) + ".",
    "exploration.item.consumed": lambda event: f"{event.character_id} uses up {event.item_id}.",
    "exploration.item.dropped": lambda event: f"{event.character_id} drops " + _goods(event) + ".",
    "exploration.item.left_behind": lambda event: (
        "The party cannot carry it all, so " + _goods(event) + " stays where it lies."
    ),
    "exploration.item.given": lambda event: (
        f"{event.character_id} gives " + _goods(event) + f" to {event.recipient_id}."
    ),
    "exploration.light.lit": lambda event: f"{event.character_id} lights a {event.source}.",
    "exploration.light.extinguished": lambda event: f"{event.character_id} extinguishes the {event.source}.",
    "exploration.light.failed": lambda event: f"{event.character_id} fumbles with the tinder box and gets no flame.",
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
        f"Wandering check: {event.roll if event.roll is not None else 'skipped'} vs {event.chance}-in-6. "
        + ("An encounter!" if event.encounter else "Nothing comes.")
    ),
    "encounter.started": _encounter_started,
    "encounter.group.joined": lambda event: (
        f"More monsters join the encounter: {event.count} {event.label if event.count == 1 else event.label_plural}"
        f" at {event.distance_feet}'."
    ),
    "encounter.surprise.rolled": lambda event: (
        f"Surprise ({event.side}): "
        + (f"rolled {event.roll}" if event.roll is not None else "no roll")
        + f", surprised on 1-{event.threshold}. "
        + ("Surprised." if event.surprised else "Not surprised.")
    ),
    "encounter.stance.changed": lambda event: f"The monsters' bearing: {event.stance}.",
    "encounter.evasion.succeeded": lambda event: "The party slips away and evades the encounter.",
    "encounter.evasion.pursuit": lambda event: "The monsters give chase!",
    "encounter.pursuit.round": lambda event: f"Pursuit round {event.round}: the gap is {event.gap_feet}'.",
    "encounter.pursuit.distracted": lambda event: (
        f"Pursuit round {event.round}: the monsters stop for the dropped bait."
    ),
    "encounter.pursuit.escaped": lambda event: f"Pursuit round {event.round}: the party escapes.",
    "encounter.pursuit.caught": lambda event: f"Pursuit round {event.round}: the monsters catch the party!",
    "encounter.exhaustion.gained": lambda event: "The party is exhausted from running: -2 to attacks, damage, and AC.",
    "encounter.exhaustion.recovered": lambda event: "The party catches its breath, and the exhaustion fades.",
    "encounter.loot.dropped": lambda event: "The fallen leave behind " + _goods(event) + ".",
    "encounter.ended": lambda event: f"The encounter ends ({event.outcome}).",
    "battle.started": lambda event: "Battle is joined!",
    "battle.round.started": lambda event: f"Battle round {event.round} begins.",
    "battle.spell.declared": lambda event: (
        f"{event.caster_id} begins casting {event.spell_id}" + (" (reversed)" if event.reversed else "") + "."
    ),
    "battle.group.moved": lambda event: f"{event.group_id} is now {event.distance_feet}' away.",
    "battle.member.moved": lambda event: (
        f"{event.character_id} "
        + {"close": "closes", "fighting_withdrawal": "falls back", "retreat": "retreats"}.get(event.move, "moves")
        + f" to {event.distance_feet}' from {event.group_id}."
    ),
    "battle.member.withdrew": lambda event: (
        f"{event.character_id} falls back and trades places in the marching order with {event.replacement_id}."
    ),
    "battle.member.retreated": lambda event: (
        f"{event.character_id} turns and retreats, and trades places in the marching order with {event.replacement_id}."
    ),
    "battle.hide.attempted": lambda event: f"{event.character_id} tries to hide in the shadows.",
    "battle.side.fled": lambda event: f"{event.group_id} breaks and runs!",
    "battle.side.escaped": lambda event: f"{event.group_id} gets away.",
    "battle.group.left_behind": lambda event: (
        f"{event.source_group_id} leaves {event.count} helpless behind as {event.group_id}."
    ),
    "battle.monster.defeated": lambda event: f"{event.monster_id} ({event.template_id}) is {event.outcome}.",
    "battle.ended.victory": lambda event: "The battle is won.",
    "battle.ended.fled": lambda event: "The party flees the battle.",
    "battle.ended.defeat": lambda event: "The party is defeated.",
    "treasure.hoard.generated": lambda event: (
        f"Treasure generated at {event.cache_ref}"
        + (f" ({', '.join(event.treasure_types)})" if event.treasure_types else "")
        + f": {_coins(event) or 'no coin'}, {len(event.valuable_ids)} valuable(s), "
        + f"{len(event.magic_item_ids)} magic item(s)."
    ),
    "items.potion.drunk": lambda event: (
        f"{event.character_id} gives {event.target_id} {event.instance_id} to drink."
        if event.target_id
        else f"{event.character_id} drinks {event.instance_id}."
    ),
    "items.potion.mixed": lambda event: (
        f"{event.target_id or event.character_id} mixes potions. Both effects cancel and sickness takes hold!"
    ),
    "items.scroll.read": lambda event: f"{event.character_id} reads {event.instance_id}, and the words disappear.",
    "items.scroll.cursed": lambda event: (
        f"{event.character_id} looks upon {event.instance_id}, and a curse takes hold!"
    ),
    "items.device.activated": lambda event: f"{event.character_id} activates {event.instance_id}.",
    # An event recorded before `display_name` existed has it empty, and names the item by its template id.
    "items.item.identified": lambda event: (
        f"{event.instance_id} is identified: {event.display_name or event.template_id}."
    ),
    "items.curse.revealed": lambda event: (
        f"{event.instance_id} reveals its curse ({event.template_id}), and {event.character_id} cannot be rid of it."
    ),
    "encounter.npc_party.spawned": lambda event: (
        f"An NPC party ({event.party_kind}, {event.alignment}) takes the field: "
        + _listed(
            [
                f"{npc} ({cls} {level})"
                for npc, cls, level in zip(event.npc_ids, event.class_ids, event.levels, strict=True)
            ]
        )
        + "."
    ),
    "session.xp.adventure_award": lambda event: (
        f"The adventure ends: {event.monster_xp} XP from monsters and {event.treasure_xp} XP from treasure, "
        f"and each of {len(event.survivors)} survivor(s) gets {event.share} XP."
    ),
    "town.treasure.sold": lambda event: (
        f"{event.character_id} sells {len(event.instance_ids)} valuable(s) for {event.gp_value} gp."
    ),
    "town.healing.purchased": _healing_purchased,
    "session.flag.set": lambda event: f"Flag {event.key} = {event.value!r}.",
    "session.monsters.spawned": _monsters_spawned,
    "session.xp.awarded": _xp_awarded,
    "session.level.gained": lambda event: (
        f"{event.character_id} advances to level {event.level_after}"
        + (f" ({event.title})" if event.title is not None else "")
        + f": +{event.hp_gained} hp"
        + (f" (rolled {event.hp_roll})" if event.hp_roll is not None else "")
        + "."
    ),
    "session.time.advanced": lambda event: f"Time advances {event.n} {event.unit}(s), to round {event.rounds_total}.",
    "session.game_over": lambda event: f"The game is over: {event.reason}.",
    "adjudication.dice_rolled": lambda event: (
        f"Referee rolls {event.expression}: [{', '.join(str(die) for die in event.rolls)}]"
        f", for a total of {event.total}."
    ),
    "session.trigger.fired": lambda event: f"Trigger {event.trigger_id} fired.",
    "session.journal.entry_added": lambda event: f"Journal: {event.text}",
    "session.note.recorded": lambda event: f"Referee note: {event.text}",
    "session.quest.activated": lambda event: f"A new quest: {event.name}.",
    # The quest and objective names fall back to the ids, so an event that doesn't include
    # them still formats. The session fills them in when it emits these events.
    "session.quest.objective_revealed": lambda event: (
        f"Quest {event.quest_name or event.quest_id}: a new objective, {event.name or event.objective_id}."
    ),
    "session.quest.objective_completed": lambda event: (
        f"Quest {event.quest_name or event.quest_id}: objective {event.name or event.objective_id} is done."
    ),
    "session.quest.completed": lambda event: f"Quest complete: {event.name}.",
    "session.adventure.completed": lambda event: f"The adventure is over: {event.name or event.quest_id} is finished.",
    "session.items.removed": lambda event: f"{event.character_id} gives up " + _goods(event) + ".",
    "session.member.added": lambda event: (
        f"{event.name}, a level {event.level} {event.class_id}, joins the party as {event.character_id}."
    ),
    "session.member.removed": lambda event: (
        f"{event.name} leaves the party"
        + (f", and {event.given_to} takes their belongings." if event.given_to else ".")
    ),
}


def format_message(event: Event) -> str:
    """Format an event as a line of default English.

    Call it on each event in a [`CommandResult`][osrlib.crawl.commands.CommandResult]'s
    `events`, or on each event in a session's `event_log`, to get a transcript a person can
    read. If you're showing the result to a player, first keep only the events whose
    `visibility` is [`Visibility.PLAYER`][osrlib.core.events.Visibility], so referee-only
    events stay hidden.

    A session's `event_log` can also contain plain dicts. After
    [`load_game`][osrlib.persistence.load_game], an entry whose event type this version of
    osrlib doesn't recognize stays the dict it was in the save. Skip those, or print their
    `code` key, before you filter on `visibility`. A dict has no `visibility` attribute, and
    this function takes an event object, not a dict.

    On an event osrlib built, it doesn't raise and it doesn't return an empty string. An
    event whose code has no template, like a base [`Event`][osrlib.core.events.Event] you
    build with a code of your own, comes back as the code itself. An event you build by hand
    can still raise `AttributeError`, because a template reads the fields its event class
    declares: give an event a code from another class and the field that template needs
    isn't there. When you build an event by hand, use the class that declares its code.

    Some events include a `narrative`, the sentence an adventure's author wrote for that
    moment. When there is one, it's added to the end of the templated line, word for word,
    because the wording of authored text belongs to the author.

    Write your own formatter when you want control of the wording. This one prints entity
    ids as ids, writes only English, and has no way to change a template. Every fact it uses
    is a typed field on the event, so your version can read the same fields and use your
    own names for things.

    Args:
        event: Any event, from a command result, a session's event log, or a save you loaded.

    Returns:
        One line, with no trailing newline: the template for the event's code, followed by
            the event's `narrative` when it has one, or the bare code when no template
            matches.

    Examples:
        ```python
        from osrlib.core.events import DamageDealtEvent, Event, Visibility
        from osrlib.messages import format_message

        event = DamageDealtEvent(target_id="orc-1", attacker_id="hild", amount=5)
        print(format_message(event))
        # orc-1 takes 5 damage from hild.

        unknown = Event(code="future.thing.happened", visibility=Visibility.PLAYER)
        print(format_message(unknown))
        # future.thing.happened
        ```
    """
    template = _TEMPLATES.get(event.code)
    line = event.code if template is None else template(event)
    narrative = getattr(event, "narrative", None)
    return f"{line} {narrative}" if narrative else line
