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
from osrlib.core.items import ItemInstance, equip, unequip, validate_equip
from osrlib.core.spells import MAGIC_STREAM, CastContext, cast_spell, memorize_spells, validate_cast
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
    RemoveTreasureTrap,
    ReorderParty,
    Rest,
    Search,
    SessionMode,
    TakeTreasure,
    TravelToTown,
    TurnParty,
    UnequipItem,
    UseStairs,
    WedgeDoor,
)
from osrlib.crawl.dungeon import (
    Coins,
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
    DetectionRolledEvent,
    DoorEvent,
    FatigueEvent,
    ItemAcquiredEvent,
    ItemsDroppedEvent,
    LightEvent,
    ListenedEvent,
    LocationEnteredEvent,
    PartyMovedEvent,
    ProvisionsEvent,
    RestedEvent,
    SearchCompletedEvent,
    TrapEvent,
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
    while row.entry.kind == "npc_party":
        # NPC parties arrive in Phase 5; re-roll until a monster row, draws consumed.
        row = table.rows[stream.randbelow(20)]
    count = row.count_fixed if row.count_fixed is not None else roll(row.count_dice, stream).total
    count = max(1, count)
    entry = row.entry
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
    events.extend(
        encounter_module.start_encounter(
            session,
            groups=[(row.name, instances)],
            kind="wandering",
            monsters_roll_surprise=False,  # wandering monsters know the dungeon (pinned)
        )
    )
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
    if (
        old_location.kind == "dungeon"
        and old_location.dungeon_id == dungeon_id
        and old_location.level_number == level_number
    ):
        old_area = _level(session).area_at(old_location.position)
    state.location = PartyLocation(
        kind="dungeon", dungeon_id=dungeon_id, level_number=level_number, position=position, facing=facing
    )
    events: list[Event] = []
    if old_location.kind != "dungeon" or old_location.dungeon_id != dungeon_id:
        events.append(LocationEnteredEvent(location_kind="dungeon", location_id=dungeon_id, level_number=level_number))
    elif old_location.level_number != level_number:
        events.append(LocationEnteredEvent(location_kind="level", location_id=dungeon_id, level_number=level_number))
    state.mark_explored(dungeon_id, level_number, position)
    events.extend(_boundary_events(session, old_area, position))
    events.extend(_enter_hooks(session))
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
    for keyed in area.encounter.monsters:
        if keyed.count_fixed is not None:
            count = keyed.count_fixed
        else:
            count = max(1, roll(keyed.count_dice, session.streams.get(WANDERING_STREAM)).total)
        instances = session.spawn(keyed.template_id, count, alignment=area.encounter.alignment)
        groups.append((load_monsters().get(keyed.template_id).name, instances))
    return encounter_module.start_encounter(
        session,
        groups=groups,
        kind="keyed",
        area_ref=area_ref,
        monsters_aware=area.encounter.aware or area_ref in session.alerted_areas,
        party_aware=area_ref in session.heard_areas,
        pinned_stance=area.encounter.stance,
    )


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
    events.extend(_room_trap_check(session))
    events.extend(_keyed_encounter_check(session))
    events.extend(_accrue_movement(session, 10 if explored_before else 30))
    return [], events


def _swing_shut(session, *, previous) -> list[Event]:
    """Doors the party opened swing shut behind it unless wedged (pinned always)."""
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
        if location.position not in ((x, y), neighbour):
            state.open = False
            events.append(DoorEvent(code="exploration.door.swung_shut", x=x, y=y, direction=side))
    return events


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
    session.mode = SessionMode.TOWN
    session.dungeon_state.location = PartyLocation(kind="town")
    travel = session.adventure.town.travel_turns.get(location.dungeon_id, 0)
    events, _ = session.advance_turns(travel, field=False)
    events.append(LocationEnteredEvent(location_kind="town", location_id="town"))
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
    if feature is None or feature.kind != "treasure_cache":
        return [Rejection(code="exploration.feature.unknown", params={"feature": command.feature_id})], []
    light_rejections = _requires_light(session, member, infravision_suffices=False)
    if light_rejections:
        return light_rejections, []
    ref = _feature_ref(session, feature)
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
    trapped = feature.trap is not None and ref not in state.sprung_traps and ref not in state.removed_traps
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
    purse = taker.inventory.purse
    for denomination in ("pp", "gp", "ep", "sp", "cp"):
        setattr(purse, denomination, getattr(purse, denomination) + getattr(feature.coins, denomination))
    state.emptied_caches.append(ref)
    if feature.coins.value_gp:
        from osrlib.crawl.session import RecoveredTreasureRecord

        session.recovered_treasure.append(RecoveredTreasureRecord(source_ref=ref, gp_value=feature.coins.value_gp))
    events.append(
        ItemAcquiredEvent(character_id=taker.id, item_ids=tuple(item_ids), coins_gp_value=feature.coins.value_gp)
    )
    turn_events, _ = _spend_turn(session)
    events.extend(turn_events)
    return [], events


def _transfer_pile(taker, pile: DropPile) -> list[str]:
    equipment = load_equipment()
    item_ids: list[str] = []
    for dropped in pile.items:
        taker.inventory.items.append(ItemInstance(template=equipment.get(dropped.item_id), quantity=dropped.quantity))
        item_ids.extend([dropped.item_id] * dropped.quantity)
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
        counts[item_id] = counts.get(item_id, 0) + 1
    for item_id, needed in counts.items():
        held = sum(
            instance.quantity for instance in member.inventory.all_instances() if instance.template.id == item_id
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
    for item_id in command.item_ids:
        _consume_item(member, item_id)
    purse = member.inventory.purse
    for denomination in ("pp", "gp", "ep", "sp", "cp"):
        setattr(purse, denomination, getattr(purse, denomination) - getattr(command.coins, denomination))
    if to_pile:
        ref = _cell_ref(session)
        pile = session.dungeon_state.piles.setdefault(ref, DropPile())
        for item_id in command.item_ids:
            existing = next((entry for entry in pile.items if entry.item_id == item_id), None)
            if existing is None:
                pile.items.append(DroppedItem(item_id=item_id, quantity=1))
            else:
                existing.quantity += 1
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
        # Lighting oil ignites a dropped pool on the party's cell.
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
        (candidate for candidate in member.inventory.items if candidate.template.id == command.item_id), None
    )
    if instance is None:
        return [Rejection(code="exploration.item.not_carried", params={"item": command.item_id})], []
    definition = load_classes().get(member.class_id)
    equip_rejections = validate_equip(definition, instance, member.inventory)
    if equip_rejections:
        return equip_rejections, []
    equip(member.inventory, definition, instance)
    return [], []


def handle_unequip_item(session, command: UnequipItem) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    inventory = member.inventory
    equipped = [*inventory.wielded]
    if inventory.worn_armour is not None:
        equipped.append(inventory.worn_armour)
    if inventory.shield is not None:
        equipped.append(inventory.shield)
    instance = next((candidate for candidate in equipped if candidate.template.id == command.item_id), None)
    if instance is None:
        return [Rejection(code="exploration.item.not_equipped", params={"item": command.item_id})], []
    unequip(inventory, instance)
    return [], []


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
    UseStairs: handle_use_stairs,
    EnterDungeon: handle_enter_dungeon,
    TravelToTown: handle_travel_to_town,
    PurchaseEquipment: handle_purchase_equipment,
}
