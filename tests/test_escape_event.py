"""Acceptance tests for issue 292: an event marks the command in which a monster group gets away.

A group that fails morale emits `MonsterFledEvent` and keeps running. It leaves the battle once it stands farther than
`FLEE_EXIT_FEET` (120') from every living party member, and `EncounterGroup.fled` becomes true. A group whose morale
score is 2 leaves as it enters the battle, because the OSE SRD's morale page says "A score of 2: Means the monster will
not fight." Those are the only two places `fled` becomes true, and the session emits one `MonsterEscapedEvent`
(`monster_escaped`, `battle.side.escaped`) for the group in the same command result, and never otherwise:

- In the monster block, the event comes right after the running group's `GroupMovedEvent`.
- When the group leaves as it enters the battle, the event comes right after its `MonsterFledEvent`.
- When the escape ends the battle, the event comes before the `BattleEndedEvent`.

The event is additive within the current `schema_version`, and it takes no draw, no clock time, and changes no state.

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
from osrlib.core.events import Event, Visibility
from osrlib.crawl import battle as battle_module
from osrlib.crawl import commands as commands_module
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
from osrlib.crawl.events import (
    BattleEndedEvent,
    GroupMovedEvent,
    MonsterEscapedEvent,
    MonsterFledEvent,
    parse_any_event,
)
from osrlib.crawl.session import GameSession, SessionMode
from osrlib.crawl.views import PlayerView
from osrlib.data import load_monsters
from osrlib.messages import format_message
from test_battle import battle_session, hold_all
from test_spawn_into_encounter import craven_session

CHUNK = "chunk: escape-event"
FIGHTER = "character-0001"
RUNNER_RATE_FEET = 60
"""The movement rate of a goblin and of a skeleton, which a running group of either covers each round."""

SRC = Path(__file__).resolve().parent.parent / "src" / "osrlib"


# ---------------------------------------------------------------------- helpers


def execute_checked(session: GameSession, command: Command) -> CommandResult:
    """Execute an accepted command and check that its escape events match the groups whose `fled` turned true."""
    groups = list(session.encounter.groups) if session.encounter is not None else []
    already_fled = {group.id for group in groups if group.fled}
    result = session.execute(command)
    assert result.accepted, [rejection.code for rejection in result.rejections]
    if session.encounter is not None:
        known = {group.id for group in groups}
        groups += [group for group in session.encounter.groups if group.id not in known]
    newly_fled = sorted(group.id for group in groups if group.fled and group.id not in already_fled)
    escaped = sorted(event.group_id for event in result.events if isinstance(event, MonsterEscapedEvent))
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


@pytest.mark.xfail(reason=CHUNK)
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


@pytest.mark.xfail(reason=CHUNK)
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


@pytest.mark.xfail(reason=CHUNK)
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


@pytest.mark.xfail(reason=CHUNK)
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


@pytest.mark.xfail(reason=CHUNK)
def test_a_spawn_that_opens_the_battle_reports_the_escape():
    """Seed 2: the morale 2 goblins' reaction opens the battle inside `SpawnMonsters`, and they leave at once."""
    session = craven_exploring(2)

    result = execute_checked(session, SpawnMonsters(template_id="craven_goblin", count_fixed=2, distance_feet=60))

    events = list(result.events)
    assert "battle.started" in battle_codes(events), "precondition: the spawn opened the battle"
    fled = next(event for event in events if isinstance(event, MonsterFledEvent))
    escape_at = assert_escape_follows_flight(events, fled.group_id)
    assert escape_at < index_of(events, lambda event: isinstance(event, BattleEndedEvent))


@pytest.mark.xfail(reason=CHUNK)
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


@pytest.mark.xfail(reason=CHUNK)
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


@pytest.mark.xfail(reason=CHUNK)
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


@pytest.mark.xfail(reason=CHUNK)
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


# ---------------------------------------------------------------------- the invariant across seeds


@pytest.mark.xfail(reason=CHUNK)
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


@pytest.mark.xfail(reason=CHUNK)
@pytest.mark.parametrize("label", list(REFERENCE_DOCSTRINGS))
def test_the_reference_for_the_later_round_names_the_escape(label):
    assert "MonsterEscapedEvent" in (REFERENCE_DOCSTRINGS[label]() or "")
