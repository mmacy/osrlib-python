"""Adventure-bundled items: the field, validation, engine paths, persistence."""

import json

import pytest

from crawl_fixtures import build_adventure, build_party
from osrlib.core.items import AmmunitionTemplate, ArmourTemplate, GearTemplate, ItemTemplate, WeaponTemplate
from osrlib.core.ruleset import EncumbranceMode, Ruleset
from osrlib.crawl.adventure import Adventure, validate_adventure
from osrlib.crawl.commands import (
    DropItems,
    GiveItems,
    GrantItem,
    PlaceParty,
    PurchaseEquipment,
    TakeTreasure,
)
from osrlib.crawl.dungeon import Direction, PartyLocation
from osrlib.crawl.session import GameSession
from osrlib.data import load_equipment, load_magic_items, load_monsters
from osrlib.errors import ContentValidationError
from osrlib.persistence import load_game, replay_game, save_game, session_state

CUSTOM_ID = "temple_idol"
MAGIC_ID = "armour_plus_1"


def idol(item_id: str = CUSTOM_ID, **overrides) -> GearTemplate:
    """The authored object this phase exists for: a gear item only the adventure knows."""
    data = {"id": item_id, "name": "Temple idol", "cost_gp": 0}
    data.update(overrides)
    return GearTemplate.model_validate(data)


def maul(item_id: str = "iron_maul", **overrides) -> WeaponTemplate:
    """A bundled weapon heavier than the 1,600-coin maximum load — nobody can lift it."""
    data = {
        "id": item_id,
        "name": "Iron maul",
        "cost_gp": 0,
        "weight_coins": 2000,
        "damage": "1d6",
        "qualities": ("melee", "two_handed"),
    }
    data.update(overrides)
    return WeaponTemplate.model_validate(data)


def carapace(item_id: str = "stone_carapace", **overrides) -> ArmourTemplate:
    data = {
        "id": item_id,
        "name": "Stone carapace",
        "cost_gp": 0,
        "weight_coins": 500,
        "ac": 4,
        "ac_ascending": 15,
        "category": "heavy",
    }
    data.update(overrides)
    return ArmourTemplate.model_validate(data)


def shard(item_id: str = "obsidian_shard", **overrides) -> AmmunitionTemplate:
    data = {"id": item_id, "name": "Obsidian shard", "cost_gp": 0, "lot_size": 10}
    data.update(overrides)
    return AmmunitionTemplate.model_validate(data)


def bundled_adventure(
    *,
    items: tuple[ItemTemplate, ...] | None = None,
    cache_item_ids: tuple[str, ...] = ("holy_water",),
) -> Adventure:
    """The fixture delve with room_a's chest re-stocked (and disarmed) and the bundle swapped."""
    adventure = build_adventure(wandering_chance=0)
    dungeon = adventure.dungeons[0]
    level_1 = dungeon.levels[0]
    areas = []
    for area in level_1.areas:
        if area.id == "room_a":
            # The needle trap is the cache path's other subject; take it off so a
            # take reports only what the carriers packed.
            chest = area.features[0].model_copy(update={"item_ids": cache_item_ids, "trap": None})
            area = area.model_copy(update={"features": (chest,)})
        areas.append(area)
    level_1 = level_1.model_copy(update={"areas": tuple(areas)})
    dungeon = dungeon.model_copy(update={"levels": (level_1, dungeon.levels[1])})
    update: dict = {"dungeons": (dungeon,)}
    if items is not None:
        update["items"] = items
    return adventure.model_copy(update=update)


def at_the_chest(adventure: Adventure, seed: int = 5, *, ruleset: Ruleset | None = None) -> GameSession:
    """A session standing on the chest's cell, exploring — the take needs no light."""
    session = GameSession.new(build_party(), adventure, seed=seed, ruleset=ruleset)
    session.execute(
        PlaceParty(
            location=PartyLocation(
                kind="dungeon", dungeon_id="delve", level_number=1, position=(3, 2), facing=Direction.NORTH
            )
        )
    )
    return session


def acquired_ids(result) -> list[str]:
    """Every item id the result's acquisitions report, in event order."""
    return [item_id for event in result.events if event.event_type == "item_acquired" for item_id in event.item_ids]


class TestTheField:
    def test_the_default_is_the_empty_tuple(self):
        adventure = build_adventure()
        assert adventure.items == ()
        dumped = adventure.model_dump(mode="json")
        assert dumped["items"] == []
        assert Adventure.model_validate(dumped).items == ()

    def test_a_bundled_template_of_each_type_round_trips_the_document(self):
        bundle = (idol(), maul(), carapace(), shard())
        adventure = bundled_adventure(items=bundle)
        reloaded = Adventure.model_validate(json.loads(json.dumps(adventure.model_dump(mode="json"))))
        assert reloaded == adventure
        assert [type(template) for template in reloaded.items] == [
            GearTemplate,
            WeaponTemplate,
            ArmourTemplate,
            AmmunitionTemplate,
        ]

    def test_a_bundled_template_survives_save_and_load(self):
        session = GameSession.new(build_party(), bundled_adventure(items=(idol(),)), seed=5)
        restored = load_game(json.loads(json.dumps(save_game(session))))
        assert session_state(restored) == session_state(session)
        assert restored.adventure.items == (idol(),)

    def test_a_save_without_the_key_loads_with_the_default(self):
        session = GameSession.new(build_party(), build_adventure(), seed=5)
        document = json.loads(json.dumps(save_game(session)))
        del document["payload"]["adventure"]["items"]
        restored = load_game(document)
        assert restored.adventure.items == ()


class TestValidation:
    def test_duplicate_bundled_ids_fail_the_standard_gate(self):
        adventure = bundled_adventure(items=(idol(), idol()))
        with pytest.raises(ContentValidationError, match="adventure validation failed") as excinfo:
            validate_adventure(adventure, load_monsters(), load_equipment())
        assert f"bundled item id {CUSTOM_ID!r} collides with the catalog" in str(excinfo.value)

    def test_an_equipment_catalog_collision_names_every_colliding_id(self):
        adventure = bundled_adventure(items=(idol("torch"), maul("sword")))
        with pytest.raises(ContentValidationError) as excinfo:
            validate_adventure(adventure, load_monsters(), load_equipment())
        message = str(excinfo.value)
        assert "bundled item id 'torch' collides with the catalog" in message
        assert "bundled item id 'sword' collides with the catalog" in message

    def test_a_magic_catalog_collision_fails_the_same_gate(self):
        adventure = bundled_adventure(items=(idol(MAGIC_ID),))
        with pytest.raises(ContentValidationError) as excinfo:
            validate_adventure(adventure, load_monsters(), load_equipment())
        assert f"bundled item id {MAGIC_ID!r} collides with the catalog" in str(excinfo.value)

    def test_a_collision_reports_no_spurious_unknown_item_echo(self):
        # The chest names 'torch'; the colliding bundle must produce exactly the
        # collision line — cache references resolve the union's first occurrence
        # and stay quiet.
        adventure = bundled_adventure(items=(idol("torch"),), cache_item_ids=("torch",))
        with pytest.raises(ContentValidationError) as excinfo:
            validate_adventure(adventure, load_monsters(), load_equipment())
        message = str(excinfo.value)
        assert "bundled item id 'torch' collides with the catalog" in message
        assert "unknown item" not in message

    def test_a_cache_reference_to_a_bundled_id_validates(self):
        adventure = bundled_adventure(items=(idol(),), cache_item_ids=(CUSTOM_ID,))
        validate_adventure(adventure, load_monsters(), load_equipment())

    def test_a_dangling_bundled_looking_id_still_fails(self):
        adventure = bundled_adventure(cache_item_ids=(CUSTOM_ID,))
        with pytest.raises(ContentValidationError, match="unknown item"):
            validate_adventure(adventure, load_monsters(), load_equipment())

    def test_the_shipped_equipment_and_magic_namespaces_are_disjoint(self):
        # The precondition the three-way collision rule stands on: one item id
        # names one thing, whichever catalog carries it.
        equipment = load_equipment()
        shipped = {template.id for template in (*equipment.weapons, *equipment.armour, *equipment.gear)}
        shipped.update(template.id for template in equipment.ammunition)
        assert shipped.isdisjoint({template.id for template in load_magic_items().items})

    def test_a_treasure_weight_id_is_outside_the_identity_namespace(self):
        # 'gem' is an encumbrance row, not an item: bundling it collides with nothing.
        assert "gem" in {row.id for row in load_equipment().treasure_weights}
        adventure = bundled_adventure(items=(idol("gem"),), cache_item_ids=("gem",))
        validate_adventure(adventure, load_monsters(), load_equipment())
        session = GameSession.new(build_party(), adventure, seed=5)
        assert session.effective_equipment.get("gem").name == "Temple idol"


class TestTheEngine:
    def test_a_cache_take_reports_the_bundled_id(self):
        session = at_the_chest(bundled_adventure(items=(idol(),), cache_item_ids=(CUSTOM_ID,)))
        result = session.execute(TakeTreasure(feature_id="chest"))
        assert result.accepted
        assert CUSTOM_ID in acquired_ids(result)
        carried = [
            instance
            for member in session.party.members
            for instance in member.inventory.items
            if instance.template.id == CUSTOM_ID
        ]
        assert len(carried) == 1

    def test_grant_item_grants_a_bundled_id(self):
        session = GameSession.new(build_party(), bundled_adventure(items=(idol(),)), seed=5)
        result = session.execute(GrantItem(character_id="character-0001", item_id=CUSTOM_ID))
        assert result.accepted
        assert acquired_ids(result) == [CUSTOM_ID]
        assert session.member("character-0001").inventory.items[0].template is session.adventure.items[0]

    def test_give_items_hands_a_bundled_stack_across(self):
        session = GameSession.new(build_party(), bundled_adventure(items=(idol(),)), seed=5)
        session.execute(GrantItem(character_id="character-0001", item_id=CUSTOM_ID, quantity=2))
        result = session.execute(
            GiveItems(character_id="character-0001", recipient_id="character-0002", item_ids=(CUSTOM_ID,))
        )
        assert result.accepted
        giver = session.member("character-0001").inventory.items
        recipient = session.member("character-0002").inventory.items
        assert [instance.quantity for instance in giver] == [1]
        assert [instance.template.id for instance in recipient] == [CUSTOM_ID]
        assert recipient[0].template is session.adventure.items[0]

    def test_a_bundled_item_dropped_and_retaken_round_trips_the_pile(self):
        session = at_the_chest(bundled_adventure(items=(idol(),)))
        session.execute(GrantItem(character_id="character-0001", item_id=CUSTOM_ID))
        assert session.execute(DropItems(character_id="character-0001", item_ids=(CUSTOM_ID,))).accepted
        assert not any(member.inventory.items for member in session.party.members)
        result = session.execute(TakeTreasure(feature_id="pile"))
        assert result.accepted
        assert acquired_ids(result) == [CUSTOM_ID]

    def test_the_town_shop_refuses_a_bundled_id_as_unstocked(self):
        session = GameSession.new(build_party(), bundled_adventure(items=(idol(),)), seed=5)
        bundled = session.execute(PurchaseEquipment(character_id="character-0001", item_ids=(CUSTOM_ID,)))
        assert not bundled.accepted
        assert bundled.rejections[0].code == "items.purchase.not_stocked"
        assert bundled.rejections[0].params == {"item": CUSTOM_ID}
        unknown = session.execute(PurchaseEquipment(character_id="character-0001", item_ids=("no_such_item",)))
        assert not unknown.accepted
        assert unknown.rejections[0].code == "session.command.unknown_item"

    def test_the_shop_still_sells_the_shipped_lists(self):
        session = GameSession.new(build_party(), bundled_adventure(items=(idol(),)), seed=5)
        session.execute(GrantItem(character_id="character-0001", item_id=CUSTOM_ID))
        buyer = session.member("character-0001")
        buyer.inventory.purse.gp = 10
        result = session.execute(PurchaseEquipment(character_id="character-0001", item_ids=("torch",)))
        assert result.accepted
        assert "torch" in acquired_ids(result)

    def test_a_bundled_item_nobody_can_lift_is_left_behind(self):
        session = at_the_chest(
            bundled_adventure(items=(maul(),), cache_item_ids=("iron_maul",)),
            ruleset=Ruleset(encumbrance=EncumbranceMode.DETAILED),
        )
        result = session.execute(TakeTreasure(feature_id="chest"))
        assert result.accepted
        left = next(event for event in result.events if event.event_type == "items_left_behind")
        assert left.item_ids == ("iron_maul",)


class TestPersistenceAndReplay:
    def test_bundled_items_save_load_and_replay_to_equal_state(self):
        adventure = bundled_adventure(items=(idol(),), cache_item_ids=(CUSTOM_ID,))
        session = GameSession.new(build_party(), adventure, seed=11)
        commands = [
            PlaceParty(
                location=PartyLocation(
                    kind="dungeon", dungeon_id="delve", level_number=1, position=(3, 2), facing=Direction.NORTH
                )
            ),
            TakeTreasure(feature_id="chest"),
            GrantItem(character_id="character-0002", item_id=CUSTOM_ID),
            DropItems(character_id="character-0002", item_ids=(CUSTOM_ID,)),
        ]
        accepted = [command for command in commands if session.execute(command).accepted]
        assert len(accepted) == len(commands)
        pile = session.dungeon_state.piles["cell:delve:1:3,2"]
        assert [dropped.item_id for dropped in pile.items] == [CUSTOM_ID]
        restored = load_game(json.loads(json.dumps(save_game(session))))
        assert session_state(restored) == session_state(session)

        from osrlib.core.character import party_to_document

        replayed = replay_game(11, party_to_document(build_party().members), adventure, Ruleset(), accepted)
        assert session_state(replayed) == session_state(session)

    def test_a_doctored_save_with_a_colliding_bundled_id_fails_typed(self):
        session = GameSession.new(build_party(), bundled_adventure(items=(idol(),)), seed=5)
        document = json.loads(json.dumps(save_game(session)))
        torch = load_equipment().get("torch")
        document["payload"]["adventure"]["items"].append(torch.model_dump(mode="json"))
        with pytest.raises(ContentValidationError, match="colliding item ids"):
            load_game(document)


class TestEffectiveCatalog:
    def test_an_empty_bundle_is_the_cached_base_catalog_object(self):
        session = GameSession.new(build_party(), build_adventure(), seed=5)
        assert session.effective_equipment is load_equipment()

    def test_a_bundle_resolves_base_and_bundled_ids(self):
        session = GameSession.new(build_party(), bundled_adventure(items=(idol(), maul())), seed=5)
        assert session.effective_equipment.get("torch").id == "torch"
        assert session.effective_equipment.get(CUSTOM_ID) is session.adventure.items[0]
        assert session.effective_equipment.get("iron_maul").weight_coins == 2000
        assert session.effective_equipment.treasure_weights == load_equipment().treasure_weights

    def test_the_property_is_stable_across_calls(self):
        session = GameSession.new(build_party(), bundled_adventure(items=(idol(),)), seed=5)
        assert session.effective_equipment is session.effective_equipment
