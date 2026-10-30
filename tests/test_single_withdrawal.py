"""A fighting withdrawal or a retreat that one party member makes alone (issue #171).

The OSE SRD describes both defensive moves per combatant: "The combatant moves backwards at up to
half their encounter movement rate. There must be clear space behind the combatant." and "The
combatant turns and flees from melee, moving up to their full encounter movement rate. This
round: the retreating combatant may not attack; the opponent gains a +2 bonus to all attacks
against the retreating combatant and ignores any AC bonus due to the combatant's shield (if
applicable)." osrlib keeps the formation move for a round every declarer agrees on, and makes
every other defensive move the declaring member's own. The contract, which the adaptations
register states under "The battle round":

- A front-rank member who withdraws alone names a back-rank member in
  `BattleDeclaration.replacement_id`. In the party's movement step the two trade places in the
  marching order, and a `MemberWithdrewEvent` reports it. The party doesn't move, and the order
  stays changed.
- Each rank stands 5' behind the one in front of it. The withdrawing member covers the distance at
  up to half their encounter rate for a fighting withdrawal or their full encounter rate for a
  retreat, the replacement at up to their full encounter rate, each doubled by their own *haste*.
- The refusals: `battle.declaration.not_in_front_rank` for a back-rank member,
  `battle.declaration.no_replacement`, `battle.declaration.replacement_unavailable`, and
  `battle.declaration.replacement_too_far`.
- A retreat's penalties fall on each member who retreats, alone or with the formation, and last the
  whole round, whichever side acts first.
- The replacement may strike in melee the round they step up. A withdrawing member makes no attack.
- A swap that can't happen when the movement step comes doesn't happen and reports nothing.

Every acceptance test here is marked expected-to-fail until the chunk lands. The classes at the end
are unmarked guards: they pin the formation moves as they are today, which the chunk must keep.
"""

import pytest

from crawl_fixtures import STOCK_ROSTER, build_adventure, build_party
from osrlib.core.character import party_to_document
from osrlib.core.combat import DamageSource, deal_damage
from osrlib.core.effects import ActiveCondition, Condition, EffectDefinition, has_condition
from osrlib.core.events import AttackRolledEvent, InitiativeRolledEvent, Visibility
from osrlib.core.ruleset import Ruleset
from osrlib.core.spells import MemorizedSpell, memorize_spells
from osrlib.core.tables import ReactionResult
from osrlib.crawl import battle as battle_module
from osrlib.crawl import encounter as encounter_module
from osrlib.crawl.battle import MonsterAction
from osrlib.crawl.commands import (
    BattleDeclaration,
    EngageBattle,
    EnterDungeon,
    EquipItem,
    GrantItem,
    LightSource,
    ReorderParty,
    ResolveBattleRound,
    SessionMode,
    SpawnMonsters,
)
from osrlib.crawl.events import MemberWithdrewEvent
from osrlib.crawl.session import GameSession
from osrlib.data import load_classes, load_equipment, load_spells
from osrlib.persistence import load_game, replay_game, save_game, session_state

CHUNK = pytest.mark.xfail(reason="chunk: single-withdrawal", strict=True)

BRAKK = "character-0001"
"""The fighter, with a sword. Front rank in the stock marching order."""
SABLE = "character-0002"
"""The thief, with a crossbow. Front rank."""
WYNN = "character-0003"
"""The cleric. Second rank."""
ELARA = "character-0004"
"""The magic-user, with *sleep* in her book. Second rank."""
GARR = "character-0005"
HALE = "character-0006"
IVER = "character-0007"
JORY = "character-0008"

SIX = (*STOCK_ROSTER, ("Garr", "fighter"), ("Hale", "fighter"))
"""Three ranks in the two-abreast corridor: Brakk and Sable, Wynn and Elara, Garr and Hale."""
EIGHT = (*SIX, ("Iver", "fighter"), ("Jory", "fighter"))
"""Four ranks: Iver and Jory stand fifteen feet behind the front."""

MONSTERS_FIRST_SEED = 1
"""Four goblins at 5' win initiative and strike at both Brakk and Sable before the party acts."""
PARTY_FIRST_SEED = 0
"""The party wins initiative over four goblins at 5', and the goblins strike at the front rank's first place."""
GHOUL_SEED = 1
"""Three ghouls at 5' win initiative and paralyse Brakk, and not Wynn, before the party acts."""
REPLAY_SEED = 0
"""Two goblins spawned at 5' by command give a battle whose first round the party declares."""


# ---------------------------------------------------------------------- helpers


def _delve(*, roster=STOCK_ROSTER, seed=5, ruleset=None, shield=False, laden=()) -> GameSession:
    """The party in the delve's entrance corridor, two abreast, torch lit, Brakk and Sable armed.

    `laden` names fighters who put on plate mail and haul treasure: 30' a turn under basic
    encumbrance, so a 10' encounter rate.
    """
    session = GameSession.new(build_party(roster), build_adventure(wandering_chance=0), seed=seed, ruleset=ruleset)
    grants = [(BRAKK, "torch", 6), (BRAKK, "tinder_box", 1), (BRAKK, "sword", 1), (SABLE, "crossbow", 1)]
    grants.append((SABLE, "crossbow_bolts", 1))
    equips = [(BRAKK, "sword"), (SABLE, "crossbow")]
    if shield:
        grants.append((BRAKK, "shield", 1))
        equips.append((BRAKK, "shield"))
    for member_id in laden:
        grants.append((member_id, "plate_mail", 1))
        equips.append((member_id, "plate_mail"))
    for member_id, item_id, quantity in grants:
        assert session.execute(GrantItem(character_id=member_id, item_id=item_id, quantity=quantity)).accepted
    for member_id, item_id in equips:
        assert session.execute(EquipItem(character_id=member_id, item_id=item_id)).accepted
    for member_id in laden:
        session.member(member_id).carrying_treasure = True
        assert session.member(member_id).movement_rate(session.ruleset) == 30
    assert session.execute(EnterDungeon(dungeon_id="delve")).accepted
    for _ in range(20):
        lit = session.execute(LightSource(character_id=BRAKK, item_id="torch"))
        if any(event.code == "exploration.light.lit" for event in lit.events):
            break
    return session


def _battle(
    *,
    roster=STOCK_ROSTER,
    template_id="goblin",
    count=2,
    distance=40,
    seed=5,
    ruleset=None,
    shield=False,
    laden=(),
    hp=50,
) -> GameSession:
    """A battle at round 0 against one group, every member at `hp` so nobody falls by chance."""
    session = _delve(roster=roster, seed=seed, ruleset=ruleset, shield=shield, laden=laden)
    instances = session.spawn(template_id, count)
    encounter_module.start_encounter(
        session,
        groups=[(template_id, instances)],
        kind="spawned",
        distance_feet=distance,
        pinned_stance=ReactionResult.HOSTILE,
        party_aware=True,
    )
    assert session.execute(EngageBattle()).accepted
    assert session.mode is SessionMode.BATTLE
    assert session.battle.round == 0
    for member in session.party.members:
        member.max_hp = hp
        member.current_hp = hp
    return session


def _group(session) -> str:
    return session.encounter.groups[0].id


def _order(session) -> list[str]:
    return [member.id for member in session.party.members]


def _view(session):
    return session.view(Visibility.PLAYER).encounter


def _ranks(session) -> list[list[str]]:
    width = 2  # the delve's entrance corridor is ten feet wide
    living = [member.id for member in session.party.living_members()]
    return [living[index : index + width] for index in range(0, len(living), width)]


def _withdraw(character_id, replacement_id=None, move="fighting_withdrawal", **fields) -> BattleDeclaration:
    return BattleDeclaration(
        character_id=character_id, action="move", move=move, replacement_id=replacement_id, **fields
    )


def _declare(session, *declarations) -> tuple[BattleDeclaration, ...]:
    """The given declarations, and a `hold` for every other member the round expects."""
    covered = {declaration.character_id for declaration in declarations}
    rest = tuple(
        BattleDeclaration(character_id=member_id, action="hold")
        for member_id in _view(session).declarers
        if member_id not in covered
    )
    return (*declarations, *rest)


def _round(session, *declarations):
    return session.execute(ResolveBattleRound(declarations=_declare(session, *declarations)))


def _rejections(result) -> list[tuple[str, dict]]:
    return sorted(((rejection.code, dict(rejection.params)) for rejection in result.rejections), key=repr)


def _swaps(result) -> list[MemberWithdrewEvent]:
    return [event for event in result.events if isinstance(event, MemberWithdrewEvent)]


def _attacks(result) -> list[AttackRolledEvent]:
    return [event for event in result.events if isinstance(event, AttackRolledEvent)]


def _position(result, event) -> int:
    """Where `event` sits in the round's events, by identity, since two attack events can be equal."""
    return next(index for index, candidate in enumerate(result.events) if candidate is event)


def _initiative(result) -> InitiativeRolledEvent:
    return next(event for event in result.events if isinstance(event, InitiativeRolledEvent))


def _monsters_acted_first(session, result) -> bool:
    return _initiative(result).order[0] == _group(session)


def _attach(session, definition, target_id) -> None:
    session.ledger.attach(
        definition, target_id, clock=session.clock, allocator=session.allocator, registry=session.registry()
    )


def _assert_refused_cleanly(session, result, before) -> None:
    """A refused round costs no draw, no round, no clock, no order change, and no event."""
    streams, round_number, rounds, order, log_size = before
    assert not result.accepted
    assert result.events == ()
    assert session.streams.export_states() == streams
    assert session.battle.round == round_number
    assert session.clock.rounds == rounds
    assert _order(session) == order
    assert len(session.event_log) == log_size


def _snapshot(session):
    return (
        session.streams.export_states(),
        session.battle.round,
        session.clock.rounds,
        _order(session),
        len(session.event_log),
    )


class _StrikeAt:
    """A test policy: every living monster in the group strikes one named member, wherever they stand."""

    def __init__(self, target_id: str) -> None:
        self.target_id = target_id

    def choose(self, session, group, stream):
        return [
            MonsterAction(monster_id=monster_id, kind="melee", target_id=self.target_id)
            for monster_id in group.monster_ids
        ]


# ---------------------------------------------------------------------- the swap


@CHUNK
class TestAFightingWithdrawalAlone:
    """One front-rank member falls back, a named back-rank member steps up, and the party stays put."""

    def test_the_member_trades_places_with_the_named_replacement(self):
        session = _battle(distance=5)
        assert _view(session).front_rank == (BRAKK, SABLE)
        assert _ranks(session) == [[BRAKK, SABLE], [WYNN, ELARA]]
        result = _round(session, _withdraw(BRAKK, WYNN))
        assert result.accepted, _rejections(result)
        assert _order(session) == [WYNN, SABLE, BRAKK, ELARA]
        assert _view(session).front_rank == (WYNN, SABLE)
        [swap] = _swaps(result)
        assert (swap.code, swap.character_id, swap.replacement_id) == ("battle.member.withdrew", BRAKK, WYNN)
        assert swap.visibility is Visibility.PLAYER
        assert session.encounter.groups[0].distance_feet == 5
        assert "battle.group.moved" not in [event.code for event in result.events]
        assert session.mode is SessionMode.BATTLE
        assert session.battle.round == 1

    def test_a_withdrawal_needs_no_group_in_melee(self):
        session = _battle(distance=40)
        result = _round(session, _withdraw(BRAKK, WYNN))
        assert result.accepted, _rejections(result)
        assert _order(session) == [WYNN, SABLE, BRAKK, ELARA]
        # The party never backs away: every distance the round reports is the goblins closing.
        assert all(event.distance_feet < 40 for event in result.events if event.code == "battle.group.moved")

    def test_a_replacement_two_ranks_back_takes_the_front_place(self):
        session = _battle(roster=SIX, distance=5)
        assert _ranks(session) == [[BRAKK, SABLE], [WYNN, ELARA], [GARR, HALE]]
        result = _round(session, _withdraw(BRAKK, GARR))
        assert result.accepted, _rejections(result)
        assert _order(session) == [GARR, SABLE, WYNN, ELARA, BRAKK, HALE]
        assert _view(session).front_rank == (GARR, SABLE)

    def test_the_whole_front_rank_can_step_back_behind_the_second(self):
        session = _battle(distance=5)
        result = _round(session, _withdraw(SABLE, ELARA), _withdraw(BRAKK, WYNN))
        assert result.accepted, _rejections(result)
        assert _order(session) == [WYNN, ELARA, BRAKK, SABLE]
        # One event per swap, in the marching order of the members who withdrew.
        assert [(swap.character_id, swap.replacement_id) for swap in _swaps(result)] == [(BRAKK, WYNN), (SABLE, ELARA)]

    def test_a_withdrawal_shares_a_round_with_a_close_and_resolves_first(self):
        session = _battle(distance=40)
        close = BattleDeclaration(character_id=SABLE, action="move", move="close", target_group_id=_group(session))
        result = _round(session, _withdraw(BRAKK, WYNN), close)
        assert result.accepted, _rejections(result)
        assert _order(session) == [WYNN, SABLE, BRAKK, ELARA]
        [swap] = _swaps(result)
        at = _position(result, swap)
        closing = result.events[at + 1]
        assert closing.code == "battle.group.moved"
        before = [event.distance_feet for event in result.events[:at] if event.code == "battle.group.moved"]
        assert closing.distance_feet < (before[-1] if before else 40)


@CHUNK
class TestARetreatAlone:
    """A retreat alone trades places as a withdrawal does, and its penalties are the retreater's alone."""

    def test_the_member_trades_places_and_the_battle_goes_on(self):
        session = _battle(distance=5)
        result = _round(session, _withdraw(BRAKK, WYNN, move="retreat"))
        assert result.accepted, _rejections(result)
        assert _order(session) == [WYNN, SABLE, BRAKK, ELARA]
        [swap] = _swaps(result)
        assert (swap.code, swap.character_id, swap.replacement_id) == ("battle.member.retreated", BRAKK, WYNN)
        codes = [event.code for event in result.events]
        assert "battle.ended.fled" not in codes
        assert session.mode is SessionMode.BATTLE
        assert session.encounter.pursuit is None
        assert session.encounter.groups[0].distance_feet == 5

    def test_attackers_get_the_bonus_against_the_retreater_alone_when_they_strike_first(self):
        retreating = _battle(distance=5, count=4, seed=MONSTERS_FIRST_SEED, shield=True)
        holding = _battle(distance=5, count=4, seed=MONSTERS_FIRST_SEED, shield=True)
        result = _round(retreating, _withdraw(BRAKK, WYNN, move="retreat"))
        baseline = _round(holding)
        assert result.accepted, _rejections(result)
        assert baseline.accepted
        # Preconditions: the goblins win initiative in both, and strike at both front-rank members.
        assert _monsters_acted_first(retreating, result)
        assert _monsters_acted_first(holding, baseline)
        [swap] = _swaps(result)
        assert all(_position(result, attack) < _position(result, swap) for attack in _attacks(result))
        pairs = list(zip(_attacks(result), _attacks(baseline), strict=True))
        assert {attack.defender_id for attack, _ in pairs} == {BRAKK, SABLE}
        shield_bonus = load_equipment().get("shield").ac_bonus
        for attack, held in pairs:
            assert (attack.attacker_id, attack.defender_id) == (held.attacker_id, held.defender_id)
            if attack.defender_id == BRAKK:
                assert attack.modifier == held.modifier + 2
                assert attack.defender_ac == held.defender_ac + shield_bonus
            else:
                assert attack.modifier == held.modifier
                assert attack.defender_ac == held.defender_ac
        # The retreat still happens once the party's turn comes.
        assert _order(retreating) == [WYNN, SABLE, BRAKK, ELARA]

    @pytest.mark.parametrize("move", ["fighting_withdrawal", "retreat"])
    def test_once_the_party_has_moved_melee_no_longer_reaches_the_member(self, move):
        session = _battle(distance=5, count=4, seed=PARTY_FIRST_SEED, shield=True)
        holding = _battle(distance=5, count=4, seed=PARTY_FIRST_SEED, shield=True)
        result = _round(session, _withdraw(BRAKK, WYNN, move=move))
        baseline = _round(holding)
        assert result.accepted, _rejections(result)
        assert not _monsters_acted_first(session, result)
        attacks = _attacks(result)
        assert attacks, "precondition: the goblins struck at the front rank"
        [swap] = _swaps(result)
        assert all(_position(result, attack) > _position(result, swap) for attack in attacks)
        assert BRAKK not in {attack.defender_id for attack in attacks}
        assert WYNN in {attack.defender_id for attack in attacks}
        # Nobody else pays for the retreat: every goblin strikes at its ordinary modifier.
        held_modifiers = {attack.modifier for attack in _attacks(baseline)}
        assert len(held_modifiers) == 1
        assert {attack.modifier for attack in attacks} == held_modifiers


@CHUNK
class TestTheMovementLimit:
    """Each rank stands 5' behind the one in front, and both members have to cover the distance."""

    def test_a_laden_member_withdraws_one_rank(self):
        session = _battle(roster=SIX, distance=5, laden=(BRAKK,))
        result = _round(session, _withdraw(BRAKK, WYNN))
        assert result.accepted, _rejections(result)
        assert _order(session)[:3] == [WYNN, SABLE, BRAKK]

    def test_a_laden_member_cannot_withdraw_two_ranks(self):
        session = _battle(roster=SIX, distance=5, laden=(BRAKK,))
        before = _snapshot(session)
        result = _round(session, _withdraw(BRAKK, GARR))
        assert _rejections(result) == [
            (
                "battle.declaration.replacement_too_far",
                {"character": BRAKK, "replacement": GARR, "distance_feet": 10},
            )
        ]
        _assert_refused_cleanly(session, result, before)

    def test_a_laden_member_retreats_two_ranks_at_the_full_rate(self):
        session = _battle(roster=SIX, distance=5, laden=(BRAKK,))
        result = _round(session, _withdraw(BRAKK, GARR, move="retreat"))
        assert result.accepted, _rejections(result)
        assert _order(session)[0] == GARR
        assert _order(session)[4] == BRAKK

    def test_haste_doubles_the_withdrawal_limit(self):
        session = _battle(roster=SIX, distance=5, laden=(BRAKK,))
        _attach(
            session, EffectDefinition(kind="haste", params={"attacks_multiplier": 2, "movement_multiplier": 2}), BRAKK
        )
        result = _round(session, _withdraw(BRAKK, GARR))
        assert result.accepted, _rejections(result)
        assert _order(session)[0] == GARR

    def test_an_unladen_member_withdraws_three_ranks(self):
        session = _battle(roster=EIGHT, distance=5)
        assert _ranks(session)[3] == [IVER, JORY]
        result = _round(session, _withdraw(BRAKK, IVER))
        assert result.accepted, _rejections(result)
        assert _order(session)[0] == IVER
        assert _order(session)[6] == BRAKK

    def test_a_laden_replacement_cannot_step_up_three_ranks(self):
        session = _battle(roster=EIGHT, distance=5, laden=(IVER,))
        before = _snapshot(session)
        result = _round(session, _withdraw(BRAKK, IVER))
        assert _rejections(result) == [
            (
                "battle.declaration.replacement_too_far",
                {"character": BRAKK, "replacement": IVER, "distance_feet": 15},
            )
        ]
        _assert_refused_cleanly(session, result, before)


@CHUNK
class TestWhenNoOneCanStepUp:
    """A member who withdraws alone needs a replacement who can take the front place this round."""

    @pytest.mark.parametrize("move", ["fighting_withdrawal", "retreat"])
    def test_a_member_who_names_no_replacement_is_refused(self, move):
        session = _battle(distance=5)
        before = _snapshot(session)
        result = _round(session, _withdraw(BRAKK, move=move))
        assert _rejections(result) == [("battle.declaration.no_replacement", {"character": BRAKK})]
        _assert_refused_cleanly(session, result, before)

    def test_a_back_rank_member_cannot_withdraw_alone(self):
        session = _battle(distance=5)
        assert WYNN not in _view(session).front_rank
        result = _round(session, _withdraw(WYNN, ELARA))
        assert _rejections(result) == [("battle.declaration.not_in_front_rank", {"character": WYNN})]

    @pytest.mark.parametrize("named", [SABLE, BRAKK, "character-9999"])
    def test_the_replacement_has_to_be_a_member_behind_the_front_rank(self, named):
        session = _battle(distance=5)
        before = _snapshot(session)
        result = _round(session, _withdraw(BRAKK, named))
        assert _rejections(result) == [
            ("battle.declaration.replacement_unavailable", {"character": BRAKK, "replacement": named})
        ]
        _assert_refused_cleanly(session, result, before)

    def test_a_dead_replacement_cannot_step_up(self):
        session = _battle(distance=5)
        deal_damage(session.member(WYNN), 999, source=DamageSource())
        assert has_condition(session.member(WYNN), Condition.DEAD)
        result = _round(session, _withdraw(BRAKK, WYNN))
        assert _rejections(result) == [
            ("battle.declaration.replacement_unavailable", {"character": BRAKK, "replacement": WYNN})
        ]

    @pytest.mark.parametrize("condition", [Condition.ASLEEP, Condition.ENTANGLED])
    def test_a_replacement_who_cannot_act_or_move_cannot_step_up(self, condition):
        session = _battle(distance=5)
        session.member(WYNN).conditions = (ActiveCondition(condition=condition, effect_id="effect-9999"),)
        view = _view(session)
        assert (WYNN in view.declarers) is (condition is Condition.ENTANGLED)
        assert (WYNN in view.immobile) is (condition is Condition.ENTANGLED)
        result = _round(session, _withdraw(BRAKK, WYNN))
        assert _rejections(result) == [
            ("battle.declaration.replacement_unavailable", {"character": BRAKK, "replacement": WYNN})
        ]

    def test_a_replacement_who_casts_cannot_step_up(self):
        session = _battle(distance=5)
        elara = session.member(ELARA)
        memorize_spells(elara, load_classes().get("magic_user"), load_spells(), [MemorizedSpell(spell_id="sleep")])
        cast = BattleDeclaration(
            character_id=ELARA, action="cast", spell_id="sleep", spell_mode="hd_budget", target_group_id=_group(session)
        )
        result = _round(session, _withdraw(BRAKK, ELARA), cast)
        assert _rejections(result) == [
            ("battle.declaration.replacement_unavailable", {"character": BRAKK, "replacement": ELARA})
        ]
        # The same cast with nobody naming Elara is a legal round, so the refusal is the step up's.
        legal = _round(session, cast)
        assert legal.accepted, _rejections(legal)

    def test_a_replacement_who_withdraws_too_cannot_step_up(self):
        session = _battle(distance=5)
        result = _round(session, _withdraw(BRAKK, WYNN), _withdraw(WYNN, ELARA))
        assert _rejections(result) == [
            ("battle.declaration.not_in_front_rank", {"character": WYNN}),
            ("battle.declaration.replacement_unavailable", {"character": BRAKK, "replacement": WYNN}),
        ]

    def test_two_members_cannot_name_one_replacement(self):
        session = _battle(distance=5)
        result = _round(session, _withdraw(BRAKK, WYNN), _withdraw(SABLE, WYNN))
        # The member earlier in marching order keeps the replacement.
        assert _rejections(result) == [
            ("battle.declaration.replacement_unavailable", {"character": SABLE, "replacement": WYNN})
        ]

    def test_without_the_width_limit_nobody_stands_behind(self):
        session = _battle(distance=5, ruleset=Ruleset(formation_width_limit=False))
        assert _view(session).front_rank == (BRAKK, SABLE, WYNN, ELARA)
        named = _round(session, _withdraw(BRAKK, WYNN))
        assert _rejections(named) == [
            ("battle.declaration.replacement_unavailable", {"character": BRAKK, "replacement": WYNN})
        ]
        unnamed = _round(session, _withdraw(BRAKK))
        assert _rejections(unnamed) == [("battle.declaration.no_replacement", {"character": BRAKK})]

    def test_a_formation_move_has_no_replacement_to_name(self):
        session = _battle(distance=5)
        declarations = [_withdraw(member_id) for member_id in (SABLE, WYNN, ELARA)]
        result = _round(session, _withdraw(BRAKK, WYNN), *declarations)
        # Every declarer withdraws, so the formation moves, and the named member is withdrawing too.
        assert _rejections(result) == [
            ("battle.declaration.replacement_unavailable", {"character": BRAKK, "replacement": WYNN})
        ]


# ---------------------------------------------------------------------- melee around the swap


@CHUNK
class TestSteppingUpIntoMelee:
    """From the movement step on the replacement stands in the front rank, and the withdrawn member doesn't."""

    def test_the_replacement_may_strike_the_round_they_step_up(self):
        session = _battle(distance=5)
        assert WYNN not in _view(session).front_rank
        strike = BattleDeclaration(character_id=WYNN, action="attack", target_group_id=_group(session))
        result = _round(session, _withdraw(BRAKK, WYNN), strike)
        assert result.accepted, _rejections(result)
        [swap] = _swaps(result)
        wynn_attacks = [attack for attack in _attacks(result) if attack.attacker_id == WYNN]
        assert wynn_attacks
        assert all(_position(result, attack) > _position(result, swap) for attack in wynn_attacks)

    def test_a_back_rank_member_who_does_not_step_up_still_cannot_strike(self):
        session = _battle(distance=5)
        strike = BattleDeclaration(character_id=ELARA, action="attack", target_group_id=_group(session))
        result = _round(session, _withdraw(BRAKK, WYNN), strike)
        assert _rejections(result) == [("battle.declaration.not_in_front_rank", {"character": ELARA})]

    def test_a_member_paralysed_before_the_movement_step_stays_in_front(self):
        session = _battle(template_id="ghoul", count=3, distance=5, seed=GHOUL_SEED)
        strike = BattleDeclaration(character_id=WYNN, action="attack", target_group_id=_group(session))
        result = _round(session, _withdraw(BRAKK, WYNN), strike)
        assert result.accepted, _rejections(result)
        # Preconditions: the ghouls act first and paralyse Brakk, and Wynn can still move.
        assert _monsters_acted_first(session, result)
        assert has_condition(session.member(BRAKK), Condition.PARALYSED)
        assert not has_condition(session.member(WYNN), Condition.PARALYSED)
        assert _swaps(result) == []
        assert _order(session) == [BRAKK, SABLE, WYNN, ELARA]
        # Wynn never stepped up, so the melee attack Wynn declared doesn't resolve.
        assert WYNN not in {attack.attacker_id for attack in _attacks(result)}

    def test_a_strike_chosen_against_a_withdrawn_member_is_skipped(self):
        session = _battle(distance=5, count=4, seed=PARTY_FIRST_SEED)
        holding = _battle(distance=5, count=4, seed=PARTY_FIRST_SEED)
        for game in (session, holding):
            game.action_policies[_group(game)] = _StrikeAt(BRAKK)
        result = _round(session, _withdraw(BRAKK, WYNN))
        baseline = _round(holding)
        assert result.accepted, _rejections(result)
        # Preconditions: the party acts first, and the policy's strikes at Brakk land when he holds.
        assert not _monsters_acted_first(session, result)
        assert BRAKK in {attack.defender_id for attack in _attacks(baseline)}
        assert BRAKK not in {attack.defender_id for attack in _attacks(result)}


class TestAReplacementWhoCannotActWhenTheMomentComes:
    """The swap is judged again in the movement step. A replacement who can no longer act on the party's
    behalf by then doesn't step up: one who is incapacitated, charmed, or confused. The two stay where
    they are, nothing reports the swap, and the replacement's melee attack doesn't resolve, because the
    replacement never reached the front rank.

    The condition lands between acceptance and the movement step through a wrapper around the battle
    module's `_swap_ranks`, which the party block calls to start its movement step. The confusion is a
    bare condition with no ledger effect behind it, so the behavior roll that would override a confused
    member's declaration never runs, and the outcome takes no draw.
    """

    @staticmethod
    def _afflict_before_the_move(monkeypatch, member_id: str, condition: Condition) -> list[bool]:
        original = battle_module._swap_ranks
        ran: list[bool] = []

        def afflicted(session, swaps):
            member = session.member(member_id)
            member.conditions = (*member.conditions, ActiveCondition(condition=condition, effect_id="effect-9999"))
            ran.append(True)
            return original(session, swaps)

        monkeypatch.setattr(battle_module, "_swap_ranks", afflicted)
        return ran

    def _round_with_a_late_condition(self, monkeypatch, condition: Condition):
        session = _battle(distance=5)
        # Preconditions: Wynn stands behind the front rank, free to act, when the round is declared.
        assert WYNN in _view(session).declarers
        assert WYNN not in _view(session).front_rank
        assert not has_condition(session.member(WYNN), condition)
        ran = self._afflict_before_the_move(monkeypatch, WYNN, condition)
        strike = BattleDeclaration(character_id=WYNN, action="attack", target_group_id=_group(session))
        result = _round(session, _withdraw(BRAKK, WYNN), strike)
        assert result.accepted, _rejections(result)
        # Preconditions: the condition landed in the movement step, and Wynn still bears it.
        assert ran == [True]
        assert has_condition(session.member(WYNN), condition)
        return session, result

    @CHUNK
    @pytest.mark.parametrize("condition", [Condition.CHARMED, Condition.CONFUSED])
    def test_a_charmed_or_confused_replacement_does_not_step_up(self, monkeypatch, condition):
        session, result = self._round_with_a_late_condition(monkeypatch, condition)
        assert _swaps(result) == []
        assert _order(session) == [BRAKK, SABLE, WYNN, ELARA]
        assert WYNN not in {attack.attacker_id for attack in _attacks(result)}

    def test_a_paralysed_replacement_does_not_step_up(self, monkeypatch):
        """Unmarked guard: the implemented re-check already holds for a replacement who can't move."""
        session, result = self._round_with_a_late_condition(monkeypatch, Condition.PARALYSED)
        assert _swaps(result) == []
        assert _order(session) == [BRAKK, SABLE, WYNN, ELARA]
        assert WYNN not in {attack.attacker_id for attack in _attacks(result)}


@CHUNK
class TestNoAttackWhileWithdrawing:
    """The move is the member's one declared action: a withdrawing member makes no attack that round."""

    @pytest.mark.parametrize("move", ["fighting_withdrawal", "retreat"])
    def test_a_member_who_withdraws_alone_does_not_shoot(self, move):
        session = _battle(distance=40)
        # Precondition: the same shot declared as an attack is legal and fires.
        shooting = _battle(distance=40)
        shot = BattleDeclaration(
            character_id=SABLE, action="attack", target_group_id=_group(shooting), weapon_id="crossbow"
        )
        fired = _round(shooting, shot)
        assert fired.accepted, _rejections(fired)
        assert SABLE in {attack.attacker_id for attack in _attacks(fired)}
        withdrawal = _withdraw(SABLE, ELARA, move=move, target_group_id=_group(session), weapon_id="crossbow")
        result = _round(session, withdrawal)
        assert result.accepted, _rejections(result)
        assert _order(session) == [BRAKK, ELARA, WYNN, SABLE]
        assert SABLE not in {attack.attacker_id for attack in _attacks(result)}
        assert SABLE not in session.battle.fired_last_round


# ---------------------------------------------------------------------- views


@CHUNK
class TestTheViewsAfterTheSwap:
    def test_the_player_view_shows_the_new_ranks(self):
        session = _battle(distance=5)
        result = _round(session, _withdraw(BRAKK, WYNN))
        assert result.accepted, _rejections(result)
        view = session.view(Visibility.PLAYER)
        assert [member.id for member in view.party] == [WYNN, SABLE, BRAKK, ELARA]
        assert view.encounter.front_rank == (WYNN, SABLE)
        assert view.encounter.declarers == (WYNN, SABLE, BRAKK, ELARA)
        shown = [event for event in result.events if event.visibility is Visibility.PLAYER]
        assert [
            (event.character_id, event.replacement_id) for event in shown if isinstance(event, MemberWithdrewEvent)
        ] == [(BRAKK, WYNN)]

    def test_the_referee_view_shows_the_new_marching_order(self):
        session = _battle(distance=5)
        assert _round(session, _withdraw(BRAKK, WYNN, move="retreat")).accepted
        view = session.view(Visibility.REFEREE)
        assert [member.id for member in view.party.members] == [WYNN, SABLE, BRAKK, ELARA]

    def test_the_new_order_holds_through_the_next_round_and_after_the_battle(self):
        session = _battle(distance=5)
        assert _round(session, _withdraw(BRAKK, WYNN)).accepted
        assert _round(session).accepted
        assert _view(session).front_rank == (WYNN, SABLE)
        for monster_id in session.encounter.groups[0].monster_ids:
            deal_damage(session.combatant(monster_id), 999, source=DamageSource())
        assert _round(session).accepted
        assert session.mode is SessionMode.EXPLORING
        assert _order(session) == [WYNN, SABLE, BRAKK, ELARA]


# ---------------------------------------------------------------------- determinism


def _commanded_battle(seed: int) -> tuple[GameSession, list]:
    """A battle built by commands alone, so a replay rebuilds it, and the starting party document."""
    start = party_to_document(build_party().members)
    session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=seed)
    assert session.execute(GrantItem(character_id=BRAKK, item_id="sword")).accepted
    assert session.execute(EquipItem(character_id=BRAKK, item_id="sword")).accepted
    assert session.execute(EnterDungeon(dungeon_id="delve")).accepted
    assert session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=5)).accepted
    if session.mode is SessionMode.ENCOUNTER:
        assert session.execute(EngageBattle()).accepted
    return session, start


@CHUNK
class TestDeterminism:
    def test_a_refused_withdrawal_costs_nothing(self):
        session = _battle(distance=5)
        before = _snapshot(session)
        distance = session.encounter.groups[0].distance_feet
        result = _round(session, _withdraw(BRAKK, SABLE))
        assert _rejections(result) == [
            ("battle.declaration.replacement_unavailable", {"character": BRAKK, "replacement": SABLE})
        ]
        _assert_refused_cleanly(session, result, before)
        assert session.encounter.groups[0].distance_feet == distance

    def test_a_swap_draws_nothing(self):
        # At 40' the goblins only close, so the round's one draw is initiative, in both sessions.
        swapping = _battle(distance=40, seed=11)
        holding = _battle(distance=40, seed=11)
        assert swapping.streams.export_states() == holding.streams.export_states()
        result = _round(swapping, _withdraw(BRAKK, WYNN))
        baseline = _round(holding)
        assert result.accepted, _rejections(result)
        assert _attacks(baseline) == []
        assert _order(swapping) == [WYNN, SABLE, BRAKK, ELARA]
        assert swapping.streams.export_states() == holding.streams.export_states()

    def test_a_replay_rebuilds_the_swap(self):
        session, start = _commanded_battle(REPLAY_SEED)
        assert session.mode is SessionMode.BATTLE
        assert _view(session).front_rank[0] == BRAKK
        assert WYNN in _view(session).declarers
        result = _round(session, _withdraw(BRAKK, WYNN))
        assert result.accepted, _rejections(result)
        assert _swaps(result)
        while session.mode is SessionMode.BATTLE and session.battle.round < 3:
            assert _round(session).accepted
        replayed = replay_game(REPLAY_SEED, start, session.adventure, Ruleset(), session.command_log)
        assert _order(replayed) == _order(session)
        assert _order(replayed)[:3] == [WYNN, SABLE, BRAKK]
        assert session_state(replayed) == session_state(session)

    def test_a_save_after_the_swap_resumes_the_same_battle(self):
        session = _battle(distance=5, count=4, seed=PARTY_FIRST_SEED)
        assert _round(session, _withdraw(BRAKK, WYNN)).accepted
        restored = load_game(save_game(session))
        assert _order(restored) == [WYNN, SABLE, BRAKK, ELARA]
        assert _view(restored).front_rank == (WYNN, SABLE)
        original = _round(session)
        again = _round(restored)
        assert original.accepted and again.accepted
        assert [event.model_dump() for event in again.events] == [event.model_dump() for event in original.events]


def _everyone(move: str) -> list[BattleDeclaration]:
    """The same defensive move for every stock member, which makes it the formation's."""
    return [_withdraw(member_id, move=move) for member_id in (BRAKK, SABLE, WYNN, ELARA)]


def _assert_shielded(session) -> int:
    """Precondition: Brakk wears a shield. Returns its AC bonus."""
    shield = session.member(BRAKK).inventory.shield
    assert shield is not None
    assert shield.template.ac_bonus == load_equipment().get("shield").ac_bonus
    return shield.template.ac_bonus


def _assert_exposed(result, baseline, shield_bonus: int) -> None:
    """Every attack is the held twin's attack at +2, with Brakk's shield ignored."""
    pairs = list(zip(_attacks(result), _attacks(baseline), strict=True))
    assert pairs, "precondition: the monsters struck"
    for attack, held in pairs:
        assert (attack.attacker_id, attack.defender_id) == (held.attacker_id, held.defender_id)
        assert attack.modifier == held.modifier + 2
        ignored = shield_bonus if attack.defender_id == BRAKK else 0
        assert attack.defender_ac == held.defender_ac + ignored


def _party_moved_off(result):
    """The formation's own move: the first group move to a greater distance than melee range."""
    return next(event for event in result.events if event.code == "battle.group.moved" and event.distance_feet > 5)


@CHUNK
class TestAFormationRetreatIsExposedAllRound:
    """Every declarer retreating is every member retreating, so the SRD's "This round" penalty covers the
    whole formation for the whole round, also when the monsters strike before the party moves off."""

    def test_monsters_who_strike_first_get_the_bonus_and_ignore_the_shield(self):
        retreating = _battle(distance=5, count=4, seed=MONSTERS_FIRST_SEED, shield=True)
        holding = _battle(distance=5, count=4, seed=MONSTERS_FIRST_SEED, shield=True)
        shield_bonus = _assert_shielded(retreating)
        result = _round(retreating, *_everyone("retreat"))
        baseline = _round(holding)
        assert result.accepted, _rejections(result)
        # Preconditions: the goblins win initiative in both, strike at Brakk and Sable before the
        # party moves off, and the round is the formation's retreat.
        assert _monsters_acted_first(retreating, result)
        assert _monsters_acted_first(holding, baseline)
        moved = _party_moved_off(result)
        assert all(_position(result, attack) < _position(result, moved) for attack in _attacks(result))
        assert {attack.defender_id for attack in _attacks(result)} == {BRAKK, SABLE}
        assert "battle.ended.fled" in [event.code for event in result.events]
        _assert_exposed(result, baseline, shield_bonus)


# ---------------------------------------------------------------------- guards: the formation moves as they are


class TestTheFormationMovesStayAsTheyAre:
    """Unmarked guards. A defensive move every declarer makes moves the whole formation, as before the chunk."""

    def test_every_declarer_withdrawing_backs_the_formation_off_at_half_rate(self):
        session = _battle(distance=40)
        declarations = [_withdraw(member_id) for member_id in (BRAKK, SABLE, WYNN, ELARA)]
        result = _round(session, *declarations)
        assert result.accepted, _rejections(result)
        distances = [event.distance_feet for event in result.events if event.code == "battle.group.moved"]
        # The slowest member's encounter rate is 40', so the formation backs off 20'.
        assert any(after == before + 20 for before, after in zip([40, *distances], distances, strict=False))
        assert _swaps(result) == []
        assert _order(session) == [BRAKK, SABLE, WYNN, ELARA]

    def test_every_declarer_retreating_ends_the_battle(self):
        session = _battle(distance=40)
        declarations = [_withdraw(member_id, move="retreat") for member_id in (BRAKK, SABLE, WYNN, ELARA)]
        result = _round(session, *declarations)
        assert result.accepted, _rejections(result)
        assert "battle.ended.fled" in [event.code for event in result.events]
        assert session.battle is None
        assert _swaps(result) == []
        assert _order(session) == [BRAKK, SABLE, WYNN, ELARA]

    @pytest.mark.parametrize("move", ["fighting_withdrawal", "retreat"])
    def test_the_only_able_declarer_moves_the_formation(self, move):
        session = _battle(distance=40)
        for member_id in (SABLE, WYNN, ELARA):
            session.member(member_id).conditions = (
                ActiveCondition(condition=Condition.ASLEEP, effect_id="effect-9999"),
            )
        assert _view(session).declarers == (BRAKK,)
        result = session.execute(ResolveBattleRound(declarations=(_withdraw(BRAKK, move=move),)))
        assert result.accepted, _rejections(result)
        moved = [event.distance_feet for event in result.events if event.code == "battle.group.moved"]
        assert any(after > before for before, after in zip([40, *moved], moved, strict=False))
        assert _swaps(result) == []
        assert _order(session) == [BRAKK, SABLE, WYNN, ELARA]

    def test_a_formation_withdrawal_makes_no_attack(self):
        session = _battle(distance=40)
        armed = _withdraw(SABLE, target_group_id=_group(session), weapon_id="crossbow")
        declarations = [_withdraw(member_id) for member_id in (BRAKK, WYNN, ELARA)]
        result = _round(session, armed, *declarations)
        assert result.accepted, _rejections(result)
        assert SABLE not in {attack.attacker_id for attack in _attacks(result)}
        assert SABLE not in session.battle.fired_last_round

    def test_a_formation_retreat_is_exposed_once_the_party_has_moved(self):
        retreating = _battle(distance=5, count=4, seed=PARTY_FIRST_SEED, shield=True)
        holding = _battle(distance=5, count=4, seed=PARTY_FIRST_SEED, shield=True)
        for game in (retreating, holding):
            game.action_policies[_group(game)] = _StrikeAt(BRAKK)
        shield_bonus = _assert_shielded(retreating)
        result = _round(retreating, *_everyone("retreat"))
        baseline = _round(holding)
        assert result.accepted, _rejections(result)
        # Preconditions: the party wins initiative in both, and the goblins strike after it moves off.
        assert not _monsters_acted_first(retreating, result)
        assert not _monsters_acted_first(holding, baseline)
        moved = _party_moved_off(result)
        assert all(_position(result, attack) > _position(result, moved) for attack in _attacks(result))
        _assert_exposed(result, baseline, shield_bonus)

    def test_reorder_party_stays_refused_in_battle(self):
        session = _battle(distance=5)
        result = session.execute(ReorderParty(order=(WYNN, SABLE, BRAKK, ELARA)))
        assert not result.accepted
        assert [rejection.code for rejection in result.rejections] == ["session.command.wrong_mode"]
        assert _order(session) == [BRAKK, SABLE, WYNN, ELARA]
