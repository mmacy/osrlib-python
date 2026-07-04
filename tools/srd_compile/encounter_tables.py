"""Parser for the dungeon encounter tables → `encounter_tables.json`.

Source: `Dungeon_Encounters.md` — two pipe tables carrying six level columns
(`Level 1`, `2`, `3`, `4–5`, `6-7`, `8+` — note the en-dash/hyphen inconsistency in
the printed headers) of twenty d20 rows each. Every row's number-appearing dice are
the table's own — pinned per the SRD's note and the spec: the table value overrides
the monster description's.

The cell-name → id mapping is hand-curated in `_TABLE_ENTRIES` (the
`_SPELL_MECHANICS` precedent), because the printed links are unresolvable: they point
at per-variant pages that don't exist (`Beetle, Oil` → the `Beetle, Giant` page's
`oil_beetle`), use raw commas and single encoding against double-encoded filenames
(`Doppelg%C3%A4nger`), and disagree with variant naming (`Cat, Mountain Lion` →
`mountain_lion`, `Wolf` → `normal_wolf`, `Dwarf` → `dwarf_monster`, `Neanderthal` →
`neanderthal_caveman`). Every reference is asserted to resolve against the compiled
monster catalog, exactly like *conjure elemental*'s in Phase 3.

Overrides apply *inside* this compiler, between parsing and entry resolution, because
they normalize printed cell names the resolution step keys on: the "Basic Adventures"
typo (row 2 of the Level 3 column — the spec's own overrides example) and the deeper
tables' singular "Expert Adventurer" label.
"""

import re
from pathlib import Path

from .overrides import apply_overrides, load_overrides
from .pipetable import parse_range, section_prose, tables_after_heading

SOURCE_PAGES = ("Dungeon_Encounters.md", "Adventuring_Parties.md")

# (table id, printed column header, min level, max level or None for the open band).
_TABLES = (
    ("level_1", "Level 1", 1, 1),
    ("level_2", "Level 2", 2, 2),
    ("level_3", "Level 3", 3, 3),
    ("level_4_5", "Level 4–5", 4, 5),
    ("level_6_7", "Level 6-7", 6, 7),
    ("level_8_plus", "Level 8+", 8, None),
)

_CELL = re.compile(r"(?P<name>.+?) \((?P<count>[^)]+)\)")

# Exact (post-override) cell names → compiled monster ids. A tuple is a packed-variant
# pool: at spawn time each individual picks uniformly on the wandering stream (pinned —
# RAW leaves the pick to the referee; per-individual uniform is deterministic and
# matches the pages' "3 to 7" spreads). An unknown name is an error, so SRD drift is
# caught at compile time instead of shipping a dangling reference.
_TABLE_ENTRIES: dict[str, str | tuple[str, ...]] = {
    "Acolyte": "acolyte",
    "Ape, White": "ape_white",
    "Bandit": "bandit",
    "Basilisk": "basilisk",
    "Bear, Cave": "cave_bear",
    "Beetle, Fire": "fire_beetle",
    "Beetle, Oil": "oil_beetle",
    "Beetle, Tiger": "tiger_beetle",
    "Berserker": "berserker",
    "Black Pudding": "black_pudding",
    "Blink Dog": "blink_dog",
    "Bugbear": "bugbear",
    "Caecilia": "caecilia",
    "Carcass Crawler": "carcass_crawler",
    "Cat, Mountain Lion": "mountain_lion",
    "Chimera": "chimera",
    "Cockatrice": "cockatrice",
    "Doppelgänger": "doppelganger",
    "Dragon, Black": "black_dragon",
    "Dragon, Blue": "blue_dragon",
    "Dragon, Gold": "gold_dragon",
    "Dragon, Green": "green_dragon",
    "Dragon, Red": "red_dragon",
    "Dragon, White": "white_dragon",
    "Driver Ant": "driver_ant",
    "Dwarf": "dwarf_monster",
    "Elf": "elf_monster",
    "Gargoyle": "gargoyle",
    "Gelatinous Cube": "gelatinous_cube",
    "Ghoul": "ghoul",
    "Giant, Hill": "hill_giant",
    "Giant, Stone": "stone_giant",
    "Gnoll": "gnoll",
    "Gnome": "gnome",
    "Goblin": "goblin",
    "Golem, Amber": "amber_golem",
    "Golem, Bone": "bone_golem",
    "Gorgon": "gorgon",
    "Green Slime": "green_slime",
    "Grey Ooze": "grey_ooze",
    "Halfling": "halfling_monster",
    "Harpy": "harpy",
    "Hellhound": ("hellhound_3", "hellhound_4", "hellhound_5", "hellhound_6", "hellhound_7"),
    "Hobgoblin": "hobgoblin",
    "Killer Bee": "killer_bee",
    "Kobold": "kobold",
    "Living Statue, Crystal": "crystal_living_statue",
    "Lizard, Draco": "draco",
    "Lizard, Gecko": "gecko",
    "Lizard, Tuatara": "tuatara",
    "Lizard Man": "lizard_man",
    "Lycanthrope, Devil Swine": "devil_swine",
    "Lycanthrope, Werebear": "werebear",
    "Lycanthrope, Wereboar": "wereboar",
    "Lycanthrope, Wererat": "wererat",
    "Lycanthrope, Weretiger": "weretiger",
    "Lycanthrope, Werewolf": "werewolf",
    "Manticore": "manticore",
    "Medium": "medium",
    "Medusa": "medusa",
    "Minotaur": "minotaur",
    "Mummy": "mummy",
    "Neanderthal": "neanderthal_caveman",
    "Noble": "noble",
    "Ochre Jelly": "ochre_jelly",
    "Ogre": "ogre",
    "Orc": "orc",
    "Owl Bear": "owl_bear",
    "Pixie": "pixie",
    "Purple Worm": "purple_worm",
    "Rhagodessa": "rhagodessa",
    "Robber Fly": "robber_fly",
    "Rock Baboon": "rock_baboon",
    "Rust Monster": "rust_monster",
    "Salamander, Flame": "flame_salamander",
    "Salamander, Frost": "frost_salamander",
    "Scorpion, Giant": "scorpion_giant",
    "Shadow": "shadow",
    "Shrew, Giant": "shrew_giant",
    "Skeleton": "skeleton",
    "Snake, Cobra": "spitting_cobra",
    "Snake, Pit Viper": "pit_viper",
    "Spectre": "spectre",
    "Spider, Black Widow": "black_widow",
    "Spider, Crab": "crab_spider",
    "Spider, Tarantella": "tarantella",
    "Sprite": "sprite",
    "Stirge": "stirge",
    "Thoul": "thoul",
    "Trader": "trader",
    "Troglodyte": "troglodyte",
    "Troll": "troll",
    "Veteran": ("veteran_1", "veteran_2", "veteran_3"),
    "Vampire": ("vampire_7", "vampire_8", "vampire_9"),
    "Warp Beast": "warp_beast",
    "Weasel, Giant": "weasel_giant",
    "Wight": "wight",
    "Wolf": "normal_wolf",
    "Wraith": "wraith",
    "Zombie": "zombie",
}

# The two hydra cells compile as variant pools with `variant_dice`: the printed HD
# dice roll on the wandering stream selects the template — pinned. The pool is
# ordered so the dice minimum maps to index 0.
_HYDRA_ENTRIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "Hydra 1d4+4HD": ("1d4+4", ("hydra_5", "hydra_6", "hydra_7", "hydra_8")),
    "Hydra 1d4+8HD": ("1d4+8", ("hydra_9", "hydra_10", "hydra_11", "hydra_12")),
}

# NPC adventuring party rows: kind plus the printed count dice on the cell.
_NPC_ENTRIES: dict[str, str] = {"Basic Adventurers": "basic", "Expert Adventurers": "expert"}


_NPC_CLASS_IDS = {
    "Cleric": "cleric",
    "Dwarf": "dwarf",
    "Elf": "elf",
    "Fighter": "fighter",
    "Halfling": "halfling",
    "Magic-User": "magic_user",
    "Thief": "thief",
}


def _parse_npc_tables(page: str, classes: list[dict[str, object]]) -> dict[str, object]:
    """Parse the NPC adventurer class/level, alignment, and composition tables.

    The class/level table carries a two-row spanning header (`|  | | Level | |`
    above the real one — the scroll spell-level treatment). Every printed Expert
    level range is asserted against the class's own maximum at compile time (the
    demi-human caps).
    """
    import re as _re

    from .pipetable import parse_tables

    tables = parse_tables(page)
    class_table = next(table for table in tables if len(table[0]) == 4 and "Level" in table[0])
    if class_table[1] != ["d8", "Class", "Basic", "Expert"]:
        raise ValueError(f"unexpected NPC class table header {class_table[1]!r}")
    caps = {str(definition["id"]): int(definition["max_level"]) for definition in classes}
    rows = []
    for roll_cell, class_name, basic_dice, expert_dice in class_table[2:]:
        class_id = _NPC_CLASS_IDS[class_name]
        parsed = _re.fullmatch(r"(\d*)d(\d+)(?:\+(\d+))?", expert_dice)
        maximum = int(parsed[1] or 1) * int(parsed[2]) + int(parsed[3] or 0)
        if maximum > caps[class_id]:
            raise ValueError(f"NPC expert dice {expert_dice!r} exceed the {class_id} cap of {caps[class_id]}")
        rows.append(
            {"roll": int(roll_cell), "class_id": class_id, "basic_dice": basic_dice, "expert_dice": expert_dice}
        )
    alignment_table = next(table for table in tables if table[0] == ["d6", "Alignment"])
    bands = []
    for roll_cell, alignment in alignment_table[1:]:
        low, high = parse_range(roll_cell)
        bands.append({"roll_min": low, "roll_max": high, "alignment": alignment.lower()})
    compositions = []
    for kind, heading in (("basic", "Basic Adventurers"), ("expert", "Expert Adventurers")):
        prose = section_prose(page, heading)
        match = _re.search(r"Composition: (\d+d\d+\+\d+) characters", prose)
        if match is None:
            raise ValueError(f"cannot find the {kind} composition dice")
        compositions.append({"kind": kind, "count_dice": match[1]})
    return {"npc_class_levels": rows, "npc_alignment": bands, "npc_compositions": compositions}


def compile_encounter_tables(
    srd_dir: Path, monsters: list[dict[str, object]], classes: list[dict[str, object]]
) -> dict[str, object]:
    """Compile the dungeon encounter tables into the `encounter_tables.json` structure.

    Args:
        srd_dir: The directory holding the scraped SRD markdown.
        monsters: The compiled monster entries, whose ids every reference must
            resolve against.
        classes: The compiled class entries, whose level caps the NPC expert level
            dice are asserted against.

    Returns:
        The raw dict (sans `_meta`) ready for `EncounterTables` validation.

    Raises:
        ValueError: If a printed header, cell, or count doesn't parse, a cell name
            is unmapped, or a mapped id doesn't resolve against the monster catalog.
    """
    page = (srd_dir / "Dungeon_Encounters.md").read_text(encoding="utf-8")
    printed = [
        *_columns(tables_after_heading(page, "By Level: 1–3")[0]),
        *_columns(tables_after_heading(page, "By Level: 4+")[0]),
    ]
    if [header for header, _ in printed] != [label for _, label, _, _ in _TABLES]:
        raise ValueError(f"unexpected encounter column headers: {[header for header, _ in printed]}")

    tables: list[dict[str, object]] = []
    for (table_id, label, min_level, max_level), (_, cells) in zip(_TABLES, printed, strict=True):
        rows = []
        for roll, cell in enumerate(cells, start=1):
            match = _CELL.fullmatch(cell)
            if match is None:
                raise ValueError(f"unparseable encounter cell {cell!r} in {label}")
            row: dict[str, object] = {"roll": roll, "name": match["name"]}
            count = match["count"]
            if count.isdigit():
                row["count_fixed"] = int(count)
            else:
                row["count_dice"] = count
            rows.append(row)
        tables.append({"id": table_id, "label": label, "min_level": min_level, "max_level": max_level, "rows": rows})

    # Overrides run before entry resolution because they normalize the printed names
    # resolution keys on ("Basic Adventures" → "Basic Adventurers").
    apply_overrides(tables, load_overrides("encounter_tables.json"))

    monster_ids = {str(monster["id"]) for monster in monsters}
    for table in tables:
        for row in table["rows"]:
            row["entry"] = _resolve_entry(str(row["name"]), monster_ids)
    parties_page = (srd_dir / "Adventuring_Parties.md").read_text(encoding="utf-8")
    return {"tables": tables, **_parse_npc_tables(parties_page, classes)}


def _columns(table: list[list[str]]) -> list[tuple[str, list[str]]]:
    """Split one printed table into its per-level columns of twenty cells."""
    header, *rows = table
    if header[0] != "d20" or len(rows) != 20:
        raise ValueError(f"unexpected encounter table shape: header {header}, {len(rows)} rows")
    if [row[0] for row in rows] != [str(roll) for roll in range(1, 21)]:
        raise ValueError("encounter table d20 rows must run 1-20 in order")
    return [(label, [row[index] for row in rows]) for index, label in enumerate(header[1:], start=1)]


def _resolve_entry(name: str, monster_ids: set[str]) -> dict[str, object]:
    """Resolve one (post-override) cell name to its structured entry."""
    if name in _NPC_ENTRIES:
        return {"kind": "npc_party", "party_kind": _NPC_ENTRIES[name]}
    if name in _HYDRA_ENTRIES:
        variant_dice, ids = _HYDRA_ENTRIES[name]
        entry: dict[str, object] = {"kind": "monster", "monster_ids": list(ids), "variant_dice": variant_dice}
    elif name in _TABLE_ENTRIES:
        mapped = _TABLE_ENTRIES[name]
        ids = (mapped,) if isinstance(mapped, str) else mapped
        entry = {"kind": "monster", "monster_ids": list(ids)}
    else:
        raise ValueError(f"unmapped encounter cell name {name!r}")
    unresolved = [monster_id for monster_id in ids if monster_id not in monster_ids]
    if unresolved:
        raise ValueError(f"encounter cell {name!r} references unknown monster ids {unresolved}")
    return entry
