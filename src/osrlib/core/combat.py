"""The combat kernel: initiative, attacks, damage, saving throws, morale, and targeting.

Call these functions yourself, or let a session call them for you. Every one is a pure
resolution over state you pass in, so a script with no session, dungeon, or battle
machine can roll a whole fight. The guide "Using the rules without a session" walks
that path. Inside a session, [`osrlib.crawl.battle`][osrlib.crawl.battle] wraps these
same functions in the SRD's round sequence, so what a session logs is what these
functions return.

A round of B/X combat flows through the module in order:
[`roll_initiative`][osrlib.core.combat.roll_initiative] orders the actors,
[`resolve_attack`][osrlib.core.combat.resolve_attack] runs one attack end to end (the
roll, the immunity gate, the damage),
[`saving_throw`][osrlib.core.combat.saving_throw] resolves forced saves, and
[`check_morale`][osrlib.core.combat.check_morale] reports whether a side keeps
fighting. Start with `resolve_attack`: most of the module is the pieces it composes,
plus the specialized resolutions (breath weapons, gazes, splash weapons, energy drain)
and the shared targeting model ([`select_targets`][osrlib.core.combat.select_targets]).

Combatant-typed parameters (`attacker`, `defender`, `target`, and kin) follow one
convention across the whole library: they accept a
[`Character`][osrlib.core.character.Character] or a
[`MonsterInstance`][osrlib.core.monsters.MonsterInstance]. Both expose THAC0, attack
bonus, armour class, saving throws, hit points, and conditions, and these functions
read only those shared fields. NPC adventurers are `Character` instances, so there's no
third combatant type.

Resolutions also take an [`AttackContext`][osrlib.core.combat.AttackContext] that
describes the situation you assert (distance, cover-like situational modifiers,
back-stab position), the [`Ruleset`][osrlib.core.ruleset.Ruleset] in play, and an
[`RngStream`][osrlib.core.rng.RngStream] to draw from. There's no default stream and no
hidden global RNG: build an [`RngStreams`][osrlib.core.rng.RngStreams] from a master
seed and hand each function the stream it draws from. Battle resolution draws from
[`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM]. Four functions belong to another
subsystem and name its stream in their own entries:
[`roll_reaction`][osrlib.core.combat.roll_reaction] draws from the encounter stream,
[`natural_healing`][osrlib.core.combat.natural_healing] from the effects stream, and
[`drain_monster_hd`][osrlib.core.combat.drain_monster_hd] and
[`resolve_energy_drain`][osrlib.core.combat.resolve_energy_drain] from the advancement
stream. Resolutions return frozen result models that include an `events` tuple: a
session appends `result.events` to its log, and a caller working without one reads the
plain result fields.

The damage pipeline always runs in this order. First the immunity gate: if the
defender's `harmed_only_by` or energy defenses exclude the source, no damage is rolled
and the event reports that. Then the damage roll plus STR for melee, then the quality
and context doublings (brace, charge, back-stab), minimum 1 on a hit. Then the
reductions (the wraith's half-from-silver, the mummy's half-everything), floored but
never below 1. Last, the damage is applied: hit points floor at 0, fire and acid route
into a regenerating monster's non-regenerable ledger, and death emits at 0.

Validators ([`validate_attack`][osrlib.core.combat.validate_attack],
[`validate_breath`][osrlib.core.combat.validate_breath]) follow the same convention as
the rest of the library. They're pure pre-phase functions that return
[`Rejection`][osrlib.core.validation.Rejection] lists, with no RNG draws and no
mutation. A rejection is free (no roll, no time, no log entry), which is why holy water
against the living is *not* a rejection: it resolves normally and the damage pipeline
reports no effect. A free rejection would be a zero-cost undead detector.

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
from typing import Any

from pydantic import BaseModel, ConfigDict

from osrlib.core.classes import SavingThrows
from osrlib.core.clock import GameClock, TimeUnit
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
from osrlib.core.monsters import Element, MonsterAttack
from osrlib.core.rng import RngStream
from osrlib.core.ruleset import Ruleset
from osrlib.core.tables import ReactionResult, reaction_result, to_hit_ac
from osrlib.core.validation import Rejection

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

COMBAT_STREAM = "combat"
"""The stream key for battle-resolution draws: attacks, damage, saving throws, morale.

Pass it to [`RngStreams.get`][osrlib.core.rng.RngStreams.get] to get the stream every
function in this module draws from, except the four that belong to another subsystem
(reaction rolls, natural healing, and the two energy-drain functions). A
[`GameSession`][osrlib.crawl.session.GameSession] uses this key too, so a script that
uses it replays a session's fights draw for draw.

A stream key is a label: the same master seed and the same key always produce the same
sequence. You can pass a differently named stream instead, and a standalone script is
free to, but a saved session can't then replay your draws.

Examples:
    ```python
    from osrlib.core.combat import COMBAT_STREAM
    from osrlib.core.rng import RngStreams

    combat = RngStreams(master_seed=3).get(COMBAT_STREAM)
    assert combat.randbelow(20) + 1 == 20
    ```
"""

MELEE_REACH_FEET = 5
"""Melee attacks reach up to 5 feet.

[`validate_attack`][osrlib.core.combat.validate_attack] rejects a melee attack whose
context states a greater distance, and a weapon that's both melee and missile counts as
a missile use beyond this reach. To play at a different reach, state the distance you
want in the [`AttackContext`][osrlib.core.combat.AttackContext] instead of changing this
constant, which the whole module reads.
"""

_HELPLESS = (Condition.PARALYSED, Condition.ASLEEP)
_CANNOT_ACT = (Condition.DEAD, Condition.PETRIFIED, Condition.PARALYSED, Condition.ASLEEP)

# The three dual-listed gear items have fixed damage-source semantics. Holy water's
# combat facet presents the `holy` key, which only undead targets admit, and torch
# and burning oil deal fire damage because they're burning brands, which is what
# routes them into the troll's non-regenerable ledger.
_HOLY_ITEM_ID = "holy_water"
_FIRE_ITEM_IDS = ("torch", "oil_flask")

Attack = WeaponTemplate | CombatFacet | GearTemplate | MonsterAttack | MagicItemInstance | None
"""What a combatant attacks with. `None` is an unarmed attack (1d2).

Every attack-resolving function in this module takes one of these. Where each form
comes from: a [`WeaponTemplate`][osrlib.core.items.WeaponTemplate] or
[`GearTemplate`][osrlib.core.items.GearTemplate] from
[`load_equipment`][osrlib.data.load_equipment] (see the equipment id index), a
[`CombatFacet`][osrlib.core.items.CombatFacet] from a gear template's `combat` field
when you already have the item's fighting stats, a
[`MonsterAttack`][osrlib.core.monsters.MonsterAttack] from the attacking monster's
template, and a [`MagicItemInstance`][osrlib.core.items.MagicItemInstance] from a
character's inventory or from treasure generation.

A `MagicItemInstance` attack is an enchanted arm: its base weapon supplies the dice,
qualities, and ranges, and its template supplies the attack and damage bonuses, with a
versus clause swapping in its alternate bonus when the defender's template has the
referenced tag or id. It counts as magical for the immunity checks, cursed forms
included: a cursed sword is still a magic sword.

Use [`attack_facet`][osrlib.core.combat.attack_facet] to get the dice, qualities, and
ranges behind any of these forms without branching on the type yourself.

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
    """The situation you assert an attack resolves under.

    Build one and pass it to every attack-resolving function in this module. An empty
    `AttackContext()` is the plain case: a melee swing at an aware, standing defender.
    The model is frozen, so build a new one per attack instead of editing one.

    Everything here is a judgment the tabletop rules leave to the referee. These
    functions apply the rules to the context you give them, and working out that context
    (was the charge 60 feet? is the target unaware?) is your job, or the crawl layer's. A
    session fills it in from its own battle state, so a caller working inside one never
    builds an `AttackContext` by hand.

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
    """How far apart attacker and defender are.

    `None` states nothing. A melee weapon, and a weapon that's both melee and missile, then
    resolve as melee at reach. A missile-only weapon resolves as a missile use with no
    range-band modifier, because it can't be anything else. A distance over
    [`MELEE_REACH_FEET`][osrlib.core.combat.MELEE_REACH_FEET] makes a melee-and-missile
    weapon a missile use, and makes a melee-only attack a rejection.
    """

    situational_modifier: int = 0
    """The referee adjustment added to the attack roll.

    Cover at −1 to −4, the dozing dragon's +2, and the like.
    """

    defender_ally_ac_bonus: int = 0
    """An AC bonus an ally grants the defender.

    The Ring of Protection 5' Radius shielding the wearer's rank-mates is one. Adjacency
    is your spatial judgment, so the value arrives as context.
    """

    behind_target: bool = False
    """The attacker strikes from behind.

    The defender's shield doesn't count, and with `target_unaware` this is the thief's
    back-stab position.
    """

    target_unaware: bool = False
    """The defender is unaware of the attack.

    With `behind_target` it enables the back-stab attack bonus and damage multiplier.
    """

    defender_retreating: bool = False
    """The defender is withdrawing.

    The attacker gains +2 and the defender's shield doesn't count.
    """

    braced: bool = False
    """The attacker has set a brace-quality weapon against a charge, which doubles its damage."""

    charging: bool = False
    """The attacker is charging with a charge-quality weapon, which doubles its damage."""

    fired_last_round: bool = False
    """The weapon was fired in the previous round.

    Under the `weapon_reload` ruleset flag a reload-quality weapon is then rejected.
    """

    attacker_large: bool = False
    """The attacker is a large creature.

    This turns on the defender's `defensive_bonus` class ability, which is the halfling's
    AC bonus against large opponents.
    """

    lit: bool = False
    """The thrown oil flask is alight. Unlit oil deals no damage and no fire."""

    fixed_damage_option: int = 0
    """Which entry of a monster attack's `fixed_damage_options` to use.

    It matters only for monsters whose attack lists more than one fixed amount.
    """

    monster_missile: bool = False
    """The monster's attack is a small missile, so *protection from normal missiles* blocks it.

    A hobgoblin's arrow is one. Monster attacks are never marked automatically, because
    the hurled boulder is the counter-case.
    """


class DamageSource(BaseModel):
    """What a damage packet presents to the defender's defenses.

    Build one with [`damage_source_for`][osrlib.core.combat.damage_source_for] from an
    attacker, an attack, and a context, or construct one by hand for damage that isn't an
    attack: a spell, a trap, a fall. Hand it to
    [`check_immunity`][osrlib.core.combat.check_immunity] to ask whether the defender
    absorbs it, and to [`deal_damage`][osrlib.core.combat.deal_damage] to apply it. The
    model is frozen.

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
    """The material and enchantment keys the source presents: `silver`, `magic`, `holy`.

    A defender's `harmed_only_by` gate admits a source that presents one of the keys it
    names.
    """

    element: str | None = None
    """The energy element (`fire`, `cold`, `lightning`, and the like), or `None` for a physical source.

    Energy defenses, per-die reductions, and a regenerating monster's non-regenerable
    ledger all key off it.
    """

    magical: bool = False
    """Whether the source is magical.

    A nonmagical source is absorbed by a defense that turns aside anything but magic.
    """

    kind: str = "weapon"
    """The delivery: `weapon`, `unarmed`, `splash`, `breath`, `falling`, `effect`, or `spell`.

    It selects the saving throw category when a destructive death makes the victim's magic
    items save.
    """

    destructive: bool = False
    """Whether the source destroys the victim's equipment on a killing blow.

    Breath weapons and *lightning bolt* do.
    """

    missile: bool = False
    """Whether the source is a small missile, which *protection from normal missiles* blocks.

    Character weapon missiles and thrown splash items are marked automatically, and monster
    attacks never are, because the hurled boulder is the counter-case.
    `AttackContext.monster_missile` is how you say a hobgoblin's arrow is one.
    """


class AttackRollResult(BaseModel):
    """An attack roll's outcome. `roll` is `None` for the helpless auto-hit.

    Returned by [`attack_roll`][osrlib.core.combat.attack_roll], and on the `attack_roll`
    field of an [`AttackResult`][osrlib.core.combat.AttackResult]. Read `hit` to branch,
    and the rest to show the player the arithmetic. The model is frozen.
    """

    model_config = ConfigDict(frozen=True)

    hit: bool
    """Whether the attack landed."""

    auto: bool = False
    """Whether the hit needed no roll.

    That happens in melee against a defender that's paralysed or asleep, and against any
    defender whose `armour_class` is `None`, which is how a monster template says no hit
    roll is required. Green slime and yellow mould are the two that do. The roll fields are
    all `None` when this is true, and no draw was taken.
    """

    roll: int | None = None
    """The natural 1d20."""

    modifier: int = 0
    """The signed total of every modifier applied to the roll."""

    total: int | None = None
    """`roll` plus `modifier`."""

    required: int | None = None
    """The number `total` had to reach.

    It comes from the attack matrix, or from `THAC0 − AC` under the `thac0_arithmetic`
    ruleset flag.
    """

    natural: int | None = None
    """The natural roll when the always-hits-on-20 or always-misses-on-1 rule overrode the arithmetic.

    `None` when the arithmetic stood on its own. Use it to tell a lucky hit from an
    ordinary one.
    """

    events: tuple[Event, ...] = ()
    """The events this roll produced, ready to append to a session's log."""


class AttackResult(BaseModel):
    """A full attack resolution: the roll, the gate verdict, and any damage.

    Returned by [`resolve_attack`][osrlib.core.combat.resolve_attack] and
    [`resolve_splash_attack`][osrlib.core.combat.resolve_splash_attack]. The defender has
    already taken the damage by the time you have one of these. The result reports what
    happened instead of asking you to apply it. The model is frozen.
    """

    model_config = ConfigDict(frozen=True)

    attack_roll: AttackRollResult
    """The roll that opened the resolution."""

    absorbed: bool = False
    """Whether the defender's defenses turned the damage aside entirely, so no damage was rolled.

    A hit can be absorbed. A miss never is.
    """

    damage: int | None = None
    """The hit points the defender lost, or `None` when nothing was rolled.

    Nothing is rolled on a miss, on an absorbed hit, or when a sleeping defender is killed
    outright by a blade. An unlit oil flask that hits reports 0.
    """

    events: tuple[Event, ...] = ()
    """Every event the resolution produced, in order, ready to append to a session's log."""


class SaveResult(BaseModel):
    """A saving throw's outcome. `roll` is `None` for auto-save defenses.

    Returned by [`saving_throw`][osrlib.core.combat.saving_throw]. Read `passed` to
    branch. The saving throw itself changes nothing, so applying the consequence is
    yours. The model is frozen.
    """

    model_config = ConfigDict(frozen=True)

    passed: bool
    """Whether the save succeeded."""

    auto: bool = False
    """Whether the target passed without a roll.

    That happens when an energy defense auto-saves against a magical form of its own
    element. The roll fields are all `None` when this is true, and no draw was taken.
    """

    roll: int | None = None
    """The natural 1d20."""

    modifier: int = 0
    """The signed total of every modifier applied, including the one you passed."""

    required: int | None = None
    """The number `roll` plus `modifier` had to reach."""

    events: tuple[Event, ...] = ()
    """The events this save produced, ready to append to a session's log."""


class MoraleResult(BaseModel):
    """A morale check's outcome. `exempt` marks the morale scores 2 and 12, which never roll.

    Returned by [`check_morale`][osrlib.core.combat.check_morale] and by
    [`MoraleTracker.check`][osrlib.core.combat.MoraleTracker.check]. Acting on a broken
    side (fleeing, surrendering) is yours. The check only reports the verdict. The model
    is frozen.
    """

    model_config = ConfigDict(frozen=True)

    held: bool
    """Whether the side keeps fighting.

    A side with morale 2 never does, so this is false for it even though no roll was made.
    """

    exempt: bool = False
    """Whether the score put the side outside the roll.

    Morale 2 never fights and morale 12 never checks. The roll fields are `None` when this
    is true, and no draw was taken.
    """

    roll: int | None = None
    """The natural 2d6."""

    modifier: int = 0
    """The situational adjustment that was applied, after the clamp to ±2."""

    events: tuple[Event, ...] = ()
    """The events this check produced.

    They have referee visibility, because players learn a side's nerve from its behaviour.
    """


class Participant(BaseModel):
    """One initiative participant: a stable key, a side, and the modifier hooks.

    Build one per combatant and pass the sequence to
    [`roll_initiative`][osrlib.core.combat.roll_initiative], in the order you want ties
    and equal ranks resolved. The model is frozen.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    """The combatant's stable identifier, which comes back in the acting order.

    Use the entity id you already track, so you can map the order onto your own objects.
    """

    side: str
    """The side the combatant fights on.

    Under side initiative, everyone sharing a side acts on that side's single roll.
    """

    slow: bool = False
    """Whether the combatant wields a slow weapon.

    Slow actors always act after every non-slow actor, whatever they rolled.
    """

    modifier: int = 0
    """The individual-initiative modifier.

    It's used only when the `individual_initiative` ruleset flag is on. Compute it with
    [`participant_modifier`][osrlib.core.combat.participant_modifier], which gives
    characters their DEX modifier plus the halfling's class bonus, and monsters whatever
    modifier you supply.
    """


class InitiativeResult(BaseModel):
    """An initiative resolution: per-key rolls (re-rolls included) and the acting order.

    Returned by [`roll_initiative`][osrlib.core.combat.roll_initiative]. Iterate `order`
    to run the round. The model is frozen.
    """

    model_config = ConfigDict(frozen=True)

    mode: str
    """`side` when one roll covered each side, `individual` when each participant rolled its own.

    The `individual_initiative` ruleset flag selects which.
    """

    entries: tuple[InitiativeRoll, ...]
    """One [`InitiativeRoll`][osrlib.core.events.InitiativeRoll] per key that rolled.

    That's a side under side initiative and a participant under individual initiative. Its
    `rolls` tuple contains every die the key threw, so a tie that was re-rolled shows all
    of its attempts.
    """

    order: tuple[str, ...]
    """Every participant's key, in acting order. Slow actors come last."""

    events: tuple[Event, ...] = ()
    """The events this resolution produced, ready to append to a session's log."""


class SaveCategory(StrEnum):
    """The five saving throw categories.

    Pass one to [`saving_throw`][osrlib.core.combat.saving_throw]. Which category an
    effect forces is the effect's own business: a spell names its category, a breath
    weapon uses breath, and a destructive death makes magic items save under the
    category of the source that killed their owner.

    The wire values are lowercase and match the fields of
    [`SavingThrows`][osrlib.core.classes.SavingThrows]. They serialize into saves, so
    changing them is a `schema_version` bump.
    """

    DEATH = "death"
    """Death ray or poison, and the fallback category for anything with no category of its own."""

    WANDS = "wands"
    """Magic wands, and the category devices save under."""

    PARALYSIS = "paralysis"
    """Paralysis or petrification, which is what a petrifying gaze forces."""

    BREATH = "breath"
    """Breath attacks. The WIS magic-save modifier doesn't apply to this one."""

    SPELLS = "spells"
    """Spells, rods, and staves."""


class TargetingMode(StrEnum):
    """The shared targeting model's modes.

    Pass one to [`select_targets`][osrlib.core.combat.select_targets] along with the
    candidates it should choose from. Spells, breath weapons, and thrown weapons all
    resolve through these modes. Nothing in the kernel works out who is in range: you
    supply the candidate list, and inside a session the battle machine supplies it from
    its range-track geometry.

    The wire values are lowercase and travel in events. Changing them is a
    `schema_version` bump.
    """

    SELF = "self"
    """The caster or user only. The first candidate is taken."""

    SINGLE = "single"
    """One creature. The first candidate is taken."""

    UP_TO_N = "up_to_n"
    """The first N candidates in your order, with N either fixed or rolled (*hold person*'s 1d4)."""

    HD_BUDGET = "hd_budget"
    """Candidates taken weakest first until the Hit Dice budget runs out, as *sleep* spends its dice.

    A candidate too large for what's left is skipped and the selection continues.
    """

    AREA = "area"
    """Every candidate. The footprint is yours to resolve."""

    GAZE = "gaze"
    """Every candidate, for a gaze that reaches everyone engaged with the gazer."""


def _int_param(params: Mapping[str, Any], key: str, default: int = 0) -> int:
    """Read an integer param, which the type checker can't key by name in the validated union."""
    return int(params.get(key, default))


def _entity_id(combatant: Any) -> str:
    identifier = getattr(combatant, "id", None)
    return identifier if identifier is not None else getattr(combatant, "name", "unknown")


def alignments_differ(source: Any, target: Any) -> bool:
    """Return whether two combatants' operative alignments differ, for warding gates.

    The wards that turn aside creatures "of another alignment", *protection from evil* and
    the like, turn on this answer. [`attack_roll`][osrlib.core.combat.attack_roll] and
    [`saving_throw`][osrlib.core.combat.saving_throw] call it for you when a ward is in
    play, so you need it yourself only when you write a ward of your own.

    A combatant whose alignment is unresolved, which is `None` on a multi-option monster
    spawned without a choice, counts as being of another alignment. The ward errs
    protective.

    Args:
        source: The creature the ward is checked against, usually the attacker. A
            [`Character`][osrlib.core.character.Character] or a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance].
        target: The warded creature, a `Character` or a `MonsterInstance`.

    Returns:
        True when the alignments differ or either is unresolved.

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


def _magic_weapon_bonus(attack: Attack, defender: Any | None) -> int:
    """Return an enchanted arm's effective bonus against a defender.

    The base bonus applies unless a versus clause matches the defender's template
    (by category tag or template id), in which case the clause's alternate bonus
    swaps in. Characters have no template and never match a clause.
    """
    if not isinstance(attack, MagicItemInstance):
        return 0
    template = magic_item_template(attack)
    bonus = template.attack_bonus
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
    """Return the combat stats behind any attack.

    Use it to read an attack's damage dice, qualities, or missile ranges without branching
    on which form of [`Attack`][osrlib.core.combat.Attack] you have: a gear item returns
    its embedded combat facet, and an enchanted arm returns its base weapon. The
    resolution functions call this themselves, so you need it when you're displaying an
    attack or choosing between attacks rather than resolving one.

    Args:
        attack: The weapon, facet, gear item, magic instance, or `None`.

    Returns:
        The facet with the dice, qualities, and ranges. `None` for an unarmed attack, a
            monster's natural attack, a gear item with no fighting stats, and a magic item
            whose template names no base weapon, which is most of them: armour, rings,
            potions, and the rest. None of those has a facet to return.

    Examples:
        ```python
        from osrlib.core.combat import attack_facet
        from osrlib.data import load_equipment

        catalog = load_equipment()
        assert attack_facet(catalog.get("sword")).damage == "1d8"
        assert attack_facet(catalog.get("oil_flask")).damage == "1d8"  # the gear item's facet
        assert attack_facet(catalog.get("torch")).damage == "1d4"
        assert attack_facet(None) is None  # unarmed: the 1d2 rule lives in damage_roll
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


def melee_modifier_for(combatant: Any) -> int:
    """Return a combatant's melee attack-and-damage modifier, `strength_set` aware.

    [`attack_roll`][osrlib.core.combat.attack_roll] and
    [`damage_roll`][osrlib.core.combat.damage_roll] add this themselves on every melee
    attack, so call it to show a character sheet's melee bonus or preview a swing, not to
    feed it back into a resolution.

    A `strength_set` modifier, which is what the Gauntlets of Ogre Power (18) and the
    Ring of Weakness (3) impose, replaces the STR score the ability table derives the
    melee modifier from. Monsters have no STR score and keep their intrinsic 0.

    Args:
        combatant: The attacking combatant, a
            [`Character`][osrlib.core.character.Character] or a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance].

    Returns:
        The signed melee modifier, applied to both the attack roll and the damage.

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


def damage_source_for(attacker: Any, attack: Attack, context: AttackContext) -> DamageSource:
    """Build the damage source an attack presents to the defender's defenses.

    [`resolve_attack`][osrlib.core.combat.resolve_attack] builds one for you on every hit.
    Call it yourself to ask [`check_immunity`][osrlib.core.combat.check_immunity] whether
    an attack would land before spending a round on it, or to hand
    [`deal_damage`][osrlib.core.combat.deal_damage] a source when you're applying damage
    outside an attack. For damage that isn't an attack, like a trap or a spell, construct
    a [`DamageSource`][osrlib.core.combat.DamageSource] directly.

    What each attack presents: a silver weapon presents `silver`, holy water presents
    `holy`, and a torch or burning oil deals fire, because they're burning brands, which
    is what routes them into a regenerating monster's non-regenerable ledger. Monster
    natural attacks are mundane. The `hd5_counts_as_magical` ruleset flag is resolved from
    the attacker by `check_immunity`, not here. A wielder under *striking* presents `magic`
    on weapon attacks but never on unarmed ones, because the enchantment belongs to the
    weapon and sits on the wielder only because item instances have no ids of their own.
    Whether the source counts as a small missile is recorded here for the immunity gate.

    Args:
        attacker: The attacking combatant, a
            [`Character`][osrlib.core.character.Character] or a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance].
        attack: The weapon, facet, gear item, or monster attack (`None` for unarmed).
        context: The attack context. Set `lit` when the oil flask is alight, and
            `distance_feet` when a melee-and-missile weapon is thrown.

    Returns:
        The frozen damage source, ready for `check_immunity` and `deal_damage`.

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
    attacker: Any, defender: Any, attack: Attack, context: AttackContext, *, ruleset: Ruleset
) -> list[Rejection]:
    """Validate an attack: the pure pre-phase, with no RNG draws and no mutation.

    Call this before [`resolve_attack`][osrlib.core.combat.resolve_attack] when the attack
    might be illegal and you'd rather tell the player why than resolve it. `resolve_attack`
    doesn't call it for you, because a rejection costs nothing and what to do with one is
    yours to decide. An empty list means the attack may be rolled.

    It rejects an attacker who is dead, petrified, paralysed, asleep, weakened, or blind.
    It rejects a missile shot past its long range, a reload-quality weapon fired two rounds
    running under the `weapon_reload` ruleset flag, and a melee attack at a stated distance
    beyond [`MELEE_REACH_FEET`][osrlib.core.combat.MELEE_REACH_FEET]. A blocked attacker
    produces one rejection and stops, so the list names the first reason rather than every
    reason.

    Args:
        attacker: The attacking combatant, a
            [`Character`][osrlib.core.character.Character] or a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance].
        defender: The defending combatant, a `Character` or a `MonsterInstance`.
        attack: The weapon, facet, gear item, or monster attack (`None` for unarmed).
        context: The situation you assert. This reads `distance_feet` and
            `fired_last_round`.
        ruleset: The ruleset in play. `weapon_reload` is enforced here.

    Returns:
        Structured [`Rejection`][osrlib.core.validation.Rejection] values, each with a
            code and the parameters a message needs. Empty when the attack may be rolled.

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
    # own and the set value, never worse, which for descending AC is the minimum.
    set_kind = "ac_set_vs_missile" if missile else "ac_set"
    for value in modifier_values(defender, set_kind):
        ac = min(ac, value)
    # AC-bonus modifiers (the potion of invulnerability's ±2) improve or worsen
    # descending AC directly. Equipped armour and shields are already in the
    # character's own `armour_class` property.
    ac -= modifier_total(defender, "ac_bonus")
    # An ally's aura (the 5'-radius protection ring), which the caller asserts.
    ac -= context.defender_ally_ac_bonus
    return ac


def attack_roll(
    attacker: Any,
    defender: Any,
    attack: Attack,
    *,
    context: AttackContext,
    ruleset: Ruleset,
    stream: RngStream,
) -> AttackRollResult:
    """Roll an attack: 1d20 plus modifiers against the defender's armour class.

    This is the first step of [`resolve_attack`][osrlib.core.combat.resolve_attack], which
    is what you normally call: it rolls, checks the defender's immunities, rolls damage,
    and applies it. Call `attack_roll` on its own when you want the hit decision without
    the damage, as for an attack whose effect isn't hit points.

    The roll gathers every modifier the situation supplies: the attacker's STR in melee,
    or its missile bonus and range band at distance, the back-stab bonus behind an unaware
    target, +2 against a retreating defender, an enchanted arm's bonus, spell bonuses and
    penalties on either side, and the context's `situational_modifier`. The defender's
    armour class takes its own adjustments the same way, so a shield doesn't count from
    behind and an ally's ward does.

    Two defenders are hit automatically, with no roll taken and no draw consumed: one
    that's paralysed or asleep and struck in melee, and one whose `armour_class` is `None`,
    which is how a monster template says no hit roll is required. Green slime and yellow
    mould are the two monsters that say it.

    A natural 20 always hits and a natural 1 always misses. The target number comes from
    the attack matrix, or from unclamped `THAC0 − AC` under the `thac0_arithmetic`
    ruleset flag.

    Args:
        attacker: The attacking combatant, a
            [`Character`][osrlib.core.character.Character] or a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance].
        defender: The defending combatant, a `Character` or a `MonsterInstance`.
        attack: The weapon, facet, gear item, or monster attack (`None` for unarmed).
        context: The situation you assert.
        ruleset: The ruleset in play.
        stream: The stream to draw the d20 from, conventionally
            [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM] from your
            [`RngStreams`][osrlib.core.rng.RngStreams]. One draw on a rolled attack,
            none on an automatic hit.

    Returns:
        The roll outcome, with its events.

    Raises:
        ValueError: If the defender exposes no armour class, which means the object isn't
            a combatant.

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
    modifier += _magic_weapon_bonus(attack, defender)
    # Spell stat modifiers: the attacker's own bonuses (*bless* and *blight*) and
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
        # own probabilities are already better.
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


def check_immunity(defender: Any, source: DamageSource, *, ruleset: Ruleset, attacker: Any | None = None) -> bool:
    """Return True when the defender's defenses absorb the source: no damage is rolled.

    [`resolve_attack`][osrlib.core.combat.resolve_attack] and
    [`resolve_breath`][osrlib.core.combat.resolve_breath] run this gate on every hit, so
    call it yourself to ask whether a weapon can hurt a monster before the party spends
    rounds finding out. Build the `source` with
    [`damage_source_for`][osrlib.core.combat.damage_source_for].

    The rules this gate resolves. A monster's `harmed_only_by` list admits only sources
    presenting one of its keys, so a steel sword bounces off a werewolf and a silver
    dagger doesn't. The `holy` key is admitted through any such gate on an undead target
    and has no effect on anything else, which is why holy water against the living resolves
    as a hit that does nothing rather than as a rejection. A monster that uses fire ignores
    burning oil. An energy defense turns aside its own element, and turns aside the
    nonmagical form of it even when it doesn't turn aside the magical one. Under the
    `hd5_counts_as_magical` ruleset flag, an attacker of 5 or more Hit Dice, or one with a
    silver-or-magic gate of its own, gets through a gate that asks only for silver or
    magic. A defender under *protection from normal missiles* absorbs any small nonmagical
    missile, so an arrow or a thrown flask is blocked and a hurled boulder or an enchanted
    arrow isn't.

    Args:
        defender: The defending combatant, a
            [`Character`][osrlib.core.character.Character] or a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance].
        source: The damage source presented.
        ruleset: The ruleset in play.
        attacker: The attacking combatant, a `Character` or a `MonsterInstance`. Only
            `hd5_counts_as_magical` reads it, and the flag cannot apply without it.

    Returns:
        True when the hit is absorbed and no damage should be rolled.

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
            if attacker_template.hit_dice.count >= 5:
                return False
            attacker_gate = {key.value for key in attacker_template.defenses.harmed_only_by}
            if attacker_gate and attacker_gate <= {"silver", "magic"}:
                return False
    return True


def damage_roll(
    attacker: Any,
    attack: Attack,
    *,
    context: AttackContext,
    ruleset: Ruleset,
    stream: RngStream,
    defender: Any | None = None,
) -> RollResult:
    """Roll an attack's damage: dice, STR for melee, doublings, minimum 1.

    [`resolve_attack`][osrlib.core.combat.resolve_attack] calls this after a hit clears
    the immunity gate, and passes the result to
    [`deal_damage`][osrlib.core.combat.deal_damage]. Call it yourself to preview a
    weapon's damage, or when you're applying the damage some other way. It rolls only:
    nothing is subtracted from the defender here, and the defender's reductions are
    applied later by `deal_damage`.

    What goes into the total, in order: the attack's dice, the attacker's melee modifier
    on a melee attack, an enchanted arm's damage bonus with a versus clause swapping in
    its alternate against a matching `defender`, spell damage bonuses, then the doublings.
    Those are a braced weapon meeting a charge, a charge of the attacker's own, the
    thief's back-stab multiplier, and the item multipliers, which are giant strength on
    weapon attacks and growth on melee attacks. The total is never below 1.

    With the `variable_weapon_damage` ruleset flag off, every weapon and gear combat facet
    deals 1d6 instead of its listed dice. Unarmed attacks stay 1d2, which is a rule of
    their own rather than weapon damage, and monster damage is untouched. The Girdle of
    Giant Strength branches on the same flag: twice normal weapon damage under the
    default, and its printed 2d8 with the flag off.

    Args:
        attacker: The attacking combatant, a
            [`Character`][osrlib.core.character.Character] or a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance].
        attack: The weapon, facet, gear item, or monster attack (`None` for unarmed).
        context: The situation you assert. Its `braced`, `charging`, `behind_target`, and
            `target_unaware` fields drive the doublings.
        ruleset: The ruleset in play.
        stream: The stream to draw the damage dice from, conventionally
            [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM].
        defender: The defender, a `Character` or a `MonsterInstance`. Only an enchanted
            arm's versus clause reads it, so an ordinary weapon needs no defender.

    Returns:
        The damage roll. `rolls` contains the individual dice and `total` the final amount,
            which is at least 1. A monster attack that deals no hit point damage, like a
            wight's touch, returns a total of 0, because its effect isn't damage.

    Raises:
        ValueError: If the attack has no combat facet to roll damage from. That's a gear
            item with no fighting stats, like a lantern, and a magic item whose template
            names no base weapon, like a suit of Armour +1.

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
        # the one wired item whose mechanics branch on a `Ruleset` flag.
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
    amount += _magic_weapon_bonus(attack, defender)
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
    qualities = _qualities(attack)
    if context.braced and WeaponQuality.BRACE in qualities:
        amount *= 2
    if context.charging and WeaponQuality.CHARGE in qualities:
        amount *= 2
    if not isinstance(attack, MonsterAttack):
        # Item damage multipliers apply after the flat bonuses, beside the quality
        # doublings: giant strength on weapon attacks, growth on melee attacks
        # (unarmed included), and the girdle's double under the variable-damage
        # default.
        if attack is not None:
            multiplier = modifier_total(attacker, "damage_multiplier")
            if multiplier > 1:
                amount *= multiplier
            if girdle is not None and ruleset.variable_weapon_damage:
                amount *= 2
        if not missile:
            melee_multiplier = modifier_total(attacker, "melee_damage_multiplier")
            if melee_multiplier > 1:
                amount *= melee_multiplier
    if context.behind_target and context.target_unaware:
        back_stab = _class_ability_params(attacker, "back_stab")
        if back_stab is not None:
            amount *= int(back_stab.get("damage_multiplier", 1))
    return RollResult(rolls=rolls, modifier=0, multiplier=1, total=max(1, amount))


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
    target: Any,
    amount: int,
    *,
    source: DamageSource,
    attacker_id: str | None = None,
    rolls: tuple[int, ...] = (),
    clock: GameClock | None = None,
    ruleset: Ruleset | None = None,
    stream: RngStream | None = None,
) -> list[Event]:
    """Apply damage: reductions, the hit point floor, ledgers, and death.

    This mutates the target. [`resolve_attack`][osrlib.core.combat.resolve_attack] calls
    it for you on a hit, so call it yourself for damage that isn't an attack: a trap, a
    fall (pair it with [`falling_damage`][osrlib.core.combat.falling_damage]), a spell, or
    an effect ticking. Roll the amount first, with
    [`damage_roll`][osrlib.core.combat.damage_roll] or [`roll`][osrlib.core.dice.roll],
    and describe it with a [`DamageSource`][osrlib.core.combat.DamageSource]. This
    function draws no dice of its own unless a destructive kill makes magic items save.

    What it does, in order. Divisor reductions from the target's defenses apply first,
    like the wraith's half-from-silver and the mummy's half-from-everything, each flooring
    at 1. Then element-scoped per-die reductions, which take 1 point per damage die rolled
    and never take a die below 1, so a source that rolled no dice has nothing to reduce.
    Hit points then fall, floored at 0. Fire and acid against a regenerating monster whose
    regeneration they block also accrue in its non-regenerable ledger, capped at its
    maximum. Such a monster dies permanently only when its regeneration names a `revive`
    entry, meaning it's the kind that gets back up, and the ledger alone reaches the
    maximum. At 0 hit points the target dies, and a destructive source then destroys what
    it carried.

    Args:
        target: The creature taking damage, a
            [`Character`][osrlib.core.character.Character] or a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance]. Mutated in place.
        amount: The rolled amount, before reductions.
        source: The damage source, which selects the reductions, the ledger, and whether
            a kill destroys equipment.
        attacker_id: The attacker's entity id, which appears in the event.
        rolls: The raw damage dice, which appear in the event and set how much a per-die
            reduction can take.
        clock: The game clock. When passed, the target's `last_damaged_round` is stamped,
            which is what delays a regenerating monster's healing.
        ruleset: The ruleset in play, for the magic-item death save on a destructive kill.
        stream: The stream the magic-item death save draws from, which is the stream of
            whichever subsystem is resolving. Needed only when a destructive source can
            kill a target carrying magic items.

    Returns:
        The events in order, ready to append to a session's log: `DamageDealtEvent`, then
            `HitPointsReportedEvent`, then on a killing blow the death events, and last an
            `EquipmentDestroyedEvent` when a destructive source killed a target that was
            carrying something.

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
    target.current_hp = max(0, target.current_hp - amount)
    if clock is not None and hasattr(target, "last_damaged_round"):
        target.last_damaged_round = clock.rounds
    regeneration = _monster_ability_params(target, "regeneration")
    blocked = False
    newly_permanent = False
    if regeneration is not None and source.element is not None:
        blocked_by = tuple(str(element) for element in regeneration.get("blocked_by", ()))
        blocked = source.element in blocked_by
        if blocked:
            before = target.nonregen_damage
            target.nonregen_damage = min(target.max_hp, target.nonregen_damage + amount)
            newly_permanent = before < target.max_hp <= target.nonregen_damage
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
            and target.nonregen_damage >= target.max_hp
        )
        events.extend(kill(target, permanent=permanent))
        if source.destructive:
            events.extend(destroy_equipment(target, source=source, ruleset=ruleset, stream=stream))
    elif already_dead and newly_permanent:
        events.append(DeathEvent(code="combat.death.permanent", target_id=target_id))
    return events


_DEATH_SAVE_CATEGORIES = {"breath": SaveCategory.BREATH, "spell": SaveCategory.SPELLS, "device": SaveCategory.WANDS}


def destroy_equipment(
    target: Any,
    *,
    source: DamageSource | None = None,
    ruleset: Ruleset | None = None,
    stream: RngStream | None = None,
) -> list[Event]:
    """Destroy a victim's carried equipment: the destructive-death outcome.

    [`deal_damage`][osrlib.core.combat.deal_damage] calls this when a destructive source
    lands the killing blow, so you rarely call it yourself. *Disintegrate* does, because
    the material form it destroys includes what the victim carried.

    Under the `magic_item_death_save` ruleset flag, which is on by default, each magic
    item in the doomed inventory rolls 1d20 against the owner's saving throw value for the
    destructive source's category: a breath weapon saves versus breath, a destructive
    spell versus spells, a device versus wands, anything else versus death. The item adds
    its best combat bonus, meaning the highest of its attack, damage, and armour class
    bonuses, so a cursed item saves at its penalty. Survivors stay in the item list and
    the event's `saved_items` names their instance ids, and a session puts them in a drop
    pile at the victim's cell, because surviving the blast but not the looting would be no
    survival. The rolls themselves are silent, and the event reports the outcome.

    Args:
        target: The victim, a [`Character`][osrlib.core.character.Character] or a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance]. Its inventory is
            emptied except for saved magic items, and what it wielded, wore, and had on
            its fingers is cleared.
        source: The destructive damage source, which selects the saving throw category.
            `None` means the death category.
        ruleset: The ruleset in play. `None` skips the save and everything burns.
        stream: The stream the item saves draw from, one draw per magic item.

    Returns:
        The destruction event, naming what burned and what saved. Nothing for an empty
            inventory.

    Raises:
        ValueError: If the save is on, the victim carries magic items, and no stream was
            supplied. The save can't roll without one.

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
    attacker: Any,
    defender: Any,
    attack: Attack,
    *,
    context: AttackContext,
    ruleset: Ruleset,
    stream: RngStream,
    clock: GameClock | None = None,
) -> AttackResult:
    """Resolve one attack end to end: roll, gate, damage.

    This is the module's entry point, and the one function most callers need: it rolls the
    attack, checks the defender's immunities, rolls the damage, and applies it. The
    defender is mutated, so the hit points are already gone by the time you read the
    result. To ask first whether the attack is legal, call
    [`validate_attack`][osrlib.core.combat.validate_attack], which draws nothing and
    changes nothing. For a thrown flask of oil or holy water, call
    [`resolve_splash_attack`][osrlib.core.combat.resolve_splash_attack] instead, which
    adds the second application. For a breath weapon, a gaze, or an energy drain, use the
    specialized resolutions.

    The pipeline order is fixed. The attack roll comes first
    ([`attack_roll`][osrlib.core.combat.attack_roll]). On a hit, the immunity gate
    ([`check_immunity`][osrlib.core.combat.check_immunity]): if the defender's defenses
    exclude the source, no damage is rolled and the absorbed event reports that. Otherwise
    the damage roll ([`damage_roll`][osrlib.core.combat.damage_roll]) and its application
    ([`deal_damage`][osrlib.core.combat.deal_damage]).

    Two hits take a shorter path. A sleeping defender struck in melee with a bladed weapon
    dies outright, with no damage rolled, once the immunity gate has run. An oil flask
    thrown unlit does nothing on a hit and reports 0 damage, and you can compile a pool
    from it instead with
    [`burning_oil_pool_definition`][osrlib.core.combat.burning_oil_pool_definition].

    Args:
        attacker: The attacking combatant, a
            [`Character`][osrlib.core.character.Character] or a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance].
        defender: The defending combatant, a `Character` or a `MonsterInstance`. Mutated
            in place when damage lands.
        attack: The weapon, facet, gear item, or monster attack (`None` for unarmed).
        context: The situation you assert. `AttackContext()` is the plain melee case.
        ruleset: The ruleset in play.
        stream: The stream every draw in the resolution comes from, conventionally
            [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM]. An automatic hit costs no
            draw. A miss, an absorbed hit, a sleeping defender killed by a blade, and an
            unlit oil flask each cost the attack roll alone. An ordinary hit costs the
            attack roll plus the damage dice, and the extra dice on top of those when the
            attacker is under *striking*.
        clock: The game clock. When passed, the damage stamps the defender's
            `last_damaged_round`, which is what delays a regenerating monster's healing.

    Returns:
        The full resolution with its events.

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
        # Unlit oil deals no damage. The caller may compile a pool instead.
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
    """Build the splash weapon's dousing effect: one more application next round.

    [`resolve_splash_attack`][osrlib.core.combat.resolve_splash_attack] builds and attaches
    this for you on a damaging hit, so call it yourself only when you're attaching the
    douse through an [`EffectsLedger`][osrlib.core.effects.EffectsLedger] of your own.
    Build the `source` with
    [`damage_source_for`][osrlib.core.combat.damage_source_for] from the same attack and
    context, so the second application presents the same keys and element as the first.

    The rules read "inflicted for two rounds" as two applications: the hit's damage now,
    and the same dice once more when the effect expires at the next round boundary.

    Args:
        attack: The splash item. Its combat facet's damage dice are rolled again for the
            second application.
        source: The damage source the hit presented, whose keys and element are presented
            again.

    Returns:
        A one-round [`EffectDefinition`][osrlib.core.effects.EffectDefinition] whose
            expiry deals the second application. Attach it with
            [`EffectsLedger.attach`][osrlib.core.effects.EffectsLedger.attach].

    Raises:
        ValueError: If the attack has no combat facet, so there are no dice for the second
            application.

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
    """Build the burning oil pool: a location-attached fire that burns for one turn.

    A flask of oil thrown unlit does no damage, and this is what you do with it instead:
    it pools, and someone sets it alight. Once lit the pool burns for one turn and deals
    1d8 to creatures passing through. Who passes through is your assertion, and you apply
    the damage yourself with [`deal_damage`][osrlib.core.combat.deal_damage], because
    nothing in the kernel tracks where creatures walk.

    The definition takes no arguments because the pool is the same every time: 1d8 of fire
    in a 3-foot radius for one turn. Attach it to a location with
    [`EffectsLedger.attach`][osrlib.core.effects.EffectsLedger.attach].

    Returns:
        The one-turn pool [`EffectDefinition`][osrlib.core.effects.EffectDefinition],
            whose `params` contain the dice, the element, and the radius.

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
    attacker: Any,
    defender: Any,
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
    """Resolve a thrown splash weapon: the attack, the first application, the douse.

    Use this rather than [`resolve_attack`][osrlib.core.combat.resolve_attack] for holy
    water and burning oil, because a splash weapon damages its target twice: once on the
    hit, and once more when the douse expires at the next round boundary. This function
    runs `resolve_attack` and then attaches the douse, so it needs the effects machinery a
    bare attack doesn't: a ledger, a clock, an id allocator, and a registry.

    Holy water against a living target, and burning oil against a monster that uses fire,
    resolve as a hit that does nothing rather than as a rejection. A rejection is free,
    and a free one would give away what B/X keeps hidden until it matters.

    Args:
        attacker: The throwing combatant, a
            [`Character`][osrlib.core.character.Character] or a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance].
        defender: The target, a `Character` or a `MonsterInstance`. Mutated in place when
            damage lands.
        attack: The splash gear item, which is holy water or a flask of oil.
        context: The situation you assert. Oil does nothing unless `lit` is true.
        ruleset: The ruleset in play.
        stream: The stream every draw comes from, conventionally
            [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM].
        ledger: The [`EffectsLedger`][osrlib.core.effects.EffectsLedger] the douse
            attaches through, and the one whose expiries you must run for the second
            application to land.
        clock: The game clock, which dates the douse's expiry.
        allocator: The [`IdAllocator`][osrlib.core.monsters.IdAllocator] that mints the
            douse effect's id.
        registry: Live objects by entity id, so the ledger can find the target again.

    Returns:
        The full resolution with its events, the attachment event last when a douse was
            attached.

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


def participant_modifier(combatant: Any, *, monster_modifier: int = 0) -> int:
    """Return a combatant's individual-initiative modifier.

    Use it to fill the `modifier` field of a
    [`Participant`][osrlib.core.combat.Participant] before calling
    [`roll_initiative`][osrlib.core.combat.roll_initiative]. The modifier matters only
    when the `individual_initiative` ruleset flag is on, since side initiative rolls once
    per side and applies no modifier.

    A character gets its DEX modifier plus the halfling's `initiative_bonus` class tag. A
    monster gets whatever you pass, because the tabletop rules leave monster initiative
    modifiers to the referee.

    Args:
        combatant: The combatant, a [`Character`][osrlib.core.character.Character] or a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance].
        monster_modifier: The modifier to use for a monster. Ignored for characters.

    Returns:
        The signed modifier.

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
    """Roll initiative: by side, or per participant under `individual_initiative`.

    Call this once at the top of each combat round, then act through the `order` it
    returns. Build one [`Participant`][osrlib.core.combat.Participant] per combatant, in
    the order you want equal ranks broken, and compute each modifier with
    [`participant_modifier`][osrlib.core.combat.participant_modifier]. The function reads
    nothing off your combatants and changes nothing: it works entirely from the
    participants you describe.

    Under side initiative, which is the default, each side rolls one 1d6 and everyone on
    that side acts together. Under the `individual_initiative` ruleset flag, each
    participant rolls its own die and adds its modifier.

    Ties always re-roll. The tabletop rules offer "re-roll or simultaneous", and osrlib
    re-rolls because simultaneous resolution is a different combat model. Tied sides, or
    tied individuals among themselves, re-roll in stable input order until the totals are
    distinct, and each re-roll takes another draw, so a round with a tie costs more draws
    than one without. Slow-weapon actors act after every non-slow actor, ordered among
    themselves by their side's initiative, or by their own under individual initiative,
    and then by input order.

    Args:
        participants: One entry per combatant, in the order that breaks equal ranks.
        ruleset: The ruleset in play. `individual_initiative` selects the mode.
        stream: The stream the d6s come from, conventionally
            [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM]. One draw per side, or
            per participant under individual initiative, plus one for each re-roll a tie
            forces.

    Returns:
        The rolls, re-rolls included, and the full acting order.

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
    """Check morale: 2d6 against the morale score. Over the score means flee or surrender.

    Call this when a side's nerve is in question, and act on the result yourself: nothing
    here makes anyone flee or surrender. Ask
    [`morale_triggers`][osrlib.core.combat.morale_triggers] which of a side's states call
    for a check, and use [`MoraleTracker`][osrlib.core.combat.MoraleTracker] instead of
    this function when you want the rule that a side which holds twice stops checking. The
    morale score, ML on a monster's stat block, is its `morale` value from its template.

    The check is over a side or group, not a creature, so a creature's own spell morale
    bonus reaches it through `modifier`: read it with
    [`morale_modifier`][osrlib.core.combat.morale_modifier] and fold it into the
    situational adjustment. A side with ML 2 never fights and one with ML 12 never checks.
    Both are exempt: no roll is taken, no draw is consumed, and adjustments don't reach
    them.

    Args:
        subject: The side or group key, which appears in the event so a listener can name
            the side whose nerve broke.
        score: The morale score, which is 2 to 12 on a stat block. Any integer is accepted:
            2 or below is exempt and never fights, 12 or above is exempt and always does.
        modifier: The situational adjustment, clamped to ±2 however large a value you
            pass.
        stream: The stream the 2d6 comes from, conventionally
            [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM]. Two draws on a rolled
            check, none on an exempt one.

    Returns:
        The outcome. Its event states the verdict in `held` on every code, exempt checks
            included, so a listener never has to read it back off the score. The events have
            referee visibility, because players read a side's nerve from its behaviour rather
            than from a number.

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
    """A reaction roll's outcome: the raw 2d6, the modifier, and the table band.

    Returned by [`roll_reaction`][osrlib.core.combat.roll_reaction]. What the monsters do
    about their reaction is yours. The model is frozen.
    """

    model_config = ConfigDict(frozen=True)

    result: ReactionResult
    """The band the total fell in, from hostile through friendly."""

    roll: int
    """The natural 2d6."""

    modifier: int = 0
    """The modifier you supplied."""

    total: int
    """`roll` plus `modifier`.

    It can fall outside 2 to 12, in which case the table's outermost band applies.
    """

    events: tuple[Event, ...] = ()
    """The events this roll produced.

    They have referee visibility, because players read a monster's mood from its behaviour.
    """


def roll_reaction(*, modifier: int = 0, stream: RngStream) -> ReactionRollResult:
    """Roll a monster reaction: 2d6 plus the modifier against the reaction table.

    Roll this when the party meets monsters that aren't already fighting, to learn whether
    they attack, wait, or talk. The result changes nothing on its own: acting on it is
    yours, and a hostile band is where
    [`start_battle`][osrlib.crawl.battle.start_battle] comes in for a session.

    The CHA modifier is yours to supply, from the speaking character's
    `npc_reaction_modifier`, because the tabletop rules apply it only when one particular
    character tries to speak with the monsters. A total outside 2 to 12 falls into the
    table's outermost band rather than erroring.

    Args:
        modifier: The speaking character's CHA reaction modifier, when one applies.
        stream: The stream the 2d6 comes from. Reaction rolls belong to the encounter
            procedure rather than to battle, so a session draws them from
            [`ENCOUNTER_STREAM`][osrlib.crawl.session.ENCOUNTER_STREAM]. Use the same key
            in a standalone script to replay a session's encounters. Two draws.

    Returns:
        The outcome. Its event has referee visibility, because players learn a monster's
            mood from its behaviour, just as they do its morale.

    Examples:
        ```python
        from osrlib.core.combat import roll_reaction
        from osrlib.core.rng import RngStreams

        encounter = RngStreams(master_seed=3).get("encounter")

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
    """The two-passed-checks memory: after two held checks, no further checks.

    Build one per encounter and check morale through it instead of calling
    [`check_morale`][osrlib.core.combat.check_morale] directly, so a side that has held
    twice stops being asked. The rule it keeps: "If a monster passes two morale checks in
    an encounter, it will fight until killed, with no further checks."

    Unlike the result models in this module, a tracker is mutable, because its job is to
    keep a count across the encounter. Throw it away when the encounter ends. A new
    encounter starts the count again.
    """

    model_config = ConfigDict(validate_assignment=True)

    passed: dict[str, int] = {}
    """How many checks each subject has held, keyed by the same subject key you pass to `check`.

    A subject at 2 is never checked again.
    """

    def check(self, subject: str, score: int, *, modifier: int = 0, stream: RngStream) -> MoraleResult | None:
        """Check morale unless the subject has already passed twice.

        [`check_morale`][osrlib.core.combat.check_morale] does the check itself, and this
        adds the count. An exempt subject, at morale 2 or 12, never counts towards the two,
        because it never rolled.

        Args:
            subject: The side or group key. The tracker counts per subject, so two groups
                of goblins with different keys are counted apart.
            score: The morale score, which is 2 to 12 on a stat block. Any integer is
                accepted, and 2 or below and 12 or above are exempt, as in
                [`check_morale`][osrlib.core.combat.check_morale].
            modifier: The situational adjustment, clamped to ±2.
            stream: The stream the 2d6 comes from, conventionally
                [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM]. No draw is taken
                once the subject has held twice.

        Returns:
            The result, or `None` when no further checks are made and the subject fights
                until killed.

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


def incapacitated(combatant: Any) -> bool:
    """Return whether a combatant counts as incapacitated for morale triggers.

    [`morale_triggers`][osrlib.core.combat.morale_triggers] counts a side's incapacitated
    members with this, and [`resolve_gaze`][osrlib.core.combat.resolve_gaze] skips them.
    Call it yourself to ask whether a combatant is out of the fight, whichever way it went
    out. To ask whether it can still move, use
    [`cannot_move`][osrlib.core.combat.cannot_move], which adds entanglement.

    The tabletop rules say "slain, paralysed, etc". osrlib reads that as dead, paralysed,
    petrified, or asleep.

    Args:
        combatant: The combatant, a [`Character`][osrlib.core.character.Character] or a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance].

    Returns:
        True when incapacitated.

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


def cannot_move(combatant: Any) -> bool:
    """Return whether a combatant cannot move.

    Ask this before letting a combatant move, flee, or close to melee. It's
    [`incapacitated`][osrlib.core.combat.incapacitated] plus entanglement: a creature
    caught in a *web* "can't move", and a creature that's dead, paralysed, petrified, or
    asleep can't either. Movement itself isn't this module's business, so nothing here
    calls it for you.

    Args:
        combatant: The combatant, a [`Character`][osrlib.core.character.Character] or a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance].

    Returns:
        True when movement is impossible.

    Examples:
        ```python
        from osrlib.core.combat import cannot_move
        from osrlib.core.effects import ActiveCondition, Condition
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        spawn = RngStreams(master_seed=3).get(MONSTER_SPAWN_STREAM)
        goblin = spawn_monster(load_monsters().get("goblin"), id="goblin-1", stream=spawn)
        assert not cannot_move(goblin)

        goblin.conditions = (ActiveCondition(condition=Condition.ENTANGLED),)  # a web caught it
        assert cannot_move(goblin)
        ```
    """
    return incapacitated(combatant) or has_condition(combatant, Condition.ENTANGLED)


def morale_modifier(combatant: Any) -> int:
    """Return a combatant's spell morale modifier, from *bless*, *blight*, and their kin.

    [`check_morale`][osrlib.core.combat.check_morale] takes a side key and a score, never a
    creature, so a spell's morale modifier can't reach it on its own. Read it here and fold
    it into the `modifier` you pass. A spell morale modifier counts inside the same ±2
    clamp and the same morale 2 and 12 exemptions as any situational adjustment, because
    there's one adjustment rule rather than a second channel for spells.

    Args:
        combatant: The creature whose morale is being checked, a
            [`Character`][osrlib.core.character.Character] or a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance].

    Returns:
        The signed modifier, already totalled across every active effect and 0 when none
            applies.

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


def morale_triggers(members: Sequence[object]) -> list[str]:
    """Return the morale triggers a side's current state raises.

    Call this after each round to learn whether a side should check morale, then call
    [`check_morale`][osrlib.core.combat.check_morale] or
    [`MoraleTracker.check`][osrlib.core.combat.MoraleTracker.check] for the check itself.
    A session's battle machine does this for you. The triggers are `first_death`, raised
    once the side has lost anyone, and `half_incapacitated`, raised when half the side or
    more is dead, paralysed, petrified, or asleep.

    The triggers describe the side's current state, not what's changed since you last
    asked, so `first_death` keeps coming back while the body is on the floor. Track which
    ones you've already acted on.

    Args:
        members: The side's combatants, [`Character`][osrlib.core.character.Character] or
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] objects. An empty
            side raises nothing.

    Returns:
        The raised trigger keys.

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
    target: Any,
    category: SaveCategory,
    *,
    modifier: int = 0,
    magical: bool = False,
    element: str | None = None,
    source: Any | None = None,
    stream: RngStream,
) -> SaveResult:
    """Roll a saving throw: 1d20 at or above the target's value for the category.

    Call this whenever something forces a save, then act on `passed` yourself: the save
    reports a verdict and changes nothing. Pick the category from
    [`SaveCategory`][osrlib.core.combat.SaveCategory]. Breath weapons and petrifying gazes
    already save through [`resolve_breath`][osrlib.core.combat.resolve_breath] and
    [`resolve_gaze`][osrlib.core.combat.resolve_gaze], so you need this for spells, traps,
    poisons, and saves of your own.

    The roll gathers the target's bonuses so you don't have to. A character adds its WIS
    magic-save modifier when `magical` is true and the category isn't breath, since the
    rules say the WIS bonus "does not normally include saves against breath attacks".
    Anything a referee wants beyond that arrives as your `modifier`. Save bonuses from
    spells apply under the cumulative rule, whether unconditional, element-scoped like
    *resist cold* and *resist fire* against a matching `element`, or alignment-scoped like
    *protection from evil* against `source`. Equipped items add their bonuses on top,
    outside the spell caps. An energy defense that auto-saves, which is what a dragon has
    against magical forms of its own element, passes without a roll and without a draw.

    Args:
        target: The saving combatant, a
            [`Character`][osrlib.core.character.Character] or a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance].
        category: The saving throw category.
        modifier: Your adjustment, added to everything the target supplies.
        magical: Whether the effect is magical. It turns on the WIS modifier and is what
            an auto-saving energy defense keys off.
        element: The effect's element, read by auto-save defenses and by element-scoped
            save bonuses.
        source: The creature whose attack or ability forced the save, a `Character` or a
            `MonsterInstance`. Only alignment-scoped save bonuses read it.
        stream: The stream the d20 comes from, conventionally
            [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM]. One draw, or none on an
            automatic save.

    Returns:
        The outcome, with its events.

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


def apply_healing(target: Any, amount: int, *, source: str = "magical") -> list[Event]:
    """Apply instantaneous healing, capped at max HP.

    This mutates the target and draws nothing: roll the amount first if the healing is
    rolled. Use it for cure spells, potions, and a regenerating monster's tick. For a day
    of rest, call [`natural_healing`][osrlib.core.combat.natural_healing] instead, which
    rolls the 1d3 and applies the diseases that slow it.

    What blocks healing. The dead can't be healed. Mummy rot blocks magical healing, so a
    diseased target emits the blocked event and heals nothing from a `magical` source.
    Instantaneous healing counts as magical healing, which is why `magical` is the default:
    a cure spell that forgets to name its source still respects the rot rule. The weakness
    that follows being raised from the dead blocks healing from every source, because the
    rules say the subject "has 1 hit point" until the recovery period ends and that it
    "may not be shortened by any magical healing". The hit point comes back when the
    weakness effect ends. A cursed scroll's slow healing halves a magical amount rather
    than blocking it.

    Args:
        target: The creature to heal, a
            [`Character`][osrlib.core.character.Character] or a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance]. Mutated in place.
        amount: The healing amount, which must not be negative. Healing past the maximum
            is capped, not an error.
        source: The healing kind: `magical`, which is the default, `natural`, or
            `regeneration`. It selects which blocks apply and appears in the event.

    Returns:
        The healing and hit point events. A blocked target gets one event reporting 0
            healed. A dead target gets nothing.

    Raises:
        ValueError: If `amount` is negative.

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


def natural_healing(target: Any, stream: RngStream, *, ledger: EffectsLedger | None = None) -> list[Event]:
    """Apply one full day of complete rest: 1d3 hit points.

    Call this once per day of uninterrupted rest. Whether the rest was uninterrupted is
    yours to attest, and a session's rest procedure attests it for you. For healing that
    comes from a spell or a potion, call
    [`apply_healing`][osrlib.core.combat.apply_healing] instead, which takes the amount
    you rolled.

    A slowed-healing effect stretches the cadence rather than reducing the amount. Mummy
    rot makes natural healing run ten times slower, and an effect with a
    `healing_rest_days` param heals once per that many consecutive full rest days, which
    is 2 for *cause disease* and for a cursed scroll's "twice the usual amount of time".
    The count is kept on the effect, whether or not it's a disease, and when several apply
    the slowest one wins. A diseased target with no ledger to count on doesn't heal.

    Args:
        target: The resting creature, a
            [`Character`][osrlib.core.character.Character] or a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance]. Mutated in place.
        stream: The stream the 1d3 comes from. Natural healing is effect-internal
            randomness, so it draws from
            [`EFFECTS_STREAM`][osrlib.core.effects.EFFECTS_STREAM], not the combat
            stream. One draw on a healing day, none on a skipped one.
        ledger: The [`EffectsLedger`][osrlib.core.effects.EffectsLedger] that contains the
            target's effects, when any. Without one, no slowdown can be counted.

    Returns:
        The healing and hit point events. Empty on a rest day that a slowdown swallows,
            and empty for a dead target.

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
    """Roll falling damage: 1d6 per full 10 feet fallen, floored.

    This rolls only. Apply the total with
    [`deal_damage`][osrlib.core.combat.deal_damage], passing a
    [`DamageSource`][osrlib.core.combat.DamageSource] whose `kind` is `falling`, so a
    defense that turns aside weapons doesn't turn aside the floor.

    Args:
        feet: The distance fallen. Partial ten-foot increments don't count, so 19 feet is
            one die.
        stream: The stream the d6s come from, conventionally
            [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM].

    Returns:
        The damage roll, or `None` for a fall under 10 feet, which takes no dice and no
            draw.

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


def drain_monster_hd(monster: Any, *, levels: int = 1, stream: RngStream) -> list[Event]:
    """Drain a monster's Hit Dice, which is what "experience level (or Hit Die)" means.

    Call this when something drains a monster rather than a character. For a character,
    [`drain_levels`][osrlib.core.classes.drain_levels] is the matching function, and
    [`resolve_energy_drain`][osrlib.core.combat.resolve_energy_drain] picks between the
    two for you from the draining monster's tag. This mutates the monster.

    The drain works the way character drain does. The instance re-derives its THAC0 and
    saving throws from the reduced Hit Dice, and loses a rolled d8 from both its maximum
    and its current hit points per die drained. Neither total falls below 1, so a monster
    is never drained to death by hit point loss.

    A monster already at 1 Hit Die is killed instead. No die is rolled for that step, no
    Hit Dice come off, and the event counts the last one as lost.

    Args:
        monster: The drained [`MonsterInstance`][osrlib.core.monsters.MonsterInstance].
            Mutated in place.
        levels: How many Hit Dice the drain removes.
        stream: The stream the lost-hit-point d8s come from. Drain reverses advancement,
            so it draws from
            [`ADVANCEMENT_STREAM`][osrlib.core.character.ADVANCEMENT_STREAM] rather than
            the combat stream. One draw per Hit Die actually removed, and none for the step
            that kills.

    Returns:
        A surviving monster gets a `LevelDrainedEvent` coded `combat.drain.drained` and a
            `HitPointsReportedEvent`. A killed one gets a `LevelDrainedEvent` coded
            `combat.drain.slain` and then the death events, which end with their own
            `HitPointsReportedEvent` reporting 0.

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


def resolve_energy_drain(attacker: Any, target: Any, *, stream: RngStream) -> list[Event]:
    """Drain a victim's levels or Hit Dice from a drain-tagged monster's touch.

    Call this after a wight, wraith, spectre, or vampire lands a hit, since
    [`resolve_attack`][osrlib.core.combat.resolve_attack] deals the hit point damage and
    leaves the drain to you. It reads the attacker's `energy_drain` tag for how many levels
    to take and which XP policy to use, then applies character drain or
    [`drain_monster_hd`][osrlib.core.combat.drain_monster_hd] according to what the target
    is. The tag's own text describes what the victim becomes, and that text appears in the
    drain event.

    Args:
        attacker: The draining [`MonsterInstance`][osrlib.core.monsters.MonsterInstance],
            which must have an `energy_drain` tag.
        target: The drained combatant, a
            [`Character`][osrlib.core.character.Character] or a `MonsterInstance`.
            Mutated in place.
        stream: The stream the lost-hit-point dice come from. Drain reverses advancement,
            so it draws from
            [`ADVANCEMENT_STREAM`][osrlib.core.character.ADVANCEMENT_STREAM] rather than
            the combat stream.

    Returns:
        The drain events.

    Raises:
        ValueError: If the attacker has no `energy_drain` tag, which means it does not
            drain.

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
        ability = attacker.template.ability("energy_drain")
        result = drain_levels(
            target,
            target.definition,
            levels=levels,
            xp_policy=str(params.get("xp_policy", "level_minimum")),
            stream=stream,
            spawn_consequence=ability.prose,
        )
        return list(result.events)
    return drain_monster_hd(target, levels=levels, stream=stream)


def effective_hd(combatant: Any) -> int:
    """Return a combatant's effective Hit Dice for the HD-budget targeting mode.

    [`select_targets`][osrlib.core.combat.select_targets] spends its budget in these units,
    so call it yourself to work out in advance how many creatures a *sleep* would take.
    It's not a general power rating: a monster's plus-signs and asterisks aren't in it.

    A monster under 1 Hit Die counts as 1, and the fixed hit point bonus after a
    monster's Hit Dice is dropped, so a 2+1 HD monster counts as 2. A character counts
    its level.

    Args:
        combatant: The combatant, a [`Character`][osrlib.core.character.Character] or a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance].

    Returns:
        The effective Hit Dice, never below 1.

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
    template = getattr(combatant, "template", None)
    if template is not None:
        return max(1, template.hit_dice.count)
    return max(1, getattr(combatant, "level", 1))


def select_targets(
    mode: TargetingMode,
    candidates: Sequence[object],
    *,
    stream: RngStream,
    count: int | None = None,
    count_dice: str | None = None,
    hd_budget: int | None = None,
) -> tuple[list[object], list[Event]]:
    """Resolve the shared targeting model against an explicit candidate list.

    Spells, breath weapons, and thrown weapons all choose their victims through this one
    function, so a caster, a dragon, and a flask of oil pick targets by the same rules.
    You supply the candidates, because nothing in the kernel tracks position: who's in
    range, in the blast, or engaged with the gazer is your judgment, and a session's
    battle machine supplies it from its range track. Hand the selected targets to whichever
    resolution follows, like [`saving_throw`][osrlib.core.combat.saving_throw] or
    [`deal_damage`][osrlib.core.combat.deal_damage].

    How each mode chooses, from [`TargetingMode`][osrlib.core.combat.TargetingMode].
    `self` and `single` take the first candidate. `up_to_n` takes the first N in your
    order, with N either the fixed `count` or the rolled `count_dice`, which is where
    *hold person*'s 1d4 comes from. `area` and `gaze` take every candidate. `hd_budget`
    spends its budget weakest first by [`effective_hd`][osrlib.core.combat.effective_hd],
    breaking ties by your input order. The budget buys whole creatures, and a candidate
    larger than what's left is skipped while the selection continues down the list, which
    is the arithmetic *sleep* uses.

    Args:
        mode: The targeting mode.
        candidates: The candidates, in the order you want ties and precedence broken.
            Each a [`Character`][osrlib.core.character.Character] or a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance].
        stream: The stream a rolled count draws from, conventionally
            [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM]. Only `count_dice` draws.
        count: The fixed N for `up_to_n`.
        count_dice: The dice expression for a rolled N in `up_to_n`. `count` wins when
            both are given.
        hd_budget: The Hit Dice budget for `hd_budget`, which is required in that mode.

    Returns:
        The selected targets, in selection order, and the targeting event, which has
            referee visibility.

    Raises:
        ValueError: If `hd_budget` mode is used without a budget, or the mode is not one
            of the targeting modes.

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
    selected: list[object]
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
    gazer: object,
    engaged: Sequence[object],
    *,
    stream: RngStream,
    ledger: EffectsLedger,
    clock: GameClock,
    allocator: Any,
    registry: dict[str, Any],
) -> list[Event]:
    """Resolve one round of a petrifying gaze against the engaged combatants.

    Call this once per round for a basilisk, a medusa, or anything else whose look turns
    creatures to stone, on top of whatever it does with its attacks. You supply who's
    engaged with it, because nothing in the kernel tracks position.

    Each engaged combatant that's neither averting its eyes nor already out of the fight
    saves against paralysis, and a failed save attaches permanent petrification. Stone
    isn't dead: the effect is recoverable. Fighting with averted eyes costs an attack
    modifier, which is yours to apply through the
    [`AttackContext`][osrlib.core.combat.AttackContext] of the attacks that round, and
    counterplay with a mirror stays a matter for the referee.

    Args:
        gazer: The gazing [`MonsterInstance`][osrlib.core.monsters.MonsterInstance].
        engaged: The combatants in melee with it, each a
            [`Character`][osrlib.core.character.Character] or a `MonsterInstance`.
        stream: The stream the saves draw from, conventionally
            [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM]. One draw per combatant
            that has to save.
        ledger: The [`EffectsLedger`][osrlib.core.effects.EffectsLedger] petrification
            attaches through.
        clock: The game clock, which dates the attachment.
        allocator: The [`IdAllocator`][osrlib.core.monsters.IdAllocator] that mints
            effect ids.
        registry: Live objects by entity id, so the ledger can find the targets.

    Returns:
        The save and petrification events, per engaged combatant in order.

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


def validate_breath(monster: Any) -> list[Rejection]:
    """Validate a breath weapon use against the per-monster daily limit.

    Call this before [`resolve_breath`][osrlib.core.combat.resolve_breath], which raises
    rather than rejecting when the monster can't breathe. Like every validator here it's
    pure: no draws, no mutation, and no cost. An empty list means the monster may breathe.

    It rejects a monster with no breath weapon, and one that has already used its daily
    allowance, which is three for the dragons. A breath weapon with no daily limit, like
    the hellhound's, never exhausts. How often such a monster breathes is a matter for the
    action policy that chooses its moves, not for this validator.

    Args:
        monster: The breathing [`MonsterInstance`][osrlib.core.monsters.MonsterInstance],
            whose `breath_uses_today` is what the limit is checked against.

    Returns:
        Structured [`Rejection`][osrlib.core.validation.Rejection] values. Empty when the
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
    monster: Any,
    targets: Sequence[object],
    *,
    ruleset: Ruleset,
    stream: RngStream,
    clock: GameClock | None = None,
) -> list[Event]:
    """Resolve a breath weapon against an explicitly supplied target list.

    Call this instead of [`resolve_attack`][osrlib.core.combat.resolve_attack] when a
    monster breathes: there's no attack roll, every target saves instead, and the whole
    area resolves in one call. You supply who's caught in it, because nothing in the kernel
    tracks position. [`select_targets`][osrlib.core.combat.select_targets] in area mode is
    how a caller with a footprint turns it into a target list. Check with
    [`validate_breath`][osrlib.core.combat.validate_breath] first, because breathing past
    the daily limit raises rather than rejecting. The monster's daily counter goes up here,
    and every target is mutated.

    What the breath does depends on the monster. A dragon's deals its own current hit
    points, halved on a successful save, which is why a wounded dragon breathes weakly, and
    it gets three uses a day. A hellhound's deals dice by Hit Dice with no daily limit. A
    sea dragon's spittle kills outright on a failed save. Halving floors, so 1 point halves
    to 0 and nothing lands.

    A breath weapon is a destructive source, so a target it kills loses its equipment, with
    magic items saving under the `magic_item_death_save` ruleset flag. The tabletop rules'
    examples of item destruction are a lightning bolt and a dragon's breath, which osrlib
    reads as covering energy deaths generally, so a hellhound's fire destroys equipment the
    same way a dragon's does.

    Args:
        monster: The breathing [`MonsterInstance`][osrlib.core.monsters.MonsterInstance].
            Its `breath_uses_today` goes up when the breath has a daily limit.
        targets: The combatants caught in the breath, each a
            [`Character`][osrlib.core.character.Character] or a `MonsterInstance`.
            Mutated in place.
        ruleset: The ruleset in play.
        stream: The stream every draw comes from, conventionally
            [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM]: one save per target, the
            damage dice when the breath rolls them, and the magic-item saves of anyone it
            kills.
        clock: The game clock. When passed, the damage stamps each target's
            `last_damaged_round`.

    Returns:
        The events per target, in the order the targets were given. A target whose defenses
            absorb the breath gets a `DamageAbsorbedEvent` and never saves. Any other target
            gets its `SavingThrowRolledEvent`, then the damage and death events when damage
            lands.

    Raises:
        ValueError: If the monster has no breath weapon, or its daily uses are spent.
            Validate first. Breathing anyway is a programming mistake rather than a move
            the rules reject.

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
    # equipment too, not just a dragon's.
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
