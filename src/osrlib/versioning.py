"""Schema and engine version stamping.

Every serialized model (saves, commands, events) stamps itself with these values from
birth. `SCHEMA_VERSION` is the single monotonically increasing integer shared by saves,
commands, and events — independent of the package version. Within a schema version,
changes are additive only (new event types, new optional fields); renames, removals, and
semantic changes bump it. The engine version pins exact behavior: identical replay
outcomes are guaranteed only under an identical engine version.
"""

from importlib import metadata

__all__ = [
    "SCHEMA_VERSION",
    "engine_version",
]

SCHEMA_VERSION = 1
"""The current serialization schema version shared by saves, commands, and events."""


def engine_version() -> str:
    """Return the exact osrlib package version, for stamping into saves and replays.

    Returns:
        The installed package version as reported by package metadata.
    """
    return metadata.version("osrlib")
