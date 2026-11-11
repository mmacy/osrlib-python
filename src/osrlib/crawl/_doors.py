"""The door commands, and the door state they read and change.

A door gets an entry in `DungeonState.doors` the first time a command or the referee changes it. Until then, the
helpers here read a default built from the door's authored spec, so reading a door never changes the state.
"""

from osrlib.core.classes import detection_chance, detection_check, thief_skill_check
from osrlib.core.events import Event
from osrlib.core.validation import Rejection
from osrlib.crawl._arrival import _door_trap_check
from osrlib.crawl._gating import _gate_refusal, _gate_success, _pay_toll
from osrlib.crawl._light import _requires_light
from osrlib.crawl._odometer import _spend_turn
from osrlib.crawl._party_location import _area_ref, _edge_ref, _level, _position
from osrlib.crawl._party_members import _consume_item, _consumed_item_id, _find_item, _member_able
from osrlib.crawl.commands import CloseDoor, ForceDoor, ListenAtDoor, OpenDoor, PickLock, WedgeDoor
from osrlib.crawl.dungeon import Direction, DoorState, EdgeKind, step
from osrlib.crawl.events import DetectionRolledEvent, DoorEvent, ItemConsumedEvent, ListenedEvent


def _seed(edge, state: DoorState) -> DoorState:
    """Apply the authored `starts_open` to a fresh overlay entry."""
    if edge.kind is EdgeKind.DOOR and edge.door.starts_open:
        state.open = True
    return state


def _new_door_state(adventure, ref: str) -> DoorState:
    """A new state for the door at `ref`, seeded from its authored spec and never stored.

    [`DungeonState.door`][osrlib.crawl.dungeon.DungeonState.door] calls this for a door with no
    entry, so the state it returns reads the same as the entry `_store_door_state` would store. A
    reference that names no door in the adventure gets an all-`False` state.

    Args:
        adventure (osrlib.crawl.adventure.Adventure): The session's adventure.
        ref: The door's [`edge_ref`][osrlib.crawl.dungeon.edge_ref].

    Returns:
        A new state, with `open` set when the door starts open.
    """
    try:
        dungeon_id, level_number, cell, side = ref.rsplit(":", 3)
        level = adventure.dungeon(dungeon_id).level(int(level_number))
    except ValueError:
        return DoorState()
    edge = level.edges.get(f"{cell}:{side}")
    return DoorState() if edge is None else _seed(edge, DoorState())


def _read_door_state(session, edge, ref: str) -> DoorState:
    """The overlay entry for one door, for reading, never stored.

    A door that has never been operated has no entry, so this returns a transient default
    seeded from the authored spec, which reads identically to the entry an
    operation would store. Reading never writes, so a refused command leaves the
    overlay exactly as it found it.

    Args:
        session (osrlib.crawl.session.GameSession): The running session.
        edge (osrlib.crawl.dungeon.Edge): The authored edge the door sits on.
        ref: The door's [`edge_ref`][osrlib.crawl.dungeon.edge_ref].

    Returns:
        The stored entry when one exists, else a transient seeded default.
    """
    state = session.dungeon_state.doors.get(ref)
    return state if state is not None else _seed(edge, DoorState())


def _store_door_state(session, edge, ref: str) -> DoorState:
    """The overlay entry for one door, for writing, created and seeded on first touch.

    Every mutation of a door goes through here: opening, closing, wedging,
    unlocking, discovering a secret door, and the referee's door writes. The
    authored `starts_open` seeds only a brand-new entry, so materializing a door
    that is already stored never rewrites what play did to it.

    Args:
        session (osrlib.crawl.session.GameSession): The running session.
        edge (osrlib.crawl.dungeon.Edge): The authored edge the door sits on.
        ref: The door's [`edge_ref`][osrlib.crawl.dungeon.edge_ref].

    Returns:
        The stored, mutable entry.
    """
    state = session.dungeon_state.doors.get(ref)
    if state is None:
        state = _seed(edge, DoorState())
        session.dungeon_state.doors[ref] = state
    return state


def _door_state(session, direction: Direction) -> DoorState:
    """The read-only overlay entry for the door on one side of the party's cell."""
    edge = _level(session).edge(_position(session), direction)
    return _read_door_state(session, edge, _edge_ref(session, direction))


def _materialize_door(session, direction: Direction) -> DoorState:
    """The stored overlay entry for the door on one side of the party's cell.

    When the party forces or opens a door, the handler sets `opened_by_party` on
    this entry, and the code that swings doors shut behind the party reads that flag.
    """
    edge = _level(session).edge(_position(session), direction)
    return _store_door_state(session, edge, _edge_ref(session, direction))


def _known_door(session, direction: Direction):
    """Return `(edge, state)` when a known door faces `direction`, else `None`.

    An undiscovered secret door is a wall to the party. A command against it is
    rejected exactly as one against blank stone, so the rejection doesn't reveal the door.
    """
    edge = _level(session).edge(_position(session), direction)
    if edge.kind is not EdgeKind.DOOR:
        return None
    state = _door_state(session, direction)
    if edge.door.kind == "secret" and not state.discovered:
        return None
    return edge, state


def _handle_open_door(session, command: OpenDoor) -> tuple[list[Rejection], list[Event]]:
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
    refusal = _gate_refusal(session, edge.door.requires)
    if refusal is not None:
        params: dict[str, int | str | tuple[int | str, ...]] = {"direction": command.direction.value}
        if refusal:
            params["refusal"] = refusal
        return [Rejection(code="exploration.door.gate_refused", params=params)], []
    # The toll is paid at the threshold: its event comes before the door's event.
    events: list[Event] = _pay_toll(session, edge.door.requires)
    stored = _materialize_door(session, command.direction)
    stored.open = True
    stored.opened_by_party = True
    x, y = _position(session)
    events.append(
        DoorEvent(
            code="exploration.door.opened",
            x=x,
            y=y,
            direction=command.direction.value,
            narrative=_gate_success(edge.door.requires),
        )
    )
    events.extend(_door_trap_check(session, command.direction))
    return [], events


def _handle_close_door(session, command: CloseDoor) -> tuple[list[Rejection], list[Event]]:
    known = _known_door(session, command.direction)
    if known is None:
        return [Rejection(code="exploration.door.no_door", params={"direction": command.direction.value})], []
    _, state = known
    if not state.open:
        return [Rejection(code="exploration.door.already_closed")], []
    if state.wedged:
        return [Rejection(code="exploration.door.wedged")], []
    _materialize_door(session, command.direction).open = False
    x, y = _position(session)
    return [], [DoorEvent(code="exploration.door.closed", x=x, y=y, direction=command.direction.value)]


def _handle_force_door(session, command: ForceDoor) -> tuple[list[Rejection], list[Event]]:
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
    refusal = _gate_refusal(session, edge.door.requires)
    if refusal is not None:
        # A gate-refused forcing never happened: no noise, no denied surprise, no
        # draw. The gate is checked before the attempt.
        params: dict[str, int | str | tuple[int | str, ...]] = {"direction": command.direction.value}
        if refusal:
            params["refusal"] = refusal
        return [Rejection(code="exploration.door.gate_refused", params=params)], []
    from osrlib.crawl.session import EXPLORATION_STREAM

    # Any attempt makes noise, so the noise flag marks the next wandering
    # check. Only a *failed* attempt denies the party surprise (RAW).
    session.noise_since_check = True
    check = detection_check(member.open_doors_chance, stream=session.streams.get(EXPLORATION_STREAM))
    x, y = _position(session)
    if check.passed:
        events: list[Event] = _pay_toll(session, edge.door.requires)
        stored = _materialize_door(session, command.direction)
        stored.open = True
        stored.opened_by_party = True
        events.append(
            DoorEvent(
                code="exploration.door.forced",
                x=x,
                y=y,
                direction=command.direction.value,
                character_id=member.id,
                narrative=_gate_success(edge.door.requires),
            )
        )
        # The member who forces the door open takes the trap rigged to it.
        events.extend(_door_trap_check(session, command.direction, triggerer=member))
        return [], events
    beyond = step(_position(session), command.direction)
    area = _level(session).area_at(beyond)
    if area is not None:
        ref = _area_ref(session, area.id)
        if ref not in session.alerted_areas:
            session.alerted_areas.append(ref)
    return [], [
        DoorEvent(code="exploration.door.stuck", x=x, y=y, direction=command.direction.value, character_id=member.id)
    ]


def _handle_wedge_door(session, command: WedgeDoor) -> tuple[list[Rejection], list[Event]]:
    known = _known_door(session, command.direction)
    if known is None:
        return [Rejection(code="exploration.door.no_door", params={"direction": command.direction.value})], []
    _, state = known
    if state.wedged:
        return [Rejection(code="exploration.door.wedged")], []
    spike_carrier = next(
        (
            member
            for member in session.party.living_members()
            if member.inventory.carried_item("iron_spikes") is not None
        ),
        None,
    )
    if spike_carrier is None:
        return [Rejection(code="exploration.door.no_spike")], []
    spike = _consume_item(spike_carrier, "iron_spikes")
    if spike is None:  # unreachable: the carrier was found by the same lookup
        raise ValueError(f"{spike_carrier.name} carries no iron spikes")
    _materialize_door(session, command.direction).wedged = True
    x, y = _position(session)
    return [], [
        ItemConsumedEvent(character_id=spike_carrier.id, item_id=_consumed_item_id(spike)),
        DoorEvent(code="exploration.door.wedged", x=x, y=y, direction=command.direction.value),
    ]


def _handle_listen_at_door(session, command: ListenAtDoor) -> tuple[list[Rejection], list[Event]]:
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
    definition = member.definition
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
            monsters = session.effective_monsters
            noisy = any("undead" not in monsters.get(keyed.template_id).categories for keyed in area.encounter.monsters)
            if noisy:
                heard = True
                if area_ref not in session.heard_areas:
                    session.heard_areas.append(area_ref)
    code = "exploration.listen.heard" if heard else "exploration.listen.silent"
    events.append(ListenedEvent(code=code, character_id=member.id, direction=command.direction.value))
    return [], events


def _handle_pick_lock(session, command: PickLock) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    definition = member.definition
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
        _materialize_door(session, command.direction).unlocked = True
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
