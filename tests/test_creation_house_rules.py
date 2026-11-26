"""Acceptance tests for the two house rules of character creation (#341).

`Ruleset.ability_score_method` picks 3d6 or 4d6 drop lowest for the ability scores, and
`Ruleset.max_hp_at_first_level` counts the first-level hit die as its maximum. Both are off by
default. The tests marked `chunk: creation-house-rules` wait for the implementation. The unmarked
ones hold the contract that the default rules draw and give exactly what osrlib 2.8.0 drew and gave,
and that what 2.8.0 wrote still loads. Their literal values were computed by running the 2.8.0
source from `git archive v2.8.0` on the creation stream of each seed.

Every die on the creation stream is one `randbelow(sides) + 1` draw, so a replayed stream gives the
dice each test derives its numbers from.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from osrlib.core.abilities import AbilityScore
from osrlib.core.alignment import Alignment
from osrlib.core.character import (
    ABILITY_ROLL_ORDER,
    CHARACTER_CREATION_STREAM,
    CharacterCreationResult,
    create_character,
    roll_ability_scores,
    roll_hit_points,
)
from osrlib.core.rng import RngStream, RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.data import load_classes
from osrlib.persistence import load_game

CHUNK = "chunk: creation-house-rules"

SAVES = Path(__file__).parent / "saves"
SAVE_2_8_0 = SAVES / "creation_house_rules_save_2.8.0.json"
"""A save that osrlib 2.8.0 wrote, kept as written.

Its party is one thief, Wren, created from seed 7 with `hp_reroll_at_first_level` on: rolls (2, 3)
and 3 hit points. Its ruleset has that one flag on.
"""
RESULT_2_8_0 = SAVES / "creation_house_rules_result_2.8.0.json"
"""The `CharacterCreationResult` behind Wren, as 2.8.0 dumped it with `model_dump(mode="json")`."""

REPO = Path(__file__).resolve().parent.parent
CHANGELOG = REPO / "CHANGELOG.md"
EXAMPLE = REPO / "examples" / "rulesets" / "creation_house_rules.json"
"""The example ruleset document: the SRD ruleset with both creation house rules on."""

STR, INT, WIS, DEX, CON, CHA = ABILITY_ROLL_ORDER


def stream(seed: int) -> RngStream:
    return RngStreams(master_seed=seed).get(CHARACTER_CREATION_STREAM)


def d6_draws(seed: int, count: int) -> tuple[RngStream, list[int]]:
    """Replay `count` d6 draws from the seed's creation stream, and return the stream after them."""
    replay = stream(seed)
    return replay, [replay.randbelow(6) + 1 for _ in range(count)]


def by_ability(values: list) -> dict[AbilityScore, object]:
    return dict(zip(ABILITY_ROLL_ORDER, values, strict=True))


def create(seed: int, class_id: str, ruleset: Ruleset) -> tuple[CharacterCreationResult, RngStream]:
    creation = stream(seed)
    result = create_character(
        name="Wren", class_id=class_id, alignment=Alignment.NEUTRAL, ruleset=ruleset, stream=creation
    )
    return result, creation


# ---------------------------------------------------------------------- 3d6 is what 2.8.0 drew

# seed -> (scores, dice, the stream's next_uint64 after the roll), from the 2.8.0 source.
ABILITY_ROLLS_2_8_0 = {
    1: (
        [12, 13, 12, 8, 12, 8],
        [(4, 6, 2), (3, 5, 5), (3, 6, 3), (1, 5, 2), (2, 6, 4), (1, 3, 4)],
        10764926779193130477,
    ),
    2: (
        [10, 9, 10, 15, 13, 9],
        [(5, 1, 4), (1, 6, 2), (2, 6, 2), (4, 5, 6), (5, 4, 4), (4, 2, 3)],
        14761271966634835755,
    ),
    7: (
        [14, 6, 9, 10, 11, 11],
        [(2, 6, 6), (4, 1, 1), (3, 5, 1), (2, 2, 6), (3, 6, 2), (3, 6, 2)],
        5227041208467167983,
    ),
    11: (
        [7, 11, 9, 8, 17, 14],
        [(3, 1, 3), (4, 2, 5), (2, 4, 3), (1, 2, 5), (6, 5, 6), (6, 6, 2)],
        5439222053352104398,
    ),
    42: (
        [10, 12, 15, 15, 11, 11],
        [(2, 4, 4), (6, 4, 2), (5, 5, 5), (5, 5, 5), (3, 3, 5), (5, 1, 5)],
        9990027807474700127,
    ),
    2026: (
        [9, 10, 14, 10, 9, 3],
        [(3, 1, 5), (2, 4, 4), (4, 5, 5), (5, 3, 2), (6, 2, 1), (1, 1, 1)],
        12013198524635192933,
    ),
}


@pytest.mark.parametrize("seed", sorted(ABILITY_ROLLS_2_8_0))
def test_3d6_ability_rolls_match_2_8_0(seed):
    scores, dice, next_draw = ABILITY_ROLLS_2_8_0[seed]
    creation = stream(seed)

    rolled = roll_ability_scores(creation)

    assert rolled.scores == by_ability(scores)
    assert rolled.rolls == by_ability(dice)
    assert creation.next_uint64() == next_draw


@pytest.mark.parametrize("seed", sorted(ABILITY_ROLLS_2_8_0))
def test_the_3d6_method_named_matches_2_8_0_and_drops_nothing(seed):
    scores, dice, next_draw = ABILITY_ROLLS_2_8_0[seed]
    creation = stream(seed)

    rolled = roll_ability_scores(creation, method="3d6")

    assert rolled.scores == by_ability(scores)
    assert rolled.rolls == by_ability(dice)
    assert rolled.dropped == {}
    assert creation.next_uint64() == next_draw


# (seed, class, hp_reroll_at_first_level) -> (scores, hit dice, hit points, gold dice, gold, next_uint64),
# from the 2.8.0 source.
CREATED_2_8_0 = {
    (2, "fighter", False): ([10, 9, 10, 15, 13, 9], (7,), 8, (2, 1, 3), 60, 17037473347644245223),
    (7, "thief", True): ([14, 6, 9, 10, 11, 11], (2, 3), 3, (3, 2, 1), 60, 15955583499778908635),
    (42, "cleric", False): ([10, 12, 15, 15, 11, 11], (5,), 5, (6, 2, 6), 140, 11020725460617047001),
}


@pytest.mark.parametrize("case", sorted(CREATED_2_8_0))
def test_3d6_create_character_matches_2_8_0(case):
    seed, class_id, reroll = case
    scores, hit_dice, hit_points, gold_dice, gold, next_draw = CREATED_2_8_0[case]

    result, creation = create(seed, class_id, Ruleset(hp_reroll_at_first_level=reroll))

    assert result.ability_rolls.scores == by_ability(scores)
    assert result.ability_rolls.rolls == by_ability(ABILITY_ROLLS_2_8_0[seed][1])
    assert result.hit_point_roll.rolls == hit_dice
    assert result.hit_point_roll.hit_points == hit_points
    assert result.character.max_hp == hit_points
    assert result.gold_roll.rolls == gold_dice
    assert result.gold_roll.total == gold
    assert creation.next_uint64() == next_draw


@pytest.mark.parametrize("case", sorted(CREATED_2_8_0))
def test_the_default_options_named_match_2_8_0(case):
    seed, class_id, reroll = case
    scores, hit_dice, hit_points, gold_dice, gold, next_draw = CREATED_2_8_0[case]
    ruleset = Ruleset(hp_reroll_at_first_level=reroll, ability_score_method="3d6", max_hp_at_first_level=False)

    result, creation = create(seed, class_id, ruleset)

    assert result.ability_rolls.scores == by_ability(scores)
    assert result.ability_rolls.dropped == {}
    assert result.hit_point_roll.rolls == hit_dice
    assert result.hit_point_roll.hit_points == hit_points
    assert result.hit_point_roll.maximized is False
    assert result.gold_roll.rolls == gold_dice
    assert creation.next_uint64() == next_draw


def test_both_options_are_off_by_default():
    rules = Ruleset()
    assert rules.ability_score_method == "3d6"
    assert rules.max_hp_at_first_level is False


def test_a_ruleset_refuses_an_unknown_ability_score_method():
    with pytest.raises(ValidationError):
        Ruleset(ability_score_method="2d6")  # type: ignore[arg-type]


# ---------------------------------------------------------------------- what 2.8.0 wrote still loads


def test_a_2_8_0_save_loads_with_both_options_off():
    document = json.loads(SAVE_2_8_0.read_text(encoding="utf-8"))
    assert document["engine_version"] == "2.8.0"
    assert "ability_score_method" not in document["payload"]["ruleset"]
    assert "max_hp_at_first_level" not in document["payload"]["ruleset"]

    session = load_game(document)

    assert session.ruleset == Ruleset(hp_reroll_at_first_level=True)
    assert session.ruleset.ability_score_method == "3d6"
    assert session.ruleset.max_hp_at_first_level is False
    (wren,) = session.party.members
    assert (wren.name, wren.max_hp) == ("Wren", 3)


def test_a_2_8_0_creation_result_parses_with_the_new_fields_at_their_defaults():
    document = json.loads(RESULT_2_8_0.read_text(encoding="utf-8"))
    assert "dropped" not in document["ability_rolls"]
    assert "maximized" not in document["hit_point_roll"]

    result = CharacterCreationResult.model_validate(document)

    assert result.ability_rolls.dropped == {}
    assert result.ability_rolls.rolls[INT] == (4, 1, 1)
    assert result.hit_point_roll.maximized is False
    assert result.hit_point_roll.rolls == (2, 3)
    assert result.hit_point_roll.hit_points == 3


# ---------------------------------------------------------------------- 4d6 drop lowest

# seed -> (counted dice, dropped die, score) per ability, in STR, INT, WIS, DEX, CON, CHA order.
#
# Each ability takes the next four d6 draws. The lowest die is dropped, and when two or more tie for
# lowest, the first of them thrown is the one dropped. The other three keep their throw order.
FOUR_D6 = {
    # Draws: 5 1 4 1 | 6 2 2 6 | 2 4 5 6 | 5 4 4 4 | 2 3 2 1 | 3 2 6 5
    2: [
        ((5, 4, 1), 1, 10),  # 1s tie at throws 2 and 4: the first is dropped, so (5, 4, 1), never (5, 1, 4).
        ((6, 2, 6), 2, 14),  # 2s tie at throws 2 and 3: dropping the first leaves (6, 2, 6).
        ((4, 5, 6), 2, 15),
        ((5, 4, 4), 4, 13),  # Three 4s tie: the first, at throw 2, is dropped.
        ((2, 3, 2), 1, 7),
        ((3, 6, 5), 2, 14),
    ],
    # Draws: 2 6 6 4 | 1 1 3 5 | 1 2 2 6 | 3 6 2 3 | 6 2 3 5 | 3 2 1 5
    7: [
        ((6, 6, 4), 2, 16),
        ((1, 3, 5), 1, 9),
        ((2, 2, 6), 1, 10),
        ((3, 6, 3), 2, 12),
        ((6, 3, 5), 2, 14),
        ((3, 2, 5), 1, 10),
    ],
    # Draws: 2 4 4 6 | 4 2 5 5 | 5 5 5 5 | 3 3 5 5 | 1 5 5 6 | 2 6 5 2
    42: [
        ((4, 4, 6), 2, 14),
        ((4, 5, 5), 2, 14),
        ((5, 5, 5), 5, 15),  # Four 5s: one 5 is dropped and three count.
        ((3, 5, 5), 3, 13),
        ((5, 5, 6), 1, 16),
        ((6, 5, 2), 2, 13),  # 2s tie at throws 1 and 4: the first is dropped, so (6, 5, 2), never (2, 6, 5).
    ],
}


@pytest.mark.xfail(reason=CHUNK)
@pytest.mark.parametrize("seed", sorted(FOUR_D6))
def test_4d6_drop_lowest_counts_three_dice_and_names_the_dropped_one(seed):
    expected = FOUR_D6[seed]

    rolled = roll_ability_scores(stream(seed), method="4d6_drop_lowest")

    assert rolled.rolls == by_ability([counted for counted, _, _ in expected])
    assert rolled.dropped == by_ability([dropped for _, dropped, _ in expected])
    assert rolled.scores == by_ability([score for _, _, score in expected])
    for ability in ABILITY_ROLL_ORDER:
        assert sum(rolled.rolls[ability]) == rolled.scores[ability]
        assert rolled.dropped[ability] <= min(rolled.rolls[ability])


@pytest.mark.xfail(reason=CHUNK)
@pytest.mark.parametrize("seed", sorted(FOUR_D6))
def test_4d6_drop_lowest_throws_the_draws_in_order_and_advances_24_dice(seed):
    creation = stream(seed)

    rolled = roll_ability_scores(creation, method="4d6_drop_lowest")

    replay, draws = d6_draws(seed, 24)
    for index, ability in enumerate(ABILITY_ROLL_ORDER):
        thrown = draws[4 * index : 4 * index + 4]
        assert sorted((*rolled.rolls[ability], rolled.dropped[ability])) == sorted(thrown)
    assert creation.export_state() == replay.export_state()


@pytest.mark.xfail(reason=CHUNK)
def test_4d6_drop_lowest_scores_range_from_3_to_18():
    for seed in range(60):
        rolled = roll_ability_scores(stream(seed), method="4d6_drop_lowest")
        assert set(rolled.dropped) == set(AbilityScore)
        assert all(3 <= score <= 18 for score in rolled.scores.values())


@pytest.mark.xfail(reason=CHUNK)
def test_an_unknown_method_raises_before_the_first_draw():
    creation = stream(7)
    before = creation.export_state()

    with pytest.raises(ValueError, match="2d6"):
        roll_ability_scores(creation, method="2d6")  # type: ignore[arg-type]

    assert creation.export_state() == before


@pytest.mark.xfail(reason=CHUNK)
def test_create_character_rolls_with_the_rulesets_method():
    # Seed 2's 4d6 scores, from FOUR_D6, are STR 10, INT 14, WIS 15, DEX 13, CON 7, and CHA 14. After
    # the 24 ability dice, the fighter's d8 hit die is draw 25 and shows 6. CON 7 has a -1 hit
    # point modifier, so the fighter has 6 - 1 = 5 hit points. The gold dice are draws 26 to 28: 4 2 3,
    # 9 × 10 = 90 gold pieces.
    result, creation = create(2, "fighter", Ruleset(ability_score_method="4d6_drop_lowest"))

    expected = roll_ability_scores(stream(2), method="4d6_drop_lowest")
    assert result.ability_rolls == expected
    assert result.ability_rolls.scores == by_ability([10, 14, 15, 13, 7, 14])
    assert result.character.scores == expected.scores
    assert result.hit_point_roll.rolls == (6,)
    assert result.hit_point_roll.hit_points == 5
    assert result.hit_point_roll.maximized is False
    assert result.gold_roll.rolls == (4, 2, 3)
    assert result.gold_roll.total == 90

    replay, _ = d6_draws(2, 24)
    assert replay.randbelow(8) + 1 == 6
    assert [replay.randbelow(6) + 1 for _ in range(3)] == [4, 2, 3]
    assert creation.export_state() == replay.export_state()


# ---------------------------------------------------------------------- maximum hit points at first level

# Seed 7's thief: the 3d6 scores give CON 11, a hit point modifier of 0. The d4 hit die is draw 19
# and shows 2. With `hp_reroll_at_first_level` on, the 2 is set aside and draw 20 shows 3. The gold
# dice follow the hit dice: 5 3 2 (100 gp) without the reroll, 3 2 1 (60 gp) with it.
THIEF_HIT_DICE = {False: (2,), True: (2, 3)}
THIEF_GOLD = {False: ((5, 3, 2), 100), True: ((3, 2, 1), 60)}


@pytest.mark.xfail(reason=CHUNK)
@pytest.mark.parametrize("reroll", [False, True])
def test_the_maximum_draws_what_the_roll_draws_so_the_gold_is_the_same(reroll):
    rolled, rolled_stream = create(7, "thief", Ruleset(hp_reroll_at_first_level=reroll))
    maximum, maximum_stream = create(7, "thief", Ruleset(hp_reroll_at_first_level=reroll, max_hp_at_first_level=True))

    assert rolled.hit_point_roll.rolls == maximum.hit_point_roll.rolls == THIEF_HIT_DICE[reroll]
    assert rolled.gold_roll == maximum.gold_roll
    assert (maximum.gold_roll.rolls, maximum.gold_roll.total) == THIEF_GOLD[reroll]
    assert rolled_stream.export_state() == maximum_stream.export_state()
    assert rolled.ability_rolls == maximum.ability_rolls

    # The thief's d4 at its maximum, plus CON 11's modifier of 0.
    assert maximum.hit_point_roll.hit_points == 4
    assert maximum.character.max_hp == maximum.character.current_hp == 4
    assert maximum.hit_point_roll.maximized is True
    assert rolled.hit_point_roll.hit_points == THIEF_HIT_DICE[reroll][-1]
    assert rolled.hit_point_roll.maximized is False


@pytest.mark.xfail(reason=CHUNK)
def test_with_the_maximum_and_the_reroll_on_the_set_aside_dice_are_still_listed():
    # The reroll still throws: the 2 is set aside and the 3 is the last die. The maximum replaces the
    # last die's value in the hit points only, so they're the d4's 4 plus a modifier of 0.
    result, _ = create(7, "thief", Ruleset(hp_reroll_at_first_level=True, max_hp_at_first_level=True))

    assert result.hit_point_roll.rolls == (2, 3)
    assert result.hit_point_roll.maximized is True
    assert result.hit_point_roll.hit_points == 4


# The first-level hit die of each built-in class, from the Hit Dice row of its SRD page:
# cleric, elf, and halfling 1d6, dwarf and fighter 1d8, magic-user and thief 1d4.
HIT_DIE = {"cleric": 6, "dwarf": 8, "elf": 6, "fighter": 8, "halfling": 6, "magic_user": 4, "thief": 4}


@pytest.mark.xfail(reason=CHUNK)
@pytest.mark.parametrize("class_id", sorted(HIT_DIE))
@pytest.mark.parametrize("con_modifier", [-5, -4, -3, -1, 0, 1, 3])
def test_the_maximum_is_the_die_size_plus_the_modifier_and_at_least_1(class_id, con_modifier):
    # A d4 with -4 or -5 would be 0 or -1, so the floor of 1 holds those to 1. A CON of 3 gives -3,
    # and a d4's 4 - 3 is already 1.
    definition = load_classes().get(class_id)
    assert definition.row(1).hit_dice.die == HIT_DIE[class_id]
    for seed in (2, 7, 42):
        maximum_stream, rolled_stream = stream(seed), stream(seed)

        maximum = roll_hit_points(definition, con_modifier, Ruleset(max_hp_at_first_level=True), maximum_stream)
        rolled = roll_hit_points(definition, con_modifier, Ruleset(), rolled_stream)

        assert maximum.hit_points == max(1, HIT_DIE[class_id] + con_modifier)
        assert maximum.maximized is True
        assert maximum.rolls == rolled.rolls
        assert len(maximum.rolls) == 1
        assert maximum_stream.export_state() == rolled_stream.export_state()


# ---------------------------------------------------------------------- both options together


@pytest.mark.xfail(reason=CHUNK)
def test_both_options_in_one_create_character_call():
    # The scores and the dice are seed 2's 4d6 drop lowest from FOUR_D6. CON 7 gives -1. The fighter's
    # d8 is draw 25, a 6, and the maximum counts it as 8, so the fighter has 8 - 1 = 7 hit points. The
    # gold dice are draws 26 to 28, as with the maximum off: 4 2 3, 90 gp.
    ruleset = Ruleset(ability_score_method="4d6_drop_lowest", max_hp_at_first_level=True)

    result, creation = create(2, "fighter", ruleset)

    assert result.ability_rolls.rolls == by_ability([counted for counted, _, _ in FOUR_D6[2]])
    assert result.ability_rolls.dropped == by_ability([dropped for _, dropped, _ in FOUR_D6[2]])
    assert result.character.scores == by_ability([10, 14, 15, 13, 7, 14])
    assert result.hit_point_roll.rolls == (6,)
    assert result.hit_point_roll.maximized is True
    assert result.hit_point_roll.hit_points == 7
    assert result.character.max_hp == 7
    assert (result.gold_roll.rolls, result.gold_roll.total) == ((4, 2, 3), 90)
    assert result.character.inventory.purse.gp == 90

    replay, _ = d6_draws(2, 24)
    replay.randbelow(8)
    for _ in range(3):
        replay.randbelow(6)
    assert creation.export_state() == replay.export_state()


# ---------------------------------------------------------------------- the example ruleset

HOUSE_RULES = Ruleset(ability_score_method="4d6_drop_lowest", max_hp_at_first_level=True)


def test_the_example_ruleset_loads_as_the_two_house_rules_on():
    rules = Ruleset.model_validate_json(EXAMPLE.read_text(encoding="utf-8"))

    assert rules == HOUSE_RULES
    changed = {name for name in Ruleset.model_fields if getattr(rules, name) != getattr(Ruleset(), name)}
    assert changed == {"ability_score_method", "max_hp_at_first_level"}


def test_the_example_ruleset_writes_out_every_field():
    # A field added to `Ruleset` later fails this test until the example names it too.
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    assert set(document) == set(Ruleset.model_fields)
    assert document == HOUSE_RULES.model_dump(mode="json")


def test_the_guide_includes_the_example_file_itself():
    # pymdownx.snippets puts the file into the page at build time, and its `check_paths` fails the
    # strict build if the file is missing, so the page shows the file as it is.
    guide = (REPO / "docs" / "guides" / "ruleset-options.md").read_text(encoding="utf-8")

    assert '```json\n--8<-- "examples/rulesets/creation_house_rules.json"\n```' in guide


def test_the_example_readme_says_what_the_ruleset_changes_and_how_to_load_it():
    readme = (EXAMPLE.parent / "README.md").read_text(encoding="utf-8")

    for text in ("creation_house_rules.json", "ability_score_method", "max_hp_at_first_level", "model_validate_json"):
        assert text in readme
    assert "not the SRD" in readme


@pytest.mark.xfail(reason=CHUNK)
def test_a_character_created_with_the_example_ruleset_has_4d6_scores_and_the_maximum():
    # The same draws as test_both_options_in_one_create_character_call: seed 2's 4d6 drop lowest
    # scores, and the fighter's d8 at its maximum, 8, with CON 7's -1, for 7 hit points.
    rules = Ruleset.model_validate_json(EXAMPLE.read_text(encoding="utf-8"))

    result, _ = create(2, "fighter", rules)

    assert result.ability_rolls.dropped == by_ability([dropped for _, dropped, _ in FOUR_D6[2]])
    assert result.character.scores == by_ability([score for _, _, score in FOUR_D6[2]])
    assert result.hit_point_roll.maximized is True
    assert result.character.max_hp == 7


# ---------------------------------------------------------------------- the changelog


@pytest.mark.xfail(reason=CHUNK)
def test_the_changelog_bullet_names_both_options_and_both_fields():
    bullets = [line for line in CHANGELOG.read_text(encoding="utf-8").splitlines() if "(#341)" in line]
    assert len(bullets) == 1
    (bullet,) = bullets
    assert bullet.startswith("- ")
    for name in ("ability_score_method", "4d6_drop_lowest", "max_hp_at_first_level", "dropped", "maximized"):
        assert name in bullet
