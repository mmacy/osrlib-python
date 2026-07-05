"""Generate the command and event schema reference from the registries themselves.

Runs under mkdocs-gen-files at build time. One page per command and event class —
sourced from `ALL_COMMAND_CLASSES` and `ALL_EVENT_CLASSES`, so a class added to a
registry appears here with no further wiring — plus the two raw artifacts,
`commands.json` and `events.json`, carrying the discriminated-union JSON Schemas an
agent framework or API consumer loads directly.
"""

import json

import mkdocs_gen_files
from pydantic import TypeAdapter

from osrlib.crawl.commands import ALL_COMMAND_CLASSES, AnyCommand
from osrlib.crawl.events import ALL_EVENT_CLASSES, KERNEL_EVENT_CLASSES, AnyEvent


def _crossref(cls: type) -> str:
    return f"[`{cls.__name__}`][{cls.__module__}.{cls.__name__}]"


def _schema_block(cls: type) -> str:
    schema = json.dumps(cls.model_json_schema(), indent=2)  # type: ignore[attr-defined]
    return f"```json\n{schema}\n```\n"


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
    artifact.write(json.dumps(TypeAdapter(AnyCommand).json_schema(), indent=2) + "\n")


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
    artifact.write(json.dumps(TypeAdapter(AnyEvent).json_schema(), indent=2) + "\n")
