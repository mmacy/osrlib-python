"""Parser for the SRD's Ability Scores page → `abilities.json`.

The six modifier tables ship as score bands exactly as the SRD prints them. Hazards:
the CHA table has a two-row spanned header, the STR open-doors column uses
hyphen-minus (`1-in-6`) while score ranges use en-dashes (`4–5`), and the INT spoken
languages column mixes prose (`Native (broken speech)`) with counts.
"""

from pathlib import Path

from .pipetable import parse_modifier, parse_range, parse_tables

SOURCE_PAGE = "Ability_Scores.md"

_LITERACY = {"Illiterate": "illiterate", "Basic": "basic", "Literate": "literate"}


def _data_rows(tables: list[list[list[str]]], first_header: str) -> list[list[str]]:
    """Return the data rows of the table whose column-header row starts with `first_header`."""
    for table in tables:
        for index, row in enumerate(table):
            if row and row[0] == first_header:
                return table[index + 1 :]
    raise ValueError(f"no table with header column {first_header!r} found")


def _spoken_languages(cell: str) -> tuple[int, bool]:
    """Parse the INT spoken-languages cell into (additional count, broken speech)."""
    if cell == "Native (broken speech)":
        return 0, True
    if cell == "Native":
        return 0, False
    prefix, _, remainder = cell.partition("+")
    if prefix.strip() == "Native" and remainder.strip().endswith("additional"):
        return int(remainder.strip().split()[0]), False
    raise ValueError(f"unparseable spoken-languages cell {cell!r}")


def compile_abilities(srd_dir: Path) -> dict[str, object]:
    """Compile the ability tables into the `abilities.json` structure (sans `_meta`).

    Args:
        srd_dir: The directory holding the scraped SRD markdown.

    Returns:
        The raw dict ready for `AbilityTables` validation.
    """
    tables = parse_tables((srd_dir / SOURCE_PAGE).read_text(encoding="utf-8"))

    strength = []
    for row in _data_rows(tables, "STR"):
        low, high = parse_range(row[0])
        open_doors = int(row[2].split("-in-")[0])
        strength.append(
            {"min_score": low, "max_score": high, "melee": parse_modifier(row[1]), "open_doors": open_doors}
        )

    intelligence = []
    for row in _data_rows(tables, "INT"):
        low, high = parse_range(row[0])
        additional, broken = _spoken_languages(row[1])
        intelligence.append(
            {
                "min_score": low,
                "max_score": high,
                "additional_languages": additional,
                "literacy": _LITERACY[row[2]],
                "broken_speech": broken,
            }
        )

    wisdom = []
    for row in _data_rows(tables, "WIS"):
        low, high = parse_range(row[0])
        wisdom.append({"min_score": low, "max_score": high, "magic_saves": parse_modifier(row[1])})

    dexterity = []
    for row in _data_rows(tables, "DEX"):
        low, high = parse_range(row[0])
        dexterity.append(
            {
                "min_score": low,
                "max_score": high,
                "ac": parse_modifier(row[1]),
                "missile": parse_modifier(row[2]),
                "initiative": parse_modifier(row[3]),
            }
        )

    constitution = []
    for row in _data_rows(tables, "CON"):
        low, high = parse_range(row[0])
        constitution.append({"min_score": low, "max_score": high, "hit_points": parse_modifier(row[1])})

    charisma = []
    for row in _data_rows(tables, "CHA"):
        low, high = parse_range(row[0])
        charisma.append(
            {
                "min_score": low,
                "max_score": high,
                "npc_reactions": parse_modifier(row[1]),
                "max_retainers": int(row[2]),
                "retainer_loyalty": int(row[3]),
            }
        )

    prime_requisite = []
    for row in _data_rows(tables, "Prime Requisite"):
        low, high = parse_range(row[0])
        pct = 0 if row[1] == "None" else int(row[1].replace("%", "").replace("+", ""))
        prime_requisite.append({"min_score": low, "max_score": high, "xp_modifier_pct": pct})

    return {
        "strength": strength,
        "intelligence": intelligence,
        "wisdom": wisdom,
        "dexterity": dexterity,
        "constitution": constitution,
        "charisma": charisma,
        "prime_requisite": prime_requisite,
    }
