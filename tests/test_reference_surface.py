"""The public surface reads cleanly from outside (issues #161 and #166, items 39 and 40).

`inspect.signature` works on every public callable, so `help()` and the reference pages can show its
parameters. No public value renders a private name in the reference: the pages are built by griffe,
which prints a default or a constant as its source expression.
"""

import importlib
import inspect
import pkgutil
import re
import subprocess
import sys
import typing

import griffe
import pytest

import osrlib

PRIVATE_NAME = re.compile(r"(?<![\w.])_[A-Za-z]\w*")
"""An identifier that starts with one underscore, as a name of its own and not as an attribute."""


def public_modules() -> list[str]:
    names = ["osrlib"]
    for module in pkgutil.walk_packages(osrlib.__path__, prefix="osrlib."):
        if not any(part.startswith("_") for part in module.name.split(".")):
            names.append(module.name)
    return sorted(names)


def public_callables() -> list[tuple[str, object]]:
    """Every callable a module exports in `__all__`, and every public method of an exported class."""
    found: dict[str, object] = {}
    for module_name in public_modules():
        module = importlib.import_module(module_name)
        for name in getattr(module, "__all__", ()):
            exported = getattr(module, name)
            if inspect.isclass(exported):
                for attribute, value in vars(exported).items():
                    if attribute.startswith("_"):
                        continue
                    member = value.__func__ if isinstance(value, (classmethod, staticmethod)) else value
                    if inspect.isfunction(member) and member.__module__.startswith("osrlib"):
                        found[f"{exported.__module__}.{exported.__qualname__}.{attribute}"] = member
            elif inspect.isfunction(exported):
                found[f"{exported.__module__}.{exported.__qualname__}"] = exported
    return sorted(found.items())


CALLABLES = public_callables()

NEEDS_A_MOVE = "chunk: readable-signatures"
"""Why a signature still fails: an import cycle hides a type from the module that names it in an annotation."""

BLOCKED_BY_A_CYCLE = frozenset(
    {
        # osrlib.core.classes and osrlib.core.character
        "osrlib.core.classes.apply_xp",
        "osrlib.core.classes.detection_chance",
        "osrlib.core.classes.drain_levels",
        "osrlib.core.classes.level_up",
        "osrlib.core.classes.thief_skill_check",
        # osrlib.core.items and osrlib.core.character
        "osrlib.core.items.sword_control_check",
        # osrlib.core.items and osrlib.core.treasure
        "osrlib.core.treasure.generate_magic_item",
        "osrlib.core.treasure.generate_treasure",
        "osrlib.core.treasure.generate_treasure_entries",
        "osrlib.core.treasure.generate_unguarded_treasure",
        "osrlib.core.treasure.instantiate_magic_item",
        # osrlib.crawl.session and osrlib.crawl.views
        "osrlib.crawl.session.GameSession.view",
    }
)
"""The callables whose signatures can't be read while one of four import cycles hides a type from them.

A callable that isn't on the list has to work.
"""


def signature_cases() -> list:
    cases = []
    for name, function in CALLABLES:
        marks = [pytest.mark.xfail(reason=NEEDS_A_MOVE)] if name in BLOCKED_BY_A_CYCLE else []
        cases.append(pytest.param(name, function, id=name, marks=marks))
    return cases


def test_the_harness_finds_the_public_surface():
    names = [name for name, _ in CALLABLES]
    assert "osrlib.core.treasure.plan_treasure_ref" in names
    assert "osrlib.crawl.session.GameSession.view" in names
    assert len(names) > 200


def test_every_blocked_callable_is_still_public():
    assert BLOCKED_BY_A_CYCLE <= {name for name, _ in CALLABLES}


@pytest.mark.parametrize(("name", "function"), signature_cases())
def test_inspect_signature_works(name, function):
    signature = inspect.signature(function, eval_str=True)
    assert signature is not None
    assert typing.get_type_hints(function) is not None


CYCLE_MODULES = (
    "osrlib.core.character",
    "osrlib.core.classes",
    "osrlib.core.combat",
    "osrlib.core.items",
    "osrlib.core.spells",
    "osrlib.core.treasure",
    "osrlib.crawl.session",
    "osrlib.crawl.views",
)
"""The modules in the import cycles. Which one a program imports first decides the order the rest load in."""

READ_EVERY_SIGNATURE = """
import importlib, inspect, sys
importlib.import_module(sys.argv[1])
for path in sys.argv[2:]:
    module_name, _, rest = path.partition(":")
    member = importlib.import_module(module_name)
    for part in rest.split("."):
        member = getattr(member, part)
    inspect.signature(member, eval_str=True)
"""


@pytest.mark.xfail(reason=NEEDS_A_MOVE)
@pytest.mark.parametrize("first", CYCLE_MODULES)
def test_signatures_read_whichever_module_a_program_imports_first(first):
    paths = []
    for name in sorted(BLOCKED_BY_A_CYCLE | {"osrlib.core.combat.resolve_energy_drain"}):
        module_name = next(module for module in sorted(CYCLE_MODULES, key=len, reverse=True) if name.startswith(module))
        paths.append(f"{module_name}:{name[len(module_name) + 1 :]}")
    result = subprocess.run(
        [sys.executable, "-c", READ_EVERY_SIGNATURE, first, *paths], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def rendered_values() -> list[tuple[str, str]]:
    """The source expression griffe renders for every public attribute and every parameter default."""
    package = griffe.load("osrlib", search_paths=["src"])
    found: list[tuple[str, str]] = []

    def walk(node) -> None:
        for name, member in node.members.items():
            if name.startswith("_") or member.is_alias:
                continue
            if member.is_attribute and member.value is not None:
                found.append((member.path, str(member.value)))
            elif member.is_function:
                for parameter in member.parameters:
                    if parameter.default is not None:
                        found.append((f"{member.path}({parameter.name})", str(parameter.default)))
            if member.is_module or member.is_class:
                walk(member)

    walk(package)
    return found


def test_no_public_value_renders_a_private_name():
    offenders = [(path, value) for path, value in rendered_values() if PRIVATE_NAME.search(value)]
    assert offenders == []


@pytest.mark.parametrize(
    "path",
    [
        "osrlib.persistence.MIGRATIONS",
        "osrlib.core.rng.RngStreamState.state",
        "osrlib.crawl.commands.GiveItems.allowed_modes",
        "osrlib.crawl.commands.SpawnMonsters.allowed_modes",
    ],
)
def test_the_values_the_issues_name_are_checked(path):
    """Guards the walk above: it has to reach the values the issues reported."""
    assert path in [found for found, _ in rendered_values()]
