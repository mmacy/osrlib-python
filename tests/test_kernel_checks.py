"""Tests for the Phase 4 kernel checks: thief skills, detection, and reaction rolls."""

import pytest

from osrlib.core.abilities import AbilityScore
from osrlib.core.character import Character
from osrlib.core.classes import (
    PERCENTILE_THIEF_SKILLS,
    detection_chance,
    detection_check,
    thief_skill_check,
)
from osrlib.core.combat import roll_reaction
from osrlib.core.events import Visibility
from osrlib.core.rng import RngStream
from osrlib.core.tables import ReactionResult
from osrlib.data import load_classes


def stream(seed: int = 0, key: str = "exploration") -> RngStream:
    return RngStream.from_seed_material(seed, key)


def character(class_id: str, level: int = 1) -> Character:
    definition = load_classes().get(class_id)
    return Character(
        name="Test",
        class_id=class_id,
        race=definition.race,
        level=level,
        xp=definition.row(level).xp,
        scores={ability: 11 for ability in AbilityScore},
        alignment="neutral",
        max_hp=8,
        current_hp=8,
    )


class TestThiefSkillCheck:
    def test_percentile_skills_roll_under_the_level_row(self):
        thief = character("thief", level=4)
        definition = load_classes().get("thief")
        row = definition.thief_skills[3]
        for skill in PERCENTILE_THIEF_SKILLS:
            source = stream(skill_seed(skill))
            expected_roll = peek_d100(stream(skill_seed(skill)))
            result = thief_skill_check(thief, definition, skill, stream=source)
            assert result.roll == expected_roll
            assert result.chance == getattr(row, skill)
            assert result.passed is (result.roll <= result.chance)

    def test_hear_noise_rolls_in_six(self):
        thief = character("thief", level=7)
        definition = load_classes().get("thief")
        result = thief_skill_check(thief, definition, "hear_noise", stream=stream(3))
        assert result.chance == 4  # the level-7 row's 1–4
        assert 1 <= result.roll <= 6
        assert result.passed is (result.roll <= 4)

    def test_level_rows_advance_the_chance(self):
        definition = load_classes().get("thief")
        low = thief_skill_check(character("thief", level=1), definition, "open_locks", stream=stream(0))
        high = thief_skill_check(character("thief", level=9), definition, "open_locks", stream=stream(0))
        assert (low.chance, high.chance) == (15, 75)

    def test_pick_pockets_caps_at_99_and_notices(self):
        definition = load_classes().get("thief")
        thief = character("thief", level=14)  # printed 125
        result = thief_skill_check(thief, definition, "pick_pockets", stream=stream(0))
        assert result.chance == 99  # always at least a 1% chance of failure
        # The over-5th-level victim penalty arrives as the caller's modifier.
        penalized = thief_skill_check(
            character("thief", level=1), definition, "pick_pockets", modifier_pct=-15, stream=stream(0)
        )
        assert penalized.chance == 5
        assert penalized.noticed is (penalized.roll > 10)

    def test_non_thief_class_raises(self):
        with pytest.raises(ValueError):
            thief_skill_check(character("fighter"), load_classes().get("fighter"), "open_locks", stream=stream(0))

    def test_unknown_skill_raises(self):
        with pytest.raises(ValueError):
            thief_skill_check(character("thief"), load_classes().get("thief"), "juggling", stream=stream(0))


def skill_seed(skill: str) -> int:
    return sum(ord(letter) for letter in skill)


def peek_d100(source: RngStream) -> int:
    return source.randbelow(100) + 1


class TestDetectionCheck:
    def test_rolls_one_d6_under_the_chance(self):
        source = stream(1)
        expected = stream(1).randbelow(6) + 1
        result = detection_check(2, stream=source)
        assert result.roll == expected
        assert result.passed is (result.roll <= 2)

    def test_zero_chance_consumes_no_draw_and_fails(self):
        source = stream(2)
        before = source.export_state()
        result = detection_check(0, stream=source)
        assert result.passed is False
        assert result.roll is None
        assert source.export_state() == before


class TestDetectionChance:
    def test_listening_precedence_thief_row_over_class_tag_over_baseline(self):
        classes = load_classes()
        # Thief: the hear_noise row wins (level 7 is 1–4).
        assert detection_chance(character("thief", level=7), classes.get("thief"), "listening") == 4
        # Demi-humans: the listening_at_doors tag (2-in-6).
        assert detection_chance(character("dwarf"), classes.get("dwarf"), "listening") == 2
        # Everyone else: the universal 1-in-6.
        assert detection_chance(character("fighter"), classes.get("fighter"), "listening") == 1

    def test_secret_doors_elf_two_else_one(self):
        classes = load_classes()
        assert detection_chance(character("elf"), classes.get("elf"), "secret_doors") == 2
        assert detection_chance(character("fighter"), classes.get("fighter"), "secret_doors") == 1

    def test_room_traps_dwarf_two_else_one(self):
        classes = load_classes()
        assert detection_chance(character("dwarf"), classes.get("dwarf"), "room_traps") == 2
        assert detection_chance(character("magic_user"), classes.get("magic_user"), "room_traps") == 1

    def test_construction_is_dwarf_only_with_zero_baseline(self):
        classes = load_classes()
        assert detection_chance(character("dwarf"), classes.get("dwarf"), "construction") == 2
        assert detection_chance(character("fighter"), classes.get("fighter"), "construction") == 0

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError):
            detection_chance(character("fighter"), load_classes().get("fighter"), "vibes")


class TestRollReaction:
    def test_rolls_2d6_plus_modifier(self):
        source = stream(0, "encounter")
        probe = stream(0, "encounter")
        expected = probe.randbelow(6) + 1 + probe.randbelow(6) + 1
        result = roll_reaction(modifier=1, stream=source)
        assert result.roll == expected
        assert result.total == expected + 1
        assert result.result is ReactionResult(result.events[0].result)

    def test_extreme_modifiers_clamp_into_outer_bands(self):
        low = roll_reaction(modifier=-15, stream=stream(0, "encounter"))
        high = roll_reaction(modifier=+15, stream=stream(0, "encounter"))
        assert low.result is ReactionResult.ATTACKS
        assert high.result is ReactionResult.FRIENDLY

    def test_event_is_referee_visibility(self):
        result = roll_reaction(stream=stream(5, "encounter"))
        (event,) = result.events
        assert event.code == "encounter.reaction.rolled"
        assert event.visibility is Visibility.REFEREE
