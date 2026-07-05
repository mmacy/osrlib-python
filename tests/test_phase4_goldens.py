"""The Phase 4 milestone: the scripted delve, replayable and restorable.

From one master seed: town outfitting and travel, two levels explored (a forced
and spiked door, a listen, a search finding the secret door, torches burning out
and relit), the sprung pit, the keyed goblin battle with a machine-detected
morale rout, the crypt's turn-undead declaration routing skeletons, the kennel
flights (dropped treasure with a successful distraction; the 30-round exhaustion
terminal), a night camp with re-preparation, wandering encounters fought through
the battle machine (an area *fire ball* under the footprint rule and a disrupted
casting found by the machine), and the return to town.

The golden asserts the full event stream and formatted transcript byte-for-byte,
final stream states scoped per RNG stream, and — at each checkpoint —
`load(save) == state` and `load(save)` continuing identically to the straight run.
Regenerate with `uv run python tests/generate_phase4_goldens.py` and explain why
in the commit message.
"""

import json
from pathlib import Path

import pytest

from crawl_fixtures import build_milestone_adventure, build_milestone_party
from osrlib.core.character import party_to_document
from osrlib.core.events import Event
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.commands import parse_command
from osrlib.messages import format_message
from osrlib.persistence import load_game, replay_game, save_game, session_state
from osrlib.versioning import engine_version

GOLDEN_PATH = Path(__file__).parent / "goldens" / "phase4_delve.json"

REGENERATE_HINT = (
    "golden mismatch; if the change is intentional, regenerate with "
    "`uv run python tests/generate_phase4_goldens.py` and explain why in the commit message"
)


@pytest.fixture(scope="module")
def golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def replayed(golden):
    return replay_game(
        golden["master_seed"],
        golden["party_document"],
        build_milestone_adventure(),
        Ruleset(),
        golden["command_log"],
    )


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


class TestPartyBuild:
    def test_party_rebuilds_from_the_creation_streams(self, golden):
        members = build_milestone_party(golden["master_seed"])
        document = party_to_document(members)
        # The engine version stamp tracks the package version, so comparing it here
        # would force a golden regeneration on every release with zero behavior
        # change; the golden keeps the stamp of the engine that produced it. Strip
        # the stamp from copies — the golden fixture is module-scoped and the
        # replayed fixture feeds golden["party_document"] into replay_game, so
        # popping the shared dict would plant test-order coupling.
        assert document["engine_version"] == engine_version()
        stripped = {key: value for key, value in document.items() if key != "engine_version"}
        expected = {key: value for key, value in golden["party_document"].items() if key != "engine_version"}
        assert canonical(stripped) == canonical(expected), REGENERATE_HINT


class TestReplay:
    def test_event_stream_matches_byte_for_byte(self, golden, replayed):
        events = [entry if isinstance(entry, dict) else entry.model_dump(mode="json") for entry in replayed.event_log]
        assert canonical(events) == canonical(golden["event_log"]), REGENERATE_HINT

    def test_transcript_matches(self, golden, replayed):
        transcript = [format_message(entry) for entry in replayed.event_log if isinstance(entry, Event)]
        assert transcript == golden["transcript"], REGENERATE_HINT

    def test_final_stream_states_scoped_per_stream(self, golden, replayed):
        states = {key: state.model_dump(mode="json") for key, state in replayed.streams.export_states().items()}
        assert states == golden["final_stream_states"], REGENERATE_HINT

    def test_final_clock_and_ledgers(self, golden, replayed):
        assert replayed.clock.rounds == golden["final_clock_rounds"]
        defeated = [record.model_dump(mode="json") for record in replayed.defeated_monsters]
        assert defeated == golden["defeated_monsters"]
        assert replayed.mode.value == "town"

    def test_the_milestone_beats_are_in_the_stream(self, golden):
        codes = {event.get("code") for event in golden["event_log"]}
        for required in (
            "exploration.item.acquired",  # town outfitting
            "exploration.location.entered",
            "exploration.trap.sprung",  # the pit
            "exploration.door.forced",
            "exploration.door.wedged",
            "exploration.listen.heard",
            "exploration.search.found",  # the secret door
            "exploration.light.expired",  # torches burning out
            "exploration.wandering.checked",
            "encounter.started",
            "encounter.surprise.rolled",
            "encounter.reaction.rolled",
            "battle.started",
            "battle.round.started",
            "battle.spell.declared",
            "magic.cast.disrupted",  # the machine-found RAW trigger
            "combat.morale.broke",  # the goblin rout
            "battle.side.fled",
            "magic.turning.turned",  # the crypt declaration
            "battle.monster.defeated",
            "battle.ended.victory",
            "encounter.evasion.pursuit",
            "encounter.pursuit.distracted",  # dropped treasure worked
            "encounter.exhaustion.gained",  # thirty rounds of running
            "exploration.rest.rested",  # the night camp
            "magic.memorize.prepared",  # re-preparation
        ):
            assert required in codes, f"missing milestone beat {required}"
        # An area spell resolved under the footprint rule: the fire ball's cast
        # event names several targets.
        fire_balls = [
            event
            for event in golden["event_log"]
            if event.get("event_type") == "spell_cast" and event.get("spell_id") == "fire_ball"
        ]
        assert any(len(event.get("target_ids", ())) >= 2 for event in fire_balls)

    def test_the_command_log_round_trips(self, golden):
        for entry in golden["command_log"]:
            assert parse_command(entry) is not None


class TestCheckpoints:
    @pytest.mark.parametrize("name", ("mid_exploration", "mid_battle", "end"))
    def test_load_equals_replay_and_continues_identically(self, golden, replayed, name):
        index = golden["checkpoints"][name]
        prefix = replay_game(
            golden["master_seed"],
            golden["party_document"],
            build_milestone_adventure(),
            Ruleset(),
            golden["command_log"][:index],
        )
        document = json.loads(json.dumps(save_game(prefix)))
        restored = load_game(document)
        assert session_state(restored) == session_state(prefix)
        # The reload continues identically to the straight run.
        for entry in golden["command_log"][index:]:
            command = parse_command(entry)
            result = restored.execute(command)
            assert result.accepted, (name, command.command_type)
        assert session_state(restored) == session_state(replayed)
