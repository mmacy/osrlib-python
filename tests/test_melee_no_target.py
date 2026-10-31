"""Acceptance tests for a melee attack whose group has no monster left to strike (found in playtesting).

A game's playtest declared `action="attack"` with `move="close"` on a group. The party's archers and casters killed
every monster in that group earlier in the same round. The members closed, and then their melee attacks vanished
with no event, so the game couldn't tell the player why they didn't swing.

The contract these tests check:

- When a member's declared melee attack comes up and its group has no living, visible monster left to strike, the
  attack fizzles. The session reports a player-visible `DeclarationFizzledEvent` at `battle.melee.fizzled`, with the
  member's id as `actor_id`, the group's id as `group_id`, and `battle.declaration.no_target` as `reason`. That is the
  rejection code validation gives a declaration aimed at such a group.
- It applies whether or not the member closed, and wherever the member stands. The session asks it before it asks
  about the member's position, in the order validation asks. A slow melee attack that comes up at the end of the round
  gets the same check then.
- Each such attack fizzles once.
- A group with a living monster left still takes the attack as before.
- A guard: a group that flees out of the battle before the melee step, from a member who wasn't within 5' of it when
  the round was accepted, fizzles that member's attack with `combat.attack.out_of_reach`, as it does today.
- Such a group fizzles the attack of a member who was within 5' of it with `combat.attack.out_of_reach` too. A group
  that only runs still takes that member's attack, but a group that left the battle can't.

The battles are built by command from seeded sessions. Each test sets the hit points of the goblin it needs killed to
1, so a magic missile kills it for certain, and asserts the kill as a precondition.
"""

import pytest

from osrlib.core.effects import Condition, has_condition
from osrlib.core.events import DeclarationFizzledEvent, SpellCastEvent, Visibility
from osrlib.crawl.commands import EquipItem, GrantItem, SpawnMonsters
from osrlib.messages import format_message
from positions_fixtures import (
    AELIN,
    HILD,
    NIM,
    OSRIC,
    SEEDS,
    TAM,
    WALK,
    WREN,
    _opened,
    attacks_by,
    declare,
    hall,
    hold_monsters,
    monsters_first,
    moved,
    named,
    play,
    position,
    session,
    strike,
)

XFAIL = pytest.mark.xfail(reason="chunk: playtest-fixes", strict=True)

MELEE_FIZZLED = "battle.melee.fizzled"
NO_TARGET = "battle.declaration.no_target"
OUT_OF_REACH = "combat.attack.out_of_reach"

MISSILE_SPELLS = {"Wren": ("magic_missile", "magic_missile")}

BORS = ("Bors", "fighter")
"""A fighter the fixtures don't arm, so a test can give him a two-handed sword, a slow weapon."""


def battles(
    *,
    near: int,
    near_count: int = 1,
    template_id: str = "goblin",
    roster=WALK,
    gear: dict[str, tuple[str, ...]] | None = None,
    hold: bool = True,
):
    """Each battle in the hall against `template_id` monsters `near` feet away and two goblins 60' away, by seed.

    Each battle is at round 0, and neither side is surprised. A seed that opens a different battle is skipped. The
    near group's first monster has 1 hit point. The far group keeps the battle going when the near group dies.
    `gear` names extra items to grant and equip by command, by member name, before the encounter opens. With `hold`,
    every group holds still.
    """
    for seed in SEEDS:
        game = session(seed, roster, spells=MISSILE_SPELLS, adventure=hall())
        for name, items in (gear or {}).items():
            member_id = named(game, name)
            for item_id in items:
                assert game.execute(GrantItem(character_id=member_id, item_id=item_id)).accepted
                assert game.execute(EquipItem(character_id=member_id, item_id=item_id)).accepted
        spawn = SpawnMonsters(template_id=template_id, count_fixed=near_count, distance_feet=near)
        if not _opened(game, game.execute(spawn), surprised=False, engage=True):
            continue
        assert game.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=60)).accepted
        assert len(game.encounter.groups) == 2, "precondition: the far group joined the battle"
        assert game.encounter.groups[0].distance_feet == near, "precondition: the near group at its distance"
        weak = game.combatant(game.encounter.groups[0].monster_ids[0])
        weak.current_hp = 1
        if hold:
            hold_monsters(game)
        yield game


def two_groups(**battle_args):
    """The first battle `battles` builds with `battle_args`."""
    return next(battles(**battle_args))


def near_group(game) -> str:
    return game.encounter.groups[0].id


def weak_goblin(game) -> str:
    return game.encounter.groups[0].monster_ids[0]


def dead(game, monster_id: str) -> bool:
    return has_condition(game.combatant(monster_id), Condition.DEAD)


def magic_missile(game, caster: str):
    return declare(caster, "cast", spell_id="magic_missile", spell_mode="missiles", targets=(weak_goblin(game),))


def fizzles(events, actor_id: str) -> list[DeclarationFizzledEvent]:
    return [
        event
        for event in events
        if isinstance(event, DeclarationFizzledEvent) and event.code == MELEE_FIZZLED and event.actor_id == actor_id
    ]


def cast_by(events, caster_id: str) -> SpellCastEvent:
    return next(event for event in events if isinstance(event, SpellCastEvent) and event.caster_id == caster_id)


def assert_no_target(game, events, member_id: str) -> DeclarationFizzledEvent:
    """Assert the member made no attack and has exactly one no-target fizzle at the near group, and return it."""
    assert attacks_by(events, member_id) == [], "the member swung at a group with no monster left"
    found = fizzles(events, member_id)
    assert len(found) == 1, f"expected one melee fizzle for {member_id}, got {len(found)}"
    (fizzle,) = found
    assert (fizzle.reason, fizzle.group_id, fizzle.item_id) == (NO_TARGET, near_group(game), None)
    assert fizzle.visibility is Visibility.PLAYER
    return fizzle


# ---------------------------------------------------------------------- the playtest's case


class TestMembersWhoClosedOnAGroupKilledEarlierInTheRound:
    @XFAIL
    def test_a_caster_kills_the_group_in_the_magic_step(self):
        """Hild, Osric, and Nim close 15' and attack the goblin at 20'. Wren's magic missile kills it first."""
        game = two_groups(near=20)
        hild, osric, nim, wren = (named(game, name) for name in ("Hild", "Osric", "Nim", "Wren"))
        closers = (hild, osric, nim)

        events = play(game, *(strike(game, member, move="close") for member in closers), magic_missile(game, wren))

        assert dead(game, weak_goblin(game)), "precondition: the magic missile killed the goblin"
        assert {move.character_id: move.distance_feet for move in moved(events)} == dict.fromkeys(closers, 5), (
            "precondition: every closer reached 5'"
        )
        cast = cast_by(events, wren)
        for member_id in closers:
            fizzle = assert_no_target(game, events, member_id)
            assert position(events, fizzle) > position(events, cast)

    @XFAIL
    def test_an_archer_kills_the_group_in_the_missile_step(self):
        """Hild and Osric close and attack the goblin at 20'. Aelin's arrow kills it first."""
        for game in battles(near=20):
            hild, osric, aelin = (named(game, name) for name in ("Hild", "Osric", "Aelin"))

            events = play(
                game, strike(game, hild, move="close"), strike(game, osric, move="close"), strike(game, aelin)
            )

            arrows = attacks_by(events, aelin)
            if not (arrows and arrows[0].code == "combat.attack.hit" and dead(game, weak_goblin(game))):
                continue
            for member_id in (hild, osric):
                fizzle = assert_no_target(game, events, member_id)
                assert position(events, fizzle) > position(events, arrows[0])
            return
        raise AssertionError("precondition: no seed had Aelin's arrow kill the goblin")

    @XFAIL
    def test_a_close_that_fell_short_still_reports_no_target(self):
        """The goblin runs before Nim's close, so Nim ends out of reach. Wren's missile then kills it.

        The group has no monster left, so the reason is `no_target`, not `out_of_reach`.
        """
        for game in battles(near=25, hold=False):
            game.encounter.groups[0].fleeing = True
            nim, wren = named(game, "Nim"), named(game, "Wren")

            events = play(game, strike(game, nim, move="close"), magic_missile(game, wren))
            if not monsters_first(game, events):
                continue

            assert dead(game, weak_goblin(game)), "precondition: the magic missile killed the goblin"
            (move,) = [move for move in moved(events) if move.character_id == nim]
            assert move.distance_feet > 5, "precondition: the goblin ran before Nim's close"
            assert_no_target(game, events, nim)
            return
        raise AssertionError("precondition: no seed had the monsters act first")


# ---------------------------------------------------------------------- a member already within 5'


class TestAMemberAlreadyInReach:
    @XFAIL
    def test_the_group_dies_in_the_magic_step(self):
        """Hild stands within 5' of the goblin and attacks without moving. Wren's magic missile kills it first."""
        game = two_groups(near=5)
        hild, wren = named(game, "Hild"), named(game, "Wren")

        events = play(game, strike(game, hild), magic_missile(game, wren))

        assert dead(game, weak_goblin(game)), "precondition: the magic missile killed the goblin"
        assert moved(events) == [], "precondition: nobody moved"
        fizzle = assert_no_target(game, events, hild)
        assert position(events, fizzle) > position(events, cast_by(events, wren))

    @XFAIL
    def test_the_group_dies_to_an_earlier_member_in_the_melee_step(self):
        """Hild and Osric both attack the goblin at 5'. Hild acts first in marching order and kills it."""
        for game in battles(near=5):
            hild, osric = named(game, "Hild"), named(game, "Osric")

            events = play(game, strike(game, hild), strike(game, osric))

            swings = attacks_by(events, hild)
            if not (swings and swings[0].code == "combat.attack.hit" and dead(game, weak_goblin(game))):
                continue
            fizzle = assert_no_target(game, events, osric)
            assert position(events, fizzle) > position(events, swings[0])
            return
        raise AssertionError("precondition: no seed had Hild's sword kill the goblin")

    @XFAIL
    def test_a_slow_attack_at_the_end_of_the_round_fizzles_once(self):
        """Bors's two-handed sword is slow, so it comes up at the end of the round. Hild kills the goblin first."""
        roster = (HILD, BORS, OSRIC, NIM, AELIN, WREN, TAM)
        for game in battles(near=5, roster=roster, gear={"Bors": ("two_handed_sword",)}):
            hild, bors = named(game, "Hild"), named(game, "Bors")

            events = play(game, strike(game, hild), strike(game, bors))

            swings = attacks_by(events, hild)
            if not (swings and swings[0].code == "combat.attack.hit" and dead(game, weak_goblin(game))):
                continue
            fizzle = assert_no_target(game, events, bors)
            assert position(events, fizzle) > position(events, swings[0])
            return
        raise AssertionError("precondition: no seed had Hild's sword kill the goblin")

    @XFAIL
    def test_a_slow_attack_whose_group_died_before_the_melee_step_fizzles_once(self):
        """Wren's magic missile kills the goblin in the magic step, before Bors's slow attack is even queued."""
        roster = (HILD, BORS, OSRIC, NIM, AELIN, WREN, TAM)
        game = two_groups(near=5, roster=roster, gear={"Bors": ("two_handed_sword",)})
        bors, wren = named(game, "Bors"), named(game, "Wren")

        events = play(game, strike(game, bors), magic_missile(game, wren))

        assert dead(game, weak_goblin(game)), "precondition: the magic missile killed the goblin"
        assert_no_target(game, events, bors)


# ---------------------------------------------------------------------- guards


class TestGuards:
    def test_a_group_with_a_living_monster_still_takes_the_attack(self):
        """Wren's missile kills one of two goblins at 20'. Nim closes and strikes the other."""
        game = two_groups(near=20, near_count=2)
        nim, wren = named(game, "Nim"), named(game, "Wren")
        survivor = game.encounter.groups[0].monster_ids[1]
        game.combatant(survivor).current_hp = 100

        events = play(game, strike(game, nim, move="close"), magic_missile(game, wren))

        assert dead(game, weak_goblin(game)), "precondition: the magic missile killed one goblin"
        (swing,) = attacks_by(events, nim)
        assert swing.defender_id == survivor
        assert position(events, swing) > position(events, cast_by(events, wren))
        assert fizzles(events, nim) == []

    def test_a_group_that_flees_the_battle_is_out_of_reach_for_a_member_who_was_closing(self):
        """Dire wolves 35' away break and run 150' before Nim's close, out of the battle. Nim's attack is out of reach.

        Nim wasn't within 5' of the group when the round was accepted, and the wolves that fled are alive. This is
        today's behaviour, and the no-target rule doesn't change it.
        """
        for game in battles(near=35, template_id="dire_wolf", hold=False):
            game.encounter.groups[0].fleeing = True
            nim = named(game, "Nim")

            events = play(game, strike(game, nim, move="close"))
            if not monsters_first(game, events):
                continue

            assert game.encounter.groups[0].fled, "precondition: the wolves left the battle before Nim's turn"
            assert not dead(game, weak_goblin(game)), "precondition: the wolves fled alive"
            assert attacks_by(events, nim) == []
            (fizzle,) = fizzles(events, nim)
            assert (fizzle.reason, fizzle.group_id) == (OUT_OF_REACH, near_group(game))
            return
        raise AssertionError("precondition: no seed had the monsters act first")

    def test_a_group_that_flees_the_battle_is_out_of_reach_for_a_member_who_was_engaged(self):
        """Dire wolves 5' from Hild break and run 150' before Hild's turn, out of the battle. Hild's attack is out of
        reach.

        Hild stood within 5' of the group when the round was accepted, so a group that only ran would still take her
        attack. A group that left the battle can't, and the attack fizzles once instead of vanishing.
        """
        for game in battles(near=5, template_id="dire_wolf", hold=False):
            game.encounter.groups[0].fleeing = True
            hild = named(game, "Hild")

            events = play(game, strike(game, hild))
            if not monsters_first(game, events):
                continue

            assert game.encounter.groups[0].fled, "precondition: the wolves left the battle before Hild's turn"
            assert not dead(game, weak_goblin(game)), "precondition: the wolves fled alive"
            assert attacks_by(events, hild) == []
            (fizzle,) = fizzles(events, hild)
            assert (fizzle.reason, fizzle.group_id) == (OUT_OF_REACH, near_group(game))
            assert fizzle.visibility is Visibility.PLAYER
            return
        raise AssertionError("precondition: no seed had the monsters act first")


class TestTheNoTargetMessage:
    @XFAIL
    def test_the_message_says_there_was_nothing_left_to_strike(self):
        event = DeclarationFizzledEvent(
            code=MELEE_FIZZLED, actor_id="character-0001", reason=NO_TARGET, group_id="group-0001"
        )

        line = format_message(event)

        assert "character-0001" in line and "group-0001" in line
        assert "can't reach" not in line and "front rank" not in line
        assert "no attack" in line
