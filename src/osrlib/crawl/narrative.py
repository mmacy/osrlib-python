"""Authored text for gates, triggers, quests, and objectives, written for three audiences.

A [`NarrativeBlock`][osrlib.crawl.narrative.NarrativeBlock] contains the text an adventure's author
writes for one of those objects. Each of its fields is a *beat*: one line of text for one moment,
like a gate's refusal or a quest's offer.

You put a block in the `narrative` field of a gate
([`GateSpec`][osrlib.crawl.gates.GateSpec]), a trigger
([`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec]), a quest
([`QuestSpec`][osrlib.crawl.quests.QuestSpec]), or a quest objective
([`ObjectiveSpec`][osrlib.crawl.quests.ObjectiveSpec]). All four are part of the
[`Adventure`][osrlib.crawl.adventure.Adventure] document. osrlib evaluates nothing in the block.
The code that runs the object reads the beats it needs: the door and transition handlers of
[`GameSession.execute`][osrlib.crawl.session.GameSession.execute] for a gate, the
[`Interpreter`][osrlib.crawl.interpreter.Interpreter] for a trigger, and the quest commands for a
quest or an objective. You get the beats back in the `narrative` field of an event like
[`DoorEvent`][osrlib.crawl.events.DoorEvent],
[`TriggerFiredEvent`][osrlib.crawl.events.TriggerFiredEvent], or
[`QuestActivatedEvent`][osrlib.crawl.events.QuestActivatedEvent], in the `text` of a
[`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent], or in the parameters of the
rejection the session returns when a gate's condition isn't met.

The three audiences are:

- The display beats, `refusal`, `success`, `fired`, `offer`, `progress`, and `completion`, which
  you show exactly as written in the code you write to show the game to a player. The default
  English formatter,
  [`format_message`][osrlib.messages.format_message], appends an event's beat after its templated
  line, so a plain transcript shows the author's words unchanged.
- The `journal` beat, the object's entry in the party's journal (the written record the session
  keeps of the adventure). osrlib reads it only for a trigger. A trigger's `fired` beat goes on a
  referee-only event that players never see, so write the line meant for the players in `journal`.
  For a quest, the session writes the display beat it showed to the journal instead.
- The `guidance` beat, instructions for a language model that narrates the game. Don't show it to a
  player as written. Give it to the narrator as authored content, the same way you give it an area's
  description.

Which beats osrlib reads, and who sees them, depends on the object the block belongs to. The block has no
visibility setting. Text meant for a player reaches them on a player-visible event or in
a rejection. The conditions, flags, and guidance that produced it stay in referee-only events and in
the adventure document.
"""

from pydantic import BaseModel, ConfigDict

__all__ = [
    "NarrativeBlock",
]


class NarrativeBlock(BaseModel):
    """The authored beats for one gate, trigger, quest, or objective.

    Construct one and pass it as the `narrative` of the object it belongs to. Every field is free
    text. The empty string, the default, means the beat isn't written, so a block with only a
    refusal is normal. Which beats osrlib reads depends on the object the block belongs to:

    - For a gate ([`GateSpec`][osrlib.crawl.gates.GateSpec]), osrlib reads `refusal` and `success`. When the
      session refuses an attempt, its rejection includes `refusal`. When an attempt succeeds, its event
      includes `success`.
    - For a trigger ([`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec]), osrlib reads `fired` and
      `journal` when its consequences run. `fired` goes on a referee-only event, so it's the referee's line
      about what happened. `journal` is the players' line about the same moment.
    - For a quest ([`QuestSpec`][osrlib.crawl.quests.QuestSpec]), osrlib reads `offer` when the quest
      activates and `completion` when it completes.
    - For an objective ([`ObjectiveSpec`][osrlib.crawl.quests.ObjectiveSpec]), osrlib reads `offer`
      when the objective is revealed and `progress` when it completes. If an objective completes before
      it's revealed, the session uses only its `progress`.

    osrlib ignores the beats it doesn't read for an object, like `progress` on a quest, `completion` on an
    objective, `fired` on a gate, or `offer` on a trigger. A block that sets them still parses.

    Two fields apply to every object. `guidance` is instructions for a narrator that uses a language
    model, and osrlib never reads it. `speaker` is an attribution, like "the bronze sentinel" or
    "Sister Halda", that you can show in front of a beat.

    Examples:
        ```python
        from osrlib.crawl.narrative import NarrativeBlock

        narrative = NarrativeBlock(
            refusal="The sentinel's eyes stay dark. It wants the brass key.",
            success="The key turns, and the sentinel steps aside.",
            speaker="the bronze sentinel",
        )
        print(repr(narrative.offer))  # a beat you don't write is the empty string, never None
        # ''
        print(sorted(narrative.model_dump(exclude_defaults=True)))
        # ['refusal', 'speaker', 'success']
        ```
    """

    model_config = ConfigDict(frozen=True)

    refusal: str = ""
    """A gate's refusal line. When the session refuses an attempt because the gate's condition isn't
    met, the rejection includes this line as its `refusal` parameter. Leave it empty and the
    rejection has no `refusal` parameter."""
    success: str = ""
    """A gate's success line. It goes in the `narrative` of the event for the successful attempt: a
    [`DoorEvent`][osrlib.crawl.events.DoorEvent] for a door, a
    [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent] for a transition to another
    level, or a [`PartyMovedEvent`][osrlib.crawl.events.PartyMovedEvent] for a transition to another
    cell of the same level."""
    fired: str = ""
    """A trigger's firing line, for the referee. It goes in the `narrative` of the
    [`TriggerFiredEvent`][osrlib.crawl.events.TriggerFiredEvent], which players never see."""
    offer: str = ""
    """A quest's activation line, or an objective's reveal line. It goes in the event's `narrative`
    and into the journal."""
    progress: str = ""
    """An objective's completion line. It goes in the event's `narrative` and into the journal.
    osrlib doesn't read it on a quest."""
    completion: str = ""
    """A quest's completion line. It goes in the event's `narrative` and into the journal. osrlib
    doesn't read it on an objective."""
    journal: str = ""
    """A trigger's journal line, for the players. When the trigger fires, the
    [`Interpreter`][osrlib.crawl.interpreter.Interpreter] adds it to the journal with
    [`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry]. osrlib doesn't read it on a quest,
    because for a quest the session writes the line it showed to the journal."""
    guidance: str = ""
    """Instructions for a narrator that uses a language model, while the object is in play. It isn't
    text to show a player as written.

    osrlib never reads it, no event includes it, and no rule depends on it. Read it from the
    adventure document when your narrator writes its text. The adventure document is referee-only
    content, so don't show the field to a player. [`LevelSpec.guidance`][osrlib.crawl.dungeon.LevelSpec.guidance]
    does the same job for a whole level."""
    speaker: str = ""
    """An attribution, like "the bronze sentinel", that you can show in front of a beat.
    osrlib reads it only on a quest, and passes it to the player view as
    [`QuestView.speaker`][osrlib.crawl.views.QuestView.speaker]."""
