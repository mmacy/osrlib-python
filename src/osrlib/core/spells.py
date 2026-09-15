"""Spell memorization, casting, spell resolution, and turning undead.

Casting sits between two things you already have. On one side are the caster's spell slots: the
per-spell-level counts on the progression row of the character's
[`ClassDefinition`][osrlib.core.classes.ClassDefinition], filled for the day by
[`memorize_spells`][osrlib.core.spells.memorize_spells] and spent one copy at a time by
[`cast_spell`][osrlib.core.spells.cast_spell], this module's entry point. On the other side is the
effects engine: whatever a cast leaves running, a condition, a bundle of stat modifiers, a rolled
duration, attaches to the [`EffectsLedger`][osrlib.core.effects.EffectsLedger] in
[`osrlib.core.effects`][osrlib.core.effects], which ticks it and releases it when it ends. The
functions here take the spell catalog [`load_spells`][osrlib.data.load_spells] returns, mutate the
caster and the ledger, and hand you back events.

If you run a game session rather than the rules on their own, the crawl layer has already wrapped
this module and you call it instead: [`PrepareSpells`][osrlib.crawl.commands.PrepareSpells],
[`LearnSpell`][osrlib.crawl.commands.LearnSpell], [`CastSpell`][osrlib.crawl.commands.CastSpell],
and [`TurnUndead`][osrlib.crawl.commands.TurnUndead] call these functions once the session's own
gates have passed (a night's sleep before preparation, light to read by, the right session mode).
Call this module directly when you drive the rules yourself: everything here runs standalone with
no session, and every random draw comes from a named, seeded RNG stream you supply.

The OSE SRD's spell pages compile into a catalog of frozen
[`SpellTemplate`][osrlib.core.spells.SpellTemplate] models. A template has the page's presentation
data (duration, range, prose) alongside structured mechanics: one
[`SpellMode`][osrlib.core.spells.SpellMode] per castable usage, each naming its targeting, its
saving throw, and, for the automated subset, a [`SpellEffect`][osrlib.core.spells.SpellEffect] that
casting executes. Modes osrlib does not automate are marked `manual=True` and keep the SRD prose.
Casting one is a supported operation, the slot is consumed and the event is emitted, and your game
or narrator resolves what happens.

The daily flow is prepare, then cast. [`memorize_spells`][osrlib.core.spells.memorize_spells]
prepares a caster's list, and an arcane caster prepares from a spell book, which
[`add_spell_to_book`][osrlib.core.spells.add_spell_to_book] adds to. Then
[`validate_cast`][osrlib.core.spells.validate_cast] checks legality and
[`cast_spell`][osrlib.core.spells.cast_spell] consumes the memorized copy and resolves the mode.
[`cast_from_scroll`][osrlib.core.spells.cast_from_scroll] resolves an inscribed spell with no
memorized copy behind it, checked first by
[`validate_scroll_cast`][osrlib.core.spells.validate_scroll_cast], and
[`disrupt_casting`][osrlib.core.spells.disrupt_casting] takes a copy away from a caster whose
declared cast was broken before they could make it. Clerics also turn undead here:
[`validate_turn_undead`][osrlib.core.spells.validate_turn_undead], then
[`turn_undead`][osrlib.core.spells.turn_undead].

Casters are [`Character`][osrlib.core.character.Character] objects. Targets arrive duck-typed per
the combatant convention (see [`osrlib.core.combat`][osrlib.core.combat]) as characters,
[`MonsterInstance`][osrlib.core.monsters.MonsterInstance] objects, or location strings for effects
a game attaches to places rather than to creatures.

A reversible spell's reverse is entry data, not a separate catalog entry: it lives on its entry as
a [`ReversedForm`][osrlib.core.spells.ReversedForm]. The exception is a spell the SRD prints twice,
once as a cleric page and once as a magic-user page, where the two differ mechanically. Each such
pair compiles as two entries, the cleric one suffixed `_c` and the magic-user one `_mu`.

Every draw inside spell resolution comes from the
[`MAGIC_STREAM`][osrlib.core.spells.MAGIC_STREAM] stream: targeting dice, damage dice, touch-attack
rolls, cast-time forced saves, dispel survival rolls, and both turning rolls. Spell results
therefore replay independently of combat draws and combat draws of spell results. Draws made inside
an effect, such as a duration rolled at attach time or the charm re-save rolled on a tick, stay on
the [`EFFECTS_STREAM`][osrlib.core.effects.EFFECTS_STREAM] stream.

Typical usage:

```python
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.clock import GameClock
from osrlib.core.effects import EFFECTS_STREAM, EffectsLedger
from osrlib.core.monsters import IdAllocator
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.spells import MAGIC_STREAM, MemorizedSpell, cast_spell, caster_profile, memorize_spells
from osrlib.data import load_classes, load_spells

rules = Ruleset()
streams = RngStreams(master_seed=7)
catalog = load_spells()
definition = load_classes().get("cleric")

aldis = create_character(
    name="Aldis",
    class_id="cleric",
    alignment=Alignment.LAWFUL,
    ruleset=rules,
    stream=streams.get(CHARACTER_CREATION_STREAM),
).character
aldis.id = "pc-1"
aldis.level = 2  # a 2nd-level cleric has one first-level slot

# Prepare the day's list, then spend it healing the caster's own wounds.
prepared = memorize_spells(aldis, definition, catalog, [MemorizedSpell(spell_id="cure_light_wounds")])
assert prepared.accepted
aldis.current_hp = 1
result = cast_spell(
    aldis,
    catalog.get("cure_light_wounds"),
    "heal",
    profile=caster_profile(definition),
    targets=[aldis],
    ledger=EffectsLedger(),
    clock=GameClock(),
    allocator=IdAllocator(),
    registry={"pc-1": aldis},
    ruleset=rules,
    stream=streams.get(MAGIC_STREAM),
    effects_stream=streams.get(EFFECTS_STREAM),
)
assert result.affected_ids == ("pc-1",)
assert aldis.current_hp == aldis.max_hp  # healing never exceeds the normal maximum
assert aldis.memorized_spells == ()  # the cast spent the copy
```
"""

# Import direction: the data loaders import these models and character.py imports
# the loaders, so this module must never import character.py. Casting, memorization,
# and turning therefore take caster objects duck-typed, per the combatant
# convention, and character.py imports MemorizedSpell from here, never the reverse.

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from osrlib.core.abilities import AbilityScore
from osrlib.core.classes import ClassDefinition
from osrlib.core.clock import ROUNDS_PER_DAY, GameClock, TimeUnit
from osrlib.core.combat import (
    AttackContext,
    DamageSource,
    SaveCategory,
    TargetingMode,
    apply_healing,
    attack_roll,
    check_immunity,
    deal_damage,
    destroy_equipment,
    effective_hd,
    saving_throw,
    select_targets,
)
from osrlib.core.dice import parse, roll
from osrlib.core.effects import (
    Condition,
    EffectDefinition,
    EffectsLedger,
    ModifierSpec,
    has_condition,
    kill,
    remove_condition,
)
from osrlib.core.events import (
    DamageAbsorbedEvent,
    EffectTickedEvent,
    Event,
    HitPointsReportedEvent,
    MagicDispelledEvent,
    PreparedSpell,
    SpellBookUpdatedEvent,
    SpellCastEvent,
    SpellDisruptedEvent,
    SpellForgottenEvent,
    SpellsMemorizedEvent,
    TurningTypeOutcome,
    UndeadTurnedEvent,
)
from osrlib.core.monsters import MonsterTemplate
from osrlib.core.rng import RngStream, StreamName
from osrlib.core.ruleset import Ruleset
from osrlib.core.tables import turning_column
from osrlib.core.validation import Rejection

__all__ = [
    "CastContext",
    "CastResult",
    "CasterProfile",
    "DurationSpec",
    "EFFECT_KINDS",
    "MAGIC_STREAM",
    "MemorizationResult",
    "MemorizedSpell",
    "RangeSpec",
    "ReversedForm",
    "SaveSpec",
    "SpellBookResult",
    "SpellCatalog",
    "SpellEffect",
    "SpellMode",
    "SpellTemplate",
    "TargetingSpec",
    "TurnUndeadResult",
    "add_spell_to_book",
    "cast_from_scroll",
    "cast_spell",
    "caster_profile",
    "disrupt_casting",
    "forget_excess_memorized",
    "memorize_spells",
    "minimum_caster_level",
    "open_book_capacity",
    "pop_mirror_image",
    "turn_undead",
    "validate_cast",
    "validate_scroll_cast",
    "validate_turn_undead",
]

MAGIC_STREAM = StreamName.MAGIC
"""The name of the RNG stream every spell-resolution draw comes from.

Pass `streams.get(MAGIC_STREAM)` as the `stream` argument of
[`cast_spell`][osrlib.core.spells.cast_spell],
[`cast_from_scroll`][osrlib.core.spells.cast_from_scroll], and
[`turn_undead`][osrlib.core.spells.turn_undead], where `streams` is an
[`RngStreams`][osrlib.core.rng.RngStreams]. The draws on it are targeting dice, damage dice,
touch-attack rolls, cast-time forced saves, dispel survival rolls, and both turning rolls.

Keeping magic on its own stream is what lets a replay reproduce a spell result after the combat
draws around it have changed, and the reverse. Draws made inside an already-attached effect belong
to [`EFFECTS_STREAM`][osrlib.core.effects.EFFECTS_STREAM] instead, so pass that as `effects_stream`
rather than reusing this one.
"""

EFFECT_KINDS = frozenset(
    {"damage", "heal", "cure", "condition", "modifiers", "kill", "restore_life", "dispel", "attach_only"}
)
"""The effect kinds casting knows how to execute.

[`SpellEffect.kind`][osrlib.core.spells.SpellEffect] is validated against this set, so a spell you
author yourself has to resolve into one of these behaviors or be marked
[`manual`][osrlib.core.spells.SpellMode] and left to your game. The vocabulary is closed on purpose:
every kind is a branch of the resolution code, and a new kind is a library change, not data.

`damage` and `heal` roll dice against the selected targets, `cure` releases named conditions or
effect kinds, `condition` and `modifiers` attach to the effects ledger, `attach_only` attaches an
effect with no condition of its own (a light source, a ward, mirror images), `kill` applies a death
effect, `restore_life` is *raise dead*, and `dispel` is *dispel magic*.
"""


class DurationSpec(BaseModel):
    """How long a spell lasts, parsed out of the printed duration line.

    You read one off [`SpellTemplate.duration_spec`][osrlib.core.spells.SpellTemplate], or off a
    [`ReversedForm`][osrlib.core.spells.ReversedForm] when the reverse lasts a different length. You
    never build one during play. Casting reads it for you and turns it into the duration of the
    effect it attaches to the ledger, so you need this model only when you display a spell, sort or
    filter a list by how long its spells run, or author a spell of your own.

    The printed string is kept beside it on the template as `duration`, and it is the authority for
    anything you show a player: a duration the parser cannot make structure out of lands here as
    `kind="special"` with nothing else filled in, because the parser never fails on prose.

    Examples:
        ```python
        from osrlib.core.clock import TimeUnit
        from osrlib.data import load_spells

        catalog = load_spells()
        light = catalog.get("light_mu")
        assert light.duration == "6 turns +1 per level"  # the printed line
        spec = light.duration_spec
        assert (spec.kind, spec.unit, spec.amount, spec.per_level) == ("fixed", TimeUnit.TURN, 6, 1)
        # So a 3rd-level caster's light burns for 6 + 1 * 3 turns.
        assert catalog.get("cure_light_wounds").duration_spec.kind == "instant"
        ```
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["instant", "permanent", "concentration", "fixed", "special"]
    """Which of the five shapes this duration has.

    `instant` resolves and is over, `permanent` never ends, `concentration` lasts while the caster
    concentrates and is released by whoever is running the game, `fixed` is a length you can count
    in `unit`, and `special` means the printed line was prose the parser left alone.
    """

    unit: TimeUnit | None = None
    """The time unit a `fixed` duration counts in, as a [`TimeUnit`][osrlib.core.clock.TimeUnit].

    Rounds, turns, hours, or days. `None` on every other kind.
    """

    amount: int | None = None
    """How many `unit` a `fixed` duration lasts, before the per-level bonus.

    `None` when the length is rolled (`dice`) or is purely per-level.
    """

    dice: str | None = None
    """A dice expression rolled when the effect attaches, in place of a flat `amount`.

    *Confusion* uses `"1d6"`. The roll happens on the effects stream, not the magic stream.
    """

    per_level: int = 0
    """Extra `unit` per caster level, added to `amount` or folded into the `dice` modifier at cast.

    *Light* prints `6 turns +1 per level`, so amount 6 and per_level 1. A spell printed `1 turn per
    level` is amount `None` and per_level 1.
    """

    concentration_cap_unit: TimeUnit | None = None
    """The unit of the outer limit on a `concentration` duration, when the page prints one.

    A page reading `Concentration (up to 1 day)` sets this to days. `None` when concentration is
    open-ended.
    """

    concentration_cap_amount: int | None = None
    """How many `concentration_cap_unit` the limit on a `concentration` duration runs to."""

    @field_validator("dice")
    @classmethod
    def _dice_must_parse(cls, value: str | None) -> str | None:
        if value is not None:
            parse(value)
        return value

    @model_validator(mode="after")
    def _fixed_durations_carry_a_length(self) -> DurationSpec:
        if self.kind == "fixed":
            if self.unit is None:
                raise ValueError("a fixed duration needs a unit")
            if self.amount is None and self.dice is None and self.per_level == 0:
                raise ValueError("a fixed duration needs an amount, dice, or a per-level bonus")
        return self


class RangeSpec(BaseModel):
    """How far a spell reaches, parsed out of the printed range line.

    You read one off [`SpellTemplate.range_spec`][osrlib.core.spells.SpellTemplate], and you never
    build one during play. [`validate_cast`][osrlib.core.spells.validate_cast] reads it for you, but
    only when you tell it how far away the target is through
    [`CastContext.distance_feet`][osrlib.core.spells.CastContext]. osrlib has no map of its own, so
    with no distance asserted there is no range check. Read this model yourself when you draw a
    range indicator, filter a spell list by reach, or decide which targets to offer.

    The printed string is kept beside it on the template as `range` and is what you show a player.
    Ranges the parser cannot make structure out of, such as the presence forms, land as
    `kind="special"` with no distance.

    Examples:
        ```python
        from osrlib.data import load_spells

        catalog = load_spells()
        assert catalog.get("fire_ball").range == "240’"
        fire_ball = catalog.get("fire_ball").range_spec
        assert (fire_ball.kind, fire_ball.feet) == ("feet", 240)
        assert catalog.get("cure_light_wounds").range_spec.kind == "touch"
        ```
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["caster", "touch", "feet", "yards", "per_level", "special"]
    """Which shape the range has.

    `caster` affects the caster alone, `touch` reaches one creature in reach and allows the caster
    to be that creature, `feet` and `yards` are fixed distances, `per_level` grows with caster
    level, and `special` means the printed line was prose the parser left alone.
    """

    feet: int | None = None
    """The distance in feet, for the `feet`, `yards`, and `per_level` kinds.

    Yards are converted, so a range printed as 240 yards is 720 here. On a `per_level` range this is
    the base before the per-level bonus, and it is `None` when the printed range is purely per
    level. `None` on the other kinds.
    """

    per_level_feet: int | None = None
    """Extra feet of reach per caster level on a `per_level` range.

    A range printed `60' +10' per level` is `feet` 60 and `per_level_feet` 10, so a 5th-level caster
    reaches 110 feet.
    """


class TargetingSpec(BaseModel):
    """Who one castable usage of a spell can hit, and how many of them.

    You read one off [`SpellMode.targeting`][osrlib.core.spells.SpellMode] to know how many targets
    to collect before you call [`cast_spell`][osrlib.core.spells.cast_spell], which is the question
    a spell-targeting interface has to answer first. `mode` is the shared
    [`TargetingMode`][osrlib.core.combat.TargetingMode] that
    [`select_targets`][osrlib.core.combat.select_targets] understands, and the rest of the fields
    are the per-spell numbers that size and bound it.

    The targets you pass to casting are candidates, not the final list. Casting drops the ones the
    mode is not allowed to affect and then applies `mode` to the survivors, so an ineligible
    creature in the list costs nothing: it consumes no Hit Dice budget and no group slot. That is
    deliberate, and it is why an ineligible target is a resolution outcome rather than a rejection.
    A cast that finds nothing eligible returns a
    [`CastResult`][osrlib.core.spells.CastResult] with `no_effect` set, and the memorized copy is
    still spent.

    Examples:
        ```python
        from osrlib.core.combat import TargetingMode
        from osrlib.data import load_spells

        sleep = load_spells().get("sleep").mode("hd_budget").targeting
        assert sleep.mode is TargetingMode.HD_BUDGET
        assert (sleep.hd_budget_dice, sleep.hd_cap) == ("2d8", 4)

        fire_ball = load_spells().get("fire_ball").mode("damage").targeting
        assert fire_ball.mode is TargetingMode.AREA
        assert (fire_ball.shape, fire_ball.dimensions) == ("sphere", {"radius_feet": 20})
        ```
    """

    model_config = ConfigDict(frozen=True)

    mode: TargetingMode
    """Which targeting mode the usage takes.

    `self` takes no targets, `single` takes exactly one, `up_to_n` a bounded group, `hd_budget` as
    many creatures as a rolled pool of Hit Dice pays for, `area` everything you supply as covered by
    the shape, and `gaze` the gaze-attack form.
    """

    count: int | None = None
    """The fixed size of an `up_to_n` group, when the page prints a number rather than dice."""

    count_dice: str | None = None
    """The dice rolled at cast time to size an `up_to_n` group.

    *Hold person*'s group mode is `"1d4"` and *charm monster*'s is `"3d6"`. Rolled on the magic
    stream.
    """

    hd_budget_dice: str | None = None
    """The dice rolled to size a `hd_budget` pool, which is `"2d8"` for *sleep*.

    Creatures are affected cheapest first until the pool cannot pay for the next one, and the
    remainder is wasted rather than spent elsewhere.
    """

    hd_cap: int | None = None
    """The most Hit Dice a creature may have and still be eligible.

    *Sleep*'s group mode caps at 4 and *charm monster*'s at 3.
    """

    hd_min: int | None = None
    """The fewest Hit Dice a creature must have to be eligible.

    *Charm monster*'s single-target mode sets 4, which is the page's "more than 3 Hit Dice".
    """

    shape: str | None = None
    """The name of the area an `area` mode covers, such as `"sphere"`. `None` on every other mode."""

    dimensions: dict[str, int] = {}
    """The area's measurements in feet, keyed by name: *fire ball*'s sphere is `{"radius_feet": 20}`.

    Which creatures stand inside it is your game's question, not osrlib's. You decide who is caught
    and pass them as candidates.
    """

    @field_validator("count_dice", "hd_budget_dice")
    @classmethod
    def _dice_must_parse(cls, value: str | None) -> str | None:
        if value is not None:
            parse(value)
        return value


class SaveSpec(BaseModel):
    """The saving throw one castable usage of a spell allows its targets, and what passing it buys.

    You read one off [`SpellMode.save`][osrlib.core.spells.SpellMode], which is `None` when the mode
    allows no save at all. Casting rolls the save for you, on the magic stream, against the target's
    own save values. Read this model to tell a player what they are facing before they commit, or to
    show why a target came through unharmed.

    Spell saves are always rolled as magical, so a target's wisdom adjustment applies. A target
    immune to the spell's element passes without a roll, through the same save pipeline.

    Examples:
        ```python
        from osrlib.core.combat import SaveCategory
        from osrlib.data import load_spells

        catalog = load_spells()
        assert catalog.get("magic_missile").mode("missiles").save is None  # no save: it always hits
        fire_ball = catalog.get("fire_ball").mode("damage").save
        assert (fire_ball.category, fire_ball.on_save) == (SaveCategory.SPELLS, "half")
        assert catalog.get("hold_person_mu").mode("individual").save.modifier == -2
        ```
    """

    model_config = ConfigDict(frozen=True)

    category: SaveCategory
    """Which column of the saving-throw table the target rolls on.

    A [`SaveCategory`][osrlib.core.combat.SaveCategory].
    """

    modifier: int = 0
    """The adjustment applied to the target's roll, negative against the target.

    *Hold person*'s single-target mode is −2 and *feeblemind* is −4.
    """

    on_save: Literal["negates", "half"] = "negates"
    """What a passed save buys.

    `negates` means the target takes nothing at all. `half` means the target still takes half the
    damage, rounded down.
    """


class SpellEffect(BaseModel):
    """What one castable usage of a spell actually does to its targets.

    You read one off [`SpellMode.effect`][osrlib.core.spells.SpellMode]. Casting executes it for
    you, so you need this model to describe a spell in an interface, to decide whether a spell is
    worth casting on a given target, or to author a spell of your own.

    A mode marked [`manual`][osrlib.core.spells.SpellMode] has no effect at all: its `effect` is
    `None`, and casting it spends the copy, emits the event, and leaves the outcome to you. Every
    automated mode has one, and its `kind` is validated against
    [`EFFECT_KINDS`][osrlib.core.spells.EFFECT_KINDS] when the catalog loads.

    Examples:
        ```python
        from osrlib.core.effects import Condition
        from osrlib.data import load_spells

        catalog = load_spells()
        fire_ball = catalog.get("fire_ball").mode("damage").effect
        assert fire_ball.kind == "damage"
        assert fire_ball.params == {"dice_per_level": "1d6", "element": "fire"}  # 1d6 per caster level

        blind = catalog.get("light_mu").mode("blind").effect
        assert (blind.kind, blind.condition) == ("condition", Condition.BLIND)
        ```
    """

    model_config = ConfigDict(frozen=True)

    kind: str
    """Which resolution behavior runs, one of [`EFFECT_KINDS`][osrlib.core.spells.EFFECT_KINDS]."""

    condition: Condition | None = None
    """The [`Condition`][osrlib.core.effects.Condition] a `condition` effect attaches to each target.

    Blindness, charm, and the rest. `None` on every other kind.
    """

    cures_conditions: tuple[Condition, ...] = ()
    """The conditions a `cure` effect lifts. *Cure light wounds*' second usage lifts paralysis."""

    cures_effect_kinds: tuple[str, ...] = ()
    """The effect kinds a `cure` effect releases from the ledger by name.

    This is for spells that cancel a named magic rather than a condition: *light*'s third usage
    releases `"darkness"`.
    """

    modifiers: tuple[ModifierSpec, ...] = ()
    """The [`ModifierSpec`][osrlib.core.effects.ModifierSpec] bundle a `modifiers` effect grants.

    A bonus to armour class or to saves, for example. They ride the attached effect and lift when it
    ends.
    """

    params: dict[str, int | str | bool | tuple[int | str, ...]] = {}
    """The per-spell numbers the `kind` reads.

    Damage dice, per-level scaling, eligibility gates, revival windows, area radii. The keys differ
    by spell and by kind, so read them against the mode you are looking at rather than expecting a
    fixed shape.
    """

    @field_validator("kind")
    @classmethod
    def _kind_must_be_known(cls, value: str) -> str:
        if value not in EFFECT_KINDS:
            raise ValueError(f"effect kind must be one of {sorted(EFFECT_KINDS)}, got {value!r}")
        return value


class SpellMode(BaseModel):
    """One castable usage of a spell: what it targets, what it allows, and what it does.

    Many SRD spell pages print more than one numbered usage. *Cure light wounds* heals or lifts
    paralysis, and *light* illuminates, blinds, or cancels darkness. Each usage is a mode, and
    casting picks one by its `key`, which is the `mode` argument of
    [`cast_spell`][osrlib.core.spells.cast_spell]. Get the modes of a spell from
    [`SpellTemplate.modes`][osrlib.core.spells.SpellTemplate], or one by key from
    [`SpellTemplate.mode`][osrlib.core.spells.SpellTemplate.mode].

    A mode is either automated or manual, and the difference decides what casting does for you. An
    automated mode has both `targeting` and `effect`, and osrlib resolves it: it picks the targets,
    rolls the saves and the dice, applies the outcome, and attaches whatever runs on. A manual mode
    is marked `manual=True` and often has no targeting at all, because the SRD page gives it no
    structure to work from. Casting a manual mode is still a supported operation: the memorized copy
    is spent and the event is emitted with the manual marker and the mode's `prose`, and your game
    or narrator resolves what happens. Check `manual` before you promise a player an outcome.

    Examples:
        ```python
        from osrlib.data import load_spells

        light = load_spells().get("light_mu")
        assert [mode.key for mode in light.modes] == ["illuminate", "blind", "cancel"]
        assert not any(mode.manual for mode in light.modes)

        blind = light.mode("blind")
        assert blind.save is not None  # the target may save against being blinded
        assert blind.prose.startswith("Blinding a creature:")
        ```
    """

    model_config = ConfigDict(frozen=True)

    key: str = Field(min_length=1)
    """The mode's name, snake_case and unique within its form.

    This is what you pass as `mode` to [`cast_spell`][osrlib.core.spells.cast_spell] and
    [`validate_cast`][osrlib.core.spells.validate_cast]. A spell with a single usage still has one,
    such as *fire ball*'s `"damage"`.
    """

    targeting: TargetingSpec | None = None
    """Who the mode can hit and how many, as a [`TargetingSpec`][osrlib.core.spells.TargetingSpec].

    `None` only on manual modes.
    """

    save: SaveSpec | None = None
    """The saving throw the targets get, as a [`SaveSpec`][osrlib.core.spells.SaveSpec].

    `None` when the mode allows none.
    """

    effect: SpellEffect | None = None
    """What the mode does, as a [`SpellEffect`][osrlib.core.spells.SpellEffect].

    `None` only on manual modes.
    """

    manual: bool = False
    """True when osrlib does the bookkeeping and leaves the outcome to your game."""

    prose: str = ""
    """The SRD text for this usage.

    Show it to the player. For a manual mode it is all osrlib can tell you about the result.
    """

    @model_validator(mode="after")
    def _automated_modes_carry_structure(self) -> SpellMode:
        if not self.manual and (self.effect is None or self.targeting is None):
            raise ValueError(f"mode {self.key!r} is automated but lacks targeting or an effect")
        return self


class ReversedForm(BaseModel):
    """The reverse of a reversible spell, kept on the spell's own entry.

    *Cure light wounds* reverses into *cause light wounds*, *light* into *darkness*. The reverse is
    never a separate catalog entry, so you reach it through
    [`SpellTemplate.reversed_form`][osrlib.core.spells.SpellTemplate], which is `None` on a spell
    that does not reverse. To cast it, pass `reversed=True` to
    [`cast_spell`][osrlib.core.spells.cast_spell] with a `mode` key from this form's own `modes`,
    which are not the same keys as the normal form's.

    Who fixes the form, and when, differs by caster. An arcane caster chooses normal or reversed
    when memorizing, so the choice rides on the
    [`MemorizedSpell`][osrlib.core.spells.MemorizedSpell]. A divine caster memorizes the normal form
    and decides at the moment of casting, by speaking the words backwards, so any memorized copy
    will serve either way.

    Examples:
        ```python
        from osrlib.data import load_spells

        cure = load_spells().get("cure_light_wounds")
        assert cure.reversed_form.name == "Cause Light Wounds"
        assert [mode.key for mode in cure.modes] == ["heal", "cure_paralysis"]
        assert [mode.key for mode in cure.reversed_form.modes] == ["harm"]  # different keys
        assert load_spells().get("magic_missile").reversed_form is None  # not reversible
        ```
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    """The reverse's own name, as the SRD prints it, such as `"Cause Light Wounds"`.

    Show this rather than the entry's `name` when a cast is reversed.
    """

    prose: str = ""
    """The SRD text for the reversed version."""

    modes: tuple[SpellMode, ...] = Field(min_length=1)
    """One [`SpellMode`][osrlib.core.spells.SpellMode] per castable usage of the reverse.

    Each has its own key, and there is at least one.
    """

    duration: str | None = None
    """The reverse's printed duration, when the page prints a different one.

    `None` means the normal form's duration applies.
    """

    duration_spec: DurationSpec | None = None
    """The parsed form of `duration`, as a [`DurationSpec`][osrlib.core.spells.DurationSpec].

    `None` means the reverse lasts as long as the normal form, which is the common case. A page that
    prints a dual line such as `Instant / Permanent` splits it across the two forms.
    """


class SpellTemplate(BaseModel):
    """One spell, compiled from its SRD page: the reference data behind every cast.

    Get one from [`SpellCatalog.get`][osrlib.core.spells.SpellCatalog.get] by id, or a whole class
    list from [`SpellCatalog.by_list`][osrlib.core.spells.SpellCatalog.by_list]. The catalog itself
    comes from [`load_spells`][osrlib.data.load_spells]. Pass the template straight to
    [`cast_spell`][osrlib.core.spells.cast_spell],
    [`cast_from_scroll`][osrlib.core.spells.cast_from_scroll], or
    [`validate_cast`][osrlib.core.spells.validate_cast], and read its `modes` to know which mode
    keys those calls accept.

    A template is frozen and shared. Play never mutates one. What changes during play is the
    [`MemorizedSpell`][osrlib.core.spells.MemorizedSpell] copies a caster has prepared and the
    effects a cast leaves on the ledger, and both name a template by id rather than containing one.

    Examples:
        ```python
        from osrlib.data import load_spells

        fire_ball = load_spells().get("fire_ball")
        assert (fire_ball.name, fire_ball.spell_list, fire_ball.level) == ("Fire Ball", "magic_user", 3)
        assert [mode.key for mode in fire_ball.modes] == ["damage"]
        assert fire_ball.range == "240’" and fire_ball.duration == "Instant"
        ```
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    """The stable id you look the spell up by, slugified from its name: `"fire_ball"`.

    A handful of concepts appear in the SRD as a cleric page and a magic-user page that differ
    mechanically. Each such pair compiles as two entries, the cleric one suffixed `_c` and the
    magic-user one `_mu`: `"light_c"` and `"light_mu"`. For the ids the shipped catalog uses, see
    [the spell id index][spells-index].
    """

    name: str = Field(min_length=1)
    """The spell's printed name, such as `"Cure Light Wounds"`. Show this, not the id."""

    spell_list: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    """Which class list the spell belongs to.

    The shipped catalog has `"cleric"` and `"magic_user"`, and further lists are additive data. It
    must match the [`CasterProfile.spell_list`][osrlib.core.spells.CasterProfile] of any caster who
    memorizes or learns the spell.
    """

    level: int = Field(ge=1, le=6)
    """The spell's level, 1 to 6.

    This is what the caster's slots are counted by, not the caster's own level.
    """

    duration: str = Field(min_length=1)
    """The duration line as printed. Show this to a player."""

    duration_spec: DurationSpec
    """The parsed form of `duration`, as a [`DurationSpec`][osrlib.core.spells.DurationSpec].

    Casting reads it to set the length of what it attaches.
    """

    range: str = Field(min_length=1)
    """The range line as printed. Show this to a player."""

    range_spec: RangeSpec
    """The parsed form of `range`, as a [`RangeSpec`][osrlib.core.spells.RangeSpec]."""

    reversed_form: ReversedForm | None = None
    """The spell's reverse, as a [`ReversedForm`][osrlib.core.spells.ReversedForm].

    `None` when the spell does not reverse.
    """

    modes: tuple[SpellMode, ...] = Field(min_length=1)
    """One [`SpellMode`][osrlib.core.spells.SpellMode] per numbered usage on the page.

    In the page's order, and at least one. Their keys are the `mode` argument casting takes.
    """

    intro: str = ""
    """The page's opening text, above the numbered usages.

    On a multi-usage page it is the lead-in, such as `"This spell has two usages:"`. On a
    single-usage page it is the opening of the spell's description, and that one mode's `prose` is
    the full text, which usually runs longer.
    """

    conjured_monsters: tuple[MonsterTemplate, ...] = ()
    """Full monster stat blocks printed on the spell's own page rather than in the monster catalog.

    [`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate] models: *sticks to snakes* brings its
    own snake. Spawn them with [`spawn_monster`][osrlib.core.monsters.spawn_monster] when you
    resolve the spell.
    """

    conjured_monster_ids: tuple[str, ...] = ()
    """Ids of monsters the spell summons that already exist in the monster catalog.

    Look them up with [`load_monsters`][osrlib.data.load_monsters]. *Conjure elemental* names its
    four elementals this way.
    """

    overrides_applied: tuple[str, ...] = ()
    """The field paths a compiler correction touched when this entry was built from the SRD page.

    Empty for an entry the parser read cleanly. It is a provenance record, and nothing in play reads
    it.
    """

    @model_validator(mode="after")
    def _mode_keys_unique_per_form(self) -> SpellTemplate:
        forms: list[tuple[SpellMode, ...]] = [self.modes]
        if self.reversed_form is not None:
            forms.append(self.reversed_form.modes)
        for modes in forms:
            keys = [mode.key for mode in modes]
            if len(set(keys)) != len(keys):
                raise ValueError(f"{self.id} mode keys must be unique within a form")
        return self

    def mode(self, key: str, *, reversed: bool = False) -> SpellMode:
        """Return one castable usage of the spell by its key.

        Use this when you already know which usage you want, to read its targeting, its save, or its
        prose before you cast it. To offer a player a choice instead, iterate
        [`modes`][osrlib.core.spells.SpellTemplate] and show each mode's `prose`. The key you pass
        here is the same string casting takes as its `mode` argument.

        Args:
            key: The mode's key, such as `"damage"` or `"blind"`. Keys are unique within a form but
                the two forms are independent, so a reversed form may reuse a key or use different
                ones entirely.
            reversed: True to look on the spell's reversed form instead of its normal one.

        Returns:
            The [`SpellMode`][osrlib.core.spells.SpellMode].

        Raises:
            ValueError: If the spell has no reversed form and you asked for one, or if the form has
                no mode by that key. The message names the spell and the key.

        Examples:
            ```python
            from osrlib.data import load_spells

            cure = load_spells().get("cure_light_wounds")
            assert cure.mode("heal").effect.params["dice"] == "1d6+1"
            assert cure.mode("harm", reversed=True).effect.kind == "damage"
            try:
                cure.mode("harm")  # "harm" is a mode of the reverse, not of the normal form
            except ValueError as error:
                assert "no normal mode 'harm'" in str(error)
            ```
        """
        if reversed:
            if self.reversed_form is None:
                raise ValueError(f"{self.id} has no reversed form")
            modes = self.reversed_form.modes
        else:
            modes = self.modes
        for mode in modes:
            if mode.key == key:
                return mode
        form = "reversed" if reversed else "normal"
        raise ValueError(f"{self.id} has no {form} mode {key!r}")


class SpellCatalog(BaseModel):
    """Every spell osrlib knows, with lookup by id and by class list.

    Get the shipped catalog from [`load_spells`][osrlib.data.load_spells], which validates it once
    and caches it, so calling that loader again costs nothing and returns the same frozen object.
    Every function in this module that needs spell data takes either this catalog or one
    [`SpellTemplate`][osrlib.core.spells.SpellTemplate] out of it.

    Use [`get`][osrlib.core.spells.SpellCatalog.get] when you have an id, and
    [`by_list`][osrlib.core.spells.SpellCatalog.by_list] when you are building a menu of what a
    caster may choose. To know which list a given caster draws from, call
    [`caster_profile`][osrlib.core.spells.caster_profile] on their class definition.

    Examples:
        ```python
        from osrlib.data import load_spells

        catalog = load_spells()
        assert catalog.get("sleep").level == 1
        assert catalog.spells == tuple(sorted(catalog.spells, key=lambda spell: spell.id))
        ```
    """

    model_config = ConfigDict(frozen=True)

    spells: tuple[SpellTemplate, ...]
    """Every spell template the catalog holds.

    The shipped catalog is in id order. The model checks only that the ids are unique, so a catalog
    you build yourself keeps whatever order you gave it. Iterate this to search on something the two
    lookup methods do not cover, such as a name or an effect kind.
    """

    @model_validator(mode="after")
    def _ids_must_be_unique(self) -> SpellCatalog:
        ids = [template.id for template in self.spells]
        if len(set(ids)) != len(ids):
            raise ValueError("spell ids must be unique")
        return self

    def get(self, spell_id: str) -> SpellTemplate:
        """Return one spell by its id.

        This is how you turn a stored id back into castable data: a
        [`MemorizedSpell`][osrlib.core.spells.MemorizedSpell], a caster's `spell_book`, a scroll, and
        the events this module emits all name spells by id. Pass what you get back to
        [`cast_spell`][osrlib.core.spells.cast_spell].

        Args:
            spell_id: The spell's id, such as `"fire_ball"` or `"hold_person_c"`. Ids come from the
                catalog itself, and the full set in the shipped catalog is
                [the spell id index][spells-index].

        Returns:
            The [`SpellTemplate`][osrlib.core.spells.SpellTemplate].

        Raises:
            ValueError: If no spell has that id. The message names the id you asked for. An id that
                came from osrlib always resolves, so treat this as a signal that the id came from
                somewhere else, such as a save written against a different catalog.

        Examples:
            ```python
            from osrlib.data import load_spells

            catalog = load_spells()
            assert catalog.get("hold_person_c").name == "Hold Person"
            try:
                catalog.get("fireball")  # the id is "fire_ball"
            except ValueError as error:
                assert str(error) == "unknown spell id 'fireball'"
            ```
        """
        for template in self.spells:
            if template.id == spell_id:
                return template
        raise ValueError(f"unknown spell id {spell_id!r}")

    def by_list(self, spell_list: str, level: int | None = None) -> tuple[SpellTemplate, ...]:
        """Return the spells a class may draw on, optionally narrowed to one spell level.

        This is the menu a caster chooses from: what an arcane caster may add to their spell book
        with [`add_spell_to_book`][osrlib.core.spells.add_spell_to_book], and what a divine caster
        may prepare with [`memorize_spells`][osrlib.core.spells.memorize_spells]. Narrow by `level`
        to fill a particular slot, since a caster's slots are counted per spell level.

        Get the list id from [`caster_profile`][osrlib.core.spells.caster_profile] rather than
        hard-coding it, so a class you add with a list of its own works without a change here.

        Args:
            spell_list: The list id, such as `"cleric"` or `"magic_user"`. An id no spell uses
                returns nothing rather than raising.
            level: A spell level, 1 to 6, to filter by. `None` returns the whole list.

        Returns:
            The matching [`SpellTemplate`][osrlib.core.spells.SpellTemplate] models in the catalog's
            own order, which for the shipped catalog is id order. Empty when nothing matches.

        Examples:
            ```python
            from osrlib.core.spells import caster_profile
            from osrlib.data import load_classes, load_spells

            catalog = load_spells()
            profile = caster_profile(load_classes().get("cleric"))
            first_level = catalog.by_list(profile.spell_list, 1)
            assert [spell.id for spell in first_level][:3] == [
                "cure_light_wounds",
                "detect_evil_c",
                "detect_magic_c",
            ]
            assert len(catalog.by_list(profile.spell_list)) > len(first_level)
            assert catalog.by_list("druid") == ()  # no such list in the shipped catalog
            ```
        """
        return tuple(
            template
            for template in self.spells
            if template.spell_list == spell_list and (level is None or template.level == level)
        )


class MemorizedSpell(BaseModel):
    """One spell a caster has ready to cast: which spell, and in which form.

    You build these to hand to [`memorize_spells`][osrlib.core.spells.memorize_spells], one per slot
    you want filled, and you read them back off a character's `memorized_spells`, where they sit in
    the order they were prepared. That order matters: casting spends the first copy that matches,
    and a level drain forgets the newest first.

    A copy names a spell and fills a slot. What the spell can do comes from the template you get
    with [`SpellCatalog.get`][osrlib.core.spells.SpellCatalog.get].

    Examples:
        ```python
        from osrlib.core.spells import MemorizedSpell

        prepared = [MemorizedSpell(spell_id="magic_missile"), MemorizedSpell(spell_id="light_mu", reversed=True)]
        assert prepared[1].reversed  # this copy casts *darkness*, not *light*
        ```
    """

    model_config = ConfigDict(frozen=True)

    spell_id: str = Field(min_length=1)
    """The spell's id, from [`load_spells`][osrlib.data.load_spells].

    For the ids the shipped catalog uses, see [the spell id index][spells-index].
    """

    reversed: bool = False
    """True when this copy is prepared as the spell's reversed form.

    Only an arcane caster sets it, because the SRD has arcane casters choose the form when the spell
    is memorized. A divine caster memorizes the normal form and speaks it backwards at the moment of
    casting, so divine copies are always False and preparing one with True is rejected.
    """


class CasterProfile(BaseModel):
    """What kind of caster a class is, and which spell list it draws on.

    Get one from [`caster_profile`][osrlib.core.spells.caster_profile], which reads it off a class
    definition, and you never construct one. Several functions here take it as an argument rather
    than deriving it themselves, so a caller who already has the class definition does not pay for
    the lookup twice.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["divine", "arcane"]
    """`"divine"` for a class that prays for its spells, `"arcane"` for one that studies from a book.

    The difference shows up in three places: only an arcane caster has a book to grow with
    [`add_spell_to_book`][osrlib.core.spells.add_spell_to_book], only an arcane caster fixes a
    spell's reversed form at memorization, and only a divine caster may cast any memorized copy in
    either form.
    """

    spell_list: str
    """The list id the class draws on, such as `"cleric"` or `"magic_user"`.

    It has to match [`SpellTemplate.spell_list`][osrlib.core.spells.SpellTemplate] for the class to
    memorize or learn a spell, and it is what you pass to
    [`SpellCatalog.by_list`][osrlib.core.spells.SpellCatalog.by_list].
    """


def caster_profile(definition: ClassDefinition) -> CasterProfile | None:
    """Return how a class casts, or `None` if it casts nothing.

    Call this first when you are about to do anything magical with a character: it is how you find
    out whether the class casts at all, and it produces the `profile` argument that
    [`cast_spell`][osrlib.core.spells.cast_spell] and
    [`validate_cast`][osrlib.core.spells.validate_cast] take. A `None` answer is how you keep magic
    out of a fighter's interface, and the memorization and spell-book functions return a rejection
    rather than raising when they get one.

    The answer comes from the class's own ability tags, so a class you author yourself casts as soon
    as it has a `divine_magic` or `arcane_magic` tag naming a spell list. Nothing here is
    hard-coded to the shipped classes.

    Args:
        definition: The class, as a [`ClassDefinition`][osrlib.core.classes.ClassDefinition] from
            [`load_classes`][osrlib.data.load_classes]. A character names its class in `class_id`.

    Returns:
        The [`CasterProfile`][osrlib.core.spells.CasterProfile], or `None` when the class has
        neither casting tag.

    Examples:
        ```python
        from osrlib.core.spells import caster_profile
        from osrlib.data import load_classes

        classes = load_classes()
        magic_user = caster_profile(classes.get("magic_user"))
        assert (magic_user.kind, magic_user.spell_list) == ("arcane", "magic_user")
        assert caster_profile(classes.get("cleric")).kind == "divine"
        assert caster_profile(classes.get("fighter")) is None
        ```
    """
    for ability in getattr(definition, "abilities", ()):
        if ability.tag == "divine_magic":
            return CasterProfile(kind="divine", spell_list=str(ability.params["spell_list"]))
        if ability.tag == "arcane_magic":
            return CasterProfile(kind="arcane", spell_list=str(ability.params["spell_list"]))
    return None


def _entity_id(combatant: Any) -> str:
    identifier = getattr(combatant, "id", None)
    return identifier if identifier is not None else getattr(combatant, "name", "unknown")


def _target_ref(target: Any) -> str:
    """An explicit string target is a location ref. Anything else is an entity."""
    return target if isinstance(target, str) else _entity_id(target)


class MemorizationResult(BaseModel):
    """What came of a call to [`memorize_spells`][osrlib.core.spells.memorize_spells].

    One of the two fields is always empty. Either the preparation was legal, the caster's memorized
    list was replaced and `events` contains the record of it, or something was wrong, `rejections`
    says what, and nothing was changed at all. Check
    [`accepted`][osrlib.core.spells.MemorizationResult.accepted] rather than testing either tuple
    yourself.

    """

    model_config = ConfigDict(frozen=True)

    rejections: tuple[Rejection, ...] = ()
    """Why the preparation was refused, as [`Rejection`][osrlib.core.validation.Rejection] models.

    Each has a structured `code` and `params` you can turn into a message in your own words. Every
    problem found is reported, not just the first, so a player fixing a list sees all of it at once.
    Empty on success.
    """

    events: tuple[Event, ...] = ()
    """The [`SpellsMemorizedEvent`][osrlib.core.events.SpellsMemorizedEvent] naming what was prepared.

    Empty on a rejection.
    """

    @property
    def accepted(self) -> bool:
        """Whether the caster's memorized list was actually replaced.

        False means nothing changed and `rejections` says why.
        """
        return not self.rejections


def memorize_spells(
    caster: Any, definition: ClassDefinition, catalog: SpellCatalog, selections: Sequence[MemorizedSpell]
) -> MemorizationResult:
    """Fill a caster's spell slots for the day, replacing whatever was memorized before.

    This is the first half of the daily cycle, and a caster with an empty memorized list can cast
    nothing. The list you pass replaces the old one entirely. There is no partial top-up,
    because B/X has no such operation: a caster who spends one spell does not re-memorize that one
    slot, they prepare the whole list again at the next opportunity.

    What a caster may choose depends on how they cast, which
    [`caster_profile`][osrlib.core.spells.caster_profile] tells you. A divine caster chooses freely
    from the whole class list and never marks a copy reversed, because they decide the form when
    they cast it. An arcane caster chooses only from their own spell book, which
    [`add_spell_to_book`][osrlib.core.spells.add_spell_to_book] grows, and fixes each copy's form
    now. Either way the number of copies at each spell level must fit the slots on the caster's
    current progression row, and preparing the same spell more than once is allowed.

    The rules about when a caster may do this, once a day, after an uninterrupted night's sleep,
    over the course of an hour, are exploration procedure, and they live with
    [`PrepareSpells`][osrlib.crawl.commands.PrepareSpells] in the crawl layer. Nothing here checks
    them, so if you drive the rules yourself you decide when preparation is allowed.

    Args:
        caster: The caster preparing spells, a [`Character`][osrlib.core.character.Character] whose
            `memorized_spells` this replaces. Nothing is written when the call is rejected.
        definition: The caster's class, as a
            [`ClassDefinition`][osrlib.core.classes.ClassDefinition] from
            [`load_classes`][osrlib.data.load_classes]. Its progression row at the caster's level
            supplies the slot counts.
        catalog: The spell catalog, from [`load_spells`][osrlib.data.load_spells].
        selections: The [`MemorizedSpell`][osrlib.core.spells.MemorizedSpell] copies to prepare, in
            the order you want them held. The order decides what goes first: casting spends the
            first matching copy, and
            [`forget_excess_memorized`][osrlib.core.spells.forget_excess_memorized] drops the last
            ones first.

    Returns:
        A [`MemorizationResult`][osrlib.core.spells.MemorizationResult]: the memorized event on
        success, or every rejection found with the caster left untouched.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.core.spells import MemorizedSpell, memorize_spells
        from osrlib.data import load_classes, load_spells

        streams = RngStreams(master_seed=3)
        catalog = load_spells()
        definition = load_classes().get("magic_user")
        zelia = create_character(
            name="Zelia",
            class_id="magic_user",
            alignment=Alignment.NEUTRAL,
            ruleset=Ruleset(),
            stream=streams.get(CHARACTER_CREATION_STREAM),
            starting_spell_ids=["sleep"],
        ).character

        prepared = memorize_spells(zelia, definition, catalog, [MemorizedSpell(spell_id="sleep")])
        assert prepared.accepted
        assert zelia.memorized_spells == (MemorizedSpell(spell_id="sleep"),)

        # A 1st-level magic-user has one first-level slot, so asking for two is refused whole.
        refused = memorize_spells(
            zelia, definition, catalog, [MemorizedSpell(spell_id="sleep"), MemorizedSpell(spell_id="sleep")]
        )
        assert not refused.accepted
        assert [rejection.code for rejection in refused.rejections] == ["magic.memorize.slots_exceeded"]
        assert zelia.memorized_spells == (MemorizedSpell(spell_id="sleep"),)  # the old list stands
        ```
    """
    profile = caster_profile(definition)
    if profile is None:
        return MemorizationResult(
            rejections=(Rejection(code="magic.memorize.not_a_caster", params={"class": definition.id}),)
        )
    rejections: list[Rejection] = []
    counts: dict[int, int] = {}
    for selection in selections:
        try:
            template = catalog.get(selection.spell_id)
        except ValueError:
            rejections.append(Rejection(code="magic.memorize.unknown_spell", params={"spell": selection.spell_id}))
            continue
        if template.spell_list != profile.spell_list:
            rejections.append(
                Rejection(
                    code="magic.memorize.wrong_list",
                    params={"spell": selection.spell_id, "list": template.spell_list},
                )
            )
            continue
        if profile.kind == "divine" and selection.reversed:
            rejections.append(
                Rejection(code="magic.memorize.divine_reverses_at_cast", params={"spell": selection.spell_id})
            )
            continue
        if profile.kind == "arcane":
            if selection.spell_id not in getattr(caster, "spell_book", ()):
                rejections.append(Rejection(code="magic.memorize.not_in_book", params={"spell": selection.spell_id}))
                continue
            if selection.reversed and template.reversed_form is None:
                rejections.append(Rejection(code="magic.memorize.not_reversible", params={"spell": selection.spell_id}))
                continue
        counts[template.level] = counts.get(template.level, 0) + 1
    slots = definition.row(caster.level).spell_slots
    for spell_level, count in sorted(counts.items()):
        allowed = slots[spell_level - 1] if spell_level <= len(slots) else 0
        if count > allowed:
            rejections.append(
                Rejection(
                    code="magic.memorize.slots_exceeded",
                    params={"spell_level": spell_level, "slots": allowed, "selected": count},
                )
            )
    if rejections:
        return MemorizationResult(rejections=tuple(rejections))
    caster.memorized_spells = tuple(selections)
    event = SpellsMemorizedEvent(
        caster_id=_entity_id(caster),
        prepared=tuple(PreparedSpell(spell_id=copy.spell_id, reversed=copy.reversed) for copy in selections),
    )
    return MemorizationResult(events=(event,))


class SpellBookResult(BaseModel):
    """What came of a call to [`add_spell_to_book`][osrlib.core.spells.add_spell_to_book].

    One of the two fields is always empty: either the spell went into the book and `events` records
    it, or it did not and `rejections` says why, with the book unchanged. Check
    [`accepted`][osrlib.core.spells.SpellBookResult.accepted] rather than testing either tuple
    yourself.

    """

    model_config = ConfigDict(frozen=True)

    rejections: tuple[Rejection, ...] = ()
    """Why the addition was refused, as [`Rejection`][osrlib.core.validation.Rejection] models.

    Each has a structured `code` and `params`. At most one: the first problem found ends the call.
    Empty on success.
    """

    events: tuple[Event, ...] = ()
    """The [`SpellBookUpdatedEvent`][osrlib.core.events.SpellBookUpdatedEvent] naming the new spell.

    Empty on a rejection.
    """

    @property
    def accepted(self) -> bool:
        """Whether the spell actually went into the book.

        False means the book is unchanged and `rejections` says why.
        """
        return not self.rejections


def open_book_capacity(caster: Any, definition: ClassDefinition, catalog: SpellCatalog) -> tuple[int, ...]:
    """Return how many more spells fit in an arcane caster's book, at each spell level.

    Ask this before you offer a player a spell to learn, so the menu only shows levels with room in
    them. [`add_spell_to_book`][osrlib.core.spells.add_spell_to_book] checks the same thing and
    refuses when there is no room, so you can also skip this and read the rejection. The difference
    is that this tells you in advance, without a refused call to explain.

    A book has room, at each spell level, for as many spells as the caster could memorize at that
    level. Entry `i` of the answer is what is still free at spell level `i + 1`: the caster's current
    slot count there, minus the spells the book already contains there, never below zero.

    A book is a physical object and loses no pages when its owner loses levels, so a drained caster
    can end up with a book that is over capacity. The floor at zero is what handles that: such a
    level reads as no openings rather than as a negative number, and the caster adds nothing there
    until their levels come back.

    Args:
        caster: The caster, a [`Character`][osrlib.core.character.Character]. Its `spell_book` and
            `level` are read and nothing is written.
        definition: The caster's class, as a
            [`ClassDefinition`][osrlib.core.classes.ClassDefinition] from
            [`load_classes`][osrlib.data.load_classes].
        catalog: The spell catalog, from [`load_spells`][osrlib.data.load_spells], used to look up
            the level of each spell in the book.

    Returns:
        One count per spell level on the caster's progression row, lowest level first. An empty
        tuple for a class that keeps no spell book, which is every divine caster and every
        non-caster.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.core.spells import open_book_capacity
        from osrlib.data import load_classes, load_spells

        streams = RngStreams(master_seed=3)
        catalog = load_spells()
        classes = load_classes()
        definition = classes.get("magic_user")
        zelia = create_character(
            name="Zelia",
            class_id="magic_user",
            alignment=Alignment.NEUTRAL,
            ruleset=Ruleset(),
            stream=streams.get(CHARACTER_CREATION_STREAM),
            starting_spell_ids=["sleep"],
        ).character

        # One first-level slot, and the starting book already fills it.
        assert open_book_capacity(zelia, definition, catalog) == (0, 0, 0, 0, 0, 0)
        zelia.level = 3  # two first-level slots and one second-level
        assert open_book_capacity(zelia, definition, catalog) == (1, 1, 0, 0, 0, 0)
        assert open_book_capacity(zelia, classes.get("cleric"), catalog) == ()  # clerics keep no book
        ```
    """
    profile = caster_profile(definition)
    if profile is None or profile.kind != "arcane":
        return ()
    slots = definition.row(caster.level).spell_slots
    held: dict[int, int] = {}
    for held_id in caster.spell_book:
        spell_level = catalog.get(held_id).level
        held[spell_level] = held.get(spell_level, 0) + 1
    return tuple(max(0, capacity - held.get(index + 1, 0)) for index, capacity in enumerate(slots))


def add_spell_to_book(
    caster: Any, definition: ClassDefinition, catalog: SpellCatalog, spell_id: str
) -> SpellBookResult:
    """Write a spell into an arcane caster's spell book.

    A spell book is what an arcane caster may prepare from, so this is how such a caster's range
    grows: they gain a level, find a mentor, copy a captured book. Call
    [`open_book_capacity`][osrlib.core.spells.open_book_capacity] first if you want to show only the
    spells that will fit, and [`SpellCatalog.by_list`][osrlib.core.spells.SpellCatalog.by_list] to
    build the menu of what the class may learn at all. Once a spell is in the book,
    [`memorize_spells`][osrlib.core.spells.memorize_spells] can prepare it.

    The book has room, at each spell level, for as many spells as the caster could memorize at that
    level, and it never contains the same spell twice. It loses no pages on its own, so a caster who
    loses levels keeps every page and adds nothing more until their capacity catches up.

    osrlib models only the writing. What it costs and how long it takes, the mentor's week, the
    price of ink and a fresh book after a fire, belong to your game. Nothing here charges for it or spends
    game time. In a session, [`LearnSpell`][osrlib.crawl.commands.LearnSpell] wraps this call and
    also passes no time.

    Args:
        caster: The caster learning the spell, a [`Character`][osrlib.core.character.Character] with
            an arcane class. Its `spell_book` grows by one id. Nothing is written when the call is
            rejected.
        definition: The caster's class, as a
            [`ClassDefinition`][osrlib.core.classes.ClassDefinition] from
            [`load_classes`][osrlib.data.load_classes].
        catalog: The spell catalog, from [`load_spells`][osrlib.data.load_spells].
        spell_id: The spell to write in. For the ids the shipped catalog uses, see
            [the spell id index][spells-index].

    Returns:
        A [`SpellBookResult`][osrlib.core.spells.SpellBookResult]: the book-updated event on
        success, or the rejection with the book left untouched. A class with no book, an id no spell
        uses, a spell off the class's list, one the book already contains, and a level with no room
        are each their own rejection code.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.core.spells import add_spell_to_book
        from osrlib.data import load_classes, load_spells

        streams = RngStreams(master_seed=3)
        catalog = load_spells()
        definition = load_classes().get("magic_user")
        zelia = create_character(
            name="Zelia",
            class_id="magic_user",
            alignment=Alignment.NEUTRAL,
            ruleset=Ruleset(),
            stream=streams.get(CHARACTER_CREATION_STREAM),
            starting_spell_ids=["sleep"],
        ).character
        zelia.level = 3  # room for one more first-level spell

        learned = add_spell_to_book(zelia, definition, catalog, "magic_missile")
        assert learned.accepted
        assert zelia.spell_book == ("sleep", "magic_missile")

        refused = add_spell_to_book(zelia, definition, catalog, "cure_light_wounds")
        assert [rejection.code for rejection in refused.rejections] == ["magic.book.wrong_list"]
        ```
    """
    profile = caster_profile(definition)
    if profile is None or profile.kind != "arcane":
        return SpellBookResult(rejections=(Rejection(code="magic.book.not_arcane", params={"class": definition.id}),))
    try:
        template = catalog.get(spell_id)
    except ValueError:
        return SpellBookResult(rejections=(Rejection(code="magic.book.unknown_spell", params={"spell": spell_id}),))
    if template.spell_list != profile.spell_list:
        return SpellBookResult(
            rejections=(
                Rejection(code="magic.book.wrong_list", params={"spell": spell_id, "list": template.spell_list}),
            )
        )
    if spell_id in caster.spell_book:
        return SpellBookResult(rejections=(Rejection(code="magic.book.duplicate", params={"spell": spell_id}),))
    open_slots = open_book_capacity(caster, definition, catalog)
    if template.level > len(open_slots) or open_slots[template.level - 1] == 0:
        slots = definition.row(caster.level).spell_slots
        capacity = slots[template.level - 1] if template.level <= len(slots) else 0
        return SpellBookResult(
            rejections=(
                Rejection(
                    code="magic.book.capacity_exceeded",
                    params={"spell": spell_id, "spell_level": template.level, "capacity": capacity},
                ),
            )
        )
    caster.spell_book = (*caster.spell_book, spell_id)
    return SpellBookResult(events=(SpellBookUpdatedEvent(caster_id=_entity_id(caster), spell_id=spell_id),))


def forget_excess_memorized(caster: Any, definition: ClassDefinition, catalog: SpellCatalog) -> list[Event]:
    """Drop memorized copies the caster no longer has the slots for.

    Call this after anything that lowers a caster's level, which in B/X means energy drain. Their
    slot counts drop with the level, and the spells they had ready stop fitting. This call drops the
    surplus. Nothing calls it for you, so a game that drains a caster and skips it leaves them with
    spells they should not have.

    Nothing happens when the caster still has room, so the call is safe to make after any level
    change rather than only after a drop. It looks at each spell level on its own: a caster who lost
    a second-level slot forgets a second-level spell and keeps their first-level ones.

    Which copy goes is osrlib's choice. The tabletop rules do not say, and this drops the most
    recently prepared copies first, the ones at the end of the caster's `memorized_spells`, because
    that is decidable from the list itself and gives the same answer on every replay.

    Args:
        caster: The caster who lost levels, a [`Character`][osrlib.core.character.Character]. Its
            `memorized_spells` shrinks. A caster with nothing memorized is left alone.
        definition: The caster's class, as a
            [`ClassDefinition`][osrlib.core.classes.ClassDefinition] from
            [`load_classes`][osrlib.data.load_classes]. Its row at the caster's new level supplies
            the slot counts.
        catalog: The spell catalog, from [`load_spells`][osrlib.data.load_spells], used to look up
            the level of each memorized spell.

    Returns:
        One [`SpellForgottenEvent`][osrlib.core.events.SpellForgottenEvent] per copy dropped, newest
        first. Empty when everything still fits.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.core.spells import MemorizedSpell, forget_excess_memorized, memorize_spells
        from osrlib.data import load_classes, load_spells

        streams = RngStreams(master_seed=3)
        catalog = load_spells()
        definition = load_classes().get("cleric")
        aldis = create_character(
            name="Aldis",
            class_id="cleric",
            alignment=Alignment.LAWFUL,
            ruleset=Ruleset(),
            stream=streams.get(CHARACTER_CREATION_STREAM),
        ).character
        aldis.level = 3  # two first-level slots
        memorize_spells(
            aldis,
            definition,
            catalog,
            [MemorizedSpell(spell_id="cure_light_wounds"), MemorizedSpell(spell_id="light_c")],
        )

        aldis.level = 2  # drained back to one slot
        forgotten = forget_excess_memorized(aldis, definition, catalog)
        assert [event.spell_id for event in forgotten] == ["light_c"]  # the copy prepared last
        assert aldis.memorized_spells == (MemorizedSpell(spell_id="cure_light_wounds"),)
        assert forget_excess_memorized(aldis, definition, catalog) == []  # nothing left over
        ```
    """
    memorized = list(getattr(caster, "memorized_spells", ()))
    if not memorized:
        return []
    slots = definition.row(caster.level).spell_slots
    counts: dict[int, int] = {}
    levels: list[int] = []
    for copy in memorized:
        spell_level = catalog.get(copy.spell_id).level
        levels.append(spell_level)
        counts[spell_level] = counts.get(spell_level, 0) + 1
    overage = {
        spell_level: count - (slots[spell_level - 1] if spell_level <= len(slots) else 0)
        for spell_level, count in counts.items()
    }
    events: list[Event] = []
    dropped: set[int] = set()
    for index in range(len(memorized) - 1, -1, -1):
        spell_level = levels[index]
        if overage.get(spell_level, 0) > 0:
            overage[spell_level] -= 1
            dropped.add(index)
            copy = memorized[index]
            events.append(
                SpellForgottenEvent(caster_id=_entity_id(caster), spell_id=copy.spell_id, reversed=copy.reversed)
            )
    if dropped:
        caster.memorized_spells = tuple(copy for index, copy in enumerate(memorized) if index not in dropped)
    return events


class CastContext(BaseModel):
    """The facts about a cast's situation that only you know, asserted for the rules to use.

    Build one and pass it to [`validate_cast`][osrlib.core.spells.validate_cast],
    [`cast_spell`][osrlib.core.spells.cast_spell], or
    [`cast_from_scroll`][osrlib.core.spells.cast_from_scroll]. Every field is optional and the
    default context asserts nothing, which is the right thing to pass when none of these questions
    arises.

    These are the questions a referee at a table answers out loud, and osrlib cannot answer any of
    them from its own state. It has no map, so it does not know how far away the target is. It has
    no model of restraints, so it does not know the caster is tied up. It records no cause of death,
    so it does not know the corpse died of poison.

    A field you leave unset means the rule that reads it does not fire. Range goes unchecked if you
    assert no distance, and *raise dead* raises nobody if you assert no elapsed days. That is the
    trade: rather than guess, osrlib leaves a rule alone until you supply what it needs.

    """

    model_config = ConfigDict(frozen=True)

    in_combat: bool = False
    """True when the cast happens in a fight.

    A touch spell needs a melee attack roll in combat and lands without one outside it, so this
    decides whether the touch can miss.
    """

    distance_feet: int | None = None
    """How far the target is from the caster.

    Supplying it turns on the range check in validation, which rejects the cast when the distance is
    past what the spell's [`RangeSpec`][osrlib.core.spells.RangeSpec] reaches at the level being
    used. Leave it unset and no range check happens.
    """

    bound: bool = False
    """True when the caster is tied or held so that they cannot gesture. Casting is rejected."""

    gagged: bool = False
    """True when the caster cannot speak. Casting is rejected."""

    rounds_since_death: int | None = None
    """How many rounds ago the target died, for *neutralize poison*.

    That spell revives a character killed by poison within the last ten rounds. Setting this field
    is itself the assertion that poison was the cause, since osrlib records no cause of death. Leave
    it unset for a death by any other means.
    """

    days_since_death: int | None = None
    """How many days ago the target died, for *raise dead*.

    That spell reaches back four days per caster level above seventh. Leave it unset and nobody is
    raised.
    """

    strength_tiers: dict[str, str] = {}
    """Entity ids mapped to `"augmented"` or `"giant"`, for *web*.

    A stronger creature tears free sooner. Anyone you do not name tears free at normal strength. It
    is asserted here because osrlib has no effect that grants giant strength yet.
    """


_CANNOT_CAST_CONDITIONS = (
    Condition.DEAD,
    Condition.PETRIFIED,
    Condition.PARALYSED,
    Condition.ASLEEP,
    Condition.SILENCED,
    Condition.FEEBLEMINDED,
    Condition.WEAKENED,
)


def _int_param(params: Mapping[str, Any], key: str, default: int = 0) -> int:
    """Read an integer param: schema-validated data whose union the checker can't key by name."""
    return int(params.get(key, default))


def _mode_effect(mode: SpellMode) -> SpellEffect:
    """The mode's effect, model-validated as present on every automated mode."""
    if mode.effect is None:
        raise ValueError(f"mode {mode.key!r} is manual and carries no effect")
    return mode.effect


def _missile_count(effect: SpellEffect, caster_level: int) -> int:
    params = effect.params
    base = _int_param(params, "missiles_base", 1)
    step = _int_param(params, "missiles_step", 0)
    per = _int_param(params, "missiles_per_levels", 1)
    return base + step * ((caster_level - 1) // per)


def _max_range_feet(spell: SpellTemplate, caster_level: int) -> int | None:
    spec = spell.range_spec
    if spec.kind in ("feet", "yards"):
        return spec.feet
    if spec.kind == "per_level":
        return (spec.feet or 0) + (spec.per_level_feet or 0) * caster_level
    return None


def _memorized_index(caster: Any, spell: SpellTemplate, reversed: bool, profile: CasterProfile) -> int | None:
    """Return the index of the first matching memorized copy (lowest index).

    Divine casters match any copy of the spell, since the reversed flag is chosen freely at cast,
    whatever their spell list. Arcane casters fixed the form at memorization, so the flag must
    match.
    """
    for index, copy in enumerate(getattr(caster, "memorized_spells", ())):
        if copy.spell_id != spell.id:
            continue
        if profile.kind == "divine" or copy.reversed == reversed:
            return index
    return None


def validate_cast(
    caster: Any,
    spell: SpellTemplate,
    mode: str,
    *,
    profile: CasterProfile | None,
    reversed: bool = False,
    targets: Sequence[object] = (),
    context: CastContext | None = None,
    ledger: EffectsLedger | None = None,
) -> list[Rejection]:
    """Ask whether a cast is legal, without casting it.

    Call this to decide whether to offer a cast at all, to grey out a spell in a menu, or to explain
    to a player why they cannot do what they are trying to do. Then call
    [`cast_spell`][osrlib.core.spells.cast_spell], which runs these same checks and raises if any
    fail, so a cast you validated and then made cannot be refused.

    Nothing here draws from an RNG stream, changes the caster, or touches the ledger, so asking is
    free and leaves no trace. That is also why the answer stops short of one thing you might expect.
    Whether a target is the kind of creature the spell affects is settled during resolution, not
    here, because a validator that rejected *charm person* aimed at a disguised doppelganger would
    be a free way to find out what the doppelganger is. Such a cast is legal, resolves, spends the
    copy, and affects nobody.

    What it does check: that the caster is in a state to cast at all, which rules out dead,
    petrified, paralysed, asleep, silenced, feebleminded, and weakened casters as well as bound or
    gagged ones and any caster standing in their own anti-magic shell. That they have a memorized
    copy in the form they are asking for. That the spell has the form and the mode named. That the
    number of targets suits the mode, which for *magic missile* means exactly one target per missile
    the caster's level grants. And that the target is in range, but only if you asserted a distance.

    A cleric's holy symbol is not checked. The SRD tells clerics to carry one as a matter of their
    class, not as a condition on any procedure, so a game that wants the stricter reading checks
    inventory itself.

    Args:
        caster: The caster, a [`Character`][osrlib.core.character.Character]. Read, never written.
        spell: The [`SpellTemplate`][osrlib.core.spells.SpellTemplate] to cast, from
            [`SpellCatalog.get`][osrlib.core.spells.SpellCatalog.get].
        mode: Which usage of the spell, by its
            [`SpellMode.key`][osrlib.core.spells.SpellMode]. A key the chosen form does not have is
            a rejection, not an exception.
        profile: The caster's [`CasterProfile`][osrlib.core.spells.CasterProfile], from
            [`caster_profile`][osrlib.core.spells.caster_profile]. It decides how a memorized copy
            has to match: a divine caster's copy serves for either form, an arcane caster's only for
            the form it was prepared in. Pass `None` to skip the memorized-copy check entirely, for
            a scroll read, where the scroll is the copy.
        reversed: True to cast the spell's reversed form.
        targets: The candidate targets, per the combatant convention (see
            [`osrlib.core.combat`][osrlib.core.combat]):
            [`Character`][osrlib.core.character.Character] or
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] objects, or location strings
            for spells your game attaches to a place rather than a creature. Only the count is
            examined here.
        context: The [`CastContext`][osrlib.core.spells.CastContext] with what you assert about the
            situation. `None` asserts nothing.
        ledger: The [`EffectsLedger`][osrlib.core.effects.EffectsLedger], consulted for effects on
            the caster that block casting. Pass `None` and no such effect is found, so pass the
            ledger you play with.

    Returns:
        Every reason the cast is illegal, as [`Rejection`][osrlib.core.validation.Rejection] models
        with structured `code` and `params`. Empty when the cast may go ahead. Some checks stop the
        call at the first problem, so treat the list as the reasons found rather than every reason
        there is.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.core.spells import CastContext, MemorizedSpell, caster_profile, memorize_spells, validate_cast
        from osrlib.data import load_classes, load_monsters, load_spells

        streams = RngStreams(master_seed=5)
        catalog = load_spells()
        definition = load_classes().get("magic_user")
        zelia = create_character(
            name="Zelia",
            class_id="magic_user",
            alignment=Alignment.NEUTRAL,
            ruleset=Ruleset(),
            stream=streams.get(CHARACTER_CREATION_STREAM),
            starting_spell_ids=["magic_missile"],
        ).character
        memorize_spells(zelia, definition, catalog, [MemorizedSpell(spell_id="magic_missile")])
        template = load_monsters().get("goblin")
        goblin = spawn_monster(template, id="monster-0001", stream=streams.get(MONSTER_SPAWN_STREAM))

        profile = caster_profile(definition)
        missile = catalog.get("magic_missile")
        assert validate_cast(zelia, missile, "missiles", profile=profile, targets=[goblin]) == []

        # The same cast at a goblin 200 feet away, which is past the spell's 150 feet.
        refused = validate_cast(
            zelia,
            missile,
            "missiles",
            profile=profile,
            targets=[goblin],
            context=CastContext(distance_feet=200),
        )
        assert [rejection.code for rejection in refused] == ["magic.cast.out_of_range"]
        assert refused[0].params["range_feet"] == 150
        ```
    """
    context = context or CastContext()
    caster_id = _entity_id(caster)
    rejections: list[Rejection] = []
    for condition in _CANNOT_CAST_CONDITIONS:
        if has_condition(caster, condition):
            return [
                Rejection(
                    code="magic.cast.caster_incapacitated",
                    params={"caster": caster_id, "condition": condition.value},
                )
            ]
    if context.bound or context.gagged:
        return [
            Rejection(
                code="magic.cast.caster_restrained",
                params={"caster": caster_id, "restraint": "bound" if context.bound else "gagged"},
            )
        ]
    if ledger is not None and any(
        effect.definition.kind == "anti_magic_shell" for effect in ledger.active_on(caster_id)
    ):
        return [Rejection(code="magic.cast.anti_magic_shell", params={"caster": caster_id})]
    if reversed and spell.reversed_form is None:
        return [Rejection(code="magic.cast.not_reversible", params={"spell": spell.id})]
    try:
        spell_mode = spell.mode(mode, reversed=reversed)
    except ValueError:
        return [Rejection(code="magic.cast.unknown_mode", params={"spell": spell.id, "mode": mode})]
    if profile is not None and _memorized_index(caster, spell, reversed, profile) is None:
        rejections.append(Rejection(code="magic.cast.not_memorized", params={"spell": spell.id, "reversed": reversed}))
    targeting = spell_mode.targeting
    if targeting is not None:
        count = len(targets)
        if targeting.mode is TargetingMode.SELF and count != 0:
            rejections.append(
                Rejection(code="magic.cast.target_count", params={"mode": mode, "expected": 0, "supplied": count})
            )
        elif targeting.mode is TargetingMode.SINGLE and count != 1:
            rejections.append(
                Rejection(code="magic.cast.target_count", params={"mode": mode, "expected": 1, "supplied": count})
            )
        elif targeting.mode is TargetingMode.UP_TO_N and spell_mode.effect is not None:
            if "missiles_base" in spell_mode.effect.params:
                required = _missile_count(spell_mode.effect, caster.level)
                if count != required:
                    rejections.append(
                        Rejection(
                            code="magic.cast.target_count",
                            params={"mode": mode, "expected": required, "supplied": count},
                        )
                    )
            elif count < 1:
                rejections.append(
                    Rejection(code="magic.cast.target_count", params={"mode": mode, "expected": 1, "supplied": 0})
                )
        elif targeting.mode is TargetingMode.HD_BUDGET and count < 1:
            rejections.append(
                Rejection(code="magic.cast.target_count", params={"mode": mode, "expected": 1, "supplied": 0})
            )
    if context.distance_feet is not None:
        maximum = _max_range_feet(spell, caster.level)
        if maximum is not None and context.distance_feet > maximum:
            rejections.append(
                Rejection(
                    code="magic.cast.out_of_range",
                    params={"spell": spell.id, "distance_feet": context.distance_feet, "range_feet": maximum},
                )
            )
    return rejections


class CastResult(BaseModel):
    """What a cast did: which copy was spent, who it reached, and everything that happened.

    You get one back from [`cast_spell`][osrlib.core.spells.cast_spell] and from
    [`cast_from_scroll`][osrlib.core.spells.cast_from_scroll]. A result always describes a cast that
    happened. An illegal cast raises instead, so by the time you have one of these the copy is spent
    and the caster and the ledger have already been changed. Your work with it is to tell the player
    what happened: publish `events` to whatever consumes them, and read `manual`, `no_effect`, and
    `affected_ids` to know what to say.

    Two of the outcomes need more than a description of what the spell did. A `manual` mode means
    osrlib did the bookkeeping and stopped: nothing was resolved and `prose` is all it can tell you,
    so your game or narrator says what happened. `no_effect` means the cast resolved and reached
    nobody, because no candidate was eligible or every target saved. In both cases the copy is gone.
    Nothing is refunded once a cast resolves: a refund would tell the player something they had no
    way to know, such as that the creature they aimed at was immune.

    """

    model_config = ConfigDict(frozen=True)

    spell_id: str
    """The id of the spell that was cast.

    The same one you would pass to [`SpellCatalog.get`][osrlib.core.spells.SpellCatalog.get].
    """

    mode: str
    """The [`SpellMode.key`][osrlib.core.spells.SpellMode] that resolved."""

    reversed: bool = False
    """True when the reversed form was the one cast."""

    manual: bool = False
    """True when the mode was one osrlib does not resolve. Read `prose` and narrate it."""

    no_effect: bool = False
    """True when the cast resolved and changed nothing. The copy is still spent."""

    prose: str = ""
    """The SRD text of the mode that was cast, ready to show a player."""

    affected_ids: tuple[str, ...] = ()
    """The entity id of everything the cast reached, in the order it was reached, without repeats.

    A location-bound cast contains the location string you passed as a target instead of an entity
    id. Empty when `no_effect` or `manual` is set.
    """

    events: tuple[Event, ...] = ()
    """Everything that happened, in order.

    The [`SpellCastEvent`][osrlib.core.events.SpellCastEvent] first, then each saving throw, each
    wound, each effect attached. This is what your game publishes and what a replay reads.
    """


class _CastState:
    """Mutable bookkeeping shared by the interpreter's resolution branches."""

    def __init__(self) -> None:
        self.events: list[Event] = []
        self.affected: list[str] = []

    def affect(self, target: Any) -> None:
        ref = _target_ref(target)
        if ref not in self.affected:
            self.affected.append(ref)


def cast_spell(
    caster: Any,
    spell: SpellTemplate,
    mode: str,
    *,
    profile: CasterProfile,
    reversed: bool = False,
    targets: Sequence[object] = (),
    context: CastContext | None = None,
    ledger: EffectsLedger,
    clock: GameClock,
    allocator: Any,
    registry: dict[str, Any],
    ruleset: Ruleset,
    stream: RngStream,
    effects_stream: RngStream,
) -> CastResult:
    """Cast a memorized spell: spend the copy, resolve the mode, and hand back what happened.

    This is the module's entry point. Before you can call it the caster needs a prepared copy, which
    [`memorize_spells`][osrlib.core.spells.memorize_spells] gives them, and you need the profile
    that [`caster_profile`][osrlib.core.spells.caster_profile] returns. After it, publish the
    [`CastResult`][osrlib.core.spells.CastResult]'s events and keep the ledger you passed, because
    anything the spell left running now lives there and wants ticking by
    [`osrlib.core.effects`][osrlib.core.effects]. To cast an inscribed spell with no memorized copy
    behind it, use [`cast_from_scroll`][osrlib.core.spells.cast_from_scroll] instead.

    The copy is spent whatever comes of the cast. It is spent when every target saves, when nothing
    was eligible, and when a touch attack misses, because B/X has no rule for holding a spell back
    once it is cast. Ask [`validate_cast`][osrlib.core.spells.validate_cast] first if you want a free
    answer: an illegal cast raises here rather than returning a refusal, because you had a way to
    ask.

    Which copy goes depends on how the caster casts. A divine caster spends any copy of the spell and
    picks the form as they cast. An arcane caster fixed the form when they prepared it, so the copy
    has to match what you are asking for. Either way the first matching copy in the caster's list is
    the one that goes.

    Casting anything breaks the caster's own invisibility, before the new spell resolves, the same
    way attacking does.

    Two RNG streams go in, and they stay separate so that each replays on its own. Everything the
    cast itself rolls, targeting dice, damage dice, the touch attack, the saves it forces, comes from
    `stream`. Everything an attached effect rolls, such as a duration rolled as it attaches, comes
    from `effects_stream`.

    Args:
        caster: The caster, a [`Character`][osrlib.core.character.Character] with a matching
            memorized copy. Its `memorized_spells` loses that copy.
        spell: The [`SpellTemplate`][osrlib.core.spells.SpellTemplate] to cast, from
            [`SpellCatalog.get`][osrlib.core.spells.SpellCatalog.get].
        mode: Which usage of the spell, by its [`SpellMode.key`][osrlib.core.spells.SpellMode].
        profile: The caster's [`CasterProfile`][osrlib.core.spells.CasterProfile], from
            [`caster_profile`][osrlib.core.spells.caster_profile].
        reversed: True to cast the spell's reversed form.
        targets: The candidate targets in your own order, per the combatant convention (see
            [`osrlib.core.combat`][osrlib.core.combat]):
            [`Character`][osrlib.core.character.Character] or
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] objects, or location strings
            for spells your game attaches to a place. Casting drops the ineligible ones and then
            applies the mode's targeting to the rest, so passing more candidates than the spell can
            take is normal for an area or group mode.
        context: The [`CastContext`][osrlib.core.spells.CastContext] with what you assert about the
            situation. `None` asserts nothing.
        ledger: The [`EffectsLedger`][osrlib.core.effects.EffectsLedger] that ongoing effects attach
            to. Pass the one your game keeps, not a fresh one, or the spell's duration is lost.
        clock: The [`GameClock`][osrlib.core.clock.GameClock], read to stamp when attached effects
            began and when they end.
        allocator: The [`IdAllocator`][osrlib.core.monsters.IdAllocator] that names each attached
            effect. Pass the one your game keeps, so ids stay unique across the session.
        registry: Every live combatant by entity id, as
            [`Character`][osrlib.core.character.Character] and
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] objects. Resolution reaches
            through it to change creatures the targets alone do not name, such as when an effect
            lifts.
        ruleset: The [`Ruleset`][osrlib.core.ruleset.Ruleset] in play, read for the optional rules
            that touch attack rolls and damage.
        stream: The [`RngStream`][osrlib.core.rng.RngStream] every draw the cast makes comes from,
            conventionally [`MAGIC_STREAM`][osrlib.core.spells.MAGIC_STREAM].
        effects_stream: The stream that attaching effects draw from, conventionally
            [`EFFECTS_STREAM`][osrlib.core.effects.EFFECTS_STREAM].

    Returns:
        The [`CastResult`][osrlib.core.spells.CastResult]: what was reached, and every event, in
        order.

    Raises:
        ValueError: If the cast is illegal. Ask
            [`validate_cast`][osrlib.core.spells.validate_cast] first. Reaching this means the cast
            was not legal to offer.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.clock import GameClock
        from osrlib.core.effects import EFFECTS_STREAM, EffectsLedger
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.core.spells import MAGIC_STREAM, MemorizedSpell, cast_spell, caster_profile, memorize_spells
        from osrlib.data import load_classes, load_monsters, load_spells

        rules = Ruleset()
        streams = RngStreams(master_seed=11)
        catalog = load_spells()
        definition = load_classes().get("magic_user")

        # A 1st-level magic-user with *magic missile* in her book, memorized for the day.
        created = create_character(
            name="Zelia",
            class_id="magic_user",
            alignment=Alignment.NEUTRAL,
            ruleset=rules,
            stream=streams.get(CHARACTER_CREATION_STREAM),
            starting_spell_ids=["magic_missile"],
        )
        zelia = created.character
        prepared = memorize_spells(zelia, definition, catalog, [MemorizedSpell(spell_id="magic_missile")])
        assert prepared.accepted

        # One goblin target. The registry maps entity ids to the live objects.
        template = load_monsters().get("goblin")
        goblin = spawn_monster(template, id="monster-0001", stream=streams.get(MONSTER_SPAWN_STREAM))
        outcome = cast_spell(
            zelia,
            catalog.get("magic_missile"),
            "missiles",
            profile=caster_profile(definition),
            targets=[goblin],
            ledger=EffectsLedger(),
            clock=GameClock(),
            allocator=IdAllocator(),
            registry={"monster-0001": goblin},
            ruleset=rules,
            stream=streams.get(MAGIC_STREAM),
            effects_stream=streams.get(EFFECTS_STREAM),
        )
        assert outcome.spell_id == "magic_missile" and not outcome.no_effect
        assert outcome.affected_ids == ("monster-0001",)
        assert zelia.memorized_spells == ()  # the cast spent the memorized copy
        assert goblin.max_hp - goblin.current_hp == 3  # 1d6+1 missile damage, stable under this seed
        ```
    """
    context = context or CastContext()
    rejections = validate_cast(
        caster, spell, mode, profile=profile, reversed=reversed, targets=targets, context=context, ledger=ledger
    )
    if rejections:
        raise ValueError(f"illegal cast: {[rejection.code for rejection in rejections]}")
    spell_mode = spell.mode(mode, reversed=reversed)
    index = _memorized_index(caster, spell, reversed, profile)
    if index is None:
        raise ValueError(f"{spell.id} is not memorized in the requested form")
    copies = list(caster.memorized_spells)
    del copies[index]
    caster.memorized_spells = tuple(copies)
    return _perform_cast(
        caster,
        spell,
        spell_mode,
        reversed,
        targets,
        context=context,
        ledger=ledger,
        clock=clock,
        allocator=allocator,
        registry=registry,
        ruleset=ruleset,
        stream=stream,
        effects_stream=effects_stream,
    )


def _perform_cast(
    caster: Any,
    spell: SpellTemplate,
    spell_mode: SpellMode,
    reversed: bool,
    targets: Sequence[object],
    *,
    context: CastContext,
    ledger: EffectsLedger,
    clock: GameClock,
    allocator: Any,
    registry: dict[str, Any],
    ruleset: Ruleset,
    stream: RngStream,
    effects_stream: RngStream,
) -> CastResult:
    """Resolve a validated cast whose cost is already paid, shared by memory and scroll."""
    caster_id = _entity_id(caster)
    state = _CastState()
    # Casting breaks the caster's own invisibility, before the new spell resolves.
    for effect in list(ledger.active_on(caster_id, "invisibility")):
        state.events.extend(ledger.release(effect.effect_id, registry))

    if spell_mode.manual:
        event = SpellCastEvent(
            code="magic.cast.cast",
            caster_id=caster_id,
            spell_id=spell.id,
            mode=spell_mode.key,
            reversed=reversed,
            target_ids=tuple(_target_ref(target) for target in targets),
            manual=True,
        )
        return CastResult(
            spell_id=spell.id,
            mode=spell_mode.key,
            reversed=reversed,
            manual=True,
            prose=spell_mode.prose,
            events=(event, *state.events),
        )

    selected, selection_events = _select_cast_targets(caster, spell_mode, targets, stream)
    state.events.extend(selection_events)
    _resolve_effect(
        caster,
        spell,
        spell_mode,
        reversed,
        selected,
        state,
        context=context,
        ledger=ledger,
        clock=clock,
        allocator=allocator,
        registry=registry,
        ruleset=ruleset,
        stream=stream,
        effects_stream=effects_stream,
    )
    code = "magic.cast.cast" if state.affected else "magic.cast.no_effect"
    event = SpellCastEvent(
        code=code,
        caster_id=caster_id,
        spell_id=spell.id,
        mode=spell_mode.key,
        reversed=reversed,
        target_ids=tuple(_target_ref(target) for target in targets),
    )
    return CastResult(
        spell_id=spell.id,
        mode=spell_mode.key,
        reversed=reversed,
        no_effect=not state.affected,
        prose=spell_mode.prose,
        affected_ids=tuple(state.affected),
        events=(event, *state.events),
    )


class _ScrollReader:
    """A duck-typed caster proxy: the reader's body at the scroll's caster level.

    Attribute reads and writes pass through to the reader, so conditions and modifiers land on the
    real character. Only `level` is overridden, because a scroll spell resolves at the minimum class
    level able to cast it.
    """

    __slots__ = ("_level", "_reader")

    def __init__(self, reader: Any, level: int) -> None:
        object.__setattr__(self, "_reader", reader)
        object.__setattr__(self, "_level", level)

    @property
    def level(self) -> int:
        return object.__getattribute__(self, "_level")

    def __getattr__(self, name: str):
        return getattr(object.__getattribute__(self, "_reader"), name)

    def __setattr__(self, name: str, value: object) -> None:
        setattr(object.__getattribute__(self, "_reader"), name, value)


def minimum_caster_level(spell: SpellTemplate) -> int:
    """Return the lowest class level that could cast a spell at all.

    This is the caster level a scroll's resolution runs at, so
    [`cast_from_scroll`][osrlib.core.spells.cast_from_scroll] calls it for you and you rarely need
    it yourself. Call it directly when you want to show what a scroll will do before anyone reads
    it, since caster level is what scales a spell's damage and duration, and when you want to
    validate a scroll read ahead of time, because
    [`cast_from_scroll`][osrlib.core.spells.cast_from_scroll] checks legality at this level too.

    The answer is the lowest level at which any class drawing on the spell's list first has a slot
    of that spell's level, read off the compiled class progressions. So the answer moves if you add
    a class whose progression reaches that spell level sooner.

    The tabletop rules do not say what level a scroll's spell was inscribed at. osrlib reads a
    scroll at the lowest level that could cast it, so a scroll the party finds never outdoes the
    caster who found it. A game that wants scrolls to have their own caster level resolves them with
    [`cast_spell`][osrlib.core.spells.cast_spell] against a caster of that level instead.

    Args:
        spell: The [`SpellTemplate`][osrlib.core.spells.SpellTemplate] to look up.

    Returns:
        The caster level, 1 or higher.

    Raises:
        ValueError: If no class drawing on the spell's list ever gains a slot of the spell's level,
            which means the spell's level is higher than any of those classes ever prepares.

    Examples:
        ```python
        from osrlib.core.spells import minimum_caster_level
        from osrlib.data import load_spells

        catalog = load_spells()
        assert minimum_caster_level(catalog.get("magic_missile")) == 1
        assert minimum_caster_level(catalog.get("fire_ball")) == 5  # a fire ball scroll burns for 5d6
        ```
    """
    from osrlib.data import load_classes

    best: int | None = None
    for definition in load_classes().classes:
        profile = caster_profile(definition)
        if profile is None or profile.spell_list != spell.spell_list:
            continue
        for row in definition.progression:
            if len(row.spell_slots) >= spell.level and row.spell_slots[spell.level - 1] > 0:
                best = row.level if best is None else min(best, row.level)
                break
    if best is None:
        raise ValueError(f"no class casts level-{spell.level} {spell.spell_list} spells")
    return best


def validate_scroll_cast(
    reader: Any,
    spell: SpellTemplate,
    mode: str,
    *,
    reversed: bool = False,
    targets: Sequence[object] = (),
    context: CastContext | None = None,
    ledger: EffectsLedger | None = None,
) -> list[Rejection]:
    """Ask whether a scroll read is legal, without reading it.

    This is [`validate_cast`][osrlib.core.spells.validate_cast] for a spell coming off a page, and
    it is the check [`cast_from_scroll`][osrlib.core.spells.cast_from_scroll] makes before it
    resolves anything. Call it to decide whether to offer a read, and call it before any read you
    are about to make. `cast_from_scroll` raises on an illegal read, and osrlib has no model of the
    scroll, so your own inventory is what decides whether the refused attempt still used it up.

    The difference from `validate_cast` is the caster the question is asked about. A scroll resolves
    at the lowest class level able to cast the inscribed spell, from
    [`minimum_caster_level`][osrlib.core.spells.minimum_caster_level], whatever level the reader is,
    so this builds that caster and asks about them. The two checks that scale with caster level
    therefore follow the scroll: how many targets a mode demands, which is why a 6th-level reader of
    a *magic missile* scroll supplies one target and is refused three, and how far a per-level range
    reaches. The memorized-copy check is skipped, since the scroll is the copy.

    Two things it does not answer, because they depend on the game around the spell rather than on
    the spell: whether this reader may read this scroll at all, which is where a thief's scroll-use
    ability and the arcane and divine divide come in, and whether there is light to read by. The
    crawl layer, the [`osrlib.crawl`][osrlib.crawl] package that runs a session, checks both of
    those.

    Args:
        reader: The character reading the scroll, a
            [`Character`][osrlib.core.character.Character]. Read, never written.
        spell: The inscribed [`SpellTemplate`][osrlib.core.spells.SpellTemplate], from
            [`SpellCatalog.get`][osrlib.core.spells.SpellCatalog.get].
        mode: Which usage of the spell, by its [`SpellMode.key`][osrlib.core.spells.SpellMode]. A key
            the chosen form does not have is a rejection, not an exception.
        reversed: True to ask about the spell's reversed form.
        targets: The candidate targets, per the combatant convention (see
            [`osrlib.core.combat`][osrlib.core.combat]). Only the count is examined. `None` means no
            targets, the same as an empty sequence.
        context: The [`CastContext`][osrlib.core.spells.CastContext] with what you assert about the
            situation. `None` asserts nothing.
        ledger: The [`EffectsLedger`][osrlib.core.effects.EffectsLedger], consulted for effects on the
            reader that block casting. Pass `None` and no such effect is found, so pass the ledger you
            play with.

    Returns:
        Every reason the read is illegal, as [`Rejection`][osrlib.core.validation.Rejection] models
        with structured `code` and `params`. Empty when the read may go ahead, which means
        `cast_from_scroll` with the same arguments will not raise.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.core.spells import validate_scroll_cast
        from osrlib.data import load_monsters, load_spells

        streams = RngStreams(master_seed=5)
        zelia = create_character(
            name="Zelia",
            class_id="magic_user",
            alignment=Alignment.NEUTRAL,
            ruleset=Ruleset(),
            stream=streams.get(CHARACTER_CREATION_STREAM),
            starting_spell_ids=["read_magic"],
        ).character
        zelia.level = 6  # three missiles from memory, one off a 1st-level scroll
        template = load_monsters().get("goblin")
        spawns = streams.get(MONSTER_SPAWN_STREAM)
        goblins = [spawn_monster(template, id=f"monster-000{number}", stream=spawns) for number in (1, 2, 3)]
        missile = load_spells().get("magic_missile")

        refused = validate_scroll_cast(zelia, missile, "missiles", targets=goblins)
        assert [rejection.code for rejection in refused] == ["magic.cast.target_count"]
        assert refused[0].params["expected"] == 1  # the scroll's level, not the reader's
        assert validate_scroll_cast(zelia, missile, "missiles", targets=goblins[:1]) == []
        ```
    """
    return validate_cast(
        _ScrollReader(reader, minimum_caster_level(spell)),
        spell,
        mode,
        profile=None,
        reversed=reversed,
        targets=targets,
        context=context,
        ledger=ledger,
    )


def cast_from_scroll(
    reader: Any,
    spell: SpellTemplate,
    mode: str,
    *,
    reversed: bool = False,
    targets: Sequence[object] = (),
    context: CastContext | None = None,
    ledger: EffectsLedger,
    clock: GameClock,
    allocator: Any,
    registry: dict[str, Any],
    ruleset: Ruleset,
    stream: RngStream,
    effects_stream: RngStream,
) -> CastResult:
    """Cast a spell off a scroll, with the scroll standing in for the memorized copy.

    Use this rather than [`cast_spell`][osrlib.core.spells.cast_spell] whenever the spell comes off a
    page instead of out of the reader's memory. The reader needs no memorized copy, no slot, and no
    ability to cast the spell of their own. Everything else is the same: the same legality checks,
    the same targeting, the same resolution, the same
    [`CastResult`][osrlib.core.spells.CastResult].

    The scroll itself is your responsibility. Reading one uses it up, since the words disappear from
    the page, and osrlib has no model of the scroll, so mark the inscribed spell spent in your own
    inventory after this returns. Two other checks are yours as well, or the crawl layer's if you
    use it: whether this reader may read this scroll at all, which is where a thief's scroll-use
    ability and the arcane and divine divide come in, and whether there is light to read by.

    The read runs at one caster level throughout: the level
    [`minimum_caster_level`][osrlib.core.spells.minimum_caster_level] gives for the spell, whatever
    level the reader is. Legality and resolution both use it, so a *fire ball* off a scroll always
    burns for 5d6, a per-level duration is figured from that same level, and the two legality checks
    that scale with caster level follow the scroll rather than the reader:

    - How many targets a mode demands. *Magic missile* wants one target per missile, and a scroll's
      level grants one, so even a 6th-level reader supplies one target and is refused three.
    - How far the spell reaches, for a spell whose printed range grows per level. That reach is
      figured from the scroll's level when you assert a `distance_feet` in the
      [`CastContext`][osrlib.core.spells.CastContext].

    A condition, a modifier, or a wound the spell puts on its caster lands on the reader, the same
    as it would from a spell they had memorized.

    Args:
        reader: The character reading the scroll, a
            [`Character`][osrlib.core.character.Character]. Nothing is taken from their memorized
            spells.
        spell: The inscribed [`SpellTemplate`][osrlib.core.spells.SpellTemplate], from
            [`SpellCatalog.get`][osrlib.core.spells.SpellCatalog.get].
        mode: Which usage of the spell, by its [`SpellMode.key`][osrlib.core.spells.SpellMode].
        reversed: True to cast the spell's reversed form.
        targets: The candidate targets in your own order, per the combatant convention (see
            [`osrlib.core.combat`][osrlib.core.combat]):
            [`Character`][osrlib.core.character.Character] or
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] objects, or location strings
            for spells your game attaches to a place.
        context: The [`CastContext`][osrlib.core.spells.CastContext] with what you assert about the
            situation. `None` asserts nothing.
        ledger: The [`EffectsLedger`][osrlib.core.effects.EffectsLedger] that ongoing effects attach
            to. Pass the one your game keeps.
        clock: The [`GameClock`][osrlib.core.clock.GameClock], read to stamp attached effects.
        allocator: The [`IdAllocator`][osrlib.core.monsters.IdAllocator] that names each attached
            effect.
        registry: Every live combatant by entity id, as
            [`Character`][osrlib.core.character.Character] and
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] objects.
        ruleset: The [`Ruleset`][osrlib.core.ruleset.Ruleset] in play.
        stream: The [`RngStream`][osrlib.core.rng.RngStream] the cast's own draws come from,
            conventionally [`MAGIC_STREAM`][osrlib.core.spells.MAGIC_STREAM].
        effects_stream: The stream that attaching effects draw from, conventionally
            [`EFFECTS_STREAM`][osrlib.core.effects.EFFECTS_STREAM].

    Returns:
        The [`CastResult`][osrlib.core.spells.CastResult]: what was reached, and every event, in
        order.

    Raises:
        ValueError: If the read is illegal. Nothing is drawn or changed before the refusal. Ask
            [`validate_scroll_cast`][osrlib.core.spells.validate_scroll_cast] first to get the
            reasons instead of the exception. It is the check this call makes, asked the same way,
            so an empty answer from it means this call will not raise.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.clock import GameClock
        from osrlib.core.effects import EFFECTS_STREAM, EffectsLedger
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.core.spells import MAGIC_STREAM, cast_from_scroll
        from osrlib.data import load_monsters, load_spells

        rules = Ruleset()
        streams = RngStreams(master_seed=5)
        catalog = load_spells()

        # A 1st-level magic-user who could never memorize *fire ball* reads one off a scroll.
        zelia = create_character(
            name="Zelia",
            class_id="magic_user",
            alignment=Alignment.NEUTRAL,
            ruleset=rules,
            stream=streams.get(CHARACTER_CREATION_STREAM),
            starting_spell_ids=["read_magic"],
        ).character
        template = load_monsters().get("goblin")
        goblin = spawn_monster(template, id="monster-0001", stream=streams.get(MONSTER_SPAWN_STREAM))

        outcome = cast_from_scroll(
            zelia,
            catalog.get("fire_ball"),
            "damage",
            targets=[goblin],
            ledger=EffectsLedger(),
            clock=GameClock(),
            allocator=IdAllocator(),
            registry={"monster-0001": goblin},
            ruleset=rules,
            stream=streams.get(MAGIC_STREAM),
            effects_stream=streams.get(EFFECTS_STREAM),
        )
        assert outcome.affected_ids == ("monster-0001",)
        assert goblin.current_hp == 0  # 5d6 at the scroll's caster level, and the goblin failed its save
        assert zelia.memorized_spells == ()  # nothing was spent from memory
        ```
    """
    context = context or CastContext()
    caster = _ScrollReader(reader, minimum_caster_level(spell))
    rejections = validate_scroll_cast(
        reader,
        spell,
        mode,
        reversed=reversed,
        targets=targets,
        context=context,
        ledger=ledger,
    )
    if rejections:
        raise ValueError(f"illegal scroll cast: {[rejection.code for rejection in rejections]}")
    return _perform_cast(
        caster,
        spell,
        spell.mode(mode, reversed=reversed),
        reversed,
        targets,
        context=context,
        ledger=ledger,
        clock=clock,
        allocator=allocator,
        registry=registry,
        ruleset=ruleset,
        stream=stream,
        effects_stream=effects_stream,
    )


def disrupt_casting(caster: Any, spell_id: str, *, reversed: bool = False) -> list[Event]:
    """Take away a spell a caster declared but never got to cast.

    A caster who announces a spell and is then hit, or fails a save, before their turn comes round
    loses the spell anyway, as though they had cast it. Call this when that happens. Working out
    that it happened is your game's job, or the battle layer's: it is the caster losing initiative
    and then being successfully attacked before they act.

    Nothing is resolved and nothing is rolled. One memorized copy goes and one event comes back. The
    copy chosen is the one matching the declared form, and failing that any copy of the spell at all,
    which is what lets a divine caster's declared reversal cost them a normally prepared copy.

    Args:
        caster: The caster who was interrupted, a [`Character`][osrlib.core.character.Character].
            Its `memorized_spells` loses one copy.
        spell_id: The id of the spell they had declared. For the ids the shipped catalog uses, see
            [the spell id index][spells-index].
        reversed: True when the declared cast was of the reversed form.

    Returns:
        A single [`SpellDisruptedEvent`][osrlib.core.events.SpellDisruptedEvent], in a list, for you
        to publish alongside whatever caused the disruption.

    Raises:
        ValueError: If the caster has no memorized copy of that spell. Reaching this means the
            declaration was tracked wrongly, since a caster cannot declare what they never memorized.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.core.spells import MemorizedSpell, disrupt_casting, memorize_spells
        from osrlib.data import load_classes, load_spells

        streams = RngStreams(master_seed=5)
        definition = load_classes().get("magic_user")
        zelia = create_character(
            name="Zelia",
            class_id="magic_user",
            alignment=Alignment.NEUTRAL,
            ruleset=Ruleset(),
            stream=streams.get(CHARACTER_CREATION_STREAM),
            starting_spell_ids=["magic_missile"],
        ).character
        memorize_spells(zelia, definition, load_spells(), [MemorizedSpell(spell_id="magic_missile")])

        events = disrupt_casting(zelia, "magic_missile")
        assert [event.code for event in events] == ["magic.cast.disrupted"]
        assert zelia.memorized_spells == ()  # gone, the same as if she had cast it
        ```
    """
    memorized = list(getattr(caster, "memorized_spells", ()))
    index = next(
        (i for i, copy in enumerate(memorized) if copy.spell_id == spell_id and copy.reversed == reversed),
        None,
    )
    if index is None:
        index = next((i for i, copy in enumerate(memorized) if copy.spell_id == spell_id), None)
    if index is None:
        raise ValueError(f"{_entity_id(caster)} has no memorized copy of {spell_id!r} to disrupt")
    del memorized[index]
    caster.memorized_spells = tuple(memorized)
    return [SpellDisruptedEvent(caster_id=_entity_id(caster), spell_id=spell_id, reversed=reversed)]


def _is_undead(target: Any) -> bool:
    template = getattr(target, "template", None)
    return template is not None and "undead" in template.categories


def _is_person(target: Any) -> bool:
    """The *hold/charm person* gate: any character, or a monster with the `person` category."""
    if getattr(target, "definition", None) is not None:
        return True
    template = getattr(target, "template", None)
    return template is not None and "person" in template.categories


def _is_arcane_caster(target: Any) -> bool:
    """The *feeblemind* gate: a target whose class has the `arcane_magic` tag."""
    definition = getattr(target, "definition", None)
    if definition is None:
        return False
    profile = caster_profile(definition)
    return profile is not None and profile.kind == "arcane"


def _monster_hit_dice(target: Any) -> Any | None:
    template = getattr(target, "template", None)
    return template.hit_dice if template is not None else None


def _eligible(target: Any, mode: SpellMode) -> bool:
    """Resolve a mode's eligibility gates: resolution outcomes, never rejections."""
    if isinstance(target, str):
        return True
    params = mode.effect.params if mode.effect is not None else {}
    if params.get("excludes_undead") and _is_undead(target):
        return False
    if params.get("undead_only") and not _is_undead(target):
        return False
    if params.get("person_gate") and not _is_person(target):
        return False
    if params.get("arcane_caster_only") and not _is_arcane_caster(target):
        return False
    hit_dice = _monster_hit_dice(target)
    if params.get("hd_bonus_required"):
        # *Sleep* mode 1 reads "a single creature with 4+1 Hit Dice" as a monster
        # whose HD count equals `hd_count` and whose HD modifier is positive.
        if hit_dice is None or hit_dice.count != _int_param(params, "hd_count", 4) or hit_dice.modifier <= 0:
            return False
    if params.get("excludes_hd_4_plus") and hit_dice is not None and hit_dice.count == 4 and hit_dice.modifier > 0:
        return False
    targeting = mode.targeting
    if targeting is not None:
        if targeting.hd_cap is not None and effective_hd(target) > targeting.hd_cap:
            return False
        if targeting.hd_min is not None and effective_hd(target) < targeting.hd_min:
            return False
    return True


def _select_cast_targets(
    caster: Any, mode: SpellMode, targets: Sequence[object], stream: RngStream
) -> tuple[list[object], list[Event]]:
    """Filter eligibility, then resolve the targeting mode over the survivors.

    Eligibility filtering happens inside resolution, never as a rejection, so ineligible candidates
    consume no HD budget and no group-count slot. A wight in a *sleep* candidate list isn't
    selected.
    """
    targeting = mode.targeting
    if targeting is None:
        return list(targets), []
    if targeting.mode is TargetingMode.SELF:
        return [caster], []
    eligible = [target for target in targets if _eligible(target, mode)]
    if targeting.mode is TargetingMode.SINGLE or (mode.effect is not None and "missiles_base" in mode.effect.params):
        return eligible, []
    if targeting.mode is TargetingMode.HD_BUDGET:
        budget = roll(str(targeting.hd_budget_dice), stream).total
        return select_targets(TargetingMode.HD_BUDGET, eligible, stream=stream, hd_budget=budget)
    if targeting.mode is TargetingMode.UP_TO_N:
        return select_targets(
            TargetingMode.UP_TO_N, eligible, stream=stream, count=targeting.count, count_dice=targeting.count_dice
        )
    # Area modes take every supplied candidate. A radius ward centered on the caster,
    # such as *protection from evil 10' radius*, covers the caster too.
    if (
        mode.effect is not None
        and mode.effect.params.get("includes_caster")
        and all(target is not caster for target in eligible)
    ):
        return [caster, *eligible], []
    return list(eligible), []


def _spell_save(
    target: Any, mode: SpellMode, caster: Any, stream: RngStream, *, element: str | None = None
) -> tuple[bool, list[Event]]:
    """Roll a mode's saving throw and return `(passed, events)`.

    Spell saves pass `magical=True`, so the WIS modifier applies, along with the mode's modifier and
    the effect's element. An energy `auto_save` defense resolves through the same pipeline.
    """
    save = mode.save
    if save is None:
        raise ValueError(f"mode {mode.key!r} carries no saving throw")
    result = saving_throw(
        target,
        SaveCategory(save.category),
        modifier=save.modifier,
        magical=True,
        element=element,
        source=caster,
        stream=stream,
    )
    return result.passed, list(result.events)


def _touch_attack(
    caster: Any, target: Any, spell: SpellTemplate, *, ruleset: Ruleset, stream: RngStream
) -> tuple[bool, list[Event]]:
    """Roll the in-combat touch attack: a melee attack roll from the magic stream."""
    result = attack_roll(caster, target, None, context=AttackContext(), ruleset=ruleset, stream=stream)
    events = [
        event.model_copy(update={"attack_name": spell.id}) if hasattr(event, "attack_name") else event
        for event in result.events
    ]
    return result.hit, events


def _per_level_dice(expression: str, caster_level: int) -> str:
    """Scale a per-level dice expression: `1d6` per level at level 5 becomes `5d6`."""
    parsed = parse(expression)
    count = parsed.count * caster_level
    modifier = parsed.modifier * caster_level
    suffix = f"{modifier:+d}" if modifier else ""
    return f"{count}d{parsed.sides}{suffix}"


def _resolved_duration(
    spell: SpellTemplate, reversed: bool, caster_level: int, params: Mapping[str, Any]
) -> dict[str, Any]:
    """Build an effect definition's duration fields from the spell and overrides.

    Per-level durations are computed at cast. A fixed amount gains `per_level × caster level`, and a
    dice duration folds the bonus into the dice modifier, so `1d6 turns +1 per level` at level 3
    attaches `1d6+3` and keeps its roll-at-attach behavior on the effects stream. A concentration
    duration attaches indefinite, since the caller releases concentration effects.

    An effect param overrides all of that: `permanent`, `indefinite`, or an explicit
    `duration_dice`, `duration_amount`, and `duration_unit` (*cause disease*'s 2d12 days).
    """
    if params.get("permanent"):
        return {"permanent": True}
    if params.get("indefinite"):
        return {}
    if "duration_dice" in params or "duration_amount" in params:
        fields: dict[str, Any] = {"duration_unit": TimeUnit(str(params["duration_unit"]))}
        if "duration_dice" in params:
            fields["duration_dice"] = str(params["duration_dice"])
        else:
            fields["duration_amount"] = int(params["duration_amount"])
        return fields
    spec = spell.duration_spec
    if reversed and spell.reversed_form is not None and spell.reversed_form.duration_spec is not None:
        spec = spell.reversed_form.duration_spec
    if spec.kind == "permanent":
        return {"permanent": True}
    if spec.kind in ("instant", "special", "concentration"):
        return {}
    unit = TimeUnit(spec.unit)
    bonus = spec.per_level * caster_level
    if spec.dice is not None:
        parsed = parse(spec.dice)
        modifier = parsed.modifier + bonus
        suffix = f"{modifier:+d}" if modifier else ""
        return {"duration_unit": unit, "duration_dice": f"{parsed.count}d{parsed.sides}{suffix}"}
    return {"duration_unit": unit, "duration_amount": (spec.amount or 0) + bonus}


def _charm_interval_rounds(target: Any) -> int:
    """The charm re-save interval in rounds, by the target's INT band.

    The page's monthly band counts as 30 days and its weekly band as 7. A monster has no INT score
    and falls to the middle, weekly band, which an override can correct per monster.
    """
    scores = getattr(target, "scores", None)
    if scores is None:
        days = 7
    else:
        intelligence = scores[AbilityScore.INT]
        if intelligence <= 8:
            days = 30
        elif intelligence <= 12:
            days = 7
        else:
            days = 1
    return days * ROUNDS_PER_DAY


def _effect_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """The params copied onto the attached effect: the per-spell data, minus the consumed keys."""
    consumed = {
        "permanent",
        "indefinite",
        "duration_dice",
        "duration_amount",
        "duration_unit",
        "effect_kind",
        "tick",
        "expiry",
        "excludes_undead",
        "undead_only",
        "person_gate",
        "arcane_caster_only",
        "hd_count",
        "hd_bonus_required",
        "excludes_hd_4_plus",
        "escape_dice",
        "escape_unit",
        "augmented_strength_rounds",
        "giant_strength_rounds",
    }
    return {key: value for key, value in params.items() if key not in consumed}


def _condition_definition(
    spell: SpellTemplate,
    mode: SpellMode,
    reversed: bool,
    caster: Any,
    target: Any,
    context: CastContext,
) -> EffectDefinition:
    """Build the per-cast effect definition for a condition-attaching mode."""
    effect = _mode_effect(mode)
    params: dict[str, Any] = dict(effect.params)
    duration = _resolved_duration(spell, reversed, caster.level, params)
    fields: dict[str, Any] = {
        "kind": str(params.get("effect_kind", spell.id)),
        "condition": effect.condition,
        "modifiers": effect.modifiers,
        "dispellable": True,
        "params": _effect_params(params),
        **duration,
    }
    if params.get("tick") == "charm_resave":
        fields["tick"] = "charm_resave"
        fields["tick_interval_rounds"] = _charm_interval_rounds(target)
    if params.get("expiry"):
        fields["expiry"] = str(params["expiry"])
    if "escape_dice" in params:
        if isinstance(target, str):
            # A web cast at a cell keeps the spell's own duration, since the web
            # stays put, and carries the escape params on the effect. The crawl
            # layer's enter hook reads them and attaches the per-creature entangled
            # countdown when someone walks in.
            fields["params"] = {
                **fields["params"],
                "escape_dice": str(params["escape_dice"]),
                "escape_unit": str(params["escape_unit"]),
            }
            fields["condition"] = None
        else:
            # *Web*'s escape countdown goes by strength. Normal strength rolls the
            # escape dice. The augmented and giant tiers come from the caller's
            # context rather than from any effect osrlib grants.
            tier = context.strength_tiers.get(_target_ref(target))
            if tier == "augmented":
                fields.update(duration_unit=TimeUnit.ROUND, duration_amount=int(params["augmented_strength_rounds"]))
                fields.pop("duration_dice", None)
            elif tier == "giant":
                fields.update(duration_unit=TimeUnit.ROUND, duration_amount=int(params["giant_strength_rounds"]))
                fields.pop("duration_dice", None)
            else:
                fields.update(
                    duration_unit=TimeUnit(str(params["escape_unit"])), duration_dice=str(params["escape_dice"])
                )
                fields.pop("duration_amount", None)
    return EffectDefinition(**fields)


def _resolve_effect(
    caster: Any,
    spell: SpellTemplate,
    mode: SpellMode,
    reversed: bool,
    selected: list[Any],
    state: _CastState,
    *,
    context: CastContext,
    ledger: EffectsLedger,
    clock: GameClock,
    allocator: Any,
    registry: dict[str, Any],
    ruleset: Ruleset,
    stream: RngStream,
    effects_stream: RngStream,
) -> None:
    """Dispatch one automated mode's resolution: the casting interpreter."""
    kind = _mode_effect(mode).kind
    if kind == "damage":
        _resolve_damage(caster, spell, mode, selected, state, context, ruleset=ruleset, stream=stream, clock=clock)
    elif kind == "heal":
        _resolve_heal(mode, selected, state, stream)
    elif kind == "cure":
        _resolve_cure(caster, mode, selected, state, context, ledger, registry, stream)
    elif kind in ("condition", "modifiers", "attach_only"):
        _resolve_attachment(
            caster,
            spell,
            mode,
            reversed,
            selected,
            state,
            context=context,
            ledger=ledger,
            clock=clock,
            allocator=allocator,
            registry=registry,
            stream=stream,
            effects_stream=effects_stream,
        )
    elif kind == "kill":
        _resolve_kill(caster, mode, selected, state, stream, ruleset)
    elif kind == "dispel":
        _resolve_dispel(caster, mode, selected, state, ledger, registry, stream)
    elif kind == "restore_life":
        _resolve_restore_life(
            caster,
            spell,
            mode,
            selected,
            state,
            context=context,
            ledger=ledger,
            clock=clock,
            allocator=allocator,
            registry=registry,
            effects_stream=effects_stream,
        )
    else:
        raise ValueError(f"unknown effect kind {kind!r} on {spell.id}")


def _resolve_damage(
    caster: Any,
    spell: SpellTemplate,
    mode: SpellMode,
    selected: list[Any],
    state: _CastState,
    context: CastContext,
    *,
    ruleset: Ruleset,
    stream: RngStream,
    clock: GameClock,
) -> None:
    effect = _mode_effect(mode)
    params = effect.params
    caster_id = _entity_id(caster)
    element = str(params["element"]) if "element" in params else None
    # Spell damage is magical and presents the `magic` key, so a wight's
    # silver-or-magic gate admits *magic missile* and a gargoyle's magic-only gate
    # admits *fire ball*.
    source = DamageSource(
        keys=("magic",),
        element=element,
        magical=True,
        kind="spell",
        destructive=bool(params.get("destructive", False)),
    )
    if "missiles_base" in params:
        # *Magic missile* takes one supplied target per missile, and repeating a
        # target stacks the missiles on it. Each missile hits without an attack roll
        # and allows no save, and rolls its own damage. The whole thing resolves at
        # cast and attaches nothing, so the printed 1-turn duration never applies.
        for target in selected:
            if check_immunity(target, source, ruleset=ruleset, attacker=caster):
                state.events.extend(_absorbed_events(target, caster_id, source))
                continue
            result = roll(str(params["dice"]), stream)
            state.events.extend(
                deal_damage(
                    target,
                    result.total,
                    source=source,
                    attacker_id=caster_id,
                    rolls=result.rolls,
                    clock=clock,
                    ruleset=ruleset,
                    stream=stream,
                )
            )
            state.affect(target)
        return
    if params.get("touch_attack"):
        target = selected[0] if selected else None
        if target is None:
            return
        if context.in_combat:
            hit, touch_events = _touch_attack(caster, target, spell, ruleset=ruleset, stream=stream)
            state.events.extend(touch_events)
            if not hit:
                return
        if check_immunity(target, source, ruleset=ruleset, attacker=caster):
            state.events.extend(_absorbed_events(target, caster_id, source))
            return
        result = roll(str(params["dice"]), stream)
        state.events.extend(
            deal_damage(
                target,
                result.total,
                source=source,
                attacker_id=caster_id,
                rolls=result.rolls,
                clock=clock,
                ruleset=ruleset,
                stream=stream,
            )
        )
        state.affect(target)
        return
    for target in selected:
        if check_immunity(target, source, ruleset=ruleset, attacker=caster):
            state.events.extend(_absorbed_events(target, caster_id, source))
            continue
        passed = False
        save = mode.save
        if save is not None:
            passed, save_events = _spell_save(target, mode, caster, stream, element=element)
            state.events.extend(save_events)
            if passed and save.on_save == "negates":
                continue
        dice = (
            _per_level_dice(str(params["dice_per_level"]), caster.level)
            if "dice_per_level" in params
            else str(params["dice"])
        )
        result = roll(dice, stream)
        amount = result.total
        if passed and save is not None and save.on_save == "half":
            amount //= 2  # integer division, so a halved total rounds down
        if amount < 1:
            continue
        state.events.extend(
            deal_damage(
                target,
                amount,
                source=source,
                attacker_id=caster_id,
                rolls=result.rolls,
                clock=clock,
                ruleset=ruleset,
                stream=stream,
            )
        )
        state.affect(target)


def _absorbed_events(target: Any, caster_id: str, source: DamageSource) -> list[Event]:
    keys = source.keys if source.element is None else (*source.keys, source.element)
    return [DamageAbsorbedEvent(target_id=_target_ref(target), attacker_id=caster_id, keys=keys)]


def _resolve_heal(mode: SpellMode, selected: list[Any], state: _CastState, stream: RngStream) -> None:
    for target in selected:
        result = roll(str(_mode_effect(mode).params["dice"]), stream)
        events = apply_healing(target, result.total, source="magical")
        state.events.extend(events)
        if any(event.code == "combat.healing.applied" for event in events):
            state.affect(target)


def _resolve_cure(
    caster: Any,
    mode: SpellMode,
    selected: list[Any],
    state: _CastState,
    context: CastContext,
    ledger: EffectsLedger,
    registry: dict[str, Any],
    stream: RngStream,
) -> None:
    effect = _mode_effect(mode)
    params = effect.params
    for target in selected:
        ref = _target_ref(target)
        for active in list(ledger.active_on(ref)):
            definition = active.definition
            matches = definition.condition in effect.cures_conditions or definition.kind in effect.cures_effect_kinds
            if not matches:
                continue
            if "magical_fear_save" in params and definition.condition is Condition.AFRAID:
                # Against magical fear, *remove fear* lets the subject save with +1
                # per caster level to shake it. A failed save keeps the fear.
                result = saving_throw(
                    target,
                    SaveCategory(str(params["magical_fear_save"])),
                    modifier=_int_param(params, "save_bonus_per_level", 0) * caster.level,
                    magical=True,
                    stream=stream,
                )
                state.events.extend(result.events)
                if not result.passed:
                    continue
            state.events.extend(ledger.release(active.effect_id, registry))
            state.affect(target)
        if params.get("revives_poison_dead") and not isinstance(target, str):
            window = _int_param(params, "revive_window_rounds", 10)
            # The page titles its revival usage "Characters", so only a Character is
            # revivable here. osrlib records no cause of death, so supplying
            # `rounds_since_death` is itself the caller's assertion that the target
            # died of poison that many rounds ago. The session takes it from its own
            # death records, and omits it for a death by any other means.
            if (
                getattr(target, "definition", None) is not None
                and has_condition(target, Condition.DEAD)
                and context.rounds_since_death is not None
                and context.rounds_since_death <= window
            ):
                # Revival undoes the poison death and stands the subject up at 1 hp.
                # RAW names no hit point total, so osrlib sets the lowest one.
                state.events.extend(remove_condition(target, Condition.DEAD, None))
                target.current_hp = 1
                state.events.append(
                    HitPointsReportedEvent(target_id=ref, current_hp=1, max_hp=getattr(target, "max_hp", 1))
                )
                state.affect(target)


def _resolve_attachment(
    caster: Any,
    spell: SpellTemplate,
    mode: SpellMode,
    reversed: bool,
    selected: list[Any],
    state: _CastState,
    *,
    context: CastContext,
    ledger: EffectsLedger,
    clock: GameClock,
    allocator: Any,
    registry: dict[str, Any],
    stream: RngStream,
    effects_stream: RngStream,
) -> None:
    """Attach a condition, modifier bundle, or structured attach-only effect per target."""
    params = _mode_effect(mode).params
    for target in selected:
        if not _eligible(target, mode):
            continue
        if mode.save is not None:
            passed, save_events = _spell_save(target, mode, caster, stream)
            state.events.extend(save_events)
            if passed:
                continue
        definition = _condition_definition(spell, mode, reversed, caster, target, context)
        effect, attach_events = ledger.attach(
            definition,
            _target_ref(target),
            clock=clock,
            allocator=allocator,
            registry=registry,
            stream=effects_stream,
            caster_level=caster.level,
        )
        state.events.extend(attach_events)
        if effect is None:
            continue
        if "images_dice" in params:
            # *Mirror image*'s 1d4 images live in effect state. The count is rolled
            # as the effect attaches, so it comes from the effects stream.
            effect.state["images"] = roll(str(params["images_dice"]), effects_stream).total
        state.affect(target)


def _resolve_kill(
    caster: Any,
    mode: SpellMode,
    selected: list[Any],
    state: _CastState,
    stream: RngStream,
    ruleset: Ruleset,
) -> None:
    params = _mode_effect(mode).params
    for target in selected:
        if not _eligible(target, mode):
            continue
        if mode.save is not None:
            passed, save_events = _spell_save(target, mode, caster, stream)
            state.events.extend(save_events)
            if passed:
                continue
        events = kill(target, permanent=bool(params.get("permanent", False)))
        if not events:
            continue
        state.events.extend(events)
        if params.get("destroy_equipment"):
            spell_source = DamageSource(kind="spell", destructive=True)
            state.events.extend(destroy_equipment(target, source=spell_source, ruleset=ruleset, stream=stream))
        state.affect(target)


def _resolve_dispel(
    caster: Any,
    mode: SpellMode,
    selected: list[Any],
    state: _CastState,
    ledger: EffectsLedger,
    registry: dict[str, Any],
    stream: RngStream,
) -> None:
    """*Dispel magic*: release dispellable effects, though a higher-level effect may survive.

    Per effect, when the recorded caster level exceeds the dispelling caster's, the effect survives
    on a d100 roll at or under 5% per level of deficit (RAW: "a 5% chance per level difference of
    *not* being dispelled"). Only effects whose definition sets `dispellable` are considered at all,
    and that flag defaults to False.
    """
    pct_per_level = _int_param(_mode_effect(mode).params, "survival_pct_per_level", 5)
    released: list[str] = []
    survived: list[str] = []
    for target in selected:
        ref = _target_ref(target)
        for active in list(ledger.active_on(ref)):
            if not active.definition.dispellable:
                continue
            if active.caster_level is not None and active.caster_level > caster.level:
                chance = pct_per_level * (active.caster_level - caster.level)
                if roll("d%", stream).total <= chance:
                    survived.append(active.effect_id)
                    continue
            state.events.extend(ledger.release(active.effect_id, registry))
            released.append(active.effect_id)
            state.affect(target)
    state.events.append(
        MagicDispelledEvent(
            caster_id=_entity_id(caster),
            released_effect_ids=tuple(released),
            surviving_effect_ids=tuple(survived),
        )
    )


def _resolve_restore_life(
    caster: Any,
    spell: SpellTemplate,
    mode: SpellMode,
    selected: list[Any],
    state: _CastState,
    *,
    context: CastContext,
    ledger: EffectsLedger,
    clock: GameClock,
    allocator: Any,
    registry: dict[str, Any],
    effects_stream: RngStream,
) -> None:
    """*Raise dead*'s restore-life usage.

    Restores a dead human or demihuman, which means any `Character`, all four Classic races
    included, and never a monster. The subject must have been dead no longer than 4 days × (caster
    level − 7), which is 0 days at level 7, following RAW. Revival sets 1 hp, removes `dead`, and
    attaches the weakness effect: cannot attack or cast, half movement, fixed at 14 elapsed days as
    a simplification of RAW's "two full weeks of bed rest". Rest tracking is crawl procedure, so a
    game wanting strict bed-rest semantics extends or releases the effect through the ledger.
    Magical healing doesn't shorten it, per the page.
    """
    params = _mode_effect(mode).params
    target = selected[0] if selected else None
    if target is None or isinstance(target, str):
        return
    if getattr(target, "definition", None) is None:
        return
    if not has_condition(target, Condition.DEAD):
        return
    limit = 4 * max(0, caster.level - _int_param(params, "days_per_level_above", 7))
    if context.days_since_death is None or context.days_since_death > limit:
        return
    state.events.extend(remove_condition(target, Condition.DEAD, None))
    target.current_hp = 1
    state.events.append(
        HitPointsReportedEvent(target_id=_target_ref(target), current_hp=1, max_hp=getattr(target, "max_hp", 1))
    )
    weakness = EffectDefinition(
        kind="raise_dead_weakness",
        condition=Condition.WEAKENED,
        duration_unit=TimeUnit.DAY,
        duration_amount=_int_param(params, "weakness_days", 14),
        dispellable=True,
        params={"movement_multiplier_pct": 50, "cannot_carry_heavy": True},
    )
    _, attach_events = ledger.attach(
        weakness,
        _target_ref(target),
        clock=clock,
        allocator=allocator,
        registry=registry,
        stream=effects_stream,
        caster_level=caster.level,
    )
    state.events.extend(attach_events)
    state.affect(target)


def pop_mirror_image(
    ledger: EffectsLedger, target_ref: str, *, registry: dict[str, Any], clock: GameClock
) -> list[Event]:
    """Destroy one of a caster's mirror images.

    *Mirror image* surrounds its caster with illusory duplicates, and an attack on the caster
    destroys one of them whether or not the attack lands. Nothing in this module notices attacks, so
    call this once for every attack aimed at a caster who has the spell running, before or after you
    resolve the attack itself.

    It is safe to call on anyone. A target with no mirror images active returns nothing, so you do
    not have to check first. When the last image goes, the effect is released from the ledger and
    that release's own events come back with the pop.

    Args:
        ledger: The [`EffectsLedger`][osrlib.core.effects.EffectsLedger] the images live on, the one
            the cast attached them to.
        target_ref: The entity id of the caster being attacked.
        registry: Every live combatant by entity id, as
            [`Character`][osrlib.core.character.Character] and
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] objects. Read when the last
            image goes and the effect lifts.
        clock: The [`GameClock`][osrlib.core.clock.GameClock], read to stamp the event with the
            current round.

    Returns:
        An [`EffectTickedEvent`][osrlib.core.events.EffectTickedEvent] for the image destroyed, plus
        the release events when that was the last one. Empty when the target has no images.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.clock import GameClock
        from osrlib.core.effects import EFFECTS_STREAM, EffectsLedger
        from osrlib.core.monsters import IdAllocator
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.core.spells import (
            MAGIC_STREAM,
            MemorizedSpell,
            cast_spell,
            caster_profile,
            memorize_spells,
            pop_mirror_image,
        )
        from osrlib.data import load_classes, load_spells

        rules = Ruleset()
        streams = RngStreams(master_seed=5)
        catalog = load_spells()
        definition = load_classes().get("magic_user")
        zelia = create_character(
            name="Zelia",
            class_id="magic_user",
            alignment=Alignment.NEUTRAL,
            ruleset=rules,
            stream=streams.get(CHARACTER_CREATION_STREAM),
            starting_spell_ids=["magic_missile"],
        ).character
        zelia.id = "pc-1"
        zelia.level = 3
        zelia.spell_book = ("magic_missile", "mirror_image")
        memorize_spells(zelia, definition, catalog, [MemorizedSpell(spell_id="mirror_image")])

        ledger = EffectsLedger()
        clock = GameClock()
        cast_spell(
            zelia,
            catalog.get("mirror_image"),
            "images",
            profile=caster_profile(definition),
            ledger=ledger,
            clock=clock,
            allocator=IdAllocator(),
            registry={"pc-1": zelia},
            ruleset=rules,
            stream=streams.get(MAGIC_STREAM),
            effects_stream=streams.get(EFFECTS_STREAM),
        )
        effect = ledger.active_on("pc-1", "mirror_image")[0]
        assert effect.state["images"] == 2  # 1d4 images, stable under this seed

        popped = pop_mirror_image(ledger, "pc-1", registry={"pc-1": zelia}, clock=clock)
        assert [event.code for event in popped] == ["effects.effect.ticked"]
        assert effect.state["images"] == 1
        assert pop_mirror_image(ledger, "pc-2", registry={"pc-1": zelia}, clock=clock) == []
        ```
    """
    effects = ledger.active_on(target_ref, "mirror_image")
    if not effects:
        return []
    effect = effects[0]
    effect.state["images"] = max(0, effect.state.get("images", 0) - 1)
    events: list[Event] = [
        EffectTickedEvent(effect_id=effect.effect_id, kind="mirror_image", target_ref=target_ref, round=clock.rounds)
    ]
    if effect.state["images"] <= 0:
        events.extend(ledger.release(effect.effect_id, registry))
    return events


class TurnUndeadResult(BaseModel):
    """What came of a turning attempt: the dice, the verdict on each kind of undead, and who fled.

    You get one back from [`turn_undead`][osrlib.core.spells.turn_undead]. The attempt always
    happened, so there is no accepted flag here. Read `outcomes` to explain the result and
    `affected_ids` to know who to move.

    """

    model_config = ConfigDict(frozen=True)

    roll: int
    """The 2d6 the cleric rolled to turn.

    Compared against the table threshold for each kind of undead present, so one roll can turn some
    kinds and fail against others.
    """

    hd_pool: int | None = None
    """The second 2d6, giving the Hit Dice worth of undead the attempt can affect.

    Rolled when at least one kind came out `turn` or `destroy`. `None` when no kind succeeded and no
    second roll was made.
    """

    outcomes: tuple[TurningTypeOutcome, ...] = ()
    """One [`TurningTypeOutcome`][osrlib.core.events.TurningTypeOutcome] per kind of monster present.

    In the order the kinds first appeared among the candidates. Each `outcome` is `turn` for a kind
    that flees, `destroy` for one annihilated outright, `fail` for one that held, and `unaffected`
    for a candidate that was not undead at all.
    """

    affected_ids: tuple[str, ...] = ()
    """The entity ids of the individual monsters the attempt reached, as many as `hd_pool` paid for."""

    destroyed_ids: tuple[str, ...] = ()
    """The entity ids of those among the affected that were destroyed rather than turned.

    They are dead permanently, and *raise dead* cannot bring them back.
    """

    events: tuple[Event, ...] = ()
    """The [`UndeadTurnedEvent`][osrlib.core.events.UndeadTurnedEvent] and then the consequences.

    A death for each monster destroyed, an attached `turned` condition for each one that fled.
    Publish these.
    """


def validate_turn_undead(cleric: Any, definition: ClassDefinition) -> list[Rejection]:
    """Ask whether a character may attempt to turn undead, without rolling.

    Call this to decide whether to offer turning as an action at all. Then call
    [`turn_undead`][osrlib.core.spells.turn_undead], which runs the same checks and raises if any
    fail. Nothing here rolls dice or changes anything.

    Two things can stop an attempt. The character's class may not turn undead at all, which it does
    only if it has the `turn_undead` ability tag, so a class you author gains the ability by adding
    that tag. Or the character may be in no state to present a holy symbol: dead, petrified,
    paralysed, or asleep, or weakened, which is the state *raise dead* leaves someone in and which
    bars class abilities outright.

    Whether the character is actually carrying a holy symbol is not checked. The SRD tells clerics
    to carry one as a matter of their class rather than as a condition on the procedure, so a game
    that wants the stricter reading checks inventory itself.

    Args:
        cleric: The character attempting the turning, a
            [`Character`][osrlib.core.character.Character]. Read, never written.
        definition: Their class, as a [`ClassDefinition`][osrlib.core.classes.ClassDefinition] from
            [`load_classes`][osrlib.data.load_classes].

    Returns:
        Why the attempt cannot be made, as [`Rejection`][osrlib.core.validation.Rejection] models
        with structured `code` and `params`. Empty when the attempt may go ahead. At most one: the
        first problem found ends the call.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.core.spells import validate_turn_undead
        from osrlib.data import load_classes

        streams = RngStreams(master_seed=5)
        classes = load_classes()
        aldis = create_character(
            name="Aldis",
            class_id="cleric",
            alignment=Alignment.LAWFUL,
            ruleset=Ruleset(),
            stream=streams.get(CHARACTER_CREATION_STREAM),
        ).character

        assert validate_turn_undead(aldis, classes.get("cleric")) == []
        refused = validate_turn_undead(aldis, classes.get("fighter"))
        assert [rejection.code for rejection in refused] == ["magic.turning.not_a_turner"]
        ```
    """
    if not any(ability.tag == "turn_undead" for ability in getattr(definition, "abilities", ())):
        return [Rejection(code="magic.turning.not_a_turner", params={"class": definition.id})]
    incapacity = (Condition.DEAD, Condition.PETRIFIED, Condition.PARALYSED, Condition.ASLEEP, Condition.WEAKENED)
    for condition in incapacity:
        if has_condition(cleric, condition):
            return [
                Rejection(
                    code="magic.turning.caster_incapacitated",
                    params={"caster": _entity_id(cleric), "condition": condition.value},
                )
            ]
    return []


def turn_undead(
    cleric: Any,
    definition: ClassDefinition,
    candidates: Sequence[Any],
    *,
    ledger: EffectsLedger,
    clock: GameClock,
    allocator: Any,
    registry: dict[str, Any],
    stream: RngStream,
) -> TurnUndeadResult:
    """Drive off or destroy undead with a cleric's holy symbol, the whole procedure in one call.

    Turning is not a spell and costs no slot, so nothing here touches the caster's memorized list.
    Ask [`validate_turn_undead`][osrlib.core.spells.validate_turn_undead] first, because an attempt
    the character cannot make raises rather than returning a refusal. Afterwards, publish the
    result's events and read `affected_ids` to move the undead that fled: what fleeing looks like on
    your map is your game's business, and all that happens here is that those monsters gain the
    `turned` condition.

    The procedure runs in two rolls. First one 2d6 is compared against the turning table, once for
    each kind of monster among the candidates rather than once per monster, since a kind either
    turns or it does not. Some kinds turn automatically, some are destroyed outright, some are
    beyond the cleric's power at their level.

    If any kind came out turned or destroyed, a second 2d6 gives a pool of Hit Dice, and the
    individual monsters of those kinds are affected cheapest first until the pool cannot pay for the
    next one. Ties keep the order you
    passed them in. The remainder of the pool is wasted rather than spent on something else, and a
    successful turn always reaches at least one undead even when the pool rolls short.

    Monsters of a kind marked for destruction die permanently, and *raise dead* cannot bring them
    back. The rest gain the `turned` condition through an effect that does not expire on its own and
    cannot be dispelled, so release it from the ledger when the encounter ends.

    Pass any monsters you like as candidates. A candidate that is not undead resolves as unaffected
    rather than rejecting the attempt, which keeps a turning attempt from doubling as a free way to
    find out what is undead.

    Args:
        cleric: The character turning, a [`Character`][osrlib.core.character.Character].
        definition: Their class, as a [`ClassDefinition`][osrlib.core.classes.ClassDefinition] from
            [`load_classes`][osrlib.data.load_classes]. It must have the `turn_undead` tag.
        candidates: The monsters present, as
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] objects, in a stable order.
            Order decides ties when the Hit Dice pool runs out, so pass the same order every time if
            you want the same result on a replay.
        ledger: The [`EffectsLedger`][osrlib.core.effects.EffectsLedger] the `turned` condition
            attaches to. Pass the one your game keeps.
        clock: The [`GameClock`][osrlib.core.clock.GameClock], read to stamp the attached effects.
        allocator: The [`IdAllocator`][osrlib.core.monsters.IdAllocator] that names each attached
            effect.
        registry: Every live combatant by entity id, as
            [`Character`][osrlib.core.character.Character] and
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] objects.
        stream: The [`RngStream`][osrlib.core.rng.RngStream] both 2d6 rolls come from,
            conventionally [`MAGIC_STREAM`][osrlib.core.spells.MAGIC_STREAM]. Both rolls are on the
            player-visible event, because in B/X the player rolls them.

    Returns:
        The [`TurnUndeadResult`][osrlib.core.spells.TurnUndeadResult]: the dice, the verdict per
        kind, who was reached, and every event.

    Raises:
        ValueError: If the character cannot turn undead at all. Ask
            [`validate_turn_undead`][osrlib.core.spells.validate_turn_undead] first.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.clock import GameClock
        from osrlib.core.effects import Condition, EffectsLedger, has_condition
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.core.spells import MAGIC_STREAM, turn_undead
        from osrlib.data import load_classes, load_monsters

        streams = RngStreams(master_seed=12)
        definition = load_classes().get("cleric")
        aldis = create_character(
            name="Aldis",
            class_id="cleric",
            alignment=Alignment.LAWFUL,
            ruleset=Ruleset(),
            stream=streams.get(CHARACTER_CREATION_STREAM),
        ).character

        template = load_monsters().get("skeleton")
        spawn = streams.get(MONSTER_SPAWN_STREAM)
        skeletons = [spawn_monster(template, id=f"monster-000{n}", stream=spawn) for n in (1, 2, 3)]
        result = turn_undead(
            aldis,
            definition,
            skeletons,
            ledger=EffectsLedger(),
            clock=GameClock(),
            allocator=IdAllocator(),
            registry={monster.id: monster for monster in skeletons},
            stream=streams.get(MAGIC_STREAM),
        )
        assert (result.roll, result.hd_pool) == (7, 4)  # met the threshold, then 4 Hit Dice of effect
        assert [outcome.outcome for outcome in result.outcomes] == ["turn"]
        assert result.affected_ids == ("monster-0001", "monster-0002", "monster-0003")
        assert result.destroyed_ids == ()  # turned, not destroyed
        assert all(has_condition(monster, Condition.TURNED) for monster in skeletons)
        ```
    """
    rejections = validate_turn_undead(cleric, definition)
    if rejections:
        raise ValueError(f"illegal turning: {[rejection.code for rejection in rejections]}")
    from osrlib.data import load_combat_tables

    turning = load_combat_tables().turning
    caster_id = _entity_id(cleric)
    turn_roll = roll("2d6", stream).total

    outcomes: list[TurningTypeOutcome] = []
    verdicts: dict[str, TurningTypeOutcome] = {}
    for candidate in candidates:
        template = getattr(candidate, "template", None)
        if template is None or template.id in verdicts:
            continue
        if "undead" not in template.categories:
            outcome = TurningTypeOutcome(template_id=template.id, outcome="unaffected")
        else:
            column = turning_column(template.hit_dice)
            if column is None:
                outcome = TurningTypeOutcome(template_id=template.id, column=None, outcome="fail")
            else:
                cell = turning.result(cleric.level, column)
                if cell.outcome == "number":
                    succeeded = cell.threshold is not None and turn_roll >= cell.threshold
                    outcome = TurningTypeOutcome(
                        template_id=template.id,
                        column=column,
                        outcome="turn" if succeeded else "fail",
                        threshold=cell.threshold,
                    )
                else:
                    outcome = TurningTypeOutcome(template_id=template.id, column=column, outcome=cell.outcome)
        verdicts[template.id] = outcome
        outcomes.append(outcome)

    succeeded_types = {outcome.template_id for outcome in outcomes if outcome.outcome in ("turn", "destroy")}
    if not succeeded_types:
        event = UndeadTurnedEvent(
            code="magic.turning.failed", caster_id=caster_id, roll=turn_roll, types=tuple(outcomes)
        )
        return TurnUndeadResult(roll=turn_roll, outcomes=tuple(outcomes), events=(event,))

    hd_pool = roll("2d6", stream).total
    eligible = [
        candidate
        for candidate in candidates
        if getattr(candidate, "template", None) is not None
        and candidate.template.id in succeeded_types
        and not has_condition(candidate, Condition.DEAD)
    ]
    ordered = sorted(enumerate(eligible), key=lambda pair: (effective_hd(pair[1]), pair[0]))
    affected: list[Any] = []
    remaining = hd_pool
    for _, monster in ordered:
        cost = effective_hd(monster)
        if cost > remaining:
            break  # the rest of the pool is wasted, never spent on another monster
        affected.append(monster)
        remaining -= cost
    if not affected and ordered:
        affected.append(ordered[0][1])  # RAW minimum effect: the cheapest eligible monster

    events: list[Event] = []
    destroyed: list[str] = []
    affected_ids = tuple(_entity_id(monster) for monster in affected)
    consequence_events: list[Event] = []
    for monster in affected:
        if verdicts[monster.template.id].outcome == "destroy":
            consequence_events.extend(kill(monster, permanent=True))
            destroyed.append(_entity_id(monster))
        else:
            definition_turned = EffectDefinition(kind="turned", condition=Condition.TURNED, stacking="ignore")
            _, attach_events = ledger.attach(
                definition_turned, _entity_id(monster), clock=clock, allocator=allocator, registry=registry
            )
            consequence_events.extend(attach_events)
    code = "magic.turning.destroyed" if destroyed else "magic.turning.turned"
    events.append(
        UndeadTurnedEvent(
            code=code,
            caster_id=caster_id,
            roll=turn_roll,
            hd_pool=hd_pool,
            types=tuple(outcomes),
            affected_ids=affected_ids,
        )
    )
    events.extend(consequence_events)
    return TurnUndeadResult(
        roll=turn_roll,
        hd_pool=hd_pool,
        outcomes=tuple(outcomes),
        affected_ids=affected_ids,
        destroyed_ids=tuple(destroyed),
        events=tuple(events),
    )
