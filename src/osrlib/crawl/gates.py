"""Authored gates, the conditions they test, and the function that evaluates them.

A gate is a condition you write into an adventure. The engine checks it when the party tries to
open a door, take a stair, or walk through a passage, and refuses the attempt when the condition
fails.

You write a [`GateSpec`][osrlib.crawl.gates.GateSpec] into the `requires` field of a
[`DoorSpec`][osrlib.crawl.dungeon.DoorSpec] or a
[`TransitionSpec`][osrlib.crawl.dungeon.TransitionSpec], inside the dungeon of an
[`Adventure`][osrlib.crawl.adventure.Adventure]. Gates go nowhere else.
[`GameSession.execute`][osrlib.crawl.session.GameSession.execute] checks the gate on
[`OpenDoor`][osrlib.crawl.commands.OpenDoor], [`ForceDoor`][osrlib.crawl.commands.ForceDoor],
[`UseStairs`][osrlib.crawl.commands.UseStairs], and a
[`MoveParty`][osrlib.crawl.commands.MoveParty] that crosses a passage. The gate is the last check
before the command runs, so on a locked door the session rejects the attempt for the lock and
doesn't check the gate.

When the condition fails, the session rejects the command with `exploration.door.gate_refused` or
`exploration.transition.gate_refused`, and the command emits no events. The rejection's `refusal`
parameter contains the gate's authored refusal text. When the condition holds, the command's event
contains the gate's authored success text in its `narrative`: a
[`DoorEvent`][osrlib.crawl.events.DoorEvent] for a door, or a
[`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent] for a transition into another
level or dungeon. When the gate takes an item as a toll, an
[`ItemConsumedEvent`][osrlib.crawl.events.ItemConsumedEvent] comes before that event.

A gate has no state. [`condition_holds`][osrlib.crawl.gates.condition_holds] reads the session at
the moment of the attempt and stores nothing, so once the party drops or sells a key, the session
refuses the party at that key's door again.

The engine evaluates every condition with
[`condition_holds`][osrlib.crawl.gates.condition_holds], and compares flag values with
[`flag_values_equal`][osrlib.crawl.gates.flag_values_equal].
[`first_holder`][osrlib.crawl.gates.first_holder] finds the member a toll takes an item from.
Call these functions when your own code has to give the same answers as the engine.

Gates, triggers, and quest clauses all use the same conditions.
[`ConditionSpec`][osrlib.crawl.gates.ConditionSpec] is the union of them:

- [`HasItemCondition`][osrlib.crawl.gates.HasItemCondition]: a party member carries an item with
  that catalog id, equipped or in the pack.
  [`Inventory.carried_item`][osrlib.core.items.Inventory.carried_item] does the matching. With
  `consumes=True`, a successful command takes one of the item from the first member in marching
  order who carries it.
- [`FlagEqualsCondition`][osrlib.crawl.gates.FlagEqualsCondition]: a session flag has a given
  value. You write flags with [`SetFlag`][osrlib.crawl.commands.SetFlag].
- [`EffectActiveCondition`][osrlib.crawl.gates.EffectActiveCondition]: a party member has an
  active effect of a given kind. Use it when the party has to invoke an item, not only carry it.

Gates and locks are separate. A lock is a rules mechanic, with dice and a record of which
characters have tried it. A gate is an authored condition with no state. To open a door that has
both, the party has to get past both. Checking a gate draws no dice, costs no game time, and
changes nothing, so a refusal is an ordinary command rejection.

Use a gate when the party has to meet a condition to get through. When you want something to
happen because the party did something, write a trigger
([`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec]) instead. The guide
[Gates, triggers, and quests](https://mmacy.github.io/osrlib-python/guides/gates-triggers-quests/)
builds all three in an adventure you can run.
"""

from collections.abc import Mapping, Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from osrlib.core.character import Character
from osrlib.core.effects import EffectsLedger
from osrlib.crawl.narrative import NarrativeBlock

__all__ = [
    "ConditionSpec",
    "EffectActiveCondition",
    "FlagEqualsCondition",
    "GateSpec",
    "HasItemCondition",
    "condition_holds",
    "first_holder",
    "flag_values_equal",
]


class HasItemCondition(BaseModel):
    """A condition that holds when the party carries an item with `item_id`, mundane or magic.

    Put it in the `condition` of a [`GateSpec`][osrlib.crawl.gates.GateSpec] for a door
    that opens only with a key. Put it in the `conditions` of a
    [`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec] or a quest's
    [`TriggerClause`][osrlib.crawl.quests.TriggerClause] to make the trigger or clause
    match only while someone carries the item. Any member's inventory satisfies it,
    equipped items included. The party carries its dead and their packs, so a key in a
    dead member's pack still counts.

    `item_id` has to be in the equipment catalog, which is the built-in items plus the
    adventure's bundled items, or in the magic-item catalog.
    [`GameSession.new`][osrlib.crawl.session.GameSession.new] runs
    [`validate_adventure`][osrlib.crawl.adventure.validate_adventure], which raises
    [`ContentValidationError`][osrlib.errors.ContentValidationError] for an id in neither
    catalog.

    Examples:
        ```python
        from osrlib.crawl.gates import HasItemCondition

        show_symbol = HasItemCondition(item_id="holy_symbol")  # carrying it is enough
        pay_toll = HasItemCondition(item_id="iron_spikes", consumes=True)  # one spike per crossing
        print(show_symbol.consumes, pay_toll.consumes)
        # False True
        ```
    """

    model_config = ConfigDict(frozen=True)

    condition_type: Literal["has_item"] = "has_item"
    """The discriminator value, `has_item`. It appears in every document that
    includes this condition, and you never set it yourself."""
    item_id: str = Field(min_length=1)
    """The catalog id to look for, from the equipment catalog (built-in items plus the
    adventure's bundled ones) or the magic-item catalog."""
    consumes: bool = False
    """Whether a successful attempt takes the item. `True` makes the item a toll: every time
    the gated command succeeds, the session takes one of the item from the first member in
    marching order who carries it. A door that swings shut then takes another key to open.
    Only a gate can take a toll. In a trigger's or a quest's conditions, `True` fails
    validation with a pydantic `ValidationError`, because a trigger or a quest reacts to
    something that has already happened, and there's no attempt to charge."""


class FlagEqualsCondition(BaseModel):
    """A condition that holds when a session flag equals `value`.

    You write flags with [`SetFlag`][osrlib.crawl.commands.SetFlag], and a trigger's
    consequences can write them too. That's how a lever in one room opens a portcullis in
    another: a trigger sets the flag, and a gate tests it. The comparison goes through
    [`flag_values_equal`][osrlib.crawl.gates.flag_values_equal], which is strict. A flag
    that was never written equals nothing, not even `False`, and a stored `True` doesn't
    match an authored `1`.

    Examples:
        ```python
        from osrlib.crawl.gates import FlagEqualsCondition

        raised = FlagEqualsCondition(key="crypt.portcullis", value="raised")
        print(raised.model_dump())
        # {'condition_type': 'flag_equals', 'key': 'crypt.portcullis', 'value': 'raised'}
        ```
    """

    model_config = ConfigDict(frozen=True)

    condition_type: Literal["flag_equals"] = "flag_equals"
    """The discriminator value, `flag_equals`. It appears in every document that
    includes this condition, and you never set it yourself."""
    key: str = Field(min_length=1)
    """The name of the flag to read, as `session.flags` stores it."""
    value: str | int | bool
    """The value the flag has to have. Values of different types are never equal, so write
    the same type that the [`SetFlag`][osrlib.crawl.commands.SetFlag] for this flag writes."""


class EffectActiveCondition(BaseModel):
    """A condition that holds when an active effect of `kind` is attached to a party member.

    Use it when the party has to invoke an item, not only carry it: the item grants an
    effect, and the gate requires that effect. The condition reads the session's effects
    ledger ([`EffectsLedger`][osrlib.core.effects.EffectsLedger]), which records every
    active effect. An effect attached to a location doesn't count, because the condition
    looks only at party members.

    Examples:
        ```python
        from osrlib.crawl.gates import EffectActiveCondition

        warded = EffectActiveCondition(kind="ward_of_the_deep")
        print(warded.model_dump())
        # {'condition_type': 'effect_active', 'kind': 'ward_of_the_deep'}
        ```
    """

    model_config = ConfigDict(frozen=True)

    condition_type: Literal["effect_active"] = "effect_active"
    """The discriminator value, `effect_active`. It appears in every document that
    includes this condition, and you never set it yourself."""
    kind: str = Field(min_length=1)
    """The effect kind to look for, like `"fatigue"`, or the `kind` of an
    [`EffectDefinition`][osrlib.core.effects.EffectDefinition] you wrote. Effect kinds
    come from data, not from a fixed list, so osrlib doesn't check this value against a
    catalog. A misspelled kind never matches."""


ConditionSpec = Annotated[
    HasItemCondition | FlagEqualsCondition | EffectActiveCondition,
    Field(discriminator="condition_type"),
]
"""The condition union, discriminated on `condition_type`.

Its members are [`HasItemCondition`][osrlib.crawl.gates.HasItemCondition]
(`has_item`), [`FlagEqualsCondition`][osrlib.crawl.gates.FlagEqualsCondition]
(`flag_equals`), and
[`EffectActiveCondition`][osrlib.crawl.gates.EffectActiveCondition] (`effect_active`).
Annotate a field with this alias when you write your own model that contains authored
conditions. Pydantic then picks the member from the `condition_type` value in the
document. The `condition_type` values are part of the document format, so a later release can
add a member but doesn't rename one within a schema version.

Conditions appear in the `condition` of a [`GateSpec`][osrlib.crawl.gates.GateSpec], the
`conditions` of a [`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec], and the `conditions`
of a quest's [`TriggerClause`][osrlib.crawl.quests.TriggerClause]. The engine evaluates all
of them with [`condition_holds`][osrlib.crawl.gates.condition_holds]."""


class GateSpec(BaseModel):
    """A condition the party has to meet to get through, with the authored text for both outcomes.

    Put one in the `requires` field of a
    [`DoorSpec`][osrlib.crawl.dungeon.DoorSpec] or a
    [`TransitionSpec`][osrlib.crawl.dungeon.TransitionSpec] when you build the dungeon.
    The engine checks it every time the party tries that door, stair, or passage. When the
    condition fails, the session rejects the attempt and puts the `refusal` text in the
    rejection's `refusal` parameter. A refused attempt costs the party nothing: no dice, no
    game time, no item, and no change to the door. On a successful attempt, the command's
    event contains the `success` text in its `narrative`.

    The engine checks a door's gate only when the party opens or forces the door. If you
    open a gated door with [`SetDoorState`][osrlib.crawl.commands.SetDoorState], the party
    walks through it freely until the door closes again.

    Examples:
        A door the party can open only while someone carries a holy symbol:

        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.commands import EnterDungeon, GrantItem, OpenDoor
        from osrlib.crawl.dungeon import Direction, DoorSpec, DungeonSpec, Edge, EdgeKind, LevelSpec
        from osrlib.crawl.gates import GateSpec, HasItemCondition
        from osrlib.crawl.narrative import NarrativeBlock
        from osrlib.crawl.party import Party
        from osrlib.crawl.session import GameSession

        sentinel = GateSpec(
            condition=HasItemCondition(item_id="holy_symbol"),
            narrative=NarrativeBlock(
                refusal="The bronze sentinel folds its arms. Show the sign of the faith, it says.",
                success="The sentinel bows to the holy symbol and the door swings wide.",
            ),
        )
        door = Edge(kind=EdgeKind.DOOR, door=DoorSpec(requires=sentinel))
        level = LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": door})
        crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
        adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))

        rules = Ruleset()
        stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
        hero = create_character(
            name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=rules, stream=stream
        ).character
        session = GameSession.new(Party(members=[hero]), adventure, seed=7)
        session.execute(EnterDungeon(dungeon_id="crypt"))

        refused = session.execute(OpenDoor(direction=Direction.EAST))
        print(refused.rejections[0].code)
        # exploration.door.gate_refused
        print(refused.rejections[0].params["refusal"])
        # The bronze sentinel folds its arms. Show the sign of the faith, it says.

        session.execute(GrantItem(character_id=hero.id, item_id="holy_symbol"))
        opened = session.execute(OpenDoor(direction=Direction.EAST))
        print(opened.events[0].code, opened.events[0].narrative)
        # exploration.door.opened The sentinel bows to the holy symbol and the door swings wide.
        ```
    """

    model_config = ConfigDict(frozen=True)

    condition: ConditionSpec
    """The condition the party has to meet. A gate has exactly one, and the engine evaluates
    it against the session at the moment of each attempt. To require two things, put the
    second condition on a trigger that sets a flag, and test that flag here with a
    [`FlagEqualsCondition`][osrlib.crawl.gates.FlagEqualsCondition]."""
    narrative: NarrativeBlock | None = None
    """The authored text for both outcomes. osrlib reads two fields of a gate's block,
    `refusal` and `success`, and ignores the rest. With `None`, the gate has no text, and
    the rejection has no `refusal` parameter."""


def condition_holds(
    condition: ConditionSpec,
    *,
    members: Sequence[Character],
    flags: Mapping[str, str | int | bool],
    ledger: EffectsLedger,
) -> bool:
    """Evaluate one condition against live session state.

    The engine uses this function for every authored condition: a gate on a door, a stair,
    or a passage, the conditions of a [`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec],
    and the conditions of a quest's [`TriggerClause`][osrlib.crawl.quests.TriggerClause].
    You rarely call it yourself. The session calls it when the party attempts a gated
    door or transition, and the [`Interpreter`][osrlib.crawl.interpreter.Interpreter] calls
    it when an event matches a trigger or a clause. Call it directly when your own listener
    has to check a condition the way the engine does, or when a test checks an authored
    document against a session.

    The call draws no dice, advances no clock, and changes nothing. It searches the party
    in marching order and each inventory in order, so the same state always gives the same
    answer. It checks every party member, living or dead, because the party carries its
    dead and their packs.

    Args:
        condition: The condition to evaluate, one member of
            [`ConditionSpec`][osrlib.crawl.gates.ConditionSpec].
        members: The party, in marching order, from `session.party.members`.
        flags: The session flag store, from `session.flags`.
        ledger: The session's effects ledger, from `session.ledger`.

    Returns:
        True when the condition holds right now.

    Examples:
        ```python
        from osrlib.core.effects import EffectsLedger
        from osrlib.crawl.gates import FlagEqualsCondition, condition_holds

        condition = FlagEqualsCondition(key="portcullis", value="raised")
        print(condition_holds(condition, members=[], flags={"portcullis": "raised"}, ledger=EffectsLedger()))
        # True
        print(condition_holds(condition, members=[], flags={}, ledger=EffectsLedger()))
        # False
        ```
    """
    if isinstance(condition, HasItemCondition):
        return any(member.inventory.carried_item(condition.item_id) is not None for member in members)
    if isinstance(condition, FlagEqualsCondition):
        if condition.key not in flags:
            return False
        return flag_values_equal(flags[condition.key], condition.value)
    return any(member.id is not None and ledger.active_on(member.id, condition.kind) for member in members)


def flag_values_equal(stored: str | int | bool, expected: str | int | bool) -> bool:
    """Return whether two flag values are equal, using the strict comparison the engine uses.

    Two values are equal when Python says they are and either both or neither are `bool`.
    Python treats `True == 1` as true, but in an authored document they are different
    values. A flag set to `True` doesn't satisfy an authored `1`, and an authored `True`
    doesn't match a stored `1`. Call this function when your code has to compare flag
    values the way the engine does.

    The engine compares every flag value with this function, so a
    [`FlagEqualsCondition`][osrlib.crawl.gates.FlagEqualsCondition] and a
    [`FlagSetPattern`][osrlib.crawl.triggers.FlagSetPattern] always agree on whether two
    values are equal.

    Args:
        stored: The value in `session.flags`, or the value a
            [`FlagSetEvent`][osrlib.crawl.events.FlagSetEvent] reports.
        expected: The value written in the adventure document.

    Returns:
        True when the two are the same value.

    Examples:
        ```python
        from osrlib.crawl.gates import flag_values_equal

        assert flag_values_equal("open", "open")
        assert not flag_values_equal(True, 1)
        assert not flag_values_equal(1, True)
        ```
    """
    return stored == expected and isinstance(stored, bool) == isinstance(expected, bool)


def first_holder(members: Sequence[Character], item_id: str) -> Character | None:
    """Return the first member in marching order carrying an item with `item_id`.

    When a gate's [`HasItemCondition`][osrlib.crawl.gates.HasItemCondition] has
    `consumes=True`, the engine takes the item from the member this function returns.
    Call it when your code has to know which member will pay that toll. It uses the same
    matching as [`condition_holds`][osrlib.crawl.gates.condition_holds]: it searches every
    member, living or dead, and each inventory in order, equipped items included.

    Args:
        members: The party, in marching order, from `session.party.members`.
        item_id: The catalog id to look for.

    Returns:
        The first member in marching order who carries the item, or `None` when nobody does.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.crawl.gates import first_holder

        rules = Ruleset()
        stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
        hild = create_character(
            name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=rules, stream=stream
        ).character
        osric = create_character(
            name="Osric",
            class_id="cleric",
            alignment=Alignment.LAWFUL,
            ruleset=rules,
            stream=stream,
            purchases=[("holy_symbol", 1)],
        ).character

        print(first_holder([hild, osric], "holy_symbol").name)
        # Osric
        print(first_holder([hild, osric], "lantern"))
        # None
        ```
    """
    for member in members:
        if member.inventory.carried_item(item_id) is not None:
            return member
    return None
