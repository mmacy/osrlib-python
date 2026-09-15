"""Generate the command and event schema reference from the registries themselves.

Runs under mkdocs-gen-files at build time. One page per command and event class —
sourced from `ALL_COMMAND_CLASSES` and `ALL_EVENT_CLASSES`, so a class added to a
registry appears here with no further wiring — plus the two raw artifacts,
`commands.json` and `events.json`, carrying the discriminated-union JSON Schemas an
agent framework or API consumer loads directly.

A JSON Schema block is text inside a fenced code block, so mkdocstrings cross-reference
syntax in a class docstring never resolves there the way it does on a rendered page, and
a `description` reaches a tool-calling LLM as plain text either way. Every description
this module writes into a schema, the model's own and each property's, goes through
`_plain_prose` first. A field's description comes from its attribute docstring (the PEP
224 form: a string literal statement right after the field's annotated assignment),
which pydantic never reads on its own, so `_field_description` recovers it with `ast`
over the module source that defines the class owning the field. A field a subclass
redeclares without repeating its own docstring, or never redeclares at all, such as
`command_type` on every command and `source` on every command that isn't `Command`
itself, takes the docstring the nearest ancestor in the MRO gives it.
"""

import ast
import inspect
import json
import re
from enum import Enum
from pathlib import Path
from typing import get_args, get_origin

import mkdocs_gen_files
from pydantic import BaseModel, TypeAdapter

from osrlib.crawl.commands import ALL_COMMAND_CLASSES, AnyCommand
from osrlib.crawl.events import ALL_EVENT_CLASSES, KERNEL_EVENT_CLASSES, AnyEvent


def _crossref(cls: type) -> str:
    return f"[`{cls.__name__}`][{cls.__module__}.{cls.__name__}]"


# Matches a mkdocstrings cross-reference, backticked or not: `` [`Name`][mod.path.Name] ``
# or `[Name][mod.path.Name]`. The backreference keeps the backticks (or their absence) on
# the replacement, so `` [`Name`][...] `` becomes `` `Name` `` and `[Name][...]` becomes
# plain `Name`.
_CROSSREF = re.compile(r"\[(`?)([^\[\]]+?)\1\]\[[A-Za-z_][\w.]*\]")


def _plain_prose(text: str) -> str:
    """Reduce mkdocstrings cross-reference syntax to the name alone.

    A description that reaches an LLM as a tool definition, or a reader as raw JSON, never
    resolves that syntax into a link, so the markup itself is noise the reader has to see past.
    """
    return _CROSSREF.sub(r"\1\2\1", text)


def _module_field_docstrings(filename: str) -> dict[str, dict[str, str]]:
    """Map every class in one source file to its own `{field name: attribute docstring}`.

    Reads and parses the file once; later calls for the same file reuse the cached result
    for the rest of the build.
    """
    cached = _FIELD_DOC_CACHE.get(filename)
    if cached is not None:
        return cached
    tree = ast.parse(Path(filename).read_text(encoding="utf-8"), filename=filename)
    by_class: dict[str, dict[str, str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        fields: dict[str, str] = {}
        body = node.body
        for index, statement in enumerate(body):
            if not (isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)):
                continue
            following = body[index + 1] if index + 1 < len(body) else None
            if (
                isinstance(following, ast.Expr)
                and isinstance(following.value, ast.Constant)
                and isinstance(following.value.value, str)
            ):
                fields[statement.target.id] = inspect.cleandoc(following.value.value)
        by_class[node.name] = fields
    _FIELD_DOC_CACHE[filename] = by_class
    return by_class


_FIELD_DOC_CACHE: dict[str, dict[str, dict[str, str]]] = {}


def _field_description(cls: type, field_name: str) -> str:
    """The prose for one property, from the nearest ancestor that documents it."""
    for klass in cls.__mro__:
        if klass is BaseModel or not issubclass(klass, BaseModel):
            continue
        doc = _module_field_docstrings(inspect.getsourcefile(klass)).get(klass.__name__, {}).get(field_name)
        if doc:
            return _plain_prose(doc)
    return ""


def _referenced_models(cls: type, registry: dict[str, type]) -> None:
    """Collect every `BaseModel` and `Enum` type reachable from `cls`'s own fields, recursively.

    This is how a `$defs` entry in a discriminated-union schema (`commands.json`,
    `events.json`) gets mapped back to the class that defines it: pydantic names each entry
    after the class's own `__name__`, and this walk visits every class that can appear there.
    """
    if not isinstance(cls, type) or cls.__name__ in registry:
        return
    if issubclass(cls, BaseModel) or issubclass(cls, Enum):
        registry[cls.__name__] = cls
    if not issubclass(cls, BaseModel):
        return
    for field in cls.model_fields.values():
        for inner in _unwrap(field.annotation):
            _referenced_models(inner, registry)


def _unwrap(annotation: object) -> list[type]:
    """Flatten a field annotation down to the concrete types nested inside it.

    Handles the generic shapes the command and event fields use, such as `X | None`,
    `tuple[X, ...]`, and `list[X]`, without needing to special-case any of them.
    """
    origin = get_origin(annotation)
    if origin is None:
        return [annotation] if isinstance(annotation, type) else []
    found = []
    for arg in get_args(annotation):
        found.extend(_unwrap(arg))
    return found


def _describe_schema(schema: dict, registry: dict[str, type], cls: type | None = None) -> dict:
    """Rewrite every description a JSON Schema carries into plain, field-sourced prose.

    Covers the schema's own top level (when `cls` names the single class it describes) and
    every `$defs` entry (a discriminated union's variants, and any nested model or enum they
    reference), so this handles both a single command's or event's own schema and the combined
    `commands.json` / `events.json` artifacts with one function.
    """
    if cls is not None:
        if "description" in schema:
            schema["description"] = _plain_prose(schema["description"])
        for field_name, prop in schema.get("properties", {}).items():
            prop["description"] = _field_description(cls, field_name)
    for name, definition in schema.get("$defs", {}).items():
        if "description" in definition:
            definition["description"] = _plain_prose(definition["description"])
        member = registry.get(name)
        if member is None:
            continue
        for field_name, prop in definition.get("properties", {}).items():
            prop["description"] = _field_description(member, field_name)
    return schema


# Every model and enum reachable from a command's or an event's own fields: the closure that
# can appear as a `$defs` entry in `commands.json` or `events.json`.
_MODEL_REGISTRY: dict[str, type] = {}
for _cls in (*ALL_COMMAND_CLASSES, *ALL_EVENT_CLASSES):
    _referenced_models(_cls, _MODEL_REGISTRY)


def _schema_block(cls: type) -> str:
    schema = _describe_schema(cls.model_json_schema(), _MODEL_REGISTRY, cls)  # type: ignore[attr-defined]
    return f"```json\n{json.dumps(schema, indent=2)}\n```\n"


def _summary_line(cls: type) -> str:
    doc = cls.__doc__ or ""
    return doc.strip().splitlines()[0] if doc.strip() else ""


# Commands: one page each, alphabetical.

command_lines = ["- [Overview](index.md)"]
for cls in sorted(ALL_COMMAND_CLASSES, key=lambda c: c.__name__):
    name = cls.__name__
    command_lines.append(f"- [{name}]({name}.md)")
    modes = ", ".join(f"`{mode.value}`" for mode in sorted(cls.allowed_modes))
    with mkdocs_gen_files.open(f"reference/commands/{name}.md", "w") as page:
        page.write(f"# {name}\n\n")
        page.write(f"{_summary_line(cls)}\n\n")
        page.write(f"Full documentation: {_crossref(cls)}. ")
        page.write(f"Wire type: `{cls.model_fields['command_type'].default}`.\n\n")
        page.write(f"**Legal session modes:** {modes}\n\n")
        page.write("## JSON Schema\n\n")
        page.write(_schema_block(cls))

with mkdocs_gen_files.open("reference/commands/index.md", "w") as index:
    index.write("# Command schemas\n\n")
    index.write(
        "One page per command in the engine's registry, each carrying the model's JSON Schema "
        "and the session modes that accept it. The complete command surface as a single "
        "discriminated union (keyed on `command_type`) is downloadable as "
        "[commands.json](commands.json) — load it as a tool definition or validate requests "
        "against it without scraping these pages.\n\n"
    )
    index.write("| Command | Wire type | Legal modes |\n|---|---|---|\n")
    for cls in sorted(ALL_COMMAND_CLASSES, key=lambda c: c.__name__):
        wire = cls.model_fields["command_type"].default
        modes = ", ".join(f"`{mode.value}`" for mode in sorted(cls.allowed_modes))
        index.write(f"| [{cls.__name__}]({cls.__name__}.md) | `{wire}` | {modes} |\n")

with mkdocs_gen_files.open("reference/commands/SUMMARY.md", "w") as summary:
    summary.write("\n".join(command_lines) + "\n")

with mkdocs_gen_files.open("reference/commands/commands.json", "w") as artifact:
    commands_schema = _describe_schema(TypeAdapter(AnyCommand).json_schema(), _MODEL_REGISTRY)
    artifact.write(json.dumps(commands_schema, indent=2) + "\n")


# Events: one page each, grouped kernel/crawl in the nav.

_KERNEL = set(KERNEL_EVENT_CLASSES)

event_lines = ["- [Overview](index.md)"]
for group_title, members in (
    ("Kernel events", [c for c in ALL_EVENT_CLASSES if c in _KERNEL]),
    ("Crawl events", [c for c in ALL_EVENT_CLASSES if c not in _KERNEL]),
):
    event_lines.append(f"- {group_title}")
    for cls in sorted(members, key=lambda c: c.__name__):
        name = cls.__name__
        event_lines.append(f"    - [{name}]({name}.md)")
        with mkdocs_gen_files.open(f"reference/events/{name}.md", "w") as page:
            page.write(f"# {name}\n\n")
            page.write(f"{_summary_line(cls)}\n\n")
            page.write(f"Full documentation: {_crossref(cls)}. ")
            page.write(f"Wire type: `{cls.model_fields['event_type'].default}`.\n\n")
            page.write(f"**Default visibility:** `{cls.model_fields['visibility'].default.value}`\n\n")
            if cls.allowed_codes:
                codes = ", ".join(f"[`{code}`](../message-codes.md)" for code in sorted(cls.allowed_codes))
                page.write(f"**Message codes:** {codes}\n\n")
            page.write("## JSON Schema\n\n")
            page.write(_schema_block(cls))

with mkdocs_gen_files.open("reference/events/index.md", "w") as index:
    index.write("# Event schemas\n\n")
    index.write(
        "One page per event in the engine's registry — the kernel events the rules resolutions "
        "emit, and the crawl events the session framework adds — each carrying the model's JSON "
        "Schema, its default visibility, and its message codes. The complete event surface as a "
        "single discriminated union (keyed on `event_type`) is downloadable as "
        "[events.json](events.json).\n\n"
    )
    for group_title, members in (
        ("Kernel events", [c for c in ALL_EVENT_CLASSES if c in _KERNEL]),
        ("Crawl events", [c for c in ALL_EVENT_CLASSES if c not in _KERNEL]),
    ):
        index.write(f"## {group_title}\n\n")
        index.write("| Event | Wire type | Default visibility |\n|---|---|---|\n")
        for cls in sorted(members, key=lambda c: c.__name__):
            wire = cls.model_fields["event_type"].default
            visibility = cls.model_fields["visibility"].default.value
            index.write(f"| [{cls.__name__}]({cls.__name__}.md) | `{wire}` | `{visibility}` |\n")
        index.write("\n")

with mkdocs_gen_files.open("reference/events/SUMMARY.md", "w") as summary:
    summary.write("\n".join(event_lines) + "\n")

with mkdocs_gen_files.open("reference/events/events.json", "w") as artifact:
    events_schema = _describe_schema(TypeAdapter(AnyEvent).json_schema(), _MODEL_REGISTRY)
    artifact.write(json.dumps(events_schema, indent=2) + "\n")
