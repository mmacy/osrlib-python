"""A character of a class you write (issue #166, item 26).

Every kernel function takes a `ClassDefinition`, but a `Character` looked its class up by id in the
built-in catalog, so a character of a custom class couldn't be built, validated, saved, or played
without reassigning a module attribute. A character now states its own class in `custom_class`, the
way a monster instance states its template, and the session reads the class from the character.
"""

import pytest
from pydantic import ValidationError

import positions_fixtures as pf
from osrlib.core.abilities import AbilityScore
from osrlib.core.alignment import Alignment
from osrlib.core.character import (
    CHARACTER_CREATION_STREAM,
    Character,
    create_character,
    party_from_document,
    party_to_document,
)
from osrlib.core.classes import ClassDefinition, level_title
from osrlib.core.events import Visibility
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.spells import MemorizedSpell
from osrlib.crawl.commands import (
    CastSpell,
    EnterDungeon,
    GrantItem,
    LightSource,
    PrepareSpells,
    Rest,
    Search,
)
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.data import load_classes
from osrlib.persistence import load_game, save_game


def custom(base_id: str, class_id: str, **changes) -> ClassDefinition:
    """A class of your own: a built-in class under a new id and name, with `changes` applied."""
    document = {**load_classes().get(base_id).model_dump(), "id": class_id, "name": class_id.title(), **changes}
    document["progression"] = document["progression"][: document["max_level"]]
    return ClassDefinition.model_validate(document)


BURGLAR = custom("thief", "burglar", level_titles=("Footpad", "Cutpurse"))
HEDGE_WIZARD = custom("magic_user", "hedge_wizard", max_level=5)


def member(definition: ClassDefinition, name: str, **fields) -> Character:
    values = {
        "name": name,
        "class_id": definition.id,
        "custom_class": definition,
        "race": definition.race,
        "level": 1,
        "xp": 0,
        "scores": {ability: 12 for ability in AbilityScore},
        "alignment": "lawful",
        "max_hp": 30,
        "current_hp": 30,
    }
    return Character.model_validate({**values, **fields})


# ---------------------------------------------------------------------- the character


def test_a_character_states_its_own_class():
    burglar = member(BURGLAR, "Nim")
    assert burglar.definition == BURGLAR
    assert burglar.class_id == "burglar"
    assert level_title(burglar.definition, burglar.level) == "Footpad"
    assert burglar.saves == BURGLAR.row(1).saves
    assert burglar.thac0 == member(load_classes().get("thief"), "Sable", custom_class=None).thac0


def test_a_built_in_class_needs_no_custom_class():
    fighter = Character.model_validate(
        {**member(BURGLAR, "Hild").model_dump(), "class_id": "fighter", "custom_class": None}
    )
    assert fighter.custom_class is None
    assert fighter.definition == load_classes().get("fighter")


def test_the_custom_class_limits_the_character_as_a_built_in_one_does():
    assert member(HEDGE_WIZARD, "Wren", level=5, xp=HEDGE_WIZARD.row(5).xp).level == 5
    with pytest.raises(ValidationError):
        member(HEDGE_WIZARD, "Wren", level=6, xp=HEDGE_WIZARD.row(5).xp)


@pytest.mark.parametrize(
    "fields",
    [
        {"class_id": "burglar", "custom_class": None},
        {"class_id": "burglar", "custom_class": HEDGE_WIZARD},
        {"class_id": "fighter", "custom_class": custom("fighter", "fighter")},
    ],
    ids=["an unknown class with no definition", "a definition for another class", "a definition under a built-in id"],
)
def test_a_class_that_doesnt_fit_the_character_is_refused(fields):
    with pytest.raises(ValidationError):
        Character.model_validate({**member(BURGLAR, "Nim").model_dump(), **fields})


def test_a_character_round_trips_with_its_class():
    burglar = member(BURGLAR, "Nim")
    assert Character.from_document(burglar.to_document()) == burglar
    assert Character.model_validate(burglar.model_dump(mode="json")) == burglar
    party = [burglar, member(HEDGE_WIZARD, "Wren")]
    assert party_from_document(party_to_document(party)) == party


# ---------------------------------------------------------------------- creating one


def test_create_character_takes_a_class_of_your_own():
    stream = RngStreams(master_seed=11).get(CHARACTER_CREATION_STREAM)
    result = create_character(
        name="Nim",
        class_id="burglar",
        custom_class=BURGLAR,
        alignment=Alignment.NEUTRAL,
        ruleset=Ruleset(),
        stream=stream,
    )
    assert result.character.custom_class == BURGLAR
    assert result.character.definition == BURGLAR
    assert 1 <= result.hit_point_roll.hit_points


def test_create_character_refuses_a_class_that_doesnt_match_before_it_rolls():
    stream = RngStreams(master_seed=11).get(CHARACTER_CREATION_STREAM)
    before = stream.export_state()
    for class_id, definition in (
        ("burglar", None),
        ("burglar", HEDGE_WIZARD),
        ("fighter", custom("fighter", "fighter")),
    ):
        with pytest.raises(ValueError):
            create_character(
                name="Nim",
                class_id=class_id,
                custom_class=definition,
                alignment=Alignment.NEUTRAL,
                ruleset=Ruleset(),
                stream=stream,
            )
    assert stream.export_state() == before


# ---------------------------------------------------------------------- playing one


def session() -> GameSession:
    """A burglar and a hedge wizard beside two stock fighters, at the mouth of the corridor."""
    wizard = member(HEDGE_WIZARD, "Wren", level=3, xp=HEDGE_WIZARD.row(3).xp, spell_book=("shield", "sleep"))
    party = Party(
        members=[pf.character("Hild", "fighter"), pf.character("Osric", "fighter"), member(BURGLAR, "Nim"), wizard]
    )
    game = GameSession.new(party, pf.corridor(), seed=7)
    first = game.party.members[0].id
    for item_id in ("torch", "tinder_box"):
        assert game.execute(GrantItem(character_id=first, item_id=item_id)).accepted
    return game


def by_name(game: GameSession, name: str) -> Character:
    return next(candidate for candidate in game.party.members if candidate.name == name)


def test_a_session_starts_and_saves_with_custom_classes():
    game = session()
    assert by_name(game, "Nim").definition == BURGLAR
    sheet = next(sheet for sheet in game.view(Visibility.PLAYER).party if sheet.name == "Nim")
    assert (sheet.class_id, sheet.title) == ("burglar", "Footpad")
    loaded = load_game(save_game(game))
    assert by_name(loaded, "Nim").definition == BURGLAR
    assert by_name(loaded, "Wren").definition == HEDGE_WIZARD


def test_a_custom_thief_searches_for_traps():
    game = session()
    assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
    first = game.party.members[0].id
    for _ in range(20):
        lit = game.execute(LightSource(character_id=first, item_id="torch"))
        if any(event.code == "exploration.light.lit" for event in lit.events):
            break
    result = game.execute(Search(character_id=by_name(game, "Nim").id, kind="room_traps"))
    assert result.accepted, [rejection.code for rejection in result.rejections]


def test_a_custom_caster_prepares_and_casts_its_spells():
    game = session()
    wizard = by_name(game, "Wren")
    assert game.execute(Rest(kind="night")).accepted
    prepared = game.execute(PrepareSpells(character_id=wizard.id, selections=(MemorizedSpell(spell_id="shield"),)))
    assert prepared.accepted, [rejection.code for rejection in prepared.rejections]
    assert [copy.spell_id for copy in wizard.memorized_spells] == ["shield"]
    cast = game.execute(CastSpell(character_id=wizard.id, spell_id="shield", mode="shield"))
    assert cast.accepted, [rejection.code for rejection in cast.rejections]
    assert wizard.memorized_spells == ()
