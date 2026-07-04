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
from osrlib.core.effects import ModifierSpec
from osrlib.core.rng import RngStream
from osrlib.core.ruleset import EncumbranceMode, Ruleset
from osrlib.core.treasure import MagicItemType, TreasureEntry
from osrlib.core.validation import Rejection

__all__ = [
    "BASE_MOVEMENT_FEET",
    "COIN_VALUES_CP",
    "MAX_LOAD_COINS",
    "MAX_RINGS_WORN",
    "MISC_GEAR_WEIGHT_COINS",
    "AmmunitionTemplate",
    "AnyInstance",
    "ArmourCategory",
    "ArmourTemplate",
    "ArmourTypeRow",
    "CoinPurse",
    "Coins",
    "CombatFacet",
    "EquipmentCatalog",
    "GearTemplate",
    "GeneratedTreasure",
    "Inventory",
    "ItemInstance",
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
"""RAW's ring cap: one on each hand — a third is rejected (more than two = none function)."""

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
    `combat` is the embedded combat facet for the three dual-listed items. `params`
    carries structured exploration mechanics pinned from `Adventuring_Gear.md` (a
    torch's `burn_turns` and `light_radius_feet`, the tinder box's
    `light_chance_in_six`), consumed by the Phase 4 crawl procedures.
    """

    model_config = ConfigDict(frozen=True)

    item_type: Literal["gear"] = "gear"
    id: str
    name: str
    cost_gp: int = Field(ge=0)
    lot_size: int = Field(default=1, ge=1)
    capacity_coins: int | None = None
    combat: CombatFacet | None = None
    params: dict[str, int | str | bool] = {}
    overrides_applied: tuple[str, ...] = ()


class AmmunitionTemplate(BaseModel):
    """An ammunition row.

    Ammunition weight is 0 (pinned): the SRD's missile weapon weights already include
    the ammunition and its container, and the ammunition table has no weight column.
    Sling stones' cost of `Free` compiles to cost 0 with lot size 1 (pinned).
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


class MagicItemCategory(StrEnum):
    """The magic item catalog's categories — the master table's types, devices split.

    The master *Magic Item Type* table's `rod_staff_wand` type covers three catalog
    categories; every other type maps to one.
    """

    ARMOUR = "armour"
    MISC = "misc"
    POTION = "potion"
    RING = "ring"
    ROD = "rod"
    STAFF = "staff"
    WAND = "wand"
    SCROLL = "scroll"
    SWORD = "sword"
    WEAPON = "weapon"


class VersusBonus(BaseModel):
    """A `+2 vs Lycanthropes` clause: the printed label and its resolved targets.

    `bonus` is the alternate attack-and-damage bonus that replaces the item's base
    bonus against a matching target. Targets resolve structurally, never by
    string-matching prose: `categories` name monster category tags (`undead`,
    `enchanted`) and `template_ids` name compiled monster ids (the lycanthrope set,
    the ability-derived spell-user and regenerating sets, the dagger's
    orcs/goblins/kobolds). A clause matches a target whose template carries any
    listed category or id; characters have no template and never match (pinned).
    """

    model_config = ConfigDict(frozen=True)

    label: str = Field(min_length=1)
    bonus: int
    categories: tuple[str, ...] = ()
    template_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _targets_must_resolve(self) -> VersusBonus:
        if not self.categories and not self.template_ids:
            raise ValueError(f"versus clause {self.label!r} resolves no categories or template ids")
        return self


class UsableBy(BaseModel):
    """Who may use a magic item.

    `all` is the default ("All characters (unless noted)"); `caster` restricts to
    spell casters of `caster` kind (`arcane` for wands, per staff page otherwise);
    `classes` restricts to the named class ids. Swords, weapons, and armour stay
    `all` — "per normal class restrictions" resolves through the base item's
    equip policies, not here.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["all", "classes", "caster"] = "all"
    class_ids: tuple[str, ...] = ()
    caster: Literal["arcane", "divine", "any"] | None = None

    @model_validator(mode="after")
    def _shape_must_match_kind(self) -> UsableBy:
        if self.kind == "classes" and not self.class_ids:
            raise ValueError("a 'classes' usability names at least one class id")
        if (self.kind == "caster") != (self.caster is not None):
            raise ValueError("a caster kind is present exactly when the usability kind is 'caster'")
        return self


class MagicItemEffect(BaseModel):
    """The structured mechanics of a wired magic item — the Phase 5 wired census.

    `kind` names the kernel behavior that executes it (`worn_modifiers`, `potion`,
    `damage_area`, `condition_area`, `healing`, `save_or_die`, `on_hit_drain`,
    `striking`, `ward`, `regeneration`, `light`); everything else in the catalog is
    `manual`-tagged prose. Fields are the union the behaviors read — dice, element,
    save, shape and dimensions, duration — with `params` carrying per-item scalars.
    """

    model_config = ConfigDict(frozen=True)

    kind: str = Field(min_length=1)
    modifiers: tuple[ModifierSpec, ...] = ()
    condition: str | None = None
    damage_dice: str | None = None
    heal_dice: str | None = None
    element: str | None = None
    save_category: str | None = None
    save_on: Literal["negates", "half"] | None = None
    shape: str | None = None
    dimensions: dict[str, int] = {}
    range_feet: int | None = None
    duration_unit: str | None = None
    duration_amount: int | None = None
    duration_dice: str | None = None
    params: dict[str, int | str | bool | tuple[int | str, ...]] = {}

    @field_validator("damage_dice", "heal_dice", "duration_dice")
    @classmethod
    def _dice_must_parse(cls, value: str | None) -> str | None:
        if value is not None:
            parse(value)
        return value


class ScrollCurse(BaseModel):
    """One of the cursed scroll's six example curses, compiled as a data row.

    `wired=True` marks the two the kernel resolves (energy drain through
    `drain_levels` with the curse's own halfway XP policy, slow healing through the
    Phase 2 slowed-healing hooks); the rest are `manual` prose carried on the event.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    prose: str
    wired: bool = False


class MagicItemTemplate(BaseModel):
    """A magic item, compiled from the generation tables and per-item pages.

    Frozen SRD data: play spawns mutable
    [`MagicItemInstance`][osrlib.core.items.MagicItemInstance]s. `base_item_id` is
    the mundane `equipment.json` template an enchanted arm overlays (sword, dagger,
    chainmail, shield, arrows); generic armour outcomes leave it `None` — the
    *Magic Armour Type* d8 sets the instance's base at generation. Bonuses are
    negative for cursed items; the cursed `AC 9 [10]` forms carry `ac_set` /
    `ac_set_ascending` instead. `charges_dice` rolls at generation (referee-only
    forever after); `quantity_dice` sizes ammunition (sub-table rows may override
    per printed band). `weight_coins` is the base item's weight — enchanted armour
    at half per RAW, potions/scrolls/devices from the `TreasureWeight` rows.
    `hoard_recipe` is a treasure map's compiled hoard; `curses` is the cursed
    scroll's example-curse table.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    category: MagicItemCategory
    base_item_id: str | None = None
    attack_bonus: int = 0
    damage_bonus: int = 0
    ac_bonus: int = 0
    ac_set: int | None = None
    ac_set_ascending: int | None = None
    versus: tuple[VersusBonus, ...] = ()
    cursed: bool = False
    charges_dice: str | None = None
    quantity_dice: str | None = None
    usable_by: UsableBy = UsableBy()
    always_active: bool = False
    effect: MagicItemEffect | None = None
    params: dict[str, int | str | bool | tuple[int | str, ...]] = {}
    manual: tuple[str, ...] = ()
    weight_coins: int = Field(default=0, ge=0)
    hoard_recipe: tuple[TreasureEntry, ...] = ()
    curses: tuple[ScrollCurse, ...] = ()
    overrides_applied: tuple[str, ...] = ()

    @field_validator("charges_dice", "quantity_dice")
    @classmethod
    def _dice_must_parse(cls, value: str | None) -> str | None:
        if value is not None:
            parse(value)
        return value


class MagicSubTableRow(BaseModel):
    """One outcome row of a category generation sub-table.

    `basic_value` is the sparse small-die B column face (`None` when the printed
    cell is blank — B and X are independent index spaces over the same outcome
    list); `expert_min`/`expert_max` are the full d% X band (`00` reads as 100).
    `item_ids` is usually one id; the armour-with-shield rows are two-item bundles.
    `params` carries per-band generation data (the ring wish-count dice, the arrow
    and bolt quantity dice) that overrides the template's own fields.
    """

    model_config = ConfigDict(frozen=True)

    item_ids: tuple[str, ...] = Field(min_length=1)
    basic_value: int | None = None
    expert_min: int = Field(ge=1, le=100)
    expert_max: int = Field(ge=1, le=100)
    params: dict[str, int | str | bool | tuple[int | str, ...]] = {}


class MagicSubTable(BaseModel):
    """One category's generation sub-table: the sparse B column and the full X column."""

    model_config = ConfigDict(frozen=True)

    category: MagicItemType
    basic_die: int = Field(ge=2)
    rows: tuple[MagicSubTableRow, ...] = Field(min_length=1)

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
        """Return the row a Basic-tier small-die roll selects.

        Args:
            roll: The small-die result, 1 through `basic_die`.

        Returns:
            The selected row.

        Raises:
            ValueError: If no row carries that face.
        """
        for row in self.rows:
            if row.basic_value == roll:
                return row
        raise ValueError(f"{self.category} basic roll must be 1-{self.basic_die}, got {roll}")

    def row_for_expert(self, roll: int) -> MagicSubTableRow:
        """Return the row an Expert-tier d% roll selects.

        Args:
            roll: The d% result, 1–100.

        Returns:
            The selected row.

        Raises:
            ValueError: If the roll is outside 1–100.
        """
        for row in self.rows:
            if row.expert_min <= roll <= row.expert_max:
                return row
        raise ValueError(f"{self.category} expert roll must be 1-100, got {roll}")


class ArmourTypeRow(BaseModel):
    """One d8 band of the *Magic Armour Type* table."""

    model_config = ConfigDict(frozen=True)

    roll_min: int = Field(ge=1, le=8)
    roll_max: int = Field(ge=1, le=8)
    base_item_id: str


class MagicArmourTypeTable(BaseModel):
    """The *Magic Armour Type* d8 table: what a generated `Armour +N` is made of."""

    model_config = ConfigDict(frozen=True)

    rows: tuple[ArmourTypeRow, ...] = Field(min_length=1)

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
        """Return the base armour id a d8 roll selects.

        Args:
            roll: The d8 result, 1–8.

        Returns:
            The mundane armour template id.

        Raises:
            ValueError: If the roll is outside 1–8.
        """
        for row in self.rows:
            if row.roll_min <= roll <= row.roll_max:
                return row.base_item_id
        raise ValueError(f"armour type roll must be 1-8, got {roll}")


class ScrollSpellLevelRow(BaseModel):
    """One row of the *Random Scroll Spell Level* table.

    The B column here is d6 *bands* (`1–3`), not sparse faces — `None` bounds mark
    the Expert-only rows.
    """

    model_config = ConfigDict(frozen=True)

    basic_min: int | None = None
    basic_max: int | None = None
    expert_min: int = Field(ge=1, le=100)
    expert_max: int = Field(ge=1, le=100)
    arcane_level: int = Field(ge=1, le=6)
    divine_level: int = Field(ge=1, le=5)


class ScrollSpellLevelTable(BaseModel):
    """The *Random Scroll Spell Level* table, with its arcane and divine columns."""

    model_config = ConfigDict(frozen=True)

    rows: tuple[ScrollSpellLevelRow, ...] = Field(min_length=1)

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
        """Return the spell level a Basic-tier d6 roll selects.

        Args:
            roll: The d6 result, 1–6.
            divine: True for the divine column.

        Returns:
            The spell level.

        Raises:
            ValueError: If no band covers the roll.
        """
        for row in self.rows:
            if row.basic_min is not None and row.basic_min <= roll <= (row.basic_max or 0):
                return row.divine_level if divine else row.arcane_level
        raise ValueError(f"scroll spell level basic roll must be 1-6, got {roll}")

    def level_for_expert(self, roll: int, *, divine: bool) -> int:
        """Return the spell level an Expert-tier d% roll selects.

        Args:
            roll: The d% result, 1–100.
            divine: True for the divine column.

        Returns:
            The spell level.

        Raises:
            ValueError: If the roll is outside 1–100.
        """
        for row in self.rows:
            if row.expert_min <= roll <= row.expert_max:
                return row.divine_level if divine else row.arcane_level
        raise ValueError(f"scroll spell level roll must be 1-100, got {roll}")


class SwordCommunicationRow(BaseModel):
    """One INT row of the sentient sword *Communication* table."""

    model_config = ConfigDict(frozen=True)

    int_score: int = Field(ge=7, le=12)
    reading: bool
    communication: str


class SwordPowersRow(BaseModel):
    """One INT row of the sentient sword *Powers* table."""

    model_config = ConfigDict(frozen=True)

    int_score: int = Field(ge=7, le=12)
    sensory: int = Field(ge=0)
    extraordinary: int = Field(ge=0)


class SwordTableBand(BaseModel):
    """One die band of a sentient-sword roll table; `result` is a slug or directive.

    Directives: `roll_twice` (languages and both power tables), `roll_thrice`
    (extraordinary 00), `roll_extraordinary` (sensory 96–99).
    """

    model_config = ConfigDict(frozen=True)

    roll_min: int = Field(ge=1)
    roll_max: int = Field(ge=1)
    result: str = Field(min_length=1)


class SwordPower(BaseModel):
    """One sentient-sword power: `manual`-tagged prose data (automation is out of 1.0)."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    prose: str
    extraordinary: bool = False
    duplicates_allowed: bool = False


class SentientSwordTables(BaseModel):
    """The sentient-sword generation tables, compiled as data.

    Generation order is pinned on the page's own procedure: the special-purpose
    1-in-20 first (a special sword is always sentient at INT 12/Ego 12), otherwise
    the 30% sentience roll, then INT 1d6+6, communication, languages, alignment,
    powers, and Ego 1d12, in the printed order.
    """

    model_config = ConfigDict(frozen=True)

    communication: tuple[SwordCommunicationRow, ...]
    languages: tuple[SwordTableBand, ...]
    alignment: tuple[SwordTableBand, ...]
    powers: tuple[SwordPowersRow, ...]
    sensory_bands: tuple[SwordTableBand, ...]
    extraordinary_bands: tuple[SwordTableBand, ...]
    powers_catalog: tuple[SwordPower, ...]
    special_purposes: tuple[SwordTableBand, ...]
    special_purpose_prose: str = ""
    alignment_touch_prose: str = ""

    def power(self, power_id: str) -> SwordPower:
        """Return the power with `power_id`.

        Args:
            power_id: The power id, e.g. `"detect_magic"`.

        Returns:
            The power.

        Raises:
            ValueError: If no power has that id.
        """
        for power in self.powers_catalog:
            if power.id == power_id:
                return power
        raise ValueError(f"unknown sword power {power_id!r}")


class MagicItemCatalog(BaseModel):
    """The loaded magic item catalog: templates, sub-tables, and the sword tables."""

    model_config = ConfigDict(frozen=True)

    items: tuple[MagicItemTemplate, ...]
    sub_tables: tuple[MagicSubTable, ...]
    armour_type: MagicArmourTypeTable
    scroll_spell_levels: ScrollSpellLevelTable
    sentient_swords: SentientSwordTables

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

        Args:
            item_id: The item id, e.g. `"potion_of_healing"`.

        Returns:
            The template.

        Raises:
            ValueError: If no item has that id.
        """
        for template in self.items:
            if template.id == item_id:
                return template
        raise ValueError(f"unknown magic item id {item_id!r}")

    def sub_table(self, category: MagicItemType) -> MagicSubTable:
        """Return the generation sub-table for a master-table type.

        Args:
            category: The master-table type.

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
    """A sentient sword's rolled qualities — generation data fixed at creation."""

    model_config = ConfigDict(frozen=True)

    intelligence: int = Field(ge=7, le=12)
    ego: int = Field(ge=1, le=12)
    communication: str
    reading: bool
    alignment: str
    languages: int = Field(default=0, ge=0)
    sensory_powers: tuple[str, ...] = ()
    extraordinary_powers: tuple[str, ...] = ()
    special_purpose: str | None = None


class MagicItemInstance(BaseModel):
    """A mutable owned magic item spawned from a frozen template.

    `charges_remaining` is referee-only (RAW: undiscoverable) and `None` for
    uncharged items; `quantity` counts ammunition; `identified` gates the player
    view's masking; `base_item_id` is the generated base for generic armour
    outcomes (the *Magic Armour Type* d8) and the template's own base otherwise;
    `state` is per-item memory (the energy-drain sword's remaining drains, the
    staff of healing's per-target days, a multi-spell scroll's remaining spells).
    """

    model_config = ConfigDict(validate_assignment=True)

    instance_type: Literal["magic_item"] = "magic_item"
    instance_id: str
    template_id: str
    charges_remaining: int | None = None
    quantity: int = Field(default=1, ge=0)
    identified: bool = False
    cursed_revealed: bool = False
    base_item_id: str | None = None
    sentience: SwordSentience | None = None
    state: dict[str, int | str | bool | tuple[int | str, ...]] = {}


class ItemInstance(BaseModel):
    """A mutable owned item spawned from a frozen template.

    `quantity` counts individual units: buying one lot of torches yields one instance
    with quantity 6.
    """

    model_config = ConfigDict(validate_assignment=True)

    instance_type: Literal["item"] = "item"
    template: ItemTemplate
    quantity: int = Field(default=1, ge=1)


class Coins(BaseModel):
    """A frozen coin bundle: generated treasure, cache contents, dropped piles."""

    model_config = ConfigDict(frozen=True)

    pp: int = Field(default=0, ge=0)
    gp: int = Field(default=0, ge=0)
    ep: int = Field(default=0, ge=0)
    sp: int = Field(default=0, ge=0)
    cp: int = Field(default=0, ge=0)

    @property
    def total_coins(self) -> int:
        """How many coins the bundle holds."""
        return self.pp + self.gp + self.ep + self.sp + self.cp

    @property
    def value_cp(self) -> int:
        """The bundle's total value in copper pieces — the award math's exact unit."""
        return sum(getattr(self, denomination) * value for denomination, value in COIN_VALUES_CP.items())

    @property
    def value_gp(self) -> int:
        """The bundle's value in whole gold pieces, floored — the 1-gp-=-1-XP input."""
        return self.value_cp // 100


class ValuableInstance(BaseModel):
    """A gem or piece of jewellery, its value rolled at generation and fixed.

    Appraisal is instantaneous and exact (pinned): B/X prices treasure for the XP
    economy, and a haggling or appraisal minigame is game territory (registered).
    `weight_coins` comes from the `TreasureWeight` rows at generation.
    """

    model_config = ConfigDict(validate_assignment=True)

    instance_type: Literal["valuable"] = "valuable"
    instance_id: str
    kind: Literal["gem", "jewellery"]
    name: str = ""
    value_gp: int = Field(ge=0)
    weight_coins: int = Field(default=0, ge=0)


class GeneratedTreasure(BaseModel):
    """One generation's output: coins, valuables, and magic item instances."""

    model_config = ConfigDict(frozen=True)

    coins: Coins = Coins()
    valuables: tuple[ValuableInstance, ...] = ()
    magic_items: tuple[MagicItemInstance, ...] = ()


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


AnyInstance = Annotated[
    ItemInstance | MagicItemInstance,
    Field(discriminator="instance_type"),
]
"""Any owned instance — mundane or magic — discriminated by `instance_type`."""


class Inventory(BaseModel):
    """A character's carried items, coins, valuables, and equipped state.

    The item list is ordered (a defined order everywhere, per the determinism
    contract). Equipping moves an instance out of `items` into its slot, so each
    instance lives in exactly one place. Magic items join the item list and the
    equipped slots as union members; `rings` are the two worn-ring slots (RAW: one
    on each hand — the cap is enforced at equip validation); `valuables` are carried
    gems and jewellery.
    """

    model_config = ConfigDict(validate_assignment=True)

    items: list[AnyInstance] = []
    purse: CoinPurse = CoinPurse()
    valuables: list[ValuableInstance] = []
    worn_armour: AnyInstance | None = None
    shield: AnyInstance | None = None
    wielded: list[AnyInstance] = []
    rings: list[MagicItemInstance] = []

    def all_instances(self) -> list[ItemInstance | MagicItemInstance]:
        """Return every carried instance — the item list plus the equipped slots."""
        equipped: list[ItemInstance | MagicItemInstance] = []
        if self.worn_armour is not None:
            equipped.append(self.worn_armour)
        if self.shield is not None:
            equipped.append(self.shield)
        return [*self.items, *equipped, *self.wielded, *self.rings]

    def equipped_instances(self) -> list[ItemInstance | MagicItemInstance]:
        """Return every equipped instance, slots first then wielded then rings."""
        equipped: list[ItemInstance | MagicItemInstance] = []
        if self.worn_armour is not None:
            equipped.append(self.worn_armour)
        if self.shield is not None:
            equipped.append(self.shield)
        return [*equipped, *self.wielded, *self.rings]

    def magic_item(self, instance_id: str) -> MagicItemInstance | None:
        """Return the carried magic item with `instance_id`, or `None`.

        Args:
            instance_id: The instance id, e.g. `"magic-item-0003"`.

        Returns:
            The instance, wherever it is carried or equipped.
        """
        for instance in self.all_instances():
            if isinstance(instance, MagicItemInstance) and instance.instance_id == instance_id:
                return instance
        return None


def magic_item_template(instance: MagicItemInstance) -> MagicItemTemplate:
    """Return a magic item instance's template from the loaded catalog.

    Args:
        instance: The instance.

    Returns:
        The frozen template.
    """
    from osrlib.data import load_magic_items

    return load_magic_items().get(instance.template_id)


def equipped_item_modifiers(inventory: Inventory) -> list[ModifierSpec]:
    """Return the stat modifiers granted by equipped always-active magic items.

    Item bonuses are computed from equipped inventory at query time — never
    `ActiveEffect` stat modifiers — the `modifier_total` carve-out: they combine
    freely with spell modifiers and are exempt from the cumulative caps (pinned).

    Args:
        inventory: The inventory to scan.

    Returns:
        The modifiers, in equipped order (slots, wielded, rings).
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
    """A sentient sword control check's outcome — the RAW arithmetic, no events."""

    model_config = ConfigDict(frozen=True)

    sword_will: int
    wielder_will: int
    sword_controls: bool


def sword_control_check(character: object, sword: MagicItemInstance, *, stream: RngStream) -> SwordControlResult:
    """Resolve a sentient sword control check, à la carte.

    RAW arithmetic: the sword's Will is INT + Ego, +1 per extraordinary power,
    +1d10 when the sword's and wielder's alignments differ; the wielder's Will is
    STR + WIS, −1d4 below full hit points, −2d4 below half. The sword controls
    when its Will is strictly higher. The crawl never auto-invokes this in 1.0 —
    games narrate control through referee commands (registered).

    Args:
        character: The wielder (a character with scores and hit points).
        sword: The sentient sword instance.
        stream: The RNG stream for the situational dice; à la carte callers choose.

    Returns:
        The plain result — no events, crawl-neutral.

    Raises:
        ValueError: If the sword is not sentient.
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
    """Return the weight of carried treasure in coins.

    Purse coins weigh 1 each; valuables weigh their `TreasureWeight` figures; magic
    items in the categories those rows price (potion, scroll, rod, staff, wand)
    weigh as treasure — closing the Phase 1 seam. Rings and miscellaneous items
    weigh zero absent a page figure (the Bag of Holding's printed loaded weight
    rides its params, counted while it holds anything); enchanted weapons and
    armour weigh as *equipment* beside their mundane bases, not as treasure, so
    basic encumbrance's treasure tracking stays honest (pinned).

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
                total += int(template.params["loaded_weight_coins"])
    return total


def equipment_weight_coins(inventory: Inventory) -> int:
    """Return detailed-encumbrance equipment weight: weapons, armour, and the gear flat.

    Weapons and armour weigh their listed weights; ammunition weighs 0 (included in
    the missile weapon's listed weight, pinned); miscellaneous gear counts as a flat
    80 coins when any is carried (pinned — the SRD gives gear no per-item weights).
    Enchanted weapons and armour weigh as equipment beside their mundane bases —
    base weight, armour halved per RAW.

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


def _caster_kind(definition: ClassDefinition) -> str | None:
    """Return `"arcane"`/`"divine"` from the class's casting tag, or `None`."""
    for ability in definition.abilities:
        if ability.tag == "arcane_magic":
            return "arcane"
        if ability.tag == "divine_magic":
            return "divine"
    return None


def usable_by_class(template: MagicItemTemplate, definition: ClassDefinition) -> bool:
    """Return whether a class may use a magic item per its `usable_by`.

    Args:
        template: The magic item template.
        definition: The class definition.

    Returns:
        True when the item's usability admits the class.
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
    """Validate equipping a magic item — the base item's policies plus the item's own.

    Enchanted arms resolve through the base item's armour and weapon policies
    exactly like their mundane counterparts; rings cap at two (`items.ring.hands_full`
    — RAW, more than two = none function, delivered as the slot cap); devices and
    miscellaneous items gate on the item's `usable_by`; potions, scrolls, and
    ammunition are not equippable.
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
    """Validate returning an equipped instance to the item list.

    A revealed cursed item pins to its bearer: it rejects with `items.curse.stuck`
    until *remove curse* (each cursed category's page carries the same
    cannot-discard clause).

    Args:
        inventory: The inventory holding the instance.
        instance: The equipped instance.

    Returns:
        Structured rejections; empty when unequipping is legal.
    """
    if isinstance(instance, MagicItemInstance) and instance.cursed_revealed:
        return [Rejection(code="items.curse.stuck", params={"item": instance.instance_id})]
    return []


def validate_equip(
    definition: ClassDefinition, instance: ItemInstance | MagicItemInstance, inventory: Inventory | None = None
) -> list[Rejection]:
    """Validate equipping an item against the class's armour and weapon policies.

    Weapon policies govern the weapons list only: gear carrying a combat facet (torch,
    holy water, burning oil) is exempt and always equippable (pinned — see
    `docs/adaptations.md`, including the magic-user consequence). Gear without a
    facet, and ammunition, is not equippable at all. Magic items resolve through
    their base item's policies (enchanted arms), the ring cap, or their own
    `usable_by` (devices, miscellaneous items).

    Wielding a two-handed weapon with a shield equipped — or equipping the second of
    the pair — rejects with `items.equip.two_handed_with_shield`, pinned at equip
    time rather than silently ignoring the shield at resolution. The check needs the
    current equipped state, so pass `inventory` when one exists.

    Args:
        definition: The character's class definition.
        instance: The instance to equip.
        inventory: The inventory whose equipped state the two-handed-versus-shield
            conflict is checked against.

    Returns:
        Structured rejections; empty when equipping is legal.
    """
    if isinstance(instance, MagicItemInstance):
        return _validate_equip_magic(definition, instance, inventory)
    template = instance.template
    if isinstance(template, ArmourTemplate):
        if template.is_shield:
            if not definition.armour.shields_allowed:
                return [Rejection(code="items.equip.shield_forbidden", params={"class": definition.id})]
            if inventory is not None and any(
                isinstance(wielded.template, WeaponTemplate) and WeaponQuality.TWO_HANDED in wielded.template.qualities
                for wielded in inventory.wielded
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
    if isinstance(instance, MagicItemInstance):
        return (instance.base_item_id or magic_item_template(instance).base_item_id) == "shield"
    return isinstance(instance.template, ArmourTemplate) and instance.template.is_shield


def _is_body_armour_instance(instance: ItemInstance | MagicItemInstance) -> bool:
    if isinstance(instance, MagicItemInstance):
        template = magic_item_template(instance)
        return template.category is MagicItemCategory.ARMOUR and not _is_shield_instance(instance)
    return isinstance(instance.template, ArmourTemplate) and not instance.template.is_shield


def equip(inventory: Inventory, definition: ClassDefinition, instance: ItemInstance | MagicItemInstance) -> None:
    """Equip an item from the inventory's item list.

    Body armour goes to the worn-armour slot and the shield to the shield slot — a
    previous occupant returns to the item list. Weapons, combat-facet gear, devices,
    and miscellaneous magic items join the wielded list; rings join the ring slots.
    Equipping a two-handed weapon with a shield equipped (or the shield while a
    two-handed weapon is wielded) rejects — the conflict is enforced at equip time,
    not silently ignored at resolution.

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
    """Return an equipped instance to the item list.

    Args:
        inventory: The inventory; mutated.
        instance: The equipped instance.

    Raises:
        ValueError: If the instance is not equipped, or a revealed curse pins it
            (validate with [`validate_unequip`][osrlib.core.items.validate_unequip]
            first).
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
    elif any(existing is instance for existing in inventory.rings):
        inventory.rings.remove(instance)
    else:
        raise ValueError("instance is not equipped")
    inventory.items.append(instance)
