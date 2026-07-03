"""The event base class and emission contract.

Every rules resolution emits typed events, and this module locks the rules all of them
obey:

- Events carry structured fields and a message code — dotted snake_case namespaced by
  subsystem (`combat.attack.hit`, `exploration.torch.expired`) — never baked English
  prose. A default English message formatter ships outside the event models (with the
  first real events, Phase 2), so front ends can localize and LLM narrators get facts
  rather than canned text.
- Events carry a visibility level, because B/X hides some rolls by design: the referee
  rolls hide in shadows and hear noise on the player's behalf. Front ends filter on it;
  an LLM referee sees everything.
- Consumers must tolerate unknown event types and unknown fields: within a
  `schema_version`, the event schema grows additively only. The base class pins
  `extra="ignore"` so no subclass can silently break that guarantee with
  `extra="forbid"`.

Whether serialized events carry a type discriminator beyond `code` is deliberately
deferred to the first real event emissions and the command/event envelope (Phase 2),
before any event crosses a serialization boundary.
"""

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator

__all__ = [
    "Event",
    "Visibility",
]

_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")


class Visibility(StrEnum):
    """Who may see an event.

    The wire values are `"player"` and `"referee"` — lowercase, serialized into every
    event; changing them is a `schema_version` bump.
    """

    PLAYER = "player"
    REFEREE = "referee"


class Event(BaseModel):
    """Base class for all osrlib events.

    Events are frozen: they are records of what happened, appended to the session log,
    never mutated. Subclasses add structured fields only — entity IDs, roll results,
    quantities — and must never bake in English prose.

    `code` is the event's message code: two or more dot-separated segments, each
    matching `[a-z][a-z0-9_]*`, namespaced by subsystem (`combat.attack.hit`).
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    code: str
    visibility: Visibility

    @field_validator("code")
    @classmethod
    def _code_must_be_dotted_snake_case(cls, value: str) -> str:
        if _CODE_PATTERN.fullmatch(value) is None:
            raise ValueError(
                "event code must be two or more dot-separated snake_case segments "
                f"(like 'combat.attack.hit'), got {value!r}"
            )
        return value
