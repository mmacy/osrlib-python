"""Schema and engine version stamping.

Every serialized model (saves, commands, events) stamps itself with these values from
birth. `SCHEMA_VERSION` is the single monotonically increasing integer shared by saves,
commands, and events — independent of the package version. Within a schema version,
changes are additive only (new event types, new optional fields); renames, removals, and
semantic changes bump it. The engine version pins exact behavior: identical replay
outcomes are guaranteed only under an identical engine version.

The stamped-document helpers wrap a payload with both versions and a `kind` string, so
serialized artifacts (Phase 1 characters and parties; Phase 4 saves) share one envelope
that [`check_document`][osrlib.versioning.check_document] can vet before any payload
field is trusted.
"""

from collections.abc import Mapping
from importlib import metadata

from osrlib.errors import ContentValidationError, SaveVersionError

__all__ = [
    "SCHEMA_VERSION",
    "check_document",
    "engine_version",
    "stamp_document",
]

SCHEMA_VERSION = 2
"""The current serialization schema version shared by saves, commands, and events.

Version 2 (Phase 5): the recovered-treasure ledger left the save payload — the
end-of-adventure award's input is the departure-snapshot valuation delta, and a
ledger kept "as a log" with no consumer is exactly the accommodation the project
bans. The 1 → 2 migration drops the field; every other Phase 5 serialized change
is additive.
"""


def engine_version() -> str:
    """Return the exact osrlib package version, for stamping into saves and replays.

    Returns:
        The installed package version as reported by package metadata.
    """
    return metadata.version("osrlib")


def stamp_document(kind: str, payload: Mapping[str, object]) -> dict[str, object]:
    """Wrap a payload in the stamped-document envelope.

    Args:
        kind: The document kind, e.g. `"character"` or `"party"`. Non-empty.
        payload: The serialized model content.

    Returns:
        A dict with `kind`, `schema_version`, `engine_version`, and `payload` keys.

    Raises:
        ValueError: If `kind` is empty.
    """
    if not kind:
        raise ValueError("document kind must be non-empty")
    return {
        "kind": kind,
        "schema_version": SCHEMA_VERSION,
        "engine_version": engine_version(),
        "payload": dict(payload),
    }


def check_document(document: Mapping[str, object], expected_kind: str) -> dict[str, object]:
    """Vet a stamped document's envelope and return its payload.

    Unknown extra keys in the envelope are ignored, per the additive-schema contract.
    A `schema_version` older than the current one is accepted — ordered migrations
    arrive with full persistence in Phase 4, and schema version 1 is the floor.

    Args:
        document: A mapping previously produced by
            [`stamp_document`][osrlib.versioning.stamp_document].
        expected_kind: The kind the caller expects, e.g. `"character"`.

    Returns:
        The document's payload.

    Raises:
        ContentValidationError: If the envelope is malformed (missing keys, wrong
            types) or the document's kind is not `expected_kind`.
        SaveVersionError: If the document's `schema_version` is newer than this
            library understands.
    """
    if not isinstance(document, Mapping):
        raise ContentValidationError(f"document must be a mapping, got {type(document).__name__}")
    for key in ("kind", "schema_version", "payload"):
        if key not in document:
            raise ContentValidationError(f"document is missing required key {key!r}")
    kind = document["kind"]
    if kind != expected_kind:
        raise ContentValidationError(f"expected a {expected_kind!r} document, got kind {kind!r}")
    schema_version = document["schema_version"]
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        raise ContentValidationError(f"schema_version must be an integer, got {schema_version!r}")
    if schema_version > SCHEMA_VERSION:
        raise SaveVersionError(f"document schema_version {schema_version} is newer than the supported {SCHEMA_VERSION}")
    payload = document["payload"]
    if not isinstance(payload, Mapping):
        raise ContentValidationError(f"document payload must be a mapping, got {type(payload).__name__}")
    return dict(payload)
