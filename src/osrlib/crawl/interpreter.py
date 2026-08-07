"""The interpreter: the listener that plays an adventure's authored triggers and quests.

[`Interpreter`][osrlib.crawl.interpreter.Interpreter] is an ordinary listener the
game registers on its session
([`GameSession.register_listener`][osrlib.crawl.session.GameSession.register_listener]).
It watches the events of every accepted command, matches them against the adventure's
[`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec]s and
[`QuestSpec`][osrlib.crawl.quests.QuestSpec]s, and acts the only way anything outside
the engine may act: by executing ordinary referee commands, each stamped with the
trigger or quest it acted for.

That discipline is what keeps an authored game replayable. The interpreter emits no
events of its own and remembers nothing between commands, so a replay — which runs
with no listeners at all — rebuilds the same world by re-executing the same log. Every
effect a trigger or a quest has is a command in that log, and every one of those
commands says whose idea it was.
"""

from collections.abc import Sequence
from typing import NamedTuple

from osrlib.core.events import Event
from osrlib.crawl.commands import (
    ActivateQuest,
    AddJournalEntry,
    AwardXP,
    Command,
    CommandResult,
    CompleteObjective,
    CompleteQuest,
    GrantCoins,
    GrantItem,
    MarkTriggerFired,
    RecordNote,
    RevealObjective,
)
from osrlib.crawl.events import FlagSetEvent, ItemAcquiredEvent, LocationEnteredEvent, MonsterDefeatedEvent
from osrlib.crawl.gates import ConditionSpec, condition_holds, flag_values_equal
from osrlib.crawl.quests import QuestSpec, TriggerClause
from osrlib.crawl.session import GameSession, QuestState
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
"""The deepest events a trigger or a quest clause still matches. The events of a
player's command are depth 0, and what a firing or a quest advancement issues is one
deeper than the event that caused it."""


class _Owner(NamedTuple):
    """Whoever the interpreter is acting for: what it signs with, and what it says.

    One definition of both forms, shared by triggers and quests, so the stamp on a
    command and the subject of a note can never disagree about who acted.
    """

    kind: str
    """`"trigger"` or `"quest"`."""

    id: str

    @property
    def stamp(self) -> str:
        """The `source` every command this owner causes carries: `trigger:{id}`, `quest:{id}`."""
        return f"{self.kind}:{self.id}"

    @property
    def label(self) -> str:
        """How a note names the owner: `trigger lever-east`, `quest the-idol`."""
        return f"{self.kind} {self.id}"


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
    """Plays an adventure's authored triggers and quests by issuing referee commands.

    Register one, once, on a session that has already been built:

    ```{.python .no-run}
    session.register_listener(Interpreter(session))
    ```

    Registering twice fires everything twice — the same rule every listener follows —
    and a session restored from a save needs the registration again, because listeners
    are code and a save carries data. Nothing migrates: the interpreter's slot in
    `listener_state` is empty and stays empty forever.

    **What it does with a command's events.** It walks them in the order they
    happened and, per event, the adventure's triggers in document order and then its
    quests in document order — one rule, and the only order there is. A trigger
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

    **What a quest walk issues**, all of it stamped `source="quest:{id}"`. A quest
    clause ([`TriggerClause`][osrlib.crawl.quests.TriggerClause]) is matched exactly
    the way a trigger is — the same patterns, the same live conditions — and the walk
    goes:

    1. An inactive quest whose activation clause matches gets
       [`ActivateQuest`][osrlib.crawl.commands.ActivateQuest], and the walk carries on
       into the objectives of the quest it just activated: the same event that starts
       a quest can finish something in it.
    2. An active quest's objectives walk in authored order. A hidden, unrevealed,
       incomplete objective whose `reveal_when` matches gets
       [`RevealObjective`][osrlib.crawl.commands.RevealObjective]; an incomplete
       objective whose `when` matches gets
       [`CompleteObjective`][osrlib.crawl.commands.CompleteObjective]. An objective
       that completes without ever being revealed needs no reveal — completing shows
       it.
    3. The moment a completion lands, the quest's completion rule is checked against
       live state (`all` or `any`), and a satisfied rule gets
       [`CompleteQuest`][osrlib.crawl.commands.CompleteQuest] followed by the rewards
       in authored order, selectors expanded exactly as a trigger's consequences are.
       On a quest that concludes the adventure the session is in `victory` before the
       first reward is issued, which is why a reward that would resume play there
       drops with a note.

    Everything is evaluated as the walk goes: a flag an earlier firing wrote satisfies
    a later clause's condition in the same batch, and a quest completed earlier in the
    walk is completed for everything after it.

    **Where the interpreter's discipline stops and the referee's ruling begins.** The
    completion rule is checked only after a completion the interpreter itself issued,
    and no pattern matches the quest events, so a referee who completes the last
    objective by hand completes the quest by hand too. For the same reason a
    hand-driven [`CompleteQuest`][osrlib.crawl.commands.CompleteQuest] grants no
    rewards: rewards are this listener reading the quest, and what a replay re-executes
    is the reward commands themselves.

    **When something does not work out**, the run continues and the log says why. A
    rejected consequence or reward is dropped on its own — a spawn that meets an open
    encounter, a grant to a character who is not there — and a
    [`RecordNote`][osrlib.crawl.commands.RecordNote] records the trigger or quest, the
    slot's position and type, and the rejection. If a wipe mid-cascade ends the
    session, the remaining commands land or drop by the ordinary rules of a terminal
    mode. And a cascade is bounded: what a firing or a quest advancement issues is one
    deeper than the event that caused it, matching stops below depth five, and every
    advancement the bound suppresses is recorded as a note instead of being issued —
    no state moves, so a once-only trigger cut short here is still fireable later, and
    a suppressed quest advancement waits for its clause to match again. Clauses are
    edge-triggered on both surfaces: the suppressed edge is gone.

    **What it never does.** It returns no events, because everything it causes is
    already logged by the commands it executed, and it keeps no memory between
    commands. Read what a trigger or a quest did from the command log, the journal,
    `session.fired_triggers`, and `session.quests`, all of which a replay rebuilds.
    """

    key = "osrlib.interpreter"
    """The listener key; its state entry exists because registration creates one, and
    is the empty dict for the life of the session."""

    def __init__(self, session: GameSession) -> None:
        """Bind the interpreter to the session it watches and issues commands through.

        Args:
            session: The session; its adventure's triggers and quests are read once
                here, being frozen content.
        """
        self._session = session
        self._triggers = session.adventure.triggers
        self._quests = session.adventure.quests
        self._depth = 0

    def handle(self, events: Sequence[Event], state: dict) -> tuple[list[Event], dict]:
        """Match one command's events and act on what they crossed.

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
                    self._truncated(_Owner("trigger", trigger.id), "not fired", depth)
                    continue
                self._fire(trigger, depth)
            for quest in self._quests:
                self._advance(quest, event, depth)
        return [], {}

    def _would_fire(self, trigger: TriggerSpec, event: Event) -> bool:
        """Whether this trigger fires on this event: pattern, fired-state, conditions."""
        if not _matches(trigger.when, event, self._session):
            return False
        if trigger.id in self._session.fired_triggers and not trigger.repeatable:
            return False
        return self._conditions_hold(trigger.conditions)

    def _clause_holds(self, clause: TriggerClause, event: Event) -> bool:
        """Whether one quest clause fits this event and holds against state right now.

        The same two questions a trigger answers, asked through the same matcher and
        the same evaluation, so quests and triggers can never disagree about what an
        event means or when a condition is true.
        """
        return _matches(clause.pattern, event, self._session) and self._conditions_hold(clause.conditions)

    def _conditions_hold(self, conditions: Sequence[ConditionSpec]) -> bool:
        """Whether every condition holds against live session state, right now."""
        return all(
            condition_holds(
                condition,
                members=self._session.party.members,
                flags=self._session.flags,
                ledger=self._session.ledger,
            )
            for condition in conditions
        )

    def _fire(self, trigger: TriggerSpec, depth: int) -> None:
        """Issue one firing's whole batch, one level deeper than the event that fired it."""
        owner = _Owner("trigger", trigger.id)
        narrative = trigger.narrative
        previous = self._depth
        self._depth = depth + 1
        try:
            self._issue(
                MarkTriggerFired(trigger_id=trigger.id, narrative=(narrative.fired or None) if narrative else None),
                owner,
            )
            self._issue_authored(trigger.consequences, owner, "consequence")
            if narrative is not None and narrative.journal:
                self._issue(AddJournalEntry(text=narrative.journal), owner)
        finally:
            self._depth = previous

    # ------------------------------------------------------------------ quests

    def _advance(self, quest: QuestSpec, event: Event, depth: int) -> None:
        """Walk one quest against one event: activation, reveals, completions, the rule.

        Everything the walk issues runs one level deeper than the event that caused it,
        exactly as a firing does; past the bound the walk still evaluates every clause
        and records what it would have issued instead of issuing it.
        """
        owner = _Owner("quest", quest.id)
        state = self._session.quests.get(quest.id)
        if state is None:
            return
        previous = self._depth
        self._depth = depth + 1
        try:
            if state.status == "inactive":
                if quest.activation is None or not self._clause_holds(quest.activation, event):
                    return
                if depth > _MAX_MATCH_DEPTH:
                    self._truncated(owner, "not activated", depth)
                    return
                self._issue(ActivateQuest(quest_id=quest.id), owner)
            if state.status != "active":
                return
            for objective in quest.objectives:
                objective_state = state.objectives.get(objective.id)
                if objective_state is None or objective_state.complete:
                    continue
                if (
                    objective.hidden
                    and not objective_state.revealed
                    and objective.reveal_when is not None
                    and self._clause_holds(objective.reveal_when, event)
                ):
                    if depth > _MAX_MATCH_DEPTH:
                        self._truncated(owner, f"objective {objective.id} not revealed", depth)
                    else:
                        self._issue(RevealObjective(quest_id=quest.id, objective_id=objective.id), owner)
                if not self._clause_holds(objective.when, event):
                    continue
                if depth > _MAX_MATCH_DEPTH:
                    self._truncated(owner, f"objective {objective.id} not completed", depth)
                    continue
                self._issue(CompleteObjective(quest_id=quest.id, objective_id=objective.id), owner)
                # The rule is checked the moment a completion lands, and only after one
                # this walk issued: a completion by hand is a ruling by hand.
                if state.status == "active" and self._rule_satisfied(quest, state):
                    self._issue(CompleteQuest(quest_id=quest.id), owner)
                    self._issue_authored(quest.rewards, owner, "reward")
                    return
        finally:
            self._depth = previous

    @staticmethod
    def _rule_satisfied(quest: QuestSpec, state: QuestState) -> bool:
        """Whether the quest's completion rule holds against live objective state."""
        done = []
        for objective in quest.objectives:
            objective_state = state.objectives.get(objective.id)
            done.append(objective_state is not None and objective_state.complete)
        return any(done) if quest.completion == "any" else all(done)

    # ------------------------------------------------------------------ issuing

    def _issue_authored(self, authored: Sequence[Command], owner: _Owner, slot: str) -> None:
        """Issue an authored sequence — a trigger's consequences, a quest's rewards.

        In authored order, selectors expanded to the members they name, each command
        standing or dropping on its own so one rejection never stops the rest.
        """
        for position, command in enumerate(authored):
            site = f"{slot} {position}"
            for expanded in self._expand(command, owner, site):
                result = self._issue(expanded, owner)
                if not result.accepted:
                    self._note_drop(owner, site, expanded, result.rejections[0].code)

    def _expand(self, consequence: Command, owner: _Owner, site: str) -> list[Command]:
        """Resolve an authored command's party selector into the commands it stands for.

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
                self._note_drop(owner, site, consequence, f"no living member for {FIRST_LIVING_SELECTOR}")
                return []
            return [consequence.model_copy(update={"character_id": living[0].id})]
        return [consequence]

    def _note_drop(self, owner: _Owner, site: str, command: Command, reason: str) -> None:
        """Record an authored command that did not land, from the facts alone."""
        self._issue(RecordNote(text=f"{owner.label}: {site} ({command.command_type}) dropped ({reason})"), owner)

    def _truncated(self, owner: _Owner, what: str, depth: int) -> None:
        """Record what the cascade bound suppressed; nothing moved, so it can happen again."""
        self._issue(
            RecordNote(
                text=(f"{owner.label}: {what}, the cascade reached depth {depth} past the limit of {_MAX_MATCH_DEPTH}")
            ),
            owner,
        )

    def _issue(self, command: Command, owner: _Owner) -> CommandResult:
        """Execute one command on the trigger's or quest's behalf, stamped with its id.

        Commands are frozen, so the stamp is a copy — the authored consequence in the
        document is never touched.
        """
        return self._session.execute(command.model_copy(update={"source": owner.stamp}))
