"""The authored mini-adventure: a town, a two-level barrow, and the quest MacGuffin.

Everything here is frozen game content built from the library's authoring models:
a keyed goblin lair whose treasure ref (`R (C)`) generates a real hoard when the
encounter first spawns, an `unguarded: true` vault on level 2, a custom wandering
table whose rows field Basic Adventurers (the level-2 halls are picked clean of
monsters — rival parties prowl them instead), and the Jade Idol — a named valuable
in a hand-placed cache — whose recovery the fetch quest in `quest.py` watches.
"""

from osrlib.core.items import Coins
from osrlib.core.tables import EncounterTable, EncounterTableRow, NpcPartyEncounterEntry
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.dungeon import (
    AreaSpec,
    AreaTreasureSpec,
    Direction,
    DungeonSpec,
    Edge,
    EdgeKind,
    FeatureSpec,
    KeyedEncounter,
    KeyedMonster,
    LevelSpec,
    TransitionSpec,
    ValuableSpec,
    WanderingSpec,
)

IDOL_NAME = "Jade Idol of the Barrow King"
IDOL_VALUE_GP = 2200
QUEST_REWARD_GP = 200
QUEST_BONUS_XP = 600


def _open_row(level_y: int, width: int) -> dict[str, Edge]:
    """Open west-east edges along one corridor row."""
    edges: dict[str, Edge] = {}
    for x in range(1, width):
        edges[f"{x},{level_y}:west"] = Edge(kind=EdgeKind.OPEN)
    return edges


def _rival_party_table() -> EncounterTable:
    """A wandering table of rival adventurers: every d20 row fields a Basic pair."""
    rows = tuple(
        EncounterTableRow(
            roll=roll,
            name="Basic Adventurers",
            entry=NpcPartyEncounterEntry(party_kind="basic"),
            count_fixed=2,
        )
        for roll in range(1, 21)
    )
    return EncounterTable(id="barrow_rivals", label="Barrow halls", min_level=2, rows=rows)


def build_adventure() -> Adventure:
    """Build the example adventure — the milestone's playable minimal crawl."""
    level_one = LevelSpec(
        number=1,
        width=5,
        height=1,
        edges=_open_row(0, 5),
        entrance=(0, 0),
        areas=(
            AreaSpec(
                id="guard_room",
                name="Guard room",
                description="Broken spears and gnawed bones litter the flagstones.",
                cells=((2, 0), (3, 0)),
                encounter=KeyedEncounter(monsters=(KeyedMonster(template_id="goblin", count_fixed=2),)),
            ),
            AreaSpec(
                id="shrine",
                name="Shrine of the Barrow King",
                description="A toppled altar; something green glints beneath it.",
                cells=((4, 0),),
                features=(
                    FeatureSpec(
                        id="idol_shrine",
                        kind="treasure_cache",
                        description="The idol rests in a hollow under the altar stone.",
                        cell=(4, 0),
                        coins=Coins(gp=50),
                        valuables=(
                            ValuableSpec(kind="jewellery", name=IDOL_NAME, value_gp=IDOL_VALUE_GP, weight_coins=10),
                        ),
                    ),
                ),
            ),
        ),
        transitions=(
            TransitionSpec(
                kind="stairs_down",
                position=(4, 0),
                to_dungeon_id="barrow",
                to_level_number=2,
                to_position=(0, 0),
                to_facing=Direction.EAST,
            ),
        ),
        wandering=WanderingSpec(chance_in_six=0),
    )
    level_two = LevelSpec(
        number=2,
        width=3,
        height=1,
        edges=_open_row(0, 3),
        areas=(
            AreaSpec(
                id="vault",
                name="Looted vault",
                description="Empty niches — but one flagstone sits proud of the floor.",
                cells=((2, 0),),
                treasure=AreaTreasureSpec(unguarded=True),
            ),
        ),
        transitions=(
            TransitionSpec(
                kind="stairs_up",
                position=(0, 0),
                to_dungeon_id="barrow",
                to_level_number=1,
                to_position=(4, 0),
                to_facing=Direction.WEST,
            ),
        ),
        wandering=WanderingSpec(chance_in_six=6, interval_turns=1, table=_rival_party_table()),
    )
    return Adventure(
        name="The Barrow of the Forgotten King",
        description="A plundered barrow outside town — and the idol the temple wants back.",
        hooks=("The temple pays 200 gp for the Jade Idol's return.",),
        town=TownSpec(
            name="Threshold",
            description="A walled market town at the edge of the moors.",
            services=("equipment", "temple healing", "treasure buyers"),
            travel_turns={"barrow": 2},
        ),
        dungeons=(DungeonSpec(id="barrow", name="The Barrow", levels=(level_one, level_two)),),
    )
