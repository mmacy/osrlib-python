"""Events a front end needs and didn't get, and two battle states a direct call left wrong.

A front end that follows the event stream has to learn everything from it. These cases come from
issues #162 and #167: a trap find that didn't say who found it, dropped rations reported under an id
no catalog has, loot that appeared on the floor with no event, and a new marching order with no
event. The last two tests are session procedures that left a battle in a state no command could.
"""

from collections import Counter

import pytest

from crawl_fixtures import build_party
from osrlib.core.effects import kill
from osrlib.core.tables import ReactionResult
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.battle import start_battle
from osrlib.crawl.commands import (
    EngageBattle,
    EnterDungeon,
    Evade,
    GrantItem,
    LightSource,
    ReorderParty,
    Search,
    SessionMode,
    SpawnMonsters,
    SpawnNpcParty,
)
from osrlib.crawl.dungeon import (
    AreaSpec,
    DoorSpec,
    DungeonSpec,
    Edge,
    EdgeKind,
    LevelSpec,
    TrapEffect,
    TrapSpec,
    WanderingSpec,
)
from osrlib.crawl.encounter import end_encounter, start_encounter
from osrlib.crawl.session import GameSession
from osrlib.data import load_equipment
from osrlib.messages import format_message

QUIET = WanderingSpec(chance_in_six=0)


def session(level: LevelSpec | None = None, seed: int = 7) -> GameSession:
    """The stock party at the entrance of a one-level dungeon."""
    if level is None:
        edges = {f"{x},0:west": Edge(kind=EdgeKind.OPEN) for x in range(1, 4)}
        level = LevelSpec(number=1, width=4, height=1, entrance=(0, 0), edges=edges, wandering=QUIET)
    crypt = DungeonSpec(id="crypt", name="Crypt", levels=(level,))
    adventure = Adventure(name="A", town=TownSpec(name="Threshold", travel_turns={"crypt": 1}), dungeons=(crypt,))
    game = GameSession.new(build_party(), adventure, seed=seed)
    assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
    return game


def friendly_encounter(game: GameSession, distance_feet: int = 120) -> None:
    start_encounter(
        game,
        groups=[("goblin", game.spawn("goblin", 2))],
        kind="spawned",
        distance_feet=distance_feet,
        pinned_stance=ReactionResult.FRIENDLY,
    )


# ---------------------------------------------------------------------- who found the room trap (#167, item 19)


def test_a_room_trap_found_by_a_search_says_who_found_it():
    trap = TrapSpec(kind="room", trigger="open", effect=TrapEffect(damage_dice="1d6"))
    level = LevelSpec(
        number=1,
        width=2,
        height=1,
        entrance=(0, 0),
        areas=(AreaSpec(id="blade_room", cells=((1, 0),), trap=trap),),
        edges={"1,0:west": Edge(kind=EdgeKind.DOOR, door=DoorSpec())},
        wandering=QUIET,
    )
    for seed in range(60):
        game = session(level, seed=seed)
        searcher = game.party.members[0]
        for item_id in ("torch", "tinder_box"):
            game.execute(GrantItem(character_id=searcher.id, item_id=item_id))
        game.execute(LightSource(character_id=searcher.id, item_id="torch"))
        events = game.execute(Search(character_id=searcher.id, kind="room_traps")).events
        found = [event for event in events if event.code == "exploration.trap.found"]
        if found:
            assert found[0].character_id == searcher.id
            return
    pytest.fail("no seed found the trap")


# ---------------------------------------------------------------------- dropped rations (#167, item 20)


def test_food_dropped_in_a_chase_is_reported_by_its_catalog_id():
    equipment = load_equipment()
    for seed in range(80):
        game = session(seed=seed)
        runner = game.party.members[0]
        game.execute(GrantItem(character_id=runner.id, item_id="rations_standard", quantity=7))
        game.execute(SpawnMonsters(template_id="normal_wolf", count_fixed=2, distance_feet=60))
        if game.mode is not SessionMode.ENCOUNTER:
            continue
        before = Counter(
            {
                instance.template.id: instance.quantity
                for member in game.party.members
                for instance in member.inventory.items
            }
        )
        events = game.execute(Evade(drop="food")).events
        dropped = [event for event in events if event.code == "exploration.item.dropped"]
        if not dropped:
            continue
        assert dropped[0].item_ids, "the event lists what was dropped"
        for item_id in dropped[0].item_ids:
            assert equipment.get(item_id).id in ("rations_standard", "rations_iron")
        after = Counter(
            {
                instance.template.id: instance.quantity
                for member in game.party.members
                for instance in member.inventory.items
            }
        )
        assert before - after == Counter(dropped[0].item_ids), "the ids are the rations the party lost, one id for each"
        return
    pytest.fail("no seed dropped food")


# ---------------------------------------------------------------------- loot on the floor (#167, item 21)


def test_loot_dropped_at_the_end_of_an_encounter_is_reported():
    game = session()
    assert game.execute(SpawnNpcParty(party_kind="basic", distance_feet=120)).accepted
    for group in game.encounter.groups:
        for monster_id in group.monster_ids:
            kill(game.combatant(monster_id))
    events = end_encounter(game, "victory")
    pile = next(iter(game.dungeon_state.piles.values()))
    loot = [event for event in events if event.code == "encounter.loot.dropped"]
    assert len(loot) == 1
    expected = Counter(item.instance_id for item in pile.magic_items)
    expected.update(valuable.instance_id for valuable in pile.valuables)
    for stack in pile.items:
        expected[stack.item_id] += stack.quantity
    assert Counter(loot[0].item_ids) == expected
    assert loot[0].coins_gp_value == pile.coins.value_gp
    assert [event.code for event in events].index("encounter.loot.dropped") < [event.code for event in events].index(
        "encounter.ended"
    )
    assert format_message(loot[0])


def test_an_encounter_that_drops_nothing_reports_no_loot():
    game = session()
    friendly_encounter(game)
    events = end_encounter(game, "evaded")
    assert "encounter.loot.dropped" not in [event.code for event in events]
    assert not game.dungeon_state.piles


# ---------------------------------------------------------------------- a new marching order (#162)


def test_reordering_the_party_emits_the_new_order():
    game = session()
    order = tuple(member.id for member in reversed(game.party.members))
    result = game.execute(ReorderParty(order=order))
    assert result.accepted
    reordered = [event for event in result.events if event.code == "exploration.party.reordered"]
    assert len(reordered) == 1
    assert reordered[0].order == order
    assert reordered[0].visibility.value == "player"
    assert format_message(reordered[0])


# ---------------------------------------------------------------------- battle state left wrong by a direct call


def test_start_battle_refuses_to_replace_an_open_battle():
    game = session()
    friendly_encounter(game, distance_feet=30)
    assert game.execute(EngageBattle()).accepted
    first = game.battle
    with pytest.raises(ValueError, match="battle"):
        start_battle(game)
    assert game.battle is first


def test_end_encounter_during_a_battle_ends_the_battle_too():
    game = session()
    friendly_encounter(game, distance_feet=30)
    assert game.execute(EngageBattle()).accepted
    assert game.mode is SessionMode.BATTLE
    end_encounter(game, "evaded")
    assert game.mode is SessionMode.EXPLORING
    assert game.encounter is None
    assert game.battle is None
