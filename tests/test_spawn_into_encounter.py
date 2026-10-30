"""Acceptance tests for SpawnMonsters into an open encounter or battle (issue #170, issue #167 item 9).

Several SRD monsters summon allies in the middle of a fight, and the referee resolves the summons
with `SpawnMonsters`. While an encounter or a battle is open, the command adds one new group to it
at the requested distance. The group joins between rounds: the spawn runs no round, rolls no new
surprise or reaction, keeps the encounter's stance and the battle's round, and leaves the groups
already there as they were. From the next battle round on, the new group is a monster-side group
like any other, so the party can attack it and its members attack the party. In exploration the
command opens a new encounter, as before.
"""

import json

import pytest
from pydantic import ValidationError

from crawl_fixtures import build_adventure, build_party
from osrlib.core.character import party_to_document
from osrlib.core.events import AttackRolledEvent, Visibility
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.commands import (
    BattleDeclaration,
    EngageBattle,
    EnterDungeon,
    EquipItem,
    GrantItem,
    LightSource,
    ResolveBattleRound,
    SessionMode,
    SpawnMonsters,
)
from osrlib.crawl.events import MonstersSpawnedEvent
from osrlib.crawl.session import GameSession
from osrlib.errors import ContentValidationError
from osrlib.persistence import load_game, replay_game, save_game, session_state

CHUNK = pytest.mark.xfail(reason="chunk: spawn-into-encounter", strict=True)

SEED = 5
"""With this seed, the opening spawn leaves an indifferent encounter and a party that wasn't surprised.

The party carries a lit torch, so the goblins roll no surprise, and an indifferent stance never turns
into a battle by itself. `TestFixtures` pins both facts, so a change to the seed's trajectory fails
there, not inside an expected failure.
"""

FIGHTER = "character-0001"


def start_session(seed: int = SEED) -> GameSession:
    session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=seed)
    setup = (
        GrantItem(character_id=FIGHTER, item_id="torch", quantity=6),
        GrantItem(character_id=FIGHTER, item_id="tinder_box"),
        GrantItem(character_id=FIGHTER, item_id="sword"),
        EquipItem(character_id=FIGHTER, item_id="sword"),
        EnterDungeon(dungeon_id="delve"),
    )
    for command in setup:
        assert session.execute(command).accepted, command.command_type
    for _ in range(20):
        result = session.execute(LightSource(character_id=FIGHTER, item_id="torch"))
        if any(event.code == "exploration.light.lit" for event in result.events):
            break
    return session


def in_encounter(seed: int = SEED) -> GameSession:
    """A session in an open encounter with two goblins at 60 feet, every step a logged command."""
    session = start_session(seed)
    session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=60))
    return session


def in_battle(seed: int = SEED) -> GameSession:
    """The same encounter after the party chose to fight, so the session is in battle at round 0."""
    session = in_encounter(seed)
    session.execute(EngageBattle())
    return session


def snapshot(session: GameSession) -> dict:
    return json.loads(json.dumps(session_state(session)))


def hold_all(session: GameSession, *extra: BattleDeclaration) -> tuple[BattleDeclaration, ...]:
    covered = {declaration.character_id for declaration in extra}
    holds = tuple(
        BattleDeclaration(character_id=member.id, action="hold")
        for member in session.party.living_members()
        if member.id not in covered
    )
    return (*extra, *holds)


def attacks(events) -> list[AttackRolledEvent]:
    return [event for event in events if isinstance(event, AttackRolledEvent)]


def replay(session: GameSession, seed: int = SEED) -> GameSession:
    party_document = party_to_document(build_party().members)
    return replay_game(seed, party_document, build_adventure(wandering_chance=0), Ruleset(), session.command_log)


class TestFixtures:
    """The setup the marked tests stand on. These pass before and after the chunk."""

    def test_the_opening_spawn_leaves_a_quiet_encounter(self):
        session = in_encounter()
        assert session.mode is SessionMode.ENCOUNTER
        assert session.encounter.stance == "indifferent"
        assert not session.encounter.party_surprised
        assert session.encounter.monsters_skip_rounds == 0
        assert [len(group.monster_ids) for group in session.encounter.groups] == [2]

    def test_engaging_opens_a_battle_with_no_free_round(self):
        session = in_battle()
        assert session.mode is SessionMode.BATTLE
        assert session.battle.round == 0
        assert session.battle.monsters_hold_rounds == 0


class TestSpawnInExplorationIsUnchanged:
    def test_a_spawn_in_exploration_opens_a_new_encounter(self):
        session = start_session()
        assert session.mode is SessionMode.EXPLORING
        result = session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=60))
        assert result.accepted
        codes = [event.code for event in result.events]
        assert codes[0] == "session.monsters.spawned"
        assert "encounter.started" in codes
        (group,) = session.encounter.groups
        assert session.encounter.kind == "spawned"
        assert group.label == "goblin"
        assert group.distance_feet == 60
        assert tuple(group.monster_ids) == result.events[0].monster_ids
        assert session.command_log[-1] == SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=60)


class TestValidationStaysPure:
    """A refused spawn in an open encounter or battle costs no draws, no time, and no state."""

    @pytest.mark.parametrize("opened", [in_encounter, in_battle], ids=["encounter", "battle"])
    def test_an_unknown_template_is_refused_with_nothing_changed(self, opened):
        session = opened()
        before = snapshot(session)
        result = session.execute(SpawnMonsters(template_id="gazebo", count_fixed=1, distance_feet=30))
        assert not result.accepted
        assert [rejection.code for rejection in result.rejections] == ["session.command.unknown_monster"]
        assert result.events == ()
        assert snapshot(session) == before

    @pytest.mark.parametrize(
        "fields",
        [
            {"count_fixed": 0},
            {"count_fixed": -2},
            {"count_dice": "garbage"},
            {"count_dice": "1d4", "count_fixed": 2},
            {},
        ],
        ids=["zero", "negative", "malformed-dice", "both-counts", "no-count"],
    )
    def test_a_bad_count_never_reaches_the_session(self, fields):
        session = in_encounter()
        before = snapshot(session)
        with pytest.raises((ValidationError, ContentValidationError)):
            SpawnMonsters(template_id="goblin", distance_feet=30, **fields)
        assert snapshot(session) == before


@CHUNK
class TestSpawnInEncounter:
    def test_the_spawn_is_accepted_and_adds_a_group_at_the_distance(self):
        session = in_encounter()
        (original,) = session.encounter.groups
        original_before = original.model_dump()
        result = session.execute(SpawnMonsters(template_id="normal_wolf", count_fixed=3, distance_feet=30))
        assert result.accepted
        assert session.mode is SessionMode.ENCOUNTER
        assert len(session.encounter.groups) == 2
        first, added = session.encounter.groups
        assert first.model_dump() == original_before
        assert added.id != first.id
        assert added.label == "normal_wolf"
        assert len(added.monster_ids) == 3
        assert added.distance_feet == 30
        assert not added.fleeing and not added.fled
        for monster_id in added.monster_ids:
            assert session.combatant(monster_id).template.id == "normal_wolf"

    def test_the_encounter_is_joined_not_reopened(self):
        session = in_encounter()
        rounds_before = session.clock.rounds
        encounter_before = session.encounter.model_dump(exclude={"groups"})
        result = session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=30))
        assert result.accepted
        assert session.encounter.model_dump(exclude={"groups"}) == encounter_before
        assert session.clock.rounds == rounds_before
        assert session.battle is None
        codes = [event.code for event in result.events]
        assert "encounter.started" not in codes
        assert "battle.started" not in codes

    def test_the_spawn_emits_the_spawned_event_for_the_new_group(self):
        session = in_encounter()
        command = SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=30)
        result = session.execute(command)
        assert result.accepted
        added = session.encounter.groups[-1]
        spawned = [event for event in result.events if isinstance(event, MonstersSpawnedEvent)]
        assert len(spawned) == 1
        assert spawned[0].template_id == "goblin"
        assert spawned[0].monster_ids == tuple(added.monster_ids)
        assert spawned[0] in session.event_log
        assert session.command_log[-1] == command

    def test_a_rolled_count_sizes_the_new_group(self):
        session = in_encounter()
        result = session.execute(SpawnMonsters(template_id="goblin", count_dice="1d4", distance_feet=30))
        assert result.accepted
        added = session.encounter.groups[-1]
        (spawned,) = [event for event in result.events if isinstance(event, MonstersSpawnedEvent)]
        assert 1 <= len(added.monster_ids) <= 4
        assert spawned.monster_ids == tuple(added.monster_ids)

    def test_both_views_show_the_new_group(self):
        session = in_encounter()
        assert session.execute(SpawnMonsters(template_id="goblin", count_fixed=3, distance_feet=30)).accepted
        added = session.encounter.groups[-1]
        group_ids = [group.id for group in session.encounter.groups]
        player = session.view(Visibility.PLAYER)
        assert [group.id for group in player.encounter.groups] == group_ids
        shown = player.encounter.groups[-1]
        assert (shown.id, shown.label, shown.count, shown.distance_feet) == (added.id, "goblin", 3, 30)
        referee = session.view(Visibility.REFEREE)
        assert [group.id for group in referee.encounter.groups] == group_ids
        assert referee.encounter.groups[-1].monster_ids == added.monster_ids
        assert {monster.id for monster in referee.monsters} >= set(added.monster_ids)

    def test_a_group_spawned_in_the_encounter_fights_when_battle_opens(self):
        session = in_encounter()
        assert session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=5)).accepted
        added = session.encounter.groups[-1]
        assert session.execute(EngageBattle()).accepted
        assert session.mode is SessionMode.BATTLE
        result = session.execute(ResolveBattleRound(declarations=hold_all(session)))
        assert result.accepted
        attackers = {event.attacker_id for event in attacks(result.events)}
        assert attackers & set(added.monster_ids)


@CHUNK
class TestSpawnInBattle:
    def test_the_spawn_is_accepted_and_adds_a_group_without_running_a_round(self):
        session = in_battle()
        (original,) = session.encounter.groups
        original_before = original.model_dump()
        battle_before = session.battle.model_dump()
        rounds_before = session.clock.rounds
        result = session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=5))
        assert result.accepted
        assert session.mode is SessionMode.BATTLE
        first, added = session.encounter.groups
        assert first.model_dump() == original_before
        assert len(added.monster_ids) == 2
        assert added.distance_feet == 5
        # The group joins between rounds: the spawn itself resolves nothing.
        assert session.battle.model_dump() == battle_before
        assert session.clock.rounds == rounds_before
        assert attacks(result.events) == []
        codes = [event.code for event in result.events]
        assert "battle.started" not in codes
        assert "battle.round.started" not in codes
        (spawned,) = [event for event in result.events if isinstance(event, MonstersSpawnedEvent)]
        assert spawned.monster_ids == tuple(added.monster_ids)

    def test_the_party_can_attack_the_new_group(self):
        session = in_battle()
        assert session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=5)).accepted
        added = session.encounter.groups[-1]
        swing = BattleDeclaration(character_id=FIGHTER, action="attack", target_group_id=added.id, weapon_id="sword")
        result = session.execute(ResolveBattleRound(declarations=hold_all(session, swing)))
        assert result.accepted, [rejection.code for rejection in result.rejections]
        fighter_swings = [event for event in attacks(result.events) if event.attacker_id == FIGHTER]
        assert fighter_swings
        assert {event.defender_id for event in fighter_swings} <= set(added.monster_ids)

    def test_the_new_group_attacks_on_the_monster_side(self):
        session = in_battle()
        assert session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=5)).accepted
        added = session.encounter.groups[-1]
        party_ids = {member.id for member in session.party.members}
        result = session.execute(ResolveBattleRound(declarations=hold_all(session)))
        assert result.accepted
        theirs = [event for event in attacks(result.events) if event.attacker_id in added.monster_ids]
        assert theirs
        assert {event.defender_id for event in theirs} <= party_ids

    def test_a_distant_new_group_closes_on_the_party(self):
        session = in_battle()
        assert session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=60)).accepted
        assert len(session.encounter.groups) == 2
        added = session.encounter.groups[-1]
        result = session.execute(ResolveBattleRound(declarations=hold_all(session)))
        assert result.accepted
        # A goblin moves 60 feet a turn, so 20 feet a round.
        assert added.distance_feet == 40


@CHUNK
class TestSaveLoadAndReplay:
    def test_a_save_after_a_mid_encounter_spawn_loads_and_continues_identically(self):
        session = in_encounter()
        assert session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=5)).accepted
        restored = load_game(json.loads(json.dumps(save_game(session))))
        assert session_state(restored) == session_state(session)
        for game in (session, restored):
            assert game.execute(EngageBattle()).accepted
            assert game.execute(ResolveBattleRound(declarations=hold_all(game))).accepted
        assert session_state(restored) == session_state(session)

    def test_a_replay_reproduces_a_mid_encounter_spawn(self):
        session = in_encounter()
        assert session.execute(SpawnMonsters(template_id="goblin", count_dice="1d4", distance_feet=30)).accepted
        replayed = replay(session)
        assert len(replayed.encounter.groups) == 2
        assert session_state(replayed) == session_state(session)
        assert replayed.view(Visibility.REFEREE) == session.view(Visibility.REFEREE)

    def test_a_replay_reproduces_a_mid_battle_spawn_and_the_round_after(self):
        session = in_battle()
        assert session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=5)).accepted
        assert session.execute(ResolveBattleRound(declarations=hold_all(session))).accepted
        replayed = replay(session)
        assert session_state(replayed) == session_state(session)
        assert replayed.view(Visibility.REFEREE) == session.view(Visibility.REFEREE)
