# Authoring custom classes, spells, monsters, and items

A build pipeline compiles the OSE SRD into the package's data files: the classes, the spell list, the monster catalog, and the equipment lists that come with osrlib. You can't add your own content to that pipeline. Instead, you write your own class, spell, monster, and item definitions in code, validate them with the same checks the built-in catalogs pass, and run them through the same kernel that runs a fighter or a goblin: creation, advancement, memorization, casting, and (for monsters) creation, combat, XP, and treasure. A class's `race` and a spell's `spell_list` are both open, validated string ids for this reason. Nothing in the kernel restricts them to the values the built-in classes use.

Start with one small custom class and one custom spell for it. The [complete program](#the-complete-program) runs every step shown along the way. After that come [bundling custom monsters with an adventure](#bundling-custom-monsters-with-an-adventure) and [bundling custom items with an adventure](#bundling-custom-items-with-an-adventure), both for the crawl layer. The scope here is the content catalogs, the *things* a game and its adventures can contain. Authored *behavior* is a separate topic: the gated door, the trigger, and the quest are covered in [Gates, triggers, and quests](gates-triggers-quests.md).

## The structure of a class definition

A [`ClassDefinition`][osrlib.core.classes.ClassDefinition] is a frozen model you build with `model_validate`. There's no separate builder API, just the fields the built-in classes use. `requirements` are the minimum ability scores checked at class choice. `prime_requisites` lists the abilities used in the ability-score adjustment step (a prime requisite can never be lowered there). They're usually the abilities your `xp_tiers` depend on, though [`xp_modifier_pct`][osrlib.core.classes.xp_modifier_pct] evaluates the tiers without reference to that list. `xp_tiers` are ordered best-first: the first tier whose minimums all hold sets the class's XP-modifier percentage, and a score set matching no tier gets zero, never a penalty. That's why the multi-prime-requisite classes have no penalty rows. `hit_die` is the class's base die size:

```{.python .no-run}
WARDEN = ClassDefinition.model_validate(
    {
        "id": "warden",
        "name": "Warden",
        "race": "human",
        "requirements": {"wis": 9, "con": 9},
        "prime_requisites": ("wis", "con"),
        "xp_tiers": (
            {"modifier_pct": 10, "minimums": {"wis": 16, "con": 13}},
            {"modifier_pct": 5, "minimums": {"wis": 13, "con": 13}},
        ),
        "hit_die": 6,
        "max_level": 3,
        "armour": {"kind": "leather_only", "shields_allowed": True},
        "weapons": {"kind": "allowed", "weapon_ids": ("mace", "sling", "staff")},
        "languages": ("common",),
        "may_not_lower": ("wis",),
```

`armour` and `weapons` are structured policies, not prose. An [`ArmourPolicy`][osrlib.core.classes.ArmourPolicy] specifies the allowed armour kind (`any`, `leather_only`, or `none`) and whether shields are allowed. A [`WeaponPolicy`][osrlib.core.classes.WeaponPolicy] is either `any` with no id list, or `allowed`/`forbidden` with an explicit `weapon_ids` list. Its `manual_notes` field contains rules text about stature that the referee judges and the engine can't automate, as the dwarf and halfling pages state it. `languages` are the tongues every member of the class speaks natively. `may_not_lower` adds class-specific floors to the adjustment step on top of the prime-requisite rule: the warden above protects its casting stat the way the built-in thief class protects STR.

## Caster tags and the progression table

`abilities` is a tuple of [`ClassAbility`][osrlib.core.classes.ClassAbility] tags: a `tag` string, a display `name`, referee-facing `prose`, and a `params` dict of the numbers the engine uses. The built-in procedures read some of those tags by name. [`detection_chance`][osrlib.core.classes.detection_chance] reads `listening_at_doors`, `detect_secret_doors`, `detect_room_traps`, and `detect_construction_tricks`, all of which take a `chance_in_six` param. [`caster_profile`][osrlib.core.spells.caster_profile] reads `divine_magic` and `arcane_magic`, which take a `spell_list` param. The engine ignores an unrecognized tag, and your front end can still display it.

A class with a `divine_magic` or `arcane_magic` tag is a caster. `caster_profile` reads the tag straight off the definition and returns a [`CasterProfile`][osrlib.core.spells.CasterProfile] with its `kind` and its `spell_list`, the id your spells match against. Divine casters choose the reversed form at cast time, and arcane casters fix it when memorizing, from a spell book:

```{.python .no-run}
        "abilities": (
            {
                "tag": "divine_magic",
                "name": "Divine Magic",
                "prose": "Wardens pray for their spells from 1st level.",
                "params": {"spell_list": "warden"},
            },
        ),
        "level_titles": ("Watcher", "Keeper", "Warden"),
        "progression": (
            {
                "level": 1,
                "xp": 0,
                "hit_dice": {"count": 1, "die": 6},
                "thac0": 19,
                "attack_bonus": 0,
                "saves": {"death": 10, "wands": 11, "paralysis": 13, "breath": 15, "spells": 14},
                "spell_slots": (1,),
            },
```

`level_titles[i]` is the title at level `i + 1`, and the tuple may run shorter than `progression` because the SRD's title lists stop at name level. `progression` is one [`ProgressionRow`][osrlib.core.classes.ProgressionRow] per level, and it's the *only* place saves, THAC0, attack bonus, and spell slots live. [`ClassDefinition.row`][osrlib.core.classes.ClassDefinition.row] looks a level up fresh every time, so leveling and energy drain change which row applies to a character rather than updating a stored value. `hit_dice` on a row is a [`HitDice`][osrlib.core.classes.HitDice]: `count`, `die`, a flat `bonus` for the rows above name level, and `con_applies` for the SRD's asterisked "CON no longer applies" rows. `saves` is a [`SavingThrows`][osrlib.core.classes.SavingThrows] with the five save categories. `spell_slots[i]` is how many level-`i + 1` spells the row's caster can memorize, and it's empty for non-casters.

## The structure of a spell

A [`SpellTemplate`][osrlib.core.spells.SpellTemplate] pairs presentation strings (`duration`, `range`) with the parsed, structured forms the kernel resolves (`duration_spec`, a [`DurationSpec`][osrlib.core.spells.DurationSpec], and `range_spec`, a [`RangeSpec`][osrlib.core.spells.RangeSpec]). `spell_list` is the same kind of open, validated string id as a class's `race`. The kernel's only use of it is matching it against a caster's `CasterProfile.spell_list`. `modes` is a tuple of [`SpellMode`][osrlib.core.spells.SpellMode]: a stable `key` you cast by, a `targeting` spec, an optional `save`, and either an `effect` (one of the kernel's automated effect kinds, like `heal` or `damage`, plus its dice and parameters) or `manual=True` with SRD-style `prose` for a mode the kernel doesn't automate. Casting a manual mode still spends the memorized copy and emits the cast event, and your game narrates the rest.

A reversible spell has a [`ReversedForm`][osrlib.core.spells.ReversedForm] with a separate name and modes, which a divine caster can choose freely at cast time and an arcane caster must fix at memorization. The example below doesn't use a reversed form, but a custom spell can add one the same way `cure_light_wounds` does:

```{.python .no-run}
MEND_WOUNDS = SpellTemplate.model_validate(
    {
        "id": "mend_wounds",
        "name": "Mend Wounds",
        "spell_list": "warden",
        "level": 1,
        "duration": "Instant",
        "duration_spec": {"kind": "instant"},
        "range": "The caster or a creature touched",
        "range_spec": {"kind": "touch"},
        "modes": (
            {
                "key": "mend",
                "targeting": {"mode": "single"},
                "effect": {"kind": "heal", "params": {"dice": "1d6+1"}},
                "prose": "Restores 1d6+1 hit points of damage.",
            },
        ),
    }
)
```

## Validate the way the built-in catalogs validate

[`ClassCatalog`][osrlib.core.classes.ClassCatalog] and [`SpellCatalog`][osrlib.core.spells.SpellCatalog] are the same models [`load_classes`][osrlib.data.load_classes] and [`load_spells`][osrlib.data.load_spells] validate their generated JSON into. Build one from your own definitions, and it's validated with the same checks the built-in data has to pass: unique ids, and every per-definition rule above. A round trip through JSON shows this, because it follows the same path the loaders do (a dict goes in and a model comes out):

```{.python .no-run}
classes = ClassCatalog(classes=(*load_classes().classes, WARDEN))
reloaded = ClassCatalog.model_validate(json.loads(json.dumps(classes.model_dump(mode="json"))))
assert reloaded == classes

spells = SpellCatalog(spells=(*load_spells().spells, MEND_WOUNDS))
assert [spell.id for spell in spells.by_list("warden")] == ["mend_wounds"]

low_scores = {ability: 11 for ability in AbilityScore} | {AbilityScore.WIS: 8}
rejections = validate_class_choice(low_scores, WARDEN)
assert [rejection.code for rejection in rejections] == ["creation.class.requirements_not_met"]
```

`classes` and `spells` here are *your* catalogs, extending a copy of the built-in ones. osrlib doesn't write them back into `load_classes()` or `load_spells()`, which stay cached, frozen, and SRD-only. [`validate_class_choice`][osrlib.core.character.validate_class_choice] above takes the `WARDEN` definition directly, the same way it takes any built-in one. Most of this page follows that pattern: a kernel function accepts the `ClassDefinition` (or `SpellCatalog`) you hand it, custom or built-in, with no registration step.

## Characters of a custom class

[`level_up`][osrlib.core.classes.level_up], [`memorize_spells`][osrlib.core.spells.memorize_spells], [`cast_spell`][osrlib.core.spells.cast_spell], and the `validate_class_choice` call above all take `WARDEN` (or a [`CasterProfile`][osrlib.core.spells.CasterProfile] built from it) as a plain argument. None of them needs the definition to be in the built-in catalog.

The one place an id alone has to resolve to a definition is [`Character`][osrlib.core.character.Character] itself. [`Character.definition`][osrlib.core.character.Character.definition] looks its `class_id` up through `load_classes()`, and the validation in `Character` calls `.definition` on every construction, every field assignment (the model validates on assignment), and every document load. `osrlib.core.character` imports `load_classes` by name, and that name is what `.definition` calls, so reassigning the module attribute to a loader that returns your extended catalog is the only way to construct (or revalidate, or load) a character of a custom class:

```{.python .no-run}
character_module.load_classes = lambda: classes

scores = {ability: 11 for ability in AbilityScore} | {AbilityScore.WIS: 13, AbilityScore.CON: 13}
warden = Character(
    id="pc-warden",
    name="Halda",
    class_id="warden",
    race="human",
    level=1,
    xp=0,
    scores=scores,
    alignment=Alignment.LAWFUL,
    max_hp=6,
    current_hp=6,
)
assert warden.thac0 == 19
assert warden.saves.spells == 14
```

If you want characters of a custom class, do the reassignment once, at startup, before you build or load any character, rather than once per character. `race` needs no such setup. Both `ClassDefinition` and `Character` validate it only against a slug pattern, and no procedure looks it up, so any race string that the class definition and the character agree on already works.

## Advancing and casting

With the catalogs extended and the loader pointed at them, the rest of the lifecycle is the same kernel calls a built-in class goes through. `level_up` reads next level's row straight off `WARDEN`. `memorize_spells` checks the caster's list and slot capacity against the extended spell catalog. Casting consumes the memorized copy and resolves the mode's effect, which here heals a wounded ally by touch:

```{.python .no-run}
streams = RngStreams(master_seed=2026)
level_up(warden, WARDEN, streams.get("advancement"))
assert warden.level == 2
assert WARDEN.row(warden.level).spell_slots == (2,)

memorized = memorize_spells(warden, WARDEN, spells, (MemorizedSpell(spell_id="mend_wounds"),))
assert memorized.accepted
```

`cast_spell` needs the same objects as any spell cast outside a session: an [`EffectsLedger`][osrlib.core.effects.EffectsLedger] for attached durations, a [`GameClock`][osrlib.core.clock.GameClock], an id allocator, and a registry of live combatants by id. None of it differs for a custom spell:

```{.python .no-run}
cast_result = cast_spell(
    warden,
    spells.get("mend_wounds"),
    "mend",
    profile=caster_profile(WARDEN),
    targets=[wounded],
    ledger=EffectsLedger(),
    clock=GameClock(),
    allocator=IdAllocator(),
    registry={wounded.id: wounded},
    ruleset=Ruleset(),
    stream=streams.get(MAGIC_STREAM),
    effects_stream=streams.get("effects"),
)
assert wounded.current_hp > 2
```

## The complete program

```python
import json

from osrlib.core import character as character_module
from osrlib.core.abilities import AbilityScore
from osrlib.core.alignment import Alignment
from osrlib.core.character import Character, validate_class_choice
from osrlib.core.classes import ClassCatalog, ClassDefinition, level_up
from osrlib.core.clock import GameClock
from osrlib.core.effects import EffectsLedger
from osrlib.core.monsters import IdAllocator
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.spells import (
    MAGIC_STREAM,
    MemorizedSpell,
    SpellCatalog,
    SpellTemplate,
    cast_spell,
    caster_profile,
    memorize_spells,
)
from osrlib.data import load_classes, load_spells

# A human divine half-caster: spell slots from 1st level, its own save table.
WARDEN = ClassDefinition.model_validate(
    {
        "id": "warden",
        "name": "Warden",
        "race": "human",
        "requirements": {"wis": 9, "con": 9},
        "prime_requisites": ("wis", "con"),
        "xp_tiers": (
            {"modifier_pct": 10, "minimums": {"wis": 16, "con": 13}},
            {"modifier_pct": 5, "minimums": {"wis": 13, "con": 13}},
        ),
        "hit_die": 6,
        "max_level": 3,
        "armour": {"kind": "leather_only", "shields_allowed": True},
        "weapons": {"kind": "allowed", "weapon_ids": ("mace", "sling", "staff")},
        "languages": ("common",),
        "may_not_lower": ("wis",),
        "abilities": (
            {
                "tag": "divine_magic",
                "name": "Divine Magic",
                "prose": "Wardens pray for their spells from 1st level.",
                "params": {"spell_list": "warden"},
            },
        ),
        "level_titles": ("Watcher", "Keeper", "Warden"),
        "progression": (
            {
                "level": 1,
                "xp": 0,
                "hit_dice": {"count": 1, "die": 6},
                "thac0": 19,
                "attack_bonus": 0,
                "saves": {"death": 10, "wands": 11, "paralysis": 13, "breath": 15, "spells": 14},
                "spell_slots": (1,),
            },
            {
                "level": 2,
                "xp": 2000,
                "hit_dice": {"count": 2, "die": 6},
                "thac0": 19,
                "attack_bonus": 0,
                "saves": {"death": 10, "wands": 11, "paralysis": 13, "breath": 15, "spells": 14},
                "spell_slots": (2,),
            },
            {
                "level": 3,
                "xp": 4000,
                "hit_dice": {"count": 3, "die": 6},
                "thac0": 17,
                "attack_bonus": 2,
                "saves": {"death": 8, "wands": 9, "paralysis": 11, "breath": 13, "spells": 12},
                "spell_slots": (2, 1),
            },
        ),
    }
)

# A first-level spell on the warden's own list.
MEND_WOUNDS = SpellTemplate.model_validate(
    {
        "id": "mend_wounds",
        "name": "Mend Wounds",
        "spell_list": "warden",
        "level": 1,
        "duration": "Instant",
        "duration_spec": {"kind": "instant"},
        "range": "The caster or a creature touched",
        "range_spec": {"kind": "touch"},
        "modes": (
            {
                "key": "mend",
                "targeting": {"mode": "single"},
                "effect": {"kind": "heal", "params": {"dice": "1d6+1"}},
                "prose": "Restores 1d6+1 hit points of damage.",
            },
        ),
    }
)

# Extend the shipped catalogs and validate exactly the way the loaders validate:
# a round trip through JSON into the same catalog models.
classes = ClassCatalog(classes=(*load_classes().classes, WARDEN))
reloaded = ClassCatalog.model_validate(json.loads(json.dumps(classes.model_dump(mode="json"))))
assert reloaded == classes

spells = SpellCatalog(spells=(*load_spells().spells, MEND_WOUNDS))
assert [spell.id for spell in spells.by_list("warden")] == ["mend_wounds"]

# Ability scores below the warden's requirements are rejected before anything else runs.
low_scores = {ability: 11 for ability in AbilityScore} | {AbilityScore.WIS: 8}
rejections = validate_class_choice(low_scores, WARDEN)
assert [rejection.code for rejection in rejections] == ["creation.class.requirements_not_met"]

# The one seam: Character.definition resolves load_classes() from this module's
# namespace, so a game holding custom definitions reassigns it once, up front.
character_module.load_classes = lambda: classes

scores = {ability: 11 for ability in AbilityScore} | {AbilityScore.WIS: 13, AbilityScore.CON: 13}
warden = Character(
    id="pc-warden",
    name="Halda",
    class_id="warden",
    race="human",
    level=1,
    xp=0,
    scores=scores,
    alignment=Alignment.LAWFUL,
    max_hp=6,
    current_hp=6,
)
assert warden.thac0 == 19
assert warden.saves.spells == 14

streams = RngStreams(master_seed=2026)
level_up(warden, WARDEN, streams.get("advancement"))
assert warden.level == 2
assert WARDEN.row(warden.level).spell_slots == (2,)

memorized = memorize_spells(warden, WARDEN, spells, (MemorizedSpell(spell_id="mend_wounds"),))
assert memorized.accepted

wounded = Character(
    id="pc-wounded",
    name="Tam",
    class_id="fighter",
    race="human",
    level=1,
    xp=0,
    scores={ability: 11 for ability in AbilityScore},
    alignment=Alignment.LAWFUL,
    max_hp=8,
    current_hp=2,
)

cast_result = cast_spell(
    warden,
    spells.get("mend_wounds"),
    "mend",
    profile=caster_profile(WARDEN),
    targets=[wounded],
    ledger=EffectsLedger(),
    clock=GameClock(),
    allocator=IdAllocator(),
    registry={wounded.id: wounded},
    ruleset=Ruleset(),
    stream=streams.get(MAGIC_STREAM),
    effects_stream=streams.get("effects"),
)
assert wounded.current_hp > 2
assert cast_result.affected_ids == (wounded.id,)
assert warden.memorized_spells == ()
```

## Bundling custom monsters with an adventure

You add monsters a different way than classes and spells, because the crawl layer already has a document that contains content: the adventure. `Adventure.monsters` contains your own [`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate]s, so the templates are part of the adventure document. Every session running that adventure resolves them everywhere it resolves a built-in template id: keyed encounters, [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters], inline wandering tables, listen checks, and [`GameSession.spawn`][osrlib.crawl.session.GameSession.spawn]. You don't reassign a loader or register anything. The document contains the content, and [`GameSession.effective_monsters`][osrlib.crawl.session.GameSession.effective_monsters] is the built-in catalog plus the bundle. After creation, nothing is different for a bundled monster. A created [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] embeds its full template, so combat, morale, XP, treasure, saves, and replay never look the id up again.

A template is a frozen model you build with `model_validate`, like the class and spell above. Three table helpers derive the stat-block numbers the SRD would print, so your monster matches the attack matrix, the monster save bands, and the XP awards table: [`thac0_for_hd`][osrlib.core.tables.thac0_for_hd], [`monster_save_band_label`][osrlib.core.tables.monster_save_band_label], and [`monster_xp`][osrlib.core.tables.monster_xp]. The one rule is the collision rule: a bundled id must not collide with the built-in catalog or another bundled id. [`validate_adventure`][osrlib.crawl.adventure.validate_adventure] rejects a collision and never overrides a built-in template, so give a variant orc a different id. [The monster id index][monsters-index] documents the built-in catalog only. Bundled ids live in the adventure that includes them:

```python
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.monsters import MonsterHitDice, MonsterTemplate
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.tables import monster_save_band_label, monster_xp, thac0_for_hd
from osrlib.crawl.adventure import Adventure, TownSpec, validate_adventure
from osrlib.crawl.dungeon import AreaSpec, DungeonSpec, KeyedEncounter, KeyedMonster, LevelSpec
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.data import load_combat_tables, load_equipment, load_monsters

# 2+1 HD with one special ability: the helpers derive THAC0 (the +1 attacks one
# HD higher), the save band, and the XP award from the printed tables.
hd = MonsterHitDice(count=2, modifier=1, asterisks=1)
thac0, attack_bonus = thac0_for_hd(hd.count, bonus_modifier=hd.modifier > 0)

BONE_WARDEN = MonsterTemplate.model_validate(
    {
        "id": "bone_warden",
        "name": "Bone Warden",
        "page": "Custom",
        "ac": 5,
        "ac_ascending": 14,
        "hit_dice": hd.model_dump(),
        "attacks": ({"attacks": ({"name": "halberd", "damage": "1d10"},)},),
        "thac0": thac0,
        "attack_bonus": attack_bonus,
        "movement": ({"rate_feet": 60, "encounter_rate_feet": 20},),
        "saves": {
            "values": {"death": 12, "wands": 13, "paralysis": 14, "breath": 15, "spells": 16},
            "save_as": monster_save_band_label(hd),
        },
        "morale": 12,
        "alignment": {"options": ("chaotic",)},
        "xp": monster_xp(load_combat_tables(), hd),
        "number_appearing": {"dungeon": {"dice": "1d4"}, "lair": {"fixed": 1}},
        "categories": ("undead",),
    }
)

# Bundle it: the adventure document carries the template, and a keyed area
# references it like any built-in id.
level = LevelSpec(
    number=1,
    width=2,
    height=1,
    entrance=(0, 0),
    areas=(
        AreaSpec(
            id="ossuary",
            name="The ossuary",
            cells=((1, 0),),
            encounter=KeyedEncounter(monsters=(KeyedMonster(template_id="bone_warden", count_fixed=1),)),
        ),
    ),
)
adventure = Adventure(
    name="The Bone Warden's Vigil",
    town=TownSpec(name="Threshold"),
    dungeons=(DungeonSpec(id="crypt", name="The Crypt", levels=(level,)),),
    monsters=(BONE_WARDEN,),
)

# The same gate the built-in content passes — the base catalog goes in unchanged,
# and validation unions it with the bundle internally.
validate_adventure(adventure, load_monsters(), load_equipment())

rng = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
hero = create_character(name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=Ruleset(), stream=rng)
session = GameSession.new(Party(members=[hero.character]), adventure, seed=7)

# The session's effective catalog resolves the bundled id — the very object the
# adventure carries — and creation embeds it in each instance.
assert session.effective_monsters.get("bone_warden") is BONE_WARDEN
guards = session.spawn("bone_warden", 2)
assert [guard.template.id for guard in guards] == ["bone_warden", "bone_warden"]
assert all(guard.max_hp >= 3 for guard in guards)  # 2d8+1 rolls at least 3
```

An adventure document can't bundle classes or spells. For those, extend the catalogs as shown above.

## Bundling custom items with an adventure

You bundle items with an adventure the same way as monsters. `Adventure.items` contains your own [`WeaponTemplate`][osrlib.core.items.WeaponTemplate], [`ArmourTemplate`][osrlib.core.items.ArmourTemplate], [`GearTemplate`][osrlib.core.items.GearTemplate], and [`AmmunitionTemplate`][osrlib.core.items.AmmunitionTemplate]s, the same models the built-in equipment lists are made of, discriminated by the `item_type` field each one already has. Every session running that adventure resolves them everywhere it resolves a built-in equipment id: a treasure cache's `item_ids`, [`GrantItem`][osrlib.crawl.commands.GrantItem], and the drop pile a party recovers goods from. [`GameSession.effective_equipment`][osrlib.crawl.session.GameSession.effective_equipment] is the built-in catalog plus the bundle. After acquisition, nothing differs: an [`ItemInstance`][osrlib.core.items.ItemInstance] embeds its whole template, so equipping, encumbrance, combat, handing items between members, dropping, saving, and replay never look the id up again.

The one rule is the collision rule, and for items it's three-way: a bundled id must collide with neither the equipment catalog, nor the magic-item catalog, nor another bundled id, because an item id must refer to exactly one thing in a session. [`validate_adventure`][osrlib.crawl.adventure.validate_adventure] rejects a collision and never overrides a built-in item, so give a brighter torch a different id. The rule doesn't apply to the ids of the treasure-weight rows (`coin`, `gem`, `jewellery`, …), because those rows are an encumbrance table, not items. As with monsters, [the equipment id index][equipment-index] documents the built-in catalog only. Bundled ids live in the adventure that includes them:

```python
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.items import AmmunitionTemplate, ArmourTemplate, GearTemplate, WeaponTemplate
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec, validate_adventure
from osrlib.crawl.commands import GrantItem, PurchaseEquipment
from osrlib.crawl.dungeon import AreaSpec, DungeonSpec, FeatureSpec, LevelSpec
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.data import load_equipment, load_monsters

# The quest object: a gear item with no shop price, carried like any other kit.
TEMPLE_IDOL = GearTemplate.model_validate({"id": "temple_idol", "name": "Idol of the drowned saint", "cost_gp": 0})

# The saint's own arms and armour: each kind is the model its built-in list is made
# of, so a weapon carries damage and qualities, body armour both AC formats and its
# basic-encumbrance category, and ammunition the lot it is sold and found in.
DROWNED_BLADE = WeaponTemplate.model_validate(
    {
        "id": "drowned_blade",
        "name": "Drowned blade",
        "cost_gp": 0,
        "weight_coins": 60,
        "damage": "1d8",
        "qualities": ("melee",),
    }
)
SAINTS_SCALE = ArmourTemplate.model_validate(
    {
        "id": "saints_scale",
        "name": "Scale of the saint",
        "cost_gp": 0,
        "weight_coins": 300,
        "ac": 6,
        "ac_ascending": 13,
        "category": "light",
    }
)
BLESSED_STONES = AmmunitionTemplate.model_validate(
    {"id": "blessed_stones", "name": "Blessed sling stones", "cost_gp": 0, "lot_size": 20}
)

# Bundle them: the adventure document carries the templates, and a cache places
# them by id like any built-in item.
BUNDLED_IDS = ("temple_idol", "drowned_blade", "saints_scale", "blessed_stones")

level = LevelSpec(
    number=1,
    width=2,
    height=1,
    entrance=(0, 0),
    areas=(
        AreaSpec(
            id="shrine",
            name="The flooded shrine",
            cells=((1, 0),),
            features=(FeatureSpec(id="altar", kind="treasure_cache", cell=(1, 0), item_ids=BUNDLED_IDS),),
        ),
    ),
)
adventure = Adventure(
    name="The Drowned Saint",
    town=TownSpec(name="Threshold"),
    dungeons=(DungeonSpec(id="shrine", name="The Shrine", levels=(level,)),),
    items=(TEMPLE_IDOL, DROWNED_BLADE, SAINTS_SCALE, BLESSED_STONES),
)

# The same gate the built-in content passes: the base catalog goes in unchanged,
# and validation unions it with the bundle before resolving the cache's item ids.
validate_adventure(adventure, load_monsters(), load_equipment())

rng = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
hero = create_character(name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=Ruleset(), stream=rng)
session = GameSession.new(Party(members=[hero.character]), adventure, seed=7)

# The session's effective catalog resolves every bundled id — the very objects the
# adventure carries — and a grant hands over that template.
assert session.effective_equipment.get("temple_idol") is TEMPLE_IDOL
assert [session.effective_equipment.get(item_id).item_type for item_id in BUNDLED_IDS] == [
    "gear",
    "weapon",
    "armour",
    "ammunition",
]
granted = session.execute(GrantItem(character_id=hero.character.id, item_id="temple_idol"))
assert granted.accepted
assert granted.events[0].item_ids == ("temple_idol",)

# The town shop stocks the built-in lists, so the idol is found, never bought.
refused = session.execute(PurchaseEquipment(character_id=hero.character.id, item_ids=("temple_idol",)))
assert refused.rejections[0].code == "items.purchase.not_stocked"
```

Bundling has three boundaries. First, the town shop stocks the built-in equipment lists only, so a bundled item is never for sale: the session rejects a [`PurchaseEquipment`][osrlib.crawl.commands.PurchaseEquipment] for it with `items.purchase.not_stocked`. That code is distinct from `session.command.unknown_item`, which means no such id exists at all, so your front end can say "the trader doesn't stock that" rather than "no such thing". Second, [`create_character`][osrlib.core.character.create_character]'s `purchases` run before any session exists and resolve ids straight through `load_equipment()`, so a bundled id passed there raises `ValueError`. Equip a character with built-in gear at creation, and give them the adventure's items during play.

Third, some class policies are written as id lists. A class whose [`WeaponPolicy`][osrlib.core.classes.WeaponPolicy] is `allowed` lists by id every weapon it may wield: the cleric's five blunt weapons, the magic-user's dagger. A bundled id appears in no built-in class's list, so a cleric carrying the drowned blade can never equip it and gets the same `items.equip.weapon_not_allowed` an off-list built-in weapon does. Classes whose weapons are `any` or a `forbidden` list (fighter, elf, thief, dwarf, halfling) wield bundled weapons with no extra step. Armour behaves the same way where the policy names an id: the thief's `leather_only` allows only the id `leather`, so a thief can't wear bundled body armour whatever its category, while a class with `any` armour wears the saint's scale exactly as it wears plate. None of that is specific to bundling. It comes from the built-in classes' policies, and a custom class can list your bundled ids in its `weapon_ids`.

## What's not supported

There's no merge path into the built-in content. `load_classes` and `load_spells` are cached loaders that read the generated `classes.json` and `spells.json` files inside the package. There's no append or register call, so an extended catalog is always a value your code builds and keeps: `classes` and `spells` above, never something you pass back to the loaders. `load_monsters` and `load_equipment` don't accept additions either. Bundling ([monsters](#bundling-custom-monsters-with-an-adventure), [items](#bundling-custom-items-with-an-adventure)) combines your templates with the built-in catalog for each session through the adventure document that contains them, and the built-in catalog objects never change.

[`create_character`][osrlib.core.character.create_character], the one-call wrapper used in [the quickstart](../getting-started/quickstart.md), resolves its `class_id` argument through `load_classes().get(class_id)`. That's the same module attribute `Character.definition` reads, so [the reassignment above](#characters-of-a-custom-class) covers the wrapper too: reassign `load_classes` and `create_character(class_id="warden", ...)` rolls a warden. Leave the assignment alone and the wrapper finds built-in ids only. None of the stepwise creation functions resolves a class by id, so none of them needs the reassignment: [`roll_ability_scores`][osrlib.core.character.roll_ability_scores], `validate_class_choice`, [`roll_hit_points`][osrlib.core.character.roll_hit_points], [`validate_extra_languages`][osrlib.core.character.validate_extra_languages], [`roll_starting_gold`][osrlib.core.character.roll_starting_gold], and [`choose_starting_spells`][osrlib.core.character.choose_starting_spells] take a `ClassDefinition` object or nothing but a stream, and they run the same procedure `create_character` combines.

The `load_classes` reassignment above is a plain module attribute, not a supported extension API with a dedicated function or parameter. There's nothing to call. You reassign the name, and osrlib doesn't check whether you set it back. If your game has custom classes, reassign it once at startup and use your extended catalog as the only `load_classes` your characters see for the life of the process, the same way the [complete program](#the-complete-program) above does. Spells need no equivalent reassignment. osrlib never resolves a spell by id from a character the way `Character.definition` resolves a class, so `SpellCatalog.get` and `SpellCatalog.by_list` calls against your own extended catalog are all a caster needs.

For the ids the built-in catalogs already use, see [the class id index][classes-index] and [the spell id index][spells-index]. For every model and function this page named, see [the API reference](../reference/api/index.md).

## Where next

- [Building an adventure](../getting-started/building-an-adventure.md) - the dungeon geometry and keyed content the bundled monsters and items above attach to.
- [Gates, triggers, and quests](gates-triggers-quests.md) - authored behavior: the gate that a bundled key opens, the trigger that matches a bundled id, the quest that ends the adventure.
- [Sessions, commands, and events](sessions-commands-events.md) - running a character, custom class or not, through an actual session once it exists.
- [The API reference](../reference/api/index.md) - the full model and function reference for everything named on this page.
