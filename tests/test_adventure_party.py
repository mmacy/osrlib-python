"""The party an adventure is written for: `PartySpec` and `Adventure.party`."""

import json

import pytest
from pydantic import ValidationError

from crawl_fixtures import build_adventure
from osrlib.crawl.adventure import Adventure, PartySpec
from osrlib.versioning import SCHEMA_VERSION, check_document, stamp_document

CHUNK = pytest.mark.xfail(reason="chunk: party-spec", raises=NotImplementedError)


@CHUNK
def test_levels_and_size():
    party = PartySpec(min_level=1, max_level=3, min_size=6, max_size=8)
    assert (party.min_level, party.max_level, party.min_size, party.max_size) == (1, 3, 6, 8)


@CHUNK
def test_size_is_optional():
    party = PartySpec(min_level=2, max_level=2)
    assert (party.min_size, party.max_size) == (None, None)
    assert PartySpec(min_level=1, max_level=1, min_size=4).max_size is None
    assert PartySpec(min_level=1, max_level=1, max_size=4).min_size is None


@CHUNK
@pytest.mark.parametrize(
    "fields",
    [
        {"min_level": 3, "max_level": 1},
        {"min_level": 1, "max_level": 3, "min_size": 8, "max_size": 6},
    ],
)
def test_ranges_out_of_order_are_refused(fields):
    with pytest.raises(ValidationError):
        PartySpec(**fields)


@pytest.mark.parametrize(
    "fields",
    [
        {"min_level": 0, "max_level": 3},
        {"min_level": 1, "max_level": 0},
        {"min_level": 1, "max_level": 3, "min_size": 0},
        {"min_level": 1, "max_level": 3, "max_size": 0},
    ],
)
def test_zero_is_refused(fields):
    with pytest.raises(ValidationError):
        PartySpec(**fields)


@CHUNK
def test_party_is_frozen():
    party = PartySpec(min_level=1, max_level=3)
    with pytest.raises(ValidationError):
        party.max_level = 4  # type: ignore[misc]


def test_an_adventure_without_a_party_has_none():
    assert build_adventure().party is None


@CHUNK
def test_party_round_trips_through_a_stamped_document():
    adventure = build_adventure().model_copy(
        update={"party": PartySpec(min_level=1, max_level=3, min_size=6, max_size=8)}
    )
    document = json.loads(json.dumps(stamp_document("adventure", adventure.model_dump(mode="json"))))
    assert document["schema_version"] == SCHEMA_VERSION
    restored = Adventure.model_validate(check_document(document, "adventure"))
    assert restored.party == adventure.party
    assert restored == adventure


def test_a_document_without_the_key_still_loads():
    payload = build_adventure().model_dump(mode="json")
    del payload["party"]
    assert Adventure.model_validate(payload).party is None
