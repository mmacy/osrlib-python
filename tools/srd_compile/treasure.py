"""Parser for the treasure tables → `treasure.json`.

Sources: `Treasure_Types.md` (the A–V lists — `###`-headed bullet lists, not pipe
tables), `Gems_and_Jewellery.md` (the d20 gem value table and the jewellery dice),
`Magic_Items_%28General%29.md` (the master *Magic Item Type* table with both printed
d% columns), and `Designing_a_Dungeon.md` (the room-contents d6 and the
unguarded-treasure bands).

The entry grammar is fixed and shared (the unguarded bands and the treasure maps in
`magic_items.py` reuse it): an optional `NN%: ` gate, then either a dice quantity
with an optional `× K` multiplier and a glued coin suffix (`1d6 × 1,000cp`, `3d8cp`
— `×` is U+00D7, thousands commas strip, and the multiplier folds into the dice
expression's `×K`), a gem or jewellery count, or a structured magic-item clause.
Magic clauses parse positionally, never by free comma-tokenizing: `3 magic items`
(any), `1 magic sword, suit of armour, or weapon` (Type B's category pool),
`3 magic items (not weapons), plus 1 potion, plus 1 scroll` (an any-roll with an
exclusion plus fixed extras), and `2d4 potions` (a diced category count).

The room-contents table mixes dash conventions (an en-dash d6 column beside
ASCII-hyphen `1-in-6` chances), and the unguarded bands are `**Level N:**` prose
lines whose schema is deliberately non-uniform: sp entries are ungated everywhere,
and the gp entries are 50%-gated on the first two bands only.
"""

import re
from pathlib import Path

from .pipetable import parse_range, section_prose, tables_after_heading

SOURCE_PAGES = (
    "Treasure_Types.md",
    "Gems_and_Jewellery.md",
    "Magic_Items_%28General%29.md",
    "Designing_a_Dungeon.md",
)

_SECTION_HEADINGS = {
    "## Hoards: A–O": "hoard",
    "## Individual Treasure: P–T": "individual",
    "## Group Treasure: U–V": "group",
}

_TYPE_HEADING = re.compile(r"^### Type ([A-V]) \(([\d.,]+)gp average\)$")
_GATE = re.compile(r"^(\d+)%: (.+)$")
_COIN = re.compile(r"^(\d*d\d+)(?: × ([\d,]+))?(cp|sp|ep|gp|pp)$")
_GEMS = re.compile(r"^(\d*d\d+)(?: × ([\d,]+))? gems$")
_JEWELLERY = re.compile(r"^(\d*d\d+)(?: × ([\d,]+))? pieces of jewellery$")
_MAGIC_ANY = re.compile(r"^(\d+) magic items?( \(not weapons\)| \(no swords\))?$")
_MAGIC_POOL = re.compile(r"^(\d+) magic sword, suit of armour, or weapon$")
_MAGIC_CATEGORY_DICE = re.compile(r"^(\d*d\d+) (potion|scroll)s$")
_MAGIC_CATEGORY_FIXED = re.compile(r"^(\d+) (potion|scroll)s?$")
_UNGUARDED_LINE = re.compile(r"^Level (\d+)(?:–(\d+))?: (.+)$")
_STOCKING_CHANCE = re.compile(r"^(\d)-in-6$")

_EXCLUSIONS = {" (not weapons)": "weapon", " (no swords)": "sword"}

_MASTER_TYPES = {
    "Armour or Shield": "armour",
    "Miscellaneous Item": "misc",
    "Potion": "potion",
    "Ring": "ring",
    "Rod / Staff / Wand": "rod_staff_wand",
    "Scroll or Map": "scroll",
    "Sword": "sword",
    "Weapon": "weapon",
}

_STOCKING_CONTENTS = {"Empty": "empty", "Monster": "monster", "Special": "special", "Trap": "trap"}


def _fold_dice(dice: str, multiplier: str | None) -> str:
    """Fold a printed `× K` multiplier into the dice expression's own `×K`."""
    if multiplier is None:
        return dice
    return f"{dice}×{multiplier.replace(',', '')}"


def parse_magic_clause(text: str) -> list[dict[str, object]]:
    """Parse a magic-item clause into structured allotments.

    Args:
        text: The clause after the gate, e.g. `"3 magic items (not weapons), plus 1
            potion, plus 1 scroll"`.

    Returns:
        The allotment dicts, in printed order.

    Raises:
        ValueError: If any clause part doesn't match the pinned grammar.
    """
    allotments: list[dict[str, object]] = []
    for part in text.replace(", plus ", " plus ").split(" plus "):
        pool = _MAGIC_POOL.fullmatch(part)
        if pool is not None:
            allotments.append({"kind": "pool", "categories": ["sword", "armour", "weapon"], "count": int(pool[1])})
            continue
        any_match = _MAGIC_ANY.fullmatch(part)
        if any_match is not None:
            allotment: dict[str, object] = {"kind": "any", "count": int(any_match[1])}
            if any_match[2] is not None:
                allotment["exclude"] = [_EXCLUSIONS[any_match[2]]]
            allotments.append(allotment)
            continue
        diced = _MAGIC_CATEGORY_DICE.fullmatch(part)
        if diced is not None:
            allotments.append({"kind": "category", "categories": [diced[2]], "count_dice": diced[1]})
            continue
        fixed = _MAGIC_CATEGORY_FIXED.fullmatch(part)
        if fixed is not None:
            allotments.append({"kind": "category", "categories": [fixed[2]], "count": int(fixed[1])})
            continue
        raise ValueError(f"unparseable magic clause part {part!r}")
    return allotments


def parse_treasure_segment(text: str) -> dict[str, object]:
    """Parse one gated entry segment on the pinned grammar.

    Args:
        text: The segment, e.g. `"25%: 1d6 × 1,000cp"` or `"3d8cp"` (no trailing
            period).

    Returns:
        The entry dict: `chance_pct` plus exactly one payload.

    Raises:
        ValueError: If the segment doesn't match the grammar.
    """
    entry: dict[str, object] = {}
    gated = _GATE.fullmatch(text)
    if gated is not None:
        entry["chance_pct"] = int(gated[1])
        text = gated[2]
    coin = _COIN.fullmatch(text)
    if coin is not None:
        entry["coins"] = {"denomination": coin[3], "dice": _fold_dice(coin[1], coin[2])}
        return entry
    gems = _GEMS.fullmatch(text)
    if gems is not None:
        entry["gems_dice"] = _fold_dice(gems[1], gems[2])
        return entry
    jewellery = _JEWELLERY.fullmatch(text)
    if jewellery is not None:
        entry["jewellery_dice"] = _fold_dice(jewellery[1], jewellery[2])
        return entry
    entry["magic"] = parse_magic_clause(text)
    return entry


def _parse_treasure_types(page: str) -> list[dict[str, object]]:
    types: list[dict[str, object]] = []
    section: str | None = None
    current: dict[str, object] | None = None
    for line in page.splitlines():
        stripped = line.strip()
        if stripped in _SECTION_HEADINGS:
            section = _SECTION_HEADINGS[stripped]
            continue
        heading = _TYPE_HEADING.fullmatch(stripped)
        if heading is not None:
            if section is None:
                raise ValueError(f"treasure type {heading[1]!r} appears before any section heading")
            current = {
                "letter": heading[1],
                "kind": section,
                "average_gp": float(heading[2].replace(",", "")),
                "entries": [],
            }
            types.append(current)
            continue
        if stripped.startswith("## "):
            current = None
            continue
        if stripped.startswith("- ") and current is not None:
            current["entries"].append(parse_treasure_segment(stripped.removeprefix("- ").removesuffix(".")))
    if [entry["letter"] for entry in types] != [chr(code) for code in range(ord("A"), ord("V") + 1)]:
        raise ValueError(f"expected treasure types A-V in order, got {[entry['letter'] for entry in types]}")
    return types


def _parse_gems(page: str) -> dict[str, object]:
    bands = []
    for row in tables_after_heading(page, "Gems")[0][1:]:
        roll_min, roll_max = parse_range(row[0])
        if not row[1].endswith("gp"):
            raise ValueError(f"unparseable gem value cell {row[1]!r}")
        bands.append(
            {"roll_min": roll_min, "roll_max": roll_max, "value_gp": int(row[1].removesuffix("gp").replace(",", ""))}
        )
    jewellery_prose = section_prose(page, "Jewellery")
    match = re.search(r"worth (\d+)d(\d+) × (\d+)gp", jewellery_prose)
    if match is None:
        raise ValueError("cannot find the jewellery value dice in Gems_and_Jewellery.md")
    return {
        "bands": bands,
        "jewellery_dice": f"{match[1]}d{match[2]}×{match[3]}",
        "manual_notes": (
            section_prose(page, "Damaged Jewellery"),
            section_prose(page, "Combining Values"),
        ),
    }


def _parse_percent_band(text: str) -> tuple[int, int]:
    """Parse a d% band (`16–40`, `00`): `00` reads as 100."""
    low, high = text.split("–") if "–" in text else (text, text)
    return (100 if low == "00" else int(low)), (100 if high == "00" else int(high))


def _parse_master_table(page: str) -> dict[str, object]:
    table = tables_after_heading(page, "Basic and Expert Magic Items")[0]
    if table[0] != ["B: d%", "X: d%", "Type of Item"]:
        raise ValueError(f"unexpected magic item type header {table[0]!r}")
    rows = []
    for basic_cell, expert_cell, name in table[1:]:
        if name not in _MASTER_TYPES:
            raise ValueError(f"unknown magic item type {name!r}")
        basic_min, basic_max = _parse_percent_band(basic_cell)
        expert_min, expert_max = _parse_percent_band(expert_cell)
        rows.append(
            {
                "category": _MASTER_TYPES[name],
                "basic_min": basic_min,
                "basic_max": basic_max,
                "expert_min": expert_min,
                "expert_max": expert_max,
            }
        )
    return {"rows": rows}


def _parse_stocking(page: str) -> dict[str, object]:
    table = tables_after_heading(page, "Random Room Stocking")[0]
    if table[0] != ["d6", "Contents", "Chance of Treasure"]:
        raise ValueError(f"unexpected room contents header {table[0]!r}")
    rows = []
    for roll_cell, contents, chance_cell in table[1:]:
        roll_min, roll_max = parse_range(roll_cell)
        if chance_cell == "None":
            chance = 0
        else:
            match = _STOCKING_CHANCE.fullmatch(chance_cell)
            if match is None:
                raise ValueError(f"unparseable treasure chance {chance_cell!r}")
            chance = int(match[1])
        rows.append(
            {
                "roll_min": roll_min,
                "roll_max": roll_max,
                "contents": _STOCKING_CONTENTS[contents],
                "treasure_chance_in_six": chance,
            }
        )
    return {"rows": rows}


def _parse_unguarded(page: str) -> dict[str, object]:
    bands = []
    for line in section_prose(page, "Treasure in Empty / Trapped Rooms").splitlines():
        match = _UNGUARDED_LINE.fullmatch(line)
        if match is None:
            raise ValueError(f"unparseable unguarded treasure line {line!r}")
        min_level = int(match[1])
        max_level = int(match[2]) if match[2] is not None else min_level
        label = f"Level {match[1]}" if match[2] is None else f"Level {match[1]}–{match[2]}"
        entries = [parse_treasure_segment(segment) for segment in match[3].removesuffix(".").split("; ")]
        bands.append({"label": label, "min_level": min_level, "max_level": max_level, "entries": entries})
    return {"bands": bands}


def compile_treasure(srd_dir: Path) -> dict[str, object]:
    """Compile the treasure tables into the `treasure.json` structure (sans `_meta`).

    Args:
        srd_dir: The directory holding the scraped SRD markdown.

    Returns:
        The raw dict ready for `TreasureTables` validation.

    Raises:
        ValueError: If any printed entry, band, or table cell doesn't parse.
    """
    types_page = (srd_dir / "Treasure_Types.md").read_text(encoding="utf-8")
    gems_page = (srd_dir / "Gems_and_Jewellery.md").read_text(encoding="utf-8")
    general_page = (srd_dir / "Magic_Items_%28General%29.md").read_text(encoding="utf-8")
    dungeon_page = (srd_dir / "Designing_a_Dungeon.md").read_text(encoding="utf-8")
    return {
        "treasure_types": _parse_treasure_types(types_page),
        "gems": _parse_gems(gems_page),
        "magic_item_types": _parse_master_table(general_page),
        "stocking": _parse_stocking(dungeon_page),
        "unguarded": _parse_unguarded(dungeon_page),
    }
