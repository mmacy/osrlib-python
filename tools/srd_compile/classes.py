"""Parser for the seven SRD class pages → `classes.json`.

Hazards handled here: class stat blocks are two-column key/value tables (the demihuman
pages carry a `Demihuman Class` header row); progression tables have multi-row spanned
headers (`Saving Throws`, `Spells`); HD entries above name level are flat bonuses with
a footnote asterisk (`9d8+2*`) meaning CON no longer applies — the asterisk is data;
THAC0 is dual-format (`19 [0]`); numbers use commas.

XP-modifier tiers: single-prime-requisite classes get the standard prime requisite
table expressed as best-first tiers (the penalty rows only work under
first-match-wins). The elf's and halfling's multi-prime-requisite tiers are prose, not
tables, and are hand-authored in `overrides/classes.json` rather than brittle prose
parsing — the parser emits no tiers for them.

Level titles come from the Advancement page's per-class lists.
"""

import re
from pathlib import Path

from .pipetable import (
    parse_int,
    parse_range,
    parse_tables,
    section_prose,
    strip_links,
    tables_after_heading,
)

SOURCE_PAGES = (
    "Cleric.md",
    "Dwarf.md",
    "Elf.md",
    "Fighter.md",
    "Halfling.md",
    "Magic-User.md",
    "Thief.md",
    "Advancement.md",
    "Ability_Scores.md",
)

_CLASS_PAGES = {
    "cleric": "Cleric.md",
    "dwarf": "Dwarf.md",
    "elf": "Elf.md",
    "fighter": "Fighter.md",
    "halfling": "Halfling.md",
    "magic_user": "Magic-User.md",
    "thief": "Thief.md",
}

_TITLE_LIST_NAMES = {
    "Cleric": "cleric",
    "Dwarf": "dwarf",
    "Elf": "elf",
    "Fighter": "fighter",
    "Halfling": "halfling",
    "Magic-user": "magic_user",
    "Thief": "thief",
}

_DEMIHUMAN_RACES = {"dwarf", "elf", "halfling"}

_ABILITY_CODES = {"STR": "str", "INT": "int", "WIS": "wis", "DEX": "dex", "CON": "con", "CHA": "cha"}

_HD_PATTERN = re.compile(r"(\d+)d(\d+)(?:\+(\d+))?(\*)?")
_THAC0_PATTERN = re.compile(r"(\d+)\s*\[\+?(\d+)\]")

# Structured class-ability tags: (tag, section heading on the class page, params, manual).
# Tags now, procedures in Phases 2-4; prose is pulled from the named section so the SRD
# text stays the authority.
_CLASS_ABILITIES: dict[str, tuple[tuple[str, str, dict[str, int | str], bool], ...]] = {
    "cleric": (
        ("divine_magic", "Divine Magic", {"spell_list": "cleric"}, False),
        ("turn_undead", "Turning the Undead", {}, False),
    ),
    "dwarf": (
        ("detect_construction_tricks", "Detect Construction Tricks", {"chance_in_six": 2}, False),
        ("detect_room_traps", "Detect Room Traps", {"chance_in_six": 2}, False),
        ("infravision", "Infravision", {"range_feet": 60}, False),
        ("listening_at_doors", "Listening at Doors", {"chance_in_six": 2}, False),
    ),
    "elf": (
        ("arcane_magic", "Arcane Magic", {"spell_list": "magic_user"}, False),
        ("detect_secret_doors", "Detect Secret Doors", {"chance_in_six": 2}, False),
        ("ghoul_paralysis_immunity", "Immunity to Ghoul Paralysis", {}, False),
        ("infravision", "Infravision", {"range_feet": 60}, False),
        ("listening_at_doors", "Listening at Doors", {"chance_in_six": 2}, False),
    ),
    "fighter": (),
    "halfling": (
        ("defensive_bonus", "Defensive Bonus", {"ac_bonus": 2, "versus": "large"}, False),
        ("hiding", "Hiding", {"woods_pct": 90, "dungeon_chance_in_six": 2}, False),
        ("initiative_bonus", "Initiative Bonus (Optional Rule)", {"bonus": 1}, False),
        ("listening_at_doors", "Listening at Doors", {"chance_in_six": 2}, False),
        ("missile_attack_bonus", "Missile Attack Bonus", {"bonus": 1}, False),
    ),
    "magic_user": (("arcane_magic", "Arcane Magic", {"spell_list": "magic_user"}, False),),
    "thief": (
        ("back_stab", "Back-stab", {"attack_bonus": 4, "damage_multiplier": 2}, False),
        ("read_languages", "Read Languages", {"pct": 80, "min_level": 4}, False),
        ("scroll_use", "Scroll Use", {"error_pct": 10, "min_level": 10}, False),
    ),
}


def _stat_block(page: str) -> dict[str, str]:
    """Parse a class page's key/value stat block (the first pipe table)."""
    table = parse_tables(page)[0]
    entries: dict[str, str] = {}
    for row in table:  # a "Demihuman Class" header row parses as a harmless unknown key
        if len(row) >= 2 and row[0]:
            entries[row[0]] = row[1]
    for key in ("Requirements", "Prime requisite", "Hit Dice", "Maximum level", "Armour", "Weapons", "Languages"):
        if key not in entries:
            raise ValueError(f"class stat block is missing {key!r}")
    return entries


def _parse_requirements(cell: str) -> dict[str, int]:
    if cell == "None":
        return {}
    requirements: dict[str, int] = {}
    for match in re.finditer(r"[Mm]inimum ([A-Z]{3}) (\d+)", cell):
        requirements[_ABILITY_CODES[match[1]]] = int(match[2])
    if not requirements:
        raise ValueError(f"unparseable requirements cell {cell!r}")
    return requirements


def _parse_prime_requisites(cell: str) -> list[str]:
    return [_ABILITY_CODES[code.strip()] for code in cell.split(" and ")]


def _parse_armour_policy(cell: str) -> dict[str, object]:
    if cell == "None":
        return {"kind": "none", "shields_allowed": False}
    shields = "including shields" in cell
    if cell.startswith("Any"):
        return {"kind": "any", "shields_allowed": shields}
    if cell.startswith("Leather"):
        return {"kind": "leather_only", "shields_allowed": shields}
    raise ValueError(f"unparseable armour cell {cell!r}")


def _parse_weapon_policy(cell: str, combat_prose: str) -> dict[str, object]:
    if cell == "Any":
        return {"kind": "any", "weapon_ids": [], "manual_notes": []}
    if cell == "Dagger":
        return {"kind": "allowed", "weapon_ids": ["dagger"], "manual_notes": []}
    if cell == "Any blunt weapons":
        match = re.search(r"following weapons: ([^.]*)\.", combat_prose)
        if match is None:
            raise ValueError("cleric weapon list sentence not found in the Combat section")
        ids = [name.strip().lower().replace(" ", "_") for name in match[1].split(",")]
        return {"kind": "allowed", "weapon_ids": sorted(ids), "manual_notes": []}
    # The stature policies: the mechanizable part is the explicit longbow/two-handed
    # sword prohibition; the referee-judgment stature prose is kept as manual notes.
    if "cannot use longbows or two-handed swords" not in combat_prose:
        raise ValueError(f"expected the longbow/two-handed sword prohibition for weapons cell {cell!r}")
    notes = [sentence.strip() + "." for sentence in combat_prose.split(".") if "stature" in sentence]
    if not notes:
        raise ValueError(f"no stature prose found for weapons cell {cell!r}")
    return {"kind": "forbidden", "weapon_ids": ["long_bow", "two_handed_sword"], "manual_notes": notes}


def _parse_languages(cell: str) -> list[str]:
    names = [name.strip() for name in cell.split(",")]
    ids = [name.lower() for name in names if name != "Alignment"]
    return ids


def _parse_hd(cell: str) -> dict[str, object]:
    match = _HD_PATTERN.fullmatch(cell)
    if match is None:
        raise ValueError(f"unparseable HD cell {cell!r}")
    return {
        "count": int(match[1]),
        "die": int(match[2]),
        "bonus": int(match[3]) if match[3] else 0,
        "con_applies": match[4] is None,
    }


def _parse_progression(page: str, class_name: str) -> list[dict[str, object]]:
    tables = tables_after_heading(page, f"{class_name} Level Progression")
    if not tables:
        raise ValueError(f"no progression table found for {class_name}")
    table = tables[0]
    header_index = next(index for index, row in enumerate(table) if row and row[0] == "Level")
    header = table[header_index]
    expected_prefix = ["Level", "XP", "HD", "THAC0", "D", "W", "P", "B", "S"]
    if header[: len(expected_prefix)] != expected_prefix:
        raise ValueError(f"unexpected progression columns for {class_name}: {header}")
    spell_levels = header[len(expected_prefix) :]
    if spell_levels != [str(index + 1) for index in range(len(spell_levels))]:
        raise ValueError(f"unexpected spell slot columns for {class_name}: {spell_levels}")
    rows = []
    for row in table[header_index + 1 :]:
        thac0_match = _THAC0_PATTERN.fullmatch(row[3])
        if thac0_match is None:
            raise ValueError(f"unparseable THAC0 cell {row[3]!r}")
        rows.append(
            {
                "level": int(row[0]),
                "xp": parse_int(row[1]),
                "hit_dice": _parse_hd(row[2]),
                "thac0": int(thac0_match[1]),
                "attack_bonus": int(thac0_match[2]),
                "saves": {
                    "death": int(row[4]),
                    "wands": int(row[5]),
                    "paralysis": int(row[6]),
                    "breath": int(row[7]),
                    "spells": int(row[8]),
                },
                "spell_slots": [0 if cell == "—" else int(cell) for cell in row[9:]],
            }
        )
    return rows


def _parse_thief_skills(page: str) -> list[dict[str, int]]:
    tables = tables_after_heading(page, "Thief Skills")
    table = tables[0]
    header_index = next(index for index, row in enumerate(table) if row and row[0] == "Level")
    if table[header_index] != ["Level", "CS", "TR", "HN", "HS", "MS", "OL", "PP"]:
        raise ValueError(f"unexpected thief skill columns: {table[header_index]}")
    rows = []
    for row in table[header_index + 1 :]:
        rows.append(
            {
                "level": int(row[0]),
                "climb_sheer_surfaces": int(row[1]),
                "find_remove_treasure_traps": int(row[2]),
                "hear_noise": parse_range(row[3])[1],
                "hide_in_shadows": int(row[4]),
                "move_silently": int(row[5]),
                "open_locks": int(row[6]),
                "pick_pockets": int(row[7]),
            }
        )
    return rows


def _parse_level_titles(advancement_page: str) -> dict[str, list[str]]:
    titles: dict[str, list[str]] = {}
    for line in advancement_page.splitlines():
        stripped = strip_links(line.strip())
        match = re.match(r"- \*\*(.+?):\*\* (.+)", stripped)
        if match and match[1] in _TITLE_LIST_NAMES:
            class_id = _TITLE_LIST_NAMES[match[1]]
            titles[class_id] = [title.strip() for title in match[2].rstrip(".").split(",")]
    missing = set(_CLASS_PAGES) - set(titles)
    if missing:
        raise ValueError(f"level title lists missing for {sorted(missing)}")
    return titles


def _standard_xp_tiers(prime_requisite_rows: list[dict[str, int]], prime_requisite: str) -> list[dict[str, object]]:
    """Express the standard single-prime-requisite table as best-first tiers.

    First-match-wins makes the penalty rows work: a score of 7 falls past the
    higher-minimum tiers and lands on the -10% tier (minimum 6).
    """
    tiers = sorted(prime_requisite_rows, key=lambda row: -row["xp_modifier_pct"])
    return [{"modifier_pct": row["xp_modifier_pct"], "minimums": {prime_requisite: row["min_score"]}} for row in tiers]


def _parse_may_not_lower(page: str) -> list[str]:
    return [_ABILITY_CODES[code] for code in re.findall(r"may not lower ([A-Z]{3})", page)]


def compile_classes(srd_dir: Path, prime_requisite_rows: list[dict[str, int]]) -> dict[str, object]:
    """Compile the seven class pages into the `classes.json` structure (sans `_meta`).

    Args:
        srd_dir: The directory holding the scraped SRD markdown.
        prime_requisite_rows: The compiled prime requisite table rows from
            [`compile_abilities`][tools.srd_compile.abilities.compile_abilities],
            used to build the standard single-prime-requisite XP tiers.

    Returns:
        The raw dict ready for `ClassCatalog` validation, entries sorted by id.
    """
    advancement = (srd_dir / "Advancement.md").read_text(encoding="utf-8")
    titles = _parse_level_titles(advancement)
    classes = []
    for class_id, filename in sorted(_CLASS_PAGES.items()):
        page = (srd_dir / filename).read_text(encoding="utf-8")
        name = strip_links(page.splitlines()[0].lstrip("# ").strip())
        block = _stat_block(page)
        combat_prose = section_prose(page, "Combat")
        prime_requisites = _parse_prime_requisites(block["Prime requisite"])
        if len(prime_requisites) == 1:
            xp_tiers = _standard_xp_tiers(prime_requisite_rows, prime_requisites[0])
        else:
            xp_tiers = []  # multi-PR tiers are prose; hand-authored in overrides/classes.json
        abilities = [
            {
                "tag": tag,
                "name": heading,
                "prose": section_prose(page, heading),
                "manual": manual,
                "params": params,
            }
            for tag, heading, params, manual in _CLASS_ABILITIES[class_id]
        ]
        classes.append(
            {
                "id": class_id,
                "name": name,
                "race": class_id if class_id in _DEMIHUMAN_RACES else "human",
                "requirements": _parse_requirements(block["Requirements"]),
                "prime_requisites": prime_requisites,
                "xp_tiers": xp_tiers,
                "hit_die": _parse_hd(block["Hit Dice"])["die"],
                "max_level": int(block["Maximum level"]),
                "armour": _parse_armour_policy(block["Armour"]),
                "weapons": _parse_weapon_policy(block["Weapons"], combat_prose),
                "languages": _parse_languages(block["Languages"]),
                "may_not_lower": _parse_may_not_lower(page),
                "abilities": abilities,
                "thief_skills": _parse_thief_skills(page) if class_id == "thief" else [],
                "level_titles": titles[class_id],
                "progression": _parse_progression(page, name),
            }
        )
    return {"classes": classes}
