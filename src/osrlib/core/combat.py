"""The combat kernel: attacks, damage, initiative, morale, saves, and targeting.

Kernel functions are pure resolutions over explicitly passed state: they take
combatants (either a [`Character`][osrlib.core.character.Character] or a
[`MonsterInstance`][osrlib.core.monsters.MonsterInstance] — both expose THAC0, attack
bonus, AC both ways, saves, hit points, and conditions), an
[`AttackContext`][osrlib.core.combat.AttackContext] carrying the caller-asserted
situation (distance, cover-like situational modifiers, back-stab position — the RAW
referee surface), the [`Ruleset`][osrlib.core.ruleset.Ruleset], and an RNG stream
(conventionally [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM]). They return
frozen result models carrying an `events` tuple — the Phase 4 session appends
`result.events` to its log; à la carte callers read the plain result fields.

The damage pipeline order is pinned: (1) the immunity gate — if the defender's
`harmed_only_by`/energy defenses exclude the source, no damage is rolled and the
event says so; (2) the damage roll plus STR for melee, then quality/context doublings
(brace, charge, back-stab), minimum 1 on a hit; (3) reductions (the wraith's
half-from-silver, the mummy's half-everything), floored but never below 1; (4) apply:
hit points floor at 0, fire and acid route into a regenerating monster's
non-regenerable ledger, and death emits at 0.

Validators mirror the Phase 1 convention: pure pre-phase functions returning
[`Rejection`][osrlib.core.validation.Rejection] lists — no RNG draws, no mutation.
Rejections are free (no roll, no time, no log entry), which is why holy water against
the living is *not* a rejection: it resolves normally and the damage pipeline reports
no effect — a rejection would be a zero-cost undead detector (pinned).
"""

from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from osrlib.core.classes import SavingThrows
from osrlib.core.clock import GameClock, TimeUnit
from osrlib.core.dice import RollResult, roll
from osrlib.core.effects import Condition, EffectDefinition, EffectsLedger, has_condition, kill
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
    SavingThrowRolledEvent,
    TargetsSelectedEvent,
)
from osrlib.core.items import CombatFacet, GearTemplate, MissileRanges, WeaponQuality, WeaponTemplate
from osrlib.core.monsters import Element, MonsterAttack
from osrlib.core.rng import RngStream
from osrlib.core.ruleset import Ruleset
from osrlib.core.tables import to_hit_ac
from osrlib.core.validation import Rejection

__all__ = [
    "COMBAT_STREAM",
    "AttackContext",
    "AttackResult",
    "AttackRollResult",
    "DamageSource",
    "InitiativeResult",
    "MoraleResult",
    "MoraleTracker",
    "Participant",
    "SaveCategory",
    "SaveResult",
    "TargetingMode",
    "apply_healing",
    "attack_roll",
    "burning_oil_pool_definition",
    "check_immunity",
    "check_morale",
    "damage_roll",
    "damage_source_for",
    "deal_damage",
    "drain_monster_hd",
    "effective_hd",
    "falling_damage",
    "incapacitated",
    "morale_triggers",
    "natural_healing",
    "participant_modifier",
    "resolve_attack",
    "resolve_breath",
    "resolve_energy_drain",
    "resolve_gaze",
    "resolve_splash_attack",
    "roll_initiative",
    "saving_throw",
    "select_targets",
    "splash_douse_definition",
    "validate_attack",
    "validate_breath",
]

COMBAT_STREAM = "combat"
"""Stream key convention for battle-resolution draws: attacks, damage, saves, morale."""

MELEE_REACH_FEET = 5
"""Melee attacks reach up to 5 feet."""

_HELPLESS = (Condition.PARALYSED, Condition.ASLEEP)
_CANNOT_ACT = (Condition.DEAD, Condition.PETRIFIED, Condition.PARALYSED, Condition.ASLEEP)

# The three dual-listed gear items carry pinned damage-source semantics: holy water's
# combat facet presents the `holy` key (admitted only by undead targets), and torch
# and burning oil deal fire damage (they are burning brands — this is what routes
# them into the troll's non-regenerable ledger).
_HOLY_ITEM_ID = "holy_water"
_FIRE_ITEM_IDS = ("torch", "oil_flask")

Attack = WeaponTemplate | CombatFacet | GearTemplate | MonsterAttack | None
"""What a combatant attacks with; `None` is an unarmed attack (1d2)."""


class AttackContext(BaseModel):
    """The caller-asserted situation an attack resolves under.

    Everything here is the RAW referee surface: the kernel checks the rules given
    honest context, and supplying the context (was the charge 60 feet? is the target
    unaware?) is the caller's or Phase 4's job. `situational_modifier` is the RAW
    referee adjustment (cover −1 to −4, the dozing dragon's +2, and kin).
    """

    model_config = ConfigDict(frozen=True)

    distance_feet: int | None = None
    situational_modifier: int = 0
    behind_target: bool = False
    target_unaware: bool = False
    defender_retreating: bool = False
    braced: bool = False
    charging: bool = False
    fired_last_round: bool = False
    attacker_large: bool = False
    lit: bool = False
    fixed_damage_option: int = 0


class DamageSource(BaseModel):
    """What a damage packet presents to the defender's defenses.

    `keys` are material/enchantment keys (`silver`, `magic`, `holy`); `element` is the
    energy element, if any; `kind` names the delivery (`weapon`, `unarmed`, `splash`,
    `breath`, `falling`, `effect`); `destructive` marks sources that destroy a
    victim's equipment on death (every breath weapon now, *lightning bolt* in
    Phase 3).
    """

    model_config = ConfigDict(frozen=True)

    keys: tuple[str, ...] = ()
    element: str | None = None
    magical: bool = False
    kind: str = "weapon"
    destructive: bool = False


class AttackRollResult(BaseModel):
    """An attack roll's outcome; `roll` is `None` for the helpless auto-hit."""

    model_config = ConfigDict(frozen=True)

    hit: bool
    auto: bool = False
    roll: int | None = None
    modifier: int = 0
    total: int | None = None
    required: int | None = None
    natural: int | None = None
    events: tuple[Event, ...] = ()


class AttackResult(BaseModel):
    """A full attack resolution: the roll, the gate verdict, and any damage."""

    model_config = ConfigDict(frozen=True)

    attack_roll: AttackRollResult
    absorbed: bool = False
    damage: int | None = None
    events: tuple[Event, ...] = ()


class SaveResult(BaseModel):
    """A saving throw's outcome; `roll` is `None` for auto-save defenses."""

    model_config = ConfigDict(frozen=True)

    passed: bool
    auto: bool = False
    roll: int | None = None
    modifier: int = 0
    required: int | None = None
    events: tuple[Event, ...] = ()


class MoraleResult(BaseModel):
    """A morale check's outcome; `exempt` marks ML 2 and ML 12 (no roll made)."""

    model_config = ConfigDict(frozen=True)

    held: bool
    exempt: bool = False
    roll: int | None = None
    modifier: int = 0
    events: tuple[Event, ...] = ()


class Participant(BaseModel):
    """One initiative participant: a stable key, a side, and the modifier hooks.

    `modifier` is the individual-initiative modifier (DEX for characters plus the
    halfling's class tag, the caller-supplied modifier for monsters — compute it with
    [`participant_modifier`][osrlib.core.combat.participant_modifier]). `slow` marks
    slow-weapon actors, who act after all non-slow actors (pinned).
    """

    model_config = ConfigDict(frozen=True)

    key: str
    side: str
    slow: bool = False
    modifier: int = 0


class InitiativeResult(BaseModel):
    """An initiative resolution: per-key rolls (re-rolls included) and the acting order."""

    model_config = ConfigDict(frozen=True)

    mode: str
    entries: tuple[InitiativeRoll, ...]
    order: tuple[str, ...]
    events: tuple[Event, ...] = ()


class SaveCategory(StrEnum):
    """The five saving throw categories."""

    DEATH = "death"
    WANDS = "wands"
    PARALYSIS = "paralysis"
    BREATH = "breath"
    SPELLS = "spells"


class TargetingMode(StrEnum):
    """The shared targeting model's modes.

    Spells, breath weapons, and thrown weapons resolve through these. Phase 2
    resolves against explicitly supplied candidate lists; geometry-to-target mapping
    arrives with Phase 4's combat space.
    """

    SELF = "self"
    SINGLE = "single"
    UP_TO_N = "up_to_n"
    HD_BUDGET = "hd_budget"
    AREA = "area"
    GAZE = "gaze"


def _entity_id(combatant: object) -> str:
    identifier = getattr(combatant, "id", None)
    return identifier if identifier is not None else getattr(combatant, "name", "unknown")


def _class_ability_params(combatant: object, tag: str) -> dict[str, int | str] | None:
    definition = getattr(combatant, "definition", None)
    if definition is None:
        return None
    for ability in definition.abilities:
        if ability.tag == tag:
            return ability.params
    return None


def _monster_ability_params(combatant: object, tag: str) -> dict[str, object] | None:
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
    return attack.name


def _facet(attack: Attack) -> WeaponTemplate | CombatFacet | None:
    """Return the combat stats of a character attack (a gear item's embedded facet)."""
    if isinstance(attack, GearTemplate):
        return attack.combat
    if isinstance(attack, WeaponTemplate | CombatFacet):
        return attack
    return None


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


def damage_source_for(attacker: object, attack: Attack, context: AttackContext) -> DamageSource:
    """Build the damage source an attack presents to the defender's defenses.

    Silver weapons present `silver`; holy water presents `holy`; torch and burning
    oil deal fire (pinned — see the module docstring). Monster natural attacks are
    mundane; the `hd5_counts_as_magical` flag is resolved by
    [`check_immunity`][osrlib.core.combat.check_immunity] from the attacker, not
    here.

    Args:
        attacker: The attacking combatant.
        attack: The weapon, facet, gear item, or monster attack (`None` for unarmed).
        context: The attack context (`lit` matters for burning oil).

    Returns:
        The frozen damage source.
    """
    keys: list[str] = []
    element: str | None = None
    kind = "weapon"
    if attack is None:
        kind = "unarmed"
    elif isinstance(attack, MonsterAttack):
        kind = "monster"
    else:
        material = getattr(attack, "material", None)
        if material is not None and material.value == "silver":
            keys.append("silver")
        if isinstance(attack, GearTemplate):
            if WeaponQuality.SPLASH in _qualities(attack):
                kind = "splash"
            if attack.id == _HOLY_ITEM_ID:
                keys.append("holy")
            if attack.id == "torch" or (attack.id == "oil_flask" and context.lit):
                element = "fire"
    return DamageSource(keys=tuple(keys), element=element, kind=kind)


def _is_bladed(attack: Attack) -> bool:
    """Return whether the attack is a bladed weapon, for the sleeping-kill hook.

    Pinned: "bladed" means a weapon (not a gear facet, a monster's natural attack, or
    an unarmed strike) with the melee quality and without the blunt quality — the
    SRD's blunt list exists precisely to separate crushing weapons from edged ones.
    """
    return (
        isinstance(attack, WeaponTemplate)
        and WeaponQuality.MELEE in attack.qualities
        and WeaponQuality.BLUNT not in attack.qualities
    )


def validate_attack(
    attacker: object, defender: object, attack: Attack, context: AttackContext, *, ruleset: Ruleset
) -> list[Rejection]:
    """Validate an attack — the pure pre-phase: no RNG draws, no mutation.

    Args:
        attacker: The attacking combatant.
        defender: The defending combatant.
        attack: The weapon, facet, gear item, or monster attack (`None` for unarmed).
        context: The caller-asserted situation.
        ruleset: The ruleset in play (`weapon_reload` is enforced here).

    Returns:
        Structured rejections; empty when the attack may be rolled.
    """
    rejections: list[Rejection] = []
    for condition in _CANNOT_ACT:
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


def _defender_descending_ac(defender: object, context: AttackContext) -> int | None:
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
    return ac


def attack_roll(
    attacker: object,
    defender: object,
    attack: Attack,
    *,
    context: AttackContext,
    ruleset: Ruleset,
    stream: RngStream,
) -> AttackRollResult:
    """Roll an attack: 1d20 plus modifiers against the defender's armour class.

    Helpless defenders (paralysed, asleep) are hit automatically in melee — no roll
    is consumed, damage only, per RAW (pinned); a `No hit roll required` defender is
    likewise hit without a roll. Natural 20 always hits and natural 1 always misses.
    Resolution is the attack-matrix lookup, or unclamped `THAC0 − AC` under the
    `thac0_arithmetic` flag.

    Args:
        attacker: The attacking combatant.
        defender: The defending combatant.
        attack: The weapon, facet, gear item, or monster attack (`None` for unarmed).
        context: The caller-asserted situation.
        ruleset: The ruleset in play.
        stream: The combat stream.

    Returns:
        The roll outcome, with its events.
    """
    attacker_id, defender_id = _entity_id(attacker), _entity_id(defender)
    name = _attack_name(attack)
    missile = _is_missile_use(attack, context)
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
        modifier += getattr(attacker, "melee_modifier", 0)
    if context.behind_target and context.target_unaware:
        back_stab = _class_ability_params(attacker, "back_stab")
        if back_stab is not None:
            modifier += int(back_stab.get("attack_bonus", 0))
    if context.defender_retreating:
        modifier += 2

    ac = _defender_descending_ac(defender, context)
    thac0 = attacker.thac0
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


def check_immunity(defender: object, source: DamageSource, *, ruleset: Ruleset, attacker: object | None = None) -> bool:
    """Return True when the defender's defenses absorb the source: no damage is rolled.

    Pinned rules resolved here: the `holy` key is admitted through any
    `harmed_only_by` gate on undead targets and has *no effect* on anything else;
    `uses_fire` monsters ignore burning oil; the `hd5_counts_as_magical` flag lets a
    5+ HD monster attacker (or one bearing a silver/magic-subset gate itself) bypass
    gates whose keys are a subset of {silver, magic}.

    Args:
        defender: The defending combatant.
        source: The damage source presented.
        ruleset: The ruleset in play.
        attacker: The attacking combatant, consulted by `hd5_counts_as_magical`.

    Returns:
        True when the hit is absorbed.
    """
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
    attacker: object,
    attack: Attack,
    *,
    context: AttackContext,
    ruleset: Ruleset,
    stream: RngStream,
) -> RollResult:
    """Roll an attack's damage: dice, STR for melee, doublings, minimum 1.

    With `variable_weapon_damage` off, every weapon and gear combat facet deals 1d6;
    unarmed attacks stay 1d2 (the specific unarmed rule) and monster damage is
    unaffected (pinned). Doublings (brace against a charging attacker, a 60-foot
    mounted charge, the thief's back-stab multiplier) apply after the roll and STR.

    Args:
        attacker: The attacking combatant.
        attack: The weapon, facet, gear item, or monster attack (`None` for unarmed).
        context: The caller-asserted situation.
        ruleset: The ruleset in play.
        stream: The combat stream.

    Returns:
        The damage roll; `total` is the final amount (minimum 1).
    """
    rolls: tuple[int, ...] = ()
    if isinstance(attack, MonsterAttack):
        if attack.fixed_damage_options:
            amount = attack.fixed_damage_options[context.fixed_damage_option]
        elif attack.fixed_damage is not None:
            amount = attack.fixed_damage
        elif attack.damage is not None:
            result = roll(attack.damage, stream)
            rolls, amount = result.rolls, result.total
        else:
            # An effect-only attack (the wight's touch) deals no hit point damage;
            # its effect tags resolve separately.
            return RollResult(rolls=(), modifier=0, multiplier=1, total=0)
    elif attack is None:
        result = roll("1d2", stream)
        rolls, amount = result.rolls, result.total
    else:
        facet = _facet(attack)
        dice = facet.damage if ruleset.variable_weapon_damage else "1d6"
        result = roll(dice, stream)
        rolls, amount = result.rolls, result.total
    missile = _is_missile_use(attack, context)
    if not missile and not isinstance(attack, MonsterAttack):
        amount += getattr(attacker, "melee_modifier", 0)
    qualities = _qualities(attack)
    if context.braced and WeaponQuality.BRACE in qualities:
        amount *= 2
    if context.charging and WeaponQuality.CHARGE in qualities:
        amount *= 2
    if context.behind_target and context.target_unaware:
        back_stab = _class_ability_params(attacker, "back_stab")
        if back_stab is not None:
            amount *= int(back_stab.get("damage_multiplier", 1))
    return RollResult(rolls=rolls, modifier=0, multiplier=1, total=max(1, amount))


def deal_damage(
    target: object,
    amount: int,
    *,
    source: DamageSource,
    attacker_id: str | None = None,
    rolls: tuple[int, ...] = (),
    clock: GameClock | None = None,
) -> list[Event]:
    """Apply damage: reductions, the hit point floor, ledgers, and death.

    Reductions floor but never below 1 (pinned). Fire and acid damage against a
    regenerating monster whose regeneration they block accrue in the non-regenerable
    ledger (capped at max HP); the monster is permanently dead only when that ledger
    alone reaches max HP (pinned). A destructive killing source destroys the victim's
    mundane equipment.

    Args:
        target: The creature taking damage; mutated.
        amount: The rolled amount, before reductions.
        source: The damage source.
        attacker_id: The attacker's entity id, for the event.
        rolls: The raw damage dice, for the event.
        clock: When passed, stamps the target's `last_damaged_round` (regeneration's
            delay anchor).

    Returns:
        The damage, state, and death events, in order.
    """
    template = getattr(target, "template", None)
    if template is not None:
        for reduction in template.defenses.reductions:
            keys = {key.value for key in reduction.keys}
            if not keys or any(key in keys for key in source.keys) or (source.element in keys):
                amount = max(1, amount // reduction.divisor)
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
        # "Permanent" is the reviving regenerator's marker: the troll is permanently
        # dead only when the non-regenerable ledger alone reaches max HP (pinned).
        permanent = (
            regeneration is not None
            and regeneration.get("revive") is not None
            and target.nonregen_damage >= target.max_hp
        )
        events.extend(kill(target, permanent=permanent))
        if source.destructive:
            events.extend(_destroy_equipment(target))
    elif already_dead and newly_permanent:
        events.append(DeathEvent(code="combat.death.permanent", target_id=target_id))
    return events


def _destroy_equipment(target: object) -> list[Event]:
    inventory = getattr(target, "inventory", None)
    if inventory is None:
        return []
    names = tuple(instance.template.name for instance in inventory.all_instances())
    if not names:
        return []
    inventory.items = []
    inventory.wielded = []
    inventory.worn_armour = None
    inventory.shield = None
    return [EquipmentDestroyedEvent(target_id=_entity_id(target), item_names=names)]


def resolve_attack(
    attacker: object,
    defender: object,
    attack: Attack,
    *,
    context: AttackContext,
    ruleset: Ruleset,
    stream: RngStream,
    clock: GameClock | None = None,
) -> AttackResult:
    """Resolve one attack end to end: roll, gate, damage.

    The pinned pipeline: the attack roll first; on a hit, the immunity gate — if the
    defender's defenses exclude the source, no damage is rolled and the absorbed
    event says so; otherwise the damage roll and application.

    Args:
        attacker: The attacking combatant.
        defender: The defending combatant.
        attack: The weapon, facet, gear item, or monster attack (`None` for unarmed).
        context: The caller-asserted situation.
        ruleset: The ruleset in play.
        stream: The combat stream.
        clock: When passed, damage stamps the defender's `last_damaged_round`.

    Returns:
        The full resolution with its events.
    """
    rolled = attack_roll(attacker, defender, attack, context=context, ruleset=ruleset, stream=stream)
    events = list(rolled.events)
    if not rolled.hit:
        return AttackResult(attack_roll=rolled, events=tuple(events))
    if isinstance(attack, GearTemplate) and attack.id == "oil_flask" and not context.lit:
        # Unlit oil deals no damage (pinned); the caller may compile a pool instead.
        return AttackResult(attack_roll=rolled, damage=0, events=tuple(events))
    source = damage_source_for(attacker, attack, context)
    if check_immunity(defender, source, ruleset=ruleset, attacker=attacker):
        events.append(
            DamageAbsorbedEvent(target_id=_entity_id(defender), attacker_id=_entity_id(attacker), keys=source.keys)
        )
        return AttackResult(attack_roll=rolled, absorbed=True, events=tuple(events))
    if has_condition(defender, Condition.ASLEEP) and _is_bladed(attack) and not _is_missile_use(attack, context):
        # The sleeping condition's dies-to-a-blade hook (pinned): "A single attack
        # with a bladed weapon can kill" — the melee hit kills outright, no damage
        # roll; the immunity gate above still applies first.
        events.extend(kill(defender))
        return AttackResult(attack_roll=rolled, events=tuple(events))
    damage = damage_roll(attacker, attack, context=context, ruleset=ruleset, stream=stream)
    if damage.total > 0:
        events.extend(
            deal_damage(
                defender,
                damage.total,
                source=source,
                attacker_id=_entity_id(attacker),
                rolls=damage.rolls,
                clock=clock,
            )
        )
    return AttackResult(attack_roll=rolled, damage=damage.total, events=tuple(events))


def splash_douse_definition(attack: Attack, source: DamageSource) -> EffectDefinition:
    """Build the splash weapon's dousing effect: one more application next round.

    "Inflicted for two rounds" is pinned as two applications — the hit's damage now,
    and the douse's expiry applies the listed damage once more at the next round
    boundary.

    Args:
        attack: The splash item (its facet's damage dice carry over).
        source: The damage source the hit presented.

    Returns:
        The one-round douse effect definition.
    """
    facet = _facet(attack)
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

    Unlit oil may be compiled into a 3-foot pool; once lit it burns 1 turn and deals
    1d8 to creatures passing through — who passes through is the caller's assertion
    until Phase 4 owns space (the caller applies the damage with
    [`deal_damage`][osrlib.core.combat.deal_damage]).

    Returns:
        The one-turn pool effect definition.
    """
    return EffectDefinition(
        kind="burning_oil_pool",
        duration_unit=TimeUnit.TURN,
        duration_amount=1,
        params={"dice": "1d8", "element": "fire", "radius_feet": 3},
    )


def resolve_splash_attack(
    attacker: object,
    defender: object,
    attack: GearTemplate,
    *,
    context: AttackContext,
    ruleset: Ruleset,
    stream: RngStream,
    ledger: EffectsLedger,
    clock: GameClock,
    allocator: object,
    registry: dict[str, object],
) -> AttackResult:
    """Resolve a thrown splash weapon: the attack, the first application, the douse.

    A damaging hit attaches the two-round dousing effect (the second application
    lands at the next round boundary). Holy water against the living, and burning
    oil against `uses_fire` monsters, resolve as no effect through the damage
    pipeline — never as a rejection (pinned: a rejection is free and would leak what
    B/X hides until it matters).

    Args:
        attacker: The throwing combatant.
        defender: The target.
        attack: The splash gear item (holy water or burning oil).
        context: The caller-asserted situation (`lit` matters for oil).
        ruleset: The ruleset in play.
        stream: The combat stream.
        ledger: The effects ledger the douse attaches through.
        clock: The game clock.
        allocator: The id allocator for the douse effect.
        registry: Live objects by entity id.

    Returns:
        The full resolution with its events.
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


def participant_modifier(combatant: object, *, monster_modifier: int = 0) -> int:
    """Return a combatant's individual-initiative modifier.

    Characters get their DEX modifier plus the halfling's `initiative_bonus` class
    tag; monsters take the caller-supplied modifier — the RAW referee-judgment
    surface.

    Args:
        combatant: The combatant.
        monster_modifier: The referee's modifier for monsters.

    Returns:
        The signed modifier.
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

    Ties re-roll (pinned — RAW offers "re-roll or simultaneous", and simultaneous
    resolution is a different combat model): tied sides, or tied individuals among
    themselves, re-roll in stable input order until distinct, each re-roll consuming
    draws. Slow-weapon actors act after all non-slow actors, ordered among themselves
    by their side's initiative (their own results under individual initiative) then
    stable order (pinned).

    Args:
        participants: The combatants' initiative entries, in stable order.
        ruleset: The ruleset in play.
        stream: The combat stream.

    Returns:
        The rolls (re-rolls included) and the full acting order.
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
    """Check morale: 2d6 versus ML; over means flee or surrender.

    ML 2 never fights and ML 12 never checks — both are exempt from the roll, and
    situational adjustments (clamped to ±2 per RAW) never apply to them (pinned).

    Args:
        subject: The side or group key, for the event.
        score: The morale score, 2–12.
        modifier: The situational adjustment, clamped to ±2.
        stream: The combat stream.

    Returns:
        The outcome; referee-visibility events.
    """
    if score <= 2:
        event = MoraleCheckedEvent(code="combat.morale.exempt", subject=subject, score=score)
        return MoraleResult(held=False, exempt=True, events=(event,))
    if score >= 12:
        event = MoraleCheckedEvent(code="combat.morale.exempt", subject=subject, score=score)
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
    )
    return MoraleResult(held=held, roll=rolled, modifier=modifier, events=(event,))


class MoraleTracker(BaseModel):
    """The two-passed-checks memory: after two held checks, no further checks.

    "If a monster passes two morale checks in an encounter, it will fight until
    killed, with no further checks."
    """

    model_config = ConfigDict(validate_assignment=True)

    passed: dict[str, int] = {}

    def check(self, subject: str, score: int, *, modifier: int = 0, stream: RngStream) -> MoraleResult | None:
        """Check morale unless the subject has already passed twice.

        Args:
            subject: The side or group key.
            score: The morale score.
            modifier: The situational adjustment, clamped to ±2.
            stream: The combat stream.

        Returns:
            The result, or `None` when no further checks are made (they fight on).
        """
        if self.passed.get(subject, 0) >= 2:
            return None
        result = check_morale(subject, score, modifier=modifier, stream=stream)
        if result.held and not result.exempt:
            self.passed[subject] = self.passed.get(subject, 0) + 1
        return result


def incapacitated(combatant: object) -> bool:
    """Return whether a combatant counts as incapacitated for morale triggers.

    Pinned: dead, paralysed, petrified, or asleep (RAW: "slain, paralysed, etc").

    Args:
        combatant: The combatant.

    Returns:
        True when incapacitated.
    """
    return any(has_condition(combatant, condition) for condition in _CANNOT_ACT)


def morale_triggers(members: Sequence[object]) -> list[str]:
    """Return the morale triggers a side's current state raises.

    Queryable by the milestone scripts now and the Phase 4 battle machine later:
    `first_death` after the side's first death, `half_incapacitated` when half the
    side (or more) is dead, paralysed, petrified, or asleep.

    Args:
        members: The side's combatants.

    Returns:
        The raised trigger keys; the caller tracks which it has already acted on.
    """
    triggers = []
    if any(has_condition(member, Condition.DEAD) for member in members):
        triggers.append("first_death")
    if members and sum(1 for member in members if incapacitated(member)) * 2 >= len(members):
        triggers.append("half_incapacitated")
    return triggers


def saving_throw(
    target: object,
    category: SaveCategory,
    *,
    modifier: int = 0,
    magical: bool = False,
    element: str | None = None,
    stream: RngStream,
) -> SaveResult:
    """Roll a saving throw: 1d20 at or above the target's value for the category.

    The WIS magic-save modifier applies to characters when `magical` is true and the
    category is not breath (pinned reading of "does not normally include saves
    against breath attacks"; referee discretion beyond that arrives as a caller
    modifier). Energy `auto_save` defenses (a dragon versus similar magical forms of
    its element) pass without a roll.

    Args:
        target: The saving combatant.
        category: The saving throw category.
        modifier: The caller-supplied adjustment.
        magical: Whether the effect is magical (WIS applies; auto-save defenses key
            off it).
        element: The effect's element, consulted by auto-save defenses.
        stream: The combat stream.

    Returns:
        The outcome, with its events.
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


def apply_healing(target: object, amount: int, *, source: str = "magical") -> list[Event]:
    """Apply instantaneous healing, capped at max HP.

    Mummy rot blocks magical healing (pinned): a diseased target emits the blocked
    event and heals nothing from a `magical` source. Instantaneous healing *is*
    magical healing per the spec, so `magical` is the default — a Phase 3 cure spell
    that forgets to name its source still respects the rot rule. The dead cannot be
    healed.

    Args:
        target: The creature to heal; mutated.
        amount: The healing amount. Non-negative.
        source: The healing kind: `magical` (the default), `natural`, or
            `regeneration`.

    Returns:
        The healing and state events.
    """
    if amount < 0:
        raise ValueError(f"healing must be non-negative, got {amount}")
    target_id = _entity_id(target)
    if has_condition(target, Condition.DEAD):
        return []
    if source == "magical" and has_condition(target, Condition.DISEASED):
        return [HealingAppliedEvent(code="combat.healing.blocked", target_id=target_id, amount=0, source=source)]
    healed = min(amount, target.max_hp - target.current_hp)
    target.current_hp += healed
    return [
        HealingAppliedEvent(code="combat.healing.applied", target_id=target_id, amount=healed, source=source),
        HitPointsReportedEvent(target_id=target_id, current_hp=target.current_hp, max_hp=target.max_hp),
    ]


def natural_healing(target: object, stream: RngStream, *, ledger: EffectsLedger | None = None) -> list[Event]:
    """Apply one full day of complete rest: 1d3 hit points.

    Callable by whoever can attest the rest was uninterrupted (Phase 4 automates).
    Under mummy rot, natural healing runs ten times slower — one 1d3 recovery per
    ten consecutive full rest days, tracked on the rot effect (pinned); without a
    ledger to track on, a diseased target does not heal.

    Args:
        target: The resting creature; mutated.
        stream: The effects stream (natural-healing rolls are effect-internal
            randomness, pinned).
        ledger: The effects ledger carrying the mummy-rot effect, when any.

    Returns:
        The healing and state events; empty on a non-healing rest day.
    """
    if has_condition(target, Condition.DEAD):
        return []
    if has_condition(target, Condition.DISEASED):
        if ledger is None:
            return []
        rots = ledger.active_on(_entity_id(target), "mummy_rot")
        if rots:
            rot = rots[0]
            rot.state["rest_days"] = rot.state.get("rest_days", 0) + 1
            if rot.state["rest_days"] % 10 != 0:
                return []
    amount = stream.randbelow(3) + 1
    return apply_healing(target, amount, source="natural")


def falling_damage(feet: int, stream: RngStream) -> RollResult | None:
    """Roll falling damage: 1d6 per full 10 feet fallen, floored (pinned).

    Args:
        feet: The distance fallen.
        stream: The combat stream.

    Returns:
        The damage roll, or `None` for falls under 10 feet (no dice, no draw).
    """
    dice = feet // 10
    if dice < 1:
        return None
    return roll(f"{dice}d6", stream)


def drain_monster_hd(monster: object, *, levels: int = 1, stream: RngStream) -> list[Event]:
    """Drain a monster's Hit Dice — the SRD says "experience level (or Hit Die)".

    Symmetric with character drain (pinned): the instance re-derives THAC0 and saves
    from the reduced HD via the tables and loses a rolled d8 (minimum 1) from max and
    current hit points per die; a monster drained below 1 HD dies.

    Args:
        monster: The drained monster instance; mutated.
        levels: How many Hit Dice the drain removes.
        stream: The RNG stream for the lost die rolls, conventionally the
            advancement stream — the same subsystem as character drain.

    Returns:
        The drain, state, and death events.
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


def resolve_energy_drain(attacker: object, target: object, *, stream: RngStream) -> list[Event]:
    """Wire a drain-tagged monster's touch to the drain procedure.

    Reads the attacker's `energy_drain` tag (levels and XP policy are per-monster
    data — the wight's floored halfway, the wraith/spectre/vampire's level minimum)
    and applies character or monster drain by target kind. The spawn-consequence
    prose rides the drain event from the tag's SRD text.

    Args:
        attacker: The draining monster instance.
        target: The drained combatant.
        stream: The RNG stream for the lost hit die rolls, conventionally the
            advancement stream (drain reverses advancement, pinned).

    Returns:
        The drain events.

    Raises:
        ValueError: If the attacker has no `energy_drain` tag.
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


def effective_hd(combatant: object) -> int:
    """Return a combatant's effective Hit Dice for the HD-budget targeting mode.

    Pinned: sub-1 HD rounds up to 1 and fixed hit-point bonuses are dropped;
    characters count their level.

    Args:
        combatant: The combatant.

    Returns:
        The effective HD, minimum 1.
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

    Modes: `self` and `single` take the (single) supplied candidate; `up_to_n` takes
    the first N (fixed `count` or rolled `count_dice` — *hold person*'s 1d4) in the
    caller's order; `area` and `gaze` affect every candidate (geometry arrives with
    Phase 4's combat space); `hd_budget` consumes candidates weakest-first by
    effective HD, ties broken by stable input order — the budget spends whole
    creatures, and a target whose HD exceed the remainder is skipped while selection
    continues (pinned; *sleep*'s exact arithmetic lands with the spell in Phase 3).

    Args:
        mode: The targeting mode.
        candidates: The explicit candidate list, in the caller's order.
        stream: The combat stream, for rolled counts.
        count: The fixed N for `up_to_n`.
        count_dice: The rolled N for `up_to_n`.
        hd_budget: The dice budget for `hd_budget`.

    Returns:
        The selected targets and the referee targeting event.
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
    allocator: object,
    registry: dict[str, object],
) -> list[Event]:
    """Resolve one round of a petrifying gaze against the engaged combatants.

    Each engaged combatant not averting its eyes saves versus petrify or is turned to
    stone (a permanent effect — recoverable, stone is not dead). The ±modifiers for
    fighting with averted eyes are attack modifiers, applied by the caller through
    the attack context; mirror counterplay stays manual prose.

    Args:
        gazer: The gazing monster.
        engaged: The combatants in melee with it.
        stream: The combat stream.
        ledger: The effects ledger petrification attaches through.
        clock: The game clock.
        allocator: The id allocator.
        registry: Live objects by entity id.

    Returns:
        The save and petrification events, per engaged combatant in order.
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


def validate_breath(monster: object) -> list[Rejection]:
    """Validate a breath weapon use against the per-monster daily limit.

    Args:
        monster: The breathing monster instance.

    Returns:
        Structured rejections; empty when the monster may breathe.
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
    monster: object,
    targets: Sequence[object],
    *,
    ruleset: Ruleset,
    stream: RngStream,
    clock: GameClock | None = None,
) -> list[Event]:
    """Resolve a breath weapon against an explicitly supplied target list.

    Damage is the monster's current hit points with save-for-half (dragons, three
    uses per day tracked on the instance), dice (the hellhound's per-HD dice — no
    daily limit; its 2-in-6 per-round gate is Phase 4 action-policy data), or
    save-or-die (the sea dragon's spittle). Save-for-half halving floors (pinned).

    Args:
        monster: The breathing monster instance; its daily counter increments.
        targets: The affected combatants (the caller resolves the area for now).
        ruleset: The ruleset in play.
        stream: The combat stream.
        clock: When passed, damage stamps targets' `last_damaged_round`.

    Returns:
        The save and damage events, per target in order.

    Raises:
        ValueError: If the monster has no breath weapon or its daily uses are spent —
            validate with [`validate_breath`][osrlib.core.combat.validate_breath]
            first; over-breathing is programmer misuse.
    """
    rejections = validate_breath(monster)
    if rejections:
        raise ValueError(f"illegal breath: {[rejection.code for rejection in rejections]}")
    params = _monster_ability_params(monster, "breath_weapon")
    if params.get("uses_per_day") is not None:
        monster.breath_uses_today += 1
    element = str(params.get("element")) if params.get("element") is not None else None
    # Breath weapons are destructive deaths (pinned): the SRD's destruction-of-items
    # examples ("a lightning bolt spell or a dragon's breath") illustrate energy
    # deaths generally, so the hellhound's and chimera's fire kills destroy
    # equipment too, not just the dragons'.
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
            deal_damage(target, amount, source=source, attacker_id=_entity_id(monster), rolls=tuple(rolls), clock=clock)
        )
    return events
