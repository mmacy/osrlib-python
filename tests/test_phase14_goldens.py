"""The phase 14 golden: the lever keep, played by the interpreter and replayed without it.

Regenerate with `uv run python tests/generate_phase14_goldens.py` (and explain why in
the commit message). The golden records a scripted delve whose only actor besides the
player is the registered interpreter: a gate refusal that costs nothing, a lever flag
whose trigger opens the portcullis and writes the party's journal, a walk through the
raised grille, and an ambush trigger whose spawn arrives to find the guardroom's own
encounter already open and is dropped with a note.

It is where the milestone is checked: the scenario is authored data, and the same log
replayed with no listeners at all reaches the same world — command and event logs
byte-equal, every state block equal but the interpreter's provably empty listener slot.
"""

import json
from pathlib import Path

import pytest

from generate_phase14_goldens import (
    AMBUSH,
    PORTCULLIS,
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

GOLDEN_PATH = Path(__file__).parent / "goldens" / "phase14_triggers.json"

REGENERATE_HINT = (
    "golden mismatch; if the change is intentional, regenerate with "
    "`uv run python tests/generate_phase14_goldens.py` and explain why in the commit message"
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
    def test_the_portcullis_refuses_with_its_authored_text_and_costs_nothing(self, golden):
        refusal = golden["refusals"][0]
        assert refusal["code"] == "exploration.door.gate_refused"
        assert refusal["params"]["refusal"].startswith("The portcullis is a grille")
        assert "exploration.door.gate_refused" not in [event.get("code") for event in golden["event_log"]]

    def test_the_lever_marks_opens_and_journals_in_that_order(self, golden):
        issued = [entry["command_type"] for entry in golden["command_log"] if entry["source"] == PORTCULLIS]
        assert issued == ["mark_trigger_fired", "set_door_state", "add_journal_entry"]
        opened = next(event for event in golden["event_log"] if event.get("code") == "exploration.door.opened")
        assert (opened["x"], opened["y"], opened["direction"]) == (2, 0, "east")
        assert golden["final_state"]["doors"]["keep:1:3,0:west"]["open"] is True

    def test_the_fired_beat_is_the_referees_and_the_journal_is_the_players(self, golden):
        fired = [event for event in golden["event_log"] if event.get("code") == "session.trigger.fired"]
        assert [event["visibility"] for event in fired] == ["referee", "referee"]
        assert fired[0]["narrative"].startswith("Chain rattles in the wall")
        journal = [event for event in golden["event_log"] if event.get("code") == "session.journal.entry_added"]
        assert [event["visibility"] for event in journal] == ["player", "player"]
        assert [entry["text"] for entry in golden["final_state"]["journal"]] == [event["text"] for event in journal]

    def test_the_party_walks_through_the_grille_the_trigger_raised(self, golden):
        moves = [event for event in golden["event_log"] if event.get("code") == "exploration.party.moved"]
        assert [event["x"] for event in moves] == [1, 2, 3, 4], "past the door at (2,0) and on to the guardroom"

    def test_the_colliding_spawn_drops_alone_and_the_note_says_why(self, golden):
        assert not any(entry["command_type"] == "spawn_monsters" for entry in golden["command_log"])
        note = next(event for event in golden["event_log"] if event.get("code") == "session.note.recorded")
        assert note["text"] == (
            "trigger guard-ambush: consequence 0 (spawn_monsters) dropped (session.command.encounter_in_progress)"
        )
        assert note["visibility"] == "referee"
        # The firing itself still happened, and its journal beat still landed.
        assert golden["final_state"]["fired_triggers"] == ["portcullis-rises", "guard-ambush"]
        assert [entry["command_type"] for entry in golden["command_log"] if entry["source"] == AMBUSH] == [
            "mark_trigger_fired",
            "record_note",
            "add_journal_entry",
        ]

    def test_every_interpreter_issued_command_carries_its_stamp(self, golden):
        stamped = [entry for entry in golden["command_log"] if entry["source"] is not None]
        assert len(stamped) == 6
        assert all(entry["source"].startswith("trigger:") for entry in stamped)
        assert {entry["source"] for entry in stamped} == {PORTCULLIS, AMBUSH}


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

    def test_streams_clock_doors_and_the_trigger_blocks_match_the_golden(self, golden, replayed):
        assert canonical(snapshot(replayed)) == canonical(golden["final_state"]), REGENERATE_HINT

    def test_a_save_loads_back_to_the_replayed_state(self, scripted, replayed):
        session, _ = scripted
        restored = load_game(json.loads(json.dumps(save_game(session))))
        assert restored.fired_triggers == session.fired_triggers == replayed.fired_triggers
        assert restored.journal == session.journal == replayed.journal
        restored_slot, restored_state = without_listener_state(restored)
        replay_slot, replay_state = without_listener_state(replayed)
        assert restored_slot == INTERPRETER_ONLY, "the empty slot round-trips like any listener state"
        assert replay_slot == {}
        assert restored_state == replay_state, "load(save) equals replay(seed, commands)"

    def test_the_player_view_carries_the_beats_and_none_of_the_wiring(self, scripted):
        from osrlib.core.events import Visibility

        session, _ = scripted
        payload = json.dumps(session.view(Visibility.PLAYER).model_dump(mode="json"))
        for forbidden in ("pattern_type", "consequences", "fired_triggers", "portcullis-rises", "Chain rattles"):
            assert forbidden not in payload, forbidden
        assert "the portcullis grinds up into its slot" in payload
