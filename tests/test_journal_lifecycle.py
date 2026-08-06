"""The `source` stamp on the command log.

The stamp is an annotation and nothing else: a stamped command executes exactly as
the same command unstamped does, and the only trace it leaves is on the logged
command itself, where a save, a load, and a replay all carry it verbatim.
"""

import json

import pytest
from pydantic import ValidationError

from crawl_fixtures import build_adventure, build_party
from osrlib.crawl.commands import (
    AdvanceTime,
    Command,
    EnterDungeon,
    MoveParty,
    RollDice,
    SetFlag,
    parse_command,
)
from osrlib.crawl.dungeon import Direction
from osrlib.crawl.session import GameSession
from osrlib.persistence import load_game, save_game, session_state

STAMP = "trigger:lever-east"

# One command of each temper: a mode switch, a move that draws and spends time, a
# referee roll on its own stream, a flag write, and a span of clock.
SCRIPT: tuple[Command, ...] = (
    EnterDungeon(dungeon_id="delve"),
    MoveParty(direction=Direction.EAST),
    RollDice(expression="2d6"),
    SetFlag(key="portcullis", value="open"),
    AdvanceTime(n=2, unit="turn"),
)


def make_session(seed: int = 17) -> GameSession:
    return GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=seed)


def run(commands) -> GameSession:
    session = make_session()
    for command in commands:
        result = session.execute(command)
        assert result.accepted, [rejection.code for rejection in result.rejections]
    return session


class TestTheSourceStamp:
    def test_a_stamped_command_survives_save_and_load(self):
        session = run([command.model_copy(update={"source": STAMP}) for command in SCRIPT])
        restored = load_game(json.loads(json.dumps(save_game(session))))
        assert [command.source for command in restored.command_log] == [STAMP] * len(SCRIPT)
        assert session_state(restored) == session_state(session)

    def test_execution_ignores_the_stamp(self):
        stamped = run([command.model_copy(update={"source": STAMP}) for command in SCRIPT])
        plain = run(SCRIPT)
        stamped_state = session_state(stamped)
        plain_state = session_state(plain)
        logged = stamped_state.pop("command_log"), plain_state.pop("command_log")
        assert stamped_state == plain_state, "the stamp annotates the log and changes nothing else"
        assert [entry.pop("source") for entry in logged[0]] == [STAMP] * len(SCRIPT)
        assert [entry.pop("source") for entry in logged[1]] == [None] * len(SCRIPT)
        assert logged[0] == logged[1]

    def test_a_command_logged_without_a_stamp_parses_as_unstamped(self):
        command = parse_command({"command_type": "set_flag", "key": "portcullis", "value": "open"})
        assert command is not None
        assert command.source is None

    def test_the_empty_string_is_not_a_stamp(self):
        with pytest.raises(ValidationError):
            SetFlag(key="portcullis", value="open", source="")
