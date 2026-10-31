"""The listener that plays an adventure's triggers and quests.

[`Interpreter`][osrlib.crawl.interpreter.Interpreter] turns the triggers and quests written
in an adventure document into things that happen in play. A trigger makes something happen
when the party does something, like setting a flag when it enters a dungeon. A quest is a
task with objectives that the party completes in play.

The interpreter reads the `triggers` and `quests` of the
[`Adventure`][osrlib.crawl.adventure.Adventure] the session is playing. It's an ordinary
listener, which you register with
[`GameSession.register_listener`][osrlib.crawl.session.GameSession.register_listener]. It
checks the events of every accepted command against the adventure's
[`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec]s and
[`QuestSpec`][osrlib.crawl.quests.QuestSpec]s. It acts by
executing ordinary referee commands, each one with its `source` set to the trigger or quest
it acted for. What it does appears in the command's events as
[`TriggerFiredEvent`][osrlib.crawl.events.TriggerFiredEvent],
[`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent],
[`QuestActivatedEvent`][osrlib.crawl.events.QuestActivatedEvent],
[`ObjectiveRevealedEvent`][osrlib.crawl.events.ObjectiveRevealedEvent],
[`ObjectiveCompletedEvent`][osrlib.crawl.events.ObjectiveCompletedEvent],
[`QuestCompletedEvent`][osrlib.crawl.events.QuestCompletedEvent],
[`AdventureCompletedEvent`][osrlib.crawl.events.AdventureCompletedEvent], and
[`NoteRecordedEvent`][osrlib.crawl.events.NoteRecordedEvent], plus the events that the
consequences and rewards emit.

Acting only through commands keeps the game replayable. The interpreter returns no events and
stores nothing between commands, so a replay, which runs with no listeners at all, rebuilds the
same game by executing the same command log again. Every effect a trigger or a quest has is a
command in that log, and each of those commands has a `source` field that refers to the trigger
or quest that issued it, like `"trigger:the-door-shuts"`.

Write your own listener instead when you want something that triggers and quests can't
express. The guide
[Listeners and flags](https://mmacy.github.io/osrlib-python/guides/listeners-and-flags/)
covers the listener contract, and
[Gates, triggers, and quests](https://mmacy.github.io/osrlib-python/guides/gates-triggers-quests/)
covers what this one plays.
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
    RemoveCoins,
    RemoveItem,
    RevealObjective,
)
from osrlib.crawl.events import FlagSetEvent, ItemAcquiredEvent, LocationEnteredEvent, MonsterDefeatedEvent
from osrlib.crawl.gates import ConditionSpec, condition_holds, flag_values_equal
from osrlib.crawl.quests import QuestSpec, TriggerClause
from osrlib.crawl.session import GameSession, QuestState, _removal_rejection
from osrlib.crawl.triggers import (
    CARRIER_SELECTOR,
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
"""The deepest events a firing or a quest advancement still acts on. The events of a
player's command are depth 0, and what a firing or a quest advancement issues is one
level deeper than the event that caused it, so an event deeper than this is evaluated
and then recorded as a note instead of being acted on."""


class _Owner(NamedTuple):
    """Whoever the interpreter is acting for: its command stamp and its note label.

    One definition of both forms, shared by triggers and quests, so the stamp on a
    command and the subject of a note can never disagree about who acted.
    """

    kind: str
    """`"trigger"` or `"quest"`."""

    id: str
    """The authored id of that trigger or quest."""

    @property
    def stamp(self) -> str:
        """The `source` on every command this owner causes: `trigger:{id}`, `quest:{id}`."""
        return f"{self.kind}:{self.id}"

    @property
    def label(self) -> str:
        """How a note refers to the owner: `trigger lever-east`, `quest the-idol`."""
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
    """The party arrived on that level of that dungeon, by stair or by dungeon entry.

    The engine reports only the coarsest boundary a crossing passed, so a party coming in
    from town gets a dungeon entry and never a level entry beneath it. Both kinds match here:
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

    The acquisition event reports mundane items by catalog id and magic items by their
    session-scoped instance id, so a magic id matches by resolving the instance
    against the inventory it was just added to.
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
    """A monster of that template was defeated: slain or routed alike."""
    return isinstance(event, MonsterDefeatedEvent) and event.template_id == pattern.template_id


def _matches_flag_set(pattern: FlagSetPattern, event: Event) -> bool:
    """That flag was written, with that value or with any value at all.

    The comparison is against the value the write set, not the value the flag has now. A trigger
    reacts to the write, and a consequence earlier in the same batch may already have written
    the key again.
    """
    if not isinstance(event, FlagSetEvent) or event.key != pattern.key:
        return False
    return pattern.value is None or flag_values_equal(event.value, pattern.value)


def _matches(pattern: TriggerPattern, event: Event, session: GameSession) -> bool:
    """Whether one event satisfies one authored pattern.

    Matching reads the event's facts and never the party's current position: a
    consequence can relocate the party mid-batch, while an event still describes the
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
    """Plays an adventure's triggers and quests by executing referee commands.

    Register one, once, on a session that has already been built:

    ```{.python .no-run}
    session.register_listener(Interpreter(session))
    ```

    Registering it twice fires everything twice, as it would for any listener. Register it again
    on a session restored from a save, because listeners are code and a save contains only data.
    There's no state to restore: the interpreter's entry in `listener_state` is empty and stays
    empty for the life of the session.

    For each accepted command, the interpreter goes through the command's events in the order
    they happened. For each event, it checks the adventure's triggers in the order the
    adventure lists them, and then its quests in the same way.

    A trigger matches an event when all of these are true:

    - Its pattern fits the event.
    - It hasn't fired yet, or it's `repeatable`.
    - Every one of its conditions holds against the session's state at that moment.

    A trigger that matches fires at once, before the next trigger is checked. So a later
    trigger's conditions are checked against what an earlier firing has already changed.

    A firing executes these commands, each with `source="trigger:{id}"`:

    1. [`MarkTriggerFired`][osrlib.crawl.commands.MarkTriggerFired], with the narrative's
       `fired` text. It comes first, so a trigger that fires only once can't fire again on the
       events of its consequences.
    2. The consequences, in the order the trigger lists them. The interpreter replaces the
       selectors `@party`, `@first`, and `@carrier` with the ids of the living members they
       select, so the log records real character ids and a replay matches.
    3. [`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry] with the narrative's
       `journal` text, when it has one. It comes last, so the entry records the clock time
       after the consequences.

    A quest clause ([`TriggerClause`][osrlib.crawl.quests.TriggerClause]) matches an event the
    same way a trigger does, with the same patterns and conditions. For a quest, the interpreter
    executes these commands, each with `source="quest:{id}"`, and checks the quest in this order:

    1. For an inactive quest whose activation clause matches, the interpreter executes
       [`ActivateQuest`][osrlib.crawl.commands.ActivateQuest], and the check goes on to the
       objectives of the quest it just activated. The same event that starts a quest can
       complete an objective in it.
    2. The objectives of an active quest are checked in the order the quest lists them. For a
       hidden objective that hasn't been revealed or completed, the interpreter executes
       [`RevealObjective`][osrlib.crawl.commands.RevealObjective] when its `reveal_when`
       matches. For an incomplete objective, it executes
       [`CompleteObjective`][osrlib.crawl.commands.CompleteObjective] when its `when` matches.
       An objective that's completed before it's revealed doesn't need a reveal, because
       completing an objective reveals it.
    3. As soon as an objective completes, the quest's completion rule (`all` or `any`) is
       checked against the objectives' state. When the rule holds, the interpreter executes
       [`CompleteQuest`][osrlib.crawl.commands.CompleteQuest], followed by the rewards in the
       order the quest lists them, with selectors replaced as they are for a trigger's
       consequences. When the quest concludes the adventure, the session is already in `victory`
       before the first reward, so a reward that is illegal there, like spawning monsters, is
       dropped with a note.

    Each check runs against the state the earlier commands left. A flag an earlier firing set can
    satisfy a later clause's condition for the same event, and a quest completed earlier in
    the check stays completed for everything after it.

    The interpreter doesn't check a completed quest again, so an objective that's still
    incomplete when its quest completes stays incomplete.

    The interpreter doesn't follow up on quest commands you execute yourself. It checks
    the completion rule only after a completion it issued, and no pattern matches the quest
    events, so if you complete the last objective yourself, complete the quest yourself too. For
    the same reason, a [`CompleteQuest`][osrlib.crawl.commands.CompleteQuest] command you
    execute grants no rewards. Only the interpreter issues rewards, and a replay executes the
    reward commands themselves.

    When a command fails, the interpreter drops it, goes on with the others, and records why
    in a [`RecordNote`][osrlib.crawl.commands.RecordNote]. The note gives the trigger or
    quest, the dropped command's position and type, and the rejection code. These are some
    commands it drops:

    - A [`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty] while an encounter is already
      open.
    - A grant to a character who isn't in the party.
    - A [`RemoveCoins`][osrlib.crawl.commands.RemoveCoins] toll that the first living member
      can't pay.
    - A command whose selector selects nobody, like `@carrier` when no living member carries
      the item. Its note gives that reason instead of a rejection code.

    If the party dies partway through and the session ends, the remaining commands are
    accepted or dropped by the ordinary rules of the `game_over` mode.

    A chain of reactions has a limit. The events of the command you execute are at depth 0,
    and the events of each command the interpreter executes are one level deeper than the
    event that caused it. For an event at depth five or deeper, the interpreter issues
    nothing. It records a note for each command it would have issued instead, and changes no
    state, so a trigger that fires only once can still fire on a later event.

    It returns no events, because the commands it executed already logged everything it
    caused, and it stores nothing between commands. Read what a trigger or a quest did from
    the command log, the journal, `session.fired_triggers`, and `session.quests`, all of
    which a replay rebuilds.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.commands import EnterDungeon, SetFlag
        from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
        from osrlib.crawl.interpreter import Interpreter
        from osrlib.crawl.narrative import NarrativeBlock
        from osrlib.crawl.party import Party
        from osrlib.crawl.session import GameSession
        from osrlib.crawl.triggers import DungeonEnteredPattern, TriggerSpec

        rules = Ruleset()
        rng = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
        hero = create_character(
            name="Hild",
            class_id="fighter",
            alignment=Alignment.LAWFUL,
            ruleset=rules,
            stream=rng,
        )
        level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
        crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
        door_shuts = TriggerSpec(
            id="the-door-shuts",
            when=DungeonEnteredPattern(dungeon_id="crypt"),
            consequences=(SetFlag(key="crypt.entered", value=True),),
            narrative=NarrativeBlock(
                fired="The door shuts behind the party.",
                journal="The crypt door shut behind us.",
            ),
        )
        adventure = Adventure(
            name="A First Delve",
            town=TownSpec(name="Threshold"),
            dungeons=(crypt,),
            triggers=(door_shuts,),
        )
        session = GameSession.new(Party(members=[hero.character]), adventure, seed=7)
        session.register_listener(Interpreter(session))
        session.execute(EnterDungeon(dungeon_id="crypt"))

        print(session.fired_triggers)
        # ['the-door-shuts']
        print(session.flags)
        # {'crypt.entered': True}
        print([entry.text for entry in session.journal])
        # ['The crypt door shut behind us.']
        ```
    """

    key = "osrlib.interpreter"
    """The key of this listener's entry in the session's `listener_state`.
    Registering the interpreter creates the entry, and it's an empty dict for the life of the
    session, because the interpreter stores nothing between commands."""

    def __init__(self, session: GameSession) -> None:
        """Bind the interpreter to the session it watches and issues commands through.

        Construct it after the session exists, pass it straight to
        [`GameSession.register_listener`][osrlib.crawl.session.GameSession.register_listener],
        and do the same again after loading a save. Each interpreter works with one session.

        Args:
            session: The session to play. The constructor reads its adventure's triggers and
                quests once, because an adventure doesn't change.
        """
        self._session = session
        self._triggers = session.adventure.triggers
        self._quests = session.adventure.quests
        self._depth = 0

    def handle(self, events: Sequence[Event], state: dict) -> tuple[list[Event], dict]:
        """Check one command's events against the triggers and quests, and act on the matches.

        The session calls this after every accepted command, and you don't call it
        yourself. Every listener has this method, so its arguments show what the session
        passes a listener of your own.

        Args:
            events: The command's events in the order they happened, followed by the events
                that listeners registered before this one returned.
            state: The interpreter's entry in `listener_state`, always an empty dict.

        Returns:
            No events and the empty state. Everything the interpreter does is a command
                it executed, and it stores nothing between commands.
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

        These are the same two checks a trigger gets, made through the same matcher and
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
        exactly as a firing does. Past the bound, the walk still evaluates every clause of an
        active quest and records what it would have issued instead of issuing it. For a quest
        the event would have activated, it records one note for the activation and checks
        none of the quest's objectives. A clause matches an event, not a state, so the event
        the walk skipped doesn't match again later.

        The walk stops at the completion it issues. The rewards go out, and the quest's
        remaining objectives aren't checked for this event, even if their clauses match it.
        Under the `any` rule that's the usual case. Under the `all` rule it can't happen,
        because the quest completes only after every objective has.
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
                # The rule is checked as soon as an objective completes, and only after a
                # completion this walk issued, so a game that completes an objective by hand
                # completes the quest by hand.
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
        """Issue an authored sequence: a trigger's consequences, or a quest's rewards.

        In authored order, selectors expanded to the members they select, each command
        accepted or dropped independently so one rejection never stops the rest.
        """
        for position, command in enumerate(authored):
            site = f"{slot} {position}"
            for expanded in self._expand(command, owner, site):
                result = self._issue(expanded, owner)
                if not result.accepted:
                    self._note_drop(owner, site, expanded, result.rejections[0].code)

    def _expand(self, consequence: Command, owner: _Owner, site: str) -> list[Command]:
        """Resolve an authored command's party selector into the commands it stands for.

        A literal character id isn't a selector, so the method passes it through unchanged. The
        session rejects it like any other bad id, and the interpreter drops the command and notes
        it. The carrier selector on any command but a `RemoveItem` goes the same way.
        """
        if not isinstance(consequence, GrantItem | GrantCoins | AwardXP | RemoveItem | RemoveCoins):
            return [consequence]
        living = self._session.party.living_members()
        if consequence.character_id == PARTY_SELECTOR:
            return [consequence.model_copy(update={"character_id": member.id}) for member in living]
        if consequence.character_id == FIRST_LIVING_SELECTOR:
            if not living:
                self._note_drop(owner, site, consequence, f"no living member for {FIRST_LIVING_SELECTOR}")
                return []
            return [consequence.model_copy(update={"character_id": living[0].id})]
        if consequence.character_id == CARRIER_SELECTOR and isinstance(consequence, RemoveItem):
            # The carrier is the first living member the removal would succeed against. The check
            # is the one the session runs, so the session never refuses the expanded command.
            carrier = next(
                (
                    member
                    for member in living
                    if _removal_rejection(member, consequence.item_id, consequence.quantity) is None
                ),
                None,
            )
            if carrier is None:
                self._note_drop(owner, site, consequence, f"no living member carries it for {CARRIER_SELECTOR}")
                return []
            return [consequence.model_copy(update={"character_id": carrier.id})]
        return [consequence]

    def _note_drop(self, owner: _Owner, site: str, command: Command, reason: str) -> None:
        """Record an authored command that was dropped, from the facts alone."""
        self._issue(RecordNote(text=f"{owner.label}: {site} ({command.command_type}) dropped ({reason})"), owner)

    def _truncated(self, owner: _Owner, what: str, depth: int) -> None:
        """Record what the depth limit suppressed. No state changed, so it can happen again."""
        self._issue(
            RecordNote(
                text=(f"{owner.label}: {what}, the cascade reached depth {depth} past the limit of {_MAX_MATCH_DEPTH}")
            ),
            owner,
        )

    def _issue(self, command: Command, owner: _Owner) -> CommandResult:
        """Execute one command on the trigger's or quest's behalf, stamped with its id.

        Commands are frozen, so the stamp is a copy, and the authored consequence in
        the document is never touched.
        """
        return self._session.execute(command.model_copy(update={"source": owner.stamp}))
