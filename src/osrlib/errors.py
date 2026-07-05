"""Exception hierarchy for out-of-fiction failures.

The typed hierarchy rooted at [`OsrlibError`][osrlib.errors.OsrlibError] is reserved for
out-of-fiction failures: corrupt saves, unknown schema versions, malformed content. An
exception means the caller broke the API contract, or the content itself is malformed —
never that a player's in-fiction choice was illegal. An invalid in-fiction command
(moving through a wall) or an illegal creation choice is refused as a
[`Rejection`][osrlib.core.validation.Rejection] result, not raised. Programmer misuse
(bad argument types, out-of-range seeds) raises stdlib `ValueError` or `TypeError`
instead.

A front end maps this hierarchy to its own error surface — HTTP status codes, process
exit codes, dialog text — however suits its platform. The hierarchy grows additively
over time: new exception types may be added, but existing ones are never removed or
repurposed.
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
    different engine version is an explicit, detectable error rather than a
    silent divergence: replay reproduces the original outcomes only when run
    under the identical engine version. Loading a *save* across engine versions
    remains legal; replay is the guarantee that breaks.
    """
