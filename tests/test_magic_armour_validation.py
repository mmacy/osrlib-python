"""A generic suit of magic armour needs a mundane base before it enters play (issue 295).

Construction, JSON, copies, and assignment enforce the same rule. An armour template can supply the base, and
non-armour items need none. Refused assignments leave the item unchanged. Treasure generation keeps its existing
armour rolls and instance ids.
"""

import json

import pytest
from pydantic import ValidationError

from osrlib.core.items import MagicItemCategory, MagicItemInstance
from osrlib.core.monsters import IdAllocator
from osrlib.core.rng import RngStream
from osrlib.core.treasure import TREASURE_STREAM, instantiate_magic_item
from osrlib.data import load_magic_items

GENERIC_ARMOUR = (
    "armour_plus_1",
    "armour_plus_2",
    "armour_plus_3",
    "cursed_armour_minus_1",
    "cursed_armour_minus_2",
    "cursed_armour_ac_9",
)

INVALID_UPDATES = (
    pytest.param("armour_plus_1", "chainmail", "base_item_id", None, id="remove_base"),
    pytest.param("ring_of_protection", None, "template_id", "cursed_armour_minus_1", id="change_template"),
)


@pytest.mark.parametrize("template_id", GENERIC_ARMOUR)
def test_a_hand_built_generic_suit_requires_a_base(template_id):
    with pytest.raises(ValidationError):
        MagicItemInstance(instance_id="magic-item-0001", template_id=template_id)


@pytest.mark.parametrize("template_id", ("armour_plus_1", "cursed_armour_minus_1"))
def test_json_cannot_load_a_generic_suit_without_a_base(template_id):
    document = json.dumps({"instance_id": "magic-item-0001", "template_id": template_id, "base_item_id": None})
    with pytest.raises(ValidationError):
        MagicItemInstance.model_validate_json(document)


@pytest.mark.parametrize(("template_id", "base", "field", "value"), INVALID_UPDATES)
def test_a_copy_cannot_remove_a_suits_base(template_id, base, field, value):
    item = MagicItemInstance(instance_id="magic-item-0001", template_id=template_id, base_item_id=base)
    before = item.model_dump()
    fields_set = item.model_fields_set.copy()

    with pytest.raises(ValidationError):
        item.model_copy(update={field: value})

    assert item.model_dump() == before
    assert item.model_fields_set == fields_set


@pytest.mark.parametrize(("template_id", "base", "field", "value"), INVALID_UPDATES)
def test_a_refused_assignment_keeps_the_valid_item(template_id, base, field, value):
    item = MagicItemInstance(instance_id="magic-item-0001", template_id=template_id, base_item_id=base)
    before = item.model_dump()
    fields_set = item.model_fields_set.copy()

    with pytest.raises(ValidationError):
        setattr(item, field, value)

    assert item.model_dump() == before
    assert item.model_fields_set == fields_set


@pytest.mark.parametrize("template_id", GENERIC_ARMOUR)
def test_an_explicit_base_survives_a_json_round_trip(template_id):
    item = MagicItemInstance(instance_id="magic-item-0001", template_id=template_id, base_item_id="chainmail")

    assert MagicItemInstance.model_validate_json(item.model_dump_json()) == item
    assert item.base_item_id == "chainmail"


@pytest.mark.parametrize("template_id", ("shield_plus_1", "cursed_shield_minus_2", "cursed_shield_ac_9"))
def test_a_shield_uses_its_template_base(template_id):
    item = MagicItemInstance(instance_id="magic-item-0001", template_id=template_id)

    assert item.base_item_id is None
    assert load_magic_items().get(template_id).base_item_id == "shield"
    assert MagicItemInstance.model_validate_json(item.model_dump_json()) == item


@pytest.mark.parametrize("template_id", ("ring_of_protection", "potion_of_healing"))
def test_an_item_outside_armour_needs_no_base(template_id):
    item = MagicItemInstance(instance_id="magic-item-0001", template_id=template_id)

    assert item.base_item_id is None
    assert MagicItemInstance.model_validate_json(item.model_dump_json()) == item


def test_a_body_armour_template_can_supply_the_base(monkeypatch):
    catalog = load_magic_items()
    template = catalog.get("armour_plus_1").model_copy(update={"id": "plate_of_warding", "base_item_id": "plate_mail"})
    extended = catalog.model_copy(update={"items": (*catalog.items, template)})
    monkeypatch.setattr("osrlib.data.load_magic_items", lambda: extended)

    item = MagicItemInstance(instance_id="magic-item-0001", template_id="plate_of_warding")

    assert item.base_item_id is None
    assert MagicItemInstance.model_validate_json(item.model_dump_json()) == item


def test_a_copy_can_change_the_template_and_supply_its_base_together():
    item = MagicItemInstance(instance_id="magic-item-0001", template_id="ring_of_protection")

    copied = item.model_copy(update={"template_id": "armour_plus_1", "base_item_id": "leather"})

    assert (copied.template_id, copied.base_item_id) == ("armour_plus_1", "leather")
    assert (item.template_id, item.base_item_id) == ("ring_of_protection", None)


def test_valid_assignments_still_apply():
    item = MagicItemInstance(instance_id="magic-item-0001", template_id="armour_plus_1", base_item_id="chainmail")

    item.base_item_id = "leather"
    item.template_id = "cursed_armour_minus_1"
    item.identified = True

    assert (item.template_id, item.base_item_id, item.identified) == ("cursed_armour_minus_1", "leather", True)


def test_generation_keeps_armour_rolls_and_instance_ids():
    stream = RngStream.from_seed_material(29, TREASURE_STREAM)
    reference = RngStream.from_seed_material(29, TREASURE_STREAM)
    allocator = IdAllocator()
    catalog = load_magic_items()
    item_ids = ("armour_plus_1", "shield_plus_1", "cursed_armour_minus_1", "potion_of_healing", "cursed_armour_ac_9")

    for index, item_id in enumerate(item_ids, start=1):
        template = catalog.get(item_id)
        expected_base = template.base_item_id
        if template.category is MagicItemCategory.ARMOUR and expected_base is None:
            expected_base = catalog.armour_type.base_for_roll(reference.randbelow(8) + 1)

        item = instantiate_magic_item(item_id, tier="expert", stream=stream, allocator=allocator)

        assert item.instance_id == f"magic-item-{index:04d}"
        assert item.template_id == item_id
        assert item.base_item_id == expected_base
        assert not item.identified
        assert stream.export_state() == reference.export_state()
        assert allocator.counters == {"magic-item": index}


def test_an_allocation_failure_consumes_no_armour_roll(monkeypatch):
    stream = RngStream.from_seed_material(29, TREASURE_STREAM)
    before = stream.export_state()
    allocator = IdAllocator()

    def refuse_allocation(self, prefix):
        assert prefix == "magic-item"
        raise RuntimeError("allocation failed")

    monkeypatch.setattr(IdAllocator, "allocate", refuse_allocation)

    with pytest.raises(RuntimeError, match="allocation failed"):
        instantiate_magic_item("armour_plus_1", tier="expert", stream=stream, allocator=allocator)

    assert stream.export_state() == before
    assert allocator.counters == {}
