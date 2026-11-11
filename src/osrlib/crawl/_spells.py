"""The spell commands outside battle: `PrepareSpells`, `LearnSpell`, and `CastSpell`.

The crawl adds two results to a cast that the spell kernel in `osrlib.core.spells` doesn't handle: a *silence 15'
radius* whose target saves anchors to the party's cell, and *remove curse* lifts the curses on its target's items.
`_cast_outcomes` adds both, and every cast in a session calls it once the kernel has resolved the cast: `CastSpell`,
a scroll read outside a battle, a `cast` declaration and a scroll read in a battle, and the temple's services. The
temple's healing and scroll reading also use `_cast_context`. Every level drain in a session runs through
`_drain_character`, because a drain looks up each memorized spell.
"""

from collections.abc import Callable, Sequence

from osrlib.core.character import Character
from osrlib.core.clock import TimeUnit
from osrlib.core.creature import Creature
from osrlib.core.effects import EFFECTS_STREAM, Condition, EffectDefinition
from osrlib.core.events import Event, SavingThrowRolledEvent
from osrlib.core.items import MagicItemInstance
from osrlib.core.spells import (
    MAGIC_STREAM,
    CastContext,
    CastResult,
    SpellCatalog,
    SpellTemplate,
    add_spell_to_book,
    cast_spell,
    caster_profile,
    memorize_spells,
    validate_cast,
)
from osrlib.core.validation import Rejection
from osrlib.crawl._party_location import _cell_ref, _location
from osrlib.crawl._party_members import _member_able
from osrlib.crawl.commands import CastSpell, LearnSpell, PrepareSpells


def _handle_prepare_spells(session, command: PrepareSpells) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    if session.sleep_count == 0 or session.last_prepared_sleep.get(member.id, 0) >= session.sleep_count:
        return [Rejection(code="magic.memorize.needs_sleep", params={"character": member.id})], []
    definition = member.definition
    result = memorize_spells(member, definition, session.effective_spells, command.selections)
    if result.rejections:
        return list(result.rejections), []
    session.last_prepared_sleep[member.id] = session.sleep_count
    events = list(result.events)
    turn_events, _ = session.advance_turns(6)
    events.extend(turn_events)
    return [], events


def _handle_learn_spell(session, command: LearnSpell) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    catalog = session.effective_spells
    known = {spell.id for spell in catalog.spells}
    # The room left at a spell level depends on the level of each spell already in the book, so
    # the session can't check a book with a spell its catalog doesn't have.
    unknown = next((spell_id for spell_id in member.spell_book if spell_id not in known), None)
    if unknown is not None:
        return [Rejection(code="magic.book.unknown_spell", params={"spell": unknown})], []
    result = add_spell_to_book(member, member.definition, catalog, command.spell_id)
    return list(result.rejections), list(result.events)


def _handle_cast_spell(session, command: CastSpell) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    try:
        spell = session.effective_spells.get(command.spell_id)
    except ValueError:
        return [Rejection(code="magic.cast.unknown_spell", params={"spell": command.spell_id})], []
    if _location(session).kind == "dungeon" and session.ledger.active_on(_cell_ref(session), "silence"):
        return [Rejection(code="magic.cast.silenced_area", params={"caster": member.id})], []
    registry = session.registry()
    targets: list[Creature | str] = []
    for target_ref in command.targets:
        if target_ref.startswith("cell:"):
            targets.append(target_ref)
        elif target_ref in registry:
            targets.append(registry[target_ref])
        else:
            return [Rejection(code="magic.cast.unknown_target", params={"target": target_ref})], []
    context = _cast_context(session, targets, in_combat=False)
    profile = caster_profile(member.definition)
    if profile is None:
        # A member with no casting profile can never have a memorized copy.
        return [
            Rejection(code="magic.cast.not_memorized", params={"spell": spell.id, "reversed": command.reversed})
        ], []
    cast_rejections = validate_cast(
        member,
        spell,
        command.mode,
        profile=profile,
        reversed=command.reversed,
        targets=targets,
        context=context,
        ledger=session.ledger,
    )
    if cast_rejections:
        return cast_rejections, []
    result = cast_spell(
        member,
        spell,
        command.mode,
        profile=profile,
        reversed=command.reversed,
        targets=targets,
        context=context,
        ledger=session.ledger,
        clock=session.clock,
        allocator=session.allocator,
        registry=registry,
        ruleset=session.ruleset,
        stream=session.streams.get(MAGIC_STREAM),
        effects_stream=session.streams.get(EFFECTS_STREAM),
    )
    events = list(result.events)
    events.extend(_cast_outcomes(session, spell, result, targets))
    events.extend(session.advance_rounds(1))
    return [], events


def _cast_context(session, targets, *, in_combat: bool, distance_feet: int | None = None) -> CastContext:
    """Build the cast context from session state, with the actual numbers from the death records."""
    from osrlib.core.clock import ROUNDS_PER_DAY

    days_since_death = None
    rounds_since_death = None
    for target in targets:
        target_id = getattr(target, "id", None)
        record = session.death_records.get(target_id) if target_id else None
        if record is not None:
            days_since_death = (session.clock.rounds - record.round) // ROUNDS_PER_DAY
            if record.cause == "poison":
                rounds_since_death = session.clock.rounds - record.round
            break
    return CastContext(
        in_combat=in_combat,
        distance_feet=distance_feet,
        days_since_death=days_since_death,
        rounds_since_death=rounds_since_death,
    )


_REMOVE_CURSE_SPELL_IDS = frozenset({"remove_curse_c", "remove_curse_mu"})
"""The ids of the built-in *remove curse* spells, the cleric's and the magic-user's.

The session knows *remove curse* by these ids alone. Its mode is manual and has no effect, so no data marks it. An
adventure can't bundle a spell under a built-in id, so a bundled spell is never one of these, whatever its id starts
with.
"""


def _cast_outcomes(session, spell: SpellTemplate, result: CastResult, targets) -> list[Event]:
    """Add to a resolved cast what the session does and the spell kernel doesn't, and return the events.

    Every cast in a session calls this once the kernel has resolved it, from memory or from a scroll, in a battle or
    outside one, so a spell does the same thing however it's cast. It adds two outcomes:

    - A *silence 15' radius* whose target saves leaves a stationary silence in the party's cell (see
      `_stationary_silence`).
    - *Remove curse* frees each member it targets from the cursed items they carry (see `_lift_curses`). The reversed
      spell, *curse*, frees nothing. `result.reversed` says which form the kernel cast.

    Args:
        session: The session the cast happened in.
        spell: The spell that was cast.
        result: The kernel's result for the cast.
        targets: The targets the cast was given, creatures and cell references.
    """
    events = _stationary_silence(session, spell, result, targets)
    if spell.id in _REMOVE_CURSE_SPELL_IDS and not result.reversed:
        for target in targets:
            if getattr(target, "definition", None) is not None:
                events.extend(_lift_curses(session, target))
    return events


def _is_silence(spell: SpellTemplate, result: CastResult) -> bool:
    """Return whether the mode the cast resolved is *silence 15' radius*'s, known by its data and not by its id.

    That's a mode whose effect puts the `silenced` condition on its target, with `silence` as the `effect_kind`
    param. A spell an adventure bundles that copies the mode under another id is silence too.
    """
    effect = spell.mode(result.mode, reversed=result.reversed).effect
    return (
        effect is not None and effect.condition is Condition.SILENCED and effect.params.get("effect_kind") == "silence"
    )


def _stationary_silence(session, spell, result, targets) -> list[Event]:
    """*Silence 15' radius*'s save-passed outcome: the area anchors to a cell.

    On a passed save RAW leaves a stationary area the creature can step out of. As
    a documented adaptation (see the adaptations register), the effect instead
    attaches to the party's cell (the encounter's abstract location), and
    creatures in that cell cannot cast while it lasts. `_is_silence` decides
    which casts are silence.
    """
    if _location(session).kind != "dungeon" or not _is_silence(spell, result):
        return []
    passed = {
        event.target_id
        for event in result.events
        if isinstance(event, SavingThrowRolledEvent) and event.code == "combat.save.passed"
    }
    if not any(getattr(target, "id", None) in passed for target in targets):
        return []
    spec = spell.duration_spec
    definition = EffectDefinition(
        kind="silence",
        duration_unit=spec.unit or TimeUnit.TURN,
        duration_amount=(spec.amount or 12),
        dispellable=True,
        params={"radius_feet": 15, "stationary": True},
    )
    _, attach_events = session.ledger.attach(
        definition, _cell_ref(session), clock=session.clock, allocator=session.allocator, registry=session.registry()
    )
    return attach_events


def _lift_curses(session, member) -> list[Event]:
    """*Remove curse* lifts the bearer's item curses: stuck items become discardable.

    The item stays cursed and identified, the stuck marker clears, and item
    curse effects (the Ring of Weakness's STR set, the cursed scroll's slow
    healing) release.
    """
    events: list[Event] = []
    for instance in member.inventory.all_instances():
        if isinstance(instance, MagicItemInstance) and instance.cursed_revealed:
            instance.cursed_revealed = False
    for effect in list(session.ledger.active_on(member.id)):
        if effect.definition.kind in ("ring_of_weakness", "ring_of_weakness_onset", "cursed_slow_healing"):
            events.extend(session.ledger.release(effect.effect_id, session.registry()))
    return events


def _drain_character(session, character: Character, drain: Callable[[SpellCatalog], Sequence[Event]]) -> list[Event]:
    """Drain levels from a party member or an NPC adventurer, and return the drain's events.

    `drain` calls `drain_levels` or `resolve_energy_drain` and passes the catalog it gets as `spell_catalog`. The
    kernel refuses a memorized spell that isn't in that catalog, and the session leaves a spell its catalog doesn't
    have on the character (see `GameSession.effective_spells`). So `drain` runs with those copies set aside, and
    they take no slot. Afterwards each one is back in its place among the copies the drain kept.
    """
    catalog = session.effective_spells
    known = {spell.id for spell in catalog.spells}
    held = character.memorized_spells
    character.memorized_spells = tuple(copy for copy in held if copy.spell_id in known)
    try:
        return list(drain(catalog))
    finally:
        # The drain forgets the last of equal copies first, so matching from the front puts each kept copy back
        # where it was.
        kept = list(character.memorized_spells)
        restored = []
        for copy in held:
            if copy.spell_id not in known:
                restored.append(copy)
            elif kept and kept[0] == copy:
                restored.append(kept.pop(0))
        character.memorized_spells = tuple(restored)
