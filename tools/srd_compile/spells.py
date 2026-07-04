"""Parser for the 106 SRD spell pages → `spells.json`.

The manifest is the two list pages — `Cleric_Spells.md` (34 entries over levels 1–5)
and `Magic-User_Spells.md` (72 entries, 12 per level over levels 1–6) — read as the
authority, exactly like `Monster_Descriptions.md` was for monsters. Spell pages are
identified by the italic level line (`*3rd Level [Magic-User Spell](...)*`) — never by
the `**Duration:**`/`**Range:**` labels, which also appear on potion, hazard, and
water-vessel pages and would false-positive a label scanner.

Hazards handled here: filename encoding is inconsistent — parentheses are
single-encoded (`%28`) but the typographic foot mark `’` is double-encoded on disk
(`Silence_15%25E2%2580%2599_Radius.md`), and the list-page hrefs use single encoding
for the same files, so the href→file resolver re-encodes `%` → `%25` (the same
`quote()` mapping the monster manifest uses). Durations range from keywords through
dice and per-level arithmetic to pure prose and slash-separated dual forms; anything
unparseable keeps `kind="special"` with the raw string — the parser never fails on
prose. *Charm person*/*charm monster* put a second `**Duration:**` label deeper in
the page — the parser takes the first. `Sticks_to_Snakes.md` and
`Conjure_Elemental.md` embed inline stat lines (`**AC** 6 [13], **HD** 1 (4hp), …`)
parsed with the Phase 2 cell parsers; the elemental blocks are matched against
`monsters.json` instead of recompiled. `Spells.md` is the single rules source —
`Rules_of_Magic.md` carries identical content and is never read.

Structured mechanics are hand-curated in `_SPELL_MECHANICS` below (the Phase 1
`_CLASS_ABILITIES` precedent), never parsed from prose; the SRD prose ships
alongside and stays the authority for narration. The compiler fails if a mechanics
id doesn't resolve to a parsed page. The nine concepts with separate `(C)` and
`(MU)` pages compile as two entries with `_c`/`_mu` suffixes (the pairs differ
mechanically); the *invisible stalker* spell page's `(MU)` marker distinguishes it
from the monster page, not a dual-class pair — its id stays plain
`invisible_stalker`.
"""

import re
from pathlib import Path
from urllib.parse import quote

from . import monsters
from .pipetable import slugify, strip_emphasis, strip_links

LIST_PAGES = ("Cleric_Spells.md", "Magic-User_Spells.md")

_LIST_CLASSES = {"Cleric_Spells.md": "cleric", "Magic-User_Spells.md": "magic_user"}

_LEVEL_LINE = re.compile(r"\*(\d)(?:st|nd|rd|th) Level \[(Cleric|Magic-User) Spell\]\([^)]*\)\*")
_LEVEL_CLASSES = {"Cleric": "cleric", "Magic-User": "magic_user"}
_LIST_HEADING = re.compile(r"^## (\d)(?:st|nd|rd|th) Level$")
_LIST_ENTRY = re.compile(r"^\d+\. \[([^\]]+)\]\(/srd/index\.php/((?:[^()\s]|\([^()]*\))+)\s+\"")
_CLASS_MARKER = re.compile(r"\s*\((C|MU)\)$")
_REVERSED_HEADING = re.compile(r"^## Reversed: (.+)$")
_STAT_LINE_KEY = re.compile(r"\*\*(AC|HD|Att|THAC0|MV|SV|ML|AL|XP)\*\*")
_NUMBERED_ITEM = re.compile(r"^(\d+)\. (.+)$")

_DUAL_EXEMPT = {"invisible_stalker"}  # the (MU) marker distinguishes the monster page, not a (C) twin

_DURATION_FIXED = re.compile(r"^(?:(\d+)|(\d*d\d+(?:[+-]\d+)?)) (round|turn|day)s?( \+(\d+) per level)?$")
_DURATION_PER_LEVEL = re.compile(r"^(\d+) (round|turn|day)s? per level$")
_DURATION_CONCENTRATION = re.compile(r"^Concentration( \(up to (\d+) (round|turn|day)s?\))?$")

_RANGE_FEET = re.compile(r"^(\d+)’( around the caster)?$")
_RANGE_YARDS = re.compile(r"^(\d+) yards around the caster$")
_RANGE_PER_LEVEL = re.compile(r"^(?:(\d+)’ \+)?(\d+)’ per level$")
_TOUCH_RANGES = {
    "Touch",
    "The caster or a creature touched",
    "The caster or a creature or object touched",
}


def _mode(
    key: str,
    *,
    targeting: dict[str, object] | None = None,
    save: dict[str, object] | None = None,
    effect: dict[str, object] | None = None,
    manual: bool = False,
    usage: int | None = None,
) -> dict[str, object]:
    """Build one `_SPELL_MECHANICS` mode entry; `usage` binds a numbered item's prose."""
    return {"key": key, "targeting": targeting, "save": save, "effect": effect, "manual": manual, "usage": usage}


def _cond(
    condition: str,
    *,
    params: dict[str, object] | None = None,
    modifiers: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {"kind": "condition", "condition": condition, "params": params or {}, "modifiers": modifiers or []}


def _charm_effect(person_gate: bool) -> dict[str, object]:
    params: dict[str, object] = {"excludes_undead": True, "indefinite": True, "tick": "charm_resave"}
    if person_gate:
        params["person_gate"] = True
    return _cond("charmed", params=params)


_HOLD_EFFECT_PERSON = _cond("paralysed", params={"person_gate": True, "excludes_undead": True})
_HOLD_EFFECT_ANY = _cond("paralysed", params={"excludes_undead": True})

_HOLD_MODES = [
    _mode(
        "individual",
        targeting={"mode": "single"},
        save={"category": "spells", "modifier": -2},
        effect=_HOLD_EFFECT_PERSON,
        usage=1,
    ),
    _mode(
        "group",
        targeting={"mode": "up_to_n", "count_dice": "1d4"},
        save={"category": "spells"},
        effect=_HOLD_EFFECT_PERSON,
        usage=2,
    ),
]

_LIGHT_MODES = [
    _mode(
        "illuminate",
        targeting={"mode": "single"},
        effect={"kind": "attach_only", "params": {"effect_kind": "light", "radius_feet": 15, "brightness": "reading"}},
        usage=1,
    ),
    _mode(
        "blind",
        targeting={"mode": "single"},
        save={"category": "spells"},
        effect=_cond("blind"),
        usage=2,
    ),
    _mode(
        "cancel",
        targeting={"mode": "single"},
        effect={"kind": "cure", "cures_effect_kinds": ["darkness"]},
        usage=3,
    ),
]

_DARKNESS_MODES = [
    _mode(
        "darken",
        targeting={"mode": "single"},
        effect={
            "kind": "attach_only",
            "params": {"effect_kind": "darkness", "radius_feet": 15, "blocks_infravision": False},
        },
    ),
    _mode("blind", targeting={"mode": "single"}, save={"category": "spells"}, effect=_cond("blind")),
    _mode("cancel", targeting={"mode": "single"}, effect={"kind": "cure", "cures_effect_kinds": ["light"]}),
]

_CONTINUAL_LIGHT_MODES = [
    _mode(
        "illuminate",
        targeting={"mode": "single"},
        effect={
            "kind": "attach_only",
            "params": {"effect_kind": "continual_light", "radius_feet": 30, "brightness": "daylight"},
        },
        usage=1,
    ),
    _mode("blind", targeting={"mode": "single"}, save={"category": "spells"}, effect=_cond("blind"), usage=2),
    _mode(
        "cancel",
        targeting={"mode": "single"},
        effect={"kind": "cure", "cures_effect_kinds": ["continual_darkness"]},
        usage=3,
    ),
]

_CONTINUAL_DARKNESS_MODES = [
    _mode(
        "darken",
        targeting={"mode": "single"},
        effect={
            "kind": "attach_only",
            "params": {"effect_kind": "continual_darkness", "radius_feet": 30, "blocks_infravision": True},
        },
    ),
    _mode("blind", targeting={"mode": "single"}, save={"category": "spells"}, effect=_cond("blind")),
    _mode("cancel", targeting={"mode": "single"}, effect={"kind": "cure", "cures_effect_kinds": ["continual_light"]}),
]

_PROTECTION_FROM_EVIL_MODES = [
    _mode(
        "ward",
        targeting={"mode": "self"},
        effect={
            "kind": "modifiers",
            "modifiers": [
                {"kind": "save_bonus", "value": 1, "versus_other_alignment": True},
                {"kind": "attack_penalty_of_attackers", "value": -1, "versus_other_alignment": True},
            ],
            "params": {"bars_melee_from": ("enchanted", "constructed", "summoned")},
        },
    ),
]

_PROTECTION_RADIUS_MODES = [
    _mode(
        "ward",
        targeting={"mode": "area", "shape": "sphere", "dimensions": {"radius_feet": 10}},
        effect={
            "kind": "modifiers",
            "modifiers": [
                {"kind": "save_bonus", "value": 1, "versus_other_alignment": True},
                {"kind": "attack_penalty_of_attackers", "value": -1, "versus_other_alignment": True},
            ],
            # The ward covers the caster plus the supplied allies (the caster stands
            # at the radius' center by definition); the 10' geometry is Phase 4's.
            "params": {"bars_melee_from": ("enchanted", "constructed", "summoned"), "includes_caster": True},
        },
    ),
]


def _resist_modes(element: str, targeting: dict[str, object]) -> list[dict[str, object]]:
    return [
        _mode(
            "resist",
            targeting=targeting,
            effect={
                "kind": "modifiers",
                "modifiers": [
                    {"kind": "save_bonus", "value": 2, "element": element},
                    {"kind": "damage_reduction_per_die", "value": 1, "element": element},
                ],
                "params": {"unharmed_by_nonmagical": element},
            },
        ),
    ]


# Spell id → structured mechanics: the automated census pinned by the Phase 3 plan.
# Everything absent here compiles with a single manual "cast" mode carrying the page
# prose (and a manual reversed mode when a reversed form exists) — a supported cast,
# not a gap. "usage" binds a mode's prose to the page's numbered usage item.
_SPELL_MECHANICS: dict[str, dict[str, list[dict[str, object]]]] = {
    # --- damage and save-or-die -------------------------------------------------
    "magic_missile": {
        "modes": [
            _mode(
                "missiles",
                targeting={"mode": "up_to_n"},
                effect={
                    "kind": "damage",
                    "params": {
                        "dice": "1d6+1",
                        "auto_hit": True,
                        "missiles_base": 1,
                        "missiles_step": 2,
                        "missiles_per_levels": 5,
                    },
                },
            ),
        ],
    },
    "fire_ball": {
        "modes": [
            _mode(
                "damage",
                targeting={"mode": "area", "shape": "sphere", "dimensions": {"radius_feet": 20}},
                save={"category": "spells", "on_save": "half"},
                effect={"kind": "damage", "params": {"dice_per_level": "1d6", "element": "fire"}},
            ),
        ],
    },
    "lightning_bolt": {
        "modes": [
            _mode(
                "damage",
                targeting={"mode": "area", "shape": "line", "dimensions": {"length_feet": 60, "width_feet": 5}},
                save={"category": "spells", "on_save": "half"},
                effect={
                    "kind": "damage",
                    "params": {"dice_per_level": "1d6", "element": "lightning", "destructive": True},
                },
            ),
        ],
    },
    "death_spell": {
        "modes": [
            _mode(
                "kill",
                targeting={
                    "mode": "hd_budget",
                    "hd_budget_dice": "4d8",
                    "hd_cap": 7,
                    "shape": "cube",
                    "dimensions": {"side_feet": 60},
                },
                save={"category": "death"},
                effect={"kind": "kill", "params": {"excludes_undead": True}},
            ),
        ],
    },
    "disintegrate": {
        "modes": [
            _mode(
                "kill",
                targeting={"mode": "single"},
                save={"category": "death"},
                effect={"kind": "kill", "params": {"permanent": True, "destroy_equipment": True}},
            ),
        ],
    },
    # --- sleep --------------------------------------------------------------------
    "sleep": {
        "modes": [
            _mode(
                "single_4_plus",
                targeting={"mode": "single"},
                effect=_cond("asleep", params={"excludes_undead": True, "hd_count": 4, "hd_bonus_required": True}),
                usage=1,
            ),
            _mode(
                "hd_budget",
                targeting={"mode": "hd_budget", "hd_budget_dice": "2d8", "hd_cap": 4},
                effect=_cond("asleep", params={"excludes_undead": True, "excludes_hd_4_plus": True}),
                usage=2,
            ),
        ],
    },
    # --- hold and charm -------------------------------------------------------
    "hold_person_c": {"modes": _HOLD_MODES},
    "hold_person_mu": {"modes": _HOLD_MODES},
    "hold_monster": {
        "modes": [
            _mode(
                "individual",
                targeting={"mode": "single"},
                save={"category": "spells", "modifier": -2},
                effect=_HOLD_EFFECT_ANY,
                usage=1,
            ),
            _mode(
                "group",
                targeting={"mode": "up_to_n", "count_dice": "1d4"},
                save={"category": "spells"},
                effect=_HOLD_EFFECT_ANY,
                usage=2,
            ),
        ],
    },
    "charm_person": {
        "modes": [
            _mode(
                "charm",
                targeting={"mode": "single"},
                save={"category": "spells"},
                effect=_charm_effect(person_gate=True),
            ),
        ],
    },
    "charm_monster": {
        "modes": [
            _mode(
                "individual",
                targeting={"mode": "single", "hd_min": 4},
                save={"category": "spells"},
                effect=_charm_effect(person_gate=False),
            ),
            _mode(
                "group",
                targeting={"mode": "up_to_n", "count_dice": "3d6", "hd_cap": 3},
                save={"category": "spells"},
                effect=_charm_effect(person_gate=False),
            ),
        ],
    },
    # --- cures and restoration ----------------------------------------------------
    "cure_light_wounds": {
        "modes": [
            _mode(
                "heal",
                targeting={"mode": "single"},
                effect={"kind": "heal", "params": {"dice": "1d6+1"}},
                usage=1,
            ),
            _mode(
                "cure_paralysis",
                targeting={"mode": "single"},
                effect={"kind": "cure", "cures_conditions": ["paralysed"]},
                usage=2,
            ),
        ],
        "reversed_modes": [
            _mode(
                "harm",
                targeting={"mode": "single"},
                effect={"kind": "damage", "params": {"dice": "1d6+1", "touch_attack": True}},
            ),
        ],
    },
    "cure_serious_wounds": {
        "modes": [
            _mode("heal", targeting={"mode": "single"}, effect={"kind": "heal", "params": {"dice": "2d6+2"}}),
        ],
        "reversed_modes": [
            _mode(
                "harm",
                targeting={"mode": "single"},
                effect={"kind": "damage", "params": {"dice": "2d6+2", "touch_attack": True}},
            ),
        ],
    },
    "cure_disease": {
        "modes": [
            _mode(
                "cure",
                targeting={"mode": "single"},
                effect={"kind": "cure", "cures_conditions": ["diseased"]},
                usage=1,
            ),
            _mode("kill_green_slime", manual=True, usage=2),
        ],
        "reversed_modes": [
            _mode(
                "afflict",
                targeting={"mode": "single"},
                save={"category": "spells"},
                effect=_cond(
                    "diseased",
                    params={
                        "effect_kind": "cause_disease",
                        "duration_dice": "2d12",
                        "duration_unit": "day",
                        "expiry": "death",
                        "healing_rest_days": 2,
                    },
                    modifiers=[{"kind": "attack_bonus", "value": -2}],
                ),
            ),
        ],
    },
    "neutralize_poison": {
        "modes": [
            _mode(
                "neutralize",
                targeting={"mode": "single"},
                effect={
                    "kind": "cure",
                    "cures_conditions": ["poisoned"],
                    "params": {"revives_poison_dead": True, "revive_window_rounds": 10},
                },
                usage=1,
            ),
            _mode("items", manual=True, usage=2),
        ],
    },
    "raise_dead": {
        "modes": [
            _mode(
                "restore_life",
                targeting={"mode": "single"},
                effect={
                    "kind": "restore_life",
                    "params": {"days_per_level_above": 7, "weakness_days": 14},
                },
                usage=1,
            ),
            _mode(
                "destroy_undead",
                targeting={"mode": "single"},
                save={"category": "spells"},
                effect={"kind": "kill", "params": {"undead_only": True, "permanent": True}},
                usage=2,
            ),
        ],
        "reversed_modes": [
            _mode(
                "kill",
                targeting={"mode": "single"},
                save={"category": "death"},
                effect={"kind": "kill"},
            ),
        ],
    },
    "remove_fear": {
        "modes": [
            _mode(
                "remove",
                targeting={"mode": "single"},
                effect={
                    "kind": "cure",
                    "cures_conditions": ["afraid"],
                    "params": {"magical_fear_save": "spells", "save_bonus_per_level": 1},
                },
            ),
        ],
        "reversed_modes": [
            _mode(
                "frighten",
                targeting={"mode": "single"},
                save={"category": "spells"},
                effect=_cond("afraid"),
            ),
        ],
    },
    "stone_to_flesh": {
        "modes": [
            _mode(
                "restore",
                targeting={"mode": "single"},
                effect={"kind": "cure", "cures_conditions": ["petrified"]},
            ),
        ],
        "reversed_modes": [
            _mode(
                "petrify",
                targeting={"mode": "single"},
                save={"category": "paralysis"},
                effect=_cond("petrified", params={"permanent": True}),
            ),
        ],
    },
    # --- buffs and wards ------------------------------------------------------------
    "bless": {
        "modes": [
            _mode(
                "battle",
                targeting={"mode": "area", "shape": "square", "dimensions": {"side_feet": 20}},
                effect={
                    "kind": "modifiers",
                    "modifiers": [
                        {"kind": "attack_bonus", "value": 1},
                        {"kind": "damage_bonus", "value": 1},
                        {"kind": "morale_bonus", "value": 1},
                    ],
                },
                usage=1,
            ),
            _mode("ritual", manual=True, usage=2),
        ],
        "reversed_modes": [
            _mode(
                "battle",
                targeting={"mode": "area", "shape": "square", "dimensions": {"side_feet": 20}},
                save={"category": "spells"},
                effect={
                    "kind": "modifiers",
                    "modifiers": [
                        {"kind": "attack_bonus", "value": -1},
                        {"kind": "damage_bonus", "value": -1},
                        {"kind": "morale_bonus", "value": -1},
                    ],
                },
            ),
        ],
    },
    "striking": {
        "modes": [
            _mode(
                "enchant",
                targeting={"mode": "single"},
                effect={
                    "kind": "modifiers",
                    "modifiers": [
                        {"kind": "weapon_damage_dice_bonus", "dice": "1d6"},
                        {"kind": "counts_as_magical"},
                    ],
                },
            ),
        ],
    },
    "resist_cold": {
        "modes": _resist_modes("cold", {"mode": "area", "shape": "sphere", "dimensions": {"radius_feet": 30}})
    },
    "resist_fire": {"modes": _resist_modes("fire", {"mode": "single"})},
    "shield": {
        "modes": [
            _mode(
                "shield",
                targeting={"mode": "self"},
                effect={
                    "kind": "modifiers",
                    "modifiers": [
                        {"kind": "ac_set_vs_missile", "value": 2},
                        {"kind": "ac_set", "value": 4},
                    ],
                },
            ),
        ],
    },
    "protection_from_evil_c": {"modes": _PROTECTION_FROM_EVIL_MODES},
    "protection_from_evil_mu": {"modes": _PROTECTION_FROM_EVIL_MODES},
    "protection_from_evil_10_radius_c": {"modes": _PROTECTION_RADIUS_MODES},
    "protection_from_evil_10_radius_mu": {"modes": _PROTECTION_RADIUS_MODES},
    "protection_from_normal_missiles": {
        "modes": [
            _mode(
                "ward",
                targeting={"mode": "single"},
                effect={"kind": "modifiers", "modifiers": [{"kind": "missile_immunity_nonmagical"}]},
            ),
        ],
    },
    # --- light and darkness ---------------------------------------------------------
    "light_c": {"modes": _LIGHT_MODES, "reversed_modes": _DARKNESS_MODES},
    "light_mu": {"modes": _LIGHT_MODES, "reversed_modes": _DARKNESS_MODES},
    "continual_light_c": {"modes": _CONTINUAL_LIGHT_MODES, "reversed_modes": _CONTINUAL_DARKNESS_MODES},
    "continual_light_mu": {"modes": _CONTINUAL_LIGHT_MODES, "reversed_modes": _CONTINUAL_DARKNESS_MODES},
    # --- silence, web, dispel, feeblemind -----------------------------------------
    "silence_15_radius": {
        "modes": [
            _mode(
                "creature",
                targeting={"mode": "single"},
                save={"category": "spells"},
                effect=_cond("silenced", params={"effect_kind": "silence", "radius_feet": 15}),
            ),
            _mode("area", manual=True),
        ],
    },
    "web": {
        "modes": [
            _mode(
                "entangle",
                targeting={"mode": "area", "shape": "cube", "dimensions": {"side_feet": 10}},
                effect=_cond(
                    "entangled",
                    params={
                        "effect_kind": "web",
                        "escape_dice": "2d4",
                        "escape_unit": "turn",
                        "augmented_strength_rounds": 4,
                        "giant_strength_rounds": 2,
                    },
                ),
            ),
        ],
    },
    "dispel_magic": {
        "modes": [
            _mode(
                "dispel",
                targeting={"mode": "area", "shape": "cube", "dimensions": {"side_feet": 20}},
                effect={"kind": "dispel", "params": {"survival_pct_per_level": 5}},
            ),
        ],
    },
    "feeblemind": {
        "modes": [
            _mode(
                "feeblemind",
                targeting={"mode": "single"},
                save={"category": "spells", "modifier": -4},
                effect=_cond("feebleminded", params={"permanent": True, "arcane_caster_only": True}),
            ),
        ],
    },
    # --- attach-only census -----------------------------------------------------
    "haste": {
        "modes": [
            _mode(
                "haste",
                targeting={"mode": "up_to_n", "count": 24, "shape": "sphere", "dimensions": {"radius_feet": 30}},
                effect={
                    "kind": "attach_only",
                    "params": {"effect_kind": "haste", "attacks_multiplier": 2, "movement_multiplier": 2},
                },
            ),
        ],
    },
    "invisibility": {
        "modes": [
            _mode(
                "invisibility",
                targeting={"mode": "single"},
                effect=_cond("invisible", params={"effect_kind": "invisibility", "broken_by_attack_or_cast": True}),
            ),
        ],
    },
    "invisibility_10_radius": {
        "modes": [
            _mode(
                "invisibility",
                targeting={"mode": "area", "shape": "sphere", "dimensions": {"radius_feet": 10}},
                effect=_cond("invisible", params={"effect_kind": "invisibility", "broken_by_attack_or_cast": True}),
            ),
        ],
    },
    "mirror_image": {
        "modes": [
            _mode(
                "images",
                targeting={"mode": "self"},
                effect={"kind": "attach_only", "params": {"effect_kind": "mirror_image", "images_dice": "1d4"}},
            ),
        ],
    },
    "confusion": {
        "modes": [
            _mode(
                "confuse",
                targeting={
                    "mode": "up_to_n",
                    "count_dice": "3d6",
                    "shape": "sphere",
                    "dimensions": {"radius_feet": 30},
                },
                effect=_cond(
                    "confused",
                    params={
                        "behaviour_dice": "2d6",
                        "behaviour_table": ("2-5:attack_caster_group", "6-8:no_action", "9-12:attack_own_group"),
                        # "2+1 HD or greater" re-saves each round: HD count 3+, or
                        # count 2 with a positive fixed modifier (the sleep pin's
                        # count-plus-bonus vocabulary); "2 HD or lower" never saves.
                        "resave_hd_min_count": 3,
                        "resave_at_hd_count_2_with_bonus": True,
                        "resave_category": "spells",
                        "resave_interval": "round",
                    },
                ),
            ),
        ],
    },
    "anti_magic_shell": {
        "modes": [
            _mode(
                "shell",
                targeting={"mode": "self"},
                effect={
                    "kind": "attach_only",
                    "params": {
                        "effect_kind": "anti_magic_shell",
                        "blocks_casting": True,
                        "blocks_spell_effects": True,
                    },
                },
            ),
        ],
    },
    "infravision": {
        "modes": [
            _mode(
                "grant",
                targeting={"mode": "single"},
                effect={"kind": "attach_only", "params": {"effect_kind": "infravision", "range_feet": 60}},
            ),
        ],
    },
    "fly": {
        "modes": [
            _mode(
                "grant",
                targeting={"mode": "single"},
                effect={"kind": "attach_only", "params": {"effect_kind": "fly", "rate_feet": 360}},
            ),
        ],
    },
    "levitate": {
        "modes": [
            _mode(
                "grant",
                targeting={"mode": "self"},
                effect={"kind": "attach_only", "params": {"effect_kind": "levitate", "rate_feet_per_round": 60}},
            ),
        ],
    },
    "water_breathing": {
        "modes": [
            _mode(
                "grant",
                targeting={"mode": "single"},
                effect={"kind": "attach_only", "params": {"effect_kind": "water_breathing"}},
            ),
        ],
    },
}

_CONJURED_MONSTER_PAGES = {"sticks_to_snakes": "Sticks_to_Snakes.md", "conjure_elemental": "Conjure_Elemental.md"}

_ELEMENTAL_IDS = {
    "Air Elemental": "air_elemental_greater",
    "Earth Elemental": "earth_elemental_greater",
    "Fire Elemental": "fire_elemental_greater",
    "Water Elemental": "water_elemental_greater",
}


def _manifest(srd_dir: Path) -> list[tuple[str, str, int, str]]:
    """Return the list-page inventory: `(display name, filename, level, spell_list)`."""
    entries = []
    for list_page in LIST_PAGES:
        spell_list = _LIST_CLASSES[list_page]
        level = None
        for line in (srd_dir / list_page).read_text(encoding="utf-8").splitlines():
            heading = _LIST_HEADING.match(line.strip())
            if heading:
                level = int(heading[1])
                continue
            entry = _LIST_ENTRY.match(line.strip())
            if entry and level is not None:
                filename = quote(entry[2], safe="_.-") + ".md"
                entries.append((entry[1], filename, level, spell_list))
    return entries


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", strip_emphasis(strip_links(text))).strip()


def _parse_duration(raw: str) -> tuple[dict[str, object], dict[str, object] | None, str | None]:
    """Parse a Duration value into `(normal spec, reversed spec, reversed raw)`.

    Slash-separated dual forms (`Instant / Permanent (curse)`) split across the
    normal and reversed forms; the parenthetical names the reversed spell and is
    dropped before parsing.
    """
    parts = [part.strip() for part in strip_emphasis(raw).split(" / ")]
    if len(parts) == 2:
        reversed_raw = parts[1]
        normal = _parse_single_duration(re.sub(r"\s*\([^)]*\)$", "", parts[0]).strip())
        reverse = _parse_single_duration(re.sub(r"\s*\([^)]*\)$", "", parts[1]).strip())
        return normal, reverse, reversed_raw
    return _parse_single_duration(parts[0]), None, None


def _parse_single_duration(text: str) -> dict[str, object]:
    if text.lower() == "instant":
        return {"kind": "instant"}
    if text.startswith("Permanent"):
        return {"kind": "permanent"}
    concentration = _DURATION_CONCENTRATION.fullmatch(text)
    if concentration:
        spec: dict[str, object] = {"kind": "concentration"}
        if concentration[2]:
            spec["concentration_cap_amount"] = int(concentration[2])
            spec["concentration_cap_unit"] = concentration[3]
        return spec
    per_level = _DURATION_PER_LEVEL.fullmatch(text)
    if per_level:
        return {"kind": "fixed", "unit": per_level[2], "amount": 0, "per_level": int(per_level[1])}
    fixed = _DURATION_FIXED.fullmatch(text)
    if fixed:
        spec = {"kind": "fixed", "unit": fixed[3]}
        if fixed[1]:
            spec["amount"] = int(fixed[1])
        else:
            spec["dice"] = fixed[2]
        if fixed[4]:
            spec["per_level"] = int(fixed[5])
        return spec
    return {"kind": "special"}


def _parse_range(raw: str) -> dict[str, object]:
    text = strip_emphasis(raw).strip()
    if text == "The caster":
        return {"kind": "caster"}
    if text in _TOUCH_RANGES:
        return {"kind": "touch"}
    feet = _RANGE_FEET.fullmatch(text)
    if feet:
        return {"kind": "feet", "feet": int(feet[1])}
    yards = _RANGE_YARDS.fullmatch(text)
    if yards:
        return {"kind": "yards", "feet": int(yards[1]) * 3}
    per_level = _RANGE_PER_LEVEL.fullmatch(text)
    if per_level:
        spec: dict[str, object] = {"kind": "per_level", "per_level_feet": int(per_level[2])}
        if per_level[1]:
            spec["feet"] = int(per_level[1])
        return spec
    return {"kind": "special"}


def _parse_stat_line(line: str) -> dict[str, str]:
    """Split an inline stat line (`**AC** 6 [13], **HD** 1 (4hp), …`) into cells."""
    parts = _STAT_LINE_KEY.split(line)
    cells: dict[str, str] = {}
    for index in range(1, len(parts), 2):
        cells[parts[index]] = strip_links(parts[index + 1]).strip().strip(",").strip()
    expected = {"AC", "HD", "Att", "THAC0", "MV", "SV", "ML", "AL", "XP"}
    if set(cells) != expected:
        raise ValueError(f"stat line is missing keys {expected - set(cells)}: {line!r}")
    return cells


def _stat_line_template(name: str, page: str, intro: str, cells: dict[str, str]) -> dict[str, object]:
    """Build a monster-template dict from an inline stat line.

    Number appearing and treasure are not printed on spell pages: number appearing
    compiles as `see below` (the spell's own dice govern it) and treasure as none.
    """
    xp_match = re.match(r"(\d+)", cells["XP"])
    if xp_match is None:
        raise ValueError(f"unparseable stat-line XP {cells['XP']!r}")
    thac0_match = re.fullmatch(r"(\d+) \[([+-]?\d+)\]", cells["THAC0"])
    if thac0_match is None:
        raise ValueError(f"unparseable stat-line THAC0 {cells['THAC0']!r}")
    return {
        "id": slugify(name),
        "name": name,
        "page": page,
        "intro": intro,
        **monsters._parse_ac(cells["AC"]),
        "hit_dice": monsters._parse_hd(cells["HD"]),
        "attacks": monsters._parse_attacks(cells["Att"]),
        "thac0": int(thac0_match[1]),
        "attack_bonus": int(thac0_match[2]),
        "movement": monsters._parse_movement(cells["MV"]),
        "saves": monsters._parse_saves(cells["SV"]),
        "morale": int(cells["ML"]),
        "morale_alternates": [],
        "alignment": monsters._parse_alignment(cells["AL"]),
        "xp": int(xp_match[1]),
        "xp_notes": [],
        "number_appearing": {
            "dungeon": {"dice": None, "fixed": None, "see_below": True},
            "lair": {"dice": None, "fixed": None, "see_below": True},
        },
        "treasure": {
            "letters": [],
            "parenthetical": [],
            "extra_gp": 0,
            "multiplier": 1,
            "special": [],
            "see_below": False,
        },
        "abilities": [],
        "defenses": {"harmed_only_by": [], "reductions": [], "energy": {}, "condition_immunities": []},
        "categories": [],
    }


class _ParsedPage:
    """One spell page's parsed pieces, before mechanics are merged in."""

    def __init__(self, filename: str, text: str) -> None:
        self.filename = filename
        lines = text.splitlines()
        title = strip_links(lines[0].lstrip("# ").strip())
        marker_match = _CLASS_MARKER.search(title)
        self.class_marker = marker_match[1].lower() if marker_match else None
        if marker_match:
            title = title[: marker_match.start()].strip()
        level_match = next((m for line in lines for m in [_LEVEL_LINE.search(line)] if m), None)
        if level_match is None:
            raise ValueError(f"{filename} has no spell level line")
        self.level = int(level_match[1])
        self.spell_list = _LEVEL_CLASSES[level_match[2]]
        self.duration_raw = self._first_label(lines, "Duration")
        self.range_raw = self._first_label(lines, "Range")

        reversed_index = next((index for index, line in enumerate(lines) if _REVERSED_HEADING.match(line)), None)
        self.reversed_name: str | None = None
        reversed_lines: list[str] = []
        if reversed_index is not None:
            self.reversed_name = _clean(_REVERSED_HEADING.match(lines[reversed_index])[1])
            reversed_lines = lines[reversed_index + 1 :]
            lines = lines[:reversed_index]
        # The title's reversed parenthetical (abbreviated forms like "Cause Lt.
        # Wounds") is dropped from the name — the reversed section heading carries
        # the full reversed name.
        if self.reversed_name is not None:
            title = re.sub(r"\s*\([^)]*\)$", "", title).strip()
        self.name = title
        self.base_id = slugify(title)

        range_index = next(index for index, line in enumerate(lines) if line.startswith("**Range:**"))
        self.usages, self.body, self.intro, self.stat_lines = self._parse_body(lines[range_index + 1 :])
        self.reversed_usages, self.reversed_body, _, _ = self._parse_body(reversed_lines)

    @staticmethod
    def _first_label(lines: list[str], label: str) -> str:
        for line in lines:
            if line.startswith(f"**{label}:**"):
                return _clean(line.split(":**", 1)[1])
        raise ValueError(f"no {label} label found")

    @staticmethod
    def _parse_body(lines: list[str]) -> tuple[dict[int, str], str, str, list[tuple[str | None, str]]]:
        """Split body lines into numbered usages, joined prose, the intro, and stat lines."""
        usages: dict[int, str] = {}
        prose: list[str] = []
        stat_lines: list[tuple[str | None, str]] = []
        heading: str | None = None
        in_contents = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                heading_text = stripped.lstrip("# ").strip()
                in_contents = heading_text == "Contents"
                if not in_contents:
                    heading = heading_text
                continue
            if in_contents or not stripped or stripped.startswith("|") or stripped == "---":
                continue
            if stripped.startswith("**AC**"):
                stat_lines.append((heading, stripped))
                continue
            numbered = _NUMBERED_ITEM.match(stripped)
            if numbered:
                usages[int(numbered[1])] = _clean(numbered[2])
            cleaned = _clean(re.sub(r"^- ", "", stripped))
            if cleaned:
                prose.append(cleaned)
        body = "\n".join(prose)
        intro = prose[0] if prose else ""
        return usages, body, intro, stat_lines


def _build_mode(raw: dict[str, object], usages: dict[int, str], body: str) -> dict[str, object]:
    usage = raw.get("usage")
    prose = usages.get(usage, body) if isinstance(usage, int) else body
    mode: dict[str, object] = {
        "key": raw["key"],
        "targeting": raw["targeting"],
        "save": raw["save"],
        "effect": raw["effect"],
        "manual": raw["manual"],
        "prose": prose,
    }
    if isinstance(usage, int) and usage not in usages:
        raise ValueError(f"mode {raw['key']!r} binds usage {usage}, but the page has usages {sorted(usages)}")
    return mode


def _default_mode(body: str) -> dict[str, object]:
    return {"key": "cast", "targeting": None, "save": None, "effect": None, "manual": True, "prose": body}


def compile_spells(srd_dir: Path, monster_entries: list[dict[str, object]]) -> dict[str, object]:
    """Compile the spell pages into the `spells.json` structure (sans `_meta`).

    Args:
        srd_dir: The directory holding the scraped SRD markdown.
        monster_entries: The compiled `monsters.json` entries, for validating
            *conjure elemental*'s references.

    Returns:
        The raw dict ready for `SpellCatalog` validation, entries sorted by id.
    """
    entries = _manifest(srd_dir)
    if len(entries) != 106:
        raise ValueError(f"expected 106 spell list entries, found {len(entries)}")
    counts = {"cleric": 0, "magic_user": 0}
    for _, _, _, spell_list in entries:
        counts[spell_list] += 1
    if counts != {"cleric": 34, "magic_user": 72}:
        raise ValueError(f"unexpected list census: {counts}")

    pages: list[_ParsedPage] = []
    for display_name, filename, level, spell_list in entries:
        page = _ParsedPage(filename, (srd_dir / filename).read_text(encoding="utf-8"))
        if page.level != level or page.spell_list != spell_list:
            raise ValueError(
                f"{filename}: level line says {page.spell_list} {page.level}, "
                f"list says {spell_list} {level} ({display_name!r})"
            )
        pages.append(page)

    reversed_count = sum(1 for page in pages if page.reversed_name is not None)
    parenthesized = sum(1 for display_name, _, _, _ in entries if display_name.endswith(")"))
    if reversed_count != 16 or parenthesized != 16:
        raise ValueError(f"expected 16 reversible spells, found {reversed_count} sections / {parenthesized} entries")

    # Dual-page suffixing: base ids appearing on both lists take _c/_mu.
    by_base: dict[str, list[_ParsedPage]] = {}
    for page in pages:
        by_base.setdefault(page.base_id, []).append(page)
    duals = {base: group for base, group in by_base.items() if len(group) == 2}
    if len(duals) != 9:
        raise ValueError(f"expected 9 dual-page concepts, found {len(duals)}: {sorted(duals)}")
    ids: dict[str, str] = {}
    for page in pages:
        if page.base_id in duals:
            if page.class_marker not in ("c", "mu"):
                raise ValueError(f"{page.filename} is one of a dual pair but carries no (C)/(MU) marker")
            ids[page.filename] = f"{page.base_id}_{page.class_marker}"
        else:
            if page.class_marker is not None and page.base_id not in _DUAL_EXEMPT:
                raise ValueError(f"{page.filename} carries a class marker but has no twin")
            ids[page.filename] = page.base_id

    known_ids = set(ids.values())
    unknown_mechanics = set(_SPELL_MECHANICS) - known_ids
    if unknown_mechanics:
        raise ValueError(f"_SPELL_MECHANICS ids do not resolve to parsed pages: {sorted(unknown_mechanics)}")

    monster_ids = {entry["id"]: entry for entry in monster_entries}
    spells = []
    for page in pages:
        spell_id = ids[page.filename]
        duration_spec, reversed_duration_spec, reversed_duration_raw = _parse_duration(page.duration_raw)
        mechanics = _SPELL_MECHANICS.get(spell_id, {})
        raw_modes = mechanics.get("modes")
        modes = (
            [_build_mode(raw, page.usages, page.body) for raw in raw_modes] if raw_modes else [_default_mode(page.body)]
        )
        entry: dict[str, object] = {
            "id": spell_id,
            "name": page.name,
            "spell_list": page.spell_list,
            "level": page.level,
            "duration": page.duration_raw,
            "duration_spec": duration_spec,
            "range": page.range_raw,
            "range_spec": _parse_range(page.range_raw),
            "modes": modes,
            "intro": page.intro,
            "conjured_monsters": [],
            "conjured_monster_ids": [],
        }
        if page.reversed_name is not None:
            raw_reversed = mechanics.get("reversed_modes")
            reversed_modes = (
                [_build_mode(raw, page.reversed_usages, page.reversed_body) for raw in raw_reversed]
                if raw_reversed
                else [_default_mode(page.reversed_body)]
            )
            entry["reversed_form"] = {
                "name": page.reversed_name,
                "prose": page.reversed_body,
                "modes": reversed_modes,
                "duration": reversed_duration_raw,
                "duration_spec": reversed_duration_spec,
            }
        else:
            entry["reversed_form"] = None
            if reversed_duration_spec is not None:
                raise ValueError(f"{spell_id} has a dual-form duration but no reversed section")
        if spell_id == "sticks_to_snakes":
            if len(page.stat_lines) != 1:
                raise ValueError(f"expected one snake stat line, found {len(page.stat_lines)}")
            cells = _parse_stat_line(page.stat_lines[0][1])
            entry["conjured_monsters"] = [
                _stat_line_template(
                    "Conjured Snake", page.filename, "Sticks miraculously transformed into snakes.", cells
                )
            ]
        if spell_id == "conjure_elemental":
            if len(page.stat_lines) != 4:
                raise ValueError(f"expected four elemental stat lines, found {len(page.stat_lines)}")
            references = []
            for heading, line in page.stat_lines:
                if heading not in _ELEMENTAL_IDS:
                    raise ValueError(f"unexpected elemental heading {heading!r}")
                monster_id = _ELEMENTAL_IDS[heading]
                target = monster_ids.get(monster_id)
                if target is None:
                    raise ValueError(f"conjure elemental references unknown monster {monster_id!r}")
                cells = _parse_stat_line(line)
                page_hd = monsters._parse_hd(cells["HD"])
                if target["hit_dice"]["count"] != 16 or page_hd["count"] != 16:
                    raise ValueError(f"{monster_id} should be the 16 HD greater elemental")
                references.append(monster_id)
            entry["conjured_monster_ids"] = references
        spells.append(entry)
    spells.sort(key=lambda entry: entry["id"])
    return {"spells": spells}


def source_pages(srd_dir: Path) -> tuple[str, ...]:
    """Return every SRD page the spell compiler reads."""
    return (*(filename for _, filename, _, _ in _manifest(srd_dir)), *LIST_PAGES)
