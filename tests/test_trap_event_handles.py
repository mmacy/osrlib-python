"""Trap and search events name a feature by the `feature:` handle the player view lists it under (#322).

`TrapEvent.feature_id` carries a treasure trap's cache handle, and `SearchCompletedEvent.feature_ids` carries the
handles of the construction tricks a search found, so a client can join each event to a `MapFeatureView.id`. The
tests reach every treasure-trap code through play and check that a handle never appears in an event before the player
view lists its feature.
"""

from crawl_fixtures import build_party
from osrlib.core.events import Visibility
from osrlib.core.items import Coins
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import (
    EnterDungeon,
    GrantItem,
    InspectTreasure,
    LightSource,
    RemoveTreasureTrap,
    Search,
    TakeTreasure,
)
from osrlib.crawl.dungeon import AreaSpec, DungeonSpec, FeatureSpec, LevelSpec, TrapEffect, TrapSpec, WanderingSpec
from osrlib.crawl.events import SearchCompletedEvent, TrapEvent
from osrlib.crawl.session import GameSession

ROSTER = (("Brakk", "fighter"), ("Sable", "thief"), ("Durin", "dwarf"))
THIEF = "character-0002"
DWARF = "character-0003"
SEEDS = range(1, 300)


def vault_adventure() -> Adventure:
    """A one-cell vault whose area lists a trapped strongbox and a construction trick, and has a room trap."""
    needle = TrapSpec(kind="treasure", trigger="open", effect=TrapEffect(manual="A needle pricks the hand."))
    vault = AreaSpec(
        id="vault",
        name="Vault",
        cells=((0, 0),),
        trap=TrapSpec(kind="room", trigger="enter", effect=TrapEffect(manual="Dust falls.")),
        features=(
            FeatureSpec(id="poisoned_strongbox", kind="treasure_cache", cell=(0, 0), coins=Coins(gp=5), trap=needle),
            FeatureSpec(id="hidden_lever_wall", kind="construction_trick"),
        ),
    )
    level = LevelSpec(
        number=1, width=1, height=1, entrance=(0, 0), areas=(vault,), wandering=WanderingSpec(chance_in_six=0)
    )
    return Adventure(
        name="Handles",
        town=TownSpec(name="Threshold"),
        dungeons=(DungeonSpec(id="crypt", name="Crypt", levels=(level,)),),
    )


def in_the_vault(seed: int, ruleset: Ruleset | None = None) -> GameSession:
    """The fighter, thief, and dwarf in the vault with a torch burning."""
    session = GameSession.new(build_party(ROSTER), vault_adventure(), seed=seed, ruleset=ruleset)
    session.execute(GrantItem(character_id="character-0001", item_id="torch"))
    session.execute(GrantItem(character_id="character-0001", item_id="tinder_box"))
    session.execute(GrantItem(character_id=THIEF, item_id="thieves_tools"))
    assert session.execute(EnterDungeon(dungeon_id="crypt")).accepted
    for _ in range(20):
        lit = session.execute(LightSource(character_id="character-0001", item_id="torch"))
        if any(event.code == "exploration.light.lit" for event in lit.events):
            return session
    raise AssertionError("the torch never lit in twenty tinder attempts")


def listed(session: GameSession) -> dict[str, str]:
    """The player view's features by handle, mapped to their kind."""
    view = session.view(Visibility.PLAYER)
    return {feature.id: feature.kind for level in view.explored for feature in level.features}


def cache_handle(session: GameSession) -> str:
    (handle,) = [handle for handle, kind in listed(session).items() if kind == "treasure_cache"]
    return handle


def trap_events(result) -> list[TrapEvent]:
    return [event for event in result.events if isinstance(event, TrapEvent)]


def found_session(ruleset: Ruleset | None = None) -> tuple[GameSession, TrapEvent]:
    """A session in which the thief has just found the strongbox's trap, and the find's event."""
    for seed in SEEDS:
        session = in_the_vault(seed, ruleset)
        found = trap_events(session.execute(InspectTreasure(character_id=THIEF, feature_id=cache_handle(session))))
        if found:
            return session, found[0]
    raise AssertionError("no seed let the thief find the trap")


def test_a_found_treasure_trap_names_the_listed_cache():
    session, found = found_session()
    assert found.code == "exploration.trap.found"
    assert found.visibility is Visibility.PLAYER
    assert found.feature_id == cache_handle(session)
    assert found.trap_ref == "crypt:1:poisoned_strongbox", "trap_ref keeps its meaning in a minor release"


def test_a_cache_inspected_by_its_raw_id_reports_the_handle_the_view_lists():
    for seed in SEEDS:
        session = in_the_vault(seed)
        handle = cache_handle(session)
        found = trap_events(session.execute(InspectTreasure(character_id=THIEF, feature_id="poisoned_strongbox")))
        if found:
            assert found[0].feature_id == handle
            return
    raise AssertionError("no seed let the thief find the trap")


def test_taking_the_cache_reports_its_handle_whether_the_trap_springs_or_not():
    seen = set()
    for seed in SEEDS:
        session = in_the_vault(seed)
        handle = cache_handle(session)
        session.execute(InspectTreasure(character_id=THIEF, feature_id=handle))
        for event in trap_events(session.execute(TakeTreasure(feature_id=handle))):
            assert event.feature_id == handle, event
            seen.add(event.code)
        if seen == {"exploration.trap.safe", "exploration.trap.sprung"}:
            return
    raise AssertionError(f"reached only {seen}")


def test_every_removal_outcome_reports_the_handle():
    seen = set()
    for ruleset in (Ruleset(), Ruleset(failed_trap_removal_springs=False)):
        for seed in SEEDS:
            session = in_the_vault(seed, ruleset)
            handle = cache_handle(session)
            if not trap_events(session.execute(InspectTreasure(character_id=THIEF, feature_id=handle))):
                continue
            for event in trap_events(session.execute(RemoveTreasureTrap(character_id=THIEF, feature_id=handle))):
                assert event.feature_id == handle, event
                seen.add(event.code)
    assert seen == {"exploration.trap.removed", "exploration.trap.sprung", "exploration.trap.removal_failed"}


def test_a_room_trap_event_has_no_feature_handle():
    for seed in SEEDS:
        session = in_the_vault(seed)
        for member in (DWARF, "character-0001", THIEF):
            found = trap_events(session.execute(Search(character_id=member, kind="room_traps")))
            if found:
                assert found[0].trap_ref == "crypt:1:vault"
                assert found[0].feature_id is None
                return
    raise AssertionError("no seed let the party find the room trap")


def test_a_found_construction_trick_is_named_by_the_handle_the_view_then_lists():
    for seed in SEEDS:
        session = in_the_vault(seed)
        before = listed(session)
        result = session.execute(Search(character_id=DWARF, kind="construction"))
        (completed,) = [event for event in result.events if isinstance(event, SearchCompletedEvent)]
        if not completed.found:
            assert completed.feature_ids == ()
            continue
        assert completed.found == ("construction:hidden_lever_wall",), "found keeps its meaning in a minor release"
        (handle,) = completed.feature_ids
        assert handle not in before, "the view didn't list the trick before the search found it"
        assert listed(session)[handle] == "construction_trick"
        return
    raise AssertionError("no seed let the dwarf find the trick")


def test_searches_that_find_no_trick_carry_no_handles():
    session = in_the_vault(1)
    for kind, member in (("secret_doors", DWARF), ("room_traps", "character-0001")):
        result = session.execute(Search(character_id=member, kind=kind))
        (completed,) = [event for event in result.events if isinstance(event, SearchCompletedEvent)]
        assert completed.feature_ids == ()


def test_an_event_from_osrlib_2_11_0_validates_with_no_handles():
    trap = TrapEvent.model_validate(
        {"event_type": "trap", "code": "exploration.trap.found", "trap_ref": "crypt:1:chest", "character_id": THIEF}
    )
    assert trap.feature_id is None
    search = SearchCompletedEvent.model_validate(
        {
            "event_type": "search_completed",
            "code": "exploration.search.found",
            "character_id": DWARF,
            "kind": "construction",
            "found": ["construction:hidden_lever_wall"],
        }
    )
    assert search.feature_ids == ()
