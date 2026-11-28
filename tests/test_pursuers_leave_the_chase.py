"""A group that can no longer give chase drops out of a pursuit, and the party gets away when no pursuer is left.

`_group_pursues` is the one test for a pursuer: the group hasn't fled, isn't fleeing after a failed morale check, has
a living member who is neither turned nor afraid, and has a living member able to move. `Evade`, each round of a
chase, and a party's retreat from a battle all use it. When a round of a chase finds no pursuer left, the party
escapes at once with `encounter.pursuit.escaped` and gains no exhaustion, which only the 30-round limit brings.

Two hostile orcs run at 120', the party's own rate, so they chase it at a steady gap until something stops them. The
magic-user's wand, used during the chase, takes one pursuit round: the second, after the round that `Evade` runs.
"""

import pytest

from osrlib.core.events import ConditionGainedEvent
from osrlib.core.items import MagicItemInstance
from osrlib.core.tables import ReactionResult
from osrlib.crawl.commands import BattleDeclaration, Evade, ResolveBattleRound, UseItem
from osrlib.crawl.events import (
    BattleEndedEvent,
    EncounterEndedEvent,
    ExhaustionEvent,
    MonsterDefeatedEvent,
    PursuitEvent,
)
from osrlib.crawl.session import GameSession, SessionMode
from test_battle import battle_session, hold_all
from test_encounter import quiet_session, start

MAGIC_USER = "character-0004"
WAND_ID = "magic-item-9001"
ORC_XP = 10


def chased_by_orcs(wand: str) -> GameSession:
    session = quiet_session(seed=1)
    start(session, template_id="orc", count=2, distance=30, pinned_stance=ReactionResult.HOSTILE)
    session.member(MAGIC_USER).inventory.items.append(
        MagicItemInstance(instance_id=WAND_ID, template_id=wand, identified=True, charges_remaining=5)
    )
    assert session.execute(Evade()).accepted
    assert session.encounter is not None and session.encounter.pursuit is not None, "precondition: the orcs give chase"
    return session


@pytest.mark.parametrize(
    ("wand", "condition", "defeated"),
    [
        pytest.param("wand_of_paralysation", "paralysed", (), id="paralysed"),
        pytest.param("wand_of_fear", "afraid", ("routed", "routed"), id="afraid"),
    ],
)
def test_the_party_escapes_at_once_when_the_last_pursuers_drop_out(wand, condition, defeated):
    session = chased_by_orcs(wand)
    group = session.encounter.groups[0]
    gap = session.encounter.pursuit.gap_feet

    result = session.execute(UseItem(character_id=MAGIC_USER, item_id=WAND_ID, target_id=group.id))

    assert result.accepted
    events = list(result.events)
    stricken = [
        event.target_id for event in events if isinstance(event, ConditionGainedEvent) and event.condition == condition
    ]
    assert stricken == group.monster_ids, "precondition: the wand strikes both orcs"
    (step,) = [event for event in events if isinstance(event, PursuitEvent)]
    assert (step.code, step.round, step.gap_feet) == ("encounter.pursuit.escaped", 2, gap)
    (ended,) = [event for event in events if isinstance(event, EncounterEndedEvent)]
    assert ended.outcome == "escaped"
    assert not any(isinstance(event, ExhaustionEvent) for event in events), "a short chase doesn't exhaust the party"
    assert session.mode is SessionMode.EXPLORING and session.encounter is None
    outcomes = tuple(event.outcome for event in events if isinstance(event, MonsterDefeatedEvent))
    assert outcomes == defeated
    assert [(record.outcome, record.xp) for record in session.defeated_monsters] == [
        (outcome, ORC_XP) for outcome in defeated
    ]


@pytest.mark.parametrize(
    ("wand", "condition", "defeated"),
    [
        pytest.param("wand_of_paralysation", "paralysed", (), id="helpless"),
        pytest.param("wand_of_fear", "afraid", ("routed", "routed"), id="wholly_afraid"),
    ],
)
def test_a_group_that_cannot_pursue_lets_the_party_retreat_from_battle(wand, condition, defeated):
    """Two goblins 30' away are struck by the magic-user's wand in round 1 (seed 1), and the party retreats in round 2.

    Neither group gives chase, so the battle ends with the party fled and the encounter closes as evaded, with no
    pursuit. The afraid goblins are still within 120' when the battle ends, so they're routed with their experience.
    """
    session = battle_session(count=2, distance=30, seed=1)
    session.member(MAGIC_USER).inventory.items.append(
        MagicItemInstance(instance_id=WAND_ID, template_id=wand, identified=True, charges_remaining=5)
    )
    group = session.encounter.groups[0]
    wand_round = session.execute(
        ResolveBattleRound(
            declarations=hold_all(
                session,
                extra=(
                    BattleDeclaration(
                        character_id=MAGIC_USER, action="use_item", item_id=WAND_ID, target_group_id=group.id
                    ),
                ),
            )
        )
    )
    stricken = [
        event.target_id
        for event in wand_round.events
        if isinstance(event, ConditionGainedEvent) and event.condition == condition
    ]
    assert stricken == group.monster_ids, "precondition: the wand strikes both goblins"
    assert session.mode is SessionMode.BATTLE

    retreat = tuple(
        BattleDeclaration(character_id=member.id, action="move", move="retreat")
        for member in session.party.living_members()
    )
    result = session.execute(ResolveBattleRound(declarations=retreat))

    assert result.accepted, result.rejections
    events = list(result.events)
    assert [event.code for event in events if isinstance(event, BattleEndedEvent)] == ["battle.ended.fled"]
    (ended,) = [event for event in events if isinstance(event, EncounterEndedEvent)]
    assert ended.outcome == "evaded"
    assert not any(isinstance(event, PursuitEvent) for event in events), "nobody gives chase"
    assert session.mode is SessionMode.EXPLORING and session.encounter is None
    assert not group.fled, "the goblins never got away themselves"
    outcomes = tuple(event.outcome for event in events if isinstance(event, MonsterDefeatedEvent))
    assert outcomes == defeated
