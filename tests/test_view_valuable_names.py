"""The player view gives a named gem's or piece of jewellery's own name (issue #339).

`PlayerView.item_names` names a valuable by its kind, `"gem"` or `"piece of jewellery"`, a common noun a client puts
an article in front of. The same view shows a piece's own name in a member's `inventory` and in a pile's `items`, so a
client that words "Sam takes a piece of jewellery." from `item_names` reads worse than the view allows, and a client
that wants the own name has to pair a pile's `contents` with its `items` by position. `PlayerView.valuable_names`
gives the own name of every piece that has one, by its instance id, for the client to use as written.

A piece has a name of its own when an author gave it one. Treasure generation names every gem it rolls `"Gem"` and
every piece of jewellery `"Jewellery"`, the kind with a capital, which is no name of the piece's own, and a
`ValuableSpec` with no `name` makes a piece whose name is empty.
"""

import json
from pathlib import Path

from crawl_fixtures import build_party
from osrlib.core.events import Visibility
from osrlib.core.items import ValuableInstance
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import DropItems, EnterDungeon, TakeTreasure
from osrlib.crawl.dungeon import (
    AreaSpec,
    AreaTreasureSpec,
    DropPile,
    DungeonSpec,
    FeatureSpec,
    LevelSpec,
    ValuableSpec,
    cell_ref,
)
from osrlib.crawl.events import ItemAcquiredEvent
from osrlib.crawl.session import GameSession
from osrlib.crawl.views import PlayerView
from osrlib.persistence import load_game, save_game

SAVE_2_8_0 = Path(__file__).parent / "saves" / "view_valuable_names_2.8.0.json"
"""A save of `looted()`, written by osrlib 2.8.0 before `valuable_names` existed."""

SEED = 27
"""A seed whose roll of treasure type C in the nave gives at least two gems and two pieces of jewellery, all of which
the party carries away, leaving only copper behind. `looted()` checks it."""

CHAIN = ValuableSpec(kind="jewellery", name="Prior's chain of office", value_gp=1200)
RING = ValuableSpec(kind="jewellery", name="Ring of the drowned abbot", value_gp=900)
EYE = ValuableSpec(kind="gem", name="Eye of Ossor", value_gp=500)
STAR = ValuableSpec(kind="gem", name="Star of the lower chapel", value_gp=400)
BARE_GEM_KEPT = ValuableSpec(kind="gem", value_gp=50)
BARE_GEM_DROPPED = ValuableSpec(kind="gem", value_gp=60)
BARE_JEWEL_KEPT = ValuableSpec(kind="jewellery", value_gp=300)
BARE_JEWEL_DROPPED = ValuableSpec(kind="jewellery", value_gp=350)

RELIQUARY = (CHAIN, RING, EYE, STAR, BARE_GEM_KEPT, BARE_GEM_DROPPED, BARE_JEWEL_KEPT, BARE_JEWEL_DROPPED)
"""The placed pieces: a named and an unnamed piece of each kind for the packs, and the same again for the pile."""
DROPPED = (RING, STAR, BARE_GEM_DROPPED, BARE_JEWEL_DROPPED)

NAMED = (CHAIN, RING, EYE, STAR)

MITRE = ValuableSpec(kind="jewellery", name="Bishop's mitre jewel", value_gp=800)
"""In the tomb, a cache on a cell the party never walks."""
CROWN = ValuableInstance(instance_id="valuable-9001", kind="gem", name="Drowned crown", value_gp=700)
"""In a pile on the tomb's cell, which the party doesn't know."""


def _adventure() -> Adventure:
    """Three cells in a row. The party enters at the nave, on the west, whose reliquary holds the placed pieces and
    whose rolled treasure lies where the party enters. The tomb, on the east, stays unvisited."""
    reliquary = FeatureSpec(
        id="reliquary", kind="treasure_cache", description="A lead-lined reliquary.", cell=(0, 0), valuables=RELIQUARY
    )
    tomb = FeatureSpec(id="tomb", kind="treasure_cache", description="A sealed tomb.", cell=(2, 0), valuables=(MITRE,))
    level = LevelSpec(
        number=1,
        width=3,
        height=1,
        entrance=(0, 0),
        areas=(
            AreaSpec(
                id="nave",
                name="Nave",
                cells=((0, 0),),
                features=(reliquary,),
                treasure=AreaTreasureSpec(letters=("C",)),
            ),
            AreaSpec(id="crypt", name="Crypt", cells=((2, 0),), features=(tomb,)),
        ),
    )
    return Adventure(
        name="The Drowned Priory",
        town=TownSpec(name="Threshold", travel_turns={"priory": 1}),
        dungeons=(DungeonSpec(id="priory", name="The Priory", levels=(level,)),),
    )


def _player_view(game: GameSession) -> PlayerView:
    view = game.view(Visibility.PLAYER)
    assert isinstance(view, PlayerView)
    return view


def _carried(game: GameSession) -> list[tuple[str, ValuableInstance]]:
    """Each valuable a member carries, with the member's id, in party order."""
    return [(member.id, valuable) for member in game.party.members for valuable in member.inventory.valuables]


def _placed_id(game: GameSession, spec: ValuableSpec) -> str:
    """The instance id of the piece `spec` placed, wherever it is now, found by its kind, name, and value."""
    pieces = [valuable for _, valuable in _carried(game)]
    pieces += [valuable for pile in game.dungeon_state.piles.values() for valuable in pile.valuables]
    (instance_id,) = (
        valuable.instance_id
        for valuable in pieces
        if (valuable.kind, valuable.name, valuable.value_gp) == (spec.kind, spec.name, spec.value_gp)
    )
    return instance_id


def _take_everything(game: GameSession, seed: int) -> list[ItemAcquiredEvent]:
    acquired = []
    for feature_id in _player_view(game).features_here:
        taken = game.execute(TakeTreasure(feature_id=feature_id))
        assert taken.accepted, [rejection.code for rejection in taken.rejections]
        acquired += [event for event in taken.events if isinstance(event, ItemAcquiredEvent)]
    left = [valuable for pile in game.dungeon_state.piles.values() for valuable in pile.valuables]
    assert not left, f"precondition: the party carries every piece away with seed {seed}"
    return acquired


def looted(seed: int = SEED) -> tuple[GameSession, list[ItemAcquiredEvent]]:
    """The stock party enters the priory, empties the reliquary and the rolled cache, and drops some pieces.

    The members drop the four pieces in `DROPPED`, then the first rolled gem and the first rolled piece of jewellery
    in the party's packs, onto the nave's cell. So the packs and the pile each hold a named and an unnamed placed
    piece of each kind, and a rolled piece of each kind. Returns the session and the acquisitions.
    """
    game = GameSession.new(build_party(), _adventure(), seed=seed)
    assert game.execute(EnterDungeon(dungeon_id="priory")).accepted
    acquired = _take_everything(game, seed)
    rolled = [(holder, valuable) for holder, valuable in _carried(game) if valuable.name in ("Gem", "Jewellery")]
    for kind in ("gem", "jewellery"):
        assert sum(valuable.kind == kind for _, valuable in rolled) >= 2, f"precondition: seed {seed} rolls two {kind}"
    drops = [_placed_id(game, spec) for spec in DROPPED]
    drops += [
        next(valuable.instance_id for _, valuable in rolled if valuable.kind == kind) for kind in ("gem", "jewellery")
    ]
    holders = {valuable.instance_id: holder for holder, valuable in _carried(game)}
    for instance_id in drops:
        dropped = game.execute(DropItems(character_id=holders[instance_id], item_ids=(instance_id,)))
        assert dropped.accepted, [rejection.code for rejection in dropped.rejections]
    return game, acquired


def _expected(game: GameSession) -> dict[str, str]:
    return {_placed_id(game, spec): spec.name for spec in NAMED}


def _shown(view: PlayerView) -> tuple[dict[str, str], dict[str, str]]:
    """The name the view shows for each valuable: in the inventories' `valuables`, and in the piles' `items`."""
    in_packs = {
        valuable["instance_id"]: valuable["name"] for sheet in view.party for valuable in sheet.inventory["valuables"]
    }
    in_piles = {
        entry.item_id: text
        for pile in view.piles.values()
        for entry, text in zip(pile.contents, pile.items, strict=True)
        if entry.item_id.startswith("valuable-")
    }
    return in_packs, in_piles


# ---------------------------------------------------------------------- what the map holds


def test_valuable_names_has_each_named_piece_in_a_pack_or_a_pile():
    """The four named pieces, two carried and two in the pile. No unnamed or rolled piece is a key."""
    game, _ = looted()
    view = _player_view(game)

    assert view.valuable_names == _expected(game)
    assert set(view.valuable_names) <= set(view.item_names)


def test_valuable_names_agree_with_the_inventory_and_the_pile():
    """Every valuable the view shows, in the packs and the pile, of every variant.

    The variants are a named, an unnamed, and a rolled piece of each kind, and the packs and the pile each hold one of
    every variant. A key's value is the name both places show. A piece that isn't a key shows an empty name or the
    rolled name in a pack, and its kind or the rolled name in a pile.
    """
    game, _ = looted()
    view = _player_view(game)
    in_packs, in_piles = _shown(view)
    kinds = {valuable.instance_id: valuable.kind for _, valuable in _carried(game)}
    kinds |= {
        valuable.instance_id: valuable.kind for pile in game.dungeon_state.piles.values() for valuable in pile.valuables
    }
    for place, shown in (("packs", in_packs), ("pile", in_piles)):
        named = {kinds[instance_id] for instance_id in shown if instance_id in _expected(game)}
        rolled = {kinds[instance_id] for instance_id, text in shown.items() if text in ("Gem", "Jewellery")}
        bare = {kinds[instance_id] for instance_id, text in shown.items() if text in ("", "gem", "jewellery")}
        assert named == rolled == bare == {"gem", "jewellery"}, f"precondition: the {place} hold every variant"

    for instance_id, name in view.valuable_names.items():
        assert name == {**in_piles, **in_packs}[instance_id], instance_id
    for instance_id in set(in_packs) - set(view.valuable_names):
        assert in_packs[instance_id] in ("", "Gem", "Jewellery"), instance_id
    for instance_id in set(in_piles) - set(view.valuable_names):
        assert in_piles[instance_id] in ("gem", "jewellery", "Gem", "Jewellery"), instance_id
    assert view.valuable_names == _expected(game)


def test_item_names_still_names_every_valuable_by_its_kind():
    """`item_names` doesn't change: a common noun for a client to put an article in front of."""
    game, _ = looted()
    view = _player_view(game)
    in_packs, in_piles = _shown(view)
    kinds = {valuable.instance_id: valuable.kind for _, valuable in _carried(game)}
    kinds |= {
        valuable.instance_id: valuable.kind for pile in game.dungeon_state.piles.values() for valuable in pile.valuables
    }

    for instance_id in {*in_packs, *in_piles}:
        assert view.item_names[instance_id] == {"gem": "gem", "jewellery": "piece of jewellery"}[kinds[instance_id]]
        assert (
            view.item_names_plural[instance_id]
            == {"gem": "gems", "jewellery": "pieces of jewellery"}[kinds[instance_id]]
        )


def test_the_acquisitions_of_named_pieces_are_named_in_the_view_after_them():
    """The issue's case: `ItemAcquiredEvent` names each piece by its instance id, and the view after the take gives
    the own name of each named piece, so a client writes "Prior's chain of office" and not "a piece of jewellery"."""
    game = GameSession.new(build_party(), _adventure(), seed=SEED)
    assert game.execute(EnterDungeon(dungeon_id="priory")).accepted
    acquired = _take_everything(game, SEED)

    view = _player_view(game)

    taken = {item_id for event in acquired for item_id in event.item_ids if item_id.startswith("valuable-")}
    assert {instance_id for instance_id in taken if instance_id in view.valuable_names} == set(_expected(game))
    assert view.valuable_names[_placed_id(game, CHAIN)] == "Prior's chain of office"


def test_a_piece_the_party_cant_see_has_no_name_in_the_view():
    """The tomb's mitre jewel is in a cache the party hasn't emptied, and the crown in a pile on a cell the party has
    never walked. Neither is a key in either map, and neither name is anywhere in the view."""
    game, _ = looted()
    game.dungeon_state.piles[cell_ref("priory", 1, (2, 0))] = DropPile(valuables=[CROWN])

    view = _player_view(game)

    assert view.valuable_names == _expected(game)
    assert CROWN.instance_id not in view.item_names
    blob = view.model_dump_json()
    for hidden in (MITRE.name, CROWN.name, "mitre", "crown"):
        assert hidden not in blob, hidden


# ---------------------------------------------------------------------- saves


def test_a_session_restored_from_a_save_has_the_same_names():
    game, _ = looted()

    loaded = load_game(json.loads(json.dumps(save_game(game))))

    assert _player_view(loaded).valuable_names == _player_view(game).valuable_names == _expected(game)


def test_a_save_from_osrlib_2_8_0_gives_the_names():
    """The names come from the pieces, which a 2.8.0 save already has, and the whole view of the loaded save is the
    view of the same state built now."""
    document = json.loads(SAVE_2_8_0.read_text(encoding="utf-8"))
    assert document["engine_version"] == "2.8.0"

    loaded = load_game(document)

    game, _ = looted()
    view = _player_view(loaded)
    assert view.valuable_names == _expected(game)
    assert view == _player_view(game)


# ---------------------------------------------------------------------- the docs


def test_the_views_guide_and_the_changelog_describe_the_names():
    root = Path(__file__).parent.parent
    guide = (root / "docs" / "guides" / "views-and-visibility.md").read_text(encoding="utf-8")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    bullets = [line for line in changelog.splitlines() if line.startswith("- ") and "(#339)" in line]

    assert "valuable_names" in guide
    assert bullets, "no changelog bullet cites (#339)"
    assert "valuable_names" in "\n".join(bullets)
