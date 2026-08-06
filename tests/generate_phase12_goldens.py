"""Generate the phase 12 golden: the session that ends.

One golden file, `phase12_wipe.json` — a scripted delve that lasts two commands.
The party enters the fume vault, steps east into the gas, and the 2-in-6 spring die
fires: a save-or-die trap with no save and `affects="party"` kills all four at once,
and the session ends in `game_over` with a `GameOverEvent` closing the command.

The seed is chosen so the spring fires on entry — the wipe is scripted, not lucky —
and the run continues past the ending with two referee probes, a `RollDice` and a
`SetFlag`, which a terminal session still executes and still logs. Replaying the
accepted-command log reaches the same state, and a save of the finished run loads
back to it: the standing load-equals-replay guarantee, now over a session that ends
rather than one that stops mid-delve.

Run `uv run python tests/generate_phase12_goldens.py` and explain any golden change
in the commit message.
"""

import json
from pathlib import Path

from crawl_fixtures import build_gas_trap_adventure, build_party
from osrlib.core.events import Event
from osrlib.crawl.commands import (
    Command,
    EnterDungeon,
    MoveParty,
    RollDice,
    SessionMode,
    SetFlag,
    parse_command,
)
from osrlib.crawl.dungeon import Direction
from osrlib.crawl.session import GameSession
from osrlib.messages import format_message

GOLDEN_PATH = Path(__file__).parent / "goldens" / "phase12_wipe.json"
SEED = 20_260_806

# Enter, step into the gas, die — then two referee commands among the corpses.
SCRIPT: tuple[Command, ...] = (
    EnterDungeon(dungeon_id="vault"),
    MoveParty(direction=Direction.EAST),
    RollDice(expression="2d6"),
    SetFlag(key="fume_vault_epitaph", value="four went in"),
)


def new_session(seed: int) -> GameSession:
    """The scenario's session: the stock party at the fume vault's door."""
    return GameSession.new(build_party(), build_gas_trap_adventure(), seed=seed)


def snapshot(session: GameSession) -> dict:
    """The end state: draws, time, mode, and what became of the party."""
    return {
        "streams": {key: state.model_dump(mode="json") for key, state in session.streams.export_states().items()},
        "clock_rounds": session.clock.rounds,
        "mode": session.mode.value,
        "location": session.dungeon_state.location.model_dump(mode="json"),
        "living_members": [str(member.id) for member in session.party.living_members()],
        "death_records": {key: record.model_dump(mode="json") for key, record in session.death_records.items()},
        "flags": dict(session.flags),
        "sprung_traps": list(session.dungeon_state.sprung_traps),
    }


def run_scenario(seed: int) -> GameSession:
    """Play the script, asserting the beats the golden exists to record.

    Returns:
        The finished session, ended in `game_over`.

    Raises:
        RuntimeError: If a command is refused, if the trap does not spring on
            entry, or if the wipe does not end the session.
    """
    session = new_session(seed)
    for index, command in enumerate(SCRIPT):
        result = session.execute(command)
        if not result.accepted:
            codes = [rejection.code for rejection in result.rejections]
            raise RuntimeError(f"{command.command_type} was refused with {codes}")
        if index == 1:
            codes = [getattr(event, "code", None) for event in result.events]
            if "exploration.trap.sprung" not in codes:
                raise RuntimeError(f"seed {seed}: the spring die missed — pick a seed whose 2-in-6 fires on entry")
            if codes[-1] != "session.game_over":
                raise RuntimeError("the wipe must close the command that caused it")
    if session.mode is not SessionMode.GAME_OVER:
        raise RuntimeError(f"the run ended in {session.mode.value}, not game_over")
    if session.party.living_members():
        raise RuntimeError("the gas left someone standing")
    return session


def replay_scenario(seed: int, commands) -> GameSession:
    """Replay the accepted-command log — the referee probes among the corpses included."""
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
    commands, events = len(golden["command_log"]), len(golden["event_log"])
    print(f"wrote {GOLDEN_PATH} from seed {SEED} ({commands} commands, {events} events)")


if __name__ == "__main__":
    main()
