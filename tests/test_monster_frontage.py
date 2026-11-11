"""The formation width limits the monsters' melee attackers too (issue #214).

The OSE SRD's Other Combat Issues page says: "The referee should judge the number of opponents that
can attack a single combatant, bearing in mind the combatant's size and the available space around
them. 10' passage: Enough space for at most 2-3 characters to fight side-by-side." A battle already
limits the party's melee attackers on a group to the formation width. The same width now limits the
monsters: a group's front rank is its first living monsters, as many as the width, and only they
attack in melee. A monster in the front rank keeps its place while it lives, whatever it does.

The monsters here are skeletons, which never check morale, so every one of them fights on.
"""

import pytest

import positions_fixtures as pf
from osrlib.core.clock import TimeUnit
from osrlib.core.effects import Condition, EffectDefinition, ModifierSpec, grant_condition, has_condition, kill
from osrlib.core.events import Visibility
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.battle import MonsterAction, NpcPartyPolicy, ScriptedPolicy

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


def test_the_front_rank_is_the_groups_first_living_monsters():
    """A dead monster leaves its place, and the next one steps up."""
    game = melee(pf.corridor())
    first, second, third = monsters(game)[:3]
    assert attackers(game, pf.play(game)) == [first, second]
    kill(game.combatant(first))
    assert attackers(game, pf.play(game)) == [second, third]


def test_a_monster_that_cant_act_keeps_its_place():
    """A paralysed party member in the front rank keeps their place too, so the two sides play by one rule."""
    game = melee(pf.corridor())
    first, second = monsters(game)[:2]
    grant_condition(game.combatant(first), Condition.PARALYSED, None)
    assert attackers(game, pf.play(game)) == [second]
    grant_condition(game.combatant(second), Condition.PARALYSED, None)
    assert attackers(game, pf.play(game)) == []


def test_sleeping_monsters_in_front_block_the_ones_behind():
    """Goblins this time, because a skeleton can't be put to sleep."""
    game = pf.battle(template_id="goblin", count=HORDE, distance=5, adventure=pf.corridor())
    pf.hold_monsters(game)
    for goblin in monsters(game)[:2]:
        grant_condition(game.combatant(goblin), Condition.ASLEEP, None)
        assert has_condition(game.combatant(goblin), Condition.ASLEEP)
    pf.release_monsters(game)
    assert attackers(game, pf.play(game)) == []


class Reversed:
    """A policy of a game's own: the default actions, in the reverse of the group's order."""

    def choose(self, session, group, stream):
        return list(reversed(ScriptedPolicy().choose(session, group, stream)))


class FrontHolds:
    """A policy of a game's own: the first four monsters hold, and the last two attack."""

    def choose(self, session, group, stream):
        back = group.monster_ids[4:]
        return [
            action if action.monster_id in back else MonsterAction(monster_id=action.monster_id, kind="hold")
            for action in ScriptedPolicy().choose(session, group, stream)
        ]


class Logged:
    """A policy that remembers the actions the default policy chose."""

    def __init__(self, default) -> None:
        self.default = default
        self.last: list[MonsterAction] = []

    def choose(self, session, group, stream):
        self.last = self.default.choose(session, group, stream)
        return self.last

    def chose(self, *kinds: str) -> list[str]:
        return [action.monster_id for action in self.last if action.kind in kinds]


def test_a_policy_of_your_own_cant_change_who_stands_in_front():
    game = melee(pf.corridor())
    game.action_policies[pf.group_id(game)] = Reversed()
    assert sorted(attackers(game, pf.play(game))) == sorted(monsters(game)[:2])

    game = melee(pf.corridor())
    game.action_policies[pf.group_id(game)] = FrontHolds()
    assert attackers(game, pf.play(game)) == [], "the two at the back can't reach past the four in front"


def test_a_front_monster_that_breathes_keeps_its_place():
    """Breath isn't melee, so any hellhound may breathe, but only the two in front may bite."""
    for seed in (0, 40, 80, 120):
        game = pf.battle(template_id="hellhound_3", count=HORDE, distance=5, adventure=pf.corridor(), first_seed=seed)
        policy = Logged(ScriptedPolicy())
        game.action_policies[pf.group_id(game)] = policy
        events = pf.play(game)
        front = monsters(game)[:2]
        assert attackers(game, events) == [monster for monster in policy.chose("melee") if monster in front]


def test_confused_monsters_behind_the_front_rank_cant_reach_the_party():
    game = melee(pf.corridor())
    confusion = EffectDefinition(
        kind="confusion",
        condition=Condition.CONFUSED,
        params={"behaviour_dice": "2d6", "behaviour_table": ["2-12:attack_caster_group"], "resave_hd_min_count": 99},
    )
    for monster_id in monsters(game)[3:]:
        pf.attach(game, confusion, monster_id)
    assert attackers(game, pf.play(game)) == monsters(game)[:2]


# ---------------------------------------------------------------------- NPC adventurers


def test_npc_adventurers_fight_two_abreast_in_a_corridor_and_all_of_them_shoot():
    game = pf.battle(npc_party=True, distance=5, adventure=pf.corridor())
    policy = Logged(NpcPartyPolicy())
    game.action_policies[pf.group_id(game)] = policy
    for _ in range(3):
        events = pf.play(game)
        living = [npc for npc in monsters(game) if not has_condition(game.combatant(npc), Condition.DEAD)]
        swung = [npc for npc in attackers(game, events) if npc in policy.chose("melee")]
        assert swung == [npc for npc in living[:2] if npc in policy.chose("melee")]

    game = pf.battle(npc_party=True, distance=60, adventure=pf.corridor())
    policy = Logged(NpcPartyPolicy())
    game.action_policies[pf.group_id(game)] = policy
    events = pf.play(game)
    shooters = policy.chose("npc_shoot")
    assert shooters, "precondition: some NPC adventurers shoot from 60 feet"
    assert set(shooters) <= set(attackers(game, events))


@pytest.mark.parametrize(("distance", "kinds"), [(5, ("melee",)), (40, ("npc_shoot",))], ids=["a swing", "a shot"])
def test_an_adventurer_put_to_sleep_before_it_acts_doesnt_act(distance, kinds):
    """NPC adventurers choose their actions when the round starts, and a *sleep* can land before they act."""
    args = {"npc_party": True, "distance": distance, "spells": {"Aelin": ("sleep",)}, "adventure": pf.hall()}
    for seed in range(0, 120):
        game = pf.battle(first_seed=seed, **args)
        policy = Logged(NpcPartyPolicy())
        game.action_policies[pf.group_id(game)] = policy
        sleep = pf.declare(
            pf.named(game, "Aelin"), "cast", spell_id="sleep", spell_mode="hd_budget", target_group_id=pf.group_id(game)
        )
        events = pf.play(game, sleep)
        if pf.monsters_first(game, events) is not False:
            continue
        asleep = [npc for npc in monsters(game) if has_condition(game.combatant(npc), Condition.ASLEEP)]
        sleepers_with_plans = [npc for npc in asleep if npc in policy.chose(*kinds)]
        if sleepers_with_plans:
            assert not set(sleepers_with_plans) & set(attackers(game, events))
            return
    pytest.fail("no seed put an adventurer to sleep before it acted")


def test_a_confused_adventurer_doesnt_stop_the_round():
    game = pf.battle(npc_party=True, distance=5, adventure=pf.hall())
    confusion = EffectDefinition(
        kind="confusion",
        condition=Condition.CONFUSED,
        params={"behaviour_dice": "2d6", "behaviour_table": ["2-12:attack_caster_group"], "resave_hd_min_count": 99},
    )
    for npc in monsters(game)[:2]:
        pf.attach(game, confusion, npc)
    assert pf.round_(game).accepted


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


# ---------------------------------------------------------------------- wards and a policy of a game's own

UNDEAD_WARD = EffectDefinition(
    kind="protection_ward",
    duration_unit=TimeUnit.TURN,
    duration_amount=6,
    params={"party_wide": True, "bars_categories": ("undead",), "targets": "undead", "all_affected": True},
)
"""A scroll of protection from undead, which bars every undead monster from melee with the party."""

PROTECTION_FROM_EVIL = EffectDefinition(
    kind="protection_from_evil",
    modifiers=(
        ModifierSpec(kind="save_bonus", value=1, versus_other_alignment=True),
        ModifierSpec(kind="attack_penalty_of_attackers", value=-1, versus_other_alignment=True),
    ),
    params={"bars_melee_from": ("enchanted", "constructed", "summoned")},
)
"""*Protection from evil* on one member, whom enchanted monsters can't attack in melee."""


class StrikeFirstMember:
    """A policy of a game's own: every monster swings at one member, whatever protects them."""

    def __init__(self, target_id: str) -> None:
        self.target_id = target_id

    def choose(self, session, group, stream):
        return [
            MonsterAction(monster_id=monster, kind="melee", target_id=self.target_id) for monster in group.monster_ids
        ]


@pytest.mark.parametrize(
    ("template_id", "protection"),
    [("skeleton", UNDEAD_WARD), ("gargoyle", PROTECTION_FROM_EVIL)],
    ids=["a scroll's ward against the undead", "protection from evil against an enchanted monster"],
)
def test_a_policy_of_your_own_cant_strike_through_a_protection(template_id, protection):
    game = pf.battle(template_id=template_id, count=2, distance=5, adventure=pf.corridor())
    hild = pf.named(game, "Hild")
    pf.attach(game, protection, hild)
    game.action_policies[pf.group_id(game)] = StrikeFirstMember(hild)
    events = pf.play(game)
    assert [roll.attacker_id for roll in pf.monster_attacks(game, events) if roll.defender_id == hild] == []


@pytest.mark.parametrize("protection", [UNDEAD_WARD, PROTECTION_FROM_EVIL], ids=["a ward", "protection from evil"])
def test_npc_adventurers_fight_a_protected_party_without_stopping_the_round(protection):
    game = pf.battle(npc_party=True, distance=5, adventure=pf.corridor())
    pf.attach(game, protection, pf.named(game, "Hild"))
    assert pf.round_(game).accepted
