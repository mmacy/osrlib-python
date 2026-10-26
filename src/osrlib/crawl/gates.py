"""Authored gates, the conditions they test, and the function that evaluates them.

A gate is a condition you write into an adventure, which the engine checks when the party
attempts something, like opening a door or taking a stair.

You write a [`GateSpec`][osrlib.crawl.gates.GateSpec] into the
`requires` field of a [`DoorSpec`][osrlib.crawl.dungeon.DoorSpec] or a
[`TransitionSpec`][osrlib.crawl.dungeon.TransitionSpec], inside the dungeon geometry of
an [`Adventure`][osrlib.crawl.adventure.Adventure]. Gates go nowhere else. The
exploration handlers behind
[`GameSession.execute`][osrlib.crawl.session.GameSession.execute] evaluate it as the
last validation step of [`OpenDoor`][osrlib.crawl.commands.OpenDoor],
[`ForceDoor`][osrlib.crawl.commands.ForceDoor], and
[`UseStairs`][osrlib.crawl.commands.UseStairs], and of a
[`MoveParty`][osrlib.crawl.commands.MoveParty] that crosses a passage. When the condition fails, the command returns the
rejection `exploration.door.gate_refused` or `exploration.transition.gate_refused`,
which includes the authored refusal beat, and no event at all. When it passes, the
command's event includes the gate's success beat: a
[`DoorEvent`][osrlib.crawl.events.DoorEvent] for a door, a
[`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent] for a transition
that crosses into a new level or dungeon. The engine reports a toll the gate charges as an
[`ItemConsumedEvent`][osrlib.crawl.events.ItemConsumedEvent] ahead of that event.

A gate is stateless content. [`condition_holds`][osrlib.crawl.gates.condition_holds]
reads live session state at the moment of the attempt and stores nothing, so a key that
gets dropped or sold stops opening its door.

A gate, a trigger, and a quest clause all use the same conditions, and
[`ConditionSpec`][osrlib.crawl.gates.ConditionSpec] is their union:

- [`HasItemCondition`][osrlib.crawl.gates.HasItemCondition]: some party member's carried
  inventory contains an item with that catalog id
  ([`Inventory.carried_item`][osrlib.core.items.Inventory.carried_item] is the matching
  rule, equipped slots included). With `consumes=True`, the successful command takes one
  instance from the first holder in marching order.
- [`FlagEqualsCondition`][osrlib.crawl.gates.FlagEqualsCondition]: a session flag, which
  your game writes with [`SetFlag`][osrlib.crawl.commands.SetFlag], is set to a value.
- [`EffectActiveCondition`][osrlib.crawl.gates.EffectActiveCondition]: an active effect
  of that kind is attached to a party member, so you can ask for the talisman invoked
  rather than merely carried.

Gates and locks are separate. A lock is a rules mechanic, with dice and a record of which
characters have tried it. A gate is an authored condition with no state. To open a door with
both, the party has to get past both. Evaluating a gate draws no dice, costs no game time, and
changes nothing, so a gate refusal is an ordinary command rejection.

Use a gate when the party has to satisfy a condition to get through. When you want
something to happen because the party already did something, author a trigger
([`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec]) instead. The guide
[Gates, triggers, and quests](https://mmacy.github.io/osrlib-python/guides/gates-triggers-quests/)
walks all three from an adventure document you can run.
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

    Write this into the `condition` of a [`GateSpec`][osrlib.crawl.gates.GateSpec] for
    a door that opens only with a key, and into the `conditions` of a
    [`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec] or a quest's
    [`TriggerClause`][osrlib.crawl.quests.TriggerClause] to narrow a firing to a party
    that is still carrying something. Any member's carried inventory satisfies it,
    equipped slots included, because carrying is the test. The party carries its
    dead and their packs, so a key in a dead member's pack still counts.

    `item_id` has to resolve against the effective equipment catalog, which is the
    built-in catalog plus the adventure's bundled items, or against the magic-item
    catalog. An id in neither catalog fails
    [`validate_adventure`][osrlib.crawl.adventure.validate_adventure], which
    [`GameSession.new`][osrlib.crawl.session.GameSession.new] runs, with
    [`ContentValidationError`][osrlib.errors.ContentValidationError].

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
    """Whether a success takes the item. `True` makes it a toll: one instance leaves the
    first holder in marching order every time the gated command succeeds, so a door that
    swings shut needs another key. A trigger's or a quest's conditions reject `True` at
    parse, because they observe an event that has already happened and have no attempt
    to charge against."""


class FlagEqualsCondition(BaseModel):
    """A condition that holds when a session flag equals `value`.

    Your game writes flags with [`SetFlag`][osrlib.crawl.commands.SetFlag], and a
    trigger's consequence can write one too, so this is how a lever in one room opens
    a portcullis in another. The comparison runs through
    [`flag_values_equal`][osrlib.crawl.gates.flag_values_equal] and it is strict: a key
    that was never written equals nothing, `False` included, and a stored `True` never
    matches an authored `1`.

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
    """The flag name to read from the session flag store."""
    value: str | int | bool
    """The value the flag has to have. The comparison never treats values of different types as
    equal, so author the same type your [`SetFlag`][osrlib.crawl.commands.SetFlag] writes."""


class EffectActiveCondition(BaseModel):
    """A condition that holds when an active effect of `kind` is attached to a party member.

    Use it when the talisman has to be invoked rather than only carried. The item grants an
    effect, and the gate requires the effect. The condition reads the session's effects ledger
    ([`EffectsLedger`][osrlib.core.effects.EffectsLedger]). Effects
    attached to a location never count, because the test is for an effect on a party
    member.

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
    """The effect kind to look for. Effect kinds are an open, data-driven vocabulary
    (`"fatigue"`, or the `kind` of an
    [`EffectDefinition`][osrlib.core.effects.EffectDefinition] you wrote), so the field
    is a free string and nothing validates it against a catalog."""


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

A condition is read on the `condition` of a
[`GateSpec`][osrlib.crawl.gates.GateSpec], on the `conditions` of a
[`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec], and on the `conditions` of a quest's
[`TriggerClause`][osrlib.crawl.quests.TriggerClause], and each of those evaluates through
[`condition_holds`][osrlib.crawl.gates.condition_holds]."""


class GateSpec(BaseModel):
    """A condition guarding an attempt, with the authored text for both outcomes.

    Put one in the `requires` field of a
    [`DoorSpec`][osrlib.crawl.dungeon.DoorSpec] or a
    [`TransitionSpec`][osrlib.crawl.dungeon.TransitionSpec] when you build the dungeon,
    and the engine checks it every time the party tries that door, stair, or passage. A refused
    attempt returns the `refusal` beat inside its rejection and costs the party nothing:
    no dice, no game time, no item, and no change to the door. On a successful attempt,
    the command's event includes the `success` beat.

    A door standing open is not checked, so a gate on a door your game opens with
    [`SetDoorState`][osrlib.crawl.commands.SetDoorState] applies again only once the
    door closes.

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
    """The predicate the party has to satisfy. Exactly one condition, evaluated live at
    the moment of the attempt. To ask for two things at once, put the second condition on
    a trigger that writes the flag this gate reads."""
    narrative: NarrativeBlock | None = None
    """The authored text for both outcomes. osrlib reads two beats of a gate's block,
    `refusal` and `success`, and leaves the rest alone. With `None`, the gate has no text,
    and the rejection then includes no refusal text."""


def condition_holds(
    condition: ConditionSpec,
    *,
    members: Sequence[Character],
    flags: Mapping[str, str | int | bool],
    ledger: EffectsLedger,
) -> bool:
    """Evaluate one condition against live session state.

    Every part of the engine that checks an authored condition calls this function: the
    gate on a door or a stair, a [`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec]'s
    conditions, and a quest [`TriggerClause`][osrlib.crawl.quests.TriggerClause]'s. You
    rarely call it yourself, because the engine calls it for you at the moment of an
    attempt and the [`Interpreter`][osrlib.crawl.interpreter.Interpreter] calls it at
    the moment of a match. Call it directly when your listener has to check a condition the
    way the engine does, or to check an authored document against a session in a
    test.

    The call draws no dice, advances no clock, mutates nothing, and stores nothing.
    Every search goes through an ordered list, the party in marching order and each inventory
    in carried order, so the answer is deterministic. The members checked are every party
    member, living or dead, because the party carries its dead and their packs.

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
    """Compare two flag values the one strict way the engine compares them.

    Two values are equal when Python says they are and either both or neither are `bool`.
    Python treats `True == 1` as true, but in an authored document they are different
    values, so a flag your game set to `True` doesn't satisfy an authored `1`, and an
    authored `True` doesn't match a stored `1`. Call it when your code has to compare flag values the way the
    engine does.

    Every part of the engine that compares a flag value calls this function, which is why
    [`FlagEqualsCondition`][osrlib.crawl.gates.FlagEqualsCondition] on a gate and
    [`FlagSetPattern`][osrlib.crawl.triggers.FlagSetPattern] on a trigger can never
    disagree about what equality means.

    Args:
        stored: The value in the flag store, or the value a
            [`FlagSetEvent`][osrlib.crawl.events.FlagSetEvent] reports written.
        expected: The value the author wrote.

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

    This is the member a `has_item` toll takes the item from, and it matches exactly what
    [`condition_holds`][osrlib.crawl.gates.condition_holds] tests, so the member it
    returns is the one the engine charges. Call it when your code has to find the
    holder the engine would charge. The search is over every member, living or dead, and
    over each inventory in carried order, equipped slots included.

    Args:
        members: The party, in marching order, from `session.party.members`.
        item_id: The catalog id to look for.

    Returns:
        The member, or `None` when nobody carries one.

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
