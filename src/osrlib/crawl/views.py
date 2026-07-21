"""The projection API: the player's safe whitelist and the referee's full state.

`execute()` mutates session state; [`build_player_view`][osrlib.crawl.views.build_player_view]
and [`build_referee_view`][osrlib.crawl.views.build_referee_view] build these frozen
projections from that state alone, never from the event log.

The player view is an enumerated whitelist: party public sheets, location and
facing, explored cells with their edges (secret doors only if discovered — an
undiscovered secret door renders as wall), known piles and emptied caches in
explored space, active effects on party members with remaining durations, the
elapsed clock, the mode, the current encounter/battle public state (names,
counts, distances, visible conditions — never HP), fatigue/exhaustion/deprivation
status, and the adventure's public prose. It never carries unexplored geometry,
undiscovered traps or secret doors, monster HP or stat internals,
referee-visibility roll outcomes, session flags, RNG state, or the seed — the
seed lives only in the save, and neither view carries it.

The referee view carries everything else the save does, minus RNG internals and
the seed, for LLM referees and tests. A front end must never trust the client:
a networked game keeps the session and the referee view server-side, and returns
only the player view — or player-visibility events — over the wire.
"""

from pydantic import BaseModel, ConfigDict

from osrlib.core.effects import Condition, has_condition
from osrlib.core.items import MagicItemCategory, MagicItemInstance, magic_item_template
from osrlib.crawl.dungeon import Direction, EdgeKind, PartyLocation, Position, cell_ref, edge_ref, step
from osrlib.crawl.exploration import EXHAUSTED_KIND, FATIGUE_KIND

__all__ = [
    "EdgeView",
    "EncounterGroupView",
    "EncounterView",
    "ExploredLevelView",
    "MemberEffectView",
    "MemberView",
    "PileView",
    "PlayerView",
    "RefereeView",
    "build_player_view",
    "build_referee_view",
]


class MemberView(BaseModel):
    """One member's public sheet: the players know their own characters."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    class_id: str
    level: int
    current_hp: int
    max_hp: int
    conditions: tuple[str, ...]
    inventory: dict
    memorized_spells: tuple[dict, ...]


class MemberEffectView(BaseModel):
    """An active effect on a party member — players track their own torches and spells."""

    model_config = ConfigDict(frozen=True)

    character_id: str
    kind: str
    remaining_rounds: int | None


class EdgeView(BaseModel):
    """One visible edge: its kind (undiscovered secret doors render as wall) and door state."""

    model_config = ConfigDict(frozen=True)

    kind: str
    door_open: bool | None = None
    door_wedged: bool | None = None


class PileView(BaseModel):
    """A known dropped pile in explored space."""

    model_config = ConfigDict(frozen=True)

    items: tuple[str, ...]
    coins_gp_value: int


class ExploredLevelView(BaseModel):
    """One level's explored map: cells and their edges."""

    model_config = ConfigDict(frozen=True)

    dungeon_id: str
    level_number: int
    cells: tuple[Position, ...]
    edges: dict[str, EdgeView]


class EncounterGroupView(BaseModel):
    """A monster group as the players see it: id, name, count, distance, behavior — never HP.

    The group `id` is the command vocabulary: battle declarations name their
    `target_group_id` with it, so the projection must carry it for a wire client
    to fight at all — an allocator ordinal, not a secret (the id doctrine
    [`MemberView`][osrlib.crawl.views.MemberView] already sets).
    """

    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    count: int
    distance_feet: int
    visible_conditions: tuple[str, ...]


class EncounterView(BaseModel):
    """The current encounter or battle's public state."""

    model_config = ConfigDict(frozen=True)

    groups: tuple[EncounterGroupView, ...]
    stance: str | None
    in_battle: bool
    battle_round: int | None = None
    pursuit_gap_feet: int | None = None


class PlayerView(BaseModel):
    """The safe projection: an enumerated whitelist of exactly the fields a player may see."""

    model_config = ConfigDict(frozen=True)

    adventure_name: str
    adventure_description: str
    town_name: str
    town_description: str
    town_services: tuple[str, ...]
    party: tuple[MemberView, ...]
    location: PartyLocation
    clock_rounds: int
    mode: str
    explored: tuple[ExploredLevelView, ...]
    piles: dict[str, PileView]
    emptied_caches: tuple[str, ...]
    effects: tuple[MemberEffectView, ...]
    fatigued: bool
    exhausted: bool
    deprivation: dict[str, dict[str, int]]
    encounter: EncounterView | None = None


class RefereeView(BaseModel):
    """The full state projection minus RNG internals, for LLM referees and tests."""

    model_config = ConfigDict(frozen=True)

    state: dict


_MASKED_CATEGORY_NAMES = {
    MagicItemCategory.POTION: "a potion",
    MagicItemCategory.SCROLL: "a scroll",
    MagicItemCategory.RING: "a ring",
    MagicItemCategory.WAND: "a wand",
    MagicItemCategory.STAFF: "a staff",
    MagicItemCategory.ROD: "a rod",
    MagicItemCategory.MISC: "a curious device",
}


def _masked_magic_item(instance: MagicItemInstance) -> dict:
    """One magic item as the player sees it — masked until identified.

    An unidentified item shows only its category display name (an enchanted arm
    shows its base — "a sword with a faint aura", the concession because *detect
    magic* exists); an identified one shows its true name and id. Charges,
    sentience, and per-item state never appear at any identification level: by
    RAW, charges are undiscoverable.
    """
    from osrlib.data import load_equipment

    template = magic_item_template(instance)
    if instance.identified:
        return {
            "instance_type": "magic_item",
            "instance_id": instance.instance_id,
            "template_id": instance.template_id,
            "name": template.name,
            "quantity": instance.quantity,
            "identified": True,
            "cursed": instance.cursed_revealed,
        }
    display = _MASKED_CATEGORY_NAMES.get(template.category)
    if display is None:
        base_id = instance.base_item_id or template.base_item_id
        base_name = load_equipment().get(base_id).name.lower() if base_id is not None else "arm"
        display = f"a {base_name} with a faint aura"
    return {
        "instance_type": "magic_item",
        "instance_id": instance.instance_id,
        "display": display,
        "quantity": instance.quantity,
        "identified": False,
    }


def _masked_instance(instance) -> dict:
    if isinstance(instance, MagicItemInstance):
        return _masked_magic_item(instance)
    return instance.model_dump(mode="json")


def _masked_inventory(member) -> dict:
    """The inventory as the player sees it: valuables exact, magic items masked."""
    inventory = member.inventory
    return {
        "items": [_masked_instance(instance) for instance in inventory.items],
        "purse": inventory.purse.model_dump(mode="json"),
        "valuables": [valuable.model_dump(mode="json") for valuable in inventory.valuables],
        "worn_armour": _masked_instance(inventory.worn_armour) if inventory.worn_armour is not None else None,
        "shield": _masked_instance(inventory.shield) if inventory.shield is not None else None,
        "wielded": [_masked_instance(instance) for instance in inventory.wielded],
        "rings": [_masked_instance(instance) for instance in inventory.rings],
    }


def _effect_remaining_rounds(session, effect) -> int | None:
    """Remaining rounds for the member-effect view — potion durations stay hidden.

    By RAW, the referee rolls and tracks a potion's duration and never tells the
    player how long it will last, so a potion-sourced effect always reports
    `None` here.
    """
    if effect.definition.params.get("item_source") == "potion":
        return None
    if effect.expires_round is None:
        return None
    return max(0, effect.expires_round - session.clock.rounds)


def build_player_view(session) -> PlayerView:
    """Build the player view from session state (never from the event log).

    Args:
        session (osrlib.crawl.session.GameSession): The running session.

    Returns:
        The frozen whitelist projection.
    """
    members = tuple(
        MemberView(
            id=member.id,
            name=member.name,
            class_id=member.class_id,
            level=member.level,
            current_hp=member.current_hp,
            max_hp=member.max_hp,
            conditions=tuple(active.condition.value for active in member.conditions),
            inventory=_masked_inventory(member),
            memorized_spells=tuple(copy.model_dump(mode="json") for copy in member.memorized_spells),
        )
        for member in session.party.members
    )
    member_ids = {member.id for member in session.party.members}
    effects = tuple(
        MemberEffectView(
            character_id=effect.target_ref,
            kind=effect.definition.kind,
            remaining_rounds=_effect_remaining_rounds(session, effect),
        )
        for effect in session.ledger.effects
        if effect.target_ref in member_ids
    )
    explored = tuple(_explored_levels(session))
    visible_refs = _visible_cell_refs(session)
    piles = {
        ref: PileView(
            items=tuple(
                (
                    *(f"{entry.item_id}×{entry.quantity}" for entry in pile.items),
                    *(
                        str(_masked_magic_item(item).get("name", _masked_magic_item(item).get("display")))
                        for item in pile.magic_items
                    ),
                    *(valuable.name or valuable.kind for valuable in pile.valuables),
                )
            ),
            coins_gp_value=pile.coins.value_gp,
        )
        for ref, pile in session.dungeon_state.piles.items()
        if ref in visible_refs
    }
    fatigued = any(session.ledger.active_on(member.id, FATIGUE_KIND) for member in session.party.members)
    exhausted = any(session.ledger.active_on(member.id, EXHAUSTED_KIND) for member in session.party.members)
    deprivation = {
        member_id: {"food_days": state.food_days, "water_days": state.water_days}
        for member_id, state in session.deprivation.items()
        if state.worst > 0
    }
    return PlayerView(
        adventure_name=session.adventure.name,
        adventure_description=session.adventure.description,
        town_name=session.adventure.town.name,
        town_description=session.adventure.town.description,
        town_services=session.adventure.town.services,
        party=members,
        location=session.dungeon_state.location,
        clock_rounds=session.clock.rounds,
        mode=session.mode.value,
        explored=explored,
        piles=piles,
        emptied_caches=tuple(session.dungeon_state.emptied_caches),
        effects=effects,
        fatigued=fatigued,
        exhausted=exhausted,
        deprivation=deprivation,
        encounter=_encounter_view(session),
    )


def _visible_cell_refs(session) -> set[str]:
    refs: set[str] = set()
    for key, cells in session.dungeon_state.explored.items():
        dungeon_id, level_number = key.rsplit(":", 1)
        for cell in cells:
            refs.add(cell_ref(dungeon_id, int(level_number), cell))
    return refs


# The dungeon grid is authored at the classic ten-foot square, so a torch's
# thirty-foot radius reaches three cells of open floor.
_CELL_FEET = 10
_DEFAULT_LIGHT_FEET = 30


def _light_radius_feet(params) -> int:
    """The radius of one light-family effect, in feet.

    Equipment and magic-item light sources store the radius under
    `light_radius_feet`; the *light* spell family stores it under `radius_feet`.
    Read whichever the source carries, falling back to the torch default.
    """
    raw = params.get("light_radius_feet", params.get("radius_feet"))
    return int(raw) if raw is not None else _DEFAULT_LIGHT_FEET


def _sight_passes(session, level, location, cell: Position, direction: Direction) -> bool:
    """Whether torchlight (and sight) crosses one cell edge.

    Open floor and an open, non-secret door let light through; walls, blocked
    edges, and shut or undiscovered-secret doors stop it — the same passability
    the mover and the edge projection already agree on.
    """
    edge = level.edge(cell, direction)
    if edge.kind is EdgeKind.OPEN:
        return True
    if edge.kind is EdgeKind.DOOR:
        ref = edge_ref(location.dungeon_id, location.level_number, cell, direction)
        state = session.dungeon_state.doors.get(ref)
        if edge.door.kind == "secret" and (state is None or not state.discovered):
            return False
        return bool(state.open) if state is not None else edge.door.starts_open
    return False


def _light_reveal(session) -> tuple[str | None, set[Position]]:
    """Cells the party sees *right now* by its own light, keyed to their level.

    This is sight, not exploration: the party glimpses the lit room it stands in
    and a few cells down open passages, but these cells never enter the persisted
    explored set. So seeing a room never cheapens the movement of later walking
    it, and stepping away lets the unwalked cells fall dark again. Torchlight
    fills the keyed room whole and spills through open doorways out to the light's
    radius; walls, and shut or undiscovered doors, stop it. Empty unless the party
    stands in a dungeon with a light burning.

    Returns:
        The `"{dungeon}:{level}"` explored-map key for the party's level and the
        set of seen cells, or `(None, set())` when nothing is lit.
    """
    location = session.dungeon_state.location
    if location.kind != "dungeon":
        return None, set()
    lit, _ = session.party_light()
    if not lit:
        return None, set()
    try:
        level = session.adventure.dungeon(location.dungeon_id).level(location.level_number)
    except ValueError:
        return None, set()

    from osrlib.crawl.session import LIGHT_EFFECT_KINDS

    living_ids = {member.id for member in session.party.living_members()}
    radius_cells = max(
        (
            _light_radius_feet(effect.definition.params) // _CELL_FEET
            for effect in session.ledger.effects
            if effect.target_ref in living_ids and effect.definition.kind in LIGHT_EFFECT_KINDS
        ),
        default=_DEFAULT_LIGHT_FEET // _CELL_FEET,
    )
    origin = tuple(location.position)
    # The keyed room the party stands in is lit to its far corners — you are
    # standing inside it — so its open-connected cells reveal even past the
    # torch's reach; elsewhere, light spills through open passages only within
    # that straight-line (Chebyshev) reach. Both honour real passability: the
    # flood only crosses an edge sight passes, so walls and shut or undiscovered
    # doors stop it, and an alcove sealed off inside a keyed room stays dark.
    area = level.area_at(origin)
    in_room = {tuple(cell) for cell in area.cells} if area is not None else frozenset()
    seen: set[Position] = {origin}
    frontier = [origin]
    while frontier:
        cell = frontier.pop()
        for direction in Direction:
            neighbour = step(cell, direction)
            if neighbour in seen or not level.in_bounds(neighbour):
                continue
            within_reach = max(abs(neighbour[0] - origin[0]), abs(neighbour[1] - origin[1])) <= radius_cells
            if not (within_reach or neighbour in in_room):
                continue
            if not _sight_passes(session, level, location, cell, direction):
                continue
            seen.add(neighbour)
            frontier.append(neighbour)
    return f"{location.dungeon_id}:{location.level_number}", seen


def _explored_levels(session):
    reveal_key, reveal_cells = _light_reveal(session)
    for key, cells in session.dungeon_state.explored.items():
        dungeon_id, level_text = key.rsplit(":", 1)
        level_number = int(level_text)
        try:
            level = session.adventure.dungeon(dungeon_id).level(level_number)
        except ValueError:
            continue
        # Visible equals explored plus what the party's own light reveals from the
        # current cell (the spec's visible flag): the lit room and a few cells of
        # open passage, drawn now without waiting on a footstep into each square.
        visible = list(cells)
        if key == reveal_key:
            known = set(cells)
            visible.extend(cell for cell in reveal_cells if cell not in known)
        edges: dict[str, EdgeView] = {}
        for cell in visible:
            for direction in Direction:
                key_text = _canonical_edge(cell, direction)
                if key_text in edges:
                    continue
                edge = level.edge(cell, direction)
                if edge.kind is EdgeKind.DOOR:
                    ref = edge_ref(dungeon_id, level_number, cell, direction)
                    state = session.dungeon_state.doors.get(ref)
                    if edge.door.kind == "secret" and (state is None or not state.discovered):
                        edges[key_text] = EdgeView(kind="wall")
                        continue
                    edges[key_text] = EdgeView(
                        kind="door",
                        door_open=bool(state.open) if state is not None else edge.door.starts_open,
                        door_wedged=bool(state.wedged) if state is not None else False,
                    )
                else:
                    edges[key_text] = EdgeView(kind=edge.kind.value)
        yield ExploredLevelView(dungeon_id=dungeon_id, level_number=level_number, cells=tuple(visible), edges=edges)


def _canonical_edge(cell: Position, direction: Direction) -> str:
    from osrlib.crawl.dungeon import edge_key

    return edge_key(cell, direction)


def _encounter_view(session) -> EncounterView | None:
    state = session.encounter
    if state is None:
        return None
    groups = []
    for group in state.groups:
        living = [
            session.combatant(monster_id)
            for monster_id in group.monster_ids
            if not has_condition(session.combatant(monster_id), Condition.DEAD)
        ]
        conditions = sorted({active.condition.value for monster in living for active in monster.conditions})
        groups.append(
            EncounterGroupView(
                id=group.id,
                label=group.label,
                count=len(living),
                distance_feet=group.distance_feet,
                visible_conditions=tuple(conditions),
            )
        )
    return EncounterView(
        groups=tuple(groups),
        stance=state.stance,
        in_battle=session.battle is not None,
        battle_round=session.battle.round if session.battle is not None else None,
        pursuit_gap_feet=state.pursuit.gap_feet if state.pursuit is not None else None,
    )


def build_referee_view(session) -> RefereeView:
    """Build the referee view: everything but RNG internals and the seed.

    Args:
        session (osrlib.crawl.session.GameSession): The running session.

    Returns:
        The full-state projection.
    """
    from osrlib.persistence import session_state

    state = session_state(session, include_event_log=True)
    state.pop("rng_streams", None)
    state.pop("master_seed", None)
    return RefereeView(state=state)
