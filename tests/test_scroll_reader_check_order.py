"""A member who can't read a scroll learns nothing about its spells from the refusal.

Before, `UseItem` looked the guessed `spell_id` up on the scroll before it checked the reader's class, so a fighter
holding an unidentified scroll got `items.scroll.wrong_caster` for a spell on it and `items.scroll.no_such_spell` for
one that wasn't. The session now checks the reader's class first, out of battle and in a battle's `use_item`
declaration, so the fighter gets `items.scroll.wrong_caster` whatever they guess.
"""

import positions_fixtures as pf
from osrlib.core.items import MagicItemInstance
from osrlib.crawl.commands import BattleDeclaration, UseItem
from osrlib.crawl.session import GameSession

SCROLL = "magic-item-9001"


def _give_scroll(game: GameSession, member_id: str, *, identified: bool = False) -> None:
    game.member(member_id).inventory.items.append(
        MagicItemInstance(
            instance_id=SCROLL,
            template_id="spell_scroll_2",
            identified=identified,
            state={"spell_list": "magic_user", "spells": ("fire_ball", "web")},
        )
    )


def test_a_fighter_gets_the_same_refusal_for_any_guess():
    game = pf.session(1, (pf.HILD, pf.WREN), light=True)
    hild = pf.named(game, "Hild")
    _give_scroll(game, hild)

    codes = {
        guess: [
            rejection.code
            for rejection in game.execute(UseItem(character_id=hild, item_id=SCROLL, spell_id=guess)).rejections
        ]
        for guess in (None, "fire_ball", "web", "sleep")
    }

    assert codes == dict.fromkeys((None, "fire_ball", "web", "sleep"), ["items.scroll.wrong_caster"])


def test_a_reader_of_an_identified_scroll_is_told_a_spell_isnt_on_it():
    """A magic-user reading an identified scroll, whose spells the view lists, gets `items.scroll.no_such_spell`
    for a spell that isn't on it."""
    game = pf.session(1, (pf.HILD, pf.WREN), light=True)
    wren = pf.named(game, "Wren")
    _give_scroll(game, wren, identified=True)

    result = game.execute(UseItem(character_id=wren, item_id=SCROLL, spell_id="sleep"))

    assert [rejection.code for rejection in result.rejections] == ["items.scroll.no_such_spell"]


def test_a_fighters_battle_declaration_gets_the_same_refusal_for_any_guess():
    game = pf.battle((pf.HILD, pf.WREN), light=True)
    hild = pf.named(game, "Hild")
    _give_scroll(game, hild)

    for guess in ("fire_ball", "sleep"):
        read = BattleDeclaration(
            character_id=hild,
            action="use_item",
            item_id=SCROLL,
            spell_id=guess,
            spell_mode="damage",
            target_group_id=pf.group_id(game),
        )

        assert [code for code, _ in pf.refused(game, read)] == ["items.scroll.wrong_caster"]
