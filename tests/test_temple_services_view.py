"""Which temple services apply to each party member, in the player view (issue #363).

`PlayerView.town_healing_rejections` gives, for each member and service, the rejections a `PurchaseHealing` would get
after its funds check. The command and the view call the same check, so the tests here compare the view's answer with
what the real command returns, for every service and a spread of member states. They also pin the services the temple
now refuses because they would do nothing: *cure light wounds* and *cure serious wounds* on a member who is dead, at
full hit points, or can't heal by magic, and *remove curse* on a member with no curse to lift.
"""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from crawl_fixtures import build_party
from osrlib.core.clock import TimeUnit
from osrlib.core.effects import Condition, EffectDefinition, ModifierSpec, grant_condition, has_condition, kill
from osrlib.core.events import Visibility
from osrlib.core.items import Coins, MagicItemInstance
from osrlib.core.validation import Rejection
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import AdvanceTime, EnterDungeon, GrantCoins, PurchaseHealing, SessionMode
from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
from osrlib.crawl.exploration import HEALING_SERVICES
from osrlib.crawl.session import DeathRecord, GameSession
from osrlib.crawl.views import PlayerView

NO_EFFECT = "town.healing.no_effect"
INSUFFICIENT_FUNDS = "items.purchase.insufficient_funds"
SERVICES = tuple(HEALING_SERVICES)

VIEW_2_10_0 = Path(__file__).parent / "saves" / "view_temple_services_player_view_2.10.0.json"
"""A `PlayerView` of a two-member party in town, dumped as JSON by osrlib 2.10.0, as a client might store a feed
entry. osrlib 2.10.0 had no `town_healing_rejections`."""


def session(gold: int = 5000, services: tuple[str, ...] = ("temple",)) -> GameSession:
    """The stock party in town, with `gold` in the first member's purse."""
    level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
    crypt = DungeonSpec(id="crypt", name="Crypt", levels=(level,))
    town = TownSpec(name="Threshold", services=services, travel_turns={"crypt": 1})
    game = GameSession.new(build_party(), Adventure(name="A", town=town, dungeons=(crypt,)), seed=7)
    if gold:
        assert game.execute(GrantCoins(character_id=game.party.members[0].id, coins=Coins(gp=gold))).accepted
    return game


def attach(game: GameSession, definition: EffectDefinition, target_id: str) -> None:
    effect, _ = game.ledger.attach(
        definition, target_id, clock=game.clock, allocator=game.allocator, registry=game.registry()
    )
    assert effect is not None


def die(game: GameSession, member, *, cause: str = "damage", days_ago: int = 0) -> None:
    """Kill the member, record the death, and let `days_ago` days pass."""
    kill(member)
    game.death_records[member.id] = DeathRecord(round=game.clock.rounds, cause=cause)
    while days_ago:
        days = min(days_ago, 28)
        assert game.execute(AdvanceTime(n=days, unit=TimeUnit.DAY)).accepted
        days_ago -= days


def wound(member) -> None:
    member.current_hp = 1


def cursed_ring(member, *, revealed: bool) -> None:
    ring = MagicItemInstance(
        instance_id="magic-item-9001", template_id="ring_of_weakness", identified=True, cursed_revealed=revealed
    )
    member.inventory.rings.append(ring)


# Each state is set up on the second member, the patient. The stock party's members start at full hit points.
STATES: dict[str, Callable[[GameSession, object], None]] = {
    "full_hit_points": lambda game, member: None,
    "wounded": lambda game, member: wound(member),
    "diseased_and_wounded": lambda game, member: (
        wound(member),
        attach(game, EffectDefinition(kind="mummy_rot", permanent=True, condition=Condition.DISEASED), member.id),
    ),
    "poisoned": lambda game, member: attach(
        game, EffectDefinition(kind="poison", permanent=True, condition=Condition.POISONED), member.id
    ),
    "weakened_and_wounded": lambda game, member: (wound(member), grant_condition(member, Condition.WEAKENED, None)),
    "paralysed": lambda game, member: grant_condition(member, Condition.PARALYSED, None),
    "paralysed_and_wounded": lambda game, member: (wound(member), grant_condition(member, Condition.PARALYSED, None)),
    "revealed_cursed_ring": lambda game, member: cursed_ring(member, revealed=True),
    "unrevealed_cursed_ring": lambda game, member: cursed_ring(member, revealed=False),
    "cursed_slow_healing": lambda game, member: attach(
        game,
        EffectDefinition(
            kind="cursed_slow_healing",
            modifiers=(ModifierSpec(kind="magical_healing_half", value=1, from_item=True),),
            params={"healing_rest_days": 2, "item_source": "cursed_scroll"},
        ),
        member.id,
    ),
    "dead_today": lambda game, member: die(game, member),
    "dead_of_poison_just_now": lambda game, member: die(game, member, cause="poison"),
    "dead_three_days": lambda game, member: die(game, member, days_ago=3),
    "dead_beyond_any_cleric": lambda game, member: die(game, member, days_ago=29),
}

# The services the temple sells in each state. Every other pairing would do nothing and is refused.
SOLD: dict[str, set[str]] = {
    "full_hit_points": set(),
    "wounded": {"cure_light_wounds", "cure_serious_wounds"},
    "diseased_and_wounded": {"cure_disease"},
    "poisoned": {"neutralize_poison"},
    "weakened_and_wounded": set(),
    "paralysed": set(),
    "paralysed_and_wounded": {"cure_light_wounds", "cure_serious_wounds"},
    "revealed_cursed_ring": {"remove_curse"},
    "unrevealed_cursed_ring": set(),
    "cursed_slow_healing": {"remove_curse"},
    "dead_today": {"raise_dead"},
    "dead_of_poison_just_now": {"neutralize_poison", "raise_dead"},
    "dead_three_days": set(),
    "dead_beyond_any_cleric": set(),
}


def patient_in(state: str, gold: int = 5000) -> tuple[GameSession, object]:
    game = session(gold=gold)
    patient = game.party.members[1]
    STATES[state](game, patient)
    return game, patient


def party_gold(game: GameSession) -> int:
    return sum(member.inventory.purse.gp for member in game.party.members)


def fingerprint(game: GameSession) -> dict:
    """Everything a refused purchase or a view build must leave alone."""
    return {
        "members": [member.model_dump(mode="json") for member in game.party.members],
        "ledger": [effect.model_dump(mode="json") for effect in game.ledger.effects],
        "streams": {key: state.model_dump(mode="json") for key, state in game.streams.export_states().items()},
        "rounds": game.clock.rounds,
        "mode": game.mode,
        "log": len(game.command_log),
    }


# ---------------------------------------------------------------------- the view agrees with the command


@pytest.mark.parametrize("service", SERVICES)
@pytest.mark.parametrize("state", list(STATES))
def test_the_view_gives_the_rejections_the_purchase_gets(state, service):
    game, patient = patient_in(state)
    shown = game.view(Visibility.PLAYER).town_healing_rejections[patient.id][service]
    gold = party_gold(game)

    result = game.execute(PurchaseHealing(character_id=patient.id, service=service))

    assert shown == tuple(result.rejections)
    assert result.accepted is (service in SOLD[state])
    if result.accepted:
        assert party_gold(game) == gold - HEALING_SERVICES[service][1]
    else:
        assert [rejection.code for rejection in result.rejections] == [NO_EFFECT]
        assert result.rejections[0].params == {"item": service, "character": patient.id}
        assert party_gold(game) == gold


def test_the_view_covers_every_member_and_every_service():
    game, patient = patient_in("dead_today")
    rejections = game.view(Visibility.PLAYER).town_healing_rejections
    assert list(rejections) == [member.id for member in game.party.members]
    assert all(list(by_service) == list(SERVICES) for by_service in rejections.values())
    assert rejections[patient.id]["raise_dead"] == ()


def test_the_view_answers_for_the_lowest_level_cleric():
    """A member dead three days shows *raise dead* refused, and a level 9 cleric can still raise them."""
    game, patient = patient_in("dead_three_days")
    assert [r.code for r in game.view(Visibility.PLAYER).town_healing_rejections[patient.id]["raise_dead"]] == [
        NO_EFFECT
    ]
    assert game.execute(PurchaseHealing(character_id=patient.id, service="raise_dead", caster_level=9)).accepted
    assert not has_condition(patient, Condition.DEAD)


def test_a_service_the_party_cant_afford_still_shows_as_sold():
    game, patient = patient_in("wounded", gold=0)
    assert party_gold(game) < HEALING_SERVICES["cure_serious_wounds"][1]
    view = game.view(Visibility.PLAYER)
    assert view.town_healing_rejections[patient.id]["cure_serious_wounds"] == ()

    result = game.execute(PurchaseHealing(character_id=patient.id, service="cure_serious_wounds"))
    assert [rejection.code for rejection in result.rejections] == [INSUFFICIENT_FUNDS]


def test_a_useless_service_shows_its_reason_when_the_party_cant_pay_either():
    """The command reports the fee first, and the view leaves the fee out, so the two differ only by that check."""
    game, patient = patient_in("full_hit_points", gold=0)
    shown = game.view(Visibility.PLAYER).town_healing_rejections[patient.id]["raise_dead"]
    assert [rejection.code for rejection in shown] == [NO_EFFECT]
    result = game.execute(PurchaseHealing(character_id=patient.id, service="raise_dead"))
    assert [rejection.code for rejection in result.rejections] == [INSUFFICIENT_FUNDS]


def test_building_the_view_draws_nothing_and_changes_nothing():
    game, _ = patient_in("wounded")
    before = fingerprint(game)
    first = game.view(Visibility.PLAYER)
    assert fingerprint(game) == before
    assert game.view(Visibility.PLAYER) == first


# ---------------------------------------------------------------------- where the map is empty


def test_the_map_is_empty_in_a_dungeon_where_the_temple_refuses_every_purchase():
    game, patient = patient_in("wounded")
    assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
    assert game.view(Visibility.PLAYER).town_healing_rejections == {}
    result = game.execute(PurchaseHealing(character_id=patient.id, service="cure_light_wounds"))
    assert [rejection.code for rejection in result.rejections] == ["session.command.wrong_mode"]


@pytest.mark.parametrize("mode", [SessionMode.GAME_OVER, SessionMode.VICTORY])
def test_the_map_is_empty_once_play_has_ended(mode):
    game, _ = patient_in("dead_today")
    game.mode = mode
    assert game.view(Visibility.PLAYER).town_healing_rejections == {}


def test_a_town_that_lists_no_temple_still_serves_the_party():
    """The temple serves the party in any town, so `town_services` doesn't empty the map."""
    game = session(services=())
    patient = game.party.members[1]
    wound(patient)
    assert game.view(Visibility.PLAYER).town_healing_rejections[patient.id]["cure_light_wounds"] == ()
    assert game.execute(PurchaseHealing(character_id=patient.id, service="cure_light_wounds")).accepted


# ---------------------------------------------------------------------- the services that would do nothing


@pytest.mark.parametrize(
    ("state", "service"),
    [
        ("full_hit_points", "cure_light_wounds"),
        ("full_hit_points", "cure_serious_wounds"),
        ("paralysed", "cure_light_wounds"),
        ("dead_today", "cure_light_wounds"),
        ("dead_today", "cure_serious_wounds"),
        ("diseased_and_wounded", "cure_light_wounds"),
        ("weakened_and_wounded", "cure_serious_wounds"),
        ("full_hit_points", "remove_curse"),
        ("unrevealed_cursed_ring", "remove_curse"),
        ("full_hit_points", "cure_disease"),
        ("full_hit_points", "neutralize_poison"),
        ("full_hit_points", "raise_dead"),
        ("dead_beyond_any_cleric", "raise_dead"),
    ],
)
def test_a_service_that_would_do_nothing_is_refused_and_costs_nothing(state, service):
    game, patient = patient_in(state)
    before = fingerprint(game)
    result = game.execute(PurchaseHealing(character_id=patient.id, service=service))
    assert [rejection.code for rejection in result.rejections] == [NO_EFFECT]
    assert not result.events
    assert fingerprint(game) == before


def test_no_cleric_can_raise_a_member_dead_longer_than_level_14_reaches():
    game, patient = patient_in("dead_beyond_any_cleric")
    result = game.execute(PurchaseHealing(character_id=patient.id, service="raise_dead", caster_level=14))
    assert [rejection.code for rejection in result.rejections] == [NO_EFFECT]


def test_remove_curse_frees_a_revealed_cursed_ring():
    game, patient = patient_in("revealed_cursed_ring")
    assert game.execute(PurchaseHealing(character_id=patient.id, service="remove_curse")).accepted
    assert not patient.inventory.rings[-1].cursed_revealed
    rejections = game.view(Visibility.PLAYER).town_healing_rejections[patient.id]["remove_curse"]
    assert [rejection.code for rejection in rejections] == [NO_EFFECT]


# ---------------------------------------------------------------------- the serialized view


def test_the_map_round_trips_through_json():
    game, _ = patient_in("dead_today")
    view = game.view(Visibility.PLAYER)
    document = json.loads(view.model_dump_json())
    assert PlayerView.model_validate(document) == view
    entry = document["town_healing_rejections"][game.party.members[0].id]["raise_dead"]
    assert entry == [{"code": NO_EFFECT, "params": {"item": "raise_dead", "character": game.party.members[0].id}}]
    assert isinstance(view.town_healing_rejections[game.party.members[0].id]["raise_dead"][0], Rejection)


def test_a_player_view_from_osrlib_2_10_0_validates_with_the_map_empty():
    document = json.loads(VIEW_2_10_0.read_text(encoding="utf-8"))
    assert "town_healing_rejections" not in document
    assert document["mode"] == "town", "precondition: the stored view is of a party in town"
    view = PlayerView.model_validate(document)
    assert view.town_healing_rejections == {}
    assert view.town_healing_prices == document["town_healing_prices"]
