"""Generate the Phase 5 goldens: hoards, NPC rosters, and the milestone playthrough.

Three golden files:

- `phase5_hoards.json` — every treasure type A–V generated from one seed under
  both tiers, sequentially on one treasure stream, full contents byte-asserted.
- `phase5_npc_parties.json` — one basic and one expert NPC roster from one seed,
  members, spells, items, and the shared U+V bundle byte-asserted.
- `phase5_milestone.json` — the example adventure's scripted playthrough (the
  same seed and text script `test_example_crawler.py` drives through the actual
  binary) resolved through `GameSession.execute`: creation from the session's
  own streams, the delve with its generated lair hoard, the rival NPC party
  fought and looted, the first return with its award and town business, the
  second trip for the MacGuffin, and the homecoming that completes the authored
  quest into victory — with the accepted-command log, the full event stream, the
  formatted transcript, per-stream final states, the quest block, and
  checkpoints mid-delve, at the first return, and after the final one.

Run `uv run python tests/generate_phase5_goldens.py` and explain any golden
change in the commit message.
"""

import contextlib
import io
import json
import string
import sys
from pathlib import Path

from osrlib.core.character import CHARACTER_CREATION_STREAM, party_to_document
from osrlib.core.events import Event
from osrlib.core.monsters import IdAllocator
from osrlib.core.npc import NPC_PARTY_STREAM, generate_npc_party
from osrlib.core.rng import RngStream, RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.treasure import TREASURE_STREAM, generate_treasure
from osrlib.crawl.commands import Command, parse_command
from osrlib.crawl.session import GameSession
from osrlib.messages import format_message

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = Path(__file__).parent / "goldens"
HOARD_PATH = GOLDEN_DIR / "phase5_hoards.json"
NPC_PATH = GOLDEN_DIR / "phase5_npc_parties.json"
MILESTONE_PATH = GOLDEN_DIR / "phase5_milestone.json"
SCRIPT_PATH = REPO_ROOT / "examples" / "tui_crawler" / "scripts" / "milestone.txt"

HOARD_SEED = 20_260_705
NPC_SEED = 20_260_706
MILESTONE_SEED = 21  # pinned with test_example_crawler.py: one seed, one script, two drivers

TREASURE_TYPES = tuple(string.ascii_uppercase[:22])  # A through V


def _example():
    """Import the example package lazily — the repo root joins sys.path here.

    The example is not an installed package; the milestone golden reuses its
    content, creation script, and dispatcher directly. The quest is part of the
    content now, so there is nothing else of the example's to import: the library's
    own interpreter plays it.
    """
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from examples.tui_crawler.__main__ import _dispatch
    from examples.tui_crawler.content import build_adventure
    from examples.tui_crawler.create import scripted_party

    return _dispatch, build_adventure, scripted_party


# ------------------------------------------------------------------ hoards


def build_hoard_golden() -> dict:
    """Every type A–V under both tiers, drawn sequentially from one stream."""
    streams = RngStreams(master_seed=HOARD_SEED)
    stream = streams.get(TREASURE_STREAM)
    allocator = IdAllocator()
    hoards: dict[str, dict] = {}
    for tier in ("basic", "expert"):
        hoards[tier] = {
            letter: generate_treasure(letter, tier=tier, stream=stream, allocator=allocator).model_dump(mode="json")
            for letter in TREASURE_TYPES
        }
    return {
        "master_seed": HOARD_SEED,
        "hoards": hoards,
        "final_stream_state": stream.export_state().model_dump(mode="json"),
    }


# ------------------------------------------------------------------ NPC rosters


def build_npc_golden() -> dict:
    """One basic and one expert roster, sequentially on one pair of streams."""
    npc_stream = RngStream.from_seed_material(NPC_SEED, NPC_PARTY_STREAM)
    treasure_stream = RngStream.from_seed_material(NPC_SEED, TREASURE_STREAM)
    allocator = IdAllocator()
    basic = generate_npc_party(
        "basic", count=5, npc_stream=npc_stream, treasure_stream=treasure_stream, allocator=allocator
    )
    expert = generate_npc_party(
        "expert", count=7, npc_stream=npc_stream, treasure_stream=treasure_stream, allocator=allocator
    )
    return {
        "master_seed": NPC_SEED,
        "basic": basic.model_dump(mode="json"),
        "expert": expert.model_dump(mode="json"),
        "final_npc_stream_state": npc_stream.export_state().model_dump(mode="json"),
        "final_treasure_stream_state": treasure_stream.export_state().model_dump(mode="json"),
    }


# ------------------------------------------------------------------ the milestone


def milestone_session(seed: int, *, interpreting: bool) -> tuple[GameSession, dict]:
    """Build the session exactly as the example binary does.

    Creation draws ride the session's own creation stream (the example's
    construction), so the whole game — party included — reproduces from `seed`.

    Args:
        seed: The master seed.
        interpreting: Register the library interpreter that plays the adventure's
            quest. The scripted run wants it; a command-log replay must not — the
            commands it issued are in the log, and a registered interpreter would
            issue them a second time.

    Returns:
        The fresh session and the starting party's stamped document.
    """
    from osrlib.crawl.interpreter import Interpreter

    _, build_adventure, scripted_party = _example()
    ruleset = Ruleset()
    streams = RngStreams(master_seed=seed)
    party = scripted_party(streams.get(CHARACTER_CREATION_STREAM), ruleset)
    party_document = party_to_document(party.members)
    session = GameSession.new(party, build_adventure(), seed=seed, ruleset=ruleset)
    session.streams.restore_states(streams.export_states())
    if interpreting:
        session.register_listener(Interpreter(session))
    return session, party_document


def run_milestone(seed: int) -> tuple[GameSession, dict, dict[str, int]]:
    """Drive the milestone text script through the example's own dispatcher.

    Returns:
        The finished session, the starting party document, and the checkpoint
        indices (accepted-command counts): mid-delve, at the first return with the
        town business still ahead of it, and after the final return that ends the
        adventure.
    """
    dispatch, _, _ = _example()
    session, party_document = milestone_session(seed, interpreting=True)
    checkpoints: dict[str, int] = {}
    returns: list[int] = []
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        for line in SCRIPT_PATH.read_text(encoding="utf-8").splitlines():
            if not dispatch(session, line):
                break
            stripped = line.strip()
            if stripped == "take cache" and "mid_delve" not in checkpoints:
                checkpoints["mid_delve"] = len(session.command_log)
            elif stripped == "town":
                returns.append(len(session.command_log))
    if len(returns) != 2:
        raise RuntimeError(f"the two-trip script must come home twice, not {len(returns)} times")
    # Named rather than last-write-wins: the script comes home twice, and the two
    # returns are different beats — the first banks an award with the town business
    # and the whole second trip still ahead of it, the second ends the adventure.
    checkpoints["first_return"], checkpoints["post_award"] = returns
    for marker in ("(refused:", "(unknown command", "(no cache here)", "(nothing to sell)"):
        if marker in captured.getvalue():
            raise RuntimeError(f"the milestone script hit {marker!r} — the transcript no longer runs clean")
    return session, party_document, checkpoints


def replay_milestone(seed: int, commands) -> GameSession:
    """Replay an accepted-command log against the example's construction, listener-free."""
    session, _ = milestone_session(seed, interpreting=False)
    for entry in commands:
        command = entry if isinstance(entry, Command) else parse_command(entry)
        result = session.execute(command)
        if not result.accepted:
            codes = [rejection.code for rejection in result.rejections]
            raise RuntimeError(f"replay diverged: {command.command_type} rejected with {codes}")
    return session


def build_milestone_golden(seed: int) -> dict:
    session, party_document, checkpoints = run_milestone(seed)
    transcript = [format_message(entry) for entry in session.event_log if isinstance(entry, Event)]
    codes = {getattr(entry, "code", None) for entry in session.event_log}
    for required in (
        "treasure.hoard.generated",
        "encounter.npc_party.spawned",
        "battle.ended.victory",
        "exploration.item.acquired",
        "session.flag.set",
        "session.xp.adventure_award",
        "session.xp.awarded",
        "town.treasure.sold",
        "town.healing.purchased",
        "session.quest.activated",
        "session.quest.objective_completed",
        "session.quest.completed",
        "session.adventure.completed",
    ):
        if required not in codes:
            raise RuntimeError(f"milestone beat missing from the run: {required}")
    quest = session.quests["the-idol"]
    if quest.status != "completed" or not all(objective.complete for objective in quest.objectives.values()):
        raise RuntimeError(f"the authored quest did not finish: {quest}")
    if session.mode.value != "victory":
        raise RuntimeError(f"the concluding quest left the session in {session.mode.value}")
    if session.flags.get("quest.idol") != "recovered":
        raise RuntimeError("the quest flag never set")
    if session.listener_state != {"osrlib.interpreter": {}}:
        raise RuntimeError(f"the interpreter kept state: {session.listener_state}")
    awards = [entry for entry in session.event_log if getattr(entry, "code", None) == "session.xp.adventure_award"]
    if len(awards) != 2:
        raise RuntimeError(f"the two-trip script banks two awards, not {len(awards)}")
    if max(member.level for member in session.party.members) < 2:
        raise RuntimeError("nobody levelled up")
    return {
        "master_seed": seed,
        "party_document": party_document,
        "checkpoints": checkpoints,
        "command_log": [command.model_dump(mode="json") for command in session.command_log],
        "event_log": [
            entry if isinstance(entry, dict) else entry.model_dump(mode="json") for entry in session.event_log
        ],
        "final_stream_states": {
            key: state.model_dump(mode="json") for key, state in session.streams.export_states().items()
        },
        "final_clock_rounds": session.clock.rounds,
        "defeated_monsters": [record.model_dump(mode="json") for record in session.defeated_monsters],
        "flags": dict(session.flags),
        "mode": session.mode.value,
        "quests": {quest_id: state.model_dump(mode="json") for quest_id, state in session.quests.items()},
        "journal": [entry.model_dump(mode="json") for entry in session.journal],
        "listener_state": {key: dict(value) for key, value in session.listener_state.items()},
        "party_summary": [
            {"id": member.id, "name": member.name, "class_id": member.class_id, "level": member.level, "xp": member.xp}
            for member in session.party.members
        ],
        "transcript": transcript,
    }


def write(path: Path, golden: dict) -> None:
    path.write_text(json.dumps(golden, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    write(HOARD_PATH, build_hoard_golden())
    print(f"wrote {HOARD_PATH}")
    write(NPC_PATH, build_npc_golden())
    print(f"wrote {NPC_PATH}")
    milestone = build_milestone_golden(MILESTONE_SEED)
    write(MILESTONE_PATH, milestone)
    print(f"wrote {MILESTONE_PATH} from seed {MILESTONE_SEED} ({len(milestone['command_log'])} commands)")


if __name__ == "__main__":
    main()
