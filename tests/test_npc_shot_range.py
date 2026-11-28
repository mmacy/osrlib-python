"""An NPC adventurer's shot needs a target within the long range of the missile weapon it wields (issue 257).

`validate_attack` refuses a party member's missile attack past the weapon's long range with
`combat.attack.out_of_range`. An NPC follows the same rule: `NpcPartyPolicy` shoots only at a member within long range
of a missile weapon it wields, and otherwise closes, and the session skips an `npc_shoot` that a policy of your own
aims past long range. Weapon ranges from the OSE SRD's weapons table: a dagger's long range ends at 30', a sling's at
160', and a long bow's at 210'.
"""

import positions_fixtures as pf
from osrlib.core.events import AttackRolledEvent
from osrlib.core.items import MagicItemInstance
from osrlib.crawl.battle import MonsterAction, NpcPartyPolicy


class Logged:
    """A policy that remembers the actions the default policy chose."""

    def __init__(self) -> None:
        self.default = NpcPartyPolicy()
        self.last: list[MonsterAction] = []

    def choose(self, session, group, stream):
        self.last = self.default.choose(session, group, stream)
        return self.last


def _wielded(npc) -> list[str]:
    return [w.template_id if isinstance(w, MagicItemInstance) else w.template.id for w in npc.inventory.wielded]


def _dagger_thrower(game):
    """The battle's magic-user, whose only weapon is the kit's dagger."""
    group = game.encounter.groups[0]
    (npc_id,) = [npc_id for npc_id in group.monster_ids if game.combatant(npc_id).class_id == "magic_user"]
    npc = game.combatant(npc_id)
    assert _wielded(npc) == ["dagger"], "precondition: the magic-user wields only the dagger"
    return npc


def _attacks_by(events, attacker_id: str) -> list[AttackRolledEvent]:
    return [e for e in events if isinstance(e, AttackRolledEvent) and e.attacker_id == attacker_id]


def test_an_npc_closes_instead_of_throwing_a_dagger_past_its_long_range():
    game = pf.battle(npc_party=True, distance=200, adventure=pf.hall())
    npc = _dagger_thrower(game)
    policy = Logged()
    game.action_policies[pf.group_id(game)] = policy

    events = pf.play(game)

    kinds = [action.kind for action in policy.last if action.monster_id == npc.id]
    assert "npc_shoot" not in kinds
    assert kinds[0] == "close"
    assert _attacks_by(events, npc.id) == []


def test_an_npc_throws_the_dagger_within_its_long_range():
    game = pf.battle(npc_party=True, distance=20, adventure=pf.hall())
    npc = _dagger_thrower(game)
    policy = Logged()
    game.action_policies[pf.group_id(game)] = policy

    pf.play(game)

    assert [action.kind for action in policy.last if action.monster_id == npc.id] == ["npc_shoot"]


def test_the_session_skips_a_shot_a_policy_aims_past_long_range():
    game = pf.battle(npc_party=True, distance=200, adventure=pf.hall())
    npc = _dagger_thrower(game)
    target = game.party.members[0].id

    class Shoots:
        def choose(self, session, group, stream):
            return [MonsterAction(monster_id=npc.id, kind="npc_shoot", target_id=target)]

    game.action_policies[pf.group_id(game)] = Shoots()

    events = pf.play(game)

    assert _attacks_by(events, npc.id) == []


def test_the_shot_takes_a_weapon_that_reaches():
    """A fighter wields a dagger +1 and a sling. At 20' the dagger +1 is thrown. At 60' only the sling reaches."""
    from osrlib.crawl.battle import _npc_wielded
    from test_npc_magic_weapon import _wielding

    member = _wielding("sling")
    member.inventory.wielded.insert(
        0, MagicItemInstance(instance_id="magic-item-0009", template_id="dagger_plus_1", base_item_id="dagger")
    )

    near = _npc_wielded(member, missile=True, distance_feet=20)
    far = _npc_wielded(member, missile=True, distance_feet=60)
    beyond = _npc_wielded(member, missile=True, distance_feet=200)

    assert isinstance(near, MagicItemInstance) and near.template_id == "dagger_plus_1"
    assert getattr(far, "id", None) == "sling"
    assert beyond is None
