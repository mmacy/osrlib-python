"""Taking an item out of an inventory takes the object passed, not another one equal to it (issue #257).

Two stacks of the same mundane item with the same quantity compare equal, and so do two magic item instances with
the same fields. `list.remove` takes the first equal entry, which can leave the passed object in the list while it
is also placed somewhere else.
"""

import pytest

from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.items import ItemInstance, MagicItemInstance
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl._party_members import _remove_instance
from osrlib.data import load_equipment


def hero():
    stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
    return create_character(
        name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=Ruleset(), stream=stream
    ).character


def twins(kind: str):
    if kind == "mundane":
        torch = load_equipment().get("torch")
        return ItemInstance(template=torch, quantity=2), ItemInstance(template=torch, quantity=2)
    return (
        MagicItemInstance(instance_id="magic-item-0001", template_id="potion_of_healing"),
        MagicItemInstance(instance_id="magic-item-0001", template_id="potion_of_healing"),
    )


@pytest.mark.parametrize("kind", ["mundane", "magic"])
@pytest.mark.parametrize("where", ["items", "wielded"])
def test_the_passed_object_is_the_one_removed(kind, where):
    member = hero()
    first, second = twins(kind)
    assert first == second and first is not second
    getattr(member.inventory, where)[:] = [first, second]

    _remove_instance(member, second)

    remaining = getattr(member.inventory, where)
    assert len(remaining) == 1 and remaining[0] is first
