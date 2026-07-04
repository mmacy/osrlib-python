"""Class definitions, level progression, XP awards, and leveling up.

The seven Classic classes compile from the SRD class pages into `classes.json` and load
as frozen [`ClassDefinition`][osrlib.core.classes.ClassDefinition] models via
[`load_classes`][osrlib.data.load_classes]. Class definitions are pure data —
requirements, prime requisites, XP-modifier tiers, progression tables, armour and
weapon policies, structured ability tags — so Advanced classes are additive data
rather than a redesign. `race` is a field, populated from the class in Classic play
(the data model is Advanced-ready).

XP-modifier tiers are one uniform representation for all classes: ordered tiers of
`{modifier_pct, minimum scores}`, evaluated best-first, first tier whose conditions all
hold wins (pinned: the standard single-prime-requisite table's penalty rows only work
under first-match-wins). Elf and halfling carry exactly their stated bonus tiers, which
per RAW include no penalties — a pinned interpretation recorded in
`docs/adaptations.md`.

Saves, THAC0, and spell slots are always read from the progression row for the
character's level, never stored derivations —
[`ClassDefinition.row`][osrlib.core.classes.ClassDefinition.row] is the pure
recompute-from-level function whose inverse becomes Phase 2's energy drain.
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
    "Race",
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
"""The six d% roll-under thief skills; `hear_noise` is the seventh, rolled on 1d6."""


class Race(StrEnum):
    """Character races; in Classic play, race is implied by class.

    The wire values are lowercase — they serialize into characters and saves; changing
    them is a `schema_version` bump.
    """

    HUMAN = "human"
    DWARF = "dwarf"
    ELF = "elf"
    HALFLING = "halfling"


class HitDice(BaseModel):
    """A progression row's Hit Dice: `count` dice of `die` sides plus a flat `bonus`.

    Above name level the SRD notates flat bonuses with an asterisk (`9d8+2*`) meaning
    CON modifiers no longer apply — the asterisk is data, carried as `con_applies`.
    """

    model_config = ConfigDict(frozen=True)

    count: int = Field(ge=1)
    die: int
    bonus: int = Field(default=0, ge=0)
    con_applies: bool = True

    @model_validator(mode="after")
    def _die_must_be_rollable(self) -> HitDice:
        if self.die not in ALLOWED_SIDES:
            raise ValueError(f"hit die size must be one of {sorted(ALLOWED_SIDES)}, got {self.die}")
        return self


class SavingThrows(BaseModel):
    """The five save values: death/poison, wands, paralysis/petrify, breath, spells/rods/staves."""

    model_config = ConfigDict(frozen=True)

    death: int = Field(ge=2, le=20)
    wands: int = Field(ge=2, le=20)
    paralysis: int = Field(ge=2, le=20)
    breath: int = Field(ge=2, le=20)
    spells: int = Field(ge=2, le=20)


class ProgressionRow(BaseModel):
    """One level of a class progression table, exactly as the SRD prints it.

    THAC0 is dual-format in the SRD (`19 [0]`); both the descending value and the
    bracketed attack bonus are carried. `spell_slots[i]` is the number of memorizable
    spells of spell level `i + 1`; the tuple is empty for non-casters.
    """

    model_config = ConfigDict(frozen=True)

    level: int = Field(ge=1)
    xp: int = Field(ge=0)
    hit_dice: HitDice
    thac0: int = Field(ge=2, le=20)
    attack_bonus: int = Field(ge=0)
    saves: SavingThrows
    spell_slots: tuple[int, ...] = ()


class XpTier(BaseModel):
    """One XP-modifier tier: the modifier applies when every minimum holds."""

    model_config = ConfigDict(frozen=True)

    modifier_pct: int
    minimums: dict[AbilityScore, int]

    @model_validator(mode="after")
    def _minimums_must_be_scores(self) -> XpTier:
        if not self.minimums:
            raise ValueError("an XP tier must name at least one minimum score")
        for ability, minimum in self.minimums.items():
            if not MIN_SCORE <= minimum <= MAX_SCORE:
                raise ValueError(f"minimum for {ability} must be in {MIN_SCORE}-{MAX_SCORE}, got {minimum}")
        return self


class ArmourPolicyKind(StrEnum):
    """What armour a class may wear."""

    ANY = "any"
    LEATHER_ONLY = "leather_only"
    NONE = "none"


class ArmourPolicy(BaseModel):
    """A class's armour policy: the allowed kinds plus whether shields are allowed."""

    model_config = ConfigDict(frozen=True)

    kind: ArmourPolicyKind
    shields_allowed: bool

    @model_validator(mode="after")
    def _no_armour_means_no_shields(self) -> ArmourPolicy:
        if self.kind is ArmourPolicyKind.NONE and self.shields_allowed:
            raise ValueError("a class that can wear no armour cannot use shields")
        return self


class WeaponPolicyKind(StrEnum):
    """How a class's weapon list is expressed."""

    ANY = "any"
    ALLOWED = "allowed"
    FORBIDDEN = "forbidden"


class WeaponPolicy(BaseModel):
    """A class's weapon policy.

    `weapon_ids` is the explicit allow list (cleric: the five blunt weapons) or the
    forbidden list (dwarf and halfling: `long_bow`, `two_handed_sword`), and is empty
    for `any`. `manual_notes` keeps referee-judgment stature prose (the dwarf's "small
    or normal sized", the halfling's "appropriate to stature") that cannot be
    mechanized. The policy governs the weapons list only; gear combat facets are exempt
    (see [`validate_equip`][osrlib.core.items.validate_equip]).
    """

    model_config = ConfigDict(frozen=True)

    kind: WeaponPolicyKind
    weapon_ids: tuple[str, ...] = ()
    manual_notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _ids_must_match_kind(self) -> WeaponPolicy:
        if self.kind is WeaponPolicyKind.ANY and self.weapon_ids:
            raise ValueError("an 'any' weapon policy must not list weapon ids")
        if self.kind is not WeaponPolicyKind.ANY and not self.weapon_ids:
            raise ValueError(f"a {self.kind.value!r} weapon policy must list weapon ids")
        return self


class ThiefSkillRow(BaseModel):
    """One level of the thief skill table.

    Skills are d% roll-under percentages except `hear_noise`, an X-in-6 upper bound
    (the SRD's `1–2` is stored as 2). Pick pockets can exceed 100 at high level; the
    over-100 arithmetic belongs to the Phase 2 skill-check procedure.
    """

    model_config = ConfigDict(frozen=True)

    level: int = Field(ge=1)
    climb_sheer_surfaces: int = Field(ge=0)
    find_remove_treasure_traps: int = Field(ge=0)
    hear_noise: int = Field(ge=1, le=6)
    hide_in_shadows: int = Field(ge=0)
    move_silently: int = Field(ge=0)
    open_locks: int = Field(ge=0)
    pick_pockets: int = Field(ge=0)


class ClassAbility(BaseModel):
    """A structured class-ability tag plus the SRD prose it came from.

    `params` carries the mechanizable numbers (`{"range_feet": 60}` for infravision);
    `manual` marks abilities that need referee judgment and stay prose. Procedures that
    consume these tags land in Phases 2–4.
    """

    model_config = ConfigDict(frozen=True)

    tag: str
    name: str
    prose: str
    manual: bool = False
    params: dict[str, int | str] = {}


class ClassDefinition(BaseModel):
    """A character class, compiled from its SRD page.

    Frozen SRD data: play never mutates a class definition. `requirements` are minimum
    scores checked at class choice; `may_not_lower` carries adjustment-step
    restrictions (the thief's STR). `level_titles[i]` is the title at level `i + 1`;
    the SRD's title lists run only through name level, so they are shorter than the
    progression and levels beyond them have no title entry.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    race: Race
    requirements: dict[AbilityScore, int] = {}
    prime_requisites: tuple[AbilityScore, ...]
    xp_tiers: tuple[XpTier, ...]
    hit_die: int
    max_level: int = Field(ge=1)
    armour: ArmourPolicy
    weapons: WeaponPolicy
    languages: tuple[str, ...]
    may_not_lower: tuple[AbilityScore, ...] = ()
    abilities: tuple[ClassAbility, ...] = ()
    thief_skills: tuple[ThiefSkillRow, ...] = ()
    level_titles: tuple[str, ...] = ()
    progression: tuple[ProgressionRow, ...]
    overrides_applied: tuple[str, ...] = ()

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
        """Return the progression row for `level` — the pure recompute-from-level lookup.

        Saves, THAC0, attack bonus, and spell slots are read from here, never stored:
        Phase 2's energy drain is this function's inverse, not a redesign.

        Args:
            level: The character level, 1 through the class maximum.

        Returns:
            The progression row.

        Raises:
            ValueError: If `level` is outside the class's range.
        """
        if not 1 <= level <= self.max_level:
            raise ValueError(f"{self.id} levels are 1-{self.max_level}, got {level}")
        return self.progression[level - 1]


class ClassCatalog(BaseModel):
    """The loaded class list, with id lookup."""

    model_config = ConfigDict(frozen=True)

    classes: tuple[ClassDefinition, ...]

    @model_validator(mode="after")
    def _ids_must_be_unique(self) -> ClassCatalog:
        ids = [definition.id for definition in self.classes]
        if len(set(ids)) != len(ids):
            raise ValueError("class ids must be unique")
        return self

    def get(self, class_id: str) -> ClassDefinition:
        """Return the class definition for `class_id`.

        Args:
            class_id: The class id, e.g. `"fighter"`.

        Returns:
            The class definition.

        Raises:
            ValueError: If no class has that id.
        """
        for definition in self.classes:
            if definition.id == class_id:
                return definition
        raise ValueError(f"unknown class id {class_id!r}")


class LevelUpResult(BaseModel):
    """The outcome of gaining one level.

    While HD count still grows, `hp_roll` is the raw die (CON applies when
    `con_applied`, minimum 1 hp gained); above name level the gain is the flat-bonus
    delta with no roll and no CON.
    """

    model_config = ConfigDict(frozen=True)

    new_level: int
    hp_roll: int | None
    hp_gained: int
    con_applied: bool


class XpAwardResult(BaseModel):
    """The outcome of applying an XP award.

    `modified_award` is the award after the class XP-modifier percentage, floored.
    `clamped` reports the one-level-per-award rule firing: XP that would reach two or
    more levels above the starting level stops at 1 XP below the second level's
    threshold.
    """

    model_config = ConfigDict(frozen=True)

    award: int
    modifier_pct: int
    modified_award: int
    xp_before: int
    xp_after: int
    level_before: int
    level_after: int
    clamped: bool
    level_up: LevelUpResult | None


def xp_modifier_pct(definition: ClassDefinition, scores: dict[AbilityScore, int]) -> int:
    """Return the class XP-modifier percentage for a score set.

    Tiers are evaluated best-first; the first tier whose minimums all hold wins
    (pinned interpretation: the standard table's penalty rows only work under
    first-match-wins). With no matching tier the modifier is zero — which is how the
    multi-prime-requisite classes carry no penalties per RAW.

    Args:
        definition: The class definition.
        scores: The character's final ability scores.

    Returns:
        The XP modifier as a signed percentage (`+10`, `-20`, `0`).
    """
    for tier in definition.xp_tiers:
        if all(scores[ability] >= minimum for ability, minimum in tier.minimums.items()):
            return tier.modifier_pct
    return 0


def level_up(character: Character, definition: ClassDefinition, stream: RngStream) -> LevelUpResult:
    """Advance a character one level, rolling hit points per the SRD.

    While the HD count still grows, the gain is a new hit die roll plus the CON
    modifier, minimum 1. Above name level the gain is the flat-bonus delta between the
    progression rows — no roll, no CON. Both max and current hit points increase by
    the gain: leveling adds hit points but heals no damage already taken. Saves,
    THAC0, and spell slots are never stored — read them from
    [`ClassDefinition.row`][osrlib.core.classes.ClassDefinition.row].

    Args:
        character: The character to advance; mutated in place.
        definition: The character's class definition.
        stream: The RNG stream for the hit die roll, conventionally
            [`ADVANCEMENT_STREAM`][osrlib.core.character.ADVANCEMENT_STREAM].

    Returns:
        The level-up outcome, including the raw hit die roll if one was made.

    Raises:
        ValueError: If the definition doesn't match the character's class, or the
            character is already at the class's maximum level.
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
    """A thief skill check's outcome.

    `chance` is the effective target after modifiers (the pick-pockets ≥1%-failure
    cap applied). `noticed` is set only for pick pockets: a roll of more than twice
    the effective chance means the attempted theft is noticed (RAW) — what the
    victim does about it is a game procedure.
    """

    model_config = ConfigDict(frozen=True)

    skill: str
    roll: int
    chance: int
    passed: bool
    noticed: bool | None = None


class DetectionResult(BaseModel):
    """An X-in-6 detection check's outcome.

    `roll` is `None` for a zero chance — no die is consumed (there is nothing to
    roll under), pinned; the non-dwarf searching for construction tricks simply
    fails.
    """

    model_config = ConfigDict(frozen=True)

    chance: int
    roll: int | None = None
    passed: bool


def thief_skill_check(
    character: Character, definition: ClassDefinition, skill: str, *, modifier_pct: int = 0, stream: RngStream
) -> SkillCheckResult:
    """Roll one thief skill check — an à la carte plain result, no events.

    The six percentile skills roll d% with success on a result less than or equal
    to the level row's chance; `hear_noise` rolls 1d6 against its X-in-6 bound. The
    crawl procedures emit the events (and hide the referee-rolled outcomes); this
    function just resolves the dice.

    Pick pockets, per its RAW bullet: the caller folds the victim's over-5th-level
    penalty into `modifier_pct` (−5% per victim level above 5th — the kernel never
    sees the victim), the effective chance caps at 99 ("always at least a 1% chance
    of failure"), and a roll of more than twice the effective chance sets `noticed`.

    Args:
        character: The rolling thief.
        definition: The character's class definition; must carry a thief skill
            table.
        skill: A percentile skill name from
            [`PERCENTILE_THIEF_SKILLS`][osrlib.core.classes.PERCENTILE_THIEF_SKILLS],
            or `"hear_noise"`.
        modifier_pct: A percentage adjustment to the percentile chance (ignored for
            `hear_noise`).
        stream: The RNG stream, conventionally the crawl's `"exploration"` stream.

    Returns:
        The check outcome.

    Raises:
        ValueError: If the class has no thief skills or the skill name is unknown —
            gating who may attempt a skill is the caller's validation.
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
    """Roll the shared X-in-6 detection check: searching, listening, demi-human tags.

    A zero (or negative) chance consumes no draw and fails — pinned: the SRD grants
    construction-trick perception to dwarves alone, and there is nothing to roll
    under.

    Args:
        chance_in_six: The X-in-6 chance, from
            [`detection_chance`][osrlib.core.classes.detection_chance] or a class
            tag.
        stream: The RNG stream, conventionally the crawl's `"exploration"` stream.

    Returns:
        The check outcome.
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
    """Resolve a character's X-in-6 chance for one detection kind.

    The pinned precedence: listening uses the thief's `hear_noise` row when
    present, else the class's `listening_at_doors` param, else the universal
    1-in-6; secret doors use `detect_secret_doors` (elf 2) else 1; room traps use
    `detect_room_traps` (dwarf 2) else 1; construction tricks use
    `detect_construction_tricks` (dwarf 2) else **zero** — the SRD grants the
    perception to dwarves alone, and "as a dwarf you can sense" has no baseline for
    others, unlike the universal 1-in-6 search chances which the SRD states for all
    PCs.

    Args:
        character: The detecting character.
        definition: The character's class definition.
        kind: One of `"listening"`, `"secret_doors"`, `"room_traps"`, or
            `"construction"`.

    Returns:
        The X-in-6 chance (0 means no chance at all).

    Raises:
        ValueError: If the kind is unknown.
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
    """The outcome of energy drain.

    `hp_rolls` are the raw hit dice rolled for the drained levels (empty above name
    level, where the loss is the flat-bonus delta). `slain` marks the terminal case:
    a person drained of all levels dies, and `spawn_consequence` carries the SRD's
    spawn prose as a structured-but-manual field — the kernel kills, the game
    narrates.
    """

    model_config = ConfigDict(frozen=True)

    levels_lost: int
    new_level: int = Field(ge=0)
    hp_rolls: tuple[int, ...] = ()
    hp_lost: int
    xp_after: int | None = None
    slain: bool = False
    events: tuple[Event, ...] = ()


def drain_levels(
    character: Character,
    definition: ClassDefinition,
    *,
    levels: int = 1,
    xp_policy: str,
    stream: RngStream,
    spawn_consequence: str | None = None,
) -> DrainResult:
    """Drain experience levels — the inverse of [`level_up`][osrlib.core.classes.level_up].

    Saves, THAC0, and spell slots need no reversal because they derive from
    [`row`][osrlib.core.classes.ClassDefinition.row]; only stored state reverses.
    Per level drained, mirroring `level_up` exactly in reverse: above name level
    subtract the flat-bonus delta (no roll, no CON); otherwise roll the class hit die
    plus the CON modifier (minimum 1 per die) and subtract it from max and current
    hit points — rolling the lost die is the RAW-faithful reading of "loses one Hit
    Die of hit points" that keeps the model stateless (pinned).

    Pinned floors: drain never reduces max HP below 1 or current HP below 1 while
    the character retains a level — death by drain happens only by losing the last
    level ("a person drained of all levels"), the terminal state. XP is set once
    after all levels drain, by policy: `halfway` is the floored midpoint of the
    former and new levels' thresholds (the wight); `level_minimum` is the new
    level's threshold exactly (wraith, spectre, vampire).

    Args:
        character: The drained character; mutated in place.
        definition: The character's class definition.
        levels: How many levels the drain removes (the spectre and vampire drain
            two, applying the procedure twice).
        xp_policy: `"halfway"` or `"level_minimum"` — per-monster data from the
            `energy_drain` tag.
        stream: The RNG stream for the lost hit die rolls, conventionally
            [`ADVANCEMENT_STREAM`][osrlib.core.character.ADVANCEMENT_STREAM] — the
            same subsystem as the gains it reverses (pinned).
        spawn_consequence: The monster's spawn prose, carried on the drain event.

    Returns:
        The drain outcome, including the terminal death when all levels are lost.

    Raises:
        ValueError: If the definition doesn't match the character's class, `levels`
            is not positive, or the policy is unknown.
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
        # The killing level counts as lost: a level-1 victim loses 1 level, a
        # spectre draining a level-2 fighter reports 2 (the model's level floor of 1
        # stays — the character is dead, not level 0).
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
        # The drain/memorization interplay: memorized copies in excess of the shrunk
        # slots are forgotten newest-first. Runtime imports because the spells module
        # sits above this one in the import graph (spells → combat → classes).
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
    """Apply an XP award: class modifier, the one-level-per-award rule, and leveling.

    The class XP-modifier percentage applies first, with the result floored (pinned).
    Then the rule exactly as written: XP that would reach two or more levels above the
    starting level is clamped to 1 XP below the second level's threshold, and the
    character gains one level. At the class's maximum level no further levels are
    gained but XP keeps accumulating, unclamped — there is no next threshold to hold
    the character under.

    Args:
        character: The character receiving the award; mutated in place.
        definition: The character's class definition.
        award: The unmodified XP award. Non-negative.
        stream: The RNG stream for a level-up hit die roll.

    Returns:
        The award outcome, including the level-up result when one occurred.

    Raises:
        ValueError: If the definition doesn't match the character's class or the
            award is negative.
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
