"""Old-School Essentials (B/X) rules engine for turn-based dungeon crawlers.

osrlib is the rules authority and game-state engine; the game supplies presentation,
input, and content. The library is headless and sans-I/O: it never renders, prompts,
sleeps, or touches the network, and all randomness flows through named deterministic
streams (see [`osrlib.core.rng`][osrlib.core.rng]).
"""

from .errors import ContentValidationError, OsrlibError
from .versioning import SCHEMA_VERSION, engine_version

__all__ = [
    "SCHEMA_VERSION",
    "ContentValidationError",
    "OsrlibError",
    "engine_version",
]
