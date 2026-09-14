"""Authored gates: the condition vocabulary, the gate model, and pure evaluation.

A gate is an authored predicate the engine checks when the party attempts something,
such as opening a door or taking a stair.

Where a gate sits. You write a [`GateSpec`][osrlib.crawl.gates.GateSpec] into the
`requires` field of a [`DoorSpec`][osrlib.crawl.dungeon.DoorSpec] or a
[`TransitionSpec`][osrlib.crawl.dungeon.TransitionSpec], inside the dungeon geometry of
an [`Adventure`][osrlib.crawl.adventure.Adventure]. A gate hangs nowhere else. The
exploration handlers behind
[`GameSession.execute`][osrlib.crawl.session.GameSession.execute] evaluate it as the
last validation step of [`OpenDoor`][osrlib.crawl.commands.OpenDoor],
[`ForceDoor`][osrlib.crawl.commands.ForceDoor], and
[`UseStairs`][osrlib.crawl.commands.UseStairs]. A gate that refuses produces the
rejection `exploration.door.gate_refused` or `exploration.transition.gate_refused`,
which includes the authored refusal beat, and no event at all. A gate that opens puts its
success beat on the command's own event: a
[`DoorEvent`][osrlib.crawl.events.DoorEvent] for a door, a
[`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent] for a transition
that crosses into a new level or dungeon. A gate that charges a toll reports it as an
[`ItemConsumedEvent`][osrlib.crawl.events.ItemConsumedEvent] ahead of that event.

A gate is stateless content. [`condition_holds`][osrlib.crawl.gates.condition_holds]
reads live session state at the moment of the attempt and stores nothing, so a key that
gets dropped or sold stops opening its door.

The condition vocabulary is a discriminated union that grows additively:

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

Gates and locks are separate layers. A lock is stateful mechanics with dice and
per-character memory. A gate is a stateless authored predicate. A door with both
requires both. Evaluation draws no dice, costs no game time, and mutates nothing, which
is what lets a gate refusal be an ordinary command rejection.

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
    """The party carries an item with `item_id`, equipment or magic item alike.

    Write this into the `condition` of a [`GateSpec`][osrlib.crawl.gates.GateSpec] for
    the door that needs a key, and into the `conditions` of a
    [`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec] or a quest's
    [`TriggerClause`][osrlib.crawl.quests.TriggerClause] to narrow a firing to a party
    that is still carrying something. Any member's carried inventory satisfies it,
    equipped slots included, because carrying is the test. The party carries its
    dead and their packs, so a key in a dead member's pack still counts.

    `item_id` has to resolve against the effective equipment catalog, which is the
    shipped catalog plus the adventure's own bundled items, or against the magic-item
    catalog. A gate naming an id that is in neither catalog fails adventure validation.

    Examples:
        ```python
        from osrlib.crawl.gates import HasItemCondition

        brass_key = HasItemCondition(item_id="brass_key")
        assert not brass_key.consumes  # carrying is enough, and nothing is taken
        ```
    """

    model_config = ConfigDict(frozen=True)

    condition_type: Literal["has_item"] = "has_item"
    """The discriminator value, `has_item`. It appears in every document that
    includes this condition, and you never set it yourself."""
    item_id: str = Field(min_length=1)
    """The catalog id to look for, from the equipment catalog (shipped items plus the
    adventure's bundled ones) or the magic-item catalog."""
    consumes: bool = False
    """Whether a success takes the item. `True` makes it a toll: one instance leaves the
    first holder in marching order every time the gated command succeeds, so a door that
    swings shut needs another key. A trigger's or a quest's conditions reject `True` at
    parse, because they observe an event that has already happened and have no attempt
    of their own to charge against."""


class FlagEqualsCondition(BaseModel):
    """A session flag holds `value`: the lever that opens the portcullis.

    Your game writes flags with [`SetFlag`][osrlib.crawl.commands.SetFlag], and a
    trigger's consequence can write one too, so this is how a lever in one room governs
    a door in another. The comparison runs through
    [`flag_values_equal`][osrlib.crawl.gates.flag_values_equal] and it is strict: a key
    that was never written equals nothing, `False` included, and a stored `True` never
    matches an authored `1`.

    Examples:
        ```python
        from osrlib.crawl.gates import FlagEqualsCondition

        raised = FlagEqualsCondition(key="crypt.portcullis", value="raised")
        assert raised.condition_type == "flag_equals"
        ```
    """

    model_config = ConfigDict(frozen=True)

    condition_type: Literal["flag_equals"] = "flag_equals"
    """The discriminator value, `flag_equals`. It appears in every document that
    includes this condition, and you never set it yourself."""
    key: str = Field(min_length=1)
    """The flag name to read from the session flag store."""
    value: str | int | bool
    """The value the flag has to hold. The comparison keeps types apart, so author the
    same type your [`SetFlag`][osrlib.crawl.commands.SetFlag] writes."""


class EffectActiveCondition(BaseModel):
    """An active effect of `kind` is attached to some party member.

    Use it when the talisman has to be invoked rather than merely carried: the item
    grants an effect, and the gate asks for the effect. The session's effects ledger
    ([`EffectsLedger`][osrlib.core.effects.EffectsLedger]) is what gets read. Effects
    attached to a location never count, because the test is for an effect on a party
    member.

    Examples:
        ```python
        from osrlib.crawl.gates import EffectActiveCondition

        warded = EffectActiveCondition(kind="ward_of_the_deep")
        assert warded.condition_type == "effect_active"
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
Annotate a field with this alias when you write your own model that holds authored
conditions. Pydantic then picks the member from the `condition_type` value in the
document. New kinds join the union additively, and the discriminator values are wire
values that appear in every document that includes a gate.

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
    and the engine checks it every time the party tries that door or stair. A refused
    attempt returns the `refusal` beat inside its rejection and costs the party nothing:
    no dice, no game time, no item, and no change to the door. A successful attempt puts
    the `success` beat on the command's own event.

    A door standing open is not checked, so a gate on a door your game opens with
    [`SetDoorState`][osrlib.crawl.commands.SetDoorState] applies again only once the
    door closes.

    Examples:
        ```python
        from osrlib.crawl.gates import GateSpec, HasItemCondition
        from osrlib.crawl.narrative import NarrativeBlock

        sentinel = GateSpec(
            condition=HasItemCondition(item_id="brass_key"),
            narrative=NarrativeBlock(
                refusal="The bronze sentinel folds its arms. Brass, it says. Brass or nothing.",
                success="The brass key turns in the sentinel's palm and the door swings wide.",
            ),
        )
        assert sentinel.condition.item_id == "brass_key"
        ```
    """

    model_config = ConfigDict(frozen=True)

    condition: ConditionSpec
    """The predicate the party has to satisfy. Exactly one condition, evaluated live at
    the moment of the attempt. To ask for two things at once, put the second condition on
    a trigger that writes the flag this gate reads."""
    narrative: NarrativeBlock | None = None
    """The authored text for both outcomes. A gate reads two beats of the block,
    `refusal` and `success`; the rest are left alone. `None` gates the way with no words
    at all, and the rejection then includes no refusal text."""


def condition_holds(
    condition: ConditionSpec,
    *,
    members: Sequence[Character],
    flags: Mapping[str, str | int | bool],
    ledger: EffectsLedger,
) -> bool:
    """Evaluate one condition against live session state.

    Every surface that asks whether an authored condition holds asks through here: the
    gate on a door or a stair, a [`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec]'s
    conditions, and a quest [`TriggerClause`][osrlib.crawl.quests.TriggerClause]'s. You
    rarely call it yourself, because the engine calls it for you at the moment of an
    attempt and the [`Interpreter`][osrlib.crawl.interpreter.Interpreter] calls it at
    the moment of a match. Call it directly when your own listener wants to ask the same
    question the engine asks, or to check an authored document against a session in a
    test.

    The call draws no dice, advances no clock, mutates nothing, and stores nothing.
    Every walk is over an ordered list, the party in marching order and each inventory
    in carried order, so the answer is deterministic. The member domain is every party
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
        flags = {"portcullis": "raised"}
        assert condition_holds(condition, members=[], flags=flags, ledger=EffectsLedger())
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

    The test is equality plus matching boolness. `True == 1` in Python, so a flag your
    game set to `True` does not satisfy an authored `1`, and an authored `True` does not
    match a stored `1`: they are different values in an authored document even though
    Python calls them equal. Call it when your own code has to answer the same question
    about a flag that the engine answers.

    Every surface that asks whether a flag holds a value asks through here, which is why
    [`FlagEqualsCondition`][osrlib.crawl.gates.FlagEqualsCondition] on a gate and
    [`FlagSetPattern`][osrlib.crawl.triggers.FlagSetPattern] on a trigger can never
    disagree about what equality means.

    Args:
        stored: The value the flag store holds, or the value a
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

    This is the consumption target of a `has_item` toll, and it matches exactly what
    [`condition_holds`][osrlib.crawl.gates.condition_holds] tests, so the member it
    names is the one the engine charges. Call it when your own code has to name the
    holder the engine would charge. The search is over every member, living or dead, and
    over each inventory in carried order, equipped slots included.

    Args:
        members: The party, in marching order, from `session.party.members`.
        item_id: The catalog id to look for.

    Returns:
        The member, or `None` when nobody carries one.

    Examples:
        ```python
        from osrlib.crawl.gates import first_holder

        assert first_holder([], "brass_key") is None
        ```
    """
    for member in members:
        if member.inventory.carried_item(item_id) is not None:
            return member
    return None
