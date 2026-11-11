"""An item that sets Strength changes every number Strength gives (found while building issue #231).

The SRD's gauntlets of ogre power say: "A character who wears these gauntlets has a Strength score of
18. This grants all the usual bonuses." The ring of weakness reduces the wearer's Strength to 3. So a
character's melee modifier and chance to force a door come from the score in effect, on the
character itself, and the session and the player view report the same numbers combat uses.
"""

from crawl_fixtures import build_adventure, build_party
from osrlib.core.abilities import AbilityScore
from osrlib.core.combat import melee_modifier_for
from osrlib.core.effects import ActiveModifier
from osrlib.core.events import Visibility
from osrlib.core.items import MagicItemInstance
from osrlib.crawl.commands import EquipItem, GrantItem, UnequipItem
from osrlib.crawl.session import GameSession

WEAKNESS = ActiveModifier(kind="strength_set", value=3, from_item=True, effect_id="effect-9001")
"""What the ring of weakness's curse puts on its wearer once its onset ends."""


def session() -> GameSession:
    return GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=7)


def give(game: GameSession, item_id: str, *, worn: bool) -> MagicItemInstance:
    fighter = game.party.members[0]
    assert game.execute(GrantItem(character_id=fighter.id, item_id=item_id)).accepted
    item = next(
        entry
        for entry in fighter.inventory.items
        if isinstance(entry, MagicItemInstance) and entry.template_id == item_id
    )
    if worn:
        assert game.execute(EquipItem(character_id=fighter.id, item_id=item.instance_id)).accepted
    return item


def numbers(character) -> tuple[int, int, int]:
    return character.melee_modifier, melee_modifier_for(character), character.open_doors_chance


def test_the_stock_fighter_has_average_strength():
    fighter = session().party.members[0]
    assert fighter.scores[AbilityScore.STR] == 11
    assert numbers(fighter) == (0, 0, 2)


def test_worn_gauntlets_of_ogre_power_give_strength_18():
    game = session()
    gauntlets = give(game, "gauntlets_of_ogre_power", worn=True)
    fighter = game.party.members[0]
    assert numbers(fighter) == (3, 3, 5)
    assert fighter.scores[AbilityScore.STR] == 11, "the rolled score doesn't change"

    assert game.execute(UnequipItem(character_id=fighter.id, item_id=gauntlets.instance_id)).accepted
    assert numbers(fighter) == (0, 0, 2)


def test_gauntlets_in_the_pack_do_nothing():
    game = session()
    give(game, "gauntlets_of_ogre_power", worn=False)
    assert numbers(game.party.members[0]) == (0, 0, 2)


def test_the_ring_of_weakness_gives_strength_3():
    fighter = session().party.members[0]
    fighter.stat_modifiers = (WEAKNESS,)
    assert numbers(fighter) == (-3, -3, 1)


def test_the_curse_wins_over_the_gauntlets():
    game = session()
    give(game, "gauntlets_of_ogre_power", worn=True)
    fighter = game.party.members[0]
    fighter.stat_modifiers = (WEAKNESS,)
    assert numbers(fighter) == (-3, -3, 1)


def test_the_player_view_shows_the_numbers_combat_uses():
    game = session()
    give(game, "gauntlets_of_ogre_power", worn=True)
    sheet = game.view(Visibility.PLAYER).party[0]
    assert (sheet.melee_modifier, sheet.open_doors_chance) == (3, 5)
    assert sheet.scores["str"] == 11


def test_the_xp_modifier_still_reads_the_rolled_score():
    """A prime requisite earns its experience bonus from the score the character rolled."""
    from osrlib.core.classes import xp_modifier_pct

    game = session()
    fighter = game.party.members[0]
    before = xp_modifier_pct(fighter.definition, fighter.scores)
    give(game, "gauntlets_of_ogre_power", worn=True)
    assert xp_modifier_pct(fighter.definition, fighter.scores) == before == 0


# ---------------------------------------------------------------------- the score in effect, by name


def test_the_character_states_its_strength_in_effect():
    game = session()
    fighter = game.party.members[0]
    assert fighter.effective_strength == 11
    give(game, "gauntlets_of_ogre_power", worn=True)
    assert fighter.effective_strength == 18
    fighter.stat_modifiers = (WEAKNESS,)
    assert fighter.effective_strength == 3
    assert fighter.scores[AbilityScore.STR] == 11


def test_the_player_view_states_the_strength_in_effect():
    game = session()
    assert game.view(Visibility.PLAYER).party[0].effective_strength == 11
    give(game, "gauntlets_of_ogre_power", worn=True)
    sheet = game.view(Visibility.PLAYER).party[0]
    assert (sheet.effective_strength, sheet.scores["str"]) == (18, 11)


def test_an_intelligent_swords_contest_uses_the_strength_in_effect():
    """The SRD's control check compares the sword's will with "the sum of STR and WIS scores"."""
    from osrlib.core.items import SwordSentience, sword_control_check
    from osrlib.core.rng import RngStreams

    def wielder_will(game: GameSession) -> int:
        fighter = game.party.members[0]
        sentience = SwordSentience(
            intelligence=12,
            ego=12,
            communication="speech",
            reading=True,
            alignment=fighter.alignment.value,
            extraordinary_powers=("telekinesis",),
        )
        sword = MagicItemInstance(
            instance_id="magic-item-9001", template_id="sword_plus_1", base_item_id="sword", sentience=sentience
        )
        assert fighter.current_hp == fighter.max_hp, "an unhurt wielder loses nothing from their will"
        return sword_control_check(fighter, sword, stream=RngStreams(master_seed=47).get("magic")).wielder_will

    plain = session()
    assert wielder_will(plain) == 11 + 11
    gauntleted = session()
    give(gauntleted, "gauntlets_of_ogre_power", worn=True)
    assert wielder_will(gauntleted) == 18 + 11
    gauntleted.party.members[0].stat_modifiers = (WEAKNESS,)
    assert wielder_will(gauntleted) == 3 + 11
