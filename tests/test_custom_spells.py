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
from osrlib.core.classes import drain_levels
from osrlib.core.combat import resolve_energy_drain
from osrlib.core.events import SpellForgottenEvent
from osrlib.core.items import MagicItemInstance
from osrlib.core.monsters import spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.spells import MemorizedSpell, SpellCatalog, SpellTemplate
from osrlib.crawl.adventure import Adventure, validate_adventure
from osrlib.crawl.commands import (
    CastSpell,
    EngageBattle,
    EnterDungeon,
    GrantItem,
    LightSource,
    PrepareSpells,
    Rest,
    SessionMode,
    SpawnMonsters,
    UseItem,
)
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


# ---------------------------------------------------------------------- a level drain

# A magic-user has a second-level slot at level 3 and none at level 2, so a drain from 3 to 2 forgets the
# second-level copy. `MIRE` is a second-level spell of the game's own.
MIRE = custom_spell("web", "mire")
WITH_MIRE = SpellCatalog(spells=(*load_spells().spells, MIRE))


class OneDie:
    """A stream with one hit die to give. A second draw fails the test."""

    def __init__(self, *values: int):
        self.values = list(values)

    def randbelow(self, n: int) -> int:
        return self.values.pop(0) % n


def drained_wizard(*memorized: str) -> Character:
    wren = wizard(*memorized)
    wren.id = "character-0001"
    wren.memorized_spells = tuple(MemorizedSpell(spell_id=spell_id) for spell_id in memorized)
    return wren


def copies(character: Character) -> list[str]:
    return [copy.spell_id for copy in character.memorized_spells]


def test_drain_levels_looks_a_memorized_spell_up_in_the_catalog_you_pass():
    wren = drained_wizard("magic_missile", "mire")
    result = drain_levels(
        wren, wren.definition, levels=1, xp_policy="halfway", stream=OneDie(2), spell_catalog=WITH_MIRE
    )
    assert wren.level == 2
    assert copies(wren) == ["magic_missile"]
    assert [event.spell_id for event in result.events if isinstance(event, SpellForgottenEvent)] == ["mire"]


def test_drain_levels_refuses_a_memorized_spell_its_catalog_doesnt_have_before_anything_changes():
    wren = drained_wizard("magic_missile", "mire")
    before = wren.model_dump()
    with pytest.raises(ValueError, match="mire"):
        drain_levels(wren, wren.definition, levels=1, xp_policy="halfway", stream=OneDie())
    assert wren.model_dump() == before


def test_a_monsters_energy_drain_takes_the_catalog_too():
    stream = RngStreams(master_seed=9).get("monster_spawn")
    wight = spawn_monster(load_monsters().get("wight"), id="monster-0001", stream=stream)
    wren = drained_wizard("magic_missile", "mire")
    events = resolve_energy_drain(wight, wren, stream=OneDie(2), spell_catalog=WITH_MIRE)
    assert wren.level == 2
    assert copies(wren) == ["magic_missile"]
    assert [event.spell_id for event in events if isinstance(event, SpellForgottenEvent)] == ["mire"]

    unknown = drained_wizard("magic_missile", "mire")
    before = unknown.model_dump()
    with pytest.raises(ValueError, match="mire"):
        resolve_energy_drain(wight, unknown, stream=OneDie())
    assert unknown.model_dump() == before


def touched_by_a_wight(*spells: SpellTemplate, memorized: tuple[str, ...]) -> Character:
    """Wren, at level 3 with `memorized` prepared, after a wight's first touch in a session's battle."""
    game = pf.battle((("Wren", "magic_user"),), template_id="wight", count=1, distance=5, adventure=adventure(*spells))
    wren = game.party.members[0]
    assert wren.level == 3, "precondition: a level with a second-level slot"
    wren.memorized_spells = tuple(MemorizedSpell(spell_id=spell_id) for spell_id in memorized)
    for _ in range(40):
        events = pf.play(game, pf.declare(wren.id, "hold"))
        if any(event.code == "combat.drain.drained" for event in events):
            assert wren.level == 2
            return wren
    raise AssertionError("precondition: the wight never hit")


def test_a_wights_touch_drains_a_caster_who_memorized_a_bundled_spell():
    wren = touched_by_a_wight(MIRE, memorized=("magic_missile", "mire"))
    assert copies(wren) == ["magic_missile"]


def test_a_drain_leaves_a_memorized_spell_the_session_doesnt_have():
    """The session can't tell the level of `mire`, so the copy takes no slot and stays where it was."""
    wren = touched_by_a_wight(memorized=("mire", "magic_missile", "web", "sleep"))
    assert copies(wren) == ["mire", "magic_missile", "sleep"]


def test_a_cursed_scrolls_drain_reads_the_sessions_catalog():
    for seed in range(1, 200):
        party = Party(members=[drained_wizard("magic_missile", "mire")])
        party.members[0].id = None
        game = GameSession.new(party, adventure(MIRE), seed=seed)
        wren = game.party.members[0]
        scroll = MagicItemInstance(instance_id="magic-item-9001", template_id="cursed_scroll")
        wren.inventory.items.append(scroll)
        assert game.execute(GrantItem(character_id=wren.id, item_id="torch", quantity=6)).accepted
        assert game.execute(GrantItem(character_id=wren.id, item_id="tinder_box")).accepted
        assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
        for _ in range(20):
            if game.party_light()[0]:
                break
            game.execute(LightSource(character_id=wren.id, item_id="torch"))
        result = game.execute(UseItem(character_id=wren.id, item_id=scroll.instance_id))
        assert result.accepted, [rejection.code for rejection in result.rejections]
        if wren.level == 2:
            assert copies(wren) == ["magic_missile"]
            return
    raise AssertionError("precondition: no seed rolled the energy drain curse")
