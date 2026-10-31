"""The dungeon: the grid you author, and the record of what play changes in it.

This module defines both halves of a dungeon. [`DungeonSpec`][osrlib.crawl.dungeon.DungeonSpec] and
everything inside it (levels, edges, areas, features, traps, transitions) is authored content:
frozen models you build, put in an [`Adventure`][osrlib.crawl.adventure.Adventure], and never
change. [`DungeonState`][osrlib.crawl.dungeon.DungeonState] is the *overlay*: the mutable record a
running session keeps beside the content, of which cells the party has walked, which doors stand
open, which traps have gone off, what lies on the floor, and where the party is. A save contains the
overlay. The overlay refers to content by id strings, not by object, so it serializes as flat data.

You build the levels here and combine them into an adventure in
[`osrlib.crawl.adventure`][osrlib.crawl.adventure]. You never construct a `DungeonState` yourself:
[`GameSession.new`][osrlib.crawl.session.GameSession.new] creates one, and you read it through the
session's views. The guide
[Building an adventure](https://mmacy.github.io/osrlib-python/getting-started/building-an-adventure/)
walks through a complete program you can run.

Every model here uses one grid. A level is a grid of 10-foot cells, each addressed `(x, y)`. `x`
increases to the east and `y` increases to the south, from `(0, 0)` in the northwest corner. Every
edge between two cells is a wall unless you say otherwise. A level's `edges` map has one entry for
each edge that isn't a wall, keyed by [`edge_key`][osrlib.crawl.dungeon.edge_key], so an edge has
exactly one key whichever of its two cells you name it from. An edge with no entry is a wall, and so
is every edge on the level's boundary.

The members, in the order you use them:

- Addressing the grid: [`Position`][osrlib.crawl.dungeon.Position],
  [`Direction`][osrlib.crawl.dungeon.Direction], [`step`][osrlib.crawl.dungeon.step], and
  [`edge_key`][osrlib.crawl.dungeon.edge_key].
- What stands on an edge: [`EdgeKind`][osrlib.crawl.dungeon.EdgeKind],
  [`Edge`][osrlib.crawl.dungeon.Edge], and [`DoorSpec`][osrlib.crawl.dungeon.DoorSpec].
- A keyed room and its content: [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec], with a
  [`KeyedEncounter`][osrlib.crawl.dungeon.KeyedEncounter] of
  [`KeyedMonster`][osrlib.crawl.dungeon.KeyedMonster] lines, an
  [`AreaTreasureSpec`][osrlib.crawl.dungeon.AreaTreasureSpec],
  [`FeatureSpec`][osrlib.crawl.dungeon.FeatureSpec]s with their
  [`ValuableSpec`][osrlib.crawl.dungeon.ValuableSpec]s, and a
  [`TrapSpec`][osrlib.crawl.dungeon.TrapSpec] with its [`TrapEffect`][osrlib.crawl.dungeon.TrapEffect].
- A level and its dungeon: [`TransitionSpec`][osrlib.crawl.dungeon.TransitionSpec],
  [`WanderingSpec`][osrlib.crawl.dungeon.WanderingSpec],
  [`LevelSpec`][osrlib.crawl.dungeon.LevelSpec], and
  [`DungeonSpec`][osrlib.crawl.dungeon.DungeonSpec].
- The overlay, which you read during play: [`DungeonState`][osrlib.crawl.dungeon.DungeonState] and
  what it contains, [`PartyLocation`][osrlib.crawl.dungeon.PartyLocation],
  [`DoorState`][osrlib.crawl.dungeon.DoorState], [`DropPile`][osrlib.crawl.dungeon.DropPile],
  [`DroppedItem`][osrlib.crawl.dungeon.DroppedItem], and
  [`GeneratedCache`][osrlib.crawl.dungeon.GeneratedCache], with
  [`cell_ref`][osrlib.crawl.dungeon.cell_ref] and [`edge_ref`][osrlib.crawl.dungeon.edge_ref] for
  its keys. [`TreasureBundle`][osrlib.crawl.dungeon.TreasureBundle] contains the treasure that monsters
  carry during an encounter.

Typical usage:

```python
from osrlib.crawl.dungeon import Direction, DungeonSpec, Edge, EdgeKind, LevelSpec, edge_key

# A two-cell corridor running west to east, entered at the west end.
corridor = LevelSpec(
    number=1,
    width=2,
    height=1,
    entrance=(0, 0),
    edges={edge_key((0, 0), Direction.EAST): Edge(kind=EdgeKind.OPEN)},
)
crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(corridor,))

print(edge_key((0, 0), Direction.EAST))
# 1,0:west

print(crypt.level(1).edge((0, 0), Direction.NORTH).kind)
# wall
```
"""

from collections.abc import Callable, Iterable
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from osrlib.core.alignment import Alignment
from osrlib.core.clock import TimeUnit
from osrlib.core.dice import parse
from osrlib.core.effects import Condition
from osrlib.core.items import Coins, MagicItemInstance, ValuableInstance
from osrlib.core.spells import SaveSpec
from osrlib.core.tables import EncounterTable, ReactionResult
from osrlib.crawl.gates import GateSpec

__all__ = [
    "AreaSpec",
    "AreaTreasureSpec",
    "Direction",
    "DoorSpec",
    "DoorState",
    "DropPile",
    "DroppedItem",
    "DungeonSpec",
    "DungeonState",
    "Edge",
    "EdgeKind",
    "FeatureSpec",
    "GeneratedCache",
    "KeyedEncounter",
    "KeyedMonster",
    "LevelSpec",
    "PartyLocation",
    "Position",
    "TransitionSpec",
    "TrapEffect",
    "TrapSpec",
    "TreasureBundle",
    "ValuableSpec",
    "WanderingSpec",
    "cell_ref",
    "edge_key",
    "edge_ref",
    "step",
]

Position = tuple[int, int]
"""A cell address on a level's grid: `(x, y)`.

`x` increases to the east and `y` increases to the south, from `(0, 0)` in the level's northwest
corner. A cell is 10 feet on a side. Write a position as a plain tuple, like `(3, 0)`. A position
means something only on a particular level. A position off a level's grid is out of bounds, not
malformed: call [`LevelSpec.in_bounds`][osrlib.crawl.dungeon.LevelSpec.in_bounds] to check one.
[`validate_adventure`][osrlib.crawl.adventure.validate_adventure] reports an authored position that
is off its level's grid."""


class Direction(StrEnum):
    """The four grid directions the party faces and moves in.

    You use these for the way [`MoveParty`][osrlib.crawl.commands.MoveParty] steps, the side of a
    cell [`OpenDoor`][osrlib.crawl.commands.OpenDoor] works on, and the way a transition turns the
    party on arrival. There is no up or down. A
    [`TransitionSpec`][osrlib.crawl.dungeon.TransitionSpec] moves the party between levels.

    The values are lowercase strings, like `"north"`, and they appear in commands, events, and saves.
    """

    NORTH = "north"
    """Decreasing `y`: toward the top of the map."""
    EAST = "east"
    """Increasing `x`: toward the right of the map."""
    SOUTH = "south"
    """Increasing `y`: toward the bottom of the map."""
    WEST = "west"
    """Decreasing `x`: toward the left of the map."""

    @property
    def vector(self) -> tuple[int, int]:
        """The `(dx, dy)` change in position for one step in this direction, like `(0, -1)` for north.

        Add it to a position to get the neighbouring cell, or call
        [`step`][osrlib.crawl.dungeon.step], which does the addition for you.
        """
        return _VECTORS[self]

    @property
    def opposite(self) -> Direction:
        """The reverse direction, like `SOUTH` for `NORTH`.

        A cell's edge in one direction is its neighbour's edge in the opposite direction. Use it to
        name a door from the cell on its other side, or to set up the return passage of a
        [`TransitionSpec`][osrlib.crawl.dungeon.TransitionSpec].
        """
        return _OPPOSITES[self]


_VECTORS = {
    Direction.NORTH: (0, -1),
    Direction.EAST: (1, 0),
    Direction.SOUTH: (0, 1),
    Direction.WEST: (-1, 0),
}

_OPPOSITES = {
    Direction.NORTH: Direction.SOUTH,
    Direction.EAST: Direction.WEST,
    Direction.SOUTH: Direction.NORTH,
    Direction.WEST: Direction.EAST,
}


def step(position: Position, direction: Direction) -> Position:
    """Return the cell one step from `position` in `direction`.

    It only adds the direction's `vector` to the position. It doesn't check walls, levels, or the
    party. Call [`LevelSpec.in_bounds`][osrlib.crawl.dungeon.LevelSpec.in_bounds] to find out whether
    the result is on the grid, and [`LevelSpec.edge`][osrlib.crawl.dungeon.LevelSpec.edge] to find
    out whether the party could walk there. Use it to lay out a corridor cell by cell while you
    write a level, or to find the cell the party would move into.

    Args:
        position: The starting cell.
        direction: The direction to step.

    Returns:
        The neighbouring cell. It can be off the level's grid.

    Examples:
        ```python
        from osrlib.crawl.dungeon import Direction, step

        print(step((1, 1), Direction.NORTH))
        # (1, 0)
        ```
    """
    dx, dy = direction.vector
    return (position[0] + dx, position[1] + dy)


def edge_key(position: Position, direction: Direction) -> str:
    """Return the key for the edge on `direction`'s side of `position`, for a level's `edges` map.

    Use it to write every key of a [`LevelSpec.edges`][osrlib.crawl.dungeon.LevelSpec.edges] map, so
    you don't have to work out which of the two cells to name an edge from. Every edge has exactly
    one key: a cell and its `north` or `west` side. A cell's south edge is the north edge of the cell
    below it, and its east edge is the west edge of the cell to its right. So
    `edge_key((0, 0), Direction.EAST)` and `edge_key((1, 0), Direction.WEST)` return the same string.

    The format is `"{x},{y}:{side}"`, like `"1,0:west"`. That is what a serialized adventure
    shows, and what [`LevelSpec.edge`][osrlib.crawl.dungeon.LevelSpec.edge] looks up for you.

    Args:
        position: The cell.
        direction: Which of the cell's four edges.

    Returns:
        The edge's key. It isn't checked against any level, so a cell off the grid gets a key in the
        same format.

    Examples:
        ```python
        from osrlib.crawl.dungeon import Direction, edge_key

        print(edge_key((0, 0), Direction.EAST))
        # 1,0:west

        print(edge_key((1, 0), Direction.WEST))
        # 1,0:west
        ```
    """
    x, y = position
    if direction is Direction.SOUTH:
        return f"{x},{y + 1}:north"
    if direction is Direction.EAST:
        return f"{x + 1},{y}:west"
    return f"{x},{y}:{direction.value}"


def cell_ref(dungeon_id: str, level_number: int, position: Position) -> str:
    """Return the string that identifies one cell in a whole adventure.

    A key in a level's `edges` map locates a cell within one level. A cell reference also includes
    the dungeon and the level, so it locates the cell anywhere in the adventure.
    [`DungeonState.piles`][osrlib.crawl.dungeon.DungeonState.piles] is keyed by it, and an
    [`ActiveEffect.target_ref`][osrlib.core.effects.ActiveEffect.target_ref] in this form puts an
    effect on a dungeon cell instead of on a creature.

    The format is `"cell:{dungeon}:{level}:{x},{y}"`, like `"cell:crypt:1:2,3"`.

    Args:
        dungeon_id: The dungeon's id, as on its [`DungeonSpec`][osrlib.crawl.dungeon.DungeonSpec].
        level_number: The level's 1-based number.
        position: The cell.

    Returns:
        The cell reference.

    Examples:
        ```python
        from osrlib.crawl.dungeon import cell_ref

        print(cell_ref("crypt", 1, (2, 3)))
        # cell:crypt:1:2,3
        ```
    """
    return f"cell:{dungeon_id}:{level_number}:{position[0]},{position[1]}"


def edge_ref(dungeon_id: str, level_number: int, position: Position, direction: Direction) -> str:
    """Return the string that identifies one edge in a whole adventure.

    [`DungeonState.doors`][osrlib.crawl.dungeon.DungeonState.doors] is keyed by it, so it is how you
    look up a door's open, wedged, discovered, and unlocked flags from a cell and a direction. The
    reference ends with the edge's [`edge_key`][osrlib.crawl.dungeon.edge_key], so you get the same
    reference from either side of a door, and the door has one state. Pass the result to
    [`DungeonState.door`][osrlib.crawl.dungeon.DungeonState.door].

    The format is `"{dungeon}:{level}:{x},{y}:{side}"`, like `"crypt:1:1,0:west"`.

    Args:
        dungeon_id: The dungeon's id.
        level_number: The level's 1-based number.
        position: The cell.
        direction: Which of the cell's four edges.

    Returns:
        The edge reference.

    Examples:
        ```python
        from osrlib.crawl.dungeon import Direction, edge_ref

        print(edge_ref("crypt", 1, (0, 0), Direction.EAST))
        # crypt:1:1,0:west
        ```
    """
    return f"{dungeon_id}:{level_number}:{edge_key(position, direction)}"


class EdgeKind(StrEnum):
    """What stands on an edge between two cells: nothing, a wall, or a door.

    It is the `kind` of an [`Edge`][osrlib.crawl.dungeon.Edge], and it sets whether the party can
    cross the edge.
    """

    OPEN = "open"
    """Nothing stands here, and the party can walk across."""
    WALL = "wall"
    """A wall, which the party can't cross. [`LevelSpec.edge`][osrlib.crawl.dungeon.LevelSpec.edge]
    also returns a wall for an edge with no entry in the level's `edges` map, so you write this kind
    only to state a wall explicitly."""
    DOOR = "door"
    """A door, which the edge's [`DoorSpec`][osrlib.crawl.dungeon.DoorSpec] describes. An edge of this
    kind must have a `door`, and an edge of any other kind must not."""


class DoorSpec(BaseModel):
    """A door on an edge, as you wrote it.

    Put one in the `door` field of an [`Edge`][osrlib.crawl.dungeon.Edge] whose `kind` is `door`.
    Every field is the door's state at the start of the game, and play never changes it. What the
    party does to the door goes into a [`DoorState`][osrlib.crawl.dungeon.DoorState] in the overlay
    instead, so read the overlay to find out whether a door is open now.

    Attributes:
        kind: Whether the party can see the door from the start, or has to find it.
        stuck: Whether the party has to force the door open.
        locked: Whether the party has to unlock the door before it opens.
        starts_open: Whether the door stands open at the start of the game.
        requires: A condition the party has to meet to open the door.

    Examples:
        ```python
        from osrlib.crawl.dungeon import DoorSpec, Edge, EdgeKind

        vault = Edge(kind=EdgeKind.DOOR, door=DoorSpec(locked=True))
        print(vault.door.locked, vault.door.kind)
        # True normal
        ```
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["normal", "secret"] = "normal"
    """`"normal"` for a door the party can see, or `"secret"` for one it has to find. A secret door is
    hidden until the party finds it with a [`Search`][osrlib.crawl.commands.Search] for secret
    doors, which sets `discovered` in the door's [`DoorState`][osrlib.crawl.dungeon.DoorState]. Until then the party
    can't open it, listen at it, or walk through it, and the player view shows the edge as a wall."""
    stuck: bool = False
    """Whether the door is stuck shut. The session refuses [`OpenDoor`][osrlib.crawl.commands.OpenDoor]
    on a stuck door, so the party has to force it open with
    [`ForceDoor`][osrlib.crawl.commands.ForceDoor]. Forcing is a Strength-based roll, and it makes
    noise whether or not it works. The session refuses `ForceDoor` on a door that isn't stuck."""
    locked: bool = False
    """Whether the door is locked. The session refuses both [`OpenDoor`][osrlib.crawl.commands.OpenDoor]
    and [`ForceDoor`][osrlib.crawl.commands.ForceDoor] on a locked door until it is unlocked, by a
    thief's [`PickLock`][osrlib.crawl.commands.PickLock] or by a referee's
    [`SetDoorState`][osrlib.crawl.commands.SetDoorState]. A door that is both locked and stuck has to
    be unlocked and then forced."""
    starts_open: bool = False
    """Whether the door stands open at the start of the game. Doors that the party opens swing shut
    behind it as it walks on, but a door that starts open doesn't, because the party didn't open it.
    The party can still close it with [`CloseDoor`][osrlib.crawl.commands.CloseDoor]."""
    requires: GateSpec | None = None
    """A gate on opening the door, or `None` for a door that opens without one. A gate
    ([`GateSpec`][osrlib.crawl.gates.GateSpec]) is a condition, like carrying a key, that the
    session checks each time the party tries to open or force the door. The session checks it last,
    after every other reason to refuse the command. It is separate from `locked`: to open a locked door with a gate,
    the party has to unlock it and meet the gate's condition. [`PickLock`][osrlib.crawl.commands.PickLock] deals only
    with the lock, and [`SetDoorState`][osrlib.crawl.commands.SetDoorState] changes the door without
    checking the gate. The gate guards opening the door, not passing through it, so the party walks
    through a door that stands open without a check. Once the door is closed again, the gate applies
    to the next opening."""


class Edge(BaseModel):
    """One entry in a level's `edges` map: what stands on the edge between two cells.

    Put these in [`LevelSpec.edges`][osrlib.crawl.dungeon.LevelSpec.edges], keyed by
    [`edge_key`][osrlib.crawl.dungeon.edge_key]. You need entries only for edges that aren't walls,
    because an edge with no entry is a wall. Read an edge back with
    [`LevelSpec.edge`][osrlib.crawl.dungeon.LevelSpec.edge], which returns a wall for an edge with no
    entry and for the level's boundary.

    Attributes:
        kind: What stands on the edge.
        door: The door, when `kind` is `door`.

    Raises:
        ValueError: If `kind` is `door` and `door` is `None`, or `door` is set on any other kind.

    Examples:
        ```python
        from osrlib.crawl.dungeon import DoorSpec, Edge, EdgeKind

        print(Edge(kind=EdgeKind.OPEN).door)
        # None

        try:
            Edge(kind=EdgeKind.OPEN, door=DoorSpec())
        except ValueError as error:
            print("an edge carries a door spec exactly when its kind is 'door'" in str(error))
        # True
        ```
    """

    model_config = ConfigDict(frozen=True)

    kind: EdgeKind
    """Whether the edge is open, a wall, or a door."""
    door: DoorSpec | None = None
    """The door on this edge when `kind` is `door`, and `None` for every other kind."""

    @model_validator(mode="after")
    def _door_exactly_on_door_edges(self) -> Edge:
        if (self.kind is EdgeKind.DOOR) != (self.door is not None):
            raise ValueError("an edge carries a door spec exactly when its kind is 'door'")
        return self


class TransitionSpec(BaseModel):
    """A way from one cell to another: stairs, a trapdoor, a chute, or a passage.

    Transitions belong to a level, in
    [`LevelSpec.transitions`][osrlib.crawl.dungeon.LevelSpec.transitions], not to an area. They join
    the levels of a dungeon, and they join two dungeons when one points at the other's id. Each
    transition stands on a cell and moves the party to `to_position`, facing `to_facing`.
    [`validate_adventure`][osrlib.crawl.adventure.validate_adventure] checks that the cell it stands on
    and the cell it leads to are both on their levels' grids.

    The party takes stairs, a trapdoor, or a chute with
    [`UseStairs`][osrlib.crawl.commands.UseStairs]. The party walks through a passage instead. A
    passage stands on one `side` of its cell, and a [`MoveParty`][osrlib.crawl.commands.MoveParty]
    from that cell in that direction moves the party to the passage's destination. That side of the
    cell is a wall in the level's `edges`. The cell one step across the wall is the passage's *far
    cell*, which has walls on every side and belongs to no area. Use a passage where a map shows two
    cells side by side that the adventure can't put side by side, like the floors of two levels that
    meet, or two halves of one map that you lay out apart.

    A transition leads one way only, and osrlib doesn't add the way back. Stairs the party can climb
    back up are two transitions, one on each level, each pointing at the other. Leave out the second
    one and you have a chute: the session refuses [`UseStairs`][osrlib.crawl.commands.UseStairs]
    at the bottom, because no transition stands on the arrival cell. A passage's way back is a second
    passage. It stands on the first passage's destination cell, on the side opposite the first
    passage's `to_facing`. Its destination is the first passage's cell, and its `to_facing` is
    opposite the first passage's `side`.

    Attributes:
        kind: Which kind of connection this is.
        position: The cell it stands on.
        side: The side of that cell a passage stands on, for a passage only.
        to_dungeon_id: The dungeon the party arrives in.
        to_level_number: The level the party arrives on.
        to_position: The cell the party arrives at.
        to_facing: The direction the party faces on arrival.
        requires: A condition the party has to meet to take it.

    Raises:
        ValueError: If `kind` is `"passage"` and `side` is `None`, or if `side` is set on any other
            kind.

    Examples:
        Stairs between two levels, one transition on each level:

        ```python
        from osrlib.crawl.adventure import Adventure, TownSpec, validate_adventure
        from osrlib.crawl.dungeon import Direction, DungeonSpec, LevelSpec, TransitionSpec
        from osrlib.data import load_equipment, load_monsters

        down = TransitionSpec(
            kind="stairs_down",
            position=(0, 0),
            to_dungeon_id="crypt",
            to_level_number=2,
            to_position=(1, 1),
            to_facing=Direction.SOUTH,
        )
        up = TransitionSpec(
            kind="stairs_up",
            position=(1, 1),
            to_dungeon_id="crypt",
            to_level_number=1,
            to_position=(0, 0),
            to_facing=Direction.NORTH,
        )
        top = LevelSpec(number=1, width=1, height=1, entrance=(0, 0), transitions=(down,))
        bottom = LevelSpec(number=2, width=2, height=2, transitions=(up,))
        crypt = DungeonSpec(id="crypt", levels=(top, bottom))
        adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))
        validate_adventure(adventure, load_monsters(), load_equipment())

        print(crypt.level(2).transition_at((1, 1)).to_level_number)
        # 1
        ```
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["stairs_up", "stairs_down", "trapdoor", "chute", "passage"]
    """What the party sees and uses: `"stairs_up"`, `"stairs_down"`, `"trapdoor"`, `"chute"`, or
    `"passage"`. The first four kinds all work the same way: the party takes them with
    [`UseStairs`][osrlib.crawl.commands.UseStairs], the destination fields set where it goes, and the
    session reports the kind on the arrival event, so you can describe it. The party crosses a
    `"passage"` by walking across its `side` instead."""
    position: Position
    """The cell on this level where the transition stands. The party has to stand here to use it
    with [`UseStairs`][osrlib.crawl.commands.UseStairs] or to cross it as a passage."""
    side: Direction | None = None
    """The side of `position` that a passage stands on, and `None` for every other kind. A
    [`MoveParty`][osrlib.crawl.commands.MoveParty] from `position` in this direction crosses the
    passage. A passage without a side raises `ValueError` when you construct it, and so does a
    transition of any other kind with one."""
    to_dungeon_id: str
    """The id of the dungeon the party arrives in. It is usually the dungeon this level is in. Set
    it to another dungeon of the adventure to join the two."""
    to_level_number: int = Field(ge=1)
    """The number of the level the party arrives on, 1 or more."""
    to_position: Position
    """The cell the party arrives at, on the destination level's grid."""
    to_facing: Direction
    """The direction the party faces on arrival. Draw the party's view facing this way."""
    requires: GateSpec | None = None
    """A gate on taking the transition, or `None` for one with no gate.

    [`UseStairs`][osrlib.crawl.commands.UseStairs], or [`MoveParty`][osrlib.crawl.commands.MoveParty]
    for a passage, checks the gate after the command's other checks and before the party moves. A
    gate that takes an item takes it then, as the party crosses. When the party changes level or
    dungeon, the gate's `success` text goes on the arrival's
    [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent]. For a transition to another
    cell of the same level, it goes on the [`PartyMovedEvent`][osrlib.crawl.events.PartyMovedEvent]
    for the landing cell.

    A transition in a [`TrapEffect`][osrlib.crawl.dungeon.TrapEffect] can't have a gate, because a
    trap moves the party by force and the party attempts nothing. `TrapEffect` raises `ValueError`
    for one that does."""

    @model_validator(mode="after")
    def _side_exactly_on_passages(self) -> TransitionSpec:
        if (self.kind == "passage") != (self.side is not None):
            raise ValueError("a transition names a side exactly when its kind is 'passage'")
        return self


class TrapEffect(BaseModel):
    """What a trap does when it springs.

    Put one in the `effect` of a [`TrapSpec`][osrlib.crawl.dungeon.TrapSpec], which sets when the
    trap springs and whom it hits. The fields combine: a dart trap deals damage, a pit deals falling
    damage and can add a condition, and a gas trap calls for a saving throw and kills on a failure.
    For a trap that your own code resolves, leave the other fields unset and describe the trap in
    `manual`.

    The session rolls a trap's damage, saves, and falls on its exploration stream. It makes no attack
    roll for a trap, so the damage always applies unless a save reduces it.

    A passed save always spares the victim from `kills` and from `condition`, whatever the save's
    `on_save` is, because B/X has no half death or half blindness. `on_save` affects only
    the damage: `"negates"` spares the victim from all of it, and `"half"` halves it.

    Attributes:
        damage_dice: The damage the trap deals.
        volley_dice: The number of projectiles, for a trap that fires several.
        save: The saving throw the victim rolls.
        kills: Whether the trap kills a victim who fails the save.
        condition: A condition a failed save inflicts.
        condition_duration_dice: The condition's duration, rolled.
        condition_duration_amount: The condition's duration, fixed.
        condition_duration_unit: The unit the duration counts in.
        fall_feet: How far the victim falls.
        transition: Where the trap moves the party.
        manual: A description of a trap your own code resolves.

    Raises:
        ContentValidationError: If `damage_dice`, `volley_dice`, or `condition_duration_dice` is not
            a valid dice expression.
        ValueError: If a duration field is set without a `condition`, if `volley_dice` is set without
            `damage_dice`, or if `transition` has a gate.

    Examples:
        ```python
        from osrlib.core.combat import SaveCategory
        from osrlib.core.spells import SaveSpec
        from osrlib.crawl.dungeon import TrapEffect

        # A dart trap: 1d6 darts, each for 1d4.
        darts = TrapEffect(damage_dice="1d4", volley_dice="1d6", save=SaveSpec(category=SaveCategory.WANDS))
        print(darts.kills)
        # False
        ```
    """

    model_config = ConfigDict(frozen=True)

    damage_dice: str | None = None
    """The damage the trap deals, as a dice expression like `"1d6"`, or `None` for a trap that deals
    none. With `volley_dice` set, it is the damage of each projectile. The model checks the
    expression when you construct it, so a malformed one fails then, not when the trap springs."""
    volley_dice: str | None = None
    """How many projectiles the trap fires, as a dice expression, or `None` for a trap that fires one
    or none. Use it for a dart trap: `volley_dice="1d6"` with `damage_dice="1d4"` fires 1d6 darts
    and rolls 1d4 damage for each. There's no dice expression for "roll this many times that much",
    so a volley uses two fields. Set it only with `damage_dice`."""
    save: SaveSpec | None = None
    """The saving throw the victim rolls, or `None` for a trap that allows none. Its `category` is the
    saving throw to roll, and its `on_save` sets what a passed save does to the damage:
    `"negates"` spares the victim from all of it, and `"half"` halves both the `damage_dice` damage
    and the falling damage, rounding down. A passed save always spares the victim from `kills` and
    `condition`."""
    kills: bool = False
    """Whether the trap kills a victim who fails the save, like a poison gas trap. A victim who
    passes the save survives, whatever `on_save` is. With no `save`, the trap kills every victim."""
    condition: Condition | None = None
    """A condition the trap gives a victim who fails the save, like blindness, or `None`. A victim who
    passes the save doesn't get it. With no `save`, every victim gets it."""
    condition_duration_dice: str | None = None
    """The condition's duration as a dice expression, for a duration that varies. Set this or
    `condition_duration_amount`, not both, together with `condition_duration_unit` and a `condition`.
    The session rolls it on the effects stream when the condition takes hold."""
    condition_duration_amount: int | None = None
    """The condition's duration as a fixed number, for a duration that doesn't vary. Set this or
    `condition_duration_dice`, not both, together with `condition_duration_unit` and a
    `condition`."""
    condition_duration_unit: TimeUnit | None = None
    """The unit the duration counts in: rounds, turns, or days. Leave the three duration fields unset
    for a condition that lasts until something removes it."""
    fall_feet: int | None = None
    """How far the victim falls, in feet, or `None` for a trap with no fall, like a pit's depth. The
    fall deals 1d6 damage for each full 10 feet, the OSE SRD's falling rule, as
    [`falling_damage`][osrlib.core.combat.falling_damage] rolls it. That damage is separate from
    `damage_dice`, and a `"half"` save halves it too."""
    transition: TransitionSpec | None = None
    """Where the trap moves the party, or `None` for a trap that moves no one. Use it for a chute
    that slides the party to another cell or level. The party has one location, so the trap moves
    the whole party or no one. With a `save`, it moves the party when at least one victim fails
    the save, and leaves the party on the trapped cell when every victim passes. With no `save`,
    it always moves the party. The transition can't have a `requires` gate, because the party
    doesn't choose to take it. [`validate_adventure`][osrlib.crawl.adventure.validate_adventure]
    reports a transition whose `kind` is `"passage"`."""
    manual: str | None = None
    """A description of a trap your own code resolves, or `None`. osrlib never reads it. Use it for a
    trap whose effect is the referee's call rather than a die roll."""

    @field_validator("damage_dice", "volley_dice", "condition_duration_dice")
    @classmethod
    def _dice_must_parse(cls, value: str | None) -> str | None:
        if value is not None:
            parse(value)
        return value

    @model_validator(mode="after")
    def _condition_duration_needs_a_condition(self) -> TrapEffect:
        has_duration = (
            self.condition_duration_dice is not None
            or self.condition_duration_amount is not None
            or self.condition_duration_unit is not None
        )
        if has_duration and self.condition is None:
            raise ValueError("a condition duration needs a condition")
        if self.volley_dice is not None and self.damage_dice is None:
            raise ValueError("a volley needs per-projectile damage dice")
        if self.transition is not None and self.transition.requires is not None:
            # The chute moves the party by force. The party never attempts it, so
            # there is no attempt for a gate to guard.
            raise ValueError("a trap effect's transition never gates: it is a forced relocation, not an attempt")
        return self


class TrapSpec(BaseModel):
    """A trap: when it springs, what it does, and whom it hits.

    A trap can be in two places, and its `kind` sets which. A room trap goes on an
    [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] and covers the whole area. A treasure trap goes on a
    [`FeatureSpec`][osrlib.crawl.dungeon.FeatureSpec] and guards that one cache. Each of the two
    models accepts only one kind of trap.

    A trap doesn't always spring. Each time the party does the thing that springs it, the session
    rolls a d6, and the trap springs on a 1 or 2, the OSE SRD's 2-in-6 rule. A trap that has sprung
    never springs again. The party can find a trap before it springs: a room trap with a
    [`Search`][osrlib.crawl.commands.Search] for room traps, and a treasure trap with a thief's
    [`InspectTreasure`][osrlib.crawl.commands.InspectTreasure]. A room trap that the party has found
    no longer springs, so the party walks around it. For a treasure trap that the party has found,
    the session still rolls when the party opens the cache, until a thief takes it out with
    [`RemoveTreasureTrap`][osrlib.crawl.commands.RemoveTreasureTrap]. There is no way to disarm a room
    trap.

    The traps you write are the only traps in the game. Treasure the session rolls is never trapped.

    Attributes:
        kind: Whether this is a room trap or a treasure trap.
        trigger: The action that springs it.
        effect: What it does when it springs.
        affects: Whom it hits.

    Raises:
        ValueError: If `kind` is `"treasure"` and `trigger` is not `"open"`.

    Examples:
        ```python
        from osrlib.core.combat import SaveCategory
        from osrlib.core.spells import SaveSpec
        from osrlib.crawl.dungeon import AreaSpec, FeatureSpec, TrapEffect, TrapSpec

        # Poison gas fills the room when the party walks in: every member saves or dies.
        gas = TrapSpec(
            kind="room",
            trigger="enter",
            affects="party",
            effect=TrapEffect(kills=True, save=SaveSpec(category=SaveCategory.DEATH)),
        )
        tomb = AreaSpec(id="tomb", cells=((2, 0),), trap=gas)

        # A poisoned needle in the lock of a chest.
        needle = TrapSpec(kind="treasure", trigger="open", effect=TrapEffect(damage_dice="1d4"))
        chest = FeatureSpec(id="chest", kind="treasure_cache", trap=needle)

        try:
            AreaSpec(id="vault", cells=((3, 0),), trap=needle)
        except ValueError as error:
            print("non-room trap" in str(error))
        # True
        ```
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["room", "treasure"]
    """`"room"` for a trap over an area, or `"treasure"` for a trap on a cache.
    [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] accepts only room traps, and
    [`FeatureSpec`][osrlib.crawl.dungeon.FeatureSpec] accepts only treasure traps."""
    trigger: Literal["enter", "open"]
    """The action that springs the trap.

    - `"enter"`: the party moves into a cell of the trapped area. The session rolls for the trap on
      every move into one of its cells, including moves within the area, until it springs or the
      party finds it.
    - `"open"` on a room trap: the party opens or forces a door on an edge of one of the area's
      cells, from either side, like a blade that drops when the door swings.
    - `"open"` on a treasure trap: the party opens the cache with
      [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure].

    A treasure trap has to use `"open"`, because there is nothing to walk into on a cache."""
    effect: TrapEffect
    """What the trap does once it springs. See [`TrapEffect`][osrlib.crawl.dungeon.TrapEffect]."""
    affects: Literal["triggerer", "party"] = "triggerer"
    """Whom the effect hits. `"party"` hits every living member, like poison gas filling a room.
    `"triggerer"`, the default, hits one character: the member named in the
    [`ForceDoor`][osrlib.crawl.commands.ForceDoor] or [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure]
    that sprang it, the thief whose [`RemoveTreasureTrap`][osrlib.crawl.commands.RemoveTreasureTrap]
    failed, or otherwise the first living member in marching order."""

    @model_validator(mode="after")
    def _treasure_traps_spring_on_open(self) -> TrapSpec:
        if self.kind == "treasure" and self.trigger != "open":
            raise ValueError("a treasure trap springs on open; a cache has nothing to enter")
        return self


class ValuableSpec(BaseModel):
    """A gem or piece of jewellery that you place in a cache by hand.

    Use it when the treasure is a particular thing with a name, not one of the gems the session
    rolls on the treasure tables. Put it in the `valuables` of a [`FeatureSpec`][osrlib.crawl.dungeon.FeatureSpec].
    When the party empties the cache with [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure], the
    session turns it into a [`ValuableInstance`][osrlib.core.items.ValuableInstance] with an id.

    Attributes:
        kind: Whether it is a gem or jewellery.
        name: The name the party sees.
        value_gp: What it is worth, in gold pieces.
        weight_coins: What it weighs, in coins.

    Examples:
        ```python
        from osrlib.crawl.dungeon import FeatureSpec, ValuableSpec

        chest = FeatureSpec(
            id="abbot_chest",
            kind="treasure_cache",
            valuables=(ValuableSpec(kind="jewellery", name="The abbot's seal ring", value_gp=900),),
        )
        print(chest.valuables[0].value_gp)
        # 900
        ```
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["gem", "jewellery"]
    """Whether the valuable is a gem or jewellery. No rule in osrlib depends on it. Use it for display,
    and for any rule you apply to one kind and not the other."""
    name: str = ""
    """The name the party sees, like `"The abbot's seal ring"`. Leave it empty for an unnamed
    valuable, like the ones the session rolls on the treasure tables."""
    value_gp: int = Field(ge=0)
    """What the valuable is worth in gold pieces. It sells for this much in town, and it is worth
    this much experience to the party."""
    weight_coins: int = Field(default=0, ge=0)
    """What the valuable weighs, in coins, for encumbrance. The default, 0, makes it weightless, as
    a gem usually is."""


class AreaTreasureSpec(BaseModel):
    """Treasure the session rolls for an area, with no monster guarding it.

    Put one on an [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] when you want the room to contain treasure
    but don't want to choose it. The session rolls it the first time the party enters the area, and
    puts the result on the cell where the party entered, as a
    [`GeneratedCache`][osrlib.crawl.dungeon.GeneratedCache]. The party takes it with
    [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure]. If the roll comes up empty, the session
    puts nothing down. Rolled treasure is never trapped. To trap treasure, write a
    [`FeatureSpec`][osrlib.crawl.dungeon.FeatureSpec] with a trap.

    When the treasure belongs to the monsters in the room, use the `hoard` flag of the area's
    [`KeyedEncounter`][osrlib.crawl.dungeon.KeyedEncounter] instead. The session then rolls the
    monsters' lair treasure.

    Attributes:
        letters: The treasure type letters to roll.
        unguarded: Whether to roll on the unguarded-treasure table for the level instead.

    Raises:
        ValueError: If both `letters` and `unguarded` are given, or neither.

    Examples:
        ```python
        from osrlib.crawl.dungeon import AreaTreasureSpec

        print(AreaTreasureSpec(letters=("C",)).unguarded)
        # False
        ```
    """

    model_config = ConfigDict(frozen=True)

    letters: tuple[str, ...] = ()
    """One or more B/X treasure type letters, like `("C",)`. The session rolls each letter's table
    and puts the results in one cache. The letters are `A` to `V`. See
    [the treasure type index](https://mmacy.github.io/osrlib-python/reference/content-ids/treasure-types/),
    or read the `letter` of each entry in the `treasure_types` of
    [`load_treasure_tables`][osrlib.data.load_treasure_tables]`()`. Set this or `unguarded`, not
    both."""
    unguarded: bool = False
    """Whether to roll on the OSE SRD's table for unguarded treasure instead of listing letters. The
    table has a row for each band of dungeon levels, and the session uses the row for the number of
    the level the area is on, so the treasure is bigger on deeper levels. Set this or `letters`, not
    both."""

    @model_validator(mode="after")
    def _letters_or_unguarded(self) -> AreaTreasureSpec:
        if bool(self.letters) == self.unguarded:
            raise ValueError("an area treasure spec names letters or sets unguarded, not both or neither")
        return self


class TreasureBundle(BaseModel):
    """Rolled treasure grouped as one lot: coins, valuables, and magic items.

    When the session spawns monsters, it rolls the treasure they carry into bundles. You find them
    on an [`EncounterGroup`][osrlib.crawl.encounter.EncounterGroup]: `member_treasure` contains what
    each monster carries, and `group_treasure` contains what the group shares. When the encounter ends,
    the bundles of slain monsters drop onto the party's cell as a
    [`DropPile`][osrlib.crawl.dungeon.DropPile]. The descriptions of those two fields give the rules
    for which bundles drop. The session also uses a bundle while it rolls a hoard, then moves the
    contents into a [`GeneratedCache`][osrlib.crawl.dungeon.GeneratedCache].

    Unlike the authored models in this module, a bundle is mutable, because the session adds to it
    one entry at a time as it rolls treasure.

    Attributes:
        coins: The coins in the bundle.
        valuables: The gems and jewellery in the bundle.
        magic_items: The magic items in the bundle.
    """

    model_config = ConfigDict(validate_assignment=True)

    coins: Coins = Coins()
    """The coins, by denomination. A new bundle has none."""
    valuables: list[ValuableInstance] = []
    """The gems and jewellery, each an instance with an id and a value."""
    magic_items: list[MagicItemInstance] = []
    """The magic items, each an instance with its charges or quantity already rolled."""

    @property
    def empty(self) -> bool:
        """Whether the bundle has no coins, valuables, or magic items.

        The session can roll nothing at all for a hoard. It checks this before it puts a cache down, so it
        never puts down an empty one.
        """
        return self.coins.total_coins == 0 and not self.valuables and not self.magic_items


class GeneratedCache(BaseModel):
    """Treasure the session rolled and put down in the dungeon, recorded in the overlay.

    A cache you write is a [`FeatureSpec`][osrlib.crawl.dungeon.FeatureSpec], and it never changes.
    This is the other kind: treasure the session rolled for a keyed encounter's lair hoard or for an
    [`AreaTreasureSpec`][osrlib.crawl.dungeon.AreaTreasureSpec]. The session puts it in
    [`DungeonState.generated_caches`][osrlib.crawl.dungeon.DungeonState.generated_caches] under a new
    id like `"cache-0001"`, and reports the id in a
    [`HoardGeneratedEvent`][osrlib.crawl.events.HoardGeneratedEvent]. When the party takes it with
    [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure] and that id, the session removes the cache.
    Whatever the party can't carry goes into a [`DropPile`][osrlib.crawl.dungeon.DropPile] on the
    same cell.

    The session never traps treasure it rolls, so the only trapped treasure is treasure you write.
    That choice is one of the entries in
    [the adaptations register](https://mmacy.github.io/osrlib-python/adaptations/), the page that
    lists where osrlib picks one reading of an unclear rule or sets a default behind a
    [`Ruleset`][osrlib.core.ruleset.Ruleset] option.

    Attributes:
        cell_ref: The cell the cache is on.
        treasure_types: The tables the treasure was rolled on.
        coins: The coins in it.
        valuables: The gems and jewellery in it.
        magic_items: The magic items in it.
    """

    model_config = ConfigDict(validate_assignment=True)

    cell_ref: str
    """The cell the cache is on, as a [`cell_ref`][osrlib.crawl.dungeon.cell_ref] string. A lair
    hoard is on the first cell in its area's `cells`. An area's rolled treasure is on the cell where
    the party entered the area. The party has to stand on this cell to take the cache."""
    treasure_types: tuple[str, ...] = ()
    """The tables the treasure was rolled on, for display and for a referee who wants to see them.
    For treasure type letters, it contains the letters, like `("C",)`. For unguarded treasure, it is
    `("unguarded",)`. For a lair hoard that is only a fixed number of gold pieces, it is empty."""
    coins: Coins = Coins()
    """The coins in the cache, by denomination."""
    valuables: list[ValuableInstance] = []
    """The gems and jewellery in the cache, each with an id."""
    magic_items: list[MagicItemInstance] = []
    """The magic items in the cache, each an instance with its charges or quantity already rolled."""


class FeatureSpec(BaseModel):
    """A keyed thing in a room: a treasure cache, a construction trick, or custom content.

    Put features on an [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec], or on a
    [`LevelSpec`][osrlib.crawl.dungeon.LevelSpec] for one outside any room. A feature is something in
    the dungeon that the party can find and act on. Stairs aren't features: they are
    [`TransitionSpec`][osrlib.crawl.dungeon.TransitionSpec]s.

    osrlib runs one kind of feature for you, the `treasure_cache`. The party empties it with
    [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure] and the feature's id, and the contents go to
    the party. The session creates the magic items you list only when the party empties the cache.
    It rolls their details then, like charges, quantities, or whether a sword is intelligent, on the
    treasure stream, with [`instantiate_magic_item`][osrlib.core.treasure.instantiate_magic_item]. A
    `construction_trick` is one of the OSE SRD's odd architectural features, like a room that rotates
    or a passage that is an illusion. The party finds one with a
    [`Search`][osrlib.crawl.commands.Search] for construction, and you describe what it does.
    osrlib does nothing with a `custom` feature.

    Attributes:
        id: The feature's id, unique across its level.
        kind: Which sort of feature it is.
        description: Text describing the feature.
        cell: The cell it is on, or `None` to bind it to a whole area.
        item_ids: Ordinary items in a cache.
        magic_item_ids: Magic items in a cache.
        coins: Coins in a cache.
        valuables: Named gems and jewellery in a cache.
        trap: A treasure trap guarding the cache.

    Raises:
        ValueError: If `trap` is not a treasure trap.

    Examples:
        ```python
        from osrlib.core.items import Coins
        from osrlib.crawl.dungeon import FeatureSpec

        chest = FeatureSpec(
            id="altar_chest",
            kind="treasure_cache",
            description="A banded chest under the altar.",
            cell=(3, 0),
            item_ids=("rope_50", "holy_water"),
            coins=Coins(gp=120),
        )
        print(chest.coins.gp, chest.item_ids)
        # 120 ('rope_50', 'holy_water')
        ```
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """The feature's id. It has to be unique across the level, counting the features on the level
    and on all its areas together. You pass it to [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure]
    and the other feature commands. The id `"pile"` is reserved for the drop pile on a cell, and
    [`validate_adventure`][osrlib.crawl.adventure.validate_adventure] reports a feature that uses
    it."""
    kind: Literal["treasure_cache", "construction_trick", "custom"]
    """What kind of feature this is. osrlib runs `"treasure_cache"` for the party. You decide what
    `"construction_trick"` and `"custom"` do."""
    description: str = ""
    """Text to show when the party finds the feature. Events contain the feature's id, not its text,
    so look the text up here."""
    cell: Position | None = None
    """The cell the feature is on. A feature listed on a level needs one. A feature listed on an area
    can leave it `None`, and then the party can reach it from any cell of the area. When you set it
    on an area's feature, use one of the area's cells."""
    item_ids: tuple[str, ...] = ()
    """Ordinary items in the cache, by template id, one item for each id. Use an id from
    [`load_equipment`][osrlib.data.load_equipment]`()`, listed in
    [the equipment id index](https://mmacy.github.io/osrlib-python/reference/content-ids/equipment/),
    or the id of an item in the adventure's `items`."""
    magic_item_ids: tuple[str, ...] = ()
    """Magic items in the cache, by template id. Use an id from
    [`load_magic_items`][osrlib.data.load_magic_items]`()`, listed in
    [the magic item id index](https://mmacy.github.io/osrlib-python/reference/content-ids/magic-items/).
    You can't bundle magic items in an adventure, so only built-in ids work here."""
    coins: Coins = Coins()
    """Coins in the cache, by denomination."""
    valuables: tuple[ValuableSpec, ...] = ()
    """Named gems and jewellery in the cache. See
    [`ValuableSpec`][osrlib.crawl.dungeon.ValuableSpec]."""
    trap: TrapSpec | None = None
    """A trap guarding the cache, or `None`. It has to be a treasure trap, which can spring when the
    party opens the cache. A thief finds it with
    [`InspectTreasure`][osrlib.crawl.commands.InspectTreasure] and takes it out with
    [`RemoveTreasureTrap`][osrlib.crawl.commands.RemoveTreasureTrap]. Each character gets one try at
    each, and a failed removal springs the trap."""

    @model_validator(mode="after")
    def _trap_kind_matches(self) -> FeatureSpec:
        if self.trap is not None and self.trap.kind != "treasure":
            raise ValueError(f"feature {self.id!r} carries a non-treasure trap")
        return self


class KeyedMonster(BaseModel):
    """One line of a keyed encounter: which monster, and how many.

    A [`KeyedEncounter`][osrlib.crawl.dungeon.KeyedEncounter] has a tuple of these, so a room with
    four orcs and their ogre bodyguard has two lines. Give each line either a fixed count or count
    dice, not both.

    Attributes:
        template_id: Which monster.
        count_dice: How many, rolled when they spawn.
        count_fixed: How many, as a fixed number.

    Raises:
        ContentValidationError: If `count_dice` is not a valid dice expression.
        ValueError: If both `count_dice` and `count_fixed` are given, or neither.

    Examples:
        ```python
        from osrlib.crawl.dungeon import KeyedEncounter, KeyedMonster

        guards = KeyedEncounter(monsters=(KeyedMonster(template_id="goblin", count_fixed=4),))
        print(guards.monsters[0].template_id, guards.monsters[0].count_fixed)
        # goblin 4
        ```
    """

    model_config = ConfigDict(frozen=True)

    template_id: str
    """The monster's template id. Use an id from [`load_monsters`][osrlib.data.load_monsters]`()`,
    listed in [the monster id index](https://mmacy.github.io/osrlib-python/reference/content-ids/monsters/),
    or the id of a monster in the adventure's `monsters`."""
    count_dice: str | None = None
    """How many appear, as a dice expression like `"2d4"`. The session rolls it on the
    [`WANDERING_STREAM`][osrlib.crawl.session.WANDERING_STREAM] when the monsters spawn, and a roll
    below 1 counts as 1. Set this or `count_fixed`, not both. The model checks the expression when
    you construct it, so a malformed one fails then, not during play."""
    count_fixed: int | None = None
    """How many appear, as a fixed number. Set this or `count_dice`, not both. A printed adventure
    usually gives a fixed number, and so does [`stock_area`][osrlib.crawl.stocking.stock_area] when
    it rolls a room for you."""

    @field_validator("count_dice")
    @classmethod
    def _dice_must_parse(cls, value: str | None) -> str | None:
        if value is not None:
            parse(value)
        return value

    @model_validator(mode="after")
    def _dice_or_fixed(self) -> KeyedMonster:
        if (self.count_dice is None) == (self.count_fixed is None):
            raise ValueError("exactly one of count_dice or count_fixed is required")
        return self


class KeyedEncounter(BaseModel):
    """The monsters waiting in a keyed area, and what you decide about them in advance.

    Put one on an [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec]. The monsters spawn when the party
    enters any cell of the area, and the session opens an encounter. The session rolls surprise,
    distance, and reaction, though you can set the reaction here, and stop the monsters from being
    surprised. When the encounter ends with every monster slain or routed, the area is clear, and no
    monsters spawn when the party enters it again.

    Attributes:
        monsters: The monster lines, each a template and a count.
        alignment: A fixed alignment for monsters that can have more than one.
        aware: Whether the monsters expect the party.
        stance: A fixed reaction, instead of a reaction roll.
        hoard: Whether the monsters have their lair treasure.

    Examples:
        ```python
        from osrlib.core.tables import ReactionResult
        from osrlib.crawl.dungeon import KeyedEncounter, KeyedMonster

        ambush = KeyedEncounter(
            monsters=(KeyedMonster(template_id="goblin", count_fixed=6),),
            aware=True,
            stance=ReactionResult.ATTACKS,
        )
        print(ambush.aware, ambush.hoard)
        # True True
        ```
    """

    model_config = ConfigDict(frozen=True)

    monsters: tuple[KeyedMonster, ...] = Field(min_length=1)
    """The monster lines in the encounter, at least one. See
    [`KeyedMonster`][osrlib.crawl.dungeon.KeyedMonster]."""
    alignment: Alignment | None = None
    """The alignment every spawned monster takes, for a template that allows more than one. With
    `None`, the default, the session rolls it as usual. It has to be an alignment that each line's template allows, and
    [`validate_adventure`][osrlib.crawl.adventure.validate_adventure] reports one that isn't."""
    aware: bool = False
    """Whether the monsters expect the party. Aware monsters can't be surprised, so set it for a
    lookout, or for an ambush that has heard the party coming."""
    stance: ReactionResult | None = None
    """The monsters' reaction, used instead of a reaction roll. Set it for monsters that always
    attack on sight, or that are always friendly. Leave it `None`, the default, to roll the reaction
    as usual."""
    hoard: bool = True
    """Whether the monsters have their lair treasure. With the default, `True`, the session rolls the
    treasure types in each monster's `treasure` the first time the monsters spawn. It puts the result
    on the first cell of the area as a [`GeneratedCache`][osrlib.crawl.dungeon.GeneratedCache]. If the
    party leaves and comes back before the area is clear, the monsters spawn again, but the session
    doesn't roll the hoard again. Set it `False` for a monster room with no treasure. Stocking a
    dungeon by the B/X rules often produces such a room, because only some monster rooms get treasure
    on the stocking roll."""


class AreaSpec(BaseModel):
    """A keyed room or cave: a named group of cells with content bound to it.

    Areas are how you key a dungeon, the way a printed adventure numbers its rooms. Each area covers
    some cells of a [`LevelSpec`][osrlib.crawl.dungeon.LevelSpec], and a cell outside every area is
    corridor. When the party moves into a cell of the area, the session rolls the area's treasure,
    rolls for a trap that springs on entering, and spawns the encounter, in that order. Showing the
    area's description is up to you.

    Attributes:
        id: The area's id, unique across its level.
        name: The room's name.
        description: Text describing the room.
        cells: The cells the area covers.
        encounter: The monsters waiting here.
        features: The keyed things in the room.
        trap: A room trap over the whole area.
        treasure: Unguarded treasure the session rolls.

    Raises:
        ValueError: If `trap` is not a room trap.

    Examples:
        ```python
        from osrlib.crawl.dungeon import AreaSpec, KeyedEncounter, KeyedMonster

        guard_post = AreaSpec(
            id="guard_post",
            name="Guard post",
            description="Two goblins crouch over a game of knucklebones.",
            cells=((3, 0),),
            encounter=KeyedEncounter(monsters=(KeyedMonster(template_id="goblin", count_fixed=2),)),
        )
        print(guard_post.cells)
        # ((3, 0),)
        ```
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """The area's id, unique within the level. Events contain it, triggers match on it, and the
    overlay stores the state of the area's encounter, treasure, and trap under it."""
    name: str = ""
    """The room's name, to show the player, like `"Guard post"` or `"The abbot's cell"`."""
    description: str = ""
    """Text to show when the party walks in. Events contain the area's id, not its text, so look the
    text up here."""
    cells: tuple[Position, ...] = Field(min_length=1)
    """The cells the area covers, at least one. They don't have to touch each other, though a room's
    cells usually do. Every cell has to be on the level's grid. The first cell is where the session
    puts the encounter's lair hoard. Don't list a cell in two areas: a cell belongs to the first area
    that lists it."""
    encounter: KeyedEncounter | None = None
    """The monsters waiting in the room, or `None` for a room without any. See
    [`KeyedEncounter`][osrlib.crawl.dungeon.KeyedEncounter]."""
    features: tuple[FeatureSpec, ...] = ()
    """The keyed things in the room: treasure caches, construction tricks, and custom features. A
    feature here can leave its `cell` unset, and then the party can reach it from any cell of the
    area."""
    trap: TrapSpec | None = None
    """A trap over the whole area, or `None`. It has to be a room trap. It can spring when the party
    moves into a cell of the area, or, with `trigger="open"`, when the party opens a door on the
    edge of one of the area's cells."""
    treasure: AreaTreasureSpec | None = None
    """Treasure the session rolls the first time the party enters, for a room with treasure and no
    monster guarding it, or `None`. For a room whose treasure belongs to its monsters, use the
    encounter's `hoard` flag instead."""

    @model_validator(mode="after")
    def _trap_kind_matches(self) -> AreaSpec:
        if self.trap is not None and self.trap.kind != "room":
            raise ValueError(f"area {self.id!r} carries a non-room trap")
        return self


class WanderingSpec(BaseModel):
    """A level's wandering-monster check: how often the session rolls it, and on what table.

    Every [`LevelSpec`][osrlib.crawl.dungeon.LevelSpec] has one. The default is the B/X rule, so you
    write your own only to change the chance or the monsters. The session runs the check as the
    party spends turns on the level. You don't roll it yourself.

    Attributes:
        chance_in_six: The base chance of monsters, in six.
        interval_turns: How many turns pass between checks.
        table: An encounter table of your own for this level.

    Examples:
        ```python
        from osrlib.crawl.dungeon import WanderingSpec

        quiet = WanderingSpec(chance_in_six=0)
        print(quiet.interval_turns)
        # 2
        ```
    """

    model_config = ConfigDict(frozen=True)

    chance_in_six: int = Field(default=1, ge=0, le=6)
    """The base chance, in six, that each check brings monsters. The default, 1, is the B/X rule. The
    session adjusts it for each check, within 0 to 6. Noise since the last check adds 1, and light as
    bright as daylight adds 1. A rest with [`hidden`][osrlib.crawl.commands.Rest.hidden] set, where
    the party hides while it rests, subtracts 1. With 0, a level has wandering monsters only after
    noise or under bright light. Set it higher for a busier level."""
    interval_turns: int = Field(default=2, ge=1)
    """How many exploration turns pass between checks. The default, 2, is the B/X rule."""
    table: EncounterTable | None = None
    """An encounter table of your own for this level, or `None` to use the built-in table for the
    level's number. Your table replaces the built-in one completely, so use it to give a level
    different monsters. It has the same rows as the built-in tables from
    [`load_encounter_tables`][osrlib.data.load_encounter_tables], and
    [`stock_area`][osrlib.crawl.stocking.stock_area] can roll on it too."""


class LevelSpec(BaseModel):
    """One dungeon level: a grid of 10-foot cells with its edges, rooms, and stairs.

    You give a level a size, declare the edges that aren't walls, key some of its cells as areas, and add features and
    transitions. Then you put one or more levels in a [`DungeonSpec`][osrlib.crawl.dungeon.DungeonSpec], and the dungeon
    in an [`Adventure`][osrlib.crawl.adventure.Adventure].

    Its methods return what the session looks up about a level: whether a cell is on the grid, what
    stands on one side of it, which area it belongs to, and whether a transition stands on it. Call
    them to draw a map, or to check a level while you write it.

    Attributes:
        number: The level's number. Level 1 is the top level, and deeper levels have higher numbers.
        width: The grid's width in cells.
        height: The grid's height in cells.
        edges: Every edge that isn't a wall.
        areas: The keyed rooms.
        features: Features on the level, outside any room.
        transitions: The stairs, trapdoors, chutes, and passages.
        wandering: The wandering-monster check.
        entrance: Where the party arrives from town.
        guidance: Instructions for a narrator that uses a language model.

    Examples:
        ```python
        from osrlib.crawl.dungeon import Direction, Edge, EdgeKind, LevelSpec

        # Two cells, joined west to east, entered at the west end.
        corridor = LevelSpec(
            number=1,
            width=2,
            height=1,
            entrance=(0, 0),
            edges={"1,0:west": Edge(kind=EdgeKind.OPEN)},
        )
        print(corridor.edge((0, 0), Direction.EAST).kind)
        # open

        print(corridor.edge((0, 0), Direction.NORTH).kind)
        # wall
        ```
    """

    model_config = ConfigDict(frozen=True)

    number: int = Field(ge=1)
    """The level's number, 1 or more, where level 1 is the top. The session uses it to select the
    built-in wandering-monster table and the row of the unguarded-treasure table. The numbers have to
    be unique within a dungeon, and a [`TransitionSpec`][osrlib.crawl.dungeon.TransitionSpec] refers
    to the level it leads to by its number."""
    width: int = Field(ge=1)
    """How many cells wide the grid is, west to east. `x` runs from `0` to `width - 1`."""
    height: int = Field(ge=1)
    """How many cells tall the grid is, north to south. `y` runs from `0` to `height - 1`."""
    edges: dict[str, Edge] = {}
    """Every edge on the grid that isn't a wall, as an [`Edge`][osrlib.crawl.dungeon.Edge]. Write
    each key with [`edge_key`][osrlib.crawl.dungeon.edge_key], like `"1,0:west"`, and only for an
    edge between two cells on the grid. An edge with no entry is a wall, and so is the level's
    boundary, so an empty map is a level of solid rock. Read an edge with
    [`edge`][osrlib.crawl.dungeon.LevelSpec.edge] instead of indexing the map, because it works out
    the key and returns a wall for an edge with no entry."""
    areas: tuple[AreaSpec, ...] = ()
    """The level's keyed rooms and caves. Cells outside every area are corridor. Area ids have to be
    unique within the level, and every cell an area lists has to be on the grid."""
    features: tuple[FeatureSpec, ...] = ()
    """Features outside any room, like a cache in a corridor or a trick in a dead end. Each one needs
    a `cell`, because there is no area to bind it to. Their ids have to be unique together with the
    ids of the areas' features."""
    transitions: tuple[TransitionSpec, ...] = ()
    """The stairs, trapdoors, chutes, and passages on this level, each on one cell. A transition
    belongs to the level, not to an area, even when it stands inside a room."""
    wandering: WanderingSpec = WanderingSpec()
    """The level's wandering-monster check. The default is the B/X rule: a 1-in-6 chance every two
    turns, on the built-in table for this level's number."""
    entrance: Position | None = None
    """The cell the party arrives at from town, or `None` for a level with no way in from outside.
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] puts the party here facing north, whatever
    is around the cell. The session refuses [`TravelToTown`][osrlib.crawl.commands.TravelToTown]
    unless the party stands on its level's entrance. One level of every dungeon needs an entrance, and
    [`validate_adventure`][osrlib.crawl.adventure.validate_adventure] reports a dungeon without
    one."""
    guidance: str = ""
    """Instructions for a narrator that uses a language model, while the party is on this level: the
    mood of the place, what to say about it, and what never to say.

    osrlib never reads it, no event includes it, and no rule depends on it. To use it, read it from
    the adventure document. The player view doesn't include it, so treat it like an area's
    description, and don't show it to a player as written."""

    def in_bounds(self, position: Position) -> bool:
        """Return whether a cell is on this level's grid.

        Everything off the grid is solid rock: the party can't walk there, and an authored position
        off the grid is an error that [`validate_adventure`][osrlib.crawl.adventure.validate_adventure]
        reports.

        Args:
            position: The cell to test.

        Returns:
            True when `0 <= x < width` and `0 <= y < height`.

        Examples:
            ```python
            from osrlib.crawl.dungeon import Direction, LevelSpec, step

            corridor = LevelSpec(number=1, width=2, height=1, entrance=(0, 0))
            print(corridor.in_bounds((1, 0)), corridor.in_bounds(step((1, 0), Direction.EAST)))
            # True False
            ```
        """
        x, y = position
        return 0 <= x < self.width and 0 <= y < self.height

    def edge(self, position: Position, direction: Direction) -> Edge:
        """Return what stands on one side of a cell.

        Use it instead of indexing `edges` yourself. It works out the key, so a cell's east side and
        its neighbour's west side give the same answer, and it returns a wall for an edge with no
        entry. Call it to find out whether the party can walk that way and whether a door stands
        there. To read the door's state in play, pass the same cell and direction to
        [`edge_ref`][osrlib.crawl.dungeon.edge_ref].

        Args:
            position: The cell.
            direction: Which of the cell's four edges.

        Returns:
            The edge from `edges`, or a wall when there is no entry or either cell is off the grid.
            A wall that has no entry is a new [`Edge`][osrlib.crawl.dungeon.Edge] each time, with no
            door.
        """
        if not self.in_bounds(position) or not self.in_bounds(step(position, direction)):
            return Edge(kind=EdgeKind.WALL)
        return self.edges.get(edge_key(position, direction), Edge(kind=EdgeKind.WALL))

    def area_at(self, position: Position) -> AreaSpec | None:
        """Return the keyed area that covers a cell, or `None` for corridor.

        The session calls it on every step to find out whether the party has walked into an area.
        Call it to name the room the party is in, or to show which room a cell belongs to on a map.

        Args:
            position: The cell.

        Returns:
            The first area in `areas` whose `cells` include the position, or `None` when the cell is
            corridor. Two areas can list the same cell, and then the one earlier in `areas` is
            returned.

        Examples:
            ```python
            from osrlib.crawl.dungeon import AreaSpec, LevelSpec

            level = LevelSpec(
                number=1,
                width=3,
                height=1,
                entrance=(0, 0),
                areas=(AreaSpec(id="shrine", name="Shrine", cells=((2, 0),)),),
            )
            print(level.area_at((2, 0)).name, level.area_at((0, 0)))
            # Shrine None
            ```
        """
        for area in self.areas:
            if position in area.cells:
                return area
        return None

    def transition_at(self, position: Position) -> TransitionSpec | None:
        """Return the stairs, trapdoor, or chute on a cell, or `None`.

        The session refuses [`UseStairs`][osrlib.crawl.commands.UseStairs] on a cell where this
        returns `None`.
        That is what makes a chute one way: no transition stands on the arrival cell. Call it to draw
        stairs on a map, or to offer `UseStairs` only where it works. It never returns a passage,
        because the party walks through a passage with
        [`MoveParty`][osrlib.crawl.commands.MoveParty]. Call
        [`passage_at`][osrlib.crawl.dungeon.LevelSpec.passage_at] to find one.

        Args:
            position: The cell.

        Returns:
            The first transition in `transitions` on that cell that isn't a passage, or `None` when
            there isn't one.

        Examples:
            ```python
            from osrlib.crawl.dungeon import Direction, LevelSpec, TransitionSpec

            stairs = TransitionSpec(
                kind="stairs_down",
                position=(0, 0),
                to_dungeon_id="crypt",
                to_level_number=2,
                to_position=(0, 0),
                to_facing=Direction.SOUTH,
            )
            top = LevelSpec(number=1, width=1, height=1, entrance=(0, 0), transitions=(stairs,))
            found = top.transition_at((0, 0))
            print(found.kind, found.to_level_number)
            # stairs_down 2
            ```
        """
        for transition in self.transitions:
            if transition.kind != "passage" and transition.position == position:
                return transition
        return None

    def passage_at(self, position: Position, side: Direction) -> TransitionSpec | None:
        """Return the passage on one side of a cell, or `None`.

        A [`MoveParty`][osrlib.crawl.commands.MoveParty] across a passage's side moves the party to
        the passage's destination instead of stopping at the wall. Call it to find out whether a move
        from a cell crosses a passage, or to draw a passage on a map.

        Args:
            position: The cell.
            side: Which of the cell's four sides.

        Returns:
            The first passage in `transitions` on that cell and side, or `None` when there isn't
            one. It never returns stairs, a trapdoor, or a chute.
        """
        for transition in self.transitions:
            if transition.kind == "passage" and transition.position == position and transition.side == side:
                return transition
        return None


class DungeonSpec(BaseModel):
    """A dungeon: one or more levels joined by transitions.

    An [`Adventure`][osrlib.crawl.adventure.Adventure] contains its dungeons, and
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] takes the party into one by its id. Build the
    levels first, put them in a dungeon, then put the dungeon in an adventure with its town. An
    adventure can have several dungeons, and a
    [`TransitionSpec`][osrlib.crawl.dungeon.TransitionSpec] can lead to another dungeon's id, so a
    stair can lead out of one dungeon and into the next.

    A dungeon has no field for its way in. One of its levels needs an `entrance`, and
    [`validate_adventure`][osrlib.crawl.adventure.validate_adventure] checks that one does.

    Attributes:
        id: The dungeon's id.
        name: The dungeon's name.
        levels: Its levels, at least one.

    Raises:
        ValueError: If two levels have the same `number`.

    Examples:
        ```python
        from osrlib.crawl.dungeon import DungeonSpec, Edge, EdgeKind, LevelSpec

        corridor = LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)})
        crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(corridor,))
        print(crypt.level(1).width)
        # 2
        ```
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """The dungeon's id, unique within the adventure. You use it in
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon], the town's `travel_turns`, and transitions
    between dungeons, and every reference in the overlay includes it."""
    name: str = ""
    """The dungeon's name, to show the player, like `"The Old Crypt"`."""
    levels: tuple[LevelSpec, ...] = Field(min_length=1)
    """The dungeon's levels, at least one, each with a unique `number`. Look a level up by its number
    with [`level`][osrlib.crawl.dungeon.DungeonSpec.level], not by its position in the tuple. Only
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] depends on the order: it puts the party on the
    entrance of the first level in this tuple that has one, facing north."""

    @model_validator(mode="after")
    def _level_numbers_unique(self) -> DungeonSpec:
        numbers = [level.number for level in self.levels]
        if len(set(numbers)) != len(numbers):
            raise ValueError(f"dungeon {self.id!r} has duplicate level numbers")
        return self

    def level(self, number: int) -> LevelSpec:
        """Return the level with `number`.

        Use it to look up the level that a number from a command, an event, or a transition refers
        to.

        Args:
            number: The level's number.

        Returns:
            The level.

        Raises:
            ValueError: If no level has that number. The message includes the dungeon and the number.
        """
        for level in self.levels:
            if level.number == number:
                return level
        raise ValueError(f"dungeon {self.id!r} has no level {number}")


class PartyLocation(BaseModel):
    """Where the party is: in the base town, or on a dungeon cell facing a direction.

    The session keeps one in [`DungeonState.location`][osrlib.crawl.dungeon.DungeonState.location]
    and replaces it as the party moves. Read it to draw the map and to know whether the party is in
    town or in a dungeon. Don't change it yourself: to move the party by the referee's decision,
    execute [`PlaceParty`][osrlib.crawl.commands.PlaceParty], which takes one of these.

    A town location has none of the dungeon fields, and a dungeon location has all four.

    Attributes:
        kind: Whether the party is in town or in a dungeon.
        dungeon_id: The dungeon the party is in.
        level_number: The level the party is on.
        position: The cell the party stands on.
        facing: The direction the party faces.

    Raises:
        ValueError: If `kind` is `"dungeon"` and any dungeon field is missing, or if `kind` is
            `"town"` and any is set.

    Examples:
        ```python
        from osrlib.crawl.dungeon import Direction, PartyLocation

        here = PartyLocation(kind="dungeon", dungeon_id="crypt", level_number=1, position=(0, 0), facing=Direction.EAST)
        print(here.position, here.facing)
        # (0, 0) east
        ```
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["town", "dungeon"]
    """`"town"` when the party is in the base town, or `"dungeon"` when it stands on a dungeon cell.
    A new session starts in town."""
    dungeon_id: str | None = None
    """The id of the dungeon the party is in, and `None` in town."""
    level_number: int | None = None
    """The number of the level the party is on, and `None` in town."""
    position: Position | None = None
    """The cell the party stands on, and `None` in town."""
    facing: Direction | None = None
    """The direction the party faces, and `None` in town. Draw the party's view facing this way.
    [`MoveParty`][osrlib.crawl.commands.MoveParty] includes a direction, so the party can
    step any way, whichever way it faces, and the step turns it to face the way it moved."""

    @model_validator(mode="after")
    def _dungeon_fields_travel_together(self) -> PartyLocation:
        in_dungeon = self.kind == "dungeon"
        fields = (self.dungeon_id, self.level_number, self.position, self.facing)
        if in_dungeon and any(field is None for field in fields):
            raise ValueError("a dungeon location needs dungeon_id, level_number, position, and facing")
        if not in_dungeon and any(field is not None for field in fields):
            raise ValueError("a town location carries no dungeon fields")
        return self


class DoorState(BaseModel):
    """What has happened to one door in play: open, wedged, discovered, unlocked.

    This is the part of a door that changes. The [`DoorSpec`][osrlib.crawl.dungeon.DoorSpec] is how
    you wrote the door, and it never changes. A `DoorState` records what the party and the referee
    have done to it since. Get one from [`DungeonState.door`][osrlib.crawl.dungeon.DungeonState.door]
    with the door's [`edge_ref`][osrlib.crawl.dungeon.edge_ref].

    Attributes:
        open: Whether the door stands open.
        wedged: Whether a spike holds it.
        discovered: Whether the party has found a secret door.
        unlocked: Whether a locked door has been unlocked.
        opened_by_party: Whether the party opened it.
    """

    model_config = ConfigDict(validate_assignment=True)

    open: bool = False
    """Whether the door stands open now. The party walks through an open door without opening it. A
    door whose spec sets `starts_open` is open until something closes it."""
    wedged: bool = False
    """Whether an iron spike holds the door, from [`WedgeDoor`][osrlib.crawl.commands.WedgeDoor]. A
    wedged door doesn't swing shut behind the party, and the party can't close it."""
    discovered: bool = False
    """Whether the party has found a secret door. Until a [`Search`][osrlib.crawl.commands.Search]
    for secret doors sets it, a `secret` door is a wall to the party. It has no effect on a normal
    door."""
    unlocked: bool = False
    """Whether a locked door has been unlocked, by a thief's
    [`PickLock`][osrlib.crawl.commands.PickLock] or by a referee's
    [`SetDoorState`][osrlib.crawl.commands.SetDoorState]. Closing the door doesn't lock it again. Only a
    `SetDoorState` can."""
    opened_by_party: bool = False
    """Whether the party opened this door, with [`OpenDoor`][osrlib.crawl.commands.OpenDoor] or
    [`ForceDoor`][osrlib.crawl.commands.ForceDoor]. Only a door the party opened swings shut behind
    it, so a door that starts open, or that a referee opened, stays open."""

    # `DungeonState.door` sets this hook on a state it returns for a door with no entry. Setting a field
    # calls the hook, which stores the state in `DungeonState.doors`. Reading the state never adds an entry.
    _write_through: Callable[[DoorState, str, Any], None] | None = PrivateAttr(default=None)

    def __setattr__(self, name: str, value: Any) -> None:
        """Set an attribute. For a field, also call the hook that stores a state from `DungeonState.door`."""
        super().__setattr__(name, value)
        if name in type(self).model_fields and self._write_through is not None:
            self._write_through(self, name, value)

    # A copy or a pickle is a separate snapshot, so it drops the hook and never writes to `DungeonState.doors`.

    def __copy__(self) -> Self:
        """Return a shallow copy that doesn't write to `DungeonState.doors`."""
        copied = super().__copy__()
        copied._write_through = None
        return copied

    def __deepcopy__(self, memo: dict[int, Any] | None = None) -> Self:
        """Return a deep copy that doesn't write to `DungeonState.doors`."""
        copied = super().__deepcopy__(memo)
        copied._write_through = None
        return copied

    def __getstate__(self) -> dict[Any, Any]:
        """Return the state to pickle, without the hook that writes to `DungeonState.doors`."""
        state = super().__getstate__()
        state["__pydantic_private__"] = {**(state["__pydantic_private__"] or {}), "_write_through": None}
        return state


class DroppedItem(BaseModel):
    """One stack of an ordinary item lying on the floor.

    A [`DropPile`][osrlib.crawl.dungeon.DropPile] contains these. Identical items stack, so five iron
    spikes are one entry with a quantity of 5, not five entries.

    Attributes:
        item_id: Which item this is.
        quantity: How many are in the stack.
    """

    model_config = ConfigDict(validate_assignment=True)

    item_id: str
    """The item's template id, from the built-in equipment catalog or the adventure's `items`."""
    quantity: int = Field(ge=1)
    """How many are in the stack, at least 1. The session removes a stack from the pile when it runs
    out."""


class DropPile(BaseModel):
    """What lies on one cell's floor, waiting to be picked up.

    Loose goods end up in piles: gear the party drops with
    [`DropItems`][osrlib.crawl.commands.DropItems], what slain monsters leave at the end of an
    encounter, and the part of a cache the party couldn't carry. The party picks up a whole pile with
    [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure] and the feature id `"pile"`, which is why no
    feature you write can use that id. Goods the party drops to distract monsters that chase it are
    the exception: they are gone, not dropped in a pile.

    The session keeps piles in [`DungeonState.piles`][osrlib.crawl.dungeon.DungeonState.piles], keyed
    by [`cell_ref`][osrlib.crawl.dungeon.cell_ref]. A pile forms only on a cell where the party has
    stood, so the party knows about every pile.

    Attributes:
        items: The ordinary items in the pile.
        coins: The coins in the pile.
        valuables: The gems and jewellery in the pile.
        magic_items: The magic items in the pile.
    """

    model_config = ConfigDict(validate_assignment=True)

    items: list[DroppedItem] = []
    """The ordinary items lying here, one stack for each template id."""
    coins: Coins = Coins()
    """The coins lying here, by denomination."""
    valuables: list[ValuableInstance] = []
    """The gems and jewellery lying here, each with the id it had before it was dropped."""
    magic_items: list[MagicItemInstance] = []
    """The magic items lying here, each with the charges or quantity it had before it was dropped."""


class DungeonState(BaseModel):
    """Everything that play has changed in the dungeons: the overlay the session keeps beside the content.

    The adventure you wrote never changes. The running session records what happens to it here, and
    a save contains it. [`GameSession.new`][osrlib.crawl.session.GameSession.new] creates one, and the
    command handlers change it. Read it through
    [`GameSession.view`][osrlib.crawl.session.GameSession.view] instead of reading the session's
    copy, so each audience sees only what it is allowed to see. The referee view's `dungeon_state`
    is a copy of it.

    The overlay refers to content by strings, not by object, so it serializes as flat data and never
    repeats the adventure. It uses four formats of key:

    - `"{dungeon}:{level}"` keys `explored` and `seen`, like `"crypt:1"`.
    - [`edge_ref`][osrlib.crawl.dungeon.edge_ref] keys doors, like `"crypt:1:1,0:west"`.
    - `"{dungeon}:{level}:{id}"` is an *area reference* or a *feature reference*, to an area or a
      feature you wrote, like `"crypt:1:shrine"`.
    - [`cell_ref`][osrlib.crawl.dungeon.cell_ref] keys drop piles, like `"cell:crypt:1:2,3"`.

    `explored` and `seen` are both maps of cells, and they mean different things. `explored` contains
    the cells the party has walked into, and the session uses it for movement cost. `seen` contains the
    cells the party's light has shown it, and only the player view reads it, so a map can still show
    a room the party looked into and walked past.

    The attempt records are here because they are game state that a save has to include: who has
    listened at a door, who has searched a cell for a kind of thing, and which thief failed a lock,
    at what level.

    Attributes:
        location: Where the party is.
        explored: Cells the party has walked into.
        seen: Cells the party's light has shown it.
        doors: What has happened to each door.
        sprung_traps: Traps that have gone off.
        removed_traps: Treasure traps a thief has taken out.
        found_traps: Traps the party has found.
        found_tricks: Construction tricks the party has found.
        emptied_caches: Authored caches the party has emptied.
        piles: What is lying on the floor, by cell.
        generated_caches: Treasure the session rolled, by cache id.
        generated_treasure_areas: Areas whose treasure the session has already rolled.
        generated_lair_hoards: Areas whose keyed encounter's hoard the session has already rolled.
        resolved_encounters: Areas whose keyed encounter has been cleared.
        listen_attempts: Who has listened at each door.
        search_attempts: Who has searched each cell for what.
        inspect_attempts: Who has inspected each cache for traps.
        removal_attempts: Who has tried to remove each trap.
        lock_failures: Which thief failed each lock, and at what level.
    """

    model_config = ConfigDict(validate_assignment=True)

    location: PartyLocation = PartyLocation(kind="town")
    """Where the party is: in town, or on a dungeon cell. A new session starts in town."""
    explored: dict[str, list[Position]] = {}
    """The cells the party has walked into, as lists of positions keyed `"{dungeon}:{level}"`. The
    session uses it for movement cost: a step into an explored cell takes a third of the time of a
    step into a new one. The player view also shows only the drop piles on explored cells."""
    seen: dict[str, list[Position]] = {}
    """The cells the party's light has shown it, as lists of positions keyed `"{dungeon}:{level}"`.
    It is what the party remembers of the map. Only the player view reads it, and it never affects
    movement cost. [`mark_seen`][osrlib.crawl.dungeon.DungeonState.mark_seen] adds new cells in
    sorted `(x, y)` order, so the same game always saves the same bytes."""
    doors: dict[str, DoorState] = {}
    """Each door's [`DoorState`][osrlib.crawl.dungeon.DoorState], keyed by
    [`edge_ref`][osrlib.crawl.dungeon.edge_ref]. A door gets an entry the first time it changes in
    play, so an unchanged door has none. Read one through
    [`door`][osrlib.crawl.dungeon.DungeonState.door]."""
    sprung_traps: list[str] = []
    """The traps that have sprung, as area or feature references. A sprung trap never springs
    again."""
    removed_traps: list[str] = []
    """The treasure traps a thief has taken out with
    [`RemoveTreasureTrap`][osrlib.crawl.commands.RemoveTreasureTrap], as feature references. A removed
    trap never springs. Room traps never appear here, because there is no way to disarm one."""
    found_traps: list[str] = []
    """The traps the party has found, as area or feature references: room traps from a
    [`Search`][osrlib.crawl.commands.Search], and treasure traps from a thief's
    [`InspectTreasure`][osrlib.crawl.commands.InspectTreasure]. A room trap here no longer springs. A
    treasure trap here can still spring when the party opens the cache, and it has to be here before
    [`RemoveTreasureTrap`][osrlib.crawl.commands.RemoveTreasureTrap] can take it out."""
    found_tricks: list[str] = []
    """The construction tricks the party has found with a
    [`Search`][osrlib.crawl.commands.Search] for construction, as feature references."""
    emptied_caches: list[str] = []
    """The caches you wrote that the party has emptied, as feature references. The party gets nothing
    more from an emptied cache. A cache the session rolled isn't listed here. The session removes it
    from `generated_caches` instead."""
    piles: dict[str, DropPile] = {}
    """What lies on the floor, keyed by [`cell_ref`][osrlib.crawl.dungeon.cell_ref]. See
    [`DropPile`][osrlib.crawl.dungeon.DropPile]."""
    generated_caches: dict[str, GeneratedCache] = {}
    """Treasure the session rolled, keyed by cache id, like `"cache-0001"`. A
    [`HoardGeneratedEvent`][osrlib.crawl.events.HoardGeneratedEvent] reports the id,
    [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure] with that id takes the cache, and taking it
    removes the entry. See [`GeneratedCache`][osrlib.crawl.dungeon.GeneratedCache]."""
    generated_treasure_areas: list[str] = []
    """The areas whose [`AreaTreasureSpec`][osrlib.crawl.dungeon.AreaTreasureSpec] the session has
    already rolled, as area references, so the session doesn't roll a room's treasure again when the
    party enters it a second time."""
    generated_lair_hoards: list[str] = []
    """The areas whose [`KeyedEncounter`][osrlib.crawl.dungeon.KeyedEncounter] hoard the session has
    already rolled, as area references.

    The session rolls a lair's hoard the first time the party meets its monsters. If the party leaves
    and comes back, the monsters spawn again, but the session doesn't roll the hoard again. The party
    finds the cache from the first visit, or nothing if it took that cache."""
    resolved_encounters: list[str] = []
    """The areas whose keyed encounter ended with every monster slain or routed, as area references.
    When the party enters one of these areas again, no monsters spawn."""
    listen_attempts: dict[str, list[str]] = {}
    """Which characters have listened at each door, as lists of character ids keyed by
    [`edge_ref`][osrlib.crawl.dungeon.edge_ref]. Each character gets one try at each door, so a bad
    roll stands."""
    search_attempts: dict[str, list[str]] = {}
    """Which characters have searched each cell for each kind of thing, as lists of character ids
    keyed `"{cell_ref}:{kind}"`. The kind is the `kind` of the
    [`Search`][osrlib.crawl.commands.Search], like `"cell:crypt:1:2,3:secret_doors"`. Each character
    gets one try for each kind on each cell."""
    inspect_attempts: dict[str, list[str]] = {}
    """Which characters have inspected each cache for traps, as lists of character ids keyed by
    feature reference. Each character gets one try at each cache."""
    removal_attempts: dict[str, list[str]] = {}
    """Which characters have tried to remove each treasure trap, as lists of character ids keyed by
    feature reference. Each character gets one try at each trap, and a failed try springs it."""
    lock_failures: dict[str, dict[str, int]] = {}
    """Which thieves failed to pick each lock, keyed by [`edge_ref`][osrlib.crawl.dungeon.edge_ref].
    Each value maps a character id to the thief's level at the time. A thief who failed a lock can
    try it again after gaining a level, so the session records the level."""

    def is_explored(self, dungeon_id: str, level_number: int, position: Position) -> bool:
        """Return whether the party has walked into a cell.

        The session reads it for movement cost: a step into an explored cell takes a third of the
        time of a step into a new one. Call it to shade a map, or to work out what a move will cost.

        Args:
            dungeon_id: The dungeon's id.
            level_number: The level's number.
            position: The cell.

        Returns:
            True when the party has walked into the cell. False for a cell the party has only seen by
            its light.

        Examples:
            ```python
            from osrlib.crawl.dungeon import DungeonState

            state = DungeonState()
            state.mark_explored("crypt", 1, (0, 0))
            state.mark_seen("crypt", 1, [(1, 0)])
            print(state.is_explored("crypt", 1, (0, 0)), state.is_explored("crypt", 1, (1, 0)))
            # True False
            ```
        """
        return position in self.explored.get(f"{dungeon_id}:{level_number}", [])

    def mark_explored(self, dungeon_id: str, level_number: int, position: Position) -> None:
        """Mark a cell as walked into.

        The session calls it each time the party arrives on a cell, so you rarely call it yourself.
        A cell you mark on a running session isn't in the command log, so
        [`replay_game`][osrlib.persistence.replay_game] doesn't reproduce it. Marking a cell twice
        changes nothing.

        Args:
            dungeon_id: The dungeon's id.
            level_number: The level's number.
            position: The cell.
        """
        key = f"{dungeon_id}:{level_number}"
        cells = self.explored.get(key)
        if cells is None:
            self.explored[key] = [position]
        elif position not in cells:
            cells.append(position)

    def mark_seen(self, dungeon_id: str, level_number: int, positions: Iterable[Position]) -> None:
        """Mark cells as seen, meaning the party's light has shown them.

        The session calls it after every accepted command, so you rarely call it yourself. Only the
        player view reads seen cells, so its map can still show a room the party's light reached
        after the party has walked on. Seen cells don't affect movement cost, which comes from
        [`is_explored`][osrlib.crawl.dungeon.DungeonState.is_explored]. They don't make drop piles
        visible either: the player view shows piles only on cells the party has walked into, which is
        where piles form.

        Marking a cell twice changes nothing. This method adds new cells in sorted `(x, y)` order,
        whatever order you pass them in, so the same game always saves the same bytes.

        Args:
            dungeon_id: The dungeon's id.
            level_number: The level's number.
            positions: The cells to add. Cells already marked are skipped.
        """
        key = f"{dungeon_id}:{level_number}"
        fresh = sorted(set(positions) - set(self.seen.get(key, [])))
        if fresh:
            self.seen.setdefault(key, []).extend(fresh)

    def door(self, ref: str) -> DoorState:
        """Return one door's current state, which you can read or change.

        The session adds a door's entry to `doors` only when something in play changes the door. Use
        this method to get a door's state whether or not that has happened yet. Reading the state never
        changes `doors`.

        - When the door has an entry, the method returns that entry. Writing to it writes to `doors`.
        - When the door has no entry, the method returns a new all-`False` state and doesn't store it.
          The first time you set one of its fields, the state becomes the door's entry in `doors`. If
          the door has gained an entry in the meantime, the write goes to that entry instead.

        The method doesn't read the door's [`DoorSpec`][osrlib.crawl.dungeon.DoorSpec], so it
        ignores `starts_open`:

        - A door whose `starts_open` is set stands open in play. Until the door has an entry, the
          state this method returns still reads `open` as `False`.
        - The first time you set a field through this method, like `door(ref).discovered = True`,
          the method stores the entry with `open` still `False`.
        - When the session's commands change a door, they store an entry with `starts_open`
          applied.

        To show doors the way the party sees them, read the player view from
        [`GameSession.view`][osrlib.crawl.session.GameSession.view] instead.

        Args:
            ref: The door's [`edge_ref`][osrlib.crawl.dungeon.edge_ref]. You get the same reference
                from either side of a door, so either side reaches the same state.

        Returns:
            The door's entry in `doors`, or a new all-`False` state that becomes the entry the first
            time you set one of its fields.

        Examples:
            ```python
            from osrlib.crawl.dungeon import Direction, DungeonState, edge_ref

            state = DungeonState()
            # Both sides of one door give the same reference, and so the same state. The east
            # side of cell (0, 0) is the west side of cell (1, 0). edge_ref always names an edge
            # by its north or west side, so both calls return "crypt:1:1,0:west".
            east_side = edge_ref("crypt", 1, (0, 0), Direction.EAST)
            west_side = edge_ref("crypt", 1, (1, 0), Direction.WEST)
            state.door(east_side).open = True
            print(state.door(west_side).open, list(state.doors))
            # True ['crypt:1:1,0:west']
            ```
        """
        state = self.doors.get(ref)
        if state is not None:
            return state
        state = DoorState()
        state._write_through = lambda written, name, value: self._write_door(ref, written, name, value)
        return state

    def _write_door(self, ref: str, state: DoorState, name: str, value: Any) -> None:
        """Store a state from `door` on its first field write, or copy the write to an entry stored since."""
        stored = self.doors.get(ref)
        if stored is None:
            state._write_through = None
            self.doors[ref] = state
        elif stored is not state:
            setattr(stored, name, value)
