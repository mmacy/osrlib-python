"""Where the party stands in a dungeon, the features on its cell, and the reference strings built from them.

Each reference string identifies one area, cell, door edge, or feature anywhere in the adventure.
"""

from osrlib.crawl.dungeon import Direction, FeatureSpec, PartyLocation, Position, cell_ref, edge_ref


def _location(session) -> PartyLocation:
    return session.dungeon_state.location


def _level(session):
    dungeon_id, level_number, _ = _dungeon_coords(session)
    return session.adventure.dungeon(dungeon_id).level(level_number)


def _dungeon_coords(session) -> tuple[str, int, Position]:
    """The dungeon location's fields, narrowed: callers run only while the party is in a dungeon."""
    location = _location(session)
    if location.dungeon_id is None or location.level_number is None or location.position is None:
        raise ValueError("the party is not at a dungeon location")
    return location.dungeon_id, location.level_number, location.position


def _position(session) -> Position:
    return _dungeon_coords(session)[2]


def _area_ref(session, area_id: str) -> str:
    dungeon_id, level_number, _ = _dungeon_coords(session)
    return f"{dungeon_id}:{level_number}:{area_id}"


def _cell_ref(session, position=None) -> str:
    dungeon_id, level_number, party_position = _dungeon_coords(session)
    return cell_ref(dungeon_id, level_number, position if position is not None else party_position)


def _edge_ref(session, direction: Direction) -> str:
    dungeon_id, level_number, position = _dungeon_coords(session)
    return edge_ref(dungeon_id, level_number, position, direction)


def _features_here(session) -> list[FeatureSpec]:
    level = _level(session)
    position = _position(session)
    features: list[FeatureSpec] = []
    area = level.area_at(position)
    if area is not None:
        features.extend(feature for feature in area.features if feature.cell is None or feature.cell == position)
    features.extend(feature for feature in level.features if feature.cell == position)
    return features


def _feature_ref(session, feature: FeatureSpec) -> str:
    location = _location(session)
    return f"{location.dungeon_id}:{location.level_number}:{feature.id}"
