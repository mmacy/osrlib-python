"""Persistence tests: save/load equality, load-equals-replay, migrations, versions."""

import json

import pytest

from crawl_fixtures import build_adventure, build_party
from osrlib.core.character import party_to_document
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.commands import (
    AwardXP,
    EnterDungeon,
    ForceDoor,
    GrantItem,
    LightSource,
    MoveParty,
    SetFlag,
)
from osrlib.crawl.dungeon import Direction
from osrlib.crawl.session import GameSession
from osrlib.errors import ContentValidationError, ReplayVersionError, SaveVersionError
from osrlib.persistence import _migrate, load_game, replay_game, save_game, session_state
from osrlib.versioning import SCHEMA_VERSION, engine_version

SEED = 424_242


def drive_session() -> tuple[GameSession, list]:
    session = GameSession.new(build_party(), build_adventure(), seed=SEED)
    commands = [
        GrantItem(character_id="character-0001", item_id="torch", quantity=6),
        GrantItem(character_id="character-0001", item_id="tinder_box"),
        SetFlag(key="lever", value=True),
        AwardXP(character_id="character-0002", amount=333),
        EnterDungeon(dungeon_id="delve"),
        LightSource(character_id="character-0001", item_id="torch"),
        MoveParty(direction=Direction.EAST),
        MoveParty(direction=Direction.EAST),
        ForceDoor(direction=Direction.SOUTH, character_id="character-0001"),
    ]
    accepted = []
    for command in commands:
        if session.execute(command).accepted:
            accepted.append(command)
    return session, accepted


class TestSaveLoad:
    def test_save_load_state_equality_rng_streams_included(self):
        session, _ = drive_session()
        document = json.loads(json.dumps(save_game(session)))
        restored = load_game(document)
        assert session_state(restored) == session_state(session)

    def test_saves_are_self_contained(self):
        session, _ = drive_session()
        document = save_game(session)
        assert document["kind"] == "save"
        assert document["schema_version"] == SCHEMA_VERSION
        assert document["engine_version"] == engine_version()
        assert document["payload"]["adventure"]["name"] == "The Test Delve"
        assert document["payload"]["master_seed"] == SEED
        assert document["payload"]["command_log"]  # always present

    def test_event_log_optional_saves_restore_correctly(self):
        session, _ = drive_session()
        compact = save_game(session, include_event_log=False)
        assert "event_log" not in compact["payload"]
        restored = load_game(compact)
        assert restored.event_log == []
        # State (sans logs) still matches.
        full = session_state(session, include_event_log=False)
        again = session_state(restored, include_event_log=False)
        assert full == again

    def test_loaded_session_continues_identically(self):
        session, _ = drive_session()
        restored = load_game(json.loads(json.dumps(save_game(session))))
        next_command = ForceDoor(direction=Direction.SOUTH, character_id="character-0001")
        first = session.execute(next_command)
        second = restored.execute(next_command)
        assert first.accepted == second.accepted
        assert [event.code for event in first.events] == [event.code for event in second.events]
        assert session_state(restored) == session_state(session)

    def test_newer_schema_fails_fast(self):
        session, _ = drive_session()
        document = save_game(session)
        document["schema_version"] = SCHEMA_VERSION + 1
        with pytest.raises(SaveVersionError):
            load_game(document)


class TestReplay:
    def test_load_equals_replay(self):
        session, accepted = drive_session()
        party_document = party_to_document(build_party().members)
        replayed = replay_game(SEED, party_document, build_adventure(), Ruleset(), accepted)
        assert session_state(replayed) == session_state(session)

    def test_replay_accepts_serialized_commands(self):
        session, accepted = drive_session()
        party_document = party_to_document(build_party().members)
        serialized = [command.model_dump(mode="json") for command in accepted]
        replayed = replay_game(SEED, party_document, build_adventure(), Ruleset(), serialized)
        assert session_state(replayed) == session_state(session)

    def test_replay_under_a_different_engine_version_raises(self):
        party_document = party_to_document(build_party().members)
        with pytest.raises(ReplayVersionError):
            replay_game(
                SEED,
                party_document,
                build_adventure(),
                Ruleset(),
                [],
                recorded_engine_version="0.0.0-other",
            )

    def test_replay_under_the_same_engine_version_is_legal(self):
        party_document = party_to_document(build_party().members)
        replay_game(SEED, party_document, build_adventure(), Ruleset(), [], recorded_engine_version=engine_version())

    def test_unknown_command_type_in_the_log_raises(self):
        party_document = party_to_document(build_party().members)
        with pytest.raises(ContentValidationError):
            replay_game(SEED, party_document, build_adventure(), Ruleset(), [{"command_type": "cast_wish"}])


class TestMigrations:
    def test_version_1_save_migrates_to_2(self):
        # The framework's first honest exercise (the synthetic-only test retired
        # with it): a real version-1 save document — the version-1 envelope and
        # the recovered-treasure ledger the version carried — loads through the
        # shipped 1 → 2 migration, which drops the ledger.
        session, _ = drive_session()
        document = save_game(session)
        document["schema_version"] = 1
        document["payload"]["recovered_treasure"] = [{"source_ref": "delve:1:chest", "gp_value": 200}]
        restored = load_game(document)
        assert not hasattr(restored, "recovered_treasure")
        assert save_game(restored)["schema_version"] == 2

    def test_missing_migration_step_raises(self):
        with pytest.raises(ContentValidationError):
            _migrate({"seed": 1}, 0, migrations={})

    def test_current_version_needs_no_migration(self):
        assert _migrate({"seed": 1}, SCHEMA_VERSION, migrations={}) == {"seed": 1}


class TestUnknownEventPreservation:
    def test_unknown_event_types_round_trip_losslessly(self):
        session, _ = drive_session()
        document = save_game(session)
        alien = {"event_type": "from_the_future", "code": "future.thing", "visibility": "player", "payload": 7}
        document["payload"]["event_log"].append(alien)
        restored = load_game(document)
        assert restored.event_log[-1] == alien
        # And it reserializes losslessly.
        again = save_game(restored)
        assert again["payload"]["event_log"][-1] == alien
