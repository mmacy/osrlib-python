"""Light and sight in a dungeon, and the commands that light and put out a light source.

After every accepted command, the session adds the cells the party sees by its light to `DungeonState.seen`. The
encounter procedure limits how far away monsters appear to the party's longest straight line of sight. The handlers
for actions that need light, like picking a lock or reading a scroll, call `_requires_light` before they change
anything.
"""

from osrlib.core.clock import TimeUnit
from osrlib.core.combat import burning_oil_pool_definition
from osrlib.core.effects import EffectDefinition
from osrlib.core.events import Event
from osrlib.core.validation import Rejection
from osrlib.crawl._party_location import _cell_ref, _level, _location, _position
from osrlib.crawl._party_members import _consume_item, _find_item, _member_able
from osrlib.crawl.commands import ExtinguishSource, LightSource
from osrlib.crawl.dungeon import Direction, EdgeKind, Position, edge_ref, step
from osrlib.crawl.events import LightEvent
from osrlib.data import load_equipment

# The dungeon grid is authored at the classic ten-foot square, so a torch's
# thirty-foot radius reaches three cells of open floor and a straight run of
# three cells is thirty feet of sight.
_CELL_FEET = 10


def _sight_passes(session, level, location, cell: Position, direction: Direction) -> bool:
    """Whether sight (and torchlight) crosses one cell edge.

    Open floor and an open, non-secret door let light through. Walls, blocked
    edges, and shut or undiscovered-secret doors stop it, the same passability
    rules that movement and the edge projection use.

    Args:
        session (osrlib.crawl.session.GameSession): The running session.
        level (osrlib.crawl.dungeon.LevelSpec): The level that contains the cell.
        location: The party's dungeon location, for the door overlay's refs.
        cell: The cell sight leaves.
        direction: Which of the cell's four edges sight crosses.

    Returns:
        True when sight crosses the edge.
    """
    edge = level.edge(cell, direction)
    if edge.kind is EdgeKind.OPEN:
        return True
    if edge.kind is EdgeKind.DOOR:
        ref = edge_ref(location.dungeon_id, location.level_number, cell, direction)
        state = session.dungeon_state.doors.get(ref)
        if edge.door.kind == "secret" and (state is None or not state.discovered):
            return False
        return bool(state.open) if state is not None else edge.door.starts_open
    return False


def _sight_line_feet(session) -> int | None:
    """The longest unobstructed straight sight line from the party's cell, in feet.

    Sight runs cell by cell along each of the four grid directions, crossing only
    the edges `_sight_passes` allows, and the longest of the four runs is how far
    the space lets the party see from where it stands: the chamber's span inside
    a chamber, the whole passage down a straight corridor. On a rectangular room
    this equals the room's extent from the party's cell, since the farthest corner
    is exactly the longer of the two runs away in grid (Chebyshev) distance.

    It measures geometry, not light: darkness doesn't shorten the line, because a
    monster the party can't see is just as far away. Light affects surprise, not
    distance.

    Args:
        session (osrlib.crawl.session.GameSession): The running session.

    Returns:
        The sight line in feet (never shorter than one cell), or `None` when the
        party is not standing on a dungeon cell.
    """
    location = _location(session)
    if location.kind != "dungeon":
        return None
    try:
        level = _level(session)
    except ValueError:
        return None
    origin = _position(session)
    longest = 0
    for direction in Direction:
        cell = origin
        run = 0
        while _sight_passes(session, level, location, cell, direction):
            cell = step(cell, direction)
            run += 1
        longest = max(longest, run)
    return max(1, longest) * _CELL_FEET


_DEFAULT_LIGHT_FEET = 30


def _light_radius_feet(params) -> int:
    """The radius of one light-family effect, in feet.

    Equipment and magic-item light sources store the radius under
    `light_radius_feet`. The *light* spell family stores it under `radius_feet`.
    Read whichever the source has, falling back to the torch default.
    """
    raw = params.get("light_radius_feet", params.get("radius_feet"))
    return int(raw) if raw is not None else _DEFAULT_LIGHT_FEET


def _light_reveal(session) -> tuple[str | None, set[Position]]:
    """Cells the party sees *right now* by the party's light, keyed to their level.

    This is sight, not exploration: the party glimpses the lit room it stands in
    and a few cells down open passages, but these cells never enter the persisted
    explored set, so seeing a room doesn't make walking it later any cheaper. The
    session does persist the result as map memory. After every accepted command it
    adds these cells to
    [`DungeonState.seen`][osrlib.crawl.dungeon.DungeonState.seen], so an
    automap can still show the glimpsed room after the party walks on. Torchlight
    fills the keyed room whole and spills through open doorways out to the light's
    radius. Walls, and shut or undiscovered doors, stop it. Empty unless the party
    stands in a dungeon with a light burning.

    Returns:
        The `"{dungeon}:{level}"` explored-map key for the party's level and the
        set of seen cells, or `(None, set())` when nothing is lit.
    """
    location = session.dungeon_state.location
    if location.kind != "dungeon":
        return None, set()
    lit, _ = session.party_light()
    if not lit:
        return None, set()
    try:
        level = session.adventure.dungeon(location.dungeon_id).level(location.level_number)
    except ValueError:
        return None, set()

    from osrlib.crawl.session import LIGHT_EFFECT_KINDS

    living_ids = {member.id for member in session.party.living_members()}
    radius_cells = max(
        (
            _light_radius_feet(effect.definition.params) // _CELL_FEET
            for effect in session.ledger.effects
            if effect.target_ref in living_ids and effect.definition.kind in LIGHT_EFFECT_KINDS
        ),
        default=_DEFAULT_LIGHT_FEET // _CELL_FEET,
    )
    origin = tuple(location.position)
    # The keyed room the party stands in is lit to its far corners, because the
    # party is standing inside it, so its open-connected cells are revealed even
    # past the torch's reach. Elsewhere, light spills through open passages only
    # within that straight-line (Chebyshev) reach. Both cases follow passability:
    # the flood only crosses an edge sight passes, so walls and shut or undiscovered
    # doors stop it, and an alcove sealed off inside a keyed room stays dark.
    area = level.area_at(origin)
    in_room = {tuple(cell) for cell in area.cells} if area is not None else frozenset()
    seen: set[Position] = {origin}
    frontier = [origin]
    while frontier:
        cell = frontier.pop()
        for direction in Direction:
            neighbour = step(cell, direction)
            if neighbour in seen or not level.in_bounds(neighbour):
                continue
            within_reach = max(abs(neighbour[0] - origin[0]), abs(neighbour[1] - origin[1])) <= radius_cells
            if not (within_reach or neighbour in in_room):
                continue
            if not _sight_passes(session, level, location, cell, direction):
                continue
            seen.add(neighbour)
            frontier.append(neighbour)
    return f"{location.dungeon_id}:{location.level_number}", seen


def _requires_light(session, member, *, infravision_suffices: bool) -> list[Rejection]:
    lit, infravision_allowed = session.party_light()
    if lit:
        return []
    if infravision_suffices and infravision_allowed and session.member_has_infravision(member):
        return []
    return [Rejection(code="exploration.action.requires_light", params={"character": member.id})]


def _handle_light_source(session, command: LightSource) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    if command.item_id not in ("torch", "lantern", "oil_flask"):
        return [Rejection(code="exploration.light.not_a_source", params={"item": command.item_id})], []
    in_pile = False
    if command.item_id == "oil_flask":
        # Lighting oil ignites a dropped pool on the party's cell, and there is
        # no cell to pool on in town.
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
        definition = _light_definition("torch", getattr(load_equipment().get("torch"), "params", {}))
        _, attach_events = _attach_light(session, definition, member.id)
        events.extend(attach_events)
    elif command.item_id == "lantern":
        _consume_item(member, "oil_flask")
        definition = _light_definition("lantern", getattr(load_equipment().get("lantern"), "params", {}))
        _, attach_events = _attach_light(session, definition, member.id)
        events.extend(attach_events)
    else:
        pile = session.dungeon_state.piles[_cell_ref(session)]
        entry = next(entry for entry in pile.items if entry.item_id == "oil_flask")
        if entry.quantity == 1:
            pile.items.remove(entry)
        else:
            entry.quantity -= 1
        if not pile.items and pile.coins.total_coins == 0 and not pile.valuables and not pile.magic_items:
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


def _handle_extinguish_source(session, command: ExtinguishSource) -> tuple[list[Rejection], list[Event]]:
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
