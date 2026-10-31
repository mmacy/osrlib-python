"""Acceptance tests for how far the monsters reach once party members stand apart (issue #209).

The contract these tests check:

- A group closes toward the party line at its rate, and stops as soon as it's within 5' of the nearest living member
  it can see. It never moves past a member it can see. An invisible member doesn't stop it, and a member turned to stone
  does. A group already within 5' of a member doesn't close.
- Melee from a group reaches only its front rank within 5', the members `group_front_rank` lists. The members behind
  are out of its reach, so the members in front screen them.
- A monster can close and attack in the same round. The default policies follow a `close` that brings a member within
  reach with a `melee` for each monster, aimed at the front rank the move produces. The session resolves a `melee`
  only on a target in the group's front rank within 5' when the action comes up, whatever a policy chose.
- NPC adventurers shoot at the first row of the party's ranks toward their group, not at whoever marches first.
- A petrifying gaze reaches the gazer's group's front rank within 5'.
- A breath aimed at the party covers the ranks toward the breathing group from the front, by distance. A row at a
  greater distance starts at that distance, so a cone covers each member nearer than its length.
- Friendly fire from the party's area spell catches the members within 5' of the group in its front rank.

Each test sets members at their distances with the monsters held by a test policy, then gives the monsters their
default policy back for the round under test. Each test asserts its own preconditions.

`TestReviewRegressions` at the end holds regression tests for two defects a review found in the first build.
"""

import pytest

from osrlib.core.effects import ActiveCondition, Condition, EffectDefinition
from osrlib.core.events import SpellCastEvent
from osrlib.crawl import battle as battle_module
from osrlib.crawl.battle import MonsterAction, ScriptedPolicy, group_front_rank, member_distance_feet
from osrlib.crawl.session import MONSTER_ACTION_STREAM
from positions_fixtures import (
    HILD,
    NIM,
    advance,
    attach,
    battle,
    close,
    declare,
    group_id,
    group_moves,
    hold_monsters,
    monster_attacks,
    named,
    play,
    position,
    release_monsters,
    saves_by_members,
)


def _ids(game, *names: str) -> tuple[str, ...]:
    return tuple(named(game, name) for name in names)


def _placed(*closers: tuple[str, int], **battle_args):
    """A battle in which each named member closed the given number of rounds while the monsters held still."""
    game = battle(**battle_args)
    hold_monsters(game)
    for rounds in range(max((count for _, count in closers), default=0)):
        movers = [named(game, name) for name, count in closers if count > rounds]
        play(game, *(close(game, member_id) for member_id in movers))
    release_monsters(game)
    return game


class _Breathe:
    """A test policy: every monster of the group uses its breath weapon."""

    def choose(self, session, group, stream):
        return [MonsterAction(monster_id=monster_id, kind="breath") for monster_id in group.monster_ids]


class _StrikeAt:
    """A test policy: every monster of the group closes, then strikes one named member, wherever they stand."""

    def __init__(self, target_id: str) -> None:
        self.target_id = target_id

    def choose(self, session, group, stream):
        closes = [MonsterAction(monster_id=monster_id, kind="close") for monster_id in group.monster_ids]
        strikes = [
            MonsterAction(monster_id=monster_id, kind="melee", target_id=self.target_id)
            for monster_id in group.monster_ids
        ]
        return [*closes, *strikes]


# ---------------------------------------------------------------------- closing


class TestAGroupCloses:
    def test_a_group_stops_five_feet_from_the_nearest_member(self):
        game = _placed(("Nim", 1), distance=50)
        nim = named(game, "Nim")
        assert advance(game, nim) == 30, "precondition: Nim stands 20' from the goblins"

        events = play(game)

        assert [event.distance_feet for event in group_moves(events)] == [35]
        assert member_distance_feet(game, nim, group_id(game)) == 5

    def test_a_group_never_walks_past_a_member(self):
        game = _placed(("Hild", 1), distance=40)
        hild = named(game, "Hild")

        play(game)

        assert game.encounter.groups[0].distance_feet == 25
        assert all(member_distance_feet(game, member.id, group_id(game)) >= 5 for member in game.party.members)
        assert group_front_rank(game, group_id(game)) == (hild,)

    def test_an_invisible_member_does_not_stop_a_group(self):
        """Nim is invisible 30' ahead, so the goblins pass Nim and stop 5' from Hild, 20' ahead."""
        game = _placed(("Nim", 1), ("Hild", 1), distance=40)
        nim, hild = _ids(game, "Nim", "Hild")
        assert (advance(game, nim), advance(game, hild)) == (30, 20), "precondition: positions"
        attach(game, EffectDefinition(kind="invisibility", condition=Condition.INVISIBLE), nim)

        events = play(game)

        assert [event.distance_feet for event in group_moves(events)] == [25]
        assert {attack.defender_id for attack in monster_attacks(game, events)} <= {hild}

    def test_a_statue_stops_a_group_and_is_struck(self):
        game = _placed(("Nim", 1), distance=50)
        nim = named(game, "Nim")
        game.member(nim).conditions = (ActiveCondition(condition=Condition.PETRIFIED, effect_id="effect-9999"),)

        events = play(game)

        assert game.encounter.groups[0].distance_feet == 35
        attacks = monster_attacks(game, events)
        assert attacks, "precondition: the goblins struck"
        assert {attack.defender_id for attack in attacks} == {nim}


# ---------------------------------------------------------------------- who the group reaches


class TestWhoTheGroupReaches:
    def test_a_group_within_reach_of_a_member_stays_and_strikes_only_that_member(self):
        game = _placed(("Nim", 1), distance=30)
        nim = named(game, "Nim")
        assert member_distance_feet(game, nim, group_id(game)) == 5, "precondition: Nim stands within reach"

        struck: set[str] = set()
        for _ in range(4):
            events = play(game)
            assert group_moves(events) == [], "the goblins hold their ground"
            struck |= {attack.defender_id for attack in monster_attacks(game, events)}

        assert struck == {nim}

    def test_a_group_closes_and_strikes_in_the_same_round(self):
        game = battle(distance=25)
        hild, osric = _ids(game, "Hild", "Osric")

        events = play(game)

        (move,) = group_moves(events)
        attacks = monster_attacks(game, events)
        assert move.distance_feet == 5
        assert attacks, "the goblins struck in the round they arrived"
        assert all(position(events, attack) > position(events, move) for attack in attacks)
        assert {attack.defender_id for attack in attacks} <= {hild, osric}

    def test_the_default_policy_follows_a_close_that_reaches_with_melee(self):
        game = battle(distance=25)
        front = set(_ids(game, "Hild", "Osric"))
        group = game.encounter.groups[0]

        actions = ScriptedPolicy().choose(game, group, game.streams.get(MONSTER_ACTION_STREAM))

        assert "close" in [action.kind for action in actions]
        strikes = [action for action in actions if action.kind == "melee"]
        assert len(strikes) == len(group.monster_ids)
        assert {action.target_id for action in strikes} <= front

    def test_a_melee_on_a_member_out_of_reach_is_skipped(self):
        game = _placed(("Nim", 1), distance=30)
        wren = named(game, "Wren")
        game.action_policies[group_id(game)] = _StrikeAt(wren)

        events = play(game)

        assert [attack for attack in monster_attacks(game, events) if attack.defender_id == wren] == []

    def test_a_melee_after_a_close_that_falls_short_is_skipped(self):
        game = battle(distance=60)
        hild = named(game, "Hild")
        game.action_policies[group_id(game)] = _StrikeAt(hild)

        events = play(game)

        assert game.encounter.groups[0].distance_feet == 40, "precondition: the goblins closed 20'"
        assert monster_attacks(game, events) == []

    def test_npc_archers_shoot_at_the_first_row_toward_them(self):
        """Wren alone closed toward the NPCs, so Wren is the whole first row they see."""
        for seed in range(0, 60):
            game = _placed(("Wren", 1), distance=60, npc_party=True, first_seed=seed)
            wren = named(game, "Wren")
            assert member_distance_feet(game, wren, group_id(game)) == 20, "precondition: Wren stands forward"

            attacks = monster_attacks(game, play(game))
            if not attacks:
                continue

            assert {attack.defender_id for attack in attacks} == {wren}
            return
        pytest.fail("no seed gave the NPCs an attack")


# ---------------------------------------------------------------------- gaze, breath, and friendly fire


class TestAreasAndGazes:
    def test_a_gaze_reaches_only_the_front_rank_within_reach(self):
        game = _placed(("Nim", 1), template_id="basilisk", count=1, distance=30)
        nim = named(game, "Nim")
        assert member_distance_feet(game, nim, group_id(game)) == 5, "precondition: Nim stands within reach"

        events = play(game)

        assert saves_by_members(game, events) == {nim}

    def test_a_breath_covers_the_members_nearer_than_its_length(self):
        """The chimera's cone is 50' long. Nim at 5' and Hild at 20' are inside it. The line, 60' off, isn't."""
        game = _placed(("Nim", 2), ("Hild", 2), template_id="chimera", count=1, distance=60)
        nim, hild = _ids(game, "Nim", "Hild")
        assert member_distance_feet(game, nim, group_id(game)) == 5, "precondition: Nim at 5'"
        assert member_distance_feet(game, hild, group_id(game)) == 20, "precondition: Hild at 20'"
        game.action_policies[group_id(game)] = _Breathe()

        events = play(game)

        assert saves_by_members(game, events) == {nim, hild}

    def test_friendly_fire_catches_the_member_within_reach_of_the_group(self):
        game = _placed(("Nim", 1), distance=30, count=4, levels={"Wren": 5}, spells={"Wren": ("fire_ball",)})
        hold_monsters(game)
        nim, wren = _ids(game, "Nim", "Wren")
        assert member_distance_feet(game, nim, group_id(game)) == 5, "precondition: Nim stands within reach"
        fire_ball = declare(wren, "cast", spell_id="fire_ball", spell_mode="damage", target_group_id=group_id(game))

        events = play(game, fire_ball)

        (cast,) = [event for event in events if isinstance(event, SpellCastEvent) and event.caster_id == wren]
        party = {member.id for member in game.party.members}
        assert set(cast.target_ids) & party == {nim}


# ---------------------------------------------------------------------- review regressions


class TestReviewRegressions:
    def test_an_area_never_covers_a_farther_row_past_a_nearer_one(self):
        """Width 2, and a cone that spans 10' past the first row. Four members stand 5' from the goblins and two 10'.

        Nim and Wren got there first, so they're the first row, and Hild and Osric are the second row at 5'. Depth never
        goes down, so the second row is 15' deep and Aelin and Tam's row, 10' from the goblins, is 25' deep. The cone
        covers the first row and stops.
        """
        game = battle(distance=30)
        hold_monsters(game)
        play(game, *(close(game, member.id) for member in game.party.living_members()))
        play(game, close(game, named(game, "Hild")), close(game, named(game, "Osric")))
        gid = group_id(game)
        names = ("Hild", "Osric", "Nim", "Aelin", "Wren", "Tam")
        distances = {name: member_distance_feet(game, named(game, name), gid) for name in names}
        assert distances == {"Hild": 5, "Osric": 5, "Nim": 5, "Aelin": 10, "Wren": 5, "Tam": 10}, "precondition"
        cone = {"length_feet": 15}
        assert battle_module._area_span_feet("cone", cone, 5) == 10, "precondition: the cone spans 10'"

        covered = battle_module._area_rows(game.encounter.groups[0], battle_module._placement(game), "cone", cone)

        assert covered == list(_ids(game, "Nim", "Wren"))

    def test_a_group_reaches_a_member_behind_the_line_while_another_stands_forward_of_it(self):
        """The goblins stand 10' from the line. Nim, whom they passed while Nim was invisible, stands 40' forward of the
        line and can be seen again, so the line stays where it is. Hild stands 5' behind it, 15' from the goblins, and
        is the nearest member they can see. The goblins reach Hild in one round."""
        game = battle((HILD, NIM), distance=10)
        hild, nim = _ids(game, "Hild", "Nim")
        gid = group_id(game)
        game.battle.advance_feet.update({nim: 40, hild: -5})
        assert (member_distance_feet(game, hild, gid), member_distance_feet(game, nim, gid)) == (15, -30), (
            "precondition"
        )

        events = play(game)

        assert member_distance_feet(game, hild, gid) == 5
        assert game.encounter.groups[0].distance_feet >= 0
        assert {attack.defender_id for attack in monster_attacks(game, events)} <= {hild}

    def test_a_group_closing_on_a_member_far_behind_the_line_moves_the_line_back(self):
        """As above, with Nim 60' forward of the line and Hild 25' behind it, 35' from the goblins and still the nearest
        member they can see. A stop 5' from Hild would be behind the line, so the line moves back by the shortfall and
        the goblins stand on it. They reach Hild in the two rounds their 20' rate needs."""
        game = battle((HILD, NIM), distance=10)
        hild, nim = _ids(game, "Hild", "Nim")
        gid = group_id(game)
        game.battle.advance_feet.update({nim: 60, hild: -25})
        assert (member_distance_feet(game, hild, gid), member_distance_feet(game, nim, gid)) == (35, -50), (
            "precondition"
        )

        reached = []
        for _ in range(2):
            play(game)
            assert game.encounter.groups[0].distance_feet == 0
            reached.append(member_distance_feet(game, hild, gid))

        assert reached == [15, 5]
        assert advance(game, hild) == -5
