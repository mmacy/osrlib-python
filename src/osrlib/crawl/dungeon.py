"""The multi-level dungeon grid: cells, edges, doors, areas, traps, and state.

Authored content is frozen; play mutates a state overlay — the template/instance
split applied to space. [`DungeonSpec`][osrlib.crawl.dungeon.DungeonSpec] and its
levels, areas, features, and traps are game content, never mutated;
[`DungeonState`][osrlib.crawl.dungeon.DungeonState] carries everything play changes
(explored cells, door state, sprung traps, dropped piles, the party's location) and
serializes into saves.

Geometry, pinned as API convention: cells are 10' squares addressed `(x, y)` with
`x` increasing east and `y` increasing south from the level's northwest corner.
Edges are the single spatial truth for walls and doors: an `edges` map keyed by the
canonical edge key (a cell plus `north` or `west`, so each physical edge has exactly
one entry). An edge absent from the map is a wall — authored content declares its
passages (`open`) and doors explicitly — and the level boundary is implicitly wall.
"""

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
"""A cell address: `(x, y)`, x increasing east, y increasing south, from (0, 0)."""


class Direction(StrEnum):
    """The four grid directions.

    The wire values are lowercase — they serialize into commands, events, and
    saves; changing them is a `schema_version` bump.
    """

    NORTH = "north"
    EAST = "east"
    SOUTH = "south"
    WEST = "west"

    @property
    def vector(self) -> tuple[int, int]:
        """The `(dx, dy)` step for one cell in this direction."""
        return _VECTORS[self]

    @property
    def opposite(self) -> Direction:
        """The reverse direction."""
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

    Args:
        position: The starting cell.
        direction: The direction to step.

    Returns:
        The adjacent cell address (which may lie outside the level).
    """
    dx, dy = direction.vector
    return (position[0] + dx, position[1] + dy)


def edge_key(position: Position, direction: Direction) -> str:
    """Return the canonical key for the edge on `direction`'s side of `position`.

    Each physical edge has exactly one entry: the key is a cell plus `north` or
    `west`, so a cell's south edge is its southern neighbour's north edge and its
    east edge the eastern neighbour's west. The format is `"{x},{y}:{side}"`.

    Args:
        position: The cell.
        direction: Which of the cell's four edges.

    Returns:
        The canonical edge key.
    """
    x, y = position
    if direction is Direction.SOUTH:
        return f"{x},{y + 1}:north"
    if direction is Direction.EAST:
        return f"{x + 1},{y}:west"
    return f"{x},{y}:{direction.value}"


def cell_ref(dungeon_id: str, level_number: int, position: Position) -> str:
    """Return the structured cell reference used by location-bound effects.

    The pinned format is `cell:{dungeon}:{level}:{x},{y}` — an
    [`ActiveEffect.target_ref`][osrlib.core.effects.ActiveEffect] in this form
    anchors the effect to a dungeon cell.

    Args:
        dungeon_id: The dungeon id.
        level_number: The 1-based level number.
        position: The cell.

    Returns:
        The cell reference string.
    """
    return f"cell:{dungeon_id}:{level_number}:{position[0]},{position[1]}"


def edge_ref(dungeon_id: str, level_number: int, position: Position, direction: Direction) -> str:
    """Return the state-overlay reference for one physical edge (door bookkeeping).

    Args:
        dungeon_id: The dungeon id.
        level_number: The 1-based level number.
        position: The cell.
        direction: Which of the cell's four edges.

    Returns:
        The edge reference string, canonicalized like
        [`edge_key`][osrlib.crawl.dungeon.edge_key].
    """
    return f"{dungeon_id}:{level_number}:{edge_key(position, direction)}"


class EdgeKind(StrEnum):
    """What occupies an edge between two cells."""

    OPEN = "open"
    WALL = "wall"
    DOOR = "door"


class DoorSpec(BaseModel):
    """A door on an edge, exactly as authored.

    `kind="secret"` doors are invisible until discovered (a successful secret-door
    search marks them in the state overlay). `stuck` and `locked` are the authored
    starting conditions; play mutates the overlay, never this spec.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["normal", "secret"] = "normal"
    stuck: bool = False
    locked: bool = False
    starts_open: bool = False


class Edge(BaseModel):
    """One authored edge entry: its kind, plus the door when `kind="door"`."""

    model_config = ConfigDict(frozen=True)

    kind: EdgeKind
    door: DoorSpec | None = None

    @model_validator(mode="after")
    def _door_exactly_on_door_edges(self) -> Edge:
        if (self.kind is EdgeKind.DOOR) != (self.door is not None):
            raise ValueError("an edge carries a door spec exactly when its kind is 'door'")
        return self


class TransitionSpec(BaseModel):
    """A level transition on a cell: stairs, trapdoor, or chute.

    The destination is `(dungeon_id, level_number, position, facing)`. Chutes are
    one-way — `UseStairs` rejects on arrival cells that have no transition back.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["stairs_up", "stairs_down", "trapdoor", "chute"]
    position: Position
    to_dungeon_id: str
    to_level_number: int = Field(ge=1)
    to_position: Position
    to_facing: Direction


class TrapEffect(BaseModel):
    """What a sprung trap does — the example-trap census is the fixture set.

    `damage_dice` rolls once; `volley_dice` is the darts form (1d6 projectiles,
    each rolling `damage_dice` — a count-times-damage form the dice grammar alone
    can't say). `save` gates the whole effect (`negates` or `half` damage).
    `kills` marks save-or-die forms (poison gas). `condition` with its duration is
    the blindness form; `fall_feet` is the pit's falling damage; `transition`
    drops the victim elsewhere (slides). `manual` keeps prose for the rest.
    """

    model_config = ConfigDict(frozen=True)

    damage_dice: str | None = None
    volley_dice: str | None = None
    save: SaveSpec | None = None
    kills: bool = False
    condition: Condition | None = None
    condition_duration_dice: str | None = None
    condition_duration_amount: int | None = None
    condition_duration_unit: TimeUnit | None = None
    fall_feet: int | None = None
    transition: TransitionSpec | None = None
    manual: str | None = None

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
        return self


class TrapSpec(BaseModel):
    """A trap: room (over an area) or treasure (on a feature).

    `trigger` names the springing action (`enter` a cell of the trapped area,
    `open` a trapped cache). `affects` defaults to the triggerer; `party` covers
    forms like poison gas filling the room.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["room", "treasure"]
    trigger: Literal["enter", "open"]
    effect: TrapEffect
    affects: Literal["triggerer", "party"] = "triggerer"


class ValuableSpec(BaseModel):
    """An authored named valuable in a cache — instantiated on take.

    The authoring surface for named treasure: the example adventure's MacGuffin is
    one. `name` is the display name; the instance id comes from the session
    allocator when the cache is emptied.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["gem", "jewellery"]
    name: str = ""
    value_gp: int = Field(ge=0)
    weight_coins: int = Field(default=0, ge=0)


class AreaTreasureSpec(BaseModel):
    """An area's generated-treasure declaration: explicit type letters, or unguarded.

    Generates on first entry into the area — how content places generated treasure
    without monsters (the milestone dungeon uses it). `unguarded=True` rolls the
    dungeon level's unguarded-treasure band.
    """

    model_config = ConfigDict(frozen=True)

    letters: tuple[str, ...] = ()
    unguarded: bool = False

    @model_validator(mode="after")
    def _letters_or_unguarded(self) -> AreaTreasureSpec:
        if bool(self.letters) == self.unguarded:
            raise ValueError("an area treasure spec names letters or sets unguarded, not both or neither")
        return self


class TreasureBundle(BaseModel):
    """A mutable generated-treasure bundle: coins, valuables, and magic items."""

    model_config = ConfigDict(validate_assignment=True)

    coins: Coins = Coins()
    valuables: list[ValuableInstance] = []
    magic_items: list[MagicItemInstance] = []

    @property
    def empty(self) -> bool:
        """Whether the bundle holds nothing at all."""
        return self.coins.total_coins == 0 and not self.valuables and not self.magic_items


class GeneratedCache(BaseModel):
    """An engine-created treasure cache in the state overlay.

    Authored `FeatureSpec`s are frozen content; the state overlay owns play-created
    treasure — the template/instance split applied to loot. Generated
    hoards are untrapped (traps are authored content, per the stocking table's own
    separation, pinned).
    """

    model_config = ConfigDict(validate_assignment=True)

    cell_ref: str
    treasure_types: tuple[str, ...] = ()
    coins: Coins = Coins()
    valuables: list[ValuableInstance] = []
    magic_items: list[MagicItemInstance] = []


class FeatureSpec(BaseModel):
    """A keyed feature: a treasure cache, a construction trick, or custom content.

    Stairs are `TransitionSpec`'s alone — no second home. Caches carry the
    hand-placed contents (item ids and coins) and an optional treasure trap — the
    milestone's droppable, recoverable treasure. `cell` binds the feature to a cell;
    a feature listed on an area with `cell=None` binds to the whole area.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    kind: Literal["treasure_cache", "construction_trick", "custom"]
    description: str = ""
    cell: Position | None = None
    item_ids: tuple[str, ...] = ()
    coins: Coins = Coins()
    valuables: tuple[ValuableSpec, ...] = ()
    trap: TrapSpec | None = None

    @model_validator(mode="after")
    def _trap_kind_matches(self) -> FeatureSpec:
        if self.trap is not None and self.trap.kind != "treasure":
            raise ValueError(f"feature {self.id!r} carries a non-treasure trap")
        return self


class KeyedMonster(BaseModel):
    """One monster line of a keyed encounter: the template and its count."""

    model_config = ConfigDict(frozen=True)

    template_id: str
    count_dice: str | None = None
    count_fixed: int | None = None

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
    """An area's keyed encounter: monsters with counts and optional pins.

    `aware=True` means the monsters expect intruders (they never roll surprise);
    `stance` pins the reaction outright (no reaction roll); `alignment` fixes the
    spawn alignment for multi-option templates.
    """

    model_config = ConfigDict(frozen=True)

    monsters: tuple[KeyedMonster, ...] = Field(min_length=1)
    alignment: Alignment | None = None
    aware: bool = False
    stance: ReactionResult | None = None


class AreaSpec(BaseModel):
    """A keyed area (a room or cave): a named region over cells with content bindings.

    Areas annotate the grid; cells not in any area are corridor. Content prose
    lives here — events carry ids and front ends resolve prose against the
    adventure.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str = ""
    description: str = ""
    cells: tuple[Position, ...] = Field(min_length=1)
    encounter: KeyedEncounter | None = None
    features: tuple[FeatureSpec, ...] = ()
    trap: TrapSpec | None = None
    treasure: AreaTreasureSpec | None = None

    @model_validator(mode="after")
    def _trap_kind_matches(self) -> AreaSpec:
        if self.trap is not None and self.trap.kind != "room":
            raise ValueError(f"area {self.id!r} carries a non-room trap")
        return self


class WanderingSpec(BaseModel):
    """A level's wandering-monster parameters.

    The defaults are RAW: a 1-in-6 check every two turns. `table` overrides the
    compiled level-band table with an inline custom list (same row model).
    """

    model_config = ConfigDict(frozen=True)

    chance_in_six: int = Field(default=1, ge=0, le=6)
    interval_turns: int = Field(default=2, ge=1)
    table: EncounterTable | None = None


class LevelSpec(BaseModel):
    """One dungeon level: a grid of 10' cells with edges, areas, and transitions.

    `number` is 1-based and rules-visible — it keys the encounter-table band.
    `entrance` is where `EnterDungeon` and town travel land (required on some level
    per adventure validation).
    """

    model_config = ConfigDict(frozen=True)

    number: int = Field(ge=1)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    edges: dict[str, Edge] = {}
    areas: tuple[AreaSpec, ...] = ()
    features: tuple[FeatureSpec, ...] = ()
    transitions: tuple[TransitionSpec, ...] = ()
    wandering: WanderingSpec = WanderingSpec()
    entrance: Position | None = None

    def in_bounds(self, position: Position) -> bool:
        """Return whether a cell lies on this level's grid.

        Args:
            position: The cell to test.

        Returns:
            True when `0 <= x < width` and `0 <= y < height`.
        """
        x, y = position
        return 0 <= x < self.width and 0 <= y < self.height

    def edge(self, position: Position, direction: Direction) -> Edge:
        """Return the authored edge on one side of a cell.

        An edge absent from the map is a wall (authored content declares its
        passages), and the level boundary is implicitly wall.

        Args:
            position: The cell.
            direction: Which of the cell's four edges.

        Returns:
            The edge entry.
        """
        if not self.in_bounds(position) or not self.in_bounds(step(position, direction)):
            return Edge(kind=EdgeKind.WALL)
        return self.edges.get(edge_key(position, direction), Edge(kind=EdgeKind.WALL))

    def area_at(self, position: Position) -> AreaSpec | None:
        """Return the keyed area covering a cell, or `None` for corridor.

        Args:
            position: The cell.

        Returns:
            The first area whose cells include the position, in authored order.
        """
        for area in self.areas:
            if position in area.cells:
                return area
        return None

    def transition_at(self, position: Position) -> TransitionSpec | None:
        """Return the transition on a cell, or `None`.

        Args:
            position: The cell.

        Returns:
            The transition, if one is authored there.
        """
        for transition in self.transitions:
            if transition.position == position:
                return transition
        return None


class DungeonSpec(BaseModel):
    """A dungeon: one or more levels joined by transitions."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str = ""
    levels: tuple[LevelSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _level_numbers_unique(self) -> DungeonSpec:
        numbers = [level.number for level in self.levels]
        if len(set(numbers)) != len(numbers):
            raise ValueError(f"dungeon {self.id!r} has duplicate level numbers")
        return self

    def level(self, number: int) -> LevelSpec:
        """Return the level with `number`.

        Args:
            number: The 1-based level number.

        Returns:
            The level spec.

        Raises:
            ValueError: If no level has that number.
        """
        for level in self.levels:
            if level.number == number:
                return level
        raise ValueError(f"dungeon {self.id!r} has no level {number}")


class PartyLocation(BaseModel):
    """Where the party is: the base town, or a dungeon cell with facing."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["town", "dungeon"]
    dungeon_id: str | None = None
    level_number: int | None = None
    position: Position | None = None
    facing: Direction | None = None

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
    """One door's mutable overlay: open, wedged, discovered, unlocked.

    `opened_by_party` is the swing-shut rule's memory: only doors the party
    opened (by whatever means) swing shut behind it; authored-open doors stay.
    """

    model_config = ConfigDict(validate_assignment=True)

    open: bool = False
    wedged: bool = False
    discovered: bool = False
    unlocked: bool = False
    opened_by_party: bool = False


class DroppedItem(BaseModel):
    """One dropped item stack in a pile."""

    model_config = ConfigDict(validate_assignment=True)

    item_id: str
    quantity: int = Field(ge=1)


class DropPile(BaseModel):
    """Dropped items and coins on a cell — droppable, recoverable, distraction bait.

    Drops and loot round-trip: battle-end loot, death-save survivors, and player
    drops all land here and `TakeTreasure` recovers them.
    """

    model_config = ConfigDict(validate_assignment=True)

    items: list[DroppedItem] = []
    coins: Coins = Coins()
    valuables: list[ValuableInstance] = []
    magic_items: list[MagicItemInstance] = []


class DungeonState(BaseModel):
    """The mutable overlay play writes over the frozen adventure content.

    References are strings so the overlay serializes flat: explored cells key by
    `"{dungeon}:{level}"`, doors by [`edge_ref`][osrlib.crawl.dungeon.edge_ref],
    traps and caches by `"{dungeon}:{level}:{area_or_feature_id}"`, piles by
    [`cell_ref`][osrlib.crawl.dungeon.cell_ref]. Attempt memory (listen once per
    character per door, search once per character per cell per kind, the pick-lock
    lockout with the thief's level at failure) lives here too — it is game state,
    not procedure-local bookkeeping.
    """

    model_config = ConfigDict(validate_assignment=True)

    location: PartyLocation = PartyLocation(kind="town")
    explored: dict[str, list[Position]] = {}
    doors: dict[str, DoorState] = {}
    sprung_traps: list[str] = []
    removed_traps: list[str] = []
    found_traps: list[str] = []
    found_tricks: list[str] = []
    discovered_features: list[str] = []
    emptied_caches: list[str] = []
    piles: dict[str, DropPile] = {}
    generated_caches: dict[str, GeneratedCache] = {}
    generated_treasure_areas: list[str] = []
    resolved_encounters: list[str] = []
    listen_attempts: dict[str, list[str]] = {}
    search_attempts: dict[str, list[str]] = {}
    inspect_attempts: dict[str, list[str]] = {}
    removal_attempts: dict[str, list[str]] = {}
    lock_failures: dict[str, dict[str, int]] = {}

    def is_explored(self, dungeon_id: str, level_number: int, position: Position) -> bool:
        """Return whether the party has explored a cell.

        Args:
            dungeon_id: The dungeon id.
            level_number: The 1-based level number.
            position: The cell.

        Returns:
            True when the cell is in the explored set.
        """
        return position in self.explored.get(f"{dungeon_id}:{level_number}", [])

    def mark_explored(self, dungeon_id: str, level_number: int, position: Position) -> None:
        """Mark a cell explored (idempotent).

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

    def door(self, ref: str) -> DoorState:
        """Return (creating on first touch) the mutable state for one door.

        Args:
            ref: The door's [`edge_ref`][osrlib.crawl.dungeon.edge_ref].

        Returns:
            The door's overlay entry.
        """
        state = self.doors.get(ref)
        if state is None:
            state = DoorState()
            self.doors[ref] = state
        return state
