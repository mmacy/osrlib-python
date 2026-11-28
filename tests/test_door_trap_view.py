"""The player view shows a room trap the party knows of on the door it threatens (#323).

`EdgeView.trap_state` is set on a door of an area with an open-trigger room trap once the party has found or sprung
the trap, but only on a door the party has tied to the trap: the door it found the trap through, the door whose
opening sprang it, or a door on a cell of the trapped area that the party has walked. The tests reach each state
through play, and check that the edge agrees with the `TrapEvent` of the find and with `EnteredAreaView.trap_state`,
and that it survives a save and load.

The layout, on level 1 of "crypt" (4 by 2):

```
(0,0) corridor | D1 | (1,0) blade_room (2,0) | D2 | (3,0) corridor
(0,1) corridor ----- (1,1) ----- (2,1) ----------- (3,1) corridor
```

The two room cells join by an open edge. Each corridor cell joins the one below it, and the bottom row is open, so the
party can walk from one door to the other without entering the room. The room's south side is wall.
"""

import json
from pathlib import Path

from crawl_fixtures import build_party
from osrlib.core.events import Visibility
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import EnterDungeon, GrantItem, LightSource, MoveParty, OpenDoor, Search
from osrlib.crawl.dungeon import (
    AreaSpec,
    Direction,
    DoorSpec,
    DungeonSpec,
    Edge,
    EdgeKind,
    LevelSpec,
    TransitionSpec,
    TrapEffect,
    TrapSpec,
    WanderingSpec,
    edge_key,
)
from osrlib.crawl.events import TrapEvent
from osrlib.crawl.session import GameSession
from osrlib.crawl.views import PlayerView
from osrlib.persistence import load_game, save_game

D1 = edge_key((0, 0), Direction.EAST)
D2 = edge_key((2, 0), Direction.EAST)
SECRET = edge_key((1, 0), Direction.SOUTH)
MEMBERS = ("character-0001", "character-0002", "character-0003", "character-0004")
SEEDS = range(1, 300)
TRAP_REF = "crypt:1:blade_room"

VIEW_2_11_0 = Path(__file__).parent / "saves" / "view_door_trap_player_view_2.11.0.json"
"""A `PlayerView` of the stock party on (0, 0) of this file's layout, after a dwarf found the blade room's trap
through the east door, dumped as JSON by osrlib 2.11.0. osrlib 2.11.0 had no `EdgeView.trap_state`."""


def door_trap_adventure(
    *, trigger: str = "open", entrance=(0, 0), secret_door: bool = False, effect: TrapEffect | None = None
) -> Adventure:
    """This file's layout, with the room's trap springing on `trigger` and the party arriving on `entrance`.

    With `secret_door`, the room's south side at (1, 0) has a secret door down to (1, 1). `effect` replaces the
    trap's effect, a blade that the referee resolves by hand.
    """
    effect = effect or TrapEffect(manual="A blade drops from the lintel.")
    blade = TrapSpec(kind="room", trigger=trigger, effect=effect)
    room = AreaSpec(id="blade_room", name="Blade room", description="A long hall.", cells=((1, 0), (2, 0)), trap=blade)
    edges = {
        D1: Edge(kind=EdgeKind.DOOR, door=DoorSpec()),
        D2: Edge(kind=EdgeKind.DOOR, door=DoorSpec()),
        edge_key((1, 0), Direction.EAST): Edge(kind=EdgeKind.OPEN),
        edge_key((0, 0), Direction.SOUTH): Edge(kind=EdgeKind.OPEN),
        edge_key((3, 0), Direction.SOUTH): Edge(kind=EdgeKind.OPEN),
        **{edge_key((x, 1), Direction.EAST): Edge(kind=EdgeKind.OPEN) for x in range(3)},
    }
    if secret_door:
        edges[SECRET] = Edge(kind=EdgeKind.DOOR, door=DoorSpec(kind="secret"))
    level = LevelSpec(
        number=1,
        width=4,
        height=2,
        entrance=entrance,
        areas=(room,),
        edges=edges,
        wandering=WanderingSpec(chance_in_six=0),
    )
    return Adventure(
        name="Door trap",
        town=TownSpec(name="Threshold"),
        dungeons=(DungeonSpec(id="crypt", name="Crypt", levels=(level,)),),
    )


def lit_session(seed: int, adventure: Adventure | None = None) -> GameSession:
    """The stock party in the dungeon with a torch burning."""
    session = GameSession.new(build_party(), adventure or door_trap_adventure(), seed=seed)
    session.execute(GrantItem(character_id="character-0001", item_id="torch", quantity=4))
    session.execute(GrantItem(character_id="character-0001", item_id="tinder_box"))
    assert session.execute(EnterDungeon(dungeon_id="crypt")).accepted
    for _ in range(20):
        lit = session.execute(LightSource(character_id="character-0001", item_id="torch"))
        if any(event.code == "exploration.light.lit" for event in lit.events):
            return session
    raise AssertionError("the torch never lit in twenty tinder attempts")


def search_for_traps(session: GameSession) -> list[TrapEvent]:
    """Every member searches for room traps until one finds something, and returns the finds."""
    for member in MEMBERS:
        result = session.execute(Search(character_id=member, kind="room_traps"))
        found = [event for event in result.events if isinstance(event, TrapEvent)]
        if found:
            return found
    return []


def edges(session: GameSession) -> dict:
    (level,) = session.view(Visibility.PLAYER).explored
    return level.edges


def area_states(session: GameSession) -> dict:
    (level,) = session.view(Visibility.PLAYER).explored
    return {area.id: area.trap_state for area in level.areas}


def found_through_the_west_door() -> tuple[GameSession, TrapEvent]:
    """A session in which a search from (0, 0) found the room's trap through D1."""
    for seed in SEEDS:
        session = lit_session(seed)
        found = search_for_traps(session)
        if found:
            return session, found[0]
    raise AssertionError("no seed let the party find the trap through the door")


def walk(session: GameSession, *directions: Direction) -> None:
    for direction in directions:
        result = session.execute(MoveParty(direction=direction))
        assert result.accepted, (direction, result.rejections)


def test_a_trap_found_through_a_door_shows_on_that_door_before_the_party_enters():
    session, found = found_through_the_west_door()
    assert (found.trap_ref, found.direction) == (TRAP_REF, "east")
    assert area_states(session) == {}, "precondition: the party hasn't entered the room"
    seen = edges(session)
    assert seen[edge_key((0, 0), Direction(found.direction))].trap_state == "found"
    assert seen[D1].kind == "door"
    assert all(edge.trap_state is None for key, edge in seen.items() if key != D1)


def test_the_room_s_other_door_stays_blank_until_the_party_walks_the_cell_beside_it():
    session, _ = found_through_the_west_door()
    walk(session, Direction.SOUTH, Direction.EAST, Direction.EAST, Direction.EAST, Direction.NORTH)
    seen = edges(session)
    assert D2 in seen, "precondition: the party stands at the room's east door"
    assert seen[D2].trap_state is None, "the party can't know the east door leads into the same room"
    assert seen[D1].trap_state == "found"
    assert area_states(session) == {}


def test_inside_the_room_the_doors_agree_with_the_area_as_the_party_walks_it():
    session, _ = found_through_the_west_door()
    assert session.execute(OpenDoor(direction=Direction.EAST)).accepted
    walk(session, Direction.EAST)
    assert area_states(session) == {"blade_room": "found"}
    assert edges(session)[D1].trap_state == "found", "the door seen from the room's side"
    assert edges(session)[D2].trap_state is None, "the party hasn't walked (2, 0)"
    walk(session, Direction.EAST)
    assert edges(session)[D2].trap_state == "found"
    assert edges(session)[D2].trap_state == area_states(session)["blade_room"]


def test_a_trap_sprung_by_opening_a_door_shows_sprung_on_that_door():
    for seed in SEEDS:
        session = lit_session(seed)
        result = session.execute(OpenDoor(direction=Direction.EAST))
        sprung = [event for event in result.events if event.code == "exploration.trap.sprung"]
        if not sprung:
            continue
        assert sprung[0].trap_ref == TRAP_REF
        assert edges(session)[D1].trap_state == "sprung"
        assert all(edge.trap_state is None for key, edge in edges(session).items() if key != D1)
        walk(session, Direction.SOUTH, Direction.EAST, Direction.EAST, Direction.EAST, Direction.NORTH)
        assert edges(session)[D2].trap_state is None
        return
    raise AssertionError("no seed sprang the trap on the door")


def test_a_chute_sprung_by_opening_a_door_shows_on_the_door_the_party_opened():
    chute = TransitionSpec(
        kind="chute",
        position=(1, 0),
        to_dungeon_id="crypt",
        to_level_number=1,
        to_position=(3, 1),
        to_facing=Direction.NORTH,
    )
    adventure = door_trap_adventure(effect=TrapEffect(transition=chute))
    for seed in SEEDS:
        session = lit_session(seed, adventure)
        result = session.execute(OpenDoor(direction=Direction.EAST))
        if not any(event.code == "exploration.trap.sprung" for event in result.events):
            continue
        assert session.dungeon_state.location.position == (3, 1), "precondition: the chute moved the party"
        assert edges(session)[D1].trap_state == "sprung"
        assert all(edge.trap_state is None for key, edge in edges(session).items() if key != D1)
        return
    raise AssertionError("no seed sprang the chute on the door")


def test_a_trap_found_from_inside_shows_on_the_doors_of_the_walked_cells():
    for seed in SEEDS:
        session = lit_session(seed)
        opened = session.execute(OpenDoor(direction=Direction.EAST))
        if any(event.code == "exploration.trap.sprung" for event in opened.events):
            continue
        walk(session, Direction.EAST)
        assert edges(session)[D1].trap_state is None, "precondition: the party hasn't met the trap"
        found = search_for_traps(session)
        if not found:
            continue
        assert found[0].direction is None
        assert edges(session)[D1].trap_state == "found"
        assert edges(session)[D2].trap_state is None
        assert area_states(session) == {"blade_room": "found"}
        return
    raise AssertionError("no seed let the party walk in unharmed and find the trap")


def test_a_party_that_arrives_inside_the_room_sees_the_trap_on_its_doors_once_found():
    adventure = door_trap_adventure(entrance=(2, 0))
    for seed in SEEDS:
        session = lit_session(seed, adventure)
        assert edges(session)[D2].trap_state is None
        if not search_for_traps(session):
            continue
        assert edges(session)[D2].trap_state == "found"
        assert edges(session)[D1].trap_state is None, "the party hasn't walked (1, 0)"
        assert area_states(session) == {"blade_room": "found"}
        return
    raise AssertionError("no seed let the party find the trap")


def test_an_enter_trigger_trap_never_shows_on_a_door():
    adventure = door_trap_adventure(trigger="enter", entrance=(2, 0))
    for seed in SEEDS:
        session = lit_session(seed, adventure)
        if session.dungeon_state.sprung_traps or not search_for_traps(session):
            continue
        assert area_states(session) == {"blade_room": "found"}
        assert all(edge.trap_state is None for edge in edges(session).values())
        return
    raise AssertionError("no seed let the party find the trap")


def test_an_undiscovered_secret_door_of_the_room_stays_a_wall_with_no_trap_state():
    adventure = door_trap_adventure(entrance=(1, 0), secret_door=True)
    for seed in SEEDS:
        session = lit_session(seed, adventure)
        if not search_for_traps(session):
            continue
        assert edges(session)[D1].trap_state == "found"
        assert edges(session)[SECRET].kind == "wall"
        assert edges(session)[SECRET].trap_state is None
        return
    raise AssertionError("no seed let the party find the trap")


def test_the_door_still_shows_the_trap_after_a_save_and_load():
    session, _ = found_through_the_west_door()
    document = json.loads(json.dumps(save_game(session)))
    restored = load_game(document)
    assert edges(restored)[D1].trap_state == "found"
    assert restored.view(Visibility.PLAYER) == session.view(Visibility.PLAYER)


def test_a_save_with_no_door_record_shows_the_trap_once_the_party_walks_in():
    session, _ = found_through_the_west_door()
    document = json.loads(json.dumps(save_game(session)))
    document_state = _dungeon_state(document)
    assert document_state.pop("trap_doors") == {TRAP_REF: ["crypt:1:" + D1]}
    restored = load_game(document)
    assert edges(restored)[D1].trap_state is None
    assert restored.execute(OpenDoor(direction=Direction.EAST)).accepted
    walk(restored, Direction.EAST)
    assert edges(restored)[D1].trap_state == "found"


def test_a_player_view_from_osrlib_2_11_0_validates_with_no_door_trap_state():
    document = json.loads(VIEW_2_11_0.read_text(encoding="utf-8"))
    (level,) = document["explored"]
    assert "trap_state" not in level["edges"][D1], "precondition: osrlib 2.11.0 had no EdgeView.trap_state"
    view = PlayerView.model_validate(document)
    assert view.explored[0].edges[D1].kind == "door"
    assert all(edge.trap_state is None for edge in view.explored[0].edges.values())


def _dungeon_state(document: dict) -> dict:
    """The serialized `DungeonState` inside a save document."""
    stack = [document]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if "found_traps" in node and "doors" in node:
                return node
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    raise AssertionError("the save has no dungeon state")


def two_room_adventure() -> Adventure:
    """Two one-cell rooms with open-trigger traps, west on (0, 0) and east on (1, 0), joined by the door D1."""
    blade = TrapSpec(kind="room", trigger="open", effect=TrapEffect(manual="A blade drops from the lintel."))
    level = LevelSpec(
        number=1,
        width=2,
        height=1,
        entrance=(0, 0),
        areas=(
            AreaSpec(id="west_room", name="West room", cells=((0, 0),), trap=blade),
            AreaSpec(id="east_room", name="East room", cells=((1, 0),), trap=blade),
        ),
        edges={D1: Edge(kind=EdgeKind.DOOR, door=DoorSpec())},
        wandering=WanderingSpec(chance_in_six=0),
    )
    return Adventure(
        name="Two rooms",
        town=TownSpec(name="Threshold"),
        dungeons=(DungeonSpec(id="crypt", name="Crypt", levels=(level,)),),
    )


def test_a_door_between_two_known_trapped_rooms_shows_sprung_over_found():
    for seed in SEEDS:
        session = lit_session(seed, two_room_adventure())
        session.execute(OpenDoor(direction=Direction.EAST))
        if session.dungeon_state.sprung_traps != ["crypt:1:west_room"]:
            continue
        assert edges(session)[D1].trap_state == "sprung"
        found = search_for_traps(session)
        if [(event.trap_ref, event.direction) for event in found] != [("crypt:1:east_room", "east")]:
            continue
        assert session.dungeon_state.found_traps == ["crypt:1:east_room"]
        assert edges(session)[D1].trap_state == "sprung"
        return
    raise AssertionError("no seed sprang the west room's trap alone and then found the east room's")


def test_a_walked_room_s_door_can_differ_from_the_room_when_the_far_room_s_trap_sprang():
    """The exception the `EdgeView.trap_state` docstring states: the far room's sprung trap outranks the found one."""
    for seed in SEEDS:
        session = lit_session(seed, two_room_adventure())
        session.execute(OpenDoor(direction=Direction.EAST))
        if session.dungeon_state.sprung_traps != ["crypt:1:east_room"]:
            continue
        found = search_for_traps(session)
        if [(event.trap_ref, event.direction) for event in found] != [("crypt:1:west_room", None)]:
            continue
        assert area_states(session) == {"west_room": "found"}
        assert edges(session)[D1].trap_state == "sprung"
        return
    raise AssertionError("no seed sprang the east room's trap alone and then found the west room's")
