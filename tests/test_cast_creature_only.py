"""A spell that works only on a creature refuses a cell as its target (issue #257).

*Fly*, *infravision*, *water breathing*, and *haste* give a creature something to do with its body: flight, sight in
the dark, breath under water, or speed. Their SRD pages name "the caster or a creature touched", "a single
subject", or "up to 24 creatures". Cast at a cell, the effect would sit on the cell and do nothing, so the session
refuses the cell with `magic.cast.target_must_be_creature`, as it refuses a cell for *fire ball*. The data marks
these modes with [`TargetingSpec.creatures_only`][osrlib.core.spells.TargetingSpec.creatures_only], because their
effects look like *light*'s, which works on a cell.
"""

import pytest

import positions_fixtures as pf
from osrlib.core.items import MagicItemInstance
from osrlib.core.spells import TargetingMode, TargetingSpec
from osrlib.crawl.commands import CastSpell, UseItem
from osrlib.crawl.dungeon import cell_ref
from osrlib.data import load_spells

ROSTER = (pf.HILD, pf.WREN, pf.TAM)

CREATURE_ONLY = (("fly", "grant"), ("infravision", "grant"), ("water_breathing", "grant"), ("haste", "haste"))


@pytest.mark.parametrize(("spell", "mode"), CREATURE_ONLY)
def test_the_spell_refuses_a_cell(spell, mode):
    game = pf.session(0, ROSTER, spells={"Wren": (spell,)}, levels={"Wren": 5})
    ref = cell_ref("crypt", 1, (1, 0))
    cast = CastSpell(character_id=pf.named(game, "Wren"), spell_id=spell, mode=mode, targets=(ref,))
    before = pf.state_without_logs(game)

    result = game.execute(cast)

    assert [(rejection.code, dict(rejection.params)) for rejection in result.rejections] == [
        ("magic.cast.target_must_be_creature", {"spell": spell, "mode": mode, "target": ref})
    ]
    assert pf.state_without_logs(game) == before


@pytest.mark.parametrize(("spell", "mode"), CREATURE_ONLY)
def test_the_spell_still_takes_a_member(spell, mode):
    game = pf.session(0, ROSTER, spells={"Wren": (spell,)}, levels={"Wren": 5})
    cast = CastSpell(character_id=pf.named(game, "Wren"), spell_id=spell, mode=mode, targets=(pf.named(game, "Hild"),))

    result = game.execute(cast)

    assert result.accepted, [(rejection.code, dict(rejection.params)) for rejection in result.rejections]


def test_a_scroll_of_fly_refuses_a_cell():
    game = pf.session(0, ROSTER, light=True)
    wren = pf.named(game, "Wren")
    scroll = MagicItemInstance(
        instance_id="magic-item-9101",
        template_id="spell_scroll_1",
        identified=True,
        state={"spell_list": "magic_user", "spells": ("fly",)},
    )
    game.member(wren).inventory.items.append(scroll)
    ref = cell_ref("crypt", 1, (1, 0))
    before = pf.state_without_logs(game)

    result = game.execute(UseItem(character_id=wren, item_id=scroll.instance_id, mode="grant", targets=(ref,)))

    assert [rejection.code for rejection in result.rejections] == ["magic.cast.target_must_be_creature"]
    assert pf.state_without_logs(game) == before


def test_a_battle_cast_of_fly_refuses_a_cell():
    game = pf.battle(ROSTER, spells={"Wren": ("fly",)}, levels={"Wren": 5})
    wren = pf.named(game, "Wren")
    ref = cell_ref("crypt", 1, (1, 0))

    declared = pf.declare(wren, "cast", spell_id="fly", spell_mode="grant", targets=(ref,))

    assert [code for code, _ in pf.refused(game, declared)] == ["magic.cast.target_must_be_creature"]


def test_only_those_modes_are_marked():
    marked = {
        (spell.id, mode.key)
        for spell in load_spells().spells
        for mode in spell.modes
        if mode.targeting is not None and mode.targeting.creatures_only
    }
    assert marked == set(CREATURE_ONLY)


def test_light_still_takes_a_cell():
    game = pf.session(0, ROSTER, spells={"Wren": ("light_mu",)})
    ref = cell_ref("crypt", 1, (1, 0))
    cast = CastSpell(character_id=pf.named(game, "Wren"), spell_id="light_mu", mode="illuminate", targets=(ref,))

    assert game.execute(cast).accepted


def test_the_field_defaults_to_false():
    assert TargetingSpec(mode=TargetingMode.SINGLE).creatures_only is False
