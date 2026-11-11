"""Content the session can't run is refused before a session starts (issue #167, items 2 to 4 and 6 to 8).

Each case here used to pass [`validate_adventure`][osrlib.crawl.adventure.validate_adventure] and then
fail during play, some of them after the command had moved the party. The checks belong where the
content is read, so [`GameSession.new`][osrlib.crawl.session.GameSession.new] never starts on an
adventure with one of these faults. Item 14 of issue #162, overlapping areas, is here too.
"""

import pytest
from pydantic import ValidationError

from crawl_fixtures import build_party
from osrlib.core.clock import TimeUnit
from osrlib.core.effects import Condition
from osrlib.core.items import Coins
from osrlib.crawl.adventure import Adventure, TownSpec, validate_adventure
from osrlib.crawl.dungeon import (
    AreaSpec,
    AreaTreasureSpec,
    Direction,
    DungeonSpec,
    Edge,
    EdgeKind,
    FeatureSpec,
    LevelSpec,
    TransitionSpec,
    TrapEffect,
    TrapSpec,
    edge_key,
)
from osrlib.crawl.session import GameSession
from osrlib.data import load_equipment, load_monsters
from osrlib.errors import ContentValidationError

pytestmark = pytest.mark.xfail(reason="chunk: content-validation")


def corridor(areas=(), features=(), edges=None, width=3) -> LevelSpec:
    """A one-row level whose cells are joined by open edges, entered at the west end."""
    if edges is None:
        edges = {f"{x},0:west": Edge(kind=EdgeKind.OPEN) for x in range(1, width)}
    return LevelSpec(
        number=1, width=width, height=1, entrance=(0, 0), edges=edges, areas=tuple(areas), features=tuple(features)
    )


def adventure(level: LevelSpec | None = None, **fields) -> Adventure:
    crypt = DungeonSpec(id="crypt", name="Crypt", levels=(level or corridor(),))
    fields.setdefault("town", TownSpec(name="Threshold", travel_turns={"crypt": 1}))
    return Adventure(name="A", dungeons=(crypt,), **fields)


def check(candidate: Adventure) -> None:
    validate_adventure(candidate, load_monsters(), load_equipment())


def refused(candidate: Adventure) -> str:
    """Return the message `validate_adventure` raises for `candidate`, and check a session refuses it too."""
    with pytest.raises(ContentValidationError) as caught:
        check(candidate)
    with pytest.raises(ContentValidationError):
        GameSession.new(build_party(), candidate, seed=7)
    return str(caught.value)


def chute(**fields) -> TransitionSpec:
    target = {"to_dungeon_id": "crypt", "to_level_number": 1, "to_position": (0, 0), "to_facing": Direction.NORTH}
    return TransitionSpec(kind="chute", position=(1, 0), **{**target, **fields})


def trapped_area(effect: TrapEffect) -> AreaSpec:
    return AreaSpec(id="pit", cells=((1, 0),), trap=TrapSpec(kind="room", trigger="enter", effect=effect))


# ---------------------------------------------------------------------- treasure letters (item 2)


def test_an_unknown_treasure_letter_is_refused():
    vault = AreaSpec(id="vault", cells=((1, 0),), treasure=AreaTreasureSpec(letters=("A", "Z")))
    message = refused(adventure(corridor([vault])))
    assert "'Z'" in message
    assert "vault" in message


def test_known_treasure_letters_pass():
    vault = AreaSpec(id="vault", cells=((1, 0),), treasure=AreaTreasureSpec(letters=("A", "C")))
    check(adventure(corridor([vault])))


# ---------------------------------------------------------------------- a trap's chute (item 3)


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ({"to_dungeon_id": "nowhere"}, "nowhere"),
        ({"to_level_number": 9}, "9"),
        ({"to_position": (50, 50)}, "(50, 50)"),
    ],
)
def test_an_area_traps_chute_gets_the_transition_checks(target, expected):
    message = refused(adventure(corridor([trapped_area(TrapEffect(transition=chute(**target)))])))
    assert "pit" in message
    assert expected in message


def test_a_feature_traps_chute_gets_the_transition_checks():
    trap = TrapSpec(kind="treasure", trigger="open", effect=TrapEffect(transition=chute(to_dungeon_id="nowhere")))
    chest = FeatureSpec(id="chest", kind="treasure_cache", cell=(1, 0), coins=Coins(gp=5), trap=trap)
    message = refused(adventure(corridor(features=[chest])))
    assert "chest" in message
    assert "nowhere" in message


def test_a_trap_whose_chute_leads_somewhere_real_passes():
    check(adventure(corridor([trapped_area(TrapEffect(transition=chute()))])))


# ---------------------------------------------------------------------- a trap condition's duration (item 4)


@pytest.mark.parametrize(
    "duration",
    [
        {"condition_duration_unit": TimeUnit.TURN},
        {"condition_duration_amount": 2},
        {"condition_duration_dice": "1d4"},
        {"condition_duration_unit": TimeUnit.TURN, "condition_duration_amount": 2, "condition_duration_dice": "1d4"},
    ],
    ids=["a unit alone", "an amount with no unit", "dice with no unit", "an amount and dice together"],
)
def test_a_half_stated_duration_is_refused(duration):
    with pytest.raises(ValidationError):
        TrapEffect(condition=Condition.BLIND, **duration)


@pytest.mark.parametrize(
    "duration",
    [
        {},
        {"condition_duration_unit": TimeUnit.TURN, "condition_duration_amount": 2},
        {"condition_duration_unit": TimeUnit.ROUND, "condition_duration_dice": "1d4"},
    ],
    ids=["no duration", "an amount and a unit", "dice and a unit"],
)
def test_a_whole_duration_is_accepted(duration):
    effect = TrapEffect(condition=Condition.BLIND, **duration)
    check(adventure(corridor([trapped_area(effect)])))


# ---------------------------------------------------------------------- edge keys (item 6)


@pytest.mark.parametrize("key", ["0,0:east", "0,0:south", "9,9:north", "-1,0:west", "hello", "1,0", "1,0:West"])
def test_an_edge_key_the_level_never_reads_is_refused(key):
    message = refused(adventure(corridor(edges={key: Edge(kind=EdgeKind.OPEN)})))
    assert repr(key) in message


def test_canonical_edge_keys_pass():
    level = LevelSpec(
        number=1,
        width=2,
        height=2,
        entrance=(0, 0),
        edges={
            edge_key((x, y), side): Edge(kind=EdgeKind.WALL) for x in range(2) for y in range(2) for side in Direction
        },
    )
    check(adventure(level))


# ---------------------------------------------------------------------- a feature's cell (item 7)


def test_a_feature_outside_its_area_is_refused():
    chest = FeatureSpec(id="chest", kind="treasure_cache", cell=(0, 0), coins=Coins(gp=50))
    hall = AreaSpec(id="hall", cells=((1, 0), (2, 0)), features=(chest,))
    message = refused(adventure(corridor([hall])))
    assert "chest" in message
    assert "hall" in message


def test_a_feature_inside_its_area_passes():
    chest = FeatureSpec(id="chest", kind="treasure_cache", cell=(2, 0), coins=Coins(gp=50))
    hall = AreaSpec(id="hall", cells=((1, 0), (2, 0)), features=(chest,))
    check(adventure(corridor([hall])))


# ---------------------------------------------------------------------- travel time (item 8)


def test_a_negative_travel_time_is_refused():
    with pytest.raises(ValidationError):
        TownSpec(name="Threshold", travel_turns={"crypt": -3})


def test_a_travel_time_of_zero_is_accepted():
    check(adventure(town=TownSpec(name="Threshold", travel_turns={"crypt": 0})))


# ---------------------------------------------------------------------- overlapping areas (#162, item 14)


def test_two_areas_that_share_a_cell_are_refused():
    hall = AreaSpec(id="hall", cells=((1, 0), (2, 0)))
    shrine = AreaSpec(id="shrine", cells=((2, 0),))
    message = refused(adventure(corridor([hall, shrine])))
    assert "hall" in message
    assert "shrine" in message
    assert "(2, 0)" in message


# ---------------------------------------------------------------------- a repeated id inside the bundle (#162)


def test_a_repeated_bundled_id_is_not_called_a_clash_with_the_catalog():
    goblin = load_monsters().get("goblin")
    custom = goblin.model_copy(update={"id": "bog_witch", "name": "Bog Witch"})
    message = refused(adventure(monsters=(custom, custom)))
    assert "bog_witch" in message
    assert "collides with the catalog" not in message


def test_a_bundled_id_that_is_built_in_is_still_a_clash_with_the_catalog():
    message = refused(adventure(monsters=(load_monsters().get("goblin"),)))
    assert "goblin" in message
    assert "catalog" in message


# ---------------------------------------------------------------------- every fault in one pass


def test_every_fault_is_reported_at_once():
    vault = AreaSpec(id="vault", cells=((1, 0),), treasure=AreaTreasureSpec(letters=("Z",)))
    chest = FeatureSpec(id="chest", kind="treasure_cache", cell=(0, 0), coins=Coins(gp=50))
    hall = AreaSpec(id="hall", cells=((1, 0), (2, 0)), features=(chest,))
    edges = {
        "1,0:west": Edge(kind=EdgeKind.OPEN),
        "2,0:west": Edge(kind=EdgeKind.OPEN),
        "hello": Edge(kind=EdgeKind.OPEN),
    }
    message = refused(adventure(corridor([vault, hall], edges=edges)))
    for part in ("'Z'", "chest", "'hello'", "(1, 0)"):
        assert part in message
