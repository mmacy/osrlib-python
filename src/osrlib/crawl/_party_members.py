"""The party member or creature a command names, and the helpers that find, take, add, and remove a member's items."""

from typing import Any

from osrlib.core.combat import incapacitated
from osrlib.core.effects import Condition, has_condition
from osrlib.core.items import ItemInstance, ItemTemplate, MagicItemInstance, ValuableInstance
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
    that hasn't fled: the members in [`PlayerView.party`][osrlib.crawl.views.PlayerView.party], and the creatures
    in [`EncounterGroupView.members`][osrlib.crawl.views.EncounterGroupView.members] of a group whose `fled` is
    false. A monster or an NPC adventurer
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


def _spell_cell(session, target_ref: str) -> str | None:
    """Return `target_ref` when it names a cell a spell or a scroll can be aimed at, or None when it doesn't.

    That's a cell of the level the party stands on, in the canonical form
    [`cell_ref`][osrlib.crawl.dungeon.cell_ref] builds. A reference that doesn't parse, names no cell of the
    adventure, or names a cell of another level or another dungeon gives None, and so does every reference while the
    party isn't in a dungeon. `CastSpell`, a scroll read with `UseItem`, and a battle's `cast` and scroll
    declarations use this check, and each refuses with `magic.cast.unknown_target` any reference for which it
    returns None. Which cells of the party's level are in reach is left open, so any cell of the level passes. The
    call reads state and takes no draw.
    """
    from osrlib.crawl._feature_handles import _parse_cell_location

    location = session.dungeon_state.location
    if location.kind != "dungeon":
        return None
    parsed = _parse_cell_location(session, target_ref)
    if parsed is None or parsed[:2] != (location.dungeon_id, location.level_number):
        return None
    return target_ref


def _named_spell_target(session, target_ref: str, *, in_battle: bool) -> tuple[Any, Any, Rejection | None]:
    """Resolve one target a spell or a scroll names, as a `(target, group, rejection)` triple.

    A `cell:` reference resolves to itself when `_spell_cell` accepts it, and any other id to the creature
    `_spell_target` returns, with its encounter group. A reference or id that neither accepts gets
    `magic.cast.unknown_target`. A creature with the `invisible` condition can be refused too:

    - With `in_battle`, for a battle's `cast` and scroll declarations, any invisible creature gets
      `battle.declaration.invisible_target`: a monster, an NPC adventurer, another member, or the caster named by id.
    - Without it, for `CastSpell` and a scroll read with `UseItem`, an invisible monster or NPC adventurer of the
      open encounter gets `magic.cast.invisible_target`. An invisible party member is still a target. `CastSpell`
      is never legal while an encounter is open, so only a scroll read can get this code.

    A dead creature of the encounter gets `magic.cast.unknown_target` from `_spell_target` before the visibility
    check, so its invisibility is never reported.

    Each rejection has the reference or id as `target`. The target and group are None when there's a rejection, and
    the group is None for a cell or a party member. The call reads state and takes no draw.
    """
    if target_ref.startswith("cell:"):
        if _spell_cell(session, target_ref) is None:
            return None, None, Rejection(code="magic.cast.unknown_target", params={"target": target_ref})
        return target_ref, None, None
    creature, group = _spell_target(session, target_ref)
    if creature is None:
        return None, None, Rejection(code="magic.cast.unknown_target", params={"target": target_ref})
    if has_condition(creature, Condition.INVISIBLE):
        # The player view shows that the group has an invisible member, in `visible_conditions` and
        # `condition_counts`, and a party member's conditions. A refusal tells the players which member it is.
        if in_battle:
            return None, None, Rejection(code="battle.declaration.invisible_target", params={"target": target_ref})
        if group is not None:
            return None, None, Rejection(code="magic.cast.invisible_target", params={"target": target_ref})
    return creature, group, None


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


def _held_item(member, item_id: str) -> ItemInstance | MagicItemInstance | ValuableInstance | None:
    """Return what a member carries under the id an item event lists it by, or `None`.

    A magic item's or a valuable's instance id identifies that item, and any other id is a mundane item's catalog id,
    which names the member's first stack of it.
    """
    inventory = member.inventory
    magic = inventory.magic_item(item_id)
    if magic is not None:
        return magic
    valuable = next((piece for piece in inventory.valuables if piece.instance_id == item_id), None)
    if valuable is not None:
        return valuable
    carried = inventory.carried_item(item_id)
    return carried if isinstance(carried, ItemInstance) else None


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
