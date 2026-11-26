"""Where a model's JSON Schema gets its descriptions, in the cases the acceptance tests don't cover.

`test_schema_descriptions.py` checks the schemas of the osrlib models against their source. These tests cover an
installation that has no source, a model defined outside osrlib, a nested model, fields with aliases, each kind of
cross-reference label, a code label that touches a code span in a model's description and in an enum's, an osrlib enum
under a schema root that isn't a model, the base class of every osrlib enum, the definitions in the schema of every
osrlib model, and the OpenAPI document of a FastAPI application. They check descriptions against the cross-reference
rule of `test_schema_descriptions.py`.
"""

import compileall
import inspect
import json
import re
import shutil
import subprocess
import sys
from enum import Enum
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import AliasChoices, AliasPath, Field, TypeAdapter

from osrlib.core._descriptions import _StrEnum
from osrlib.core.frozen import _Model
from osrlib.crawl.commands import AnyCommand, MoveParty
from osrlib.crawl.dungeon import Direction
from osrlib.crawl.views import PlayerView
from test_schema_descriptions import _MODELS, _descriptions, _own_docstrings, _without_references

SOURCE = Path(__file__).resolve().parent.parent / "src" / "osrlib"

_WITHOUT_SOURCE = """
import json
from pydantic import TypeAdapter
import osrlib
from osrlib.crawl.commands import AnyCommand, MoveParty

schema = TypeAdapter(AnyCommand).json_schema()
print(json.dumps({
    "file": osrlib.__file__,
    "described": [
        f"{name}.{key}"
        for name, definition in schema["$defs"].items()
        for key, prop in definition.get("properties", {}).items()
        if "description" in prop
    ],
    "description": MoveParty.model_json_schema()["description"],
}))
"""


def test_a_schema_from_an_installation_without_source_has_no_field_descriptions(tmp_path: Path) -> None:
    package = tmp_path / "osrlib"
    shutil.copytree(SOURCE, package, ignore=shutil.ignore_patterns("__pycache__"))
    assert compileall.compile_dir(package, legacy=True, quiet=1)
    for source in package.rglob("*.py"):
        source.unlink()

    result = subprocess.run(
        [sys.executable, "-c", _WITHOUT_SOURCE], cwd=tmp_path, capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr
    found = json.loads(result.stdout)
    assert found["file"] == str(package / "__init__.pyc")
    assert found["described"] == []
    # The class docstring is in the bytecode, so the model has the description it has with the source.
    assert found["description"] == MoveParty.model_json_schema()["description"]


class Lantern(_Model):
    """A lantern, like the one in [`load_equipment`][osrlib.data.load_equipment]."""

    fuel: int
    """Flasks of oil, each an [`ItemTemplate`][osrlib.core.items.ItemTemplate] with `[a-z]` in its id."""
    colour: str = Field(default="yellow", description="The colour of the flame.")
    """A docstring that `Field(description=...)` takes the place of."""
    lit: bool = False

    class Wick(_Model):
        """A wick, which has a class of the same name below."""

        length: int
        """The wick's length in inches."""


class Wick(_Model):
    """A model with the name of a nested one."""

    length: int
    """The length of something else."""


def test_a_model_defined_outside_osrlib_has_its_docstrings() -> None:
    schema = Lantern.model_json_schema()

    assert schema["description"] == "A lantern, like the one in `load_equipment`."
    assert schema["properties"]["fuel"]["description"] == (
        "Flasks of oil, each an `ItemTemplate` with `[a-z]` in its id."
    )
    assert schema["properties"]["colour"]["description"] == "The colour of the flame."
    assert "description" not in schema["properties"]["lit"]


def test_a_nested_model_has_its_own_docstrings() -> None:
    assert Lantern.Wick.model_json_schema()["properties"]["length"]["description"] == "The wick's length in inches."
    assert Wick.model_json_schema()["properties"]["length"]["description"] == "The length of something else."


class Aliased(_Model):
    """A model whose fields have each kind of alias."""

    by_alias: int = Field(alias="byAlias")
    """A field with an `alias`."""
    by_validation_alias: int = Field(validation_alias="byValidationAlias")
    """A field with a `validation_alias` that's a name."""
    by_choices: int = Field(validation_alias=AliasChoices("why", "yy"))
    """A field with an `AliasChoices` whose first choice is a name."""
    by_choices_of_paths: int = Field(validation_alias=AliasChoices(AliasPath("outer", "inner"), "flat"))
    """A field with an `AliasChoices` whose first choice is a path."""
    by_path: int = Field(validation_alias=AliasPath("outer", 0))
    """A field with an `AliasPath` of two steps."""
    by_single_path: int = Field(validation_alias=AliasPath("single"))
    """A field with an `AliasPath` of one name."""
    by_serialization_alias: int = Field(serialization_alias="bySerializationAlias")
    """A field with a `serialization_alias`."""


# The property that pydantic emits for each field of `Aliased`, by mode, when the schema uses aliases.
_ALIASED_PROPERTIES = {
    "validation": {
        "byAlias": "by_alias",
        "byValidationAlias": "by_validation_alias",
        "why": "by_choices",
        "flat": "by_choices_of_paths",
        "by_path": "by_path",
        "by_single_path": "by_single_path",
        "by_serialization_alias": "by_serialization_alias",
    },
    "serialization": {
        "byAlias": "by_alias",
        "by_validation_alias": "by_validation_alias",
        "by_choices": "by_choices",
        "by_choices_of_paths": "by_choices_of_paths",
        "by_path": "by_path",
        "by_single_path": "by_single_path",
        "bySerializationAlias": "by_serialization_alias",
    },
}


@pytest.mark.parametrize("by_alias", [True, False])
@pytest.mark.parametrize("mode", ["validation", "serialization"])
def test_a_field_with_an_alias_has_its_docstring(mode: Any, by_alias: bool) -> None:
    properties = Aliased.model_json_schema(by_alias=by_alias, mode=mode)["properties"]
    expected = _ALIASED_PROPERTIES[mode] if by_alias else {name: name for name in Aliased.model_fields}
    docstrings = _own_docstrings(Aliased)

    assert set(properties) == set(expected)
    for property_name, field in expected.items():
        assert properties[property_name]["description"] == inspect.cleandoc(docstrings[field]), property_name


class Referenced(_Model):
    """Send [`MoveParty`][osrlib.crawl.commands.MoveParty] next. See [the rejection code reference][rejection-codes].
    Send [a `MoveParty` command][osrlib.crawl.commands.MoveParty].
    Use [`North` or south][osrlib.crawl.dungeon.Direction] here. See [the rejection
    code reference][rejection-codes]. Read `inventory["purse"]["gp"]` for the gold. An id matches `[a-z][a-z0-9_]*`.
    Write ``[`Name`][package.Name]`` to link.

    ```python
    first = [e.code for e in events][:2]
    ```
    """

    code_label: int
    """Send [`MoveParty`][osrlib.crawl.commands.MoveParty] next."""
    text_label: int
    """See [the rejection code reference][rejection-codes]."""
    text_then_code: int
    """Send [a `MoveParty` command][osrlib.crawl.commands.MoveParty]."""
    code_then_text: int
    """Use [`North` or south][osrlib.crawl.dungeon.Direction] here."""
    wrapped_label: int
    """See [the rejection
    code reference][rejection-codes]."""
    brackets_in_code: int
    """Read `inventory["purse"]["gp"]` for the gold."""
    pattern_in_code: int
    """An id matches `[a-z][a-z0-9_]*`."""
    fenced_code: int
    """```python
    first = [e.code for e in events][:2]
    ```"""
    reference_in_code: int
    """Write ``[`Name`][package.Name]`` to link."""


def test_each_kind_of_label_follows_the_rule_for_a_cross_reference() -> None:
    schema = Referenced.model_json_schema()
    docstrings = _own_docstrings(Referenced)

    assert set(docstrings) == set(Referenced.model_fields)
    assert schema["description"] == _without_references(inspect.cleandoc(Referenced.__doc__ or ""))
    for field, docstring in docstrings.items():
        assert schema["properties"][field]["description"] == _without_references(inspect.cleandoc(docstring)), field
    assert schema["properties"]["text_then_code"]["description"] == "Send a `MoveParty` command."
    assert schema["properties"]["code_then_text"]["description"] == "Use `North` or south here."
    assert "Send a `MoveParty` command.\nUse `North` or south here." in schema["description"]


class Touching(_Model):
    """Call [`load_classes`][osrlib.data.load_classes]`().get(class_id)`.
    Read `catalog.`[`spells`][osrlib.core.spells.SpellCatalog.spells].
    Pass [`North`][package.North][`South`][package.South].
    Use [`load_spells`][osrlib.data.load_spells] `()` twice.
    Send [a `MoveParty`][package.MoveParty]`(direction)`.
    Both `a` and `b` stay two spans.
    """

    call: int
    """Call [`load_classes`][osrlib.data.load_classes]`().get(class_id)`."""
    attribute: int
    """Read `catalog.`[`spells`][osrlib.core.spells.SpellCatalog.spells]."""


class TouchingKind(_StrEnum):
    """Call [`load_classes`][osrlib.data.load_classes]`().get(class_id)`.
    Read `catalog.`[`spells`][osrlib.core.spells.SpellCatalog.spells].
    Pass [`North`][package.North][`South`][package.South].
    Use [`load_spells`][osrlib.data.load_spells] `()` twice.
    Send [a `MoveParty`][package.MoveParty]`(direction)`.
    Both `a` and `b` stay two spans.
    """

    NEAR = "near"


class Touches(_Model):
    """A model with a field of the enum."""

    kind: TouchingKind
    """The kind."""


# What the docstrings of `Touching` and `TouchingKind` become in a description.
_TOUCHING = """Call `load_classes().get(class_id)`.
Read `catalog.spells`.
Pass `NorthSouth`.
Use `load_spells` `()` twice.
Send a `MoveParty(direction)`.
Both `a` and `b` stay two spans."""


def test_a_code_label_that_touches_a_code_span_joins_it_in_a_model_description() -> None:
    schema = Touching.model_json_schema()

    assert schema["description"] == _without_references(inspect.cleandoc(Touching.__doc__ or "")) == _TOUCHING
    assert schema["properties"]["call"]["description"] == "Call `load_classes().get(class_id)`."
    assert schema["properties"]["attribute"]["description"] == "Read `catalog.spells`."


def test_a_code_label_that_touches_a_code_span_joins_it_in_an_enum_description() -> None:
    description = Touches.model_json_schema()["$defs"]["TouchingKind"]["description"]

    assert description == _without_references(inspect.cleandoc(TouchingKind.__doc__ or "")) == _TOUCHING
    assert TypeAdapter(TouchingKind).json_schema()["description"] == _TOUCHING


# A span in single backticks with a double backtick inside it, between two characters that aren't spaces, like
# `load_classes``().get(class_id)`. That's what a code label and the code span it touches became before the rule
# joined them. A span in double backticks, like ``[`Name`][package.Name]``, doesn't match.
_JOINED_BY_A_LINK = re.compile(r"(?<!`)`[^`\s][^`\n]*?``(?=[^`\s])")

# A fenced code block, where a double backtick is code.
_FENCE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)


def _spans_joined_by_a_link(text: str) -> list[str]:
    """Return each code span that still has the double backtick a reduced link left in it."""
    return _JOINED_BY_A_LINK.findall(_FENCE.sub("", text))


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("Get one from `load_classes``().get(class_id)`.", ["`load_classes``"]),
        ("Read `catalog.``spells`.", ["`catalog.``"]),
        ("Get one from `load_classes().get(class_id)`.", []),
        ("Write ``[`Name`][package.Name]`` to link.", []),
        ("Write ``code``.", []),
        ("Both `a` and `b` stay two spans.", []),
        ("```python\nx = `a``b`\n```", []),
    ],
)
def test_the_check_for_a_span_joined_by_a_link(text: str, found: list[str]) -> None:
    assert _spans_joined_by_a_link(text) == found


@pytest.mark.parametrize("mode", ["validation", "serialization"])
def test_no_osrlib_schema_has_a_span_joined_by_a_link(mode: Any) -> None:
    offenders = {
        f"{cls.__module__}.{cls.__qualname__}{path}": found
        for cls in _MODELS
        for path, text in _descriptions(TypeAdapter(cls).json_schema(mode=mode))
        if (found := _spans_joined_by_a_link(text))
    }
    assert offenders == {}


class Heading(_Model):
    """Where a party faces and where it turns, two fields of the same enum."""

    facing: Direction
    """The direction the party faces."""
    turning: Direction
    """The direction the party turns to."""


def _route_heading() -> FastAPI:
    app = FastAPI()

    @app.post("/turn")
    def turn(heading: Heading) -> Heading:
        """Turn the party."""
        raise NotImplementedError

    return app


def _route_headings() -> FastAPI:
    app = FastAPI()

    @app.post("/turns")
    def turns(headings: tuple[Heading, Heading]) -> tuple[Heading, Heading]:
        """Turn two parties."""
        raise NotImplementedError

    return app


def _enum_descriptions(schemas: dict[str, Any], name: str) -> dict[str, str]:
    return {
        key: schema["description"]
        for key, schema in schemas.items()
        if key.removesuffix("-Input").removesuffix("-Output") == name
    }


@pytest.mark.parametrize("mode", ["validation", "serialization"])
def test_an_enum_under_a_root_that_isnt_a_model_is_plain(mode: Any) -> None:
    descriptions = _enum_descriptions(TypeAdapter(tuple[Heading, Heading]).json_schema(mode=mode)["$defs"], "Direction")

    assert descriptions
    assert {key: _without_references(text) for key, text in descriptions.items()} == descriptions


@pytest.mark.parametrize("build", [_route_heading, _route_headings])
def test_an_enum_in_an_openapi_document_is_plain(build: Any) -> None:
    descriptions = _enum_descriptions(build().openapi()["components"]["schemas"], "Direction")

    assert descriptions
    assert {key: _without_references(text) for key, text in descriptions.items()} == descriptions


def _with_references(schema: dict[str, Any]) -> list[str]:
    return [path for path, text in _descriptions(schema) if _without_references(text) != text]


@pytest.mark.parametrize("mode", ["validation", "serialization"])
def test_no_osrlib_schema_has_a_cross_reference_in_a_definition(mode: Any) -> None:
    offenders = {
        f"{cls.__module__}.{cls.__qualname__}": found
        for cls in _MODELS
        if (found := _with_references(TypeAdapter(cls).json_schema(mode=mode)))
    }
    assert offenders == {}


# The schemas FastAPI adds to every application that validates a request body.
_FASTAPI_SCHEMAS = {"HTTPValidationError", "ValidationError"}


def _osrlib_enums() -> set[type[Enum]]:
    return {
        value
        for name, module in sys.modules.items()
        if name.startswith("osrlib.")
        for value in vars(module).values()
        if isinstance(value, type)
        and issubclass(value, Enum)
        and value.__module__.startswith("osrlib.")
        and value is not _StrEnum
    }


def _osrlib_names() -> set[str]:
    return {enum.__name__ for enum in _osrlib_enums()} | {cls.__name__ for cls in _MODELS}


def test_every_osrlib_enum_reduces_its_own_description() -> None:
    enums = _osrlib_enums()

    assert enums
    assert sorted(enum.__qualname__ for enum in enums if not issubclass(enum, _StrEnum)) == []


def test_an_openapi_document_describes_every_osrlib_property() -> None:
    app = FastAPI()

    @app.post("/commands")
    def execute(command: AnyCommand) -> PlayerView:
        """Run a command and return what the players see."""
        raise NotImplementedError

    document = app.openapi()
    schemas: dict[str, Any] = document["components"]["schemas"]
    # Pydantic names a model's schema `Name-Input` or `Name-Output` when its validation and serialization schemas
    # differ.
    names = {key: key.removesuffix("-Input").removesuffix("-Output") for key in schemas}
    assert set(names.values()) - _osrlib_names() <= _FASTAPI_SCHEMAS
    undescribed = [
        f"{key}.{property_name}"
        for key, schema in schemas.items()
        if names[key] not in _FASTAPI_SCHEMAS
        for property_name, property_schema in schema.get("properties", {}).items()
        if not property_schema.get("description")
    ]
    assert len(schemas) > len(_FASTAPI_SCHEMAS)
    assert undescribed == []
    assert _with_references(document) == []
