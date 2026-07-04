"""Generate the event message-code table from the template registry and the event classes.

Runs under mkdocs-gen-files at build time. Every code in the formatter's `_TEMPLATES`
registry appears with its owning event class (the one whose `allowed_codes` declares it),
the event's default visibility, and the default English template — shown as the template's
Python source, extracted from the registry AST, so front-end authors see exactly what
ships before overriding it.
"""

import ast
import inspect
from pathlib import Path

import mkdocs_gen_files

import osrlib.messages
from osrlib.crawl.events import ALL_EVENT_CLASSES

_INTRO = """\
# Message codes

Every event carries a message code — dotted snake_case, namespaced by subsystem — and
[`format_message`][osrlib.messages.format_message] maps each code to a default English line.
The table lists all shipped codes: the event class that emits each one, that class's default
visibility, and the default template. Templates are code (each receives the typed event),
so the template column shows the registry source verbatim; the named helper functions it
references appear in full at the bottom of the page.

The visibility column is the event class's field default — there is no per-code visibility.
Specific emissions may override it: the referee's door-state command, for example, emits the
door open/close codes at referee visibility so scripted changes stay hidden until discovered.

A code with no template formats to the code string itself, and unknown codes never raise, so
logs from newer engine versions stay printable.
"""


def _template_sources() -> tuple[dict[str, tuple[str, set[str]]], dict[str, str]]:
    """Extract each registry entry's source and the module helpers it references."""
    source = Path(inspect.getsourcefile(osrlib.messages)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    helpers = {
        node.name: ast.get_source_segment(source, node)
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_")
    }
    registry = None
    for node in tree.body:
        targets = (
            node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "_TEMPLATES":
                registry = node.value
    if not isinstance(registry, ast.Dict):
        raise ValueError("could not locate the _TEMPLATES dict literal in osrlib.messages")

    entries: dict[str, tuple[str, set[str]]] = {}
    for key, value in zip(registry.keys, registry.values, strict=True):
        assert isinstance(key, ast.Constant)
        text = " ".join(ast.get_source_segment(source, value).split())
        used = {n.id for n in ast.walk(value) if isinstance(n, ast.Name) and n.id in helpers}
        entries[key.value] = (text, used)
    return entries, helpers


def _cell(code_text: str) -> str:
    return "`" + code_text.replace("|", "\\|") + "`"


entries, helpers = _template_sources()

owners = {}
for cls in ALL_EVENT_CLASSES:
    for code in cls.allowed_codes:
        owners[code] = cls

missing = set(entries) ^ set(owners)
if missing:
    raise ValueError(f"template registry and event allowed_codes disagree on: {sorted(missing)}")

used_helpers: set[str] = set()
namespaces: dict[str, list[str]] = {}
for code in sorted(entries):
    namespaces.setdefault(code.split(".")[0], []).append(code)

with mkdocs_gen_files.open("reference/message-codes.md", "w") as page:
    page.write(_INTRO)
    for namespace in sorted(namespaces):
        page.write(f"\n## `{namespace}.*`\n\n")
        page.write("| Code | Event | Default visibility | Default template |\n|---|---|---|---|\n")
        for code in namespaces[namespace]:
            cls = owners[code]
            text, used = entries[code]
            used_helpers |= used
            event_ref = f"[`{cls.__name__}`][{cls.__module__}.{cls.__name__}]"
            visibility = cls.model_fields["visibility"].default.value
            page.write(f"| `{code}` | {event_ref} | `{visibility}` | {_cell(text)} |\n")
    if used_helpers:
        page.write("\n## Template helpers\n\n")
        page.write("The named helpers referenced above, verbatim:\n\n")
        page.write("```python\n")
        page.write("\n\n\n".join(helpers[name] for name in sorted(used_helpers)))
        page.write("\n```\n")
