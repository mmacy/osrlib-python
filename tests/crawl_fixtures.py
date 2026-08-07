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
from osrlib.core.items import Coins, GearTemplate
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import SetDoorState, SpawnMonsters
from osrlib.crawl.dungeon import (
    AreaSpec,
    AreaTreasureSpec,
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
    ValuableSpec,
    WanderingSpec,
    edge_key,
)
from osrlib.crawl.gates import GateSpec, HasItemCondition
from osrlib.crawl.narrative import NarrativeBlock
from osrlib.crawl.party import Party
from osrlib.crawl.triggers import AreaEnteredPattern, FlagSetPattern, TriggerSpec
from osrlib.data import load_classes

__all__ = [
    "GATE_KEY",
    "GATE_SEAL",
    "GATE_SIGIL",
    "GATE_TOKEN",
    "PORTCULLIS_CRANK",
    "STOCK_ROSTER",
    "build_adventure",
    "build_blade_adventure",
    "build_chute_adventure",
    "build_double_trap_adventure",
    "build_gas_trap_adventure",
    "build_gated_adventure",
    "build_lethal_coffer_adventure",
    "build_open_door_adventure",
    "build_party",
    "build_portcullis_adventure",
    "build_sightline_adventure",
]


def _open(edges: dict, position, direction) -> None:
    edges[edge_key(position, direction)] = Edge(kind=EdgeKind.OPEN)


def _door(edges: dict, position, direction, **door_fields) -> None:
    edges[edge_key(position, direction)] = Edge(kind=EdgeKind.DOOR, door=DoorSpec(**door_fields))


def build_adventure(wandering_chance: int = 1) -> Adventure:
    """Build the shared two-level test adventure.

    Args:
        wandering_chance: The levels' wandering chance-in-six; 0 keeps tests quiet.
    """
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
    _door(edges_1, (4, 1), Direction.SOUTH, locked=True)  # a locked closet at (4,2)

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
        wandering=WanderingSpec(chance_in_six=wandering_chance, interval_turns=2),
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
        wandering=WanderingSpec(chance_in_six=wandering_chance, interval_turns=2),
    )

    return Adventure(
        name="The Test Delve",
        description="A two-level test dungeon.",
        town=TownSpec(name="Threshold", services=("inn", "trader"), travel_turns={"delve": 6}),
        dungeons=(DungeonSpec(id="delve", name="The Delve", levels=(level_1, level_2)),),
    )


def build_blade_adventure() -> Adventure:
    """Build the one-level door-trap dungeon: three trapped rooms behind three doors.

    Level 1 (3 × 2), entrance (0,0):

    ```text
        x0    x1    x2
    y0  ENT———corr—D—[blade_room]   (normal door on (1,0)'s east edge)
    y1  [cellar] [vault]            (stuck door south of (0,0); secret door south of (1,0))
    ```

    Every room trap here has `trigger="open"` — none of them springs on entry.
    The blade room exercises `OpenDoor`, the cellar's stuck door `ForceDoor`,
    and the vault's secret door the no-leak rule for searches.
    """
    edges: dict[str, Edge] = {}
    _open(edges, (0, 0), Direction.EAST)
    _door(edges, (1, 0), Direction.EAST)  # into the blade room
    _door(edges, (1, 0), Direction.SOUTH, kind="secret")  # into the vault
    _door(edges, (0, 0), Direction.SOUTH, stuck=True)  # into the cellar
    blade = TrapSpec(kind="room", trigger="open", effect=TrapEffect(damage_dice="1d8"))
    level = LevelSpec(
        number=1,
        width=3,
        height=2,
        edges=edges,
        areas=(
            AreaSpec(id="blade_room", name="Blade room", cells=((2, 0),), trap=blade),
            AreaSpec(id="vault", name="Vault", cells=((1, 1),), trap=blade),
            AreaSpec(id="cellar", name="Cellar", cells=((0, 1),), trap=blade),
        ),
        entrance=(0, 0),
        wandering=WanderingSpec(chance_in_six=0),
    )
    return Adventure(
        name="The Blade Delve",
        description="A level of doors with blades rigged to them.",
        town=TownSpec(name="Threshold", travel_turns={"blades": 1}),
        dungeons=(DungeonSpec(id="blades", name="Blades", levels=(level,)),),
    )


GATE_KEY = GearTemplate(id="brass_key", name="Brass key", cost_gp=0)
"""The gated door's key: an adventure-bundled item, so `has_item` names something real."""

GATE_TOKEN = GearTemplate(id="toll_token", name="Ferryman's token", cost_gp=0)
"""The toll the gated stair consumes, one per crossing."""

GATE_SEAL = GearTemplate(id="silver_seal", name="Silver seal", cost_gp=0)
"""The reliquary's second requirement, behind its lock."""

GATE_SIGIL = GearTemplate(id="sanctum_sigil", name="Sanctum sigil", cost_gp=0)
"""The sanctum door's requirement: a real item the warren never places, so the door
opens for nobody until a referee opens it by hand."""


def build_gated_adventure() -> Adventure:
    """Build the two-level gated dungeon: a key door, a toll stair, a lock plus a gate.

    Level 1 (5 × 2), entrance (0,0):

    ```text
        x0     x1        x2        x3          x4
    y0  ENT——corr(niche)—D—corr————corr(stair)—D—[sanctum]
    y1                  [reliquary]
    ```

    - `niche` at (1,0) is a cache holding the brass key and one ferryman's token.
    - The door east of (1,0) requires the brass key and consumes nothing.
    - The stair at (3,0) descends to level 2 and takes a token per crossing.
    - The door east of (3,0) requires a sigil nothing in the dungeon holds — the
      door a referee opens by hand.
    - The door south of (2,0) into the reliquary is locked *and* requires the
      silver seal, which lies on level 2.

    Level 2 (2 × 1): the stair back up at (0,0), and the `vault` cache holding the
    silver seal at (1,0).
    """
    key_gate = GateSpec(
        condition=HasItemCondition(item_id="brass_key"),
        narrative=NarrativeBlock(
            refusal="The bronze sentinel folds its arms. Brass, it says. Brass or nothing.",
            success="The brass key turns in the sentinel's palm and the door swings wide.",
            speaker="the bronze sentinel",
            guidance="The sentinel is bored, not hostile; it has done this for six hundred years.",
        ),
    )
    toll_gate = GateSpec(
        condition=HasItemCondition(item_id="toll_token", consumes=True),
        narrative=NarrativeBlock(
            refusal="The ferryman's hand stays out, empty. No token, no crossing.",
            success="The ferryman pockets the token and the stair opens onto the dark.",
            journal="The ferryman below the warren takes one token per crossing.",
        ),
    )
    sanctum_gate = GateSpec(
        condition=HasItemCondition(item_id="sanctum_sigil"),
        narrative=NarrativeBlock(refusal="The sanctum door has no handle on this side."),
    )
    reliquary_gate = GateSpec(
        condition=HasItemCondition(item_id="silver_seal"),
        narrative=NarrativeBlock(
            refusal="The seal-plate is empty; the door does not care that the lock is undone.",
            success="The silver seal drops into its plate and the reliquary opens.",
        ),
    )

    edges_1: dict[str, Edge] = {}
    _open(edges_1, (0, 0), Direction.EAST)
    _door(edges_1, (1, 0), Direction.EAST, requires=key_gate)
    _open(edges_1, (2, 0), Direction.EAST)
    _door(edges_1, (2, 0), Direction.SOUTH, locked=True, requires=reliquary_gate)
    _door(edges_1, (3, 0), Direction.EAST, requires=sanctum_gate)
    level_1 = LevelSpec(
        number=1,
        width=5,
        height=2,
        edges=edges_1,
        areas=(AreaSpec(id="reliquary", name="Reliquary", cells=((2, 1),)),),
        features=(
            FeatureSpec(
                id="niche",
                kind="treasure_cache",
                description="A shallow niche in the corridor wall.",
                cell=(1, 0),
                item_ids=("brass_key", "toll_token"),
            ),
        ),
        transitions=(
            TransitionSpec(
                kind="stairs_down",
                position=(3, 0),
                to_dungeon_id="warren",
                to_level_number=2,
                to_position=(0, 0),
                to_facing=Direction.EAST,
                requires=toll_gate,
            ),
        ),
        entrance=(0, 0),
        wandering=WanderingSpec(chance_in_six=0),
    )

    edges_2: dict[str, Edge] = {}
    _open(edges_2, (0, 0), Direction.EAST)
    level_2 = LevelSpec(
        number=2,
        width=2,
        height=1,
        edges=edges_2,
        features=(
            FeatureSpec(
                id="vault",
                kind="treasure_cache",
                description="A ferryman's strongbox.",
                cell=(1, 0),
                item_ids=("silver_seal",),
            ),
        ),
        transitions=(
            TransitionSpec(
                kind="stairs_up",
                position=(0, 0),
                to_dungeon_id="warren",
                to_level_number=1,
                to_position=(3, 0),
                to_facing=Direction.WEST,
            ),
        ),
        wandering=WanderingSpec(chance_in_six=0),
    )

    return Adventure(
        name="The Gated Warren",
        description="A warren of doors that want something.",
        town=TownSpec(name="Threshold", travel_turns={"warren": 1}),
        dungeons=(DungeonSpec(id="warren", name="The Warren", levels=(level_1, level_2)),),
        items=(GATE_KEY, GATE_TOKEN, GATE_SEAL, GATE_SIGIL),
    )


PORTCULLIS_CRANK = GearTemplate(id="portcullis_crank", name="Portcullis crank", cost_gp=0)
"""The portcullis gate's requirement: a real item the keep never places, so the
grille answers nothing but the lever's trigger."""

LEVER_KEY = "keep.lever"
"""The flag the lever writes — the portcullis trigger's pattern."""

PORTCULLIS_FIRED = "Chain rattles in the wall; the counterweight drops."
"""The portcullis trigger's referee beat."""

PORTCULLIS_JOURNAL = "The east lever gives, and the portcullis grinds up into its slot."
"""The portcullis trigger's journal form — the players' side of the same moment."""


def build_portcullis_adventure() -> Adventure:
    """Build the one-level keep: a lever, a gated portcullis, and a guarded room.

    Level 1 (5 × 1), entrance (0,0):

    ```text
        x0     x1     x2   ‖   x3     x4
    y0  ENT————corr———corr—D—corr———[guardroom]
    ```

    - The door east of (2,0) is the portcullis: it requires a crank nothing in the
      keep holds, so nobody opens it by hand.
    - `portcullis-rises` fires when the lever flag is written and sets that door
      open, with a referee beat and a journal form.
    - `guard-ambush` fires when the party steps into `guardroom` at (4,0) — which
      keeps two goblins of its own, so the trigger's spawn meets an encounter that
      is already open.
    """
    gate = GateSpec(
        condition=HasItemCondition(item_id="portcullis_crank"),
        narrative=NarrativeBlock(refusal="The portcullis is a grille of iron. It has no handle on this side."),
    )
    edges: dict[str, Edge] = {}
    _open(edges, (0, 0), Direction.EAST)
    _open(edges, (1, 0), Direction.EAST)
    _door(edges, (2, 0), Direction.EAST, requires=gate)
    _open(edges, (3, 0), Direction.EAST)
    level = LevelSpec(
        number=1,
        width=5,
        height=1,
        edges=edges,
        areas=(
            AreaSpec(
                id="guardroom",
                name="Guardroom",
                description="Two goblins at a table of scarred oak.",
                cells=((4, 0),),
                encounter=KeyedEncounter(monsters=(KeyedMonster(template_id="goblin", count_fixed=2),), aware=True),
            ),
        ),
        entrance=(0, 0),
        wandering=WanderingSpec(chance_in_six=0),
    )
    triggers = (
        TriggerSpec(
            id="portcullis-rises",
            when=FlagSetPattern(key=LEVER_KEY, value="pulled"),
            consequences=(
                SetDoorState(dungeon_id="keep", level_number=1, x=2, y=0, direction=Direction.EAST, open=True),
            ),
            narrative=NarrativeBlock(fired=PORTCULLIS_FIRED, journal=PORTCULLIS_JOURNAL),
        ),
        TriggerSpec(
            id="guard-ambush",
            when=AreaEnteredPattern(dungeon_id="keep", level_number=1, area_id="guardroom"),
            consequences=(SpawnMonsters(template_id="goblin", count_fixed=1, distance_feet=30),),
            narrative=NarrativeBlock(
                fired="A third goblin was meant to drop from the rafters.",
                journal="Something moved in the rafters of the guardroom.",
            ),
        ),
    )
    return Adventure(
        name="The Lever Keep",
        description="A keep whose one door answers a lever and nothing else.",
        town=TownSpec(name="Threshold", travel_turns={"keep": 1}),
        dungeons=(DungeonSpec(id="keep", name="The Keep", levels=(level,)),),
        items=(PORTCULLIS_CRANK,),
        triggers=triggers,
    )


def build_open_door_adventure() -> Adventure:
    """Build the one-level pair of cells joined by an authored-open door.

    Level 1 (2 × 1), entrance (0,0): the only edge is a door east of (0,0) with
    `starts_open=True`, so the overlay's seeding rule has something to seed.
    """
    edges: dict[str, Edge] = {}
    _door(edges, (0, 0), Direction.EAST, starts_open=True)
    level = LevelSpec(
        number=1,
        width=2,
        height=1,
        edges=edges,
        entrance=(0, 0),
        wandering=WanderingSpec(chance_in_six=0),
    )
    return Adventure(
        name="The Open Door",
        description="Two cells and a door that starts open.",
        town=TownSpec(name="Threshold", travel_turns={"vestibule": 1}),
        dungeons=(DungeonSpec(id="vestibule", name="Vestibule", levels=(level,)),),
    )


def build_double_trap_adventure(inner_trap: TrapSpec, outer_trap: TrapSpec) -> Adventure:
    """Build the two-trapped-areas corner: one door joining two open-trigger traps.

    Level 1 (2 × 1): the party starts inside `outer` at (0,0); the only door
    leads east into `inner` at (1,0). Both areas carry the caller's traps —
    author them `kind="room"`, `trigger="open"` — and opening the door rolls the
    far (inner) side first. Level 2 (1 × 1) is bare, a landing cell for
    chute-effect traps.
    """
    edges: dict[str, Edge] = {}
    _door(edges, (0, 0), Direction.EAST)
    level_1 = LevelSpec(
        number=1,
        width=2,
        height=1,
        edges=edges,
        areas=(
            AreaSpec(id="outer", name="Outer room", cells=((0, 0),), trap=outer_trap),
            AreaSpec(id="inner", name="Inner room", cells=((1, 0),), trap=inner_trap),
        ),
        entrance=(0, 0),
        wandering=WanderingSpec(chance_in_six=0),
    )
    level_2 = LevelSpec(number=2, width=1, height=1, edges={}, wandering=WanderingSpec(chance_in_six=0))
    return Adventure(
        name="The Double Blade",
        description="One door, two blades.",
        town=TownSpec(name="Threshold", travel_turns={"double": 1}),
        dungeons=(DungeonSpec(id="double", name="Double", levels=(level_1, level_2)),),
    )


def build_gas_trap_adventure() -> Adventure:
    """Build the one-level gas-trap dungeon: one step from the entrance to a wipe.

    Level 1 (2 × 1), entrance (0,0):

    ```text
        x0   x1
    y0  ENT——[gas_room]
    ```

    `gas_room` at (1,0) carries an enter-trigger room trap whose effect is
    `kills=True`, `affects="party"`, and no save — the save-or-die gas that fills
    the room, with no save to make. Stepping east springs it 2-in-6, and a spring
    ends the whole party at once.
    """
    edges: dict[str, Edge] = {}
    _open(edges, (0, 0), Direction.EAST)
    gas = TrapSpec(
        kind="room",
        trigger="enter",
        affects="party",
        effect=TrapEffect(kills=True),
    )
    level = LevelSpec(
        number=1,
        width=2,
        height=1,
        edges=edges,
        areas=(
            AreaSpec(
                id="gas_room",
                name="Fume-filled chamber",
                description="A low chamber, the air in it faintly green.",
                cells=((1, 0),),
                trap=gas,
            ),
        ),
        entrance=(0, 0),
        wandering=WanderingSpec(chance_in_six=0),
    )
    return Adventure(
        name="The Fume Vault",
        description="A doorway, a chamber, and a room full of gas.",
        town=TownSpec(name="Threshold", travel_turns={"vault": 1}),
        dungeons=(DungeonSpec(id="vault", name="The Fume Vault", levels=(level,)),),
    )


def build_lethal_coffer_adventure() -> Adventure:
    """Build the one-cell dungeon whose only feature is a coffer that kills openers.

    Level 1 (1 × 1), entrance (0,0): the `coffer` cache holds coins, an authored
    named valuable, and a magic item — instantiation the take path performs on the
    treasure stream — behind a treasure trap whose effect is `kills=True`,
    `affects="party"`, and no save. `TakeTreasure` is the springing action.
    """
    coffer = FeatureSpec(
        id="coffer",
        kind="treasure_cache",
        description="A squat iron coffer, its lid seamed with tarnish.",
        cell=(0, 0),
        coins=Coins(gp=300),
        valuables=(ValuableSpec(kind="jewellery", name="The reeve's chain", value_gp=700),),
        magic_item_ids=("potion_of_healing",),
        trap=TrapSpec(
            kind="treasure",
            trigger="open",
            affects="party",
            effect=TrapEffect(kills=True),
        ),
    )
    level = LevelSpec(
        number=1,
        width=1,
        height=1,
        edges={},
        features=(coffer,),
        entrance=(0, 0),
        wandering=WanderingSpec(chance_in_six=0),
    )
    return Adventure(
        name="The Reeve's Coffer",
        description="One cell, one coffer, one very bad idea.",
        town=TownSpec(name="Threshold", travel_turns={"strongroom": 1}),
        dungeons=(DungeonSpec(id="strongroom", name="The Strongroom", levels=(level,)),),
    )


def build_chute_adventure() -> Adventure:
    """Build the two-level chute dungeon: a slide that kills, onto a stocked landing.

    Level 1 (2 × 1), entrance (0,0): stepping east into `chute_room` at (1,0)
    springs an enter-trigger trap that kills the whole party (no save) *and*
    carries it down to level 2 (0,0) — the corpses genuinely move.

    Level 2 (2 × 1): the `landing` area at (0,0) declares unguarded treasure and
    keeps two goblins, so an arrival that discovers or ambushes has something to
    discover and something to ambush with.
    """
    edges_1: dict[str, Edge] = {}
    _open(edges_1, (0, 0), Direction.EAST)
    chute = TrapSpec(
        kind="room",
        trigger="enter",
        affects="party",
        effect=TrapEffect(
            kills=True,
            transition=TransitionSpec(
                kind="chute",
                position=(1, 0),
                to_dungeon_id="shaft",
                to_level_number=2,
                to_position=(0, 0),
                to_facing=Direction.EAST,
            ),
        ),
    )
    level_1 = LevelSpec(
        number=1,
        width=2,
        height=1,
        edges=edges_1,
        areas=(AreaSpec(id="chute_room", name="Chute room", cells=((1, 0),), trap=chute),),
        entrance=(0, 0),
        wandering=WanderingSpec(chance_in_six=0),
    )
    level_2 = LevelSpec(
        number=2,
        width=2,
        height=1,
        edges={},
        areas=(
            AreaSpec(
                id="landing",
                name="Landing",
                cells=((0, 0),),
                treasure=AreaTreasureSpec(unguarded=True),
                encounter=KeyedEncounter(monsters=(KeyedMonster(template_id="goblin", count_fixed=2),)),
            ),
        ),
        wandering=WanderingSpec(chance_in_six=0),
    )
    return Adventure(
        name="The Shaft",
        description="A chute onto a landing nobody survives to loot.",
        town=TownSpec(name="Threshold", travel_turns={"shaft": 1}),
        dungeons=(DungeonSpec(id="shaft", name="The Shaft", levels=(level_1, level_2)),),
    )


def build_sightline_adventure() -> Adventure:
    """Build the one-level sight-line dungeon: four cells with four different views.

    Level 1 (15 × 5), four unconnected pieces the referee places the party into:

    ```text
        x0   x1   x2  ...  x14
    y0  [chamber———————]           (0,0)–(2,0): a sealed 30' chamber
    y2  clo|door|corr————————————  (0,2) closet, door east, then (1,2)–(14,2)
    y4  seal                       (0,4): a sealed cell with no edges at all
    ```

    The sight lines, in feet: the corridor cell (1,2) sees 130' east (past the
    printed 2d6 × 10' maximum, so nothing there is ever clamped); the chamber's
    west end (0,0) sees 20'; the sealed cell (0,4) sees nothing, and the closet
    (0,2) sees nothing either until its door opens onto the corridor.
    """
    edges: dict[str, Edge] = {}
    _open(edges, (0, 0), Direction.EAST)
    _open(edges, (1, 0), Direction.EAST)
    _door(edges, (0, 2), Direction.EAST)  # the closet's door onto the corridor
    for x in range(1, 14):
        _open(edges, (x, 2), Direction.EAST)
    level = LevelSpec(
        number=1, width=15, height=5, edges=edges, entrance=(1, 2), wandering=WanderingSpec(chance_in_six=0)
    )
    return Adventure(
        name="The Sight Lines",
        description="A level built to measure what the party can see.",
        town=TownSpec(name="Threshold", travel_turns={"sightlines": 1}),
        dungeons=(DungeonSpec(id="sightlines", name="Sight Lines", levels=(level,)),),
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
        spell_book=("sleep",) if class_id == "magic_user" else (),
    )


STOCK_ROSTER = (("Brakk", "fighter"), ("Sable", "thief"), ("Wynn", "cleric"), ("Elara", "magic_user"))
"""The stock party's names and classes, in marching order."""


def build_party(roster=STOCK_ROSTER) -> Party:
    """Build the stock four-member party (ids unassigned; the session assigns them).

    Args:
        roster: `(name, class_id)` pairs in marching order. Pass a narrower roster to
            exercise class-gated behavior — loot only one class can use, say.
    """
    return Party(members=[_member(name, class_id) for name, class_id in roster])


def build_milestone_adventure() -> Adventure:
    """The milestone delve: two levels tuned so every scripted beat is reachable.

    Level 1 "halls" (7 × 2): entrance corridor east; a pit room off (1, 0); the
    goblin guard room behind a stuck door with a 400 gp coffer; a secret door east
    of the guard room to the stairs down. Wandering chance 2 every 2 turns.

    Level 2 (7 × 2, quiet): the skeleton crypt (aware, hostile — the turn-undead
    declaration beat), and two goblin kennels (hostile, speed 60 = the armoured
    party's 60) for the flee-with-dropped-treasure distraction and the 30-round
    exhaustion terminal.
    """
    edges_1: dict[str, Edge] = {}
    for x in range(3):
        _open(edges_1, (x, 0), Direction.EAST)
    _open(edges_1, (1, 0), Direction.SOUTH)  # pit hall
    _door(edges_1, (3, 0), Direction.SOUTH, stuck=True)  # guard room
    _open(edges_1, (3, 1), Direction.EAST)
    _door(edges_1, (4, 1), Direction.EAST, kind="secret")  # to the stairs corridor
    _open(edges_1, (5, 1), Direction.EAST)

    level_1 = LevelSpec(
        number=1,
        width=7,
        height=2,
        edges=edges_1,
        areas=(
            AreaSpec(
                id="pit_hall",
                name="Dusty hall",
                cells=((1, 1),),
                trap=TrapSpec(kind="room", trigger="enter", effect=TrapEffect(fall_feet=10)),
            ),
            AreaSpec(
                id="guard_room",
                name="Guard room",
                cells=((3, 1), (4, 1)),
                encounter=KeyedEncounter(monsters=(KeyedMonster(template_id="goblin", count_fixed=6),)),
                features=(FeatureSpec(id="coffer", kind="treasure_cache", cell=(4, 1), coins=Coins(gp=400)),),
            ),
        ),
        transitions=(
            TransitionSpec(
                kind="stairs_down",
                position=(6, 1),
                to_dungeon_id="halls",
                to_level_number=2,
                to_position=(0, 0),
                to_facing=Direction.EAST,
            ),
        ),
        entrance=(0, 0),
        wandering=WanderingSpec(chance_in_six=2, interval_turns=2),
    )

    edges_2: dict[str, Edge] = {}
    for x in range(6):
        _open(edges_2, (x, 0), Direction.EAST)
    _door(edges_2, (1, 0), Direction.SOUTH)  # crypt
    _open(edges_2, (3, 0), Direction.SOUTH)  # kennel a
    _open(edges_2, (5, 0), Direction.SOUTH)  # kennel b
    _open(edges_2, (6, 0), Direction.SOUTH)  # kennel c

    from osrlib.core.tables import ReactionResult

    level_2 = LevelSpec(
        number=2,
        width=7,
        height=2,
        edges=edges_2,
        areas=(
            AreaSpec(
                id="crypt",
                name="Crypt",
                cells=((1, 1),),
                encounter=KeyedEncounter(
                    monsters=(KeyedMonster(template_id="skeleton", count_fixed=4),),
                    aware=True,
                    stance=ReactionResult.HOSTILE,
                ),
            ),
            AreaSpec(
                id="kennel_a",
                name="Kennel",
                cells=((3, 1),),
                encounter=KeyedEncounter(
                    monsters=(KeyedMonster(template_id="goblin", count_fixed=3),),
                    aware=True,
                    stance=ReactionResult.HOSTILE,
                ),
            ),
            AreaSpec(
                id="kennel_b",
                name="Second kennel",
                cells=((5, 1),),
                encounter=KeyedEncounter(
                    monsters=(KeyedMonster(template_id="goblin", count_fixed=3),),
                    aware=True,
                    stance=ReactionResult.HOSTILE,
                ),
            ),
            AreaSpec(
                id="kennel_c",
                name="Third kennel",
                cells=((6, 1),),
                encounter=KeyedEncounter(
                    monsters=(KeyedMonster(template_id="goblin", count_fixed=3),),
                    aware=True,
                    stance=ReactionResult.HOSTILE,
                ),
            ),
        ),
        transitions=(
            TransitionSpec(
                kind="stairs_up",
                position=(0, 0),
                to_dungeon_id="halls",
                to_level_number=1,
                to_position=(6, 1),
                to_facing=Direction.WEST,
            ),
        ),
        wandering=WanderingSpec(chance_in_six=0, interval_turns=2),
    )

    return Adventure(
        name="The Milestone Delve",
        description="Enter from town, delve two levels, and return alive.",
        town=TownSpec(name="Threshold", services=("trader", "inn"), travel_turns={"halls": 6}),
        dungeons=(DungeonSpec(id="halls", name="The Halls", levels=(level_1, level_2)),),
    )


_MILESTONE_LEVELS = {"fighter": 3, "elf": 3, "thief": 3, "cleric": 3, "magic_user": 5}


def build_milestone_party(master_seed: int) -> list:
    """Build the milestone party from the seed's creation and advancement streams.

    Five members created via `create_character` (no purchases — the town shopping
    beat buys gear in-session), leveled by exact-threshold XP awards, with the
    magic-user's book grown to (magic missile, sleep, web ×0 — web and fire ball
    added at level 5 capacity) and everyone's spells memorized pre-session (the
    party arrives prepared; in-session re-preparation is the night-camp beat).

    Args:
        master_seed: The golden's master seed.

    Returns:
        The created characters, in marching order (ids unassigned).

    Raises:
        ValueError: If the rolled scores make a class choice illegal — the golden
            generator rejects the seed and tries the next.
    """
    from osrlib.core.character import ADVANCEMENT_STREAM, CHARACTER_CREATION_STREAM, create_character
    from osrlib.core.classes import apply_xp
    from osrlib.core.rng import RngStreams
    from osrlib.core.ruleset import Ruleset
    from osrlib.core.spells import MemorizedSpell, add_spell_to_book, memorize_spells
    from osrlib.data import load_spells

    streams = RngStreams(master_seed=master_seed)
    creation = streams.get(CHARACTER_CREATION_STREAM)
    advancement = streams.get(ADVANCEMENT_STREAM)
    ruleset = Ruleset(hp_reroll_at_first_level=True)
    spells = load_spells()
    members = []
    roster = (
        ("Brakk", "fighter", ()),
        ("Faelwen", "elf", ("sleep",)),
        ("Sable", "thief", ()),
        ("Wynn", "cleric", ()),
        ("Elara", "magic_user", ("magic_missile",)),
    )
    for name, class_id, starting_spells in roster:
        result = create_character(
            name=name,
            class_id=class_id,
            alignment="lawful",
            ruleset=ruleset,
            stream=creation,
            starting_spell_ids=starting_spells,
        )
        member = result.character
        definition = load_classes().get(class_id)
        while member.level < _MILESTONE_LEVELS[class_id]:
            # Double the remaining XP so a class penalty can't floor the modified
            # award short of the threshold; the one-level-per-award clamp caps it.
            remaining = definition.row(member.level + 1).xp - member.xp
            apply_xp(member, definition, remaining * 2, advancement)
        members.append(member)
    magic_user = members[4]
    for spell_id in ("sleep", "web", "fire_ball"):
        book = add_spell_to_book(magic_user, load_classes().get("magic_user"), spells, spell_id)
        if book.rejections:
            raise ValueError(f"book growth failed: {[r.code for r in book.rejections]}")
    memorize_spells(
        magic_user,
        load_classes().get("magic_user"),
        spells,
        [
            MemorizedSpell(spell_id="magic_missile"),
            MemorizedSpell(spell_id="magic_missile"),
            MemorizedSpell(spell_id="web"),
            MemorizedSpell(spell_id="web"),
            MemorizedSpell(spell_id="fire_ball"),
        ],
    )
    memorize_spells(
        members[3],
        load_classes().get("cleric"),
        spells,
        [MemorizedSpell(spell_id="cure_light_wounds"), MemorizedSpell(spell_id="cure_light_wounds")],
    )
    memorize_spells(members[1], load_classes().get("elf"), spells, [MemorizedSpell(spell_id="sleep")])
    return members
