"""Monster templates, instances, spawning, and the entity ID allocator.

The 138 SRD monster pages compile into `monsters.json` (packed-variant pages expand to
one concrete entry per variant, because a frozen template must be spawnable) and load
as frozen [`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate] models via
[`load_monsters`][osrlib.data.load_monsters]. Play spawns mutable
[`MonsterInstance`][osrlib.core.monsters.MonsterInstance]s from them, so shared data
can never be damaged by play.

Ability bullets compile as structured tags plus the SRD prose (mirroring
`ClassAbility`): the tags Phase 2 executes (`regeneration`, `energy_drain`, `poison`,
`paralysis`, `petrification`, `breath_weapon`, `gaze`, `disease`, `uses_fire`) carry
pinned params; everything else compiles with `manual=True` and stays prose the kernel
doesn't execute. Defenses the damage pipeline checks at damage time compile into the
structured [`Defenses`][osrlib.core.monsters.Defenses] shape while the bullets keep
the prose.

Spawned hit points draw from the
[`MONSTER_SPAWN_STREAM`][osrlib.core.monsters.MONSTER_SPAWN_STREAM] stream (module
constant, following the `character.py` precedent) so a combat-rules change never
shifts spawned HP in a golden scenario.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from osrlib.core.alignment import Alignment
from osrlib.core.classes import SavingThrows
from osrlib.core.dice import parse
from osrlib.core.effects import ActiveCondition, ActiveModifier, Condition
from osrlib.core.rng import RngStream

__all__ = [
    "MONSTER_SPAWN_STREAM",
    "AcAlternate",
    "AlignmentSpec",
    "AttackRoutine",
    "DamageKey",
    "DamageReduction",
    "Defenses",
    "Element",
    "EnergyDefense",
    "IdAllocator",
    "MonsterAbility",
    "MonsterAttack",
    "MonsterCatalog",
    "MonsterHitDice",
    "MonsterInstance",
    "MonsterSaves",
    "MonsterTemplate",
    "MoraleAlternate",
    "MovementMode",
    "NumberAppearing",
    "NumberAppearingValue",
    "TreasureRef",
    "XpNote",
    "spawn_monster",
]

MONSTER_SPAWN_STREAM = "monster_spawn"
"""Stream key convention for monster spawning draws: hit point rolls."""


class DamageKey(StrEnum):
    """The damage-source keys a `harmed_only_by` gate or reduction can name.

    `holy` is carried by holy water's combat facet and pinned: an undead target admits
    holy damage through any `harmed_only_by` gate — the specific rule ("holy water
    inflicts damage on undead monsters") overrides the general immunity, otherwise the
    wight's silver-or-magic gate would absorb the one weapon made for it.
    """

    SILVER = "silver"
    MAGIC = "magic"
    FIRE = "fire"
    COLD = "cold"
    HOLY = "holy"


class Element(StrEnum):
    """Energy elements that appear in monster attacks, breath weapons, and defenses."""

    FIRE = "fire"
    COLD = "cold"
    LIGHTNING = "lightning"
    ACID = "acid"
    GAS = "gas"
    POISON = "poison"
    STEAM = "steam"


class EnergyDefense(BaseModel):
    """An elemental defense, checked by the damage pipeline.

    The SRD's forms pin to two fields: `immunity` is `"all"` (a giant is "unharmed by
    fire", magical or not) or `"nonmagical"` (a red dragon is immune to its own breath
    and to flaming oil, but not to *fire ball*); `auto_save_magical` treats saving
    throws against magical forms of the element as automatically passed (the dragons'
    "automatically save versus similar attack forms").
    """

    model_config = ConfigDict(frozen=True)

    immunity: Literal["all", "nonmagical"]
    auto_save_magical: bool = False


class DamageReduction(BaseModel):
    """A damage reduction applied after the roll: divide (floor, minimum 1).

    Empty `keys` means the reduction applies to every source that passes the
    `harmed_only_by` gate (the mummy's "all damage reduced by half"); named keys
    restrict it (the wraith's half damage from silver weapons).
    """

    model_config = ConfigDict(frozen=True)

    keys: tuple[DamageKey, ...] = ()
    divisor: int = Field(default=2, ge=2)


class Defenses(BaseModel):
    """The structured defense shape combat checks at damage time.

    `harmed_only_by` is the weapon-material gate (empty means no gate): a source must
    carry at least one listed key or the hit is absorbed with no damage rolled.
    `energy` maps elements to defenses; `condition_immunities` names conditions the
    creature can never gain (the undead poison/mind immunity).
    """

    model_config = ConfigDict(frozen=True)

    harmed_only_by: tuple[DamageKey, ...] = ()
    reductions: tuple[DamageReduction, ...] = ()
    energy: dict[Element, EnergyDefense] = {}
    condition_immunities: tuple[Condition, ...] = ()


class MonsterHitDice(BaseModel):
    """A monster's Hit Dice, exactly as the stat block prints them.

    `die` is 8 unless fractional (`½` compiles as 1d4); `modifier` is signed (`1-1` is
    −1). `asterisks` is the special-ability count — XP data, not noise. `fixed_hp`
    forms (`1hp`, the hydra's 8 hp per HD) roll nothing; `count` is 0 for pure
    fixed-hp forms. The modifier drives the attack-matrix "1 HD higher" rule and the
    negative-modifier XP-band mapping (see [`osrlib.core.tables`][osrlib.core.tables]).
    """

    model_config = ConfigDict(frozen=True)

    count: int = Field(default=0, ge=0)
    die: int = 8
    modifier: int = 0
    asterisks: int = Field(default=0, ge=0)
    average_hp: int | None = None
    fixed_hp: int | None = None

    @model_validator(mode="after")
    def _rollable_or_fixed(self) -> MonsterHitDice:
        if self.die not in (4, 8):
            raise ValueError(f"monster hit die must be d8, or d4 for ½ HD, got d{self.die}")
        if self.count == 0 and self.fixed_hp is None:
            raise ValueError("a monster needs hit dice to roll or fixed hit points")
        return self


class MonsterAttack(BaseModel):
    """One attack within a routine: `count × name (damage + effects)`.

    `damage` is a dice-grammar expression; `fixed_damage` covers flat forms (`1hp`);
    `fixed_damage_options` covers printed alternatives (the insect swarm's `2 or 4hp`,
    armour-dependent per its prose, which stays manual). `by_weapon` marks `or by
    weapon` forms, with the printed modifier. `effects` are the effect keywords from
    the damage parens (`poison`, `paralysis`, `energy_drain`, `charm`, ...) compiled
    to tags on the attack.
    """

    model_config = ConfigDict(frozen=True)

    count: int = Field(default=1, ge=1)
    name: str = Field(min_length=1)
    damage: str | None = None
    fixed_damage: int | None = None
    fixed_damage_options: tuple[int, ...] = ()
    by_weapon: bool = False
    by_weapon_modifier: int = 0
    effects: tuple[str, ...] = ()

    @field_validator("damage")
    @classmethod
    def _damage_must_parse(cls, value: str | None) -> str | None:
        if value is not None:
            parse(value)
        return value


class AttackRoutine(BaseModel):
    """One alternative attack routine — a monster acts with one routine per round."""

    model_config = ConfigDict(frozen=True)

    attacks: tuple[MonsterAttack, ...] = Field(min_length=1)


class MovementMode(BaseModel):
    """One movement mode: rate per turn, encounter rate per round, and a descriptor.

    `descriptor` is `None` for plain ground movement, else the SRD's word (`flying`,
    `swimming`, `gliding`, `in human form`, `in webs`, ...).
    """

    model_config = ConfigDict(frozen=True)

    rate_feet: int = Field(ge=0)
    encounter_rate_feet: int = Field(ge=0)
    descriptor: str | None = None


class MonsterSaves(BaseModel):
    """A monster's saving throws: the five values plus the printed save-as note.

    `save_as` keeps the stat block's parenthetical (`"2"`, `"NH"`, `"Cleric 1"`,
    `"F1 to F3"`) for provenance and validation against the monster save bands.
    """

    model_config = ConfigDict(frozen=True)

    values: SavingThrows
    save_as: str


class MoraleAlternate(BaseModel):
    """A conditional morale score: `10 (8 fear of fire)` keeps score 8 + the prose."""

    model_config = ConfigDict(frozen=True)

    score: int = Field(ge=2, le=12)
    condition: str = Field(min_length=1)


class AlignmentSpec(BaseModel):
    """A monster's alignment options — compound alignments compile to options (pinned).

    `Chaotic` is one option; `Lawful or Neutral` is two; `Any` is all three, with
    `usual` carrying `Any, usually Lawful`.
    """

    model_config = ConfigDict(frozen=True)

    options: tuple[Alignment, ...] = Field(min_length=1)
    usual: Alignment | None = None


class XpNote(BaseModel):
    """A structured XP note for a leader/chieftain/guard variant (stats stay prose)."""

    model_config = ConfigDict(frozen=True)

    role: str = Field(min_length=1)
    xp: int = Field(ge=0)


class NumberAppearingValue(BaseModel):
    """One number-appearing value: dice, a fixed count, and `see below` semantics."""

    model_config = ConfigDict(frozen=True)

    dice: str | None = None
    fixed: int | None = None
    see_below: bool = False

    @field_validator("dice")
    @classmethod
    def _dice_must_parse(cls, value: str | None) -> str | None:
        if value is not None:
            parse(value)
        return value

    @model_validator(mode="after")
    def _dice_or_fixed(self) -> NumberAppearingValue:
        if self.see_below:
            if self.dice is not None or self.fixed is not None:
                raise ValueError("a 'see below' value carries no dice or fixed count")
        elif (self.dice is None) == (self.fixed is None):
            raise ValueError("exactly one of dice or fixed is required")
        return self


class NumberAppearing(BaseModel):
    """The stat block's two number-appearing values: dungeon, then lair/wilderness."""

    model_config = ConfigDict(frozen=True)

    dungeon: NumberAppearingValue
    lair: NumberAppearingValue


class TreasureRef(BaseModel):
    """A faithful reference to the stat block's treasure type — semantics pin in Phase 5.

    `letters` are the primary treasure-type letters (`R + S` is two);
    `parenthetical` keeps bracketed letters (`P (B)`); `special` keeps labels that are
    not treasure types (`Tusks`, `Honey`).
    """

    model_config = ConfigDict(frozen=True)

    letters: tuple[str, ...] = ()
    parenthetical: tuple[str, ...] = ()
    extra_gp: int = Field(default=0, ge=0)
    multiplier: int = Field(default=1, ge=1)
    special: tuple[str, ...] = ()
    see_below: bool = False


class MonsterAbility(BaseModel):
    """A structured monster-ability tag plus the SRD prose it came from.

    `params` carries the mechanizable values pinned by the compiler (the troll's
    regeneration delay and rate, a breath weapon's shape and element); `manual` marks
    abilities the Phase 2 kernel doesn't execute — games and narrators present the
    prose.
    """

    model_config = ConfigDict(frozen=True)

    tag: str = Field(min_length=1)
    name: str = Field(min_length=1)
    prose: str
    manual: bool = False
    params: dict[str, int | str | bool | tuple[int | str, ...]] = {}


class AcAlternate(BaseModel):
    """An alternate armour class with its printed condition (`9 [10] in human form`)."""

    model_config = ConfigDict(frozen=True)

    ac: int
    ac_ascending: int
    condition: str = ""


class MonsterTemplate(BaseModel):
    """A monster stat block, compiled from its SRD page.

    Frozen SRD data: play never mutates a template. `page` is the source-page grouping
    (variants of one page stay associable); `attack_roll_required` is False for the
    `No hit roll required` AC sentinel (attacks auto-hit). `xp` is the printed value,
    cross-validated against the XP-awards table at compile time.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    page: str
    intro: str = ""
    ac: int | None = None
    ac_ascending: int | None = None
    ac_alternates: tuple[AcAlternate, ...] = ()
    attack_roll_required: bool = True
    hit_dice: MonsterHitDice
    attacks: tuple[AttackRoutine, ...] = ()
    thac0: int = Field(ge=2, le=20)
    attack_bonus: int = Field(ge=-1)
    movement: tuple[MovementMode, ...] = Field(min_length=1)
    saves: MonsterSaves
    morale: int | None = Field(default=None, ge=2, le=12)
    morale_alternates: tuple[MoraleAlternate, ...] = ()
    alignment: AlignmentSpec
    xp: int = Field(ge=0)
    xp_notes: tuple[XpNote, ...] = ()
    number_appearing: NumberAppearing
    treasure: TreasureRef = TreasureRef()
    abilities: tuple[MonsterAbility, ...] = ()
    defenses: Defenses = Defenses()
    categories: tuple[str, ...] = ()
    overrides_applied: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _ac_present_when_rolled_against(self) -> MonsterTemplate:
        has_ac = self.ac is not None and self.ac_ascending is not None
        if self.attack_roll_required and not has_ac:
            raise ValueError(f"{self.id} requires attack rolls but has no armour class")
        if not self.attack_roll_required and (self.ac is not None or self.ac_ascending is not None):
            raise ValueError(f"{self.id} needs no hit roll and must not carry an armour class")
        return self

    def ability(self, tag: str) -> MonsterAbility | None:
        """Return the first ability with `tag`, or `None`.

        Args:
            tag: The ability tag, e.g. `"regeneration"`.

        Returns:
            The ability, or `None` when the monster doesn't have it.
        """
        for ability in self.abilities:
            if ability.tag == tag:
                return ability
        return None


class MonsterCatalog(BaseModel):
    """The loaded monster list, with id lookup."""

    model_config = ConfigDict(frozen=True)

    monsters: tuple[MonsterTemplate, ...]

    @model_validator(mode="after")
    def _ids_must_be_unique(self) -> MonsterCatalog:
        ids = [template.id for template in self.monsters]
        if len(set(ids)) != len(ids):
            raise ValueError("monster ids must be unique")
        return self

    def get(self, monster_id: str) -> MonsterTemplate:
        """Return the monster template for `monster_id`.

        Args:
            monster_id: The monster id, e.g. `"troll"` or `"red_dragon"`.

        Returns:
            The monster template.

        Raises:
            ValueError: If no monster has that id.
        """
        for template in self.monsters:
            if template.id == monster_id:
                return template
        raise ValueError(f"unknown monster id {monster_id!r}")


class MonsterInstance(BaseModel):
    """A mutable monster spawned from a frozen template.

    Exposes the same combatant surface as `Character` (THAC0, attack bonus, AC both
    ways, saves, conditions, stat modifiers), so combat functions take either.
    `nonregen_damage` is the troll's non-regenerable damage ledger (fire and acid
    accrue here; regeneration never heals it, and the troll is permanently dead only
    when this ledger alone reaches max HP — pinned). `last_damaged_round` feeds
    regeneration's damage delay; `breath_uses_today` tracks the dragons'
    three-per-day limit. `alignment` is the operative alignment resolved at spawn
    (pinned — a multi-option `AlignmentSpec` can't answer *protection from evil*'s
    ward gate); `None` means unresolved, which the ward treats as differing.
    """

    model_config = ConfigDict(validate_assignment=True)

    id: str
    template: MonsterTemplate
    max_hp: int = Field(ge=1)
    current_hp: int = Field(ge=0)
    conditions: tuple[ActiveCondition, ...] = ()
    stat_modifiers: tuple[ActiveModifier, ...] = ()
    alignment: Alignment | None = None
    nonregen_damage: int = Field(default=0, ge=0)
    last_damaged_round: int | None = None
    breath_uses_today: int = Field(default=0, ge=0)
    drained_hd: int = Field(default=0, ge=0)

    @property
    def name(self) -> str:
        """The template's name."""
        return self.template.name

    @property
    def hit_dice_count(self) -> int:
        """The instance's current Hit Dice count: the template's minus any drained."""
        return max(0, self.template.hit_dice.count - self.drained_hd)

    @property
    def thac0(self) -> int:
        """The printed THAC0 (already reflecting the bonus-hit-points 1-HD-higher rule).

        A drained instance re-derives from its reduced Hit Dice via the attack matrix
        rows (pinned).
        """
        if self.drained_hd == 0:
            return self.template.thac0
        from osrlib.core.tables import thac0_for_hd

        return thac0_for_hd(self.hit_dice_count, bonus_modifier=self.template.hit_dice.modifier > 0)[0]

    @property
    def attack_bonus(self) -> int:
        """The printed ascending-AC attack bonus; drained instances re-derive."""
        if self.drained_hd == 0:
            return self.template.attack_bonus
        from osrlib.core.tables import thac0_for_hd

        return thac0_for_hd(self.hit_dice_count, bonus_modifier=self.template.hit_dice.modifier > 0)[1]

    @property
    def armour_class(self) -> int | None:
        """Descending AC; `None` when no hit roll is required."""
        return self.template.ac

    @property
    def armour_class_ascending(self) -> int | None:
        """Ascending AC; `None` when no hit roll is required."""
        return self.template.ac_ascending

    @property
    def saves(self) -> SavingThrows:
        """The stat block's saving throw values; drained instances re-derive.

        A drained instance reads the monster saving-throw band for its reduced Hit
        Dice (pinned).
        """
        if self.drained_hd == 0:
            return self.template.saves.values
        from osrlib.core.tables import monster_save_band_label
        from osrlib.data import load_combat_tables

        dice = self.template.hit_dice.model_copy(update={"count": self.hit_dice_count})
        return load_combat_tables().save_band(monster_save_band_label(dice)).saves

    @property
    def melee_modifier(self) -> int:
        """Monsters' attack and damage rolls are not modified by STR (RAW)."""
        return 0

    @property
    def missile_modifier(self) -> int:
        """Monsters' attack rolls are not modified by DEX (RAW)."""
        return 0

    @property
    def initiative_modifier(self) -> int:
        """Monsters take a caller-supplied initiative modifier; the intrinsic one is 0."""
        return 0


def spawn_monster(
    template: MonsterTemplate, *, id: str, stream: RngStream, alignment: Alignment | None = None
) -> MonsterInstance:
    """Spawn a mutable instance, rolling hit points from the template's Hit Dice.

    HP is the sum of `count` rolls of the hit die (d8, or d4 for ½ HD) plus the
    signed modifier, minimum 1 (pinned); fixed-hp forms (`1hp`, the hydra's 8 hp per
    HD) roll nothing and are exact. The operative alignment resolves at spawn
    (pinned): the caller's choice wins, else the template's `usual`, else its sole
    option; a multi-option template with no usual and no caller choice stays
    unresolved, which alignment-gated wards treat as differing (erring protective).

    Args:
        template: The frozen template to spawn from.
        id: The instance's entity id, conventionally from an
            [`IdAllocator`][osrlib.core.monsters.IdAllocator].
        stream: The RNG stream for the hit point rolls, conventionally
            [`MONSTER_SPAWN_STREAM`][osrlib.core.monsters.MONSTER_SPAWN_STREAM].
        alignment: The encounter's or script's alignment choice; must be one of the
            template's options.

    Returns:
        The spawned instance at full hit points.

    Raises:
        ValueError: If `alignment` is not among the template's options.
    """
    if alignment is not None and alignment not in template.alignment.options:
        raise ValueError(f"{template.id} alignment options are {template.alignment.options}, got {alignment}")
    resolved = alignment or template.alignment.usual
    if resolved is None and len(template.alignment.options) == 1:
        resolved = template.alignment.options[0]
    dice = template.hit_dice
    if dice.fixed_hp is not None:
        hp = dice.fixed_hp
    else:
        rolls = [stream.randbelow(dice.die) + 1 for _ in range(dice.count)]
        hp = max(1, sum(rolls) + dice.modifier)
    return MonsterInstance(id=id, template=template, max_hp=hp, current_hp=hp, alignment=resolved)


class IdAllocator(BaseModel):
    """A monotonic per-prefix entity ID counter (`monster-0001`, `effect-0001`).

    Matches the spec's session-scoped ID contract: standalone users and tests hold
    one; the Phase 4 session adopts it. Serializable — the counters are plain state.
    """

    model_config = ConfigDict(validate_assignment=True)

    counters: dict[str, int] = {}

    def allocate(self, prefix: str) -> str:
        """Return the next id for `prefix`.

        Args:
            prefix: The entity kind, e.g. `"monster"` or `"effect"`.

        Returns:
            The allocated id, `{prefix}-{n:04d}` with `n` starting at 1.
        """
        n = self.counters.get(prefix, 0) + 1
        self.counters[prefix] = n
        return f"{prefix}-{n:04d}"
