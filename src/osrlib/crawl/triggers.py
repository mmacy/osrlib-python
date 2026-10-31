"""Authored triggers, which run referee commands when something happens in play.

A trigger pairs an event pattern with referee commands. When an event matches the pattern, and
the trigger's optional conditions hold, the commands run. Referee commands are the commands that
change the game directly, like [`GrantItem`][osrlib.crawl.commands.GrantItem] or
[`SetFlag`][osrlib.crawl.commands.SetFlag].

You write [`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec]s into the `triggers` tuple of an
[`Adventure`][osrlib.crawl.adventure.Adventure]. When one event matches several triggers, they
fire in the order of that tuple. Triggers do nothing until you register an
[`Interpreter`][osrlib.crawl.interpreter.Interpreter] on the session with
[`GameSession.register_listener`][osrlib.crawl.session.GameSession.register_listener]. The
interpreter matches the events of every accepted command against the adventure's triggers, and
executes the commands of each trigger that fires. When a trigger fires, you get these events:

- A [`TriggerFiredEvent`][osrlib.crawl.events.TriggerFiredEvent] with the trigger's `fired` text.
- The events its commands produce.
- A [`NoteRecordedEvent`][osrlib.crawl.events.NoteRecordedEvent] for each command the session
  rejected.
- A [`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent] when the trigger has
  `journal` text.

`session.fired_triggers` records which triggers have fired.

A gate ([`GateSpec`][osrlib.crawl.gates.GateSpec]) tests the state of the game when the party
attempts something. A trigger matches the events a command produced, so it fires when
something happens, not while something is true. The lever that opens the portcullis is a
trigger, and the locked door you open with the brass key is a gate.

A trigger has these parts:

- A pattern ([`TriggerPattern`][osrlib.crawl.triggers.TriggerPattern]) is the event to
  react to: the party entering an area, a level, a dungeon, or the town
  ([`AreaEnteredPattern`][osrlib.crawl.triggers.AreaEnteredPattern],
  [`LevelEnteredPattern`][osrlib.crawl.triggers.LevelEnteredPattern],
  [`DungeonEnteredPattern`][osrlib.crawl.triggers.DungeonEnteredPattern],
  [`TownEnteredPattern`][osrlib.crawl.triggers.TownEnteredPattern]), a member acquiring an item
  ([`ItemAcquiredPattern`][osrlib.crawl.triggers.ItemAcquiredPattern]), a monster defeated
  ([`MonsterDefeatedPattern`][osrlib.crawl.triggers.MonsterDefeatedPattern]), or a flag written
  ([`FlagSetPattern`][osrlib.crawl.triggers.FlagSetPattern]). The interpreter matches a pattern
  against the event, never against the current state. A command that the interpreter runs for
  a trigger can move the party, but each event still describes the moment it happened.
- Conditions ([`ConditionSpec`][osrlib.crawl.gates.ConditionSpec]) narrow the match. Each one has
  to hold, against the session's state at the moment of the match. A condition with
  `consumes=True` fails validation, because a trigger matches an event that has already
  happened, and there's no attempt to charge a toll for.
- Consequences ([`ConsequenceCommand`][osrlib.crawl.commands.ConsequenceCommand]) are the referee
  commands the interpreter runs when the trigger fires, in the order you write them. In a grant,
  a removal, or an award, write [`PARTY_SELECTOR`][osrlib.crawl.triggers.PARTY_SELECTOR] or
  [`FIRST_LIVING_SELECTOR`][osrlib.crawl.triggers.FIRST_LIVING_SELECTOR] in place of a character
  id. A [`RemoveItem`][osrlib.crawl.commands.RemoveItem] can also take
  [`CARRIER_SELECTOR`][osrlib.crawl.triggers.CARRIER_SELECTOR], which stands for the member
  who carries the item.
- A narrative block ([`NarrativeBlock`][osrlib.crawl.narrative.NarrativeBlock]) contains the
  trigger's text. `fired` is a line for the referee, and `journal` is a line for the players.
  Write `journal` for the text the players should see.

Use a quest ([`QuestSpec`][osrlib.crawl.quests.QuestSpec]) instead when you need to track
progress toward a goal. A quest's clauses use these same patterns and conditions. For an
adventure document that uses all three, see
[Gates, triggers, and quests](https://mmacy.github.io/osrlib-python/guides/gates-triggers-quests/).
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from osrlib.crawl.commands import ConsequenceCommand
from osrlib.crawl.gates import ConditionSpec
from osrlib.crawl.narrative import NarrativeBlock

__all__ = [
    "CARRIER_SELECTOR",
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
"""The `character_id` to write in an authored grant, removal, or award that applies to every living member.

Write it as the `character_id` of a
[`GrantItem`][osrlib.crawl.commands.GrantItem],
[`GrantCoins`][osrlib.crawl.commands.GrantCoins],
[`RemoveItem`][osrlib.crawl.commands.RemoveItem],
[`RemoveCoins`][osrlib.crawl.commands.RemoveCoins], or
[`AwardXP`][osrlib.crawl.commands.AwardXP] in a trigger's consequences or a quest's
rewards. An adventure document can't contain real character ids, because a session assigns
them when it starts. [`validate_adventure`][osrlib.crawl.adventure.validate_adventure] rejects
any other `character_id` in those commands. So each of them in a document uses this selector,
[`FIRST_LIVING_SELECTOR`][osrlib.crawl.triggers.FIRST_LIVING_SELECTOR], or, in a `RemoveItem`,
[`CARRIER_SELECTOR`][osrlib.crawl.triggers.CARRIER_SELECTOR].

Before the [`Interpreter`][osrlib.crawl.interpreter.Interpreter] runs the command, it replaces
the command with one copy for each living member, in marching order, each with that member's
id. The command log records those copies, so a replay doesn't depend on the selector. When no
member is alive, the command expands to nothing, and the interpreter records no note. When the
session refuses a copy, like a removal from a member who doesn't have the item, the interpreter
drops that copy with a note and still runs the others. Use `FIRST_LIVING_SELECTOR` when one
member should receive the whole reward."""

FIRST_LIVING_SELECTOR = "@first"
"""The `character_id` to write in an authored grant, removal, or award that applies to one member.

Before the [`Interpreter`][osrlib.crawl.interpreter.Interpreter] runs the command, it replaces
the selector with the id of the first living member in marching order. That member receives
the whole grant or award, and the party can share it out with
[`GiveItems`][osrlib.crawl.commands.GiveItems]. A removal takes from that member alone, so the
first living member in marching order pays a toll written with this selector. When no member is
alive, the interpreter drops the command and records why in a
[`NoteRecordedEvent`][osrlib.crawl.events.NoteRecordedEvent]. Use
[`PARTY_SELECTOR`][osrlib.crawl.triggers.PARTY_SELECTOR] when every living member should
receive the reward."""

CARRIER_SELECTOR = "@carrier"
"""The `character_id` to write in an authored [`RemoveItem`][osrlib.crawl.commands.RemoveItem] that takes the item
from whoever carries it.

Use it for a quest item that's used up when the quest completes, or an offering the party
leaves on an altar, when you can't know which member will carry it. Before the
[`Interpreter`][osrlib.crawl.interpreter.Interpreter] runs the command, it replaces the
selector with the id of the first living member in marching order who carries the
command's `quantity` of the item. When no living member does, the interpreter drops the
command and records why in a [`NoteRecordedEvent`][osrlib.crawl.events.NoteRecordedEvent].

[`validate_adventure`][osrlib.crawl.adventure.validate_adventure] accepts this selector
only on a `RemoveItem`. A grant or an award doesn't depend on what a member carries, and a
[`RemoveCoins`][osrlib.crawl.commands.RemoveCoins] uses
[`FIRST_LIVING_SELECTOR`][osrlib.crawl.triggers.FIRST_LIVING_SELECTOR] or
[`PARTY_SELECTOR`][osrlib.crawl.triggers.PARTY_SELECTOR]."""


class AreaEnteredPattern(BaseModel):
    """A pattern that matches the party entering a keyed area.

    It matches a [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent] for
    an area. An area id is unique only within its level, so the pattern includes the dungeon,
    the level, and the area. Use
    [`LevelEnteredPattern`][osrlib.crawl.triggers.LevelEnteredPattern] to react to the party
    arriving anywhere on a level.

    Examples:
        ```python
        from osrlib.crawl.triggers import AreaEnteredPattern

        shrine = AreaEnteredPattern(dungeon_id="barrow", level_number=2, area_id="shrine")
        print(shrine.model_dump(mode="json"))  # the form the pattern takes in an adventure document
        # {'pattern_type': 'area_entered', 'dungeon_id': 'barrow', 'level_number': 2, 'area_id': 'shrine'}
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
    """The id of the area, from its [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] on that
    level."""


class LevelEnteredPattern(BaseModel):
    """A pattern that matches the party arriving on a dungeon level.

    It matches however the party got there, by a stair from another level or by entering
    the dungeon from town. When one move enters both a dungeon and a level, the engine
    reports only the dungeon entry, and this pattern matches that report too.

    Examples:
        ```python
        from osrlib.crawl.triggers import LevelEnteredPattern

        deeper = LevelEnteredPattern(dungeon_id="barrow", level_number=2)
        print(deeper.model_dump(mode="json"))
        # {'pattern_type': 'level_entered', 'dungeon_id': 'barrow', 'level_number': 2}
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
    """A pattern that matches the party crossing into a dungeon, from town or another dungeon.

    It matches a [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent] for a
    dungeon. Use it to react to the party entering the dungeon, and
    [`LevelEnteredPattern`][osrlib.crawl.triggers.LevelEnteredPattern] to react to the party
    reaching a particular level.

    Examples:
        ```python
        from osrlib.crawl.triggers import DungeonEnteredPattern

        arrival = DungeonEnteredPattern(dungeon_id="barrow")
        print(arrival.model_dump(mode="json"))
        # {'pattern_type': 'dungeon_entered', 'dungeon_id': 'barrow'}
        ```
    """

    model_config = ConfigDict(frozen=True)

    pattern_type: Literal["dungeon_entered"] = "dungeon_entered"
    """The discriminator value, `dungeon_entered`. It appears in the document, and you
    never set it yourself."""
    dungeon_id: str = Field(min_length=1)
    """The id of the dungeon the party crossed into."""


class TownEnteredPattern(BaseModel):
    """A pattern that matches the party arriving in the adventure's town, however it got there.

    An adventure has one town, so the pattern has no fields. It matches when the party
    travels back to town and when a referee command places the party there. Pair it with a
    [`HasItemCondition`][osrlib.crawl.gates.HasItemCondition] for an errand that is done only
    when the party comes home carrying an item.

    Examples:
        ```python
        from osrlib.crawl.triggers import TownEnteredPattern

        home = TownEnteredPattern()
        print(home.model_dump(mode="json"))
        # {'pattern_type': 'town_entered'}
        ```
    """

    model_config = ConfigDict(frozen=True)

    pattern_type: Literal["town_entered"] = "town_entered"
    """The discriminator value, `town_entered`. It appears in the document, and you
    never set it yourself."""


class ItemAcquiredPattern(BaseModel):
    """A pattern that matches a party member acquiring an item with `item_id`.

    It matches an [`ItemAcquiredEvent`][osrlib.crawl.events.ItemAcquiredEvent], so it
    matches an item the party takes as treasure, buys in town, or gets from a referee's
    [`GrantItem`][osrlib.crawl.commands.GrantItem]. An item one member hands to another
    doesn't match, because [`GiveItems`][osrlib.crawl.commands.GiveItems] reports an
    [`ItemsGivenEvent`][osrlib.crawl.events.ItemsGivenEvent] instead. It accepts the same ids as a
    [`HasItemCondition`][osrlib.crawl.gates.HasItemCondition]: ids from the equipment
    catalog, which is the built-in items plus the adventure's bundled items, or from the
    magic-item catalog.

    The event contains a mundane item's catalog id, or a magic item's instance id, which the
    session assigns. To match a magic item, the interpreter looks up that instance
    in the acquiring member's inventory and compares its catalog id.

    Examples:
        ```python
        from osrlib.crawl.triggers import ItemAcquiredPattern

        taken = ItemAcquiredPattern(item_id="brass_key")
        print(taken.model_dump(mode="json"))
        # {'pattern_type': 'item_acquired', 'item_id': 'brass_key'}
        ```
    """

    model_config = ConfigDict(frozen=True)

    pattern_type: Literal["item_acquired"] = "item_acquired"
    """The discriminator value, `item_acquired`. It appears in the document, and you
    never set it yourself."""
    item_id: str = Field(min_length=1)
    """The catalog id of the item to watch for, from the equipment catalog or the
    magic-item catalog. For a magic item, write its catalog id, not an instance id."""


class MonsterDefeatedPattern(BaseModel):
    """A pattern that matches a monster of `template_id` being slain or routed.

    Slain and routed both count as defeated, and the pattern matches either one. The engine
    reports each defeat with a
    [`MonsterDefeatedEvent`][osrlib.crawl.events.MonsterDefeatedEvent] when the encounter
    closes, not when the monster falls. A trigger on a boss therefore fires after the
    fighting stops, never in the middle of a round. No pattern matches the moment of a
    kill.

    Examples:
        ```python
        from osrlib.crawl.triggers import MonsterDefeatedPattern

        beaten = MonsterDefeatedPattern(template_id="ogre")
        print(beaten.model_dump(mode="json"))
        # {'pattern_type': 'monster_defeated', 'template_id': 'ogre'}
        ```
    """

    model_config = ConfigDict(frozen=True)

    pattern_type: Literal["monster_defeated"] = "monster_defeated"
    """The discriminator value, `monster_defeated`. It appears in the document, and you
    never set it yourself."""
    template_id: str = Field(min_length=1)
    """The id of the monster to watch for, from the monster catalog, which is the built-in
    monsters plus the adventure's bundled monsters. It's the catalog id, not an instance id,
    so every monster of that kind matches."""


class FlagSetPattern(BaseModel):
    """A pattern that matches a write to a session flag.

    It matches the write, not the flag's state. When you execute
    [`SetFlag`][osrlib.crawl.commands.SetFlag], or the interpreter executes it for a trigger,
    the session emits a [`FlagSetEvent`][osrlib.crawl.events.FlagSetEvent], and a trigger with a
    pattern for that flag fires. The pattern compares the value that was written, so writing a flag again with the
    value it already has still matches. To test the value a flag has now, use a
    [`FlagEqualsCondition`][osrlib.crawl.gates.FlagEqualsCondition] instead.

    Examples:
        ```python
        from osrlib.crawl.triggers import FlagSetPattern

        pulled = FlagSetPattern(key="crypt.lever", value="pulled")
        print(pulled.model_dump(mode="json"))
        # {'pattern_type': 'flag_set', 'key': 'crypt.lever', 'value': 'pulled'}
        ```
    """

    model_config = ConfigDict(frozen=True)

    pattern_type: Literal["flag_set"] = "flag_set"
    """The discriminator value, `flag_set`. It appears in the document, and you
    never set it yourself."""
    key: str = Field(min_length=1)
    """The name of the flag to watch."""
    value: str | int | bool | None = None
    """The written value to match, or `None` to match any write of the flag. A flag value is
    a `str`, an `int`, or a `bool`, never `None`, so `None` can't be mistaken for a value.
    The pattern compares values with
    [`flag_values_equal`][osrlib.crawl.gates.flag_values_equal], the same strict comparison
    a [`FlagEqualsCondition`][osrlib.crawl.gates.FlagEqualsCondition] uses, so a written
    `True` doesn't match an authored `1`."""


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

Annotate a field with this alias when you write your own model that contains an authored
pattern. Pydantic then picks the member from the `pattern_type` value in the document.
It's the type of [`TriggerSpec.when`][osrlib.crawl.triggers.TriggerSpec.when] and of
[`TriggerClause.pattern`][osrlib.crawl.quests.TriggerClause.pattern], so a quest clause can
match the same events a trigger can. The `pattern_type` values are part of the document format, so a
later release can add a member but doesn't rename one within a schema version."""


class TriggerSpec(BaseModel):
    """One authored trigger: what it reacts to, what has to hold, and what happens.

    Put your triggers in the `triggers` tuple of an
    [`Adventure`][osrlib.crawl.adventure.Adventure], and register an
    [`Interpreter`][osrlib.crawl.interpreter.Interpreter] on the session to run them. The
    interpreter is the only part of osrlib that reads a trigger, so without it the
    trigger does nothing.

    By default a trigger fires once per session. When it fires, the interpreter executes
    [`MarkTriggerFired`][osrlib.crawl.commands.MarkTriggerFired], which adds the trigger's
    id to `session.fired_triggers`. That record is part of the session, so a trigger that
    has fired stays fired after a save, a load, or a replay. Set `repeatable=True` for a
    trigger that fires every time its pattern matches.

    For a trigger in a session from start to finish, see the
    [`Interpreter`][osrlib.crawl.interpreter.Interpreter] example.

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
        print(trigger.repeatable, trigger.consequences[0].command_type)
        # False set_door_state
        ```
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    """The trigger's id, unique across the adventure. `session.fired_triggers` records it, and
    every command the interpreter runs for the trigger has `source` set to `trigger:{id}`."""
    when: TriggerPattern
    """The pattern of the event that fires the trigger, as one member of
    [`TriggerPattern`][osrlib.crawl.triggers.TriggerPattern]."""
    conditions: tuple[ConditionSpec, ...] = ()
    """Extra tests, every one of which has to hold when an event matches `when`. There's no
    way to write "or". The interpreter evaluates each condition against the session at
    that moment with [`condition_holds`][osrlib.crawl.gates.condition_holds]. A condition
    with `consumes=True` fails validation with a pydantic `ValidationError`."""
    repeatable: bool = False
    """Whether the trigger fires every time its pattern matches. With `False`, the default,
    it fires once per session."""
    consequences: tuple[ConsequenceCommand, ...] = ()
    """The referee commands the interpreter runs when the trigger fires, in the order you write
    them. The interpreter replaces [`PARTY_SELECTOR`][osrlib.crawl.triggers.PARTY_SELECTOR],
    [`FIRST_LIVING_SELECTOR`][osrlib.crawl.triggers.FIRST_LIVING_SELECTOR], and
    [`CARRIER_SELECTOR`][osrlib.crawl.triggers.CARRIER_SELECTOR] with the members they select.
    When the session rejects one command, the interpreter drops it, records a note, and runs the
    rest. The tuple can be empty, for a trigger that only adds a journal entry. A command with
    `source` set fails validation with a pydantic `ValidationError`, because the interpreter sets
    `source` itself."""
    narrative: NarrativeBlock | None = None
    """The trigger's authored text. osrlib reads two fields of the block. `fired` goes on the
    [`TriggerFiredEvent`][osrlib.crawl.events.TriggerFiredEvent], which only the referee
    sees. `journal` becomes a journal entry that the players see."""

    @model_validator(mode="after")
    def _conditions_never_consume(self) -> TriggerSpec:
        """A trigger's conditions are tests, never tolls.

        Consumption is an effect of a *successful command*, reported through that
        command's events. A trigger matches an event that has already happened, so a
        toll here would have nothing to charge against.
        """
        for condition in self.conditions:
            if getattr(condition, "consumes", False):
                raise ValueError("a trigger condition cannot consume: a trigger fires, it does not take")
        return self

    @model_validator(mode="after")
    def _consequences_carry_no_source(self) -> TriggerSpec:
        """Whoever issues a command sets its `source`, not the document.

        The interpreter issues consequences with the trigger's id as their `source`, so a
        source written in the document would either be overwritten or be accepted, and the
        log would then show a source that never issued the command.
        """
        for position, consequence in enumerate(self.consequences):
            if consequence.source is not None:
                raise ValueError(f"consequence {position} carries a source; the issuing trigger stamps it")
        return self
