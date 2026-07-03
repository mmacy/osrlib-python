"""The three alignments, in a home importable by both characters and monsters.

[`Alignment`][osrlib.core.alignment.Alignment] began life in
[`osrlib.core.character`][osrlib.core.character] (which still re-exports it — the
Phase 1 import path stays valid). It lives here because monster data also carries
alignments and the generated-data loaders import the monster models: a module the
loaders import must not import `character`, which itself imports the loaders.
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
