"""Authored text for gates, triggers, quests, and objectives, written for three audiences.

A [`NarrativeBlock`][osrlib.crawl.narrative.NarrativeBlock] is the authored text you
attach to one of those objects. Each of its fields is a beat, one line of text for one
moment, like a gate's refusal or a quest's offer.

You write one into the `narrative` field of a gate
([`GateSpec`][osrlib.crawl.gates.GateSpec]), a trigger
([`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec]), a quest
([`QuestSpec`][osrlib.crawl.quests.QuestSpec]), or a quest objective
([`ObjectiveSpec`][osrlib.crawl.quests.ObjectiveSpec]), all of which are part of the
[`Adventure`][osrlib.crawl.adventure.Adventure] document. osrlib evaluates nothing in the
block. The code that evaluates the gate, trigger, quest, or objective reads the beats it
needs from the block: the exploration handlers of
[`GameSession.execute`][osrlib.crawl.session.GameSession.execute] for a gate, the
[`Interpreter`][osrlib.crawl.interpreter.Interpreter] for a trigger, and the quest commands for
a quest or an objective. The
beats then reach you as the `narrative` field of an event like
[`DoorEvent`][osrlib.crawl.events.DoorEvent],
[`TriggerFiredEvent`][osrlib.crawl.events.TriggerFiredEvent], or
[`QuestActivatedEvent`][osrlib.crawl.events.QuestActivatedEvent], as the `text` of a
[`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent], or inside a
gate refusal's rejection.

The three audiences are:

- Display beats, shown as written by a deterministic renderer. The default English
  formatter ([`format_message`][osrlib.messages.format_message]) appends the beat from
  an event after the templated line, so a bare transcript shows the authored words
  exactly as you wrote them.
- The journal form, the object's entry in the party's written record. Quests don't use it,
  because a quest journals the display text it showed, so the journal reads as a
  transcript of what the table was told. Use the field for a trigger, whose `fired` text is
  included on a referee-visibility event that players never see, and write the line meant
  for the table here.
- LLM guidance, instructions for a front end that uses a language model to narrate. It is
  never displayed as written. A narrator treats it as authored content, the way it treats an
  area's description.

Which beats are read, and who may see them, depends on the object the block is attached to.
The block has no visibility setting. Authored text that reaches a player is included on a player-visible
event or inside a rejection, while the mechanics that produced it, meaning conditions,
flags, and guidance, stay on the referee's side of the screen.
"""

from pydantic import BaseModel, ConfigDict

__all__ = [
    "NarrativeBlock",
]


class NarrativeBlock(BaseModel):
    """Authored text for one mechanical object, in three audiences.

    Construct one and pass it as the `narrative` of the gate, trigger, quest, or
    objective it belongs to. Every field is free prose defaulting to the empty string,
    which means unauthored, so a block with only a refusal beat is normal. Which beats
    osrlib reads from a block depends on the object the block belongs to:

    - `refusal` and `success` on a gate ([`GateSpec`][osrlib.crawl.gates.GateSpec]): the
      line a refused attempt returns, and the line included on the successful
      command's event.
    - `fired` on a trigger ([`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec]), when its
      consequences run. It's included on a referee-visibility event, so it is the referee's
      line about the mechanics, and `journal` is the players' line about the same moment.
    - `offer` and `completion` on a quest ([`QuestSpec`][osrlib.crawl.quests.QuestSpec]),
      at its activation and at its completion.
    - `offer` and `progress` on an objective
      ([`ObjectiveSpec`][osrlib.crawl.quests.ObjectiveSpec]), when it is revealed, the
      objective presenting itself, and when it completes, the story advancing.

    An objective reuses the `offer` and `progress` fields for its reveal and its completion,
    so each objective can have beats separate from its quest's. osrlib never reads a quest's
    `progress` or an objective's `completion`. It accepts them when it parses a block rather
    than rejecting them, the same way it accepts `fired` on a gate and `offer` on a trigger.

    `journal` is the written-record form, which the
    [`Interpreter`][osrlib.crawl.interpreter.Interpreter] writes to the journal when a trigger
    fires. Quests don't read it, and journal the display text they showed instead. `guidance`
    is instructions for a language-model narrator while the object is in play, and `speaker` is
    an attribution, like "the bronze sentinel" or "Sister Halda", that a renderer may put in
    front of a beat.

    Examples:
        ```python
        from osrlib.crawl.narrative import NarrativeBlock

        narrative = NarrativeBlock(
            refusal="The sentinel's eyes stay dark. It wants the brass key.",
            success="The key turns, and the sentinel steps aside.",
            speaker="the bronze sentinel",
        )
        print(repr(narrative.offer))  # an unauthored beat is the empty string, never None
        # ''
        print(sorted(narrative.model_dump(exclude_defaults=True)))
        # ['refusal', 'speaker', 'success']
        ```
    """

    model_config = ConfigDict(frozen=True)

    refusal: str = ""
    """A gate's refusal line, returned inside the rejection when the attempt is
    refused."""
    success: str = ""
    """A gate's success line, included on the successful command's event: a
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
    """The written-record form. When a trigger fires, the
    [`Interpreter`][osrlib.crawl.interpreter.Interpreter] adds it to the journal with
    [`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry]. Quests don't read it, because they
    journal the display text they showed."""
    guidance: str = ""
    """Steering for an LLM narrator while the object it belongs to is in play, and never
    displayed as written.

    osrlib never reads it, no event includes it, and no rule depends on it. A narrator reaches
    it through the adventure document, which stays on the referee's side of the screen, so read
    it from the authored model yourself when you write the narration. The same field on
    [`LevelSpec`][osrlib.crawl.dungeon.LevelSpec] does the same job for a whole level."""
    speaker: str = ""
    """An attribution, like "the bronze sentinel", that a renderer may put in front of
    a beat. [`QuestView.speaker`][osrlib.crawl.views.QuestView] includes a quest's speaker
    in the player view."""
