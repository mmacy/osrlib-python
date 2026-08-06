"""Authored narrative attached to mechanical objects: the three-audience block.

A [`NarrativeBlock`][osrlib.crawl.narrative.NarrativeBlock] is content a game's
author hangs on a mechanical object — a gate today, a trigger or a quest as those
land — and it is inert data: it decides nothing and is evaluated by nobody. Its
three audiences are:

- **Display beats**, shown verbatim by a deterministic renderer. The default
  English formatter ([`format_message`][osrlib.messages.format_message]) appends
  the beat that rides an event, so a bare transcript reads the authored line
  exactly as written.
- **The journal form**, the entry a quest or trigger appends to the party's
  written record.
- **LLM guidance**, steering for a narrating front end that is never displayed
  verbatim — the same trust posture as an area's description prose, which
  already flows into narration.

Which beats a carrier reads, and who may see them, is the carrier's business:
the block itself carries no visibility. Authored text that reaches a player rides
a player-visible event or a rejection; the wiring that produced it — conditions,
flags, guidance — stays referee-side.
"""

from pydantic import BaseModel, ConfigDict

__all__ = [
    "NarrativeBlock",
]


class NarrativeBlock(BaseModel):
    """Authored text for one mechanical object, in three audiences.

    Every field is free prose defaulting to the empty string, which means
    unauthored — a block with a refusal beat and nothing else is the normal shape.
    The display beats are read by the carriers named:

    - `refusal`, `success` — a gate ([`GateSpec`][osrlib.crawl.gates.GateSpec]):
      the line a refused attempt returns, and the line that rides the successful
      command's event.
    - `fired` — a trigger, when its consequences run.
    - `offer`, `progress`, `completion` — a quest, at activation, at an
      objective's completion, and at the quest's own.

    `journal` is the written-record form, `guidance` the LLM steering that applies
    while the carrier is in play, and `speaker` an attribution ("the bronze
    sentinel", "Sister Halda") a renderer may put in front of a beat.

    Examples:
        ```python
        from osrlib.crawl.narrative import NarrativeBlock

        narrative = NarrativeBlock(
            refusal="The sentinel's eyes stay dark. It wants the brass key.",
            success="The key turns; the sentinel steps aside.",
            speaker="the bronze sentinel",
        )
        assert narrative.offer == ""  # unauthored beats are empty, never None
        ```
    """

    model_config = ConfigDict(frozen=True)

    refusal: str = ""
    success: str = ""
    fired: str = ""
    offer: str = ""
    progress: str = ""
    completion: str = ""
    journal: str = ""
    guidance: str = ""
    speaker: str = ""
