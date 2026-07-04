"""Tests for osrlib.core.character — the model, creation steps, and derived values."""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from osrlib.core.abilities import AbilityScore
from osrlib.core.alignment import Alignment
from osrlib.core.character import (
    ABILITY_ROLL_ORDER,
    Character,
    create_character,
    roll_ability_scores,
    roll_hit_points,
    roll_starting_gold,
    validate_class_choice,
    validate_extra_languages,
)
from osrlib.core.items import CoinPurse, Inventory, ItemInstance, equip
from osrlib.core.rng import RngStream, RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.data import load_ability_tables, load_classes, load_equipment


def stream(seed: int = 0) -> RngStream:
    return RngStream.from_seed_material(seed, "character_creation")


def base_scores(**overrides: int) -> dict[AbilityScore, int]:
    scores = {ability: 11 for ability in AbilityScore}
    scores.update({AbilityScore(key): value for key, value in overrides.items()})
    return scores


def make_character(**overrides: object) -> Character:
    fields: dict[str, object] = {
        "name": "Test",
        "class_id": "fighter",
        "race": "human",
        "level": 1,
        "xp": 0,
        "scores": base_scores(),
        "alignment": "neutral",
        "max_hp": 6,
        "current_hp": 6,
    }
    fields.update(overrides)
    return Character(**fields)


class TestCharacterModel:
    def test_structural_validation(self):
        with pytest.raises(ValueError):
            make_character(scores={AbilityScore.STR: 11})  # missing abilities
        with pytest.raises(ValueError):
            make_character(scores=base_scores(str=19))
        with pytest.raises(ValueError):
            make_character(level=15)  # fighter caps at 14
        with pytest.raises(ValueError):
            make_character(current_hp=7)  # above max
        with pytest.raises(ValueError):
            make_character(class_id="paladin")  # unknown class

    def test_id_defaults_to_none_until_sessions_exist(self):
        assert make_character().id is None

    def test_modifiers_derive_from_tables(self):
        hero = make_character(scores=base_scores(str=17, dex=3, con=13, wis=18, cha=5))
        assert hero.melee_modifier == 2
        assert hero.open_doors_chance == 4
        assert hero.missile_modifier == -3
        assert hero.initiative_modifier == -2
        assert hero.hit_point_modifier == 1
        assert hero.magic_save_modifier == 3
        assert hero.npc_reaction_modifier == -1

    def test_ac_unarmoured_with_dex(self):
        hero = make_character(scores=base_scores(dex=16))
        assert hero.armour_class == 7  # 9 - 2
        assert hero.armour_class_ascending == 12  # 10 + 2

    def test_ac_with_armour_shield_and_dex(self):
        fighter = load_classes().get("fighter")
        equipment = load_equipment()
        inventory = Inventory(
            items=[ItemInstance(template=equipment.get("plate_mail")), ItemInstance(template=equipment.get("shield"))]
        )
        equip(inventory, fighter, inventory.items[0])
        equip(inventory, fighter, inventory.items[0])
        hero = make_character(inventory=inventory, scores=base_scores(dex=13))
        assert hero.armour_class == 1  # 3 - 1 shield - 1 dex
        assert hero.armour_class_ascending == 18  # 16 + 1 + 1
        assert hero.armour_class + hero.armour_class_ascending == 19

    def test_penalty_dex_raises_descending_ac(self):
        hero = make_character(scores=base_scores(dex=4))
        assert hero.armour_class == 11  # 9 - (-2)

    def test_literacy_and_languages(self):
        hero = make_character(class_id="dwarf", race="dwarf", scores=base_scores(con=9, int=6))
        assert hero.literacy == "basic"
        assert hero.languages == ("alignment_neutral", "common", "dwarvish", "gnomish", "goblin", "kobold")

    def test_alignment_tongue_derives_from_alignment(self):
        hero = make_character(alignment="chaotic")
        assert hero.alignment_tongue == "alignment_chaotic"
        hero.alignment = Alignment.LAWFUL
        assert hero.alignment_tongue == "alignment_lawful"

    def test_movement_rate_uses_ruleset_and_treasure_flag(self):
        hero = make_character(inventory=Inventory(purse=CoinPurse(gp=100)), carrying_treasure=True)
        assert hero.movement_rate(Ruleset()) == 90


class TestRollAbilityScores:
    def test_draw_order_is_pinned(self):
        # 18 sequential d6 draws, three per ability, in SRD listing order.
        rolls = roll_ability_scores(stream(7))
        replay = stream(7)
        for ability in ABILITY_ROLL_ORDER:
            expected = tuple(replay.randbelow(6) + 1 for _ in range(3))
            assert rolls.rolls[ability] == expected
            assert rolls.scores[ability] == sum(expected)

    def test_scores_in_3d6_range(self):
        for seed in range(30):
            rolls = roll_ability_scores(stream(seed))
            assert all(3 <= score <= 18 for score in rolls.scores.values())


class TestClassChoice:
    def test_requirements_met(self):
        dwarf = load_classes().get("dwarf")
        assert validate_class_choice(base_scores(con=9), dwarf) == []

    def test_demihuman_requirement_rejections(self):
        classes = load_classes()
        rejections = validate_class_choice(base_scores(con=8), classes.get("dwarf"))
        assert [rejection.code for rejection in rejections] == ["creation.class.requirements_not_met"]
        assert rejections[0].params == {"class": "dwarf", "ability": "con", "minimum": 9, "score": 8}
        assert validate_class_choice(base_scores(int=8), classes.get("elf")) != []
        halfling_rejections = validate_class_choice(base_scores(con=8, dex=8), classes.get("halfling"))
        assert len(halfling_rejections) == 2


class TestRollHitPoints:
    def test_die_plus_con_minimum_one(self):
        fighter = load_classes().get("fighter")
        for seed in range(40):
            result = roll_hit_points(fighter, -3, Ruleset(), stream(seed))
            assert result.hit_points == max(1, result.rolls[-1] - 3)
            assert len(result.rolls) == 1

    def test_reroll_flag_rerolls_while_die_shows_1_or_2(self):
        # Pinned: re-roll repeats until the raw die shows 3+, each consuming a draw.
        fighter = load_classes().get("fighter")
        ruleset = Ruleset(hp_reroll_at_first_level=True)
        saw_reroll = False
        for seed in range(60):
            result = roll_hit_points(fighter, 0, ruleset, stream(seed))
            assert result.rolls[-1] >= 3
            assert all(roll <= 2 for roll in result.rolls[:-1])
            saw_reroll = saw_reroll or len(result.rolls) > 1
        assert saw_reroll

    def test_reroll_draw_count_accounting(self):
        # The flag changes the number of draws consumed, and only the raw die
        # (before CON) is consulted — verified by replaying the same seed.
        fighter = load_classes().get("fighter")
        for seed in range(60):
            with_flag = roll_hit_points(fighter, 0, Ruleset(hp_reroll_at_first_level=True), stream(seed))
            without = roll_hit_points(fighter, 0, Ruleset(), stream(seed))
            assert with_flag.rolls[0] == without.rolls[0]
            expected_draws = 1
            replay = stream(seed)
            while replay.randbelow(8) + 1 <= 2:
                expected_draws += 1
            assert len(with_flag.rolls) == expected_draws


class TestExtraLanguages:
    def test_int_allowance(self):
        elf = load_classes().get("elf")
        rejections = validate_extra_languages(elf, 12, ["dragon"])
        assert [rejection.code for rejection in rejections] == ["creation.languages.too_many"]
        assert validate_extra_languages(elf, 13, ["dragon"]) == []
        assert validate_extra_languages(elf, 18, ["dragon", "medusa", "pixie"]) == []

    def test_choices_come_from_the_other_languages_table(self):
        elf = load_classes().get("elf")
        rejections = validate_extra_languages(elf, 18, ["common"])
        assert [rejection.code for rejection in rejections] == ["creation.languages.not_available"]
        rejections = validate_extra_languages(elf, 18, ["dothraki"])
        assert [rejection.code for rejection in rejections] == ["creation.languages.not_available"]

    def test_no_duplicating_class_natives(self):
        # Pinned: a dwarf cannot spend an INT language on Dwarvish.
        dwarf = load_classes().get("dwarf")
        rejections = validate_extra_languages(dwarf, 18, ["dwarvish"])
        assert [rejection.code for rejection in rejections] == ["creation.languages.duplicates_native"]

    def test_no_repeats(self):
        elf = load_classes().get("elf")
        rejections = validate_extra_languages(elf, 18, ["dragon", "dragon"])
        assert [rejection.code for rejection in rejections] == ["creation.languages.duplicate_choice"]


class TestStartingGold:
    def test_three_d6_times_ten(self):
        for seed in range(30):
            result = roll_starting_gold(stream(seed))
            assert result.total % 10 == 0
            assert 30 <= result.total <= 180
            assert result.total == sum(result.rolls) * 10


class TestCreateCharacter:
    def test_draws_come_only_from_the_given_stream(self):
        # Same master seed, fresh container: identical characters — and only the
        # character_creation stream is consumed.
        results = []
        for _ in range(2):
            streams = RngStreams(master_seed=99)
            result = create_character(
                name="Ilse",
                class_id="magic_user",
                alignment=Alignment.NEUTRAL,
                ruleset=Ruleset(),
                stream=streams.get("character_creation"),
                starting_spell_ids=["sleep"],
                purchases=[("dagger", 1), ("oil_flask", 1)],
                equip_ids=["dagger"],
            )
            results.append(result)
        assert results[0].character == results[1].character
        assert results[0].ability_rolls == results[1].ability_rolls

    def test_failed_requirements_raise(self):
        for seed in range(200):
            streams = RngStreams(master_seed=seed)
            probe = roll_ability_scores(streams.get("character_creation"))
            if probe.scores[AbilityScore.CON] >= 9:
                continue
            fresh = RngStreams(master_seed=seed)
            with pytest.raises(ValueError, match="requirements_not_met"):
                create_character(
                    name="Doomed",
                    class_id="dwarf",
                    alignment=Alignment.LAWFUL,
                    ruleset=Ruleset(),
                    stream=fresh.get("character_creation"),
                )
            return
        pytest.fail("no seed produced a sub-9 CON in 200 tries")

    def test_unknown_ids_raise(self):
        with pytest.raises(ValueError):
            create_character(
                name="X",
                class_id="bard",
                alignment=Alignment.NEUTRAL,
                ruleset=Ruleset(),
                stream=stream(),
            )
        with pytest.raises(ValueError):
            create_character(
                name="X",
                class_id="fighter",
                alignment=Alignment.NEUTRAL,
                ruleset=Ruleset(),
                stream=stream(),
                purchases=[("vorpal_sword", 1)],
            )


@settings(max_examples=25, deadline=None)
@given(
    seed=st.integers(min_value=0, max_value=10_000),
    class_id=st.sampled_from(["cleric", "fighter", "magic_user", "thief"]),
    alignment=st.sampled_from(list(Alignment)),
)
def test_any_legal_creation_yields_a_structurally_valid_character(seed: int, class_id: str, alignment: Alignment):
    """Human classes have no requirements, so every seed must create cleanly."""
    streams = RngStreams(master_seed=seed)
    result = create_character(
        name="Prop",
        class_id=class_id,
        alignment=alignment,
        ruleset=Ruleset(),
        stream=streams.get("character_creation"),
        starting_spell_ids=["magic_missile"] if class_id == "magic_user" else (),
        purchases=[("torch", 1)],
    )
    character = result.character
    assert character.level == 1
    assert character.xp == 0
    assert character.max_hp >= 1
    assert character.race == "human"
    assert 30 <= result.gold_roll.total <= 180
    tables = load_ability_tables()
    expected_hp = max(
        1, result.hit_point_roll.rolls[-1] + tables.hit_point_modifier(character.scores[AbilityScore.CON])
    )
    assert character.max_hp == expected_hp
