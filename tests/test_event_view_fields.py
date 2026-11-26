"""Acceptance tests for the additive event and player-view fields planned for osrlib 2.7.0.

- Issue 285: events and piles that report `coins_gp_value` also carry the exact `coins`, and the
  messages describe small amounts instead of printing nothing.
- Issue 286: `XpAwardedEvent` reports what the character gained and whether the one-level limit cut it.
- Issue 287: every declaration rejection that `ResolveBattleRound` returns names the declaring member.
- Issue 269: `EncounterGroupView` says when the group is fleeing.
- Issue 257: `MemberView` carries the class's name.

Everything here is additive within the current `schema_version`: `coins_gp_value` keeps its meaning.
"""

from crawl_fixtures import build_adventure, build_party
from osrlib.core.events import Visibility
from osrlib.core.items import Coins
from osrlib.crawl import events as events_module
from osrlib.crawl.commands import (
    AwardXP,
    BattleDeclaration,
    DropItems,
    EnterDungeon,
    GrantCoins,
    RemoveCoins,
    ResolveBattleRound,
)
from osrlib.crawl.events import ItemAcquiredEvent, ItemsRemovedEvent, LootDroppedEvent, MonsterFledEvent, XpAwardedEvent
from osrlib.crawl.session import GameSession, SessionMode
from osrlib.crawl.views import PlayerView
from osrlib.messages import format_message
from test_battle import battle_session, hold_all, prepare_spell

FIGHTER = "character-0001"


def _session() -> GameSession:
    return GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=5)


def _player_view(session: GameSession) -> PlayerView:
    view = session.view(Visibility.PLAYER)
    assert isinstance(view, PlayerView)
    return view


def _only(events, event_class):
    found = [event for event in events if isinstance(event, event_class)]
    assert len(found) == 1, [type(event).__name__ for event in events]
    return found[0]


# ---------------------------------------------------------------------- issue 285: exact coins


def test_every_event_that_reports_a_gp_value_also_carries_the_coins():
    """The contract: no event class reports `coins_gp_value` without `coins` beside it."""
    classes = [
        value
        for value in vars(events_module).values()
        if isinstance(value, type) and "coins_gp_value" in getattr(value, "model_fields", {})
    ]
    assert classes
    assert [cls.__name__ for cls in classes if "coins" not in cls.model_fields] == []


def test_a_grant_of_copper_reports_every_coin_and_reads_as_copper():
    session = _session()

    result = session.execute(GrantCoins(character_id=FIGHTER, coins=Coins(cp=39)))

    event = _only(result.events, ItemAcquiredEvent)
    assert event.coins == Coins(cp=39)
    assert event.coins_gp_value == 0
    message = format_message(event)
    assert "39 cp" in message
    assert " ." not in message


def test_a_removal_of_one_copper_reports_that_coin():
    session = _session()
    assert session.execute(GrantCoins(character_id=FIGHTER, coins=Coins(gp=2, cp=5))).accepted

    result = session.execute(RemoveCoins(character_id=FIGHTER, coins=Coins(cp=1)))

    event = _only(result.events, ItemsRemovedEvent)
    assert event.coins == Coins(cp=1)
    assert "1 cp" in format_message(event)


def test_a_pile_of_copper_alone_is_not_an_empty_pile():
    session = _session()
    assert session.execute(GrantCoins(character_id=FIGHTER, coins=Coins(cp=48))).accepted
    assert session.execute(EnterDungeon(dungeon_id="delve")).accepted

    result = session.execute(DropItems(character_id=FIGHTER, coins=Coins(cp=48)))

    assert result.accepted, [rejection.code for rejection in result.rejections]
    dropped = [event for event in result.events if getattr(event, "coins_gp_value", None) is not None]
    assert [event.coins for event in dropped] == [Coins(cp=48)]
    piles = list(_player_view(session).piles.values())
    assert [pile.coins for pile in piles] == [Coins(cp=48)]
    assert piles[0].coins_gp_value == 0


def test_loot_of_copper_alone_names_the_copper():
    message = format_message(LootDroppedEvent(coins=Coins(cp=39)))

    assert "39 cp" in message
    assert "behind ." not in message


def test_a_message_names_each_denomination_that_moved():
    message = format_message(ItemAcquiredEvent(character_id=FIGHTER, coins=Coins(gp=3, sp=4), coins_gp_value=3))

    assert "3 gp" in message
    assert "4 sp" in message


# ---------------------------------------------------------------------- issue 286: the experience gained


def test_an_award_the_one_level_limit_cuts_reports_the_gain_and_the_cut():
    session = _session()
    before = session.member(FIGHTER).xp

    result = session.execute(AwardXP(character_id=FIGHTER, amount=1_000_000))

    event = _only(result.events, XpAwardedEvent)
    gained = session.member(FIGHTER).xp - before
    assert event.clamped is True
    assert event.gained == gained
    assert event.gained < event.modified_award
    message = format_message(event)
    assert f"{gained:,}" in message or str(gained) in message
    assert "limit" in message


def test_an_award_within_the_limit_reports_the_whole_award_as_gained():
    session = _session()
    before = session.member(FIGHTER).xp

    result = session.execute(AwardXP(character_id=FIGHTER, amount=10))

    event = _only(result.events, XpAwardedEvent)
    assert event.clamped is False
    assert event.gained == event.modified_award == session.member(FIGHTER).xp - before
    assert "limit" not in format_message(event)


# ---------------------------------------------------------------------- issue 287: the declaring member


def test_every_declaration_rejection_names_the_member_who_declared():
    """A sword swing at 40' and a magic missile with too many targets, refused in one round."""
    session = battle_session(count=2, distance=40)
    assert session.mode is SessionMode.BATTLE
    caster_id = prepare_spell(session, "magic_missile")
    swing = BattleDeclaration(character_id=FIGHTER, action="attack", group_id=session.encounter.groups[0].id)
    missiles = BattleDeclaration(
        character_id=caster_id,
        action="cast",
        spell_id="magic_missile",
        spell_mode="missiles",
        targets=tuple(session.encounter.groups[0].monster_ids),
    )

    result = session.execute(ResolveBattleRound(declarations=hold_all(session, extra=(swing, missiles))))

    by_code = {rejection.code: rejection.params.get("character") for rejection in result.rejections}
    assert by_code.get("magic.cast.target_count") == caster_id
    assert by_code.get("combat.attack.out_of_reach", FIGHTER) == FIGHTER
    assert FIGHTER in by_code.values()
    assert all(rejection.params.get("character") in (FIGHTER, caster_id) for rejection in result.rejections)


# ---------------------------------------------------------------------- issue 269: the group view


def test_a_group_view_says_the_group_is_fleeing():
    session = battle_session(count=3, distance=40)
    assert _player_view(session).encounter.groups[0].fleeing is False

    session.encounter.groups[0].fleeing = True

    (view_group,) = _player_view(session).encounter.groups
    assert view_group.fleeing is True
    assert view_group.fled is False


def test_the_fled_message_does_not_say_the_group_left():
    message = format_message(MonsterFledEvent(code="battle.side.fled", group_id="group-0001"))

    assert "group-0001" in message
    assert "flees the battle" not in message


def test_a_member_view_names_the_class():
    session = _session()

    for member_view in _player_view(session).party:
        assert member_view.class_name == session.member(member_view.id).definition.name
        assert member_view.class_name
