# Authoring custom classes, spells, monsters, and items

osrlib compiles its built-in classes, spells, monsters, and equipment from the OSE SRD into frozen package data. Rather than mutating those cached catalogs, you define custom models in code, validate them with the same Pydantic models as the built-in data, and attach them where the engine looks them up (for authored gates, triggers, and quests, see [Gates, triggers, and quests](gates-triggers-quests.md)):

| Custom content | Model | Where to pass or bundle it |
| --- | --- | --- |
| Character class | [`ClassDefinition`][osrlib.core.classes.ClassDefinition] | `Character.custom_class` / `create_character(..., custom_class=...)` (or pass directly to kernel functions) |
| Spell | [`SpellTemplate`][osrlib.core.spells.SpellTemplate] | `Adventure.spells` for sessions, or `SpellCatalog` for `create_character` and standalone calls |
| Monster | [`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate] | [Bundle on `Adventure.monsters`](#bundling-custom-monsters-with-an-adventure) or pass directly to `spawn_monster` |
| Equipment | `WeaponTemplate`, `ArmourTemplate`, `GearTemplate`, `AmmunitionTemplate` | [Bundle on `Adventure.items`](#bundling-custom-items-with-an-adventure) |

## Define a custom class

Construct a [`ClassDefinition`][osrlib.core.classes.ClassDefinition] with `model_validate` (`race` accepts any valid slug string rather than restricting you to built-in races):

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
```

Each field controls a step of character creation, equipment validation, or XP calculation:

- **`requirements` and `prime_requisites`**: `requirements` sets minimum ability scores checked during class selection. `prime_requisites` lists abilities that cannot be lowered during ability-score adjustment. [`xp_modifier_pct`][osrlib.core.classes.xp_modifier_pct] evaluates `xp_tiers` directly in best-first order, returning the first matching `modifier_pct` (or `0` when none match, which is why multi-prime-requisite classes omit penalty tiers).
- **`may_not_lower`**: protects additional ability scores from being lowered during score adjustment (matching how the built-in thief protects STR).
- **`armour` and `weapons`**: [`ArmourPolicy`][osrlib.core.classes.ArmourPolicy] sets `kind` (`"any"`, `"leather_only"`, or `"none"`) and `shields_allowed`. [`WeaponPolicy`][osrlib.core.classes.WeaponPolicy] sets `kind` to `"any"` (with no `weapon_ids`) or `"allowed"` / `"forbidden"` (with an explicit `weapon_ids` tuple), plus optional `manual_notes` for stature restrictions the engine does not automate.
- **`hit_die`, `max_level`, and `languages`**: set the class's base Hit Die size, level cap, and starting language IDs.

### Configure class abilities and level progression

On `abilities`, each [`ClassAbility`][osrlib.core.classes.ClassAbility] entry specifies `tag`, `name`, `prose`, and a `params` dict (stored as a read-only [`FrozenMap`][osrlib.core.frozen.FrozenMap]). Built-in rules inspect known ability tags by name while ignoring custom tags (which your front end can still display):

- **Detection skills**: [`detection_chance`][osrlib.core.classes.detection_chance] reads `listening_at_doors`, `detect_secret_doors`, `detect_room_traps`, and `detect_construction_tricks`, each expecting a `chance_in_six` integer in `params`.
- **Spellcasting**: [`caster_profile`][osrlib.core.spells.caster_profile] reads `divine_magic` and `arcane_magic` (both require a `spell_list` string in `params`) and returns a [`CasterProfile`][osrlib.core.spells.CasterProfile]. A class can have one of these two abilities at most, so `model_validate` refuses a class that has both, or two of either. Divine casters choose between normal and reversed forms when casting, whereas arcane casters choose when memorizing spells from their spell book.

`level_titles[i]` gives the title at level `i + 1` (and can contain fewer entries than `progression` when titles stop at name level). `progression` defines one [`ProgressionRow`][osrlib.core.classes.ProgressionRow] per level. [`ClassDefinition.row`][osrlib.core.classes.ClassDefinition.row] looks up THAC0, `attack_bonus`, [`SavingThrows`][osrlib.core.classes.SavingThrows] (`saves`), [`HitDice`][osrlib.core.classes.HitDice] (`count`, `die`, post-name-level `bonus`, and `con_applies`), and `spell_slots` (`spell_slots[i]` slots for spell level `i + 1`) dynamically for the character's current level.

## Define a custom spell

Construct a [`SpellTemplate`][osrlib.core.spells.SpellTemplate] with display strings (`duration` and `range`), structured specs ([`DurationSpec`][osrlib.core.spells.DurationSpec] and [`RangeSpec`][osrlib.core.spells.RangeSpec]), a `spell_list` slug matching `CasterProfile.spell_list`, and one or more [`SpellMode`][osrlib.core.spells.SpellMode] entries:

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

Each `SpellMode` defines a castable `key`, an optional [`display_name`][osrlib.core.spells.SpellMode.display_name] that your front end shows where a player chooses one of a form's modes, a `targeting` spec, an optional `save`, and either an automated `effect` (such as `"heal"` or `"damage"` with `params`) or `manual=True` with `prose` (which expends the memorized spell and emits the cast event so your front end can resolve the effect). Reversible spells also accept an optional [`ReversedForm`][osrlib.core.spells.ReversedForm] with its reversed name and modes, matching built-in spells such as `cure_light_wounds`. When the reverse reaches a different distance from the normal form, as *cause fear* reaches 120' and *remove fear* only a creature touched, give the reverse its own `range` and `range_spec`, and a reversed cast is checked against them. A mode that leaves `display_name` out, or gives one that is empty or only spaces, gets a label made from its key: each run of underscores becomes one space and the first letter becomes a capital, so `"mend"` is labelled `"Mend"` and `"kill_green_slime"` is labelled `"Kill green slime"`. When a form has more than one mode, give each one a label that says what the caster does, such as `"Blind a creature"`, and give no two of them the same label, so a player can tell them apart.

A mode can take a cell reference as its target, as *light* does, unless the mode needs a creature, such as a mode that allows a save, deals damage, or grants a condition. When your mode's effect does something only to a creature but needs none of those things, such as an `attach_only` effect that grants flight, set `creatures_only` to `true` in its `targeting`, as the built-in *fly* does. [`validate_cast`][osrlib.core.spells.validate_cast] then refuses a cell reference with `magic.cast.target_must_be_creature`.

## Validate custom class and spell catalogs

Combine your definitions with [`load_classes`][osrlib.data.load_classes] and [`load_spells`][osrlib.data.load_spells] inside [`ClassCatalog`][osrlib.core.classes.ClassCatalog] and [`SpellCatalog`][osrlib.core.spells.SpellCatalog] to enforce unique IDs and schema validation without mutating the cached built-in catalogs:

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

Kernel functions such as [`validate_class_choice`][osrlib.core.character.validate_class_choice] accept `WARDEN` or `spells` directly as arguments.

## Create, advance, and cast with a custom class

Pass `class_id` and [`custom_class`][osrlib.core.character.Character.custom_class] when constructing a [`Character`][osrlib.core.character.Character] or calling [`create_character`][osrlib.core.character.create_character]. `Character` raises `ValueError` if `custom_class.id` does not match `class_id` or collides with a built-in class ID (give variants of built-in classes a distinct `id`, as you do when [making a variant of a built-in monster](#make-a-variant-of-a-built-in-monster)). `race` accepts any valid slug string shared by the class and character:

```{.python .no-run}
scores = {ability: 11 for ability in AbilityScore} | {AbilityScore.WIS: 13, AbilityScore.CON: 13}
warden = Character(
    id="pc-warden",
    name="Halda",
    class_id="warden",
    custom_class=WARDEN,
    race="human",
    level=1,
    xp=0,
    scores=scores,
    alignment=Alignment.LAWFUL,
    max_hp=6,
    current_hp=6,
)
assert warden.definition is WARDEN
assert warden.thac0 == 19
assert warden.saves.spells == 14
```

[`Character.definition`][osrlib.core.character.Character.definition] returns `WARDEN`, and [`Character.to_document`][osrlib.core.character.Character.to_document], [`party_to_document`][osrlib.core.character.party_to_document], and [`save_game`][osrlib.persistence.save_game] embed `custom_class` automatically so [`Character.from_document`][osrlib.core.character.Character.from_document] and `load_game` restore the character without external class lookups:

```{.python .no-run}
assert Character.from_document(warden.to_document()).definition == WARDEN
```

Roll a 1st-level custom-class character with `create_character` the same way:

```{.python .no-run}
streams = RngStreams(master_seed=2026)
oswin = create_character(
    name="Oswin",
    class_id="warden",
    custom_class=WARDEN,
    alignment=Alignment.LAWFUL,
    ruleset=Ruleset(),
    stream=streams.get(CHARACTER_CREATION_STREAM),
).character
assert oswin.definition is WARDEN
```

Outside a session, pass `WARDEN` and your extended `SpellCatalog` to [`level_up`][osrlib.core.classes.level_up], [`memorize_spells`][osrlib.core.spells.memorize_spells], and [`cast_spell`][osrlib.core.spells.cast_spell]. `cast_spell` takes candidate creatures in `targets` and a dict of all live combatants by ID in `registry` (used when resolving effects that touch creatures beyond `targets`), along with an [`EffectsLedger`][osrlib.core.effects.EffectsLedger], [`GameClock`][osrlib.core.clock.GameClock], and `IdAllocator`:

```{.python .no-run}
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
```

## Bundling custom spells with an adventure

[`GameSession`][osrlib.crawl.session.GameSession] reads custom classes directly from each party member's `Character.definition`, and resolves spells through [`GameSession.effective_spells`][osrlib.crawl.session.GameSession.effective_spells] (the union of `load_spells()` and [`Adventure.spells`][osrlib.crawl.adventure.Adventure.spells]). Add custom or variant spells to `Adventure.spells`. [`validate_adventure`][osrlib.crawl.adventure.validate_adventure] rejects duplicate IDs or collisions with built-in spell IDs:

```{.python .no-run}
# A magic-user spell of your own, made from magic missile's entry.
FROST_DART = SpellTemplate.model_validate(
    {**load_spells().get("magic_missile").model_dump(), "id": "frost_dart", "name": "Frost Dart"}
)

level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
adventure = Adventure(
    name="The Warden's Watch",
    town=TownSpec(name="Threshold"),
    dungeons=(DungeonSpec(id="watch", name="The Watch", levels=(level,)),),
    spells=(MEND_WOUNDS, FROST_DART),
)
validate_adventure(adventure, load_monsters(), load_equipment())
```

To place a bundled spell in a new character's starting spell book before creating the session, pass a combined `SpellCatalog` to `create_character(..., spell_catalog=...)`:

```{.python .no-run}
miri = create_character(
    name="Miri",
    class_id="magic_user",
    alignment=Alignment.NEUTRAL,
    ruleset=Ruleset(),
    stream=streams.get(CHARACTER_CREATION_STREAM),
    starting_spell_ids=["frost_dart"],
    spell_catalog=SpellCatalog(spells=(*load_spells().spells, *adventure.spells)),
).character
assert miri.spell_book == ("frost_dart",)
```

Once the session starts, [`PrepareSpells`][osrlib.crawl.commands.PrepareSpells], [`LearnSpell`][osrlib.crawl.commands.LearnSpell], [`CastSpell`][osrlib.crawl.commands.CastSpell], and battle `cast` declarations resolve bundled spells alongside built-in spells. [`MemberView`][osrlib.crawl.views.MemberView] lists `spell_book`, `preparable_spells`, `learnable_spells`, and `memorized_spells` by spell ID (such as `"frost_dart"`). A bundled spell is in [`preparable_spells`][osrlib.crawl.views.MemberView.preparable_spells] for a divine caster whose class casts from its list and who has a slot at its level, and in [`learnable_spells`][osrlib.crawl.views.MemberView.learnable_spells] for an arcane caster whose class casts from its list and whose book has room at its level, so a player can find it before any member has it. [`PlayerView.spell_names`][osrlib.crawl.views.PlayerView.spell_names] contains the name of each listed spell and [`PlayerView.spell_levels`][osrlib.crawl.views.PlayerView.spell_levels] its level, both read from `session.effective_spells`, so show those names rather than looking the ids up in `load_spells()`, which lacks the spells the adventure bundles:

```{.python .no-run}
bran = Character(
    name="Bran",
    class_id="fighter",
    race="human",
    level=1,
    xp=0,
    scores={ability: 11 for ability in AbilityScore},
    alignment=Alignment.LAWFUL,
    max_hp=8,
    current_hp=2,
)
session = GameSession.new(Party(members=[oswin, miri, bran]), adventure, seed=2026)
assert session.effective_spells.get("mend_wounds") is MEND_WOUNDS

assert session.execute(Rest(kind="night")).accepted
prayer = PrepareSpells(character_id=oswin.id, selections=(MemorizedSpell(spell_id="mend_wounds"),))
assert session.execute(prayer).accepted
healed = session.execute(CastSpell(character_id=oswin.id, spell_id="mend_wounds", mode="mend", targets=(bran.id,)))
assert healed.accepted
assert bran.current_hp > 2
```

## Complete class and spell example

```python
import json

from osrlib.core.abilities import AbilityScore
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, Character, create_character, validate_class_choice
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
from osrlib.crawl.adventure import Adventure, TownSpec, validate_adventure
from osrlib.crawl.commands import CastSpell, PrepareSpells, Rest
from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.data import load_classes, load_equipment, load_monsters, load_spells

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

# A character of your class contains the definition, and a document of the character includes it.
scores = {ability: 11 for ability in AbilityScore} | {AbilityScore.WIS: 13, AbilityScore.CON: 13}
warden = Character(
    id="pc-warden",
    name="Halda",
    class_id="warden",
    custom_class=WARDEN,
    race="human",
    level=1,
    xp=0,
    scores=scores,
    alignment=Alignment.LAWFUL,
    max_hp=6,
    current_hp=6,
)
assert warden.definition is WARDEN
assert warden.thac0 == 19
assert warden.saves.spells == 14
assert Character.from_document(warden.to_document()).definition == WARDEN

# create_character rolls a first-level warden the way it rolls a fighter.
streams = RngStreams(master_seed=2026)
oswin = create_character(
    name="Oswin",
    class_id="warden",
    custom_class=WARDEN,
    alignment=Alignment.LAWFUL,
    ruleset=Ruleset(),
    stream=streams.get(CHARACTER_CREATION_STREAM),
).character
assert oswin.definition is WARDEN

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

# A magic-user spell of your own, made from magic missile's entry.
FROST_DART = SpellTemplate.model_validate(
    {**load_spells().get("magic_missile").model_dump(), "id": "frost_dart", "name": "Frost Dart"}
)

# Bundle both spells with an adventure, so a session can play them.
level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
adventure = Adventure(
    name="The Warden's Watch",
    town=TownSpec(name="Threshold"),
    dungeons=(DungeonSpec(id="watch", name="The Watch", levels=(level,)),),
    spells=(MEND_WOUNDS, FROST_DART),
)
validate_adventure(adventure, load_monsters(), load_equipment())

# A new magic-user starts with a bundled spell in her book.
miri = create_character(
    name="Miri",
    class_id="magic_user",
    alignment=Alignment.NEUTRAL,
    ruleset=Ruleset(),
    stream=streams.get(CHARACTER_CREATION_STREAM),
    starting_spell_ids=["frost_dart"],
    spell_catalog=SpellCatalog(spells=(*load_spells().spells, *adventure.spells)),
).character
assert miri.spell_book == ("frost_dart",)

bran = Character(
    name="Bran",
    class_id="fighter",
    race="human",
    level=1,
    xp=0,
    scores={ability: 11 for ability in AbilityScore},
    alignment=Alignment.LAWFUL,
    max_hp=8,
    current_hp=2,
)
session = GameSession.new(Party(members=[oswin, miri, bran]), adventure, seed=2026)
assert session.effective_spells.get("mend_wounds") is MEND_WOUNDS

# The warden prays for the bundled spell after a night's rest and heals Bran in town.
assert session.execute(Rest(kind="night")).accepted
prayer = PrepareSpells(character_id=oswin.id, selections=(MemorizedSpell(spell_id="mend_wounds"),))
assert session.execute(prayer).accepted
healed = session.execute(CastSpell(character_id=oswin.id, spell_id="mend_wounds", mode="mend", targets=(bran.id,)))
assert healed.accepted
assert bran.current_hp > 2
assert oswin.memorized_spells == ()
```

## Bundling custom monsters with an adventure

Add custom [`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate] instances to `Adventure.monsters` so [`GameSession.effective_monsters`][osrlib.crawl.session.GameSession.effective_monsters] resolves them everywhere monster template IDs appear: keyed encounters, [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters], custom wandering tables, listen checks, and [`GameSession.spawn`][osrlib.crawl.session.GameSession.spawn]. Once spawned, each [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] embeds its `MonsterTemplate`, so combat, morale, XP, treasure, saves, and replay never look up the template ID again.

Build a `MonsterTemplate` with `model_validate`, using [`thac0_for_hd`][osrlib.core.tables.thac0_for_hd], [`monster_save_band_label`][osrlib.core.tables.monster_save_band_label], and [`monster_xp`][osrlib.core.tables.monster_xp] to compute B/X attack numbers, save bands, and XP values from Hit Dice. Configure naming and validation fields as follows:

- **Unique IDs and treasure types**: [`validate_adventure`][osrlib.crawl.adventure.validate_adventure] rejects bundled monster IDs that duplicate another bundled ID or collide with [`load_monsters`][osrlib.data.load_monsters] ([the monster id index][monsters-index]), and verifies that every letter in `treasure` appears in [the treasure type index][treasure-types-index].
- **Intelligence**: Set [`intelligent`][osrlib.core.monsters.MonsterTemplate.intelligent] to `True` or `False` to say whether your monster is intelligent. When monsters pursue a fleeing party, dropped treasure can stop intelligent pursuers and dropped food can stop unintelligent ones. If you leave it `None`, the session judges the monster by its `treasure`: a monster whose treasure lists letters counts as intelligent, and any other monster counts as unintelligent.
- **Display names and families**: Set [`display_name`][osrlib.core.monsters.MonsterTemplate.display_name] (for example, `"bone warden"`) for lowercase group labels and message formatting. Omitting it defaults to `name` with its capitalization, and regular plurals are formed automatically unless you override [`display_name_plural`][osrlib.core.monsters.MonsterTemplate.display_name_plural]. The player view shows the players each group's [`family_id`][osrlib.core.monsters.MonsterTemplate.family_id], in [`EncounterGroupView.family_id`][osrlib.crawl.views.EncounterGroupView.family_id], so a client can tell what kind of creature a group is. It shows each group's [`categories`][osrlib.core.monsters.MonsterTemplate.categories] too. A template's `family_id` defaults to its `id`. Hit Dice variants that look identical to players share a `family_id` and `display_name` (matching how built-in hellhound variants share `"hellhound"`), and a creature disguised as another sets `family_id` and `display_name` to those of the creature the players see, as [Make a variant of a built-in monster](#make-a-variant-of-a-built-in-monster) shows:

```python
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.monsters import MonsterHitDice, MonsterTemplate
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.tables import monster_save_band_label, monster_xp, thac0_for_hd
from osrlib.crawl.adventure import Adventure, TownSpec, validate_adventure
from osrlib.crawl.dungeon import AreaSpec, DungeonSpec, Edge, EdgeKind, KeyedEncounter, KeyedMonster, LevelSpec
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
        "display_name": "bone warden",
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
assert (BONE_WARDEN.display_name_plural, BONE_WARDEN.family_id) == ("bone wardens", "bone_warden")

# Bundle it: the adventure document carries the template, and a keyed area
# references it like any built-in id.
level = LevelSpec(
    number=1,
    width=2,
    height=1,
    entrance=(0, 0),
    edges={"1,0:west": Edge(kind=EdgeKind.OPEN)},
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

# The same check the built-in content passes. You pass the base catalog unchanged,
# and validation combines it with the bundle.
validate_adventure(adventure, load_monsters(), load_equipment())

rng = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
hero = create_character(name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=Ruleset(), stream=rng)
session = GameSession.new(Party(members=[hero.character]), adventure, seed=7)

# The session's effective catalog resolves the bundled id to the same object the
# adventure contains, and spawning embeds it in each instance.
assert session.effective_monsters.get("bone_warden") is BONE_WARDEN
guards = session.spawn("bone_warden", 2)
assert [guard.template.id for guard in guards] == ["bone_warden", "bone_warden"]
assert all(guard.max_hp >= 3 for guard in guards)  # 2d8+1 rolls at least 3
```

### Make a variant of a built-in monster

Built-in templates and their nested `FrozenMap` fields are immutable (`TypeError` on item assignment) and shared across all sessions in the process. To modify nested fields (such as an ability's `params`), call `model_dump()` to obtain mutable dicts, edit the dump, and call `model_validate` with a distinct `id`. The dump contains the base creature's `display_name`, `family_id`, and `categories`, and the player view shows all three to the players. A variant that the players can tell from the base creature, like the bog troll below, gets a `display_name` and `family_id` of its own. A disguised variant, like a doppelganger the players see as a goblin, leaves the base creature's `display_name`, `family_id`, and `categories` as they are, so the players see a goblin:

```python
from osrlib.core.monsters import MonsterTemplate
from osrlib.data import load_monsters

troll = load_monsters().get("troll")
document = troll.model_dump()
for ability in document["abilities"]:
    if ability["tag"] == "regeneration":
        ability["params"]["per_round"] = 5

BOG_TROLL = MonsterTemplate.model_validate(
    {**document, "id": "bog_troll", "name": "Bog Troll", "display_name": "bog troll", "family_id": "bog_troll"}
)
print(BOG_TROLL.ability("regeneration").params["per_round"], troll.ability("regeneration").params["per_round"])
# 5 3
```

When you only need to override top-level fields (such as `id` and `name`), call `model_copy(update=...)`, which re-validates the updated fields and converts mapping values to `FrozenMap`:

```python
from osrlib.data import load_monsters

cave_goblin = load_monsters().get("goblin").model_copy(update={"id": "cave_goblin", "name": "Cave Goblin"})
print(cave_goblin.id, cave_goblin.hit_dice.count)
# cave_goblin 1
```

Unlike spells, monsters, and items, custom classes are stored on each character (`Character.custom_class`, see [Create, advance, and cast with a custom class](#create-advance-and-cast-with-a-custom-class)) rather than on `Adventure`.

## Bundling custom items with an adventure

Add custom [`WeaponTemplate`][osrlib.core.items.WeaponTemplate], [`ArmourTemplate`][osrlib.core.items.ArmourTemplate], [`GearTemplate`][osrlib.core.items.GearTemplate], and [`AmmunitionTemplate`][osrlib.core.items.AmmunitionTemplate] instances (discriminated by `item_type`) to `Adventure.items`. [`GameSession.effective_equipment`][osrlib.crawl.session.GameSession.effective_equipment] unions `load_equipment()` with `Adventure.items`, resolving bundled IDs in treasure caches (`FeatureSpec.item_ids`), [`GrantItem`][osrlib.crawl.commands.GrantItem], and dropped item piles. Once acquired, [`ItemInstance`][osrlib.core.items.ItemInstance] embeds its template for equipping, encumbrance, combat, item transfers, saves, and replay.

You can also set what the players call a bundled item in a sentence. Set [`display_name`][osrlib.core.items.GearTemplate.display_name] to what to call one, in lowercase with no article, like `"idol of the drowned saint"`, and [`display_name_plural`][osrlib.core.items.GearTemplate.display_name_plural] to what to call two or more. [`PlayerView.item_names`][osrlib.crawl.views.PlayerView.item_names] and [`PlayerView.item_names_plural`][osrlib.crawl.views.PlayerView.item_names_plural] give them to the players' client. A template that leaves `display_name` out gets its `name`, and one that leaves `display_name_plural` out gets the display name with the regular English ending on the word that names the item, the last word before the first `of`, `with`, or `vs` in any case, a comma, a bonus such as `+1`, a closing Roman numeral, or an opening parenthesis, or the last word of a name with none of them. So `"idol of the drowned saint"` gets `"idols of the drowned saint"`, and `"Staff Of Power"` gets `"Staves Of Power"`. Set the plural yourself when the name is already plural, such as `"Torches (6)"`, or when the plural is irregular.

[`validate_adventure`][osrlib.crawl.adventure.validate_adventure] requires every bundled item ID to be unique across `Adventure.items`, the built-in equipment catalog ([the equipment id index][equipment-index]), and the built-in magic-item catalog (treasure encumbrance row IDs such as `coin`, `gem`, and `jewellery` do not count as item IDs):

```python
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.items import AmmunitionTemplate, ArmourTemplate, GearTemplate, WeaponTemplate
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec, validate_adventure
from osrlib.crawl.commands import GrantItem, PurchaseEquipment
from osrlib.crawl.dungeon import AreaSpec, DungeonSpec, Edge, EdgeKind, FeatureSpec, LevelSpec
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.data import load_equipment, load_monsters

# The quest object: a gear item with no shop price, carried like any other kit. Its
# display names are what the players' client shows in a sentence.
TEMPLE_IDOL = GearTemplate.model_validate(
    {
        "id": "temple_idol",
        "name": "Idol of the drowned saint",
        "cost_gp": 0,
        "display_name": "idol of the drowned saint",
        "display_name_plural": "idols of the drowned saint",
    }
)

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

# A template written without display names gets its name, and the plural ending on the
# word that names the item.
assert (DROWNED_BLADE.display_name, DROWNED_BLADE.display_name_plural) == ("Drowned blade", "Drowned blades")

# Bundle them: the adventure document carries the templates, and a cache places
# them by id like any built-in item.
BUNDLED_IDS = ("temple_idol", "drowned_blade", "saints_scale", "blessed_stones")

level = LevelSpec(
    number=1,
    width=2,
    height=1,
    entrance=(0, 0),
    edges={"1,0:west": Edge(kind=EdgeKind.OPEN)},
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

# The session's effective catalog resolves every bundled id to the same objects the
# adventure contains, and a grant hands over that template.
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

Item bundling has specific scope rules:

- **Town shop stock**: [`PurchaseEquipment`][osrlib.crawl.commands.PurchaseEquipment] sells only built-in equipment and rejects bundled IDs with `items.purchase.not_stocked` (distinct from `session.command.unknown_item`, so your UI can distinguish unstocked items from nonexistent IDs).
- **Character creation**: [`create_character`][osrlib.core.character.create_character] resolves `purchases` against `load_equipment()` before any session exists, raising `ValueError` for bundled IDs. Purchase built-in starting gear at creation and grant or place bundled items during play.
- **Class weapon and armour allowlists**: Built-in classes whose [`WeaponPolicy`][osrlib.core.classes.WeaponPolicy] uses `kind="allowed"` (such as cleric and magic-user) permit only the specific built-in IDs in `weapon_ids`, rejecting bundled weapons with `items.equip.weapon_not_allowed`, whereas classes with `kind="any"` or `kind="forbidden"` (fighter, elf, thief, dwarf, halfling) can wield bundled weapons. Similarly, `ArmourPolicy(kind="leather_only")` permits only the built-in `"leather"` ID, whereas `kind="any"` accepts custom armour such as `saints_scale`. Custom classes can list your bundled weapon IDs directly in `weapon_ids`.

## Describe a treasure cache for the players

A treasure cache has two readers, and [`FeatureSpec`][osrlib.crawl.dungeon.FeatureSpec] gives each one a field. The players read the cache's [`description`][osrlib.crawl.dungeon.FeatureSpec.description], which the player view shows as [`MapFeatureView.description`][osrlib.crawl.views.MapFeatureView.description]. The referee reads [`referee_notes`][osrlib.crawl.dungeon.FeatureSpec.referee_notes], but the players never read it.

The player view shows the description as soon as the cache comes into view. A cache without a `cell` comes into view when the party enters its area. A cache on a cell comes into view when that cell is on the party's map, because the party has walked it, remembers seeing it, or lights it now. The players read the description before anyone opens the cache, and they still read it after the party empties it. So write what the party sees from outside the cache, in words that stay true once it's empty, and put everything else in the field that osrlib uses for it:

- **Contents**: List them in `item_ids`, `magic_item_ids`, `coins`, and `valuables`. The party learns what it took from the events of [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure], and the inventory shows an unidentified magic item under a masked name, as [Views and visibility](views-and-visibility.md#read-character-sheets-and-masked-magic-items) describes. A description that names the potion gives away what the masked name hides.
- **Trap**: Put it in `trap`, as a treasure [`TrapSpec`][osrlib.crawl.dungeon.TrapSpec]. The players learn about it from [`MapFeatureView.trap_state`][osrlib.crawl.views.MapFeatureView.trap_state] once a thief finds it, it springs, or a thief removes it. A description that mentions the needle tells the players about a trap they haven't found.
- **What the referee needs to know**: Write it in `referee_notes`, such as where a cache's contents came from or what a thief sees when the trap is found. osrlib never reads the field, no event contains it, and no rule depends on it. It's in the adventure document, so a save and [`RefereeView.adventure`][osrlib.crawl.views.RefereeView.adventure] have it, but the player view never includes it.

The coffer in this sacristy holds coins, a potion, and a censer, and a venomed needle guards its hasp. The players read only the description of the iron coffer on the shelf:

```python
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.clock import TimeUnit
from osrlib.core.combat import SaveCategory
from osrlib.core.effects import Condition
from osrlib.core.events import Visibility
from osrlib.core.items import Coins
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.spells import SaveSpec
from osrlib.crawl.adventure import Adventure, TownSpec, validate_adventure
from osrlib.crawl.commands import EnterDungeon, MoveParty, TakeTreasure
from osrlib.crawl.dungeon import (
    AreaSpec,
    Direction,
    DungeonSpec,
    Edge,
    EdgeKind,
    FeatureSpec,
    LevelSpec,
    TrapEffect,
    TrapSpec,
    ValuableSpec,
    WanderingSpec,
)
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.data import load_equipment, load_monsters

# The description is what the party sees from the doorway, and it stays true after the
# party empties the coffer. The contents, the trap, and what the referee needs to run
# them each go in a separate field.
COFFER = FeatureSpec(
    id="sacristy_coffer",
    kind="treasure_cache",
    description="A small iron coffer sits on a shelf behind the altar, its lid held by a brass hasp.",
    referee_notes=(
        "The priests' poor-box. They kept the potion for the sick. A needle in the hasp is "
        "smeared with a paralysing venom, and a thief who finds it sees a dark stain around "
        "the hasp's pin."
    ),
    coins=Coins(sp=200, gp=35),
    magic_item_ids=("potion_of_healing",),
    valuables=(ValuableSpec(kind="jewellery", name="A silver censer on a chain", value_gp=150, weight_coins=20),),
    trap=TrapSpec(
        kind="treasure",
        trigger="open",
        effect=TrapEffect(
            save=SaveSpec(category=SaveCategory.PARALYSIS),
            condition=Condition.PARALYSED,
            condition_duration_dice="2d4",
            condition_duration_unit=TimeUnit.TURN,
        ),
    ),
)

level = LevelSpec(
    number=1,
    width=2,
    height=1,
    entrance=(0, 0),
    edges={"1,0:west": Edge(kind=EdgeKind.OPEN)},
    areas=(AreaSpec(id="sacristy", name="The sacristy", cells=((1, 0),), features=(COFFER,)),),
    wandering=WanderingSpec(chance_in_six=0),
)
adventure = Adventure(
    name="The Sacked Chapel",
    town=TownSpec(name="Threshold", travel_turns={"chapel": 1}),
    dungeons=(DungeonSpec(id="chapel", name="The Chapel", levels=(level,)),),
)
validate_adventure(adventure, load_monsters(), load_equipment())

rng = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
hero = create_character(name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=Ruleset(), stream=rng)
session = GameSession.new(Party(members=[hero.character]), adventure, seed=7)
assert session.execute(EnterDungeon(dungeon_id="chapel")).accepted


def players_coffer():
    """The coffer as the players see it, and the whole player view it's in."""
    view = session.view(Visibility.PLAYER)
    (feature,) = (feature for explored in view.explored for feature in explored.features)
    return feature, view


# The party walks into the sacristy, and the coffer comes into view with its description.
assert session.execute(MoveParty(direction=Direction.EAST)).accepted
feature, view = players_coffer()
assert feature.description == COFFER.description
assert COFFER.referee_notes not in view.model_dump_json()

# The referee reads the notes in the adventure document that the referee view contains.
referee = session.view(Visibility.REFEREE)
assert referee.adventure.dungeons[0].levels[0].areas[0].features[0].referee_notes == COFFER.referee_notes

# The party empties the coffer, and the players still read the same description.
assert session.execute(TakeTreasure(feature_id=feature.id)).accepted
feature, view = players_coffer()
assert feature.description == COFFER.description
assert COFFER.referee_notes not in view.model_dump_json()
```

### Name a gem or piece of jewellery

A [`ValuableSpec`][osrlib.crawl.dungeon.ValuableSpec] with a `name` places a piece that the players know by that name. [`PlayerView.valuable_names`][osrlib.crawl.views.PlayerView.valuable_names] gives that name, which a client shows in a list, such as an inventory row. A client also puts the piece in a sentence, such as "Hild takes a silver censer on a chain.", and [`PlayerView.valuable_phrases`][osrlib.crawl.views.PlayerView.valuable_phrases] gives the phrase to use there, with the article and the case. You can write that phrase yourself in the spec's [`phrase`][osrlib.crawl.dungeon.ValuableSpec.phrase].

When `phrase` is empty or only whitespace, osrlib makes the phrase from the name, as for a common noun. A name whose first word is "the", "a", or "an" has that word in lowercase, so the censer above, "A silver censer on a chain", reads "a silver censer on a chain". Any other name has its first character in lowercase and an article in front, "an" before `a`, `e`, `i`, `o`, or `u`, with or without an accent, and "a" otherwise, so "Silver altar candlestick" reads "a silver altar candlestick". Only the first character changes case, so "Eye of Ossor" reads "an eye of Ossor". The view leaves out any spaces around a name or a phrase.

Set `phrase` yourself when the piece isn't a common noun whose first letter gives its article:

- For a proper name, such as "Ossor's Eye", set `phrase="Ossor's Eye"`.
- For a piece that takes the definite article, such as "Prior's chain of office", set `phrase="the Prior's chain of office"`.
- For a name whose first letter doesn't give its article, such as "Unicorn-horn cup" or "Hourglass of smoked crystal", set `phrase="a unicorn-horn cup"` or `phrase="an hourglass of smoked crystal"`.

A piece with no name of its own never has a phrase in the view, whatever `phrase` holds. That's a piece whose `name` is empty, is only whitespace, or is the name treasure generation gives its kind, `"Gem"` for a gem or `"Jewellery"` for a piece of jewellery. The view names such a piece by its kind in [`PlayerView.item_names`][osrlib.crawl.views.PlayerView.item_names], and a client puts it in a sentence with an article, as in "Hild takes a gem."

## Scope and cross-adventure limits

- **Read-only built-in loaders**: `load_classes`, `load_spells`, `load_monsters`, and `load_equipment` return cached, immutable SRD catalogs and provide no global registration or mutation API. Pass extended catalogs explicitly to kernel functions or bundle custom spells, monsters, and items on `Adventure`.
- **Generated scrolls and NPC casters**: Randomly rolled treasure scrolls and NPC party spell lists draw exclusively from the built-in `load_spells()` catalog, never from `Adventure.spells`.
- **Cross-adventure spell books**: If a character enters a session with a spell ID from another adventure that is absent from `session.effective_spells`, the session rejects [`PrepareSpells`][osrlib.crawl.commands.PrepareSpells] (`magic.memorize.unknown_spell`), [`CastSpell`][osrlib.crawl.commands.CastSpell] (`magic.cast.unknown_spell`), and battle `cast` declarations for that spell. While any unknown spell remains in the character's `spell_book`, the session also rejects [`LearnSpell`][osrlib.crawl.commands.LearnSpell] (`magic.book.unknown_spell`) because remaining spell-book capacity depends on every known spell's level. Energy drain cannot determine the level of an unknown memorized spell, so it leaves that entry untouched in `memorized_spells`. In the player view, the character's [`learnable_spells`][osrlib.crawl.views.MemberView.learnable_spells] is empty while the book contains the unknown spell, and [`preparable_spells`][osrlib.crawl.views.MemberView.preparable_spells] leaves the unknown id out.

See [the class id index][classes-index] and [the spell id index][spells-index] for built-in IDs, and [the API reference](../reference/api/index.md) for full model definitions.

## Where next

- [Building an adventure](../getting-started/building-an-adventure.md) - placing bundled monsters and items into dungeon levels and keyed areas.
- [Gates, triggers, and quests](gates-triggers-quests.md) - gating doors and matching triggers or quests on bundled item and monster IDs.
- [Sessions, commands, and events](sessions-commands-events.md) - running custom-class characters and bundled content through `GameSession`.
- [The API reference](../reference/api/index.md) - full reference for class, spell, monster, and item models.
