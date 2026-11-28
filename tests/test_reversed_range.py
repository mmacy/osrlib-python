"""A reverse whose text states its own range is cast at that range (issue #300).

The OSE SRD gives *remove fear* a range of "The caster or a creature touched", and its reverse, *cause fear*,
"Causes a target within 120’ to flee". Up to 2.11.0 the reverse had no range of its own, so the kernel checked
*cause fear* against touch range and a battle declaration refused a goblin 60' away.
"""

import pytest
from pydantic import ValidationError

from osrlib.core.combat import MELEE_REACH_FEET
from osrlib.core.spells import (
    CastContext,
    MemorizedSpell,
    RangeSpec,
    ReversedForm,
    memorize_spells,
    validate_cast,
)
from osrlib.crawl.commands import BattleDeclaration, ResolveBattleRound
from osrlib.data import load_spells
from test_battle import battle_session
from test_casting import build_caster, profile_of

# Each reversible spell of the built-in catalog, and the range its reverse's text states, or None where the
# reverse's text states none and the spell's own range line covers both forms.
REVERSED_RANGES: dict[str, RangeSpec | None] = {
    "bless": None,
    "continual_light_c": None,
    "continual_light_mu": None,
    "cure_disease": None,
    "cure_light_wounds": None,
    "cure_serious_wounds": None,
    "geas": None,
    "light_c": None,
    "light_mu": None,
    "quest": None,
    "raise_dead": None,
    "remove_curse_c": None,
    "remove_curse_mu": None,
    "remove_fear": RangeSpec(kind="feet", feet=120),
    "stone_to_flesh": None,
    "transmute_rock_to_mud": None,
}


def test_the_census_of_reversible_spells_is_complete():
    reversible = {spell.id for spell in load_spells().spells if spell.reversed_form is not None}
    assert reversible == set(REVERSED_RANGES)


@pytest.mark.parametrize(("spell_id", "expected"), REVERSED_RANGES.items())
def test_each_reverse_has_the_range_its_text_states(spell_id, expected):
    spell = load_spells().get(spell_id)
    assert spell.reversed_form.range_spec == expected
    assert (spell.reversed_form.range is None) == (expected is None)
    assert spell.range_spec_for(reversed=True) == (expected or spell.range_spec)
    assert spell.range_spec_for() == spell.range_spec


def test_cause_fear_reaches_120_feet_and_remove_fear_stays_at_touch():
    remove_fear = load_spells().get("remove_fear")
    assert remove_fear.range_spec.kind == "touch"
    assert remove_fear.reversed_form.range == "120’"
    assert "within 120’" in remove_fear.reversed_form.prose


def test_a_spell_without_a_reverse_has_its_own_range_either_way():
    fire_ball = load_spells().get("fire_ball")
    assert fire_ball.range_spec_for(reversed=True) == fire_ball.range_spec


def _cleric():
    return build_caster("cleric", 2, memorized=("remove_fear",))


@pytest.mark.parametrize(("distance", "accepted"), [(5, True), (60, True), (120, True), (125, False)])
def test_the_kernel_checks_cause_fear_against_120_feet(distance, accepted):
    cleric = _cleric()
    goblin = build_caster("fighter", 1, name="goblin")
    rejections = validate_cast(
        cleric,
        load_spells().get("remove_fear"),
        "frighten",
        profile=profile_of(cleric),
        reversed=True,
        targets=[goblin],
        context=CastContext(distance_feet=distance),
    )
    if accepted:
        assert rejections == []
    else:
        assert [(r.code, r.params["range_feet"]) for r in rejections] == [("magic.cast.out_of_range", 120)]


def test_the_kernel_still_checks_remove_fear_against_touch():
    cleric = _cleric()
    ally = build_caster("fighter", 1, name="ally")
    rejections = validate_cast(
        cleric,
        load_spells().get("remove_fear"),
        "remove",
        profile=profile_of(cleric),
        targets=[ally],
        context=CastContext(distance_feet=60),
    )
    assert [(r.code, r.params["range_feet"]) for r in rejections] == [("magic.cast.out_of_range", MELEE_REACH_FEET)]


def test_a_scroll_of_cause_fear_reaches_120_feet():
    from osrlib.core.spells import validate_scroll_cast

    reader = build_caster("cleric", 1)
    goblin = build_caster("fighter", 1, name="goblin")
    spell = load_spells().get("remove_fear")
    near = validate_scroll_cast(
        reader, spell, "frighten", reversed=True, targets=[goblin], context=CastContext(distance_feet=60)
    )
    assert near == []
    far = validate_scroll_cast(
        reader, spell, "frighten", reversed=True, targets=[goblin], context=CastContext(distance_feet=130)
    )
    assert [r.code for r in far] == ["magic.cast.out_of_range"]


@pytest.mark.parametrize("distance", [60, 5])
def test_a_battle_declaration_of_cause_fear_reaches_a_goblin(distance):
    """The issue's repro: a 2nd-level cleric casts *cause fear* at one goblin, 60' away and then 5' away."""
    session = battle_session(count=1, distance=distance, seed=1)
    cleric = session.member("character-0003")
    cleric.level = 2
    assert memorize_spells(
        cleric, cleric.definition, session.effective_spells, [MemorizedSpell(spell_id="remove_fear")]
    ).accepted
    goblin_id = session.encounter.groups[0].monster_ids[0]
    cast = BattleDeclaration(
        character_id=cleric.id,
        action="cast",
        spell_id="remove_fear",
        spell_mode="frighten",
        reversed=True,
        targets=(goblin_id,),
    )
    others = tuple(
        BattleDeclaration(character_id=member.id, action="hold")
        for member in session.party.living_members()
        if member.id != cleric.id
    )
    result = session.execute(ResolveBattleRound(declarations=(*others, cast)))
    assert result.rejections == ()
    assert result.accepted


@pytest.mark.parametrize(
    "fields",
    [{"range": "120’"}, {"range_spec": {"kind": "feet", "feet": 120}}],
    ids=["range_alone", "range_spec_alone"],
)
def test_a_reverse_needs_its_range_and_range_spec_together(fields):
    """A reverse with a printed range and no spec would show one distance while casting checked another."""
    form = load_spells().get("remove_fear").reversed_form.model_dump(mode="json")
    form.update({"range": None, "range_spec": None, **fields})
    with pytest.raises(ValidationError, match="range and range_spec must be set together"):
        ReversedForm.model_validate(form)


def test_a_reverse_with_both_or_neither_validates():
    form = load_spells().get("remove_fear").reversed_form
    assert ReversedForm.model_validate(form.model_dump(mode="json")) == form
    neither = ReversedForm.model_validate({**form.model_dump(mode="json"), "range": None, "range_spec": None})
    assert neither.range_spec is None
