"""Tests that the arrival event names the town and the dungeon by their authored names (#314).

Before, the default line for a town arrival printed the location's kind and then its id, and both are `town`, so it
read "The party enters town town." `LocationEnteredEvent.display_name` holds the town's or the dungeon's authored
name, and the default line uses it. An event recorded before the field existed has no name, and its line still reads
as a sentence.
"""

import json

import pytest

from crawl_fixtures import build_adventure, build_party
from osrlib.core.events import Visibility
from osrlib.crawl.commands import EnterDungeon, PlaceParty, TravelToTown, UseStairs
from osrlib.crawl.dungeon import Direction, PartyLocation
from osrlib.crawl.events import LocationEnteredEvent, parse_any_event
from osrlib.crawl.session import GameSession
from osrlib.messages import format_message


def arrivals(result) -> list[LocationEnteredEvent]:
    return [event for event in result.events if isinstance(event, LocationEnteredEvent)]


def session() -> GameSession:
    return GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=7)


# ---------------------------------------------------------------------- the line


@pytest.mark.parametrize(
    ("fields", "line"),
    [
        ({"location_kind": "town", "location_id": "town", "display_name": "Threshold"}, "The party enters Threshold."),
        ({"location_kind": "town", "location_id": "town"}, "The party enters town."),
        (
            {"location_kind": "dungeon", "location_id": "delve", "display_name": "The Delve", "level_number": 1},
            "The party enters The Delve (level 1).",
        ),
        (
            {"location_kind": "dungeon", "location_id": "delve", "level_number": 1},
            "The party enters dungeon delve (level 1).",
        ),
        (
            {"location_kind": "level", "location_id": "delve", "display_name": "The Delve", "level_number": 2},
            "The party enters level 2 of The Delve.",
        ),
        (
            {"location_kind": "level", "location_id": "delve", "level_number": 2, "via": "trapdoor"},
            "The party enters level 2 of delve.",
        ),
        ({"location_kind": "town", "location_id": "town", "display_name": "   "}, "The party enters town."),
        (
            {"location_kind": "dungeon", "location_id": "delve", "display_name": " \t ", "level_number": 1},
            "The party enters dungeon delve (level 1).",
        ),
        ({"location_kind": "town", "location_id": "town", "display_name": "Barrow!"}, "The party enters Barrow!"),
        (
            {"location_kind": "level", "location_id": "delve", "display_name": "St. Ulric's.", "level_number": 2},
            "The party enters level 2 of St. Ulric's.",
        ),
        (
            {
                "location_kind": "level",
                "location_id": "delve",
                "display_name": "Why?",
                "level_number": 2,
                "via": "stairs_down",
            },
            "The party descends to level 2 of Why?",
        ),
        (
            {
                "location_kind": "level",
                "location_id": "delve",
                "display_name": "The Delve",
                "level_number": 2,
                "via": "stairs_down",
            },
            "The party descends to level 2 of The Delve.",
        ),
        (
            {"location_kind": "level", "location_id": "delve", "level_number": 1, "via": "stairs_up"},
            "The party climbs to level 1 of delve.",
        ),
        (
            {
                "location_kind": "dungeon",
                "location_id": "delve",
                "display_name": "The Delve",
                "level_number": 1,
                "via": "stairs_down",
            },
            "The party descends into The Delve, level 1.",
        ),
        (
            {"location_kind": "area", "location_id": "room_a", "level_number": 1, "dungeon_id": "delve"},
            "The party enters area room_a (level 1).",
        ),
    ],
    ids=[
        "town",
        "town with no name",
        "dungeon",
        "dungeon with no name",
        "level",
        "level with no name",
        "town with a blank name",
        "dungeon with a blank name",
        "town name ending in !",
        "level name ending in .",
        "stairs to a level name ending in ?",
        "stairs down a level",
        "stairs up with no name",
        "stairs into another dungeon",
        "area",
    ],
)
def test_the_default_line(fields, line):
    assert format_message(LocationEnteredEvent(**fields)) == line


def test_an_event_recorded_before_the_field_parses_with_no_name():
    document = LocationEnteredEvent(location_kind="town", location_id="town", display_name="Threshold").model_dump(
        mode="json"
    )
    del document["display_name"]

    event = parse_any_event(json.loads(json.dumps(document)))

    assert isinstance(event, LocationEnteredEvent)
    assert event.display_name == ""
    assert format_message(event) == "The party enters town."


# ---------------------------------------------------------------------- what the session records


def test_entering_the_dungeon_and_returning_to_town_record_the_authored_names():
    game = session()

    entered = arrivals(game.execute(EnterDungeon(dungeon_id="delve")))
    returned = arrivals(game.execute(TravelToTown()))

    assert [(event.location_kind, event.display_name) for event in entered] == [("dungeon", "The Delve")]
    assert [(event.location_kind, event.display_name) for event in returned] == [("town", "Threshold")]


def test_a_dungeon_entry_names_only_a_dungeon_the_player_view_names():
    # The view lists a dungeon's name once the party has entered it, so the event tells the players nothing more.
    game = session()
    assert game.view(Visibility.PLAYER).dungeons == ()

    (event,) = arrivals(game.execute(EnterDungeon(dungeon_id="delve")))

    names = {dungeon.id: dungeon.name for dungeon in game.view(Visibility.PLAYER).dungeons}
    assert event.visibility is Visibility.PLAYER
    assert names[event.location_id] == event.display_name
    assert game.view(Visibility.PLAYER).town_name == "Threshold"


def test_a_level_entry_records_the_dungeons_name():
    game = session()
    game.execute(EnterDungeon(dungeon_id="delve"))
    game.execute(
        PlaceParty(
            location=PartyLocation(
                kind="dungeon", dungeon_id="delve", level_number=1, position=(4, 1), facing=Direction.EAST
            )
        )
    )

    result = game.execute(UseStairs())

    levels = [event for event in arrivals(result) if event.location_kind == "level"]
    assert [(event.level_number, event.display_name) for event in levels] == [(2, "The Delve")]
    assert format_message(levels[0]) == "The party descends to level 2 of The Delve."


def test_a_referee_placement_records_the_authored_names():
    game = session()

    placed = arrivals(
        game.execute(
            PlaceParty(
                location=PartyLocation(
                    kind="dungeon", dungeon_id="delve", level_number=1, position=(0, 0), facing=Direction.EAST
                )
            )
        )
    )
    home = arrivals(game.execute(PlaceParty(location=PartyLocation(kind="town"))))

    assert [event.display_name for event in placed] == ["The Delve"]
    assert [event.display_name for event in home] == ["Threshold"]


def test_the_event_carries_the_name_without_the_spaces_around_it():
    adventure = build_adventure(wandering_chance=0)
    padded = adventure.model_copy(
        update={
            "town": adventure.town.model_copy(update={"name": "  Threshold  "}),
            "dungeons": (adventure.dungeons[0].model_copy(update={"name": " The Delve "}),),
        }
    )
    game = GameSession.new(build_party(), padded, seed=7)

    (entered,) = arrivals(game.execute(EnterDungeon(dungeon_id="delve")))
    (returned,) = arrivals(game.execute(TravelToTown()))

    assert (entered.display_name, returned.display_name) == ("The Delve", "Threshold")
