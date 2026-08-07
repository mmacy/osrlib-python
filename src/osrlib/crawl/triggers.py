"""Authored triggers: the observable-event patterns and the trigger spec.

A trigger is an authored binding from an observable event pattern, optionally
gated by conditions, to referee-command consequences. Where a gate
([`GateSpec`][osrlib.crawl.gates.GateSpec]) is level-triggered — evaluated live at
the moment the party attempts something — a trigger is edge-triggered: it watches
the events a command produced and fires on the crossing itself. The lever that
opens the portcullis is a trigger; the door that wants the brass key is a gate.

The pieces:

- A **pattern** ([`TriggerPattern`][osrlib.crawl.triggers.TriggerPattern]) names the
  observable: a location crossed, an item acquired, a monster defeated, a flag
  written. Patterns are matched against the events themselves, never against
  current state, because a consequence can move the party mid-batch and an event
  still carries the facts of the moment it described.
- **Conditions** ([`ConditionSpec`][osrlib.crawl.gates.ConditionSpec]) narrow the
  firing further, all of them evaluated live against session state at match time.
  A trigger fires, it does not take: a condition with `consumes=True` is rejected
  at parse, because a trigger reacts to something that has already happened and has
  no attempt of its own to charge a toll against.
- **Consequences** ([`ConsequenceCommand`][osrlib.crawl.commands.ConsequenceCommand])
  are the referee commands the firing issues, in authored order.
- A **narrative block** ([`NarrativeBlock`][osrlib.crawl.narrative.NarrativeBlock])
  carries the trigger's text: `fired` is the referee's beat, `journal` is the
  players' — a trigger that should say something to the table authors a journal
  form.

Document order is the order of the [`Adventure.triggers`][osrlib.crawl.adventure.Adventure]
tuple: triggers matching one event fire in that order, and a trigger's consequences
execute in authored order.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from osrlib.crawl.commands import ConsequenceCommand
from osrlib.crawl.gates import ConditionSpec
from osrlib.crawl.narrative import NarrativeBlock

__all__ = [
    "FIRST_LIVING_SELECTOR",
    "PARTY_SELECTOR",
    "AreaEnteredPattern",
    "DungeonEnteredPattern",
    "FlagSetPattern",
    "ItemAcquiredPattern",
    "LevelEnteredPattern",
    "MonsterDefeatedPattern",
    "TownEnteredPattern",
    "TriggerPattern",
    "TriggerSpec",
]

PARTY_SELECTOR = "@party"
"""The `character_id` an authored consequence writes to address the whole party.

Expanded at issue time to one command per *living* member, in marching order, so the
command log stays fully concrete and replays exactly. A party with nobody left
standing expands to no commands at all — a reward for the dead is nothing, not an
error."""

FIRST_LIVING_SELECTOR = "@first"
"""The `character_id` an authored consequence writes to address one member.

Expanded at issue time to the first living member in marching order — the
treasure-recipient convention. With nobody standing there is no recipient, and the
consequence is dropped with a note rather than guessed at."""


class AreaEnteredPattern(BaseModel):
    """The party entered a keyed area.

    Area ids are level-scoped, so the pattern names the whole triple: an `id` of
    `"crypt"` means nothing without the dungeon and level it belongs to.
    """

    model_config = ConfigDict(frozen=True)

    pattern_type: Literal["area_entered"] = "area_entered"
    dungeon_id: str = Field(min_length=1)
    level_number: int = Field(ge=1)
    area_id: str = Field(min_length=1)


class LevelEnteredPattern(BaseModel):
    """The party arrived on a dungeon level.

    However it got there: a stair between levels and an entry from town both land
    the party on the level, and both match. (The engine reports the coarser crossing
    when a move changes dungeons, and a dungeon crossing is a level arrival too.)
    """

    model_config = ConfigDict(frozen=True)

    pattern_type: Literal["level_entered"] = "level_entered"
    dungeon_id: str = Field(min_length=1)
    level_number: int = Field(ge=1)


class DungeonEnteredPattern(BaseModel):
    """The party crossed into a dungeon — from town, or from another dungeon."""

    model_config = ConfigDict(frozen=True)

    pattern_type: Literal["dungeon_entered"] = "dungeon_entered"
    dungeon_id: str = Field(min_length=1)


class TownEnteredPattern(BaseModel):
    """The party arrived in the base town, however it got there.

    An adventure has one town, so the pattern needs no fields: the homecoming beat
    fires on the return trip and on a referee's placement alike.
    """

    model_config = ConfigDict(frozen=True)

    pattern_type: Literal["town_entered"] = "town_entered"


class ItemAcquiredPattern(BaseModel):
    """A party member acquired an item with `item_id`.

    The id domain is the one `has_item` reads: the effective equipment catalog
    (shipped ∪ adventure-bundled) or the magic-item catalog. Acquisitions report
    mundane items by catalog id and magic items by their session-scoped instance id,
    so a magic `item_id` matches by resolving that instance against the acquiring
    character's inventory.
    """

    model_config = ConfigDict(frozen=True)

    pattern_type: Literal["item_acquired"] = "item_acquired"
    item_id: str = Field(min_length=1)


class MonsterDefeatedPattern(BaseModel):
    """A monster of `template_id` was defeated — slain, routed, or surrendered.

    Every outcome is a defeat, so the pattern does not filter on one. Defeats are
    reported at battle end, so the boss falling opens the portcullis after the
    fighting stops, never mid-round.
    """

    model_config = ConfigDict(frozen=True)

    pattern_type: Literal["monster_defeated"] = "monster_defeated"
    template_id: str = Field(min_length=1)


class FlagSetPattern(BaseModel):
    """A session flag was written — the edge, not the state.

    The match is against the value the write carried, so a flag rewritten with the
    value it already held still fires. `value=None` matches any written value, which
    is unambiguous because a flag value is a `str`, an `int`, or a `bool` and never
    `None`; an authored value compares through
    [`flag_values_equal`][osrlib.crawl.gates.flag_values_equal], the same strict
    comparison [`FlagEqualsCondition`][osrlib.crawl.gates.FlagEqualsCondition] uses.
    """

    model_config = ConfigDict(frozen=True)

    pattern_type: Literal["flag_set"] = "flag_set"
    key: str = Field(min_length=1)
    value: str | int | bool | None = None


TriggerPattern = Annotated[
    AreaEnteredPattern
    | LevelEnteredPattern
    | DungeonEnteredPattern
    | TownEnteredPattern
    | ItemAcquiredPattern
    | MonsterDefeatedPattern
    | FlagSetPattern,
    Field(discriminator="pattern_type"),
]
"""The pattern union, discriminated on `pattern_type`. New observables join it
additively; the discriminator values are wire values and serialize into every
document that carries a trigger."""


class TriggerSpec(BaseModel):
    """One authored trigger: when it fires, what must hold, and what happens.

    Once-only by default — the fired-mark that
    [`MarkTriggerFired`][osrlib.crawl.commands.MarkTriggerFired] writes is session
    state, so once-only survives a save, a load, and a replay. `repeatable=True` is
    the authored opt-in for a trigger that fires every time its pattern matches.

    `conditions` all have to hold: the tuple is an AND with no combinators, each
    condition evaluated live at the moment of the match. `consequences` may be empty
    — a trigger whose whole job is its journal beat is a normal shape.

    Examples:
        ```python
        from osrlib.crawl.commands import SetDoorState
        from osrlib.crawl.dungeon import Direction
        from osrlib.crawl.narrative import NarrativeBlock
        from osrlib.crawl.triggers import FlagSetPattern, TriggerSpec

        portcullis = SetDoorState(dungeon_id="crypt", level_number=1, x=2, y=0, direction=Direction.SOUTH, open=True)
        trigger = TriggerSpec(
            id="portcullis-rises",
            when=FlagSetPattern(key="crypt.lever", value="pulled"),
            consequences=(portcullis,),
            narrative=NarrativeBlock(
                fired="The counterweight drops somewhere in the wall.",
                journal="The east lever gives; below, a portcullis grinds upward.",
            ),
        )
        assert not trigger.repeatable
        ```
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    when: TriggerPattern
    conditions: tuple[ConditionSpec, ...] = ()
    repeatable: bool = False
    consequences: tuple[ConsequenceCommand, ...] = ()
    narrative: NarrativeBlock | None = None

    @model_validator(mode="after")
    def _conditions_never_consume(self) -> TriggerSpec:
        """A trigger's conditions are tests, never tolls.

        Consumption is an effect of a *successful command*, reported through that
        command's events. A trigger observes an event that has already happened, so a
        toll here would have nothing to charge against.
        """
        for condition in self.conditions:
            if getattr(condition, "consumes", False):
                raise ValueError("a trigger condition cannot consume: a trigger fires, it does not take")
        return self

    @model_validator(mode="after")
    def _consequences_carry_no_source(self) -> TriggerSpec:
        """The `source` stamp belongs to whoever issues the command, not the document.

        Consequences are issued stamped with the trigger's own id, so an authored
        stamp would either be overwritten or, worse, believed — a line in the log
        claiming a provenance nothing produced.
        """
        for position, consequence in enumerate(self.consequences):
            if consequence.source is not None:
                raise ValueError(f"consequence {position} carries a source; the issuing trigger stamps it")
        return self
