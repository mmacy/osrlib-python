"""The temple: prices a town sets, and a cleric of the level a game chooses (issues #162 and #226).

The standard prices are in `HEALING_SERVICES`, which can't be changed. A town states its own price
for a service in `TownSpec.healing_prices`, so the price is part of the adventure and a replay charges
the same. `PurchaseHealing.caster_level` picks the level of the temple cleric, which decides how long
dead a character *raise dead* can bring back. The temple never takes payment for a service that would
do nothing.
"""

import pytest
from pydantic import ValidationError

from crawl_fixtures import build_party
from osrlib.core.clock import TimeUnit
from osrlib.core.effects import Condition, has_condition, kill
from osrlib.core.events import Visibility
from osrlib.core.items import Coins
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import AdvanceTime, GrantCoins, PurchaseHealing, parse_command
from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
from osrlib.crawl.events import HealingPurchasedEvent
from osrlib.crawl.exploration import HEALING_SERVICES
from osrlib.crawl.session import DeathRecord, GameSession
from osrlib.persistence import load_game, save_game

STANDARD = {
    "cure_light_wounds": 25,
    "cure_serious_wounds": 100,
    "cure_disease": 150,
    "neutralize_poison": 150,
    "remove_curse": 200,
    "raise_dead": 1500,
}
NO_EFFECT = "town.healing.no_effect"
LEVEL_OUT_OF_RANGE = "town.healing.caster_level_out_of_range"


def session(gold: int = 5000, **town) -> GameSession:
    """The stock party in a town with a temple, with `gold` in the first member's purse."""
    level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
    crypt = DungeonSpec(id="crypt", name="Crypt", levels=(level,))
    spec = TownSpec(name="Threshold", services=("temple",), travel_turns={"crypt": 1}, **town)
    game = GameSession.new(build_party(), Adventure(name="A", town=spec, dungeons=(crypt,)), seed=7)
    if gold:
        payer = game.party.members[0]
        assert game.execute(GrantCoins(character_id=payer.id, coins=Coins(gp=gold))).accepted
    return game


def party_gold(game: GameSession) -> int:
    return sum(member.inventory.purse.gp for member in game.party.members)


def slay(game: GameSession, days_ago: int = 0):
    """Kill the second member, record the death, and let `days_ago` days pass."""
    victim = game.party.members[1]
    kill(victim)
    game.death_records[victim.id] = DeathRecord(round=game.clock.rounds, cause="damage")
    if days_ago:
        assert game.execute(AdvanceTime(n=days_ago, unit=TimeUnit.DAY)).accepted
    return victim


def purchased(result) -> HealingPurchasedEvent:
    return next(event for event in result.events if isinstance(event, HealingPurchasedEvent))


# ---------------------------------------------------------------------- the cleric's level (#226)


def test_the_default_cleric_raises_a_member_who_died_today():
    game = session()
    victim = slay(game)
    result = game.execute(PurchaseHealing(character_id=victim.id, service="raise_dead"))
    assert result.accepted
    assert not has_condition(victim, Condition.DEAD)


def test_the_default_cleric_cant_raise_a_member_dead_for_two_days_and_takes_no_payment():
    game = session()
    victim = slay(game, days_ago=2)
    gold, logged = party_gold(game), len(game.command_log)
    result = game.execute(PurchaseHealing(character_id=victim.id, service="raise_dead"))
    assert [rejection.code for rejection in result.rejections] == [NO_EFFECT]
    assert has_condition(victim, Condition.DEAD)
    assert (party_gold(game), len(game.command_log)) == (gold, logged)


@pytest.mark.parametrize(
    ("days_ago", "caster_level", "raised"),
    [(2, 9, True), (8, 9, True), (9, 9, False), (9, 10, True), (28, 14, True), (29, 14, False)],
)
def test_a_higher_cleric_reaches_further_back(days_ago, caster_level, raised):
    """*Raise dead* reaches back four days for each of the caster's levels above 7."""
    game = session()
    victim = slay(game, days_ago=days_ago)
    gold = party_gold(game)
    result = game.execute(PurchaseHealing(character_id=victim.id, service="raise_dead", caster_level=caster_level))
    assert result.accepted is raised
    assert has_condition(victim, Condition.DEAD) is not raised
    assert party_gold(game) == (gold - 1500 if raised else gold)
    if not raised:
        assert [rejection.code for rejection in result.rejections] == [NO_EFFECT]


@pytest.mark.parametrize(
    ("service", "caster_level", "minimum"),
    [("raise_dead", 6, 7), ("raise_dead", 15, 7), ("cure_light_wounds", 1, 2), ("cure_light_wounds", 0, 2)],
)
def test_a_level_no_temple_cleric_can_have_is_refused(service, caster_level, minimum):
    game = session()
    victim = slay(game) if service == "raise_dead" else game.party.members[1]
    gold = party_gold(game)
    result = game.execute(PurchaseHealing(character_id=victim.id, service=service, caster_level=caster_level))
    assert [rejection.code for rejection in result.rejections] == [LEVEL_OUT_OF_RANGE]
    assert result.rejections[0].params["minimum"] == minimum
    assert result.rejections[0].params["maximum"] == 14
    assert party_gold(game) == gold


def test_a_higher_cleric_casts_the_other_services_too():
    game = session()
    patient = game.party.members[1]
    patient.current_hp = 1
    result = game.execute(PurchaseHealing(character_id=patient.id, service="cure_light_wounds", caster_level=14))
    assert result.accepted
    assert patient.current_hp > 1


def test_the_level_survives_parsing_and_a_replayed_log():
    command = parse_command(
        {
            "command_type": "purchase_healing",
            "character_id": "character-0002",
            "service": "raise_dead",
            "caster_level": 9,
        }
    )
    assert isinstance(command, PurchaseHealing)
    assert command.caster_level == 9
    assert PurchaseHealing(character_id="character-0002", service="raise_dead").caster_level is None


# ---------------------------------------------------------------------- a town's prices (#162, item 10)


def test_the_standard_price_table_cant_be_changed():
    with pytest.raises(TypeError):
        HEALING_SERVICES["raise_dead"] = ("raise_dead", 1)  # type: ignore[index]
    with pytest.raises(TypeError):
        del HEALING_SERVICES["raise_dead"]  # type: ignore[attr-defined]
    assert {service: price for service, (_, price) in HEALING_SERVICES.items()} == STANDARD


def test_a_town_charges_its_own_price_for_a_service_it_lists():
    game = session(healing_prices={"raise_dead": 1000})
    victim = slay(game)
    gold = party_gold(game)
    result = game.execute(PurchaseHealing(character_id=victim.id, service="raise_dead"))
    assert result.accepted
    assert purchased(result).cost_gp == 1000
    assert party_gold(game) == gold - 1000


def test_a_town_charges_the_standard_price_for_a_service_it_doesnt_list():
    game = session(healing_prices={"raise_dead": 1000})
    patient = game.party.members[1]
    patient.current_hp = 1
    result = game.execute(PurchaseHealing(character_id=patient.id, service="cure_light_wounds"))
    assert purchased(result).cost_gp == 25


def test_a_town_can_heal_for_nothing():
    game = session(gold=0, healing_prices={"cure_light_wounds": 0})
    patient = game.party.members[1]
    patient.current_hp = 1
    result = game.execute(PurchaseHealing(character_id=patient.id, service="cure_light_wounds"))
    assert result.accepted
    assert purchased(result).cost_gp == 0
    assert patient.current_hp > 1


def test_the_partys_funds_are_judged_against_the_towns_price():
    cheap = session(gold=1200, healing_prices={"raise_dead": 1000})
    assert cheap.execute(PurchaseHealing(character_id=slay(cheap).id, service="raise_dead")).accepted
    standard = session(gold=1200)
    result = standard.execute(PurchaseHealing(character_id=slay(standard).id, service="raise_dead"))
    assert [rejection.code for rejection in result.rejections] == ["items.purchase.insufficient_funds"]
    assert result.rejections[0].params["cost_gp"] == 1500


@pytest.mark.parametrize("prices", [{"raise_dead": -1}, {"resurrection": 5000}])
def test_a_town_refuses_a_price_list_it_cant_charge(prices):
    with pytest.raises(ValidationError):
        TownSpec(name="Threshold", healing_prices=prices)


def test_the_towns_prices_are_part_of_the_save():
    game = session(healing_prices={"raise_dead": 1000})
    loaded = load_game(save_game(game))
    assert loaded.adventure.town.healing_prices == {"raise_dead": 1000}
    victim = slay(loaded)
    assert purchased(loaded.execute(PurchaseHealing(character_id=victim.id, service="raise_dead"))).cost_gp == 1000


# ---------------------------------------------------------------------- the player view


def test_the_player_view_lists_what_this_town_charges():
    assert session().view(Visibility.PLAYER).town_healing_prices == STANDARD
    view = session(healing_prices={"raise_dead": 1000, "cure_light_wounds": 0}).view(Visibility.PLAYER)
    assert view.town_healing_prices == {**STANDARD, "raise_dead": 1000, "cure_light_wounds": 0}
