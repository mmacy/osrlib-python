"""Acceptance tests for issue 306: an encounter that ends routes a group whose living members are all turned or afraid.

A group whose living members are all turned or afraid retreats as a whole while the battle runs, and since #297 a battle
that ends with such a group past 120' marks it fled. When the encounter ends while such a group is still present, each
of its living members ends `routed`, as a turned member and a member of a group that failed morale do. Each one gets a
`MonsterDefeatedEvent` and a record in `session.defeated_monsters` with its experience. The rule is the same however the
encounter ends: the tests close most encounters with the referee's `EndEncounter`, and one with an escape from a chase.
An afraid member of a group that still has a living member who is neither turned nor afraid gets no outcome.
"""

from osrlib.core.effects import Condition, grant_condition, has_condition
from osrlib.core.events import ConditionGainedEvent, Visibility
from osrlib.core.items import MagicItemInstance
from osrlib.core.tables import ReactionResult
from osrlib.crawl.battle import MELEE_RANGE_FEET
from osrlib.crawl.commands import BattleDeclaration, EndEncounter, Evade, ResolveBattleRound, UseItem, Wait
from osrlib.crawl.encounter import EncounterGroup
from osrlib.crawl.events import EncounterEndedEvent, MonsterDefeatedEvent
from osrlib.crawl.session import GameSession, SessionMode
from test_battle import battle_session, hold_all
from test_encounter import quiet_session, start

FIGHTER = "character-0001"
CLERIC = "character-0003"
MAGIC_USER = "character-0004"
WAND_ID = "magic-item-9001"
GOBLIN_XP = 5
ORC_XP = 10
SKELETON_XP = 10

MIXED_SKELETONS_SEED = 208
"""Seed 208: the wand of fear frightens three of four skeletons, and the cleric's turning then turns three of them."""


def give_wand(session: GameSession, template_id: str = "wand_of_fear") -> None:
    session.member(MAGIC_USER).inventory.items.append(
        MagicItemInstance(instance_id=WAND_ID, template_id=template_id, identified=True, charges_remaining=5)
    )


def use_wand(group: EncounterGroup) -> BattleDeclaration:
    return BattleDeclaration(character_id=MAGIC_USER, action="use_item", item_id=WAND_ID, target_group_id=group.id)


def hold(session: GameSession, *extra: BattleDeclaration) -> None:
    result = session.execute(ResolveBattleRound(declarations=hold_all(session, extra=extra)))
    assert result.accepted, result.rejections


def defeats(events) -> list[tuple[str, str, int]]:
    return [(event.monster_id, event.outcome, event.xp) for event in events if isinstance(event, MonsterDefeatedEvent)]


def depart(session: GameSession) -> list[tuple[str, str, int]]:
    """Send `EndEncounter(outcome="departed")` and return each `MonsterDefeatedEvent` as `(id, outcome, xp)`."""
    result = session.execute(EndEncounter(outcome="departed"))
    assert result.accepted, result.rejections
    assert session.encounter is None and session.mode is SessionMode.EXPLORING
    defeated = defeats(result.events)
    records = [(record.monster_id, record.outcome, record.xp) for record in session.defeated_monsters]
    assert defeated == records, "every defeat the command reports is on record for the award"
    return defeated


def conditions(session: GameSession, monster_id: str) -> set[Condition]:
    return {
        condition
        for condition in (Condition.AFRAID, Condition.TURNED)
        if has_condition(session.combatant(monster_id), condition)
    }


def test_a_wholly_afraid_group_is_routed_by_end_encounter():
    """A goblin made afraid by a wand of fear in round 1 (seed 14) is still in the battle at 40' when the round ends."""
    session = battle_session(count=1, distance=60, seed=14)
    give_wand(session)
    group = session.encounter.groups[0]
    hold(session, use_wand(group))
    (goblin_id,) = group.monster_ids
    assert session.mode is SessionMode.BATTLE, "precondition: the battle goes on"
    assert conditions(session, goblin_id) == {Condition.AFRAID}, "precondition: the goblin is afraid"
    assert not (group.fleeing or group.fled), "precondition: the goblin runs because it's afraid, not from morale"

    assert depart(session) == [(goblin_id, "routed", GOBLIN_XP)]


def test_a_wholly_turned_group_is_routed_by_end_encounter():
    """Four skeletons, all turned in round 1 (seed 6), are running and still in the battle."""
    session = battle_session(template_id="skeleton", count=4, distance=10, seed=6)
    group = session.encounter.groups[0]
    hold(session, BattleDeclaration(character_id=CLERIC, action="turn_undead"))
    assert session.mode is SessionMode.BATTLE and not group.fled, "precondition: the skeletons are still in the battle"
    assert all(conditions(session, monster_id) == {Condition.TURNED} for monster_id in group.monster_ids)

    assert depart(session) == [(monster_id, "routed", SKELETON_XP) for monster_id in group.monster_ids]


def test_a_group_that_failed_morale_is_routed_by_end_encounter():
    """Four goblins at melee range break under the fighter's sword (seed 1) and still run when the battle stops."""
    session = battle_session(count=4, distance=MELEE_RANGE_FEET, seed=1)
    group = session.encounter.groups[0]
    for _ in range(10):
        declarers = session.view(Visibility.PLAYER).encounter.declarers
        attack = BattleDeclaration(character_id=FIGHTER, action="attack", target_group_id=group.id, weapon_id="sword")
        hold(session, *((attack,) if FIGHTER in declarers else ()))
        if group.fleeing:
            break
    assert session.mode is SessionMode.BATTLE and group.fleeing and not group.fled, "precondition: still running"
    slain = [m for m in group.monster_ids if has_condition(session.combatant(m), Condition.DEAD)]

    assert depart(session) == [
        (monster_id, "slain" if monster_id in slain else "routed", GOBLIN_XP) for monster_id in group.monster_ids
    ]


def test_a_group_of_turned_and_afraid_members_is_routed_together():
    """Four skeletons: a wand of fear frightens three, and then the cleric turns three, so one is only afraid."""
    session = battle_session(template_id="skeleton", count=4, distance=60, seed=MIXED_SKELETONS_SEED)
    give_wand(session)
    group = session.encounter.groups[0]
    hold(session, use_wand(group))
    hold(session, BattleDeclaration(character_id=CLERIC, action="turn_undead"))
    assert session.mode is SessionMode.BATTLE and len(session.encounter.groups) == 1, "precondition: one group"
    mix = [conditions(session, monster_id) for monster_id in group.monster_ids]
    assert all(mix) and {Condition.AFRAID} in mix and {Condition.TURNED} in mix, "precondition: afraid and turned"
    assert not (group.fleeing or group.fled)

    assert depart(session) == [(monster_id, "routed", SKELETON_XP) for monster_id in group.monster_ids]


def test_an_afraid_member_beside_an_unshaken_one_is_not_routed():
    """The group still has a goblin who is neither turned nor afraid, so it doesn't run as a whole, and nobody routs."""
    session = battle_session(count=2, distance=60, seed=5)
    group = session.encounter.groups[0]
    grant_condition(session.combatant(group.monster_ids[0]), Condition.AFRAID, None)

    assert depart(session) == []


def test_a_wholly_afraid_group_is_routed_when_the_party_escapes_a_chase():
    """Two hostile orcs chase the party, and a wand of fear frightens both (seed 1). The party gets away."""
    session = quiet_session(seed=1)
    start(session, template_id="orc", count=2, distance=30, pinned_stance=ReactionResult.HOSTILE)
    give_wand(session)
    assert session.execute(Evade()).accepted
    group = session.encounter.groups[0]
    assert session.encounter.pursuit is not None, "precondition: the orcs give chase"
    events = list(session.execute(UseItem(character_id=MAGIC_USER, item_id=WAND_ID, target_id=group.id)).events)
    frightened = [
        event.target_id for event in events if isinstance(event, ConditionGainedEvent) and event.condition == "afraid"
    ]
    assert frightened == group.monster_ids, "precondition: the wand frightens both orcs"
    while session.mode is SessionMode.ENCOUNTER:
        events.extend(session.execute(Wait()).events)

    assert session.mode is SessionMode.EXPLORING
    (ended,) = [event for event in events if isinstance(event, EncounterEndedEvent)]
    assert ended.outcome == "escaped"
    expected = [(monster_id, "routed", ORC_XP) for monster_id in group.monster_ids]
    assert defeats(events) == expected
    assert [(record.monster_id, record.outcome, record.xp) for record in session.defeated_monsters] == expected
