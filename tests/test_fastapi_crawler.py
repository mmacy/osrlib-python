"""The FastAPI example's suite: playthrough, concurrency, handshake, statuses, wire leaks.

Every test drives the app through its HTTP surface with the TestClient — the same
ASGI app `uvicorn examples.fastapi_crawler:app` serves. The wire-leak tests apply
the leak property test's assertions to serialized responses: whatever the endpoints
return is everything a player can ever see.
"""

import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from osrlib.core.character import CHARACTER_CREATION_STREAM, party_to_document
from osrlib.core.items import ValuableInstance
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.commands import SessionMode
from osrlib.versioning import SCHEMA_VERSION, engine_version

REPO_ROOT = Path(__file__).resolve().parent.parent


def _import_example():
    """Import the example lazily — it is repo content, not an installed package."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from examples.fastapi_crawler import app
    from examples.fastapi_crawler.app import _saves, _sessions
    from examples.tui_crawler.create import scripted_party

    return app, _saves, _sessions, scripted_party


app, _saves, _sessions, scripted_party = _import_example()

# 13+ digit seed: its decimal rendering can't collide with content numbers, so
# "the seed never crosses the wire" is assertable on raw response text.
MASTER_SEED = 20_260_704_000_001


@pytest.fixture(autouse=True)
def clean_stores():
    _sessions.clear()
    _saves.clear()
    yield
    _sessions.clear()
    _saves.clear()


@pytest.fixture
def client():
    return TestClient(app)


def build_party_document(seed: int = 1) -> dict:
    streams = RngStreams(master_seed=seed)
    party = scripted_party(streams.get(CHARACTER_CREATION_STREAM), Ruleset())
    return party_to_document(party.members)


def create_session(client, *, seed: int = MASTER_SEED) -> str:
    response = client.post("/sessions", json={"party_document": build_party_document(), "seed": seed})
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


def run(client, session_id: str, command: dict, *, capture: list | None = None) -> dict:
    response = client.post(f"/sessions/{session_id}/commands", json=command)
    assert response.status_code == 200, response.text
    if capture is not None:
        capture.append(response.text)
    return response.json()


def get_view(client, session_id: str, *, capture: list | None = None) -> dict:
    response = client.get(f"/sessions/{session_id}/view")
    assert response.status_code == 200, response.text
    if capture is not None:
        capture.append(response.text)
    return response.json()


def first_weapon_id(member_view: dict) -> str | None:
    for instance in member_view["inventory"]["wielded"]:
        template = instance.get("template")
        if template is not None:
            return template["id"]
        return instance.get("instance_id")
    return None


def fight(client, session_id: str, *, capture: list | None = None) -> list[dict]:
    """The TUI's auto-declared battle loop, driven purely from the wire.

    Every input a declaration needs — member ids, the group id, the distance, the
    wielded weapon — comes from the player view: the example's proof that the
    projection carries a fighting client's whole vocabulary.
    """
    results = [run(client, session_id, {"command_type": "engage_battle"}, capture=capture)]
    for _ in range(40):
        view = get_view(client, session_id, capture=capture)
        encounter = view["encounter"]
        if encounter is None or not encounter["in_battle"]:
            break
        group = next((entry for entry in encounter["groups"] if entry["count"] > 0), None)
        if group is None:
            break
        living = [member for member in view["party"] if "dead" not in member["conditions"]]
        declarations = []
        for index, member in enumerate(living):
            if group["distance_feet"] > 5:
                declarations.append(
                    {
                        "character_id": member["id"],
                        "action": "move",
                        "move": "close",
                        "target_group_id": group["id"],
                    }
                )
            elif index < 2:
                declarations.append(
                    {
                        "character_id": member["id"],
                        "action": "attack",
                        "target_group_id": group["id"],
                        "weapon_id": first_weapon_id(member),
                    }
                )
            else:
                declarations.append({"character_id": member["id"], "action": "hold"})
        results.append(
            run(
                client,
                session_id,
                {"command_type": "resolve_battle_round", "declarations": declarations},
                capture=capture,
            )
        )
    return results


class TestHandshake:
    def test_create_returns_the_schema_handshake_and_never_the_seed(self, client):
        response = client.post("/sessions", json={"party_document": build_party_document(), "seed": MASTER_SEED})
        assert response.status_code == 200
        body = response.json()
        assert body["schema_version"] == SCHEMA_VERSION
        assert body["engine_version"] == engine_version()
        assert set(body) == {"session_id", "schema_version", "engine_version"}
        assert str(MASTER_SEED) not in response.text

    def test_metadata_handshake(self, client):
        session_id = create_session(client)
        response = client.get(f"/sessions/{session_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["schema_version"] == SCHEMA_VERSION
        assert body["engine_version"] == engine_version()
        assert body["mode"] == "town"
        assert body["clock_rounds"] == 0

    def test_server_draws_the_seed_when_the_client_sends_none(self, client):
        response = client.post("/sessions", json={"party_document": build_party_document()})
        assert response.status_code == 200
        assert "seed" not in response.json()


class TestScriptedPlaythrough:
    def test_the_shortened_barrow_script(self, client):
        """Create, delve, fight the keyed goblins, take the idol, come home to victory."""
        session_id = create_session(client)
        result = run(client, session_id, {"command_type": "enter_dungeon", "dungeon_id": "barrow"})
        assert result["accepted"], result
        # The threshold activates the adventure's quest — authored data, played by
        # the interpreter the server registers, projected in the player's own view.
        activated = [event for event in result["events"] if event["code"] == "session.quest.activated"]
        assert activated and activated[0]["name"] == "The Jade Idol"
        view = get_view(client, session_id)
        assert [quest["id"] for quest in view["quests"]] == ["the-idol"]
        assert [objective["state"] for objective in view["quests"][0]["objectives"]] == ["incomplete", "incomplete"]
        # East twice: the guard room's keyed goblins spawn an encounter.
        run(client, session_id, {"command_type": "move_party", "direction": "east"})
        run(client, session_id, {"command_type": "move_party", "direction": "east"})
        view = get_view(client, session_id)
        assert view["encounter"] is not None
        assert view["encounter"]["groups"][0]["label"] == "Goblin"
        fight(client, session_id)
        view = get_view(client, session_id)
        assert view["encounter"] is None and view["mode"] == "exploring"
        # On to the shrine and the idol.
        run(client, session_id, {"command_type": "move_party", "direction": "east"})
        run(client, session_id, {"command_type": "move_party", "direction": "east"})
        view = get_view(client, session_id)
        gold_before = sum(member["inventory"]["purse"]["gp"] for member in view["party"])
        result = run(client, session_id, {"command_type": "take_treasure", "feature_id": "idol_shrine"})
        assert result["accepted"], result
        acquired = [event for event in result["events"] if event["code"] == "exploration.item.acquired"]
        assert acquired, result["events"]
        # The idol is a bundled item, so the acquisition reports a catalog id and the
        # quest's first objective completes on it. The cache's 50 gp is all the coin
        # that changes hands here: the temple pays on delivery.
        completed = [event for event in result["events"] if event["code"] == "session.quest.objective_completed"]
        assert [event["objective_id"] for event in completed] == ["recover-idol"]
        view = get_view(client, session_id)
        gold_after = sum(member["inventory"]["purse"]["gp"] for member in view["party"])
        assert gold_after == gold_before + 50
        carried = [
            instance["template"]["id"]
            for member in view["party"]
            for instance in member["inventory"]["items"]
            if instance.get("template")
        ]
        assert "jade-idol" in carried
        assert [objective["state"] for objective in view["quests"][0]["objectives"]] == ["complete", "incomplete"]
        # Home: west to the entrance, then the town travel fires the award — and,
        # behind it, the homecoming that completes the quest and ends the adventure.
        for _ in range(4):
            run(client, session_id, {"command_type": "move_party", "direction": "west"})
        result = run(client, session_id, {"command_type": "travel_to_town"})
        assert result["accepted"], result
        award = [event for event in result["events"] if event["code"] == "session.xp.adventure_award"]
        assert award and award[0]["treasure_xp"] > 0
        codes = [event["code"] for event in result["events"]]
        assert codes.index("session.xp.adventure_award") < codes.index("session.quest.completed")
        assert "session.adventure.completed" in codes
        view = get_view(client, session_id)
        assert view["mode"] == "victory"
        assert view["quests"] == [], "a finished quest leaves the list; its record is the journal"
        assert any("The almoner counts out the reward" in entry["text"] for entry in view["journal"])

    def test_town_services_over_the_wire(self, client):
        """Sell and heal on a session the errand has not ended yet."""
        session_id = create_session(client)
        # A valuable the party never had to delve for: the walkthrough above carries
        # the idol home instead, and a concluded adventure sells nothing.
        session, _lock = _sessions[session_id]
        member = session.party.members[0]
        member.inventory.valuables.append(
            ValuableInstance(instance_id="valuable-7001", kind="gem", value_gp=120, weight_coins=1)
        )
        view = get_view(client, session_id)
        instance_ids = [
            valuable["instance_id"]
            for member_view in view["party"]
            for valuable in member_view["inventory"]["valuables"]
        ]
        assert instance_ids == ["valuable-7001"]
        result = run(client, session_id, {"command_type": "sell_treasure", "item_ids": instance_ids})
        assert result["accepted"], result
        result = run(
            client,
            session_id,
            {"command_type": "purchase_healing", "character_id": "character-0001", "service": "cure_light_wounds"},
        )
        assert result["accepted"], result

    def test_the_town_is_closed_once_the_adventure_is_won(self, client):
        """Victory is terminal over the wire too: play refuses, the record stands."""
        session_id = create_session(client)
        session, _lock = _sessions[session_id]
        session.mode = SessionMode.VICTORY
        result = run(
            client,
            session_id,
            {"command_type": "purchase_healing", "character_id": "character-0001", "service": "cure_light_wounds"},
        )
        assert not result["accepted"]
        assert [rejection["code"] for rejection in result["rejections"]] == ["session.command.wrong_mode"]
        assert get_view(client, session_id)["mode"] == "victory"

    def test_save_and_restore_round_trip(self, client):
        session_id = create_session(client)
        run(client, session_id, {"command_type": "enter_dungeon", "dungeon_id": "barrow"})
        run(client, session_id, {"command_type": "move_party", "direction": "east"})
        response = client.post(f"/sessions/{session_id}/save")
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"save_id"}  # the save document never crosses the wire
        restored = client.post("/sessions", json={"save_id": body["save_id"]})
        assert restored.status_code == 200
        restored_id = restored.json()["session_id"]
        assert restored_id != session_id
        assert get_view(client, restored_id) == get_view(client, session_id)


class TestConcurrency:
    def test_racing_threads_serialize_on_the_session_lock(self, client):
        """Commands race from many threads; the lock makes their effects a clean sum.

        No cross-thread order is asserted — no lock promises one. What the lock
        does promise: every command lands whole (accepted or cleanly rejected),
        and the clock ends at exactly the accepted total, with no lost updates.
        """
        session_id = create_session(client)
        threads, statuses, bodies = [], [], []
        per_thread, thread_count = 5, 8

        def hammer():
            for _ in range(per_thread):
                response = client.post(
                    f"/sessions/{session_id}/commands",
                    json={"command_type": "advance_time", "n": 1, "unit": "round"},
                )
                statuses.append(response.status_code)
                bodies.append(response.json())

        for _ in range(thread_count):
            threads.append(threading.Thread(target=hammer))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert statuses == [200] * (per_thread * thread_count)
        assert all(body["accepted"] for body in bodies)
        metadata = client.get(f"/sessions/{session_id}").json()
        assert metadata["clock_rounds"] == per_thread * thread_count


class TestStatusMapping:
    def test_in_fiction_rejection_is_a_200(self, client):
        session_id = create_session(client)
        result = run(client, session_id, {"command_type": "move_party", "direction": "east"})
        assert result["accepted"] is False
        assert result["rejections"][0]["code"] == "session.command.wrong_mode"
        assert result["events"] == []

    def test_unknown_command_type_is_a_422(self, client):
        session_id = create_session(client)
        response = client.post(f"/sessions/{session_id}/commands", json={"command_type": "dance"})
        assert response.status_code == 422

    def test_malformed_known_command_is_a_422(self, client):
        session_id = create_session(client)
        response = client.post(
            f"/sessions/{session_id}/commands", json={"command_type": "move_party", "direction": "up"}
        )
        assert response.status_code == 422

    def test_malformed_party_document_is_a_422(self, client):
        response = client.post("/sessions", json={"party_document": {"kind": "party"}})
        assert response.status_code == 422

    def test_newer_schema_party_document_is_a_409(self, client):
        document = build_party_document()
        document["schema_version"] = SCHEMA_VERSION + 1
        response = client.post("/sessions", json={"party_document": document})
        assert response.status_code == 409

    def test_newer_schema_save_in_the_store_is_a_409(self, client):
        session_id = create_session(client)
        save_id = client.post(f"/sessions/{session_id}/save").json()["save_id"]
        _saves[save_id]["schema_version"] = SCHEMA_VERSION + 1
        response = client.post("/sessions", json={"save_id": save_id})
        assert response.status_code == 409

    def test_unknown_ids_are_404s(self, client):
        assert client.get("/sessions/nope").status_code == 404
        assert client.get("/sessions/nope/view").status_code == 404
        assert client.post("/sessions/nope/commands", json={"command_type": "wait"}).status_code == 404
        assert client.post("/sessions/nope/save").status_code == 404
        assert client.post("/sessions", json={"save_id": "nope"}).status_code == 404

    def test_neither_party_nor_save_is_a_422(self, client):
        assert client.post("/sessions", json={}).status_code == 422
        assert (
            client.post("/sessions", json={"party_document": build_party_document(), "save_id": "both"}).status_code
            == 422
        )

    def test_anything_else_is_a_500(self, client, monkeypatch):
        session_id = create_session(client)

        def explode(session):
            raise RuntimeError("boom")

        # The package's `app` attribute (the ASGI object) shadows the `app`
        # submodule on attribute access; reach the module through sys.modules.
        monkeypatch.setattr(sys.modules["examples.fastapi_crawler.app"], "save_game", explode)
        crashing_client = TestClient(app, raise_server_exceptions=False)
        assert crashing_client.post(f"/sessions/{session_id}/save").status_code == 500


class TestWireLeaks:
    """The leak property test's assertions, applied to every serialized response."""

    def test_no_endpoint_ever_leaks(self, client):
        from osrlib.core.items import MagicItemInstance

        session_id = create_session(client)
        # Plant unidentified magic items server-side (referee content the wire
        # client must never see through), exactly like the leak property test.
        session, _lock = _sessions[session_id]
        member = session.party.members[0]
        member.inventory.items.append(
            MagicItemInstance(instance_id="magic-item-8001", template_id="potion_of_giant_strength")
        )
        member.inventory.items.append(
            MagicItemInstance(
                instance_id="magic-item-8002",
                template_id="wand_of_fire_balls",
                charges_remaining=7,
                state={"secret": 1},
            )
        )
        captured: list[str] = []
        run(client, session_id, {"command_type": "enter_dungeon", "dungeon_id": "barrow"}, capture=captured)
        run(client, session_id, {"command_type": "move_party", "direction": "east"}, capture=captured)
        run(client, session_id, {"command_type": "move_party", "direction": "east"}, capture=captured)
        fight(client, session_id, capture=captured)
        run(client, session_id, {"command_type": "move_party", "direction": "east"}, capture=captured)
        run(client, session_id, {"command_type": "move_party", "direction": "east"}, capture=captured)
        run(client, session_id, {"command_type": "take_treasure", "feature_id": "idol_shrine"}, capture=captured)
        get_view(client, session_id, capture=captured)
        captured.append(client.get(f"/sessions/{session_id}").text)
        captured.append(client.post(f"/sessions/{session_id}/save").text)
        blob = "\n".join(captured)
        # The master seed is a server secret.
        assert str(MASTER_SEED) not in blob
        # Unidentified items mask: no true ids, no charges, no per-item state.
        assert "potion_of_giant_strength" not in blob
        assert "wand_of_fire_balls" not in blob
        assert "charges" not in blob and '"secret"' not in blob
        # Referee bookkeeping: no flag store, no generated-cache ids, no save internals.
        assert '"flags"' not in blob
        assert "cache-" not in blob
        assert "master_seed" not in blob and "rng_streams" not in blob
        # Referee-visibility outcomes never appear in command results.
        assert "exploration.detection.rolled" not in blob
        assert '"referee"' not in blob
        # Authored wiring the adventure carries: the quest's clauses and rewards, and
        # the levels' ambient narrator guidance, are the game's side of the screen.
        assert "guidance" not in blob and "Grave goods" not in blob
        assert "pattern_type" not in blob and "rewards" not in blob
        # Unexplored geometry and monster HP ride the same guarantee: /view
        # returns session.view(Visibility.PLAYER) verbatim, whose projection the
        # leak property test (test_crawl_properties.test_the_player_view_never_leaks)
        # fuzzes for exactly those two — this suite adds nothing to the projection,
        # so its assertions compose with that one at the wire.

    def test_every_returned_event_is_player_visible(self, client):
        session_id = create_session(client)
        result = run(client, session_id, {"command_type": "enter_dungeon", "dungeon_id": "barrow"})
        events = result["events"]
        assert events, "entering the dungeon reports something"
        assert all(event["visibility"] == "player" for event in events)

    def test_no_referee_view_endpoint_exists(self, client):
        session_id = create_session(client)
        assert client.get(f"/sessions/{session_id}/referee").status_code in (404, 405)
        paths = {route.path for route in app.routes}
        assert not any("referee" in path for path in paths)
