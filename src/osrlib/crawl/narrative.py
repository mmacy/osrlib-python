"""Authored narrative attached to mechanical objects: the three-audience block.

A [`NarrativeBlock`][osrlib.crawl.narrative.NarrativeBlock] is content a game's
author hangs on a mechanical object — a gate today, a trigger or a quest as those
land — and it is inert data: it decides nothing and is evaluated by nobody. Its
three audiences are:

- **Display beats**, shown verbatim by a deterministic renderer. The default
  English formatter ([`format_message`][osrlib.messages.format_message]) appends
  the beat that rides an event, so a bare transcript reads the authored line
  exactly as written.
- **The journal form**, the entry a carrier appends to the party's written record.
  A quest beat does not use it: what a quest journals *is* the display text it
  showed, so the journal reads as the transcript of what the table was told. This
  field is the voice of carriers whose display beat the players never see — a
  trigger's `fired` text rides a referee-visibility event, so a trigger that should
  say something to the table says it here.
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
    Which display beats a block speaks depends on what it hangs on:

    - `refusal`, `success` — a gate ([`GateSpec`][osrlib.crawl.gates.GateSpec]):
      the line a refused attempt returns, and the line that rides the successful
      command's event.
    - `fired` — a trigger ([`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec]), when
      its consequences run. It rides a referee-visibility event, so it is the
      referee's line about the wiring; `journal` is the players' line about the
      same moment.
    - `offer`, `completion` — a quest ([`QuestSpec`][osrlib.crawl.quests.QuestSpec]),
      at its activation and at its own completion.
    - `offer`, `progress` — an objective
      ([`ObjectiveSpec`][osrlib.crawl.quests.ObjectiveSpec]), when it is revealed
      (the objective presenting itself) and when it completes (the story advancing).

    Per-objective beats need a per-objective carrier, which is why the objective
    reads the same two field names for moments of its own. A quest's `progress` and
    an objective's `completion` are read by nobody, and are silently unread rather
    than rejected at parse — the same standing convention by which a gate leaves
    `fired` alone and a trigger leaves `offer` alone.

    `journal` is the written-record form (unread by the quest layer, which journals
    the display text it showed), `guidance` the LLM steering that applies while the
    carrier is in play, and `speaker` an attribution ("the bronze sentinel",
    "Sister Halda") a renderer may put in front of a beat.

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
