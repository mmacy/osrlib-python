"""The authored mini-adventure: a town, a two-level barrow, and the quest that ends it.

Everything here is frozen game content built from the library's authoring models:
a keyed goblin lair whose treasure ref (`R (C)`) generates a real hoard when the
encounter first spawns, an `unguarded: true` vault on level 2, a custom wandering
table whose rows field Basic Adventurers (the level-2 halls are picked clean of
monsters — rival parties prowl them instead), the Jade Idol — a bundled item in a
hand-placed cache, so taking it reports a catalog id — and the fetch quest that
watches for it, written as adventure data and played by the library's
[`Interpreter`][osrlib.crawl.interpreter.Interpreter].
"""

from osrlib.core.items import Coins, GearTemplate
from osrlib.core.tables import EncounterTable, EncounterTableRow, NpcPartyEncounterEntry
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import AwardXP, GrantCoins, SetFlag
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
    WanderingSpec,
)
from osrlib.crawl.gates import HasItemCondition
from osrlib.crawl.narrative import NarrativeBlock
from osrlib.crawl.quests import ObjectiveSpec, QuestSpec, TriggerClause
from osrlib.crawl.triggers import (
    FIRST_LIVING_SELECTOR,
    PARTY_SELECTOR,
    DungeonEnteredPattern,
    ItemAcquiredPattern,
    TownEnteredPattern,
)

IDOL_ID = "jade-idol"
IDOL_NAME = "Jade Idol of the Barrow King"
QUEST_REWARD_GP = 200
QUEST_BONUS_XP = 1200

# --8<-- [start:bundled-idol]
JADE_IDOL = GearTemplate(id=IDOL_ID, name=IDOL_NAME, cost_gp=0)
"""The MacGuffin as a bundled item: an id the shop never stocks and the temple wants
back. Carrying it is a fact the quest can match on and a condition it can test."""
# --8<-- [end:bundled-idol]


def _open_row(level_y: int, width: int) -> dict[str, Edge]:
    """Open west-east edges along one corridor row."""
    edges: dict[str, Edge] = {}
    for x in range(1, width):
        edges[f"{x},{level_y}:west"] = Edge(kind=EdgeKind.OPEN)
    return edges


# --8<-- [start:wandering-table]
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


# --8<-- [end:wandering-table]


# --8<-- [start:fetch-quest]
def _fetch_quest() -> QuestSpec:
    """The temple's errand, authored as data: take the idol, bring it home.

    Both clauses are the trigger vocabulary the library already speaks — an
    acquisition matched on the bundled idol's catalog id, and a homecoming narrowed
    by a condition that asks whether the party is still carrying it. Walking back
    empty-handed is not a return; the second objective simply does not fire.
    """
    return QuestSpec(
        id="the-idol",
        name="The Jade Idol",
        activation=TriggerClause(pattern=DungeonEnteredPattern(dungeon_id="barrow")),
        objectives=(
            ObjectiveSpec(
                id="recover-idol",
                name="Recover the idol",
                when=TriggerClause(pattern=ItemAcquiredPattern(item_id=IDOL_ID)),
                narrative=NarrativeBlock(progress="The idol comes up out of the hollow, cold as well-water."),
            ),
            ObjectiveSpec(
                id="return-home",
                name="Bring it home",
                when=TriggerClause(
                    pattern=TownEnteredPattern(),
                    conditions=(HasItemCondition(item_id=IDOL_ID),),
                ),
                narrative=NarrativeBlock(progress="Threshold's gate shuts behind you with the idol inside it."),
            ),
        ),
        rewards=(
            GrantCoins(character_id=FIRST_LIVING_SELECTOR, coins=Coins(gp=QUEST_REWARD_GP)),
            AwardXP(character_id=PARTY_SELECTOR, amount=QUEST_BONUS_XP),
            SetFlag(key="quest.idol", value="recovered"),
        ),
        concludes_adventure=True,
        narrative=NarrativeBlock(
            offer="The temple wants the Jade Idol off the barrow king's altar and back on its own.",
            completion="The almoner counts out the reward without looking up. The idol is home.",
            speaker="the temple almoner",
        ),
    )


# --8<-- [end:fetch-quest]


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
            # --8<-- [start:idol-shrine-area]
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
                        item_ids=(IDOL_ID,),
                    ),
                ),
            ),
            # --8<-- [end:idol-shrine-area]
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
        # --8<-- [start:level-guidance]
        guidance=(
            "Grave goods, not treasure: the barrow king was buried, not hoarded. "
            "Keep the goblins squalid and the shrine quiet."
        ),
        # --8<-- [end:level-guidance]
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
        guidance="Somebody else is always down here first. Rivals are rude, not evil.",
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
        items=(JADE_IDOL,),
        quests=(_fetch_quest(),),
    )
