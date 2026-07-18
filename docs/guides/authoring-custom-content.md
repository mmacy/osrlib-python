# Authoring custom classes, spells, and monsters

The seven classes, the spell list, and the monster catalog that ship with osrlib are compiled from the
OSE SRD into the package's data files by a build pipeline — that pipeline is not the extension point,
and there is no way to feed your own content into it. What *is* supported is authoring your own class,
spell, and monster definitions in code, validating them the same way the shipped catalogs validate
their own content, and running them through the same kernel that plays a fighter or a goblin: creation,
advancement, memorization, casting, and — for monsters — spawning, combat, XP, and treasure. A class's
`race` and a spell's `spell_list` are both open, validated string ids for exactly this reason — nothing
in the kernel restricts them to the values the shipped classes happen to use. This page builds one
small custom class and one custom spell for it ([the complete program](#the-complete-program) runs
every step shown along the way), then [a custom monster bundled into an
adventure](#bundling-custom-monsters-with-an-adventure) for the crawl layer.

## The shape of a class definition

A [`ClassDefinition`][osrlib.core.classes.ClassDefinition] is a frozen model you build with
`model_validate` — there is no separate builder API, just the fields the shipped classes carry.
`requirements` are the minimum ability scores checked at class choice; `prime_requisites` names the
abilities that feed the ability-score adjustment step (a prime requisite can never be lowered there) and
conventionally the abilities your `xp_tiers` key off of, though the tiers are evaluated on their own,
independent of that list. `xp_tiers` are ordered best-first: the first tier whose minimums all hold sets
the class's XP-modifier percentage, and a score set matching no tier gets zero, never a penalty — how
the multi-prime-requisite classes carry no penalty rows. `hit_die` is the class's base die size:

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

`armour` and `weapons` are structured policies, not prose: an
[`ArmourPolicy`][osrlib.core.classes.ArmourPolicy] names the allowed armour kind (`any`, `leather_only`,
or `none`) and whether shields are allowed; a [`WeaponPolicy`][osrlib.core.classes.WeaponPolicy] is
either `any` with no id list, or `allowed`/`forbidden` with an explicit `weapon_ids` list (`manual_notes`
carries referee-judgment stature prose that can't be mechanized, the way the dwarf and halfling pages
do). `languages` are the tongues every member of the class speaks natively. `may_not_lower` adds
class-specific floors to the adjustment step on top of the prime-requisite rule — the warden above
protects its casting stat the way the thief's table protects STR.

## Caster tags and the progression table

`abilities` is a tuple of [`ClassAbility`][osrlib.core.classes.ClassAbility] tags: a `tag` string, a
display `name`, referee-facing `prose`, and a `params` dict of the mechanizable numbers. The shipped
procedures read a handful of tags by name — `listening_at_doors`, `detect_secret_doors`,
`detect_room_traps`, and `detect_construction_tricks` (all a `chance_in_six` param, consumed by
[`detection_chance`][osrlib.core.classes.detection_chance]) and `divine_magic`/`arcane_magic` (a
`spell_list` param, consumed by [`caster_profile`][osrlib.core.spells.caster_profile]) — but an
unrecognized tag is simply inert data your own front end can still display. A class with a
`divine_magic` or `arcane_magic` tag is a caster; `caster_profile` reads the tag straight off the
definition and returns a [`CasterProfile`][osrlib.core.spells.CasterProfile] naming its `kind` (divine
casters choose the reversed form at cast time; arcane casters fix it when memorizing, from a spell
book) and its `spell_list` — the id your spells will match against:

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

`level_titles[i]` is the title at level `i + 1`; it may run shorter than `progression` (the SRD's title
lists stop at name level). `progression` is one [`ProgressionRow`][osrlib.core.classes.ProgressionRow]
per level, and it is the *only* place saves, THAC0, attack bonus, and spell slots live —
[`ClassDefinition.row`][osrlib.core.classes.ClassDefinition.row] looks a level up fresh every time, so
leveling and energy drain are just moving which row a character reads, never a stored value to keep in
sync. `hit_dice` on a row is a [`HitDice`][osrlib.core.classes.HitDice] (`count`, `die`, a flat `bonus`
for above-name-level rows, and `con_applies` for the SRD's asterisked "CON no longer applies" rows);
`saves` is a [`SavingThrows`][osrlib.core.classes.SavingThrows] naming the five save categories;
`spell_slots[i]` is how many level-`i + 1` spells the row's caster can memorize, and it is empty for
non-casters.

## The shape of a spell

A [`SpellTemplate`][osrlib.core.spells.SpellTemplate] carries the same split: presentation strings
(`duration`, `range`) alongside the parsed, structured forms the kernel actually resolves
(`duration_spec`, a [`DurationSpec`][osrlib.core.spells.DurationSpec]; `range_spec`, a
[`RangeSpec`][osrlib.core.spells.RangeSpec]). `spell_list` is the same kind of open, validated string id
as a class's `race` — the kernel's only use of it is matching it against a caster's
`CasterProfile.spell_list`. `modes` is a tuple of [`SpellMode`][osrlib.core.spells.SpellMode]: a stable
`key` you cast by, a `targeting` spec, an optional `save`, and either an `effect` (naming one of the
kernel's automated effect kinds, like `heal` or `damage`, plus its dice and parameters) or `manual=True`
with SRD-style `prose` for a mode the kernel doesn't automate — casting a manual mode still spends the
memorized copy and emits the cast event; your game narrates the rest. A reversible spell carries a
[`ReversedForm`][osrlib.core.spells.ReversedForm] with its own name and modes, which a divine caster can
choose freely at cast time and an arcane caster must fix at memorization — none of that machinery is
exercised below, but it costs a custom spell nothing to opt in the same way `cure_light_wounds` does:

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

## Validate the way the shipped catalogs validate

[`ClassCatalog`][osrlib.core.classes.ClassCatalog] and [`SpellCatalog`][osrlib.core.spells.SpellCatalog]
are the same models [`load_classes`][osrlib.data.load_classes] and
[`load_spells`][osrlib.data.load_spells] validate their generated JSON into — building one from your own
definitions runs the identical checks (unique ids, and every per-definition rule above) that the shipped
data has to pass. A round trip through JSON is a convenient way to prove it: it exercises the exact path
the loaders take, dict in, model out:

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

Note that `classes` and `spells` here are *your* catalogs, extending a copy of the shipped ones — they
are never written back into `load_classes()` or `load_spells()`, which stay cached, frozen, and
SRD-only. [`validate_class_choice`][osrlib.core.character.validate_class_choice] above takes the
`WARDEN` definition directly, the same way it takes any shipped one; that pattern — a kernel function
accepting the `ClassDefinition` (or `SpellCatalog`) you hand it, custom or shipped, with no
registration step — is how most of this page works.

## The one seam: characters of a custom class

[`level_up`][osrlib.core.classes.level_up], [`memorize_spells`][osrlib.core.spells.memorize_spells],
[`cast_spell`][osrlib.core.spells.cast_spell], and `validate_class_choice` above all took `WARDEN` (or a
[`CasterProfile`][osrlib.core.spells.CasterProfile] built from it) as a plain argument — none of them
cared that the definition wasn't in the shipped catalog. The one place an id alone has to resolve to a
definition is [`Character`][osrlib.core.character.Character] itself:
[`Character.definition`][osrlib.core.character.Character.definition] looks its `class_id` up through
`load_classes()`, and Character's own structural validation calls `.definition` on every construction,
every field assignment (the model validates on assignment), and every document load. `load_classes` is
imported by name into `osrlib.core.character`, and that name is what `.definition` actually calls — so
reassigning the module attribute to a loader that returns your extended catalog is what makes
constructing (or revalidating, or loading) a character of a custom class possible at all:

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

This is a plain Python module attribute, not a documented plugin point with its own function — a game
that wants custom-class characters performs that reassignment once, at startup, before building or
loading any character, rather than treating it as an API to call per character. `race` needs no such
wiring: it is validated only against a slug pattern on both `ClassDefinition` and `Character`, and no
procedure looks it up anywhere, so any race string the two sides agree on already works.

## Advancing and casting

With the catalogs extended and the loader binding pointed at them, the rest of the lifecycle is the
same kernel calls a shipped class goes through. `level_up` reads next level's row straight off `WARDEN`;
`memorize_spells` checks the caster's list and slot capacity against the extended spell catalog; casting
consumes the memorized copy and resolves the mode's effect — here, healing a wounded ally by touch:

```{.python .no-run}
streams = RngStreams(master_seed=2026)
level_up(warden, WARDEN, streams.get("advancement"))
assert warden.level == 2
assert WARDEN.row(warden.level).spell_slots == (2,)

memorized = memorize_spells(warden, WARDEN, spells, (MemorizedSpell(spell_id="mend_wounds"),))
assert memorized.accepted
```

`cast_spell` needs the same standalone kernel scaffolding any spell does — an
[`EffectsLedger`][osrlib.core.effects.EffectsLedger] for attached durations, a
[`GameClock`][osrlib.core.clock.GameClock], an id allocator, and a registry of live combatants by id —
none of which differs for a custom spell:

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

# A reversible first-level spell on the warden's own list.
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
# namespace, so a game holding custom definitions swaps that binding once, up front.
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

Monsters take a different transport than classes and spells, because the crawl layer already has a
document that carries content: the adventure. `Adventure.monsters` bundles your own
[`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate]s with the adventure document, and every
session running that adventure resolves them everywhere it resolves a shipped template id — keyed
encounters, [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters], inline wandering tables, listen
checks, and [`GameSession.spawn`][osrlib.crawl.session.GameSession.spawn]. No loader reassignment, no
registration: the document carries the content, and
[`GameSession.effective_monsters`][osrlib.crawl.session.GameSession.effective_monsters] is the shipped
catalog plus the bundle. Downstream of spawning nothing is different for a bundled monster — a spawned
[`MonsterInstance`][osrlib.core.monsters.MonsterInstance] embeds its full template, so combat, morale,
XP, treasure, saves, and replay never look the id up again.

A template is a frozen model you build with `model_validate`, exactly like the class and spell above.
Three table helpers derive the stat-block numbers the SRD would print so your creation matches the
attack matrix, the monster save bands, and the XP awards table:
[`thac0_for_hd`][osrlib.core.tables.thac0_for_hd],
[`monster_save_band_label`][osrlib.core.tables.monster_save_band_label], and
[`monster_xp`][osrlib.core.tables.monster_xp]. The one rule is the collision rule: a bundled id must
not collide with the shipped catalog or another bundled id —
[`validate_adventure`][osrlib.crawl.adventure.validate_adventure] rejects collisions outright, never
overrides (an adventure that wants a variant orc names a variant id). Note that [the monster id
index][monsters-index] documents the shipped catalog only; bundled ids live in the adventure that
carries them:

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
# references it like any shipped id.
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

# The same gate the shipped content passes — the base catalog goes in unchanged,
# and validation unions it with the bundle internally.
validate_adventure(adventure, load_monsters(), load_equipment())

rng = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
hero = create_character(name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=Ruleset(), stream=rng)
session = GameSession.new(Party(members=[hero.character]), adventure, seed=7)

# The session's effective catalog resolves the bundled id — the very object the
# adventure carries — and spawning embeds it in each instance.
assert session.effective_monsters.get("bone_warden") is BONE_WARDEN
guards = session.spawn("bone_warden", 2)
assert [guard.template.id for guard in guards] == ["bone_warden", "bone_warden"]
assert all(guard.max_hp >= 3 for guard in guards)  # 2d8+1 rolls at least 3
```

Bundled equipment, classes, and spells have no adventure-document seam yet — monsters earned theirs
first, and the same shape is the template if a future need is demonstrated. For classes and spells the
catalog-extension pattern above is the supported path.

## What's not supported

There is no merge path into the shipped content. `load_classes` and `load_spells` are cached loaders
that read the generated `classes.json`/`spells.json` shipped inside the package; there is no append or
register call, so an extended catalog is always a value your own code builds and holds — `classes` and
`spells` above, never something fed back into the loaders themselves. `load_monsters` is just as
closed: [bundling](#bundling-custom-monsters-with-an-adventure) unions per session through the
adventure document that carries the templates, and the shipped catalog object never changes.

[`create_character`][osrlib.core.character.create_character], the one-call convenience wrapper used in
[the quickstart](../getting-started/quickstart.md), resolves its `class_id` argument straight through
`load_classes().get(class_id)` — as written, it only ever finds shipped ids. Every function it calls
internally takes a `ClassDefinition` object rather than an id, though:
[`roll_ability_scores`][osrlib.core.character.roll_ability_scores], `validate_class_choice`,
[`roll_hit_points`][osrlib.core.character.roll_hit_points],
[`validate_extra_languages`][osrlib.core.character.validate_extra_languages],
[`roll_starting_gold`][osrlib.core.character.roll_starting_gold], and
[`choose_starting_spells`][osrlib.core.character.choose_starting_spells] run the identical stepwise
procedure `create_character` composes, unchanged, for a custom class — only the single-call shortcut is
closed to shipped ids.

The `load_classes` reassignment above is a plain module attribute, not a supported extension API with
its own function or parameter — there's nothing to call except swapping the name, and nothing checks
that you swapped it back. A game holding custom classes reassigns it once, at startup, and keeps its
extended catalog as the only `load_classes` its characters ever see for the life of the process, the
same way this page's [complete program](#the-complete-program) does. Spells need no equivalent seam:
nothing resolves a spell by id off a character the way `Character.definition` resolves a class, so
`SpellCatalog.get`/`SpellCatalog.by_list` calls against your own extended catalog are all a caster
needs.

See [the class id index][classes-index] and [the spell id index][spells-index] for what ids the shipped
catalogs already use, and [the API reference](../reference/api/index.md) for every model and function
this page named.

## Where next

- [Building an adventure](../getting-started/building-an-adventure.md) — validating monster and
  equipment ids the same catalog-driven way, for the crawl layer instead of a character sheet.
- [Sessions, commands, and events](sessions-commands-events.md) — running a character, custom class or
  not, through an actual session once it exists.
- [The API reference](../reference/api/index.md) — the full model and function reference for everything
  named on this page.
