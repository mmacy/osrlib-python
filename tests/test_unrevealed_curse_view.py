"""Acceptance tests for the player view of cursed armour and shields before the curse is revealed (issue 267).

The OSE SRD's Armour and Shields page says: "All types of tests indicate that a cursed shield or suit of armour is
enchanted with a +1 bonus. It is only when the armour is used in deadly combat that the curse is revealed." It also
says: "Once a cursed shield or suit of armour has been used, the character comes under the curse and is not able to
discard the item."

The contract these tests check:

- Until the curse is revealed, `MemberView.armour_class` and `MemberView.armour_class_ascending` count a worn cursed
  shield or suit of armour as the +1 item it tests as: its base item with a +1 enchantment, and no armour class of its
  own. A plain shield gives +1, so the masked cursed shield gives +2 in all, as a shield +1 does.
- The rules don't change. `Character.armour_class`, the referee view, and the `defender_ac` of the attacks the
  monsters roll all keep the real number, and taking the item off before the first battle stays legal.
- The session reveals the curse, and identifies the item, the first time the member is attacked in a battle or when a
  referee sends `IdentifyItem`. From then on the player view shows the real number, and it keeps showing it after
  *remove curse* frees the member, because the item stays identified.

The stock fighter has scores of 11, so the DEX modifier is 0 unless a test sets it. Printed armour classes, from the
OSE SRD's Armour and Shields page: unarmoured 9 [10], leather 7 [12], chainmail 5 [14], plate mail 3 [16], and a
shield +1.
"""

import pytest

from crawl_fixtures import build_party
from osrlib.core.abilities import AbilityScore
from osrlib.core.events import AttackRolledEvent, Visibility
from osrlib.core.items import Coins, MagicItemInstance
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import (
    BattleDeclaration,
    EquipItem,
    GrantCoins,
    GrantItem,
    IdentifyItem,
    PurchaseHealing,
    ResolveBattleRound,
    SpawnMonsters,
    UnequipItem,
)
from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
from osrlib.crawl.events import CurseRevealedEvent, ItemAcquiredEvent
from osrlib.crawl.session import GameSession
from osrlib.crawl.views import MemberView, PlayerView, RefereeView
from positions_fixtures import HILD, SEEDS, _opened, named
from positions_fixtures import session as dungeon_session

CHUNK = "chunk: unrevealed-curse-view"
FIGHTER = "character-0001"

CASES = [
    # A shield adds the plain shield's +1 and its enchantment. Masked as a shield +1: 9 - (1 + 1) = 7, 10 + 2 = 12.
    # Real -2: 9 - (1 - 2) = 10, 10 + (1 - 2) = 9.
    pytest.param("cursed_shield_minus_2", None, (7, 12), (10, 9), id="shield_minus_2"),
    # Real AC 9 [10]: the shield sets the base outright and adds nothing.
    pytest.param("cursed_shield_ac_9", None, (7, 12), (9, 10), id="shield_ac_9"),
    # A suit adds its enchantment to its base. Masked as chainmail +1: 5 - 1 = 4, 14 + 1 = 15. Real -1: 6 [13].
    pytest.param("cursed_armour_minus_1", "chainmail", (4, 15), (6, 13), id="armour_minus_1_chainmail"),
    # Real -2 on chainmail: 5 + 2 = 7, 14 - 2 = 12.
    pytest.param("cursed_armour_minus_2", "chainmail", (4, 15), (7, 12), id="armour_minus_2_chainmail"),
    # Masked, the AC 9 [10] suit sets nothing and reads as chainmail +1. Real: 9 [10] outright.
    pytest.param("cursed_armour_ac_9", "chainmail", (4, 15), (9, 10), id="armour_ac_9_chainmail"),
    # Leather 7 [12]: masked 6 [13], real -1 is 8 [11].
    pytest.param("cursed_armour_minus_1", "leather", (6, 13), (8, 11), id="armour_minus_1_leather"),
    # Plate 3 [16]: masked 2 [17], real -2 is 5 [14].
    pytest.param("cursed_armour_minus_2", "plate_mail", (2, 17), (5, 14), id="armour_minus_2_plate"),
    # Plate 3 [16]: masked 2 [17], real 9 [10] outright.
    pytest.param("cursed_armour_ac_9", "plate_mail", (2, 17), (9, 10), id="armour_ac_9_plate"),
]

LOOKALIKES = [
    pytest.param("cursed_shield_minus_2", "shield_plus_1", id="shield_minus_2"),
    pytest.param("cursed_shield_ac_9", "shield_plus_1", id="shield_ac_9"),
    pytest.param("cursed_armour_minus_1", "armour_plus_1", id="armour_minus_1"),
    pytest.param("cursed_armour_minus_2", "armour_plus_1", id="armour_minus_2"),
    pytest.param("cursed_armour_ac_9", "armour_plus_1", id="armour_ac_9"),
]
"""Each cursed template beside the +1 item it tests as."""


def _town(seed: int = 5) -> GameSession:
    """The stock party in a town with a temple, with gold for *remove curse* in the fighter's purse."""
    level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
    crypt = DungeonSpec(id="crypt", name="Crypt", levels=(level,))
    town = TownSpec(name="Threshold", services=("temple",), travel_turns={"crypt": 1})
    game = GameSession.new(build_party(), Adventure(name="A", town=town, dungeons=(crypt,)), seed=seed)
    assert game.execute(GrantCoins(character_id=FIGHTER, coins=Coins(gp=1000))).accepted
    return game


def _granted(game: GameSession, member_id: str, item_id: str) -> str:
    result = game.execute(GrantItem(character_id=member_id, item_id=item_id))
    assert result.accepted, [rejection.code for rejection in result.rejections]
    (instance_id,) = next(event.item_ids for event in result.events if isinstance(event, ItemAcquiredEvent))
    return instance_id


def _wear(game: GameSession, item_id: str, base: str | None = None, member_id: str = FIGHTER) -> MagicItemInstance:
    """Grant `item_id` to the member, give a suit the `base` item, and equip it. Returns the worn instance."""
    instance_id = _granted(game, member_id, item_id)
    instance = game.member(member_id).inventory.magic_item(instance_id)
    assert instance is not None
    if base is not None:
        instance.base_item_id = base
    equipped = game.execute(EquipItem(character_id=member_id, item_id=instance_id))
    assert equipped.accepted, [rejection.code for rejection in equipped.rejections]
    assert not instance.identified and not instance.cursed_revealed, "precondition: the curse is still hidden"
    return instance


def _player_view(game: GameSession) -> PlayerView:
    view = game.view(Visibility.PLAYER)
    assert isinstance(view, PlayerView)
    return view


def _sheet(game: GameSession, member_id: str = FIGHTER) -> MemberView:
    return next(member for member in _player_view(game).party if member.id == member_id)


def _shown(game: GameSession, member_id: str = FIGHTER) -> tuple[int, int]:
    sheet = _sheet(game, member_id)
    return sheet.armour_class, sheet.armour_class_ascending


def _real(game: GameSession, member_id: str = FIGHTER) -> tuple[int, int]:
    member = game.member(member_id)
    return member.armour_class, member.armour_class_ascending


def _refereed(game: GameSession, member_id: str = FIGHTER) -> tuple[int, int]:
    view = game.view(Visibility.REFEREE)
    assert isinstance(view, RefereeView)
    member = next(member for member in view.party.members if member.id == member_id)
    return member.armour_class, member.armour_class_ascending


# ---------------------------------------------------------------------- before the reveal


@pytest.mark.xfail(reason=CHUNK)
@pytest.mark.parametrize(("item_id", "base", "masked", "real"), CASES)
def test_an_unrevealed_cursed_item_reads_as_the_plus_1_item_it_tests_as(item_id, base, masked, real):
    game = _town()

    _wear(game, item_id, base)

    assert _shown(game) == masked
    assert _real(game) == real, "the character keeps the armour class the monsters roll against"
    assert _refereed(game) == real, "the referee sees the real number"


@pytest.mark.xfail(reason=CHUNK)
@pytest.mark.parametrize(("cursed", "lookalike"), LOOKALIKES)
def test_the_player_view_and_events_match_the_plus_1_item(cursed, lookalike):
    """Everything the players get, the whole view and every event of the grant and the equip, is the same."""
    seen = []
    for item_id in (cursed, lookalike):
        game = _town()
        granted = game.execute(GrantItem(character_id=FIGHTER, item_id=item_id))
        equipped = game.execute(EquipItem(character_id=FIGHTER, item_id="magic-item-0001"))
        assert granted.accepted and equipped.accepted
        events = [
            event.model_dump(mode="json")
            for event in (*granted.events, *equipped.events)
            if event.visibility is Visibility.PLAYER
        ]
        seen.append((_player_view(game).model_dump(mode="json"), events))

    cursed_view, cursed_events = seen[0]
    lookalike_view, lookalike_events = seen[1]
    assert cursed_events == lookalike_events
    assert cursed_view == lookalike_view


@pytest.mark.xfail(reason=CHUNK)
def test_unequipping_before_the_first_battle_stays_legal():
    """The SRD binds the curse once the armour "has been used", so an unused item comes off."""
    game = _town()
    shield = _wear(game, "cursed_shield_minus_2")
    assert _shown(game) == (7, 12)

    result = game.execute(UnequipItem(character_id=FIGHTER, item_id=shield.instance_id))

    assert result.accepted, [rejection.code for rejection in result.rejections]
    assert not any(isinstance(event, CurseRevealedEvent) for event in result.events)
    assert _shown(game) == _real(game) == (9, 10), "unarmoured again"


@pytest.mark.xfail(reason=CHUNK)
def test_the_rest_of_the_suit_counts_beside_the_masked_shield():
    """A ring of protection adds 1. Masked: 9 - 2 - 1 = 6 [10 + 2 + 1 = 13]. Real: 9 - (-1) - 1 = 9 [10]."""
    game = _town()
    _wear(game, "cursed_shield_minus_2")
    ring_id = _granted(game, FIGHTER, "ring_of_protection")
    assert game.execute(EquipItem(character_id=FIGHTER, item_id=ring_id)).accepted

    assert _shown(game) == (6, 13)
    assert _real(game) == (9, 10)


@pytest.mark.xfail(reason=CHUNK)
def test_cursed_armour_with_a_shield_plus_1_masks_only_the_armour():
    """The SRD's row "Cursed Armour -2 with Shield +1", in chainmail.

    Masked: chainmail +1 is 4, and the shield +1 gives 2 more, so 2 [17]. Real: chainmail -2 is 7, less 2, so 5 [14].
    """
    game = _town()
    _wear(game, "cursed_armour_minus_2", "chainmail")
    _wear(game, "shield_plus_1")

    assert _shown(game) == (2, 17)
    assert _real(game) == (5, 14)


@pytest.mark.xfail(reason=CHUNK)
def test_the_dex_modifier_counts_on_top_of_the_masked_armour_class():
    """DEX 16 gives -2 [+2]. The SRD's AC 9 [10] is "before modifiers due to Dexterity".

    Masked as a shield +1: 9 - 2 - 2 = 5 [10 + 2 + 2 = 14]. Real: 9 - 2 = 7 [10 + 2 = 12].
    """
    game = _town()
    game.member(FIGHTER).scores[AbilityScore.DEX] = 16
    _wear(game, "cursed_shield_ac_9")

    assert _shown(game) == (5, 14)
    assert _real(game) == (7, 12)


# ---------------------------------------------------------------------- the reveal


@pytest.mark.xfail(reason=CHUNK)
@pytest.mark.parametrize(("item_id", "base", "masked", "real"), CASES)
def test_identify_item_reveals_the_curse_and_the_real_numbers(item_id, base, masked, real):
    game = _town()
    instance = _wear(game, item_id, base)
    assert _shown(game) == masked

    result = game.execute(IdentifyItem(character_id=FIGHTER, item_id=instance.instance_id))

    assert any(isinstance(event, CurseRevealedEvent) for event in result.events)
    assert _shown(game) == real
    stuck = game.execute(UnequipItem(character_id=FIGHTER, item_id=instance.instance_id))
    assert [rejection.code for rejection in stuck.rejections] == ["items.curse.stuck"]


def _cursed_shield_battle(first_seed: int) -> GameSession:
    """Hild alone, in plate with a sword and an unrevealed cursed shield -2, facing two goblins 5' away.

    Returns the first session from `first_seed` on in which neither side was surprised.
    """
    for seed in range(first_seed, SEEDS.stop):
        game = dungeon_session(seed, (HILD,))
        hild = named(game, "Hild")
        _wear(game, "cursed_shield_minus_2", member_id=hild)
        spawn = SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=5)
        if _opened(game, game.execute(spawn), surprised=False, engage=True):
            return game
    raise AssertionError("precondition: no seed opened the battle")


@pytest.mark.xfail(reason=CHUNK)
def test_the_first_attack_in_a_battle_reveals_the_curse_and_monsters_roll_against_the_real_number():
    """Plate is 3 [16]. Masked as a shield +1: 3 - 2 = 1 [18]. Real -2: 3 - (1 - 2) = 4 [15]."""
    seed = SEEDS.start
    while seed < SEEDS.stop:
        game = _cursed_shield_battle(seed)
        seed = game.master_seed + 1
        hild = named(game, "Hild")
        assert _shown(game, hild) == (1, 18), "the battle has begun and no one has attacked yet"
        assert _real(game, hild) == (4, 15)

        result = game.execute(ResolveBattleRound(declarations=(BattleDeclaration(character_id=hild, action="hold"),)))

        assert result.accepted, [rejection.code for rejection in result.rejections]
        swings = [
            event for event in result.events if isinstance(event, AttackRolledEvent) and event.defender_id == hild
        ]
        if not swings:
            continue
        assert [swing.defender_ac for swing in swings] == [4] * len(swings), "the goblins roll against the real 4"
        revealed = [event for event in result.events if isinstance(event, CurseRevealedEvent)]
        assert [event.character_id for event in revealed] == [hild]
        assert _shown(game, hild) == (4, 15)
        return
    raise AssertionError("precondition: no seed had a goblin attack Hild")


@pytest.mark.xfail(reason=CHUNK)
def test_the_sheet_keeps_the_real_numbers_after_remove_curse():
    """*Remove curse* clears `cursed_revealed`, but the item stays identified, so the players know what it is."""
    game = _town()
    shield = _wear(game, "cursed_shield_minus_2")
    assert _shown(game) == (7, 12)
    assert game.execute(IdentifyItem(character_id=FIGHTER, item_id=shield.instance_id)).accepted

    lifted = game.execute(PurchaseHealing(character_id=FIGHTER, service="remove_curse"))

    assert lifted.accepted, [rejection.code for rejection in lifted.rejections]
    assert shield.identified and not shield.cursed_revealed, "precondition: the curse is lifted from the bearer"
    assert _shown(game) == _real(game) == (10, 9)


# ---------------------------------------------------------------------- what the SRD leaves alone


def test_a_cursed_sword_shows_the_same_sheet_as_a_sword_plus_1_before_it_strikes():
    """The SRD gives cursed weapons no "tests as" rule, and the sheet has no per-weapon number to give one away.

    `thac0` and `attack_bonus` are the member's own, so the whole player view of an unidentified sword -1, cursed is
    the view of an unidentified sword +1. The penalty first shows in the attack roll that also reveals the curse.
    """
    views = []
    for item_id in ("sword_minus_1_cursed", "sword_plus_1"):
        game = _town()
        _wear(game, item_id)
        views.append(_player_view(game).model_dump(mode="json"))

    assert views[0] == views[1]
