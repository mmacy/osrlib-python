"""Compiler entry point: parse `srd/`, apply overrides, validate, write `osrlib/data/`.

Run from the repo root:

```sh
uv run python -m tools.srd_compile
```

Output is deterministic and diff-reviewable: JSON with sorted keys, 2-space indent,
LF line endings, a trailing newline, entries sorted by id, and no timestamps —
regenerating from an unchanged `srd/` is byte-identical. Every output validates
through the same frozen osrlib models the shipped loaders use, so a bad parse fails
the build instead of shipping.
"""

import argparse
import json
from pathlib import Path

from pydantic import BaseModel

from osrlib.core.abilities import AbilityTables
from osrlib.core.classes import ClassCatalog
from osrlib.core.items import EquipmentCatalog
from osrlib.core.monsters import MonsterCatalog
from osrlib.core.tables import CombatTables
from osrlib.data import LanguageCatalog

from . import abilities, classes, combat_tables, equipment, languages, monsters
from .overrides import apply_overrides, load_overrides

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _write(out_dir: Path, filename: str, model: BaseModel, source_pages: tuple[str, ...]) -> None:
    document = {
        "_meta": {
            "generator": "tools/srd_compile",
            "license": "Open Game Content under OGL 1.0a; see LICENSE-OGL.md in this directory",
            "source_pages": sorted(source_pages),
        },
        **model.model_dump(mode="json"),
    }
    text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (out_dir / filename).write_text(text, encoding="utf-8")
    print(f"wrote {out_dir / filename}")


def main() -> None:
    """Compile all SRD data files."""
    parser = argparse.ArgumentParser(description="Compile the SRD markdown into osrlib's generated JSON data.")
    parser.add_argument("--srd-dir", type=Path, default=_REPO_ROOT / "srd", help="the scraped SRD directory")
    parser.add_argument(
        "--out-dir", type=Path, default=_REPO_ROOT / "src" / "osrlib" / "data", help="the output directory"
    )
    arguments = parser.parse_args()
    srd_dir: Path = arguments.srd_dir
    out_dir: Path = arguments.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    abilities_data = abilities.compile_abilities(srd_dir)
    _write(out_dir, "abilities.json", AbilityTables.model_validate(abilities_data), (abilities.SOURCE_PAGE,))

    classes_data = classes.compile_classes(srd_dir, abilities_data["prime_requisite"])
    apply_overrides(classes_data["classes"], load_overrides("classes.json"))
    _write(out_dir, "classes.json", ClassCatalog.model_validate(classes_data), classes.SOURCE_PAGES)

    equipment_data = equipment.compile_equipment(srd_dir)
    apply_overrides(
        [*equipment_data["weapons"], *equipment_data["armour"], *equipment_data["gear"], *equipment_data["ammunition"]],
        load_overrides("equipment.json"),
    )
    _write(out_dir, "equipment.json", EquipmentCatalog.model_validate(equipment_data), equipment.SOURCE_PAGES)

    languages_data = languages.compile_languages(srd_dir)
    apply_overrides(languages_data["languages"], load_overrides("languages.json"))
    _write(out_dir, "languages.json", LanguageCatalog.model_validate(languages_data), (languages.SOURCE_PAGE,))

    combat_tables_data = combat_tables.compile_combat_tables(srd_dir)
    _write(
        out_dir,
        "combat_tables.json",
        CombatTables.model_validate(combat_tables_data),
        combat_tables.SOURCE_PAGES,
    )

    monsters_data = monsters.compile_monsters(srd_dir)
    apply_overrides(monsters_data["monsters"], load_overrides("monsters.json"))
    monsters.validate_xp(monsters_data["monsters"], combat_tables_data["xp_awards"])
    _write(out_dir, "monsters.json", MonsterCatalog.model_validate(monsters_data), monsters.source_pages(srd_dir))


if __name__ == "__main__":
    main()
