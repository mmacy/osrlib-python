"""Authored quests, their objectives, and the clauses that start and finish them.

A quest is a task the session tracks for the party: what starts it, what it asks for, what it
pays, and whether finishing it ends the adventure.

You write [`QuestSpec`][osrlib.crawl.quests.QuestSpec]s into the `quests` tuple of an
[`Adventure`][osrlib.crawl.adventure.Adventure], each with one or more
[`ObjectiveSpec`][osrlib.crawl.quests.ObjectiveSpec]s. When a session starts, it creates one
[`QuestState`][osrlib.crawl.session.QuestState] for each quest and stores them in
`session.quests`, keyed by quest id and in the order of the tuple. Each quest's objective
states are in the order of [`QuestSpec.objectives`][osrlib.crawl.quests.QuestSpec.objectives].

Quests don't advance until you register an
[`Interpreter`][osrlib.crawl.interpreter.Interpreter] on the session. The interpreter advances
quest state with the quest commands
[`ActivateQuest`][osrlib.crawl.commands.ActivateQuest],
[`RevealObjective`][osrlib.crawl.commands.RevealObjective],
[`CompleteObjective`][osrlib.crawl.commands.CompleteObjective], and
[`CompleteQuest`][osrlib.crawl.commands.CompleteQuest]. Those commands emit player-visible
events: [`QuestActivatedEvent`][osrlib.crawl.events.QuestActivatedEvent],
[`ObjectiveRevealedEvent`][osrlib.crawl.events.ObjectiveRevealedEvent],
[`ObjectiveCompletedEvent`][osrlib.crawl.events.ObjectiveCompletedEvent],
[`QuestCompletedEvent`][osrlib.crawl.events.QuestCompletedEvent], and, for a quest that ends
the adventure, [`AdventureCompletedEvent`][osrlib.crawl.events.AdventureCompletedEvent]. To
show the active quests and their revealed objectives, read
[`PlayerView.quests`][osrlib.crawl.views.PlayerView].

The interpreter matches a quest's clauses the same way it matches a trigger. A quest's activation,
an objective's completion, and a hidden objective's reveal are each a
[`TriggerClause`][osrlib.crawl.quests.TriggerClause]. A clause pairs a
[`TriggerPattern`][osrlib.crawl.triggers.TriggerPattern], the event to react to,
with [`ConditionSpec`][osrlib.crawl.gates.ConditionSpec]s that have to hold when the event
matches. A [`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec] uses the same patterns and
conditions. A quest's rewards are the same
[`ConsequenceCommand`][osrlib.crawl.commands.ConsequenceCommand]s a trigger runs, with the same
party selectors ([`PARTY_SELECTOR`][osrlib.crawl.triggers.PARTY_SELECTOR] and
[`FIRST_LIVING_SELECTOR`][osrlib.crawl.triggers.FIRST_LIVING_SELECTOR]).

A quest's clauses only match events. A clause condition with `consumes=True` fails validation for the
same reason a trigger's does: the event the clause matches has already happened, so there's no
attempt to charge a toll for.

Write a trigger instead when you only need the adventure to react to an event, and there's no
progress to track. To follow a quest from start to finish in an adventure document, see
[Gates, triggers, and quests](https://mmacy.github.io/osrlib-python/guides/gates-triggers-quests/).
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

    A quest has clauses in three places, and they work the same way in all three: the
    `activation` of a [`QuestSpec`][osrlib.crawl.quests.QuestSpec], and the `when` and
    `reveal_when` of an [`ObjectiveSpec`][osrlib.crawl.quests.ObjectiveSpec]. The
    [`Interpreter`][osrlib.crawl.interpreter.Interpreter] matches a clause the same way it
    matches a [`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec], so an event that matches a
    trigger's pattern and conditions also matches a clause with that pattern and those
    conditions.

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
    """The event the clause matches, as one member of
    [`TriggerPattern`][osrlib.crawl.triggers.TriggerPattern]. The field is named `pattern`,
    not `when`, so an objective's completion pattern is `objective.when.pattern`."""
    conditions: tuple[ConditionSpec, ...] = ()
    """Extra tests, every one of which has to hold when an event matches `pattern`. There's
    no way to write "or". The interpreter evaluates each condition against the session at
    that moment with [`condition_holds`][osrlib.crawl.gates.condition_holds]. A condition
    with `consumes=True` fails validation with a pydantic `ValidationError`."""

    @model_validator(mode="after")
    def _conditions_never_consume(self) -> TriggerClause:
        """A clause's conditions are tests, never tolls.

        Consumption is an effect of a *successful command*, reported through that
        command's events. A clause matches an event that has already happened, so a
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
    them. The session tracks each objective in play as an
    [`ObjectiveState`][osrlib.crawl.session.ObjectiveState], and you get the revealed ones
    as [`ObjectiveView`][osrlib.crawl.views.ObjectiveView]s.

    An objective's state changes in one direction only. A hidden objective becomes
    revealed, an incomplete one becomes complete, and neither change is undone. There's no
    way to write an objective that repeats.

    A hidden objective with no `reveal_when` is normal. It appears in the quest log when it
    completes, because completing an objective reveals it. `reveal_when` on an objective
    that starts visible fails validation with a pydantic `ValidationError`, because the
    interpreter would never read it.

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
        print(recover.hidden, recover.reveal_when)  # visible from the start, so it has no reveal clause
        # False None
        ```
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    """The objective's id. It has to be unique within its quest, and another quest can use
    the same id. The objective's state and view use it as their key, and it's shown in
    place of `name` when `name` is empty."""
    name: str = ""
    """The objective's display name, the words a quest log shows beside its checkbox. When
    it's empty, the view, the quest events, and the default message text use the id
    instead."""
    when: TriggerClause
    """The clause that completes the objective. Completing an objective also reveals it,
    so a hidden objective needs no reveal clause to appear once it's done."""
    hidden: bool = False
    """Whether the objective starts hidden from the party. A hidden objective has no view
    until it's revealed."""
    reveal_when: TriggerClause | None = None
    """The clause that reveals a hidden objective before it completes. On an objective that
    starts visible, it fails validation with a pydantic `ValidationError`."""
    narrative: NarrativeBlock | None = None
    """The objective's text. osrlib reads two fields of the block: `offer`, the line shown
    and added to the journal when the objective is revealed, and `progress`, the line shown
    and added to the journal when it completes."""

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
    [`Interpreter`][osrlib.crawl.interpreter.Interpreter] on the session to run them. The
    interpreter is the only part of osrlib that reads a quest, so without it the quest
    doesn't advance. The session tracks each quest in play as a
    [`QuestState`][osrlib.crawl.session.QuestState] in `session.quests`, and you get each
    active quest as a [`QuestView`][osrlib.crawl.views.QuestView].

    The interpreter runs the rewards after the quest completes, in the order you write
    them, and only when the interpreter completed the quest. A quest you complete
    with [`CompleteQuest`][osrlib.crawl.commands.CompleteQuest] pays no rewards.

    Examples:
        A quest that starts when the party enters the crypt and ends when a member picks up a
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
    """The quest's id, unique across the adventure. `session.quests` uses it as the key, and
    every command the interpreter runs for the quest has `source` set to `quest:{id}`."""
    name: str = Field(min_length=1)
    """The quest's display name. The quest events and the quest's view include it, so you
    can show it without the adventure document."""
    activation: TriggerClause | None = None
    """The clause that starts the quest. With `None`, the quest is active from the start of
    the session. No command starts it, so the session emits no
    [`QuestActivatedEvent`][osrlib.crawl.events.QuestActivatedEvent] for it, but its
    [`QuestView`][osrlib.crawl.views.QuestView] still includes the `offer` text. With a clause,
    the quest starts when an event matches the clause."""
    objectives: tuple[ObjectiveSpec, ...] = Field(min_length=1)
    """What the quest asks for, in the order a quest log should show the objectives. At least
    one is required, because a quest with no objectives under the `"all"` rule would be
    complete from the start."""
    rewards: tuple[ConsequenceCommand, ...] = ()
    """The referee commands the interpreter runs after the quest completes, in the order you
    write them. The interpreter replaces
    [`PARTY_SELECTOR`][osrlib.crawl.triggers.PARTY_SELECTOR] and
    [`FIRST_LIVING_SELECTOR`][osrlib.crawl.triggers.FIRST_LIVING_SELECTOR] with the members
    they select. When the session rejects one reward, the interpreter drops it, records a
    note, and runs the rest. A reward with `source` set fails validation with a pydantic
    `ValidationError`, because the interpreter sets `source` itself."""
    completion: Literal["all", "any"] = "all"
    """The rule for completing the quest. With `"all"`, the quest completes when every
    objective is complete. With `"any"`, it completes when the first objective completes.

    Once the quest is complete, its other objectives never change. Under `"any"`, an
    objective that wasn't complete stays incomplete, even when the same event matched its
    clause."""
    concludes_adventure: bool = False
    """Whether completing this quest ends the adventure. The session moves to `victory`
    mode and emits an
    [`AdventureCompletedEvent`][osrlib.crawl.events.AdventureCompletedEvent] before the
    interpreter runs the first reward. A reward that `victory` mode doesn't allow, like
    [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters] or
    [`PlaceParty`][osrlib.crawl.commands.PlaceParty], is rejected, and the interpreter drops
    it and records a note. Grants and experience awards still apply."""
    narrative: NarrativeBlock | None = None
    """The quest's text. osrlib reads two fields of the block: `offer`, the line shown and
    added to the journal when the quest starts, and `completion`, the line shown and added
    to the journal when it completes. Each objective's text is in its
    [`ObjectiveSpec.narrative`][osrlib.crawl.quests.ObjectiveSpec.narrative]."""

    @model_validator(mode="after")
    def _objective_ids_unique(self) -> QuestSpec:
        """Objective ids are unique within a quest and can repeat across quests.

        Two quests may both have an objective with the id `"return"`, but one quest may not have two, because
        the quest's state uses objective ids as keys.
        """
        ids = [objective.id for objective in self.objectives]
        if len(set(ids)) != len(ids):
            raise ValueError(f"quest {self.id!r}: objective ids must be unique within the quest")
        return self

    @model_validator(mode="after")
    def _rewards_carry_no_source(self) -> QuestSpec:
        """Whoever issues a command sets its `source`, not the document.

        The interpreter issues rewards with the quest's id as their `source`, so a source written
        in the document would either be overwritten or be accepted, and the log would then show
        a source that never issued the command.
        """
        for position, reward in enumerate(self.rewards):
            if reward.source is not None:
                raise ValueError(f"reward {position} carries a source; the issuing quest stamps it")
        return self
