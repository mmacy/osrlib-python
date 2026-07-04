"""Exception hierarchy for out-of-fiction failures.

The typed hierarchy rooted at [`OsrlibError`][osrlib.errors.OsrlibError] is reserved for
out-of-fiction failures: corrupt saves, unknown schema versions, malformed content.
Programmer misuse (bad argument types, out-of-range seeds) raises stdlib `ValueError` or
`TypeError` instead, and in-fiction invalid commands (moving through a wall) are rejected
by the session rather than raised. The hierarchy grows additively — each phase adds the
exception types it needs.
"""

__all__ = [
    "ContentValidationError",
    "OsrlibError",
    "ReplayVersionError",
    "SaveVersionError",
]


class OsrlibError(Exception):
    """Base class for all osrlib exceptions."""


class ContentValidationError(OsrlibError):
    """Raised when rules content is malformed.

    Covers content that fails validation at a library boundary, such as a dice
    expression that doesn't match the grammar in [`parse`][osrlib.core.dice.parse],
    compiled SRD data that fails model validation, or a serialized document whose
    structure or kind is not what the loader expects.
    """


class SaveVersionError(OsrlibError):
    """Raised when a serialized document's `schema_version` is newer than the library understands.

    Loading a document written by a newer library fails fast with this error rather
    than silently misreading it; see
    [`check_document`][osrlib.versioning.check_document].
    """


class ReplayVersionError(OsrlibError):
    """Raised when a command log is replayed under a different engine version.

    Any rules change may legitimately alter outcomes, so replaying under a
    different engine version is an explicit, detectable error rather than silent
    divergence — the spec's replay contract. Loading a *save* across engine
    versions remains legal; replay is the guarantee that breaks.
    """
