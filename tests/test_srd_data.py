"""Tests for the SRD data pipeline: determinism, validation, counts, and spot checks."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from osrlib.core.items import Material, WeaponQuality
from osrlib.data import load_ability_tables, load_classes, load_equipment, load_languages

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "src" / "osrlib" / "data"
DATA_FILES = (
    "abilities.json",
    "classes.json",
    "combat_tables.json",
    "equipment.json",
    "languages.json",
    "monsters.json",
    "spells.json",
)


def run_compiler(out_dir: Path) -> None:
    subprocess.run(
        [sys.executable, "-m", "tools.srd_compile", "--out-dir", str(out_dir)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    first = tmp_path_factory.mktemp("srd-run-1")
    second = tmp_path_factory.mktemp("srd-run-2")
    run_compiler(first)
    run_compiler(second)
    return first, second


class TestPipelineDeterminism:
    def test_two_runs_are_byte_identical(self, generated: tuple[Path, Path]):
        first, second = generated
        for filename in DATA_FILES:
            assert (first / filename).read_bytes() == (second / filename).read_bytes()

    def test_generated_matches_committed(self, generated: tuple[Path, Path]):
        # The same guard CI runs: srd/, the compiler, and osrlib/data/ cannot drift.
        first, _ = generated
        for filename in DATA_FILES:
            assert (first / filename).read_bytes() == (DATA_DIR / filename).read_bytes(), (
                f"{filename} is stale; run: uv run python -m tools.srd_compile"
            )

    def test_output_is_canonical_json(self):
        for filename in DATA_FILES:
            text = (DATA_DIR / filename).read_text(encoding="utf-8")
            parsed = json.loads(text)
            assert text == json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def test_meta_names_source_pages(self):
        for filename in DATA_FILES:
            meta = json.loads((DATA_DIR / filename).read_text(encoding="utf-8"))["_meta"]
            assert meta["source_pages"], filename
            assert "OGL" in meta["license"]


class TestLoaders:
    def test_every_file_validates(self):
        assert load_ability_tables() is load_ability_tables()  # cached
        assert load_classes().classes
        assert load_equipment().weapons
        assert load_languages().languages

    def test_entry_counts(self):
        assert len(load_classes().classes) == 7
        equipment = load_equipment()
        assert len(equipment.weapons) == 19
        assert len(equipment.armour) == 4
        assert len(equipment.gear) == 24
        assert sum(1 for gear in equipment.gear if gear.combat is not None) == 3
        assert len(equipment.ammunition) == 4
        assert len(equipment.treasure_weights) == 8
        assert len(load_languages().languages) == 21


class TestClassData:
    def test_fighter_spot_values(self):
        fighter = load_classes().get("fighter")
        row = fighter.row(9)
        assert row.xp == 240_000
        assert (row.thac0, row.attack_bonus) == (14, 5)

    def test_elf_languages_include_gnoll(self):
        assert "gnoll" in load_classes().get("elf").languages

    def test_override_provenance_recorded(self):
        classes = load_classes()
        assert classes.get("elf").overrides_applied == ("xp_tiers",)
        assert classes.get("halfling").overrides_applied == ("xp_tiers",)
        assert classes.get("fighter").overrides_applied == ()

    def test_races_derive_from_class(self):
        classes = load_classes()
        assert classes.get("dwarf").race == "dwarf"
        assert classes.get("cleric").race == "human"

    def test_stature_notes_kept_as_manual_prose(self):
        classes = load_classes()
        assert any("stature" in note for note in classes.get("dwarf").weapons.manual_notes)
        assert any("stature" in note for note in classes.get("halfling").weapons.manual_notes)


class TestEquipmentData:
    def test_plate_mail_spot_values(self):
        plate = load_equipment().get("plate_mail")
        assert (plate.ac, plate.ac_ascending) == (3, 16)
        assert plate.weight_coins == 500

    def test_silver_dagger_material(self):
        assert load_equipment().get("silver_dagger").material is Material.SILVER

    def test_ammunition_weighs_zero(self):
        # Pinned: the SRD's missile weapon weights already include ammunition.
        for ammunition in load_equipment().ammunition:
            assert ammunition.weight_coins == 0

    def test_sling_stones_are_free(self):
        # Pinned: cost cell "Free" compiles to cost 0, lot size 1.
        stones = load_equipment().get("sling_stones")
        assert stones.cost_gp == 0
        assert stones.lot_size == 1

    def test_dual_listed_items_compile_as_gear_with_facets(self):
        equipment = load_equipment()
        weapon_ids = {weapon.id for weapon in equipment.weapons}
        for item_id in ("torch", "holy_water", "oil_flask"):
            assert item_id not in weapon_ids
            assert equipment.get(item_id).combat is not None

    def test_torch_lot_and_facet(self):
        torch = load_equipment().get("torch")
        assert torch.cost_gp == 1
        assert torch.lot_size == 6
        assert torch.combat.damage == "1d4"
        assert torch.combat.qualities == (WeaponQuality.MELEE,)

    def test_splash_weapon_facets(self):
        equipment = load_equipment()
        for item_id in ("holy_water", "oil_flask"):
            facet = equipment.get(item_id).combat
            assert facet.damage == "1d8"
            assert WeaponQuality.SPLASH in facet.qualities
            assert facet.missile_ranges.long.max_feet == 50

    def test_container_capacities(self):
        equipment = load_equipment()
        assert equipment.get("backpack").capacity_coins == 400
        assert equipment.get("sack_large").capacity_coins == 600
        assert equipment.get("sack_small").capacity_coins == 200

    def test_stakes_and_mallet_is_one_kit_item(self):
        stakes = load_equipment().get("stakes_and_mallet")
        assert stakes.lot_size == 1

    def test_treasure_weights(self):
        weights = {row.id: row.weight_coins for row in load_equipment().treasure_weights}
        assert weights == {
            "coin": 1,
            "gem": 1,
            "jewellery": 10,
            "potion": 10,
            "rod": 20,
            "scroll": 1,
            "staff": 40,
            "wand": 10,
        }


class TestLanguageData:
    def test_common_plus_twenty_choosable(self):
        catalog = load_languages()
        assert not catalog.get("common").choosable
        assert sum(1 for language in catalog.languages if language.choosable) == 20

    def test_diacritics_fold_to_ascii(self):
        assert load_languages().get("doppelganger").name == "Doppelgänger"

    def test_class_natives_resolve(self):
        catalog = load_languages()
        for definition in load_classes().classes:
            for language_id in definition.languages:
                assert catalog.get(language_id)
