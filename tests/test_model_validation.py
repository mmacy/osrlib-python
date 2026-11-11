"""Models refuse what the engine can't use, and the catalogs can't be changed (issues #162 and #166).

A model that builds also works: the failure comes when the content is read, not partway through a
battle. A refused assignment leaves the model as it was. The built-in catalogs are shared by every
session in the process, so nothing in them can be changed, at any depth.
"""

from collections.abc import Mapping

import pytest
from pydantic import BaseModel, ValidationError

import osrlib.data
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.classes import ClassDefinition
from osrlib.core.items import CombatFacet, WeaponQuality
from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.spells import TargetingMode, TargetingSpec
from osrlib.core.treasure import MagicAllotment, MagicItemType
from osrlib.data import load_classes, load_equipment, load_monsters

pytestmark = pytest.mark.xfail(reason="chunk: model-validation")

LOADERS = sorted(name for name in osrlib.data.__all__ if name.startswith("load_"))


def hero():
    stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
    return create_character(
        name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=Ruleset(), stream=stream
    ).character


def goblin():
    stream = RngStreams(master_seed=3).get(MONSTER_SPAWN_STREAM)
    return spawn_monster(load_monsters().get("goblin"), id="monster-0001", stream=stream)


# ---------------------------------------------------------------------- CombatFacet (#166, item 22)


def test_combat_facet_needs_missile_ranges_exactly_when_it_has_the_missile_quality():
    with pytest.raises(ValidationError):
        CombatFacet(damage="1d4", qualities=(WeaponQuality.MISSILE,))
    sling = load_equipment().get("sling")
    with pytest.raises(ValidationError):
        CombatFacet(damage="1d4", qualities=(WeaponQuality.MELEE,), missile_ranges=sling.missile_ranges)
    assert CombatFacet(damage="1d4", qualities=(WeaponQuality.MISSILE,), missile_ranges=sling.missile_ranges)
    assert CombatFacet(damage="1d4", qualities=(WeaponQuality.MELEE,)).missile_ranges is None


# ---------------------------------------------------------------------- ClassDefinition (#166, item 24)


@pytest.mark.parametrize(("class_id", "tag"), [("magic_user", "arcane_magic"), ("cleric", "divine_magic")])
def test_a_casting_class_needs_a_spell_list(class_id, tag):
    document = load_classes().get(class_id).model_dump()
    casting = [ability for ability in document["abilities"] if ability["tag"] == tag]
    assert casting, f"{class_id} has a {tag} ability"
    for ability in casting:
        ability["params"] = {key: value for key, value in ability["params"].items() if key != "spell_list"}
    with pytest.raises(ValidationError):
        ClassDefinition.model_validate({**document, "id": "hedge_caster"})


def test_the_built_in_classes_round_trip():
    for definition in load_classes().classes:
        assert ClassDefinition.model_validate(definition.model_dump()) == definition


# ---------------------------------------------------- hit points above the maximum (#166 item 25, #162 item 4)


def test_a_monster_cant_have_more_hit_points_than_its_maximum():
    monster = goblin()
    with pytest.raises(ValidationError):
        monster.current_hp = monster.max_hp + 50
    with pytest.raises(ValidationError):
        type(monster).model_validate({**monster.model_dump(), "current_hp": monster.max_hp + 50})


@pytest.mark.parametrize("build", [hero, goblin], ids=["a character", "a monster"])
def test_a_refused_assignment_leaves_the_old_value(build):
    creature = build()
    before = creature.current_hp
    with pytest.raises(ValidationError):
        creature.current_hp = creature.max_hp + 50
    assert creature.current_hp == before


def test_a_refused_level_leaves_the_old_level():
    character = hero()
    with pytest.raises(ValidationError):
        character.level = 99
    assert character.level == 1


def test_an_assignment_that_passes_still_applies():
    character, monster = hero(), goblin()
    character.current_hp = 1
    monster.current_hp = 1
    assert (character.current_hp, monster.current_hp) == (1, 1)


# ---------------------------------------------------------------------- targeting and allotments


@pytest.mark.parametrize(
    "fields",
    [
        {"mode": TargetingMode.UP_TO_N},
        {"mode": TargetingMode.UP_TO_N, "count": 0},
        {"mode": TargetingMode.UP_TO_N, "count": -2},
        {"mode": TargetingMode.UP_TO_N, "count_dice": "1d4-4"},
        {"mode": TargetingMode.HD_BUDGET},
        {"mode": TargetingMode.HD_BUDGET, "hd_budget_dice": "1d4-4"},
    ],
    ids=[
        "up to N with no count",
        "a count of zero",
        "a negative count",
        "count dice that can roll below one",
        "a Hit Dice budget with no dice",
        "budget dice that can roll below one",
    ],
)
def test_a_targeting_spec_that_can_pick_nobody_is_refused(fields):
    with pytest.raises(ValidationError):
        TargetingSpec(**fields)


def test_targeting_specs_that_work_are_accepted():
    assert TargetingSpec(mode=TargetingMode.UP_TO_N, count=3).count == 3
    assert TargetingSpec(mode=TargetingMode.UP_TO_N, count_dice="2d4").count_dice == "2d4"
    assert TargetingSpec(mode=TargetingMode.HD_BUDGET, hd_budget_dice="2d8").hd_budget_dice == "2d8"
    assert TargetingSpec(mode=TargetingMode.SINGLE).count is None


def test_a_magic_allotment_cant_exclude_every_kind():
    with pytest.raises(ValidationError):
        MagicAllotment(kind="any", count=1, exclude=tuple(MagicItemType))
    some = tuple(kind for kind in MagicItemType if kind is not MagicItemType.POTION)
    assert MagicAllotment(kind="any", count=1, exclude=some).exclude == some


# ---------------------------------------------------------------------- catalogs nobody can change (#166, item 36)


def mutable_containers(value, path: str, found: list[str]) -> None:
    """Collect the path of every dict, list, set, or bytearray reachable from `value`."""
    if isinstance(value, (dict, list, set, bytearray)):
        found.append(f"{path}: {type(value).__name__}")
    elif isinstance(value, BaseModel):
        for name in type(value).model_fields:
            mutable_containers(getattr(value, name), f"{path}.{name}", found)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            mutable_containers(item, f"{path}[{key!r}]", found)
    elif isinstance(value, (tuple, frozenset)):
        for index, item in enumerate(value):
            mutable_containers(item, f"{path}[{index}]", found)


@pytest.mark.parametrize("loader", LOADERS)
def test_a_catalog_has_nothing_that_can_be_changed(loader):
    found: list[str] = []
    mutable_containers(getattr(osrlib.data, loader)(), loader, found)
    assert found[:5] == [], f"{len(found)} mutable containers"


def test_the_trolls_regeneration_cant_be_rewritten():
    params = load_monsters().get("troll").ability("regeneration").params
    try:
        with pytest.raises(TypeError):
            params["per_round"] = 99  # type: ignore[index]
    finally:
        if params["per_round"] != 3:
            # The write went through. Undo it, so the shared catalog doesn't spoil the tests after this one.
            params["per_round"] = 3  # type: ignore[index]
    assert load_monsters().get("troll").ability("regeneration").params["per_round"] == 3


@pytest.mark.parametrize("loader", LOADERS)
def test_a_catalog_still_dumps_and_loads(loader):
    catalog = getattr(osrlib.data, loader)()
    as_json = catalog.model_dump(mode="json")
    assert type(catalog).model_validate(as_json) == catalog
    assert type(catalog).model_validate(catalog.model_dump()) == catalog


def test_a_template_you_copy_is_still_yours_to_change():
    """An adventure builds its own monster from a built-in one, with params of its own."""
    troll = load_monsters().get("troll")
    document = troll.model_dump()
    for ability in document["abilities"]:
        if ability["tag"] == "regeneration":
            ability["params"]["per_round"] = 5
    custom = type(troll).model_validate({**document, "id": "bog_troll", "name": "Bog Troll"})
    assert custom.ability("regeneration").params["per_round"] == 5
    assert troll.ability("regeneration").params["per_round"] == 3
