"""Generate the phase 13 golden: the journal a trigger layer would write.

One golden file, `phase13_journal.json` — a short delve driven the way an authored
trigger and quest layer drives one, with no listeners registered. The party steps to
the east lever: the pull is a `MarkTriggerFired` and a `SetFlag` stamped with the
trigger's own id, and the beat it leaves behind is an `AddJournalEntry` stamped the
same way. A turn's breather later the party hauls the same lever again — the re-mark
appends nothing to the fired-marks and still writes its second journal beat, stamped a
turn further on — a `RecordNote` puts the referee's margin on the record, and a `GrantItem`
stamped with a quest id shows "why did the party get this" sitting in the log.

The milestone the file records: save/load and replay rebuild the journal and the
fired-marks exactly, and every `source` stamp survives the command log through both.

Run `uv run python tests/generate_phase13_goldens.py` and explain any golden change
in the commit message.
"""

import json
from pathlib import Path

from crawl_fixtures import build_adventure, build_party
from osrlib.core.events import Event
from osrlib.crawl.commands import (
    AddJournalEntry,
    Command,
    EnterDungeon,
    GrantItem,
    MarkTriggerFired,
    MoveParty,
    RecordNote,
    Rest,
    SetFlag,
    parse_command,
)
from osrlib.crawl.dungeon import Direction
from osrlib.crawl.session import GameSession
from osrlib.messages import format_message

GOLDEN_PATH = Path(__file__).parent / "goldens" / "phase13_journal.json"
SEED = 20_260_807

LEVER = "trigger:lever-east"
QUEST = "quest:the-jade-idol"

# Enter, walk to the lever, pull it; breathe, pull it again; note the margin; pay out.
SCRIPT: tuple[Command, ...] = (
    EnterDungeon(dungeon_id="delve"),
    MoveParty(direction=Direction.EAST),
    MarkTriggerFired(trigger_id="lever-east", source=LEVER),
    SetFlag(key="crypt.portcullis", value="open", source=LEVER),
    AddJournalEntry(text="The east lever gives; somewhere below, a portcullis grinds upward.", source=LEVER),
    Rest(kind="turn"),
    MarkTriggerFired(trigger_id="lever-east", source=LEVER),
    AddJournalEntry(text="The lever, hauled a second time, only grinds.", source=LEVER),
    RecordNote(text="The portcullis was already up: the second pull changed nothing."),
    GrantItem(character_id="character-0001", item_id="silver_dagger", quantity=1, source=QUEST),
)


def new_session(seed: int) -> GameSession:
    """The scenario's session: the stock party at the test delve's entrance."""
    return GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=seed)


def snapshot(session: GameSession) -> dict:
    """The end state: draws, time, and the two blocks this phase exists for."""
    return {
        "streams": {key: state.model_dump(mode="json") for key, state in session.streams.export_states().items()},
        "clock_rounds": session.clock.rounds,
        "mode": session.mode.value,
        "location": session.dungeon_state.location.model_dump(mode="json"),
        "flags": dict(session.flags),
        "fired_triggers": list(session.fired_triggers),
        "journal": [entry.model_dump(mode="json") for entry in session.journal],
    }


def run_scenario(seed: int) -> GameSession:
    """Play the script, asserting the beats the golden exists to record.

    Returns:
        The finished session, its journal written and its lever marked once.

    Raises:
        RuntimeError: If a command is refused, if the re-mark appends a second
            fired-mark, or if the two beats carry the same clock stamp.
    """
    session = new_session(seed)
    for command in SCRIPT:
        result = session.execute(command)
        if not result.accepted:
            codes = [rejection.code for rejection in result.rejections]
            raise RuntimeError(f"{command.command_type} was refused with {codes}")
    if session.fired_triggers != ["lever-east"]:
        raise RuntimeError(f"the re-mark appended a duplicate: {session.fired_triggers}")
    if len(session.journal) != 2:
        raise RuntimeError(f"the run wrote {len(session.journal)} journal entries, not 2")
    if session.journal[0].rounds == session.journal[1].rounds:
        raise RuntimeError("the two beats must straddle the turn between them")
    return session


def replay_scenario(seed: int, commands) -> GameSession:
    """Replay the accepted-command log — the stamped commands included."""
    session = new_session(seed)
    for entry in commands:
        command = entry if isinstance(entry, Command) else parse_command(entry)
        result = session.execute(command)
        if not result.accepted:
            codes = [rejection.code for rejection in result.rejections]
            raise RuntimeError(f"replay diverged: {command.command_type} rejected with {codes}")
    return session


def build_golden(seed: int) -> dict:
    session = run_scenario(seed)
    transcript = [format_message(entry) for entry in session.event_log if isinstance(entry, Event)]
    return {
        "master_seed": seed,
        "command_log": [command.model_dump(mode="json") for command in session.command_log],
        "event_log": [
            entry if isinstance(entry, dict) else entry.model_dump(mode="json") for entry in session.event_log
        ],
        "transcript": transcript,
        "final_state": snapshot(session),
    }


def write(path: Path, golden: dict) -> None:
    path.write_text(json.dumps(golden, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    golden = build_golden(SEED)
    write(GOLDEN_PATH, golden)
    commands, entries = len(golden["command_log"]), len(golden["final_state"]["journal"])
    print(f"wrote {GOLDEN_PATH} from seed {SEED} ({commands} commands, {entries} journal entries)")


if __name__ == "__main__":
    main()
