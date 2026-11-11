"""The town commands: buying equipment, selling treasure, and buying healing at the temple.

The temple's services and standard prices are `HEALING_SERVICES` in `osrlib.crawl.exploration`.
"""

from typing import Any

from osrlib.core.character import Character
from osrlib.core.effects import EFFECTS_STREAM, Condition, has_condition
from osrlib.core.events import Event
from osrlib.core.spells import (
    MAGIC_STREAM,
    CastContext,
    SpellTemplate,
    _restores_life,
    _revives_poison_dead,
    cast_spell,
    caster_profile,
    minimum_caster_level,
    validate_cast,
)
from osrlib.core.validation import Rejection
from osrlib.crawl._party_members import _member_able
from osrlib.crawl._spells import _cast_context, _lift_curses
from osrlib.crawl.commands import PurchaseEquipment, PurchaseHealing, SellTreasure
from osrlib.crawl.events import HealingPurchasedEvent, ItemAcquiredEvent, TreasureSoldEvent
from osrlib.data import load_classes, load_equipment


def _handle_purchase_equipment(session, command: PurchaseEquipment) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    # The shop stocks the built-in equipment lists. An adventure's bundled items
    # enter play through caches and grants, never off a shelf.
    equipment = load_equipment()
    templates = []
    total_cost = 0
    for item_id in command.item_ids:
        try:
            template = equipment.get(item_id)
        except ValueError:
            try:
                session.effective_equipment.get(item_id)
            except ValueError:
                return [Rejection(code="session.command.unknown_item", params={"item": item_id})], []
            return [Rejection(code="items.purchase.not_stocked", params={"item": item_id})], []
        templates.append(template)
        total_cost += template.cost_gp
    if not member.inventory.purse.can_afford(total_cost):
        return [Rejection(code="items.purchase.insufficient_funds", params={"cost_gp": total_cost})], []
    from osrlib.core.items import purchase

    for template in templates:
        purchase(member.inventory, template, 1)
    return [], [ItemAcquiredEvent(character_id=member.id, item_ids=tuple(command.item_ids), origin="purchase")]


def _handle_sell_treasure(session, command: SellTreasure) -> tuple[list[Rejection], list[Event]]:
    sales: list[tuple[Any, Any]] = []  # (owner, valuable)
    for item_id in command.item_ids:
        found = None
        for member in session.party.members:
            if member.inventory.magic_item(item_id) is not None:
                return [Rejection(code="town.sell.no_fixed_value", params={"item": item_id})], []
            valuable = next(
                (candidate for candidate in member.inventory.valuables if candidate.instance_id == item_id), None
            )
            if valuable is not None:
                found = (member, valuable)
                break
        # A valuable's instance id identifies one gem or piece of jewellery, so an id listed
        # twice has nothing left to sell the second time.
        if found is None or any(sold is found[1] for _, sold in sales):
            return [Rejection(code="exploration.item.not_carried", params={"item": item_id})], []
        sales.append(found)
    events: list[Event] = []
    by_seller: dict[str, tuple[Any, list]] = {}
    for member, valuable in sales:
        by_seller.setdefault(member.id, (member, []))[1].append(valuable)
    for member, valuables in by_seller.values():
        total = 0
        for valuable in valuables:
            member.inventory.valuables.remove(valuable)
            total += valuable.value_gp
        member.inventory.purse.gp += total
        events.append(
            TreasureSoldEvent(
                character_id=member.id,
                instance_ids=tuple(valuable.instance_id for valuable in valuables),
                gp_value=total,
            )
        )
    return [], events


def _healing_payers(session, patient: Character, cost_gp: int) -> list[tuple[Character, int]] | None:
    """Plan which purses cover a temple fee, in the order the temple charges them.

    The treated member pays first, then the rest of the party in marching order, dead
    members included. A purse pays in whole gold pieces, because
    [`CoinPurse.spend`][osrlib.core.items.CoinPurse.spend] prices in gold. It pays as much of
    what is still owed as its total value covers, rounded down to a whole gold piece, and
    keeps any value below a gold piece, so the last purse charged pays the outstanding
    remainder and the purses after it pay nothing.

    Planning and charging are separate so the funds check stays a pure validation step: a
    party whose whole gold pieces fall short is refused before any purse is opened.

    Args:
        session: The session whose party pays.
        patient: The member being treated, charged first whether alive or dead.
        cost_gp: The fee in whole gold pieces.

    Returns:
        The (member, gold pieces) pairs to charge in order, or None when the whole gold
        pieces in the party's purses together fall short of the fee.
    """
    order = [patient] + [member for member in session.party.members if member.id != patient.id]
    plan: list[tuple[Character, int]] = []
    outstanding = cost_gp
    for member in order:
        if outstanding <= 0:
            break
        share = min(outstanding, member.inventory.purse.value_cp // 100)
        if share <= 0:
            continue
        plan.append((member, share))
        outstanding -= share
    return None if outstanding > 0 else plan


def _handle_purchase_healing(session, command: PurchaseHealing) -> tuple[list[Rejection], list[Event]]:
    from osrlib.crawl.exploration import HEALING_SERVICES

    try:
        member = session.member(command.character_id)
    except ValueError:
        return [Rejection(code="session.command.unknown_member", params={"character": command.character_id})], []
    spell_id, standard_gp = HEALING_SERVICES[command.service]
    spell = session.effective_spells.get(spell_id)
    lowest = minimum_caster_level(spell)
    highest = load_classes().get("cleric").max_level
    caster_level = lowest if command.caster_level is None else command.caster_level
    if not lowest <= caster_level <= highest:
        return [
            Rejection(
                code="town.healing.caster_level_out_of_range",
                params={"item": command.service, "caster_level": caster_level, "minimum": lowest, "maximum": highest},
            )
        ], []
    cost_gp = session.adventure.town.healing_prices.get(command.service, standard_gp)
    payers = _healing_payers(session, member, cost_gp)
    if payers is None:
        return [
            Rejection(code="items.purchase.insufficient_funds", params={"item": command.service, "cost_gp": cost_gp})
        ], []
    context = _cast_context(session, [member], in_combat=False)
    if not _healing_would_work(command.service, member, spell, caster_level, context):
        return [Rejection(code="town.healing.no_effect", params={"item": command.service, "character": member.id})], []
    temple_cleric = _temple_cleric(spell, caster_level)
    temple_profile = caster_profile(temple_cleric.definition)
    if temple_profile is None:  # unreachable: the temple cleric is a cleric
        raise ValueError("the temple cleric has no casting profile")
    cast_rejections = validate_cast(
        temple_cleric,
        spell,
        spell.modes[0].key,
        profile=temple_profile,
        targets=[member],
        context=context,
        ledger=session.ledger,
    )
    if cast_rejections:
        return cast_rejections, []
    for payer, share in payers:
        payer.inventory.purse.spend(share)
    events: list[Event] = [
        HealingPurchasedEvent(
            character_id=member.id,
            service=command.service,
            cost_gp=cost_gp,
            payers=tuple(payer.id or payer.name for payer, _ in payers),
            payments_gp=tuple(share for _, share in payers),
        )
    ]
    result = cast_spell(
        temple_cleric,
        spell,
        spell.modes[0].key,
        profile=temple_profile,
        targets=[member],
        context=context,
        ledger=session.ledger,
        clock=session.clock,
        allocator=session.allocator,
        registry=session.registry(),
        ruleset=session.ruleset,
        stream=session.streams.get(MAGIC_STREAM),
        effects_stream=session.streams.get(EFFECTS_STREAM),
    )
    events.extend(result.events)
    if command.service == "remove_curse":
        events.extend(_lift_curses(session, member))
    return [], events


def _healing_would_work(
    service: str, member: Character, spell: SpellTemplate, caster_level: int, context: CastContext
) -> bool:
    """Return whether a temple service cast at `caster_level` would do anything for the member.

    *Raise dead* needs a dead member, dead no longer than the spell reaches at the cleric's level.
    *Cure disease* needs a diseased member. *Neutralize poison* needs a poisoned member, or one
    that poison killed within the spell's revival window. The two revivals are judged by the same
    functions the spells resolve with, so the temple and a cast agree about who comes back.
    `_cast_context` works out the time since death from the session's death records. The other
    services always count as working. The check only reads state, so a refusal makes no random
    roll and changes nothing.
    """
    effect = spell.modes[0].effect
    if service == "raise_dead":
        return effect is not None and _restores_life(effect, caster_level, member, context)
    if service == "cure_disease":
        return has_condition(member, Condition.DISEASED)
    if service == "neutralize_poison":
        return has_condition(member, Condition.POISONED) or (
            effect is not None and _revives_poison_dead(effect, member, context)
        )
    return True


def _temple_cleric(spell: SpellTemplate, level: int) -> Character:
    """An abstract temple cleric of the given level, with the service's spell memorized.

    Built fresh per purchase, with no draws and no registry entry. The rolls come
    from the magic and effects streams the spell already uses.
    """
    from osrlib.core.abilities import AbilityScore
    from osrlib.core.alignment import Alignment
    from osrlib.core.spells import MemorizedSpell

    return Character(
        id="town-cleric",
        name="Temple cleric",
        class_id="cleric",
        race="human",
        level=level,
        xp=0,
        scores={ability: 9 for ability in AbilityScore},
        alignment=Alignment.LAWFUL,
        max_hp=1,
        current_hp=1,
        memorized_spells=(MemorizedSpell(spell_id=spell.id),),
    )
