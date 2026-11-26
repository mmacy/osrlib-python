"""Acceptance tests for the SRD's sub-par characters rule (#345).

`srd/Creating_a_Character.md`, "Sub-Par Characters": "If you roll a character with very poor ability scores—for
example an 8 or less in every score or an extremely low rating in more than one ability—the referee may allow you to
discard the character and start again."

osrlib reads it as two tests. The scores are sub-par when every score is 8 or less, the SRD's own example, or when two
or more scores are 5 or less, osrlib's threshold for "an extremely low rating". Every ability table on the SRD's
Ability Scores page has a row boundary between 6 and 5. `scores_are_sub_par` answers for six scores, and
`AbilityScoreRolls.sub_par` answers for a roll, computed from its `scores`, the scores as rolled before the adjustment
step. osrlib never discards or rerolls a character, so no draw changes.

The tests marked `chunk: sub-par-scores` wait for the implementation. The unmarked ones hold what already holds: the
draws and the characters that `create_character` gives for the seeds below, the schemas, and the docstrings. Every
literal score and draw was computed by running the source from `git archive v2.8.0`, which gives the same values as
the source this chunk starts from, on the creation stream of each seed. The 4d6 drop lowest values, which 2.8.0 can't
roll, were computed on the source this chunk starts from.
"""

import json
import re
from itertools import combinations
from pathlib import Path

import pytest
from pydantic import ValidationError

import osrlib.core.character as character_module
from osrlib.core.abilities import AbilityAdjustment, AbilityScore
from osrlib.core.alignment import Alignment
from osrlib.core.character import (
    ABILITY_ROLL_ORDER,
    CHARACTER_CREATION_STREAM,
    AbilityScoreRolls,
    CharacterCreationResult,
    create_character,
    roll_ability_scores,
    scores_are_sub_par,
)
from osrlib.core.rng import RngStream, RngStreams
from osrlib.core.ruleset import Ruleset

REPO = Path(__file__).resolve().parent.parent
SAVES = Path(__file__).parent / "saves"
CHANGELOG = REPO / "CHANGELOG.md"

HOUSE_RULES_RESULT_2_8_0 = SAVES / "creation_house_rules_result_2.8.0.json"
"""The `CharacterCreationResult` of the thief Wren, seed 7 with `hp_reroll_at_first_level` on, as 2.8.0 dumped it.

Its scores are STR 14, INT 6, WIS 9, DEX 10, CON 11, CHA 11: one score of 8 or less that's above 5, so not sub-par.
"""
SUB_PAR_RESULT_2_8_0 = SAVES / "sub_par_scores_result_2.8.0.json"
"""The `CharacterCreationResult` of the fighter Grub, seed 6 with `Ruleset()`, as 2.8.0 dumped it.

2.8.0 wrote it with `json.dumps(result.model_dump(mode="json"), indent=1, sort_keys=True)`. Its scores are STR 4,
INT 5, WIS 12, DEX 12, CON 8, CHA 15: two scores of 5 or less, so sub-par.
"""

STR, INT, WIS, DEX, CON, CHA = ABILITY_ROLL_ORDER


def stream(seed: int) -> RngStream:
    return RngStreams(master_seed=seed).get(CHARACTER_CREATION_STREAM)


def by_ability(values: list[int]) -> dict[AbilityScore, int]:
    return dict(zip(ABILITY_ROLL_ORDER, values, strict=True))


def the_rule(scores: dict[AbilityScore, int]) -> bool:
    """The rule as the tests state it: every score 8 or less, or two or more scores 5 or less."""
    values = [scores[ability] for ability in ABILITY_ROLL_ORDER]
    return all(value <= 8 for value in values) or sum(value <= 5 for value in values) >= 2


# ---------------------------------------------------------------------- the two clauses and their boundaries

# Scores in STR, INT, WIS, DEX, CON, CHA order, and the answer.
BOUNDARIES = {
    "all six at 8": ([8, 8, 8, 8, 8, 8], True),
    "all six at 3": ([3, 3, 3, 3, 3, 3], True),
    "five at 8 and one at 9": ([8, 8, 8, 9, 8, 8], False),
    "two at 5 and the rest at 18": ([5, 18, 18, 18, 5, 18], True),
    "two at 3 and the rest at 18": ([18, 3, 18, 3, 18, 18], True),
    "one at 5 and one at 6": ([5, 18, 18, 6, 18, 18], False),
    "one at 3 alone": ([3, 18, 18, 18, 18, 18], False),
    "two at 6 and the rest at 18": ([6, 18, 18, 18, 6, 18], False),
    # Seed 3835's 3d6 roll: every score is 8 or less, and STR and INT are 5.
    "both clauses": ([5, 5, 8, 8, 6, 7], True),
    # Every score is 8 or less, and none is 5 or less: the first clause alone.
    "the first clause alone": ([6, 6, 7, 7, 8, 8], True),
    # One score at 9 breaks the first clause, and two at 5 still meet the second.
    "the second clause with a 9": ([5, 5, 8, 9, 8, 8], True),
}


@pytest.mark.parametrize("case", sorted(BOUNDARIES))
def test_each_clause_at_its_boundary(case):
    values, expected = BOUNDARIES[case]
    assert the_rule(by_ability(values)) is expected

    assert scores_are_sub_par(by_ability(values)) is expected


@pytest.mark.parametrize("ability", ABILITY_ROLL_ORDER)
def test_a_single_9_in_any_ability_breaks_the_first_clause(ability):
    scores = dict.fromkeys(ABILITY_ROLL_ORDER, 8)
    assert scores_are_sub_par(scores) is True

    scores[ability] = 9

    assert scores_are_sub_par(scores) is False


@pytest.mark.parametrize("pair", list(combinations(ABILITY_ROLL_ORDER, 2)), ids=lambda pair: "-".join(pair))
def test_any_two_abilities_at_5_meet_the_second_clause(pair):
    # The 15 pairs of the six abilities. Each one alone at 5 is not enough, and the two together are.
    first, second = pair
    scores = dict.fromkeys(ABILITY_ROLL_ORDER, 18)
    scores[first] = 5
    assert scores_are_sub_par(scores) is False

    scores[second] = 5

    assert scores_are_sub_par(scores) is True


def test_the_function_draws_nothing_and_leaves_its_argument_alone():
    scores = by_ability([4, 5, 12, 12, 8, 15])
    before = dict(scores)
    creation = stream(6)
    state = creation.export_state()

    assert scores_are_sub_par(scores) is True

    assert scores == before
    assert creation.export_state() == state


def test_the_function_reads_a_characters_scores():
    # Seed 6's fighter keeps its rolled scores: STR 4 and INT 5.
    result = create_character(
        name="Grub", class_id="fighter", alignment=Alignment.CHAOTIC, ruleset=Ruleset(), stream=stream(6)
    )

    assert scores_are_sub_par(result.character.scores) is True


@pytest.mark.parametrize(
    "scores",
    [
        {ability: 8 for ability in ABILITY_ROLL_ORDER if ability is not CHA},
        {**dict.fromkeys(ABILITY_ROLL_ORDER, 8), CON: 2},
        {**dict.fromkeys(ABILITY_ROLL_ORDER, 12), STR: 19},
        {},
    ],
    ids=["missing CHA", "a score of 2", "a score of 19", "empty"],
)
def test_the_function_refuses_scores_that_are_not_six_from_3_to_18(scores):
    with pytest.raises(ValueError):
        scores_are_sub_par(scores)


# ---------------------------------------------------------------------- the field on a roll

# seed -> (method, scores in STR, INT, WIS, DEX, CON, CHA order, sub-par), from the creation stream.
NAMED_ROLLS = {
    # 3d6 dice (2, 1, 1) (1, 1, 3) (5, 5, 2) (5, 5, 2) (2, 5, 1) (6, 3, 6): STR 4 and INT 5, the second clause.
    (6, "3d6"): ([4, 5, 12, 12, 8, 15], True),
    # 3d6 dice (3, 1, 2) (2, 2, 2) (1, 5, 1) (2, 4, 1) (5, 2, 1) (2, 5, 1): all six 8 or less, the first clause alone.
    (1862, "3d6"): ([6, 6, 7, 7, 8, 8], True),
    # 3d6 dice (1, 2, 2) (1, 1, 3) (3, 1, 4) (4, 2, 2) (3, 2, 1) (3, 2, 2): both clauses.
    (3835, "3d6"): ([5, 5, 8, 8, 6, 7], True),
    # 3d6 dice (4, 5, 6) (1, 5, 6) (2, 1, 1) (5, 5, 2) (2, 3, 2) (5, 6, 4): WIS 4 is the only score of 5 or less.
    (3, "3d6"): ([15, 12, 4, 12, 7, 15], False),
    # 3d6 dice (3, 1, 5) (2, 4, 4) (4, 5, 5) (5, 3, 2) (6, 2, 1) (1, 1, 1): CHA 3 alone.
    (2026, "3d6"): ([9, 10, 14, 10, 9, 3], False),
    # 3d6 dice (2, 6, 6) (4, 1, 1) (3, 5, 1) (2, 2, 6) (3, 6, 2) (3, 6, 2): INT 6 is low, and nothing is 5 or less.
    (7, "3d6"): ([14, 6, 9, 10, 11, 11], False),
    # 4d6 drop lowest, counted dice (1, 1, 2) (5, 6, 5) (6, 6, 6) (4, 6, 5) (2, 3, 5) (1, 1, 3), dropped 1 1 3 3 2 1:
    # STR 4 and CHA 5 from the three dice that count.
    (280, "4d6_drop_lowest"): ([4, 16, 18, 15, 10, 5], True),
    # 4d6 drop lowest, from tests/test_creation_house_rules.py: CON 7 is the lowest score.
    (2, "4d6_drop_lowest"): ([10, 14, 15, 13, 7, 14], False),
}


@pytest.mark.parametrize("case", sorted(NAMED_ROLLS))
def test_the_named_seeds_roll_the_scores_written_here(case):
    seed, method = case
    scores, expected = NAMED_ROLLS[case]

    rolled = roll_ability_scores(stream(seed), method=method)

    assert rolled.scores == by_ability(scores)
    assert the_rule(rolled.scores) is expected


@pytest.mark.parametrize("case", sorted(NAMED_ROLLS))
def test_a_named_roll_says_whether_it_is_sub_par(case):
    seed, method = case
    _, expected = NAMED_ROLLS[case]

    rolled = roll_ability_scores(stream(seed), method=method)

    assert rolled.sub_par is expected
    assert scores_are_sub_par(rolled.scores) is expected


@pytest.mark.parametrize("method", ["3d6", "4d6_drop_lowest"])
def test_the_field_and_the_function_agree_for_every_roll(method):
    answers = []
    for seed in range(400):
        rolled = roll_ability_scores(stream(seed), method=method)
        expected = the_rule(rolled.scores)
        assert rolled.sub_par is expected, seed
        assert scores_are_sub_par(rolled.scores) is expected, seed
        answers.append(expected)
    if method == "3d6":
        # About 1 roll in 35 is sub-par with 3d6, so 400 seeds hold both answers.
        assert True in answers and False in answers


def test_the_field_is_in_the_dump_and_the_json():
    rolled = roll_ability_scores(stream(6))

    assert rolled.model_dump()["sub_par"] is True
    assert rolled.model_dump(mode="json")["sub_par"] is True
    assert json.loads(rolled.model_dump_json())["sub_par"] is True
    not_sub_par = roll_ability_scores(stream(7))
    assert json.loads(not_sub_par.model_dump_json())["sub_par"] is False


def test_a_creation_result_dumps_the_field_inside_ability_rolls():
    result = create_character(
        name="Grub", class_id="fighter", alignment=Alignment.CHAOTIC, ruleset=Ruleset(), stream=stream(6)
    )

    document = json.loads(result.model_dump_json())

    assert document["ability_rolls"]["sub_par"] is True
    assert CharacterCreationResult.model_validate(document) == result


@pytest.mark.parametrize(("seed", "stated"), [(6, False), (7, True)])
def test_a_document_that_states_the_wrong_value_does_not_change_it(seed, stated):
    # Seed 6's scores are sub-par and seed 7's are not, and each document says the opposite.
    rolled = roll_ability_scores(stream(seed))
    document = {**rolled.model_dump(mode="json"), "sub_par": stated}

    parsed = AbilityScoreRolls.model_validate(document)
    from_json = AbilityScoreRolls.model_validate_json(json.dumps(document))

    for model in (parsed, from_json):
        assert model == rolled
        assert model.sub_par is not stated
        assert model.model_dump()["sub_par"] is not stated


def test_a_copy_cannot_set_the_field():
    rolled = roll_ability_scores(stream(6))

    copied = rolled.model_copy(update={"sub_par": False})

    assert copied.sub_par is True
    assert copied.model_dump()["sub_par"] is True
    with pytest.raises(ValidationError):
        rolled.sub_par = False  # type: ignore[misc]


def test_a_copy_with_new_scores_answers_for_them():
    rolled = roll_ability_scores(stream(6))

    raised = rolled.model_copy(update={"scores": {**rolled.scores, INT: 6}})

    # INT 6 leaves STR 4 the only score of 5 or less.
    assert raised.sub_par is False


@pytest.mark.parametrize(
    "scores",
    [
        {ability: 8 for ability in ABILITY_ROLL_ORDER if ability is not CHA},
        {**dict.fromkeys(ABILITY_ROLL_ORDER, 8), CON: 2},
        {**dict.fromkeys(ABILITY_ROLL_ORDER, 12), STR: 19},
    ],
    ids=["missing CHA", "a score of 2", "a score of 19"],
)
def test_a_roll_refuses_scores_that_are_not_six_from_3_to_18(scores):
    # The field docstring of `scores` says all six abilities are present, each from 3 to 18. A roll that held
    # anything else couldn't say whether it's sub-par.
    rolls = {ability: (1, 1, 1) for ability in scores}

    with pytest.raises(ValidationError):
        AbilityScoreRolls(scores=scores, rolls=rolls)


def test_the_serialization_schema_lists_the_field_and_the_validation_schema_does_not():
    # A client reads the fact from the JSON, so the schema it reads the JSON with lists it, read-only. A document
    # sent to osrlib doesn't carry it.
    serialization = AbilityScoreRolls.model_json_schema(mode="serialization")
    validation = AbilityScoreRolls.model_json_schema(mode="validation")

    field = serialization["properties"]["sub_par"]
    assert field["type"] == "boolean"
    assert field["readOnly"] is True
    assert "sub_par" in serialization["required"]
    assert "Sub-Par Characters" in field["description"]
    assert "][osrlib." not in field["description"]
    assert "sub_par" not in validation["properties"]
    assert set(validation["properties"]) == {"scores", "rolls", "dropped"}


# ---------------------------------------------------------------------- what 2.8.0 wrote


def test_a_2_8_0_result_that_is_not_sub_par_parses_with_the_field_false():
    document = json.loads(HOUSE_RULES_RESULT_2_8_0.read_text(encoding="utf-8"))
    assert "sub_par" not in document["ability_rolls"]

    result = CharacterCreationResult.model_validate(document)

    assert result.ability_rolls.scores == by_ability([14, 6, 9, 10, 11, 11])
    assert result.ability_rolls.sub_par is False
    assert json.loads(result.model_dump_json())["ability_rolls"]["sub_par"] is False


def test_a_2_8_0_result_that_is_sub_par_parses_with_the_field_true():
    document = json.loads(SUB_PAR_RESULT_2_8_0.read_text(encoding="utf-8"))
    assert document["character"]["name"] == "Grub"
    assert "sub_par" not in document["ability_rolls"]

    result = CharacterCreationResult.model_validate(document)

    assert result.ability_rolls.scores == by_ability([4, 5, 12, 12, 8, 15])
    assert result.ability_rolls.sub_par is True
    assert json.loads(result.model_dump_json())["ability_rolls"]["sub_par"] is True


def test_the_2_8_0_sub_par_result_is_what_create_character_gives_today():
    # The fixture is what this source creates from the same seed and arguments, so it's a real 2.8.0 result.
    document = json.loads(SUB_PAR_RESULT_2_8_0.read_text(encoding="utf-8"))
    result = create_character(
        name="Grub", class_id="fighter", alignment=Alignment.CHAOTIC, ruleset=Ruleset(), stream=stream(6)
    )

    assert CharacterCreationResult.model_validate(document) == result


# ---------------------------------------------------------------------- create_character neither refuses nor rerolls

# (seed, method) -> (scores as rolled, hit dice, hit points, gold dice, gold, next_uint64 after the call), for a
# fighter, which no score keeps out. Seed 280's d8 shows 1 and CON 10's modifier is 0, so 1 hit point. Seed 6's d8
# shows 3 and CON 8's -1 leaves 2. Seed 3835's d8 shows 5 and CON 6's -1 leaves 4. Seed 1862's d8 shows 8 and CON
# 8's -1 leaves 7.
SUB_PAR_FIGHTERS = {
    (6, "3d6"): ([4, 5, 12, 12, 8, 15], (3,), 2, (4, 2, 6), 120, 988488090159952427),
    (1862, "3d6"): ([6, 6, 7, 7, 8, 8], (8,), 7, (1, 5, 5), 110, 751514679970455309),
    (3835, "3d6"): ([5, 5, 8, 8, 6, 7], (5,), 4, (6, 1, 4), 110, 541578076563961857),
    (280, "4d6_drop_lowest"): ([4, 16, 18, 15, 10, 5], (1,), 1, (4, 2, 5), 110, 11980387446473264426),
}


def fighter(seed: int, method: str) -> tuple[CharacterCreationResult, RngStream]:
    creation = stream(seed)
    result = create_character(
        name="Grub",
        class_id="fighter",
        alignment=Alignment.CHAOTIC,
        ruleset=Ruleset(ability_score_method=method),  # type: ignore[arg-type]
        stream=creation,
    )
    return result, creation


@pytest.mark.parametrize("case", sorted(SUB_PAR_FIGHTERS))
def test_create_character_draws_what_it_drew_before_for_a_sub_par_roll(case):
    scores, hit_dice, hit_points, gold_dice, gold, next_draw = SUB_PAR_FIGHTERS[case]

    result, creation = fighter(*case)

    assert result.ability_rolls.scores == by_ability(scores)
    assert result.character.scores == by_ability(scores)
    assert result.hit_point_roll.rolls == hit_dice
    assert result.character.max_hp == hit_points
    assert (result.gold_roll.rolls, result.gold_roll.total) == (gold_dice, gold)
    assert creation.next_uint64() == next_draw


@pytest.mark.parametrize("case", sorted(SUB_PAR_FIGHTERS))
def test_create_character_creates_a_sub_par_character_and_says_so(case):
    scores, _, hit_points, _, _, next_draw = SUB_PAR_FIGHTERS[case]

    result, creation = fighter(*case)

    assert result.ability_rolls.sub_par is True
    assert result.character.name == "Grub"
    assert result.character.max_hp == hit_points
    assert creation.next_uint64() == next_draw


def test_the_adjustment_does_not_change_the_answer_on_the_roll():
    # Seed 6 rolls STR 4, INT 5, WIS 12, DEX 12, CON 8, CHA 15, which is sub-par. A magic-user lowers WIS by 2 to
    # raise INT, its prime requisite, by 1. The character has STR 4, INT 6, and WIS 10, which alone would not be
    # sub-par, because STR 4 is the only score left at 5 or less. The roll still says sub-par, because the test is on
    # the scores as rolled. The d4 shows 2 and CON 8's -1 leaves 1 hit point, and the gold dice are the fighter's.
    creation = stream(6)

    result = create_character(
        name="Grub",
        class_id="magic_user",
        alignment=Alignment.CHAOTIC,
        ruleset=Ruleset(),
        stream=creation,
        adjustment=AbilityAdjustment(lowered={WIS: 2}, raised={INT: 1}),
        starting_spell_ids=["sleep"],
    )

    assert result.ability_rolls.scores == by_ability([4, 5, 12, 12, 8, 15])
    assert result.character.scores == by_ability([4, 6, 10, 12, 8, 15])
    assert result.ability_rolls.sub_par is True
    assert scores_are_sub_par(result.character.scores) is False
    assert result.hit_point_roll.rolls == (2,)
    assert result.character.max_hp == 1
    assert creation.next_uint64() == 988488090159952427


# ---------------------------------------------------------------------- the documents


def srd_sentence() -> str:
    """The SRD's sub-par sentence, with its link to the referee's page reduced to the word."""
    page = (REPO / "srd" / "Creating_a_Character.md").read_text(encoding="utf-8")
    (line,) = [line for line in page.splitlines() if line.startswith("If you roll a character with very poor")]
    return re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)


def words(text: str) -> str:
    return " ".join(text.split())


@pytest.mark.parametrize(
    "docstring",
    [scores_are_sub_par.__doc__, AbilityScoreRolls.sub_par.__doc__],  # type: ignore[attr-defined]
    ids=["scores_are_sub_par", "AbilityScoreRolls.sub_par"],
)
def test_the_docstrings_quote_the_srd_and_say_there_is_no_flag(docstring):
    assert docstring is not None
    text = words(docstring)
    assert words(srd_sentence()) in text
    assert "has no flag for it" in text
    assert "never refuses or rerolls" in text


def _section(text: str, start: str, end: str) -> str:
    return text[text.index(start) : text.index(end, text.index(start))]


def test_the_adaptations_register_records_the_threshold_as_an_interpretation():
    register = (REPO / "docs" / "adaptations.md").read_text(encoding="utf-8")
    interpretations = _section(register, "\n## Interpretations\n", "\n## Documented adaptations\n")
    entries = [entry for entry in interpretations.split("\n### ")[1:] if "sub-par" in entry.splitlines()[0].lower()]

    assert len(entries) == 1
    (entry,) = entries
    for text in ("scores_are_sub_par", "sub_par", "8 or less", "5 or less", "test_sub_par_scores.py::"):
        assert text in entry


def test_the_creation_walkthrough_mentions_the_check_at_the_ability_roll():
    module_doc = character_module.__doc__
    assert module_doc is not None
    step_one = _section(module_doc, "\n1. ", "\n2. ")

    assert "roll_ability_scores" in step_one
    assert "sub_par" in step_one


def test_the_quickstart_mentions_the_check_where_it_describes_the_roll():
    quickstart = (REPO / "docs" / "getting-started" / "quickstart.md").read_text(encoding="utf-8")
    described = "`create_character` rolls 1st-level ability scores"
    (paragraph,) = [line for line in quickstart.splitlines() if described in line]

    assert "sub_par" in paragraph


def test_the_spec_mentions_the_check_in_the_ability_score_scope():
    spec = (REPO / "docs" / "spec.md").read_text(encoding="utf-8")
    (line,) = [line for line in spec.splitlines() if line.startswith("- Ability scores 3d6")]

    assert "sub-par" in line


def test_the_changelog_bullet_names_the_function_and_the_field():
    bullets = [line for line in CHANGELOG.read_text(encoding="utf-8").splitlines() if "(#345)" in line]
    assert len(bullets) == 1
    (bullet,) = bullets
    assert bullet.startswith("- ")
    for name in ("scores_are_sub_par", "AbilityScoreRolls.sub_par"):
        assert name in bullet
