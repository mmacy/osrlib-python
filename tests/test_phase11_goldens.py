"""The phase 11 golden: the gated warren, refusal probes and all.

Regenerate with `uv run python tests/generate_phase11_goldens.py` (and explain why
in the commit message). The golden records the scripted delve — accepted commands,
events, transcript, final streams, clock, doors, and packs — plus every refusal the
script probed with. Rejections join neither log, so the replay runs the accepted
commands alone; the two runs landing on identical state is the proof that a gate
refusal consumes no draws, no time, and no items.
"""

import json
from pathlib import Path

import pytest

from generate_phase11_goldens import SCRIPT, build_golden, replay_scenario, run_scenario, snapshot
from osrlib.core.events import Event
from osrlib.crawl.commands import parse_command
from osrlib.messages import format_message
from osrlib.persistence import load_game, save_game, session_state

GOLDEN_PATH = Path(__file__).parent / "goldens" / "phase11_gates.json"

REGENERATE_HINT = (
    "golden mismatch; if the change is intentional, regenerate with "
    "`uv run python tests/generate_phase11_goldens.py` and explain why in the commit message"
)


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


@pytest.fixture(scope="module")
def golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def scripted(golden):
    """The scripted run: accepted commands with refusal probes between them."""
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

    def test_save_and_load_round_trip_the_finished_run(self, scripted):
        session, _ = scripted
        restored = load_game(json.loads(json.dumps(save_game(session))))
        assert session_state(restored) == session_state(session)


class TestMilestoneBeats:
    def test_the_key_door_refuses_with_its_authored_text_then_opens_with_its_beat(self, golden):
        refusal = golden["refusals"][0]
        assert refusal["code"] == "exploration.door.gate_refused"
        assert refusal["params"]["refusal"].startswith("The bronze sentinel")
        opened = next(
            event
            for event in golden["event_log"]
            if event.get("code") == "exploration.door.opened" and event.get("narrative")
        )
        assert opened["narrative"].startswith("The brass key turns")
        assert any(
            line.endswith("The brass key turns in the sentinel's palm and the door swings wide.")
            for line in golden["transcript"]
        )

    def test_the_toll_is_taken_before_the_arrival_and_only_on_success(self, golden):
        codes = [event.get("code") for event in golden["event_log"]]
        consumed = codes.index("exploration.item.consumed")
        crossing = next(
            index
            for index, event in enumerate(golden["event_log"])
            if event.get("code") == "exploration.location.entered" and event.get("location_kind") == "level"
        )
        assert consumed < crossing, "the toll is paid at the threshold, before the party arrives"
        assert codes.count("exploration.item.consumed") == 1, "one token, one crossing"
        spent = next(entry for entry in golden["refusals"] if entry["code"] == "exploration.transition.gate_refused")
        assert spent["params"]["refusal"].startswith("The ferryman's hand")
        assert golden["final_state"]["inventories"]["character-0001"].get("toll_token") is None

    def test_the_lock_answers_before_the_gate_and_both_are_required(self, golden):
        ladder = [entry["code"] for entry in golden["refusals"][1:3]]
        assert ladder == ["exploration.door.locked", "exploration.door.gate_refused"]
        assert golden["refusals"][2]["params"]["refusal"].startswith("The seal-plate is empty")
        # The reliquary door ends the run open, unlocked, and opened by the party.
        reliquary = golden["final_state"]["doors"]["warren:1:2,1:north"]
        assert reliquary == {
            "open": True,
            "unlocked": True,
            "wedged": False,
            "discovered": False,
            "opened_by_party": True,
        }

    def test_a_referee_opened_door_admits_passage_until_it_closes(self, golden):
        refused = [
            entry for entry in golden["refusals"] if entry["params"].get("refusal", "").startswith("The sanctum")
        ]
        assert len(refused) == 2, "refused before the referee opened it, and again once it shut"
        assert refused[0]["after_commands"] < refused[1]["after_commands"]
        blocked = next(entry for entry in golden["refusals"] if entry["code"] == "exploration.move.blocked")
        assert refused[0]["after_commands"] <= blocked["after_commands"] <= refused[1]["after_commands"]
        # The passage between the two refusals happened: the party stood beyond it.
        moves = [event for event in golden["event_log"] if event.get("code") == "exploration.party.moved"]
        assert any(event["x"] == 4 for event in moves)

    def test_the_referee_door_write_stayed_referee_visible_and_beatless(self, golden):
        referee_writes = [
            event
            for event in golden["event_log"]
            if event.get("event_type") == "door" and event.get("visibility") == "referee"
        ]
        assert referee_writes, "the referee opened the sanctum door"
        assert all(event["narrative"] is None for event in referee_writes)

    def test_only_operated_doors_are_stored(self, golden):
        assert set(golden["final_state"]["doors"]) == {
            "warren:1:2,0:west",  # the sentinel's door, opened and swung shut
            "warren:1:2,1:north",  # the reliquary, unlocked by the referee and opened
            "warren:1:4,0:west",  # the sanctum, set open by the referee and closed again
        }, "a probed-but-refused door leaves no overlay entry"


class TestReplayIsThePurityProof:
    def test_the_replay_reaches_the_same_state_as_the_probed_run(self, scripted, replayed):
        session, _ = scripted
        assert session_state(replayed) == session_state(session), (
            "the probes cost nothing: the run that made them and the log that omits them agree"
        )

    def test_streams_clock_doors_and_packs_match_the_golden(self, golden, replayed):
        assert canonical(snapshot(replayed)) == canonical(golden["final_state"]), REGENERATE_HINT

    def test_the_event_stream_and_transcript_match(self, golden, replayed):
        events = [entry if isinstance(entry, dict) else entry.model_dump(mode="json") for entry in replayed.event_log]
        assert canonical(events) == canonical(golden["event_log"]), REGENERATE_HINT
        transcript = [format_message(entry) for entry in replayed.event_log if isinstance(entry, Event)]
        assert transcript == golden["transcript"], REGENERATE_HINT

    def test_the_refusals_never_reached_either_log(self, golden, scripted):
        session, refusals = scripted
        assert len(refusals) == len(golden["refusals"]) == 7
        # Every step of the script is either logged or refused, never both.
        assert len(SCRIPT) == len(session.command_log) + len(refusals)
        assert len(session.command_log) == len(golden["command_log"])
        codes = [getattr(event, "code", None) for event in session.event_log]
        assert "exploration.door.gate_refused" not in codes
