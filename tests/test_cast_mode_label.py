"""The cast event carries the cast mode's label, and the default line uses it (issue #347).

Every `SpellMode` has a `display_name` (#340). A client that words a cast from the event alone could show only the
mode's key, as the default line did: "casts light_mu [blind]". `SpellCastEvent.mode_display_name` carries the label
beside the key, as the monster and item events carry `display_name`, and an event recorded before the field existed
falls back to the key.
"""

import positions_fixtures as pf
from osrlib.core.events import SpellCastEvent, parse_event
from osrlib.core.items import MagicItemInstance
from osrlib.core.spells import MemorizedSpell
from osrlib.crawl.commands import CastSpell, EnterDungeon, PrepareSpells, Rest, UseItem
from osrlib.messages import format_message
from test_custom_spells import prepare, session


def _cast_events(events) -> list[SpellCastEvent]:
    return [event for event in events if isinstance(event, SpellCastEvent)]


def test_a_cast_names_its_mode_by_label():
    game = session(book=("light_mu", "magic_missile", "web"))
    assert prepare(game, "light_mu").accepted
    wren = pf.named(game, "Wren")
    hild = pf.named(game, "Hild")

    result = game.execute(CastSpell(character_id=wren, spell_id="light_mu", mode="blind", targets=(hild,)))

    (event,) = _cast_events(result.events)
    assert (event.mode, event.mode_display_name) == ("blind", "Blind a creature")
    assert "[Blind a creature]" in format_message(event)


def test_a_reversed_cast_has_the_reverses_label():
    """The reverse of *light*'s `cancel` mode cancels light, and the normal form's cancels darkness."""
    game = session(book=("light_mu", "magic_missile", "web"))
    wren = pf.named(game, "Wren")
    assert game.execute(Rest(kind="night")).accepted
    selections = (MemorizedSpell(spell_id="light_mu", reversed=True),)
    assert game.execute(PrepareSpells(character_id=wren, selections=selections)).accepted
    hild = pf.named(game, "Hild")

    cast = CastSpell(character_id=wren, spell_id="light_mu", mode="cancel", reversed=True, targets=(hild,))
    result = game.execute(cast)

    (event,) = _cast_events(result.events)
    assert event.mode_display_name == "Cancel light"
    assert format_message(event) == f"{wren} casts light_mu (reversed) [Cancel light]. It has no effect."


def test_a_scroll_read_has_the_label_too():
    game = pf.session(3, (pf.HILD, pf.WREN), light=True)
    wren = pf.named(game, "Wren")
    game.member(wren).inventory.items.append(
        MagicItemInstance(
            instance_id="magic-item-9001",
            template_id="spell_scroll_1",
            state={"spell_list": "magic_user", "spells": ("shield",)},
        )
    )

    result = game.execute(UseItem(character_id=wren, item_id="magic-item-9001", mode="shield"))

    (event,) = _cast_events(result.events)
    assert (event.mode, event.mode_display_name) == ("shield", "Shield")


def test_an_event_from_before_the_label_falls_back_to_the_key():
    game = session(book=("light_mu", "magic_missile", "web"))
    assert prepare(game, "light_mu").accepted
    assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
    wren = pf.named(game, "Wren")
    result = game.execute(CastSpell(character_id=wren, spell_id="light_mu", mode="illuminate", targets=(wren,)))
    (event,) = _cast_events(result.events)
    record = event.model_dump(mode="json")
    del record["mode_display_name"]

    old = parse_event(record)

    assert isinstance(old, SpellCastEvent)
    assert old.mode_display_name == ""
    assert format_message(old) == f"{wren} casts light_mu [illuminate] at {wren}."
    assert format_message(event) == f"{wren} casts light_mu [Conjure light] at {wren}."
