"""Acceptance tests for closing and attacking in one round, and the dry run of a round (issue #209).

The OSE SRD's combat page says "Movement and attacking may be combined in the same round", and under spell casting,
"The spell caster cannot move and cast a spell in the same round." The contract these tests check (chunk:
move-and-attack):

- An `attack` may set `move="close"`. The member closes in the party's movement step and attacks in the missile or
  melee step of the same round. The session judges the attack before the round starts, as if every declared move had
  happened: the member's distance after the move decides melee or missile, the default weapon, and reach, and a melee
  attack needs the member in the group's front rank toward the group after every declared move. Width, arrival order,
  and marching order decide the front rank. `combat.attack.out_of_reach` reports the distance the move would leave.
- A `cast` that sets a `move` is refused with `battle.declaration.cast_cannot_move`. A `turn_undead`, `use_item`, or
  `hold` that sets one, and an `attack` with a `fighting_withdrawal` or a `retreat`, get
  `battle.declaration.move_not_combinable`. Both come after the back-stab checks and before the action's own. An attack
  that closes, by a member who can't move, gets `battle.declaration.cannot_move`.
- A melee attack resolves only if the member stands in the group's front rank within 5' at the melee step, or the group
  is fleeing and was within the member's reach when the round was accepted. A member whose close fell short because the
  group ran first makes no attack.
- A spell's range is measured from the caster's own distance to the group.
- `preview_battle_round` runs the same checks over the same projection, for any subset of the declarers, and changes
  nothing. A complete plan it finds no fault with is accepted, and leaves the members where it said.

Most tests keep the monsters still with a hold policy. Each test asserts its own preconditions, and every refused
round is checked to cost nothing. The unmarked tests are guards that pass before and after the chunk.
"""

import pytest

from osrlib.core.effects import ActiveCondition, Condition
from osrlib.core.events import SpellCastEvent
from osrlib.crawl.battle import BattleRoundPreview, group_front_rank, preview_battle_round
from osrlib.crawl.commands import GrantItem, ResolveBattleRound
from positions_fixtures import (
    advance,
    advances,
    attacks_by,
    battle,
    close,
    declare,
    group_id,
    hold_monsters,
    monsters_first,
    moved,
    named,
    plan,
    play,
    position,
    refused,
    seeds,
    state_without_logs,
    strike,
)

MOVE_AND_ATTACK = pytest.mark.xfail(reason="chunk: move-and-attack", strict=True)

NOT_IN_FRONT_RANK = "battle.declaration.not_in_front_rank"
OUT_OF_REACH = "combat.attack.out_of_reach"
CAST_CANNOT_MOVE = "battle.declaration.cast_cannot_move"
MOVE_NOT_COMBINABLE = "battle.declaration.move_not_combinable"

MISSILE_SPELLS = {"Wren": ("magic_missile", "magic_missile")}


def _held(**battle_args):
    game = battle(**battle_args)
    hold_monsters(game)
    return game


def _ids(game, *names: str) -> tuple[str, ...]:
    return tuple(named(game, name) for name in names)


def _missile(game, caster_id: str, **fields):
    goblin = game.encounter.groups[0].monster_ids[0]
    return declare(caster_id, "cast", spell_id="magic_missile", spell_mode="missiles", targets=(goblin,), **fields)


def _cast_at(events, caster_id: str) -> int:
    return position(events, next(e for e in events if isinstance(e, SpellCastEvent) and e.caster_id == caster_id))


def _set_up_the_front(game):
    """Nim closes to 5' of the goblins at 30', and Hild and Osric to 10', in one round."""
    hild, osric, nim = _ids(game, "Hild", "Osric", "Nim")
    play(game, close(game, nim), close(game, hild), close(game, osric))
    assert (advance(game, nim), advance(game, hild), advance(game, osric)) == (25, 20, 20), "precondition: positions"


# ---------------------------------------------------------------------- close and attack


@MOVE_AND_ATTACK
class TestCloseAndAttack:
    def test_a_member_closes_and_strikes_in_one_round(self):
        game = _held(distance=30)
        nim = named(game, "Nim")

        events = play(game, strike(game, nim, move="close"))

        (move,) = moved(events)
        (swing,) = attacks_by(events, nim)
        assert (move.character_id, move.move, move.advance_feet, move.distance_feet) == (nim, "close", 25, 5)
        assert position(events, move) < position(events, swing)
        assert swing.attack_name == "Dagger"

    @pytest.mark.parametrize(("distance", "reaches"), [(25, True), (30, False)])
    def test_a_member_reaches_a_group_up_to_their_rate_plus_five(self, distance, reaches):
        game = _held(distance=distance)
        hild = named(game, "Hild")
        declaration = strike(game, hild, move="close", weapon_id="sword")

        if reaches:
            events = play(game, declaration)
            assert advance(game, hild) == distance - 5
            assert attacks_by(events, hild), "Hild closed and swung"
        else:
            assert refused(game, declaration) == [(OUT_OF_REACH, {"distance_feet": 10})]

    def test_a_member_can_close_and_shoot(self):
        game = _held(distance=60)
        aelin = named(game, "Aelin")

        events = play(game, strike(game, aelin, move="close", weapon_id="short_bow"))

        (move,) = moved(events)
        (shot,) = attacks_by(events, aelin)
        assert (move.advance_feet, move.distance_feet) == (20, 40)
        assert position(events, move) < position(events, shot)

    def test_a_dagger_is_swung_after_a_close_into_reach(self):
        """The melee step comes after the magic step, and the thrown dagger's missile step before it."""
        game = _held(distance=30, spells=MISSILE_SPELLS)
        nim, wren = _ids(game, "Nim", "Wren")

        events = play(game, strike(game, nim, move="close"), _missile(game, wren))

        (swing,) = attacks_by(events, nim)
        assert position(events, swing) > _cast_at(events, wren), "swung in the melee step"

    def test_a_spell_reaches_from_the_casters_own_place(self):
        """Magic missile reaches 150'. Wren closed 40' alone, so Wren stands 150' from goblins 190' off the line."""
        game = _held(distance=190, spells=MISSILE_SPELLS)
        wren = named(game, "Wren")
        play(game, close(game, wren))
        assert advance(game, wren) == 40, "precondition: Wren closed alone"

        events = play(game, _missile(game, wren))

        assert any(isinstance(event, SpellCastEvent) and event.caster_id == wren for event in events)


class TestTheDefaultWeaponWithoutAMove:
    """A guard: a dagger aimed at a group 30' away, with no close, is thrown, before and after the chunk."""

    def test_a_dagger_is_thrown_without_a_close(self):
        game = _held(distance=30, spells=MISSILE_SPELLS)
        nim, wren = _ids(game, "Nim", "Wren")

        events = play(game, strike(game, nim), _missile(game, wren))

        (throw,) = attacks_by(events, nim)
        assert position(events, throw) < _cast_at(events, wren), "thrown in the missile step"


# ---------------------------------------------------------------------- the front rank after the moves


@MOVE_AND_ATTACK
class TestTheFrontRankAfterTheMoves:
    def test_the_last_place_goes_to_the_first_in_marching_order(self):
        """Section 8, round 2: Nim got there first, so Hild takes the last place and Osric waits behind."""
        game = _held(distance=30)
        hild, osric, nim = _ids(game, "Hild", "Osric", "Nim")
        _set_up_the_front(game)

        rejections = refused(game, strike(game, hild, move="close"), strike(game, osric, move="close"))
        assert rejections == [(NOT_IN_FRONT_RANK, {"character": osric})]

        events = play(game, strike(game, hild, move="close"), close(game, osric))

        assert attacks_by(events, hild), "Hild closed and swung"
        assert (advance(game, hild), advance(game, osric)) == (25, 25)
        assert group_front_rank(game, group_id(game)) == (hild, nim)

    def test_two_members_closing_together_fill_an_empty_front_rank(self):
        game = _held(distance=30)
        hild, osric = _ids(game, "Hild", "Osric")
        play(game, close(game, hild), close(game, osric))

        events = play(game, strike(game, hild, move="close"), strike(game, osric, move="close"))

        assert attacks_by(events, hild) and attacks_by(events, osric)

    def test_a_member_falling_back_alone_opens_a_place_the_same_round(self):
        game = _held(distance=5)
        hild, nim = _ids(game, "Hild", "Nim")

        events = play(game, declare(hild, "move", move="fighting_withdrawal"), strike(game, nim))

        assert attacks_by(events, nim), "Nim stepped into the front rank Hild left"


# ---------------------------------------------------------------------- what may combine with a move


@MOVE_AND_ATTACK
class TestWhatCombinesWithAMove:
    @pytest.mark.parametrize("move", ["close", "fighting_withdrawal", "retreat"])
    def test_a_caster_cannot_move(self, move):
        game = _held(distance=30, spells=MISSILE_SPELLS)
        wren = named(game, "Wren")

        rejections = refused(game, _missile(game, wren, move=move, target_group_id=group_id(game)))

        assert rejections == [(CAST_CANNOT_MOVE, {"character": wren})]

    @pytest.mark.parametrize(
        ("member", "action", "move"),
        [
            ("Tam", "turn_undead", "close"),
            ("Nim", "use_item", "close"),
            ("Hild", "hold", "close"),
            ("Hild", "hold", "retreat"),
            ("Hild", "attack", "fighting_withdrawal"),
            ("Hild", "attack", "retreat"),
        ],
    )
    def test_only_an_attack_takes_a_close(self, member, action, move):
        game = _held(distance=30)
        member_id = named(game, member)
        assert game.execute(GrantItem(character_id=named(game, "Nim"), item_id="oil_flask")).accepted
        fields = {"item_id": "oil_flask"} if action == "use_item" else {}

        rejections = refused(game, declare(member_id, action, move=move, target_group_id=group_id(game), **fields))

        assert rejections == [(MOVE_NOT_COMBINABLE, {"character": member_id})]

    def test_the_combination_is_checked_before_the_actions_own_checks(self):
        game = _held(distance=30)
        wren = named(game, "Wren")

        rejections = refused(game, declare(wren, "cast", move="close", target_group_id=group_id(game)))

        assert rejections == [(CAST_CANNOT_MOVE, {"character": wren})], "not battle.declaration.missing_spell"

    def test_an_attack_that_closes_needs_a_member_who_can_move(self):
        game = _held(distance=20)
        hild = named(game, "Hild")
        game.member(hild).conditions = (ActiveCondition(condition=Condition.ENTANGLED, effect_id="effect-9999"),)

        rejections = refused(game, strike(game, hild, move="close", weapon_id="sword"))

        assert rejections == [("battle.declaration.cannot_move", {"character": hild})]


# ---------------------------------------------------------------------- the melee step


@MOVE_AND_ATTACK
def test_a_close_cut_short_by_a_group_that_runs_first_makes_no_attack():
    """The goblins break and run 60' before the party acts, so Nim's close ends 60' short and Nim strikes nothing."""
    for seed in seeds(distance=30):
        game = battle(distance=30, first_seed=seed)
        game.encounter.groups[0].fleeing = True
        nim = named(game, "Nim")

        events = play(game, strike(game, nim, move="close"))
        if not monsters_first(game, events):
            continue

        (move,) = moved(events)
        assert (move.advance_feet, move.distance_feet) == (30, 60)
        assert attacks_by(events, nim) == []
        return


def test_a_member_within_reach_still_strikes_a_group_that_runs_first():
    """A guard: the SRD's retreating combatant gives its opponent the attack, before and after the chunk."""
    for seed in seeds(distance=5):
        game = battle(distance=5, first_seed=seed)
        game.encounter.groups[0].fleeing = True
        hild = named(game, "Hild")

        events = play(game, strike(game, hild, weapon_id="sword"))
        if not monsters_first(game, events):
            continue

        assert game.encounter.groups[0].distance_feet > 5, "precondition: the goblins ran before Hild's turn"
        assert attacks_by(events, hild), "Hild still got the swing"
        return


# ---------------------------------------------------------------------- the dry run


def _flat(rejections) -> list[str]:
    return sorted(repr((rejection.code, sorted(rejection.params.items()))) for rejection in rejections)


@MOVE_AND_ATTACK
class TestTheDryRun:
    def test_a_preview_changes_nothing(self):
        game = _held(distance=30, spells=MISSILE_SPELLS)
        hild, nim, wren = _ids(game, "Hild", "Nim", "Wren")
        before = state_without_logs(game)
        log_size = len(game.command_log), len(game.event_log)

        preview = preview_battle_round(
            game, [strike(game, nim, move="close"), close(game, hild), _missile(game, wren, move="close")]
        )

        assert isinstance(preview, BattleRoundPreview)
        assert state_without_logs(game) == before
        assert (len(game.command_log), len(game.event_log)) == log_size

    def test_an_empty_plan_shows_where_everyone_stands(self):
        game = _held(distance=30)
        nim = named(game, "Nim")
        play(game, close(game, nim))

        preview = preview_battle_round(game, [])

        assert preview.rejections == {}
        assert preview.advance_feet == advances(game)
        assert preview.front_rank == {group_id(game): (nim,)}

    def test_a_partial_plan_gets_no_roster_rejection(self):
        game = _held(distance=30)
        hild = named(game, "Hild")

        preview = preview_battle_round(game, [close(game, hild)])

        assert preview.rejections == {hild: ()}
        assert preview.advance_feet == {**advances(game), hild: 20}

    def test_the_preview_refuses_what_the_round_refuses(self):
        game = _held(distance=30, spells=MISSILE_SPELLS)
        hild, osric, wren, tam = _ids(game, "Hild", "Osric", "Wren", "Tam")
        declarations = plan(
            game,
            strike(game, hild, move="close", weapon_id="sword"),
            strike(game, osric, weapon_id="sword"),
            _missile(game, wren, move="close", target_group_id=group_id(game)),
            declare(tam, "hold", move="close", target_group_id=group_id(game)),
        )

        preview = preview_battle_round(game, declarations)
        rejections = refused(game, *declarations)

        assert set(preview.rejections) == {declaration.character_id for declaration in declarations}
        assert all(preview.rejections[member_id] for member_id in (hild, osric, wren, tam))
        flattened = [rejection for found in preview.rejections.values() for rejection in found]
        assert _flat(flattened) == sorted(repr((code, sorted(params.items()))) for code, params in rejections)

    def test_a_complete_plan_it_finds_no_fault_with_is_accepted_and_leaves_the_members_where_it_said(self):
        game = battle(distance=30, count=4, surprised=True, spells=MISSILE_SPELLS)
        hild, osric, nim, aelin, wren = _ids(game, "Hild", "Osric", "Nim", "Aelin", "Wren")
        declarations = plan(
            game,
            close(game, hild),
            close(game, osric),
            strike(game, nim, move="close", back_stab=True),
            strike(game, aelin, weapon_id="short_bow"),
            _missile(game, wren),
        )

        preview = preview_battle_round(game, declarations)
        assert all(found == () for found in preview.rejections.values()), preview.rejections
        result = game.execute(ResolveBattleRound(declarations=declarations))

        assert result.accepted, [rejection.code for rejection in result.rejections]
        assert advances(game) == preview.advance_feet
        assert {group_id(game): group_front_rank(game, group_id(game))} == preview.front_rank
        assert preview.advance_feet[nim] == 25 and preview.front_rank[group_id(game)] == (nim,)

    def test_the_preview_greys_out_the_close_and_attack_the_last_place_takes(self):
        game = _held(distance=30)
        hild, osric, nim = _ids(game, "Hild", "Osric", "Nim")
        _set_up_the_front(game)

        both_attack = preview_battle_round(game, [strike(game, hild, move="close"), strike(game, osric, move="close")])
        osric_closes = preview_battle_round(game, [strike(game, hild, move="close"), close(game, osric)])

        assert both_attack.rejections[hild] == ()
        assert [(r.code, dict(r.params)) for r in both_attack.rejections[osric]] == [
            (NOT_IN_FRONT_RANK, {"character": osric})
        ]
        assert osric_closes.rejections == {hild: (), osric: ()}
        assert osric_closes.front_rank == {group_id(game): (hild, nim)}

    def test_the_order_of_the_plan_does_not_matter(self):
        game = _held(distance=30)
        hild, osric = _ids(game, "Hild", "Osric")
        _set_up_the_front(game)
        first, second = strike(game, hild, move="close"), strike(game, osric, move="close")

        assert preview_battle_round(game, [second, first]) == preview_battle_round(game, [first, second])

    def test_a_defensive_move_is_the_formations_only_when_the_whole_plan_makes_it(self):
        game = _held(distance=30)
        hild = named(game, "Hild")
        everyone = [declare(member.id, "move", move="fighting_withdrawal") for member in game.party.members]

        alone = preview_battle_round(game, everyone[:1])
        together = preview_battle_round(game, everyone)

        assert alone.advance_feet[hild] == -10
        assert set(together.advance_feet.values()) == {0}, "a formation move moves the groups, not the members"

    def test_a_bad_call_raises_and_changes_nothing(self):
        game = _held(distance=30)
        hild = named(game, "Hild")
        before = state_without_logs(game)

        with pytest.raises(ValueError):
            preview_battle_round(game, [declare("character-9999")])
        with pytest.raises(ValueError):
            preview_battle_round(game, [declare(hild), close(game, hild)])
        assert state_without_logs(game) == before

    def test_a_session_with_no_battle_is_a_bad_call(self):
        game = battle(engage=False)
        with pytest.raises(ValueError):
            preview_battle_round(game, [])


def test_the_preview_result_has_the_fields_a_battle_screen_reads():
    """A guard on the stub's shape, which passes before and after the chunk."""
    assert set(BattleRoundPreview.model_fields) == {"rejections", "advance_feet", "front_rank"}
    assert BattleRoundPreview.model_config.get("frozen") is True
