"""The player view names every spell whose id it shows (issue #257).

A remote front end builds its screens from `PlayerView` alone. It used to turn each spell id into a
name with `load_spells()`, which lacks the spells an adventure bundles. `PlayerView.spell_names`
carries the name of every spell id in the view, read from the session's catalog, and names nothing
the view doesn't already show.
"""

import pytest

import positions_fixtures as pf
from osrlib.core.events import Visibility
from osrlib.core.items import MagicItemInstance
from osrlib.core.spells import MemorizedSpell
from osrlib.crawl.commands import CastSpell, PrepareSpells, Rest
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.crawl.views import PlayerView
from test_custom_spells import GLIMMER, adventure, custom_spell, prepare, session

pytestmark = pytest.mark.xfail(reason="chunk: view-text")

# A cleric's spell of the adventure's own: protection from evil under a new id. Its effect
# sets no kind of its own, so the effect's kind is the spell's id.
WARDING = custom_spell("protection_from_evil_c", "warding")


def _player_view(game: GameSession) -> PlayerView:
    view = game.view(Visibility.PLAYER)
    assert isinstance(view, PlayerView)
    return view


def _cleric_session(*spells) -> GameSession:
    # A 3rd-level cleric has two 1st-level spell slots and no spell book.
    party = Party(members=[pf.character("Hild", "fighter"), pf.character("Oswin", "cleric")])
    game = GameSession.new(party, adventure(*spells), seed=7)
    assert game.execute(Rest(kind="night")).accepted
    return game


def _cleric_prepares(game: GameSession, *spell_ids: str) -> str:
    oswin = pf.named(game, "Oswin")
    selections = tuple(MemorizedSpell(spell_id=spell_id) for spell_id in spell_ids)
    prepared = game.execute(PrepareSpells(character_id=oswin, selections=selections))
    assert prepared.accepted, [rejection.code for rejection in prepared.rejections]
    return oswin


def test_built_in_spells_in_the_book_and_memorized_have_their_names():
    """The book's ids and the memorized copies' ids are named from the catalog's `name`.

    A reversed copy of *light* casts *darkness*, and its entry is the spell's own name, "Light",
    because `spell_names` is keyed by id and the copy's `reversed` says which form it casts.
    """
    game = session(book=("magic_missile", "sleep", "light_mu"))
    wren = pf.named(game, "Wren")
    assert game.execute(Rest(kind="night")).accepted
    selections = (MemorizedSpell(spell_id="magic_missile"), MemorizedSpell(spell_id="light_mu", reversed=True))
    assert game.execute(PrepareSpells(character_id=wren, selections=selections)).accepted

    view = _player_view(game)

    assert view.spell_names == {"magic_missile": "Magic Missile", "sleep": "Sleep", "light_mu": "Light"}
    member = next(member for member in view.party if member.id == wren)
    assert member.memorized_spells[1] == {"spell_id": "light_mu", "reversed": True}


def test_a_bundled_spell_under_a_new_id_has_the_name_the_adventure_gave_it():
    """`load_spells()` has no "glimmer". The session's catalog does, and its name is "Glimmer"."""
    game = session(GLIMMER, book=("glimmer", "magic_missile"))
    assert prepare(game, "glimmer").accepted

    view = _player_view(game)

    assert view.spell_names == {"glimmer": "Glimmer", "magic_missile": "Magic Missile"}


def test_a_member_with_no_spells_adds_no_names():
    """The fighter beside the magic-user adds nothing, and a party of fighters has no spell names."""
    game = session(book=("sleep",))

    assert _player_view(game).spell_names == {"sleep": "Sleep"}

    fighters = Party(members=[pf.character("Hild", "fighter"), pf.character("Tam", "fighter")])
    assert _player_view(GameSession.new(fighters, adventure(), seed=7)).spell_names == {}


def test_a_spell_effect_is_named_after_the_cleric_has_spent_the_copy():
    """A cleric has no book, so once the only copy is cast, the effect's kind is the one place the id appears."""
    game = _cleric_session(WARDING)
    oswin = _cleric_prepares(game, "warding")
    cast = game.execute(CastSpell(character_id=oswin, spell_id="warding", mode="ward"))
    assert cast.accepted, [rejection.code for rejection in cast.rejections]

    view = _player_view(game)

    assert [(effect.character_id, effect.kind) for effect in view.effects] == [(oswin, "warding")]
    assert all(member.memorized_spells == () and member.spell_book == () for member in view.party)
    assert view.spell_names == {"warding": "Warding"}


def test_an_effect_kind_that_isnt_a_spell_id_has_no_name():
    """*Light* gives its effect the kind "light", which is no spell's id (the spells are `light_c` and `light_mu`)."""
    game = _cleric_session()
    oswin = _cleric_prepares(game, "light_c", "cure_light_wounds")
    cast = game.execute(CastSpell(character_id=oswin, spell_id="light_c", mode="illuminate", targets=(oswin,)))
    assert cast.accepted, [rejection.code for rejection in cast.rejections]

    view = _player_view(game)

    assert [effect.kind for effect in view.effects] == ["light"]
    assert view.spell_names == {"cure_light_wounds": "Cure Light Wounds"}


def test_a_spell_id_the_sessions_catalog_lacks_has_no_entry():
    """A book from another adventure can hold "glimmer". This session's catalog doesn't have it, so it has no name."""
    game = session(book=("glimmer", "magic_missile"))

    view = _player_view(game)

    assert view.spell_names == {"magic_missile": "Magic Missile"}


def test_the_spells_on_a_scroll_stay_hidden():
    """No spell on a scroll is named, identified or not, because the view shows no scroll's spells."""
    game = session(book=("magic_missile",))
    wren = game.member(pf.named(game, "Wren"))
    wren.inventory.items.append(
        MagicItemInstance(
            instance_id="magic-item-9001",
            template_id="spell_scroll_1",
            state={"spell_list": "magic_user", "spells": ("sleep",)},
        )
    )
    wren.inventory.items.append(
        MagicItemInstance(
            instance_id="magic-item-9002",
            template_id="spell_scroll_2",
            identified=True,
            state={"spell_list": "magic_user", "spells": ("web", "fire_ball")},
        )
    )

    view = _player_view(game)

    assert view.spell_names == {"magic_missile": "Magic Missile"}
    blob = view.model_dump_json()
    for hidden in ('"sleep"', '"Sleep"', '"web"', '"Web"', '"fire_ball"', '"Fire Ball"'):
        assert hidden not in blob
