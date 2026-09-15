"""Authored triggers: the observable-event patterns and the trigger spec.

A trigger binds an observable event pattern, optionally narrowed by conditions, to the
referee commands that run when it matches.

Where a trigger sits. You write [`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec]s into
the `triggers` tuple of an [`Adventure`][osrlib.crawl.adventure.Adventure], and that
tuple's order is document order: triggers matching one event fire in it. Nothing plays
them until your game registers an
[`Interpreter`][osrlib.crawl.interpreter.Interpreter] on the session with
[`GameSession.register_listener`][osrlib.crawl.session.GameSession.register_listener].
The interpreter matches every accepted command's events against the adventure's triggers
and issues each firing's commands. A firing reports itself through a
[`TriggerFiredEvent`][osrlib.crawl.events.TriggerFiredEvent], which includes the `fired`
beat, through a [`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent]
when the block authors a journal form, through whatever events its consequences produce,
and through a [`NoteRecordedEvent`][osrlib.crawl.events.NoteRecordedEvent] for a
consequence the engine refused. The fired-mark lives in `session.fired_triggers`.

A gate ([`GateSpec`][osrlib.crawl.gates.GateSpec]) is level-triggered, evaluated live at
the moment the party attempts something. A trigger is edge-triggered: it watches the
events a command produced and fires on the crossing itself. The lever that opens the
portcullis is a trigger, and the door that needs the brass key is a gate.

The pieces:

- A pattern ([`TriggerPattern`][osrlib.crawl.triggers.TriggerPattern]) names the
  observable: a location crossed, an item acquired, a monster defeated, a flag written.
  Patterns are matched against the events themselves and never against current state,
  because a consequence can move the party inside the same batch while an event keeps
  describing the moment it reported.
- Conditions ([`ConditionSpec`][osrlib.crawl.gates.ConditionSpec]) narrow the firing
  further, all of them evaluated live against session state at match time. A trigger
  fires, it does not take, so a condition with `consumes=True` is rejected at parse: a
  trigger reacts to something that has already happened and has no attempt of its own to
  charge a toll against.
- Consequences ([`ConsequenceCommand`][osrlib.crawl.commands.ConsequenceCommand]) are
  the referee commands the firing issues, in authored order.
- A narrative block ([`NarrativeBlock`][osrlib.crawl.narrative.NarrativeBlock]) contains
  the trigger's text. `fired` is the referee's beat and `journal` the players', so write
  a journal form for the line the table should see.

Reach for a quest ([`QuestSpec`][osrlib.crawl.quests.QuestSpec]) instead when the
adventure has to keep score toward an ending. A quest composes these same patterns and
conditions. The guide
[Gates, triggers, and quests](https://mmacy.github.io/osrlib-python/guides/gates-triggers-quests/)
runs all three from one adventure document.
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

Write it as the `character_id` of a
[`GrantItem`][osrlib.crawl.commands.GrantItem],
[`GrantCoins`][osrlib.crawl.commands.GrantCoins], or
[`AwardXP`][osrlib.crawl.commands.AwardXP] in a trigger's consequences or a quest's
rewards, where a concrete character id would be a guess: a document written before play
cannot know the ids a session hands out.

The [`Interpreter`][osrlib.crawl.interpreter.Interpreter] expands it at issue time into
one command per living member, in marching order, so the command log stays concrete and
replays exactly. A party with nobody left standing expands to no commands at all,
because a reward for the dead is nothing rather than an error. Use
[`FIRST_LIVING_SELECTOR`][osrlib.crawl.triggers.FIRST_LIVING_SELECTOR] when one member
should receive the whole thing. No other command honors a selector, and a literal
character id is passed through untouched."""

FIRST_LIVING_SELECTOR = "@first"
"""The `character_id` an authored consequence writes to address one member.

The [`Interpreter`][osrlib.crawl.interpreter.Interpreter] expands it at issue time to
the first living member in marching order, which is the treasure-recipient convention:
one object goes to one member, and the party sorts it out with
[`GiveItems`][osrlib.crawl.commands.GiveItems]. With nobody standing there is no
recipient, so the consequence is dropped and a
[`NoteRecordedEvent`][osrlib.crawl.events.NoteRecordedEvent] says why rather than the
engine guessing. Use [`PARTY_SELECTOR`][osrlib.crawl.triggers.PARTY_SELECTOR] when every
member should receive the reward."""


class AreaEnteredPattern(BaseModel):
    """The party entered a keyed area.

    Matches a [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent] for an
    area. Area ids are scoped to their level, so the pattern names the whole triple: an
    area id of `"shrine"` means nothing without the dungeon and level it belongs to.
    Reach for [`LevelEnteredPattern`][osrlib.crawl.triggers.LevelEnteredPattern] when the
    whole level is the crossing you want.

    Examples:
        ```python
        from osrlib.crawl.triggers import AreaEnteredPattern

        shrine = AreaEnteredPattern(dungeon_id="barrow", level_number=2, area_id="shrine")
        assert shrine.pattern_type == "area_entered"
        ```
    """

    model_config = ConfigDict(frozen=True)

    pattern_type: Literal["area_entered"] = "area_entered"
    """The discriminator value, `area_entered`. It appears in the document, and you
    never set it yourself."""
    dungeon_id: str = Field(min_length=1)
    """The id of the dungeon the area belongs to."""
    level_number: int = Field(ge=1)
    """The 1-based number of the level the area belongs to."""
    area_id: str = Field(min_length=1)
    """The keyed area's id, as the level's
    [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] authored it."""


class LevelEnteredPattern(BaseModel):
    """The party arrived on a dungeon level.

    It matches however the party got there: a stair between levels and an entry from
    town both land it on the level. The engine reports the coarsest crossing a move
    passed, so a party walking in from town reports a dungeon entry, and this pattern
    counts that as a level arrival too.

    Examples:
        ```python
        from osrlib.crawl.triggers import LevelEnteredPattern

        deeper = LevelEnteredPattern(dungeon_id="barrow", level_number=2)
        assert deeper.pattern_type == "level_entered"
        ```
    """

    model_config = ConfigDict(frozen=True)

    pattern_type: Literal["level_entered"] = "level_entered"
    """The discriminator value, `level_entered`. It appears in the document, and you
    never set it yourself."""
    dungeon_id: str = Field(min_length=1)
    """The id of the dungeon the level belongs to."""
    level_number: int = Field(ge=1)
    """The 1-based level number."""


class DungeonEnteredPattern(BaseModel):
    """The party crossed into a dungeon, from town or from another dungeon.

    The coarse arrival, matched by a
    [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent] for a dungeon.
    Use it for the beat that belongs to walking in the front door, and
    [`LevelEnteredPattern`][osrlib.crawl.triggers.LevelEnteredPattern] for one that
    belongs to a particular level.

    Examples:
        ```python
        from osrlib.crawl.triggers import DungeonEnteredPattern

        arrival = DungeonEnteredPattern(dungeon_id="barrow")
        assert arrival.pattern_type == "dungeon_entered"
        ```
    """

    model_config = ConfigDict(frozen=True)

    pattern_type: Literal["dungeon_entered"] = "dungeon_entered"
    """The discriminator value, `dungeon_entered`. It appears in the document, and you
    never set it yourself."""
    dungeon_id: str = Field(min_length=1)
    """The id of the dungeon the party crossed into."""


class TownEnteredPattern(BaseModel):
    """The party arrived in the base town, however it got there.

    An adventure has one town, so the pattern needs no fields. The homecoming beat fires
    on the return trip and on a referee's placement alike. Pair it with a
    [`HasItemCondition`][osrlib.crawl.gates.HasItemCondition] for the errand that is
    finished only when the party walks home carrying the thing.

    Examples:
        ```python
        from osrlib.crawl.triggers import TownEnteredPattern

        home = TownEnteredPattern()
        assert home.pattern_type == "town_entered"
        ```
    """

    model_config = ConfigDict(frozen=True)

    pattern_type: Literal["town_entered"] = "town_entered"
    """The discriminator value, `town_entered`. It appears in the document, and you
    never set it yourself."""


class ItemAcquiredPattern(BaseModel):
    """A party member acquired an item with `item_id`.

    Matched by an [`ItemAcquiredEvent`][osrlib.crawl.events.ItemAcquiredEvent], however
    the item arrived: out of a cache, from a referee's grant, or from another member's
    hands. The id domain is the one a `has_item` condition reads, which is the effective
    equipment catalog, meaning the shipped items plus the adventure's bundled ones, or
    the magic-item catalog.

    An acquisition reports mundane items by catalog id and magic items by their
    session-scoped instance id, so a magic `item_id` matches by resolving that instance
    against the acquiring character's inventory.

    Examples:
        ```python
        from osrlib.crawl.triggers import ItemAcquiredPattern

        taken = ItemAcquiredPattern(item_id="brass_key")
        assert taken.pattern_type == "item_acquired"
        ```
    """

    model_config = ConfigDict(frozen=True)

    pattern_type: Literal["item_acquired"] = "item_acquired"
    """The discriminator value, `item_acquired`. It appears in the document, and you
    never set it yourself."""
    item_id: str = Field(min_length=1)
    """The catalog id of the item to watch for, from the equipment catalog (shipped plus
    adventure-bundled) or the magic-item catalog. For a magic item, author the template
    id rather than an instance id."""


class MonsterDefeatedPattern(BaseModel):
    """A monster of `template_id` was defeated: slain or routed.

    Both outcomes count as a defeat and the pattern does not filter on one. Defeats are
    reported at battle end through
    [`MonsterDefeatedEvent`][osrlib.crawl.events.MonsterDefeatedEvent], so a boss falling
    opens the portcullis once the fighting stops and never mid-round. Author no trigger
    that has to land the instant a blow kills.

    Examples:
        ```python
        from osrlib.crawl.triggers import MonsterDefeatedPattern

        beaten = MonsterDefeatedPattern(template_id="ogre")
        assert beaten.pattern_type == "monster_defeated"
        ```
    """

    model_config = ConfigDict(frozen=True)

    pattern_type: Literal["monster_defeated"] = "monster_defeated"
    """The discriminator value, `monster_defeated`. It appears in the document, and you
    never set it yourself."""
    template_id: str = Field(min_length=1)
    """The monster template id to watch for, from the monster catalog (shipped plus
    adventure-bundled). It is the template, not a session-scoped instance id, so every
    monster of that kind matches."""


class FlagSetPattern(BaseModel):
    """A session flag was written: the edge, not the state.

    This is the lever. Your game, or another trigger's consequence, executes
    [`SetFlag`][osrlib.crawl.commands.SetFlag], the write emits a
    [`FlagSetEvent`][osrlib.crawl.events.FlagSetEvent], and the trigger watching that key
    fires. The match is against the value the write set, so a flag rewritten with the
    value it already had still fires. To ask about the value a flag has now instead of a
    write that just happened, use a
    [`FlagEqualsCondition`][osrlib.crawl.gates.FlagEqualsCondition].

    Examples:
        ```python
        from osrlib.crawl.triggers import FlagSetPattern

        pulled = FlagSetPattern(key="crypt.lever", value="pulled")
        assert pulled.pattern_type == "flag_set"
        ```
    """

    model_config = ConfigDict(frozen=True)

    pattern_type: Literal["flag_set"] = "flag_set"
    """The discriminator value, `flag_set`. It appears in the document, and you
    never set it yourself."""
    key: str = Field(min_length=1)
    """The flag name to watch."""
    value: str | int | bool | None = None
    """The written value to match, or `None` to match any write of the key. `None` is
    unambiguous because a flag value is a `str`, an `int`, or a `bool` and never `None`.
    An authored value compares through
    [`flag_values_equal`][osrlib.crawl.gates.flag_values_equal], the same strict
    comparison a [`FlagEqualsCondition`][osrlib.crawl.gates.FlagEqualsCondition] uses,
    so a stored `True` never matches an authored `1`."""


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
"""The pattern union, discriminated on `pattern_type`.

Annotate a field with this alias when you write your own model that holds an authored
pattern. Pydantic then picks the member from the `pattern_type` value in the document.
It is the type of [`TriggerSpec.when`][osrlib.crawl.triggers.TriggerSpec] and of
[`TriggerClause.pattern`][osrlib.crawl.quests.TriggerClause], so a quest watches exactly
what a trigger watches. New observables join the union additively, and the discriminator
values are wire values that appear in every document that includes a trigger."""


class TriggerSpec(BaseModel):
    """One authored trigger: when it fires, what must hold, and what happens.

    Put your triggers in the `triggers` tuple of an
    [`Adventure`][osrlib.crawl.adventure.Adventure], and register an
    [`Interpreter`][osrlib.crawl.interpreter.Interpreter] on the session to play them. A
    spec on its own is inert data, and nothing in the engine reads it without that
    listener.

    A trigger fires once ever by default. The fired-mark that
    [`MarkTriggerFired`][osrlib.crawl.commands.MarkTriggerFired] writes is session state,
    so once-only survives a save, a load, and a replay, and `session.fired_triggers` is
    where you read it. `repeatable=True` is the authored opt-in for a trigger that fires
    every time its pattern matches.

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
    """The trigger's id, unique across the adventure. It is what the fired-mark records,
    and what the `source` stamp on every command the firing issues names, in the form
    `trigger:{id}`."""
    when: TriggerPattern
    """The observable that fires it, one member of
    [`TriggerPattern`][osrlib.crawl.triggers.TriggerPattern]."""
    conditions: tuple[ConditionSpec, ...] = ()
    """Extra tests that all have to hold at the moment of the match. The tuple is an AND
    with no combinators, and each condition is evaluated live against session state
    through [`condition_holds`][osrlib.crawl.gates.condition_holds]. A condition with
    `consumes=True` is rejected at parse."""
    repeatable: bool = False
    """Whether the trigger fires every time its pattern matches. The default fires it
    once for the life of the session."""
    consequences: tuple[ConsequenceCommand, ...] = ()
    """The referee commands the firing issues, in authored order, with
    [`PARTY_SELECTOR`][osrlib.crawl.triggers.PARTY_SELECTOR] and
    [`FIRST_LIVING_SELECTOR`][osrlib.crawl.triggers.FIRST_LIVING_SELECTOR] expanded to
    the members they name. Each command stands or drops on its own, so one rejection
    never stops the rest. The tuple may be empty: a trigger whose whole job is its
    journal beat is a normal shape. An authored `source` is rejected at parse, because
    the issuing trigger stamps it."""
    narrative: NarrativeBlock | None = None
    """The trigger's authored text. A trigger reads two beats of the block: `fired`,
    reported by the referee-visibility
    [`TriggerFiredEvent`][osrlib.crawl.events.TriggerFiredEvent], and `journal`, which
    becomes a player-visible journal entry."""

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
        stamp would either be overwritten or, worse, believed: a line in the log
        claiming a provenance nothing produced.
        """
        for position, consequence in enumerate(self.consequences):
            if consequence.source is not None:
                raise ValueError(f"consequence {position} carries a source; the issuing trigger stamps it")
        return self
