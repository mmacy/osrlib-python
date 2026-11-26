"""The party member or creature a command names, and the helpers that find, take, add, and remove a member's items."""

from typing import Any

from osrlib.core.combat import incapacitated
from osrlib.core.effects import Condition, has_condition
from osrlib.core.items import ItemInstance, ItemTemplate, MagicItemInstance
from osrlib.core.validation import Rejection


def _member_able(session, character_id: str) -> tuple[Any, list[Rejection]]:
    try:
        member = session.member(character_id)
    except ValueError:
        return None, [Rejection(code="session.command.unknown_member", params={"character": character_id})]
    if incapacitated(member):
        return None, [Rejection(code="session.command.member_incapacitated", params={"character": character_id})]
    return member, []


def _actable_target(session, target_id: str) -> tuple[Any, Any]:
    """Return the creature with `target_id` that the party can act on now, and its encounter group.

    A command can name as a target only a party member, in any state, or a creature in a group of the open encounter
    that hasn't fled: the creatures the player view lists in
    [`PlayerView.party`][osrlib.crawl.views.PlayerView.party] and
    [`EncounterGroupView.members`][osrlib.crawl.views.EncounterGroupView.members]. A monster or an NPC adventurer
    of an encounter that has ended, a monster of a group that fled, and a monster spawned into no encounter stay in
    `session.registry()`, but this returns `(None, None)` for them, as it does for an id that matches nothing, so
    a command's refusal reveals nothing about a creature the party can't see.

    Returns:
        `(member, None)` for a party member, `(creature, group)` for a creature of the open encounter, and
            `(None, None)` for any other id. The call reads state and takes no draw.
    """
    for member in session.party.members:
        if member.id == target_id:
            return member, None
    if session.encounter is None:
        return None, None
    group = next((group for group in session.encounter.groups if target_id in group.monster_ids), None)
    creature = session.registry().get(target_id) if group is not None and not group.fled else None
    return (creature, group) if creature is not None else (None, None)


def _spell_target(session, target_id: str) -> tuple[Any, Any]:
    """Return the creature with `target_id` that a spell or a scroll can name, and its encounter group.

    This is `_actable_target` without the dead creatures of the encounter, because a spell can't name a dead enemy.
    A party member counts in any state, so *raise dead* and *stone to flesh* can name a dead or petrified member.
    `CastSpell`, a scroll read with `UseItem`, and a battle's `cast` and scroll declarations use this lookup, and
    each refuses with `magic.cast.unknown_target` any id for which it returns `(None, None)`.
    """
    creature, group = _actable_target(session, target_id)
    if group is not None and has_condition(creature, Condition.DEAD):
        return None, None
    return creature, group


def _consume_item(member, item_id: str, quantity: int = 1) -> ItemInstance | MagicItemInstance | None:
    """Take units of a carried item and return the instance they came from.

    The lookup is
    [`Inventory.carried_item`][osrlib.core.items.Inventory.carried_item], which searches
    everything the member carries, mundane and magic alike. A `has_item` gate checks
    with the same lookup, so the engine can take as a toll any item that satisfies
    the gate. A stack spent to zero leaves the inventory, and a larger stack goes down
    by the quantity taken.

    Args:
        member (osrlib.core.character.Character): The carrier.
        item_id: The catalog id to take.
        quantity: How many units.

    Returns:
        The instance the units came from, or `None` when the member carries no
        such item or too few of it (in which case nothing is taken).
    """
    instance = member.inventory.carried_item(item_id)
    if instance is None or instance.quantity < quantity:
        return None
    if instance.quantity == quantity:
        _remove_instance(member, instance)
    else:
        instance.quantity -= quantity
    return instance


def _find_item(member, item_id: str):
    """The mundane-item lookup: presence checks for standard equipment.

    Narrower than
    [`Inventory.carried_item`][osrlib.core.items.Inventory.carried_item] on
    purpose. A torch to light, a tinder box to strike it with, and thieves' tools
    to pick a lock with are mundane equipment by definition, and a caller that
    goes on to read `instance.template` needs the mundane instance this returns.
    Anything that finds an item in order to *take* one uses the wider lookup, so
    a found item is always one that can be taken.
    """
    for instance in member.inventory.all_instances():
        if not isinstance(instance, MagicItemInstance) and instance.template.id == item_id:
            return instance
    return None


def _remove_instance(member, instance: ItemInstance | MagicItemInstance) -> None:
    """Take one instance out of wherever it is carried: pack, hands, slot, or ring."""
    inventory = member.inventory
    if any(existing is instance for existing in inventory.items):
        inventory.items.remove(instance)
    elif any(existing is instance for existing in inventory.wielded):
        inventory.wielded.remove(instance)
    elif isinstance(instance, MagicItemInstance) and any(existing is instance for existing in inventory.rings):
        inventory.rings.remove(instance)
    elif inventory.worn_armour is instance:
        inventory.worn_armour = None
    elif inventory.shield is instance:
        inventory.shield = None


def _consumed_item_id(instance) -> str:
    """What a consumption reports: a magic item's instance id, else the catalog id.

    A player-visible event never includes an unidentified item's true identity, so
    the event for a consumed magic item contains only its session-scoped instance id.
    """
    return instance.instance_id if isinstance(instance, MagicItemInstance) else instance.template.id


def _grant_mundane(member, template: ItemTemplate, quantity: int = 1) -> None:
    """Add a mundane equipment item to a member's pack, merging with any like stack."""
    existing = next(
        (
            instance
            for instance in member.inventory.items
            if not isinstance(instance, MagicItemInstance) and instance.template.id == template.id
        ),
        None,
    )
    if existing is not None:
        existing.quantity += quantity
    else:
        member.inventory.items.append(ItemInstance(template=template, quantity=quantity))
