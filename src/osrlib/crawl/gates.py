"""Authored gates: the condition vocabulary, the gate model, and pure evaluation.

A gate is an authored predicate the engine checks when the party *attempts*
something — opening a door, taking a stair. It is a stateless content object:
[`condition_holds`][osrlib.crawl.gates.condition_holds] reads live session state
at the moment of the attempt and stores nothing, so a key dropped or sold stops
opening its door and evaluation never drifts from the truth.

The condition vocabulary is a discriminated union that grows additively:

- [`HasItemCondition`][osrlib.crawl.gates.HasItemCondition] — some party member's
  carried inventory holds an item with that catalog id. With `consumes=True`, the
  successful command takes one instance from the first holder in marching order.
- [`FlagEqualsCondition`][osrlib.crawl.gates.FlagEqualsCondition] — a session flag
  ([`SetFlag`][osrlib.crawl.commands.SetFlag]) holds a value.
- [`EffectActiveCondition`][osrlib.crawl.gates.EffectActiveCondition] — an active
  effect of that kind is attached to a party member, so an author can ask for the
  talisman invoked rather than merely carried.

Gates and locks are orthogonal layers: `locked` is stateful mechanics with dice
and per-character memory, a gate is a stateless authored predicate, and a door
carrying both requires both. Evaluation is pure — it draws no dice, costs no game
time, and mutates nothing — which is what lets a gate refusal be an ordinary
command rejection.
"""

from collections.abc import Mapping, Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from osrlib.core.character import Character
from osrlib.core.effects import EffectsLedger
from osrlib.core.items import ItemInstance, MagicItemInstance
from osrlib.crawl.narrative import NarrativeBlock

__all__ = [
    "ConditionSpec",
    "EffectActiveCondition",
    "FlagEqualsCondition",
    "GateSpec",
    "HasItemCondition",
    "condition_holds",
    "first_holder",
]


class HasItemCondition(BaseModel):
    """The party carries an item with `item_id` — equipment or magic item.

    Any member's carried inventory satisfies it, equipped slots included: carrying
    is the whole test. `consumes=True` makes the item a toll — one instance leaves
    the first holder in marching order when the gated command succeeds, per
    success, so a door that swings shut wants another key.
    """

    model_config = ConfigDict(frozen=True)

    condition_type: Literal["has_item"] = "has_item"
    item_id: str = Field(min_length=1)
    consumes: bool = False


class FlagEqualsCondition(BaseModel):
    """A session flag holds `value` — the lever that opens the portcullis.

    The comparison is strict: an absent key equals nothing (`False` included), and
    a stored `True` never matches an authored `1`.
    """

    model_config = ConfigDict(frozen=True)

    condition_type: Literal["flag_equals"] = "flag_equals"
    key: str = Field(min_length=1)
    value: str | int | bool


class EffectActiveCondition(BaseModel):
    """An active effect of `kind` is attached to some party member.

    Effect kinds are an open, data-driven vocabulary (`"fatigue"`, a custom
    [`EffectDefinition.kind`][osrlib.core.effects.EffectDefinition]), so the field
    is a free string. Effects attached to a location never count — the test is
    what the party carries in its bones.
    """

    model_config = ConfigDict(frozen=True)

    condition_type: Literal["effect_active"] = "effect_active"
    kind: str = Field(min_length=1)


ConditionSpec = Annotated[
    HasItemCondition | FlagEqualsCondition | EffectActiveCondition,
    Field(discriminator="condition_type"),
]
"""The condition union, discriminated on `condition_type` (`has_item`, `flag_equals`,
`effect_active`). New kinds join it additively; the discriminator values are wire
values and serialize into every document that carries a gate."""


class GateSpec(BaseModel):
    """A condition guarding an attempt, with the authored text for both outcomes.

    The gate is the object the narrative hangs on: a refused attempt returns the
    block's `refusal` beat in its rejection, and a successful one rides the
    `success` beat on the command's event.
    """

    model_config = ConfigDict(frozen=True)

    condition: ConditionSpec
    narrative: NarrativeBlock | None = None


def _carried_instance(member: Character, item_id: str) -> ItemInstance | MagicItemInstance | None:
    """The member's first carried instance of a catalog id, or `None`.

    Mundane instances match on their template's id and magic instances on their
    `template_id` — the union `has_item` speaks. Valuables never match: gems and
    jewellery carry no catalog id. A spent stack (a magic quiver shot down to zero,
    the only instance a quantity of zero is expressible on) is not carrying: the
    empty quiver satisfies nothing and pays no toll.
    """
    for instance in member.inventory.all_instances():
        if instance.quantity < 1:
            continue
        if isinstance(instance, MagicItemInstance):
            if instance.template_id == item_id:
                return instance
        elif instance.template.id == item_id:
            return instance
    return None


def condition_holds(
    condition: ConditionSpec,
    *,
    members: Sequence[Character],
    flags: Mapping[str, str | int | bool],
    ledger: EffectsLedger,
) -> bool:
    """Evaluate one condition against live session state.

    Pure: no draws, no clock, no mutation, nothing stored. Every walk is over an
    ordered list — the party in marching order, each inventory in carried order —
    so the answer is deterministic.

    The member domain is every party member, living or dead. The party carries its
    dead and their packs, so a key that rides a corpse still opens its door.

    Args:
        condition: The condition to evaluate.
        members: The party, in marching order.
        flags: The session flag store.
        ledger: The session's effects ledger.

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
        return any(_carried_instance(member, condition.item_id) is not None for member in members)
    if isinstance(condition, FlagEqualsCondition):
        if condition.key not in flags:
            return False
        stored = flags[condition.key]
        # `True == 1` in Python, so boolness has to match too: an authored 1 must
        # not be satisfied by a flag somebody set to True.
        return stored == condition.value and isinstance(stored, bool) == isinstance(condition.value, bool)
    return any(member.id is not None and ledger.active_on(member.id, condition.kind) for member in members)


def first_holder(members: Sequence[Character], item_id: str) -> Character | None:
    """The first member in marching order carrying an item with `item_id`.

    The consumption target of a `has_item` toll, matching exactly what
    [`condition_holds`][osrlib.crawl.gates.condition_holds] tests.

    Args:
        members: The party, in marching order.
        item_id: The catalog id to look for.

    Returns:
        The member, or `None` when nobody carries one.
    """
    for member in members:
        if _carried_instance(member, item_id) is not None:
            return member
    return None
