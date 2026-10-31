"""Acceptance tests for each party member's own place on the range track (issue #209, chunk: positions).

The range track is how a battle measures distance. Each monster group keeps a distance from the party line, where the
party stood when the encounter opened. Each member now has an advance, in feet forward of that line, in
`BattleState.advance_feet`, and a member's distance to a group is the group's distance minus the member's advance.
The contract these tests check:

- A `close` moves only the member who declares it, at that member's encounter rate, doubled by *haste* on them. It
  stops 5' from the group it names, and short of 5' from every other group still in the fight. A member already within
  5' doesn't move. Each move a member makes alone reports a `MemberMovedEvent`, and moves nobody else.
- A `fighting_withdrawal` or a `retreat` that not every declarer makes moves that member alone, back at half or full
  rate, below the line if need be. Any member can make one, with no replacement. A retreat alone doesn't end the battle.
- `replacement_id` is a trade of marching-order places, reported by a `MemberWithdrewEvent` right after the move.
- A formation move, every declarer making the same defensive move, moves the groups and no member, as before. A
  formation retreat's pursuit starts at the smallest distance between a living member and a group that can follow.
- `BattleState.arrived_round` records the round each member reached their advance. The ranks toward a group are the
  living members sorted by distance and cut into rows of the formation width, never mixing distances, with the earlier
  arrival in front and marching order settling a tie. `group_front_rank` is the front row's members within 5'.
- Melee declarations in a round with no moves are judged against those ranks and distances.
- Positions end with the battle, are saved with the session, and replay from the command log.

Most tests keep the monsters still with a hold policy, so they check the party's movement alone. Each test asserts its
own preconditions. The classes marked `chunk: positions` fail until that chunk is built. The unmarked tests are guards
that pass before and after it.
"""

import sys
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from osrlib.core.effects import ActiveCondition, Condition, EffectDefinition
from osrlib.core.events import Visibility
from osrlib.core.ruleset import Ruleset
from osrlib.crawl import battle as battle_module
from osrlib.crawl.battle import BattleState, group_front_rank, member_distance_feet
from osrlib.crawl.commands import AnyCommand, ResolveBattleRound, SessionMode, SpawnMonsters
from osrlib.crawl.events import MemberMovedEvent, parse_any_event
from osrlib.messages import format_message
from osrlib.persistence import load_game, replay_game, save_game, session_state
from positions_fixtures import (
    HASTE,
    RATE,
    WALK,
    advance,
    advances,
    attach,
    battle,
    character,
    close,
    declare,
    group_id,
    group_moves,
    hold_monsters,
    moved,
    named,
    play,
    refused,
    release_monsters,
    round_,
    strike,
    trades,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.docs.rejection_scan import scan_rejection_codes  # noqa: E402

POSITIONS = pytest.mark.xfail(reason="chunk: positions", strict=True)


def _held(**battle_args):
    """A battle whose monsters hold still, so only the party moves."""
    game = battle(**battle_args)
    hold_monsters(game)
    return game


def _ids(game, *names: str) -> tuple[str, ...]:
    return tuple(named(game, name) for name in names)


def _withdraw(member_id: str, move: str = "fighting_withdrawal", replacement_id: str | None = None):
    return declare(member_id, "move", move=move, replacement_id=replacement_id)


def _order(game) -> list[str]:
    return [member.name for member in game.party.members]


# ---------------------------------------------------------------------- the fields


class TestTheFields:
    """The new fields, event, and names. These pass before and after the chunk, because the stub ships them."""

    def test_a_battle_opens_with_everyone_on_the_line(self):
        game = battle()
        assert game.battle.advance_feet == {}
        assert game.battle.arrived_round == {}

    def test_a_battle_state_saved_before_the_fields_existed_loads_with_everyone_on_the_line(self):
        state = BattleState.model_validate({"started_round": 3, "round": 2})
        assert state.advance_feet == {}
        assert state.arrived_round == {}

    def test_the_fields_round_trip_through_json(self):
        state = BattleState(started_round=0, advance_feet={"character-0003": 25}, arrived_round={"character-0003": 1})
        assert BattleState.model_validate(state.model_dump(mode="json")) == state

    @pytest.mark.parametrize(
        ("move", "verb"), [("close", "closes"), ("fighting_withdrawal", "falls back"), ("retreat", "retreats")]
    )
    def test_the_member_moved_event_round_trips_and_formats(self, move, verb):
        event = MemberMovedEvent(
            character_id="character-0003", move=move, advance_feet=25, group_id="group-0001", distance_feet=5
        )
        assert event.code == "battle.member.moved"
        assert event.visibility is Visibility.PLAYER
        assert parse_any_event(event.model_dump(mode="json")) == event
        line = format_message(event)
        assert "character-0003" in line and verb in line and "5'" in line and "group-0001" in line

    def test_an_attack_that_closes_round_trips_through_its_json(self):
        command = ResolveBattleRound(
            declarations=(
                declare("character-0003", "attack", move="close", target_group_id="group-0001", back_stab=True),
            )
        )
        restored = TypeAdapter(AnyCommand).validate_python(command.model_dump(mode="json"))
        assert restored == command

    def test_the_new_names_are_public(self):
        for name in ("member_distance_feet", "group_front_rank", "preview_battle_round", "BattleRoundPreview"):
            assert name in battle_module.__all__


# ---------------------------------------------------------------------- a close is the member's own


@POSITIONS
class TestACloseMovesOnlyTheMember:
    def test_a_close_moves_only_the_declarer_at_their_own_rate(self):
        game = _held(distance=40)
        hild = named(game, "Hild")

        events = play(game, close(game, hild))

        assert advances(game) == {member_id: (20 if member_id == hild else 0) for member_id in advances(game)}
        assert game.encounter.groups[0].distance_feet == 40, "the group stays where it was"
        assert group_moves(events) == []
        (event,) = moved(events)
        assert (event.character_id, event.move, event.advance_feet) == (hild, "close", 20)
        assert (event.group_id, event.distance_feet) == (group_id(game), 20)

    def test_each_member_closes_at_their_own_rate(self):
        game = _held(distance=60)
        members = [member.id for member in game.party.members]

        events = play(game, *(close(game, member_id) for member_id in members))

        expected = {member.id: RATE[member.name] for member in game.party.members}
        assert advances(game) == expected
        assert [event.character_id for event in moved(events)] == members, "in marching order"
        assert [event.distance_feet for event in moved(events)] == [60 - expected[member_id] for member_id in members]
        assert game.encounter.groups[0].distance_feet == 60

    def test_a_close_stops_five_feet_from_the_group(self):
        game = _held(distance=30)
        wren = named(game, "Wren")

        (event,) = moved(play(game, close(game, wren)))

        assert advance(game, wren) == 25
        assert (event.advance_feet, event.distance_feet) == (25, 5)

    def test_a_member_already_within_five_feet_neither_moves_nor_reports(self):
        game = _held(distance=5)
        hild = named(game, "Hild")

        events = play(game, close(game, hild))

        assert moved(events) == []
        assert group_moves(events) == []
        assert advance(game, hild) == 0
        assert game.battle.arrived_round == {}

    def test_haste_doubles_the_members_own_close(self):
        game = _held(distance=80)
        nim, hild = _ids(game, "Nim", "Hild")
        attach(game, HASTE, nim)

        play(game, close(game, nim), close(game, hild))

        assert advance(game, nim) == 60
        assert advance(game, hild) == 20, "haste on one member speeds nobody else"

    def test_a_close_stops_short_of_every_other_group(self):
        game = battle(distance=60)
        assert game.execute(SpawnMonsters(template_id="kobold", count_fixed=2, distance_feet=30)).accepted
        assert [group.distance_feet for group in game.encounter.groups] == [60, 30], "precondition: two groups"
        hold_monsters(game)
        goblins, kobolds = group_id(game, 0), group_id(game, 1)
        wren = named(game, "Wren")

        (event,) = moved(play(game, close(game, wren, group=goblins)))

        assert advance(game, wren) == 25
        assert (event.group_id, event.distance_feet) == (goblins, 35)
        assert member_distance_feet(game, wren, kobolds) == 5

    def test_the_round_a_member_arrives_is_recorded(self):
        game = _held(distance=60)
        nim, hild = _ids(game, "Nim", "Hild")

        play(game, close(game, nim))
        play(game, close(game, hild))

        assert game.battle.arrived_round == {nim: 1, hild: 2}

    def test_a_member_who_cannot_move_further_keeps_their_arrival_round(self):
        game = _held(distance=30)
        nim = named(game, "Nim")
        play(game, close(game, nim))
        assert advance(game, nim) == 25, "precondition: Nim stands 5' from the goblins"

        events = play(game, close(game, nim))

        assert moved(events) == []
        assert game.battle.arrived_round == {nim: 1}

    def test_the_distance_helper_measures_each_member(self):
        game = _held(distance=50)
        nim, wren = _ids(game, "Nim", "Wren")
        play(game, close(game, nim), close(game, wren))

        distances = {member.id: member_distance_feet(game, member.id, group_id(game)) for member in game.party.members}

        assert distances == {member_id: 50 - feet for member_id, feet in advances(game).items()}
        assert (distances[nim], distances[wren]) == (20, 10)

    def test_the_helpers_refuse_a_name_they_do_not_know(self):
        game = _held(distance=30)
        hild = named(game, "Hild")
        with pytest.raises(ValueError):
            member_distance_feet(game, "character-9999", group_id(game))
        with pytest.raises(ValueError):
            member_distance_feet(game, hild, "group-9999")
        with pytest.raises(ValueError):
            group_front_rank(game, "group-9999")

    def test_the_helpers_refuse_a_session_with_no_battle(self):
        game = battle(engage=False)
        assert game.mode is SessionMode.ENCOUNTER, "precondition: no battle yet"
        with pytest.raises(ValueError):
            member_distance_feet(game, named(game, "Hild"), group_id(game))
        with pytest.raises(ValueError):
            group_front_rank(game, group_id(game))


# ---------------------------------------------------------------------- falling back alone


@POSITIONS
class TestFallingBackAlone:
    def test_a_fighting_withdrawal_alone_moves_the_member_back_at_half_rate(self):
        game = _held(distance=40)
        wren = named(game, "Wren")

        events = play(game, _withdraw(wren))

        assert advance(game, wren) == -20
        (event,) = moved(events)
        assert (event.character_id, event.move, event.advance_feet) == (wren, "fighting_withdrawal", -20)
        assert (event.group_id, event.distance_feet) == (group_id(game), 60)
        assert trades(events) == []
        assert game.encounter.groups[0].distance_feet == 40, "the party didn't back away as one body"

    def test_a_retreat_alone_moves_the_member_back_at_full_rate_and_the_battle_goes_on(self):
        game = _held(distance=40)
        wren = named(game, "Wren")

        events = play(game, _withdraw(wren, "retreat"))

        assert advance(game, wren) == -40
        assert [event.move for event in moved(events)] == ["retreat"]
        assert "battle.ended.fled" not in [event.code for event in events]
        assert game.mode is SessionMode.BATTLE
        assert game.encounter.pursuit is None

    def test_any_member_may_fall_back_alone(self):
        game = _held(distance=5)
        tam = named(game, "Tam")

        play(game, _withdraw(tam))

        assert advance(game, tam) == -10

    def test_haste_doubles_a_withdrawal(self):
        game = _held(distance=5)
        hild = named(game, "Hild")
        attach(game, HASTE, hild)

        play(game, _withdraw(hild))

        assert advance(game, hild) == -20

    def test_a_majority_falling_back_does_not_carry_the_rest(self):
        game = _held(distance=40)
        tam = named(game, "Tam")
        retreating = [member for member in game.party.members if member.id != tam]

        events = play(game, *(_withdraw(member.id, "retreat") for member in retreating))

        assert advances(game) == {**{member.id: -RATE[member.name] for member in retreating}, tam: 0}
        assert group_moves(events) == []
        assert game.mode is SessionMode.BATTLE

    def test_the_moves_back_come_before_the_closes(self):
        game = _held(distance=40)
        hild, tam = _ids(game, "Hild", "Tam")

        events = play(game, close(game, hild), _withdraw(tam))

        assert [(event.character_id, event.move) for event in moved(events)] == [
            (tam, "fighting_withdrawal"),
            (hild, "close"),
        ]

    def test_a_move_back_reports_the_nearest_group(self):
        game = battle(distance=60)
        assert game.execute(SpawnMonsters(template_id="kobold", count_fixed=2, distance_feet=30)).accepted
        hold_monsters(game)
        wren = named(game, "Wren")

        (event,) = moved(play(game, _withdraw(wren)))

        assert (event.group_id, event.distance_feet) == (group_id(game, 1), 50)


# ---------------------------------------------------------------------- the marching-order trade


@POSITIONS
class TestTheTrade:
    def test_the_named_member_trades_places_in_the_marching_order(self):
        game = _held(distance=5)
        hild, aelin, osric = _ids(game, "Hild", "Aelin", "Osric")

        events = play(game, _withdraw(hild, replacement_id=aelin))

        (move,) = moved(events)
        (trade,) = trades(events)
        assert (move.character_id, move.advance_feet, move.distance_feet) == (hild, -10, 15)
        assert (trade.code, trade.character_id, trade.replacement_id) == ("battle.member.withdrew", hild, aelin)
        assert events.index(trade) == events.index(move) + 1, "the trade comes right after the move"
        assert _order(game) == ["Aelin", "Osric", "Nim", "Hild", "Wren", "Tam"]
        assert advance(game, aelin) == 0, "the trade moves nobody"
        assert group_front_rank(game, group_id(game)) == (aelin, osric)

    def test_without_a_trade_the_next_nearest_members_fill_the_front(self):
        game = _held(distance=5)
        hild, osric, nim = _ids(game, "Hild", "Osric", "Nim")

        events = play(game, _withdraw(hild))

        assert trades(events) == []
        assert _order(game) == [name for name, _ in WALK]
        assert group_front_rank(game, group_id(game)) == (osric, nim)

    def test_a_retreat_alone_trades_with_its_own_code(self):
        game = _held(distance=5)
        hild, tam = _ids(game, "Hild", "Tam")

        (trade,) = trades(play(game, _withdraw(hild, "retreat", replacement_id=tam)))

        assert trade.code == "battle.member.retreated"
        assert advance(game, hild) == -20

    def test_a_member_in_front_may_be_named(self):
        game = _held(distance=5)
        hild, osric = _ids(game, "Hild", "Osric")

        play(game, _withdraw(hild, replacement_id=osric))

        assert _order(game)[:2] == ["Osric", "Hild"]

    def test_a_member_who_casts_may_be_named(self):
        game = _held(distance=5, spells={"Wren": ("magic_missile",)})
        hild, wren = _ids(game, "Hild", "Wren")
        goblin = game.encounter.groups[0].monster_ids[0]
        missile = declare(wren, "cast", spell_id="magic_missile", spell_mode="missiles", targets=(goblin,))

        play(game, _withdraw(hild, replacement_id=wren), missile)

        assert _order(game)[0] == "Wren"

    def test_a_member_who_cannot_move_may_be_named(self):
        game = _held(distance=5)
        hild, aelin = _ids(game, "Hild", "Aelin")
        game.member(aelin).conditions = (ActiveCondition(condition=Condition.ENTANGLED, effect_id="effect-9999"),)
        assert aelin in game.view(Visibility.PLAYER).encounter.immobile, "precondition: Aelin is held fast"

        play(game, _withdraw(hild, replacement_id=aelin))

        assert _order(game)[0] == "Aelin"

    def test_a_member_who_falls_back_too_cannot_be_named(self):
        game = _held(distance=5)
        hild, osric = _ids(game, "Hild", "Osric")

        rejections = refused(game, _withdraw(hild, replacement_id=osric), _withdraw(osric))

        assert rejections == [
            ("battle.declaration.replacement_unavailable", {"character": hild, "replacement": osric}),
        ]

    def test_without_the_width_limit_a_trade_is_still_a_trade(self):
        game = _held(distance=5, ruleset=Ruleset(formation_width_limit=False))
        hild, wren = _ids(game, "Hild", "Wren")

        play(game, _withdraw(hild, replacement_id=wren))

        assert _order(game)[0] == "Wren"
        assert advance(game, hild) == -10


class TestTheTradeRefusals:
    """Guards: a trade refused under the old swap rules is refused under the trade rules too, and costs nothing."""

    @pytest.mark.parametrize("who", ["themselves", "a stranger", "a sleeper"])
    def test_the_named_member_has_to_be_another_declarer(self, who):
        game = _held(distance=5)
        hild, tam = _ids(game, "Hild", "Tam")
        if who == "a sleeper":
            game.member(tam).conditions = (ActiveCondition(condition=Condition.ASLEEP, effect_id="effect-9999"),)
        name = {"themselves": hild, "a stranger": "character-9999", "a sleeper": tam}[who]

        rejections = refused(game, _withdraw(hild, replacement_id=name))

        assert rejections == [
            ("battle.declaration.replacement_unavailable", {"character": hild, "replacement": name}),
        ]

    def test_two_members_cannot_name_one_member(self):
        game = _held(distance=5)
        hild, osric, tam = _ids(game, "Hild", "Osric", "Tam")

        rejections = refused(game, _withdraw(hild, replacement_id=tam), _withdraw(osric, replacement_id=tam))

        assert rejections == [
            ("battle.declaration.replacement_unavailable", {"character": osric, "replacement": tam}),
        ]


# ---------------------------------------------------------------------- the ranks toward a group


@POSITIONS
class TestTheRanksTowardAGroup:
    def test_the_front_rank_is_the_members_within_five_feet(self):
        game = _held(distance=30)
        nim = named(game, "Nim")
        assert group_front_rank(game, group_id(game)) == (), "precondition: nobody within reach"

        play(game, close(game, nim))

        assert group_front_rank(game, group_id(game)) == (nim,)

    def test_a_front_rank_still_out_of_reach_reaches_nobody(self):
        game = _held(distance=30)
        hild, osric = _ids(game, "Hild", "Osric")

        play(game, close(game, hild), close(game, osric))

        assert member_distance_feet(game, hild, group_id(game)) == 10
        assert group_front_rank(game, group_id(game)) == ()

    def test_the_first_to_arrive_keeps_their_place(self):
        game = _held(distance=30)
        hild, osric, nim = _ids(game, "Hild", "Osric", "Nim")
        play(game, close(game, nim))
        play(game, close(game, hild), close(game, osric))
        play(game, close(game, hild), close(game, osric))
        assert {member_distance_feet(game, member_id, group_id(game)) for member_id in (hild, osric, nim)} == {5}

        assert group_front_rank(game, group_id(game)) == (hild, nim), "Nim got there first, then Hild by marching"

    def test_without_the_width_limit_every_member_within_reach_is_in_front(self):
        game = _held(distance=30, ruleset=Ruleset(formation_width_limit=False))
        hild, nim, wren = _ids(game, "Hild", "Nim", "Wren")

        play(game, close(game, hild), close(game, nim), close(game, wren))

        assert group_front_rank(game, group_id(game)) == (nim, wren)

    def test_a_second_row_member_within_reach_is_refused_melee(self):
        game = _held(distance=30)
        hild, osric, nim = _ids(game, "Hild", "Osric", "Nim")
        play(game, close(game, nim))
        play(game, close(game, hild), close(game, osric))
        play(game, close(game, hild), close(game, osric))
        assert member_distance_feet(game, osric, group_id(game)) == 5, "precondition: Osric stands within reach"

        rejections = refused(game, strike(game, osric))

        assert rejections == [("battle.declaration.not_in_front_rank", {"character": osric})]

    def test_a_front_row_member_out_of_reach_is_refused_at_their_own_distance(self):
        """Wren alone stands nearest the goblins, so Wren is their front row, still 10' short of reach."""
        game = _held(distance=50)
        wren = named(game, "Wren")
        play(game, close(game, wren))

        rejections = refused(game, strike(game, wren))

        assert rejections == [("combat.attack.out_of_reach", {"distance_feet": 10})]

    def test_the_member_alone_in_front_strikes_and_the_row_behind_waits(self):
        """A row never mixes distances: Nim at 5' is the whole front row, and Hild at 30' is in the next one."""
        game = _held(distance=30)
        nim, hild = _ids(game, "Nim", "Hild")
        play(game, close(game, nim))

        assert refused(game, strike(game, hild)) == [("battle.declaration.not_in_front_rank", {"character": hild})]
        events = play(game, strike(game, nim))

        assert any(getattr(event, "attacker_id", None) == nim for event in events)


# ---------------------------------------------------------------------- the party line moves back


@POSITIONS
class TestThePartyLineMovesBack:
    """At the end of the party's movement step, when every living member who isn't invisible stands behind the party
    line, the line moves back to the foremost of them. Every advance and every group's distance grow by the same
    number of feet, so no distance between a member and a group changes, and no event reports it. A group can then
    close on the party as usual.
    """

    def test_a_lone_withdrawal_that_takes_the_whole_party_behind_the_line_moves_the_line_back(self):
        game = _held(distance=40)
        hild, osric, nim, aelin, wren, tam = _ids(game, "Hild", "Osric", "Nim", "Aelin", "Wren", "Tam")
        play(game, *(_withdraw(member_id, "retreat") for member_id in (osric, nim, aelin, wren, tam)))
        assert advances(game) == {hild: 0, osric: -20, nim: -30, aelin: -20, wren: -40, tam: -20}, (
            "precondition: Hild alone holds the line"
        )
        assert game.encounter.groups[0].distance_feet == 40

        events = play(game, _withdraw(hild))

        (move,) = moved(events)
        assert (move.character_id, move.advance_feet, move.distance_feet) == (hild, -10, 50)
        assert group_moves(events) == [], "nobody moved toward or away from anyone"
        assert advances(game) == {hild: 0, osric: -10, nim: -20, aelin: -10, wren: -30, tam: -10}
        assert game.encounter.groups[0].distance_feet == 50
        distances = {member_id: member_distance_feet(game, member_id, group_id(game)) for member_id in advances(game)}
        assert distances == {hild: 50, osric: 60, nim: 70, aelin: 60, wren: 80, tam: 60}

    def test_a_group_closes_and_reaches_a_party_that_fell_back_one_member_at_a_time(self):
        game = _held(distance=40)
        hild, osric = _ids(game, "Hild", "Osric")
        for member in list(game.party.members):
            play(game, _withdraw(member.id))
        assert max(advances(game).values()) == 0, "the line moved back to the foremost members"
        assert game.encounter.groups[0].distance_feet == 50
        release_monsters(game)

        for _ in range(3):
            play(game)

        assert game.encounter.groups[0].distance_feet == 5
        assert group_front_rank(game, group_id(game)) == (hild, osric)

    @pytest.mark.parametrize("holder", ["at the line", "forward of it"])
    def test_a_member_at_or_forward_of_the_line_keeps_it_where_it_is(self, holder):
        game = _held(distance=40)
        nim, tam = _ids(game, "Nim", "Tam")
        stays = tam if holder == "at the line" else nim
        if holder == "forward of it":
            play(game, close(game, nim))
            assert advance(game, nim) == 30, "precondition: Nim stands forward"
        others = [member.id for member in game.party.members if member.id != stays]

        play(game, *(_withdraw(member_id, "retreat") for member_id in others))

        assert advance(game, stays) == (0 if holder == "at the line" else 30)
        assert all(advance(game, member_id) < 0 for member_id in others)
        assert game.encounter.groups[0].distance_feet == 40

    def test_an_invisible_member_on_the_line_does_not_hold_it(self):
        game = _held(distance=40)
        hild, osric, nim, aelin, wren, tam = _ids(game, "Hild", "Osric", "Nim", "Aelin", "Wren", "Tam")
        attach(game, EffectDefinition(kind="invisibility", condition=Condition.INVISIBLE), tam)

        play(game, *(_withdraw(member_id, "retreat") for member_id in (hild, osric, nim, aelin, wren)))

        assert advances(game) == {hild: 0, osric: 0, nim: -10, aelin: 0, wren: -20, tam: 20}
        assert game.encounter.groups[0].distance_feet == 60


# ---------------------------------------------------------------------- the formation, the pursuit, and the end


@POSITIONS
class TestTheFormationAndTheEnd:
    def test_a_formation_move_moves_the_groups_and_leaves_every_advance_alone(self):
        game = _held(distance=30)
        nim = named(game, "Nim")
        play(game, close(game, nim))

        events = play(game, *(_withdraw(member.id) for member in game.party.members))

        assert [event.distance_feet for event in group_moves(events)] == [40], "half the slowest rate of 20'"
        assert moved(events) == []
        assert advance(game, nim) == 25
        assert member_distance_feet(game, nim, group_id(game)) == 15

    def test_a_formation_retreat_starts_the_pursuit_from_the_nearest_member(self):
        game = _held(distance=30)
        nim = named(game, "Nim")
        play(game, close(game, nim))

        events = play(game, *(_withdraw(member.id, "retreat") for member in game.party.members))

        assert "battle.ended.fled" in [event.code for event in events]
        assert game.mode is SessionMode.ENCOUNTER, "precondition: the goblins give chase"
        assert game.encounter.groups[0].distance_feet == 50
        assert game.encounter.pursuit.gap_feet == 25, "Nim stood 25' ahead of the line"

    def test_a_spread_out_party_retreats_as_one_body_and_the_chase_starts_from_the_nearest_member(self):
        """Nim stands 25' forward and Wren 40' back. The goblins sleep, so only the kobolds can follow."""
        game = battle(distance=30)
        assert game.execute(SpawnMonsters(template_id="kobold", count_fixed=2, distance_feet=60)).accepted
        hold_monsters(game)
        nim, wren = _ids(game, "Nim", "Wren")
        play(game, close(game, nim), _withdraw(wren, "retreat"))
        assert (advance(game, nim), advance(game, wren)) == (25, -40), "precondition: the party stands apart"
        for monster_id in game.encounter.groups[0].monster_ids:
            game.monsters[monster_id].conditions = (
                ActiveCondition(condition=Condition.ASLEEP, effect_id="effect-9999"),
            )

        events = play(game, *(_withdraw(member.id, "retreat") for member in game.party.members))

        assert "battle.ended.fled" in [event.code for event in events]
        assert moved(events) == [], "a formation move moves the groups, not the members"
        assert [group.distance_feet for group in game.encounter.groups] == [50, 80], "the slowest rate of 20'"
        assert game.mode is SessionMode.ENCOUNTER, "precondition: the kobolds give chase"
        assert game.encounter.pursuit.gap_feet == 55, "Nim, 25' forward, is 55' from the kobolds"

    def test_positions_end_with_the_battle(self):
        from osrlib.core.combat import DamageSource, deal_damage

        game = _held(distance=30)
        nim = named(game, "Nim")
        play(game, close(game, nim))
        assert advance(game, nim) == 25, "precondition: Nim stood forward"
        for monster_id in game.encounter.groups[0].monster_ids:
            deal_damage(game.combatant(monster_id), 999, source=DamageSource())
        play(game)
        assert game.mode is SessionMode.EXPLORING, "precondition: the battle is won"

        assert game.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=30)).accepted
        if game.mode is SessionMode.ENCOUNTER:
            from osrlib.crawl.commands import EngageBattle

            assert game.execute(EngageBattle()).accepted

        assert game.battle is not None
        assert game.battle.advance_feet == {}
        assert game.battle.arrived_round == {}


# ---------------------------------------------------------------------- determinism


@POSITIONS
class TestDeterminism:
    def test_a_refused_round_with_a_close_in_it_costs_nothing(self):
        game = _held(distance=30)
        hild, osric, nim = _ids(game, "Hild", "Osric", "Nim")
        play(game, close(game, nim))
        assert advance(game, nim) == 25, "precondition: Nim stands forward"

        rejections = refused(game, close(game, hild), strike(game, osric))

        assert rejections == [("combat.attack.out_of_reach", {"distance_feet": 30})]
        assert game.battle.advance_feet == {nim: 25}

    def test_a_save_mid_battle_keeps_the_positions_and_plays_on_the_same(self):
        game = battle(surprised=True)
        nim, hild = _ids(game, "Nim", "Hild")
        play(game, close(game, nim), close(game, hild))
        assert advance(game, nim) == 25, "precondition: Nim stands forward"

        restored = load_game(save_game(game))

        assert restored.battle.advance_feet == game.battle.advance_feet
        assert restored.battle.arrived_round == game.battle.arrived_round
        original, again = round_(game), round_(restored)
        assert original.accepted and again.accepted
        assert [event.model_dump() for event in again.events] == [event.model_dump() for event in original.events]

    def test_a_replay_rebuilds_the_positions(self):
        from osrlib.core.character import party_to_document

        game = battle(surprised=True)
        nim, hild = _ids(game, "Nim", "Hild")
        play(game, close(game, nim), close(game, hild))
        assert advance(game, nim) == 25, "precondition: Nim stands forward"
        for _ in range(2):
            if game.mode is SessionMode.BATTLE:
                play(game)
        members = [character(name, class_id) for name, class_id in WALK]

        replayed = replay_game(
            game.master_seed, party_to_document(members), game.adventure, Ruleset(), game.command_log
        )

        assert session_state(replayed) == session_state(game)


@POSITIONS
def test_the_old_swap_codes_are_no_longer_raised():
    """A withdrawal alone needs no replacement and covers no rank distance, so neither code can come back."""
    scanned = scan_rejection_codes()
    assert "battle.declaration.no_replacement" not in scanned
    assert "battle.declaration.replacement_too_far" not in scanned
