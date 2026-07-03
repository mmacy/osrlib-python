"""Parser for the 138 SRD monster pages → `monsters.json`.

The page inventory is exactly the entries of `Monster_Descriptions.md` (the A–Z
index), which is the compiler's manifest — `Game_Statistics_(Monsters).md` and
`General.md` match a naive stat-block grep but are rules pages. Stat blocks are
uniform 2-column key/value pipe tables with 11 rows in fixed order, fenced by
horizontal rules, followed by `- **Label:** prose` ability bullets. Multi-variant
pages carry one `##` section per variant; shared bullets above the page's
`## Contents` merge into every variant, and per-variant `See main entry.` bullets
(including combined `A; B` labels) resolve against them.

Packed-variant pages — cells that pack several creatures — expand into one concrete
entry per variant, because a frozen template must be spawnable: `2 [17] / 0 [19]`
slash-lists zip per variant, `3 to 7` HD ranges enumerate, `By HD` THAC0 resolves
from the attack matrix HD rows, `By HD` and `See main entry (8 / 12 / 16)` saves
resolve from the monster save bands, and `1d6 per HD` breath damage pins per variant.

Mechanized ability tags carry params pinned in the `_ABILITY_PARAMS` table below
(the Phase 1 `_CLASS_ABILITIES` precedent); every other bullet compiles with
`manual=True`. Defense bullets are keyword-matched, not exact-string-matched — the
lycanthrope's `can only harmed by` grammar slip parses fine — and every compiled
shape is override-correctable. Mechanical cell noise (the gnome XP cell's stray
commas, the centaur attacks cell's trailing comma, the carcass crawler's stray
`Att` prefix, bold `**HD**` markers, typographic `’` and `×`) is normalized by the
parsers; semantic corrections (the bone golem's `2 or 4 × weapon` routines, the
minotaur's missing bite damage) live in `overrides/monsters.json` with reasons.

Printed XP is authoritative and ships as data; the compiler cross-validates every
entry against the XP-awards table (base by HD row plus asterisks × bonus, both
inflated by 250 per HD above 21) and fails the build on mismatch unless the entry's
overrides record an `xp` correction with a reason.
"""

import re
from pathlib import Path
from urllib.parse import quote

from .pipetable import parse_int, parse_tables, slugify, strip_emphasis, strip_links

MANIFEST_PAGE = "Monster_Descriptions.md"
PERSONS_PAGE = "General.md"

_LINK_TARGET = re.compile(r"\[([^\]]+)\]\(/srd/index\.php/((?:[^()\s]|\([^()]*\))+)\s+\"")
_BULLET = re.compile(r"^-\s+\*\*(.+?):?\*\*:?\s*(.*)$")
_HEADING2 = re.compile(r"^## (.+)$")
_AC_PAIR = re.compile(r"(-?\d+) \[(-?\d+)\]")
_THAC0 = re.compile(r"(\d+) \[([+-]?\d+)\]")
_SAVES = re.compile(r"D(\d+) W(\d+) P(\d+) B(\d+) S(\d+) \((.+)\)")
_MOVE_MODE = re.compile(r"(\d+)’ \((\d+)’\)\s*(.*)")
_HD_SINGLE = re.compile(r"(?:(\d+)|½)(?:([+-])(\d+))?(\*{0,2})")
_XP_NOTE = re.compile(r"([A-Za-z][A-Za-z ]*): ([\d,]+(?:/[\d,]+)*)")
_ATTACK_ITEM = re.compile(
    r"(?:(?P<label>[a-z]+): )?(?:(?P<count>\d+)(?: or \d+)? × )?(?P<name>[^()]+?)(?: \((?P<parens>[^)]*)\))?"
)
_FIXED_OPTIONS = re.compile(r"(\d+) or (\d+)hp")

# The attack matrix HD rows as (max effective HD, THAC0): effective HD is the count
# plus 1 for a bonus hit-point modifier ("attack as 1 HD higher"); a negative modifier
# keeps the unmodified row (the goblin's 1-1 keeps 19 [0], pinned). Used to resolve
# packed `By HD` THAC0 cells; the shipped table is compiled separately and the
# fidelity tests assert both agree with the SRD.
_MATRIX_THAC0 = ((1, 19), (2, 18), (3, 17), (4, 16), (5, 15), (6, 14), (7, 13), (9, 12), (11, 11), (13, 10), (15, 9))

# The monster saving-throw bands as (max HD, values): resolves packed `By HD` and
# `See main entry` save cells during expansion. No packed page exceeds 16 HD.
_SAVE_BANDS = (
    (3, (12, 13, 14, 15, 16)),
    (6, (10, 11, 12, 13, 14)),
    (9, (8, 9, 10, 10, 12)),
    (12, (6, 7, 8, 8, 10)),
    (15, (4, 5, 6, 5, 8)),
    (18, (2, 3, 4, 3, 6)),
)

_ALIGNMENTS = {"Lawful": "lawful", "Neutral": "neutral", "Chaotic": "chaotic"}

# Damage-paren effect keywords the kernel executes in Phase 2; everything else in an
# attack's parens compiles to a slugified rider tag the kernel doesn't execute.
_ELEMENT_WORDS = {"fire": "fire", "cold": "cold", "lightning": "lightning", "electricity": "lightning", "acid": "acid"}

# Packed-variant expansion: pages whose cells pack several creatures, with the pinned
# id suffix per expanded entry. Elemental power levels are labels; the rest suffix
# the Hit Dice number.
_POWER_LEVELS = ("lesser", "intermediate", "greater")

# (page filename, variant heading or None, bullet label) → (tag, params, manual).
# Params are pinned here rather than parsed from prose — the Phase 1 precedent; the
# prose ships alongside and stays the authority for narration.
_ABILITY_PARAMS: dict[tuple[str, str | None, str], tuple[str, dict[str, object], bool]] = {
    ("Troll.md", None, "Regeneration"): (
        "regeneration",
        {"delay_rounds": 3, "per_round": 3, "blocked_by": ["fire", "acid"], "revive": "2d6"},
        False,
    ),
    ("Thoul.md", None, "Regeneration"): (
        "regeneration",
        {"delay_rounds": 0, "per_round": 1, "while_alive": True},
        False,
    ),
    ("Vampire.md", None, "Regeneration"): (
        "regeneration",
        {"delay_rounds": 0, "per_round": 3, "while_alive": True},
        False,
    ),
    ("Wight.md", None, "Energy drain"): ("energy_drain", {"levels": 1, "xp_policy": "halfway"}, False),
    ("Wraith.md", None, "Energy drain"): ("energy_drain", {"levels": 1, "xp_policy": "level_minimum"}, False),
    ("Spectre.md", None, "Energy drain"): ("energy_drain", {"levels": 2, "xp_policy": "level_minimum"}, False),
    ("Vampire.md", None, "Energy drain"): ("energy_drain", {"levels": 2, "xp_policy": "level_minimum"}, False),
    ("Ghoul.md", None, "Paralysis"): (
        "paralysis",
        {"duration": "2d4", "unit": "turn", "unaffected": ["elf", "larger_than_ogre"]},
        False,
    ),
    ("Thoul.md", None, "Paralysis"): (
        "paralysis",
        {"duration": "2d4", "unit": "turn", "unaffected": ["elf", "larger_than_ogre"]},
        False,
    ),
    ("Carcass_Crawler.md", None, "Paralysis"): ("paralysis", {"duration": "2d4", "unit": "turn"}, False),
    ("Gelatinous_Cube.md", None, "Paralysis"): ("paralysis", {"duration": "2d4", "unit": "turn"}, False),
    ("Basilisk.md", None, "Petrifying touch"): ("petrification", {"vector": "touch"}, False),
    ("Basilisk.md", None, "Petrifying gaze"): ("petrification", {"vector": "gaze"}, False),
    ("Cockatrice.md", None, "Petrification"): ("petrification", {"vector": "touch"}, False),
    ("Medusa.md", None, "Petrification"): ("petrification", {"vector": "gaze"}, False),
    ("Gorgon.md", None, "Petrifying breath"): ("petrification", {"vector": "breath"}, False),
    ("Killer_Bee.md", None, "Poison"): ("poison", {"outcome": "death"}, False),
    ("Scorpion%2C_Giant.md", None, "Poison"): ("poison", {"outcome": "death"}, False),
    ("Purple_Worm.md", None, "Poison"): ("poison", {"outcome": "death"}, False),
    ("Wyvern.md", None, "Poison"): ("poison", {"outcome": "death"}, False),
    ("Fish%2C_Giant.md", "Giant Rockfish", "Poison"): ("poison", {"outcome": "death"}, False),
    ("Medusa.md", None, "Poison"): ("poison", {"outcome": "death", "onset_amount": 1, "onset_unit": "turn"}, False),
    ("Snake.md", "Giant Rattler", "Poison"): (
        "poison",
        {"outcome": "death", "onset_dice": "1d6", "onset_unit": "turn"},
        False,
    ),
    ("Snake.md", "Pit Viper", "Poison"): ("poison", {"outcome": "death"}, False),
    ("Snake.md", "Spitting Cobra", "Poison"): (
        "poison",
        {"outcome": "death", "onset_dice": "1d10", "onset_unit": "turn"},
        False,
    ),
    # The sea snake's slow-acting onset interacts with *neutralize poison* (25% failure
    # after onset) — referee territory; the tarantella's dance and the giant
    # centipede's sickness are not death outcomes. All three stay manual.
    ("Snake.md", "Sea Snake", "Poison"): ("poison", {"outcome": "death"}, True),
    ("Spider%2C_Giant.md", "Black Widow", "Poison"): (
        "poison",
        {"outcome": "death", "onset_amount": 1, "onset_unit": "turn"},
        False,
    ),
    ("Spider%2C_Giant.md", "Crab Spider", "Poison"): (
        "poison",
        {"outcome": "death", "onset_dice": "1d4", "onset_unit": "turn", "save_modifier": 2},
        False,
    ),
    ("Spider%2C_Giant.md", "Tarantella", "Poison"): ("poison", {}, True),
    ("Centipede%2C_Giant.md", None, "Poison"): ("poison", {}, True),
    ("Mummy.md", None, "Disease"): ("disease", {"kind": "mummy_rot"}, False),
    ("Hellhound.md", None, "Fire breath"): (
        "breath_weapon",
        {
            "targeting": "single",
            "element": "fire",
            "per_round_chance_in_six": 2,
            "save": "breath",
            "save_effect": "half",
        },
        False,
    ),
    ("Chimera.md", None, "Breath weapon"): (
        "breath_weapon",
        {
            "targeting": "area",
            "shape": "cone",
            "length_feet": 50,
            "end_width_feet": 10,
            "element": "fire",
            "uses_per_day": 3,
            "damage": "3d6",
            "save": "breath",
            "save_effect": "half",
        },
        False,
    ),
    ("Dragon_Turtle.md", None, "Breath weapon"): (
        "breath_weapon",
        {
            "targeting": "area",
            "shape": "cloud",
            "length_feet": 90,
            "width_feet": 30,
            "element": "steam",
            "uses_per_day": 3,
            "damage": "current_hp",
            "save": "breath",
            "save_effect": "half",
        },
        False,
    ),
}

# The dragons' page-level breath defaults ("Shapes of breath weapon" sub-bullets),
# applied where a variant omits dimensions; per-variant dimensions win.
_DRAGON_SHAPE_DEFAULTS: dict[str, dict[str, int]] = {
    "cloud": {"length_feet": 50, "width_feet": 40, "height_feet": 20},
    "cone": {"length_feet": 30, "mouth_width_feet": 2, "end_width_feet": 30},
    "line": {"width_feet": 5},
}

# Dragon variant breath weapons, pinned from each variant's bullet prose: shape,
# dimensions where printed, and element. Damage is current hit points, three uses per
# day, save versus breath for half — the page-level rule. The sea dragon's spittle is
# save-or-die poison instead.
_DRAGON_BREATH: dict[str, dict[str, object]] = {
    "Black Dragon": {"shape": "line", "length_feet": 60, "element": "acid"},
    "Blue Dragon": {"shape": "line", "length_feet": 100, "element": "lightning"},
    "Gold Dragon": {"shape": "cone", "length_feet": 90, "element": "fire", "alternate": "gas_cloud"},
    "Green Dragon": {"shape": "cloud", "element": "gas"},
    "Red Dragon": {"shape": "cone", "length_feet": 90, "element": "fire"},
    "White Dragon": {"shape": "cone", "length_feet": 80, "element": "cold"},
    "Sea Dragon": {},  # save-or-die spittle, handled below
}

# Monsters that use fire, immune to burning oil (pinned; the SRD's example is "e.g.
# red dragons"). Elemental defenses already gate most of these — the tag is the
# burning-oil marker the damage pipeline checks directly.
_USES_FIRE_PAGES = {
    "Hellhound.md": None,
    "Dragon.md": ("Red Dragon", "Gold Dragon"),
    "Chimera.md": None,
    "Efreeti_%28Lesser%29.md": None,
    "Elemental.md": ("Fire Elemental",),
    "Salamander.md": ("Flame Salamander",),
    "Giant.md": ("Fire Giant",),
}


def manifest(srd_dir: Path) -> list[tuple[str, str]]:
    """Return the monster page inventory: `(display name, filename)` pairs.

    The manifest is `Monster_Descriptions.md`'s A–Z index; link targets map to
    on-disk filenames by URL-encoding (`Dwarf_(Monster)` → `Dwarf_%28Monster%29.md`,
    the already-encoded `Doppelg%C3%A4nger` double-encodes).

    Args:
        srd_dir: The directory holding the scraped SRD markdown.

    Returns:
        The 138 pages, in index order.
    """
    text = (srd_dir / MANIFEST_PAGE).read_text(encoding="utf-8")
    pages = []
    for match in _LINK_TARGET.finditer(text):
        name, target = match[1], match[2]
        if target.startswith("#"):
            continue
        pages.append((name, quote(target, safe="_.-") + ".md"))
    return pages


def _sections(text: str) -> tuple[str, list[tuple[str, str]], list[tuple[str | None, str, list[list[str]], list]]]:
    """Split a page into intro, shared bullets, and per-variant sections.

    Returns:
        `(page_intro, shared_bullets, variants)` where each variant is
        `(heading or None, intro, stat rows, bullets)`; single-block pages yield one
        variant with heading `None`.
    """
    lines = text.splitlines()
    heading: str | None = None
    intro_by_section: dict[str | None, str] = {}
    bullets_by_section: dict[str | None, list[tuple[str, str]]] = {None: []}
    section_order: list[str | None] = []
    for line in lines:
        match = _HEADING2.match(line)
        if match:
            heading = match[1] if match[1] != "Contents" else "__contents__"
            if heading != "__contents__":
                bullets_by_section.setdefault(heading, [])
                section_order.append(heading)
            continue
        if heading == "__contents__":
            continue
        bullet = _BULLET.match(line)
        if bullet:
            prose = re.sub(r"\s+", " ", strip_emphasis(strip_links(bullet[2]))).strip()
            bullets_by_section.setdefault(heading, []).append((bullet[1], prose))
            continue
        stripped = re.sub(r"\s+", " ", strip_emphasis(strip_links(line))).strip()
        if stripped and not stripped.startswith(("#", "|", "-")) and heading not in intro_by_section:
            intro_by_section[heading] = stripped

    tables = _stat_tables(text)
    shared = bullets_by_section.get(None, [])
    page_intro = intro_by_section.get(None, "")
    if not section_order:
        if len(tables) != 1:
            raise ValueError(f"expected one stat block on a sectionless page, found {len(tables)}")
        return page_intro, shared, [(None, page_intro, tables[0], bullets_by_section[None])]
    if len(tables) != len(section_order):
        raise ValueError(f"{len(tables)} stat blocks for {len(section_order)} variant sections")
    variants = []
    for heading, table in zip(section_order, tables, strict=True):
        variants.append((heading, intro_by_section.get(heading, page_intro), table, bullets_by_section[heading]))
    return page_intro, shared, variants


def _stat_tables(text: str) -> list[list[list[str]]]:
    """Return every 11-row stat block on the page, in order."""
    blocks = []
    for table in parse_tables(text):
        keys = [row[0] for row in table if row]
        if "Hit Dice" in keys and "Armour Class" in keys:
            if len(table) != 11:
                raise ValueError(f"stat block has {len(table)} rows, expected 11")
            blocks.append(table)
    return blocks


def _block_cells(table: list[list[str]]) -> dict[str, str]:
    expected = (
        "Armour Class",
        "Hit Dice",
        "Attacks",
        "THAC0",
        "Movement",
        "Saving Throws",
        "Morale",
        "Alignment",
        "XP",
        "Number Appearing",
        "Treasure Type",
    )
    cells = {row[0]: row[1] for row in table if len(row) >= 2}
    if tuple(row[0] for row in table) != expected:
        raise ValueError(f"stat block rows out of order: {[row[0] for row in table]}")
    return cells


def _normalize(cell: str) -> str:
    """Strip paired bold markers (the hydra's `**HD**`) from a stat cell.

    Only *paired* markers strip: a double asterisk after an HD rating (`7**`) is the
    special-ability count, not markup.
    """
    return re.sub(r"\*\*([^*]+)\*\*", r"\1", cell).strip()


def _matrix_thac0(hd: int) -> tuple[int, int]:
    for max_hd, thac0 in _MATRIX_THAC0:
        if hd <= max_hd:
            return thac0, 19 - thac0
    raise ValueError(f"no packed page reaches HD {hd}")


def _band_saves(hd: int) -> tuple[int, ...]:
    for max_hd, values in _SAVE_BANDS:
        if hd <= max_hd:
            return values
    raise ValueError(f"no packed page reaches HD {hd}")


def _parse_hd(cell: str) -> dict[str, object]:
    """Parse a single (non-packed) Hit Dice cell."""
    cell = _normalize(cell)
    average = None
    fixed = None
    paren = re.search(r"\(([\d]+)hp\)", cell)
    if paren:
        average = int(paren[1])
        cell = cell[: paren.start()].strip()
    if cell == "1hp":
        return {"count": 0, "die": 8, "modifier": 0, "asterisks": 0, "average_hp": None, "fixed_hp": 1}
    match = _HD_SINGLE.fullmatch(cell)
    if match is None:
        raise ValueError(f"unparseable HD cell {cell!r}")
    fractional = match[1] is None
    modifier = int(match[3]) * (1 if match[2] == "+" else -1) if match[2] else 0
    return {
        "count": 1 if fractional else int(match[1]),
        "die": 4 if fractional else 8,
        "modifier": modifier,
        "asterisks": len(match[4]),
        "average_hp": average,
        "fixed_hp": fixed,
    }


def _parse_ac(cell: str) -> dict[str, object]:
    """Parse an AC cell: dual-format value, optional alternates, or the no-roll sentinel."""
    if cell == "No hit roll required":
        return {"ac": None, "ac_ascending": None, "ac_alternates": [], "attack_roll_required": False}
    pairs = _AC_PAIR.findall(cell)
    if not pairs:
        raise ValueError(f"unparseable AC cell {cell!r}")
    condition_match = re.search(r"\((-?\d+) \[(-?\d+)\] ([^)]+)\)", cell)
    alternates = []
    if condition_match:
        alternates.append(
            {"ac": int(condition_match[1]), "ac_ascending": int(condition_match[2]), "condition": condition_match[3]}
        )
        pairs = pairs[:1]
    else:
        for ac, aac in pairs[1:]:
            alternates.append({"ac": int(ac), "ac_ascending": int(aac), "condition": ""})
    return {
        "ac": int(pairs[0][0]),
        "ac_ascending": int(pairs[0][1]),
        "ac_alternates": alternates,
        "attack_roll_required": True,
    }


def _parse_damage_parens(content: str) -> dict[str, object]:
    """Parse the damage parens of one attack into damage, fixed forms, and effect tags."""
    result: dict[str, object] = {
        "damage": None,
        "fixed_damage": None,
        "fixed_damage_options": [],
        "by_weapon": False,
        "by_weapon_modifier": 0,
        "effects": [],
    }
    content = _normalize(content)
    options = _FIXED_OPTIONS.fullmatch(content)
    if options:
        result["fixed_damage_options"] = [int(options[1]), int(options[2])]
        return result
    dice_pattern = re.compile(r"\d*d\d+([+-]\d+)?(×\d+)?")
    for alternative in content.split(" or "):
        alternative = alternative.strip()
        weapon = re.fullmatch(r"by weapon\s*([+-]\s*\d+)?", alternative)
        if weapon:
            result["by_weapon"] = True
            if weapon[1]:
                result["by_weapon_modifier"] = int(weapon[1].replace(" ", ""))
            continue
        compact_whole = alternative.replace(" ", "")
        if dice_pattern.fullmatch(compact_whole):  # "1d6 + 2" is dice+modifier, not dice plus a rider
            result["damage"] = compact_whole
            continue
        for term in alternative.split(" + "):
            term = term.strip()
            compact = term.replace(" ", "")
            if re.fullmatch(r"\d+", compact):
                result["fixed_damage"] = int(compact)
            elif re.fullmatch(r"(\d+)hp", compact):
                result["fixed_damage"] = int(compact[:-2])
            elif dice_pattern.fullmatch(compact):
                result["damage"] = compact
            else:
                result["effects"].append(slugify(term))
    return result


def _split_top_level(text: str, separator: str) -> list[str]:
    """Split on a separator outside parentheses and square brackets."""
    parts = []
    depth = 0
    current = ""
    index = 0
    while index < len(text):
        if text.startswith(separator, index) and depth == 0:
            parts.append(current)
            current = ""
            index += len(separator)
            continue
        char = text[index]
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        current += char
        index += 1
    parts.append(current)
    return [part.strip() for part in parts if part.strip()]


_ROUTINE_START = re.compile(r"^(\[|\d+(?: or \d+)? × |breath$|magic$|\d+ to \d+ × )")


def _parse_attacks(cell: str) -> list[dict[str, object]]:
    """Parse an Attacks cell into alternative routines of attacks."""
    cell = _normalize(cell)
    if cell == "None":
        return []
    cell = re.sub(r"^Att ", "", cell).rstrip(",").strip()
    # Split top-level " or " into alternative routines, but only where the next
    # segment starts an attack — "1 × talons or beak (1d2)" keeps its name intact.
    segments = _split_top_level(cell, " or ")
    routines_text: list[str] = []
    for segment in segments:
        if routines_text and not _ROUTINE_START.match(segment):
            routines_text[-1] += " or " + segment
        else:
            routines_text.append(segment)
    routines = []
    for routine_text in routines_text:
        if routine_text.startswith("[") and routine_text.endswith("]"):
            routine_text = routine_text[1:-1]
        attacks = []
        for item in _split_top_level(routine_text, ","):
            match = _ATTACK_ITEM.fullmatch(item)
            if match is None:
                raise ValueError(f"unparseable attack {item!r}")
            attack: dict[str, object] = {"count": int(match["count"]) if match["count"] else 1}
            name = match["name"].strip()
            attack["name"] = f"{match['label']}: {name}" if match["label"] else name
            if match["parens"] is not None:
                attack.update(_parse_damage_parens(match["parens"]))
            attacks.append(attack)
        routines.append({"attacks": attacks})
    return routines


def _parse_movement(cell: str) -> list[dict[str, object]]:
    modes = []
    for part in _normalize(cell).split(" / "):
        match = _MOVE_MODE.fullmatch(part.strip())
        if match is None:
            raise ValueError(f"unparseable movement {part!r}")
        modes.append(
            {
                "rate_feet": int(match[1]),
                "encounter_rate_feet": int(match[2]),
                "descriptor": match[3] or None,
            }
        )
    return modes


def _parse_saves(cell: str) -> dict[str, object]:
    match = _SAVES.fullmatch(_normalize(cell))
    if match is None:
        raise ValueError(f"unparseable saving throws {cell!r}")
    return {
        "values": {
            "death": int(match[1]),
            "wands": int(match[2]),
            "paralysis": int(match[3]),
            "breath": int(match[4]),
            "spells": int(match[5]),
        },
        "save_as": match[6],
    }


def _parse_morale(cell: str) -> dict[str, object]:
    if cell == "Varies":
        return {"morale": None, "morale_alternates": []}
    match = re.fullmatch(r"(\d+)(?: \((\d+) (.+)\))?", cell)
    if match is None:
        raise ValueError(f"unparseable morale {cell!r}")
    alternates = []
    if match[2]:
        alternates.append({"score": int(match[2]), "condition": match[3]})
    return {"morale": int(match[1]), "morale_alternates": alternates}


def _parse_alignment(cell: str) -> dict[str, object]:
    if cell.startswith("Any"):
        usual = None
        match = re.fullmatch(r"Any, usually (\w+)", cell)
        if match:
            usual = _ALIGNMENTS[match[1]]
        return {"options": ["lawful", "neutral", "chaotic"], "usual": usual}
    options = [_ALIGNMENTS[name] for name in cell.split(" or ")]
    return {"options": options, "usual": None}


def _parse_xp(cell: str) -> dict[str, object]:
    """Parse an XP cell, tolerating stray commas (the gnome cell's mechanical noise)."""
    cell = cell.strip().rstrip(",").strip()
    match = re.fullmatch(r"([\d,]+),?\s*(?:\((.+)\))?", cell)
    if match is None:
        raise ValueError(f"unparseable XP cell {cell!r}")
    notes = []
    if match[2]:
        for role, values in _XP_NOTE.findall(match[2]):
            for value in values.split("/"):
                notes.append({"role": role.strip(), "xp": parse_int(value)})
        if not notes:
            raise ValueError(f"unparseable XP notes {match[2]!r}")
    return {"xp": parse_int(match[1]), "xp_notes": notes}


def _parse_na_value(text: str) -> dict[str, object]:
    text = _normalize(text).strip()
    if text == "see below":
        return {"dice": None, "fixed": None, "see_below": True}
    compact = text.replace(" ", "")
    if re.fullmatch(r"\d+", compact):
        return {"dice": None, "fixed": int(compact), "see_below": False}
    return {"dice": compact, "fixed": None, "see_below": False}


def _parse_number_appearing(cell: str) -> dict[str, object]:
    match = re.fullmatch(r"(.+?) \((.+)\)", _normalize(cell))
    if match is None:
        raise ValueError(f"unparseable number appearing {cell!r}")
    return {"dungeon": _parse_na_value(match[1]), "lair": _parse_na_value(match[2])}


def _parse_treasure(cell: str) -> dict[str, object]:
    result: dict[str, object] = {
        "letters": [],
        "parenthetical": [],
        "extra_gp": 0,
        "multiplier": 1,
        "special": [],
        "see_below": False,
    }
    cell = _normalize(cell)
    if cell == "None":
        return result
    paren = re.search(r"\((.+)\)", cell)
    if paren:
        content = paren[1]
        if content == "see below":
            result["see_below"] = True
        else:
            result["parenthetical"] = [content]
        cell = cell[: paren.start()].strip()
    multiplier = re.search(r"× (\d+)$", cell)
    if multiplier:
        result["multiplier"] = int(multiplier[1])
        cell = cell[: multiplier.start()].strip()
    extra = re.search(r"\+ ([\d,]+)gp$", cell)
    if extra:
        result["extra_gp"] = parse_int(extra[1])
        cell = cell[: extra.start()].strip()
    for part in cell.split(" + "):
        part = part.strip()
        if re.fullmatch(r"[A-V]", part):
            letters = result["letters"]
            assert isinstance(letters, list)
            letters.append(part)
        elif part:
            special = result["special"]
            assert isinstance(special, list)
            special.append(part)
    return result


def _packed_hd_values(cell: str) -> list[int] | None:
    """Return the packed HD list for a multi-creature cell, or None for one creature."""
    cell = _normalize(cell)
    head = cell.split("(")[0]
    range_match = re.match(r"(\d+) to (\d+)", head)
    if range_match:
        return list(range(int(range_match[1]), int(range_match[2]) + 1))
    slash_match = re.match(r"(\d+(?: / \d+)+)", head)
    if slash_match:
        return [int(value) for value in slash_match[1].split(" / ")]
    return None


def _resolve_packed_cell(cell: str, index: int, n: int, hd: int) -> str:
    """Resolve one variant's view of a packed cell.

    Slash-lists of length `n` pick the `index`-th value; `N to M` prefixes pick the
    variant's HD; `per HD` dice pin to the variant (`1d6 per HD` → `{hd}d6`).
    """
    cell = _normalize(cell)
    cell = re.sub(r"(\d+) to (\d+) × ", f"{hd} × ", cell)
    cell = re.sub(r"(\d)d(\d+) per HD", lambda m: f"{hd * int(m[1])}d{m[2]}", cell)

    def pick(match: re.Match) -> str:
        values = [value.strip() for value in match[0].split("/")]
        if len(values) != n:
            return match[0]
        return values[index]

    # Longest-first: values may themselves contain spaces ("19 [0] / 18 [+1]").
    return re.sub(r"[^/()]+(?:/[^/()]+)+", pick, cell).strip()


def _expand_variants(cells: dict[str, str]) -> list[tuple[str, dict[str, str], int]]:
    """Expand a packed stat block into per-creature cell dicts.

    Returns:
        `(id suffix, resolved cells, hd)` triples; a single-creature block returns
        one entry with an empty suffix.
    """
    hd_cell = _normalize(cells["Hit Dice"])
    hd_values = _packed_hd_values(hd_cell)
    if hd_values is None:
        return [("", cells, 0)]
    n = len(hd_values)
    labels = _POWER_LEVELS if "See main entry" in cells["Saving Throws"] else [str(hd) for hd in hd_values]
    # Only these rows ever pack per-creature values; the census pins cardinality as
    # per-row (the insect swarm's movement slash is two modes, not three creatures).
    packed_keys = ("Armour Class", "Attacks", "THAC0", "XP")
    expanded = []
    for index, hd in enumerate(hd_values):
        resolved: dict[str, str] = {}
        for key, cell in cells.items():
            cell = _normalize(cell)
            if key == "Hit Dice":
                resolved[key] = _resolve_packed_hd(cell, index, n, hd)
            elif key == "THAC0" and cell.startswith("By HD"):
                thac0, bonus = _matrix_thac0(hd)
                resolved[key] = f"{thac0} [{'+' if bonus > 0 else ''}{bonus}]"
            elif key == "Saving Throws" and (cell == "By HD" or cell.startswith("See main entry")):
                death, wands, paralysis, breath, spells = _band_saves(hd)
                resolved[key] = f"D{death} W{wands} P{paralysis} B{breath} S{spells} ({hd})"
            elif key in packed_keys:
                resolved[key] = _resolve_packed_cell(cell, index, n, hd)
            else:
                resolved[key] = cell
        expanded.append((labels[index], resolved, hd))
    return expanded


def _resolve_packed_hd(cell: str, index: int, n: int, hd: int) -> str:
    """Resolve one variant's Hit Dice cell from a packed form."""
    asterisks = "*" * cell.split("(")[0].count("*")
    paren = re.search(r"\((.+)\)", cell)
    if paren is None:
        raise ValueError(f"packed HD cell has no hit point parenthetical: {cell!r}")
    content = paren[1]
    if "per HD" in content:
        per_hd = int(re.match(r"(\d+)hp per HD", content)[1])
        return f"{hd}hd_fixed_{per_hd * hd}{asterisks}"
    values = [value.strip().removesuffix("hp") for value in content.split("/")]
    if len(values) != n:
        raise ValueError(f"packed HD parenthetical has {len(values)} values for {n} variants: {cell!r}")
    return f"{hd}{asterisks} ({values[index]}hp)"


def _parse_hd_expanded(cell: str) -> dict[str, object]:
    """Parse an HD cell that may carry the expansion's fixed-hp form."""
    fixed = re.fullmatch(r"(\d+)hd_fixed_(\d+)(\*{0,2})", cell)
    if fixed:
        return {
            "count": int(fixed[1]),
            "die": 8,
            "modifier": 0,
            "asterisks": len(fixed[3]),
            "average_hp": None,
            "fixed_hp": int(fixed[2]),
        }
    return _parse_hd(cell)


def _persons(srd_dir: Path) -> set[str]:
    """Return the person-page filenames from `General.md` §Persons (the pinned list)."""
    text = (srd_dir / PERSONS_PAGE).read_text(encoding="utf-8")
    section = text.split("## Persons", 1)[1]
    paragraph = next(p for p in section.split("\n") if "classified as" in p and "acolyte" in p)
    filenames = set()
    for match in _LINK_TARGET.finditer(paragraph):
        filenames.add(quote(match[2], safe="_.-") + ".md")
    if len(filenames) != 33:
        raise ValueError(f"expected 33 persons in {PERSONS_PAGE}, found {len(filenames)}")
    return filenames


def _defense_conditions(prose: str) -> list[str]:
    conditions = []
    lowered = prose.lower()
    for word, condition in (("charm", "charmed"), ("hold", "paralysed"), ("sleep", "asleep"), ("poison", "poisoned")):
        if word in lowered:
            conditions.append(condition)
    return conditions


def _apply_defense_bullet(
    defenses: dict[str, object], label: str, prose: str, breath_elements: tuple[str, ...]
) -> None:
    """Keyword-match one defense bullet into the structured shape."""
    lowered = prose.lower()
    gate = defenses.setdefault("harmed_only_by", [])
    reductions = defenses.setdefault("reductions", [])
    energy = defenses.setdefault("energy", {})
    conditions = defenses.setdefault("condition_immunities", [])
    assert isinstance(gate, list) and isinstance(reductions, list)
    assert isinstance(energy, dict) and isinstance(conditions, list)
    if label in ("Mundane damage immunity", "Damage immunity"):
        if "silver" in lowered:
            gate.append("silver")
        if "fire" in lowered:
            gate.append("fire")
        if "magic" in lowered:
            gate.append("magic")
        if "reduced by half" in lowered:
            reductions.append({"keys": [], "divisor": 2})
    elif label == "Damage reduction":
        reductions.append({"keys": ["silver"], "divisor": 2})
    elif label == "Immunity":
        if "only harmed by" in lowered or "unharmed by all attacks" in lowered:
            if "fire" in lowered:
                gate.append("fire")
            if "cold" in lowered:
                gate.append("cold")
        else:
            if "gas" in lowered:
                energy["gas"] = {"immunity": "all", "auto_save_magical": False}
            conditions.extend(_defense_conditions(prose))
    elif label == "Spell immunity":
        conditions.extend(_defense_conditions(prose))
    elif label in ("Fire immunity", "Cold immunity", "Lightning immunity"):
        element = label.split()[0].lower()
        immunity = "nonmagical" if "non-magical" in lowered else "all"
        energy[element] = {"immunity": immunity, "auto_save_magical": False}
    elif label == "Energy immunity":
        if breath_elements:
            # The dragons' shared bullet (whose prose names elements only in its
            # red-dragon example): immune to their own breath element(s) and lesser
            # (non-magical) versions, automatically saving versus similar (magical)
            # forms — pinned encoding, keyed by the variant's breath element(s).
            for element in breath_elements:
                energy[element] = {"immunity": "nonmagical", "auto_save_magical": True}
        else:
            elements = {element for word, element in _ELEMENT_WORDS.items() if word in lowered}
            for element in sorted(elements):
                energy[element] = {"immunity": "all", "auto_save_magical": False}
    elif label == "Poison immunity":
        if "killer bees" not in lowered:  # the robber fly's immunity is bee-specific
            conditions.append("poisoned")
    elif label == "Undead":
        conditions.extend(["poisoned", "charmed", "asleep"])


_DEFENSE_LABELS = (
    "Mundane damage immunity",
    "Damage immunity",
    "Damage reduction",
    "Immunity",
    "Spell immunity",
    "Fire immunity",
    "Cold immunity",
    "Lightning immunity",
    "Energy immunity",
    "Poison immunity",
    "Undead",
)

# Page-structure bullets consumed by the expansion itself, not creature abilities.
_EXCLUDED_BULLETS = {
    ("Elemental.md", "Power level"),
    ("Elemental.md", "Lesser"),
    ("Elemental.md", "Intermediate"),
    ("Elemental.md", "Greater"),
}


def _dragon_breath_ability(heading: str, prose: str) -> dict[str, object]:
    """Build a dragon variant's breath tag from the pinned shape table + page defaults."""
    spec = dict(_DRAGON_BREATH[heading])
    if heading == "Sea Dragon":
        params: dict[str, object] = {
            "targeting": "area",
            "shape": "cloud",
            "range_feet": 100,
            "diameter_feet": 20,
            "element": "poison",
            "uses_per_day": 3,
            "save": "breath",
            "save_effect": "negates",
            "outcome": "death",
        }
        return {"tag": "breath_weapon", "name": "Breath weapon", "prose": prose, "manual": False, "params": params}
    shape = spec.pop("shape")
    assert isinstance(shape, str)
    params = dict(_DRAGON_SHAPE_DEFAULTS[shape])
    params.update(spec)
    params.update(
        {
            "targeting": "area",
            "shape": shape,
            "uses_per_day": 3,
            "damage": "current_hp",
            "save": "breath",
            "save_effect": "half",
        }
    )
    return {"tag": "breath_weapon", "name": "Breath weapon", "prose": prose, "manual": False, "params": params}


def _abilities_and_defenses(
    filename: str,
    heading: str | None,
    shared: list[tuple[str, str]],
    own: list[tuple[str, str]],
    hd: int,
) -> tuple[list[dict[str, object]], dict[str, object], bool]:
    """Merge shared and per-variant bullets into abilities, defenses, and the undead flag."""
    shared_by_label = dict(shared)
    merged: list[tuple[str, str]] = []
    seen = set()
    for label, prose in own:
        if prose in ("See main entry.", "See main entry"):
            for part in [part.strip() for part in label.split(";")]:
                if part in shared_by_label:
                    merged.append((part, shared_by_label[part]))
                    seen.add(part)
        else:
            merged.append((label, prose))
            seen.add(label)
    for label, prose in shared:
        if label not in seen:
            merged.append((label, prose))

    breath_elements: tuple[str, ...] = ()
    if filename == "Dragon.md" and heading in _DRAGON_BREATH:
        spec = _DRAGON_BREATH[heading]
        element = spec.get("element")
        breath_elements = (element,) if isinstance(element, str) else ("poison",)
        if spec.get("alternate") == "gas_cloud":  # the gold dragon breathes fire or chlorine gas
            breath_elements = (*breath_elements, "gas")

    abilities: list[dict[str, object]] = []
    defenses: dict[str, object] = {}
    undead = False
    uses_fire_variants = _USES_FIRE_PAGES.get(filename, ())
    uses_fire = filename in _USES_FIRE_PAGES and (uses_fire_variants is None or heading in uses_fire_variants)
    for label, prose in merged:
        if (filename, label) in _EXCLUDED_BULLETS:
            continue
        if label in _DEFENSE_LABELS:
            _apply_defense_bullet(defenses, label, prose, breath_elements)
            if label == "Undead":
                undead = True
        pinned = _ABILITY_PARAMS.get((filename, heading, label)) or _ABILITY_PARAMS.get((filename, None, label))
        if filename == "Dragon.md" and label == "Breath weapon" and heading in _DRAGON_BREATH:
            abilities.append(_dragon_breath_ability(heading, prose))
        elif filename == "Hellhound.md" and label == "Fire breath":
            tag, params, manual = _ABILITY_PARAMS[(filename, None, label)]
            params = dict(params, damage=f"{hd}d6")
            abilities.append({"tag": tag, "name": label, "prose": prose, "manual": manual, "params": params})
        elif pinned:
            tag, params, manual = pinned
            abilities.append({"tag": tag, "name": label, "prose": prose, "manual": manual, "params": dict(params)})
        else:
            abilities.append({"tag": slugify(label), "name": label, "prose": prose, "manual": True, "params": {}})
    if uses_fire:
        abilities.append(
            {
                "tag": "uses_fire",
                "name": "Uses fire",
                "prose": "Monsters that use fire are unharmed by burning oil.",
                "manual": False,
                "params": {},
            }
        )
    for key, default in (("harmed_only_by", []), ("reductions", []), ("energy", {}), ("condition_immunities", [])):
        defenses.setdefault(key, default)
    return abilities, defenses, undead


def compile_monsters(srd_dir: Path) -> dict[str, object]:
    """Compile the monster pages into the `monsters.json` structure (sans `_meta`).

    Args:
        srd_dir: The directory holding the scraped SRD markdown.

    Returns:
        The raw dict ready for `MonsterCatalog` validation, entries sorted by id.
    """
    persons = _persons(srd_dir)
    monsters: list[dict[str, object]] = []
    for _, filename in manifest(srd_dir):
        text = (srd_dir / filename).read_text(encoding="utf-8")
        page_title = strip_links(text.splitlines()[0].lstrip("# ").strip())
        _, shared, variants = _sections(text)
        for heading, intro, table, bullets in variants:
            cells = _block_cells(table)
            base_name = heading or page_title
            for suffix, resolved, hd in _expand_variants(cells):
                hd_data = _parse_hd_expanded(_normalize(resolved["Hit Dice"]))
                effective_hd = hd or hd_data["count"]
                assert isinstance(effective_hd, int)
                abilities, defenses, undead = _abilities_and_defenses(filename, heading, shared, bullets, effective_hd)
                categories = []
                if undead:
                    categories.append("undead")
                if filename in persons:
                    categories.append("person")
                thac0_match = _THAC0.fullmatch(_normalize(resolved["THAC0"]))
                if thac0_match is None:
                    raise ValueError(f"unparseable THAC0 {resolved['THAC0']!r} on {filename}")
                entry: dict[str, object] = {
                    "id": slugify(base_name) + (f"_{suffix}" if suffix else ""),
                    "name": f"{base_name} ({suffix.replace('_', ' ').title()})" if suffix else base_name,
                    "page": filename,
                    "intro": intro,
                    **_parse_ac(resolved["Armour Class"]),
                    "hit_dice": hd_data,
                    "attacks": _parse_attacks(resolved["Attacks"]),
                    "thac0": int(thac0_match[1]),
                    "attack_bonus": int(thac0_match[2]),
                    "movement": _parse_movement(resolved["Movement"]),
                    "saves": _parse_saves(resolved["Saving Throws"]),
                    **_parse_morale(resolved["Morale"]),
                    "alignment": _parse_alignment(resolved["Alignment"]),
                    **_parse_xp(resolved["XP"]),
                    "number_appearing": _parse_number_appearing(resolved["Number Appearing"]),
                    "treasure": _parse_treasure(resolved["Treasure Type"]),
                    "abilities": abilities,
                    "defenses": defenses,
                    "categories": categories,
                }
                monsters.append(entry)
    monsters.sort(key=lambda entry: entry["id"])
    return {"monsters": monsters}


def source_pages(srd_dir: Path) -> tuple[str, ...]:
    """Return every SRD page the monster compiler reads."""
    return (*(filename for _, filename in manifest(srd_dir)), MANIFEST_PAGE, PERSONS_PAGE)


def validate_xp(monsters: list[dict[str, object]], xp_rows: list[dict[str, object]]) -> None:
    """Cross-validate every printed XP value against the XP-awards table.

    Pinned: negative hit-point modifiers map to the lower band (the goblin's 1-1 HD
    validates against "Less than 1" at 5 XP); above 21 HD both the base and bonus
    amounts inflate by 250 per HD above 21 (the dragon turtle's 9,000 proves it).
    Printed values are authoritative; a mismatch fails the build unless the entry's
    overrides record an `xp` correction with a reason.

    Args:
        monsters: The compiled entries, after overrides.
        xp_rows: The compiled XP-awards rows from `compile_combat_tables`.

    Raises:
        ValueError: On an unacknowledged mismatch.
    """
    by_label = {row["label"]: row for row in xp_rows}
    mismatches = []
    for entry in monsters:
        hd = entry["hit_dice"]
        assert isinstance(hd, dict)
        count, modifier, die = hd["count"], hd["modifier"], hd["die"]
        assert isinstance(count, int) and isinstance(modifier, int) and isinstance(die, int)
        plus = modifier > 0
        if modifier < 0:
            count -= 1
            plus = True
        if die == 4 or count < 1:
            label = "Less than 1"
        elif count >= 21:
            label = "21–21+"
        elif count <= 6:
            label = f"{count}+" if plus else str(count)
        elif count <= 7:
            label = "7–7+"
        elif count <= 8:
            label = "8–8+"
        elif count <= 10:
            label = "9–10+"
        elif count <= 12:
            label = "11–12+"
        elif count <= 16:
            label = "13–16+"
        else:
            label = "17–20+"
        row = by_label[label]
        base, bonus = row["base"], row["bonus"]
        assert isinstance(base, int) and isinstance(bonus, int)
        raw_count = hd["count"]
        assert isinstance(raw_count, int)
        if raw_count > 21:
            base += (raw_count - 21) * 250
            bonus += (raw_count - 21) * 250
        asterisks = hd["asterisks"]
        assert isinstance(asterisks, int)
        expected = base + asterisks * bonus
        overridden = entry.get("overrides_applied", [])
        assert isinstance(overridden, list)
        if entry["xp"] != expected and "xp" not in overridden:
            mismatches.append(f"{entry['id']}: printed {entry['xp']}, table gives {expected}")
    if mismatches:
        raise ValueError(
            "XP cross-validation failed (record SRD typos in overrides/monsters.json):\n" + "\n".join(mismatches)
        )
