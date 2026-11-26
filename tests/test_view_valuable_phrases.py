"""The player view words each named gem's or piece of jewellery's phrase for the middle of a sentence (issue #354).

`PlayerView.valuable_names` gives a piece's own name as its author wrote it for a list, with a capital, like
`"Silver altar candlestick"` or `"Prior's chain of office"`. A client can't put that in a sentence, because it can't
tell a common noun, which needs an article and a lowercase start, from a proper name, which needs neither.
`PlayerView.valuable_phrases` gives the whole phrase, article and case included, under the same keys: the piece's
`phrase` when its author set one, and otherwise a phrase osrlib makes from the name.

The made phrase follows one rule. A name whose first word is "the", "a", or "an", in any case, keeps its words with
that first word in lowercase. Any other name gets its first letter in lowercase and "an" in front before a vowel, "a"
otherwise. A `phrase` that's only whitespace counts as empty. A piece with no name of its own, whose name is empty or
the `"Gem"` or `"Jewellery"` that treasure generation gives its kind, is a key in neither map, whatever its `phrase`
holds.
"""

import json
from pathlib import Path

import pytest

from crawl_fixtures import build_party
from osrlib.core.events import Visibility
from osrlib.core.items import ValuableInstance
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import DropItems, EnterDungeon, TakeTreasure
from osrlib.crawl.dungeon import AreaSpec, DropPile, DungeonSpec, FeatureSpec, LevelSpec, ValuableSpec, cell_ref
from osrlib.crawl.session import GameSession
from osrlib.crawl.views import PileItemView, PlayerView
from osrlib.persistence import load_game, save_game

CHUNK = "chunk: valuable-phrases"

SAVES = Path(__file__).parent / "saves"
SAVE_2_9_0 = SAVES / "view_valuable_phrases_2.9.0.json"
"""A save of `hauled(OLD_KEPT, OLD_DROPPED)`, written by osrlib 2.9.0 before `phrase` existed."""
VIEW_2_9_0 = SAVES / "view_valuable_phrases_player_view_2.9.0.json"
"""The `PlayerView` of the same state, dumped as JSON by osrlib 2.9.0, as a client might store a feed entry."""

SEED = 31
"""Any seed: the adventure has no rolled treasure and no wandering monsters, so nothing here depends on a roll."""

NAVE = cell_ref("priory", 1, (0, 0))
"""The entrance cell, where the party stands, so a pile here is a known pile."""
TOMB = cell_ref("priory", 1, (2, 0))
"""A cell the party never walks, so a pile here is a pile the party doesn't know."""

# The placed pieces of the 2.9.0 save. None has a phrase, because osrlib 2.9.0 wrote the save, so each named piece's
# phrase is made from its name.
CANDLESTICK = ValuableSpec(kind="jewellery", name="Silver altar candlestick", value_gp=300)
SEAL_RING = ValuableSpec(kind="jewellery", name="The abbot's seal ring", value_gp=900)
ROLLED_LOOKALIKE = ValuableSpec(kind="gem", name="Gem", value_gp=40)
EYE = ValuableSpec(kind="gem", name="Eye of Ossor", value_gp=500)
AMBER = ValuableSpec(kind="gem", name="An amber tear", value_gp=250)
BARE_JEWEL = ValuableSpec(kind="jewellery", value_gp=350)
OLD_KEPT = (CANDLESTICK, SEAL_RING, ROLLED_LOOKALIKE)
OLD_DROPPED = (EYE, AMBER, BARE_JEWEL)
OLD_PHRASES = {
    # No phrase, so each is made from the name. "Silver" isn't an article and starts with a consonant: "a", and only
    # the first letter is lowered.
    CANDLESTICK: "a silver altar candlestick",
    # The first word is "The": it keeps its words, with "The" lowered.
    SEAL_RING: "the abbot's seal ring",
    # "Eye" starts with a vowel: "an". Only the first letter is lowered, so "Ossor" keeps its capital.
    EYE: "an eye of Ossor",
    # The first word is "An": it keeps its words, with "An" lowered.
    AMBER: "an amber tear",
}
"""The phrase of each named piece in the 2.9.0 save. `ROLLED_LOOKALIKE` is named `"Gem"`, the name treasure
generation gives a gem, and `BARE_JEWEL` has no name, so neither is a key."""

# The placed pieces with phrases, for the haul that only this version can make.
OSSOR = ValuableSpec(kind="gem", name="Ossor's Eye", phrase="Ossor's Eye", value_gp=1500)
CHAIN = ValuableSpec(
    kind="jewellery", name="Prior's chain of office", phrase="the Prior's chain of office", value_gp=1200
)
STAR = ValuableSpec(kind="gem", name="Star of the lower chapel", phrase=" \t ", value_gp=400)
CLOUDY = ValuableSpec(kind="gem", phrase="a cloudy opal", value_gp=60)
BROOCH = ValuableSpec(kind="jewellery", name="Jewellery", phrase="a tarnished brooch", value_gp=70)
NEW_KEPT = (OSSOR, STAR, CLOUDY, CANDLESTICK)
NEW_DROPPED = (CHAIN, BROOCH)
NEW_PHRASES = {
    # A phrase is used as written.
    OSSOR: "Ossor's Eye",
    CHAIN: "the Prior's chain of office",
    # A phrase of whitespace alone counts as empty, so the phrase is made from the name: "a", first letter lowered.
    STAR: "a star of the lower chapel",
    # No phrase.
    CANDLESTICK: "a silver altar candlestick",
}
"""The phrase of each named piece in the phrased haul. `CLOUDY` has no name, and `BROOCH` has the name treasure
generation gives a piece of jewellery, so neither is a key, whatever its phrase."""

MITRE = ValuableSpec(kind="jewellery", name="Bishop's mitre jewel", phrase="the bishop's mitre jewel", value_gp=800)
"""In the tomb, a cache on a cell the party never walks."""
CROWN = ValuableInstance(
    instance_id="valuable-9001", kind="gem", name="Drowned crown", phrase="the drowned crown", value_gp=700
)
"""In a pile on the tomb's cell, which the party doesn't know."""


def _adventure(reliquary: tuple[ValuableSpec, ...]) -> Adventure:
    """Three cells in a row. The party enters at the nave, on the west, whose reliquary holds `reliquary`. The tomb,
    on the east, stays unvisited."""
    nave_features = (
        (FeatureSpec(id="reliquary", kind="treasure_cache", cell=(0, 0), valuables=reliquary),) if reliquary else ()
    )
    tomb = FeatureSpec(id="tomb", kind="treasure_cache", description="A sealed tomb.", cell=(2, 0), valuables=(MITRE,))
    level = LevelSpec(
        number=1,
        width=3,
        height=1,
        entrance=(0, 0),
        areas=(
            AreaSpec(id="nave", name="Nave", cells=((0, 0),), features=nave_features),
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


def _pieces(game: GameSession) -> list[ValuableInstance]:
    """Every valuable in the session, carried or in a pile, known or not."""
    carried = [valuable for member in game.party.members for valuable in member.inventory.valuables]
    return carried + [valuable for pile in game.dungeon_state.piles.values() for valuable in pile.valuables]


def _placed(game: GameSession, spec: ValuableSpec) -> ValuableInstance:
    """The piece `spec` placed, wherever it is now, found by its kind, name, and value."""
    (piece,) = (
        valuable
        for valuable in _pieces(game)
        if (valuable.kind, valuable.name, valuable.value_gp) == (spec.kind, spec.name, spec.value_gp)
    )
    return piece


def hauled(kept: tuple[ValuableSpec, ...], dropped: tuple[ValuableSpec, ...]) -> GameSession:
    """The stock party enters the priory, empties the reliquary of `kept` and `dropped`, and drops `dropped` onto
    the nave's cell, so the packs hold `kept` and a known pile holds `dropped`.

    The builder uses no name that osrlib 2.9.0 lacks, so the 2.9.0 fixture writer runs it too.
    """
    game = GameSession.new(build_party(), _adventure(kept + dropped), seed=SEED)
    assert game.execute(EnterDungeon(dungeon_id="priory")).accepted
    for feature_id in _player_view(game).features_here:
        taken = game.execute(TakeTreasure(feature_id=feature_id))
        assert taken.accepted, [rejection.code for rejection in taken.rejections]
    holders = {
        valuable.instance_id: member.id for member in game.party.members for valuable in member.inventory.valuables
    }
    assert len(holders) == len(kept) + len(dropped), "precondition: the party carries every placed piece"
    for spec in dropped:
        instance_id = _placed(game, spec).instance_id
        result = game.execute(DropItems(character_id=holders[instance_id], item_ids=(instance_id,)))
        assert result.accepted, [rejection.code for rejection in result.rejections]
    return game


def _with_pieces(pack: list[ValuableInstance], pile: list[ValuableInstance]) -> GameSession:
    """A session in the nave whose first member carries `pack` and whose known pile on the nave's cell holds `pile`,
    each piece built by hand, so a test can give a piece any name and phrase."""
    game = GameSession.new(build_party(), _adventure(()), seed=SEED)
    assert game.execute(EnterDungeon(dungeon_id="priory")).accepted
    game.party.members[0].inventory.valuables.extend(pack)
    game.dungeon_state.piles[NAVE] = DropPile(valuables=pile)
    view = _player_view(game)
    shown = {valuable["instance_id"] for sheet in view.party for valuable in sheet.inventory["valuables"]}
    assert {piece.instance_id for piece in pack} <= shown, "precondition: the view shows the pack"
    assert NAVE in view.piles, "precondition: the nave's pile is known"
    return game


def _piece(number: int, name: str, phrase: str = "", kind: str = "jewellery") -> ValuableInstance:
    return ValuableInstance(instance_id=f"valuable-{number:04d}", kind=kind, name=name, phrase=phrase, value_gp=100)


def _expected(game: GameSession, phrases: dict[ValuableSpec, str]) -> dict[str, str]:
    return {_placed(game, spec).instance_id: phrase for spec, phrase in phrases.items()}


# ---------------------------------------------------------------------- the made phrase


MADE = [
    # A name with no article starts with a consonant: "a", and its first letter is lowered.
    ("Silver altar candlestick", "a silver altar candlestick"),
    ("Moonstone", "a moonstone"),
    # "y" isn't a vowel.
    ("Yellow topaz", "a yellow topaz"),
    # A name that already starts in lowercase gets the article alone.
    ("garnet", "a garnet"),
    # A vowel takes "an". Only the first letter is lowered, so a capital later in the name stays.
    ("Eye of Ossor", "an eye of Ossor"),
    ("Opal of the Lower Chapel", "an opal of the Lower Chapel"),
    ("Ivory comb", "an ivory comb"),
    ("Aquamarine", "an aquamarine"),
    # "u" is a vowel, so the rule gives "an". An author who wants "a" sets the phrase.
    ("Unicorn-horn cup", "an unicorn-horn cup"),
    # A proper name gets the rule's phrase too, which reads wrong. That's what `phrase` is for.
    ("Ossor's Eye", "an ossor's Eye"),
    # A first word of "the", "a", or "an" keeps the name's words, with that word lowered, in any case.
    ("The abbot's seal ring", "the abbot's seal ring"),
    ("the prior's chain", "the prior's chain"),
    ("THE ABBOT'S SEAL", "the ABBOT'S SEAL"),
    ("A ring of drowned silver", "a ring of drowned silver"),
    ("An amber tear", "an amber tear"),
    ("AN Onyx Idol", "an Onyx Idol"),
    # The article must be the whole first word: "Theodric's" and "Annulet" only start with one.
    ("Theodric's torc", "a theodric's torc"),
    ("Annulet of jet", "an annulet of jet"),
]


@pytest.mark.xfail(reason=CHUNK)
@pytest.mark.parametrize(("name", "phrase"), MADE, ids=[name for name, _ in MADE])
def test_a_piece_with_no_phrase_has_the_phrase_made_from_its_name(name, phrase):
    """The same piece's phrase, made by the rule, in a member's pack and in a known pile."""
    game = _with_pieces([_piece(101, name)], [_piece(102, name)])

    view = _player_view(game)

    assert view.valuable_phrases == {"valuable-0101": phrase, "valuable-0102": phrase}
    assert view.valuable_names == {"valuable-0101": name, "valuable-0102": name}


@pytest.mark.xfail(reason=CHUNK)
@pytest.mark.parametrize(
    ("name", "phrase"),
    [
        ("Ossor's Eye", "Ossor's Eye"),
        ("Prior's chain of office", "the Prior's chain of office"),
        # The phrase is used as written, so a capital the author gave it stays.
        ("Silver altar candlestick", "A silver altar candlestick"),
        ("Moonstone", "the moonstone of the lower chapel"),
    ],
)
def test_a_piece_with_a_phrase_has_it_as_written(name, phrase):
    game = _with_pieces([_piece(101, name, phrase)], [_piece(102, name, phrase)])

    view = _player_view(game)

    assert view.valuable_phrases == {"valuable-0101": phrase, "valuable-0102": phrase}


@pytest.mark.xfail(reason=CHUNK)
@pytest.mark.parametrize("blank", ["", " ", "\t", " \n\t "])
def test_a_phrase_of_whitespace_alone_counts_as_empty(blank):
    """As `SpellMode.display_name` treats a label of whitespace alone. "Star" takes "a", first letter lowered."""
    game = _with_pieces([_piece(101, "Star of the lower chapel", blank)], [])

    assert _player_view(game).valuable_phrases == {"valuable-0101": "a star of the lower chapel"}


@pytest.mark.xfail(reason=CHUNK)
def test_the_keys_are_exactly_the_keys_of_valuable_names():
    """Every variant, in a member's pack and in a known pile.

    The variants are a named piece with no phrase, one with a phrase, one with a phrase of whitespace alone, and, with
    no name of their own, an empty name with and without a phrase, and the rolled name of each kind with and without a
    phrase. Only the first three are keys, in both maps, in both places.
    """
    variants = [
        ("Silver altar candlestick", "", "jewellery", True),
        ("Ossor's Eye", "Ossor's Eye", "gem", True),
        ("Star of the lower chapel", "  ", "gem", True),
        ("", "a cloudy opal", "gem", False),
        ("", "", "jewellery", False),
        ("Gem", "a gem of the lower chapel", "gem", False),
        ("Gem", "", "gem", False),
        ("Jewellery", "a tarnished brooch", "jewellery", False),
        ("Jewellery", "", "jewellery", False),
    ]
    pack = [_piece(100 + n, name, phrase, kind) for n, (name, phrase, kind, _) in enumerate(variants)]
    pile = [_piece(200 + n, name, phrase, kind) for n, (name, phrase, kind, _) in enumerate(variants)]
    game = _with_pieces(pack, pile)

    view = _player_view(game)

    keyed = {f"valuable-{base + n:04d}" for base in (100, 200) for n, (*_, has_name) in enumerate(variants) if has_name}
    assert set(view.valuable_names) == keyed
    assert set(view.valuable_phrases) == keyed
    assert view.valuable_phrases == {
        f"valuable-{base + n:04d}": phrase
        for base in (100, 200)
        for n, phrase in enumerate(["a silver altar candlestick", "Ossor's Eye", "a star of the lower chapel"])
    }


# ---------------------------------------------------------------------- the haul


@pytest.mark.xfail(reason=CHUNK)
def test_taking_a_placed_piece_copies_its_phrase():
    """The session copies each spec's `phrase` to the piece it makes, as it copies `name`, whitespace and all. The
    inventory's `valuables` show it as they show `name`, and the view has the phrase of each named piece, carried
    and dropped."""
    game = hauled(NEW_KEPT, NEW_DROPPED)

    for spec in NEW_KEPT + NEW_DROPPED:
        assert _placed(game, spec).phrase == spec.phrase, spec.name
    view = _player_view(game)
    shown = {valuable["instance_id"]: valuable for sheet in view.party for valuable in sheet.inventory["valuables"]}
    for spec in NEW_KEPT:
        entry = shown[_placed(game, spec).instance_id]
        assert (entry["name"], entry["phrase"]) == (spec.name, spec.phrase)
    assert view.valuable_phrases == _expected(game, NEW_PHRASES)
    assert set(view.valuable_phrases) == set(view.valuable_names)


@pytest.mark.xfail(reason=CHUNK)
def test_a_session_restored_from_a_save_has_the_same_phrases():
    game = hauled(NEW_KEPT, NEW_DROPPED)

    loaded = load_game(json.loads(json.dumps(save_game(game))))

    for spec in NEW_KEPT + NEW_DROPPED:
        assert _placed(loaded, spec).phrase == spec.phrase, spec.name
    assert _player_view(loaded).valuable_phrases == _player_view(game).valuable_phrases == _expected(game, NEW_PHRASES)


def test_an_adventure_document_keeps_a_phrase():
    """A guard: an author's phrase survives the adventure document's JSON round trip."""
    adventure = _adventure(NEW_KEPT + NEW_DROPPED)

    restored = Adventure.model_validate_json(adventure.model_dump_json())

    (reliquary, *_) = restored.dungeons[0].levels[0].areas[0].features
    assert [spec.phrase for spec in reliquary.valuables] == [spec.phrase for spec in NEW_KEPT + NEW_DROPPED]


def test_a_piece_the_party_cant_see_has_no_phrase_in_the_view():
    """A guard: the tomb's mitre jewel is in a cache the party hasn't emptied, and the crown in a pile on a cell the
    party has never walked. Neither phrase is anywhere in the view."""
    game = hauled(NEW_KEPT, NEW_DROPPED)
    game.dungeon_state.piles[TOMB] = DropPile(valuables=[CROWN])

    view = _player_view(game)

    assert CROWN.instance_id not in view.valuable_phrases
    blob = view.model_dump_json()
    for hidden in ("mitre", "crown"):
        assert hidden not in blob.lower(), hidden


# ---------------------------------------------------------------------- what osrlib 2.9.0 wrote


@pytest.mark.xfail(reason=CHUNK)
def test_a_save_from_osrlib_2_9_0_gives_a_phrase_for_every_named_piece():
    """A 2.9.0 save's pieces have no phrase, so each named piece's phrase is made from its name, and the keys are the
    keys of `valuable_names`. The whole view of the loaded save is the view of the same state built now."""
    document = json.loads(SAVE_2_9_0.read_text(encoding="utf-8"))
    assert document["engine_version"] == "2.9.0"

    loaded = load_game(document)

    game = hauled(OLD_KEPT, OLD_DROPPED)
    assert all(piece.phrase == "" for piece in _pieces(loaded))
    view = _player_view(loaded)
    assert set(view.valuable_names) == set(_expected(game, OLD_PHRASES)), "precondition: the named pieces"
    assert view.valuable_phrases == _expected(game, OLD_PHRASES)
    assert set(view.valuable_phrases) == set(view.valuable_names)
    assert view == _player_view(game)


def test_a_player_view_from_osrlib_2_9_0_validates_with_no_phrases():
    """A guard: a client that stored a `PlayerView` osrlib 2.9.0 built, like a feed entry, can still read it. The
    view has `valuable_names` and no `valuable_phrases`, and it validates with the map empty."""
    document = json.loads(VIEW_2_9_0.read_text(encoding="utf-8"))
    assert "valuable_phrases" not in document
    assert document["valuable_names"], "precondition: the stored view names some pieces"

    view = PlayerView.model_validate(document)

    assert view.valuable_phrases == {}
    assert view.valuable_names == document["valuable_names"]
    assert set(view.valuable_names) == {
        _placed(hauled(OLD_KEPT, OLD_DROPPED), spec).instance_id for spec in OLD_PHRASES
    }


# ---------------------------------------------------------------------- the docs


def _description(model: type, field: str) -> str:
    return model.model_json_schema()["properties"][field]["description"]


@pytest.mark.xfail(reason=CHUNK)
def test_the_docstrings_send_a_sentence_to_valuable_phrases():
    """Each place that told a client to show a valuable's name in a sentence names `valuable_phrases` instead."""
    guidance = {
        "PlayerView.valuable_names": _description(PlayerView, "valuable_names"),
        "PlayerView.item_names": _description(PlayerView, "item_names"),
        "PileItemView": PileItemView.__doc__ or "",
    }
    for place, text in guidance.items():
        assert "valuable_phrases" in text, place
        assert "Sam takes Prior's chain of office" not in text, place


@pytest.mark.xfail(reason=CHUNK)
def test_the_guides_and_the_changelog_describe_the_phrases():
    root = Path(__file__).parent.parent
    views_guide = (root / "docs" / "guides" / "views-and-visibility.md").read_text(encoding="utf-8")
    authoring_guide = (root / "docs" / "guides" / "authoring-custom-content.md").read_text(encoding="utf-8")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    bullets = [line for line in changelog.splitlines() if line.startswith("- ") and "(#354)" in line]

    assert "valuable_phrases" in views_guide
    assert "Sam takes Prior's chain of office" not in views_guide
    assert "ValuableSpec.phrase" in authoring_guide or "`phrase`" in authoring_guide
    (bullet,) = (line for line in bullets if "valuable_phrases" in line)
    assert "phrase" in bullet.replace("valuable_phrases", "")
