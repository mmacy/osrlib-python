"""The player view lists the spells left on a scroll the party has identified (issue #294).

`UseItem.spell_id` chooses which spell to read off a scroll of several, and `None` reads the first spell left. A
client that builds its screens from `PlayerView` couldn't offer that choice, because no part of the view listed a
scroll's spells. An identified spell scroll's inventory entry now has a `spells` key, and the view names those spells
in `spell_names` and `spell_modes`. Reading the scroll and a referee's `IdentifyItem` identify it. Nothing else does,
so an unidentified scroll stays masked. The OSE SRD's spell scroll entry says "If a scroll contains multiple spells,
only the spell cast disappears from the scroll", so the spells left are ones the reader has seen.
"""

import positions_fixtures as pf
from osrlib.core.events import SpellCastEvent, Visibility
from osrlib.core.items import MagicItemInstance
from osrlib.crawl.commands import GrantItem, IdentifyItem, UseItem
from osrlib.crawl.events import ItemAcquiredEvent
from osrlib.crawl.session import GameSession
from osrlib.crawl.views import PlayerView

ROSTER = (pf.HILD, pf.WREN)


def _player_view(game: GameSession) -> PlayerView:
    view = game.view(Visibility.PLAYER)
    assert isinstance(view, PlayerView)
    return view


def _entry(game: GameSession, member_id: str, instance_id: str) -> dict:
    member = next(member for member in _player_view(game).party if member.id == member_id)
    return next(item for item in member.inventory["items"] if item.get("instance_id") == instance_id)


def _granted(game: GameSession, member_id: str, item_id: str) -> str:
    result = game.execute(GrantItem(character_id=member_id, item_id=item_id))
    assert result.accepted
    (event,) = [event for event in result.events if isinstance(event, ItemAcquiredEvent)]
    (instance_id,) = event.item_ids
    return instance_id


def _true_spells(game: GameSession, instance_id: str) -> list[str]:
    """The spells that the treasure roll put on the scroll, in the session's state."""
    for member in game.party.members:
        instance = member.inventory.magic_item(instance_id)
        if instance is not None:
            spells = instance.state["spells"]
            assert isinstance(spells, tuple)
            return [str(spell) for spell in spells]
    raise AssertionError("no member carries the scroll")


def test_an_unidentified_scroll_lists_no_spells():
    game = pf.session(3, ROSTER)
    wren = pf.named(game, "Wren")
    names_before = _player_view(game).spell_names

    scroll = _granted(game, wren, "spell_scroll_3")

    entry = _entry(game, wren, scroll)
    assert entry["identified"] is False
    assert "spells" not in entry
    assert _player_view(game).spell_names == names_before


def test_a_referee_identification_lists_the_spells_in_reading_order():
    game = pf.session(3, ROSTER)
    wren = pf.named(game, "Wren")
    scroll = _granted(game, wren, "spell_scroll_3")

    assert game.execute(IdentifyItem(character_id=wren, item_id=scroll)).accepted

    entry = _entry(game, wren, scroll)
    spells = _true_spells(game, scroll)
    assert entry["spells"] == spells
    view = _player_view(game)
    assert set(spells) <= set(view.spell_names)
    assert set(spells) <= set(view.spell_modes)


def test_reading_a_scroll_lists_the_spells_left():
    """Reading *shield* off a scroll of *shield*, *magic missile*, and *fire ball* identifies it and leaves two.

    Wren, a 3rd-level magic-user, has no 3rd-level slot, so the view names *fire ball* only because of the scroll.
    """
    game = pf.session(3, ROSTER, light=True)
    wren = pf.named(game, "Wren")
    game.member(wren).inventory.items.append(
        MagicItemInstance(
            instance_id="magic-item-9001",
            template_id="spell_scroll_3",
            state={"spell_list": "magic_user", "spells": ("shield", "magic_missile", "fire_ball")},
        )
    )
    assert "spells" not in _entry(game, wren, "magic-item-9001")
    assert "fire_ball" not in _player_view(game).spell_names

    read = game.execute(UseItem(character_id=wren, item_id="magic-item-9001", mode="shield"))
    assert read.accepted, [rejection.code for rejection in read.rejections]

    entry = _entry(game, wren, "magic-item-9001")
    assert (entry["identified"], entry["spells"]) == (True, ["magic_missile", "fire_ball"])
    view = _player_view(game)
    assert view.spell_names["fire_ball"] == "Fire Ball"
    assert [mode.key for mode in view.spell_modes["fire_ball"]] == ["damage"]
    assert view.spell_modes["magic_missile"][0].scroll_missile_count == 1

    hild = pf.named(game, "Hild")
    first = game.execute(UseItem(character_id=wren, item_id="magic-item-9001", mode="missiles", targets=(hild,)))
    assert first.accepted, [rejection.code for rejection in first.rejections]
    assert [event.spell_id for event in first.events if isinstance(event, SpellCastEvent)] == ["magic_missile"]
    assert _entry(game, wren, "magic-item-9001")["spells"] == ["fire_ball"]


def test_a_scroll_without_spells_has_no_spell_list():
    """A cursed scroll, a scroll of protection, and a treasure map are scrolls with no spells on them."""
    game = pf.session(3, ROSTER)
    hild = pf.named(game, "Hild")
    for item_id in ("cursed_scroll", "scroll_of_protection_from_undead", "treasure_map_i"):
        scroll = _granted(game, hild, item_id)
        assert game.execute(IdentifyItem(character_id=hild, item_id=scroll)).accepted

        entry = _entry(game, hild, scroll)

        assert entry["identified"] is True
        assert "spells" not in entry
