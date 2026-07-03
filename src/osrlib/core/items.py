"""Weapons, armour, gear, ammunition, inventory, coins, and encumbrance.

Equipment compiles from the SRD's equipment pages into `equipment.json` and loads as
frozen templates via [`load_equipment`][osrlib.data.load_equipment]; play spawns
mutable [`ItemInstance`][osrlib.core.items.ItemInstance]s from them, so shared data can
never be damaged by play.

Torch, holy water, and burning oil appear on both the SRD's weapon table and its gear
list; pinned, one entry per physical item: they compile as *gear* carrying an embedded
combat facet, the weapons list holds the 19 pure weapons, and no item has two ids.
Class weapon policies govern the weapons list only — gear combat facets are exempt (a
strict quality-tag reading would forbid a cleric holy water, which is absurd; the rule
deliberately over-grants: a magic-user may also throw oil or swing a torch — see
`docs/adaptations.md`).

All weights are in coins (ten coins to the pound); coins themselves weigh 1 each. The
maximum load rule is general, not a detailed-mode extra: tracked weight above 1,600
coins means the character cannot move, under both tracking modes. Inventory itself is
never capped.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from osrlib.core.classes import ArmourPolicyKind, ClassDefinition, WeaponPolicyKind
from osrlib.core.dice import parse
from osrlib.core.ruleset import EncumbranceMode, Ruleset
from osrlib.core.validation import Rejection

__all__ = [
    "BASE_MOVEMENT_FEET",
    "COIN_VALUES_CP",
    "MAX_LOAD_COINS",
    "MISC_GEAR_WEIGHT_COINS",
    "AmmunitionTemplate",
    "ArmourCategory",
    "ArmourTemplate",
    "CoinPurse",
    "CombatFacet",
    "EquipmentCatalog",
    "GearTemplate",
    "Inventory",
    "ItemInstance",
    "Material",
    "MissileRanges",
    "RangeBand",
    "TreasureWeight",
    "WeaponQuality",
    "WeaponTemplate",
    "encounter_movement_rate",
    "equip",
    "equipment_weight_coins",
    "movement_rate_feet",
    "purchase",
    "tracked_weight_coins",
    "treasure_weight_coins",
    "unequip",
    "validate_equip",
    "validate_purchase",
]

MAX_LOAD_COINS = 1600
"""The maximum load any character can carry; above it, movement is 0."""

BASE_MOVEMENT_FEET = 120
"""The default movement rate, feet per exploration turn: 120' (40')."""

MISC_GEAR_WEIGHT_COINS = 80
"""Detailed encumbrance's flat weight for carrying any miscellaneous gear (pinned)."""

COIN_VALUES_CP = {"pp": 500, "gp": 100, "ep": 50, "sp": 10, "cp": 1}
"""Coin values in copper pieces, from the SRD's Wealth conversion table."""


class WeaponQuality(StrEnum):
    """The SRD's weapon qualities; execution of most arrives with Phase 2 combat."""

    BLUNT = "blunt"
    BRACE = "brace"
    CHARGE = "charge"
    MELEE = "melee"
    MISSILE = "missile"
    RELOAD = "reload"
    SLOW = "slow"
    SPLASH = "splash"
    TWO_HANDED = "two_handed"


class Material(StrEnum):
    """Weapon material — silver matters to Phase 2's damage resolution. Extensible."""

    STANDARD = "standard"
    SILVER = "silver"


class ArmourCategory(StrEnum):
    """Basic-encumbrance armour categories; unarmoured is the absence of worn armour."""

    LIGHT = "light"
    HEAVY = "heavy"


class RangeBand(BaseModel):
    """One missile range band in feet, as the SRD prints it (`5'–80'`)."""

    model_config = ConfigDict(frozen=True)

    min_feet: int = Field(ge=0)
    max_feet: int = Field(ge=0)

    @model_validator(mode="after")
    def _band_must_be_ordered(self) -> RangeBand:
        if self.min_feet > self.max_feet:
            raise ValueError(f"range band minimum {self.min_feet} exceeds maximum {self.max_feet}")
        return self


class MissileRanges(BaseModel):
    """A missile weapon's short (+1 to hit), medium, and long (−1 to hit) range bands."""

    model_config = ConfigDict(frozen=True)

    short: RangeBand
    medium: RangeBand
    long: RangeBand


class WeaponTemplate(BaseModel):
    """A mundane weapon from the SRD's Weapon Combat Stats table."""

    model_config = ConfigDict(frozen=True)

    item_type: Literal["weapon"] = "weapon"
    id: str
    name: str
    cost_gp: int = Field(ge=0)
    weight_coins: int = Field(ge=0)
    damage: str
    qualities: tuple[WeaponQuality, ...]
    missile_ranges: MissileRanges | None = None
    material: Material = Material.STANDARD
    overrides_applied: tuple[str, ...] = ()

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
    """An armour row: body armour with dual-format AC, or the shield with its bonus."""

    model_config = ConfigDict(frozen=True)

    item_type: Literal["armour"] = "armour"
    id: str
    name: str
    cost_gp: int = Field(ge=0)
    weight_coins: int = Field(ge=0)
    ac: int | None = None
    ac_ascending: int | None = None
    ac_bonus: int | None = None
    category: ArmourCategory | None = None
    overrides_applied: tuple[str, ...] = ()

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
        """Whether this row is the shield (an AC bonus rather than a base AC)."""
        return self.ac_bonus is not None


class CombatFacet(BaseModel):
    """The combat statistics embedded in a gear item (torch, holy water, burning oil)."""

    model_config = ConfigDict(frozen=True)

    damage: str
    qualities: tuple[WeaponQuality, ...]
    missile_ranges: MissileRanges | None = None

    @field_validator("damage")
    @classmethod
    def _damage_must_parse(cls, value: str) -> str:
        parse(value)
        return value


class GearTemplate(BaseModel):
    """An adventuring gear item.

    `lot_size` is the purchase lot (six torches for 1 gp buys a quantity of 6);
    `capacity_coins` is container capacity where the SRD gives one (backpack, sacks);
    `combat` is the embedded combat facet for the three dual-listed items.
    """

    model_config = ConfigDict(frozen=True)

    item_type: Literal["gear"] = "gear"
    id: str
    name: str
    cost_gp: int = Field(ge=0)
    lot_size: int = Field(default=1, ge=1)
    capacity_coins: int | None = None
    combat: CombatFacet | None = None
    overrides_applied: tuple[str, ...] = ()


class AmmunitionTemplate(BaseModel):
    """An ammunition row.

    Ammunition weight is 0 (pinned): the SRD's missile weapon weights already include
    the ammunition and its container, and the ammunition table has no weight column.
    """

    model_config = ConfigDict(frozen=True)

    item_type: Literal["ammunition"] = "ammunition"
    id: str
    name: str
    cost_gp: int = Field(ge=0)
    lot_size: int = Field(default=1, ge=1)
    weight_coins: int = Field(default=0, ge=0)
    material: Material = Material.STANDARD
    overrides_applied: tuple[str, ...] = ()


ItemTemplate = Annotated[
    WeaponTemplate | ArmourTemplate | GearTemplate | AmmunitionTemplate,
    Field(discriminator="item_type"),
]
"""Any equipment template, discriminated by `item_type`."""


class TreasureWeight(BaseModel):
    """A treasure encumbrance row (coin, gem, jewellery, ...); treasure itself is Phase 5."""

    model_config = ConfigDict(frozen=True)

    id: str
    weight_coins: int = Field(ge=0)


class EquipmentCatalog(BaseModel):
    """The loaded equipment lists, with id lookup across all four."""

    model_config = ConfigDict(frozen=True)

    weapons: tuple[WeaponTemplate, ...]
    armour: tuple[ArmourTemplate, ...]
    gear: tuple[GearTemplate, ...]
    ammunition: tuple[AmmunitionTemplate, ...]
    treasure_weights: tuple[TreasureWeight, ...]

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
        """Return the template with `item_id` from any of the four lists.

        Args:
            item_id: The item id, e.g. `"sword"` or `"torch"`.

        Returns:
            The template.

        Raises:
            ValueError: If no item has that id.
        """
        for template in (*self.weapons, *self.armour, *self.gear, *self.ammunition):
            if template.id == item_id:
                return template
        raise ValueError(f"unknown item id {item_id!r}")


class ItemInstance(BaseModel):
    """A mutable owned item spawned from a frozen template.

    `quantity` counts individual units: buying one lot of torches yields one instance
    with quantity 6.
    """

    model_config = ConfigDict(validate_assignment=True)

    template: ItemTemplate
    quantity: int = Field(default=1, ge=1)


class CoinPurse(BaseModel):
    """Coins by denomination. Each coin weighs 1, whatever its metal.

    Payment consumes denominations smallest-first (cp, sp, ep, gp, pp) until the cost
    is covered; change for any overpayment returns in the fewest coins, largest
    denominations first. The algorithm is pinned — purse contents after a purchase are
    deterministic and value-preserving.
    """

    model_config = ConfigDict(validate_assignment=True)

    pp: int = Field(default=0, ge=0)
    gp: int = Field(default=0, ge=0)
    ep: int = Field(default=0, ge=0)
    sp: int = Field(default=0, ge=0)
    cp: int = Field(default=0, ge=0)

    @property
    def value_cp(self) -> int:
        """The purse's total value in copper pieces."""
        return sum(getattr(self, denomination) * value for denomination, value in COIN_VALUES_CP.items())

    @property
    def total_coins(self) -> int:
        """How many coins the purse holds — its weight in coins."""
        return self.pp + self.gp + self.ep + self.sp + self.cp

    def can_afford(self, cost_gp: int) -> bool:
        """Whether the purse's total value covers a cost in gold pieces.

        Args:
            cost_gp: The cost in whole gold pieces. Non-negative.

        Returns:
            True if the purse's value in cp is at least the cost's.

        Raises:
            ValueError: If `cost_gp` is negative.
        """
        if cost_gp < 0:
            raise ValueError(f"cost must be non-negative, got {cost_gp}")
        return self.value_cp >= cost_gp * 100

    def spend(self, cost_gp: int) -> None:
        """Pay a cost in gold pieces, making change per the pinned algorithm.

        Args:
            cost_gp: The cost in whole gold pieces. Non-negative.

        Raises:
            ValueError: If `cost_gp` is negative or the purse cannot cover it —
                validate with [`can_afford`][osrlib.core.items.CoinPurse.can_afford]
                first; overspending is programmer misuse.
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


class Inventory(BaseModel):
    """A character's carried items, coins, and equipped state.

    The item list is ordered (a defined order everywhere, per the determinism
    contract). Equipping moves an instance out of `items` into its slot, so each
    instance lives in exactly one place.
    """

    model_config = ConfigDict(validate_assignment=True)

    items: list[ItemInstance] = []
    purse: CoinPurse = CoinPurse()
    worn_armour: ItemInstance | None = None
    shield: ItemInstance | None = None
    wielded: list[ItemInstance] = []

    def all_instances(self) -> list[ItemInstance]:
        """Return every carried instance — the item list plus the equipped slots."""
        equipped: list[ItemInstance] = []
        if self.worn_armour is not None:
            equipped.append(self.worn_armour)
        if self.shield is not None:
            equipped.append(self.shield)
        return [*self.items, *equipped, *self.wielded]


def treasure_weight_coins(inventory: Inventory) -> int:
    """Return the weight of carried treasure in coins.

    In Phase 1 treasure is coins, weighing 1 each; treasure items (gems, jewellery)
    arrive with Phase 5 and will add their `equipment.json` treasure weights here.

    Args:
        inventory: The inventory to weigh.

    Returns:
        The treasure weight in coins.
    """
    return inventory.purse.total_coins


def equipment_weight_coins(inventory: Inventory) -> int:
    """Return detailed-encumbrance equipment weight: weapons, armour, and the gear flat.

    Weapons and armour weigh their listed weights; ammunition weighs 0 (included in
    the missile weapon's listed weight, pinned); miscellaneous gear counts as a flat
    80 coins when any is carried (pinned — the SRD gives gear no per-item weights).

    Args:
        inventory: The inventory to weigh.

    Returns:
        The equipment weight in coins.
    """
    total = 0
    has_gear = False
    for instance in inventory.all_instances():
        template = instance.template
        if isinstance(template, WeaponTemplate | ArmourTemplate | AmmunitionTemplate):
            total += template.weight_coins * instance.quantity
        else:
            has_gear = True
    if has_gear:
        total += MISC_GEAR_WEIGHT_COINS
    return total


def tracked_weight_coins(inventory: Inventory, mode: EncumbranceMode) -> int:
    """Return the weight the given encumbrance mode tracks.

    Args:
        inventory: The inventory to weigh.
        mode: The encumbrance mode in play.

    Returns:
        0 under `none` (nothing is tracked), treasure weight under `basic`, and
        treasure plus equipment weight under `detailed`.
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


def movement_rate_feet(inventory: Inventory, ruleset: Ruleset, carrying_treasure: bool = False) -> int:
    """Return the movement rate in feet per exploration turn.

    Per the `Ruleset` encumbrance flag:

    - `none`: always 120; nothing is tracked and no load cap applies.
    - `basic`: rate by worn-armour category (unarmoured/light/heavy) and the
      `carrying_treasure` judgment — significant treasure is a referee call in RAW, so
      it stays one: the game sets the flag, no invented threshold. Treasure weight
      (coins included) is still tracked against the 1,600-coin maximum load.
    - `detailed`: rate by total tracked weight; the SRD's thresholds are inclusive
      ("up to").

    In both tracking modes, tracked weight above the 1,600-coin maximum load means the
    character cannot move (movement 0).

    Args:
        inventory: The inventory to weigh.
        ruleset: The ruleset whose encumbrance flag governs.
        carrying_treasure: Basic mode's referee judgment: is the character carrying a
            significant amount of treasure?

    Returns:
        The movement rate in feet per turn: 120, 90, 60, 30, or 0.
    """
    mode = ruleset.encumbrance
    if mode is EncumbranceMode.NONE:
        return BASE_MOVEMENT_FEET
    tracked = tracked_weight_coins(inventory, mode)
    if tracked > MAX_LOAD_COINS:
        return 0
    if mode is EncumbranceMode.BASIC:
        category = None
        if inventory.worn_armour is not None and isinstance(inventory.worn_armour.template, ArmourTemplate):
            category = inventory.worn_armour.template.category
        without, with_treasure = _BASIC_RATES[category]
        return with_treasure if carrying_treasure else without
    for threshold, rate in _DETAILED_RATES:
        if tracked <= threshold:
            return rate
    return 0  # unreachable: tracked > MAX_LOAD_COINS returned above


def encounter_movement_rate(base_rate_feet: int) -> int:
    """Return the encounter movement rate: base ÷ 3, computed, never stored.

    Args:
        base_rate_feet: The base movement rate in feet per turn.

    Returns:
        The per-round encounter rate in feet.
    """
    if base_rate_feet < 0:
        raise ValueError(f"movement rate must be non-negative, got {base_rate_feet}")
    return base_rate_feet // 3


def validate_purchase(purse: CoinPurse, template: ItemTemplate, lots: int = 1) -> list[Rejection]:
    """Validate buying `lots` purchase lots of an item.

    Args:
        purse: The buyer's purse.
        template: The item to buy.
        lots: How many purchase lots. Positive.

    Returns:
        Structured rejections; empty when the purchase is legal.

    Raises:
        ValueError: If `lots` is not positive.
    """
    if lots < 1:
        raise ValueError(f"lots must be positive, got {lots}")
    cost = template.cost_gp * lots
    if not purse.can_afford(cost):
        return [Rejection(code="items.purchase.insufficient_funds", params={"item": template.id, "cost_gp": cost})]
    return []


def purchase(inventory: Inventory, template: ItemTemplate, lots: int = 1) -> ItemInstance:
    """Buy `lots` purchase lots of an item, paying from the purse.

    Gear and ammunition arrive in lot-sized quantities (one lot of torches is 6);
    weapons and armour have no lot size, so `lots` is the quantity. The new instance
    is appended to the item list.

    Args:
        inventory: The buyer's inventory; mutated.
        template: The item to buy.
        lots: How many purchase lots. Positive.

    Returns:
        The purchased instance.

    Raises:
        ValueError: If the purchase fails
            [`validate_purchase`][osrlib.core.items.validate_purchase] — buying what
            you cannot afford is programmer misuse.
    """
    rejections = validate_purchase(inventory.purse, template, lots)
    if rejections:
        raise ValueError(f"illegal purchase: {[rejection.code for rejection in rejections]}")
    inventory.purse.spend(template.cost_gp * lots)
    lot_size = template.lot_size if isinstance(template, GearTemplate | AmmunitionTemplate) else 1
    instance = ItemInstance(template=template, quantity=lot_size * lots)
    inventory.items.append(instance)
    return instance


def validate_equip(definition: ClassDefinition, instance: ItemInstance) -> list[Rejection]:
    """Validate equipping an item against the class's armour and weapon policies.

    Weapon policies govern the weapons list only: gear carrying a combat facet (torch,
    holy water, burning oil) is exempt and always equippable (pinned — see
    `docs/adaptations.md`, including the magic-user consequence). Gear without a
    facet, and ammunition, is not equippable at all.

    Args:
        definition: The character's class definition.
        instance: The instance to equip.

    Returns:
        Structured rejections; empty when equipping is legal.
    """
    template = instance.template
    if isinstance(template, ArmourTemplate):
        if template.is_shield:
            if not definition.armour.shields_allowed:
                return [Rejection(code="items.equip.shield_forbidden", params={"class": definition.id})]
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
        return []
    if isinstance(template, GearTemplate) and template.combat is not None:
        return []
    return [Rejection(code="items.equip.not_equippable", params={"item": template.id})]


def equip(inventory: Inventory, definition: ClassDefinition, instance: ItemInstance) -> None:
    """Equip an item from the inventory's item list.

    Body armour goes to the worn-armour slot and the shield to the shield slot — a
    previous occupant returns to the item list. Weapons and combat-facet gear join the
    wielded list. Slot-conflict rules (two-handed weapons versus shields) are Phase 2
    combat's concern.

    Args:
        inventory: The inventory; mutated.
        definition: The character's class definition.
        instance: The instance to equip; must be in the item list.

    Raises:
        ValueError: If the instance is not in the item list, or equipping it fails
            [`validate_equip`][osrlib.core.items.validate_equip].
    """
    if not any(existing is instance for existing in inventory.items):
        raise ValueError("only an instance in the inventory's item list can be equipped")
    rejections = validate_equip(definition, instance)
    if rejections:
        raise ValueError(f"illegal equip: {[rejection.code for rejection in rejections]}")
    inventory.items.remove(instance)
    template = instance.template
    if isinstance(template, ArmourTemplate):
        if template.is_shield:
            if inventory.shield is not None:
                inventory.items.append(inventory.shield)
            inventory.shield = instance
        else:
            if inventory.worn_armour is not None:
                inventory.items.append(inventory.worn_armour)
            inventory.worn_armour = instance
    else:
        inventory.wielded.append(instance)


def unequip(inventory: Inventory, instance: ItemInstance) -> None:
    """Return an equipped instance to the item list.

    Args:
        inventory: The inventory; mutated.
        instance: The equipped instance.

    Raises:
        ValueError: If the instance is not equipped.
    """
    if inventory.worn_armour is instance:
        inventory.worn_armour = None
    elif inventory.shield is instance:
        inventory.shield = None
    elif any(existing is instance for existing in inventory.wielded):
        inventory.wielded.remove(instance)
    else:
        raise ValueError("instance is not equipped")
    inventory.items.append(instance)
