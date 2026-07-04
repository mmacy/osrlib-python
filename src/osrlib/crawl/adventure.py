"""The adventure container: dungeons, the base town, and scenario metadata.

An adventure is frozen game content — the session runs it, never mutates it. The
base town anchors the XP rule's "survive and return to safety" and safe day-level
rest; in 1.0 it is a marker offering safe rest and equipment purchase through the
Phase 1 kernel, not a simulated town. Content prose lives in these models — events
carry ids and front ends resolve prose against the adventure.

[`validate_adventure`][osrlib.crawl.adventure.validate_adventure] is the fail-fast
content gate the spec's error taxonomy names: dangling references (transition
targets, monster template ids, item ids, area cells out of bounds) raise
[`ContentValidationError`][osrlib.errors.ContentValidationError] before a session
ever runs the content.
"""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from osrlib.core.items import EquipmentCatalog
from osrlib.core.monsters import MonsterCatalog
from osrlib.crawl.dungeon import DungeonSpec, FeatureSpec, LevelSpec
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
    """An adventure: one or more dungeons plus the base town and metadata."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str = ""
    hooks: tuple[str, ...] = ()
    town: TownSpec
    dungeons: tuple[DungeonSpec, ...] = Field(min_length=1)

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


def _validate_feature(
    feature: FeatureSpec, level: LevelSpec, owner: str, equipment: EquipmentCatalog, errors: list[str]
) -> None:
    if feature.cell is not None and not level.in_bounds(feature.cell):
        errors.append(f"{owner}: feature {feature.id!r} cell {feature.cell} is out of bounds")
    for item_id in feature.item_ids:
        try:
            equipment.get(item_id)
        except ValueError:
            errors.append(f"{owner}: feature {feature.id!r} references unknown item {item_id!r}")


def validate_adventure(adventure: Adventure, monsters: MonsterCatalog, equipment: EquipmentCatalog) -> None:
    """Validate an adventure's cross-references — the fail-fast content gate.

    Checks, per level: area cells and features in bounds, feature ids unique,
    cache item ids resolving against the equipment catalog, keyed-encounter
    template ids (and pinned alignments) resolving against the monster catalog,
    transition destinations resolving to real cells, town travel entries naming
    real dungeons, and an entrance existing somewhere in every dungeon.

    Args:
        adventure: The adventure to validate.
        monsters: The monster catalog keyed encounters resolve against.
        equipment: The equipment catalog cache contents resolve against.

    Raises:
        ContentValidationError: Listing every dangling reference found.
    """
    errors: list[str] = []
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
                            template = monsters.get(keyed.template_id)
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
                    _validate_feature(feature, level, owner, equipment, errors)
            for feature in level.features:
                if feature.cell is None:
                    errors.append(f"{owner}: level-scope feature {feature.id!r} needs a cell")
                _validate_feature(feature, level, owner, equipment, errors)
            for transition in level.transitions:
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
    if errors:
        raise ContentValidationError("adventure validation failed:\n" + "\n".join(errors))
