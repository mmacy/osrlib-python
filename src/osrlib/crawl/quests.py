"""Authored quests: the matching clause, the objective spec, and the quest spec.

A quest composes the trigger vocabulary rather than introducing one of its own. An
activation, an objective's completion, and a hidden objective's reveal are each a
[`TriggerClause`][osrlib.crawl.quests.TriggerClause]: a
[`TriggerPattern`][osrlib.crawl.triggers.TriggerPattern] naming the observable, plus
the [`ConditionSpec`][osrlib.crawl.gates.ConditionSpec]s that must hold when it
matches — the same edge-triggered patterns and the same live condition evaluation an
authored [`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec] uses. Rewards are the
same [`ConsequenceCommand`][osrlib.crawl.commands.ConsequenceCommand] surface under
the same party selectors ([`PARTY_SELECTOR`][osrlib.crawl.triggers.PARTY_SELECTOR]
and [`FIRST_LIVING_SELECTOR`][osrlib.crawl.triggers.FIRST_LIVING_SELECTOR]).

A quest observes; it does not take. A clause condition with `consumes=True` is
rejected at parse for the reason a trigger's is: the event a clause matches has
already happened, so there is no attempt of the quest's own to charge a toll
against.

Document order is the order of the
[`Adventure.quests`][osrlib.crawl.adventure.Adventure] tuple, and an objective's
order is its position in
[`QuestSpec.objectives`][osrlib.crawl.quests.QuestSpec] — the order a session's quest
state ([`QuestState`][osrlib.crawl.session.QuestState]) keys its objectives in, so
every walk over either is deterministic.
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
    """One matching clause: the observable, and what must hold when it happens.

    `conditions` all have to hold: the tuple is an AND with no combinators, each
    condition evaluated live against session state at the moment of the match,
    through [`condition_holds`][osrlib.crawl.gates.condition_holds]. The field is
    `pattern` rather than `when`, so an objective's completion clause reads
    `objective.when.pattern`.

    Examples:
        ```python
        from osrlib.crawl.gates import HasItemCondition
        from osrlib.crawl.quests import TriggerClause
        from osrlib.crawl.triggers import TownEnteredPattern

        walked_home_carrying_it = TriggerClause(
            pattern=TownEnteredPattern(),
            conditions=(HasItemCondition(item_id="holy_water"),),
        )
        assert walked_home_carrying_it.pattern.pattern_type == "town_entered"
        ```
    """

    model_config = ConfigDict(frozen=True)

    pattern: TriggerPattern
    conditions: tuple[ConditionSpec, ...] = ()

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
    """One objective: how it completes, whether it starts hidden, and its text.

    Objectives are monotonic — hidden becomes revealed, incomplete becomes complete,
    and neither goes back — because the quest vocabulary authors no repeat.

    A hidden objective with no `reveal_when` is a normal shape: it surfaces when it
    completes, because completing an objective reveals it. `reveal_when` on an
    objective that starts visible is rejected at parse — a reveal clause for
    something already on the list is authored dead weight.

    `narrative` carries the objective's own beats: `offer` is the line its reveal
    shows and journals, `progress` the line its completion shows and journals.

    Examples:
        ```python
        from osrlib.crawl.narrative import NarrativeBlock
        from osrlib.crawl.quests import ObjectiveSpec, TriggerClause
        from osrlib.crawl.triggers import ItemAcquiredPattern

        recover = ObjectiveSpec(
            id="recover-idol",
            when=TriggerClause(pattern=ItemAcquiredPattern(item_id="holy_water")),
            narrative=NarrativeBlock(progress="The flask is yours; the shrine is quiet again."),
        )
        assert not recover.hidden and recover.reveal_when is None
        ```
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    when: TriggerClause
    hidden: bool = False
    reveal_when: TriggerClause | None = None
    narrative: NarrativeBlock | None = None

    @model_validator(mode="after")
    def _only_a_hidden_objective_reveals(self) -> ObjectiveSpec:
        """A reveal clause belongs to an objective the party cannot see yet."""
        if self.reveal_when is not None and not self.hidden:
            raise ValueError(f"objective {self.id!r} is not hidden, so reveal_when would never be read")
        return self


class QuestSpec(BaseModel):
    """One authored quest: when it starts, what it asks for, and what it pays.

    `activation` absent means the quest is active from session start — a standing
    charge the party carries from round 0, with no activation beat to show, because
    there is no command channel before the first command. An authored clause makes
    activation an event the party crosses.

    `completion` is `"all"` (every objective) or `"any"` (the first one to land).
    `objectives` holds at least one: an objective-less quest under the all rule would
    be born complete. `concludes_adventure=True` marks the quest whose completion
    ends the adventure in `victory`
    ([`CompleteQuest`][osrlib.crawl.commands.CompleteQuest]).

    `rewards` are issued after the quest completes, in authored order, with the party
    selectors expanded to the members they name; an authored `source` is rejected at
    parse, because whoever issues a command stamps it.

    `narrative` carries the quest's own beats: `offer` is the line its activation
    shows and journals, `completion` the line its completion shows and journals.
    Per-objective beats live on the objectives.

    Examples:
        ```python
        from osrlib.crawl.commands import AwardXP
        from osrlib.crawl.narrative import NarrativeBlock
        from osrlib.crawl.quests import ObjectiveSpec, QuestSpec, TriggerClause
        from osrlib.crawl.triggers import PARTY_SELECTOR, DungeonEnteredPattern, ItemAcquiredPattern

        recover = ObjectiveSpec(id="recover", when=TriggerClause(pattern=ItemAcquiredPattern(item_id="holy_water")))
        errand = QuestSpec(
            id="the-flask",
            name="The Stolen Reliquary",
            activation=TriggerClause(pattern=DungeonEnteredPattern(dungeon_id="barrow")),
            objectives=(recover,),
            rewards=(AwardXP(character_id=PARTY_SELECTOR, amount=200),),
            concludes_adventure=True,
            narrative=NarrativeBlock(
                offer="Sister Halda wants the reliquary back, and she is not asking twice.",
                completion="The flask returns to its niche. The temple bells answer.",
            ),
        )
        assert errand.completion == "all"
        ```
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    activation: TriggerClause | None = None
    objectives: tuple[ObjectiveSpec, ...] = Field(min_length=1)
    rewards: tuple[ConsequenceCommand, ...] = ()
    completion: Literal["all", "any"] = "all"
    concludes_adventure: bool = False
    narrative: NarrativeBlock | None = None

    @model_validator(mode="after")
    def _objective_ids_unique(self) -> QuestSpec:
        """Objective ids are quest-scoped: unique here, free to repeat elsewhere.

        Two quests may both name an objective `"return"`; one quest may not, because
        its state keys its objectives by id.
        """
        ids = [objective.id for objective in self.objectives]
        if len(set(ids)) != len(ids):
            raise ValueError(f"quest {self.id!r}: objective ids must be unique within the quest")
        return self

    @model_validator(mode="after")
    def _rewards_carry_no_source(self) -> QuestSpec:
        """The `source` stamp belongs to whoever issues the command, not the document.

        Rewards are issued stamped with the quest's own id, so an authored stamp
        would either be overwritten or, worse, believed — a line in the log claiming
        a provenance nothing produced.
        """
        for position, reward in enumerate(self.rewards):
            if reward.source is not None:
                raise ValueError(f"reward {position} carries a source; the issuing quest stamps it")
        return self
