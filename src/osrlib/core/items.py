"""Equipment, inventories, magic items, identification, and curses.

This module takes the two item catalogs that
[`load_equipment`][osrlib.data.load_equipment] and
[`load_magic_items`][osrlib.data.load_magic_items] return, and turns them into what a
character carries. Start at [`Inventory`][osrlib.core.items.Inventory], the container
for one character's items, coins, and equipped slots. Fill it with
[`purchase`][osrlib.core.items.purchase] and
[`equip`][osrlib.core.items.equip]. Read it back with
[`movement_rate_feet`][osrlib.core.items.movement_rate_feet] for the exploration
movement rate and [`equipped_item_modifiers`][osrlib.core.items.equipped_item_modifiers]
for the stat bonuses worn magic items grant. Every character built by
[`create_character`][osrlib.core.character.create_character] owns one inventory, and
combat resolution in [`osrlib.core.combat`][osrlib.core.combat] reads the templates
here to score an attack.

Under a running game you drive all of this through commands
([`PurchaseEquipment`][osrlib.crawl.commands.PurchaseEquipment],
[`EquipItem`][osrlib.crawl.commands.EquipItem],
[`UnequipItem`][osrlib.crawl.commands.UnequipItem]), which validate, emit events, and
record the change in the save. Call the functions here directly when you are using the
rules without a session.

The catalogs are frozen and shared. Play never mutates a template. It spawns an owned
instance from one instead: [`ItemInstance`][osrlib.core.items.ItemInstance] for mundane
equipment, [`MagicItemInstance`][osrlib.core.items.MagicItemInstance] for magic items.
A magic item instance starts unidentified, and even once identified may still hide a
curse: a revealed cursed item sticks to its bearer until *remove curse*.
[`validate_equip`][osrlib.core.items.validate_equip] and
[`validate_unequip`][osrlib.core.items.validate_unequip] enforce what a class may wear
or wield: armour and weapon policies, the two-ring cap, and the conflict between a
two-handed weapon and a shield. Both return structured rejections rather than raising.

Torch, holy water, and burning oil appear on both the SRD's weapon table and its gear
list. osrlib reads each as one physical item, not two: they compile as gear with an
embedded combat facet ([`CombatFacet`][osrlib.core.items.CombatFacet]), the weapons
list contains the pure weapons, and no item has two ids. Class weapon policies govern the
weapons list only, so a cleric may use holy water and a magic-user may throw oil or
swing a torch, as a documented adaptation (see the [adaptations
register](https://mmacy.github.io/osrlib-python/adaptations/)).

All weights are in coins, the SRD's unit of encumbrance at ten coins to the pound.
Coins themselves weigh 1 each. The maximum load rule always applies, not only under
detailed encumbrance: tracked weight above
[`MAX_LOAD_COINS`][osrlib.core.items.MAX_LOAD_COINS] means the character cannot move,
under both tracking modes. How much an inventory contains is never capped.

Typical usage:

```python
from osrlib.core.items import Inventory, equip, movement_rate_feet, purchase
from osrlib.core.ruleset import Ruleset
from osrlib.data import load_classes, load_equipment

catalog = load_equipment()
fighter = load_classes().get("fighter")

inventory = Inventory()
inventory.purse.gp = 100
plate = purchase(inventory, catalog.get("plate_mail"))
sword = purchase(inventory, catalog.get("sword"))
torches = purchase(inventory, catalog.get("torch"))
equip(inventory, fighter, plate)
equip(inventory, fighter, sword)

print(torches.quantity, inventory.purse.gp, movement_rate_feet(inventory, Ruleset()))
# 6 29 60
```
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from osrlib.core.classes import ArmourPolicyKind, ClassDefinition, WeaponPolicyKind
from osrlib.core.dice import parse
from osrlib.core.effects import ModifierSpec
from osrlib.core.rng import RngStream
from osrlib.core.ruleset import EncumbranceMode, Ruleset
from osrlib.core.treasure import MagicItemType, TreasureEntry
from osrlib.core.validation import Rejection

if TYPE_CHECKING:
    from osrlib.core.character import Character

__all__ = [
    "AmmunitionTemplate",
    "AnyInstance",
    "ArmourCategory",
    "ArmourTemplate",
    "ArmourTypeRow",
    "BASE_MOVEMENT_FEET",
    "COIN_VALUES_CP",
    "CoinPurse",
    "Coins",
    "CombatFacet",
    "EquipmentCatalog",
    "GearTemplate",
    "GeneratedTreasure",
    "Inventory",
    "ItemInstance",
    "ItemTemplate",
    "MAX_LOAD_COINS",
    "MAX_RINGS_WORN",
    "MISC_GEAR_WEIGHT_COINS",
    "MagicArmourTypeTable",
    "MagicItemCatalog",
    "MagicItemCategory",
    "MagicItemEffect",
    "MagicItemInstance",
    "MagicItemTemplate",
    "MagicSubTable",
    "MagicSubTableRow",
    "Material",
    "MissileRanges",
    "RangeBand",
    "ScrollCurse",
    "ScrollSpellLevelRow",
    "ScrollSpellLevelTable",
    "SentientSwordTables",
    "SwordCommunicationRow",
    "SwordControlResult",
    "SwordPower",
    "SwordPowersRow",
    "SwordSentience",
    "SwordTableBand",
    "TreasureWeight",
    "UsableBy",
    "ValuableInstance",
    "VersusBonus",
    "WeaponQuality",
    "WeaponTemplate",
    "encounter_movement_rate",
    "equip",
    "equipment_weight_coins",
    "equipped_item_modifiers",
    "magic_item_template",
    "movement_rate_feet",
    "purchase",
    "sword_control_check",
    "tracked_weight_coins",
    "treasure_weight_coins",
    "unequip",
    "usable_by_class",
    "validate_equip",
    "validate_purchase",
    "validate_unequip",
]

MAX_RINGS_WORN = 2
"""How many magic rings a character can wear at once: one on each hand.

[`validate_equip`][osrlib.core.items.validate_equip] rejects a third ring with
`items.ring.hands_full` rather than letting it on, because in the tabletop rules a
third ring makes none of them function. Nothing reads this constant at attack or
effect time, so changing it here would let a third ring be worn without granting it
any behavior. Apply your own cap before you call [`equip`][osrlib.core.items.equip] if
you want more ring slots.
"""

MAX_LOAD_COINS = 1600
"""The most a character can carry in coins of weight before movement drops to 0.

Ten coins weigh a pound, so this is 160 pounds. Every weight this module reports is in
this unit. [`movement_rate_feet`][osrlib.core.items.movement_rate_feet] returns 0 above
this figure under both tracking modes, basic and detailed, and the detailed mode's
slowest band ends here. Nothing in the library stops you from putting more in an
[`Inventory`][osrlib.core.items.Inventory], but the load then shows up as a movement rate
of 0.
"""

BASE_MOVEMENT_FEET = 120
"""The unencumbered exploration movement rate in feet per turn, printed as 120' (40').

This is what [`movement_rate_feet`][osrlib.core.items.movement_rate_feet] returns for a
character carrying nothing that counts, and always what it returns when the ruleset
tracks no encumbrance at all. The parenthesized 40' is the encounter rate, a third of
the base. [`encounter_movement_rate`][osrlib.core.items.encounter_movement_rate]
computes it.
"""

MISC_GEAR_WEIGHT_COINS = 80
"""The flat weight in coins that any amount of miscellaneous gear adds under detailed encumbrance.

The SRD prices weapons and armour individually but gives adventuring gear no per-item
weights, so [`equipment_weight_coins`][osrlib.core.items.equipment_weight_coins] adds
this figure once when a character carries any gear at all, and nothing more however
much gear that is.
"""

COIN_VALUES_CP = {"pp": 500, "gp": 100, "ep": 50, "sp": 10, "cp": 1}
"""What one coin of each denomination is worth in copper pieces.

The keys are the denomination names the purse and the treasure tables use (`pp`, `gp`,
`ep`, `sp`, `cp`). [`CoinPurse`][osrlib.core.items.CoinPurse] and
[`Coins`][osrlib.core.items.Coins] convert with it, and
[`CoinPurse.spend`][osrlib.core.items.CoinPurse.spend] pays and makes change in these
values. Copper is the exact unit for all coin arithmetic, so that mixed purses convert
without rounding. Gold is the unit of the experience award, at 1 gp to 1 XP.
"""


class WeaponQuality(StrEnum):
    """What a weapon can do in combat, as the SRD's weapon table prints it.

    Every [`WeaponTemplate`][osrlib.core.items.WeaponTemplate] and every gear
    [`CombatFacet`][osrlib.core.items.CombatFacet] has a tuple of these, and attack
    resolution in [`osrlib.core.combat`][osrlib.core.combat] reads them: they are what makes
    a bow behave differently from a mace. You never set them yourself for shipped equipment. You do
    choose them when an adventure bundles a weapon of its own.

    The wire values are the lowercase names below. They serialize into the compiled
    equipment data and into saves, so changing one is a `schema_version` bump.
    """

    BLUNT = "blunt"
    """A crushing weapon rather than an edged one. Only an edged melee weapon kills a sleeping target outright with a
    single hit.
    """
    BRACE = "brace"
    """Damage doubles when the wielder sets the weapon against a charging enemy."""
    CHARGE = "charge"
    """Damage doubles when the wielder charges with it."""
    MELEE = "melee"
    """Usable hand to hand, within melee reach."""
    MISSILE = "missile"
    """Usable at range. A template with this quality also has [`MissileRanges`][osrlib.core.items.MissileRanges], and
    the range band sets the attack modifier.
    """
    RELOAD = "reload"
    """Cannot fire two rounds running. The shot is rejected only when the `weapon_reload` flag of
    [`Ruleset`][osrlib.core.ruleset.Ruleset] is on.
    """
    SLOW = "slow"
    """The wielder always acts after everyone not using a slow weapon, whatever initiative said."""
    SPLASH = "splash"
    """Thrown to burst on the target, so it damages again the following round unless the target douses it. Holy water
    and burning oil have this quality.
    """
    TWO_HANDED = "two_handed"
    """Occupies both hands, so it cannot be wielded with a shield equipped."""


class Material(StrEnum):
    """What a weapon or piece of ammunition is made of, where the rules care.

    Some monsters are hurt only by silver or magical weapons, and this is how a mundane
    weapon claims the silver exemption: the damage pipeline in
    [`osrlib.core.combat`][osrlib.core.combat] reads it when it checks a target's
    immunities. Everything else is `STANDARD`, the default on
    [`WeaponTemplate`][osrlib.core.items.WeaponTemplate] and
    [`AmmunitionTemplate`][osrlib.core.items.AmmunitionTemplate]. The shipped catalog uses
    `SILVER` for silver-tipped arrows.

    The wire values are `"standard"` and `"silver"`, serialized into the compiled equipment
    data. Changing them is a `schema_version` bump.
    """

    STANDARD = "standard"
    """Ordinary steel, wood, or stone. No immunity exemption."""
    SILVER = "silver"
    """Silver or silver-tipped, so it harms a monster that only silver or magic can hurt."""


class ArmourCategory(StrEnum):
    """How bulky a suit of body armour is, for the basic encumbrance rates.

    Basic encumbrance sets a character's movement rate from what they wear rather than from
    what they weigh, and this is the column it looks up:
    [`movement_rate_feet`][osrlib.core.items.movement_rate_feet] reads the category of the
    armour in the worn slot. Wearing nothing is the absence of a category, not a value here,
    so an unarmoured character has no `ArmourCategory` at all. Enchanted armour moves like
    the mundane armour it is made from, since enchantment lightens a suit without making it
    less bulky.

    The wire values are `"light"` and `"heavy"`, serialized into the compiled equipment
    data. Changing them is a `schema_version` bump.
    """

    LIGHT = "light"
    """Leather. 90' unencumbered, 60' carrying treasure."""
    HEAVY = "heavy"
    """Chainmail and plate mail. 60' unencumbered, 30' carrying treasure."""


class RangeBand(BaseModel):
    """One missile range band in feet, as the SRD prints it (`5'–80'`).

    Three of these make up a weapon's [`MissileRanges`][osrlib.core.items.MissileRanges],
    and the band a shot falls into sets its attack modifier. Bands come from the
    compiled equipment data ([`load_equipment`][osrlib.data.load_equipment]). Construct one
    only when an adventure bundles a missile weapon of its own. Both bounds are inclusive,
    and a shot past the long band's maximum cannot be attempted at all.
    """

    model_config = ConfigDict(frozen=True)

    min_feet: int = Field(ge=0)
    """The band's nearest distance in feet, inclusive."""
    max_feet: int = Field(ge=0)
    """The band's farthest distance in feet, inclusive. Never less than `min_feet`. A reversed pair is rejected at load.
    """

    @model_validator(mode="after")
    def _band_must_be_ordered(self) -> RangeBand:
        if self.min_feet > self.max_feet:
            raise ValueError(f"range band minimum {self.min_feet} exceeds maximum {self.max_feet}")
        return self


class MissileRanges(BaseModel):
    """A missile weapon's three range bands, near to far.

    Attack resolution measures the distance to the target, finds the band it falls in, and
    applies that band's modifier: +1 at short range, nothing at medium, −1 at long. Beyond
    the long band the shot is out of range. A
    [`WeaponTemplate`][osrlib.core.items.WeaponTemplate] or
    [`CombatFacet`][osrlib.core.items.CombatFacet] has one of these exactly when it has
    the `MISSILE` quality of
    [`WeaponQuality`][osrlib.core.items.WeaponQuality]. The two are validated together at
    load.
    """

    model_config = ConfigDict(frozen=True)

    short: RangeBand
    """The nearest band, worth +1 to hit."""
    medium: RangeBand
    """The middle band, with no attack modifier."""
    long: RangeBand
    """The farthest band, worth −1 to hit, and the limit of the weapon's range."""


class WeaponTemplate(BaseModel):
    """A mundane weapon, from the SRD's weapon table.

    One of the four kinds of equipment template. Get one from the shipped catalog with
    [`EquipmentCatalog.get`][osrlib.core.items.EquipmentCatalog.get], then buy it with
    [`purchase`][osrlib.core.items.purchase], which spawns the owned
    [`ItemInstance`][osrlib.core.items.ItemInstance] a character actually carries.
    Templates are frozen and shared: never mutate one, and construct one yourself only to
    bundle a weapon of your own in an
    [`Adventure`][osrlib.crawl.adventure.Adventure].
    """

    model_config = ConfigDict(frozen=True)

    item_type: Literal["weapon"] = "weapon"
    """Always `"weapon"`. It is what tells the four template kinds apart when they are stored or loaded together."""
    id: str
    """The catalog id, for example `"sword"`. Unique across every equipment list. See [the equipment id
    index][equipment-index].
    """
    name: str
    """The display name, for example `"Sword"`."""
    cost_gp: int = Field(ge=0)
    """The listed price in gold pieces, for one weapon."""
    weight_coins: int = Field(ge=0)
    """The weight in coins. For a missile weapon this already includes its ammunition and quiver, which is why
    ammunition itself weighs nothing.
    """
    damage: str
    """The damage the weapon deals, as a dice expression, for example `"1d8"`. Ignored when the `variable_weapon_damage`
    flag of [`Ruleset`][osrlib.core.ruleset.Ruleset] is off, which makes every weapon deal 1d6.
    """
    qualities: tuple[WeaponQuality, ...]
    """What the weapon can do. See [`WeaponQuality`][osrlib.core.items.WeaponQuality]."""
    missile_ranges: MissileRanges | None = None
    """The three range bands, present exactly when `qualities` includes the missile quality."""
    material: Material = Material.STANDARD
    """What it is made of, for the silver immunity exemption. Standard unless the weapon is silvered."""
    overrides_applied: tuple[str, ...] = ()
    """Field paths a compiler override corrected when this row was compiled from the SRD. Provenance for the generated
    data. Nothing in play reads it.
    """

    @field_validator("damage")
    @classmethod
    def _damage_must_parse(cls, value: str) -> str:
        parse(value)
        return value

    @model_validator(mode="after")
    def _missile_quality_needs_ranges(self) -> WeaponTemplate:
        has_quality = WeaponQuality.MISSILE in self.qualities
        if has_quality != (self.missile_ranges is not None):
            raise ValueError("missile ranges are present exactly when the missile quality is")
        return self


class ArmourTemplate(BaseModel):
    """A suit of body armour or the shield, from the SRD's armour table.

    Body armour sets a wearer's armour class outright and a shield adds a bonus to it, so
    one of the two field groups is filled and the other is empty: body armour has `ac`,
    `ac_ascending`, and `category`, while the shield has `ac_bonus` alone. Ask
    [`is_shield`][osrlib.core.items.ArmourTemplate.is_shield] which kind you have rather
    than testing the fields. Get one from
    [`EquipmentCatalog.get`][osrlib.core.items.EquipmentCatalog.get], buy it with
    [`purchase`][osrlib.core.items.purchase], and put it on with
    [`equip`][osrlib.core.items.equip], which routes body armour to the worn slot and the
    shield to the shield slot.

    Both armour class formats are here because the tabletop rules print both: the
    descending scale, where lower is better and unarmoured is 9, and the ascending scale in
    brackets, where higher is better and unarmoured is 10. Which one a game shows its
    players is the game's choice. The rules resolve identically either way.
    """

    model_config = ConfigDict(frozen=True)

    item_type: Literal["armour"] = "armour"
    """Always `"armour"`."""
    id: str
    """The catalog id, for example `"plate_mail"`. See [the equipment id index][equipment-index]."""
    name: str
    """The display name."""
    cost_gp: int = Field(ge=0)
    """The listed price in gold pieces."""
    weight_coins: int = Field(ge=0)
    """The weight in coins. Enchanted armour weighs half this."""
    ac: int | None = None
    """Body armour's armour class on the descending scale. `None` on the shield."""
    ac_ascending: int | None = None
    """The same protection on the ascending scale. `None` on the shield."""
    ac_bonus: int | None = None
    """The shield's bonus, which improves the wearer's armour class by 1 on either scale. `None` on body armour."""
    category: ArmourCategory | None = None
    """How bulky the suit is, for the basic encumbrance movement rates. See
    [`ArmourCategory`][osrlib.core.items.ArmourCategory]. `None` on the shield.
    """
    overrides_applied: tuple[str, ...] = ()
    """Field paths a compiler override corrected when this row was compiled from the SRD."""

    @model_validator(mode="after")
    def _body_armour_or_shield(self) -> ArmourTemplate:
        body_fields = (self.ac is not None, self.ac_ascending is not None, self.category is not None)
        if self.ac_bonus is not None:
            if any(body_fields):
                raise ValueError("a shield has an AC bonus only, not base AC values or a category")
        elif not all(body_fields):
            raise ValueError("body armour needs descending AC, ascending AC, and a basic-encumbrance category")
        return self

    @property
    def is_shield(self) -> bool:
        """Whether this row is the shield rather than a suit of body armour.

        Read it instead of testing the armour class fields yourself: the shield has an
        armour class bonus and body armour has base values, and
        [`equip`][osrlib.core.items.equip] sends the two to different slots.

        Returns:
            True for the shield, False for body armour.
        """
        return self.ac_bonus is not None


class CombatFacet(BaseModel):
    """The combat statistics of a piece of gear that can also be used as a weapon.

    Torch, holy water, and burning oil are printed on both the SRD's weapon table and its
    gear list. osrlib compiles each as one gear item whose `combat` field contains this facet,
    so the item has a single id and a single weight. Attack resolution reads the facet
    exactly as it reads a [`WeaponTemplate`][osrlib.core.items.WeaponTemplate], and class
    weapon policies do not apply to it: a cleric may throw holy water and a magic-user may
    swing a torch. That exemption is a documented adaptation (see the [adaptations
    register](https://mmacy.github.io/osrlib-python/adaptations/)).
    """

    model_config = ConfigDict(frozen=True)

    damage: str
    """The damage dealt, as a dice expression."""
    qualities: tuple[WeaponQuality, ...]
    """What the item can do when used as a weapon. See [`WeaponQuality`][osrlib.core.items.WeaponQuality]. Holy water
    and burning oil have the splash quality, which is what makes them burn on for a second round.
    """
    missile_ranges: MissileRanges | None = None
    """The three range bands, present exactly when `qualities` includes the missile quality."""

    @field_validator("damage")
    @classmethod
    def _damage_must_parse(cls, value: str) -> str:
        parse(value)
        return value


class GearTemplate(BaseModel):
    """A piece of adventuring gear: a torch, a rope, a backpack, a flask of oil.

    Gear is what everything that is neither weapon, armour, nor ammunition compiles to. Get
    one from [`EquipmentCatalog.get`][osrlib.core.items.EquipmentCatalog.get] and buy it
    with [`purchase`][osrlib.core.items.purchase]. Gear sells in lots: one purchase at the
    listed price delivers `lot_size` units, so buying torches once costs 1 gp and yields
    six torches in one [`ItemInstance`][osrlib.core.items.ItemInstance].

    Most gear cannot be equipped. The three items with a `combat` facet can be, and are the
    only gear [`equip`][osrlib.core.items.equip] accepts. Gear has no per-item weight in the
    SRD, so detailed encumbrance charges a flat
    [`MISC_GEAR_WEIGHT_COINS`][osrlib.core.items.MISC_GEAR_WEIGHT_COINS] once for carrying
    any of it.
    """

    model_config = ConfigDict(frozen=True)

    item_type: Literal["gear"] = "gear"
    """Always `"gear"`."""
    id: str
    """The catalog id, for example `"torch"`. See [the equipment id index][equipment-index]."""
    name: str
    """The display name as the price list prints it, which names the lot size for gear sold in lots:
    `"Torches (6)"`, `"Iron spikes (12)"`.
    """
    cost_gp: int = Field(ge=0)
    """The listed price in gold pieces, for one lot."""
    lot_size: int = Field(default=1, ge=1)
    """How many units one purchase at `cost_gp` delivers. 1 for gear sold singly."""
    capacity_coins: int | None = None
    """How much fits in the container, in coins of weight, where the SRD gives a figure (backpack, small sack, large
    sack). `None` for gear that contains nothing. Nothing in the library enforces the figure, so enforce container
    limits in your own game if you want them.
    """
    combat: CombatFacet | None = None
    """The combat statistics for the three items that are also weapons. See
    [`CombatFacet`][osrlib.core.items.CombatFacet]. `None` for everything else.
    """
    params: dict[str, int | str | bool] = {}
    """The exploration mechanics the SRD's gear table prints, keyed by name: a torch's `burn_turns` and
    `light_radius_feet`, the tinder box's `light_chance_in_six`, and so on. The dungeon-crawl procedures in
    [`osrlib.crawl.exploration`][osrlib.crawl.exploration] read these.
    """
    overrides_applied: tuple[str, ...] = ()
    """Field paths a compiler override corrected when this row was compiled from the SRD."""


class AmmunitionTemplate(BaseModel):
    """Ammunition for a missile weapon: arrows, quarrels, sling stones.

    Bought like gear, in lots: one purchase at the listed price delivers `lot_size`
    units. Ammunition never weighs anything, because the SRD folds the weight of the
    ammunition and its container into the missile weapon's own listed weight and gives the
    ammunition table no weight column. It is not equippable either: wield the bow, and the
    arrows go in the item list. Sling stones are free, which compiles to a cost of 0.
    """

    model_config = ConfigDict(frozen=True)

    item_type: Literal["ammunition"] = "ammunition"
    """Always `"ammunition"`."""
    id: str
    """The catalog id, for example `"arrows"`. See [the equipment id index][equipment-index]."""
    name: str
    """The display name as the price list prints it, which names the lot: `"Arrows (quiver of 20)"`."""
    cost_gp: int = Field(ge=0)
    """The listed price in gold pieces, for one lot. 0 for sling stones."""
    lot_size: int = Field(default=1, ge=1)
    """How many units one purchase delivers, for example 20 arrows."""
    weight_coins: int = Field(default=0, ge=0)
    """Always 0."""
    material: Material = Material.STANDARD
    """What the ammunition is made of, for the silver immunity exemption. See [`Material`][osrlib.core.items.Material].
    """
    overrides_applied: tuple[str, ...] = ()
    """Field paths a compiler override corrected when this row was compiled from the SRD."""


ItemTemplate = Annotated[
    WeaponTemplate | ArmourTemplate | GearTemplate | AmmunitionTemplate,
    Field(discriminator="item_type"),
]
"""Any one of the four mundane equipment templates, told apart by its `item_type` field.

Annotate a parameter or a field with this when it takes equipment of any kind:
[`purchase`][osrlib.core.items.purchase] and
[`validate_purchase`][osrlib.core.items.validate_purchase] do, and so does the `items`
bundle of [`Adventure`][osrlib.crawl.adventure.Adventure]. Because the union is
discriminated, pydantic reads a serialized item back as the right class without
guessing, and a `match` on `item_type` covers every case.

The members are [`WeaponTemplate`][osrlib.core.items.WeaponTemplate],
[`ArmourTemplate`][osrlib.core.items.ArmourTemplate],
[`GearTemplate`][osrlib.core.items.GearTemplate], and
[`AmmunitionTemplate`][osrlib.core.items.AmmunitionTemplate]. Only weapons, armour, and
ammunition have a weight. Only gear and ammunition have a lot size.
"""


class TreasureWeight(BaseModel):
    """What one unit of a kind of treasure weighs, from the SRD's encumbrance table.

    The rows price treasure the way the equipment lists price gear: `coin` and `gem` weigh
    1 each, `jewellery` 10, and each magic item kind the table names has its own figure.
    [`treasure_weight_coins`][osrlib.core.items.treasure_weight_coins] reads them to weigh a
    character's loot, and treasure generation stamps the gem and jewellery figures onto each
    [`ValuableInstance`][osrlib.core.items.ValuableInstance] it creates. The rows ship with
    the equipment catalog ([`load_equipment`][osrlib.data.load_equipment]) rather than the
    treasure tables, because the SRD prints them on its encumbrance page.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """What is being weighed: `"coin"`, `"gem"`, `"jewellery"`, or a magic item kind like `"potion"` or `"staff"`."""
    weight_coins: int = Field(ge=0)
    """The weight of one of them, in coins."""


class EquipmentCatalog(BaseModel):
    """The whole mundane equipment list: what a shop sells and what a character can own.

    Call [`load_equipment`][osrlib.data.load_equipment] to get the shipped catalog. It is
    frozen, cached, and shared, so hold onto the one you are given rather than loading it
    per lookup. Reach an item by id with
    [`get`][osrlib.core.items.EquipmentCatalog.get], or iterate a list when you are
    building a shop screen. An [`Adventure`][osrlib.crawl.adventure.Adventure] that bundles
    item templates of its own is given a catalog with those added.

    Ids are unique across the four equipment lists, and across the magic item catalog too,
    so an id names exactly one thing anywhere in the library.
    """

    model_config = ConfigDict(frozen=True)

    weapons: tuple[WeaponTemplate, ...]
    """Every weapon."""
    armour: tuple[ArmourTemplate, ...]
    """Every suit of body armour, plus the shield."""
    gear: tuple[GearTemplate, ...]
    """Every piece of adventuring gear."""
    ammunition: tuple[AmmunitionTemplate, ...]
    """Every kind of ammunition."""
    treasure_weights: tuple[TreasureWeight, ...]
    """What each kind of treasure weighs. See [`TreasureWeight`][osrlib.core.items.TreasureWeight]."""

    @model_validator(mode="after")
    def _ids_must_be_unique(self) -> EquipmentCatalog:
        ids = [template.id for template in (*self.weapons, *self.armour, *self.gear, *self.ammunition)]
        if len(set(ids)) != len(ids):
            raise ValueError("equipment ids must be unique across weapons, armour, gear, and ammunition")
        weight_ids = [row.id for row in self.treasure_weights]
        if len(set(weight_ids)) != len(weight_ids):
            raise ValueError("treasure weight ids must be unique")
        return self

    def get(self, item_id: str) -> WeaponTemplate | ArmourTemplate | GearTemplate | AmmunitionTemplate:
        """Return the template with `item_id`, whichever of the four lists contains it.

        Use this whenever you have an id and need the item: before
        [`purchase`][osrlib.core.items.purchase], when rendering what a character carries, or
        when resolving an id an adventure supplied. Lookup is a scan, so hoist it out of a hot
        loop if you are resolving many ids at once.

        Args:
            item_id: Any equipment id this catalog contains. For the shipped catalog
                ([`load_equipment`][osrlib.data.load_equipment]) that is an id from
                [the equipment id index][equipment-index], for example `"sword"` or `"torch"`. A
                catalog built for an adventure also answers the ids that adventure bundles,
                which no index documents.

        Returns:
            The template.

        Raises:
            ValueError: If no item has that id. The message names the id.

        Examples:
            ```python
            from osrlib.data import load_equipment

            catalog = load_equipment()
            torch = catalog.get("torch")
            print(torch.name, torch.cost_gp, torch.lot_size)
            # Torches (6) 1 6
            ```
        """
        for template in (*self.weapons, *self.armour, *self.gear, *self.ammunition):
            if template.id == item_id:
                return template
        raise ValueError(f"unknown item id {item_id!r}")


class MagicItemCategory(StrEnum):
    """What kind of magic item a template is, in the magic item catalog.

    Every [`MagicItemTemplate`][osrlib.core.items.MagicItemTemplate] has one. The category
    governs how the item is handled: what [`equip`][osrlib.core.items.equip] does with it,
    whether [`treasure_weight_coins`][osrlib.core.items.treasure_weight_coins] weighs it as
    treasure, and whether generation rolls sentience for it.

    These are the categories of the catalog, not the types of the random-generation table.
    The table's rod, staff, and wand row covers three categories here. See
    [`MagicItemType`][osrlib.core.treasure.MagicItemType] for the table's own types and
    [`MagicItemCatalog.sub_table`][osrlib.core.items.MagicItemCatalog.sub_table] for how one
    maps to the other.

    The wire values are the lowercase names below, serialized into the compiled magic item
    data and into saves. Changing one is a `schema_version` bump.
    """

    ARMOUR = "armour"
    """Enchanted armour and shields. Worn in the armour or shield slot."""
    MISC = "misc"
    """Everything with no other home: cloaks, boots, bags, crystal balls."""
    POTION = "potion"
    """Drunk once, then gone. Not equippable."""
    RING = "ring"
    """Worn, and capped at [`MAX_RINGS_WORN`][osrlib.core.items.MAX_RINGS_WORN]."""
    ROD = "rod"
    """Rods. Wielded, and charged at creation like staves and wands."""
    STAFF = "staff"
    """Staves. Wielded, and charged at creation like rods and wands."""
    WAND = "wand"
    """Wands. Wielded, charged at creation, and restricted to arcane casters."""
    SCROLL = "scroll"
    """Scrolls and treasure maps. Not equippable."""
    SWORD = "sword"
    """Enchanted swords, the only items that can be sentient."""
    WEAPON = "weapon"
    """Every other enchanted weapon."""


class VersusBonus(BaseModel):
    """A magic weapon's bonus against particular enemies, as in `+2 vs Lycanthropes`.

    When the target matches, this bonus replaces the item's ordinary attack and damage
    bonus rather than adding to it. Attack resolution reads the clause off the template. You
    read it to show a player what a weapon is good against.

    Targets resolve structurally rather than by matching the printed label against a
    monster's name: `categories` names tags a monster template has, like `undead` or
    `enchanted`, and `template_ids` names compiled monster ids from
    [`load_monsters`][osrlib.data.load_monsters]. A clause matches a target whose template
    has any of the listed tags or ids. Characters have no monster template, so a clause never
    matches a character.
    """

    model_config = ConfigDict(frozen=True)

    label: str = Field(min_length=1)
    """The clause as the item's page prints it, for example `"+2 vs Lycanthropes"`. Show this to players. Do not parse
    it.
    """
    bonus: int
    """The attack and damage bonus that applies against a matching target, replacing the item's base bonus."""
    categories: tuple[str, ...] = ()
    """Monster category tags that match, for example `("undead",)`."""
    template_ids: tuple[str, ...] = ()
    """Monster template ids that match. See [the monster id index][monsters-index]. At least one of `categories` and
    `template_ids` is non-empty.
    """

    @model_validator(mode="after")
    def _targets_must_resolve(self) -> VersusBonus:
        if not self.categories and not self.template_ids:
            raise ValueError(f"versus clause {self.label!r} resolves no categories or template ids")
        return self


class UsableBy(BaseModel):
    """Which characters a magic item works for.

    [`usable_by_class`][osrlib.core.items.usable_by_class] answers the question this model
    poses, and [`validate_equip`][osrlib.core.items.validate_equip] applies it to devices
    and miscellaneous items, rejecting with `items.equip.not_usable`.

    Enchanted swords, weapons, and armour stay at the default `all`: their pages print "per
    normal class restrictions", and those restrictions are the class's own armour and weapon
    policies, applied to the mundane item underneath rather than here.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["all", "classes", "caster"] = "all"
    """`"all"` for anything a character can use, `"classes"` to restrict to named classes, `"caster"` to restrict to
    spell casters of a kind.
    """
    class_ids: tuple[str, ...] = ()
    """The classes that may use it, when `kind` is `"classes"`, as ids from [`load_classes`][osrlib.data.load_classes].
    See [the class id index][classes-index]. Empty otherwise.
    """
    caster: Literal["arcane", "divine", "any"] | None = None
    """Which kind of caster may use it, when `kind` is `"caster"`: `"arcane"` (magic-users and elves), `"divine"`
    (clerics), or `"any"`. `None` otherwise. Wands are arcane-only. Each staff follows its own page.
    """

    @model_validator(mode="after")
    def _shape_must_match_kind(self) -> UsableBy:
        if self.kind == "classes" and not self.class_ids:
            raise ValueError("a 'classes' usability names at least one class id")
        if (self.kind == "caster") != (self.caster is not None):
            raise ValueError("a caster kind is present exactly when the usability kind is 'caster'")
        return self


class MagicItemEffect(BaseModel):
    """The part of a magic item's behavior the engine resolves for you.

    An item whose page describes something the engine can execute has one of these. The rest
    have their page text in the template's `manual` field, for a game to narrate and
    adjudicate itself. `kind` names which behavior runs, and the behavior reads the fields
    it needs, so most fields are empty on most items.

    Read `kind` to decide what an item does. Read `modifiers` to show what a worn item
    grants, since
    [`equipped_item_modifiers`][osrlib.core.items.equipped_item_modifiers] returns exactly
    those for every equipped always-active item.

    The behaviors that ship are `worn_modifiers`, `potion`, `damage_area`, `condition_area`,
    `healing`, `save_or_die`, `on_hit_drain`, `striking`, `ward`, `regeneration`, and
    `light`.
    """

    model_config = ConfigDict(frozen=True)

    kind: str = Field(min_length=1)
    """Which behavior executes the item."""
    modifiers: tuple[ModifierSpec, ...] = ()
    """Stat modifiers the item grants. See [`ModifierSpec`][osrlib.core.effects.ModifierSpec]."""
    condition: str | None = None
    """The condition the item inflicts, for the behaviors that inflict one."""
    damage_dice: str | None = None
    """The damage it deals, as a dice expression."""
    heal_dice: str | None = None
    """The hit points it restores, as a dice expression."""
    element: str | None = None
    """The damage element, for example `"fire"`, for the target's immunity checks."""
    save_category: str | None = None
    """Which saving throw column the target rolls against."""
    save_on: Literal["negates", "half"] | None = None
    """What a successful save does: `"negates"` the effect entirely, or `"half"` the damage."""
    shape: str | None = None
    """The area's shape, for an area effect."""
    dimensions: dict[str, int] = {}
    """The area's measurements in feet, keyed by name."""
    range_feet: int | None = None
    """How far the effect reaches."""
    duration_unit: str | None = None
    """The unit the duration counts in, for example `"turns"` or `"rounds"`."""
    duration_amount: int | None = None
    """A fixed duration, in `duration_unit`s."""
    duration_dice: str | None = None
    """A rolled duration, as a dice expression, in `duration_unit`s."""
    params: dict[str, int | str | bool | tuple[int | str, ...]] = {}
    """Per-item scalars the behavior reads, keyed by name."""

    @field_validator("damage_dice", "heal_dice", "duration_dice")
    @classmethod
    def _dice_must_parse(cls, value: str | None) -> str | None:
        if value is not None:
            parse(value)
        return value


class ScrollCurse(BaseModel):
    """One of the cursed scroll's example curses.

    The SRD lists six example curses a cursed scroll can have and leaves the choice to the
    referee, and osrlib compiles them as rows so a game can roll or pick among them. Two are
    resolved by the engine and the rest are prose for a game to adjudicate.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """The curse id, for example `"energy_drain"`."""
    name: str
    """The display name."""
    prose: str
    """The curse as its page prints it. Show this to the referee or the player."""
    wired: bool = False
    """True when the engine resolves the curse itself: the energy drain, which takes a level, and the slow healing,
    which doubles the rest a day's natural healing takes and halves what healing magic restores. False means the prose
    is all there is, and the game decides what happens.
    """


class MagicItemTemplate(BaseModel):
    """A magic item, compiled from the generation tables and the per-item pages.

    Get one from [`MagicItemCatalog.get`][osrlib.core.items.MagicItemCatalog.get], or from
    [`magic_item_template`][osrlib.core.items.magic_item_template] when what you have is an
    instance. Templates are frozen and shared, and play uses
    [`MagicItemInstance`][osrlib.core.items.MagicItemInstance]s spawned from them by
    [`instantiate_magic_item`][osrlib.core.treasure.instantiate_magic_item], which is what
    rolls the details that differ from copy to copy.

    A cursed item's penalty is a negative bonus, so the arithmetic is the same as for a good
    item. The two cursed armours that fix armour class outright use `ac_set` instead.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """The catalog id, for example `"potion_of_healing"`. See [the magic item id index][magic-items-index]."""
    name: str
    """The display name. Show this only once the instance is identified."""
    category: MagicItemCategory
    """What kind of item it is. See [`MagicItemCategory`][osrlib.core.items.MagicItemCategory]."""
    base_item_id: str | None = None
    """The mundane equipment id the enchantment overlays, for an enchanted weapon, arrow, or shield. See [the equipment
    id index][equipment-index]. `None` for everything else, including generic enchanted armour, whose base is rolled per
    instance on the *Magic Armour Type* table.
    """
    attack_bonus: int = 0
    """What the item adds to an attack roll. Negative when cursed."""
    damage_bonus: int = 0
    """What it adds to damage. Negative when cursed."""
    ac_bonus: int = 0
    """What it adds to the wearer's armour class. Negative when cursed."""
    ac_set: int | None = None
    """The armour class the item forces, on the descending scale, for the cursed suits whose page prints `AC 9 [10]`.
    `None` on every other item.
    """
    ac_set_ascending: int | None = None
    """The same forced armour class on the ascending scale."""
    versus: tuple[VersusBonus, ...] = ()
    """Bonuses against particular enemies. See [`VersusBonus`][osrlib.core.items.VersusBonus]."""
    cursed: bool = False
    """True when the item is cursed. A cursed instance reveals itself in use, and a revealed cursed item cannot be taken
    off until *remove curse*.
    """
    charges_dice: str | None = None
    """How many charges are in a new copy, as a dice expression, for a rod, staff, or wand. `None` for an item with no
    charges.
    """
    quantity_dice: str | None = None
    """How many arrive at once, as a dice expression, for enchanted ammunition."""
    usable_by: UsableBy = UsableBy()
    """Who can use it. See [`UsableBy`][osrlib.core.items.UsableBy]."""
    always_active: bool = False
    """True when the item works while it is worn or wielded, with nothing to invoke."""
    effect: MagicItemEffect | None = None
    """What the engine resolves for the item. See [`MagicItemEffect`][osrlib.core.items.MagicItemEffect]. `None` for an
    item whose behavior is left to the game, which has `manual` prose instead.
    """
    params: dict[str, int | str | bool | tuple[int | str, ...]] = {}
    """Per-item scalars, keyed by name, for behaviors that read them."""
    manual: tuple[str, ...] = ()
    """The item's page text, for the parts a game adjudicates itself. Show these lines to the referee."""
    weight_coins: int = Field(default=0, ge=0)
    """The weight in coins. For an enchanted weapon or suit of armour this is the base item's weight, armour halved. For
    potions, scrolls, and devices it is the figure the treasure encumbrance rows give.
    """
    hoard_recipe: tuple[TreasureEntry, ...] = ()
    """The treasure a map leads to, as printed treasure entries. See
    [`TreasureEntry`][osrlib.core.treasure.TreasureEntry]. Empty on everything but a treasure map. Generate the hoard
    with [`generate_treasure_entries`][osrlib.core.treasure.generate_treasure_entries].
    """
    curses: tuple[ScrollCurse, ...] = ()
    """The cursed scroll's example curses. See [`ScrollCurse`][osrlib.core.items.ScrollCurse]. Empty on every other
    item.
    """
    overrides_applied: tuple[str, ...] = ()
    """Field paths a compiler override corrected when this item was compiled from the SRD."""

    @field_validator("charges_dice", "quantity_dice")
    @classmethod
    def _dice_must_parse(cls, value: str | None) -> str | None:
        if value is not None:
            parse(value)
        return value


class MagicSubTableRow(BaseModel):
    """One outcome of a magic item generation sub-table: the item it yields and the rolls that select it.

    The rules print two probability columns for every generation table, B for Basic play and
    X for Expert, and osrlib calls the choice between them the tier. The two columns index the
    same list of outcomes independently, so a row can sit in the X column without appearing
    in the B column at all.

    You rarely read a row yourself:
    [`generate_magic_item`][osrlib.core.treasure.generate_magic_item] rolls one and hands
    the result to
    [`instantiate_magic_item`][osrlib.core.treasure.instantiate_magic_item]. Read rows when
    you are showing a referee what a table can produce.
    """

    model_config = ConfigDict(frozen=True)

    item_ids: tuple[str, ...] = Field(min_length=1)
    """What the row yields. See [the magic item id index][magic-items-index]. One id usually, two for the armour rows
    that come with a shield.
    """
    basic_value: int | None = None
    """The single face of the sub-table's small die that selects this row in the B column. `None` when the printed cell
    is blank, which means the B column cannot produce this row.
    """
    expert_min: int = Field(ge=1, le=100)
    """The lowest d% roll that selects this row in the X column."""
    expert_max: int = Field(ge=1, le=100)
    """The highest, with a printed `00` read as 100. The X bands are contiguous and cover the whole d%."""
    params: dict[str, int | str | bool | tuple[int | str, ...]] = {}
    """Generation values this row overrides the template with, keyed by name: the quantity dice a printed band gives for
    arrows and bolts, the wish count for a ring of wishes.
    """


class MagicSubTable(BaseModel):
    """One magic item type's generation table: everything that type can produce, and the rolls that produce it.

    There is one of these per type of the master table. Reach the one you want with
    [`MagicItemCatalog.sub_table`][osrlib.core.items.MagicItemCatalog.sub_table]. Roll on it
    with [`row_for_basic`][osrlib.core.items.MagicSubTable.row_for_basic] or
    [`row_for_expert`][osrlib.core.items.MagicSubTable.row_for_expert], depending on the
    tier, or let [`generate_magic_item`][osrlib.core.treasure.generate_magic_item] do the
    whole job: pick the type, roll the sub-table, and instantiate the item.

    The rules print two columns, B for Basic play and X for Expert. The B column is a small
    die whose faces reach only part of the outcome list. The X column is a d% covering all
    of it.
    """

    model_config = ConfigDict(frozen=True)

    category: MagicItemType
    """The master-table type this table generates. See [`MagicItemType`][osrlib.core.treasure.MagicItemType]."""
    basic_die: int = Field(ge=2)
    """How many sides the B column's die has, for example 8 for a d8."""
    rows: tuple[MagicSubTableRow, ...] = Field(min_length=1)
    """The outcomes, in printed order. See [`MagicSubTableRow`][osrlib.core.items.MagicSubTableRow]."""

    @model_validator(mode="after")
    def _columns_must_cover_their_dice(self) -> MagicSubTable:
        expected = 1
        for row in self.rows:
            if row.expert_min != expected:
                raise ValueError(f"{self.category} expert bands must be contiguous from 01")
            expected = row.expert_max + 1
        if expected != 101:
            raise ValueError(f"{self.category} expert bands must cover the whole d%")
        basic_values = [row.basic_value for row in self.rows if row.basic_value is not None]
        if basic_values != list(range(1, self.basic_die + 1)):
            raise ValueError(f"{self.category} basic column must cover 1-{self.basic_die} in order")
        return self

    def row_for_basic(self, roll: int) -> MagicSubTableRow:
        """Return the row a Basic-tier roll of the table's small die selects.

        Call this when you are rolling a table by hand and the game is at the Basic tier. For
        the Expert tier call
        [`row_for_expert`][osrlib.core.items.MagicSubTable.row_for_expert]. Roll the die
        yourself, from the treasure stream, so the draw is part of the reproducible sequence.

        Args:
            roll: The die result, 1 through `basic_die`.

        Returns:
            The selected row.

        Raises:
            ValueError: If no row has that face, which includes every roll outside the
                die's range.

        Examples:
            ```python
            from osrlib.core.treasure import MagicItemType
            from osrlib.data import load_magic_items

            potions = load_magic_items().sub_table(MagicItemType.POTION)
            print(potions.basic_die, potions.row_for_basic(1).item_ids)
            # 8 ('potion_of_diminution',)
            ```
        """
        for row in self.rows:
            if row.basic_value == roll:
                return row
        raise ValueError(f"{self.category} basic roll must be 1-{self.basic_die}, got {roll}")

    def row_for_expert(self, roll: int) -> MagicSubTableRow:
        """Return the row an Expert-tier d% roll selects.

        The X column covers the whole d%, so every roll from 1 to 100 selects a row. For the
        Basic tier call [`row_for_basic`][osrlib.core.items.MagicSubTable.row_for_basic].

        Args:
            roll: The d% result, 1 to 100, with a rolled `00` passed as 100.

        Returns:
            The selected row.

        Raises:
            ValueError: If the roll is outside 1 to 100.
        """
        for row in self.rows:
            if row.expert_min <= roll <= row.expert_max:
                return row
        raise ValueError(f"{self.category} expert roll must be 1-100, got {roll}")


class ArmourTypeRow(BaseModel):
    """One band of the *Magic Armour Type* table: the d8 rolls that settle what a generated suit is made of."""

    model_config = ConfigDict(frozen=True)

    roll_min: int = Field(ge=1, le=8)
    """The lowest d8 result in this band."""
    roll_max: int = Field(ge=1, le=8)
    """The highest d8 result in this band."""
    base_item_id: str
    """The mundane armour the band yields, for example `"chainmail"`. See [the equipment id index][equipment-index]."""


class MagicArmourTypeTable(BaseModel):
    """The *Magic Armour Type* table: what a generated suit of `Armour +N` turns out to be made of.

    The generation tables produce enchanted armour without saying which armour, so
    [`instantiate_magic_item`][osrlib.core.treasure.instantiate_magic_item] rolls this d8 to
    settle it and records the answer on the instance's `base_item_id`. Roll it yourself with
    [`base_for_roll`][osrlib.core.items.MagicArmourTypeTable.base_for_roll] only when you
    are placing armour by hand and want the same distribution.
    """

    model_config = ConfigDict(frozen=True)

    rows: tuple[ArmourTypeRow, ...] = Field(min_length=1)
    """The bands, in order, covering the whole d8. See [`ArmourTypeRow`][osrlib.core.items.ArmourTypeRow]."""

    @model_validator(mode="after")
    def _rows_cover_the_d8(self) -> MagicArmourTypeTable:
        expected = 1
        for row in self.rows:
            if row.roll_min != expected:
                raise ValueError("armour type rows must be contiguous from 1")
            expected = row.roll_max + 1
        if expected != 9:
            raise ValueError("armour type rows must cover the whole d8")
        return self

    def base_for_roll(self, roll: int) -> str:
        """Return the mundane armour a d8 roll makes a generated suit out of.

        Args:
            roll: The d8 result, 1 to 8.

        Returns:
            The mundane armour id, one of `"leather"`, `"chainmail"`, or `"plate_mail"` in the
            shipped table. Look it up with
            [`EquipmentCatalog.get`][osrlib.core.items.EquipmentCatalog.get].

        Raises:
            ValueError: If the roll is outside 1 to 8.

        Examples:
            ```python
            from osrlib.data import load_magic_items

            table = load_magic_items().armour_type
            print(table.base_for_roll(1), table.base_for_roll(8))
            # leather plate_mail
            ```
        """
        for row in self.rows:
            if row.roll_min <= roll <= row.roll_max:
                return row.base_item_id
        raise ValueError(f"armour type roll must be 1-8, got {roll}")


class ScrollSpellLevelRow(BaseModel):
    """One row of the *Random Scroll Spell Level* table: the rolls that select a level, and the level they give.

    The B column here is bands of a d6 rather than single faces, and its bounds are `None`
    on the rows only the X column can reach.
    """

    model_config = ConfigDict(frozen=True)

    basic_min: int | None = None
    """The lowest d6 result in this row's B band, or `None` when the row is Expert-only."""
    basic_max: int | None = None
    """The highest d6 result in the row's B band, or `None` when the row is Expert-only."""
    expert_min: int = Field(ge=1, le=100)
    """The lowest d% result in this row's X band."""
    expert_max: int = Field(ge=1, le=100)
    """The highest d% result in the row's X band."""
    arcane_level: int = Field(ge=1, le=6)
    """The spell level this row gives a magic-user scroll."""
    divine_level: int = Field(ge=1, le=5)
    """The spell level it gives a cleric scroll. Clerics have no sixth-level spells, so the last row gives them a
    fifth-level one.
    """


class ScrollSpellLevelTable(BaseModel):
    """The *Random Scroll Spell Level* table: which spell level each spell on a generated scroll is.

    [`instantiate_magic_item`][osrlib.core.treasure.instantiate_magic_item] rolls this once
    per spell on a scroll, then picks a spell of that level from the scroll's list.
    Roll it yourself with
    [`level_for_basic`][osrlib.core.items.ScrollSpellLevelTable.level_for_basic] or
    [`level_for_expert`][osrlib.core.items.ScrollSpellLevelTable.level_for_expert] when you
    are writing a scroll by hand.
    """

    model_config = ConfigDict(frozen=True)

    rows: tuple[ScrollSpellLevelRow, ...] = Field(min_length=1)
    """The rows, in printed order. See [`ScrollSpellLevelRow`][osrlib.core.items.ScrollSpellLevelRow]."""

    @model_validator(mode="after")
    def _columns_cover_their_dice(self) -> ScrollSpellLevelTable:
        expected = 1
        for row in self.rows:
            if row.expert_min != expected:
                raise ValueError("scroll spell level expert bands must be contiguous from 01")
            expected = row.expert_max + 1
        if expected != 101:
            raise ValueError("scroll spell level expert bands must cover the whole d%")
        expected = 1
        for row in self.rows:
            if row.basic_min is None:
                continue
            if row.basic_min != expected:
                raise ValueError("scroll spell level basic bands must be contiguous from 1")
            expected = (row.basic_max or 0) + 1
        if expected != 7:
            raise ValueError("scroll spell level basic bands must cover the whole d6")
        return self

    def level_for_basic(self, roll: int, *, divine: bool) -> int:
        """Return the spell level a Basic-tier d6 roll gives.

        Args:
            roll: The d6 result, 1 to 6.
            divine: True for a cleric scroll, False for a magic-user scroll.

        Returns:
            The spell level. Pass it to
            [`SpellCatalog.by_list`][osrlib.core.spells.SpellCatalog.by_list] to get the
            spells you can choose among.

        Raises:
            ValueError: If no band covers the roll.

        Examples:
            ```python
            from osrlib.data import load_magic_items

            table = load_magic_items().scroll_spell_levels
            print(table.level_for_basic(1, divine=False), table.level_for_basic(6, divine=True))
            # 1 3
            ```
        """
        for row in self.rows:
            if row.basic_min is not None and row.basic_min <= roll <= (row.basic_max or 0):
                return row.divine_level if divine else row.arcane_level
        raise ValueError(f"scroll spell level basic roll must be 1-6, got {roll}")

    def level_for_expert(self, roll: int, *, divine: bool) -> int:
        """Return the spell level an Expert-tier d% roll gives.

        The X column reaches levels the B column cannot, which is what makes scrolls found in
        Expert play stronger.

        Args:
            roll: The d% result, 1 to 100.
            divine: True for a cleric scroll, False for a magic-user scroll.

        Returns:
            The spell level.

        Raises:
            ValueError: If the roll is outside 1 to 100.
        """
        for row in self.rows:
            if row.expert_min <= roll <= row.expert_max:
                return row.divine_level if divine else row.arcane_level
        raise ValueError(f"scroll spell level roll must be 1-100, got {roll}")


class SwordCommunicationRow(BaseModel):
    """How a sentient sword of a given intelligence talks, from the *Communication* table."""

    model_config = ConfigDict(frozen=True)

    int_score: int = Field(ge=7, le=12)
    """The sword's intelligence, 7 to 12."""
    reading: bool
    """True when the sword can read, which the brightest swords can."""
    communication: str
    """How it makes itself understood: `"empathy"` for a sword that only sends feelings, `"speech"` for one that talks.
    Only a speaking sword rolls languages.
    """


class SwordPowersRow(BaseModel):
    """How many powers a sentient sword of a given intelligence has, from the *Powers* table."""

    model_config = ConfigDict(frozen=True)

    int_score: int = Field(ge=7, le=12)
    """The sword's intelligence, 7 to 12."""
    sensory: int = Field(ge=0)
    """How many sensory powers it gets, the ones that detect things."""
    extraordinary: int = Field(ge=0)
    """How many extraordinary powers it gets, the ones that do things. Only the brightest swords have any."""


class SwordTableBand(BaseModel):
    """One band of a sentient sword roll table: either an outcome or an instruction to roll again.

    The sword tables all share this shape: a roll range and a result. The result is usually
    a value, like an alignment or the id of a power, and sometimes an instruction:
    `roll_twice` on the language and both power tables, `roll_thrice` on an extraordinary
    result of `00`, and `roll_extraordinary` on a high sensory roll, which trades the
    sensory power for an extraordinary one.
    [`instantiate_magic_item`][osrlib.core.treasure.instantiate_magic_item] resolves the
    instructions for you when it rolls up a sword. Read the bands yourself only to show a
    referee the table.
    """

    model_config = ConfigDict(frozen=True)

    roll_min: int = Field(ge=1)
    """The lowest roll in this band."""
    roll_max: int = Field(ge=1)
    """The highest roll in this band."""
    result: str = Field(min_length=1)
    """What the band yields: an alignment, a language count, a power id, or one of the instructions above."""


class SwordPower(BaseModel):
    """One power a sentient sword can have.

    Each power is text for a game to adjudicate. osrlib rolls which powers a sword has and
    leaves what they do to the referee. Look one up by id with
    [`SentientSwordTables.power`][osrlib.core.items.SentientSwordTables.power], and show
    `prose` to the referee.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """The power id, for example `"detect_magic"`. This is what a sword's
    [`SwordSentience`][osrlib.core.items.SwordSentience] records.
    """
    name: str
    """The display name."""
    prose: str
    """The power as its page prints it."""
    extraordinary: bool = False
    """True for an extraordinary power, False for a sensory one. The two are rolled on separate tables."""
    duplicates_allowed: bool = False
    """True when rolling the same power twice means something, so the roll stands instead of being re-rolled."""


class SentientSwordTables(BaseModel):
    """Every table that goes into rolling up a sentient sword.

    Generation runs these in the order the rules print them, and
    [`instantiate_magic_item`][osrlib.core.treasure.instantiate_magic_item] does it for you
    whenever it creates a sword: first the 1-in-20 check for a sword with a special purpose,
    which is always sentient at intelligence 12 and ego 12, otherwise the 30% check for
    ordinary sentience. Then intelligence on 1d6+6, communication, languages, alignment,
    powers, and ego on 1d12. The result is a
    [`SwordSentience`][osrlib.core.items.SwordSentience] on the instance.

    Read these tables directly when you are writing a sword by hand and want the printed
    odds.
    """

    model_config = ConfigDict(frozen=True)

    communication: tuple[SwordCommunicationRow, ...]
    """How a sword of each intelligence communicates. See
    [`SwordCommunicationRow`][osrlib.core.items.SwordCommunicationRow].
    """
    languages: tuple[SwordTableBand, ...]
    """How many languages a speaking sword knows."""
    alignment: tuple[SwordTableBand, ...]
    """The sword's alignment."""
    powers: tuple[SwordPowersRow, ...]
    """How many powers a sword of each intelligence has. See [`SwordPowersRow`][osrlib.core.items.SwordPowersRow]."""
    sensory_bands: tuple[SwordTableBand, ...]
    """Which sensory power a roll yields."""
    extraordinary_bands: tuple[SwordTableBand, ...]
    """Which extraordinary power a roll yields."""
    powers_catalog: tuple[SwordPower, ...]
    """Every power, with its text. See [`SwordPower`][osrlib.core.items.SwordPower]."""
    special_purposes: tuple[SwordTableBand, ...]
    """The purposes a special sword can be made for, like slaying a kind of creature."""
    special_purpose_prose: str = ""
    """The rule for the extra power a special sword brings to bear on its purpose. Show it to the referee."""
    alignment_touch_prose: str = ""
    """The rule for the damage a sword deals to a bearer of the wrong alignment, which is also the only way to learn its
    alignment. Show it to the referee. The engine does not apply it.
    """

    def power(self, power_id: str) -> SwordPower:
        """Return the power with `power_id`.

        Use it to turn the ids on a sword's
        [`SwordSentience`][osrlib.core.items.SwordSentience] into names and text you can show.

        Args:
            power_id: The power id, for example `"detect_magic"`, as recorded on a sword's
                sentience.

        Returns:
            The power.

        Raises:
            ValueError: If no power has that id.

        Examples:
            ```python
            from osrlib.data import load_magic_items

            tables = load_magic_items().sentient_swords
            print(tables.power("detect_magic").name)
            # Detect Magic
            ```
        """
        for power in self.powers_catalog:
            if power.id == power_id:
                return power
        raise ValueError(f"unknown sword power {power_id!r}")


class MagicItemCatalog(BaseModel):
    """The whole magic item list, with the tables that generate from it.

    Call [`load_magic_items`][osrlib.data.load_magic_items] to get the shipped catalog. It
    is frozen, cached, and shared. Reach an item by id with
    [`get`][osrlib.core.items.MagicItemCatalog.get], and a type's generation table with
    [`sub_table`][osrlib.core.items.MagicItemCatalog.sub_table]. Treasure generation in
    [`osrlib.core.treasure`][osrlib.core.treasure] loads this catalog itself, so you need it
    only to read items, not to generate them.

    Magic item ids never collide with equipment ids, so a single id names one thing across
    both catalogs. An adventure cannot bundle magic items of its own. It places the shipped
    ones.
    """

    model_config = ConfigDict(frozen=True)

    items: tuple[MagicItemTemplate, ...]
    """Every magic item template."""
    sub_tables: tuple[MagicSubTable, ...]
    """One generation table per master-table type. See [`MagicSubTable`][osrlib.core.items.MagicSubTable]."""
    armour_type: MagicArmourTypeTable
    """What a generated suit of enchanted armour is made of. See
    [`MagicArmourTypeTable`][osrlib.core.items.MagicArmourTypeTable].
    """
    scroll_spell_levels: ScrollSpellLevelTable
    """Which spell level each spell on a generated scroll is. See
    [`ScrollSpellLevelTable`][osrlib.core.items.ScrollSpellLevelTable].
    """
    sentient_swords: SentientSwordTables
    """The tables for rolling up a sentient sword. See [`SentientSwordTables`][osrlib.core.items.SentientSwordTables].
    """

    @model_validator(mode="after")
    def _ids_unique_and_rows_resolve(self) -> MagicItemCatalog:
        ids = [template.id for template in self.items]
        if len(set(ids)) != len(ids):
            raise ValueError("magic item ids must be unique")
        known = set(ids)
        for sub_table in self.sub_tables:
            for row in sub_table.rows:
                for item_id in row.item_ids:
                    if item_id not in known:
                        raise ValueError(f"{sub_table.category} row references unknown item {item_id!r}")
        return self

    def get(self, item_id: str) -> MagicItemTemplate:
        """Return the magic item template with `item_id`.

        Use it to turn an id into an item: the `template_id` on a
        [`MagicItemInstance`][osrlib.core.items.MagicItemInstance] (for which
        [`magic_item_template`][osrlib.core.items.magic_item_template] is the shorthand), an id
        an adventure places, or an id you are generating from. Lookup is a scan, so hoist it out
        of a hot loop.

        Args:
            item_id: A magic item id from
                [`load_magic_items`][osrlib.data.load_magic_items]. See
                [the magic item id index][magic-items-index], for example `"potion_of_healing"`.

        Returns:
            The template.

        Raises:
            ValueError: If no item has that id.

        Examples:
            ```python
            from osrlib.data import load_magic_items

            potion = load_magic_items().get("potion_of_healing")
            print(potion.name, potion.category, potion.weight_coins)
            # Potion of Healing potion 10
            ```
        """
        for template in self.items:
            if template.id == item_id:
                return template
        raise ValueError(f"unknown magic item id {item_id!r}")

    def sub_table(self, category: MagicItemType) -> MagicSubTable:
        """Return the generation table for one type of the master *Magic Item Type* table.

        Call it when you are rolling a type's table yourself. When you want a whole item rolled,
        call [`generate_magic_item`][osrlib.core.treasure.generate_magic_item] instead, which
        rolls the master table, this one, and the item's own details.

        The master table's rod, staff, and wand row covers three catalog categories, and asking
        for `ROD_STAFF_WAND` returns the one table that produces all three.

        Args:
            category: The master-table type. See
                [`MagicItemType`][osrlib.core.treasure.MagicItemType].

        Returns:
            The sub-table.

        Raises:
            ValueError: If no sub-table covers that type.
        """
        for sub_table in self.sub_tables:
            if sub_table.category is category:
                return sub_table
        raise ValueError(f"no sub-table for category {category!r}")


class SwordSentience(BaseModel):
    """What a sentient sword turned out to be: its mind, its alignment, and its powers.

    Rolled once when the sword is created and fixed from then on. It lives on the sword's
    [`MagicItemInstance`][osrlib.core.items.MagicItemInstance]. Most swords have none, and
    the field is `None` for those. Pass the sword to
    [`sword_control_check`][osrlib.core.items.sword_control_check] to find out whether it
    takes charge of its wielder.
    """

    model_config = ConfigDict(frozen=True)

    intelligence: int = Field(ge=7, le=12)
    """The sword's intelligence, 7 to 12. It sets how the sword communicates and how many powers it has."""
    ego: int = Field(ge=1, le=12)
    """The sword's ego, 1 to 12, or 12 for a sword of special purpose. Intelligence and ego together are what the sword
    brings to a contest of wills.
    """
    communication: str
    """How it makes itself understood: `"empathy"` or `"speech"`."""
    reading: bool
    """True when the sword can read."""
    alignment: str
    """The sword's alignment, as a lowercase name. A bearer of a different alignment takes damage for holding it, which
    the rules leave to the referee to apply.
    """
    languages: int = Field(default=0, ge=0)
    """How many languages a speaking sword knows. 0 for an empathic sword."""
    sensory_powers: tuple[str, ...] = ()
    """The ids of its detecting powers. Look them up with
    [`SentientSwordTables.power`][osrlib.core.items.SentientSwordTables.power].
    """
    extraordinary_powers: tuple[str, ...] = ()
    """The ids of its greater powers."""
    special_purpose: str | None = None
    """What the sword was made to do, for example `"chaotic_creatures"`, or `None` for a sword with no special purpose.
    """


class MagicItemInstance(BaseModel):
    """One magic item a character owns, with everything that differs from copy to copy.

    Templates are shared and frozen. This is the copy in play, and it is mutable. Treasure
    generation makes them
    ([`instantiate_magic_item`][osrlib.core.treasure.instantiate_magic_item] and
    [`generate_magic_item`][osrlib.core.treasure.generate_magic_item]), an
    [`Inventory`][osrlib.core.items.Inventory] contains them, and
    [`magic_item_template`][osrlib.core.items.magic_item_template] gets you back to the
    template behind one.

    An instance starts unidentified, and a player-facing view shows it as an unknown item
    until it is not. Identification happens in play: drinking the potion, swinging the
    sword, wearing the ring. A cursed item reveals its curse the same way, and once revealed
    it cannot be taken off until *remove curse*:
    [`unequip`][osrlib.core.items.unequip] rejects with `items.curse.stuck`.
    """

    model_config = ConfigDict(validate_assignment=True)

    instance_type: Literal["magic_item"] = "magic_item"
    """Always `"magic_item"`. It is what tells this apart from a mundane
    [`ItemInstance`][osrlib.core.items.ItemInstance] when both are stored in one list.
    """
    instance_id: str
    """This copy's own id, for example `"magic-item-0003"`, allocated by
    [`IdAllocator`][osrlib.core.monsters.IdAllocator]. Commands that act on a magic item name it by this, not by its
    template id.
    """
    template_id: str
    """Which item it is. See [the magic item id index][magic-items-index]."""
    charges_remaining: int | None = None
    """How many charges are left in a rod, staff, or wand, or `None` for an item that has no charges. Keep this out of
    the player's view: in the tabletop rules a charge count cannot be discovered.
    """
    quantity: int = Field(default=1, ge=0)
    """How many the stack contains, for enchanted ammunition. A stack at 0 is spent and no longer counts as carried."""
    identified: bool = False
    """True once the party knows what the item is. A player-facing view masks an unidentified item's name and
    properties.
    """
    cursed_revealed: bool = False
    """True once the curse has shown itself. From then on the item cannot be unequipped or given away until *remove
    curse*.
    """
    base_item_id: str | None = None
    """The mundane item underneath an enchanted weapon, arrow, or suit of armour. See [the equipment id
    index][equipment-index]. For generic enchanted armour this is what the *Magic Armour Type* roll settled on.
    """
    sentience: SwordSentience | None = None
    """The sword's mind, for a sentient sword. See [`SwordSentience`][osrlib.core.items.SwordSentience]. `None` on
    everything else.
    """
    state: dict[str, int | str | bool | tuple[int | str, ...]] = {}
    """What this copy records, keyed by name: the effects a worn item has attached, an energy-drain sword's remaining
    drains, the day a staff of healing last healed each target, the spells left on a scroll.
    """


class ItemInstance(BaseModel):
    """A stack of mundane items a character owns.

    Made by [`purchase`][osrlib.core.items.purchase], or constructed directly when you are
    giving a character something without charging for it, and carried in an
    [`Inventory`][osrlib.core.items.Inventory]. Unlike a magic item, a mundane instance has
    no id of its own: it is identified by its template, so two stacks of the same item are
    interchangeable.
    """

    model_config = ConfigDict(validate_assignment=True)

    instance_type: Literal["item"] = "item"
    """Always `"item"`."""
    template: ItemTemplate
    """The item itself, one of the four equipment templates. See [`ItemTemplate`][osrlib.core.items.ItemTemplate]. The
    whole template is embedded rather than referenced by id, so an instance of an item an adventure bundled stays
    readable without that adventure.
    """
    quantity: int = Field(default=1, ge=1)
    """How many units the stack contains, counting individual items rather than lots: buying one lot of torches gives
    you one instance of quantity 6.
    """


class Coins(BaseModel):
    """A fixed pile of coins: generated treasure, the contents of a chest, a pile dropped on the floor.

    Frozen, unlike the [`CoinPurse`][osrlib.core.items.CoinPurse] a character carries. This
    is what [`generate_treasure`][osrlib.core.treasure.generate_treasure] reports and what a
    dungeon feature contains until someone picks it up. Adding it to a character means adding
    each denomination to their purse.
    """

    model_config = ConfigDict(frozen=True)

    pp: int = Field(default=0, ge=0)
    """Platinum pieces, worth 5 gp each."""
    gp: int = Field(default=0, ge=0)
    """Gold pieces."""
    ep: int = Field(default=0, ge=0)
    """Electrum pieces, worth half a gold piece each."""
    sp: int = Field(default=0, ge=0)
    """Silver pieces, ten to the gold piece."""
    cp: int = Field(default=0, ge=0)
    """Copper pieces, a hundred to the gold piece."""

    @property
    def total_coins(self) -> int:
        """How many coins are in the pile, whatever they are worth.

        This is also its weight in coins, since every coin weighs 1 whatever its metal.

        Returns:
            The number of coins.
        """
        return self.pp + self.gp + self.ep + self.sp + self.cp

    @property
    def value_cp(self) -> int:
        """What the pile is worth in copper pieces.

        Copper is the exact unit for coin arithmetic: totalling a mixed pile in gold would lose
        the odd silver and copper to rounding.

        Returns:
            The value in copper pieces.
        """
        return sum(getattr(self, denomination) * value for denomination, value in COIN_VALUES_CP.items())

    @property
    def value_gp(self) -> int:
        """What the pile is worth in whole gold pieces, rounding down.

        This is the figure the experience award uses, at 1 gp to 1 XP.

        Returns:
            The value in gold pieces, with any fraction dropped.
        """
        return self.value_cp // 100


class ValuableInstance(BaseModel):
    """One gem or piece of jewellery a character carries.

    Treasure generation rolls the value once, when the piece is created, and it never
    changes: [`generate_treasure`][osrlib.core.treasure.generate_treasure] returns these
    alongside the coins. Selling is exact and immediate, because the tabletop rules price
    treasure to feed the experience economy. Add haggling or appraisal in your own game if
    you want them.
    """

    model_config = ConfigDict(validate_assignment=True)

    instance_type: Literal["valuable"] = "valuable"
    """Always `"valuable"`."""
    instance_id: str
    """This piece's own id, for example `"valuable-0001"`, allocated by
    [`IdAllocator`][osrlib.core.monsters.IdAllocator].
    """
    kind: Literal["gem", "jewellery"]
    """`"gem"` or `"jewellery"`."""
    name: str = ""
    """A display name for the piece. Generation sets a plain one. An adventure that places treasure by hand can name it
    whatever it likes.
    """
    value_gp: int = Field(ge=0)
    """What it is worth in gold pieces, and what it pays when sold."""
    weight_coins: int = Field(default=0, ge=0)
    """The weight in coins, taken from the treasure encumbrance rows when the piece was generated. See
    [`TreasureWeight`][osrlib.core.items.TreasureWeight].
    """


class GeneratedTreasure(BaseModel):
    """Everything one roll of a treasure table produced.

    What every generation entry point in
    [`osrlib.core.treasure`][osrlib.core.treasure] returns. Nothing is placed or given to
    anyone: put the coins in a purse, the valuables and magic items in an
    [`Inventory`][osrlib.core.items.Inventory], or keep the whole thing in a dungeon feature
    until the party opens it. Any of the three fields can be empty, and an unlucky roll
    leaves all three empty.
    """

    model_config = ConfigDict(frozen=True)

    coins: Coins = Coins()
    """The coins, by denomination. See [`Coins`][osrlib.core.items.Coins]."""
    valuables: tuple[ValuableInstance, ...] = ()
    """The gems and jewellery. See [`ValuableInstance`][osrlib.core.items.ValuableInstance]."""
    magic_items: tuple[MagicItemInstance, ...] = ()
    """The magic items, already rolled up as instances. See [`MagicItemInstance`][osrlib.core.items.MagicItemInstance].
    """


class CoinPurse(BaseModel):
    """The coins a character is carrying, by denomination.

    Every [`Inventory`][osrlib.core.items.Inventory] has one, and it is mutable: this is the
    money that gets spent. Ask [`can_afford`][osrlib.core.items.CoinPurse.can_afford] before
    you charge, and [`spend`][osrlib.core.items.CoinPurse.spend] to charge. Both work in
    whole gold pieces, the unit the equipment lists price in.

    Paying takes the smallest coins first and returns change in the largest, so a purse
    always ends up with the fewest coins that preserve its value. That matters because
    coins are weight: each coin weighs 1 whatever its metal, and a purse full of copper
    slows a character down.
    """

    model_config = ConfigDict(validate_assignment=True)

    pp: int = Field(default=0, ge=0)
    """Platinum pieces, worth 5 gp each."""
    gp: int = Field(default=0, ge=0)
    """Gold pieces."""
    ep: int = Field(default=0, ge=0)
    """Electrum pieces, worth half a gold piece each."""
    sp: int = Field(default=0, ge=0)
    """Silver pieces, ten to the gold piece."""
    cp: int = Field(default=0, ge=0)
    """Copper pieces, a hundred to the gold piece."""

    @property
    def value_cp(self) -> int:
        """What the purse is worth in copper pieces.

        Copper is the exact unit: totalling a mixed purse in gold would lose the odd silver and
        copper to rounding.

        Returns:
            The value in copper pieces.
        """
        return sum(getattr(self, denomination) * value for denomination, value in COIN_VALUES_CP.items())

    @property
    def total_coins(self) -> int:
        """How many coins are in the purse, which is also its weight in coins.

        Every coin weighs 1 whatever its metal, so this figure goes straight into
        [`treasure_weight_coins`][osrlib.core.items.treasure_weight_coins].

        Returns:
            The number of coins.
        """
        return self.pp + self.gp + self.ep + self.sp + self.cp

    def can_afford(self, cost_gp: int) -> bool:
        """Return whether the purse can cover a price in gold pieces.

        Ask before you charge: [`spend`][osrlib.core.items.CoinPurse.spend] raises rather than
        going into debt. Coins of every denomination count, so a purse with no gold at all can
        still afford a gold-priced item.

        Args:
            cost_gp: The price in whole gold pieces. Not negative.

        Returns:
            True when the purse is worth at least that much.

        Raises:
            ValueError: If `cost_gp` is negative.

        Examples:
            ```python
            from osrlib.core.items import CoinPurse

            purse = CoinPurse(sp=250)
            print(purse.can_afford(25), purse.can_afford(26))
            # True False
            ```
        """
        if cost_gp < 0:
            raise ValueError(f"cost must be non-negative, got {cost_gp}")
        return self.value_cp >= cost_gp * 100

    def spend(self, cost_gp: int) -> None:
        """Pay a price in gold pieces out of the purse, making change.

        Mutates the purse. Coins go out smallest denomination first, and any overpayment comes
        back as the fewest coins that make up the difference, largest denomination first: paying
        1 gp from a purse of two gold and five silver spends the silver, then a gold piece to
        cover the rest, and returns the change as a single electrum piece. The result is
        deterministic and preserves value exactly, so a purse can be spent from and saved
        without drifting.

        [`purchase`][osrlib.core.items.purchase] calls this for you when a character buys
        equipment. Call it directly for anything else a game charges for, like lodging or
        travel.

        Args:
            cost_gp: The price in whole gold pieces. Not negative.

        Raises:
            ValueError: If `cost_gp` is negative, or the purse cannot cover it. Ask
                [`can_afford`][osrlib.core.items.CoinPurse.can_afford] first. Overspending is
                a programming mistake, not a rejection a player should see.

        Examples:
            ```python
            from osrlib.core.items import CoinPurse

            purse = CoinPurse(gp=2, sp=5)
            purse.spend(1)
            print(purse.gp, purse.sp, purse.ep)
            # 1 0 1
            ```
        """
        if not self.can_afford(cost_gp):
            raise ValueError(f"insufficient funds: {cost_gp} gp costs more than the purse holds")
        cost_cp = cost_gp * 100
        paid = 0
        for denomination in ("cp", "sp", "ep", "gp", "pp"):
            value = COIN_VALUES_CP[denomination]
            held = getattr(self, denomination)
            used = min(held, -(-max(cost_cp - paid, 0) // value))
            setattr(self, denomination, held - used)
            paid += used * value
            if paid >= cost_cp:
                break
        change = paid - cost_cp
        for denomination in ("pp", "gp", "ep", "sp", "cp"):
            value = COIN_VALUES_CP[denomination]
            coins, change = divmod(change, value)
            setattr(self, denomination, getattr(self, denomination) + coins)


AnyInstance = Annotated[
    ItemInstance | MagicItemInstance,
    Field(discriminator="instance_type"),
]
"""Any owned item, mundane or magic, told apart by its `instance_type` field.

This is what an [`Inventory`][osrlib.core.items.Inventory] contains: its item list and its
equipped slots take either kind, because a character wields a sword and a sword +1 the
same way. Because the union is discriminated, pydantic reads a saved inventory back as
the right classes, and a `match` on `instance_type` covers both cases.

The members are [`ItemInstance`][osrlib.core.items.ItemInstance] and
[`MagicItemInstance`][osrlib.core.items.MagicItemInstance]. Only a magic instance has an
id of its own. A mundane one is identified by its template.
"""


class Inventory(BaseModel):
    """Everything one character carries: items, coins, valuables, and what is in hand or worn.

    Every [`Character`][osrlib.core.character.Character] owns one. Build it up with
    [`purchase`][osrlib.core.items.purchase] and [`equip`][osrlib.core.items.equip], search
    it with [`carried_item`][osrlib.core.items.Inventory.carried_item] and
    [`magic_item`][osrlib.core.items.Inventory.magic_item], and weigh it with
    [`tracked_weight_coins`][osrlib.core.items.tracked_weight_coins] or
    [`movement_rate_feet`][osrlib.core.items.movement_rate_feet]. Under a running game the
    commands do all of this and record it in the save.

    An instance lives in exactly one place: equipping moves it out of the item list and into
    its slot, and unequipping moves it back. So iterate
    [`all_instances`][osrlib.core.items.Inventory.all_instances] rather than `items` when
    you want everything a character has. The item list keeps the order things were added,
    which is what makes a saved game replay identically.

    Nothing here caps what a character can carry. Weight is not a limit but a movement rate:
    past [`MAX_LOAD_COINS`][osrlib.core.items.MAX_LOAD_COINS] the character cannot move.
    """

    model_config = ConfigDict(validate_assignment=True)

    items: list[AnyInstance] = []
    """What is carried but not in use, in the order it was acquired. See [`AnyInstance`][osrlib.core.items.AnyInstance].
    """
    purse: CoinPurse = CoinPurse()
    """The coins. See [`CoinPurse`][osrlib.core.items.CoinPurse]."""
    valuables: list[ValuableInstance] = []
    """Carried gems and jewellery. See [`ValuableInstance`][osrlib.core.items.ValuableInstance]."""
    worn_armour: AnyInstance | None = None
    """The suit of body armour being worn, or `None`."""
    shield: AnyInstance | None = None
    """The shield being carried, or `None`."""
    wielded: list[AnyInstance] = []
    """What is in hand: weapons, a lit torch, a wand. A two-handed weapon here rules out a shield."""
    rings: list[MagicItemInstance] = []
    """The worn rings, at most [`MAX_RINGS_WORN`][osrlib.core.items.MAX_RINGS_WORN]."""

    def all_instances(self) -> list[ItemInstance | MagicItemInstance]:
        """Return every instance the character has, carried or equipped.

        Use it whenever "what does this character have" is the question: weighing a load,
        looking for an item, rendering a character sheet. Equipped items are not in `items`, so
        reading that field alone misses the sword in hand.

        Returns:
            A new list: the item list first, then worn armour, the shield, what is wielded,
            and the rings. The order is stable, so anything that iterates it behaves the same
            on replay.
        """
        equipped: list[ItemInstance | MagicItemInstance] = []
        if self.worn_armour is not None:
            equipped.append(self.worn_armour)
        if self.shield is not None:
            equipped.append(self.shield)
        return [*self.items, *equipped, *self.wielded, *self.rings]

    def equipped_instances(self) -> list[ItemInstance | MagicItemInstance]:
        """Return only what the character has in use.

        This is the set that grants bonuses:
        [`equipped_item_modifiers`][osrlib.core.items.equipped_item_modifiers] scans exactly
        this. For everything a character has, carried items included, call
        [`all_instances`][osrlib.core.items.Inventory.all_instances].

        Returns:
            A new list: worn armour, the shield, what is wielded, then the rings, in that
            order.
        """
        equipped: list[ItemInstance | MagicItemInstance] = []
        if self.worn_armour is not None:
            equipped.append(self.worn_armour)
        if self.shield is not None:
            equipped.append(self.shield)
        return [*equipped, *self.wielded, *self.rings]

    def magic_item(self, instance_id: str) -> MagicItemInstance | None:
        """Return the carried magic item with `instance_id`, or `None`.

        Magic items are addressed by their own ids, which is how a command names the one to
        drink, read, or take off. It looks everywhere the character keeps things: pack,
        hands, worn slots, rings.

        Args:
            instance_id: The instance id, for example `"magic-item-0003"`, from the item's
                [`MagicItemInstance`][osrlib.core.items.MagicItemInstance].

        Returns:
            The instance, or `None` when this character is not carrying it.
        """
        for instance in self.all_instances():
            if isinstance(instance, MagicItemInstance) and instance.instance_id == instance_id:
                return instance
        return None

    def carried_item(self, item_id: str) -> ItemInstance | MagicItemInstance | None:
        """Return the first instance of a catalog id the character is carrying, mundane or magic, or `None`.

        Ask this when you know what you want but not which copy: does anyone have a torch, does
        this character still have arrows. It looks everywhere, in
        [`all_instances`][osrlib.core.items.Inventory.all_instances] order: pack, hands, worn
        slots, rings. A mundane instance matches on its template's id and a magic one on its
        `template_id`, and since equipment ids and magic item ids never collide, an id can
        resolve to only one of the two.

        A spent stack never matches. A quantity of zero is expressible only on a magic instance,
        like an emptied quiver of arrows +1, and it means the character no longer has any, so
        whatever this returns you can take a unit from.

        Valuables never match either: a gem has no catalog id, only an id of its own.

        Args:
            item_id: The catalog id to look for: an equipment id (see
                [the equipment id index][equipment-index], or an id an adventure bundles) or a
                magic item id (see [the magic item id index][magic-items-index]).

        Returns:
            The instance, or `None` when the character has none.

        Examples:
            ```python
            from osrlib.core.items import Inventory, ItemInstance
            from osrlib.data import load_equipment

            inventory = Inventory(items=[ItemInstance(template=load_equipment().get("torch"), quantity=6)])
            carried = inventory.carried_item("torch")
            assert carried is not None and carried.quantity == 6
            assert inventory.carried_item("lantern") is None
            ```
        """
        for instance in self.all_instances():
            if instance.quantity < 1:
                continue
            if isinstance(instance, MagicItemInstance):
                if instance.template_id == item_id:
                    return instance
            elif instance.template.id == item_id:
                return instance
        return None


def magic_item_template(instance: MagicItemInstance) -> MagicItemTemplate:
    """Return the template behind a magic item instance.

    The shorthand for looking the instance's `template_id` up in the shipped catalog: it is
    what you call to get from the copy a character carries to the item's name, category,
    bonuses, and text. Equivalent to
    [`MagicItemCatalog.get`][osrlib.core.items.MagicItemCatalog.get] on
    [`load_magic_items`][osrlib.data.load_magic_items], which is what to call when you have
    an id rather than an instance.

    Args:
        instance: The instance whose template to look up.

    Returns:
        The frozen template.

    Raises:
        ValueError: If the instance names an item the catalog does not have.

    Examples:
        ```python
        from osrlib.core.items import MagicItemInstance, magic_item_template

        instance = MagicItemInstance(instance_id="magic-item-0001", template_id="ring_of_protection")
        print(magic_item_template(instance).name)
        # Ring of Protection
        ```
    """
    from osrlib.data import load_magic_items

    return load_magic_items().get(instance.template_id)


def equipped_item_modifiers(inventory: Inventory) -> list[ModifierSpec]:
    """Return the stat modifiers a character's equipped magic items grant.

    Call it wherever a bonus from an item has to be counted: attack and damage resolution,
    saving throws, armour class. An item contributes only while it is equipped and only if
    it works by being worn or wielded, so taking the ring off takes its bonus away with no
    bookkeeping.

    Item bonuses are computed from the inventory each time you ask rather than stored as
    effects, and that is deliberate: they stack freely with spell bonuses and are never
    subject to the cap that
    [`modifier_total`][osrlib.core.effects.modifier_total] applies to spell-sourced
    modifiers, which counts only the largest bonus and the largest penalty.

    Args:
        inventory: The inventory to scan.

    Returns:
        The modifiers, in equipped order: worn armour, shield, wielded, rings. Empty when
        nothing equipped grants one.

    Examples:
        ```python
        from osrlib.core.items import Inventory, MagicItemInstance, equipped_item_modifiers

        ring = MagicItemInstance(instance_id="magic-item-0001", template_id="ring_of_protection")
        modifiers = equipped_item_modifiers(Inventory(rings=[ring]))
        print([(modifier.kind, modifier.value) for modifier in modifiers])
        # [('save_bonus', 1)]
        ```
    """
    modifiers: list[ModifierSpec] = []
    for instance in inventory.equipped_instances():
        if not isinstance(instance, MagicItemInstance):
            continue
        template = magic_item_template(instance)
        if not template.always_active or template.effect is None:
            continue
        modifiers.extend(template.effect.modifiers)
    return modifiers


class SwordControlResult(BaseModel):
    """The arithmetic of one contest of wills between a sentient sword and its wielder.

    Returned by
    [`sword_control_check`][osrlib.core.items.sword_control_check]. It reports the two
    totals and who won. Nothing else happens: no events, no conditions, no change to either
    party. What a sword in control makes its wielder do is the referee's to narrate.
    """

    model_config = ConfigDict(frozen=True)

    sword_will: int
    """What the sword brought to the contest."""
    wielder_will: int
    """What the wielder brought to the contest."""
    sword_controls: bool
    """True when the sword's total is higher and it takes charge. A tie goes to the wielder."""


# The annotation stays quoted because `Character` is imported for type checking alone,
# and this signature is read at runtime, where a bare forward reference cannot resolve.
def sword_control_check(
    character: "Character",  # noqa: UP037
    sword: MagicItemInstance,
    *,
    stream: RngStream,
) -> SwordControlResult:
    """Resolve one contest of wills between a sentient sword and the character holding it.

    A sentient sword can try to take charge of its wielder. This runs that contest and
    reports who won. Nothing follows from it automatically, because what a controlling sword
    makes its wielder do is a referee's call. Nothing in the crawl calls this for you: a game
    decides when a sword pushes its luck, and narrates the result.

    The sword's will is its intelligence plus its ego, plus 1 for each extraordinary power,
    plus 1d10 when wielder and sword are of different alignments. The wielder's will is
    strength plus wisdom, less 1d4 when they are hurt at all and 2d4 when they are below half
    their hit points. The sword takes charge when its total is strictly higher.

    Args:
        character: The wielder, a [`Character`][osrlib.core.character.Character]: the contest reads the
            ability scores that no monster has. Nothing is mutated.
        sword: The sword, which must have a
            [`SwordSentience`][osrlib.core.items.SwordSentience].
        stream: The RNG stream the situational dice come from. Pass a session stream so
            the draws replay. Which stream is yours to choose.

    Returns:
        The two totals and who won. See
        [`SwordControlResult`][osrlib.core.items.SwordControlResult].

    Raises:
        ValueError: If the sword is not sentient.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.items import MagicItemInstance, SwordSentience, sword_control_check
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset

        streams = RngStreams(master_seed=7)
        wielder = create_character(
            name="Aleran",
            class_id="fighter",
            alignment=Alignment.LAWFUL,
            ruleset=Ruleset(),
            stream=streams.get(CHARACTER_CREATION_STREAM),
        ).character
        sentience = SwordSentience(intelligence=12, ego=12, communication="speech", reading=True, alignment="chaotic")
        sword = MagicItemInstance(
            instance_id="magic-item-0001", template_id="sword_plus_1", base_item_id="sword", sentience=sentience
        )

        result = sword_control_check(wielder, sword, stream=streams.get("treasure"))
        print(result.sword_will, result.wielder_will, result.sword_controls)
        # 25 23 True
        ```
    """
    from osrlib.core.abilities import AbilityScore

    if sword.sentience is None:
        raise ValueError(f"{sword.instance_id} is not sentient")
    sentience = sword.sentience
    sword_will = sentience.intelligence + sentience.ego + len(sentience.extraordinary_powers)
    wielder_alignment = getattr(getattr(character, "alignment", None), "value", None)
    if wielder_alignment != sentience.alignment:
        sword_will += stream.randbelow(10) + 1
    scores = character.scores
    wielder_will = scores[AbilityScore.STR] + scores[AbilityScore.WIS]
    if character.current_hp < character.max_hp:
        if character.current_hp * 2 < character.max_hp:
            wielder_will -= stream.randbelow(4) + 1 + stream.randbelow(4) + 1
        else:
            wielder_will -= stream.randbelow(4) + 1
    return SwordControlResult(
        sword_will=sword_will, wielder_will=wielder_will, sword_controls=sword_will > wielder_will
    )


def treasure_weight_coins(inventory: Inventory) -> int:
    """Return the weight of the treasure a character is carrying, in coins.

    This is the figure basic encumbrance tracks: coins, gems, jewellery, and the magic items
    the encumbrance table prices as treasure, which are potions, scrolls, rods, staves, and
    wands. Every coin weighs 1, whatever its metal.

    A stack of those weighs its template's figure times its quantity, and a spent stack still
    weighs one, because a quantity of 0 counts as 1. Rings and miscellaneous items weigh
    nothing, because their pages give no weight. A bag of holding weighs its printed loaded
    weight while it holds anything. Enchanted weapons and armour weigh as equipment beside
    the mundane kind rather than as treasure, so basic encumbrance stays what the rules mean
    by it: how much loot is being hauled out.

    Call [`tracked_weight_coins`][osrlib.core.items.tracked_weight_coins] instead when you
    want whatever the ruleset in play actually tracks.

    Args:
        inventory: The inventory to weigh.

    Returns:
        The treasure weight in coins.
    """
    total = inventory.purse.total_coins
    total += sum(valuable.weight_coins for valuable in inventory.valuables)
    for instance in inventory.all_instances():
        if isinstance(instance, MagicItemInstance):
            template = magic_item_template(instance)
            if template.category in (
                MagicItemCategory.POTION,
                MagicItemCategory.SCROLL,
                MagicItemCategory.ROD,
                MagicItemCategory.STAFF,
                MagicItemCategory.WAND,
            ):
                total += template.weight_coins * max(1, instance.quantity)
            elif "loaded_weight_coins" in template.params and instance.state.get("holding"):
                total += _int_param(template.params, "loaded_weight_coins")
    return total


def equipment_weight_coins(inventory: Inventory) -> int:
    """Return the weight of a character's weapons, armour, and gear, in coins.

    The other half of detailed encumbrance, alongside
    [`treasure_weight_coins`][osrlib.core.items.treasure_weight_coins]. Weapons, armour, and
    ammunition weigh their listed weights, times the number carried. The shipped ammunition
    templates are all weight 0, because the SRD folds ammunition into the missile weapon's
    own weight, so shipped arrows and bolts add nothing. Ammunition an adventure bundles with
    a weight of its own counts like any other template. Gear has no per-item weights, so
    carrying any gear at all adds a flat
    [`MISC_GEAR_WEIGHT_COINS`][osrlib.core.items.MISC_GEAR_WEIGHT_COINS] and no more.

    Enchanted weapons and armour weigh what the mundane item underneath weighs, with armour
    halved, since enchanted armour is lighter than the plate it is made from.

    Args:
        inventory: The inventory to weigh.

    Returns:
        The equipment weight in coins.
    """
    from osrlib.data import load_equipment

    total = 0
    has_gear = False
    for instance in inventory.all_instances():
        if isinstance(instance, MagicItemInstance):
            if instance.base_item_id is None:
                continue
            base = load_equipment().get(instance.base_item_id)
            weight = getattr(base, "weight_coins", 0)
            template = magic_item_template(instance)
            if template.category is MagicItemCategory.ARMOUR:
                weight //= 2
            total += weight
            continue
        template = instance.template
        if isinstance(template, WeaponTemplate | ArmourTemplate | AmmunitionTemplate):
            total += template.weight_coins * instance.quantity
        else:
            has_gear = True
    if has_gear:
        total += MISC_GEAR_WEIGHT_COINS
    return total


def tracked_weight_coins(inventory: Inventory, mode: EncumbranceMode) -> int:
    """Return the weight the encumbrance rules in play actually count, in coins.

    Ask this rather than the two weighing functions directly: the answer depends on the
    `encumbrance` flag of [`Ruleset`][osrlib.core.ruleset.Ruleset], and this is what
    [`movement_rate_feet`][osrlib.core.items.movement_rate_feet] compares against
    [`MAX_LOAD_COINS`][osrlib.core.items.MAX_LOAD_COINS].

    Args:
        inventory: The inventory to weigh.
        mode: The encumbrance mode in play, from the ruleset.

    Returns:
        0 when nothing is tracked, the treasure weight under basic encumbrance, and
        treasure plus equipment under detailed.

    Examples:
        ```python
        from osrlib.core.items import CoinPurse, Inventory, tracked_weight_coins
        from osrlib.core.ruleset import EncumbranceMode

        inventory = Inventory(purse=CoinPurse(gp=300))
        print(
            tracked_weight_coins(inventory, EncumbranceMode.NONE),
            tracked_weight_coins(inventory, EncumbranceMode.BASIC),
        )
        # 0 300
        ```
    """
    if mode is EncumbranceMode.NONE:
        return 0
    if mode is EncumbranceMode.BASIC:
        return treasure_weight_coins(inventory)
    return treasure_weight_coins(inventory) + equipment_weight_coins(inventory)


_BASIC_RATES: dict[ArmourCategory | None, tuple[int, int]] = {
    None: (120, 90),
    ArmourCategory.LIGHT: (90, 60),
    ArmourCategory.HEAVY: (60, 30),
}

_DETAILED_RATES: tuple[tuple[int, int], ...] = ((400, 120), (600, 90), (800, 60), (MAX_LOAD_COINS, 30))


def _int_param(params: Mapping[str, Any], key: str, default: int = 0) -> int:
    """Read an integer param from schema-validated data the type checker cannot key by name."""
    return int(params.get(key, default))


def _worn_armour_category(worn: Any) -> ArmourCategory | None:
    """Return the worn body armour's category, resolving enchanted armour's base.

    Enchanted armour moves like its mundane base: enchantment halves the weight, not the
    bulk, so +1 plate is still heavy for the basic encumbrance rates.

    Args:
        worn: The `worn_armour` slot's instance, or `None`.

    Returns:
        The armour category, or `None` for unarmoured.
    """
    if worn is None:
        return None
    if isinstance(worn, MagicItemInstance):
        if worn.base_item_id is None:
            return None
        from osrlib.data import load_equipment

        base = load_equipment().get(worn.base_item_id)
        return base.category if isinstance(base, ArmourTemplate) else None
    return worn.template.category if isinstance(worn.template, ArmourTemplate) else None


def movement_rate_feet(inventory: Inventory, ruleset: Ruleset, carrying_treasure: bool = False) -> int:
    """Return how far a character moves in one exploration turn, in feet.

    This is the number a dungeon crawl runs on: how far a party gets on a turn of careful
    movement. [`Character.movement_rate`][osrlib.core.character.Character.movement_rate] is
    the shorthand when you have a character rather than a bare inventory. Divide by 3 with
    [`encounter_movement_rate`][osrlib.core.items.encounter_movement_rate] for the rate
    inside a fight.

    What decides it depends on the `encumbrance` flag of
    [`Ruleset`][osrlib.core.ruleset.Ruleset]:

    - `none`: always
      [`BASE_MOVEMENT_FEET`][osrlib.core.items.BASE_MOVEMENT_FEET]. Nothing is weighed and
      no load limit applies.
    - `basic`: the armour being worn, and whether the character is hauling treasure.
      Unarmoured is 120' and 90' hauling. Light armour 90' and 60'. Heavy armour 60' and
      30'.
    - `detailed`: the weight carried, against the printed thresholds, which are inclusive:
      120' up to 400 coins, 90' up to 600, 60' up to 800, 30' up to the maximum load.

    Under both tracking modes, weight past
    [`MAX_LOAD_COINS`][osrlib.core.items.MAX_LOAD_COINS] means the character cannot move at
    all.

    Args:
        inventory: The inventory to weigh.
        ruleset: The ruleset whose encumbrance flag decides which rule applies.
        carrying_treasure: Whether the character is hauling a significant amount of
            treasure, under basic encumbrance. The tabletop rules leave "significant" to
            the referee and so does osrlib: the game sets this, and there is no invented
            threshold behind it. Ignored under the other two modes.

    Returns:
        The movement rate in feet per turn: 120, 90, 60, 30, or 0.

    Examples:
        ```python
        from osrlib.core.items import Inventory, ItemInstance, movement_rate_feet
        from osrlib.core.ruleset import Ruleset
        from osrlib.data import load_equipment

        plate = ItemInstance(template=load_equipment().get("plate_mail"))
        inventory = Inventory(worn_armour=plate)
        print(
            movement_rate_feet(inventory, Ruleset()),
            movement_rate_feet(inventory, Ruleset(), carrying_treasure=True),
        )
        # 60 30
        ```
    """
    mode = ruleset.encumbrance
    if mode is EncumbranceMode.NONE:
        return BASE_MOVEMENT_FEET
    tracked = tracked_weight_coins(inventory, mode)
    if tracked > MAX_LOAD_COINS:
        return 0
    if mode is EncumbranceMode.BASIC:
        without, with_treasure = _BASIC_RATES[_worn_armour_category(inventory.worn_armour)]
        return with_treasure if carrying_treasure else without
    for threshold, rate in _DETAILED_RATES:
        if tracked <= threshold:
            return rate
    return 0  # unreachable: tracked > MAX_LOAD_COINS returned above


def encounter_movement_rate(base_rate_feet: int) -> int:
    """Return how far a character moves in one combat round, in feet.

    A third of the exploration rate, rounded down: that is the figure printed in brackets on
    the movement tables. It is computed whenever asked rather than stored, so it follows the
    exploration rate as a load changes.

    Args:
        base_rate_feet: The exploration movement rate, from
            [`movement_rate_feet`][osrlib.core.items.movement_rate_feet]. Not negative.

    Returns:
        The rate in feet per round.

    Raises:
        ValueError: If `base_rate_feet` is negative.

    Examples:
        ```python
        from osrlib.core.items import encounter_movement_rate

        print(encounter_movement_rate(120), encounter_movement_rate(60))
        # 40 20
        ```
    """
    if base_rate_feet < 0:
        raise ValueError(f"movement rate must be non-negative, got {base_rate_feet}")
    return base_rate_feet // 3


def validate_purchase(purse: CoinPurse, template: ItemTemplate, lots: int = 1) -> list[Rejection]:
    """Check whether a character can afford to buy `lots` of an item.

    The check half of [`purchase`][osrlib.core.items.purchase], which raises where this
    reports. Call this when you want a reason to show a player rather than an exception:
    a shop screen that greys out what the party cannot afford, or a command that rejects.

    A purchase lot is what one purchase at the item's listed price delivers. See
    [`purchase`][osrlib.core.items.purchase] for what a lot buys.

    Args:
        purse: The buyer's purse.
        template: The item to buy.
        lots: How many purchase lots. Positive.

    Returns:
        The reasons the purchase cannot go ahead, as
        [`Rejection`][osrlib.core.validation.Rejection]s. Empty when it can. The only
        reason here is `items.purchase.insufficient_funds`.

    Raises:
        ValueError: If `lots` is not positive.

    Examples:
        ```python
        from osrlib.core.items import CoinPurse, validate_purchase
        from osrlib.data import load_equipment

        purse = CoinPurse(gp=5)
        sword = load_equipment().get("sword")
        print([rejection.code for rejection in validate_purchase(purse, sword)])
        # ['items.purchase.insufficient_funds']
        ```
    """
    if lots < 1:
        raise ValueError(f"lots must be positive, got {lots}")
    cost = template.cost_gp * lots
    if not purse.can_afford(cost):
        return [Rejection(code="items.purchase.insufficient_funds", params={"item": template.id, "cost_gp": cost})]
    return []


def purchase(inventory: Inventory, template: ItemTemplate, lots: int = 1) -> ItemInstance:
    """Buy `lots` of an item, pay for it out of the purse, and add it to the inventory.

    The à la carte way to equip a character outside a session. In a session the
    [`PurchaseEquipment`][osrlib.crawl.commands.PurchaseEquipment] command does this and
    records it. Check first with
    [`validate_purchase`][osrlib.core.items.validate_purchase] if you would rather have a
    reason than an exception.

    A purchase lot is what one purchase at the item's listed price delivers. Gear and
    ammunition come in lots of the size the catalog prints, so one purchase of torches costs
    1 gp and yields six torches. Weapons and armour have no lot size, so one lot is one
    item. `lots` multiplies both the price and what arrives: two lots of torches cost 2 gp
    and yield twelve torches, in one stack.

    Args:
        inventory: The buyer's inventory. Mutated: the purse is charged and the new stack
            is appended to the item list.
        template: The item to buy, from
            [`EquipmentCatalog.get`][osrlib.core.items.EquipmentCatalog.get].
        lots: How many purchase lots. Positive.

    Returns:
        The new stack, which is also now in the inventory's item list. Pass it to
        [`equip`][osrlib.core.items.equip] to put it to use.

    Raises:
        ValueError: If `lots` is not positive, or the purse cannot cover the price.
            Buying what you cannot afford is a programming mistake, not a rejection a
            player should see. Ask
            [`validate_purchase`][osrlib.core.items.validate_purchase] first.

    Examples:
        ```python
        from osrlib.core.items import CoinPurse, Inventory, purchase
        from osrlib.data import load_equipment

        inventory = Inventory(purse=CoinPurse(gp=10))
        torches = purchase(inventory, load_equipment().get("torch"), lots=2)
        print(torches.quantity, inventory.purse.gp)
        # 12 8
        ```
    """
    rejections = validate_purchase(inventory.purse, template, lots)
    if rejections:
        raise ValueError(f"illegal purchase: {[rejection.code for rejection in rejections]}")
    inventory.purse.spend(template.cost_gp * lots)
    lot_size = template.lot_size if isinstance(template, GearTemplate | AmmunitionTemplate) else 1
    instance = ItemInstance(template=template, quantity=lot_size * lots)
    inventory.items.append(instance)
    return instance


def _wielded_qualities(instance: ItemInstance | MagicItemInstance) -> tuple[WeaponQuality, ...]:
    """Return a wielded instance's weapon qualities. An enchanted arm reads its base item's."""
    if isinstance(instance, MagicItemInstance):
        base_id = instance.base_item_id or magic_item_template(instance).base_item_id
        if base_id is None:
            return ()
        from osrlib.data import load_equipment

        base = load_equipment().get(base_id)
        return getattr(base, "qualities", ())
    template = instance.template
    facet = getattr(template, "combat", None) or template
    return getattr(facet, "qualities", ()) or ()


def _caster_kind(definition: ClassDefinition) -> str | None:
    """Return `"arcane"` or `"divine"` from the class's casting tag, or `None` for no caster."""
    for ability in definition.abilities:
        if ability.tag == "arcane_magic":
            return "arcane"
        if ability.tag == "divine_magic":
            return "divine"
    return None


def usable_by_class(template: MagicItemTemplate, definition: ClassDefinition) -> bool:
    """Return whether a character of this class can use a magic item.

    The rules restrict some items to particular classes or to spell casters, and this is the
    question that answers. [`validate_equip`][osrlib.core.items.validate_equip] applies it to
    devices and miscellaneous items. Call it directly when you are deciding whether to offer
    a player the option to drink, read, or invoke something.

    Enchanted swords, weapons, and armour are not restricted here: their pages defer to the
    class's ordinary armour and weapon policies, which apply to the mundane item underneath.

    Args:
        template: The magic item template.
        definition: The character's class definition, from
            [`ClassCatalog.get`][osrlib.core.classes.ClassCatalog.get].

    Returns:
        True when the class may use the item.

    Examples:
        ```python
        from osrlib.core.items import usable_by_class
        from osrlib.data import load_classes, load_magic_items

        staff = load_magic_items().get("staff_of_healing")
        classes = load_classes()
        print(
            usable_by_class(staff, classes.get("cleric")),
            usable_by_class(staff, classes.get("magic_user")),
        )
        # True False
        ```
    """
    usable = template.usable_by
    if usable.kind == "all":
        return True
    if usable.kind == "classes":
        return definition.id in usable.class_ids
    kind = _caster_kind(definition)
    if kind is None:
        return False
    return usable.caster == "any" or usable.caster == kind


def _validate_equip_magic(
    definition: ClassDefinition, instance: MagicItemInstance, inventory: Inventory | None
) -> list[Rejection]:
    """Validate equipping a magic item against the base item's policies and the item's own.

    Enchanted arms resolve through the base item's armour and weapon policies exactly like
    their mundane counterparts. Rings cap at two (`items.ring.hands_full`, the slot cap
    standing in for the rule that more than two rings make none of them work). Devices and
    miscellaneous items gate on the item's `usable_by`. Potions, scrolls, and ammunition are
    not equippable.
    """
    from osrlib.data import load_equipment

    template = magic_item_template(instance)
    if template.category is MagicItemCategory.RING:
        if inventory is not None and len(inventory.rings) >= MAX_RINGS_WORN:
            return [Rejection(code="items.ring.hands_full", params={"item": instance.instance_id})]
        return []
    if template.category in (MagicItemCategory.POTION, MagicItemCategory.SCROLL):
        return [Rejection(code="items.equip.not_equippable", params={"item": instance.instance_id})]
    if template.category in (MagicItemCategory.SWORD, MagicItemCategory.WEAPON, MagicItemCategory.ARMOUR):
        base_id = instance.base_item_id or template.base_item_id
        if base_id is None:
            return [Rejection(code="items.equip.not_equippable", params={"item": instance.instance_id})]
        base = load_equipment().get(base_id)
        if isinstance(base, AmmunitionTemplate):
            return [Rejection(code="items.equip.not_equippable", params={"item": instance.instance_id})]
        return validate_equip(definition, ItemInstance(template=base), inventory)
    # Devices and miscellaneous items: the item's own usability governs (the RAW
    # staves-in-melee carve-out means class weapon policies do not apply, pinned).
    if not usable_by_class(template, definition):
        return [Rejection(code="items.equip.not_usable", params={"item": instance.instance_id})]
    return []


def validate_unequip(inventory: Inventory, instance: ItemInstance | MagicItemInstance) -> list[Rejection]:
    """Check whether an equipped item can be taken off.

    The check half of [`unequip`][osrlib.core.items.unequip], which raises where this
    reports. There is one reason it can fail: a cursed item whose curse has shown itself
    sticks to its bearer until *remove curse*, and every cursed item's page says so.

    Args:
        inventory: The inventory holding the item.
        instance: The equipped item.

    Returns:
        The reasons it cannot come off, as
        [`Rejection`][osrlib.core.validation.Rejection]s. Empty when it can. The only
        reason is `items.curse.stuck`.

    Examples:
        ```python
        from osrlib.core.items import Inventory, MagicItemInstance, validate_unequip

        ring = MagicItemInstance(instance_id="magic-item-0001", template_id="ring_of_weakness", cursed_revealed=True)
        inventory = Inventory(rings=[ring])
        print([rejection.code for rejection in validate_unequip(inventory, ring)])
        # ['items.curse.stuck']
        ```
    """
    if isinstance(instance, MagicItemInstance) and instance.cursed_revealed:
        return [Rejection(code="items.curse.stuck", params={"item": instance.instance_id})]
    return []


def validate_equip(
    definition: ClassDefinition, instance: ItemInstance | MagicItemInstance, inventory: Inventory | None = None
) -> list[Rejection]:
    """Check whether a character of this class can equip an item.

    The check half of [`equip`][osrlib.core.items.equip], which raises where this reports.
    Call it to decide what to offer a player: which weapons a magic-user can actually pick
    up, why a cleric cannot draw the sword the party just found.

    What it enforces:

    - The class's armour policy: whether armour is allowed at all, and whether this suit is,
      and whether shields are.
    - The class's weapon policy, which covers the weapons list only. Gear with a combat use
      (torch, holy water, burning oil) is exempt and always equippable, a documented
      adaptation that also lets a magic-user throw oil. Gear without a combat use, and
      ammunition, cannot be equipped by anyone.
    - Two-handed weapons and shields, which cannot be used together. Whichever of the pair
      comes second is rejected, so this check needs to see what is already equipped: pass
      `inventory` whenever you have one.
    - For magic items: enchanted arms resolve through the policies of the mundane item
      underneath, rings against the two-ring cap, and devices and miscellaneous items
      against their own usability. See
      [`usable_by_class`][osrlib.core.items.usable_by_class]. Potions and scrolls are not
      equipment.

    Args:
        definition: The character's class definition, from
            [`ClassCatalog.get`][osrlib.core.classes.ClassCatalog.get].
        instance: The item to equip.
        inventory: The inventory whose equipped state the two-handed-and-shield check reads.
            Leave it out only when there is no inventory yet.

    Returns:
        The reasons it cannot be equipped, as
        [`Rejection`][osrlib.core.validation.Rejection]s. Empty when it can. The reasons
        are `items.equip.armour_forbidden`, `items.equip.armour_not_allowed`,
        `items.equip.shield_forbidden`, `items.equip.weapon_not_allowed`,
        `items.equip.weapon_forbidden`, `items.equip.two_handed_with_shield`,
        `items.equip.not_equippable`, `items.equip.not_usable`, and
        `items.ring.hands_full`.

    Examples:
        ```python
        from osrlib.core.items import ItemInstance, validate_equip
        from osrlib.data import load_classes, load_equipment

        catalog = load_equipment()
        cleric = load_classes().get("cleric")
        print([rejection.code for rejection in validate_equip(cleric, ItemInstance(template=catalog.get("sword")))])
        # ['items.equip.weapon_not_allowed']
        print([rejection.code for rejection in validate_equip(cleric, ItemInstance(template=catalog.get("mace")))])
        # []
        ```
    """
    if isinstance(instance, MagicItemInstance):
        return _validate_equip_magic(definition, instance, inventory)
    template = instance.template
    if isinstance(template, ArmourTemplate):
        if template.is_shield:
            if not definition.armour.shields_allowed:
                return [Rejection(code="items.equip.shield_forbidden", params={"class": definition.id})]
            if inventory is not None and any(
                WeaponQuality.TWO_HANDED in _wielded_qualities(wielded) for wielded in inventory.wielded
            ):
                return [Rejection(code="items.equip.two_handed_with_shield", params={"class": definition.id})]
            return []
        if definition.armour.kind is ArmourPolicyKind.NONE:
            return [Rejection(code="items.equip.armour_forbidden", params={"class": definition.id})]
        if definition.armour.kind is ArmourPolicyKind.LEATHER_ONLY and template.id != "leather":
            return [
                Rejection(
                    code="items.equip.armour_not_allowed",
                    params={"class": definition.id, "item": template.id},
                )
            ]
        return []
    if isinstance(template, WeaponTemplate):
        policy = definition.weapons
        if policy.kind is WeaponPolicyKind.ALLOWED and template.id not in policy.weapon_ids:
            return [
                Rejection(
                    code="items.equip.weapon_not_allowed",
                    params={"class": definition.id, "item": template.id},
                )
            ]
        if policy.kind is WeaponPolicyKind.FORBIDDEN and template.id in policy.weapon_ids:
            return [
                Rejection(
                    code="items.equip.weapon_forbidden",
                    params={"class": definition.id, "item": template.id},
                )
            ]
        if WeaponQuality.TWO_HANDED in template.qualities and inventory is not None and inventory.shield is not None:
            return [Rejection(code="items.equip.two_handed_with_shield", params={"item": template.id})]
        return []
    if isinstance(template, GearTemplate) and template.combat is not None:
        return []
    return [Rejection(code="items.equip.not_equippable", params={"item": template.id})]


def _is_shield_instance(instance: ItemInstance | MagicItemInstance) -> bool:
    """Return whether an instance is the shield, mundane or enchanted."""
    if isinstance(instance, MagicItemInstance):
        return (instance.base_item_id or magic_item_template(instance).base_item_id) == "shield"
    return isinstance(instance.template, ArmourTemplate) and instance.template.is_shield


def _is_body_armour_instance(instance: ItemInstance | MagicItemInstance) -> bool:
    """Return whether an instance is a suit of body armour, mundane or enchanted."""
    if isinstance(instance, MagicItemInstance):
        template = magic_item_template(instance)
        return template.category is MagicItemCategory.ARMOUR and not _is_shield_instance(instance)
    return isinstance(instance.template, ArmourTemplate) and not instance.template.is_shield


def equip(inventory: Inventory, definition: ClassDefinition, instance: ItemInstance | MagicItemInstance) -> None:
    """Move an item out of the item list and into use.

    The à la carte way to arm a character outside a session. In a session the
    [`EquipItem`][osrlib.crawl.commands.EquipItem] command does this and records it. Check
    first with [`validate_equip`][osrlib.core.items.validate_equip] if you would rather have
    a reason than an exception.

    Where the item goes depends on what it is: body armour to the worn slot, a shield to the
    shield slot, rings to the ring slots, and everything else that can be equipped, weapons
    and lit torches and wands, to the wielded list. Whatever was in the armour or shield slot
    goes back to the item list. The item leaves the item list, so it is in exactly one place
    afterwards.

    Args:
        inventory: The inventory. Mutated.
        definition: The character's class definition, from
            [`ClassCatalog.get`][osrlib.core.classes.ClassCatalog.get].
        instance: The item to equip. It must be in this inventory's item list. Equip what
            the character is holding, not a template or a copy.

    Raises:
        ValueError: If the item is not in the item list, or the class cannot equip it. Ask
            [`validate_equip`][osrlib.core.items.validate_equip] first for the reason.

    Examples:
        ```python
        from osrlib.core.items import CoinPurse, Inventory, equip, purchase
        from osrlib.data import load_classes, load_equipment

        inventory = Inventory(purse=CoinPurse(gp=50))
        fighter = load_classes().get("fighter")
        sword = purchase(inventory, load_equipment().get("sword"))
        equip(inventory, fighter, sword)
        print(len(inventory.items), [held.template.id for held in inventory.wielded])
        # 0 ['sword']
        ```
    """
    if not any(existing is instance for existing in inventory.items):
        raise ValueError("only an instance in the inventory's item list can be equipped")
    rejections = validate_equip(definition, instance, inventory)
    if rejections:
        raise ValueError(f"illegal equip: {[rejection.code for rejection in rejections]}")
    inventory.items.remove(instance)
    if isinstance(instance, MagicItemInstance) and magic_item_template(instance).category is MagicItemCategory.RING:
        inventory.rings.append(instance)
    elif _is_shield_instance(instance):
        if inventory.shield is not None:
            inventory.items.append(inventory.shield)
        inventory.shield = instance
    elif _is_body_armour_instance(instance):
        if inventory.worn_armour is not None:
            inventory.items.append(inventory.worn_armour)
        inventory.worn_armour = instance
    else:
        inventory.wielded.append(instance)


def unequip(inventory: Inventory, instance: ItemInstance | MagicItemInstance) -> None:
    """Take an item out of use and put it back in the item list.

    The reverse of [`equip`][osrlib.core.items.equip]. In a session the
    [`UnequipItem`][osrlib.crawl.commands.UnequipItem] command does this and records it.
    Check first with [`validate_unequip`][osrlib.core.items.validate_unequip] if you would
    rather have a reason than an exception, since a revealed cursed item cannot be taken off
    at all.

    The curse check runs before the slot search, so an item under a revealed curse reports
    the curse whether or not it is equipped at all.

    Args:
        inventory: The inventory. Mutated.
        instance: The equipped item, from whichever slot holds it.

    Raises:
        ValueError: If a revealed curse holds the item in place, and otherwise if the item
            is not equipped in this inventory.
    """
    rejections = validate_unequip(inventory, instance)
    if rejections:
        raise ValueError(f"illegal unequip: {[rejection.code for rejection in rejections]}")
    if inventory.worn_armour is instance:
        inventory.worn_armour = None
    elif inventory.shield is instance:
        inventory.shield = None
    elif any(existing is instance for existing in inventory.wielded):
        inventory.wielded.remove(instance)
    elif isinstance(instance, MagicItemInstance) and any(existing is instance for existing in inventory.rings):
        inventory.rings.remove(instance)
    else:
        raise ValueError("instance is not equipped")
    inventory.items.append(instance)
