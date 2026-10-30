"""Resolve B/X combat: initiative, attacks, damage, saving throws, morale, and targeting.

Every function here works on the creatures and dice streams you pass in, changes the
creatures in place where the rules say so, and reports what happened as events. A script
with no session, dungeon, or battle can roll a whole fight with them, as the guide
[Using the rules without a session](https://mmacy.github.io/osrlib-python/guides/rules-without-a-session/)
shows. A session is a [`GameSession`][osrlib.crawl.session.GameSession], which runs a
whole game. Inside one, [`osrlib.crawl.battle`][osrlib.crawl.battle] calls these same
functions in the B/X round sequence, so a session logs the same events these functions
return.

In the order you use them in a combat round:

- [`roll_initiative`][osrlib.core.combat.roll_initiative] takes one
  [`Participant`][osrlib.core.combat.Participant] per combatant and returns the acting
  order.
- [`validate_attack`][osrlib.core.combat.validate_attack] takes an attacker, a defender,
  and what the attacker attacks with, and returns the reasons the attack isn't allowed. It changes
  nothing.
- [`resolve_attack`][osrlib.core.combat.resolve_attack] rolls one attack, applies the
  damage to the defender, and returns an [`AttackResult`][osrlib.core.combat.AttackResult].
  Start with this function.
- [`saving_throw`][osrlib.core.combat.saving_throw] rolls one creature's save and returns
  a [`SaveResult`][osrlib.core.combat.SaveResult].
- [`morale_triggers`][osrlib.core.combat.morale_triggers] takes a side's creatures and
  returns the reasons it must check morale.
  [`check_morale`][osrlib.core.combat.check_morale] rolls the check and returns a
  [`MoraleResult`][osrlib.core.combat.MoraleResult].

Some attacks resolve through a separate function instead of `resolve_attack`:
[`resolve_splash_attack`][osrlib.core.combat.resolve_splash_attack] for thrown holy water
and oil, [`resolve_breath`][osrlib.core.combat.resolve_breath] for breath weapons,
[`resolve_gaze`][osrlib.core.combat.resolve_gaze] for petrifying gazes, and
[`resolve_energy_drain`][osrlib.core.combat.resolve_energy_drain] for the level drain that
follows an undead creature's hit. [`select_targets`][osrlib.core.combat.select_targets]
chooses which of the candidates you list a spell reaches. Outside a fight,
[`roll_reaction`][osrlib.core.combat.roll_reaction] rolls how monsters react to the party,
[`apply_healing`][osrlib.core.combat.apply_healing] and
[`natural_healing`][osrlib.core.combat.natural_healing] restore hit points, and
[`falling_damage`][osrlib.core.combat.falling_damage] rolls the damage of a fall. The
rest of the module is the steps `resolve_attack` runs, which you can call one at a time.

Each parameter that takes a creature, like `attacker`, `defender`, or `target`, is typed
with a protocol from [`osrlib.core.creature`][osrlib.core.creature]. A
[`Character`][osrlib.core.character.Character] and a
[`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy each protocol. A
function that reads THAC0, armour class, or saving throws takes a
[`Combatant`][osrlib.core.creature.Combatant]. A function that reads only hit points,
conditions, and stat modifiers takes a [`Creature`][osrlib.core.creature.Creature]. A
function that needs what only a monster has, like the daily breath count, takes a
`MonsterInstance`. An NPC adventurer is a `Character`.

Attack functions also take an [`AttackContext`][osrlib.core.combat.AttackContext], which
states the situation of the attack, like the distance, cover, or a back-stab position.
Most functions take the [`Ruleset`][osrlib.core.ruleset.Ruleset] in play, and every
function that rolls dice takes an [`RngStream`][osrlib.core.rng.RngStream]. There's no
default stream. Build an [`RngStreams`][osrlib.core.rng.RngStreams] from a master seed
and pass each function the stream it draws from. Battle draws come from
[`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM]. `roll_reaction` draws from the
encounter stream, `natural_healing` from the effects stream, and
[`drain_monster_hd`][osrlib.core.combat.drain_monster_hd] and `resolve_energy_drain`
from the advancement stream.

Each function that resolves part of a fight returns events, either as a list or in the
`events` tuple of a frozen result model. Append them to your log. The result models also
have the outcome as plain fields, like `hit` and `damage`, in case you don't keep
a log. [`damage_roll`][osrlib.core.combat.damage_roll] and `falling_damage` only roll
dice, and return a [`RollResult`][osrlib.core.dice.RollResult] with no events.

Damage from an attack goes through these steps in this order:

1. The immunity gate. This is osrlib's name for the check of whether the defender's
   defenses let the attack harm it at all. If they don't, osrlib rolls no damage, and a
   [`DamageAbsorbedEvent`][osrlib.core.events.DamageAbsorbedEvent] reports the hit.
2. The damage roll: the dice, the attacker's STR modifier in melee, magic and spell
   bonuses, then the doublings for a brace, a charge, or a back-stab. The total is at
   least 1.
3. The defender's reductions, like the wraith's half damage from silver or *resist
   fire*'s 1 point off each die. None of them takes the damage below 1.
4. The hit points come off, stopping at 0. Fire and acid also count against a troll's
   regeneration. A defender at 0 hit points dies.

A monster attack that deals no hit point damage, like a wight's touch, stops after the
immunity gate.

[`validate_attack`][osrlib.core.combat.validate_attack] and
[`validate_breath`][osrlib.core.combat.validate_breath] check an action before you
resolve it. They take no draws, change nothing, and return a list of
[`Rejection`][osrlib.core.validation.Rejection] values, which is empty when the action
may go ahead. A session refuses a rejected command without spending a roll or any game
time. That's why holy water thrown at a living creature isn't rejected: it hits and does
nothing. Rejecting it would tell the player, at no cost, which creatures are undead.

Typical usage:

```python
from osrlib.core.combat import COMBAT_STREAM, AttackContext, Participant, check_morale, resolve_attack, roll_initiative
from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.data import load_equipment, load_monsters

rules = Ruleset()
streams = RngStreams(master_seed=3)
spawn = streams.get(MONSTER_SPAWN_STREAM)
catalog = load_monsters()
goblin = spawn_monster(catalog.get("goblin"), id="goblin-1", stream=spawn)
orc = spawn_monster(catalog.get("orc"), id="orc-1", stream=spawn)

combat = streams.get(COMBAT_STREAM)
initiative = roll_initiative(
    [Participant(key="goblin-1", side="goblins"), Participant(key="orc-1", side="orcs")],
    ruleset=rules,
    stream=combat,
)
assert initiative.order == ("goblin-1", "orc-1")

sword = load_equipment().get("sword")
attack = resolve_attack(goblin, orc, sword, context=AttackContext(), ruleset=rules, stream=combat)
assert attack.attack_roll.hit
assert attack.damage == 4
assert orc.current_hp == 3  # down from 7

morale = check_morale("orcs", orc.template.morale, stream=combat)
assert not morale.held  # 2d6 showed 9 against the orc's morale score of 6
```
"""

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, ConfigDict

from osrlib.core.classes import SavingThrows
from osrlib.core.clock import GameClock, TimeUnit
from osrlib.core.creature import Combatant, Creature
from osrlib.core.dice import RollResult, roll
from osrlib.core.effects import (
    Condition,
    EffectDefinition,
    EffectsLedger,
    has_condition,
    has_modifier,
    kill,
    modifier_dice,
    modifier_total,
    modifier_values,
)
from osrlib.core.events import (
    AttackRolledEvent,
    DamageAbsorbedEvent,
    DamageDealtEvent,
    DeathEvent,
    EquipmentDestroyedEvent,
    Event,
    HealingAppliedEvent,
    HitPointsReportedEvent,
    InitiativeRoll,
    InitiativeRolledEvent,
    MoraleCheckedEvent,
    ReactionRolledEvent,
    SavingThrowRolledEvent,
    TargetsSelectedEvent,
)
from osrlib.core.items import (
    CombatFacet,
    GearTemplate,
    MagicItemInstance,
    MissileRanges,
    WeaponQuality,
    WeaponTemplate,
    equipped_item_modifiers,
    magic_item_template,
)
from osrlib.core.monsters import Element, MonsterAbility, MonsterAttack, MonsterInstance
from osrlib.core.rng import RngStream, StreamName
from osrlib.core.ruleset import Ruleset
from osrlib.core.tables import ReactionResult, reaction_result, to_hit_ac
from osrlib.core.validation import Rejection

if TYPE_CHECKING:
    from osrlib.core.character import Character

__all__ = [
    "Attack",
    "AttackContext",
    "AttackResult",
    "AttackRollResult",
    "COMBAT_STREAM",
    "DamageSource",
    "InitiativeResult",
    "MELEE_REACH_FEET",
    "MoraleResult",
    "MoraleTracker",
    "Participant",
    "ReactionRollResult",
    "SaveCategory",
    "SaveResult",
    "TargetingMode",
    "alignments_differ",
    "apply_healing",
    "attack_facet",
    "attack_roll",
    "burning_oil_pool_definition",
    "cannot_move",
    "check_immunity",
    "check_morale",
    "damage_roll",
    "damage_source_for",
    "deal_damage",
    "destroy_equipment",
    "drain_monster_hd",
    "effective_hd",
    "falling_damage",
    "incapacitated",
    "melee_modifier_for",
    "morale_modifier",
    "morale_triggers",
    "natural_healing",
    "participant_modifier",
    "resolve_attack",
    "resolve_breath",
    "resolve_energy_drain",
    "resolve_gaze",
    "resolve_splash_attack",
    "roll_initiative",
    "roll_reaction",
    "saving_throw",
    "select_targets",
    "splash_douse_definition",
    "validate_attack",
    "validate_breath",
]

COMBAT_STREAM = StreamName.COMBAT
"""The stream key for battle draws: attacks, damage, saving throws, morale, and initiative.

Pass it to [`RngStreams.get`][osrlib.core.rng.RngStreams.get] to get the stream that most
functions in this module draw from. Reaction rolls, natural healing, and energy drain
draw from other streams, which their functions' descriptions give. A
[`GameSession`][osrlib.crawl.session.GameSession] draws its battle rolls from this key too.

A stream key is a name. The same master seed and the same key always produce the same
sequence of draws, so a script that seeds its streams the same way rolls the same fight
every time. Any key works for a script of your own, but with this one your battle draws
come from the same stream a session uses for battles.

Examples:
    ```python
    from osrlib.core.combat import COMBAT_STREAM
    from osrlib.core.rng import RngStreams

    combat = RngStreams(master_seed=3).get(COMBAT_STREAM)
    assert combat.randbelow(20) + 1 == 20
    ```
"""

MELEE_REACH_FEET = 5
"""How far a melee attack reaches, in feet.

When an [`AttackContext`][osrlib.core.combat.AttackContext] states a `distance_feet`
greater than this, [`validate_attack`][osrlib.core.combat.validate_attack] rejects a
melee-only attack, and a weapon that's both melee and missile is thrown or fired instead
of swung.
"""

_HELPLESS = (Condition.PARALYSED, Condition.ASLEEP, Condition.PETRIFIED)
_CANNOT_ACT = (Condition.DEAD, Condition.PETRIFIED, Condition.PARALYSED, Condition.ASLEEP)

# Holy water, the torch, and the oil flask are gear with combat stats, and each has
# a fixed damage source. Holy water has the `holy` key, which harms only undead
# targets. A torch and lit oil deal fire damage because they burn, and fire is what counts
# against a troll's regeneration.
_HOLY_ITEM_ID = "holy_water"
_FIRE_ITEM_IDS = ("torch", "oil_flask")

Attack = WeaponTemplate | CombatFacet | GearTemplate | MonsterAttack | MagicItemInstance | None
"""What a combatant attacks with. `None` is an unarmed attack, which deals 1d2.

[`resolve_attack`][osrlib.core.combat.resolve_attack],
[`attack_roll`][osrlib.core.combat.attack_roll],
[`damage_roll`][osrlib.core.combat.damage_roll],
[`validate_attack`][osrlib.core.combat.validate_attack], and
[`damage_source_for`][osrlib.core.combat.damage_source_for] take one. Each form comes
from a different place:

- A [`WeaponTemplate`][osrlib.core.items.WeaponTemplate] or a
  [`GearTemplate`][osrlib.core.items.GearTemplate] comes from
  [`load_equipment`][osrlib.data.load_equipment]. [The equipment id index](https://mmacy.github.io/osrlib-python/reference/content-ids/equipment/)
  lists the ids.
- A [`CombatFacet`][osrlib.core.items.CombatFacet] is the `combat` field of a gear
  template: the item's damage dice, qualities, and ranges without the item. An attack
  with a bare facet is named `improvised` in its event. Pass the gear template itself
  for holy water, a torch, or oil, because the facet has no item id to tell them apart.
- A [`MonsterAttack`][osrlib.core.monsters.MonsterAttack] comes from the attacking
  monster's template.
- A [`MagicItemInstance`][osrlib.core.items.MagicItemInstance] comes from a character's
  inventory or from treasure generation.

A `MagicItemInstance` attack is a magic weapon. Its base weapon gives the dice,
qualities, and ranges, and its template gives the attack and damage bonus. A magic
weapon with a bonus against certain monsters, like a Sword +1, +3 vs Dragons, uses
the higher bonus when the defender's template is one of them. Every magic weapon counts
as magical against a defender that only magic can harm, and a cursed one does too.

Use [`attack_facet`][osrlib.core.combat.attack_facet] to get the dice, qualities, and
ranges behind any of these forms without checking which form you have.

Examples:
    ```python
    from osrlib.core.combat import attack_facet
    from osrlib.data import load_equipment, load_monsters

    sword = load_equipment().get("sword")
    assert attack_facet(sword).damage == "1d8"

    goblin_attack = load_monsters().get("goblin").attacks[0].attacks[0]
    assert goblin_attack.damage == "1d6"
    assert attack_facet(None) is None  # unarmed has no facet
    ```
"""


class AttackContext(BaseModel):
    """The situation an attack happens in, as you state it.

    Build one and pass it as the `context` of
    [`resolve_attack`][osrlib.core.combat.resolve_attack] and the other functions that
    take one. An empty `AttackContext()` is the plain case: a melee swing at a defender
    that's aware of the attack and not retreating. The model is frozen, so build a new one
    for each attack instead of changing one.

    Most of these fields are judgments the tabletop rules leave to the referee, like
    whether the target is unaware of the attacker. The combat functions apply the rules to
    the context you give them and don't work any of it out themselves. A
    [`GameSession`][osrlib.crawl.session.GameSession] fills it in from the state of its
    battle, so you don't build one when a session runs the fight.

    Examples:
        ```python
        from osrlib.core.combat import AttackContext

        plain = AttackContext()
        assert plain.distance_feet is None
        assert plain.situational_modifier == 0

        back_stab = AttackContext(behind_target=True, target_unaware=True)
        assert back_stab.behind_target and back_stab.target_unaware
        ```
    """

    model_config = ConfigDict(frozen=True)

    distance_feet: int | None = None
    """How far apart the attacker and the defender are, in feet, or `None` to leave it unstated.

    With no distance, a melee weapon and a weapon that's both melee and missile are swung
    in melee, and a missile-only weapon is fired with no range modifier. A distance over
    [`MELEE_REACH_FEET`][osrlib.core.combat.MELEE_REACH_FEET] makes a melee-and-missile
    weapon a missile, and makes [`validate_attack`][osrlib.core.combat.validate_attack]
    reject a melee-only attack. A missile's distance sets its range modifier: +1 at short
    range, 0 at medium, and −1 at long.
    """

    situational_modifier: int = 0
    """The referee's adjustment to the attack roll, like −1 to −4 for cover, or +2 against a sleeping dragon."""

    defender_ally_ac_bonus: int = 0
    """How much an ally's magic improves the defender's armour class.

    A Ring of Protection 5' Radius protects the creatures near its wearer, for example.
    Which creatures are near enough is your judgment, so you pass the bonus here. A
    positive value makes the defender harder to hit.
    """

    behind_target: bool = False
    """Whether the attacker strikes from behind.

    The defender's shield doesn't count. With `target_unaware`, a thief attacker gets the
    back-stab bonus to hit and to damage.
    """

    target_unaware: bool = False
    """Whether the defender is unaware of the attack.

    With `behind_target`, a thief attacker gets the back-stab bonus to hit and to damage.
    """

    defender_retreating: bool = False
    """Whether the defender is retreating.

    The attacker gets +2 to hit and the defender's shield doesn't count. A
    [`GameSession`][osrlib.crawl.session.GameSession] sets it for an attack on a monster
    group that's fleeing. It also sets it for every attack the monster side makes on a party
    member who declared a retreat that round, alone or with the whole party. It's set from the
    start of the round, so it holds against monsters that act before the member moves. A
    retreat by the whole party sets it for every member.
    """

    braced: bool = False
    """Whether the attacker has set a brace-quality weapon, like a spear, against a charge. Its damage doubles."""

    charging: bool = False
    """Whether the attacker is charging with a charge-quality weapon, like a lance. Its damage doubles."""

    fired_last_round: bool = False
    """Whether the weapon was fired in the previous round.

    Under the `weapon_reload` ruleset flag,
    [`validate_attack`][osrlib.core.combat.validate_attack] then rejects a reload-quality
    weapon, like a crossbow.
    """

    attacker_large: bool = False
    """Whether the attacker is a large creature.

    A halfling defender then gets its armour class bonus against large opponents.
    """

    lit: bool = False
    """Whether a thrown oil flask is alight. Unlit oil deals no damage and no fire."""

    fixed_damage_option: int = 0
    """Which of a monster attack's `fixed_damage_options` to use, counting from 0.

    It matters only for a monster attack that lists more than one fixed amount, like an
    insect swarm's 2 or 4.
    """

    monster_missile: bool = False
    """Whether the monster's attack is a small missile, like a hobgoblin's arrow.

    *Protection from normal missiles* then blocks it, and it doesn't hit a paralysed,
    sleeping, or petrified defender automatically, as a melee attack does. osrlib never marks a monster
    attack as a small missile by itself, because some monster missiles, like a hurled
    boulder, aren't small.
    """


class DamageSource(BaseModel):
    """The properties of one source of damage that the defender's defenses are checked against.

    osrlib checks it against the defender's defenses to decide whether the damage harms the
    defender, and how much.
    Build one with [`damage_source_for`][osrlib.core.combat.damage_source_for] from an
    attacker, an attack, and a context. For damage that isn't an attack, like a spell, a
    trap, or a fall, construct one yourself. Pass it to
    [`check_immunity`][osrlib.core.combat.check_immunity] to ask whether the defender is
    immune to it, and to [`deal_damage`][osrlib.core.combat.deal_damage] to apply the
    damage. The model is frozen.

    Examples:
        ```python
        from osrlib.core.combat import DamageSource

        dragon_breath = DamageSource(element="fire", kind="breath", destructive=True)
        assert dragon_breath.keys == ()
        assert not dragon_breath.magical

        silver_dagger = DamageSource(keys=("silver",))
        assert silver_dagger.kind == "weapon"  # the default delivery
        ```
    """

    model_config = ConfigDict(frozen=True)

    keys: tuple[str, ...] = ()
    """The material and enchantment keys of the source, like `silver`, `magic`, or `holy`.

    A monster that only some materials can harm lists them in its `harmed_only_by`
    defense. A source harms that monster only when it has one of the listed keys, so a
    magic weapon has `magic` and a silver one has `silver`. Damage reductions
    can also depend on the keys, like the wraith's half damage from silver.
    """

    element: str | None = None
    """The energy element, like `fire`, `cold`, or `lightning`, or `None` for physical damage.

    The values are those of [`Element`][osrlib.core.monsters.Element]. Energy defenses,
    spells like *resist fire*, and a troll's regeneration all depend on the element.
    """

    magical: bool = False
    """Whether the source is magical.

    A monster that's immune only to the ordinary form of an element, as a red dragon is to
    fire, takes damage from a magical source of that element and none from a nonmagical
    one. *Protection from normal missiles* blocks only a nonmagical missile. osrlib checks
    a `harmed_only_by` defense against `keys` instead, so a magic source also has the
    `magic` key.
    """

    kind: str = "weapon"
    """How the damage is delivered, like `weapon`, `breath`, or `falling`.

    The model accepts any string. [`damage_source_for`][osrlib.core.combat.damage_source_for]
    sets `weapon`, `unarmed`, `monster`, or `splash`, and the rest of osrlib uses
    `breath`, `spell`, `device`, `effect`, `trap`, `falling`, and `deprivation`. osrlib
    reads it in two places. When a destructive source kills a creature, its magic
    items save versus breath for `breath`, versus spells for `spell`, versus wands for
    `device`, and versus death for any other kind. A monster that uses fire is immune to
    a nonmagical fire source of kind `splash`, which is burning oil.
    """

    destructive: bool = False
    """Whether the source destroys what the victim carries when it kills the victim.

    Breath weapons and *lightning bolt* do.
    [`destroy_equipment`][osrlib.core.combat.destroy_equipment] describes what's destroyed
    and which magic items get a save.
    """

    missile: bool = False
    """Whether the source is a small missile, which *protection from normal missiles* blocks.

    [`damage_source_for`][osrlib.core.combat.damage_source_for] sets it for a character's
    missile weapon and a thrown splash item. It sets it for a monster attack only when the
    context's `monster_missile` is true, because some monster missiles, like a hurled
    boulder, aren't small.
    """


class AttackRollResult(BaseModel):
    """An attack roll's outcome: whether it hit, and the numbers behind it.

    [`attack_roll`][osrlib.core.combat.attack_roll] returns one, and an
    [`AttackResult`][osrlib.core.combat.AttackResult] has one in its `attack_roll` field.
    Read `hit` to decide what happens next, and the other fields to show the player the
    arithmetic. The model is frozen.
    """

    model_config = ConfigDict(frozen=True)

    hit: bool
    """Whether the attack hit."""

    auto: bool = False
    """Whether the attack hit without a roll.

    That happens in melee against a defender that's paralysed, asleep, or petrified. It also happens
    against a monster whose template has no armour class, which means attacks against it
    need no roll, as for green slime and yellow mould. When this is true,
    `roll`, `total`, and `required` are `None`, and no draw was taken.
    """

    roll: int | None = None
    """The d20 as it landed, before modifiers, or `None` on an automatic hit."""

    modifier: int = 0
    """The sum of every modifier added to the roll. It can be negative."""

    total: int | None = None
    """`roll` plus `modifier`, or `None` on an automatic hit."""

    required: int | None = None
    """The total the attack needed to hit, or `None` on an automatic hit.

    It comes from the B/X attack table for the attacker's THAC0 and the defender's armour
    class, or from `THAC0 − AC` under the `thac0_arithmetic` ruleset flag.
    """

    natural: int | None = None
    """1 or 20 when the d20 alone decided the outcome, and `None` otherwise.

    A natural 20 always hits and a natural 1 always misses. This field is set only when
    that rule changed the outcome: a 20 whose total was below `required`, or a 1 whose
    total reached it. Use it to tell a lucky hit from an ordinary one.
    """

    events: tuple[Event, ...] = ()
    """The roll's [`AttackRolledEvent`][osrlib.core.events.AttackRolledEvent], ready to append to your log."""


class AttackResult(BaseModel):
    """The outcome of one whole attack: the roll, whether the defender's defenses stopped it, and the damage.

    [`resolve_attack`][osrlib.core.combat.resolve_attack] and
    [`resolve_splash_attack`][osrlib.core.combat.resolve_splash_attack] return one. By the
    time you have it, the damage is already applied to the defender, so the result reports
    what happened and leaves nothing for you to apply. The model is frozen.
    """

    model_config = ConfigDict(frozen=True)

    attack_roll: AttackRollResult
    """The attack roll."""

    absorbed: bool = False
    """Whether the attack hit and the defender's defenses stopped all its damage, so none was rolled.

    A miss is never absorbed.
    """

    damage: int | None = None
    """The damage rolled, before the defender's reductions, or `None` when none was rolled.

    osrlib rolls no damage on a miss, on an absorbed hit, or when a blade kills a sleeping
    defender outright. It's 0 for an unlit oil flask that hits, and for a monster attack
    that deals no hit point damage, like a wight's touch.

    The defender can lose fewer hit points than this. The
    [`DamageDealtEvent`][osrlib.core.events.DamageDealtEvent] in `events` has the amount
    after reductions, and the
    [`HitPointsReportedEvent`][osrlib.core.events.HitPointsReportedEvent] after it has the
    hit points the defender has left.
    """

    events: tuple[Event, ...] = ()
    """Every event the attack produced, in order, ready to append to your log."""


class SaveResult(BaseModel):
    """A saving throw's outcome: whether it passed, and the numbers behind it.

    [`saving_throw`][osrlib.core.combat.saving_throw] returns one. Read `passed` to decide
    the consequence. The save changes nothing itself, so applying the consequence is up to
    you. The model is frozen.
    """

    model_config = ConfigDict(frozen=True)

    passed: bool
    """Whether the save succeeded."""

    auto: bool = False
    """Whether the target passed without a roll.

    That happens when a monster's energy defense gives it an automatic save against a
    magical effect of that element, as a red dragon's does against a *fire ball*. When this is true, `roll`
    and `required` are `None`, and no draw was taken.
    """

    roll: int | None = None
    """The d20 as it landed, before modifiers, or `None` on an automatic save."""

    modifier: int = 0
    """The sum of every modifier added to the roll, including the `modifier` you passed."""

    required: int | None = None
    """The number `roll` plus `modifier` had to reach, or `None` on an automatic save."""

    events: tuple[Event, ...] = ()
    """The save's [`SavingThrowRolledEvent`][osrlib.core.events.SavingThrowRolledEvent], ready to append to your log."""


class MoraleResult(BaseModel):
    """A morale check's outcome: whether the side keeps fighting.

    [`check_morale`][osrlib.core.combat.check_morale] and
    [`MoraleTracker.check`][osrlib.core.combat.MoraleTracker.check] return one. When `held`
    is false, the side flees or surrenders, and making it do so is up to you. The model is
    frozen.
    """

    model_config = ConfigDict(frozen=True)

    held: bool
    """Whether the side keeps fighting.

    It's false for a side with a morale score of 2 or less, which never fights, even though
    no roll was made.
    """

    exempt: bool = False
    """Whether the side's morale score means it doesn't roll.

    A side with a score of 2 or less never fights, and a side with 12 or more never checks. When this is
    true, `roll` is `None` and no draw was taken.
    """

    roll: int | None = None
    """The 2d6 total, before the modifier, or `None` when the side was exempt."""

    modifier: int = 0
    """The adjustment that was applied, after it was limited to the range −2 to +2."""

    events: tuple[Event, ...] = ()
    """The check's [`MoraleCheckedEvent`][osrlib.core.events.MoraleCheckedEvent], ready to append to your log.

    It's visible to the referee only, because players learn a side's morale from what it
    does.
    """


class Participant(BaseModel):
    """One combatant in an initiative roll: its key, its side, and what changes its place in the order.

    Build one for each combatant and pass the list to
    [`roll_initiative`][osrlib.core.combat.roll_initiative]. List them in the order you
    want combatants with the same rank to act in. The model is frozen.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    """The combatant's identifier, which comes back in the acting order.

    Use the entity id you already track, so you can match the order to your creatures. Each
    participant needs a different key.
    """

    side: str
    """The name of the side the combatant fights on, like `party` or `goblins`.

    Under side initiative, everyone on a side acts on that side's single roll.
    """

    slow: bool = False
    """Whether the combatant fights with a slow weapon, like a two-handed sword.

    A slow combatant acts after every combatant that isn't slow, whatever the rolls are.
    """

    modifier: int = 0
    """The combatant's modifier to an individual initiative roll.

    It's used only when the `individual_initiative` ruleset flag is on. Get it from
    [`participant_modifier`][osrlib.core.combat.participant_modifier], which gives a
    character its DEX modifier plus the halfling's class bonus, and a monster the modifier
    you choose.
    """


class InitiativeResult(BaseModel):
    """The outcome of an initiative roll: every roll, re-rolls included, and the acting order.

    [`roll_initiative`][osrlib.core.combat.roll_initiative] returns one. Go through `order`
    to run the round. The model is frozen.
    """

    model_config = ConfigDict(frozen=True)

    mode: str
    """`side` when each side rolled once, or `individual` when each participant rolled.

    The `individual_initiative` ruleset flag selects which.
    """

    entries: tuple[InitiativeRoll, ...]
    """One [`InitiativeRoll`][osrlib.core.events.InitiativeRoll] for each key that rolled.

    A key is a side under side initiative, and a participant under individual initiative.
    The entries are in the order the keys first appear in the list you passed. Each
    entry's `rolls` has every die rolled for it, so a tie that was re-rolled shows each
    roll.
    """

    order: tuple[str, ...]
    """Every participant's key, in the order they act. Slow participants come last."""

    events: tuple[Event, ...] = ()
    """The roll's [`InitiativeRolledEvent`][osrlib.core.events.InitiativeRolledEvent], ready to append to your log."""


class SaveCategory(StrEnum):
    """The B/X saving throw categories.

    Pass one to [`saving_throw`][osrlib.core.combat.saving_throw]. The category depends
    on what forces the save: each spell lists its category, and a breath weapon uses `breath`.
    When a destructive source kills a creature, its magic items save in the category that
    matches the source.

    Each value is the lowercase name of a field of
    [`SavingThrows`][osrlib.core.classes.SavingThrows], which has a creature's target
    number for that category. The values appear in events and saved games, and stay fixed
    within a `schema_version`, the version stamp of the saved format.
    """

    DEATH = "death"
    """Death ray or poison. It's also the category for anything that has no other category."""

    WANDS = "wands"
    """Magic wands, and other magic devices."""

    PARALYSIS = "paralysis"
    """Paralysis or turning to stone, which is what a petrifying gaze forces."""

    BREATH = "breath"
    """Breath attacks. A character's WIS modifier to saves against magic doesn't apply to this category."""

    SPELLS = "spells"
    """Spells, rods, and staves."""


class TargetingMode(StrEnum):
    """The ways [`select_targets`][osrlib.core.combat.select_targets] chooses targets from a list of candidates.

    Pass one to `select_targets` with the candidates it chooses from. A spell that targets
    creatures targets with one of these modes, and so does a breath weapon. `osrlib.core`
    doesn't work out who is in range, so you supply the candidates. Inside a session,
    [`osrlib.crawl.battle`][osrlib.crawl.battle] supplies them from the distances it
    tracks.

    The values appear in events and stay fixed within a `schema_version`, the version stamp
    of the saved format.
    """

    SELF = "self"
    """The caster or user only. Put the caster first in the candidates, because `select_targets` takes the first one."""

    SINGLE = "single"
    """One creature: the first candidate."""

    UP_TO_N = "up_to_n"
    """The first N candidates in your order. N is fixed or rolled, like the 1d4 creatures of *hold person*."""

    HD_BUDGET = "hd_budget"
    """Candidates taken weakest first until a Hit Dice budget runs out, the way *sleep* works.

    `select_targets` skips a candidate with more Hit Dice than the budget has left, and goes
    on down the list.
    """

    AREA = "area"
    """Every candidate. Which creatures the area covers is yours to work out."""

    GAZE = "gaze"
    """Every candidate, for a gaze that reaches every creature in melee with the gazer."""


def _int_param(params: Mapping[str, Any], key: str, default: int = 0) -> int:
    """Read an integer param. The type checker can't narrow the validated union's value types by key."""
    return int(params.get(key, default))


def _entity_id(combatant: Any) -> str:
    identifier = getattr(combatant, "id", None)
    return identifier if identifier is not None else getattr(combatant, "name", "unknown")


def alignments_differ(source: Creature, target: Creature) -> bool:
    """Return whether two creatures have different alignments, in the sense a ward against another alignment uses.

    Wards like *protection from evil* protect against creatures "of another alignment",
    and this function gives that answer.
    [`attack_roll`][osrlib.core.combat.attack_roll] and
    [`saving_throw`][osrlib.core.combat.saving_throw] call it for you, so you need it only
    when you write a ward of your own.

    A creature with no alignment counts as being of another alignment, so the ward
    protects against it. A monster whose template lists several alignments has none when
    it was spawned without one chosen.

    Args:
        source: The creature the ward protects against, usually the attacker. A
            [`Creature`][osrlib.core.creature.Creature], which a
            [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy.
        target: The `Creature` the ward protects, a `Character` or a `MonsterInstance`.

    Returns:
        True when the alignments differ or either creature has no alignment.

    Examples:
        ```python
        from osrlib.core.combat import alignments_differ
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        spawn = RngStreams(master_seed=3).get(MONSTER_SPAWN_STREAM)
        catalog = load_monsters()
        goblin = spawn_monster(catalog.get("goblin"), id="goblin-1", stream=spawn)
        acolyte = spawn_monster(catalog.get("acolyte"), id="acolyte-1", stream=spawn)

        assert goblin.alignment == "chaotic"
        assert acolyte.alignment is None  # the acolyte lists several, and none was chosen
        assert alignments_differ(goblin, acolyte)
        assert not alignments_differ(goblin, goblin)
        ```
    """
    source_alignment = getattr(source, "alignment", None)
    target_alignment = getattr(target, "alignment", None)
    if source_alignment is None or target_alignment is None:
        return True
    return source_alignment != target_alignment


def _class_ability_params(combatant: Any, tag: str) -> dict[str, int | str] | None:
    definition = getattr(combatant, "definition", None)
    if definition is None:
        return None
    for ability in definition.abilities:
        if ability.tag == tag:
            return ability.params
    return None


def _monster_ability_params(combatant: Any, tag: str) -> dict[str, Any] | None:
    template = getattr(combatant, "template", None)
    if template is None:
        return None
    ability = template.ability(tag)
    return ability.params if ability is not None else None


def _attack_name(attack: Attack) -> str:
    if attack is None:
        return "unarmed"
    if isinstance(attack, MonsterAttack):
        return attack.name
    if isinstance(attack, CombatFacet):
        return "improvised"
    if isinstance(attack, MagicItemInstance):
        return magic_item_template(attack).name
    return attack.name


def _magic_base(attack: MagicItemInstance) -> WeaponTemplate | None:
    """Return an enchanted arm's mundane base weapon template, when it has one.

    The staff of striking has no listed base damage for ordinary use. Its 2d6-per-charge
    form resolves through the crawl layer's device path, and wielded plainly it swings as
    the mundane staff.
    """
    from osrlib.data import load_equipment

    base_id = attack.base_item_id or magic_item_template(attack).base_item_id
    if base_id is None:
        return None
    base = load_equipment().get(base_id)
    return base if isinstance(base, WeaponTemplate) else None


def _magic_weapon_bonus(attack: Attack, defender: Any | None, *, damage: bool) -> int:
    """Return an enchanted arm's effective attack or damage bonus against a defender.

    The template's `attack_bonus` applies to the attack roll and its `damage_bonus` to the
    damage roll, unless a versus clause matches the defender's template (by category tag or
    template id). Then the clause's single `bonus` replaces both. Characters have no
    template and never match a clause.

    Args:
        attack: The attack. Anything but an enchanted item adds 0.
        defender: The defender, or `None` when there isn't one.
        damage: True for the damage roll's bonus, False for the attack roll's.
    """
    if not isinstance(attack, MagicItemInstance):
        return 0
    template = magic_item_template(attack)
    bonus = template.damage_bonus if damage else template.attack_bonus
    defender_template = getattr(defender, "template", None)
    if defender_template is not None:
        for clause in template.versus:
            if (
                set(clause.categories) & set(defender_template.categories)
                or defender_template.id in clause.template_ids
            ):
                bonus = clause.bonus
                break
    return bonus


def _facet(attack: Attack) -> WeaponTemplate | CombatFacet | None:
    """Return the combat stats of a character attack (a gear item's embedded facet)."""
    if isinstance(attack, GearTemplate):
        return attack.combat
    if isinstance(attack, MagicItemInstance):
        return _magic_base(attack)
    if isinstance(attack, WeaponTemplate | CombatFacet):
        return attack
    return None


def attack_facet(attack: Attack) -> WeaponTemplate | CombatFacet | None:
    """Return the damage dice, qualities, and ranges behind any attack.

    Use it to read those stats without checking which form of
    [`Attack`][osrlib.core.combat.Attack] you have. It returns a weapon as it is, a gear
    item's `combat` field, and a magic weapon's base weapon. The functions
    that resolve attacks read these stats themselves, so you need this function to show an
    attack's stats or to choose between attacks, not to resolve one.

    Args:
        attack: The [`Attack`][osrlib.core.combat.Attack]: a weapon, a combat facet, a gear
            item, a monster attack, a magic item, or `None` for unarmed.

    Returns:
        The stats, as a [`WeaponTemplate`][osrlib.core.items.WeaponTemplate] or a
        [`CombatFacet`][osrlib.core.items.CombatFacet]. `None` for an unarmed attack, a
        monster attack, a gear item with no combat stats, and a magic item with no base
        weapon, which is most magic items: armour, rings, potions, and the rest. A monster
        attack has its damage on the [`MonsterAttack`][osrlib.core.monsters.MonsterAttack]
        itself.

    Examples:
        ```python
        from osrlib.core.combat import attack_facet
        from osrlib.data import load_equipment

        catalog = load_equipment()
        assert attack_facet(catalog.get("sword")).damage == "1d8"
        assert attack_facet(catalog.get("oil_flask")).damage == "1d8"  # the gear item's facet
        assert attack_facet(catalog.get("torch")).damage == "1d4"
        assert attack_facet(None) is None  # unarmed: damage_roll rolls 1d2 for it
        ```
    """
    return _facet(attack)


def _item_modifier_total(
    target: Any,
    kind: str,
    *,
    element: str | None = None,
    save_category: str | None = None,
    melee: bool = False,
) -> int:
    """Total the matching modifiers from equipped items.

    Item bonuses are computed from the equipped inventory at query time rather than from
    `ActiveEffect` stat modifiers, and the cumulative caps on spell modifiers don't apply
    to them. Matching values sum.
    """
    inventory = getattr(target, "inventory", None)
    if inventory is None:
        return 0
    total = 0
    for spec in equipped_item_modifiers(inventory):
        if spec.kind != kind:
            continue
        if spec.element is not None and spec.element != element:
            continue
        if spec.save_categories and save_category not in spec.save_categories:
            continue
        if spec.melee_only and not melee:
            continue
        total += spec.value
    return total


def _strength_set_value(combatant: Any) -> int | None:
    """Return the operative `strength_set` value, or `None`.

    Precedence: the first active stat modifier in attachment order wins (so the
    Ring of Weakness's curse, an attached item effect, dominates worn gauntlets),
    then the first equipped item's, in equipped order.
    """
    for modifier in getattr(combatant, "stat_modifiers", ()):
        if modifier.kind == "strength_set":
            return modifier.value
    inventory = getattr(combatant, "inventory", None)
    if inventory is not None:
        for spec in equipped_item_modifiers(inventory):
            if spec.kind == "strength_set":
                return spec.value
    return None


def melee_modifier_for(combatant: Combatant) -> int:
    """Return the modifier a combatant adds to melee attack rolls and melee damage.

    [`attack_roll`][osrlib.core.combat.attack_roll] and
    [`damage_roll`][osrlib.core.combat.damage_roll] add it themselves on every melee
    attack. Call it to show a character's melee bonus or to preview a swing. Don't add it
    to the `situational_modifier` of an attack, or it counts twice.

    A character's modifier comes from its STR score. A magic item that sets STR to a fixed
    score, like the Gauntlets of Ogre Power (18) or the Ring of Weakness (3), replaces the
    character's STR score for this. A monster has no STR score, so its modifier is 0.

    Args:
        combatant: The attacking [`Combatant`][osrlib.core.creature.Combatant], which a
            [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy.

    Returns:
        The modifier, from −3 to +3.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.combat import melee_modifier_for
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.data import load_monsters

        streams = RngStreams(master_seed=17)
        hild = create_character(
            name="Hild",
            class_id="fighter",
            alignment=Alignment.LAWFUL,
            ruleset=Ruleset(),
            stream=streams.get(CHARACTER_CREATION_STREAM),
        ).character
        assert melee_modifier_for(hild) == 2  # this Hild rolled STR 16

        goblin = spawn_monster(load_monsters().get("goblin"), id="goblin-1", stream=streams.get(MONSTER_SPAWN_STREAM))
        assert melee_modifier_for(goblin) == 0
        ```
    """
    strength = _strength_set_value(combatant)
    if strength is not None and getattr(combatant, "definition", None) is not None:
        from osrlib.data import load_ability_tables

        return load_ability_tables().melee_modifier(strength)
    return getattr(combatant, "melee_modifier", 0)


def _qualities(attack: Attack) -> tuple[WeaponQuality, ...]:
    facet = _facet(attack)
    return facet.qualities if facet is not None else ()


def _missile_ranges(attack: Attack) -> MissileRanges | None:
    facet = _facet(attack)
    return facet.missile_ranges if facet is not None else None


def _is_missile_use(attack: Attack, context: AttackContext) -> bool:
    qualities = _qualities(attack)
    if WeaponQuality.MISSILE not in qualities:
        return False
    if WeaponQuality.MELEE in qualities:
        return context.distance_feet is not None and context.distance_feet > MELEE_REACH_FEET
    return True


def _range_band_modifier(attack: Attack, context: AttackContext) -> int | None:
    """Return +1/0/−1 for short/medium/long range, or `None` beyond long range."""
    ranges = _missile_ranges(attack)
    if ranges is None or context.distance_feet is None:
        return 0
    distance = context.distance_feet
    if distance <= ranges.short.max_feet:
        return 1
    if distance <= ranges.medium.max_feet:
        return 0
    if distance <= ranges.long.max_feet:
        return -1
    return None


def damage_source_for(attacker: Creature, attack: Attack, context: AttackContext) -> DamageSource:
    """Build the damage source of an attack, for checking against the defender's defenses.

    [`resolve_attack`][osrlib.core.combat.resolve_attack] builds one for you on every hit.
    Call this function yourself to ask
    [`check_immunity`][osrlib.core.combat.check_immunity] whether an attack can harm a
    defender before the party spends a round finding out. For damage that isn't an attack,
    like a trap or a spell, construct a [`DamageSource`][osrlib.core.combat.DamageSource]
    yourself.

    What each attack's source has:

    - A silver weapon has the `silver` key, and a magic weapon has `magic`.
    - Holy water has `holy`.
    - A torch, and an oil flask with `lit` set in the context, deal fire. Fire counts
      against a troll's regeneration.
    - A weapon, but not an unarmed strike, has `magic` while its wielder is under the
      *striking* spell.
    - A monster attack has no keys, and its `kind` is `monster`.
    - The function marks a missile weapon used at a distance, and thrown oil or holy water,
      as a small missile. It marks a monster attack only when the context's
      `monster_missile` is true.

    The `hd5_counts_as_magical` ruleset flag lets a strong monster harm monsters that only
    silver or magic can harm. `check_immunity` applies it from the attacker, so this
    function doesn't.

    Args:
        attacker: The attacking [`Creature`][osrlib.core.creature.Creature], which a
            [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy.
        attack: The [`Attack`][osrlib.core.combat.Attack], or `None` for unarmed.
        context: The attack's context. Its `lit` field is whether an oil flask is alight, and
            its `distance_feet` sets whether a melee-and-missile weapon is thrown.

    Returns:
        The frozen damage source, ready for `check_immunity` and
        [`deal_damage`][osrlib.core.combat.deal_damage].

    Examples:
        ```python
        from osrlib.core.combat import AttackContext, damage_source_for
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_equipment, load_monsters

        spawn = RngStreams(master_seed=3).get(MONSTER_SPAWN_STREAM)
        goblin = spawn_monster(load_monsters().get("goblin"), id="goblin-1", stream=spawn)
        catalog = load_equipment()

        silver = damage_source_for(goblin, catalog.get("silver_dagger"), AttackContext())
        assert silver.keys == ("silver",)

        oil = damage_source_for(goblin, catalog.get("oil_flask"), AttackContext(lit=True))
        assert (oil.element, oil.kind, oil.missile) == ("fire", "splash", True)

        fist = damage_source_for(goblin, None, AttackContext())
        assert fist.kind == "unarmed"
        ```
    """
    keys: list[str] = []
    element: str | None = None
    kind = "weapon"
    magical = False
    missile = False
    if attack is None:
        kind = "unarmed"
    elif isinstance(attack, MonsterAttack):
        kind = "monster"
        missile = context.monster_missile
    elif isinstance(attack, MagicItemInstance):
        # An enchanted arm counts as magical for the immunity checks, cursed
        # forms included.
        keys.append("magic")
        magical = True
        if _is_missile_use(attack, context):
            missile = True
    else:
        material = getattr(attack, "material", None)
        if material is not None and material.value == "silver":
            keys.append("silver")
        if isinstance(attack, GearTemplate):
            if WeaponQuality.SPLASH in _qualities(attack):
                kind = "splash"
                missile = True
            if attack.id == _HOLY_ITEM_ID:
                keys.append("holy")
            if attack.id == "torch" or (attack.id == "oil_flask" and context.lit):
                element = "fire"
        if _is_missile_use(attack, context):
            missile = True
        # The wielder's *striking* enchantment: the weapon counts as magical.
        if has_modifier(attacker, "counts_as_magical"):
            keys.append("magic")
            magical = True
    return DamageSource(keys=tuple(keys), element=element, kind=kind, magical=magical, missile=missile)


def _is_bladed(attack: Attack) -> bool:
    """Return whether the attack is a bladed weapon, for the sleeping-kill hook.

    osrlib reads "bladed" as a weapon (not a gear facet, a monster's natural attack, or an
    unarmed strike) with the melee quality and without the blunt quality. The SRD's blunt
    list is what separates crushing weapons from edged ones.
    """
    return (
        isinstance(attack, WeaponTemplate)
        and WeaponQuality.MELEE in attack.qualities
        and WeaponQuality.BLUNT not in attack.qualities
    )


def validate_attack(
    attacker: Creature, defender: Creature, attack: Attack, context: AttackContext, *, ruleset: Ruleset
) -> list[Rejection]:
    """Check whether an attack is allowed, without rolling anything or changing anything.

    Call this before [`resolve_attack`][osrlib.core.combat.resolve_attack] to tell the
    player why an attack can't happen instead of resolving it. `resolve_attack` doesn't
    call it for you, and resolves whatever you pass it. An empty list means the attack may
    go ahead.

    The rejections, by [`Rejection`][osrlib.core.validation.Rejection] code:

    - `combat.attack.attacker_incapacitated`: the attacker is dead, turned to stone,
      paralysed, asleep, or weakened. The `condition` param is the condition.
    - `combat.attack.attacker_blind`: the attacker is blind.
    - `combat.attack.out_of_range`: a missile is aimed past its long range.
    - `combat.attack.reload`: under the `weapon_reload` ruleset flag, a reload-quality
      weapon, like a crossbow, fired in the previous round too.
    - `combat.attack.out_of_reach`: a melee attack at a distance beyond
      [`MELEE_REACH_FEET`][osrlib.core.combat.MELEE_REACH_FEET].

    An attacker who is incapacitated or blind gets that one rejection, and the other checks
    don't run. [The rejection code reference][rejection-codes] lists every code and its
    params.

    Args:
        attacker: The attacking [`Creature`][osrlib.core.creature.Creature], which a
            [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy. The function
            reads only its conditions.
        defender: The defending `Creature`, a `Character` or a `MonsterInstance`. The function
            doesn't read it.
        attack: The [`Attack`][osrlib.core.combat.Attack], or `None` for unarmed.
        context: The attack's context. The function reads only `distance_feet` and
            `fired_last_round`.
        ruleset: The ruleset in play. The function reads only `weapon_reload`.

    Returns:
        The reasons the attack isn't allowed, as
        [`Rejection`][osrlib.core.validation.Rejection] values. Each has a `code` and the
        `params` a message needs. The list is empty when the attack may go ahead.

    Examples:
        ```python
        from osrlib.core.combat import AttackContext, validate_attack
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.data import load_equipment, load_monsters

        spawn = RngStreams(master_seed=3).get(MONSTER_SPAWN_STREAM)
        catalog = load_monsters()
        goblin = spawn_monster(catalog.get("goblin"), id="goblin-1", stream=spawn)
        orc = spawn_monster(catalog.get("orc"), id="orc-1", stream=spawn)
        sword = load_equipment().get("sword")
        rules = Ruleset()

        assert validate_attack(goblin, orc, sword, AttackContext(distance_feet=5), ruleset=rules) == []

        far = validate_attack(goblin, orc, sword, AttackContext(distance_feet=30), ruleset=rules)
        assert far[0].code == "combat.attack.out_of_reach"
        assert far[0].params == {"attacker": "goblin-1", "distance_feet": 30}
        ```
    """
    rejections: list[Rejection] = []
    for condition in (*_CANNOT_ACT, Condition.WEAKENED):
        if has_condition(attacker, condition):
            rejections.append(
                Rejection(
                    code="combat.attack.attacker_incapacitated",
                    params={"attacker": _entity_id(attacker), "condition": condition.value},
                )
            )
            return rejections
    if has_condition(attacker, Condition.BLIND):
        rejections.append(Rejection(code="combat.attack.attacker_blind", params={"attacker": _entity_id(attacker)}))
        return rejections
    missile = _is_missile_use(attack, context)
    if missile:
        if _range_band_modifier(attack, context) is None:
            rejections.append(
                Rejection(
                    code="combat.attack.out_of_range",
                    params={"attacker": _entity_id(attacker), "distance_feet": context.distance_feet or 0},
                )
            )
        if ruleset.weapon_reload and WeaponQuality.RELOAD in _qualities(attack) and context.fired_last_round:
            rejections.append(
                Rejection(code="combat.attack.reload", params={"attacker": _entity_id(attacker)}),
            )
    elif context.distance_feet is not None and context.distance_feet > MELEE_REACH_FEET:
        rejections.append(
            Rejection(
                code="combat.attack.out_of_reach",
                params={"attacker": _entity_id(attacker), "distance_feet": context.distance_feet},
            )
        )
    return rejections


def _defender_descending_ac(defender: Any, context: AttackContext, *, missile: bool = False) -> int | None:
    ac = getattr(defender, "armour_class", None)
    if ac is None:
        return None
    # Shield AC is ignored against a retreating defender and attacks from behind.
    if context.defender_retreating or context.behind_target:
        inventory = getattr(defender, "inventory", None)
        shield = getattr(inventory, "shield", None) if inventory is not None else None
        if shield is not None and shield.template.ac_bonus is not None:
            ac += shield.template.ac_bonus
    if context.attacker_large:
        params = _class_ability_params(defender, "defensive_bonus")
        if params is not None:
            ac -= int(params.get("ac_bonus", 0))
    # AC-set modifiers (*shield*): the effective AC is the better of the defender's
    # AC and the set value, never worse, which for descending AC is the minimum.
    set_kind = "ac_set_vs_missile" if missile else "ac_set"
    for value in modifier_values(defender, set_kind):
        ac = min(ac, value)
    # AC-bonus modifiers (the potion of invulnerability's ±2) improve or worsen
    # descending AC directly. Equipped armour and shields are already in the
    # character's `armour_class` property.
    ac -= modifier_total(defender, "ac_bonus")
    # An ally's aura (the 5'-radius protection ring), which the caller asserts.
    ac -= context.defender_ally_ac_bonus
    return ac


def attack_roll(
    attacker: Combatant,
    defender: Combatant,
    attack: Attack,
    *,
    context: AttackContext,
    ruleset: Ruleset,
    stream: RngStream,
) -> AttackRollResult:
    """Roll an attack: 1d20 plus modifiers against the defender's armour class.

    This is the first step of [`resolve_attack`][osrlib.core.combat.resolve_attack], which
    is what you usually call: it rolls the attack, checks the defender's immunities, rolls
    the damage, and applies it. Call `attack_roll` on its own when you want only whether
    the attack hits, as for an attack whose effect isn't damage. It changes nothing.

    The roll adds every modifier that applies:

    - In melee, the attacker's STR modifier. With a missile, its DEX modifier, the range
      modifier from the context's `distance_feet`, and the halfling's missile bonus.
    - A thief's back-stab bonus, when the context sets both `behind_target` and
      `target_unaware`.
    - +2 against a retreating defender.
    - A magic weapon's bonus.
    - Spell bonuses on the attacker, like *bless*, and penalties from wards on the
      defender, like *protection from evil*.
    - The context's `situational_modifier`.

    The function adjusts the defender's armour class too. Its shield doesn't count against
    an attack from behind or while it retreats, and an ally's bonus from the context does.

    The function hits two kinds of defender automatically, with no roll and no draw. One is
    a defender that's paralysed, asleep, or petrified and attacked in melee. The other is a monster
    whose template has no armour class, which means attacks against it need no roll, as for
    green slime and yellow mould.

    A natural 20 always hits and a natural 1 always misses. The total needed comes from
    the B/X attack table, or from `THAC0 − AC` with no limits under the
    `thac0_arithmetic` ruleset flag.

    Args:
        attacker: The attacking [`Combatant`][osrlib.core.creature.Combatant], which a
            [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy.
        defender: The defending `Combatant`, a `Character` or a `MonsterInstance`.
        attack: The [`Attack`][osrlib.core.combat.Attack], or `None` for unarmed.
        context: The attack's context.
        ruleset: The ruleset in play.
        stream: The stream to draw the d20 from, usually
            [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM] from your
            [`RngStreams`][osrlib.core.rng.RngStreams]. One draw for a rolled attack, and
            none for an automatic hit.

    Returns:
        The roll's outcome. Read `hit` to decide what happens next. `roll` is `None` on an
        automatic hit. `events` contains the
        [`AttackRolledEvent`][osrlib.core.events.AttackRolledEvent], with the code
        `combat.attack.hit`, `combat.attack.missed`, or `combat.attack.auto_hit`.

    Raises:
        ValueError: If the defender has no `armour_class` attribute, which means it isn't
            a combatant. The function draws nothing first.

    Examples:
        ```python
        from osrlib.core.combat import COMBAT_STREAM, AttackContext, attack_roll
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.data import load_equipment, load_monsters

        streams = RngStreams(master_seed=5)
        spawn = streams.get(MONSTER_SPAWN_STREAM)
        catalog = load_monsters()
        goblin = spawn_monster(catalog.get("goblin"), id="goblin-1", stream=spawn)
        orc = spawn_monster(catalog.get("orc"), id="orc-1", stream=spawn)

        rolled = attack_roll(
            goblin,
            orc,
            load_equipment().get("sword"),
            context=AttackContext(situational_modifier=-2),  # the orc has cover
            ruleset=Ruleset(),
            stream=streams.get(COMBAT_STREAM),
        )
        assert (rolled.roll, rolled.modifier, rolled.total) == (16, -2, 14)
        assert rolled.required == 13
        assert rolled.hit
        assert rolled.natural is None  # no natural 1 or 20 overrode the arithmetic
        ```
    """
    attacker_id, defender_id = _entity_id(attacker), _entity_id(defender)
    name = _attack_name(attack)
    missile = _is_missile_use(attack, context) or (isinstance(attack, MonsterAttack) and context.monster_missile)
    helpless = not missile and any(has_condition(defender, condition) for condition in _HELPLESS)
    if helpless or getattr(defender, "armour_class", 0) is None:
        event = AttackRolledEvent(
            code="combat.attack.auto_hit",
            attacker_id=attacker_id,
            defender_id=defender_id,
            attack_name=name,
        )
        return AttackRollResult(hit=True, auto=True, events=(event,))

    modifier = context.situational_modifier
    if missile:
        modifier += getattr(attacker, "missile_modifier", 0)
        band = _range_band_modifier(attack, context)
        modifier += band if band is not None else 0
        halfling = _class_ability_params(attacker, "missile_attack_bonus")
        if halfling is not None:
            modifier += int(halfling.get("bonus", 0))
    else:
        modifier += melee_modifier_for(attacker)
    if context.behind_target and context.target_unaware:
        back_stab = _class_ability_params(attacker, "back_stab")
        if back_stab is not None:
            modifier += int(back_stab.get("attack_bonus", 0))
    if context.defender_retreating:
        modifier += 2
    # An enchanted arm's bonus (versus-clauses swapping in their alternate).
    modifier += _magic_weapon_bonus(attack, defender, damage=False)
    # Spell stat modifiers: the attacker's bonuses (*bless* and *blight*) and
    # the defender's ward penalty on attackers of another alignment (*protection
    # from evil*), each under the cumulative rule. Then the defender's
    # equipped-item penalties (the Displacer Cloak's melee-only −2), which sit
    # outside the caps.
    modifier += modifier_total(attacker, "attack_bonus")
    modifier += modifier_total(
        defender, "attack_penalty_of_attackers", versus_differs=alignments_differ(attacker, defender), melee=not missile
    )
    modifier += _item_modifier_total(defender, "attack_penalty_of_attackers", melee=not missile)

    ac = _defender_descending_ac(defender, context, missile=missile)
    if ac is None:
        raise ValueError(f"{_entity_id(defender)} has no armour class to attack against")
    thac0 = attacker.thac0
    girdle = _item_effect_params(attacker, "giant_strength")
    if girdle is not None:
        # The girdle's wearer attacks as an 8 HD monster, unless the character's
        # probabilities are already better.
        from osrlib.core.tables import thac0_for_hd

        thac0 = min(thac0, thac0_for_hd(int(girdle["attack_as_hd"]))[0])
    required = (thac0 - ac) if ruleset.thac0_arithmetic else to_hit_ac(thac0, ac)
    natural = stream.randbelow(20) + 1
    total = natural + modifier
    if natural == 20:
        hit = True
    elif natural == 1:
        hit = False
    else:
        hit = total >= required
    natural_override = natural if natural in (1, 20) and (total >= required) != hit else None
    event = AttackRolledEvent(
        code="combat.attack.hit" if hit else "combat.attack.missed",
        attacker_id=attacker_id,
        defender_id=defender_id,
        attack_name=name,
        roll=natural,
        modifier=modifier,
        total=total,
        required=required,
        defender_ac=ac,
        natural=natural_override,
    )
    return AttackRollResult(
        hit=hit,
        roll=natural,
        modifier=modifier,
        total=total,
        required=required,
        natural=natural_override,
        events=(event,),
    )


def check_immunity(
    defender: Creature, source: DamageSource, *, ruleset: Ruleset, attacker: Creature | None = None
) -> bool:
    """Return True when the defender is immune to the damage source, so no damage is rolled.

    osrlib calls this check the immunity gate.
    [`resolve_attack`][osrlib.core.combat.resolve_attack] runs it on every hit, and
    [`resolve_breath`][osrlib.core.combat.resolve_breath] runs it for every target. Call it
    yourself to ask whether a weapon can hurt a monster before the party spends rounds
    finding out. Build the `source` with
    [`damage_source_for`][osrlib.core.combat.damage_source_for].
    [`deal_damage`][osrlib.core.combat.deal_damage] doesn't run this check.

    The defender is immune in these cases:

    - It's a monster that only some materials can harm, and the source has none of
      them. A steel sword can't harm a werewolf, and a silver dagger can.
    - The source has the `holy` key and the defender isn't undead. Holy water harms only the
      undead, and gets through any material defense an undead monster has.
    - It's a monster that uses fire, and the source is burning oil.
    - It's a monster immune to the source's element. A monster immune to every form of
      the element takes no damage from it. A monster immune only to the ordinary form
      takes damage from a magical source of the element.
    - It's under *protection from normal missiles*, and the source is a small nonmagical
      missile. An arrow or a thrown flask is blocked, and a hurled boulder or a magic
      arrow isn't.

    Under the `hd5_counts_as_magical` ruleset flag, a monster attacker with 5 or more Hit
    Dice left after any energy drain, or one that only silver or magic can harm itself, can
    harm a monster that only silver or magic can harm.

    Args:
        defender: The defending [`Creature`][osrlib.core.creature.Creature], which a
            [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy.
        source: The damage source.
        ruleset: The ruleset in play.
        attacker: The attacking `Creature`, a `Character` or a `MonsterInstance`. The function
            reads it only for the `hd5_counts_as_magical` rule, and without it that rule
            doesn't apply.

    Returns:
        True when the defender is immune and no damage should be rolled.

    Examples:
        ```python
        from osrlib.core.combat import AttackContext, check_immunity, damage_source_for
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.data import load_equipment, load_monsters

        spawn = RngStreams(master_seed=3).get(MONSTER_SPAWN_STREAM)
        catalog = load_monsters()
        goblin = spawn_monster(catalog.get("goblin"), id="goblin-1", stream=spawn)
        werewolf = spawn_monster(catalog.get("werewolf"), id="werewolf-1", stream=spawn)

        equipment = load_equipment()
        rules = Ruleset()
        steel = damage_source_for(goblin, equipment.get("sword"), AttackContext())
        silver = damage_source_for(goblin, equipment.get("silver_dagger"), AttackContext())

        assert check_immunity(werewolf, steel, ruleset=rules)  # the werewolf shrugs off steel
        assert not check_immunity(werewolf, silver, ruleset=rules)
        ```
    """
    if source.missile and not source.magical and has_modifier(defender, "missile_immunity_nonmagical"):
        return True
    template = getattr(defender, "template", None)
    categories = template.categories if template is not None else ()
    if "holy" in source.keys and "undead" not in categories:
        return True
    if template is None:
        return False
    defenses = template.defenses
    if source.element is not None:
        if source.kind == "splash" and source.element == "fire" and not source.magical:
            if template.ability("uses_fire") is not None:
                return True
        energy = defenses.energy.get(Element(source.element)) if source.element in Element else None
        if energy is not None and (energy.immunity == "all" or not source.magical):
            return True
    gate = defenses.harmed_only_by
    if not gate:
        return False
    gate_values = {key.value for key in gate}
    if "holy" in source.keys and "undead" in categories:
        return False
    if any(key in gate_values for key in source.keys):
        return False
    if source.element is not None and source.element in gate_values:
        return False
    if ruleset.hd5_counts_as_magical and attacker is not None and gate_values <= {"silver", "magic"}:
        attacker_template = getattr(attacker, "template", None)
        if attacker_template is not None:
            if getattr(attacker, "hit_dice_count", attacker_template.hit_dice.count) >= 5:
                return False
            attacker_gate = {key.value for key in attacker_template.defenses.harmed_only_by}
            if attacker_gate and attacker_gate <= {"silver", "magic"}:
                return False
    return True


def damage_roll(
    attacker: Combatant,
    attack: Attack,
    *,
    context: AttackContext,
    ruleset: Ruleset,
    stream: RngStream,
    defender: Creature | None = None,
) -> RollResult:
    """Roll an attack's damage: the dice, the bonuses, and the doublings, at least 1.

    [`resolve_attack`][osrlib.core.combat.resolve_attack] calls this after a hit gets past
    the defender's immunities, and passes the total to
    [`deal_damage`][osrlib.core.combat.deal_damage]. Call it yourself to preview a
    weapon's damage, or to apply the damage some other way. It only rolls: the defender
    loses nothing here, and `deal_damage` applies the defender's reductions later.

    The total adds these, in this order:

    1. The attack's dice. A monster attack with a fixed amount rolls no dice.
    2. The attacker's STR modifier, on a melee attack that isn't a monster attack.
    3. A magic weapon's damage bonus. When `defender` is one of the monsters the weapon
       is made against, the bonus for those monsters replaces it.
    4. Spell bonuses, like *bless*, and the extra die of *striking* on a weapon attack.
    5. The doublings: a braced weapon against a charge, a charging attack with a lance or
       other charge weapon, the multipliers of magic like a potion of giant strength or of
       growth, and a thief's back-stab.

    The total is at least 1, except for a monster attack that deals no hit point damage,
    like a wight's touch, which totals 0.

    With the `variable_weapon_damage` ruleset flag off, every weapon and every gear item
    with combat stats deals 1d6 instead of its listed dice. An unarmed attack still deals
    1d2, and a monster attack still deals its listed damage. A Girdle of Giant Strength
    doubles weapon damage with the flag on, and deals its listed 2d8 with the flag off.

    Args:
        attacker: The attacking [`Combatant`][osrlib.core.creature.Combatant], which a
            [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy.
        attack: The [`Attack`][osrlib.core.combat.Attack], or `None` for unarmed.
        context: The attack's context. The doublings depend on its `braced`, `charging`,
            `behind_target`, and `target_unaware` fields, and a monster attack's fixed amount
            on its `fixed_damage_option`.
        ruleset: The ruleset in play.
        stream: The stream to draw the damage dice from, usually
            [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM].
        defender: The defending `Creature`, a `Character` or a `MonsterInstance`. The function
            reads it only for a magic weapon with a bonus against certain monsters.

    Returns:
        The damage roll. `rolls` contains each die, and `total` the damage to pass to
        `deal_damage`. `modifier` contains every flat amount added to the dice: steps 2 to 4,
        any modifier in the dice expression, and a monster attack's fixed amount. `multiplier`
        is the product of the step 5 doublings. So `total` is
        `(sum(rolls) + modifier) * multiplier`, raised to 1 when it would be lower, and 0 for
        an attack that deals no hit point damage.

    Raises:
        ValueError: If the attack has no combat stats to roll damage from: a gear item like
            a lantern, or a magic item with no base weapon, like Armour +1.

    Examples:
        ```python
        from osrlib.core.combat import COMBAT_STREAM, AttackContext, damage_roll
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.data import load_equipment, load_monsters

        streams = RngStreams(master_seed=3)
        spawn = streams.get(MONSTER_SPAWN_STREAM)
        goblin = spawn_monster(load_monsters().get("goblin"), id="goblin-1", stream=spawn)

        rolled = damage_roll(
            goblin,
            load_equipment().get("sword"),
            context=AttackContext(),
            ruleset=Ruleset(),
            stream=streams.get(COMBAT_STREAM),
        )
        assert rolled.rolls == (5,)  # one d8
        assert rolled.total == 5  # a goblin adds no strength modifier
        ```
    """
    rolls: tuple[int, ...] = ()
    girdle = _item_effect_params(attacker, "giant_strength")
    if isinstance(attack, MonsterAttack):
        if attack.fixed_damage_options:
            amount = attack.fixed_damage_options[context.fixed_damage_option]
        elif attack.fixed_damage is not None:
            amount = attack.fixed_damage
        elif attack.damage is not None:
            result = roll(attack.damage, stream)
            rolls, amount = result.rolls, result.total
        else:
            # An effect-only attack (the wight's touch) deals no hit point damage.
            # Its effect tags resolve separately.
            return RollResult(rolls=(), modifier=0, multiplier=1, total=0)
    elif attack is None:
        result = roll("1d2", stream)
        rolls, amount = result.rolls, result.total
    elif girdle is not None and not ruleset.variable_weapon_damage:
        # The girdle's printed 2d8 replaces the flat 1d6 with the flag off. It's
        # the one implemented item whose mechanics branch on a `Ruleset` flag.
        result = roll(str(girdle["flat_damage_dice"]), stream)
        rolls, amount = result.rolls, result.total
    else:
        facet = _facet(attack)
        if facet is None:
            raise ValueError("attack carries no combat facet to roll damage from")
        dice = facet.damage if ruleset.variable_weapon_damage else "1d6"
        result = roll(dice, stream)
        rolls, amount = result.rolls, result.total
    missile = _is_missile_use(attack, context)
    if not missile and not isinstance(attack, MonsterAttack):
        amount += melee_modifier_for(attacker)
    # An enchanted arm's damage bonus (the versus alternate against a match).
    amount += _magic_weapon_bonus(attack, defender, damage=True)
    # Spell stat modifiers join the sum before the doublings: *bless*'s flat bonus
    # on any attack, *striking*'s extra die on weapon attacks only, never on an
    # unarmed strike or a monster's natural attack.
    amount += modifier_total(attacker, "damage_bonus")
    if attack is not None and not isinstance(attack, MonsterAttack):
        striking = modifier_dice(attacker, "weapon_damage_dice_bonus")
        if striking is not None:
            bonus = roll(striking, stream)
            rolls = (*rolls, *bonus.rolls)
            amount += bonus.total
    # Every flat amount added to the dice so far, a fixed amount included, is the modifier.
    modifier = amount - sum(rolls)
    multiplier = 1
    qualities = _qualities(attack)
    if context.braced and WeaponQuality.BRACE in qualities:
        multiplier *= 2
    if context.charging and WeaponQuality.CHARGE in qualities:
        multiplier *= 2
    if not isinstance(attack, MonsterAttack):
        # Item damage multipliers apply after the flat bonuses, beside the quality
        # doublings: giant strength on weapon attacks, growth on melee attacks
        # (unarmed included), and the girdle's double under the variable-damage
        # default.
        if attack is not None:
            item_multiplier = modifier_total(attacker, "damage_multiplier")
            if item_multiplier > 1:
                multiplier *= item_multiplier
            if girdle is not None and ruleset.variable_weapon_damage:
                multiplier *= 2
        if not missile:
            melee_multiplier = modifier_total(attacker, "melee_damage_multiplier")
            if melee_multiplier > 1:
                multiplier *= melee_multiplier
    if context.behind_target and context.target_unaware:
        back_stab = _class_ability_params(attacker, "back_stab")
        if back_stab is not None:
            multiplier *= int(back_stab.get("damage_multiplier", 1))
    total = (sum(rolls) + modifier) * multiplier
    return RollResult(rolls=rolls, modifier=modifier, multiplier=multiplier, total=max(1, total))


def _item_effect_params(combatant: Any, effect_kind: str) -> dict[str, Any] | None:
    """Return the params of an equipped always-active item effect of `effect_kind`."""
    inventory = getattr(combatant, "inventory", None)
    if inventory is None:
        return None
    for instance in inventory.equipped_instances():
        if not isinstance(instance, MagicItemInstance):
            continue
        template = magic_item_template(instance)
        if template.always_active and template.effect is not None and template.effect.kind == effect_kind:
            return dict(template.effect.params)
    return None


def deal_damage(
    target: Combatant,
    amount: int,
    *,
    source: DamageSource,
    attacker_id: str | None = None,
    rolls: tuple[int, ...] = (),
    clock: GameClock | None = None,
    ruleset: Ruleset | None = None,
    stream: RngStream | None = None,
) -> list[Event]:
    """Apply damage to a creature: its reductions, its hit points, and its death.

    This changes the target. [`resolve_attack`][osrlib.core.combat.resolve_attack] calls
    it for you on a hit. Call it yourself for damage that isn't an attack, like a trap, a
    spell, or a fall, whose dice [`falling_damage`][osrlib.core.combat.falling_damage]
    rolls. Roll the amount first, with [`damage_roll`][osrlib.core.combat.damage_roll] or
    [`roll`][osrlib.core.dice.roll], and describe where it comes from with a
    [`DamageSource`][osrlib.core.combat.DamageSource]. This function draws no dice unless
    a destructive source kills a target that carries magic items.

    It doesn't check immunity. A monster that only silver or magic can harm takes the
    damage anyway, so call [`check_immunity`][osrlib.core.combat.check_immunity] first
    when the source is one its defenses should stop.

    It works in this order:

    1. The target's reductions that divide damage apply, like the wraith's half damage
       from silver and the mummy's half damage from everything. Each one leaves at least
       1.
    2. Reductions for the source's element apply, like *resist fire*. Each takes 1 point
       off each die in `rolls` and leaves every die at least 1, so damage with no `rolls`
       isn't reduced.
    3. The hit points come off, stopping at 0. When you pass a `clock`, the function records
       the round in a [`MonsterInstance`][osrlib.core.monsters.MonsterInstance]'s
       `last_damaged_round`. A troll's regeneration waits after that round, and a dead
       troll gets back up 2d6 rounds after it.
    4. Damage of an element that stops a target's regeneration also adds to the target's
       `nonregen_damage`, up to its maximum hit points. Fire and acid stop a troll's
       regeneration and the regeneration of a character who wears a ring of regeneration.
       For any damage to a target that regenerates, the function first cuts
       `nonregen_damage` to the damage the target had before this hit, because healing
       from another source, like a potion, heals that damage too.
    5. At 0 hit points the target dies, through [`kill`][osrlib.core.effects.kill]. A
       monster that regenerates after death dies for good only when its
       `nonregen_damage` reaches its maximum hit points. Then a destructive source
       destroys what the target carried, through
       [`destroy_equipment`][osrlib.core.combat.destroy_equipment].

    A target that's already dead doesn't die again. When the damage makes a dead troll's
    death permanent, a [`DeathEvent`][osrlib.core.events.DeathEvent] with the code
    `combat.death.permanent` reports it.

    Args:
        target: The [`Combatant`][osrlib.core.creature.Combatant] taking the damage, which a
            [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy. The function
            changes it in place. Its saving throws are what its magic items save against when a
            destructive source kills it.
        amount: The damage rolled, before reductions.
        source: Where the damage comes from. Which reductions apply, whether the damage counts
            against regeneration, and whether a kill destroys equipment all depend on it.
        attacker_id: The attacker's entity id, for the event. `None` when there's no attacker.
        rolls: The damage dice as rolled. They appear in the event and set how much a
            reduction per die can take.
        clock: The game clock. When you pass it, the function sets a monster's `last_damaged_round`.
        ruleset: The ruleset in play. Whether magic items save when a destructive source
            kills the target depends on it.
        stream: The stream the magic items' saves draw from, which is the stream of
            whatever is resolving the damage. It's needed only when a destructive source can
            kill a target that carries magic items.

    Returns:
        The events in order, ready to append to your log: a
        [`DamageDealtEvent`][osrlib.core.events.DamageDealtEvent] and a
        [`HitPointsReportedEvent`][osrlib.core.events.HitPointsReportedEvent], then the
        events of [`kill`][osrlib.core.effects.kill] when the damage kills the target, and
        last an [`EquipmentDestroyedEvent`][osrlib.core.events.EquipmentDestroyedEvent]
        when a destructive source killed a target that carried something.

    Examples:
        ```python
        from osrlib.core.combat import DamageSource, deal_damage
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        spawn = RngStreams(master_seed=3).get(MONSTER_SPAWN_STREAM)
        goblin = spawn_monster(load_monsters().get("goblin"), id="goblin-1", stream=spawn)
        assert goblin.current_hp == 5

        events = deal_damage(goblin, 2, source=DamageSource(kind="falling"), attacker_id=None)
        assert [event.code for event in events] == ["combat.damage.dealt", "combat.state.hit_points"]
        assert goblin.current_hp == 3

        events = deal_damage(goblin, 99, source=DamageSource(kind="falling"))
        assert "combat.death.died" in [event.code for event in events]
        assert goblin.current_hp == 0  # hit points floor at 0, they never go negative
        ```
    """
    template = getattr(target, "template", None)
    if template is not None:
        for reduction in template.defenses.reductions:
            keys = {key.value for key in reduction.keys}
            if not keys or any(key in keys for key in source.keys) or (source.element in keys):
                amount = max(1, amount // reduction.divisor)
    # Element-scoped per-die reduction (*resist cold*, *resist fire*, the
    # fire-resistance ring and potion): 1 point per damage die rolled, each die
    # inflicting a minimum of 1. A source that rolled no dice (fixed damage, a
    # dragon's current-hp breath) has no dice to reduce, because the rule is per
    # die rolled. Item bonuses join outside the spell caps.
    if source.element is not None and rolls:
        per_die = modifier_total(target, "damage_reduction_per_die", element=source.element)
        per_die += _item_modifier_total(target, "damage_reduction_per_die", element=source.element)
        if per_die > 0:
            amount = max(min(amount, len(rolls)), amount - per_die * len(rolls))
    events: list[Event] = []
    target_id = _entity_id(target)
    already_dead = has_condition(target, Condition.DEAD)
    damage_before = target.max_hp - target.current_hp
    target.current_hp = max(0, target.current_hp - amount)
    if clock is not None and hasattr(target, "last_damaged_round"):
        # Only a monster instance has the round stamp. The type checker can't see that
        # this `hasattr` proves the target is one, so the use casts to that type.
        cast(MonsterInstance, target).last_damaged_round = clock.rounds
    # A troll regenerates by its template's ability, and a character by a worn ring of
    # regeneration. Both record the damage the regeneration can't heal in `nonregen_damage`.
    regeneration = _monster_ability_params(target, "regeneration") or _item_effect_params(target, "regeneration")
    blocked = False
    newly_permanent = False
    if regeneration is not None:
        # The type checker can't see that a regeneration ability or ring proves the target
        # has the `nonregen_damage` field, so the uses cast to the two types that have it.
        regenerator = cast("Character | MonsterInstance", target)
        # Healing from another source heals the blocked damage too, so the record never
        # counts more than the damage the target had before this hit.
        before = min(regenerator.nonregen_damage, damage_before)
        regenerator.nonregen_damage = before
        blocked_by = tuple(str(element) for element in regeneration.get("blocked_by", ()))
        blocked = source.element is not None and source.element in blocked_by
        if blocked:
            regenerator.nonregen_damage = min(regenerator.max_hp, before + amount)
            newly_permanent = before < regenerator.max_hp <= regenerator.nonregen_damage
    keys = source.keys if source.element is None or source.element in source.keys else (*source.keys, source.element)
    events.append(
        DamageDealtEvent(
            target_id=target_id,
            attacker_id=attacker_id,
            amount=amount,
            rolls=rolls,
            keys=keys,
            non_regenerable=blocked,
        )
    )
    events.append(HitPointsReportedEvent(target_id=target_id, current_hp=target.current_hp, max_hp=target.max_hp))
    if target.current_hp == 0 and not already_dead:
        # "Permanent" marks a regenerator that can revive: the troll is permanently
        # dead only when the non-regenerable ledger alone reaches max HP.
        permanent = (
            regeneration is not None
            and regeneration.get("revive") is not None
            and cast("Character | MonsterInstance", target).nonregen_damage >= target.max_hp
        )
        events.extend(kill(target, permanent=permanent))
        if source.destructive:
            events.extend(destroy_equipment(target, source=source, ruleset=ruleset, stream=stream))
    elif already_dead and newly_permanent:
        events.append(DeathEvent(code="combat.death.permanent", target_id=target_id))
    return events


_DEATH_SAVE_CATEGORIES = {"breath": SaveCategory.BREATH, "spell": SaveCategory.SPELLS, "device": SaveCategory.WANDS}


def destroy_equipment(
    target: Combatant,
    *,
    source: DamageSource | None = None,
    ruleset: Ruleset | None = None,
    stream: RngStream | None = None,
) -> list[Event]:
    """Destroy everything a victim carries, as a breath weapon or *disintegrate* does when it kills.

    [`deal_damage`][osrlib.core.combat.deal_damage] calls this when a destructive source
    kills its target, so you rarely call it yourself.
    [`cast_spell`][osrlib.core.spells.cast_spell] calls it directly for *disintegrate*,
    because the spell destroys the victim and what it carried without dealing damage.

    Under the `magic_item_death_save` ruleset flag, which is on by default, each magic item
    the victim carries gets a saving throw. The function rolls 1d20 for the item, which
    saves on the victim's target number in the category that matches the source: versus
    breath for a breath weapon, versus spells for a spell, versus wands for a device, and
    versus death for anything else. The roll adds the item's best bonus, the highest of its
    attack, damage, and armour class bonuses, so a cursed item saves at its penalty. The
    items that save stay in the inventory's `items` list, no longer equipped. In a
    [`GameSession`][osrlib.crawl.session.GameSession], the session then moves a dead party
    member's saved items to a drop pile on the party's cell, in the same command, where the
    party can pick them up. The rolls have no events, and the one event reports the outcome.

    Args:
        target: The victim, a [`Combatant`][osrlib.core.creature.Combatant], which a
            [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy. The
            function empties its inventory except for the magic items that saved, and leaves
            nothing wielded, worn, or on its fingers.
        source: The destructive damage source, whose `kind` picks the saving throw
            category. With `None`, the items save versus death.
        ruleset: The ruleset in play. With `None`, the function skips the saves and destroys
            every item.
        stream: The stream the items' saves draw from, one draw for each magic item.

    Returns:
        A list with one [`EquipmentDestroyedEvent`][osrlib.core.events.EquipmentDestroyedEvent],
        which lists the destroyed items and the saved ones. An empty list when the victim
        carries nothing.

    Raises:
        ValueError: If the saves are on, the victim carries a magic item, and you passed no
            `stream`. The function changes nothing first.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.combat import COMBAT_STREAM, DamageSource, destroy_equipment
        from osrlib.core.items import MagicItemInstance
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset

        rules = Ruleset()
        streams = RngStreams(master_seed=3)
        hild = create_character(
            name="Hild",
            class_id="fighter",
            alignment=Alignment.LAWFUL,
            ruleset=rules,
            stream=streams.get(CHARACTER_CREATION_STREAM),
            purchases=[("torch", 1)],
        ).character
        hild.inventory.items.append(MagicItemInstance(instance_id="item-1", template_id="sword_plus_1"))

        breath = DamageSource(element="fire", kind="breath", destructive=True)
        events = destroy_equipment(hild, source=breath, ruleset=rules, stream=streams.get(COMBAT_STREAM))
        assert events[0].item_names == ("Torches (6)",)
        assert events[0].saved_items == ("item-1",)  # the Sword +1 made its save versus breath
        assert [item.instance_id for item in hild.inventory.items] == ["item-1"]
        ```
    """
    inventory = getattr(target, "inventory", None)
    if inventory is None:
        return []
    instances = inventory.all_instances()
    if not instances:
        return []
    save_enabled = ruleset is not None and ruleset.magic_item_death_save
    category = _DEATH_SAVE_CATEGORIES.get(source.kind if source is not None else "", SaveCategory.DEATH)
    destroyed: list[str] = []
    saved: list[MagicItemInstance] = []
    for instance in instances:
        if isinstance(instance, MagicItemInstance):
            template = magic_item_template(instance)
            if save_enabled:
                if stream is None:
                    raise ValueError("the magic-item death save needs a stream; pass the resolving subsystem's")
                required = getattr(target.saves, category.value)
                # The best of the item's actual bonuses: unset bonuses are 0 and
                # must not mask a cursed item's penalty (−2/−2/unset saves at −2).
                bonuses = [
                    bonus for bonus in (template.attack_bonus, template.damage_bonus, template.ac_bonus) if bonus
                ]
                best_bonus = max(bonuses) if bonuses else 0
                if stream.randbelow(20) + 1 + best_bonus >= required:
                    saved.append(instance)
                    continue
            destroyed.append(template.name)
        else:
            destroyed.append(instance.template.name)
    inventory.items = list(saved)
    inventory.wielded = []
    inventory.worn_armour = None
    inventory.shield = None
    inventory.rings = []
    return [
        EquipmentDestroyedEvent(
            target_id=_entity_id(target),
            item_names=tuple(destroyed),
            saved_items=tuple(instance.instance_id for instance in saved),
        )
    ]


def resolve_attack(
    attacker: Combatant,
    defender: Combatant,
    attack: Attack,
    *,
    context: AttackContext,
    ruleset: Ruleset,
    stream: RngStream,
    clock: GameClock | None = None,
) -> AttackResult:
    """Resolve one attack from start to finish: the roll, the immunity check, and the damage.

    This is the function you usually need. It rolls the attack, checks whether the
    defender is immune, rolls the damage, and applies it. It changes the defender, so the
    hit points are already gone when you read the result. To check first whether the attack
    is allowed, call [`validate_attack`][osrlib.core.combat.validate_attack], which draws
    nothing and changes nothing.

    Other attacks have separate functions:

    - Thrown oil or holy water:
      [`resolve_splash_attack`][osrlib.core.combat.resolve_splash_attack], which also
      deals the second round of damage.
    - A breath weapon: [`resolve_breath`][osrlib.core.combat.resolve_breath].
    - A petrifying gaze: [`resolve_gaze`][osrlib.core.combat.resolve_gaze].
    - The level drain of an undead creature's touch: call
      [`resolve_energy_drain`][osrlib.core.combat.resolve_energy_drain] after this
      function reports a hit.

    The steps always run in this order. First the attack roll, through
    [`attack_roll`][osrlib.core.combat.attack_roll]. On a hit,
    [`check_immunity`][osrlib.core.combat.check_immunity]: when the defender is immune, the
    function rolls no damage, and a
    [`DamageAbsorbedEvent`][osrlib.core.events.DamageAbsorbedEvent] reports the hit. Otherwise
    [`damage_roll`][osrlib.core.combat.damage_roll] rolls the damage and [`deal_damage`][osrlib.core.combat.deal_damage]
    applies it.

    Three hits end early:

    - A sleeping defender hit in melee with a bladed weapon dies at once, with no damage
      rolled. The immunity check still comes first.
    - An oil flask thrown unlit does nothing and reports 0 damage. To have it burn on the
      ground instead, attach the effect from
      [`burning_oil_pool_definition`][osrlib.core.combat.burning_oil_pool_definition].
    - A monster attack that deals no hit point damage, like a wight's touch, reports 0
      damage and applies none.

    Args:
        attacker: The attacking [`Combatant`][osrlib.core.creature.Combatant], which a
            [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy.
        defender: The defending `Combatant`, a `Character` or a `MonsterInstance`. The
            function changes it in place when damage lands.
        attack: The [`Attack`][osrlib.core.combat.Attack], or `None` for unarmed.
        context: The attack's context. `AttackContext()` is the plain melee case.
        ruleset: The ruleset in play.
        stream: The stream every draw comes from, usually
            [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM]. The attack roll takes one
            draw, and none on an automatic hit. A hit that deals damage then takes one draw
            for each damage die, and more when the attacker is under *striking*.
        clock: The game clock. When you pass it, the function sets a monster defender's
            `last_damaged_round` on a damaging hit, which delays a troll's regeneration.

    Returns:
        The attack's outcome. `attack_roll` has the roll. `absorbed` is true when the
        defender was immune. `damage` is the damage rolled, before the defender's
        reductions, or `None` when none was rolled. `events` has every event the attack
        caused, in order. Its [`DamageDealtEvent`][osrlib.core.events.DamageDealtEvent] has
        the hit points the defender lost, and the
        [`HitPointsReportedEvent`][osrlib.core.events.HitPointsReportedEvent] after it has
        the hit points the defender has left. When the defender dies, the events of
        [`kill`][osrlib.core.effects.kill] come next.

    Examples:
        A 1st-level fighter swings a sword at a goblin. Every draw comes from a
        named, seeded stream, so the same seed always replays the same fight:

        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.combat import COMBAT_STREAM, AttackContext, resolve_attack
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.data import load_equipment, load_monsters

        rules = Ruleset()
        streams = RngStreams(master_seed=7)
        hild = create_character(
            name="Hild",
            class_id="fighter",
            alignment=Alignment.LAWFUL,
            ruleset=rules,
            stream=streams.get(CHARACTER_CREATION_STREAM),
        ).character
        spawn = streams.get(MONSTER_SPAWN_STREAM)
        goblin = spawn_monster(load_monsters().get("goblin"), id="goblin-1", stream=spawn)
        sword = load_equipment().get("sword")

        combat = streams.get(COMBAT_STREAM)
        result = resolve_attack(hild, goblin, sword, context=AttackContext(), ruleset=rules, stream=combat)
        assert result.events[0].code in ("combat.attack.hit", "combat.attack.missed")
        assert result.attack_roll.hit  # seed 7 hits: 12 rolled + 1 = 13 against a required 13
        assert result.damage == 9  # same seed, same damage roll
        assert goblin.current_hp == 0
        assert "combat.death.died" in [event.code for event in result.events]
        ```
    """
    rolled = attack_roll(attacker, defender, attack, context=context, ruleset=ruleset, stream=stream)
    events = list(rolled.events)
    if not rolled.hit:
        return AttackResult(attack_roll=rolled, events=tuple(events))
    if isinstance(attack, GearTemplate) and attack.id == "oil_flask" and not context.lit:
        # Unlit oil deals no damage. The caller may build a pool instead.
        return AttackResult(attack_roll=rolled, damage=0, events=tuple(events))
    source = damage_source_for(attacker, attack, context)
    if check_immunity(defender, source, ruleset=ruleset, attacker=attacker):
        events.append(
            DamageAbsorbedEvent(target_id=_entity_id(defender), attacker_id=_entity_id(attacker), keys=source.keys)
        )
        return AttackResult(attack_roll=rolled, absorbed=True, events=tuple(events))
    if has_condition(defender, Condition.ASLEEP) and _is_bladed(attack) and not _is_missile_use(attack, context):
        # The sleeping condition's dies-to-a-blade hook: "A single attack with a
        # bladed weapon can kill", so the melee hit kills outright with no damage
        # roll. The immunity gate above still applies first.
        events.extend(kill(defender))
        return AttackResult(attack_roll=rolled, events=tuple(events))
    damage = damage_roll(attacker, attack, context=context, ruleset=ruleset, stream=stream, defender=defender)
    if damage.total > 0:
        events.extend(
            deal_damage(
                defender,
                damage.total,
                source=source,
                attacker_id=_entity_id(attacker),
                rolls=damage.rolls,
                clock=clock,
                ruleset=ruleset,
                stream=stream,
            )
        )
    return AttackResult(attack_roll=rolled, damage=damage.total, events=tuple(events))


def splash_douse_definition(attack: Attack, source: DamageSource) -> EffectDefinition:
    """Build the effect that deals a splash weapon's second round of damage.

    The rules say oil and holy water deal their damage "for two rounds". osrlib reads that
    as two hits of damage: the first when the flask hits, and the second, with the same
    dice, when this one-round effect expires. osrlib calls this effect the douse.
    [`resolve_splash_attack`][osrlib.core.combat.resolve_splash_attack] builds and attaches
    it for you on a hit that deals damage. Call this function yourself only to attach the
    douse through your own [`EffectsLedger`][osrlib.core.effects.EffectsLedger]. Build the
    `source` with [`damage_source_for`][osrlib.core.combat.damage_source_for] from the same
    attack and context, so the second hit has the same keys and element as the first.

    Args:
        attack: The splash item. The ledger rolls its combat stats' damage dice again for the
            second hit.
        source: The damage source of the first hit. The second hit has the same keys and
            element.

    Returns:
        A one-round [`EffectDefinition`][osrlib.core.effects.EffectDefinition] whose expiry
        deals the second hit. Attach it with
        [`EffectsLedger.attach`][osrlib.core.effects.EffectsLedger.attach], and the second
        hit lands when [`EffectsLedger.advance`][osrlib.core.effects.EffectsLedger.advance]
        reaches the next round.

    Raises:
        ValueError: If the attack has no combat stats, so there are no dice for the second
            hit.

    Examples:
        ```python
        from osrlib.core.combat import AttackContext, damage_source_for, splash_douse_definition
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_equipment, load_monsters

        spawn = RngStreams(master_seed=3).get(MONSTER_SPAWN_STREAM)
        goblin = spawn_monster(load_monsters().get("goblin"), id="goblin-1", stream=spawn)

        oil = load_equipment().get("oil_flask")
        context = AttackContext(lit=True)
        definition = splash_douse_definition(oil, damage_source_for(goblin, oil, context))
        assert definition.kind == "splash_douse"
        assert definition.duration_amount == 1
        assert definition.params == {"dice": "1d8", "keys": (), "element": "fire"}
        ```
    """
    facet = _facet(attack)
    if facet is None:
        raise ValueError("splash attack carries no combat facet")
    params: dict[str, int | str | bool | tuple[int | str, ...]] = {"dice": facet.damage, "keys": source.keys}
    if source.element is not None:
        params["element"] = source.element
    return EffectDefinition(
        kind="splash_douse",
        duration_unit=TimeUnit.ROUND,
        duration_amount=1,
        expiry="splash_damage",
        params=params,
    )


def burning_oil_pool_definition() -> EffectDefinition:
    """Build the effect for a pool of burning oil, which burns in one place for one turn.

    A flask of oil thrown unlit does no damage. Instead it spills into a pool, and someone
    can set the pool alight. A lit pool burns for one turn, which is ten minutes of game
    time, and deals 1d8 fire damage to each creature that passes through it. osrlib doesn't
    track where creatures walk, so you decide who passes through and apply the damage with
    [`deal_damage`][osrlib.core.combat.deal_damage]. The effect only marks the burning pool
    and when it goes out.

    The function takes no arguments because the pool is the same every time: 1d8 fire
    damage in a 3-foot radius for one turn. Attach the effect to a location string with
    [`EffectsLedger.attach`][osrlib.core.effects.EffectsLedger.attach].

    Returns:
        The one-turn [`EffectDefinition`][osrlib.core.effects.EffectDefinition], whose
        `params` contain the dice, the element, and the radius in feet.

    Examples:
        ```python
        from osrlib.core.combat import burning_oil_pool_definition

        pool = burning_oil_pool_definition()
        assert pool.kind == "burning_oil_pool"
        assert pool.duration_unit == "turn"
        assert pool.params == {"dice": "1d8", "element": "fire", "radius_feet": 3}
        ```
    """
    return EffectDefinition(
        kind="burning_oil_pool",
        duration_unit=TimeUnit.TURN,
        duration_amount=1,
        params={"dice": "1d8", "element": "fire", "radius_feet": 3},
    )


def resolve_splash_attack(
    attacker: Combatant,
    defender: Combatant,
    attack: GearTemplate,
    *,
    context: AttackContext,
    ruleset: Ruleset,
    stream: RngStream,
    ledger: EffectsLedger,
    clock: GameClock,
    allocator: Any,
    registry: dict[str, Any],
) -> AttackResult:
    """Resolve a thrown flask of holy water or oil: the attack, its first damage, and its second.

    Use this instead of [`resolve_attack`][osrlib.core.combat.resolve_attack] for holy
    water and oil, because they damage the target twice: once on the hit, and once more at
    the next round. This function runs `resolve_attack`, and on a hit that deals damage it
    attaches the effect from
    [`splash_douse_definition`][osrlib.core.combat.splash_douse_definition], which deals the
    second damage when it expires. Attaching it is why this function needs an effects
    ledger, a clock, an id allocator, and a registry.

    Holy water thrown at a living target, and burning oil thrown at a monster that uses
    fire, hit and do nothing. They aren't rejected, because a rejection costs nothing, and
    the player could use it to test whether a target is undead or uses fire.

    Args:
        attacker: The throwing [`Combatant`][osrlib.core.creature.Combatant], which a
            [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy.
        defender: The target, a `Combatant`, which a `Character` and a `MonsterInstance`
            both satisfy. The function changes it in place when damage lands.
        attack: The holy water or oil flask, as a
            [`GearTemplate`][osrlib.core.items.GearTemplate] from
            [`load_equipment`][osrlib.data.load_equipment].
        context: The attack's context. Oil does nothing unless `lit` is true.
        ruleset: The ruleset in play.
        stream: The stream every draw comes from, usually
            [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM].
        ledger: The [`EffectsLedger`][osrlib.core.effects.EffectsLedger] to attach the
            second damage to. It lands when you advance this ledger to the next round with
            [`EffectsLedger.advance`][osrlib.core.effects.EffectsLedger.advance].
        clock: The game clock. Its current round sets when the second damage lands.
        allocator: The [`IdAllocator`][osrlib.core.monsters.IdAllocator] that assigns the
            effect's id.
        registry: Your creatures by entity id, so the ledger can find the target again.

    Returns:
        The attack's outcome. When the second damage was attached, the last event is its
        [`EffectAttachedEvent`][osrlib.core.events.EffectAttachedEvent].

    Examples:
        ```python
        from osrlib.core.clock import GameClock
        from osrlib.core.combat import COMBAT_STREAM, AttackContext, resolve_splash_attack
        from osrlib.core.effects import EffectsLedger
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.data import load_equipment, load_monsters

        streams = RngStreams(master_seed=5)
        spawn = streams.get(MONSTER_SPAWN_STREAM)
        catalog = load_monsters()
        goblin = spawn_monster(catalog.get("goblin"), id="goblin-1", stream=spawn)
        ogre = spawn_monster(catalog.get("ogre"), id="ogre-1", stream=spawn)

        result = resolve_splash_attack(
            goblin,
            ogre,
            load_equipment().get("oil_flask"),
            context=AttackContext(lit=True, distance_feet=10),
            ruleset=Ruleset(),
            stream=streams.get(COMBAT_STREAM),
            ledger=EffectsLedger(),
            clock=GameClock(),
            allocator=IdAllocator(),
            registry={"goblin-1": goblin, "ogre-1": ogre},
        )
        assert result.attack_roll.hit
        assert result.damage == 2
        assert ogre.current_hp == 15  # down from 17
        assert result.events[-1].code == "effects.effect.attached"  # the douse, due next round
        ```
    """
    result = resolve_attack(attacker, defender, attack, context=context, ruleset=ruleset, stream=stream, clock=clock)
    if result.attack_roll.hit and not result.absorbed and (result.damage or 0) > 0:
        source = damage_source_for(attacker, attack, context)
        _, attach_events = ledger.attach(
            splash_douse_definition(attack, source),
            _entity_id(defender),
            clock=clock,
            allocator=allocator,
            registry=registry,
        )
        return AttackResult(
            attack_roll=result.attack_roll,
            absorbed=result.absorbed,
            damage=result.damage,
            events=(*result.events, *attach_events),
        )
    return result


def participant_modifier(combatant: Combatant, *, monster_modifier: int = 0) -> int:
    """Return a combatant's modifier to an individual initiative roll.

    Use it to fill the `modifier` field of a
    [`Participant`][osrlib.core.combat.Participant] before you call
    [`roll_initiative`][osrlib.core.combat.roll_initiative]. The modifier counts only when
    the `individual_initiative` ruleset flag is on. Side initiative, the default, rolls
    once for each side and adds no modifier.

    A character gets its DEX modifier, plus 1 for a halfling. A monster gets
    `monster_modifier`, because the tabletop rules leave a monster's initiative modifier to
    the referee.

    Args:
        combatant: The [`Combatant`][osrlib.core.creature.Combatant] rolling, which a
            [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy.
        monster_modifier: The modifier to use for a monster. The function ignores it for a
            character.

    Returns:
        The modifier to put in the `Participant`'s `modifier` field. It can be negative.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.combat import participant_modifier
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.data import load_monsters

        streams = RngStreams(master_seed=9)
        hild = create_character(
            name="Hild",
            class_id="fighter",
            alignment=Alignment.LAWFUL,
            ruleset=Ruleset(),
            stream=streams.get(CHARACTER_CREATION_STREAM),
        ).character
        assert participant_modifier(hild) == 1  # this Hild rolled DEX 17

        goblin = spawn_monster(load_monsters().get("goblin"), id="goblin-1", stream=streams.get(MONSTER_SPAWN_STREAM))
        assert participant_modifier(goblin) == 0
        assert participant_modifier(goblin, monster_modifier=1) == 1
        ```
    """
    if getattr(combatant, "definition", None) is None:
        return monster_modifier
    modifier = getattr(combatant, "initiative_modifier", 0)
    bonus = _class_ability_params(combatant, "initiative_bonus")
    if bonus is not None:
        modifier += int(bonus.get("bonus", 0))
    return modifier


def roll_initiative(participants: Sequence[Participant], *, ruleset: Ruleset, stream: RngStream) -> InitiativeResult:
    """Roll initiative for one combat round, by side or, under `individual_initiative`, by combatant.

    Call this at the start of each combat round, then let the combatants act in the
    `order` it returns. Build one [`Participant`][osrlib.core.combat.Participant] for each
    combatant, and get each one's modifier from
    [`participant_modifier`][osrlib.core.combat.participant_modifier]. The function reads
    nothing from your creatures and changes nothing. It works only from the participants
    you pass.

    Under side initiative, the default, each side rolls 1d6 and everyone on the side acts
    on that roll. Under the `individual_initiative` ruleset flag, each participant rolls
    1d6 and adds its modifier.

    The function re-rolls every tie. The tabletop rules let the referee re-roll a tie or resolve
    the tied actions at the same time, and osrlib re-rolls because acting at the same time
    would need a different combat model. Every side or participant in a tie rolls again,
    in the order you passed them, until no two totals are the same. A slow participant
    acts after every participant that isn't slow. Among themselves, slow participants act
    in initiative order. Participants with the same rank act in the order you passed them.

    Args:
        participants: One for each combatant, each with a different `key`. Combatants with
            the same rank act in this order.
        ruleset: The ruleset in play. Its `individual_initiative` flag picks the mode.
        stream: The stream the d6s come from, usually
            [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM]. One draw for each side,
            or for each participant under individual initiative, and one more for each
            re-roll of a tie.

    Returns:
        Every roll, re-rolls included, and the acting order of every participant.

    Examples:
        ```python
        from osrlib.core.combat import COMBAT_STREAM, Participant, roll_initiative
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset

        combat = RngStreams(master_seed=3).get(COMBAT_STREAM)
        result = roll_initiative(
            [
                Participant(key="hild", side="party"),
                Participant(key="brand", side="party", slow=True),  # a two-handed sword
                Participant(key="goblin-1", side="goblins"),
            ],
            ruleset=Ruleset(),
            stream=combat,
        )
        assert result.mode == "side"
        assert result.order == ("hild", "goblin-1", "brand")  # the slow actor goes last
        assert [(entry.key, entry.total) for entry in result.entries] == [("party", 5), ("goblins", 3)]
        ```
    """
    individual = ruleset.individual_initiative
    if individual:
        keys = [participant.key for participant in participants]
        modifiers = {participant.key: participant.modifier for participant in participants}
    else:
        keys = list(dict.fromkeys(participant.side for participant in participants))
        modifiers = dict.fromkeys(keys, 0)
    rolls: dict[str, list[int]] = {key: [stream.randbelow(6) + 1] for key in keys}
    totals = {key: rolls[key][-1] + modifiers[key] for key in keys}
    while True:
        tied = [key for key in keys if sum(1 for other in keys if totals[other] == totals[key]) > 1]
        if not tied:
            break
        for key in tied:
            rolls[key].append(stream.randbelow(6) + 1)
            totals[key] = rolls[key][-1] + modifiers[key]
    entries = tuple(
        InitiativeRoll(key=key, rolls=tuple(rolls[key]), modifier=modifiers[key], total=totals[key]) for key in keys
    )
    rank = {key: totals[key] for key in keys}
    indexed = list(enumerate(participants))
    if individual:
        ordering = sorted(indexed, key=lambda pair: (pair[1].slow, -rank[pair[1].key], pair[0]))
    else:
        ordering = sorted(indexed, key=lambda pair: (pair[1].slow, -rank[pair[1].side], pair[0]))
    order = tuple(pair[1].key for pair in ordering)
    mode = "individual" if individual else "side"
    event = InitiativeRolledEvent(mode=mode, entries=entries, order=order)
    return InitiativeResult(mode=mode, entries=entries, order=order, events=(event,))


def check_morale(subject: str, score: int, *, modifier: int = 0, stream: RngStream) -> MoraleResult:
    """Check a side's morale: 2d6 against its morale score, where a roll over the score breaks it.

    Call this when a side must check morale, and act on the result yourself: this function
    doesn't make anyone flee or surrender.
    [`morale_triggers`][osrlib.core.combat.morale_triggers] tells you when a side must
    check. To apply the rule that a side which holds twice stops checking, call
    [`MoraleTracker.check`][osrlib.core.combat.MoraleTracker.check] instead. A monster's
    morale score, ML on its stat block, is the `morale` field of its template.

    The check is for a side, not a creature. A spell like *bless* changes a creature's
    morale, so read that change with [`morale_modifier`][osrlib.core.combat.morale_modifier]
    and add it to the `modifier` you pass. A side with a score of 2 or less never fights,
    and a side with 12 or more never checks. Both are exempt: the function makes no roll,
    takes no draw, and ignores the modifier.

    Args:
        subject: The side's key. It appears in the event, so you can show which side checked.
        score: The morale score, which is 2 to 12 on a stat block. The function accepts any
            integer. A side with a score of 2 or less never fights, and a side with 12 or
            more always does.
        modifier: The adjustment to the roll. A value outside −2 to +2 counts as −2 or +2.
        stream: The stream the 2d6 comes from, usually
            [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM]. Two draws for a rolled
            check, and none for an exempt one.

    Returns:
        The outcome. Its [`MoraleCheckedEvent`][osrlib.core.events.MoraleCheckedEvent] has
        the verdict in `held` for every code, exempt checks included. The event is visible
        to the referee only, because players learn a side's morale from what it does.

    Examples:
        ```python
        from osrlib.core.combat import COMBAT_STREAM, check_morale
        from osrlib.core.rng import RngStreams

        combat = RngStreams(master_seed=3).get(COMBAT_STREAM)

        goblins = check_morale("goblins", 7, stream=combat)
        assert goblins.roll == 8  # over their morale score of 7
        assert not goblins.held

        skeletons = check_morale("skeletons", 12, stream=combat)
        assert skeletons.exempt and skeletons.held  # morale 12 never checks
        assert skeletons.roll is None
        ```
    """
    if score <= 2:
        event = MoraleCheckedEvent(code="combat.morale.exempt", subject=subject, score=score, held=False)
        return MoraleResult(held=False, exempt=True, events=(event,))
    if score >= 12:
        event = MoraleCheckedEvent(code="combat.morale.exempt", subject=subject, score=score, held=True)
        return MoraleResult(held=True, exempt=True, events=(event,))
    modifier = max(-2, min(2, modifier))
    rolled = stream.randbelow(6) + 1 + stream.randbelow(6) + 1
    held = rolled + modifier <= score
    event = MoraleCheckedEvent(
        code="combat.morale.held" if held else "combat.morale.broke",
        subject=subject,
        score=score,
        roll=rolled,
        modifier=modifier,
        held=held,
    )
    return MoraleResult(held=held, roll=rolled, modifier=modifier, events=(event,))


class ReactionRollResult(BaseModel):
    """A reaction roll's outcome: the 2d6, the modifier, and how the monsters react.

    [`roll_reaction`][osrlib.core.combat.roll_reaction] returns one. What the monsters do
    about it is up to you. The model is frozen.
    """

    model_config = ConfigDict(frozen=True)

    result: ReactionResult
    """How the monsters react: `attacks`, `hostile`, `uncertain`, `indifferent`, or `friendly`."""

    roll: int
    """The 2d6 total, before the modifier."""

    modifier: int = 0
    """The modifier you passed."""

    total: int
    """`roll` plus `modifier`.

    It can fall outside 2 to 12. `roll_reaction` reads a total below 2 as 2, and a total
    above 12 as 12.
    """

    events: tuple[Event, ...] = ()
    """The roll's [`ReactionRolledEvent`][osrlib.core.events.ReactionRolledEvent], ready to append to your log.

    It's visible to the referee only, because players learn a monster's mood from what it
    does.
    """


def roll_reaction(*, modifier: int = 0, stream: RngStream) -> ReactionRollResult:
    """Roll how monsters react to the party: 2d6 plus a modifier, read on the reaction table.

    Roll this when the party meets monsters that aren't already fighting, to learn whether
    they attack, wait, or talk. The roll changes nothing, and you decide how the monsters
    act on it. In a session, the encounter procedure rolls it for you, and a hostile result
    can lead to [`start_battle`][osrlib.crawl.battle.start_battle].

    You supply the modifier, from the `npc_reaction_modifier` of the character who speaks
    to the monsters, because the tabletop rules apply it only when one character speaks. The
    function reads a total outside 2 to 12 as the nearest end of the table, and doesn't
    treat it as an error.

    Args:
        modifier: The speaking character's CHA modifier to reactions, when a character
            speaks.
        stream: The stream the 2d6 comes from. A session draws reaction rolls from
            [`ENCOUNTER_STREAM`][osrlib.crawl.session.ENCOUNTER_STREAM], not the combat
            stream, because they belong to the encounter, not the battle. Two draws.

    Returns:
        The outcome. Its [`ReactionRolledEvent`][osrlib.core.events.ReactionRolledEvent] is
        visible to the referee only, because players learn a monster's mood from what it
        does.

    Examples:
        ```python
        from osrlib.core.combat import roll_reaction
        from osrlib.core.rng import RngStreams
        from osrlib.crawl.session import ENCOUNTER_STREAM

        encounter = RngStreams(master_seed=3).get(ENCOUNTER_STREAM)

        met = roll_reaction(stream=encounter)
        assert met.roll == 6
        assert met.result == "uncertain"

        spoken_to = roll_reaction(modifier=2, stream=encounter)
        assert (spoken_to.roll, spoken_to.modifier, spoken_to.total) == (3, 2, 5)
        assert spoken_to.result == "hostile"  # a 5 is still a bad start
        ```
    """
    from osrlib.data import load_combat_tables

    rolled = stream.randbelow(6) + 1 + stream.randbelow(6) + 1
    total = rolled + modifier
    result = reaction_result(load_combat_tables().reaction, total)
    event = ReactionRolledEvent(roll=rolled, modifier=modifier, total=total, result=result.value)
    return ReactionRollResult(result=result, roll=rolled, modifier=modifier, total=total, events=(event,))


class MoraleTracker(BaseModel):
    """Counts morale checks through one encounter, so a side that holds twice stops checking.

    Build one for each encounter and check morale through it instead of calling
    [`check_morale`][osrlib.core.combat.check_morale] directly. It applies the rule "If a
    monster passes two morale checks in an encounter, it will fight until killed, with no
    further checks."

    A tracker can be changed, unlike the result models in this module, because it keeps a
    count through the encounter. Build a new one for the next encounter, which starts the
    count again. A session's battle keeps one for you.
    """

    model_config = ConfigDict(validate_assignment=True)

    passed: dict[str, int] = {}
    """How many checks each side has held, by the side's key as you pass it to `check`.

    The tracker never checks a side at 2 again.
    """

    def check(self, subject: str, score: int, *, modifier: int = 0, stream: RngStream) -> MoraleResult | None:
        """Check a side's morale, unless it has already held twice in this encounter.

        [`check_morale`][osrlib.core.combat.check_morale] makes the check, and this method
        counts it. An exempt side, at morale 2 or less or 12 or more, never adds to the
        count, because it never rolls.

        Args:
            subject: The side's key. The tracker counts each key apart, so two groups of
                goblins with different keys are counted apart.
            score: The morale score, which is 2 to 12 on a stat block. The method accepts
                any integer. A score of 2 or less, or of 12 or more, is exempt, as in
                `check_morale`.
            modifier: The adjustment to the roll. A value outside −2 to +2 counts as −2
                or +2.
            stream: The stream the 2d6 comes from, usually
                [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM]. The method takes no draw
                once the side has held twice.

        Returns:
            The check's outcome, or `None` when the side has held twice and fights until
            it's killed, with no check made.

        Examples:
            ```python
            from osrlib.core.combat import COMBAT_STREAM, MoraleTracker
            from osrlib.core.rng import RngStreams

            combat = RngStreams(master_seed=3).get(COMBAT_STREAM)
            tracker = MoraleTracker()

            first = tracker.check("goblins", 10, stream=combat)
            second = tracker.check("goblins", 10, stream=combat)
            assert first.held and second.held
            assert tracker.passed == {"goblins": 2}

            assert tracker.check("goblins", 10, stream=combat) is None  # they fight on
            ```
        """
        if self.passed.get(subject, 0) >= 2:
            return None
        result = check_morale(subject, score, modifier=modifier, stream=stream)
        if result.held and not result.exempt:
            self.passed[subject] = self.passed.get(subject, 0) + 1
        return result


def incapacitated(combatant: Creature) -> bool:
    """Return whether a creature is out of the fight: dead, paralysed, turned to stone, or asleep.

    [`morale_triggers`][osrlib.core.combat.morale_triggers] counts a side's incapacitated
    members with this, and [`resolve_gaze`][osrlib.core.combat.resolve_gaze] skips them.
    Call it yourself to ask whether a creature is out of the fight, whichever way it went
    out. To ask whether it can still move, call
    [`cannot_move`][osrlib.core.combat.cannot_move], which also counts a creature caught
    in a web.

    The tabletop rules count a side's members who are "slain, paralysed, etc" for morale.
    osrlib reads that as dead, paralysed, petrified, or asleep.

    Args:
        combatant: The [`Creature`][osrlib.core.creature.Creature] to ask about, which a
            [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy.

    Returns:
        True when the creature has any of those conditions.

    Examples:
        ```python
        from osrlib.core.combat import DamageSource, deal_damage, incapacitated
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        spawn = RngStreams(master_seed=3).get(MONSTER_SPAWN_STREAM)
        goblin = spawn_monster(load_monsters().get("goblin"), id="goblin-1", stream=spawn)
        assert not incapacitated(goblin)

        deal_damage(goblin, 99, source=DamageSource())
        assert incapacitated(goblin)
        ```
    """
    return any(has_condition(combatant, condition) for condition in _CANNOT_ACT)


def cannot_move(combatant: Creature) -> bool:
    """Return whether a creature can't move.

    Ask this before you let a creature move, flee, or close to melee. It's true for an
    [`incapacitated`][osrlib.core.combat.incapacitated] creature, which is dead, paralysed,
    turned to stone, or asleep, and for an entangled one, which is caught in a *web*.
    Nothing in this module moves creatures, so no function here calls it for you.

    Args:
        combatant: The [`Creature`][osrlib.core.creature.Creature] to ask about, which a
            [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy.

    Returns:
        True when the creature can't move.

    Examples:
        ```python
        from osrlib.core.combat import cannot_move
        from osrlib.core.effects import Condition, grant_condition
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        spawn = RngStreams(master_seed=3).get(MONSTER_SPAWN_STREAM)
        goblin = spawn_monster(load_monsters().get("goblin"), id="goblin-1", stream=spawn)
        assert not cannot_move(goblin)

        grant_condition(goblin, Condition.ENTANGLED, None)  # a web caught it
        assert cannot_move(goblin)
        ```
    """
    return incapacitated(combatant) or has_condition(combatant, Condition.ENTANGLED)


def morale_modifier(combatant: Creature) -> int:
    """Return the change that spells like *bless* and *blight* make to a creature's morale.

    [`check_morale`][osrlib.core.combat.check_morale] takes a side's key and score, not a
    creature, so it applies a spell's morale change only when you pass it. Read the change
    here and add it to the `modifier` you pass to `check_morale`. It then counts like any
    other adjustment: the total is limited to −2 to +2, and it doesn't apply to an exempt
    side, at morale 2 or less or 12 or more.

    Args:
        combatant: The [`Creature`][osrlib.core.creature.Creature] whose morale is being
            checked, which a [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy.

    Returns:
        The sum of the morale changes from every effect on the creature, and 0 when there
        are none. It can be negative.

    Examples:
        ```python
        from osrlib.core.combat import COMBAT_STREAM, check_morale, morale_modifier
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        streams = RngStreams(master_seed=3)
        spawn = streams.get(MONSTER_SPAWN_STREAM)
        goblin = spawn_monster(load_monsters().get("goblin"), id="goblin-1", stream=spawn)
        assert morale_modifier(goblin) == 0  # nothing has blessed or blighted it

        outnumbered = -1
        adjustment = outnumbered + morale_modifier(goblin)
        checked = check_morale("goblins", 7, modifier=adjustment, stream=streams.get(COMBAT_STREAM))
        assert checked.modifier == -1
        assert checked.roll == 8 and checked.held  # 8 - 1 is within the morale score of 7
        ```
    """
    return modifier_total(combatant, "morale_bonus")


def morale_triggers(members: Sequence[Creature]) -> list[str]:
    """Return the reasons a side must check morale, from the state of its members now.

    Call this after each round to learn whether a side should check morale. Then call
    [`check_morale`][osrlib.core.combat.check_morale] or
    [`MoraleTracker.check`][osrlib.core.combat.MoraleTracker.check] for the check itself.
    In a session, [`osrlib.crawl.battle`][osrlib.crawl.battle] does this for you. The
    reasons, which osrlib calls triggers, are `first_death`, when any member of the side
    is dead, and `half_incapacitated`, when half the side or more is dead, paralysed,
    turned to stone, or asleep.

    The triggers describe the side as it is now, not what changed since you last asked, so
    every call returns `first_death` while a member lies dead. Keep track of the triggers
    you've already acted on.

    Args:
        members: The side's creatures, each a [`Creature`][osrlib.core.creature.Creature],
            which a [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy.

    Returns:
        The triggers: `first_death` when any member is dead, and `half_incapacitated` when
        half the side or more is [`incapacitated`][osrlib.core.combat.incapacitated]. When
        both apply, `first_death` comes first. The list is empty when neither applies, and
        for an empty side.

    Examples:
        ```python
        from osrlib.core.combat import DamageSource, deal_damage, morale_triggers
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        spawn = RngStreams(master_seed=3).get(MONSTER_SPAWN_STREAM)
        catalog = load_monsters()
        first = spawn_monster(catalog.get("goblin"), id="goblin-1", stream=spawn)
        second = spawn_monster(catalog.get("goblin"), id="goblin-2", stream=spawn)
        assert morale_triggers([first, second]) == []

        deal_damage(first, 99, source=DamageSource())
        assert morale_triggers([first, second]) == ["first_death", "half_incapacitated"]
        ```
    """
    triggers = []
    if any(has_condition(member, Condition.DEAD) for member in members):
        triggers.append("first_death")
    if members and sum(1 for member in members if incapacitated(member)) * 2 >= len(members):
        triggers.append("half_incapacitated")
    return triggers


def saving_throw(
    target: Combatant,
    category: SaveCategory,
    *,
    modifier: int = 0,
    magical: bool = False,
    element: str | None = None,
    source: Creature | None = None,
    stream: RngStream,
) -> SaveResult:
    """Roll a saving throw: 1d20 plus modifiers, which passes at or above the target's number for the category.

    Call this whenever something forces a save, then act on `passed` yourself: the function
    reports the outcome and changes nothing. Pick the category from
    [`SaveCategory`][osrlib.core.combat.SaveCategory].
    [`resolve_breath`][osrlib.core.combat.resolve_breath] and
    [`resolve_gaze`][osrlib.core.combat.resolve_gaze] already roll the saves they need, so you
    need this function for spells, traps, poisons, and saves of your own.

    The roll adds the target's bonuses for you:

    - A character's WIS modifier, when `magical` is true and the category isn't breath.
      The rules say the WIS bonus "does not normally include saves against breath
      attacks".
    - Bonuses from spells on the target. Some apply to every save. Some apply only against
      an `element`, like *resist fire* against fire. Some apply only against a `source` of
      another alignment, like *protection from evil*. Spell bonuses don't add up: only
      the largest bonus and the largest penalty count.
    - Bonuses from magic items the target has equipped, like a ring of protection. They
      add on top of the spell bonuses.

    Pass any other adjustment, like a referee's ruling, in `modifier`. A monster whose
    defenses give it an automatic save against a magical effect of an element, as a red
    dragon has against magical fire, passes without a roll and without a draw.

    Args:
        target: The saving [`Combatant`][osrlib.core.creature.Combatant], which a
            [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy.
        category: The saving throw category.
        modifier: Your adjustment, added to the target's bonuses.
        magical: Whether the effect is magical. It turns on the WIS modifier, and a
            monster's automatic save against an element needs it.
        element: The effect's element, like `fire`, or `None`. The function reads it for
            element bonuses and automatic saves.
        source: The `Creature` whose attack or ability forced the save, a `Character` or a
            `MonsterInstance`. The function reads it only for bonuses against another
            alignment.
        stream: The stream the d20 comes from, usually
            [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM]. One draw, or none for an
            automatic save.

    Returns:
        The outcome. Read `passed` to decide the consequence. `roll` is `None` on an
        automatic save. `events` contains the
        [`SavingThrowRolledEvent`][osrlib.core.events.SavingThrowRolledEvent], with the
        code `combat.save.passed`, `combat.save.failed`, or `combat.save.auto`.

    Examples:
        ```python
        from osrlib.core.combat import COMBAT_STREAM, SaveCategory, saving_throw
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        streams = RngStreams(master_seed=3)
        spawn = streams.get(MONSTER_SPAWN_STREAM)
        goblin = spawn_monster(load_monsters().get("goblin"), id="goblin-1", stream=spawn)

        result = saving_throw(goblin, SaveCategory.DEATH, stream=streams.get(COMBAT_STREAM))
        assert (result.roll, result.required) == (20, 14)
        assert result.passed
        assert [event.code for event in result.events] == ["combat.save.passed"]
        ```
    """
    target_id = _entity_id(target)
    template = getattr(target, "template", None)
    if template is not None and element is not None and magical and element in Element:
        energy = template.defenses.energy.get(Element(element))
        if energy is not None and energy.auto_save_magical:
            event = SavingThrowRolledEvent(code="combat.save.auto", target_id=target_id, category=category.value)
            return SaveResult(passed=True, auto=True, events=(event,))
    saves: SavingThrows = target.saves
    required = getattr(saves, category.value)
    if magical and category is not SaveCategory.BREATH:
        modifier += getattr(target, "magic_save_modifier", 0)
    versus_differs = alignments_differ(source, target) if source is not None else False
    modifier += modifier_total(
        target, "save_bonus", element=element, versus_differs=versus_differs, save_category=category.value
    )
    # Equipped-item save bonuses (rings of protection and fire resistance, the
    # Displacer Cloak's category-scoped +2). These are read from the equipped
    # inventory at query time and are exempt from the cumulative caps on spells.
    modifier += _item_modifier_total(target, "save_bonus", element=element, save_category=category.value)
    rolled = stream.randbelow(20) + 1
    passed = rolled + modifier >= required
    event = SavingThrowRolledEvent(
        code="combat.save.passed" if passed else "combat.save.failed",
        target_id=target_id,
        category=category.value,
        roll=rolled,
        modifier=modifier,
        required=required,
    )
    return SaveResult(passed=passed, roll=rolled, modifier=modifier, required=required, events=(event,))


def apply_healing(target: Creature, amount: int, *, source: str = "magical") -> list[Event]:
    """Restore hit points at once, up to the target's maximum.

    This changes the target and draws nothing, so roll the amount first when the healing
    is rolled. Use it for a cure spell or a healing potion. For a day of rest, call
    [`natural_healing`][osrlib.core.combat.natural_healing] instead, which rolls the 1d3
    and applies the diseases that slow it. A regenerating monster heals through its
    effect in the [`EffectsLedger`][osrlib.core.effects.EffectsLedger], which doesn't call
    this function.

    Some targets heal less or not at all:

    - A dead target can't be healed, and the call returns no events.
    - A target with the `diseased` condition, as from mummy rot, heals nothing from a
      `magical` source. Healing that's instant counts as magical, which is why `magical`
      is the default.
    - A target with the `weakened` condition, which follows being raised from the dead,
      heals nothing from any source until the weakness effect ends. The rules say the
      subject "has 1 hit point" until the recovery period ends, and that it "may not be
      shortened by any magical healing".
    - A target with a `magical_healing_half` modifier, as from a cursed scroll, heals half
      of a `magical` amount, rounded down.

    A target that heals nothing because of a condition gets a
    [`HealingAppliedEvent`][osrlib.core.events.HealingAppliedEvent] with the code
    `combat.healing.blocked`, which reports 0 healed.

    Args:
        target: The [`Creature`][osrlib.core.creature.Creature] to heal, which a
            [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy. The
            function changes it in place.
        amount: The hit points to restore, 0 or more. The function cuts healing past the
            maximum to the maximum, and doesn't treat it as an error.
        source: The kind of healing: `magical`, the default, `natural`, or
            `regeneration`. It decides which conditions block the healing, and appears in
            the event.

    Returns:
        A `HealingAppliedEvent` with the hit points actually restored, then a
        [`HitPointsReportedEvent`][osrlib.core.events.HitPointsReportedEvent]. A blocked
        target gets only the blocked `HealingAppliedEvent`. A dead target gets an empty
        list.

    Raises:
        ValueError: If `amount` is negative. The function changes nothing first.

    Examples:
        ```python
        from osrlib.core.combat import DamageSource, apply_healing, deal_damage
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        spawn = RngStreams(master_seed=3).get(MONSTER_SPAWN_STREAM)
        goblin = spawn_monster(load_monsters().get("goblin"), id="goblin-1", stream=spawn)
        deal_damage(goblin, 3, source=DamageSource())
        assert (goblin.current_hp, goblin.max_hp) == (2, 5)

        events = apply_healing(goblin, 10)
        assert events[0].amount == 3  # capped at the maximum, not 10
        assert goblin.current_hp == 5
        ```
    """
    if amount < 0:
        raise ValueError(f"healing must be non-negative, got {amount}")
    target_id = _entity_id(target)
    if has_condition(target, Condition.DEAD):
        return []
    if has_condition(target, Condition.WEAKENED) or (source == "magical" and has_condition(target, Condition.DISEASED)):
        return [HealingAppliedEvent(code="combat.healing.blocked", target_id=target_id, amount=0, source=source)]
    if source == "magical" and has_modifier(target, "magical_healing_half"):
        # The cursed scroll's slow healing: "healing spells only cure half the
        # normal number of hit points", so half, floored, unlike *cause
        # disease*'s outright block.
        amount //= 2
    healed = min(amount, target.max_hp - target.current_hp)
    target.current_hp += healed
    return [
        HealingAppliedEvent(code="combat.healing.applied", target_id=target_id, amount=healed, source=source),
        HitPointsReportedEvent(target_id=target_id, current_hp=target.current_hp, max_hp=target.max_hp),
    ]


def natural_healing(target: Creature, stream: RngStream, *, ledger: EffectsLedger | None = None) -> list[Event]:
    """Heal one full day of complete rest: 1d3 hit points.

    Call this once for each day of rest without interruption. You decide whether the rest
    was interrupted. In a session, the rest procedure decides it for you. For healing from a
    spell or a potion, call [`apply_healing`][osrlib.core.combat.apply_healing] instead,
    which takes the amount you rolled.

    An effect that slows healing makes the target heal less often, not heal less. Mummy
    rot makes natural healing ten times slower, so the target heals on every tenth day of
    rest. With an effect that has a `healing_rest_days` param, the target heals once in that
    many days of rest, which is 2 for *cause disease* and for a cursed scroll's "twice the
    usual amount of time". The function counts the days in the effect's `state`, and when
    several effects apply, the slowest one counts. A diseased target doesn't heal at all when you pass no `ledger`.

    Args:
        target: The resting [`Creature`][osrlib.core.creature.Creature], which a
            [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy. The
            function changes it in place.
        stream: The stream the 1d3 comes from. Natural healing draws from
            [`EFFECTS_STREAM`][osrlib.core.effects.EFFECTS_STREAM], with the draws for
            effects, not from the combat stream. One draw on a day the target heals,
            and none on a day it doesn't.
        ledger: The [`EffectsLedger`][osrlib.core.effects.EffectsLedger] that contains the
            target's effects. Without it, no effect can slow the healing.

    Returns:
        The events of [`apply_healing`][osrlib.core.combat.apply_healing] for the 1d3. An
        empty list on a day an effect skips, and for a dead target.

    Examples:
        ```python
        from osrlib.core.combat import DamageSource, deal_damage, natural_healing
        from osrlib.core.effects import EFFECTS_STREAM
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        streams = RngStreams(master_seed=3)
        spawn = streams.get(MONSTER_SPAWN_STREAM)
        goblin = spawn_monster(load_monsters().get("goblin"), id="goblin-1", stream=spawn)
        deal_damage(goblin, 4, source=DamageSource())
        assert goblin.current_hp == 1

        events = natural_healing(goblin, streams.get(EFFECTS_STREAM))
        assert events[0].amount == 1  # the 1d3 came up 1
        assert goblin.current_hp == 2
        ```
    """
    if has_condition(target, Condition.DEAD):
        return []
    if has_condition(target, Condition.DISEASED) and ledger is None:
        return []
    slowdowns = []
    if ledger is not None:
        for effect in ledger.active_on(_entity_id(target)):
            if effect.definition.kind == "mummy_rot":
                slowdowns.append((effect, 10))
            elif "healing_rest_days" in effect.definition.params:
                slowdowns.append((effect, _int_param(effect.definition.params, "healing_rest_days")))
    if slowdowns:
        effect, cadence = max(slowdowns, key=lambda pair: pair[1])
        effect.state["rest_days"] = effect.state.get("rest_days", 0) + 1
        if effect.state["rest_days"] % cadence != 0:
            return []
    amount = stream.randbelow(3) + 1
    return apply_healing(target, amount, source="natural")


def falling_damage(feet: int, stream: RngStream) -> RollResult | None:
    """Roll falling damage: 1d6 for each full 10 feet fallen.

    This only rolls. Apply the total with [`deal_damage`][osrlib.core.combat.deal_damage],
    passing a [`DamageSource`][osrlib.core.combat.DamageSource] whose `kind` is `falling`,
    and the roll's `rolls`. `deal_damage` doesn't check immunity, so a monster that only
    silver or magic can harm takes the fall too. Its damage reductions still apply.

    Args:
        feet: The distance fallen. A part of 10 feet doesn't count, so 19 feet is one die.
        stream: The stream the d6s come from, usually
            [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM].

    Returns:
        The damage roll, or `None` for a fall under 10 feet, for which the function rolls no
        dice and takes no draw.

    Examples:
        ```python
        from osrlib.core.combat import COMBAT_STREAM, falling_damage
        from osrlib.core.rng import RngStreams

        combat = RngStreams(master_seed=3).get(COMBAT_STREAM)

        rolled = falling_damage(25, combat)
        assert rolled.rolls == (5, 3)  # two dice: 25 feet is two full ten-foot drops
        assert rolled.total == 8

        assert falling_damage(8, combat) is None
        ```
    """
    dice = feet // 10
    if dice < 1:
        return None
    return roll(f"{dice}d6", stream)


def drain_monster_hd(monster: MonsterInstance, *, levels: int = 1, stream: RngStream) -> list[Event]:
    """Drain Hit Dice from a monster, which is how energy drain works on a monster.

    The rules say energy drain takes "one experience level (or Hit Die)": a character loses
    levels and a monster loses Hit Dice. Call this when something drains a monster. For a character,
    call [`drain_levels`][osrlib.core.classes.drain_levels].
    [`resolve_energy_drain`][osrlib.core.combat.resolve_energy_drain] calls the right one
    of the two for you after an undead creature's hit. This function changes the monster.

    For each Hit Die drained, the monster loses a rolled d8 from both its maximum and its
    current hit points, and its THAC0 and saving throws become those of its new Hit Dice.
    Neither hit point total falls below 1, so losing hit points never kills a monster.

    The function kills a monster already at 1 Hit Die instead. It rolls no die for that step
    and takes no Hit Die off, and the event counts the last one as lost.

    Args:
        monster: The drained [`MonsterInstance`][osrlib.core.monsters.MonsterInstance].
            The function changes it in place.
        levels: How many Hit Dice the drain takes.
        stream: The stream the d8s for lost hit points come from. Drain undoes
            advancement, so it draws from
            [`ADVANCEMENT_STREAM`][osrlib.core.character.ADVANCEMENT_STREAM], not the
            combat stream. One draw for each Hit Die taken, and none for the step that
            kills.

    Returns:
        For a monster that survives, a
        [`LevelDrainedEvent`][osrlib.core.events.LevelDrainedEvent] with the code
        `combat.drain.drained`, then a
        [`HitPointsReportedEvent`][osrlib.core.events.HitPointsReportedEvent]. For a monster
        the drain kills, a `LevelDrainedEvent` with the code `combat.drain.slain`, then the
        events of [`kill`][osrlib.core.effects.kill], which end with a
        `HitPointsReportedEvent` that reports 0.

    Examples:
        ```python
        from osrlib.core.character import ADVANCEMENT_STREAM
        from osrlib.core.combat import drain_monster_hd
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        streams = RngStreams(master_seed=3)
        spawn = streams.get(MONSTER_SPAWN_STREAM)
        ogre = spawn_monster(load_monsters().get("ogre"), id="ogre-1", stream=spawn)
        assert (ogre.hit_dice_count, ogre.max_hp, ogre.thac0) == (4, 25, 15)

        events = drain_monster_hd(ogre, stream=streams.get(ADVANCEMENT_STREAM))
        assert events[0].code == "combat.drain.drained"
        assert (ogre.hit_dice_count, ogre.max_hp, ogre.thac0) == (3, 21, 16)  # worse at everything
        ```
    """
    from osrlib.core.events import LevelDrainedEvent

    monster_id = _entity_id(monster)
    former = monster.hit_dice_count
    hp_lost = 0
    slain = False
    for _ in range(levels):
        if monster.hit_dice_count <= 1:
            slain = True
            break
        lost = max(1, stream.randbelow(8) + 1)
        monster.drained_hd += 1
        monster.current_hp = max(1, monster.current_hp - lost)
        monster.max_hp = max(1, monster.max_hp - lost)
        hp_lost += lost
    events: list[Event] = []
    if slain:
        # The killing Hit Die counts as lost, mirroring character drain.
        events.append(
            LevelDrainedEvent(
                code="combat.drain.slain",
                target_id=monster_id,
                levels_lost=former - monster.hit_dice_count + 1,
                new_level=0,
                hp_lost=hp_lost,
            )
        )
        events.extend(kill(monster))
        return events
    events.append(
        LevelDrainedEvent(
            code="combat.drain.drained",
            target_id=monster_id,
            levels_lost=former - monster.hit_dice_count,
            new_level=monster.hit_dice_count,
            hp_lost=hp_lost,
        )
    )
    events.append(HitPointsReportedEvent(target_id=monster_id, current_hp=monster.current_hp, max_hp=monster.max_hp))
    return events


def resolve_energy_drain(attacker: MonsterInstance, target: Creature, *, stream: RngStream) -> list[Event]:
    """Drain levels or Hit Dice from the target of an undead creature's hit.

    Call this after a wight, wraith, spectre, or vampire hits.
    [`resolve_attack`][osrlib.core.combat.resolve_attack] deals the hit's damage and
    leaves the drain to you. This function reads the attacker's `energy_drain` ability for
    how many levels to take and how to set a character's experience points. A
    [`Character`][osrlib.core.character.Character], which has a class, loses levels
    through [`drain_levels`][osrlib.core.classes.drain_levels]. A
    [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] loses Hit Dice through
    [`drain_monster_hd`][osrlib.core.combat.drain_monster_hd]. For a character, the
    ability's text about what the victim becomes appears in the drain event.

    Args:
        attacker: The draining [`MonsterInstance`][osrlib.core.monsters.MonsterInstance],
            whose template has an `energy_drain` ability.
        target: The drained [`Creature`][osrlib.core.creature.Creature], a
            [`Character`][osrlib.core.character.Character] or a `MonsterInstance`. Its type
            decides whether levels or Hit Dice come off. The function changes it in place.
        stream: The stream the dice for lost hit points come from. Drain undoes
            advancement, so it draws from
            [`ADVANCEMENT_STREAM`][osrlib.core.character.ADVANCEMENT_STREAM], not the
            combat stream.

    Returns:
        The events in order. The first is a
        [`LevelDrainedEvent`][osrlib.core.events.LevelDrainedEvent] with the code
        `combat.drain.drained`. A
        [`HitPointsReportedEvent`][osrlib.core.events.HitPointsReportedEvent] with the
        target's new hit points comes next. A character then gets a
        [`SpellForgottenEvent`][osrlib.core.events.SpellForgottenEvent] for each memorized
        spell it no longer has room for. When the drain kills the target, the first event
        has the code `combat.drain.slain`, and only the events of
        [`kill`][osrlib.core.effects.kill] follow it.

    Raises:
        ValueError: If the attacker has no `energy_drain` ability, so it doesn't drain.
            The function changes nothing first.

    Examples:
        ```python
        from osrlib.core.character import ADVANCEMENT_STREAM
        from osrlib.core.combat import resolve_energy_drain
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        streams = RngStreams(master_seed=3)
        spawn = streams.get(MONSTER_SPAWN_STREAM)
        catalog = load_monsters()
        wight = spawn_monster(catalog.get("wight"), id="wight-1", stream=spawn)
        ogre = spawn_monster(catalog.get("ogre"), id="ogre-1", stream=spawn)

        events = resolve_energy_drain(wight, ogre, stream=streams.get(ADVANCEMENT_STREAM))
        assert events[0].code == "combat.drain.drained"
        assert ogre.hit_dice_count == 3  # the wight's touch takes one Hit Die
        ```
    """
    from osrlib.core.classes import drain_levels

    params = _monster_ability_params(attacker, "energy_drain")
    if params is None:
        raise ValueError(f"{_entity_id(attacker)} has no energy_drain ability")
    levels = int(params.get("levels", 1))
    if getattr(target, "definition", None) is not None:
        # A class definition is a character's, and its absence is what marks a monster.
        # The branch has settled which the target is, and the casts state that, since a `getattr`
        # test proves nothing to the type checker. The type name stays quoted because
        # `Character` is imported for type checking alone and `cast` never evaluates it.
        character = cast("Character", target)
        # The params gate above proves the ability is there. The second lookup is for its
        # prose, which the params don't contain.
        ability = cast(MonsterAbility, attacker.template.ability("energy_drain"))
        result = drain_levels(
            character,
            character.definition,
            levels=levels,
            xp_policy=str(params.get("xp_policy", "level_minimum")),
            stream=stream,
            spawn_consequence=ability.prose,
        )
        return list(result.events)
    return drain_monster_hd(cast(MonsterInstance, target), levels=levels, stream=stream)


def effective_hd(combatant: Creature) -> int:
    """Return how many Hit Dice a creature counts as when a spell like *sleep* spends a Hit Dice budget.

    [`select_targets`][osrlib.core.combat.select_targets] spends its `hd_budget` in these
    units. Call this function yourself to work out in advance how many creatures a
    *sleep* would take.

    A monster counts its Hit Dice without the hit point bonus after them, so a 2+1 HD
    monster counts as 2. A drained monster counts the Hit Dice it has left, its
    `hit_dice_count`. A monster under 1 Hit Die counts as 1. A
    character counts its level.

    Args:
        combatant: The [`Creature`][osrlib.core.creature.Creature] to measure, which a
            [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy.

    Returns:
        The Hit Dice the creature counts as, at least 1.

    Examples:
        ```python
        from osrlib.core.combat import effective_hd
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        spawn = RngStreams(master_seed=3).get(MONSTER_SPAWN_STREAM)
        catalog = load_monsters()

        assert effective_hd(spawn_monster(catalog.get("goblin"), id="goblin-1", stream=spawn)) == 1
        assert effective_hd(spawn_monster(catalog.get("ogre"), id="ogre-1", stream=spawn)) == 4  # 4+1 HD
        assert effective_hd(spawn_monster(catalog.get("normal_rat"), id="rat-1", stream=spawn)) == 1  # under 1 HD
        ```
    """
    if isinstance(combatant, MonsterInstance):
        return max(1, combatant.hit_dice_count)
    return max(1, getattr(combatant, "level", 1))


def select_targets(
    mode: TargetingMode,
    candidates: Sequence[Creature],
    *,
    stream: RngStream,
    count: int | None = None,
    count_dice: str | None = None,
    hd_budget: int | None = None,
) -> tuple[list[Creature], list[Event]]:
    """Choose which of the candidates you list a spell or other area effect reaches.

    [`cast_spell`][osrlib.core.spells.cast_spell] calls this for a spell that targets up
    to a number of creatures, or a budget of Hit Dice. Call it yourself to apply the same
    rules to an effect of your own. You supply the candidates, because `osrlib.core` has no
    record of where creatures stand: who's in range or in the blast is your judgment. In a session,
    [`osrlib.crawl.battle`][osrlib.crawl.battle] supplies them from the distances it tracks. Pass the selected targets
    to whatever resolves the effect, like [`saving_throw`][osrlib.core.combat.saving_throw] or
    [`deal_damage`][osrlib.core.combat.deal_damage].

    Each [`TargetingMode`][osrlib.core.combat.TargetingMode] chooses its targets this way:

    - `self` and `single` take the first candidate.
    - `up_to_n` takes the first N candidates in your order. N is the fixed `count`, or a
      roll of `count_dice`, like the 1d4 of *hold person*.
    - `area` and `gaze` take every candidate.
    - `hd_budget` takes candidates weakest first, by
      [`effective_hd`][osrlib.core.combat.effective_hd], with equal ones in your order,
      until the budget runs out. It skips a candidate with more Hit Dice than the budget has
      left, and goes on down the list. That's how *sleep* works.

    Args:
        mode: The targeting mode.
        candidates: The creatures that could be targeted, in the order you want them
            chosen when the mode or a tie leaves a choice. Each is a
            [`Creature`][osrlib.core.creature.Creature], which a
            [`Character`][osrlib.core.character.Character] and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy.
        stream: The stream a rolled count draws from, usually
            [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM]. Only `count_dice` draws.
        count: The fixed N for `up_to_n`.
        count_dice: The dice expression, like `1d4`, for a rolled N in `up_to_n`. When you
            pass both, the function uses `count`.
        hd_budget: The Hit Dice budget for `hd_budget`, which that mode requires.

    Returns:
        The selected targets, and a list with one
        [`TargetsSelectedEvent`][osrlib.core.events.TargetsSelectedEvent], which is visible
        to the referee only. The targets are in the order they were chosen, which for
        `hd_budget` is weakest first.

    Raises:
        ValueError: If the mode is `hd_budget` and `hd_budget` is `None`, or if `mode`
            isn't one of the targeting modes.

    Examples:
        ```python
        from osrlib.core.combat import COMBAT_STREAM, TargetingMode, select_targets
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        streams = RngStreams(master_seed=3)
        spawn = streams.get(MONSTER_SPAWN_STREAM)
        catalog = load_monsters()
        ogre = spawn_monster(catalog.get("ogre"), id="ogre-1", stream=spawn)
        goblins = [spawn_monster(catalog.get("goblin"), id=f"goblin-{n}", stream=spawn) for n in (1, 2, 3)]
        combat = streams.get(COMBAT_STREAM)

        # Four Hit Dice of sleep: three 1 HD goblins first, then nothing left for the 4 HD ogre.
        selected, events = select_targets(TargetingMode.HD_BUDGET, [ogre, *goblins], stream=combat, hd_budget=4)
        assert [target.id for target in selected] == ["goblin-1", "goblin-2", "goblin-3"]
        assert events[0].code == "combat.targeting.selected"

        selected, _ = select_targets(TargetingMode.UP_TO_N, goblins, stream=combat, count=2)
        assert [target.id for target in selected] == ["goblin-1", "goblin-2"]
        ```
    """
    selected: list[Creature]
    if mode in (TargetingMode.SELF, TargetingMode.SINGLE):
        selected = list(candidates[:1])
    elif mode is TargetingMode.UP_TO_N:
        n = count if count is not None else roll(str(count_dice), stream).total
        selected = list(candidates[:n])
    elif mode in (TargetingMode.AREA, TargetingMode.GAZE):
        selected = list(candidates)
    elif mode is TargetingMode.HD_BUDGET:
        if hd_budget is None:
            raise ValueError("hd_budget mode needs a budget")
        remaining = hd_budget
        ordered = sorted(enumerate(candidates), key=lambda pair: (effective_hd(pair[1]), pair[0]))
        selected = []
        for _, candidate in ordered:
            hd = effective_hd(candidate)
            if hd <= remaining:
                selected.append(candidate)
                remaining -= hd
    else:
        raise ValueError(f"unknown targeting mode {mode!r}")
    event = TargetsSelectedEvent(mode=mode.value, target_ids=tuple(_entity_id(target) for target in selected))
    return selected, [event]


def resolve_gaze(
    gazer: Creature,
    engaged: Sequence[Combatant],
    *,
    stream: RngStream,
    ledger: EffectsLedger,
    clock: GameClock,
    allocator: Any,
    registry: dict[str, Any],
) -> list[Event]:
    """Resolve one round of a petrifying gaze against the creatures in melee with the gazer.

    Call this once each round for a basilisk, a medusa, or another monster whose gaze
    turns creatures to stone, as well as whatever it does with its attacks. You supply the
    creatures in melee with it, because `osrlib.core` has no record of where creatures
    stand.

    Each of those creatures saves versus paralysis, unless it's averting its eyes or is
    already [`incapacitated`][osrlib.core.combat.incapacitated]. On a failed save, the
    function attaches a permanent petrification effect, which gives the `petrified`
    condition. A petrified
    creature isn't dead, and the effect can be ended. A creature that fights with its eyes
    averted takes a penalty to hit, which you apply through the `situational_modifier` of
    its [`AttackContext`][osrlib.core.combat.AttackContext], as you do the gazer's bonus
    against it. This function doesn't model a mirror, so leave a creature that watches the
    gazer in a mirror out of `engaged`. A battle in a
    [`GameSession`][osrlib.crawl.session.GameSession] does all of this for you, from
    [`BattleDeclaration.gaze_defense`][osrlib.crawl.commands.BattleDeclaration.gaze_defense].

    Args:
        gazer: The gazing [`Creature`][osrlib.core.creature.Creature], usually a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance]. The function doesn't read it.
        engaged: The creatures in melee with the gazer, each a
            [`Combatant`][osrlib.core.creature.Combatant], which a
            [`Character`][osrlib.core.character.Character] and a `MonsterInstance` both
            satisfy.
        stream: The stream the saves draw from, usually
            [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM]. One draw for each creature
            that saves.
        ledger: The [`EffectsLedger`][osrlib.core.effects.EffectsLedger] the petrification
            effect is attached to.
        clock: The game clock. The function records its current round as the round the
            effect was attached.
        allocator: The [`IdAllocator`][osrlib.core.monsters.IdAllocator] that assigns the
            effect ids.
        registry: Your creatures by entity id, so the ledger can find the targets.

    Returns:
        For each creature in `engaged` that saves, in order, its
        [`SavingThrowRolledEvent`][osrlib.core.events.SavingThrowRolledEvent], and on a
        failed save the events of attaching the petrification.

    Examples:
        ```python
        from osrlib.core.clock import GameClock
        from osrlib.core.combat import COMBAT_STREAM, resolve_gaze
        from osrlib.core.effects import Condition, EffectsLedger, has_condition
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        streams = RngStreams(master_seed=5)
        spawn = streams.get(MONSTER_SPAWN_STREAM)
        catalog = load_monsters()
        basilisk = spawn_monster(catalog.get("basilisk"), id="basilisk-1", stream=spawn)
        goblins = [spawn_monster(catalog.get("goblin"), id=f"goblin-{n}", stream=spawn) for n in (1, 2)]

        events = resolve_gaze(
            basilisk,
            goblins,
            stream=streams.get(COMBAT_STREAM),
            ledger=EffectsLedger(),
            clock=GameClock(),
            allocator=IdAllocator(),
            registry={goblin.id: goblin for goblin in goblins},
        )
        assert [event.code for event in events][:2] == ["combat.save.passed", "combat.save.failed"]
        assert [has_condition(goblin, Condition.PETRIFIED) for goblin in goblins] == [False, True]
        ```
    """
    events: list[Event] = []
    for target in engaged:
        if has_condition(target, Condition.AVERTED_EYES) or incapacitated(target):
            continue
        save = saving_throw(target, SaveCategory.PARALYSIS, stream=stream)
        events.extend(save.events)
        if not save.passed:
            definition = EffectDefinition(kind="petrification", permanent=True, condition=Condition.PETRIFIED)
            _, attach_events = ledger.attach(
                definition, _entity_id(target), clock=clock, allocator=allocator, registry=registry
            )
            events.extend(attach_events)
    return events


def validate_breath(monster: MonsterInstance) -> list[Rejection]:
    """Check whether a monster may use its breath weapon, without rolling anything or changing anything.

    Call this before [`resolve_breath`][osrlib.core.combat.resolve_breath], which raises
    an error, instead of returning a rejection, when the monster can't breathe. An empty
    list means the monster may breathe.

    The rejections, by [`Rejection`][osrlib.core.validation.Rejection] code:

    - `combat.breath.no_breath_weapon`: the monster has no breath weapon.
    - `combat.breath.exhausted`: the monster has used its breath weapon as many times
      today as its daily limit allows, which is three for a dragon. The `uses_per_day`
      param has the limit.

    A breath weapon with no daily limit, like a hellhound's, is never exhausted. How often
    such a monster breathes is up to whatever chooses its actions.

    Args:
        monster: The breathing [`MonsterInstance`][osrlib.core.monsters.MonsterInstance].
            The function checks its `breath_uses_today` against the limit.

    Returns:
        The reasons the monster can't breathe, as
        [`Rejection`][osrlib.core.validation.Rejection] values. The list is empty when the
        monster may breathe.

    Examples:
        ```python
        from osrlib.core.combat import validate_breath
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        spawn = RngStreams(master_seed=3).get(MONSTER_SPAWN_STREAM)
        catalog = load_monsters()
        dragon = spawn_monster(catalog.get("white_dragon"), id="dragon-1", stream=spawn)
        goblin = spawn_monster(catalog.get("goblin"), id="goblin-1", stream=spawn)

        assert validate_breath(dragon) == []
        assert validate_breath(goblin)[0].code == "combat.breath.no_breath_weapon"

        dragon.breath_uses_today = 3
        assert validate_breath(dragon)[0].code == "combat.breath.exhausted"
        ```
    """
    params = _monster_ability_params(monster, "breath_weapon")
    if params is None:
        return [Rejection(code="combat.breath.no_breath_weapon", params={"monster": _entity_id(monster)})]
    limit = params.get("uses_per_day")
    if limit is not None and getattr(monster, "breath_uses_today", 0) >= int(limit):
        return [
            Rejection(
                code="combat.breath.exhausted",
                params={"monster": _entity_id(monster), "uses_per_day": int(limit)},
            )
        ]
    return []


def resolve_breath(
    monster: MonsterInstance,
    targets: Sequence[Combatant],
    *,
    ruleset: Ruleset,
    stream: RngStream,
    clock: GameClock | None = None,
) -> list[Event]:
    """Resolve a monster's breath weapon against the creatures you say it catches.

    Call this instead of [`resolve_attack`][osrlib.core.combat.resolve_attack] when a
    monster breathes. There's no attack roll: every target saves versus breath instead,
    and the whole breath resolves in one call. You supply the creatures caught in it,
    because `osrlib.core` has no record of where creatures stand. Call
    [`validate_breath`][osrlib.core.combat.validate_breath] first, because breathing past
    the daily limit raises an error instead of returning a rejection. This function adds
    one to the monster's daily count and changes every target it harms.

    What the breath does depends on the monster:

    - A dragon's breath deals damage equal to the dragon's current hit points, so a
      wounded dragon's breath is weaker. A dragon breathes up to three times a day.
    - A hellhound's breath rolls its listed dice, with no daily limit. The function rolls
      the dice again for each target.
    - A sea dragon's spittle kills a target that fails its save, and does nothing to one
      that passes.

    A target that saves takes half damage, rounded down, so a breath of 1 point does
    nothing to it. A target immune to the breath's element doesn't save and takes nothing.

    A breath weapon is a destructive source, so a target it kills loses what it carried,
    and its magic items save under the `magic_item_death_save` ruleset flag. The tabletop
    rules give a lightning bolt and a dragon's breath as examples of deaths that destroy
    items. osrlib applies the rule to every breath weapon, so a hellhound's fire destroys
    equipment as a dragon's does.

    Args:
        monster: The breathing [`MonsterInstance`][osrlib.core.monsters.MonsterInstance].
            Its `breath_uses_today` goes up by one when its breath has a daily limit.
        targets: The creatures caught in the breath, each a
            [`Combatant`][osrlib.core.creature.Combatant], which a
            [`Character`][osrlib.core.character.Character] and a `MonsterInstance` both
            satisfy. The function changes them in place.
        ruleset: The ruleset in play.
        stream: The stream every draw comes from, usually
            [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM]: one save for each target,
            the damage dice when the breath rolls them, and the saves of the magic items of
            each target it kills.
        clock: The game clock. When you pass it, the function sets the `last_damaged_round`
            of each monster target the breath damages.

    Returns:
        The events for each target, in the order you passed the targets. A target immune
        to the breath gets a
        [`DamageAbsorbedEvent`][osrlib.core.events.DamageAbsorbedEvent] and doesn't save.
        Any other target gets its
        [`SavingThrowRolledEvent`][osrlib.core.events.SavingThrowRolledEvent], then the
        events of [`deal_damage`][osrlib.core.combat.deal_damage] when damage lands, or of
        [`kill`][osrlib.core.effects.kill] when the breath kills outright.

    Raises:
        ValueError: If the monster has no breath weapon, or has used up its breath for the
            day. The function draws nothing and changes nothing first. Breathing anyway is a
            mistake in your code, not a move the rules reject, so call `validate_breath` first.

    Examples:
        ```python
        from osrlib.core.combat import COMBAT_STREAM, resolve_breath
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.data import load_monsters

        streams = RngStreams(master_seed=3)
        spawn = streams.get(MONSTER_SPAWN_STREAM)
        catalog = load_monsters()
        dragon = spawn_monster(catalog.get("white_dragon"), id="dragon-1", stream=spawn)
        goblins = [spawn_monster(catalog.get("goblin"), id=f"goblin-{n}", stream=spawn) for n in (1, 2)]
        assert dragon.current_hp == 39  # the cone deals this much, halved on a save

        events = resolve_breath(dragon, goblins, ruleset=Ruleset(), stream=streams.get(COMBAT_STREAM))
        assert [goblin.current_hp for goblin in goblins] == [0, 0]  # half of 39 still kills a goblin
        assert [event.code for event in events].count("combat.death.died") == 2
        assert dragon.breath_uses_today == 1
        ```
    """
    rejections = validate_breath(monster)
    if rejections:
        raise ValueError(f"illegal breath: {[rejection.code for rejection in rejections]}")
    params = _monster_ability_params(monster, "breath_weapon")
    if params is None:
        raise ValueError(f"{_entity_id(monster)} has no breath_weapon ability")
    if params.get("uses_per_day") is not None:
        monster.breath_uses_today += 1
    element = str(params.get("element")) if params.get("element") is not None else None
    # Breath weapons are destructive deaths: the SRD's destruction-of-items
    # examples ("a lightning bolt spell or a dragon's breath") illustrate energy
    # deaths generally, so a hellhound's or a chimera's fire kill destroys
    # equipment too, not only a dragon's.
    source = DamageSource(element=element, kind="breath", destructive=True)
    events: list[Event] = []
    save_or_die = params.get("outcome") == "death"
    for target in targets:
        if check_immunity(target, source, ruleset=ruleset, attacker=monster):
            events.append(
                DamageAbsorbedEvent(
                    target_id=_entity_id(target),
                    attacker_id=_entity_id(monster),
                    keys=(element,) if element else (),
                )
            )
            continue
        save = saving_throw(target, SaveCategory.BREATH, element=element, stream=stream)
        events.extend(save.events)
        if save_or_die:
            if not save.passed:
                events.extend(kill(target))
            continue
        damage_spec = params.get("damage")
        if damage_spec == "current_hp":
            amount, rolls = monster.current_hp, ()
        else:
            result = roll(str(damage_spec), stream)
            amount, rolls = result.total, result.rolls
        if save.passed:
            amount = amount // 2
        if amount < 1:
            continue
        events.extend(
            deal_damage(
                target,
                amount,
                source=source,
                attacker_id=_entity_id(monster),
                rolls=tuple(rolls),
                clock=clock,
                ruleset=ruleset,
                stream=stream,
            )
        )
    return events
