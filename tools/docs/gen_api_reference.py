"""Generate the API reference: one page per exporting module, rendering its `__all__` surface.

Runs under mkdocs-gen-files at build time. Page-per-module mirrors the one-home-per-symbol
import contract, and rendering exactly `__all__` (kept honest by the standing completeness
gate in the test suite) keeps the reference and the import surface identical. Emits a
`SUMMARY.md` consumed by mkdocs-literate-nav.
"""

import importlib
import pkgutil

import mkdocs_gen_files

import osrlib

_LAYERS = (
    ("The core kernel", "osrlib.core."),
    ("The crawl framework", "osrlib.crawl."),
    ("Shared services", "osrlib."),
)


def _exporting_modules() -> list[tuple[str, list[str]]]:
    """Return (module name, __all__) for every public osrlib module that exports symbols."""
    found = []
    for info in pkgutil.walk_packages(osrlib.__path__, "osrlib."):
        module = importlib.import_module(info.name)
        exported = getattr(module, "__all__", None)
        if exported:
            found.append((info.name, list(exported)))
    return sorted(found)


def _layer(name: str) -> str:
    for title, prefix in _LAYERS:
        if name.startswith(prefix):
            return title
    raise ValueError(f"module {name} matches no documented layer")


modules = _exporting_modules()

summary_lines = ["- [Overview](index.md)"]
for layer_title, _prefix in _LAYERS:
    members = [(name, exported) for name, exported in modules if _layer(name) == layer_title]
    summary_lines.append(f"- {layer_title}")
    for name, exported in members:
        path = name.replace(".", "/") + ".md"
        summary_lines.append(f"    - [{name}]({path})")
        with mkdocs_gen_files.open(f"reference/api/{path}", "w") as page:
            page.write(f"# `{name}`\n\n::: {name}\n    options:\n      members:\n")
            for symbol in exported:
                page.write(f"        - {symbol}\n")

with mkdocs_gen_files.open("reference/api/index.md", "w") as index:
    index.write("# API reference\n\n")
    index.write("::: osrlib\n    options:\n      members: false\n\n")
    index.write("One page per module, each rendering that module's public (importable) surface:\n\n")
    for layer_title, _ in _LAYERS:
        index.write(f"\n## {layer_title}\n\n")
        for name, exported in modules:
            if _layer(name) == layer_title:
                path = name.replace(".", "/") + ".md"
                summary = importlib.import_module(name).__doc__.strip().splitlines()[0].rstrip(".")
                index.write(f"- [`{name}`]({path}) — {summary} ({len(exported)} symbols)\n")

with mkdocs_gen_files.open("reference/api/SUMMARY.md", "w") as summary:
    summary.write("\n".join(summary_lines) + "\n")
