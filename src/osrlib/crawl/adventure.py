"""The adventure: the root document a session plays.

An [`Adventure`][osrlib.crawl.adventure.Adventure] contains everything you need to run a game except
the party and the seed for the random numbers. You build the levels in
[`osrlib.crawl.dungeon`][osrlib.crawl.dungeon], put them in dungeons, write a
[`TownSpec`][osrlib.crawl.adventure.TownSpec] for the base town, and combine them here. Then you
pass the adventure and a [`Party`][osrlib.crawl.party.Party] to
[`GameSession.new`][osrlib.crawl.session.GameSession.new] with a seed, and it starts the game.

The members, in the order you use them:

- [`TownSpec`][osrlib.crawl.adventure.TownSpec]: the base town, where a session starts.
- [`PartySpec`][osrlib.crawl.adventure.PartySpec]: the levels and number of characters the
  adventure is written for. osrlib doesn't read it.
- [`Adventure`][osrlib.crawl.adventure.Adventure]: the town, the dungeons, and the monsters, items,
  triggers, and quests the adventure adds.
- [`validate_adventure`][osrlib.crawl.adventure.validate_adventure]: takes an adventure and the
  built-in catalogs, and raises [`ContentValidationError`][osrlib.errors.ContentValidationError]
  with a list of every reference it can't resolve.

An adventure is frozen. The session reads it and never changes it. Everything that changes in play
goes into the session's [`DungeonState`][osrlib.crawl.dungeon.DungeonState] instead, so what you
wrote stays apart from what happened, and a save contains both. Names and descriptions live in these
models, not in events. An event contains ids, so to show a player the text, you look each id up in
the adventure.

Besides its dungeons, an adventure can add content and behavior. `monsters` and `items` bundle
templates that join the built-in catalogs in the sessions that play this adventure. `triggers`
([`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec]) define what happens when something happens
in play, and `quests` ([`QuestSpec`][osrlib.crawl.quests.QuestSpec]) define what the party is trying
to do. Gates ([`GateSpec`][osrlib.crawl.gates.GateSpec]) go on the doors and transitions of the levels.

`validate_adventure` checks that the document is consistent before anyone plays it. It follows the
ids in the tree to what they refer to, and raises one error that lists every problem it finds.
`GameSession.new` calls it for you, so a session doesn't start on content that fails it. Call it
yourself while you write the adventure and you find the problems sooner.

The guide
[Building an adventure](https://mmacy.github.io/osrlib-python/getting-started/building-an-adventure/)
walks through a complete program you can run.
"""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from osrlib.core.items import (
    AmmunitionTemplate,
    ArmourTemplate,
    EquipmentCatalog,
    GearTemplate,
    ItemTemplate,
    MagicItemCatalog,
    WeaponTemplate,
)
from osrlib.core.monsters import MonsterCatalog, MonsterTemplate
from osrlib.crawl.commands import (
    AwardXP,
    ConsequenceCommand,
    GrantCoins,
    GrantItem,
    PlaceParty,
    RemoveCoins,
    RemoveItem,
    SetDoorState,
    SpawnMonsters,
)
from osrlib.crawl.dungeon import (
    AreaSpec,
    Direction,
    DungeonSpec,
    EdgeKind,
    FeatureSpec,
    LevelSpec,
    Position,
    TransitionSpec,
    TrapSpec,
    step,
)
from osrlib.crawl.gates import ConditionSpec, GateSpec, HasItemCondition
from osrlib.crawl.quests import QuestSpec
from osrlib.crawl.triggers import (
    CARRIER_SELECTOR,
    FIRST_LIVING_SELECTOR,
    PARTY_SELECTOR,
    AreaEnteredPattern,
    DungeonEnteredPattern,
    ItemAcquiredPattern,
    LevelEnteredPattern,
    MonsterDefeatedPattern,
    TriggerPattern,
    TriggerSpec,
)
from osrlib.data import load_magic_items
from osrlib.errors import ContentValidationError

__all__ = [
    "Adventure",
    "PartySpec",
    "TownSpec",
    "validate_adventure",
]


class TownSpec(BaseModel):
    """The base town: where the party is safe, buys gear, and comes back to.

    Every adventure has exactly one town, and a session starts there. The town is not a map: it has
    no grid, no rooms, and nothing to explore. It is where the rules that need a safe place apply.
    In town the party can rest, buy equipment, sell treasure, and pay a temple for healing. Under
    the default ruleset, the party earns the experience for a delve only once it is back in town.

    Attributes:
        name: The town's name.
        description: Text describing the town.
        services: The services the town offers, to list for the player.
        travel_turns: The travel time from town to each dungeon.

    Examples:
        ```python
        from osrlib.crawl.adventure import TownSpec

        threshold = TownSpec(name="Threshold", services=("temple", "smith"), travel_turns={"crypt": 1})
        print(threshold.travel_turns["crypt"])
        # 1
        ```
    """

    model_config = ConfigDict(frozen=True)

    name: str
    """The town's name, to show the player, like `"Threshold"`."""
    description: str = ""
    """Text to show the player while the party is in town."""
    services: tuple[str, ...] = ()
    """The services the town offers, as free-form strings like `("temple", "smith")`. osrlib doesn't
    read them: the shop and temple commands work in any town. The session passes them to the player view as
    [`PlayerView.town_services`][osrlib.crawl.views.PlayerView.town_services], so you can list them on
    a town screen."""
    travel_turns: dict[str, int] = {}
    """How long the trip from town to each dungeon's entrance takes, in exploration turns, keyed by
    dungeon id. [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] and
    [`TravelToTown`][osrlib.crawl.commands.TravelToTown] each advance the clock by this many turns,
    so on the way to a distant dungeon and back, light sources burn down and the session uses up the
    party's rations at each day boundary. A trip to a dungeon with no entry here takes no time.
    Every key has to be the id of a dungeon in this adventure, and
    [`validate_adventure`][osrlib.crawl.adventure.validate_adventure] reports one that isn't."""


class PartySpec(BaseModel):
    """The party an adventure is written for: the character levels, and how many characters.

    A published adventure states this on its cover or in its introduction, like "for 6 to 8
    characters of levels 1 to 3". osrlib never reads it. It's there so you can pick a party that
    suits the adventure, in a front end, a converter, or a playtest.

    Attributes:
        min_level: The lowest character level the adventure is written for.
        max_level: The highest.
        min_size: The fewest characters, or `None` when the adventure gives no number.
        max_size: The most characters, or `None` when the adventure gives no number.

    Raises:
        ValueError: If `max_level` is below `min_level`, or `max_size` is below `min_size` when
            both are given.

    Examples:
        ```python
        from osrlib.crawl.adventure import PartySpec

        party = PartySpec(min_level=1, max_level=3, min_size=6, max_size=8)
        print(party.max_level)
        # 3
        ```
    """

    model_config = ConfigDict(frozen=True)

    min_level: int = Field(ge=1)
    """The lowest character level the adventure is written for, 1 or more."""
    max_level: int = Field(ge=1)
    """The highest character level the adventure is written for. Equal to `min_level` for an
    adventure written for one level."""
    min_size: int | None = Field(default=None, ge=1)
    """The fewest characters the adventure is written for, or `None` when it gives no number."""
    max_size: int | None = Field(default=None, ge=1)
    """The most characters the adventure is written for, or `None` when it gives no number."""

    @model_validator(mode="after")
    def _ranges_in_order(self) -> PartySpec:
        if self.max_level < self.min_level:
            raise ValueError("max_level must not be below min_level")
        if self.min_size is not None and self.max_size is not None and self.max_size < self.min_size:
            raise ValueError("max_size must not be below min_size")
        return self


class Adventure(BaseModel):
    """An adventure: one or more dungeons, the base town, and everything they need.

    This is the root of the content tree and the document a session plays. Build the levels first,
    put them in [`DungeonSpec`][osrlib.crawl.dungeon.DungeonSpec]s, write a
    [`TownSpec`][osrlib.crawl.adventure.TownSpec], and construct the adventure from them. Then call
    [`GameSession.new`][osrlib.crawl.session.GameSession.new] with a
    [`Party`][osrlib.crawl.party.Party] and the adventure. It validates the adventure, gives each
    party member an entity id, combines the built-in catalogs with the templates the adventure
    bundles, sets up the state of each quest, and returns a session with the party in town. The
    adventure is frozen, and it stays as you wrote it for the life of the session.

    Every model in the tree is a pydantic model with keyword arguments, so you can write an
    adventure as Python calls, generate it from your own file format, or round-trip it through
    JSON with pydantic's `model_dump_json` and `model_validate_json`. osrlib has no function that
    reads an adventure from a file.

    Attributes:
        name: The adventure's title.
        description: Text describing the adventure.
        hooks: Reasons a party might take the adventure on.
        party: The party the adventure is written for, if it gives one.
        town: The base town.
        dungeons: The dungeons, at least one.
        monsters: Monster templates the adventure adds.
        items: Item templates the adventure adds.
        triggers: What happens when something happens in play.
        quests: What the party is trying to do.

    Raises:
        ValueError: If two dungeons have the same `id`.

    Examples:
        ```python
        from osrlib.crawl.adventure import Adventure, TownSpec, validate_adventure
        from osrlib.crawl.dungeon import DungeonSpec, Edge, EdgeKind, LevelSpec
        from osrlib.data import load_equipment, load_monsters

        # Two cells joined west to east, entered at the west end.
        corridor = LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)})
        crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(corridor,))
        adventure = Adventure(
            name="A First Delve",
            town=TownSpec(name="Threshold", travel_turns={"crypt": 1}),
            dungeons=(crypt,),
        )
        validate_adventure(adventure, load_monsters(), load_equipment())

        print(adventure.dungeon("crypt").level(1).entrance)
        # (0, 0)
        ```
    """

    model_config = ConfigDict(frozen=True)

    name: str
    """The adventure's title, to show the player."""
    description: str = ""
    """Text describing the adventure, to show the player."""
    hooks: tuple[str, ...] = ()
    """The reasons a party might take the adventure on, as free-form strings, like a rumour in the
    tavern or a patron's offer. osrlib never reads them. They let the adventure document include its
    pitch."""
    party: PartySpec | None = None
    """The levels and number of characters the adventure is written for, or `None` when it
    doesn't give one. osrlib never reads it. See [`PartySpec`][osrlib.crawl.adventure.PartySpec]."""
    town: TownSpec
    """The base town. There is exactly one, and the session starts there. See
    [`TownSpec`][osrlib.crawl.adventure.TownSpec]."""
    dungeons: tuple[DungeonSpec, ...] = Field(min_length=1)
    """The dungeons, at least one, each with a unique id. One level of each dungeon needs an
    `entrance`, because that is where the party arrives from town."""
    monsters: tuple[MonsterTemplate, ...] = ()
    """[`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate]s the adventure adds to the built-in
    catalog. In the sessions that play this adventure, they work everywhere a monster template id
    does: keyed encounters, [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters], wandering tables
    on a level, and listening at doors.

    A bundled id can't be the same as a built-in id or another bundled id.
    [`validate_adventure`][osrlib.crawl.adventure.validate_adventure] reports a repeated id instead
    of letting one template replace the other, because the template id in a
    session's events has to refer to one monster. The guide
    [Authoring custom classes, spells, monsters, and items](https://mmacy.github.io/osrlib-python/guides/authoring-custom-content/)
    covers writing a monster template."""
    items: tuple[ItemTemplate, ...] = ()
    """[`ItemTemplate`][osrlib.core.items.ItemTemplate]s the adventure adds to the built-in equipment
    catalog: weapons, armour, gear, and ammunition. In the sessions that play this adventure, they
    work everywhere an authored item id does: treasure caches, `has_item` gates,
    [`GrantItem`][osrlib.crawl.commands.GrantItem], and picking up a drop pile. An item instance
    includes its template, so a bundled item can be given, equipped, and dropped like any other.

    A bundled id can't be the same as an id in the equipment catalog, the magic-item catalog, or
    another bundled item, because one item id has to mean one thing in a session. The town shop
    doesn't sell bundled items. It sells only the built-in equipment."""
    triggers: tuple[TriggerSpec, ...] = ()
    """The adventure's [`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec]s: what happens when
    something happens in play. Triggers that match the same event fire in the order of this tuple.

    Triggers do nothing by themselves. To play them, register an
    [`Interpreter`][osrlib.crawl.interpreter.Interpreter] on the session. An adventure with no
    triggers plays the same whether or not one is registered. See the guide
    [Gates, triggers, and quests](https://mmacy.github.io/osrlib-python/guides/gates-triggers-quests/)."""
    quests: tuple[QuestSpec, ...] = ()
    """The adventure's [`QuestSpec`][osrlib.crawl.quests.QuestSpec]s: what the party is trying to do.
    When [`GameSession.new`][osrlib.crawl.session.GameSession.new] creates the session, it sets up
    the state of each quest in this order, and it goes
    through the quests in this order whenever it checks them.

    The session keeps quest state apart from trigger state, so quest ids and trigger ids don't
    clash, and a quest can have the same id as a trigger."""

    @model_validator(mode="after")
    def _dungeon_ids_unique(self) -> Adventure:
        ids = [dungeon.id for dungeon in self.dungeons]
        if len(set(ids)) != len(ids):
            raise ValueError("dungeon ids must be unique")
        return self

    def dungeon(self, dungeon_id: str) -> DungeonSpec:
        """Return the dungeon with `dungeon_id`.

        Use it to look up the dungeon that an id from a command, an event, or a
        [`PartyLocation`][osrlib.crawl.dungeon.PartyLocation] refers to. Then call
        [`DungeonSpec.level`][osrlib.crawl.dungeon.DungeonSpec.level] to get a level.

        Args:
            dungeon_id: The dungeon's `id`.

        Returns:
            The dungeon.

        Raises:
            ValueError: If no dungeon has that id. The message includes the id.
        """
        for dungeon in self.dungeons:
            if dungeon.id == dungeon_id:
                return dungeon
        raise ValueError(f"unknown dungeon id {dungeon_id!r}")

    def quest(self, quest_id: str) -> QuestSpec:
        """Return the quest with `quest_id`.

        Call it to show a quest's objectives and rewards next to the state the session keeps for it.
        The quest commands look up quest ids with this method, so the session refuses a quest command
        with an id that this method can't find.

        Args:
            quest_id: The quest's `id`.

        Returns:
            The quest.

        Raises:
            ValueError: If no quest has that id. The message includes the id.
        """
        for quest in self.quests:
            if quest.id == quest_id:
                return quest
        raise ValueError(f"unknown quest id {quest_id!r}")


def _effective_monsters(adventure: Adventure, base: MonsterCatalog) -> tuple[MonsterCatalog, tuple[str, ...]]:
    """Compose the base monster catalog with the adventure's bundled templates, first occurrence winning.

    Always returns a usable catalog plus the ids it skipped, so both callers, `validate_adventure` and
    `GameSession`, get a whole answer, and each turns a non-empty collision list into the failure type
    it needs. An empty bundle returns the base catalog object itself, so an adventure that bundles
    nothing copies nothing.
    """
    if not adventure.monsters:
        return base, ()
    seen = {template.id for template in base.monsters}
    accepted: list[MonsterTemplate] = []
    colliding: list[str] = []
    for template in adventure.monsters:
        if template.id in seen:
            colliding.append(template.id)
            continue
        seen.add(template.id)
        accepted.append(template)
    return MonsterCatalog(monsters=(*base.monsters, *accepted)), tuple(colliding)


def _effective_equipment(adventure: Adventure, base: EquipmentCatalog) -> tuple[EquipmentCatalog, tuple[str, ...]]:
    """Compose the base equipment catalog with the adventure's bundled templates, first occurrence winning.

    Works like `_effective_monsters`, with one wider rule: an item id refers to one thing per session, so a
    bundled id collides with the built-in magic-item ids as well as with the four equipment lists and
    the rest of the bundle. The function skips collisions before it builds the catalog rather than
    after, because `EquipmentCatalog`'s uniqueness validator raises a bare `ValueError` and a content
    problem needs a typed one. `treasure_weights` is an encumbrance table rather than item identity,
    so it passes through untouched and its ids fall outside the rule. An empty bundle returns the base
    catalog object itself, so an adventure that bundles nothing copies nothing.
    """
    if not adventure.items:
        return base, ()
    seen = {template.id for template in (*base.weapons, *base.armour, *base.gear, *base.ammunition)}
    seen.update(template.id for template in load_magic_items().items)
    weapons: list[WeaponTemplate] = []
    armour: list[ArmourTemplate] = []
    gear: list[GearTemplate] = []
    ammunition: list[AmmunitionTemplate] = []
    colliding: list[str] = []
    for template in adventure.items:
        if template.id in seen:
            colliding.append(template.id)
            continue
        seen.add(template.id)
        if isinstance(template, WeaponTemplate):
            weapons.append(template)
        elif isinstance(template, ArmourTemplate):
            armour.append(template)
        elif isinstance(template, GearTemplate):
            gear.append(template)
        else:
            ammunition.append(template)
    effective = EquipmentCatalog(
        weapons=(*base.weapons, *weapons),
        armour=(*base.armour, *armour),
        gear=(*base.gear, *gear),
        ammunition=(*base.ammunition, *ammunition),
        treasure_weights=base.treasure_weights,
    )
    return effective, tuple(colliding)


def _area_has_boundary_door(area: AreaSpec, level: LevelSpec) -> bool:
    """Return whether any edge of any of `area`'s cells is a door.

    This is what an `open`-trigger room trap needs before it can ever fire: the springing action is
    a door of the area swinging, so the engine never rolls for it in an area with no door edge. Every edge of every
    cell counts, interior or exterior, read through
    [`LevelSpec.edge`][osrlib.crawl.dungeon.LevelSpec.edge] so the wall-or-door answer matches the one
    the engine gets at play time. A door that `starts_open` counts too, because a referee command or
    a trigger can shut it later and the trap needs only one opening to spring.
    """
    return any(level.edge(cell, direction).kind is EdgeKind.DOOR for cell in area.cells for direction in Direction)


def _trap_crosses_a_passage(trap: TrapSpec | None) -> bool:
    """Return whether a trap's effect moves its victim through a passage.

    A trap's transition is a forced relocation, and a passage is something the party walks through
    by choice, so validation refuses the two together.
    """
    return trap is not None and trap.effect.transition is not None and trap.effect.transition.kind == "passage"


def _passage_far_cells(adventure: Adventure) -> set[tuple[str, int, Position]]:
    """Return the far cell of every passage in the adventure, as `(dungeon id, level number, cell)`.

    A passage's far cell is the cell one step across its side. Only passages that stand on the grid
    and whose far cell is on the grid count, because other checks already reject the rest.
    """
    far_cells: set[tuple[str, int, Position]] = set()
    for dungeon in adventure.dungeons:
        for level in dungeon.levels:
            for transition in level.transitions:
                if transition.kind != "passage" or transition.side is None:
                    continue
                far = step(transition.position, transition.side)
                if level.in_bounds(transition.position) and level.in_bounds(far):
                    far_cells.add((dungeon.id, level.number, far))
    return far_cells


def _validate_passage(
    passage: TransitionSpec,
    side: Direction,
    dungeon_id: str,
    level: LevelSpec,
    owner: str,
    far_cells: set[tuple[str, int, Position]],
    errors: list[str],
) -> None:
    """Check the rules a passage's crossing depends on.

    The passage's side has to be a wall. Its far cell has to be on the grid, outside every area, and
    walled on every other side, so the party can never reach it and sight never passes through it.
    The geometry checks run only for a passage that stands on the grid, since one that doesn't has
    already been refused for that. The destination can't be the cell the passage stands on, and it can't be
    any passage's far cell, which is walled in on every side.
    """
    site = f"{owner}: passage at {passage.position} {side.value}"
    if level.in_bounds(passage.position):
        if level.edge(passage.position, side).kind is not EdgeKind.WALL:
            errors.append(f"{site} stands on an edge that is not a wall")
        far = step(passage.position, side)
        if not level.in_bounds(far):
            errors.append(f"{site} has its far cell {far} off the grid")
        else:
            area = level.area_at(far)
            if area is not None:
                errors.append(f"{site} has its far cell {far} in area {area.id!r}")
            for direction in Direction:
                if direction is not side.opposite and level.edge(far, direction).kind is not EdgeKind.WALL:
                    errors.append(f"{site} has a {direction.value} edge on its far cell {far} that is not a wall")
    destination = (passage.to_dungeon_id, passage.to_level_number, passage.to_position)
    if destination == (dungeon_id, level.number, passage.position):
        errors.append(f"{site} lands on its own cell")
    elif destination in far_cells:
        errors.append(
            f"{site} lands on {passage.to_dungeon_id} level {passage.to_level_number} {passage.to_position}, "
            "the far cell of a passage"
        )


def _validate_feature(
    feature: FeatureSpec,
    level: LevelSpec,
    owner: str,
    equipment: EquipmentCatalog,
    magic: MagicItemCatalog,
    errors: list[str],
) -> None:
    if feature.cell is not None and not level.in_bounds(feature.cell):
        errors.append(f"{owner}: feature {feature.id!r} cell {feature.cell} is out of bounds")
    if _trap_crosses_a_passage(feature.trap):
        errors.append(f"{owner}: feature {feature.id!r} has a trap whose transition is a passage")
    for item_id in feature.item_ids:
        try:
            equipment.get(item_id)
        except ValueError:
            errors.append(f"{owner}: feature {feature.id!r} references unknown item {item_id!r}")
    for item_id in feature.magic_item_ids:
        try:
            magic.get(item_id)
        except ValueError:
            errors.append(f"{owner}: feature {feature.id!r} references unknown magic item {item_id!r}")


def _dangling_condition_item(
    condition: ConditionSpec, equipment: EquipmentCatalog, magic: MagicItemCatalog
) -> str | None:
    """Return the condition's item id when no item the party could carry has that id, else `None`.

    The domain is the composed equipment catalog and the magic-item catalog, which is exactly what
    `has_item` evaluates against, so a condition on an id in neither catalog can never be satisfied
    and is a dangling reference. It returns `None` for every other condition kind: flag keys and
    effect kinds are open domains and get no check, because a flag that is never written is
    something an authoring tool warns about rather than a broken document. Validation resolves gates
    and bare trigger conditions through this one helper, so the domain is the same for both.
    """
    if not isinstance(condition, HasItemCondition):
        return None
    try:
        equipment.get(condition.item_id)
    except ValueError:
        try:
            magic.get(condition.item_id)
        except ValueError:
            return condition.item_id
    return None


def _validate_gate(
    gate: GateSpec | None,
    owner: str,
    site: str,
    equipment: EquipmentCatalog,
    magic: MagicItemCatalog,
    errors: list[str],
) -> None:
    """Resolve a gate's `has_item` id against the item domain that condition matches."""
    if gate is None:
        return
    dangling = _dangling_condition_item(gate.condition, equipment, magic)
    if dangling is not None:
        errors.append(f"{owner}: {site} gate references unknown item {dangling!r}")


def _resolve_level(adventure: Adventure, dungeon_id: str, level_number: int) -> LevelSpec | None:
    """Return the level that a dungeon id and level number refer to, or `None` when either doesn't resolve."""
    try:
        return adventure.dungeon(dungeon_id).level(level_number)
    except ValueError:
        return None


def _validate_clause(
    pattern: TriggerPattern,
    conditions: tuple[ConditionSpec, ...],
    owner: str,
    adventure: Adventure,
    monsters: MonsterCatalog,
    equipment: EquipmentCatalog,
    magic: MagicItemCatalog,
    errors: list[str],
) -> None:
    """Resolve one matching clause's pattern and condition references.

    Every clause in a document goes through this function: a trigger's `when` and `conditions`, a
    quest's activation, an objective's completion, and a hidden objective's reveal. Because triggers
    and quests share it, the same references resolve for both. `owner` is the subject of the error
    lines, like `trigger 'lever-east'` or `quest 'the-idol' objective 'return-home'`.

    Flag keys stay unchecked at every site. The flag namespace is open by design, so a key that is
    never written is something an authoring tool warns about rather than a broken document.
    """
    if isinstance(pattern, AreaEnteredPattern | LevelEnteredPattern):
        level = _resolve_level(adventure, pattern.dungeon_id, pattern.level_number)
        if level is None:
            errors.append(f"{owner}: pattern references unknown {pattern.dungeon_id!r} level {pattern.level_number}")
        elif isinstance(pattern, AreaEnteredPattern) and not any(area.id == pattern.area_id for area in level.areas):
            errors.append(
                f"{owner}: pattern references unknown area {pattern.area_id!r} "
                f"on {pattern.dungeon_id!r} level {pattern.level_number}"
            )
    elif isinstance(pattern, DungeonEnteredPattern):
        try:
            adventure.dungeon(pattern.dungeon_id)
        except ValueError:
            errors.append(f"{owner}: pattern references unknown dungeon {pattern.dungeon_id!r}")
    elif isinstance(pattern, ItemAcquiredPattern):
        if _dangling_condition_item(HasItemCondition(item_id=pattern.item_id), equipment, magic) is not None:
            errors.append(f"{owner}: pattern references unknown item {pattern.item_id!r}")
    elif isinstance(pattern, MonsterDefeatedPattern):
        try:
            monsters.get(pattern.template_id)
        except ValueError:
            errors.append(f"{owner}: pattern references unknown monster {pattern.template_id!r}")
    for condition in conditions:
        dangling = _dangling_condition_item(condition, equipment, magic)
        if dangling is not None:
            errors.append(f"{owner}: condition references unknown item {dangling!r}")


def _validate_consequence(
    consequence: ConsequenceCommand,
    site: str,
    adventure: Adventure,
    monsters: MonsterCatalog,
    equipment: EquipmentCatalog,
    magic: MagicItemCatalog,
    errors: list[str],
) -> None:
    """Resolve one authored consequence's references and its character addressing.

    Every consequence in a document goes through this function, a trigger's consequences and a
    quest's rewards alike. `site` is the subject at the start of each error line, like
    `trigger 'reward': consequence 0` or `quest 'the-idol': reward 0`.
    """
    if isinstance(consequence, GrantItem | GrantCoins | AwardXP | RemoveItem | RemoveCoins):
        # Character ids are allocated per session, so a document can never contain one:
        # authored consequences address the party through the selectors. Only a removal of
        # an item has a carrier to look for.
        selectors = (PARTY_SELECTOR, FIRST_LIVING_SELECTOR)
        if isinstance(consequence, RemoveItem):
            selectors = (*selectors, CARRIER_SELECTOR)
        if consequence.character_id not in selectors:
            errors.append(
                f"{site} names character {consequence.character_id!r}; an authored "
                f"{consequence.command_type} addresses {' or '.join(repr(selector) for selector in selectors)}"
            )
    if isinstance(consequence, GrantItem | RemoveItem):
        if _dangling_condition_item(HasItemCondition(item_id=consequence.item_id), equipment, magic) is not None:
            errors.append(f"{site} references unknown item {consequence.item_id!r}")
    elif isinstance(consequence, SpawnMonsters):
        try:
            monsters.get(consequence.template_id)
        except ValueError:
            errors.append(f"{site} references unknown monster {consequence.template_id!r}")
    elif isinstance(consequence, SetDoorState):
        level = _resolve_level(adventure, consequence.dungeon_id, consequence.level_number)
        if level is None:
            errors.append(f"{site} references unknown {consequence.dungeon_id!r} level {consequence.level_number}")
        elif level.edge((consequence.x, consequence.y), consequence.direction).kind is not EdgeKind.DOOR:
            errors.append(f"{site} names no door at ({consequence.x}, {consequence.y}) {consequence.direction.value}")
    elif isinstance(consequence, PlaceParty):
        # A town placement refers to the adventure's one town and needs no check, and the
        # location model guarantees a dungeon location's fields are set together.
        location = consequence.location
        if location.dungeon_id is None or location.level_number is None:
            return
        level = _resolve_level(adventure, location.dungeon_id, location.level_number)
        if level is None:
            errors.append(f"{site} references unknown {location.dungeon_id!r} level {location.level_number}")
        elif location.position is not None and not level.in_bounds(location.position):
            errors.append(f"{site} places the party out of bounds at {location.position}")


def _validate_trigger(
    trigger: TriggerSpec,
    adventure: Adventure,
    monsters: MonsterCatalog,
    equipment: EquipmentCatalog,
    magic: MagicItemCatalog,
    errors: list[str],
) -> None:
    """Resolve one trigger's pattern, condition, and consequence references."""
    owner = f"trigger {trigger.id!r}"
    _validate_clause(trigger.when, trigger.conditions, owner, adventure, monsters, equipment, magic, errors)
    for position, consequence in enumerate(trigger.consequences):
        _validate_consequence(
            consequence, f"{owner}: consequence {position}", adventure, monsters, equipment, magic, errors
        )


def _validate_quest(
    quest: QuestSpec,
    adventure: Adventure,
    monsters: MonsterCatalog,
    equipment: EquipmentCatalog,
    magic: MagicItemCatalog,
    errors: list[str],
) -> None:
    """Resolve one quest's clause and reward references, clause by clause.

    Every clause in a quest goes through the shared clause check: the activation, each objective's
    completion, and each hidden objective's reveal. The reveal is labelled apart from the completion
    so an error line shows which of the two is unresolved. Rewards go through the shared consequence check, so a
    quest's reward and a trigger's consequence are held to the same references and the same
    party-selector rule.
    """
    owner = f"quest {quest.id!r}"
    if quest.activation is not None:
        _validate_clause(
            quest.activation.pattern, quest.activation.conditions, owner, adventure, monsters, equipment, magic, errors
        )
    for objective in quest.objectives:
        site = f"{owner} objective {objective.id!r}"
        _validate_clause(
            objective.when.pattern, objective.when.conditions, site, adventure, monsters, equipment, magic, errors
        )
        if objective.reveal_when is not None:
            _validate_clause(
                objective.reveal_when.pattern,
                objective.reveal_when.conditions,
                f"{site} reveal",
                adventure,
                monsters,
                equipment,
                magic,
                errors,
            )
    for position, reward in enumerate(quest.rewards):
        _validate_consequence(reward, f"{owner}: reward {position}", adventure, monsters, equipment, magic, errors)


def validate_adventure(adventure: Adventure, monsters: MonsterCatalog, equipment: EquipmentCatalog) -> None:
    """Check that every id in an adventure refers to something that exists.

    Call it while you write an adventure, as soon as you have one to check. It follows the references
    in the tree and raises once, with a list of everything wrong, so you can fix the whole document
    in one pass. [`GameSession.new`][osrlib.crawl.session.GameSession.new] calls it too, so a session
    doesn't start on content that fails these checks. It changes nothing, and it returns `None` when
    the adventure passes.

    For the whole adventure, it checks that:

    - Bundled monster ids are unique and aren't in `monsters`.
    - Bundled item ids are unique and aren't in `equipment` or the built-in magic-item catalog.
    - Each dungeon id in the town's `travel_turns` is a dungeon of the adventure.
    - Each dungeon has at least one level whose `entrance` is set.

    For each level, it checks that:

    - Feature ids are unique across the level, and none is `"pile"`, which is reserved for drop
      piles.
    - The entrance is on the grid.
    - Area ids are unique, and no area has the same id as a feature. The session records found,
      sprung, and removed traps by area id and feature id in the same way, so it would confuse the
      two.
    - Every area cell is on the grid.
    - An area whose room trap has `trigger="open"` has a door on at least one edge of its cells. A
      door that starts open counts. A trap with `trigger="enter"` needs no door.
    - No area trap and no feature trap moves the party through a passage.
    - Each monster id in a keyed encounter resolves, and an encounter's fixed `alignment` is one the
      monster's template allows.
    - Every feature cell is on the grid, and the ids in each feature's `item_ids` and
      `magic_item_ids` resolve.
    - Every feature listed on the level itself, not on an area, has a cell.
    - The monster ids in the level's `wandering.table`, when it has one, resolve.
    - The item id of each `has_item` gate on a door or a transition resolves.
    - Each transition stands on the grid and leads to a cell on the grid of a level that exists.
    - Each passage stands on a wall. Its far cell, the cell one step across that wall, is on the
      grid, outside every area, and walled on its other three sides. The passage doesn't lead to
      the cell it stands on or to any passage's far cell, and no two passages share a cell and side.

    For each trigger, it checks that the id is unique, and that the area, level, dungeon, item, or
    monster its pattern refers to exists. For each of the trigger's consequences, it checks that a
    granted or removed item resolves in the equipment catalog or the magic item catalog, that a spawned
    monster resolves, that a door-state change refers to an edge where a door stands, and that a party
    placement is on the grid. For each quest, it checks that the id is
    unique, and runs the same checks over each of its clauses (its activation, each objective's
    completion, and each hidden objective's reveal) and over each reward.

    A consequence or reward that gives something to a character or takes something away has to
    address the party with a selector, not a character id. Every such command accepts `"@party"` and
    `"@first"`, and a [`RemoveItem`][osrlib.crawl.commands.RemoveItem] also accepts `"@carrier"`.
    [`GameSession.new`][osrlib.crawl.session.GameSession.new] gives out character ids, so no
    character id written in an adventure can refer to a character.

    It doesn't check flag keys. Any key can be set during play, so an unset key isn't an error in the
    document.

    You don't pass a magic-item catalog. You can't bundle magic items in an adventure, so the function
    loads the built-in one with [`load_magic_items`][osrlib.data.load_magic_items].

    It checks quest ids and trigger ids separately, so a quest can have the same id as a trigger.

    Args:
        adventure: The adventure to check.
        monsters: The base monster catalog, usually [`load_monsters`][osrlib.data.load_monsters]`()`.
            The function combines it with the adventure's bundled monsters, and resolves every
            monster id against the result. Pass the base catalog, not one you have already combined
            with the bundle, or every bundled monster is reported as a repeated id.
        equipment: The base equipment catalog, usually
            [`load_equipment`][osrlib.data.load_equipment]`()`. The function combines it with the
            adventure's bundled items in the same way.

    Raises:
        ContentValidationError: If any check fails. The message starts with
            `adventure validation failed:`, then lists each problem on a separate line with where it is,
            like the dungeon, the level, and the object. Fix the adventure and call again. The same
            adventure always fails with the same message.

    Examples:
        ```python
        from osrlib.crawl.adventure import Adventure, TownSpec, validate_adventure
        from osrlib.crawl.dungeon import AreaSpec, DungeonSpec, KeyedEncounter, KeyedMonster, LevelSpec
        from osrlib.data import load_equipment, load_monsters
        from osrlib.errors import ContentValidationError

        hall = AreaSpec(
            id="hall",
            cells=((0, 0),),
            encounter=KeyedEncounter(monsters=(KeyedMonster(template_id="grue", count_fixed=1),)),
        )
        level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0), areas=(hall,))
        broken = Adventure(
            name="A First Delve",
            town=TownSpec(name="Threshold"),
            dungeons=(DungeonSpec(id="crypt", levels=(level,)),),
        )
        try:
            validate_adventure(broken, load_monsters(), load_equipment())
        except ContentValidationError as error:
            print(error)
        # adventure validation failed:
        # crypt level 1: area 'hall' references unknown monster 'grue'
        ```
    """
    errors: list[str] = []
    magic = load_magic_items()
    effective, colliding = _effective_monsters(adventure, monsters)
    for monster_id in colliding:
        errors.append(f"bundled monster id {monster_id!r} collides with the catalog")
    effective_items, colliding_items = _effective_equipment(adventure, equipment)
    for item_id in colliding_items:
        errors.append(f"bundled item id {item_id!r} collides with the catalog")
    for dungeon_id in adventure.town.travel_turns:
        if not any(dungeon.id == dungeon_id for dungeon in adventure.dungeons):
            errors.append(f"town travel names unknown dungeon {dungeon_id!r}")
    far_cells = _passage_far_cells(adventure)
    for dungeon in adventure.dungeons:
        if not any(level.entrance is not None for level in dungeon.levels):
            errors.append(f"dungeon {dungeon.id!r} has no entrance on any level")
        for level in dungeon.levels:
            owner = f"{dungeon.id} level {level.number}"
            feature_ids = [feature.id for feature in level.features]
            for area in level.areas:
                feature_ids.extend(feature.id for feature in area.features)
            if len(set(feature_ids)) != len(feature_ids):
                errors.append(f"{owner}: feature ids are not unique")
            if "pile" in feature_ids:
                # `TakeTreasure(feature_id="pile")` targets the cell's drop pile. The name is
                # reserved so that no authored feature id can collide with it.
                errors.append(f"{owner}: feature id 'pile' is reserved for drop piles")
            if level.entrance is not None and not level.in_bounds(level.entrance):
                errors.append(f"{owner}: entrance {level.entrance} is out of bounds")
            area_ids = [area.id for area in level.areas]
            if len(set(area_ids)) != len(area_ids):
                errors.append(f"{owner}: area ids are not unique")
            feature_id_set = set(feature_ids)
            for area_id in area_ids:
                if area_id in feature_id_set:
                    errors.append(f"{owner}: id {area_id!r} names both an area and a feature")
            for area in level.areas:
                for cell in area.cells:
                    if not level.in_bounds(cell):
                        errors.append(f"{owner}: area {area.id!r} cell {cell} is out of bounds")
                if area.trap is not None and area.trap.trigger == "open" and not _area_has_boundary_door(area, level):
                    errors.append(f"{owner}: area {area.id!r} has an open-trigger trap and no door on its boundary")
                if _trap_crosses_a_passage(area.trap):
                    errors.append(f"{owner}: area {area.id!r} has a trap whose transition is a passage")
                if area.encounter is not None:
                    for keyed in area.encounter.monsters:
                        try:
                            template = effective.get(keyed.template_id)
                        except ValueError:
                            errors.append(f"{owner}: area {area.id!r} references unknown monster {keyed.template_id!r}")
                            continue
                        alignment = area.encounter.alignment
                        if alignment is not None and alignment not in template.alignment.options:
                            errors.append(
                                f"{owner}: area {area.id!r} pins alignment {alignment.value!r} "
                                f"outside {keyed.template_id!r}'s options"
                            )
                for feature in area.features:
                    _validate_feature(feature, level, owner, effective_items, magic, errors)
            for feature in level.features:
                if feature.cell is None:
                    errors.append(f"{owner}: level-scope feature {feature.id!r} needs a cell")
                _validate_feature(feature, level, owner, effective_items, magic, errors)
            if level.wandering.table is not None:
                for row in level.wandering.table.rows:
                    if row.entry.kind != "monster":
                        continue
                    for monster_id in row.entry.monster_ids:
                        try:
                            effective.get(monster_id)
                        except ValueError:
                            errors.append(
                                f"{owner}: wandering row {row.name!r} references unknown monster {monster_id!r}"
                            )
            for edge_key_, edge in level.edges.items():
                if edge.door is not None:
                    _validate_gate(edge.door.requires, owner, f"door {edge_key_}", effective_items, magic, errors)
            passage_sides: set[tuple[Position, Direction]] = set()
            doubled_sides: set[tuple[Position, Direction]] = set()
            for transition in level.transitions:
                _validate_gate(
                    transition.requires, owner, f"transition at {transition.position}", effective_items, magic, errors
                )
                if not level.in_bounds(transition.position):
                    errors.append(f"{owner}: transition at {transition.position} is out of bounds")
                target = _resolve_level(adventure, transition.to_dungeon_id, transition.to_level_number)
                if target is None:
                    errors.append(
                        f"{owner}: transition targets unknown "
                        f"{transition.to_dungeon_id!r} level {transition.to_level_number}"
                    )
                elif not target.in_bounds(transition.to_position):
                    errors.append(f"{owner}: transition target cell {transition.to_position} is out of bounds")
                if transition.kind != "passage" or transition.side is None:
                    continue
                _validate_passage(transition, transition.side, dungeon.id, level, owner, far_cells, errors)
                cell_side = (transition.position, transition.side)
                if cell_side in passage_sides and cell_side not in doubled_sides:
                    doubled_sides.add(cell_side)
                    errors.append(f"{owner}: two passages stand at {transition.position} {transition.side.value}")
                passage_sides.add(cell_side)
    seen_triggers: set[str] = set()
    for trigger in adventure.triggers:
        if trigger.id in seen_triggers:
            errors.append(f"trigger {trigger.id!r}: id is not unique")
        seen_triggers.add(trigger.id)
        _validate_trigger(trigger, adventure, effective, effective_items, magic, errors)
    seen_quests: set[str] = set()
    for quest in adventure.quests:
        if quest.id in seen_quests:
            errors.append(f"quest {quest.id!r}: id is not unique")
        seen_quests.add(quest.id)
        _validate_quest(quest, adventure, effective, effective_items, magic, errors)
    if errors:
        raise ContentValidationError("adventure validation failed:\n" + "\n".join(errors))
