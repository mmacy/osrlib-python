"""Defects an independent review found in the work since 2.4.0.

Pydantic's `model_copy(update=...)` skips validation, so it could build a character whose class
disagreed with its id and put a plain dict in a read-only field. The session's rule for a listener
that executes commands missed one way to lose an update.
"""

from collections.abc import Mapping, Sequence

import pytest
from pydantic import BaseModel, ValidationError

import osrlib.data
from crawl_fixtures import build_milestone_adventure, build_party
from osrlib.core.abilities import AbilityScore
from osrlib.core.character import Character
from osrlib.core.classes import ClassDefinition
from osrlib.core.events import Event
from osrlib.core.frozen import FrozenMap
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import EnterDungeon, MoveParty, SetFlag
from osrlib.crawl.dungeon import Direction, DungeonSpec, Edge, EdgeKind, LevelSpec, WanderingSpec
from osrlib.crawl.session import GameSession
from osrlib.data import load_classes, load_monsters

pytestmark = pytest.mark.xfail(reason="chunk: review-fixes")


# ---------------------------------------------------------------------- a copy with an update is validated


def knight() -> Character:
    document = {**load_classes().get("fighter").model_dump(), "id": "knight", "name": "Knight"}
    definition = ClassDefinition.model_validate(document)
    return Character(
        name="Hild",
        class_id="knight",
        custom_class=definition,
        race=definition.race,
        level=1,
        xp=0,
        scores={ability: 12 for ability in AbilityScore},
        alignment="lawful",
        max_hp=8,
        current_hp=8,
    )


@pytest.mark.parametrize(
    "update",
    [{"class_id": "fighter"}, {"custom_class": None}, {"current_hp": 999}, {"level": 99}, {"name": ""}],
    ids=["another class id", "no class", "hit points above the maximum", "a level past the cap", "an empty name"],
)
def test_a_character_copy_cant_break_the_characters_rules(update):
    character = knight()
    with pytest.raises(ValidationError):
        character.model_copy(update=update)
    assert character.class_id == "knight"


def test_a_character_copy_with_a_good_update_works():
    character = knight()
    twin = character.model_copy(update={"name": "Osric", "current_hp": 3})
    assert (twin.name, twin.current_hp, twin.definition.id) == ("Osric", 3, "knight")
    assert (character.name, character.current_hp) == ("Hild", 8)
    assert character.model_copy() == character


def test_a_monster_copy_cant_break_the_monsters_rules():
    from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
    from osrlib.core.rng import RngStreams

    goblin = spawn_monster(
        load_monsters().get("goblin"), id="monster-0001", stream=RngStreams(master_seed=3).get(MONSTER_SPAWN_STREAM)
    )
    with pytest.raises(ValidationError):
        goblin.model_copy(update={"current_hp": goblin.max_hp + 50})
    assert goblin.model_copy(update={"current_hp": 1}).current_hp == 1


def mapping_fields(value, found: dict, seen: set) -> None:
    """Collect one instance for each model class and mapping field reachable from `value`."""
    if isinstance(value, BaseModel):
        if id(value) in seen:
            return
        seen.add(id(value))
        for name in type(value).model_fields:
            field = getattr(value, name)
            if isinstance(field, FrozenMap):
                found.setdefault((type(value), name), value)
            mapping_fields(field, found, seen)
    elif isinstance(value, Mapping):
        for item in value.values():
            mapping_fields(item, found, seen)
    elif isinstance(value, (tuple, frozenset)):
        for item in value:
            mapping_fields(item, found, seen)


def content_models_with_mapping_fields() -> list:
    found: dict = {}
    seen: set = set()
    for name in sorted(osrlib.data.__all__):
        if name.startswith("load_"):
            mapping_fields(getattr(osrlib.data, name)(), found, seen)
    mapping_fields(build_milestone_adventure(), found, seen)
    return [
        pytest.param(model, field, id=f"{cls.__name__}.{field}")
        for (cls, field), model in sorted(found.items(), key=str)
    ]


@pytest.mark.parametrize(("model", "field"), content_models_with_mapping_fields())
def test_a_copy_with_a_new_mapping_is_still_read_only(model, field):
    replacement = dict(getattr(model, field))
    copy = model.model_copy(update={field: replacement})
    value = getattr(copy, field)
    assert isinstance(value, FrozenMap), type(value).__name__
    assert value == replacement
    assert copy == model
    with pytest.raises(TypeError):
        value["anything"] = 1  # type: ignore[index]


def test_a_copied_ability_is_frozen_and_hashable():
    regeneration = load_monsters().get("troll").ability("regeneration")
    faster = regeneration.model_copy(update={"params": {**regeneration.params, "per_round": 9}})
    assert faster.params["per_round"] == 9
    assert regeneration.params["per_round"] == 3
    assert hash(faster) != hash(regeneration)
    with pytest.raises(TypeError):
        faster.params["per_round"] = 1  # type: ignore[index]


def test_a_content_copy_with_a_bad_update_is_refused():
    troll = load_monsters().get("troll")
    with pytest.raises(ValidationError):
        troll.model_copy(update={"hit_dice": "banana"})
    bog_troll = troll.model_copy(update={"id": "bog_troll", "name": "Bog Troll"})
    assert (bog_troll.id, bog_troll.hit_dice) == ("bog_troll", troll.hit_dice)


# ---------------------------------------------------------------------- a listener that would lose an update


def session() -> GameSession:
    edges = {f"{x},0:west": Edge(kind=EdgeKind.OPEN) for x in range(1, 4)}
    level = LevelSpec(
        number=1, width=4, height=1, entrance=(0, 0), edges=edges, wandering=WanderingSpec(chance_in_six=0)
    )
    crypt = DungeonSpec(id="crypt", name="Crypt", levels=(level,))
    adventure = Adventure(name="A", town=TownSpec(name="Threshold", travel_turns={"crypt": 1}), dungeons=(crypt,))
    game = GameSession.new(build_party(), adventure, seed=7)
    assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
    return game


class CopiesThenReacts:
    """Copies its state, executes a command, and returns the copy. The call inside updates in place."""

    key = "counter"

    def __init__(self, game: GameSession) -> None:
        self.game = game

    def handle(self, events: Sequence[Event], state: dict) -> tuple[list[Event], dict]:
        if any(event.code == "exploration.party.moved" for event in events):
            copied = dict(state)
            copied["outer"] = copied.get("outer", 0) + 1
            self.game.execute(SetFlag(key="moved", value=1))
            return [], copied
        state["inner"] = state.get("inner", 0) + 1
        return [], state


class ReactsInPlace(CopiesThenReacts):
    """The same listener written to the contract: every call updates the dict it's given."""

    def handle(self, events: Sequence[Event], state: dict) -> tuple[list[Event], dict]:
        if any(event.code == "exploration.party.moved" for event in events):
            state["outer"] = state.get("outer", 0) + 1
            self.game.execute(SetFlag(key="moved", value=1))
            return [], state
        state["inner"] = state.get("inner", 0) + 1
        return [], state


def test_an_outer_call_that_returns_an_older_copy_raises():
    """The call inside saved its update in the shared dict, and the copy the outer call returns lacks it."""
    game = session()
    game.register_listener(CopiesThenReacts(game))
    with pytest.raises(RuntimeError, match="counter"):
        game.execute(MoveParty(direction=Direction.EAST))


def test_a_listener_that_updates_in_place_at_both_depths_loses_nothing():
    game = session()
    game.register_listener(ReactsInPlace(game))
    assert game.execute(MoveParty(direction=Direction.EAST)).accepted
    assert game.listener_state == {"counter": {"outer": 1, "inner": 1}}
