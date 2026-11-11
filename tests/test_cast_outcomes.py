"""What a session adds to a cast: *remove curse* frees a stuck item, and a saved-against silence stays in the cell.

The kernel resolves neither one, so the session does. It has to do it the same way however the spell is cast,
from memory or from a scroll, in a battle or outside one. And it has to know the spell by what the spell is, not
by the letters its id starts with, now that an adventure can bundle a spell under any id.
"""

import pytest

import positions_fixtures as pf
from osrlib.core.items import MagicItemInstance
from osrlib.core.spells import MemorizedSpell, SpellTemplate
from osrlib.crawl._party_location import _cell_ref
from osrlib.crawl.adventure import Adventure
from osrlib.crawl.commands import CastSpell, UseItem
from osrlib.crawl.session import GameSession
from osrlib.data import load_spells

pytestmark = pytest.mark.xfail(reason="chunk: review-fixes-two")

ROSTER = (pf.HILD, pf.TAM, pf.WREN)
LEVELS = {"Tam": 6, "Wren": 7}


def custom_spell(base_id: str, spell_id: str) -> SpellTemplate:
    """A spell of the game's own: a built-in spell under a new id and name."""
    document = {**load_spells().get(base_id).model_dump(), "id": spell_id, "name": spell_id.replace("_", " ").title()}
    return SpellTemplate.model_validate(document)


def bundling(*spells: SpellTemplate) -> Adventure:
    return Adventure.model_validate({**pf.corridor().model_dump(), "spells": [spell.model_dump() for spell in spells]})


def stuck_sword(game: GameSession, name: str) -> MagicItemInstance:
    """A cursed sword the member has found out about, which they can't put down."""
    cursed = MagicItemInstance(
        instance_id="magic-item-9001",
        template_id="sword_minus_1_cursed",
        base_item_id="sword",
        identified=True,
        cursed_revealed=True,
    )
    game.member(pf.named(game, name)).inventory.items.append(cursed)
    return cursed


def memorize(game: GameSession, name: str, *spell_ids: str) -> str:
    member = game.member(pf.named(game, name))
    if member.class_id == "magic_user":
        member.spell_book = spell_ids
    member.memorized_spells = tuple(MemorizedSpell(spell_id=spell_id) for spell_id in spell_ids)
    return member.id


# ---------------------------------------------------------------------- remove curse


def test_remove_curse_cast_from_memory_frees_a_stuck_item():
    game = pf.session(0, ROSTER, levels=LEVELS)
    sword = stuck_sword(game, "Hild")
    tam = memorize(game, "Tam", "remove_curse_c")
    cast = game.execute(
        CastSpell(character_id=tam, spell_id="remove_curse_c", mode="cast", targets=(pf.named(game, "Hild"),))
    )
    assert cast.accepted, [rejection.code for rejection in cast.rejections]
    assert not sword.cursed_revealed


def test_remove_curse_read_from_a_scroll_frees_a_stuck_item():
    game = pf.session(0, ROSTER, levels=LEVELS, light=True)
    sword = stuck_sword(game, "Hild")
    wren = game.member(pf.named(game, "Wren"))
    scroll = MagicItemInstance(
        instance_id="magic-item-9002",
        template_id="spell_scroll_1",
        identified=True,
        state={"spell_list": "magic_user", "spells": ("remove_curse_mu",)},
    )
    wren.inventory.items.append(scroll)
    read = game.execute(
        UseItem(
            character_id=wren.id,
            item_id=scroll.instance_id,
            spell_id="remove_curse_mu",
            mode="cast",
            targets=(pf.named(game, "Hild"),),
        )
    )
    assert read.accepted, [rejection.code for rejection in read.rejections]
    assert not sword.cursed_revealed


def test_remove_curse_cast_in_a_battle_frees_a_stuck_item():
    game = pf.battle(ROSTER, levels=LEVELS)
    pf.hold_monsters(game)
    sword = stuck_sword(game, "Hild")
    tam = memorize(game, "Tam", "remove_curse_c")
    cast = pf.declare(tam, "cast", spell_id="remove_curse_c", spell_mode="cast", targets=(pf.named(game, "Hild"),))
    pf.play(game, cast)
    assert not sword.cursed_revealed


def test_the_reversed_spell_frees_nothing():
    game = pf.session(0, ROSTER, levels=LEVELS)
    sword = stuck_sword(game, "Hild")
    tam = memorize(game, "Tam", "remove_curse_c")
    hild = pf.named(game, "Hild")
    cast = game.execute(
        CastSpell(character_id=tam, spell_id="remove_curse_c", mode="cast", reversed=True, targets=(hild,))
    )
    assert cast.accepted, [rejection.code for rejection in cast.rejections]
    assert sword.cursed_revealed


def test_a_bundled_spell_isnt_remove_curse_because_its_id_starts_that_way():
    """`remove_curse_minor` here is *cure light wounds* under another id."""
    minor = custom_spell("cure_light_wounds", "remove_curse_minor")
    game = pf.session(0, ROSTER, levels=LEVELS, adventure=bundling(minor))
    sword = stuck_sword(game, "Hild")
    tam = memorize(game, "Tam", "remove_curse_minor")
    hild = pf.named(game, "Hild")
    cast = game.execute(CastSpell(character_id=tam, spell_id="remove_curse_minor", mode="heal", targets=(hild,)))
    assert cast.accepted, [rejection.code for rejection in cast.rejections]
    assert sword.cursed_revealed


# ---------------------------------------------------------------------- silence that stays in the cell


def silence_that_was_saved_against(spell_id: str, *spells: SpellTemplate) -> GameSession:
    """A battle in which Tam cast the spell on an ogre and the ogre made its save."""
    for seed in pf.seeds(roster=ROSTER, levels=LEVELS, template_id="ogre", count=1, adventure=bundling(*spells)):
        game = pf.battle(
            ROSTER, levels=LEVELS, template_id="ogre", count=1, adventure=bundling(*spells), first_seed=seed
        )
        pf.hold_monsters(game)
        tam = memorize(game, "Tam", spell_id)
        ogre = game.encounter.groups[0].monster_ids[0]
        events = pf.play(game, pf.declare(tam, "cast", spell_id=spell_id, spell_mode="creature", targets=(ogre,)))
        if any(event.code == "combat.save.passed" for event in events):
            return game
        if seed > 150:
            break
    raise AssertionError("precondition: no ogre made its save")


def test_silence_that_a_creature_saves_against_stays_in_the_cell():
    game = silence_that_was_saved_against("silence_15_radius")
    assert game.ledger.active_on(_cell_ref(game), "silence")


def test_a_bundled_silence_stays_in_the_cell_too():
    """`hush` is *silence 15' radius* under another id, so it does what that spell does."""
    game = silence_that_was_saved_against("hush", custom_spell("silence_15_radius", "hush"))
    assert game.ledger.active_on(_cell_ref(game), "silence")
