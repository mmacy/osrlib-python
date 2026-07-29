"""Content packs: portable, geometry-free keyed content an editor can carry between adventures.

A [`ContentPack`][osrlib.crawl.content_pack.ContentPack] is finished room content
with the geometry left behind: sections of entries that mirror
[`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] minus its cells, each section
optionally carrying a level's [`WanderingSpec`][osrlib.crawl.dungeon.WanderingSpec],
plus the bundled [`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate]s the
entries' encounters and the sections' wandering tables reference — the pack's
closure. Packs are how an authoring
tool moves stocked rooms from one adventure to another: the consumer writes an
entry's content into a target area it already has, so a pack never places cells,
transitions, or any other geometry.

Identity is the contract consumers stand on: section ids, entry ids (pack-wide),
and bundled monster ids are each unique, enforced at construction — a pack that
breaks them refuses to load rather than lingering as a diagnostic. Dangling
references are legal by the same posture as
[`Adventure`][osrlib.crawl.adventure.Adventure]: an encounter may name a template
neither the pack nor the shipped catalog carries, and
[`validate_content_pack`][osrlib.crawl.content_pack.validate_content_pack]
reports such gaps as structured
[`PackFinding`][osrlib.crawl.content_pack.PackFinding]s instead of raising — the
first consumer is a panel that lists findings, not a gate.

Packs serialize as stamped `"content_pack"` documents
([`CONTENT_PACK_KIND`][osrlib.crawl.content_pack.CONTENT_PACK_KIND]), the
longest-lived artifacts in the document family, and own their acceptance rules:
a document stamped by an older schema version is accepted on load, one stamped
by a newer version fails with [`SaveVersionError`][osrlib.errors.SaveVersionError],
and any write re-stamps at the current schema and engine versions — a loaded
older pack saves as a current one.

```python
from osrlib.crawl.content_pack import ContentPack, ContentPackEntry, PackSection, validate_content_pack
from osrlib.crawl.dungeon import KeyedEncounter, KeyedMonster
from osrlib.data import load_equipment, load_monsters

pack = ContentPack(
    name="The gnawing dark",
    sections=(
        PackSection(
            id="level-1",
            label="Level 1",
            entries=(
                ContentPackEntry(
                    id="guard-post",
                    name="Guard post",
                    encounter=KeyedEncounter(monsters=(KeyedMonster(template_id="orc", count_fixed=4),)),
                ),
            ),
        ),
    ),
)
document = pack.to_document()
assert document["kind"] == "content_pack"
assert ContentPack.from_document(document) == pack
assert validate_content_pack(pack, load_monsters(), load_equipment()) == ()
```
"""

import re
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from osrlib.core.items import EquipmentCatalog
from osrlib.core.monsters import MonsterCatalog, MonsterTemplate
from osrlib.crawl.dungeon import AreaTreasureSpec, FeatureSpec, KeyedEncounter, TrapSpec, WanderingSpec
from osrlib.errors import ContentValidationError
from osrlib.versioning import check_document, stamp_document

__all__ = [
    "CONTENT_PACK_KIND",
    "ContentPack",
    "ContentPackEntry",
    "PackFinding",
    "PackSection",
    "validate_content_pack",
]

CONTENT_PACK_KIND = "content_pack"
"""The stamped-document kind for serialized content packs."""

_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")


class ContentPackEntry(BaseModel):
    """One portable room: [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] minus its geometry.

    The entry carries the content slots an area has — prose, encounter, trap,
    treasure, features — and nothing that binds to a grid: there are no cells,
    and a consumer writes the carried slots into a target area it already has.
    An entry trap must be a room trap, the same rule `AreaSpec` enforces; the
    treasure-trap coupling needs no restatement because it lives on
    [`FeatureSpec`][osrlib.crawl.dungeon.FeatureSpec] itself.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    name: str = ""
    description: str = ""
    encounter: KeyedEncounter | None = None
    trap: TrapSpec | None = None
    treasure: AreaTreasureSpec | None = None
    features: tuple[FeatureSpec, ...] = ()

    @model_validator(mode="after")
    def _trap_kind_matches(self) -> ContentPackEntry:
        if self.trap is not None and self.trap.kind != "room":
            raise ValueError(f"entry {self.id!r} carries a non-room trap")
        return self


class PackSection(BaseModel):
    """A pack's level grouping: entries plus the level's optional wandering table.

    Sections are structural because three consumers operate on a level grouping —
    a panel's level rows, wandering's level scope, and a level-scoped capture.
    Entry ids are unique pack-wide, so consumers address entries by id alone;
    the section contributes presentation grouping and the wandering slot.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    label: str = ""
    entries: tuple[ContentPackEntry, ...] = ()
    wandering: WanderingSpec | None = None


class ContentPack(BaseModel):
    """A content pack: sections of geometry-free entries plus their monster closure.

    `monsters` bundles the [`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate]s
    the pack's encounters and wandering tables reference beyond the shipped
    catalog. `id` defaults empty: a pack derived on the fly takes its identity
    from its source, and only a persisted pack mints an id of its own.
    """

    model_config = ConfigDict(frozen=True)

    id: str = ""
    name: str = ""
    description: str = ""
    author: str = ""
    sections: tuple[PackSection, ...] = ()
    monsters: tuple[MonsterTemplate, ...] = ()

    @model_validator(mode="after")
    def _identities_are_unique(self) -> ContentPack:
        section_ids = [section.id for section in self.sections]
        if len(set(section_ids)) != len(section_ids):
            raise ValueError("section ids must be unique")
        entry_ids = [entry.id for section in self.sections for entry in section.entries]
        if len(set(entry_ids)) != len(entry_ids):
            raise ValueError("entry ids must be unique pack-wide")
        monster_ids = [template.id for template in self.monsters]
        if len(set(monster_ids)) != len(monster_ids):
            raise ValueError("monster ids must be unique")
        return self

    def to_document(self) -> dict[str, object]:
        """Serialize to a stamped document with schema and engine versions.

        A write always stamps the current versions: re-serializing a pack loaded
        from an older document re-stamps it as a current one.

        Returns:
            The stamped document envelope wrapping the serialized pack.
        """
        return stamp_document(CONTENT_PACK_KIND, self.model_dump(mode="json"))

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> ContentPack:
        """Load a content pack from a stamped document.

        A `schema_version` older than the current one is accepted — pack payload
        changes are additive within a version, so an older document validates
        directly. Unknown payload fields are ignored, per the additive-schema
        contract.

        Args:
            document: A document produced by
                [`to_document`][osrlib.crawl.content_pack.ContentPack.to_document].

        Returns:
            The reconstructed pack.

        Raises:
            ContentValidationError: If the envelope or payload is malformed or of
                the wrong kind.
            SaveVersionError: If the document's schema version is newer than this
                library understands.
        """
        payload = check_document(document, CONTENT_PACK_KIND)
        try:
            return cls.model_validate(payload)
        except ValueError as error:
            raise ContentValidationError(f"content pack document payload failed validation: {error}") from error


class PackFinding(BaseModel):
    """A structured self-containment gap in a content pack.

    `code` is dotted snake_case namespaced by subsystem, the
    [`Rejection`][osrlib.core.validation.Rejection] discipline. `entry_id` names
    the entry the gap sits on, or `None` for a section-scoped gap (a wandering
    table's); `message` carries the specifics either way.
    """

    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    entry_id: str | None = None

    @field_validator("code")
    @classmethod
    def _code_must_be_dotted_snake_case(cls, value: str) -> str:
        if _CODE_PATTERN.fullmatch(value) is None:
            raise ValueError(
                "finding code must be two or more dot-separated snake_case segments "
                f"(like 'pack.encounter.unknown_monster'), got {value!r}"
            )
        return value


def validate_content_pack(
    pack: ContentPack, monsters: MonsterCatalog, equipment: EquipmentCatalog
) -> tuple[PackFinding, ...]:
    """Report a pack's self-containment gaps — references its closure does not cover.

    Checks every monster reference (keyed-encounter lines and wandering-table
    rows) against the union of the shipped catalog and the pack's bundled
    monsters, and every feature's `item_ids` against the equipment catalog.
    Findings are data, not errors: a dangling reference is legal in a pack
    exactly as it is while editing an adventure, and a caller wanting a gate
    checks for a non-empty result.

    Args:
        pack: The pack to check.
        monsters: The *base* monster catalog — the check unions it with
            `pack.monsters` internally.
        equipment: The equipment catalog feature contents resolve against.

    Returns:
        One finding per gap, in section and entry order; empty means the pack is
        self-contained.
    """
    known_monsters = {template.id for template in monsters.monsters}
    known_monsters.update(template.id for template in pack.monsters)
    findings: list[PackFinding] = []

    def check_items(features: tuple[FeatureSpec, ...], entry_id: str) -> None:
        for feature in features:
            for item_id in feature.item_ids:
                try:
                    equipment.get(item_id)
                except ValueError:
                    findings.append(
                        PackFinding(
                            code="pack.feature.unknown_item",
                            message=f"entry {entry_id!r} feature {feature.id!r} references unknown item {item_id!r}",
                            entry_id=entry_id,
                        )
                    )

    for section in pack.sections:
        for entry in section.entries:
            if entry.encounter is not None:
                for keyed in entry.encounter.monsters:
                    if keyed.template_id not in known_monsters:
                        findings.append(
                            PackFinding(
                                code="pack.encounter.unknown_monster",
                                message=f"entry {entry.id!r} references unknown monster {keyed.template_id!r}",
                                entry_id=entry.id,
                            )
                        )
            check_items(entry.features, entry.id)
        if section.wandering is not None and section.wandering.table is not None:
            for row in section.wandering.table.rows:
                if row.entry.kind != "monster":
                    continue
                for monster_id in row.entry.monster_ids:
                    if monster_id not in known_monsters:
                        findings.append(
                            PackFinding(
                                code="pack.wandering.unknown_monster",
                                message=(
                                    f"section {section.id!r} wandering row {row.name!r} "
                                    f"references unknown monster {monster_id!r}"
                                ),
                            )
                        )
    return tuple(findings)
