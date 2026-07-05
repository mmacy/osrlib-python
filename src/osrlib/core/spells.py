"""Spell memorization, casting, spell resolution, and turning undead.

Part of the core kernel: everything here runs standalone — no game session required —
and every random draw comes from a named, seeded RNG stream.

The OSE SRD's spell pages compile into a catalog of frozen
[`SpellTemplate`][osrlib.core.spells.SpellTemplate] models loaded by
[`load_spells`][osrlib.data.load_spells]. A template carries the page's presentation
data (duration, range, prose) alongside structured mechanics: one
[`SpellMode`][osrlib.core.spells.SpellMode] per castable usage, each naming its
targeting, saving throw, and — for the automated subset — a
[`SpellEffect`][osrlib.core.spells.SpellEffect] the casting interpreter executes.
Modes the kernel doesn't automate ship `manual=True` with the SRD prose: casting one
is a supported operation (the slot is consumed, the event is emitted), and the game
or narrator resolves the fiction.

The daily flow: [`memorize_spells`][osrlib.core.spells.memorize_spells] prepares a
caster's list (arcane casters choose from a spell book grown with
[`add_spell_to_book`][osrlib.core.spells.add_spell_to_book]), then
[`validate_cast`][osrlib.core.spells.validate_cast] checks legality and
[`cast_spell`][osrlib.core.spells.cast_spell] consumes the memorized copy and
resolves the mode. [`cast_from_scroll`][osrlib.core.spells.cast_from_scroll]
resolves an inscribed spell without a memorized copy, and
[`disrupt_casting`][osrlib.core.spells.disrupt_casting] loses one when a declared
cast is broken. Clerics also turn undead here:
[`validate_turn_undead`][osrlib.core.spells.validate_turn_undead], then
[`turn_undead`][osrlib.core.spells.turn_undead].

Casters are [`Character`][osrlib.core.character.Character] objects; targets arrive
duck-typed per the combatant convention (see [`osrlib.core.combat`][osrlib.core.combat])
as characters, [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] objects, or
location strings for effects a game attaches to places.

Reversed forms are entry data, not separate catalog entries: the nine concepts
printed as separate cleric and magic-user pages compile as two entries with
`_c`/`_mu` id suffixes because the pairs differ mechanically, while a reversible
spell's reverse lives on its entry as a
[`ReversedForm`][osrlib.core.spells.ReversedForm].

Every draw inside spell resolution — targeting dice, damage dice, touch-attack
rolls, cast-time forced saves, dispel survival rolls, and both turning rolls — comes
from the [`MAGIC_STREAM`][osrlib.core.spells.MAGIC_STREAM] stream, so spell results
replay independently of combat draws and vice versa. Effect-internal draws (rolled
durations at attach, tick-time saves such as the charm re-save) stay on the
[`EFFECTS_STREAM`][osrlib.core.effects.EFFECTS_STREAM] stream.
"""

# Import direction, mirroring the alignment.py lesson: the data loaders import these
# models and character.py imports the loaders, so this module never imports
# character.py — casting, memorization, and turning take caster objects duck-typed
# (the combatant convention), and character.py imports MemorizedSpell from here,
# never the reverse.

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
from osrlib.core.rng import RngStream
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
    "pop_mirror_image",
    "turn_undead",
    "validate_cast",
    "validate_turn_undead",
]

MAGIC_STREAM = "magic"
"""Stream key convention for spell-resolution draws: targeting, damage, cast-time saves, turning."""

EFFECT_KINDS = frozenset(
    {"damage", "heal", "cure", "condition", "modifiers", "kill", "restore_life", "dispel", "attach_only"}
)
"""The closed vocabulary of effect kinds the casting interpreter executes."""


class DurationSpec(BaseModel):
    """A parsed spell duration.

    `kind="fixed"` durations are `amount` (or `dice`, rolled at attach) counts of
    `unit`, plus `per_level` extra units per caster level (the additive bonus:
    *light (MU)* is 6 turns +1 per level; *hold person (MU)*'s `1 turn per level` is
    amount 0, per_level 1). `concentration` durations may carry a cap (`Concentration
    (up to 1 day)`). Anything unparseable keeps `kind="special"` with the raw string
    on the template — the parser never fails on prose.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["instant", "permanent", "concentration", "fixed", "special"]
    unit: TimeUnit | None = None
    amount: int | None = None
    dice: str | None = None
    per_level: int = 0
    concentration_cap_unit: TimeUnit | None = None
    concentration_cap_amount: int | None = None

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
    """A parsed spell range.

    `feet` carries the distance in feet for `feet`, `yards` (converted: `240 yards
    around the caster` is 720), and `per_level` kinds; `per_level_feet` is the extra
    feet per caster level (*cloudkill*-style `60' +10' per level` is feet 60,
    per_level_feet 10). `touch` covers `The caster or a creature touched` —
    self-targeting is allowed; presence forms and other prose are `special`.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["caster", "touch", "feet", "yards", "per_level", "special"]
    feet: int | None = None
    per_level_feet: int | None = None


class TargetingSpec(BaseModel):
    """One mode's targeting: the shared combat targeting mode plus its parameters.

    `count`/`count_dice` size `up_to_n` modes (*hold person*'s group mode is 1d4);
    `hd_budget_dice` sizes `hd_budget` modes (*sleep*'s 2d8); `hd_cap` bounds
    eligibility by Hit Dice (*sleep* mode 2's "4 HD or less", *charm monster*'s
    "3 HD or less"); `hd_min` bounds it from below (*charm monster*'s single mode
    takes "more than 3 HD"). Area `shape` and `dimensions` ship as structured data
    now; the battle machine maps its range-track geometry onto candidates.
    """

    model_config = ConfigDict(frozen=True)

    mode: TargetingMode
    count: int | None = None
    count_dice: str | None = None
    hd_budget_dice: str | None = None
    hd_cap: int | None = None
    hd_min: int | None = None
    shape: str | None = None
    dimensions: dict[str, int] = {}

    @field_validator("count_dice", "hd_budget_dice")
    @classmethod
    def _dice_must_parse(cls, value: str | None) -> str | None:
        if value is not None:
            parse(value)
        return value


class SaveSpec(BaseModel):
    """One mode's saving throw: the category, a modifier, and what a pass means.

    `modifier` is the target's adjustment (*hold person*'s single-target −2,
    *feeblemind*'s −4). `on_save="negates"` means a passed save avoids the effect;
    `"half"` halves damage, rounding down.
    """

    model_config = ConfigDict(frozen=True)

    category: SaveCategory
    modifier: int = 0
    on_save: Literal["negates", "half"] = "negates"


class SpellEffect(BaseModel):
    """The structured effect a mode's resolution executes — the closed vocabulary.

    `kind` names the interpreter behavior; `params` carries the per-spell
    scalars (damage dice, per-level scaling, exclusions, revival windows).
    `condition` is the condition a `condition` effect attaches; `cures_conditions`
    and `cures_effect_kinds` name what a `cure` effect releases; `modifiers` is the
    stat-modifier bundle a `modifiers` effect grants through the effects engine.
    """

    model_config = ConfigDict(frozen=True)

    kind: str
    condition: Condition | None = None
    cures_conditions: tuple[Condition, ...] = ()
    cures_effect_kinds: tuple[str, ...] = ()
    modifiers: tuple[ModifierSpec, ...] = ()
    params: dict[str, int | str | bool | tuple[int | str, ...]] = {}

    @field_validator("kind")
    @classmethod
    def _kind_must_be_known(cls, value: str) -> str:
        if value not in EFFECT_KINDS:
            raise ValueError(f"effect kind must be one of {sorted(EFFECT_KINDS)}, got {value!r}")
        return value


class SpellMode(BaseModel):
    """One castable usage of a spell.

    Multi-usage pages (*cure light wounds*, *light*) carry one mode per numbered
    usage. `key` is stable snake_case within the spell's form — casting names the
    mode by it. `manual=True` marks modes the kernel doesn't execute: casting one
    consumes the memorized copy and emits the cast event with the manual marker plus
    the prose, and the game or narrator resolves the fiction. Manual modes may omit
    `targeting` (the page carries no structured targeting); automated modes always
    carry targeting and an effect.
    """

    model_config = ConfigDict(frozen=True)

    key: str = Field(min_length=1)
    targeting: TargetingSpec | None = None
    save: SaveSpec | None = None
    effect: SpellEffect | None = None
    manual: bool = False
    prose: str = ""

    @model_validator(mode="after")
    def _automated_modes_carry_structure(self) -> SpellMode:
        if not self.manual and (self.effect is None or self.targeting is None):
            raise ValueError(f"mode {self.key!r} is automated but lacks targeting or an effect")
        return self


class ReversedForm(BaseModel):
    """A reversible spell's reversed version — entry data, never a separate entry.

    `duration_spec` overrides the normal form's duration when the page prints a
    dual form (`Instant / Permanent (curse)` splits across the two); `None` means
    the reverse shares the normal form's duration.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    prose: str = ""
    modes: tuple[SpellMode, ...] = Field(min_length=1)
    duration: str | None = None
    duration_spec: DurationSpec | None = None


class SpellTemplate(BaseModel):
    """A spell, compiled from its SRD page.

    Frozen SRD data: play never mutates a spell template. `id` is the slugified
    primary name (`fire_ball`, `cure_light_wounds`), with `_c`/`_mu` suffixes for
    the nine dual-page concepts. `spell_list` is an open, validated list id matched
    against [`CasterProfile.spell_list`][osrlib.core.spells.CasterProfile] — the
    Classic catalog carries `cleric` and `magic_user`, and Advanced lists are
    additive data. `duration` and `range` keep the printed strings; the specs are
    the parsed forms. `conjured_monsters` embeds stat blocks printed on the page
    (*sticks to snakes*' snake, validated as a full monster template);
    `conjured_monster_ids` references existing `monsters.json` entries (*conjure
    elemental*'s four 16-HD elementals).
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    spell_list: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    level: int = Field(ge=1, le=6)
    duration: str = Field(min_length=1)
    duration_spec: DurationSpec
    range: str = Field(min_length=1)
    range_spec: RangeSpec
    reversed_form: ReversedForm | None = None
    modes: tuple[SpellMode, ...] = Field(min_length=1)
    intro: str = ""
    conjured_monsters: tuple[MonsterTemplate, ...] = ()
    conjured_monster_ids: tuple[str, ...] = ()
    overrides_applied: tuple[str, ...] = ()

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
        """Return the mode with `key` on the normal or reversed form.

        Args:
            key: The mode key, e.g. `"damage"` or `"blind"`.
            reversed: True to look on the reversed form.

        Returns:
            The mode.

        Raises:
            ValueError: If the form or the key doesn't exist.
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
    """The loaded spell list, with id lookup and per-list filtering."""

    model_config = ConfigDict(frozen=True)

    spells: tuple[SpellTemplate, ...]

    @model_validator(mode="after")
    def _ids_must_be_unique(self) -> SpellCatalog:
        ids = [template.id for template in self.spells]
        if len(set(ids)) != len(ids):
            raise ValueError("spell ids must be unique")
        return self

    def get(self, spell_id: str) -> SpellTemplate:
        """Return the spell template for `spell_id`.

        Args:
            spell_id: The spell id, e.g. `"fire_ball"` or `"hold_person_c"` — see
                [the spell id index][spells-index].

        Returns:
            The spell template.

        Raises:
            ValueError: If no spell has that id.
        """
        for template in self.spells:
            if template.id == spell_id:
                return template
        raise ValueError(f"unknown spell id {spell_id!r}")

    def by_list(self, spell_list: str, level: int | None = None) -> tuple[SpellTemplate, ...]:
        """Return the spells on a class's list, optionally at one spell level.

        Args:
            spell_list: The list id, e.g. `"cleric"` or `"magic_user"`.
            level: A spell level to filter by, or `None` for the whole list.

        Returns:
            The matching templates, in catalog (id) order.
        """
        return tuple(
            template
            for template in self.spells
            if template.spell_list == spell_list and (level is None or template.level == level)
        )


class MemorizedSpell(BaseModel):
    """One memorized copy of a spell: the id and the form fixed at memorization.

    `spell_id` is a spell id from [`load_spells`][osrlib.data.load_spells] — see
    [the spell id index][spells-index]. Arcane casters fix the normal or reversed
    form when memorizing (the OSE SRD: "The normal or reversed form of a spell must
    be selected when the spell is memorized"); divine casters always memorize the
    normal form and choose at cast time, so their copies carry `reversed=False`.
    """

    model_config = ConfigDict(frozen=True)

    spell_id: str = Field(min_length=1)
    reversed: bool = False


class CasterProfile(BaseModel):
    """A class's casting nature, read from its `divine_magic`/`arcane_magic` tag."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["divine", "arcane"]
    spell_list: str


def caster_profile(definition: ClassDefinition) -> CasterProfile | None:
    """Return a class definition's casting profile, or `None` for non-casters.

    Args:
        definition: The [`ClassDefinition`][osrlib.core.classes.ClassDefinition],
            from [`load_classes`][osrlib.data.load_classes].

    Returns:
        The profile, from the `divine_magic`/`arcane_magic` tag's `spell_list` param.
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
    """An explicit string target is a location ref; anything else is an entity."""
    return target if isinstance(target, str) else _entity_id(target)


class MemorizationResult(BaseModel):
    """The outcome of a preparation: rejections, or the memorized event."""

    model_config = ConfigDict(frozen=True)

    rejections: tuple[Rejection, ...] = ()
    events: tuple[Event, ...] = ()

    @property
    def accepted(self) -> bool:
        """Whether the preparation was applied."""
        return not self.rejections


def memorize_spells(
    caster: Any, definition: ClassDefinition, catalog: SpellCatalog, selections: Sequence[MemorizedSpell]
) -> MemorizationResult:
    """Prepare a caster's daily spells — a full replacement of the memorized list.

    Models the daily preparation: the new list wholly replaces the old (partial
    top-ups are not a B/X operation). Divine casters choose freely from their class
    list and never fix the reversed form (they reverse at cast time, "by speaking
    the words and performing the gestures backwards"); arcane casters choose from
    their spell book and fix normal or reversed per copy at memorization. Duplicate
    selections are legal per RAW ("may opt to memorize the same spell twice"). The
    once-a-day/after-sleep/one-hour gates are exploration procedure owned by the
    crawl layer — standalone callers may call this freely, by design.

    Args:
        caster: The preparing caster: a [`Character`][osrlib.core.character.Character]
            with a casting class; its `memorized_spells` tuple is replaced.
        definition: The caster's [`ClassDefinition`][osrlib.core.classes.ClassDefinition].
        catalog: The loaded spell catalog, from [`load_spells`][osrlib.data.load_spells].
        selections: The prepared [`MemorizedSpell`][osrlib.core.spells.MemorizedSpell]
            copies, in memorization order (order is load-bearing: casting consumes
            the first matching copy and drain forgets newest-first).

    Returns:
        The rejections (nothing mutated) or the memorized event.
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
    """The outcome of a spell-book addition: rejections, or the book event."""

    model_config = ConfigDict(frozen=True)

    rejections: tuple[Rejection, ...] = ()
    events: tuple[Event, ...] = ()

    @property
    def accepted(self) -> bool:
        """Whether the spell was added."""
        return not self.rejections


def add_spell_to_book(
    caster: Any, definition: ClassDefinition, catalog: SpellCatalog, spell_id: str
) -> SpellBookResult:
    """Add a spell to an arcane caster's book — the referee-level growth surface.

    Covers mentoring and leveling; the fiction around it (the mentor's week, a lost
    book's rewriting costs) belongs to the game. The book holds, per spell level, at
    most the caster's current slot count at that level (the RAW "contains exactly
    the number of spells that the character is capable of memorizing", read per
    level). The book never auto-shrinks — it is a physical object; a drained
    character may hold a book over capacity and simply cannot add more until
    capacity catches up.

    Args:
        caster: The learning caster: a [`Character`][osrlib.core.character.Character]
            with an arcane class; its `spell_book` tuple grows.
        definition: The caster's [`ClassDefinition`][osrlib.core.classes.ClassDefinition].
        catalog: The loaded spell catalog, from [`load_spells`][osrlib.data.load_spells].
        spell_id: The spell id to add — see [the spell id index][spells-index].

    Returns:
        The rejections (nothing mutated) or the book-updated event.
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
    slots = definition.row(caster.level).spell_slots
    capacity = slots[template.level - 1] if template.level <= len(slots) else 0
    held = sum(1 for held_id in caster.spell_book if catalog.get(held_id).level == template.level)
    if held >= capacity:
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
    """Forget memorized copies in excess of the caster's (shrunk) slots.

    The drain interplay: after a level drop, for each spell level where the
    memorized count exceeds the new slot count, excess copies are forgotten
    newest-first (highest tuple index). RAW is silent on which copies go; osrlib
    adopts newest-first because it keeps the rule deterministic without new state.

    Args:
        caster: The drained caster: a [`Character`][osrlib.core.character.Character];
            its `memorized_spells` tuple shrinks.
        definition: The caster's [`ClassDefinition`][osrlib.core.classes.ClassDefinition].
        catalog: The loaded spell catalog, from [`load_spells`][osrlib.data.load_spells].

    Returns:
        One forgotten event per dropped copy, newest first.
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
    """The caller-asserted situation a cast resolves under — the RAW referee surface.

    `in_combat` gates the touch-attack roll ("In combat, a melee attack roll is
    required"; outside combat the touch lands without a roll). `bound`/`gagged` are
    the OSE SRD's freedom restraints, which the kernel has no model for.
    `rounds_since_death` and `days_since_death` are the caller's attestations for
    *neutralize poison* and *raise dead* (the session supplies them from its death
    records) — the kernel has no cause-of-death model, so supplying
    `rounds_since_death` *is* the attestation that the target died of poison; omit
    it for any other death. `strength_tiers` maps entity ids to
    `"augmented"`/`"giant"` for *web*'s faster escape tiers (caller-asserted until
    such effects exist).
    """

    model_config = ConfigDict(frozen=True)

    in_combat: bool = False
    distance_feet: int | None = None
    bound: bool = False
    gagged: bool = False
    rounds_since_death: int | None = None
    days_since_death: int | None = None
    strength_tiers: dict[str, str] = {}


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
    """Read an integer param — schema-validated data whose union the checker can't key by name."""
    return int(params.get(key, default))


def _mode_effect(mode: SpellMode) -> SpellEffect:
    """The mode's effect — model-validated as present on every automated mode."""
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

    Divine casters match any copy of the spell — the reversed flag is chosen freely
    at cast, whatever their spell list; arcane casters fixed the form at
    memorization, so the flag must match.
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
    """Validate a cast — the pure pre-phase: no RNG draws, no mutation.

    Checks caster capacity (dead, petrified, paralysed, asleep, silenced,
    feebleminded, or weakened cannot cast; `bound`/`gagged` arrive as context flags;
    an active *anti-magic shell* blocks the caster's own casting when a ledger is
    supplied), a matching memorized copy, form and mode legality, target counts
    (single takes one; *magic missile* takes exactly one target per missile), and
    range — only when the context supplies a distance, mirroring the combat
    convention. Category and immunity gates are **resolution** outcomes, never
    validator rejections, by design: casting *charm person* at a disguised
    doppelgänger must not be a zero-cost detector. A cleric's holy symbol is not
    checked — the OSE SRD states carrying one as a class edict, not a mechanical
    gate on any procedure.

    Args:
        caster: The casting caster: a [`Character`][osrlib.core.character.Character].
        spell: The [`SpellTemplate`][osrlib.core.spells.SpellTemplate] to cast.
        mode: The mode key on the chosen form (see
            [`SpellTemplate.mode`][osrlib.core.spells.SpellTemplate.mode]).
        profile: The caster's [`CasterProfile`][osrlib.core.spells.CasterProfile],
            from [`caster_profile`][osrlib.core.spells.caster_profile] on the
            definition the caller holds — divine casters match any memorized copy
            (the reversed form is chosen at cast). `None` skips the memorized-copy
            check entirely, for scroll reads: the scroll is the copy.
        reversed: True to cast the reversed form.
        targets: The explicit target list, per the combatant convention (see
            [`osrlib.core.combat`][osrlib.core.combat]):
            [`Character`][osrlib.core.character.Character] or
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] objects, or
            location strings for effects games attach to places.
        context: The caller-asserted [`CastContext`][osrlib.core.spells.CastContext].
        ledger: The [`EffectsLedger`][osrlib.core.effects.EffectsLedger], consulted
            for the caster's own blocking effects.

    Returns:
        Structured rejections; empty when the cast may resolve.
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
    """A cast's outcome: the consumed copy, what it affected, and the events.

    `manual=True` means the kernel did the bookkeeping (the copy is spent, the cast
    event is emitted) and the game narrates the effect — `prose` carries the mode's
    SRD text for that. `no_effect` marks a resolved cast that affected nothing (an
    ineligible target, every save passed on a negating spell): the copy is still
    spent, never refunded — validation rejections are free, and refunding a resolved
    cast would leak hidden state such as an unseen target's immunity.
    """

    model_config = ConfigDict(frozen=True)

    spell_id: str
    mode: str
    reversed: bool = False
    manual: bool = False
    no_effect: bool = False
    prose: str = ""
    affected_ids: tuple[str, ...] = ()
    events: tuple[Event, ...] = ()


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
    """Cast a memorized spell: consume the copy, resolve the mode, return the events.

    Consumes the first matching memorized copy (lowest tuple index) whether or not
    the resolution ends up affecting anything — including a touch attack that misses
    (nothing in RAW holds the charge). Divine casters consume any copy of the spell
    and choose the form at cast time; arcane casters fixed the form at memorization.
    Casting anything releases the caster's own *invisibility* (RAW: attacking or
    casting breaks it). Manual modes emit the cast event with the manual marker and
    the kernel stops there — the game or narrator resolves the fiction.

    Spell-resolution draws (targeting dice, damage dice, touch-attack rolls,
    cast-time forced saves, dispel survival rolls) come from `stream` — the
    [`MAGIC_STREAM`][osrlib.core.spells.MAGIC_STREAM] convention; attach-time draws
    (rolled durations, *web* escape dice) come from `effects_stream` per the
    effects-engine convention, so the two subsystems replay independently.

    Args:
        caster: The casting caster: a [`Character`][osrlib.core.character.Character]
            with a matching memorized copy; its `memorized_spells` tuple shrinks by
            one copy.
        spell: The [`SpellTemplate`][osrlib.core.spells.SpellTemplate] to cast, from
            the catalog [`load_spells`][osrlib.data.load_spells] returns.
        mode: The mode key on the chosen form (see
            [`SpellTemplate.mode`][osrlib.core.spells.SpellTemplate.mode]).
        profile: The caster's [`CasterProfile`][osrlib.core.spells.CasterProfile],
            from [`caster_profile`][osrlib.core.spells.caster_profile] on the
            definition the caller holds.
        reversed: True to cast the reversed form.
        targets: The explicit target list, in the caller's order, per the combatant
            convention (see [`osrlib.core.combat`][osrlib.core.combat]):
            [`Character`][osrlib.core.character.Character] or
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] objects, or
            location strings for effects games attach to places.
        context: The caller-asserted [`CastContext`][osrlib.core.spells.CastContext].
        ledger: The [`EffectsLedger`][osrlib.core.effects.EffectsLedger] durations ride.
        clock: The [`GameClock`][osrlib.core.clock.GameClock].
        allocator: The id allocator for attached effects: an
            [`IdAllocator`][osrlib.core.monsters.IdAllocator].
        registry: Live combatants by entity id —
            [`Character`][osrlib.core.character.Character] and
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] objects the
            resolution may mutate.
        ruleset: The [`Ruleset`][osrlib.core.ruleset.Ruleset] in play.
        stream: The magic stream (the `"magic"` [`RngStream`][osrlib.core.rng.RngStream]).
        effects_stream: The effects stream, for attach-time dice.

    Returns:
        The cast outcome with its events.

    Raises:
        ValueError: If the cast is invalid — validate with
            [`validate_cast`][osrlib.core.spells.validate_cast] first; casting an
            unmemorized spell is programmer misuse.

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

        # One goblin target; the registry maps entity ids to the live objects.
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
        assert zelia.memorized_spells == ()  # the cast consumed the memorized copy
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
    """Resolve a validated cast whose cost is already paid — shared by memory and scroll."""
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

    Attribute reads and writes pass through to the reader, so conditions and
    modifiers land on the real character; only `level` is overridden — scroll
    spells resolve at the minimum class level able to cast the spell.
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
    """Return the minimum class level able to cast a spell, per the compiled progressions.

    RAW is silent on a scroll's caster level; osrlib adopts the least-power reading
    (a documented adaptation): the lowest level at which any class on the spell's
    list has a slot of the spell's level.

    Args:
        spell: The [`SpellTemplate`][osrlib.core.spells.SpellTemplate].

    Returns:
        The minimum caster level.

    Raises:
        ValueError: If no class on the spell's list ever gains a slot of that level.
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
    """Cast an inscribed spell from a scroll: the scroll is the copy, and it burns.

    Reuses [`validate_cast`][osrlib.core.spells.validate_cast]'s legality gates and
    the full mode-resolution and effects machinery, but skips the memorized-copy
    consume — "when a scroll is read, the words disappear", so the caller marks
    the inscribed spell spent. The spell resolves at the minimum class level able
    to cast it (the least-power reading — see
    [`minimum_caster_level`][osrlib.core.spells.minimum_caster_level]). Class-list
    gating (arcane readers for arcane scrolls, the thief's scroll-use ability) and
    the light requirement are the crawl's validation; the kernel resolves a legal
    read.

    Args:
        reader: The reading character: a
            [`Character`][osrlib.core.character.Character]. Conditions and modifiers
            from the resolution land on the reader; only its effective level is
            proxied.
        spell: The inscribed [`SpellTemplate`][osrlib.core.spells.SpellTemplate].
        mode: The mode key on the chosen form (see
            [`SpellTemplate.mode`][osrlib.core.spells.SpellTemplate.mode]).
        reversed: True to cast the reversed form (divine readers choose at cast).
        targets: The explicit target list, in the caller's order, per the combatant
            convention (see [`osrlib.core.combat`][osrlib.core.combat]):
            [`Character`][osrlib.core.character.Character] or
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] objects, or
            location strings.
        context: The caller-asserted [`CastContext`][osrlib.core.spells.CastContext].
        ledger: The [`EffectsLedger`][osrlib.core.effects.EffectsLedger] durations ride.
        clock: The [`GameClock`][osrlib.core.clock.GameClock].
        allocator: The id allocator for attached effects: an
            [`IdAllocator`][osrlib.core.monsters.IdAllocator].
        registry: Live combatants by entity id —
            [`Character`][osrlib.core.character.Character] and
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] objects the
            resolution may mutate.
        ruleset: The [`Ruleset`][osrlib.core.ruleset.Ruleset] in play.
        stream: The magic stream (the `"magic"` [`RngStream`][osrlib.core.rng.RngStream]).
        effects_stream: The effects stream, for attach-time dice.

    Returns:
        The cast outcome with its events.

    Raises:
        ValueError: If the cast is invalid — validate with
            [`validate_cast`][osrlib.core.spells.validate_cast] (`profile=None`)
            first.
    """
    context = context or CastContext()
    rejections = validate_cast(
        reader,
        spell,
        mode,
        profile=None,
        reversed=reversed,
        targets=targets,
        context=context,
        ledger=ledger,
    )
    if rejections:
        raise ValueError(f"illegal scroll cast: {[rejection.code for rejection in rejections]}")
    caster = _ScrollReader(reader, minimum_caster_level(spell))
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
    """Disrupt a declared casting: the copy is lost "as if it had been cast".

    The trigger — lost initiative, then successfully attacked or failed a save
    before acting — is detected by the battle machine; the kernel resolves a
    disruption when told it happened. The exactly-matching copy is removed first;
    failing that, any copy of the spell (a divine caster's reversed declaration
    consumes a normal copy, mirroring the cast-time rule).

    Args:
        caster: The disrupted caster: a
            [`Character`][osrlib.core.character.Character]; its `memorized_spells`
            tuple shrinks by one copy.
        spell_id: The declared spell's id — see [the spell id index][spells-index].
        reversed: Whether the declared cast was the reversed form.

    Returns:
        The disruption event.

    Raises:
        ValueError: If no copy of the spell is memorized (programmer misuse).
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
    """The *hold/charm person* gate: any character, or a monster bearing the `person` category."""
    if getattr(target, "definition", None) is not None:
        return True
    template = getattr(target, "template", None)
    return template is not None and "person" in template.categories


def _is_arcane_caster(target: Any) -> bool:
    """The *feeblemind* gate: a target whose class bears the `arcane_magic` tag."""
    definition = getattr(target, "definition", None)
    if definition is None:
        return False
    profile = caster_profile(definition)
    return profile is not None and profile.kind == "arcane"


def _monster_hit_dice(target: Any) -> Any | None:
    template = getattr(target, "template", None)
    return template.hit_dice if template is not None else None


def _eligible(target: Any, mode: SpellMode) -> bool:
    """Resolve a mode's eligibility gates — resolution outcomes, never rejections."""
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
        # *Sleep* mode 1: "a single creature with 4+1 Hit Dice" — pinned as a
        # monster with HD count `hd_count` and a positive fixed modifier.
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

    Eligibility filtering happens inside resolution (never as a rejection), so
    ineligible candidates consume no HD budget and no group-count slot — a wight in
    a *sleep* candidate list simply isn't selected.
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
    # Area modes: every supplied candidate; a radius ward centered on the caster
    # (*protection from evil 10' radius*) covers the caster too.
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
    """Roll a mode's saving throw; returns `(passed, events)`.

    Spell saves always pass `magical=True` (the WIS modifier applies) and carry the
    mode's modifier and the effect's element (energy `auto_save` defenses resolve
    through the existing pipeline unchanged).
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
    """Scale a per-level dice expression (`1d6` per level at level 5 → `5d6`)."""
    parsed = parse(expression)
    count = parsed.count * caster_level
    modifier = parsed.modifier * caster_level
    suffix = f"{modifier:+d}" if modifier else ""
    return f"{count}d{parsed.sides}{suffix}"


def _resolved_duration(
    spell: SpellTemplate, reversed: bool, caster_level: int, params: Mapping[str, Any]
) -> dict[str, Any]:
    """Build an effect definition's duration fields from the spell and overrides.

    Per-level durations are computed at cast: fixed amounts gain
    `per_level × caster level`, and dice durations fold the bonus into the dice
    modifier (`1d6 turns +1 per level` at level 3 attaches `1d6+3`), keeping the
    rolled-at-attach behavior on the effects stream. Concentration
    durations attach indefinite — concentration effects are released by the caller.
    Effect-param overrides win: `permanent`, `indefinite`, or explicit
    `duration_dice`/`duration_amount`/`duration_unit` (*cause disease*'s 2d12
    days).
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
    """The charm re-save interval by INT band: month = 30 days, week = 7.

    Monsters have no INT score and default to the middle weekly band
    (override-correctable per monster).
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
    """The params carried onto the attached effect: the per-spell data, minus consumed keys."""
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
            # A location-bound web (cast at a cell): the cell keeps the
            # spell's own duration — the web sits there — and the escape params
            # ride the effect for the crawl's enter hook, which attaches the
            # per-creature entangled countdown on entry (pinned).
            fields["params"] = {
                **fields["params"],
                "escape_dice": str(params["escape_dice"]),
                "escape_unit": str(params["escape_unit"]),
            }
            fields["condition"] = None
        else:
            # *Web*'s escape countdown by STR, pinned: normal strength rolls the
            # escape dice; the augmented and giant tiers are caller/context
            # assertions.
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
    """Dispatch one automated mode's resolution — the casting interpreter."""
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
    # Spell damage is magical and presents the `magic` key: a wight's
    # silver-or-magic gate admits *magic missile*, a gargoyle's magic-only gate
    # admits *fire ball*.
    source = DamageSource(
        keys=("magic",),
        element=element,
        magical=True,
        kind="spell",
        destructive=bool(params.get("destructive", False)),
    )
    if "missiles_base" in params:
        # *Magic missile*: one supplied target per missile (repeats stack); each
        # missile hits unerringly — no attack roll, no save (pinned) — and rolls
        # its own damage, resolved instantly at cast (the 1-turn duration is
        # holding prose, pinned).
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
            amount //= 2  # halving floors (pinned)
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
                # *Remove fear* versus magical fear: the subject saves with +1 per
                # caster level to shake it; a failed save keeps the fear.
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
            # The page's revival usage is titled "Characters" — only a Character is
            # revivable (pinned). The kernel has no cause-of-death model: supplying
            # `rounds_since_death` IS the caller's attestation that the target died
            # of poison within that many rounds (the session supplies it from its
            # death records); omit it for any other death.
            if (
                getattr(target, "definition", None) is not None
                and has_condition(target, Condition.DEAD)
                and context.rounds_since_death is not None
                and context.rounds_since_death <= window
            ):
                # Revival, pinned: the poison death is undone and the subject
                # stands at 1 hp (RAW names no hit point total).
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
            # *Mirror image*'s 1d4 images live in effect state — attach-time
            # randomness, drawn from the effects stream per the convention.
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
    """*Dispel magic*: release dispellable effects; higher-level effects may survive.

    Per effect, when the recorded caster level exceeds the dispelling caster's, the
    effect survives on a d100 roll at or under 5% per level of deficit (RAW: "a 5%
    chance per level difference of *not* being dispelled"). Monster-inflicted
    effects are non-dispellable by construction; magic items are exempt.
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

    Restores a dead human or demihuman — any `Character` qualifies (all four Classic
    races), never a monster — dead no longer than 4 days × (caster level − 7), which
    is 0 days at level 7 (RAW-faithful). Revival sets 1 hp, removes `dead`, and
    attaches the weakness effect: cannot attack or cast, half movement, fixed at 14
    elapsed days as a simplification of RAW's "two full weeks of bed rest" (rest
    tracking is crawl procedure; games wanting strict bed-rest semantics extend or
    release via the ledger). Magical healing doesn't shorten it, per the page.
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
    """Destroy one mirror image — called by the game or battle machine per incoming attack.

    "Attacks on the caster destroy one of the mirror images (even if the attack
    misses)." When the last image pops, the effect is released.

    Args:
        ledger: The [`EffectsLedger`][osrlib.core.effects.EffectsLedger].
        target_ref: The mirrored caster's entity id.
        registry: Live combatants by entity id —
            [`Character`][osrlib.core.character.Character] and
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] objects the
            release may mutate.
        clock: The [`GameClock`][osrlib.core.clock.GameClock], stamped on the
            bookkeeping event.

    Returns:
        The pop (and final release) events; empty when no mirror-image effect is
        active.
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
    """A turning attempt's outcome: the rolls, per-type verdicts, and who was affected."""

    model_config = ConfigDict(frozen=True)

    roll: int
    hd_pool: int | None = None
    outcomes: tuple[TurningTypeOutcome, ...] = ()
    affected_ids: tuple[str, ...] = ()
    destroyed_ids: tuple[str, ...] = ()
    events: tuple[Event, ...] = ()


def validate_turn_undead(cleric: Any, definition: ClassDefinition) -> list[Rejection]:
    """Validate a turning attempt — the pure pre-phase.

    Turning is gated by the `turn_undead` class-ability tag; an incapacitated cleric
    (dead, petrified, paralysed, asleep) cannot present the symbol, and a `weakened`
    one cannot turn — the raise-dead weakness bans class abilities ("cannot attack,
    cast spells, or use other class abilities"). The holy symbol itself is *not* a
    precondition: the OSE SRD states carrying one as a class edict, not a mechanical
    gate; games wanting the stricter reading check inventory themselves.

    Args:
        cleric: The turning character: a [`Character`][osrlib.core.character.Character].
        definition: The character's [`ClassDefinition`][osrlib.core.classes.ClassDefinition].

    Returns:
        Structured rejections; empty when the attempt may be rolled.
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
    """Turn undead — the full procedure, one call.

    One 2d6 turn roll (the magic stream) is compared per candidate *type* against
    that type's turning-table cell — `—` types are unaffected, number types succeed
    when the roll meets the threshold, `T`/`D` succeed automatically. If any type
    succeeded, one 2d6 HD-pool roll follows; eligible monsters are affected
    lowest-HD-first (stable input order on ties), each costing its HD count
    (minimum 1, fixed bonuses dropped — the *sleep* convention); the pool stops at
    the first unaffordable monster (RAW: excess Hit Dice "are wasted", not
    reallocated); at least one undead is always affected on a successful turn even
    when the pool rolls short (RAW minimum effect), resolved as the cheapest
    eligible monster. Affected monsters whose column says `D` die permanently
    ("instantly and permanently annihilated"); the rest gain the `turned` condition
    via an indefinite, non-dispellable effect the encounter releases (flee behavior
    is the battle machine's).

    Only monsters bearing the `undead` category are candidates; non-undead in the
    list resolve as unaffected rather than rejecting, so a turning attempt never
    doubles as a free undead detector.

    Args:
        cleric: The turning character: a [`Character`][osrlib.core.character.Character].
        definition: The character's [`ClassDefinition`][osrlib.core.classes.ClassDefinition]
            (the `turn_undead` tag).
        candidates: The encounter's monsters, in stable order:
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] objects.
        ledger: The [`EffectsLedger`][osrlib.core.effects.EffectsLedger] the
            `turned` condition attaches through.
        clock: The [`GameClock`][osrlib.core.clock.GameClock].
        allocator: The id allocator for the attached effect: an
            [`IdAllocator`][osrlib.core.monsters.IdAllocator].
        registry: Live combatants by entity id —
            [`Character`][osrlib.core.character.Character] and
            [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] objects.
        stream: The magic stream — the player rolls turning dice in B/X, so both
            rolls are player-visible on the event.

    Returns:
        The turning outcome with its events.

    Raises:
        ValueError: If the character cannot turn — validate with
            [`validate_turn_undead`][osrlib.core.spells.validate_turn_undead] first.
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
            break  # excess is wasted, not reallocated (pinned)
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
