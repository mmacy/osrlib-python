"""The commands that move the party around a dungeon, and between a dungeon and town.

They're `MoveParty`, `TurnParty`, `ReorderParty`, `UseStairs`, `EnterDungeon`, and `TravelToTown`. A step and a
crossing both end in the arrival checks of `osrlib.crawl._arrival`.
"""

from osrlib.core.combat import cannot_move
from osrlib.core.events import Event
from osrlib.core.validation import Rejection
from osrlib.crawl._arrival import (
    _boundary_events,
    _enter_hooks,
    _keyed_encounter_check,
    _relocate,
    _room_trap_check,
    _swing_shut,
    _swing_shut_on_leave,
)
from osrlib.crawl._doors import _door_state
from osrlib.crawl._gating import _gate_refusal, _gate_success, _pay_toll
from osrlib.crawl._odometer import _accrue_movement
from osrlib.crawl._party_location import _cell_ref, _level, _location, _position
from osrlib.crawl._treasure import _area_treasure_check
from osrlib.crawl.commands import EnterDungeon, MoveParty, ReorderParty, SessionMode, TravelToTown, TurnParty, UseStairs
from osrlib.crawl.dungeon import Direction, EdgeKind, PartyLocation, TransitionSpec, step
from osrlib.crawl.events import LocationEnteredEvent, PartyMovedEvent, PartyReorderedEvent


def _handle_move_party(session, command: MoveParty) -> tuple[list[Rejection], list[Event]]:
    from osrlib.crawl.exploration import exploration_rate

    level = _level(session)
    position = _position(session)
    if exploration_rate(session) <= 0:
        return [Rejection(code="exploration.move.cannot_move", params={"reason": "overloaded"})], []
    for member in session.party.living_members():
        if cannot_move(member):
            return [Rejection(code="exploration.move.cannot_move", params={"character": member.id})], []
    passage = level.passage_at(position, command.direction)
    if passage is not None:
        return _cross_passage(session, passage)
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


def _cross_passage(session, passage: TransitionSpec) -> tuple[list[Rejection], list[Event]]:
    """Walk the party through a passage, in place of a step that the wall on its side would block.

    The gate is the last validation step, as it is for `UseStairs`: a refusal costs no
    draw, no time, and no item. The crossing reports what `UseStairs` reports for the
    same destination, then the step itself, and it costs what a step onto the landing
    cell costs.
    """
    refusal = _gate_refusal(session, passage.requires)
    if refusal is not None:
        params: dict[str, int | str | tuple[int | str, ...]] = {}
        if refusal:
            params["refusal"] = refusal
        return [Rejection(code="exploration.transition.gate_refused", params=params)], []
    explored_before = session.dungeon_state.is_explored(
        passage.to_dungeon_id, passage.to_level_number, passage.to_position
    )
    events = _pay_toll(session, passage.requires)
    events.extend(
        _relocate(
            session,
            passage.to_dungeon_id,
            passage.to_level_number,
            passage.to_position,
            passage.to_facing,
            narrative=_gate_success(passage.requires),
            via="passage",
            transition_ref=_cell_ref(session, passage.position),
            walked=True,
        )
    )
    events.extend(_accrue_movement(session, 10 if explored_before else 30))
    return [], events


def _handle_turn_party(session, command: TurnParty) -> tuple[list[Rejection], list[Event]]:
    location = _location(session)
    position = _position(session)
    session.dungeon_state.location = location.model_copy(update={"facing": command.facing})
    return [], [
        PartyMovedEvent(code="exploration.party.turned", x=position[0], y=position[1], facing=command.facing.value)
    ]


def _handle_reorder_party(session, command: ReorderParty) -> tuple[list[Rejection], list[Event]]:
    by_id = {member.id for member in session.party.members}
    if sorted(command.order) != sorted(by_id):
        return [Rejection(code="exploration.party.bad_order")], []
    session.party.reorder(command.order)
    return [], [PartyReorderedEvent(order=tuple(command.order))]


def _handle_use_stairs(session, command: UseStairs) -> tuple[list[Rejection], list[Event]]:
    """Take the transition on the party's cell, paying its gate's toll at the threshold.

    The gate is the last validation step: a refusal costs no draw, no time, and no
    item, and the toll is taken only once the crossing is certain. The toll's event
    comes before the arrival's cascade.
    """
    transition = _level(session).transition_at(_position(session))
    if transition is None:
        return [Rejection(code="exploration.stairs.none")], []
    refusal = _gate_refusal(session, transition.requires)
    if refusal is not None:
        params: dict[str, int | str | tuple[int | str, ...]] = {}
        if refusal:
            params["refusal"] = refusal
        return [Rejection(code="exploration.transition.gate_refused", params=params)], []
    events = _pay_toll(session, transition.requires)
    events.extend(
        _relocate(
            session,
            transition.to_dungeon_id,
            transition.to_level_number,
            transition.to_position,
            transition.to_facing,
            narrative=_gate_success(transition.requires),
            via=transition.kind,
            # The cell the party leaves from, which is the cell the transition stands
            # on: the engine uses it to find the spec again, gate and all, on the far side.
            transition_ref=_cell_ref(session, transition.position),
        )
    )
    events.extend(_accrue_movement(session, 30))
    return [], events


def _handle_enter_dungeon(session, command: EnterDungeon) -> tuple[list[Rejection], list[Event]]:
    try:
        dungeon = session.adventure.dungeon(command.dungeon_id)
    except ValueError:
        return [Rejection(code="session.command.unknown_location", params={"dungeon": command.dungeon_id})], []
    entrance_level = next((level for level in dungeon.levels if level.entrance is not None), None)
    if entrance_level is None:
        return [Rejection(code="session.command.unknown_location", params={"dungeon": command.dungeon_id})], []
    # The award is based on what the party brings back: each departure
    # records the valuation, and the return award is the difference.
    session.snapshot_treasure()
    travel = session.adventure.town.travel_turns.get(command.dungeon_id, 0)
    events, _ = session.advance_turns(travel, field=False)
    session.mode = SessionMode.EXPLORING
    events.extend(
        _relocate(session, dungeon.id, entrance_level.number, entrance_level.entrance, Direction.NORTH, via="entrance")
    )
    return [], events


def _handle_travel_to_town(session, command: TravelToTown) -> tuple[list[Rejection], list[Event]]:
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
        # Immediate mode: town arrival awards nothing, and the snapshot still resets.
        session.defeated_monsters = []
        session.treasure_snapshot_cp = None
    return [], events
