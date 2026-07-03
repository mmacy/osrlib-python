"""Tests for stamped documents: round-trips, version gates, and the additive-schema contract."""

import pytest

from osrlib.core.abilities import AbilityScore
from osrlib.core.character import Character, party_from_document, party_to_document
from osrlib.errors import ContentValidationError, SaveVersionError
from osrlib.versioning import SCHEMA_VERSION, check_document, engine_version, stamp_document


def make_character(name: str = "Test") -> Character:
    return Character(
        name=name,
        class_id="fighter",
        race="human",
        level=1,
        xp=0,
        scores={ability: 11 for ability in AbilityScore},
        alignment="neutral",
        max_hp=6,
        current_hp=6,
    )


class TestStamping:
    def test_stamp_carries_kind_and_both_versions(self):
        document = stamp_document("character", {"name": "X"})
        assert document["kind"] == "character"
        assert document["schema_version"] == SCHEMA_VERSION
        assert document["engine_version"] == engine_version()
        assert document["payload"] == {"name": "X"}

    def test_check_returns_payload(self):
        document = stamp_document("character", {"name": "X"})
        assert check_document(document, "character") == {"name": "X"}

    def test_kind_mismatch_raises(self):
        document = stamp_document("party", {})
        with pytest.raises(ContentValidationError):
            check_document(document, "character")

    def test_newer_schema_version_raises_save_version_error(self):
        document = stamp_document("character", {})
        document["schema_version"] = SCHEMA_VERSION + 1
        with pytest.raises(SaveVersionError):
            check_document(document, "character")

    def test_malformed_envelopes_raise(self):
        with pytest.raises(ContentValidationError):
            check_document("not a mapping", "character")
        with pytest.raises(ContentValidationError):
            check_document({"kind": "character"}, "character")
        with pytest.raises(ContentValidationError):
            check_document({"kind": "character", "schema_version": "1", "payload": {}}, "character")
        with pytest.raises(ContentValidationError):
            check_document({"kind": "character", "schema_version": 1, "payload": []}, "character")

    def test_unknown_envelope_keys_are_ignored(self):
        document = stamp_document("character", {})
        document["future_envelope_field"] = True
        assert check_document(document, "character") == {}

    def test_empty_kind_raises(self):
        with pytest.raises(ValueError):
            stamp_document("", {})


class TestCharacterDocuments:
    def test_round_trip(self):
        hero = make_character()
        document = hero.to_document()
        assert Character.from_document(document) == hero

    def test_round_trip_survives_json(self):
        import json

        hero = make_character()
        document = json.loads(json.dumps(hero.to_document()))
        assert Character.from_document(document) == hero

    def test_unknown_payload_fields_are_ignored(self):
        # The additive-schema contract: a newer minor writer may add fields.
        document = make_character().to_document()
        document["payload"]["future_field"] = "ignored"
        assert Character.from_document(document) == make_character()

    def test_malformed_payload_raises_content_error(self):
        document = make_character().to_document()
        document["payload"]["level"] = 40
        with pytest.raises(ContentValidationError):
            Character.from_document(document)


class TestPartyDocuments:
    def test_round_trip_preserves_order(self):
        party = [make_character("A"), make_character("B"), make_character("C")]
        loaded = party_from_document(party_to_document(party))
        assert [character.name for character in loaded] == ["A", "B", "C"]
        assert loaded == party

    def test_newer_schema_version_raises(self):
        document = party_to_document([make_character()])
        document["schema_version"] = SCHEMA_VERSION + 1
        with pytest.raises(SaveVersionError):
            party_from_document(document)

    def test_missing_characters_list_raises(self):
        document = stamp_document("party", {"members": []})
        with pytest.raises(ContentValidationError):
            party_from_document(document)
