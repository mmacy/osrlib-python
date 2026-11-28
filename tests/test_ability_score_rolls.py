"""Tests that `AbilityScoreRolls` refuses dice that don't match its scores (#351).

The field docstrings say that `rolls` has the three d6 that count for each of the six abilities and that they sum to
the score, and that `dropped`, when it isn't empty, has the one die of each ability's four that doesn't count: the
lowest, or the first of the lowest thrown when two or more tie. Every result of `roll_ability_scores` meets this under
both methods.
"""

import itertools
import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from osrlib.core.abilities import AbilityScore
from osrlib.core.character import (
    ABILITY_ROLL_ORDER,
    CHARACTER_CREATION_STREAM,
    AbilityScoreRolls,
    roll_ability_scores,
)
from osrlib.core.rng import RngStreams

STR, INT, WIS, DEX, CON, CHA = ABILITY_ROLL_ORDER


def document(
    rolls: dict[AbilityScore, tuple[int, int, int]], dropped: dict[AbilityScore, int] | None = None
) -> dict[str, object]:
    """A roll document whose scores are the sums of `rolls`."""
    return {
        "scores": {ability.value: sum(dice) for ability, dice in rolls.items()},
        "rolls": {ability.value: list(dice) for ability, dice in rolls.items()},
        "dropped": {ability.value: die for ability, die in (dropped or {}).items()},
    }


THREE_D6 = {STR: (3, 4, 5), INT: (1, 1, 1), WIS: (6, 6, 6), DEX: (2, 5, 3), CON: (4, 4, 1), CHA: (6, 1, 2)}
DROPPED = {STR: 2, INT: 1, WIS: 6, DEX: 1, CON: 1, CHA: 1}


@settings(max_examples=200, deadline=None)
@given(seed=st.integers(min_value=0, max_value=2**32 - 1), method=st.sampled_from(["3d6", "4d6_drop_lowest"]))
def test_every_roll_of_roll_ability_scores_validates(seed, method):
    rolled = roll_ability_scores(RngStreams(master_seed=seed).get(CHARACTER_CREATION_STREAM), method=method)

    assert AbilityScoreRolls.model_validate(rolled.model_dump()) == rolled
    assert AbilityScoreRolls.model_validate_json(rolled.model_dump_json()) == rolled


def test_every_four_dice_throw_validates_with_the_first_lowest_dropped():
    # `roll_ability_scores` pops the first of the lowest dice thrown. Every one of the 1296 throws of 4d6, ties
    # included, gives counted dice and a dropped die that the model accepts.
    for thrown in itertools.product(range(1, 7), repeat=4):
        counted = list(thrown)
        dropped = counted.pop(counted.index(min(counted)))
        rolls = {ability: (counted[0], counted[1], counted[2]) for ability in ABILITY_ROLL_ORDER}
        AbilityScoreRolls.model_validate(document(rolls, dict.fromkeys(ABILITY_ROLL_ORDER, dropped)))


def test_consistent_documents_parse():
    assert AbilityScoreRolls.model_validate(document(THREE_D6)).dropped == {}
    assert AbilityScoreRolls.model_validate(document(THREE_D6, DROPPED)).dropped == DROPPED


def test_the_issue_repro_is_refused():
    scores = {ability: 10 for ability in ("str", "int", "wis", "dex", "con", "cha")}

    with pytest.raises(ValidationError, match="rolls must include all six abilities"):
        AbilityScoreRolls.model_validate({"scores": scores, "rolls": {"str": [1, 1, 1]}})


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda doc: doc["rolls"].pop("cha"), "rolls must include all six abilities; missing \\['cha'\\]"),
        (lambda doc: doc["rolls"].update(str=[3, 4, 6]), "rolls for str sum to 13, not to its score of 12"),
        (lambda doc: doc["rolls"].update(str=[0, 6, 6]), "rolls for str must be d6 results from 1 to 6"),
        (lambda doc: doc["rolls"].update(wis=[7, 6, 5]), "rolls for wis must be d6 results from 1 to 6"),
        (lambda doc: doc["dropped"].pop("int"), "dropped must include all six abilities or none; missing \\['int'\\]"),
        (lambda doc: doc["dropped"].update(str=4), "dropped die for str must be from 1 to 3"),
        (lambda doc: doc["dropped"].update(dex=0), "dropped die for dex must be from 1 to 2"),
    ],
    ids=[
        "an ability missing from rolls",
        "dice that don't sum to the score",
        "a die of 0",
        "a die of 7",
        "an ability missing from dropped",
        "a dropped die above the lowest counted",
        "a dropped die of 0",
    ],
)
def test_inconsistent_documents_are_refused(change, message):
    doc = json.loads(json.dumps(document(THREE_D6, DROPPED)))
    change(doc)

    with pytest.raises(ValidationError, match=message):
        AbilityScoreRolls.model_validate(doc)
