"""The player view names every item whose id it shows (issue #316, part 3).

A remote front end builds its screens from `PlayerView` and the player-visible events alone. The view shows items by
id: a catalog id like `"holy_water"` for a mundane stack, an instance id like `"magic-item-0003"` for a magic item,
and an instance id like `"valuable-0007"` for a gem or piece of jewellery. A pile shows strings like
`"holy_water×2"`. `PlayerView.item_names` and `item_names_plural` carry a singular and a plural for every one of
those ids, read from the template a stack embeds or the session's catalog, so an item the adventure bundles is named.
`PileView.contents` gives each pile entry's id and count beside the strings.

A masked item keeps its mask. Until the party identifies a magic item, its name is the masked name the inventory
shows, without the article, so a cursed item and the item it passes for read the same. The OSE SRD's Armour and
Shields page says "All types of tests indicate that a cursed shield or suit of armour is enchanted with a +1 bonus",
which is why `tests/test_unrevealed_curse_view.py` pins a cursed shield to the view of a shield +1.
"""

import json
from pathlib import Path

import pytest

from crawl_fixtures import build_party
from osrlib.core.character import party_from_document
from osrlib.core.events import Visibility
from osrlib.core.items import GearTemplate, MagicItemInstance, ValuableInstance
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import DropItems, EnterDungeon, GrantItem, IdentifyItem, TakeTreasure, UseItem
from osrlib.crawl.dungeon import AreaSpec, DungeonSpec, FeatureSpec, LevelSpec, ValuableSpec
from osrlib.crawl.events import ItemAcquiredEvent, ItemIdentifiedEvent, ItemsDroppedEvent
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.crawl.views import PileItemView, PlayerView
from osrlib.data import load_equipment, load_magic_items
from osrlib.persistence import load_game
from test_loot_and_traps import breath_kill

FIGHTER = "character-0001"

TEMPLE_IDOL = GearTemplate.model_validate(
    {
        "id": "temple_idol",
        "name": "Idol of the drowned saint",
        "cost_gp": 0,
        "display_name": "idol of the drowned saint",
        "display_name_plural": "idols of the drowned saint",
    }
)
"""A bundled item with names its author wrote. `load_equipment()` has no `temple_idol`."""

SEAL_RING = ValuableSpec(kind="jewellery", name="The abbot's seal ring", value_gp=900)
PLAIN_GEM = ValuableSpec(kind="gem", value_gp=50)


def _adventure() -> Adventure:
    """One cell, the entrance, in an area whose cache holds gear, a bundled item, a potion, and two valuables."""
    vault = FeatureSpec(
        id="reliquary",
        kind="treasure_cache",
        description="A lead-lined reliquary.",
        cell=(0, 0),
        item_ids=("holy_water", "holy_water", "temple_idol"),
        magic_item_ids=("potion_of_healing",),
        valuables=(SEAL_RING, PLAIN_GEM),
    )
    level = LevelSpec(
        number=1,
        width=1,
        height=1,
        entrance=(0, 0),
        areas=(AreaSpec(id="vault", name="Vault", cells=((0, 0),), features=(vault,)),),
    )
    return Adventure(
        name="The Drowned Saint",
        town=TownSpec(name="Threshold", travel_turns={"shrine": 1}),
        dungeons=(DungeonSpec(id="shrine", name="The Shrine", levels=(level,)),),
        items=(TEMPLE_IDOL,),
    )


def _player_view(game: GameSession) -> PlayerView:
    view = game.view(Visibility.PLAYER)
    assert isinstance(view, PlayerView)
    return view


def _looted() -> tuple[GameSession, list[ItemAcquiredEvent]]:
    """The stock party enters the shrine and empties the reliquary. Returns the session and the acquisitions."""
    game = GameSession.new(build_party(), _adventure(), seed=5)
    assert game.execute(EnterDungeon(dungeon_id="shrine")).accepted
    (feature,) = _player_view(game).explored[0].features
    taken = game.execute(TakeTreasure(feature_id=feature.id))
    assert taken.accepted, [rejection.code for rejection in taken.rejections]
    acquired = [event for event in taken.events if isinstance(event, ItemAcquiredEvent)]
    assert acquired, "precondition: the party took the reliquary's contents"
    return game, acquired


def _ids_of(acquired: list[ItemAcquiredEvent]) -> tuple[str, str, str]:
    """The potion's and the two valuables' instance ids, in the order potion, seal ring, gem."""
    ids = [item_id for event in acquired for item_id in event.item_ids]
    potion = next(item_id for item_id in ids if item_id.startswith("magic-item-"))
    valuables = [item_id for item_id in ids if item_id.startswith("valuable-")]
    assert len(valuables) == 2
    seal_ring, gem = sorted(valuables)
    return potion, seal_ring, gem


def _holders(game: GameSession, item_id: str) -> list[str]:
    """Every member who carries `item_id`, once per unit, as a catalog id or an instance id."""
    held = []
    for member in game.party.members:
        for instance in member.inventory.all_instances():
            if isinstance(instance, MagicItemInstance):
                if instance.instance_id == item_id:
                    held.append(member.id)
            elif instance.template.id == item_id:
                held.extend([member.id] * instance.quantity)
        held.extend(member.id for valuable in member.inventory.valuables if valuable.instance_id == item_id)
    return held


def _drop_everything(game: GameSession, order: list[str]) -> list[ItemsDroppedEvent]:
    """Each holder drops each unit of the ids in `order`, in that order, onto the party's cell."""
    dropped = []
    for item_id in order:
        for member_id in _holders(game, item_id):
            result = game.execute(DropItems(character_id=member_id, item_ids=(item_id,)))
            assert result.accepted, [rejection.code for rejection in result.rejections]
            dropped += [event for event in result.events if isinstance(event, ItemsDroppedEvent)]
    return dropped


def _shown_ids(view: PlayerView) -> set[str]:
    """Every item id the view shows, read from the inventories and from the piles' contents."""
    shown: set[str] = set()
    for member in view.party:
        inventory = member.inventory
        entries = [*inventory["items"], *inventory["wielded"], *inventory["rings"]]
        entries += [entry for entry in (inventory["worn_armour"], inventory["shield"]) if entry is not None]
        for entry in entries:
            if entry["instance_type"] == "magic_item":
                shown.add(entry["instance_id"])
                if entry["identified"]:
                    shown.add(entry["template_id"])
            else:
                shown.add(entry["template"]["id"])
        shown.update(valuable["instance_id"] for valuable in inventory["valuables"])
    for pile in view.piles.values():
        shown.update(entry.item_id for entry in pile.contents)
    return shown


# ---------------------------------------------------------------------- what the party carries


def test_every_item_a_member_carries_is_named_singular_and_plural():
    """The reliquary's contents, spread across the party's packs.

    Holy water is named from the template the stack embeds, the bundled idol with the names its author wrote, the
    unidentified potion by its masked name, and each valuable by its kind. The seal ring's own name stays in the
    inventory's `valuables`.
    """
    game, acquired = _looted()
    potion, seal_ring, gem = _ids_of(acquired)
    view = _player_view(game)

    assert view.item_names == {
        "holy_water": "vial of holy water",
        "temple_idol": "idol of the drowned saint",
        potion: "potion",
        seal_ring: "piece of jewellery",
        gem: "gem",
    }
    assert view.item_names_plural == {
        "holy_water": "vials of holy water",
        "temple_idol": "idols of the drowned saint",
        potion: "potions",
        seal_ring: "pieces of jewellery",
        gem: "gems",
    }


def test_every_id_an_acquisition_names_is_a_key_in_the_view_after_it():
    """`ItemAcquiredEvent.item_ids` repeats a catalog id once per unit, and each id is named after the command."""
    game, acquired = _looted()
    view = _player_view(game)

    for event in acquired:
        for item_id in event.item_ids:
            assert item_id in view.item_names and item_id in view.item_names_plural, item_id
    assert sum(event.item_ids.count("holy_water") for event in acquired) == 2


def test_the_view_names_exactly_the_ids_it_shows():
    """Every key is an id the inventories or the piles' contents show, and every such id is a key.

    The state holds an identified magic item in each place: a ring of protection the fighter carries, which shows
    its template id, and the reliquary's potion, identified and then dropped, which a pile shows by its instance id
    alone. Without the identified potion in the pile, a view that keyed a pile item's template id would pass.
    """
    game, acquired = _looted()
    potion, _, _ = _ids_of(acquired)
    ring = _granted(game, "ring_of_protection")
    for instance_id in (ring, potion):
        holder = _holders(game, instance_id)[0]
        identified = game.execute(IdentifyItem(character_id=holder, item_id=instance_id))
        assert identified.accepted, [rejection.code for rejection in identified.rejections]
    _drop_everything(game, ["holy_water", potion])
    view = _player_view(game)

    assert "ring_of_protection" in _shown_ids(view)
    assert potion in _shown_ids(view) and "potion_of_healing" not in _shown_ids(view)
    assert set(view.item_names) == _shown_ids(view)
    assert set(view.item_names_plural) == set(view.item_names)


def test_an_identified_item_in_a_pile_is_named_by_its_instance_id_alone():
    """A wand of cold, identified and then dropped in the dungeon.

    The pile shows it by its instance id and its true name, never its template id, so `wand_of_cold` is no key.
    """
    game = _town()
    instance_id = _granted(game, "wand_of_cold")
    assert game.execute(IdentifyItem(character_id=FIGHTER, item_id=instance_id)).accepted
    assert game.execute(EnterDungeon(dungeon_id="shrine")).accepted
    dropped = game.execute(DropItems(character_id=FIGHTER, item_ids=(instance_id,)))
    assert dropped.accepted, [rejection.code for rejection in dropped.rejections]

    view = _player_view(game)

    (pile,) = view.piles.values()
    assert pile.contents == (PileItemView(item_id=instance_id, quantity=1),)
    assert view.item_names == {instance_id: "wand of cold"}
    assert view.item_names_plural == {instance_id: "wands of cold"}


def test_a_party_that_carries_nothing_has_no_item_names():
    """The stock party starts with empty packs. This holds before the chunk too."""
    game = GameSession.new(build_party(), _adventure(), seed=5)

    view = _player_view(game)

    assert view.item_names == {}
    assert view.item_names_plural == {}


# ---------------------------------------------------------------------- what lies in a pile


def test_a_piles_contents_give_each_entrys_id_and_count_beside_the_strings():
    """The whole haul dropped on the party's cell. The strings stay as they were, and `contents` follows their order.

    The pile lists mundane stacks, then magic items, then valuables. The two vials merge into one stack of 2 whoever
    dropped them, and the valuables keep the order they were dropped in.
    """
    game, acquired = _looted()
    potion, seal_ring, gem = _ids_of(acquired)
    dropped = _drop_everything(game, ["holy_water", "temple_idol", potion, seal_ring, gem])
    view = _player_view(game)

    (pile,) = view.piles.values()
    assert pile.items == ("holy_water×2", "temple_idol×1", "a potion", "The abbot's seal ring", "gem")
    assert pile.contents == (
        PileItemView(item_id="holy_water", quantity=2),
        PileItemView(item_id="temple_idol", quantity=1),
        PileItemView(item_id=potion, quantity=1),
        PileItemView(item_id=seal_ring, quantity=1),
        PileItemView(item_id=gem, quantity=1),
    )
    for event in dropped:
        for item_id in event.item_ids:
            assert item_id in view.item_names, item_id


def test_a_bundled_item_in_a_pile_is_named_from_the_sessions_catalog():
    """Nobody carries the idol once it's dropped, so its name comes from `effective_equipment`, not a pack."""
    game, acquired = _looted()
    potion, seal_ring, gem = _ids_of(acquired)
    _drop_everything(game, ["holy_water", "temple_idol", potion, seal_ring, gem])

    view = _player_view(game)

    assert all(member.inventory["items"] == [] for member in view.party)
    assert view.item_names == {
        "holy_water": "vial of holy water",
        "temple_idol": "idol of the drowned saint",
        potion: "potion",
        seal_ring: "piece of jewellery",
        gem: "gem",
    }
    assert view.item_names_plural["temple_idol"] == "idols of the drowned saint"


# ---------------------------------------------------------------------- masked and identified magic items


def _granted(game: GameSession, item_id: str, member_id: str = FIGHTER) -> str:
    result = game.execute(GrantItem(character_id=member_id, item_id=item_id))
    assert result.accepted, [rejection.code for rejection in result.rejections]
    (instance_id,) = next(event.item_ids for event in result.events if isinstance(event, ItemAcquiredEvent))
    return instance_id


def _town() -> GameSession:
    return GameSession.new(build_party(), _adventure(), seed=5)


def _entry(view: PlayerView, instance_id: str, member_id: str = FIGHTER) -> dict:
    (member,) = (member for member in view.party if member.id == member_id)
    return next(entry for entry in member.inventory["items"] if entry.get("instance_id") == instance_id)


@pytest.mark.parametrize(
    ("template_id", "names"),
    [
        pytest.param("potion_of_healing", ("potion", "potions"), id="potion"),
        pytest.param("spell_scroll_1", ("scroll", "scrolls"), id="scroll"),
        pytest.param("ring_of_protection", ("ring", "rings"), id="ring"),
        pytest.param("wand_of_cold", ("wand", "wands"), id="wand"),
        pytest.param("staff_of_healing", ("staff", "staves"), id="staff"),
        pytest.param("rod_of_cancellation", ("rod", "rods"), id="rod"),
        pytest.param("bag_of_holding", ("curious device", "curious devices"), id="misc"),
    ],
)
def test_an_unidentified_item_is_named_by_its_category_mask(template_id: str, names: tuple[str, str]):
    """The mask is the inventory's `display` without its article, and the template id stays out of the view."""
    game = _town()
    instance_id = _granted(game, template_id)

    view = _player_view(game)

    assert (view.item_names[instance_id], view.item_names_plural[instance_id]) == names
    assert _entry(view, instance_id)["display"] == f"a {names[0]}"
    assert template_id not in view.model_dump_json()


def test_an_unidentified_items_true_name_never_reaches_the_view():
    """Neither the potion's catalog id nor its true name, in either case, appears anywhere in the view."""
    game = _town()
    _granted(game, "potion_of_healing")

    blob = _player_view(game).model_dump_json()

    for hidden in ("potion_of_healing", "Potion of Healing", "potion of healing", "potions of healing"):
        assert hidden not in blob
    assert '"potion"' in blob


@pytest.mark.parametrize(
    ("cursed", "lookalike", "base", "names"),
    [
        pytest.param(
            "cursed_shield_minus_2",
            "shield_plus_1",
            None,
            ("shield with a faint aura", "shields with a faint aura"),
            id="shield_minus_2",
        ),
        pytest.param(
            "cursed_shield_ac_9",
            "shield_plus_1",
            None,
            ("shield with a faint aura", "shields with a faint aura"),
            id="shield_ac_9",
        ),
        pytest.param(
            "cursed_armour_minus_1",
            "armour_plus_1",
            "chainmail",
            ("suit of chainmail with a faint aura", "suits of chainmail with a faint aura"),
            id="armour_minus_1",
        ),
        pytest.param(
            "cursed_armour_ac_9",
            "armour_plus_1",
            "plate_mail",
            ("suit of plate mail with a faint aura", "suits of plate mail with a faint aura"),
            id="armour_ac_9",
        ),
    ],
)
def test_a_cursed_item_and_its_lookalike_have_the_same_names(cursed, lookalike, base, names):
    """Two sessions that differ only in which of the pair the fighter got show the same names for it."""
    views = []
    for template_id in (cursed, lookalike):
        game = _town()
        instance_id = _granted(game, template_id)
        if base is not None:
            instance = game.member(FIGHTER).inventory.magic_item(instance_id)
            assert instance is not None
            instance.base_item_id = base
        views.append((instance_id, _player_view(game)))

    (cursed_id, cursed_view), (lookalike_id, lookalike_view) = views
    assert cursed_id == lookalike_id
    assert cursed_view.item_names == lookalike_view.item_names == {cursed_id: names[0]}
    assert cursed_view.item_names_plural == lookalike_view.item_names_plural == {cursed_id: names[1]}
    assert cursed not in cursed_view.model_dump_json()


@pytest.mark.parametrize(
    ("template_id", "base", "display", "names"),
    [
        # The base's display_name with an article: "an" before a vowel. Today it reads "a arrows (quiver of 20) ...".
        pytest.param(
            "arrows_plus_1",
            "arrows",
            "an arrow with a faint aura",
            ("arrow with a faint aura", "arrows with a faint aura"),
            id="arrows",
        ),
        pytest.param(
            "crossbow_bolts_plus_2",
            "crossbow_bolts",
            "a crossbow bolt with a faint aura",
            ("crossbow bolt with a faint aura", "crossbow bolts with a faint aura"),
            id="crossbow_bolts",
        ),
        # Body armour is a suit. Today it reads "a chainmail with a faint aura".
        pytest.param(
            "armour_plus_1",
            "chainmail",
            "a suit of chainmail with a faint aura",
            ("suit of chainmail with a faint aura", "suits of chainmail with a faint aura"),
            id="chainmail",
        ),
        # A suit with no mundane item recorded. Today it reads "a arm with a faint aura".
        pytest.param(
            "armour_plus_1",
            None,
            "a suit of armour with a faint aura",
            ("suit of armour with a faint aura", "suits of armour with a faint aura"),
            id="no_base",
        ),
        # The names a single word gives are unchanged in `display`, and these two already hold.
        pytest.param("shield_plus_1", "shield", "a shield with a faint aura", None, id="shield"),
        pytest.param("dagger_plus_1", "dagger", "a dagger with a faint aura", None, id="dagger"),
    ],
)
def test_an_enchanted_arm_is_named_after_one_unit_of_its_base(template_id, base, display, names):
    """The mask names the mundane item underneath by its display_name, so a stack of arrows reads in units."""
    game = _town()
    quantity = 13 if base in ("arrows", "crossbow_bolts") else 1
    instance = MagicItemInstance(
        instance_id="magic-item-9001", template_id=template_id, base_item_id=base, quantity=quantity
    )
    game.member(FIGHTER).inventory.items.append(instance)

    view = _player_view(game)

    assert _entry(view, "magic-item-9001")["display"] == display
    if names is not None:
        assert (view.item_names["magic-item-9001"], view.item_names_plural["magic-item-9001"]) == names


def test_an_identified_item_is_named_by_its_template_under_both_ids():
    """Once identified, the inventory shows the template id, and it and the instance id have the true names."""
    game = _town()
    instance_id = _granted(game, "potion_of_healing")
    identified = game.execute(IdentifyItem(character_id=FIGHTER, item_id=instance_id))
    assert identified.accepted, [rejection.code for rejection in identified.rejections]

    view = _player_view(game)

    assert _entry(view, instance_id)["template_id"] == "potion_of_healing"
    assert view.item_names == {instance_id: "potion of healing", "potion_of_healing": "potion of healing"}
    assert view.item_names_plural == {instance_id: "potions of healing", "potion_of_healing": "potions of healing"}


def test_the_identification_event_names_the_item():
    """`IdentifyItem` leaves the potion in the pack, and the event and the view give the same names."""
    game = _town()
    instance_id = _granted(game, "potion_of_healing")

    result = game.execute(IdentifyItem(character_id=FIGHTER, item_id=instance_id))

    (event,) = (event for event in result.events if isinstance(event, ItemIdentifiedEvent))
    assert (event.display_name, event.display_name_plural) == ("potion of healing", "potions of healing")
    assert _player_view(game).item_names[instance_id] == event.display_name


def test_a_potion_identified_by_drinking_it_is_named_only_by_its_event():
    """Drinking an unidentified potion identifies it and uses it up in one command.

    The view before showed it masked, and the view after doesn't show it, so neither names `potion_of_healing`. The
    identification event does.
    """
    game = _town()
    instance_id = _granted(game, "potion_of_healing")
    assert game.execute(EnterDungeon(dungeon_id="shrine")).accepted
    before = _player_view(game)

    result = game.execute(UseItem(character_id=FIGHTER, item_id=instance_id))
    assert result.accepted, [rejection.code for rejection in result.rejections]

    after = _player_view(game)
    (event,) = (event for event in result.events if isinstance(event, ItemIdentifiedEvent))
    assert event.template_id == "potion_of_healing"
    assert (event.display_name, event.display_name_plural) == ("potion of healing", "potions of healing")
    assert before.item_names == {instance_id: "potion"}
    assert "potion_of_healing" not in before.item_names and "potion_of_healing" not in after.item_names
    assert instance_id not in after.item_names


# ---------------------------------------------------------------------- valuables


def test_a_valuable_is_named_by_its_kind_whatever_its_own_name():
    """A gem the treasure tables roll is named `"Gem"`, and a piece of jewellery can have any name an author gives it.

    Both are named by their kind, and the inventory keeps each one's own name and value, which the players see from
    the moment they take it.
    """
    game = _town()
    valuables = game.member(FIGHTER).inventory.valuables
    valuables.append(ValuableInstance(instance_id="valuable-9001", kind="gem", name="Gem", value_gp=50))
    valuables.append(ValuableInstance(instance_id="valuable-9002", kind="jewellery", name="Jewellery", value_gp=600))
    valuables.append(
        ValuableInstance(instance_id="valuable-9003", kind="jewellery", name="The abbot's seal ring", value_gp=900)
    )

    view = _player_view(game)

    assert view.item_names == {
        "valuable-9001": "gem",
        "valuable-9002": "piece of jewellery",
        "valuable-9003": "piece of jewellery",
    }
    assert view.item_names_plural == {
        "valuable-9001": "gems",
        "valuable-9002": "pieces of jewellery",
        "valuable-9003": "pieces of jewellery",
    }
    (member,) = (member for member in view.party if member.id == FIGHTER)
    assert [(entry["name"], entry["value_gp"]) for entry in member.inventory["valuables"]] == [
        ("Gem", 50),
        ("Jewellery", 600),
        ("The abbot's seal ring", 900),
    ]


# ---------------------------------------------------------------------- destroyed equipment


def test_a_destroyed_unidentified_item_is_named_by_its_mask():
    """A hellhound's breath kills a member whose pack holds one unidentified Sword +1, which fails its save.

    `EquipmentDestroyedEvent` has player visibility, so it names the sword as the inventory showed it, "a sword with a
    faint aura", not as "Sword +1". The sword's base is the mundane sword, so the mask is the sword's display name.
    """
    _, _, event = breath_kill(need_saved=False)

    assert event.visibility is Visibility.PLAYER
    assert event.item_names == ("a sword with a faint aura",)


# ---------------------------------------------------------------------- saves and parties from 2.7.0

SAVES = Path(__file__).parent / "saves"

SAVE_2_7_0 = SAVES / "view_item_names_2.7.0.json"
"""A save that osrlib 2.7.0 wrote, before the templates had display names. It's kept as 2.7.0 wrote it.

The adventure bundles `temple_idol`, named "Idol of the drowned saint", with no display names. The fighter carries
2 vials of holy water, 6 torches, 20 arrows, a short bow, an idol, and an unidentified potion of healing, and wears
chainmail and wields a sword. The party stands on the shrine's one cell, where the fighter dropped 2 flasks of oil
and the second idol.
"""

PARTY_2_7_0 = SAVES / "view_item_names_party_2.7.0.json"
"""The same party as a party document that osrlib 2.7.0 wrote, in town, before the drop: the fighter also carries
the 2 flasks of oil and both idols."""

CATALOG_NAMES = {
    "holy_water": ("vial of holy water", "vials of holy water"),
    "torch": ("torch", "torches"),
    "arrows": ("arrow", "arrows"),
    "short_bow": ("short bow", "short bows"),
    "chainmail": ("suit of chainmail", "suits of chainmail"),
    "sword": ("sword", "swords"),
    "oil_flask": ("flask of oil", "flasks of oil"),
}
"""The compiled catalog's names for the built-in items the fixtures hold, as `test_item_display_names.py` pins them."""

UNNAMED_IDOL = ("Idol of the drowned saint", "Idols of the drowned saint")
"""The names a template written without them gets: its `name`, and the ending on the word before " of "."""


def _embedded_templates(document) -> list[dict]:
    """Every template a document embeds in a mundane stack, wherever it sits."""
    found = []
    if isinstance(document, dict):
        if document.get("instance_type") == "item":
            found.append(document["template"])
        for value in document.values():
            found += _embedded_templates(value)
    elif isinstance(document, list):
        for value in document:
            found += _embedded_templates(value)
    return found


def _names(view: PlayerView) -> dict[str, tuple[str, str]]:
    return {item_id: (view.item_names[item_id], view.item_names_plural.get(item_id, "")) for item_id in view.item_names}


def test_the_2_7_0_fixtures_embed_templates_without_display_names():
    """The precondition for the two tests below: osrlib 2.7.0 wrote them, so no embedded template has the fields."""
    for path in (SAVE_2_7_0, PARTY_2_7_0):
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["engine_version"] == "2.7.0"
        templates = _embedded_templates(document)
        assert {template["id"] for template in templates} >= {"holy_water", "torch", "chainmail", "temple_idol"}
        assert all("display_name" not in template for template in templates)


def test_a_2_7_0_save_names_built_in_items_from_the_catalog():
    """Each carried stack is named by the session's catalog, never by the template the save embedded.

    Named from the embedded template, holy water would read "Holy water (vial)", with the plural
    "Holy water (vial)s". The bundled idol has no display names in the save's adventure either, so the catalog's
    template gets the defaults, and the carried idol and the one in the pile read the same.
    """
    game = load_game(json.loads(SAVE_2_7_0.read_text(encoding="utf-8")))

    view = _player_view(game)

    (pile,) = view.piles.values()
    assert pile.contents == (
        PileItemView(item_id="oil_flask", quantity=2),
        PileItemView(item_id="temple_idol", quantity=1),
    )
    assert _names(view) == {**CATALOG_NAMES, "temple_idol": UNNAMED_IDOL, "magic-item-0001": ("potion", "potions")}


def test_a_2_7_0_party_document_starts_a_session_with_catalog_names():
    """The party document goes into a new session of an adventure whose idol has names its author wrote.

    The carried idol embeds 2.7.0's template, with no names, and the view names it as the catalog does.
    """
    party = Party(members=party_from_document(json.loads(PARTY_2_7_0.read_text(encoding="utf-8"))))
    game = GameSession.new(party, _adventure(), seed=5)

    view = _player_view(game)

    assert _names(view) == {
        **CATALOG_NAMES,
        "temple_idol": ("idol of the drowned saint", "idols of the drowned saint"),
        "magic-item-0001": ("potion", "potions"),
    }


def test_a_carried_item_the_catalog_lacks_is_named_from_its_embedded_template():
    """The same party in an adventure that bundles no idol: the catalog has no `temple_idol`.

    The view still names the carried idol, from the template it embeds, with the defaults a template written without
    names gets. Every built-in item still comes from the catalog.
    """
    party = Party(members=party_from_document(json.loads(PARTY_2_7_0.read_text(encoding="utf-8"))))
    level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
    adventure = Adventure(
        name="An Empty Crypt",
        town=TownSpec(name="Threshold"),
        dungeons=(DungeonSpec(id="crypt", name="Crypt", levels=(level,)),),
    )
    game = GameSession.new(party, adventure, seed=5)

    view = _player_view(game)

    assert _names(view) == {**CATALOG_NAMES, "temple_idol": UNNAMED_IDOL, "magic-item-0001": ("potion", "potions")}


# ---------------------------------------------------------------------- masks that changed from 2.7.0

MASKS_2_7_0_BY_BASE = {
    None: "a arm with a faint aura",
    "battle_axe": "a battle axe with a faint aura",
    "club": "a club with a faint aura",
    "crossbow": "a crossbow with a faint aura",
    "dagger": "a dagger with a faint aura",
    "hand_axe": "a hand axe with a faint aura",
    "javelin": "a javelin with a faint aura",
    "lance": "a lance with a faint aura",
    "long_bow": "a long bow with a faint aura",
    "mace": "a mace with a faint aura",
    "pole_arm": "a pole arm with a faint aura",
    "short_bow": "a short bow with a faint aura",
    "short_sword": "a short sword with a faint aura",
    "silver_dagger": "a silver dagger with a faint aura",
    "sling": "a sling with a faint aura",
    "spear": "a spear with a faint aura",
    "staff": "a staff with a faint aura",
    "sword": "a sword with a faint aura",
    "two_handed_sword": "a two-handed sword with a faint aura",
    "war_hammer": "a war hammer with a faint aura",
    "chainmail": "a chainmail with a faint aura",
    "leather": "a leather with a faint aura",
    "plate_mail": "a plate mail with a faint aura",
    "shield": "a shield with a faint aura",
    "arrows": "a arrows (quiver of 20) with a faint aura",
    "crossbow_bolts": "a crossbow bolts (case of 30) with a faint aura",
    "silver_tipped_arrow": "a silver tipped arrow (1) with a faint aura",
    "sling_stones": "a sling stones with a faint aura",
}
"""The `display` osrlib 2.7.0 gave an unidentified enchanted weapon, suit of armour, or shield, by its base item.

The base is the instance's `base_item_id`, or the template's when the instance has none. Computed by running 2.7.0's
masking over every magic item of the armour, sword, and weapon categories with no base and with each mundane weapon,
armour, and ammunition id as its base: 1,433 instances in all, each giving the string its base gives here.
"""

MASKS_2_7_0_BY_CATEGORY = {
    "misc": "a curious device",
    "potion": "a potion",
    "ring": "a ring",
    "rod": "a rod",
    "scroll": "a scroll",
    "staff": "a staff",
    "wand": "a wand",
}
"""The `display` osrlib 2.7.0 gave every unidentified magic item of the other categories, whatever its base."""

CHANGED_MASKS = {
    "a arm with a faint aura": "a suit of armour with a faint aura",
    "a arrows (quiver of 20) with a faint aura": "an arrow with a faint aura",
    "a chainmail with a faint aura": "a suit of chainmail with a faint aura",
    "a crossbow bolts (case of 30) with a faint aura": "a crossbow bolt with a faint aura",
    "a leather with a faint aura": "a suit of leather armour with a faint aura",
    "a plate mail with a faint aura": "a suit of plate mail with a faint aura",
    "a silver tipped arrow (1) with a faint aura": "a silver tipped arrow with a faint aura",
    "a sling stones with a faint aura": "a sling stone with a faint aura",
}
"""Every 2.7.0 mask that reads differently now, and what it reads as. Every other mask is unchanged."""

ARM_CATEGORIES = ("armour", "sword", "weapon")


def test_the_masks_that_changed_from_2_7_0_are_exactly_these():
    """Every unidentified magic item, with every base an arm can have, shows the 2.7.0 mask or its replacement.

    The instances sit in one fighter's pack, and the test reads each `display` from the player view.
    """
    equipment = load_equipment()
    bases = [None, *(template.id for template in (*equipment.weapons, *equipment.armour, *equipment.ammunition))]
    game = _town()
    pack = game.member(FIGHTER).inventory.items
    expected: dict[str, str] = {}
    changed: dict[str, str] = {}
    for template in load_magic_items().items:
        arm = template.category.value in ARM_CATEGORIES
        for base in bases if arm else [None]:
            instance_id = f"magic-item-{len(expected) + 1:05d}"
            pack.append(MagicItemInstance(instance_id=instance_id, template_id=template.id, base_item_id=base))
            if arm:
                old = MASKS_2_7_0_BY_BASE[base or template.base_item_id]
            else:
                old = MASKS_2_7_0_BY_CATEGORY[template.category.value]
            expected[instance_id] = CHANGED_MASKS.get(old, old)
            if old in CHANGED_MASKS:
                changed[old] = CHANGED_MASKS[old]

    view = _player_view(game)

    assert len(expected) == 1433
    (member,) = (member for member in view.party if member.id == FIGHTER)
    shown = {entry["instance_id"]: entry["display"] for entry in member.inventory["items"]}
    assert shown == expected
    assert changed == CHANGED_MASKS
