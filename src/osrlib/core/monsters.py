"""Monster templates, the creatures spawned from them, and the ids they are given.

Two models do the work here, and they do different jobs.
[`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate] is the stat block, frozen and shared: one
troll template describes every troll in the game.
[`MonsterInstance`][osrlib.core.monsters.MonsterInstance] is one creature in play, mutable, with its
own hit points and its own wounds. Load the catalog once with
[`load_monsters`][osrlib.data.load_monsters], then call
[`spawn_monster`][osrlib.core.monsters.spawn_monster] for each creature that enters a fight. Nothing
that happens to a creature then reaches the template or the other creatures spawned from it.

The instances you spawn are combatants. Hand them to
[`osrlib.core.combat`][osrlib.core.combat] to fight, which takes a
[`Character`][osrlib.core.character.Character] or a monster instance as attacker or target, and to
[`osrlib.core.effects`][osrlib.core.effects] for conditions and timed modifiers. Their treasure
comes from [`osrlib.core.treasure`][osrlib.core.treasure] using the letters in
[`TreasureRef`][osrlib.core.monsters.TreasureRef], and the experience they are worth is on the
template.

What a monster can do beyond hitting things is on the template as
[`MonsterAbility`][osrlib.core.monsters.MonsterAbility] records, each a tag the rules match on plus
the SRD's own text. The tags osrlib acts on are `regeneration`, `energy_drain`, `poison`,
`paralysis`, `petrification`, `breath_weapon`, `gaze`, `disease`, and `uses_fire`, and each one
includes the numbers the procedures need. Every other ability is marked manual and is text for a
referee to read. What a monster resists is separate, in
[`Defenses`][osrlib.core.monsters.Defenses], which the damage rules check every time a hit lands.

Each monster page in the SRD becomes one template per creature it describes, so a page that prints
several sizes of hydra becomes one entry per size. Each template has to be spawnable by itself, and
a template covering several creatures at once would not be.

Hit points are rolled from the
[`MONSTER_SPAWN_STREAM`][osrlib.core.monsters.MONSTER_SPAWN_STREAM] stream, kept apart from combat
so that a change to the combat rules never alters the creatures a seeded scenario spawns.
[`IdAllocator`][osrlib.core.monsters.IdAllocator], also here, hands out the entity ids those
creatures are known by.

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
    "TreasureRef",
    "XpNote",
    "spawn_monster",
]

MONSTER_SPAWN_STREAM = StreamName.MONSTER_SPAWN
"""The stream key every session uses for rolling a spawned monster's hit points.

A stream key names one independent random-number sequence inside an
[`RngStreams`][osrlib.core.rng.RngStreams] set. Pass `streams.get(MONSTER_SPAWN_STREAM)` as the
`stream` argument of [`spawn_monster`][osrlib.core.monsters.spawn_monster].

It is separate from the combat stream so that a change to how a fight resolves never alters the
creatures a seeded scenario puts in front of the players.
"""


class DamageKey(StrEnum):
    """What a source of damage can be, for the purposes of a monster's defenses.

    A monster that can be hurt only by certain kinds of attack names them here in
    [`Defenses.harmed_only_by`][osrlib.core.monsters.Defenses], and a monster that takes reduced
    damage from a kind names it in [`DamageReduction`][osrlib.core.monsters.DamageReduction]. The
    damage rules check the keys on the attack against both.

    `holy` is the one that behaves unlike the rest. Holy water has it, and it gets through any
    gate when the target is undead, because the SRD says outright that holy water harms undead. The
    wight's silver-or-magic gate would otherwise absorb the one weapon made for killing wights.
    """

    SILVER = "silver"
    """A silver weapon, which is what gets through the lesser undead."""

    MAGIC = "magic"
    """An enchanted weapon or a spell."""

    FIRE = "fire"
    """Fire, whether from a torch, a flask of oil, or a spell."""

    COLD = "cold"
    """Cold, from a creature's attack or a spell."""

    HOLY = "holy"
    """Holy water, which harms undead whatever else they resist."""


class Element(StrEnum):
    """The kinds of energy a monster's breath, attack, or defense can be made of.

    A dragon's breath names one of these, and so does a creature's immunity in
    [`EnergyDefense`][osrlib.core.monsters.EnergyDefense]. The damage rules match the two against
    each other to decide whether an attack lands at all.
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
    """Poison delivered by an attack rather than as a cloud."""

    STEAM = "steam"
    """Scalding steam, as a dragon turtle breathes."""


class EnergyDefense(BaseModel):
    """How a monster resists one kind of energy.

    Read them from [`Defenses.energy`][osrlib.core.monsters.Defenses], keyed by
    [`Element`][osrlib.core.monsters.Element]. The damage rules check the entry for the element of
    an incoming attack before rolling any damage. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    immunity: Literal["all", "nonmagical"]
    """`"all"` when nothing of this element can hurt the creature, magical or not, as with a fire giant and fire.
    `"nonmagical"` when ordinary sources cannot but magic can: a red dragon shrugs off its own breath and a flask of
    burning oil, and still takes damage from a fire ball.
    """

    auto_save_magical: bool = False
    """True when the creature passes any saving throw against a magical form of this element without rolling, which is
    the dragons' automatic save against attacks like their own breath.
    """


class DamageReduction(BaseModel):
    """A cut taken out of the damage a monster suffers, applied after the dice are rolled.

    Read them from [`Defenses.reductions`][osrlib.core.monsters.Defenses]. The damage is divided
    and rounded down, and a hit that gets through always does at least 1 point. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    keys: tuple[DamageKey, ...] = ()
    """Which sources of damage are reduced. Empty means all of them that got past the monster's gate, which is the mummy
    taking half from everything. Naming keys narrows it, as with the wraith, which takes half from silver weapons alone.
    """

    divisor: int = Field(default=2, ge=2)
    """What the damage is divided by. 2 is halving, which is what the SRD prints."""


class Defenses(BaseModel):
    """What a monster resists, in the form the damage rules check.

    Read it as [`MonsterTemplate.defenses`][osrlib.core.monsters.MonsterTemplate]. The combat
    procedures consult it every time a hit lands, before any damage is rolled. The SRD's own
    wording for the same defenses stays on the template's abilities, for a front end to show.
    Frozen.
    """

    model_config = ConfigDict(frozen=True)

    harmed_only_by: tuple[DamageKey, ...] = ()
    """The kinds of damage that can hurt this creature at all. An attack with none of them is absorbed and no damage is
    rolled, which is how a wight ignores an ordinary sword. Empty means anything hurts it.
    """

    reductions: tuple[DamageReduction, ...] = ()
    """Cuts taken out of the damage that does get through; see
    [`DamageReduction`][osrlib.core.monsters.DamageReduction].
    """

    energy: dict[Element, EnergyDefense] = {}
    """How the creature resists each kind of energy; see [`EnergyDefense`][osrlib.core.monsters.EnergyDefense]."""

    condition_immunities: tuple[Condition, ...] = ()
    """Conditions the creature can never be put into, like the undead being beyond poison, charm, and sleep. Attempts to
    apply one are dropped.
    """


class MonsterHitDice(BaseModel):
    """A monster's Hit Dice, as the stat block prints them.

    Read it as [`MonsterTemplate.hit_dice`][osrlib.core.monsters.MonsterTemplate].
    [`spawn_monster`][osrlib.core.monsters.spawn_monster] rolls hit points from it, and
    [`osrlib.core.tables`][osrlib.core.tables] uses it to place the monster on the attack matrix
    and in an experience band. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    count: int = Field(default=0, ge=0)
    """How many dice are rolled. 0 for a creature whose hit points are fixed."""

    die: int = 8
    """The size of each die, which is 8 for almost everything. A creature with half a Hit Die rolls 1d4 instead."""

    modifier: int = 0
    """Hit points added to or taken from the total, and it may be negative: the SRD's `1-1` is a modifier of −1. A
    positive modifier also makes the creature attack as though it had one more Hit Die, and a negative one lowers the
    experience band.
    """

    asterisks: int = Field(default=0, ge=0)
    """How many special abilities the SRD credits the creature with, which is what raises its experience award. Not
    decoration.
    """

    average_hp: int | None = None
    """The average hit points the SRD prints for the creature, for a referee who would rather not roll. Spawning ignores
    it and rolls.
    """

    fixed_hp: int | None = None
    """Hit points that are not rolled at all, like the creature with exactly 1 hit point or a hydra with 8 per head.
    `None` when the dice decide.
    """

    @model_validator(mode="after")
    def _rollable_or_fixed(self) -> MonsterHitDice:
        if self.die not in (4, 8):
            raise ValueError(f"monster hit die must be d8, or d4 for ½ HD, got d{self.die}")
        if self.count == 0 and self.fixed_hp is None:
            raise ValueError("a monster needs hit dice to roll or fixed hit points")
        return self


class MonsterAttack(BaseModel):
    """One attack a monster makes: what it is called, how often, how much it hurts, and what else it does.

    Read them from an [`AttackRoutine`][osrlib.core.monsters.AttackRoutine]. A troll's routine
    contains two talon attacks and a bite, so its routine has two of these, one with a count of 2.
    Frozen.
    """

    model_config = ConfigDict(frozen=True)

    count: int = Field(default=1, ge=1)
    """How many of this attack the monster makes in a round."""

    name: str = Field(min_length=1)
    """What the attack is, as the SRD names it: `"talon"`, `"bite"`, `"weapon"`."""

    damage: str | None = None
    """The damage as a dice expression, like `"1d6"`, which [`roll`][osrlib.core.dice.roll] evaluates. `None` when the
    damage is fixed instead.
    """

    fixed_damage: int | None = None
    """Damage that is a flat number rather than a roll. `None` when `damage` says it."""

    fixed_damage_options: tuple[int, ...] = ()
    """The alternatives the SRD prints when the damage depends on something it leaves to the referee, like an insect
    swarm doing 2 or 4 depending on the target's armour. Choosing between them is the referee's call.
    """

    by_weapon: bool = False
    """True when the monster attacks with whatever weapon it carries, so `damage` is what the SRD prints as typical
    rather than a fixed property of the creature.
    """

    by_weapon_modifier: int = 0
    """The bonus or penalty the SRD prints alongside a by-weapon attack."""

    effects: tuple[str, ...] = ()
    """What a hit does beyond damage, as tags like `"poison"`, `"paralysis"`, or `"energy_drain"`. Look the matching
    [`MonsterAbility`][osrlib.core.monsters.MonsterAbility] up on the template for the numbers behind each.
    """

    @field_validator("damage")
    @classmethod
    def _damage_must_parse(cls, value: str | None) -> str | None:
        if value is not None:
            parse(value)
        return value


class AttackRoutine(BaseModel):
    """One set of attacks a monster can make in a round, chosen as a whole.

    Read them from [`MonsterTemplate.attacks`][osrlib.core.monsters.MonsterTemplate]. Most
    creatures have one. A creature with more than one is choosing between them, not doing both: a
    dragon either claws and bites or breathes, and the referee or your game decides which in a
    given round. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    attacks: tuple[MonsterAttack, ...] = Field(min_length=1)
    """The attacks the routine makes, at least one; see [`MonsterAttack`][osrlib.core.monsters.MonsterAttack]."""


class MovementMode(BaseModel):
    """One way a monster gets around, and how fast.

    Read them from [`MonsterTemplate.movement`][osrlib.core.monsters.MonsterTemplate]. The first
    is always the creature's ordinary movement. Later ones are its other ways of moving, and you
    pick the one the situation calls for. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    rate_feet: int = Field(ge=0)
    """Feet covered in one exploration turn, which is the rate used while mapping and searching."""

    encounter_rate_feet: int = Field(ge=0)
    """Feet covered in one combat round, which is a third of the exploration rate."""

    descriptor: str | None = None
    """How the creature is moving, in the SRD's own word: `"flying"`, `"swimming"`, `"in webs"`. `None` for walking,
    which needs no word.
    """


class MonsterSaves(BaseModel):
    """A monster's saving throws, and the stat block's note about where they come from.

    Read it as [`MonsterTemplate.saves`][osrlib.core.monsters.MonsterTemplate], or read
    [`MonsterInstance.saves`][osrlib.core.monsters.MonsterInstance] to get the values already
    adjusted for a creature that has been drained of Hit Dice. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    values: SavingThrows
    """The five targets; see [`SavingThrows`][osrlib.core.classes.SavingThrows]."""

    save_as: str
    """What the stat block says the creature saves as, like `"2"` for a second-level fighter or `"Cleric 1"`. Keep it
    for display and for checking the values against the SRD's bands. The rules read `values`.
    """


class MoraleAlternate(BaseModel):
    """A morale score that applies only in a particular situation.

    Read them from [`MonsterTemplate.morale_alternates`][osrlib.core.monsters.MonsterTemplate]. A
    creature that fights bravely except when fire is involved has its ordinary score on the
    template and the exception here. Deciding whether the condition holds is the referee's call, so
    nothing applies these for you. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    score: int = Field(ge=2, le=12)
    """The morale score in this situation, from 2 to 12. A morale check rolls 2d6 and the creature holds on a result at
    or under it.
    """

    condition: str = Field(min_length=1)
    """When it applies, in the SRD's own words, like `"in melee"` or `"fear of fire"`."""


class AlignmentSpec(BaseModel):
    """Which alignments a monster may have, and which it usually has.

    Read it as [`MonsterTemplate.alignment`][osrlib.core.monsters.MonsterTemplate].
    [`spawn_monster`][osrlib.core.monsters.spawn_monster] settles on one alignment for each
    creature it spawns, because the wards that turn on alignment need a single answer. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    options: tuple[Alignment, ...] = Field(min_length=1)
    """The alignments this creature may be, at least one. A troll is chaotic and nothing else. A creature the SRD prints
    as any alignment has all three here.
    """

    usual: Alignment | None = None
    """Which of the options the creature usually is, when the SRD says so. `None` when it does not, and a creature with
    several options and no usual one spawns unresolved unless you name its alignment yourself.
    """


class XpNote(BaseModel):
    """What a leader among a group of these creatures is worth in experience.

    Read them from [`MonsterTemplate.xp_notes`][osrlib.core.monsters.MonsterTemplate]. A band of
    gnolls has a leader worth more than the rest, and this is that number. What else makes the
    leader different stays in the template's abilities as prose, because the SRD gives it as prose.
    Frozen.
    """

    model_config = ConfigDict(frozen=True)

    role: str = Field(min_length=1)
    """What the variant is called, like `"leader"`, `"chieftain"`, or `"bodyguard"`."""

    xp: int = Field(ge=0)
    """The experience for defeating one."""


class NumberAppearingValue(BaseModel):
    """How many of a creature turn up, as either dice to roll or a flat number.

    Read them off [`NumberAppearing`][osrlib.core.monsters.NumberAppearing]. Roll `dice` with
    [`roll`][osrlib.core.dice.roll] when it is set, take `fixed` when that is, and fall back to the
    creature's own description when `see_below` is. Exactly one of the three applies. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    dice: str | None = None
    """The dice to roll, like `"1d8"`. `None` when the count is fixed or described in prose."""

    fixed: int | None = None
    """A flat count. `None` when dice or prose decide."""

    see_below: bool = False
    """True when the SRD gives no number here and the creature's description says how many appear. `dice` and `fixed`
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
    [`MonsterTemplate.number_appearing`][osrlib.core.monsters.MonsterTemplate] and roll the value
    that matches the encounter. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    dungeon: NumberAppearingValue
    """How many are met wandering in a dungeon, which is the smaller group."""

    lair: NumberAppearingValue
    """How many are met in their lair or in the wilderness, where a creature is found in its full numbers."""


class TreasureRef(BaseModel):
    """What treasure a monster has, as the stat block prints it.

    Read it as [`MonsterTemplate.treasure`][osrlib.core.monsters.MonsterTemplate], then pass it to
    [`plan_treasure_ref`][osrlib.core.treasure.plan_treasure_ref], which sorts the letters into
    lair, per-creature, and per-group treasure and carries `parenthetical`, `extra_gp`, and
    `multiplier` through. Generate each letter in the plan with
    [`generate_treasure`][osrlib.core.treasure.generate_treasure]; see
    [the treasure type index][treasure-types-index] for the letters.

    Go through the plan rather than looping `letters` into
    [`generate_treasure`][osrlib.core.treasure.generate_treasure] yourself. A loop over `letters`
    alone drops the bracketed letters, the flat gold, and the multiplier without telling you, and
    it treats a per-creature letter as though it were a lair hoard.

    A creature with no treasure has an empty reference. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    letters: tuple[str, ...] = ()
    """The treasure type letters, like `("D",)`.

    More than one means every one of them is generated.
    [`plan_treasure_ref`][osrlib.core.treasure.plan_treasure_ref] sorts them by section, so a lair
    letter, a per-creature letter, and a per-group letter in the same reference each land in the
    right place.
    """

    parenthetical: tuple[str, ...] = ()
    """The letters the SRD prints in brackets.

    [`plan_treasure_ref`][osrlib.core.treasure.plan_treasure_ref] adds them to the lair treasure
    whatever section they belong to, so a bandit's `U (A)` puts U on the group and A in the lair.
    """

    extra_gp: int = Field(default=0, ge=0)
    """Gold pieces the stat block adds on top of the rolled treasure, into the lair hoard."""

    multiplier: int = Field(default=1, ge=1)
    """How many times the whole listed generation repeats, as with a noble's `V × 3`.

    1 unless the SRD says otherwise.
    """

    special: tuple[str, ...] = ()
    """Valuables that are not treasure types at all, like an elephant's tusks or a bee's honey. Nothing generates these;
    show them to the referee.
    """

    see_below: bool = False
    """True when the creature's description says what it has rather than the treasure line."""


class MonsterAbility(BaseModel):
    """One thing a monster can do, as a tag the rules read plus the SRD text it came from.

    Read them from [`MonsterTemplate.abilities`][osrlib.core.monsters.MonsterTemplate], or look one
    up by tag with [`MonsterTemplate.ability`][osrlib.core.monsters.MonsterTemplate.ability]. The
    combat and effect procedures match on the tag and read the numbers out of `params`, so a
    monster's special powers are data rather than branches in the code. Frozen.

    Not everything reduces to numbers. An ability marked manual is text for a referee to read and
    act on. Nothing in osrlib does anything with it.
    """

    model_config = ConfigDict(frozen=True)

    tag: str = Field(min_length=1)
    """The identifier the rules match on. The ones osrlib acts on are `"regeneration"`, `"energy_drain"`, `"poison"`,
    `"paralysis"`, `"petrification"`, `"breath_weapon"`, `"gaze"`, `"disease"`, and `"uses_fire"`.
    """

    name: str = Field(min_length=1)
    """The ability's name as the SRD prints it, for display."""

    prose: str
    """The SRD's own description, which is what to show a player or referee."""

    manual: bool = False
    """True when nothing in osrlib acts on this ability and the prose is the whole of it."""

    params: dict[str, int | str | bool | tuple[int | str, ...]] = {}
    """The values the rules read, like a troll's regeneration delay and rate, or the number of levels an energy drain
    takes. Empty when there are none.
    """


class AcAlternate(BaseModel):
    """An armour class a monster has only in particular circumstances.

    Read them from
    [`MonsterTemplate.ac_alternates`][osrlib.core.monsters.MonsterTemplate]. A creature that
    changes shape, or a band whose members wear different armour, prints more than one armour class
    and this contains the ones that are not the creature's ordinary value. Deciding when one applies
    is the referee's call. Nothing switches between them for you. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    ac: int
    """The armour class in the descending presentation, where lower is better."""

    ac_ascending: int
    """The same defense in the ascending presentation, where higher is better."""

    condition: str = ""
    """When this value applies, in the SRD's own words, like `"in human form"`. Empty when the SRD prints the
    alternative without saying when it holds, as it does for a band whose members are armed differently.
    """


class MonsterTemplate(BaseModel):
    """A monster's stat block: everything true of every creature of that kind.

    Get one from [`load_monsters`][osrlib.data.load_monsters]`().get(monster_id)`; see
    [the monster id index][monsters-index] for the ids. Then call
    [`spawn_monster`][osrlib.core.monsters.spawn_monster] to put an actual creature in front of the
    players.

    It is frozen, and play never writes to it. A template is shared by every creature of its kind,
    while the hit points one creature has left, the wounds it has taken, and the conditions on it
    all live on the [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] spawned from it. That
    separation is why a wounded troll does not weaken every other troll in the dungeon.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """The monster id, like `"troll"`, which is what you look it up by."""

    name: str
    """The creature's name as the SRD prints it, for display."""

    page: str
    """Which SRD page this template was compiled from. Templates sharing a page are variants of one creature, like the
    sizes of hydra or the colours of dragon.
    """

    intro: str = ""
    """The SRD's description of the creature, for a referee or a narrator to read out."""

    ac: int | None = None
    """Armour class in the descending presentation. `None` for a creature whose attackers need no hit roll."""

    ac_ascending: int | None = None
    """The same defense in the ascending presentation, or `None` alongside `ac`."""

    ac_alternates: tuple[AcAlternate, ...] = ()
    """Armour classes that apply only in particular circumstances; see
    [`AcAlternate`][osrlib.core.monsters.AcAlternate].
    """

    attack_roll_required: bool = True
    """False for a creature no attack roll is needed against, like a green slime, whose attacks land without one. Both
    armour class fields are `None` then.
    """

    hit_dice: MonsterHitDice
    """The dice its hit points are rolled on; see [`MonsterHitDice`][osrlib.core.monsters.MonsterHitDice]."""

    attacks: tuple[AttackRoutine, ...] = ()
    """The sets of attacks it can make, one chosen per round; see [`AttackRoutine`][osrlib.core.monsters.AttackRoutine].
    """

    thac0: int = Field(ge=2, le=20)
    """The number it needs to hit armour class 0 under descending armour class."""

    attack_bonus: int = Field(ge=-1)
    """The same attack under ascending armour class."""

    movement: tuple[MovementMode, ...] = Field(min_length=1)
    """How it gets around and how fast, ordinary movement first; see
    [`MovementMode`][osrlib.core.monsters.MovementMode].
    """

    saves: MonsterSaves
    """Its saving throws; see [`MonsterSaves`][osrlib.core.monsters.MonsterSaves]."""

    morale: int | None = Field(default=None, ge=2, le=12)
    """How willing it is to keep fighting, from 2 to 12. A morale check rolls 2d6 and the creature holds on a result at
    or under it. `None` when the stat block prints no morale score.
    """

    morale_alternates: tuple[MoraleAlternate, ...] = ()
    """Morale scores that apply only in particular circumstances; see
    [`MoraleAlternate`][osrlib.core.monsters.MoraleAlternate].
    """

    alignment: AlignmentSpec
    """Which alignments it may have; see [`AlignmentSpec`][osrlib.core.monsters.AlignmentSpec]."""

    xp: int = Field(ge=0)
    """The experience for defeating one, as the SRD prints it."""

    xp_notes: tuple[XpNote, ...] = ()
    """What a leader among them is worth instead; see [`XpNote`][osrlib.core.monsters.XpNote]."""

    number_appearing: NumberAppearing
    """How many turn up, which depends on where they are met; see
    [`NumberAppearing`][osrlib.core.monsters.NumberAppearing].
    """

    treasure: TreasureRef = TreasureRef()
    """What they have; see [`TreasureRef`][osrlib.core.monsters.TreasureRef]."""

    abilities: tuple[MonsterAbility, ...] = ()
    """What it can do beyond attacking; see [`MonsterAbility`][osrlib.core.monsters.MonsterAbility]."""

    defenses: Defenses = Defenses()
    """What it resists, in the form the damage rules check; see [`Defenses`][osrlib.core.monsters.Defenses]."""

    categories: tuple[str, ...] = ()
    """What kind of thing it is, as tags like `"undead"`, `"person"`, and `"enchanted"`. Spells and effects that single
    out a kind of creature match on these.
    """

    overrides_applied: tuple[str, ...] = ()
    """The names of the compile-time corrections applied to this creature's SRD page. Provenance for anyone checking the
    data against the SRD. Nothing in play reads it.
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

        Use it to ask whether a creature has a power and to read the numbers behind it in one step:
        the troll's regeneration rate, the number of levels a wight's touch drains, the shape and
        element of a dragon's breath.

        Args:
            tag: The ability tag to look for, like `"regeneration"` or `"energy_drain"`.

        Returns:
            The first ability with that tag, or `None`.

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
    """Every monster, as returned by [`load_monsters`][osrlib.data.load_monsters].

    Look one up by id with [`get`][osrlib.core.monsters.MonsterCatalog.get], or iterate `monsters`
    to filter by Hit Dice, category, or whatever your encounter table needs. The catalog is loaded
    once and cached, so calling the loader again is free. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    monsters: tuple[MonsterTemplate, ...]
    """The templates, in the order the data file lists them. Ids are unique."""

    @model_validator(mode="after")
    def _ids_must_be_unique(self) -> MonsterCatalog:
        ids = [template.id for template in self.monsters]
        if len(set(ids)) != len(ids):
            raise ValueError("monster ids must be unique")
        return self

    def get(self, monster_id: str) -> MonsterTemplate:
        """Return the monster with `monster_id`.

        Args:
            monster_id: The id to look up, like `"troll"` or `"red_dragon"`; see
                [the monster id index][monsters-index] for all of them.

        Returns:
            The monster template. Spawn a creature from it with
            [`spawn_monster`][osrlib.core.monsters.spawn_monster].

        Raises:
            ValueError: If no monster has that id. The message names the id you passed.

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

    Get one from [`spawn_monster`][osrlib.core.monsters.spawn_monster]. This is what takes damage,
    gains conditions, and dies. The template it came from never changes, and every other creature
    spawned from it is unaffected by what happens here.

    It offers the same surface a [`Character`][osrlib.core.character.Character] does, which is
    THAC0, attack bonus, both armour classes, saving throws, conditions, and stat modifiers, so
    the functions in [`osrlib.core.combat`][osrlib.core.combat] and
    [`osrlib.core.effects`][osrlib.core.effects] take either without caring which they got.

    Anything the template already says is read through `template` rather than copied here, and the
    properties below do that for you where a drained creature would otherwise read the wrong value.
    """

    model_config = ConfigDict(validate_assignment=True)

    id: str
    """The entity id, usually from an [`IdAllocator`][osrlib.core.monsters.IdAllocator]. Events name the creature by it.
    """

    template: MonsterTemplate
    """The stat block this creature was spawned from. Read anything the creature has in common with its kind from here.
    """

    max_hp: int = Field(ge=1)
    """The hit points it was spawned with."""

    current_hp: int = Field(ge=0)
    """The hit points it has left, from 0 up to `max_hp`. Reaching 0 means it has dropped. Death itself is the `dead`
    condition, applied by [`kill`][osrlib.core.effects.kill].
    """

    conditions: tuple[ActiveCondition, ...] = ()
    """The conditions on it, applied and cleared through [`osrlib.core.effects`][osrlib.core.effects]."""

    stat_modifiers: tuple[ActiveModifier, ...] = ()
    """Timed bonuses and penalties on it, from spells and effects."""

    alignment: Alignment | None = None
    """The alignment this creature actually has, settled when it was spawned. `None` when the template offered several
    and neither you nor the template named one. A ward that turns on alignment then treats it as differing, which errs
    toward protecting the party.
    """

    nonregen_damage: int = Field(default=0, ge=0)
    """Damage a regenerating creature can never heal. Fire and acid land here, and a troll stays dead only once this
    alone reaches its maximum hit points.
    """

    last_damaged_round: int | None = None
    """The combat round in which it was last hurt, which is what regeneration counts its delay from. `None` before
    anything has hurt it.
    """

    breath_uses_today: int = Field(default=0, ge=0)
    """How many times it has used its breath weapon today. A creature with a breath weapon gets three uses a day."""

    drained_hd: int = Field(default=0, ge=0)
    """How many Hit Dice have been drained from it. Its THAC0, attack bonus, and saving throws all re-derive from what
    is left.
    """

    @property
    def name(self) -> str:
        """The creature's name, from its template. Use it wherever you show the creature to a player."""
        return self.template.name

    @property
    def hit_dice_count(self) -> int:
        """How many Hit Dice this creature still has: its template's, less any that were drained away.

        Its THAC0, attack bonus, and saving throws all follow from this rather than from the
        template, which is why draining a creature weakens it in every way at once.
        """
        return max(0, self.template.hit_dice.count - self.drained_hd)

    @property
    def thac0(self) -> int:
        """The number this creature needs to hit armour class 0 under descending armour class.

        It is the template's printed value, which already accounts for a creature whose Hit Dice
        have a bonus attacking as though it had one more. A creature that has been drained looks
        its value up again for the Hit Dice it has left.
        """
        if self.drained_hd == 0:
            return self.template.thac0
        from osrlib.core.tables import thac0_for_hd

        return thac0_for_hd(self.hit_dice_count, bonus_modifier=self.template.hit_dice.modifier > 0)[0]

    @property
    def attack_bonus(self) -> int:
        """The bonus this creature adds to an attack roll under ascending armour class.

        The ascending presentation of [`thac0`][osrlib.core.monsters.MonsterInstance.thac0], and it
        re-derives after a drain in the same way.
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

        The template's printed values, unless the creature has been drained of Hit Dice, in which
        case it saves as the band its remaining Hit Dice put it in.
        """
        if self.drained_hd == 0:
            return self.template.saves.values
        from osrlib.core.tables import monster_save_band_label
        from osrlib.data import load_combat_tables

        dice = self.template.hit_dice.model_copy(update={"count": self.hit_dice_count})
        return load_combat_tables().save_band(monster_save_band_label(dice)).saves

    @property
    def melee_modifier(self) -> int:
        """Always 0: monsters have no STR score, and the SRD gives them no bonus in its place.

        It exists so that the combat functions can read the same property on a monster as on a
        [`Character`][osrlib.core.character.Character].
        """
        return 0

    @property
    def missile_modifier(self) -> int:
        """Always 0: monsters have no DEX score, and the SRD gives them no missile bonus in its place."""
        return 0

    @property
    def initiative_modifier(self) -> int:
        """Always 0: a monster has no initiative modifier of its own.

        The SRD leaves a monster's initiative to the referee, so pass one to the initiative roll
        yourself when you want it.
        """
        return 0


def spawn_monster(
    template: MonsterTemplate, *, id: str, stream: RngStream, alignment: Alignment | None = None
) -> MonsterInstance:
    """Put one creature into play: roll its hit points and give it an identity of its own.

    Call it once per creature an encounter puts in front of the players. Load the catalog once with
    [`load_monsters`][osrlib.data.load_monsters] and spawn from the same template as often as you
    like. Each creature gets its own hit points, its own wounds, and its own conditions, and
    nothing that happens to one reaches the template or any of its siblings. That is the reason to
    spawn rather than pass templates around.

    Hand what you get to [`osrlib.core.combat`][osrlib.core.combat] to fight it. Its treasure comes
    from [`generate_treasure`][osrlib.core.treasure.generate_treasure] using the letters on the
    template.

    Hit points are rolled from the template's Hit Dice and floored at 1, so even the unluckiest
    roll leaves a creature standing. A creature whose hit points the SRD fixes, like the one
    with exactly 1 or a hydra with 8 per head, gets that number and rolls nothing.

    The creature's alignment is settled here rather than left open, because a ward like
    *protection from evil* has to have something to test. Your choice wins, then the template's
    usual alignment, then its only option. A creature whose template offers
    several with no usual one and no choice from you is left unresolved, and such a ward then
    treats it as differing, which errs toward protecting the party.

    Args:
        template: The stat block to spawn from, from
            [`load_monsters`][osrlib.data.load_monsters]`().get(monster_id)`; see
            [the monster id index][monsters-index] for the ids.
        id: The entity id to give it, usually
            `allocator.allocate("monster")` from an
            [`IdAllocator`][osrlib.core.monsters.IdAllocator].
        stream: The stream to roll hit points on, conventionally
            `streams.get(`[`MONSTER_SPAWN_STREAM`][osrlib.core.monsters.MONSTER_SPAWN_STREAM]`)`.
            No draw is taken for a creature with fixed hit points.
        alignment: The alignment this particular creature has, when you want to choose. It must be
            one the template allows.

    Returns:
        The creature, at full hit points, with no conditions and no wounds.

    Raises:
        ValueError: If `alignment` is not one the template allows.

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
    """Hands out entity ids that no two things in a game share.

    Every creature, effect, and valuable a game creates needs an id, and this is what gives them
    one. A [`GameSession`][osrlib.crawl.session.GameSession] keeps its own and passes it to
    everything that allocates, so pass the session's allocator rather than a fresh one when you are
    inside a session. Outside one, build your own.

    Ids count up per prefix and never repeat, which is what lets a save and its replay refer to the
    same creature. The counters are ordinary state, so an allocator serializes with the rest of a
    save and resumes where it left off.

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
    """How many ids have been handed out under each prefix. Written by
    [`allocate`][osrlib.core.monsters.IdAllocator.allocate]; you never set it yourself.
    """

    def allocate(self, prefix: str) -> str:
        """Return the next unused id under `prefix`.

        Each prefix counts independently and never repeats, so calling it twice with the same
        prefix gives two different ids. It mutates the allocator, which is the point: the id is
        spent once it is returned.

        Args:
            prefix: What kind of thing is being named, like `"monster"`, `"effect"`, `"npc"`,
                or `"valuable"`.

        Returns:
            The id, which is the prefix, a hyphen, and a number padded to four digits, counting
            from `0001`.
        """
        n = self.counters.get(prefix, 0) + 1
        self.counters[prefix] = n
        return f"{prefix}-{n:04d}"
