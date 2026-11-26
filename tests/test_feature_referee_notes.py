"""Who reads a feature's text: the players read `description`, and only the referee reads `referee_notes` (#318).

Since 2.6.0 the player view shows a cache's authored description from the moment the cache comes
into view, before anyone opens it and after the party empties it. `FeatureSpec.referee_notes` is
where an author puts what the players must not read. It lives in the adventure document, so a save
and the referee view carry it, and no player view or event ever does.

These tests pass on the field's declaration alone, because osrlib reads nothing from it. They are
guards: they fail if a later change shows the notes to the players, or stops showing the
description.
"""

import json

from crawl_fixtures import build_party
from osrlib.core.events import Visibility
from osrlib.core.items import Coins
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import (
    EnterDungeon,
    GrantItem,
    InspectTreasure,
    LightSource,
    MoveParty,
    RemoveTreasureTrap,
    TakeTreasure,
)
from osrlib.crawl.dungeon import (
    AreaSpec,
    Direction,
    DungeonSpec,
    Edge,
    EdgeKind,
    FeatureSpec,
    LevelSpec,
    TrapEffect,
    TrapSpec,
    WanderingSpec,
)
from osrlib.crawl.session import GameSession
from osrlib.persistence import load_game, save_game

DESCRIPTION = "An iron-bound strongbox squats in the corner, its hasp furred with rust."
REFEREE_NOTES = "Forty gold crowns and a coil of rope. A needle in the hasp carries a sleeping venom."
THIEF = "character-0002"

# The thief's find and remove treasure traps chance at first level is 10%. On seed 4 the
# inspection rolls 4, which finds the needle, and the removal rolls 25, which fails and springs it
# under the default `failed_trap_removal_springs`. So the scenario walks the cache through found,
# sprung, and emptied.
SEED = 4


def build_vault_adventure(*, referee_notes: str = REFEREE_NOTES) -> Adventure:
    """A four-cell corridor whose east half is a vault holding one trapped strongbox.

    The strongbox has no cell, so it belongs to the whole vault and comes into view only when the
    party walks into the vault. Its trap has a manual effect, which hurts nobody.
    """
    strongbox = FeatureSpec(
        id="strongbox",
        kind="treasure_cache",
        description=DESCRIPTION,
        referee_notes=referee_notes,
        item_ids=("rope",),
        coins=Coins(gp=40),
        trap=TrapSpec(kind="treasure", trigger="open", effect=TrapEffect(manual="The thief sleeps for a turn.")),
    )
    vault = AreaSpec(id="vault", name="Vault", cells=((2, 0), (3, 0)), features=(strongbox,))
    level = LevelSpec(
        number=1,
        width=4,
        height=1,
        entrance=(0, 0),
        edges={f"{x},0:west": Edge(kind=EdgeKind.OPEN) for x in range(1, 4)},
        areas=(vault,),
        wandering=WanderingSpec(chance_in_six=0),
    )
    return Adventure(
        name="The strongbox",
        town=TownSpec(name="Threshold", travel_turns={"crypt": 1}),
        dungeons=(DungeonSpec(id="crypt", name="Crypt", levels=(level,)),),
    )


def strongbox_spec(adventure: Adventure) -> FeatureSpec:
    return adventure.dungeons[0].levels[0].areas[0].features[0]


def lit_session(seed: int = SEED) -> GameSession:
    """The stock party at the crypt's entrance with a torch burning, so the thief can work."""
    session = GameSession.new(build_party(), build_vault_adventure(), seed=seed)
    session.execute(GrantItem(character_id="character-0001", item_id="torch", quantity=6))
    session.execute(GrantItem(character_id="character-0001", item_id="tinder_box"))
    session.execute(GrantItem(character_id=THIEF, item_id="thieves_tools"))
    assert session.execute(EnterDungeon(dungeon_id="crypt")).accepted
    for _ in range(20):  # tinder lights on a 1 or 2 in 6, so retry until the torch takes
        lit = session.execute(LightSource(character_id="character-0001", item_id="torch"))
        if any(event.code == "exploration.light.lit" for event in lit.events):
            return session
    raise AssertionError("the torch never lit in twenty tinder attempts")


def cache_views(session: GameSession) -> list:
    view = session.view(Visibility.PLAYER)
    return [feature for level in view.explored for feature in level.features if feature.kind == "treasure_cache"]


def assert_notes_hidden_from_players(session: GameSession) -> None:
    """Neither the whole player view nor any event the session has emitted contains the notes."""
    assert REFEREE_NOTES not in session.view(Visibility.PLAYER).model_dump_json()
    for event in session.event_log:
        assert REFEREE_NOTES not in event.model_dump_json(), event


def assert_referee_reads_notes(session: GameSession) -> None:
    referee = session.view(Visibility.REFEREE)
    assert strongbox_spec(referee.adventure).referee_notes == REFEREE_NOTES
    assert REFEREE_NOTES in referee.model_dump_json()


def assert_players_read_description(session: GameSession, trap_state: str | None) -> str:
    """The strongbox is on the player map with its description, and its handle comes back."""
    (cache,) = cache_views(session)
    assert cache.description == DESCRIPTION
    assert cache.area_id == "vault"
    assert cache.trap_state == trap_state
    return cache.id


class TestTheDocument:
    def test_referee_notes_default_to_empty(self):
        assert FeatureSpec(id="chest", kind="treasure_cache").referee_notes == ""

    def test_referee_notes_round_trip_through_an_adventure_document(self):
        adventure = build_vault_adventure()
        text = adventure.model_dump_json()
        assert json.loads(text)["dungeons"][0]["levels"][0]["areas"][0]["features"][0]["referee_notes"] == REFEREE_NOTES
        loaded = Adventure.model_validate_json(text)
        assert loaded == adventure
        assert strongbox_spec(loaded).referee_notes == REFEREE_NOTES

    def test_an_adventure_document_without_the_field_loads_unchanged(self):
        # A document written before the field existed has no `referee_notes` key on any feature.
        payload = build_vault_adventure().model_dump(mode="json")
        feature = payload["dungeons"][0]["levels"][0]["areas"][0]["features"][0]
        del feature["referee_notes"]
        loaded = Adventure.model_validate(payload)
        assert strongbox_spec(loaded).referee_notes == ""
        assert strongbox_spec(loaded).description == DESCRIPTION
        assert loaded == build_vault_adventure(referee_notes="")

    def test_referee_notes_survive_a_save(self):
        session = lit_session()
        restored = load_game(json.loads(json.dumps(save_game(session))))
        assert strongbox_spec(restored.adventure).referee_notes == REFEREE_NOTES
        assert_referee_reads_notes(restored)
        assert_notes_hidden_from_players(restored)


class TestFindOpenAndEmpty:
    """Walk the strongbox from out of sight to emptied, and read both views at every step."""

    def test_the_players_read_the_description_and_never_the_notes(self):
        session = lit_session()

        # Out of sight: the party stands in the corridor, and the vault is unentered.
        assert cache_views(session) == []
        assert_notes_hidden_from_players(session)
        assert_referee_reads_notes(session)

        # Found: the party walks into the vault, and the strongbox comes into view.
        assert session.execute(MoveParty(direction=Direction.EAST)).accepted
        assert session.execute(MoveParty(direction=Direction.EAST)).accepted
        handle = assert_players_read_description(session, trap_state=None)
        assert_notes_hidden_from_players(session)
        assert_referee_reads_notes(session)

        # Inspected: the thief finds the needle.
        inspected = session.execute(InspectTreasure(character_id=THIEF, feature_id=handle))
        assert [event.code for event in inspected.events][:2] == [
            "exploration.detection.rolled",
            "exploration.trap.found",
        ]
        assert_players_read_description(session, trap_state="found")
        assert_notes_hidden_from_players(session)
        assert_referee_reads_notes(session)

        # Sprung: the removal fails and the needle fires.
        removal = session.execute(RemoveTreasureTrap(character_id=THIEF, feature_id=handle))
        assert [event.code for event in removal.events][:2] == [
            "exploration.detection.rolled",
            "exploration.trap.sprung",
        ]
        assert_players_read_description(session, trap_state="sprung")
        assert_notes_hidden_from_players(session)
        assert_referee_reads_notes(session)

        # Opened and emptied: the party takes the coins and the rope.
        taken = session.execute(TakeTreasure(feature_id=handle))
        assert taken.accepted
        assert any(event.code == "exploration.item.acquired" for event in taken.events)
        assert session.execute(TakeTreasure(feature_id=handle)).rejections[0].code == "exploration.feature.emptied"
        assert_players_read_description(session, trap_state="sprung")
        assert_notes_hidden_from_players(session)
        assert_referee_reads_notes(session)

        # Remembered: the party walks back out of the vault, and the emptied strongbox stays on the map.
        assert session.execute(MoveParty(direction=Direction.WEST)).accepted
        assert session.execute(MoveParty(direction=Direction.WEST)).accepted
        assert_players_read_description(session, trap_state="sprung")
        assert_notes_hidden_from_players(session)
        assert_referee_reads_notes(session)
