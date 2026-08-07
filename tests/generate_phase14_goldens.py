"""Generate the phase 14 golden: the lever, the portcullis, and the ambush that missed.

One golden file, `phase14_triggers.json` — a scripted delve through a keep whose
wiring is authored data and whose only actor is the registered interpreter:

- the portcullis wants a crank nobody has, so the party's probe is refused with the
  gate's authored text and costs nothing;
- a game-issued `SetFlag` pulls the lever, and the trigger watching that key marks
  itself, sets the door open, and writes its journal beat — all in the result of the
  player's own command;
- the party walks through the grille that now stands open;
- stepping into the guardroom opens the room's own keyed encounter, and the
  area-entered trigger's spawn arrives to find it already open: the consequence is
  dropped alone and a note says exactly why, while the trigger's journal beat still
  lands.

The milestone the file records: the scenario authored as data, live with the
interpreter registered, replays identically with no listeners — command and event logs
byte-equal, every state block equal but the interpreter's provably empty listener slot.

Run `uv run python tests/generate_phase14_goldens.py` and explain any golden change in
the commit message.
"""

import json
from pathlib import Path

from crawl_fixtures import LEVER_KEY, build_party, build_portcullis_adventure
from osrlib.core.events import Event
from osrlib.crawl.commands import Command, EnterDungeon, MoveParty, OpenDoor, SetFlag, parse_command
from osrlib.crawl.dungeon import Direction
from osrlib.crawl.interpreter import Interpreter
from osrlib.crawl.session import GameSession
from osrlib.messages import format_message

GOLDEN_PATH = Path(__file__).parent / "goldens" / "phase14_triggers.json"
SEED = 20_260_808

PORTCULLIS = "trigger:portcullis-rises"
AMBUSH = "trigger:guard-ambush"

# Each step is a command and the rejection code it must draw — `None` for the commands
# that must be accepted. The one refusal is the probe: it leaves no trace in either log.
SCRIPT: tuple[tuple[Command, str | None], ...] = (
    (EnterDungeon(dungeon_id="keep"), None),
    (MoveParty(direction=Direction.EAST), None),
    (MoveParty(direction=Direction.EAST), None),
    # The grille has no handle on this side, and the crank is nowhere in the keep.
    (OpenDoor(direction=Direction.EAST), "exploration.door.gate_refused"),
    # The lever: a flag the game writes, and the trigger that watches it.
    (SetFlag(key=LEVER_KEY, value="pulled"), None),
    (MoveParty(direction=Direction.EAST), None),
    # Into the guardroom, whose goblins open an encounter before the trigger can spawn one.
    (MoveParty(direction=Direction.EAST), None),
)


def new_session(seed: int, *, listening: bool) -> GameSession:
    """The scenario's session; `listening` decides whether the interpreter plays."""
    session = GameSession.new(build_party(), build_portcullis_adventure(), seed=seed)
    if listening:
        session.register_listener(Interpreter(session))
    return session


def snapshot(session: GameSession) -> dict:
    """The end state: draws, time, the door the trigger opened, and the trigger blocks."""
    return {
        "streams": {key: state.model_dump(mode="json") for key, state in session.streams.export_states().items()},
        "clock_rounds": session.clock.rounds,
        "mode": session.mode.value,
        "location": session.dungeon_state.location.model_dump(mode="json"),
        "doors": {ref: state.model_dump(mode="json") for ref, state in session.dungeon_state.doors.items()},
        "flags": dict(session.flags),
        "fired_triggers": list(session.fired_triggers),
        "journal": [entry.model_dump(mode="json") for entry in session.journal],
    }


def run_scenario(seed: int) -> tuple[GameSession, list[dict]]:
    """Play the script with the interpreter registered, recording the gate probe.

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
    if session.fired_triggers != ["portcullis-rises", "guard-ambush"]:
        raise RuntimeError(f"both triggers must fire, in order: {session.fired_triggers}")
    if len(session.journal) != 2:
        raise RuntimeError(f"the run wrote {len(session.journal)} journal entries, not 2")
    if session.listener_state != {Interpreter.key: {}}:
        raise RuntimeError(f"the interpreter kept state: {session.listener_state}")
    if not any(command.command_type == "record_note" for command in session.command_log):
        raise RuntimeError("the colliding spawn recorded no note")
    if any(command.command_type == "spawn_monsters" for command in session.command_log):
        raise RuntimeError("the colliding spawn was accepted; it must be dropped")
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
    commands, fired = len(golden["command_log"]), len(golden["final_state"]["fired_triggers"])
    print(f"wrote {GOLDEN_PATH} from seed {SEED} ({commands} commands, {fired} triggers fired)")


if __name__ == "__main__":
    main()
