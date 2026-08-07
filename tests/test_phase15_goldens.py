"""The phase 15 golden: the barrow errand, played by the interpreter and replayed without it.

Regenerate with `uv run python tests/generate_phase15_goldens.py` (and explain why in
the commit message). The golden records a scripted delve whose quest is authored data
and whose only actor besides the player is the registered interpreter: a threshold that
fires a trigger and then activates the quest, an idol whose acquisition completes the
first objective, a crypt that reveals the hidden second one, a rite flag that finishes
it and ends the adventure in `victory`, rewards paying after that transition with the
illegal one dropped and noted, a play command refused over the closed adventure, and a
referee's grant still accepted.

It is where the milestone is checked: the quest is authored data, and the same log
replayed with no listeners at all reaches the same world — command and event logs
byte-equal, every state block equal but the interpreter's provably empty listener slot.
"""

import json
from pathlib import Path

import pytest

from generate_phase15_goldens import (
    MOUTH,
    QUEST,
    SCRIPT,
    build_golden,
    replay_scenario,
    run_scenario,
    snapshot,
)
from osrlib.core.events import Event
from osrlib.crawl.commands import parse_command
from osrlib.crawl.interpreter import Interpreter
from osrlib.messages import format_message
from osrlib.persistence import load_game, save_game, session_state

GOLDEN_PATH = Path(__file__).parent / "goldens" / "phase15_quest.json"

REGENERATE_HINT = (
    "golden mismatch; if the change is intentional, regenerate with "
    "`uv run python tests/generate_phase15_goldens.py` and explain why in the commit message"
)

INTERPRETER_ONLY = {Interpreter.key: {}}
"""What a live session's listener store holds: one slot, created by registration, empty
for the life of the session."""


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def without_listener_state(session) -> tuple[dict, dict]:
    """The session's state split into the listener store and everything else."""
    state = session_state(session)
    return state.pop("listener_state"), state


def codes(golden: dict) -> list[str]:
    return [event.get("code") for event in golden["event_log"]]


@pytest.fixture(scope="module")
def golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def scripted(golden):
    """The scripted run, with the interpreter registered."""
    return run_scenario(golden["master_seed"])


@pytest.fixture(scope="module")
def replayed(golden):
    """The determinism contract: the accepted-command log alone, no listeners at all."""
    return replay_scenario(golden["master_seed"], golden["command_log"])


class TestScriptedRun:
    def test_the_whole_golden_matches_byte_for_byte(self, golden):
        assert canonical(build_golden(golden["master_seed"])) == canonical(golden), REGENERATE_HINT

    def test_the_command_log_round_trips(self, golden):
        for entry in golden["command_log"]:
            assert parse_command(entry) is not None

    def test_every_step_is_either_logged_or_refused_never_both(self, golden, scripted):
        session, refusals = scripted
        player_commands = [command for command in session.command_log if command.source is None]
        assert len(SCRIPT) == len(player_commands) + len(refusals)
        assert len(session.command_log) == len(golden["command_log"])


class TestMilestoneBeats:
    def test_the_threshold_fires_the_trigger_and_then_activates_the_quest(self, golden):
        stamped = [entry["source"] for entry in golden["command_log"] if entry["source"] is not None]
        assert stamped[:3] == [MOUTH, MOUTH, QUEST], "triggers first, then quests, on the one crossing"
        activated = next(event for event in golden["event_log"] if event.get("code") == "session.quest.activated")
        assert (activated["quest_id"], activated["name"]) == ("the-idol", "The Votive Idol")
        assert activated["narrative"].startswith("Sister Halda wants the ninth idol")
        assert activated["visibility"] == "player"

    def test_the_bundled_idol_completes_the_visible_objective_on_its_catalog_id(self, golden):
        acquired = next(event for event in golden["event_log"] if event.get("code") == "exploration.item.acquired")
        assert acquired["item_ids"] == ["votive_idol"]
        completed = next(
            event for event in golden["event_log"] if event.get("code") == "session.quest.objective_completed"
        )
        assert completed["objective_id"] == "recover-idol"
        assert completed["narrative"].startswith("The idol comes out of its niche")

    def test_the_crypt_reveals_the_hidden_objective_and_the_rite_completes_it(self, golden):
        revealed = next(
            event for event in golden["event_log"] if event.get("code") == "session.quest.objective_revealed"
        )
        assert revealed["objective_id"] == "speak-the-rite"
        assert revealed["narrative"].startswith("The slab wants words said over it")
        # Written by the game, matched by the objective's own clause.
        assert "session.flag.set" in codes(golden)
        finished = [
            event["objective_id"]
            for event in golden["event_log"]
            if event.get("code") == "session.quest.objective_completed"
        ]
        assert finished == ["recover-idol", "speak-the-rite"]

    def test_the_completion_ends_the_adventure_in_victory(self, golden):
        emitted = codes(golden)
        completed = emitted.index("session.quest.completed")
        ended = emitted.index("session.adventure.completed")
        assert ended == completed + 1, "the quest finishes, and the adventure closes behind it"
        completion, ending = golden["event_log"][completed], golden["event_log"][ended]
        assert completion["narrative"] == ending["narrative"] == "The barrow is quiet. The idol is yours to carry home."
        assert (completion["visibility"], ending["visibility"]) == ("player", "player")
        assert golden["final_state"]["mode"] == "victory"
        assert emitted.count("session.adventure.completed") == 1, "victory is entered once and stays entered"

    def test_the_rewards_land_after_the_transition_and_the_illegal_one_drops(self, golden):
        issued = [entry["command_type"] for entry in golden["command_log"] if entry["source"] == QUEST]
        assert issued == [
            "activate_quest",
            "complete_objective",
            "reveal_objective",
            "complete_objective",
            "complete_quest",
            "record_note",
            "grant_coins",
            "award_xp",
            "award_xp",
            "award_xp",
            "award_xp",
            "set_flag",
        ]
        assert not any(entry["command_type"] == "spawn_monsters" for entry in golden["command_log"])
        note = next(event for event in golden["event_log"] if event.get("code") == "session.note.recorded")
        assert note["text"] == "quest the-idol: reward 0 (spawn_monsters) dropped (session.command.wrong_mode)"
        assert note["visibility"] == "referee"
        assert golden["final_state"]["flags"]["quest.idol"] == "recovered", "the rewards after the drop still landed"

    def test_play_is_over_and_the_referee_is_not(self, golden):
        refusal = golden["refusals"][0]
        assert refusal["code"] == "session.command.wrong_mode"
        assert refusal["params"] == {"command": "move_party", "mode": "victory"}
        assert refusal["after_commands"] == len(golden["command_log"]) - 1, "the grant is the only step after it"
        assert golden["command_log"][-1]["command_type"] == "grant_item"
        assert golden["command_log"][-1]["source"] is None

    def test_the_final_quest_block_and_journal_are_exact(self, golden):
        assert golden["final_state"]["quests"] == {
            "the-idol": {
                "status": "completed",
                "objectives": {
                    "recover-idol": {"revealed": True, "complete": True},
                    "speak-the-rite": {"revealed": True, "complete": True},
                },
            }
        }
        assert golden["final_state"]["journal"] == [
            {"text": "The barrow takes you in, and the daylight stops at the lintel.", "rounds": 60},
            {"text": "Sister Halda wants the ninth idol back in the temple, rite and all.", "rounds": 60},
            {"text": "The idol comes out of its niche as if it were waiting.", "rounds": 120},
            {"text": "The slab wants words said over it before the idol may leave.", "rounds": 120},
            {"text": "The rite is spoken, and the cold goes out of the air.", "rounds": 120},
            {"text": "The barrow is quiet. The idol is yours to carry home.", "rounds": 120},
        ]

    def test_a_quest_beat_reports_itself_once_and_never_twice(self, golden):
        # The lifecycle event is the beat's event: the journal grows without a
        # `session.journal.entry_added` behind it, and the trigger's beat is the one
        # entry that has one.
        journal_events = [event for event in golden["event_log"] if event.get("code") == "session.journal.entry_added"]
        assert [event["text"] for event in journal_events] == [
            "The barrow takes you in, and the daylight stops at the lintel."
        ]
        assert len(golden["final_state"]["journal"]) == 6

    def test_every_machine_issued_command_carries_its_stamp(self, golden):
        stamped = [entry for entry in golden["command_log"] if entry["source"] is not None]
        assert {entry["source"] for entry in stamped} == {MOUTH, QUEST}
        quest_issued = [entry for entry in stamped if entry["source"] == QUEST]
        assert len(quest_issued) == 12
        assert all(entry["source"] == "quest:the-idol" for entry in quest_issued)


class TestReplayIsTheMilestone:
    def test_the_replay_reaches_the_same_state_but_the_empty_listener_slot(self, scripted, replayed):
        session, _ = scripted
        live_slot, live = without_listener_state(session)
        replay_slot, again = without_listener_state(replayed)
        assert live_slot == INTERPRETER_ONLY, "registration creates the slot; the interpreter never fills it"
        assert replay_slot == {}, "a replay runs with no listeners at all"
        assert live == again

    def test_the_command_and_event_logs_are_byte_equal(self, golden, replayed):
        commands = [command.model_dump(mode="json") for command in replayed.command_log]
        assert canonical(commands) == canonical(golden["command_log"]), REGENERATE_HINT
        events = [entry if isinstance(entry, dict) else entry.model_dump(mode="json") for entry in replayed.event_log]
        assert canonical(events) == canonical(golden["event_log"]), REGENERATE_HINT
        transcript = [format_message(entry) for entry in replayed.event_log if isinstance(entry, Event)]
        assert transcript == golden["transcript"], REGENERATE_HINT

    def test_streams_clock_mode_and_the_quest_blocks_match_the_golden(self, golden, replayed):
        assert canonical(snapshot(replayed)) == canonical(golden["final_state"]), REGENERATE_HINT

    def test_a_save_loads_back_to_the_replayed_state(self, scripted, replayed):
        session, _ = scripted
        restored = load_game(json.loads(json.dumps(save_game(session))))
        assert restored.quests == session.quests == replayed.quests
        assert restored.journal == session.journal == replayed.journal
        assert restored.mode is session.mode is replayed.mode
        restored_slot, restored_state = without_listener_state(restored)
        replay_slot, replay_state = without_listener_state(replayed)
        assert restored_slot == INTERPRETER_ONLY, "the empty slot round-trips like any listener state"
        assert replay_slot == {}
        assert restored_state == replay_state, "load(save) equals replay(seed, commands)"

    def test_the_player_view_carries_the_beats_and_none_of_the_wiring(self, scripted):
        from osrlib.core.events import Visibility

        session, _ = scripted
        view = session.view(Visibility.PLAYER)
        payload = json.dumps(view.model_dump(mode="json"))
        for forbidden in ("pattern_type", "reveal_when", "activation", "rewards", "barrow-mouth", "fired_triggers"):
            assert forbidden not in payload, forbidden
        assert view.quests == (), "a finished quest leaves the list; its record is the journal"
        assert "The barrow is quiet. The idol is yours to carry home." in payload
