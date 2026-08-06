"""The phase 12 golden: the fume vault, where the session ends.

Regenerate with `uv run python tests/generate_phase12_goldens.py` (and explain why
in the commit message). The golden records a delve that lasts two commands — enter,
step into the gas, die — plus the two referee commands a terminal session still
executes among the corpses. It is the first golden whose session *ends*, so it is
where the load-equals-replay guarantee is checked over a finished game.
"""

import json
from pathlib import Path

import pytest

from generate_phase12_goldens import SCRIPT, build_golden, replay_scenario, run_scenario, snapshot
from osrlib.core.events import Event
from osrlib.crawl.commands import parse_command
from osrlib.messages import format_message
from osrlib.persistence import load_game, save_game, session_state

GOLDEN_PATH = Path(__file__).parent / "goldens" / "phase12_wipe.json"

REGENERATE_HINT = (
    "golden mismatch; if the change is intentional, regenerate with "
    "`uv run python tests/generate_phase12_goldens.py` and explain why in the commit message"
)


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


@pytest.fixture(scope="module")
def golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def scripted(golden):
    """The scripted run, played command by command."""
    return run_scenario(golden["master_seed"])


@pytest.fixture(scope="module")
def replayed(golden):
    """The determinism contract: the accepted-command log alone."""
    return replay_scenario(golden["master_seed"], golden["command_log"])


class TestScriptedRun:
    def test_the_whole_golden_matches_byte_for_byte(self, golden):
        assert canonical(build_golden(golden["master_seed"])) == canonical(golden), REGENERATE_HINT

    def test_the_command_log_round_trips(self, golden):
        for entry in golden["command_log"]:
            assert parse_command(entry) is not None

    def test_every_scripted_command_was_accepted_and_logged(self, golden, scripted):
        assert len(golden["command_log"]) == len(SCRIPT) == len(scripted.command_log)


class TestMilestoneBeats:
    def test_the_spring_die_fires_on_entry(self, golden):
        spring = next(
            event
            for event in golden["event_log"]
            if event.get("code") == "exploration.detection.rolled" and event.get("kind") == "trap_spring"
        )
        assert spring["passed"] is True, "the seed is chosen so the wipe is scripted, not lucky"
        assert golden["final_state"]["sprung_traps"] == ["vault:1:gas_room"]

    def test_the_gas_takes_the_whole_party_and_ends_the_session(self, golden):
        codes = [event.get("code") for event in golden["event_log"]]
        assert codes.count("combat.death.died") == 4, "no save, and the gas fills the room"
        assert golden["final_state"]["living_members"] == []
        assert golden["final_state"]["mode"] == "game_over"
        ending = codes.index("session.game_over")
        assert ending == max(index for index, code in enumerate(codes) if code == "combat.death.died") + 2, (
            "the ending closes the command that caused it, straight after the last death's report"
        )
        assert sum(1 for code in codes if code == "session.game_over") == 1
        assert any(line == "The game is over: the party has fallen." for line in golden["transcript"])

    def test_the_referee_still_plays_after_the_ending(self, golden):
        codes = [event.get("code") for event in golden["event_log"]]
        ending = codes.index("session.game_over")
        assert codes[ending + 1 :] == ["adjudication.dice_rolled", "session.flag.set"], (
            "a terminal session still executes and logs referee commands"
        )
        assert [entry["command_type"] for entry in golden["command_log"][-2:]] == ["roll_dice", "set_flag"]
        assert golden["final_state"]["flags"] == {"fume_vault_epitaph": "four went in"}

    def test_the_deaths_are_recorded_for_the_temple(self, golden):
        records = golden["final_state"]["death_records"]
        assert sorted(records) == [f"character-000{index}" for index in range(1, 5)]
        assert {record["round"] for record in records.values()} == {golden["final_state"]["clock_rounds"]}


class TestReplayAndLoadAgree:
    def test_the_replay_reaches_the_same_state_as_the_scripted_run(self, scripted, replayed):
        assert session_state(replayed) == session_state(scripted)

    def test_streams_clock_and_mode_match_the_golden(self, golden, replayed):
        assert canonical(snapshot(replayed)) == canonical(golden["final_state"]), REGENERATE_HINT

    def test_the_event_stream_and_transcript_match(self, golden, replayed):
        events = [entry if isinstance(entry, dict) else entry.model_dump(mode="json") for entry in replayed.event_log]
        assert canonical(events) == canonical(golden["event_log"]), REGENERATE_HINT
        transcript = [format_message(entry) for entry in replayed.event_log if isinstance(entry, Event)]
        assert transcript == golden["transcript"], REGENERATE_HINT

    def test_a_save_of_the_ended_session_loads_back_to_the_replayed_state(self, scripted, replayed):
        restored = load_game(json.loads(json.dumps(save_game(scripted))))
        assert restored.mode is scripted.mode
        assert session_state(restored) == session_state(replayed), (
            "load(save) equals replay(seed, commands), over a session that ended"
        )
