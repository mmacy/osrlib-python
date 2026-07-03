"""The `Ruleset` model: optional-rule and adaptation flags.

Every SRD optional rule and every documented adaptation is a named flag with a
default. Flags are read at resolution time, so a `Ruleset` is fixed for the life of a
session (it participates in saves and replays). The model is frozen and rejects unknown
flags — a typo'd flag name errors instead of silently doing nothing.

Phase 1 defines only the flags Phase 1 reads. The spec's remaining 1.0 flags are added
by the phases that implement their behavior; shipping a flag whose behavior doesn't
exist yet would be a lie in the API. Additive flag growth is schema-legal.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

__all__ = [
    "EncumbranceMode",
    "Ruleset",
]


class EncumbranceMode(StrEnum):
    """How carried weight is tracked; see [`osrlib.core.items`][osrlib.core.items].

    The wire values are `"none"`, `"basic"`, and `"detailed"` — lowercase, serialized
    into saves; changing them is a `schema_version` bump.
    """

    NONE = "none"
    BASIC = "basic"
    DETAILED = "detailed"


class Ruleset(BaseModel):
    """The optional-rule and adaptation flags a session plays under.

    Attributes:
        hp_reroll_at_first_level: SRD optional rule: re-roll starting hit-point rolls
            of 1–2 (the raw die, before the CON modifier) until the die shows 3 or
            more. Default off.
        encumbrance: Which encumbrance system tracks carried weight and drives
            movement rates. Default basic.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    hp_reroll_at_first_level: bool = False
    encumbrance: EncumbranceMode = EncumbranceMode.BASIC
