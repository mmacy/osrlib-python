"""Tests for osrlib.core.events and osrlib.versioning — the emission and stamping contracts."""

from importlib import metadata

import pytest
from pydantic import ValidationError

import osrlib
from osrlib.core.events import Event, Visibility
from osrlib.versioning import SCHEMA_VERSION, engine_version

VALID_CODES = [
    "combat.attack.hit",
    "exploration.torch.expired",
    "a.b",
    "combat.attack_roll.natural_20",
    "x2.y_3",
]

INVALID_CODES = [
    "combat",  # one segment
    "Combat.attack",  # uppercase
    "combat.Attack",
    "combat..hit",  # empty segment
    ".combat.hit",
    "combat.hit.",
    "combat.2attack",  # segment must start with a letter
    "combat._attack",
    "combat.att ack",
    "combat.attack-roll",
    "",
]


class TestVisibility:
    def test_wire_values(self):
        # Lowercase, serialized into every event; changing them is a schema_version bump.
        assert Visibility.PLAYER.value == "player"
        assert Visibility.REFEREE.value == "referee"
        assert len(Visibility) == 2


class TestEventContract:
    @pytest.mark.parametrize("code", VALID_CODES)
    def test_valid_codes_accepted(self, code):
        assert Event(code=code, visibility=Visibility.PLAYER).code == code

    @pytest.mark.parametrize("code", INVALID_CODES)
    def test_invalid_codes_rejected(self, code):
        with pytest.raises(ValidationError):
            Event(code=code, visibility=Visibility.PLAYER)

    def test_events_are_frozen(self):
        event = Event(code="combat.attack.hit", visibility=Visibility.PLAYER)
        with pytest.raises(ValidationError):
            event.visibility = Visibility.REFEREE

    def test_unknown_fields_are_ignored_on_deserialization(self):
        # The additive-schema contract: consumers must tolerate fields they don't know.
        payload = {
            "code": "combat.attack.hit",
            "visibility": "player",
            "field_from_the_future": 42,
            "another_one": {"nested": True},
        }
        event = Event.model_validate(payload)
        assert event.code == "combat.attack.hit"
        assert event.visibility is Visibility.PLAYER
        assert not hasattr(event, "field_from_the_future")

    def test_subclasses_inherit_the_contract(self):
        class AttackRolled(Event):
            attacker_id: str
            total: int

        payload = {
            "code": "combat.attack.hit",
            "visibility": "referee",
            "attacker_id": "monster-0007",
            "total": 15,
            "unknown_field": "ignored",
        }
        event = AttackRolled.model_validate(payload)
        assert event.attacker_id == "monster-0007"
        with pytest.raises(ValidationError):
            event.total = 20

    def test_visibility_serializes_to_wire_value(self):
        event = Event(code="combat.attack.hit", visibility=Visibility.REFEREE)
        assert event.model_dump()["visibility"] == "referee"


class TestVersioning:
    def test_schema_version(self):
        assert SCHEMA_VERSION == 1
        assert isinstance(SCHEMA_VERSION, int)

    def test_top_level_exports(self):
        assert osrlib.SCHEMA_VERSION is SCHEMA_VERSION
        assert osrlib.engine_version is engine_version

    def test_engine_version_matches_package_metadata(self):
        assert engine_version() == metadata.version("osrlib")
