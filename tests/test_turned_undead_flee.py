"""Acceptance tests for issue #168: turned undead in a partly turned group flee and harm no one.

The OSE SRD's cleric page says of a successful turning: "Turned undead: Flee from the cleric's presence if
possible, and will not harm the cleric." The adaptations register pins the mixed-group case: "A group whose living
members are all turned or afraid retreats as a whole, and individually turned members of a mixed group don't act."

Every scenario is set up through commands and observed through session state and emitted events. Each test that
depends on the turning roll asserts its precondition (a partial turn, or a full one), so a seed that stops producing
the case fails loudly instead of passing vacuously.
"""

import pytest

from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.effects import Condition, EffectDefinition, has_condition, kill
from osrlib.core.events import Event
from osrlib.core.items import Coins
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.battle import MELEE_RANGE_FEET, MonsterAction
from osrlib.crawl.commands import (
    BattleDeclaration,
    EngageBattle,
    EnterDungeon,
    ResolveBattleRound,
    SessionMode,
    SpawnMonsters,
    TurnUndead,
)
from osrlib.crawl.dungeon import DungeonSpec, Edge, EdgeKind, LevelSpec, TreasureBundle
from osrlib.crawl.encounter import end_encounter
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession

SOLO = ("cleric",)
"""The issue's reproduction party: one cleric, so every monster attack is aimed at the cleric who turned."""

TRIO = ("cleric", "fighter", "fighter")
"""A party that lasts long enough to watch the turned monsters leave. The cleric marches first, in the front rank."""

PARTIAL_SEEDS = (4, 10, 11, 12, 44, 50)
"""Seeds whose turning roll turns some, but not all, of six skeletons, on both the encounter and the battle path."""

ALL_TURNED_ENCOUNTER_SEED = 14
"""A seed whose encounter-mode turning turns all six skeletons."""

ALL_TURNED_BATTLE_SEED = 6
"""A seed whose turning declared in battle turns all six skeletons."""

MAX_ROUNDS = 6


# ---------------------------------------------------------------------- setup


def new_session(seed: int, classes: tuple[str, ...] = SOLO) -> GameSession:
    """Open an encounter with six skeletons 10 feet from a party of level 1 characters."""
    draw = RngStreams(master_seed=seed).get(CHARACTER_CREATION_STREAM)
    members = [
        create_character(
            name=f"Member {index}", class_id=class_id, alignment=Alignment.LAWFUL, ruleset=Ruleset(), stream=draw
        ).character
        for index, class_id in enumerate(classes)
    ]
    level = LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)})
    adventure = Adventure(
        name="Turning",
        town=TownSpec(name="Town", travel_turns={"crypt": 1}),
        dungeons=(DungeonSpec(id="crypt", name="Crypt", levels=(level,)),),
    )
    session = GameSession.new(Party(members=members), adventure, seed=seed)
    assert session.execute(EnterDungeon(dungeon_id="crypt")).accepted
    assert session.execute(SpawnMonsters(template_id="skeleton", count_fixed=6, distance_feet=10)).accepted
    assert session.mode is SessionMode.ENCOUNTER, "precondition: the skeletons did not open battle at once"
    return session


def cleric_of(session: GameSession):
    return next(member for member in session.party.members if member.class_id == "cleric")


def living_monster_ids(session: GameSession) -> set[str]:
    return {
        monster_id
        for monster_id in session.monsters
        if not has_condition(session.combatant(monster_id), Condition.DEAD)
    }


def turned_ids(session: GameSession) -> set[str]:
    return {
        monster_id
        for monster_id in living_monster_ids(session)
        if has_condition(session.combatant(monster_id), Condition.TURNED)
    }


def group_of(session: GameSession, monster_id: str):
    return next(group for group in session.encounter.groups if monster_id in group.monster_ids)


def attacks(events: list[Event]) -> list[Event]:
    return [event for event in events if getattr(event, "event_type", None) == "attack_rolled"]


def hold_round(session: GameSession) -> list[Event]:
    declarations = tuple(
        BattleDeclaration(character_id=member.id, action="hold") for member in session.party.living_members()
    )
    result = session.execute(ResolveBattleRound(declarations=declarations))
    assert result.accepted, result.rejections
    return list(result.events)


def turn_in_encounter(session: GameSession) -> tuple[set[str], list[Event]]:
    """Turn with `TurnUndead` and assert the precondition: a partial turn that opens battle."""
    result = session.execute(TurnUndead(character_id=cleric_of(session).id))
    assert result.accepted, result.rejections
    turned = turned_ids(session)
    unturned = living_monster_ids(session) - turned
    assert turned, "precondition: the turning roll turned some skeletons"
    assert unturned, "precondition: the turning roll left some skeletons unturned"
    assert session.mode is SessionMode.BATTLE, "precondition: the unturned skeletons opened battle"
    return turned, list(result.events)


def turn_in_battle(session: GameSession) -> tuple[set[str], list[Event]]:
    """Engage, then declare `turn_undead` in round 1, and assert the precondition: a partial turn."""
    assert session.execute(EngageBattle()).accepted
    assert session.mode is SessionMode.BATTLE
    cleric = cleric_of(session)
    declarations = tuple(
        BattleDeclaration(character_id=member.id, action="turn_undead" if member is cleric else "hold")
        for member in session.party.living_members()
    )
    result = session.execute(ResolveBattleRound(declarations=declarations))
    assert result.accepted, result.rejections
    events = list(result.events)
    assert any(event.code == "magic.turning.turned" for event in events), "precondition: the turning succeeded"
    turned = turned_ids(session)
    unturned = living_monster_ids(session) - turned
    assert turned, "precondition: the turning roll turned some skeletons"
    assert unturned, "precondition: the turning roll left some skeletons unturned"
    return turned, events


def events_after_turning(events: list[Event]) -> list[Event]:
    index = next(i for i, event in enumerate(events) if getattr(event, "event_type", None) == "undead_turned")
    return events[index + 1 :]


def play_out(session: GameSession, rounds: int = MAX_ROUNDS) -> list[list[Event]]:
    """Hold for up to `rounds` battle rounds, stopping when the battle ends. Return each round's events."""
    played: list[list[Event]] = []
    for _ in range(rounds):
        if session.mode is not SessionMode.BATTLE:
            break
        played.append(hold_round(session))
    return played


def attacks_by(events: list[Event], attacker_ids: set[str]) -> list[Event]:
    return [event for event in attacks(events) if event.attacker_id in attacker_ids]


# ---------------------------------------------------------------------- turned monsters harm no one


def test_reproduction_turned_skeleton_does_not_attack_the_cleric():
    # The issue's reproduction, unchanged: one cleric, six skeletons, seed 4.
    session = new_session(4, SOLO)
    cleric = cleric_of(session)
    turned, _ = turn_in_encounter(session)
    assert len(turned) == 3, "precondition: seed 4 turns three of the six skeletons"
    played = play_out(session)
    assert played, "precondition: at least one battle round resolved"
    offending = attacks_by([event for events in played for event in events], turned)
    assert not [event for event in offending if event.defender_id == cleric.id]
    assert not offending


@pytest.mark.parametrize("seed", PARTIAL_SEEDS)
def test_turned_monsters_attack_no_one_in_later_rounds(seed):
    # Encounter-mode turning: no turned skeleton attacks anyone, the cleric above all, while the battle lasts.
    session = new_session(seed, TRIO)
    cleric = cleric_of(session)
    turned, _ = turn_in_encounter(session)
    played = play_out(session)
    assert played, "precondition: at least one battle round resolved"
    for round_number, events in enumerate(played, start=1):
        offending = attacks_by(events, turned)
        on_cleric = [event for event in offending if event.defender_id == cleric.id]
        assert not on_cleric, f"round {round_number}: a turned skeleton attacked the cleric who turned it"
        assert not offending, f"round {round_number}: a turned skeleton attacked {offending[0].defender_id}"


@pytest.mark.parametrize("seed", PARTIAL_SEEDS)
def test_turning_declared_in_battle_stops_the_turned_from_attacking(seed):
    # A `turn_undead` declaration: no turned skeleton attacks after the turning resolves, in the same round or later.
    session = new_session(seed, TRIO)
    cleric = cleric_of(session)
    turned, turning_round = turn_in_battle(session)
    rounds = [events_after_turning(turning_round), *play_out(session)]
    offending = attacks_by([event for events in rounds for event in events], turned)
    assert not [event for event in offending if event.defender_id == cleric.id]
    assert not offending


def test_a_custom_policy_cannot_make_a_turned_monster_attack():
    # Turning is a rule the session enforces, not a tactic a policy chooses: even a policy that orders every
    # member to strike the cleric (or, once the cleric falls, anyone left standing) gets no blow from a turned one.
    class StrikeTheClericPolicy:
        def __init__(self, cleric) -> None:
            self.cleric = cleric

        def choose(self, session, group, stream):
            living = [mid for mid in group.monster_ids if not has_condition(session.combatant(mid), Condition.DEAD)]
            if group.distance_feet > MELEE_RANGE_FEET:
                return [MonsterAction(monster_id=mid, kind="close") for mid in living]
            standing = session.party.living_members()
            target = self.cleric if self.cleric in standing else standing[0]
            return [MonsterAction(monster_id=mid, kind="melee", target_id=target.id) for mid in living]

    session = new_session(4, TRIO)
    cleric = cleric_of(session)
    session.action_policies[session.encounter.groups[0].id] = StrikeTheClericPolicy(cleric)
    turned, _ = turn_in_encounter(session)
    played = play_out(session)
    all_events = [event for events in played for event in events]
    unturned_blows = attacks_by(all_events, living_monster_ids(session) - turned)
    assert unturned_blows, "precondition: the policy drove the unturned skeletons to attack"
    assert not attacks_by(all_events, turned)


def test_a_turned_monster_that_cannot_flee_still_attacks_no_one():
    # The SRD's "flee ... if possible" leaves open what a turned monster does when it can't move. The register's
    # "individually turned members of a mixed group don't act" decides it: a webbed turned skeleton can't run, and
    # it still attacks no one, though the engine lets a webbed monster that isn't turned fight on.
    session = new_session(4, TRIO)
    turned, _ = turn_in_encounter(session)
    webbed = sorted(turned)[0]
    session.ledger.attach(
        EffectDefinition(kind="web", condition=Condition.ENTANGLED),
        webbed,
        clock=session.clock,
        allocator=session.allocator,
        registry=session.registry(),
    )
    played = play_out(session)
    assert played, "precondition: at least one battle round resolved"
    all_events = [event for events in played for event in events]
    assert not attacks_by(all_events, {webbed})
    assert not attacks_by(all_events, turned)


# ---------------------------------------------------------------------- turned monsters flee, the rest fight on


@pytest.mark.parametrize("seed", (4, 12))
def test_turned_monsters_flee_while_the_unturned_fight_on(seed):
    session = new_session(seed, TRIO)
    turned, _ = turn_in_encounter(session)
    unturned = living_monster_ids(session) - turned
    last_distance = {monster_id: group_of(session, monster_id).distance_feet for monster_id in turned}
    fled_by_round: dict[str, int] = {}
    unturned_blows = 0
    for round_number in range(1, 4):
        assert session.mode is SessionMode.BATTLE, f"precondition: the battle is still on in round {round_number}"
        events = hold_round(session)
        unturned_blows += len(attacks_by(events, unturned))
        if session.encounter is None:
            break
        for monster_id in turned:
            group = group_of(session, monster_id)
            # Turned monsters run with no unturned monster beside them, and never turn back toward the party.
            assert not [mid for mid in group.monster_ids if mid in unturned and mid in living_monster_ids(session)]
            assert group.distance_feet >= last_distance[monster_id]
            if group.fled:
                fled_by_round.setdefault(monster_id, round_number)
            else:
                assert group.distance_feet > last_distance[monster_id], f"round {round_number}: not running"
            last_distance[monster_id] = group.distance_feet
        for monster_id in unturned & living_monster_ids(session):
            group = group_of(session, monster_id)
            # The unturned members are not swept into the flight: they stand and fight at melee range.
            assert not group.fleeing
            assert not group.fled
            assert group.distance_feet == MELEE_RANGE_FEET
        if len(fled_by_round) == len(turned):
            break
    assert set(fled_by_round) == turned, "every turned skeleton got away within three rounds"
    assert unturned_blows, "the unturned skeletons kept attacking"


@pytest.mark.parametrize("seed", (4, 12))
def test_turned_monsters_flee_after_a_turning_declared_in_battle(seed):
    session = new_session(seed, TRIO)
    turned, _ = turn_in_battle(session)
    unturned = living_monster_ids(session) - turned
    got_away: set[str] = set()
    for _ in range(3):
        if session.mode is not SessionMode.BATTLE:
            break
        hold_round(session)
        if session.encounter is None:
            break
        got_away |= {monster_id for monster_id in turned if group_of(session, monster_id).fled}
        for monster_id in unturned & living_monster_ids(session):
            assert not group_of(session, monster_id).fleeing
            assert not group_of(session, monster_id).fled
        if got_away == turned:
            break
    assert got_away == turned, "every turned skeleton got away within three rounds of the turning"


# ---------------------------------------------------------------------- determinism


def _scenario_events(seed: int) -> tuple[list[dict], list[tuple]]:
    session = new_session(seed, TRIO)
    turned, opening = turn_in_encounter(session)
    played = play_out(session, rounds=4)
    events = [*opening, *[event for events in played for event in events]]
    assert not attacks_by(events, turned)
    groups = (
        [
            (sorted(group.monster_ids), group.distance_feet, group.fleeing, group.fled)
            for group in session.encounter.groups
        ]
        if session.encounter is not None
        else []
    )
    return [event.model_dump(mode="json") for event in events], groups


def test_the_same_seed_and_commands_give_the_same_events():
    first = _scenario_events(4)
    second = _scenario_events(4)
    assert first == second


# ---------------------------------------------------------------------- a fully turned group (unchanged behavior)


def test_a_fully_turned_group_in_an_encounter_ends_it_as_before():
    # Regression guard: this passes today and must keep passing. Turning every skeleton in encounter mode ends the
    # encounter without a battle, and each skeleton is reported routed.
    session = new_session(ALL_TURNED_ENCOUNTER_SEED, SOLO)
    result = session.execute(TurnUndead(character_id=cleric_of(session).id))
    assert result.accepted
    codes = [event.code for event in result.events]
    assert "magic.turning.turned" in codes
    assert "battle.started" not in codes
    assert "encounter.ended" in codes
    assert session.mode is SessionMode.EXPLORING
    outcomes = {event.monster_id: event.outcome for event in result.events if event.code == "battle.monster.defeated"}
    assert outcomes == dict.fromkeys(session.monsters, "routed")


def test_a_fully_turned_group_in_battle_retreats_as_a_whole_as_before():
    # Regression guard: this passes today and must keep passing. A group whose living members are all turned
    # retreats as a whole, harms no one, and the battle ends in victory with every skeleton routed.
    session = new_session(ALL_TURNED_BATTLE_SEED, TRIO)
    assert session.execute(EngageBattle()).accepted
    cleric = cleric_of(session)
    declarations = tuple(
        BattleDeclaration(character_id=member.id, action="turn_undead" if member is cleric else "hold")
        for member in session.party.living_members()
    )
    result = session.execute(ResolveBattleRound(declarations=declarations))
    assert result.accepted
    turned = turned_ids(session)
    assert turned == living_monster_ids(session) == set(session.monsters), "precondition: all six were turned"
    assert len(session.encounter.groups) == 1
    group = session.encounter.groups[0]
    assert set(group.monster_ids) == turned
    distance = group.distance_feet
    played = play_out(session)
    all_events = [event for events in played for event in events]
    assert not attacks_by(all_events, turned)
    moves = [event.distance_feet for event in all_events if event.code == "battle.group.moved"]
    assert moves and moves == sorted(moves) and moves[0] > distance
    codes = [event.code for event in all_events]
    assert "battle.ended.victory" in codes
    outcomes = {event.monster_id: event.outcome for event in all_events if event.code == "battle.monster.defeated"}
    assert outcomes == dict.fromkeys(turned, "routed")


SHARED_GP = 100
CARRIED_GP = 10


def stock_the_group(session: GameSession) -> None:
    """Give the skeletons a shared hoard and each one a purse, as a keyed lair would."""
    (group,) = session.encounter.groups
    group.group_treasure = TreasureBundle(coins=Coins(gp=SHARED_GP))
    for monster_id in group.monster_ids:
        group.member_treasure[monster_id] = TreasureBundle(coins=Coins(gp=CARRIED_GP))


def test_the_unturned_keep_the_shared_hoard_when_the_group_splits():
    session = new_session(4, TRIO)
    stock_the_group(session)
    turned, _ = turn_in_encounter(session)
    stayers = group_of(session, next(iter(living_monster_ids(session) - turned)))
    runners = group_of(session, next(iter(turned)))
    assert stayers.id != runners.id, "precondition: the turned skeletons split off"
    assert stayers.group_treasure is not None and stayers.group_treasure.coins.gp == SHARED_GP
    assert runners.group_treasure is None or runners.group_treasure.empty


def test_killing_the_runners_drops_only_what_they_carry():
    session = new_session(4, TRIO)
    stock_the_group(session)
    turned, _ = turn_in_encounter(session)
    for monster_id in sorted(turned):
        kill(session.combatant(monster_id))
    end_encounter(session, "evaded")
    dropped = sum(pile.coins.gp for pile in session.dungeon_state.piles.values())
    assert dropped == CARRIED_GP * len(turned), "the unturned still stand, so the shared hoard stays with them"


def test_a_running_group_rolls_no_morale_when_a_runner_dies():
    session = new_session(4, TRIO)
    turned, _ = turn_in_encounter(session)
    runners = group_of(session, next(iter(turned)))
    kill(session.combatant(sorted(turned)[0]))
    events = hold_round(session)
    assert not [event for event in events if event.code.startswith("combat.morale") and event.subject == runners.id]
