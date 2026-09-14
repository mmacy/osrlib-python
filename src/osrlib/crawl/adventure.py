"""The adventure: the root document a session plays.

An [`Adventure`][osrlib.crawl.adventure.Adventure] is everything a game needs to run except the party
and the dice. You build the geometry in [`osrlib.crawl.dungeon`][osrlib.crawl.dungeon], wrap the
levels in dungeons, add a [`TownSpec`][osrlib.crawl.adventure.TownSpec] for the party to come home
to, and assemble the two here. Then you hand the result to
[`GameSession.new`][osrlib.crawl.session.GameSession.new] beside a
[`Party`][osrlib.crawl.party.Party], and the session runs it.

The adventure is frozen. The session reads it and never writes back: everything play changes goes
into [`DungeonState`][osrlib.crawl.dungeon.DungeonState] instead. That split is what lets a save file
carry the overlay alone, and it is why loading a save against the same adventure gives you the same
game. Prose lives in these models rather than in events, because events carry ids and your front end
resolves them against the adventure it already has.

Besides its dungeons, an adventure contains its own content and behavior. `monsters` and `items` bundle
templates that resolve beside the shipped catalogs for the sessions that run this adventure.
`triggers` ([`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec]) are what fire when something happens,
`quests` ([`QuestSpec`][osrlib.crawl.quests.QuestSpec]) are what the party is trying to accomplish, and
gates ([`GateSpec`][osrlib.crawl.gates.GateSpec]) sit on the doors and transitions of the geometry.

[`validate_adventure`][osrlib.crawl.adventure.validate_adventure] is what tells you the document
hangs together before anybody plays it. It follows every id in the tree to the thing it names and
raises [`ContentValidationError`][osrlib.errors.ContentValidationError] listing everything that
dangles at once. `GameSession.new` runs it for you, so a session can never start on broken content.
Call it yourself while you author and you find out sooner.

The long form, with a complete program you can run, is the guide
[Building an adventure](https://mmacy.github.io/osrlib-python/getting-started/building-an-adventure/).
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
    SetDoorState,
    SpawnMonsters,
)
from osrlib.crawl.dungeon import DungeonSpec, EdgeKind, FeatureSpec, LevelSpec
from osrlib.crawl.gates import ConditionSpec, GateSpec, HasItemCondition
from osrlib.crawl.quests import QuestSpec
from osrlib.crawl.triggers import (
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
    "TownSpec",
    "validate_adventure",
]


class TownSpec(BaseModel):
    """The base town: where the party is safe, buys gear, and comes home to.

    Every adventure has exactly one town, and a session starts there. It is a marker rather than a
    place you can walk around: there is no grid, no rooms, and nothing to explore. What it does is
    anchor the rules that need somewhere safe. The party rests a full day here, buys and sells
    through the equipment catalog, pays a temple for healing, and, under the default XP timing,
    earns the treasure it carried out only once it has come back.

    Attributes:
        name: The town's name.
        description: Prose for your front end.
        services: The services the town offers.
        travel_turns: The travel cost from town to each dungeon.

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
    """The town's name, for your front end to show: `"Threshold"`."""
    description: str = ""
    """Prose your front end shows while the party is in town."""
    services: tuple[str, ...] = ()
    """The services the town offers, as free-form strings like `("temple", "smith")`. Nothing in the
    engine reads them: the shop and the temple commands work in town regardless. They reach your
    front end on the player view's `town_services`, which is what a town screen lists."""
    travel_turns: dict[str, int] = {}
    """How long it takes to get from town to each dungeon's entrance, in exploration turns, keyed by
    dungeon id. [`EnterDungeon`][osrlib.crawl.commands.EnterDungeon] and
    [`TravelToTown`][osrlib.crawl.commands.TravelToTown] each advance the clock by this much, so a
    far-off dungeon costs light and rations to reach and to leave. A dungeon with no entry here
    travels free. Every id named here has to be a dungeon of this adventure, and
    [`validate_adventure`][osrlib.crawl.adventure.validate_adventure] refuses one that is not."""


class Adventure(BaseModel):
    """An adventure: one or more dungeons, the base town, and everything they need.

    This is the root of the content tree and the document a session plays. Build the levels first,
    wrap them in [`DungeonSpec`][osrlib.crawl.dungeon.DungeonSpec]s, write a
    [`TownSpec`][osrlib.crawl.adventure.TownSpec], and assemble them here. Then call
    [`GameSession.new`][osrlib.crawl.session.GameSession.new] with a
    [`Party`][osrlib.crawl.party.Party] and this adventure: it validates the whole tree, assigns the
    party's members their entity ids, composes the shipped catalogs with whatever this adventure
    bundles, seeds a state block for each quest, and hands you a session standing in town. The
    adventure itself is frozen and stays as you wrote it for the life of the session.

    Everything nested here is a pydantic model with a keyword constructor, so an adventure is a tree
    of calls you can write in Python, generate from your own file format, or round-trip through JSON.
    Nothing reads files for you.

    Attributes:
        name: The adventure's title.
        description: Prose for your front end.
        hooks: Why a party might take this on.
        town: The base town.
        dungeons: The dungeons, at least one.
        monsters: Monster templates this adventure brings with it.
        items: Item templates this adventure brings with it.
        triggers: What fires when something happens.
        quests: What the party is trying to accomplish.

    Raises:
        ValueError: If two dungeons carry the same `id`.

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
    """The adventure's title, for your front end to show."""
    description: str = ""
    """Prose describing the adventure, for your front end."""
    hooks: tuple[str, ...] = ()
    """The reasons a party might take this on, as free-form strings: the rumours in the tavern, the
    patron's offer. Nothing in the engine reads them. They are here so an adventure document contains
    its own pitch."""
    town: TownSpec
    """The base town. Exactly one, and the session starts there. See
    [`TownSpec`][osrlib.crawl.adventure.TownSpec]."""
    dungeons: tuple[DungeonSpec, ...] = Field(min_length=1)
    """The dungeons, at least one, with unique ids. Some level of each needs an `entrance`, since
    that is where the party arrives from town."""
    monsters: tuple[MonsterTemplate, ...] = ()
    """[`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate]s this adventure brings with it,
    beyond the shipped catalog. They join that catalog for the sessions that run this adventure,
    everywhere the engine resolves a template id: keyed encounters,
    [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters], inline wandering tables, listen checks.

    A bundled id may not collide with a shipped one or with another bundled one. A collision is a
    validation error rather than an override, because one id has to name one monster for the session
    to be able to say what it spawned. Writing a monster template is covered in the guide
    [Authoring custom classes, spells, monsters, and items](https://mmacy.github.io/osrlib-python/guides/authoring-custom-content/)."""
    items: tuple[ItemTemplate, ...] = ()
    """[`ItemTemplate`][osrlib.core.items.ItemTemplate]s this adventure brings with it: weapons,
    armour, gear, and ammunition. They join the shipped equipment catalog for the sessions that run
    this adventure, everywhere the engine resolves an authored item id, which is treasure caches,
    [`GrantItem`][osrlib.crawl.commands.GrantItem], and drop-pile recovery. Once an instance exists it
    carries its template with it, so a bundled item gives, equips, and drops like any other.

    A bundled id may not collide with the equipment catalog, the magic-item catalog, or another
    bundled item: one item id names one thing per session. The town shop is the one place these do
    not reach, because it stocks the shipped equipment lists."""
    triggers: tuple[TriggerSpec, ...] = ()
    """The adventure's [`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec]s: what happens when
    something happens. The tuple's order is the firing order, so triggers matching one event fire in
    the order you wrote them.

    Triggers do nothing on their own. A game plays them by registering an
    [`Interpreter`][osrlib.crawl.interpreter.Interpreter] on its session, and an adventure with no
    triggers plays the same whether or not one is registered. See the guide
    [Gates, triggers, and quests](https://mmacy.github.io/osrlib-python/guides/gates-triggers-quests/)."""
    quests: tuple[QuestSpec, ...] = ()
    """The adventure's [`QuestSpec`][osrlib.crawl.quests.QuestSpec]s: what the party is trying to
    accomplish. The session seeds one state block per quest when it is constructed, in this order,
    and every walk over them follows it.

    Quest ids and trigger ids live in separate state blocks, so they are separate namespaces and a
    quest may share an id with a trigger."""

    @model_validator(mode="after")
    def _dungeon_ids_unique(self) -> Adventure:
        ids = [dungeon.id for dungeon in self.dungeons]
        if len(set(ids)) != len(ids):
            raise ValueError("dungeon ids must be unique")
        return self

    def dungeon(self, dungeon_id: str) -> DungeonSpec:
        """Return the dungeon with `dungeon_id`.

        Use this to turn a dungeon id out of a command, an event, or a party location back into the
        dungeon it names, rather than searching `dungeons` yourself. From there,
        [`DungeonSpec.level`][osrlib.crawl.dungeon.DungeonSpec.level] gets you the level.

        Args:
            dungeon_id: The dungeon id.

        Returns:
            The dungeon spec.

        Raises:
            ValueError: If no dungeon has that id. The message names the id.
        """
        for dungeon in self.dungeons:
            if dungeon.id == dungeon_id:
                return dungeon
        raise ValueError(f"unknown dungeon id {dungeon_id!r}")

    def quest(self, quest_id: str) -> QuestSpec:
        """Return the quest with `quest_id`.

        This is what the quest lifecycle commands resolve against, which is what makes their id
        domain closed: an id this cannot answer names no quest of this adventure, and the command is
        refused. Call it to show a quest's objectives and rewards beside the state the session keeps
        for it.

        Args:
            quest_id: The quest id.

        Returns:
            The quest spec.

        Raises:
            ValueError: If no quest has that id. The message names the id.
        """
        for quest in self.quests:
            if quest.id == quest_id:
                return quest
        raise ValueError(f"unknown quest id {quest_id!r}")


def _effective_monsters(adventure: Adventure, base: MonsterCatalog) -> tuple[MonsterCatalog, tuple[str, ...]]:
    """Compose the base monster catalog with the adventure's bundled templates, first occurrence winning.

    Always returns a usable catalog plus the ids it skipped, so both callers get a whole answer and
    each turns a non-empty collision list into the failure shape it needs. An empty bundle returns the
    base catalog object itself, so an adventure that bundles nothing copies nothing.
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

    The monster helper's sibling, with one wider rule: an item id names one thing per session, so a
    bundled id collides with the shipped magic-item ids as well as with the four equipment lists and
    the rest of the bundle. Collisions are skipped before the catalog is built rather than after,
    because `EquipmentCatalog`'s own uniqueness validator raises a bare `ValueError` and a content
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
    """Return the condition's item id when it names nothing the party could ever carry, else `None`.

    The domain is the composed equipment catalog and the magic-item catalog, which is exactly what
    `has_item` evaluates against, so an id neither holds can never be satisfied and is a dangling
    reference. Every other condition kind answers `None`: flag keys and effect kinds are open domains
    and get no check, because a flag nobody writes is something an authoring tool warns about rather
    than a broken document. Validation resolves gates and bare trigger conditions through this one
    helper, so the two can never disagree about the domain.
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
    """Return the level a dungeon id and level number name, or `None` when either dangles."""
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

    This is the one body behind every clause in a document: a trigger's `when` and `conditions`, a
    quest's activation, an objective's completion, and a hidden objective's reveal. Sharing it keeps
    the two authoring paths from drifting apart on what resolves. `owner` is the subject the error
    lines name, like `trigger 'lever-east'` or `quest 'the-idol' objective 'return-home'`.

    Flag keys stay unchecked at every site. The flag namespace is open by design, so a key nobody
    writes is something an authoring tool warns about rather than a broken document.
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
    errors: list[str],
) -> None:
    """Resolve one authored consequence's references and its character addressing.

    This is the one body behind every consequence in a document, a trigger's consequences and a
    quest's rewards alike. `site` is the subject the error lines name, like
    `trigger 'reward': consequence 0` or `quest 'the-idol': reward 0`.
    """
    if isinstance(consequence, GrantItem | GrantCoins | AwardXP):
        # Character ids are allocated per session, so a document can never name one:
        # authored consequences address the party through the selectors.
        if consequence.character_id not in (PARTY_SELECTOR, FIRST_LIVING_SELECTOR):
            errors.append(
                f"{site} names character {consequence.character_id!r}; an authored consequence "
                f"addresses {PARTY_SELECTOR!r} or {FIRST_LIVING_SELECTOR!r}"
            )
    if isinstance(consequence, GrantItem):
        try:
            equipment.get(consequence.item_id)
        except ValueError:
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
        # A town placement names the adventure's one town and needs no check, and the
        # location model guarantees a dungeon location's fields travel together.
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
        _validate_consequence(consequence, f"{owner}: consequence {position}", adventure, monsters, equipment, errors)


def _validate_quest(
    quest: QuestSpec,
    adventure: Adventure,
    monsters: MonsterCatalog,
    equipment: EquipmentCatalog,
    magic: MagicItemCatalog,
    errors: list[str],
) -> None:
    """Resolve one quest's clause and reward references, clause by clause.

    Every clause a quest carries walks the shared clause check: the activation, each objective's
    completion, and each hidden objective's reveal. The reveal is named apart from the completion so
    an error line says which of the two dangles. Rewards walk the shared consequence check, so a
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
        _validate_consequence(reward, f"{owner}: reward {position}", adventure, monsters, equipment, errors)


def validate_adventure(adventure: Adventure, monsters: MonsterCatalog, equipment: EquipmentCatalog) -> None:
    """Check that every id in an adventure names something that exists.

    Call this while you author, as soon as you have an adventure to check. It follows every reference
    in the tree and raises once, listing everything wrong, so you fix the whole document in one pass
    instead of finding the next broken id on the next run.
    [`GameSession.new`][osrlib.crawl.session.GameSession.new] runs it too, which is why a session can
    never start on content that would fail partway through a delve. It changes nothing and returns
    nothing, so a clean adventure comes back unchanged.

    It checks the following, roughly in the order the message lists them. Bundled monster ids,
    against the shipped catalog and each other. Bundled item ids, against the equipment catalog, the shipped
    magic-item catalog, and each other. Then, for each level: area cells and feature cells on the
    grid, feature ids unique across the level, cache item ids and magic item ids resolving, keyed
    encounter template ids resolving and any fixed alignment being one the template allows, inline
    wandering-table monster ids resolving, the item ids named by `has_item` gates on doors and
    transitions resolving, transitions landing on real cells of real levels, the town's travel entries
    naming real dungeons, and every dungeon having an entrance on some level. Then, for each trigger:
    its id unique, the area, level, dungeon, item, and monster its pattern names resolving, and for
    each consequence the item it grants, the monster it spawns, a door actually standing at the cell a
    door-state consequence names, and a placement landing on the grid. Then, for each quest: its id unique, and the
    same checks over every clause it has (its activation, each objective's completion, each hidden
    objective's reveal) and every reward it pays.

    One rule is about authoring rather than about a dangling id. A consequence that addresses a
    character has to do so through a party selector, because character ids are allocated when a
    session starts: a document naming one is naming something that cannot exist at the time it is
    read.

    Magic items are the one catalog you do not pass. Adventures bundle no magic items, so validation
    loads the shipped one itself with [`load_magic_items`][osrlib.data.load_magic_items].

    Quest ids and trigger ids are separate namespaces, so nothing here compares them and a quest may
    share an id with a trigger.

    Args:
        adventure: The adventure to check.
        monsters: The base monster catalog, usually [`load_monsters`][osrlib.data.load_monsters]. The
            check composes it with the adventure's bundled templates itself, and every monster
            reference resolves against that union, so pass the shipped catalog rather than one you
            have already merged.
        equipment: The base equipment catalog, usually
            [`load_equipment`][osrlib.data.load_equipment]. Composed with the adventure's bundled
            items the same way.

    Raises:
        ContentValidationError: If anything is wrong. The message lists every problem found, one per
            line, each naming the dungeon, level, and object it sits on.

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
                # `TakeTreasure(feature_id="pile")` targets the cell's drop pile;
                # the name is reserved so authored content can never collide.
                errors.append(f"{owner}: feature id 'pile' is reserved for drop piles")
            if level.entrance is not None and not level.in_bounds(level.entrance):
                errors.append(f"{owner}: entrance {level.entrance} is out of bounds")
            area_ids = [area.id for area in level.areas]
            if len(set(area_ids)) != len(area_ids):
                errors.append(f"{owner}: area ids are not unique")
            for area in level.areas:
                for cell in area.cells:
                    if not level.in_bounds(cell):
                        errors.append(f"{owner}: area {area.id!r} cell {cell} is out of bounds")
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
            for transition in level.transitions:
                _validate_gate(
                    transition.requires, owner, f"transition at {transition.position}", effective_items, magic, errors
                )
                if not level.in_bounds(transition.position):
                    errors.append(f"{owner}: transition at {transition.position} is out of bounds")
                try:
                    target = adventure.dungeon(transition.to_dungeon_id).level(transition.to_level_number)
                except ValueError:
                    errors.append(
                        f"{owner}: transition targets unknown "
                        f"{transition.to_dungeon_id!r} level {transition.to_level_number}"
                    )
                    continue
                if not target.in_bounds(transition.to_position):
                    errors.append(f"{owner}: transition target cell {transition.to_position} is out of bounds")
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
