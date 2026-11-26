"""A model's JSON Schema describes every field with the docstring the source gives it.

osrlib documents a field with a string on the line after it. A program that reads the JSON Schema, like a
web framework that publishes an OpenAPI document or an agent framework that builds a tool definition, shows
its reader only what the schema contains. These tests hold the schema to three rules:

- Every property of every model has a `description`.
- The description is the field's docstring. A field that a subclass declares again with no docstring, like
  `command_type` on each command, has the docstring of the nearest ancestor that documents it.
- No description contains a mkdocstrings cross-reference, like ``[`MoveParty`][osrlib.crawl.commands.MoveParty]``.
  Only the documentation site resolves one, so the schema has the label alone: `` `MoveParty` ``. Brackets in
  code, like `inventory["purse"]["gp"]` in an example, aren't a cross-reference, and the schema has them as
  the docstring does. A label that is code and a code span that touches it become one code span.

The tests read each docstring from the source with `ast`, on their own, so they also catch a description
that comes from the wrong class.
"""

import ast
import importlib
import inspect
import pkgutil
import re
from functools import cache
from itertools import pairwise
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter

import osrlib
from osrlib.crawl.adventure import Adventure
from osrlib.crawl.commands import AnyCommand
from osrlib.crawl.events import AnyEvent
from osrlib.crawl.views import PlayerView, RefereeView

# A fenced code block or a span in two or more backticks. Markdown reads nothing inside one as a link, so
# `inventory["purse"]["gp"]` in an example stays as it is.
_LITERAL = re.compile(r"(```.*?```|~~~.*?~~~|``.+?``)", re.DOTALL)

# A span in single backticks, or a cross-reference: a label in brackets, then a target in brackets. The label is
# text, code spans, or both, like [`MoveParty`][osrlib.crawl.commands.MoveParty] or
# [the rejection code reference][rejection-codes]. A span that starts first is matched first, so brackets inside
# code are never read as a cross-reference.
_SPAN_OR_REFERENCE = re.compile(r"(`[^`\n]+`)|\[((?:[^\[\]`]|`[^`\n]+`)+)\]\[[^\]\s]+\]")


# Marks each end of a label while `_without_references` works. No docstring contains it.
_EDGE = "\x00"

# The end of one code span and the start of the next, with only the edge of a reduced cross-reference between them.
_JOIN = re.compile(f"`{_EDGE}+`")


def _without_references(text: str) -> str:
    """Return Markdown with each mkdocstrings cross-reference reduced to its label, and code left as it is.

    A label that is code can touch a code span, like ``[`load_classes`][osrlib.data.load_classes]`()` ``, which the
    documentation site shows as one run of code with a link on its first part. Reduced, the two become one code
    span, `` `load_classes()` ``.
    """

    def reduce(match: re.Match[str]) -> str:
        return match.group(1) or f"{_EDGE}{match.group(2)}{_EDGE}"

    parts = _LITERAL.split(text)
    parts[::2] = [_JOIN.sub("", _SPAN_OR_REFERENCE.sub(reduce, part)).replace(_EDGE, "") for part in parts[::2]]
    return "".join(parts)


def _plain(docstring: str) -> str:
    """Return a docstring as the schema should have it."""
    return _without_references(inspect.cleandoc(docstring))


def _words(text: str) -> str:
    return " ".join(text.split())


def _models() -> list[type[BaseModel]]:
    for module in pkgutil.walk_packages(osrlib.__path__, "osrlib."):
        importlib.import_module(module.name)
    found: dict[type[BaseModel], None] = {}
    pending = list(BaseModel.__subclasses__())
    while pending:
        cls = pending.pop()
        pending.extend(cls.__subclasses__())
        # A parametrized generic, like `Page[int]`, has the fields of the generic it came from.
        if cls.__module__.startswith("osrlib.") and cls.__pydantic_generic_metadata__["origin"] is None:
            found[cls] = None
    return sorted(found, key=lambda cls: (cls.__module__, cls.__qualname__))


_MODELS = _models()


@cache
def _module_classes(module_name: str) -> dict[str, ast.ClassDef]:
    module = importlib.import_module(module_name)
    classes: dict[str, ast.ClassDef] = {}

    def collect(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                classes[prefix + node.name] = node
                collect(node.body, f"{prefix}{node.name}.")

    collect(ast.parse(inspect.getsource(module)).body, "")
    return classes


def _own_docstrings(cls: type) -> dict[str, str]:
    """Return the docstring of each field that a class declares and documents, read from its source."""
    node = _module_classes(cls.__module__).get(cls.__qualname__)
    if node is None:
        return {}
    documented: dict[str, str] = {}
    for statement, following in pairwise(node.body):
        if (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and isinstance(following, ast.Expr)
            and isinstance(following.value, ast.Constant)
            and isinstance(following.value.value, str)
        ):
            documented[statement.target.id] = following.value.value
    return documented


def _docstring(cls: type[BaseModel], field: str) -> str | None:
    """Return a field's docstring, from the class or from the nearest ancestor that documents the field."""
    for ancestor in cls.__mro__:
        if ancestor.__module__.startswith("osrlib."):
            documented = _own_docstrings(ancestor)
            if field in documented:
                return documented[field]
    return None


def _property_names(cls: type[BaseModel]) -> dict[str, str]:
    """Map each field's name in the JSON Schema to its name in the class."""
    return {info.alias or name: name for name, info in cls.model_fields.items()}


@pytest.mark.parametrize("cls", _MODELS, ids=lambda cls: f"{cls.__module__}.{cls.__qualname__}")
def test_each_property_has_its_docstring_as_its_description(cls: type[BaseModel]) -> None:
    properties = cls.model_json_schema().get("properties", {})
    names = _property_names(cls)
    assert set(properties) == set(names), "the schema lists the model's fields"
    for property_name, schema in properties.items():
        field = names[property_name]
        docstring = _docstring(cls, field)
        assert docstring is not None, f"{cls.__qualname__}.{field} has no docstring, on the class or on an ancestor"
        description = schema.get("description")
        assert description, f"{cls.__qualname__}.{field} has no description in the schema"
        assert _words(description) == _words(_plain(docstring)), f"{cls.__qualname__}.{field}"


def _descriptions(value: Any, path: str = "") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            # A model can have a field named `description`, and then the key is a property name, with a schema
            # as its value.
            if key == "description" and isinstance(item, str):
                found.append((path, item))
            else:
                found.extend(_descriptions(item, f"{path}/{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_descriptions(item, f"{path}/{index}"))
    return found


_ROOTS = {
    "AnyCommand": AnyCommand,
    "AnyEvent": AnyEvent,
    "PlayerView": PlayerView,
    "RefereeView": RefereeView,
    "Adventure": Adventure,
}


@pytest.mark.parametrize("mode", ["validation", "serialization"])
@pytest.mark.parametrize("root", _ROOTS)
def test_a_published_schema_describes_every_property_in_plain_text(root: str, mode: Any) -> None:
    schema = TypeAdapter(_ROOTS[root]).json_schema(mode=mode)
    definitions = schema.get("$defs", {})
    assert definitions, "the schema defines the models it refers to"
    undescribed = [
        f"{name}.{property_name}"
        for name, definition in definitions.items()
        for property_name, property_schema in definition.get("properties", {}).items()
        if not property_schema.get("description")
    ]
    assert not undescribed, f"properties with no description: {undescribed[:20]}"
    with_links = [path for path, text in _descriptions(schema) if _without_references(text) != text]
    assert not with_links, f"descriptions with a cross-reference: {with_links[:20]}"


@pytest.mark.parametrize(
    ("markdown", "plain"),
    [
        ("Send [`MoveParty`][osrlib.crawl.commands.MoveParty] next.", "Send `MoveParty` next."),
        ("See [the rejection code reference][rejection-codes].", "See the rejection code reference."),
        ("Send [a `MoveParty` command][osrlib.crawl.commands.MoveParty].", "Send a `MoveParty` command."),
        ("Use [`North` or south][osrlib.crawl.dungeon.Direction] here.", "Use `North` or south here."),
        ("See [the rejection\ncode reference][rejection-codes].", "See the rejection\ncode reference."),
        ("Call [`load_classes`][osrlib.data.load_classes]`().get(class_id)`.", "Call `load_classes().get(class_id)`."),
        ("Read `catalog.`[`spells`][osrlib.core.spells.SpellCatalog.spells].", "Read `catalog.spells`."),
        ("Pass [`North`][package.North][`South`][package.South].", "Pass `NorthSouth`."),
        ("Use [`load_spells`][osrlib.data.load_spells] `()` twice.", "Use `load_spells` `()` twice."),
        ("Send [a `MoveParty`][package.MoveParty]`(direction)`.", "Send a `MoveParty(direction)`."),
        ("Both `a` and `b` stay two spans.", "Both `a` and `b` stay two spans."),
        ('Read `inventory["purse"]["gp"]` for the gold.', 'Read `inventory["purse"]["gp"]` for the gold.'),
        ("An id matches `[a-z][a-z0-9_]*`.", "An id matches `[a-z][a-z0-9_]*`."),
        (
            "```python\nfirst = [e.code for e in events][:2]\n```",
            "```python\nfirst = [e.code for e in events][:2]\n```",
        ),
        ("Write ``[`Name`][package.Name]`` to link.", "Write ``[`Name`][package.Name]`` to link."),
    ],
)
def test_the_rule_for_a_cross_reference(markdown: str, plain: str) -> None:
    # The rule that the other tests check each description against.
    assert _without_references(markdown) == plain


def test_a_class_docstring_keeps_the_brackets_in_its_code() -> None:
    schema = TypeAdapter(RefereeView).json_schema()
    assert 'rebinding `view.flags` raises while `view.flags["key"] = 1`' in schema["description"]


def test_a_redeclared_field_has_its_ancestors_docstring() -> None:
    from osrlib.crawl.commands import Command, MoveParty

    inherited = _own_docstrings(Command)["command_type"]
    assert "command_type" not in _own_docstrings(MoveParty)
    schema = MoveParty.model_json_schema()
    assert _words(schema["properties"]["command_type"]["description"]) == _words(_plain(inherited))
    assert schema["properties"]["command_type"]["const"] == "move_party"
