"""The player view's supplies summary: light and food left, counted the way the session spends them."""

from copy import deepcopy

import pytest

from crawl_fixtures import build_adventure, build_party
from osrlib.core.clock import ROUNDS_PER_TURN, TimeUnit
from osrlib.core.effects import ActiveCondition, Condition, EffectDefinition
from osrlib.core.events import Visibility
from osrlib.crawl._light import _light_definition
from osrlib.crawl.commands import AdvanceTime, EnterDungeon, GrantItem, LightSource, SessionMode
from osrlib.crawl.exploration import consume_provisions
from osrlib.crawl.session import GameSession
from osrlib.crawl.views import PlayerView, SuppliesView
from osrlib.data import load_equipment

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

FIGHTER, THIEF, CLERIC, MAGIC_USER = "character-0001", "character-0002", "character-0003", "character-0004"
TORCH_ROUNDS = load_equipment().get("torch").params["burn_turns"] * ROUNDS_PER_TURN
FLASK_ROUNDS = load_equipment().get("lantern").params["burn_turns_per_flask"] * ROUNDS_PER_TURN


def new_session() -> GameSession:
    return GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=5)


def grant(session: GameSession, character_id: str, item_id: str, quantity: int = 1) -> None:
    assert session.execute(GrantItem(character_id=character_id, item_id=item_id, quantity=quantity)).accepted


def enter(session: GameSession) -> None:
    assert session.execute(EnterDungeon(dungeon_id="delve")).accepted


def supplies(session: GameSession) -> SuppliesView:
    view = session.view(Visibility.PLAYER)
    assert isinstance(view, PlayerView)
    assert view.supplies is not None
    return view.supplies


def light_until_lit(session: GameSession, character_id: str, item_id: str = "torch") -> None:
    for _ in range(30):
        result = session.execute(LightSource(character_id=character_id, item_id=item_id))
        assert result.accepted, result.rejections
        if any(event.code == "exploration.light.lit" for event in result.events):
            return
    raise AssertionError("the light never took")


def kill(session: GameSession, character_id: str) -> None:
    member = session.member(character_id)
    member.conditions = (*member.conditions, ActiveCondition(condition=Condition.DEAD))


def any_light_source_accepted(session: GameSession) -> bool:
    """Whether the session accepts some LightSource for a carried torch or lantern, tried on copies."""
    for member in session.party.members:
        for item_id in ("torch", "lantern"):
            if deepcopy(session).execute(LightSource(character_id=member.id, item_id=item_id)).accepted:
                return True
    return False


def assert_usable_agrees(session: GameSession) -> None:
    assert supplies(session).light_reserve_usable is any_light_source_accepted(session)


class TestLight:
    def test_two_torches_report_the_longer_one_not_the_sum(self):
        session = new_session()
        grant(session, FIGHTER, "torch", 2)
        grant(session, FIGHTER, "tinder_box")
        grant(session, THIEF, "torch")
        enter(session)
        light_until_lit(session, FIGHTER)
        assert session.execute(AdvanceTime(n=2, unit=TimeUnit.TURN)).accepted
        light_until_lit(session, THIEF)  # the fighter's flame lights it without a tinder box
        effects = session.view(Visibility.PLAYER).effects
        remaining = sorted(effect.remaining_rounds for effect in effects if effect.kind == "light")
        assert len(remaining) == 2 and remaining[0] < remaining[1]
        summary = supplies(session)
        assert summary.light_burning_rounds == remaining[1] == TORCH_ROUNDS - 1  # lighting takes a round
        assert not summary.light_permanent
        assert summary.torches == 1  # a lit torch is used up, so only the fighter's spare counts
        assert summary.light_reserve_rounds == TORCH_ROUNDS

    def test_continual_light_is_permanent_and_adds_no_burning_rounds(self):
        session = new_session()
        enter(session)
        definition = EffectDefinition(kind="continual_light", params={"brightness": "daylight", "radius_feet": 30})
        session.ledger.attach(
            definition, CLERIC, clock=session.clock, allocator=session.allocator, registry=session.registry()
        )
        summary = supplies(session)
        assert summary.light_permanent
        assert summary.light_burning_rounds == 0

    def test_a_light_on_a_dead_member_does_not_count(self):
        session = new_session()
        enter(session)
        definition = _light_definition("torch", load_equipment().get("torch").params)
        session.ledger.attach(
            definition, THIEF, clock=session.clock, allocator=session.allocator, registry=session.registry()
        )
        assert supplies(session).light_burning_rounds == TORCH_ROUNDS
        kill(session, THIEF)
        assert supplies(session).light_burning_rounds == 0

    def test_oil_without_a_lantern_adds_nothing_to_the_reserve(self):
        session = new_session()
        grant(session, FIGHTER, "oil_flask", 3)
        grant(session, FIGHTER, "tinder_box")
        enter(session)
        summary = supplies(session)
        assert (summary.oil_flasks, summary.lanterns, summary.light_reserve_rounds) == (3, 0, 0)
        assert not summary.light_reserve_usable
        assert_usable_agrees(session)

    def test_oil_with_a_lantern_on_another_member_counts_but_is_not_usable(self):
        session = new_session()
        grant(session, FIGHTER, "oil_flask", 2)
        grant(session, FIGHTER, "tinder_box")
        grant(session, THIEF, "lantern")
        grant(session, THIEF, "tinder_box")
        enter(session)
        summary = supplies(session)
        assert summary.light_reserve_rounds == 2 * FLASK_ROUNDS
        assert not summary.light_reserve_usable
        assert_usable_agrees(session)

    def test_a_lantern_and_oil_on_one_member_are_usable(self):
        session = new_session()
        grant(session, THIEF, "oil_flask", 2)
        grant(session, THIEF, "lantern")
        grant(session, THIEF, "tinder_box")
        enter(session)
        summary = supplies(session)
        assert summary.light_reserve_usable
        assert_usable_agrees(session)
        light_until_lit(session, THIEF, "lantern")
        summary = supplies(session)
        assert summary.oil_flasks == 1  # the burning flask is used up
        assert summary.lanterns == 1  # the lit lantern is still carried
        assert summary.light_burning_rounds == FLASK_ROUNDS - 1  # lighting takes a round
        assert summary.light_reserve_rounds == FLASK_ROUNDS

    def test_no_tinder_box_makes_a_nonzero_reserve_unusable(self):
        session = new_session()
        grant(session, FIGHTER, "torch", 4)
        enter(session)
        summary = supplies(session)
        assert summary.light_reserve_rounds == 4 * TORCH_ROUNDS
        assert not summary.light_reserve_usable
        assert_usable_agrees(session)

    def test_a_burning_flame_stands_in_for_the_tinder_box(self):
        session = new_session()
        grant(session, FIGHTER, "torch")
        grant(session, FIGHTER, "tinder_box")
        grant(session, THIEF, "torch")
        enter(session)
        light_until_lit(session, FIGHTER)
        summary = supplies(session)
        assert summary.light_reserve_usable  # the thief has no tinder box, but the fighter's torch burns
        assert_usable_agrees(session)

    def test_an_incapacitated_carrier_cannot_light(self):
        session = new_session()
        grant(session, FIGHTER, "torch")
        grant(session, FIGHTER, "tinder_box")
        enter(session)
        member = session.member(FIGHTER)
        member.conditions = (*member.conditions, ActiveCondition(condition=Condition.ASLEEP))
        summary = supplies(session)
        assert summary.torches == 1  # a sleeping member is living, so the torch counts
        assert not summary.light_reserve_usable
        assert_usable_agrees(session)

    def test_usable_is_false_in_a_mode_that_refuses_light_source(self):
        session = new_session()
        grant(session, FIGHTER, "torch")
        grant(session, FIGHTER, "tinder_box")
        enter(session)
        assert supplies(session).light_reserve_usable
        session.mode = SessionMode.ENCOUNTER
        assert not supplies(session).light_reserve_usable
        assert_usable_agrees(session)

    def test_dead_members_items_do_not_count(self):
        session = new_session()
        grant(session, FIGHTER, "torch", 2)
        grant(session, FIGHTER, "tinder_box")
        grant(session, FIGHTER, "lantern")
        grant(session, THIEF, "oil_flask", 2)
        grant(session, FIGHTER, "rations_standard", 5)
        enter(session)
        kill(session, FIGHTER)
        summary = supplies(session)
        assert (summary.torches, summary.lanterns, summary.oil_flasks) == (0, 0, 2)
        assert summary.light_reserve_rounds == 0  # no living member carries a lantern
        assert summary.rations == 0
        assert FIGHTER not in summary.food_short_first_ids
        assert_usable_agrees(session)

    def test_town_reports_the_same_light_numbers(self):
        session = new_session()
        grant(session, FIGHTER, "torch", 3)
        grant(session, FIGHTER, "tinder_box")
        grant(session, THIEF, "lantern")
        grant(session, THIEF, "oil_flask", 2)
        grant(session, CLERIC, "rations_iron", 7)
        in_town = supplies(session)
        assert session.mode is SessionMode.TOWN
        assert in_town.light_reserve_usable
        assert_usable_agrees(session)
        enter(session)
        assert supplies(session) == in_town


class TestFood:
    def test_a_member_without_rations_goes_short_first(self):
        session = new_session()
        for member_id in (FIGHTER, THIEF, CLERIC):
            grant(session, member_id, "rations_iron", 7)
        grant(session, THIEF, "rations_standard", 7)
        summary = supplies(session)
        assert summary.food_days == 0
        assert summary.food_days_shared == 28 // 4
        assert summary.food_short_first_ids == (MAGIC_USER,)

    def test_iron_and_standard_rations_count_together(self):
        session = new_session()
        for member_id in (FIGHTER, THIEF, CLERIC, MAGIC_USER):
            grant(session, member_id, "rations_standard", 2)
            grant(session, member_id, "rations_iron", 3)
        grant(session, CLERIC, "rations_iron", 1)
        summary = supplies(session)
        assert (summary.rations_standard, summary.rations_iron, summary.rations) == (8, 13, 21)
        assert summary.food_days == 5
        assert summary.food_days_shared == 5
        assert summary.food_short_first_ids == (FIGHTER, THIEF, MAGIC_USER)

    def test_dead_members_do_not_eat_or_count(self):
        session = new_session()
        grant(session, FIGHTER, "rations_iron", 6)
        grant(session, THIEF, "rations_iron", 6)
        grant(session, CLERIC, "rations_iron", 6)
        kill(session, MAGIC_USER)
        summary = supplies(session)
        assert summary.food_days == 6
        assert summary.food_days_shared == 6
        assert summary.food_short_first_ids == (FIGHTER, THIEF, CLERIC)

    def test_a_party_with_no_living_members_reports_zeros(self):
        session = new_session()
        grant(session, FIGHTER, "rations_iron", 6)
        grant(session, FIGHTER, "torch", 6)
        grant(session, FIGHTER, "tinder_box")
        for member_id in (FIGHTER, THIEF, CLERIC, MAGIC_USER):
            kill(session, member_id)
        summary = supplies(session)
        assert summary.food_days == summary.food_days_shared == summary.rations == 0
        assert summary.food_short_first_ids == ()
        assert summary.light_reserve_rounds == summary.torches == 0
        assert not summary.light_reserve_usable

    def test_town_reports_the_same_food_numbers(self):
        session = new_session()
        grant(session, FIGHTER, "rations_iron", 3)
        grant(session, THIEF, "rations_standard", 1)
        in_town = supplies(session)
        enter(session)
        assert supplies(session) == in_town

    def test_food_numbers_match_what_consume_provisions_does(self):
        session = new_session()
        grant(session, FIGHTER, "rations_standard", 2)
        grant(session, FIGHTER, "rations_iron", 3)
        grant(session, THIEF, "rations_iron", 3)
        grant(session, CLERIC, "rations_standard", 3)
        grant(session, MAGIC_USER, "rations_iron", 7)
        enter(session)
        summary = supplies(session)
        assert summary.food_days == 3
        assert summary.food_short_first_ids == (THIEF, CLERIC)
        for day in range(1, summary.food_days + 1):
            events = consume_provisions(session)
            short = [event.character_id for event in events if event.kind == "food" and "short" in event.code]
            assert short == [], f"someone went short on day {day}"
            assert supplies(session).food_days == summary.food_days - day
        events = consume_provisions(session)
        short = tuple(event.character_id for event in events if event.kind == "food" and "short" in event.code)
        assert short == summary.food_short_first_ids


class TestSerialization:
    def test_the_summary_round_trips(self):
        session = new_session()
        grant(session, FIGHTER, "torch", 2)
        grant(session, FIGHTER, "tinder_box")
        view = session.view(Visibility.PLAYER)
        assert isinstance(view, PlayerView)
        restored = PlayerView.model_validate_json(view.model_dump_json())
        assert restored.supplies == view.supplies

    def test_a_view_document_without_supplies_parses(self):
        session = new_session()
        document = session.view(Visibility.PLAYER).model_dump(mode="json")
        del document["supplies"]
        assert PlayerView.model_validate(document).supplies is None
