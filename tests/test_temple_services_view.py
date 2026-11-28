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
from osrlib.core.effects import Condition, EffectDefinition, ModifierSpec, grant_condition, kill
from osrlib.core.events import Visibility
from osrlib.core.items import CoinPurse, Coins, MagicItemInstance
from osrlib.core.spells import minimum_caster_level
from osrlib.core.validation import Rejection
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import AdvanceTime, EnterDungeon, GrantCoins, PurchaseHealing, SessionMode
from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
from osrlib.crawl.exploration import HEALING_SERVICES
from osrlib.crawl.session import DeathRecord, GameSession
from osrlib.crawl.views import PlayerView
from osrlib.data import load_classes, load_spells

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


def unrecorded_death(game: GameSession, member) -> None:
    """Kill the member the way a game that bypasses the session would, so the session has no death record."""
    kill(member)
    game.death_records.pop(member.id, None)


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
    "weakened_diseased_and_wounded": lambda game, member: (
        wound(member),
        grant_condition(member, Condition.WEAKENED, None),
        attach(game, EffectDefinition(kind="mummy_rot", permanent=True, condition=Condition.DISEASED), member.id),
    ),
    # Cure light wounds' first usage heals and leaves paralysis alone. `test_temple_spell_modes.py` covers the
    # `cure_paralysis` usage (#368).
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
    "dead_unrecorded": unrecorded_death,
}

HEALTHY = {
    "cure_light_wounds": "full_hit_points",
    "cure_serious_wounds": "full_hit_points",
    "cure_disease": "not_diseased",
    "neutralize_poison": "not_poisoned",
    "remove_curse": "not_cursed",
    "raise_dead": "not_dead",
}
"""The `reason` of each service's refusal for a living member at full hit points with nothing wrong."""
CURED = {"cure_light_wounds": None, "cure_serious_wounds": None}
DEAD = {**HEALTHY, "cure_light_wounds": "dead", "cure_serious_wounds": "dead"}

EXPECTED: dict[str, dict[str, str | None]] = {
    "full_hit_points": HEALTHY,
    "wounded": {**HEALTHY, **CURED},
    "diseased_and_wounded": {
        **HEALTHY,
        "cure_light_wounds": "diseased",
        "cure_serious_wounds": "diseased",
        "cure_disease": None,
    },
    "poisoned": {**HEALTHY, "neutralize_poison": None},
    "weakened_and_wounded": {**HEALTHY, "cure_light_wounds": "weakened", "cure_serious_wounds": "weakened"},
    "weakened_diseased_and_wounded": {
        **HEALTHY,
        "cure_light_wounds": "weakened",
        "cure_serious_wounds": "weakened",
        "cure_disease": None,
    },
    "paralysed": HEALTHY,
    "paralysed_and_wounded": {**HEALTHY, **CURED},
    "revealed_cursed_ring": {**HEALTHY, "remove_curse": None},
    "unrevealed_cursed_ring": HEALTHY,
    "cursed_slow_healing": {**HEALTHY, "remove_curse": None},
    "dead_today": {**DEAD, "raise_dead": None},
    "dead_of_poison_just_now": {**DEAD, "neutralize_poison": None, "raise_dead": None},
    "dead_three_days": {**DEAD, "raise_dead": None},
    "dead_beyond_any_cleric": {**DEAD, "raise_dead": "dead_too_long"},
    "dead_unrecorded": {**DEAD, "raise_dead": "no_death_record"},
}
"""For each state and service, the `reason` of the temple's refusal, or None when some temple cleric sells it."""

LEVEL_OVERRIDES = {("dead_three_days", "raise_dead"): 8}
"""The sold services whose lowest working level isn't the service's minimum: *raise dead* reaches 4 days per level
above 7th, so a member dead 3 days needs level 8."""


def levels(service: str) -> tuple[int, int]:
    """The lowest and highest temple cleric level for a service."""
    spell = load_spells().get(HEALING_SERVICES[service][0])
    return minimum_caster_level(spell), load_classes().get("cleric").max_level


def patient_in(state: str, gold: int = 5000) -> tuple[GameSession, object]:
    game = session(gold=gold)
    patient = game.party.members[1]
    STATES[state](game, patient)
    return game, patient


def party_gold(game: GameSession) -> int:
    return sum(member.inventory.purse.gp for member in game.party.members)


def reasons(rejections) -> list[tuple[str, object]]:
    return [(rejection.code, rejection.params.get("reason")) for rejection in rejections]


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


def test_every_state_has_an_expectation_for_every_service():
    assert set(EXPECTED) == set(STATES)
    assert all(set(by_service) == set(SERVICES) for by_service in EXPECTED.values())


@pytest.mark.parametrize("service", SERVICES)
@pytest.mark.parametrize("state", list(STATES))
def test_the_view_agrees_with_the_purchase_at_every_level(state, service):
    """A service with a level is sold at that level and refused at every lower one. A service without one is refused
    at every level, and at the default level with exactly the rejections the view shows."""
    game, patient = patient_in(state)
    view = game.view(Visibility.PLAYER)
    shown = view.town_healing_rejections[patient.id][service]
    lowest, highest = levels(service)
    gold = party_gold(game)
    expected = EXPECTED[state][service]

    if expected is None:
        assert shown == ()
        level = view.town_healing_caster_levels[patient.id][service]
        assert level == LEVEL_OVERRIDES.get((state, service), lowest)
        for lower in range(lowest, level):
            refused = game.execute(PurchaseHealing(character_id=patient.id, service=service, caster_level=lower))
            assert reasons(refused.rejections) == [(NO_EFFECT, "caster_level_too_low")]
        default = game.execute(PurchaseHealing(character_id=patient.id, service=service))
        assert default.accepted is (level == lowest)
        if not default.accepted:
            assert reasons(default.rejections) == [(NO_EFFECT, "caster_level_too_low")]
            assert game.execute(PurchaseHealing(character_id=patient.id, service=service, caster_level=level)).accepted
        assert party_gold(game) == gold - HEALING_SERVICES[service][1]
    else:
        assert service not in view.town_healing_caster_levels[patient.id]
        assert reasons(shown) == [(NO_EFFECT, expected)]
        assert shown[0].params == {"item": service, "character": patient.id, "reason": expected}
        assert tuple(game.execute(PurchaseHealing(character_id=patient.id, service=service)).rejections) == shown
        for level in range(lowest, highest + 1):
            refused = game.execute(PurchaseHealing(character_id=patient.id, service=service, caster_level=level))
            assert [rejection.code for rejection in refused.rejections] == [NO_EFFECT]
        assert party_gold(game) == gold


@pytest.mark.parametrize("state", ["dead_today", "dead_three_days", "dead_beyond_any_cleric", "wounded"])
def test_raise_dead_once_sold_at_a_level_is_sold_at_every_higher_level(state):
    """The view's search relies on this: whether the temple sells a service never gets worse with the level."""
    lowest, highest = levels("raise_dead")
    sold = []
    for level in range(lowest, highest + 1):
        game, patient = patient_in(state)
        sold.append(
            game.execute(PurchaseHealing(character_id=patient.id, service="raise_dead", caster_level=level)).accepted
        )
    assert sold == sorted(sold)


def test_the_view_covers_every_member_and_every_service():
    game, patient = patient_in("dead_three_days")
    view = game.view(Visibility.PLAYER)
    rejections = view.town_healing_rejections
    assert list(rejections) == [member.id for member in game.party.members]
    assert list(view.town_healing_caster_levels) == list(rejections)
    assert all(list(by_service) == list(SERVICES) for by_service in rejections.values())
    assert view.town_healing_caster_levels[patient.id] == {"raise_dead": 8}


def test_a_service_the_party_cant_afford_still_shows_as_sold():
    game, patient = patient_in("wounded", gold=0)
    view = game.view(Visibility.PLAYER)
    assert view.town_healing_funds_gp is not None
    assert view.town_healing_funds_gp < view.town_healing_prices["cure_serious_wounds"]
    assert view.town_healing_rejections[patient.id]["cure_serious_wounds"] == ()

    result = game.execute(PurchaseHealing(character_id=patient.id, service="cure_serious_wounds"))
    assert [rejection.code for rejection in result.rejections] == [INSUFFICIENT_FUNDS]


def test_a_useless_service_shows_its_reason_when_the_party_cant_pay_either():
    """The command reports the fee first, and the view leaves the fee out, so the two differ only by that check."""
    game, patient = patient_in("full_hit_points", gold=0)
    shown = game.view(Visibility.PLAYER).town_healing_rejections[patient.id]["raise_dead"]
    assert reasons(shown) == [(NO_EFFECT, "not_dead")]
    result = game.execute(PurchaseHealing(character_id=patient.id, service="raise_dead"))
    assert [rejection.code for rejection in result.rejections] == [INSUFFICIENT_FUNDS]


def test_building_the_view_draws_nothing_and_changes_nothing():
    game, _ = patient_in("dead_three_days")
    before = fingerprint(game)
    first = game.view(Visibility.PLAYER)
    assert fingerprint(game) == before
    assert game.view(Visibility.PLAYER) == first


# ---------------------------------------------------------------------- what the party can pay

PURSES = [
    ((CoinPurse(gp=12, sp=5), CoinPurse(gp=12, sp=5)), 24),
    ((CoinPurse(gp=12, sp=5), CoinPurse(gp=12, sp=5), CoinPurse(cp=100)), 25),
    ((CoinPurse(pp=19, ep=9, sp=4, cp=9),), 99),
    ((CoinPurse(pp=19, ep=9, sp=5),), 100),
    ((CoinPurse(sp=999), CoinPurse(cp=9999)), 198),
    ((CoinPurse(),), 0),
]
"""Purses for the first members in marching order, the rest empty, with what the party can pay in whole gold."""


@pytest.mark.parametrize(("purses", "funds"), PURSES)
@pytest.mark.parametrize("service", ["cure_light_wounds", "cure_serious_wounds"])
def test_the_funds_decide_the_funds_check(purses, funds, service):
    game = session(gold=0)
    for member in game.party.members:
        member.inventory.purse = CoinPurse()
    for member, purse in zip(game.party.members, purses, strict=False):
        member.inventory.purse = purse.model_copy()
    patient = game.party.members[-1]
    wound(patient)
    view = game.view(Visibility.PLAYER)
    assert view.town_healing_funds_gp == funds

    result = game.execute(PurchaseHealing(character_id=patient.id, service=service))
    price = view.town_healing_prices[service]
    assert (INSUFFICIENT_FUNDS in [rejection.code for rejection in result.rejections]) is (price > funds)
    assert result.accepted is (price <= funds)


def test_a_dead_members_purse_counts_and_the_patient_doesnt_change_the_total():
    game = session(gold=0)
    for member in game.party.members:
        member.inventory.purse = CoinPurse()
    rich = game.party.members[0]
    rich.inventory.purse = CoinPurse(gp=30, sp=9)
    die(game, rich)
    assert game.view(Visibility.PLAYER).town_healing_funds_gp == 30
    for patient in game.party.members[1:]:
        wound(patient)
    assert game.execute(PurchaseHealing(character_id=game.party.members[2].id, service="cure_light_wounds")).accepted
    assert game.view(Visibility.PLAYER).town_healing_funds_gp == 5


# ---------------------------------------------------------------------- where the map is empty


def test_the_map_is_empty_in_a_dungeon_where_the_temple_refuses_every_purchase():
    game, patient = patient_in("wounded")
    assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
    view = game.view(Visibility.PLAYER)
    assert (view.town_healing_rejections, view.town_healing_caster_levels) == ({}, {})
    assert view.town_healing_funds_gp == 5000, "the funds are stated in every mode, like the prices"
    result = game.execute(PurchaseHealing(character_id=patient.id, service="cure_light_wounds"))
    assert [rejection.code for rejection in result.rejections] == ["session.command.wrong_mode"]


@pytest.mark.parametrize("mode", [SessionMode.GAME_OVER, SessionMode.VICTORY])
def test_the_map_is_empty_once_play_has_ended(mode):
    game, _ = patient_in("dead_today")
    game.mode = mode
    view = game.view(Visibility.PLAYER)
    assert (view.town_healing_rejections, view.town_healing_caster_levels) == ({}, {})


def test_a_town_that_lists_no_temple_still_serves_the_party():
    """The temple serves the party in any town, so `town_services` doesn't empty the map."""
    game = session(services=())
    patient = game.party.members[1]
    wound(patient)
    assert game.view(Visibility.PLAYER).town_healing_rejections[patient.id]["cure_light_wounds"] == ()
    assert game.execute(PurchaseHealing(character_id=patient.id, service="cure_light_wounds")).accepted


# ---------------------------------------------------------------------- the services that would do nothing


@pytest.mark.parametrize(
    ("state", "service", "reason"),
    [
        ("full_hit_points", "cure_light_wounds", "full_hit_points"),
        ("full_hit_points", "cure_serious_wounds", "full_hit_points"),
        # Cure light wounds' first usage heals and leaves paralysis alone (#368).
        ("paralysed", "cure_light_wounds", "full_hit_points"),
        ("dead_today", "cure_light_wounds", "dead"),
        ("dead_today", "cure_serious_wounds", "dead"),
        ("diseased_and_wounded", "cure_light_wounds", "diseased"),
        ("weakened_and_wounded", "cure_serious_wounds", "weakened"),
        ("full_hit_points", "remove_curse", "not_cursed"),
        ("unrevealed_cursed_ring", "remove_curse", "not_cursed"),
        ("full_hit_points", "cure_disease", "not_diseased"),
        ("full_hit_points", "neutralize_poison", "not_poisoned"),
        ("full_hit_points", "raise_dead", "not_dead"),
        ("dead_three_days", "raise_dead", "caster_level_too_low"),
        ("dead_beyond_any_cleric", "raise_dead", "dead_too_long"),
        ("dead_unrecorded", "raise_dead", "no_death_record"),
    ],
)
def test_a_service_that_would_do_nothing_is_refused_and_costs_nothing(state, service, reason):
    game, patient = patient_in(state)
    before = fingerprint(game)
    result = game.execute(PurchaseHealing(character_id=patient.id, service=service))
    assert reasons(result.rejections) == [(NO_EFFECT, reason)]
    assert not result.events
    assert fingerprint(game) == before


def test_no_cleric_can_raise_a_member_dead_longer_than_level_14_reaches():
    game, patient = patient_in("dead_beyond_any_cleric")
    result = game.execute(PurchaseHealing(character_id=patient.id, service="raise_dead", caster_level=14))
    assert reasons(result.rejections) == [(NO_EFFECT, "dead_too_long")]


def test_remove_curse_frees_a_revealed_cursed_ring():
    game, patient = patient_in("revealed_cursed_ring")
    assert game.execute(PurchaseHealing(character_id=patient.id, service="remove_curse")).accepted
    assert not patient.inventory.rings[-1].cursed_revealed
    rejections = game.view(Visibility.PLAYER).town_healing_rejections[patient.id]["remove_curse"]
    assert reasons(rejections) == [(NO_EFFECT, "not_cursed")]


# ---------------------------------------------------------------------- the serialized view


def test_the_map_round_trips_through_json():
    game, _ = patient_in("dead_today")
    view = game.view(Visibility.PLAYER)
    document = json.loads(view.model_dump_json())
    assert PlayerView.model_validate(document) == view
    entry = document["town_healing_rejections"][game.party.members[0].id]["raise_dead"]
    first = game.party.members[0].id
    assert entry == [{"code": NO_EFFECT, "params": {"item": "raise_dead", "character": first, "reason": "not_dead"}}]
    assert document["town_healing_caster_levels"][game.party.members[1].id] == {"raise_dead": 7}
    assert document["town_healing_funds_gp"] == view.town_healing_funds_gp
    assert isinstance(view.town_healing_rejections[game.party.members[0].id]["raise_dead"][0], Rejection)


def test_a_player_view_from_osrlib_2_10_0_validates_with_the_new_fields_at_their_defaults():
    document = json.loads(VIEW_2_10_0.read_text(encoding="utf-8"))
    assert "town_healing_rejections" not in document
    assert document["mode"] == "town", "precondition: the stored view is of a party in town"
    assert "town_healing_caster_levels" not in document
    assert "town_healing_funds_gp" not in document
    view = PlayerView.model_validate(document)
    assert (view.town_healing_rejections, view.town_healing_caster_levels) == ({}, {})
    assert view.town_healing_funds_gp is None
    assert view.town_healing_prices == document["town_healing_prices"]
