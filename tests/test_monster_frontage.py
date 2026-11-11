"""The formation width limits the monsters' melee attackers too (issue #214).

The OSE SRD's Other Combat Issues page says: "The referee should judge the number of opponents that
can attack a single combatant, bearing in mind the combatant's size and the available space around
them. 10' passage: Enough space for at most 2-3 characters to fight side-by-side." A battle already
limits the party's melee attackers on a group to the formation width. The same width now limits how
many monsters of a group attack in melee in a round.

The monsters here are skeletons, which never check morale, so every one of them fights on.
"""

import pytest

import positions_fixtures as pf
from osrlib.core.effects import Condition, grant_condition, kill
from osrlib.core.events import Visibility
from osrlib.core.ruleset import Ruleset

pytestmark = pytest.mark.xfail(reason="chunk: monster-frontage")

HORDE = 6


def melee(adventure, **battle_args):
    """A battle with six skeletons already in melee reach of the party line."""
    return pf.battle(template_id="skeleton", count=HORDE, distance=5, adventure=adventure, **battle_args)


def attackers(game, events) -> list[str]:
    """The monsters that rolled an attack, in the order they first attacked."""
    return list(dict.fromkeys(event.attacker_id for event in pf.monster_attacks(game, events)))


def monsters(game) -> list[str]:
    return list(game.encounter.groups[0].monster_ids)


@pytest.mark.parametrize(("build", "width"), [(pf.corridor, 2), (pf.hall, 4)], ids=["a 10' corridor", "a hall"])
def test_only_as_many_monsters_as_the_space_allows_attack_in_melee(build, width):
    game = melee(build())
    assert game.view(Visibility.PLAYER).encounter.formation_width == width
    for _ in range(3):
        fighting = attackers(game, pf.play(game))
        assert fighting == monsters(game)[:width]


def test_every_monster_attacks_when_the_width_limit_is_off():
    game = melee(pf.corridor(), ruleset=Ruleset(formation_width_limit=False))
    assert sorted(attackers(game, pf.play(game))) == sorted(monsters(game))


def test_the_next_monsters_step_up_when_the_front_ones_fall():
    game = melee(pf.corridor())
    first, second, third, fourth = monsters(game)[:4]
    assert attackers(game, pf.play(game)) == [first, second]
    kill(game.combatant(first))
    assert attackers(game, pf.play(game)) == [second, third]
    grant_condition(game.combatant(second), Condition.PARALYSED, None)
    assert attackers(game, pf.play(game)) == [third, fourth]


def test_a_member_who_runs_ahead_alone_faces_only_the_monsters_that_fit():
    """The issue's example: one member reaches a group alone, and the group can't all attack them."""
    game = pf.battle(template_id="skeleton", count=4, distance=30, adventure=pf.corridor())
    pf.hold_monsters(game)
    nim = pf.named(game, "Nim")
    pf.play(game, pf.close(game, nim))
    assert game.view(Visibility.PLAYER).encounter.groups[0].member_distance_feet[nim] == 5
    pf.release_monsters(game)
    events = pf.play(game)
    rolls = pf.monster_attacks(game, events)
    assert {roll.defender_id for roll in rolls} == {nim}
    assert attackers(game, events) == monsters(game)[:2]
