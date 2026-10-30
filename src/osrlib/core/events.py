"""Read what the rules did: the event base class and the events the core rules report.

osrlib's rules functions report what happened as events, and never print anything. An event is a frozen record of one
thing the rules did, like an attack roll or a creature's death. This module defines the base class every event shares,
and the events of the core rules: combat, effects, and magic. You get events from the rules functions, and you rarely
construct one yourself. [`resolve_attack`][osrlib.core.combat.resolve_attack], for example, returns its events in the
`events` field of its result, and [`EffectsLedger.advance`][osrlib.core.effects.EffectsLedger.advance] returns a list.
You decide what to do with them. [`osrlib.crawl.events`][osrlib.crawl.events] defines the events of a crawl on the
same base class.

The members, in the order you use them:

- [`Event`][osrlib.core.events.Event] is the base class. Annotate with it, and read `code` and `visibility` on
  any event.
- [`Visibility`][osrlib.core.events.Visibility] has the values for who may see an event: the players, or only the
  referee.
- [`parse_event`][osrlib.core.events.parse_event] turns a stored event back into its class.
  [`parse_any_event`][osrlib.crawl.events.parse_any_event] does the same for a log that also has crawl events.
- [`KernelEvent`][osrlib.core.events.KernelEvent] is the union of the core rules' event classes, for
  annotations and pydantic validation, and
  [`KERNEL_EVENT_CLASSES`][osrlib.core.events.KERNEL_EVENT_CLASSES] is the same classes as a tuple.
- The remaining classes are the events themselves, one for each kind of thing the rules do.

Every event has structured fields and a message code, never a sentence. A code is a lowercase string of two or
more parts separated by dots, like `combat.attack.hit`, and it identifies the outcome. Turn one into English with
[`format_message`][osrlib.messages.format_message], write your own formatter, or pass the fields to a narrator.

Every event has a visibility, because in B/X the referee makes some rolls out of the players' sight. Monster hit
points and morale rolls are for the referee only. Filter on [`Visibility`][osrlib.core.events.Visibility] before you
show a log to a player, or let [`GameSession.view`][osrlib.crawl.session.GameSession.view] do it for you.

The events can gain fields and types without breaking your program. Within a `schema_version`, the version stamp
of the saved format, events only gain fields and types. osrlib ignores an unknown field rather than rejecting it,
and `parse_event` returns `None` for an `event_type` your version of osrlib doesn't define. Write your program to
skip what it doesn't recognize, and it can read a log from a newer release.

Each class has an `event_type`, a fixed string that identifies the class in a serialized event. Tell the classes
apart by `event_type`, not by `code`: the code identifies the outcome, and one class can have codes for several
outcomes.

Typical usage:

```python
from osrlib.core.events import DamageDealtEvent, MoraleCheckedEvent, Visibility, parse_event
from osrlib.messages import format_message

# Built by hand here; in a program, these come back from a rules call.
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
    """Who may see an event: the players, or only the referee.

    Every event has one of these values, because in B/X the referee makes some rolls behind a screen. Filter
    a log on it before you show anything to a player, or call
    [`GameSession.view`][osrlib.crawl.session.GameSession.view], which builds the players' view and the referee's
    view for you. A narrator that plays the referee reads every event.

    A serialized event contains the lowercase value. The values stay fixed within a `schema_version`, the version
    stamp of the saved format.

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
    """Anyone may see it. These events are what the players see happen in the game: a hit, a spell, a door
    opening."""

    REFEREE = "referee"
    """Only the referee may see it. These events have the numbers that only the referee sees in B/X, like
    monster hit points and morale, reaction, surprise, and detection rolls. An effects ledger's records of
    its effects are referee events too."""


class Event(BaseModel):
    """The base class of every osrlib event: a frozen record of one thing the rules did.

    Use it as the type you annotate with, as in `list[Event]`. The two fields declared here are the two you can
    read on any event: `code`, which identifies what happened, and `visibility`, which is who may see it. The
    other fields are on each subclass. Check `event_type` or use `isinstance` to find out which subclass you have.

    Subclass it to add events of your own, for rules osrlib doesn't cover. Give the subclass structured fields
    only, report each outcome as a code rather than a sentence, and give it an `event_type` that differs from every
    osrlib event's. [`parse_event`][osrlib.core.events.parse_event] reads only osrlib's events and returns `None` for
    yours, so read yours back with your class's `model_validate`. Defining a subclass that turns off `frozen`, or
    sets `extra` to anything but `"ignore"`, raises `TypeError`. Either one would break two promises osrlib
    makes about every event: the event never changes, and osrlib ignores its unknown fields.

    Events never change once made, so you can keep a list of them as a log and read it at any time.

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
    """The codes a subclass may use. Building an event with any other code raises a pydantic `ValidationError`.

    It's empty on the base class, which accepts any code in the right format. Read it to find out which codes a
    class accepts. Set it on your own subclass to get the same check."""

    code: str
    """What happened, like `combat.attack.hit`: two or more lowercase parts separated by dots. The first part is
    the area of the rules the event comes from, like `combat` or `magic`.

    Branch on it, and pass the event to [`format_message`][osrlib.messages.format_message] to turn it into English.
    Building an event with a code in any other format raises a pydantic `ValidationError`."""

    visibility: Visibility
    """Who may see the event. Filter on it before you show a log to a player."""

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        """Reject a subclass whose `model_config` isn't frozen or doesn't ignore unknown fields."""
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

    Read these from the event's `entries` to show what each side or combatant rolled. Check the event's
    `mode` to know which: a side under side initiative, a combatant under individual initiative.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    """Who rolled: a side's name under side initiative, or a combatant's entity id under individual
    initiative."""

    rolls: tuple[int, ...]
    """Every d6 rolled for this side or combatant, in the order rolled. `roll_initiative` re-rolls a tie, so more
    than one entry means there was a tie, and the later entries are the rolls that broke it. The last entry is the
    one that counts."""

    modifier: int = 0
    """The modifier added to the last roll. It's 0 under side initiative. Under individual initiative, a
    character's comes from its DEX and class, through
    [`participant_modifier`][osrlib.core.combat.participant_modifier], and a monster's is the one you chose."""

    total: int
    """The last roll plus the modifier. `roll_initiative` sorts the acting order on this total."""


class InitiativeRolledEvent(Event):
    """Who acts first this round, and what everyone rolled to get there.

    [`roll_initiative`][osrlib.core.combat.roll_initiative] reports one of these each time it's called, which is
    at the start of each combat round. Read `order` to know who acts next, and `entries` to show the dice.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"combat.initiative.rolled"})
    """The only code this event uses."""

    event_type: Literal["initiative_rolled"] = "initiative_rolled"
    """The fixed string that identifies this event class in a serialized event."""

    code: str = "combat.initiative.rolled"
    """Fixed at `combat.initiative.rolled`."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: everyone at the table can see the order of play."""

    mode: Literal["side", "individual"]
    """How initiative was rolled: `"side"` when each side rolled once for all its members, `"individual"` when
    each combatant rolled for itself."""

    entries: tuple[InitiativeRoll, ...]
    """One [`InitiativeRoll`][osrlib.core.events.InitiativeRoll] for each side, or for each combatant under
    individual initiative. They're in the order `roll_initiative` received them, not the order they act."""

    order: tuple[str, ...]
    """Every combatant's key, usually its entity id, in the order they act this round, first to last."""


class AttackRolledEvent(Event):
    """One attack roll and everything that went into it.

    [`attack_roll`][osrlib.core.combat.attack_roll] reports one of these for every attack, and
    [`resolve_attack`][osrlib.core.combat.resolve_attack] reports it before the damage that follows. Read `code`
    for the outcome, and the number fields to show the arithmetic.

    An automatic hit has no roll. It happens against a paralysed or sleeping defender in melee, or a monster with
    no armour class, like green slime. Then `roll`, `total`, `required`, and `defender_ac` are `None`.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {"combat.attack.hit", "combat.attack.missed", "combat.attack.auto_hit"}
    )
    """The three outcomes: `combat.attack.hit`, `combat.attack.missed`, and `combat.attack.auto_hit` for an
    automatic hit."""

    event_type: Literal["attack_rolled"] = "attack_rolled"
    """The fixed string that identifies this event class in a serialized event."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: attack rolls happen in the open."""

    attacker_id: str
    """The entity id of whoever attacked."""

    defender_id: str
    """The entity id of whoever was attacked."""

    attack_name: str
    """The name of what the attacker used: a weapon, a gear item, a magic item, or a monster's attack. It's
    `"unarmed"` for an unarmed attack, and `"improvised"` for an attack with a gear item's combat stats alone."""

    roll: int | None = None
    """The d20 as it landed, before modifiers. `None` on an automatic hit."""

    modifier: int = 0
    """The sum of everything added to the roll, like STR, range, magic, spells, and your situational modifier. It
    can be negative."""

    total: int | None = None
    """The roll plus the modifier, which is the number compared with `required`. `None` on an automatic hit."""

    required: int | None = None
    """The total the attacker needed to hit, from its THAC0 and the defender's armour class. `None` on an
    automatic hit."""

    defender_ac: int | None = None
    """The defender's armour class as the roll used it, after its shield, spells, and the situation. `None` on
    an automatic hit."""

    natural: int | None = None
    """1 or 20 when the d20 alone decided the outcome, and `None` otherwise.

    A natural 20 always hits and a natural 1 always misses. This field is set only when that rule changed the
    outcome: a 20 whose total was below `required`, or a 1 whose total reached it."""


class DamageDealtEvent(Event):
    """Damage dealt to a creature.

    [`deal_damage`][osrlib.core.combat.deal_damage] reports one of these each time it deals damage, whether the
    damage came from a weapon, a spell, or an effect like burning oil.

    It reports how much damage was dealt and never how many hit points the target has left, because in B/X only
    the referee sees monster hit points. The hit points left are in the
    [`HitPointsReportedEvent`][osrlib.core.events.HitPointsReportedEvent] that follows it, which only the referee
    sees.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"combat.damage.dealt"})
    """The only code this event uses."""

    event_type: Literal["damage_dealt"] = "damage_dealt"
    """The fixed string that identifies this event class in a serialized event."""

    code: str = "combat.damage.dealt"
    """Fixed at `combat.damage.dealt`."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: everyone sees how hard a creature was hit."""

    target_id: str
    """The entity id of the creature that took the damage."""

    attacker_id: str | None = None
    """The entity id of the attacker, or `None` when there was no attacker, as for a fall or a trap."""

    amount: int
    """The hit points the target lost, after all its reductions."""

    rolls: tuple[int, ...] = ()
    """Each damage die as rolled, before modifiers. Empty for damage that wasn't rolled, like a dragon's breath."""

    keys: tuple[str, ...] = ()
    """The keys of the damage that the target's defenses check: keys like `silver`, `magic`, or `holy`, then the
    damage's element, like `fire`, when it had one."""

    non_regenerable: bool = False
    """True when the target regenerates and this damage's element stops its regeneration, as fire and acid do for
    a troll. Such damage can't be regenerated, and once enough of it adds up, the troll stays dead."""


class DamageAbsorbedEvent(Event):
    """A hit that did nothing, because the target can't be harmed by what hit it.

    This comes instead of a [`DamageDealtEvent`][osrlib.core.events.DamageDealtEvent] when the target is immune to
    the damage, like a creature that only silver or magic can harm hit by a steel sword, or a creature immune to
    the breath's element. No damage was rolled. Treat it as a hit that did nothing, not as a miss.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"combat.damage.absorbed"})
    """The only code this event uses."""

    event_type: Literal["damage_absorbed"] = "damage_absorbed"
    """The fixed string that identifies this event class in a serialized event."""

    code: str = "combat.damage.absorbed"
    """Fixed at `combat.damage.absorbed`."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the players see the hit land and do nothing."""

    target_id: str
    """The entity id of the creature the attack couldn't harm."""

    attacker_id: str | None = None
    """The entity id of the attacker, or `None` when there was no attacker."""

    keys: tuple[str, ...] = ()
    """The keys of the attack that the target's defenses checked, like `silver` or `magic`."""


class SavingThrowRolledEvent(Event):
    """A saving throw and how it went.

    [`saving_throw`][osrlib.core.combat.saving_throw] reports one of these whenever a creature gets a chance to
    avoid something. What passing or failing means depends on what forced the save, so read this event with the
    events after it.

    A monster whose defenses give it an automatic save against a magical effect's element, as a red dragon has
    against magical fire, rolls nothing. Then `roll` and `required` are `None`.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {"combat.save.passed", "combat.save.failed", "combat.save.auto"}
    )
    """The three outcomes: `combat.save.passed`, `combat.save.failed`, and `combat.save.auto` for a save that
    passed without a roll."""

    event_type: Literal["saving_throw_rolled"] = "saving_throw_rolled"
    """The fixed string that identifies this event class in a serialized event."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: saving throws are rolled in the open, monsters included."""

    target_id: str
    """The entity id of the creature that saved."""

    category: str
    """Which saving throw it was, as a [`SaveCategory`][osrlib.core.combat.SaveCategory] value: `death`, `wands`,
    `paralysis`, `breath`, or `spells`."""

    roll: int | None = None
    """The d20 as it landed, before modifiers. `None` on an automatic save."""

    modifier: int = 0
    """The sum of everything added to the roll, like a spell, a ring, and your adjustment."""

    required: int | None = None
    """The number the roll plus the modifier had to reach. `None` on an automatic save."""


class MoraleCheckedEvent(Event):
    """A side's morale check, and whether the side held.

    [`check_morale`][osrlib.core.combat.check_morale] reports one of these each time a side checks morale. A side
    that breaks flees or surrenders, and making it do so is up to you.

    At some morale scores, a side never rolls. A side with a score of 2 or less never fights, and a side with 12 or
    more never checks. `check_morale` reports both with `combat.morale.exempt` and no roll. Read `held` to tell them
    apart, as for any other code: at 2 or less the side has broken, and at 12 or more it holds.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {"combat.morale.held", "combat.morale.broke", "combat.morale.exempt"}
    )
    """The three codes: `combat.morale.held`, `combat.morale.broke`, and `combat.morale.exempt` when the side's
    score means it doesn't roll. The exempt code covers both exempt scores, so read `held` to tell them apart."""

    event_type: Literal["morale_checked"] = "morale_checked"
    """The fixed string that identifies this event class in a serialized event."""

    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: the players learn that a side has broken by watching it run."""

    subject: str
    """The key of the side whose morale was checked, as you passed it."""

    score: int
    """The side's morale score. A side with a score from 3 to 11 rolls. A side with 2 or less doesn't roll and has
    broken, and a side with 12 or more doesn't roll and holds."""

    roll: int | None = None
    """The 2d6 total, before the modifier. `None` when the side didn't roll."""

    modifier: int = 0
    """The adjustment applied, after it was limited to the range −2 to +2."""

    held: bool | None = None
    """Whether the side keeps fighting.

    [`check_morale`][osrlib.core.combat.check_morale] sets it on every event, whatever the code. After a roll, it's
    the outcome of the roll. It's `True` for a score of 12 or more, and `False` for a score of 2 or less. Show the
    outcome from this field, not from the code and the score. It's `None` on an event loaded from a save written
    before the field existed, because osrlib doesn't fill it in when it loads such a save."""


class ReactionRolledEvent(Event):
    """How monsters react to meeting the party.

    [`roll_reaction`][osrlib.core.combat.roll_reaction] reports one of these each time it's called, which is when
    the party meets monsters that aren't already fighting. Use the result to decide how the monsters act.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"encounter.reaction.rolled"})
    """The only code this event uses."""

    event_type: Literal["reaction_rolled"] = "reaction_rolled"
    """The fixed string that identifies this event class in a serialized event."""

    code: str = "encounter.reaction.rolled"
    """Fixed at `encounter.reaction.rolled`."""

    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: the players learn a monster's mood from what it does, not from the dice."""

    roll: int
    """The 2d6 total, before the modifier."""

    modifier: int = 0
    """The CHA modifier to reactions of the character who spoke to the monsters, or 0 when no one did."""

    total: int
    """The roll plus the modifier, which is the number osrlib looks up on the reaction table. osrlib reads a total
    outside 2 to 12 as the nearest end of the table."""

    result: str
    """How the monsters react, as a [`ReactionResult`][osrlib.core.tables.ReactionResult] value: `attacks`,
    `hostile`, `uncertain`, `indifferent`, or `friendly`."""


class ConditionGainedEvent(Event):
    """A creature has fallen asleep, been turned to stone, or otherwise gained a condition.

    A condition is a named state, from [`Condition`][osrlib.core.effects.Condition], that changes what a creature
    can do. [`grant_condition`][osrlib.core.effects.grant_condition] reports this, whether you called it yourself
    or [`EffectsLedger.attach`][osrlib.core.effects.EffectsLedger.attach] called it while attaching an effect that
    gives a condition. It reports no event for a creature immune to the condition, or for one that already has it
    from the same effect.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"effects.condition.gained"})
    """The only code this event uses."""

    event_type: Literal["condition_gained"] = "condition_gained"
    """The fixed string that identifies this event class in a serialized event."""

    code: str = "effects.condition.gained"
    """Fixed at `effects.condition.gained`."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: a creature going rigid or falling asleep is plain to see."""

    target_id: str
    """The entity id of the creature that gained the condition."""

    condition: str
    """The condition, as a [`Condition`][osrlib.core.effects.Condition] value like `asleep` or `petrified`."""

    effect_id: str | None = None
    """The id of the effect that gave the condition and takes it away when it ends, or `None` for a condition
    that doesn't come from an effect. In the core rules, that's `dead`."""


class ConditionRemovedEvent(Event):
    """A creature has lost a condition: it's awake again, no longer stone, no longer entangled.

    [`remove_condition`][osrlib.core.effects.remove_condition] reports this, whether you called it yourself or an
    [`EffectsLedger`][osrlib.core.effects.EffectsLedger] called it when the effect that gave the condition
    expired or was released.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"effects.condition.removed"})
    """The only code this event uses."""

    event_type: Literal["condition_removed"] = "condition_removed"
    """The fixed string that identifies this event class in a serialized event."""

    code: str = "effects.condition.removed"
    """Fixed at `effects.condition.removed`."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: everyone can see a creature come out of a condition."""

    target_id: str
    """The entity id of the creature that lost the condition."""

    condition: str
    """The condition, as a [`Condition`][osrlib.core.effects.Condition] value."""

    effect_id: str | None = None
    """The id of the effect that gave the condition, or `None` for a condition that didn't come from an effect."""


class EffectAttachedEvent(Event):
    """An effect has started on a creature, an item, or a location.

    An effect is a lasting change the rules make, like a spell's *sleep* or a troll's regeneration, which an
    [`EffectsLedger`][osrlib.core.effects.EffectsLedger] tracks until it ends.
    [`EffectsLedger.attach`][osrlib.core.effects.EffectsLedger.attach] reports this first, before any condition the
    effect gives. Keep the `effect_id` if you plan to end the effect early with
    [`EffectsLedger.release`][osrlib.core.effects.EffectsLedger.release].
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"effects.effect.attached"})
    """The only code this event uses."""

    event_type: Literal["effect_attached"] = "effect_attached"
    """The fixed string that identifies this event class in a serialized event."""

    code: str = "effects.effect.attached"
    """Fixed at `effects.effect.attached`."""

    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: this is the ledger's record. The players notice the condition the effect gives, which
    a separate event reports."""

    effect_id: str
    """The id the ledger gave the effect, like `effect-0001`."""

    kind: str
    """The effect's kind, from its definition, like `"sleep"`, `"regeneration"`, or `"light"`."""

    target_ref: str
    """What the effect is attached to: an entity id for a creature, or a location string for an effect in a
    place."""

    expires_round: int | None = None
    """The round the effect is due to end, counted from the start of the game clock, like
    [`GameClock.rounds`][osrlib.core.clock.GameClock]. `None` when the effect has no duration or is
    permanent."""


class EffectTickedEvent(Event):
    """An effect did what it does at regular intervals, like a troll's regeneration healing it.

    [`EffectsLedger.advance`][osrlib.core.effects.EffectsLedger.advance] reports this each time a regeneration
    heals or a charmed creature rolls its repeated save, and
    [`pop_mirror_image`][osrlib.core.spells.pop_mirror_image] reports it when an attack destroys one image. What
    the effect did is in the events next to this one: healing, a saving throw, or a release.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"effects.effect.ticked"})
    """The only code this event uses."""

    event_type: Literal["effect_ticked"] = "effect_ticked"
    """The fixed string that identifies this event class in a serialized event."""

    code: str = "effects.effect.ticked"
    """Fixed at `effects.effect.ticked`."""

    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: this is the ledger's record."""

    effect_id: str
    """The id of the effect."""

    kind: str
    """The effect's kind, from its definition."""

    target_ref: str
    """The entity id or location string the effect is attached to."""

    round: int
    """The round the effect acted on, counted from the start of the game clock."""


class EffectExpiredEvent(Event):
    """An effect ran out of time and ended.

    [`EffectsLedger.advance`][osrlib.core.effects.EffectsLedger.advance] reports this when the clock reaches the
    round an effect ends on. This event comes first. Then the effect's condition and modifiers come off. Last, an
    effect that does something when it ends does it, like the second hit of thrown oil. When you end an effect early,
    the ledger reports an [`EffectReleasedEvent`][osrlib.core.events.EffectReleasedEvent] instead.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"effects.effect.expired"})
    """The only code this event uses."""

    event_type: Literal["effect_expired"] = "effect_expired"
    """The fixed string that identifies this event class in a serialized event."""

    code: str = "effects.effect.expired"
    """Fixed at `effects.effect.expired`."""

    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: this is the ledger's record. When a light source burns out, a session also reports a
    separate event the players see."""

    effect_id: str
    """The id of the effect that ended."""

    kind: str
    """The effect's kind, from its definition."""

    target_ref: str
    """The entity id or location string the effect was attached to."""

    round: int
    """The round the effect ended on, counted from the start of the game clock."""


class EffectReleasedEvent(Event):
    """An effect was ended early, before its time was up.

    [`EffectsLedger.release`][osrlib.core.effects.EffectsLedger.release] reports this, as when a *dispel magic*
    ends a spell, a charmed creature makes its save, or someone puts out a torch. For an effect that ran out of time,
    the ledger reports an [`EffectExpiredEvent`][osrlib.core.events.EffectExpiredEvent] instead.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"effects.effect.released"})
    """The only code this event uses."""

    event_type: Literal["effect_released"] = "effect_released"
    """The fixed string that identifies this event class in a serialized event."""

    code: str = "effects.effect.released"
    """Fixed at `effects.effect.released`."""

    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: this is the ledger's record."""

    effect_id: str
    """The id of the effect that was ended."""

    kind: str
    """The effect's kind, from its definition."""

    target_ref: str
    """The entity id or location string the effect was attached to."""


class HealingAppliedEvent(Event):
    """Hit points restored, or healing refused.

    [`apply_healing`][osrlib.core.combat.apply_healing] reports this, and so does
    [`EffectsLedger.advance`][osrlib.core.effects.EffectsLedger.advance] each round a regeneration effect heals. Read
    `amount`, not the amount you asked for: healing stops at the creature's maximum hit points, and a cursed creature
    may have had it halved.

    `apply_healing` can also refuse healing. Then the code is `combat.healing.blocked` and `amount` is 0. A diseased
    creature gets no magical healing, and a weakened one gets no healing at all.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"combat.healing.applied", "combat.healing.blocked"})
    """The two outcomes: `combat.healing.applied`, and `combat.healing.blocked` when a condition prevented
    the healing."""

    event_type: Literal["healing_applied"] = "healing_applied"
    """The fixed string that identifies this event class in a serialized event."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: everyone sees a wound close. The hit points the creature now has are in the
    [`HitPointsReportedEvent`][osrlib.core.events.HitPointsReportedEvent] that follows, which only the referee
    sees."""

    target_id: str
    """The entity id of the creature that was healed."""

    amount: int
    """The hit points restored, after the maximum and any halving. 0 when the healing was blocked."""

    source: str
    """Where the healing came from: `magical` for a spell or potion, `natural` for rest, or `regeneration` for a
    regenerating creature."""


class DeathEvent(Event):
    """A creature has been killed.

    [`kill`][osrlib.core.effects.kill] reports this, whether the death came from damage, a failed save, or an
    effect running out. It comes after the [`ConditionGainedEvent`][osrlib.core.events.ConditionGainedEvent] for
    `dead`, and before the [`HitPointsReportedEvent`][osrlib.core.events.HitPointsReportedEvent].

    [`deal_damage`][osrlib.core.combat.deal_damage] reports a second one, with the code `combat.death.permanent`,
    when the damage a dead monster can't regenerate adds up to its maximum hit points. That one
    comes after the `HitPointsReportedEvent`, with no condition event, because the monster is already `dead`.

    Read the code to know whether the death can be undone. An ordinary death can be, by magic or by regeneration. A
    permanent one can't, which is what enough fire and acid do to a troll.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"combat.death.died", "combat.death.permanent"})
    """The two outcomes: `combat.death.died` for an ordinary death, and `combat.death.permanent` for one that
    can't be undone."""

    event_type: Literal["death"] = "death"
    """The fixed string that identifies this event class in a serialized event."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: everyone sees a creature fall."""

    target_id: str
    """The entity id of the creature that died."""


class EquipmentDestroyedEvent(Event):
    """Everything a victim carried was destroyed with it.

    [`destroy_equipment`][osrlib.core.combat.destroy_equipment] reports this when a death destroys the victim and
    what it carries, as a dragon's breath or *disintegrate* does. It reports no event for a victim that carried
    nothing.

    When the ruleset's `magic_item_death_save` flag is on, `destroy_equipment` rolls a save for each magic item, and
    `saved_items` lists the ones that survive. `destroy_equipment` leaves them in the victim's inventory. When the
    victim is a party member, a [`GameSession`][osrlib.crawl.session.GameSession] moves them to a drop pile on the
    party's cell in the same command, and the party picks them up with
    [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure] and the feature id `"pile"`.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"combat.equipment.destroyed"})
    """The only code this event uses."""

    event_type: Literal["equipment_destroyed"] = "equipment_destroyed"
    """The fixed string that identifies this event class in a serialized event."""

    code: str = "combat.equipment.destroyed"
    """Fixed at `combat.equipment.destroyed`."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the players see what was lost."""

    target_id: str
    """The entity id of the victim."""

    item_names: tuple[str, ...]
    """The display names of the items that were destroyed."""

    saved_items: tuple[str, ...] = ()
    """The instance ids of the magic items that passed their save and still exist. Empty when none survived,
    which is always so when the ruleset's `magic_item_death_save` flag is off."""


class LevelDrainedEvent(Event):
    """An undead creature's touch has taken levels, or killed its victim.

    [`drain_levels`][osrlib.core.classes.drain_levels] reports this for a character, and
    [`drain_monster_hd`][osrlib.core.combat.drain_monster_hd] for a monster, which loses Hit Dice instead of
    levels.

    When the victim has nothing left to lose, at level 1 or 1 Hit Die, the drain kills it instead. Then the code
    is `combat.drain.slain`, and a [`DeathEvent`][osrlib.core.events.DeathEvent] follows. The killing level
    counts as lost, so when a spectre, which drains two levels, kills a level-2 fighter, the event shows two levels
    lost.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"combat.drain.drained", "combat.drain.slain"})
    """The two outcomes: `combat.drain.drained` when levels were lost, and `combat.drain.slain` when the drain
    killed the victim."""

    event_type: Literal["level_drained"] = "level_drained"
    """The fixed string that identifies this event class in a serialized event."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the players know when a level is lost."""

    target_id: str
    """The entity id of the victim."""

    levels_lost: int
    """How many levels, or Hit Dice, the drain took."""

    new_level: int
    """The victim's level, or Hit Dice, after the drain. 0 when the drain killed it."""

    hp_lost: int
    """The maximum hit points the victim lost with the levels."""

    xp_after: int | None = None
    """The character's experience points after the drain. Depending on the draining monster, they're either
    halfway between the old and new levels' thresholds, or at the new level's threshold. `None` for a monster, whose Hit
    Dice are worth no experience points, and for a victim the drain killed."""

    spawn_consequence: str | None = None
    """What the rules say becomes of a character this undead creature kills, like rising as a wight in 1d4
    days under the control of its killer. osrlib kills the victim, and playing out what it becomes is up to you.
    `None` for a monster victim, and when the undead creature's rules say nothing about it."""


class MonsterRevivedEvent(Event):
    """A monster the party thought dead has got back up.

    [`EffectsLedger.advance`][osrlib.core.effects.EffectsLedger.advance] reports this for a regeneration effect
    when the monster's countdown runs out,
    which for a troll is 2d6 rounds after it was killed. The monster comes back with 1 hit point, and loses the
    `dead` condition at the same time. A monster stops getting back up once the damage it can't regenerate, fire
    and acid for a troll, adds up to its maximum hit points.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"effects.regeneration.revived"})
    """The only code this event uses."""

    event_type: Literal["monster_revived"] = "monster_revived"
    """The fixed string that identifies this event class in a serialized event."""

    code: str = "effects.regeneration.revived"
    """Fixed at `effects.regeneration.revived`."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the players see the monster get back up."""

    target_id: str
    """The entity id of the monster that came back."""


class HitPointsReportedEvent(Event):
    """A creature's hit points after something changed them.

    Every rules function that changes hit points reports one of these right after the change: the functions for
    damage, healing, death, energy drain, and regeneration. Follow them and you know every creature's hit points without
    reading the creatures.

    This is the only event with a creature's current and maximum hit points. The others report only the change:
    a [`DamageDealtEvent`][osrlib.core.events.DamageDealtEvent] the damage, and a
    [`LevelDrainedEvent`][osrlib.core.events.LevelDrainedEvent] the maximum hit points lost. Only the referee
    sees this event, because in B/X the players don't see monster hit points. Show the players the events that
    report the change.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"combat.state.hit_points"})
    """The only code this event uses."""

    event_type: Literal["hit_points_reported"] = "hit_points_reported"
    """The fixed string that identifies this event class in a serialized event."""

    code: str = "combat.state.hit_points"
    """Fixed at `combat.state.hit_points`."""

    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: in B/X, the players don't see monster hit points."""

    target_id: str
    """The entity id of the creature reported on."""

    current_hp: int
    """The creature's hit points now, never below 0."""

    max_hp: int
    """The creature's maximum hit points, which energy drain can lower."""


class TargetsSelectedEvent(Event):
    """Which of the candidates a spell reached.

    [`select_targets`][osrlib.core.combat.select_targets] reports this before the spell's other events, so a log
    shows which creatures a spell like *sleep* or *hold person* reached, not only the creatures it was aimed
    at.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"combat.targeting.selected"})
    """The only code this event uses."""

    event_type: Literal["targets_selected"] = "targets_selected"
    """The fixed string that identifies this event class in a serialized event."""

    code: str = "combat.targeting.selected"
    """Fixed at `combat.targeting.selected`."""

    visibility: Visibility = Visibility.REFEREE
    """Referee visibility: this is a record for the referee. What happens to the targets comes as separate
    events."""

    mode: str
    """How the targets were chosen, as a [`TargetingMode`][osrlib.core.combat.TargetingMode] value: `self`,
    `single`, `up_to_n`, `hd_budget`, `area`, or `gaze`."""

    target_ids: tuple[str, ...]
    """The entity ids of the selected creatures, in the order they were chosen."""


class PreparedSpell(BaseModel):
    """One spell a caster has in memory, as a memorization event lists it.

    Read these from the `prepared` field of a [`SpellsMemorizedEvent`][osrlib.core.events.SpellsMemorizedEvent].

    A caster who prepares a reversible spell chooses its form when preparing it, not when casting it, so each
    prepared copy records its form.
    """

    model_config = ConfigDict(frozen=True)

    spell_id: str
    """The spell's content id, like `"magic_missile"`."""

    reversed: bool = False
    """True when the copy was prepared in the spell's reversed form, as *cause light wounds* is the reverse of
    *cure light wounds*."""


class SpellsMemorizedEvent(Event):
    """A caster has finished preparing spells for the day.

    [`memorize_spells`][osrlib.core.spells.memorize_spells] reports one of these each time it prepares a caster's
    spells. Preparing replaces everything the caster had in memory, so `prepared` lists all of the caster's
    memory now, not only what was added.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"magic.memorize.prepared"})
    """The only code this event uses."""

    event_type: Literal["spells_memorized"] = "spells_memorized"
    """The fixed string that identifies this event class in a serialized event."""

    code: str = "magic.memorize.prepared"
    """Fixed at `magic.memorize.prepared`."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the player may see what their caster has memorized."""

    caster_id: str
    """The entity id of the caster."""

    prepared: tuple[PreparedSpell, ...]
    """Every copy now in the caster's memory, as [`PreparedSpell`][osrlib.core.events.PreparedSpell] entries. A
    spell prepared twice appears twice."""


class SpellCastEvent(Event):
    """A spell was cast, from memory or from a scroll.

    [`cast_spell`][osrlib.core.spells.cast_spell] reports this when a caster casts a memorized spell, which uses
    up the memorized copy, and [`cast_from_scroll`][osrlib.core.spells.cast_from_scroll] reports it when a caster
    reads a spell from a scroll. What the spell did comes in the events after this one: saving throws, damage,
    conditions, effects, healing, and deaths.

    When a cast affects no one, the event has the code `magic.cast.no_effect`, and the spell is still used up. Refusing
    the cast instead would show the player, at no cost, which targets the spell could affect, and the B/X rules don't
    reveal that.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"magic.cast.cast", "magic.cast.no_effect"})
    """The two outcomes: `magic.cast.cast`, and `magic.cast.no_effect` when the spell affected no one."""

    event_type: Literal["spell_cast"] = "spell_cast"
    """The fixed string that identifies this event class in a serialized event."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: everyone hears a spell cast aloud."""

    caster_id: str
    """The entity id of the caster."""

    spell_id: str
    """The spell's content id, like `"magic_missile"`."""

    mode: str
    """The key of the spell mode that was cast. For a spell with more than one mode, like one that can harm or
    heal, it's the mode the caster chose."""

    reversed: bool = False
    """True when the spell was cast in its reversed form."""

    target_ids: tuple[str, ...] = ()
    """The entity ids or locations the caster aimed the spell at. Empty for a spell with no target."""

    manual: bool = False
    """True when osrlib didn't resolve the spell's effect, because the rules leave that mode to the referee. The
    copy is used up, and you play out the outcome from the spell's text."""


class SpellDisruptedEvent(Event):
    """A spell the caster declared came to nothing, and the caster lost it anyway.

    It's reported in two cases, and you tell them apart by `code`:

    - `magic.cast.disrupted`: [`disrupt_casting`][osrlib.core.spells.disrupt_casting] reports it when a caster
      who declared a spell is hit, or stopped some other way, before the spell goes off.
    - `magic.cast.fizzled`: a session reports it during a battle round. In the round's magic phase, the session
      checks each declared spell again, just before the spell would go off. If a check the spell passed at the start of
      the round now fails, because something earlier in the round changed, the spell fizzles. Two examples are an ally's
      *silence 15' radius* centered on the party's cell, and the party's last torch going out when its bearer
      dies. Any of the checks can cause a fizzle, and `reason` has the rejection code of the one that failed.

    Either way, nothing happened and the memorized copy is gone, as if the spell had been cast. Tell the player
    the spell failed and the copy is used up. A spell read from a scroll that fizzles uses up the scroll's spell
    the same way, and the caster loses no memorized copy.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"magic.cast.disrupted", "magic.cast.fizzled"})
    """The two codes: `magic.cast.disrupted` and `magic.cast.fizzled`."""

    event_type: Literal["spell_disrupted"] = "spell_disrupted"
    """The fixed string that identifies this event class in a serialized event."""

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
    """Why a `magic.cast.fizzled` spell failed: the code of the first check that failed when the session checked
    the spell again during the battle round.

    Any check a spell passes at the start of the round can fail, so read the code and don't assume a cause. An
    ally's silence on the party's cell gives `magic.cast.silenced_area`, and a scroll read in the dark gives
    `exploration.action.requires_light`. Match on it as you match on the code of a
    [`Rejection`][osrlib.core.validation.Rejection]. [The rejection code reference][rejection-codes] explains
    each code. `None` on a `magic.cast.disrupted` event, whose cause is the hit, and on an event from a log
    written before the field existed.
    """


class SpellForgottenEvent(Event):
    """A caster forgot a memorized spell, because the caster no longer has room for it.

    [`forget_excess_memorized`][osrlib.core.spells.forget_excess_memorized] reports one of these for each copy the
    caster forgets. That happens when energy drain takes a caster's levels, and the caster has fewer spell slots
    than spells in memory. A caster who forgot two copies gets two events.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"magic.memory.forgotten"})
    """The only code this event uses."""

    event_type: Literal["spell_forgotten"] = "spell_forgotten"
    """The fixed string that identifies this event class in a serialized event."""

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
    """A new spell has gone into an arcane caster's spell book.

    [`add_spell_to_book`][osrlib.core.spells.add_spell_to_book] reports this. A caster may prepare only the
    spells in its book. Adding a spell to the book doesn't prepare it. Preparing is a separate step, which
    `memorize_spells` reports with a [`SpellsMemorizedEvent`][osrlib.core.events.SpellsMemorizedEvent].
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"magic.book.added"})
    """The only code this event uses."""

    event_type: Literal["spell_book_updated"] = "spell_book_updated"
    """The fixed string that identifies this event class in a serialized event."""

    code: str = "magic.book.added"
    """Fixed at `magic.book.added`."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the book belongs to the player."""

    caster_id: str
    """The entity id of the caster whose book gained the spell."""

    spell_id: str
    """The spell's content id."""


class TurningTypeOutcome(BaseModel):
    """How one kind of monster fared against a cleric's attempt to turn undead.

    `turn_undead` reads the turning table once for each kind of monster present, not once for each monster, and
    each of these is one reading. Read them from the `types` field of an
    [`UndeadTurnedEvent`][osrlib.core.events.UndeadTurnedEvent].
    """

    model_config = ConfigDict(frozen=True)

    template_id: str
    """The monster template's content id, like `"skeleton"`."""

    column: str | None = None
    """The turning table column this kind is read in, which depends on its Hit Dice. `None` when the kind is too
    strong to have a column, or isn't undead."""

    outcome: str
    """The result for this kind, as one of four strings.

    `turn` means the roll reached the threshold, or the table's entry is an automatic turn. `fail` means the roll
    fell short, the table's entry gives the cleric no chance, or the kind has no column. `destroy` means the
    table's entry at the cleric's level is an automatic destroy. `unaffected` means the monster isn't undead."""

    threshold: int | None = None
    """The 2d6 total the cleric needed for this kind. `None` when the table has an automatic result instead of
    a number."""


class UndeadTurnedEvent(Event):
    """A cleric tried to turn undead, and this is what happened.

    [`turn_undead`][osrlib.core.spells.turn_undead] reports one of these for each attempt. Turning takes two
    rolls. `turn_undead` reads the first on the turning table once for each kind of undead present. When any kind is
    turned or destroyed, a second roll sets how many Hit Dice of those kinds are affected. What then happens to each
    monster comes as separate events: a condition gained, or a death.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset(
        {"magic.turning.turned", "magic.turning.destroyed", "magic.turning.failed"}
    )
    """The three outcomes: `magic.turning.failed` when no kind was turned or destroyed, `magic.turning.turned`
    when monsters were turned, and `magic.turning.destroyed` when at least one was destroyed."""

    event_type: Literal["undead_turned"] = "undead_turned"
    """The fixed string that identifies this event class in a serialized event."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the player rolls the turning dice."""

    caster_id: str
    """The entity id of the cleric who tried to turn undead."""

    roll: int
    """The first 2d6, read on the turning table."""

    hd_pool: int | None = None
    """The second 2d6: how many Hit Dice of undead the turning affects. `None` when no kind was turned or
    destroyed, so no second roll was made.

    `turn_undead` takes the weakest undead first. When the next one has more Hit Dice than are left, the rest go
    unused, and it takes no other monster in its place."""

    types: tuple[TurningTypeOutcome, ...] = ()
    """One [`TurningTypeOutcome`][osrlib.core.events.TurningTypeOutcome] for each kind of monster present, in the
    order you listed the monsters."""

    affected_ids: tuple[str, ...] = ()
    """The entity ids of the monsters the second roll reached. When any kind was turned or destroyed, at least one
    monster is affected, even when the second roll was too low for it."""


class MagicDispelledEvent(Event):
    """A *dispel magic* ended the effects on its targets, except those that survived.

    [`cast_spell`][osrlib.core.spells.cast_spell] reports this when *dispel magic* resolves. Only effects marked
    dispellable can end: every effect a spell attaches is, and what a monster inflicts isn't. An effect cast by a
    higher-level caster gets a roll to survive, with a better chance the
    wider the gap in levels, so one dispel can end some effects and leave others. Each effect that ended also has
    an [`EffectReleasedEvent`][osrlib.core.events.EffectReleasedEvent], before this event.
    """

    allowed_codes: ClassVar[frozenset[str]] = frozenset({"magic.dispel.resolved"})
    """The only code this event uses."""

    event_type: Literal["magic_dispelled"] = "magic_dispelled"
    """The fixed string that identifies this event class in a serialized event."""

    code: str = "magic.dispel.resolved"
    """Fixed at `magic.dispel.resolved`."""

    visibility: Visibility = Visibility.PLAYER
    """Player visibility: the players see the spell and what it ended."""

    caster_id: str
    """The entity id of the caster of *dispel magic*."""

    released_effect_ids: tuple[str, ...] = ()
    """The ids of the effects the spell ended."""

    surviving_effect_ids: tuple[str, ...] = ()
    """The ids of the dispellable effects that survived their roll."""


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
"""Every event class of the core rules, as a tuple, in the order this module defines them.

osrlib calls the core rules the kernel, so these are its kernel events. Iterate the tuple when you need every
class rather than one event: to build a table of handlers, to generate a JSON Schema for each class, or to check
that your program handles every class. The same classes make up
[`KernelEvent`][osrlib.core.events.KernelEvent], which is what you annotate and validate with.

It has only the core rules' events. For those and the crawl's events, use
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
"""The union of every event class of the core rules, told apart by `event_type`.

Annotate with it where a value is one event of the core rules and you want its exact class back. Pass it to a
pydantic `TypeAdapter` to validate serialized events. Pydantic reads `event_type` and goes straight to the right
class instead of trying each in turn, and the JSON Schema it generates lists which class each `event_type` means, for
programs that read the schema.

To read back a stored log, call [`parse_event`][osrlib.core.events.parse_event] instead. It validates the same
way, but returns `None` for an event type your version of osrlib doesn't define, rather than raising. Annotate
with [`Event`][osrlib.core.events.Event] instead where any event will do.

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
    """Turn one stored event back into its class, or return `None` if your version of osrlib doesn't define its type.

    Use this to read a saved log, or events sent from another program. It returns `None` for an event it doesn't
    recognize, so an older release can read a log a newer one wrote, except for the events the older one has no
    class for. Leave out the `None` results and go on.

    It reads only the core rules' events. To read a log that also has crawl events, call
    [`parse_any_event`][osrlib.crawl.events.parse_any_event], which reads both. Neither reads an event class you
    defined yourself.

    Args:
        data: One event as a mapping, in the form `model_dump` produces.

    Returns:
        The event as an instance of its class, or `None` when the mapping's `event_type` isn't one your version of
        osrlib defines, or is missing.

    Raises:
        osrlib.errors.ContentValidationError: If osrlib defines the event type but the rest of the mapping doesn't
            fit the class, like a required field that's missing. A broken event of a known type is an error in the
            log, so `parse_event` raises rather than skipping it. The message includes pydantic's report.

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
