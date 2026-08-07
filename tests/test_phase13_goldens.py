"""The phase 13 golden: the lever, the journal it wrote, and the stamps on the log.

Regenerate with `uv run python tests/generate_phase13_goldens.py` (and explain why in
the commit message). The golden records a short delve driven the way an authored
trigger and quest layer drives one, with no listeners registered: a trigger marked,
its flag written, its beat journalled, the same trigger marked again a turn later, a
referee note, and a quest-stamped grant. It is where the milestone is checked —
save/load and replay rebuild the journal and the fired-marks exactly, and every
`source` stamp survives the command log through both.
"""

import json
from pathlib import Path

import pytest

from generate_phase13_goldens import LEVER, QUEST, SCRIPT, build_golden, replay_scenario, run_scenario, snapshot
from osrlib.core.events import Event
from osrlib.crawl.commands import parse_command
from osrlib.messages import format_message
from osrlib.persistence import load_game, save_game, session_state

GOLDEN_PATH = Path(__file__).parent / "goldens" / "phase13_journal.json"

REGENERATE_HINT = (
    "golden mismatch; if the change is intentional, regenerate with "
    "`uv run python tests/generate_phase13_goldens.py` and explain why in the commit message"
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
    def test_the_lever_is_marked_once_and_reported_twice(self, golden):
        assert golden["final_state"]["fired_triggers"] == ["lever-east"]
        codes = [event.get("code") for event in golden["event_log"]]
        assert codes.count("session.trigger.fired") == 2, "state records the trigger, the log records each firing"
        assert [entry["command_type"] for entry in golden["command_log"]].count("mark_trigger_fired") == 2

    def test_the_journal_is_two_beats_stamped_a_turn_apart(self, golden):
        journal = golden["final_state"]["journal"]
        assert [entry["text"] for entry in journal] == [
            "The east lever gives; somewhere below, a portcullis grinds upward.",
            "The lever, hauled a second time, only grinds.",
        ]
        assert journal[1]["rounds"] - journal[0]["rounds"] == 60, "the breather between them was one turn"
        assert [line for line in golden["transcript"] if line.startswith("Journal: ")] == [
            f"Journal: {entry['text']}" for entry in journal
        ]

    def test_the_note_and_the_mark_stay_behind_the_screen(self, golden):
        visibility = {
            event["code"]: event["visibility"]
            for event in golden["event_log"]
            if event.get("code", "").startswith("session.")
        }
        assert visibility["session.trigger.fired"] == "referee"
        assert visibility["session.note.recorded"] == "referee"
        assert visibility["session.journal.entry_added"] == "player"

    def test_every_stamp_is_on_the_logged_command(self, golden):
        stamps = [(entry["command_type"], entry["source"]) for entry in golden["command_log"]]
        assert stamps == [
            ("enter_dungeon", None),
            ("move_party", None),
            ("mark_trigger_fired", LEVER),
            ("set_flag", LEVER),
            ("add_journal_entry", LEVER),
            ("rest", None),
            ("mark_trigger_fired", LEVER),
            ("add_journal_entry", LEVER),
            ("record_note", None),
            ("grant_item", QUEST),
        ]


class TestReplayAndLoadAgree:
    def test_the_replay_reaches_the_same_state_as_the_scripted_run(self, scripted, replayed):
        assert session_state(replayed) == session_state(scripted)

    def test_streams_clock_and_both_blocks_match_the_golden(self, golden, replayed):
        assert canonical(snapshot(replayed)) == canonical(golden["final_state"]), REGENERATE_HINT

    def test_the_event_stream_and_transcript_match(self, golden, replayed):
        events = [entry if isinstance(entry, dict) else entry.model_dump(mode="json") for entry in replayed.event_log]
        assert canonical(events) == canonical(golden["event_log"]), REGENERATE_HINT
        transcript = [format_message(entry) for entry in replayed.event_log if isinstance(entry, Event)]
        assert transcript == golden["transcript"], REGENERATE_HINT

    def test_a_save_loads_back_to_the_replayed_state_with_both_blocks_intact(self, scripted, replayed):
        restored = load_game(json.loads(json.dumps(save_game(scripted))))
        assert restored.fired_triggers == scripted.fired_triggers == replayed.fired_triggers
        assert restored.journal == scripted.journal == replayed.journal
        assert session_state(restored) == session_state(replayed), (
            "load(save) equals replay(seed, commands), the journal and the fired-marks included"
        )

    def test_the_reloaded_command_log_carries_every_stamp_verbatim(self, golden, scripted):
        restored = load_game(json.loads(json.dumps(save_game(scripted))))
        assert [command.source for command in restored.command_log] == [
            entry["source"] for entry in golden["command_log"]
        ]
        assert [command.source for command in restored.command_log].count(LEVER) == 5
        assert [command.source for command in restored.command_log].count(QUEST) == 1
