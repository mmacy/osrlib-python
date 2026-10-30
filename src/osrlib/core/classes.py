"""Character classes, experience and levels, and the skill checks that depend on a character's class.

A character's class is what they are trained as, like a fighter, a cleric, or a thief. In B/X, the
dwarf, the elf, and the halfling are classes too. [`load_classes`][osrlib.data.load_classes]
returns the classes osrlib includes, compiled from the OSE SRD's class pages, as frozen
[`ClassDefinition`][osrlib.core.classes.ClassDefinition] models. Look one up by id with
[`ClassCatalog.get`][osrlib.core.classes.ClassCatalog.get]. For the ids, iterate
[`ClassCatalog.classes`][osrlib.core.classes.ClassCatalog.classes] or see
[the class id index](https://mmacy.github.io/osrlib-python/reference/content-ids/classes/). A character stores only the
class id, and its [`definition`][osrlib.core.character.Character.definition] property returns the definition.

A definition is shared by every character of the class, and play never changes it. It contains the
minimum ability scores, the prime requisites, the experience-modifier tiers, the armour and weapon
rules, the class abilities as tags that the rules functions read, and a progression row for each
level. A progression row contains the experience points needed to reach the level, the hit dice,
THAC0, the saving throws, and the spell slots. A character stores none of these values. Its
properties read them from the row for the current level, which
[`ClassDefinition.row`][osrlib.core.classes.ClassDefinition.row] returns, so when a character
gains or loses a level, only the level changes.

The functions here, in the order you usually need them:

- [`apply_xp`][osrlib.core.classes.apply_xp] awards experience points. It applies the class's
  experience modifier and levels the character up when the total reaches the next level. It's the
  advancement function you usually call.
- [`level_up`][osrlib.core.classes.level_up] raises a character one level and rolls the hit points
  for it. Call it to grant a level directly.
- [`drain_levels`][osrlib.core.classes.drain_levels] takes levels away, as the touch of a wight
  does.
- [`xp_modifier_pct`][osrlib.core.classes.xp_modifier_pct] and
  [`level_title`][osrlib.core.classes.level_title] return the experience modifier and the level
  title to show on a character sheet.
- [`thief_skill_check`][osrlib.core.classes.thief_skill_check] rolls one of a thief's skills, like
  climbing a wall or picking a lock.
- [`detection_chance`][osrlib.core.classes.detection_chance] returns a character's chance in 6 at
  a search, like listening at a door, and [`detection_check`][osrlib.core.classes.detection_check]
  rolls against it.

To create a character, see [`osrlib.core.character`][osrlib.core.character].

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
"""The names of the thief skills rolled on percentile dice, like `"open_locks"`.

Pass any of these as the `skill` argument of
[`thief_skill_check`][osrlib.core.classes.thief_skill_check], which rolls d% and succeeds on a
result equal to or under the chance for the thief's level. The thief's other skill,
`"hear_noise"`, isn't here, because it's a chance in 6 rolled on 1d6. `thief_skill_check` accepts
it too.

Each name is also a field of [`ThiefSkillRow`][osrlib.core.classes.ThiefSkillRow]. To show a
thief's percentile skills, iterate this tuple and read each chance off the row with `getattr`.
"""


class HitDice(BaseModel):
    """A class's hit dice at one level: how many, of what size, and any flat bonus.

    Read it off [`ProgressionRow.hit_dice`][osrlib.core.classes.ProgressionRow.hit_dice].
    [`level_up`][osrlib.core.classes.level_up] and
    [`drain_levels`][osrlib.core.classes.drain_levels] compare the hit dice of two neighbouring
    levels. When `count` rises from one level to the next, the level gained or lost is worth a
    rolled die. When it doesn't, the level is worth the difference between the two `bonus` values.
    Frozen.

    A class stops gaining dice at name level, the level at which a character may build a
    stronghold. After that it gains a flat number of hit points per level. The SRD writes those
    levels with an asterisk, as in `9d8+2*`, to show that the CON modifier no longer applies.
    """

    model_config = ConfigDict(frozen=True)

    count: int = Field(ge=1)
    """The number of hit dice the class has at this level. At least 1."""

    die: int
    """The number of sides on each die, which is the class's hit die: 4 for the magic-user and thief, 6 for the cleric,
    elf, and halfling, and 8 for the dwarf and fighter.
    """

    bonus: int = Field(default=0, ge=0)
    """The flat hit points added to the dice, which is how a character gains hit points past name level. At least 0."""

    con_applies: bool = True
    """Whether the CON modifier applies to a die gained at this level.

    It's False at the levels the SRD writes with an asterisk, as in `9d8+2*`. Whether a die is rolled at all is a
    separate question, which depends on whether `count` rises from the level below.
    """

    @model_validator(mode="after")
    def _die_must_be_rollable(self) -> HitDice:
        if self.die not in ALLOWED_SIDES:
            raise ValueError(f"hit die size must be one of {sorted(ALLOWED_SIDES)}, got {self.die}")
        return self


class SavingThrows(BaseModel):
    """The five saving throw targets, one for each kind of danger.

    A save succeeds on a 1d20 roll equal to or higher than the target for the danger, so a lower
    target is better. Read a character's current targets from
    [`Character.saves`][osrlib.core.character.Character.saves] and a monster's from
    [`MonsterInstance.saves`][osrlib.core.monsters.MonsterInstance.saves].
    [`saving_throw`][osrlib.core.combat.saving_throw] rolls a save against them for you. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    death: int = Field(ge=2, le=20)
    """The target against death rays and poison."""

    wands: int = Field(ge=2, le=20)
    """The target against magic wands."""

    paralysis: int = Field(ge=2, le=20)
    """The target against paralysis and being turned to stone."""

    breath: int = Field(ge=2, le=20)
    """The target against a breath attack, like a dragon's."""

    spells: int = Field(ge=2, le=20)
    """The target against spells, rods, and staves."""


class ProgressionRow(BaseModel):
    """A class's values at one level: the experience a character needs to reach the level, and what the level grants.

    Get one from [`ClassDefinition.row`][osrlib.core.classes.ClassDefinition.row]. A character's
    THAC0, attack bonus, saving throws, and spell slots come from the row for their current level,
    read each time rather than stored. That's why [`level_up`][osrlib.core.classes.level_up] and
    [`drain_levels`][osrlib.core.classes.drain_levels] need to change only the level. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    level: int = Field(ge=1)
    """The level this row describes, from 1."""

    xp: int = Field(ge=0)
    """The total experience points a character needs to reach this level. It's 0 at level 1, and rises each level."""

    hit_dice: HitDice
    """The class's hit dice at this level. See [`HitDice`][osrlib.core.classes.HitDice]."""

    thac0: int = Field(ge=2, le=20)
    """The d20 roll needed to hit armour class 0, for descending armour class."""

    attack_bonus: int = Field(ge=0)
    """The bonus added to an attack roll, for ascending armour class. It describes the same attack as `thac0`."""

    saves: SavingThrows
    """The saving throw targets at this level. See [`SavingThrows`][osrlib.core.classes.SavingThrows]."""

    spell_slots: tuple[int, ...] = ()
    """How many spells of each spell level the class can memorize at this level.

    The first entry is for first-level spells, the second for second-level spells, and so on. An entry is 0 when the
    class can't yet memorize spells of that level, as with a first-level cleric. Empty for a class that casts no spells.
    """


class XpTier(BaseModel):
    """One tier of a class's experience modifier: a percentage, and the minimum scores that earn it.

    A class adjusts every experience award up for a character whose prime requisites are high, and
    down for one whose prime requisites are low. Read a class's tiers from
    [`ClassDefinition.xp_tiers`][osrlib.core.classes.ClassDefinition.xp_tiers].
    [`xp_modifier_pct`][osrlib.core.classes.xp_modifier_pct] checks them in order and returns the
    percentage of the first tier whose minimums the character meets. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    modifier_pct: int
    """The adjustment as a percentage, like `10` for a tenth more experience or `-20` for a fifth less."""

    minimums: dict[AbilityScore, int]
    """The lowest score in each ability that earns this tier, keyed by ability. The character must meet every entry for
    the tier to apply. There's at least one entry.
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
    """Which armour a class may wear.

    Read it from [`ArmourPolicy.kind`][osrlib.core.classes.ArmourPolicy.kind].
    [`validate_equip`][osrlib.core.items.validate_equip] checks it when a character puts armour
    on. The values are the strings `"any"`, `"leather_only"`, and `"none"`.
    """

    ANY = "any"
    """Any armour. The cleric, dwarf, elf, fighter, and halfling may wear any armour."""

    LEATHER_ONLY = "leather_only"
    """Leather armour only, which is the thief's rule. The thief may not use a shield either."""

    NONE = "none"
    """No armour, which is the magic-user's rule. A class with this rule may not use a shield either."""


class ArmourPolicy(BaseModel):
    """Which armour and shields a class may use.

    Read it from [`ClassDefinition.armour`][osrlib.core.classes.ClassDefinition.armour].
    [`validate_equip`][osrlib.core.items.validate_equip] checks a character's armour and shield
    against it. Among the built-in classes, the magic-user wears no armour, the thief wears leather
    only, and the other classes may wear any armour. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    kind: ArmourPolicyKind
    """Which armour the class may wear. See [`ArmourPolicyKind`][osrlib.core.classes.ArmourPolicyKind]."""

    shields_allowed: bool
    """Whether the class may use a shield. It must be False when `kind` is `"none"`."""

    @model_validator(mode="after")
    def _no_armour_means_no_shields(self) -> ArmourPolicy:
        if self.kind is ArmourPolicyKind.NONE and self.shields_allowed:
            raise ValueError("a class that can wear no armour cannot use shields")
        return self


class WeaponPolicyKind(StrEnum):
    """Whether a class's weapon list contains the weapons it may use or the weapons it may not.

    Read it from [`WeaponPolicy.kind`][osrlib.core.classes.WeaponPolicy.kind]. With `"any"` the list
    is empty and every weapon is allowed. With `"allowed"` the list contains the only weapons allowed.
    With `"forbidden"` the list contains the only weapons the class may not use. The values are those three strings.
    """

    ANY = "any"
    """Any weapon. The list is empty."""

    ALLOWED = "allowed"
    """Only the listed weapons. The cleric's list contains its blunt weapons."""

    FORBIDDEN = "forbidden"
    """Any weapon except the listed ones. The dwarf's and halfling's lists contain the long bow and two-handed sword."""


class WeaponPolicy(BaseModel):
    """Which weapons a class may wield.

    Read it from [`ClassDefinition.weapons`][osrlib.core.classes.ClassDefinition.weapons].
    [`validate_equip`][osrlib.core.items.validate_equip] checks a weapon against it. It covers
    weapons only. [`validate_equip`][osrlib.core.items.validate_equip] doesn't check a piece of
    gear used as a weapon, like a torch, against it. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    kind: WeaponPolicyKind
    """Whether `weapon_ids` lists the weapons allowed, the weapons refused, or nothing. See
    [`WeaponPolicyKind`][osrlib.core.classes.WeaponPolicyKind].
    """

    weapon_ids: tuple[str, ...] = ()
    """The weapon ids the policy lists, like `("long_bow", "two_handed_sword")` for the dwarf.

    Look each one up with [`load_equipment`][osrlib.data.load_equipment]`().get(item_id)`, or see
    [the equipment id index](https://mmacy.github.io/osrlib-python/reference/content-ids/equipment/). Empty when `kind`
    is `"any"`, and not empty otherwise.
    """

    manual_notes: tuple[str, ...] = ()
    """Weapon rules from the SRD that osrlib doesn't check, like the dwarf's limit to "small or normal sized" weapons.

    Show them to the referee, who applies them.
    """

    @model_validator(mode="after")
    def _ids_must_match_kind(self) -> WeaponPolicy:
        if self.kind is WeaponPolicyKind.ANY and self.weapon_ids:
            raise ValueError("an 'any' weapon policy must not list weapon ids")
        if self.kind is not WeaponPolicyKind.ANY and not self.weapon_ids:
            raise ValueError(f"a {self.kind.value!r} weapon policy must list weapon ids")
        return self


class ThiefSkillRow(BaseModel):
    """A thief's skill chances at one level.

    Read the row for a thief's level from
    [`ClassDefinition.thief_skills`][osrlib.core.classes.ClassDefinition.thief_skills], or let
    [`thief_skill_check`][osrlib.core.classes.thief_skill_check] find it and roll for you. Frozen.

    Every skill except `hear_noise` is a percentage, rolled on d%, and succeeds on a roll equal to
    or under the chance. `hear_noise` is a chance in 6, rolled on 1d6, so osrlib stores the SRD's
    "1-2" as 2. `pick_pockets` is above 100 at high levels. `thief_skill_check` lowers the chance
    to 99, so a theft can always fail.
    """

    model_config = ConfigDict(frozen=True)

    level: int = Field(ge=1)
    """The thief level this row describes."""

    climb_sheer_surfaces: int = Field(ge=0)
    """The percent chance to climb a sheer surface. It's 87 at first level."""

    find_remove_treasure_traps: int = Field(ge=0)
    """The percent chance to find or remove a trap on a treasure container, like a chest.

    Spotting a trap in a room is a different check, made with
    [`detection_chance`][osrlib.core.classes.detection_chance].
    """

    hear_noise: int = Field(ge=1, le=6)
    """The chance in 6 of hearing noise through a door, rolled on 1d6."""

    hide_in_shadows: int = Field(ge=0)
    """The percent chance to stay unseen while keeping still in shadow."""

    move_silently: int = Field(ge=0)
    """The percent chance to move without being heard."""

    open_locks: int = Field(ge=0)
    """The percent chance to pick a lock. In a session, the engine rejects a
    [`PickLock`][osrlib.crawl.commands.PickLock] command unless the thief carries thieves' tools.
    """

    pick_pockets: int = Field(ge=0)
    """The percent chance to steal something a person is carrying."""


class ClassAbility(BaseModel):
    """One thing a class can do, like a dwarf's infravision: a tag the rules functions match, its numbers, and its text.

    Read a class's abilities from
    [`ClassDefinition.abilities`][osrlib.core.classes.ClassDefinition.abilities]. The combat,
    magic, and exploration functions look for the tags they recognize and read the numbers from
    `params`. They ignore a tag they don't recognize. Frozen.

    An ability osrlib doesn't apply has `manual` set. Show its `prose` to the referee, who applies
    it.
    """

    model_config = ConfigDict(frozen=True)

    tag: str
    """The identifier the rules functions match, like `"infravision"`, `"detect_secret_doors"`, or `"back_stab"`."""

    name: str
    """The ability's name as the SRD prints it, for display."""

    prose: str
    """The SRD's description of the ability, to show a player or the referee."""

    manual: bool = False
    """True when osrlib doesn't apply this ability, and the referee applies it from `prose`."""

    params: dict[str, int | str] = {}
    """The values the rules functions read, like `{"range_feet": 60}` for infravision or `{"chance_in_six": 2}` for a
    detection ability. Empty when the ability has none.
    """


class ClassDefinition(BaseModel):
    """A character class, like the fighter or the elf: its rules, and its values at every level.

    Get one from [`load_classes`][osrlib.data.load_classes]`().get(class_id)`. For the ids, iterate
    `load_classes().classes` or see [the class id
    index](https://mmacy.github.io/osrlib-python/reference/content-ids/classes/). A
    [`Character`][osrlib.core.character.Character] stores only the class id, and
    [`Character.definition`][osrlib.core.character.Character.definition] returns the definition for it. Pass the
    definition to the functions in this module that take one, like [`apply_xp`][osrlib.core.classes.apply_xp].

    A definition is frozen and shared by every character of the class. What changes in play, like
    the level and the hit points, is stored on the character. The built-in definitions are compiled
    from the SRD's class pages. To write a class of your own, build a `ClassDefinition` with
    `model_validate`, which checks it with the same rules as the built-in classes.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """The class id, like `"fighter"`. A character stores it in `class_id`."""

    name: str
    """The class's name as the SRD prints it, like `"Magic-User"`, for display."""

    race: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    """The race of the class's characters, as a lowercase identifier, like `"human"` or `"dwarf"`.

    Creation copies it onto the character. No rules function reads it. What a race can do comes from `abilities`.
    """

    requirements: dict[AbilityScore, int] = {}
    """The lowest ability score a character needs in each listed ability to take this class.

    [`validate_class_choice`][osrlib.core.character.validate_class_choice] checks them. Empty for the human classes.
    The dwarf, the elf, and the halfling each require a 9 in one or two abilities.
    """

    prime_requisites: tuple[AbilityScore, ...]
    """The abilities the class depends on most. There's one for most classes, and two for the elf and the halfling.

    The ability-score adjustment at creation can raise only these, and can't lower them. The experience modifier in
    `xp_tiers` usually depends on them.
    """

    xp_tiers: tuple[XpTier, ...]
    """The experience-modifier tiers, highest percentage first.
    [`xp_modifier_pct`][osrlib.core.classes.xp_modifier_pct] reads them.
    """

    hit_die: int
    """The number of sides on the class's hit die. It's 4, 6, or 8 for the built-in classes."""

    max_level: int = Field(ge=1)
    """The highest level a character of this class can reach. It's 14 for the human classes, 12 for the dwarf, 10 for
    the elf, and 8 for the halfling.
    """

    armour: ArmourPolicy
    """Which armour and shields the class may use. See [`ArmourPolicy`][osrlib.core.classes.ArmourPolicy]."""

    weapons: WeaponPolicy
    """Which weapons the class may wield. See [`WeaponPolicy`][osrlib.core.classes.WeaponPolicy]."""

    languages: tuple[str, ...]
    """The ids of the languages every character of this class speaks, starting with `"common"`.

    A character's full list, with the alignment tongue and any extra languages, is
    [`Character.languages`][osrlib.core.character.Character.languages].
    """

    may_not_lower: tuple[AbilityScore, ...] = ()
    """Abilities the ability-score adjustment at creation can't lower, besides the prime requisites. The thief's is
    STR.
    """

    abilities: tuple[ClassAbility, ...] = ()
    """What the class can do, as tags the rules functions read.

    See [`ClassAbility`][osrlib.core.classes.ClassAbility].
    """

    thief_skills: tuple[ThiefSkillRow, ...] = ()
    """The thief's skill chances, one row per level from 1 to `max_level`. Empty for every built-in class but the thief.
    See [`ThiefSkillRow`][osrlib.core.classes.ThiefSkillRow].
    """

    level_titles: tuple[str, ...] = ()
    """The title of a character at each level, starting with level 1, like `"Veteran"` for a first-level fighter.

    The SRD prints titles only up to name level, so this can be shorter than `progression`. Call
    [`level_title`][osrlib.core.classes.level_title] to read one, which returns `None` for a level past the end.
    """

    progression: tuple[ProgressionRow, ...]
    """One progression row for each level from 1 to `max_level`, in order. Read one with
    [`row`][osrlib.core.classes.ClassDefinition.row], which checks the level.
    """

    overrides_applied: tuple[str, ...] = ()
    """The fields osrlib corrected when it compiled this class from the SRD, like `"xp_tiers"`. Empty when none were.

    Read it when you check osrlib's data against the SRD. No rules function reads it.
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
        """Return the class's progression row for `level`.

        A character's THAC0, attack bonus, saving throws, hit dice, and spell slots come from the
        row for their current level. When you have a character, read its properties instead, like
        [`Character.thac0`][osrlib.core.character.Character.thac0] and
        [`Character.saves`][osrlib.core.character.Character.saves], which call this for you. Call
        it yourself to look at another level, like the experience a character needs for the next level.

        Args:
            level: The level to read, from 1 to `max_level`.

        Returns:
            The progression row for that level.

        Raises:
            ValueError: If `level` is below 1 or above `max_level`. The message includes the
                class's level range.

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
    """A set of class definitions, like the built-in classes that [`load_classes`][osrlib.data.load_classes] returns.

    Look a class up by id with [`get`][osrlib.core.classes.ClassCatalog.get], or iterate `classes`
    to list the classes a player can choose from. `load_classes` loads its catalog once and caches
    it, so calling it again costs almost nothing. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    classes: tuple[ClassDefinition, ...]
    """The class definitions. The built-in catalog lists them in alphabetical order of id. No two have the same id."""

    @model_validator(mode="after")
    def _ids_must_be_unique(self) -> ClassCatalog:
        ids = [definition.id for definition in self.classes]
        if len(set(ids)) != len(ids):
            raise ValueError("class ids must be unique")
        return self

    def get(self, class_id: str) -> ClassDefinition:
        """Return the class with `class_id`.

        Args:
            class_id: The id to look up, like `"fighter"`. The ids are the `id` values of the
                definitions in `classes`, and [the class id
                index](https://mmacy.github.io/osrlib-python/reference/content-ids/classes/) lists the built-in ones.

        Returns:
            The class definition.

        Raises:
            ValueError: If no class has that id. The message includes the id you passed.

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
    """What a character gained with a new level.

    [`level_up`][osrlib.core.classes.level_up] returns one, and
    [`XpAwardResult.level_up`][osrlib.core.classes.XpAwardResult.level_up] contains one when an
    experience award raised the level. Show it to tell a player what the new level gave them.
    Frozen.
    """

    model_config = ConfigDict(frozen=True)

    new_level: int
    """The level the character now has."""

    hp_roll: int | None
    """The hit die result before the CON modifier, or `None` when the new level adds no hit die.

    With no hit die, the gain is the difference between the `bonus` values of the two levels' hit dice.
    """

    hp_gained: int
    """The hit points added to both maximum and current hit points.

    When a die was rolled, it's at least 1, however low the die and the CON modifier.
    """

    con_applied: bool
    """Whether the CON modifier counted toward the gain.

    It's the `con_applies` value of the new level's hit dice when a die was rolled, and False when none was.
    """


class XpAwardResult(BaseModel):
    """What an experience award did to a character.

    [`apply_xp`][osrlib.core.classes.apply_xp] returns one. It contains what you need to show a player
    the award, how their class changed it, and whether it raised their level. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    award: int
    """The award passed to `apply_xp`, before the class's experience modifier."""

    modifier_pct: int
    """The class's experience modifier for this character's scores, as a percentage, like `10` or `-20`. See
    [`xp_modifier_pct`][osrlib.core.classes.xp_modifier_pct].
    """

    modified_award: int
    """The award after the modifier, rounded down. It can be more than the character gained when `clamped` is True."""

    xp_before: int
    """The character's experience points before the award."""

    xp_after: int
    """The character's experience points after the award. `Character.xp` now has this value."""

    level_before: int
    """The character's level before the award."""

    level_after: int
    """The character's level after the award. It's at most one higher than `level_before`."""

    clamped: bool
    """True when the award was cut so the character gains no more than one level.

    [`apply_xp`][osrlib.core.classes.apply_xp] cuts an award large enough to reach the level after next to 1
    experience point short of that level's threshold, and the rest of the award is lost.
    """

    level_up: LevelUpResult | None
    """What the new level gave the character, or `None` when the award raised no level. See
    [`LevelUpResult`][osrlib.core.classes.LevelUpResult].
    """


def xp_modifier_pct(definition: ClassDefinition, scores: dict[AbilityScore, int]) -> int:
    """Return how much a class adjusts a character's experience awards, as a percentage.

    A class raises every experience award for a character with high prime requisites and lowers it
    for one with low prime requisites. [`apply_xp`][osrlib.core.classes.apply_xp] calls this for
    you. Call it yourself to show the modifier on a character sheet, or to show a player what a
    trade of ability points at creation would change.

    This function checks the class's `xp_tiers` in order, highest percentage first, and the first
    tier whose minimums the scores meet applies. Scores that meet no tier get no adjustment. That's
    how the elf and the halfling have bonus tiers and no penalty tiers, as the SRD prints them.

    Args:
        definition: The character's class.
        scores: The character's final ability scores, after any adjustment at creation.

    Returns:
        The adjustment as a percentage: `10` for a tenth more, `-20` for a fifth less, and `0` for
        no change.

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
    """Return the title of a character of this class and level, like "Veteran" for a first-level fighter.

    Use it wherever you show a character's rank, like a character sheet, a party list, or the
    message you show when a character gains a level.

    The SRD prints titles only up to name level, the level at which a character may build a
    stronghold. Past the last title, this returns `None`, so show the class name instead.

    Args:
        definition: The character's class.
        level: The level to name, 1 or higher.

    Returns:
        The title, or `None` when the class's `level_titles` don't reach that level.

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

    In ordinary play, call [`apply_xp`][osrlib.core.classes.apply_xp] instead. It awards
    experience and calls this when the total reaches the next level. Call this yourself when a
    character gains a level without earning the experience, like a character you create above
    first level or a level the referee grants.

    The hit point gain depends on the hit dice of the new level. When the new level has more hit dice
    than the level below it, the character rolls one die, adds the CON modifier, and gains at
    least 1 hit point. When it has the same number, no die is rolled, and the character gains the
    difference between the two levels' flat `bonus` values. The CON modifier counts only when the
    new level's `con_applies` is True. The result's
    [`con_applied`][osrlib.core.classes.LevelUpResult.con_applied] is True when it counted.

    For the built-in classes, both change at the first level past name level, the level at which a
    character may build a stronghold. Up to name level, a character rolls a die and adds CON.
    After it, the character gains a flat amount without CON. A class you write yourself can set
    `count` and `con_applies` separately, so read `con_applied` rather than working it out from the
    level.

    Maximum and current hit points both rise by the gain, so a wounded character stays wounded by
    the same amount. THAC0, saving throws, and spell slots come from the new level's progression
    row, so they need no update.

    Args:
        character: The character to advance. Changed in place: its level, maximum hit points, and
            current hit points.
        definition: The character's class definition. Its `id` must match the character's `class_id`.
        stream: The stream for the hit die, usually
            `streams.get(`[`ADVANCEMENT_STREAM`][osrlib.core.character.ADVANCEMENT_STREAM]`)`. It
            doesn't advance when the new level adds no hit die.

    Returns:
        What the new level gave the character, including the die result when a die was rolled.

    Raises:
        ValueError: If `definition` isn't the character's class, or the character is already at
            the class's maximum level. Nothing changes and the stream doesn't advance.

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
    """The result of a thief skill check.

    [`thief_skill_check`][osrlib.core.classes.thief_skill_check] returns one. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    skill: str
    """The name of the skill rolled, like `"open_locks"`."""

    roll: int
    """The die result: from 1 to 100 for a percentile skill, or from 1 to 6 for `hear_noise`."""

    chance: int
    """The number the roll had to equal or come under, after the `modifier_pct` you passed.

    For `pick_pockets`, it's at most 99, so a theft can always fail.
    """

    passed: bool
    """Whether the check succeeded: `roll` is equal to or under `chance`."""

    noticed: bool | None = None
    """For `pick_pockets` only, True when the victim noticed the attempt, which happens when `roll` is more than twice
    `chance`. `None` for every other skill. What happens to a thief who is noticed is up to you or the referee.
    """


class DetectionResult(BaseModel):
    """The result of a chance-in-6 check, like listening at a door or searching for a secret door.

    [`detection_check`][osrlib.core.classes.detection_check] returns one. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    chance: int
    """The chance in 6 that the roll had to equal or come under."""

    roll: int | None = None
    """The 1d6 result, or `None` when the chance was 0 or less and no die was thrown."""

    passed: bool
    """Whether the check succeeded: a die was thrown and `roll` is equal to or under `chance`."""


def thief_skill_check(
    character: Character, definition: ClassDefinition, skill: str, *, modifier_pct: int = 0, stream: RngStream
) -> SkillCheckResult:
    """Roll one of a thief's skills and return the result.

    Call it when a thief tries something a thief skill covers, like climbing a wall, listening at a
    door, or picking a pocket. It rolls and returns the result, and does nothing else. It emits no
    events, and it doesn't decide what the players may see. If you show players only what their
    characters know, you decide what to reveal. In a session, the engine calls it and emits the
    events for you when it resolves commands like [`PickLock`][osrlib.crawl.commands.PickLock].

    For a skill in [`PERCENTILE_THIEF_SKILLS`][osrlib.core.classes.PERCENTILE_THIEF_SKILLS], it
    rolls d%, and the check succeeds on a roll equal to or under the chance for the thief's level.
    For `"hear_noise"`, it rolls 1d6 against a chance in 6 instead, and ignores `modifier_pct`.

    Picking pockets has two more rules. Stealing from a victim above fifth level is harder by 5%
    for each level above the fifth. This function takes no victim, so include that penalty in
    `modifier_pct` yourself. The function then lowers the chance to 99 if it's higher, so a theft
    can always fail. A roll of more than twice the chance means the victim noticed the attempt.

    Args:
        character: The thief making the attempt. The check uses the skill row for their level.
        definition: The character's class. It must have `thief_skills`, as the thief does.
        skill: One of the names in
            [`PERCENTILE_THIEF_SKILLS`][osrlib.core.classes.PERCENTILE_THIEF_SKILLS], or
            `"hear_noise"`.
        modifier_pct: A percentage added to the chance before rolling. Make it negative to make
            the attempt harder. Ignored for `"hear_noise"`.
        stream: The stream to draw from, usually the one a session uses for these checks,
            `streams.get(`[`EXPLORATION_STREAM`][osrlib.crawl.session.EXPLORATION_STREAM]`)`.
            It advances by one draw.

    Returns:
        The roll, the chance it was checked against, and whether it succeeded.

    Raises:
        ValueError: If the class has no thief skills, or `skill` isn't one of the names above.
            The check takes no draw. Deciding who may try a skill is up to you, before you call.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.classes import thief_skill_check
        from osrlib.core.rng import RngStreams, StreamName
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
        stream = RngStreams(master_seed=1).get(StreamName.EXPLORATION)
        result = thief_skill_check(character, thief, "climb_sheer_surfaces", stream=stream)
        print(result.roll, result.chance, result.passed)
        # 65 87 True
        stream = RngStreams(master_seed=9).get(StreamName.EXPLORATION)
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
    """Roll 1d6 against a chance in 6, and succeed on a roll equal to or under the chance.

    This one roll covers searching for a secret door, listening at a door, spotting a trap in a
    room, and a dwarf noticing unusual stonework. It also covers forcing a stuck door open, with
    [`Character.open_doors_chance`][osrlib.core.character.Character.open_doors_chance] as the
    chance. For a search, get the character's chance from
    [`detection_chance`][osrlib.core.classes.detection_chance] and pass it here. In a session,
    the engine makes the roll for you when it resolves commands like
    [`Search`][osrlib.crawl.commands.Search] and
    [`ListenAtDoor`][osrlib.crawl.commands.ListenAtDoor].

    A chance of 0 or less fails without throwing a die, and the stream doesn't advance. No die is
    thrown for a character who can't succeed, like anyone but a dwarf looking for unusual
    stonework.

    Args:
        chance_in_six: The chance to roll equal to or under, usually from
            [`detection_chance`][osrlib.core.classes.detection_chance].
        stream: The stream to draw from, usually the one a session uses for these checks,
            `streams.get(`[`EXPLORATION_STREAM`][osrlib.crawl.session.EXPLORATION_STREAM]`)`.
            It advances by one draw unless the chance is 0 or less.

    Returns:
        The roll and whether it succeeded. `roll` is `None` when no die was thrown.

    Examples:
        ```python
        from osrlib.core.classes import detection_check
        from osrlib.core.rng import RngStreams, StreamName

        stream = RngStreams(master_seed=4).get(StreamName.EXPLORATION)
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
    """Return a character's chance in 6 at one kind of search.

    Call it before [`detection_check`][osrlib.core.classes.detection_check], and pass the number
    it returns. It reads the class's abilities, and a thief's skill row for listening, so you don't
    need to know which classes are better at which search.

    - `"listening"`: the thief's `hear_noise` chance for a class with thief skills. Otherwise the
      class's `listening_at_doors` ability, which the dwarf, the elf, and the halfling have at 2.
      Otherwise 1.
    - `"secret_doors"`: the class's `detect_secret_doors` ability, which the elf has at 2.
      Otherwise 1.
    - `"room_traps"`: the class's `detect_room_traps` ability, which the dwarf has at 2. Otherwise
      1. A trap on a chest is a thief skill instead, `find_remove_treasure_traps`.
    - `"construction"`, noticing unusual stonework like a sloping passage or a sliding wall: the
      class's `detect_construction_tricks` ability, which the dwarf has at 2. Otherwise 0, because
      the SRD gives this chance only to dwarves.

    Args:
        character: The character searching. For a thief, the function reads the skill row for
            their level.
        definition: The character's class.
        kind: What the character is searching for: `"listening"`, `"secret_doors"`,
            `"room_traps"`, or `"construction"`.

    Returns:
        The chance in 6. A chance of 0 means the character can't succeed, and
        [`detection_check`][osrlib.core.classes.detection_check] fails it without a roll.

    Raises:
        ValueError: If `kind` isn't one of the four names above.

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

    [`drain_levels`][osrlib.core.classes.drain_levels] returns one. Its `events` describe the
    drain, for you to show. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    levels_lost: int
    """How many levels the drain took. When the drain killed the character, the last level, the one that killed them,
    counts too.
    """

    new_level: int = Field(ge=0)
    """The character's level after the drain, or 0 when the drain killed them.

    A character killed by a drain still has a `level` of 1, because the model doesn't allow 0.
    """

    hp_rolls: tuple[int, ...] = ()
    """The hit die results thrown for the levels lost, before the CON modifier, in the order thrown.

    No die is thrown for a lost level that added no hit die, so this can be shorter than `levels_lost`, or empty.
    """

    hp_lost: int
    """The hit points the drain takes, from both maximum and current hit points.

    Neither total drops below 1 while the character has a level left, so the totals can fall by less than this.
    The `hp_lost` of the drain event in `events` is how far the maximum actually fell, so the two can differ.
    """

    xp_after: int | None = None
    """The character's experience points after the drain, or `None` when the drain killed them."""

    slain: bool = False
    """True when the drain took the character's last level and killed them."""

    events: tuple[Event, ...] = ()
    """The events that describe the drain, in the order they happen.

    The first is a [`LevelDrainedEvent`][osrlib.core.events.LevelDrainedEvent]. When the character survives, a
    [`HitPointsReportedEvent`][osrlib.core.events.HitPointsReportedEvent] follows, then a
    [`SpellForgottenEvent`][osrlib.core.events.SpellForgottenEvent] for each memorized spell that no longer fits. When
    the drain kills the character, the death events follow instead.
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
    """Take experience levels away from a character, reversing what [`level_up`][osrlib.core.classes.level_up] does.

    Energy drain is the attack of some undead: a wight's touch takes one level, and a spectre's or
    a vampire's takes two. You rarely call this yourself, because
    [`resolve_energy_drain`][osrlib.core.combat.resolve_energy_drain] reads the monster's
    `energy_drain` ability and calls it for you. Call it directly for a drain that doesn't come
    from a monster, like a cursed scroll.

    Each level is lost the way [`level_up`][osrlib.core.classes.level_up] gains it, read from the
    hit dice of the level being lost and the level below it. When the level being lost has more
    hit dice, the character rolls that die and loses the result plus the CON modifier, at least 1.
    The CON modifier counts only when the lost level's `con_applies` is True. When the two levels
    have the same number of dice, no die is rolled, and the loss is the difference between their
    flat `bonus` values. A character has no record of the dice behind their hit points, so this
    function rolls a new die for each lost level. THAC0, saving throws, and spell slots come from
    the level, so they need no update.

    While a character has a level left, neither maximum nor current hit points drops below 1. Losing
    the last level kills the character, as the SRD rules for a character drained of all levels.
    The result's `slain` is then True, its `new_level` is 0, and its events include the death. The
    character's `level` stays at 1, because the model doesn't allow a level below 1, and their
    experience becomes 0. A character who survives forgets memorized spells that no longer fit the
    lower level's spell slots, the most recently memorized first.

    Experience points change once, after every level is taken. Under `"halfway"`, the character is
    left with the point halfway between the threshold of the level they had and the threshold of
    their new level. Under `"level_minimum"`, they're left with exactly the new level's threshold.

    Args:
        character: The character being drained. Changed in place: level, experience points, and
            both hit point totals.
        definition: The character's class definition. Its `id` must match the character's `class_id`.
        levels: How many levels to take, 1 or more.
        xp_policy: `"halfway"` or `"level_minimum"`, from the `xp_policy` of the monster's
            `energy_drain` ability.
        stream: The stream for the hit dice, usually
            `streams.get(`[`ADVANCEMENT_STREAM`][osrlib.core.character.ADVANCEMENT_STREAM]`)`,
            the stream that level gains draw from.
        spawn_consequence: A description of what a victim drained of all levels becomes, like the
            description of the monster's `energy_drain` ability. When the drain kills the character,
            this function copies it onto the `combat.drain.slain` event for you to show, and osrlib
            doesn't act on it. A drain the character survives leaves it off its event, because the
            character hasn't become anything. `None` leaves it off.

    Returns:
        What the character lost, and the events that describe it.

    Raises:
        ValueError: If `definition` isn't the character's class, `levels` is less than 1, or
            `xp_policy` isn't one of the two values above. Nothing changes and the stream doesn't
            advance.

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
    former_max_hp = character.max_hp
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
        # doesn't allow a lower one. The character is dead, not level 0.
        levels_lost = former_level - character.level + 1
        character.xp = 0
        events.append(
            LevelDrainedEvent(
                code="combat.drain.slain",
                target_id=character.id or character.name,
                levels_lost=levels_lost,
                new_level=0,
                hp_lost=former_max_hp - character.max_hp,
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
            hp_lost=former_max_hp - character.max_hp,
            xp_after=xp_after,
        )
    )
    events.append(
        HitPointsReportedEvent(
            target_id=character.id or character.name, current_hp=character.current_hp, max_hp=character.max_hp
        )
    )
    if getattr(character, "memorized_spells", ()):
        # Memorized spells beyond what the smaller slots can fit are forgotten, newest first.
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
    """Give a character experience points, and raise their level when the new total reaches the next level.

    This is how characters advance. B/X divides the experience a party earns evenly among the
    surviving members, rounded down. Work out each member's share, then call this once for each
    member with that share. It applies the class's experience modifier, adds the result to
    the character's `xp`, and calls [`level_up`][osrlib.core.classes.level_up] when the total
    reaches the next level's threshold. In a session,
    [`GameSession.award_adventure_xp`][osrlib.crawl.session.GameSession.award_adventure_xp],
    [`GameSession.award_immediate_xp`][osrlib.crawl.session.GameSession.award_immediate_xp], and
    the [`AwardXP`][osrlib.crawl.commands.AwardXP] command call it for you.

    One award never raises a character more than one level. An award large enough to reach the
    level after next is cut to 1 point short of that level's threshold, and the rest is lost. A
    first-level character who earns enough for third level ends at second level. The result's
    `clamped` is True when an award was cut.

    At the class's maximum level, the character gains no more levels, but the whole award still
    adds to `xp`.

    Args:
        character: The character receiving the award. Changed in place: experience points, and on
            a new level, the level and hit points too.
        definition: The character's class definition. Its `id` must match the character's `class_id`.
        award: The experience points to award, before the class's modifier. Not negative.
        stream: The stream for the hit die of a new level, usually
            `streams.get(`[`ADVANCEMENT_STREAM`][osrlib.core.character.ADVANCEMENT_STREAM]`)`. It
            doesn't advance when the award raises no level.

    Returns:
        The award, how the class changed it, and what the character gained. Its `modified_award` is
        the award after the class's modifier and before any cut, so compare it with `xp_after -
        xp_before` to show the player what a cut took.

    Raises:
        ValueError: If `definition` isn't the character's class, or `award` is negative. Nothing
            changes and the stream doesn't advance.

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
