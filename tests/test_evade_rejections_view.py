"""Which evasions the session would accept, in the player view (issue #370).

`EncounterView.evade_rejections` gives, for each value of `Evade.drop`, the rejections an `Evade` with that drop would
get now. The command's handler and the view call the same check, so the tests here compare the view's answer with what
the real command returns, for every drop and a spread of party states. They also pin what the map holds in the modes
that refuse every `Evade`, and that an `EncounterView` stored by osrlib 2.10.0 still validates.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import get_args

import pytest

from crawl_fixtures import build_adventure, build_party
from osrlib.core.effects import Condition, grant_condition, kill
from osrlib.core.events import Visibility
from osrlib.core.items import Coins
from osrlib.core.tables import ReactionResult
from osrlib.core.validation import Rejection
from osrlib.crawl import encounter as encounter_module
from osrlib.crawl.commands import EngageBattle, EnterDungeon, Evade, GrantCoins, GrantItem, SessionMode
from osrlib.crawl.session import GameSession
from osrlib.crawl.views import PlayerView
from osrlib.persistence import session_state

DROPS = get_args(Evade.model_fields["drop"].annotation)

VIEW_2_10_0 = Path(__file__).parent / "saves" / "view_evade_rejections_player_view_2.10.0.json"
"""A `PlayerView` of a two-member party in an encounter with two goblins, dumped as JSON by osrlib 2.10.0, as a
client might store a feed entry. osrlib 2.10.0 had no `EncounterView.evade_rejections`."""

NOTHING_TO_DROP = "encounter.evade.nothing_to_drop"
ALREADY_EVADING = Rejection(code="encounter.evade.already_evading")
NOBODY_ABLE_TO_ACT = Rejection(code="encounter.evade.nobody_able_to_act")
CANNOT_MOVE = "encounter.evade.cannot_move"
OVERLOADED = (Rejection(code=CANNOT_MOVE, params={"reason": "overloaded"}),)


def encounter_session(*, stance: ReactionResult = ReactionResult.INDIFFERENT, distance: int = 40) -> GameSession:
    """The stock party in a dungeon, with empty purses and no food, facing two wolves.

    Indifferent wolves don't pursue, so an accepted `Evade` ends the encounter. Wolves outrun the party, so hostile
    ones start a pursuit.
    """
    game = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=5)
    assert game.execute(EnterDungeon(dungeon_id="delve")).accepted
    for member in game.party.members:
        assert member.inventory.purse.total_coins == 0
        assert not any(
            instance.template.id in ("rations_standard", "rations_iron")
            for instance in member.inventory.all_instances()
        )
    wolves = game.spawn("normal_wolf", 2)
    encounter_module.start_encounter(
        game, groups=[("normal_wolf", wolves)], kind="spawned", distance_feet=distance, pinned_stance=stance
    )
    assert game.mode is SessionMode.ENCOUNTER
    return game


def coins(game: GameSession, index: int) -> None:
    assert game.execute(GrantCoins(character_id=game.party.members[index].id, coins=Coins(sp=5))).accepted


def food(game: GameSession, index: int, item_id: str = "rations_standard") -> None:
    assert game.execute(GrantItem(character_id=game.party.members[index].id, item_id=item_id)).accepted


def paralyse(game: GameSession, index: int) -> None:
    grant_condition(game.party.members[index], Condition.PARALYSED, None)


def entangle(game: GameSession, index: int) -> None:
    grant_condition(game.party.members[index], Condition.ENTANGLED, None)


def overload(game: GameSession, index: int) -> None:
    """Give the member 5,000 gp, which weighs more than the maximum load, so they can't move until it's dropped."""
    assert game.execute(GrantCoins(character_id=game.party.members[index].id, coins=Coins(gp=5000))).accepted
    assert game.party.members[index].movement_rate(game.ruleset) == 0


STATES: dict[str, Callable[[GameSession], None]] = {
    "nothing_to_drop": lambda game: None,
    "coins_in_one_purse": lambda game: coins(game, 1),
    "coins_only_on_a_dead_member": lambda game: (coins(game, 0), kill(game.party.members[0])),
    "coins_on_a_living_and_a_dead_member": lambda game: (coins(game, 0), coins(game, 2), kill(game.party.members[0])),
    "standard_rations": lambda game: food(game, 2),
    "iron_rations_only": lambda game: food(game, 3, "rations_iron"),
    "food_only_on_a_dead_member": lambda game: (food(game, 1), kill(game.party.members[1])),
    "food_and_coins_only_on_a_paralysed_member": lambda game: (food(game, 1), coins(game, 1), paralyse(game, 1)),
    "nobody_able_to_act": lambda game: (
        food(game, 0, "rations_iron"),
        coins(game, 3),
        *(paralyse(game, index) for index in range(len(game.party.members))),
    ),
    "one_member_entangled": lambda game: (coins(game, 0), food(game, 1), entangle(game, 2)),
    "entangled_beside_the_asleep": lambda game: (
        coins(game, 0),
        food(game, 1),
        entangle(game, 3),
        *(grant_condition(game.party.members[index], Condition.ASLEEP, None) for index in range(3)),
    ),
    "overloaded_by_coins": lambda game: (overload(game, 0), food(game, 1)),
    "coins_and_food": lambda game: (coins(game, 0), food(game, 1)),
}

TREASURE_REFUSED = (Rejection(code=NOTHING_TO_DROP, params={"drop": "treasure"}),)
FOOD_REFUSED = (Rejection(code=NOTHING_TO_DROP, params={"drop": "food"}),)

EXPECTED: dict[str, dict[str, tuple[Rejection, ...]]] = {
    "nothing_to_drop": {"none": (), "treasure": TREASURE_REFUSED, "food": FOOD_REFUSED},
    "coins_in_one_purse": {"none": (), "treasure": (), "food": FOOD_REFUSED},
    "coins_only_on_a_dead_member": {"none": (), "treasure": TREASURE_REFUSED, "food": FOOD_REFUSED},
    "coins_on_a_living_and_a_dead_member": {"none": (), "treasure": (), "food": FOOD_REFUSED},
    "standard_rations": {"none": (), "treasure": TREASURE_REFUSED, "food": ()},
    "iron_rations_only": {"none": (), "treasure": TREASURE_REFUSED, "food": ()},
    "food_only_on_a_dead_member": {"none": (), "treasure": TREASURE_REFUSED, "food": FOOD_REFUSED},
    # A member who is alive counts, whether or not they can act.
    "food_and_coins_only_on_a_paralysed_member": {"none": (), "treasure": (), "food": ()},
    # A party with nobody able to act can't run, whatever it would drop (#372).
    "nobody_able_to_act": dict.fromkeys(DROPS, (NOBODY_ABLE_TO_ACT,)),
    # A member caught in a web can't be carried out of it, whatever the party would drop (#372).
    "one_member_entangled": dict.fromkeys(
        DROPS, (Rejection(code=CANNOT_MOVE, params={"character": "character-0003"}),)
    ),
    "entangled_beside_the_asleep": dict.fromkeys(
        DROPS, (Rejection(code=CANNOT_MOVE, params={"character": "character-0004"}),)
    ),
    # Scattering the coins lightens the load enough to run, and dropping a ration doesn't (#372).
    "overloaded_by_coins": {"none": OVERLOADED, "treasure": (), "food": OVERLOADED},
    "coins_and_food": {"none": (), "treasure": (), "food": ()},
}
"""For each state and drop, the rejections an `Evade` gets with indifferent wolves and no pursuit running."""


def fingerprint(game: GameSession) -> dict:
    """Everything a refused command or a view build must leave alone."""
    return {
        "state": json.loads(json.dumps(session_state(game))),
        "streams": {key: state.model_dump(mode="json") for key, state in game.streams.export_states().items()},
        "rounds": game.clock.rounds,
        "log": len(game.command_log),
    }


def evade_rejections(game: GameSession) -> dict[str, tuple[Rejection, ...]]:
    view = game.view(Visibility.PLAYER)
    assert view.encounter is not None
    return view.encounter.evade_rejections


# ---------------------------------------------------------------------- the view agrees with the command


def test_every_state_has_an_expectation_for_every_drop():
    assert set(EXPECTED) == set(STATES)
    assert all(tuple(by_drop) == DROPS for by_drop in EXPECTED.values())


@pytest.mark.parametrize("drop", DROPS)
@pytest.mark.parametrize("state", list(STATES))
def test_the_view_agrees_with_the_evade(state, drop):
    game = encounter_session()
    STATES[state](game)
    shown = evade_rejections(game)
    assert tuple(shown) == DROPS
    assert shown[drop] == EXPECTED[state][drop]

    before = fingerprint(game)
    result = game.execute(Evade(drop=drop))
    assert result.rejections == shown[drop]
    assert result.accepted is (shown[drop] == ())
    if not result.accepted:
        assert result.events == ()
        assert fingerprint(game) == before, "a refused evasion draws nothing, takes no time, and changes nothing"


@pytest.mark.parametrize("drop", DROPS)
def test_every_drop_is_refused_while_a_pursuit_runs(drop):
    game = encounter_session(stance=ReactionResult.HOSTILE, distance=200)
    STATES["coins_and_food"](game)
    assert game.execute(Evade()).accepted
    assert game.encounter is not None and game.encounter.pursuit is not None, "precondition: a chase is on"
    view = game.view(Visibility.PLAYER)
    assert view.encounter is not None and view.encounter.pursuit_gap_feet is not None
    assert view.encounter.evade_rejections == dict.fromkeys(DROPS, (ALREADY_EVADING,))

    before = fingerprint(game)
    result = game.execute(Evade(drop=drop))
    assert result.rejections == view.encounter.evade_rejections[drop]
    assert fingerprint(game) == before


def test_a_pursuit_that_the_evade_starts_shows_in_the_next_view():
    game = encounter_session(stance=ReactionResult.HOSTILE, distance=200)
    assert evade_rejections(game)["none"] == ()
    assert game.execute(Evade()).accepted
    assert evade_rejections(game)["none"] == (ALREADY_EVADING,)


def test_building_the_view_draws_nothing_and_changes_nothing():
    game = encounter_session()
    STATES["coins_and_food"](game)
    before = fingerprint(game)
    first = game.view(Visibility.PLAYER)
    assert fingerprint(game) == before
    assert game.view(Visibility.PLAYER) == first


# ---------------------------------------------------------------------- where the map is empty or absent


def test_there_is_no_encounter_view_outside_an_encounter():
    game = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=5)
    assert game.view(Visibility.PLAYER).encounter is None
    result = game.execute(Evade())
    assert [rejection.code for rejection in result.rejections] == ["session.command.wrong_mode"]
    assert game.execute(EnterDungeon(dungeon_id="delve")).accepted
    assert game.view(Visibility.PLAYER).encounter is None
    result = game.execute(Evade())
    assert [rejection.code for rejection in result.rejections] == ["session.command.wrong_mode"]


def test_the_map_is_empty_in_a_battle_where_the_session_refuses_every_evade():
    game = encounter_session(stance=ReactionResult.HOSTILE)
    STATES["coins_and_food"](game)
    assert game.execute(EngageBattle()).accepted
    assert game.mode is SessionMode.BATTLE
    assert evade_rejections(game) == {}
    for drop in DROPS:
        result = game.execute(Evade(drop=drop))
        assert result.rejections == (
            Rejection(code="session.command.wrong_mode", params={"command": "evade", "mode": "battle"}),
        )


@pytest.mark.parametrize("mode", [SessionMode.GAME_OVER, SessionMode.VICTORY])
def test_the_map_is_empty_once_play_has_ended(mode):
    game = encounter_session()
    STATES["coins_and_food"](game)
    game.mode = mode
    assert evade_rejections(game) == {}
    assert [rejection.code for rejection in game.execute(Evade()).rejections] == ["session.command.wrong_mode"]


# ---------------------------------------------------------------------- the serialized view


def test_the_map_round_trips_through_json():
    game = encounter_session()
    STATES["coins_in_one_purse"](game)
    view = game.view(Visibility.PLAYER)
    document = json.loads(view.model_dump_json())
    assert PlayerView.model_validate(document) == view
    assert document["encounter"]["evade_rejections"] == {
        "none": [],
        "treasure": [],
        "food": [{"code": NOTHING_TO_DROP, "params": {"drop": "food"}}],
    }


def test_a_player_view_from_osrlib_2_10_0_validates_with_the_map_empty():
    document = json.loads(VIEW_2_10_0.read_text(encoding="utf-8"))
    assert document["mode"] == "encounter", "precondition: the stored view is of a party in an encounter"
    assert "evade_rejections" not in document["encounter"]
    view = PlayerView.model_validate(document)
    assert view.encounter is not None
    assert view.encounter.evade_rejections == {}
    assert view.encounter.stance == document["encounter"]["stance"]
