"""The dungeon: the grid you author, and the overlay play writes over it.

This module holds both halves of a dungeon. [`DungeonSpec`][osrlib.crawl.dungeon.DungeonSpec] and
everything under it (levels, edges, areas, features, traps, transitions) is authored content: frozen
models you build, hand to an [`Adventure`][osrlib.crawl.adventure.Adventure], and never change again.
[`DungeonState`][osrlib.crawl.dungeon.DungeonState] is the mutable overlay the running session writes
alongside it: which cells the party has walked, which doors stand open, which traps have gone off,
what has been dropped on the floor, and where the party is standing. The overlay is what a save file
carries, and it refers to the content by string references rather than by object, so it serializes
flat.

You build the geometry here and assemble it into an adventure in
[`osrlib.crawl.adventure`][osrlib.crawl.adventure]. You never construct `DungeonState` yourself:
[`GameSession.new`][osrlib.crawl.session.GameSession.new] makes one, and you read it through the
session's views. The long form, with a complete program you can run, is the guide
[Building an adventure](https://mmacy.github.io/osrlib-python/getting-started/building-an-adventure/).

The geometry, which every member here assumes: a level is a grid of 10-foot cells addressed `(x, y)`,
with `x` increasing east and `y` increasing south from `(0, 0)` in the northwest corner. Walls are
the default, and you declare the exceptions. An `edges` map holds one entry per physical edge that is
something other than wall, keyed by [`edge_key`][osrlib.crawl.dungeon.edge_key] so the boundary
between two cells has exactly one entry no matter which side you name it from. An edge with no entry
is wall, and so is the level boundary.

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
# EdgeKind.WALL
```
"""

from collections.abc import Iterable
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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

`x` increases east and `y` increases south from `(0, 0)` in the level's northwest corner, and one
cell is 10 feet on a side. Write one as a plain tuple, `(3, 0)`. A position is only meaningful
against a particular level, and one that names a cell off the grid is out of bounds rather than
invalid: [`LevelSpec.in_bounds`][osrlib.crawl.dungeon.LevelSpec.in_bounds] is how you ask, and
[`validate_adventure`][osrlib.crawl.adventure.validate_adventure] is what catches an authored one
that lands outside."""


class Direction(StrEnum):
    """The four grid directions the party faces and moves in.

    This is the direction vocabulary the whole crawl uses: which way
    [`MoveParty`][osrlib.crawl.commands.MoveParty] steps, which side of a cell
    [`OpenDoor`][osrlib.crawl.commands.OpenDoor] works on, which way a transition faces the party on
    arrival. There is no up or down here. Between levels is a
    [`TransitionSpec`][osrlib.crawl.dungeon.TransitionSpec].

    The wire values are lowercase and they serialize into commands, events, and saves, so changing
    one is a `schema_version` bump.
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
        """The `(dx, dy)` step for one cell in this direction.

        Add it to a position to get the neighbour, or call
        [`step`][osrlib.crawl.dungeon.step], which does the addition for you.
        """
        return _VECTORS[self]

    @property
    def opposite(self) -> Direction:
        """The reverse direction.

        The direction you came from is the opposite of the one you went. A transition's `to_facing`
        and a door seen from the far side are both read this way.
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

    This is grid arithmetic, and it takes no account of walls, levels, or the party. Ask
    [`LevelSpec.in_bounds`][osrlib.crawl.dungeon.LevelSpec.in_bounds] whether the answer is on the
    grid and [`LevelSpec.edge`][osrlib.crawl.dungeon.LevelSpec.edge] whether the party could get
    there. Use it while authoring to walk a corridor cell by cell, or in a front end to work out
    which cell a click landed on.

    Args:
        position: The starting cell.
        direction: The direction to step.

    Returns:
        The adjacent cell address, which may lie outside the level.

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
    """Return the canonical key for the edge on `direction`'s side of `position`.

    Use this whenever you write a [`LevelSpec.edges`][osrlib.crawl.dungeon.LevelSpec] map, so you
    don't have to work out which of the two neighbouring cells owns the boundary between them. Every
    physical edge has exactly one key: a cell plus `north` or `west`. A cell's south edge is its
    southern neighbour's north edge, and its east edge is its eastern neighbour's west edge, so
    `edge_key((0, 0), Direction.EAST)` and `edge_key((1, 0), Direction.WEST)` are the same string.

    The format is `"{x},{y}:{side}"`, which is what you see in a serialized adventure and what
    [`LevelSpec.edge`][osrlib.crawl.dungeon.LevelSpec.edge] looks up for you at read time.

    Args:
        position: The cell.
        direction: Which of the cell's four edges.

    Returns:
        The canonical edge key. It is not checked against any level, so a key for a cell off the grid
            comes back the same way.

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
    """Return the reference string that names one cell across a whole adventure.

    An `edges` key locates a cell inside one level. A cell reference locates it inside the game: it
    carries the dungeon and the level too, which is what the state overlay and the effects system
    need. Drop piles key on this in [`DungeonState.piles`][osrlib.crawl.dungeon.DungeonState], and an
    [`ActiveEffect.target_ref`][osrlib.core.effects.ActiveEffect] in this form anchors an effect to a
    dungeon cell rather than to a creature.

    The format is `"cell:{dungeon}:{level}:{x},{y}"`.

    Args:
        dungeon_id: The dungeon id, as it appears on its
            [`DungeonSpec`][osrlib.crawl.dungeon.DungeonSpec].
        level_number: The 1-based level number.
        position: The cell.

    Returns:
        The cell reference string.

    Examples:
        ```python
        from osrlib.crawl.dungeon import cell_ref

        print(cell_ref("crypt", 1, (2, 3)))
        # cell:crypt:1:2,3
        ```
    """
    return f"cell:{dungeon_id}:{level_number}:{position[0]},{position[1]}"


def edge_ref(dungeon_id: str, level_number: int, position: Position, direction: Direction) -> str:
    """Return the reference string that names one physical edge across a whole adventure.

    This is what [`DungeonState.doors`][osrlib.crawl.dungeon.DungeonState] keys on, so it is how you
    look a door's live open, wedged, discovered, and unlocked flags up from a cell and a direction.
    It canonicalizes the same way [`edge_key`][osrlib.crawl.dungeon.edge_key] does, so both sides of
    a door produce one reference and the two sides can never disagree about its state. Pass the
    result to [`DungeonState.door`][osrlib.crawl.dungeon.DungeonState.door].

    The format is `"{dungeon}:{level}:{x},{y}:{side}"`.

    Args:
        dungeon_id: The dungeon id.
        level_number: The 1-based level number.
        position: The cell.
        direction: Which of the cell's four edges.

    Returns:
        The edge reference string, canonicalized like [`edge_key`][osrlib.crawl.dungeon.edge_key].

    Examples:
        ```python
        from osrlib.crawl.dungeon import Direction, edge_ref

        print(edge_ref("crypt", 1, (0, 0), Direction.EAST))
        # crypt:1:1,0:west
        ```
    """
    return f"{dungeon_id}:{level_number}:{edge_key(position, direction)}"


class EdgeKind(StrEnum):
    """What occupies an edge between two cells.

    This is the `kind` of an [`Edge`][osrlib.crawl.dungeon.Edge] entry, and it is what makes the
    boundary between two cells passable or not.
    """

    OPEN = "open"
    """Nothing in the way: the party walks across."""
    WALL = "wall"
    """Solid: the party cannot cross. This is also what a level reports for an edge with no entry at
    all, so you only write it when you want the wall stated outright."""
    DOOR = "door"
    """A door stands here, and the entry contains the [`DoorSpec`][osrlib.crawl.dungeon.DoorSpec] that
    describes it. An edge of this kind must include one, and no other kind may."""


class DoorSpec(BaseModel):
    """A door on an edge, as you authored it.

    Put one on an [`Edge`][osrlib.crawl.dungeon.Edge] whose `kind` is `door`. Everything here is the
    door's starting condition, and play never writes back to it. What the party does to the door goes
    into [`DoorState`][osrlib.crawl.dungeon.DoorState] in the overlay instead, which is where you
    read whether a door is open right now.

    Attributes:
        kind: Whether the door is visible from the start or has to be found.
        stuck: Whether the door needs forcing before it opens.
        locked: Whether the door needs a key or a thief before it opens.
        starts_open: Whether the door stands open when the party first arrives.
        requires: An authored condition the party must satisfy to open the door.

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
    """`"normal"` for a door the party can see, `"secret"` for one it cannot. A secret door is
    invisible until a successful secret-door search finds it, which marks `discovered` on the door's
    overlay entry. Until then the party cannot open it, listen at it, or walk through it, and the
    edge reads to the player as wall."""
    stuck: bool = False
    """Whether the door is stuck shut. The engine refuses
    [`OpenDoor`][osrlib.crawl.commands.OpenDoor] on a stuck door, so the party has to force it with
    [`ForceDoor`][osrlib.crawl.commands.ForceDoor], a strength check that costs a turn whether or not
    it works."""
    locked: bool = False
    """Whether the door is locked. The engine refuses to open a locked door until something unlocks it:
    [`PickLock`][osrlib.crawl.commands.PickLock] by a thief, or a referee
    [`SetDoorState`][osrlib.crawl.commands.SetDoorState]. Forcing still works, since a locked door can
    be broken open."""
    starts_open: bool = False
    """Whether the door already stands open when the party first reaches it. An authored-open door
    stays open: the rule that a door swings shut behind the party applies only to doors the party
    itself opened."""
    requires: GateSpec | None = None
    """An authored gate on opening the door, or `None` for a door anyone may open. A gate
    ([`GateSpec`][osrlib.crawl.gates.GateSpec]) is a stateless condition the engine checks whenever
    the party tries to open or force the door, after every ordinary refusal has passed. It is
    independent of `locked`: a door with both needs both, [`PickLock`][osrlib.crawl.commands.PickLock]
    addresses only the lock, and [`SetDoorState`][osrlib.crawl.commands.SetDoorState] writes the
    overlay without consulting the gate at all. A door standing open lets the party through
    unchecked, because the gate guards the opening rather than the doorway, and applies again once
    the door closes."""


class Edge(BaseModel):
    """One entry in a level's `edges` map: what stands on the boundary between two cells.

    You write these into [`LevelSpec.edges`][osrlib.crawl.dungeon.LevelSpec] keyed by
    [`edge_key`][osrlib.crawl.dungeon.edge_key]. Only the exceptions need entries, because an edge
    with no entry is wall. Read one back with
    [`LevelSpec.edge`][osrlib.crawl.dungeon.LevelSpec.edge], which supplies a wall for anything
    absent and for the level boundary.

    Attributes:
        kind: What occupies the edge.
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
    """Whether the boundary is open, wall, or a door."""
    door: DoorSpec | None = None
    """The door standing on this edge, and `None` on every other kind. The two fields travel
    together: a `door` edge must carry one and no other kind may."""

    @model_validator(mode="after")
    def _door_exactly_on_door_edges(self) -> Edge:
        if (self.kind is EdgeKind.DOOR) != (self.door is not None):
            raise ValueError("an edge carries a door spec exactly when its kind is 'door'")
        return self


class TransitionSpec(BaseModel):
    """A way between levels standing on one cell: stairs, a trapdoor, or a chute.

    Transitions live on the level rather than on an area, in
    [`LevelSpec.transitions`][osrlib.crawl.dungeon.LevelSpec]. They are how a multi-level dungeon
    joins up, and how two dungeons join if you point one at the other's id. The party takes one with
    [`UseStairs`][osrlib.crawl.commands.UseStairs], which lands it at `to_position` facing
    `to_facing`. [`validate_adventure`][osrlib.crawl.adventure.validate_adventure] checks that the
    cell you leave from and the cell you arrive at are both on their grids.

    Nothing pairs transitions up for you. A staircase the party can walk back up is two transitions,
    one on each level, pointing at each other. Leave the return one out and you have a chute: a
    one-way drop, which `UseStairs` refuses to climb back because the arrival cell holds no
    transition.

    Attributes:
        kind: Which kind of connection this is.
        position: The cell it stands on.
        to_dungeon_id: The dungeon the party arrives in.
        to_level_number: The level the party arrives on.
        to_position: The cell the party arrives at.
        to_facing: The direction the party faces on arrival.
        requires: An authored condition the party must satisfy to take it.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["stairs_up", "stairs_down", "trapdoor", "chute"]
    """What the party sees and uses: `"stairs_up"`, `"stairs_down"`, `"trapdoor"`, or `"chute"`. The
    value is descriptive. The destination fields set where the party goes, and the
    presence or absence of a return transition decides whether it can come back."""
    position: Position
    """The cell on this level where the transition stands. The party has to be standing here for
    [`UseStairs`][osrlib.crawl.commands.UseStairs] to do anything."""
    to_dungeon_id: str
    """The id of the dungeon the party arrives in. Naming this level's own dungeon is the ordinary
    case. Naming another dungeon of the same adventure joins the two."""
    to_level_number: int = Field(ge=1)
    """The 1-based number of the level the party arrives on."""
    to_position: Position
    """The cell the party arrives at, on the destination level's grid."""
    to_facing: Direction
    """The direction the party faces on arrival, so a front end knows which way the view points and
    the party's first step is not a surprise."""
    requires: GateSpec | None = None
    """An authored gate on taking the transition, or `None` for one anyone may take.
    [`UseStairs`][osrlib.crawl.commands.UseStairs] evaluates it after the there-is-no-transition-here
    refusal and before the party moves, so a gate that costs the party something is paid at the
    threshold. The gate's `success` narration is attached to the arrival's
    [`LocationEnteredEvent`][osrlib.crawl.events.LocationEnteredEvent], so a transition whose
    destination is its own level crosses no boundary, emits no such event, and has nowhere to show
    one. A transition inside a [`TrapEffect`][osrlib.crawl.dungeon.TrapEffect] may carry no gate at
    all: that is a forced relocation rather than an attempt, and the trap effect rejects one that
    does."""


class TrapEffect(BaseModel):
    """What a sprung trap does to its victim.

    You attach one to a [`TrapSpec`][osrlib.crawl.dungeon.TrapSpec], which is what says when it
    springs. The fields compose: a dart trap rolls damage, a pit trap rolls falling damage and may add
    a condition, a gas trap calls for a save and kills on a failure. Leave everything unset and put
    your description in `manual` for a trap your front end narrates and resolves itself.

    A passed save always spares the victim from `kills` and from `condition`, whatever `on_save` says,
    because half a death and half a blindness are not things B/X expresses. `on_save` scales damage
    only.

    Attributes:
        damage_dice: The damage the trap deals.
        volley_dice: The number of projectiles, for a trap that fires several.
        save: The saving throw the victim gets.
        kills: Whether a failed save kills outright.
        condition: A condition a failed save inflicts.
        condition_duration_dice: The condition's duration, rolled.
        condition_duration_amount: The condition's duration, fixed.
        condition_duration_unit: The unit the duration counts in.
        fall_feet: How far the victim falls.
        transition: Where the trap drops the victim.
        manual: Prose for a trap the rules do not resolve.

    Raises:
        ValueError: If a duration is given with no `condition`, if `volley_dice` is given with no
            `damage_dice`, or if `transition` carries a gate.

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
    none. With `volley_dice` set this is the damage of one projectile rather than the whole trap. The
    expression is parsed when the model is built, so a malformed one fails here rather than when the
    trap springs."""
    volley_dice: str | None = None
    """How many projectiles the trap fires, as a dice expression, or `None` for a trap that fires
    one thing or none. This is the darts form: `volley_dice="1d6"` with `damage_dice="1d4"` fires
    1d6 darts and rolls 1d4 for each. The dice grammar cannot say "this many times that much" on its
    own, which is why it is two fields. It requires `damage_dice`."""
    save: SaveSpec | None = None
    """The saving throw the victim rolls, or `None` for a trap that allows none. Its `on_save` says
    what a successful save does, and `negates` spares the victim outright. A passed save always
    spares them from `kills` and `condition` whatever `on_save` says. `half` halves damage, both the
    `damage_dice` roll and `fall_feet` damage."""
    kills: bool = False
    """Whether a failed save kills the victim outright. This is the save-or-die form, poison gas
    being the printed example. A passed save always spares them, whatever `on_save` says."""
    condition: Condition | None = None
    """A condition the trap inflicts on a failed save, or `None`. Blindness is the printed example.
    A passed save always spares the victim from it."""
    condition_duration_dice: str | None = None
    """The condition's duration rolled as a dice expression, for a duration that varies. Set this or
    `condition_duration_amount`, not both, and only alongside a `condition`. The roll draws on the
    effects stream, like every other effect attachment."""
    condition_duration_amount: int | None = None
    """The condition's duration as a fixed number, for a duration that does not vary. Set this or
    `condition_duration_dice`, and only alongside a `condition`."""
    condition_duration_unit: TimeUnit | None = None
    """What the duration counts in: rounds, turns, or days. Only meaningful alongside a `condition`.
    A condition with no duration at all lasts until something removes it."""
    fall_feet: int | None = None
    """How far the victim falls, in feet, or `None` for a trap with no drop. This is the pit form.
    Falling damage is the SRD's own by distance, separate from `damage_dice`, and a `half` save
    halves it too."""
    transition: TransitionSpec | None = None
    """Where the trap puts the victim, or `None` for a trap that moves nobody. This is the chute
    form: the victim slides somewhere else instead of staying where they stood. The transition may
    carry no `requires` gate, because the victim is not attempting anything."""
    manual: str | None = None
    """Prose for a trap the rules do not resolve, or `None`. Nothing here reads it: it is for the
    referee or the front end, and it is how you author a trap whose effect is a judgement call rather
    than a die roll."""

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
            # The chute drops the victim; the party never attempts it, so there is
            # no attempt for a gate to guard.
            raise ValueError("a trap effect's transition never gates: it is a forced relocation, not an attempt")
        return self


class TrapSpec(BaseModel):
    """A trap: when it springs, what it does, and whom it catches.

    There are two places a trap can sit, and the `kind` says which. A room trap goes on an
    [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] and covers the whole area. A treasure trap goes on a
    [`FeatureSpec`][osrlib.crawl.dungeon.FeatureSpec] and guards that one cache. Each model accepts
    only its own kind, so a trap cannot end up somewhere it has no meaning.

    A trap does not spring on sight. The engine rolls a 2-in-6 chance each time the triggering action
    happens, which is the SRD's rule, so walking into a trapped room is not certain death. A trap the
    party has already found stops rolling: a room trap by a successful
    [`Search`][osrlib.crawl.commands.Search], a treasure trap by a thief's
    [`InspectTreasure`][osrlib.crawl.commands.InspectTreasure]. Only a treasure trap can then be taken
    out of play, with [`RemoveTreasureTrap`][osrlib.crawl.commands.RemoveTreasureTrap]. A room trap the
    party knows about is avoided rather than disarmed.

    Traps you author are the only traps in the game. Treasure the engine generates is never trapped.

    Attributes:
        kind: Whether this is a room trap or a treasure trap.
        trigger: The action that springs it.
        effect: What it does when it springs.
        affects: Whom it catches.

    Raises:
        ValueError: If `kind` is `"treasure"` and `trigger` is not `"open"`.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["room", "treasure"]
    """`"room"` for a trap over an area, `"treasure"` for one on a cache.
    [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] accepts only room traps and
    [`FeatureSpec`][osrlib.crawl.dungeon.FeatureSpec] only treasure traps."""
    trigger: Literal["enter", "open"]
    """The action that springs the trap. `"enter"` is the party stepping into a cell of the trapped
    area. `"open"` is a door being opened: for a room trap, any door of the area, from either side,
    which is the blade that drops when the door swings. For a treasure trap, `"open"` is the cache
    itself being opened. A treasure trap must use `"open"`, because a cache has nothing to walk into."""
    effect: TrapEffect
    """What the trap does once it springs. See [`TrapEffect`][osrlib.crawl.dungeon.TrapEffect]."""
    affects: Literal["triggerer", "party"] = "triggerer"
    """Whom the effect lands on: `"triggerer"` for the one character who set it off, the default, or
    `"party"` for every living member, which is the form for poison gas filling the room."""

    @model_validator(mode="after")
    def _treasure_traps_spring_on_open(self) -> TrapSpec:
        if self.kind == "treasure" and self.trigger != "open":
            raise ValueError("a treasure trap springs on open; a cache has nothing to enter")
        return self


class ValuableSpec(BaseModel):
    """A named gem or piece of jewellery you placed by hand in a cache.

    Use this when the treasure is a particular thing with a name, rather than one of the anonymous
    gems the treasure generators roll. It goes in a
    [`FeatureSpec`][osrlib.crawl.dungeon.FeatureSpec]'s `valuables`. It stays a description until the
    party empties the cache, at which point the session turns it into a
    [`ValuableInstance`][osrlib.core.items.ValuableInstance] with an id of its own.

    Attributes:
        kind: Whether it is a gem or jewellery.
        name: What the party sees it called.
        value_gp: What it sells for, in gold pieces.
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
    """Which sort of valuable this is. Nothing mechanical turns on it. It is what the item is, for
    display and for any rule your game applies to one sort and not the other."""
    name: str = ""
    """The display name the party sees, like `"The abbot's seal ring"`. Empty leaves the valuable
    unnamed, which is how the generated ones arrive."""
    value_gp: int = Field(ge=0)
    """What the valuable is worth in gold pieces. This is the sale price in town and the XP the party
    earns for bringing it back."""
    weight_coins: int = Field(default=0, ge=0)
    """What the valuable weighs, in coins, for encumbrance. The default of 0 makes it weightless,
    which is the usual treatment for a gem."""


class AreaTreasureSpec(BaseModel):
    """Treasure the engine rolls for an area that has no monsters guarding it.

    Put one on an [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] when you want the room to hold loot but
    do not want to choose it. It rolls the first time the party enters the area and lands as a cache
    on the floor, which the party then picks up with
    [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure]. Generated treasure is never trapped. Trapping
    is authored, through a [`FeatureSpec`][osrlib.crawl.dungeon.FeatureSpec] with a trap on it.

    For an area whose treasure is a monster's hoard, use a
    [`KeyedEncounter`][osrlib.crawl.dungeon.KeyedEncounter] and its `hoard` flag instead: that is the
    lair treasure, and it comes with the monsters.

    Attributes:
        letters: The treasure type letters to roll.
        unguarded: Whether to roll the level's unguarded-treasure band instead.

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
    """One or more B/X treasure type letters, like `("C",)`. Each letter is its own hoard table, and the
    full list is [the treasure type index][treasure-types-index]. Set this or `unguarded`, not
    both."""
    unguarded: bool = False
    """Whether to roll the dungeon level's unguarded-treasure band instead of naming letters. That
    band is the SRD's own table for treasure lying about with nothing watching it, and it scales with
    the level number. Set this or `letters`, not both."""

    @model_validator(mode="after")
    def _letters_or_unguarded(self) -> AreaTreasureSpec:
        if bool(self.letters) == self.unguarded:
            raise ValueError("an area treasure spec names letters or sets unguarded, not both or neither")
        return self


class TreasureBundle(BaseModel):
    """A working pile of rolled treasure: coins, valuables, and magic items together.

    The treasure generators fill one of these while a hoard rolls, and the engine then moves its
    contents into a [`GeneratedCache`][osrlib.crawl.dungeon.GeneratedCache] or a
    [`DropPile`][osrlib.crawl.dungeon.DropPile]. You meet it if you drive the generators yourself
    outside a session. Inside one, the cache and the pile are what you read.

    Unlike the authored models here it is mutable, because rolling a hoard adds to it entry by entry.

    Attributes:
        coins: The coins in the bundle.
        valuables: The gems and jewellery in the bundle.
        magic_items: The magic items in the bundle.
    """

    model_config = ConfigDict(validate_assignment=True)

    coins: Coins = Coins()
    """The coins, by denomination. A fresh bundle starts with none of each."""
    valuables: list[ValuableInstance] = []
    """The gems and jewellery, each already an instance with its own id and value."""
    magic_items: list[MagicItemInstance] = []
    """The magic items, each already rolled out with its charges or quantity."""

    @property
    def empty(self) -> bool:
        """Whether the bundle holds nothing at all.

        A hoard can roll to nothing, and the engine checks this before it writes a cache, so an
        empty one never lands on the floor.
        """
        return self.coins.total_coins == 0 and not self.valuables and not self.magic_items


class GeneratedCache(BaseModel):
    """Treasure the engine rolled and put on the floor, in the state overlay.

    Authored caches are [`FeatureSpec`][osrlib.crawl.dungeon.FeatureSpec]s and never change. This is
    the other kind: what a monster's lair hoard or an
    [`AreaTreasureSpec`][osrlib.crawl.dungeon.AreaTreasureSpec] produced when it rolled. The session
    puts one in [`DungeonState.generated_caches`][osrlib.crawl.dungeon.DungeonState] under a minted
    id like `"cache-0001"`, announces it with a `HoardGeneratedEvent`, and removes it when the party
    empties it with [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure] naming that id.

    Generated hoards are never trapped. Trapping treasure is something you author, not something a
    roll produces, which is one of the choices listed in
    [the adaptations register](https://mmacy.github.io/osrlib-python/adaptations/), the page recording
    where osrlib commits to one reading of an ambiguous rule or supplies a default behind a
    [`Ruleset`][osrlib.core.ruleset.Ruleset] flag.

    Attributes:
        cell_ref: The cell the cache lies on.
        treasure_types: The treasure type letters it rolled from.
        coins: The coins in it.
        valuables: The gems and jewellery in it.
        magic_items: The magic items in it.
    """

    model_config = ConfigDict(validate_assignment=True)

    cell_ref: str
    """Where the cache lies, as a [`cell_ref`][osrlib.crawl.dungeon.cell_ref] string. The party has
    to be standing on that cell to take it."""
    treasure_types: tuple[str, ...] = ()
    """The treasure type letters the hoard rolled from, kept for display and for a referee who wants
    to see what the dice were asked. Empty for an unguarded roll, which names no letters."""
    coins: Coins = Coins()
    """The coins in the cache, by denomination."""
    valuables: list[ValuableInstance] = []
    """The gems and jewellery in the cache, each with its own id."""
    magic_items: list[MagicItemInstance] = []
    """The magic items in the cache, each already rolled out with its charges or quantity."""


class FeatureSpec(BaseModel):
    """A keyed thing in a room: a treasure cache, a construction trick, or your own content.

    Features hang on an [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] or straight on a
    [`LevelSpec`][osrlib.crawl.dungeon.LevelSpec], and they are how a room holds something the party
    can find and interact with. Stairs are not features. Those are
    [`TransitionSpec`][osrlib.crawl.dungeon.TransitionSpec]s, and they have no second home.

    A `treasure_cache` is the one kind the engine resolves on its own: the party opens it with
    [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure] naming the feature's id, and its contents go
    into the party's hands. Hand-placed magic items are named here and instantiated when the cache is
    emptied, so an item's own details (charges, quantities, whether a sword turns out to be sentient)
    roll then, on the treasure stream, through
    [`instantiate_magic_item`][osrlib.core.treasure.instantiate_magic_item]. A `construction_trick`
    is one of the SRD's weird architectural features, like a room that rotates or an illusory
    passage: the party finds it by searching, and your front end says what it does. A `custom`
    feature is yours entirely.

    Attributes:
        id: The feature's id, unique across its level.
        kind: Which sort of feature it is.
        description: Prose for your front end.
        cell: The cell it sits on, or `None` to bind it to a whole area.
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
    """The feature's id, which has to be unique across the level, counting features on the level and
    on all of its areas together. [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure] and the other
    feature commands name it. The id `"pile"` is reserved for the drop pile on a cell, so authored
    content may not use it, and `validate_adventure` refuses an adventure that does."""
    kind: Literal["treasure_cache", "construction_trick", "custom"]
    """What sort of feature this is. `"treasure_cache"` is the only kind the engine resolves for the
    party. `"construction_trick"` and `"custom"` contain content your front end interprets."""
    description: str = ""
    """Prose your front end shows when the party finds the feature. Events carry the feature's id
    rather than its words, so the text lives here and the front end looks it up."""
    cell: Position | None = None
    """The cell the feature sits on. A feature listed on a level needs one. A feature listed on an
    area may leave it `None`, which binds the feature to the whole area rather than to one square of
    it."""
    item_ids: tuple[str, ...] = ()
    """Ordinary items in the cache, by template id. Any id the session's effective equipment catalog
    holds works: a shipped id from [`load_equipment`][osrlib.data.load_equipment], listed in
    [the equipment id index][equipment-index], or one the adventure bundles on its `items` field."""
    magic_item_ids: tuple[str, ...] = ()
    """Magic items in the cache, by template id. Any id from
    [`load_magic_items`][osrlib.data.load_magic_items], listed in
    [the magic item id index][magic-items-index]. Adventures bundle no magic items of their own, so
    only the shipped catalog resolves here."""
    coins: Coins = Coins()
    """Coins in the cache, by denomination."""
    valuables: tuple[ValuableSpec, ...] = ()
    """Named gems and jewellery in the cache. See
    [`ValuableSpec`][osrlib.crawl.dungeon.ValuableSpec]."""
    trap: TrapSpec | None = None
    """A trap guarding the cache, or `None`. It has to be a treasure trap, which springs when the
    party opens the cache. A thief finds it with
    [`InspectTreasure`][osrlib.crawl.commands.InspectTreasure] and takes it out with
    [`RemoveTreasureTrap`][osrlib.crawl.commands.RemoveTreasureTrap], one attempt each per
    character."""

    @model_validator(mode="after")
    def _trap_kind_matches(self) -> FeatureSpec:
        if self.trap is not None and self.trap.kind != "treasure":
            raise ValueError(f"feature {self.id!r} carries a non-treasure trap")
        return self


class KeyedMonster(BaseModel):
    """One line of a keyed encounter: which monster, and how many.

    A [`KeyedEncounter`][osrlib.crawl.dungeon.KeyedEncounter] is a tuple of these, so a room holding
    four orcs and their ogre bodyguard is two lines. Give each line a fixed count or count dice,
    exactly one of the two.

    Attributes:
        template_id: Which monster stands here.
        count_dice: How many, rolled when they spawn.
        count_fixed: How many, decided now.

    Raises:
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
    """The monster's template id. Any id the session's effective catalog holds works: a shipped id
    from [`load_monsters`][osrlib.data.load_monsters], listed in
    [the monster id index][monsters-index], or one the adventure bundles on its `monsters` field."""
    count_dice: str | None = None
    """How many appear, as a dice expression like `"2d4"`, rolled on the
    [`WANDERING_STREAM`][osrlib.crawl.session.WANDERING_STREAM] the first time the party enters the
    area, and held at 1 or more. Set this or `count_fixed`, not both. The expression is parsed when
    the model is built, so a malformed one fails while you author rather than at play."""
    count_fixed: int | None = None
    """How many appear, as a number decided now. Set this or `count_dice`, not both. A printed module
    gives concrete numbers, and so does [`stock_area`][osrlib.crawl.stocking.stock_area] when it
    rolls a room for you."""

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
    """The monsters waiting in a keyed area, and what is already decided about them.

    Put one on an [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec]. The monsters spawn the first time the
    party enters any cell of the area, and the session moves into encounter mode: surprise, distance,
    and reaction roll unless you have decided them here. The encounter resolves once, and the area
    stays clear afterwards.

    Attributes:
        monsters: The monster lines, each a template and a count.
        alignment: A fixed alignment for templates that offer a choice.
        aware: Whether the monsters are expecting the party.
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
    """The monster lines making up the encounter, at least one. See
    [`KeyedMonster`][osrlib.crawl.dungeon.KeyedMonster]."""
    alignment: Alignment | None = None
    """The alignment the spawned monsters take, for a template whose own alignment offers more than
    one. `None` rolls it the ordinary way. The value has to be one the template allows, and
    [`validate_adventure`][osrlib.crawl.adventure.validate_adventure] refuses one that is not."""
    aware: bool = False
    """Whether the monsters already know the party is coming. Aware monsters never roll surprise,
    which is how you author a lookout or an ambush that has heard the party's armour."""
    stance: ReactionResult | None = None
    """The reaction the monsters take, instead of rolling for it. Set it when the room's monsters
    attack on sight or are friendly by design. Leave it `None` and the reaction roll decides."""
    hoard: bool = True
    """Whether the monsters have their lair treasure with them. The default generates their printed
    hoard the first time the encounter spawns. Set it `False` for a monster room with no treasure,
    which B/X stocking produces often: the room-contents roll puts treasure in only some monster
    rooms, while a monster's printed lair letters would otherwise always come along."""


class AreaSpec(BaseModel):
    """A keyed room or cave: a named region of cells with content bound to it.

    Areas are how you key a dungeon. Each one covers some cells of a
    [`LevelSpec`][osrlib.crawl.dungeon.LevelSpec], and cells no area covers are corridor. Entering
    any cell of an area is what brings its content into play: the encounter spawns, the trap gets its
    spring roll, the treasure rolls, and your front end shows the description.

    Attributes:
        id: The area's id, unique across its level.
        name: The room's name.
        description: Prose for your front end.
        cells: The cells the area covers.
        encounter: The monsters waiting here.
        features: The keyed things in the room.
        trap: A room trap over the whole area.
        treasure: Generated treasure with nothing guarding it.

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
    """The area's id, which has to be unique across the level. Events carry it, triggers match on it,
    and the state overlay records the area's encounter and treasure against it."""
    name: str = ""
    """The room's name, for your front end to show: `"Guard post"`, `"The abbot's cell"`."""
    description: str = ""
    """Prose your front end shows when the party walks in. Events carry the area's id rather than its
    words, so the text lives here and the front end looks it up. For prose split by audience, see
    [`NarrativeBlock`][osrlib.crawl.narrative.NarrativeBlock]."""
    cells: tuple[Position, ...] = Field(min_length=1)
    """The cells the area covers, at least one. They need not be contiguous, though a room usually
    is. Every one has to be on the level's grid.
    [`AreaSpec.cells[0]`][osrlib.crawl.dungeon.AreaSpec] is where a generated hoard lands."""
    encounter: KeyedEncounter | None = None
    """The monsters waiting in the room, or `None` for an empty one. See
    [`KeyedEncounter`][osrlib.crawl.dungeon.KeyedEncounter]."""
    features: tuple[FeatureSpec, ...] = ()
    """The keyed things in the room: caches, tricks, and your own content. A feature here may leave
    its `cell` unset, which binds it to the area rather than to one square."""
    trap: TrapSpec | None = None
    """A trap over the whole area, or `None`. It has to be a room trap, which springs when the party
    enters a cell of the area or, with `trigger="open"`, when a door of the area is opened."""
    treasure: AreaTreasureSpec | None = None
    """Treasure the engine rolls on first entry, for a room with loot and nothing guarding it. A
    room whose treasure belongs to its monsters uses the encounter's `hoard` flag instead."""

    @model_validator(mode="after")
    def _trap_kind_matches(self) -> AreaSpec:
        if self.trap is not None and self.trap.kind != "room":
            raise ValueError(f"area {self.id!r} carries a non-room trap")
        return self


class WanderingSpec(BaseModel):
    """A level's wandering-monster check: how often it rolls, and from what.

    Every [`LevelSpec`][osrlib.crawl.dungeon.LevelSpec] has one, and the default is the B/X rule, so
    you only write your own to change the odds or the monsters. The session runs the check on its own
    clock as the party spends turns, and you never roll it yourself.

    Attributes:
        chance_in_six: The odds of monsters showing up.
        interval_turns: How often the check runs.
        table: A custom monster table for this level.

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
    """How many faces of a d6 bring monsters, checked once per interval. The default of 1 is the
    printed rule. Set it 0 for a level nothing wanders on, and higher for one that is busier."""
    interval_turns: int = Field(default=2, ge=1)
    """How many exploration turns pass between checks. The default of 2 is the printed rule."""
    table: EncounterTable | None = None
    """A custom encounter table for this level, or `None` to use the compiled table for the level's
    number band. Setting it replaces the band table entirely, which is how you give a level its own
    inhabitants. Same row model as the shipped tables, from
    [`load_encounter_tables`][osrlib.data.load_encounter_tables]."""


class LevelSpec(BaseModel):
    """One dungeon level: a grid of 10-foot cells with its edges, rooms, and stairs.

    A level is where all of this module's geometry comes together. You give it a size, declare the
    edges that are not wall, key some of its cells as areas, and hang features and transitions on it.
    Then you put one or more levels in a [`DungeonSpec`][osrlib.crawl.dungeon.DungeonSpec] and that
    dungeon in an [`Adventure`][osrlib.crawl.adventure.Adventure].

    The methods read the level back the way the engine does: is this cell on the grid, what stands on
    this side of it, which room is it part of, do stairs go from it. A front end drawing a map calls
    them, and so does a tool checking your work while you author.

    Attributes:
        number: The level's depth number.
        width: The grid's width in cells.
        height: The grid's height in cells.
        edges: Everything that is not wall.
        areas: The keyed rooms.
        features: Features on the level rather than on a room.
        transitions: The ways to other levels.
        wandering: The wandering-monster check.
        entrance: Where the party arrives from town.
        guidance: Ambient steering for a narrating front end.

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
        # EdgeKind.OPEN

        print(corridor.edge((0, 0), Direction.NORTH).kind)
        # EdgeKind.WALL
        ```
    """

    model_config = ConfigDict(frozen=True)

    number: int = Field(ge=1)
    """The level's depth, 1-based and visible to the rules: it selects the wandering-monster table
    band and the treasure bands. Level 1 is the top. The numbers have to be unique within a dungeon,
    and a [`TransitionSpec`][osrlib.crawl.dungeon.TransitionSpec] names one to say where it goes."""
    width: int = Field(ge=1)
    """How many cells the grid runs east to west. Valid `x` values are `0` to `width - 1`."""
    height: int = Field(ge=1)
    """How many cells the grid runs north to south. Valid `y` values are `0` to `height - 1`."""
    edges: dict[str, Edge] = {}
    """Everything on the grid that is not wall, keyed by
    [`edge_key`][osrlib.crawl.dungeon.edge_key]. An edge with no entry here is wall, and so is the
    level boundary, so an empty map is a level of solid rock. Read it back through
    [`edge`][osrlib.crawl.dungeon.LevelSpec.edge] rather than by hand, which supplies the wall for
    you."""
    areas: tuple[AreaSpec, ...] = ()
    """The level's keyed rooms and caves. Cells no area covers are corridor. Ids have to be unique
    within the level, and every cell an area names has to be on the grid."""
    features: tuple[FeatureSpec, ...] = ()
    """Features that belong to the level rather than to a room: a cache in a corridor, a trick in a
    dead end. Each one needs a `cell`, since there is no area to bind it to. Ids share one namespace
    with the areas' features."""
    transitions: tuple[TransitionSpec, ...] = ()
    """The stairs, trapdoors, and chutes on this level, each standing on one cell. Transitions belong
    to the level, not to an area, even when they stand inside a room."""
    wandering: WanderingSpec = WanderingSpec()
    """The level's wandering-monster check. The default is the printed rule, a 1-in-6 check every two
    turns off the compiled table for this level's number."""
    entrance: Position | None = None
    """The cell the party arrives at from town, or `None` for a level with no way in from outside.
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] and
    [`TravelToTown`][osrlib.crawl.commands.TravelToTown] both use it. Some level of every dungeon
    needs one, and `validate_adventure` refuses a dungeon where no level has any."""
    guidance: str = ""
    """Ambient steering for a narrating front end while the party is on this level: the tone of the
    place, what you want said about it, what you never want said.

    The engine reads it nowhere, no event contains it, and no rule turns on it. It is authored data
    for a narrator, which reaches it through the adventure document. That document is referee-side,
    since the player view contains no level internals, so treat this the way you treat an area's
    description prose and don't show it to the player word for word."""

    def in_bounds(self, position: Position) -> bool:
        """Return whether a cell lies on this level's grid.

        Everything off the grid is outside the dungeon, which the engine treats as solid: the party
        cannot walk there, and an authored position out here is a content error that
        [`validate_adventure`][osrlib.crawl.adventure.validate_adventure] catches.

        Args:
            position: The cell to test.

        Returns:
            True when `0 <= x < width` and `0 <= y < height`.
        """
        x, y = position
        return 0 <= x < self.width and 0 <= y < self.height

    def edge(self, position: Position, direction: Direction) -> Edge:
        """Return what stands on one side of a cell.

        This is the read you want rather than indexing `edges` yourself: it canonicalizes the key, so
        a cell's east side and its neighbour's west side give the same answer, and it supplies the
        wall for everything absent. Call it to find out whether the party can walk that way, whether
        there is a door to open, and which door the overlay's state belongs to.

        An edge with no entry in the map is wall, because authored content declares its passages
        rather than its walls, and the level boundary is wall too.

        Args:
            position: The cell.
            direction: Which of the cell's four edges.

        Returns:
            The edge entry, or a wall edge when none is authored or either cell is off the grid. The
                wall is a fresh [`Edge`][osrlib.crawl.dungeon.Edge] rather than a shared one, and it
                contains no door.
        """
        if not self.in_bounds(position) or not self.in_bounds(step(position, direction)):
            return Edge(kind=EdgeKind.WALL)
        return self.edges.get(edge_key(position, direction), Edge(kind=EdgeKind.WALL))

    def area_at(self, position: Position) -> AreaSpec | None:
        """Return the keyed area covering a cell, or `None` for corridor.

        The engine calls this on every step to work out whether the party has just walked into a
        room and its content is due. Call it to label the party's location, or to show which room a
        cell belongs to on a map.

        Args:
            position: The cell.

        Returns:
            The first area whose `cells` include the position, in the order you authored them, or
                `None` when the cell is corridor. Overlapping areas are not rejected, so the authored
                order is what decides between them.
        """
        for area in self.areas:
            if position in area.cells:
                return area
        return None

    def transition_at(self, position: Position) -> TransitionSpec | None:
        """Return the transition standing on a cell, or `None`.

        [`UseStairs`][osrlib.crawl.commands.UseStairs] asks this and refuses when the answer is
        `None`, which is also what makes a chute one-way: the arrival cell holds no transition back.
        Call it to show a stairs marker on a map, or to offer the command only where it works.

        Args:
            position: The cell.

        Returns:
            The first transition authored on that cell, or `None` when none is.
        """
        for transition in self.transitions:
            if transition.position == position:
                return transition
        return None


class DungeonSpec(BaseModel):
    """A dungeon: one or more levels joined by transitions.

    This is the unit an [`Adventure`][osrlib.crawl.adventure.Adventure] holds and the unit
    [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] names. Build its levels first, then wrap
    them here, then put the dungeon in an adventure beside its town. An adventure may hold several,
    and a [`TransitionSpec`][osrlib.crawl.dungeon.TransitionSpec] may point at another one's id, so a
    stair can lead out of one dungeon and into the next.

    Nothing here says which level is the way in. Some level needs an `entrance`, and
    [`validate_adventure`][osrlib.crawl.adventure.validate_adventure] is what checks that one does.

    Attributes:
        id: The dungeon's id.
        name: The dungeon's name.
        levels: Its levels.

    Raises:
        ValueError: If two levels carry the same `number`.

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
    """The dungeon's id, unique within the adventure. [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon],
    the town's `travel_turns` map, transitions between dungeons, and every state-overlay reference
    name it."""
    name: str = ""
    """The dungeon's name, for your front end to show: `"The Old Crypt"`."""
    levels: tuple[LevelSpec, ...] = Field(min_length=1)
    """The dungeon's levels, at least one, with unique `number`s. Look one up with
    [`level`][osrlib.crawl.dungeon.DungeonSpec.level] rather than by position. The order does matter
    in one place: [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] lands the party on the first
    level in this tuple that has an `entrance`."""

    @model_validator(mode="after")
    def _level_numbers_unique(self) -> DungeonSpec:
        numbers = [level.number for level in self.levels]
        if len(set(numbers)) != len(numbers):
            raise ValueError(f"dungeon {self.id!r} has duplicate level numbers")
        return self

    def level(self, number: int) -> LevelSpec:
        """Return the level with `number`.

        Use this to turn a level number out of a command, an event, or a transition back into the
        level it names, rather than searching `levels` yourself.

        Args:
            number: The 1-based level number.

        Returns:
            The level spec.

        Raises:
            ValueError: If no level has that number. The message names the dungeon and the number.
        """
        for level in self.levels:
            if level.number == number:
                return level
        raise ValueError(f"dungeon {self.id!r} has no level {number}")


class PartyLocation(BaseModel):
    """Where the party is: in the base town, or on a dungeon cell facing a direction.

    The session keeps one of these on
    [`DungeonState.location`][osrlib.crawl.dungeon.DungeonState] and moves it as the party moves. You
    read it to draw the map and to know which mode the party is in, and you never write it. The referee
    command [`PlaceParty`][osrlib.crawl.commands.PlaceParty] is how a game moves the party by fiat.

    The two shapes are exclusive and the model enforces it: a town location carries no dungeon
    fields, and a dungeon location carries all four.

    Attributes:
        kind: Which of the two shapes this is.
        dungeon_id: The dungeon the party is in.
        level_number: The level it is on.
        position: The cell it stands on.
        facing: The direction it faces.

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
    """`"town"` when the party is in the base town between delves, `"dungeon"` when it is standing on
    a grid. A new session starts in town."""
    dungeon_id: str | None = None
    """The id of the dungeon the party is in, and `None` in town."""
    level_number: int | None = None
    """The 1-based number of the level the party is on, and `None` in town."""
    position: Position | None = None
    """The cell the party stands on, and `None` in town."""
    facing: Direction | None = None
    """The direction the party faces, and `None` in town. Facing is what a front end draws the view
    from. Movement itself names its own direction, so the party can step any way it likes regardless
    of which way it looks."""

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
    """What has happened to one door: open, wedged, discovered, unlocked.

    This is the mutable half of a door. [`DoorSpec`][osrlib.crawl.dungeon.DoorSpec] is how you
    authored it and never changes. This records what the party has done since. Get one from
    [`DungeonState.door`][osrlib.crawl.dungeon.DungeonState.door] with the door's
    [`edge_ref`][osrlib.crawl.dungeon.edge_ref].

    Attributes:
        open: Whether the door stands open.
        wedged: Whether a spike holds it.
        discovered: Whether a secret door has been found.
        unlocked: Whether a lock has been dealt with.
        opened_by_party: Whether the party is the one that opened it.
    """

    model_config = ConfigDict(validate_assignment=True)

    open: bool = False
    """Whether the door stands open right now. A door with `starts_open` set begins here as `True`.
    The party walks through an open door without opening it again."""
    wedged: bool = False
    """Whether an iron spike holds the door, from [`WedgeDoor`][osrlib.crawl.commands.WedgeDoor]. A
    wedged door does not swing shut behind the party, which is the point of carrying spikes."""
    discovered: bool = False
    """Whether a secret door has been found. A `secret` door does nothing for the party until a
    successful secret-door [`Search`][osrlib.crawl.commands.Search] sets this. Until then the edge reads
    as wall. A normal door ignores it."""
    unlocked: bool = False
    """Whether a locked door has been dealt with, by
    [`PickLock`][osrlib.crawl.commands.PickLock] or by a referee
    [`SetDoorState`][osrlib.crawl.commands.SetDoorState]. A door stays unlocked once it is: relocking
    is a referee's write, not something closing the door does."""
    opened_by_party: bool = False
    """Whether the party is the one that opened this door. The swing-shut rule reads it: only doors
    the party opened, by whatever means, swing closed behind it, while a door you authored open stays
    open."""


class DroppedItem(BaseModel):
    """One stack of an ordinary item lying on the floor.

    A [`DropPile`][osrlib.crawl.dungeon.DropPile] holds these. Identical items stack, so five iron
    spikes are one entry with a quantity rather than five entries.

    Attributes:
        item_id: Which item this is.
        quantity: How many are in the stack.
    """

    model_config = ConfigDict(validate_assignment=True)

    item_id: str
    """The item's template id, resolving against the session's effective equipment catalog."""
    quantity: int = Field(ge=1)
    """How many are in the stack, at least one. A stack that reaches zero is removed from the pile
    rather than kept at zero."""


class DropPile(BaseModel):
    """What is lying on one cell's floor, waiting to be picked up.

    Piles are where loose goods end up: gear the party dropped with
    [`DropItems`][osrlib.crawl.commands.DropItems], what the monsters left at the end of a fight, and
    the part of a cache the party could not carry. The party picks a pile up with
    [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure] naming the reserved feature id `"pile"`,
    which is why no authored feature may use that id. Goods the party scatters as bait while it runs
    from a pursuer are the exception: those are gone rather than dropped here.

    The session keeps these in [`DungeonState.piles`][osrlib.crawl.dungeon.DungeonState] keyed by
    [`cell_ref`][osrlib.crawl.dungeon.cell_ref]. Piles form only where the party has stood, so a pile
    is always on a cell the party knows about.

    Attributes:
        items: The ordinary items in the pile.
        coins: The coins in the pile.
        valuables: The gems and jewellery in the pile.
        magic_items: The magic items in the pile.
    """

    model_config = ConfigDict(validate_assignment=True)

    items: list[DroppedItem] = []
    """The ordinary items lying here, stacked by template id."""
    coins: Coins = Coins()
    """The coins lying here, by denomination."""
    valuables: list[ValuableInstance] = []
    """The gems and jewellery lying here, each keeping the id it already had."""
    magic_items: list[MagicItemInstance] = []
    """The magic items lying here, each keeping its own charges or quantity."""


class DungeonState(BaseModel):
    """Everything play has changed: the overlay the session writes over frozen content.

    The adventure you authored never changes. This is where the running game records what happened to
    it, and it is the part a save file contains. [`GameSession.new`][osrlib.crawl.session.GameSession.new]
    makes one, the command handlers write it, and you read it through
    [`build_player_view`][osrlib.crawl.views.build_player_view] or
    [`build_referee_view`][osrlib.crawl.views.build_referee_view] rather than reaching in, so you get
    the right visibility for your audience.

    References into content are strings rather than objects, so the overlay serializes flat and a
    save never has to carry the adventure with it. They come in four shapes: `"{dungeon}:{level}"`
    keys the explored and seen maps, [`edge_ref`][osrlib.crawl.dungeon.edge_ref] keys doors,
    `"{dungeon}:{level}:{area_or_feature_id}"` keys areas and authored features, and
    [`cell_ref`][osrlib.crawl.dungeon.cell_ref] keys drop piles.

    Two maps of cells look alike and are not. `explored` is the party's footprint, the cells it has
    physically walked, and it is what movement cost reads. `seen` is the party's map memory, the
    cells its own light has shown it, and it is read by the player projection only so a front end's
    automap can keep a room the party looked into and walked past.

    The attempt memories are here rather than in a procedure's local variables because they are game
    state that has to survive a save: who has already listened at this door, who has already searched
    this cell for this kind of thing, and which thief failed this lock and at what level.

    Attributes:
        location: Where the party is.
        explored: Cells the party has walked.
        seen: Cells the party has looked at.
        doors: What has happened to each door.
        sprung_traps: Traps that have gone off.
        removed_traps: Traps a thief has taken out.
        found_traps: Traps the party knows about.
        found_tricks: Construction tricks the party has found.
        discovered_features: Unused.
        emptied_caches: Authored caches the party has emptied.
        piles: What is lying on the floor, by cell.
        generated_caches: Treasure the engine rolled, by cache id.
        generated_treasure_areas: Areas whose treasure has already rolled.
        resolved_encounters: Areas whose keyed encounter is over.
        listen_attempts: Who has listened at each door.
        search_attempts: Who has searched each cell for what.
        inspect_attempts: Who has inspected each cache for traps.
        removal_attempts: Who has tried to remove each trap.
        lock_failures: Which thief failed each lock, and at what level.
    """

    model_config = ConfigDict(validate_assignment=True)

    location: PartyLocation = PartyLocation(kind="town")
    """Where the party is standing, or that it is in town. A new session starts in town."""
    explored: dict[str, list[Position]] = {}
    """The cells the party has physically entered, keyed `"{dungeon}:{level}"`. This is the footprint
    movement cost reads: stepping back into an explored cell is three times as fast as breaking new
    ground. Drop-pile visibility reads it too."""
    seen: dict[str, list[Position]] = {}
    """The cells the party's light has shown it, keyed `"{dungeon}:{level}"`. This is map memory,
    read by the player projection only, and it never affects movement cost. Cells append in sorted
    `(x, y)` order so a save is byte-identical across runs."""
    doors: dict[str, DoorState] = {}
    """Each door's [`DoorState`][osrlib.crawl.dungeon.DoorState], keyed by
    [`edge_ref`][osrlib.crawl.dungeon.edge_ref]. Entries appear on first touch rather than up front,
    so a door nobody has reached has none. Read one through
    [`door`][osrlib.crawl.dungeon.DungeonState.door]."""
    sprung_traps: list[str] = []
    """The traps that have gone off, by area or feature reference. A sprung trap is done: it never
    rolls again."""
    removed_traps: list[str] = []
    """The treasure traps a thief has taken out with
    [`RemoveTreasureTrap`][osrlib.crawl.commands.RemoveTreasureTrap], by feature reference. A removed
    trap never rolls again either. Room traps never appear here, since nothing disarms one."""
    found_traps: list[str] = []
    """The traps the party knows about, by area or feature reference, from a successful
    [`Search`][osrlib.crawl.commands.Search] on a room trap or
    [`InspectTreasure`][osrlib.crawl.commands.InspectTreasure] on a treasure trap. A found trap stops
    taking its spring roll, which is what finding one gets you, and a treasure trap has to be here before
    [`RemoveTreasureTrap`][osrlib.crawl.commands.RemoveTreasureTrap] will work on it."""
    found_tricks: list[str] = []
    """The construction tricks the party has found by searching, by feature reference."""
    discovered_features: list[str] = []
    """Nothing writes this. Secret doors record their discovery on
    [`DoorState.discovered`][osrlib.crawl.dungeon.DoorState] and found features on `found_traps` and
    `found_tricks`, so this stays empty in every session the engine runs."""
    emptied_caches: list[str] = []
    """The authored caches the party has emptied, by feature reference. An emptied cache gives
    nothing more. Engine-rolled caches are removed from `generated_caches` outright instead of being
    listed here."""
    piles: dict[str, DropPile] = {}
    """What is lying on the floor, keyed by [`cell_ref`][osrlib.crawl.dungeon.cell_ref]. See
    [`DropPile`][osrlib.crawl.dungeon.DropPile]."""
    generated_caches: dict[str, GeneratedCache] = {}
    """Treasure the engine rolled, keyed by a minted cache id like `"cache-0001"`. A
    `HoardGeneratedEvent` announces the id, [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure] names
    it, and emptying one deletes the entry."""
    generated_treasure_areas: list[str] = []
    """The areas whose [`AreaTreasureSpec`][osrlib.crawl.dungeon.AreaTreasureSpec] has already rolled,
    by area reference, so entering a room twice does not double its loot."""
    resolved_encounters: list[str] = []
    """The areas whose keyed encounter is over, by area reference. The room stays clear afterwards."""
    listen_attempts: dict[str, list[str]] = {}
    """Which characters have listened at each door: character ids keyed by
    [`edge_ref`][osrlib.crawl.dungeon.edge_ref]. One try each, so a party cannot listen its way past
    a bad roll by queueing up."""
    search_attempts: dict[str, list[str]] = {}
    """Which characters have searched each cell for each kind of thing: character ids keyed
    `"{cell_ref}:{kind}"`, where the kind is what [`Search`][osrlib.crawl.commands.Search] was looking
    for. One try each per cell per kind."""
    inspect_attempts: dict[str, list[str]] = {}
    """Which characters have inspected each cache for traps: character ids keyed by feature
    reference. One try each."""
    removal_attempts: dict[str, list[str]] = {}
    """Which characters have tried to remove each trap: character ids keyed by feature reference. One
    try each, so a failed removal is final for that thief."""
    lock_failures: dict[str, dict[str, int]] = {}
    """Which thief failed which lock, and at what level: character id to level, keyed by
    [`edge_ref`][osrlib.crawl.dungeon.edge_ref]. The level is why it records a number rather than a
    flag. A thief who failed a lock may try it again once they have gained a level, and this is what
    that comparison reads."""

    def is_explored(self, dungeon_id: str, level_number: int, position: Position) -> bool:
        """Return whether the party has walked a cell.

        Movement cost reads this: a step back into an explored cell costs a third of a step into new
        ground. Call it to shade a map, or to work out what a move is about to cost.

        Args:
            dungeon_id: The dungeon id.
            level_number: The 1-based level number.
            position: The cell.

        Returns:
            True when the party has physically entered that cell. A cell the party has only seen by
                its own light answers False.
        """
        return position in self.explored.get(f"{dungeon_id}:{level_number}", [])

    def mark_explored(self, dungeon_id: str, level_number: int, position: Position) -> None:
        """Mark a cell as walked.

        The session calls this as the party arrives. Marking a cell twice changes nothing, so you can
        call it without checking first.

        Args:
            dungeon_id: The dungeon id.
            level_number: The 1-based level number.
            position: The cell.
        """
        key = f"{dungeon_id}:{level_number}"
        cells = self.explored.get(key)
        if cells is None:
            self.explored[key] = [position]
        elif position not in cells:
            cells.append(position)

    def mark_seen(self, dungeon_id: str, level_number: int, positions: Iterable[Position]) -> None:
        """Mark cells as seen: the party's map memory of what its light has shown it.

        Seen cells are read by the player projection only, so a front end's automap remembers a room
        the party's light reached after the party has walked on. They never affect movement cost,
        which reads the walked footprint through
        [`is_explored`][osrlib.crawl.dungeon.DungeonState.is_explored], and they never affect
        drop-pile visibility, which stays on walked cells because piles only form where the party has
        stood.

        Marking a cell twice changes nothing. New cells append in sorted `(x, y)` order, so a save is
        byte-identical across runs regardless of the order you pass them in.

        Args:
            dungeon_id: The dungeon id.
            level_number: The 1-based level number.
            positions: The cells to remember. Already-seen cells are skipped.
        """
        key = f"{dungeon_id}:{level_number}"
        fresh = sorted(set(positions) - set(self.seen.get(key, [])))
        if fresh:
            self.seen.setdefault(key, []).extend(fresh)

    def door(self, ref: str) -> DoorState:
        """Return one door's live state, creating the entry the first time you ask.

        Door entries are not created up front, so this is how you read one without worrying about
        whether the party has reached that door yet. The object it returns is the one in the map, so
        writing to it writes to the overlay.

        Args:
            ref: The door's [`edge_ref`][osrlib.crawl.dungeon.edge_ref]. Both sides of a door
                canonicalize to the same reference, so either one reaches the same state.

        Returns:
            The door's overlay entry, freshly created and all-`False` when the door has not been
                touched before. A door you authored `starts_open` is seeded to open by the session,
                not here.
        """
        state = self.doors.get(ref)
        if state is None:
            state = DoorState()
            self.doors[ref] = state
        return state
