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
]


class OsrlibError(Exception):
    """Base class for all osrlib exceptions."""


class ContentValidationError(OsrlibError):
    """Raised when rules content is malformed.

    Covers content that fails validation at a library boundary, such as a dice
    expression that doesn't match the grammar in [`parse`][osrlib.core.dice.parse]
    or, in later phases, compiled SRD data that fails model validation.
    """
