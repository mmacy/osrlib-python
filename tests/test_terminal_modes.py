"""The terminal modes: `game_over` and `victory`.

Three contracts live here. The legality census — which commands a session that has
ended still executes — asserted both as declared data and at the executed seam.
The centralized wipe check, which routes every party wipe to `game_over` whatever
killed the party, and leaves a concluded session alone. And the wiped-party guards
that keep a mid-command procedure from starting something new for corpses.

Nothing in this phase transitions *into* `victory` — the entrance arrives with the
authored quest layer — so these tests assign `session.mode` directly, the same
direct-state posture the battle tests use for hit points.
"""

import json

import pytest

from crawl_fixtures import build_adventure, build_party
from osrlib.crawl.commands import (
    ALL_COMMAND_CLASSES,
    AdvanceTime,
    AwardXP,
    GrantItem,
    PlaceParty,
    RollDice,
    SessionMode,
    SetFlag,
    SpawnMonsters,
    SpawnNpcParty,
)
from osrlib.crawl.session import GameSession
from osrlib.persistence import load_game, save_game, session_state
from test_commands import REFEREE_COMMANDS, RESUME_PLAY_CARVE_OUTS, sample_command

TERMINAL_MODES = frozenset({SessionMode.GAME_OVER, SessionMode.VICTORY})

PLAY_COMMAND_CLASSES = tuple(
    command_class
    for command_class in ALL_COMMAND_CLASSES
    if command_class.model_fields["command_type"].default not in REFEREE_COMMANDS
)


def make_session(seed: int = 11) -> GameSession:
    return GameSession.new(build_party(), build_adventure(), seed=seed)


class TestTheLegalityCensus:
    """Terminal-mode membership follows referee-ness, with three exceptions."""

    def test_terminal_names_exactly_game_over_and_victory(self):
        assert {mode for mode in SessionMode if mode.terminal} == TERMINAL_MODES

    @pytest.mark.parametrize("command_class", PLAY_COMMAND_CLASSES, ids=lambda cls: cls.__name__)
    def test_play_commands_are_illegal_in_both_terminal_modes(self, command_class):
        assert not command_class.allowed_modes & TERMINAL_MODES

    def test_referee_commands_are_legal_in_both_terminal_modes_but_the_carve_outs(self):
        for command_class in ALL_COMMAND_CLASSES:
            command_type = command_class.model_fields["command_type"].default
            if command_type not in REFEREE_COMMANDS:
                continue
            withheld = RESUME_PLAY_CARVE_OUTS.get(command_type, frozenset())
            assert command_class.allowed_modes & TERMINAL_MODES == TERMINAL_MODES - withheld, command_class.__name__

    def test_the_carve_outs_are_the_three_commands_that_resume_play(self):
        # PlaceParty keeps game_over — the salvage door — and loses victory alone;
        # spawning opens an encounter, which no ended session gets.
        assert PlaceParty.allowed_modes & TERMINAL_MODES == frozenset({SessionMode.GAME_OVER})
        assert not SpawnMonsters.allowed_modes & TERMINAL_MODES
        assert not SpawnNpcParty.allowed_modes & TERMINAL_MODES


class TestVictoryAtTheExecutedSeam:
    """The declared census again, this time through `execute`."""

    @pytest.mark.parametrize("command_class", PLAY_COMMAND_CLASSES, ids=lambda cls: cls.__name__)
    def test_every_play_command_rejects_wrong_mode_in_victory(self, command_class):
        session = make_session()
        session.mode = SessionMode.VICTORY
        result = session.execute(sample_command(command_class))
        assert not result.accepted
        assert [rejection.code for rejection in result.rejections] == ["session.command.wrong_mode"]
        assert result.rejections[0].params["mode"] == "victory"
        assert not session.command_log

    def test_the_referee_surface_still_executes_in_victory(self):
        session = make_session()
        session.mode = SessionMode.VICTORY
        accepted = [
            GrantItem(character_id="character-0001", item_id="torch"),
            AwardXP(character_id="character-0001", amount=100),
            SetFlag(key="idol_returned", value=True),
            AdvanceTime(n=1, unit="turn"),
            RollDice(expression="2d6"),
        ]
        for command in accepted:
            assert session.execute(command).accepted, command.command_type
        assert session.mode is SessionMode.VICTORY
        assert len(session.command_log) == len(accepted)

    def test_the_resume_play_commands_reject_in_victory(self):
        session = make_session()
        session.mode = SessionMode.VICTORY
        for command in (
            PlaceParty(location={"kind": "town"}),
            SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=30),
            SpawnNpcParty(party_kind="basic", distance_feet=30),
        ):
            result = session.execute(command)
            assert not result.accepted, command.command_type
            assert result.rejections[0].code == "session.command.wrong_mode"
        assert session.mode is SessionMode.VICTORY


class TestPersistence:
    @pytest.mark.parametrize("mode", sorted(TERMINAL_MODES))
    def test_a_terminal_mode_round_trips_through_a_save(self, mode):
        session = make_session()
        session.mode = mode
        restored = load_game(json.loads(json.dumps(save_game(session))))
        assert restored.mode is mode
        assert session_state(restored) == session_state(session)

    def test_a_loaded_victory_session_still_enforces_its_legality(self):
        session = make_session()
        session.mode = SessionMode.VICTORY
        restored = load_game(json.loads(json.dumps(save_game(session))))
        assert not restored.execute(PlaceParty(location={"kind": "town"})).accepted
        assert restored.execute(SetFlag(key="epilogue", value="told")).accepted
        assert restored.mode is SessionMode.VICTORY
