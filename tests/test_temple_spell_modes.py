"""The temple sells a service's spell in each usage it can resolve on a party member (issue #368).

*Cure light wounds* has two usages in the SRD: "Healing a living subject" and "Curing paralysis: Paralysing effects are
negated." `PurchaseHealing.spell_mode` names the usage, and `None` keeps the first. The player view answers for each
usage of a service the temple sells in more than one, with the check the command makes.
"""

import json

import pytest

from crawl_fixtures import build_party
from osrlib.core.effects import Condition, EffectDefinition, grant_condition, has_condition, kill
from osrlib.core.events import Visibility
from osrlib.core.items import Coins
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import EnterDungeon, GrantCoins, PurchaseHealing
from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
from osrlib.crawl.exploration import HEALING_SERVICES
from osrlib.crawl.session import DeathRecord, GameSession
from osrlib.crawl.views import PlayerView

NO_EFFECT = "town.healing.no_effect"
CLW = "cure_light_wounds"
CLW_PRICE = HEALING_SERVICES[CLW][1]


def session(gold: int = 5000) -> GameSession:
    level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
    crypt = DungeonSpec(id="crypt", name="Crypt", levels=(level,))
    town = TownSpec(name="Threshold", services=("temple",), travel_turns={"crypt": 1})
    game = GameSession.new(build_party(), Adventure(name="A", town=town, dungeons=(crypt,)), seed=7)
    if gold:
        assert game.execute(GrantCoins(character_id=game.party.members[0].id, coins=Coins(gp=gold))).accepted
    return game


def paralyse(game: GameSession, member) -> None:
    """Paralyse the member with a ledger effect, as a ghoul's touch does."""
    effect, _ = game.ledger.attach(
        EffectDefinition(kind="ghoul_paralysis", permanent=True, condition=Condition.PARALYSED),
        member.id,
        clock=game.clock,
        allocator=game.allocator,
        registry=game.registry(),
    )
    assert effect is not None


def party_gold(game: GameSession) -> int:
    return sum(member.inventory.purse.gp for member in game.party.members)


def fingerprint(game: GameSession) -> dict:
    return {
        "members": [member.model_dump(mode="json") for member in game.party.members],
        "ledger": [effect.model_dump(mode="json") for effect in game.ledger.effects],
        "streams": {key: state.model_dump(mode="json") for key, state in game.streams.export_states().items()},
        "rounds": game.clock.rounds,
        "log": len(game.command_log),
    }


def reasons(rejections) -> list[tuple[str, object]]:
    return [(rejection.code, rejection.params.get("reason")) for rejection in rejections]


# ---------------------------------------------------------------------- buying the paralysis usage


@pytest.mark.parametrize("how", ["ledger_effect", "bare_condition"])
def test_cure_paralysis_ends_the_paralysis_of_a_member_at_full_hit_points(how):
    game = session()
    patient = game.party.members[1]
    if how == "ledger_effect":
        paralyse(game, patient)
    else:
        grant_condition(patient, Condition.PARALYSED, None)
    gold = party_gold(game)

    healing = game.execute(PurchaseHealing(character_id=patient.id, service=CLW))
    assert reasons(healing.rejections) == [(NO_EFFECT, "full_hit_points")], "the default usage still heals"

    result = game.execute(PurchaseHealing(character_id=patient.id, service=CLW, spell_mode="cure_paralysis"))
    assert result.accepted
    assert not has_condition(patient, Condition.PARALYSED)
    assert not game.ledger.active_on(patient.id)
    assert party_gold(game) == gold - CLW_PRICE, "the price is per service, the same for every usage"
    cast = next(event for event in result.events if event.code == "magic.cast.cast")
    assert cast.mode == "cure_paralysis"


def test_cure_paralysis_leaves_hit_points_alone_and_heal_leaves_paralysis_alone():
    game = session()
    patient = game.party.members[1]
    patient.current_hp = 1
    paralyse(game, patient)

    assert game.execute(PurchaseHealing(character_id=patient.id, service=CLW, spell_mode="cure_paralysis")).accepted
    assert patient.current_hp == 1
    assert not has_condition(patient, Condition.PARALYSED)

    paralyse(game, patient)
    assert game.execute(PurchaseHealing(character_id=patient.id, service=CLW, spell_mode="heal")).accepted
    assert patient.current_hp > 1
    assert has_condition(patient, Condition.PARALYSED)


def test_naming_the_first_usage_is_the_same_as_leaving_it_unset():
    results = []
    for spell_mode in (None, "heal"):
        game = session()
        patient = game.party.members[1]
        patient.current_hp = 1
        result = game.execute(PurchaseHealing(character_id=patient.id, service=CLW, spell_mode=spell_mode))
        results.append(([event.model_dump(mode="json") for event in result.events], fingerprint(game)))
    assert results[0] == results[1]


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        (lambda game, member: None, "not_paralysed"),
        (lambda game, member: setattr(member, "current_hp", 1), "not_paralysed"),
        (lambda game, member: (paralyse(game, member), kill(member)), "dead"),
    ],
)
def test_cure_paralysis_that_would_do_nothing_is_refused_and_costs_nothing(setup, reason):
    game = session()
    patient = game.party.members[1]
    setup(game, patient)
    if has_condition(patient, Condition.DEAD):
        game.death_records[patient.id] = DeathRecord(round=game.clock.rounds, cause="damage")
    before = fingerprint(game)
    result = game.execute(PurchaseHealing(character_id=patient.id, service=CLW, spell_mode="cure_paralysis"))
    assert reasons(result.rejections) == [(NO_EFFECT, reason)]
    assert result.rejections[0].params == {"item": CLW, "character": patient.id, "reason": reason}
    assert fingerprint(game) == before


# ---------------------------------------------------------------------- usages the temple refuses


@pytest.mark.parametrize(
    ("service", "spell_mode"),
    [
        # Manual usages: the session resolves nothing for them, so the party would pay for no change.
        ("cure_disease", "kill_green_slime"),
        ("neutralize_poison", "items"),
        # It kills only undead, and no party member is undead.
        ("raise_dead", "destroy_undead"),
    ],
)
def test_a_usage_the_temple_doesnt_sell_is_refused_before_the_fee(service, spell_mode):
    game = session(gold=0)
    patient = game.party.members[1]
    before = fingerprint(game)
    result = game.execute(PurchaseHealing(character_id=patient.id, service=service, spell_mode=spell_mode))
    assert [(rejection.code, rejection.params) for rejection in result.rejections] == [
        ("town.healing.mode_not_offered", {"item": service, "mode": spell_mode})
    ]
    assert fingerprint(game) == before


def test_an_unknown_usage_gets_the_cast_code_before_the_level_and_the_fee():
    game = session(gold=0)
    patient = game.party.members[1]
    before = fingerprint(game)
    result = game.execute(
        PurchaseHealing(character_id=patient.id, service=CLW, spell_mode="restore_life", caster_level=99)
    )
    assert [(rejection.code, rejection.params) for rejection in result.rejections] == [
        ("magic.cast.unknown_mode", {"spell": "cure_light_wounds", "mode": "restore_life"})
    ]
    assert fingerprint(game) == before


def test_a_command_without_spell_mode_validates_as_the_first_usage():
    command = PurchaseHealing.model_validate(
        {"command_type": "purchase_healing", "character_id": "pc-1", "service": CLW}
    )
    assert command.spell_mode is None


# ---------------------------------------------------------------------- the player view


@pytest.mark.parametrize(
    ("state", "heal", "cure_paralysis"),
    [
        ("healthy", "full_hit_points", "not_paralysed"),
        ("wounded", None, "not_paralysed"),
        ("paralysed", "full_hit_points", None),
        ("paralysed_and_wounded", None, None),
        ("dead", "dead", "dead"),
    ],
)
def test_the_view_answers_for_each_usage_and_agrees_with_the_purchase(state, heal, cure_paralysis):
    game = session()
    patient = game.party.members[1]
    if "wounded" in state:
        patient.current_hp = 1
    if "paralysed" in state:
        paralyse(game, patient)
    if state == "dead":
        kill(patient)
        game.death_records[patient.id] = DeathRecord(round=game.clock.rounds, cause="damage")
    view = game.view(Visibility.PLAYER)
    by_mode = view.town_healing_mode_rejections[patient.id][CLW]
    assert list(by_mode) == ["heal", "cure_paralysis"]
    assert by_mode["heal"] == view.town_healing_rejections[patient.id][CLW], "the first usage is the old answer"

    for spell_mode, expected in (("heal", heal), ("cure_paralysis", cure_paralysis)):
        shown = by_mode[spell_mode]
        if expected is None:
            assert shown == ()
        else:
            assert reasons(shown) == [(NO_EFFECT, expected)]
            purchase = PurchaseHealing(character_id=patient.id, service=CLW, spell_mode=spell_mode)
            assert tuple(game.execute(purchase).rejections) == shown


def test_cure_light_wounds_is_the_only_service_sold_in_more_than_one_usage():
    game = session()
    view = game.view(Visibility.PLAYER)
    assert list(view.town_healing_mode_rejections) == [member.id for member in game.party.members]
    assert all(list(by_service) == [CLW] for by_service in view.town_healing_mode_rejections.values())
    assert view.town_healing_mode_names == {CLW: {"heal": "Heal a living subject", "cure_paralysis": "Cure paralysis"}}


def test_outside_town_the_usage_map_is_empty_and_the_names_stay():
    game = session()
    assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
    view = game.view(Visibility.PLAYER)
    assert view.town_healing_mode_rejections == {}
    assert list(view.town_healing_mode_names) == [CLW]


def test_building_the_view_draws_nothing_and_changes_nothing():
    game = session()
    paralyse(game, game.party.members[1])
    before = fingerprint(game)
    first = game.view(Visibility.PLAYER)
    assert fingerprint(game) == before
    assert game.view(Visibility.PLAYER) == first


def test_the_usage_maps_round_trip_through_json_and_default_to_empty():
    game = session()
    paralyse(game, game.party.members[1])
    view = game.view(Visibility.PLAYER)
    document = json.loads(view.model_dump_json())
    assert PlayerView.model_validate(document) == view
    assert document["town_healing_mode_rejections"][game.party.members[1].id][CLW]["cure_paralysis"] == []

    del document["town_healing_mode_rejections"], document["town_healing_mode_names"]
    older = PlayerView.model_validate(document)
    assert (older.town_healing_mode_rejections, older.town_healing_mode_names) == ({}, {})
