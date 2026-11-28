"""Acceptance tests for what the views say about map features and areas (issues 316 and 318).

A remote front end builds its map from `PlayerView` alone, so the view has to say what the party
knows about each feature and room:

- An authored cache the party has emptied stays in `features`, marked `emptied`.
- An entered area carries the `trap_state` of its room trap once the party has met the trap.
- `PlayerView.features_here` lists the handles the feature commands accept from where the party
  stands, from the same resolution the session runs, so the two can't drift.
- A generated cache names the entered area its cell belongs to in `area_id`.
- `RefereeView.feature_handles` gives a referee tool each feature's player handle, so it can send the
  `feature_id` the players do, while the handle key stays out of both views.

Every field is new and has a default, and none of them is stored in a save: each is derived from
state a 2.7.0 save already holds.

The Counting House, level 1 (6 x 1), every cell open to the next:

```text
    x0        x1     x2         x3          x4       x5
y0  ENT       hall   [counting  counting]   [cellar] hall
    mural            shelf      strongbox
```

- `counting` covers (2,0) and (3,0) and has an enter-trigger room trap (`snare`). It lists a trapped
  `strongbox` on (3,0), a `bare_shelf` with nothing in it on (2,0), a `tithe_bowl` for the whole room,
  and a `sliding_wall` construction trick for the whole room.
- `cellar` covers (4,0) and has an enter-trigger pit.
- `ledger_mural` is a custom level feature on the entrance.

`PlaceParty` moves the party without entry checks, so no room trap rolls unless a test walks in with
`MoveParty`.
"""

import json
from pathlib import Path

import pytest

from crawl_fixtures import build_party
from osrlib.core.effects import kill
from osrlib.core.events import Visibility
from osrlib.core.items import Coins
from osrlib.crawl import encounter as encounter_module
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import (
    EnterDungeon,
    GrantItem,
    InspectTreasure,
    LightSource,
    MoveParty,
    PlaceParty,
    RemoveTreasureTrap,
    Search,
    SessionMode,
    SpawnMonsters,
    TakeTreasure,
)
from osrlib.crawl.dungeon import (
    AreaSpec,
    Direction,
    DropPile,
    DungeonSpec,
    Edge,
    EdgeKind,
    FeatureSpec,
    GeneratedCache,
    LevelSpec,
    PartyLocation,
    TrapEffect,
    TrapSpec,
    WanderingSpec,
    cell_ref,
    edge_key,
)
from osrlib.crawl.session import GameSession
from osrlib.crawl.views import ExploredLevelView, MapFeatureView, PlayerView, RefereeView
from osrlib.errors import ContentValidationError
from osrlib.persistence import load_game, save_game
from test_loot_and_traps import shared_cache_session

FIGHTER = "character-0001"
THIEF = "character-0002"
MEMBERS = ("character-0001", "character-0002", "character-0003", "character-0004")

SEARCH_SEED = 1
"""A seed on which one of the stock party finds the counting room's snare from (2,0).

Each member searches once. The OSE SRD's dungeon adventuring rules: "If a character is searching in
the right location, there is a 1-in-6 chance of finding a room trap. (Some types of adventurers may
have an increased chance.)" On this seed the magic-user, the fourth to search, finds it.
"""

PIT_SEED = 2
"""A seed on which the cellar's pit springs when the party walks into it from (3,0).

The OSE SRD's trap rule: "When a character performs the action that triggers a trap, there is a 2-in-6
chance of the trap being sprung."
"""

SAVE_2_7_0 = Path(__file__).parent / "saves" / "view_features_2.7.0.json"
"""A save that osrlib 2.7.0 wrote, before any of these fields existed. It's kept as 2.7.0 wrote it.

The party stands on (3,0) with a torch lit. It has emptied the tithe bowl and found the counting room's
snare, and the save's `generated_caches` holds `cache-0901` on (3,0) and `cache-0902` on (1,0).
"""

CACHE_KINDS = ("treasure_cache", "generated_cache")


# ---------------------------------------------------------------------- the scenario


def counting_house(*, room_trap: bool = True) -> Adventure:
    """The Counting House. Without `room_trap`, the counting room is the same room with no snare."""
    needle = TrapSpec(kind="treasure", trigger="open", effect=TrapEffect(damage_dice="1d4"))
    snare = TrapSpec(kind="room", trigger="enter", effect=TrapEffect(damage_dice="1d6"))
    pit = TrapSpec(kind="room", trigger="enter", effect=TrapEffect(fall_feet=10))
    counting = AreaSpec(
        id="counting",
        name="Counting room",
        description="Ledgers are stacked to the ceiling.",
        cells=((2, 0), (3, 0)),
        trap=snare if room_trap else None,
        features=(
            FeatureSpec(
                id="strongbox",
                kind="treasure_cache",
                description="An iron strongbox.",
                cell=(3, 0),
                coins=Coins(gp=40),
                trap=needle,
            ),
            FeatureSpec(id="tithe_bowl", kind="treasure_cache", description="A tithe bowl.", coins=Coins(gp=9)),
            FeatureSpec(id="bare_shelf", kind="treasure_cache", description="A dusty shelf.", cell=(2, 0)),
            FeatureSpec(id="sliding_wall", kind="construction_trick", description="A wall that slides."),
        ),
    )
    cellar = AreaSpec(id="cellar", name="Cellar", description="The floor is damp.", cells=((4, 0),), trap=pit)
    level = LevelSpec(
        number=1,
        width=6,
        height=1,
        edges={edge_key((x, 0), Direction.EAST): Edge(kind=EdgeKind.OPEN) for x in range(5)},
        areas=(counting, cellar),
        features=(FeatureSpec(id="ledger_mural", kind="custom", description="A mural of ledgers.", cell=(0, 0)),),
        entrance=(0, 0),
        wandering=WanderingSpec(chance_in_six=0),
    )
    return Adventure(
        name="The Counting House",
        town=TownSpec(name="Threshold", travel_turns={"vault": 1}),
        dungeons=(DungeonSpec(id="vault", name="The Vault", levels=(level,)),),
    )


AUTHORED_REFS = {
    "vault:1:ledger_mural",
    "vault:1:strongbox",
    "vault:1:tithe_bowl",
    "vault:1:bare_shelf",
    "vault:1:sliding_wall",
}
"""Every feature The Counting House authors, as the reference the save uses for it."""


def new_session(
    seed: int = 7, *, room_trap: bool = True, player_handle_key: bytes | None = None, lit: bool = True
) -> GameSession:
    """Enter the vault and, with `lit`, light a torch, so the party can search and inspect."""
    session = GameSession.new(
        build_party(), counting_house(room_trap=room_trap), seed=seed, player_handle_key=player_handle_key
    )
    assert session.execute(EnterDungeon(dungeon_id="vault")).accepted
    if lit:
        session.execute(GrantItem(character_id=FIGHTER, item_id="torch", quantity=6))
        session.execute(GrantItem(character_id=FIGHTER, item_id="tinder_box"))
        for _ in range(20):
            result = session.execute(LightSource(character_id=FIGHTER, item_id="torch"))
            if any(event.code == "exploration.light.lit" for event in result.events):
                break
        else:
            raise AssertionError("the torch never lit")
    return session


def place(session: GameSession, x: int) -> None:
    result = session.execute(
        PlaceParty(
            location=PartyLocation(
                kind="dungeon", dungeon_id="vault", level_number=1, position=(x, 0), facing=Direction.EAST
            )
        )
    )
    assert result.accepted, [rejection.code for rejection in result.rejections]


def put_cache(session: GameSession, cache_id: str, x: int) -> None:
    """Record a rolled cache on (x, 0), the way the session records area treasure it rolls.

    The test writes the record directly, so that no treasure roll decides whether a cache exists.
    """
    session.dungeon_state.generated_caches[cache_id] = GeneratedCache(
        cell_ref=cell_ref("vault", 1, (x, 0)), treasure_types=("A",), coins=Coins(gp=5), origin="area"
    )


def player(session: GameSession) -> PlayerView:
    view = session.view(Visibility.PLAYER)
    assert isinstance(view, PlayerView)
    return view


def referee(session: GameSession) -> RefereeView:
    view = session.view(Visibility.REFEREE)
    assert isinstance(view, RefereeView)
    return view


def level_view(session: GameSession) -> ExploredLevelView:
    return next(level for level in player(session).explored if (level.dungeon_id, level.level_number) == ("vault", 1))


def by_description(session: GameSession) -> dict[str, MapFeatureView]:
    return {feature.description: feature for feature in level_view(session).features}


def generated(session: GameSession) -> dict[tuple[int, int], MapFeatureView]:
    return {feature.cell: feature for feature in level_view(session).features if feature.kind == "generated_cache"}


def area(session: GameSession, area_id: str):
    return next((entry for entry in level_view(session).areas if entry.id == area_id), None)


def clone(session: GameSession) -> GameSession:
    """An independent copy to try a command on, so the original stays as it was."""
    return load_game(json.loads(json.dumps(save_game(session))))


def codes(result) -> list[str]:
    return [rejection.code for rejection in result.rejections]


def find_the_snare(session: GameSession) -> None:
    """Each member searches the counting room for its trap until one finds it."""
    for member_id in MEMBERS:
        result = session.execute(Search(character_id=member_id, kind="room_traps"))
        assert result.accepted, codes(result)
        if any(event.code == "exploration.trap.found" for event in result.events):
            return
    raise AssertionError("nobody found the snare; SEARCH_SEED no longer finds it")


# ---------------------------------------------------------------------- an emptied authored cache


def test_an_emptied_authored_cache_stays_listed_under_its_handle_and_is_marked_emptied():
    session = new_session()
    place(session, 2)
    bowl = by_description(session)["A tithe bowl."]
    assert bowl.emptied is False

    assert session.execute(TakeTreasure(feature_id=bowl.id)).accepted

    after = by_description(session)["A tithe bowl."]
    assert (after.id, after.kind, after.cell, after.area_id) == (bowl.id, "treasure_cache", None, "counting")
    assert after.emptied is True
    assert [feature.description for feature in level_view(session).features if feature.emptied] == ["A tithe bowl."]


def test_the_emptied_flag_agrees_with_the_sessions_refusal():
    session = new_session()
    put_cache(session, "cache-0901", 3)
    place(session, 2)
    assert session.execute(TakeTreasure(feature_id=by_description(session)["A tithe bowl."].id)).accepted
    place(session, 3)
    view = player(session)
    caches = [feature for feature in level_view(session).features if feature.id in view.features_here]
    assert {feature.description for feature in caches} == {"An iron strongbox.", "A tithe bowl.", ""}

    for feature in caches:
        result = clone(session).execute(TakeTreasure(feature_id=feature.id, recipient_id=FIGHTER))
        if feature.emptied:
            assert codes(result) == ["exploration.feature.emptied"], feature
        else:
            assert "exploration.feature.emptied" not in codes(result), feature
    assert [feature.emptied for feature in caches] == [False, True, False]


def test_emptied_tells_the_players_nothing_before_the_party_empties_the_cache():
    """The bare shelf holds nothing, and the view doesn't say so until the party has emptied it."""
    session = new_session()
    place(session, 2)
    before = player(session)
    before_json = before.model_dump_json()
    assert '"emptied":true' not in before_json
    shelf = by_description(session)["A dusty shelf."]
    assert shelf.emptied is False

    assert session.execute(TakeTreasure(feature_id=shelf.id)).accepted

    after = player(session)
    after_json = after.model_dump_json()
    assert after_json.count('"emptied":true') == 1
    flipped = [
        feature.model_copy(update={"emptied": True}) if feature.id == shelf.id else feature
        for feature in level_view(session).features
    ]
    assert tuple(flipped) == next(level for level in after.explored if level.dungeon_id == "vault").features
    before_features = next(level for level in before.explored if level.dungeon_id == "vault").features
    assert [feature.model_copy(update={"emptied": False}) for feature in flipped] == list(before_features)


def test_a_taken_generated_cache_leaves_the_list_and_nothing_is_marked_emptied():
    session = new_session()
    put_cache(session, "cache-0901", 3)
    place(session, 3)
    handle = generated(session)[(3, 0)].id
    assert handle in player(session).features_here

    assert session.execute(TakeTreasure(feature_id=handle)).accepted

    features = level_view(session).features
    assert handle not in {feature.id for feature in features}
    assert not any(feature.emptied for feature in features)
    assert handle not in player(session).features_here


# ---------------------------------------------------------------------- room trap knowledge


def test_a_room_trap_the_party_finds_shows_on_its_area():
    session = new_session(SEARCH_SEED)
    place(session, 2)
    assert area(session, "counting").trap_state is None

    find_the_snare(session)

    assert area(session, "counting").trap_state == "found"
    assert area(session, "cellar") is None


def test_a_room_trap_that_springs_shows_as_sprung():
    session = new_session(PIT_SEED)
    place(session, 3)

    result = session.execute(MoveParty(direction=Direction.EAST))

    assert result.accepted, codes(result)
    assert "exploration.trap.sprung" in [event.code for event in result.events], "PIT_SEED no longer springs the pit"
    assert area(session, "cellar").trap_state == "sprung"
    assert area(session, "counting").trap_state is None


def test_a_room_whose_trap_the_party_has_not_met_looks_like_a_room_without_one():
    """The trapped house and the same house with no snare give the same view until the snare is found."""
    trapped = new_session(SEARCH_SEED)
    plain = new_session(SEARCH_SEED, room_trap=False)
    for session in (trapped, plain):
        place(session, 2)
    assert player(trapped).model_dump_json() == player(plain).model_dump_json()

    find_the_snare(trapped)
    for member_id in MEMBERS[: len(trapped.dungeon_state.search_attempts["cell:vault:1:2,0:room_traps"])]:
        assert plain.execute(Search(character_id=member_id, kind="room_traps")).accepted

    trapped_view = player(trapped).model_dump(mode="json")
    plain_view = player(plain).model_dump(mode="json")
    assert trapped_view != plain_view
    counting = next(entry for entry in trapped_view["explored"][0]["areas"] if entry["id"] == "counting")
    assert counting["trap_state"] == "found"
    counting["trap_state"] = None
    assert trapped_view == plain_view


# ---------------------------------------------------------------------- which features a command accepts here


def test_features_here_lists_the_caches_in_reach_in_the_order_of_features():
    session = new_session()
    handles = {feature.description: feature.id for feature in by_description(session).values()}

    place(session, 2)
    handles.update({feature.description: feature.id for feature in by_description(session).values()})
    assert player(session).features_here == (handles["A tithe bowl."], handles["A dusty shelf."])

    place(session, 3)
    assert player(session).features_here == (handles["An iron strongbox."], handles["A tithe bowl."])

    place(session, 1)
    assert player(session).features_here == ()
    place(session, 0)
    assert player(session).features_here == (), "a custom feature takes no command"


def test_features_here_agrees_with_the_commands_at_every_cell():
    """A cache the view lists is never refused for reach, and every other listed cache is."""
    session = new_session()
    put_cache(session, "cache-0901", 3)
    put_cache(session, "cache-0902", 1)
    session.dungeon_state.found_tricks.append("vault:1:sliding_wall")
    for x in range(6):
        place(session, x)
    checked = 0
    for x in range(6):
        place(session, x)
        view = player(session)
        features = level_view(session).features
        assert set(view.features_here) <= {feature.id for feature in features if feature.kind in CACHE_KINDS}
        for feature in features:
            commands = [
                TakeTreasure(feature_id=feature.id, recipient_id=FIGHTER),
                InspectTreasure(character_id=THIEF, feature_id=feature.id),
            ]
            for command in commands:
                result = clone(session).execute(command)
                if feature.id in view.features_here:
                    assert "exploration.feature.unknown" not in codes(result), (x, feature, command)
                else:
                    assert codes(result) == ["exploration.feature.unknown"], (x, feature, command)
                checked += 1
    # Every cell lists all seven features: the five authored ones, the found trick among them, and two caches.
    assert checked == 2 * 6 * 7


def test_a_cache_in_features_here_with_a_found_trap_takes_remove_treasure_trap():
    session = new_session()
    place(session, 3)
    box = by_description(session)["An iron strongbox."]
    assert box.id in player(session).features_here
    session.dungeon_state.found_traps.append("vault:1:strongbox")
    assert by_description(session)["An iron strongbox."].trap_state == "found"

    result = clone(session).execute(RemoveTreasureTrap(character_id=THIEF, feature_id=box.id))

    assert result.accepted, codes(result)


def test_features_here_is_empty_unless_the_party_is_exploring():
    session = GameSession.new(build_party(), counting_house(), seed=7)
    assert player(session).features_here == ()

    session = new_session()
    place(session, 2)
    assert player(session).features_here != ()
    spawned = session.execute(SpawnMonsters(template_id="goblin", count_fixed=1, distance_feet=30))
    assert spawned.accepted, codes(spawned)
    assert session.mode is SessionMode.ENCOUNTER
    assert player(session).features_here == ()


def test_features_here_never_names_a_feature_the_view_hides():
    """The sliding wall is in reach from (2,0), but the party hasn't found it, so its handle is nowhere."""
    session = new_session()
    place(session, 2)
    found = clone(session)
    found.dungeon_state.found_tricks.append("vault:1:sliding_wall")
    trick = by_description(found)["A wall that slides."]

    view = player(session)

    assert view.features_here
    assert trick.id not in view.model_dump_json()
    assert trick.id not in player(found).features_here


# ---------------------------------------------------------------------- a generated cache's area


def test_a_rolled_cache_in_an_entered_area_names_the_area():
    """In the loot tests' lair, the rolled area cache and the lair hoard both lie in the lair."""
    session, _, _, _ = shared_cache_session()
    for group in session.encounter.groups:
        for monster_id in group.monster_ids:
            kill(session.monsters[monster_id])
    encounter_module.end_encounter(session, "victory")

    level = next(entry for entry in player(session).explored if entry.level_number == 1)
    rolled = [feature for feature in level.features if feature.kind == "generated_cache"]

    assert len(rolled) == 2
    assert [feature.area_id for feature in rolled] == ["lair", "lair"]
    assert "lair" in {entry.id for entry in level.areas}


def test_a_generated_cache_never_names_an_area_the_party_has_not_entered():
    session = new_session()
    put_cache(session, "cache-0901", 4)
    put_cache(session, "cache-0902", 1)
    place(session, 3)
    assert (4, 0) in level_view(session).cells, "the torch shows the cellar's cell from (3,0)"

    before = player(session)
    assert generated(session)[(4, 0)].area_id is None
    assert '"cellar"' not in before.model_dump_json()

    place(session, 4)

    assert generated(session)[(4, 0)].area_id == "cellar"
    assert generated(session)[(1, 0)].area_id is None, "(1,0) is corridor"


# ---------------------------------------------------------------------- the referee view's handles


def test_the_referee_view_has_the_player_handle_of_every_feature():
    session = new_session()
    put_cache(session, "cache-0901", 3)
    session.dungeon_state.found_tricks.append("vault:1:sliding_wall")
    place(session, 2)

    handles = referee(session).feature_handles

    assert set(handles) == AUTHORED_REFS | {"cache-0901"}
    features = by_description(session)
    assert handles["vault:1:ledger_mural"] == features["A mural of ledgers."].id
    assert handles["vault:1:strongbox"] == features["An iron strongbox."].id
    assert handles["vault:1:tithe_bowl"] == features["A tithe bowl."].id
    assert handles["vault:1:bare_shelf"] == features["A dusty shelf."].id
    assert handles["vault:1:sliding_wall"] == features["A wall that slides."].id
    assert handles["cache-0901"] == generated(session)[(3, 0)].id
    assert len(set(handles.values())) == len(handles)
    assert all(handle.startswith("feature:") for handle in handles.values())


def test_a_referee_tool_sends_the_handle_the_players_see():
    session = new_session()
    put_cache(session, "cache-0901", 3)
    handles = referee(session).feature_handles
    place(session, 3)
    assert handles["vault:1:tithe_bowl"] in player(session).features_here

    assert session.execute(TakeTreasure(feature_id=handles["vault:1:tithe_bowl"])).accepted
    assert session.execute(TakeTreasure(feature_id=handles["cache-0901"])).accepted

    assert "vault:1:tithe_bowl" in referee(session).feature_handles
    assert "cache-0901" not in referee(session).feature_handles, "a taken rolled cache leaves the save"


def test_the_referee_view_has_a_handle_for_a_lair_cache_the_players_cannot_see_yet():
    session, _, area_cache_id, lair_cache_id = shared_cache_session()
    level = next(entry for entry in player(session).explored if entry.level_number == 1)

    handles = referee(session).feature_handles

    assert {area_cache_id, lair_cache_id} <= set(handles)
    shown = {feature.id for feature in level.features}
    assert handles[area_cache_id] in shown
    assert handles[lair_cache_id] not in shown


def test_the_referee_handles_follow_the_session_key_and_the_key_stays_out():
    first_key = bytes(range(32))
    second_key = bytes(range(1, 33))
    first = new_session(player_handle_key=first_key)
    second = new_session(player_handle_key=second_key)

    first_handles = referee(first).feature_handles
    second_handles = referee(second).feature_handles

    assert set(first_handles) == set(second_handles) == AUTHORED_REFS
    assert all(first_handles[ref] != second_handles[ref] for ref in AUTHORED_REFS)
    assert first_handles["vault:1:ledger_mural"] == by_description(first)["A mural of ledgers."].id
    assert second_handles["vault:1:ledger_mural"] == by_description(second)["A mural of ledgers."].id
    for session, key in ((first, first_key), (second, second_key)):
        referee_json = referee(session).model_dump_json()
        player_json = player(session).model_dump_json()
        assert key.hex() not in referee_json and key.hex() not in player_json
        assert "player_handle_key" not in referee_json and "player_handle_key" not in player_json
        assert "feature_handles" not in player_json


# ---------------------------------------------------------------------- save and load


def played_house() -> GameSession:
    """The state the 2.7.0 save holds: the bowl emptied, the snare found, two rolled caches, the party on (3,0)."""
    session = new_session(SEARCH_SEED)
    place(session, 2)
    find_the_snare(session)
    assert session.execute(TakeTreasure(feature_id=by_description(session)["A tithe bowl."].id)).accepted
    put_cache(session, "cache-0901", 3)
    put_cache(session, "cache-0902", 1)
    place(session, 3)
    return session


def assert_the_house_as_saved(session: GameSession) -> None:
    """What the view says about the state `played_house` builds."""
    view = player(session)
    features = by_description(session)
    rolled = generated(session)
    assert features["A tithe bowl."].emptied is True
    assert [feature.description for feature in level_view(session).features if feature.emptied] == ["A tithe bowl."]
    assert area(session, "counting").trap_state == "found"
    assert view.features_here == (features["An iron strongbox."].id, features["A tithe bowl."].id, rolled[(3, 0)].id)
    assert rolled[(3, 0)].area_id == "counting"
    assert rolled[(1, 0)].area_id is None
    handles = referee(session).feature_handles
    assert set(handles) == AUTHORED_REFS | {"cache-0901", "cache-0902"}
    assert handles["vault:1:tithe_bowl"] == features["A tithe bowl."].id
    assert handles["cache-0901"] == rolled[(3, 0)].id


def test_the_new_fields_read_the_same_after_save_and_load():
    session = played_house()
    assert_the_house_as_saved(session)

    restored = clone(session)

    assert player(restored) == player(session)
    assert referee(restored).feature_handles == referee(session).feature_handles
    assert_the_house_as_saved(restored)


def test_a_save_written_by_2_7_0_loads_with_the_new_fields():
    document = json.loads(SAVE_2_7_0.read_text(encoding="utf-8"))
    assert document["engine_version"] == "2.7.0"

    restored = load_game(document)

    assert_the_house_as_saved(restored)
    assert player(restored) == player(played_house())


def test_the_2_7_0_save_holds_the_state_the_house_builds():
    """The fixture is the state `played_house` builds, so the two tests above check the same thing."""
    document = json.loads(SAVE_2_7_0.read_text(encoding="utf-8"))
    state = load_game(document).dungeon_state
    assert state.emptied_caches == ["vault:1:tithe_bowl"]
    assert state.found_traps == ["vault:1:counting"]
    assert set(state.generated_caches) == {"cache-0901", "cache-0902"}
    assert state.location.position == (3, 0)
    assert load_game(document).dungeon_state == played_house().dungeon_state


def test_a_player_view_document_without_the_new_fields_validates_with_their_defaults():
    document = player(played_house()).model_dump(mode="json")
    document.pop("features_here", None)
    for level in document["explored"]:
        for entry in level["areas"]:
            entry.pop("trap_state", None)
        for feature in level["features"]:
            feature.pop("emptied", None)

    restored = PlayerView.model_validate(document)

    assert restored.features_here == ()
    assert all(entry.trap_state is None for level in restored.explored for entry in level.areas)
    assert not any(feature.emptied for level in restored.explored for feature in level.features)


# ---------------------------------------------------------------------- references that name nothing (#328)


@pytest.mark.parametrize(
    "ref",
    ["cell:vault:9:0,0", "cell:cellar:1:0,0", "cell:vault:1:9,0", "malformed-on-purpose"],
    ids=["no such level", "no such dungeon", "off the grid", "not a cell reference"],
)
def test_a_save_with_a_generated_cache_on_a_cell_that_does_not_exist_is_refused(ref):
    session = new_session()
    session.dungeon_state.generated_caches["cache-0009"] = GeneratedCache(
        cell_ref=ref, treasure_types=("A",), coins=Coins(gp=5), origin="area"
    )
    document = json.loads(json.dumps(save_game(session)))

    with pytest.raises(ContentValidationError) as excinfo:
        load_game(document)

    message = str(excinfo.value)
    assert "generated cache 'cache-0009'" in message
    assert repr(ref) in message


def test_a_save_with_a_lair_cache_whose_area_does_not_exist_is_refused():
    session = new_session()
    session.dungeon_state.generated_caches["cache-0009"] = GeneratedCache(
        cell_ref=cell_ref("vault", 1, (2, 0)), treasure_types=("A",), origin="lair", lair_area_ref="vault:1:vestry"
    )
    document = json.loads(json.dumps(save_game(session)))

    with pytest.raises(ContentValidationError, match="generated cache 'cache-0009' has lair_area_ref 'vault:1:vestry'"):
        load_game(document)


@pytest.mark.parametrize(
    ("origin", "lair_area_ref", "message"),
    [
        ("lair", None, "has origin 'lair' and no lair_area_ref"),
        ("area", "vault:1:cellar", "has origin 'area' and lair_area_ref 'vault:1:cellar'"),
        ("legacy", "vault:1:cellar", "has origin 'legacy' and lair_area_ref 'vault:1:cellar'"),
        ("lair", "vault:1:cellar", "has lair_area_ref 'vault:1:cellar', an area with no keyed encounter"),
    ],
    ids=["lair with no area", "area with an area", "legacy with an area", "lair on an area with no encounter"],
)
def test_a_save_with_a_cache_whose_lair_reference_does_not_fit_its_origin_is_refused(origin, lair_area_ref, message):
    # Each of these caches sits on a real cell, and `_cache_available` would never show it to the party standing there.
    session = new_session()
    session.dungeon_state.generated_caches["cache-0009"] = GeneratedCache(
        cell_ref=cell_ref("vault", 1, (4, 0)),
        treasure_types=("A",),
        coins=Coins(gp=5),
        origin=origin,
        lair_area_ref=lair_area_ref,
    )
    document = json.loads(json.dumps(save_game(session)))

    with pytest.raises(ContentValidationError, match=f"generated cache 'cache-0009' {message}"):
        load_game(document)


def test_a_save_with_a_lair_cache_off_its_areas_cells_is_refused():
    session, _, _, lair_cache_id = shared_cache_session()
    document = json.loads(json.dumps(save_game(session)))
    lair_cache = document["payload"]["dungeon_state"]["generated_caches"][lair_cache_id]
    lair_cache["cell_ref"] = lair_cache["cell_ref"].replace(":1,0", ":0,0")

    with pytest.raises(ContentValidationError, match="an area whose cells don't include its cell_ref"):
        load_game(document)


def test_a_save_with_a_lair_cache_the_party_cannot_see_yet_loads():
    # A lair cache stays hidden until its encounter is resolved, so an unresolved one is normal play.
    session, _, _, lair_cache_id = shared_cache_session()

    restored = clone(session)

    assert lair_cache_id in restored.dungeon_state.generated_caches
    assert lair_cache_id in referee(restored).feature_handles


@pytest.mark.parametrize(
    ("location", "message"),
    [
        ({"dungeon_id": "nowhere", "level_number": 1, "position": [0, 0]}, "names dungeon 'nowhere' level 1"),
        ({"dungeon_id": "vault", "level_number": 9, "position": [0, 0]}, "names dungeon 'vault' level 9"),
        ({"dungeon_id": "vault", "level_number": 1, "position": [99, 0]}, "party location \\(99, 0\\) is off the grid"),
    ],
    ids=["no such dungeon", "no such level", "off the grid"],
)
def test_a_save_with_the_party_somewhere_the_adventure_does_not_have_is_refused(location, message):
    # Before, the save loaded and every view raised `ValueError`.
    document = json.loads(json.dumps(save_game(new_session())))
    document["payload"]["dungeon_state"]["location"] = {"kind": "dungeon", "facing": "east", **location}

    with pytest.raises(ContentValidationError, match=message):
        load_game(document)


def test_a_save_with_a_drop_pile_on_a_cell_that_does_not_exist_is_refused():
    session = new_session()
    session.dungeon_state.piles["cell:vault:9:0,0"] = DropPile(coins=Coins(gp=3))
    document = json.loads(json.dumps(save_game(session)))

    with pytest.raises(ContentValidationError, match="drop pile key 'cell:vault:9:0,0'"):
        load_game(document)


def test_a_save_with_caches_and_piles_on_real_cells_loads():
    session = new_session()
    put_cache(session, "cache-0009", 4)
    session.dungeon_state.piles[cell_ref("vault", 1, (1, 0))] = DropPile(coins=Coins(gp=3))

    restored = clone(session)

    assert "cache-0009" in referee(restored).feature_handles
    assert restored.dungeon_state == session.dungeon_state
