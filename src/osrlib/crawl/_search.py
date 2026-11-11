"""Searching the party's cell, and finding and removing the trap on a treasure cache.

`Search` looks for secret doors, room traps, or construction tricks. `InspectTreasure` finds a cache's trap, and
`RemoveTreasureTrap` disarms it.
"""

from osrlib.core.classes import detection_chance, detection_check, thief_skill_check
from osrlib.core.events import Event
from osrlib.core.validation import Rejection
from osrlib.crawl._arrival import _areas_at_door, _resolve_trap
from osrlib.crawl._doors import _door_state, _materialize_door
from osrlib.crawl._light import _requires_light
from osrlib.crawl._odometer import _spend_turn
from osrlib.crawl._party_location import (
    _area_ref,
    _cell_ref,
    _dungeon_coords,
    _feature_ref,
    _features_here,
    _level,
    _position,
)
from osrlib.crawl._party_members import _member_able
from osrlib.crawl.commands import InspectTreasure, RemoveTreasureTrap, Search
from osrlib.crawl.dungeon import AreaSpec, Direction, EdgeKind, Position, cell_ref, step
from osrlib.crawl.events import DetectionRolledEvent, SearchCompletedEvent, TrapEvent


def _handle_search(session, command: Search) -> tuple[list[Rejection], list[Event]]:
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
    definition = member.definition
    chance = detection_chance(member, definition, command.kind)
    check = detection_check(chance, stream=session.streams.get(EXPLORATION_STREAM))
    events: list[Event] = [
        DetectionRolledEvent(
            character_id=member.id, kind=command.kind, chance=chance, roll=check.roll, passed=check.passed
        )
    ]
    found: list[str] = []
    if check.passed:
        found = _reveal(session, member, command.kind, events)
    code = "exploration.search.found" if found else "exploration.search.nothing"
    events.append(SearchCompletedEvent(code=code, character_id=member.id, kind=command.kind, found=tuple(found)))
    turn_events, _ = _spend_turn(session)
    events.extend(turn_events)
    return [], events


def _refund_trap_search(session, dungeon_id: str, level_number: int, position: Position, direction: Direction) -> None:
    """Clear both cells' `room_traps` search attempts, so every member on either side may search again.

    Call this when the secret door on `position`'s `direction` side is discovered. A secret door is
    one edge shared by two cells, and a `room_traps` search from either cell covers that edge. While
    the door is undiscovered it reads as blank wall from both cells, and the trap behind it is out
    of reach from both, so a member who searched the far cell had no chance at that trap either. So
    on discovery, this function refunds both cells, not only the one it was named from. Attempts of
    other kinds, and attempts on every other cell, stand.

    Args:
        session (osrlib.crawl.session.GameSession): The running session.
        dungeon_id: The dungeon the edge is in.
        level_number: The 1-based level number.
        position: The cell the discovery was named from.
        direction: Which side of `position` the door sits on. The cell across it shares the edge.
    """
    for cell in (position, step(position, direction)):
        session.dungeon_state.search_attempts.pop(f"{cell_ref(dungeon_id, level_number, cell)}:room_traps", None)


def _reveal(session, member, kind: str, events: list[Event]) -> list[str]:
    """Reveal every hidden feature of one kind on the party's cell, and no other cell.

    `member` is the member who searched, and each reveal event has that member's id as its
    `character_id`. Appends the reveal events to `events` and returns the reference tokens for what
    turned up, which are the tokens `_handle_search` puts in
    [`SearchCompletedEvent.found`][osrlib.crawl.events.SearchCompletedEvent]. Discovering a secret
    door also clears the room-trap search attempts on both cells its edge joins, this one and the
    one across it, through `_refund_trap_search`.
    """
    level = _level(session)
    position = _position(session)
    state = session.dungeon_state
    found: list[str] = []
    if kind == "secret_doors":
        discovered_directions: list[Direction] = []
        for direction in Direction:
            edge = level.edge(position, direction)
            if edge.kind is EdgeKind.DOOR and edge.door.kind == "secret":
                if not _door_state(session, direction).discovered:
                    _materialize_door(session, direction).discovered = True
                    found.append(f"secret_door:{direction.value}")
                    discovered_directions.append(direction)
        dungeon_id, level_number, _ = _dungeon_coords(session)
        for direction in discovered_directions:
            # The door that just appeared may have a trapped area behind it, which was
            # unfindable while the door was wall, on either of the two cells it joins.
            # Everyone on both sides gets their trap search back.
            _refund_trap_search(session, dungeon_id, level_number, position, direction)
    elif kind == "room_traps":
        # Each candidate records the door it was found through, or `None` for the
        # trap in the searcher's area. Only this walk computes the door's direction.
        # A renderer that had to work it out again would also have to repeat the
        # guards below, so the search records it on the token and on the event.
        candidates: list[tuple[AreaSpec, Direction | None]] = []
        own = level.area_at(position)
        if own is not None and own.trap is not None:
            candidates.append((own, None))
        # A door trap threatens from the corridor side too: the searched cell's
        # door edges count as part of it, so an open-trigger trap in the area
        # beyond is findable before the door is ever opened. An undiscovered
        # secret door stays blank wall, since finding the trap would leak the door.
        # The walk shares `_areas_at_door` with the spring path, so a door trap
        # is findable from exactly the cells it can spring from.
        for direction in Direction:
            edge = level.edge(position, direction)
            if edge.kind is not EdgeKind.DOOR:
                continue
            if edge.door.kind == "secret" and not _door_state(session, direction).discovered:
                continue
            for area in _areas_at_door(level, position, direction):
                if area is not own and area.trap is not None and area.trap.trigger == "open":
                    candidates.append((area, direction))
        for candidate, direction_found in candidates:
            trap_ref = _area_ref(session, candidate.id)
            if trap_ref not in state.found_traps and trap_ref not in state.sprung_traps:
                state.found_traps.append(trap_ref)
                bearing = None if direction_found is None else direction_found.value
                found.append(f"room_trap:{candidate.id}" + (f":{bearing}" if bearing is not None else ""))
                events.append(
                    TrapEvent(
                        code="exploration.trap.found", trap_ref=trap_ref, character_id=member.id, direction=bearing
                    )
                )
    elif kind == "construction":
        for feature in _features_here(session):
            if feature.kind != "construction_trick":
                continue
            ref = _feature_ref(session, feature)
            if ref not in state.found_tricks:
                state.found_tricks.append(ref)
                found.append(f"construction:{feature.id}")
    return found


def _handle_inspect_treasure(session, command: InspectTreasure) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    definition = member.definition
    if not definition.thief_skills:
        return [Rejection(code="exploration.trap.not_a_thief", params={"character": member.id})], []
    feature = next((f for f in _features_here(session) if f.id == command.feature_id), None)
    cache = session.dungeon_state.generated_caches.get(command.feature_id)
    if cache is not None and cache.cell_ref == _cell_ref(session):
        feature = None  # generated hoards are untrapped: the check runs and finds nothing
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


def _handle_remove_treasure_trap(session, command: RemoveTreasureTrap) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    definition = member.definition
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
    elif not session.ruleset.failed_trap_removal_springs:
        # RAW says only "attempted once per trap". With failed_trap_removal_springs off, a
        # failure leaves the trap armed and found, and only this thief is barred from retrying.
        events.append(TrapEvent(code="exploration.trap.removal_failed", trap_ref=ref, character_id=member.id))
    else:
        # With failed_trap_removal_springs on (the default), a failed removal springs the trap
        # on the thief, the classic reading. RAW says only "attempted once per trap".
        state.sprung_traps.append(ref)
        events.append(TrapEvent(code="exploration.trap.sprung", trap_ref=ref, character_id=member.id))
        events.extend(_resolve_trap(session, feature.trap, triggerer=member))
    turn_events, _ = _spend_turn(session)
    events.extend(turn_events)
    return [], events
