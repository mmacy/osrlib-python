"""The compiler's overrides mechanism.

Bad or ambiguous parses are corrected by JSON patch files in `overrides/`, keyed by
output file and entry id and merged after parsing — `srd/` is read-only and
`src/osrlib/data/` is never hand-edited, so this is the only place parser corrections
live. Every override carries a `reason`, and overridden entries record the touched
field paths in an `overrides_applied` list in the output, per the spec's provenance
requirement.

First known customers: the elf's and halfling's multi-prime-requisite XP tiers, which
are prose in the SRD, not tables.
"""

import json
from pathlib import Path

OVERRIDES_DIR = Path(__file__).parent / "overrides"


def load_overrides(output_filename: str, overrides_dir: Path = OVERRIDES_DIR) -> list[dict[str, object]]:
    """Load the override list for one output file.

    Args:
        output_filename: The output file the overrides target, e.g. `"classes.json"`.
        overrides_dir: The directory holding the override patch files.

    Returns:
        The override entries (empty if the file has no overrides). Each entry has
        `id`, a non-empty `reason`, and a `set` mapping of field paths to values.

    Raises:
        ValueError: If an override file is malformed.
    """
    path = overrides_dir / output_filename
    if not path.exists():
        return []
    overrides = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(overrides, list):
        raise ValueError(f"{path} must contain a JSON list")
    for override in overrides:
        if not isinstance(override, dict) or not {"id", "reason", "set"} <= set(override):
            raise ValueError(f"each override in {path} needs 'id', 'reason', and 'set' keys")
        if not override["reason"]:
            raise ValueError(f"override for {override['id']!r} in {path} has an empty reason")
        if not isinstance(override["set"], dict) or not override["set"]:
            raise ValueError(f"override for {override['id']!r} in {path} must set at least one field")
    return overrides


def apply_overrides(entries: list[dict[str, object]], overrides: list[dict[str, object]]) -> None:
    """Merge overrides into parsed entries, recording provenance.

    Each override's `set` paths are dotted from the entry root; the touched paths are
    recorded, sorted, in the entry's `overrides_applied` list. A numeric path segment
    steps into a list (`rows.1.name` is the second row's name — the encounter tables'
    per-row corrections).

    Args:
        entries: The parsed entries (each a dict with an `id`), mutated in place.
        overrides: The override list for these entries' output file.

    Raises:
        ValueError: If an override targets an unknown entry id or an unknown
            intermediate path.
    """
    by_id = {entry["id"]: entry for entry in entries}
    for override in overrides:
        entry = by_id.get(override["id"])
        if entry is None:
            raise ValueError(f"override targets unknown entry id {override['id']!r}")
        for path, value in override["set"].items():
            target: dict[str, object] | list[object] = entry
            *parents, leaf = path.split(".")
            for parent in parents:
                if isinstance(target, list):
                    stepped = target[int(parent)] if parent.isdigit() and int(parent) < len(target) else None
                else:
                    stepped = target.get(parent)
                if not isinstance(stepped, dict | list):
                    raise ValueError(f"override path {path!r} does not exist on entry {override['id']!r}")
                target = stepped
            if isinstance(target, list):
                if not leaf.isdigit() or int(leaf) >= len(target):
                    raise ValueError(f"override path {path!r} does not exist on entry {override['id']!r}")
                target[int(leaf)] = value
            else:
                target[leaf] = value
        applied = sorted({*entry.get("overrides_applied", []), *override["set"]})
        entry["overrides_applied"] = applied
