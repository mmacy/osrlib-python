"""Tests for content packs: identity validators, document round-trips, and the findings matrix."""

import json

import pytest

from osrlib.core.tables import EncounterTable, EncounterTableRow, MonsterEncounterEntry
from osrlib.crawl.content_pack import (
    CONTENT_PACK_KIND,
    ContentPack,
    ContentPackEntry,
    PackFinding,
    PackSection,
    validate_content_pack,
)
from osrlib.crawl.dungeon import FeatureSpec, KeyedEncounter, KeyedMonster, TrapEffect, TrapSpec, WanderingSpec
from osrlib.data import load_equipment, load_monsters
from osrlib.errors import ContentValidationError, SaveVersionError
from osrlib.versioning import SCHEMA_VERSION, engine_version

MONSTERS = load_monsters()
EQUIPMENT = load_equipment()


def make_entry(entry_id: str = "guard-post", template_id: str = "orc") -> ContentPackEntry:
    return ContentPackEntry(
        id=entry_id,
        name="Guard post",
        description="Four orcs dice by torchlight.",
        encounter=KeyedEncounter(monsters=(KeyedMonster(template_id=template_id, count_fixed=4),)),
    )


def make_pack(**overrides: object) -> ContentPack:
    fields: dict[str, object] = {
        "name": "The gnawing dark",
        "sections": (PackSection(id="level-1", label="Level 1", entries=(make_entry(),)),),
    }
    fields.update(overrides)
    return ContentPack.model_validate(fields)


def make_wandering_table(bad_id: str | None = None) -> EncounterTable:
    rows = tuple(
        EncounterTableRow(
            roll=roll,
            name="Orc",
            entry=MonsterEncounterEntry(monster_ids=(bad_id if bad_id is not None and roll == 1 else "orc",)),
            count_fixed=1,
        )
        for roll in range(1, 21)
    )
    return EncounterTable(id="pack-test", label="Pack test", min_level=1, rows=rows)


class TestPackModels:
    def test_entry_rejects_a_non_room_trap(self):
        treasure_trap = TrapSpec(kind="treasure", trigger="open", effect=TrapEffect(damage_dice="1d6"))
        with pytest.raises(ValueError, match="non-room trap"):
            ContentPackEntry(id="cache", trap=treasure_trap)

    def test_entry_accepts_a_room_trap(self):
        room_trap = TrapSpec(kind="room", trigger="enter", effect=TrapEffect(damage_dice="1d6"))
        assert ContentPackEntry(id="pit", trap=room_trap).trap == room_trap

    def test_entry_and_section_ids_must_be_non_empty(self):
        with pytest.raises(ValueError):
            ContentPackEntry(id="")
        with pytest.raises(ValueError):
            PackSection(id="")

    def test_entry_ids_must_be_unique_pack_wide(self):
        sections = (
            PackSection(id="level-1", entries=(make_entry("dup"),)),
            PackSection(id="level-2", entries=(make_entry("dup"),)),
        )
        with pytest.raises(ValueError, match="entry ids must be unique pack-wide"):
            ContentPack(sections=sections)

    def test_section_ids_must_be_unique(self):
        sections = (PackSection(id="dup"), PackSection(id="dup"))
        with pytest.raises(ValueError, match="section ids must be unique"):
            ContentPack(sections=sections)

    def test_monster_ids_must_be_unique(self):
        template = MONSTERS.get("orc").model_copy(update={"id": "pack-orc"})
        with pytest.raises(ValueError, match="monster ids must be unique"):
            ContentPack(monsters=(template, template))

    def test_finding_code_must_be_dotted_snake_case(self):
        with pytest.raises(ValueError, match="dotted"):
            PackFinding(code="notdotted", message="x")


class TestPackDocuments:
    def test_round_trip_survives_json(self):
        pack = make_pack(monsters=(MONSTERS.get("orc").model_copy(update={"id": "pack-orc"}),))
        document = json.loads(json.dumps(pack.to_document()))
        assert document["kind"] == CONTENT_PACK_KIND
        assert ContentPack.from_document(document) == pack

    def test_kind_mismatch_raises(self):
        document = make_pack().to_document()
        document["kind"] = "save"
        with pytest.raises(ContentValidationError):
            ContentPack.from_document(document)

    def test_older_schema_version_is_accepted(self):
        document = make_pack().to_document()
        document["schema_version"] = 1
        assert ContentPack.from_document(document) == make_pack()

    def test_newer_schema_version_raises_save_version_error(self):
        document = make_pack().to_document()
        document["schema_version"] = SCHEMA_VERSION + 1
        with pytest.raises(SaveVersionError):
            ContentPack.from_document(document)

    def test_a_write_re_stamps_at_current_versions(self):
        document = make_pack().to_document()
        document["schema_version"] = 1
        document["engine_version"] = "0.0.1"
        re_stamped = ContentPack.from_document(document).to_document()
        assert re_stamped["schema_version"] == SCHEMA_VERSION
        assert re_stamped["engine_version"] == engine_version()

    def test_unknown_payload_fields_are_ignored(self):
        document = make_pack().to_document()
        document["payload"]["future_field"] = "ignored"
        assert ContentPack.from_document(document) == make_pack()

    def test_malformed_payload_raises_content_error(self):
        document = make_pack().to_document()
        document["payload"]["sections"] = [{"id": ""}]
        with pytest.raises(ContentValidationError):
            ContentPack.from_document(document)


class TestValidateContentPack:
    def test_clean_pack_reports_nothing(self):
        pack = make_pack(
            sections=(
                PackSection(
                    id="level-1",
                    entries=(make_entry(),),
                    wandering=WanderingSpec(chance_in_six=2, table=make_wandering_table()),
                ),
            )
        )
        assert validate_content_pack(pack, MONSTERS, EQUIPMENT) == ()

    def test_bundled_monster_resolves(self):
        template = MONSTERS.get("orc").model_copy(update={"id": "pack-orc"})
        pack = make_pack(
            sections=(PackSection(id="level-1", entries=(make_entry(template_id="pack-orc"),)),),
            monsters=(template,),
        )
        assert validate_content_pack(pack, MONSTERS, EQUIPMENT) == ()

    def test_dangling_encounter_monster_is_a_finding(self):
        pack = make_pack(sections=(PackSection(id="level-1", entries=(make_entry(template_id="gone"),)),))
        findings = validate_content_pack(pack, MONSTERS, EQUIPMENT)
        assert [finding.code for finding in findings] == ["pack.encounter.unknown_monster"]
        assert findings[0].entry_id == "guard-post"
        assert "'gone'" in findings[0].message

    def test_dangling_wandering_monster_is_a_section_finding(self):
        pack = make_pack(
            sections=(PackSection(id="level-1", wandering=WanderingSpec(table=make_wandering_table(bad_id="gone"))),)
        )
        findings = validate_content_pack(pack, MONSTERS, EQUIPMENT)
        assert [finding.code for finding in findings] == ["pack.wandering.unknown_monster"]
        assert findings[0].entry_id is None
        assert "'level-1'" in findings[0].message

    def test_unknown_feature_item_is_a_finding(self):
        entry = ContentPackEntry(
            id="cache",
            features=(FeatureSpec(id="chest", kind="treasure_cache", item_ids=("torch", "no-such-item")),),
        )
        pack = make_pack(sections=(PackSection(id="level-1", entries=(entry,)),))
        findings = validate_content_pack(pack, MONSTERS, EQUIPMENT)
        assert [finding.code for finding in findings] == ["pack.feature.unknown_item"]
        assert findings[0].entry_id == "cache"
        assert "'no-such-item'" in findings[0].message
