"""Generate the phase 11 golden: the gated warren.

One golden file, `phase11_gates.json` — a scripted delve through a dungeon whose
doors and stairs want something, with refusal probes interleaved between the
accepted commands:

- the key-gated door refuses with its authored text, then opens once the key is
  out of the niche, with its success beat in the transcript;
- the reliquary's locked-and-gated door refuses on its lock first, then on its
  gate once a referee undoes the lock, then opens when the seal is in hand;
- the ferryman's stair takes a token per crossing and refuses once the tokens run
  out — the level-triggered rule, proved by the same command refusing later;
- the sanctum door refuses for everyone until a referee sets it open, admits the
  party while it stands open, and gates again the moment it is shut.

Rejections join neither log, so the replay executes the accepted commands alone.
The two runs reaching identical state — streams, clock, doors, inventories — is
the proof that a gate refusal consumes no draws, no time, and no items; the
generator also asserts it probe by probe, comparing full state across each one.

Run `uv run python tests/generate_phase11_goldens.py` and explain any golden
change in the commit message.
"""

import json
from pathlib import Path

from crawl_fixtures import build_gated_adventure, build_party
from osrlib.core.events import Event
from osrlib.core.items import MagicItemInstance
from osrlib.crawl.commands import (
    CloseDoor,
    Command,
    EnterDungeon,
    MoveParty,
    OpenDoor,
    SetDoorState,
    TakeTreasure,
    UseStairs,
    parse_command,
)
from osrlib.crawl.dungeon import Direction
from osrlib.crawl.session import GameSession
from osrlib.messages import format_message

GOLDEN_PATH = Path(__file__).parent / "goldens" / "phase11_gates.json"
SEED = 20_260_805

# Each step is a command and the rejection code it must draw — `None` for the
# commands that must be accepted. The refusals are the probes: they leave no
# trace in either log, so the replay never sees them.
SCRIPT: tuple[tuple[Command, str | None], ...] = (
    (EnterDungeon(dungeon_id="warren"), None),
    (MoveParty(direction=Direction.EAST), None),
    # The sentinel wants brass, and the key is still in the niche.
    (OpenDoor(direction=Direction.EAST), "exploration.door.gate_refused"),
    (TakeTreasure(feature_id="niche"), None),
    (OpenDoor(direction=Direction.EAST), None),
    (MoveParty(direction=Direction.EAST), None),
    # The reliquary is locked as well as gated: the lock answers first.
    (OpenDoor(direction=Direction.SOUTH), "exploration.door.locked"),
    (SetDoorState(dungeon_id="warren", level_number=1, x=2, y=0, direction=Direction.SOUTH, unlocked=True), None),
    # Unlocked, and now the gate alone bars the way.
    (OpenDoor(direction=Direction.SOUTH), "exploration.door.gate_refused"),
    (MoveParty(direction=Direction.EAST), None),
    # The ferryman takes the token and the stair opens.
    (UseStairs(), None),
    (MoveParty(direction=Direction.EAST), None),
    (TakeTreasure(feature_id="vault"), None),
    (MoveParty(direction=Direction.WEST), None),
    (UseStairs(), None),
    # The token is spent, so the same crossing now refuses.
    (UseStairs(), "exploration.transition.gate_refused"),
    # The sanctum's sigil is nowhere in the warren.
    (OpenDoor(direction=Direction.EAST), "exploration.door.gate_refused"),
    (SetDoorState(dungeon_id="warren", level_number=1, x=3, y=0, direction=Direction.EAST, open=True), None),
    # A door standing open admits passage unchecked, in both directions.
    (MoveParty(direction=Direction.EAST), None),
    (MoveParty(direction=Direction.WEST), None),
    (CloseDoor(direction=Direction.EAST), None),
    (MoveParty(direction=Direction.EAST), "exploration.move.blocked"),
    # Shut again, the gate applies again.
    (OpenDoor(direction=Direction.EAST), "exploration.door.gate_refused"),
    (MoveParty(direction=Direction.WEST), None),
    # Lock undone and seal in hand: both layers satisfied at last.
    (OpenDoor(direction=Direction.SOUTH), None),
    (MoveParty(direction=Direction.SOUTH), None),
)


def new_session(seed: int) -> GameSession:
    """The scenario's session: the stock party in the gated warren."""
    return GameSession.new(build_party(), build_gated_adventure(), seed=seed)


def inventories(session: GameSession) -> dict[str, dict[str, int]]:
    """Every member's carried catalog ids and counts, in marching order."""
    carried: dict[str, dict[str, int]] = {}
    for member in session.party.members:
        counts: dict[str, int] = {}
        for instance in member.inventory.all_instances():
            item_id = instance.template_id if isinstance(instance, MagicItemInstance) else instance.template.id
            counts[item_id] = counts.get(item_id, 0) + instance.quantity
        carried[str(member.id)] = counts
    return carried


def snapshot(session: GameSession) -> dict:
    """What a refusal must leave untouched: draws, time, doors, and packs."""
    return {
        "streams": {key: state.model_dump(mode="json") for key, state in session.streams.export_states().items()},
        "clock_rounds": session.clock.rounds,
        "odometer_thirds": session.odometer_thirds,
        "doors": {ref: state.model_dump(mode="json") for ref, state in session.dungeon_state.doors.items()},
        "inventories": inventories(session),
        "location": session.dungeon_state.location.model_dump(mode="json"),
    }


def run_scenario(seed: int) -> tuple[GameSession, list[dict]]:
    """Play the script, recording every refusal and proving each one changed nothing.

    Returns:
        The finished session and the recorded refusals, each carrying the position
        in the accepted-command log where it happened.
    """
    session = new_session(seed)
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
    return session, refusals


def replay_scenario(seed: int, commands) -> GameSession:
    """Replay the accepted-command log alone — the probes are not in it."""
    session = new_session(seed)
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
    codes = {getattr(entry, "code", None) for entry in session.event_log}
    for required in ("exploration.item.consumed", "exploration.door.opened", "exploration.location.entered"):
        if required not in codes:
            raise RuntimeError(f"milestone beat missing from the run: {required}")
    if not any("The brass key turns" in line for line in transcript):
        raise RuntimeError("the door's success beat never reached the transcript")
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
    commands, refusals = len(golden["command_log"]), len(golden["refusals"])
    print(f"wrote {GOLDEN_PATH} from seed {SEED} ({commands} commands, {refusals} refusals)")


if __name__ == "__main__":
    main()
