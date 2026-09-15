"""The creature protocols: both concrete types satisfy them, and the kernel's signatures name them.

`osrlib.core.creature` declares what a rules function needs from the creature it acts on. The
conformance tests hold from the moment the module exists. The signature tests hold once the
kernel's creature parameters are annotated with a protocol, a concrete creature type, or a
sequence of one, in place of `Any` and `object`.
"""

import inspect
import typing

import pytest

from osrlib.core import combat, effects, items, spells
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.creature import Caster, Combatant, Creature
from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.data import load_monsters

CREATURE_PARAMETERS = frozenset(
    {
        "attacker",
        "defender",
        "target",
        "combatant",
        "caster",
        "reader",
        "cleric",
        "monster",
        "source",
        "gazer",
        "members",
        "candidates",
        "engaged",
        "targets",
        "character",
        "creature",
        "subject",
    }
)
"""Parameter names that hold a creature or a sequence of creatures in the four kernel modules."""


def hero(class_id: str = "fighter"):
    stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
    return create_character(
        name="Hild",
        class_id=class_id,
        alignment=Alignment.LAWFUL,
        ruleset=Ruleset(),
        stream=stream,
        starting_spell_ids=["sleep"] if class_id == "magic_user" else None,
    ).character


def goblin():
    stream = RngStreams(master_seed=3).get(MONSTER_SPAWN_STREAM)
    return spawn_monster(load_monsters().get("goblin"), id="monster-0001", stream=stream)


class TestBothConcreteTypesSatisfyTheProtocols:
    def test_a_character_is_a_creature_a_combatant_and_a_caster(self):
        character = hero("magic_user")
        assert isinstance(character, Creature)
        assert isinstance(character, Combatant)
        assert isinstance(character, Caster)

    def test_a_monster_instance_is_a_creature_and_a_combatant_but_not_a_caster(self):
        monster = goblin()
        assert isinstance(monster, Creature)
        assert isinstance(monster, Combatant)
        assert not isinstance(monster, Caster)

    def test_the_protocol_members_read_the_same_values_as_the_concrete_attributes(self):
        monster = goblin()
        creature: Creature = monster
        combatant: Combatant = monster
        assert (creature.id, creature.name, creature.current_hp, creature.max_hp) == (
            monster.id,
            monster.name,
            monster.current_hp,
            monster.max_hp,
        )
        assert (combatant.thac0, combatant.armour_class, combatant.saves) == (
            monster.thac0,
            monster.armour_class,
            monster.saves,
        )
        creature.current_hp = 1
        assert monster.current_hp == 1

    def test_something_without_the_surface_is_refused(self):
        class Rock:
            name = "rock"

        assert not isinstance(Rock(), Creature)


def _public_functions(module):
    return [
        member
        for name, member in inspect.getmembers(module, inspect.isfunction)
        if not name.startswith("_") and member.__module__ == module.__name__
    ]


def _creature_parameters():
    found = []
    for module in (combat, spells, effects, items):
        for function in _public_functions(module):
            for parameter in inspect.signature(function).parameters.values():
                if parameter.name in CREATURE_PARAMETERS:
                    found.append((module.__name__, function.__name__, parameter))
    return found


def _mentions_a_duck(annotation) -> bool:
    text = annotation if isinstance(annotation, str) else repr(annotation)
    return annotation is inspect.Parameter.empty or annotation is typing.Any or "Any" in text or "object" in text


class TestTheKernelSignaturesNameTheProtocols:
    @pytest.mark.xfail(reason="chunk: kernel-protocols")
    def test_no_public_creature_parameter_is_any_or_object(self):
        ducks = [
            f"{module}.{function}({parameter.name}: {parameter.annotation})"
            for module, function, parameter in _creature_parameters()
            if _mentions_a_duck(parameter.annotation)
        ]
        assert ducks == [], "\n".join(ducks)

    @pytest.mark.xfail(reason="chunk: kernel-protocols")
    def test_the_flagship_functions_take_a_combatant_or_a_caster(self):
        assert inspect.signature(combat.attack_roll).parameters["attacker"].annotation is Combatant
        assert inspect.signature(combat.attack_roll).parameters["defender"].annotation is Combatant
        assert inspect.signature(combat.saving_throw).parameters["target"].annotation is Combatant
        assert inspect.signature(effects.has_condition).parameters["target"].annotation is Creature
        assert inspect.signature(spells.cast_spell).parameters["caster"].annotation is Caster
        assert inspect.signature(spells.validate_cast).parameters["caster"].annotation is Caster

    def test_the_census_finds_the_parameters_it_is_meant_to(self):
        names = {(module, function) for module, function, _ in _creature_parameters()}
        assert ("osrlib.core.combat", "attack_roll") in names
        assert ("osrlib.core.spells", "cast_spell") in names
        assert ("osrlib.core.effects", "has_condition") in names
        assert ("osrlib.core.items", "sword_control_check") in names
