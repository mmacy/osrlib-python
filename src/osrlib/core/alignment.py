"""The three alignments, in a home importable by both characters and monsters.

[`Alignment`][osrlib.core.alignment.Alignment] lives in its own module because both
character and monster data carry alignments, and the generated-data loaders import
the monster models: a module the loaders import must not import `character`, which
itself imports the loaders.
"""

from enum import StrEnum

__all__ = [
    "Alignment",
]


class Alignment(StrEnum):
    """The three alignments.

    The wire values are lowercase — they serialize into characters and saves; changing
    them is a `schema_version` bump.
    """

    LAWFUL = "lawful"
    NEUTRAL = "neutral"
    CHAOTIC = "chaotic"
