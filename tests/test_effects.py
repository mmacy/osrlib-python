"""Tests for the effects engine: tick order, durations, regeneration, conditions."""

import pytest

from osrlib.core.clock import GameClock, TimeUnit
from osrlib.core.combat import apply_healing, natural_healing
from osrlib.core.effects import (
    Condition,
    EffectDefinition,
    EffectsLedger,
    grant_condition,
    has_condition,
    kill,
    regeneration_definition,
    remove_condition,
)
from osrlib.core.monsters import IdAllocator, spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.data import load_monsters

MASTER_SEED = 20_260_703


@pytest.fixture
def streams():
    return RngStreams(master_seed=MASTER_SEED)


@pytest.fixture
def allocator():
    return IdAllocator()


def make_troll(streams, allocator):
    template = load_monsters().get("troll")
    return spawn_monster(template, id=allocator.allocate("monster"), stream=streams.get("monster_spawn"))


class TestConditions:
    def test_grant_and_remove(self, streams, allocator):
        troll = make_troll(streams, allocator)
        events = grant_condition(troll, Condition.BLIND, "effect-0001")
        assert has_condition(troll, Condition.BLIND)
        assert events[0].code == "effects.condition.gained"
        assert not remove_condition(troll, Condition.BLIND, "other-effect")  # wrong owner: no-op
        assert has_condition(troll, Condition.BLIND)
        events = remove_condition(troll, Condition.BLIND, "effect-0001")
        assert not has_condition(troll, Condition.BLIND)
        assert events[0].code == "effects.condition.removed"

    def test_condition_immunity_blocks_grant(self, streams, allocator):
        wight = spawn_monster(
            load_monsters().get("wight"), id=allocator.allocate("monster"), stream=streams.get("monster_spawn")
        )
        assert grant_condition(wight, Condition.POISONED, "effect-0001") == []
        assert not has_condition(wight, Condition.POISONED)

    def test_kill_is_idempotent(self, streams, allocator):
        troll = make_troll(streams, allocator)
        events = kill(troll)
        assert has_condition(troll, Condition.DEAD)
        assert troll.current_hp == 0
        assert [event.code for event in events] == [
            "effects.condition.gained",
            "combat.death.died",
            "combat.state.hit_points",
        ]
        assert kill(troll) == []

    def test_attach_refuses_immune_condition(self, streams, allocator):
        wight = spawn_monster(
            load_monsters().get("wight"), id=allocator.allocate("monster"), stream=streams.get("monster_spawn")
        )
        ledger, clock = EffectsLedger(), GameClock()
        definition = EffectDefinition(
            kind="poison_onset",
            duration_unit=TimeUnit.TURN,
            duration_amount=1,
            expiry="death",
            condition=Condition.POISONED,
        )
        effect, events = ledger.attach(
            definition, wight.id, clock=clock, allocator=allocator, registry={wight.id: wight}
        )
        assert effect is None and events == []


class TestLedger:
    def test_tick_order_expirations_before_ticks_in_attachment_order(self, streams, allocator):
        troll = make_troll(streams, allocator)
        troll.current_hp -= 12
        ledger, clock = EffectsLedger(), GameClock()
        registry = {troll.id: troll}
        # Attach an expiring effect *after* the regeneration so attachment order is
        # regeneration first; the expiry must still resolve before the tick.
        regen = regeneration_definition({"delay_rounds": 0, "per_round": 3})
        ledger.attach(regen, troll.id, clock=clock, allocator=allocator, registry=registry)
        timed = EffectDefinition(kind="marker", duration_unit=TimeUnit.ROUND, duration_amount=1)
        ledger.attach(timed, troll.id, clock=clock, allocator=allocator, registry=registry)
        events = ledger.advance(clock, 1, TimeUnit.ROUND, registry, stream=streams.get("effects"))
        codes = [event.code for event in events]
        assert codes.index("effects.effect.expired") < codes.index("effects.effect.ticked")

    def test_simultaneous_expiries_resolve_in_attachment_order(self, streams, allocator):
        troll = make_troll(streams, allocator)
        ledger, clock = EffectsLedger(), GameClock()
        registry = {troll.id: troll}
        first = EffectDefinition(kind="marker_a", duration_unit=TimeUnit.ROUND, duration_amount=1)
        second = EffectDefinition(kind="marker_b", duration_unit=TimeUnit.ROUND, duration_amount=1)
        ledger.attach(first, troll.id, clock=clock, allocator=allocator, registry=registry)
        ledger.attach(second, troll.id, clock=clock, allocator=allocator, registry=registry)
        events = ledger.advance(clock, 1, TimeUnit.ROUND, registry, stream=streams.get("effects"))
        expired = [event.kind for event in events if event.code == "effects.effect.expired"]
        assert expired == ["marker_a", "marker_b"]

    def test_advance_spans_turn_and_day_boundaries(self, streams, allocator):
        ledger, clock = EffectsLedger(), GameClock()
        torch = EffectDefinition(kind="torch", duration_unit=TimeUnit.TURN, duration_amount=6)
        effect, _ = ledger.attach(torch, "torch-1", clock=clock, allocator=allocator)
        assert effect.expires_round == 360
        events = ledger.advance(clock, 5, TimeUnit.TURN, {}, stream=streams.get("effects"))
        assert events == [] and ledger.effects
        events = ledger.advance(clock, 1, TimeUnit.TURN, {}, stream=streams.get("effects"))
        assert [event.code for event in events] == ["effects.effect.expired"]
        assert not ledger.effects

    def test_paralysis_duration_dice_and_expiry(self, streams, allocator):
        troll = make_troll(streams, allocator)
        ledger, clock = EffectsLedger(), GameClock()
        registry = {troll.id: troll}
        paralysis = EffectDefinition(
            kind="paralysis", duration_unit=TimeUnit.TURN, duration_dice="2d4", condition=Condition.PARALYSED
        )
        effect, events = ledger.attach(
            paralysis, troll.id, clock=clock, allocator=allocator, registry=registry, stream=streams.get("effects")
        )
        turns = effect.expires_round // 60
        assert 2 <= turns <= 8
        assert has_condition(troll, Condition.PARALYSED)
        events = ledger.advance(clock, turns, TimeUnit.TURN, registry, stream=streams.get("effects"))
        assert not has_condition(troll, Condition.PARALYSED)
        assert "effects.condition.removed" in [event.code for event in events]

    def test_delayed_death_poison(self, streams, allocator):
        troll = make_troll(streams, allocator)
        ledger, clock = EffectsLedger(), GameClock()
        registry = {troll.id: troll}
        onset = EffectDefinition(
            kind="poison_onset",
            duration_unit=TimeUnit.TURN,
            duration_dice="1d6",
            expiry="death",
            condition=Condition.POISONED,
        )
        effect, _ = ledger.attach(
            onset, troll.id, clock=clock, allocator=allocator, registry=registry, stream=streams.get("effects")
        )
        assert has_condition(troll, Condition.POISONED)
        ledger.advance(clock, effect.expires_round, TimeUnit.ROUND, registry, stream=streams.get("effects"))
        assert has_condition(troll, Condition.DEAD)
        assert troll.current_hp == 0

    def test_petrification_suspends_other_effects(self, streams, allocator):
        troll = make_troll(streams, allocator)
        ledger, clock = EffectsLedger(), GameClock()
        registry = {troll.id: troll}
        onset = EffectDefinition(
            kind="poison_onset",
            duration_unit=TimeUnit.ROUND,
            duration_amount=3,
            expiry="death",
            condition=Condition.POISONED,
        )
        ledger.attach(onset, troll.id, clock=clock, allocator=allocator, registry=registry)
        petrify = EffectDefinition(kind="petrification", permanent=True, condition=Condition.PETRIFIED)
        ledger.attach(petrify, troll.id, clock=clock, allocator=allocator, registry=registry)
        ledger.advance(clock, 10, TimeUnit.ROUND, registry, stream=streams.get("effects"))
        # The poison is frozen while petrified: still alive, still poisoned.
        assert not has_condition(troll, Condition.DEAD)
        assert has_condition(troll, Condition.POISONED)
        # Un-petrify (stone to flesh arrives in Phase 3; release stands in) and the
        # poison resumes where it left off.
        petrification = ledger.active_on(troll.id, "petrification")[0]
        ledger.release(petrification.effect_id, registry)
        ledger.advance(clock, 3, TimeUnit.ROUND, registry, stream=streams.get("effects"))
        assert has_condition(troll, Condition.DEAD)

    def test_stacking_refresh_and_ignore(self, streams, allocator):
        troll = make_troll(streams, allocator)
        ledger, clock = EffectsLedger(), GameClock()
        registry = {troll.id: troll}
        refresh = EffectDefinition(
            kind="refresher", duration_unit=TimeUnit.ROUND, duration_amount=5, stacking="refresh"
        )
        effect, _ = ledger.attach(refresh, troll.id, clock=clock, allocator=allocator, registry=registry)
        clock.advance(3)
        again, _ = ledger.attach(refresh, troll.id, clock=clock, allocator=allocator, registry=registry)
        assert again is effect
        assert effect.expires_round == 8
        ignore = EffectDefinition(kind="regeneration", tick="regeneration", stacking="ignore")
        first, _ = ledger.attach(ignore, troll.id, clock=clock, allocator=allocator, registry=registry)
        second, _ = ledger.attach(ignore, troll.id, clock=clock, allocator=allocator, registry=registry)
        assert first is not None and second is None

    def test_release_removes_condition(self, streams, allocator):
        troll = make_troll(streams, allocator)
        ledger, clock = EffectsLedger(), GameClock()
        registry = {troll.id: troll}
        blind = EffectDefinition(kind="blindness", condition=Condition.BLIND)
        effect, _ = ledger.attach(blind, troll.id, clock=clock, allocator=allocator, registry=registry)
        events = ledger.release(effect.effect_id, registry)
        assert not has_condition(troll, Condition.BLIND)
        assert [event.code for event in events] == ["effects.effect.released", "effects.condition.removed"]

    def test_single_writer_invariant(self, streams, allocator):
        # Every condition on the creature is owned by a live ledger effect (or is the
        # kernel's `dead`); releasing and expiring effects never leaves an orphan.
        troll = make_troll(streams, allocator)
        ledger, clock = EffectsLedger(), GameClock()
        registry = {troll.id: troll}
        paralysis = EffectDefinition(
            kind="paralysis", duration_unit=TimeUnit.ROUND, duration_amount=2, condition=Condition.PARALYSED
        )
        blind = EffectDefinition(kind="blindness", condition=Condition.BLIND)
        ledger.attach(paralysis, troll.id, clock=clock, allocator=allocator, registry=registry)
        effect, _ = ledger.attach(blind, troll.id, clock=clock, allocator=allocator, registry=registry)

        def assert_no_orphans():
            live = {effect.effect_id for effect in ledger.effects}
            for active in troll.conditions:
                assert active.effect_id is None or active.effect_id in live

        assert_no_orphans()
        ledger.advance(clock, 2, TimeUnit.ROUND, registry, stream=streams.get("effects"))
        assert_no_orphans()
        ledger.release(effect.effect_id, registry)
        assert_no_orphans()
        assert troll.conditions == ()


class TestRegeneration:
    def test_troll_delay_and_rate(self, streams, allocator):
        troll = make_troll(streams, allocator)
        ledger, clock = EffectsLedger(), GameClock()
        registry = {troll.id: troll}
        regen = regeneration_definition(load_monsters().get("troll").ability("regeneration").params)
        ledger.attach(regen, troll.id, clock=clock, allocator=allocator, registry=registry)
        troll.current_hp -= 10
        troll.last_damaged_round = clock.rounds
        hp = troll.current_hp
        ledger.advance(clock, 2, TimeUnit.ROUND, registry, stream=streams.get("effects"))
        assert troll.current_hp == hp  # 3-round delay: rounds 1 and 2 heal nothing
        ledger.advance(clock, 1, TimeUnit.ROUND, registry, stream=streams.get("effects"))
        assert troll.current_hp == hp + 3
        ledger.advance(clock, 10, TimeUnit.ROUND, registry, stream=streams.get("effects"))
        assert troll.current_hp == troll.max_hp  # capped

    def test_fire_damage_is_never_regenerated(self, streams, allocator):
        troll = make_troll(streams, allocator)
        troll.nonregen_damage = 8
        troll.current_hp = troll.max_hp - 8
        ledger, clock = EffectsLedger(), GameClock()
        registry = {troll.id: troll}
        regen = regeneration_definition(load_monsters().get("troll").ability("regeneration").params)
        ledger.attach(regen, troll.id, clock=clock, allocator=allocator, registry=registry)
        ledger.advance(clock, 20, TimeUnit.ROUND, registry, stream=streams.get("effects"))
        assert troll.current_hp == troll.max_hp - 8

    def test_death_and_revival(self, streams, allocator):
        troll = make_troll(streams, allocator)
        ledger, clock = EffectsLedger(), GameClock()
        registry = {troll.id: troll}
        regen = regeneration_definition(load_monsters().get("troll").ability("regeneration").params)
        ledger.attach(regen, troll.id, clock=clock, allocator=allocator, registry=registry)
        troll.current_hp = 0
        kill(troll)
        troll.last_damaged_round = clock.rounds
        events = ledger.advance(clock, 13, TimeUnit.ROUND, registry, stream=streams.get("effects"))
        codes = [event.code for event in events]
        assert "effects.regeneration.revived" in codes
        assert not has_condition(troll, Condition.DEAD)
        assert troll.current_hp >= 1

    def test_permanently_dead_troll_never_revives(self, streams, allocator):
        troll = make_troll(streams, allocator)
        ledger, clock = EffectsLedger(), GameClock()
        registry = {troll.id: troll}
        regen = regeneration_definition(load_monsters().get("troll").ability("regeneration").params)
        ledger.attach(regen, troll.id, clock=clock, allocator=allocator, registry=registry)
        troll.nonregen_damage = troll.max_hp
        troll.current_hp = 0
        kill(troll, permanent=True)
        events = ledger.advance(clock, 30, TimeUnit.ROUND, registry, stream=streams.get("effects"))
        assert "effects.regeneration.revived" not in [event.code for event in events]
        assert has_condition(troll, Condition.DEAD)

    def test_vampire_regenerates_only_while_alive(self, streams, allocator):
        vampire = spawn_monster(
            load_monsters().get("vampire_9"), id=allocator.allocate("monster"), stream=streams.get("monster_spawn")
        )
        ledger, clock = EffectsLedger(), GameClock()
        registry = {vampire.id: vampire}
        regen = regeneration_definition(load_monsters().get("vampire_9").ability("regeneration").params)
        ledger.attach(regen, vampire.id, clock=clock, allocator=allocator, registry=registry)
        vampire.current_hp -= 10
        vampire.last_damaged_round = clock.rounds
        hp = vampire.current_hp
        ledger.advance(clock, 1, TimeUnit.ROUND, registry, stream=streams.get("effects"))
        assert vampire.current_hp == hp + 3  # no delay
        vampire.current_hp = 0
        kill(vampire)
        events = ledger.advance(clock, 20, TimeUnit.ROUND, registry, stream=streams.get("effects"))
        assert "effects.regeneration.revived" not in [event.code for event in events]


class TestMummyRot:
    def make_rotting(self, streams, allocator):
        victim = make_troll(streams, allocator)  # any creature with hit points serves
        ledger, clock = EffectsLedger(), GameClock()
        registry = {victim.id: victim}
        rot = EffectDefinition(kind="mummy_rot", condition=Condition.DISEASED, params={"kind": "mummy_rot"})
        ledger.attach(rot, victim.id, clock=clock, allocator=allocator, registry=registry)
        victim.current_hp = 1
        return victim, ledger

    def test_magical_healing_is_blocked(self, streams, allocator):
        victim, _ = self.make_rotting(streams, allocator)
        events = apply_healing(victim, 5, source="magical")
        assert [event.code for event in events] == ["combat.healing.blocked"]
        assert victim.current_hp == 1

    def test_natural_healing_runs_ten_times_slower(self, streams, allocator):
        victim, ledger = self.make_rotting(streams, allocator)
        healed_days = 0
        for _ in range(20):
            events = natural_healing(victim, streams.get("effects"), ledger=ledger)
            if events:
                healed_days += 1
        assert healed_days == 2  # days 10 and 20 of complete rest

    def test_healthy_creature_heals_every_rest_day(self, streams, allocator):
        victim = make_troll(streams, allocator)
        victim.current_hp = 1
        events = natural_healing(victim, streams.get("effects"))
        assert events and events[0].code == "combat.healing.applied"
        assert 1 + 1 <= victim.current_hp <= 1 + 3
