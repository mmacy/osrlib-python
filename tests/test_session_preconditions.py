"""A refused call changes nothing, and an accepted command doesn't raise (issues #162 and #167).

The cases here are the session's own preconditions: a command that names one treasure twice, a
listener that executes commands and keeps state, two listeners under one key, a procedure called in
the wrong state, a negative span or count, and a command model that lists a mode the session always
refuses it in.
"""

from collections.abc import Sequence

import pytest
from pydantic import BaseModel, ValidationError

from crawl_fixtures import build_party
from osrlib.core.events import Event
from osrlib.core.items import ValuableInstance
from osrlib.core.tables import ReactionResult
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.battle import start_battle
from osrlib.crawl.commands import (
    AnyCommand,
    DropItems,
    EngageBattle,
    EnterDungeon,
    GiveItems,
    GrantItem,
    MoveParty,
    PlaceParty,
    RollDice,
    SellTreasure,
    SessionMode,
    SetFlag,
    SpawnMonsters,
    SpawnNpcParty,
    parse_command,
)
from osrlib.crawl.dungeon import Direction, DungeonSpec, Edge, EdgeKind, LevelSpec, PartyLocation, WanderingSpec
from osrlib.crawl.encounter import start_encounter
from osrlib.crawl.session import ENCOUNTER_STREAM, GameSession
from osrlib.errors import ContentValidationError

NOT_CARRIED = "exploration.item.not_carried"
FAR = 120


def session(enter: bool = True) -> GameSession:
    """The stock party in town, or at the entrance of a quiet four-cell corridor."""
    edges = {f"{x},0:west": Edge(kind=EdgeKind.OPEN) for x in range(1, 4)}
    level = LevelSpec(
        number=1, width=4, height=1, entrance=(0, 0), edges=edges, wandering=WanderingSpec(chance_in_six=0)
    )
    crypt = DungeonSpec(id="crypt", name="Crypt", levels=(level,))
    adventure = Adventure(name="A", town=TownSpec(name="Threshold", travel_turns={"crypt": 1}), dungeons=(crypt,))
    game = GameSession.new(build_party(), adventure, seed=7)
    if enter:
        assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
    return game


def garnet(instance_id: str = "valuable-9001") -> ValuableInstance:
    return ValuableInstance(instance_id=instance_id, kind="gem", name="Garnet", value_gp=100, weight_coins=1)


def friendly_encounter(game: GameSession) -> None:
    start_encounter(
        game,
        groups=[("goblin", game.spawn("goblin", 2))],
        kind="spawned",
        distance_feet=FAR,
        pinned_stance=ReactionResult.FRIENDLY,
    )
    assert game.mode is SessionMode.ENCOUNTER


def battle(game: GameSession) -> None:
    friendly_encounter(game)
    assert game.execute(EngageBattle()).accepted
    assert game.mode is SessionMode.BATTLE


# ---------------------------------------------------------------------- one treasure named twice (#167, item 1)

TWICE = ("valuable-9001", "valuable-9001")


def test_selling_one_gem_twice_is_refused_and_sells_nothing():
    game = session(enter=False)
    seller = game.party.members[0]
    seller.inventory.valuables.append(garnet())
    gold, logged = seller.inventory.purse.gp, len(game.command_log)
    result = game.execute(SellTreasure(item_ids=TWICE))
    assert not result.accepted
    assert [(rejection.code, rejection.params["item"]) for rejection in result.rejections] == [
        (NOT_CARRIED, "valuable-9001")
    ]
    assert [valuable.instance_id for valuable in seller.inventory.valuables] == ["valuable-9001"]
    assert seller.inventory.purse.gp == gold
    assert len(game.command_log) == logged


def test_selling_two_different_gems_still_works():
    game = session(enter=False)
    seller = game.party.members[0]
    seller.inventory.valuables.extend([garnet(), garnet("valuable-9002")])
    gold = seller.inventory.purse.gp
    assert game.execute(SellTreasure(item_ids=("valuable-9001", "valuable-9002"))).accepted
    assert seller.inventory.purse.gp == gold + 200


@pytest.mark.parametrize("in_encounter", [False, True], ids=["exploring", "in an encounter"])
def test_dropping_one_gem_twice_is_refused_and_drops_nothing(in_encounter):
    game = session()
    member = game.party.members[0]
    member.inventory.valuables.append(garnet())
    if in_encounter:
        friendly_encounter(game)
    logged, clock = len(game.command_log), game.clock.rounds
    result = game.execute(DropItems(character_id=member.id, item_ids=TWICE))
    assert not result.accepted
    assert [rejection.code for rejection in result.rejections] == [NOT_CARRIED]
    assert [valuable.instance_id for valuable in member.inventory.valuables] == ["valuable-9001"]
    assert not game.dungeon_state.piles
    assert (len(game.command_log), game.clock.rounds) == (logged, clock)


def test_giving_one_gem_twice_is_refused_and_gives_nothing():
    game = session(enter=False)
    giver, recipient = game.party.members[:2]
    giver.inventory.valuables.append(garnet())
    logged = len(game.command_log)
    result = game.execute(GiveItems(character_id=giver.id, recipient_id=recipient.id, item_ids=TWICE))
    assert not result.accepted
    assert [rejection.code for rejection in result.rejections] == [NOT_CARRIED]
    assert [valuable.instance_id for valuable in giver.inventory.valuables] == ["valuable-9001"]
    assert recipient.inventory.valuables == []
    assert len(game.command_log) == logged


def test_naming_a_stack_twice_still_moves_two_of_it():
    """An equipment id counts items in a stack, so listing it twice asks for two, which is fine."""
    game = session(enter=False)
    giver, recipient = game.party.members[:2]
    assert game.execute(GrantItem(character_id=giver.id, item_id="torch", quantity=6)).accepted
    assert game.execute(
        GiveItems(character_id=giver.id, recipient_id=recipient.id, item_ids=("torch", "torch"))
    ).accepted


# ---------------------------------------------------------------------- listeners (#167, items 5 and 11)


class InPlaceCounter:
    """Counts its `handle` calls in the state dict it's given, and reacts to a move with a command."""

    key = "counter"

    def __init__(self, game: GameSession) -> None:
        self.game = game

    def handle(self, events: Sequence[Event], state: dict) -> tuple[list[Event], dict]:
        state["calls"] = state.get("calls", 0) + 1
        if any(event.code == "exploration.party.moved" for event in events):
            self.game.execute(SetFlag(key="moved", value=1))
        return [], state


class StaleCounter(InPlaceCounter):
    """Reads its count before it executes a command, and returns a new dict built from that count."""

    def handle(self, events: Sequence[Event], state: dict) -> tuple[list[Event], dict]:
        calls = state.get("calls", 0) + 1
        if any(event.code == "exploration.party.moved" for event in events):
            self.game.execute(SetFlag(key="moved", value=1))
        return [], {"calls": calls}


class Quiet:
    def __init__(self, key: str) -> None:
        self.key = key

    def handle(self, events: Sequence[Event], state: dict) -> tuple[list[Event], dict]:
        return [], {**state, "calls": state.get("calls", 0) + 1}


def test_a_listener_that_executes_a_command_keeps_every_update_to_its_state():
    game = session()
    game.register_listener(InPlaceCounter(game))
    assert game.execute(MoveParty(direction=Direction.EAST)).accepted
    # One call for the move, and one inside it for the SetFlag the listener executed.
    assert game.listener_state == {"counter": {"calls": 2}}
    assert game.flags["moved"] == 1


def test_a_listener_that_would_lose_an_update_raises():
    """Returning a state built before a nested call drops what the nested call saved, so the session says so."""
    game = session()
    game.register_listener(StaleCounter(game))
    with pytest.raises(RuntimeError, match="counter"):
        game.execute(MoveParty(direction=Direction.EAST))


def test_a_listener_may_still_return_a_new_dict_when_it_executes_nothing():
    game = session()
    game.register_listener(Quiet("quiet"))
    game.execute(MoveParty(direction=Direction.EAST))
    game.execute(MoveParty(direction=Direction.EAST))
    assert game.listener_state == {"quiet": {"calls": 2}}


def test_a_second_listener_with_the_same_key_is_refused():
    game = session()
    first = Quiet("audit")
    game.register_listener(first)
    with pytest.raises(ValueError, match="audit"):
        game.register_listener(Quiet("audit"))
    assert game.listeners == [first]
    game.register_listener(Quiet("other"))
    assert len(game.listeners) == 2


# ---------------------------------------------------------- procedures in the wrong state (#162, items 5 and 6)


def test_start_battle_needs_an_open_encounter():
    game = session()
    with pytest.raises(ValueError, match="encounter"):
        start_battle(game)
    assert game.mode is SessionMode.EXPLORING
    assert game.battle is None


def test_start_encounter_refuses_to_replace_an_open_encounter():
    game = session()
    friendly_encounter(game)
    first = game.encounter
    newcomers = game.spawn("orc", 1)
    stream = game.streams.get(ENCOUNTER_STREAM)
    before = stream.export_state()
    with pytest.raises(ValueError, match="encounter"):
        start_encounter(game, groups=[("orc", newcomers)], kind="wandering")
    assert game.encounter is first
    assert [group.label for group in first.groups] == ["goblin"]
    assert stream.export_state() == before


# ---------------------------------------------------------- negative spans and counts (#162, items 3 and 15)


def test_advance_turns_refuses_a_negative_span():
    game = session()
    clock = game.clock.rounds
    with pytest.raises(ValueError):
        game.advance_turns(-1)
    assert game.clock.rounds == clock
    assert game.advance_turns(0) == ([], False)


def test_spawn_refuses_a_negative_count():
    game = session()
    with pytest.raises(ValueError):
        game.spawn("goblin", -1)
    assert game.monsters == {}


def test_award_immediate_xp_refuses_a_negative_amount():
    game = session()
    before = [member.xp for member in game.party.members]
    with pytest.raises(ValueError):
        game.award_immediate_xp(-500)
    assert [member.xp for member in game.party.members] == before
    assert game.award_immediate_xp(0) == []


# ---------------------------------------------------------------------- allowed_modes (#167, item 9)


def test_spawn_monsters_lists_the_modes_it_can_be_accepted_in():
    assert SpawnMonsters.allowed_modes == {SessionMode.EXPLORING, SessionMode.ENCOUNTER, SessionMode.BATTLE}
    spawn = SpawnMonsters(template_id="orc", count_fixed=1, distance_feet=FAR)
    in_town = session(enter=False).execute(spawn)
    assert [rejection.code for rejection in in_town.rejections] == ["session.command.wrong_mode"]
    for setup in (lambda game: None, friendly_encounter, battle):
        game = session()
        setup(game)
        assert game.execute(spawn).accepted, game.mode


def test_spawn_npc_party_lists_the_modes_it_can_be_accepted_in():
    assert SpawnNpcParty.allowed_modes == {SessionMode.EXPLORING}
    spawn = SpawnNpcParty(party_kind="basic", distance_feet=FAR)
    assert session().execute(spawn).accepted
    for setup in (friendly_encounter, battle):
        game = session()
        setup(game)
        result = game.execute(spawn)
        assert [rejection.code for rejection in result.rejections] == ["session.command.wrong_mode"], game.mode
    in_town = session(enter=False).execute(spawn)
    assert [rejection.code for rejection in in_town.rejections] == ["session.command.wrong_mode"]


def test_place_party_lists_no_mode_with_an_encounter_open():
    assert SessionMode.ENCOUNTER not in PlaceParty.allowed_modes
    assert SessionMode.BATTLE not in PlaceParty.allowed_modes
    assert {SessionMode.TOWN, SessionMode.EXPLORING} <= PlaceParty.allowed_modes
    place = PlaceParty(location=PartyLocation(kind="town"))
    assert session().execute(place).accepted
    for setup in (friendly_encounter, battle):
        game = session()
        setup(game)
        result = game.execute(place)
        assert [rejection.code for rejection in result.rejections] == ["session.command.wrong_mode"], game.mode


# ---------------------------------------------------------------------- dice in a command (#167, item 10)


class Request(BaseModel):
    """A web front end's request body."""

    command: AnyCommand


BAD_DICE = [
    {"command_type": "roll_dice", "expression": "1d"},
    {"command_type": "spawn_monsters", "template_id": "goblin", "count_dice": "2q6", "distance_feet": 30},
    {"command_type": "spawn_npc_party", "party_kind": "basic", "count_dice": "many", "distance_feet": 30},
]


@pytest.mark.parametrize("payload", BAD_DICE, ids=lambda payload: payload["command_type"])
def test_a_malformed_dice_expression_is_a_pydantic_validation_error(payload):
    with pytest.raises(ValidationError):
        Request.model_validate({"command": payload})


@pytest.mark.parametrize("payload", BAD_DICE, ids=lambda payload: payload["command_type"])
def test_parse_command_still_reports_malformed_content(payload):
    with pytest.raises(ContentValidationError):
        parse_command(payload)


def test_building_a_command_with_bad_dice_is_a_validation_error():
    with pytest.raises(ValidationError):
        RollDice(expression="1d")
    assert RollDice(expression="2d6+1").expression == "2d6+1"
