"""Authored narrative attached to mechanical objects: the three-audience block.

A [`NarrativeBlock`][osrlib.crawl.narrative.NarrativeBlock] is the authored text you
hang on a mechanical object.

Where a block sits. You write one into the `narrative` field of a gate
([`GateSpec`][osrlib.crawl.gates.GateSpec]), a trigger
([`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec]), a quest
([`QuestSpec`][osrlib.crawl.quests.QuestSpec]), or a quest objective
([`ObjectiveSpec`][osrlib.crawl.quests.ObjectiveSpec]), all of which travel in the
[`Adventure`][osrlib.crawl.adventure.Adventure] document. The block itself is inert: it
decides nothing, and nothing evaluates it. What reads it is whatever evaluates its
carrier, meaning the gate, trigger, quest, or objective it hangs on: the exploration
handlers of
[`GameSession.execute`][osrlib.crawl.session.GameSession.execute] for a gate and the
[`Interpreter`][osrlib.crawl.interpreter.Interpreter] for a trigger or a quest. The
beats then reach you as the `narrative` field of an event such as
[`DoorEvent`][osrlib.crawl.events.DoorEvent],
[`TriggerFiredEvent`][osrlib.crawl.events.TriggerFiredEvent], or
[`QuestActivatedEvent`][osrlib.crawl.events.QuestActivatedEvent], as the `text` of a
[`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent], or inside a
gate refusal's rejection.

The three audiences are:

- Display beats, shown as written by a deterministic renderer. The default English
  formatter ([`format_message`][osrlib.messages.format_message]) appends the beat from
  an event after the templated line, so a bare transcript reads the authored words
  exactly as you wrote them.
- The journal form, the entry a carrier appends to the party's written record. A quest
  beat does not use it, because what a quest journals is the display text it showed, so
  the journal reads as the transcript of what the table was told. The field is the voice
  of carriers whose display beat the players never see: a trigger's `fired` text travels
  on a referee-visibility event, so write the line meant for the table here.
- LLM guidance, steering for a narrating front end that is never displayed as written.
  It has the same trust posture as an area's description prose, which already flows into
  narration.

Which beats a carrier reads, and who may see them, is the carrier's business: the block
itself has no visibility. Authored text that reaches a player travels on a player-visible
event or inside a rejection, while the wiring that produced it, meaning conditions, flags,
and guidance, stays on the referee's side of the screen.
"""

from pydantic import BaseModel, ConfigDict

__all__ = [
    "NarrativeBlock",
]


class NarrativeBlock(BaseModel):
    """Authored text for one mechanical object, in three audiences.

    Construct one and pass it as the `narrative` of the gate, trigger, quest, or
    objective it belongs to. Every field is free prose defaulting to the empty string,
    which means unauthored, so a block with a refusal beat and nothing else is a normal
    shape. Which display beats a block speaks depends on what it hangs on:

    - `refusal` and `success` on a gate ([`GateSpec`][osrlib.crawl.gates.GateSpec]): the
      line a refused attempt returns, and the line that travels on the successful
      command's event.
    - `fired` on a trigger ([`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec]), when its
      consequences run. It travels on a referee-visibility event, so it is the referee's
      line about the wiring, and `journal` is the players' line about the same moment.
    - `offer` and `completion` on a quest ([`QuestSpec`][osrlib.crawl.quests.QuestSpec]),
      at its activation and at its own completion.
    - `offer` and `progress` on an objective
      ([`ObjectiveSpec`][osrlib.crawl.quests.ObjectiveSpec]), when it is revealed, the
      objective presenting itself, and when it completes, the story advancing.

    Per-objective beats need a per-objective carrier, which is why an objective reads the
    same two field names for moments of its own. A quest's `progress` and an objective's
    `completion` are read by nobody. They are silently unread rather than rejected at
    parse, by the same standing convention that lets a gate leave `fired` alone and a
    trigger leave `offer` alone.

    `journal` is the written-record form, unread by the quest layer, which journals the
    display text it showed. `guidance` is the LLM steering that applies while the carrier
    is in play, and `speaker` an attribution such as "the bronze sentinel" or "Sister
    Halda" that a renderer may put in front of a beat.

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
    """A gate's refusal line, returned inside the rejection when the attempt is
    refused."""
    success: str = ""
    """A gate's success line, which travels on the successful command's own event: a
    [`DoorEvent`][osrlib.crawl.events.DoorEvent] for a door, a
    [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent] for a transition
    that crosses into a new level or dungeon."""
    fired: str = ""
    """A trigger's firing line: the referee's beat, reported by the referee-visibility
    [`TriggerFiredEvent`][osrlib.crawl.events.TriggerFiredEvent]."""
    offer: str = ""
    """A quest's activation line, or an objective's reveal line. Shown and journaled."""
    progress: str = ""
    """An objective's completion line. Shown and journaled, and unread on a quest
    block."""
    completion: str = ""
    """A quest's completion line. Shown and journaled, and unread on an objective
    block."""
    journal: str = ""
    """The written-record form, for carriers whose display beat the players never see,
    which in practice means a trigger's `fired`. Unread by quests, which journal the
    display text they showed."""
    guidance: str = ""
    """Steering for an LLM narrator while the carrier is in play, and never displayed as
    written.

    Inert authored data, the way a quest's `progress` beat is: osrlib reads it nowhere, no
    event includes it, and no rule turns on it. A narrator reaches it through the adventure
    document, which stays on the referee's side of the screen, so read it from the
    authored model yourself when you write the narration. The same field on
    [`LevelSpec`][osrlib.crawl.dungeon.LevelSpec] does the same job for a whole level."""
    speaker: str = ""
    """An attribution, such as "the bronze sentinel", that a renderer may put in front of
    a beat. [`QuestView.speaker`][osrlib.crawl.views.QuestView] ships a quest's to the
    player view."""
