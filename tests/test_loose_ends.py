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


# ---------------------------------------------------------------------- the bag of holding (#166, item 30)


def with_bags(coins: int, bags: int = 1):
    from osrlib.core.items import CoinPurse, Inventory

    inventory = Inventory(purse=CoinPurse(gp=coins))
    for number in range(bags):
        inventory.items.append(MagicItemInstance(instance_id=f"magic-item-900{number}", template_id="bag_of_holding"))
    return inventory


@pytest.mark.parametrize(
    ("coins", "bags", "weight"),
    [
        (5000, 0, 5000),
        (0, 1, 0),
        (1, 1, 600),
        (5000, 1, 600),
        (10000, 1, 600),
        (10500, 1, 1100),
        (15000, 2, 1200),
        (25000, 2, 6200),
    ],
    ids=[
        "no bag",
        "an empty bag",
        "one coin in the bag",
        "half a bag",
        "a full bag",
        "a full bag and 500 coins over",
        "two bags",
        "two full bags and 5,000 coins over",
    ],
)
def test_a_bag_of_holding_takes_the_treasure_its_carrier_has(coins, bags, weight):
    """The SRD: up to 10,000 coins of weight go in the bag, and a full bag weighs 600 coins."""
    from osrlib.core.items import treasure_weight_coins

    assert treasure_weight_coins(with_bags(coins, bags)) == weight


def test_a_character_with_a_bag_of_holding_can_walk_out_with_the_hoard():
    from osrlib.core.items import movement_rate_feet
    from osrlib.core.ruleset import EncumbranceMode

    for mode in (EncumbranceMode.BASIC, EncumbranceMode.DETAILED):
        rules = Ruleset(encumbrance=mode)
        assert movement_rate_feet(with_bags(5000, bags=0), rules) == 0, mode
        assert movement_rate_feet(with_bags(5000), rules) > 0, mode


def test_a_haul_fills_the_bag_without_overloading_its_carrier():
    """The first coin in an empty bag adds the bag's 600 coins, so a share is sized by the weight it makes."""
    from crawl_fixtures import _member
    from osrlib.core.items import Coins
    from osrlib.core.ruleset import EncumbranceMode
    from osrlib.crawl.adventure import Adventure, TownSpec
    from osrlib.crawl.commands import EnterDungeon, TakeTreasure
    from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
    from osrlib.crawl.party import Party

    hoard = FeatureSpec(id="hoard", kind="treasure_cache", cell=(0, 0), coins=Coins(gp=20000))
    level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0), features=(hoard,))
    crypt = DungeonSpec(id="crypt", name="Crypt", levels=(level,))
    adventure = Adventure(name="A", town=TownSpec(name="Threshold", travel_turns={"crypt": 1}), dungeons=(crypt,))
    rules = Ruleset(encumbrance=EncumbranceMode.DETAILED)
    game = GameSession.new(Party(members=[_member("Brakk", "fighter")]), adventure, seed=7, ruleset=rules)
    brakk = game.party.members[0]
    for item_id in ("plate_mail", "shield", "sword"):
        assert game.execute(GrantItem(character_id=brakk.id, item_id=item_id)).accepted
        assert game.execute(EquipItem(character_id=brakk.id, item_id=item_id)).accepted
    assert game.execute(GrantItem(character_id=brakk.id, item_id="bag_of_holding")).accepted
    assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted

    assert game.execute(TakeTreasure(feature_id="hoard")).accepted
    assert brakk.inventory.purse.gp >= 10000, "the bag takes its 10,000 coins"
    assert brakk.movement_rate(rules) > 0, "and its carrier can still walk"
    pile = next(iter(game.dungeon_state.piles.values()), None)
    left = pile.coins.gp if pile is not None else 0
    assert brakk.inventory.purse.gp + left == 20000, "the rest stays on the floor"


def test_equipping_one_of_two_equal_items_moves_that_one():
    from osrlib.core.items import Inventory, ItemInstance, equip
    from osrlib.data import load_classes

    dagger = load_equipment().get("dagger")
    inventory = Inventory(items=[ItemInstance(template=dagger, quantity=1), ItemInstance(template=dagger, quantity=1)])
    first, second = inventory.items
    equip(inventory, load_classes().get("fighter"), second)
    assert [item is second for item in inventory.wielded] == [True]
    assert [item is first for item in inventory.items] == [True]
    unequip(inventory, second)
    assert inventory.wielded == []
    assert sorted(id(item) for item in inventory.items) == sorted((id(first), id(second)))
