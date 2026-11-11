"""The rules behind the exploring and town commands, and the per-turn checks for fatigue, food, and wandering monsters.

A party gives these commands while it explores a dungeon, in `exploring` mode, or visits town, in
`town` mode: moving, doors, searching, traps, treasure, items, light, rest, spells, shopping, and
temple healing.
[`DropItems`][osrlib.crawl.commands.DropItems] and [`UseItem`][osrlib.crawl.commands.UseItem] also
work in `encounter` mode, so the party can drop or use gear while monsters stand in front of it.

You don't call a command handler here yourself. Build one of the command models in
[`osrlib.crawl.commands`][osrlib.crawl.commands], like [`MoveParty`][osrlib.crawl.commands.MoveParty],
[`Search`][osrlib.crawl.commands.Search], or [`Rest`][osrlib.crawl.commands.Rest], and pass it to
[`GameSession.execute`][osrlib.crawl.session.GameSession.execute]. The session returns a
[`CommandResult`][osrlib.crawl.commands.CommandResult]. It contains either
[`Rejection`][osrlib.core.validation.Rejection] models that give the reason the session refused the
command, or the event models of [`osrlib.crawl.events`][osrlib.crawl.events] that record what happened. The
session checks a command before it runs any of it, so for a refused command it rolls no dice,
changes no state, and spends no game time.

The public functions here are the checks the session runs as time passes, rather than in answer to a
command:

- [`exploration_rate`][osrlib.crawl.exploration.exploration_rate] takes the session and returns the
  party's movement rate in feet per turn.
- [`wandering_interval`][osrlib.crawl.exploration.wandering_interval] takes the session and returns
  how many turns pass between wandering-monster checks on the party's level.
- [`check_fatigue`][osrlib.crawl.exploration.check_fatigue] attaches the penalty for going too long
  without rest, and returns its events.
- [`consume_provisions`][osrlib.crawl.exploration.consume_provisions] feeds and waters the party for
  one day, and returns its events.
- [`wandering_check`][osrlib.crawl.exploration.wandering_check] rolls one wandering-monster check,
  and returns its events and whether an encounter opened.

[`GameSession.advance_turns`][osrlib.crawl.session.GameSession.advance_turns] and
[`GameSession.advance_rounds`][osrlib.crawl.session.GameSession.advance_rounds] run the last three
on their schedules, so if you move time by executing commands, you don't call them. Call
`exploration_rate` and `wandering_interval` to show the party's numbers on a status display. Call
the other three yourself only when you advance `GameSession.clock` directly instead of through those
methods, because each one changes the session.

The constants cover what those checks attach and what the temple sells.
[`FATIGUE_KIND`][osrlib.crawl.exploration.FATIGUE_KIND],
[`EXHAUSTED_KIND`][osrlib.crawl.exploration.EXHAUSTED_KIND], and
[`DEPRIVATION_KIND`][osrlib.crawl.exploration.DEPRIVATION_KIND] are the effect kinds of the
penalties for going without rest, running from a long chase, and going without food or water.
[`EXHAUSTED_DEFINITION`][osrlib.crawl.exploration.EXHAUSTED_DEFINITION] is the exhaustion effect
itself, and [`HEALING_SERVICES`][osrlib.crawl.exploration.HEALING_SERVICES] is the temple's
standard price list.

The session counts movement on its odometer,
[`GameSession.odometer_thirds`][osrlib.crawl.session.GameSession.odometer_thirds], in thirds of a
foot. A step into a cell the party hasn't explored costs 30 units, and a step into an explored one
costs 10, which is the SRD's rule that the party crosses familiar ground three times as fast. When
the total reaches three times the party's exploration rate, the clock advances one turn and the
odometer goes back to zero. At the base rate of 120 feet a turn, a turn passes after 12 steps into
unexplored cells, or 36 into explored ones. An action that takes a turn, like a search, advances the
clock a whole turn and sets the odometer back to zero.

The session draws the rolls for a trap from
[`EXPLORATION_STREAM`][osrlib.crawl.session.EXPLORATION_STREAM]: the 2-in-6 spring check (a d6 roll
of 1 or 2 springs the trap), the saving throws, the damage, and the number of darts or other
missiles. It rolls the duration of a condition a trap attaches on
[`EFFECTS_STREAM`][osrlib.core.effects.EFFECTS_STREAM] instead, like every other effect's duration.

Typical usage:

```python
from osrlib.core.abilities import AbilityScore
from osrlib.core.alignment import Alignment
from osrlib.core.character import Character
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import EnterDungeon, MoveParty
from osrlib.crawl.dungeon import Direction, DungeonSpec, Edge, EdgeKind, LevelSpec
from osrlib.crawl.exploration import exploration_rate
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession

hero = Character(
    name="Hild",
    class_id="fighter",
    race="human",
    level=1,
    xp=0,
    scores={ability: 12 for ability in AbilityScore},
    alignment=Alignment.LAWFUL,
    max_hp=8,
    current_hp=8,
)
level = LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)})
adventure = Adventure(
    name="A First Delve",
    town=TownSpec(name="Threshold"),
    dungeons=(DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,)),),
)
session = GameSession.new(Party(members=[hero]), adventure, seed=7)
session.execute(EnterDungeon(dungeon_id="crypt"))

moved = session.execute(MoveParty(direction=Direction.EAST))
assert [event.code for event in moved.events] == ["exploration.party.moved"]

blocked = session.execute(MoveParty(direction=Direction.EAST))  # the level is two cells wide
assert not blocked.accepted
assert blocked.rejections[0].code == "exploration.move.blocked"

print(exploration_rate(session))
# 120
```
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from osrlib.core.character import Character
from osrlib.core.clock import ROUNDS_PER_TURN, TimeUnit
from osrlib.core.combat import DamageSource, SaveCategory, deal_damage, natural_healing, saving_throw
from osrlib.core.creature import Creature
from osrlib.core.dice import roll
from osrlib.core.effects import EFFECTS_STREAM, Condition, EffectDefinition, ModifierSpec, has_condition
from osrlib.core.events import Event, SavingThrowRolledEvent
from osrlib.core.items import (
    Coins,
    ItemInstance,
    MagicItemCategory,
    MagicItemInstance,
    equip,
    magic_item_template,
    unequip,
    usable_by_class,
    validate_equip,
    validate_unequip,
)
from osrlib.core.spells import (
    MAGIC_STREAM,
    CastContext,
    SpellTemplate,
    _restores_life,
    _revives_poison_dead,
    add_spell_to_book,
    cast_from_scroll,
    cast_spell,
    caster_profile,
    memorize_spells,
    minimum_caster_level,
    validate_cast,
    validate_scroll_cast,
)
from osrlib.core.tables import select_encounter_individuals
from osrlib.core.validation import Rejection
from osrlib.crawl._doors import (
    _handle_close_door,
    _handle_force_door,
    _handle_listen_at_door,
    _handle_open_door,
    _handle_pick_lock,
    _handle_wedge_door,
)
from osrlib.crawl._hauling import _DENOMINATIONS, _add_to_pile, _handle_take_treasure
from osrlib.crawl._light import _handle_extinguish_source, _handle_light_source, _requires_light
from osrlib.crawl._movement import (
    _handle_enter_dungeon,
    _handle_move_party,
    _handle_reorder_party,
    _handle_travel_to_town,
    _handle_turn_party,
    _handle_use_stairs,
)
from osrlib.crawl._odometer import _spend_turn
from osrlib.crawl._party_location import _cell_ref, _level, _location
from osrlib.crawl._party_members import _consume_item, _find_item, _grant_mundane, _member_able, _remove_instance
from osrlib.crawl._search import _handle_inspect_treasure, _handle_remove_treasure_trap, _handle_search
from osrlib.crawl._treasure import _assign_carried, _field_npc_party, _generate_carried_treasure
from osrlib.crawl.commands import (
    CastSpell,
    CloseDoor,
    DropItems,
    EnterDungeon,
    EquipItem,
    ExtinguishSource,
    ForceDoor,
    GiveItems,
    HealingService,
    InspectTreasure,
    LearnSpell,
    LightSource,
    ListenAtDoor,
    MoveParty,
    OpenDoor,
    PickLock,
    PrepareSpells,
    PurchaseEquipment,
    PurchaseHealing,
    RemoveTreasureTrap,
    ReorderParty,
    Rest,
    Search,
    SellTreasure,
    SessionMode,
    TakeTreasure,
    TravelToTown,
    TurnParty,
    UnequipItem,
    UseItem,
    UseStairs,
    WedgeDoor,
)
from osrlib.crawl.dungeon import DroppedItem, DropPile
from osrlib.crawl.events import (
    CurseRevealedEvent,
    FatigueEvent,
    HealingPurchasedEvent,
    ItemAcquiredEvent,
    ItemIdentifiedEvent,
    ItemsDroppedEvent,
    ItemsGivenEvent,
    ItemUsedEvent,
    LightEvent,
    ProvisionsEvent,
    RestedEvent,
    TreasureSoldEvent,
    WanderingCheckEvent,
)
from osrlib.crawl.party import _stands
from osrlib.data import load_classes, load_encounter_tables, load_equipment, load_spells

__all__ = [
    "DEPRIVATION_KIND",
    "EXHAUSTED_DEFINITION",
    "EXHAUSTED_KIND",
    "FATIGUE_KIND",
    "HEALING_SERVICES",
    "check_fatigue",
    "consume_provisions",
    "exploration_rate",
    "wandering_check",
    "wandering_interval",
]

FATIGUE_KIND = "fatigue"
"""The effect kind of the penalty for going too long without rest.

Pass it with a member's id to
[`EffectsLedger.active_on`][osrlib.core.effects.EffectsLedger.active_on] to ask whether that member
is fatigued: `session.ledger.active_on(member.id, FATIGUE_KIND)` returns the active effects, and an
empty list means the member is rested. Use that query for a status display.
[`check_fatigue`][osrlib.crawl.exploration.check_fatigue] makes the same query before it attaches
anything. The effect applies −1 to attack rolls and −1 to damage rolls and has no duration. In play,
a [`Rest`][osrlib.crawl.commands.Rest] releases it once the party has rested one full turn, even if a
wandering monster interrupts the rest later.
"""

EXHAUSTED_KIND = "exhausted"
"""The effect kind of the penalty for running from a long chase.

Query it the same way as [`FATIGUE_KIND`][osrlib.crawl.exploration.FATIGUE_KIND]. A party that keeps
running until a chase lasts [`PURSUIT_ROUND_CAP`][osrlib.crawl.encounter.PURSUIT_ROUND_CAP] rounds
gets away and is exhausted, and three turns of [`Rest`][osrlib.crawl.commands.Rest] clear it.
[`EXHAUSTED_DEFINITION`][osrlib.crawl.exploration.EXHAUSTED_DEFINITION] is the effect attached
under this kind.
"""

DEPRIVATION_KIND = "deprivation"
"""The effect kind of the penalty for going without food or water.

Query it the same way as [`FATIGUE_KIND`][osrlib.crawl.exploration.FATIGUE_KIND]. Nothing attaches
under this kind unless
[`Ruleset.deprivation_penalties`][osrlib.core.ruleset.Ruleset.deprivation_penalties] is on, because
the penalties for going hungry or thirsty are optional.
[`consume_provisions`][osrlib.crawl.exploration.consume_provisions] keeps the day counts it depends
on either way, in `session.deprivation`. The effect applies −1 to attack rolls. It attaches on the
first day a member goes without food or water, and is released on the first day they have both.
"""

_FATIGUE_DEFINITION = EffectDefinition(
    kind=FATIGUE_KIND,
    stacking="ignore",
    modifiers=(
        ModifierSpec(kind="attack_bonus", value=-1),
        ModifierSpec(kind="damage_bonus", value=-1),
    ),
)

EXHAUSTED_DEFINITION = EffectDefinition(
    kind=EXHAUSTED_KIND,
    condition=Condition.EXHAUSTED,
    stacking="ignore",
    modifiers=(
        ModifierSpec(kind="attack_bonus", value=-2),
        ModifierSpec(kind="damage_bonus", value=-2),
        ModifierSpec(kind="attack_penalty_of_attackers", value=2),
    ),
)
"""The exhaustion effect: −2 to attack rolls, −2 to damage rolls, and 2 points worse armour class.

The session attaches it to every living member who isn't already exhausted when a chase lasts
[`PURSUIT_ROUND_CAP`][osrlib.crawl.encounter.PURSUIT_ROUND_CAP] rounds and the party gets away, and
three turns of [`Rest`][osrlib.crawl.commands.Rest] release it. Attach this same definition with
[`EffectsLedger.attach`][osrlib.core.effects.EffectsLedger.attach] when your own code exhausts a
member. A second definition of your own would leave a member under two penalties at once. To ask
whether a member is exhausted, query [`EXHAUSTED_KIND`][osrlib.crawl.exploration.EXHAUSTED_KIND]
rather than this object.

The definition grants [`Condition.EXHAUSTED`][osrlib.core.effects.Condition] and stacks as `ignore`,
so attaching it to a member who is already exhausted does nothing. It has no duration, so it stays
until something releases it: in play, rest does, and time alone doesn't. The worse armour class is
an `attack_penalty_of_attackers` modifier of +2, so attackers of an exhausted creature get +2 to
hit, which is the same as descending armour class 2 points worse.
"""

_DEPRIVATION_DEFINITION = EffectDefinition(
    kind=DEPRIVATION_KIND,
    stacking="ignore",
    modifiers=(ModifierSpec(kind="attack_bonus", value=-1),),
)


# ---------------------------------------------------------------------- light gates


def _int_param(params: Mapping[str, Any], key: str, default: int = 0) -> int:
    """Read an integer param: schema-validated data whose union type the type checker can't index by key name."""
    return int(params.get(key, default))


# ---------------------------------------------------------------------- time and cadence


def exploration_rate(session) -> int:
    """Return the party's exploration rate in feet per turn.

    A party moves at the pace of its slowest living member, so that member's movement rate is the
    party's. Read it to show how far the party moves in a turn, or to find out whether the party can
    move at all. A rate of 0 means that a living member is carrying too much to move, or that no
    member is living. In the first case the session refuses a
    [`MoveParty`][osrlib.crawl.commands.MoveParty] with `exploration.move.cannot_move` and the
    reason `overloaded`, until the member lightens the load with
    [`DropItems`][osrlib.crawl.commands.DropItems] or [`GiveItems`][osrlib.crawl.commands.GiveItems].
    In the second case the session is already in `game_over`.

    For one character's movement rate rather than the party's, call
    [`Character.movement_rate`][osrlib.core.character.Character.movement_rate], which this function
    calls for each member.

    You don't spend the rate yourself. The session counts each step against it on an odometer and
    advances the clock a turn when the odometer fills. The rate changes as the party's load does,
    so read it again after anything that changes what the party carries.

    Under the `deprivation_penalties` ruleset flag, a member two or more days into the worse of
    their food and water tracks moves at half rate, which slows the whole party.

    Args:
        session (osrlib.crawl.session.GameSession): The running session.

    Returns:
        The slowest living member's movement rate in feet per turn, or 0 when no member is living.

    Examples:
        ```python
        from osrlib.core.abilities import AbilityScore
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import Character
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.commands import EnterDungeon
        from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
        from osrlib.crawl.exploration import exploration_rate
        from osrlib.crawl.party import Party
        from osrlib.crawl.session import GameSession

        hero = Character(
            name="Hild",
            class_id="fighter",
            race="human",
            level=1,
            xp=0,
            scores={ability: 12 for ability in AbilityScore},
            alignment=Alignment.LAWFUL,
            max_hp=8,
            current_hp=8,
        )
        level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
        adventure = Adventure(
            name="A First Delve",
            town=TownSpec(name="Threshold"),
            dungeons=(DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,)),),
        )
        session = GameSession.new(Party(members=[hero]), adventure, seed=7)
        session.execute(EnterDungeon(dungeon_id="crypt"))

        print(exploration_rate(session))
        # 120
        ```
    """
    rates = []
    for member in session.party.living_members():
        rate = member.movement_rate(session.ruleset)
        if session.ruleset.deprivation_penalties:
            state = session.deprivation.get(member.id)
            if state is not None and state.worst >= 2:
                rate //= 2
        rates.append(rate)
    return min(rates, default=0)


def _fatigue_threshold(session) -> int:
    """Six unrested turns, or three when any living member is a day deprived and the flag is on."""
    if session.ruleset.deprivation_penalties:
        for member in session.party.living_members():
            state = session.deprivation.get(member.id)
            if state is not None and state.worst >= 1:
                return 3
    return 6


def check_fatigue(session) -> list[Event]:
    """Attach the fatigue penalty once the party has gone too long without a rest.

    The SRD's dungeon rule is that a party rests one turn in every six, and a party that presses on
    takes −1 to attack rolls and −1 to damage rolls until it does. This is the check for that rule.
    It reads the session's `turns_since_rest` counter. When the counter has reached the threshold, it
    attaches the fatigue effect to every living member who isn't fatigued already.

    [`GameSession.advance_turns`][osrlib.crawl.session.GameSession.advance_turns] calls this for
    every turn that passes while the session is in `exploring` mode, except a turn spent resting, so
    if you move time by executing commands, you don't call it. Call it yourself when you
    move the clock some other way. To ask whether the party is already fatigued, query
    [`FATIGUE_KIND`][osrlib.crawl.exploration.FATIGUE_KIND] against `session.ledger` instead of
    calling this. A [`Rest`][osrlib.crawl.commands.Rest] releases the effect and sets the counter back
    to zero once the party has rested one full turn, even if a wandering monster interrupts the rest
    later.

    The threshold is six unrested turns, or three when the `deprivation_penalties` ruleset flag is
    on and some living member has gone a full day without food or water.

    Args:
        session (osrlib.crawl.session.GameSession): The running session.

    Returns:
        The attachment events, closed by a [`FatigueEvent`][osrlib.crawl.events.FatigueEvent] with
        code `exploration.fatigue.gained`, when at least one member gained the effect on this call.
        The list is empty before the threshold, and empty when every living member is already
        fatigued, so calling this again during the same unrested stretch attaches nothing. A member
        who joins an already-tired party gains the effect on the next call.

    Examples:
        ```python
        from osrlib.core.abilities import AbilityScore
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import Character
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.commands import EnterDungeon
        from osrlib.crawl.dungeon import DungeonSpec, LevelSpec, WanderingSpec
        from osrlib.crawl.exploration import FATIGUE_KIND, check_fatigue
        from osrlib.crawl.party import Party
        from osrlib.crawl.session import GameSession

        hero = Character(
            name="Hild",
            class_id="fighter",
            race="human",
            level=1,
            xp=0,
            scores={ability: 12 for ability in AbilityScore},
            alignment=Alignment.LAWFUL,
            max_hp=8,
            current_hp=8,
        )
        # chance_in_six=0 keeps a wandering monster from interrupting the six turns.
        level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0), wandering=WanderingSpec(chance_in_six=0))
        adventure = Adventure(
            name="A First Delve",
            town=TownSpec(name="Threshold"),
            dungeons=(DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,)),),
        )
        session = GameSession.new(Party(members=[hero]), adventure, seed=7)
        session.execute(EnterDungeon(dungeon_id="crypt"))

        # advance_turns runs the check itself, so the fatigue event is already in its events.
        events, interrupted = session.advance_turns(6)
        assert not interrupted
        assert "exploration.fatigue.gained" in [event.code for event in events]
        assert session.ledger.active_on(hero.id, FATIGUE_KIND)

        print(check_fatigue(session))
        # []
        ```
    """
    if session.turns_since_rest < _fatigue_threshold(session):
        return []
    events: list[Event] = []
    attached = False
    for member in session.party.living_members():
        # Per member, not per party: someone who joined partway through the unrested
        # stretch, or who lost the effect some other way, is as tired as everyone else.
        if session.ledger.active_on(member.id, FATIGUE_KIND):
            continue
        _, attach_events = session.ledger.attach(
            _FATIGUE_DEFINITION,
            member.id,
            clock=session.clock,
            allocator=session.allocator,
            registry=session.registry(),
        )
        events.extend(attach_events)
        attached = True
    if not attached:
        return []
    events.append(FatigueEvent(code="exploration.fatigue.gained"))
    return events


def _clear_fatigue(session) -> list[Event]:
    events: list[Event] = []
    cleared = False
    for member in session.party.members:
        for effect in list(session.ledger.active_on(member.id, FATIGUE_KIND)):
            events.extend(session.ledger.release(effect.effect_id, session.registry()))
            cleared = True
    session.turns_since_rest = 0
    if cleared:
        events.append(FatigueEvent(code="exploration.fatigue.recovered"))
    return events


def _credit_exhaustion_rest(session, rest_turns: int) -> list[Event]:
    """Credit rest turns against running exhaustion, which three full turns clear."""
    events: list[Event] = []
    recovered = False
    for member in session.party.members:
        for effect in list(session.ledger.active_on(member.id, EXHAUSTED_KIND)):
            effect.state["rest_turns"] = effect.state.get("rest_turns", 0) + rest_turns
            if effect.state["rest_turns"] >= 3:
                events.extend(session.ledger.release(effect.effect_id, session.registry()))
                recovered = True
    if recovered:
        from osrlib.crawl.events import ExhaustionEvent

        events.append(ExhaustionEvent(code="encounter.exhaustion.recovered"))
    return events


def consume_provisions(session) -> list[Event]:
    """Feed and water every living member for one day, and report who went short.

    One call covers one day. Each living member eats a standard ration, or an iron ration when they
    have no standard one, since fresh food spoils first. Each drinks from a waterskin they carry.
    The waterskin isn't used up, so carrying one covers every day. In town nobody goes short:
    a member still eats their rations, and a member with none is fed anyway.

    [`GameSession.advance_rounds`][osrlib.crawl.session.GameSession.advance_rounds] calls this each
    time the clock crosses into a new day, and every command that passes time goes through it, so
    if you move time by executing commands, you don't call this. Call it yourself when you
    move the clock some other way.

    `session.deprivation` maps each member's id to a
    [`DeprivationState`][osrlib.crawl.session.DeprivationState] that counts the days in a row the
    member has gone without food and without water. A day with food sets the food count back to
    zero, and a day without adds one, and the same goes for water. This function keeps the counts
    however the ruleset is set, so you can show a hunger indicator without turning the penalties on.
    When [`Ruleset.deprivation_penalties`][osrlib.core.ruleset.Ruleset.deprivation_penalties] is on,
    the member also takes penalties from the counts: −1 to attack rolls and fatigue twice as fast
    from one day, half movement from two, and 1d4 damage a day from three. Food and water don't add
    up: the worse of the two counts sets the penalty. The SRD gives examples rather than a schedule,
    so osrlib chose this one and records it in the
    [adaptations register](https://mmacy.github.io/osrlib-python/adaptations/).

    Args:
        session (osrlib.crawl.session.GameSession): The running session.

    Returns:
        One [`ProvisionsEvent`][osrlib.crawl.events.ProvisionsEvent] per living member per resource,
        food then water, coded `exploration.provisions.consumed` or `exploration.provisions.short`.
        Under the flag, each member's pair is followed by the events of the deprivation schedule:
        the effect attaching or releasing, and the damage from the third day on.

    Examples:
        ```python
        from osrlib.core.abilities import AbilityScore
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import Character
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.commands import EnterDungeon
        from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
        from osrlib.crawl.exploration import consume_provisions
        from osrlib.crawl.party import Party
        from osrlib.crawl.session import GameSession

        hero = Character(
            name="Hild",
            class_id="fighter",
            race="human",
            level=1,
            xp=0,
            scores={ability: 12 for ability in AbilityScore},
            alignment=Alignment.LAWFUL,
            max_hp=8,
            current_hp=8,
        )
        level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
        adventure = Adventure(
            name="A First Delve",
            town=TownSpec(name="Threshold"),
            dungeons=(DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,)),),
        )
        session = GameSession.new(Party(members=[hero]), adventure, seed=7)
        session.execute(EnterDungeon(dungeon_id="crypt"))

        events = consume_provisions(session)  # Hild carries no rations and no waterskin
        print([(event.code, event.kind) for event in events])
        # [('exploration.provisions.short', 'food'), ('exploration.provisions.short', 'water')]

        assert session.deprivation[hero.id].worst == 1
        ```
    """
    events: list[Event] = []
    in_town = _location(session).kind == "town"
    for member in session.party.living_members():
        state = session.deprivation.get(member.id)
        if state is None:
            state = _new_deprivation()
            session.deprivation[member.id] = state
        # Food: standard before iron.
        if _consume_item(member, "rations_standard") or _consume_item(member, "rations_iron") or in_town:
            state.food_days = 0
            events.append(ProvisionsEvent(code="exploration.provisions.consumed", character_id=member.id, kind="food"))
        else:
            state.food_days += 1
            events.append(ProvisionsEvent(code="exploration.provisions.short", character_id=member.id, kind="food"))
        if _find_item(member, "waterskin") is not None or in_town:
            state.water_days = 0
            events.append(ProvisionsEvent(code="exploration.provisions.consumed", character_id=member.id, kind="water"))
        else:
            state.water_days += 1
            events.append(ProvisionsEvent(code="exploration.provisions.short", character_id=member.id, kind="water"))
        if session.ruleset.deprivation_penalties:
            events.extend(_sync_deprivation(session, member, state))
    return events


def _new_deprivation():
    from osrlib.crawl.session import DeprivationState

    return DeprivationState()


def _sync_deprivation(session, member, state) -> list[Event]:
    """Apply the deprivation schedule: −1 attack at one day, 1d4/day at three."""
    events: list[Event] = []
    active = session.ledger.active_on(member.id, DEPRIVATION_KIND)
    if state.worst >= 1 and not active:
        _, attach_events = session.ledger.attach(
            _DEPRIVATION_DEFINITION,
            member.id,
            clock=session.clock,
            allocator=session.allocator,
            registry=session.registry(),
        )
        events.extend(attach_events)
    elif state.worst == 0 and active:
        for effect in list(active):
            events.extend(session.ledger.release(effect.effect_id, session.registry()))
    if state.worst >= 3:
        result = roll("1d4", session.streams.get(EFFECTS_STREAM))
        events.extend(
            deal_damage(
                member,
                result.total,
                source=DamageSource(kind="deprivation"),
                rolls=result.rolls,
                clock=session.clock,
            )
        )
    return events


# ---------------------------------------------------------------------- wandering


def wandering_interval(session) -> int:
    """Return how many turns pass between wandering-monster checks on the level the party is on.

    Each dungeon level has an interval, set in its
    [`WanderingSpec`][osrlib.crawl.dungeon.WanderingSpec]. The default is two turns, as in the SRD.
    The session counts turns against this number. When the count reaches it, the session runs
    [`wandering_check`][osrlib.crawl.exploration.wandering_check] and starts counting again. Read it
    to show how often the checks come, or to run checks on the same schedule from a clock of your
    own.

    Args:
        session (osrlib.crawl.session.GameSession): The running session.

    Returns:
        The interval in turns for the level the party is on. Outside a dungeon it's 1000000000, a
        number the turn counter never reaches, because no wandering-monster checks run in town or
        on the road.

    Examples:
        ```python
        from osrlib.core.abilities import AbilityScore
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import Character
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.commands import EnterDungeon
        from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
        from osrlib.crawl.exploration import wandering_interval
        from osrlib.crawl.party import Party
        from osrlib.crawl.session import GameSession

        hero = Character(
            name="Hild",
            class_id="fighter",
            race="human",
            level=1,
            xp=0,
            scores={ability: 12 for ability in AbilityScore},
            alignment=Alignment.LAWFUL,
            max_hp=8,
            current_hp=8,
        )
        level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
        adventure = Adventure(
            name="A First Delve",
            town=TownSpec(name="Threshold"),
            dungeons=(DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,)),),
        )
        session = GameSession.new(Party(members=[hero]), adventure, seed=7)

        print(wandering_interval(session))  # the session starts in town
        # 1000000000

        session.execute(EnterDungeon(dungeon_id="crypt"))
        print(wandering_interval(session))
        # 2
        ```
    """
    if _location(session).kind != "dungeon":
        return 10**9
    return _level(session).wandering.interval_turns


def wandering_check(session, *, resting: bool = False) -> tuple[list[Event], bool]:
    """Roll one wandering-monster check, and when monsters come, spawn them and open the encounter.

    [`GameSession.advance_turns`][osrlib.crawl.session.GameSession.advance_turns] calls this every
    [`wandering_interval`][osrlib.crawl.exploration.wandering_interval] turns while the session is in
    `exploring` mode, so if you move time by executing commands, you don't call it. Call it
    yourself to run the checks from a clock of your own, or to make an extra check the rules don't
    schedule.

    Every check clears the session's noise flag, because the noise has now counted for a check.

    When monsters come, the check also changes these:

    - It spawns the monsters into the session and opens an encounter.
    - It puts the session in `encounter` mode, or in `battle` mode when the monsters attack at
      once. The session then refuses the exploring commands and accepts the encounter commands.
    - When `advance_turns` made the call, it passes no more time.

    The chance in six starts from the level's chance and changes like this:

    - +1 for noise since the last check.
    - +1 for light as bright as daylight.
    - −1 while the party hides. Resting alone doesn't count. The OSE SRD lowers the chance only
      for a party that rests quietly in an out-of-the-way place, which it calls hiding. To have
      the party hide, send a [`Rest`][osrlib.crawl.commands.Rest] with `hidden` set.

    The chance is limited to the range 0 to 6. When it's 0, the check makes no random roll and
    reports that no monsters came.

    The check, the roll on the encounter table, and the number of monsters use
    [`WANDERING_STREAM`][osrlib.crawl.session.WANDERING_STREAM], one of the session's named
    random number streams. The monsters and the encounter that follows use their own streams.

    Args:
        session (osrlib.crawl.session.GameSession): The running session.
        resting: True while the party rests quietly in an out-of-the-way place, which the OSE SRD
            calls hiding. It applies the −1 to the chance. A plain rest passes False.

    Returns:
        The check's events, and True when an encounter opened. The events start with one
        [`WanderingCheckEvent`][osrlib.crawl.events.WanderingCheckEvent] that contains the chance and
        the roll, with `roll` set to None when the chance was 0. When monsters come, the encounter's opening
        events follow it, after an
        [`NpcPartySpawnedEvent`][osrlib.crawl.events.NpcPartySpawnedEvent] when the monsters are an
        NPC party.

    Raises:
        ValueError: If the party isn't in a dungeon, because no wandering-monster checks run in
            town or on the road. The call changes nothing before it raises. To check first, test
            that `session.dungeon_state.location.kind` is `"dungeon"`.

    Examples:
        ```python
        from osrlib.core.abilities import AbilityScore
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import Character
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.commands import EnterDungeon
        from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
        from osrlib.crawl.exploration import wandering_check
        from osrlib.crawl.party import Party
        from osrlib.crawl.session import GameSession

        hero = Character(
            name="Hild",
            class_id="fighter",
            race="human",
            level=1,
            xp=0,
            scores={ability: 12 for ability in AbilityScore},
            alignment=Alignment.LAWFUL,
            max_hp=8,
            current_hp=8,
        )
        level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
        adventure = Adventure(
            name="A First Delve",
            town=TownSpec(name="Threshold"),
            dungeons=(DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,)),),
        )
        session = GameSession.new(Party(members=[hero]), adventure, seed=7)
        session.execute(EnterDungeon(dungeon_id="crypt"))

        events, encountered = wandering_check(session)
        check = events[0]
        print((check.chance, check.roll, encountered))  # 1 in 6, rolled a 4, nothing came
        # (1, 4, False)
        ```
    """
    from osrlib.crawl import encounter as encounter_module
    from osrlib.crawl.session import WANDERING_STREAM

    level = _level(session)
    chance = level.wandering.chance_in_six
    if session.noise_since_check:
        chance += 1
    if session.bright_light():
        chance += 1
    if resting:
        chance -= 1
    chance = max(0, min(6, chance))
    session.noise_since_check = False
    stream = session.streams.get(WANDERING_STREAM)
    if chance == 0:
        return [WanderingCheckEvent(chance=0, roll=None, encounter=False)], False
    check_roll = stream.randbelow(6) + 1
    hit = check_roll <= chance
    events: list[Event] = [WanderingCheckEvent(chance=chance, roll=check_roll, encounter=hit)]
    if not hit:
        return events, False
    table = level.wandering.table or load_encounter_tables().for_level(level.number)
    row = table.rows[stream.randbelow(20)]
    count = row.count_fixed if row.count_fixed is not None else roll(row.count_dice, stream).total
    count = max(1, count)
    entry = row.entry
    if entry.kind == "npc_party":
        # An NPC-party row generates a party rather than a monster group. This function
        # rolls the row's count dice on the wandering stream, the generator builds the
        # party, and the encounter opens as usual: surprise for both sides, distance,
        # and a reaction roll. NPC parties roll on the reaction table like monsters,
        # parley works on them, and an author's fixed stance doesn't apply.
        party, bundle, npc_events = _field_npc_party(session, entry.party_kind, count)
        events.extend(npc_events)
        events.extend(
            encounter_module._open_encounter(
                session,
                groups=[(*encounter_module._group_labels(party.members), party.members)],
                kind="wandering",
                monsters_roll_surprise=True,
            )
        )
        _assign_carried(session, [({}, bundle)])
        return events, True
    template_ids = select_encounter_individuals(entry, count, stream)
    instances = []
    for template_id in template_ids:
        instances.extend(session.spawn(template_id, 1))
    carried = [_generate_carried_treasure(session, instances)]
    events.extend(
        encounter_module._open_encounter(
            session,
            groups=[(*encounter_module._group_labels(instances, row_name=row.name), instances)],
            kind="wandering",
            monsters_roll_surprise=False,  # wandering monsters know the dungeon
        )
    )
    _assign_carried(session, carried)
    return events, True


# ---------------------------------------------------------------------- items and light


def _handle_drop_items(session, command: DropItems) -> tuple[list[Rejection], list[Event]]:
    if session.mode is SessionMode.ENCOUNTER:
        from osrlib.crawl import encounter as encounter_module

        return encounter_module._handle_drop_during_encounter(session, command)
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    rejections = _validate_carried(member, command.item_ids, command.coins)
    if rejections:
        return rejections, []
    events = _apply_drop(session, member, command, to_pile=True)
    return [], events


def _validate_carried(member, item_ids: tuple[str, ...], coins: Coins) -> list[Rejection]:
    """Guard the item/coin handout shared by drop and give.

    The member must carry every named item and coin, and no revealed cursed item
    may leave its bearer.

    The instance id of a magic item or a valuable identifies one object, so a command
    that lists it twice requests something the member doesn't carry: the object leaves
    on the first listing, and nothing is left for the second.
    """
    counts: dict[str, int] = {}
    named_instances: set[str] = set()
    for item_id in item_ids:
        magic = member.inventory.magic_item(item_id)
        if magic is not None and magic.cursed_revealed:
            # A revealed cursed item stays with its bearer until *remove curse*.
            return [Rejection(code="items.curse.stuck", params={"item": item_id})]
        if magic is not None or any(valuable.instance_id == item_id for valuable in member.inventory.valuables):
            if item_id in named_instances:
                return [Rejection(code="exploration.item.not_carried", params={"item": item_id})]
            named_instances.add(item_id)
            continue
        counts[item_id] = counts.get(item_id, 0) + 1
    for item_id, needed in counts.items():
        held = sum(
            instance.quantity
            for instance in member.inventory.all_instances()
            if not isinstance(instance, MagicItemInstance) and instance.template.id == item_id
        )
        if held < needed:
            return [Rejection(code="exploration.item.not_carried", params={"item": item_id})]
    purse = member.inventory.purse
    for denomination in ("pp", "gp", "ep", "sp", "cp"):
        if getattr(coins, denomination) > getattr(purse, denomination):
            return [Rejection(code="exploration.item.not_carried", params={"item": denomination})]
    return []


def _handle_give_items(session, command: GiveItems) -> tuple[list[Rejection], list[Event]]:
    giver, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    if command.recipient_id == command.character_id:
        return [Rejection(code="exploration.give.same_member", params={"character": command.character_id})], []
    recipient, rejections = _member_able(session, command.recipient_id)
    if rejections:
        return rejections, []
    rejections = _validate_carried(giver, command.item_ids, command.coins)
    if rejections:
        return rejections, []
    events = _apply_give(session, giver, recipient, command.item_ids, command.coins)
    if session.mode is SessionMode.ENCOUNTER:
        from osrlib.crawl import encounter as encounter_module

        events.extend(encounter_module._pass_round(session))
    return [], events


def _apply_give(session, giver, recipient, item_ids: tuple[str, ...], coins: Coins) -> list[Event]:
    """Move the named goods and coins from giver to recipient, and take no time.

    [`GiveItems`][osrlib.crawl.commands.GiveItems] and a battle's `give_item` declaration both hand
    goods over through this, after their checks have passed. A given magic item releases any worn
    effects first, then arrives unequipped in the recipient's pack. Mundane items merge into a like
    stack, and coins move purse to purse.
    """
    events: list[Event] = []
    for item_id in item_ids:
        magic = giver.inventory.magic_item(item_id)
        if magic is not None:
            events.extend(_release_instance_effects(session, magic))
            _remove_instance(giver, magic)
            recipient.inventory.items.append(magic)
            continue
        valuable = next(
            (candidate for candidate in giver.inventory.valuables if candidate.instance_id == item_id), None
        )
        if valuable is not None:
            giver.inventory.valuables.remove(valuable)
            recipient.inventory.valuables.append(valuable)
            continue
        # The giver's instance brings its template across: no catalog
        # resolution, so an adventure-bundled item hands over like any other. One
        # lookup takes it and returns what was taken, so nothing can shift between.
        carried = _consume_item(giver, item_id)
        if not isinstance(carried, ItemInstance):  # unreachable: magic left through the branch above
            raise ValueError(f"{giver.name} does not carry {item_id!r}")
        _grant_mundane(recipient, carried.template)
    giver_purse = giver.inventory.purse
    recipient_purse = recipient.inventory.purse
    for denomination in ("pp", "gp", "ep", "sp", "cp"):
        moved = getattr(coins, denomination)
        setattr(giver_purse, denomination, getattr(giver_purse, denomination) - moved)
        setattr(recipient_purse, denomination, getattr(recipient_purse, denomination) + moved)
    events.append(
        ItemsGivenEvent(
            character_id=giver.id,
            recipient_id=recipient.id,
            item_ids=tuple(item_ids),
            coins_gp_value=coins.value_gp,
        )
    )
    return events


def _apply_drop(session, member, command: DropItems, *, to_pile: bool) -> list[Event]:
    """Remove the dropped goods, onto the cell's pile or scattered (pursuit bait)."""
    goods = DropPile(coins=command.coins)
    for item_id in command.item_ids:
        magic = member.inventory.magic_item(item_id)
        if magic is not None:
            _remove_instance(member, magic)
            goods.magic_items.append(magic)
            continue
        valuable = next(
            (candidate for candidate in member.inventory.valuables if candidate.instance_id == item_id), None
        )
        if valuable is not None:
            member.inventory.valuables.remove(valuable)
            goods.valuables.append(valuable)
            continue
        _consume_item(member, item_id)
        existing = next((entry for entry in goods.items if entry.item_id == item_id), None)
        if existing is None:
            goods.items.append(DroppedItem(item_id=item_id, quantity=1))
        else:
            existing.quantity += 1
    purse = member.inventory.purse
    for denomination in _DENOMINATIONS:
        setattr(purse, denomination, getattr(purse, denomination) - getattr(command.coins, denomination))
    if to_pile:
        _add_to_pile(session, goods)
    return [
        ItemsDroppedEvent(
            character_id=member.id, item_ids=tuple(command.item_ids), coins_gp_value=command.coins.value_gp
        )
    ]


def _handle_equip_item(session, command: EquipItem) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    instance = next(
        (
            candidate
            for candidate in member.inventory.items
            if (
                candidate.instance_id == command.item_id
                if isinstance(candidate, MagicItemInstance)
                else candidate.template.id == command.item_id
            )
        ),
        None,
    )
    if instance is None:
        return [Rejection(code="exploration.item.not_carried", params={"item": command.item_id})], []
    definition = member.definition
    equip_rejections = validate_equip(definition, instance, member.inventory)
    if equip_rejections:
        return equip_rejections, []
    equip(member.inventory, definition, instance)
    events: list[Event] = []
    if isinstance(instance, MagicItemInstance):
        events.extend(_attach_worn_item_effects(session, member, instance))
    return [], events


def _handle_unequip_item(session, command: UnequipItem) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    inventory = member.inventory
    instance = next(
        (
            candidate
            for candidate in inventory.equipped_instances()
            if (
                candidate.instance_id == command.item_id
                if isinstance(candidate, MagicItemInstance)
                else candidate.template.id == command.item_id
            )
        ),
        None,
    )
    if instance is None:
        return [Rejection(code="exploration.item.not_equipped", params={"item": command.item_id})], []
    unequip_rejections = validate_unequip(inventory, instance)
    if unequip_rejections:
        return unequip_rejections, []
    events: list[Event] = []
    if isinstance(instance, MagicItemInstance):
        events.extend(_release_instance_effects(session, instance))
    unequip(inventory, instance)
    return [], events


def _handle_purchase_equipment(session, command: PurchaseEquipment) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    # The shop stocks the built-in equipment lists. An adventure's bundled items
    # enter play through caches and grants, never off a shelf.
    equipment = load_equipment()
    templates = []
    total_cost = 0
    for item_id in command.item_ids:
        try:
            template = equipment.get(item_id)
        except ValueError:
            try:
                session.effective_equipment.get(item_id)
            except ValueError:
                return [Rejection(code="session.command.unknown_item", params={"item": item_id})], []
            return [Rejection(code="items.purchase.not_stocked", params={"item": item_id})], []
        templates.append(template)
        total_cost += template.cost_gp
    if not member.inventory.purse.can_afford(total_cost):
        return [Rejection(code="items.purchase.insufficient_funds", params={"cost_gp": total_cost})], []
    from osrlib.core.items import purchase

    for template in templates:
        purchase(member.inventory, template, 1)
    return [], [ItemAcquiredEvent(character_id=member.id, item_ids=tuple(command.item_ids), origin="purchase")]


# ---------------------------------------------------------------------- rest and magic


def _handle_rest(session, command: Rest) -> tuple[list[Rejection], list[Event]]:
    turns = {"turn": 1, "night": 48, "day": 144}[command.kind]
    start = session.clock.rounds
    resting_events, interrupted = (
        _spend_turn(session, resting=True, hidden=command.hidden)
        if turns == 1
        else session.advance_turns(turns, resting=True, hidden=command.hidden)
    )
    events = list(resting_events)
    if turns > 1:
        session.odometer_thirds = 0
    if interrupted:
        # The wandering check is the first step of a turn, so every whole turn that passed
        # before it is a full turn rested. Those turns still clear fatigue and count toward
        # recovery from exhaustion, but an interrupted rest isn't sleep and heals nobody.
        rested_turns = (session.clock.rounds - start) // ROUNDS_PER_TURN
        if rested_turns:
            events.extend(_clear_fatigue(session))
            events.extend(_credit_exhaustion_rest(session, rested_turns))
        events.append(RestedEvent(code="exploration.rest.interrupted", kind=command.kind))
        return [], events
    events.extend(_clear_fatigue(session))
    events.extend(_credit_exhaustion_rest(session, turns))
    if command.kind in ("night", "day"):
        session.sleep_count += 1
    if command.kind == "day":
        for member in session.party.living_members():
            events.extend(natural_healing(member, session.streams.get(EFFECTS_STREAM), ledger=session.ledger))
    events.append(RestedEvent(code="exploration.rest.rested", kind=command.kind))
    return [], events


def _handle_prepare_spells(session, command: PrepareSpells) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    if session.sleep_count == 0 or session.last_prepared_sleep.get(member.id, 0) >= session.sleep_count:
        return [Rejection(code="magic.memorize.needs_sleep", params={"character": member.id})], []
    definition = member.definition
    result = memorize_spells(member, definition, load_spells(), command.selections)
    if result.rejections:
        return list(result.rejections), []
    session.last_prepared_sleep[member.id] = session.sleep_count
    events = list(result.events)
    turn_events, _ = session.advance_turns(6)
    events.extend(turn_events)
    return [], events


def _handle_learn_spell(session, command: LearnSpell) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    definition = member.definition
    result = add_spell_to_book(member, definition, load_spells(), command.spell_id)
    return list(result.rejections), list(result.events)


def _handle_cast_spell(session, command: CastSpell) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    try:
        spell = load_spells().get(command.spell_id)
    except ValueError:
        return [Rejection(code="magic.cast.unknown_spell", params={"spell": command.spell_id})], []
    if _location(session).kind == "dungeon" and session.ledger.active_on(_cell_ref(session), "silence"):
        return [Rejection(code="magic.cast.silenced_area", params={"caster": member.id})], []
    registry = session.registry()
    targets: list[Creature | str] = []
    for target_ref in command.targets:
        if target_ref.startswith("cell:"):
            targets.append(target_ref)
        elif target_ref in registry:
            targets.append(registry[target_ref])
        else:
            return [Rejection(code="magic.cast.unknown_target", params={"target": target_ref})], []
    context = _cast_context(session, targets, in_combat=False)
    profile = caster_profile(member.definition)
    if profile is None:
        # A member with no casting profile can never have a memorized copy.
        return [
            Rejection(code="magic.cast.not_memorized", params={"spell": spell.id, "reversed": command.reversed})
        ], []
    cast_rejections = validate_cast(
        member,
        spell,
        command.mode,
        profile=profile,
        reversed=command.reversed,
        targets=targets,
        context=context,
        ledger=session.ledger,
    )
    if cast_rejections:
        return cast_rejections, []
    result = cast_spell(
        member,
        spell,
        command.mode,
        profile=profile,
        reversed=command.reversed,
        targets=targets,
        context=context,
        ledger=session.ledger,
        clock=session.clock,
        allocator=session.allocator,
        registry=registry,
        ruleset=session.ruleset,
        stream=session.streams.get(MAGIC_STREAM),
        effects_stream=session.streams.get(EFFECTS_STREAM),
    )
    events = list(result.events)
    events.extend(_stationary_silence(session, spell, result, targets))
    if spell.id.startswith("remove_curse") and not command.reversed:
        for target in targets:
            if getattr(target, "definition", None) is not None:
                events.extend(_lift_curses(session, target))
    events.extend(session.advance_rounds(1))
    return [], events


def _cast_context(session, targets, *, in_combat: bool, distance_feet: int | None = None) -> CastContext:
    """Build the cast context from session state, with the actual numbers from the death records."""
    from osrlib.core.clock import ROUNDS_PER_DAY

    days_since_death = None
    rounds_since_death = None
    for target in targets:
        target_id = getattr(target, "id", None)
        record = session.death_records.get(target_id) if target_id else None
        if record is not None:
            days_since_death = (session.clock.rounds - record.round) // ROUNDS_PER_DAY
            if record.cause == "poison":
                rounds_since_death = session.clock.rounds - record.round
            break
    return CastContext(
        in_combat=in_combat,
        distance_feet=distance_feet,
        days_since_death=days_since_death,
        rounds_since_death=rounds_since_death,
    )


def _stationary_silence(session, spell, result, targets) -> list[Event]:
    """*Silence 15' radius*'s save-passed outcome: the area anchors to a cell.

    On a passed save RAW leaves a stationary area the creature can step out of. As
    a documented adaptation (see the adaptations register), the effect instead
    attaches to the party's cell (the encounter's abstract location), and
    creatures in that cell cannot cast while it lasts.
    """
    if spell.id != "silence_15_radius" or result.manual or _location(session).kind != "dungeon":
        return []
    passed = {
        event.target_id
        for event in result.events
        if isinstance(event, SavingThrowRolledEvent) and event.code == "combat.save.passed"
    }
    if not any(getattr(target, "id", None) in passed for target in targets):
        return []
    spec = spell.duration_spec
    definition = EffectDefinition(
        kind="silence",
        duration_unit=spec.unit or TimeUnit.TURN,
        duration_amount=(spec.amount or 12),
        dispellable=True,
        params={"radius_feet": 15, "stationary": True},
    )
    _, attach_events = session.ledger.attach(
        definition, _cell_ref(session), clock=session.clock, allocator=session.allocator, registry=session.registry()
    )
    return attach_events


# ---------------------------------------------------------------------- magic item use


def _identify_item_events(session, member, instance: MagicItemInstance) -> list:
    """Identify an item (and reveal its curse) on its first meaningful use.

    Cursed items identify as their true nature at the same trigger and stick to
    their bearer (`items.curse.stuck` on unequip, drop, and sale) until *remove
    curse*. The armour that reads as +1 gives itself away at first battle use,
    which is when the SRD rules reveal it.
    """
    template = magic_item_template(instance)
    events: list[Event] = []
    if not instance.identified:
        instance.identified = True
        events.append(ItemIdentifiedEvent(instance_id=instance.instance_id, template_id=instance.template_id))
    if template.cursed and not instance.cursed_revealed:
        instance.cursed_revealed = True
        events.append(
            CurseRevealedEvent(
                character_id=member.id, instance_id=instance.instance_id, template_id=instance.template_id
            )
        )
    return events


def _release_instance_effects(session, instance: MagicItemInstance) -> list:
    """Release the ledger effects a worn item attached (its state records them)."""
    events: list[Event] = []
    effect_ids = instance.state.get("effect_ids", ())
    for effect_id in effect_ids if isinstance(effect_ids, tuple) else ():
        if any(effect.effect_id == effect_id for effect in session.ledger.effects):
            events.extend(session.ledger.release(str(effect_id), session.registry()))
    if "effect_ids" in instance.state:
        instance.state = {key: value for key, value in instance.state.items() if key != "effect_ids"}
    return events


def _attach_worn_item_effects(session, member, instance: MagicItemInstance) -> list:
    """Attach a worn item's ledger effects: the regeneration ring, the weakness onset.

    Item-sourced ledger effects attach undispellable (RAW's dispel exemption for
    items), and the instance's state records the effect ids so unequipping releases
    them. A cursed ring reveals when its curse takes effect. The onset is
    automatic at wearing, so the reveal is too (RAW: "the ring cannot be removed,
    once worn").
    """
    template = magic_item_template(instance)
    events: list[Event] = []
    if template.effect is None:
        return events
    definition = None
    if template.effect.kind == "regeneration":
        definition = EffectDefinition(
            kind="regeneration",
            tick="regeneration",
            stacking="ignore",
            params={
                str(key): value if not isinstance(value, list) else tuple(value)
                for key, value in template.effect.params.items()
            },
        )
    elif template.effect.kind == "weakness":
        events.extend(_identify_item_events(session, member, instance))
        definition = EffectDefinition(
            kind="ring_of_weakness_onset",
            duration_unit=TimeUnit.ROUND,
            duration_amount=_int_param(template.effect.params, "onset_rounds"),
            expiry="weakness_strength_set",
            params={"strength_set": _int_param(template.effect.params, "strength_set")},
        )
    if definition is None:
        return events
    effect, attach_events = session.ledger.attach(
        definition, member.id, clock=session.clock, allocator=session.allocator, registry=session.registry()
    )
    events.extend(attach_events)
    if effect is not None:
        held = instance.state.get("effect_ids", ())
        held_ids = held if isinstance(held, tuple) else ()
        instance.state = {**instance.state, "effect_ids": (*held_ids, effect.effect_id)}
    return events


def _potion_effect_definition(template, instance) -> EffectDefinition:
    """Build a potion's timed item effect: 1d6+6 turns, undispellable, referee-tracked."""
    effect = template.effect
    params: dict[str, int | str | bool | tuple[int | str, ...]] = {
        str(key): value for key, value in effect.params.items() if key != "effect_kind"
    }
    params["item_source"] = "potion"
    condition = Condition(effect.condition) if effect.condition is not None else None
    return EffectDefinition(
        kind=str(effect.params.get("effect_kind", template.id)),
        duration_unit=TimeUnit.TURN,
        duration_dice="1d6+6",
        condition=condition,
        modifiers=effect.modifiers,
        stacking="stack",
        params=params,
    )


def _potion_drinker(session, carrier, instance: MagicItemInstance, target_id: str | None):
    """Return who drinks a potion the carrier uses, with every reason they can't, as a `(drinker, rejections)` pair.

    With no `target_id`, or the carrier's own id, the carrier drinks. Any other id has to be a party member's,
    or the use gets `items.use.target_not_allowed`. A member who is dead or turned to stone can't drink, and the
    use gets `items.use.target_cannot_drink`. A member who is asleep or paralysed can be given a potion to drink.
    Both rejections have the potion's instance id as `item` and the id as `target`. The drinker is None when
    there are rejections. A battle adds a check of reach (see `battle._drinker`). The call reads state and takes
    no draw.
    """
    if target_id is None or target_id == carrier.id:
        return carrier, []
    drinker = next((member for member in session.party.members if member.id == target_id), None)
    if drinker is None:
        return None, [
            Rejection(code="items.use.target_not_allowed", params={"item": instance.instance_id, "target": target_id})
        ]
    if not _stands(drinker):
        return None, [
            Rejection(code="items.use.target_cannot_drink", params={"item": instance.instance_id, "target": target_id})
        ]
    return drinker, []


def _use_potion(session, carrier, drinker, instance: MagicItemInstance, template) -> list[Event]:
    """Use up a potion the carrier carries, and apply its effect to the drinker, who can be the carrier.

    The potion leaves the carrier's pack, and the carrier's use identifies it. The drinker gets the potion's
    effect, its mixing with another potion still in effect on them, and its weekly limit. Its events report
    the carrier in `character_id`, and the drinker in `target_id` when the drinker is another member.
    """
    from osrlib.core.combat import apply_healing

    stream = session.streams.get(MAGIC_STREAM)
    events: list[Event] = []
    effect_spec = template.effect
    instantaneous = effect_spec is not None and bool(effect_spec.params.get("instantaneous"))
    fed_to = drinker.id if drinker is not carrier else None
    active_potions = [
        effect
        for effect in session.ledger.effects
        if effect.target_ref == drinker.id and effect.definition.params.get("item_source") == "potion"
    ]
    _remove_instance(carrier, instance)
    events.extend(_identify_item_events(session, carrier, instance))
    if active_potions and not instantaneous and effect_spec is not None:
        # Mixing applies both printed consequences: both effects cancel and the
        # drinker is disabled for 3 turns. Neither applies to an instantaneous or
        # permanent potion.
        events.append(
            ItemUsedEvent(
                code="items.potion.mixed",
                character_id=carrier.id,
                instance_id=instance.instance_id,
                target_id=fed_to,
            )
        )
        for effect in active_potions:
            events.extend(session.ledger.release(effect.effect_id, session.registry()))
        sickness = EffectDefinition(
            kind="potion_sickness",
            duration_unit=TimeUnit.TURN,
            duration_amount=3,
            condition=Condition.PARALYSED,
            params={"item_source": "potion_sickness"},
        )
        _, attach_events = session.ledger.attach(
            sickness, drinker.id, clock=session.clock, allocator=session.allocator, registry=session.registry()
        )
        events.extend(attach_events)
        return events
    events.append(
        ItemUsedEvent(
            code="items.potion.drunk",
            character_id=carrier.id,
            instance_id=instance.instance_id,
            manual=template.manual if effect_spec is None else (),
            target_id=fed_to,
        )
    )
    if effect_spec is None:
        return events
    if effect_spec.kind == "healing":
        paralysing = [
            effect
            for effect in session.ledger.active_on(drinker.id)
            if effect.definition.condition is Condition.PARALYSED
        ]
        if paralysing:
            # The potion's second usage: paralysing effects are negated instead.
            for effect in paralysing:
                events.extend(session.ledger.release(effect.effect_id, session.registry()))
        else:
            amount = roll(str(effect_spec.heal_dice), stream).total
            events.extend(apply_healing(drinker, amount, source="magical"))
        return events
    if effect_spec.kind == "save_or_die":
        save = saving_throw(drinker, SaveCategory(str(effect_spec.save_category)), magical=True, stream=stream)
        events.extend(save.events)
        if not save.passed:
            from osrlib.core.effects import kill

            events.extend(kill(drinker))
        return events
    definition = _potion_effect_definition(template, instance)
    if effect_spec.params.get("weekly_inversion"):
        # More than one in a week inverts the benefits. The week runs per character
        # on the clock's day, tracked as a ledger marker so it serializes.
        cooldown_active = bool(session.ledger.active_on(drinker.id, "invulnerability_cooldown"))
        if cooldown_active:
            inverted = tuple(spec.model_copy(update={"value": -spec.value}) for spec in definition.modifiers)
            definition = definition.model_copy(update={"modifiers": inverted})
        marker = EffectDefinition(
            kind="invulnerability_cooldown", duration_unit=TimeUnit.DAY, duration_amount=7, stacking="refresh"
        )
        _, marker_events = session.ledger.attach(
            marker, drinker.id, clock=session.clock, allocator=session.allocator, registry=session.registry()
        )
        events.extend(marker_events)
    _, attach_events = session.ledger.attach(
        definition,
        drinker.id,
        clock=session.clock,
        allocator=session.allocator,
        registry=session.registry(),
        stream=session.streams.get(EFFECTS_STREAM),
    )
    events.extend(attach_events)
    return events


def _thief_scroll_use(definition) -> dict | None:
    for ability in definition.abilities:
        if ability.tag == "scroll_use":
            return ability.params
    return None


def _use_scroll(session, member, instance: MagicItemInstance, template, command) -> tuple[list[Rejection], list]:
    light_rejections = _requires_light(session, member, infravision_suffices=False)
    if light_rejections:
        return light_rejections, []
    stream = session.streams.get(MAGIC_STREAM)
    events: list[Event] = []
    if template.cursed:
        # Looking at a cursed scroll curses the reader, whatever their class.
        _remove_instance(member, instance)
        events.extend(_identify_item_events(session, member, instance))
        curse = template.curses[session.streams.get(EFFECTS_STREAM).randbelow(len(template.curses))]
        events.append(
            ItemUsedEvent(
                code="items.scroll.cursed",
                character_id=member.id,
                instance_id=instance.instance_id,
                manual=(f"{curse.name}: {curse.prose}",) if not curse.wired else (),
            )
        )
        if curse.id == "energy_drain":
            from osrlib.core.character import ADVANCEMENT_STREAM
            from osrlib.core.classes import drain_levels

            result = drain_levels(
                member,
                member.definition,
                levels=1,
                xp_policy="halfway",
                stream=session.streams.get(ADVANCEMENT_STREAM),
            )
            events.extend(result.events)
        elif curse.id == "slow_healing":
            # Not a disease: the curse halves healing spells where *cause
            # disease* voids them, so DISEASED (the magical-healing block)
            # would overshoot RAW. Natural healing doubles via the rest-day
            # cadence, magical halves via the modifier.
            slow = EffectDefinition(
                kind="cursed_slow_healing",
                modifiers=(ModifierSpec(kind="magical_healing_half", value=1, from_item=True),),
                params={"healing_rest_days": 2, "item_source": "cursed_scroll"},
            )
            _, attach_events = session.ledger.attach(
                slow, member.id, clock=session.clock, allocator=session.allocator, registry=session.registry()
            )
            events.extend(attach_events)
        return [], events
    if template.effect is not None and template.effect.kind == "ward":
        _remove_instance(member, instance)
        events.extend(_identify_item_events(session, member, instance))
        events.append(ItemUsedEvent(code="items.scroll.read", character_id=member.id, instance_id=instance.instance_id))
        params: dict[str, int | str | bool | tuple[int | str, ...]] = {"party_wide": True}
        for key in ("targets", "bars_categories", "bars_template_ids"):
            if key in template.effect.params:
                params[key] = template.effect.params[key]
        for band in template.effect.params.get("bands", ()):
            band_label, _, dice = str(band).partition(":")
            params[f"affected_{band_label.replace('-', '_').replace('+', '_plus')}"] = roll(dice, stream).total
        if template.effect.params.get("all_affected"):
            params["all_affected"] = True
        ward = EffectDefinition(
            kind="protection_ward",
            duration_unit=TimeUnit(str(template.effect.duration_unit)),
            duration_amount=template.effect.duration_amount,
            params=params,
        )
        _, attach_events = session.ledger.attach(
            ward, member.id, clock=session.clock, allocator=session.allocator, registry=session.registry()
        )
        events.extend(attach_events)
        return [], events
    if "spell_count" in template.params:
        inscribed = instance.state.get("spells", ())
        remaining = tuple(str(spell) for spell in inscribed) if isinstance(inscribed, tuple) else ()
        if not remaining:
            return [Rejection(code="items.scroll.spent", params={"item": instance.instance_id})], []
        spell_id = command.spell_id or remaining[0]
        if spell_id not in remaining:
            return [Rejection(code="items.scroll.no_such_spell", params={"spell": spell_id})], []
        spell = load_spells().get(spell_id)
        definition = member.definition
        profile = caster_profile(definition)
        thief_params = _thief_scroll_use(definition)
        divine_scroll = instance.state.get("spell_list") == "cleric"
        thief_reading = False
        if divine_scroll:
            if profile is None or profile.kind != "divine":
                return [Rejection(code="items.scroll.wrong_caster", params={"item": instance.instance_id})], []
        elif profile is None or profile.kind != "arcane":
            if thief_params is not None and member.level >= int(thief_params.get("min_level", 10)):
                thief_reading = True
            else:
                return [Rejection(code="items.scroll.wrong_caster", params={"item": instance.instance_id})], []
        mode = command.mode or spell.modes[0].key
        registry = session.registry()
        targets: list[Creature | str] = []
        target_refs = command.targets or ((command.target_id,) if command.target_id else ())
        for target_ref in target_refs:
            if target_ref.startswith("cell:"):
                targets.append(target_ref)
            elif target_ref in registry:
                targets.append(registry[target_ref])
            else:
                return [Rejection(code="magic.cast.unknown_target", params={"target": target_ref})], []
        context = _cast_context(session, targets, in_combat=False)
        # The kernel's pre-check, asked about a caster at the scroll's level, which is
        # the level `cast_from_scroll` resolves at. This handler refuses here what the kernel
        # would refuse there, before the scroll is spent.
        cast_rejections = validate_scroll_cast(
            member,
            spell,
            mode,
            targets=targets,
            context=context,
            ledger=session.ledger,
        )
        if cast_rejections:
            return cast_rejections, []
        left = tuple(spell for spell in remaining if spell != spell_id) + tuple(
            spell_id for _ in range(remaining.count(spell_id) - 1)
        )
        if left:
            instance.state = {**instance.state, "spells": left}
        else:
            _remove_instance(member, instance)
        events.extend(_identify_item_events(session, member, instance))
        events.append(ItemUsedEvent(code="items.scroll.read", character_id=member.id, instance_id=instance.instance_id))
        if thief_reading and thief_params is not None:
            error_pct = _int_param(thief_params, "error_pct", 10)
            if stream.randbelow(100) + 1 <= error_pct:
                # The 10% error resolves as a fizzle that consumes the spell.
                # RAW leaves the "unusual or deleterious effect" to the referee,
                # so osrlib picks the fizzle and records it in the adaptations
                # register.
                return [], events
        result = cast_from_scroll(
            member,
            spell,
            mode,
            targets=targets,
            context=context,
            ledger=session.ledger,
            clock=session.clock,
            allocator=session.allocator,
            registry=registry,
            ruleset=session.ruleset,
            stream=stream,
            effects_stream=session.streams.get(EFFECTS_STREAM),
        )
        events.extend(result.events)
        events.extend(_stationary_silence(session, spell, result, targets))
        return [], events
    # The engine resolves no other scroll, like protection from magic or a treasure
    # map. Reading one is allowed, and the event includes its `manual` text for the
    # game to narrate.
    _remove_instance(member, instance)
    events.extend(_identify_item_events(session, member, instance))
    events.append(
        ItemUsedEvent(
            code="items.scroll.read",
            character_id=member.id,
            instance_id=instance.instance_id,
            manual=template.manual,
        )
    )
    return [], events


def _device_area_events(session, member, instance: MagicItemInstance, template, group) -> list:
    """Resolve a device's area effect (cone or sphere) against one encounter group.

    Cones and spheres resolve through the battle footprint rule, and each caught
    target saves per the item's spec. Device damage presents `magic` and is
    destructive: the SRD's equipment-destruction examples are all energy-type
    deaths.
    """
    from osrlib.core.combat import DamageSource, deal_damage
    from osrlib.crawl import battle as battle_module

    effect_spec = template.effect
    stream = session.streams.get(MAGIC_STREAM)
    events: list[Event] = []
    candidates = battle_module._area_candidates(
        session, group, effect_spec.shape, dict(effect_spec.dimensions), caster_id=member.id
    )
    for target in candidates:
        save = saving_throw(
            target,
            SaveCategory(str(effect_spec.save_category)),
            magical=True,
            element=effect_spec.element,
            stream=stream,
        )
        events.extend(save.events)
        if effect_spec.kind == "damage_area":
            source = DamageSource(
                keys=("magic",), element=effect_spec.element, magical=True, kind="device", destructive=True
            )
            result = roll(str(effect_spec.damage_dice), stream)
            amount = result.total
            if save.passed:
                amount //= 2
            if amount < 1:
                continue
            events.extend(
                deal_damage(
                    target,
                    amount,
                    source=source,
                    attacker_id=member.id,
                    rolls=result.rolls,
                    clock=session.clock,
                    ruleset=session.ruleset,
                    stream=stream,
                )
            )
        else:
            if save.passed:
                continue
            condition = Condition(str(effect_spec.condition))
            definition = EffectDefinition(
                kind=str(effect_spec.params.get("effect_kind", template.id)),
                duration_unit=TimeUnit(str(effect_spec.duration_unit)),
                duration_amount=effect_spec.duration_amount,
                condition=condition,
                params={"item_source": "device"},
            )
            _, attach_events = session.ledger.attach(
                definition,
                str(getattr(target, "id", target)),
                clock=session.clock,
                allocator=session.allocator,
                registry=session.registry(),
            )
            events.extend(attach_events)
    return events


def _spend_device_charge(instance: MagicItemInstance, template) -> None:
    """Spend one charge, leaving the item inert at zero and emitting no event for it (RAW)."""
    if template.charges_dice is not None and instance.charges_remaining is not None:
        instance.charges_remaining -= 1


def _use_device(session, member, instance: MagicItemInstance, template, command) -> tuple[list[Rejection], list]:
    from osrlib.core.combat import apply_healing

    definition = member.definition
    if not usable_by_class(template, definition):
        return [Rejection(code="items.use.not_usable", params={"item": instance.instance_id})], []
    if template.charges_dice is not None and (instance.charges_remaining or 0) <= 0:
        return [Rejection(code="items.device.inert", params={"item": instance.instance_id})], []
    effect_spec = template.effect
    group = None
    if effect_spec is not None and effect_spec.kind in ("damage_area", "condition_area"):
        if session.encounter is not None:
            if command.target_id is None:
                return [Rejection(code="items.use.target_required", params={"item": instance.instance_id})], []
            group = next((entry for entry in session.encounter.groups if entry.id == command.target_id), None)
            if group is None or group.fled:
                return [Rejection(code="items.use.unknown_target", params={"target": command.target_id or ""})], []
    if effect_spec is not None and effect_spec.kind == "striking":
        return [Rejection(code="items.use.battle_only", params={"item": instance.instance_id})], []
    target: Any = None
    if effect_spec is not None and effect_spec.kind == "healing":
        # Resolve the touch target before anything mutates: a rejected command
        # mutates nothing, and identification below is a mutation.
        target = member if command.target_id is None else session.registry().get(command.target_id)
        if target is None:
            return [Rejection(code="items.use.unknown_target", params={"target": command.target_id or ""})], []
    events: list[Event] = []
    events.extend(_identify_item_events(session, member, instance))
    events.append(
        ItemUsedEvent(
            code="items.device.activated",
            character_id=member.id,
            instance_id=instance.instance_id,
            manual=template.manual if effect_spec is None else (),
        )
    )
    if effect_spec is not None and effect_spec.kind == "healing":
        day_key = f"healed:{getattr(target, 'id', command.target_id)}"
        today = session.clock.days
        if effect_spec.params.get("once_per_target_per_day") and instance.state.get(day_key) == today:
            # RAW: effective on any individual at most once per day. The
            # activation still happens, and nothing more heals.
            pass
        else:
            instance.state = {**instance.state, day_key: today}
            amount = roll(str(effect_spec.heal_dice), session.streams.get(MAGIC_STREAM)).total
            events.extend(apply_healing(target, amount, source="magical"))
    elif group is not None:
        events.extend(_device_area_events(session, member, instance, template, group))
    _spend_device_charge(instance, template)
    return [], events


def _toggle_sword_light(session, member, instance: MagicItemInstance, template) -> tuple[list[Rejection], list]:
    """The Sword +1, Light command toggle, through the light-effect machinery."""
    events: list[Event] = []
    events.extend(_identify_item_events(session, member, instance))
    active = [
        effect
        for effect in session.ledger.active_on(member.id, "light")
        if effect.definition.params.get("source") == "sword"
    ]
    if active:
        for effect in active:
            events.extend(session.ledger.release(effect.effect_id, session.registry()))
        events.append(LightEvent(code="exploration.light.extinguished", character_id=member.id, source="sword"))
        return [], events
    definition = EffectDefinition(
        kind="light",
        params={
            "source": "sword",
            "light_radius_feet": int(template.effect.params.get("light_radius_feet", 30)),
            "brightness": "flame",
        },
    )
    _, attach_events = session.ledger.attach(
        definition, member.id, clock=session.clock, allocator=session.allocator, registry=session.registry()
    )
    events.extend(attach_events)
    events.append(LightEvent(code="exploration.light.lit", character_id=member.id, source="sword"))
    return [], events


def _handle_use_item(session, command: UseItem) -> tuple[list[Rejection], list[Event]]:
    member, rejections = _member_able(session, command.character_id)
    if rejections:
        return rejections, []
    instance = member.inventory.magic_item(command.item_id)
    if instance is None:
        return [Rejection(code="exploration.item.not_carried", params={"item": command.item_id})], []
    template = magic_item_template(instance)
    if template.category is MagicItemCategory.POTION:
        drinker, rejections = _potion_drinker(session, member, instance, command.target_id)
        events = [] if drinker is None else _use_potion(session, member, drinker, instance, template)
    elif template.category is MagicItemCategory.SCROLL:
        rejections, events = _use_scroll(session, member, instance, template, command)
    elif template.category in (MagicItemCategory.ROD, MagicItemCategory.STAFF, MagicItemCategory.WAND):
        rejections, events = _use_device(session, member, instance, template, command)
    elif template.effect is not None and template.effect.kind == "light":
        rejections, events = _toggle_sword_light(session, member, instance, template)
    else:
        return [Rejection(code="items.use.not_usable", params={"item": command.item_id})], []
    if rejections:
        return rejections, []
    if session.mode is SessionMode.ENCOUNTER:
        from osrlib.crawl import encounter as encounter_module

        events.extend(encounter_module._pass_round(session))
    else:
        events.extend(session.advance_rounds(1))
    return [], events


# ---------------------------------------------------------------------- town services

HEALING_SERVICES: Mapping[HealingService, tuple[str, int]] = MappingProxyType(
    {
        "cure_light_wounds": ("cure_light_wounds", 25),
        "cure_serious_wounds": ("cure_serious_wounds", 100),
        "cure_disease": ("cure_disease", 150),
        "neutralize_poison": ("neutralize_poison", 150),
        "remove_curse": ("remove_curse_c", 200),
        "raise_dead": ("raise_dead", 1500),
    }
)
"""The temple's healing services: each service name mapped to its spell id and its standard price in gold pieces.

The keys are the [`HealingService`][osrlib.crawl.commands.HealingService] values that
[`PurchaseHealing`][osrlib.crawl.commands.PurchaseHealing] accepts in its `service` field. The
spell id is the spell the temple casts, which is why *remove curse* maps to `remove_curse_c`, the
cleric's version of that spell rather than the magic-user's.

A town charges the standard price for a service unless the adventure sets a different price for
that service in [`TownSpec.healing_prices`][osrlib.crawl.adventure.TownSpec.healing_prices]. To show a
town's price list, or to check the party's gold against a price before you send the command, read
[`PlayerView.town_healing_prices`][osrlib.crawl.views.PlayerView.town_healing_prices], which gives
what the party's town charges for every service.

The SRD's town rules don't list temple services, so osrlib chose these names and prices and records
the choice in [the adaptations register](https://mmacy.github.io/osrlib-python/adaptations/). Every
service is always available. Whether a town is big enough to have a temple, or a cleric is in
today, is up to you.

The mapping is read-only, so assigning or deleting an entry raises `TypeError`.

```python
from osrlib.crawl.exploration import HEALING_SERVICES

print(HEALING_SERVICES["remove_curse"])
# ('remove_curse_c', 200)
```
"""


def _lift_curses(session, member) -> list[Event]:
    """*Remove curse* lifts the bearer's item curses: stuck items become discardable.

    The item stays cursed and identified, the stuck marker clears, and item
    curse effects (the Ring of Weakness's STR set, the cursed scroll's slow
    healing) release.
    """
    events: list[Event] = []
    for instance in member.inventory.all_instances():
        if isinstance(instance, MagicItemInstance) and instance.cursed_revealed:
            instance.cursed_revealed = False
    for effect in list(session.ledger.active_on(member.id)):
        if effect.definition.kind in ("ring_of_weakness", "ring_of_weakness_onset", "cursed_slow_healing"):
            events.extend(session.ledger.release(effect.effect_id, session.registry()))
    return events


def _handle_sell_treasure(session, command: SellTreasure) -> tuple[list[Rejection], list[Event]]:
    sales: list[tuple[Any, Any]] = []  # (owner, valuable)
    for item_id in command.item_ids:
        found = None
        for member in session.party.members:
            if member.inventory.magic_item(item_id) is not None:
                return [Rejection(code="town.sell.no_fixed_value", params={"item": item_id})], []
            valuable = next(
                (candidate for candidate in member.inventory.valuables if candidate.instance_id == item_id), None
            )
            if valuable is not None:
                found = (member, valuable)
                break
        # A valuable's instance id identifies one gem or piece of jewellery, so an id listed
        # twice has nothing left to sell the second time.
        if found is None or any(sold is found[1] for _, sold in sales):
            return [Rejection(code="exploration.item.not_carried", params={"item": item_id})], []
        sales.append(found)
    events: list[Event] = []
    by_seller: dict[str, tuple[Any, list]] = {}
    for member, valuable in sales:
        by_seller.setdefault(member.id, (member, []))[1].append(valuable)
    for member, valuables in by_seller.values():
        total = 0
        for valuable in valuables:
            member.inventory.valuables.remove(valuable)
            total += valuable.value_gp
        member.inventory.purse.gp += total
        events.append(
            TreasureSoldEvent(
                character_id=member.id,
                instance_ids=tuple(valuable.instance_id for valuable in valuables),
                gp_value=total,
            )
        )
    return [], events


def _healing_payers(session, patient: Character, cost_gp: int) -> list[tuple[Character, int]] | None:
    """Plan which purses cover a temple fee, in the order the temple charges them.

    The treated member pays first, then the rest of the party in marching order, dead
    members included. A purse pays in whole gold pieces, because
    [`CoinPurse.spend`][osrlib.core.items.CoinPurse.spend] prices in gold. It pays as much of
    what is still owed as its total value covers, rounded down to a whole gold piece, and
    keeps any value below a gold piece, so the last purse charged pays the outstanding
    remainder and the purses after it pay nothing.

    Planning and charging are separate so the funds check stays a pure validation step: a
    party whose whole gold pieces fall short is refused before any purse is opened.

    Args:
        session: The session whose party pays.
        patient: The member being treated, charged first whether alive or dead.
        cost_gp: The fee in whole gold pieces.

    Returns:
        The (member, gold pieces) pairs to charge in order, or None when the whole gold
        pieces in the party's purses together fall short of the fee.
    """
    order = [patient] + [member for member in session.party.members if member.id != patient.id]
    plan: list[tuple[Character, int]] = []
    outstanding = cost_gp
    for member in order:
        if outstanding <= 0:
            break
        share = min(outstanding, member.inventory.purse.value_cp // 100)
        if share <= 0:
            continue
        plan.append((member, share))
        outstanding -= share
    return None if outstanding > 0 else plan


def _handle_purchase_healing(session, command: PurchaseHealing) -> tuple[list[Rejection], list[Event]]:
    try:
        member = session.member(command.character_id)
    except ValueError:
        return [Rejection(code="session.command.unknown_member", params={"character": command.character_id})], []
    spell_id, standard_gp = HEALING_SERVICES[command.service]
    spell = load_spells().get(spell_id)
    lowest = minimum_caster_level(spell)
    highest = load_classes().get("cleric").max_level
    caster_level = lowest if command.caster_level is None else command.caster_level
    if not lowest <= caster_level <= highest:
        return [
            Rejection(
                code="town.healing.caster_level_out_of_range",
                params={"item": command.service, "caster_level": caster_level, "minimum": lowest, "maximum": highest},
            )
        ], []
    cost_gp = session.adventure.town.healing_prices.get(command.service, standard_gp)
    payers = _healing_payers(session, member, cost_gp)
    if payers is None:
        return [
            Rejection(code="items.purchase.insufficient_funds", params={"item": command.service, "cost_gp": cost_gp})
        ], []
    context = _cast_context(session, [member], in_combat=False)
    if not _healing_would_work(command.service, member, spell, caster_level, context):
        return [Rejection(code="town.healing.no_effect", params={"item": command.service, "character": member.id})], []
    temple_cleric = _temple_cleric(spell, caster_level)
    temple_profile = caster_profile(temple_cleric.definition)
    if temple_profile is None:  # unreachable: the temple cleric is a cleric
        raise ValueError("the temple cleric has no casting profile")
    cast_rejections = validate_cast(
        temple_cleric,
        spell,
        spell.modes[0].key,
        profile=temple_profile,
        targets=[member],
        context=context,
        ledger=session.ledger,
    )
    if cast_rejections:
        return cast_rejections, []
    for payer, share in payers:
        payer.inventory.purse.spend(share)
    events: list[Event] = [
        HealingPurchasedEvent(
            character_id=member.id,
            service=command.service,
            cost_gp=cost_gp,
            payers=tuple(payer.id or payer.name for payer, _ in payers),
            payments_gp=tuple(share for _, share in payers),
        )
    ]
    result = cast_spell(
        temple_cleric,
        spell,
        spell.modes[0].key,
        profile=temple_profile,
        targets=[member],
        context=context,
        ledger=session.ledger,
        clock=session.clock,
        allocator=session.allocator,
        registry=session.registry(),
        ruleset=session.ruleset,
        stream=session.streams.get(MAGIC_STREAM),
        effects_stream=session.streams.get(EFFECTS_STREAM),
    )
    events.extend(result.events)
    if command.service == "remove_curse":
        events.extend(_lift_curses(session, member))
    return [], events


def _healing_would_work(
    service: str, member: Character, spell: SpellTemplate, caster_level: int, context: CastContext
) -> bool:
    """Return whether a temple service cast at `caster_level` would do anything for the member.

    *Raise dead* needs a dead member, dead no longer than the spell reaches at the cleric's level.
    *Cure disease* needs a diseased member. *Neutralize poison* needs a poisoned member, or one
    that poison killed within the spell's revival window. The two revivals are judged by the same
    functions the spells resolve with, so the temple and a cast agree about who comes back.
    `_cast_context` works out the time since death from the session's death records. The other
    services always count as working. The check only reads state, so a refusal makes no random
    roll and changes nothing.
    """
    effect = spell.modes[0].effect
    if service == "raise_dead":
        return effect is not None and _restores_life(effect, caster_level, member, context)
    if service == "cure_disease":
        return has_condition(member, Condition.DISEASED)
    if service == "neutralize_poison":
        return has_condition(member, Condition.POISONED) or (
            effect is not None and _revives_poison_dead(effect, member, context)
        )
    return True


def _temple_cleric(spell: SpellTemplate, level: int) -> Character:
    """An abstract temple cleric of the given level, with the service's spell memorized.

    Built fresh per purchase, with no draws and no registry entry. The rolls come
    from the magic and effects streams the spell already uses.
    """
    from osrlib.core.abilities import AbilityScore
    from osrlib.core.alignment import Alignment
    from osrlib.core.spells import MemorizedSpell

    return Character(
        id="town-cleric",
        name="Temple cleric",
        class_id="cleric",
        race="human",
        level=level,
        xp=0,
        scores={ability: 9 for ability in AbilityScore},
        alignment=Alignment.LAWFUL,
        max_hp=1,
        current_hp=1,
        memorized_spells=(MemorizedSpell(spell_id=spell.id),),
    )


_HANDLERS = {
    MoveParty: _handle_move_party,
    TurnParty: _handle_turn_party,
    ReorderParty: _handle_reorder_party,
    OpenDoor: _handle_open_door,
    CloseDoor: _handle_close_door,
    ForceDoor: _handle_force_door,
    WedgeDoor: _handle_wedge_door,
    ListenAtDoor: _handle_listen_at_door,
    PickLock: _handle_pick_lock,
    Search: _handle_search,
    InspectTreasure: _handle_inspect_treasure,
    RemoveTreasureTrap: _handle_remove_treasure_trap,
    TakeTreasure: _handle_take_treasure,
    DropItems: _handle_drop_items,
    GiveItems: _handle_give_items,
    LightSource: _handle_light_source,
    ExtinguishSource: _handle_extinguish_source,
    EquipItem: _handle_equip_item,
    UnequipItem: _handle_unequip_item,
    Rest: _handle_rest,
    PrepareSpells: _handle_prepare_spells,
    LearnSpell: _handle_learn_spell,
    CastSpell: _handle_cast_spell,
    UseItem: _handle_use_item,
    UseStairs: _handle_use_stairs,
    EnterDungeon: _handle_enter_dungeon,
    TravelToTown: _handle_travel_to_town,
    PurchaseEquipment: _handle_purchase_equipment,
    SellTreasure: _handle_sell_treasure,
    PurchaseHealing: _handle_purchase_healing,
}
"""The command classes this module handles, each mapped to the function that handles it.

[`GameSession`][osrlib.crawl.session.GameSession] merges this mapping into its private handler
table the first time it dispatches a command, alongside the encounter, battle, and referee mappings.
There is no registration point here: the table is private to this module, and the only documented
way to run a command is [`GameSession.execute`][osrlib.crawl.session.GameSession.execute], which
picks the handler, runs the mode check, and does the logging and bookkeeping a handler alone would
skip.
"""
