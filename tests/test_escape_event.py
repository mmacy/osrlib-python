"""Acceptance tests for issues 292 and 297: an event marks the command in which a monster group gets away.

A group that fails morale emits `MonsterFledEvent` and keeps running. It leaves the battle once it stands farther than
`FLEE_EXIT_FEET` (120') from every living party member, and `EncounterGroup.fled` becomes true. A group whose morale
score is 2 leaves as it enters the battle, because the OSE SRD's morale page says "A score of 2: Means the monster will
not fight." A group whose living members are all turned or afraid, and which stands past 120' without a move of its own,
counts as gone when the battle ends, and it's marked fled then (#297). In each case the session emits one
`MonsterEscapedEvent` (`monster_escaped`, `battle.side.escaped`) for the group in the same command result, and never
otherwise:

- In the monster block, the event comes right after the running group's `GroupMovedEvent`.
- When the group leaves as it enters the battle, the event comes right after its `MonsterFledEvent`.
- When the battle ends with a turned or afraid group past 120' that its own move didn't take out, its event comes
  right before the `BattleEndedEvent`.
- When the escape ends the battle, the event comes before the `BattleEndedEvent`.

The event is additive within the current `schema_version`, and it takes no draw and no clock time. Marking a turned or
afraid group fled as the battle ends makes it end the way the same group ends when its own move takes it out: the same
outcomes, the same experience, and the same treasure.

Every scenario runs through commands, and `execute_checked` checks the invariant on every command it runs: the groups
named by the result's escape events are exactly the groups whose `fled` turned true in that command, once each.

A goblin's or a skeleton's full movement rate is 60', so a running group of either at 10' stands at 70' after one
round and at 130' after two, and one at the 5' melee range stands at 65' and then 125'.
"""

import ast
import inspect
from pathlib import Path

import pytest

import test_turned_undead_flee as turned_fixture
from crawl_fixtures import build_adventure, build_party
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.effects import Condition, has_condition
from osrlib.core.events import Event, InitiativeRolledEvent, UndeadTurnedEvent, Visibility
from osrlib.core.items import Coins, MagicItemInstance
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl import battle as battle_module
from osrlib.crawl import commands as commands_module
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.battle import FLEE_EXIT_FEET, MELEE_RANGE_FEET
from osrlib.crawl.commands import (
    BattleDeclaration,
    Command,
    CommandResult,
    EngageBattle,
    EnterDungeon,
    GrantItem,
    LightSource,
    ResolveBattleRound,
    SpawnMonsters,
    TurnUndead,
)
from osrlib.crawl.dungeon import DungeonSpec, Edge, EdgeKind, LevelSpec, TreasureBundle
from osrlib.crawl.encounter import EncounterGroup
from osrlib.crawl.events import (
    BattleEndedEvent,
    EncounterEndedEvent,
    EncounterStartedEvent,
    GroupMovedEvent,
    MonsterDefeatedEvent,
    MonsterEscapedEvent,
    MonsterFledEvent,
    parse_any_event,
)
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession, SessionMode
from osrlib.crawl.views import PlayerView
from osrlib.data import load_monsters
from osrlib.messages import format_message
from test_battle import battle_session, hold_all
from test_spawn_into_encounter import craven_session

FIGHTER = "character-0001"
MAGIC_USER = "character-0004"
RUNNER_RATE_FEET = 60
"""The movement rate of a goblin and of a skeleton, which a running group of either covers each round."""

SRC = Path(__file__).resolve().parent.parent / "src" / "osrlib"


# ---------------------------------------------------------------------- helpers


def execute_checked(session: GameSession, command: Command) -> CommandResult:
    """Execute an accepted command and check that its escape events match the groups whose `fled` turned true.

    The groups are read from the encounter open before the command, and from the one open after it. A command that
    opens an encounter and closes it again, like a spawn whose monsters attack and leave at once, leaves no group to
    read. For that command the check is only that no group escapes twice, and the test asserts the escape it expects.
    """
    groups = list(session.encounter.groups) if session.encounter is not None else []
    already_fled = {group.id for group in groups if group.fled}
    result = session.execute(command)
    assert result.accepted, [rejection.code for rejection in result.rejections]
    escaped = sorted(event.group_id for event in result.events if isinstance(event, MonsterEscapedEvent))
    if session.encounter is None and not groups:
        opened = any(isinstance(event, EncounterStartedEvent) for event in result.events)
        assert opened or escaped == [], f"escape events {escaped} with no encounter open before, during, or after"
        assert len(set(escaped)) == len(escaped), f"a group escaped twice: {escaped}"
        return result
    if session.encounter is not None:
        known = {group.id for group in groups}
        groups += [group for group in session.encounter.groups if group.id not in known]
    newly_fled = sorted(group.id for group in groups if group.fled and group.id not in already_fled)
    assert escaped == newly_fled, f"escape events {escaped}, groups that got away {newly_fled}"
    return result


def hold_round(session: GameSession, *extra: BattleDeclaration) -> CommandResult:
    return execute_checked(session, ResolveBattleRound(declarations=hold_all(session, extra=extra)))


def escapes(events) -> list[MonsterEscapedEvent]:
    return [event for event in events if isinstance(event, MonsterEscapedEvent)]


def index_of(events, predicate) -> int:
    return next(index for index, event in enumerate(events) if predicate(event))


def assert_escape_follows_move(events, group_id: str, distance_feet: int) -> int:
    """The group's one escape comes right after its move to `distance_feet`. Return the escape's index."""
    (escape,) = escapes(events)
    assert escape.group_id == group_id
    position = events.index(escape)
    moved = events[position - 1]
    assert isinstance(moved, GroupMovedEvent), type(moved).__name__
    assert (moved.group_id, moved.distance_feet) == (group_id, distance_feet)
    return position


def assert_escape_follows_flight(events, group_id: str) -> int:
    """The group's one escape comes right after its `MonsterFledEvent`. Return the escape's index."""
    (escape,) = escapes(events)
    assert escape.group_id == group_id
    position = events.index(escape)
    fled = events[position - 1]
    assert isinstance(fled, MonsterFledEvent), type(fled).__name__
    assert fled.group_id == group_id
    return position


def view_fled(session: GameSession, group_id: str) -> bool:
    view = session.view(Visibility.PLAYER)
    assert isinstance(view, PlayerView) and view.encounter is not None
    return next(group for group in view.encounter.groups if group.id == group_id).fled


def craven_exploring(seed: int) -> GameSession:
    """The party in the delve with a lit torch, and an adventure that bundles a goblin of morale 2."""
    craven = (
        load_monsters().get("goblin").model_copy(update={"id": "craven_goblin", "name": "Craven Goblin", "morale": 2})
    )
    adventure = build_adventure(wandering_chance=0).model_copy(update={"monsters": (craven,)})
    session = GameSession.new(build_party(), adventure, seed=seed)
    for command in (
        GrantItem(character_id=FIGHTER, item_id="torch", quantity=6),
        GrantItem(character_id=FIGHTER, item_id="tinder_box"),
        EnterDungeon(dungeon_id="delve"),
    ):
        assert session.execute(command).accepted, command.command_type
    for _ in range(20):
        result = session.execute(LightSource(character_id=FIGHTER, item_id="torch"))
        if any(event.code == "exploration.light.lit" for event in result.events):
            break
    return session


def battle_codes(events) -> list[str]:
    return [event.code for event in events if event.code.startswith("battle.")]


# ---------------------------------------------------------------------- the event and its line


def test_the_escape_is_its_own_event_type_with_one_code():
    """A new type, so a client that matches `monster_fled` never prints "breaks and runs" twice for one group."""
    event = MonsterEscapedEvent(group_id="group-0001")

    assert event.event_type == "monster_escaped"
    assert event.code == "battle.side.escaped"
    assert MonsterEscapedEvent.allowed_codes == frozenset({"battle.side.escaped"})
    assert event.visibility is Visibility.PLAYER
    assert set(MonsterEscapedEvent.model_fields) - set(Event.model_fields) == {"event_type", "group_id"}
    assert "battle.side.escaped" not in MonsterFledEvent.allowed_codes
    assert parse_any_event(event.model_dump(mode="json")) == event


def test_the_escape_line_says_the_group_gets_away():
    assert format_message(MonsterEscapedEvent(group_id="group-0001")) == "group-0001 gets away."


# ---------------------------------------------------------------------- a group that runs gets away


def test_a_group_that_breaks_gets_away_in_a_later_round():
    """Seed 1: the fighter cuts at four goblins at melee range until they break, then everyone holds.

    The goblins break and run their 60' from 5' to 65', which is inside 120', so that round has no escape. The next
    round takes them to 125', past 120' from every member, and that round's result has the one escape, right after
    their move. No group is left fighting, so the battle ends after it.
    """
    session = battle_session(count=4, distance=MELEE_RANGE_FEET, seed=1)
    goblins = session.encounter.groups[0]
    broke = None
    for _ in range(10):
        declarers = session.view(Visibility.PLAYER).encounter.declarers
        attack = BattleDeclaration(character_id=FIGHTER, action="attack", target_group_id=goblins.id, weapon_id="sword")
        result = hold_round(session, *((attack,) if FIGHTER in declarers else ()))
        if any(isinstance(event, MonsterFledEvent) for event in result.events):
            broke = result
            break
    assert broke is not None, "precondition: the goblins broke"
    assert goblins.fleeing and not goblins.fled
    assert goblins.distance_feet == MELEE_RANGE_FEET + RUNNER_RATE_FEET <= FLEE_EXIT_FEET
    assert escapes(broke.events) == []

    result = hold_round(session)

    events = list(result.events)
    escape_at = assert_escape_follows_move(events, goblins.id, MELEE_RANGE_FEET + 2 * RUNNER_RATE_FEET)
    ended = index_of(events, lambda event: isinstance(event, BattleEndedEvent))
    assert escape_at < ended
    assert events[ended].code == "battle.ended.victory"


def test_an_escape_beside_a_group_still_fighting_leaves_the_battle_on():
    """Goblins running from 10' reach 70' after one round and 130' after two. Hobgoblins keep the battle going."""
    session = battle_session(count=2, distance=10)
    execute_checked(session, SpawnMonsters(template_id="hobgoblin", count_fixed=2, distance_feet=60))
    goblins = session.encounter.groups[0]
    goblins.fleeing = True

    first = hold_round(session)

    assert goblins.distance_feet == 10 + RUNNER_RATE_FEET <= FLEE_EXIT_FEET
    assert escapes(first.events) == []
    assert view_fled(session, goblins.id) is False

    second = hold_round(session)

    events = list(second.events)
    escape_at = assert_escape_follows_move(events, goblins.id, 10 + 2 * RUNNER_RATE_FEET)
    assert events[escape_at].visibility is Visibility.PLAYER
    assert format_message(events[escape_at]) == f"{goblins.id} gets away."
    assert view_fled(session, goblins.id) is True
    assert session.mode is SessionMode.BATTLE
    assert not any(isinstance(event, BattleEndedEvent) for event in events)

    third = hold_round(session)

    assert escapes(third.events) == []


def test_an_escape_that_ends_the_battle_comes_before_the_end():
    """The only group runs from 10'. At 130' it's gone, the battle ends in victory, and the encounter closes."""
    session = battle_session(count=2, distance=10)
    goblins = session.encounter.groups[0]
    goblins.fleeing = True
    assert escapes(hold_round(session).events) == []

    result = hold_round(session)

    events = list(result.events)
    escape_at = assert_escape_follows_move(events, goblins.id, 10 + 2 * RUNNER_RATE_FEET)
    ended = index_of(events, lambda event: isinstance(event, BattleEndedEvent))
    assert escape_at < ended
    assert events[ended].code == "battle.ended.victory"
    assert session.mode is SessionMode.EXPLORING


# ---------------------------------------------------------------------- a group that will not fight


def test_a_group_that_will_not_fight_gets_away_as_the_battle_opens():
    """Seed 1: morale 2 goblins meet the party, and `EngageBattle` opens a battle they leave at once."""
    session = craven_exploring(1)
    execute_checked(session, SpawnMonsters(template_id="craven_goblin", count_fixed=2, distance_feet=60))
    assert session.mode is SessionMode.ENCOUNTER, "precondition: the battle did not open at once"
    group_id = session.encounter.groups[0].id

    result = execute_checked(session, EngageBattle())

    events = list(result.events)
    escape_at = assert_escape_follows_flight(events, group_id)
    assert escape_at < index_of(events, lambda event: isinstance(event, BattleEndedEvent))
    assert battle_codes(events)[:4] == [
        "battle.started",
        "battle.side.fled",
        "battle.side.escaped",
        "battle.ended.victory",
    ]


def test_a_spawn_that_opens_the_battle_reports_the_escape():
    """Seed 2: the morale 2 goblins' reaction opens the battle inside `SpawnMonsters`, and they leave at once."""
    session = craven_exploring(2)

    result = execute_checked(session, SpawnMonsters(template_id="craven_goblin", count_fixed=2, distance_feet=60))

    events = list(result.events)
    assert "battle.started" in battle_codes(events), "precondition: the spawn opened the battle"
    fled = next(event for event in events if isinstance(event, MonsterFledEvent))
    escape_at = assert_escape_follows_flight(events, fled.group_id)
    assert escape_at < index_of(events, lambda event: isinstance(event, BattleEndedEvent))


def test_a_group_that_will_not_fight_gets_away_beside_one_that_does():
    """Seed 1: morale 2 goblins and ordinary goblins. The battle opens, the first group leaves, the second fights."""
    session = craven_exploring(1)
    execute_checked(session, SpawnMonsters(template_id="craven_goblin", count_fixed=2, distance_feet=60))
    execute_checked(session, SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=40))
    assert session.mode is SessionMode.ENCOUNTER, "precondition: the battle did not open at once"
    craven, fighters = session.encounter.groups

    result = execute_checked(session, EngageBattle())

    assert_escape_follows_flight(list(result.events), craven.id)
    assert session.mode is SessionMode.BATTLE
    assert view_fled(session, craven.id) is True
    assert view_fled(session, fighters.id) is False


def test_a_group_that_joins_a_battle_and_will_not_fight_gets_away_last():
    """A referee's spawn adds morale 2 goblins to a running battle. Their escape is the spawn's last event."""
    session = craven_session()

    result = execute_checked(session, SpawnMonsters(template_id="craven_goblin", count_fixed=2, distance_feet=10))

    events = list(result.events)
    joined = session.encounter.groups[-1]
    escape_at = assert_escape_follows_flight(events, joined.id)
    assert escape_at == len(events) - 1
    assert session.mode is SessionMode.BATTLE


# ---------------------------------------------------------------------- turned undead get away


@pytest.mark.parametrize("seed", turned_fixture.PARTIAL_SEEDS)
def test_turned_skeletons_that_split_off_get_away(seed):
    """A partial turning in the encounter splits the turned skeletons off as a new group that runs from 10'.

    The split is reported with `MonsterFledEvent` and no escape. The runners reach 70' and then 130', and the second
    round's result has their one escape, right after their move, while the unturned skeletons fight on.
    """
    session = turned_fixture.new_session(seed, turned_fixture.TRIO)
    cleric = turned_fixture.cleric_of(session)
    turning = execute_checked(session, TurnUndead(character_id=cleric.id))
    assert session.mode is SessionMode.BATTLE, "precondition: the unturned skeletons opened battle"
    runners = session.encounter.groups[-1]
    assert [event.group_id for event in turning.events if isinstance(event, MonsterFledEvent)] == [runners.id]
    assert escapes(turning.events) == []

    first = hold_round(session)
    assert escapes(first.events) == []
    second = hold_round(session)

    assert_escape_follows_move(list(second.events), runners.id, 10 + 2 * RUNNER_RATE_FEET)
    assert session.mode is SessionMode.BATTLE


def test_a_wholly_turned_group_gets_away_and_ends_the_battle():
    """A turning declared in round 1 turns all six skeletons. They run from 10' and are gone at 130' in round 2."""
    session = turned_fixture.new_session(turned_fixture.ALL_TURNED_BATTLE_SEED, turned_fixture.TRIO)
    execute_checked(session, EngageBattle())
    cleric = turned_fixture.cleric_of(session)
    skeletons = session.encounter.groups[0]
    turning = BattleDeclaration(character_id=cleric.id, action="turn_undead")
    first = hold_round(session, turning)
    assert turned_fixture.turned_ids(session) == set(session.monsters), "precondition: all six were turned"
    assert escapes(first.events) == []

    result = hold_round(session)

    events = list(result.events)
    escape_at = assert_escape_follows_move(events, skeletons.id, 10 + 2 * RUNNER_RATE_FEET)
    assert escape_at < index_of(events, lambda event: isinstance(event, BattleEndedEvent))


# ---------------------------------------------------------------------- a turned or afraid group past 120' (#297)

TURNED_SEED = turned_fixture.ALL_TURNED_BATTLE_SEED
"""Seed 6: the round 1 turning turns all six skeletons, whether they stand 70' or 130' away."""

AFRAID_FIRST_SEED = 14
"""Seed 14: the party acts first in the round the goblin is left past 120', so the battle ends before it runs."""

AFRAID_REFERENCE_SEED = 1
"""Seed 1: the goblin acts first in that round, runs past 120', and the monster block marks it fled."""

GROUP_COINS = Coins(gp=50)
MEMBER_COINS = Coins(sp=7)


def give_treasure(group: EncounterGroup) -> None:
    """Give the group a shared bundle and its first member a bundle of its own, so the endings can be compared."""
    group.group_treasure = TreasureBundle(coins=GROUP_COINS)
    group.member_treasure[group.monster_ids[0]] = TreasureBundle(coins=MEMBER_COINS)


def skeletons_at(distance_feet: int) -> tuple[GameSession, EncounterGroup]:
    """Six skeletons `distance_feet` away from a cleric and two fighters, with the battle open.

    Return the session and the skeletons' group, which carries a shared bundle and a bundle for its first member.
    """
    draw = RngStreams(master_seed=TURNED_SEED).get(CHARACTER_CREATION_STREAM)
    members = [
        create_character(
            name=f"Member {index}", class_id=class_id, alignment=Alignment.LAWFUL, ruleset=Ruleset(), stream=draw
        ).character
        for index, class_id in enumerate(turned_fixture.TRIO)
    ]
    level = LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)})
    adventure = Adventure(
        name="Turning",
        town=TownSpec(name="Town", travel_turns={"crypt": 1}),
        dungeons=(DungeonSpec(id="crypt", name="Crypt", levels=(level,)),),
    )
    session = GameSession.new(Party(members=members), adventure, seed=TURNED_SEED)
    execute_checked(session, EnterDungeon(dungeon_id="crypt"))
    execute_checked(session, SpawnMonsters(template_id="skeleton", count_fixed=6, distance_feet=distance_feet))
    assert session.mode is SessionMode.ENCOUNTER, "precondition: the skeletons did not open battle at once"
    execute_checked(session, EngageBattle())
    group = session.encounter.groups[0]
    give_treasure(group)
    return session, group


def turn_all(session: GameSession) -> CommandResult:
    """Round 1: the cleric turns, and the fighters hold. Assert the precondition that all six are turned."""
    cleric = turned_fixture.cleric_of(session)
    result = hold_round(session, BattleDeclaration(character_id=cleric.id, action="turn_undead"))
    # The encounter can close in this round and release the turned condition, so the turning is read from its event.
    (turning,) = [event for event in result.events if isinstance(event, UndeadTurnedEvent)]
    assert turning.code == "magic.turning.turned", "precondition: the turning succeeded"
    assert set(turning.affected_ids) == set(session.monsters), "precondition: all six were turned"
    return result


def afraid_goblin(seed: int) -> tuple[GameSession, EncounterGroup, CommandResult]:
    """A goblin 60' away, made afraid by a wand of fear, that the party leaves behind by falling back.

    In round 1 the goblin closes to 40' and the magic-user's wand makes it afraid. From round 2 every member makes a
    fighting withdrawal each round, and the goblin runs from them, until the battle ends. Return the session, the
    goblin's group, and the result of the round that ended the battle.
    """
    session = battle_session(count=1, distance=60, seed=seed)
    session.member(MAGIC_USER).inventory.items.append(
        MagicItemInstance(
            instance_id="magic-item-9001", template_id="wand_of_fear", identified=True, charges_remaining=5
        )
    )
    group = session.encounter.groups[0]
    give_treasure(group)
    wand = BattleDeclaration(
        character_id=MAGIC_USER, action="use_item", item_id="magic-item-9001", target_group_id=group.id
    )
    hold_round(session, wand)
    (goblin_id,) = group.monster_ids
    assert has_condition(session.combatant(goblin_id), Condition.AFRAID), "precondition: the goblin is afraid"
    for _ in range(5):
        withdrawal = tuple(
            BattleDeclaration(character_id=member.id, action="move", move="fighting_withdrawal")
            for member in session.party.living_members()
        )
        result = execute_checked(session, ResolveBattleRound(declarations=withdrawal))
        if session.mode is not SessionMode.BATTLE:
            return session, group, result
    pytest.fail("the battle did not end")


def acted_first(events) -> str:
    return next(event for event in events if isinstance(event, InitiativeRolledEvent)).order[0]


def test_a_group_turned_past_the_exit_gets_away_as_the_battle_ends():
    """Six skeletons 130' away, all turned in round 1, are past 120' without a move, and the battle ends at once.

    The session marks the group fled and emits its escape right before the `BattleEndedEvent`.
    """
    session, skeletons = skeletons_at(130)

    events = list(turn_all(session).events)

    assert session.mode is SessionMode.EXPLORING
    assert skeletons.distance_feet == 130
    assert skeletons.fled is True
    (escape,) = escapes(events)
    assert escape.group_id == skeletons.id
    ended = index_of(events, lambda event: isinstance(event, BattleEndedEvent))
    assert events.index(escape) == ended - 1
    assert events[ended].code == "battle.ended.victory"


def test_a_group_turned_past_the_exit_ends_as_one_that_runs_out():
    """The same six skeletons turned at 70' run to 130' in round 1 and are marked fled by the monster block.

    Both endings give each skeleton the `routed` outcome and its 10 XP, and the group keeps its shared bundle and the
    member its own, because turned monsters flee with their treasure. Nothing drops.
    """
    near, near_group = skeletons_at(70)
    near_events = list(turn_all(near).events)
    assert near.mode is SessionMode.EXPLORING and near_group.fled, "precondition: the monster block marked it fled"
    far, far_group = skeletons_at(130)
    far_events = list(turn_all(far).events)

    near_ending = ending(near, near_group, near_events)
    assert ending(far, far_group, far_events) == near_ending
    assert near_ending["records"] == [(monster_id, "routed", 10) for monster_id in near_group.monster_ids]
    assert near_ending["group_treasure"] == TreasureBundle(coins=GROUP_COINS)
    assert near_ending["piles"] == {}


def test_an_afraid_group_left_past_the_exit_gets_away_as_the_battle_ends():
    """Seed 14: the party falls back first, the afraid goblin is past 120' before it runs, and the battle ends.

    The session marks the group fled and emits its escape right before the `BattleEndedEvent`, and the goblin is
    routed and worth its 5 XP, as an afraid goblin that runs out is.
    """
    session, group, result = afraid_goblin(AFRAID_FIRST_SEED)

    events = list(result.events)
    assert acted_first(events) != group.id, "precondition: the party acted first in the last round"
    assert group.fled is True
    (escape,) = escapes(events)
    assert escape.group_id == group.id
    ended = index_of(events, lambda event: isinstance(event, BattleEndedEvent))
    assert events.index(escape) == ended - 1
    (goblin_id,) = group.monster_ids
    assert ending(session, group, events)["defeated"] == [(goblin_id, "routed", 5)]


def test_an_afraid_group_left_past_the_exit_ends_as_one_that_runs_out():
    """Seed 1 is the reference: the goblin runs first, past 120', and the monster block marks it fled.

    Both endings route the goblin for its 5 XP, and in both the group keeps its treasure and nothing drops.
    """
    reference, reference_group, reference_result = afraid_goblin(AFRAID_REFERENCE_SEED)
    reference_events = list(reference_result.events)
    assert acted_first(reference_events) == reference_group.id, "precondition: the goblin acted first"
    assert_escape_follows_move(reference_events, reference_group.id, reference_group.distance_feet)
    session, group, result = afraid_goblin(AFRAID_FIRST_SEED)

    expected = ending(reference, reference_group, reference_events)
    assert ending(session, group, list(result.events)) == expected
    assert expected["records"] == [(reference_group.monster_ids[0], "routed", 5)]
    assert expected["group_treasure"] == TreasureBundle(coins=GROUP_COINS)
    assert expected["piles"] == {}


def ending(session: GameSession, group: EncounterGroup, events) -> dict:
    """What the end of the encounter did with the group: outcomes, experience, and treasure."""
    view = session.view(Visibility.PLAYER)
    assert isinstance(view, PlayerView)
    return {
        "records": [(record.monster_id, record.outcome, record.xp) for record in session.defeated_monsters],
        "defeated": [
            (event.monster_id, event.outcome, event.xp) for event in events if isinstance(event, MonsterDefeatedEvent)
        ],
        "encounter_outcome": [event.outcome for event in events if isinstance(event, EncounterEndedEvent)],
        "group_treasure": group.group_treasure,
        "member_treasure": dict(group.member_treasure),
        "piles": view.piles,
    }


# ---------------------------------------------------------------------- the invariant across seeds


def test_escapes_match_the_groups_that_get_away_across_seeds():
    """Twelve seeds of the fighter against four goblins at melee range, checked on every round.

    The event takes no draw, so the seeds whose goblins break and get away are the ones they are without the event.
    """
    got_away = []
    for seed in range(1, 13):
        session = battle_session(count=4, distance=MELEE_RANGE_FEET, seed=seed)
        goblins = session.encounter.groups[0]
        for _ in range(15):
            if session.mode is not SessionMode.BATTLE:
                break
            declarers = session.view(Visibility.PLAYER).encounter.declarers
            reach = goblins.distance_feet <= MELEE_RANGE_FEET and not goblins.fleeing and FIGHTER in declarers
            attack = BattleDeclaration(
                character_id=FIGHTER, action="attack", target_group_id=goblins.id, weapon_id="sword"
            )
            result = hold_round(session, *((attack,) if reach else ()))
            if escapes(result.events):
                got_away.append(seed)

    assert got_away == [1, 5, 10, 12]


# ---------------------------------------------------------------------- the reference names the event


def attribute_docstring(path: Path, owner: str | None, name: str) -> str:
    """The docstring that follows an assignment to `name`, at module level or in the class `owner`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    body = tree.body
    if owner is not None:
        body = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == owner).body
    for node, following in zip(body, body[1:], strict=False):
        target = node.target if isinstance(node, ast.AnnAssign) else getattr(node, "targets", [None])[0]
        if isinstance(target, ast.Name) and target.id == name and isinstance(following, ast.Expr):
            return following.value.value
    raise AssertionError(f"no docstring for {owner}.{name} in {path}")


REFERENCE_DOCSTRINGS = {
    "FLEE_EXIT_FEET": lambda: attribute_docstring(SRC / "crawl" / "battle.py", None, "FLEE_EXIT_FEET"),
    "EncounterGroup.fled": lambda: attribute_docstring(SRC / "crawl" / "encounter.py", "EncounterGroup", "fled"),
    "EncounterGroupView.fled": lambda: attribute_docstring(SRC / "crawl" / "views.py", "EncounterGroupView", "fled"),
    "MonsterFledEvent": lambda: inspect.getdoc(MonsterFledEvent),
    "osrlib.crawl.battle": lambda: inspect.getdoc(battle_module),
    "EngageBattle": lambda: inspect.getdoc(commands_module.EngageBattle),
    "ResolveBattleRound": lambda: inspect.getdoc(commands_module.ResolveBattleRound),
    "SpawnMonsters": lambda: inspect.getdoc(commands_module.SpawnMonsters),
}
"""Each docstring that describes how a group leaves the battle, or lists the events a command emits when one does."""


@pytest.mark.parametrize("label", list(REFERENCE_DOCSTRINGS))
def test_the_reference_for_the_later_round_names_the_escape(label):
    assert "MonsterEscapedEvent" in (REFERENCE_DOCSTRINGS[label]() or "")
