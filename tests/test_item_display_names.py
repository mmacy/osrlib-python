"""Every item template has a name for one unit in a sentence and a name for two or more (issue #316, part 3).

A remote front end words a count, like "The party finds 2 vials of holy water", from the player view and the events
alone. The templates' `name` is the price list's label, like `"Holy water (vial)"` or `"Torches (6)"`, which names a
lot and has no plural, so a client can't build that sentence from it. `display_name` and `display_name_plural` are
the words a sentence needs, as `MonsterTemplate.display_name` and `display_name_plural` are for monsters, and
`PlayerView.item_names` carries them to the client.

The unit is the one the session counts. `ItemInstance.quantity` counts individual items, not lots, so the OSE SRD's
"Torches (6)" is six units of `"torch"`, "Iron spikes (12)" twelve of `"iron spike"`, "Wine (2 pints)" two of
`"pint of wine"`, and "Rations (iron, 7 days)" seven of `"iron ration"`. The SRD's gear descriptions name the
containers: "Holy water: Water blessed by a holy person, stored in a special vial" and "Oil flask: A flask of oil
fuels a lantern", so a unit of holy water is a vial and a unit of oil a flask.
"""

import pytest

from osrlib.core.items import AmmunitionTemplate, ArmourTemplate, GearTemplate, MagicItemTemplate, WeaponTemplate
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
from osrlib.data import load_equipment, load_magic_items

XFAIL = pytest.mark.xfail(reason="chunk: view-item-names")

MUNDANE = {
    # Gear. A lot from the price list becomes one unit, and a substance becomes its container or measure.
    "backpack": ("backpack", "backpacks"),
    "crowbar": ("crowbar", "crowbars"),
    "garlic": ("bulb of garlic", "bulbs of garlic"),
    "grappling_hook": ("grappling hook", "grappling hooks"),
    "hammer": ("small hammer", "small hammers"),
    "holy_symbol": ("holy symbol", "holy symbols"),
    "holy_water": ("vial of holy water", "vials of holy water"),
    "iron_spikes": ("iron spike", "iron spikes"),
    "lantern": ("lantern", "lanterns"),
    "mirror": ("hand mirror", "hand mirrors"),
    "oil_flask": ("flask of oil", "flasks of oil"),
    "pole": ("10’ pole", "10’ poles"),
    "rations_iron": ("iron ration", "iron rations"),
    "rations_standard": ("standard ration", "standard rations"),
    "rope": ("50’ coil of rope", "50’ coils of rope"),
    "sack_large": ("large sack", "large sacks"),
    "sack_small": ("small sack", "small sacks"),
    "stakes_and_mallet": ("set of stakes and mallet", "sets of stakes and mallet"),
    "thieves_tools": ("set of thieves’ tools", "sets of thieves’ tools"),
    "tinder_box": ("tinder box", "tinder boxes"),
    "torch": ("torch", "torches"),
    "waterskin": ("waterskin", "waterskins"),
    "wine": ("pint of wine", "pints of wine"),
    "wolfsbane": ("bunch of wolfsbane", "bunches of wolfsbane"),
    # Ammunition: one arrow, not the quiver of 20.
    "arrows": ("arrow", "arrows"),
    "crossbow_bolts": ("crossbow bolt", "crossbow bolts"),
    "silver_tipped_arrow": ("silver tipped arrow", "silver tipped arrows"),
    "sling_stones": ("sling stone", "sling stones"),
    # Weapons: the printed name in lowercase, with the regular plural except for the staff.
    "battle_axe": ("battle axe", "battle axes"),
    "club": ("club", "clubs"),
    "crossbow": ("crossbow", "crossbows"),
    "dagger": ("dagger", "daggers"),
    "hand_axe": ("hand axe", "hand axes"),
    "javelin": ("javelin", "javelins"),
    "lance": ("lance", "lances"),
    "long_bow": ("long bow", "long bows"),
    "mace": ("mace", "maces"),
    "pole_arm": ("pole arm", "pole arms"),
    "short_bow": ("short bow", "short bows"),
    "short_sword": ("short sword", "short swords"),
    "silver_dagger": ("silver dagger", "silver daggers"),
    "sling": ("sling", "slings"),
    "spear": ("spear", "spears"),
    "staff": ("staff", "staves"),
    "sword": ("sword", "swords"),
    "two_handed_sword": ("two-handed sword", "two-handed swords"),
    "war_hammer": ("war hammer", "war hammers"),
    # Armour: body armour is a suit, and the SRD's "Leather" is leather armour.
    "chainmail": ("suit of chainmail", "suits of chainmail"),
    "leather": ("suit of leather armour", "suits of leather armour"),
    "plate_mail": ("suit of plate mail", "suits of plate mail"),
    "shield": ("shield", "shields"),
}
"""The display names of every compiled equipment template, keyed by catalog id."""

MAGIC = {
    "potion_of_healing": ("potion of healing", "potions of healing"),
    # An abbreviation keeps its capitals.
    "potion_of_esp": ("potion of ESP", "potions of ESP"),
    "ring_of_protection": ("ring of protection", "rings of protection"),
    "wand_of_cold": ("wand of cold", "wands of cold"),
    # The ending goes on the word that names the item, and "staff" has the plural the mundane staff has.
    "staff_of_healing": ("staff of healing", "staves of healing"),
    "bag_of_holding": ("bag of holding", "bags of holding"),
    "boots_of_speed": ("pair of boots of speed", "pairs of boots of speed"),
    "gauntlets_of_ogre_power": ("pair of gauntlets of ogre power", "pairs of gauntlets of ogre power"),
    "sword_plus_1": ("sword +1", "swords +1"),
    "sword_plus_1_plus_3_vs_undead": ("sword +1, +3 vs undead", "swords +1, +3 vs undead"),
    "sword_minus_1_cursed": ("sword -1, cursed", "swords -1, cursed"),
    "shield_plus_1": ("shield +1", "shields +1"),
    "armour_plus_1": ("suit of armour +1", "suits of armour +1"),
    "cursed_armour_ac_9": ("suit of cursed armour, AC 9 [10]", "suits of cursed armour, AC 9 [10]"),
    # Enchanted ammunition names one unit, which MagicItemInstance.quantity counts, not the generated count.
    "arrows_plus_1": ("arrow +1", "arrows +1"),
    "crossbow_bolts_plus_2": ("crossbow bolt +2", "crossbow bolts +2"),
    # A spell scroll keeps how many spells it holds, which its printed name gives in a parenthesis.
    "spell_scroll_1": ("spell scroll with 1 spell", "spell scrolls with 1 spell"),
    "spell_scroll_3": ("spell scroll with 3 spells", "spell scrolls with 3 spells"),
    # A treasure map's Roman numeral keeps its capitals.
    "treasure_map_ii": ("treasure map II", "treasure maps II"),
}
"""The display names of a sample of compiled magic item templates, keyed by catalog id."""


def _equipment() -> dict:
    catalog = load_equipment()
    return {
        template.id: template for template in (*catalog.weapons, *catalog.armour, *catalog.gear, *catalog.ammunition)
    }


def test_the_table_covers_every_compiled_equipment_template():
    """A template the compiler adds needs its names in `MUNDANE`, so this table stays the whole catalog."""
    assert set(_equipment()) == set(MUNDANE)


@XFAIL
@pytest.mark.parametrize(("item_id", "names"), sorted(MUNDANE.items()))
def test_each_compiled_equipment_template_has_its_display_names(item_id: str, names: tuple[str, str]):
    template = _equipment()[item_id]

    assert (template.display_name, template.display_name_plural) == names


@XFAIL
@pytest.mark.parametrize(("item_id", "names"), sorted(MAGIC.items()))
def test_a_compiled_magic_item_template_has_its_display_names(item_id: str, names: tuple[str, str]):
    template = load_magic_items().get(item_id)

    assert (template.display_name, template.display_name_plural) == names


@XFAIL
def test_every_compiled_magic_item_has_display_names_a_sentence_can_use():
    """Each one is filled, starts in lowercase, drops the printed parenthesis, and has a distinct plural."""
    for template in load_magic_items().items:
        singular, plural = template.display_name, template.display_name_plural
        assert singular and plural, template.id
        assert not singular[0].isupper(), (template.id, singular)
        assert "(" not in singular and "(" not in plural, (template.id, singular)
        assert singular != plural, (template.id, singular)


@pytest.mark.parametrize(
    ("template", "names"),
    [
        # A template written without the fields gets its name, and the regular ending on the last word.
        pytest.param(
            GearTemplate.model_validate({"id": "crypt_key", "name": "Crypt key", "cost_gp": 0}),
            ("Crypt key", "Crypt keys"),
            id="gear",
            marks=XFAIL,
        ),
        pytest.param(
            GearTemplate.model_validate({"id": "compass", "name": "Brass compass", "cost_gp": 0}),
            ("Brass compass", "Brass compasses"),
            id="gear_es",
            marks=XFAIL,
        ),
        pytest.param(
            AmmunitionTemplate.model_validate({"id": "dart", "name": "Barbed dart", "cost_gp": 0}),
            ("Barbed dart", "Barbed darts"),
            id="ammunition",
            marks=XFAIL,
        ),
        pytest.param(
            WeaponTemplate.model_validate(
                {
                    "id": "blade",
                    "name": "Drowned blade",
                    "cost_gp": 0,
                    "weight_coins": 60,
                    "damage": "1d8",
                    "qualities": ("melee",),
                }
            ),
            ("Drowned blade", "Drowned blades"),
            id="weapon",
            marks=XFAIL,
        ),
        pytest.param(
            ArmourTemplate.model_validate(
                {
                    "id": "scale",
                    "name": "Saint's scale",
                    "cost_gp": 0,
                    "weight_coins": 300,
                    "ac": 6,
                    "ac_ascending": 13,
                    "category": "light",
                }
            ),
            ("Saint's scale", "Saint's scales"),
            id="armour",
            marks=XFAIL,
        ),
        pytest.param(
            MagicItemTemplate.model_validate({"id": "orb", "name": "Gleaming orb", "category": "misc"}),
            ("Gleaming orb", "Gleaming orbs"),
            id="magic_item",
            marks=XFAIL,
        ),
        # A consonant before the y: "ies".
        pytest.param(
            GearTemplate.model_validate({"id": "reliquary", "name": "Reliquary", "cost_gp": 0}),
            ("Reliquary", "Reliquaries"),
            id="gear_ies",
            marks=XFAIL,
        ),
        # A name written for one keeps a plural written for two or more. This holds before the chunk too.
        pytest.param(
            GearTemplate.model_validate(
                {
                    "id": "lamp_oil",
                    "name": "Lamp oil",
                    "cost_gp": 0,
                    "display_name": "jar of lamp oil",
                    "display_name_plural": "jars of lamp oil",
                }
            ),
            ("jar of lamp oil", "jars of lamp oil"),
            id="written",
        ),
        # A plural left out of a written name follows the written name.
        pytest.param(
            GearTemplate.model_validate(
                {"id": "idol", "name": "Idol of the drowned saint", "cost_gp": 0, "display_name": "jade idol"}
            ),
            ("jade idol", "jade idols"),
            id="written_singular",
            marks=XFAIL,
        ),
    ],
)
def test_a_template_you_write_without_the_fields_gets_them(template, names: tuple[str, str]):
    assert (template.display_name, template.display_name_plural) == names


@XFAIL
def test_a_bundled_items_display_names_survive_the_adventure_document():
    """An adventure document round-trips the names its author wrote, and one written without them still loads."""
    idol = GearTemplate.model_validate(
        {
            "id": "temple_idol",
            "name": "Idol of the drowned saint",
            "cost_gp": 0,
            "display_name": "idol of the drowned saint",
            "display_name_plural": "idols of the drowned saint",
        }
    )
    key = GearTemplate.model_validate({"id": "crypt_key", "name": "Crypt key", "cost_gp": 0})
    level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
    adventure = Adventure(
        name="A",
        town=TownSpec(name="Threshold"),
        dungeons=(DungeonSpec(id="crypt", name="Crypt", levels=(level,)),),
        items=(idol, key),
    )

    document = adventure.model_dump(mode="json")
    for item in document["items"]:
        if item["id"] == "crypt_key":
            del item["display_name"], item["display_name_plural"]
    loaded = Adventure.model_validate(document)

    names = {item.id: (item.display_name, item.display_name_plural) for item in loaded.items}
    assert names == {
        "temple_idol": ("idol of the drowned saint", "idols of the drowned saint"),
        "crypt_key": ("Crypt key", "Crypt keys"),
    }
