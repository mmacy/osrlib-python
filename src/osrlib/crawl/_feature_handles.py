"""Opaque player feature handles and current-location feature resolution."""

import hmac
from dataclasses import dataclass
from hashlib import sha256

from osrlib.crawl._party_location import _cell_ref, _feature_ref, _features_here
from osrlib.crawl.dungeon import FeatureSpec, GeneratedCache, Position, cell_ref

__all__ = []

_AUTHORED_DOMAIN = b"osrlib:player-feature-handle:authored:v1\0"
_GENERATED_DOMAIN = b"osrlib:player-feature-handle:generated:v1\0"
_SEED_KEY_DOMAIN = b"osrlib:player-feature-handle-key:v1\0"
_HANDLE_PREFIX = "feature:"


@dataclass(frozen=True)
class _ResolvedFeature:
    """One public feature candidate resolved to its internal identity."""

    raw_id: str
    public_id: str
    feature: FeatureSpec | None = None
    cache_id: str | None = None
    ref: str | None = None


def _player_handle_key(session) -> bytes:
    supplied = session._player_handle_key
    if supplied is not None:
        return supplied
    seed_bytes = str(session.master_seed).encode("ascii")
    return hmac.digest(_SEED_KEY_DOMAIN, seed_bytes, sha256)


def _encode_identity(dungeon_id: str, level_number: int, item_id: str) -> bytes:
    """Encode one structured feature identity without delimiter ambiguity."""
    encoded = []
    for value in (dungeon_id.encode(), str(level_number).encode(), item_id.encode()):
        encoded.append(len(value).to_bytes(8, "big"))
        encoded.append(value)
    return b"".join(encoded)


def _authored_feature_id(session, dungeon_id: str, level_number: int, feature_id: str) -> str:
    identity = _encode_identity(dungeon_id, level_number, feature_id)
    digest = hmac.digest(_player_handle_key(session), _AUTHORED_DOMAIN + identity, sha256)
    return _HANDLE_PREFIX + digest.hex()


def _generated_feature_id(session, dungeon_id: str, level_number: int, cache_id: str) -> str:
    identity = _encode_identity(dungeon_id, level_number, cache_id)
    digest = hmac.digest(_player_handle_key(session), _GENERATED_DOMAIN + identity, sha256)
    return _HANDLE_PREFIX + digest.hex()


def _parse_cell_location(session, ref: str) -> tuple[str, int, Position] | None:
    """Resolve a canonical cell reference against the session's adventure."""
    if not isinstance(ref, str) or not ref.startswith("cell:"):
        return None
    try:
        dungeon_id, level_text, cell_text = ref.removeprefix("cell:").rsplit(":", 2)
        x_text, y_text = cell_text.split(",", 1)
        position = (int(x_text), int(y_text))
        level_number = int(level_text)
        level = session.adventure.dungeon(dungeon_id).level(level_number)
    except TypeError, ValueError:
        return None
    if not dungeon_id or not level.in_bounds(position) or ref != cell_ref(dungeon_id, level_number, position):
        return None
    return dungeon_id, level_number, position


def _area_at_ref(session, ref: str):
    matches = [
        (dungeon.id, level.number, area)
        for dungeon in session.adventure.dungeons
        for level in dungeon.levels
        for area in level.areas
        if f"{dungeon.id}:{level.number}:{area.id}" == ref
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def _lair_cache_available(session, cache: GeneratedCache) -> bool:
    parsed_cache = _parse_cell_location(session, cache.cell_ref)
    parsed_area = _area_at_ref(session, cache.lair_area_ref or "")
    if parsed_cache is None or parsed_area is None:
        return False
    dungeon_id, level_number, position = parsed_cache
    area_dungeon_id, area_level_number, area = parsed_area
    return (
        (dungeon_id, level_number) == (area_dungeon_id, area_level_number)
        and area.encounter is not None
        and position in area.cells
        and cache.lair_area_ref in session.dungeon_state.resolved_encounters
    )


def _cache_available(session, cache: GeneratedCache) -> bool:
    """Return whether players may see and act on a generated cache."""
    parsed = _parse_cell_location(session, cache.cell_ref)
    if parsed is None:
        return False
    dungeon_id, level_number, position = parsed
    if cache.origin == "area":
        return cache.lair_area_ref is None
    if cache.origin == "lair":
        return _lair_cache_available(session, cache)
    if cache.lair_area_ref is not None:
        return False
    level = session.adventure.dungeon(dungeon_id).level(level_number)
    for area in level.areas:
        if position not in area.cells or area.encounter is None:
            continue
        ref = f"{dungeon_id}:{level_number}:{area.id}"
        if ref not in session.dungeon_state.resolved_encounters:
            return False
    return True


def _current_feature_candidates(session) -> list[_ResolvedFeature]:
    location = session.dungeon_state.location
    if location.dungeon_id is None or location.level_number is None:
        return []
    dungeon_id = location.dungeon_id
    level_number = location.level_number
    candidates = []
    for feature in _features_here(session):
        ref = _feature_ref(session, feature)
        candidates.append(
            _ResolvedFeature(
                raw_id=feature.id,
                public_id=_authored_feature_id(session, dungeon_id, level_number, feature.id),
                feature=feature,
                ref=ref,
            )
        )
    location_ref = _cell_ref(session)
    for cache_id, cache in session.dungeon_state.generated_caches.items():
        if cache.cell_ref != location_ref or not _cache_available(session, cache):
            continue
        candidates.append(
            _ResolvedFeature(
                raw_id=cache_id,
                public_id=_generated_feature_id(session, dungeon_id, level_number, cache_id),
                cache_id=cache_id,
            )
        )
    return candidates


def _resolve_current_feature(session, identifier: str) -> _ResolvedFeature | None:
    """Resolve a public handle or unambiguous legacy raw id at the current location."""
    if identifier == "pile":
        return None
    candidates = _current_feature_candidates(session)
    if identifier.startswith(_HANDLE_PREFIX):
        matches = [
            candidate
            for candidate in candidates
            if hmac.compare_digest(candidate.public_id.encode(), identifier.encode())
        ]
    else:
        matches = [candidate for candidate in candidates if candidate.raw_id == identifier]
    return matches[0] if len(matches) == 1 else None
