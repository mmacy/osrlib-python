"""Parser for the combat tables → `combat_tables.json`.

Four tables: the attack matrix (16 THAC0 rows from `20 [-1]`/NH to `5 [+14]`, AC
columns −3..9, with the monster-HD row labels), the monster saving-throw bands (NH,
1–3, ... 22 or more), the XP-awards-for-defeated-monsters table including the
separate `N+` rows for bonus hit-point modifiers, and the turning-undead table from
`Cleric.md` (11 cleric-level rows × 8 monster-HD columns, cells `—`/number/`T`/`D`).
The matrix's AC-0 column is printed bold — data, stripped here.
"""

import re
from pathlib import Path

from .pipetable import parse_int, tables_after_heading

SOURCE_PAGES = ("Combat_Tables.md", "Awarding_XP.md", "Cleric.md")

_TURNING_COLUMNS = ["1", "2", "2*", "3", "4", "5", "6", "7-9"]
_TURNING_LEVELS = [*(str(level) for level in range(1, 11)), "11+"]

_THAC0 = re.compile(r"(\d+) \[([+-]?\d+)\]")
_AC_COLUMNS = tuple(range(-3, 10))


def compile_combat_tables(srd_dir: Path) -> dict[str, object]:
    """Compile the combat tables into the `combat_tables.json` structure (sans `_meta`).

    Args:
        srd_dir: The directory holding the scraped SRD markdown.

    Returns:
        The raw dict ready for `CombatTables` validation.
    """
    combat_page = (srd_dir / "Combat_Tables.md").read_text(encoding="utf-8")
    xp_page = (srd_dir / "Awarding_XP.md").read_text(encoding="utf-8")
    cleric_page = (srd_dir / "Cleric.md").read_text(encoding="utf-8")
    return {
        "attack_matrix": {"rows": _parse_matrix(combat_page)},
        "monster_saves": _parse_save_bands(combat_page),
        "xp_awards": _parse_xp_awards(xp_page),
        "turning": {"rows": _parse_turning(cleric_page)},
    }


def _parse_matrix(page: str) -> list[dict[str, object]]:
    table = tables_after_heading(page, "Attack Matrix")[0]
    header_index = next(index for index, row in enumerate(table) if row and row[0] == "Monster HD")
    if table[header_index][2:] != [str(ac) for ac in _AC_COLUMNS]:
        raise ValueError(f"unexpected attack matrix AC columns: {table[header_index][2:]}")
    rows = []
    for row in table[header_index + 1 :]:
        thac0_match = _THAC0.fullmatch(row[1])
        if thac0_match is None:
            raise ValueError(f"unparseable matrix THAC0 cell {row[1]!r}")
        cells = [int(cell.replace("**", "")) for cell in row[2:]]
        if len(cells) != len(_AC_COLUMNS):
            raise ValueError(f"matrix row {row[0]!r} has {len(cells)} cells")
        rows.append(
            {
                "hd_label": row[0],
                "thac0": int(thac0_match[1]),
                "attack_bonus": int(thac0_match[2]),
                "by_ac": dict(zip((str(ac) for ac in _AC_COLUMNS), cells, strict=True)),
            }
        )
    if len(rows) != 16:
        raise ValueError(f"expected 16 attack matrix rows, found {len(rows)}")
    return rows


def _parse_save_bands(page: str) -> list[dict[str, object]]:
    table = tables_after_heading(page, "Monster Saving Throws")[0]
    header_index = next(index for index, row in enumerate(table) if row and row[0] == "Hit Dice")
    if table[header_index] != ["Hit Dice", "Death", "Wands", "Paralysis", "Breath", "Spells"]:
        raise ValueError(f"unexpected save band columns: {table[header_index]}")
    bands = []
    for row in table[header_index + 1 :]:
        label = row[0]
        if label == "NH":
            min_hd, max_hd = None, 0
        elif label.endswith("or more"):
            min_hd, max_hd = int(label.split()[0]), None
        else:
            low, high = label.split("–")
            min_hd, max_hd = int(low), int(high)
        bands.append(
            {
                "label": label,
                "min_hd": min_hd,
                "max_hd": max_hd,
                "saves": {
                    "death": int(row[1]),
                    "wands": int(row[2]),
                    "paralysis": int(row[3]),
                    "breath": int(row[4]),
                    "spells": int(row[5]),
                },
            }
        )
    if len(bands) != 9:
        raise ValueError(f"expected 9 monster save bands, found {len(bands)}")
    return bands


def _parse_turning(page: str) -> list[dict[str, object]]:
    table = tables_after_heading(page, "Turning the Undead")[0]
    header_index = next(index for index, row in enumerate(table) if row and row[0] == "Level")
    if table[header_index][1:] != _TURNING_COLUMNS:
        raise ValueError(f"unexpected turning columns: {table[header_index][1:]}")
    rows = []
    for row in table[header_index + 1 :]:
        cells = dict(zip(_TURNING_COLUMNS, row[1:], strict=True))
        rows.append({"label": row[0], "cells": cells})
    if [row["label"] for row in rows] != _TURNING_LEVELS:
        raise ValueError(f"unexpected turning row labels: {[row['label'] for row in rows]}")
    return rows


def _parse_xp_awards(page: str) -> list[dict[str, object]]:
    table = tables_after_heading(page, "Defeated Monsters")[0]
    header_index = next(index for index, row in enumerate(table) if row and row[0] == "Monster HD")
    rows = []
    for row in table[header_index + 1 :]:
        rows.append({"label": row[0], "base": parse_int(row[1]), "bonus": parse_int(row[2])})
    if len(rows) != 20:
        raise ValueError(f"expected 20 XP award rows, found {len(rows)}")
    return rows
