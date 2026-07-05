"""The Phase 5 goldens: hoards, NPC rosters, and the milestone playthrough.

Three golden files, regenerated with `uv run python tests/generate_phase5_goldens.py`
(explain why in the commit message):

- the hoard golden — every treasure type A–V from one seed, both tiers, full
  contents byte-asserted;
- the NPC-party golden — one basic and one expert roster, byte-asserted;
- the milestone golden — the example adventure's scripted playthrough (the same
  seed and script `test_example_crawler.py` drives through the actual binary)
  resolved through `GameSession.execute`: creation, the delve with its generated
  lair hoard, the rival NPC party fought and looted, the MacGuffin and the quest
  listener, the return, the award, the level-up. The full event stream and
  formatted transcript assert byte-for-byte, final stream states scope per RNG
  stream, and the checkpoints satisfy `load(save) == state == replay(seed,
  commands)` — the replay listener-free, since the listener's commands are in
  the log; its private state is the game's, not the kernel's, and is asserted
  separately.
"""

import json
from pathlib import Path

import pytest

from generate_phase5_goldens import (
    build_hoard_golden,
    build_npc_golden,
    replay_milestone,
    run_milestone,
)
from osrlib.core.events import Event
from osrlib.crawl.commands import parse_command
from osrlib.messages import format_message
from osrlib.persistence import load_game, save_game, session_state
from osrlib.versioning import engine_version

GOLDEN_DIR = Path(__file__).parent / "goldens"

REGENERATE_HINT = (
    "golden mismatch; if the change is intentional, regenerate with "
    "`uv run python tests/generate_phase5_goldens.py` and explain why in the commit message"
)


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def load_golden(name: str) -> dict:
    return json.loads((GOLDEN_DIR / name).read_text(encoding="utf-8"))


class TestHoardGolden:
    def test_every_type_under_both_tiers_byte_identical(self):
        golden = load_golden("phase5_hoards.json")
        assert canonical(build_hoard_golden()) == canonical(golden), REGENERATE_HINT

    def test_the_hoards_hold_the_full_spread(self):
        golden = load_golden("phase5_hoards.json")
        hoards = [hoard for tier in golden["hoards"].values() for hoard in tier.values()]
        assert any(hoard["coins"]["gp"] for hoard in hoards)
        assert any(hoard["valuables"] for hoard in hoards)
        assert any(hoard["magic_items"] for hoard in hoards)


class TestNpcPartyGolden:
    def test_both_rosters_byte_identical(self):
        golden = load_golden("phase5_npc_parties.json")
        assert canonical(build_npc_golden()) == canonical(golden), REGENERATE_HINT

    def test_the_rosters_have_their_shapes(self):
        golden = load_golden("phase5_npc_parties.json")
        assert len(golden["basic"]["members"]) == 5
        assert len(golden["expert"]["members"]) == 7
        assert all(1 <= member["level"] <= 3 for member in golden["basic"]["members"])
        assert all(member["level"] >= 3 for member in golden["expert"]["members"])


@pytest.fixture(scope="module")
def golden() -> dict:
    return load_golden("phase5_milestone.json")


@pytest.fixture(scope="module")
def scripted(golden):
    """The example's run: the text script through its dispatcher, listener registered."""
    session, party_document, checkpoints = run_milestone(golden["master_seed"])
    return session, party_document, checkpoints


@pytest.fixture(scope="module")
def replayed(golden):
    """The determinism contract: the accepted-command log alone, listener-free."""
    return replay_milestone(golden["master_seed"], golden["command_log"])


class TestMilestoneScriptedRun:
    def test_party_document_matches(self, golden, scripted):
        _, party_document, _ = scripted
        # The engine version stamp tracks the package version, so comparing it here
        # would force a golden regeneration on every release with zero behavior
        # change; the golden keeps the stamp of the engine that produced it. Strip
        # the stamp from copies — both fixtures are module-scoped, so popping the
        # shared dicts would plant test-order coupling.
        assert party_document["engine_version"] == engine_version()
        stripped = {key: value for key, value in party_document.items() if key != "engine_version"}
        expected = {key: value for key, value in golden["party_document"].items() if key != "engine_version"}
        assert canonical(stripped) == canonical(expected), REGENERATE_HINT

    def test_command_log_and_checkpoints_match(self, golden, scripted):
        session, _, checkpoints = scripted
        logged = [command.model_dump(mode="json") for command in session.command_log]
        assert canonical(logged) == canonical(golden["command_log"]), REGENERATE_HINT
        assert checkpoints == golden["checkpoints"], REGENERATE_HINT

    def test_listener_state_and_flags_match(self, golden, scripted):
        session, _, _ = scripted
        assert (
            session.listener_state
            == golden["listener_state"]
            == {"fetch_quest": {"reward_granted": True, "completed": True}}
        )
        assert session.flags == golden["flags"]
        assert session.flags["quest.idol"] == "recovered"

    def test_save_load_round_trips_the_listener_run(self, scripted):
        session, _, _ = scripted
        document = json.loads(json.dumps(save_game(session)))
        restored = load_game(document)
        assert session_state(restored) == session_state(session)


class TestMilestoneReplay:
    def test_event_stream_matches_byte_for_byte(self, golden, replayed):
        events = [entry if isinstance(entry, dict) else entry.model_dump(mode="json") for entry in replayed.event_log]
        assert canonical(events) == canonical(golden["event_log"]), REGENERATE_HINT

    def test_transcript_matches(self, golden, replayed):
        transcript = [format_message(entry) for entry in replayed.event_log if isinstance(entry, Event)]
        assert transcript == golden["transcript"], REGENERATE_HINT

    def test_final_stream_states_scoped_per_stream(self, golden, replayed):
        states = {key: state.model_dump(mode="json") for key, state in replayed.streams.export_states().items()}
        for key in ("treasure", "npc_party", "character_creation", "combat", "encounter", "wandering", "magic"):
            assert key in golden["final_stream_states"], f"stream {key} never drew in the milestone"
        assert states == golden["final_stream_states"], REGENERATE_HINT

    def test_final_clock_summary_and_records(self, golden, replayed):
        assert replayed.clock.rounds == golden["final_clock_rounds"]
        assert replayed.mode.value == "town"
        defeated = [record.model_dump(mode="json") for record in replayed.defeated_monsters]
        assert defeated == golden["defeated_monsters"]
        summary = [
            {"id": member.id, "name": member.name, "class_id": member.class_id, "level": member.level, "xp": member.xp}
            for member in replayed.party.members
        ]
        assert summary == golden["party_summary"], REGENERATE_HINT
        assert max(entry["level"] for entry in summary) == 2

    def test_replay_agrees_with_the_scripted_run_except_listener_state(self, scripted, replayed):
        session, _, _ = scripted
        original = session_state(session)
        replay = session_state(replayed)
        assert original.pop("listener_state") == {"fetch_quest": {"reward_granted": True, "completed": True}}
        assert replay.pop("listener_state") == {}
        assert original == replay

    def test_the_milestone_beats_are_in_the_stream(self, golden):
        codes = {event.get("code") for event in golden["event_log"]}
        for required in (
            "treasure.hoard.generated",  # the goblin lair's type C, generated lazily
            "exploration.item.acquired",  # the cache looted, the idol recovered
            "encounter.npc_party.spawned",  # the rival adventurers (referee-visibility roster)
            "battle.ended.victory",
            "battle.monster.defeated",
            "session.flag.set",  # the quest listener's reaction
            "session.xp.adventure_award",  # the end-of-adventure valuation delta
            "session.xp.awarded",
            "town.treasure.sold",
            "town.healing.purchased",
        ):
            assert required in codes, f"missing milestone beat {required}"

    def test_npc_defeats_fed_the_award_as_level_for_hd(self, golden):
        # The award clears the defeat ledger on return, so the beat lives in the
        # event stream: NPC adventurers fell under npc: template ids, and their
        # level-as-HD XP is inside the award's monster total.
        defeats = [event for event in golden["event_log"] if event.get("code") == "battle.monster.defeated"]
        npc_defeats = [event for event in defeats if event["template_id"].startswith("npc:")]
        assert npc_defeats, "no NPC adventurers fell in the milestone"
        award = next(event for event in golden["event_log"] if event.get("code") == "session.xp.adventure_award")
        assert award["monster_xp"] == sum(event["xp"] for event in defeats)
        assert golden["defeated_monsters"] == []  # the ledger reset with the award

    def test_the_command_log_round_trips(self, golden):
        for entry in golden["command_log"]:
            assert parse_command(entry) is not None


class TestCheckpoints:
    @pytest.mark.parametrize("name", ("mid_delve", "post_award"))
    def test_load_equals_replay_and_continues_identically(self, golden, replayed, name):
        index = golden["checkpoints"][name]
        prefix = replay_milestone(golden["master_seed"], golden["command_log"][:index])
        document = json.loads(json.dumps(save_game(prefix)))
        restored = load_game(document)
        assert session_state(restored) == session_state(prefix)
        for entry in golden["command_log"][index:]:
            command = parse_command(entry)
            result = restored.execute(command)
            assert result.accepted, (name, command.command_type)
        assert session_state(restored) == session_state(replayed)
