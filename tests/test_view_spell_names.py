"""The player view names every spell whose id it shows (issue #257).

A remote front end builds its screens from `PlayerView` alone. It used to turn each spell id into a
name with `load_spells()`, which lacks the spells an adventure bundles. `PlayerView.spell_names`
carries the name of every spell id in the view, read from the session's catalog, and names nothing
the view doesn't already show.

Wren, the 3rd-level magic-user in these sessions, has two 1st-level slots and one 2nd-level slot. Where a test
pins her names exactly, her book is full at both levels, so the spells she may learn (issue #326) add none. A
cleric with a slot may prepare every spell on the cleric's list at that level, so the cleric tests name the
spells the cleric may prepare beside the ones they pin.
"""

import positions_fixtures as pf
from osrlib.core.events import Visibility
from osrlib.core.items import MagicItemInstance
from osrlib.core.spells import MemorizedSpell, SpellTemplate
from osrlib.crawl.commands import CastSpell, PrepareSpells, Rest
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.crawl.views import PlayerView
from osrlib.data import load_spells
from test_custom_spells import GLIMMER, adventure, custom_spell, prepare, session

# A cleric's spell of the adventure's own: protection from evil under a new id. Its effect
# sets no kind of its own, so the effect's kind is the spell's id.
WARDING = custom_spell("protection_from_evil_c", "warding")


def _mending() -> SpellTemplate:
    """A reversible cleric spell of the adventure's own: cure light wounds as "Mending", reversed as "Rending"."""
    document = custom_spell("cure_light_wounds", "mending").model_dump()
    document["reversed_form"] = {**document["reversed_form"], "name": "Rending"}
    return SpellTemplate.model_validate(document)


MENDING = _mending()


def _player_view(game: GameSession) -> PlayerView:
    view = game.view(Visibility.PLAYER)
    assert isinstance(view, PlayerView)
    return view


def _preparable(view: PlayerView, name: str) -> set[str]:
    return set(next(member for member in view.party if member.name == name).preparable_spells)


def _reverses(game: GameSession, spell_ids: set[str]) -> dict[str, str]:
    """The name of each reverse among `spell_ids`, from the session's catalog."""
    spells = (game.effective_spells.get(spell_id) for spell_id in spell_ids)
    return {spell.id: spell.reversed_form.name for spell in spells if spell.reversed_form is not None}


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

    A reversed copy of *light* casts *darkness*, and its entry here is the spell's own name, "Light",
    because `spell_names` always names the normal form. `reversed_spell_names` has "Darkness".
    """
    game = session(book=("magic_missile", "sleep", "light_mu", "web"))
    wren = pf.named(game, "Wren")
    assert game.execute(Rest(kind="night")).accepted
    selections = (MemorizedSpell(spell_id="magic_missile"), MemorizedSpell(spell_id="light_mu", reversed=True))
    assert game.execute(PrepareSpells(character_id=wren, selections=selections)).accepted

    view = _player_view(game)

    assert view.spell_names == {"magic_missile": "Magic Missile", "sleep": "Sleep", "light_mu": "Light", "web": "Web"}
    member = next(member for member in view.party if member.id == wren)
    assert member.memorized_spells[1] == {"spell_id": "light_mu", "reversed": True}


def test_a_bundled_spell_under_a_new_id_has_the_name_the_adventure_gave_it():
    """`load_spells()` has no "glimmer". The session's catalog does, and its name is "Glimmer"."""
    game = session(GLIMMER, book=("glimmer", "magic_missile", "web"))
    assert prepare(game, "glimmer").accepted

    view = _player_view(game)

    assert view.spell_names == {"glimmer": "Glimmer", "magic_missile": "Magic Missile", "web": "Web"}


def test_a_member_with_no_spells_adds_no_names():
    """The fighter beside the magic-user adds nothing, and a party of fighters has no spell names."""
    game = session(book=("sleep", "shield", "web"))

    assert _player_view(game).spell_names == {"sleep": "Sleep", "shield": "Shield", "web": "Web"}

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
    assert view.spell_names["warding"] == "Warding"
    assert set(view.spell_names) == {"warding"} | _preparable(view, "Oswin")


def test_an_effect_kind_that_isnt_a_spell_id_has_no_name():
    """*Light* gives its effect the kind "light", which is no spell's id (the spells are `light_c` and `light_mu`)."""
    game = _cleric_session()
    oswin = _cleric_prepares(game, "light_c", "cure_light_wounds")
    cast = game.execute(CastSpell(character_id=oswin, spell_id="light_c", mode="illuminate", targets=(oswin,)))
    assert cast.accepted, [rejection.code for rejection in cast.rejections]

    view = _player_view(game)

    assert [effect.kind for effect in view.effects] == ["light"]
    assert "light" not in view.spell_names
    assert view.spell_names["cure_light_wounds"] == "Cure Light Wounds"
    assert set(view.spell_names) == {"cure_light_wounds"} | _preparable(view, "Oswin")


def test_a_spell_id_the_sessions_catalog_lacks_has_no_entry():
    """A book from another adventure can hold "glimmer". This session's catalog doesn't have it, so it has no name."""
    game = session(book=("glimmer", "magic_missile"))

    view = _player_view(game)

    assert view.spell_names == {"magic_missile": "Magic Missile"}


def test_the_spells_on_a_scroll_stay_hidden():
    """No spell on a scroll is named, identified or not, because the view shows no scroll's spells.

    Wren's book is full at both levels she has slots for, so she can't learn the scrolls' spells either.
    """
    game = session(book=("magic_missile", "shield", "invisibility"))
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

    assert view.spell_names == {"magic_missile": "Magic Missile", "shield": "Shield", "invisibility": "Invisibility"}
    blob = view.model_dump_json()
    for hidden in ('"sleep"', '"Sleep"', '"web"', '"Web"', '"fire_ball"', '"Fire Ball"'):
        assert hidden not in blob


# ---------------------------------------------------------------------- the reverse's name


def test_a_reversible_spell_in_a_book_has_its_reverses_name():
    """*Light* reverses into *darkness*. The book holds `light_mu` and nothing is memorized."""
    game = session(book=("light_mu", "magic_missile", "web"))

    view = _player_view(game)

    assert view.reversed_spell_names == {"light_mu": load_spells().get("light_mu").reversed_form.name}
    assert view.reversed_spell_names["light_mu"] == "Darkness"
    assert view.spell_names["light_mu"] == "Light"


def test_a_clerics_unreversed_copy_still_has_its_reverses_name():
    """A cleric chooses the form when casting, so a copy prepared plain can still be cast as *cause light wounds*."""
    game = _cleric_session()
    oswin = _cleric_prepares(game, "cure_light_wounds", "protection_from_evil_c")

    view = _player_view(game)

    member = next(member for member in view.party if member.id == oswin)
    assert member.memorized_spells[0] == {"spell_id": "cure_light_wounds", "reversed": False}
    assert view.reversed_spell_names["cure_light_wounds"] == "Cause Light Wounds"
    assert view.reversed_spell_names == {"cure_light_wounds": "Cause Light Wounds"} | _reverses(
        game, _preparable(view, "Oswin")
    )


def test_a_spell_with_no_reverse_has_no_reverse_entry():
    """*Magic missile* and *sleep* don't reverse, so neither has a reverse's name."""
    game = session(book=("magic_missile", "sleep", "web"))

    view = _player_view(game)

    assert view.spell_names == {"magic_missile": "Magic Missile", "sleep": "Sleep", "web": "Web"}
    assert view.reversed_spell_names == {}


def test_a_bundled_reversible_spell_has_the_reverses_name_the_adventure_gave_it():
    """`load_spells()` has no "mending". The session's catalog does, and its reverse is "Rending"."""
    game = _cleric_session(MENDING)
    _cleric_prepares(game, "mending")

    view = _player_view(game)

    assert view.spell_names["mending"] == "Mending"
    assert set(view.spell_names) == {"mending"} | _preparable(view, "Oswin")
    assert view.reversed_spell_names["mending"] == "Rending"
    assert view.reversed_spell_names == {"mending": "Rending"} | _reverses(game, _preparable(view, "Oswin"))
