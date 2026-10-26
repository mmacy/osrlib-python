"""Spell memorization, casting, spell resolution, and turning undead.

This module runs the magic rules: preparing spells for the day, casting them, resolving what they
do, and turning undead. The functions that act on a caster change the caster and the effects
ledger in place and return events, and the functions named `validate_` change nothing. Everything
here runs without a game session, and every random draw comes from a seeded RNG stream you pass in.

The module uses these terms:

- A caster's *spell slots* are how many spells of each spell level they can prepare. They are the
  `spell_slots` of the progression row for the caster's level in their
  [`ClassDefinition`][osrlib.core.classes.ClassDefinition].
- A *memorized copy* is one prepared spell, a [`MemorizedSpell`][osrlib.core.spells.MemorizedSpell].
  Casting a spell spends one copy.
- A *mode* is one castable usage of a spell, a [`SpellMode`][osrlib.core.spells.SpellMode]. A spell
  page that prints two numbered usages has two modes.
- The *effects ledger* is the [`EffectsLedger`][osrlib.core.effects.EffectsLedger] that contains what
  a cast leaves running, like a condition or a timed bonus. Its
  [`advance`][osrlib.core.effects.EffectsLedger.advance] method counts each effect down and
  releases it when it ends.

The members, in the order you use them:

- [`load_spells`][osrlib.data.load_spells] returns the
  [`SpellCatalog`][osrlib.core.spells.SpellCatalog]. Its
  [`get`][osrlib.core.spells.SpellCatalog.get] takes a spell id and returns a
  [`SpellTemplate`][osrlib.core.spells.SpellTemplate], and its
  [`by_list`][osrlib.core.spells.SpellCatalog.by_list] returns the spells of one class's list.
- [`caster_profile`][osrlib.core.spells.caster_profile] takes a class definition and returns its
  [`CasterProfile`][osrlib.core.spells.CasterProfile], or `None` for a class that casts nothing.
- [`add_spell_to_book`][osrlib.core.spells.add_spell_to_book] writes a spell into an arcane
  caster's spell book and returns a [`SpellBookResult`][osrlib.core.spells.SpellBookResult].
  [`open_book_capacity`][osrlib.core.spells.open_book_capacity] returns the room left at each
  spell level.
- [`memorize_spells`][osrlib.core.spells.memorize_spells] takes a list of memorized copies,
  replaces the caster's prepared list, and returns a
  [`MemorizationResult`][osrlib.core.spells.MemorizationResult].
- [`validate_cast`][osrlib.core.spells.validate_cast] takes the caster, a template, a mode key,
  the targets, and a [`CastContext`][osrlib.core.spells.CastContext], and returns the reasons the
  cast is illegal.
- [`cast_spell`][osrlib.core.spells.cast_spell] spends a memorized copy, resolves the mode, and
  returns a [`CastResult`][osrlib.core.spells.CastResult].
- [`validate_scroll_cast`][osrlib.core.spells.validate_scroll_cast] and
  [`cast_from_scroll`][osrlib.core.spells.cast_from_scroll] do the same for a spell read off a
  scroll, at the caster level [`minimum_caster_level`][osrlib.core.spells.minimum_caster_level]
  returns.
- [`disrupt_casting`][osrlib.core.spells.disrupt_casting] removes a copy from a caster whose spell
  was disrupted before they could cast it.
- [`pop_mirror_image`][osrlib.core.spells.pop_mirror_image] removes one of a caster's mirror
  images when an attack is aimed at them.
- [`forget_excess_memorized`][osrlib.core.spells.forget_excess_memorized] removes the copies a
  caster has no slots for after losing levels.
- [`validate_turn_undead`][osrlib.core.spells.validate_turn_undead] and
  [`turn_undead`][osrlib.core.spells.turn_undead] check and run a cleric's attempt to turn undead,
  which returns a [`TurnUndeadResult`][osrlib.core.spells.TurnUndeadResult].

A template's fields contain the rest as models you read: one
[`SpellMode`][osrlib.core.spells.SpellMode] per mode, each with a
[`TargetingSpec`][osrlib.core.spells.TargetingSpec], a [`SaveSpec`][osrlib.core.spells.SaveSpec],
and a [`SpellEffect`][osrlib.core.spells.SpellEffect], and the
[`ReversedForm`][osrlib.core.spells.ReversedForm],
[`DurationSpec`][osrlib.core.spells.DurationSpec], and
[`RangeSpec`][osrlib.core.spells.RangeSpec]. [`EFFECT_KINDS`][osrlib.core.spells.EFFECT_KINDS]
lists what an effect can do, and [`MAGIC_STREAM`][osrlib.core.spells.MAGIC_STREAM] is the name of
the RNG stream for spell draws. Every model in this module is frozen: assigning to one of its fields raises
`pydantic.ValidationError`.

In a game session, you execute commands instead of calling these functions, and the crawl layer,
the [`osrlib.crawl`][osrlib.crawl] package, calls them after the session's checks pass.
[`PrepareSpells`][osrlib.crawl.commands.PrepareSpells] requires a night's sleep before
preparation, [`UseItem`][osrlib.crawl.commands.UseItem] reads a scroll only when there is light to
read by, and [`LearnSpell`][osrlib.crawl.commands.LearnSpell],
[`CastSpell`][osrlib.crawl.commands.CastSpell], and
[`TurnUndead`][osrlib.crawl.commands.TurnUndead] check the session's mode. Call this module
directly when you run the rules yourself.

The OSE SRD's spell pages compile into the catalog of
[`SpellTemplate`][osrlib.core.spells.SpellTemplate] models. A template has the page's printed
duration, range, and text, and one mode per castable usage. Each mode has its targeting, its saving
throw, and, when osrlib resolves the mode itself, the effect that casting runs. A mode osrlib
doesn't resolve is marked `manual=True` and has the SRD's text. Casting a manual mode still spends
the copy and returns the cast event, and your game or narrator decides what happens.

A reversible spell's reverse, like *cause light wounds* for *cure light wounds*, is not a separate
catalog entry. It's the template's `reversed_form`, a
[`ReversedForm`][osrlib.core.spells.ReversedForm]. A spell the SRD prints twice, once on a cleric
page and once on a magic-user page with different rules, is two entries: the cleric one has the id
suffix `_c` and the magic-user one `_mu`, like `light_c` and `light_mu`.

Casters are [`Caster`][osrlib.core.creature.Caster] values, which a
[`Character`][osrlib.core.character.Character] satisfies and a monster does not. Targets are
[`Creature`][osrlib.core.creature.Creature] values, which characters and
[`MonsterInstance`][osrlib.core.monsters.MonsterInstance] objects satisfy, or location strings for
effects a game attaches to places rather than to creatures.

Every random draw made while a spell resolves comes from the
[`MAGIC_STREAM`][osrlib.core.spells.MAGIC_STREAM] stream: targeting dice, damage dice, touch
attack rolls, the targets' saving throws, *dispel magic*'s survival rolls, and both turning
rolls. Spell results and combat draws therefore replay independently of each other. Draws made
for an attached effect, like a duration rolled as it attaches or a charmed creature's later
saving throws to break free, come from the [`EFFECTS_STREAM`][osrlib.core.effects.EFFECTS_STREAM]
stream.

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

# Prepare the day's list, then cast the spell to heal the caster.
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
# and turning therefore take their caster as the `Caster` protocol, which a character
# satisfies, and character.py imports MemorizedSpell from here, never the reverse.

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from osrlib.core.abilities import AbilityScore
from osrlib.core.alignment import Alignment
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
from osrlib.core.creature import Caster, Creature
from osrlib.core.dice import parse, roll
from osrlib.core.effects import (
    ActiveCondition,
    ActiveModifier,
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
from osrlib.core.monsters import MonsterInstance, MonsterTemplate
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
"""The name of the RNG stream that every draw made while a spell resolves comes from.

Pass `streams.get(MAGIC_STREAM)` as the `stream` argument of
[`cast_spell`][osrlib.core.spells.cast_spell],
[`cast_from_scroll`][osrlib.core.spells.cast_from_scroll], and
[`turn_undead`][osrlib.core.spells.turn_undead], where `streams` is an
[`RngStreams`][osrlib.core.rng.RngStreams]. The draws on it are targeting dice, damage dice,
touch attack rolls, the targets' saving throws, *dispel magic*'s survival rolls, and both
turning rolls.

Because magic has a separate stream, a replay reproduces a spell result even after the combat
draws around it have changed, and the reverse. Draws made for an attached effect come from
[`EFFECTS_STREAM`][osrlib.core.effects.EFFECTS_STREAM] instead, so pass that stream as
`effects_stream` rather than this one.
"""

EFFECT_KINDS = frozenset(
    {"damage", "heal", "cure", "condition", "modifiers", "kill", "restore_life", "dispel", "attach_only"}
)
"""The effect kinds osrlib can resolve when a spell is cast.

[`SpellEffect.kind`][osrlib.core.spells.SpellEffect.kind] must be one of these, so a spell you
write yourself has to use one of these behaviors, or have its mode marked
[`manual`][osrlib.core.spells.SpellMode.manual] and left to your game. Each kind is a branch of
osrlib's resolution code, so a new kind needs a change to osrlib, not only new data.

- `damage`: casting rolls damage dice against each target.
- `heal`: casting rolls healing dice for each target.
- `cure`: casting releases the effects that put named conditions on a target, or effects of named
  kinds.
- `condition`: casting attaches an effect that puts a condition on each target.
- `modifiers`: casting attaches an effect that gives each target bonuses or penalties.
- `attach_only`: casting attaches an effect with no condition, as for *light*, *fly*, and *mirror
  image*.
- `kill`: casting kills each target, as for *death spell* and *disintegrate*.
- `restore_life`: casting brings a dead character back, as for *raise dead*.
- `dispel`: casting releases magical effects, as for *dispel magic*.
"""


class DurationSpec(BaseModel):
    """How long a spell lasts, parsed out of the printed duration line.

    Read one from [`SpellTemplate.duration_spec`][osrlib.core.spells.SpellTemplate.duration_spec],
    or from [`ReversedForm.duration_spec`][osrlib.core.spells.ReversedForm.duration_spec] when the
    reverse lasts a different length. Casting reads it for you and turns it into the duration of
    the effect it attaches to the ledger. You need this model when you show a spell, sort or filter
    spells by how long they last, or write a spell of your own.

    The printed line is the template's `duration`, and that is what you show a player. A printed
    duration that has no structure osrlib can read, like `"Special"`, is stored here as
    `kind="special"` with no other field set.

    Constructing one raises `pydantic.ValidationError` when `dice` is not a valid dice expression,
    or when a `fixed` duration has no `unit`, or has no `amount`, `dice`, or `per_level`.

    Examples:
        ```python
        from osrlib.core.clock import TimeUnit
        from osrlib.data import load_spells

        catalog = load_spells()
        light = catalog.get("light_mu")
        assert light.duration == "6 turns +1 per level"  # the printed line
        spec = light.duration_spec
        assert (spec.kind, spec.unit, spec.amount, spec.per_level) == ("fixed", TimeUnit.TURN, 6, 1)
        # So a 3rd-level caster's light lasts 6 + 1 * 3 turns.
        assert catalog.get("cure_light_wounds").duration_spec.kind == "instant"
        ```
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["instant", "permanent", "concentration", "fixed", "special"]
    """Which kind of duration this is.

    - `instant`: the spell resolves and is over.
    - `permanent`: the effect never ends on its own.
    - `concentration`: the effect lasts while the caster concentrates. Casting attaches it with no
      end, and you release it from the ledger when the caster stops.
    - `fixed`: a length counted in `unit`.
    - `special`: the printed line has no structure osrlib can read. Casting attaches the effect
      with no end, and you release it when the printed line describes.
    """

    unit: TimeUnit | None = None
    """The time unit a `fixed` duration counts in: rounds, turns, or days. `None` on every other kind.

    See [`TimeUnit`][osrlib.core.clock.TimeUnit].
    """

    amount: int | None = None
    """How many `unit` a `fixed` duration lasts, before the per-level bonus.

    `None` when the length is rolled with `dice`. When `amount` and `dice` are both `None`, the
    length is `per_level` times the caster's level.
    """

    dice: str | None = None
    """A dice expression for a rolled length, in place of `amount`.

    *Sleep* lasts `"4d4"` turns. The dice are rolled on the effects stream when the effect attaches.
    """

    per_level: int = 0
    """How many extra `unit` the duration lasts per caster level.

    Casting adds it to `amount`, or to the modifier of `dice`. *Light* prints `6 turns +1 per
    level`, so `amount` is 6 and `per_level` is 1, and a 3rd-level caster's light lasts 9 turns.
    """

    concentration_cap_unit: TimeUnit | None = None
    """The unit of the longest a `concentration` duration can last, when the page prints a limit.

    *Insect plague* prints `Concentration (up to 1 day)`, so this is days and
    `concentration_cap_amount` is 1. `None` when the page prints no limit. Casting doesn't apply the
    limit.
    """

    concentration_cap_amount: int | None = None
    """How many `concentration_cap_unit` a `concentration` duration can last. `None` when the page prints no limit."""

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

    Read one from [`SpellTemplate.range_spec`][osrlib.core.spells.SpellTemplate.range_spec].
    [`validate_cast`][osrlib.core.spells.validate_cast] reads it for you, but only when you pass
    the distance to the target in
    [`CastContext.distance_feet`][osrlib.core.spells.CastContext.distance_feet]. osrlib has no map,
    so with no distance there is no range check. Read this model yourself when you draw a range
    indicator, filter spells by reach, or decide which targets to offer.

    The printed line is the template's `range`, and that is what you show a player. A printed range
    with no structure osrlib can read, like `"Appears in the caster’s presence"`, is stored as
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
    """Which kind of range this is.

    - `caster`: the spell affects only the caster.
    - `touch`: the spell affects a creature the caster touches, which can be the caster.
    - `feet` and `yards`: a fixed distance, in `feet`.
    - `per_level`: a distance that grows with caster level.
    - `special`: the printed line has no structure osrlib can read.
    """

    feet: int | None = None
    """The distance in feet, for the `feet`, `yards`, and `per_level` kinds. `None` on the other kinds.

    Yards are converted, so a range printed as 240 yards is 720 here. On a `per_level` range this is
    the distance before the per-level bonus, and it is `None` when the printed range is only a
    distance per level, like `10’ per level`.
    """

    per_level_feet: int | None = None
    """Extra feet of reach per caster level on a `per_level` range. `None` on the other kinds.

    A range printed `60’ +10’ per level` is `feet` 60 and `per_level_feet` 10, so a 5th-level caster
    reaches 110 feet.
    """


class TargetingSpec(BaseModel):
    """Who one castable usage of a spell can hit, and how many of them.

    Read one from [`SpellMode.targeting`][osrlib.core.spells.SpellMode.targeting] to find out how
    many targets to collect before you call [`cast_spell`][osrlib.core.spells.cast_spell]. `mode` is
    the [`TargetingMode`][osrlib.core.combat.TargetingMode] that
    [`select_targets`][osrlib.core.combat.select_targets] also uses, and the other fields are the
    numbers that size and limit it for this spell.

    The targets you pass to casting are candidates, not the final list. Casting drops the ones the
    mode can't affect and then applies `mode` to the rest, so an ineligible creature in the list
    uses up no Hit Dice and no place in a group. An ineligible target is therefore an outcome of the
    cast rather than a reason to refuse it. A cast that finds nothing eligible returns a
    [`CastResult`][osrlib.core.spells.CastResult] with `no_effect` set, and the memorized copy is
    still spent.

    Constructing one raises `pydantic.ValidationError` when `count_dice` or `hd_budget_dice` is not
    a valid dice expression.

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
    """Which targeting mode the spell's mode uses, and so how many targets you pass.

    - `self`: no targets. The spell affects the caster.
    - `single`: exactly one target.
    - `up_to_n`: a group, sized by `count` or `count_dice`. *Magic missile* is the exception: it
      takes exactly one target per missile, and the same target can appear more than once.
    - `hd_budget`: as many creatures as a rolled pool of Hit Dice pays for.
    - `area`: every candidate you pass, which should be the creatures inside the area.
    - `gaze`: every candidate you pass, for a gaze that reaches everyone engaged with the gazer.
    """

    count: int | None = None
    """The fixed size of an `up_to_n` group, when the page prints a number rather than dice."""

    count_dice: str | None = None
    """The dice rolled when the spell is cast to size an `up_to_n` group, on the magic stream.

    *Hold person*'s group mode is `"1d4"` and *charm monster*'s is `"3d6"`.
    """

    hd_budget_dice: str | None = None
    """The dice rolled to size an `hd_budget` pool, like `"2d8"` for *sleep*.

    Creatures are affected in order of Hit Dice, fewest first, until the pool can't pay for the
    next one. The rest of the pool is lost rather than spent on anyone else.
    """

    hd_cap: int | None = None
    """The most Hit Dice a creature may have and still be eligible.

    *Sleep*'s `hd_budget` mode sets 4, and *charm monster*'s group mode sets 3.
    """

    hd_min: int | None = None
    """The fewest Hit Dice a creature must have to be eligible.

    *Charm monster*'s single-target mode sets 4, for the page's "more than 3 Hit Dice".
    """

    shape: str | None = None
    """The name of the area an `area` mode covers, like `"sphere"`. `None` on every other mode."""

    dimensions: dict[str, int] = {}
    """The area's measurements in feet, keyed by name. *Fire ball*'s sphere is `{"radius_feet": 20}`.

    osrlib doesn't work out which creatures are inside the area. Your game decides who is caught and
    passes them as candidates.
    """

    @field_validator("count_dice", "hd_budget_dice")
    @classmethod
    def _dice_must_parse(cls, value: str | None) -> str | None:
        if value is not None:
            parse(value)
        return value


class SaveSpec(BaseModel):
    """The saving throw one castable usage of a spell allows its targets, and what passing it does.

    Read one from [`SpellMode.save`][osrlib.core.spells.SpellMode.save], which is `None` when the
    mode allows no save. Casting rolls each target's save for you on the magic stream, with
    [`saving_throw`][osrlib.core.combat.saving_throw]. Read this model to show a player what a
    target faces before they cast, or to explain why a target came through unharmed.

    A spell's save counts as a save against magic, so a character's WIS adjustment applies. A
    monster with an automatic save against the element of a damage spell, like a red dragon against
    *fire ball*, passes without a roll.

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
    """Which saving throw the target rolls, as a [`SaveCategory`][osrlib.core.combat.SaveCategory]."""

    modifier: int = 0
    """The adjustment added to the target's roll. A negative value makes the save harder.

    *Hold person*'s single-target mode is −2, and *feeblemind* is −4.
    """

    on_save: Literal["negates", "half"] = "negates"
    """What passing the save does.

    `negates` means the spell does nothing to that target. `half` means the target still takes half
    the damage, rounded down.
    """


class SpellEffect(BaseModel):
    """What one castable usage of a spell does to its targets.

    Read one from [`SpellMode.effect`][osrlib.core.spells.SpellMode.effect]. Casting runs it for
    you. You need this model to describe a spell in an interface, to decide whether a spell is worth
    casting on a given target, or to write a spell of your own.

    A mode marked [`manual`][osrlib.core.spells.SpellMode.manual] in the built-in catalog has no
    effect: its `effect` is `None`, and casting it spends the copy, returns the cast event, and
    leaves the outcome to you. Every other mode has an effect.

    Constructing one raises `pydantic.ValidationError` when `kind` is not one of
    [`EFFECT_KINDS`][osrlib.core.spells.EFFECT_KINDS].

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
    """What casting does, one of [`EFFECT_KINDS`][osrlib.core.spells.EFFECT_KINDS]."""

    condition: Condition | None = None
    """The [`Condition`][osrlib.core.effects.Condition] a `condition` effect puts on each target, like `blind` or
    `charmed`. `None` on every other kind.
    """

    cures_conditions: tuple[Condition, ...] = ()
    """The conditions a `cure` effect lifts. *Cure light wounds*' `cure_paralysis` mode lifts `paralysed`.

    Casting releases each ledger effect on the target that puts one of these conditions on it.
    """

    cures_effect_kinds: tuple[str, ...] = ()
    """The kinds of ledger effect a `cure` effect releases, by name.

    This is for spells that cancel a named magic rather than a condition. *Light*'s `cancel` mode
    releases `"darkness"`.
    """

    modifiers: tuple[ModifierSpec, ...] = ()
    """The bonuses and penalties a `modifiers` effect gives, as [`ModifierSpec`][osrlib.core.effects.ModifierSpec]
    models.

    A bonus to armour class or to saves, for example. They belong to the attached effect and end
    when it ends.
    """

    params: dict[str, int | str | bool | tuple[int | str, ...]] = {}
    """The numbers and options for this spell that casting reads for `kind`, keyed by name.

    They include damage dice, per-level scaling, which creatures are eligible, how long after death
    a spell can revive, and area sizes. The keys differ by spell and by kind, so read them for the
    mode you have rather than expecting a fixed set.
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
    paralysis, and *light* lights an area, blinds a creature, or cancels darkness. Each usage is a
    mode, and you choose one by its `key`, which is the `mode` argument of
    [`cast_spell`][osrlib.core.spells.cast_spell]. Get a spell's modes from
    [`SpellTemplate.modes`][osrlib.core.spells.SpellTemplate.modes], or one mode by key from
    [`SpellTemplate.mode`][osrlib.core.spells.SpellTemplate.mode].

    A mode is either automated or manual, and the difference changes what casting does for you. An
    automated mode has both `targeting` and `effect`, and osrlib resolves it: it picks the targets,
    rolls the saves and the dice, applies the outcome, and attaches any lasting effect. A manual
    mode has `manual=True`, and in the built-in catalog it has no targeting and no effect, because
    the SRD page gives osrlib nothing to resolve. Casting a manual mode still spends the memorized
    copy and returns a [`CastResult`][osrlib.core.spells.CastResult] with `manual` set and the
    mode's `prose`, and your game or narrator decides what happens. Check `manual` before you
    promise a player an outcome.

    Constructing one raises `pydantic.ValidationError` when `manual` is False and `targeting` or
    `effect` is `None`.

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
    """The mode's name, like `"heal"`. No two modes of the same form have the same key.

    Pass it as `mode` to [`cast_spell`][osrlib.core.spells.cast_spell] and
    [`validate_cast`][osrlib.core.spells.validate_cast]. A spell with a single usage still has a
    mode, like *fire ball*'s `"damage"`.
    """

    targeting: TargetingSpec | None = None
    """Who the mode can affect and how many, as a [`TargetingSpec`][osrlib.core.spells.TargetingSpec].

    `None` only on a manual mode.
    """

    save: SaveSpec | None = None
    """The saving throw the targets get, as a [`SaveSpec`][osrlib.core.spells.SaveSpec]. `None` when the mode allows
    no save.
    """

    effect: SpellEffect | None = None
    """What the mode does, as a [`SpellEffect`][osrlib.core.spells.SpellEffect].

    `None` only on a manual mode.
    """

    manual: bool = False
    """True when casting spends the copy and returns the event, and your game decides the outcome."""

    prose: str = ""
    """The SRD text for this usage.

    Show it to the player. For a manual mode, it is everything osrlib has about the result.
    """

    @model_validator(mode="after")
    def _automated_modes_carry_structure(self) -> SpellMode:
        if not self.manual and (self.effect is None or self.targeting is None):
            raise ValueError(f"mode {self.key!r} is automated but lacks targeting or an effect")
        return self


class ReversedForm(BaseModel):
    """The reverse of a reversible spell, stored on the spell's entry.

    *Cure light wounds* reverses into *cause light wounds*, and *light* into *darkness*. The reverse
    is never a separate catalog entry. Read it from
    [`SpellTemplate.reversed_form`][osrlib.core.spells.SpellTemplate.reversed_form], which is
    `None` on a spell that doesn't reverse. To cast it, pass `reversed=True` to
    [`cast_spell`][osrlib.core.spells.cast_spell] with a `mode` key from this form's `modes`. The
    reverse's keys can differ from the normal form's: *cure light wounds* has `heal` and
    `cure_paralysis`, and its reverse has `harm`.

    When the form is chosen depends on the kind of caster. An arcane caster chooses normal or
    reversed when memorizing, and the choice is the
    [`MemorizedSpell`][osrlib.core.spells.MemorizedSpell]'s `reversed` field. A divine caster
    memorizes the normal form and chooses when casting, by speaking the words backwards, so any of
    their memorized copies can be cast either way.

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
    """The reverse's name, as the SRD prints it, like `"Cause Light Wounds"`.

    Show this rather than the template's `name` when a cast is reversed.
    """

    prose: str = ""
    """The SRD text for the reverse."""

    modes: tuple[SpellMode, ...] = Field(min_length=1)
    """One [`SpellMode`][osrlib.core.spells.SpellMode] per castable usage of the reverse, at least one, each with a
    different key.
    """

    duration: str | None = None
    """The reverse's printed duration, when the page prints a different one for it.

    `None` means the normal form's duration applies.
    """

    duration_spec: DurationSpec | None = None
    """The structured form of `duration`, as a [`DurationSpec`][osrlib.core.spells.DurationSpec].

    `None` means the reverse lasts as long as the normal form, which is the usual case. A page that
    prints two durations in one line, like *remove curse*'s `Instant / Permanent (curse)`, gives
    the second one to the reverse.
    """


class SpellTemplate(BaseModel):
    """One spell, compiled from its SRD page: the data that casting it uses.

    Get one by id from [`SpellCatalog.get`][osrlib.core.spells.SpellCatalog.get], or a class's
    whole list from [`SpellCatalog.by_list`][osrlib.core.spells.SpellCatalog.by_list]. The catalog
    comes from [`load_spells`][osrlib.data.load_spells]. Pass the template to
    [`validate_cast`][osrlib.core.spells.validate_cast],
    [`cast_spell`][osrlib.core.spells.cast_spell], or
    [`cast_from_scroll`][osrlib.core.spells.cast_from_scroll], and read its `modes` for the mode
    keys those calls accept.

    A template is frozen and shared, and play never changes one. What changes during play are the
    [`MemorizedSpell`][osrlib.core.spells.MemorizedSpell] copies a caster has prepared and the
    effects a cast leaves on the ledger. Both refer to the template by id.

    Constructing one raises `pydantic.ValidationError` when two modes of the same form have the
    same key.

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
    """The id you look the spell up by, made from its name in lowercase with underscores, like `"fire_ball"`.

    A few spells appear in the SRD as a cleric page and a magic-user page with different rules.
    Each such pair is two entries: the cleric one has the suffix `_c` and the magic-user one `_mu`,
    like `"light_c"` and `"light_mu"`. For the built-in ids, iterate
    [`load_spells`][osrlib.data.load_spells]`().spells` or see [the spell id
    index](https://mmacy.github.io/osrlib-python/reference/content-ids/spells/).
    """

    name: str = Field(min_length=1)
    """The spell's printed name, like `"Cure Light Wounds"`. Show this, not the id."""

    spell_list: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    """Which class's spell list the spell belongs to, in lowercase letters, digits, and underscores.

    The built-in catalog has `"cleric"` and `"magic_user"`. A spell you write can name another list,
    for a class you write. It must match the
    [`CasterProfile.spell_list`][osrlib.core.spells.CasterProfile.spell_list] of any caster who
    memorizes or learns the spell.
    """

    level: int = Field(ge=1, le=6)
    """The spell's level, 1 to 6.

    A caster's slots are counted by spell level, not by the caster's level.
    """

    duration: str = Field(min_length=1)
    """The duration line as printed. Show this to a player."""

    duration_spec: DurationSpec
    """The structured form of `duration`, as a [`DurationSpec`][osrlib.core.spells.DurationSpec].

    Casting reads it to set how long an attached effect lasts.
    """

    range: str = Field(min_length=1)
    """The range line as printed. Show this to a player."""

    range_spec: RangeSpec
    """The structured form of `range`, as a [`RangeSpec`][osrlib.core.spells.RangeSpec]."""

    reversed_form: ReversedForm | None = None
    """The spell's reverse, as a [`ReversedForm`][osrlib.core.spells.ReversedForm].

    `None` when the spell doesn't reverse.
    """

    modes: tuple[SpellMode, ...] = Field(min_length=1)
    """One [`SpellMode`][osrlib.core.spells.SpellMode] per numbered usage on the page, in the page's order.

    There is at least one. Their keys are the values casting accepts as its `mode` argument.
    """

    intro: str = ""
    """The page's opening text, above the numbered usages.

    On a page with several usages, it is the lead-in, like `"This spell has two usages:"`. On a
    page with one usage, it is the opening of the spell's description, and that mode's `prose` is
    the full text.
    """

    conjured_monsters: tuple[MonsterTemplate, ...] = ()
    """Monster stat blocks printed on the spell's page rather than in the monster catalog.

    The *sticks to snakes* page, for example, prints the snake's stat block. Spawn them with
    [`spawn_monster`][osrlib.core.monsters.spawn_monster] when you resolve the spell. Casting
    doesn't spawn them.
    """

    conjured_monster_ids: tuple[str, ...] = ()
    """Ids of monsters the spell summons that are in the monster catalog.

    Look them up with [`load_monsters`][osrlib.data.load_monsters]`().get(monster_id)`. *Conjure
    elemental* lists its four elementals this way. Casting doesn't spawn them.
    """

    overrides_applied: tuple[str, ...] = ()
    """The field paths that a correction changed when this template was compiled from the SRD page.

    Empty when the page compiled without corrections. It records where the data departs from the
    SRD for anyone checking it, and osrlib never reads it during play.
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

        Use this when you know which usage you want, to read its targeting, its save, or its prose
        before you cast it. To offer a player a choice instead, iterate
        [`modes`][osrlib.core.spells.SpellTemplate.modes] and show each mode's `prose`. The key you
        pass here is the same string casting takes as its `mode` argument.

        Args:
            key: The mode's key, like `"damage"` or `"blind"`. No two modes of one form share a key,
                but the normal and reversed forms are separate, so the reverse can reuse a key of
                the normal form or use different ones.
            reversed: True to look in the spell's reversed form instead of its normal one.

        Returns:
            The [`SpellMode`][osrlib.core.spells.SpellMode].

        Raises:
            ValueError: If `reversed` is True and the spell has no reversed form, or if the form has
                no mode with that key. The message includes the spell id, and the key when the
                key is missing. Read the form's `modes` for the keys it has.

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
    """A set of spell templates, with lookup by id and by class list.

    [`load_spells`][osrlib.data.load_spells] returns the built-in catalog. It validates the data on
    the first call and returns the same object on every later call. Every function in this module
    that needs spell data takes either a catalog or one
    [`SpellTemplate`][osrlib.core.spells.SpellTemplate] from it. To add spells of your own, build a
    catalog from the built-in `spells` and yours, and pass it instead.

    Use [`get`][osrlib.core.spells.SpellCatalog.get] when you have an id, and
    [`by_list`][osrlib.core.spells.SpellCatalog.by_list] when you build a menu of the spells a
    caster may choose. To find which list a caster's class uses, call
    [`caster_profile`][osrlib.core.spells.caster_profile] on their class definition.

    Constructing one raises `pydantic.ValidationError` when two templates have the same id.

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
    """Every spell template in the catalog, each with a different id.

    The built-in catalog is in id order. A catalog you build is in the order you gave it. Iterate
    this to search on something the two lookup methods don't cover, like a name or an effect kind.
    """

    @model_validator(mode="after")
    def _ids_must_be_unique(self) -> SpellCatalog:
        ids = [template.id for template in self.spells]
        if len(set(ids)) != len(ids):
            raise ValueError("spell ids must be unique")
        return self

    def get(self, spell_id: str) -> SpellTemplate:
        """Return one spell by its id.

        A [`MemorizedSpell`][osrlib.core.spells.MemorizedSpell], a caster's `spell_book`, and the
        events this module returns all refer to spells by id. Call this to turn an id back into the
        spell's data, then pass the template to [`cast_spell`][osrlib.core.spells.cast_spell].

        Args:
            spell_id: The spell's id, like `"fire_ball"` or `"hold_person_c"`. Iterate `spells`
                for every id in this catalog, or see [the spell id
                index](https://mmacy.github.io/osrlib-python/reference/content-ids/spells/) for the built-in ones.

        Returns:
            The [`SpellTemplate`][osrlib.core.spells.SpellTemplate].

        Raises:
            ValueError: If no spell has that id. The message includes the id you passed. An id
                that osrlib gave you resolves in the catalog it came from, so this error means the
                id came from somewhere else, like a typo or a save made with a different catalog.

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
        may prepare with [`memorize_spells`][osrlib.core.spells.memorize_spells]. Pass `level` to
        fill one slot, because a caster's slots are counted per spell level.

        Get the list id from the `spell_list` of
        [`caster_profile`][osrlib.core.spells.caster_profile] rather than typing it, so your code
        also works for a class you write with a new spell list.

        Args:
            spell_list: The list id, like `"cleric"` or `"magic_user"`. An id that matches no spell
                returns an empty tuple rather than raising.
            level: A spell level, 1 to 6, to filter by. `None` returns the whole list.

        Returns:
            The matching [`SpellTemplate`][osrlib.core.spells.SpellTemplate] models in the catalog's
            order, which for the built-in catalog is id order. Empty when nothing matches.

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
            assert catalog.by_list("druid") == ()  # no such list in the built-in catalog
            ```
        """
        return tuple(
            template
            for template in self.spells
            if template.spell_list == spell_list and (level is None or template.level == level)
        )


class MemorizedSpell(BaseModel):
    """One spell a caster has ready to cast: which spell, and in which form.

    Construct one for each slot you want filled, and pass them to
    [`memorize_spells`][osrlib.core.spells.memorize_spells] or to the
    [`PrepareSpells`][osrlib.crawl.commands.PrepareSpells] command. Read them back from a
    character's `memorized_spells`, in the order they were prepared. Which copy goes depends on
    the order: casting spends the first copy that matches, and
    [`forget_excess_memorized`][osrlib.core.spells.forget_excess_memorized], which a level drain
    calls, removes the last ones first.

    A copy has only a spell id and a form. What the spell can do comes from the template you get
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
    """The spell's id, like `"magic_missile"`.

    For the built-in ids, iterate [`load_spells`][osrlib.data.load_spells]`().spells` or see
    [the spell id index](https://mmacy.github.io/osrlib-python/reference/content-ids/spells/).
    """

    reversed: bool = False
    """True when this copy is prepared as the spell's reversed form.

    Only an arcane caster sets it, because in the SRD an arcane caster chooses the form when the
    spell is memorized. A divine caster memorizes the normal form and chooses the form when casting,
    so a divine caster's copies are always False, and preparing one with True is rejected.
    """


class CasterProfile(BaseModel):
    """What kind of caster a class is, and which spell list it draws on.

    Get one from [`caster_profile`][osrlib.core.spells.caster_profile], which reads it from a class
    definition. Pass it as the `profile` argument of
    [`validate_cast`][osrlib.core.spells.validate_cast] and
    [`cast_spell`][osrlib.core.spells.cast_spell].
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["divine", "arcane"]
    """`"divine"` for a class that prays for its spells, `"arcane"` for one that studies them from a book.

    The two kinds differ in three ways. Only an arcane caster has a spell book, which
    [`add_spell_to_book`][osrlib.core.spells.add_spell_to_book] adds to. Only an arcane caster
    chooses a spell's reversed form when memorizing it. Only a divine caster can cast any memorized
    copy in either form.
    """

    spell_list: str
    """The id of the spell list the class uses, like `"cleric"` or `"magic_user"`.

    It has to match [`SpellTemplate.spell_list`][osrlib.core.spells.SpellTemplate.spell_list] for
    the class to memorize or learn a spell. Pass it to
    [`SpellCatalog.by_list`][osrlib.core.spells.SpellCatalog.by_list] for the spells the class can
    choose from.
    """


def caster_profile(definition: ClassDefinition) -> CasterProfile | None:
    """Return how a class casts, or `None` if it casts nothing.

    Call this first when you are about to do anything with a character's spells. It tells you
    whether the class casts at all, and it returns the `profile` argument that
    [`validate_cast`][osrlib.core.spells.validate_cast] and
    [`cast_spell`][osrlib.core.spells.cast_spell] take. Use a `None` answer to leave spells out of
    a fighter's interface. [`memorize_spells`][osrlib.core.spells.memorize_spells] and
    [`add_spell_to_book`][osrlib.core.spells.add_spell_to_book] return a rejection, not an error,
    for a class with no profile.

    The answer comes from the class's ability tags, not from a list of built-in classes. A class you
    write casts when it has a `divine_magic` or `arcane_magic` ability whose `params` has a
    `spell_list`.

    Args:
        definition: The class, as a [`ClassDefinition`][osrlib.core.classes.ClassDefinition] from
            [`load_classes`][osrlib.data.load_classes]`().get(class_id)`, where `class_id` is the
            character's `class_id`.

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

    One of the two fields is always empty. When the preparation was legal, the caster's memorized
    list was replaced, and `events` reports it. When it wasn't, `rejections` gives the reasons, and
    nothing changed. Check [`accepted`][osrlib.core.spells.MemorizationResult.accepted] rather
    than testing either tuple yourself.
    """

    model_config = ConfigDict(frozen=True)

    rejections: tuple[Rejection, ...] = ()
    """Why the preparation was refused, as [`Rejection`][osrlib.core.validation.Rejection] models.

    Each has a `code` and `params` you can turn into a message in your own words. Each problem found
    in the list has a rejection, not only the first, so a player can fix the whole list at once.
    Empty on success.
    """

    events: tuple[Event, ...] = ()
    """The one [`SpellsMemorizedEvent`][osrlib.core.events.SpellsMemorizedEvent], which lists what was prepared.

    Empty on a rejection.
    """

    @property
    def accepted(self) -> bool:
        """Whether the caster's memorized list was replaced.

        False means nothing changed, and `rejections` gives the reasons.
        """
        return not self.rejections


def memorize_spells(
    caster: Caster, definition: ClassDefinition, catalog: SpellCatalog, selections: Sequence[MemorizedSpell]
) -> MemorizationResult:
    """Fill a caster's spell slots for the day, replacing whatever was memorized before.

    Preparing comes before casting each day, and a caster with an empty memorized list can cast
    nothing. The list you pass replaces the old one entirely, because B/X has no way to refill one
    slot. A caster who casts one spell prepares the whole list again at the next opportunity. Then
    cast the copies with [`cast_spell`][osrlib.core.spells.cast_spell].

    What a caster may choose depends on the kind of caster, which
    [`caster_profile`][osrlib.core.spells.caster_profile] reports. A divine caster chooses from the
    whole class list and never marks a copy reversed, because they choose the form when casting. An
    arcane caster chooses only from their spell book, which
    [`add_spell_to_book`][osrlib.core.spells.add_spell_to_book] adds to, and chooses each copy's
    form now. For both, the number of copies at each spell level must fit the slots for the
    caster's current level, and the same spell can be prepared more than once.

    The rules about when a caster may prepare (once a day, after an uninterrupted night's sleep,
    over an hour) are part of exploration, so the
    [`PrepareSpells`][osrlib.crawl.commands.PrepareSpells] command in the crawl layer checks them.
    This function doesn't, so when you run the rules yourself, you decide when preparation is
    allowed.

    Args:
        caster: The [`Caster`][osrlib.core.creature.Caster] preparing spells, which a
            [`Character`][osrlib.core.character.Character] satisfies. This replaces its
            `memorized_spells`. Nothing changes when the call is rejected.
        definition: The caster's class, as a
            [`ClassDefinition`][osrlib.core.classes.ClassDefinition] from
            [`load_classes`][osrlib.data.load_classes]. Its progression row for the caster's level
            gives the slot counts.
        catalog: The spell catalog, from [`load_spells`][osrlib.data.load_spells].
        selections: The [`MemorizedSpell`][osrlib.core.spells.MemorizedSpell] copies to prepare, in
            the order you want them stored. Casting spends the first matching copy, and
            [`forget_excess_memorized`][osrlib.core.spells.forget_excess_memorized] removes the last
            ones first.

    Returns:
        A [`MemorizationResult`][osrlib.core.spells.MemorizationResult]. On success it has the
        memorized event. Otherwise it has every rejection found, and the caster is unchanged. The
        rejection codes are `magic.memorize.not_a_caster`, `magic.memorize.unknown_spell`,
        `magic.memorize.wrong_list`, `magic.memorize.divine_reverses_at_cast`,
        `magic.memorize.not_in_book`, `magic.memorize.not_reversible`, and
        `magic.memorize.slots_exceeded`.

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

    One of the two fields is always empty. Either the spell went into the book and `events` reports
    it, or it didn't, `rejections` gives the reason, and the book is unchanged. Check
    [`accepted`][osrlib.core.spells.SpellBookResult.accepted] rather than testing either tuple
    yourself.
    """

    model_config = ConfigDict(frozen=True)

    rejections: tuple[Rejection, ...] = ()
    """Why the addition was refused, as [`Rejection`][osrlib.core.validation.Rejection] models.

    Each has a `code` and `params`. There is at most one, because the call stops at the first
    problem. Empty on success.
    """

    events: tuple[Event, ...] = ()
    """The one [`SpellBookUpdatedEvent`][osrlib.core.events.SpellBookUpdatedEvent] for the new spell.

    Empty on a rejection.
    """

    @property
    def accepted(self) -> bool:
        """Whether the spell went into the book.

        False means the book is unchanged, and `rejections` gives the reason.
        """
        return not self.rejections


def open_book_capacity(caster: Caster, definition: ClassDefinition, catalog: SpellCatalog) -> tuple[int, ...]:
    """Return how many more spells fit in an arcane caster's book, at each spell level.

    Call this before you offer a player a spell to learn, so the menu shows only the spell levels
    with room. [`add_spell_to_book`][osrlib.core.spells.add_spell_to_book] makes the same check and
    returns a rejection when there is no room. This function gives you the answer before that call.

    At each spell level, a book has room for as many spells as the caster could memorize at that
    level. Entry `i` of the answer is the room left at spell level `i + 1`: the caster's current slot
    count there, minus the spells the book already has there, and never below zero.

    A book loses no pages when its owner loses levels, so a drained caster can have more spells at
    a level than the level has room for. The count for that level is 0, and the caster can add nothing
    there until they regain levels.

    Args:
        caster: The [`Caster`][osrlib.core.creature.Caster], which a
            [`Character`][osrlib.core.character.Character] satisfies. Its `spell_book` and `level`
            are read, and nothing is changed.
        definition: The caster's class, as a
            [`ClassDefinition`][osrlib.core.classes.ClassDefinition] from
            [`load_classes`][osrlib.data.load_classes].
        catalog: The spell catalog, from [`load_spells`][osrlib.data.load_spells], used to look up
            the level of each spell in the book.

    Returns:
        One count per spell level in the caster's `spell_slots`, lowest level first. An empty
        tuple for a class with no spell book, which is every divine caster and every class that
        casts nothing.

    Raises:
        ValueError: If the book has a spell id that isn't in `catalog`. The message includes the id.
            Pass the catalog the book's spells came from.

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
    caster: Caster, definition: ClassDefinition, catalog: SpellCatalog, spell_id: str
) -> SpellBookResult:
    """Write a spell into an arcane caster's spell book.

    An arcane caster prepares spells only from their spell book, so this is how their choice grows:
    when they gain a level, find a mentor, or copy a captured book. Call
    [`open_book_capacity`][osrlib.core.spells.open_book_capacity] first to show only the spell
    levels with room, and [`SpellCatalog.by_list`][osrlib.core.spells.SpellCatalog.by_list] to list
    what the class can learn. Once a spell is in the book,
    [`memorize_spells`][osrlib.core.spells.memorize_spells] can prepare it.

    At each spell level, the book has room for as many spells as the caster could memorize at that
    level, and it never has the same spell twice. A caster who loses levels keeps every spell in the
    book, and can add no more at a level until it has room again.

    This function only writes the spell in. What learning costs and how long it takes are up to
    your game, and nothing here charges money or spends game time. In a session,
    [`LearnSpell`][osrlib.crawl.commands.LearnSpell] calls this function, and it spends no game
    time either.

    Args:
        caster: The [`Caster`][osrlib.core.creature.Caster] learning the spell, a
            [`Character`][osrlib.core.character.Character] with an arcane class. One id is added to
            its `spell_book`. Nothing changes when the call is rejected.
        definition: The caster's class, as a
            [`ClassDefinition`][osrlib.core.classes.ClassDefinition] from
            [`load_classes`][osrlib.data.load_classes].
        catalog: The spell catalog, from [`load_spells`][osrlib.data.load_spells].
        spell_id: The spell to write in. For the ids the class may learn, call
            [`SpellCatalog.by_list`][osrlib.core.spells.SpellCatalog.by_list], or see
            [the spell id index](https://mmacy.github.io/osrlib-python/reference/content-ids/spells/) for every built-in
            id.

    Returns:
        A [`SpellBookResult`][osrlib.core.spells.SpellBookResult]. On success it has the
        book-updated event. Otherwise it has one rejection, and the book is unchanged. The rejection
        codes are `magic.book.not_arcane` for a class with no book, `magic.book.unknown_spell`,
        `magic.book.wrong_list` for a spell from another class's list, `magic.book.duplicate` for a
        spell already in the book, and `magic.book.capacity_exceeded` for a spell level with no
        room.

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


def forget_excess_memorized(caster: Caster, definition: ClassDefinition, catalog: SpellCatalog) -> list[Event]:
    """Remove the memorized copies a caster no longer has slots for.

    Call this after anything that lowers a caster's level, which in B/X means energy drain. The
    caster's slots shrink with the level, and the spells they had prepared may no longer fit. This
    call removes the extra copies. [`drain_levels`][osrlib.core.classes.drain_levels] calls it for
    you. If you lower a caster's level some other way, call it yourself, or the caster keeps spells
    they no longer have slots for.

    When everything still fits, nothing changes, so you can call this after any level change. Each
    spell level is counted separately: a caster who lost a second-level slot forgets a second-level
    spell and keeps their first-level ones.

    The SRD doesn't say which copy goes, so osrlib removes the most recently prepared copies first,
    the ones at the end of the caster's `memorized_spells`. That choice depends only on the list,
    so it's the same on every replay.

    Args:
        caster: The [`Caster`][osrlib.core.creature.Caster] who lost levels, which a
            [`Character`][osrlib.core.character.Character] satisfies. Copies are removed from its
            `memorized_spells`.
        definition: The caster's class, as a
            [`ClassDefinition`][osrlib.core.classes.ClassDefinition] from
            [`load_classes`][osrlib.data.load_classes]. Its row for the caster's new level gives
            the slot counts.
        catalog: The spell catalog, from [`load_spells`][osrlib.data.load_spells], used to look up
            the level of each memorized spell.

    Returns:
        One [`SpellForgottenEvent`][osrlib.core.events.SpellForgottenEvent] per copy removed, the
        last-prepared copy first. Empty when everything still fits.

    Raises:
        ValueError: If a memorized copy has a spell id that isn't in `catalog`. The message
            includes the id. Pass the catalog the copies came from.

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
    """Facts about a cast's situation that osrlib can't work out, which you state for the rules to use.

    Construct one and pass it to [`validate_cast`][osrlib.core.spells.validate_cast],
    [`cast_spell`][osrlib.core.spells.cast_spell],
    [`validate_scroll_cast`][osrlib.core.spells.validate_scroll_cast], or
    [`cast_from_scroll`][osrlib.core.spells.cast_from_scroll]. Every field is optional. The default
    context states nothing, which is right when none of these facts applies.

    A referee at a table answers these questions, and osrlib has nothing to answer them from. It has
    no map, so it can't work out how far away the target is. It has no model of restraints, so it
    can't tell that the caster is tied up. It records no cause of death, so it can't tell that the
    target died of poison.

    A rule whose field you leave unset doesn't apply. Range goes unchecked if you give no
    distance, and *raise dead* raises nobody if you give no number of days since death.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.core.spells import CastContext, MemorizedSpell, caster_profile, memorize_spells, validate_cast
        from osrlib.data import load_classes, load_spells

        catalog = load_spells()
        definition = load_classes().get("magic_user")
        zelia = create_character(
            name="Zelia",
            class_id="magic_user",
            alignment=Alignment.NEUTRAL,
            ruleset=Ruleset(),
            stream=RngStreams(master_seed=5).get(CHARACTER_CREATION_STREAM),
            starting_spell_ids=["sleep"],
        ).character
        memorize_spells(zelia, definition, catalog, [MemorizedSpell(spell_id="sleep")])
        profile = caster_profile(definition)
        sleep = catalog.get("sleep")

        # Her captors have gagged her, so she can't speak the words.
        refused = validate_cast(zelia, sleep, "hd_budget", profile=profile, context=CastContext(gagged=True))
        print([(rejection.code, rejection.params["restraint"]) for rejection in refused])
        # [('magic.cast.caster_restrained', 'gagged')]
        ```
    """

    model_config = ConfigDict(frozen=True)

    in_combat: bool = False
    """True when the cast happens in a fight.

    In combat, a touch spell like *cause light wounds* needs a melee attack roll and can miss.
    Outside combat, the caster touches the target without a roll.
    """

    distance_feet: int | None = None
    """How far the target is from the caster, in feet.

    Setting it turns on the range check in validation. The cast is rejected when the distance is
    more than the spell's [`RangeSpec`][osrlib.core.spells.RangeSpec] reaches at the caster level
    in use. Leave it unset, and no range check happens.
    """

    bound: bool = False
    """True when the caster is tied or held so that they can't gesture. The cast is rejected."""

    gagged: bool = False
    """True when the caster can't speak. The cast is rejected."""

    rounds_since_death: int | None = None
    """How many rounds ago the target died, for *neutralize poison*.

    That spell revives a character killed by poison within the last 10 rounds. Setting this field
    states that poison was the cause, because osrlib records no cause of death. Leave it unset for
    a death by any other cause.
    """

    days_since_death: int | None = None
    """How many days ago the target died, for *raise dead*.

    That spell can raise a character dead up to 4 days for each caster level above 7th. Leave it
    unset, and nobody is raised.
    """

    strength_tiers: dict[str, str] = {}
    """Entity ids mapped to `"augmented"` or `"giant"`, for *web*.

    A stronger creature breaks free of a web sooner. A creature you don't list breaks free at
    normal strength. You state it here because no osrlib effect gives giant strength.
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
    """Read an integer param from schema-validated data the type checker can't narrow by key."""
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
    caster: Caster,
    spell: SpellTemplate,
    mode: str,
    *,
    profile: CasterProfile | None,
    reversed: bool = False,
    targets: Sequence[Creature | str] = (),
    context: CastContext | None = None,
    ledger: EffectsLedger | None = None,
) -> list[Rejection]:
    """Ask whether a cast is legal, without casting it.

    Call this to decide whether to offer a cast at all, to grey out a spell in a menu, or to explain
    to a player why they can't cast it. Then call [`cast_spell`][osrlib.core.spells.cast_spell],
    which runs the same checks and raises if any fail, so a cast that passes here and is then made
    with the same arguments isn't refused.

    This function draws nothing from an RNG stream and changes neither the caster nor the ledger,
    so you can call it as often as you like. It doesn't check whether a target is the kind of
    creature the spell affects. Casting settles that, because a check that rejected *charm person*
    aimed at a disguised doppelganger would tell the player what the doppelganger is. Such a cast
    is legal: it resolves, spends the copy, and affects nobody.

    It checks these things:

    - The caster can cast. A dead, petrified, paralysed, asleep, silenced, feebleminded, weakened,
      bound, or gagged caster can't, and neither can a caster inside their *anti-magic shell*.
    - The caster has a memorized copy in the form asked for.
    - The spell has the form and the mode asked for.
    - The number of targets suits the mode. For *magic missile*, that is exactly one target per
      missile the caster's level gives.
    - The target is in range, but only when the context gives a distance.

    It doesn't check for a cleric's holy symbol. In the SRD, a cleric carries one as part of their
    class, not as a condition of casting, so a game that requires one checks the inventory itself.

    Args:
        caster: The [`Caster`][osrlib.core.creature.Caster], which a
            [`Character`][osrlib.core.character.Character] satisfies. Read, never written.
        spell: The [`SpellTemplate`][osrlib.core.spells.SpellTemplate] to cast, from
            [`SpellCatalog.get`][osrlib.core.spells.SpellCatalog.get].
        mode: Which usage of the spell, by its [`SpellMode.key`][osrlib.core.spells.SpellMode.key].
            A key the chosen form doesn't have is a rejection, not an exception.
        profile: The caster's [`CasterProfile`][osrlib.core.spells.CasterProfile], from
            [`caster_profile`][osrlib.core.spells.caster_profile]. Which memorized copies match
            depends on it: a divine caster's copy can be cast in either form, and an arcane caster's only in
            the form it was prepared in. `None` skips the memorized-copy check, which is how
            [`validate_scroll_cast`][osrlib.core.spells.validate_scroll_cast] calls this for a
            scroll.
        reversed: True to cast the spell's reversed form.
        targets: The candidate targets: [`Creature`][osrlib.core.creature.Creature] values, which a
            `Character` and a [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both
            satisfy, or location strings for spells your game attaches to a place rather than a
            creature. Only the number of targets is checked here.
        context: The [`CastContext`][osrlib.core.spells.CastContext] with the facts you state about
            the situation. `None` states nothing.
        ledger: The [`EffectsLedger`][osrlib.core.effects.EffectsLedger], checked for an effect on
            the caster that prevents casting, like *anti-magic shell*. With `None`, no such effect
            is found, so pass the ledger your game keeps.

    Returns:
        The reasons the cast is illegal, as [`Rejection`][osrlib.core.validation.Rejection] models
        with a `code` and `params`. Empty when the cast may go ahead. A caster who can't cast, or a
        form or mode the spell doesn't have, returns that one rejection alone. The other checks
        each add a rejection. The codes are `magic.cast.caster_incapacitated`,
        `magic.cast.caster_restrained`, `magic.cast.anti_magic_shell`,
        `magic.cast.not_reversible`, `magic.cast.unknown_mode`, `magic.cast.not_memorized`,
        `magic.cast.target_count`, and `magic.cast.out_of_range`.

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

    [`cast_spell`][osrlib.core.spells.cast_spell] and
    [`cast_from_scroll`][osrlib.core.spells.cast_from_scroll] return one. A result always describes
    a cast that happened, because an illegal cast raises instead. By the time you have a result,
    the copy is spent, and the targets, the caster, and the ledger have changed. Publish `events`
    to whatever shows the game, and read `manual`, `no_effect`, and `affected_ids` to tell the
    player what happened.

    Two outcomes need more than the events. `manual` means osrlib spent the copy and resolved
    nothing: `prose` has the SRD text, and your game or narrator decides what happened. `no_effect`
    means the cast resolved and reached nobody, because no candidate was eligible, every target
    saved or was immune, or a touch attack missed. In both cases the copy is gone. Nothing is
    refunded once a cast resolves, because a refund would tell the player something they had no
    way to know, like the fact that the creature they aimed at was immune.
    """

    model_config = ConfigDict(frozen=True)

    spell_id: str
    """The id of the spell that was cast, which [`SpellCatalog.get`][osrlib.core.spells.SpellCatalog.get] takes."""

    mode: str
    """The [`SpellMode.key`][osrlib.core.spells.SpellMode.key] of the mode that was cast."""

    reversed: bool = False
    """True when the reversed form was cast."""

    manual: bool = False
    """True when the mode is one osrlib doesn't resolve. Read `prose`, and decide what happens."""

    no_effect: bool = False
    """True when the cast resolved and reached nobody. The copy is still spent."""

    prose: str = ""
    """The SRD text of the mode that was cast, to show a player."""

    affected_ids: tuple[str, ...] = ()
    """The entity id of each creature the cast reached, in the order it was reached, each once.

    For a cast at a place, it has the location string you passed as a target instead. Empty when
    `no_effect` or `manual` is set.
    """

    events: tuple[Event, ...] = ()
    """Everything that happened, in order.

    The [`SpellCastEvent`][osrlib.core.events.SpellCastEvent] comes first. Its `target_ids` are the
    candidates you passed, not only the ones reached. Then come the events for each saving throw,
    each wound, and each effect attached or released. Publish these, and keep them for a replay.
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
    caster: Caster,
    spell: SpellTemplate,
    mode: str,
    *,
    profile: CasterProfile,
    reversed: bool = False,
    targets: Sequence[Creature | str] = (),
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

    This is the module's entry point. Before you call it, the caster needs a memorized copy, from
    [`memorize_spells`][osrlib.core.spells.memorize_spells], and you need the profile
    [`caster_profile`][osrlib.core.spells.caster_profile] returns. Afterwards, publish the
    [`CastResult`][osrlib.core.spells.CastResult]'s events, and keep the ledger you passed. It now
    contains any effect the spell left running, and
    [`EffectsLedger.advance`][osrlib.core.effects.EffectsLedger.advance] counts those effects down
    as game time passes. To cast a spell off a scroll, with no memorized copy, use
    [`cast_from_scroll`][osrlib.core.spells.cast_from_scroll] instead.

    The copy is spent whatever comes of the cast: when every target saves, when no target is
    eligible, and when a touch attack misses, because B/X has no rule for holding a spell back once
    it's cast. Call [`validate_cast`][osrlib.core.spells.validate_cast] first to find out whether
    the cast is legal without spending anything. An illegal cast raises here rather than returning
    a refusal.

    Which copy goes depends on the kind of caster. A divine caster spends any copy of the spell and
    chooses the form as they cast. An arcane caster chose the form when preparing, so the copy has
    to be in the form you ask for. Either way, the first matching copy in the caster's
    `memorized_spells` is spent.

    Casting any spell ends the caster's *invisibility* before the new spell resolves, the same way
    attacking does.

    The cast uses two RNG streams, kept separate so that each replays independently. Everything
    the cast rolls (targeting dice, damage dice, the touch attack, the targets' saving throws)
    comes from `stream`. Every roll for an attached effect, like a duration rolled as it
    attaches, comes from `effects_stream`.

    Args:
        caster: The [`Caster`][osrlib.core.creature.Caster] with a matching memorized copy, which a
            [`Character`][osrlib.core.character.Character] satisfies. That copy is removed from its
            `memorized_spells`.
        spell: The [`SpellTemplate`][osrlib.core.spells.SpellTemplate] to cast, from
            [`SpellCatalog.get`][osrlib.core.spells.SpellCatalog.get].
        mode: Which usage of the spell, by its [`SpellMode.key`][osrlib.core.spells.SpellMode.key].
        profile: The caster's [`CasterProfile`][osrlib.core.spells.CasterProfile], from
            [`caster_profile`][osrlib.core.spells.caster_profile].
        reversed: True to cast the spell's reversed form.
        targets: The candidate targets, in your order: [`Creature`][osrlib.core.creature.Creature]
            values, which a `Character` and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy, or location
            strings for spells your game attaches to a place. Casting drops the ineligible ones and
            then applies the mode's targeting to the rest, so for an area or group mode you can pass
            more candidates than the spell takes.
        context: The [`CastContext`][osrlib.core.spells.CastContext] with the facts you state about
            the situation. `None` states nothing.
        ledger: The [`EffectsLedger`][osrlib.core.effects.EffectsLedger] that lasting effects attach
            to. Pass the one your game keeps, not a new one, or the spell's effect is lost when you
            discard the new ledger.
        clock: The [`GameClock`][osrlib.core.clock.GameClock], read to record when attached effects
            begin and end.
        allocator: The [`IdAllocator`][osrlib.core.monsters.IdAllocator] that gives each attached
            effect its id. Pass the one your game keeps, so ids stay unique across the game.
        registry: Every live combatant by entity id, as
            [`Character`][osrlib.core.character.Character] and
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] objects. Resolution uses it to
            change creatures other than the targets, like the creature an ending effect was on.
        ruleset: The [`Ruleset`][osrlib.core.ruleset.Ruleset] in play, read for the optional rules
            that change attack rolls and damage.
        stream: The [`RngStream`][osrlib.core.rng.RngStream] every draw the cast makes comes from,
            conventionally `streams.get(`[`MAGIC_STREAM`][osrlib.core.spells.MAGIC_STREAM]`)`.
        effects_stream: The stream that attaching effects draw from, conventionally
            `streams.get(`[`EFFECTS_STREAM`][osrlib.core.effects.EFFECTS_STREAM]`)`.

    Returns:
        The [`CastResult`][osrlib.core.spells.CastResult]: who was reached, and every event, in
        order.

    Raises:
        ValueError: If [`validate_cast`][osrlib.core.spells.validate_cast] would return any
            rejection for the same arguments. The message lists the rejection codes. Nothing is
            spent, drawn, or changed before the error. Retrying with the same arguments fails the
            same way, so call `validate_cast` first and offer only the casts it allows.

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
    caster: Caster,
    spell: SpellTemplate,
    spell_mode: SpellMode,
    reversed: bool,
    targets: Sequence[Creature | str],
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
    # Casting breaks the caster's invisibility, before the new spell resolves.
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
    """A caster proxy: the scroll's reader, with `level` replaced by the scroll's caster level.

    Attribute reads and writes pass through to the reader, so conditions and modifiers apply to the
    real character. Only `level` is answered here, because a scroll spell resolves at the minimum
    class level able to cast it. The proxy is passed where a
    [`Caster`][osrlib.core.creature.Caster] is expected, so it declares that surface rather than
    leaving every member to `__getattr__`.
    """

    __slots__ = ("_reader", "level")

    # Forwarded to the reader at runtime, and declared here so the type checker sees the
    # caster surface the proxy stands in for.
    id: str | None
    name: str
    alignment: Alignment | None
    current_hp: int
    max_hp: int
    conditions: tuple[ActiveCondition, ...]
    stat_modifiers: tuple[ActiveModifier, ...]
    spell_book: tuple[str, ...]
    memorized_spells: tuple[MemorizedSpell, ...]
    level: int
    """The scroll's caster level, the only attribute the proxy doesn't forward to the reader."""

    def __init__(self, reader: Caster, level: int) -> None:
        object.__setattr__(self, "_reader", reader)
        object.__setattr__(self, "level", level)

    def __getattr__(self, name: str):
        return getattr(object.__getattribute__(self, "_reader"), name)

    def __setattr__(self, name: str, value: object) -> None:
        setattr(object.__getattribute__(self, "_reader"), name, value)


def minimum_caster_level(spell: SpellTemplate) -> int:
    """Return the lowest class level that could cast a spell at all.

    A spell read off a scroll resolves at this caster level, so
    [`validate_scroll_cast`][osrlib.core.spells.validate_scroll_cast] and
    [`cast_from_scroll`][osrlib.core.spells.cast_from_scroll] call it for you. Call it yourself to
    show what a scroll will do before anyone reads it, because a spell's damage, duration, and
    number of targets depend on caster level.

    The answer is the lowest level at which a class that uses the spell's list has a slot of the
    spell's level. It reads only the built-in classes from
    [`load_classes`][osrlib.data.load_classes], so a class you write doesn't change it.

    The SRD doesn't say at what level a scroll's spell was written. osrlib uses the lowest level
    that could cast it, so a scroll's spell is never stronger than the same spell cast by the
    lowest-level caster able to cast it. Neither function that reads a scroll takes a different
    level.

    Args:
        spell: The [`SpellTemplate`][osrlib.core.spells.SpellTemplate] to look up.

    Returns:
        The caster level, 1 or higher.

    Raises:
        ValueError: If no built-in class that uses the spell's list ever has a slot of the spell's
            level. That happens for a spell on a list you wrote, or a level no class reaches.

    Examples:
        ```python
        from osrlib.core.spells import minimum_caster_level
        from osrlib.data import load_spells

        catalog = load_spells()
        assert minimum_caster_level(catalog.get("magic_missile")) == 1
        assert minimum_caster_level(catalog.get("fire_ball")) == 5  # a fire ball scroll deals 5d6
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
    reader: Caster,
    spell: SpellTemplate,
    mode: str,
    *,
    reversed: bool = False,
    targets: Sequence[Creature | str] = (),
    context: CastContext | None = None,
    ledger: EffectsLedger | None = None,
) -> list[Rejection]:
    """Ask whether a scroll read is legal, without reading it.

    This is [`validate_cast`][osrlib.core.spells.validate_cast] for a spell read off a scroll, and
    [`cast_from_scroll`][osrlib.core.spells.cast_from_scroll] makes the same check before it
    resolves anything. Call it to decide whether to offer a read, and before every read. osrlib has
    no model of the scroll, so call this before your inventory code marks the scroll used:
    `cast_from_scroll` raises on an illegal read.

    It differs from `validate_cast` in the caster level it checks at. A scroll's spell resolves at
    the level [`minimum_caster_level`][osrlib.core.spells.minimum_caster_level] returns, whatever
    the reader's level, so this runs `validate_cast` for a caster of that level. The two checks
    that depend on caster level follow the scroll's level. One is how many targets a mode takes, so
    a 6th-level reader of a *magic missile* scroll passes one target, and three are refused. The
    other is how far a per-level range reaches. The memorized-copy check is skipped, because the
    scroll takes the place of the copy.

    It doesn't check two things that depend on the game rather than on the spell: whether this
    reader can read this scroll at all, which depends on a thief's scroll-reading ability and on
    whether the scroll is arcane or divine, and whether there is light to read by. In a session, the
    [`UseItem`][osrlib.crawl.commands.UseItem] command checks both.

    Args:
        reader: The [`Caster`][osrlib.core.creature.Caster] reading the scroll, which a
            [`Character`][osrlib.core.character.Character] satisfies. Read, never changed.
        spell: The [`SpellTemplate`][osrlib.core.spells.SpellTemplate] written on the scroll, from
            [`SpellCatalog.get`][osrlib.core.spells.SpellCatalog.get].
        mode: Which usage of the spell, by its [`SpellMode.key`][osrlib.core.spells.SpellMode.key].
            A key the chosen form doesn't have is a rejection, not an exception.
        reversed: True to ask about the spell's reversed form.
        targets: The candidate targets, [`Creature`][osrlib.core.creature.Creature] values or
            location strings, as [`cast_from_scroll`][osrlib.core.spells.cast_from_scroll] takes
            them. Only the number of targets is checked. Leave it out for a spell with no targets.
        context: The [`CastContext`][osrlib.core.spells.CastContext] with the facts you state about
            the situation. `None` states nothing.
        ledger: The [`EffectsLedger`][osrlib.core.effects.EffectsLedger], checked for an effect on
            the reader that prevents casting. With `None`, no such effect is found, so pass the
            ledger your game keeps.

    Returns:
        The reasons the read is illegal, as [`Rejection`][osrlib.core.validation.Rejection] models
        with a `code` and `params`, the same codes `validate_cast` returns. Empty when the read may
        go ahead, which means `cast_from_scroll` with the same arguments won't raise.

    Raises:
        ValueError: If [`minimum_caster_level`][osrlib.core.spells.minimum_caster_level] raises for
            `spell`, because no built-in class can cast it.

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
    reader: Caster,
    spell: SpellTemplate,
    mode: str,
    *,
    reversed: bool = False,
    targets: Sequence[Creature | str] = (),
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

    Use this rather than [`cast_spell`][osrlib.core.spells.cast_spell] when the spell is read off a
    scroll instead of cast from memory. The reader needs no memorized copy, no slot, and no ability
    to cast the spell. The legality checks, targeting, resolution, and
    [`CastResult`][osrlib.core.spells.CastResult] are otherwise the same as for `cast_spell`.

    The scroll is yours to track. Reading a spell uses it up, because the words vanish from the
    page, and osrlib has no model of the scroll, so mark the spell used in your inventory after
    this returns. Two other checks are yours too, or the [`UseItem`][osrlib.crawl.commands.UseItem]
    command's in a session: whether this reader can read this scroll at all, which depends on a
    thief's scroll-reading ability and on whether the scroll is arcane or divine, and whether there
    is light to read by.

    The whole read uses one caster level: the level
    [`minimum_caster_level`][osrlib.core.spells.minimum_caster_level] returns for the spell,
    whatever the reader's level. The checks and the resolution both use it, so a *fire ball* off a
    scroll always does 5d6 damage, and a per-level duration uses the same level. The two checks
    that depend on caster level follow the scroll, not the reader:

    - How many targets a mode takes. *Magic missile* takes one target per missile, and the
      scroll's level gives one missile, so even a 6th-level reader passes one target, and three are
      refused.
    - How far a spell reaches when its printed range grows per level, when you give a
      `distance_feet` in the [`CastContext`][osrlib.core.spells.CastContext].

    A condition, a modifier, or a wound the spell puts on its caster goes on the reader, as it
    would for a spell they had memorized.

    Args:
        reader: The [`Caster`][osrlib.core.creature.Caster] reading the scroll, which a
            [`Character`][osrlib.core.character.Character] satisfies. Nothing is removed from its
            memorized spells.
        spell: The [`SpellTemplate`][osrlib.core.spells.SpellTemplate] written on the scroll, from
            [`SpellCatalog.get`][osrlib.core.spells.SpellCatalog.get].
        mode: Which usage of the spell, by its [`SpellMode.key`][osrlib.core.spells.SpellMode.key].
        reversed: True to cast the spell's reversed form.
        targets: The candidate targets, in your order: [`Creature`][osrlib.core.creature.Creature]
            values, which a `Character` and a
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] both satisfy, or location
            strings for spells your game attaches to a place.
        context: The [`CastContext`][osrlib.core.spells.CastContext] with the facts you state about
            the situation. `None` states nothing.
        ledger: The [`EffectsLedger`][osrlib.core.effects.EffectsLedger] that lasting effects attach
            to. Pass the one your game keeps.
        clock: The [`GameClock`][osrlib.core.clock.GameClock], read to record when attached effects
            begin and end.
        allocator: The [`IdAllocator`][osrlib.core.monsters.IdAllocator] that gives each attached
            effect its id. Pass the one your game keeps.
        registry: Every live combatant by entity id, as
            [`Character`][osrlib.core.character.Character] and
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] objects.
        ruleset: The [`Ruleset`][osrlib.core.ruleset.Ruleset] in play.
        stream: The [`RngStream`][osrlib.core.rng.RngStream] the cast's draws come from,
            conventionally `streams.get(`[`MAGIC_STREAM`][osrlib.core.spells.MAGIC_STREAM]`)`.
        effects_stream: The stream that attached effects draw from, conventionally
            `streams.get(`[`EFFECTS_STREAM`][osrlib.core.effects.EFFECTS_STREAM]`)`.

    Returns:
        The [`CastResult`][osrlib.core.spells.CastResult]: who was reached, and every event, in
        order.

    Raises:
        ValueError: If [`validate_scroll_cast`][osrlib.core.spells.validate_scroll_cast] would
            return any rejection for the same arguments, or if no built-in class can cast `spell`.
            The message lists the rejection codes. Nothing is drawn or changed before the error.
            Call `validate_scroll_cast` first to get the reasons as rejections.

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


def disrupt_casting(caster: Caster, spell_id: str, *, reversed: bool = False) -> list[Event]:
    """Remove a spell a caster declared but was stopped from casting.

    In the SRD, a caster who loses initiative and is then successfully attacked, or fails a saving
    throw, before their turn loses the spell they were casting, as though they had cast it. Call
    this when that happens. Your game decides that it happened. In a game session's battle, the
    [`osrlib.crawl`][osrlib.crawl] package decides and calls this for you.

    Nothing is resolved and nothing is rolled. One memorized copy is removed, and one event is
    returned. The copy removed is the first one in the declared form, or, when there is none, the
    first copy of the spell in either form. That is how a divine caster's declared reverse costs
    them a normally prepared copy.

    Args:
        caster: The [`Caster`][osrlib.core.creature.Caster] who was interrupted, which a
            [`Character`][osrlib.core.character.Character] satisfies. One copy is removed from its
            `memorized_spells`.
        spell_id: The id of the spell they declared, as it appears in a
            [`MemorizedSpell`][osrlib.core.spells.MemorizedSpell] in the caster's
            `memorized_spells`. [The spell id
            index](https://mmacy.github.io/osrlib-python/reference/content-ids/spells/) lists the built-in ids.
        reversed: True when the declared cast was of the reversed form.

    Returns:
        A list with one [`SpellDisruptedEvent`][osrlib.core.events.SpellDisruptedEvent], to publish
        with whatever caused the disruption.

    Raises:
        ValueError: If the caster has no memorized copy of that spell. Nothing changes. The caster
            can't have declared a spell they hadn't memorized, so check how your game recorded the
            declaration.

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
    caster: Caster, mode: SpellMode, targets: Sequence[Creature | str], stream: RngStream
) -> tuple[list[Any], list[Event]]:
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
    eligible: list[Any] = [target for target in targets if _eligible(target, mode)]
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
    # like *protection from evil 10' radius*, covers the caster too.
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
    dice duration adds the bonus to the dice modifier, so `1d6 turns +1 per level` at level 3
    attaches `1d6+3`, which is still rolled on the effects stream when the effect attaches. A concentration
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
            # A web cast at a cell has the spell's duration, since the web
            # stays put, and stores the escape params on the effect. The crawl
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
        # and allows no save, and rolls damage separately. The whole thing resolves at
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
                # per caster level to shake it. After a failed save, the fear stays.
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
            # died of poison that many rounds ago. The session takes it from its
            # death records, and omits it for a death by any other means.
            if (
                getattr(target, "definition", None) is not None
                and has_condition(target, Condition.DEAD)
                and context.rounds_since_death is not None
                and context.rounds_since_death <= window
            ):
                # Revival undoes the poison death and stands the subject up at 1 hp.
                # RAW gives no hit point total, so osrlib sets the lowest one.
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
    level − 7), which is 0 days at level 7, following the rules as written. Revival sets 1 hp, removes `dead`, and
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

    *Mirror image* surrounds its caster with illusory copies. In the SRD, each attack on the caster
    destroys one image, even if the attack misses. Nothing in this module sees attacks, so call this
    once for each attack aimed at a caster who has images, in place of resolving that attack
    against the caster. In a game session's battle, the [`osrlib.crawl`][osrlib.crawl] package calls
    this for you.

    You can call it for anyone. For a target with no mirror images, it returns an empty list, so you
    don't need to check first. When the last image goes, the effect is released from the ledger, and
    the events from that release are in the returned list.

    Args:
        ledger: The [`EffectsLedger`][osrlib.core.effects.EffectsLedger] the cast attached the
            images' effect to.
        target_ref: The entity id of the caster being attacked.
        registry: Every live combatant by entity id, as
            [`Character`][osrlib.core.character.Character] and
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] objects. Read when the last
            image goes and the effect is released.
        clock: The [`GameClock`][osrlib.core.clock.GameClock], read for the current round, which
            the event records.

    Returns:
        An [`EffectTickedEvent`][osrlib.core.events.EffectTickedEvent] for the image destroyed, then
        the release events when it was the last one. Empty when the target has no images.

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

    [`turn_undead`][osrlib.core.spells.turn_undead] returns one. An illegal attempt raises instead,
    so a result always describes an attempt that was made, and it has no `accepted` flag. Read
    `outcomes` to explain the result, and `affected_ids` to find the monsters to move.
    """

    model_config = ConfigDict(frozen=True)

    roll: int
    """The 2d6 total the cleric rolled to turn.

    It is compared with the turning table's number for each kind of undead present, so one roll
    can turn some kinds and fail against others.
    """

    hd_pool: int | None = None
    """The second 2d6 total: how many Hit Dice of undead the attempt can affect.

    It is rolled when at least one kind came out `turn` or `destroy`. `None` when no kind did, and
    no second roll was made.
    """

    outcomes: tuple[TurningTypeOutcome, ...] = ()
    """One [`TurningTypeOutcome`][osrlib.core.events.TurningTypeOutcome] per kind of monster among the candidates.

    They are in the order each kind first appears among the candidates. Each `outcome` is `turn`
    for a kind that flees, `destroy` for a kind destroyed outright, `fail` for a kind that held,
    and `unaffected` for a kind that isn't undead.
    """

    affected_ids: tuple[str, ...] = ()
    """The entity ids of the monsters the attempt reached, as many as `hd_pool` paid for.

    A successful attempt reaches at least one living monster of a kind that came out `turn` or
    `destroy`, even when `hd_pool` is too small for it. Empty when no kind came out `turn` or
    `destroy`.
    """

    destroyed_ids: tuple[str, ...] = ()
    """The entity ids of the affected monsters that were destroyed rather than turned.

    They are dead permanently, and *raise dead* can't bring them back.
    """

    events: tuple[Event, ...] = ()
    """The [`UndeadTurnedEvent`][osrlib.core.events.UndeadTurnedEvent], then the events of what followed.

    That is a death for each monster destroyed and an attached `turned` condition for each one that
    fled. Publish these.
    """


def validate_turn_undead(cleric: Caster, definition: ClassDefinition) -> list[Rejection]:
    """Ask whether a character may attempt to turn undead, without rolling.

    Call this to decide whether to offer turning as an action at all. Then call
    [`turn_undead`][osrlib.core.spells.turn_undead], which runs the same checks and raises if any
    fail. Nothing here rolls dice or changes anything.

    Two things can stop an attempt. The character's class may not turn undead: a class can only if
    it has a `turn_undead` ability, so a class you write gets the power when you add that ability.
    Or the character may be unable to present a holy symbol, because they are dead, petrified,
    paralysed, asleep, or weakened. Weakened is the state *raise dead* leaves a character in, and it
    prevents class abilities.

    It doesn't check whether the character has a holy symbol. In the SRD, a cleric carries one as
    part of their class, not as a condition of turning, so a game that requires one checks the
    inventory itself.

    Args:
        cleric: The [`Caster`][osrlib.core.creature.Caster] attempting the turning, a
            [`Character`][osrlib.core.character.Character] whose class can turn undead. Read, never
            changed.
        definition: Their class, as a [`ClassDefinition`][osrlib.core.classes.ClassDefinition] from
            [`load_classes`][osrlib.data.load_classes].

    Returns:
        Why the attempt can't be made, as [`Rejection`][osrlib.core.validation.Rejection] models
        with a `code` and `params`. Empty when the attempt may go ahead. There is at most one,
        because the call stops at the first problem: `magic.turning.not_a_turner` for a class that
        can't turn undead, or `magic.turning.caster_incapacitated`, whose `condition` param names
        the condition.

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
    cleric: Caster,
    definition: ClassDefinition,
    candidates: Sequence[MonsterInstance],
    *,
    ledger: EffectsLedger,
    clock: GameClock,
    allocator: Any,
    registry: dict[str, Any],
    stream: RngStream,
) -> TurnUndeadResult:
    """Drive off or destroy undead with a cleric's holy symbol, the whole procedure in one call.

    Turning isn't a spell and uses no slot, so the caster's memorized spells don't change. Call
    [`validate_turn_undead`][osrlib.core.spells.validate_turn_undead] first, because an attempt the
    character can't make raises rather than returning a refusal. Afterwards, publish the result's
    events, and read `affected_ids` to move the undead that fled. How fleeing looks on your map is
    up to your game: this function only gives those monsters the `turned` condition.

    The attempt uses two rolls. First, one 2d6 roll is compared with the turning table once for
    each kind of monster among the candidates, not once per monster, because a whole kind either
    turns or doesn't. Some kinds turn automatically, some are destroyed outright, and some are
    beyond the cleric's power at their level.

    If any kind came out turned or destroyed, a second 2d6 roll gives a pool of Hit Dice. The
    monsters of those kinds are affected in order of Hit Dice, fewest first, until the pool can't
    pay for the next one. Monsters with the same Hit Dice keep the order you passed them in. The
    rest of the pool is lost rather than spent on anyone else, and a successful attempt always
    reaches at least one monster, even when the pool is too small for it.

    Monsters of a kind that came out `destroy` die permanently, and *raise dead* can't bring them
    back. The others get the `turned` condition through an effect that doesn't end on its own and
    can't be dispelled. Release it from the ledger when the encounter ends. In a session, ending
    the encounter releases it.

    Pass any monsters as candidates. A candidate that isn't undead comes out `unaffected` rather
    than causing a refusal, so an attempt can't be used to find out what is undead. A candidate
    that is already dead still counts toward its kind's result, but the Hit Dice pool skips it.

    Args:
        cleric: The [`Caster`][osrlib.core.creature.Caster] turning, which a
            [`Character`][osrlib.core.character.Character] satisfies. Its `level` picks the row of
            the turning table.
        definition: Their class, as a [`ClassDefinition`][osrlib.core.classes.ClassDefinition] from
            [`load_classes`][osrlib.data.load_classes]. It must have a `turn_undead` ability.
        candidates: The monsters present, as
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] objects. The order decides
            between monsters with the same Hit Dice when the pool runs out, so pass them in the same
            order every time to get the same result on a replay.
        ledger: The [`EffectsLedger`][osrlib.core.effects.EffectsLedger] the `turned` effects
            attach to. Pass the one your game keeps.
        clock: The [`GameClock`][osrlib.core.clock.GameClock], read to record when the attached
            effects begin.
        allocator: The [`IdAllocator`][osrlib.core.monsters.IdAllocator] that gives each attached
            effect its id.
        registry: Every live combatant by entity id, as
            [`Character`][osrlib.core.character.Character] and
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] objects.
        stream: The [`RngStream`][osrlib.core.rng.RngStream] both 2d6 rolls come from,
            conventionally `streams.get(`[`MAGIC_STREAM`][osrlib.core.spells.MAGIC_STREAM]`)`.
            The event shows both rolls to the player, because in B/X the player rolls them.

    Returns:
        The [`TurnUndeadResult`][osrlib.core.spells.TurnUndeadResult]: the rolls, the result for
        each kind, the monsters reached, and every event.

    Raises:
        ValueError: If [`validate_turn_undead`][osrlib.core.spells.validate_turn_undead] refuses the
            attempt, because the class can't turn undead, or the cleric is dead, petrified,
            paralysed, asleep, or weakened. The message lists the rejection code. Nothing is
            rolled or changed before the error. Call `validate_turn_undead` first to get the reason
            as a rejection.

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
