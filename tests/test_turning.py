"""Tests for turning undead: column mapping, the procedure, and the pool arithmetic."""

import pytest

from osrlib.core.abilities import AbilityScore
from osrlib.core.alignment import Alignment
from osrlib.core.character import Character
from osrlib.core.clock import GameClock
from osrlib.core.effects import Condition, EffectsLedger, has_condition
from osrlib.core.monsters import IdAllocator, MonsterHitDice, spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.core.spells import MAGIC_STREAM, turn_undead, validate_turn_undead
from osrlib.core.tables import turning_column
from osrlib.data import load_classes, load_monsters

MASTER_SEED = 20_260_703

BASE_SCORES = {ability: 10 for ability in AbilityScore}


def build_cleric(level, name="Turner"):
    definition = load_classes().get("cleric")
    return Character(
        id=f"pc-{name.lower()}",
        name=name,
        class_id="cleric",
        race=definition.race,
        level=level,
        xp=definition.row(level).xp,
        scores=BASE_SCORES,
        alignment=Alignment.LAWFUL,
        max_hp=4 * level + 4,
        current_hp=4 * level + 4,
    )


class Harness:
    def __init__(self, seed=MASTER_SEED):
        self.streams = RngStreams(master_seed=seed)
        self.clock = GameClock()
        self.ledger = EffectsLedger()
        self.allocator = IdAllocator()
        self.registry = {}
        self.magic = self.streams.get(MAGIC_STREAM)

    def monster(self, monster_id):
        instance = spawn_monster(
            load_monsters().get(monster_id),
            id=self.allocator.allocate("monster"),
            stream=self.streams.get("monster_spawn"),
        )
        self.registry[instance.id] = instance
        return instance

    def turn(self, cleric, candidates):
        self.registry[cleric.id] = cleric
        return turn_undead(
            cleric,
            load_classes().get("cleric"),
            candidates,
            ledger=self.ledger,
            clock=self.clock,
            allocator=self.allocator,
            registry=self.registry,
            stream=self.magic,
        )


class TestColumnMapping:
    def test_count_maps_to_its_column(self):
        monsters = load_monsters()
        assert turning_column(monsters.get("skeleton").hit_dice) == "1"
        assert turning_column(monsters.get("zombie").hit_dice) == "2"
        assert turning_column(monsters.get("ghoul").hit_dice) == "2*"  # 2 HD with an asterisk
        assert turning_column(monsters.get("wight").hit_dice) == "3"  # 3* — the asterisk matters only at 2 HD
        assert turning_column(monsters.get("mummy").hit_dice) == "5"  # 5+1 — modifiers don't shift columns
        assert turning_column(monsters.get("spectre").hit_dice) == "6"
        assert turning_column(monsters.get("vampire_7").hit_dice) == "7-9"
        assert turning_column(monsters.get("vampire_9").hit_dice) == "7-9"

    def test_counts_above_nine_have_no_column(self):
        assert turning_column(MonsterHitDice(count=10)) is None
        assert turning_column(MonsterHitDice(count=15, asterisks=2)) is None

    def test_sub_one_counts_use_column_one(self):
        assert turning_column(MonsterHitDice(count=1, die=4)) == "1"

    def test_no_classic_undead_exceeds_count_nine(self):
        # The counts-above-9 rule is exercised by direct lookups because the census
        # tops out at the 9-HD vampire.
        undead = [m for m in load_monsters().monsters if "undead" in m.categories]
        assert undead and max(m.hit_dice.count for m in undead) == 9


class TestValidation:
    def test_non_clerics_cannot_turn(self):
        fighter_definition = load_classes().get("fighter")
        cleric = build_cleric(3)
        rejections = validate_turn_undead(cleric, fighter_definition)
        assert rejections[0].code == "magic.turning.not_a_turner"

    def test_incapacitated_cleric_cannot_turn(self):
        from osrlib.core.effects import ActiveCondition

        for condition in (Condition.PARALYSED, Condition.WEAKENED):
            cleric = build_cleric(3)
            cleric.conditions = (ActiveCondition(condition=condition, effect_id=None),)
            rejections = validate_turn_undead(cleric, load_classes().get("cleric"))
            assert rejections[0].code == "magic.turning.caster_incapacitated"
            assert rejections[0].params["condition"] == condition.value

    def test_holy_symbol_is_not_a_gate(self):
        # Pinned: Cleric.md's "must carry a holy symbol" is a class edict, not a
        # mechanical precondition — a gear-less cleric turns (and casts) unimpeded.
        harness = Harness()
        cleric = build_cleric(4)
        assert not cleric.inventory.items
        skeleton = harness.monster("skeleton")
        result = harness.turn(cleric, [skeleton])
        assert result.events[0].code in ("magic.turning.turned", "magic.turning.destroyed")

    def test_turn_undead_raises_on_invalid(self):
        harness = Harness()
        cleric = build_cleric(3)
        with pytest.raises(ValueError, match="not_a_turner"):
            turn_undead(
                cleric,
                load_classes().get("fighter"),
                [],
                ledger=harness.ledger,
                clock=harness.clock,
                allocator=harness.allocator,
                registry=harness.registry,
                stream=harness.magic,
            )


class TestProcedure:
    def test_low_level_cleric_fails_against_a_wraith(self):
        # Level 1 versus the wraith's column 4: the printed cell is em-dash.
        harness = Harness()
        cleric = build_cleric(1)
        wraith = harness.monster("wraith")
        result = harness.turn(cleric, [wraith])
        assert result.hd_pool is None
        assert result.events[0].code == "magic.turning.failed"
        assert result.outcomes[0].outcome == "fail"
        assert not has_condition(wraith, Condition.TURNED)

    def test_number_cells_compare_the_single_roll(self):
        # Level 1 versus skeletons needs a 7: assert both branches across seeds.
        success = failure = None
        for seed in range(MASTER_SEED, MASTER_SEED + 40):
            harness = Harness(seed)
            cleric = build_cleric(1)
            skeleton = harness.monster("skeleton")
            result = harness.turn(cleric, [skeleton])
            if result.roll >= 7:
                success = result
                assert result.affected_ids
            else:
                failure = result
                assert result.events[0].code == "magic.turning.failed"
            if success and failure:
                break
        assert success is not None and failure is not None
        assert success.outcomes[0].threshold == 7

    def test_automatic_turn_and_destroy_cells(self):
        harness = Harness()
        cleric = build_cleric(4)  # skeletons: D, zombies: T
        skeleton = harness.monster("skeleton")
        zombie = harness.monster("zombie")
        result = harness.turn(cleric, [skeleton, zombie])
        by_type = {outcome.template_id: outcome.outcome for outcome in result.outcomes}
        assert by_type == {"skeleton": "destroy", "zombie": "turn"}

    def test_mixed_group_lowest_hd_first_and_pool_arithmetic(self):
        def probe(seed):
            harness = Harness(seed)
            cleric = build_cleric(9)  # every classic undead type succeeds at 9
            skeletons = [harness.monster("skeleton") for _ in range(3)]  # cost 1 each
            zombies = [harness.monster("zombie") for _ in range(2)]  # cost 2 each
            wight = harness.monster("wight")  # cost 3
            result = harness.turn(cleric, [wight, *zombies, *skeletons])
            if result.hd_pool == 7:
                return result, skeletons, zombies, wight
            return None

        for seed in range(MASTER_SEED, MASTER_SEED + 200):
            outcome = probe(seed)
            if outcome is not None:
                break
        else:
            pytest.fail("no seed rolled a 7 HD pool")
        result, skeletons, zombies, wight = outcome
        # Pool 7 spends lowest-first: 3 skeletons (3) + 2 zombies (4) = 7; the wight
        # (cost 3) is unaffordable at 0 remaining.
        assert set(result.affected_ids) == {m.id for m in (*skeletons, *zombies)}
        assert wight.id not in result.affected_ids

    def test_pool_stops_at_first_unaffordable_monster(self):
        def probe(seed):
            harness = Harness(seed)
            cleric = build_cleric(9)
            skeleton = harness.monster("skeleton")  # cost 1
            wight = harness.monster("wight")  # cost 3
            spectre = harness.monster("spectre")  # cost 6
            result = harness.turn(cleric, [spectre, wight, skeleton])
            if result.hd_pool == 5:
                return result, skeleton, wight, spectre
            return None

        for seed in range(MASTER_SEED, MASTER_SEED + 200):
            outcome = probe(seed)
            if outcome is not None:
                break
        else:
            pytest.fail("no seed rolled a 5 HD pool")
        result, skeleton, wight, spectre = outcome
        # Lowest-first: skeleton (1), wight (3), then the spectre (6) exceeds the
        # remaining 1 — the excess is wasted, not reallocated.
        assert set(result.affected_ids) == {skeleton.id, wight.id}

    def test_minimum_one_effect_on_a_successful_turn(self):
        def probe(seed):
            harness = Harness(seed)
            cleric = build_cleric(9)
            spectres = [harness.monster("spectre") for _ in range(2)]  # cost 6 each
            result = harness.turn(cleric, spectres)
            if result.hd_pool is not None and result.hd_pool < 6:
                return result, spectres
            return None

        for seed in range(MASTER_SEED, MASTER_SEED + 200):
            outcome = probe(seed)
            if outcome is not None:
                break
        else:
            pytest.fail("no seed rolled a pool under 6")
        result, spectres = outcome
        assert result.affected_ids == (spectres[0].id,)  # the cheapest eligible monster, pinned

    def test_destroy_is_permanent_death(self):
        harness = Harness()
        cleric = build_cleric(8)
        skeletons = [harness.monster("skeleton") for _ in range(2)]
        result = harness.turn(cleric, skeletons)
        assert result.events[0].code == "magic.turning.destroyed"
        for monster_id in result.destroyed_ids:
            death = next(
                event for event in result.events if event.event_type == "death" and event.target_id == monster_id
            )
            assert death.code == "combat.death.permanent"

    def test_turned_monsters_carry_an_indefinite_condition(self):
        def probe(seed):
            harness = Harness(seed)
            cleric = build_cleric(2)  # skeletons: T at level 2
            skeleton = harness.monster("skeleton")
            result = harness.turn(cleric, [skeleton])
            if result.affected_ids:
                return harness, skeleton
            return None

        for seed in range(MASTER_SEED, MASTER_SEED + 40):
            outcome = probe(seed)
            if outcome is not None:
                break
        else:
            pytest.fail("turning never succeeded")
        harness, skeleton = outcome
        assert has_condition(skeleton, Condition.TURNED)
        effect = harness.ledger.active_on(skeleton.id, "turned")[0]
        assert effect.expires_round is None
        assert effect.definition.dispellable is False  # turning is not a spell
        # The encounter releases the effect when the fiction says the flight ends.
        harness.ledger.release(effect.effect_id, harness.registry)
        assert not has_condition(skeleton, Condition.TURNED)

    def test_non_undead_candidates_resolve_as_unaffected(self):
        harness = Harness()
        cleric = build_cleric(11)
        goblin = harness.monster("goblin")
        skeleton = harness.monster("skeleton")
        result = harness.turn(cleric, [goblin, skeleton])
        by_type = {outcome.template_id: outcome.outcome for outcome in result.outcomes}
        assert by_type["goblin"] == "unaffected"
        assert goblin.id not in result.affected_ids
        assert not has_condition(goblin, Condition.TURNED)

    def test_dead_undead_are_not_candidates(self):
        from osrlib.core.effects import kill

        harness = Harness()
        cleric = build_cleric(11)
        corpse = harness.monster("skeleton")
        kill(corpse)
        standing = harness.monster("skeleton")
        result = harness.turn(cleric, [corpse, standing])
        assert result.affected_ids == (standing.id,)

    def test_player_visibility(self):
        harness = Harness()
        cleric = build_cleric(4)
        skeleton = harness.monster("skeleton")
        result = harness.turn(cleric, [skeleton])
        assert result.events[0].visibility == "player"  # the player rolls turning dice in B/X
