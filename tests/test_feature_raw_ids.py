"""A feature command's raw id reaches and reveals nothing that a `feature:` handle doesn't (#318).

`TakeTreasure`, `InspectTreasure`, and `RemoveTreasureTrap` take a `feature:` handle from the player
view or a raw id. Both forms resolve against the same candidates, the features the party can reach
where it stands, so an id the players could guess gets them no further than the handles they're
shown. These tests pin what the `FeatureSpec.id` and `feature_id` docstrings say, and they pass
today.
"""

from crawl_fixtures import build_party
from osrlib.core.events import Visibility
from osrlib.core.items import Coins
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import (
    EnterDungeon,
    GrantItem,
    InspectTreasure,
    LightSource,
    MoveParty,
    RemoveTreasureTrap,
    TakeTreasure,
)
from osrlib.crawl.dungeon import (
    AreaSpec,
    AreaTreasureSpec,
    Direction,
    DungeonSpec,
    Edge,
    EdgeKind,
    FeatureSpec,
    LevelSpec,
    TrapEffect,
    TrapSpec,
    WanderingSpec,
)
from osrlib.crawl.session import GameSession

THIEF = "character-0002"
UNKNOWN = "exploration.feature.unknown"


def corridor_adventure() -> Adventure:
    """A four-cell corridor on level 1 and a one-cell level 2, with caches the party can't all reach.

    The party stands on (1, 0), where a trapped chest and an unfound construction trick are. A coffer
    sits on (3, 0), lit by the torch and so on the player map, but out of reach. A third cache is on
    level 2.
    """
    trap = TrapSpec(kind="treasure", trigger="open", effect=TrapEffect(manual="A puff of dust."))
    level_one = LevelSpec(
        number=1,
        width=4,
        height=1,
        entrance=(0, 0),
        edges={f"{x},0:west": Edge(kind=EdgeKind.OPEN) for x in range(1, 4)},
        features=(
            FeatureSpec(id="chest", kind="treasure_cache", cell=(1, 0), coins=Coins(gp=10), trap=trap),
            FeatureSpec(id="rotating_floor", kind="construction_trick", cell=(1, 0)),
            FeatureSpec(id="coffer", kind="treasure_cache", cell=(3, 0), coins=Coins(gp=5)),
        ),
        wandering=WanderingSpec(chance_in_six=0),
    )
    level_two = LevelSpec(
        number=2,
        width=1,
        height=1,
        features=(FeatureSpec(id="deep_chest", kind="treasure_cache", cell=(0, 0), coins=Coins(gp=1)),),
    )
    return Adventure(
        name="Raw ids",
        town=TownSpec(name="Threshold", travel_turns={"crypt": 1}),
        dungeons=(DungeonSpec(id="crypt", name="Crypt", levels=(level_one, level_two)),),
    )


def at_the_chest(seed: int = 5) -> GameSession:
    """The stock party on the chest's cell with a torch burning."""
    session = GameSession.new(build_party(), corridor_adventure(), seed=seed)
    session.execute(GrantItem(character_id="character-0001", item_id="torch", quantity=6))
    session.execute(GrantItem(character_id="character-0001", item_id="tinder_box"))
    session.execute(GrantItem(character_id=THIEF, item_id="thieves_tools"))
    assert session.execute(EnterDungeon(dungeon_id="crypt")).accepted
    for _ in range(20):  # tinder lights on a 1 or 2 in 6, so retry until the torch takes
        lit = session.execute(LightSource(character_id="character-0001", item_id="torch"))
        if any(event.code == "exploration.light.lit" for event in lit.events):
            break
    else:
        raise AssertionError("the torch never lit in twenty tinder attempts")
    assert session.execute(MoveParty(direction=Direction.EAST)).accepted
    return session


def handles_by_cell(session: GameSession) -> dict:
    view = session.view(Visibility.PLAYER)
    return {feature.cell: feature.id for level in view.explored for feature in level.features}


def commands_for(feature_id: str) -> list:
    return [
        InspectTreasure(character_id=THIEF, feature_id=feature_id),
        RemoveTreasureTrap(character_id=THIEF, feature_id=feature_id),
        TakeTreasure(feature_id=feature_id),
    ]


def refusal(result) -> list:
    return [(rejection.code, rejection.params) for rejection in result.rejections]


class TestTheSameCandidates:
    def test_the_player_map_lists_both_caches_and_no_unfound_trick(self):
        view = at_the_chest().view(Visibility.PLAYER)
        listed = [(feature.kind, feature.cell) for level in view.explored for feature in level.features]
        assert sorted(listed) == [("treasure_cache", (1, 0)), ("treasure_cache", (3, 0))]

    def test_a_raw_id_and_a_handle_do_the_same_thing(self):
        # Two sessions on one seed take the same commands, one naming the chest by its raw id and
        # one by its handle. Every event, and the player view after each command, match.
        by_raw, by_handle = at_the_chest(), at_the_chest()
        handle = handles_by_cell(by_handle)[(1, 0)]
        for raw_command, handle_command in zip(commands_for("chest"), commands_for(handle), strict=True):
            raw_result, handle_result = by_raw.execute(raw_command), by_handle.execute(handle_command)
            assert raw_result.accepted == handle_result.accepted
            assert [event.model_dump(mode="json") for event in raw_result.events] == [
                event.model_dump(mode="json") for event in handle_result.events
            ]
            assert by_raw.view(Visibility.PLAYER) == by_handle.view(Visibility.PLAYER)
        assert "crypt:1:chest" in by_raw.dungeon_state.emptied_caches

    def test_out_of_reach_features_are_refused_like_an_id_no_feature_has(self):
        # The coffer is on the player map, but on another cell. The deep chest is on another level.
        # The rotating floor is on the party's cell, but the party hasn't found it. Each one, by
        # raw id or by the coffer's handle, gets exactly the refusal that a made-up id gets.
        session = at_the_chest()
        coffer_handle = handles_by_cell(session)[(3, 0)]
        for feature_id in ("coffer", coffer_handle, "deep_chest", "rotating_floor", "no_such_feature"):
            for command in commands_for(feature_id):
                result = session.execute(command)
                assert refusal(result) == [(UNKNOWN, {"feature": feature_id})], (command, feature_id)

    def test_a_raw_id_shared_with_a_generated_cache_is_refused_and_the_handle_works(self):
        # On seed 1 the unguarded hall's treasure roll leaves a generated cache, `cache-0001`, on
        # the cell of an authored cache that has the same id. The raw id names both, so the session
        # refuses it. The authored cache's handle names one of them and works.
        hall = AreaSpec(
            id="hall",
            cells=((1, 0),),
            treasure=AreaTreasureSpec(unguarded=True),
            features=(FeatureSpec(id="cache-0001", kind="treasure_cache", coins=Coins(gp=3)),),
        )
        level = LevelSpec(
            number=1,
            width=2,
            height=1,
            entrance=(0, 0),
            edges={"1,0:west": Edge(kind=EdgeKind.OPEN)},
            areas=(hall,),
            wandering=WanderingSpec(chance_in_six=0),
        )
        adventure = Adventure(
            name="Shared id",
            town=TownSpec(name="Threshold", travel_turns={"crypt": 1}),
            dungeons=(DungeonSpec(id="crypt", name="Crypt", levels=(level,)),),
        )
        session = GameSession.new(build_party(), adventure, seed=1)
        assert session.execute(EnterDungeon(dungeon_id="crypt")).accepted
        assert session.execute(MoveParty(direction=Direction.EAST)).accepted
        assert "cache-0001" in session.dungeon_state.generated_caches
        assert refusal(session.execute(TakeTreasure(feature_id="cache-0001"))) == [(UNKNOWN, {"feature": "cache-0001"})]
        view = session.view(Visibility.PLAYER)
        (authored,) = [f for level in view.explored for f in level.features if f.kind == "treasure_cache"]
        assert session.execute(TakeTreasure(feature_id=authored.id)).accepted
        assert "crypt:1:cache-0001" in session.dungeon_state.emptied_caches
