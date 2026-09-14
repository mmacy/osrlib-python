"""Content packs: finished rooms you can carry from one adventure to another.

A [`ContentPack`][osrlib.crawl.content_pack.ContentPack] is room content with the geometry left out.
Where an [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] binds an encounter, a trap, treasure, and
features to particular cells of a particular level, a pack entry carries the same content slots and
no cells at all. That is what makes it portable: an authoring tool reads an entry and writes its
content into whatever area the target adventure already has, so a pack never places geometry and
never has to agree with a map it has not seen.

Sections group the entries by dungeon level, and each section can carry that level's
[`WanderingSpec`][osrlib.crawl.dungeon.WanderingSpec]. Alongside them, the pack bundles the
[`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate]s its encounters and wandering tables
reference beyond the shipped catalog, which is the pack's closure: what it needs that the engine does
not already ship. Item templates are deliberately outside that closure, because a bundled item id
belongs to the one adventure that carries it and would arrive dangling anywhere else, so a pack's
features reference the shipped equipment catalog only.

Ids are what an authoring tool addresses a pack by, so all three sets are checked when the pack is
constructed: section ids, entry ids (unique across the whole pack, not only within a section), and
bundled monster ids. A pack that breaks any of the three fails to construct rather than surviving as
a warning.

A reference that resolves to nothing is legal here, the way it is in an
[`Adventure`][osrlib.crawl.adventure.Adventure] you have not finished writing. An encounter may name a
template neither the pack nor the shipped catalog holds, and
[`validate_content_pack`][osrlib.crawl.content_pack.validate_content_pack] hands those back as
[`PackFinding`][osrlib.crawl.content_pack.PackFinding] models instead of raising, so the tool that
reads a pack can show you the gaps and let you decide.

Packs serialize as stamped `"content_pack"` documents
([`CONTENT_PACK_KIND`][osrlib.crawl.content_pack.CONTENT_PACK_KIND]) and have their own acceptance
rules, because a pack is meant to be kept and passed around longer than a save file is. A document stamped by an older
schema version loads, one stamped by a newer version fails with
[`SaveVersionError`][osrlib.errors.SaveVersionError], and every write re-stamps at the current schema
and engine versions, so an older pack you load and save comes back current.

Typical usage:

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
from osrlib.data import load_magic_items
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
"""The `kind` stamped on a serialized content pack.

Every document osrlib writes has a kind, and this is the pack's.
[`ContentPack.to_document`][osrlib.crawl.content_pack.ContentPack.to_document] stamps it and
[`from_document`][osrlib.crawl.content_pack.ContentPack.from_document] refuses anything else, so
handing a save file to a pack loader fails with a message naming both kinds rather than producing a
nonsense pack. Read it to route a document you have just parsed to the right loader."""

_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")


def _rewrite_dead_treasure_triggers(payload: dict) -> None:
    """Rewrite a pre-3 payload's `treasure` traps with `trigger="enter"` to `"open"`, in place.

    The pack-side twin of the save chain's `_migrate_2_to_3`. A treasure trap can sit only on an
    entry's features, and the engine never read the dead trigger value, so the rewrite loses nothing.
    """
    for section in payload.get("sections", ()):
        for entry in section.get("entries", ()):
            for feature in entry.get("features", ()):
                trap = feature.get("trap")
                if isinstance(trap, dict) and trap.get("kind") == "treasure" and trap.get("trigger") == "enter":
                    trap["trigger"] = "open"


class ContentPackEntry(BaseModel):
    """One portable room: an [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] with its geometry left out.

    An entry carries the content slots an area has, which is prose, an encounter, a trap, treasure,
    and features, and nothing that binds to a grid. There are no cells. A tool applying a pack picks
    a target area the adventure already has and writes these slots onto it, so the same entry works on
    a corridor dead end in one adventure and a vaulted hall in another.

    An entry's trap has to be a room trap, the same rule `AreaSpec` enforces. Treasure traps need no
    rule here, because they belong to a [`FeatureSpec`][osrlib.crawl.dungeon.FeatureSpec] and that
    model already enforces it.

    Attributes:
        id: The entry's id, unique across the pack.
        name: The room's name.
        description: Prose for your front end.
        encounter: The monsters waiting in the room.
        trap: A room trap over the whole room.
        treasure: Generated treasure with nothing guarding it.
        features: The keyed things in the room.

    Raises:
        ValueError: If `trap` is not a room trap.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    """The entry's id, which has to be unique across the whole pack rather than only within its
    section. An authoring tool addresses an entry by this alone, so a pack with two entries of the
    same id fails to construct. It cannot be empty."""
    name: str = ""
    """The room's name, for a panel to list and for the target area to take."""
    description: str = ""
    """Prose describing the room, for the target area to take."""
    encounter: KeyedEncounter | None = None
    """The monsters waiting in the room, or `None`. Any template id it names that neither the shipped
    catalog nor the pack's own `monsters` holds is what
    [`validate_content_pack`][osrlib.crawl.content_pack.validate_content_pack] reports as a gap."""
    trap: TrapSpec | None = None
    """A trap over the whole room, or `None`. It has to be a room trap."""
    treasure: AreaTreasureSpec | None = None
    """Treasure the engine rolls on first entry, or `None`. It names treasure type letters or the
    unguarded band, so it carries across adventures without needing anything else."""
    features: tuple[FeatureSpec, ...] = ()
    """The keyed things in the room: caches, tricks, and custom content. Their `item_ids` and
    `magic_item_ids` resolve against the shipped catalogs only, since a pack bundles no items of its
    own."""

    @model_validator(mode="after")
    def _trap_kind_matches(self) -> ContentPackEntry:
        if self.trap is not None and self.trap.kind != "room":
            raise ValueError(f"entry {self.id!r} carries a non-room trap")
        return self


class PackSection(BaseModel):
    """A pack's level grouping: the entries for one dungeon level, plus that level's wandering table.

    Sections exist because three things work on a level at a time: a panel showing the pack level by
    level, the wandering-monster check, whose scope is a level, and a capture that pulls one level's
    rooms out of an adventure. Entry ids are unique across the pack, so an authoring tool never needs
    the section to address an entry. What the section adds is the grouping and the wandering slot.

    Attributes:
        id: The section's id, unique in the pack.
        label: The section's display name.
        entries: The rooms in this section.
        wandering: The level's wandering-monster check.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    """The section's id, unique within the pack. It cannot be empty."""
    label: str = ""
    """What a panel calls this section: `"Level 1"`, `"The lower caves"`."""
    entries: tuple[ContentPackEntry, ...] = ()
    """The rooms in this section. Their ids are unique across the whole pack, not only here."""
    wandering: WanderingSpec | None = None
    """The level's wandering-monster check, or `None` for a section that has none. An authoring tool
    writes it onto the target level's `wandering`. Monster ids in its table are part of what the
    pack's closure has to cover."""


class ContentPack(BaseModel):
    """A content pack: sections of geometry-free rooms, plus the monsters they need.

    Build one by hand, or have an authoring tool capture it out of an adventure you have already
    written. Check it with
    [`validate_content_pack`][osrlib.crawl.content_pack.validate_content_pack], write it out with
    [`to_document`][osrlib.crawl.content_pack.ContentPack.to_document], and read it back with
    [`from_document`][osrlib.crawl.content_pack.ContentPack.from_document]. What you do with the
    entries is yours: nothing in osrlib applies a pack to an adventure, because only your tool has the
    mapping from an entry to the target area it belongs on.

    A pack is frozen, and its three id rules are checked when you construct it rather than when you
    validate it, so a pack you have in hand is one whose ids are already sound.

    Attributes:
        id: The pack's own id, when it has one.
        name: The pack's name.
        description: Prose about the pack.
        author: Who wrote it.
        sections: The level groupings holding the entries.
        monsters: The monster templates the entries need.

    Raises:
        ValueError: If two sections share an id, if two entries share an id anywhere in the pack, or
            if two bundled monsters share an id.
    """

    model_config = ConfigDict(frozen=True)

    id: str = ""
    """The pack's own id, empty by default. A pack derived on the fly takes its identity from
    whatever it was derived from, so only a pack somebody saved needs to mint one."""
    name: str = ""
    """The pack's name, for a panel to show."""
    description: str = ""
    """Prose about what the pack contains and what it is for."""
    author: str = ""
    """Who wrote the pack. Nothing reads it, and it travels with the document as credit."""
    sections: tuple[PackSection, ...] = ()
    """The pack's level groupings. Section ids are unique, and entry ids are unique across all of
    them together."""
    monsters: tuple[MonsterTemplate, ...] = ()
    """The [`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate]s the pack's encounters and
    wandering tables need beyond the shipped catalog. This is the pack's closure: bundle what your
    rooms reference and the pack arrives self-contained.

    There is no matching item bundle, because a bundled item id belongs to the adventure that carries
    it. A pack's features reference the shipped equipment and magic-item catalogs, and an entry naming
    an adventure's bundled item is reported as a gap rather than carried along."""

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
        """Serialize the pack to a stamped document you can write to a file.

        The result is plain JSON-compatible data: an envelope containing the kind, the schema version,
        the engine version, and the pack itself as the payload. Hand it to `json.dump`, put it in a
        database, or send it over a wire. Read it back with
        [`from_document`][osrlib.crawl.content_pack.ContentPack.from_document].

        A write always stamps the current versions, so a pack you loaded from an older document
        saves as a current one.

        Returns:
            The stamped document envelope wrapping the serialized pack.

        Examples:
            ```python
            from osrlib.crawl.content_pack import ContentPack

            document = ContentPack(name="The gnawing dark").to_document()
            print(document["kind"])
            # content_pack
            ```
        """
        return stamp_document(CONTENT_PACK_KIND, self.model_dump(mode="json"))

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> ContentPack:
        """Load a content pack from a stamped document.

        This is the other half of [`to_document`][osrlib.crawl.content_pack.ContentPack.to_document]:
        parse your file however you like, then hand the resulting mapping here. The pack's identity
        rules are checked on the way in, so a document with duplicate ids fails here rather than
        later.

        An older `schema_version` is accepted, because pack payloads only grow within a version.
        There has been one narrowing: schema 3 made [`TrapSpec`][osrlib.crawl.dungeon.TrapSpec]
        refuse `trigger="enter"` on a treasure trap, and a pre-3 document carrying that combination is
        repaired in place on the way in, rewritten to `"open"` exactly as the save migration does.
        Payload fields this version doesn't recognize are ignored.

        Args:
            document: A document produced by
                [`to_document`][osrlib.crawl.content_pack.ContentPack.to_document].

        Returns:
            The reconstructed pack.

        Raises:
            ContentValidationError: If the envelope is malformed or is not a `content_pack`
                document, or if the payload fails validation.
            SaveVersionError: If the document's schema version is newer than this library
                understands, which means the pack was written by a later osrlib than the one reading
                it.
        """
        payload = check_document(document, CONTENT_PACK_KIND)
        schema_version = document["schema_version"]  # an int: check_document vetted the envelope
        if isinstance(schema_version, int) and schema_version < 3:
            _rewrite_dead_treasure_triggers(payload)
        try:
            return cls.model_validate(payload)
        except ValueError as error:
            raise ContentValidationError(f"content pack document payload failed validation: {error}") from error


class PackFinding(BaseModel):
    """One reference a content pack does not cover: what is missing, and where.

    [`validate_content_pack`][osrlib.crawl.content_pack.validate_content_pack] returns these. They are
    data rather than errors, so a panel can list them beside the pack and let the author decide
    whether a gap matters. To refuse a pack that has any gap, check for a non-empty result.

    Attributes:
        code: What kind of gap this is.
        message: The specifics, in English.
        entry_id: The entry the gap sits on, or `None` for a section-level one.

    Raises:
        ValueError: If `code` is not two or more dot-separated snake_case segments.
    """

    model_config = ConfigDict(frozen=True)

    code: str
    """What kind of gap this is, as a dotted snake_case code namespaced by subsystem, like
    `"pack.encounter.unknown_monster"`. It is the part a program reads, and it follows the same rule
    [`Rejection`][osrlib.core.validation.Rejection] codes do, so a front end can switch on it instead
    of parsing the message."""
    message: str
    """The specifics in English: which entry, which feature, which id. Written for a person reading a
    list of findings."""
    entry_id: str | None = None
    """The entry the gap sits on, or `None` when the gap belongs to a section rather than an entry,
    which today means a wandering table's monster. `message` names the section in that case."""

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
    """Report the references a content pack does not cover on its own.

    Call this before you share a pack, or whenever a panel needs to show what is still missing. It
    follows every id in the pack and reports the ones that resolve nowhere, so you can bundle the
    monster, change the id, or decide the gap is acceptable for the adventures you mean to apply the
    pack to.

    It checks every monster reference, which is the keyed-encounter lines and the wandering-table
    rows, against the shipped catalog composed with the pack's own `monsters`. It checks every
    feature's `item_ids` against the shipped equipment catalog and every feature's `magic_item_ids`
    against the shipped magic-item catalog, which it loads itself with
    [`load_magic_items`][osrlib.data.load_magic_items] because packs bundle no magic items.

    It never raises. A dangling reference in a pack is as legal as one in an adventure you are still
    writing, so the gaps come back as data. Compare it with
    [`validate_adventure`][osrlib.crawl.adventure.validate_adventure], which does raise, because by
    then the content is about to be played.

    Args:
        pack: The pack to check.
        monsters: The base monster catalog, usually [`load_monsters`][osrlib.data.load_monsters]. The
            check composes it with `pack.monsters` itself.
        equipment: The shipped equipment catalog, usually
            [`load_equipment`][osrlib.data.load_equipment]. Nothing is composed with it, because a
            pack carries no items of its own. An entry naming an item that some adventure bundles is
            reported as a gap, which is the right answer for content meant to travel.

    Returns:
        One finding per gap, in section then entry order. Empty means the pack is self-contained.

    Examples:
        ```python
        from osrlib.crawl.content_pack import ContentPack, ContentPackEntry, PackSection, validate_content_pack
        from osrlib.crawl.dungeon import KeyedEncounter, KeyedMonster
        from osrlib.data import load_equipment, load_monsters

        entry = ContentPackEntry(
            id="guard-post",
            encounter=KeyedEncounter(monsters=(KeyedMonster(template_id="grue", count_fixed=1),)),
        )
        pack = ContentPack(name="The gnawing dark", sections=(PackSection(id="level-1", entries=(entry,)),))
        for finding in validate_content_pack(pack, load_monsters(), load_equipment()):
            print(finding.code, finding.entry_id, finding.message)
        # pack.encounter.unknown_monster guard-post entry 'guard-post' references unknown monster 'grue'
        ```
    """
    known_monsters = {template.id for template in monsters.monsters}
    known_monsters.update(template.id for template in pack.monsters)
    magic = load_magic_items()
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
            for item_id in feature.magic_item_ids:
                try:
                    magic.get(item_id)
                except ValueError:
                    findings.append(
                        PackFinding(
                            code="pack.feature.unknown_magic_item",
                            message=(
                                f"entry {entry_id!r} feature {feature.id!r} references unknown magic item {item_id!r}"
                            ),
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
