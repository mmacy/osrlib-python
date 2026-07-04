"""Parser for the magic item generation tables and per-item pages → `magic_items.json`.

Sources: the eight category pages — `Potions.md`, `Scrolls_and_Maps.md`, `Rings.md`,
`Rods%2C_Staves%2C_Wands.md`, `Miscellaneous_Items.md`, `Swords.md`, `Weapons.md`
(the *magic* weapons page — mundane weapons live in `Weapons_and_Armour.md`), and
`Armour_and_Shields.md` — plus the four sentient-sword pages and the per-item pages.

Sub-table conventions, pinned from the survey: each category table carries a sparse
small-die B column (blank cells are not data — B and X are independent index spaces
over the same outcome list) and a full d% X column; `00` reads as 100;
`Miscellaneous_Items.md` alone uses ASCII hyphens in its ranges (every other table
uses en-dashes) and contains the degenerate `94-94` — the range parser accepts both
dash forms and single-value ranges. The scroll spell-level table's two-row spanning
header gets the encounter-table special-case treatment.

Rows whose printed links have no backing page (the six `Potion of Control *` forms,
the two `Crystal Ball with *` forms, `Ring of Protection, 5' Radius`, and the four
elemental summoning devices) resolve to hand-mapped ids against the combined pages —
the `_TABLE_ENTRIES` precedent — and every sub-table outcome is asserted to resolve
to a catalog id. Catalog ids are per printed variant (the `oil_beetle` precedent),
while payload-only bands (the three ring wish-count rows, the repeated arrow and
bolt quantity rows) compile as one id whose count or quantity dice are generation
params on the row, pinned.

Swords, weapons, and armour have no per-item pages — their outcomes compile from the
inline table-cell grammar (`Sword +1, +3 vs Dragons`, `Cursed Armour -2 with Shield
+1`): leading bonus, optional versus clause, optional paired second item.
Comma-tokenizing is banned; the grammar is positional. Versus targets resolve
structurally through `_VERSUS_TARGETS` — lycanthropes and dragons as page-derived id
sets, spell users and regenerating creatures as ability-derived sets, undead and
enchanted creatures as category tags — so no clause resolves by string-matching
prose at play time.

Base-item pins for enchanted arms (registered): `Axe` overlays `battle_axe` and
`Bow` overlays `long_bow` — the SRD prints the bare nouns and the mundane list has
two of each; the heavier form is the pinned reading. Structured mechanics are
hand-curated in `_MAGIC_ITEM_MECHANICS` (the `_SPELL_MECHANICS` precedent) for
exactly the Phase 5 wired census; everything else ships `manual`-tagged prose.
`Helm_of_Telepathy.md` is truncated mid-sentence in the SRD dump — its description
completes via `overrides/magic_items.json` with provenance.
"""

import re
from pathlib import Path
from urllib.parse import quote

from .pipetable import slugify, strip_emphasis, strip_links, tables_after_heading
from .treasure import parse_treasure_segment

SOURCE_PAGES = (
    "Potions.md",
    "Scrolls_and_Maps.md",
    "Rings.md",
    "Rods%2C_Staves%2C_Wands.md",
    "Miscellaneous_Items.md",
    "Swords.md",
    "Weapons.md",
    "Armour_and_Shields.md",
    "Sentient_Swords.md",
    "Sensory_Powers_of_Sentient_Swords.md",
    "Extraordinary_Powers_of_Sentient_Swords.md",
    "Special_Purpose_of_Sentient_Swords.md",
)

_BAND = re.compile(r"^(\d\d?|00)(?:[–-](\d\d?|00))?$")
_ROMAN = ("i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii")

_DEFAULT_CHARGES = {"rod": "1d10", "staff": "3d10", "wand": "2d10"}

# Per-staff usability, from each page's usage bullet; staves without a note take the
# category page's "Usage: Spell casters" (any caster kind).
_STAFF_CASTERS = {
    "staff_of_healing": "divine",
    "staff_of_power": "arcane",
    "staff_of_snakes": "divine",
    "staff_of_withering": "divine",
    "staff_of_wizardry": "arcane",
}

# Ring table cell names → (id, source page or None for a section of Ring_of_Protection).
_RING_ENTRIES: dict[str, tuple[str, str]] = {
    "Control Animals": ("ring_of_controlling_animals", "Ring_of_Controlling_Animals.md"),
    "Control Humans": ("ring_of_controlling_humans", "Ring_of_Controlling_Humans.md"),
    "Control Plants": ("ring_of_controlling_plants", "Ring_of_Controlling_Plants.md"),
    "Delusion": ("ring_of_delusion", "Ring_of_Delusion.md"),
    "Djinni Summoning": ("ring_of_djinni_summoning", "Ring_of_Djinni_Summoning.md"),
    "Fire Resistance": ("ring_of_fire_resistance", "Ring_of_Fire_Resistance.md"),
    "Invisibility": ("ring_of_invisibility", "Ring_of_Invisibility.md"),
    "Protection +1, 5’ radius": ("ring_of_protection_5_radius", "Ring_of_Protection.md"),
    "Protection +1": ("ring_of_protection", "Ring_of_Protection.md"),
    "Regeneration": ("ring_of_regeneration", "Ring_of_Regeneration.md"),
    "Spell Storing": ("ring_of_spell_storing", "Ring_of_Spell_Storing.md"),
    "Spell Turning": ("ring_of_spell_turning", "Ring_of_Spell_Turning.md"),
    "Telekinesis": ("ring_of_telekinesis", "Ring_of_Telekinesis.md"),
    "Water Walking": ("ring_of_water_walking", "Ring_of_Water_Walking.md"),
    "Weakness": ("ring_of_weakness", "Ring_of_Weakness.md"),
    "X-Ray Vision": ("ring_of_x_ray_vision", "Ring_of_X-Ray_Vision.md"),
}

_WISH_COUNTS = {"Wishes, 1–2": "1d2", "Wishes, 1–3": "1d3", "Wishes, 2–4": "1d3+1"}

# Weapon nouns → the mundane equipment id the enchanted form overlays.
_WEAPON_BASES = {
    "Arrows": "arrows",
    "Axe": "battle_axe",
    "Bow": "long_bow",
    "Crossbow Bolts": "crossbow_bolts",
    "Dagger": "dagger",
    "Mace": "mace",
    "Sling": "sling",
    "Spear": "spear",
    "Sword": "sword",
    "War Hammer": "war_hammer",
}

_AMMUNITION_BASES = {"arrows", "crossbow_bolts"}

# Printed versus-clause targets → structural resolution. Category names resolve
# against monster category tags; the derived keys enumerate template-id sets from
# the compiled monster data (page-derived for lycanthropes and dragons,
# ability-derived for spell users and regenerating creatures), so no clause is ever
# string-matched at play time.
_VERSUS_TARGETS: dict[str, dict[str, object]] = {
    "Lycanthropes": {"derived": "lycanthropes"},
    "Spell Users": {"derived": "spell_users"},
    "Dragons": {"derived": "dragons"},
    "Enchanted Creatures": {"categories": ["enchanted"]},
    "Regenerating Creatures": {"derived": "regenerating"},
    "Undead": {"categories": ["undead"]},
    "orcs, goblins, and kobolds": {"template_ids": ["orc", "goblin", "kobold"]},
}

_SWORD_POWER_SUFFIXES = {
    "Energy Drain": "energy_drain",
    "Flaming": "flaming",
    "Light": "light",
    "Locate Objects": "locate_objects",
    "Wishes": "wishes",
    "Charm Person": "charm_person",
    "Dwarven Thrower": "dwarven_thrower",
}

_SENSORY_POWERS = {
    "Detect evil or good": "detect_evil_or_good",
    "Detect gems": "detect_gems",
    "Detect magic": "detect_magic",
    "Detect metals": "detect_metals",
    "Detect shifting architecture": "detect_shifting_architecture",
    "Detect slopes": "detect_slopes",
    "Detect traps": "detect_traps",
    "Locate secret doors": "locate_secret_doors",
    "See invisible objects": "see_invisible_objects",
    "Roll an extraordinary power": "roll_extraordinary",
    "Roll twice again on this table": "roll_twice",
}

_EXTRAORDINARY_POWERS = {
    "Clairaudience": "clairaudience",
    "Clairvoyance": "clairvoyance",
    "ESP": "esp",
    "Extra damage (dups. allowed)": "extra_damage",
    "Flying": "flying",
    "Healing (duplicates allowed)": "healing",
    "Illusion": "illusion",
    "Levitation": "levitation",
    "Telekinesis": "telekinesis",
    "Telepathy": "telepathy",
    "Teleportation": "teleportation",
    "X-ray vision": "x_ray_vision",
    "Roll twice again on this table": "roll_twice",
    "Roll 3 times again on this table": "roll_thrice",
}

_SPECIAL_PURPOSES = {
    "Arcane spell casters": "arcane_spell_casters",
    "Divine spell casters": "divine_spell_casters",
    "Warriors (e.g. fighters or other primarily combat-oriented, non-spell casting classes, "
    "including non-spell casting demihumans)": "warriors",
    "Specific type of monster (determine randomly)": "specific_monster",
    "Lawful creatures (or chaotic creatures if the sword is lawful)": "lawful_creatures",
    "Chaotic creatures (or lawful creatures if the sword is chaotic)": "chaotic_creatures",
}

# The wired census (the `_SPELL_MECHANICS` precedent): id → template field patches.
# Everything not named here ships `manual`-tagged prose. Modifiers carry
# `from_item=True` — item bonuses are exempt from the spell cumulative cap (pinned).
_MAGIC_ITEM_MECHANICS: dict[str, dict[str, object]] = {
    "sword_plus_1_energy_drain": {
        "effect": {
            "kind": "on_hit_drain",
            "params": {"levels": 1, "xp_policy": "level_minimum", "total_drains_dice": "1d4+4"},
        },
    },
    "sword_plus_1_light": {
        "effect": {"kind": "light", "params": {"light_radius_feet": 30}},
    },
    "potion_of_fire_resistance": {
        "effect": {
            "kind": "potion",
            "modifiers": [
                {"kind": "save_bonus", "value": 2, "element": "fire", "from_item": True},
                {"kind": "damage_reduction_per_die", "value": 1, "element": "fire", "from_item": True},
            ],
            "params": {"effect_kind": "potion_fire_resistance"},
        },
    },
    "potion_of_giant_strength": {
        "effect": {
            "kind": "potion",
            "modifiers": [{"kind": "damage_multiplier", "value": 2, "from_item": True}],
            "params": {"effect_kind": "potion_giant_strength"},
        },
    },
    "potion_of_growth": {
        "effect": {
            "kind": "potion",
            "modifiers": [{"kind": "melee_damage_multiplier", "value": 2, "from_item": True}],
            "params": {"effect_kind": "potion_growth"},
        },
    },
    "potion_of_healing": {
        "effect": {"kind": "healing", "heal_dice": "1d6+1", "params": {"cures_paralysis": True, "instantaneous": True}},
    },
    "potion_of_invisibility": {
        "effect": {"kind": "potion", "condition": "invisible", "params": {"effect_kind": "invisibility"}},
    },
    "potion_of_invulnerability": {
        "effect": {
            "kind": "potion",
            "modifiers": [
                {"kind": "ac_bonus", "value": 2, "from_item": True},
                {"kind": "save_bonus", "value": 2, "from_item": True},
            ],
            "params": {"effect_kind": "potion_invulnerability", "weekly_inversion": True},
        },
    },
    "potion_of_poison": {
        "effect": {"kind": "save_or_die", "save_category": "death", "params": {"instantaneous": True}},
    },
    "potion_of_speed": {
        "effect": {
            "kind": "potion",
            "params": {"effect_kind": "haste", "movement_multiplier": 2, "attacks_multiplier": 2},
        },
    },
    "ring_of_protection": {
        "always_active": True,
        "ac_bonus": 1,
        "effect": {"kind": "worn_modifiers", "modifiers": [{"kind": "save_bonus", "value": 1, "from_item": True}]},
    },
    "ring_of_protection_5_radius": {
        "always_active": True,
        "ac_bonus": 1,
        "effect": {"kind": "worn_modifiers", "modifiers": [{"kind": "save_bonus", "value": 1, "from_item": True}]},
        "params": {"radius_rank": True},
    },
    "ring_of_fire_resistance": {
        "always_active": True,
        "effect": {
            "kind": "worn_modifiers",
            "modifiers": [
                {"kind": "save_bonus", "value": 2, "element": "fire", "from_item": True},
                {"kind": "damage_reduction_per_die", "value": 1, "element": "fire", "from_item": True},
            ],
        },
    },
    "ring_of_regeneration": {
        "always_active": True,
        "effect": {
            "kind": "regeneration",
            "params": {"per_round": 1, "blocked_by": ("fire", "acid"), "while_alive": True},
        },
    },
    "ring_of_weakness": {
        "cursed": True,
        "always_active": True,
        "effect": {"kind": "weakness", "params": {"onset_rounds": 6, "strength_set": 3}},
    },
    "wand_of_cold": {
        "effect": {
            "kind": "damage_area",
            "damage_dice": "6d6",
            "element": "cold",
            "save_category": "wands",
            "save_on": "half",
            "shape": "cone",
            "dimensions": {"length_feet": 60, "width_feet": 30},
        },
    },
    "wand_of_fear": {
        "effect": {
            "kind": "condition_area",
            "condition": "afraid",
            "save_category": "wands",
            "save_on": "negates",
            "shape": "cone",
            "dimensions": {"length_feet": 60, "width_feet": 30},
            "duration_unit": "round",
            "duration_amount": 30,
            "params": {"effect_kind": "fear"},
        },
    },
    "wand_of_fire_balls": {
        "effect": {
            "kind": "damage_area",
            "damage_dice": "6d6",
            "element": "fire",
            "save_category": "wands",
            "save_on": "half",
            "shape": "sphere",
            "dimensions": {"radius_feet": 20},
            "range_feet": 240,
        },
    },
    "wand_of_lightning_bolts": {
        "effect": {
            "kind": "damage_area",
            "damage_dice": "6d6",
            "element": "lightning",
            "save_category": "wands",
            "save_on": "half",
            "shape": "line",
            "dimensions": {"length_feet": 60, "width_feet": 5},
            "range_feet": 180,
        },
    },
    "wand_of_paralysation": {
        "effect": {
            "kind": "condition_area",
            "condition": "paralysed",
            "save_category": "wands",
            "save_on": "negates",
            "shape": "cone",
            "dimensions": {"length_feet": 60, "width_feet": 30},
            "duration_unit": "turn",
            "duration_amount": 6,
            "params": {"effect_kind": "wand_paralysation"},
        },
    },
    "staff_of_healing": {
        "charges_dice": None,
        "effect": {"kind": "healing", "heal_dice": "1d6+1", "params": {"once_per_target_per_day": True}},
    },
    "staff_of_striking": {
        "base_item_id": "staff",
        "effect": {"kind": "striking", "damage_dice": "2d6"},
    },
    "sword_plus_1_wishes": {
        "params": {"wish_count_dice": "1d4"},
    },
    "scroll_of_protection_from_lycanthropes": {
        "effect": {
            "kind": "ward",
            "duration_unit": "turn",
            "duration_amount": 6,
            "params": {"targets": "lycanthropes", "bands": ("1-3:1d10", "4-5:1d8", "6+:1d4")},
        },
    },
    "scroll_of_protection_from_undead": {
        "effect": {
            "kind": "ward",
            "duration_unit": "turn",
            "duration_amount": 6,
            "params": {"targets": "undead", "bands": ("1-3:2d12", "4-5:2d6", "6+:1d6"), "bars_categories": ("undead",)},
        },
    },
    "scroll_of_protection_from_elementals": {
        "effect": {
            "kind": "ward",
            "duration_unit": "turn",
            "duration_amount": 2,
            "params": {"targets": "elementals", "all_affected": True},
        },
    },
    "gauntlets_of_ogre_power": {
        "always_active": True,
        "effect": {
            "kind": "worn_modifiers",
            "modifiers": [{"kind": "strength_set", "value": 18, "from_item": True}],
        },
        "params": {"max_load_bonus_coins": 1000},
    },
    "girdle_of_giant_strength": {
        "always_active": True,
        "effect": {"kind": "giant_strength", "params": {"attack_as_hd": 8, "flat_damage_dice": "2d8"}},
    },
    "bag_of_holding": {
        "params": {"capacity_coins": 10000, "loaded_weight_coins": 600},
    },
    "displacer_cloak": {
        "always_active": True,
        "effect": {
            "kind": "worn_modifiers",
            "modifiers": [
                {
                    "kind": "save_bonus",
                    "value": 2,
                    "save_categories": ("paralysis", "wands", "spells"),
                    "from_item": True,
                },
                {"kind": "attack_penalty_of_attackers", "value": -2, "melee_only": True, "from_item": True},
            ],
        },
    },
}

# The cursed scroll's six example curses as data rows; energy drain and slow healing
# are the wired pair (work item 5), the rest are manual prose on the emitted event.
_WIRED_CURSES = {"energy_drain", "slow_healing"}


def source_pages(srd_dir: Path) -> tuple[str, ...]:
    """Return every SRD page this compiler reads, for the output's `_meta` block."""
    pages = set(SOURCE_PAGES)
    pages.update(page.name for page in _item_pages(srd_dir))
    return tuple(sorted(pages))


def _item_pages(srd_dir: Path) -> list[Path]:
    names = set()
    for prefix in ("Potion_of_", "Ring_of_", "Rod_of_", "Staff_of_", "Wand_of_"):
        names.update(page.name for page in srd_dir.glob(f"{prefix}*.md"))
    names.update(_MISC_PAGES)
    return [srd_dir / name for name in sorted(names)]


def _parse_band(cell: str) -> tuple[int, int]:
    """Parse a d% band cell: en-dash, ASCII hyphen (Miscellaneous Items), or single."""
    match = _BAND.fullmatch(cell)
    if match is None:
        raise ValueError(f"unparseable d% band {cell!r}")
    low = 100 if match[1] == "00" else int(match[1])
    high = low if match[2] is None else (100 if match[2] == "00" else int(match[2]))
    return low, high


def _page_prose(text: str, *, heading: str | None = None) -> list[str]:
    """Return a page's (or one section's) cleaned prose lines.

    Skips the title, the Contents block, pipe tables, and the B/X footer lines;
    bullet markers drop but bullet text is kept. With `heading`, only that
    section's lines return.
    """
    lines: list[str] = []
    collecting = heading is None
    in_contents = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if heading is not None:
                if title == heading:
                    collecting = True
                    continue
                if collecting:
                    break
            in_contents = title == "Contents"
            continue
        if in_contents or not collecting:
            continue
        if stripped.startswith("|") or stripped.startswith("*") and stripped.endswith("*") and "**" not in stripped:
            continue
        if stripped.startswith("**B:**") or stripped.startswith("**X:**"):
            continue
        cleaned = re.sub(r"\s+", " ", strip_emphasis(strip_links(stripped))).strip()
        cleaned = re.sub(r"^- ", "", cleaned)
        if cleaned:
            lines.append(cleaned)
    return lines


def _read(srd_dir: Path, filename: str) -> str:
    return (srd_dir / filename).read_text(encoding="utf-8")


def _category_table(text: str) -> list[tuple[int | None, int, int, str]]:
    """Parse the first pipe table of a category page."""
    tables = [table for table in _all_tables(text) if len(table[0]) == 3]
    header, *rows = tables[0]
    if not header[0].startswith("B: d") or header[1] != "X: d%":
        raise ValueError(f"unexpected sub-table header {header!r}")
    parsed = []
    for basic_cell, expert_cell, name in rows:
        basic_value = int(basic_cell) if basic_cell else None
        expert_min, expert_max = _parse_band(expert_cell)
        parsed.append((basic_value, expert_min, expert_max, name))
    return parsed


def _basic_die(text: str) -> int:
    header = [table[0] for table in _all_tables(text) if len(table[0]) == 3][0]
    return int(header[0].removeprefix("B: d"))


def _all_tables(text: str) -> list[list[list[str]]]:
    from .pipetable import parse_tables

    return parse_tables(text)


_MISC_PAGES = (
    "Amulet_of_Protection_Against_Scrying.md",
    "Bag_of_Devouring.md",
    "Bag_of_Holding.md",
    "Boots_of_Levitation.md",
    "Boots_of_Speed.md",
    "Boots_of_Travelling_and_Leaping.md",
    "Broom_of_Flying.md",
    "Crystal_Ball.md",
    "Displacer_Cloak.md",
    "Drums_of_Panic.md",
    "Efreeti_Bottle.md",
    "Elemental_Summoning_Device.md",
    "Elven_Cloak_and_Boots.md",
    "Flying_Carpet.md",
    "Gauntlets_of_Ogre_Power.md",
    "Girdle_of_Giant_Strength.md",
    "Helm_of_Alignment_Changing.md",
    "Helm_of_Reading_Languages_and_Magic.md",
    "Helm_of_Telepathy.md",
    "Helm_of_Teleportation.md",
    "Horn_of_Blasting.md",
    "Medallion_of_ESP_30%E2%80%99.md",
    "Medallion_of_ESP_90%E2%80%99.md",
    "Mirror_of_Life_Trapping.md",
    "Rope_of_Climbing.md",
    "Scarab_of_Protection.md",
)


def _misc_page_for(name: str) -> tuple[str, str | None]:
    """Return (filename, section heading or None) for a misc table cell name."""
    if name.startswith("Crystal Ball with "):
        return "Crystal_Ball.md", name.replace("with", "With")
    if name.startswith("Elemental Summoning Device"):
        return "Elemental_Summoning_Device.md", None
    encoded = quote(name.replace(" ", "_"), safe="_-,()'’") + ".md"
    encoded = encoded.replace("’", quote("’"))
    return encoded, None


def _ward_target_sets(monsters: list[dict[str, object]]) -> dict[str, list[str]]:
    """Page-derived id sets for the protection scroll wards (no category tags exist)."""
    return {
        "lycanthropes": sorted(str(m["id"]) for m in monsters if str(m["page"]).startswith("Lycanthrope")),
        "elementals": sorted(str(m["id"]) for m in monsters if str(m["page"]) == "Elemental.md"),
    }


def _derived_versus_sets(monsters: list[dict[str, object]]) -> dict[str, list[str]]:
    """Enumerate the ability- and page-derived versus target sets from the monster data."""
    lycanthropes = sorted(str(m["id"]) for m in monsters if str(m["page"]).startswith("Lycanthrope"))
    dragons = sorted(str(m["id"]) for m in monsters if str(m["page"]).startswith("Dragon"))
    spell_tags = {"spells", "language_srd_index_php_languages_languages_and_spells"}
    spell_users = sorted(str(m["id"]) for m in monsters if any(a["tag"] in spell_tags for a in m["abilities"]))
    regenerating = sorted(str(m["id"]) for m in monsters if any(a["tag"] == "regeneration" for a in m["abilities"]))
    return {
        "lycanthropes": lycanthropes,
        "dragons": dragons,
        "spell_users": spell_users,
        "regenerating": regenerating,
    }


def _resolve_versus(label: str, bonus: int, derived: dict[str, list[str]]) -> dict[str, object]:
    spec = _VERSUS_TARGETS.get(label)
    if spec is None:
        raise ValueError(f"unmapped versus target {label!r}")
    clause: dict[str, object] = {"label": label, "bonus": bonus}
    if "categories" in spec:
        clause["categories"] = spec["categories"]
    if "template_ids" in spec:
        clause["template_ids"] = spec["template_ids"]
    if "derived" in spec:
        clause["template_ids"] = derived[str(spec["derived"])]
    return clause


def _signed_slug(bonus: int) -> str:
    return f"plus_{bonus}" if bonus >= 0 else f"minus_{-bonus}"


def _apply_mechanics(template: dict[str, object]) -> None:
    patch = _MAGIC_ITEM_MECHANICS.get(str(template["id"]))
    if patch is None:
        return
    for key, value in patch.items():
        if key == "params":
            merged = dict(template.get("params", {}))
            merged.update(value)
            template["params"] = merged
        else:
            template[key] = value


def _template(
    item_id: str,
    name: str,
    category: str,
    *,
    manual: list[str] | None = None,
    weight_coins: int = 0,
    **fields: object,
) -> dict[str, object]:
    template: dict[str, object] = {
        "id": item_id,
        "name": name,
        "category": category,
        "manual": manual or [],
        "weight_coins": weight_coins,
        **fields,
    }
    _apply_mechanics(template)
    return template


def _compile_potions(srd_dir: Path, weights: dict[str, int]) -> tuple[list[dict], list[dict]]:
    page = _read(srd_dir, "Potions.md")
    control_page = _read(srd_dir, "Potion_of_Control.md")
    page_notes = _page_prose(page)
    rows = []
    templates: dict[str, dict] = {}
    for basic_value, expert_min, expert_max, name in _category_table(page):
        if name.startswith("Control "):
            item_id = slugify(f"potion_of_{name}")
            manual = [
                *_page_prose(control_page, heading=None)[:4],
                *_page_prose(control_page, heading=name),
            ]
        else:
            item_id = slugify(f"potion_of_{name}")
            manual = _page_prose(_read(srd_dir, f"Potion_of_{name.replace(' ', '_')}.md"))
        if item_id not in templates:
            templates[item_id] = _template(
                item_id,
                f"Potion of {name}",
                "potion",
                manual=[*manual, *page_notes],
                weight_coins=weights["potion"],
            )
        rows.append(
            {"item_ids": [item_id], "basic_value": basic_value, "expert_min": expert_min, "expert_max": expert_max}
        )
    return rows, list(templates.values())


def _compile_rings(srd_dir: Path, weights: dict[str, int]) -> tuple[list[dict], list[dict]]:
    page = _read(srd_dir, "Rings.md")
    page_notes = _page_prose(page)
    rows = []
    templates: dict[str, dict] = {}
    for basic_value, expert_min, expert_max, name in _category_table(page):
        params: dict[str, object] = {}
        if name in _WISH_COUNTS:
            item_id, filename = "ring_of_wishes", "Ring_of_Wishes.md"
            display = "Ring of Wishes"
            params = {"wish_count_dice": _WISH_COUNTS[name]}
            manual = _page_prose(_read(srd_dir, filename))
        elif name in _RING_ENTRIES:
            item_id, filename = _RING_ENTRIES[name]
            display = f"Ring of {name}" if not name.startswith("Protection") else f"Ring of {name}"
            if item_id == "ring_of_protection_5_radius":
                source = _read(srd_dir, filename)
                manual = [
                    *_page_prose(source, heading="Ring of Protection"),
                    *_page_prose(source, heading="Ring of Protection, 5’ Radius"),
                ]
                display = "Ring of Protection, 5’ Radius"
            elif item_id == "ring_of_protection":
                manual = _page_prose(_read(srd_dir, filename), heading="Ring of Protection")
                display = "Ring of Protection"
            else:
                manual = _page_prose(_read(srd_dir, filename))
                display = strip_links(_read(srd_dir, filename)).splitlines()[0].removeprefix("# ").strip()
        else:
            raise ValueError(f"unmapped ring cell {name!r}")
        if item_id not in templates:
            templates[item_id] = _template(item_id, display, "ring", manual=[*manual, *page_notes])
        row: dict[str, object] = {
            "item_ids": [item_id],
            "basic_value": basic_value,
            "expert_min": expert_min,
            "expert_max": expert_max,
        }
        if params:
            row["params"] = params
        rows.append(row)
    return rows, list(templates.values())


def _compile_devices(srd_dir: Path, weights: dict[str, int]) -> tuple[list[dict], list[dict]]:
    page = _read(srd_dir, "Rods%2C_Staves%2C_Wands.md")
    rows = []
    templates: dict[str, dict] = {}
    category_notes = {
        "rod": _page_prose(page, heading="Rods"),
        "staff": [*_page_prose(page, heading="Staves"), *_page_prose(page, heading="Staves in Melee")],
        "wand": _page_prose(page, heading="Wands"),
    }
    charge_notes = _page_prose(page, heading="Charges")
    for basic_value, expert_min, expert_max, name in _category_table(page):
        item_id = slugify(name)
        category = item_id.split("_")[0]
        if category not in ("rod", "staff", "wand"):
            raise ValueError(f"unexpected device name {name!r}")
        if item_id not in templates:
            manual = _page_prose(_read(srd_dir, f"{name.replace(' ', '_')}.md"))
            usable_by: dict[str, object]
            if category == "wand":
                usable_by = {"kind": "caster", "caster": "arcane"}
            elif category == "staff":
                usable_by = {"kind": "caster", "caster": _STAFF_CASTERS.get(item_id, "any")}
            else:
                usable_by = {"kind": "all"}
            templates[item_id] = _template(
                item_id,
                name,
                category,
                manual=[*manual, *category_notes[category], *charge_notes],
                weight_coins=weights[category],
                charges_dice=_DEFAULT_CHARGES[category],
                usable_by=usable_by,
            )
        rows.append(
            {"item_ids": [item_id], "basic_value": basic_value, "expert_min": expert_min, "expert_max": expert_max}
        )
    return rows, list(templates.values())


def _compile_misc(srd_dir: Path) -> tuple[list[dict], list[dict]]:
    page = _read(srd_dir, "Miscellaneous_Items.md")
    page_notes = _page_prose(page)
    rows = []
    templates: dict[str, dict] = {}
    for basic_value, expert_min, expert_max, name in _category_table(page):
        item_id = slugify(name)
        filename, heading = _misc_page_for(name)
        if item_id not in templates:
            source = _read(srd_dir, filename)
            manual = _page_prose(source, heading=heading) if heading else _page_prose(source)
            templates[item_id] = _template(item_id, name, "misc", manual=[*manual, *page_notes])
        rows.append(
            {"item_ids": [item_id], "basic_value": basic_value, "expert_min": expert_min, "expert_max": expert_max}
        )
    return rows, list(templates.values())


def _compile_scrolls(srd_dir: Path, weights: dict[str, int]) -> tuple[list[dict], list[dict], dict]:
    page = _read(srd_dir, "Scrolls_and_Maps.md")
    rows = []
    templates: dict[str, dict] = {}
    scroll_notes = [
        "One use only: When a scroll is read, the words disappear.",
        "Light: A scroll can only be used if there is enough light to read by.",
    ]
    spell_scroll_notes = _page_prose(page, heading="Spell Scroll")
    protection_notes = _page_prose(page, heading="Protection Scroll")
    map_notes = [
        *_page_prose(page, heading="Treasure Map"),
        *_page_prose(page, heading="Hoard Value"),
        *_page_prose(page, heading="Guardians"),
    ]
    curses = _parse_curses(page)
    protection_headings = {
        "Prot. from Elementals": "Protection from Elementals",
        "Prot. from Lycanthropes": "Protection from Lycanthropes",
        "Prot. from Magic": "Protection from Magic",
        "Prot. from Undead": "Protection from Undead",
    }
    map_recipes = _parse_map_recipes(page)
    for basic_value, expert_min, expert_max, name in _category_table(page):
        spells_match = re.fullmatch(r"(\d) Spells?", name)
        if spells_match is not None:
            count = int(spells_match[1])
            item_id = f"spell_scroll_{count}"
            if item_id not in templates:
                templates[item_id] = _template(
                    item_id,
                    f"Spell Scroll ({count} {'spell' if count == 1 else 'spells'})",
                    "scroll",
                    manual=[*spell_scroll_notes, *scroll_notes],
                    weight_coins=weights["scroll"],
                    usable_by={"kind": "caster", "caster": "any"},
                    params={"spell_count": count},
                )
        elif name == "Cursed Scroll":
            item_id = "cursed_scroll"
            if item_id not in templates:
                templates[item_id] = _template(
                    item_id,
                    "Cursed Scroll",
                    "scroll",
                    manual=[*_page_prose(page, heading="Cursed Scroll"), *scroll_notes],
                    weight_coins=weights["scroll"],
                    cursed=True,
                    curses=curses,
                )
        elif name in protection_headings:
            heading = protection_headings[name]
            item_id = slugify(f"scroll_of_{heading}")
            if item_id not in templates:
                templates[item_id] = _template(
                    item_id,
                    f"Scroll of {heading}",
                    "scroll",
                    manual=[*protection_notes, *_page_prose(page, heading=heading), *scroll_notes],
                    weight_coins=weights["scroll"],
                )
        elif name.startswith("Treasure Map: "):
            numeral = name.removeprefix("Treasure Map: ").lower()
            item_id = f"treasure_map_{numeral}"
            if item_id not in templates:
                templates[item_id] = _template(
                    item_id,
                    name,
                    "scroll",
                    manual=map_notes,
                    weight_coins=weights["scroll"],
                    hoard_recipe=map_recipes[numeral],
                )
        else:
            raise ValueError(f"unmapped scroll cell {name!r}")
        rows.append(
            {"item_ids": [item_id], "basic_value": basic_value, "expert_min": expert_min, "expert_max": expert_max}
        )
    spell_levels = _parse_scroll_spell_levels(page)
    return rows, list(templates.values()), spell_levels


def _parse_curses(page: str) -> list[dict[str, object]]:
    curses = []
    for line in _page_prose(page, heading="Example Curses"):
        name, _, prose = line.partition(":")
        curse_id = slugify(name)
        curses.append({"id": curse_id, "name": name, "prose": prose.strip(), "wired": curse_id in _WIRED_CURSES})
    if len(curses) != 6:
        raise ValueError(f"expected 6 example curses, got {len(curses)}")
    return curses


def _parse_map_recipes(page: str) -> dict[str, list[dict[str, object]]]:
    recipes: dict[str, list[dict[str, object]]] = {}
    for line in _page_prose(page, heading="Treasures"):
        match = re.fullmatch(r"([IVX]+): (.+)\.", line)
        if match is None:
            continue  # the section's intro sentence
        segments = re.split(r", | and ", match[2])
        entries = []
        for segment in segments:
            hoard = re.fullmatch(r"[Hh]oard worth (\d*d\d+) × ([\d,]+)gp", segment)
            if hoard is not None:
                entries.append({"coins": {"denomination": "gp", "dice": f"{hoard[1]}×{hoard[2].replace(',', '')}"}})
                continue
            entries.append(parse_treasure_segment(segment))
        recipes[match[1].lower()] = entries
    if set(recipes) != set(_ROMAN):
        raise ValueError(f"expected treasure maps I-XII, got {sorted(recipes)}")
    return recipes


def _parse_scroll_spell_levels(page: str) -> dict[str, object]:
    """Parse the *Random Scroll Spell Level* table (two-row spanning header)."""
    table = next(table for table in _all_tables(page) if len(table[0]) == 4 and "Spell Level" in table[0])
    header = table[1]
    if header != ["B: d6", "X: d%", "Arcane", "Divine"]:
        raise ValueError(f"unexpected scroll spell level header {header!r}")
    rows = []
    for basic_cell, expert_cell, arcane, divine in table[2:]:
        basic_min: int | None = None
        basic_max: int | None = None
        if basic_cell:
            low, high = _parse_band(basic_cell)
            basic_min, basic_max = low, high
        expert_min, expert_max = _parse_band(expert_cell)
        rows.append(
            {
                "basic_min": basic_min,
                "basic_max": basic_max,
                "expert_min": expert_min,
                "expert_max": expert_max,
                "arcane_level": int(arcane.removesuffix("st").removesuffix("nd").removesuffix("rd").removesuffix("th")),
                "divine_level": int(divine.removesuffix("st").removesuffix("nd").removesuffix("rd").removesuffix("th")),
            }
        )
    return {"rows": rows}


def _compile_swords(srd_dir: Path, derived: dict[str, list[str]]) -> tuple[list[dict], list[dict]]:
    page = _read(srd_dir, "Swords.md")
    cursed_notes = _page_prose(page, heading="Cursed Swords")
    enchanted_notes = _page_prose(page, heading="Enchanted Swords")
    rows = []
    templates: dict[str, dict] = {}
    for basic_value, expert_min, expert_max, name in _category_table(page):
        match = re.fullmatch(r"Sword ([+-]\d+)(?:, (.+))?", name)
        if match is None:
            raise ValueError(f"unparseable sword cell {name!r}")
        bonus = int(match[1])
        suffix = match[2]
        item_id = f"sword_{_signed_slug(bonus)}"
        fields: dict[str, object] = {
            "base_item_id": "sword",
            "attack_bonus": bonus,
            "damage_bonus": bonus,
        }
        manual = list(enchanted_notes)
        if suffix == "Cursed":
            item_id += "_cursed"
            fields["cursed"] = True
            manual = list(cursed_notes)
        elif suffix is not None:
            versus_match = re.fullmatch(r"([+-]\d+) vs (.+)", suffix)
            if versus_match is not None:
                target = versus_match[2]
                item_id += f"_{_signed_slug(int(versus_match[1]))}_vs_{slugify(target)}"
                fields["versus"] = [_resolve_versus(target, int(versus_match[1]), derived)]
            elif suffix in _SWORD_POWER_SUFFIXES:
                item_id += f"_{_SWORD_POWER_SUFFIXES[suffix]}"
                manual = [*manual, *_page_prose(page, heading=f"Sword {match[1]}, {suffix}")]
            else:
                raise ValueError(f"unparseable sword suffix {suffix!r}")
        if item_id not in templates:
            templates[item_id] = _template(item_id, name, "sword", manual=manual, always_active=True, **fields)
        rows.append(
            {"item_ids": [item_id], "basic_value": basic_value, "expert_min": expert_min, "expert_max": expert_max}
        )
    return rows, list(templates.values())


def _compile_weapons(srd_dir: Path, derived: dict[str, list[str]]) -> tuple[list[dict], list[dict]]:
    page = _read(srd_dir, "Weapons.md")
    enchanted_notes = _page_prose(page, heading="Enchanted Weapons")
    rows = []
    templates: dict[str, dict] = {}
    cell_pattern = re.compile(
        r"^(?P<name>[A-Za-z ]+?) (?P<bonus>[+-]\d+)(?:, (?P<suffix>[^(]+?))?(?: \((?P<qty>[^)]+)\))?$"
    )
    for basic_value, expert_min, expert_max, name in _category_table(page):
        match = cell_pattern.fullmatch(name)
        if match is None:
            raise ValueError(f"unparseable weapon cell {name!r}")
        noun = match["name"]
        bonus = int(match["bonus"])
        base = _WEAPON_BASES.get(noun)
        if base is None:
            raise ValueError(f"unmapped weapon noun {noun!r}")
        item_id = f"{slugify(noun)}_{_signed_slug(bonus)}"
        fields: dict[str, object] = {"base_item_id": base, "attack_bonus": bonus, "damage_bonus": bonus}
        manual = list(enchanted_notes)
        row_params: dict[str, object] = {}
        if match["suffix"] is not None:
            suffix = match["suffix"].strip()
            versus_match = re.fullmatch(r"([+-]\d+) vs (.+)", suffix)
            if versus_match is not None:
                target = versus_match[2]
                item_id += f"_{_signed_slug(int(versus_match[1]))}_vs_{slugify(target)}"
                fields["versus"] = [_resolve_versus(target, int(versus_match[1]), derived)]
            elif suffix in _SWORD_POWER_SUFFIXES:
                item_id += f"_{_SWORD_POWER_SUFFIXES[suffix]}"
                manual = [*manual, *_page_prose(page, heading=f"{noun} {match['bonus']}, {suffix}")]
            else:
                raise ValueError(f"unparseable weapon suffix {suffix!r}")
        if match["qty"] is not None:
            row_params = _parse_quantity(match["qty"])
        if item_id not in templates:
            if base in _AMMUNITION_BASES and "quantity_dice" in row_params and len(row_params) == 1:
                fields["quantity_dice"] = row_params["quantity_dice"]
            templates[item_id] = _template(item_id, name, "weapon", manual=manual, always_active=True, **fields)
        row: dict[str, object] = {
            "item_ids": [item_id],
            "basic_value": basic_value,
            "expert_min": expert_min,
            "expert_max": expert_max,
        }
        if row_params:
            row["params"] = row_params
        rows.append(row)
    return rows, list(templates.values())


def _parse_quantity(text: str) -> dict[str, object]:
    """Parse an ammunition quantity cell: `3d10 arrows` or `Basic: 10; Expert: 2d6 arrows`."""
    tiered = re.fullmatch(r"Basic: (\d+); Expert: (\d*d\d+) (?:arrows|bolts)", text)
    if tiered is not None:
        return {"quantity_dice": tiered[2], "basic_quantity_fixed": int(tiered[1])}
    plain = re.fullmatch(r"(\d*d\d+) (?:arrows|bolts)", text)
    if plain is None:
        raise ValueError(f"unparseable quantity cell {text!r}")
    return {"quantity_dice": plain[1]}


def _compile_armour(srd_dir: Path) -> tuple[list[dict], list[dict], dict]:
    page = _read(srd_dir, "Armour_and_Shields.md")
    cursed_notes = _page_prose(page, heading="Cursed Armour and Shields")
    enchanted_notes = _page_prose(page, heading="Enchanted Armour and Shields")
    rows = []
    templates: dict[str, dict] = {}

    def _ensure(cell: str) -> str:
        item_id, fields, manual = _parse_armour_item(cell, cursed_notes, enchanted_notes)
        if item_id not in templates:
            templates[item_id] = _template(item_id, cell, "armour", manual=manual, always_active=True, **fields)
        return item_id

    for basic_value, expert_min, expert_max, name in _category_table(page):
        if ", Shield" in name:
            armour_cell, _, shield_bonus = name.partition(", Shield ")
            item_ids = [_ensure(armour_cell), _ensure(f"Shield {shield_bonus}")]
        elif " with Shield " in name:
            armour_cell, _, shield_bonus = name.partition(" with Shield ")
            item_ids = [_ensure(armour_cell), _ensure(f"Shield {shield_bonus}")]
        else:
            item_ids = [_ensure(name)]
        rows.append(
            {"item_ids": item_ids, "basic_value": basic_value, "expert_min": expert_min, "expert_max": expert_max}
        )
    armour_type_rows = []
    type_table = tables_after_heading(page, "Type of Armour")[0]
    type_bases = {"Leather": "leather", "Chainmail": "chainmail", "Plate mail": "plate_mail"}
    for roll_cell, type_name in type_table[1:]:
        low, high = _parse_band(roll_cell)
        armour_type_rows.append({"roll_min": low, "roll_max": high, "base_item_id": type_bases[type_name]})
    return rows, list(templates.values()), {"rows": armour_type_rows}


def _parse_armour_item(
    cell: str, cursed_notes: list[str], enchanted_notes: list[str]
) -> tuple[str, dict[str, object], list[str]]:
    ac_set = re.fullmatch(r"Cursed (Armour|Shield), AC (\d+) \[(\d+)\]", cell)
    if ac_set is not None:
        kind = ac_set[1].lower()
        item_id = f"cursed_{kind}_ac_{ac_set[2]}"
        fields: dict[str, object] = {"cursed": True, "ac_set": int(ac_set[2]), "ac_set_ascending": int(ac_set[3])}
        if kind == "shield":
            fields["base_item_id"] = "shield"
        return item_id, fields, list(cursed_notes)
    match = re.fullmatch(r"(Cursed )?(Armour|Shield) ([+-]\d+)", cell)
    if match is None:
        raise ValueError(f"unparseable armour cell {cell!r}")
    cursed = match[1] is not None
    kind = match[2].lower()
    bonus = int(match[3])
    item_id = f"{'cursed_' if cursed else ''}{kind}_{_signed_slug(bonus)}"
    fields = {"ac_bonus": bonus, "cursed": cursed}
    if kind == "shield":
        fields["base_item_id"] = "shield"
    return item_id, fields, list(cursed_notes if cursed else enchanted_notes)


def _compile_sentient_swords(srd_dir: Path) -> dict[str, object]:
    page = _read(srd_dir, "Sentient_Swords.md")
    sensory_page = _read(srd_dir, "Sensory_Powers_of_Sentient_Swords.md")
    extraordinary_page = _read(srd_dir, "Extraordinary_Powers_of_Sentient_Swords.md")
    purpose_page = _read(srd_dir, "Special_Purpose_of_Sentient_Swords.md")
    tables = _all_tables(page)
    communication_table = next(table for table in tables if table[0] == ["INT", "Reading", "Communication"])
    languages_table = next(table for table in tables if table[0] == ["d100", "Languages"])
    alignment_table = next(table for table in tables if table[0] == ["d20", "Alignment"])
    powers_table = next(table for table in tables if table[0] == ["INT", "Powers"])

    communication = [
        {"int_score": int(row[0]), "reading": row[1] == "Yes", "communication": row[2].lower()}
        for row in communication_table[1:]
    ]
    languages = []
    for roll_cell, result in languages_table[1:]:
        low, high = _parse_band(roll_cell)
        if result == "Roll twice again, adding results":
            languages.append({"roll_min": low, "roll_max": high, "result": "roll_twice"})
        else:
            count = re.fullmatch(r"Alignment tongue \+ (\d)", result)
            languages.append({"roll_min": low, "roll_max": high, "result": count[1]})
    alignment = []
    for roll_cell, result in alignment_table[1:]:
        low, high = _parse_band(roll_cell)
        alignment.append({"roll_min": low, "roll_max": high, "result": result.lower()})
    powers = []
    for int_cell, result in powers_table[1:]:
        match = re.fullmatch(r"(\d) sensory powers?( \+ 1 extraordinary)?", result)
        powers.append({"int_score": int(int_cell), "sensory": int(match[1]), "extraordinary": 1 if match[2] else 0})

    def _power_bands(page_text: str, mapping: dict[str, str]) -> list[dict[str, object]]:
        table = next(table for table in _all_tables(page_text) if table[0][0] == "d100")
        bands = []
        for roll_cell, result in table[1:]:
            low, high = _parse_band(roll_cell)
            if result not in mapping:
                raise ValueError(f"unmapped sword power {result!r}")
            bands.append({"roll_min": low, "roll_max": high, "result": mapping[result]})
        return bands

    powers_catalog = []
    for heading, power_id in (
        ("Detect Evil or Good", "detect_evil_or_good"),
        ("Detect Gems", "detect_gems"),
        ("Detect Magic", "detect_magic"),
        ("Detect Metals", "detect_metals"),
        ("Detect Shifting Architecture", "detect_shifting_architecture"),
        ("Detect Slopes", "detect_slopes"),
        ("Detect Traps", "detect_traps"),
        ("Locate Secret Doors", "locate_secret_doors"),
        ("See Invisible Objects", "see_invisible_objects"),
    ):
        powers_catalog.append(
            {"id": power_id, "name": heading, "prose": "\n".join(_page_prose(sensory_page, heading=heading))}
        )
    for heading, power_id, duplicates in (
        ("Clairaudience", "clairaudience", False),
        ("Clairvoyance", "clairvoyance", False),
        ("ESP", "esp", False),
        ("Extra Damage", "extra_damage", True),
        ("Flying", "flying", False),
        ("Healing", "healing", True),
        ("Illusion", "illusion", False),
        ("Levitation", "levitation", False),
        ("Telekinesis", "telekinesis", False),
        ("Telepathy", "telepathy", False),
        ("Teleportation", "teleportation", False),
        ("X-Ray Vision", "x_ray_vision", False),
    ):
        powers_catalog.append(
            {
                "id": power_id,
                "name": heading,
                "prose": "\n".join(_page_prose(extraordinary_page, heading=heading)),
                "extraordinary": True,
                "duplicates_allowed": duplicates,
            }
        )

    purpose_table = next(table for table in _all_tables(purpose_page) if table[0][0] == "d6")
    special_purposes = []
    for roll_cell, result in purpose_table[1:]:
        low, high = _parse_band(roll_cell)
        if result not in _SPECIAL_PURPOSES:
            raise ValueError(f"unmapped special purpose {result!r}")
        special_purposes.append({"roll_min": low, "roll_max": high, "result": _SPECIAL_PURPOSES[result]})

    return {
        "communication": communication,
        "languages": languages,
        "alignment": alignment,
        "powers": powers,
        "sensory_bands": _power_bands(sensory_page, _SENSORY_POWERS),
        "extraordinary_bands": _power_bands(extraordinary_page, _EXTRAORDINARY_POWERS),
        "powers_catalog": powers_catalog,
        "special_purposes": special_purposes,
        "special_purpose_prose": "\n".join(_page_prose(purpose_page, heading="Alignment Power")),
        "alignment_touch_prose": "\n".join(_page_prose(page, heading="Alignment")),
    }


def compile_magic_items(
    srd_dir: Path, monsters: list[dict[str, object]], treasure_weights: list[dict[str, object]]
) -> dict[str, object]:
    """Compile the magic item catalog into the `magic_items.json` structure (sans `_meta`).

    Args:
        srd_dir: The directory holding the scraped SRD markdown.
        monsters: The compiled monster entries, for the versus target sets.
        treasure_weights: The compiled `TreasureWeight` rows, for potion, scroll,
            and device weights.

    Returns:
        The raw dict ready for `MagicItemCatalog` validation.

    Raises:
        ValueError: If any table cell, page, or mapping fails to parse or resolve.
    """
    weights = {str(row["id"]): int(row["weight_coins"]) for row in treasure_weights}
    derived = _derived_versus_sets(monsters)

    potion_rows, potion_templates = _compile_potions(srd_dir, weights)
    ring_rows, ring_templates = _compile_rings(srd_dir, weights)
    device_rows, device_templates = _compile_devices(srd_dir, weights)
    misc_rows, misc_templates = _compile_misc(srd_dir)
    scroll_rows, scroll_templates, spell_levels = _compile_scrolls(srd_dir, weights)
    ward_sets = _ward_target_sets(monsters)
    for template in scroll_templates:
        effect = template.get("effect")
        if isinstance(effect, dict) and effect.get("kind") == "ward":
            target_set = ward_sets.get(str(effect["params"].get("targets")))
            if target_set is not None:
                effect["params"] = {**effect["params"], "bars_template_ids": tuple(target_set)}
    sword_rows, sword_templates = _compile_swords(srd_dir, derived)
    weapon_rows, weapon_templates = _compile_weapons(srd_dir, derived)
    armour_rows, armour_templates, armour_type = _compile_armour(srd_dir)

    def _die(page_name: str) -> int:
        return _basic_die(_read(srd_dir, page_name))

    sub_tables = [
        {"category": "armour", "basic_die": _die("Armour_and_Shields.md"), "rows": armour_rows},
        {"category": "misc", "basic_die": _die("Miscellaneous_Items.md"), "rows": misc_rows},
        {"category": "potion", "basic_die": _die("Potions.md"), "rows": potion_rows},
        {"category": "ring", "basic_die": _die("Rings.md"), "rows": ring_rows},
        {"category": "rod_staff_wand", "basic_die": _die("Rods%2C_Staves%2C_Wands.md"), "rows": device_rows},
        {"category": "scroll", "basic_die": _die("Scrolls_and_Maps.md"), "rows": scroll_rows},
        {"category": "sword", "basic_die": _die("Swords.md"), "rows": sword_rows},
        {"category": "weapon", "basic_die": _die("Weapons.md"), "rows": weapon_rows},
    ]
    items = [
        *potion_templates,
        *ring_templates,
        *device_templates,
        *misc_templates,
        *scroll_templates,
        *sword_templates,
        *weapon_templates,
        *armour_templates,
    ]
    known_ids = {str(item["id"]) for item in items}
    for mechanics_id in _MAGIC_ITEM_MECHANICS:
        if mechanics_id not in known_ids:
            raise ValueError(f"mechanics entry {mechanics_id!r} resolves to no catalog item")
    return {
        "items": sorted(items, key=lambda item: str(item["id"])),
        "sub_tables": sub_tables,
        "armour_type": armour_type,
        "scroll_spell_levels": spell_levels,
        "sentient_swords": _compile_sentient_swords(srd_dir),
    }
