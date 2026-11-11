"""Loading and saving leave the caller's data alone, and refuse what they can't read (issues #162 and #166).

A load never changes the document it's given, the state a save is built from shares nothing with the
session, the migration table can't be changed, and a document or a data file osrlib can't read raises
`ContentValidationError`. A save from a newer osrlib that adds a message code to an event type this
release knows still loads.
"""

import copy
import pathlib
from unittest import mock

import pytest

import osrlib.data
from crawl_fixtures import build_adventure, build_party
from osrlib.crawl.commands import EnterDungeon, MoveParty
from osrlib.crawl.content_pack import ContentPack
from osrlib.crawl.dungeon import Direction
from osrlib.crawl.events import parse_any_event
from osrlib.crawl.session import GameSession
from osrlib.errors import ContentValidationError
from osrlib.persistence import MIGRATIONS, load_game, save_game, session_state
from osrlib.versioning import check_document


def session() -> GameSession:
    game = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=7)
    dungeon_id = game.adventure.dungeons[0].id
    assert game.execute(EnterDungeon(dungeon_id=dungeon_id)).accepted
    return game


FUTURE_EVENT = {
    "event_type": "future_thing",
    "code": "future.thing.happened",
    "visibility": "player",
    "detail": {"n": 1},
}
"""An event of a type this release doesn't know, which a load keeps as a plain dict."""


# ---------------------------------------------------------------------- session_state (#162, item 11)


def test_session_state_shares_nothing_with_the_session():
    document = save_game(session())
    document["payload"]["event_log"].append(copy.deepcopy(FUTURE_EVENT))
    game = load_game(document)
    game.listener_state["counter"] = {"seen": [1, 2]}

    state = session_state(game)
    state["event_log"][-1]["detail"]["n"] = 999
    state["listener_state"]["counter"]["seen"].append(3)
    state["flags"]["planted"] = 1
    state["command_log"].clear()

    assert game.event_log[-1]["detail"] == {"n": 1}
    assert game.listener_state["counter"] == {"seen": [1, 2]}
    assert "planted" not in game.flags
    assert len(game.command_log) == 1


def test_two_states_of_one_session_are_equal_and_separate():
    game = session()
    first, second = session_state(game), session_state(game)
    assert first == second
    first["party"].clear()
    assert second["party"]


# ------------------------------------------------------------ a load leaves its argument alone (#162, item 12)


def old_save() -> dict:
    """A schema 3 save whose last command is a declaration the 3-to-4 migration rewrites."""
    document = copy.deepcopy(save_game(session()))
    document["schema_version"] = 3
    document["payload"]["command_log"].append(
        {
            "command_type": "resolve_battle_round",
            "declarations": [{"character_id": "x", "action": "move", "move": "withdraw"}],
        }
    )
    return document


def test_load_game_leaves_an_old_save_as_it_was():
    document = old_save()
    snapshot = copy.deepcopy(document)
    try:
        load_game(document)
    except ContentValidationError:
        pass  # The appended declaration names no real member. Only the document matters here.
    assert document == snapshot


def test_load_game_leaves_a_current_save_as_it_was():
    document = save_game(session())
    snapshot = copy.deepcopy(document)
    load_game(document)
    assert document == snapshot


def test_content_pack_from_document_leaves_an_old_pack_as_it_was():
    trap = {"kind": "treasure", "trigger": "enter", "effect": {"damage_dice": "1d6"}}
    chest = {"id": "chest", "kind": "treasure_cache", "trap": trap}
    document = {
        "kind": "content_pack",
        "schema_version": 2,
        "engine_version": "1.0.0",
        "payload": {"sections": [{"id": "level-1", "entries": [{"id": "vault", "features": [chest]}]}]},
    }
    snapshot = copy.deepcopy(document)
    pack = ContentPack.from_document(document)
    assert document == snapshot
    assert pack.sections[0].entries[0].features[0].trap.trigger == "open", "the pack itself is still upgraded"


# ---------------------------------------------------------------------- the migration table (#162, item 13)


def test_the_migration_table_cant_be_changed():
    original = MIGRATIONS[3]
    with pytest.raises(TypeError):
        MIGRATIONS[3] = lambda payload: payload  # type: ignore[index]
    with pytest.raises(TypeError):
        del MIGRATIONS[3]  # type: ignore[attr-defined]
    with pytest.raises((TypeError, AttributeError)):
        MIGRATIONS.clear()  # type: ignore[attr-defined]
    assert MIGRATIONS[3] is original
    assert sorted(MIGRATIONS) == [1, 2, 3]


# ---------------------------------------------------------------------- schema_version below 1 (#166, item 27)


@pytest.mark.parametrize("version", [0, -5])
def test_check_document_refuses_a_schema_version_below_one(version):
    document = save_game(session())
    document["schema_version"] = version
    with pytest.raises(ContentValidationError, match="schema"):
        check_document(document, "save")
    with pytest.raises(ContentValidationError, match="schema"):
        load_game(document)


# ------------------------------------------------------------ a code this release doesn't know (#166, item 37)

NEW_CODE = "exploration.party.teleported"
"""A message code a later release adds to `party_moved` within the same schema version."""


def test_parse_any_event_treats_an_unknown_code_like_an_unknown_type():
    record = {"event_type": "party_moved", "code": NEW_CODE, "visibility": "player", "x": 1, "y": 0, "facing": "east"}
    assert parse_any_event(record) is None
    assert parse_any_event({"event_type": "teleported", "code": NEW_CODE, "visibility": "player"}) is None


def test_parse_any_event_still_refuses_a_malformed_event_of_a_known_type():
    with pytest.raises(ContentValidationError):
        parse_any_event({"event_type": "party_moved", "code": "exploration.party.moved", "visibility": "player"})


def test_a_save_with_an_unknown_code_loads_and_keeps_the_record():
    game = session()
    moved = next(direction for direction in Direction if game.execute(MoveParty(direction=direction)).accepted)
    assert moved is not None
    document = save_game(game)
    changed = [entry for entry in document["payload"]["event_log"] if entry["event_type"] == "party_moved"]
    assert changed
    for entry in changed:
        entry["code"] = NEW_CODE

    loaded = load_game(document)
    kept = [entry for entry in loaded.event_log if isinstance(entry, dict)]
    assert kept == changed
    # The record survives the next save too.
    assert [entry for entry in save_game(loaded)["payload"]["event_log"] if entry.get("code") == NEW_CODE] == changed


# ---------------------------------------------------------------------- a data file osrlib can't read (#166, item 38)


def test_a_truncated_data_file_is_a_content_validation_error(tmp_path: pathlib.Path):
    (tmp_path / "classes.json").write_text('{"classes": [', encoding="utf-8")
    with mock.patch.object(osrlib.data.resources, "files", return_value=tmp_path):
        with pytest.raises(ContentValidationError, match="classes.json"):
            osrlib.data.load_classes.__wrapped__()
