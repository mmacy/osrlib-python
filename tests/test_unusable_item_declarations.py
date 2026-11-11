"""A battle declaration with an item that can't do what's declared is refused whole (issue #166).

`resolve_attack` refuses an item with no combat stats, and `resolve_splash_attack` refuses an item
that isn't a splash weapon. The session has to refuse the matching declarations before the round
resolves, or an accepted `ResolveBattleRound` would raise partway through.
"""

from crawl_fixtures import build_adventure, build_party
from osrlib.core.items import MagicItemInstance
from osrlib.core.tables import ReactionResult
from osrlib.crawl.commands import (
    BattleDeclaration,
    EngageBattle,
    EnterDungeon,
    EquipItem,
    GrantItem,
    ReorderParty,
    ResolveBattleRound,
    SessionMode,
)
from osrlib.crawl.encounter import start_encounter
from osrlib.crawl.session import GameSession

UNUSABLE = "battle.declaration.item_unusable"


def battle(lead_index: int = 0) -> tuple[GameSession, str]:
    """The stock party in a battle with two goblins in melee reach, with one member at the front."""
    game = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=7)
    members = [member.id for member in game.party.members]
    lead = members.pop(lead_index)
    assert game.execute(ReorderParty(order=(lead, *members))).accepted
    assert game.execute(EnterDungeon(dungeon_id=game.adventure.dungeons[0].id)).accepted
    return game, lead


def fight(game: GameSession) -> str:
    start_encounter(
        game,
        groups=[("goblin", game.spawn("goblin", 2))],
        kind="spawned",
        distance_feet=5,
        pinned_stance=ReactionResult.FRIENDLY,
    )
    assert game.execute(EngageBattle()).accepted
    assert game.mode is SessionMode.BATTLE
    return game.encounter.groups[0].id


def round_with(game: GameSession, declaration: BattleDeclaration):
    """Resolve a round in which one member makes `declaration` and the others hold."""
    others = [
        BattleDeclaration(character_id=member.id, action="hold")
        for member in game.party.living_members()
        if member.id != declaration.character_id
    ]
    logged, clock = len(game.command_log), game.clock.rounds
    result = game.execute(ResolveBattleRound(declarations=(declaration, *others)))
    return result, (len(game.command_log) - logged, game.clock.rounds - clock)


def test_an_attack_with_a_wielded_wand_is_refused():
    game, mage = battle(lead_index=3)
    assert game.execute(GrantItem(character_id=mage, item_id="wand_of_fire_balls")).accepted
    wand = next(item for item in game.member(mage).inventory.items if isinstance(item, MagicItemInstance))
    assert game.execute(EquipItem(character_id=mage, item_id=wand.instance_id)).accepted
    group_id = fight(game)

    declaration = BattleDeclaration(
        character_id=mage, action="attack", target_group_id=group_id, weapon_id=wand.instance_id
    )
    result, changed = round_with(game, declaration)
    assert [(rejection.code, rejection.params["item"]) for rejection in result.rejections] == [
        (UNUSABLE, wand.instance_id)
    ]
    assert changed == (0, 0)


def test_throwing_an_item_that_isnt_a_splash_weapon_is_refused():
    game, fighter = battle()
    for item_id in ("torch", "oil_flask"):
        assert game.execute(GrantItem(character_id=fighter, item_id=item_id)).accepted
    group_id = fight(game)

    torch = BattleDeclaration(character_id=fighter, action="use_item", item_id="torch", target_group_id=group_id)
    result, changed = round_with(game, torch)
    assert [(rejection.code, rejection.params["item"]) for rejection in result.rejections] == [(UNUSABLE, "torch")]
    assert changed == (0, 0)

    oil = BattleDeclaration(character_id=fighter, action="use_item", item_id="oil_flask", target_group_id=group_id)
    result, changed = round_with(game, oil)
    assert result.accepted
    assert changed[0] == 1
