"""Taking treasure, and dividing a haul among the party.

`TakeTreasure` empties a cache or a drop pile into the carriers' packs. Items go to a member whose class can use them,
gems and jewellery are divided by value, and coins are divided evenly. What nobody can carry goes into the drop pile on
the party's cell. Under the `immediate` XP timing, the session awards the treasure's experience as the party takes it.
`DropItems` and the loot that slain monsters drop also use the drop pile functions.
"""

from osrlib.core.combat import incapacitated
from osrlib.core.events import Event
from osrlib.core.items import (
    COIN_VALUES_CP,
    Coins,
    ItemInstance,
    MagicItemInstance,
    _max_load_coins,
    _treasure_room_coins,
    magic_item_template,
    tracked_weight_coins,
    usable_by_class,
    validate_equip,
)
from osrlib.core.ruleset import EncumbranceMode
from osrlib.core.validation import Rejection
from osrlib.crawl._arrival import _spring_check
from osrlib.crawl._odometer import _spend_turn
from osrlib.crawl._party_location import _cell_ref, _feature_ref, _features_here
from osrlib.crawl._party_members import _member_able
from osrlib.crawl._treasure import _treasure_tier
from osrlib.crawl.commands import TakeTreasure
from osrlib.crawl.dungeon import DroppedItem, DropPile
from osrlib.crawl.events import ItemAcquiredEvent, ItemsLeftBehindEvent, TrapEvent


def _handle_take_treasure(session, command: TakeTreasure) -> tuple[list[Rejection], list[Event]]:
    living = session.party.living_members()
    if not living:
        return [Rejection(code="session.command.no_living_members")], []
    carriers = living
    if command.recipient_id is not None:
        recipient, rejections = _member_able(session, command.recipient_id)
        if rejections:
            return rejections, []
        carriers = [recipient]
    opener = carriers[0]  # whoever reaches in: the named recipient, else the leader
    state = session.dungeon_state
    events: list[Event] = []
    if command.feature_id == "pile":
        ref = _cell_ref(session)
        pile = state.piles.get(ref)
        if pile is None:
            return [Rejection(code="exploration.feature.unknown", params={"feature": "pile"})], []
        del state.piles[ref]
        events.extend(_take_haul(session, carriers, pile))
        turn_events, _ = _spend_turn(session)
        events.extend(turn_events)
        return [], events
    cache = state.generated_caches.get(command.feature_id)
    if cache is not None:
        if cache.cell_ref != _cell_ref(session):
            return [Rejection(code="exploration.feature.unknown", params={"feature": command.feature_id})], []
        haul = DropPile(coins=cache.coins, valuables=list(cache.valuables), magic_items=list(cache.magic_items))
        del state.generated_caches[command.feature_id]
        events.extend(_take_haul(session, carriers, haul))
        turn_events, _ = _spend_turn(session)
        events.extend(turn_events)
        return [], events
    feature = next((f for f in _features_here(session) if f.id == command.feature_id), None)
    if feature is None or feature.kind != "treasure_cache":
        return [Rejection(code="exploration.feature.unknown", params={"feature": command.feature_id})], []
    ref = _feature_ref(session, feature)
    if ref in state.emptied_caches:
        return [Rejection(code="exploration.feature.emptied", params={"feature": command.feature_id})], []
    trap = feature.trap
    if trap is not None and ref not in state.sprung_traps and ref not in state.removed_traps:
        spring_events, sprung = _spring_check(session, trap, ref, triggerer=opener)
        events.extend(spring_events)
        if not sprung and ref in state.found_traps:
            # The party knows the trap is there and sees it fail to fire. An
            # unknown trap that doesn't spring stays referee-only (no leak).
            events.append(TrapEvent(code="exploration.trap.safe", trap_ref=ref, character_id=opener.id))
    # The spring check may have felled somebody. Whoever is still standing takes the
    # haul, and when the named recipient went down, the rest of the party takes it
    # instead.
    carriers = [member for member in carriers if not incapacitated(member)] or session.party.living_members()
    if not carriers:
        # The trap took the whole party: looting needs a living looter. Nothing is
        # instantiated, the cache stays sealed with its contents for whoever comes
        # back for them, and only the turn the attempt cost passes.
        turn_events, _ = _spend_turn(session)
        events.extend(turn_events)
        return [], events
    from osrlib.core.items import ValuableInstance
    from osrlib.core.treasure import TREASURE_STREAM, instantiate_magic_item

    haul = DropPile(
        coins=feature.coins,
        items=[DroppedItem(item_id=item_id, quantity=1) for item_id in feature.item_ids],
        # Named valuables the author placed, like a quest item, are created on take.
        valuables=[
            ValuableInstance(
                instance_id=session.allocator.allocate("valuable"),
                kind=spec.kind,
                name=spec.name,
                value_gp=spec.value_gp,
                weight_coins=spec.weight_coins,
            )
            for spec in feature.valuables
        ],
        # Magic items the author placed are created on take as well. Their details roll
        # on the treasure stream at the tier in force when the cache is emptied, as a
        # generated hoard's do.
        magic_items=[
            instantiate_magic_item(
                item_id,
                tier=_treasure_tier(session),
                stream=session.streams.get(TREASURE_STREAM),
                allocator=session.allocator,
            )
            for item_id in feature.magic_item_ids
        ],
    )
    state.emptied_caches.append(ref)
    events.extend(_take_haul(session, carriers, haul))
    turn_events, _ = _spend_turn(session)
    events.extend(turn_events)
    return [], events


_DENOMINATIONS: tuple[str, ...] = tuple(COIN_VALUES_CP)
"""The coin denominations, most valuable first. Every coin weighs 1, so richest-first is
also best value per coin of weight: the order a party short of capacity packs them."""


_CLASS_USE_CODES = frozenset(
    {
        "items.equip.armour_forbidden",
        "items.equip.armour_not_allowed",
        "items.equip.shield_forbidden",
        "items.equip.weapon_forbidden",
        "items.equip.weapon_not_allowed",
        "items.equip.not_usable",
    }
)
"""The [`validate_equip`][osrlib.core.items.validate_equip] rejections that mean *this
class* may not use the item. `items.equip.not_equippable` is deliberately absent: plain
gear and ammunition are equippable by nobody, which is no reason to steer them."""


_SPREAD_MODES: dict[EncumbranceMode, EncumbranceMode] = {
    EncumbranceMode.NONE: EncumbranceMode.BASIC,
    EncumbranceMode.BASIC: EncumbranceMode.BASIC,
    EncumbranceMode.DETAILED: EncumbranceMode.DETAILED,
}
"""The weight that `_distribute_haul` compares loads by. Under `none` the session tracks no weight,
so the distribution compares treasure weight anyway (the loot still shouldn't all go to one
member) and never refuses a load."""


def _carry_load(member, ruleset) -> int:
    """Return the carried weight in coins that `_distribute_haul` compares carriers by."""
    return tracked_weight_coins(member.inventory, _SPREAD_MODES[ruleset.encumbrance])


def _within_max_load(member, ruleset) -> bool:
    """Return whether the member is still inside their maximum load.

    The maximum load is 1,600 coins, raised under detailed encumbrance by worn gauntlets
    of ogre power. Always true under the `none` encumbrance mode, which tracks no weight
    and imposes no cap.
    """
    if ruleset.encumbrance is EncumbranceMode.NONE:
        return True
    mode = ruleset.encumbrance
    return tracked_weight_coins(member.inventory, mode) <= _max_load_coins(member.inventory, mode)


def _uncarry(collection: list, instance) -> None:
    """Remove exactly the object that was handed over, by identity rather than by equality.

    Two like `ItemInstance`s (same template, same quantity) compare equal under
    pydantic, so `list.remove` could take a torch the member already owned and leave
    the newly offered one behind, aliased into two inventories at once.

    Args:
        collection: The inventory list the item was appended to.
        instance: The exact object to take back out.
    """
    for index, entry in enumerate(collection):
        if entry is instance:
            del collection[index]
            return


def _can_use(member, instance) -> bool:
    """Return whether the member's class may use this item, the loot-sorting preference.

    Only class policy counts, so the equipped-state checks are skipped (no inventory
    is passed): the question is whether the plate mail belongs with the fighter, not
    whether their hands are full this instant. A magic item must also pass its
    `usable_by` check, which is what steers an arcane scroll to the magic-user.

    Args:
        member (osrlib.core.character.Character): The candidate carrier.
        instance: The [`ItemInstance`][osrlib.core.items.ItemInstance] or
            [`MagicItemInstance`][osrlib.core.items.MagicItemInstance] being sorted.

    Returns:
        True when the class's policies allow the item.
    """
    if any(rejection.code in _CLASS_USE_CODES for rejection in validate_equip(member.definition, instance, None)):
        return False
    if isinstance(instance, MagicItemInstance):
        return usable_by_class(magic_item_template(instance), member.definition)
    return True


def _hand_item(carriers, ruleset, instance, put, take_back):
    """Hand one item to a carrier who can use it, preferring the least loaded.

    The two best-effort rules in order: a carrier whose class may use the item wins
    over one who may not, and among equals the lightest-laden takes it, so the loot
    goes where it is useful without immobilising anyone. Ties break on marching
    order, so the choice is deterministic and spends no RNG draw. `put` and
    `take_back` place the item and undo that placement, which measures the marginal
    weight against the real inventory instead of re-deriving it, and keeps the
    encumbrance rules in one place.

    Args:
        carriers: The candidate carriers, in marching order.
        ruleset: The ruleset whose encumbrance mode governs.
        instance: The item being handed out, for the usability preference.
        put: Callable placing the item on a member.
        take_back: Callable undoing `put` on a member.

    Returns:
        The index of the carrier who took it, or `None` when every carrier is
        already at maximum load.
    """
    order = sorted(
        range(len(carriers)),
        key=lambda index: (not _can_use(carriers[index], instance), _carry_load(carriers[index], ruleset), index),
    )
    for index in order:
        member = carriers[index]
        put(member)
        if _within_max_load(member, ruleset):
            return index
        take_back(member)
    return None


def _hand_valuable(carriers, ruleset, valuable):
    """Hand one gem or piece of jewellery to the carrier with the least treasure by worth.

    Gems and jewellery divide by *value*, not by count: three 50 gp gems against one
    300 gp ring is even in objects and lopsided in worth, and worth is what a share of
    the treasure means. Value is cheap to move (a 1,000 gp gem weighs one coin), so
    balancing it costs the party nothing in mobility. Ties break on marching order.

    Args:
        carriers: The candidate carriers, in marching order.
        ruleset: The ruleset whose encumbrance mode governs.
        valuable: The [`ValuableInstance`][osrlib.core.items.ValuableInstance] to place.

    Returns:
        The index of the carrier who took it, or `None` when every carrier is
        already at maximum load.
    """

    def worth(member) -> int:
        return sum(held.value_gp for held in member.inventory.valuables)

    order = sorted(range(len(carriers)), key=lambda index: (worth(carriers[index]), index))
    for index in order:
        member = carriers[index]
        member.inventory.valuables.append(valuable)
        if _within_max_load(member, ruleset):
            return index
        _uncarry(member.inventory.valuables, valuable)
    return None


def _even_shares(headrooms: list[int], total: int) -> list[int]:
    """Split `total` coins as evenly across the carriers as their headroom allows.

    Every coin weighs one, whatever its metal. An even split of a denomination is
    therefore even in worth *and* in weight, so the fair split also keeps the loads
    even. A carrier who would pass their maximum load takes only what
    fits and their surplus re-splits among the rest. The sub-coin remainder goes to
    the front of the march.

    Args:
        headrooms: Each carrier's remaining capacity in coins, in marching order.
        total: The number of coins of one denomination to split.

    Returns:
        Each carrier's share, in the same order. The shares sum to less than `total`
        only when the party has run out of capacity.
    """
    shares = [0] * len(headrooms)
    remaining = total
    while remaining > 0:
        open_indices = [index for index in range(len(headrooms)) if headrooms[index] - shares[index] > 0]
        if not open_indices:
            break
        base, extra = divmod(remaining, len(open_indices))
        if base == 0:  # fewer coins left than carriers with room
            for index in open_indices[:remaining]:
                shares[index] += 1
            break
        remaining = 0
        for position, index in enumerate(open_indices):
            wanted = base + (1 if position < extra else 0)
            given = min(wanted, headrooms[index] - shares[index])
            shares[index] += given
            remaining += wanted - given
    return shares


def _spread_coins(carriers, ruleset, coins: Coins) -> tuple[list[dict[str, int]], Coins]:
    """Deal a haul's coins evenly across the carriers, richest denomination first.

    Each denomination splits separately, so every carrier's share is equal in worth
    and in weight. The richest-first order makes a difference only when the party runs
    short of capacity: the platinum goes in the packs and the copper stays behind.

    Args:
        carriers: The carriers, in marching order.
        ruleset: The ruleset whose encumbrance mode governs.
        coins: The pot to deal.

    Returns:
        Each carrier's take by denomination (in carrier order) and the coins the
        party could not carry.
    """
    takes = [dict.fromkeys(_DENOMINATIONS, 0) for _ in carriers]
    left = dict.fromkeys(_DENOMINATIONS, 0)
    for denomination in _DENOMINATIONS:
        total = getattr(coins, denomination)
        if not total:
            continue
        headrooms = [_treasure_room_coins(member.inventory, ruleset.encumbrance, total) for member in carriers]
        shares = _even_shares(headrooms, total)
        for index, share in enumerate(shares):
            if not share:
                continue
            purse = carriers[index].inventory.purse
            setattr(purse, denomination, getattr(purse, denomination) + share)
            takes[index][denomination] += share
        left[denomination] = total - sum(shares)
    return takes, Coins(**left)


def _distribute_haul(session, carriers, haul: DropPile) -> tuple[list[Event], DropPile, int, list]:
    """Pack a haul into the carriers' packs: items where they are useful, wealth evenly.

    The order is items, then valuables, then coins: items, which only some
    classes can use, go first while every carrier still has room for them, then the
    valuables and coins, which can be split to even out the loads. Items (magic items first, then gear) go to a
    carrier whose class can use them, breaking ties toward the lightest load. Gems and
    jewellery divide by worth, and coins divide evenly per denomination. No carrier is
    loaded past their maximum load, so a take can never immobilise a carrier
    that some other arrangement would have kept moving.

    Args:
        session (osrlib.crawl.session.GameSession): The running session.
        carriers: The carriers, in marching order.
        haul: The goods to pack. Consumed, not mutated in place.

    Returns:
        The per-carrier [`ItemAcquiredEvent`][osrlib.crawl.events.ItemAcquiredEvent]s
        in marching order, the remainder nobody could carry, the coin value taken in
        copper pieces, and the valuables taken (the XP award's exact inputs).
    """
    ruleset = session.ruleset
    equipment = session.effective_equipment
    taken_ids: list[list[str]] = [[] for _ in carriers]
    taken_valuables: list = []
    leftovers = DropPile()

    for magic_item in haul.magic_items:
        holder = _hand_item(
            carriers,
            ruleset,
            magic_item,
            lambda member, item=magic_item: member.inventory.items.append(item),
            lambda member, item=magic_item: _uncarry(member.inventory.items, item),
        )
        if holder is None:
            leftovers.magic_items.append(magic_item)
            continue
        taken_ids[holder].append(magic_item.instance_id)
    for dropped in haul.items:
        instance = ItemInstance(template=equipment.get(dropped.item_id), quantity=dropped.quantity)
        holder = _hand_item(
            carriers,
            ruleset,
            instance,
            lambda member, item=instance: member.inventory.items.append(item),
            lambda member, item=instance: _uncarry(member.inventory.items, item),
        )
        if holder is None:
            leftovers.items.append(dropped)
            continue
        taken_ids[holder].extend([dropped.item_id] * dropped.quantity)
    # Richest piece first: the greedy split of a set of values is at its evenest when
    # the big lumps are placed while there is still room to balance them.
    for valuable in sorted(haul.valuables, key=lambda piece: -piece.value_gp):
        holder = _hand_valuable(carriers, ruleset, valuable)
        if holder is None:
            leftovers.valuables.append(valuable)
            continue
        taken_ids[holder].append(valuable.instance_id)
        taken_valuables.append(valuable)
    takes, leftovers.coins = _spread_coins(carriers, ruleset, haul.coins)

    events: list[Event] = []
    taken_cp = 0
    for index, member in enumerate(carriers):
        taken_coins = Coins(**takes[index])
        if not taken_ids[index] and not taken_coins.total_coins:
            continue
        taken_cp += taken_coins.value_cp
        events.append(
            ItemAcquiredEvent(
                character_id=member.id,
                item_ids=tuple(taken_ids[index]),
                coins_gp_value=taken_coins.value_gp,
                origin="treasure",
            )
        )
    return events, leftovers, taken_cp, taken_valuables


def _add_to_pile(session, goods: DropPile) -> tuple[str, ...]:
    """Put goods in the drop pile on the party's cell, and return their ids the way events list them.

    A stack of an item the pile already has adds to that stack. The ids are an instance id for each
    magic item, then an instance id for each valuable, then a catalog id for each unit of mundane
    gear, which is the form of `item_ids` on
    [`ItemsLeftBehindEvent`][osrlib.crawl.events.ItemsLeftBehindEvent] and
    [`LootDroppedEvent`][osrlib.crawl.events.LootDroppedEvent].
    """
    item_ids = [magic_item.instance_id for magic_item in goods.magic_items]
    item_ids += [valuable.instance_id for valuable in goods.valuables]
    for dropped in goods.items:
        item_ids.extend([dropped.item_id] * dropped.quantity)
    pile = session.dungeon_state.piles.setdefault(_cell_ref(session), DropPile())
    for dropped in goods.items:
        existing = next((entry for entry in pile.items if entry.item_id == dropped.item_id), None)
        if existing is None:
            pile.items.append(dropped)
        else:
            existing.quantity += dropped.quantity
    pile.valuables.extend(goods.valuables)
    pile.magic_items.extend(goods.magic_items)
    pile.coins = Coins(
        **{
            denomination: getattr(pile.coins, denomination) + getattr(goods.coins, denomination)
            for denomination in _DENOMINATIONS
        }
    )
    return tuple(item_ids)


def _leave_behind(session, leftovers: DropPile) -> list[Event]:
    """Put what the party could not carry in the drop pile at its feet.

    Treasure is never destroyed: a party that drops gear (or comes back later) picks
    the remainder up with another `TakeTreasure`.
    """
    if not (leftovers.items or leftovers.valuables or leftovers.magic_items or leftovers.coins.total_coins):
        return []
    item_ids = _add_to_pile(session, leftovers)
    return [ItemsLeftBehindEvent(item_ids=item_ids, coins_gp_value=leftovers.coins.value_gp)]


def _take_haul(session, carriers, haul: DropPile) -> list[Event]:
    """Distribute one haul, park the remainder, and award the immediate-timing XP."""
    events, leftovers, taken_cp, taken_valuables = _distribute_haul(session, carriers, haul)
    events.extend(_leave_behind(session, leftovers))
    events.extend(_immediate_treasure_award(session, taken_cp, taken_valuables))
    return events


def _immediate_treasure_award(session, coins_cp: int, valuables) -> list:
    """The `immediate` timing's treasure XP: the acquisition's value as the delta."""
    from osrlib.core.ruleset import XpAwardTiming

    if session.ruleset.xp_award_timing is not XpAwardTiming.IMMEDIATE:
        return []
    total_cp = coins_cp + sum(valuable.value_gp * 100 for valuable in valuables)
    return session.award_immediate_xp(total_cp // 100)
