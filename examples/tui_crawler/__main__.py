"""The crawler's loop: read a line, parse to a command, execute, render the events.

Plain synchronous terminal I/O on the standard library alone — the library never
renders or prompts; this loop owns both. `--seed N --script FILE` replays a text
command transcript non-interactively (the integration test's path).
"""

import argparse
import sys
from pathlib import Path

from osrlib.core.character import CHARACTER_CREATION_STREAM
from osrlib.core.events import Visibility
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.commands import (
    BattleDeclaration,
    EngageBattle,
    EnterDungeon,
    Evade,
    MoveParty,
    Parley,
    PurchaseEquipment,
    PurchaseHealing,
    ResolveBattleRound,
    Rest,
    SellTreasure,
    TakeTreasure,
    TravelToTown,
    UseItem,
    UseStairs,
    Wait,
)
from osrlib.crawl.session import GameSession
from osrlib.messages import format_message

from .content import build_adventure
from .create import interactive_party, scripted_party
from .quest import FetchQuestListener

_DIRECTIONS = {"n": "north", "s": "south", "e": "east", "w": "west"}


def _run(session, command):
    """Execute one command and print every player-visible event it logged.

    Printing the event-log delta (rather than the result's events) shows the
    quest listener's reactions too: its nested commands append to the same log.
    """
    mark = len(session.event_log)
    result = session.execute(command)
    if not result.accepted:
        print("  (refused: " + ", ".join(rejection.code for rejection in result.rejections) + ")")
        return result
    for event in session.event_log[mark:]:
        if event.visibility is Visibility.PLAYER:
            print("  " + format_message(event))
    return result


def _first_weapon_id(member) -> str | None:
    for instance in member.inventory.wielded:
        if hasattr(instance, "instance_id"):
            return instance.instance_id
        facet = getattr(instance.template, "combat", None) or instance.template
        if getattr(facet, "damage", None) is not None:
            return instance.template.id
    return None


def _battle_round(session) -> ResolveBattleRound:
    """One auto-declared battle round: front rank attacks, the rest close or hold."""
    group = next(entry for entry in session.encounter.groups if not entry.fled and not entry.surrendered)
    living = session.party.living_members()
    front = living[:2]
    declarations = []
    for member in living:
        weapon_id = _first_weapon_id(member)
        if group.distance_feet > 5:
            declarations.append(
                BattleDeclaration(character_id=member.id, action="move", move="close", target_group_id=group.id)
            )
        elif member in front:
            declarations.append(
                BattleDeclaration(
                    character_id=member.id, action="attack", target_group_id=group.id, weapon_id=weapon_id
                )
            )
        else:
            declarations.append(BattleDeclaration(character_id=member.id, action="hold"))
    return ResolveBattleRound(declarations=tuple(declarations))


def _fight(session) -> None:
    if session.battle is None:
        _run(session, EngageBattle())
    rounds = 0
    while session.battle is not None and rounds < 50:
        result = _run(session, _battle_round(session))
        if not result.accepted:
            return
        rounds += 1


def _status(session) -> None:
    view = session.view(Visibility.PLAYER)
    print(f"[{view.mode}] round {view.clock_rounds}")
    for member in view.party:
        print(f"  {member.name} ({member.class_id} {member.level}) HP {member.current_hp}/{member.max_hp}")
        purse = member.inventory["purse"]
        valuables = ", ".join(v["name"] or v["kind"] for v in member.inventory["valuables"])
        print(f"    gold {purse['gp']} gp" + (f"; carrying {valuables}" if valuables else ""))


def _dispatch(session, line: str) -> bool:
    """Run one text command; returns False on quit."""
    words = line.strip().split()
    if not words or words[0].startswith("#"):
        return True
    verb, args = words[0].lower(), words[1:]
    if verb in ("quit", "exit"):
        return False
    if verb == "status":
        _status(session)
        return True
    if verb == "fight":
        _fight(session)
        return True
    command = None
    if verb == "enter":
        command = EnterDungeon(dungeon_id=args[0] if args else "barrow")
    elif verb == "move" and args:
        command = MoveParty.model_validate({"direction": _DIRECTIONS.get(args[0], args[0])})
    elif verb == "stairs":
        command = UseStairs()
    elif verb == "take" and args:
        target = args[0]
        if target == "cache":
            # Take the engine-created cache on this cell, whatever id it drew.
            location = session.dungeon_state.location
            from osrlib.crawl.dungeon import cell_ref

            here = cell_ref(location.dungeon_id, location.level_number, location.position)
            target = next(
                (
                    cache_id
                    for cache_id, cache in session.dungeon_state.generated_caches.items()
                    if cache.cell_ref == here
                ),
                None,
            )
            if target is None:
                print("  (no cache here)")
                return True
        command = TakeTreasure(feature_id=target)
    elif verb == "rest":
        command = Rest.model_validate({"kind": args[0] if args else "turn"})
    elif verb == "wait":
        command = Wait()
    elif verb == "parley" and args:
        command = Parley(character_id=args[0])
    elif verb == "evade":
        command = Evade.model_validate({"drop": args[0] if args else "none"})
    elif verb == "town":
        command = TravelToTown()
    elif verb == "buy" and len(args) >= 2:
        command = PurchaseEquipment(character_id=args[0], item_ids=tuple(args[1:]))
    elif verb == "sell":
        if args and args[0] == "all":
            instance_ids = tuple(
                valuable.instance_id for member in session.party.members for valuable in member.inventory.valuables
            )
            if not instance_ids:
                print("  (nothing to sell)")
                return True
            command = SellTreasure(item_ids=instance_ids)
        else:
            command = SellTreasure(item_ids=tuple(args))
    elif verb == "heal" and len(args) >= 2:
        command = PurchaseHealing.model_validate({"character_id": args[0], "service": args[1]})
    elif verb == "use" and args:
        command = UseItem(
            character_id=args[0], item_id=args[1] if len(args) > 1 else "", target_id=args[2] if len(args) > 2 else None
        )
    if command is None:
        print(f"  (unknown command: {line.strip()!r})")
        return True
    _run(session, command)
    return True


def main(argv: list[str] | None = None) -> int:
    """Run the crawler; returns the process exit code."""
    parser = argparse.ArgumentParser(description="A minimal osrlib terminal crawler.")
    parser.add_argument("--seed", type=int, default=1, help="the master seed")
    parser.add_argument("--script", type=Path, default=None, help="a command transcript to run non-interactively")
    arguments = parser.parse_args(argv)

    ruleset = Ruleset()
    adventure = build_adventure()
    # Creation draws ride the session's own creation stream so `--seed` governs
    # the whole game, party included.
    from osrlib.core.rng import RngStreams

    streams = RngStreams(master_seed=arguments.seed)
    creation_stream = streams.get(CHARACTER_CREATION_STREAM)
    if arguments.script is not None:
        party = scripted_party(creation_stream, ruleset)
    else:
        party = interactive_party(creation_stream, ruleset)
    session = GameSession.new(party, adventure, seed=arguments.seed, ruleset=ruleset)
    session.streams.restore_states(streams.export_states())
    session.register_listener(FetchQuestListener(session))

    print(f"— {adventure.name} —")
    print(adventure.description)
    for hook in adventure.hooks:
        print(f"Hook: {hook}")

    if arguments.script is not None:
        for line in arguments.script.read_text(encoding="utf-8").splitlines():
            print(f"> {line}")
            if not _dispatch(session, line):
                break
    else:
        while True:
            try:
                line = input("> ")
            except EOFError:
                break
            if not _dispatch(session, line):
                break
    _status(session)
    print(f"quest.idol = {session.flags.get('quest.idol', 'unrecovered')!r}")
    highest = max(member.level for member in session.party.members)
    print(f"Highest level reached: {highest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
