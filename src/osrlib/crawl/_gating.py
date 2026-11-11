"""Applying an authored gate in a command: the check, the toll the party pays, and the text for a success.

A door, a stair, a trapdoor, a chute, or a passage can have a gate. The check is the last validation step of the
command, so a refusal costs no draw, no time, and no item.
"""

from osrlib.core.events import Event
from osrlib.crawl import gates
from osrlib.crawl._party_members import _consume_item, _consumed_item_id
from osrlib.crawl.events import ItemConsumedEvent


def _gate_refusal(session, gate) -> str | None:
    """The gate's refusal beat when its condition fails right now, else `None`.

    A gate check is the last validation step of the command that checks it: every
    other check has already passed, so a refusal from here means the gate alone
    barred the way, and the authored line is the only refusal the player sees. The
    check draws no dice, costs no time, and mutates nothing.

    Args:
        session (osrlib.crawl.session.GameSession): The running session.
        gate (osrlib.crawl.gates.GateSpec | None): The authored gate, or `None`
            for an ungated door or transition.

    Returns:
        `None` when the way is open (an absent gate included), else the gate's
        authored refusal text, which is the empty string when the author wrote none.
    """
    if gate is None:
        return None
    if gates.condition_holds(gate.condition, members=session.party.members, flags=session.flags, ledger=session.ledger):
        return None
    return gate.narrative.refusal if gate.narrative is not None else ""


def _pay_toll(session, gate) -> list[Event]:
    """Take a consuming gate's toll: one instance from the first holder in marching order.

    Consumption is an effect of the successful command, never of evaluation, and
    it happens per success: a toll gate charges again every time it is passed.
    """
    condition = None if gate is None else gate.condition
    if not isinstance(condition, gates.HasItemCondition) or not condition.consumes:
        return []
    holder = gates.first_holder(session.party.members, condition.item_id)
    instance = _consume_item(holder, condition.item_id) if holder is not None else None
    if holder is None or holder.id is None or instance is None:
        # Unreachable: the gate passed on this very matching rule a moment ago.
        raise ValueError(f"the toll {condition.item_id!r} went missing between the check and the taking")
    return [ItemConsumedEvent(character_id=holder.id, item_id=_consumed_item_id(instance))]


def _gate_success(gate) -> str | None:
    """The gate's authored success text, when its author wrote one.

    The text goes out on the successful command's event, either the door event or
    the arrival's boundary event, so the text appears with the event it describes.
    """
    if gate is None or gate.narrative is None or not gate.narrative.success:
        return None
    return gate.narrative.success
