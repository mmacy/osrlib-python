"""The trigger interpreter: the listener that plays an adventure's authored triggers.

[`Interpreter`][osrlib.crawl.interpreter.Interpreter] is an ordinary listener the
game registers on its session
([`GameSession.register_listener`][osrlib.crawl.session.GameSession.register_listener]).
It watches the events of every accepted command, matches them against the adventure's
[`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec]s, and acts the only way anything
outside the engine may act: by executing ordinary referee commands, each stamped with
the trigger it acted for.

That discipline is what keeps a triggered game replayable. The interpreter emits no
events of its own and remembers nothing between commands, so a replay — which runs
with no listeners at all — rebuilds the same world by re-executing the same log. Every
effect a trigger has is a command in that log, and every one of those commands says
whose idea it was.
"""

from collections.abc import Sequence

from osrlib.core.events import Event
from osrlib.crawl.commands import (
    AddJournalEntry,
    AwardXP,
    Command,
    CommandResult,
    GrantCoins,
    GrantItem,
    MarkTriggerFired,
    RecordNote,
)
from osrlib.crawl.events import FlagSetEvent, ItemAcquiredEvent, LocationEnteredEvent, MonsterDefeatedEvent
from osrlib.crawl.gates import condition_holds, flag_values_equal
from osrlib.crawl.session import GameSession
from osrlib.crawl.triggers import (
    FIRST_LIVING_SELECTOR,
    PARTY_SELECTOR,
    AreaEnteredPattern,
    DungeonEnteredPattern,
    FlagSetPattern,
    ItemAcquiredPattern,
    LevelEnteredPattern,
    MonsterDefeatedPattern,
    TownEnteredPattern,
    TriggerPattern,
    TriggerSpec,
)

__all__ = [
    "Interpreter",
]

_MAX_MATCH_DEPTH = 4
"""The deepest events a trigger still matches. The events of a player's command are
depth 0, and a firing's own events are one deeper than the event that fired it."""


def _matches_area_entered(pattern: AreaEnteredPattern, event: Event) -> bool:
    """The party entered that area, on that level, of that dungeon."""
    return (
        isinstance(event, LocationEnteredEvent)
        and event.location_kind == "area"
        and event.dungeon_id == pattern.dungeon_id
        and event.level_number == pattern.level_number
        and event.location_id == pattern.area_id
    )


def _matches_level_entered(pattern: LevelEnteredPattern, event: Event) -> bool:
    """The party arrived on that level of that dungeon — by stair or by dungeon entry.

    A crossing reports the coarsest boundary it passed, so a party coming in from town
    reports a dungeon entry and never a level entry beneath it. Both kinds match here:
    arriving on a level is arriving on it however the party got there.
    """
    return (
        isinstance(event, LocationEnteredEvent)
        and event.location_kind in ("level", "dungeon")
        and event.location_id == pattern.dungeon_id
        and event.level_number == pattern.level_number
    )


def _matches_dungeon_entered(pattern: DungeonEnteredPattern, event: Event) -> bool:
    """The party crossed into that dungeon."""
    return (
        isinstance(event, LocationEnteredEvent)
        and event.location_kind == "dungeon"
        and event.location_id == pattern.dungeon_id
    )


def _matches_town_entered(event: Event) -> bool:
    """The party arrived in town, whether it walked back or a referee put it there."""
    return isinstance(event, LocationEnteredEvent) and event.location_kind == "town"


def _matches_item_acquired(pattern: ItemAcquiredPattern, event: Event, session: GameSession) -> bool:
    """A member acquired an item with that catalog id.

    An acquisition names mundane items by catalog id and magic items by their
    session-scoped instance id, so a magic id matches by resolving the instance
    against the pack it just landed in.
    """
    if not isinstance(event, ItemAcquiredEvent):
        return False
    if pattern.item_id in event.item_ids:
        return True
    try:
        member = session.member(event.character_id)
    except ValueError:
        return False
    for acquired_id in event.item_ids:
        instance = member.inventory.magic_item(acquired_id)
        if instance is not None and instance.template_id == pattern.item_id:
            return True
    return False


def _matches_monster_defeated(pattern: MonsterDefeatedPattern, event: Event) -> bool:
    """A monster of that template was defeated — slain, routed, or surrendered alike."""
    return isinstance(event, MonsterDefeatedEvent) and event.template_id == pattern.template_id


def _matches_flag_set(pattern: FlagSetPattern, event: Event) -> bool:
    """That flag was written — with that value, or with any value at all.

    The comparison is against the value the write carried, not the value the flag
    holds now: a trigger watches the edge, and a consequence earlier in the same batch
    may already have written the key again.
    """
    if not isinstance(event, FlagSetEvent) or event.key != pattern.key:
        return False
    return pattern.value is None or flag_values_equal(event.value, pattern.value)


def _matches(pattern: TriggerPattern, event: Event, session: GameSession) -> bool:
    """Whether one event satisfies one authored pattern.

    Matching reads the event's own facts and never the party's current position: a
    consequence can relocate the party mid-batch, while an event keeps describing the
    moment it was emitted.
    """
    if isinstance(pattern, AreaEnteredPattern):
        return _matches_area_entered(pattern, event)
    if isinstance(pattern, LevelEnteredPattern):
        return _matches_level_entered(pattern, event)
    if isinstance(pattern, DungeonEnteredPattern):
        return _matches_dungeon_entered(pattern, event)
    if isinstance(pattern, TownEnteredPattern):
        return _matches_town_entered(event)
    if isinstance(pattern, ItemAcquiredPattern):
        return _matches_item_acquired(pattern, event, session)
    if isinstance(pattern, MonsterDefeatedPattern):
        return _matches_monster_defeated(pattern, event)
    return _matches_flag_set(pattern, event)


class Interpreter:
    """Plays an adventure's authored triggers by issuing referee commands.

    Register one, once, on a session that has already been built:

    ```{.python .no-run}
    session.register_listener(Interpreter(session))
    ```

    Registering twice fires everything twice — the same rule every listener follows —
    and a session restored from a save needs the registration again, because listeners
    are code and a save carries data. Nothing migrates: the interpreter's slot in
    `listener_state` is empty and stays empty forever.

    **What it does with a command's events.** It walks them in the order they
    happened and, per event, the adventure's triggers in document order. A trigger
    matches when its pattern fits the event, its fired-state allows it (once-only
    unless `repeatable`), and every one of its conditions holds against session state
    right now. A match fires immediately, before the walk moves on, so a later
    trigger's conditions see what an earlier firing has already changed.

    **What a firing issues**, all of it stamped `source="trigger:{id}"`:

    1. [`MarkTriggerFired`][osrlib.crawl.commands.MarkTriggerFired], carrying the
       `fired` beat. The mark goes in first, which is what makes once-only safe
       against a trigger whose own consequences would match it again.
    2. The consequences, in authored order, with `@party` and `@first` expanded to the
       living members they name — so the log records concrete character ids and
       replays exactly.
    3. [`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry] when the trigger's
       narrative carries a journal form, last, so the beat is stamped with the clock
       the consequences left behind.

    **When something does not work out**, the run continues and the log says why. A
    rejected consequence is dropped on its own — a spawn that meets an open encounter,
    a grant to a character who is not there — and a
    [`RecordNote`][osrlib.crawl.commands.RecordNote] records the trigger, the
    consequence's position and type, and the rejection. If a wipe mid-cascade ends the
    session, the remaining consequences land or drop by the ordinary rules of a
    terminal mode. And a cascade is bounded: a trigger's events are one deeper than
    the event that fired it, matching stops below depth five, and every firing the
    bound suppresses is recorded as a note rather than a mark — so a once-only trigger
    cut short here is still fireable later.

    **What it never does.** It returns no events, because everything it causes is
    already logged by the commands it executed, and it keeps no memory between
    commands. Read what a trigger did from the command log, the journal, and
    `session.fired_triggers`, all of which a replay rebuilds.
    """

    key = "osrlib.interpreter"
    """The listener key; its state entry exists because registration creates one, and
    is the empty dict for the life of the session."""

    def __init__(self, session: GameSession) -> None:
        """Bind the interpreter to the session it watches and issues commands through.

        Args:
            session: The session; its adventure's triggers are read once here, being
                frozen content.
        """
        self._session = session
        self._triggers = session.adventure.triggers
        self._depth = 0

    def handle(self, events: Sequence[Event], state: dict) -> tuple[list[Event], dict]:
        """Match one command's events and fire what they fired.

        Args:
            events: The command's accumulated events, in the order they happened.
            state: The listener's state slot, always the empty dict.

        Returns:
            No events and the empty state — everything the interpreter does is a
            command it executed, and it remembers nothing.
        """
        depth = self._depth
        for event in events:
            for trigger in self._triggers:
                if not self._would_fire(trigger, event):
                    continue
                if depth > _MAX_MATCH_DEPTH:
                    # Evaluated in full and suppressed: no mark, so a once-only
                    # trigger cut short here stays fireable later.
                    self._issue(
                        RecordNote(
                            text=(
                                f"trigger {trigger.id}: not fired, the cascade reached "
                                f"depth {depth} past the limit of {_MAX_MATCH_DEPTH}"
                            )
                        ),
                        trigger.id,
                    )
                    continue
                self._fire(trigger, depth)
        return [], {}

    def _would_fire(self, trigger: TriggerSpec, event: Event) -> bool:
        """Whether this trigger fires on this event: pattern, fired-state, conditions."""
        if not _matches(trigger.when, event, self._session):
            return False
        if trigger.id in self._session.fired_triggers and not trigger.repeatable:
            return False
        return all(
            condition_holds(
                condition,
                members=self._session.party.members,
                flags=self._session.flags,
                ledger=self._session.ledger,
            )
            for condition in trigger.conditions
        )

    def _fire(self, trigger: TriggerSpec, depth: int) -> None:
        """Issue one firing's whole batch, one level deeper than the event that fired it."""
        narrative = trigger.narrative
        previous = self._depth
        self._depth = depth + 1
        try:
            self._issue(
                MarkTriggerFired(trigger_id=trigger.id, narrative=(narrative.fired or None) if narrative else None),
                trigger.id,
            )
            for position, consequence in enumerate(trigger.consequences):
                for command in self._expand(consequence, trigger, position):
                    result = self._issue(command, trigger.id)
                    if not result.accepted:
                        self._note_drop(trigger, position, command, result.rejections[0].code)
            if narrative is not None and narrative.journal:
                self._issue(AddJournalEntry(text=narrative.journal), trigger.id)
        finally:
            self._depth = previous

    def _expand(self, consequence: Command, trigger: TriggerSpec, position: int) -> list[Command]:
        """Resolve a consequence's party selector into the commands it stands for.

        A literal character id is not a selector and is passed through untouched: an
        id a document could not have known lands as an ordinary rejection, dropped and
        noted like any other.
        """
        if not isinstance(consequence, GrantItem | GrantCoins | AwardXP):
            return [consequence]
        living = self._session.party.living_members()
        if consequence.character_id == PARTY_SELECTOR:
            return [consequence.model_copy(update={"character_id": member.id}) for member in living]
        if consequence.character_id == FIRST_LIVING_SELECTOR:
            if not living:
                self._note_drop(trigger, position, consequence, f"no living member for {FIRST_LIVING_SELECTOR}")
                return []
            return [consequence.model_copy(update={"character_id": living[0].id})]
        return [consequence]

    def _note_drop(self, trigger: TriggerSpec, position: int, command: Command, reason: str) -> None:
        """Record a consequence that did not land, from the facts alone."""
        self._issue(
            RecordNote(
                text=f"trigger {trigger.id}: consequence {position} ({command.command_type}) dropped ({reason})"
            ),
            trigger.id,
        )

    def _issue(self, command: Command, trigger_id: str) -> CommandResult:
        """Execute one command on the trigger's behalf, stamped with its id.

        Commands are frozen, so the stamp is a copy — the authored consequence in the
        document is never touched.
        """
        return self._session.execute(command.model_copy(update={"source": f"trigger:{trigger_id}"}))
