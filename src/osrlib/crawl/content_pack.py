"""Content packs, for moving finished rooms from one adventure to another.

A [`ContentPack`][osrlib.crawl.content_pack.ContentPack] contains room content with the geometry left
out. An [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] binds an encounter, a trap, treasure, and
features to particular cells of a particular level. A pack entry has the same content fields and no
cells. That makes it portable. osrlib has no function that applies a pack: an authoring tool, like
one you write, reads an entry and copies its content onto an area that the target adventure
already has. A pack contains no geometry, so it doesn't have to fit a
particular map.

The members, in the order you use them:

- [`ContentPackEntry`][osrlib.crawl.content_pack.ContentPackEntry] is one room's content.
- [`PackSection`][osrlib.crawl.content_pack.PackSection] groups the entries for one dungeon level,
  with that level's [`WanderingSpec`][osrlib.crawl.dungeon.WanderingSpec] if you include it.
- [`ContentPack`][osrlib.crawl.content_pack.ContentPack] contains the sections and the monster
  templates they need.
- [`validate_content_pack`][osrlib.crawl.content_pack.validate_content_pack] takes a pack and the
  built-in catalogs, and returns a [`PackFinding`][osrlib.crawl.content_pack.PackFinding] for each
  reference the pack doesn't cover.
- [`ContentPack.to_document`][osrlib.crawl.content_pack.ContentPack.to_document] and
  [`ContentPack.from_document`][osrlib.crawl.content_pack.ContentPack.from_document] write a pack to
  JSON-compatible data and read it back.

Besides its sections, a pack contains the [`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate]s
that its encounters and wandering tables use and that the built-in catalog doesn't have. These
bundled templates are the pack's *closure*: everything the pack's rooms need that osrlib doesn't
already include. Item templates are outside the closure. An item you bundle in one adventure would
be an unresolved reference in any other adventure, so a pack's features can use items from the
built-in equipment catalog only.

An authoring tool finds a section, an entry, or a bundled monster by its id. When you construct a
pack, osrlib checks that section ids are unique, that entry ids are unique across the whole pack and
not only within a section, and that bundled monster ids are unique. If a pack breaks any of these
rules, constructing it raises an error.

A reference that resolves to nothing is allowed in a pack, as it is in an
[`Adventure`][osrlib.crawl.adventure.Adventure] you haven't finished. An encounter may use a
monster id that neither the pack nor the built-in catalog contains. `validate_content_pack` returns
those gaps as data instead of raising, so the tool that reads a pack can show them to you and let
you decide what to do.

A pack serializes as a stamped document: an envelope whose `kind` is
[`CONTENT_PACK_KIND`][osrlib.crawl.content_pack.CONTENT_PACK_KIND], with the schema version, the
engine version, and the pack as its payload. A document from an older schema version loads, and one
from a newer version raises [`SaveVersionError`][osrlib.errors.SaveVersionError]. Writing a pack
always stamps the current versions, so an older pack that you load and save again comes back
current.

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
"""The `kind` stamped on a serialized content pack: `"content_pack"`.

Every document osrlib writes has a kind, and this is the kind for a pack.
[`ContentPack.to_document`][osrlib.crawl.content_pack.ContentPack.to_document] stamps it, and
[`ContentPack.from_document`][osrlib.crawl.content_pack.ContentPack.from_document] refuses a document
of any other kind. Passing a save file to `from_document` raises an error whose message includes both
kinds, instead of building a pack from the wrong data. Compare a parsed document's `kind` with this
constant to decide which loader to pass it to."""

_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")


def _rewrite_dead_treasure_triggers(payload: dict) -> None:
    """Rewrite a pre-3 payload's `treasure` traps with `trigger="enter"` to `"open"`, in place.

    This does for a pack what the save migration `_migrate_2_to_3` does for a save. A treasure trap
    can be only on an entry's features, and the engine never read the `"enter"` value on one, so the
    rewrite loses nothing.
    """
    for section in payload.get("sections", ()):
        for entry in section.get("entries", ()):
            for feature in entry.get("features", ()):
                trap = feature.get("trap")
                if isinstance(trap, dict) and trap.get("kind") == "treasure" and trap.get("trigger") == "enter":
                    trap["trigger"] = "open"


class ContentPackEntry(BaseModel):
    """One portable room: the content of an [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] without its cells.

    An entry has the content fields an area has: its name, description, encounter, trap, treasure,
    and features. It has no cells and nothing else tied to a grid. A tool that applies a pack picks
    an area the target adventure already has and copies these fields onto it, so the same entry can
    fill a corridor dead end in one adventure and a vaulted hall in another.

    The trap has to be a room trap, as on an `AreaSpec`. A treasure trap goes on one of the
    entry's features instead.

    Attributes:
        id: The entry's id, unique across the pack.
        name: The room's name.
        description: Text describing the room.
        encounter: The monsters waiting in the room.
        trap: A room trap over the whole room.
        treasure: Unguarded treasure the session rolls.
        features: The keyed things in the room.

    Raises:
        ValueError: If `trap` is not a room trap.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    """The entry's id. It can't be empty, and it has to be unique across the whole pack, not only
    within its section, because an authoring tool finds an entry by its id alone. Constructing a pack
    with two entries that share an id raises an error."""
    name: str = ""
    """The room's name, for a tool to list and to copy onto the target area."""
    description: str = ""
    """Text describing the room, for a tool to copy onto the target area."""
    encounter: KeyedEncounter | None = None
    """The monsters waiting in the room, or `None`.
    [`validate_content_pack`][osrlib.crawl.content_pack.validate_content_pack] reports each monster
    id in it that is in neither the built-in catalog nor the pack's `monsters`."""
    trap: TrapSpec | None = None
    """A trap over the whole room, or `None`. It has to be a room trap."""
    treasure: AreaTreasureSpec | None = None
    """Treasure the session rolls the first time the party enters the area, or `None`. It uses
    treasure type letters or the unguarded-treasure table, so it works in any adventure."""
    features: tuple[FeatureSpec, ...] = ()
    """The keyed things in the room: treasure caches, construction tricks, and custom features. A
    pack can't bundle items, so their `item_ids` and `magic_item_ids` have to be ids from the built-in
    catalogs."""

    @model_validator(mode="after")
    def _trap_kind_matches(self) -> ContentPackEntry:
        if self.trap is not None and self.trap.kind != "room":
            raise ValueError(f"entry {self.id!r} carries a non-room trap")
        return self


class PackSection(BaseModel):
    """The entries for one dungeon level, and that level's wandering-monster table.

    A section groups entries the way a tool shows a pack, level by level. It also has a
    wandering-monster check, which applies to a whole level. Entry ids are unique across the pack,
    so a tool doesn't need the section to find an entry.

    Attributes:
        id: The section's id, unique in the pack.
        label: The section's display name.
        entries: The rooms in this section.
        wandering: The level's wandering-monster check.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    """The section's id, unique within the pack. It can't be empty."""
    label: str = ""
    """The name a tool shows for the section, like `"Level 1"` or `"The lower caves"`."""
    entries: tuple[ContentPackEntry, ...] = ()
    """The rooms in this section. Their ids are unique across the whole pack, not only here."""
    wandering: WanderingSpec | None = None
    """The level's wandering-monster check, or `None` for a section without one. A tool applying the
    pack writes it to the target level's `wandering` field. Bundle each monster in its table that the
    built-in catalog doesn't have, as you do for the entries' encounters."""


class ContentPack(BaseModel):
    """A content pack: sections of rooms without geometry, plus the monsters they need.

    Construct one yourself, or have an authoring tool build one from an adventure you have already
    written. Check it with
    [`validate_content_pack`][osrlib.crawl.content_pack.validate_content_pack], write it with
    [`to_document`][osrlib.crawl.content_pack.ContentPack.to_document], and read it back with
    [`from_document`][osrlib.crawl.content_pack.ContentPack.from_document]. osrlib has no function
    that applies a pack to an adventure, because you decide which target area each entry belongs
    on.

    A pack is frozen. osrlib checks its id rules when you construct it, not when you validate it, so
    the ids in any pack you have are already valid.

    Attributes:
        id: The pack's id, when it has one.
        name: The pack's name.
        description: Text about the pack.
        author: Who wrote it.
        sections: The level groupings that contain the entries.
        monsters: The monster templates the entries need.

    Raises:
        ValueError: If two sections share an id, if two entries anywhere in the pack share an id, or
            if two bundled monsters share an id.
    """

    model_config = ConfigDict(frozen=True)

    id: str = ""
    """The pack's id, empty by default. osrlib never reads it. Set one when your tool stores
    several packs and needs to tell them apart."""
    name: str = ""
    """The pack's name, for a tool to show."""
    description: str = ""
    """Text about what the pack contains and what it is for."""
    author: str = ""
    """Who wrote the pack. osrlib never reads it. It stays in the document as credit."""
    sections: tuple[PackSection, ...] = ()
    """The pack's level groupings. Section ids are unique, and entry ids are unique across all the
    sections together."""
    monsters: tuple[MonsterTemplate, ...] = ()
    """The [`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate]s that the pack's encounters and
    wandering tables use and that the built-in catalog doesn't have. Bundle every monster your rooms
    use and the pack is self-contained.

    There is no field for bundled items, because an item you bundle in an adventure exists only in
    that adventure. A pack's features use the built-in equipment and magic-item catalogs, and
    [`validate_content_pack`][osrlib.crawl.content_pack.validate_content_pack] reports an item id from
    an adventure's bundle as a gap."""

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
        """Serialize the pack to a stamped document you can store or send.

        The result is plain JSON-compatible data: an envelope that contains the kind, the schema
        version, the engine version, and the pack as its payload. Pass it to `json.dump`, store it in
        a database, or send it over a network. Read it back with
        [`from_document`][osrlib.crawl.content_pack.ContentPack.from_document].

        Writing always stamps the current versions, so a pack you loaded from an older document
        becomes a current one when you write it.

        Returns:
            The stamped document, with the serialized pack as its `payload`.

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

        This reverses [`to_document`][osrlib.crawl.content_pack.ContentPack.to_document]. Parse your
        file however you like, then pass the resulting mapping here. osrlib checks the pack's id rules
        as it loads, so a document with duplicate ids fails here.

        A document from an older `schema_version` loads. One schema change affects packs: from
        schema 3, [`TrapSpec`][osrlib.crawl.dungeon.TrapSpec] refuses `trigger="enter"` on a treasure
        trap. When an older document has that combination, `from_document` reads the trap with
        `trigger="open"`, as [`load_game`][osrlib.persistence.load_game] does for a save. It ignores
        payload fields this version doesn't recognize.

        Args:
            document: A document from
                [`to_document`][osrlib.crawl.content_pack.ContentPack.to_document], already parsed into a
                mapping.

        Returns:
            The pack.

        Raises:
            ContentValidationError: If the envelope is malformed, if its `kind` isn't
                `"content_pack"`, or if the payload isn't a valid pack. The message gives the reason.
                Fix the document. Loading it again fails the same way.
            osrlib.errors.SaveVersionError: If the document's schema version is newer than this
                version of osrlib supports, which means a later osrlib wrote it. Upgrade osrlib to
                read it.

        Examples:
            ```python
            import json

            from osrlib.crawl.content_pack import ContentPack, ContentPackEntry, PackSection

            entry = ContentPackEntry(id="guard-post", name="Guard post")
            written = ContentPack(name="The gnawing dark", sections=(PackSection(id="level-1", entries=(entry,)),))
            stored = json.dumps(written.to_document())

            # Later, in the authoring tool: read the stored text back and list what the pack offers.
            pack = ContentPack.from_document(json.loads(stored))
            for section in pack.sections:
                print(section.id, [entry.name for entry in section.entries])
            # level-1 ['Guard post']
            ```
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
    """One reference that a content pack doesn't cover: what is missing, and where.

    [`validate_content_pack`][osrlib.crawl.content_pack.validate_content_pack] returns these. A finding
    is data, not an error, so a tool can list the findings beside the pack and let the author decide
    whether each gap matters. To refuse any pack with a gap, check whether the result is empty.

    Attributes:
        code: What kind of gap this is.
        message: The details, in English.
        entry_id: The entry the gap is on, or `None` for a gap in a section's wandering table.

    Raises:
        ValueError: If `code` is not two or more dot-separated snake_case segments.
    """

    model_config = ConfigDict(frozen=True)

    code: str
    """What kind of gap this is, as a dotted snake_case code that starts with `pack`, like
    `"pack.encounter.unknown_monster"`. Read this field in your code. It follows the same format as
    [`Rejection`][osrlib.core.validation.Rejection] codes, so you can switch on it instead of
    parsing the message. The codes `validate_content_pack` returns are:

    - `pack.encounter.unknown_monster`: an entry's encounter uses a monster id the catalogs don't have.
    - `pack.wandering.unknown_monster`: a section's wandering table uses a monster id the catalogs
      don't have.
    - `pack.feature.unknown_item`: a feature's `item_ids` has an id the equipment catalog doesn't have.
    - `pack.feature.unknown_magic_item`: a feature's `magic_item_ids` has an id the magic-item catalog
      doesn't have."""
    message: str
    """The details in English: which entry or section, which feature, and which id. It's written for
    a person reading a list of findings."""
    entry_id: str | None = None
    """The id of the entry the gap is on, or `None` when the gap is in a section's wandering table
    rather than in an entry. For a wandering-table gap, `message` includes the section's id."""

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
    """Report the references that a content pack doesn't cover by itself.

    Call it before you share a pack, or whenever a tool needs to show what is still missing. It
    follows every id in the pack and reports each one that resolves nowhere. For each finding, you
    can bundle the monster, change the id, or accept the gap for the adventures you plan to apply the
    pack to.

    It checks each monster id in the entries' encounters and the sections' wandering tables against
    `monsters` combined with the pack's `monsters`. It checks each feature's `item_ids` against
    `equipment`, and each feature's `magic_item_ids` against the built-in magic-item catalog, which it
    loads itself with [`load_magic_items`][osrlib.data.load_magic_items] because packs bundle no magic
    items.

    It never raises for a gap. A dangling reference is as allowed in a pack as in an adventure you're
    still writing, so the gaps come back as data.
    [`validate_adventure`][osrlib.crawl.adventure.validate_adventure] raises instead, because it checks
    content that is about to be played.

    Args:
        pack: The pack to check.
        monsters: The base monster catalog, usually [`load_monsters`][osrlib.data.load_monsters]`()`.
            The function combines it with `pack.monsters` for you.
        equipment: The base equipment catalog, usually
            [`load_equipment`][osrlib.data.load_equipment]`()`. The function adds nothing to it, because
            you can't bundle items in a pack. It reports an item id that only some adventure's bundle
            has as a gap, because a pack has to work in other adventures.

    Returns:
        One finding for each gap, in pack order: section by section, and within a section, the
        entries in order, then the wandering table. An id that appears in several places gives a
        finding for each place. An empty tuple means the pack is self-contained.

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
