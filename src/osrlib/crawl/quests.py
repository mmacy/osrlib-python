"""Authored quests, their objectives, and the clauses that start and finish them.

A quest is an errand the session tracks: what starts it, what it asks for,
what it pays, and whether finishing it ends the adventure.

You write [`QuestSpec`][osrlib.crawl.quests.QuestSpec]s into the
`quests` tuple of an [`Adventure`][osrlib.crawl.adventure.Adventure], and that tuple's
order is document order. A session seeds one
[`QuestState`][osrlib.crawl.session.QuestState] per quest at construction and keeps them
in `session.quests`, keyed by quest id, with each quest's objectives keyed in the order
[`QuestSpec.objectives`][osrlib.crawl.quests.QuestSpec] lists them, so every walk over
either is deterministic. Nothing advances that state until your game registers an
[`Interpreter`][osrlib.crawl.interpreter.Interpreter] on the session, and even then the
state moves only through the quest lifecycle commands
[`ActivateQuest`][osrlib.crawl.commands.ActivateQuest],
[`RevealObjective`][osrlib.crawl.commands.RevealObjective],
[`CompleteObjective`][osrlib.crawl.commands.CompleteObjective], and
[`CompleteQuest`][osrlib.crawl.commands.CompleteQuest]. Each one emits a
player-visible event:
[`QuestActivatedEvent`][osrlib.crawl.events.QuestActivatedEvent],
[`ObjectiveRevealedEvent`][osrlib.crawl.events.ObjectiveRevealedEvent],
[`ObjectiveCompletedEvent`][osrlib.crawl.events.ObjectiveCompletedEvent],
[`QuestCompletedEvent`][osrlib.crawl.events.QuestCompletedEvent], and, for the quest
that concludes the adventure,
[`AdventureCompletedEvent`][osrlib.crawl.events.AdventureCompletedEvent]. The active
quests and their revealed objectives reach a front end through
[`PlayerView.quests`][osrlib.crawl.views.PlayerView].

A quest uses the trigger vocabulary rather than a separate one. An
activation, an objective's completion, and a hidden objective's reveal are each a
[`TriggerClause`][osrlib.crawl.quests.TriggerClause]: a
[`TriggerPattern`][osrlib.crawl.triggers.TriggerPattern] that identifies the event, plus the
[`ConditionSpec`][osrlib.crawl.gates.ConditionSpec]s that have to hold when it matches.
An authored [`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec] uses the same patterns, which
match events rather than states, and evaluates its conditions the same way. Rewards are the same
[`ConsequenceCommand`][osrlib.crawl.commands.ConsequenceCommand] types under the same
party selectors ([`PARTY_SELECTOR`][osrlib.crawl.triggers.PARTY_SELECTOR] and
[`FIRST_LIVING_SELECTOR`][osrlib.crawl.triggers.FIRST_LIVING_SELECTOR]).

A quest only observes events. A clause condition with `consumes=True` is rejected at
parse for the reason a trigger's is: the event a clause matches has already happened,
so the quest has no attempt to charge a toll against.

Author a trigger instead when nothing has to be scored and the adventure only has to
react. The guide
[Gates, triggers, and quests](https://mmacy.github.io/osrlib-python/guides/gates-triggers-quests/)
runs a quest end to end from an adventure document.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from osrlib.crawl.commands import ConsequenceCommand
from osrlib.crawl.gates import ConditionSpec
from osrlib.crawl.narrative import NarrativeBlock
from osrlib.crawl.triggers import TriggerPattern

__all__ = [
    "ObjectiveSpec",
    "QuestSpec",
    "TriggerClause",
]


class TriggerClause(BaseModel):
    """A pattern to match, and the conditions that must hold when an event matches it.

    A quest uses clauses in three places, and they behave the same in all three: the
    `activation` of a [`QuestSpec`][osrlib.crawl.quests.QuestSpec], and the `when` and
    `reveal_when` of an [`ObjectiveSpec`][osrlib.crawl.quests.ObjectiveSpec]. The
    [`Interpreter`][osrlib.crawl.interpreter.Interpreter] matches a clause exactly the
    way it matches a [`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec], so a quest and a
    trigger can never disagree about what an event means.

    Examples:
        ```python
        from osrlib.crawl.gates import HasItemCondition
        from osrlib.crawl.quests import TriggerClause
        from osrlib.crawl.triggers import TownEnteredPattern

        # Matches only when the party reaches town with the holy water on someone.
        walked_home_carrying_it = TriggerClause(
            pattern=TownEnteredPattern(),
            conditions=(HasItemCondition(item_id="holy_water"),),
        )
        print(walked_home_carrying_it.model_dump(mode="json")["conditions"])
        # [{'condition_type': 'has_item', 'item_id': 'holy_water', 'consumes': False}]
        ```
    """

    model_config = ConfigDict(frozen=True)

    pattern: TriggerPattern
    """The event pattern the clause matches, one member of
    [`TriggerPattern`][osrlib.crawl.triggers.TriggerPattern]. The field is `pattern`
    rather than `when`, so an objective's completion clause reads
    `objective.when.pattern`."""
    conditions: tuple[ConditionSpec, ...] = ()
    """Extra tests that all have to hold at the moment of the match. The tuple is an AND
    with no combinators, and each condition is evaluated live against session state
    through [`condition_holds`][osrlib.crawl.gates.condition_holds]. A condition with
    `consumes=True` is rejected at parse."""

    @model_validator(mode="after")
    def _conditions_never_consume(self) -> TriggerClause:
        """A clause's conditions are tests, never tolls.

        Consumption is an effect of a *successful command*, reported through that
        command's events. A clause observes an event that has already happened, so a
        toll here would have nothing to charge against.
        """
        for condition in self.conditions:
            if getattr(condition, "consumes", False):
                raise ValueError("a quest condition cannot consume: a quest observes, it does not take")
        return self


class ObjectiveSpec(BaseModel):
    """One objective: what it is called, how it completes, whether it starts hidden, and its text.

    Put your objectives in the `objectives` tuple of a
    [`QuestSpec`][osrlib.crawl.quests.QuestSpec], in the order the quest log should show
    them. Their live state is [`ObjectiveState`][osrlib.crawl.session.ObjectiveState],
    and the revealed ones reach a front end as
    [`ObjectiveView`][osrlib.crawl.views.ObjectiveView]s.

    An objective's state changes one way only. Hidden becomes revealed and incomplete becomes
    complete, and neither goes back, because there's no way to author a repeating objective.

    A hidden objective with no `reveal_when` is normal. It appears on the list when it
    completes, because completing an objective reveals it. `reveal_when` on an objective
    that starts visible is rejected at parse, since a reveal clause for something already
    on the list would never be read.

    Examples:
        ```python
        from osrlib.crawl.narrative import NarrativeBlock
        from osrlib.crawl.quests import ObjectiveSpec, TriggerClause
        from osrlib.crawl.triggers import ItemAcquiredPattern

        recover = ObjectiveSpec(
            id="recover-flask",
            name="Recover the flask",
            when=TriggerClause(pattern=ItemAcquiredPattern(item_id="holy_water")),
            narrative=NarrativeBlock(progress="The flask is yours, and the shrine is quiet again."),
        )
        print(recover.hidden, recover.reveal_when)  # visible from the start, so no reveal clause
        # False None
        ```
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    """The objective's id, unique within its quest and free to repeat in another. It is
    the key its state and its view use, and the label everything falls back to when
    `name` is unauthored."""
    name: str = ""
    """The objective's display label, the words a quest log shows beside its checkbox. Empty
    means unauthored, and then the view, the lifecycle events, and the default formatter all
    show the id instead."""
    when: TriggerClause
    """The clause that completes the objective. Completing it also reveals it, so a
    hidden objective needs no reveal clause to show up once it is done."""
    hidden: bool = False
    """Whether the objective starts off the party's list. A hidden objective has no view
    until it is revealed."""
    reveal_when: TriggerClause | None = None
    """The clause that reveals a hidden objective ahead of its completion. It is rejected
    at parse on an objective that starts visible."""
    narrative: NarrativeBlock | None = None
    """The objective's beats. osrlib reads two of the block's fields: `offer`, the line its reveal
    shows and journals, and `progress`, the line its completion shows and journals."""

    @model_validator(mode="after")
    def _only_a_hidden_objective_reveals(self) -> ObjectiveSpec:
        """A reveal clause belongs to an objective the party cannot see yet."""
        if self.reveal_when is not None and not self.hidden:
            raise ValueError(f"objective {self.id!r} is not hidden, so reveal_when would never be read")
        return self


class QuestSpec(BaseModel):
    """One authored quest: when it starts, what it asks for, and what it pays.

    Put your quests in the `quests` tuple of an
    [`Adventure`][osrlib.crawl.adventure.Adventure], and register an
    [`Interpreter`][osrlib.crawl.interpreter.Interpreter] on the session to play them. A
    spec on its own is inert data. Its live state is
    [`QuestState`][osrlib.crawl.session.QuestState] in `session.quests`, and an active
    quest reaches a front end as a [`QuestView`][osrlib.crawl.views.QuestView].

    Rewards are issued after the quest completes, in authored order, and only when the
    interpreter itself ruled the quest complete. A quest your game completes by hand with
    [`CompleteQuest`][osrlib.crawl.commands.CompleteQuest] pays nothing.

    Examples:
        A quest that starts when the party enters the crypt and ends when someone picks up a
        flask of holy water:

        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.commands import AwardXP, EnterDungeon, GrantItem
        from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
        from osrlib.crawl.interpreter import Interpreter
        from osrlib.crawl.narrative import NarrativeBlock
        from osrlib.crawl.party import Party
        from osrlib.crawl.quests import ObjectiveSpec, QuestSpec, TriggerClause
        from osrlib.crawl.session import GameSession
        from osrlib.crawl.triggers import PARTY_SELECTOR, DungeonEnteredPattern, ItemAcquiredPattern

        recover = ObjectiveSpec(
            id="recover-flask",
            name="Recover the flask",
            when=TriggerClause(pattern=ItemAcquiredPattern(item_id="holy_water")),
        )
        errand = QuestSpec(
            id="the-flask",
            name="The Stolen Flask",
            activation=TriggerClause(pattern=DungeonEnteredPattern(dungeon_id="crypt")),
            objectives=(recover,),
            rewards=(AwardXP(character_id=PARTY_SELECTOR, amount=200),),
            narrative=NarrativeBlock(
                offer="Sister Halda wants her flask of holy water back, and she is not asking twice.",
                completion="The flask returns to its niche. The temple bells answer.",
            ),
        )
        level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
        crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
        town = TownSpec(name="Threshold")
        adventure = Adventure(name="A First Delve", town=town, dungeons=(crypt,), quests=(errand,))

        rules = Ruleset()
        stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
        hero = create_character(
            name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=rules, stream=stream
        ).character
        session = GameSession.new(Party(members=[hero]), adventure, seed=7)
        session.register_listener(Interpreter(session))

        session.execute(EnterDungeon(dungeon_id="crypt"))
        print(session.quests["the-flask"].status)
        # active

        # Stands in for the party finding the flask in play.
        result = session.execute(GrantItem(character_id=hero.id, item_id="holy_water"))
        for event in result.events:
            print(event.code)
        # exploration.item.acquired
        # session.quest.objective_completed
        # session.quest.completed
        # session.xp.awarded
        ```
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    """The quest's id, unique across the adventure. It keys the quest's state, and the
    `source` stamp on every command the quest issues contains it, in the form
    `quest:{id}`."""
    name: str = Field(min_length=1)
    """The quest's display name, included in its lifecycle events and its view so a
    renderer needs no document to look it up in."""
    activation: TriggerClause | None = None
    """The clause that brings the quest into play. `None` means the quest is active from
    session start, from round 0, with no activation beat to show, because no command has
    run yet to report one. With an authored clause, the quest
    activates during play, when the clause matches."""
    objectives: tuple[ObjectiveSpec, ...] = Field(min_length=1)
    """What the quest asks for, in the order a quest log should show it. At least one is
    required, because a quest with no objectives under the `"all"` rule would be
    complete from the start."""
    rewards: tuple[ConsequenceCommand, ...] = ()
    """The referee commands issued after the quest completes, in authored order, with
    [`PARTY_SELECTOR`][osrlib.crawl.triggers.PARTY_SELECTOR] and
    [`FIRST_LIVING_SELECTOR`][osrlib.crawl.triggers.FIRST_LIVING_SELECTOR] expanded to
    the members they select. Each reward is applied or dropped independently. An authored `source` is
    rejected at parse, because the interpreter stamps it for the quest."""
    completion: Literal["all", "any"] = "all"
    """The completion rule: `"all"` requires every objective, `"any"` completes on the first
    objective completed.

    The quest walk stops at the completion it issues, so when one event would complete two
    objectives at once, the second one is left incomplete and finishes on the next event
    that matches its clause. Under `"any"` that is what usually happens, since the first
    objective to complete finishes the quest and the rest stay open."""
    concludes_adventure: bool = False
    """Whether finishing this quest ends the adventure. The session moves to `victory`
    and emits an
    [`AdventureCompletedEvent`][osrlib.crawl.events.AdventureCompletedEvent], which
    happens before the first reward is issued, so a reward that would resume play there
    is dropped with a note."""
    narrative: NarrativeBlock | None = None
    """The quest's beats. osrlib reads two of the block's fields: `offer`, the line its activation
    shows and journals, and `completion`, the line its completion shows and journals.
    Per-objective beats are on the objectives."""

    @model_validator(mode="after")
    def _objective_ids_unique(self) -> QuestSpec:
        """Objective ids are quest-scoped: unique here, free to repeat elsewhere.

        Two quests may both have an objective with the id `"return"`, but one quest may not have two, because
        its state keys its objectives by id.
        """
        ids = [objective.id for objective in self.objectives]
        if len(set(ids)) != len(ids):
            raise ValueError(f"quest {self.id!r}: objective ids must be unique within the quest")
        return self

    @model_validator(mode="after")
    def _rewards_carry_no_source(self) -> QuestSpec:
        """The `source` stamp belongs to whoever issues the command, not the document.

        Rewards are issued stamped with the quest's id, so an authored stamp
        would either be overwritten or, worse, accepted: a line in the log claiming
        a source that never issued the command.
        """
        for position, reward in enumerate(self.rewards):
            if reward.source is not None:
                raise ValueError(f"reward {position} carries a source; the issuing quest stamps it")
        return self
