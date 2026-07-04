"""Tests for casting: the pipeline, the automated census, and the pinned parameters."""

import pytest

from osrlib.core.abilities import AbilityScore
from osrlib.core.alignment import Alignment
from osrlib.core.character import Character
from osrlib.core.clock import ROUNDS_PER_DAY, ROUNDS_PER_TURN, GameClock, TimeUnit
from osrlib.core.combat import (
    AttackContext,
    DamageSource,
    SaveCategory,
    attack_roll,
    check_immunity,
    check_morale,
    damage_roll,
    deal_damage,
    morale_modifier,
    resolve_attack,
    saving_throw,
    validate_attack,
)
from osrlib.core.effects import (
    Condition,
    EffectDefinition,
    EffectsLedger,
    has_condition,
    modifier_total,
)
from osrlib.core.items import Inventory, purchase
from osrlib.core.monsters import IdAllocator, spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.spells import (
    MAGIC_STREAM,
    CastContext,
    MemorizedSpell,
    cast_spell,
    disrupt_casting,
    pop_mirror_image,
    validate_cast,
)
from osrlib.data import load_classes, load_equipment, load_monsters, load_spells

MASTER_SEED = 20_260_703

BASE_SCORES = {
    AbilityScore.STR: 10,
    AbilityScore.INT: 10,
    AbilityScore.WIS: 10,
    AbilityScore.DEX: 10,
    AbilityScore.CON: 10,
    AbilityScore.CHA: 10,
}


def build_caster(class_id, level, *, name=None, memorized=(), spell_book=(), scores=None, alignment=Alignment.LAWFUL):
    definition = load_classes().get(class_id)
    return Character(
        id=f"pc-{name or class_id}",
        name=name or f"Test {class_id}",
        class_id=class_id,
        race=definition.race,
        level=level,
        xp=definition.row(level).xp,
        scores={**BASE_SCORES, **(scores or {})},
        alignment=alignment,
        max_hp=4 * level + 4,
        current_hp=4 * level + 4,
        spell_book=tuple(spell_book),
        memorized_spells=tuple(MemorizedSpell(spell_id=spell_id) for spell_id in memorized),
    )


class Harness:
    """One casting scene: streams, ledger, clock, and a live registry."""

    def __init__(self, seed=MASTER_SEED):
        self.streams = RngStreams(master_seed=seed)
        self.ruleset = Ruleset()
        self.clock = GameClock()
        self.ledger = EffectsLedger()
        self.allocator = IdAllocator()
        self.registry = {}
        self.magic = self.streams.get(MAGIC_STREAM)
        self.effects = self.streams.get("effects")

    def add(self, *combatants):
        for combatant in combatants:
            self.registry[combatant.id] = combatant
        return combatants[0] if len(combatants) == 1 else combatants

    def monster(self, monster_id, *, alignment=None):
        template = load_monsters().get(monster_id)
        instance = spawn_monster(
            template,
            id=self.allocator.allocate("monster"),
            stream=self.streams.get("monster_spawn"),
            alignment=alignment,
        )
        self.registry[instance.id] = instance
        return instance

    def cast(self, caster, spell_id, mode, *, reversed=False, targets=(), context=None):
        return cast_spell(
            caster,
            load_spells().get(spell_id),
            mode,
            reversed=reversed,
            targets=targets,
            context=context,
            ledger=self.ledger,
            clock=self.clock,
            allocator=self.allocator,
            registry=self.registry,
            ruleset=self.ruleset,
            stream=self.magic,
            effects_stream=self.effects,
        )


def scan_seeds(predicate, *, tries=60):
    """Run a scenario across seeds until the predicate-marked outcome appears."""
    for seed in range(MASTER_SEED, MASTER_SEED + tries):
        outcome = predicate(seed)
        if outcome is not None:
            return outcome
    pytest.fail("no seed produced the wanted outcome")


class TestCastingPipeline:
    def test_consumes_first_matching_copy(self):
        harness = Harness()
        cleric = build_caster("cleric", 4, memorized=("bless", "cure_light_wounds", "cure_light_wounds"))
        harness.add(cleric)
        harness.cast(cleric, "cure_light_wounds", "heal", targets=[cleric])
        assert [copy.spell_id for copy in cleric.memorized_spells] == ["bless", "cure_light_wounds"]

    def test_divine_reversed_cast_consumes_a_normal_copy(self):
        harness = Harness()
        cleric = build_caster("cleric", 4, memorized=("cure_light_wounds",))
        goblin = harness.monster("goblin")
        harness.add(cleric)
        result = harness.cast(cleric, "cure_light_wounds", "harm", reversed=True, targets=[goblin])
        assert cleric.memorized_spells == ()
        assert result.reversed is True

    def test_arcane_form_must_match(self):
        magic_user = build_caster("magic_user", 3, spell_book=("light_mu",), memorized=("light_mu",))
        rejections = validate_cast(magic_user, load_spells().get("light_mu"), "blind", reversed=True)
        assert "magic.cast.not_memorized" in [rejection.code for rejection in rejections]

    def test_disruption_consumes_identically(self):
        cleric = build_caster("cleric", 4, memorized=("bless", "cure_light_wounds"))
        events = disrupt_casting(cleric, "cure_light_wounds")
        assert events[0].code == "magic.cast.disrupted"
        assert [copy.spell_id for copy in cleric.memorized_spells] == ["bless"]
        with pytest.raises(ValueError):
            disrupt_casting(cleric, "cure_light_wounds")

    def test_manual_mode_bookkeeping(self):
        harness = Harness()
        magic_user = build_caster("magic_user", 1, spell_book=("ventriloquism",), memorized=("ventriloquism",))
        harness.add(magic_user)
        result = harness.cast(magic_user, "ventriloquism", "cast")
        assert result.manual is True
        assert result.prose  # the SRD text rides the result for the narrator
        assert magic_user.memorized_spells == ()
        assert result.events[0].code == "magic.cast.cast" and result.events[0].manual is True

    def test_validator_purity_no_draws_no_mutation(self):
        harness = Harness()
        magic_user = build_caster("magic_user", 1, spell_book=("sleep",), memorized=("sleep",))
        before_stream = harness.magic.export_state()
        before_copies = magic_user.memorized_spells
        rejections = validate_cast(magic_user, load_spells().get("fire_ball"), "damage", targets=[])
        assert rejections
        assert harness.magic.export_state() == before_stream
        assert magic_user.memorized_spells == before_copies

    @pytest.mark.parametrize(
        "condition",
        [
            Condition.DEAD,
            Condition.PETRIFIED,
            Condition.PARALYSED,
            Condition.ASLEEP,
            Condition.SILENCED,
            Condition.FEEBLEMINDED,
            Condition.WEAKENED,
        ],
    )
    def test_incapacity_rejections(self, condition):
        from osrlib.core.effects import ActiveCondition

        cleric = build_caster("cleric", 2, memorized=("cure_light_wounds",))
        cleric.conditions = (ActiveCondition(condition=condition, effect_id=None),)
        rejections = validate_cast(cleric, load_spells().get("cure_light_wounds"), "heal", targets=[cleric])
        assert rejections[0].code == "magic.cast.caster_incapacitated"
        assert rejections[0].params["condition"] == condition.value

    def test_restrained_rejections(self):
        cleric = build_caster("cleric", 2, memorized=("cure_light_wounds",))
        for context in (CastContext(bound=True), CastContext(gagged=True)):
            rejections = validate_cast(
                cleric, load_spells().get("cure_light_wounds"), "heal", targets=[cleric], context=context
            )
            assert rejections[0].code == "magic.cast.caster_restrained"

    def test_anti_magic_shell_blocks_own_casting(self):
        harness = Harness()
        magic_user = build_caster(
            "magic_user", 11, spell_book=("anti_magic_shell", "magic_missile"), memorized=("anti_magic_shell",)
        )
        harness.add(magic_user)
        harness.cast(magic_user, "anti_magic_shell", "shell")
        magic_user.memorized_spells = (MemorizedSpell(spell_id="magic_missile"),)
        goblin = harness.monster("goblin")
        rejections = validate_cast(
            magic_user, load_spells().get("magic_missile"), "missiles", targets=[goblin] * 5, ledger=harness.ledger
        )
        assert rejections[0].code == "magic.cast.anti_magic_shell"

    def test_range_validated_only_with_a_distance(self):
        magic_user = build_caster("magic_user", 5, spell_book=("fire_ball",), memorized=("fire_ball",))
        goblin_stub = build_caster("fighter", 1, name="stub")
        spell = load_spells().get("fire_ball")
        assert validate_cast(magic_user, spell, "damage", targets=[goblin_stub]) == []
        rejections = validate_cast(
            magic_user, spell, "damage", targets=[goblin_stub], context=CastContext(distance_feet=300)
        )
        assert rejections[0].code == "magic.cast.out_of_range"

    def test_casting_breaks_the_casters_invisibility(self):
        harness = Harness()
        magic_user = build_caster(
            "magic_user", 5, spell_book=("invisibility", "magic_missile"), memorized=("invisibility",)
        )
        harness.add(magic_user)
        harness.cast(magic_user, "invisibility", "invisibility", targets=[magic_user])
        assert has_condition(magic_user, Condition.INVISIBLE)
        magic_user.memorized_spells = (MemorizedSpell(spell_id="magic_missile"),)
        goblin = harness.monster("goblin")
        result = harness.cast(magic_user, "magic_missile", "missiles", targets=[goblin])
        assert not has_condition(magic_user, Condition.INVISIBLE)
        assert any(event.code == "effects.effect.released" for event in result.events)

    def test_no_effect_still_consumes_the_copy(self):
        # The no-leak doctrine: *charm person* at a zombie is a spent spell and a
        # no-effect report, never a rejection.
        harness = Harness()
        magic_user = build_caster("magic_user", 3, spell_book=("charm_person",), memorized=("charm_person",))
        zombie = harness.monster("zombie")
        harness.add(magic_user)
        result = harness.cast(magic_user, "charm_person", "charm", targets=[zombie])
        assert result.no_effect is True
        assert result.events[0].code == "magic.cast.no_effect"
        assert magic_user.memorized_spells == ()
        assert not has_condition(zombie, Condition.CHARMED)


class TestMagicMissile:
    @pytest.mark.parametrize(("level", "missiles"), [(1, 1), (5, 1), (6, 3), (10, 3), (11, 5)])
    def test_count_breakpoints(self, level, missiles):
        magic_user = build_caster("magic_user", level, spell_book=("magic_missile",), memorized=("magic_missile",))
        stub = build_caster("fighter", 1, name="stub")
        rejections = validate_cast(
            magic_user, load_spells().get("magic_missile"), "missiles", targets=[stub] * (missiles + 1)
        )
        assert rejections[0].code == "magic.cast.target_count"
        assert rejections[0].params["expected"] == missiles
        assert (
            validate_cast(magic_user, load_spells().get("magic_missile"), "missiles", targets=[stub] * missiles) == []
        )

    def test_auto_hit_no_save_and_stacking(self):
        harness = Harness()
        magic_user = build_caster("magic_user", 6, spell_book=("magic_missile",), memorized=("magic_missile",))
        ogre = harness.monster("ogre")
        harness.add(magic_user)
        result = harness.cast(magic_user, "magic_missile", "missiles", targets=[ogre, ogre, ogre])
        damage_events = [event for event in result.events if event.code == "combat.damage.dealt"]
        assert len(damage_events) == 3  # three missiles stacked on one target
        assert all(3 <= event.amount for event in damage_events)  # 1d6+1 each, no halving
        assert not any(event.code.startswith("combat.save") for event in result.events)
        assert not any(event.code.startswith("combat.attack") for event in result.events)


class TestFireBallAndLightningBolt:
    def test_damage_scales_per_caster_level(self):
        harness = Harness()
        magic_user = build_caster("magic_user", 7, spell_book=("fire_ball",), memorized=("fire_ball",))
        ogre = harness.monster("ogre")
        harness.add(magic_user)
        result = harness.cast(magic_user, "fire_ball", "damage", targets=[ogre])
        damage = next(event for event in result.events if event.code == "combat.damage.dealt")
        assert len(damage.rolls) == 7  # 1d6 per caster level

    def test_save_half_floors(self):
        def probe(seed):
            harness = Harness(seed)
            magic_user = build_caster("magic_user", 5, spell_book=("fire_ball",), memorized=("fire_ball",))
            ogre = harness.monster("ogre")
            harness.add(magic_user)
            result = harness.cast(magic_user, "fire_ball", "damage", targets=[ogre])
            save = next(event for event in result.events if event.code.startswith("combat.save"))
            damage = next((event for event in result.events if event.code == "combat.damage.dealt"), None)
            if save.code == "combat.save.passed" and damage is not None:
                return (sum(damage.rolls), damage.amount)
            return None

        total, amount = scan_seeds(probe)
        assert amount == total // 2

    def test_red_dragon_auto_saves_against_fire_ball(self):
        harness = Harness()
        magic_user = build_caster("magic_user", 5, spell_book=("fire_ball",), memorized=("fire_ball",))
        dragon = harness.monster("red_dragon")
        harness.add(magic_user)
        result = harness.cast(magic_user, "fire_ball", "damage", targets=[dragon])
        save = next(event for event in result.events if event.code.startswith("combat.save"))
        assert save.code == "combat.save.auto"

    def test_lightning_bolt_kill_destroys_equipment(self):
        def probe(seed):
            harness = Harness(seed)
            magic_user = build_caster("magic_user", 10, spell_book=("lightning_bolt",), memorized=("lightning_bolt",))
            victim = build_caster("fighter", 1, name="victim")
            victim.inventory = Inventory()
            victim.inventory.purse.gp = 100
            purchase(victim.inventory, load_equipment().get("sword"), 1)
            harness.add(magic_user, victim)
            result = harness.cast(magic_user, "lightning_bolt", "damage", targets=[victim])
            if has_condition(victim, Condition.DEAD):
                return result
            return None

        result = scan_seeds(probe)
        destroyed = next(event for event in result.events if event.event_type == "equipment_destroyed")
        assert "Sword" in destroyed.item_names


class TestSleep:
    def test_no_save_and_undead_consume_nothing(self):
        harness = Harness()
        magic_user = build_caster("magic_user", 1, spell_book=("sleep",), memorized=("sleep",))
        goblins = [harness.monster("goblin") for _ in range(4)]
        wight = harness.monster("wight")
        harness.add(magic_user)
        result = harness.cast(magic_user, "sleep", "hd_budget", targets=[wight, *goblins])
        assert not any(event.code.startswith("combat.save") for event in result.events)
        assert not has_condition(wight, Condition.ASLEEP)
        assert wight.id not in result.affected_ids
        # 2d8 always covers four 1-HD goblins; the wight consumed no budget.
        assert all(goblin.id in result.affected_ids for goblin in goblins)

    def test_mode_one_takes_exactly_the_4_plus_1_creature(self):
        harness = Harness()
        magic_user = build_caster("magic_user", 1, spell_book=("sleep",), memorized=("sleep",))
        ogre = harness.monster("ogre")  # 4+1 HD
        harness.add(magic_user)
        result = harness.cast(magic_user, "sleep", "single_4_plus", targets=[ogre])
        assert result.affected_ids == (ogre.id,)
        assert has_condition(ogre, Condition.ASLEEP)

    def test_mode_one_rejects_others_as_no_effect(self):
        harness = Harness()
        magic_user = build_caster("magic_user", 1, spell_book=("sleep",), memorized=("sleep",))
        gnoll = harness.monster("gnoll")  # 2 HD: mode 2's territory
        harness.add(magic_user)
        result = harness.cast(magic_user, "sleep", "single_4_plus", targets=[gnoll])
        assert result.no_effect is True

    def test_mode_two_excludes_4_plus_1_and_counts_bonuses_dropped(self):
        harness = Harness()
        magic_user = build_caster("magic_user", 1, spell_book=("sleep",), memorized=("sleep",))
        ogre = harness.monster("ogre")  # 4+1: mode 1's target, excluded here
        lion = harness.monster("mountain_lion")  # 3+2 counts as 3
        kobold = harness.monster("kobold")  # ½ HD counts as 1
        harness.add(magic_user)
        result = harness.cast(magic_user, "sleep", "hd_budget", targets=[ogre, lion, kobold])
        assert ogre.id not in result.affected_ids
        selection = next(event for event in result.events if event.code == "combat.targeting.selected")
        # Weakest-first: the kobold (1) selects before the lion (3).
        assert selection.target_ids[0] == kobold.id

    def test_asleep_duration_is_4d4_turns(self):
        harness = Harness()
        magic_user = build_caster("magic_user", 1, spell_book=("sleep",), memorized=("sleep",))
        goblin = harness.monster("goblin")
        harness.add(magic_user)
        harness.cast(magic_user, "sleep", "hd_budget", targets=[goblin])
        effect = harness.ledger.active_on(goblin.id, "sleep")[0]
        turns = effect.expires_round // ROUNDS_PER_TURN
        assert 4 <= turns <= 16

    def test_sleeping_target_dies_to_a_blade(self):
        harness = Harness()
        magic_user = build_caster("magic_user", 1, spell_book=("sleep",), memorized=("sleep",))
        goblin = harness.monster("goblin")
        harness.add(magic_user)
        harness.cast(magic_user, "sleep", "hd_budget", targets=[goblin])
        fighter = harness.add(build_caster("fighter", 1, name="axeman"))
        result = resolve_attack(
            fighter,
            goblin,
            load_equipment().get("sword"),
            context=AttackContext(),
            ruleset=harness.ruleset,
            stream=harness.streams.get("combat"),
        )
        assert result.attack_roll.auto  # helpless auto-hit
        assert has_condition(goblin, Condition.DEAD)


class TestHoldAndCharm:
    def test_hold_person_save_modifiers(self):
        spell = load_spells().get("hold_person_c")
        assert spell.mode("individual").save.modifier == -2
        assert spell.mode("group").save.modifier == 0

    def test_hold_person_gates(self):
        def probe(seed):
            harness = Harness(seed)
            cleric = build_caster("cleric", 6, memorized=("hold_person_c",))
            goblin = harness.monster("goblin")
            harness.add(cleric)
            harness.cast(cleric, "hold_person_c", "individual", targets=[goblin])
            if has_condition(goblin, Condition.PARALYSED):
                return harness, goblin
            return None

        harness, goblin = scan_seeds(probe)
        # Paralysis lasts the cleric spell's 9 turns.
        effect = harness.ledger.active_on(goblin.id, "hold_person_c")[0]
        assert effect.expires_round == 9 * ROUNDS_PER_TURN

    def test_hold_person_no_effect_on_non_persons_and_undead(self):
        harness = Harness()
        cleric = build_caster("cleric", 6, memorized=("hold_person_c", "hold_person_c"))
        troll = harness.monster("troll")
        wight = harness.monster("wight")
        harness.add(cleric)
        assert harness.cast(cleric, "hold_person_c", "individual", targets=[troll]).no_effect
        assert harness.cast(cleric, "hold_person_c", "individual", targets=[wight]).no_effect

    def test_hold_monster_affects_non_persons_but_never_undead(self):
        def probe(seed):
            harness = Harness(seed)
            magic_user = build_caster("magic_user", 11, spell_book=("hold_monster",), memorized=("hold_monster",))
            troll = harness.monster("troll")
            harness.add(magic_user)
            harness.cast(magic_user, "hold_monster", "individual", targets=[troll])
            if has_condition(troll, Condition.PARALYSED):
                return True
            return None

        assert scan_seeds(probe)

    def test_hold_person_group_takes_1d4(self):
        harness = Harness()
        cleric = build_caster("cleric", 6, memorized=("hold_person_c",))
        goblins = [harness.monster("goblin") for _ in range(6)]
        harness.add(cleric)
        result = harness.cast(cleric, "hold_person_c", "group", targets=goblins)
        selection = next(event for event in result.events if event.code == "combat.targeting.selected")
        assert 1 <= len(selection.target_ids) <= 4

    def test_hold_person_mu_duration_is_per_level(self):
        def probe(seed):
            harness = Harness(seed)
            magic_user = build_caster("magic_user", 5, spell_book=("hold_person_mu",), memorized=("hold_person_mu",))
            goblin = harness.monster("goblin")
            harness.add(magic_user)
            harness.cast(magic_user, "hold_person_mu", "individual", targets=[goblin])
            effects = harness.ledger.active_on(goblin.id, "hold_person_mu")
            if effects:
                return effects[0]
            return None

        effect = scan_seeds(probe)
        assert effect.expires_round == 5 * ROUNDS_PER_TURN  # 1 turn per level at level 5

    @pytest.mark.parametrize(("int_score", "days"), [(6, 30), (10, 7), (16, 1)])
    def test_charm_resave_interval_by_int(self, int_score, days):
        def probe(seed):
            harness = Harness(seed)
            magic_user = build_caster("magic_user", 3, spell_book=("charm_person",), memorized=("charm_person",))
            victim = build_caster("fighter", 1, name="victim", scores={AbilityScore.INT: int_score})
            harness.add(magic_user, victim)
            harness.cast(magic_user, "charm_person", "charm", targets=[victim])
            effects = harness.ledger.active_on(victim.id, "charm_person")
            if effects:
                return effects[0]
            return None

        effect = scan_seeds(probe)
        assert effect.definition.tick == "charm_resave"
        assert effect.definition.tick_interval_rounds == days * ROUNDS_PER_DAY
        assert effect.expires_round is None  # indefinite until a re-save passes

    def test_charm_monster_default_band_is_weekly(self):
        def probe(seed):
            harness = Harness(seed)
            magic_user = build_caster("magic_user", 9, spell_book=("charm_monster",), memorized=("charm_monster",))
            troll = harness.monster("troll")
            harness.add(magic_user)
            harness.cast(magic_user, "charm_monster", "individual", targets=[troll])
            effects = harness.ledger.active_on(troll.id, "charm_monster")
            if effects:
                return effects[0]
            return None

        effect = scan_seeds(probe)
        assert effect.definition.tick_interval_rounds == 7 * ROUNDS_PER_DAY

    def test_charm_resave_releases_on_a_passed_save(self):
        def probe(seed):
            harness = Harness(seed)
            magic_user = build_caster("magic_user", 3, spell_book=("charm_person",), memorized=("charm_person",))
            victim = build_caster("fighter", 3, name="victim", scores={AbilityScore.INT: 16})
            harness.add(magic_user, victim)
            harness.cast(magic_user, "charm_person", "charm", targets=[victim])
            if not has_condition(victim, Condition.CHARMED):
                return None
            for _ in range(3):
                harness.ledger.advance(harness.clock, 1, TimeUnit.DAY, harness.registry, stream=harness.effects)
                if not has_condition(victim, Condition.CHARMED):
                    return True
            return None

        assert scan_seeds(probe)

    def test_charm_monster_single_mode_gate_is_more_than_3_hd(self):
        harness = Harness()
        magic_user = build_caster("magic_user", 9, spell_book=("charm_monster",), memorized=("charm_monster",))
        gnoll = harness.monster("gnoll")  # 2 HD: group mode's territory
        harness.add(magic_user)
        result = harness.cast(magic_user, "charm_monster", "individual", targets=[gnoll])
        assert result.no_effect is True


class TestCuresAndRestoration:
    def test_cure_light_wounds_heals_1d6_plus_1_capped(self):
        harness = Harness()
        cleric = build_caster("cleric", 2, memorized=("cure_light_wounds",))
        cleric.current_hp = cleric.max_hp - 1
        harness.add(cleric)
        result = harness.cast(cleric, "cure_light_wounds", "heal", targets=[cleric])
        healed = next(event for event in result.events if event.code == "combat.healing.applied")
        assert healed.amount == 1  # capped at max
        assert cleric.current_hp == cleric.max_hp

    def test_cure_paralysis_mode_releases_paralysis(self):
        harness = Harness()
        cleric = build_caster("cleric", 2, memorized=("cure_light_wounds",))
        victim = build_caster("fighter", 1, name="victim")
        harness.add(cleric, victim)
        definition = EffectDefinition(kind="ghoul_paralysis", condition=Condition.PARALYSED, permanent=True)
        harness.ledger.attach(
            definition, victim.id, clock=harness.clock, allocator=harness.allocator, registry=harness.registry
        )
        assert has_condition(victim, Condition.PARALYSED)
        result = harness.cast(cleric, "cure_light_wounds", "cure_paralysis", targets=[victim])
        assert not has_condition(victim, Condition.PARALYSED)
        assert victim.id in result.affected_ids

    def test_cause_wounds_touch_attack_consumes_hit_or_miss(self):
        hit_seen = miss_seen = False
        for seed in range(MASTER_SEED, MASTER_SEED + 40):
            harness = Harness(seed)
            cleric = build_caster("cleric", 6, memorized=("cure_serious_wounds",))
            troll = harness.monster("troll")
            harness.add(cleric)
            result = harness.cast(
                cleric,
                "cure_serious_wounds",
                "harm",
                reversed=True,
                targets=[troll],
                context=CastContext(in_combat=True),
            )
            assert cleric.memorized_spells == ()  # consumed either way
            attack = next(event for event in result.events if event.event_type == "attack_rolled")
            assert attack.attack_name == "cure_serious_wounds"
            if attack.code == "combat.attack.hit":
                hit_seen = True
                assert any(event.code == "combat.damage.dealt" for event in result.events)
            else:
                miss_seen = True
                assert result.no_effect
            if hit_seen and miss_seen:
                return
        pytest.fail("touch attacks never produced both a hit and a miss")

    def test_touch_lands_without_a_roll_out_of_combat(self):
        harness = Harness()
        cleric = build_caster("cleric", 6, memorized=("cure_serious_wounds",))
        troll = harness.monster("troll")
        harness.add(cleric)
        result = harness.cast(cleric, "cure_serious_wounds", "harm", reversed=True, targets=[troll])
        assert not any(event.event_type == "attack_rolled" for event in result.events)
        assert any(event.code == "combat.damage.dealt" for event in result.events)

    def test_cure_disease_unblocks_mummy_rot_healing(self):
        harness = Harness()
        cleric = build_caster("cleric", 6, memorized=("cure_disease", "cure_light_wounds"))
        victim = build_caster("fighter", 3, name="victim")
        victim.current_hp = 5
        harness.add(cleric, victim)
        rot = EffectDefinition(kind="mummy_rot", condition=Condition.DISEASED, permanent=True)
        harness.ledger.attach(
            rot, victim.id, clock=harness.clock, allocator=harness.allocator, registry=harness.registry
        )
        blocked = harness.cast(cleric, "cure_light_wounds", "heal", targets=[victim])
        assert blocked.events[1].code == "combat.healing.blocked"
        harness.cast(cleric, "cure_disease", "cure", targets=[victim])
        assert not has_condition(victim, Condition.DISEASED)
        cleric.memorized_spells = (MemorizedSpell(spell_id="cure_light_wounds"),)
        healed = harness.cast(cleric, "cure_light_wounds", "heal", targets=[victim])
        assert any(event.code == "combat.healing.applied" for event in healed.events)

    def test_cause_disease_effect_shape(self):
        def probe(seed):
            harness = Harness(seed)
            cleric = build_caster("cleric", 6, memorized=("cure_disease",))
            victim = build_caster("fighter", 1, name="victim")
            harness.add(cleric, victim)
            harness.cast(cleric, "cure_disease", "afflict", reversed=True, targets=[victim])
            effects = harness.ledger.active_on(victim.id, "cause_disease")
            if effects:
                return harness, victim, effects[0]
            return None

        harness, victim, effect = scan_seeds(probe)
        assert effect.definition.expiry == "death"  # death in 2d12 days
        assert 2 * ROUNDS_PER_DAY <= effect.expires_round <= 24 * ROUNDS_PER_DAY
        assert modifier_total(victim, "attack_bonus") == -2
        assert has_condition(victim, Condition.DISEASED)
        assert effect.definition.params["healing_rest_days"] == 2

    def test_neutralize_poison_revival_window(self):
        from osrlib.core.effects import kill as kill_creature

        for rounds, revived in ((10, True), (11, False)):
            harness = Harness()
            cleric = build_caster("cleric", 8, memorized=("neutralize_poison",))
            victim = build_caster("fighter", 1, name="victim")
            harness.add(cleric, victim)
            kill_creature(victim)
            result = harness.cast(
                cleric,
                "neutralize_poison",
                "neutralize",
                targets=[victim],
                context=CastContext(rounds_since_death=rounds),
            )
            assert has_condition(victim, Condition.DEAD) is not revived
            if revived:
                assert victim.current_hp == 1
            else:
                assert result.no_effect

    def test_raise_dead_level_seven_allows_zero_days(self):
        from osrlib.core.effects import kill as kill_creature

        for days, raised in ((0, True), (1, False)):
            harness = Harness()
            cleric = build_caster("cleric", 7, memorized=("raise_dead",))
            victim = build_caster("fighter", 3, name="victim")
            harness.add(cleric, victim)
            kill_creature(victim)
            result = harness.cast(
                cleric, "raise_dead", "restore_life", targets=[victim], context=CastContext(days_since_death=days)
            )
            if not raised:
                assert result.no_effect
                continue
            assert not has_condition(victim, Condition.DEAD)
            assert victim.current_hp == 1
            assert has_condition(victim, Condition.WEAKENED)
            # The weakness runs 14 elapsed days and gates attacking and casting.
            effect = harness.ledger.active_on(victim.id, "raise_dead_weakness")[0]
            assert effect.expires_round == 14 * ROUNDS_PER_DAY
            assert validate_attack(victim, cleric, None, AttackContext(), ruleset=harness.ruleset)
            victim.memorized_spells = (MemorizedSpell(spell_id="cure_light_wounds"),)
            assert validate_cast(victim, load_spells().get("cure_light_wounds"), "heal", targets=[victim])
            # The weakness also bans other class abilities (turning) and pins the
            # subject at 1 hp: healing from any source is blocked while it runs.
            from osrlib.core.combat import apply_healing
            from osrlib.core.spells import validate_turn_undead

            blocked = apply_healing(victim, 5)
            assert blocked[0].code == "combat.healing.blocked" and victim.current_hp == 1
            weak_cleric = build_caster("cleric", 7, name="weak-cleric")
            weak_cleric.conditions = victim.conditions
            assert validate_turn_undead(weak_cleric, load_classes().get("cleric"))

    def test_neutralize_poison_never_revives_monsters(self):
        # The page's revival usage is titled "Characters" (pinned).
        from osrlib.core.effects import kill as kill_creature

        harness = Harness()
        cleric = build_caster("cleric", 8, memorized=("neutralize_poison",))
        goblin = harness.monster("goblin")
        harness.add(cleric)
        kill_creature(goblin)
        result = harness.cast(
            cleric, "neutralize_poison", "neutralize", targets=[goblin], context=CastContext(rounds_since_death=1)
        )
        assert result.no_effect
        assert has_condition(goblin, Condition.DEAD)

    def test_raise_dead_never_raises_monsters(self):
        harness = Harness()
        cleric = build_caster("cleric", 9, memorized=("raise_dead",))
        goblin = harness.monster("goblin")
        harness.add(cleric)
        from osrlib.core.effects import kill as kill_creature

        kill_creature(goblin)
        result = harness.cast(
            cleric, "raise_dead", "restore_life", targets=[goblin], context=CastContext(days_since_death=0)
        )
        assert result.no_effect

    def test_raise_dead_destroy_undead_usage(self):
        def probe(seed):
            harness = Harness(seed)
            cleric = build_caster("cleric", 9, memorized=("raise_dead",))
            wight = harness.monster("wight")
            harness.add(cleric)
            result = harness.cast(cleric, "raise_dead", "destroy_undead", targets=[wight])
            if has_condition(wight, Condition.DEAD):
                return result
            return None

        result = scan_seeds(probe)
        death = next(event for event in result.events if event.event_type == "death")
        assert death.code == "combat.death.permanent"

    def test_remove_fear_and_cause_fear(self):
        def probe(seed):
            harness = Harness(seed)
            cleric = build_caster("cleric", 4, memorized=("remove_fear", "remove_fear"))
            victim = build_caster("fighter", 1, name="victim")
            harness.add(cleric, victim)
            harness.cast(cleric, "remove_fear", "frighten", reversed=True, targets=[victim])
            if not has_condition(victim, Condition.AFRAID):
                return None
            effect = harness.ledger.active_on(victim.id, "remove_fear")[0]
            assert effect.expires_round == 2 * ROUNDS_PER_TURN  # cause fear lasts 2 turns
            result = harness.cast(cleric, "remove_fear", "remove", targets=[victim])
            save = next(event for event in result.events if event.event_type == "saving_throw_rolled")
            assert save.modifier >= 4  # +1 per caster level against magical fear
            return not has_condition(victim, Condition.AFRAID) or result.no_effect

        assert scan_seeds(probe)

    def test_stone_to_flesh_recovers_the_petrified(self):
        harness = Harness()
        magic_user = build_caster("magic_user", 11, spell_book=("stone_to_flesh",), memorized=("stone_to_flesh",))
        victim = build_caster("fighter", 1, name="victim")
        harness.add(magic_user, victim)
        petrification = EffectDefinition(kind="petrification", permanent=True, condition=Condition.PETRIFIED)
        harness.ledger.attach(
            petrification, victim.id, clock=harness.clock, allocator=harness.allocator, registry=harness.registry
        )
        assert has_condition(victim, Condition.PETRIFIED)
        harness.cast(magic_user, "stone_to_flesh", "restore", targets=[victim])
        assert not has_condition(victim, Condition.PETRIFIED)

    def test_flesh_to_stone_saves_versus_paralysis_and_is_permanent(self):
        spell = load_spells().get("stone_to_flesh")
        petrify = spell.mode("petrify", reversed=True)
        assert petrify.save.category == "paralysis"

        def probe(seed):
            harness = Harness(seed)
            magic_user = build_caster("magic_user", 11, spell_book=("stone_to_flesh",))
            # Arcane casters fix the reversed form at memorization.
            magic_user.memorized_spells = (MemorizedSpell(spell_id="stone_to_flesh", reversed=True),)
            victim = build_caster("fighter", 1, name="victim")
            harness.add(magic_user, victim)
            harness.cast(magic_user, "stone_to_flesh", "petrify", reversed=True, targets=[victim])
            effects = harness.ledger.active_on(victim.id)
            if effects:
                return effects[0]
            return None

        effect = scan_seeds(probe)
        assert effect.definition.permanent is True
        assert effect.expires_round is None


class TestSaveOrDie:
    def test_death_spell_exclusions(self):
        harness = Harness()
        magic_user = build_caster("magic_user", 13, spell_book=("death_spell",), memorized=("death_spell",))
        wight = harness.monster("wight")
        dragon = harness.monster("red_dragon")  # over 7 HD
        goblins = [harness.monster("goblin") for _ in range(3)]
        harness.add(magic_user)
        result = harness.cast(magic_user, "death_spell", "kill", targets=[wight, dragon, *goblins])
        assert wight.id not in result.affected_ids
        assert dragon.id not in result.affected_ids
        assert not has_condition(wight, Condition.DEAD)
        assert not has_condition(dragon, Condition.DEAD)
        for event in result.events:
            if event.event_type == "saving_throw_rolled":
                assert event.category == "death"

    def test_finger_of_death(self):
        def probe(seed):
            harness = Harness(seed)
            cleric = build_caster("cleric", 9, memorized=("raise_dead",))
            goblin = harness.monster("goblin")
            harness.add(cleric)
            result = harness.cast(cleric, "raise_dead", "kill", reversed=True, targets=[goblin])
            save = next(event for event in result.events if event.event_type == "saving_throw_rolled")
            assert save.category == "death"
            if has_condition(goblin, Condition.DEAD):
                return result
            return None

        result = scan_seeds(probe)
        death = next(event for event in result.events if event.event_type == "death")
        assert death.code == "combat.death.died"  # not permanent: raise dead can still undo it

    def test_disintegrate_kills_permanently_and_destroys_equipment(self):
        def probe(seed):
            harness = Harness(seed)
            magic_user = build_caster("magic_user", 13, spell_book=("disintegrate",), memorized=("disintegrate",))
            victim = build_caster("fighter", 1, name="victim")
            victim.inventory = Inventory()
            victim.inventory.purse.gp = 100
            purchase(victim.inventory, load_equipment().get("sword"), 1)
            harness.add(magic_user, victim)
            result = harness.cast(magic_user, "disintegrate", "kill", targets=[victim])
            if has_condition(victim, Condition.DEAD):
                return result
            return None

        result = scan_seeds(probe)
        death = next(event for event in result.events if event.event_type == "death")
        assert death.code == "combat.death.permanent"
        assert any(event.event_type == "equipment_destroyed" for event in result.events)


class TestBuffsAndWards:
    def test_bless_grants_and_blight_offsets(self):
        harness = Harness()
        cleric = build_caster("cleric", 4, memorized=("bless", "bless"))
        ally = build_caster("fighter", 1, name="ally")
        harness.add(cleric, ally)
        harness.cast(cleric, "bless", "battle", targets=[ally])
        assert modifier_total(ally, "attack_bonus") == 1
        assert modifier_total(ally, "damage_bonus") == 1
        assert morale_modifier(ally) == 1
        # A second bless does not stack (the cumulative rule).
        harness.cast(cleric, "bless", "battle", targets=[ally])
        assert modifier_total(ally, "attack_bonus") == 1

    def test_bless_plus_blight_offset(self):
        def probe(seed):
            harness = Harness(seed)
            cleric = build_caster("cleric", 4, memorized=("bless", "bless"))
            ally = build_caster("fighter", 1, name="ally")
            harness.add(cleric, ally)
            harness.cast(cleric, "bless", "battle", targets=[ally])
            harness.cast(cleric, "bless", "battle", reversed=True, targets=[ally])
            values = [m.value for m in ally.stat_modifiers if m.kind == "attack_bonus"]
            if -1 in values:  # the blight save failed and the penalty landed
                return ally
            return None

        ally = scan_seeds(probe)
        assert modifier_total(ally, "attack_bonus") == 0  # largest bonus + largest penalty

    def test_morale_modifier_rides_check_morale_inside_the_clamp(self):
        harness = Harness()
        cleric = build_caster("cleric", 4, memorized=("bless",))
        ally = build_caster("fighter", 1, name="ally")
        harness.add(cleric, ally)
        harness.cast(cleric, "bless", "battle", targets=[ally])
        result = check_morale("party", 7, modifier=morale_modifier(ally) + 2, stream=harness.streams.get("combat"))
        assert result.modifier == 2  # clamped to the RAW +-2

    def test_shield_ac_is_better_of(self):
        harness = Harness()
        magic_user = build_caster("magic_user", 1, spell_book=("shield",), memorized=("shield",))
        harness.add(magic_user)
        harness.cast(magic_user, "shield", "shield")
        goblin = harness.monster("goblin")
        # Unarmoured AC 9: shield sets 4 vs melee, 2 vs missiles.
        melee = attack_roll(
            goblin,
            magic_user,
            None,
            context=AttackContext(),
            ruleset=harness.ruleset,
            stream=harness.streams.get("combat"),
        )
        assert melee.events[0].defender_ac == 4
        sling = load_equipment().get("sling")
        missile = attack_roll(
            goblin,
            magic_user,
            sling,
            context=AttackContext(distance_feet=50),
            ruleset=harness.ruleset,
            stream=harness.streams.get("combat"),
        )
        assert missile.events[0].defender_ac == 2

    def test_shield_never_worsens_good_armour(self):
        harness = Harness()
        magic_user = build_caster("magic_user", 1, spell_book=("shield",), memorized=("shield",))
        harness.add(magic_user)
        harness.cast(magic_user, "shield", "shield")
        # Fake plate: descending AC 3 beats the shield's 4 in melee.
        fighter = build_caster("fighter", 1, name="tank")
        fighter.inventory = Inventory()
        fighter.inventory.purse.gp = 100
        purchase(fighter.inventory, load_equipment().get("plate_mail"), 1)
        from osrlib.core.items import equip

        equip(fighter.inventory, load_classes().get("fighter"), fighter.inventory.items[0])
        fighter.stat_modifiers = magic_user.stat_modifiers
        goblin = harness.monster("goblin")
        melee = attack_roll(
            goblin,
            fighter,
            None,
            context=AttackContext(),
            ruleset=harness.ruleset,
            stream=harness.streams.get("combat"),
        )
        assert melee.events[0].defender_ac == 3

    def test_resist_fire_save_bonus_and_per_die_reduction(self):
        harness = Harness()
        cleric = build_caster("cleric", 4, memorized=("resist_fire",))
        ally = build_caster("fighter", 1, name="ally")
        harness.add(cleric, ally)
        harness.cast(cleric, "resist_fire", "resist", targets=[ally])
        save = saving_throw(
            ally, SaveCategory.SPELLS, magical=True, element="fire", stream=harness.streams.get("combat")
        )
        assert save.modifier == 2
        cold_save = saving_throw(
            ally, SaveCategory.SPELLS, magical=True, element="cold", stream=harness.streams.get("combat")
        )
        assert cold_save.modifier == 0  # element-scoped
        # Per-die reduction floors at 1 per die.
        events = deal_damage(ally, 9, source=DamageSource(element="fire", magical=True), rolls=(1, 2, 6))
        assert events[0].amount == 6
        ally.current_hp = ally.max_hp
        events = deal_damage(ally, 2, source=DamageSource(element="fire", magical=True), rolls=(1, 1))
        assert events[0].amount == 2  # each die already at its minimum

    def test_protection_from_normal_missiles_boundary(self):
        harness = Harness()
        magic_user = build_caster(
            "magic_user",
            5,
            spell_book=("protection_from_normal_missiles",),
            memorized=("protection_from_normal_missiles",),
        )
        harness.add(magic_user)
        harness.cast(magic_user, "protection_from_normal_missiles", "ward", targets=[magic_user])
        ruleset = harness.ruleset
        # An arrow (a character weapon missile) is blocked.
        arrow = DamageSource(kind="weapon", missile=True)
        assert check_immunity(magic_user, arrow, ruleset=ruleset)
        # A thrown splash flask is blocked.
        splash = DamageSource(kind="splash", missile=True)
        assert check_immunity(magic_user, splash, ruleset=ruleset)
        # A hurled boulder (a monster attack, never auto-marked) is not.
        boulder = DamageSource(kind="monster", missile=False)
        assert not check_immunity(magic_user, boulder, ruleset=ruleset)
        # An enchanted missile is not.
        magic_arrow = DamageSource(kind="weapon", missile=True, magical=True, keys=("magic",))
        assert not check_immunity(magic_user, magic_arrow, ruleset=ruleset)

    def test_monster_missile_opt_in(self):
        harness = Harness()
        magic_user = build_caster(
            "magic_user",
            5,
            spell_book=("protection_from_normal_missiles",),
            memorized=("protection_from_normal_missiles",),
        )
        harness.add(magic_user)
        harness.cast(magic_user, "protection_from_normal_missiles", "ward", targets=[magic_user])
        hobgoblin = harness.monster("hobgoblin")
        attack = hobgoblin.template.attacks[0].attacks[0]
        result = resolve_attack(
            hobgoblin,
            magic_user,
            attack,
            context=AttackContext(monster_missile=True),
            ruleset=harness.ruleset,
            stream=harness.streams.get("combat"),
        )
        if result.attack_roll.hit:
            assert result.absorbed

    def test_protection_from_evil_gates_on_alignment(self):
        harness = Harness()
        cleric = build_caster("cleric", 4, memorized=("protection_from_evil_c",), alignment=Alignment.LAWFUL)
        harness.add(cleric)
        harness.cast(cleric, "protection_from_evil_c", "ward")
        goblin = harness.monster("goblin")  # chaotic: sole option, resolved at spawn
        assert goblin.alignment is Alignment.CHAOTIC
        combat = harness.streams.get("combat")
        attack = attack_roll(goblin, cleric, None, context=AttackContext(), ruleset=harness.ruleset, stream=combat)
        assert attack.modifier == -1  # the ward's penalty on differing attackers
        save = saving_throw(cleric, SaveCategory.SPELLS, source=goblin, stream=combat)
        assert save.modifier == 1
        # A same-alignment attacker is unaffected by the ward.
        ally = build_caster("fighter", 1, name="ally", alignment=Alignment.LAWFUL)
        attack = attack_roll(ally, cleric, None, context=AttackContext(), ruleset=harness.ruleset, stream=combat)
        assert attack.modifier == 0

    def test_unresolved_alignment_counts_as_differing(self):
        harness = Harness()
        cleric = build_caster("cleric", 4, memorized=("protection_from_evil_c",), alignment=Alignment.LAWFUL)
        harness.add(cleric)
        harness.cast(cleric, "protection_from_evil_c", "ward")
        monsters = load_monsters()
        multi = next(
            template
            for template in monsters.monsters
            if len(template.alignment.options) > 1 and template.alignment.usual is None
        )
        unresolved = spawn_monster(multi, id="monster-x", stream=harness.streams.get("monster_spawn"))
        assert unresolved.alignment is None
        attack = attack_roll(
            unresolved,
            cleric,
            None,
            context=AttackContext(),
            ruleset=harness.ruleset,
            stream=harness.streams.get("combat"),
        )
        assert attack.modifier == -1  # the ward errs protective

    def test_alignment_resolution_at_spawn(self):
        harness = Harness()
        monsters = load_monsters()
        usual = next(template for template in monsters.monsters if template.alignment.usual is not None)
        instance = spawn_monster(usual, id="monster-u", stream=harness.streams.get("monster_spawn"))
        assert instance.alignment is usual.alignment.usual
        chosen = spawn_monster(
            usual,
            id="monster-c",
            stream=harness.streams.get("monster_spawn"),
            alignment=usual.alignment.options[0],
        )
        assert chosen.alignment is usual.alignment.options[0]
        with pytest.raises(ValueError):
            goblin = monsters.get("goblin")
            spawn_monster(
                goblin, id="monster-b", stream=harness.streams.get("monster_spawn"), alignment=Alignment.LAWFUL
            )

    def test_protection_radius_covers_the_caster_plus_allies(self):
        harness = Harness()
        cleric = build_caster("cleric", 8, memorized=("protection_from_evil_10_radius_c",))
        ally = build_caster("fighter", 1, name="ally")
        harness.add(cleric, ally)
        result = harness.cast(cleric, "protection_from_evil_10_radius_c", "ward", targets=[ally])
        assert set(result.affected_ids) == {cleric.id, ally.id}
        for warded in (cleric, ally):
            assert any(m.kind == "attack_penalty_of_attackers" for m in warded.stat_modifiers)

    def test_protection_melee_ban_ships_as_data(self):
        ward = load_spells().get("protection_from_evil_c").mode("ward")
        assert ward.effect.params["bars_melee_from"] == ("enchanted", "constructed", "summoned")

    def test_striking_applies_to_weapon_attacks_never_unarmed(self):
        harness = Harness()
        cleric = build_caster("cleric", 6, memorized=("striking",))
        harness.add(cleric)
        harness.cast(cleric, "striking", "enchant", targets=[cleric])
        combat = harness.streams.get("combat")
        mace = load_equipment().get("mace")
        armed = damage_roll(cleric, mace, context=AttackContext(), ruleset=harness.ruleset, stream=combat)
        assert len(armed.rolls) == 2  # the weapon die plus striking's 1d6
        unarmed = damage_roll(cleric, None, context=AttackContext(), ruleset=harness.ruleset, stream=combat)
        assert len(unarmed.rolls) == 1  # unarmed gains nothing

    def test_striking_counts_as_magical(self):
        harness = Harness()
        cleric = build_caster("cleric", 6, memorized=("striking",))
        harness.add(cleric)
        harness.cast(cleric, "striking", "enchant", targets=[cleric])
        wight = harness.monster("wight")
        mace = load_equipment().get("mace")
        result = resolve_attack(
            cleric,
            wight,
            mace,
            context=AttackContext(),
            ruleset=harness.ruleset,
            stream=harness.streams.get("combat"),
        )
        assert not result.absorbed  # the silver-or-magic gate admits the enchanted mace


class TestLightAndDarkness:
    def test_blind_mode_attaches_for_the_duration(self):
        def probe(seed):
            harness = Harness(seed)
            cleric = build_caster("cleric", 2, memorized=("light_c",))
            goblin = harness.monster("goblin")
            harness.add(cleric)
            harness.cast(cleric, "light_c", "blind", targets=[goblin])
            if has_condition(goblin, Condition.BLIND):
                return harness, goblin
            return None

        harness, goblin = scan_seeds(probe)
        effect = harness.ledger.active_on(goblin.id, "light_c")[0]
        assert effect.expires_round == 12 * ROUNDS_PER_TURN
        # A blind creature cannot attack (the Phase 2 hook).
        assert validate_attack(goblin, goblin, None, AttackContext(), ruleset=harness.ruleset)

    def test_continual_blind_is_permanent(self):
        def probe(seed):
            harness = Harness(seed)
            cleric = build_caster("cleric", 6, memorized=("continual_light_c",))
            goblin = harness.monster("goblin")
            harness.add(cleric)
            harness.cast(cleric, "continual_light_c", "blind", targets=[goblin])
            effects = harness.ledger.active_on(goblin.id, "continual_light_c")
            if effects:
                return effects[0]
            return None

        effect = scan_seeds(probe)
        assert effect.definition.permanent is True

    def test_illuminate_attaches_structured_data_to_a_location(self):
        harness = Harness()
        cleric = build_caster("cleric", 2, memorized=("light_c",))
        harness.add(cleric)
        result = harness.cast(cleric, "light_c", "illuminate", targets=["room-12"])
        assert "room-12" in result.affected_ids
        effect = harness.ledger.active_on("room-12", "light")[0]
        assert effect.definition.params["radius_feet"] == 15

    def test_cancel_mode_is_a_targeted_dispel(self):
        harness = Harness()
        cleric = build_caster("cleric", 2, memorized=("light_c", "light_c"))
        harness.add(cleric)
        harness.cast(cleric, "light_c", "darken", reversed=True, targets=["corridor"])
        assert harness.ledger.active_on("corridor", "darkness")
        result = harness.cast(cleric, "light_c", "cancel", targets=["corridor"])
        assert not harness.ledger.active_on("corridor", "darkness")
        assert "corridor" in result.affected_ids


class TestSilenceWebDispelFeeblemind:
    def test_silence_failed_save_moves_with_the_creature(self):
        def probe(seed):
            harness = Harness(seed)
            cleric = build_caster("cleric", 4, memorized=("silence_15_radius",))
            victim = build_caster("magic_user", 2, name="victim", spell_book=("sleep",))
            victim.memorized_spells = (MemorizedSpell(spell_id="sleep"),)
            harness.add(cleric, victim)
            harness.cast(cleric, "silence_15_radius", "creature", targets=[victim])
            if has_condition(victim, Condition.SILENCED):
                return harness, victim
            return None

        harness, victim = scan_seeds(probe)
        # A silenced caster cannot cast.
        rejections = validate_cast(victim, load_spells().get("sleep"), "hd_budget", targets=[victim])
        assert rejections[0].code == "magic.cast.caster_incapacitated"

    def test_silence_passed_save_attaches_nothing(self):
        def probe(seed):
            harness = Harness(seed)
            cleric = build_caster("cleric", 4, memorized=("silence_15_radius",))
            victim = build_caster("fighter", 9, name="victim")  # good saves
            harness.add(cleric, victim)
            result = harness.cast(cleric, "silence_15_radius", "creature", targets=[victim])
            if result.no_effect:
                return harness, victim
            return None

        harness, victim = scan_seeds(probe)
        assert not harness.ledger.active_on(victim.id)  # the stationary form is a registered Phase 4 gap

    def test_web_escape_tiers(self):
        harness = Harness()
        magic_user = build_caster("magic_user", 3, spell_book=("web",), memorized=("web", "web", "web"))
        normal = build_caster("fighter", 1, name="normal")
        augmented = build_caster("fighter", 1, name="augmented")
        giant = build_caster("fighter", 1, name="giant")
        harness.add(magic_user, normal, augmented, giant)
        context = CastContext(strength_tiers={augmented.id: "augmented", giant.id: "giant"})
        for _ in range(1):
            harness.cast(magic_user, "web", "entangle", targets=[normal, augmented, giant], context=context)
        for target in (normal, augmented, giant):
            assert has_condition(target, Condition.ENTANGLED)
        normal_effect = harness.ledger.active_on(normal.id, "web")[0]
        assert 2 * ROUNDS_PER_TURN <= normal_effect.expires_round <= 8 * ROUNDS_PER_TURN  # 2d4 turns
        assert harness.ledger.active_on(augmented.id, "web")[0].expires_round == 4
        assert harness.ledger.active_on(giant.id, "web")[0].expires_round == 2
        from osrlib.core.combat import cannot_move

        assert cannot_move(normal)

    def test_dispel_magic_releases_and_higher_levels_may_survive(self):
        released_seen = survived_seen = False
        for seed in range(MASTER_SEED, MASTER_SEED + 80):
            harness = Harness(seed)
            magic_user = build_caster("magic_user", 6, spell_book=("dispel_magic",), memorized=("dispel_magic",))
            victim = build_caster("fighter", 1, name="victim")
            harness.add(magic_user, victim)
            high = EffectDefinition(kind="high_buff", dispellable=True, permanent=True)
            harness.ledger.attach(
                high,
                victim.id,
                clock=harness.clock,
                allocator=harness.allocator,
                registry=harness.registry,
                caster_level=16,
            )
            rot = EffectDefinition(kind="mummy_rot", condition=Condition.DISEASED, permanent=True)
            harness.ledger.attach(
                rot, victim.id, clock=harness.clock, allocator=harness.allocator, registry=harness.registry
            )
            result = harness.cast(magic_user, "dispel_magic", "dispel", targets=[victim])
            dispelled = next(event for event in result.events if event.event_type == "magic_dispelled")
            assert has_condition(victim, Condition.DISEASED)  # monster effects are never dispellable
            if dispelled.released_effect_ids:
                released_seen = True
            if dispelled.surviving_effect_ids:
                survived_seen = True
            if released_seen and survived_seen:
                return
        pytest.fail("dispel never produced both outcomes at 50% survival")

    def test_dispel_releases_lower_level_effects_automatically(self):
        harness = Harness()
        magic_user = build_caster("magic_user", 6, spell_book=("dispel_magic",), memorized=("dispel_magic",))
        victim = build_caster("fighter", 1, name="victim")
        harness.add(magic_user, victim)
        low = EffectDefinition(kind="low_buff", dispellable=True, permanent=True)
        harness.ledger.attach(
            low,
            victim.id,
            clock=harness.clock,
            allocator=harness.allocator,
            registry=harness.registry,
            caster_level=3,
        )
        result = harness.cast(magic_user, "dispel_magic", "dispel", targets=[victim])
        dispelled = next(event for event in result.events if event.event_type == "magic_dispelled")
        assert len(dispelled.released_effect_ids) == 1
        assert not dispelled.surviving_effect_ids

    def test_feeblemind_targets_only_arcane_casters(self):
        harness = Harness()
        magic_user = build_caster("magic_user", 11, spell_book=("feeblemind",), memorized=("feeblemind", "feeblemind"))
        cleric = build_caster("cleric", 5, name="priest")
        harness.add(magic_user, cleric)
        result = harness.cast(magic_user, "feeblemind", "feeblemind", targets=[cleric])
        assert result.no_effect  # divine casters are not in the page's domain

        def probe(seed):
            probe_harness = Harness(seed)
            attacker = build_caster("magic_user", 11, spell_book=("feeblemind",), memorized=("feeblemind",))
            target = build_caster("elf", 3, name="elf-target", spell_book=("sleep",))
            probe_harness.add(attacker, target)
            probe_result = probe_harness.cast(attacker, "feeblemind", "feeblemind", targets=[target])
            save = next(event for event in probe_result.events if event.event_type == "saving_throw_rolled")
            assert save.modifier == -4
            if has_condition(target, Condition.FEEBLEMINDED):
                return target
            return None

        target = scan_seeds(probe)
        target.memorized_spells = (MemorizedSpell(spell_id="sleep"),)
        rejections = validate_cast(target, load_spells().get("sleep"), "hd_budget", targets=[target])
        assert rejections[0].code == "magic.cast.caster_incapacitated"


class TestAttachOnlyCensus:
    def test_haste_and_kin_carry_structured_params(self):
        catalog = load_spells()
        haste = catalog.get("haste").mode("haste")
        assert haste.effect.params["attacks_multiplier"] == 2
        assert haste.effect.params["movement_multiplier"] == 2
        confusion = catalog.get("confusion").mode("confuse")
        assert confusion.effect.params["behaviour_dice"] == "2d6"
        assert confusion.targeting.count_dice == "3d6"
        # "2+1 HD or greater" re-saves: count 3+, or count 2 with a positive
        # modifier — encoded so a 2+1 creature keeps its save under the
        # bonuses-dropped HD convention.
        assert confusion.effect.params["resave_hd_min_count"] == 3
        assert confusion.effect.params["resave_at_hd_count_2_with_bonus"] is True
        infravision = catalog.get("infravision").mode("grant")
        assert infravision.effect.params["range_feet"] == 60

    def test_mirror_image_pops_per_incoming_attack(self):
        harness = Harness()
        magic_user = build_caster("magic_user", 3, spell_book=("mirror_image",), memorized=("mirror_image",))
        harness.add(magic_user)
        harness.cast(magic_user, "mirror_image", "images")
        effect = harness.ledger.active_on(magic_user.id, "mirror_image")[0]
        images = effect.state["images"]
        assert 1 <= images <= 4
        for remaining in range(images - 1, -1, -1):
            events = pop_mirror_image(harness.ledger, magic_user.id, registry=harness.registry, clock=harness.clock)
            assert events
            if remaining == 0:
                assert any(event.code == "effects.effect.released" for event in events)
        assert not harness.ledger.active_on(magic_user.id, "mirror_image")
        assert pop_mirror_image(harness.ledger, magic_user.id, registry=harness.registry, clock=harness.clock) == []

    def test_spell_attached_effects_are_dispellable_with_caster_level(self):
        harness = Harness()
        cleric = build_caster("cleric", 4, memorized=("bless",))
        ally = build_caster("fighter", 1, name="ally")
        harness.add(cleric, ally)
        harness.cast(cleric, "bless", "battle", targets=[ally])
        effect = harness.ledger.active_on(ally.id, "bless")[0]
        assert effect.definition.dispellable is True
        assert effect.caster_level == 4
