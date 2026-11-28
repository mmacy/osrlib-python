"""A cure that ends only conditions refuses a cell as its target (issue #257).

Only a creature has conditions, so a cure whose effect lists `cures_conditions` and no `cures_effect_kinds` does
nothing at a cell. The session refuses the cell with `magic.cast.target_must_be_creature` in the validation
pre-phase, at no cost. A cure that ends effects, like *light*'s `cancel`, which ends a *darkness* on a cell, still
takes one.
"""

import pytest

import positions_fixtures as pf
from osrlib.core.items import MagicItemInstance
from osrlib.crawl.commands import CastSpell, UseItem
from osrlib.crawl.dungeon import cell_ref
from osrlib.data import load_spells

ROSTER = (pf.HILD, pf.WREN, pf.TAM)
REF = cell_ref("crypt", 1, (1, 0))

CONDITION_CURES = (
    ("Tam", "cure_light_wounds", "cure_paralysis"),
    ("Tam", "cure_disease", "cure"),
    ("Tam", "neutralize_poison", "neutralize"),
    ("Tam", "remove_fear", "remove"),
    ("Wren", "stone_to_flesh", "restore"),
)


def caster_session(caster: str, spell: str):
    return pf.session(0, ROSTER, spells={caster: (spell,)}, levels={caster: 12})


@pytest.mark.parametrize(("caster", "spell", "mode"), CONDITION_CURES)
def test_the_cure_refuses_a_cell(caster, spell, mode):
    game = caster_session(caster, spell)
    before = pf.state_without_logs(game)

    result = game.execute(CastSpell(character_id=pf.named(game, caster), spell_id=spell, mode=mode, targets=(REF,)))

    assert [(rejection.code, dict(rejection.params)) for rejection in result.rejections] == [
        ("magic.cast.target_must_be_creature", {"spell": spell, "mode": mode, "target": REF})
    ]
    assert result.events == ()
    assert pf.state_without_logs(game) == before


@pytest.mark.parametrize(("caster", "spell", "mode"), CONDITION_CURES)
def test_the_cure_still_takes_a_member(caster, spell, mode):
    game = caster_session(caster, spell)
    cast = CastSpell(character_id=pf.named(game, caster), spell_id=spell, mode=mode, targets=(pf.named(game, "Hild"),))

    result = game.execute(cast)

    assert result.accepted, [(rejection.code, dict(rejection.params)) for rejection in result.rejections]


def test_the_catalog_cures_are_the_five_condition_cures_and_the_cancel_modes():
    cures = {
        (spell.id, mode.key): bool(mode.effect.cures_effect_kinds)
        for spell in load_spells().spells
        for form in (spell, spell.reversed_form)
        if form is not None
        for mode in form.modes
        if mode.effect is not None and mode.effect.kind == "cure"
    }
    assert {key for key, ends_effects in cures.items() if not ends_effects} == {
        (spell, mode) for _, spell, mode in CONDITION_CURES
    }
    assert all(mode == "cancel" for (_, mode), ends_effects in cures.items() if ends_effects)


def test_light_cancel_still_takes_a_cell():
    game = caster_session("Tam", "light_c")

    result = game.execute(
        CastSpell(character_id=pf.named(game, "Tam"), spell_id="light_c", mode="cancel", targets=(REF,))
    )

    assert result.accepted, [(rejection.code, dict(rejection.params)) for rejection in result.rejections]


def test_a_scroll_of_remove_fear_refuses_a_cell():
    game = pf.session(0, ROSTER, light=True)
    tam = pf.named(game, "Tam")
    scroll = MagicItemInstance(
        instance_id="magic-item-9101",
        template_id="spell_scroll_1",
        identified=True,
        state={"spell_list": "cleric", "spells": ("remove_fear",)},
    )
    game.member(tam).inventory.items.append(scroll)
    before = pf.state_without_logs(game)

    result = game.execute(UseItem(character_id=tam, item_id=scroll.instance_id, mode="remove", targets=(REF,)))

    assert [rejection.code for rejection in result.rejections] == ["magic.cast.target_must_be_creature"]
    assert pf.state_without_logs(game) == before


def test_a_battle_cast_of_cure_paralysis_refuses_a_cell():
    game = pf.battle(ROSTER, spells={"Tam": ("cure_light_wounds",)})
    tam = pf.named(game, "Tam")

    declared = pf.declare(tam, "cast", spell_id="cure_light_wounds", spell_mode="cure_paralysis", targets=(REF,))

    assert [code for code, _ in pf.refused(game, declared)] == ["magic.cast.target_must_be_creature"]
