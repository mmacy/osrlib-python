"""Tests for energy drain: the level_up inverse, XP policies, and monster HD drain."""

import pytest

from osrlib.core.abilities import AbilityScore
from osrlib.core.character import Alignment, Character
from osrlib.core.classes import apply_xp, drain_levels
from osrlib.core.combat import drain_monster_hd, resolve_energy_drain
from osrlib.core.effects import Condition, has_condition
from osrlib.core.monsters import spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.data import load_classes, load_monsters


class FixedStream:
    def __init__(self, values):
        self.values = list(values)

    def randbelow(self, n):
        return self.values.pop(0) % n


def make_fighter(level, *, con=10, max_hp=None):
    scores = {ability: 10 for ability in AbilityScore}
    scores[AbilityScore.CON] = con
    hp = max_hp if max_hp is not None else 6 * level
    return Character(
        id="pc",
        name="Hero",
        class_id="fighter",
        race="human",
        level=level,
        xp=load_classes().get("fighter").row(level).xp,
        scores=scores,
        alignment=Alignment.NEUTRAL,
        max_hp=hp,
        current_hp=hp,
    )


class TestDrainLevels:
    def test_one_level_with_hp_roll_and_halfway_xp(self):
        fighter = make_fighter(4)
        definition = load_classes().get("fighter")
        result = drain_levels(fighter, definition, xp_policy="halfway", stream=FixedStream([5]))
        assert result.levels_lost == 1 and fighter.level == 3
        assert result.hp_rolls == (6,)
        assert result.hp_lost == 6
        assert fighter.max_hp == 24 - 6
        # Fighter L4 threshold 8,000, L3 threshold 4,000 → floored halfway 6,000.
        assert fighter.xp == 6_000 and result.xp_after == 6_000
        assert result.events[0].code == "combat.drain.drained"

    def test_halfway_floors(self):
        thief = Character(
            id="pc",
            name="Sly",
            class_id="thief",
            race="human",
            level=2,
            xp=load_classes().get("thief").row(2).xp,
            scores={ability: 10 for ability in AbilityScore},
            alignment=Alignment.NEUTRAL,
            max_hp=8,
            current_hp=8,
        )
        definition = load_classes().get("thief")
        result = drain_levels(thief, definition, xp_policy="halfway", stream=FixedStream([3]))
        # Thief L2 threshold 1,200, L1 threshold 0 → floored halfway 600.
        assert result.xp_after == (1_200 + 0) // 2 == 600

    def test_level_minimum_policy(self):
        fighter = make_fighter(4)
        definition = load_classes().get("fighter")
        result = drain_levels(fighter, definition, xp_policy="level_minimum", stream=FixedStream([5]))
        assert result.xp_after == 4_000 and fighter.xp == 4_000

    def test_con_modifier_applies_and_floors_at_one_per_die(self):
        fighter = make_fighter(2, con=18, max_hp=30)  # +3 per die
        definition = load_classes().get("fighter")
        result = drain_levels(fighter, definition, xp_policy="halfway", stream=FixedStream([4]))
        assert result.hp_lost == 8  # d8 shows 5, +3 CON
        fighter2 = make_fighter(2, con=3, max_hp=30)  # −3 per die
        result = drain_levels(fighter2, definition, xp_policy="halfway", stream=FixedStream([0]))
        assert result.hp_lost == 1  # 1 − 3 floors at 1

    def test_two_level_spectre_drain_applies_twice_sets_xp_once(self):
        fighter = make_fighter(5)
        definition = load_classes().get("fighter")
        result = drain_levels(fighter, definition, levels=2, xp_policy="level_minimum", stream=FixedStream([5, 3]))
        assert result.levels_lost == 2 and fighter.level == 3
        assert result.hp_rolls == (6, 4)
        assert fighter.xp == definition.row(3).xp

    def test_above_name_level_flat_reversal_no_roll(self):
        fighter = make_fighter(10, max_hp=60)
        definition = load_classes().get("fighter")
        stream = FixedStream([])  # any draw would raise
        result = drain_levels(fighter, definition, xp_policy="level_minimum", stream=stream)
        # Fighter L10 is 9d8+2, L9 is 9d8: the flat delta is 2.
        assert result.hp_rolls == () and result.hp_lost == 2
        assert fighter.level == 9

    def test_floors_at_one_hp_while_a_level_remains(self):
        fighter = make_fighter(2, max_hp=3)
        fighter.current_hp = 2
        definition = load_classes().get("fighter")
        drain_levels(fighter, definition, xp_policy="halfway", stream=FixedStream([7]))
        assert fighter.level == 1
        assert fighter.max_hp == 1 and fighter.current_hp == 1
        assert not has_condition(fighter, Condition.DEAD)

    def test_terminal_drain_kills_with_the_manual_spawn_field(self):
        fighter = make_fighter(1)
        definition = load_classes().get("fighter")
        prose = "A person drained of all levels becomes a wight in 1d4 days."
        result = drain_levels(fighter, definition, xp_policy="halfway", stream=FixedStream([]), spawn_consequence=prose)
        assert result.slain and result.new_level == 0
        assert result.levels_lost == 1  # the killing level counts (pinned)
        assert has_condition(fighter, Condition.DEAD)
        assert fighter.current_hp == 0 and fighter.xp == 0
        drained = result.events[0]
        assert drained.code == "combat.drain.slain"
        assert drained.spawn_consequence == prose

    def test_drain_then_award_round_trip(self):
        streams = RngStreams(master_seed=5)
        fighter = make_fighter(3)
        definition = load_classes().get("fighter")
        drain_levels(fighter, definition, xp_policy="halfway", stream=streams.get("advancement"))
        assert fighter.level == 2
        award = apply_xp(fighter, definition, 10_000, streams.get("advancement"))
        # The one-level-per-award clamp holds from the drained state.
        assert fighter.level == 3
        assert award.clamped and fighter.xp == definition.row(4).xp - 1

    def test_validation(self):
        fighter = make_fighter(2)
        definition = load_classes().get("fighter")
        with pytest.raises(ValueError):
            drain_levels(fighter, load_classes().get("thief"), xp_policy="halfway", stream=FixedStream([]))
        with pytest.raises(ValueError):
            drain_levels(fighter, definition, levels=0, xp_policy="halfway", stream=FixedStream([]))
        with pytest.raises(ValueError):
            drain_levels(fighter, definition, xp_policy="midpoint", stream=FixedStream([]))


class TestMonsterDrain:
    def test_monster_loses_hd_and_rederives(self):
        streams = RngStreams(master_seed=9)
        troll = spawn_monster(load_monsters().get("troll"), id="m-1", stream=streams.get("monster_spawn"))
        assert (troll.thac0, troll.saves.death) == (13, 10)
        events = drain_monster_hd(troll, levels=2, stream=FixedStream([4, 4]))
        assert troll.hit_dice_count == 4
        assert troll.thac0 == 15  # HD 4 with the +3 bonus attacks as 5: the 4+ to 5 row
        assert troll.saves.death == 10  # 4-6 band
        assert events[0].code == "combat.drain.drained"
        events = drain_monster_hd(troll, levels=1, stream=FixedStream([4]))
        assert troll.hit_dice_count == 3
        assert troll.saves.death == 12  # 1-3 band

    def test_monster_drained_below_one_hd_dies(self):
        streams = RngStreams(master_seed=9)
        ghoul = spawn_monster(load_monsters().get("ghoul"), id="m-2", stream=streams.get("monster_spawn"))
        events = drain_monster_hd(ghoul, levels=2, stream=FixedStream([4]))
        assert has_condition(ghoul, Condition.DEAD)
        assert events[0].code == "combat.drain.slain"
        assert events[0].levels_lost == 2  # the killing Hit Die counts (pinned)


class TestWiring:
    def test_wight_touch_drains_by_tag(self):
        streams = RngStreams(master_seed=9)
        wight = spawn_monster(load_monsters().get("wight"), id="m-w", stream=streams.get("monster_spawn"))
        fighter = make_fighter(4)
        events = resolve_energy_drain(wight, fighter, stream=FixedStream([5]))
        assert fighter.level == 3 and fighter.xp == 6_000
        assert "becomes a wight" in events[0].spawn_consequence

    def test_spectre_touch_drains_two_by_tag(self):
        streams = RngStreams(master_seed=9)
        spectre = spawn_monster(load_monsters().get("spectre"), id="m-s", stream=streams.get("monster_spawn"))
        fighter = make_fighter(5)
        resolve_energy_drain(spectre, fighter, stream=FixedStream([5, 5]))
        assert fighter.level == 3
        assert fighter.xp == load_classes().get("fighter").row(3).xp

    def test_wight_drains_a_monster_symmetrically(self):
        streams = RngStreams(master_seed=9)
        wight = spawn_monster(load_monsters().get("wight"), id="m-w", stream=streams.get("monster_spawn"))
        troll = spawn_monster(load_monsters().get("troll"), id="m-t", stream=streams.get("monster_spawn"))
        resolve_energy_drain(wight, troll, stream=FixedStream([4]))
        assert troll.hit_dice_count == 5
