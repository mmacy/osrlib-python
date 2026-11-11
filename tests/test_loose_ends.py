"""Small faults left from the API reference audit (issues #162 and #166).

An absorbed attack that didn't say what was absorbed, a restore that left old stream references
behind, constants a docstring asked readers not to reassign, an unequip that reported a curse on an
item that wasn't worn, and a content pack finding whose facts were only in its English message.
"""

import typing

import pytest

from crawl_fixtures import build_adventure, build_party
from osrlib.core import character as character_module
from osrlib.core import clock as clock_module
from osrlib.core.combat import COMBAT_STREAM, AttackContext, resolve_attack, resolve_breath
from osrlib.core.items import MagicItemInstance, unequip, validate_unequip
from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.commands import EquipItem, GrantItem, UnequipItem
from osrlib.crawl.content_pack import ContentPack, ContentPackEntry, PackFinding, PackSection, validate_content_pack
from osrlib.crawl.dungeon import FeatureSpec, KeyedEncounter, KeyedMonster
from osrlib.crawl.session import GameSession
from osrlib.data import load_equipment, load_monsters

pytestmark = pytest.mark.xfail(reason="chunk: loose-ends")


def monster(template_id: str, monster_id: str = "monster-0001"):
    stream = RngStreams(master_seed=3).get(MONSTER_SPAWN_STREAM)
    return spawn_monster(load_monsters().get(template_id), id=monster_id, stream=stream)


# ---------------------------------------------------------------------- what was absorbed (#166, item 35)


def test_an_absorbed_torch_attack_says_fire_was_absorbed():
    torch = load_equipment().get("torch")
    for seed in range(1, 80):
        result = resolve_attack(
            monster("goblin"),
            monster("red_dragon", "monster-0002"),
            torch,
            context=AttackContext(),
            ruleset=Ruleset(),
            stream=RngStreams(master_seed=seed).get(COMBAT_STREAM),
        )
        if result.absorbed:
            absorbed = result.events[-1]
            assert absorbed.code == "combat.damage.absorbed"
            assert absorbed.keys == ("fire",)
            break
    else:
        pytest.fail("no seed landed the torch")
    breath = resolve_breath(
        monster("red_dragon"),
        [monster("red_dragon", "monster-0002")],
        ruleset=Ruleset(),
        stream=RngStreams(master_seed=1).get(COMBAT_STREAM),
    )
    assert breath[0].keys == absorbed.keys


# ---------------------------------------------------------------------- restoring stream positions (#166)


def test_restore_states_winds_back_the_streams_a_caller_already_has():
    streams = RngStreams(master_seed=42)
    combat = streams.get("combat")
    saved = streams.export_states()
    first = combat.randbelow(1000)
    streams.restore_states(saved)
    assert streams.get("combat") is combat, "the reference a caller took earlier is still the stream"
    assert combat.randbelow(1000) == first


def test_restore_states_winds_back_a_stream_made_after_the_export():
    streams = RngStreams(master_seed=42)
    streams.get("combat")
    saved = streams.export_states()
    late = streams.get("treasure")
    first = late.randbelow(1000)
    streams.restore_states(saved)
    assert streams.get("treasure").randbelow(1000) == first
    assert RngStreams(master_seed=42).get("treasure").randbelow(1000) == first


# ---------------------------------------------------------------------- constants (#166)


@pytest.mark.parametrize(
    ("module", "name"),
    [(clock_module, "ROUNDS_PER_TURN"), (character_module, "ABILITY_ROLL_ORDER")],
    ids=["ROUNDS_PER_TURN", "ABILITY_ROLL_ORDER"],
)
def test_a_constant_is_marked_final(module, name):
    """A type checker refuses a reassignment, so no docstring has to ask readers not to make one."""
    hints = typing.get_type_hints(module, include_extras=True)
    assert name in hints
    annotation = hints[name]
    assert annotation is typing.Final or typing.get_origin(annotation) is typing.Final


# ---------------------------------------------------------------------- unequip and a curse (#162)


def cursed_shield_session(*, worn: bool):
    game = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=7)
    fighter = game.party.members[0]
    assert game.execute(GrantItem(character_id=fighter.id, item_id="cursed_shield_minus_2")).accepted
    shield = next(item for item in fighter.inventory.items if isinstance(item, MagicItemInstance))
    if worn:
        assert game.execute(EquipItem(character_id=fighter.id, item_id=shield.instance_id)).accepted
    shield.cursed_revealed = True
    return game, fighter, shield


def test_unequipping_a_cursed_item_that_isnt_worn_says_it_isnt_worn():
    game, fighter, shield = cursed_shield_session(worn=False)
    with pytest.raises(ValueError, match="not equipped"):
        validate_unequip(fighter.inventory, shield)
    with pytest.raises(ValueError, match="not equipped"):
        unequip(fighter.inventory, shield)
    result = game.execute(UnequipItem(character_id=fighter.id, item_id=shield.instance_id))
    assert [rejection.code for rejection in result.rejections] == ["exploration.item.not_equipped"]


def test_unequipping_a_worn_cursed_item_still_reports_the_curse():
    game, fighter, shield = cursed_shield_session(worn=True)
    assert [rejection.code for rejection in validate_unequip(fighter.inventory, shield)] == ["items.curse.stuck"]
    result = game.execute(UnequipItem(character_id=fighter.id, item_id=shield.instance_id))
    assert [rejection.code for rejection in result.rejections] == ["items.curse.stuck"]


# ---------------------------------------------------------------------- a finding's facts (#162)


def findings_for(entry: ContentPackEntry) -> tuple[PackFinding, ...]:
    pack = ContentPack(name="The gnawing dark", sections=(PackSection(id="level-1", entries=(entry,)),))
    return validate_content_pack(pack, load_monsters(), load_equipment())


def test_a_finding_states_its_facts_as_params():
    guards = KeyedEncounter(monsters=(KeyedMonster(template_id="gone", count_fixed=4),))
    (finding,) = findings_for(ContentPackEntry(id="guard-post", name="Guard post", encounter=guards))
    assert finding.code == "pack.encounter.unknown_monster"
    assert dict(finding.params) == {"monster": "gone"}

    chest = FeatureSpec(id="chest", kind="treasure_cache", item_ids=("vorpal_spoon",))
    (finding,) = findings_for(ContentPackEntry(id="vault", name="Vault", features=(chest,)))
    assert finding.code == "pack.feature.unknown_item"
    assert dict(finding.params) == {"feature": "chest", "item": "vorpal_spoon"}


def test_a_finding_you_build_needs_no_params():
    finding = PackFinding(code="pack.encounter.unknown_monster", message="entry 'x' references unknown monster 'y'")
    assert dict(finding.params) == {}
