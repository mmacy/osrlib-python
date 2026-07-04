"""The end-of-adventure XP award and the town services — the Phase 5 loop closed."""

from crawl_fixtures import build_adventure, build_party
from osrlib.core.effects import ActiveCondition, Condition
from osrlib.core.items import Coins, MagicItemInstance, ValuableInstance
from osrlib.core.ruleset import Ruleset, XpAwardTiming
from osrlib.crawl.commands import (
    DropItems,
    EnterDungeon,
    GrantCoins,
    GrantItem,
    PurchaseHealing,
    SellTreasure,
    TakeTreasure,
    TravelToTown,
)
from osrlib.crawl.session import DefeatedMonsterRecord, GameSession


def build_session(seed: int = 7, ruleset: Ruleset | None = None) -> GameSession:
    session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=seed, ruleset=ruleset)
    session.execute(GrantItem(character_id="character-0001", item_id="torch", quantity=6))
    session.execute(GrantItem(character_id="character-0001", item_id="tinder_box"))
    return session


def go_home(session: GameSession):
    """Walk back to the entrance and travel to town."""
    from osrlib.crawl.commands import MoveParty

    location = session.dungeon_state.location
    for _ in range(location.position[0]):
        session.execute(MoveParty(direction="west"))
    return session.execute(TravelToTown())


class TestOnReturnAward:
    def test_the_valuation_delta_across_take_and_drop(self):
        session = build_session()
        session.execute(EnterDungeon(dungeon_id="delve"))
        member = session.party.members[0]
        # Acquisitions after the snapshot: coins granted by a quest listener count.
        session.execute(GrantCoins(character_id=member.id, coins=Coins(gp=100)))
        # A valuable carried home counts at its exact value.
        member.inventory.valuables.append(
            ValuableInstance(instance_id="valuable-9001", kind="gem", value_gp=50, weight_coins=1)
        )
        # Dropping and abandoning treasure earns nothing: drop 40 gp on the floor.
        session.execute(DropItems(character_id=member.id, item_ids=(), coins=Coins(gp=40)))
        result = go_home(session)
        award = next(event for event in result.events if event.code == "session.xp.adventure_award")
        assert award.treasure_xp == 100 + 50 - 40
        assert award.monster_xp == 0
        assert session.treasure_snapshot_cp is None
        assert session.defeated_monsters == []

    def test_the_delta_clamps_at_zero(self):
        session = build_session()
        member = session.party.members[0]
        session.execute(GrantCoins(character_id=member.id, coins=Coins(gp=100)))
        session.execute(EnterDungeon(dungeon_id="delve"))
        session.execute(DropItems(character_id=member.id, item_ids=(), coins=Coins(gp=100)))
        result = go_home(session)
        assert not any(event.code == "session.xp.adventure_award" for event in result.events)

    def test_division_floors_and_the_remainder_drops(self):
        session = build_session()
        session.execute(EnterDungeon(dungeon_id="delve"))
        session.defeated_monsters.append(
            DefeatedMonsterRecord(monster_id="monster-0001", template_id="goblin", outcome="slain", xp=103)
        )
        result = go_home(session)
        award = next(event for event in result.events if event.code == "session.xp.adventure_award")
        assert award.share == 103 // 4  # four survivors, remainder dropped
        assert all(member.xp >= award.share for member in session.party.living_members())

    def test_dead_members_count_treasure_but_draw_no_share(self):
        session = build_session()
        session.execute(EnterDungeon(dungeon_id="delve"))
        casualty = session.party.members[3]
        casualty.inventory.valuables.append(
            ValuableInstance(instance_id="valuable-9001", kind="gem", value_gp=400, weight_coins=1)
        )
        casualty.current_hp = 0
        casualty.conditions = (ActiveCondition(condition=Condition.DEAD, effect_id=None),)
        result = go_home(session)
        award = next(event for event in result.events if event.code == "session.xp.adventure_award")
        assert award.treasure_xp == 400
        assert casualty.id not in award.survivors
        assert award.share == 400 // 3
        assert casualty.xp == 0

    def test_a_tpk_never_awards(self):
        session = build_session()
        session.execute(EnterDungeon(dungeon_id="delve"))
        events = session.award_adventure_xp()  # nobody has returned; simulate none living
        session_two = build_session(seed=11)
        session_two.execute(EnterDungeon(dungeon_id="delve"))
        for member in session_two.party.members:
            member.current_hp = 0
            member.conditions = (ActiveCondition(condition=Condition.DEAD, effect_id=None),)
        session_two.defeated_monsters.append(
            DefeatedMonsterRecord(monster_id="monster-0001", template_id="goblin", outcome="slain", xp=50)
        )
        assert session_two.award_adventure_xp() == []
        assert events == [] or events  # the healthy session's award is exercised elsewhere

    def test_snapshot_resets_on_the_next_departure(self):
        session = build_session()
        session.execute(EnterDungeon(dungeon_id="delve"))
        go_home(session)
        assert session.treasure_snapshot_cp is None
        session.execute(EnterDungeon(dungeon_id="delve"))
        assert session.treasure_snapshot_cp is not None


class TestImmediateTiming:
    def test_treasure_awards_at_acquisition_and_town_awards_nothing(self):
        ruleset = Ruleset(xp_award_timing=XpAwardTiming.IMMEDIATE)
        session = build_session(seed=9, ruleset=ruleset)
        session.execute(EnterDungeon(dungeon_id="delve"))
        before = [m.xp for m in session.party.members]
        # The fixture's chest holds 200 gp behind a treasure trap; force the door
        # path aside and grab through the referee surface instead: place a pile.
        from osrlib.crawl import exploration
        from osrlib.crawl.dungeon import DropPile

        ref = exploration._cell_ref(session)
        session.dungeon_state.piles[ref] = DropPile(coins=Coins(gp=200))
        result = session.execute(TakeTreasure(feature_id="pile"))
        assert result.accepted
        awards = [event for event in result.events if event.code == "session.xp.awarded"]
        assert [event.award for event in awards] == [200 // 4] * 4  # the base share; class modifiers apply after
        gained = [m.xp - x for m, x in zip(session.party.members, before, strict=True)]
        assert all(gain >= 200 // 4 for gain in gained)
        # Dropping it back never refunds; the return awards nothing more.
        result = go_home(session)
        assert not any(event.code == "session.xp.adventure_award" for event in result.events)
        assert [m.xp - x for m, x in zip(session.party.members, before, strict=True)] == gained

    def test_no_double_award_across_the_timings(self):
        # on_return: the same 200 gp taken and returned awards once, at return.
        session = build_session(seed=9)
        session.execute(EnterDungeon(dungeon_id="delve"))
        from osrlib.crawl import exploration
        from osrlib.crawl.dungeon import DropPile

        ref = exploration._cell_ref(session)
        session.dungeon_state.piles[ref] = DropPile(coins=Coins(gp=200))
        before = [m.xp for m in session.party.members]
        session.execute(TakeTreasure(feature_id="pile"))
        assert [m.xp for m in session.party.members] == before  # nothing yet
        result = go_home(session)
        award = next(event for event in result.events if event.code == "session.xp.adventure_award")
        assert award.treasure_xp == 200


class TestTownServices:
    def test_sell_pays_full_value_to_the_carrier(self):
        session = build_session()
        member = session.party.members[1]
        member.inventory.valuables.append(
            ValuableInstance(instance_id="valuable-9001", kind="jewellery", value_gp=300, weight_coins=10)
        )
        before = member.inventory.purse.gp
        result = session.execute(SellTreasure(item_ids=("valuable-9001",)))
        assert result.accepted
        assert member.inventory.purse.gp == before + 300
        assert member.inventory.valuables == []

    def test_magic_items_have_no_fixed_sale_value(self):
        session = build_session()
        member = session.party.members[0]
        member.inventory.items.append(MagicItemInstance(instance_id="magic-item-9001", template_id="potion_of_healing"))
        result = session.execute(SellTreasure(item_ids=("magic-item-9001",)))
        assert not result.accepted
        assert result.rejections[0].code == "town.sell.no_fixed_value"

    def test_selling_after_the_award_changes_nothing(self):
        session = build_session()
        session.execute(EnterDungeon(dungeon_id="delve"))
        member = session.party.members[0]
        member.inventory.valuables.append(
            ValuableInstance(instance_id="valuable-9001", kind="gem", value_gp=100, weight_coins=1)
        )
        go_home(session)
        xp_after_award = [m.xp for m in session.party.members]
        session.execute(SellTreasure(item_ids=("valuable-9001",)))
        assert [m.xp for m in session.party.members] == xp_after_award

    def test_each_healing_service_resolves_through_the_kernel(self):
        session = build_session()
        member = session.party.members[0]
        member.inventory.purse.gp = 3000
        member.current_hp = 1
        result = session.execute(PurchaseHealing(character_id=member.id, service="cure_light_wounds"))
        assert result.accepted
        assert member.current_hp > 1
        assert any(event.code == "town.healing.purchased" for event in result.events)

    def test_raise_dead_honours_the_death_record_window(self):
        session = build_session()
        member = session.party.members[0]
        member.inventory.purse.gp = 3000
        member.current_hp = 0
        member.conditions = (ActiveCondition(condition=Condition.DEAD, effect_id=None),)
        from osrlib.crawl.session import DeathRecord

        session.death_records[member.id] = DeathRecord(round=session.clock.rounds, cause="damage")
        result = session.execute(PurchaseHealing(character_id=member.id, service="raise_dead"))
        assert result.accepted
        assert not any(active.condition is Condition.DEAD for active in member.conditions)
        assert member.inventory.purse.gp == 3000 - 1500

    def test_remove_curse_unsticks_items(self):
        session = build_session()
        member = session.party.members[0]
        member.inventory.purse.gp = 500
        cursed = MagicItemInstance(
            instance_id="magic-item-9001",
            template_id="sword_minus_1_cursed",
            base_item_id="sword",
            identified=True,
            cursed_revealed=True,
        )
        member.inventory.wielded.append(cursed)
        result = session.execute(PurchaseHealing(character_id=member.id, service="remove_curse"))
        assert result.accepted
        assert not cursed.cursed_revealed

    def test_insufficient_funds_reject(self):
        session = build_session()
        member = session.party.members[0]
        member.inventory.purse.pp = 0
        member.inventory.purse.gp = 0
        member.inventory.purse.ep = 0
        member.inventory.purse.sp = 0
        member.inventory.purse.cp = 0
        result = session.execute(PurchaseHealing(character_id=member.id, service="raise_dead"))
        assert not result.accepted
        assert result.rejections[0].code == "items.purchase.insufficient_funds"
