"""The descriptions in the JSON Schema of an osrlib model: each field's docstring, as plain Markdown.

osrlib documents a field with a string on the line after it. osrlib's models don't turn on pydantic's
`use_attribute_docstrings`, so pydantic doesn't read that string. Each time pydantic builds the JSON Schema of a model,
`_Model.__get_pydantic_json_schema__` calls `_describe`, which gives each property without a `Field(description=...)`
the docstring of its field. To read the docstrings, `_describe` parses the source of the modules that define the
model and its base classes. It parses a module only when pydantic builds a schema, and only once, so importing osrlib
parses nothing.

Pydantic writes an enum's docstring as the description of the enum's schema. Every osrlib enum subclasses `_StrEnum`,
whose `__get_pydantic_json_schema__` reduces the cross-references in that description each time pydantic builds the
enum's schema.

A description reaches its reader as raw text, outside the documentation site, so `_describe` and `_StrEnum` reduce each
mkdocstrings cross-reference, like ``[`MoveParty`][osrlib.crawl.commands.MoveParty]``, to its label, `` `MoveParty` ``.
Markdown doesn't treat anything inside code as a link, so brackets in code, like `inventory["purse"]["gp"]`, stay as
they are.
"""

import ast
import inspect
import re
import sys
from enum import StrEnum
from functools import cache
from itertools import pairwise
from typing import Any

from pydantic import AliasChoices, BaseModel, GetJsonSchemaHandler
from pydantic.fields import FieldInfo
from pydantic.json_schema import JsonSchemaMode, JsonSchemaValue
from pydantic_core import CoreSchema

# A fenced code block or a span in two or more backticks.
_LITERAL = re.compile(r"(```.*?```|~~~.*?~~~|``.+?``)", re.DOTALL)

# A span in single backticks, or a cross-reference: a label in brackets, then a target in brackets. The label is text,
# code spans, or both, like [`MoveParty`][osrlib.crawl.commands.MoveParty], [the rejection code
# reference][rejection-codes], or [a `MoveParty` command][osrlib.crawl.commands.MoveParty]. The expression matches the
# span or the cross-reference that starts first, so it never reads brackets inside code as a cross-reference.
_SPAN_OR_REFERENCE = re.compile(r"(`[^`\n]+`)|\[((?:[^\[\]`]|`[^`\n]+`)+)\]\[[^\]\s]+\]")


def _plain(text: str) -> str:
    """Return Markdown with each mkdocstrings cross-reference reduced to its label, and code left as it is."""
    if "][" not in text:
        return text
    parts = _LITERAL.split(text)
    parts[::2] = [_SPAN_OR_REFERENCE.sub(lambda match: match.group(1) or match.group(2), part) for part in parts[::2]]
    return "".join(parts)


@cache
def _module_docstrings(module_name: str) -> dict[str, dict[str, str]]:
    """Map the qualified name of each class in a module to the plain docstrings of the fields it declares.

    For a module whose source can't be read, like one installed as bytecode alone, the mapping is empty. The cache lasts
    as long as the process, so after `importlib.reload` reloads a module, its schemas still have the docstrings from
    before the reload.
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


def _reduce(schema: dict[str, Any]) -> None:
    """Reduce the cross-references in a schema's top-level `description`."""
    if isinstance(description := schema.get("description"), str):
        schema["description"] = _plain(description)


def _property_name(name: str, info: FieldInfo, mode: JsonSchemaMode) -> str:
    """Return the name of a field's property in a schema that uses aliases, as pydantic chooses it.

    The alias for the mode is a name, an `AliasPath`, or an `AliasChoices`. For an `AliasChoices`, pydantic uses the
    first choice that's a path of one name, like `"flat"` in `AliasChoices(AliasPath("outer", "inner"), "flat")`. For
    an `AliasPath` alone, even a path of one name, and for an `AliasChoices` with no such choice, it uses the field's
    name.
    """
    alias = info.validation_alias if mode == "validation" else info.serialization_alias
    if alias is None:
        alias = info.alias
    if isinstance(alias, str):
        return alias
    if isinstance(alias, AliasChoices):
        for path in alias.convert_to_aliases():
            if len(path) == 1 and isinstance(path[0], str):
                return path[0]
    return name


def _describe(cls: type[BaseModel], json_schema: JsonSchemaValue, handler: GetJsonSchemaHandler) -> None:
    """Give a model's JSON Schema the docstring of each field, and reduce the cross-references in its descriptions.

    A field declared with `Field(description=...)` has that description in the schema instead, with its cross-references
    reduced. A property can be named for the field or for its alias, so the schema is described both when it uses
    aliases and when it doesn't.

    Args:
        cls: The model.
        json_schema: The model's JSON Schema, or a reference to it. `_describe` changes the schema in place.
        handler: The handler pydantic passed to the model's `__get_pydantic_json_schema__`, which resolves a
            reference to its definition.
    """
    schema = handler.resolve_ref_schema(json_schema)
    _reduce(schema)
    fields = {name: name for name in cls.model_fields}
    # A property named for a field's alias belongs to that field, even when another field has the same name.
    fields.update({_property_name(name, info, handler.mode): name for name, info in cls.model_fields.items()})
    docstrings = _field_docstrings(cls)
    properties: dict[str, Any] = schema.get("properties", {})
    for key, prop in properties.items():
        name = fields.get(key)
        if name is not None and cls.model_fields[name].description is None and name in docstrings:
            prop["description"] = docstrings[name]
        else:
            _reduce(prop)


class _StrEnum(StrEnum):
    """The base class of every osrlib enum. It reduces the cross-references in the description of the enum's schema."""

    @classmethod
    def __get_pydantic_json_schema__(cls, schema: CoreSchema, handler: GetJsonSchemaHandler, /) -> JsonSchemaValue:
        """Return the enum's JSON Schema, with each cross-reference in its description reduced to its label.

        Pydantic calls this each time it builds the enum's schema, after it writes the enum's docstring as the
        description. The call reads and changes only the enum's schema, so it doesn't depend on the order in which
        pydantic builds the definitions of a larger schema.
        """
        json_schema = handler(schema)
        _reduce(handler.resolve_ref_schema(json_schema))
        return json_schema
