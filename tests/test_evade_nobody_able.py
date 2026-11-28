"""Acceptance tests for issue 372: a party with nobody able to act can't evade an encounter.

The OSE SRD's evasion rule compares the two sides' movement rates, and "The evasion automatically succeeds, unless the
fleeing side is forced to stop." A party whose living members are all dead, paralysed, petrified, or asleep can't run,
so the session refuses `Evade` with `encounter.evade.nobody_able_to_act` for every drop, and the refusal draws nothing,
takes no time, and changes nothing. A party with at least one member able to act runs at its usual rate, and the members
who can act carry the others when those others are paralysed or asleep. A party that becomes wholly unable to act
during a pursuit stops running, so the pursuers close the gap at their own rate until they catch it. So does a party
with a member caught in a *web*, because the others can't carry that member out of it. The view tests in
`test_evade_rejections_view.py` cover the refusals for an entangled or overloaded member.
"""

import json

import pytest

from osrlib.core.effects import Condition, grant_condition, kill
from osrlib.core.tables import ReactionResult
from osrlib.core.validation import Rejection
from osrlib.crawl import encounter as encounter_module
from osrlib.crawl.commands import Evade, Wait
from osrlib.crawl.events import EvasionEvent, PursuitEvent
from osrlib.crawl.session import GameSession, SessionMode
from osrlib.persistence import session_state
from test_encounter import quiet_session, start

NOBODY_ABLE = Rejection(code="encounter.evade.nobody_able_to_act")
WOLF_RATE_FEET = 180
"""A normal wolf's full movement rate, 180' per round, which outruns every member of the stock party."""


def fingerprint(game: GameSession) -> dict:
    return {
        "state": json.loads(json.dumps(session_state(game))),
        "streams": {key: state.model_dump(mode="json") for key, state in game.streams.export_states().items()},
        "rounds": game.clock.rounds,
        "log": len(game.command_log),
    }


@pytest.mark.parametrize("condition", [Condition.PARALYSED, Condition.ASLEEP])
@pytest.mark.parametrize("drop", ["none", "treasure", "food"])
def test_a_party_with_nobody_able_to_act_cannot_evade(condition, drop):
    session = quiet_session()
    start(session, template_id="normal_wolf", pinned_stance=ReactionResult.INDIFFERENT)
    for member in session.party.members:
        grant_condition(member, condition, None)

    before = fingerprint(session)
    result = session.execute(Evade(drop=drop))

    assert result.rejections == (NOBODY_ABLE,)
    assert result.events == ()
    assert fingerprint(session) == before
    assert session.mode is SessionMode.ENCOUNTER


def test_the_dead_do_not_count_as_able_to_act():
    session = quiet_session()
    start(session, template_id="normal_wolf", pinned_stance=ReactionResult.INDIFFERENT)
    first, *rest = session.party.members
    kill(first)
    for member in rest:
        grant_condition(member, Condition.ASLEEP, None)

    assert session.execute(Evade()).rejections == (NOBODY_ABLE,)


def test_one_member_able_to_act_carries_the_rest_at_the_partys_rate():
    """Three paralysed members don't slow the party: the members who can act carry them at no extra cost."""
    session = quiet_session()
    start(session, template_id="normal_wolf", pinned_stance=ReactionResult.INDIFFERENT)
    rate = encounter_module._party_run_rate(session)
    for member in session.party.members[1:]:
        grant_condition(member, Condition.PARALYSED, None)
    assert encounter_module._party_run_rate(session) == rate

    result = session.execute(Evade())

    assert result.accepted
    assert [event.code for event in result.events if isinstance(event, EvasionEvent)] == ["encounter.evasion.succeeded"]
    assert session.encounter is None


@pytest.mark.parametrize(
    "stopped",
    [
        pytest.param([Condition.PARALYSED] * 4, id="nobody_able_to_act"),
        pytest.param([None, None, Condition.ENTANGLED, None], id="one_member_entangled"),
    ],
)
def test_a_party_that_is_stopped_mid_pursuit_is_caught(stopped):
    session = quiet_session()
    start(session, template_id="normal_wolf", distance=900, pinned_stance=ReactionResult.HOSTILE)
    assert session.execute(Evade()).accepted
    pursuit = session.encounter.pursuit
    assert pursuit is not None, "precondition: the wolves give chase"
    for member, condition in zip(session.party.members, stopped, strict=True):
        if condition is not None:
            grant_condition(member, condition, None)
    assert encounter_module._party_run_rate(session) == 0

    gaps = [pursuit.gap_feet]
    while session.mode is SessionMode.ENCOUNTER:
        result = session.execute(Wait())
        assert result.accepted
        (step,) = [event for event in result.events if isinstance(event, PursuitEvent)]
        gaps.append(step.gap_feet)

    assert session.mode is SessionMode.BATTLE
    assert step.code == "encounter.pursuit.caught"
    assert all(earlier - later == WOLF_RATE_FEET for earlier, later in zip(gaps, gaps[1:-1], strict=False))
    assert gaps[-1] == max(0, gaps[-2] - WOLF_RATE_FEET)
