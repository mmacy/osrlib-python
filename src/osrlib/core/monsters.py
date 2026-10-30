"""Monster templates, the creatures spawned from them, and the ids they are given.

A monster has two models. A [`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate] is a stat
block: the shared description of one kind of creature, so one troll template describes every
troll. A [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] is one creature in play, with
separate hit points, wounds, and conditions. You spawn an instance from a template, and a change
to the instance doesn't change the template or the other instances spawned from it.

The members, in the order you use them:

- [`load_monsters`][osrlib.data.load_monsters] returns the built-in
  [`MonsterCatalog`][osrlib.core.monsters.MonsterCatalog]. Its
  [`get`][osrlib.core.monsters.MonsterCatalog.get] takes a monster id, like `"troll"`, and returns
  the [`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate].
- [`IdAllocator.allocate`][osrlib.core.monsters.IdAllocator.allocate] takes a prefix, like
  `"monster"`, and returns a new entity id, like `"monster-0001"`. Events refer to a creature by
  its entity id.
- [`spawn_monster`][osrlib.core.monsters.spawn_monster] takes a template, an entity id, and the
  stream named by [`MONSTER_SPAWN_STREAM`][osrlib.core.monsters.MONSTER_SPAWN_STREAM], rolls hit
  points, and returns a [`MonsterInstance`][osrlib.core.monsters.MonsterInstance].
- The template's fields contain the rest of the stat block as models you read:
  [`MonsterHitDice`][osrlib.core.monsters.MonsterHitDice],
  [`AttackRoutine`][osrlib.core.monsters.AttackRoutine] with
  [`RoutineUse`][osrlib.core.monsters.RoutineUse], and
  [`MonsterAttack`][osrlib.core.monsters.MonsterAttack],
  [`MovementMode`][osrlib.core.monsters.MovementMode],
  [`MonsterSaves`][osrlib.core.monsters.MonsterSaves],
  [`MoraleAlternate`][osrlib.core.monsters.MoraleAlternate],
  [`AlignmentSpec`][osrlib.core.monsters.AlignmentSpec], [`XpNote`][osrlib.core.monsters.XpNote],
  [`NumberAppearing`][osrlib.core.monsters.NumberAppearing] and
  [`NumberAppearingValue`][osrlib.core.monsters.NumberAppearingValue],
  [`TreasureRef`][osrlib.core.monsters.TreasureRef],
  [`MonsterAbility`][osrlib.core.monsters.MonsterAbility],
  [`AcAlternate`][osrlib.core.monsters.AcAlternate], and
  [`Defenses`][osrlib.core.monsters.Defenses] with
  [`DamageKey`][osrlib.core.monsters.DamageKey],
  [`DamageReduction`][osrlib.core.monsters.DamageReduction],
  [`Element`][osrlib.core.monsters.Element], and
  [`EnergyDefense`][osrlib.core.monsters.EnergyDefense].

Every model here except `MonsterInstance` and `IdAllocator` is frozen: assigning to one of its
fields raises `pydantic.ValidationError`.

A monster instance is a combatant. Pass it to the functions in
[`osrlib.core.combat`][osrlib.core.combat], which take a
[`Character`][osrlib.core.character.Character] or a monster instance as attacker or target, and to
[`osrlib.core.effects`][osrlib.core.effects] for conditions and timed modifiers. For its treasure,
pass the template's [`TreasureRef`][osrlib.core.monsters.TreasureRef] to
[`plan_treasure_ref`][osrlib.core.treasure.plan_treasure_ref]. The experience it's worth is the
template's `xp`.

What a monster can do beyond its attacks is on the template as
[`MonsterAbility`][osrlib.core.monsters.MonsterAbility] records. Each record has a tag, the SRD's
text, and the numbers the SRD gives, in `params`. The combat functions read four tags:
`regeneration` in [`deal_damage`][osrlib.core.combat.deal_damage], `energy_drain` in
[`resolve_energy_drain`][osrlib.core.combat.resolve_energy_drain], `breath_weapon` in
[`resolve_breath`][osrlib.core.combat.resolve_breath], and `uses_fire` in
[`check_immunity`][osrlib.core.combat.check_immunity]. Battle also reads `paralysis`, `poison`,
`disease`, and `petrification` when a monster's attack hits. An ability marked `manual` is text for a
referee to read. What a monster resists is in [`Defenses`][osrlib.core.monsters.Defenses], which
the combat functions check every time an attack hits.

Each monster page in the SRD becomes one template per creature it describes, so a page with
several sizes of hydra becomes one template per size. A template that covered several creatures
couldn't be spawned by itself.

Hit points are rolled on the [`MONSTER_SPAWN_STREAM`][osrlib.core.monsters.MONSTER_SPAWN_STREAM]
stream, which is separate from the combat stream. A change to the combat rules therefore never
changes the creatures that spawn in a seeded scenario.

Typical usage:

```python
from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.data import load_monsters

allocator = IdAllocator()
stream = RngStreams(master_seed=3).get(MONSTER_SPAWN_STREAM)
template = load_monsters().get("troll")
troll = spawn_monster(template, id=allocator.allocate("monster"), stream=stream)
print(troll.id, troll.name, troll.max_hp, troll.armour_class, troll.thac0)
# monster-0001 Troll 42 4 13
print(template.xp, template.ability("regeneration").params["per_round"])
# 650 3
```
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from osrlib.core.alignment import Alignment
from osrlib.core.classes import SavingThrows
from osrlib.core.dice import parse
from osrlib.core.effects import ActiveCondition, ActiveModifier, Condition
from osrlib.core.rng import RngStream, StreamName

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
    "RoutineUse",
    "TreasureRef",
    "XpNote",
    "spawn_monster",
]

MONSTER_SPAWN_STREAM = StreamName.MONSTER_SPAWN
"""The stream key every session uses for rolling a spawned monster's hit points.

A stream key identifies one independent random-number sequence inside an
[`RngStreams`][osrlib.core.rng.RngStreams] set. Pass `streams.get(MONSTER_SPAWN_STREAM)` as the
`stream` argument of [`spawn_monster`][osrlib.core.monsters.spawn_monster].

It is separate from the combat stream so that a change to how a fight resolves never changes the
creatures that spawn in a seeded scenario.
"""


class DamageKey(StrEnum):
    """A kind of damage source that a monster's defenses can list.

    For a monster that only certain kinds of attack can hurt,
    [`Defenses.harmed_only_by`][osrlib.core.monsters.Defenses.harmed_only_by] lists them. For a
    monster that takes reduced damage from a kind,
    [`DamageReduction.keys`][osrlib.core.monsters.DamageReduction.keys] lists it. The damage rules
    compare these with the keys and the element of the attack's
    [`DamageSource`][osrlib.core.combat.DamageSource].

    `holy` works differently from the others. Holy water presents it, and against an undead target
    it gets past any `harmed_only_by` list, because in the SRD holy water harms undead. Without that
    exception, the wight's silver-or-magic list would stop holy water. Against a target that isn't
    undead, a `holy` source does no damage.
    """

    SILVER = "silver"
    """A silver weapon, which harms a wight, a wraith, or a werewolf that steel can't."""

    MAGIC = "magic"
    """An enchanted weapon or a spell."""

    FIRE = "fire"
    """Fire, whether from a torch, a flask of oil, or a spell."""

    COLD = "cold"
    """Cold, from a creature's attack or a spell."""

    HOLY = "holy"
    """Holy water, which harms undead whatever else they resist."""


class Element(StrEnum):
    """A kind of energy that a breath weapon, an attack, or a spell can deal, and a monster can resist.

    A dragon's breath has one of these elements, and so does each entry in
    [`Defenses.energy`][osrlib.core.monsters.Defenses.energy]. Before any damage is rolled, the
    combat functions look up the attack's element there to decide whether the attack hurts the monster
    at all.
    """

    FIRE = "fire"
    """Fire, as a red dragon breathes and a fire giant ignores."""

    COLD = "cold"
    """Cold, as a white dragon breathes."""

    LIGHTNING = "lightning"
    """Lightning, as a blue dragon breathes."""

    ACID = "acid"
    """Acid, as a black dragon breathes."""

    GAS = "gas"
    """Poisonous gas, as a green dragon breathes."""

    POISON = "poison"
    """Poison that isn't a gas, as a sea dragon spits."""

    STEAM = "steam"
    """Scalding steam, as a dragon turtle breathes."""


class EnergyDefense(BaseModel):
    """How a monster resists one kind of energy.

    Read them from [`Defenses.energy`][osrlib.core.monsters.Defenses.energy], keyed by
    [`Element`][osrlib.core.monsters.Element]. Before rolling damage,
    [`check_immunity`][osrlib.core.combat.check_immunity] looks up the element of the incoming
    attack, and [`saving_throw`][osrlib.core.combat.saving_throw] reads `auto_save_magical`. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    immunity: Literal["all", "nonmagical"]
    """How much of this element the creature ignores.

    `"all"` means no damage of this element hurts the creature, magical or not, as with a fire giant
    and fire. `"nonmagical"` means only magical damage of this element hurts it. A red dragon takes
    no damage from a dragon's fire breath or a flask of burning oil, and still takes damage from a
    *fire ball*.
    """

    auto_save_magical: bool = False
    """True when the creature passes every saving throw against a magical form of this element without rolling.

    This is a dragon's automatic save against a spell of the element it resists, like a red dragon's save
    against *fire ball*, so it takes half damage.
    """


class DamageReduction(BaseModel):
    """A reduction of the damage a monster takes, applied after the dice are rolled.

    Read them from [`Defenses.reductions`][osrlib.core.monsters.Defenses.reductions].
    [`deal_damage`][osrlib.core.combat.deal_damage] divides the damage by `divisor` and rounds
    down, and a hit that gets through always does at least 1 point. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    keys: tuple[DamageKey, ...] = ()
    """Which sources of damage are reduced.

    Empty means every source that gets past `harmed_only_by`, as with the mummy, which takes half
    damage from everything. A list of keys limits the reduction to sources that present one of
    them, as with the wraith, which takes half damage from silver weapons only.
    """

    divisor: int = Field(default=2, ge=2)
    """What the damage is divided by, 2 or more. The SRD prints only halving, which is 2."""


class Defenses(BaseModel):
    """What a monster resists, in the form the damage rules check.

    Read it as [`MonsterTemplate.defenses`][osrlib.core.monsters.MonsterTemplate.defenses]. Every
    time an attack hits, [`check_immunity`][osrlib.core.combat.check_immunity] reads it before any
    damage is rolled, and [`deal_damage`][osrlib.core.combat.deal_damage] applies the reductions.
    The SRD's wording for the same defenses is in the template's `abilities`, for you to show.
    Frozen.
    """

    model_config = ConfigDict(frozen=True)

    harmed_only_by: tuple[DamageKey, ...] = ()
    """The kinds of damage that can hurt this creature at all.

    An attack that presents none of them is absorbed, and no damage is rolled. That is how a wight
    ignores an ordinary sword. Empty means anything hurts it.
    """

    reductions: tuple[DamageReduction, ...] = ()
    """Reductions of the damage that does get through. See
    [`DamageReduction`][osrlib.core.monsters.DamageReduction].
    """

    energy: dict[Element, EnergyDefense] = {}
    """How the creature resists each kind of energy, keyed by element. See
    [`EnergyDefense`][osrlib.core.monsters.EnergyDefense]. An element with no entry hurts it as usual.
    """

    condition_immunities: tuple[Condition, ...] = ()
    """Conditions the creature can never be put into, like the undead's immunity to being poisoned, charmed, or put
    to sleep. [`grant_condition`][osrlib.core.effects.grant_condition] grants nothing when you try.
    """


class MonsterHitDice(BaseModel):
    """A monster's Hit Dice, as the stat block prints them.

    Read it as [`MonsterTemplate.hit_dice`][osrlib.core.monsters.MonsterTemplate.hit_dice].
    [`spawn_monster`][osrlib.core.monsters.spawn_monster] rolls hit points from it, and
    [`osrlib.core.tables`][osrlib.core.tables] uses it to place the monster on the attack matrix
    and in an experience band. The SRD's `6+3*` is a `count` of 6, a `modifier` of 3, and one
    asterisk.

    Constructing one raises `pydantic.ValidationError` when `die` is not 8 or 4, or when `count` is
    0 and `fixed_hp` is `None`. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    count: int = Field(default=0, ge=0)
    """How many dice are rolled. 0 for a creature whose hit points are fixed."""

    die: int = 8
    """The size of each die: 8, or 4 for a creature with half a Hit Die, which rolls 1d4."""

    modifier: int = 0
    """Hit points added to the rolled total, or taken from it when negative. The SRD's `1-1` is a modifier of −1.

    A positive modifier also makes the creature attack as though it had one more Hit Die, and moves
    it to the higher `+` row of its experience band. A negative modifier drops it a row.
    """

    asterisks: int = Field(default=0, ge=0)
    """How many asterisks the SRD prints after the Hit Dice, one per special ability. Each one raises the experience
    award.
    """

    average_hp: int | None = None
    """The average hit points the SRD prints, for a referee who would rather not roll. `spawn_monster` ignores it and
    rolls.
    """

    fixed_hp: int | None = None
    """Hit points that are not rolled, like a normal bat's 1 or a hydra's 8 per head. `None` when they're rolled."""

    @model_validator(mode="after")
    def _rollable_or_fixed(self) -> MonsterHitDice:
        if self.die not in (4, 8):
            raise ValueError(f"monster hit die must be d8, or d4 for ½ HD, got d{self.die}")
        if self.count == 0 and self.fixed_hp is None:
            raise ValueError("a monster needs hit dice to roll or fixed hit points")
        return self


class MonsterAttack(BaseModel):
    """One attack a monster makes: its name, how often it's made, how much it hurts, and what else it does.

    Read them from [`AttackRoutine.attacks`][osrlib.core.monsters.AttackRoutine.attacks], and pass
    one to [`resolve_attack`][osrlib.core.combat.resolve_attack] as the attack. A troll's routine
    is two talon attacks and a bite, so it has two of these, and the talon's `count` is 2.

    Constructing one raises `pydantic.ValidationError` when `damage` is not a valid dice
    expression. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    count: int = Field(default=1, ge=1)
    """How many of this attack the monster makes in a round."""

    name: str = Field(min_length=1)
    """The attack's name as the SRD prints it, like `"talon"`, `"bite"`, or `"weapon"`."""

    damage: str | None = None
    """The damage as a dice expression, like `"1d6"`, which [`roll`][osrlib.core.dice.roll] evaluates.

    `None` when the damage is fixed, and when the attack has no damage dice, like a
    dragon's breath, whose damage comes from its `breath_weapon` ability.
    """

    fixed_damage: int | None = None
    """Damage that is a flat number rather than a roll. `None` when `damage` is set."""

    fixed_damage_options: tuple[int, ...] = ()
    """The alternative damage values the SRD prints when the referee decides between them.

    An insect swarm does 2 or 4 damage depending on the target's armour, and the referee picks one.
    Empty for every other attack.
    """

    by_weapon: bool = False
    """True when the monster attacks with whatever weapon it has, so `damage` is the SRD's typical value rather than
    a fixed property of the creature.
    """

    by_weapon_modifier: int = 0
    """The bonus or penalty the SRD prints with a by-weapon attack, like a bugbear's +1 to damage."""

    effects: tuple[str, ...] = ()
    """What a hit does beyond damage, as tags like `"poison"`, `"paralysis"`, or `"energy_drain"`.

    For the numbers behind a tag, call [`MonsterTemplate.ability`][osrlib.core.monsters.MonsterTemplate.ability]
    with the same tag. It returns `None` when the template has no ability with that tag.
    """

    @field_validator("damage")
    @classmethod
    def _damage_must_parse(cls, value: str | None) -> str | None:
        if value is not None:
            parse(value)
        return value


class RoutineUse(StrEnum):
    """When a monster uses one of its attack routines, as the OSE SRD's stat block and description put it.

    Read it as [`AttackRoutine.use`][osrlib.core.monsters.AttackRoutine.use]. The first routine of
    every template is `PRIMARY`. Each later routine has one of the other three values, which tell a
    policy whether it may pick the routine in a round of melee. The default policy,
    [`ScriptedPolicy`][osrlib.crawl.battle.ScriptedPolicy], picks with equal chance among a
    monster's `PRIMARY` and `ALTERNATIVE` routines. Your own policy can name any routine by its
    index in [`MonsterAction.routine`][osrlib.crawl.battle.MonsterAction.routine].
    """

    PRIMARY = "primary"
    """The first routine the stat block prints, which battle resolves when a policy names no routine."""

    ALTERNATIVE = "alternative"
    """A routine the monster may use in place of the first in any round, as the SRD's "or" offers.

    A thoul claws or uses a weapon, and a mule kicks or bites. The default policy picks it as often
    as the first routine.
    """

    SITUATIONAL = "situational"
    """A routine the SRD ties to a situation osrlib doesn't model, so the default policy never picks it.

    The situation is a range, a charge, a chance to trample, water, a ship, a form, or a loadout:
    an elephant's trample, a giant's thrown boulder, a sperm whale's ram against a ship. A policy of
    your own can name the routine when your game decides the situation applies, and battle then
    rolls its attacks as melee attacks.
    """

    SPECIAL = "special"
    """A routine battle never rolls as an attack: a breath, a gaze, or a spell.

    A dragon breathes through a `"breath"` action, a petrifying gaze runs once a round before its
    group acts, and the rest are left to the referee. Naming this routine in a `"melee"` action
    makes the monster roll nothing that round.
    """


class AttackRoutine(BaseModel):
    """One set of attacks a monster can make in a round, chosen as a whole.

    Read them from [`MonsterTemplate.attacks`][osrlib.core.monsters.MonsterTemplate.attacks]. Most
    creatures have one. A creature with more than one uses one of them in a round, not all of them:
    a dragon either claws and bites or breathes. The routine's `use` says when the SRD lets the
    monster use it, and an action policy picks one each round. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    attacks: tuple[MonsterAttack, ...] = Field(min_length=1)
    """The attacks the routine makes, at least one. See [`MonsterAttack`][osrlib.core.monsters.MonsterAttack]."""

    use: RoutineUse = RoutineUse.PRIMARY
    """When the monster uses this routine. See [`RoutineUse`][osrlib.core.monsters.RoutineUse].

    The first routine of a template is `PRIMARY`. Every later routine in the built-in catalogs
    carries one of the other labels, taken from the OSE SRD's text for that monster. In a template
    of your own, label each later routine: one you leave unlabelled is `PRIMARY` too, so the default
    policy picks it as often as the first.
    """


class MovementMode(BaseModel):
    """One way a monster moves, and how fast.

    Read them from [`MonsterTemplate.movement`][osrlib.core.monsters.MonsterTemplate.movement]. The
    first is the rate the stat block prints first, which is the creature's main way of moving.
    Later ones are its other ways of moving, and you pick the one the situation calls for. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    rate_feet: int = Field(ge=0)
    """Feet covered in one exploration turn of 10 minutes, the rate used while mapping and searching."""

    encounter_rate_feet: int = Field(ge=0)
    """Feet covered in one combat round, a third of `rate_feet`, rounded down."""

    descriptor: str | None = None
    """How the creature is moving, in the SRD's word: `"flying"`, `"swimming"`, `"in webs"`. `None` for walking."""


class MonsterSaves(BaseModel):
    """A monster's saving throws, and the stat block's entry for which class and level it saves as.

    Read it as [`MonsterTemplate.saves`][osrlib.core.monsters.MonsterTemplate.saves]. To get the
    values adjusted for a creature that has been drained of Hit Dice, read
    [`MonsterInstance.saves`][osrlib.core.monsters.MonsterInstance.saves] instead. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    values: SavingThrows
    """The five saving throw targets. See [`SavingThrows`][osrlib.core.classes.SavingThrows]."""

    save_as: str
    """The stat block's save-as entry, like `"2"` for a 2nd-level fighter, `"NH"` for a normal human, or `"Cleric 1"`.

    Show it, or use it to check `values` against the SRD's tables. osrlib uses only `values`.
    """


class MoraleAlternate(BaseModel):
    """A morale score that applies only in a particular situation.

    Read them from [`MonsterTemplate.morale_alternates`][osrlib.core.monsters.MonsterTemplate.morale_alternates]. A
    creature that fights bravely except when fire is involved has its usual score in the template's
    `morale` and the exception here.

    The functions in `osrlib.core` never apply these, because deciding whether the situation holds
    is the referee's call. In a game session's battle, the [`osrlib.crawl`][osrlib.crawl] package
    uses an alternate whose `condition` mentions fire once the group has taken fire damage. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    score: int = Field(ge=2, le=12)
    """The morale score in this situation, from 2 to 12. A morale check is a 2d6 roll, and the creature holds on a
    result at or under the score.
    """

    condition: str = Field(min_length=1)
    """When it applies, in the SRD's words, like `"in melee"` or `"fear of fire"`."""


class AlignmentSpec(BaseModel):
    """Which alignments a monster may have, and which it usually has.

    Read it as [`MonsterTemplate.alignment`][osrlib.core.monsters.MonsterTemplate.alignment].
    [`spawn_monster`][osrlib.core.monsters.spawn_monster] gives each creature it spawns one
    alignment, because the wards that depend on alignment, like *protection from evil*, need one
    alignment to test against. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    options: tuple[Alignment, ...] = Field(min_length=1)
    """The alignments this creature may have, at least one. A troll is always chaotic. A creature the SRD prints
    as any alignment has all three here.
    """

    usual: Alignment | None = None
    """Which of the options the creature usually has, when the SRD gives one. `None` when it doesn't.

    A creature with several options and no usual one spawns with no alignment unless you pass one to
    [`spawn_monster`][osrlib.core.monsters.spawn_monster].
    """


class XpNote(BaseModel):
    """What a leader or other special member of a group of these creatures is worth in experience.

    Read them from [`MonsterTemplate.xp_notes`][osrlib.core.monsters.MonsterTemplate.xp_notes]. A band of
    gnolls has a leader worth more than the rest, and this is that number. What else makes the
    leader different is prose in the template's `abilities`, because the SRD gives it as prose.
    Frozen.
    """

    model_config = ConfigDict(frozen=True)

    role: str = Field(min_length=1)
    """The group member's role, like `"leader"`, `"chieftain"`, or `"bodyguard"`."""

    xp: int = Field(ge=0)
    """The experience for defeating one."""


class NumberAppearingValue(BaseModel):
    """How many of a creature appear, as either dice to roll or a flat number.

    Read it from [`NumberAppearing.dungeon`][osrlib.core.monsters.NumberAppearing.dungeon] or
    [`NumberAppearing.lair`][osrlib.core.monsters.NumberAppearing.lair]. Exactly one of the three
    fields applies. When `dice` is set, roll it with [`roll`][osrlib.core.dice.roll]. When `fixed`
    is set, use it. When `see_below` is True, read the number from the creature's description.

    Constructing one raises `pydantic.ValidationError` when `dice` is not a valid dice expression,
    or when the fields don't set exactly one of the three. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    dice: str | None = None
    """The dice to roll, like `"1d8"`. `None` when the count is fixed or given in prose."""

    fixed: int | None = None
    """A flat count. `None` when the count is rolled or given in prose."""

    see_below: bool = False
    """True when the SRD gives no number here and the creature's description gives it instead. `dice` and `fixed`
    are both `None` then.
    """

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
    """How many of a creature appear, which depends on where they are met.

    Read it as
    [`MonsterTemplate.number_appearing`][osrlib.core.monsters.MonsterTemplate.number_appearing], and
    roll the value that matches the encounter. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    dungeon: NumberAppearingValue
    """How many appear when they are met wandering in a dungeon."""

    lair: NumberAppearingValue
    """How many appear in their lair or in the wilderness, where they are usually found in larger numbers."""


class TreasureRef(BaseModel):
    """What treasure a monster has, as the stat block prints it.

    Read it as [`MonsterTemplate.treasure`][osrlib.core.monsters.MonsterTemplate.treasure], then
    pass it to [`plan_treasure_ref`][osrlib.core.treasure.plan_treasure_ref]. That function sorts
    the letters into lair, per-creature, and per-group treasure, and returns `extra_gp` and
    `multiplier` with them. Generate each letter in the plan with
    [`generate_treasure`][osrlib.core.treasure.generate_treasure]. Each letter is the `letter` of
    one table in [`load_treasure_tables`][osrlib.data.load_treasure_tables]`().treasure_types`, and
    [the treasure type index](https://mmacy.github.io/osrlib-python/reference/content-ids/treasure-types/) lists them.

    Use the plan rather than passing each of `letters` to `generate_treasure` yourself. A loop over
    `letters` drops the bracketed letters, the extra gold, and the multiplier, and it treats a
    per-creature letter as a lair hoard.

    A creature with no treasure has an empty reference. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    letters: tuple[str, ...] = ()
    """The treasure type letters, like `("D",)`.

    When there is more than one, each of them is generated.
    [`plan_treasure_ref`][osrlib.core.treasure.plan_treasure_ref] sorts them by section, so each
    letter goes to the lair, to each creature, or to the group, according to its table.
    """

    parenthetical: tuple[str, ...] = ()
    """The letters the SRD prints in brackets.

    [`plan_treasure_ref`][osrlib.core.treasure.plan_treasure_ref] adds them to the lair treasure,
    whatever section their tables are in, so a bandit's `U (A)` puts U on the group and A in the
    lair.
    """

    extra_gp: int = Field(default=0, ge=0)
    """Gold pieces added to the lair hoard on top of the rolled treasure, like a cloud giant's 5,000."""

    multiplier: int = Field(default=1, ge=1)
    """How many times to generate every listed letter, as with a noble's `V × 3`.

    1 unless the SRD prints a multiplier.
    """

    special: tuple[str, ...] = ()
    """Valuables that are not treasure types, like an elephant's tusks or a killer bee's honey.

    osrlib doesn't generate these, so show them to the referee.
    """

    see_below: bool = False
    """True when the creature's description, not its treasure line, gives what it has."""


class MonsterAbility(BaseModel):
    """One thing a monster can do, as a tag plus the SRD text it came from.

    Read them from [`MonsterTemplate.abilities`][osrlib.core.monsters.MonsterTemplate.abilities], or
    look one up by tag with [`MonsterTemplate.ability`][osrlib.core.monsters.MonsterTemplate.ability].
    The combat functions find an ability by its tag and read the numbers in `params`, so a monster's
    special powers are data rather than code. Frozen.

    An ability marked `manual` is text for a referee to read and act on, and osrlib doesn't use it.
    """

    model_config = ConfigDict(frozen=True)

    tag: str = Field(min_length=1)
    """The ability's identifier, a lowercase name like `"regeneration"`.

    The combat functions read four tags: `"regeneration"`, `"energy_drain"`, `"breath_weapon"`, and
    `"uses_fire"`. Battle also reads `"paralysis"`, `"poison"`, `"disease"`, and `"petrification"`
    when a monster's attack hits.
    """

    name: str = Field(min_length=1)
    """The ability's name as the SRD prints it, for display."""

    prose: str
    """The SRD's description, to show a player or referee."""

    manual: bool = False
    """True when osrlib doesn't act on this ability, so the referee applies it from the description."""

    params: dict[str, int | str | bool | tuple[int | str, ...]] = {}
    """The numbers and options the SRD gives for the ability, keyed by name.

    A troll's regeneration has `delay_rounds` 3 and `per_round` 3, and a wight's energy drain has
    `levels` 1. The keys differ by tag. Empty when the SRD gives no numbers.
    """


class AcAlternate(BaseModel):
    """An armour class a monster has only in particular circumstances.

    Read them from
    [`MonsterTemplate.ac_alternates`][osrlib.core.monsters.MonsterTemplate.ac_alternates]. The SRD
    prints more than one armour class for a creature that changes shape, or for a band whose members
    wear different armour. The template's `ac` is the usual value, and these are the others.
    Deciding when one applies is the referee's call, and osrlib never switches to one. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    ac: int
    """The armour class in the descending presentation, where lower is better."""

    ac_ascending: int
    """The same armour class in the ascending presentation, where higher is better."""

    condition: str = ""
    """When this value applies, in the SRD's words, like `"in human form"`.

    Empty when the SRD prints the alternative without a condition, as it does for a band whose
    members wear different armour.
    """


class MonsterTemplate(BaseModel):
    """A monster's stat block: everything true of every creature of that kind.

    Get one from [`load_monsters`][osrlib.data.load_monsters]`().get(monster_id)`.
    [The monster id index](https://mmacy.github.io/osrlib-python/reference/content-ids/monsters/) lists the ids. Then
    call [`spawn_monster`][osrlib.core.monsters.spawn_monster] to put a creature into play.
    To write a monster of your own, construct one, and pass it to `spawn_monster` the same way.

    A template is frozen, and play never changes it. Every creature of its kind shares it. The hit
    points one creature has left, the wounds it has taken, and the conditions on it are on the
    [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] spawned from it, so a wounded troll
    doesn't weaken every other troll in the dungeon.

    Constructing one raises `pydantic.ValidationError` when `attack_roll_required` is True and
    either armour class is `None`, or when it is False and either armour class is set.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """The monster id, like `"troll"`.

    Pass it to [`MonsterCatalog.get`][osrlib.core.monsters.MonsterCatalog.get] to look the template up.
    """

    name: str
    """The creature's name as the SRD prints it, for display."""

    page: str
    """The file name of the SRD page this template was compiled from, like `"Hydra.md"`.

    Templates with the same page are variants of one creature, like the sizes of hydra or the
    colours of dragon.
    """

    intro: str = ""
    """The SRD's description of the creature, for a referee or a narrator to read out."""

    ac: int | None = None
    """Armour class in the descending presentation, where lower is better.

    `None` for a creature that every attack hits without a roll, when `attack_roll_required` is False.
    """

    ac_ascending: int | None = None
    """The same armour class in the ascending presentation, where higher is better. `None` when `ac` is."""

    ac_alternates: tuple[AcAlternate, ...] = ()
    """Armour classes that apply only in particular circumstances. See
    [`AcAlternate`][osrlib.core.monsters.AcAlternate].
    """

    attack_roll_required: bool = True
    """False for a creature that every attack hits without a roll, like a green slime. Both armour class fields are
    `None` then.
    """

    hit_dice: MonsterHitDice
    """The dice its hit points are rolled on. See [`MonsterHitDice`][osrlib.core.monsters.MonsterHitDice]."""

    attacks: tuple[AttackRoutine, ...] = ()
    """The sets of attacks it can make, one set per round. See [`AttackRoutine`][osrlib.core.monsters.AttackRoutine].
    """

    thac0: int = Field(ge=2, le=20)
    """Its THAC0 ("to hit armour class 0"): the d20 roll it needs to hit armour class 0 under descending armour class.

    To read the value for a creature that has been drained of Hit Dice, use
    [`MonsterInstance.thac0`][osrlib.core.monsters.MonsterInstance.thac0].
    """

    attack_bonus: int = Field(ge=-1)
    """The bonus it adds to a d20 attack roll under ascending armour class. It is the same attack skill as `thac0`."""

    movement: tuple[MovementMode, ...] = Field(min_length=1)
    """How it gets around and how fast, ordinary movement first. See
    [`MovementMode`][osrlib.core.monsters.MovementMode].
    """

    saves: MonsterSaves
    """Its saving throws. See [`MonsterSaves`][osrlib.core.monsters.MonsterSaves]."""

    morale: int | None = Field(default=None, ge=2, le=12)
    """How willing it is to keep fighting, from 2 to 12. A morale check is a 2d6 roll, and the creature holds on a
    result at or under the score. Pass it to [`check_morale`][osrlib.core.combat.check_morale]. `None` when the stat
    block prints no morale score.
    """

    morale_alternates: tuple[MoraleAlternate, ...] = ()
    """Morale scores that apply only in particular circumstances. See
    [`MoraleAlternate`][osrlib.core.monsters.MoraleAlternate].
    """

    alignment: AlignmentSpec
    """Which alignments it may have. See [`AlignmentSpec`][osrlib.core.monsters.AlignmentSpec]."""

    xp: int = Field(ge=0)
    """The experience for defeating one, as the SRD prints it."""

    xp_notes: tuple[XpNote, ...] = ()
    """What a leader or other special member of a group is worth instead of `xp`. See
    [`XpNote`][osrlib.core.monsters.XpNote].
    """

    number_appearing: NumberAppearing
    """How many appear, which depends on where they are met. See
    [`NumberAppearing`][osrlib.core.monsters.NumberAppearing].
    """

    treasure: TreasureRef = TreasureRef()
    """What treasure they have. See [`TreasureRef`][osrlib.core.monsters.TreasureRef]."""

    abilities: tuple[MonsterAbility, ...] = ()
    """What it can do beyond its attacks. See [`MonsterAbility`][osrlib.core.monsters.MonsterAbility]."""

    defenses: Defenses = Defenses()
    """What it resists, in the form the damage rules check. See [`Defenses`][osrlib.core.monsters.Defenses]."""

    categories: tuple[str, ...] = ()
    """What kind of creature it is, as tags like `"undead"`, `"person"`, and `"enchanted"`.

    Spells and turning check these. *Hold person* affects a monster only when it has `"person"`, and
    turning affects only `"undead"`.
    """

    overrides_applied: tuple[str, ...] = ()
    """The field paths, like `"categories"`, that a correction changed when this template was compiled from the SRD.

    Empty when the SRD page compiled without corrections. Read it when you check osrlib's data
    against the SRD. osrlib doesn't read it during play.
    """

    @model_validator(mode="after")
    def _ac_present_when_rolled_against(self) -> MonsterTemplate:
        has_ac = self.ac is not None and self.ac_ascending is not None
        if self.attack_roll_required and not has_ac:
            raise ValueError(f"{self.id} requires attack rolls but has no armour class")
        if not self.attack_roll_required and (self.ac is not None or self.ac_ascending is not None):
            raise ValueError(f"{self.id} needs no hit roll and must not carry an armour class")
        return self

    def ability(self, tag: str) -> MonsterAbility | None:
        """Return this monster's ability with `tag`, or `None` when it has none.

        Use it to find out whether a creature has a power and to read the numbers behind it in one
        step: the troll's regeneration rate, the number of levels a wight's touch drains, the
        `shape` and `element` of a dragon's breath.

        Args:
            tag: The ability tag to look for, like `"regeneration"` or `"energy_drain"`.

        Returns:
            The first ability in `abilities` with that tag, or `None`. Read its `params` for the
            numbers.

        Examples:
            ```python
            from osrlib.data import load_monsters

            wight = load_monsters().get("wight")
            drain = wight.ability("energy_drain")
            print(drain.params["levels"], drain.params["xp_policy"])
            # 1 halfway
            print(wight.ability("breath_weapon"))
            # None
            ```
        """
        for ability in self.abilities:
            if ability.tag == tag:
                return ability
        return None


class MonsterCatalog(BaseModel):
    """A set of monster templates with lookup by id.

    [`load_monsters`][osrlib.data.load_monsters] returns the built-in catalog. It validates the data
    on the first call and returns the same object on every later call. Look a monster up by id with
    [`get`][osrlib.core.monsters.MonsterCatalog.get], or iterate `monsters` to filter by Hit Dice,
    category, or anything else you need for an encounter table.

    Constructing one raises `pydantic.ValidationError` when two templates have the same id. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    monsters: tuple[MonsterTemplate, ...]
    """The templates, each with a different id. The built-in catalog is in id order."""

    @model_validator(mode="after")
    def _ids_must_be_unique(self) -> MonsterCatalog:
        ids = [template.id for template in self.monsters]
        if len(set(ids)) != len(ids):
            raise ValueError("monster ids must be unique")
        return self

    def get(self, monster_id: str) -> MonsterTemplate:
        """Return the monster with `monster_id`.

        Args:
            monster_id: The id to look up, like `"troll"` or `"red_dragon"`. Each template's `id` in
                `monsters` is one, and [the monster id
                index](https://mmacy.github.io/osrlib-python/reference/content-ids/monsters/) lists the built-in ids.

        Returns:
            The monster template. Spawn a creature from it with
            [`spawn_monster`][osrlib.core.monsters.spawn_monster].

        Raises:
            ValueError: If no monster has that id. The message includes the id you passed. Check
                the spelling against the monster id index.

        Examples:
            ```python
            from osrlib.data import load_monsters

            catalog = load_monsters()
            troll = catalog.get("troll")
            print(troll.name, troll.hit_dice.count, troll.xp)
            # Troll 6 650
            print([template.id for template in catalog.monsters if template.page == "Hydra.md"])
            # ['hydra_10', 'hydra_11', 'hydra_12', 'hydra_5', 'hydra_6', 'hydra_7', 'hydra_8', 'hydra_9']
            ```
        """
        for template in self.monsters:
            if template.id == monster_id:
                return template
        raise ValueError(f"unknown monster id {monster_id!r}")


class MonsterInstance(BaseModel):
    """One creature in play, spawned from a frozen [`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate].

    Get one from [`spawn_monster`][osrlib.core.monsters.spawn_monster]. An instance is what takes
    damage, gains conditions, and dies. The template it came from never changes, and what happens to
    one instance doesn't affect the other creatures spawned from the same template.

    An instance has the same rules attributes as a [`Character`][osrlib.core.character.Character]:
    THAC0, attack bonus, both armour classes, saving throws, conditions, and stat modifiers. The
    functions in [`osrlib.core.combat`][osrlib.core.combat] and
    [`osrlib.core.effects`][osrlib.core.effects] therefore accept either one, through the protocols
    in [`osrlib.core.creature`][osrlib.core.creature].

    The instance doesn't copy what the template defines. Read that through `template`. For the
    values a drain changes, read the instance's properties, like
    [`thac0`][osrlib.core.monsters.MonsterInstance.thac0] and
    [`saves`][osrlib.core.monsters.MonsterInstance.saves], rather than the template's fields.

    An instance isn't frozen. Each assignment to a field is validated, and a value that breaks a
    field's constraint, like a negative `current_hp`, raises `pydantic.ValidationError`.
    """

    model_config = ConfigDict(validate_assignment=True)

    id: str
    """The entity id, usually from [`IdAllocator.allocate`][osrlib.core.monsters.IdAllocator.allocate]. Events refer
    to the creature by it.
    """

    template: MonsterTemplate
    """The stat block this creature was spawned from. Read anything the creature shares with its kind from here."""

    max_hp: int = Field(ge=1)
    """The hit points it was spawned with, 1 or more."""

    current_hp: int = Field(ge=0)
    """The hit points it has left.

    [`deal_damage`][osrlib.core.combat.deal_damage] stops it at 0 and applies the `dead` condition
    through [`kill`][osrlib.core.effects.kill]. Healing stops it at `max_hp`.
    """

    conditions: tuple[ActiveCondition, ...] = ()
    """The conditions on it. Apply and clear them through [`osrlib.core.effects`][osrlib.core.effects]."""

    stat_modifiers: tuple[ActiveModifier, ...] = ()
    """Timed bonuses and penalties on it, from spells and effects."""

    alignment: Alignment | None = None
    """The alignment this creature has, set when it was spawned.

    `None` when the template offered several alignments with no usual one, and you passed none to
    [`spawn_monster`][osrlib.core.monsters.spawn_monster]. A ward that depends on alignment treats
    such a creature as being of a different alignment, so the ward protects the party.
    """

    nonregen_damage: int = Field(default=0, ge=0)
    """Damage a regenerating creature can never heal, up to `max_hp`.

    Damage of an element in the `blocked_by` param of the template's `regeneration` ability counts
    here, which is fire and acid for a troll. A troll stays dead only once this damage alone reaches
    its maximum hit points.
    """

    last_damaged_round: int | None = None
    """The combat round in which it was last hurt, from which the regeneration delay is counted. `None` before anything
    has hurt it.
    """

    breath_uses_today: int = Field(default=0, ge=0)
    """How many times it has used its breath weapon today.

    [`resolve_breath`][osrlib.core.combat.resolve_breath] adds 1 each time when the breath has a
    daily limit. The limit is the `uses_per_day` param of the template's `breath_weapon` ability,
    which is 3 for a dragon. A breath weapon with no `uses_per_day` has no daily limit.
    """

    drained_hd: int = Field(default=0, ge=0)
    """How many Hit Dice have been drained from it, which [`drain_monster_hd`][osrlib.core.combat.drain_monster_hd]
    adds to. Its THAC0, attack bonus, and saving throws follow the Hit Dice it has left.
    """

    @property
    def name(self) -> str:
        """The creature's name, from its template. Use it wherever you show the creature to a player."""
        return self.template.name

    @property
    def hit_dice_count(self) -> int:
        """How many Hit Dice this creature still has: the template's count less `drained_hd`, never below 0.

        Its THAC0, attack bonus, and saving throws all follow from this rather than from the
        template, so draining a creature weakens it in every way at once.
        """
        return max(0, self.template.hit_dice.count - self.drained_hd)

    @property
    def thac0(self) -> int:
        """The d20 roll this creature needs to hit armour class 0 under descending armour class.

        For a creature that hasn't been drained, it's the template's printed value. That value
        already includes the rule that a creature with a positive Hit Dice modifier attacks as
        though it had one more Hit Die. For a drained creature, the value is looked up again for
        the Hit Dice it has left, with the same rule.
        """
        if self.drained_hd == 0:
            return self.template.thac0
        from osrlib.core.tables import thac0_for_hd

        return thac0_for_hd(self.hit_dice_count, bonus_modifier=self.template.hit_dice.modifier > 0)[0]

    @property
    def attack_bonus(self) -> int:
        """The bonus this creature adds to an attack roll under ascending armour class.

        It is the ascending form of [`thac0`][osrlib.core.monsters.MonsterInstance.thac0], and a
        drain changes it the same way.
        """
        if self.drained_hd == 0:
            return self.template.attack_bonus
        from osrlib.core.tables import thac0_for_hd

        return thac0_for_hd(self.hit_dice_count, bonus_modifier=self.template.hit_dice.modifier > 0)[1]

    @property
    def armour_class(self) -> int | None:
        """The creature's armour class, where lower is better. `None` for a creature no attack roll is made against."""
        return self.template.ac

    @property
    def armour_class_ascending(self) -> int | None:
        """The creature's armour class, where higher is better. `None` for a creature no attack roll is made against."""
        return self.template.ac_ascending

    @property
    def saves(self) -> SavingThrows:
        """The five saving throw targets for this creature.

        For a creature that hasn't been drained, these are the template's printed values. A drained
        creature saves as a monster with the Hit Dice it has left, from the monster saving throw
        table.
        """
        if self.drained_hd == 0:
            return self.template.saves.values
        from osrlib.core.tables import monster_save_band_label
        from osrlib.data import load_combat_tables

        dice = self.template.hit_dice.model_copy(update={"count": self.hit_dice_count})
        return load_combat_tables().save_band(monster_save_band_label(dice)).saves

    @property
    def melee_modifier(self) -> int:
        """Always 0, because monsters have no STR score and the SRD gives them no melee bonus in its place.

        The combat functions read this property on a monster the same way they read it on a
        [`Character`][osrlib.core.character.Character].
        """
        return 0

    @property
    def missile_modifier(self) -> int:
        """Always 0, because monsters have no DEX score and the SRD gives them no missile bonus in its place."""
        return 0

    @property
    def initiative_modifier(self) -> int:
        """Always 0, because the SRD gives a monster no initiative modifier.

        To give a monster one, pass it as `monster_modifier` to
        [`participant_modifier`][osrlib.core.combat.participant_modifier].
        """
        return 0


def spawn_monster(
    template: MonsterTemplate, *, id: str, stream: RngStream, alignment: Alignment | None = None
) -> MonsterInstance:
    """Put one creature into play: roll its hit points and give it an entity id.

    Call it once for each creature in an encounter. You can spawn from the
    same template as often as you like. Each creature gets separate hit points, wounds, and
    conditions, and what happens to one doesn't affect the template or the other creatures spawned
    from it.

    Pass the creature to the functions in [`osrlib.core.combat`][osrlib.core.combat] to fight it.
    For its treasure, pass the template's `treasure` to
    [`plan_treasure_ref`][osrlib.core.treasure.plan_treasure_ref]. In a game session,
    [`GameSession.spawn`][osrlib.crawl.session.GameSession.spawn] calls this for you with the
    session's allocator and stream.

    Hit points are one roll of each of the template's Hit Dice plus its modifier, and never less
    than 1. A creature whose hit points the SRD fixes, like a normal bat's 1 or a hydra's 8 per
    head, gets that number, and nothing is rolled.

    The creature gets one alignment here, because a ward like *protection from evil* needs one
    alignment to test against. The alignment is the one you pass, or else the template's usual
    alignment, or else the template's only option. When the template offers several alignments
    with no usual one and you pass none, the creature's `alignment` is `None`. Such a ward treats
    it as being of a different alignment, so the ward protects the party.

    Args:
        template: The stat block to spawn from, from
            [`load_monsters`][osrlib.data.load_monsters]`().get(monster_id)`.
            [The monster id index](https://mmacy.github.io/osrlib-python/reference/content-ids/monsters/) lists the ids.
        id: The entity id to give it, usually `allocator.allocate("monster")` from an
            [`IdAllocator`][osrlib.core.monsters.IdAllocator]. Use a different id for each
            creature, because events refer to creatures by id.
        stream: The stream to roll hit points on, conventionally
            `streams.get(`[`MONSTER_SPAWN_STREAM`][osrlib.core.monsters.MONSTER_SPAWN_STREAM]`)`.
            It takes one draw per Hit Die, and none for a creature with fixed hit points.
        alignment: The alignment to give this creature. It must be one of the template's
            `alignment.options`. `None` uses the template's usual or only alignment.

    Returns:
        The creature, at full hit points, with no conditions and no wounds.

    Raises:
        ValueError: If `alignment` is not one of the template's options. Nothing is rolled before
            the error. Pass one of `template.alignment.options`, or `None`.

    Examples:
        ```python
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        allocator = IdAllocator()
        stream = RngStreams(master_seed=3).get(MONSTER_SPAWN_STREAM)
        template = load_monsters().get("troll")
        first = spawn_monster(template, id=allocator.allocate("monster"), stream=stream)
        second = spawn_monster(template, id=allocator.allocate("monster"), stream=stream)
        print(first.id, first.max_hp, second.id, second.max_hp)
        # monster-0001 42 monster-0002 22

        first.current_hp -= 10
        print(first.current_hp, second.current_hp)
        # 32 22
        ```
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
    """Hands out entity ids, each one unique within a game.

    Every creature, effect, and valuable in a game needs an id, and an allocator gives it one.
    A [`GameSession`][osrlib.crawl.session.GameSession] has one allocator and uses it everywhere it
    needs an id, so inside a session, pass the session's `allocator` rather than a new one. Outside
    a session, construct one and pass the same one to every call that takes an allocator.

    Ids count up per prefix and never repeat, so a save and its replay refer to the same creature by
    the same id. The counters are a plain field, so you can save and load an allocator with
    `model_dump` and `model_validate`, and it continues the count.

    Examples:
        ```python
        from osrlib.core.monsters import IdAllocator

        allocator = IdAllocator()
        print(allocator.allocate("monster"), allocator.allocate("monster"))
        # monster-0001 monster-0002
        print(allocator.allocate("effect"))
        # effect-0001
        ```
    """

    model_config = ConfigDict(validate_assignment=True)

    counters: dict[str, int] = {}
    """How many ids have been handed out under each prefix, keyed by prefix.

    [`allocate`][osrlib.core.monsters.IdAllocator.allocate] updates it. Don't set it yourself, or
    the allocator can hand out an id it has already given.
    """

    def allocate(self, prefix: str) -> str:
        """Return the next unused id under `prefix`.

        Each prefix counts independently and never repeats, so calling it twice with the same
        prefix gives two different ids. It mutates the allocator, so an id is spent once
        it is returned.

        Args:
            prefix: What kind of thing the id is for, like `"monster"`, `"effect"`, `"npc"`,
                or `"valuable"`.

        Returns:
            The id, which is the prefix, a hyphen, and a number padded to at least four digits,
            counting from `0001`.

        Examples:
            ```python
            from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, spawn_monster
            from osrlib.core.rng import RngStreams
            from osrlib.data import load_monsters

            allocator = IdAllocator()
            stream = RngStreams(master_seed=3).get(MONSTER_SPAWN_STREAM)
            goblin = load_monsters().get("goblin")
            goblins = [spawn_monster(goblin, id=allocator.allocate("monster"), stream=stream) for _ in range(3)]
            print([goblin.id for goblin in goblins])
            # ['monster-0001', 'monster-0002', 'monster-0003']

            # The counters are plain data, so a saved allocator resumes the count.
            restored = IdAllocator.model_validate(allocator.model_dump())
            print(restored.allocate("monster"))
            # monster-0004
            ```
        """
        n = self.counters.get(prefix, 0) + 1
        self.counters[prefix] = n
        return f"{prefix}-{n:04d}"
