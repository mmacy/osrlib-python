"""A session plays a spell your game wrote (issue #253).

A game can write a spell and resolve it with the kernel functions, but a session found spells in the
built-in catalog only. An adventure now bundles spells the way it bundles monsters and items, and the
session reads every spell from its own catalog: the built-in spells and the adventure's.
"""

import pytest

import positions_fixtures as pf
from osrlib.core.abilities import AbilityScore
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, Character, create_character
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.spells import MemorizedSpell, SpellCatalog, SpellTemplate
from osrlib.crawl.adventure import Adventure, validate_adventure
from osrlib.crawl.commands import CastSpell, EngageBattle, EnterDungeon, PrepareSpells, Rest, SessionMode, SpawnMonsters
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.data import load_classes, load_equipment, load_monsters, load_spells
from osrlib.errors import ContentValidationError
from osrlib.persistence import load_game, save_game

pytestmark = pytest.mark.xfail(reason="chunk: custom-spells")


def custom_spell(base_id: str, spell_id: str) -> SpellTemplate:
    """A spell of your own: a built-in spell under a new id and name."""
    document = {**load_spells().get(base_id).model_dump(), "id": spell_id, "name": spell_id.replace("_", " ").title()}
    return SpellTemplate.model_validate(document)


GLIMMER = custom_spell("shield", "glimmer")
FROST_DART = custom_spell("magic_missile", "frost_dart")


def adventure(*spells: SpellTemplate) -> Adventure:
    base = pf.corridor()
    return Adventure.model_validate({**base.model_dump(), "spells": [spell.model_dump() for spell in spells]})


def wizard(*book: str) -> Character:
    definition = load_classes().get("magic_user")
    return Character(
        name="Wren",
        class_id="magic_user",
        race=definition.race,
        level=3,
        xp=definition.row(3).xp,
        scores={ability: 12 for ability in AbilityScore},
        alignment="lawful",
        max_hp=400,
        current_hp=400,
        spell_book=book,
    )


def session(*spells: SpellTemplate, book: tuple[str, ...]) -> GameSession:
    party = Party(members=[pf.character("Hild", "fighter"), wizard(*book)])
    return GameSession.new(party, adventure(*spells), seed=7)


def prepare(game: GameSession, *spell_ids: str):
    wren = pf.named(game, "Wren")
    assert game.execute(Rest(kind="night")).accepted
    selections = tuple(MemorizedSpell(spell_id=spell_id) for spell_id in spell_ids)
    return game.execute(PrepareSpells(character_id=wren, selections=selections))


# ---------------------------------------------------------------------- the adventure's spells


def test_an_adventure_bundles_spells():
    bundled = adventure(GLIMMER, FROST_DART)
    assert [spell.id for spell in bundled.spells] == ["glimmer", "frost_dart"]
    validate_adventure(bundled, load_monsters(), load_equipment())
    assert adventure().spells == ()


def test_a_bundled_spell_cant_take_a_built_in_id():
    clash = custom_spell("shield", "sleep")
    with pytest.raises(ContentValidationError, match="sleep"):
        validate_adventure(adventure(clash), load_monsters(), load_equipment())
    with pytest.raises(ContentValidationError):
        session(clash, book=())


def test_a_bundled_spell_id_cant_repeat():
    with pytest.raises(ContentValidationError, match="glimmer") as caught:
        validate_adventure(adventure(GLIMMER, GLIMMER), load_monsters(), load_equipment())
    assert "collides with the catalog" not in str(caught.value)


def test_the_session_has_one_catalog_of_every_spell_it_can_use():
    game = session(GLIMMER, book=())
    catalog = game.effective_spells
    assert isinstance(catalog, SpellCatalog)
    assert catalog.get("glimmer") == GLIMMER
    assert catalog.get("sleep") == load_spells().get("sleep")
    with pytest.raises(ValueError):
        load_spells().get("glimmer")
    assert session(book=()).effective_spells.spells == load_spells().spells


# ---------------------------------------------------------------------- playing one


def test_a_caster_prepares_and_casts_a_bundled_spell():
    game = session(GLIMMER, book=("glimmer", "sleep"))
    wren = game.member(pf.named(game, "Wren"))
    prepared = prepare(game, "glimmer", "sleep")
    assert prepared.accepted, [rejection.code for rejection in prepared.rejections]
    assert [copy.spell_id for copy in wren.memorized_spells] == ["glimmer", "sleep"]
    cast = game.execute(CastSpell(character_id=wren.id, spell_id="glimmer", mode="shield"))
    assert cast.accepted, [rejection.code for rejection in cast.rejections]
    assert [copy.spell_id for copy in wren.memorized_spells] == ["sleep"]


def test_a_spell_the_adventure_doesnt_bundle_is_still_unknown():
    game = session(book=("glimmer",))
    refused = prepare(game, "glimmer")
    assert [rejection.code for rejection in refused.rejections] == ["magic.memorize.unknown_spell"]


def test_a_bundled_spell_is_cast_in_a_battle():
    game = session(FROST_DART, book=("frost_dart",))
    wren = pf.named(game, "Wren")
    assert prepare(game, "frost_dart").accepted
    assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
    for _ in range(40):
        if game.mode is SessionMode.BATTLE:
            break
        if game.mode is SessionMode.EXPLORING:
            game.execute(SpawnMonsters(template_id="ogre", count_fixed=1, distance_feet=30))
        if game.mode is SessionMode.ENCOUNTER:
            game.execute(EngageBattle())
    assert game.mode is SessionMode.BATTLE
    pf.hold_monsters(game)
    ogre = game.encounter.groups[0].monster_ids[0]
    before = game.combatant(ogre).current_hp
    dart = pf.declare(wren, "cast", spell_id="frost_dart", spell_mode="missiles", targets=(ogre,))
    events = pf.play(game, dart)
    assert game.combatant(ogre).current_hp < before
    assert any(event.code == "magic.cast.cast" and event.spell_id == "frost_dart" for event in events)
    assert game.member(wren).memorized_spells == ()


def test_a_save_keeps_the_adventures_spells():
    game = session(GLIMMER, book=("glimmer",))
    assert prepare(game, "glimmer").accepted
    loaded = load_game(save_game(game))
    assert loaded.effective_spells.get("glimmer") == GLIMMER
    wren = loaded.member(pf.named(loaded, "Wren"))
    assert loaded.execute(CastSpell(character_id=wren.id, spell_id="glimmer", mode="shield")).accepted


# ---------------------------------------------------------------------- creating a caster


def test_create_character_takes_the_spells_a_caster_can_start_with():
    catalog = SpellCatalog(spells=(*load_spells().spells, GLIMMER))
    stream = RngStreams(master_seed=11).get(CHARACTER_CREATION_STREAM)
    result = create_character(
        name="Wren",
        class_id="magic_user",
        alignment=Alignment.NEUTRAL,
        ruleset=Ruleset(),
        stream=stream,
        starting_spell_ids=["glimmer"],
        spell_catalog=catalog,
    )
    assert result.character.spell_book == ("glimmer",)


def test_create_character_still_refuses_a_spell_no_catalog_has():
    stream = RngStreams(master_seed=11).get(CHARACTER_CREATION_STREAM)
    before = stream.export_state()
    with pytest.raises(ValueError):
        create_character(
            name="Wren",
            class_id="magic_user",
            alignment=Alignment.NEUTRAL,
            ruleset=Ruleset(),
            stream=stream,
            starting_spell_ids=["glimmer"],
        )
    assert stream.export_state() == before
