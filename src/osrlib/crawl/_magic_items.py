"""Using magic items, and identifying them and attaching the effects of worn ones.

`UseItem` drinks a potion, reads a scroll, activates a rod, staff, or wand, or turns a sword's light on or off. A
battle's item declarations use the same functions, and `EquipItem` and `UnequipItem` attach and release a worn item's
effects through them.
"""

from collections.abc import Mapping
from typing import Any

from osrlib.core.clock import TimeUnit
from osrlib.core.combat import SaveCategory, saving_throw
from osrlib.core.creature import Creature
from osrlib.core.dice import roll
from osrlib.core.effects import EFFECTS_STREAM, Condition, EffectDefinition, ModifierSpec
from osrlib.core.events import Event
from osrlib.core.items import MagicItemCategory, MagicItemInstance, magic_item_template, usable_by_class
from osrlib.core.spells import MAGIC_STREAM, cast_from_scroll, caster_profile, validate_scroll_cast
from osrlib.core.validation import Rejection
from osrlib.crawl._light import _requires_light
from osrlib.crawl._party_members import _member_able, _remove_instance
from osrlib.crawl._spells import _cast_context, _cast_outcomes, _drain_character
from osrlib.crawl.commands import SessionMode, UseItem
from osrlib.crawl.events import CurseRevealedEvent, ItemIdentifiedEvent, ItemUsedEvent, LightEvent
from osrlib.crawl.party import _stands


def _int_param(params: Mapping[str, Any], key: str, default: int = 0) -> int:
    """Read an integer param: schema-validated data whose union type the type checker can't index by key name."""
    return int(params.get(key, default))


def _identify_item_events(session, member, instance: MagicItemInstance) -> list:
    """Identify an item (and reveal its curse) on its first meaningful use.

    Cursed items identify as their true nature at the same trigger and stick to
    their bearer (`items.curse.stuck` on unequip, drop, and sale) until *remove
    curse*. The armour that reads as +1 gives itself away at first battle use,
    which is when the SRD rules reveal it.
    """
    template = magic_item_template(instance)
    events: list[Event] = []
    if not instance.identified:
        instance.identified = True
        events.append(ItemIdentifiedEvent(instance_id=instance.instance_id, template_id=instance.template_id))
    if template.cursed and not instance.cursed_revealed:
        instance.cursed_revealed = True
        events.append(
            CurseRevealedEvent(
                character_id=member.id, instance_id=instance.instance_id, template_id=instance.template_id
            )
        )
    return events


def _release_instance_effects(session, instance: MagicItemInstance) -> list:
    """Release the ledger effects a worn item attached (its state records them)."""
    events: list[Event] = []
    effect_ids = instance.state.get("effect_ids", ())
    for effect_id in effect_ids if isinstance(effect_ids, tuple) else ():
        if any(effect.effect_id == effect_id for effect in session.ledger.effects):
            events.extend(session.ledger.release(str(effect_id), session.registry()))
    if "effect_ids" in instance.state:
        instance.state = {key: value for key, value in instance.state.items() if key != "effect_ids"}
    return events


def _attach_worn_item_effects(session, member, instance: MagicItemInstance) -> list:
    """Attach a worn item's ledger effects: the regeneration ring, the weakness onset.

    Item-sourced ledger effects attach undispellable (RAW's dispel exemption for
    items), and the instance's state records the effect ids so unequipping releases
    them. A cursed ring reveals when its curse takes effect. The onset is
    automatic at wearing, so the reveal is too (RAW: "the ring cannot be removed,
    once worn").
    """
    template = magic_item_template(instance)
    events: list[Event] = []
    if template.effect is None:
        return events
    definition = None
    if template.effect.kind == "regeneration":
        definition = EffectDefinition(
            kind="regeneration",
            tick="regeneration",
            stacking="ignore",
            params={
                str(key): value if not isinstance(value, list) else tuple(value)
                for key, value in template.effect.params.items()
            },
        )
    elif template.effect.kind == "weakness":
        events.extend(_identify_item_events(session, member, instance))
        definition = EffectDefinition(
            kind="ring_of_weakness_onset",
            duration_unit=TimeUnit.ROUND,
            duration_amount=_int_param(template.effect.params, "onset_rounds"),
            expiry="weakness_strength_set",
            params={"strength_set": _int_param(template.effect.params, "strength_set")},
        )
    if definition is None:
        return events
    effect, attach_events = session.ledger.attach(
        definition, member.id, clock=session.clock, allocator=session.allocator, registry=session.registry()
    )
    events.extend(attach_events)
    if effect is not None:
        held = instance.state.get("effect_ids", ())
        held_ids = held if isinstance(held, tuple) else ()
        instance.state = {**instance.state, "effect_ids": (*held_ids, effect.effect_id)}
    return events


def _potion_effect_definition(template, instance) -> EffectDefinition:
    """Build a potion's timed item effect: 1d6+6 turns, undispellable, referee-tracked."""
    effect = template.effect
    params: dict[str, int | str | bool | tuple[int | str, ...]] = {
        str(key): value for key, value in effect.params.items() if key != "effect_kind"
    }
    params["item_source"] = "potion"
    condition = Condition(effect.condition) if effect.condition is not None else None
    return EffectDefinition(
        kind=str(effect.params.get("effect_kind", template.id)),
        duration_unit=TimeUnit.TURN,
        duration_dice="1d6+6",
        condition=condition,
        modifiers=effect.modifiers,
        stacking="stack",
        params=params,
    )


def _potion_drinker(session, carrier, instance: MagicItemInstance, target_id: str | None):
    """Return who drinks a potion the carrier uses, with every reason they can't, as a `(drinker, rejections)` pair.

    With no `target_id`, or the carrier's own id, the carrier drinks. Any other id has to be a party member's,
    or the use gets `items.use.target_not_allowed`. A member who is dead or turned to stone can't drink, and the
    use gets `items.use.target_cannot_drink`. A member who is asleep or paralysed can be given a potion to drink.
    Both rejections have the potion's instance id as `item` and the id as `target`. The drinker is None when
    there are rejections. A battle adds a check of reach (see `battle._drinker`). The call reads state and takes
    no draw.
    """
    if target_id is None or target_id == carrier.id:
        return carrier, []
    drinker = next((member for member in session.party.members if member.id == target_id), None)
    if drinker is None:
        return None, [
            Rejection(code="items.use.target_not_allowed", params={"item": instance.instance_id, "target": target_id})
        ]
    if not _stands(drinker):
        return None, [
            Rejection(code="items.use.target_cannot_drink", params={"item": instance.instance_id, "target": target_id})
        ]
    return drinker, []


def _healing_target(session, user, item_id: str, target_id: str | None):
    """Return whom a staff of healing heals, with every reason it can't, as a `(target, rejections)` pair.

    With no `target_id`, or the user's own id, the staff heals its user. Any other id has to be a creature's, or
    the use gets `items.use.unknown_target` with the id as `target`. A creature that's dead or turned to stone can't
    be healed, and the use gets `items.use.target_cannot_be_healed`, with the staff's instance id as `item` and the
    id as `target`. [`UseItem`][osrlib.crawl.commands.UseItem] takes these checks, and a battle adds checks of reach
    (see `battle._healing_touch`). The target is None when there are rejections. The call reads state and takes no
    draw.
    """
    if target_id is None or target_id == user.id:
        return user, []
    target = session.registry().get(target_id)
    if target is None:
        return None, [Rejection(code="items.use.unknown_target", params={"target": target_id})]
    if not _stands(target):
        return None, [
            Rejection(code="items.use.target_cannot_be_healed", params={"item": item_id, "target": target_id})
        ]
    return target, []


def _use_potion(session, carrier, drinker, instance: MagicItemInstance, template) -> list[Event]:
    """Use up a potion the carrier carries, and apply its effect to the drinker, who can be the carrier.

    The potion leaves the carrier's pack, and the carrier's use identifies it. The drinker gets the potion's
    effect, its mixing with another potion still in effect on them, and its weekly limit. Its events report
    the carrier in `character_id`, and the drinker in `target_id` when the drinker is another member.
    """
    from osrlib.core.combat import apply_healing

    stream = session.streams.get(MAGIC_STREAM)
    events: list[Event] = []
    effect_spec = template.effect
    instantaneous = effect_spec is not None and bool(effect_spec.params.get("instantaneous"))
    fed_to = drinker.id if drinker is not carrier else None
    active_potions = [
        effect
        for effect in session.ledger.effects
        if effect.target_ref == drinker.id and effect.definition.params.get("item_source") == "potion"
    ]
    _remove_instance(carrier, instance)
    events.extend(_identify_item_events(session, carrier, instance))
    if active_potions and not instantaneous and effect_spec is not None:
        # Mixing applies both printed consequences: both effects cancel and the
        # drinker is disabled for 3 turns. Neither applies to an instantaneous or
        # permanent potion.
        events.append(
            ItemUsedEvent(
                code="items.potion.mixed",
                character_id=carrier.id,
                instance_id=instance.instance_id,
                target_id=fed_to,
            )
        )
        for effect in active_potions:
            events.extend(session.ledger.release(effect.effect_id, session.registry()))
        sickness = EffectDefinition(
            kind="potion_sickness",
            duration_unit=TimeUnit.TURN,
            duration_amount=3,
            condition=Condition.PARALYSED,
            params={"item_source": "potion_sickness"},
        )
        _, attach_events = session.ledger.attach(
            sickness, drinker.id, clock=session.clock, allocator=session.allocator, registry=session.registry()
        )
        events.extend(attach_events)
        return events
    events.append(
        ItemUsedEvent(
            code="items.potion.drunk",
            character_id=carrier.id,
            instance_id=instance.instance_id,
            manual=template.manual if effect_spec is None else (),
            target_id=fed_to,
        )
    )
    if effect_spec is None:
        return events
    if effect_spec.kind == "healing":
        paralysing = [
            effect
            for effect in session.ledger.active_on(drinker.id)
            if effect.definition.condition is Condition.PARALYSED
        ]
        if paralysing:
            # The potion's second usage: paralysing effects are negated instead.
            for effect in paralysing:
                events.extend(session.ledger.release(effect.effect_id, session.registry()))
        else:
            amount = roll(str(effect_spec.heal_dice), stream).total
            events.extend(apply_healing(drinker, amount, source="magical"))
        return events
    if effect_spec.kind == "save_or_die":
        save = saving_throw(drinker, SaveCategory(str(effect_spec.save_category)), magical=True, stream=stream)
        events.extend(save.events)
        if not save.passed:
            from osrlib.core.effects import kill

            events.extend(kill(drinker))
        return events
    definition = _potion_effect_definition(template, instance)
    if effect_spec.params.get("weekly_inversion"):
        # More than one in a week inverts the benefits. The week runs per character
        # on the clock's day, tracked as a ledger marker so it serializes.
        cooldown_active = bool(session.ledger.active_on(drinker.id, "invulnerability_cooldown"))
        if cooldown_active:
            inverted = tuple(spec.model_copy(update={"value": -spec.value}) for spec in definition.modifiers)
            definition = definition.model_copy(update={"modifiers": inverted})
        marker = EffectDefinition(
            kind="invulnerability_cooldown", duration_unit=TimeUnit.DAY, duration_amount=7, stacking="refresh"
        )
        _, marker_events = session.ledger.attach(
            marker, drinker.id, clock=session.clock, allocator=session.allocator, registry=session.registry()
        )
        events.extend(marker_events)
    _, attach_events = session.ledger.attach(
        definition,
        drinker.id,
        clock=session.clock,
        allocator=session.allocator,
        registry=session.registry(),
        stream=session.streams.get(EFFECTS_STREAM),
    )
    events.extend(attach_events)
    return events


def _thief_scroll_use(definition) -> dict | None:
    for ability in definition.abilities:
        if ability.tag == "scroll_use":
            return ability.params
    return None


def _use_scroll(session, member, instance: MagicItemInstance, template, command) -> tuple[list[Rejection], list]:
    light_rejections = _requires_light(session, member, infravision_suffices=False)
    if light_rejections:
        return light_rejections, []
    stream = session.streams.get(MAGIC_STREAM)
    events: list[Event] = []
    if template.cursed:
        # Looking at a cursed scroll curses the reader, whatever their class.
        _remove_instance(member, instance)
        events.extend(_identify_item_events(session, member, instance))
        curse = template.curses[session.streams.get(EFFECTS_STREAM).randbelow(len(template.curses))]
        events.append(
            ItemUsedEvent(
                code="items.scroll.cursed",
                character_id=member.id,
                instance_id=instance.instance_id,
                manual=(f"{curse.name}: {curse.prose}",) if not curse.wired else (),
            )
        )
        if curse.id == "energy_drain":
            from osrlib.core.character import ADVANCEMENT_STREAM
            from osrlib.core.classes import drain_levels

            stream = session.streams.get(ADVANCEMENT_STREAM)
            events.extend(
                _drain_character(
                    session,
                    member,
                    lambda spells: (
                        drain_levels(
                            member,
                            member.definition,
                            levels=1,
                            xp_policy="halfway",
                            stream=stream,
                            spell_catalog=spells,
                        ).events
                    ),
                )
            )
        elif curse.id == "slow_healing":
            # Not a disease: the curse halves healing spells where *cause
            # disease* voids them, so DISEASED (the magical-healing block)
            # would overshoot RAW. Natural healing doubles via the rest-day
            # cadence, magical halves via the modifier.
            slow = EffectDefinition(
                kind="cursed_slow_healing",
                modifiers=(ModifierSpec(kind="magical_healing_half", value=1, from_item=True),),
                params={"healing_rest_days": 2, "item_source": "cursed_scroll"},
            )
            _, attach_events = session.ledger.attach(
                slow, member.id, clock=session.clock, allocator=session.allocator, registry=session.registry()
            )
            events.extend(attach_events)
        return [], events
    if template.effect is not None and template.effect.kind == "ward":
        _remove_instance(member, instance)
        events.extend(_identify_item_events(session, member, instance))
        events.append(ItemUsedEvent(code="items.scroll.read", character_id=member.id, instance_id=instance.instance_id))
        params: dict[str, int | str | bool | tuple[int | str, ...]] = {"party_wide": True}
        for key in ("targets", "bars_categories", "bars_template_ids"):
            if key in template.effect.params:
                params[key] = template.effect.params[key]
        for band in template.effect.params.get("bands", ()):
            band_label, _, dice = str(band).partition(":")
            params[f"affected_{band_label.replace('-', '_').replace('+', '_plus')}"] = roll(dice, stream).total
        if template.effect.params.get("all_affected"):
            params["all_affected"] = True
        ward = EffectDefinition(
            kind="protection_ward",
            duration_unit=TimeUnit(str(template.effect.duration_unit)),
            duration_amount=template.effect.duration_amount,
            params=params,
        )
        _, attach_events = session.ledger.attach(
            ward, member.id, clock=session.clock, allocator=session.allocator, registry=session.registry()
        )
        events.extend(attach_events)
        return [], events
    if "spell_count" in template.params:
        inscribed = instance.state.get("spells", ())
        remaining = tuple(str(spell) for spell in inscribed) if isinstance(inscribed, tuple) else ()
        if not remaining:
            return [Rejection(code="items.scroll.spent", params={"item": instance.instance_id})], []
        spell_id = command.spell_id or remaining[0]
        if spell_id not in remaining:
            return [Rejection(code="items.scroll.no_such_spell", params={"spell": spell_id})], []
        spell = session.effective_spells.get(spell_id)
        definition = member.definition
        profile = caster_profile(definition)
        thief_params = _thief_scroll_use(definition)
        divine_scroll = instance.state.get("spell_list") == "cleric"
        thief_reading = False
        if divine_scroll:
            if profile is None or profile.kind != "divine":
                return [Rejection(code="items.scroll.wrong_caster", params={"item": instance.instance_id})], []
        elif profile is None or profile.kind != "arcane":
            if thief_params is not None and member.level >= int(thief_params.get("min_level", 10)):
                thief_reading = True
            else:
                return [Rejection(code="items.scroll.wrong_caster", params={"item": instance.instance_id})], []
        mode = command.mode or spell.modes[0].key
        registry = session.registry()
        targets: list[Creature | str] = []
        target_refs = command.targets or ((command.target_id,) if command.target_id else ())
        for target_ref in target_refs:
            if target_ref.startswith("cell:"):
                targets.append(target_ref)
            elif target_ref in registry:
                targets.append(registry[target_ref])
            else:
                return [Rejection(code="magic.cast.unknown_target", params={"target": target_ref})], []
        context = _cast_context(session, targets, in_combat=False)
        # The kernel's pre-check, asked about a caster at the scroll's level, which is
        # the level `cast_from_scroll` resolves at. This handler refuses here what the kernel
        # would refuse there, before the scroll is spent.
        cast_rejections = validate_scroll_cast(
            member,
            spell,
            mode,
            targets=targets,
            context=context,
            ledger=session.ledger,
        )
        if cast_rejections:
            return cast_rejections, []
        left = tuple(spell for spell in remaining if spell != spell_id) + tuple(
            spell_id for _ in range(remaining.count(spell_id) - 1)
        )
        if left:
            instance.state = {**instance.state, "spells": left}
        else:
            _remove_instance(member, instance)
        events.extend(_identify_item_events(session, member, instance))
        events.append(ItemUsedEvent(code="items.scroll.read", character_id=member.id, instance_id=instance.instance_id))
        if thief_reading and thief_params is not None:
            error_pct = _int_param(thief_params, "error_pct", 10)
            if stream.randbelow(100) + 1 <= error_pct:
                # The 10% error resolves as a fizzle that consumes the spell.
                # RAW leaves the "unusual or deleterious effect" to the referee,
                # so osrlib picks the fizzle and records it in the adaptations
                # register.
                return [], events
        result = cast_from_scroll(
            member,
            spell,
            mode,
            targets=targets,
            context=context,
            ledger=session.ledger,
            clock=session.clock,
            allocator=session.allocator,
            registry=registry,
            ruleset=session.ruleset,
            stream=stream,
            effects_stream=session.streams.get(EFFECTS_STREAM),
        )
        events.extend(result.events)
        events.extend(_cast_outcomes(session, spell, result, targets))
        return [], events
    # The engine resolves no other scroll, like protection from magic or a treasure
    # map. Reading one is allowed, and the event includes its `manual` text for the
    # game to narrate.
    _remove_instance(member, instance)
    events.extend(_identify_item_events(session, member, instance))
    events.append(
        ItemUsedEvent(
            code="items.scroll.read",
            character_id=member.id,
            instance_id=instance.instance_id,
            manual=template.manual,
        )
    )
    return [], events


def _device_area_events(session, member, instance: MagicItemInstance, template, group) -> list:
    """Resolve a device's area effect (cone or sphere) against one encounter group.

    Cones and spheres resolve through the battle footprint rule, and each caught
    target saves per the item's spec. Device damage presents `magic` and is
    destructive: the SRD's equipment-destruction examples are all energy-type
    deaths.
    """
    from osrlib.core.combat import DamageSource, deal_damage
    from osrlib.crawl import battle as battle_module

    effect_spec = template.effect
    stream = session.streams.get(MAGIC_STREAM)
    events: list[Event] = []
    candidates = battle_module._area_candidates(
        session, group, effect_spec.shape, dict(effect_spec.dimensions), caster_id=member.id
    )
    for target in candidates:
        save = saving_throw(
            target,
            SaveCategory(str(effect_spec.save_category)),
            magical=True,
            element=effect_spec.element,
            stream=stream,
        )
        events.extend(save.events)
        if effect_spec.kind == "damage_area":
            source = DamageSource(
                keys=("magic",), element=effect_spec.element, magical=True, kind="device", destructive=True
            )
            result = roll(str(effect_spec.damage_dice), stream)
            amount = result.total
            if save.passed:
                amount //= 2
            if amount < 1:
                continue
            events.extend(
                deal_damage(
                    target,
                    amount,
                    source=source,
                    attacker_id=member.id,
                    rolls=result.rolls,
                    clock=session.clock,
                    ruleset=session.ruleset,
                    stream=stream,
                )
            )
        else:
            if save.passed:
                continue
            condition = Condition(str(effect_spec.condition))
            definition = EffectDefinition(
                kind=str(effect_spec.params.get("effect_kind", template.id)),
                duration_unit=TimeUnit(str(effect_spec.duration_unit)),
                duration_amount=effect_spec.duration_amount,
                condition=condition,
                params={"item_source": "device"},
            )
            _, attach_events = session.ledger.attach(
                definition,
                str(getattr(target, "id", target)),
                clock=session.clock,
                allocator=session.allocator,
                registry=session.registry(),
            )
            events.extend(attach_events)
    return events


def _spend_device_charge(instance: MagicItemInstance, template) -> None:
    """Spend one charge, leaving the item inert at zero and emitting no event for it (RAW)."""
    if template.charges_dice is not None and instance.charges_remaining is not None:
        instance.charges_remaining -= 1


def _left_to_referee(template, *, strikes_a_group: bool) -> bool:
    """Return whether a use of a device is one the session leaves to the referee, with the device's `manual` text.

    That's every use of a device with no effect the session resolves. It's also a use of a staff that strikes and
    has other powers, like the staff of power, when the use doesn't strike a group: a `use_item` declaration that
    has no `target_group_id`, or any use outside a battle. The template's `manual_powers` param marks such a staff.

    Args:
        template: The device's template.
        strikes_a_group: Whether the use has a group to strike, a `use_item` declaration's `target_group_id`.
    """
    effect = template.effect
    if effect is None:
        return True
    return effect.kind == "striking" and not strikes_a_group and bool(template.params.get("manual_powers"))


def _use_device(session, member, instance: MagicItemInstance, template, command) -> tuple[list[Rejection], list]:
    from osrlib.core.combat import apply_healing

    definition = member.definition
    if not usable_by_class(template, definition):
        return [Rejection(code="items.use.not_usable", params={"item": instance.instance_id})], []
    if template.charges_dice is not None and (instance.charges_remaining or 0) <= 0:
        return [Rejection(code="items.device.inert", params={"item": instance.instance_id})], []
    effect_spec = template.effect
    referee = _left_to_referee(template, strikes_a_group=False)
    group = None
    if effect_spec is not None and effect_spec.kind in ("damage_area", "condition_area"):
        if session.encounter is not None:
            if command.target_id is None:
                return [Rejection(code="items.use.target_required", params={"item": instance.instance_id})], []
            group = next((entry for entry in session.encounter.groups if entry.id == command.target_id), None)
            if group is None or group.fled:
                return [Rejection(code="items.use.unknown_target", params={"target": command.target_id or ""})], []
    if effect_spec is not None and effect_spec.kind == "striking" and not referee:
        return [Rejection(code="items.use.battle_only", params={"item": instance.instance_id})], []
    target: Any = None
    if effect_spec is not None and effect_spec.kind == "healing":
        # Resolve the touch target before anything mutates: a rejected command
        # mutates nothing, and identification below is a mutation.
        target, rejections = _healing_target(session, member, instance.instance_id, command.target_id)
        if rejections:
            return rejections, []
    events: list[Event] = []
    events.extend(_identify_item_events(session, member, instance))
    events.append(
        ItemUsedEvent(
            code="items.device.activated",
            character_id=member.id,
            instance_id=instance.instance_id,
            manual=template.manual if referee else (),
        )
    )
    if effect_spec is not None and effect_spec.kind == "healing":
        day_key = f"healed:{getattr(target, 'id', command.target_id)}"
        today = session.clock.days
        if effect_spec.params.get("once_per_target_per_day") and instance.state.get(day_key) == today:
            # RAW: effective on any individual at most once per day. The
            # activation still happens, and nothing more heals.
            pass
        else:
            instance.state = {**instance.state, day_key: today}
            amount = roll(str(effect_spec.heal_dice), session.streams.get(MAGIC_STREAM)).total
            events.extend(apply_healing(target, amount, source="magical"))
    elif group is not None:
        events.extend(_device_area_events(session, member, instance, template, group))
    _spend_device_charge(instance, template)
    return [], events


def _toggle_sword_light(session, member, instance: MagicItemInstance, template) -> tuple[list[Rejection], list]:
    """The Sword +1, Light command toggle, through the light-effect machinery."""
    events: list[Event] = []
    events.extend(_identify_item_events(session, member, instance))
    active = [
        effect
        for effect in session.ledger.active_on(member.id, "light")
        if effect.definition.params.get("source") == "sword"
    ]
    if active:
        for effect in active:
            events.extend(session.ledger.release(effect.effect_id, session.registry()))
        events.append(LightEvent(code="exploration.light.extinguished", character_id=member.id, source="sword"))
        return [], events
    definition = EffectDefinition(
        kind="light",
        params={
            "source": "sword",
            "light_radius_feet": int(template.effect.params.get("light_radius_feet", 30)),
            "brightness": "flame",
        },
    )
    _, attach_events = session.ledger.attach(
        definition, member.id, clock=session.clock, allocator=session.allocator, registry=session.registry()
    )
    events.extend(attach_events)
    events.append(LightEvent(code="exploration.light.lit", character_id=member.id, source="sword"))
    return [], events


def _handle_use_item(session, command: UseItem) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    instance = member.inventory.magic_item(command.item_id)
    if instance is None:
        return [Rejection(code="exploration.item.not_carried", params={"item": command.item_id})], []
    template = magic_item_template(instance)
    if template.category is MagicItemCategory.POTION:
        drinker, rejections = _potion_drinker(session, member, instance, command.target_id)
        events = [] if drinker is None else _use_potion(session, member, drinker, instance, template)
    elif template.category is MagicItemCategory.SCROLL:
        rejections, events = _use_scroll(session, member, instance, template, command)
    elif template.category in (MagicItemCategory.ROD, MagicItemCategory.STAFF, MagicItemCategory.WAND):
        rejections, events = _use_device(session, member, instance, template, command)
    elif template.effect is not None and template.effect.kind == "light":
        rejections, events = _toggle_sword_light(session, member, instance, template)
    else:
        return [Rejection(code="items.use.not_usable", params={"item": command.item_id})], []
    if rejections:
        return rejections, []
    if session.mode is SessionMode.ENCOUNTER:
        from osrlib.crawl import encounter as encounter_module

        events.extend(encounter_module._pass_round(session))
    else:
        events.extend(session.advance_rounds(1))
    return [], events
