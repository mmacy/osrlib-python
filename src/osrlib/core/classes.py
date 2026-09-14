"""Class definitions, level progression, XP awards, and leveling up.

[`load_classes`][osrlib.data.load_classes] gives you the catalog of playable classes as frozen
[`ClassDefinition`][osrlib.core.classes.ClassDefinition] models, compiled from the OSE SRD's class
pages. Look one up by id with [`ClassCatalog.get`][osrlib.core.classes.ClassCatalog.get]; see
[the class id index][classes-index] for the ids. Hand the definition you get to
[`create_character`][osrlib.core.character.create_character], and afterwards read it back off a
character through [`definition`][osrlib.core.character.Character.definition].

A definition is a frozen template, and a character is the mutable state of one person playing it.
Nothing in play ever writes to a definition. It contains the ability requirements, the prime requisites, the
experience-modifier tiers, a row per level with hit dice, THAC0, saving throws, and spell capacity,
the armour and weapon policies, and the class's abilities as tags the rules procedures read. All of
it is data, so a class the SRD did not print is a data file rather than a code change.

Nothing a character derives from its class is stored on the character. Read the progression row for
the current level with [`ClassDefinition.row`][osrlib.core.classes.ClassDefinition.row] and you
always get the values that match, which is why leveling up and being drained of levels both need
only change the level.

Advancement lives here. [`apply_xp`][osrlib.core.classes.apply_xp] is the one you usually want: it
applies the class's experience modifier, adds the award, and levels the character up when a
threshold is crossed. [`level_up`][osrlib.core.classes.level_up] does the level gain on its own
when the game hands out a level directly, and [`drain_levels`][osrlib.core.classes.drain_levels]
reverses it for the undead that drain levels. Creating a character in the first place is
[`osrlib.core.character`][osrlib.core.character].

Two other procedures read class data, so they live here too:
[`thief_skill_check`][osrlib.core.classes.thief_skill_check] rolls the thief's skills, and
[`detection_check`][osrlib.core.classes.detection_check] with
[`detection_chance`][osrlib.core.classes.detection_chance] rolls the chance-in-6 checks for
listening at doors, finding secret doors, and spotting traps.

Typical usage:

```python
from osrlib.core.alignment import Alignment
from osrlib.core.character import ADVANCEMENT_STREAM, CHARACTER_CREATION_STREAM, create_character
from osrlib.core.classes import apply_xp, level_title
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.data import load_classes

streams = RngStreams(master_seed=2)
fighter = load_classes().get("fighter")
character = create_character(
    name="Rurik",
    class_id="fighter",
    alignment=Alignment.LAWFUL,
    ruleset=Ruleset(),
    stream=streams.get(CHARACTER_CREATION_STREAM),
).character
result = apply_xp(character, fighter, 2500, streams.get(ADVANCEMENT_STREAM))
print(result.level_before, result.level_after, character.max_hp)
# 1 2 10
print(level_title(fighter, character.level), character.thac0, character.saves.death)
# Warrior 19 12
```
"""

from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, model_validator

from osrlib.core.abilities import MAX_SCORE, MIN_SCORE, AbilityScore
from osrlib.core.dice import ALLOWED_SIDES
from osrlib.core.effects import kill
from osrlib.core.events import Event, HitPointsReportedEvent, LevelDrainedEvent
from osrlib.core.rng import RngStream

if TYPE_CHECKING:
    from osrlib.core.character import Character

__all__ = [
    "PERCENTILE_THIEF_SKILLS",
    "ArmourPolicy",
    "ArmourPolicyKind",
    "ClassAbility",
    "ClassCatalog",
    "ClassDefinition",
    "DetectionResult",
    "DrainResult",
    "HitDice",
    "LevelUpResult",
    "ProgressionRow",
    "SavingThrows",
    "SkillCheckResult",
    "ThiefSkillRow",
    "WeaponPolicy",
    "WeaponPolicyKind",
    "XpAwardResult",
    "XpTier",
    "apply_xp",
    "detection_chance",
    "detection_check",
    "drain_levels",
    "level_title",
    "level_up",
    "thief_skill_check",
    "xp_modifier_pct",
]

PERCENTILE_THIEF_SKILLS = (
    "climb_sheer_surfaces",
    "find_remove_treasure_traps",
    "hide_in_shadows",
    "move_silently",
    "open_locks",
    "pick_pockets",
)
"""The names of the six thief skills rolled on percentile dice.

Pass any of these as the `skill` argument of
[`thief_skill_check`][osrlib.core.classes.thief_skill_check], which rolls d% and succeeds on a
result at or under the level's chance. The thief's seventh skill, `"hear_noise"`, is not here
because it rolls 1d6 instead. That function takes it too.

Iterate this tuple to show a thief's whole percentile skill list, reading each level's numbers off
[`ThiefSkillRow`][osrlib.core.classes.ThiefSkillRow] by the same names.
"""


class HitDice(BaseModel):
    """How many hit dice a class rolls at one level, and of what size.

    Read it off [`ProgressionRow.hit_dice`][osrlib.core.classes.ProgressionRow].
    [`level_up`][osrlib.core.classes.level_up] and
    [`drain_levels`][osrlib.core.classes.drain_levels] compare this level's row against the next
    one to decide whether a level change rolls a die or moves a flat bonus. Frozen.

    A class stops gaining dice at name level and gains a flat number of hit points per level after
    that. The SRD marks those levels with an asterisk, as in `9d8+2*`, meaning the CON modifier no
    longer applies to the gain.
    """

    model_config = ConfigDict(frozen=True)

    count: int = Field(ge=1)
    """How many dice are rolled. At least 1."""

    die: int
    """The size of each die, which is the class's hit die: d4 for the magic-user and thief, d6 for the cleric, elf, and
    halfling, d8 for the dwarf and fighter.
    """

    bonus: int = Field(default=0, ge=0)
    """Flat hit points added on top of the dice, which is how levels past name level grow. Never negative."""

    con_applies: bool = True
    """Whether the CON modifier applies to a die gained at this level.

    The SRD clears it at the levels it marks with an asterisk, as in `9d8+2*`. It is read separately from whether a die
    is rolled at all, which depends on `count` rising from the row below.
    """

    @model_validator(mode="after")
    def _die_must_be_rollable(self) -> HitDice:
        if self.die not in ALLOWED_SIDES:
            raise ValueError(f"hit die size must be one of {sorted(ALLOWED_SIDES)}, got {self.die}")
        return self


class SavingThrows(BaseModel):
    """The five saving throw target numbers.

    Roll 1d20 against the field that matches the threat and succeed on that number or higher, so
    lower is better. Read a character's current set from
    [`Character.saves`][osrlib.core.character.Character] or a monster's from
    [`MonsterInstance.saves`][osrlib.core.monsters.MonsterInstance]. The saving-throw procedures in
    [`osrlib.core.combat`][osrlib.core.combat] read them for you. Frozen.

    """

    model_config = ConfigDict(frozen=True)

    death: int = Field(ge=2, le=20)
    """Against death rays and poison, the deadliest category."""

    wands: int = Field(ge=2, le=20)
    """Against the effects of magic wands."""

    paralysis: int = Field(ge=2, le=20)
    """Against paralysis and turning to stone."""

    breath: int = Field(ge=2, le=20)
    """Against a dragon's or other creature's breath attack."""

    spells: int = Field(ge=2, le=20)
    """Against spells, magic rods, and staves."""


class ProgressionRow(BaseModel):
    """Everything a class is at one level: the experience it costs, and what it grants.

    Get one from [`ClassDefinition.row`][osrlib.core.classes.ClassDefinition.row] for the level you
    care about. This is where a character's THAC0, attack bonus, saving throws, and spell capacity
    come from, recomputed from the level every time rather than stored, which is why
    [`level_up`][osrlib.core.classes.level_up] and
    [`drain_levels`][osrlib.core.classes.drain_levels] need only change the level. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    level: int = Field(ge=1)
    """The level this row describes, counting from 1."""

    xp: int = Field(ge=0)
    """The experience points needed to reach this level. Level 1 is 0, and the numbers rise from there."""

    hit_dice: HitDice
    """The dice this level's hit points are rolled on; see [`HitDice`][osrlib.core.classes.HitDice]."""

    thac0: int = Field(ge=2, le=20)
    """The number needed to hit armour class 0 under descending armour class."""

    attack_bonus: int = Field(ge=0)
    """The same attack, expressed as the bonus added to the roll under ascending armour class."""

    saves: SavingThrows
    """The five saving throw targets at this level; see [`SavingThrows`][osrlib.core.classes.SavingThrows]."""

    spell_slots: tuple[int, ...] = ()
    """How many spells of each level the class may memorize, with the first entry being first-level spells. Empty for a
    class that casts nothing.
    """


class XpTier(BaseModel):
    """One band of the class's experience-modifier table: a percentage, and the scores that earn it.

    A class rewards a character whose prime requisite is high and penalizes one whose prime
    requisite is low, by adjusting every experience award up or down.
    [`xp_modifier_pct`][osrlib.core.classes.xp_modifier_pct] walks a class's tiers in order and
    returns the first one whose minimums the character meets, so read the tiers rather than this
    model on its own. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    modifier_pct: int
    """The adjustment as a signed percentage, like `10` for a tenth more experience or `-20` for a fifth less."""

    minimums: dict[AbilityScore, int]
    """The lowest score in each named ability that earns this tier. Every entry must hold for the tier to apply. At
    least one ability is named.
    """

    @model_validator(mode="after")
    def _minimums_must_be_scores(self) -> XpTier:
        if not self.minimums:
            raise ValueError("an XP tier must name at least one minimum score")
        for ability, minimum in self.minimums.items():
            if not MIN_SCORE <= minimum <= MAX_SCORE:
                raise ValueError(f"minimum for {ability} must be in {MIN_SCORE}-{MAX_SCORE}, got {minimum}")
        return self


class ArmourPolicyKind(StrEnum):
    """What armour a class is allowed to wear.

    Read it as [`ArmourPolicy.kind`][osrlib.core.classes.ArmourPolicy].
    [`validate_equip`][osrlib.core.items.validate_equip] enforces it when a character tries to put
    something on. The wire values are `"any"`, `"leather_only"`, and `"none"`.
    """

    ANY = "any"
    """Any armour, which is what the cleric, dwarf, elf, fighter, and halfling wear."""

    LEATHER_ONLY = "leather_only"
    """Leather armour and nothing heavier, which is the thief's limit."""

    NONE = "none"
    """No armour at all, which is the magic-user's limit. Shields are out too."""


class ArmourPolicy(BaseModel):
    """What armour and shields a class may use.

    Read it as [`ClassDefinition.armour`][osrlib.core.classes.ClassDefinition];
    [`validate_equip`][osrlib.core.items.validate_equip] checks against it. The magic-user wears
    nothing, the thief wears leather only, and everyone else wears anything. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    kind: ArmourPolicyKind
    """Which armour the class may wear; see [`ArmourPolicyKind`][osrlib.core.classes.ArmourPolicyKind]."""

    shields_allowed: bool
    """Whether the class may carry a shield. A class that can wear no armour cannot carry one either."""

    @model_validator(mode="after")
    def _no_armour_means_no_shields(self) -> ArmourPolicy:
        if self.kind is ArmourPolicyKind.NONE and self.shields_allowed:
            raise ValueError("a class that can wear no armour cannot use shields")
        return self


class WeaponPolicyKind(StrEnum):
    """Whether a class's weapon list names what it may use or what it may not.

    Read it as [`WeaponPolicy.kind`][osrlib.core.classes.WeaponPolicy]. `"any"` lists nothing and
    permits everything, `"allowed"` lists the only weapons permitted, and `"forbidden"` lists the
    only ones refused. The wire values are those three strings.
    """

    ANY = "any"
    """Any weapon. The class lists none, because none are refused."""

    ALLOWED = "allowed"
    """Only the listed weapons, which is how the cleric is limited to blunt weapons."""

    FORBIDDEN = "forbidden"
    """Anything but the listed weapons, which is how the dwarf and halfling are kept off the long bow."""


class WeaponPolicy(BaseModel):
    """What weapons a class may wield.

    Read it as [`ClassDefinition.weapons`][osrlib.core.classes.ClassDefinition];
    [`validate_equip`][osrlib.core.items.validate_equip] checks against it. It governs weapons
    only. A piece of gear a character swings in a pinch, like a torch, is not on the weapons
    list and is not refused by it. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    kind: WeaponPolicyKind
    """Whether `weapon_ids` is the permitted list, the refused list, or unused; see
    [`WeaponPolicyKind`][osrlib.core.classes.WeaponPolicyKind].
    """

    weapon_ids: tuple[str, ...] = ()
    """The weapon ids the policy names, from [`load_equipment`][osrlib.data.load_equipment]; see
    [the equipment id index][equipment-index]. The cleric's five blunt weapons are an example of a permitted list, and
    the long bow and two-handed sword the dwarf and halfling are refused are an example of the other. Empty when the
    class may use anything.
    """

    manual_notes: tuple[str, ...] = ()
    """The SRD's prose restrictions that no rule can settle, like the dwarf's weapons being "small or normal sized".
    Show them to the referee. Nothing enforces them.
    """

    @model_validator(mode="after")
    def _ids_must_match_kind(self) -> WeaponPolicy:
        if self.kind is WeaponPolicyKind.ANY and self.weapon_ids:
            raise ValueError("an 'any' weapon policy must not list weapon ids")
        if self.kind is not WeaponPolicyKind.ANY and not self.weapon_ids:
            raise ValueError(f"a {self.kind.value!r} weapon policy must list weapon ids")
        return self


class ThiefSkillRow(BaseModel):
    """A thief's seven skill chances at one level.

    Read the row for a thief's level out of
    [`ClassDefinition.thief_skills`][osrlib.core.classes.ClassDefinition], or let
    [`thief_skill_check`][osrlib.core.classes.thief_skill_check] find it and roll for you. Frozen.

    Six of the seven are percentages rolled on d%, succeeding at or under the number.
    `hear_noise` is the odd one out: it is a chance in 6 rolled on 1d6, and the SRD's "1-2" is
    stored here as 2. Pick pockets passes 100 at high level, and the check caps the effective
    chance at 99 so a theft is never certain.
    """

    model_config = ConfigDict(frozen=True)

    level: int = Field(ge=1)
    """The thief level this row describes."""

    climb_sheer_surfaces: int = Field(ge=0)
    """Percent chance to climb a sheer surface. It starts high, at 87 for a first-level thief, because a thief can climb
    from the start.
    """

    find_remove_treasure_traps: int = Field(ge=0)
    """Percent chance to find or disarm a trap on a treasure container, which is not the same as spotting a trap in a
    room.
    """

    hear_noise: int = Field(ge=1, le=6)
    """Chance in 6 of hearing something through a door, rolled on 1d6."""

    hide_in_shadows: int = Field(ge=0)
    """Percent chance to go unseen while staying still in shadow."""

    move_silently: int = Field(ge=0)
    """Percent chance to move without being heard."""

    open_locks: int = Field(ge=0)
    """Percent chance to pick a lock, which needs thieves' tools."""

    pick_pockets: int = Field(ge=0)
    """Percent chance to take something from a person unnoticed."""


class ClassAbility(BaseModel):
    """One thing a class can do, as a tag the rules read plus the SRD text it came from.

    Read them off [`ClassDefinition.abilities`][osrlib.core.classes.ClassDefinition]. The combat,
    magic, and exploration procedures look for the tags they recognize and read the numbers out of
    `params`, so a class ability is data rather than a branch in the code. Frozen.

    Some abilities cannot be reduced to a number. Those are marked manual, and a front end shows
    the prose to the referee rather than acting on it.
    """

    model_config = ConfigDict(frozen=True)

    tag: str
    """The identifier the rules match on, like `"infravision"`, `"detect_secret_doors"`, or `"back_stab"`."""

    name: str
    """The ability's name as the SRD prints it, for display."""

    prose: str
    """The SRD's own description, which is what to show a player or referee."""

    manual: bool = False
    """True when nothing in osrlib acts on this ability and the prose is the whole of it."""

    params: dict[str, int | str] = {}
    """The numbers the rules read, like `{"range_feet": 60}` for infravision or `{"chance_in_six": 2}` for a detection
    ability. Empty when there are none.
    """


class ClassDefinition(BaseModel):
    """A playable character class: the template a [`Character`][osrlib.core.character.Character] plays.

    Get one from [`load_classes`][osrlib.data.load_classes]`().get(class_id)`; see
    [the class id index][classes-index] for the ids. A character stores only the id, and
    [`Character.definition`][osrlib.core.character.Character.definition] looks the definition back
    up, so read it from there rather than keeping a copy alongside.

    It is frozen, and play never writes to it: a definition is shared by every character of that
    class, while the mutable state of one played person lives on the
    [`Character`][osrlib.core.character.Character]. Everything here is data compiled from the SRD's
    class pages, which is why adding a class means adding data rather than code.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """The class id, like `"fighter"`, which is what a character stores."""

    name: str
    """The class's name as the SRD prints it, for display."""

    race: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    """The people this class belongs to, as a lowercase identifier, like `"human"` or `"dwarf"`. Creation copies it onto
    the character. No rule reads it: what a people can do comes through `abilities` instead.
    """

    requirements: dict[AbilityScore, int] = {}
    """The lowest ability scores a character needs to take this class, checked by
    [`validate_class_choice`][osrlib.core.character.validate_class_choice]. Empty for the human classes. The demi-human
    classes each require a 9 in one or two abilities.
    """

    prime_requisites: tuple[AbilityScore, ...]
    """The abilities that set the experience modifier. One for most classes, two for the elf and the halfling."""

    xp_tiers: tuple[XpTier, ...]
    """The experience-modifier bands, best first; see [`xp_modifier_pct`][osrlib.core.classes.xp_modifier_pct], which
    reads them.
    """

    hit_die: int
    """The size of the class's hit die, which is 4, 6, or 8."""

    max_level: int = Field(ge=1)
    """The highest level this class reaches. The human classes reach 14, and the demi-human classes stop lower."""

    armour: ArmourPolicy
    """What armour and shields the class may use; see [`ArmourPolicy`][osrlib.core.classes.ArmourPolicy]."""

    weapons: WeaponPolicy
    """What weapons the class may wield; see [`WeaponPolicy`][osrlib.core.classes.WeaponPolicy]."""

    languages: tuple[str, ...]
    """The language ids the class speaks for free, Common first. A character's full list, including the alignment tongue
    and any extras, is [`Character.languages`][osrlib.core.character.Character.languages].
    """

    may_not_lower: tuple[AbilityScore, ...] = ()
    """Abilities the creation-time adjustment may not take points from, which for the thief is STR."""

    abilities: tuple[ClassAbility, ...] = ()
    """What the class can do, as tags the rules read; see [`ClassAbility`][osrlib.core.classes.ClassAbility]."""

    thief_skills: tuple[ThiefSkillRow, ...] = ()
    """A row per level of the thief's seven skills, empty for every class but the thief; see
    [`ThiefSkillRow`][osrlib.core.classes.ThiefSkillRow].
    """

    level_titles: tuple[str, ...] = ()
    """The title a character has at each level, with the first entry being level 1. The SRD prints titles only up to
    name level, so this is shorter than the progression, and
    [`level_title`][osrlib.core.classes.level_title] returns `None` past the end rather than raising.
    """

    progression: tuple[ProgressionRow, ...]
    """A row per level from 1 to `max_level`, in order. Read one with
    [`row`][osrlib.core.classes.ClassDefinition.row] rather than indexing.
    """

    overrides_applied: tuple[str, ...] = ()
    """The names of the compile-time corrections applied to this class's SRD page. Provenance for anyone checking the
    data against the SRD. Nothing in play reads it.
    """

    @model_validator(mode="after")
    def _progression_must_cover_levels(self) -> ClassDefinition:
        if self.hit_die not in ALLOWED_SIDES:
            raise ValueError(f"hit die size must be one of {sorted(ALLOWED_SIDES)}, got {self.hit_die}")
        if not self.prime_requisites:
            raise ValueError("a class must have at least one prime requisite")
        levels = [row.level for row in self.progression]
        if levels != list(range(1, self.max_level + 1)):
            raise ValueError(f"progression rows must cover levels 1-{self.max_level} in order")
        thresholds = [row.xp for row in self.progression]
        if thresholds != sorted(thresholds) or len(set(thresholds)) != len(thresholds):
            raise ValueError("progression XP thresholds must be strictly increasing")
        if self.progression[0].xp != 0:
            raise ValueError("level 1 must require 0 XP")
        pcts = [tier.modifier_pct for tier in self.xp_tiers]
        if pcts != sorted(pcts, reverse=True):
            raise ValueError("XP tiers must be ordered best-first")
        if self.thief_skills:
            skill_levels = [row.level for row in self.thief_skills]
            if skill_levels != list(range(1, self.max_level + 1)):
                raise ValueError(f"thief skill rows must cover levels 1-{self.max_level} in order")
        return self

    def row(self, level: int) -> ProgressionRow:
        """Return what this class grants at `level`.

        This is where a character's THAC0, attack bonus, saving throws, hit dice, and spell
        capacity come from. Nothing derived is stored on a character, so reading the row for the
        current level always gives values that match it, and changing the level is all that
        leveling up or being drained has to do.

        Read the convenience properties on
        [`Character`][osrlib.core.character.Character] instead when you have a character in hand:
        `character.thac0`, `character.saves`, and the rest call this for you. Call it directly to
        look ahead, like asking what the next level costs in experience.

        Args:
            level: The level to read, from 1 through `max_level`.

        Returns:
            The progression row for that level.

        Raises:
            ValueError: If `level` is below 1 or above the class's maximum.

        Examples:
            ```python
            from osrlib.data import load_classes

            fighter = load_classes().get("fighter")
            row = fighter.row(3)
            print(row.xp, row.thac0, row.hit_dice.count, row.saves.death)
            # 4000 19 3 12
            ```
        """
        if not 1 <= level <= self.max_level:
            raise ValueError(f"{self.id} levels are 1-{self.max_level}, got {level}")
        return self.progression[level - 1]


class ClassCatalog(BaseModel):
    """Every playable class, as returned by [`load_classes`][osrlib.data.load_classes].

    Look a class up by id with [`get`][osrlib.core.classes.ClassCatalog.get], or iterate `classes`
    to build a menu of what a player may choose. The catalog is loaded once and cached, so calling
    the loader again is free. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    classes: tuple[ClassDefinition, ...]
    """The class definitions, in the order the data file lists them. Ids are unique."""

    @model_validator(mode="after")
    def _ids_must_be_unique(self) -> ClassCatalog:
        ids = [definition.id for definition in self.classes]
        if len(set(ids)) != len(ids):
            raise ValueError("class ids must be unique")
        return self

    def get(self, class_id: str) -> ClassDefinition:
        """Return the class with `class_id`.

        Args:
            class_id: The id to look up, like `"fighter"`; see
                [the class id index][classes-index] for all of them.

        Returns:
            The class definition.

        Raises:
            ValueError: If no class has that id. The message names the id you passed.

        Examples:
            ```python
            from osrlib.data import load_classes

            catalog = load_classes()
            print(catalog.get("halfling").max_level)
            # 8
            print([definition.id for definition in catalog.classes])
            # ['cleric', 'dwarf', 'elf', 'fighter', 'halfling', 'magic_user', 'thief']
            ```
        """
        for definition in self.classes:
            if definition.id == class_id:
                return definition
        raise ValueError(f"unknown class id {class_id!r}")


class LevelUpResult(BaseModel):
    """What happened when a character gained a level.

    Returned by [`level_up`][osrlib.core.classes.level_up], and set on
    [`XpAwardResult.level_up`][osrlib.core.classes.XpAwardResult] when an experience award caused
    the gain. Show it to tell a player what their new level got them. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    new_level: int
    """The level the character now has."""

    hp_roll: int | None
    """The raw hit die that was thrown, or `None` when the new level added no hit die and the gain was the difference
    between the two rows' flat bonuses.
    """

    hp_gained: int
    """The hit points added to both maximum and current. At least 1 while dice are still being rolled, however poor the
    die and the CON modifier were together.
    """

    con_applied: bool
    """Whether the CON modifier counted toward the gain.

    It follows the new progression row's `con_applies`, which the SRD clears at the levels it marks with an asterisk,
    and it is always False when no die was rolled. Read it rather than working it out from the level, because the two
    are separate settings in the data.
    """


class XpAwardResult(BaseModel):
    """What happened when a character received an experience award.

    Returned by [`apply_xp`][osrlib.core.classes.apply_xp]. It contains enough to show a player the
    whole story: what the award was, what their class made of it, and whether it took them up a
    level. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    award: int
    """The award as it was handed in, before the class modifier."""

    modifier_pct: int
    """The class's experience modifier for this character's scores, as a signed percentage; see
    [`xp_modifier_pct`][osrlib.core.classes.xp_modifier_pct].
    """

    modified_award: int
    """The award after the modifier, rounded down."""

    xp_before: int
    """The character's experience before the award."""

    xp_after: int
    """The character's experience after it, which is what is now stored."""

    level_before: int
    """The level the character had before the award."""

    level_after: int
    """The level they have after it. At most one higher, because a single award never grants two levels."""

    clamped: bool
    """True when the award was cut back to keep the character below the level after next. An award big enough to jump
    two levels stops 1 experience point short of the second threshold, and the rest is lost.
    """

    level_up: LevelUpResult | None
    """What the level gain granted, or `None` when no level was gained; see
    [`LevelUpResult`][osrlib.core.classes.LevelUpResult].
    """


def xp_modifier_pct(definition: ClassDefinition, scores: dict[AbilityScore, int]) -> int:
    """Return how much a class adjusts this character's experience awards, as a percentage.

    A class rewards a high prime requisite and penalizes a low one by changing every experience
    award. [`apply_xp`][osrlib.core.classes.apply_xp] calls this for you, so call it yourself only
    to show a player the number on a character sheet, or to let them see what raising a score at
    creation would buy them.

    The tiers are stored best first, and the first one whose minimums the character meets wins.
    A character who meets none gets no adjustment, which is how the elf and the halfling end up
    with a bonus band and no penalty band, as the SRD prints them.

    Args:
        definition: The character's class.
        scores: The character's final ability scores, after any creation-time adjustment.

    Returns:
        The adjustment as a signed percentage: `10` for a tenth more, `-20` for a fifth less, `0`
        for no change.

    Examples:
        ```python
        from osrlib.core.abilities import AbilityScore
        from osrlib.core.classes import xp_modifier_pct
        from osrlib.data import load_classes

        fighter = load_classes().get("fighter")
        scores = dict.fromkeys(AbilityScore, 12)
        scores[AbilityScore.STR] = 16
        print(xp_modifier_pct(fighter, scores))
        # 10
        scores[AbilityScore.STR] = 5
        print(xp_modifier_pct(fighter, scores))
        # -20
        ```
    """
    for tier in definition.xp_tiers:
        if all(scores[ability] >= minimum for ability, minimum in tier.minimums.items()):
            return tier.modifier_pct
    return 0


def level_title(definition: ClassDefinition, level: int) -> str | None:
    """Return what a character of this class and level is called, like "Veteran".

    Use it wherever you show a character's standing: a sheet, a party roster, the line a front end
    prints when someone levels up.

    The SRD prints titles only up to name level, the level at which a character may build a
    stronghold, so a character past that has no title and this returns `None`. Show the class name
    instead when it does.

    Args:
        definition: The character's class.
        level: The level to name, 1 or higher.

    Returns:
        The title, or `None` when the class's list does not reach that level.

    Examples:
        ```python
        from osrlib.core.classes import level_title
        from osrlib.data import load_classes

        fighter = load_classes().get("fighter")
        print(level_title(fighter, 1), level_title(fighter, 4))
        # Veteran Hero
        print(level_title(fighter, 11))
        # None
        ```
    """
    if 1 <= level <= len(definition.level_titles):
        return definition.level_titles[level - 1]
    return None


def level_up(character: Character, definition: ClassDefinition, stream: RngStream) -> LevelUpResult:
    """Raise a character one level and roll the hit points that come with it.

    Use [`apply_xp`][osrlib.core.classes.apply_xp] for ordinary play, which awards experience and
    calls this when a threshold is crossed. Call this directly when a level is granted outright
    rather than earned: building a character above first level, a referee's ruling, restoring a
    level a wight took.

    Two things about the new level's progression row decide what the gain is, and they are read
    separately. Whether a die is rolled depends on the row having more hit dice than the row below
    it: when it does, the character rolls one, and when it does not, the gain is the difference
    between the two rows' flat bonuses and no die is thrown. Whether the CON modifier counts
    depends on the new row's `con_applies`, which the SRD clears at the levels it marks with an
    asterisk. A rolled die with CON cleared gains the raw die alone, and
    [`con_applied`][osrlib.core.classes.LevelUpResult.con_applied] on the result says which way it
    went.

    For the classes osrlib ships, both settings change over at name level, so a character rolls
    with CON up to name level and takes a flat gain without CON after it. A class added as data can
    set them independently, which is why the result reports them rather than leaving you to work
    one out from the other.

    A rolled gain is floored at 1 hit point, however poor the die and the CON modifier are
    together. Both maximum and current hit points rise by the gain, so a level heals nothing: a
    wounded character is still wounded, with a higher ceiling.

    Nothing else needs updating. THAC0, saving throws, and spell capacity are read from the
    progression row for the new level, so they change on their own.

    Args:
        character: The character to advance. Mutated in place: its level, maximum hit points, and
            current hit points all change.
        definition: The character's class. It must be the character's own class.
        stream: The stream for the hit die, conventionally
            `streams.get(`[`ADVANCEMENT_STREAM`][osrlib.core.character.ADVANCEMENT_STREAM]`)`. No
            draw is taken when the new row adds no hit die.

    Returns:
        What the level gained, including the raw die when one was thrown.

    Raises:
        ValueError: If `definition` is not the character's class, or the character is already at
            the class's maximum level.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import (
            ADVANCEMENT_STREAM,
            CHARACTER_CREATION_STREAM,
            create_character,
        )
        from osrlib.core.classes import level_up
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.data import load_classes

        streams = RngStreams(master_seed=2)
        fighter = load_classes().get("fighter")
        character = create_character(
            name="Rurik",
            class_id="fighter",
            alignment=Alignment.LAWFUL,
            ruleset=Ruleset(),
            stream=streams.get(CHARACTER_CREATION_STREAM),
        ).character
        result = level_up(character, fighter, streams.get(ADVANCEMENT_STREAM))
        print(result.new_level, result.hp_roll, result.hp_gained)
        # 2 1 2
        print(character.level, character.max_hp, character.thac0)
        # 2 10 19
        ```
    """
    if definition.id != character.class_id:
        raise ValueError(f"class definition {definition.id!r} does not match character class {character.class_id!r}")
    if character.level >= definition.max_level:
        raise ValueError(f"{character.class_id} is capped at level {definition.max_level}")
    old_dice = definition.row(character.level).hit_dice
    new_dice = definition.row(character.level + 1).hit_dice
    if new_dice.count > old_dice.count:
        roll = stream.randbelow(new_dice.die) + 1
        con_modifier = character.hit_point_modifier if new_dice.con_applies else 0
        gained = max(1, roll + con_modifier)
        result = LevelUpResult(
            new_level=character.level + 1, hp_roll=roll, hp_gained=gained, con_applied=new_dice.con_applies
        )
    else:
        gained = new_dice.bonus - old_dice.bonus
        result = LevelUpResult(new_level=character.level + 1, hp_roll=None, hp_gained=gained, con_applied=False)
    character.level += 1
    character.max_hp += result.hp_gained
    character.current_hp += result.hp_gained
    return result


class SkillCheckResult(BaseModel):
    """How a thief skill check came out.

    Returned by [`thief_skill_check`][osrlib.core.classes.thief_skill_check]. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    skill: str
    """The skill that was rolled, as its name."""

    roll: int
    """The die result: d% for the six percentile skills, 1d6 for `hear_noise`."""

    chance: int
    """The number the roll had to come in at or under, after any modifier you passed. Pick pockets is capped here at 99,
    so a theft always has some chance of failing.
    """

    passed: bool
    """Whether the check succeeded."""

    noticed: bool | None = None
    """For pick pockets only: True when the roll came in at more than twice the chance, which means the victim noticed
    the attempt. `None` for every other skill. What a noticed thief then faces is the game's business, not the kernel's.
    """


class DetectionResult(BaseModel):
    """How a chance-in-6 detection check came out.

    Returned by [`detection_check`][osrlib.core.classes.detection_check]. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    chance: int
    """The chance in 6 the roll had to come in at or under."""

    roll: int | None = None
    """The 1d6 result, or `None` when the chance was zero and no die was thrown. A character with no chance at all, such
    as anyone but a dwarf looking for a shift in the stonework, fails without rolling.
    """

    passed: bool
    """Whether the check succeeded."""


def thief_skill_check(
    character: Character, definition: ClassDefinition, skill: str, *, modifier_pct: int = 0, stream: RngStream
) -> SkillCheckResult:
    """Roll one of a thief's skills and return how it came out.

    Call it when a thief tries something their skills cover: climbing a wall, listening at a door,
    lifting a purse. It rolls and reports, nothing more. It emits no events and hides nothing, so
    a front end that shows players only what their characters would know must decide for itself
    what to reveal. Inside a crawl, the commands in
    [`osrlib.crawl.exploration`][osrlib.crawl.exploration] call it and emit the events for you.

    The six skills in
    [`PERCENTILE_THIEF_SKILLS`][osrlib.core.classes.PERCENTILE_THIEF_SKILLS] roll d% and succeed at
    or under the chance for the thief's level. `"hear_noise"` rolls 1d6 against a chance in 6
    instead, and ignores `modifier_pct`.

    Pick pockets has two rules of its own. Stealing from someone above fifth level is harder, by
    5% per level above the fifth, and you fold that into `modifier_pct` yourself, because the
    kernel never sees the victim. The chance then caps at 99, so a theft is never certain, and a
    roll of more than twice the chance means the victim noticed.

    Args:
        character: The thief making the attempt. Their level chooses the row.
        definition: The character's class, which must have a thief skill table.
        skill: One of the names in
            [`PERCENTILE_THIEF_SKILLS`][osrlib.core.classes.PERCENTILE_THIEF_SKILLS], or
            `"hear_noise"`.
        modifier_pct: A percentage added to the chance before rolling, negative to make the
            attempt harder. Ignored for `"hear_noise"`.
        stream: The stream to draw from, conventionally the one a crawl names
            [`EXPLORATION_STREAM`][osrlib.crawl.session.EXPLORATION_STREAM], whose key is
            `"exploration"`. One draw is taken. The example below spells the key out rather than
            importing the constant, because this module sits in the core layer and never reaches
            up into the crawl layer.

    Returns:
        The roll, the chance it was measured against, and whether it passed.

    Raises:
        ValueError: If the class has no thief skills, or the skill name is not one this function
            knows. Deciding who is allowed to try a skill is yours to do before calling.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.classes import thief_skill_check
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.data import load_classes

        thief = load_classes().get("thief")
        character = create_character(
            name="Nim",
            class_id="thief",
            alignment=Alignment.NEUTRAL,
            ruleset=Ruleset(),
            stream=RngStreams(master_seed=4).get(CHARACTER_CREATION_STREAM),
        ).character
        stream = RngStreams(master_seed=1).get("exploration")
        result = thief_skill_check(character, thief, "climb_sheer_surfaces", stream=stream)
        print(result.roll, result.chance, result.passed)
        # 65 87 True
        stream = RngStreams(master_seed=9).get("exploration")
        theft = thief_skill_check(character, thief, "pick_pockets", stream=stream)
        print(theft.roll, theft.chance, theft.passed, theft.noticed)
        # 100 20 False True
        ```
    """
    if not definition.thief_skills:
        raise ValueError(f"{definition.id} has no thief skill table")
    row = definition.thief_skills[character.level - 1]
    if skill == "hear_noise":
        roll = stream.randbelow(6) + 1
        return SkillCheckResult(skill=skill, roll=roll, chance=row.hear_noise, passed=roll <= row.hear_noise)
    if skill not in PERCENTILE_THIEF_SKILLS:
        raise ValueError(f"unknown thief skill {skill!r}")
    chance = getattr(row, skill) + modifier_pct
    if skill == "pick_pockets":
        chance = min(99, chance)
    roll = stream.randbelow(100) + 1
    noticed = roll > 2 * chance if skill == "pick_pockets" else None
    return SkillCheckResult(skill=skill, roll=roll, chance=chance, passed=roll <= chance, noticed=noticed)


def detection_check(chance_in_six: int, *, stream: RngStream) -> DetectionResult:
    """Roll a chance-in-6 check: 1d6, succeeding at or under the chance.

    This is the one roll behind searching a wall for a secret door, listening at a door, spotting
    a trap in a room, and a dwarf noticing that the stonework is wrong. Get the chance from
    [`detection_chance`][osrlib.core.classes.detection_chance], which works out what this
    character's chance at this kind of search is, then pass it here.

    A chance of zero, or below, fails without throwing a die and takes no draw from the stream.
    Anyone but a dwarf looking for a shift in the stonework has no chance at all, and rolling for
    them would both mislead the player and shift every later draw.

    Args:
        chance_in_six: The chance to roll at or under, usually from
            [`detection_chance`][osrlib.core.classes.detection_chance].
        stream: The stream to draw from, conventionally the one a crawl names
            [`EXPLORATION_STREAM`][osrlib.crawl.session.EXPLORATION_STREAM], whose key is
            `"exploration"`. One draw is taken unless the chance is zero. The example below spells
            the key out rather than importing the constant, because this module sits in the core
            layer and never reaches up into the crawl layer.

    Returns:
        The roll and whether it passed, with `roll` left `None` when no die was thrown.

    Examples:
        ```python
        from osrlib.core.classes import detection_check
        from osrlib.core.rng import RngStreams

        stream = RngStreams(master_seed=4).get("exploration")
        result = detection_check(2, stream=stream)
        print(result.roll, result.passed)
        # 2 True
        nothing = detection_check(0, stream=stream)
        print(nothing.roll, nothing.passed)
        # None False
        ```
    """
    if chance_in_six <= 0:
        return DetectionResult(chance=chance_in_six, passed=False)
    roll = stream.randbelow(6) + 1
    return DetectionResult(chance=chance_in_six, roll=roll, passed=roll <= chance_in_six)


def _ability_chance(definition: ClassDefinition, tag: str) -> int | None:
    for ability in definition.abilities:
        if ability.tag == tag:
            return int(ability.params.get("chance_in_six", 0))
    return None


def detection_chance(character: Character, definition: ClassDefinition, kind: str) -> int:
    """Return this character's chance in 6 at one kind of search.

    Call it before [`detection_check`][osrlib.core.classes.detection_check], which rolls against
    the number it gives you. It reads the class's abilities and, for a thief listening, the level's
    skill row, so it answers for whoever is searching without you having to know which classes are
    good at what.

    Listening at doors takes the thief's `hear_noise` chance when the character is a thief, else
    the class's own listening ability, else the 1 in 6 anyone gets. Searching for a secret door
    takes the class's `detect_secret_doors` ability, which the elf has at 2, else 1. Looking for a
    trap in a room takes `detect_room_traps`, which the dwarf has at 2, else 1. Noticing a shift
    in the stonework takes `detect_construction_tricks`, which the dwarf has at 2, and everyone
    else gets zero: the SRD gives this perception to dwarves and states no chance for anyone else,
    unlike the searches it opens to every character.

    Args:
        character: The character searching. Their level chooses a thief's skill row.
        definition: The character's class.
        kind: What they are searching for: `"listening"`, `"secret_doors"`, `"room_traps"`, or
            `"construction"`.

    Returns:
        The chance in 6. Zero means the character cannot do it at all, and
        [`detection_check`][osrlib.core.classes.detection_check] fails it without a roll.

    Raises:
        ValueError: If `kind` is not one of the four.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.classes import detection_chance
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.data import load_classes

        dwarf = load_classes().get("dwarf")
        character = create_character(
            name="Thora",
            class_id="dwarf",
            alignment=Alignment.LAWFUL,
            ruleset=Ruleset(),
            stream=RngStreams(master_seed=1).get(CHARACTER_CREATION_STREAM),
        ).character
        print(detection_chance(character, dwarf, "room_traps"))
        # 2
        print(detection_chance(character, dwarf, "secret_doors"))
        # 1
        ```
    """
    if kind == "listening":
        if definition.thief_skills:
            return definition.thief_skills[character.level - 1].hear_noise
        chance = _ability_chance(definition, "listening_at_doors")
        return chance if chance is not None else 1
    if kind == "secret_doors":
        chance = _ability_chance(definition, "detect_secret_doors")
        return chance if chance is not None else 1
    if kind == "room_traps":
        chance = _ability_chance(definition, "detect_room_traps")
        return chance if chance is not None else 1
    if kind == "construction":
        chance = _ability_chance(definition, "detect_construction_tricks")
        return chance if chance is not None else 0
    raise ValueError(f"unknown detection kind {kind!r}")


class DrainResult(BaseModel):
    """What an energy drain took from a character.

    Returned by [`drain_levels`][osrlib.core.classes.drain_levels]. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    levels_lost: int
    """How many levels the drain removed. When the drain killed the character, the level that killed them is counted
    here.
    """

    new_level: int = Field(ge=0)
    """The level the character now has, or 0 when the drain killed them."""

    hp_rolls: tuple[int, ...] = ()
    """The raw hit dice thrown for the levels lost, in order.

    A level whose row carries no extra hit die throws nothing, so this is shorter than `levels_lost` when the drain
    crossed such a level, and empty when every level it took was one of them.
    """

    hp_lost: int
    """The hit points taken from both maximum and current."""

    xp_after: int | None = None
    """The experience the character is left with, or `None` when the drain killed them."""

    slain: bool = False
    """True when the drain took the character's last level and killed them."""

    events: tuple[Event, ...] = ()
    """What to publish: a [`LevelDrainedEvent`][osrlib.core.events.LevelDrainedEvent] and, depending on the outcome, a
    hit point report, the death events, and any spells forgotten because the character's capacity shrank. Feed them to
    your event sink in order.
    """


def drain_levels(
    character: Character,
    definition: ClassDefinition,
    *,
    levels: int = 1,
    xp_policy: str,
    stream: RngStream,
    spawn_consequence: str | None = None,
) -> DrainResult:
    """Take experience levels away from a character, undoing what [`level_up`][osrlib.core.classes.level_up] did.

    Call it when an undead creature that drains levels lands a hit: the wight takes one level, the
    spectre and the vampire take two. Read the monster's `energy_drain` ability with
    [`MonsterTemplate.ability`][osrlib.core.monsters.MonsterTemplate.ability] for the number of
    levels and the experience policy, then pass them here. The attack itself resolves in
    [`osrlib.core.combat`][osrlib.core.combat]. This is the consequence.

    Each level is taken exactly as it was given, reading the same two settings
    [`level_up`][osrlib.core.classes.level_up] reads. When the level being lost had more hit dice
    than the level below it, the character throws that die and loses the result plus their CON
    modifier, at least 1, with CON counting only when the row it came from says it does. When the
    two rows have the same number of dice, the loss is the difference between their flat bonuses
    and no die is thrown. Rolling the die back is what lets the model stay stateless: a character
    keeps no record of which dice built their hit points, so the drain rolls a fresh one. THAC0,
    saving throws, and spell capacity need nothing done to them, because they are read from the
    level.

    A character never drops below 1 maximum or 1 current hit point while they still have a level.
    Death comes only from losing the last one, which is the SRD's person drained of all levels: the
    result reports `slain`, the events include the death, and any spells that no longer fit the
    shrunken capacity are forgotten newest first.

    Experience is rewritten once, after every level is taken. Under `"halfway"` the character keeps
    the midpoint between the threshold they had reached and the one they fell back to. Under
    `"level_minimum"` they keep exactly the new level's threshold.

    Args:
        character: The character being drained. Mutated in place: level, experience, and both hit
            point totals change.
        definition: The character's class. It must be the character's own class.
        levels: How many levels to take. The procedure runs once per level.
        xp_policy: `"halfway"` or `"level_minimum"`, from the monster's `energy_drain` ability.
        stream: The stream for the hit dice thrown back, conventionally
            `streams.get(`[`ADVANCEMENT_STREAM`][osrlib.core.character.ADVANCEMENT_STREAM]`)`, the
            same stream the gains came from.
        spawn_consequence: What the victim becomes, in the monster's own words, put on the drain
            event for a front end to show. Nothing acts on it.

    Returns:
        What was lost, and the events to publish.

    Raises:
        ValueError: If `definition` is not the character's class, if `levels` is not positive, or
            if `xp_policy` is neither of the two.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import (
            ADVANCEMENT_STREAM,
            CHARACTER_CREATION_STREAM,
            create_character,
        )
        from osrlib.core.classes import apply_xp, drain_levels
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.data import load_classes

        streams = RngStreams(master_seed=2)
        fighter = load_classes().get("fighter")
        character = create_character(
            name="Rurik",
            class_id="fighter",
            alignment=Alignment.LAWFUL,
            ruleset=Ruleset(),
            stream=streams.get(CHARACTER_CREATION_STREAM),
        ).character
        advancement = streams.get(ADVANCEMENT_STREAM)
        apply_xp(character, fighter, 2500, advancement)
        print(character.level, character.xp, character.max_hp)
        # 2 2500 10

        result = drain_levels(character, fighter, levels=1, xp_policy="halfway", stream=advancement)
        print(result.levels_lost, result.new_level, result.hp_lost, result.slain)
        # 1 1 9 False
        print(character.level, character.xp, character.max_hp)
        # 1 1000 1
        ```
    """
    if definition.id != character.class_id:
        raise ValueError(f"class definition {definition.id!r} does not match character class {character.class_id!r}")
    if levels < 1:
        raise ValueError(f"levels must be positive, got {levels}")
    if xp_policy not in ("halfway", "level_minimum"):
        raise ValueError(f"unknown xp policy {xp_policy!r}")
    former_level = character.level
    hp_rolls: list[int] = []
    hp_lost = 0
    slain = False
    for _ in range(levels):
        if character.level <= 1:
            slain = True
            break
        old_dice = definition.row(character.level).hit_dice
        new_dice = definition.row(character.level - 1).hit_dice
        if old_dice.count > new_dice.count:
            rolled = stream.randbelow(old_dice.die) + 1
            con_modifier = character.hit_point_modifier if old_dice.con_applies else 0
            lost = max(1, rolled + con_modifier)
            hp_rolls.append(rolled)
        else:
            lost = old_dice.bonus - new_dice.bonus
        character.level -= 1
        character.current_hp = max(1, character.current_hp - lost)
        character.max_hp = max(1, character.max_hp - lost)
        hp_lost += lost
    events: list[Event] = []
    if slain:
        # The level that killed them counts as lost, so a level-1 victim loses 1 and a spectre
        # draining a level-2 fighter reports 2. The stored level stays at 1 because the model
        # floors it there. The character is dead, not level 0.
        levels_lost = former_level - character.level + 1
        character.xp = 0
        events.append(
            LevelDrainedEvent(
                code="combat.drain.slain",
                target_id=character.id or character.name,
                levels_lost=levels_lost,
                new_level=0,
                hp_lost=hp_lost,
                spawn_consequence=spawn_consequence,
            )
        )
        events.extend(kill(character))
        return DrainResult(
            levels_lost=levels_lost,
            new_level=0,
            hp_rolls=tuple(hp_rolls),
            hp_lost=hp_lost,
            slain=True,
            events=tuple(events),
        )
    new_threshold = definition.row(character.level).xp
    if xp_policy == "halfway":
        former_threshold = definition.row(former_level).xp
        xp_after = (former_threshold + new_threshold) // 2
    else:
        xp_after = new_threshold
    character.xp = xp_after
    events.append(
        LevelDrainedEvent(
            code="combat.drain.drained",
            target_id=character.id or character.name,
            levels_lost=former_level - character.level,
            new_level=character.level,
            hp_lost=hp_lost,
            xp_after=xp_after,
            spawn_consequence=spawn_consequence,
        )
    )
    events.append(
        HitPointsReportedEvent(
            target_id=character.id or character.name, current_hp=character.current_hp, max_hp=character.max_hp
        )
    )
    if getattr(character, "memorized_spells", ()):
        # Memorized spells beyond what the shrunken slots can fit are forgotten, newest first.
        # The imports are here rather than at the top because spells imports combat, which
        # imports this module.
        from osrlib.core.spells import forget_excess_memorized
        from osrlib.data import load_spells

        events.extend(forget_excess_memorized(character, definition, load_spells()))
    return DrainResult(
        levels_lost=former_level - character.level,
        new_level=character.level,
        hp_rolls=tuple(hp_rolls),
        hp_lost=hp_lost,
        xp_after=xp_after,
        events=tuple(events),
    )


def apply_xp(character: Character, definition: ClassDefinition, award: int, stream: RngStream) -> XpAwardResult:
    """Give a character experience points, and level them up if the award takes them over a threshold.

    This is how characters advance. Split the experience a party earned among its members however
    your game divides it, then call this once per member. It applies the class's modifier, stores
    the new total, and calls [`level_up`][osrlib.core.classes.level_up] when the character has
    crossed the next threshold, all in one step, so you never have to check thresholds yourself.

    A single award never grants two levels. An award large enough to reach the level after next is
    cut back to one point short of that second threshold, and the excess is lost, so a character
    who kills a dragon at first level ends up at second and has to earn the rest. The result says
    when that happened.

    At the class's maximum level the character stops gaining levels but keeps accumulating
    experience, uncut, because there is no further threshold to keep them under.

    Args:
        character: The character receiving the award. Mutated in place: experience, and on a level
            gain the level and hit points too.
        definition: The character's class. It must be the character's own class.
        award: The experience to award, before the class modifier. Not negative.
        stream: The stream for a hit die if the award levels the character up, conventionally
            `streams.get(`[`ADVANCEMENT_STREAM`][osrlib.core.character.ADVANCEMENT_STREAM]`)`. No
            draw is taken when no level is gained.

    Returns:
        The whole story of the award: what it was, what the class made of it, and what the
        character gained.

    Raises:
        ValueError: If `definition` is not the character's class, or `award` is negative.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import (
            ADVANCEMENT_STREAM,
            CHARACTER_CREATION_STREAM,
            create_character,
        )
        from osrlib.core.classes import apply_xp
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.data import load_classes

        streams = RngStreams(master_seed=2)
        fighter = load_classes().get("fighter")
        character = create_character(
            name="Rurik",
            class_id="fighter",
            alignment=Alignment.LAWFUL,
            ruleset=Ruleset(),
            stream=streams.get(CHARACTER_CREATION_STREAM),
        ).character
        result = apply_xp(character, fighter, 10000, streams.get(ADVANCEMENT_STREAM))
        print(result.modified_award, result.xp_after, result.level_after, result.clamped)
        # 10000 3999 2 True
        ```
    """
    if definition.id != character.class_id:
        raise ValueError(f"class definition {definition.id!r} does not match character class {character.class_id!r}")
    if award < 0:
        raise ValueError(f"XP award must be non-negative, got {award}")
    pct = xp_modifier_pct(definition, character.scores)
    modified = award * (100 + pct) // 100
    xp_before = character.xp
    level_before = character.level
    new_xp = xp_before + modified
    clamped = False
    gains_level = False
    if level_before < definition.max_level:
        next_threshold = definition.row(level_before + 1).xp
        if level_before + 2 <= definition.max_level:
            second_threshold = definition.row(level_before + 2).xp
            if new_xp >= second_threshold:
                new_xp = second_threshold - 1
                clamped = True
        gains_level = new_xp >= next_threshold
    character.xp = new_xp
    up = level_up(character, definition, stream) if gains_level else None
    return XpAwardResult(
        award=award,
        modifier_pct=pct,
        modified_award=modified,
        xp_before=xp_before,
        xp_after=new_xp,
        level_before=level_before,
        level_after=character.level,
        clamped=clamped,
        level_up=up,
    )
