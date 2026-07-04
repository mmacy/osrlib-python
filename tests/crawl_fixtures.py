"""Shared crawl-test content: a small two-level adventure and a stock party.

The delve dungeon, level 1 (5 × 4):

```text
    x0   x1   x2   x3   x4
y0  ENT——corr——corr           (entrance at (0,0); corridor east to (2,0))
y1       pit  [room_a  ]——sec——(4,1) stairs down
y2            [room_a  ]
```

- A stuck normal door on (2,0)'s south edge into room_a.
- room_a spans (2,1), (3,1), (2,2), (3,2); keyed goblins ×2; a treasure cache
  (chest) with coins and a poison-needle treasure trap.
- A secret door on (3,1)'s east edge to the corridor cell (4,1), which carries
  stairs down to level 2 (0,0).
- The pit room trap covers (1,1), reached by an open edge south of (1,0).

Level 2 (3 × 3): open corridor row y0; a keyed skeleton area at (2,0).
"""

from osrlib.core.abilities import AbilityScore
from osrlib.core.character import Character
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.dungeon import (
    AreaSpec,
    Coins,
    Direction,
    DoorSpec,
    DungeonSpec,
    Edge,
    EdgeKind,
    FeatureSpec,
    KeyedEncounter,
    KeyedMonster,
    LevelSpec,
    TransitionSpec,
    TrapEffect,
    TrapSpec,
    WanderingSpec,
    edge_key,
)
from osrlib.crawl.party import Party
from osrlib.data import load_classes

__all__ = [
    "build_adventure",
    "build_party",
]


def _open(edges: dict, position, direction) -> None:
    edges[edge_key(position, direction)] = Edge(kind=EdgeKind.OPEN)


def _door(edges: dict, position, direction, **door_fields) -> None:
    edges[edge_key(position, direction)] = Edge(kind=EdgeKind.DOOR, door=DoorSpec(**door_fields))


def build_adventure() -> Adventure:
    """Build the shared two-level test adventure."""
    edges_1: dict[str, Edge] = {}
    _open(edges_1, (0, 0), Direction.EAST)
    _open(edges_1, (1, 0), Direction.EAST)
    _open(edges_1, (1, 0), Direction.SOUTH)  # into the pit room
    _door(edges_1, (2, 0), Direction.SOUTH, stuck=True)  # into room_a
    _open(edges_1, (2, 1), Direction.EAST)
    _open(edges_1, (2, 1), Direction.SOUTH)
    _open(edges_1, (3, 1), Direction.SOUTH)
    _open(edges_1, (2, 2), Direction.EAST)
    _door(edges_1, (3, 1), Direction.EAST, kind="secret")  # to the stairs corridor

    pit = TrapSpec(
        kind="room",
        trigger="enter",
        effect=TrapEffect(fall_feet=10),
    )
    needle = TrapSpec(
        kind="treasure",
        trigger="open",
        effect=TrapEffect(save={"category": "death", "on_save": "negates"}, kills=True),
    )
    chest = FeatureSpec(
        id="chest",
        kind="treasure_cache",
        description="An iron-bound chest.",
        cell=(3, 2),
        item_ids=("holy_water",),
        coins=Coins(gp=200),
        trap=needle,
    )
    level_1 = LevelSpec(
        number=1,
        width=5,
        height=4,
        edges=edges_1,
        areas=(
            AreaSpec(
                id="pit_room",
                name="Dusty cell",
                cells=((1, 1),),
                trap=pit,
            ),
            AreaSpec(
                id="room_a",
                name="Guard room",
                description="Bones and bedrolls.",
                cells=((2, 1), (3, 1), (2, 2), (3, 2)),
                encounter=KeyedEncounter(monsters=(KeyedMonster(template_id="goblin", count_fixed=2),)),
                features=(chest,),
            ),
        ),
        transitions=(
            TransitionSpec(
                kind="stairs_down",
                position=(4, 1),
                to_dungeon_id="delve",
                to_level_number=2,
                to_position=(0, 0),
                to_facing=Direction.EAST,
            ),
        ),
        entrance=(0, 0),
    )

    edges_2: dict[str, Edge] = {}
    _open(edges_2, (0, 0), Direction.EAST)
    _open(edges_2, (1, 0), Direction.EAST)
    level_2 = LevelSpec(
        number=2,
        width=3,
        height=3,
        edges=edges_2,
        areas=(
            AreaSpec(
                id="crypt",
                name="Crypt",
                cells=((2, 0),),
                encounter=KeyedEncounter(monsters=(KeyedMonster(template_id="skeleton", count_fixed=3),), aware=True),
            ),
        ),
        transitions=(
            TransitionSpec(
                kind="stairs_up",
                position=(0, 0),
                to_dungeon_id="delve",
                to_level_number=1,
                to_position=(4, 1),
                to_facing=Direction.WEST,
            ),
        ),
        wandering=WanderingSpec(chance_in_six=1, interval_turns=2),
    )

    return Adventure(
        name="The Test Delve",
        description="A two-level test dungeon.",
        town=TownSpec(name="Threshold", services=("inn", "trader"), travel_turns={"delve": 6}),
        dungeons=(DungeonSpec(id="delve", name="The Delve", levels=(level_1, level_2)),),
    )


def _member(name: str, class_id: str) -> Character:
    definition = load_classes().get(class_id)
    scores = {ability: 11 for ability in AbilityScore}
    if class_id == "cleric":
        scores[AbilityScore.WIS] = 13
    return Character(
        name=name,
        class_id=class_id,
        race=definition.race,
        level=1,
        xp=0,
        scores=scores,
        alignment="lawful",
        max_hp=6,
        current_hp=6,
    )


def build_party() -> Party:
    """Build a stock four-member party (ids unassigned; the session assigns them)."""
    return Party(
        members=[
            _member("Brakk", "fighter"),
            _member("Sable", "thief"),
            _member("Wynn", "cleric"),
            _member("Elara", "magic_user"),
        ]
    )
