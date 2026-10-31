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
from osrlib.core.items import Coins
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.battle import MELEE_RANGE_FEET, preview_battle_round
from osrlib.crawl.commands import (
    BattleDeclaration,
    EngageBattle,
    EnterDungeon,
    Evade,
    GiveItems,
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
from osrlib.crawl.interpreter import Interpreter
from osrlib.crawl.session import GameSession
from osrlib.messages import format_message

from .content import build_adventure
from .create import interactive_party, scripted_party

_DIRECTIONS = {"n": "north", "s": "south", "e": "east", "w": "west"}


# --8<-- [start:render-events]
def _run(session, command):
    """Execute one command and print every player-visible event it came back with.

    The result envelope already carries the whole chain — everything a nested
    listener-issued command logged, the interpreter's reactions included, folds
    into `result.events` in log order — so rendering is a plain iteration.
    A rejection prints its code, and the authored refusal text when a gate wrote
    one.
    """
    result = session.execute(command)
    if not result.accepted:
        reasons = []
        for rejection in result.rejections:
            refusal = rejection.params.get("refusal")
            reasons.append(f"{rejection.code} — {refusal}" if refusal else rejection.code)
        print("  (refused: " + ", ".join(reasons) + ")")
        return result
    for event in result.events:
        if event.visibility is Visibility.PLAYER:
            print("  " + format_message(event))
    return result


# --8<-- [end:render-events]


def _first_weapon_id(member) -> str | None:
    for instance in member.inventory.wielded:
        if hasattr(instance, "instance_id"):
            return instance.instance_id
        facet = getattr(instance.template, "combat", None) or instance.template
        if getattr(facet, "damage", None) is not None:
            return instance.template.id
    return None


def _battle_round(session) -> ResolveBattleRound:
    """One auto-declared battle round: every member closes on the nearest group and fights it.

    The plan comes from the player view and the round preview, as any front end would build it.
    The round must name exactly the members able to act, so the roster is the view's `declarers`.
    A slept member named alongside the rest bounces the whole round (`roster_mismatch`). Each
    member then gets the first of these plans that the preview accepts.

    1. A member in the group's front rank attacks where they stand.
    2. A member who can reach the group this round attacks with `move="close"`, when the front rank
       toward it still has a place. The front rank is as wide as the space the party stands in
       allows: two in a ten-foot passage, more in a room.
    3. A member farther than 5' away closes without attacking.
    4. Anyone else holds.
    """
    view = session.view(Visibility.PLAYER)
    encounter = view.encounter
    rates = {member.id: member.encounter_rate_feet for member in view.party}
    members = {member.id: member for member in session.party.members}
    groups = [group for group in encounter.groups if group.count and not group.fled]
    if not groups:
        return ResolveBattleRound(
            declarations=tuple(
                BattleDeclaration(character_id=member_id, action="hold") for member_id in encounter.declarers
            )
        )
    group = min(groups, key=lambda entry: min(entry.member_distance_feet.values()))
    planned: list[BattleDeclaration] = []
    for member_id in encounter.declarers:
        distance = group.member_distance_feet[member_id]
        attack = BattleDeclaration(
            character_id=member_id,
            action="attack",
            target_group_id=group.id,
            weapon_id=_first_weapon_id(members[member_id]),
        )
        candidates = []
        if member_id in group.front_rank:
            candidates.append(attack)
        else:
            taken = preview_battle_round(session, planned).front_rank[group.id]
            room = encounter.formation_width is None or len(taken) < encounter.formation_width
            if room and distance <= rates[member_id] + MELEE_RANGE_FEET:
                candidates.append(attack.model_copy(update={"move": "close"}))
            if distance > MELEE_RANGE_FEET:
                candidates.append(
                    BattleDeclaration(character_id=member_id, action="move", move="close", target_group_id=group.id)
                )
        planned.append(
            next(
                (
                    candidate
                    for candidate in candidates
                    if not preview_battle_round(session, [*planned, candidate]).rejections[member_id]
                ),
                BattleDeclaration(character_id=member_id, action="hold"),
            )
        )
    return ResolveBattleRound(declarations=tuple(planned))


def _fight(session) -> None:
    if session.battle is None:
        _run(session, EngageBattle())
    rounds = 0
    while session.battle is not None and rounds < 50:
        result = _run(session, _battle_round(session))
        if not result.accepted:
            return
        rounds += 1


# --8<-- [start:player-view]
def _status(session) -> None:
    view = session.view(Visibility.PLAYER)
    print(f"[{view.mode}] round {view.clock_rounds}")
    for member in view.party:
        print(f"  {member.name} ({member.class_id} {member.level}) HP {member.current_hp}/{member.max_hp}")
        purse = member.inventory["purse"]
        valuables = ", ".join(v["name"] or v["kind"] for v in member.inventory["valuables"])
        print(f"    gold {purse['gp']} gp" + (f"; carrying {valuables}" if valuables else ""))
    # Active quests only: a completed quest leaves the projection, its record kept
    # by the journal.
    for quest in view.quests:
        objectives = ", ".join(f"{objective.name} {objective.state}" for objective in quest.objectives)
        print(f"  Quest: {quest.name}" + (f" — {objectives}" if objectives else ""))


def _journal(session) -> None:
    view = session.view(Visibility.PLAYER)
    if not view.journal:
        print("  (the journal is empty)")
        return
    for entry in view.journal:
        print(f"  [round {entry.rounds}] {entry.text}")


# --8<-- [end:player-view]


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
    if verb == "journal":
        _journal(session)
        return True
    if verb == "fight":
        _fight(session)
        return True
    # --8<-- [start:parse-command]
    command = None
    if verb == "enter":
        command = EnterDungeon(dungeon_id=args[0] if args else "barrow")
    elif verb == "move" and args:
        command = MoveParty.model_validate({"direction": _DIRECTIONS.get(args[0], args[0])})
    # --8<-- [end:parse-command]
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
    elif verb == "give" and len(args) >= 3:
        # The distribute-the-load move: a purse full of sale coin weighs a coin
        # apiece, and the party moves at its slowest member's rate.
        command = GiveItems(character_id=args[0], recipient_id=args[1], coins=Coins(gp=int(args[2])))
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
    # --8<-- [start:register-interpreter]
    session = GameSession.new(party, adventure, seed=arguments.seed, ruleset=ruleset)
    session.streams.restore_states(streams.export_states())
    session.register_listener(Interpreter(session))
    # --8<-- [end:register-interpreter]

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
