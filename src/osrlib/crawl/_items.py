"""The commands that drop, give, equip, and unequip items.

A drop during an encounter and a battle's `give_item` declaration use the same checks and moves as these commands.
"""

from osrlib.core.events import Event
from osrlib.core.items import Coins, ItemInstance, MagicItemInstance, equip, unequip, validate_equip, validate_unequip
from osrlib.core.validation import Rejection
from osrlib.crawl._hauling import _DENOMINATIONS, _add_to_pile
from osrlib.crawl._magic_items import _attach_worn_item_effects, _release_instance_effects
from osrlib.crawl._party_members import _consume_item, _grant_mundane, _member_able, _remove_instance
from osrlib.crawl.commands import DropItems, EquipItem, GiveItems, SessionMode, UnequipItem
from osrlib.crawl.dungeon import DroppedItem, DropPile
from osrlib.crawl.events import ItemsDroppedEvent, ItemsGivenEvent


def _handle_drop_items(session, command: DropItems) -> tuple[list[Rejection], list[Event]]:
    if session.mode is SessionMode.ENCOUNTER:
        from osrlib.crawl import encounter as encounter_module

        return encounter_module._handle_drop_during_encounter(session, command)
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    rejections = _validate_carried(member, command.item_ids, command.coins)
    if rejections:
        return rejections, []
    events = _apply_drop(session, member, command, to_pile=True)
    return [], events


def _validate_carried(member, item_ids: tuple[str, ...], coins: Coins) -> list[Rejection]:
    """Guard the item/coin handout shared by drop and give.

    The member must carry every named item and coin, and no revealed cursed item
    may leave its bearer.

    The instance id of a magic item or a valuable identifies one object, so a command
    that lists it twice requests something the member doesn't carry: the object leaves
    on the first listing, and nothing is left for the second.
    """
    counts: dict[str, int] = {}
    named_instances: set[str] = set()
    for item_id in item_ids:
        magic = member.inventory.magic_item(item_id)
        if magic is not None and magic.cursed_revealed:
            # A revealed cursed item stays with its bearer until *remove curse*.
            return [Rejection(code="items.curse.stuck", params={"item": item_id})]
        if magic is not None or any(valuable.instance_id == item_id for valuable in member.inventory.valuables):
            if item_id in named_instances:
                return [Rejection(code="exploration.item.not_carried", params={"item": item_id})]
            named_instances.add(item_id)
            continue
        counts[item_id] = counts.get(item_id, 0) + 1
    for item_id, needed in counts.items():
        held = sum(
            instance.quantity
            for instance in member.inventory.all_instances()
            if not isinstance(instance, MagicItemInstance) and instance.template.id == item_id
        )
        if held < needed:
            return [Rejection(code="exploration.item.not_carried", params={"item": item_id})]
    purse = member.inventory.purse
    for denomination in ("pp", "gp", "ep", "sp", "cp"):
        if getattr(coins, denomination) > getattr(purse, denomination):
            return [Rejection(code="exploration.item.not_carried", params={"item": denomination})]
    return []


def _handle_give_items(session, command: GiveItems) -> tuple[list[Rejection], list[Event]]:
    giver, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    if command.recipient_id == command.character_id:
        return [Rejection(code="exploration.give.same_member", params={"character": command.character_id})], []
    recipient, rejections = _member_able(session, command.recipient_id)
    if rejections:
        return rejections, []
    rejections = _validate_carried(giver, command.item_ids, command.coins)
    if rejections:
        return rejections, []
    events = _apply_give(session, giver, recipient, command.item_ids, command.coins)
    if session.mode is SessionMode.ENCOUNTER:
        from osrlib.crawl import encounter as encounter_module

        events.extend(encounter_module._pass_round(session))
    return [], events


def _apply_give(session, giver, recipient, item_ids: tuple[str, ...], coins: Coins) -> list[Event]:
    """Move the named goods and coins from giver to recipient, and take no time.

    [`GiveItems`][osrlib.crawl.commands.GiveItems] and a battle's `give_item` declaration both hand
    goods over through this, after their checks have passed. A given magic item releases any worn
    effects first, then arrives unequipped in the recipient's pack. Mundane items merge into a like
    stack, and coins move purse to purse.
    """
    events: list[Event] = []
    for item_id in item_ids:
        magic = giver.inventory.magic_item(item_id)
        if magic is not None:
            events.extend(_release_instance_effects(session, magic))
            _remove_instance(giver, magic)
            recipient.inventory.items.append(magic)
            continue
        valuable = next(
            (candidate for candidate in giver.inventory.valuables if candidate.instance_id == item_id), None
        )
        if valuable is not None:
            giver.inventory.valuables.remove(valuable)
            recipient.inventory.valuables.append(valuable)
            continue
        # The giver's instance brings its template across: no catalog
        # resolution, so an adventure-bundled item hands over like any other. One
        # lookup takes it and returns what was taken, so nothing can shift between.
        carried = _consume_item(giver, item_id)
        if not isinstance(carried, ItemInstance):  # unreachable: magic left through the branch above
            raise ValueError(f"{giver.name} does not carry {item_id!r}")
        _grant_mundane(recipient, carried.template)
    giver_purse = giver.inventory.purse
    recipient_purse = recipient.inventory.purse
    for denomination in ("pp", "gp", "ep", "sp", "cp"):
        moved = getattr(coins, denomination)
        setattr(giver_purse, denomination, getattr(giver_purse, denomination) - moved)
        setattr(recipient_purse, denomination, getattr(recipient_purse, denomination) + moved)
    events.append(
        ItemsGivenEvent(
            character_id=giver.id,
            recipient_id=recipient.id,
            item_ids=tuple(item_ids),
            coins_gp_value=coins.value_gp,
        )
    )
    return events


def _apply_drop(session, member, command: DropItems, *, to_pile: bool) -> list[Event]:
    """Remove the dropped goods, onto the cell's pile or scattered (pursuit bait)."""
    goods = DropPile(coins=command.coins)
    for item_id in command.item_ids:
        magic = member.inventory.magic_item(item_id)
        if magic is not None:
            _remove_instance(member, magic)
            goods.magic_items.append(magic)
            continue
        valuable = next(
            (candidate for candidate in member.inventory.valuables if candidate.instance_id == item_id), None
        )
        if valuable is not None:
            member.inventory.valuables.remove(valuable)
            goods.valuables.append(valuable)
            continue
        _consume_item(member, item_id)
        existing = next((entry for entry in goods.items if entry.item_id == item_id), None)
        if existing is None:
            goods.items.append(DroppedItem(item_id=item_id, quantity=1))
        else:
            existing.quantity += 1
    purse = member.inventory.purse
    for denomination in _DENOMINATIONS:
        setattr(purse, denomination, getattr(purse, denomination) - getattr(command.coins, denomination))
    if to_pile:
        _add_to_pile(session, goods)
    return [
        ItemsDroppedEvent(
            character_id=member.id, item_ids=tuple(command.item_ids), coins_gp_value=command.coins.value_gp
        )
    ]


def _handle_equip_item(session, command: EquipItem) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    instance = next(
        (
            candidate
            for candidate in member.inventory.items
            if (
                candidate.instance_id == command.item_id
                if isinstance(candidate, MagicItemInstance)
                else candidate.template.id == command.item_id
            )
        ),
        None,
    )
    if instance is None:
        return [Rejection(code="exploration.item.not_carried", params={"item": command.item_id})], []
    definition = member.definition
    equip_rejections = validate_equip(definition, instance, member.inventory)
    if equip_rejections:
        return equip_rejections, []
    equip(member.inventory, definition, instance)
    events: list[Event] = []
    if isinstance(instance, MagicItemInstance):
        events.extend(_attach_worn_item_effects(session, member, instance))
    return [], events


def _handle_unequip_item(session, command: UnequipItem) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    inventory = member.inventory
    instance = next(
        (
            candidate
            for candidate in inventory.equipped_instances()
            if (
                candidate.instance_id == command.item_id
                if isinstance(candidate, MagicItemInstance)
                else candidate.template.id == command.item_id
            )
        ),
        None,
    )
    if instance is None:
        return [Rejection(code="exploration.item.not_equipped", params={"item": command.item_id})], []
    unequip_rejections = validate_unequip(inventory, instance)
    if unequip_rejections:
        return unequip_rejections, []
    events: list[Event] = []
    if isinstance(instance, MagicItemInstance):
        events.extend(_release_instance_effects(session, instance))
    unequip(inventory, instance)
    return [], events
