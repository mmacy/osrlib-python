"""Read what the rules did: the event base class, the emission contract, and the core rules' own event types.

Every rules call in osrlib returns events rather than printing anything, and this module defines the base class
they all share along with the events the core rules emit themselves. You receive these, you don't construct
them: an attack, a cast, a clock advance each hand you a list, and what your program does with that list is the
game.
[`osrlib.crawl.events`][osrlib.crawl.events] adds the crawl's own types on the same base, and
[`parse_any_event`][osrlib.crawl.events.parse_any_event] reads back a log that contains both.

Three things are true of every event, and they are what you can build on.

An event has structured fields and a message code, never a sentence. A code is a dotted lowercase string
namespaced by subsystem, like `combat.attack.hit`, and it names the outcome. Turn one into English with
[`format_message`][osrlib.messages.format_message], or write your own formatter, or hand the fields to a
narrator. The events themselves stay language-free, so any of those work.

An event has a visibility, because B/X hides some rolls from the players on purpose. Monster hit points and
morale rolls are the referee's business. Filter on
[`Visibility`][osrlib.core.events.Visibility] before you show a log to a player, or let
[`GameSession.view`][osrlib.crawl.session.GameSession.view] do it for you.

An event tolerates growth. Within a `schema_version` the event schema only gains things, unknown fields are
ignored rather than rejected, and an `event_type` your copy of the library has never heard of parses to `None`
instead of raising. Write your consumer to skip what it doesn't recognize and a log from a newer release still
replays.

Each class declares an `event_type`, a fixed string that names the type on the wire. Discriminate on that, not on
the code: one class can use several codes, since the code names the outcome and the type names the shape.
[`KernelEvent`][osrlib.core.events.KernelEvent] is the union pydantic discriminates,
[`KERNEL_EVENT_CLASSES`][osrlib.core.events.KERNEL_EVENT_CLASSES] is the same set as a tuple you can iterate, and
[`parse_event`][osrlib.core.events.parse_event] turns a saved mapping back into the right class.

Typical usage:

```python
from osrlib.core.events import DamageDealtEvent, MoraleCheckedEvent, Visibility, parse_event
from osrlib.messages import format_message

# A rules call hands you events like these.
log = [
    DamageDealtEvent(target_id="monster-0001", attacker_id="hild", amount=5, rolls=(5,)),
    MoraleCheckedEvent(code="combat.morale.held", subject="goblins", score=8, roll=6),
]

# The players see their own half of it.
player_lines = [format_message(event) for event in log if event.visibility is Visibility.PLAYER]
assert player_lines == ["monster-0001 takes 5 damage from hild."]

# And the whole log round-trips through JSON and back.
restored = [parse_event(event.model_dump(mode="json")) for event in log]
assert restored == log
```
"""

import re
from collections.abc import Mapping
from enum import StrEnum
from functools import cache
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator, model_validator

__all__ = [
    "AttackRolledEvent",
    "ConditionGainedEvent",
    "ConditionRemovedEvent",
    "DamageAbsorbedEvent",
    "DamageDealtEvent",
    "DeathEvent",
    "EffectAttachedEvent",
    "EffectExpiredEvent",
    "EffectReleasedEvent",
    "EffectTickedEvent",
    "EquipmentDestroyedEvent",
    "Event",
    "HealingAppliedEvent",
    "HitPointsReportedEvent",
    "InitiativeRoll",
    "InitiativeRolledEvent",
    "KERNEL_EVENT_CLASSES",
    "KernelEvent",
    "LevelDrainedEvent",
    "MagicDispelledEvent",
    "MonsterRevivedEvent",
    "MoraleCheckedEvent",
    "PreparedSpell",
    "ReactionRolledEvent",
    "SavingThrowRolledEvent",
    "SpellBookUpdatedEvent",
    "SpellCastEvent",
    "SpellDisruptedEvent",
    "SpellForgottenEvent",
    "SpellsMemorizedEvent",
    "TargetsSelectedEvent",
    "TurningTypeOutcome",
    "UndeadTurnedEvent",
    "Visibility",
    "parse_event",
]

_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")


class Visibility(StrEnum):
    """Who is allowed to see an event.

    Every event has one of these two values, and the split exists because B/X keeps some rolls behind the
    referee's screen. Filter a log on it before you show anything to a player, or call
    [`GameSession.view`][osrlib.crawl.session.GameSession.view], which builds the player's and the referee's
    views for you. A narrator playing referee reads both.

    The values are the lowercase strings and they serialize into every event, so a renamed value is a
    `schema_version` bump.

    Examples:
        ```python
        from osrlib.core.events import DamageDealtEvent, HitPointsReportedEvent, Visibility

        log = [
            DamageDealtEvent(target_id="monster-0001", amount=5),
            HitPointsReportedEvent(target_id="monster-0001", current_hp=2, max_hp=7),
        ]
        shown = [event.code for event in log if event.visibility is Visibility.PLAYER]
        assert shown == ["combat.damage.dealt"]  # the goblin's remaining hit points stay hidden
        ```
    """

    PLAYER = "player"
    """Anyone may see it. The players learn what happened in the fiction: a hit, a spell, a door opening."""

    REFEREE = "referee"
    """Only the referee may see it. These are the numbers B/X keeps hidden: monster hit points, morale and
    reaction rolls, surprise and detection rolls, and the effects ledger's bookkeeping."""


class Event(BaseModel):
    """What every osrlib event is: a frozen record of one thing the rules did.

    Use this as the type you annotate with. A rules call returns `list[Event]`, and the two fields declared here
    are the two you can read on anything in that list: the `code` that says what happened and the `visibility`
    that says who may see it. Everything else is on the subclass, which you reach by checking `event_type` or by
    an `isinstance` test.

    Subclass it to add your own events for rules osrlib doesn't cover. Add structured fields only, keep the
    code discipline, and give the class an `event_type` of its own if you want it to parse back from a saved
    log. A subclass that turns off `frozen`, or sets `extra` to anything but `"ignore"`, raises `TypeError` as
    soon as it is defined, because either one would break the guarantees above.

    Events never change once made, so keep a list of them as a log and read it whenever you like.

    Examples:
        ```python
        from pydantic import ValidationError

        from osrlib.core.events import DamageDealtEvent

        event = DamageDealtEvent(target_id="monster-0001", amount=5)
        assert event.code == "combat.damage.dealt"

        # A code the class doesn't declare is refused.
        try:
            DamageDealtEvent(code="combat.damage.absorbed", target_id="monster-0001", amount=5)
        except ValidationError as error:
            assert "combat.damage.dealt" in str(error)
        ```
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    allowed_codes: ClassVar[frozenset[str]] = frozenset()
    """The codes a subclass is allowed to use, checked when an instance is built. Empty on the base class,
    which accepts any well-formed code. Read it to find out what outcomes a type can report without constructing
    one, and declare it on a subclass of your own to get the same check."""

    code: str
    """What happened, as two or more lowercase segments separated by dots and namespaced by subsystem, like
    `combat.attack.hit`. This is what you branch on and what
    [`format_message`][osrlib.messages.format_message] looks up. Anything else raises a validation error."""

    visibility: Visibility
    """Who may see the event. Filter on it before showing a log to a player."""

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        """Reject a subclass whose `model_config` would weaken the emission contract."""
        super().__pydantic_init_subclass__(**kwargs)
        if cls.model_config.get("extra") != "ignore":
            raise TypeError(
                f"{cls.__name__} must keep extra='ignore': consumers ignore unknown fields, "
                "and the event schema grows additively within a schema_version"
            )
        if not cls.model_config.get("frozen"):
            raise TypeError(f"{cls.__name__} must stay frozen: events are immutable records of what happened")

    @field_validator("code")
    @classmethod
    def _code_must_be_dotted_snake_case(cls, value: str) -> str:
        if _CODE_PATTERN.fullmatch(value) is None:
            raise ValueError(
                "event code must be two or more dot-separated snake_case segments "
                f"(like 'combat.attack.hit'), got {value!r}"
            )
        return value

    @model_validator(mode="after")
    def _code_within_declared_set(self) -> Event:
        if self.allowed_codes and self.code not in self.allowed_codes:
            raise ValueError(f"{type(self).__name__} emits {sorted(self.allowed_codes)}, got {self.code!r}")
        return self


class InitiativeRoll(BaseModel):
    """One initiative roll on an [`InitiativeRolledEvent`][osrlib.core.events.InitiativeRolledEvent].

    You read these off the event's `entries` to show what each side or combatant rolled. Which of the two it is
    depends on the event's `mode`.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    """Who rolled: a side's name under side initiative, or a combatant's entity id under individual
    initiative."""

    rolls: tuple[int, ...]
    """Every d6 rolled for this participant, oldest first. A tie is re-rolled, so a tuple longer than one entry
    is the record of a tie and the rolls that broke it. The last entry is the one that counts."""

    modifier: int = 0
    """The adjustment added to the final roll. Under individual initiative a character's comes from dexterity
    and class, from [`participant_modifier`][osrlib.core.combat.participant_modifier], and a monster's is
    whatever the caller passed."""

    total: int
    """The last roll plus the modifier, which is the number the order was sorted on."""


class InitiativeRolledEvent(Event):
    """Who acts first this round, and what everyone rolled to get there.

    [`roll_initiative`][osrlib.core.combat.roll_initiative] emits one of these at the top of each combat round.
    Read `order` to know whose turn comes next, and `entries` to show the dice.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"combat.initiative.rolled"})
    """The only code this event uses."""

    event_type: Literal["initiative_rolled"] = "initiative_rolled"
    """The wire name for this event type."""

    code: str = "combat.initiative.rolled"
    """Fixed at `combat.initiative.rolled`."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the order of play is something everyone at the table can see."""

    mode: Literal["side", "individual"]
    """How initiative was rolled: `"side"` when one roll sets the order for a whole side, `"individual"` when
    every combatant rolled for itself."""

    entries: tuple[InitiativeRoll, ...]
    """One [`InitiativeRoll`][osrlib.core.events.InitiativeRoll] per participant, in the order they were passed
    in rather than the order they act."""

    order: tuple[str, ...]
    """The acting order for the round, first to last. The entries are side names or combatant entity ids,
    matching `mode`."""


class AttackRolledEvent(Event):
    """One attack roll and everything that went into it.

    [`attack_roll`][osrlib.core.combat.attack_roll] emits one of these for every swing, and
    [`resolve_attack`][osrlib.core.combat.resolve_attack] passes it on with the damage that followed. Read `code`
    for the outcome. The numeric fields are there so you can show the arithmetic.

    An attack against a helpless target needs no roll, and then `roll`, `total`, and `required` are all `None`.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {"combat.attack.hit", "combat.attack.missed", "combat.attack.auto_hit"}
    )
    """The three outcomes: a hit, a miss, or an automatic hit against a helpless target."""

    event_type: Literal["attack_rolled"] = "attack_rolled"
    """The wire name for this event type."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: attack rolls happen in the open."""

    attacker_id: str
    """The entity id of whoever attacked."""

    defender_id: str
    """The entity id of whoever was attacked."""

    attack_name: str
    """What was attacked with, by name: a weapon, a monster's natural attack, or `"unarmed"`."""

    roll: int | None = None
    """The d20 as it landed, before modifiers. `None` on an automatic hit."""

    modifier: int = 0
    """Everything added to the roll: strength, range band, magic, spells, the situation you attested."""

    total: int | None = None
    """The roll plus the modifier, which is the number compared against `required`. `None` on an automatic
    hit."""

    required: int | None = None
    """The total the attacker needed for a hit, worked out from its attack table and the defender's armour
    class. `None` on an automatic hit."""

    defender_ac: int | None = None
    """The defender's armour class as the attack saw it, after shields, spells, and the situation."""

    natural: int | None = None
    """Set to 1 or 20 when the unmodified d20 settled the outcome against what the total said, since a natural
    20 always hits and a natural 1 always misses. `None` otherwise, including when the natural roll and the total
    agreed."""


class DamageDealtEvent(Event):
    """Damage landed on a creature.

    [`deal_damage`][osrlib.core.combat.deal_damage] emits one of these for every packet of damage, whether it
    came from a weapon, a spell, or an effect like burning oil.

    It says how much was dealt and never how much the target has left, because B/X hides monster hit points. The
    remaining total rides the referee-visible
    [`HitPointsReportedEvent`][osrlib.core.events.HitPointsReportedEvent] that follows it.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"combat.damage.dealt"})
    """The only code this event uses."""

    event_type: Literal["damage_dealt"] = "damage_dealt"
    """The wire name for this event type."""

    code: str = "combat.damage.dealt"
    """Fixed at `combat.damage.dealt`."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: how hard something was hit is plain to see."""

    target_id: str
    """The entity id of whoever took the damage."""

    attacker_id: str | None = None
    """The entity id of whoever dealt it, or `None` when nothing did: a fall, a trap, a hazard."""

    amount: int
    """The hit points actually taken off, after every resistance and reduction."""

    rolls: tuple[int, ...] = ()
    """The individual dice the damage was rolled on, before modifiers. Empty for damage that was not rolled."""

    keys: tuple[str, ...] = ()
    """What the damage presented to the target's defenses: material and enchantment keys like `silver`,
    `magic`, or `holy`, plus the energy element when there was one."""

    non_regenerable: bool = False
    """True when this damage cannot be regenerated away, which happens when its element is one the target's
    regeneration is blocked by, as fire and acid are for a troll."""


class DamageAbsorbedEvent(Event):
    """A hit that landed and did nothing, because the target cannot be harmed by that kind of attack.

    This arrives instead of [`DamageDealtEvent`][osrlib.core.events.DamageDealtEvent] when the target's defenses
    shut the source out altogether: a creature that only silver or magic can touch, or one immune to the energy
    in play. No damage was rolled, so treat it as a hit that accomplished nothing rather than as a miss.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"combat.damage.absorbed"})
    """The only code this event uses."""

    event_type: Literal["damage_absorbed"] = "damage_absorbed"
    """The wire name for this event type."""

    code: str = "combat.damage.absorbed"
    """Fixed at `combat.damage.absorbed`."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the blow landing and doing nothing is something the players watch happen."""

    target_id: str
    """The entity id of the creature the attack could not harm."""

    attacker_id: str | None = None
    """The entity id of the attacker, or `None` when nothing was attacking."""

    keys: tuple[str, ...] = ()
    """What the attack presented to the defenses, which is what they turned away: keys like `silver` or
    `magic`, plus the energy element when there was one."""


class SavingThrowRolledEvent(Event):
    """A saving throw and how it went.

    [`saving_throw`][osrlib.core.combat.saving_throw] emits one of these whenever a creature gets a chance to
    avoid something. What passing or failing means belongs to whatever called for the save, so read this event
    alongside the ones around it.

    A creature whose defenses pass the save for it rolls nothing, and then `roll` and `required` are both `None`.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {"combat.save.passed", "combat.save.failed", "combat.save.auto"}
    )
    """The three outcomes: passed, failed, or passed automatically without a roll."""

    event_type: Literal["saving_throw_rolled"] = "saving_throw_rolled"
    """The wire name for this event type."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: saving throws are rolled in the open, monsters included."""

    target_id: str
    """The entity id of whoever saved."""

    category: str
    """Which of the five saving throws it was, as a
    [`SaveCategory`][osrlib.core.combat.SaveCategory] value: `death`, `wands`, `paralysis`, `breath`, or
    `spells`."""

    roll: int | None = None
    """The d20 as it landed, before modifiers. `None` on an automatic save."""

    modifier: int = 0
    """Everything added to the roll: a ward, a ring, the situation the caller attested."""

    required: int | None = None
    """The number the creature needed to meet or beat. `None` on an automatic save."""


class MoraleCheckedEvent(Event):
    """A side's nerve tested, and whether it held.

    [`check_morale`][osrlib.core.combat.check_morale] emits one of these when a fight gives a side reason to
    reconsider. A broken side flees or surrenders, and acting on that is the caller's job.

    Some morale scores never roll. A score of 2 or less never fights and a score of 12 or more never checks,
    and both report `combat.morale.exempt` with no roll. Read `held` to tell them apart, as on any other code:
    at 2 or less the side is already broken, and at 12 or more it holds.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {"combat.morale.held", "combat.morale.broke", "combat.morale.exempt"}
    )
    """The three codes: the side held, the side broke, or the score exempted it from rolling. The exempt code
    covers both exemptions, so `held` is what separates them."""

    event_type: Literal["morale_checked"] = "morale_checked"
    """The wire name for this event type."""

    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: the players find out a side has broken by watching it run."""

    subject: str
    """Whose morale was checked, by the side key the caller passed in."""

    score: int
    """The side's morale score. Anything from 3 to 11 rolls. A score of 2 or less exempts the side and leaves it
    broken, and 12 or more exempts it and leaves it holding."""

    roll: int | None = None
    """The 2d6 total, before modifiers. `None` when the score exempted the side from rolling."""

    modifier: int = 0
    """The situational adjustment applied, which B/X caps at plus or minus 2."""

    held: bool | None = None
    """Whether the side keeps fighting, on every code: a rolled check's verdict, `True` for a score of
    12 or more, and `False` for a score of 2 or less.
    [`check_morale`][osrlib.core.combat.check_morale] sets it on every event it emits, so render the
    outcome from this field rather than from the code and the score together. It is `None` on an event
    loaded from a save written before the field existed, because no migration fills it in."""


class ReactionRolledEvent(Event):
    """How a monster takes to meeting the party.

    [`roll_reaction`][osrlib.core.combat.roll_reaction] emits one of these at the start of an encounter that is
    not already a fight. The result tells you how to play the monster.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"encounter.reaction.rolled"})
    """The only code this event uses."""

    event_type: Literal["reaction_rolled"] = "reaction_rolled"
    """The wire name for this event type."""

    code: str = "encounter.reaction.rolled"
    """Fixed at `encounter.reaction.rolled`."""

    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: the players read a monster's mood from how it behaves, not from the dice."""

    roll: int
    """The 2d6 total, before the modifier."""

    modifier: int = 0
    """The speaking character's charisma reaction adjustment, when one applied."""

    total: int
    """The roll plus the modifier, which is what the reaction table was read with. A total outside 2 to 12 reads
    as the nearest end of the table."""

    result: str
    """The band the total fell in, as a [`ReactionResult`][osrlib.core.tables.ReactionResult] value: `attacks`,
    `hostile`, `uncertain`, `indifferent`, or `friendly`."""


class ConditionGainedEvent(Event):
    """A creature has fallen asleep, been turned to stone, or otherwise taken on a named state.

    [`grant_condition`][osrlib.core.effects.grant_condition] emits this, whether you called it yourself or
    [`EffectsLedger.attach`][osrlib.core.effects.EffectsLedger.attach] called it for you while attaching an
    effect that brings a condition with it. A creature immune to the condition produces no event at all.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"effects.condition.gained"})
    """The only code this event uses."""

    event_type: Literal["condition_gained"] = "condition_gained"
    """The wire name for this event type."""

    code: str = "effects.condition.gained"
    """Fixed at `effects.condition.gained`."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: a creature going rigid or falling asleep is plain to see."""

    target_id: str
    """The entity id of the creature that gained the condition."""

    condition: str
    """The condition, as a [`Condition`][osrlib.core.effects.Condition] value like `asleep` or `petrified`."""

    effect_id: str | None = None
    """The id of the effect that granted it and will take it back, or `None` for a state no effect owns, which
    in the core rules means `dead`."""


class ConditionRemovedEvent(Event):
    """A creature is out of a named state: awake again, unfrozen, no longer entangled.

    [`remove_condition`][osrlib.core.effects.remove_condition] emits this, whether you called it yourself or the
    effects ledger called it for you when the owning effect expired or was released.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"effects.condition.removed"})
    """The only code this event uses."""

    event_type: Literal["condition_removed"] = "condition_removed"
    """The wire name for this event type."""

    code: str = "effects.condition.removed"
    """Fixed at `effects.condition.removed`."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: a creature coming out of it is plain to see."""

    target_id: str
    """The entity id of the creature that lost the condition."""

    condition: str
    """The condition, as a [`Condition`][osrlib.core.effects.Condition] value."""

    effect_id: str | None = None
    """The id of the effect that had granted it, or `None` for a state no effect owned."""


class EffectAttachedEvent(Event):
    """An effect has started running on a creature, item, or location.

    [`EffectsLedger.attach`][osrlib.core.effects.EffectsLedger.attach] emits this first, before any condition or
    modifier the effect grants. Keep the `effect_id` if you plan to end the effect early through
    [`EffectsLedger.release`][osrlib.core.effects.EffectsLedger.release].
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"effects.effect.attached"})
    """The only code this event uses."""

    event_type: Literal["effect_attached"] = "effect_attached"
    """The wire name for this event type."""

    code: str = "effects.effect.attached"
    """Fixed at `effects.effect.attached`."""

    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: this is the ledger's bookkeeping. What the players notice is the condition the effect
    granted, which arrives as its own event."""

    effect_id: str
    """The id the ledger allocated for the effect, in the form `effect-0001`."""

    kind: str
    """The effect's kind, from its definition: `"sleep"`, `"regeneration"`, `"light"`, and so on."""

    target_ref: str
    """What it attached to: an entity id for a creature, or a location string for something that sits in a
    place."""

    expires_round: int | None = None
    """The absolute round the effect is due to end on, or `None` when it has no duration or is permanent."""


class EffectTickedEvent(Event):
    """An effect did its periodic thing this round.

    [`EffectsLedger.advance`][osrlib.core.effects.EffectsLedger.advance] emits this each time a regeneration
    heals or a charmed creature rolls its recurring save, and
    [`pop_mirror_image`][osrlib.core.spells.pop_mirror_image] emits it when an attack destroys one duplicate.
    What the tick did arrives as the events beside it: healing, a saving throw, a release.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"effects.effect.ticked"})
    """The only code this event uses."""

    event_type: Literal["effect_ticked"] = "effect_ticked"
    """The wire name for this event type."""

    code: str = "effects.effect.ticked"
    """Fixed at `effects.effect.ticked`."""

    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: this is the ledger's bookkeeping."""

    effect_id: str
    """The id of the effect that ticked."""

    kind: str
    """The effect's kind, from its definition."""

    target_ref: str
    """The entity id or location string the effect is attached to."""

    round: int
    """The absolute round the tick resolved on."""


class EffectExpiredEvent(Event):
    """An effect ran out of time and stopped.

    [`EffectsLedger.advance`][osrlib.core.effects.EffectsLedger.advance] emits this when the clock reaches an
    effect's expiry round. This event goes out first, then the condition and modifiers come off, and an effect
    with an expiry outcome, like a delayed poison, resolves that outcome last. An effect you ended early reports
    as [`EffectReleasedEvent`][osrlib.core.events.EffectReleasedEvent] instead.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"effects.effect.expired"})
    """The only code this event uses."""

    event_type: Literal["effect_expired"] = "effect_expired"
    """The wire name for this event type."""

    code: str = "effects.effect.expired"
    """Fixed at `effects.effect.expired`."""

    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: this is the ledger's bookkeeping. A crawl session translates the expiry of a light
    source into a player-facing event of its own."""

    effect_id: str
    """The id of the effect that ended."""

    kind: str
    """The effect's kind, from its definition."""

    target_ref: str
    """The entity id or location string the effect had been attached to."""

    round: int
    """The absolute round it expired on."""


class EffectReleasedEvent(Event):
    """An effect was ended early, before its time was up.

    [`EffectsLedger.release`][osrlib.core.effects.EffectsLedger.release] emits this: a *dispel magic* stripping
    an enchantment, a charmed creature making its save, a torch put out. An effect that ran out of time on its
    own reports as [`EffectExpiredEvent`][osrlib.core.events.EffectExpiredEvent] instead.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"effects.effect.released"})
    """The only code this event uses."""

    event_type: Literal["effect_released"] = "effect_released"
    """The wire name for this event type."""

    code: str = "effects.effect.released"
    """Fixed at `effects.effect.released`."""

    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: this is the ledger's bookkeeping."""

    effect_id: str
    """The id of the effect that was ended."""

    kind: str
    """The effect's kind, from its definition."""

    target_ref: str
    """The entity id or location string the effect had been attached to."""


class HealingAppliedEvent(Event):
    """Hit points restored, or refused.

    [`apply_healing`][osrlib.core.combat.apply_healing] emits this, and so does a regeneration tick inside
    [`EffectsLedger.advance`][osrlib.core.effects.EffectsLedger.advance]. Read `amount` rather than what was
    asked for: healing is capped at the hit points the creature was missing, and a cursed creature may have had
    it halved.

    Healing can also be refused outright, and then the code says blocked and `amount` is 0. A creature that's
    diseased takes no magical healing, and one that's weakened takes none at all.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"combat.healing.applied", "combat.healing.blocked"})
    """The two outcomes: healing landed, or a condition refused it."""

    event_type: Literal["healing_applied"] = "healing_applied"
    """The wire name for this event type."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: a wound closing is plain to see. The hit points behind it ride the referee-visible
    [`HitPointsReportedEvent`][osrlib.core.events.HitPointsReportedEvent]."""

    target_id: str
    """The entity id of whoever was healed."""

    amount: int
    """The hit points actually restored, after the cap and any halving. 0 when the healing was blocked."""

    source: str
    """Where the healing came from: `magical` for a spell or potion, `natural` for rest, `regeneration` for a
    regenerating creature's own tick."""


class DeathEvent(Event):
    """A creature has been killed.

    [`kill`][osrlib.core.effects.kill] emits this, whether death came from damage, from a failed save, or from
    an effect running out, and there it arrives after the
    [`ConditionGainedEvent`][osrlib.core.events.ConditionGainedEvent] for `dead` and before the hit point
    report.

    [`deal_damage`][osrlib.core.combat.deal_damage] emits a second one, with the permanent code, the first time
    a monster that is already dead takes enough damage of the kind it cannot regenerate to reach its full hit
    points. That one comes after the [`HitPointsReportedEvent`][osrlib.core.events.HitPointsReportedEvent] and
    has no condition event with it, because the monster was already carrying `dead`.

    The code tells you whether the death can be undone. An ordinary death can be, by magic or by regeneration.
    A permanent one cannot, which is what fire and acid do to a troll.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"combat.death.died", "combat.death.permanent"})
    """The two outcomes: an ordinary death, or one nothing can bring the creature back from."""

    event_type: Literal["death"] = "death"
    """The wire name for this event type."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: a creature falling is plain to see."""

    target_id: str
    """The entity id of whoever died."""


class EquipmentDestroyedEvent(Event):
    """What a victim was carrying burned with them.

    [`destroy_equipment`][osrlib.core.combat.destroy_equipment] emits this when a death destroys the body and
    everything on it: dragon breath, a *disintegrate*. An empty inventory produces no event.

    Under the ruleset's magic item death save, each magic item rolls to survive, and the ones that make it are
    listed separately. A crawl session drops them where the victim fell.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"combat.equipment.destroyed"})
    """The only code this event uses."""

    event_type: Literal["equipment_destroyed"] = "equipment_destroyed"
    """The wire name for this event type."""

    code: str = "combat.equipment.destroyed"
    """Fixed at `combat.equipment.destroyed`."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the loss is the players' to feel."""

    target_id: str
    """The entity id of the victim."""

    item_names: tuple[str, ...]
    """The display names of the items that were destroyed."""

    saved_items: tuple[str, ...] = ()
    """The instance ids of the magic items that passed their save and still exist. Empty when nothing survived,
    which is always the case when the ruleset has the magic item death save turned off."""


class LevelDrainedEvent(Event):
    """An undead creature's touch has taken levels, or taken everything.

    [`drain_levels`][osrlib.core.classes.drain_levels] emits this for a character and
    [`drain_monster_hd`][osrlib.core.combat.drain_monster_hd] for a monster, whose Hit Dice drain the same way.

    When the victim has nothing left to lose, at level 1 or one Hit Die, the drain kills instead, the code says
    slain, and a [`DeathEvent`][osrlib.core.events.DeathEvent] follows. The killing level counts as lost, so a
    spectre draining a level-2 fighter reports two levels gone.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"combat.drain.drained", "combat.drain.slain"})
    """The two outcomes: levels lost, or the victim drained to death."""

    event_type: Literal["level_drained"] = "level_drained"
    """The wire name for this event type."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: losing a level is the players' to know."""

    target_id: str
    """The entity id of the victim."""

    levels_lost: int
    """How many levels, or Hit Dice, the drain took."""

    new_level: int
    """What the victim is now, and 0 when the drain killed it."""

    hp_lost: int
    """The maximum hit points that went with the levels."""

    xp_after: int | None = None
    """The character's experience points after the drain, set by the draining monster's own experience policy:
    halfway between the old and new level thresholds, or the new level's minimum. `None` for a monster, whose
    Hit Dice are worth no experience points, and for a victim the drain killed."""

    spawn_consequence: str | None = None
    """The printed consequence of dying to this particular undead, like becoming a wight in 1d4 days under the
    control of the one that killed you. osrlib kills the victim, and turning them into something is yours to
    play. `None` when the source has no such consequence."""


class MonsterRevivedEvent(Event):
    """A monster the party thought dead has got back up.

    A regeneration tick inside [`EffectsLedger.advance`][osrlib.core.effects.EffectsLedger.advance] emits this
    when the countdown runs out, which for a troll is 2d6 rounds after it fell. The monster comes back on one
    hit point, and the `dead` condition comes off in the same breath. It stops happening once the damage the
    monster cannot regenerate, fire and acid for a troll, adds up to its full hit points.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"effects.regeneration.revived"})
    """The only code this event uses."""

    event_type: Literal["monster_revived"] = "monster_revived"
    """The wire name for this event type."""

    code: str = "effects.regeneration.revived"
    """Fixed at `effects.regeneration.revived`."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the players find out the hard way."""

    target_id: str
    """The entity id of the monster that came back."""


class HitPointsReportedEvent(Event):
    """Where a creature's hit points stand after something changed them.

    Anything that moves hit points emits one of these right after it: damage, healing, death, energy drain, a
    regeneration tick. Follow them and you always know the true state without reading the creature objects.

    This is the only event that reports a creature's standing current and maximum. The others report the
    change alone: [`DamageDealtEvent`][osrlib.core.events.DamageDealtEvent] the size of a hit,
    [`LevelDrainedEvent`][osrlib.core.events.LevelDrainedEvent] the maximum a drain took off. It's
    referee-visible on purpose, because B/X keeps monster hit points hidden, so show the players the change
    events instead.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"combat.state.hit_points"})
    """The only code this event uses."""

    event_type: Literal["hit_points_reported"] = "hit_points_reported"
    """The wire name for this event type."""

    code: str = "combat.state.hit_points"
    """Fixed at `combat.state.hit_points`."""

    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: monster hit points are hidden by design."""

    target_id: str
    """The entity id of the creature reported on."""

    current_hp: int
    """Hit points now, never below 0."""

    max_hp: int
    """Hit points at full, which energy drain can lower."""


class TargetsSelectedEvent(Event):
    """Who a spell, a breath weapon, or a thrown flask ended up catching.

    [`select_targets`][osrlib.core.combat.select_targets] emits this before the resolution that follows, so a log
    records which candidates a Hit Dice budget or an area actually reached rather than only who was standing
    nearby.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"combat.targeting.selected"})
    """The only code this event uses."""

    event_type: Literal["targets_selected"] = "targets_selected"
    """The wire name for this event type."""

    code: str = "combat.targeting.selected"
    """Fixed at `combat.targeting.selected`."""

    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: this is bookkeeping. What the targets then suffer arrives as its own events."""

    mode: str
    """How targets were chosen, as a [`TargetingMode`][osrlib.core.combat.TargetingMode] value: `self`,
    `single`, `up_to_n`, `hd_budget`, `area`, or `gaze`."""

    target_ids: tuple[str, ...]
    """The entity ids selected, in resolution order."""


class PreparedSpell(BaseModel):
    """One spell a caster has in memory, as it appears on a memorization event.

    You read these off a [`SpellsMemorizedEvent`][osrlib.core.events.SpellsMemorizedEvent]'s `prepared` tuple.

    A caster who prepares a reversible spell chooses which way round it goes at preparation time, not at casting
    time, so each prepared copy records the form it's locked into.
    """

    model_config = ConfigDict(frozen=True)

    spell_id: str
    """The spell's content id, like `"magic_missile"`."""

    reversed: bool = False
    """True when the copy was prepared in the spell's reversed form, as *cause light wounds* is the reverse of
    *cure light wounds*."""


class SpellsMemorizedEvent(Event):
    """A caster has finished preparing spells for the day.

    [`memorize_spells`][osrlib.core.spells.memorize_spells] emits one of these per preparation. Preparation
    replaces everything the caster had in memory, so `prepared` is the whole new memory rather than what was
    added to it.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"magic.memorize.prepared"})
    """The only code this event uses."""

    event_type: Literal["spells_memorized"] = "spells_memorized"
    """The wire name for this event type."""

    code: str = "magic.memorize.prepared"
    """Fixed at `magic.memorize.prepared`."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the caster's own memory is the player's to see."""

    caster_id: str
    """The entity id of the caster."""

    prepared: tuple[PreparedSpell, ...]
    """Every copy now in memory, as [`PreparedSpell`][osrlib.core.events.PreparedSpell] entries. A spell
    prepared twice appears twice."""


class SpellCastEvent(Event):
    """A spell was cast and its memorized copy spent.

    [`cast_spell`][osrlib.core.spells.cast_spell] and
    [`cast_from_scroll`][osrlib.core.spells.cast_from_scroll] emit this. What the spell then did arrives as the
    events beside it: saving throws, damage, conditions, effects, healing, deaths.

    A cast that found nothing to work on reports the no-effect code and still spends the copy. Refusing the cast
    instead would tell the player which targets were eligible, and B/X doesn't give that away for free.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"magic.cast.cast", "magic.cast.no_effect"})
    """The two outcomes: the spell landed, or every target was out of its reach."""

    event_type: Literal["spell_cast"] = "spell_cast"
    """The wire name for this event type."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: casting a spell is done out loud."""

    caster_id: str
    """The entity id of the caster."""

    spell_id: str
    """The spell's content id, like `"magic_missile"`."""

    mode: str
    """Which of the spell's modes was used, by the mode's own name. A spell with several modes, like one that
    can damage or heal, names the one that resolved."""

    reversed: bool = False
    """True when the spell was cast in its reversed form."""

    target_ids: tuple[str, ...] = ()
    """The entity ids the spell was aimed at. Empty for a spell that targets no creature."""

    manual: bool = False
    """True when osrlib didn't resolve the spell's effect, because the mode is one the rules leave to the
    table. The copy is spent and the outcome is yours to narrate from the spell's printed text."""


class SpellDisruptedEvent(Event):
    """A declared spell came to nothing, and the caster lost it anyway.

    Two things produce one, and `code` tells them apart.
    [`disrupt_casting`][osrlib.core.spells.disrupt_casting] emits `magic.cast.disrupted` when a caster who
    declared a spell is hit, or otherwise stopped, before it goes off. The battle round emits
    `magic.cast.fizzled` when it judges the declaration again in the magic phase, just before the spell would
    resolve, and a check the declaration passed at the top of the round no longer passes, because the phases
    before it changed what that check reads. The reachable case is an ally's *silence 15' radius* anchoring on
    the party's cell earlier in the same magic phase. `reason` carries the rejection code behind a fizzle.

    Either way nothing resolved and the memorized copy is gone, exactly as if the spell had been cast, so tell
    the player the spell failed and the prepared copy is spent. A scroll read that fizzles spends the scroll
    the same way, and the caster loses no memorized copy, because a read never used one.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"magic.cast.disrupted", "magic.cast.fizzled"})
    """The two codes this event uses: `magic.cast.disrupted` and `magic.cast.fizzled`."""

    event_type: Literal["spell_disrupted"] = "spell_disrupted"
    """The wire name for this event type."""

    code: str = "magic.cast.disrupted"
    """Which of the two failures this is, defaulting to `magic.cast.disrupted`."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the caster's spell visibly fails."""

    caster_id: str
    """The entity id of the caster."""

    spell_id: str
    """The spell's content id."""

    reversed: bool = False
    """True when the lost copy was the spell's reversed form."""

    reason: str | None = None
    """Why a `magic.cast.fizzled` spell failed: the first rejection code the magic phase's re-check produced.

    An ally's silence on the party's cell reads `magic.cast.silenced_area`. Match on this rather than on any
    text, the way you match on a [`Rejection`][osrlib.core.validation.Rejection]'s own code, and
    [the rejection code reference][rejection-codes] lists what each one means. `None` on a
    `magic.cast.disrupted` event, which needs no reason beyond the blow that landed, and on a log written
    before the field existed.
    """


class SpellForgottenEvent(Event):
    """A memorized spell slipped away because the caster no longer has room for it.

    [`forget_excess_memorized`][osrlib.core.spells.forget_excess_memorized] emits one of these per lost copy,
    which happens when energy drain takes levels and the caster's allowance shrinks below what is in memory. One
    event names one copy, so a caster who lost two gets two events.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"magic.memory.forgotten"})
    """The only code this event uses."""

    event_type: Literal["spell_forgotten"] = "spell_forgotten"
    """The wire name for this event type."""

    code: str = "magic.memory.forgotten"
    """Fixed at `magic.memory.forgotten`."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the caster's memory is the player's to track."""

    caster_id: str
    """The entity id of the caster."""

    spell_id: str
    """The spell's content id."""

    reversed: bool = False
    """True when the forgotten copy was the spell's reversed form."""


class SpellBookUpdatedEvent(Event):
    """A new spell has gone into an arcane caster's book.

    [`add_spell_to_book`][osrlib.core.spells.add_spell_to_book] emits this. A spell in the book is one the caster
    may prepare. Getting it there is a separate matter from preparing it, which reports as
    [`SpellsMemorizedEvent`][osrlib.core.events.SpellsMemorizedEvent].
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"magic.book.added"})
    """The only code this event uses."""

    event_type: Literal["spell_book_updated"] = "spell_book_updated"
    """The wire name for this event type."""

    code: str = "magic.book.added"
    """Fixed at `magic.book.added`."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the book belongs to the player."""

    caster_id: str
    """The entity id of the caster whose book gained the spell."""

    spell_id: str
    """The spell's content id."""


class TurningTypeOutcome(BaseModel):
    """How one kind of undead fared against a turning attempt.

    Turning is read off a table once per kind of undead present, not once per monster, and these are those
    readings. You find them on an [`UndeadTurnedEvent`][osrlib.core.events.UndeadTurnedEvent]'s `types`.
    """

    model_config = ConfigDict(frozen=True)

    template_id: str
    """The monster template's content id, like `"skeleton"`."""

    column: str | None = None
    """The turning table column this kind is read under, which follows its Hit Dice. `None` when the kind is too
    strong for the table to give it a column."""

    outcome: str
    """The verdict: `turn` when the roll met the threshold, `fail` when it didn't or there was no column,
    `destroy` when the table destroys this kind outright at the cleric's level, and `unaffected` for a creature
    that isn't undead at all."""

    threshold: int | None = None
    """The 2d6 total the cleric needed for this kind. `None` when the table gives an automatic result rather than
    a number."""


class UndeadTurnedEvent(Event):
    """A cleric held up a holy symbol, and this is what happened.

    [`turn_undead`][osrlib.core.spells.turn_undead] emits one of these per attempt. Turning resolves in two
    steps: the table is read once per kind of undead present, and then a second roll decides how many Hit Dice
    of the kinds that succumbed are actually affected. What then happens to each monster arrives as its own
    events, a condition gained or a death.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {"magic.turning.turned", "magic.turning.destroyed", "magic.turning.failed"}
    )
    """The three outcomes: nothing succumbed, something was turned, or something was destroyed outright."""

    event_type: Literal["undead_turned"] = "undead_turned"
    """The wire name for this event type."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the player rolls the turning dice."""

    caster_id: str
    """The entity id of the cleric who turned."""

    roll: int
    """The 2d6 read against the turning table."""

    hd_pool: int | None = None
    """The second 2d6, the Hit Dice of undead the turning reaches. `None` when nothing succumbed and no second
    roll was made. Undead are taken weakest first, and Hit Dice left over when the next one is too large go to
    waste rather than being spent elsewhere."""

    types: tuple[TurningTypeOutcome, ...] = ()
    """One [`TurningTypeOutcome`][osrlib.core.events.TurningTypeOutcome] per kind of undead present, in the order
    the candidates were given."""

    affected_ids: tuple[str, ...] = ()
    """The entity ids of the monsters the Hit Dice pool actually reached. At least one monster is always
    affected when any kind succumbed, even when the pool could not pay for it."""


class MagicDispelledEvent(Event):
    """A *dispel magic* went off, and these enchantments went with it.

    [`cast_spell`][osrlib.core.spells.cast_spell] emits this when a dispelling spell resolves. Every dispellable
    effect on the targets goes, except that one put there by a higher-level caster gets a roll to survive, better
    the wider the gap in levels, so a single dispel can take some enchantments and leave others. What a monster
    inflicted is not dispellable at all. The effects that went are also reported one by one as
    [`EffectReleasedEvent`][osrlib.core.events.EffectReleasedEvent].
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"magic.dispel.resolved"})
    """The only code this event uses."""

    event_type: Literal["magic_dispelled"] = "magic_dispelled"
    """The wire name for this event type."""

    code: str = "magic.dispel.resolved"
    """Fixed at `magic.dispel.resolved`."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the spell and what it undid are the players' to see."""

    caster_id: str
    """The entity id of the caster who dispelled."""

    released_effect_ids: tuple[str, ...] = ()
    """The ids of the effects the dispel ended."""

    surviving_effect_ids: tuple[str, ...] = ()
    """The ids of the dispellable effects that survived."""


KERNEL_EVENT_CLASSES: tuple[type[Event], ...] = (
    InitiativeRolledEvent,
    AttackRolledEvent,
    DamageDealtEvent,
    DamageAbsorbedEvent,
    SavingThrowRolledEvent,
    MoraleCheckedEvent,
    ReactionRolledEvent,
    ConditionGainedEvent,
    ConditionRemovedEvent,
    EffectAttachedEvent,
    EffectTickedEvent,
    EffectExpiredEvent,
    EffectReleasedEvent,
    HealingAppliedEvent,
    DeathEvent,
    EquipmentDestroyedEvent,
    LevelDrainedEvent,
    MonsterRevivedEvent,
    HitPointsReportedEvent,
    TargetsSelectedEvent,
    SpellsMemorizedEvent,
    SpellCastEvent,
    SpellDisruptedEvent,
    SpellForgottenEvent,
    SpellBookUpdatedEvent,
    UndeadTurnedEvent,
    MagicDispelledEvent,
)
"""Every kernel event class as a tuple, in declaration order. A kernel event is one the core rules emit.

Iterate it when you need the set itself rather than one event: to build a table of handlers, to generate JSON
Schema for each type, to check that your consumer covers everything. The same classes make up
[`KernelEvent`][osrlib.core.events.KernelEvent], which is what you annotate and validate with.

This contains the core rules' events alone. For those plus the crawl's, use
[`ALL_EVENT_CLASSES`][osrlib.crawl.events.ALL_EVENT_CLASSES].

```python
from osrlib.core.events import KERNEL_EVENT_CLASSES

by_type = {cls.model_fields["event_type"].default: cls for cls in KERNEL_EVENT_CLASSES}
assert by_type["damage_dealt"].__name__ == "DamageDealtEvent"
```
"""

KernelEvent = Annotated[
    InitiativeRolledEvent
    | AttackRolledEvent
    | DamageDealtEvent
    | DamageAbsorbedEvent
    | SavingThrowRolledEvent
    | MoraleCheckedEvent
    | ReactionRolledEvent
    | ConditionGainedEvent
    | ConditionRemovedEvent
    | EffectAttachedEvent
    | EffectTickedEvent
    | EffectExpiredEvent
    | EffectReleasedEvent
    | HealingAppliedEvent
    | DeathEvent
    | EquipmentDestroyedEvent
    | LevelDrainedEvent
    | MonsterRevivedEvent
    | HitPointsReportedEvent
    | TargetsSelectedEvent
    | SpellsMemorizedEvent
    | SpellCastEvent
    | SpellDisruptedEvent
    | SpellForgottenEvent
    | SpellBookUpdatedEvent
    | UndeadTurnedEvent
    | MagicDispelledEvent,
    Field(discriminator="event_type"),
]
"""The union of every event the core rules emit, tagged by `event_type`.

Annotate with this where a value is one specific event the core rules emit and you want the exact type back,
and hand it to a
pydantic `TypeAdapter` to validate serialized events. Because the union is discriminated, validation reads
`event_type` and goes straight to the right class instead of trying each in turn, and the JSON Schema it
generates is a tagged union your API consumers can read.

Prefer [`parse_event`][osrlib.core.events.parse_event] for reading back a stored log: it does the same
validation but returns `None` for an event type this release doesn't know, rather than raising. Annotate with
[`Event`][osrlib.core.events.Event] instead where any event will do.

```python
from pydantic import TypeAdapter

from osrlib.core.events import DamageDealtEvent, KernelEvent

adapter = TypeAdapter(KernelEvent)
payload = DamageDealtEvent(target_id="monster-0001", amount=5).model_dump(mode="json")
assert isinstance(adapter.validate_python(payload), DamageDealtEvent)
```
"""


@cache
def _kernel_event_adapter() -> TypeAdapter:
    return TypeAdapter(KernelEvent)


@cache
def _known_event_types() -> frozenset[str]:
    return frozenset(variant.model_fields["event_type"].default for variant in KERNEL_EVENT_CLASSES)


def parse_event(data: Mapping[str, object]) -> Event | None:
    """Turn one stored event back into its class, or `None` if this release has never heard of its type.

    Use this to read a saved log or a stream of events from somewhere else. Skipping what it doesn't recognize
    is the point: a log written by a newer release replays under an older one, minus the events the older one has
    no class for. Filter the `None` results out and carry on.

    It covers the core rules' events only. To read a log that also contains the crawl's, use
    [`parse_any_event`][osrlib.crawl.events.parse_any_event], which covers both.

    Args:
        data: One event as a mapping, of the shape `model_dump` produces.

    Returns:
        The event as its own class, or `None` when the mapping's `event_type` is one this release doesn't
            define.

    Raises:
        ContentValidationError: If the event type is one this release does define and the rest of the mapping
            doesn't fit it. A malformed event of a known type is a real problem, so it is raised rather than
            skipped.

    Examples:
        ```python
        from osrlib.core.events import DamageDealtEvent, parse_event

        event = DamageDealtEvent(target_id="monster-0001", attacker_id="hild", amount=5, rolls=(5,))
        assert parse_event(event.model_dump(mode="json")) == event

        # An event type from a release this one predates is skipped, not an error.
        assert parse_event({"event_type": "something_newer", "code": "x.y", "visibility": "player"}) is None
        ```
    """
    from osrlib.errors import ContentValidationError

    if data.get("event_type") not in _known_event_types():
        return None
    try:
        return _kernel_event_adapter().validate_python(data)
    except ValidationError as error:
        raise ContentValidationError(f"malformed kernel event: {error}") from error
