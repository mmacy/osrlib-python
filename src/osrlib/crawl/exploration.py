"""The rules behind the exploring and town commands, and the per-turn checks for fatigue, food, and wandering monsters.

A party gives these commands while it explores a dungeon, in `exploring` mode, or visits town, in
`town` mode: moving, doors, searching, traps, treasure, items, light, rest, spells, shopping, and
temple healing.
[`DropItems`][osrlib.crawl.commands.DropItems] and [`UseItem`][osrlib.crawl.commands.UseItem] also
work in `encounter` mode, so the party can drop or use gear while monsters stand in front of it.

The command handlers are private, so you don't call one yourself. Build one of the command models in
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

from osrlib.core.clock import ROUNDS_PER_TURN
from osrlib.core.combat import DamageSource, deal_damage, natural_healing
from osrlib.core.dice import roll
from osrlib.core.effects import EFFECTS_STREAM, Condition, EffectDefinition, ModifierSpec
from osrlib.core.events import Event
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
from osrlib.crawl._hauling import _handle_take_treasure
from osrlib.crawl._items import _handle_drop_items, _handle_equip_item, _handle_give_items, _handle_unequip_item
from osrlib.crawl._light import _handle_extinguish_source, _handle_light_source
from osrlib.crawl._magic_items import _handle_use_item
from osrlib.crawl._movement import (
    _handle_enter_dungeon,
    _handle_move_party,
    _handle_reorder_party,
    _handle_travel_to_town,
    _handle_turn_party,
    _handle_use_stairs,
)
from osrlib.crawl._odometer import _spend_turn
from osrlib.crawl._party_location import _level, _location
from osrlib.crawl._party_members import _consume_item, _find_item
from osrlib.crawl._search import _handle_inspect_treasure, _handle_remove_treasure_trap, _handle_search
from osrlib.crawl._spells import _handle_cast_spell, _handle_learn_spell, _handle_prepare_spells
from osrlib.crawl._town import _handle_purchase_equipment, _handle_purchase_healing, _handle_sell_treasure
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
    TakeTreasure,
    TravelToTown,
    TurnParty,
    UnequipItem,
    UseItem,
    UseStairs,
    WedgeDoor,
)
from osrlib.crawl.events import FatigueEvent, ProvisionsEvent, RestedEvent, WanderingCheckEvent
from osrlib.data import load_encounter_tables

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


# ---------------------------------------------------------------------- rest


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

`_handle_rest` is in this module, because a rest clears the fatigue that `check_fatigue` attaches. The other handlers
are in the private modules beside this one, one module for each kind of command, like `_movement` for `MoveParty`
and `_doors` for `OpenDoor`.

[`GameSession`][osrlib.crawl.session.GameSession] merges this mapping into its private handler
table the first time it dispatches a command, alongside the encounter, battle, and referee mappings.
There is no registration point here: the table is private to this module, and the only documented
way to run a command is [`GameSession.execute`][osrlib.crawl.session.GameSession.execute], which
picks the handler, runs the mode check, and does the logging and bookkeeping a handler alone would
skip.
"""
