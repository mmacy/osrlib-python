"""The town commands: buying equipment, selling treasure, and buying healing at the temple.

The temple's services and standard prices are `HEALING_SERVICES` in `osrlib.crawl.exploration`.
"""

from typing import Any

from osrlib.core.character import Character
from osrlib.core.combat import _healing_blocked
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
from osrlib.crawl._spells import _cast_context, _cast_outcomes, _has_curse_to_lift
from osrlib.crawl.commands import HealingService, PurchaseEquipment, PurchaseHealing, SellTreasure
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


def _purse_payable_gp(member: Character) -> int:
    """The whole gold pieces a member's purse can pay toward a temple fee: its total value, rounded down."""
    return member.inventory.purse.value_cp // 100


def _temple_funds_gp(session) -> int:
    """The whole gold pieces the party's purses can pay toward a temple fee now.

    It's the sum of `_purse_payable_gp` over every member, dead members included. The order in which
    `_healing_payers` charges the purses doesn't change the sum, so it's the same whoever is treated. The temple
    sells a service exactly when its price is at most this sum, which is the funds check
    `_handle_purchase_healing` makes and the number `PlayerView.town_healing_funds_gp` shows.
    """
    return sum(_purse_payable_gp(member) for member in session.party.members)


def _healing_payers(session, patient: Character, cost_gp: int) -> list[tuple[Character, int]]:
    """Plan which purses cover a temple fee, in the order the temple charges them.

    The treated member pays first, then the rest of the party in marching order, dead
    members included. A purse pays in whole gold pieces, because
    [`CoinPurse.spend`][osrlib.core.items.CoinPurse.spend] prices in gold. It pays as much of
    what is still owed as `_purse_payable_gp` allows, and keeps any value below a gold piece,
    so the last purse charged pays the outstanding remainder and the purses after it pay
    nothing.

    Call it only once `_temple_funds_gp` covers the fee. Planning and charging are separate
    so the funds check stays a pure validation step: a party whose whole gold pieces fall
    short is refused before any purse is opened.

    Args:
        session: The session whose party pays.
        patient: The member being treated, charged first whether alive or dead.
        cost_gp: The fee in whole gold pieces, at most `_temple_funds_gp(session)`.

    Returns:
        The (member, gold pieces) pairs to charge in order.
    """
    order = [patient] + [member for member in session.party.members if member.id != patient.id]
    plan: list[tuple[Character, int]] = []
    outstanding = cost_gp
    for member in order:
        if outstanding <= 0:
            break
        share = min(outstanding, _purse_payable_gp(member))
        if share <= 0:
            continue
        plan.append((member, share))
        outstanding -= share
    return plan


def _handle_purchase_healing(session, command: PurchaseHealing) -> tuple[list[Rejection], list[Event]]:
    try:
        member = session.member(command.character_id)
    except ValueError:
        return [Rejection(code="session.command.unknown_member", params={"character": command.character_id})], []
    spell = _temple_spell(session, command.service)
    lowest, highest = _temple_levels(spell)
    caster_level = lowest if command.caster_level is None else command.caster_level
    if not lowest <= caster_level <= highest:
        return [
            Rejection(
                code="town.healing.caster_level_out_of_range",
                params={"item": command.service, "caster_level": caster_level, "minimum": lowest, "maximum": highest},
            )
        ], []
    cost_gp = _healing_price(session, command.service)
    if _temple_funds_gp(session) < cost_gp:
        return [
            Rejection(code="items.purchase.insufficient_funds", params={"item": command.service, "cost_gp": cost_gp})
        ], []
    context = _cast_context(session, [member], in_combat=False)
    rejections = _healing_rejections(session, member, command.service, spell, caster_level, context)
    if rejections:
        return rejections, []
    temple_cleric = _temple_cleric(spell, caster_level)
    temple_profile = caster_profile(temple_cleric.definition)
    if temple_profile is None:  # unreachable: the temple cleric is a cleric
        raise ValueError("the temple cleric has no casting profile")
    payers = _healing_payers(session, member, cost_gp)
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
    events.extend(_cast_outcomes(session, spell, result, [member]))
    return [], events


def _temple_spell(session, service: HealingService) -> SpellTemplate:
    """The spell the temple casts for a service, from the session's catalog."""
    from osrlib.crawl.exploration import HEALING_SERVICES

    return session.effective_spells.get(HEALING_SERVICES[service][0])


_TEMPLE_LEVELS: dict[tuple[str, int], tuple[int, int]] = {}
"""The temple's cleric levels for a spell, keyed by the spell's list and level, filled by `_temple_levels`."""


def _temple_levels(spell: SpellTemplate) -> tuple[int, int]:
    """Return the lowest and the highest temple cleric level for a service's spell.

    The lowest is [`minimum_caster_level`][osrlib.core.spells.minimum_caster_level], which depends only on the
    spell's list and level and on the built-in classes, and the highest is the built-in cleric's maximum level.
    Neither can change while the process runs, so the answer is computed once for each list and level and kept in
    `_TEMPLE_LEVELS`, for every session.
    """
    key = (str(spell.spell_list), spell.level)
    levels = _TEMPLE_LEVELS.get(key)
    if levels is None:
        levels = _TEMPLE_LEVELS[key] = (minimum_caster_level(spell), load_classes().get("cleric").max_level)
    return levels


def _temple_offer(
    session, member, service: HealingService, spell: SpellTemplate, context: CastContext
) -> tuple[int | None, list[Rejection]]:
    """Return the lowest temple cleric level that would sell a service to a member, and the rejections otherwise.

    `PlayerView.town_healing_rejections` and `PlayerView.town_healing_caster_levels` come from this. It asks
    `_healing_rejections`, the check `_handle_purchase_healing` makes after the fee, at as few levels as it can.
    Whether the temple sells a service is monotonic in the cleric's level: only *raise dead*'s reach depends on the
    level, and it grows with each level, while the temple cleric's cast check is the same at every level for these
    single-target spells. So it tries the lowest level first. A refusal there whose `reason` is
    `caster_level_too_low` means that `_no_effect_reason` found the highest level sells the service, and only then
    does it search between the two by halving. Every other refusal is the same at every level.

    Args:
        session: The session whose party member is treated.
        member: The member to treat, alive or dead.
        service: The service to check.
        spell: The service's spell, from `_temple_spell`.
        context: The member's cast context, from `_cast_context`.

    Returns:
        The lowest level that sells the service and no rejections, or `None` and the rejections at the lowest level
        when no level from the lowest to the highest sells it.
    """
    lowest, highest = _temple_levels(spell)
    rejections = _healing_rejections(session, member, service, spell, lowest, context)
    if not rejections:
        return lowest, []
    if [rejection.params.get("reason") for rejection in rejections] != ["caster_level_too_low"]:
        return None, rejections
    refused, sold = lowest, highest
    while sold - refused > 1:
        middle = (refused + sold) // 2
        if _healing_rejections(session, member, service, spell, middle, context):
            refused = middle
        else:
            sold = middle
    return sold, []


def _healing_price(session, service: HealingService) -> int:
    """What the session's town charges for a service, in gold pieces: the town's own price, or the standard one."""
    from osrlib.crawl.exploration import HEALING_SERVICES

    return session.adventure.town.healing_prices.get(service, HEALING_SERVICES[service][1])


def _healing_rejections(
    session,
    member,
    service: HealingService,
    spell: SpellTemplate,
    caster_level: int,
    context: CastContext,
) -> list[Rejection]:
    """Return why the temple would refuse a service for a member, apart from the caster level and the fee.

    `_handle_purchase_healing` calls this after its caster level and funds checks, and `_temple_offer` calls it to
    build `PlayerView.town_healing_rejections` and `PlayerView.town_healing_caster_levels`, so the view and the
    command can't disagree. It returns `town.healing.no_effect`, with the `reason` from `_no_effect_reason`, when
    the service would do nothing for the member, and otherwise the rejections of the temple cleric's cast from
    [`validate_cast`][osrlib.core.spells.validate_cast], which is empty when the temple would sell the service. The
    check only reads the session, so it makes no draw, takes no time, and changes nothing.

    Args:
        session: The session whose party member is treated.
        member: The member to treat, alive or dead.
        service: The service to check.
        spell: The service's spell, from `_temple_spell`.
        caster_level: The temple cleric's level, which the caller has checked.
        context: The member's cast context, from `_cast_context`, which carries the time since the member died.

    Returns:
        The rejections, empty when the temple would sell the service to a party that can pay for it.
    """
    reason = _no_effect_reason(session, service, member, spell, caster_level, context)
    if reason is not None:
        return [
            Rejection(code="town.healing.no_effect", params={"item": service, "character": member.id, "reason": reason})
        ]
    temple_cleric = _temple_cleric(spell, caster_level)
    return validate_cast(
        temple_cleric,
        spell,
        spell.modes[0].key,
        profile=caster_profile(temple_cleric.definition),
        targets=[member],
        context=context,
        ledger=session.ledger,
    )


def _no_effect_reason(
    session, service: str, member: Character, spell: SpellTemplate, caster_level: int, context: CastContext
) -> str | None:
    """Return why a temple service cast at `caster_level` would do nothing for the member, or None when it would work.

    The reason is the `reason` param of `town.healing.no_effect`. Where more than one applies, the first in this
    order for the service wins:

    - *Cure light wounds* and *cure serious wounds*: `dead`, then `weakened`, then `diseased`, because neither
      condition lets a member heal by magic, then `full_hit_points`.
    - *Cure disease*: `not_diseased`.
    - *Neutralize poison*: `not_poisoned`, for a member who isn't poisoned and isn't dead of poison within the
      spell's revival window.
    - *Remove curse*: `not_cursed`, for a member with no carried item whose curse has been revealed and no item
      curse's effect.
    - *Raise dead*: `not_dead` for a living member, then `no_death_record` for a dead member the session has no
      record of the death for, then `caster_level_too_low` when a higher temple cleric could raise the member, then
      `dead_too_long` when no temple cleric could.

    Each check uses the function that the spell or the session resolves with, so the temple and a cast agree about
    what the service changes. `_cast_context` works out the time since death from the session's death records. The
    check only reads state, so a refusal makes no random roll and changes nothing.
    """
    effect = spell.modes[0].effect
    if service in ("cure_light_wounds", "cure_serious_wounds"):
        if has_condition(member, Condition.DEAD):
            return "dead"
        if _healing_blocked(member, "magical"):
            return "weakened" if has_condition(member, Condition.WEAKENED) else "diseased"
        return "full_hit_points" if member.current_hp >= member.max_hp else None
    if service == "cure_disease":
        return None if has_condition(member, Condition.DISEASED) else "not_diseased"
    if service == "neutralize_poison":
        revives = effect is not None and _revives_poison_dead(effect, member, context)
        return None if has_condition(member, Condition.POISONED) or revives else "not_poisoned"
    if service == "remove_curse":
        return None if _has_curse_to_lift(session, member) else "not_cursed"
    if service == "raise_dead":
        if not has_condition(member, Condition.DEAD):
            return "not_dead"
        if context.days_since_death is None:
            return "no_death_record"
        if effect is not None and _restores_life(effect, caster_level, member, context):
            return None
        highest = _temple_levels(spell)[1]
        if effect is not None and _restores_life(effect, highest, member, context):
            return "caster_level_too_low"
        return "dead_too_long"
    return None


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
