"""The adventure container: dungeons, the base town, and scenario metadata.

An adventure is frozen game content — the session runs it, never mutates it. The
base town anchors the XP rule's "survive and return to safety" and safe day-level
rest. It is a marker offering safe rest and equipment purchase through the
kernel, not a simulated town. Content prose lives in these models — events
carry ids and front ends resolve prose against the adventure.

Beyond the dungeons, the document carries the adventure's own content and
behavior: `monsters` and `items` bundle templates that resolve beside the shipped
catalogs for that session, `triggers` is the authored wiring
([`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec]), and `quests` the authored
errands ([`QuestSpec`][osrlib.crawl.quests.QuestSpec]) — with gates
([`GateSpec`][osrlib.crawl.gates.GateSpec]) riding the dungeon geometry's doors
and transitions.

[`validate_adventure`][osrlib.crawl.adventure.validate_adventure] is the fail-fast
content gate: dangling references (transition targets, monster template ids,
item ids, area cells out of bounds, gate item ids, trigger and quest references —
patterns, conditions, consequence targets, selectors) raise
[`ContentValidationError`][osrlib.errors.ContentValidationError] before a session
ever runs the content.
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
    """The base town: safe rest, equipment purchase, and travel costs.

    `services` is prose for front ends. `travel_turns` maps dungeon ids to the
    town-to-entrance travel cost in exploration turns — content-authored, consumed
    by `EnterDungeon` and `TravelToTown`.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    description: str = ""
    services: tuple[str, ...] = ()
    travel_turns: dict[str, int] = {}


class Adventure(BaseModel):
    """An adventure: one or more dungeons plus the base town and metadata.

    `monsters` are the adventure's bundled custom
    [`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate]s: they join the
    shipped catalog for this adventure's sessions everywhere the engine resolves
    template ids (keyed encounters, `SpawnMonsters`, inline wandering tables,
    listen checks). Bundled ids must not collide with the shipped catalog or each
    other — a collision is a validation error, never an override. The empty tuple
    is the universal default: an adventure that bundles nothing plays exactly as
    before.

    `items` are the adventure's bundled custom
    [`ItemTemplate`][osrlib.core.items.ItemTemplate]s — weapons, armour, gear, and
    ammunition — under the same contract: they join the shipped equipment catalog
    for this adventure's sessions everywhere the engine resolves authored item ids
    (treasure caches, `GrantItem`, drop-pile recovery), and they ride every carry
    surface (gives, equips, drops) through the templates their instances embed.
    Bundled item ids must not collide with the equipment catalog, the magic-item
    catalog, or each other — one item id names one thing per session. The town
    shop is the one place they do not reach: it stocks the shipped equipment
    lists.

    `triggers` are the adventure's authored
    [`TriggerSpec`][osrlib.crawl.triggers.TriggerSpec]s, and the tuple's order *is*
    document order: triggers matching one event fire in it. A game plays them by
    registering an [`Interpreter`][osrlib.crawl.interpreter.Interpreter] on its
    session; an adventure that authors none plays exactly as one that never could.

    `quests` are the adventure's authored
    [`QuestSpec`][osrlib.crawl.quests.QuestSpec]s, in document order too: a session
    seeds one state block per quest at construction, in this order, and every walk
    over them follows it. Quest ids and trigger ids are separate namespaces — they
    live in separate state blocks — so a quest and a trigger may share an id.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    description: str = ""
    hooks: tuple[str, ...] = ()
    town: TownSpec
    dungeons: tuple[DungeonSpec, ...] = Field(min_length=1)
    monsters: tuple[MonsterTemplate, ...] = ()
    items: tuple[ItemTemplate, ...] = ()
    triggers: tuple[TriggerSpec, ...] = ()
    quests: tuple[QuestSpec, ...] = ()

    @model_validator(mode="after")
    def _dungeon_ids_unique(self) -> Adventure:
        ids = [dungeon.id for dungeon in self.dungeons]
        if len(set(ids)) != len(ids):
            raise ValueError("dungeon ids must be unique")
        return self

    def dungeon(self, dungeon_id: str) -> DungeonSpec:
        """Return the dungeon with `dungeon_id`.

        Args:
            dungeon_id: The dungeon id.

        Returns:
            The dungeon spec.

        Raises:
            ValueError: If no dungeon has that id.
        """
        for dungeon in self.dungeons:
            if dungeon.id == dungeon_id:
                return dungeon
        raise ValueError(f"unknown dungeon id {dungeon_id!r}")

    def quest(self, quest_id: str) -> QuestSpec:
        """Return the quest with `quest_id`.

        The resolution behind the quest lifecycle commands' closed id domain: an id
        this cannot answer names no quest of this adventure.

        Args:
            quest_id: The quest id.

        Returns:
            The quest spec.

        Raises:
            ValueError: If no quest has that id.
        """
        for quest in self.quests:
            if quest.id == quest_id:
                return quest
        raise ValueError(f"unknown quest id {quest_id!r}")


def _effective_monsters(adventure: Adventure, base: MonsterCatalog) -> tuple[MonsterCatalog, tuple[str, ...]]:
    """Build the adventure's effective monster catalog: base ∪ bundled, first occurrence wins.

    Always returns a usable catalog plus every skipped colliding id (empty means
    clean) — both callers get a total answer, and each turns a non-empty collision
    list into its own typed failure. An empty bundle returns the base catalog
    object itself: no copy, no behavior change for adventures that bundle nothing.
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
    """Build the adventure's effective equipment catalog: base ∪ bundled, first occurrence wins.

    The monster helper's sibling, with one wider rule: an item id names one thing
    per session, so a bundled id collides with the shipped magic-item ids as well
    as the four equipment lists and the rest of the bundle. Collisions are skipped
    before the catalog is built rather than after — `EquipmentCatalog`'s own
    uniqueness validator raises a bare `ValueError`, the wrong failure shape for a
    content problem. `treasure_weights` is an encumbrance table, not item
    identity, so it passes through untouched and its ids are outside the rule. An
    empty bundle returns the base catalog object itself: no copy, no behavior
    change for adventures that bundle nothing.
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
    """The condition's item id when it names nothing that can ever be carried, else `None`.

    Equipment (base ∪ bundled) or magic item: exactly the union `has_item`
    evaluates against, so an id neither catalog holds can never be satisfied and is
    a dangling reference. Every other condition kind answers `None` — flag keys and
    effect kinds are open domains and get no check, because a flag nobody writes is
    authoring-tool territory, not a broken document. Gates and bare trigger
    conditions both ask here, so the two can never disagree about the domain.
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
    """Resolve a gate's `has_item` id against the item domain the condition matches."""
    if gate is None:
        return
    dangling = _dangling_condition_item(gate.condition, equipment, magic)
    if dangling is not None:
        errors.append(f"{owner}: {site} gate references unknown item {dangling!r}")


def _resolve_level(adventure: Adventure, dungeon_id: str, level_number: int) -> LevelSpec | None:
    """The level a dungeon id and level number name, or `None` when either dangles."""
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

    The one body behind every clause in a document — a trigger's `when` and
    `conditions`, a quest's activation, an objective's completion, a hidden
    objective's reveal — so the two authoring surfaces can never drift apart on what
    resolves. `owner` is the subject the error lines name (`trigger 'lever-east'`,
    `quest 'the-idol' objective 'return-home'`).

    Flag keys stay unchecked at every site — the flag namespace is open by design,
    and a key nobody writes is an authoring lint rather than a broken document.
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

    The one body behind every consequence in a document — a trigger's consequences
    and a quest's rewards alike. `site` is the subject the error lines name
    (`trigger 'reward': consequence 0`, `quest 'the-idol': reward 0`).
    """
    if isinstance(consequence, GrantItem | GrantCoins | AwardXP):
        # Character ids are allocated per session, so a document can never name
        # one: authored consequences address the party through the selectors.
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
        # A town placement names the adventure's one town and needs no check; the
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

    Every clause a quest carries walks the shared clause check: the activation, each
    objective's completion, and each hidden objective's reveal — the reveal named
    apart from the completion so an error line says which of the two dangles. Rewards
    walk the shared consequence check, so a quest's reward and a trigger's consequence
    are held to the same references and the same party-selector rule.
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
    """Validate an adventure's cross-references — the fail-fast content gate.

    Checks: bundled monster ids colliding with the shipped catalog or each other,
    and bundled item ids colliding with the equipment catalog, the shipped
    magic-item catalog, or each other; then, per level: area cells and features in
    bounds, feature ids unique, cache item ids resolving against the effective
    equipment catalog, cache magic item ids resolving against the shipped
    magic-item catalog ([`load_magic_items`][osrlib.data.load_magic_items] —
    adventures bundle no magic items, so validation loads it itself),
    keyed-encounter template ids (and any fixed spawn alignment) and inline
    wandering-table monster ids resolving against the effective catalog, item ids
    named by `has_item` gates on doors and transitions resolving against the
    effective equipment catalog or the magic-item catalog, transition destinations
    resolving to real cells, town travel entries naming real dungeons, and an
    entrance existing somewhere in every dungeon.

    Then, per trigger: ids unique across the adventure; the pattern's area, level,
    dungeon, item, and monster references resolving; the same item domain for
    `has_item` conditions; and per consequence, granted item ids, spawned template
    ids, a door edge at the cell a door write names, a placement landing on the
    grid, and the rule that a consequence addressing a character does so through a
    party selector — a session allocates character ids, so a document naming one is
    naming something that cannot exist when it is read.

    Then, per quest: ids unique across the adventure (quest ids and trigger ids are
    separate namespaces, and nothing here cross-checks them); per clause — the
    activation, each objective's completion, each hidden objective's reveal — the
    same pattern and condition references a trigger's clause resolves; and per
    reward, the same references and the same party-selector rule a consequence gets.
    The two surfaces share one clause check and one consequence check, so neither can
    grow a reference the other fails to resolve.

    Args:
        adventure: The adventure to validate.
        monsters: The *base* monster catalog — validation unions it internally
            with the adventure's bundled templates, and every monster reference
            resolves against that union.
        equipment: The *base* equipment catalog — validation unions it internally
            with the adventure's bundled templates, and every cache item reference
            resolves against that union.

    Raises:
        ContentValidationError: Listing every dangling reference found.
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
