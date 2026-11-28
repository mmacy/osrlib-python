"""A spell or a scroll can name as a cell target only a cell of the level the party stands on (issue #320).

`CastSpell`, a scroll read with `UseItem`, and a battle's `cast` declaration take a `cell:` reference as a target,
in the form [`cell_ref`][osrlib.crawl.dungeon.cell_ref] builds. The session refuses a reference that doesn't parse,
that names no cell of the adventure, or that names a cell of another level or another dungeon, with
`magic.cast.unknown_target`, the code an unknown creature id gets. With the party in town, no cell is in reach,
so every cell reference gets that refusal. The refusal comes in the validation pre-phase, so it costs no draw, no
time, and no state.

Which cells of the party's own level are in reach is left open, so any cell of that level passes this check.
"""

import pytest

import positions_fixtures as pf
from osrlib.core.items import MagicItemInstance
from osrlib.crawl._party_location import _cell_ref
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import CastSpell, Command, CommandResult, TravelToTown, UseItem
from osrlib.crawl.dungeon import DungeonSpec, Edge, EdgeKind, LevelSpec, cell_ref
from osrlib.crawl.session import GameSession

ROSTER = (pf.HILD, pf.WREN, pf.TAM)

REFUSED_REFS = (
    "cell:nowhere:1:0,0",
    "cell:crypt:9:0,0",
    "cell:crypt:1:5,5",
    "cell:crypt:1:-1,0",
    "cell:crypt:2:0,0",
    "cell:barrow:1:0,0",
    "cell:crypt:1:0,0:extra",
    "cell:crypt:1:x,0",
    "cell:crypt:01:0,0",
    "cell:",
    "cell:crypt:1",
)
"""References that name no cell of the level the party stands on, `crypt` level 1, two cells wide and one deep.

The first five name no cell of the adventure. `cell:crypt:2:0,0` and `cell:barrow:1:0,0` name real cells on
another level and in another dungeon. The rest don't parse, or aren't in the canonical form `cell_ref` builds."""


def two_levels() -> Adventure:
    """The fixtures' corridor as `crypt` level 1, with a level 2 below it and a second dungeon, `barrow`."""
    first = LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)})
    second = LevelSpec(number=2, width=2, height=1, edges={"1,0:west": Edge(kind=EdgeKind.OPEN)})
    crypt = DungeonSpec(id="crypt", name="Crypt", levels=(first, second))
    barrow = DungeonSpec(id="barrow", name="Barrow", levels=(LevelSpec(number=1, width=1, height=1, entrance=(0, 0)),))
    town = TownSpec(name="Threshold", travel_turns={"crypt": 1, "barrow": 1})
    return Adventure(name="A", town=town, dungeons=(crypt, barrow))


def refusal(game: GameSession, command: Command) -> list[tuple[str, dict]]:
    """Send a command that should be refused, check that it cost nothing, and return its rejections in order."""
    before = pf.state_without_logs(game)
    result = game.execute(command)
    assert not result.accepted, "the command was accepted"
    assert result.events == ()
    assert pf.state_without_logs(game) == before, "the refusal changed state, drew, or took time"
    return [(rejection.code, dict(rejection.params)) for rejection in result.rejections]


def accepted(result: CommandResult) -> CommandResult:
    assert result.accepted, [(rejection.code, dict(rejection.params)) for rejection in result.rejections]
    return result


def light_at(game: GameSession, ref: str) -> CastSpell:
    return CastSpell(character_id=pf.named(game, "Wren"), spell_id="light_mu", mode="illuminate", targets=(ref,))


def scroll(game: GameSession, spell_id: str) -> MagicItemInstance:
    instance = MagicItemInstance(
        instance_id="magic-item-9101",
        template_id="spell_scroll_1",
        identified=True,
        state={"spell_list": "magic_user", "spells": (spell_id,)},
    )
    game.member(pf.named(game, "Wren")).inventory.items.append(instance)
    return instance


@pytest.mark.parametrize("ref", REFUSED_REFS)
@pytest.mark.parametrize(("spell", "mode"), [("light_mu", "illuminate"), ("web", "entangle")])
def test_cast_spell_refuses_a_cell_off_the_party_level(ref, spell, mode):
    game = pf.session(0, ROSTER, spells={"Wren": (spell,)}, adventure=two_levels())
    cast = CastSpell(character_id=pf.named(game, "Wren"), spell_id=spell, mode=mode, targets=(ref,))

    assert refusal(game, cast) == [("magic.cast.unknown_target", {"target": ref})]


@pytest.mark.parametrize("position", [(0, 0), (1, 0)])
def test_cast_spell_takes_any_cell_of_the_party_level(position):
    game = pf.session(0, ROSTER, spells={"Wren": ("light_mu",)}, adventure=two_levels())

    accepted(game.execute(light_at(game, cell_ref("crypt", 1, position))))


def test_cast_spell_in_town_refuses_every_cell():
    game = pf.session(0, ROSTER, spells={"Wren": ("light_mu",)}, adventure=two_levels())
    ref = _cell_ref(game)
    accepted(game.execute(TravelToTown()))

    assert refusal(game, light_at(game, ref)) == [("magic.cast.unknown_target", {"target": ref})]


@pytest.mark.parametrize("ref", REFUSED_REFS)
def test_a_scroll_read_refuses_a_cell_off_the_party_level(ref):
    game = pf.session(0, ROSTER, light=True, adventure=two_levels())
    instance = scroll(game, "light_mu")
    use = UseItem(character_id=pf.named(game, "Wren"), item_id=instance.instance_id, mode="illuminate", targets=(ref,))

    assert refusal(game, use) == [("magic.cast.unknown_target", {"target": ref})]


def test_a_scroll_read_takes_the_party_cell():
    game = pf.session(0, ROSTER, light=True, adventure=two_levels())
    instance = scroll(game, "light_mu")
    use = UseItem(
        character_id=pf.named(game, "Wren"), item_id=instance.instance_id, mode="illuminate", targets=(_cell_ref(game),)
    )

    accepted(game.execute(use))


def test_a_scroll_read_names_a_cell_in_target_id_too():
    game = pf.session(0, ROSTER, light=True, adventure=two_levels())
    instance = scroll(game, "light_mu")
    ref = "cell:crypt:2:0,0"
    use = UseItem(character_id=pf.named(game, "Wren"), item_id=instance.instance_id, mode="illuminate", target_id=ref)

    assert refusal(game, use) == [("magic.cast.unknown_target", {"target": ref})]


def light_declared_at(game: GameSession, ref: str):
    return pf.declare(pf.named(game, "Wren"), "cast", spell_id="light_mu", spell_mode="illuminate", targets=(ref,))


@pytest.mark.parametrize("ref", REFUSED_REFS)
def test_a_battle_cast_refuses_a_cell_off_the_party_level(ref):
    game = pf.battle(ROSTER, spells={"Wren": ("light_mu",)}, adventure=two_levels())
    wren = pf.named(game, "Wren")

    assert pf.refused(game, light_declared_at(game, ref)) == [
        ("magic.cast.unknown_target", {"target": ref, "character": wren})
    ]


def test_a_battle_cast_takes_a_cell_of_the_party_level():
    game = pf.battle(ROSTER, spells={"Wren": ("light_mu",)}, adventure=two_levels())

    pf.play(game, light_declared_at(game, cell_ref("crypt", 1, (1, 0))))
