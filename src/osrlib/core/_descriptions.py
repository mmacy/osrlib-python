"""The descriptions in the JSON Schema of an osrlib model: each field's docstring, as plain Markdown.

osrlib documents a field with a string on the line after it, and pydantic doesn't read that string. Each time
pydantic builds the JSON Schema of a model, `_Model.__get_pydantic_json_schema__` calls `_describe`, which gives each
property the docstring of its field. To read the docstrings, `_describe` parses the source of the model's module. It
parses each module once, the first time a schema needs it, so importing osrlib parses nothing.

A description reaches its reader as raw text, outside the documentation site, so `_describe` reduces each mkdocstrings
cross-reference, like ``[`MoveParty`][osrlib.crawl.commands.MoveParty]``, to its label, `` `MoveParty` ``. Markdown
reads nothing inside code as a link, so brackets in code, like `inventory["purse"]["gp"]`, stay as they are.
"""

import ast
import inspect
import re
import sys
from collections.abc import Iterator
from functools import cache
from itertools import pairwise
from typing import Any

from pydantic import BaseModel, GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue

# A fenced code block or a span in two or more backticks.
_LITERAL = re.compile(r"(```.*?```|~~~.*?~~~|``.+?``)", re.DOTALL)

# A span in single backticks.
_CODE_SPAN = re.compile(r"(`[^`\n]+`)")

# A cross-reference whose label is a code span, like [`MoveParty`][osrlib.crawl.commands.MoveParty].
_CODE_REFERENCE = re.compile(r"\[(`[^`\n]+`)\]\[[^\]\s]+\]")

# A cross-reference whose label is text, like [the rejection code reference][rejection-codes].
_TEXT_REFERENCE = re.compile(r"\[([^\[\]`]+)\]\[[^\]\s]+\]")


def _plain(text: str) -> str:
    """Return Markdown with each mkdocstrings cross-reference reduced to its label, and code left as it is."""
    if "][" not in text:
        return text
    parts = _LITERAL.split(text)
    for index in range(0, len(parts), 2):
        spans = _CODE_SPAN.split(_CODE_REFERENCE.sub(r"\1", parts[index]))
        spans[::2] = [_TEXT_REFERENCE.sub(r"\1", span) for span in spans[::2]]
        parts[index] = "".join(spans)
    return "".join(parts)


@cache
def _module_docstrings(module_name: str) -> dict[str, dict[str, str]]:
    """Map the qualified name of each class in a module to the plain docstrings of the fields it declares.

    A module whose source can't be read, like one installed as bytecode alone, maps to no classes.
    """
    try:
        tree = ast.parse(inspect.getsource(sys.modules[module_name]))
    except KeyError, OSError, TypeError, SyntaxError:
        return {}
    classes: dict[str, dict[str, str]] = {}

    def collect(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                classes[prefix + node.name] = {
                    statement.target.id: _plain(inspect.cleandoc(following.value.value))
                    for statement, following in pairwise(node.body)
                    if isinstance(statement, ast.AnnAssign)
                    and isinstance(statement.target, ast.Name)
                    and isinstance(following, ast.Expr)
                    and isinstance(following.value, ast.Constant)
                    and isinstance(following.value.value, str)
                }
                collect(node.body, f"{prefix}{node.name}.")

    collect(tree.body, "")
    return classes


def _field_docstrings(cls: type[BaseModel]) -> dict[str, str]:
    """Return the plain docstring of each field, from the class or from the nearest ancestor that documents it."""
    docstrings: dict[str, str] = {}
    for ancestor in reversed(cls.__mro__):
        if ancestor is not BaseModel and issubclass(ancestor, BaseModel):
            docstrings.update(_module_docstrings(ancestor.__module__).get(ancestor.__qualname__, {}))
    return docstrings


def _references(value: Any) -> Iterator[str]:
    """Yield each `$ref` in a JSON Schema, at any depth."""
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if isinstance(reference := item.get("$ref"), str):
                yield reference
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


def _reduce(schema: dict[str, Any]) -> None:
    """Reduce the cross-references in a schema's own description."""
    if isinstance(description := schema.get("description"), str):
        schema["description"] = _plain(description)


def _describe(cls: type[BaseModel], json_schema: JsonSchemaValue, handler: GetJsonSchemaHandler) -> None:
    """Give a model's JSON Schema the docstring of each field, and reduce the cross-references in its descriptions.

    A field whose `Field(description=...)` is set keeps that description. This also reduces the descriptions of the
    definitions that the fields refer to, directly or through another definition, so an enum's docstring reaches the
    schema as plain text.

    Args:
        cls: The model.
        json_schema: The model's JSON Schema, or a reference to it, which this changes in place.
        handler: The handler pydantic passed to the model's `__get_pydantic_json_schema__`, which resolves a
            reference to its definition.
    """
    schema = handler.resolve_ref_schema(json_schema)
    _reduce(schema)
    fields: dict[str, str] = {}
    for name, info in cls.model_fields.items():
        for key in (name, info.alias, info.validation_alias, info.serialization_alias):
            if isinstance(key, str):
                fields[key] = name
    docstrings = _field_docstrings(cls)
    properties: dict[str, Any] = schema.get("properties", {})
    for key, prop in properties.items():
        name = fields.get(key)
        if name is not None and cls.model_fields[name].description is None and name in docstrings:
            prop["description"] = docstrings[name]
        else:
            _reduce(prop)
    _reduce_definitions(properties, handler, set())


def _reduce_definitions(value: Any, handler: GetJsonSchemaHandler, seen: set[str]) -> None:
    """Reduce the description of each definition that a JSON Schema refers to, directly or through another one."""
    for reference in _references(value):
        if reference in seen:
            continue
        seen.add(reference)
        try:
            definition = handler.resolve_ref_schema({"$ref": reference})
        except LookupError:
            # Pydantic hasn't built this definition yet. A model whose schema pydantic builds later, like the root
            # of the schema, reaches the definition again.
            continue
        _reduce(definition)
        _reduce_definitions(definition, handler, seen)
