"""Monster display names, plurals, and families (issue #229).

A [`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate] has the SRD's printed name, which is often
written for an index, like "Centipede, Giant". These tests fix the three fields a game shows and matches
on instead: `display_name`, `display_name_plural`, and `family_id`.

The built-in catalog's values are stated here in full. `ODD_DISPLAY_NAMES` lists every template whose
display name isn't its printed name in lowercase, `IRREGULAR_PLURALS` lists every plural that doesn't
follow `regular_plural`, and `FAMILIES` lists every family with more than one form.
"""

import json
import re
from importlib import resources

import pytest

from osrlib.core.monsters import MonsterCatalog, MonsterTemplate
from osrlib.data import load_monsters

ODD_DISPLAY_NAMES = {
    "air_elemental_greater": "air elemental",
    "air_elemental_intermediate": "air elemental",
    "air_elemental_lesser": "air elemental",
    "ape_white": "white ape",
    "centipede_giant": "giant centipede",
    "crab_giant": "giant crab",
    "djinni_lesser": "djinni",
    "dwarf_monster": "dwarf",
    "earth_elemental_greater": "earth elemental",
    "earth_elemental_intermediate": "earth elemental",
    "earth_elemental_lesser": "earth elemental",
    "efreeti_lesser": "efreeti",
    "elf_monster": "elf",
    "ferret_giant": "giant ferret",
    "fire_elemental_greater": "fire elemental",
    "fire_elemental_intermediate": "fire elemental",
    "fire_elemental_lesser": "fire elemental",
    "halfling_monster": "halfling",
    "hellhound_3": "hellhound",
    "hellhound_4": "hellhound",
    "hellhound_5": "hellhound",
    "hellhound_6": "hellhound",
    "hellhound_7": "hellhound",
    "hydra_10": "hydra",
    "hydra_11": "hydra",
    "hydra_12": "hydra",
    "hydra_5": "hydra",
    "hydra_6": "hydra",
    "hydra_7": "hydra",
    "hydra_8": "hydra",
    "hydra_9": "hydra",
    "insect_swarm_2": "insect swarm",
    "insect_swarm_3": "insect swarm",
    "insect_swarm_4": "insect swarm",
    "invisible_stalker_monster": "invisible stalker",
    "leech_giant": "giant leech",
    "neanderthal_caveman": "Neanderthal",
    "normal_bat": "bat",
    "normal_crocodile": "crocodile",
    "normal_hawk": "hawk",
    "normal_human": "human",
    "normal_rat": "rat",
    "normal_rhinoceros": "rhinoceros",
    "normal_wolf": "wolf",
    "octopus_giant": "giant octopus",
    "scorpion_giant": "giant scorpion",
    "sea_serpent_lesser": "sea serpent",
    "shrew_giant": "giant shrew",
    "small_herd_animal_1": "small herd animal",
    "small_herd_animal_2": "small herd animal",
    "squid_giant": "giant squid",
    "toad_giant": "giant toad",
    "vampire_7": "vampire",
    "vampire_8": "vampire",
    "vampire_9": "vampire",
    "veteran_1": "veteran",
    "veteran_2": "veteran",
    "veteran_3": "veteran",
    "water_elemental_greater": "water elemental",
    "water_elemental_intermediate": "water elemental",
    "water_elemental_lesser": "water elemental",
    "weasel_giant": "giant weasel",
}
"""Every built-in template whose display name isn't its printed name in lowercase.

The printed name loses a trailing parenthesis, the word after a comma moves to the front, and a leading
"Normal" goes. "Neanderthal" is a proper noun and keeps its capital.
"""

IRREGULAR_PLURALS = {
    "cyclops": "cyclopes",
    "devil_swine": "devil swine",
    "dire_wolf": "dire wolves",
    "djinni_lesser": "djinn",
    "dwarf_monster": "dwarves",
    "efreeti_lesser": "efreet",
    "elf_monster": "elves",
    "giant_bass": "giant bass",
    "giant_catfish": "giant catfish",
    "giant_rockfish": "giant rockfish",
    "giant_sturgeon": "giant sturgeon",
    "lizard_man": "lizard men",
    "merman": "mermen",
    "normal_wolf": "wolves",
    "pegasus": "pegasi",
    "squid_giant": "giant squid",
    "triceratops": "triceratops",
    "werewolf": "werewolves",
}
"""Every built-in template whose plural isn't `regular_plural` of its display name."""

FAMILIES = {
    "air_elemental": 3,
    "earth_elemental": 3,
    "fire_elemental": 3,
    "hellhound": 5,
    "hydra": 8,
    "insect_swarm": 3,
    "small_herd_animal": 2,
    "vampire": 3,
    "veteran": 3,
    "water_elemental": 3,
}
"""Every built-in family with more than one form, and how many forms it has."""


def regular_plural(name: str) -> str:
    """The regular English plural that `MonsterTemplate.display_name_plural` documents."""
    if name.endswith(("s", "x", "z", "ch", "sh")):
        return name + "es"
    if name.endswith("y") and name[-2] not in "aeiou":
        return name[:-1] + "ies"
    return name + "s"


def custom_template(**fields) -> MonsterTemplate:
    """A template an adventure might add: the goblin's stat block under the fields given."""
    document = load_monsters().get("goblin").model_dump()
    for field in ("display_name", "display_name_plural", "family_id"):
        document.pop(field)
    return MonsterTemplate.model_validate({**document, **fields})


# ---------------------------------------------------------------------- the built-in catalog


def test_the_expectation_tables_name_real_templates():
    ids = {template.id for template in load_monsters().monsters}
    assert set(ODD_DISPLAY_NAMES) <= ids
    assert set(IRREGULAR_PLURALS) <= ids


@pytest.mark.parametrize("template", load_monsters().monsters, ids=lambda template: template.id)
def test_display_name(template):
    expected = ODD_DISPLAY_NAMES.get(template.id, template.name.lower())
    assert template.display_name == expected


@pytest.mark.parametrize("template", load_monsters().monsters, ids=lambda template: template.id)
def test_display_name_plural(template):
    expected = IRREGULAR_PLURALS.get(template.id) or regular_plural(template.display_name)
    assert template.display_name_plural == expected


def test_no_display_name_reads_like_an_index_entry():
    for template in load_monsters().monsters:
        for text in (template.display_name, template.display_name_plural):
            assert text and text == text.strip(), template.id
            assert not re.search(r"[,()\d]", text), f"{template.id}: {text!r}"
            assert not text.lower().startswith("normal "), f"{template.id}: {text!r}"


@pytest.mark.parametrize(
    ("monster_id", "singular", "plural", "family_id"),
    [
        ("centipede_giant", "giant centipede", "giant centipedes", "centipede_giant"),
        ("veteran_2", "veteran", "veterans", "veteran"),
        ("hellhound_5", "hellhound", "hellhounds", "hellhound"),
        ("hydra_12", "hydra", "hydras", "hydra"),
        ("air_elemental_greater", "air elemental", "air elementals", "air_elemental"),
        ("dwarf_monster", "dwarf", "dwarves", "dwarf_monster"),
        ("normal_wolf", "wolf", "wolves", "normal_wolf"),
        ("normal_human", "human", "humans", "normal_human"),
        ("neanderthal_caveman", "Neanderthal", "Neanderthals", "neanderthal_caveman"),
        ("octopus_giant", "giant octopus", "giant octopuses", "octopus_giant"),
        ("harpy", "harpy", "harpies", "harpy"),
        ("tyrannosaurus_rex", "tyrannosaurus rex", "tyrannosaurus rexes", "tyrannosaurus_rex"),
        ("black_dragon", "black dragon", "black dragons", "black_dragon"),
        ("goblin", "goblin", "goblins", "goblin"),
    ],
)
def test_names_a_game_shows(monster_id, singular, plural, family_id):
    template = load_monsters().get(monster_id)
    assert (template.display_name, template.display_name_plural, template.family_id) == (singular, plural, family_id)


def test_families_with_more_than_one_form():
    sizes: dict[str, int] = {}
    for template in load_monsters().monsters:
        sizes[template.family_id] = sizes.get(template.family_id, 0) + 1
    assert {family_id: size for family_id, size in sizes.items() if size > 1} == FAMILIES


def test_a_creature_with_one_form_is_its_own_family():
    for template in load_monsters().monsters:
        if template.family_id not in FAMILIES:
            assert template.family_id == template.id


def test_the_forms_of_a_family_look_the_same_to_the_players():
    catalog = load_monsters()
    for family_id in FAMILIES:
        forms = catalog.family(family_id)
        assert len({form.display_name for form in forms}) == 1, family_id
        assert len({form.display_name_plural for form in forms}) == 1, family_id
        assert len({form.categories for form in forms}) == 1, family_id
        assert len({form.page for form in forms}) == 1, family_id


def test_the_data_file_states_every_name():
    """The compiled data has each field written out, so no built-in template relies on a default."""
    document = json.loads(resources.files("osrlib.data").joinpath("monsters.json").read_text(encoding="utf-8"))
    for entry in document["monsters"]:
        assert entry["display_name"], entry["id"]
        assert entry["display_name_plural"], entry["id"]
        assert entry["family_id"], entry["id"]


# ---------------------------------------------------------------------- MonsterCatalog.family


def test_family_returns_every_form_in_catalog_order():
    catalog = load_monsters()
    assert [template.id for template in catalog.family("hellhound")] == [
        "hellhound_3",
        "hellhound_4",
        "hellhound_5",
        "hellhound_6",
        "hellhound_7",
    ]
    assert [template.id for template in catalog.family("hydra")] == [
        template.id for template in catalog.monsters if template.family_id == "hydra"
    ]


def test_family_of_one():
    catalog = load_monsters()
    assert catalog.family("goblin") == (catalog.get("goblin"),)


def test_family_refuses_an_unknown_id():
    with pytest.raises(ValueError, match="'veteran_9'"):
        load_monsters().family("veteran_9")
    # A template id that isn't a family id is unknown too: hellhound_5's family is "hellhound".
    with pytest.raises(ValueError, match="'hellhound_5'"):
        load_monsters().family("hellhound_5")


def test_family_in_a_catalog_of_your_own():
    pup = custom_template(id="barrow_hound_pup", name="Barrow Hound (Pup)", family_id="barrow_hound")
    elder = custom_template(id="barrow_hound_elder", name="Barrow Hound (Elder)", family_id="barrow_hound")
    stray = custom_template(id="stray", name="Stray")
    catalog = MonsterCatalog(monsters=(pup, stray, elder))
    assert catalog.family("barrow_hound") == (pup, elder)
    assert catalog.family("stray") == (stray,)


# ---------------------------------------------------------------------- a template you write


def test_a_template_without_the_fields_gets_its_name_and_id():
    template = custom_template(id="bog_witch", name="Bog Witch")
    assert template.display_name == "Bog Witch"
    assert template.display_name_plural == "Bog Witches"
    assert template.family_id == "bog_witch"


@pytest.mark.parametrize(
    ("name", "plural"),
    [
        ("Ash Wraith", "Ash Wraiths"),
        ("Grey Fox", "Grey Foxes"),
        ("Glass Moss", "Glass Mosses"),
        ("Thornbush", "Thornbushes"),
        ("Fizz", "Fizzes"),
        ("Marsh Fly", "Marsh Flies"),
        ("Bone Boy", "Bone Boys"),
        ("Gully Dwarf", "Gully Dwarfs"),
        ("Draco", "Dracos"),
    ],
)
def test_the_regular_ending(name, plural):
    assert custom_template(id="custom", name=name).display_name_plural == plural


def test_a_plural_follows_the_display_name_you_set():
    template = custom_template(id="marsh_light", name="Light, Marsh", display_name="marsh light")
    assert template.display_name == "marsh light"
    assert template.display_name_plural == "marsh lights"


def test_the_fields_you_set_are_kept():
    template = custom_template(
        id="gully_dwarf_2",
        name="Gully Dwarf (2)",
        display_name="gully dwarf",
        display_name_plural="gully dwarves",
        family_id="gully_dwarf",
    )
    assert template.display_name == "gully dwarf"
    assert template.display_name_plural == "gully dwarves"
    assert template.family_id == "gully_dwarf"


def test_a_template_round_trips_through_its_document():
    for template in (load_monsters().get("normal_wolf"), custom_template(id="bog_witch", name="Bog Witch")):
        assert MonsterTemplate.model_validate(template.model_dump(mode="json")) == template
