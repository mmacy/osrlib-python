"""The exploration turn loop: movement, time, doors, searching, traps, light, rest.

Every handler here is one function `(session, command) -> (rejections, events)`
under the pure pre-phase discipline: all validation happens before the first draw,
mutation, or clock tick, so a rejected command costs nothing.

Time bookkeeping, pinned: the odometer accrues movement in thirds-of-feet
(integers — an unexplored cell costs 30 units, a previously explored cell 10,
implementing the SRD's "three times their base movement rate" through familiar
areas exactly); when the accrued total reaches 3 × the party's exploration rate,
the clock advances one full turn and the odometer resets. Turn-costing actions
advance one whole turn and reset the odometer, absorbing the partial move.

Trap resolution draws (the 2-in-6 spring check, saves, damage, volley counts) run
on the exploration stream — the procedure owns its dice; attach-time duration dice
stay on the effects stream per the Phase 2 convention (pinned, registered).
"""

from osrlib.core.classes import detection_chance, detection_check, thief_skill_check
from osrlib.core.clock import TimeUnit
from osrlib.core.combat import (
    DamageSource,
    SaveCategory,
    burning_oil_pool_definition,
    cannot_move,
    deal_damage,
    falling_damage,
    incapacitated,
    natural_healing,
    saving_throw,
)
from osrlib.core.dice import roll
from osrlib.core.effects import EFFECTS_STREAM, Condition, EffectDefinition, ModifierSpec
from osrlib.core.events import Event
from osrlib.core.items import (
    Coins,
    ItemInstance,
    MagicItemCategory,
    MagicItemInstance,
    equip,
    magic_item_template,
    unequip,
    usable_by_class,
    validate_equip,
    validate_unequip,
)
from osrlib.core.spells import (
    MAGIC_STREAM,
    CastContext,
    cast_from_scroll,
    cast_spell,
    caster_profile,
    memorize_spells,
    validate_cast,
)
from osrlib.core.validation import Rejection
from osrlib.crawl.commands import (
    CastSpell,
    CloseDoor,
    DropItems,
    EnterDungeon,
    EquipItem,
    ExtinguishSource,
    ForceDoor,
    InspectTreasure,
    LightSource,
    ListenAtDoor,
    MoveParty,
    OpenDoor,
    PickLock,
    PrepareSpells,
    PurchaseEquipment,
    PurchaseHealing,
    RemoveTreasureTrap,
    ReorderParty,
    Rest,
    Search,
    SellTreasure,
    SessionMode,
    TakeTreasure,
    TravelToTown,
    TurnParty,
    UnequipItem,
    UseItem,
    UseStairs,
    WedgeDoor,
)
from osrlib.crawl.dungeon import (
    Direction,
    DroppedItem,
    DropPile,
    EdgeKind,
    FeatureSpec,
    PartyLocation,
    TrapSpec,
    cell_ref,
    edge_ref,
    step,
)
from osrlib.crawl.events import (
    CurseRevealedEvent,
    DetectionRolledEvent,
    DoorEvent,
    FatigueEvent,
    HealingPurchasedEvent,
    ItemAcquiredEvent,
    ItemIdentifiedEvent,
    ItemsDroppedEvent,
    ItemUsedEvent,
    LightEvent,
    ListenedEvent,
    LocationEnteredEvent,
    PartyMovedEvent,
    ProvisionsEvent,
    RestedEvent,
    SearchCompletedEvent,
    TrapEvent,
    TreasureSoldEvent,
    WanderingCheckEvent,
)
from osrlib.data import load_classes, load_encounter_tables, load_equipment, load_monsters, load_spells

__all__ = [
    "HANDLERS",
    "check_fatigue",
    "consume_provisions",
    "wandering_check",
    "wandering_interval",
]

FATIGUE_KIND = "fatigue"
EXHAUSTED_KIND = "exhausted"
DEPRIVATION_KIND = "deprivation"

_FATIGUE_DEFINITION = EffectDefinition(
    kind=FATIGUE_KIND,
    stacking="ignore",
    modifiers=(
        ModifierSpec(kind="attack_bonus", value=-1),
        ModifierSpec(kind="damage_bonus", value=-1),
    ),
)

# Exhaustion's −2 to AC rides `attack_penalty_of_attackers` +2: attackers of the
# exhausted creature gain +2, which is exactly descending AC worsened by 2.
EXHAUSTED_DEFINITION = EffectDefinition(
    kind=EXHAUSTED_KIND,
    condition=Condition.EXHAUSTED,
    stacking="ignore",
    modifiers=(
        ModifierSpec(kind="attack_bonus", value=-2),
        ModifierSpec(kind="damage_bonus", value=-2),
        ModifierSpec(kind="attack_penalty_of_attackers", value=2),
    ),
)

_DEPRIVATION_DEFINITION = EffectDefinition(
    kind=DEPRIVATION_KIND,
    stacking="ignore",
    modifiers=(ModifierSpec(kind="attack_bonus", value=-1),),
)


# ---------------------------------------------------------------------- location helpers


def _location(session) -> PartyLocation:
    return session.dungeon_state.location


def _level(session):
    location = _location(session)
    return session.adventure.dungeon(location.dungeon_id).level(location.level_number)


def _position(session) -> tuple[int, int]:
    return _location(session).position


def _area_ref(session, area_id: str) -> str:
    location = _location(session)
    return f"{location.dungeon_id}:{location.level_number}:{area_id}"


def _cell_ref(session, position=None) -> str:
    location = _location(session)
    return cell_ref(location.dungeon_id, location.level_number, position or location.position)


def _edge_ref(session, direction: Direction) -> str:
    location = _location(session)
    return edge_ref(location.dungeon_id, location.level_number, location.position, direction)


def _door_state(session, direction: Direction):
    """The overlay entry for the door on one side of the party's cell.

    Initialized from the authored `starts_open` on first touch; a door forced or
    opened by the party marks `opened_by_party`, which is what the swing-shut
    rule watches.
    """
    edge = _level(session).edge(_position(session), direction)
    ref = _edge_ref(session, direction)
    state = session.dungeon_state.doors.get(ref)
    if state is None:
        state = session.dungeon_state.door(ref)
        if edge.kind is EdgeKind.DOOR and edge.door.starts_open:
            state.open = True
    return state


def _known_door(session, direction: Direction):
    """Return `(edge, state)` when a known door faces `direction`, else `None`.

    An undiscovered secret door is a wall to the party — commands against it
    reject exactly as against blank stone (no leak).
    """
    edge = _level(session).edge(_position(session), direction)
    if edge.kind is not EdgeKind.DOOR:
        return None
    state = _door_state(session, direction)
    if edge.door.kind == "secret" and not state.discovered:
        return None
    return edge, state


# ---------------------------------------------------------------------- light gates


def _requires_light(session, member, *, infravision_suffices: bool) -> list[Rejection]:
    lit, infravision_allowed = session.party_light()
    if lit:
        return []
    if infravision_suffices and infravision_allowed and session.member_has_infravision(member):
        return []
    return [Rejection(code="exploration.action.requires_light", params={"character": member.id})]


def _member_able(session, character_id: str) -> tuple[object | None, list[Rejection]]:
    try:
        member = session.member(character_id)
    except ValueError:
        return None, [Rejection(code="session.command.unknown_member", params={"character": character_id})]
    if incapacitated(member):
        return None, [Rejection(code="session.command.member_incapacitated", params={"character": character_id})]
    return member, []


# ---------------------------------------------------------------------- time and cadence


def exploration_rate(session) -> int:
    """The party's exploration rate: slowest living member, deprivation-halved.

    Under the `deprivation_penalties` flag, a member two or more days into the
    worse deprivation track moves at half rate.
    """
    rates = []
    for member in session.party.living_members():
        rate = member.movement_rate(session.ruleset)
        if session.ruleset.deprivation_penalties:
            state = session.deprivation.get(member.id)
            if state is not None and state.worst >= 2:
                rate //= 2
        rates.append(rate)
    return min(rates, default=0)


def _accrue_movement(session, units: int) -> list[Event]:
    """Accrue odometer units; a full turn's worth advances the clock one turn."""
    session.odometer_thirds += units
    if session.odometer_thirds >= 3 * max(1, exploration_rate(session)):
        session.odometer_thirds = 0
        events, _ = session.advance_turns(1)
        return events
    return []


def _spend_turn(session, *, resting: bool = False) -> tuple[list[Event], bool]:
    """Advance one whole turn, absorbing the partial move (odometer reset)."""
    session.odometer_thirds = 0
    return session.advance_turns(1, resting=resting)


def _fatigue_threshold(session) -> int:
    """Six unrested turns; three when any living member is a day deprived (flag on)."""
    if session.ruleset.deprivation_penalties:
        for member in session.party.living_members():
            state = session.deprivation.get(member.id)
            if state is not None and state.worst >= 1:
                return 3
    return 6


def check_fatigue(session) -> list[Event]:
    """Attach the unrested-fatigue penalty once the cadence threshold passes."""
    if session.turns_since_rest < _fatigue_threshold(session):
        return []
    living = session.party.living_members()
    if any(session.ledger.active_on(member.id, FATIGUE_KIND) for member in living):
        return []
    events: list[Event] = []
    for member in living:
        _, attach_events = session.ledger.attach(
            _FATIGUE_DEFINITION,
            member.id,
            clock=session.clock,
            allocator=session.allocator,
            registry=session.registry(),
        )
        events.extend(attach_events)
    events.append(FatigueEvent(code="exploration.fatigue.gained"))
    return events


def _clear_fatigue(session) -> list[Event]:
    events: list[Event] = []
    cleared = False
    for member in session.party.members:
        for effect in list(session.ledger.active_on(member.id, FATIGUE_KIND)):
            events.extend(session.ledger.release(effect.effect_id, session.registry()))
            cleared = True
    session.turns_since_rest = 0
    if cleared:
        events.append(FatigueEvent(code="exploration.fatigue.recovered"))
    return events


def _credit_exhaustion_rest(session, rest_turns: int) -> list[Event]:
    """Credit rest turns against running exhaustion; three full turns clear it."""
    events: list[Event] = []
    recovered = False
    for member in session.party.members:
        for effect in list(session.ledger.active_on(member.id, EXHAUSTED_KIND)):
            effect.state["rest_turns"] = effect.state.get("rest_turns", 0) + rest_turns
            if effect.state["rest_turns"] >= 3:
                events.extend(session.ledger.release(effect.effect_id, session.registry()))
                recovered = True
    if recovered:
        from osrlib.crawl.events import ExhaustionEvent

        events.append(ExhaustionEvent(code="encounter.exhaustion.recovered"))
    return events


def consume_provisions(session) -> list[Event]:
    """One day-boundary crossing: rations and water per living member.

    Standard rations consume before iron (fresh food spoils first, pinned); a
    carried waterskin satisfies the day (per-pint bookkeeping is below the
    simulation floor, pinned). In town, provisions consume but never run short.
    A successful day resets that deprivation track; under the flag, the schedule's
    effects sync afterwards.
    """
    events: list[Event] = []
    in_town = _location(session).kind == "town"
    for member in session.party.living_members():
        state = session.deprivation.get(member.id)
        if state is None:
            state = _new_deprivation()
            session.deprivation[member.id] = state
        # Food: standard before iron.
        if _consume_item(member, "rations_standard") or _consume_item(member, "rations_iron") or in_town:
            state.food_days = 0
            events.append(ProvisionsEvent(code="exploration.provisions.consumed", character_id=member.id, kind="food"))
        else:
            state.food_days += 1
            events.append(ProvisionsEvent(code="exploration.provisions.short", character_id=member.id, kind="food"))
        if _find_item(member, "waterskin") is not None or in_town:
            state.water_days = 0
            events.append(ProvisionsEvent(code="exploration.provisions.consumed", character_id=member.id, kind="water"))
        else:
            state.water_days += 1
            events.append(ProvisionsEvent(code="exploration.provisions.short", character_id=member.id, kind="water"))
        if session.ruleset.deprivation_penalties:
            events.extend(_sync_deprivation(session, member, state))
    return events


def _new_deprivation():
    from osrlib.crawl.session import DeprivationState

    return DeprivationState()


def _sync_deprivation(session, member, state) -> list[Event]:
    """Apply the pinned deprivation schedule: −1 attack at one day, 1d4/day at three."""
    events: list[Event] = []
    active = session.ledger.active_on(member.id, DEPRIVATION_KIND)
    if state.worst >= 1 and not active:
        _, attach_events = session.ledger.attach(
            _DEPRIVATION_DEFINITION,
            member.id,
            clock=session.clock,
            allocator=session.allocator,
            registry=session.registry(),
        )
        events.extend(attach_events)
    elif state.worst == 0 and active:
        for effect in list(active):
            events.extend(session.ledger.release(effect.effect_id, session.registry()))
    if state.worst >= 3:
        result = roll("1d4", session.streams.get(EFFECTS_STREAM))
        events.extend(
            deal_damage(
                member,
                result.total,
                source=DamageSource(kind="deprivation"),
                rolls=result.rolls,
                clock=session.clock,
            )
        )
    return events


def _consume_item(member, item_id: str, quantity: int = 1) -> bool:
    instance = _find_item(member, item_id)
    if instance is None or instance.quantity < quantity:
        return False
    if instance.quantity == quantity:
        _remove_instance(member, instance)
    else:
        instance.quantity -= quantity
    return True


def _find_item(member, item_id: str):
    for instance in member.inventory.all_instances():
        if instance.template.id == item_id:
            return instance
    return None


def _remove_instance(member, instance) -> None:
    inventory = member.inventory
    if any(existing is instance for existing in inventory.items):
        inventory.items.remove(instance)
    elif any(existing is instance for existing in inventory.wielded):
        inventory.wielded.remove(instance)
    elif inventory.worn_armour is instance:
        inventory.worn_armour = None
    elif inventory.shield is instance:
        inventory.shield = None


# ---------------------------------------------------------------------- wandering


def wandering_interval(session) -> int:
    """The current level's wandering-check interval in turns (RAW default 2)."""
    if _location(session).kind != "dungeon":
        return 10**9
    return _level(session).wandering.interval_turns


def wandering_check(session, *, resting: bool = False) -> tuple[list[Event], bool]:
    """Fire one wandering-monster check; a hit spawns and opens an encounter.

    The chance takes +1 for noise since the last check, +1 for daylight-bright
    light, −1 while resting, clamped to [0, 6]; a clamped 0 skips the roll. The
    check die draws from the wandering stream; the d20 table roll, count dice,
    variant picks, and NPC-party re-rolls (draws consumed) follow on the same
    stream; spawned hit points draw from the monster-spawn stream and the
    encounter's own dice from the encounter stream.
    """
    from osrlib.crawl import encounter as encounter_module
    from osrlib.crawl.session import WANDERING_STREAM

    level = _level(session)
    chance = level.wandering.chance_in_six
    if session.noise_since_check:
        chance += 1
    if session.bright_light():
        chance += 1
    if resting:
        chance -= 1
    chance = max(0, min(6, chance))
    session.noise_since_check = False
    stream = session.streams.get(WANDERING_STREAM)
    if chance == 0:
        return [WanderingCheckEvent(chance=0, roll=None, encounter=False)], False
    check_roll = stream.randbelow(6) + 1
    hit = check_roll <= chance
    events: list[Event] = [WanderingCheckEvent(chance=chance, roll=check_roll, encounter=hit)]
    if not hit:
        return events, False
    table = level.wandering.table or load_encounter_tables().for_level(level.number)
    row = table.rows[stream.randbelow(20)]
    count = row.count_fixed if row.count_fixed is not None else roll(row.count_dice, stream).total
    count = max(1, count)
    entry = row.entry
    if entry.kind == "npc_party":
        # The wandering re-roll ends: the row rolls its printed count dice on the
        # wandering stream, generates the party, and the encounter procedure runs
        # unchanged — surprise both ways, distance, reaction (NPC parties react on
        # the table like anyone; parley works; keyed stances don't apply).
        party, bundle, npc_events = field_npc_party(session, entry.party_kind, count)
        events.extend(npc_events)
        events.extend(
            encounter_module.start_encounter(
                session,
                groups=[(row.name, party.members)],
                kind="wandering",
                monsters_roll_surprise=True,
            )
        )
        _assign_carried(session, [({}, bundle)])
        return events, True
    if entry.variant_dice is not None:
        # The hydra form: the printed HD dice select the template once.
        dice = roll(entry.variant_dice, stream)
        minimum = _dice_minimum(entry.variant_dice)
        template_ids = [entry.monster_ids[dice.total - minimum]] * count
    elif len(entry.monster_ids) > 1:
        # Packed-variant pool: each individual picks uniformly (pinned).
        template_ids = [entry.monster_ids[stream.randbelow(len(entry.monster_ids))] for _ in range(count)]
    else:
        template_ids = [entry.monster_ids[0]] * count
    instances = []
    for template_id in template_ids:
        instances.extend(session.spawn(template_id, 1))
    carried = [generate_carried_treasure(session, instances)]
    events.extend(
        encounter_module.start_encounter(
            session,
            groups=[(row.name, instances)],
            kind="wandering",
            monsters_roll_surprise=False,  # wandering monsters know the dungeon (pinned)
        )
    )
    _assign_carried(session, carried)
    return events, True


def _dice_minimum(expression: str) -> int:
    from osrlib.core.dice import parse

    parsed = parse(expression)
    return parsed.count + parsed.modifier


# ---------------------------------------------------------------------- arrival processing


def _boundary_events(session, old_area, new_position) -> list[Event]:
    level = _level(session)
    area = level.area_at(new_position)
    if area is not None and area is not old_area:
        return [LocationEnteredEvent(location_kind="area", location_id=area.id, level_number=level.number)]
    return []


def _enter_hooks(session) -> list[Event]:
    """Run location-bound effect enter behaviors, in attachment order (pinned).

    The burning-oil pool deals its 1d8 to each living member entering the cell;
    a *web* cell entangles each entering member with the escape countdown; a
    stationary *silence* has no enter behavior (it gates casting while there).
    """
    from osrlib.crawl.session import EXPLORATION_STREAM

    events: list[Event] = []
    ref = _cell_ref(session)
    for effect in list(session.ledger.active_on(ref)):
        kind = effect.definition.kind
        if kind == "burning_oil_pool":
            dice = str(effect.definition.params.get("dice", "1d8"))
            for member in session.party.living_members():
                result = roll(dice, session.streams.get(EXPLORATION_STREAM))
                events.extend(
                    deal_damage(
                        member,
                        result.total,
                        source=DamageSource(element="fire", kind="effect"),
                        rolls=result.rolls,
                        clock=session.clock,
                    )
                )
        elif kind == "web":
            params = effect.definition.params
            entangle = EffectDefinition(
                kind="web",
                condition=Condition.ENTANGLED,
                duration_unit=TimeUnit(str(params.get("escape_unit", "turn"))),
                duration_dice=str(params.get("escape_dice", "2d4")),
                dispellable=True,
            )
            for member in session.party.living_members():
                if session.ledger.active_on(member.id, "web"):
                    continue
                _, attach_events = session.ledger.attach(
                    entangle,
                    member.id,
                    clock=session.clock,
                    allocator=session.allocator,
                    registry=session.registry(),
                    stream=session.streams.get(EFFECTS_STREAM),
                )
                events.extend(attach_events)
    return events


def _room_trap_check(session) -> list[Event]:
    """The enter-trigger room trap: 2-in-6 to spring; found traps never spring."""
    from osrlib.crawl.session import EXPLORATION_STREAM

    level = _level(session)
    area = level.area_at(_position(session))
    if area is None or area.trap is None or area.trap.trigger != "enter":
        return []
    trap_ref = _area_ref(session, area.id)
    state = session.dungeon_state
    if trap_ref in state.sprung_traps or trap_ref in state.found_traps or trap_ref in state.removed_traps:
        return []
    stream = session.streams.get(EXPLORATION_STREAM)
    spring_roll = stream.randbelow(6) + 1
    events: list[Event] = [
        DetectionRolledEvent(kind="trap_spring", chance=2, roll=spring_roll, passed=spring_roll <= 2)
    ]
    if spring_roll <= 2:
        state.sprung_traps.append(trap_ref)
        first = session.party.living_members()[0]
        events.append(TrapEvent(code="exploration.trap.sprung", trap_ref=trap_ref, character_id=first.id))
        events.extend(resolve_trap(session, area.trap, triggerer=first))
    return events


def resolve_trap(session, trap: TrapSpec, *, triggerer) -> list[Event]:
    """Resolve a sprung trap's effect — damage automatic, no attack roll.

    Draws run on the exploration stream (the procedure owns its dice, pinned);
    attach-time condition durations roll on the effects stream per the Phase 2
    convention.
    """
    from osrlib.crawl.session import EXPLORATION_STREAM

    stream = session.streams.get(EXPLORATION_STREAM)
    victims = [triggerer] if trap.affects == "triggerer" else list(session.party.living_members())
    effect = trap.effect
    events: list[Event] = []
    for victim in victims:
        negated = False
        halved = False
        if effect.save is not None:
            save = saving_throw(victim, SaveCategory(effect.save.category), stream=stream)
            events.extend(save.events)
            if save.passed:
                if effect.save.on_save == "negates":
                    negated = True
                else:
                    halved = True
        if negated:
            continue
        if effect.kills:
            from osrlib.core.effects import kill

            events.extend(kill(victim))
            continue
        if effect.damage_dice is not None:
            if effect.volley_dice is not None:
                volley = roll(effect.volley_dice, stream).total
                rolls: list[int] = []
                total = 0
                for _ in range(max(0, volley)):
                    result = roll(effect.damage_dice, stream)
                    rolls.extend(result.rolls)
                    total += result.total
            else:
                result = roll(effect.damage_dice, stream)
                rolls, total = list(result.rolls), result.total
            if halved:
                total //= 2
            if total > 0:
                events.extend(
                    deal_damage(
                        victim,
                        total,
                        source=DamageSource(kind="trap"),
                        rolls=tuple(rolls),
                        clock=session.clock,
                    )
                )
        if effect.fall_feet is not None:
            fall = falling_damage(effect.fall_feet, stream)
            if fall is not None:
                events.extend(
                    deal_damage(
                        victim,
                        fall.total,
                        source=DamageSource(kind="falling"),
                        rolls=fall.rolls,
                        clock=session.clock,
                    )
                )
        if effect.condition is not None:
            definition = EffectDefinition(
                kind=f"trap_{effect.condition.value}",
                condition=effect.condition,
                duration_unit=effect.condition_duration_unit,
                duration_amount=effect.condition_duration_amount,
                duration_dice=effect.condition_duration_dice,
            )
            _, attach_events = session.ledger.attach(
                definition,
                victim.id,
                clock=session.clock,
                allocator=session.allocator,
                registry=session.registry(),
                stream=session.streams.get(EFFECTS_STREAM),
            )
            events.extend(attach_events)
    if effect.transition is not None:
        # A slide relocates the whole party — the party model has one location,
        # so a trap transition moves everyone (pinned simplification).
        events.extend(
            _relocate(
                session,
                effect.transition.to_dungeon_id,
                effect.transition.to_level_number,
                effect.transition.to_position,
                effect.transition.to_facing,
            )
        )
    return events


def _relocate(session, dungeon_id: str, level_number: int, position, facing) -> list[Event]:
    """Move the party to a cell (transitions, slides): explore, events, hooks."""
    state = session.dungeon_state
    old_location = state.location
    old_area = None
    leave_events: list[Event] = []
    if (
        old_location.kind == "dungeon"
        and old_location.dungeon_id == dungeon_id
        and old_location.level_number == level_number
    ):
        old_area = _level(session).area_at(old_location.position)
    elif old_location.kind == "dungeon":
        leave_events = _swing_shut_on_leave(session)
    state.location = PartyLocation(
        kind="dungeon", dungeon_id=dungeon_id, level_number=level_number, position=position, facing=facing
    )
    events: list[Event] = []
    if old_location.kind != "dungeon" or old_location.dungeon_id != dungeon_id:
        events.append(LocationEnteredEvent(location_kind="dungeon", location_id=dungeon_id, level_number=level_number))
    elif old_location.level_number != level_number:
        events.append(LocationEnteredEvent(location_kind="level", location_id=dungeon_id, level_number=level_number))
    events.extend(leave_events)
    state.mark_explored(dungeon_id, level_number, position)
    events.extend(_boundary_events(session, old_area, position))
    events.extend(_enter_hooks(session))
    events.extend(_area_treasure_check(session))
    events.extend(_room_trap_check(session))
    events.extend(_keyed_encounter_check(session))
    return events


def _keyed_encounter_check(session) -> list[Event]:
    from osrlib.crawl import encounter as encounter_module

    if session.encounter is not None or session.battle is not None:
        return []
    level = _level(session)
    area = level.area_at(_position(session))
    if area is None or area.encounter is None:
        return []
    area_ref = _area_ref(session, area.id)
    if area_ref in session.dungeon_state.resolved_encounters:
        return []
    from osrlib.crawl.session import WANDERING_STREAM

    groups = []
    carried = []
    templates = []
    for keyed in area.encounter.monsters:
        if keyed.count_fixed is not None:
            count = keyed.count_fixed
        else:
            count = max(1, roll(keyed.count_dice, session.streams.get(WANDERING_STREAM)).total)
        template = load_monsters().get(keyed.template_id)
        templates.append(template)
        instances = session.spawn(keyed.template_id, count, alignment=area.encounter.alignment)
        groups.append((template.name, instances))
        # Carried treasure generates at spawn, per keyed line in printed order;
        # the lair hoard follows (pinned draw order on the treasure stream).
        carried.append(generate_carried_treasure(session, instances))
    events = _generate_lair_hoard(session, area, templates)
    events.extend(
        encounter_module.start_encounter(
            session,
            groups=groups,
            kind="keyed",
            area_ref=area_ref,
            monsters_aware=area.encounter.aware or area_ref in session.alerted_areas,
            party_aware=area_ref in session.heard_areas,
            pinned_stance=area.encounter.stance,
        )
    )
    _assign_carried(session, carried)
    return events


# ---------------------------------------------------------------------- movement handlers


def handle_move_party(session, command: MoveParty) -> tuple[list[Rejection], list[Event]]:
    level = _level(session)
    position = _position(session)
    if exploration_rate(session) <= 0:
        return [Rejection(code="exploration.move.cannot_move", params={"reason": "overloaded"})], []
    for member in session.party.living_members():
        if cannot_move(member):
            return [Rejection(code="exploration.move.cannot_move", params={"character": member.id})], []
    edge = level.edge(position, command.direction)
    passable = edge.kind is EdgeKind.OPEN
    if edge.kind is EdgeKind.DOOR:
        state = _door_state(session, command.direction)
        visible = edge.door.kind != "secret" or state.discovered
        passable = visible and state.open
    if not passable:
        return [Rejection(code="exploration.move.blocked", params={"direction": command.direction.value})], []

    target = step(position, command.direction)
    old_area = level.area_at(position)
    state = session.dungeon_state
    explored_before = state.is_explored(_location(session).dungeon_id, _location(session).level_number, target)
    location = _location(session)
    state.location = PartyLocation(
        kind="dungeon",
        dungeon_id=location.dungeon_id,
        level_number=location.level_number,
        position=target,
        facing=command.direction,
    )
    state.mark_explored(location.dungeon_id, location.level_number, target)
    events: list[Event] = [
        PartyMovedEvent(code="exploration.party.moved", x=target[0], y=target[1], facing=command.direction.value)
    ]
    events.extend(_boundary_events(session, old_area, target))
    events.extend(_swing_shut(session, previous=position))
    events.extend(_enter_hooks(session))
    events.extend(_area_treasure_check(session))
    events.extend(_room_trap_check(session))
    events.extend(_keyed_encounter_check(session))
    events.extend(_accrue_movement(session, 10 if explored_before else 30))
    return [], events


def _swing_shut(session, *, previous, leaving_level: bool = False) -> list[Event]:
    """Doors the party opened swing shut behind it unless wedged (pinned always).

    With `leaving_level` (a transition or the trip to town), every party-opened
    unwedged door on the departed level shuts — the party is gone from all of
    them.
    """
    location = _location(session)
    prefix = f"{location.dungeon_id}:{location.level_number}:"
    events: list[Event] = []
    for ref, state in session.dungeon_state.doors.items():
        if not ref.startswith(prefix) or not state.open or state.wedged or not state.opened_by_party:
            continue
        cell_part, side = ref.removeprefix(prefix).split(":")
        x, y = (int(value) for value in cell_part.split(","))
        # The canonical key names the edge from its south/east cell's north/west
        # side: the two adjoining cells are (x, y) and its north/west neighbour.
        neighbour = step((x, y), Direction.NORTH if side == "north" else Direction.WEST)
        if leaving_level or location.position not in ((x, y), neighbour):
            state.open = False
            events.append(DoorEvent(code="exploration.door.swung_shut", x=x, y=y, direction=side))
    return events


def _swing_shut_on_leave(session) -> list[Event]:
    """Close every party-opened unwedged door on the level being left."""
    return _swing_shut(session, previous=None, leaving_level=True)


def handle_turn_party(session, command: TurnParty) -> tuple[list[Rejection], list[Event]]:
    location = _location(session)
    session.dungeon_state.location = location.model_copy(update={"facing": command.facing})
    position = location.position
    return [], [
        PartyMovedEvent(code="exploration.party.turned", x=position[0], y=position[1], facing=command.facing.value)
    ]


def handle_reorder_party(session, command: ReorderParty) -> tuple[list[Rejection], list[Event]]:
    by_id = {member.id for member in session.party.members}
    if sorted(command.order) != sorted(by_id):
        return [Rejection(code="exploration.party.bad_order")], []
    session.party.reorder(command.order)
    return [], []


def handle_use_stairs(session, command: UseStairs) -> tuple[list[Rejection], list[Event]]:
    transition = _level(session).transition_at(_position(session))
    if transition is None:
        return [Rejection(code="exploration.stairs.none")], []
    events = _relocate(
        session, transition.to_dungeon_id, transition.to_level_number, transition.to_position, transition.to_facing
    )
    events.extend(_accrue_movement(session, 30))
    return [], events


def handle_enter_dungeon(session, command: EnterDungeon) -> tuple[list[Rejection], list[Event]]:
    try:
        dungeon = session.adventure.dungeon(command.dungeon_id)
    except ValueError:
        return [Rejection(code="session.command.unknown_location", params={"dungeon": command.dungeon_id})], []
    entrance_level = next((level for level in dungeon.levels if level.entrance is not None), None)
    if entrance_level is None:
        return [Rejection(code="session.command.unknown_location", params={"dungeon": command.dungeon_id})], []
    # The award values what the party actually brings back: each departure
    # snapshots the valuation, and the return award is the delta (pinned).
    session.snapshot_treasure()
    travel = session.adventure.town.travel_turns.get(command.dungeon_id, 0)
    events, _ = session.advance_turns(travel, field=False)
    session.mode = SessionMode.EXPLORING
    events.extend(_relocate(session, dungeon.id, entrance_level.number, entrance_level.entrance, Direction.NORTH))
    return [], events


def handle_travel_to_town(session, command: TravelToTown) -> tuple[list[Rejection], list[Event]]:
    location = _location(session)
    level = _level(session)
    if level.entrance is None or location.position != level.entrance:
        return [Rejection(code="exploration.travel.not_at_entrance")], []
    events = _swing_shut_on_leave(session)
    session.mode = SessionMode.TOWN
    session.dungeon_state.location = PartyLocation(kind="town")
    travel = session.adventure.town.travel_turns.get(location.dungeon_id, 0)
    travel_events, _ = session.advance_turns(travel, field=False)
    events.extend(travel_events)
    events.append(LocationEnteredEvent(location_kind="town", location_id="town"))
    from osrlib.core.ruleset import XpAwardTiming

    if session.ruleset.xp_award_timing is XpAwardTiming.ON_RETURN:
        events.extend(session.award_adventure_xp())
    else:
        # Immediate mode: town arrival awards nothing; the snapshot still resets.
        session.defeated_monsters = []
        session.treasure_snapshot_cp = None
    return [], events


# ---------------------------------------------------------------------- door handlers


def handle_open_door(session, command: OpenDoor) -> tuple[list[Rejection], list[Event]]:
    known = _known_door(session, command.direction)
    if known is None:
        return [Rejection(code="exploration.door.no_door", params={"direction": command.direction.value})], []
    edge, state = known
    if state.open:
        return [Rejection(code="exploration.door.already_open")], []
    if edge.door.locked and not state.unlocked:
        return [Rejection(code="exploration.door.locked")], []
    if edge.door.stuck:
        return [Rejection(code="exploration.door.stuck")], []
    state.open = True
    state.opened_by_party = True
    x, y = _position(session)
    return [], [DoorEvent(code="exploration.door.opened", x=x, y=y, direction=command.direction.value)]


def handle_close_door(session, command: CloseDoor) -> tuple[list[Rejection], list[Event]]:
    known = _known_door(session, command.direction)
    if known is None:
        return [Rejection(code="exploration.door.no_door", params={"direction": command.direction.value})], []
    _, state = known
    if not state.open:
        return [Rejection(code="exploration.door.already_closed")], []
    if state.wedged:
        return [Rejection(code="exploration.door.wedged")], []
    state.open = False
    x, y = _position(session)
    return [], [DoorEvent(code="exploration.door.closed", x=x, y=y, direction=command.direction.value)]


def handle_force_door(session, command: ForceDoor) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    known = _known_door(session, command.direction)
    if known is None:
        return [Rejection(code="exploration.door.no_door", params={"direction": command.direction.value})], []
    edge, state = known
    if state.open:
        return [Rejection(code="exploration.door.already_open")], []
    if edge.door.locked and not state.unlocked:
        return [Rejection(code="exploration.door.locked")], []
    if not edge.door.stuck:
        return [Rejection(code="exploration.door.not_stuck")], []
    from osrlib.crawl.session import EXPLORATION_STREAM

    # Any attempt bangs on the door: the noise flag marks the next wandering
    # check (pinned); only a *failed* attempt denies the party surprise (RAW).
    session.noise_since_check = True
    check = detection_check(member.open_doors_chance, stream=session.streams.get(EXPLORATION_STREAM))
    x, y = _position(session)
    if check.passed:
        state.open = True
        state.opened_by_party = True
        return [], [
            DoorEvent(
                code="exploration.door.forced", x=x, y=y, direction=command.direction.value, character_id=member.id
            )
        ]
    beyond = step(_position(session), command.direction)
    area = _level(session).area_at(beyond)
    if area is not None:
        ref = _area_ref(session, area.id)
        if ref not in session.alerted_areas:
            session.alerted_areas.append(ref)
    return [], [
        DoorEvent(code="exploration.door.stuck", x=x, y=y, direction=command.direction.value, character_id=member.id)
    ]


def handle_wedge_door(session, command: WedgeDoor) -> tuple[list[Rejection], list[Event]]:
    known = _known_door(session, command.direction)
    if known is None:
        return [Rejection(code="exploration.door.no_door", params={"direction": command.direction.value})], []
    _, state = known
    if state.wedged:
        return [Rejection(code="exploration.door.wedged")], []
    spike_carrier = next(
        (member for member in session.party.living_members() if _find_item(member, "iron_spikes") is not None), None
    )
    if spike_carrier is None:
        return [Rejection(code="exploration.door.no_spike")], []
    _consume_item(spike_carrier, "iron_spikes")
    state.wedged = True
    x, y = _position(session)
    return [], [DoorEvent(code="exploration.door.wedged", x=x, y=y, direction=command.direction.value)]


def handle_listen_at_door(session, command: ListenAtDoor) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    known = _known_door(session, command.direction)
    if known is None:
        return [Rejection(code="exploration.door.no_door", params={"direction": command.direction.value})], []
    light_rejections = _requires_light(session, member, infravision_suffices=True)
    if light_rejections:
        return light_rejections, []
    ref = _edge_ref(session, command.direction)
    attempts = session.dungeon_state.listen_attempts.setdefault(ref, [])
    if member.id in attempts:
        return [Rejection(code="exploration.listen.already_tried", params={"character": member.id})], []
    from osrlib.crawl.session import EXPLORATION_STREAM

    attempts.append(member.id)
    definition = load_classes().get(member.class_id)
    chance = detection_chance(member, definition, "listening")
    check = detection_check(chance, stream=session.streams.get(EXPLORATION_STREAM))
    events: list[Event] = [
        DetectionRolledEvent(
            character_id=member.id, kind="listening", chance=chance, roll=check.roll, passed=check.passed
        )
    ]
    heard = False
    beyond = step(_position(session), command.direction)
    area = _level(session).area_at(beyond)
    if check.passed and area is not None and area.encounter is not None:
        area_ref = _area_ref(session, area.id)
        if area_ref not in session.dungeon_state.resolved_encounters:
            monsters = load_monsters()
            noisy = any("undead" not in monsters.get(keyed.template_id).categories for keyed in area.encounter.monsters)
            if noisy:
                heard = True
                if area_ref not in session.heard_areas:
                    session.heard_areas.append(area_ref)
    code = "exploration.listen.heard" if heard else "exploration.listen.silent"
    events.append(ListenedEvent(code=code, character_id=member.id, direction=command.direction.value))
    return [], events


def handle_pick_lock(session, command: PickLock) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    definition = load_classes().get(member.class_id)
    if not definition.thief_skills:
        return [Rejection(code="exploration.lock.not_a_thief", params={"character": member.id})], []
    if _find_item(member, "thieves_tools") is None:
        return [Rejection(code="exploration.lock.no_tools", params={"character": member.id})], []
    known = _known_door(session, command.direction)
    if known is None:
        return [Rejection(code="exploration.door.no_door", params={"direction": command.direction.value})], []
    edge, state = known
    if not edge.door.locked or state.unlocked:
        return [Rejection(code="exploration.lock.not_locked")], []
    light_rejections = _requires_light(session, member, infravision_suffices=False)
    if light_rejections:
        return light_rejections, []
    ref = _edge_ref(session, command.direction)
    failed_at = session.dungeon_state.lock_failures.get(ref, {}).get(member.id)
    if failed_at is not None and member.level <= failed_at:
        return [Rejection(code="exploration.lock.locked_out", params={"character": member.id})], []
    from osrlib.crawl.session import EXPLORATION_STREAM

    result = thief_skill_check(member, definition, "open_locks", stream=session.streams.get(EXPLORATION_STREAM))
    events: list[Event] = [
        DetectionRolledEvent(
            character_id=member.id, kind="open_locks", chance=result.chance, roll=result.roll, passed=result.passed
        )
    ]
    if result.passed:
        state.unlocked = True
        x, y = _position(session)
        events.append(
            DoorEvent(
                code="exploration.door.unlocked", x=x, y=y, direction=command.direction.value, character_id=member.id
            )
        )
    else:
        session.dungeon_state.lock_failures.setdefault(ref, {})[member.id] = member.level
    turn_events, _ = _spend_turn(session)
    events.extend(turn_events)
    return [], events


# ---------------------------------------------------------------------- searching


def handle_search(session, command: Search) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    light_rejections = _requires_light(session, member, infravision_suffices=True)
    if light_rejections:
        return light_rejections, []
    key = f"{_cell_ref(session)}:{command.kind}"
    attempts = session.dungeon_state.search_attempts.setdefault(key, [])
    if member.id in attempts:
        return [Rejection(code="exploration.search.already_tried", params={"character": member.id})], []
    from osrlib.crawl.session import EXPLORATION_STREAM

    attempts.append(member.id)
    definition = load_classes().get(member.class_id)
    chance = detection_chance(member, definition, command.kind)
    check = detection_check(chance, stream=session.streams.get(EXPLORATION_STREAM))
    events: list[Event] = [
        DetectionRolledEvent(
            character_id=member.id, kind=command.kind, chance=chance, roll=check.roll, passed=check.passed
        )
    ]
    found: list[str] = []
    if check.passed:
        found = _reveal(session, command.kind, events)
    code = "exploration.search.found" if found else "exploration.search.nothing"
    events.append(SearchCompletedEvent(code=code, character_id=member.id, kind=command.kind, found=tuple(found)))
    turn_events, _ = _spend_turn(session)
    events.extend(turn_events)
    return [], events


def _reveal(session, kind: str, events: list[Event]) -> list[str]:
    """Reveal every hidden feature of one kind on the current cell (pinned: the cell)."""
    level = _level(session)
    position = _position(session)
    state = session.dungeon_state
    found: list[str] = []
    if kind == "secret_doors":
        for direction in Direction:
            edge = level.edge(position, direction)
            if edge.kind is EdgeKind.DOOR and edge.door.kind == "secret":
                door = _door_state(session, direction)
                if not door.discovered:
                    door.discovered = True
                    found.append(f"secret_door:{direction.value}")
    elif kind == "room_traps":
        area = level.area_at(position)
        if area is not None and area.trap is not None:
            trap_ref = _area_ref(session, area.id)
            if trap_ref not in state.found_traps and trap_ref not in state.sprung_traps:
                state.found_traps.append(trap_ref)
                found.append(f"room_trap:{area.id}")
                events.append(TrapEvent(code="exploration.trap.found", trap_ref=trap_ref))
    elif kind == "construction":
        for feature in _features_here(session):
            if feature.kind != "construction_trick":
                continue
            ref = _feature_ref(session, feature)
            if ref not in state.found_tricks:
                state.found_tricks.append(ref)
                found.append(f"construction:{feature.id}")
    return found


def _features_here(session) -> list[FeatureSpec]:
    level = _level(session)
    position = _position(session)
    features: list[FeatureSpec] = []
    area = level.area_at(position)
    if area is not None:
        features.extend(feature for feature in area.features if feature.cell is None or feature.cell == position)
    features.extend(feature for feature in level.features if feature.cell == position)
    return features


def _feature_ref(session, feature: FeatureSpec) -> str:
    location = _location(session)
    return f"{location.dungeon_id}:{location.level_number}:{feature.id}"


def handle_inspect_treasure(session, command: InspectTreasure) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    definition = load_classes().get(member.class_id)
    if not definition.thief_skills:
        return [Rejection(code="exploration.trap.not_a_thief", params={"character": member.id})], []
    feature = next((f for f in _features_here(session) if f.id == command.feature_id), None)
    cache = session.dungeon_state.generated_caches.get(command.feature_id)
    if cache is not None and cache.cell_ref == _cell_ref(session):
        feature = None  # generated hoards are untrapped (pinned): the check runs, nothing is found
    elif feature is None or feature.kind != "treasure_cache":
        return [Rejection(code="exploration.feature.unknown", params={"feature": command.feature_id})], []
    light_rejections = _requires_light(session, member, infravision_suffices=False)
    if light_rejections:
        return light_rejections, []
    ref = _feature_ref(session, feature) if feature is not None else command.feature_id
    attempts = session.dungeon_state.inspect_attempts.setdefault(ref, [])
    if member.id in attempts:
        return [Rejection(code="exploration.search.already_tried", params={"character": member.id})], []
    from osrlib.crawl.session import EXPLORATION_STREAM

    attempts.append(member.id)
    result = thief_skill_check(
        member, definition, "find_remove_treasure_traps", stream=session.streams.get(EXPLORATION_STREAM)
    )
    events: list[Event] = [
        DetectionRolledEvent(
            character_id=member.id, kind="treasure_traps", chance=result.chance, roll=result.roll, passed=result.passed
        )
    ]
    state = session.dungeon_state
    trapped = (
        feature is not None
        and feature.trap is not None
        and ref not in state.sprung_traps
        and ref not in state.removed_traps
    )
    if result.passed and trapped and ref not in state.found_traps:
        state.found_traps.append(ref)
        events.append(TrapEvent(code="exploration.trap.found", trap_ref=ref, character_id=member.id))
    else:
        events.append(
            SearchCompletedEvent(code="exploration.search.nothing", character_id=member.id, kind="treasure_traps")
        )
    turn_events, _ = _spend_turn(session)
    events.extend(turn_events)
    return [], events


def handle_remove_treasure_trap(session, command: RemoveTreasureTrap) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    definition = load_classes().get(member.class_id)
    if not definition.thief_skills:
        return [Rejection(code="exploration.trap.not_a_thief", params={"character": member.id})], []
    feature = next((f for f in _features_here(session) if f.id == command.feature_id), None)
    if feature is None or feature.trap is None:
        return [Rejection(code="exploration.feature.unknown", params={"feature": command.feature_id})], []
    ref = _feature_ref(session, feature)
    state = session.dungeon_state
    if ref not in state.found_traps:
        return [Rejection(code="exploration.trap.not_found", params={"feature": command.feature_id})], []
    if ref in state.removed_traps or ref in state.sprung_traps:
        return [Rejection(code="exploration.trap.already_resolved", params={"feature": command.feature_id})], []
    light_rejections = _requires_light(session, member, infravision_suffices=False)
    if light_rejections:
        return light_rejections, []
    attempts = state.removal_attempts.setdefault(ref, [])
    if member.id in attempts:
        return [Rejection(code="exploration.search.already_tried", params={"character": member.id})], []
    from osrlib.crawl.session import EXPLORATION_STREAM

    attempts.append(member.id)
    result = thief_skill_check(
        member, definition, "find_remove_treasure_traps", stream=session.streams.get(EXPLORATION_STREAM)
    )
    events: list[Event] = [
        DetectionRolledEvent(
            character_id=member.id, kind="treasure_traps", chance=result.chance, roll=result.roll, passed=result.passed
        )
    ]
    if result.passed:
        state.removed_traps.append(ref)
        events.append(TrapEvent(code="exploration.trap.removed", trap_ref=ref, character_id=member.id))
    else:
        # A failed removal springs the trap on the thief (the classic reading,
        # pinned; RAW says only "attempted once per trap").
        state.sprung_traps.append(ref)
        events.append(TrapEvent(code="exploration.trap.sprung", trap_ref=ref, character_id=member.id))
        events.extend(resolve_trap(session, feature.trap, triggerer=member))
    turn_events, _ = _spend_turn(session)
    events.extend(turn_events)
    return [], events


def handle_take_treasure(session, command: TakeTreasure) -> tuple[list[Rejection], list[Event]]:
    living = session.party.living_members()
    if not living:
        return [Rejection(code="session.command.no_living_members")], []
    taker = living[0]
    state = session.dungeon_state
    events: list[Event] = []
    if command.feature_id == "pile":
        ref = _cell_ref(session)
        pile = state.piles.get(ref)
        if pile is None:
            return [Rejection(code="exploration.feature.unknown", params={"feature": "pile"})], []
        item_ids = _transfer_pile(taker, pile)
        del state.piles[ref]
        events.append(
            ItemAcquiredEvent(character_id=taker.id, item_ids=tuple(item_ids), coins_gp_value=pile.coins.value_gp)
        )
        events.extend(_immediate_treasure_award(session, pile.coins.value_cp, pile.valuables))
        turn_events, _ = _spend_turn(session)
        events.extend(turn_events)
        return [], events
    cache = state.generated_caches.get(command.feature_id)
    if cache is not None:
        if cache.cell_ref != _cell_ref(session):
            return [Rejection(code="exploration.feature.unknown", params={"feature": command.feature_id})], []
        item_ids = []
        for valuable in cache.valuables:
            taker.inventory.valuables.append(valuable)
            item_ids.append(valuable.instance_id)
        for magic_item in cache.magic_items:
            taker.inventory.items.append(magic_item)
            item_ids.append(magic_item.instance_id)
        purse = taker.inventory.purse
        for denomination in ("pp", "gp", "ep", "sp", "cp"):
            setattr(purse, denomination, getattr(purse, denomination) + getattr(cache.coins, denomination))
        coins_value = cache.coins.value_gp
        del state.generated_caches[command.feature_id]
        events.append(ItemAcquiredEvent(character_id=taker.id, item_ids=tuple(item_ids), coins_gp_value=coins_value))
        events.extend(_immediate_treasure_award(session, cache.coins.value_cp, cache.valuables))
        turn_events, _ = _spend_turn(session)
        events.extend(turn_events)
        return [], events
    feature = next((f for f in _features_here(session) if f.id == command.feature_id), None)
    if feature is None or feature.kind != "treasure_cache":
        return [Rejection(code="exploration.feature.unknown", params={"feature": command.feature_id})], []
    ref = _feature_ref(session, feature)
    if ref in state.emptied_caches:
        return [Rejection(code="exploration.feature.emptied", params={"feature": command.feature_id})], []
    trap = feature.trap
    if trap is not None and ref not in state.sprung_traps and ref not in state.removed_traps:
        from osrlib.crawl.session import EXPLORATION_STREAM

        stream = session.streams.get(EXPLORATION_STREAM)
        spring_roll = stream.randbelow(6) + 1
        sprung = spring_roll <= 2
        events.append(DetectionRolledEvent(kind="trap_spring", chance=2, roll=spring_roll, passed=sprung))
        if sprung:
            state.sprung_traps.append(ref)
            events.append(TrapEvent(code="exploration.trap.sprung", trap_ref=ref, character_id=taker.id))
            events.extend(resolve_trap(session, trap, triggerer=taker))
        elif ref in state.found_traps:
            # The party knows the trap is there and sees it fail to fire; an
            # unknown trap that doesn't spring stays referee-only (no leak).
            events.append(TrapEvent(code="exploration.trap.safe", trap_ref=ref, character_id=taker.id))
    equipment = load_equipment()
    item_ids: list[str] = []
    for item_id in feature.item_ids:
        taker.inventory.items.append(ItemInstance(template=equipment.get(item_id)))
        item_ids.append(item_id)
    for spec in feature.valuables:
        # Authored named valuables (the example's MacGuffin) instantiate on take.
        from osrlib.core.items import ValuableInstance

        valuable = ValuableInstance(
            instance_id=session.allocator.allocate("valuable"),
            kind=spec.kind,
            name=spec.name,
            value_gp=spec.value_gp,
            weight_coins=spec.weight_coins,
        )
        taker.inventory.valuables.append(valuable)
        item_ids.append(valuable.instance_id)
    purse = taker.inventory.purse
    for denomination in ("pp", "gp", "ep", "sp", "cp"):
        setattr(purse, denomination, getattr(purse, denomination) + getattr(feature.coins, denomination))
    state.emptied_caches.append(ref)
    events.append(
        ItemAcquiredEvent(character_id=taker.id, item_ids=tuple(item_ids), coins_gp_value=feature.coins.value_gp)
    )
    taken_valuables = [valuable for valuable in taker.inventory.valuables if valuable.instance_id in item_ids]
    events.extend(_immediate_treasure_award(session, feature.coins.value_cp, taken_valuables))
    turn_events, _ = _spend_turn(session)
    events.extend(turn_events)
    return [], events


def _transfer_pile(taker, pile: DropPile) -> list[str]:
    equipment = load_equipment()
    item_ids: list[str] = []
    for dropped in pile.items:
        taker.inventory.items.append(ItemInstance(template=equipment.get(dropped.item_id), quantity=dropped.quantity))
        item_ids.extend([dropped.item_id] * dropped.quantity)
    for valuable in pile.valuables:
        taker.inventory.valuables.append(valuable)
        item_ids.append(valuable.instance_id)
    for magic_item in pile.magic_items:
        taker.inventory.items.append(magic_item)
        item_ids.append(magic_item.instance_id)
    purse = taker.inventory.purse
    for denomination in ("pp", "gp", "ep", "sp", "cp"):
        setattr(purse, denomination, getattr(purse, denomination) + getattr(pile.coins, denomination))
    return item_ids


# ---------------------------------------------------------------------- items and light


def handle_drop_items(session, command: DropItems) -> tuple[list[Rejection], list[Event]]:
    if session.mode is SessionMode.ENCOUNTER:
        from osrlib.crawl import encounter as encounter_module

        return encounter_module.handle_drop_during_encounter(session, command)
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    rejections = _validate_drops(member, command)
    if rejections:
        return rejections, []
    events = _apply_drop(session, member, command, to_pile=True)
    return [], events


def _validate_drops(member, command: DropItems) -> list[Rejection]:
    counts: dict[str, int] = {}
    for item_id in command.item_ids:
        magic = member.inventory.magic_item(item_id)
        if magic is not None:
            if magic.cursed_revealed:
                # A revealed cursed item pins to its bearer until *remove curse*.
                return [Rejection(code="items.curse.stuck", params={"item": item_id})]
            continue
        if any(valuable.instance_id == item_id for valuable in member.inventory.valuables):
            continue
        counts[item_id] = counts.get(item_id, 0) + 1
    for item_id, needed in counts.items():
        held = sum(
            instance.quantity
            for instance in member.inventory.all_instances()
            if not isinstance(instance, MagicItemInstance) and instance.template.id == item_id
        )
        if held < needed:
            return [Rejection(code="exploration.item.not_carried", params={"item": item_id})]
    purse = member.inventory.purse
    for denomination in ("pp", "gp", "ep", "sp", "cp"):
        if getattr(command.coins, denomination) > getattr(purse, denomination):
            return [Rejection(code="exploration.item.not_carried", params={"item": denomination})]
    return []


def _apply_drop(session, member, command: DropItems, *, to_pile: bool) -> list[Event]:
    """Remove the dropped goods; onto the cell's pile, or scattered (pursuit bait)."""
    pile = None
    if to_pile:
        pile = session.dungeon_state.piles.setdefault(_cell_ref(session), DropPile())
    for item_id in command.item_ids:
        magic = member.inventory.magic_item(item_id)
        if magic is not None:
            _remove_magic_instance(member, magic)
            if pile is not None:
                pile.magic_items.append(magic)
            continue
        valuable = next(
            (candidate for candidate in member.inventory.valuables if candidate.instance_id == item_id), None
        )
        if valuable is not None:
            member.inventory.valuables.remove(valuable)
            if pile is not None:
                pile.valuables.append(valuable)
            continue
        _consume_item(member, item_id)
        if pile is not None:
            existing = next((entry for entry in pile.items if entry.item_id == item_id), None)
            if existing is None:
                pile.items.append(DroppedItem(item_id=item_id, quantity=1))
            else:
                existing.quantity += 1
    purse = member.inventory.purse
    for denomination in ("pp", "gp", "ep", "sp", "cp"):
        setattr(purse, denomination, getattr(purse, denomination) - getattr(command.coins, denomination))
    if pile is not None:
        pile.coins = Coins(
            **{
                denomination: getattr(pile.coins, denomination) + getattr(command.coins, denomination)
                for denomination in ("pp", "gp", "ep", "sp", "cp")
            }
        )
    return [
        ItemsDroppedEvent(
            character_id=member.id, item_ids=tuple(command.item_ids), coins_gp_value=command.coins.value_gp
        )
    ]


def handle_light_source(session, command: LightSource) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    if command.item_id not in ("torch", "lantern", "oil_flask"):
        return [Rejection(code="exploration.light.not_a_source", params={"item": command.item_id})], []
    in_pile = False
    if command.item_id == "oil_flask":
        # Lighting oil ignites a dropped pool on the party's cell — there is no
        # cell to pool on in town.
        if _location(session).kind != "dungeon":
            return [Rejection(code="exploration.item.not_carried", params={"item": "oil_flask"})], []
        pile = session.dungeon_state.piles.get(_cell_ref(session))
        in_pile = pile is not None and any(entry.item_id == "oil_flask" and entry.quantity > 0 for entry in pile.items)
        if not in_pile:
            return [Rejection(code="exploration.item.not_carried", params={"item": "oil_flask"})], []
    elif _find_item(member, command.item_id) is None:
        return [Rejection(code="exploration.item.not_carried", params={"item": command.item_id})], []
    if command.item_id == "lantern" and _find_item(member, "oil_flask") is None:
        return [Rejection(code="exploration.item.not_carried", params={"item": "oil_flask"})], []
    needs_tinder = not _party_open_flame(session)
    if needs_tinder and _find_item(member, "tinder_box") is None:
        return [Rejection(code="exploration.light.no_flame", params={"character": member.id})], []
    from osrlib.crawl.session import EXPLORATION_STREAM

    events: list[Event] = []
    if needs_tinder:
        tinder_roll = session.streams.get(EXPLORATION_STREAM).randbelow(6) + 1
        if tinder_roll > 2:
            events.append(LightEvent(code="exploration.light.failed", character_id=member.id, source=command.item_id))
            events.extend(session.advance_rounds(1))
            return [], events
    if command.item_id == "torch":
        _consume_item(member, "torch")
        definition = _light_definition("torch", load_equipment().get("torch").params)
        _, attach_events = _attach_light(session, definition, member.id)
        events.extend(attach_events)
    elif command.item_id == "lantern":
        _consume_item(member, "oil_flask")
        definition = _light_definition("lantern", load_equipment().get("lantern").params)
        _, attach_events = _attach_light(session, definition, member.id)
        events.extend(attach_events)
    else:
        pile = session.dungeon_state.piles[_cell_ref(session)]
        entry = next(entry for entry in pile.items if entry.item_id == "oil_flask")
        if entry.quantity == 1:
            pile.items.remove(entry)
        else:
            entry.quantity -= 1
        if not pile.items and pile.coins.total_coins == 0:
            del session.dungeon_state.piles[_cell_ref(session)]
        _, attach_events = session.ledger.attach(
            burning_oil_pool_definition(),
            _cell_ref(session),
            clock=session.clock,
            allocator=session.allocator,
            registry=session.registry(),
        )
        events.extend(attach_events)
    events.append(
        LightEvent(
            code="exploration.light.lit",
            character_id=member.id,
            source="oil" if command.item_id == "oil_flask" else command.item_id,
        )
    )
    events.extend(session.advance_rounds(1))
    return [], events


def _light_definition(source: str, params) -> EffectDefinition:
    burn = int(params.get("burn_turns", params.get("burn_turns_per_flask", 6)))
    return EffectDefinition(
        kind="light",
        duration_unit=TimeUnit.TURN,
        duration_amount=burn,
        params={
            "source": source,
            "light_radius_feet": int(params.get("light_radius_feet", 30)),
            "brightness": str(params.get("brightness", "flame")),
        },
    )


def _attach_light(session, definition: EffectDefinition, bearer_id: str):
    return session.ledger.attach(
        definition, bearer_id, clock=session.clock, allocator=session.allocator, registry=session.registry()
    )


def _party_open_flame(session) -> bool:
    living_ids = {member.id for member in session.party.living_members()}
    return any(
        effect.target_ref in living_ids
        and effect.definition.kind == "light"
        and effect.definition.params.get("brightness") == "flame"
        for effect in session.ledger.effects
    )


def handle_extinguish_source(session, command: ExtinguishSource) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    burning = [
        effect
        for effect in session.ledger.active_on(member.id, "light")
        if effect.definition.params.get("source") in ("torch", "lantern")
    ]
    if not burning:
        return [Rejection(code="exploration.light.not_burning", params={"character": member.id})], []
    events: list[Event] = []
    for effect in burning:
        source = str(effect.definition.params.get("source"))
        events.extend(session.ledger.release(effect.effect_id, session.registry()))
        events.append(LightEvent(code="exploration.light.extinguished", character_id=member.id, source=source))
    return [], events


def handle_equip_item(session, command: EquipItem) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    instance = next(
        (
            candidate
            for candidate in member.inventory.items
            if (
                candidate.instance_id == command.item_id
                if isinstance(candidate, MagicItemInstance)
                else candidate.template.id == command.item_id
            )
        ),
        None,
    )
    if instance is None:
        return [Rejection(code="exploration.item.not_carried", params={"item": command.item_id})], []
    definition = load_classes().get(member.class_id)
    equip_rejections = validate_equip(definition, instance, member.inventory)
    if equip_rejections:
        return equip_rejections, []
    equip(member.inventory, definition, instance)
    events: list[Event] = []
    if isinstance(instance, MagicItemInstance):
        events.extend(attach_worn_item_effects(session, member, instance))
    return [], events


def handle_unequip_item(session, command: UnequipItem) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    inventory = member.inventory
    instance = next(
        (
            candidate
            for candidate in inventory.equipped_instances()
            if (
                candidate.instance_id == command.item_id
                if isinstance(candidate, MagicItemInstance)
                else candidate.template.id == command.item_id
            )
        ),
        None,
    )
    if instance is None:
        return [Rejection(code="exploration.item.not_equipped", params={"item": command.item_id})], []
    unequip_rejections = validate_unequip(inventory, instance)
    if unequip_rejections:
        return unequip_rejections, []
    events: list[Event] = []
    if isinstance(instance, MagicItemInstance):
        events.extend(_release_instance_effects(session, instance))
    unequip(inventory, instance)
    return [], events


def handle_purchase_equipment(session, command: PurchaseEquipment) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    equipment = load_equipment()
    templates = []
    total_cost = 0
    for item_id in command.item_ids:
        try:
            template = equipment.get(item_id)
        except ValueError:
            return [Rejection(code="session.command.unknown_item", params={"item": item_id})], []
        templates.append(template)
        total_cost += template.cost_gp
    if not member.inventory.purse.can_afford(total_cost):
        return [Rejection(code="items.purchase.insufficient_funds", params={"cost_gp": total_cost})], []
    from osrlib.core.items import purchase

    for template in templates:
        purchase(member.inventory, template, 1)
    return [], [ItemAcquiredEvent(character_id=member.id, item_ids=tuple(command.item_ids))]


# ---------------------------------------------------------------------- rest and magic


def handle_rest(session, command: Rest) -> tuple[list[Rejection], list[Event]]:
    turns = {"turn": 1, "night": 48, "day": 144}[command.kind]
    resting_events, interrupted = (
        _spend_turn(session, resting=True) if turns == 1 else session.advance_turns(turns, resting=True)
    )
    events = list(resting_events)
    if turns > 1:
        session.odometer_thirds = 0
    if interrupted:
        events.append(RestedEvent(code="exploration.rest.interrupted", kind=command.kind))
        return [], events
    events.extend(_clear_fatigue(session))
    events.extend(_credit_exhaustion_rest(session, turns))
    if command.kind in ("night", "day"):
        session.sleep_count += 1
    if command.kind == "day":
        for member in session.party.living_members():
            events.extend(natural_healing(member, session.streams.get(EFFECTS_STREAM), ledger=session.ledger))
    events.append(RestedEvent(code="exploration.rest.rested", kind=command.kind))
    return [], events


def handle_prepare_spells(session, command: PrepareSpells) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    if session.sleep_count == 0 or session.last_prepared_sleep.get(member.id, 0) >= session.sleep_count:
        return [Rejection(code="magic.memorize.needs_sleep", params={"character": member.id})], []
    definition = load_classes().get(member.class_id)
    result = memorize_spells(member, definition, load_spells(), command.selections)
    if result.rejections:
        return list(result.rejections), []
    session.last_prepared_sleep[member.id] = session.sleep_count
    events = list(result.events)
    turn_events, _ = session.advance_turns(6)
    events.extend(turn_events)
    return [], events


def handle_cast_spell(session, command: CastSpell) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    try:
        spell = load_spells().get(command.spell_id)
    except ValueError:
        return [Rejection(code="magic.cast.unknown_spell", params={"spell": command.spell_id})], []
    if _location(session).kind == "dungeon" and session.ledger.active_on(_cell_ref(session), "silence"):
        return [Rejection(code="magic.cast.silenced_area", params={"caster": member.id})], []
    registry = session.registry()
    targets: list[object] = []
    for target_ref in command.targets:
        if target_ref.startswith("cell:"):
            targets.append(target_ref)
        elif target_ref in registry:
            targets.append(registry[target_ref])
        else:
            return [Rejection(code="magic.cast.unknown_target", params={"target": target_ref})], []
    context = _cast_context(session, targets, in_combat=False)
    cast_rejections = validate_cast(
        member, spell, command.mode, reversed=command.reversed, targets=targets, context=context, ledger=session.ledger
    )
    if cast_rejections:
        return cast_rejections, []
    result = cast_spell(
        member,
        spell,
        command.mode,
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
    events.extend(_stationary_silence(session, spell, result, targets))
    if spell.id.startswith("remove_curse") and not command.reversed:
        for target in targets:
            if getattr(target, "definition", None) is not None:
                events.extend(lift_curses(session, target))
    events.extend(session.advance_rounds(1))
    return [], events


def _cast_context(session, targets, *, in_combat: bool, distance_feet: int | None = None) -> CastContext:
    """Build the cast context from session truth: the death records' honest numbers."""
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


def _stationary_silence(session, spell, result, targets) -> list[Event]:
    """*Silence 15' radius*'s save-passed outcome: the area anchors to a cell.

    On a passed save RAW leaves a stationary area the creature can step out of —
    the Phase 3 registered gap, closed: the effect attaches to the party's cell
    (the encounter's abstract location), and creatures in that cell cannot cast
    while there.
    """
    if spell.id != "silence_15_radius" or result.manual or _location(session).kind != "dungeon":
        return []
    passed = {
        event.target_id
        for event in result.events
        if isinstance(event, Event) and event.code == "combat.save.passed" and hasattr(event, "target_id")
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


# ---------------------------------------------------------------------- treasure generation


def _immediate_treasure_award(session, coins_cp: int, valuables) -> list:
    """The `immediate` timing's treasure XP: the acquisition's value as the delta."""
    from osrlib.core.ruleset import XpAwardTiming

    if session.ruleset.xp_award_timing is not XpAwardTiming.IMMEDIATE:
        return []
    total_cp = coins_cp + sum(valuable.value_gp * 100 for valuable in valuables)
    return session.award_immediate_xp(total_cp // 100)


def treasure_tier(session) -> str:
    """The tier the crawl passes to generation, evaluated at generation time (pinned).

    `basic` while the party's highest living level is 1–3, `expert` at 4+ — the
    master table's own definition of Basic and Expert characters.
    """
    highest = max((member.level for member in session.party.living_members()), default=1)
    return "expert" if highest >= 4 else "basic"


def _merge_generated(bundle, generated) -> None:
    from osrlib.core.items import Coins

    bundle.coins = Coins(
        **{
            denomination: getattr(bundle.coins, denomination) + getattr(generated.coins, denomination)
            for denomination in ("pp", "gp", "ep", "sp", "cp")
        }
    )
    bundle.valuables.extend(generated.valuables)
    bundle.magic_items.extend(generated.magic_items)


def generate_carried_treasure(session, instances):
    """Generate carried treasure at spawn: individual letters per monster, group per group.

    Individual letters (P–T) read each instance's own template (packed-variant
    pools may mix); group letters (U–V) read the first instance's — the row's
    members share a stat block. The `multiplier` repeats the whole listed
    generation (the Noble's `V × 3`), pinned. Returns `(member_treasure,
    group_bundle)` for the encounter group.
    """
    from osrlib.core.treasure import TREASURE_STREAM, generate_treasure, plan_treasure_ref
    from osrlib.crawl.dungeon import TreasureBundle

    stream = session.streams.get(TREASURE_STREAM)
    tier = treasure_tier(session)
    member_treasure = {}
    for instance in instances:
        plan = plan_treasure_ref(instance.template.treasure)
        if not plan.individual:
            continue
        bundle = TreasureBundle()
        for letter in plan.individual:
            for _ in range(plan.multiplier):
                generated = generate_treasure(letter, tier=tier, stream=stream, allocator=session.allocator)
                _merge_generated(bundle, generated)
        if not bundle.empty:
            member_treasure[instance.id] = bundle
    group_plan = plan_treasure_ref(instances[0].template.treasure) if instances else None
    group_bundle = None
    if group_plan is not None and group_plan.group:
        bundle = TreasureBundle()
        for letter in group_plan.group:
            for _ in range(group_plan.multiplier):
                generated = generate_treasure(letter, tier=tier, stream=stream, allocator=session.allocator)
                _merge_generated(bundle, generated)
        if not bundle.empty:
            group_bundle = bundle
    return member_treasure, group_bundle


def field_npc_party(session, kind: str, count: int):
    """Generate an NPC party, register its members, and return them with the events.

    Members register in `session.npcs` (allocator prefix `npc`); the referee-only
    roster event carries classes and levels — the player-facing encounter event
    names "adventurers" and the count.
    """
    from osrlib.core.npc import NPC_PARTY_STREAM, generate_npc_party
    from osrlib.core.treasure import TREASURE_STREAM
    from osrlib.crawl.dungeon import TreasureBundle
    from osrlib.crawl.events import NpcPartySpawnedEvent

    party = generate_npc_party(
        kind,
        count=count,
        npc_stream=session.streams.get(NPC_PARTY_STREAM),
        treasure_stream=session.streams.get(TREASURE_STREAM),
        allocator=session.allocator,
    )
    for member in party.members:
        session.npcs[member.id] = member
    bundle = TreasureBundle(
        coins=party.treasure.coins,
        valuables=list(party.treasure.valuables),
        magic_items=list(party.treasure.magic_items),
    )
    event = NpcPartySpawnedEvent(
        party_kind=kind,
        npc_ids=tuple(member.id for member in party.members),
        class_ids=tuple(member.class_id for member in party.members),
        levels=tuple(member.level for member in party.members),
        alignment=party.alignment.value,
    )
    return party, (None if bundle.empty else bundle), [event]


def _assign_carried(session, carried) -> None:
    """Attach the spawn-time bundles to the encounter groups, in input order."""
    if session.encounter is None:
        return
    for group, (member_treasure, group_bundle) in zip(session.encounter.groups, carried, strict=True):
        group.member_treasure = member_treasure
        group.group_treasure = group_bundle


def _generate_cache(session, *, cell, treasure_types, entries_lists, extra_gp=0) -> list:
    """Land generated treasure as an engine-created cache in the state overlay."""
    from osrlib.core.items import Coins
    from osrlib.core.treasure import TREASURE_STREAM, generate_treasure_entries
    from osrlib.crawl.dungeon import GeneratedCache, TreasureBundle
    from osrlib.crawl.events import HoardGeneratedEvent

    stream = session.streams.get(TREASURE_STREAM)
    tier = treasure_tier(session)
    bundle = TreasureBundle()
    for entries in entries_lists:
        _merge_generated(
            bundle, generate_treasure_entries(entries, tier=tier, stream=stream, allocator=session.allocator)
        )
    if extra_gp:
        bundle.coins = Coins(
            **{
                denomination: getattr(bundle.coins, denomination) + (extra_gp if denomination == "gp" else 0)
                for denomination in ("pp", "gp", "ep", "sp", "cp")
            }
        )
    if bundle.empty:
        return []
    cache_id = session.allocator.allocate("cache")
    location = _location(session)
    ref = cell_ref(location.dungeon_id, location.level_number, cell)
    session.dungeon_state.generated_caches[cache_id] = GeneratedCache(
        cell_ref=ref,
        treasure_types=tuple(treasure_types),
        coins=bundle.coins,
        valuables=bundle.valuables,
        magic_items=bundle.magic_items,
    )
    return [
        HoardGeneratedEvent(
            cache_ref=cache_id,
            treasure_types=tuple(treasure_types),
            coins_gp_value=bundle.coins.value_gp,
            valuable_ids=tuple(valuable.instance_id for valuable in bundle.valuables),
            magic_item_ids=tuple(item.instance_id for item in bundle.magic_items),
        )
    ]


def _generate_lair_hoard(session, area, templates) -> list:
    """Generate a keyed area's lair hoard when its keyed encounter first spawns.

    The keyed monsters' hoard letters (A–O), parenthetical letters, and `extra_gp`
    land as one engine-created cache on the area's first listed cell (pinned).
    """
    from osrlib.core.treasure import plan_treasure_ref
    from osrlib.data import load_treasure_tables

    letters: list[str] = []
    extra_gp = 0
    for template in templates:
        plan = plan_treasure_ref(template.treasure)
        for _ in range(plan.multiplier):
            letters.extend(plan.lair)
        extra_gp += plan.extra_gp
    if not letters and not extra_gp:
        return []
    tables = load_treasure_tables()
    entries_lists = [tables.treasure_type(letter).entries for letter in letters]
    return _generate_cache(
        session, cell=area.cells[0], treasure_types=letters, entries_lists=entries_lists, extra_gp=extra_gp
    )


def _area_treasure_check(session) -> list:
    """Generate an area's declared treasure on first entry (the authoring surface)."""
    from osrlib.data import load_treasure_tables

    level = _level(session)
    area = level.area_at(_position(session))
    if area is None or area.treasure is None:
        return []
    area_ref = _area_ref(session, area.id)
    state = session.dungeon_state
    if area_ref in state.generated_treasure_areas:
        return []
    state.generated_treasure_areas.append(area_ref)
    tables = load_treasure_tables()
    if area.treasure.unguarded:
        band = tables.unguarded.band_for_level(level.number)
        return _generate_cache(
            session, cell=_position(session), treasure_types=("unguarded",), entries_lists=[band.entries]
        )
    entries_lists = [tables.treasure_type(letter).entries for letter in area.treasure.letters]
    return _generate_cache(
        session, cell=_position(session), treasure_types=area.treasure.letters, entries_lists=entries_lists
    )


# ---------------------------------------------------------------------- magic item use


def identify_item_events(session, member, instance: MagicItemInstance) -> list:
    """Identify an item (and reveal its curse) — the first-meaningful-use trigger.

    Cursed items identify as their true nature at the same trigger and pin to
    their bearer (`items.curse.stuck` on unequip, drop, and sale) until *remove
    curse* — the armour-reads-as-+1 deception collapses at first battle use,
    which is RAW's own reveal moment.
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


def _remove_magic_instance(member, instance: MagicItemInstance) -> None:
    inventory = member.inventory
    for collection in (inventory.items, inventory.wielded, inventory.rings):
        if any(existing is instance for existing in collection):
            collection.remove(instance)
            return
    if inventory.worn_armour is instance:
        inventory.worn_armour = None
    elif inventory.shield is instance:
        inventory.shield = None


def _release_instance_effects(session, instance: MagicItemInstance) -> list:
    """Release the ledger effects a worn item attached (its state remembers them)."""
    events: list[Event] = []
    for effect_id in instance.state.get("effect_ids", ()):
        if any(effect.effect_id == effect_id for effect in session.ledger.effects):
            events.extend(session.ledger.release(str(effect_id), session.registry()))
    if "effect_ids" in instance.state:
        instance.state = {key: value for key, value in instance.state.items() if key != "effect_ids"}
    return events


def attach_worn_item_effects(session, member, instance: MagicItemInstance) -> list:
    """Attach a worn item's ledger effects: the regeneration ring, the weakness onset.

    Item-sourced ledger effects attach undispellable (RAW's dispel exemption for
    items); the instance's state remembers the effect ids so unequipping releases
    them. A cursed ring reveals when its curse takes effect — the onset is
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
        events.extend(identify_item_events(session, member, instance))
        definition = EffectDefinition(
            kind="ring_of_weakness_onset",
            duration_unit=TimeUnit.ROUND,
            duration_amount=int(template.effect.params["onset_rounds"]),
            expiry="weakness_strength_set",
            params={"strength_set": int(template.effect.params["strength_set"])},
        )
    if definition is None:
        return events
    effect, attach_events = session.ledger.attach(
        definition, member.id, clock=session.clock, allocator=session.allocator, registry=session.registry()
    )
    events.extend(attach_events)
    if effect is not None:
        held = instance.state.get("effect_ids", ())
        instance.state = {**instance.state, "effect_ids": (*held, effect.effect_id)}
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


def _use_potion(session, member, instance: MagicItemInstance, template) -> tuple[list[Rejection], list]:
    from osrlib.core.combat import apply_healing

    stream = session.streams.get(MAGIC_STREAM)
    events: list[Event] = []
    effect_spec = template.effect
    instantaneous = effect_spec is not None and bool(effect_spec.params.get("instantaneous"))
    active_potions = [
        effect
        for effect in session.ledger.effects
        if effect.target_ref == member.id and effect.definition.params.get("item_source") == "potion"
    ]
    _remove_magic_instance(member, instance)
    events.extend(identify_item_events(session, member, instance))
    if active_potions and not instantaneous and effect_spec is not None:
        # Mixing, pinned as both printed consequences: both effects cancel and the
        # drinker is disabled for 3 turns; inapplicable to instantaneous or
        # permanent potions.
        events.append(
            ItemUsedEvent(code="items.potion.mixed", character_id=member.id, instance_id=instance.instance_id)
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
            sickness, member.id, clock=session.clock, allocator=session.allocator, registry=session.registry()
        )
        events.extend(attach_events)
        return [], events
    events.append(
        ItemUsedEvent(
            code="items.potion.drunk",
            character_id=member.id,
            instance_id=instance.instance_id,
            manual=template.manual if effect_spec is None else (),
        )
    )
    if effect_spec is None:
        return [], events
    if effect_spec.kind == "healing":
        paralysing = [
            effect
            for effect in session.ledger.active_on(member.id)
            if effect.definition.condition is Condition.PARALYSED
        ]
        if paralysing:
            # The potion's second usage: paralysing effects are negated instead.
            for effect in paralysing:
                events.extend(session.ledger.release(effect.effect_id, session.registry()))
        else:
            amount = roll(str(effect_spec.heal_dice), stream).total
            events.extend(apply_healing(member, amount, source="magical"))
        return [], events
    if effect_spec.kind == "save_or_die":
        save = saving_throw(member, SaveCategory(str(effect_spec.save_category)), magical=True, stream=stream)
        events.extend(save.events)
        if not save.passed:
            from osrlib.core.effects import kill

            events.extend(kill(member))
        return [], events
    definition = _potion_effect_definition(template, instance)
    if effect_spec.params.get("weekly_inversion"):
        # More than one in a week inverts the benefits (pinned per-character on the
        # clock's day, tracked as a ledger marker so it serializes).
        cooldown_active = bool(session.ledger.active_on(member.id, "invulnerability_cooldown"))
        if cooldown_active:
            inverted = tuple(spec.model_copy(update={"value": -spec.value}) for spec in definition.modifiers)
            definition = definition.model_copy(update={"modifiers": inverted})
        marker = EffectDefinition(
            kind="invulnerability_cooldown", duration_unit=TimeUnit.DAY, duration_amount=7, stacking="refresh"
        )
        _, marker_events = session.ledger.attach(
            marker, member.id, clock=session.clock, allocator=session.allocator, registry=session.registry()
        )
        events.extend(marker_events)
    _, attach_events = session.ledger.attach(
        definition,
        member.id,
        clock=session.clock,
        allocator=session.allocator,
        registry=session.registry(),
        stream=session.streams.get(EFFECTS_STREAM),
    )
    events.extend(attach_events)
    return [], events


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
        # Merely looking at the baneful script curses the reader, class regardless.
        _remove_magic_instance(member, instance)
        events.extend(identify_item_events(session, member, instance))
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

            result = drain_levels(
                member,
                load_classes().get(member.class_id),
                levels=1,
                xp_policy="halfway",
                stream=session.streams.get(ADVANCEMENT_STREAM),
            )
            events.extend(result.events)
        elif curse.id == "slow_healing":
            slow = EffectDefinition(
                kind="cursed_slow_healing",
                condition=Condition.DISEASED,
                params={"healing_rest_days": 2, "item_source": "cursed_scroll"},
            )
            _, attach_events = session.ledger.attach(
                slow, member.id, clock=session.clock, allocator=session.allocator, registry=session.registry()
            )
            events.extend(attach_events)
        return [], events
    if template.effect is not None and template.effect.kind == "ward":
        _remove_magic_instance(member, instance)
        events.extend(identify_item_events(session, member, instance))
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
        remaining = tuple(str(spell) for spell in instance.state.get("spells", ()))
        if not remaining:
            return [Rejection(code="items.scroll.spent", params={"item": instance.instance_id})], []
        spell_id = command.spell_id or remaining[0]
        if spell_id not in remaining:
            return [Rejection(code="items.scroll.no_such_spell", params={"spell": spell_id})], []
        spell = load_spells().get(spell_id)
        definition = load_classes().get(member.class_id)
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
        targets: list[object] = []
        target_refs = command.targets or ((command.target_id,) if command.target_id else ())
        for target_ref in target_refs:
            if target_ref.startswith("cell:"):
                targets.append(target_ref)
            elif target_ref in registry:
                targets.append(registry[target_ref])
            else:
                return [Rejection(code="magic.cast.unknown_target", params={"target": target_ref})], []
        context = _cast_context(session, targets, in_combat=False)
        cast_rejections = validate_cast(
            member,
            spell,
            mode,
            targets=targets,
            context=context,
            ledger=session.ledger,
            require_memorized=False,
        )
        if cast_rejections:
            return cast_rejections, []
        left = tuple(spell for spell in remaining if spell != spell_id) + tuple(
            spell_id for _ in range(remaining.count(spell_id) - 1)
        )
        if left:
            instance.state = {**instance.state, "spells": left}
        else:
            _remove_magic_instance(member, instance)
        events.extend(identify_item_events(session, member, instance))
        events.append(ItemUsedEvent(code="items.scroll.read", character_id=member.id, instance_id=instance.instance_id))
        if thief_reading:
            error_pct = int(thief_params.get("error_pct", 10))
            if stream.randbelow(100) + 1 <= error_pct:
                # The 10% error resolves as a simple fizzle consuming the spell
                # (pinned, registered — RAW's "unusual or deleterious effect" is
                # referee territory).
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
        events.extend(_stationary_silence(session, spell, result, targets))
        return [], events
    # Everything else on the scroll table (protection from magic, treasure maps)
    # is manual prose: reading it is supported, the game narrates.
    _remove_magic_instance(member, instance)
    events.extend(identify_item_events(session, member, instance))
    events.append(
        ItemUsedEvent(
            code="items.scroll.read",
            character_id=member.id,
            instance_id=instance.instance_id,
            manual=template.manual,
        )
    )
    return [], events


def device_area_events(session, member, instance: MagicItemInstance, template, group) -> list:
    """Resolve a device's wired area effect against one encounter group.

    Cones and spheres resolve through the Phase 4 footprint rule; each caught
    target saves per the item's spec. Device damage presents `magic` and is
    destructive (the SRD's equipment-destruction examples are energy deaths
    generally, pinned).
    """
    from osrlib.core.combat import DamageSource, deal_damage
    from osrlib.crawl import battle as battle_module

    effect_spec = template.effect
    stream = session.streams.get(MAGIC_STREAM)
    events: list[Event] = []
    candidates = battle_module._area_candidates(session, group, effect_spec.shape, dict(effect_spec.dimensions))
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


def spend_device_charge(instance: MagicItemInstance, template) -> None:
    """Spend one charge; the last one exhausts the item to inert, silently (RAW)."""
    if template.charges_dice is not None and instance.charges_remaining is not None:
        instance.charges_remaining -= 1


def _use_device(session, member, instance: MagicItemInstance, template, command) -> tuple[list[Rejection], list]:
    from osrlib.core.combat import apply_healing

    definition = load_classes().get(member.class_id)
    if not usable_by_class(template, definition):
        return [Rejection(code="items.use.not_usable", params={"item": instance.instance_id})], []
    if template.charges_dice is not None and (instance.charges_remaining or 0) <= 0:
        return [Rejection(code="items.device.inert", params={"item": instance.instance_id})], []
    effect_spec = template.effect
    group = None
    if effect_spec is not None and effect_spec.kind in ("damage_area", "condition_area"):
        if session.encounter is not None:
            if command.target_id is None:
                return [Rejection(code="items.use.target_required", params={"item": instance.instance_id})], []
            group = next((entry for entry in session.encounter.groups if entry.id == command.target_id), None)
            if group is None or group.fled or group.surrendered:
                return [Rejection(code="items.use.unknown_target", params={"target": command.target_id or ""})], []
    if effect_spec is not None and effect_spec.kind == "striking":
        return [Rejection(code="items.use.battle_only", params={"item": instance.instance_id})], []
    events: list[Event] = []
    events.extend(identify_item_events(session, member, instance))
    events.append(
        ItemUsedEvent(
            code="items.device.activated",
            character_id=member.id,
            instance_id=instance.instance_id,
            manual=template.manual if effect_spec is None else (),
        )
    )
    if effect_spec is not None and effect_spec.kind == "healing":
        target = member if command.target_id is None else session.registry().get(command.target_id)
        if target is None:
            return [Rejection(code="items.use.unknown_target", params={"target": command.target_id or ""})], []
        day_key = f"healed:{getattr(target, 'id', command.target_id)}"
        today = session.clock.days
        if effect_spec.params.get("once_per_target_per_day") and instance.state.get(day_key) == today:
            # RAW: effective on any individual at most once per day — the
            # activation still happens, nothing more heals.
            pass
        else:
            instance.state = {**instance.state, day_key: today}
            amount = roll(str(effect_spec.heal_dice), session.streams.get(MAGIC_STREAM)).total
            events.extend(apply_healing(target, amount, source="magical"))
    elif group is not None:
        events.extend(device_area_events(session, member, instance, template, group))
    spend_device_charge(instance, template)
    return [], events


def _toggle_sword_light(session, member, instance: MagicItemInstance, template) -> tuple[list[Rejection], list]:
    """The Sword +1, Light command toggle — the Phase 3 light-effect machinery."""
    events: list[Event] = []
    events.extend(identify_item_events(session, member, instance))
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


def handle_use_item(session, command: UseItem) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    instance = member.inventory.magic_item(command.item_id)
    if instance is None:
        return [Rejection(code="exploration.item.not_carried", params={"item": command.item_id})], []
    template = magic_item_template(instance)
    if template.category is MagicItemCategory.POTION:
        rejections, events = _use_potion(session, member, instance, template)
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

        events.extend(encounter_module.end_of_round(session))
    else:
        events.extend(session.advance_rounds(1))
    return [], events


# ---------------------------------------------------------------------- town services

HEALING_SERVICES: dict[str, tuple[str, int]] = {
    "cure_light_wounds": ("cure_light_wounds", 25),
    "cure_serious_wounds": ("cure_serious_wounds", 100),
    "cure_disease": ("cure_disease", 150),
    "neutralize_poison": ("neutralize_poison", 150),
    "remove_curse": ("remove_curse_c", 200),
    "raise_dead": ("raise_dead", 1500),
}
"""The temple's six services: spell id and price in gp (invented, registered).

All services are always available in the 1.0 marker town (registered — town size
and cleric availability are game territory).
"""


def lift_curses(session, member) -> list[Event]:
    """*Remove curse* lifts the bearer's item curses: stuck items become discardable.

    The item stays cursed and identified — the stuck marker clears — and item
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


def handle_sell_treasure(session, command: SellTreasure) -> tuple[list[Rejection], list[Event]]:
    sales: list[tuple[object, object]] = []  # (owner, valuable)
    for item_id in command.item_ids:
        found = None
        for member in session.party.members:
            if member.inventory.magic_item(item_id) is not None:
                return [Rejection(code="town.sell.no_fixed_value", params={"item": item_id})], []
            valuable = next(
                (candidate for candidate in member.inventory.valuables if candidate.instance_id == item_id), None
            )
            if valuable is not None:
                found = (member, valuable)
                break
        if found is None:
            return [Rejection(code="exploration.item.not_carried", params={"item": item_id})], []
        sales.append(found)
    events: list[Event] = []
    by_seller: dict[str, tuple[object, list]] = {}
    for member, valuable in sales:
        by_seller.setdefault(member.id, (member, []))[1].append(valuable)
    for member, valuables in by_seller.values():
        total = 0
        for valuable in valuables:
            member.inventory.valuables.remove(valuable)
            total += valuable.value_gp
        member.inventory.purse.gp += total
        events.append(
            TreasureSoldEvent(
                character_id=member.id,
                instance_ids=tuple(valuable.instance_id for valuable in valuables),
                gp_value=total,
            )
        )
    return [], events


def handle_purchase_healing(session, command: PurchaseHealing) -> tuple[list[Rejection], list[Event]]:
    try:
        member = session.member(command.character_id)
    except ValueError:
        return [Rejection(code="session.command.unknown_member", params={"character": command.character_id})], []
    spell_id, cost_gp = HEALING_SERVICES[command.service]
    spell = load_spells().get(spell_id)
    if not member.inventory.purse.can_afford(cost_gp):
        return [
            Rejection(code="items.purchase.insufficient_funds", params={"item": command.service, "cost_gp": cost_gp})
        ], []
    from osrlib.core.spells import validate_cast

    temple_cleric = _temple_cleric(spell)
    context = _cast_context(session, [member], in_combat=False)
    cast_rejections = validate_cast(
        temple_cleric, spell, spell.modes[0].key, targets=[member], context=context, ledger=session.ledger
    )
    if cast_rejections:
        return cast_rejections, []
    member.inventory.purse.spend(cost_gp)
    events: list[Event] = [HealingPurchasedEvent(character_id=member.id, service=command.service, cost_gp=cost_gp)]
    result = cast_spell(
        temple_cleric,
        spell,
        spell.modes[0].key,
        targets=[member],
        context=context,
        ledger=session.ledger,
        clock=session.clock,
        allocator=session.allocator,
        registry=session.registry(),
        ruleset=session.ruleset,
        stream=session.streams.get(MAGIC_STREAM),
        effects_stream=session.streams.get(EFFECTS_STREAM),
    )
    events.extend(result.events)
    if command.service == "remove_curse":
        events.extend(lift_curses(session, member))
    return [], events


def _temple_cleric(spell):
    """An abstract temple cleric at the minimum level able to cast the service.

    Built fresh per purchase — no draws, no registry entry; the magic and effects
    streams the spell already owns carry the rolls.
    """
    from osrlib.core.abilities import AbilityScore
    from osrlib.core.alignment import Alignment
    from osrlib.core.character import Character
    from osrlib.core.spells import MemorizedSpell, minimum_caster_level

    level = minimum_caster_level(spell)
    return Character(
        id="town-cleric",
        name="Temple cleric",
        class_id="cleric",
        race="human",
        level=level,
        xp=0,
        scores={ability: 9 for ability in AbilityScore},
        alignment=Alignment.LAWFUL,
        max_hp=1,
        current_hp=1,
        memorized_spells=(MemorizedSpell(spell_id=spell.id),),
    )


HANDLERS = {
    MoveParty: handle_move_party,
    TurnParty: handle_turn_party,
    ReorderParty: handle_reorder_party,
    OpenDoor: handle_open_door,
    CloseDoor: handle_close_door,
    ForceDoor: handle_force_door,
    WedgeDoor: handle_wedge_door,
    ListenAtDoor: handle_listen_at_door,
    PickLock: handle_pick_lock,
    Search: handle_search,
    InspectTreasure: handle_inspect_treasure,
    RemoveTreasureTrap: handle_remove_treasure_trap,
    TakeTreasure: handle_take_treasure,
    DropItems: handle_drop_items,
    LightSource: handle_light_source,
    ExtinguishSource: handle_extinguish_source,
    EquipItem: handle_equip_item,
    UnequipItem: handle_unequip_item,
    Rest: handle_rest,
    PrepareSpells: handle_prepare_spells,
    CastSpell: handle_cast_spell,
    UseItem: handle_use_item,
    UseStairs: handle_use_stairs,
    EnterDungeon: handle_enter_dungeon,
    TravelToTown: handle_travel_to_town,
    PurchaseEquipment: handle_purchase_equipment,
    SellTreasure: handle_sell_treasure,
    PurchaseHealing: handle_purchase_healing,
}
