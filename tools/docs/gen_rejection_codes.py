"""Generate the rejection-code reference: the source scan merged with the description catalog.

Runs under mkdocs-gen-files at build time. The codes come from the AST scan (see
`rejection_scan.py`), the one-line descriptions from the hand-maintained
`rejection_codes.json` beside this script. Generation fails on any mismatch in either
direction — a scanned code with no description, or a described code no longer in the
source — so the page and the code can never drift apart.
"""

import json
import sys
from pathlib import Path

import mkdocs_gen_files

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.docs.rejection_scan import scan_rejection_codes  # noqa: E402

_INTRO = """\
# Rejection codes

A command the engine cannot accept comes back with a rejection: a structured refusal
carrying a dotted snake_case `code`, never an exception. Validation is a pure
pre-phase, so a rejected command consumes no randomness, no game time, and mutates
nothing — in-fiction failures are a normal outcome, not an error. See
[`Rejection`][osrlib.core.validation.Rejection] and
[`CommandResult`][osrlib.crawl.commands.CommandResult].

This page lists every rejection code the engine emits, grouped by namespace. Each
command's own documentation lists the codes it can come back with.
"""

_descriptions_path = Path(__file__).resolve().parent / "rejection_codes.json"
descriptions: dict[str, str] = json.loads(_descriptions_path.read_text(encoding="utf-8"))

scanned = scan_rejection_codes()

undescribed = sorted(set(scanned) - set(descriptions))
stale = sorted(set(descriptions) - set(scanned))
if undescribed or stale:
    raise ValueError(
        "rejection_codes.json and the source scan disagree — "
        f"scanned codes with no description: {undescribed}; described codes not in source: {stale}. "
        "Update tools/docs/rejection_codes.json."
    )

namespaces: dict[str, list[str]] = {}
for code in scanned:
    namespaces.setdefault(code.split(".")[0], []).append(code)

with mkdocs_gen_files.open("reference/rejection-codes.md", "w") as page:
    page.write(_INTRO)
    for namespace in sorted(namespaces):
        page.write(f"\n## `{namespace}.*`\n\n")
        page.write("| Code | Meaning |\n|---|---|\n")
        for code in namespaces[namespace]:
            page.write(f"| `{code}` | {descriptions[code]} |\n")
