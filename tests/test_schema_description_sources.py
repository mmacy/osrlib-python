"""Where a model's JSON Schema gets its descriptions, in the cases the acceptance tests don't cover.

`test_schema_descriptions.py` checks the schemas of the osrlib models against their source. These tests cover an
installation that has no source, a model defined outside osrlib, a nested model, an enum that pydantic reaches only
through a shared definition, the definitions in the schema of every osrlib model, and the OpenAPI document of a FastAPI
application. They check descriptions against the cross-reference rule of `test_schema_descriptions.py`.
"""

import compileall
import inspect
import json
import shutil
import subprocess
import sys
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import Field, TypeAdapter

from osrlib.core.frozen import _Model
from osrlib.crawl.commands import AnyCommand, MoveParty
from osrlib.crawl.views import PlayerView
from test_schema_descriptions import _MODELS, _descriptions, _without_references

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
    # The class docstring is in the bytecode, so the model still has its description.
    assert found["description"] == _without_references(inspect.cleandoc(MoveParty.__doc__ or ""))


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


class Shade(StrEnum):
    """A shade, like [`Lantern`][tests.test_schema_description_sources.Lantern] gives."""

    DIM = "dim"
    BRIGHT = "bright"


class Pair(_Model):
    """Two shades."""

    first: Shade
    """The first shade."""
    second: Shade
    """The second shade."""


class Pairs(_Model):
    """Two pairs, so the schema defines `Pair` once and refers to it twice."""

    left: Pair
    """The left pair."""
    right: Pair
    """The right pair."""


def test_an_enum_that_a_shared_definition_refers_to_is_plain() -> None:
    schema = Pairs.model_json_schema()

    assert schema["$defs"]["Shade"]["description"] == "A shade, like `Lantern` gives."
    assert schema["$defs"]["Pair"]["properties"]["first"]["description"] == "The first shade."


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


def _osrlib_names() -> set[str]:
    enums = {
        value.__name__
        for name, module in sys.modules.items()
        if name.startswith("osrlib.")
        for value in vars(module).values()
        if isinstance(value, type) and issubclass(value, Enum) and value.__module__.startswith("osrlib.")
    }
    return enums | {cls.__name__ for cls in _MODELS}


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
