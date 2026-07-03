"""Tests for osrlib.core.classes — XP modifiers, awards, and leveling up."""

import pytest

from osrlib.core.abilities import AbilityScore
from osrlib.core.character import Character
from osrlib.core.classes import apply_xp, level_up, xp_modifier_pct
from osrlib.core.rng import RngStream
from osrlib.data import load_classes


def stream(seed: int = 0) -> RngStream:
    return RngStream.from_seed_material(seed, "advancement")


def character(class_id: str, level: int = 1, xp: int = 0, **score_overrides: int) -> Character:
    scores = {ability: 11 for ability in AbilityScore}
    scores.update({AbilityScore(key): value for key, value in score_overrides.items()})
    definition = load_classes().get(class_id)
    return Character(
        name="Test",
        class_id=class_id,
        race=definition.race,
        level=level,
        xp=xp,
        scores=scores,
        alignment="neutral",
        max_hp=8,
        current_hp=8,
    )


class TestXpModifier:
    def test_standard_table_first_match_wins(self):
        fighter = load_classes().get("fighter")
        expectations = {3: -20, 5: -20, 6: -10, 8: -10, 9: 0, 12: 0, 13: 5, 15: 5, 16: 10, 18: 10}
        for score, expected in expectations.items():
            scores = {ability: 11 for ability in AbilityScore}
            scores[AbilityScore.STR] = score
            assert xp_modifier_pct(fighter, scores) == expected, score

    def test_elf_tiers(self):
        elf = load_classes().get("elf")
        scores = {ability: 9 for ability in AbilityScore}
        scores[AbilityScore.INT] = 16
        scores[AbilityScore.STR] = 13
        assert xp_modifier_pct(elf, scores) == 10
        scores[AbilityScore.INT] = 13
        assert xp_modifier_pct(elf, scores) == 5
        scores[AbilityScore.STR] = 12
        assert xp_modifier_pct(elf, scores) == 0

    def test_halfling_either_or_tier(self):
        halfling = load_classes().get("halfling")
        scores = {ability: 9 for ability in AbilityScore}
        scores[AbilityScore.DEX] = 13
        assert xp_modifier_pct(halfling, scores) == 5
        scores[AbilityScore.DEX] = 9
        scores[AbilityScore.STR] = 13
        assert xp_modifier_pct(halfling, scores) == 5
        scores[AbilityScore.DEX] = 13
        assert xp_modifier_pct(halfling, scores) == 10

    def test_multi_prime_requisite_classes_have_no_penalties(self):
        # Pinned: the standard table applies to single-prime-requisite characters;
        # the elf's and halfling's descriptions note only bonuses.
        elf = load_classes().get("elf")
        halfling = load_classes().get("halfling")
        floor_scores = {ability: 3 for ability in AbilityScore}
        assert xp_modifier_pct(elf, floor_scores) == 0
        assert xp_modifier_pct(halfling, floor_scores) == 0


class TestApplyXp:
    def test_award_crossing_one_threshold_levels_once(self):
        fighter = load_classes().get("fighter")
        hero = character("fighter", level=1, xp=0)
        result = apply_xp(hero, fighter, 2_000, stream())
        assert (hero.level, hero.xp) == (2, 2_000)
        assert result.level_up is not None
        assert not result.clamped
        assert hero.max_hp == 8 + result.level_up.hp_gained

    def test_award_that_would_cross_two_clamps_to_one_below(self):
        fighter = load_classes().get("fighter")
        hero = character("fighter", level=1, xp=0)
        result = apply_xp(hero, fighter, 4_500, stream())
        assert (hero.level, hero.xp) == (2, 3_999)
        assert result.clamped

    def test_modifier_floors(self):
        # Pinned: percentage results floor. 15 XP at -10% is 13.5 → 13.
        fighter = load_classes().get("fighter")
        hero = character("fighter", str=7)
        result = apply_xp(hero, fighter, 15, stream())
        assert result.modifier_pct == -10
        assert result.modified_award == 13
        bonus = character("fighter", str=18)
        result = apply_xp(bonus, fighter, 15, stream())
        assert result.modifier_pct == 10
        assert result.modified_award == 16  # 16.5 floors

    def test_at_max_level_xp_accumulates_without_leveling(self):
        halfling = load_classes().get("halfling")
        hero = character("halfling", level=8, xp=120_000, con=9, dex=9)
        result = apply_xp(hero, halfling, 1_000_000, stream())
        assert (hero.level, hero.xp) == (8, 1_120_000)
        assert result.level_up is None
        assert not result.clamped

    def test_demihuman_cap_holds_when_crossing_into_it(self):
        halfling = load_classes().get("halfling")
        hero = character("halfling", level=7, xp=64_000, con=9, dex=9)
        apply_xp(hero, halfling, 10_000_000, stream())
        assert hero.level == 8
        assert hero.xp == 64_000 + 10_000_000  # no second threshold exists, so no clamp

    def test_negative_award_raises(self):
        fighter = load_classes().get("fighter")
        with pytest.raises(ValueError):
            apply_xp(character("fighter"), fighter, -1, stream())

    def test_mismatched_definition_raises(self):
        thief = load_classes().get("thief")
        with pytest.raises(ValueError):
            apply_xp(character("fighter"), thief, 100, stream())


class TestLevelUp:
    def test_hp_roll_plus_con_while_hd_grow(self):
        fighter = load_classes().get("fighter")
        hero = character("fighter", con=16)  # +2 hp per die
        result = level_up(hero, fighter, stream())
        assert result.hp_roll is not None
        assert result.con_applied
        assert result.hp_gained == max(1, result.hp_roll + 2)
        assert hero.level == 2
        assert hero.max_hp == 8 + result.hp_gained
        assert hero.current_hp == 8 + result.hp_gained

    def test_minimum_one_per_die(self):
        fighter = load_classes().get("fighter")
        for seed in range(60):
            hero = character("fighter", con=3)  # -3 hp per die
            result = level_up(hero, fighter, stream(seed))
            assert result.hp_gained >= 1
            if result.hp_roll <= 3:
                assert result.hp_gained == 1

    def test_name_level_flat_bonus_ignores_con(self):
        fighter = load_classes().get("fighter")
        hero = character("fighter", level=9, xp=240_000, con=18)
        result = level_up(hero, fighter, stream())
        assert result.hp_roll is None
        assert not result.con_applied
        assert result.hp_gained == 2  # 9d8 → 9d8+2
        assert hero.max_hp == 10

    def test_flat_bonus_delta_between_name_levels(self):
        dwarf = load_classes().get("dwarf")
        hero = character("dwarf", level=10, xp=400_000, con=9)
        result = level_up(hero, dwarf, stream())
        assert result.hp_gained == 3  # 9d8+3 → 9d8+6

    def test_leveling_does_not_heal_damage(self):
        fighter = load_classes().get("fighter")
        hero = character("fighter")
        hero.current_hp = 3
        level_up(hero, fighter, stream())
        assert hero.max_hp - hero.current_hp == 5  # the wound persists

    def test_at_cap_raises(self):
        halfling = load_classes().get("halfling")
        hero = character("halfling", level=8, xp=120_000, con=9, dex=9)
        with pytest.raises(ValueError):
            level_up(hero, halfling, stream())

    def test_saves_thac0_and_slots_read_from_the_row(self):
        cleric = load_classes().get("cleric")
        row = cleric.row(5)
        assert (row.thac0, row.attack_bonus) == (17, 2)
        assert row.saves.death == 9
        assert row.spell_slots == (2, 2, 0, 0, 0)
        with pytest.raises(ValueError):
            cleric.row(15)
