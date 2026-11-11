"""What happens when the party arrives on a cell, and how a trap springs and what it does.

After a step, a stair, a passage, the way in from town, or a trap's slide puts the party on a cell, the engine checks
the cell for a new area, the effects on it, the area's treasure, the area's trap, and its keyed encounter, in that
order. A slide can drop the party into another trapped area, so the trap functions and `_relocate` call each other.
Opening a door and opening a treasure cache spring their traps through the same `_spring_check`.
"""

from osrlib.core.classes import detection_check
from osrlib.core.clock import TimeUnit
from osrlib.core.combat import DamageSource, SaveCategory, deal_damage, falling_damage, saving_throw
from osrlib.core.dice import roll
from osrlib.core.effects import EFFECTS_STREAM, Condition, EffectDefinition
from osrlib.core.events import Event
from osrlib.crawl._party_location import _area_ref, _cell_ref, _dungeon_coords, _level, _location, _position
from osrlib.crawl._treasure import (
    _area_treasure_check,
    _assign_carried,
    _generate_carried_treasure,
    _generate_lair_hoard,
)
from osrlib.crawl.dungeon import AreaSpec, Direction, PartyLocation, Position, TrapSpec, step
from osrlib.crawl.events import DetectionRolledEvent, DoorEvent, LocationEnteredEvent, PartyMovedEvent, TrapEvent


def _boundary_events(session, old_area, new_position) -> list[Event]:
    dungeon_id, _, _ = _dungeon_coords(session)
    level = _level(session)
    area = level.area_at(new_position)
    if area is not None and area is not old_area:
        # Area ids are level-scoped, so the crossing includes the whole triple:
        # dungeon, level, area. The ids of the other kinds are unique without it.
        return [
            LocationEnteredEvent(
                location_kind="area", location_id=area.id, level_number=level.number, dungeon_id=dungeon_id
            )
        ]
    return []


def _enter_hooks(session) -> list[Event]:
    """Run location-bound effect enter behaviors, in attachment order.

    The burning-oil pool deals its 1d8 to each living member entering the cell.
    A *web* cell entangles each entering member with the escape countdown. A
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


def _spring_check(session, trap: TrapSpec, trap_ref: str, *, triggerer) -> tuple[list[Event], bool]:
    """Roll one trap's 2-in-6 spring die and resolve the hit.

    The one mechanism behind every springing action, whether the party enters a
    trapped area, opens a door of one, or opens a trapped cache: the referee-only
    spring die, then, on a spring, the sprung record, the player-facing event,
    and the effect resolution on the triggerer. Each caller decides which traps
    get a spring roll, and handles any follow-up for a trap the party already found (the
    cache path's `exploration.trap.safe`).
    """
    from osrlib.crawl.session import EXPLORATION_STREAM

    check = detection_check(2, stream=session.streams.get(EXPLORATION_STREAM))
    events: list[Event] = [DetectionRolledEvent(kind="trap_spring", chance=2, roll=check.roll, passed=check.passed)]
    if check.passed:
        session.dungeon_state.sprung_traps.append(trap_ref)
        events.append(TrapEvent(code="exploration.trap.sprung", trap_ref=trap_ref, character_id=triggerer.id))
        events.extend(_resolve_trap(session, trap, triggerer=triggerer))
    return events, check.passed


def _areas_at_door(level, position: Position, direction: Direction) -> list[AreaSpec]:
    """The keyed areas a door edge adjoins, far side first, each area once.

    The one definition of a door's areas, shared by the spring path and the
    room-trap search, so a door trap is findable from exactly the cells it can
    spring from.
    """
    far = level.area_at(step(position, direction))
    near = level.area_at(position)
    if near is far:
        near = None
    return [area for area in (far, near) if area is not None]


def _room_trap_check(session) -> list[Event]:
    """The enter-trigger room trap: 2-in-6 to spring, and a found trap never springs.

    A trap needs a living victim: the engine rolls no spring die for a party
    with no one left standing.
    """
    level = _level(session)
    area = level.area_at(_position(session))
    if area is None or area.trap is None or area.trap.trigger != "enter":
        return []
    trap_ref = _area_ref(session, area.id)
    state = session.dungeon_state
    if trap_ref in state.sprung_traps or trap_ref in state.found_traps:
        return []
    living = session.party.living_members()
    if not living:
        return []
    events, _ = _spring_check(session, area.trap, trap_ref, triggerer=living[0])
    return events


def _door_trap_check(session, direction: Direction, *, triggerer=None) -> list[Event]:
    """The open-trigger room trap: opening a door of the trapped area springs it.

    A door belongs to an area when either adjoining cell lies inside it, so the
    blade over the lintel drops on the way in and the way out alike. 2-in-6 to
    spring on each opening, like every triggering action. A found trap never
    springs, just as it doesn't when the party enters its area. The engine rolls for
    the far side first when a door joins two trapped areas. Each spring hits the opener,
    meaning the named `triggerer`, or the first living member in marching order,
    and passes to the next member standing if an earlier spring killed the opener.
    The engine rolls no die for a trap with no living victim left, and a spring whose effect
    moved the party away from the door (a chute) cancels the springs after it:
    the party is no longer at the door it opened.
    """
    dungeon_id, level_number, position = _dungeon_coords(session)
    level = _level(session)
    state = session.dungeon_state
    events: list[Event] = []
    for area in _areas_at_door(level, position, direction):
        if area.trap is None or area.trap.trigger != "open":
            continue
        trap_ref = f"{dungeon_id}:{level_number}:{area.id}"
        if trap_ref in state.sprung_traps or trap_ref in state.found_traps:
            continue
        location = _location(session)
        if (location.dungeon_id, location.level_number, location.position) != (dungeon_id, level_number, position):
            break
        living = session.party.living_members()
        if not living:
            break
        victim = triggerer if triggerer in living else living[0]
        spring_events, _ = _spring_check(session, area.trap, trap_ref, triggerer=victim)
        events.extend(spring_events)
    return events


def _resolve_trap(session, trap: TrapSpec, *, triggerer) -> list[Event]:
    """Resolve a sprung trap's effect: damage is automatic, with no attack roll.

    A passed save spares the victim outright when it negates, and always spares
    them from the kill and the condition, whatever `on_save` is, the rule
    [`_resolve_kill`][osrlib.core.spells] and [`_resolve_attachment`][osrlib.core.spells]
    apply on the spell side. A passed `half` save halves damage, rolled and
    falling alike. A transition moves the whole party when at least one victim
    fails the save, and always when the trap allows no save.

    Draws run on the exploration stream, since these are the trap
    procedure's dice. Durations rolled when a condition attaches run on the effects stream
    instead, matching every other effect attachment in the engine.
    """
    from osrlib.crawl.session import EXPLORATION_STREAM

    stream = session.streams.get(EXPLORATION_STREAM)
    victims = [triggerer] if trap.affects == "triggerer" else list(session.party.living_members())
    effect = trap.effect
    events: list[Event] = []
    # A chute with no save takes everyone. With a save, it takes the party only
    # when some victim fails it.
    someone_failed = effect.save is None
    for victim in victims:
        passed = False
        if effect.save is not None:
            save = saving_throw(
                victim, SaveCategory(effect.save.category), modifier=effect.save.modifier, stream=stream
            )
            events.extend(save.events)
            passed = save.passed
            someone_failed = someone_failed or not passed
            if passed and effect.save.on_save == "negates":
                continue
        # Past the negates gate, `passed` means a made "half" save: damage halves,
        # the kill and the condition are spared.
        if effect.kills:
            if passed:
                continue
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
            if passed:
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
                total = fall.total // 2 if passed else fall.total
                if total > 0:
                    events.extend(
                        deal_damage(
                            victim,
                            total,
                            source=DamageSource(kind="falling"),
                            rolls=fall.rolls,
                            clock=session.clock,
                        )
                    )
        if effect.condition is not None and not passed:
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
    if effect.transition is not None and someone_failed:
        # A slide relocates the whole party. The party model has one location,
        # so a trap transition moves everyone or no one.
        events.extend(
            _relocate(
                session,
                effect.transition.to_dungeon_id,
                effect.transition.to_level_number,
                effect.transition.to_position,
                effect.transition.to_facing,
                via="trap",
            )
        )
    return events


def _relocate(
    session,
    dungeon_id: str,
    level_number: int,
    position,
    facing,
    *,
    narrative=None,
    via: str | None = None,
    transition_ref: str | None = None,
    walked: bool = False,
) -> list[Event]:
    """Move the party to a cell (transitions, slides): explore, events, hooks.

    `narrative` is the success text the author wrote on the gate the party passed to
    get here. It goes on the `LocationEnteredEvent` for crossing into a new level or
    dungeon. A relocation within one level has no such event, so the text goes on a
    `PartyMovedEvent` for the landing cell instead. A relocation that isn't `walked`
    emits that event only when there's text to report.

    `via` and `transition_ref` describe the crossing. For `UseStairs` and for a passage,
    they're the kind of the transition taken and the cell it stands on. For a slide,
    `via` is `"trap"`, and for the way in, `"entrance"`. Every caller passes both, so a
    consumer never has to work out the crossing from where the party ended up. Both go
    on the same `LocationEnteredEvent` as `narrative`, so a relocation within one level
    drops them.

    `walked` is for a passage, which the party crosses on foot with `MoveParty`. The
    relocation then reports the step as well: a `PartyMovedEvent` for the landing cell
    after the crossing's events and before the landing cell's events. Within one level,
    the doors the party opened also swing shut behind it, as they do after any step.
    A relocation to another level shuts them on the way out whether or not the party
    walked.
    """
    state = session.dungeon_state
    old_location = state.location
    old_area = None
    leave_events: list[Event] = []
    same_level = (
        old_location.kind == "dungeon"
        and old_location.dungeon_id == dungeon_id
        and old_location.level_number == level_number
    )
    if same_level:
        old_area = _level(session).area_at(old_location.position)
    elif old_location.kind == "dungeon":
        leave_events = _swing_shut_on_leave(session)
    state.location = PartyLocation(
        kind="dungeon", dungeon_id=dungeon_id, level_number=level_number, position=position, facing=facing
    )
    events: list[Event] = []
    if old_location.kind != "dungeon" or old_location.dungeon_id != dungeon_id:
        events.append(
            LocationEnteredEvent(
                location_kind="dungeon",
                location_id=dungeon_id,
                level_number=level_number,
                narrative=narrative,
                via=via,
                transition_ref=transition_ref,
            )
        )
    elif old_location.level_number != level_number:
        events.append(
            LocationEnteredEvent(
                location_kind="level",
                location_id=dungeon_id,
                level_number=level_number,
                narrative=narrative,
                via=via,
                transition_ref=transition_ref,
            )
        )
    events.extend(leave_events)
    # Within one level there is no crossing event, so the PartyMovedEvent for the landing cell
    # contains the gate's success text. The session emits that event for every passage, and for
    # stairs only when there is text to show.
    beat = narrative if same_level else None
    if walked or beat:
        events.append(
            PartyMovedEvent(
                code="exploration.party.moved", x=position[0], y=position[1], facing=facing.value, narrative=beat
            )
        )
    state.mark_explored(dungeon_id, level_number, position)
    events.extend(_boundary_events(session, old_area, position))
    if walked and same_level:
        events.extend(_swing_shut(session, previous=old_location.position))
    events.extend(_enter_hooks(session))
    events.extend(_area_treasure_check(session))
    events.extend(_room_trap_check(session))
    events.extend(_keyed_encounter_check(session))
    return events


def _keyed_encounter_check(session) -> list[Event]:
    """Open the cell's keyed encounter on first entry.

    After a wipe earlier in the same command, like a chute that killed everyone on
    the way down, the engine spawns no monsters and opens no encounter for the
    dead.
    """
    from osrlib.crawl import encounter as encounter_module

    if session.encounter is not None or session.battle is not None:
        return []
    if not session.party.living_members():
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
        template = session.effective_monsters.get(keyed.template_id)
        templates.append(template)
        instances = session.spawn(keyed.template_id, count, alignment=area.encounter.alignment)
        groups.append((*encounter_module._group_labels(instances), instances))
        # Carried treasure is generated at spawn, one keyed line at a time in the order
        # the author listed them, and the lair hoard after that. Keep this order: it
        # fixes the draws on the treasure stream.
        carried.append(_generate_carried_treasure(session, instances))
    # A keyed encounter whose `hoard` is False gets no cache, as for a monster room the
    # stocking roll gave no treasure. The session rolls a lair's hoard once, so a party
    # that evades and comes back finds the same cache.
    events: list[Event] = []
    if area.encounter.hoard and area_ref not in session.dungeon_state.generated_lair_hoards:
        session.dungeon_state.generated_lair_hoards.append(area_ref)
        events = _generate_lair_hoard(session, area, templates)
    events.extend(
        encounter_module._open_encounter(
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


def _swing_shut(session, *, previous, leaving_level: bool = False) -> list[Event]:
    """Doors the party opened swing shut behind it unless wedged.

    With `leaving_level` (a transition or the trip to town), every party-opened
    unwedged door on the departed level shuts, because the party is gone from all
    of them.
    """
    location = _location(session)
    prefix = f"{location.dungeon_id}:{location.level_number}:"
    events: list[Event] = []
    for ref, state in session.dungeon_state.doors.items():
        if not ref.startswith(prefix) or not state.open or state.wedged or not state.opened_by_party:
            continue
        cell_part, side = ref.removeprefix(prefix).split(":")
        x, y = (int(value) for value in cell_part.split(","))
        # The canonical key identifies the edge from its south/east cell's north/west
        # side: the two adjoining cells are (x, y) and its north/west neighbour.
        neighbour = step((x, y), Direction.NORTH if side == "north" else Direction.WEST)
        if leaving_level or location.position not in ((x, y), neighbour):
            state.open = False
            events.append(DoorEvent(code="exploration.door.swung_shut", x=x, y=y, direction=side))
    return events


def _swing_shut_on_leave(session) -> list[Event]:
    """Close every party-opened unwedged door on the level being left."""
    return _swing_shut(session, previous=None, leaving_level=True)
