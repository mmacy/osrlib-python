"""Tests for the combat kernel: attacks, damage, initiative, morale, saves, targeting."""

import pytest

from osrlib.core.abilities import AbilityScore
from osrlib.core.character import Alignment, Character
from osrlib.core.clock import GameClock, TimeUnit
from osrlib.core.combat import (
    AttackContext,
    MoraleTracker,
    Participant,
    SaveCategory,
    TargetingMode,
    attack_roll,
    check_immunity,
    check_morale,
    damage_roll,
    damage_source_for,
    deal_damage,
    effective_hd,
    falling_damage,
    incapacitated,
    morale_triggers,
    participant_modifier,
    resolve_attack,
    resolve_breath,
    resolve_gaze,
    resolve_splash_attack,
    roll_initiative,
    saving_throw,
    select_targets,
    validate_attack,
    validate_breath,
)
from osrlib.core.effects import Condition, EffectsLedger, grant_condition, has_condition
from osrlib.core.items import ItemInstance
from osrlib.core.monsters import IdAllocator, spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.data import load_classes, load_equipment, load_monsters

MASTER_SEED = 424_242


class FixedStream:
    """A stream stub yielding scripted `randbelow` results (values, not raw draws)."""

    def __init__(self, values):
        self.values = list(values)
        self.draws = 0

    def randbelow(self, n):
        self.draws += 1
        return self.values.pop(0) % n


@pytest.fixture
def streams():
    return RngStreams(master_seed=MASTER_SEED)


@pytest.fixture
def allocator():
    return IdAllocator()


def make_character(class_id="fighter", level=1, str_score=10, dex_score=10, **overrides):
    # 9-12 is the +0 modifier band; 13 would already grant +1.
    scores = {ability: 10 for ability in AbilityScore}
    scores[AbilityScore.STR] = str_score
    scores[AbilityScore.DEX] = dex_score
    fields = {
        "id": f"pc-{class_id}",
        "name": class_id.title(),
        "class_id": class_id,
        "race": load_classes().get(class_id).race,
        "level": level,
        "xp": load_classes().get(class_id).row(level).xp,
        "scores": scores,
        "alignment": Alignment.NEUTRAL,
        "max_hp": 12,
        "current_hp": 12,
    }
    fields.update(overrides)
    return Character(**fields)


def make_monster(monster_id, streams, allocator):
    return spawn_monster(
        load_monsters().get(monster_id), id=allocator.allocate("monster"), stream=streams.get("monster_spawn")
    )


class TestValidateAttack:
    def test_incapacitated_attacker_rejected_purely(self, streams, allocator):
        attacker = make_character()
        defender = make_monster("troll", streams, allocator)
        sword = load_equipment().get("sword")
        for condition in (Condition.DEAD, Condition.PETRIFIED, Condition.PARALYSED, Condition.ASLEEP):
            attacker.conditions = ()
            grant_condition(attacker, condition, "effect-x")
            stream = FixedStream([10] * 8)
            rejections = validate_attack(attacker, defender, sword, AttackContext(), ruleset=Ruleset())
            assert [rejection.code for rejection in rejections] == ["combat.attack.attacker_incapacitated"]
            assert stream.draws == 0  # validator purity: no draws
        assert attacker.current_hp == 12  # no mutation

    def test_blind_attacker_cannot_attack(self, streams, allocator):
        attacker = make_character()
        grant_condition(attacker, Condition.BLIND, "effect-x")
        defender = make_monster("troll", streams, allocator)
        rejections = validate_attack(attacker, defender, None, AttackContext(), ruleset=Ruleset())
        assert [rejection.code for rejection in rejections] == ["combat.attack.attacker_blind"]

    def test_melee_beyond_five_feet(self, streams, allocator):
        attacker = make_character()
        defender = make_monster("troll", streams, allocator)
        sword = load_equipment().get("sword")
        rejections = validate_attack(attacker, defender, sword, AttackContext(distance_feet=10), ruleset=Ruleset())
        assert [rejection.code for rejection in rejections] == ["combat.attack.out_of_reach"]

    def test_missile_beyond_long_range(self, streams, allocator):
        attacker = make_character()
        defender = make_monster("troll", streams, allocator)
        bow = load_equipment().get("short_bow")
        beyond = bow.missile_ranges.long.max_feet + 10
        rejections = validate_attack(attacker, defender, bow, AttackContext(distance_feet=beyond), ruleset=Ruleset())
        assert [rejection.code for rejection in rejections] == ["combat.attack.out_of_range"]

    def test_reload_enforced_from_context_under_the_flag(self, streams, allocator):
        attacker = make_character()
        defender = make_monster("troll", streams, allocator)
        crossbow = load_equipment().get("crossbow")
        context = AttackContext(distance_feet=60, fired_last_round=True)
        assert validate_attack(attacker, defender, crossbow, context, ruleset=Ruleset()) == []
        rejections = validate_attack(attacker, defender, crossbow, context, ruleset=Ruleset(weapon_reload=True))
        assert [rejection.code for rejection in rejections] == ["combat.attack.reload"]


class TestAttackRoll:
    def test_matrix_resolution_and_modifiers(self, streams, allocator):
        attacker = make_character(str_score=16)  # +2 melee
        defender = make_monster("troll", streams, allocator)  # AC 4, fighter L1 THAC0 19 → needs 15
        stream = FixedStream([12])  # d20 shows 13
        result = attack_roll(
            attacker, defender, load_equipment().get("sword"), context=AttackContext(), ruleset=Ruleset(), stream=stream
        )
        assert (result.roll, result.modifier, result.total, result.required) == (13, 2, 15, 15)
        assert result.hit

    def test_situational_modifier_stacks(self, streams, allocator):
        attacker = make_character(str_score=16)
        defender = make_monster("troll", streams, allocator)
        stream = FixedStream([10])  # d20 shows 11; +2 STR +2 situational = 15
        result = attack_roll(
            attacker,
            defender,
            load_equipment().get("sword"),
            context=AttackContext(situational_modifier=2),
            ruleset=Ruleset(),
            stream=stream,
        )
        assert result.total == 15 and result.hit

    def test_natural_twenty_always_hits(self, streams, allocator):
        attacker = make_character()
        dragon = make_monster("gold_dragon", streams, allocator)  # AC −2: needs 20 (matrix clamp)
        stream = FixedStream([19])
        result = attack_roll(
            attacker,
            dragon,
            load_equipment().get("sword"),
            context=AttackContext(situational_modifier=-10),
            ruleset=Ruleset(),
            stream=stream,
        )
        assert result.hit and result.natural == 20

    def test_natural_one_always_misses(self, streams, allocator):
        attacker = make_character()
        rat = make_monster("normal_rat", streams, allocator)
        stream = FixedStream([0])
        result = attack_roll(
            attacker,
            rat,
            load_equipment().get("sword"),
            context=AttackContext(situational_modifier=19),
            ruleset=Ruleset(),
            stream=stream,
        )
        assert not result.hit and result.natural == 1

    def test_matrix_versus_arithmetic_divergence_at_the_plateaus(self, streams, allocator):
        # A modified 20 hits matrix-required-20 but misses arithmetic-required-21.
        attacker = make_character()  # THAC0 19
        dragon = make_monster("gold_dragon", streams, allocator)  # AC −2
        context = AttackContext(situational_modifier=1)
        result = attack_roll(
            attacker,
            dragon,
            load_equipment().get("sword"),
            context=context,
            ruleset=Ruleset(),
            stream=FixedStream([18]),
        )
        assert result.total == 20 and result.required == 20 and result.hit
        result = attack_roll(
            attacker,
            dragon,
            load_equipment().get("sword"),
            context=context,
            ruleset=Ruleset(thac0_arithmetic=True),
            stream=FixedStream([18]),
        )
        assert result.total == 20 and result.required == 21 and not result.hit

    def test_arithmetic_hits_below_matrix_floor(self, streams, allocator):
        # The other plateau: a low modified total misses the matrix's clamped floor
        # of 2 but hits the unclamped arithmetic requirement of 1. Fighter L13 has
        # THAC0 10; against AC 9 the matrix requires clamp(10 - 9) = 2 while
        # arithmetic requires 1. A natural 2 at -1 totals 1: matrix miss,
        # arithmetic hit.
        attacker = make_character(level=13)
        assert attacker.thac0 == 10
        rat = make_monster("normal_rat", streams, allocator)  # AC 9
        context = AttackContext(situational_modifier=-1)
        result = attack_roll(
            attacker, rat, load_equipment().get("sword"), context=context, ruleset=Ruleset(), stream=FixedStream([1])
        )
        assert result.total == 1 and result.required == 2 and not result.hit
        result = attack_roll(
            attacker,
            rat,
            load_equipment().get("sword"),
            context=context,
            ruleset=Ruleset(thac0_arithmetic=True),
            stream=FixedStream([1]),
        )
        assert result.total == 1 and result.required == 1 and result.hit

    def test_helpless_defender_auto_hit_consumes_no_draw(self, streams, allocator):
        for condition in (Condition.PARALYSED, Condition.ASLEEP):
            attacker = make_character()
            defender = make_monster("troll", streams, allocator)
            grant_condition(defender, condition, "effect-x")
            stream = FixedStream([10])
            result = attack_roll(
                attacker,
                defender,
                load_equipment().get("mace"),
                context=AttackContext(),
                ruleset=Ruleset(),
                stream=stream,
            )
            assert result.auto and result.hit and result.roll is None
            assert stream.draws == 0
            assert result.events[0].code == "combat.attack.auto_hit"

    def test_no_hit_roll_required_defender(self, streams, allocator):
        attacker = make_character()
        slime = make_monster("green_slime", streams, allocator)
        stream = FixedStream([10])
        result = attack_roll(
            attacker, slime, load_equipment().get("sword"), context=AttackContext(), ruleset=Ruleset(), stream=stream
        )
        assert result.auto and result.hit and stream.draws == 0

    def test_missile_range_bands(self, streams, allocator):
        attacker = make_character()
        defender = make_monster("troll", streams, allocator)
        bow = load_equipment().get("short_bow")
        short = attack_roll(
            attacker,
            defender,
            bow,
            context=AttackContext(distance_feet=bow.missile_ranges.short.max_feet),
            ruleset=Ruleset(),
            stream=FixedStream([10]),
        )
        medium = attack_roll(
            attacker,
            defender,
            bow,
            context=AttackContext(distance_feet=bow.missile_ranges.medium.max_feet),
            ruleset=Ruleset(),
            stream=FixedStream([10]),
        )
        long = attack_roll(
            attacker,
            defender,
            bow,
            context=AttackContext(distance_feet=bow.missile_ranges.long.max_feet),
            ruleset=Ruleset(),
            stream=FixedStream([10]),
        )
        assert (short.modifier, medium.modifier, long.modifier) == (1, 0, -1)

    def test_halfling_missile_bonus_and_defensive_bonus(self, streams, allocator):
        halfling = make_character(class_id="halfling")
        troll = make_monster("troll", streams, allocator)
        bow = load_equipment().get("short_bow")
        result = attack_roll(
            halfling, troll, bow, context=AttackContext(distance_feet=60), ruleset=Ruleset(), stream=FixedStream([10])
        )
        assert result.modifier == 1  # +1 halfling missile tag, DEX 13 is +0, medium band +0
        # Defensive bonus: a large attacker needs 2 more to hit the halfling.
        troll_attack = load_monsters().get("troll").attacks[0].attacks[0]
        without = attack_roll(
            troll, halfling, troll_attack, context=AttackContext(), ruleset=Ruleset(), stream=FixedStream([10])
        )
        with_bonus = attack_roll(
            troll,
            halfling,
            troll_attack,
            context=AttackContext(attacker_large=True),
            ruleset=Ruleset(),
            stream=FixedStream([10]),
        )
        assert with_bonus.required == without.required + 2

    def test_back_stab_bonus_from_the_thief_tag(self, streams, allocator):
        thief = make_character(class_id="thief")
        troll = make_monster("troll", streams, allocator)
        dagger = load_equipment().get("dagger")
        context = AttackContext(behind_target=True, target_unaware=True)
        result = attack_roll(thief, troll, dagger, context=context, ruleset=Ruleset(), stream=FixedStream([10]))
        assert result.modifier == 4
        # A fighter behind an unaware target gets no back-stab.
        fighter = make_character()
        result = attack_roll(fighter, troll, dagger, context=context, ruleset=Ruleset(), stream=FixedStream([10]))
        assert result.modifier == 0

    def test_retreating_defender_plus_two_and_no_shield(self, streams, allocator):
        attacker = make_character()
        defender = make_character(class_id="cleric")
        equipment = load_equipment()
        defender.inventory.shield = ItemInstance(template=equipment.get("shield"))
        base = attack_roll(
            attacker,
            defender,
            equipment.get("sword"),
            context=AttackContext(),
            ruleset=Ruleset(),
            stream=FixedStream([10]),
        )
        retreating = attack_roll(
            attacker,
            defender,
            equipment.get("sword"),
            context=AttackContext(defender_retreating=True),
            ruleset=Ruleset(),
            stream=FixedStream([10]),
        )
        assert retreating.modifier == base.modifier + 2
        assert retreating.required == base.required - 1  # shield AC ignored (AC 1 worse)

    def test_behind_shield_ignored(self, streams, allocator):
        attacker = make_character()
        defender = make_character(class_id="cleric")
        equipment = load_equipment()
        defender.inventory.shield = ItemInstance(template=equipment.get("shield"))
        base = attack_roll(
            attacker,
            defender,
            equipment.get("sword"),
            context=AttackContext(),
            ruleset=Ruleset(),
            stream=FixedStream([10]),
        )
        behind = attack_roll(
            attacker,
            defender,
            equipment.get("sword"),
            context=AttackContext(behind_target=True),
            ruleset=Ruleset(),
            stream=FixedStream([10]),
        )
        assert behind.required == base.required - 1


class TestDamagePipeline:
    def test_immunity_gate_rolls_no_damage(self, streams, allocator):
        fighter = make_character()
        wight = make_monster("wight", streams, allocator)
        stream = FixedStream([18, 7, 7, 7])  # hit, then damage dice would follow
        result = resolve_attack(
            fighter, wight, load_equipment().get("sword"), context=AttackContext(), ruleset=Ruleset(), stream=stream
        )
        assert result.attack_roll.hit and result.absorbed and result.damage is None
        assert stream.draws == 1  # the d20 only — no damage roll
        assert result.events[-1].code == "combat.damage.absorbed"

    def test_silver_passes_the_wight_gate(self, streams, allocator):
        fighter = make_character()
        wight = make_monster("wight", streams, allocator)
        stream = FixedStream([18, 3])
        result = resolve_attack(
            fighter,
            wight,
            load_equipment().get("silver_dagger"),
            context=AttackContext(),
            ruleset=Ruleset(),
            stream=stream,
        )
        assert not result.absorbed and result.damage == 4

    def test_hd5_flag_lets_big_monsters_through(self, streams, allocator):
        troll = make_monster("troll", streams, allocator)  # 6+3 HD
        wight = make_monster("wight", streams, allocator)
        talon = load_monsters().get("troll").attacks[0].attacks[0]
        source = damage_source_for(troll, talon, AttackContext())
        assert check_immunity(wight, source, ruleset=Ruleset(), attacker=troll)
        assert not check_immunity(wight, source, ruleset=Ruleset(hd5_counts_as_magical=True), attacker=troll)

    def test_hd5_flag_invulnerable_monster_reading(self, streams, allocator):
        # A wraith (silver/magic gate itself, 4 HD) counts as "another invulnerable
        # monster" and bypasses the wight's gate under the flag.
        wraith = make_monster("wraith", streams, allocator)
        wight = make_monster("wight", streams, allocator)
        touch = load_monsters().get("wraith").attacks[0].attacks[0]
        source = damage_source_for(wraith, touch, AttackContext())
        assert check_immunity(wight, source, ruleset=Ruleset(), attacker=wraith)
        assert not check_immunity(wight, source, ruleset=Ruleset(hd5_counts_as_magical=True), attacker=wraith)

    def test_hd5_flag_never_touches_element_gates(self, streams, allocator):
        # The black pudding's fire-only gate is not a silver/magic subset: unaffected.
        troll = make_monster("troll", streams, allocator)
        pudding = make_monster("black_pudding", streams, allocator)
        talon = load_monsters().get("troll").attacks[0].attacks[0]
        source = damage_source_for(troll, talon, AttackContext())
        assert check_immunity(pudding, source, ruleset=Ruleset(hd5_counts_as_magical=True), attacker=troll)

    def test_mummy_gate_and_half_damage(self, streams, allocator):
        fighter = make_character()
        mummy = make_monster("mummy", streams, allocator)
        sword_source = damage_source_for(fighter, load_equipment().get("sword"), AttackContext())
        assert check_immunity(mummy, sword_source, ruleset=Ruleset())
        torch = load_equipment().get("torch")
        torch_source = damage_source_for(fighter, torch, AttackContext())
        assert not check_immunity(mummy, torch_source, ruleset=Ruleset())
        hp = mummy.current_hp
        deal_damage(mummy, 7, source=torch_source)
        assert hp - mummy.current_hp == 3  # halved, floored

    def test_reduction_floors_at_one(self, streams, allocator):
        wraith = make_monster("wraith", streams, allocator)
        silver = damage_source_for(make_character(), load_equipment().get("silver_dagger"), AttackContext())
        hp = wraith.current_hp
        deal_damage(wraith, 1, source=silver)
        assert hp - wraith.current_hp == 1

    def test_minimum_one_on_a_hit(self):
        thief = make_character(class_id="thief", str_score=5)  # −2 melee
        dagger = load_equipment().get("dagger")
        result = damage_roll(thief, dagger, context=AttackContext(), ruleset=Ruleset(), stream=FixedStream([0]))
        assert result.total == 1  # 1 − 2 floors at 1

    def test_variable_damage_flag_off(self, streams):
        fighter = make_character()
        ruleset = Ruleset(variable_weapon_damage=False)
        sword = load_equipment().get("sword")  # 1d8 normally
        result = damage_roll(fighter, sword, context=AttackContext(), ruleset=ruleset, stream=FixedStream([5]))
        assert result.rolls == (6,)  # a d6 was rolled
        torch = load_equipment().get("torch")  # facet 1d4 normally
        result = damage_roll(fighter, torch, context=AttackContext(), ruleset=ruleset, stream=FixedStream([5]))
        assert result.rolls == (6,)
        unarmed = damage_roll(fighter, None, context=AttackContext(), ruleset=ruleset, stream=FixedStream([1]))
        assert unarmed.rolls == (2,)  # unarmed stays 1d2
        troll_bite = load_monsters().get("troll").attacks[0].attacks[1]
        result = damage_roll(fighter, troll_bite, context=AttackContext(), ruleset=ruleset, stream=FixedStream([9]))
        assert result.rolls == (10,)  # monster damage unaffected (1d10)

    def test_brace_and_charge_doubling(self):
        fighter = make_character()
        spear = load_equipment().get("spear")  # brace quality
        lance = load_equipment().get("lance")  # charge quality
        braced = damage_roll(
            fighter, spear, context=AttackContext(braced=True), ruleset=Ruleset(), stream=FixedStream([3])
        )
        assert braced.total == 8  # (4 + 0) × 2
        charged = damage_roll(
            fighter, lance, context=AttackContext(charging=True), ruleset=Ruleset(), stream=FixedStream([3])
        )
        assert charged.total == 8

    def test_back_stab_doubles_damage(self):
        thief = make_character(class_id="thief")
        dagger = load_equipment().get("dagger")
        context = AttackContext(behind_target=True, target_unaware=True)
        result = damage_roll(thief, dagger, context=context, ruleset=Ruleset(), stream=FixedStream([3]))
        assert result.total == 8

    def test_unarmed_deals_1d2(self):
        fighter = make_character()
        result = damage_roll(fighter, None, context=AttackContext(), ruleset=Ruleset(), stream=FixedStream([1]))
        assert result.rolls == (2,) and result.total == 2

    def test_sleeping_defender_dies_to_a_blade(self, streams, allocator):
        # "A single attack with a bladed weapon can kill a creature enchanted by
        # this spell" — the melee hit kills outright, no damage roll. Pinned:
        # bladed means a melee-quality weapon without the blunt quality.
        attacker = make_character()
        sleeper = make_monster("troll", streams, allocator)
        grant_condition(sleeper, Condition.ASLEEP, "effect-x")
        stream = FixedStream([])  # auto-hit, and the kill rolls no damage
        result = resolve_attack(
            attacker, sleeper, load_equipment().get("sword"), context=AttackContext(), ruleset=Ruleset(), stream=stream
        )
        assert result.attack_roll.auto and result.damage is None
        assert has_condition(sleeper, Condition.DEAD)
        assert stream.draws == 0
        assert "combat.death.died" in [event.code for event in result.events]

    def test_blunt_weapon_on_a_sleeper_deals_normal_damage(self, streams, allocator):
        attacker = make_character()
        sleeper = make_monster("troll", streams, allocator)
        grant_condition(sleeper, Condition.ASLEEP, "effect-x")
        result = resolve_attack(
            attacker,
            sleeper,
            load_equipment().get("mace"),
            context=AttackContext(),
            ruleset=Ruleset(),
            stream=FixedStream([3]),
        )
        assert result.damage == 4  # 1d6 shows 4; no outright kill
        assert not has_condition(sleeper, Condition.DEAD)

    def test_blade_kill_respects_the_immunity_gate(self, streams, allocator):
        # A sleeping black pudding is still only harmed by fire: the sword is
        # absorbed before the dies-to-a-blade hook can fire.
        attacker = make_character()
        pudding = make_monster("black_pudding", streams, allocator)
        grant_condition(pudding, Condition.ASLEEP, "effect-x")
        result = resolve_attack(
            attacker,
            pudding,
            load_equipment().get("sword"),
            context=AttackContext(),
            ruleset=Ruleset(),
            stream=FixedStream([]),
        )
        assert result.absorbed
        assert not has_condition(pudding, Condition.DEAD)

    def test_thrown_blade_does_not_kill_a_sleeper(self, streams, allocator):
        # The hook is melee-only: a thrown dagger resolves as a normal missile
        # attack (no helpless auto-hit, no outright kill).
        attacker = make_character()
        sleeper = make_monster("troll", streams, allocator)
        grant_condition(sleeper, Condition.ASLEEP, "effect-x")
        result = resolve_attack(
            attacker,
            sleeper,
            load_equipment().get("dagger"),
            context=AttackContext(distance_feet=15),
            ruleset=Ruleset(),
            stream=FixedStream([18, 2]),
        )
        assert not result.attack_roll.auto
        assert not has_condition(sleeper, Condition.DEAD)

    def test_death_at_zero_emits(self, streams, allocator):
        rat = make_monster("normal_rat", streams, allocator)
        source = damage_source_for(make_character(), load_equipment().get("sword"), AttackContext())
        events = deal_damage(rat, rat.current_hp, source=source)
        assert "combat.death.died" in [event.code for event in events]
        assert has_condition(rat, Condition.DEAD)

    def test_destructive_death_destroys_equipment(self, streams, allocator):
        fighter = make_character()
        equipment = load_equipment()
        fighter.inventory.items.append(ItemInstance(template=equipment.get("rope")))
        fighter.inventory.wielded.append(ItemInstance(template=equipment.get("sword")))
        dragon = make_monster("red_dragon", streams, allocator)
        events = resolve_breath(dragon, [fighter], ruleset=Ruleset(), stream=FixedStream([0, 0]))
        codes = [event.code for event in events]
        assert "combat.death.died" in codes and "combat.equipment.destroyed" in codes
        assert fighter.inventory.items == [] and fighter.inventory.wielded == []


class TestSplashWeapons:
    def setup_battle(self, streams, allocator):
        fighter = make_character()
        ledger, clock = EffectsLedger(), GameClock()
        return fighter, ledger, clock

    def throw(self, attacker, defender, item_id, context, streams, allocator, ledger, clock, stream):
        registry = {getattr(defender, "id", "x"): defender, "pc-fighter": attacker}
        return resolve_splash_attack(
            attacker,
            defender,
            load_equipment().get(item_id),
            context=context,
            ruleset=Ruleset(),
            stream=stream,
            ledger=ledger,
            clock=clock,
            allocator=allocator,
            registry=registry,
        )

    def test_douse_applies_twice_then_expires(self, streams, allocator):
        fighter, ledger, clock = self.setup_battle(streams, allocator)
        troll = make_monster("troll", streams, allocator)
        stream = FixedStream([18, 4])  # hit; 1d8 shows 5
        context = AttackContext(distance_feet=20, lit=True)
        result = self.throw(fighter, troll, "oil_flask", context, streams, allocator, ledger, clock, stream)
        assert result.damage == 5
        assert troll.nonregen_damage == 5
        assert len(ledger.effects) == 1
        registry = {troll.id: troll}
        events = ledger.advance(clock, 1, TimeUnit.ROUND, registry, stream=streams.get("effects"))
        assert "combat.damage.dealt" in [event.code for event in events]
        assert troll.nonregen_damage > 5  # the second application also routes non-regenerable
        assert ledger.effects == []  # two applications, then done
        events = ledger.advance(clock, 1, TimeUnit.ROUND, registry, stream=streams.get("effects"))
        assert events == []

    def test_holy_water_no_effect_on_the_living_never_a_rejection(self, streams, allocator):
        fighter, ledger, clock = self.setup_battle(streams, allocator)
        troll = make_monster("troll", streams, allocator)
        context = AttackContext(distance_feet=20)
        assert validate_attack(fighter, troll, load_equipment().get("holy_water"), context, ruleset=Ruleset()) == []
        stream = FixedStream([18])
        result = self.throw(fighter, troll, "holy_water", context, streams, allocator, ledger, clock, stream)
        assert result.attack_roll.hit and result.absorbed
        assert ledger.effects == []

    def test_holy_water_admitted_through_the_wight_gate(self, streams, allocator):
        fighter, ledger, clock = self.setup_battle(streams, allocator)
        wight = make_monster("wight", streams, allocator)
        stream = FixedStream([18, 6])
        result = self.throw(
            fighter, wight, "holy_water", AttackContext(distance_feet=20), streams, allocator, ledger, clock, stream
        )
        assert not result.absorbed and result.damage == 7
        assert len(ledger.effects) == 1  # the douse

    def test_unlit_oil_deals_no_damage(self, streams, allocator):
        fighter, ledger, clock = self.setup_battle(streams, allocator)
        troll = make_monster("troll", streams, allocator)
        stream = FixedStream([18])
        result = self.throw(
            fighter, troll, "oil_flask", AttackContext(distance_feet=20), streams, allocator, ledger, clock, stream
        )
        assert result.attack_roll.hit and result.damage == 0
        assert troll.current_hp == troll.max_hp and ledger.effects == []

    def test_uses_fire_monsters_ignore_burning_oil(self, streams, allocator):
        fighter, ledger, clock = self.setup_battle(streams, allocator)
        hound = make_monster("hellhound_4", streams, allocator)
        stream = FixedStream([18])
        result = self.throw(
            fighter,
            hound,
            "oil_flask",
            AttackContext(distance_feet=20, lit=True),
            streams,
            allocator,
            ledger,
            clock,
            stream,
        )
        assert result.attack_roll.hit and result.absorbed
        assert hound.current_hp == hound.max_hp


class TestInitiative:
    def test_side_initiative_reroll_on_ties(self):
        participants = [Participant(key="pc-1", side="party"), Participant(key="m-1", side="monsters")]
        stream = FixedStream([3, 3, 2, 5])  # tie 4/4, re-roll 3 and 6
        result = roll_initiative(participants, ruleset=Ruleset(), stream=stream)
        assert stream.draws == 4
        by_key = {entry.key: entry for entry in result.entries}
        assert by_key["party"].rolls == (4, 3)
        assert by_key["monsters"].rolls == (4, 6)
        assert result.order == ("m-1", "pc-1")

    def test_slow_actors_act_last_by_side_initiative(self):
        participants = [
            Participant(key="pc-sword", side="party"),
            Participant(key="pc-two-hander", side="party", slow=True),
            Participant(key="m-troll", side="monsters"),
        ]
        stream = FixedStream([5, 2])  # party 6, monsters 3
        result = roll_initiative(participants, ruleset=Ruleset(), stream=stream)
        assert result.order == ("pc-sword", "m-troll", "pc-two-hander")

    def test_individual_initiative_with_modifiers(self, streams, allocator):
        halfling = make_character(class_id="halfling")
        troll = make_monster("troll", streams, allocator)
        assert participant_modifier(halfling) == 1  # the class tag; DEX 10 is +0
        assert participant_modifier(troll, monster_modifier=-1) == -1
        participants = [
            Participant(key="pc-h", side="party", modifier=participant_modifier(halfling)),
            Participant(key="m-t", side="monsters", modifier=participant_modifier(troll, monster_modifier=-1)),
        ]
        stream = FixedStream([2, 3])  # halfling 3+1=4, troll 4−1=3
        result = roll_initiative(participants, ruleset=Ruleset(individual_initiative=True), stream=stream)
        assert result.order == ("pc-h", "m-t")
        assert result.mode == "individual"

    def test_tied_individuals_reroll_among_themselves(self):
        participants = [
            Participant(key="a", side="party"),
            Participant(key="b", side="party"),
            Participant(key="c", side="monsters"),
        ]
        stream = FixedStream([3, 3, 5, 1, 2])  # a and b tie at 4; c has 6; a re-rolls 2, b 3
        result = roll_initiative(participants, ruleset=Ruleset(individual_initiative=True), stream=stream)
        by_key = {entry.key: entry for entry in result.entries}
        assert by_key["a"].rolls == (4, 2)
        assert by_key["b"].rolls == (4, 3)
        assert by_key["c"].rolls == (6,)
        assert result.order == ("c", "b", "a")


class TestMorale:
    def test_two_never_fights_twelve_never_checks(self):
        stream = FixedStream([])
        result = check_morale("cowards", 2, stream=stream)
        assert not result.held and result.exempt and stream.draws == 0
        result = check_morale("fearless", 12, stream=stream)
        assert result.held and result.exempt and stream.draws == 0

    def test_adjustments_clamp_to_plus_minus_two(self):
        result = check_morale("group", 7, modifier=5, stream=FixedStream([3, 2]))
        assert result.modifier == 2
        result = check_morale("group", 7, modifier=-5, stream=FixedStream([3, 2]))
        assert result.modifier == -2

    def test_over_means_flee(self):
        result = check_morale("group", 8, stream=FixedStream([5, 4]))  # 6+5 = 11 > 8
        assert not result.held
        assert result.events[0].code == "combat.morale.broke"
        result = check_morale("group", 8, stream=FixedStream([3, 3]))  # 4+4 = 8 ≤ 8
        assert result.held

    def test_two_passed_checks_mean_no_further_checks(self):
        tracker = MoraleTracker()
        assert tracker.check("trolls", 10, stream=FixedStream([3, 3])).held
        assert tracker.check("trolls", 10, stream=FixedStream([3, 3])).held
        assert tracker.check("trolls", 10, stream=FixedStream([5, 5])) is None

    def test_triggers_and_incapacitated_definition(self, streams, allocator):
        side = [make_monster("troll", streams, allocator) for _ in range(4)]
        assert morale_triggers(side) == []
        grant_condition(side[0], Condition.PARALYSED, "effect-x")
        assert incapacitated(side[0])
        assert morale_triggers(side) == []  # 1 of 4 is not half
        grant_condition(side[1], Condition.DEAD, None)
        assert morale_triggers(side) == ["first_death", "half_incapacitated"]


class TestSavingThrows:
    def test_character_values_from_the_progression_row(self):
        fighter = make_character()
        result = saving_throw(fighter, SaveCategory.DEATH, stream=FixedStream([11]))
        assert result.required == 12 and result.roll == 12 and result.passed

    def test_monster_values_from_the_stat_block(self, streams, allocator):
        troll = make_monster("troll", streams, allocator)
        result = saving_throw(troll, SaveCategory.SPELLS, stream=FixedStream([12]))
        assert result.required == 14 and not result.passed

    def test_wis_modifier_only_when_magical_and_never_on_breath(self):
        wise = make_character()
        wise.scores[AbilityScore.WIS] = 16  # +2 magic saves
        mundane = saving_throw(wise, SaveCategory.SPELLS, stream=FixedStream([9]))
        assert mundane.modifier == 0
        magical = saving_throw(wise, SaveCategory.SPELLS, magical=True, stream=FixedStream([9]))
        assert magical.modifier == 2
        breath = saving_throw(wise, SaveCategory.BREATH, magical=True, stream=FixedStream([9]))
        assert breath.modifier == 0

    def test_auto_save_defense(self, streams, allocator):
        dragon = make_monster("red_dragon", streams, allocator)
        stream = FixedStream([])
        result = saving_throw(dragon, SaveCategory.SPELLS, magical=True, element="fire", stream=stream)
        assert result.auto and result.passed and stream.draws == 0
        assert result.events[0].code == "combat.save.auto"


class TestBreathAndGaze:
    def test_dragon_three_per_day(self, streams, allocator):
        dragon = make_monster("red_dragon", streams, allocator)
        tough = make_character(level=9, max_hp=200, current_hp=200)
        for _ in range(3):
            assert validate_breath(dragon) == []
            resolve_breath(dragon, [tough], ruleset=Ruleset(), stream=streams.get("combat"))
        rejections = validate_breath(dragon)
        assert [rejection.code for rejection in rejections] == ["combat.breath.exhausted"]
        with pytest.raises(ValueError):
            resolve_breath(dragon, [tough], ruleset=Ruleset(), stream=streams.get("combat"))

    def test_breath_damage_is_current_hp_save_for_half_floored(self, streams, allocator):
        dragon = make_monster("red_dragon", streams, allocator)
        dragon.current_hp = 31
        tough = make_character(level=9, max_hp=200, current_hp=200)
        events = resolve_breath(dragon, [tough], ruleset=Ruleset(), stream=FixedStream([19]))  # save passes
        damage = next(event for event in events if event.code == "combat.damage.dealt")
        assert damage.amount == 15  # 31 // 2, floored

    def test_hellhound_dice_breath_no_limit(self, streams, allocator):
        hound = make_monster("hellhound_3", streams, allocator)
        tough = make_character(level=9, max_hp=200, current_hp=200)
        for _ in range(5):
            assert validate_breath(hound) == []
            resolve_breath(hound, [tough], ruleset=Ruleset(), stream=streams.get("combat"))
        assert hound.breath_uses_today == 0

    def test_sea_dragon_save_or_die(self, streams, allocator):
        dragon = make_monster("sea_dragon", streams, allocator)
        victim = make_character()
        events = resolve_breath(dragon, [victim], ruleset=Ruleset(), stream=FixedStream([2]))
        assert "combat.death.died" in [event.code for event in events]
        survivor = make_character(class_id="cleric")
        events = resolve_breath(dragon, [survivor], ruleset=Ruleset(), stream=FixedStream([19]))
        assert "combat.death.died" not in [event.code for event in events]
        assert survivor.current_hp == survivor.max_hp

    def test_gaze_petrifies_unless_averted(self, streams, allocator):
        medusa = make_monster("medusa", streams, allocator)
        victim = make_character()
        averter = make_character(class_id="cleric")
        grant_condition(averter, Condition.AVERTED_EYES, "effect-x")
        ledger, clock = EffectsLedger(), GameClock()
        registry = {victim.id: victim, averter.id: averter}
        resolve_gaze(
            medusa,
            [victim, averter],
            stream=FixedStream([2]),
            ledger=ledger,
            clock=clock,
            allocator=allocator,
            registry=registry,
        )
        assert has_condition(victim, Condition.PETRIFIED)
        assert not has_condition(averter, Condition.PETRIFIED)
        petrification = ledger.active_on(victim.id, "petrification")[0]
        assert petrification.expires_round is None  # permanent — recoverable, not dead


class TestTargeting:
    def test_up_to_n_fixed_and_rolled(self, streams, allocator):
        candidates = [make_monster("goblin", streams, allocator) for _ in range(5)]
        selected, _ = select_targets(TargetingMode.UP_TO_N, candidates, stream=FixedStream([]), count=3)
        assert selected == candidates[:3]
        selected, _ = select_targets(TargetingMode.UP_TO_N, candidates, stream=FixedStream([1]), count_dice="1d4")
        assert selected == candidates[:2]

    def test_hd_budget_weakest_first_whole_creatures_skip_and_continue(self, streams, allocator):
        troll = make_monster("troll", streams, allocator)  # 6 HD
        goblin = make_monster("goblin", streams, allocator)  # sub-1 → 1
        ghoul = make_monster("ghoul", streams, allocator)  # 2 HD
        fighter = make_character(level=4)  # characters count their level
        selected, events = select_targets(
            TargetingMode.HD_BUDGET, [troll, goblin, ghoul, fighter], stream=FixedStream([]), hd_budget=7
        )
        # Weakest-first: goblin (1), ghoul (2), fighter (4) = 7; the troll exceeds the remainder.
        assert [getattr(target, "id", None) for target in selected] == [goblin.id, ghoul.id, "pc-fighter"]
        assert events[0].target_ids == (goblin.id, ghoul.id, "pc-fighter")

    def test_effective_hd_pins(self, streams, allocator):
        assert effective_hd(make_monster("goblin", streams, allocator)) == 1  # 1-1 rounds up
        assert effective_hd(make_monster("troll", streams, allocator)) == 6  # +3 bonus dropped
        assert effective_hd(make_character(level=3)) == 3

    def test_area_and_gaze_affect_all_candidates(self, streams, allocator):
        candidates = [make_monster("goblin", streams, allocator) for _ in range(3)]
        for mode in (TargetingMode.AREA, TargetingMode.GAZE):
            selected, _ = select_targets(mode, candidates, stream=FixedStream([]))
            assert selected == candidates


class TestHealingAndFalling:
    def test_healing_caps_at_max(self):
        from osrlib.core.combat import apply_healing

        fighter = make_character()
        fighter.current_hp = 10
        events = apply_healing(fighter, 10)
        assert fighter.current_hp == 12
        assert events[0].amount == 2

    def test_dead_cannot_be_healed(self):
        from osrlib.core.combat import apply_healing

        fighter = make_character()
        grant_condition(fighter, Condition.DEAD, None)
        assert apply_healing(fighter, 5) == []

    def test_falling_damage_floors_per_full_ten_feet(self):
        assert falling_damage(9, FixedStream([])) is None
        result = falling_damage(25, FixedStream([3, 4]))
        assert len(result.rolls) == 2
        result = falling_damage(30, FixedStream([3, 4, 5]))
        assert len(result.rolls) == 3
