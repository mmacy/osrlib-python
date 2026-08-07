"""Generate the phase 15 golden: the barrow errand, run and won as authored data.

One golden file, `phase15_quest.json` — a scripted delve into a barrow whose quest is
authored content and whose only actor besides the player is the registered
interpreter:

- crossing the threshold fires an ordinary trigger and then activates the quest, in
  that order, and both write their beat to the party's journal;
- the shrine's reliquary holds the bundled idol, and taking it completes the first
  objective on the catalog id the acquisition reported;
- the crypt reveals the hidden second objective, which the game completes by writing
  the rite flag;
- that completion satisfies the quest's `all` rule, so the quest completes, the
  adventure concludes into `victory`, and the rewards pay afterwards: the authored
  spawn is illegal in a session that has ended and drops with a note, while the coins,
  the XP, and the flag all land;
- a play command after the ending is refused `wrong_mode` and leaves no trace, and a
  referee's grant is still accepted over the closed adventure.

The milestone the file records: the quest authored as data, live with the interpreter
registered, replays identically with no listeners — command and event logs byte-equal,
every state block equal but the interpreter's provably empty listener slot.

Run `uv run python tests/generate_phase15_goldens.py` and explain any golden change in
the commit message.
"""

import json
from pathlib import Path

from crawl_fixtures import RITE_KEY, build_barrow_adventure, build_party
from osrlib.core.events import Event
from osrlib.crawl.commands import (
    Command,
    EnterDungeon,
    GrantItem,
    MoveParty,
    SetFlag,
    TakeTreasure,
    parse_command,
)
from osrlib.crawl.dungeon import Direction
from osrlib.crawl.interpreter import Interpreter
from osrlib.crawl.session import GameSession
from osrlib.messages import format_message

GOLDEN_PATH = Path(__file__).parent / "goldens" / "phase15_quest.json"
SEED = 20_260_815

QUEST = "quest:the-idol"
MOUTH = "trigger:barrow-mouth"

# Each step is a command and the rejection code it must draw — `None` for the commands
# that must be accepted. The one refusal is the move after the ending: play is over,
# and it leaves no trace in either log.
SCRIPT: tuple[tuple[Command, str | None], ...] = (
    # The threshold: the trigger fires, then the quest activates.
    (EnterDungeon(dungeon_id="barrow"), None),
    (MoveParty(direction=Direction.EAST), None),
    # Into the shrine, and the idol out of the reliquary.
    (MoveParty(direction=Direction.EAST), None),
    (TakeTreasure(feature_id="reliquary"), None),
    # Into the crypt, which reveals what the slab wants.
    (MoveParty(direction=Direction.EAST), None),
    # The rite: a flag the game writes, and the objective that watches it. The quest's
    # rule is satisfied the moment it lands, so the adventure ends here.
    (SetFlag(key=RITE_KEY, value="spoken"), None),
    # Play is over: the next step is refused and changes nothing.
    (MoveParty(direction=Direction.WEST), "session.command.wrong_mode"),
    # The referee's hand still reaches the closed adventure.
    (GrantItem(character_id="character-0001", item_id="torch", quantity=6), None),
)


def new_session(seed: int, *, listening: bool) -> GameSession:
    """The scenario's session; `listening` decides whether the interpreter plays."""
    session = GameSession.new(build_party(), build_barrow_adventure(), seed=seed)
    if listening:
        session.register_listener(Interpreter(session))
    return session


def snapshot(session: GameSession) -> dict:
    """The end state: draws, time, mode, and the blocks the quest layer writes."""
    return {
        "streams": {key: state.model_dump(mode="json") for key, state in session.streams.export_states().items()},
        "clock_rounds": session.clock.rounds,
        "mode": session.mode.value,
        "location": session.dungeon_state.location.model_dump(mode="json"),
        "flags": dict(session.flags),
        "fired_triggers": list(session.fired_triggers),
        "quests": {quest_id: state.model_dump(mode="json") for quest_id, state in session.quests.items()},
        "journal": [entry.model_dump(mode="json") for entry in session.journal],
    }


def run_scenario(seed: int) -> tuple[GameSession, list[dict]]:
    """Play the script with the interpreter registered, recording the refused step.

    Returns:
        The finished session and the recorded refusals, each carrying the position in
        the accepted-command log where it happened.

    Raises:
        RuntimeError: If a command draws the wrong answer, if a refusal changes state,
            or if the milestone's beats are not all in the run.
    """
    session = new_session(seed, listening=True)
    refusals: list[dict] = []
    for command, expected in SCRIPT:
        before = snapshot(session)
        result = session.execute(command)
        if expected is None:
            if not result.accepted:
                codes = [rejection.code for rejection in result.rejections]
                raise RuntimeError(f"{command.command_type} was refused with {codes}")
            continue
        if result.accepted:
            raise RuntimeError(f"{command.command_type} was accepted where {expected} was expected")
        codes = [rejection.code for rejection in result.rejections]
        if codes != [expected]:
            raise RuntimeError(f"{command.command_type} drew {codes}, expected [{expected!r}]")
        if snapshot(session) != before:
            raise RuntimeError(f"the refused {command.command_type} changed session state")
        refusals.append(
            {
                "after_commands": len(session.command_log),
                "command": command.model_dump(mode="json"),
                "code": result.rejections[0].code,
                "params": dict(result.rejections[0].params),
            }
        )
    state = session.quests["the-idol"]
    if state.status != "completed":
        raise RuntimeError(f"the quest did not complete: {state.status}")
    if not all(objective.complete for objective in state.objectives.values()):
        raise RuntimeError(f"the all rule completed with an objective outstanding: {state.objectives}")
    if session.mode.value != "victory":
        raise RuntimeError(f"the concluding quest left the session in {session.mode.value}")
    if session.fired_triggers != ["barrow-mouth"]:
        raise RuntimeError(f"the threshold trigger must fire once: {session.fired_triggers}")
    # The trigger's beat, then the quest's five: offer, the idol, the slab, the rite,
    # and the closing line.
    if len(session.journal) != 6:
        raise RuntimeError(f"the run wrote {len(session.journal)} journal entries, not 6")
    if session.listener_state != {Interpreter.key: {}}:
        raise RuntimeError(f"the interpreter kept state: {session.listener_state}")
    if any(command.command_type == "spawn_monsters" for command in session.command_log):
        raise RuntimeError("the reward spawn was accepted after the ending; it must be dropped")
    if not any(command.command_type == "record_note" for command in session.command_log):
        raise RuntimeError("the dropped reward recorded no note")
    if session.flags.get("quest.idol") != "recovered":
        raise RuntimeError("the flag reward after the dropped one did not land")
    return session, refusals


def replay_scenario(seed: int, commands) -> GameSession:
    """Replay the accepted-command log with no listeners at all — the milestone's proof."""
    session = new_session(seed, listening=False)
    for entry in commands:
        command = entry if isinstance(entry, Command) else parse_command(entry)
        result = session.execute(command)
        if not result.accepted:
            codes = [rejection.code for rejection in result.rejections]
            raise RuntimeError(f"replay diverged: {command.command_type} rejected with {codes}")
    return session


def build_golden(seed: int) -> dict:
    session, refusals = run_scenario(seed)
    transcript = [format_message(entry) for entry in session.event_log if isinstance(entry, Event)]
    return {
        "master_seed": seed,
        "refusals": refusals,
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
    commands = len(golden["command_log"])
    beats = len(golden["final_state"]["journal"])
    print(f"wrote {GOLDEN_PATH} from seed {SEED} ({commands} commands, {beats} journal beats)")


if __name__ == "__main__":
    main()
