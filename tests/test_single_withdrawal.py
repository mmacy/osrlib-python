"""A fighting withdrawal or a retreat that one party member makes alone (issues #171 and #209).

The OSE SRD describes both defensive moves per combatant: "The combatant moves backwards at up to
half their encounter movement rate. There must be clear space behind the combatant." and "The
combatant turns and flees from melee, moving up to their full encounter movement rate. This
round: the retreating combatant may not attack; the opponent gains a +2 bonus to all attacks
against the retreating combatant and ignores any AC bonus due to the combatant's shield (if
applicable)." osrlib keeps the formation move for a round every declarer agrees on, and makes
every other defensive move the declaring member's own. The contract, which the adaptations
register states under "The battle round":

- A member who withdraws alone moves back on the range track by themselves, at half their
  encounter rate for a fighting withdrawal or their full rate for a retreat, doubled by their own
  *haste*. Their advance goes down, below the party line if need be, and a `MemberMovedEvent`
  reports it. Any member can do it, wherever they stand, and needs nobody to step up.
- The member can name another member in `BattleDeclaration.replacement_id`. The two trade places
  in the marching order right after the move, a `MemberWithdrewEvent` reports it, and the order
  stays changed. The trade moves nobody. Marching order decides who stands in front among members
  at the same distance who got there in the same round, so naming a member beside the others
  brings that member to the front once the withdrawing member steps out.
- The named member has to be another declarer who isn't withdrawing too and isn't named twice,
  or the round is refused with `battle.declaration.replacement_unavailable`. Their rank, their
  rate, their cast, and whether they can move don't matter, because the trade isn't a move.
- A retreat's penalties fall on each member who retreats, alone or with the formation, and last the
  whole round, whichever side acts first.
- A member who can no longer move when the movement step comes doesn't fall back and doesn't trade.
- A withdrawing member makes no attack.

The classes marked `chunk: positions` fail until that chunk is built. The unmarked classes are
guards that pass before and after it, among them the classes at the end, which check that the
formation moves stay as they were.
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
from osrlib.crawl.events import MemberMovedEvent, MemberWithdrewEvent
from osrlib.crawl.session import GameSession
from osrlib.data import load_classes, load_equipment, load_spells
from osrlib.persistence import load_game, replay_game, save_game, session_state

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


# ---------------------------------------------------------------------- the move and the trade

POSITIONS = pytest.mark.xfail(reason="chunk: positions", strict=True)


def _advance(session, member_id: str) -> int:
    return session.battle.advance_feet.get(member_id, 0)


def _front(session) -> tuple[str, ...]:
    """The members who can fight the first group hand to hand, as the session reports them."""
    return battle_module.group_front_rank(session, _group(session))


def _moves(result) -> list[MemberMovedEvent]:
    return [event for event in result.events if isinstance(event, MemberMovedEvent)]


@POSITIONS
class TestAFightingWithdrawalAlone:
    """One member falls back at half rate, the named member takes their marching place, and the party stays put."""

    def test_the_member_falls_back_and_trades_places_with_the_named_member(self):
        session = _battle(distance=5)
        assert _front(session) == (BRAKK, SABLE)
        result = _round(session, _withdraw(BRAKK, WYNN))
        assert result.accepted, _rejections(result)
        assert _advance(session, BRAKK) == -20
        assert _order(session) == [WYNN, SABLE, BRAKK, ELARA]
        assert _front(session) == (WYNN, SABLE)
        [move] = _moves(result)
        [swap] = _swaps(result)
        assert (move.character_id, move.move, move.distance_feet) == (BRAKK, "fighting_withdrawal", 25)
        assert (swap.code, swap.character_id, swap.replacement_id) == ("battle.member.withdrew", BRAKK, WYNN)
        assert _position(result, swap) == _position(result, move) + 1
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
        assert _advance(session, BRAKK) == -20

    def test_a_member_two_ranks_back_takes_the_marching_place(self):
        session = _battle(roster=SIX, distance=5)
        result = _round(session, _withdraw(BRAKK, GARR))
        assert result.accepted, _rejections(result)
        assert _order(session) == [GARR, SABLE, WYNN, ELARA, BRAKK, HALE]
        assert _front(session) == (GARR, SABLE)
        assert _advance(session, GARR) == 0, "the trade moves nobody"

    def test_the_whole_front_rank_can_fall_back_together(self):
        session = _battle(distance=5)
        result = _round(session, _withdraw(SABLE, ELARA), _withdraw(BRAKK, WYNN))
        assert result.accepted, _rejections(result)
        assert _order(session) == [WYNN, ELARA, BRAKK, SABLE]
        # One trade per withdrawal, in the marching order of the members who withdrew.
        assert [(swap.character_id, swap.replacement_id) for swap in _swaps(result)] == [(BRAKK, WYNN), (SABLE, ELARA)]
        assert _front(session) == (WYNN, ELARA)

    def test_a_withdrawal_shares_a_round_with_a_close_and_comes_first(self):
        session = _battle(distance=40)
        close = BattleDeclaration(character_id=SABLE, action="move", move="close", target_group_id=_group(session))
        result = _round(session, _withdraw(BRAKK, WYNN), close)
        assert result.accepted, _rejections(result)
        assert _order(session) == [WYNN, SABLE, BRAKK, ELARA]
        assert [(move.character_id, move.move) for move in _moves(result)] == [
            (BRAKK, "fighting_withdrawal"),
            (SABLE, "close"),
        ]
        [swap] = _swaps(result)
        assert _position(result, swap) < _position(result, _moves(result)[1])
        assert (_advance(session, BRAKK), _advance(session, SABLE)) == (-20, 35)


@POSITIONS
class TestARetreatAlone:
    def test_the_member_retreats_alone_and_the_battle_goes_on(self):
        session = _battle(distance=5)
        result = _round(session, _withdraw(BRAKK, WYNN, move="retreat"))
        assert result.accepted, _rejections(result)
        assert _order(session) == [WYNN, SABLE, BRAKK, ELARA]
        assert _advance(session, BRAKK) == -40
        [swap] = _swaps(result)
        assert (swap.code, swap.character_id, swap.replacement_id) == ("battle.member.retreated", BRAKK, WYNN)
        codes = [event.code for event in result.events]
        assert "battle.ended.fled" not in codes
        assert session.mode is SessionMode.BATTLE
        assert session.encounter.pursuit is None
        assert session.encounter.groups[0].distance_feet == 5


class TestARetreatAloneIsExposed:
    """Guards: the retreat's penalties and the melee it escapes, before and after the chunk."""

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


@POSITIONS
class TestHowFarAMemberFallsBack:
    """A member falls back at half their encounter rate, or their full rate for a retreat, and names anyone."""

    @pytest.mark.parametrize(
        ("move", "laden", "feet"),
        [
            ("fighting_withdrawal", False, 20),
            ("retreat", False, 40),
            ("fighting_withdrawal", True, 5),
            ("retreat", True, 10),
        ],
    )
    def test_the_member_falls_back_at_half_or_full_rate(self, move, laden, feet):
        session = _battle(roster=SIX, distance=5, laden=(BRAKK,) if laden else ())
        result = _round(session, _withdraw(BRAKK, move=move))
        assert result.accepted, _rejections(result)
        assert _advance(session, BRAKK) == -feet

    def test_haste_doubles_the_withdrawal(self):
        session = _battle(roster=SIX, distance=5, laden=(BRAKK,))
        _attach(
            session, EffectDefinition(kind="haste", params={"attacks_multiplier": 2, "movement_multiplier": 2}), BRAKK
        )
        result = _round(session, _withdraw(BRAKK))
        assert result.accepted, _rejections(result)
        assert _advance(session, BRAKK) == -10

    def test_a_laden_member_names_a_member_three_ranks_back(self):
        session = _battle(roster=EIGHT, distance=5, laden=(BRAKK, IVER))
        result = _round(session, _withdraw(BRAKK, IVER))
        assert result.accepted, _rejections(result)
        assert _order(session)[0] == IVER
        assert _order(session)[6] == BRAKK


# ---------------------------------------------------------------------- the named member


@POSITIONS
class TestWhoCanBeNamed:
    """Any other declarer who isn't falling back too, because the trade isn't a move."""

    @pytest.mark.parametrize("move", ["fighting_withdrawal", "retreat"])
    def test_a_member_who_names_nobody_falls_back_alone(self, move):
        session = _battle(distance=5)
        result = _round(session, _withdraw(BRAKK, move=move))
        assert result.accepted, _rejections(result)
        assert _swaps(result) == []
        assert _order(session) == [BRAKK, SABLE, WYNN, ELARA]
        assert _front(session) == (SABLE, WYNN)

    def test_a_member_behind_the_front_rank_can_fall_back_alone(self):
        session = _battle(distance=5)
        result = _round(session, _withdraw(WYNN, ELARA))
        assert result.accepted, _rejections(result)
        assert _order(session) == [BRAKK, SABLE, ELARA, WYNN]

    def test_a_member_in_the_front_rank_can_be_named(self):
        session = _battle(distance=5)
        result = _round(session, _withdraw(BRAKK, SABLE))
        assert result.accepted, _rejections(result)
        assert _order(session)[:2] == [SABLE, BRAKK]

    def test_a_member_who_cannot_move_can_be_named(self):
        session = _battle(distance=5)
        session.member(WYNN).conditions = (ActiveCondition(condition=Condition.ENTANGLED, effect_id="effect-9999"),)
        assert WYNN in _view(session).immobile
        result = _round(session, _withdraw(BRAKK, WYNN))
        assert result.accepted, _rejections(result)
        assert _order(session)[0] == WYNN

    def test_a_member_who_casts_can_be_named(self):
        session = _battle(distance=5)
        elara = session.member(ELARA)
        memorize_spells(elara, load_classes().get("magic_user"), load_spells(), [MemorizedSpell(spell_id="sleep")])
        cast = BattleDeclaration(
            character_id=ELARA, action="cast", spell_id="sleep", spell_mode="hd_budget", target_group_id=_group(session)
        )
        result = _round(session, _withdraw(BRAKK, ELARA), cast)
        assert result.accepted, _rejections(result)
        assert _order(session)[0] == ELARA

    def test_a_member_who_withdraws_too_cannot_be_named(self):
        session = _battle(distance=5)
        before = _snapshot(session)
        result = _round(session, _withdraw(BRAKK, WYNN), _withdraw(WYNN, ELARA))
        assert _rejections(result) == [
            ("battle.declaration.replacement_unavailable", {"character": BRAKK, "replacement": WYNN}),
        ]
        _assert_refused_cleanly(session, result, before)

    def test_without_the_width_limit_a_member_falls_back_and_trades_the_same(self):
        session = _battle(distance=5, ruleset=Ruleset(formation_width_limit=False))
        named = _round(session, _withdraw(BRAKK, WYNN))
        assert named.accepted, _rejections(named)
        assert _order(session)[0] == WYNN
        unnamed = _round(session, _withdraw(SABLE))
        assert unnamed.accepted, _rejections(unnamed)


class TestWhoCannotBeNamed:
    """Guards: refusals that stand before and after the chunk, each costing nothing."""

    @pytest.mark.parametrize("named", [BRAKK, "character-9999"])
    def test_the_named_member_has_to_be_another_member(self, named):
        session = _battle(distance=5)
        before = _snapshot(session)
        result = _round(session, _withdraw(BRAKK, named))
        assert _rejections(result) == [
            ("battle.declaration.replacement_unavailable", {"character": BRAKK, "replacement": named})
        ]
        _assert_refused_cleanly(session, result, before)

    def test_a_dead_member_cannot_be_named(self):
        session = _battle(distance=5)
        deal_damage(session.member(WYNN), 999, source=DamageSource())
        assert has_condition(session.member(WYNN), Condition.DEAD)
        result = _round(session, _withdraw(BRAKK, WYNN))
        assert _rejections(result) == [
            ("battle.declaration.replacement_unavailable", {"character": BRAKK, "replacement": WYNN})
        ]

    def test_a_sleeping_member_cannot_be_named(self):
        session = _battle(distance=5)
        session.member(WYNN).conditions = (ActiveCondition(condition=Condition.ASLEEP, effect_id="effect-9999"),)
        assert WYNN not in _view(session).declarers
        result = _round(session, _withdraw(BRAKK, WYNN))
        assert _rejections(result) == [
            ("battle.declaration.replacement_unavailable", {"character": BRAKK, "replacement": WYNN})
        ]

    def test_two_members_cannot_name_one_member(self):
        session = _battle(distance=5)
        result = _round(session, _withdraw(BRAKK, WYNN), _withdraw(SABLE, WYNN))
        # The member earlier in marching order keeps the named member.
        assert _rejections(result) == [
            ("battle.declaration.replacement_unavailable", {"character": SABLE, "replacement": WYNN})
        ]

    def test_a_formation_move_has_nobody_to_name(self):
        session = _battle(distance=5)
        declarations = [_withdraw(member_id) for member_id in (SABLE, WYNN, ELARA)]
        result = _round(session, _withdraw(BRAKK, WYNN), *declarations)
        # Every declarer withdraws, so the formation moves, and the named member is withdrawing too.
        assert _rejections(result) == [
            ("battle.declaration.replacement_unavailable", {"character": BRAKK, "replacement": WYNN})
        ]


# ---------------------------------------------------------------------- melee around the trade


class TestSteppingUpIntoMelee:
    """Guards: from the movement step on, the named member stands in front, and the withdrawn member doesn't."""

    def test_the_named_member_may_strike_the_round_they_step_up(self):
        session = _battle(distance=5)
        assert WYNN not in _view(session).front_rank
        strike = BattleDeclaration(character_id=WYNN, action="attack", target_group_id=_group(session))
        result = _round(session, _withdraw(BRAKK, WYNN), strike)
        assert result.accepted, _rejections(result)
        [swap] = _swaps(result)
        wynn_attacks = [attack for attack in _attacks(result) if attack.attacker_id == WYNN]
        assert wynn_attacks
        assert all(_position(result, attack) > _position(result, swap) for attack in wynn_attacks)

    def test_a_member_left_behind_the_front_rank_still_cannot_strike(self):
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
        # Brakk can't fall back, so there's no trade either, and Wynn stays behind the front rank.
        assert _swaps(result) == []
        assert _order(session) == [BRAKK, SABLE, WYNN, ELARA]
        # Wynn never reached the front rank, so the melee attack Wynn declared doesn't resolve.
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


@POSITIONS
class TestTheTradeIsNotAMove:
    """A named member who becomes paralysed after the round was accepted still trades places, because the trade
    moves nobody. The paralysis lands as initiative is rolled, through a wrapper around the battle module's
    `roll_initiative`, which the round calls once, after it accepts the declarations and before either side acts.
    """

    def test_a_named_member_paralysed_after_the_round_was_accepted_still_trades(self, monkeypatch):
        original = battle_module.roll_initiative

        def paralysis_then_initiative(*args, **kwargs):
            wynn = session.member(WYNN)
            wynn.conditions = (*wynn.conditions, ActiveCondition(condition=Condition.PARALYSED, effect_id=None))
            return original(*args, **kwargs)

        session = _battle(distance=40)
        monkeypatch.setattr(battle_module, "roll_initiative", paralysis_then_initiative)
        result = _round(session, _withdraw(BRAKK, WYNN))
        assert result.accepted, _rejections(result)
        assert has_condition(session.member(WYNN), Condition.PARALYSED), "precondition: Wynn was paralysed mid-round"
        assert [(swap.character_id, swap.replacement_id) for swap in _swaps(result)] == [(BRAKK, WYNN)]
        assert _order(session) == [WYNN, SABLE, BRAKK, ELARA]


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


class TestTheViewsAfterTheTrade:
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


class TestDeterminism:
    def test_a_refused_withdrawal_costs_nothing(self):
        session = _battle(distance=5)
        before = _snapshot(session)
        distance = session.encounter.groups[0].distance_feet
        result = _round(session, _withdraw(BRAKK, BRAKK))
        assert _rejections(result) == [
            ("battle.declaration.replacement_unavailable", {"character": BRAKK, "replacement": BRAKK})
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
    """A defensive move every declarer makes moves the whole formation, as before."""

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
