"""Parser for the SRD equipment pages → `equipment.json`.

Sources — beware the filenames: `Weapons.md` and `Armour_and_Shields.md` are the
*magic item* pages. Mundane weapon/armour tables live in `Weapons_and_Armour.md`, gear
in `Adventuring_Gear.md`, and treasure weights in `Time%2C_Weight%2C_Movement.md` (the
URL-encoded scrape filename).

Torch, holy water, and burning oil appear in both the 22-row Weapon Combat Stats table
and the 24-row Adventuring Gear list. Pinned: one entry per physical item — they
compile as gear carrying an embedded combat facet, so the weapons list holds the 19
pure weapons and no item ever has two ids.

Decided oddities: torch cost `1 (for 6)` normalizes to lot size 6; holy water and
burning oil weight `-` means no tracked weight (gear is covered by the
detailed-encumbrance flat 80 coins); sling stones cost `Free` compiles to cost 0, lot
size 1; `Stakes (3) and mallet` is one kit item; the ammunition table has no weight
column and compiles to weight 0 — per the SRD, missile weapon weights already include
the ammunition and its container.
"""

import re
from pathlib import Path

from .pipetable import parse_range, section_prose, slugify, tables_after_heading

SOURCE_PAGES = ("Weapons_and_Armour.md", "Adventuring_Gear.md", "Time%2C_Weight%2C_Movement.md")

# The three dual-listed items: weapon-table name → the gear id the facet attaches to.
_FACET_ITEMS = {"Holy water (vial)": "holy_water", "Oil (flask), burning": "oil_flask", "Torch": "torch"}

# Exact SRD gear names → stable ids. An unknown name is an error, so SRD drift is
# caught at compile time instead of shipping a silently renamed id.
_GEAR_IDS = {
    "Backpack": "backpack",
    "Crowbar": "crowbar",
    "Garlic": "garlic",
    "Grappling hook": "grappling_hook",
    "Hammer (small)": "hammer",
    "Holy symbol": "holy_symbol",
    "Holy water (vial)": "holy_water",
    "Iron spikes (12)": "iron_spikes",
    "Lantern": "lantern",
    "Mirror (hand-sized, steel)": "mirror",
    "Oil (1 flask)": "oil_flask",
    "Pole (10’ long, wooden)": "pole",
    "Rations (iron, 7 days)": "rations_iron",
    "Rations (standard, 7 days)": "rations_standard",
    "Rope (50’)": "rope",
    "Sack (large)": "sack_large",
    "Sack (small)": "sack_small",
    "Stakes (3) and mallet": "stakes_and_mallet",
    "Thieves’ tools": "thieves_tools",
    "Tinder box (flint & steel)": "tinder_box",
    "Torches (6)": "torch",
    "Waterskin": "waterskin",
    "Wine (2 pints)": "wine",
    "Wolfsbane (1 bunch)": "wolfsbane",
}

# Purchase lots: the item plus a lot size, rather than counts encoded in ids. Units:
# one torch, one iron spike, one day of rations, one pint of wine. `Stakes (3) and
# mallet` is one kit item (pinned), so it is absent here.
_GEAR_LOT_SIZES = {"torch": 6, "iron_spikes": 12, "rations_iron": 7, "rations_standard": 7, "wine": 2}

# Structured exploration mechanics, hand-curated from the Descriptions prose (the
# `_CLASS_ABILITIES` precedent), pinned per the Phase 4 plan: torch "casts light in a
# 30' radius and burns for 1 hour (6 turns)"; lantern "burns one oil flask every four
# hours (24 turns)"; tinder box "one round per attempt, 2-in-6 chance of success";
# rations mark the daily consumable (the 7-day lot quantity already ships via
# `_GEAR_LOT_SIZES`, so no duplicate day count — the standard/iron distinction rides
# the ids). Flame brightness is the printed 1-in-6 wandering baseline; *continual
# light* carries `"daylight"`.
_GEAR_MECHANICS: dict[str, dict[str, int | str | bool]] = {
    "torch": {"burn_turns": 6, "light_radius_feet": 30, "brightness": "flame"},
    "lantern": {"burn_turns_per_flask": 24, "light_radius_feet": 30, "brightness": "flame"},
    "oil_flask": {"fuels_lantern": True},
    "tinder_box": {"light_chance_in_six": 2},
    "rations_iron": {"ration": True},
    "rations_standard": {"ration": True},
    "iron_spikes": {"wedges_doors": True},
    "waterskin": {"water_container": True},
    "thieves_tools": {"required_for": "open_locks"},
}

# Basic-encumbrance categories, from Weapons and Armour: "Leather armour counts as
# light armour, chainmail and plate mail count as heavy armour."
_ARMOUR_CATEGORIES = {"leather": "light", "chainmail": "heavy", "plate_mail": "heavy"}

_AMMUNITION_IDS = {
    "Arrows (quiver of 20)": ("arrows", 20),
    "Crossbow bolts (case of 30)": ("crossbow_bolts", 30),
    "Silver tipped arrow (1)": ("silver_tipped_arrow", 1),
    "Sling stones": ("sling_stones", 1),
}

_TREASURE_IDS = {
    "Coin (any type)": "coin",
    "Gem": "gem",
    "Jewellery (1 piece)": "jewellery",
    "Potion": "potion",
    "Rod": "rod",
    "Scroll": "scroll",
    "Staff": "staff",
    "Wand": "wand",
}

_QUALITY_MAP = {
    "Blunt": "blunt",
    "Brace": "brace",
    "Charge": "charge",
    "Melee": "melee",
    "Reload": "reload",
    "Slow": "slow",
    "Splash weapon": "splash",
    "Two-handed": "two_handed",
}

_MISSILE_PATTERN = re.compile(r"Missile \(([^)]*)\)")


def _weapon_id(name: str) -> str:
    return name.lower().replace("-", "_").replace(" ", "_")


def _parse_qualities(cell: str) -> tuple[list[str], dict[str, object] | None]:
    """Parse a qualities cell into (quality names, missile range bands or None)."""
    qualities: list[str] = []
    ranges: dict[str, object] | None = None
    for token in cell.split(", "):
        missile = _MISSILE_PATTERN.fullmatch(token)
        if missile:
            qualities.append("missile")
            bands = []
            for band_text in missile[1].split(" / "):
                low, high = parse_range(band_text.replace("’", "").strip())
                bands.append({"min_feet": low, "max_feet": high})
            if len(bands) != 3:
                raise ValueError(f"expected three range bands in {cell!r}")
            ranges = {"short": bands[0], "medium": bands[1], "long": bands[2]}
        elif token in _QUALITY_MAP:
            qualities.append(_QUALITY_MAP[token])
        else:
            raise ValueError(f"unknown weapon quality {token!r}")
    return sorted(qualities), ranges


def _parse_cost(cell: str) -> int:
    if cell == "Free":
        return 0
    match = re.fullmatch(r"(\d+)(?: \(for \d+\))?", cell)
    if match is None:
        raise ValueError(f"unparseable cost cell {cell!r}")
    return int(match[1])


def compile_equipment(srd_dir: Path) -> dict[str, object]:
    """Compile the equipment lists into the `equipment.json` structure (sans `_meta`).

    Args:
        srd_dir: The directory holding the scraped SRD markdown.

    Returns:
        The raw dict ready for `EquipmentCatalog` validation, entries sorted by id.
    """
    weapons_page = (srd_dir / "Weapons_and_Armour.md").read_text(encoding="utf-8")
    gear_page = (srd_dir / "Adventuring_Gear.md").read_text(encoding="utf-8")
    weight_page = (srd_dir / "Time%2C_Weight%2C_Movement.md").read_text(encoding="utf-8")

    weapons: list[dict[str, object]] = []
    facets: dict[str, dict[str, object]] = {}
    weapon_table = tables_after_heading(weapons_page, "Weapons")[0]
    for row in weapon_table[1:]:
        name, cost_cell, weight_cell, damage, qualities_cell = row[:5]
        qualities, ranges = _parse_qualities(qualities_cell)
        if name in _FACET_ITEMS:
            facet: dict[str, object] = {"damage": damage, "qualities": qualities}
            if ranges is not None:
                facet["missile_ranges"] = ranges
            facets[_FACET_ITEMS[name]] = facet
            continue
        weapon: dict[str, object] = {
            "id": _weapon_id(name),
            "name": name,
            "cost_gp": _parse_cost(cost_cell),
            "weight_coins": int(weight_cell),
            "damage": damage,
            "qualities": qualities,
            "material": "silver" if "Silver" in name else "standard",
        }
        if ranges is not None:
            weapon["missile_ranges"] = ranges
        weapons.append(weapon)
    if len(weapons) != 19 or set(facets) != {"holy_water", "oil_flask", "torch"}:
        raise ValueError(f"expected 19 weapons and 3 facets, got {len(weapons)} and {sorted(facets)}")

    ammunition = []
    for row in tables_after_heading(weapons_page, "Ammunition")[0][1:]:
        name, cost_cell = row[:2]
        if name not in _AMMUNITION_IDS:
            raise ValueError(f"unknown ammunition {name!r}")
        ammunition_id, lot_size = _AMMUNITION_IDS[name]
        ammunition.append(
            {
                "id": ammunition_id,
                "name": name,
                "cost_gp": _parse_cost(cost_cell),
                "lot_size": lot_size,
                "weight_coins": 0,
                "material": "silver" if "Silver" in name else "standard",
            }
        )

    armour = []
    for row in tables_after_heading(weapons_page, "Armour")[0][1:]:
        name, ac_cell, cost_cell, weight_cell = row[:4]
        armour_id = name.lower().replace(" ", "_")
        entry: dict[str, object] = {
            "id": armour_id,
            "name": name,
            "cost_gp": _parse_cost(cost_cell),
            "weight_coins": int(weight_cell),
        }
        bonus_match = re.fullmatch(r"\+(\d+) bonus", ac_cell)
        if bonus_match:
            entry["ac_bonus"] = int(bonus_match[1])
        else:
            ac_match = re.fullmatch(r"(\d+) \[(\d+)\]", ac_cell)
            if ac_match is None:
                raise ValueError(f"unparseable AC cell {ac_cell!r}")
            entry["ac"] = int(ac_match[1])
            entry["ac_ascending"] = int(ac_match[2])
            entry["category"] = _ARMOUR_CATEGORIES[armour_id]
        armour.append(entry)

    # Container capacities from the Descriptions prose ("Holds up to 400 coins");
    # the "coins" unit excludes the waterskin's liquid capacity.
    capacities: dict[str, int] = {}
    for line in section_prose(gear_page, "Descriptions").splitlines():
        match = re.match(r"(.+?): .*?up to ([\d,]+) coins", line)
        if match:
            capacities[slugify(match[1])] = int(match[2].replace(",", ""))

    gear = []
    gear_table = tables_after_heading(gear_page, "Adventuring Gear")[0]
    for row in gear_table[1:]:
        name, cost_cell = row[:2]
        if name not in _GEAR_IDS:
            raise ValueError(f"unknown gear item {name!r}")
        gear_id = _GEAR_IDS[name]
        entry = {
            "id": gear_id,
            "name": name,
            "cost_gp": _parse_cost(cost_cell),
            "lot_size": _GEAR_LOT_SIZES.get(gear_id, 1),
        }
        if gear_id in capacities:
            entry["capacity_coins"] = capacities[gear_id]
        if gear_id in facets:
            entry["combat"] = facets[gear_id]
        if gear_id in _GEAR_MECHANICS:
            entry["params"] = _GEAR_MECHANICS[gear_id]
        gear.append(entry)
    if len(gear) != 24:
        raise ValueError(f"expected 24 gear items, got {len(gear)}")

    treasure_weights = []
    for row in tables_after_heading(weight_page, "Encumbrance (Optional Rule)")[0][1:]:
        name, weight_cell = row[:2]
        if name not in _TREASURE_IDS:
            raise ValueError(f"unknown treasure weight row {name!r}")
        treasure_weights.append({"id": _TREASURE_IDS[name], "weight_coins": int(weight_cell)})

    return {
        "weapons": sorted(weapons, key=lambda entry: entry["id"]),
        "armour": sorted(armour, key=lambda entry: entry["id"]),
        "gear": sorted(gear, key=lambda entry: entry["id"]),
        "ammunition": sorted(ammunition, key=lambda entry: entry["id"]),
        "treasure_weights": sorted(treasure_weights, key=lambda entry: entry["id"]),
    }
