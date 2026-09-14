"""Decide whether and how a fight starts: surprise, distance, reaction, parley, evasion, pursuit.

An encounter is the state a session is in once the party and a monster group are aware of each
other and before anyone has swung. This module opens one, runs it round by round, and closes it. It
takes a [`GameSession`][osrlib.crawl.session.GameSession] whose party stands on a dungeon cell and
monster instances already in the session registry, and it hands the fighting itself to
[`osrlib.crawl.battle`][osrlib.crawl.battle].

[`start_encounter`][osrlib.crawl.encounter.start_encounter] is the entry point. After it returns,
the session is in `encounter` mode and each of [`Parley`][osrlib.crawl.commands.Parley],
[`Evade`][osrlib.crawl.commands.Evade], [`Wait`][osrlib.crawl.commands.Wait],
[`TurnUndead`][osrlib.crawl.commands.TurnUndead], and
[`EngageBattle`][osrlib.crawl.commands.EngageBattle] runs one encounter round through
[`HANDLERS`][osrlib.crawl.encounter.HANDLERS], with the monsters acting per their stance after it.
[`end_encounter`][osrlib.crawl.encounter.end_encounter] closes the encounter and puts the session
back in `exploring`.

You rarely call `start_encounter` yourself. The session's
[`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters] and
[`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty] handlers, the wandering-monster check, and
entry into a keyed area all call it for you. Call it directly when your own content decides that a
group has just come into view.

The results reach a front end as events from [`osrlib.crawl.events`][osrlib.crawl.events]:
a [`SurpriseRolledEvent`][osrlib.crawl.events.SurpriseRolledEvent] per side,
[`EncounterStartedEvent`][osrlib.crawl.events.EncounterStartedEvent] reporting the count and the
distance, [`StanceChangedEvent`][osrlib.crawl.events.StanceChangedEvent] whenever the reaction
moves, [`EvasionEvent`][osrlib.crawl.events.EvasionEvent] and
[`PursuitEvent`][osrlib.crawl.events.PursuitEvent] while the party runs,
[`ExhaustionEvent`][osrlib.crawl.events.ExhaustionEvent] when a chase runs its course, a
[`MonsterDefeatedEvent`][osrlib.crawl.events.MonsterDefeatedEvent] per monster at the close, and
[`EncounterEndedEvent`][osrlib.crawl.events.EncounterEndedEvent] with the outcome. The reaction
roll itself posts the kernel's
[`ReactionRolledEvent`][osrlib.core.events.ReactionRolledEvent] at referee visibility.

The stance is the monsters' current disposition, and the stance map resolves bands the OSE SRD
leaves to a human referee. A reaction of 2 or less attacks now. A 3 to 5 is hostile: the monsters
attack at the end of the next encounter round unless the party has begun evading or has improved
the stance by parley. A 6 to 8 is uncertain, so the monsters hold and posture, and the reaction
re-rolls next round with no modifier. A 9 to 11 is indifferent, and the party may pass, parley, or
withdraw freely. A 12 or more is friendly. Only the attacking and hostile stances pursue an evading
party, as a documented adaptation (see the adaptations register): RAW leaves pursuit itself to the
referee, and osrlib keys it to low reactions.

The distance roll is bounded by the space it happens in. RAW rolls 2d6 × 10' only "if there is
uncertainty", and the walls around the party's cell resolve that uncertainty, so the rolled distance
caps at the longest straight sight line the cell affords: the room's own span in a room, the whole
passage down a corridor, never shortened by darkness. A caller that supplies `distance_feet` is
never capped, because the referee places what the referee spawns.

Typical usage:

```python
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import EnterDungeon, Parley, SessionMode, SpawnMonsters
from osrlib.crawl.dungeon import DungeonSpec, Edge, EdgeKind, LevelSpec
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession

rules = Ruleset()
draw = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
hild = create_character(
    name="Hild",
    class_id="fighter",
    alignment=Alignment.LAWFUL,
    ruleset=rules,
    stream=draw,
).character
level = LevelSpec(
    number=1,
    width=2,
    height=1,
    entrance=(0, 0),
    edges={"1,0:west": Edge(kind=EdgeKind.OPEN)},
)
crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
town = TownSpec(name="Threshold", travel_turns={"crypt": 1})
adventure = Adventure(name="A First Delve", town=town, dungeons=(crypt,))
session = GameSession.new(Party(members=[hild]), adventure, seed=7)
session.execute(EnterDungeon(dungeon_id="crypt"))

# Spawning a group opens the encounter: surprise, distance, and reaction all resolve here.
session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=30))
assert session.mode is SessionMode.ENCOUNTER
assert session.encounter.stance == "indifferent"  # seed 7 rolls a reaction of 10 on 2d6
assert session.encounter.groups[0].distance_feet == 30

# One encounter command is one round beat: this one rerolls the reaction with Hild's CHA modifier.
result = session.execute(Parley(character_id=hild.id))
assert result.accepted
assert session.encounter.round == 1
```
"""

from pydantic import BaseModel, ConfigDict, Field

from osrlib.core.combat import cannot_move, roll_reaction
from osrlib.core.effects import Condition, has_condition
from osrlib.core.events import Event
from osrlib.core.items import Coins
from osrlib.core.spells import MAGIC_STREAM, turn_undead, validate_turn_undead
from osrlib.core.tables import ReactionResult
from osrlib.core.validation import Rejection
from osrlib.crawl.commands import DropItems, EngageBattle, Evade, Parley, SessionMode, TurnUndead, Wait
from osrlib.crawl.dungeon import TreasureBundle
from osrlib.crawl.events import (
    EncounterEndedEvent,
    EncounterStartedEvent,
    EvasionEvent,
    ExhaustionEvent,
    MonsterDefeatedEvent,
    PursuitEvent,
    StanceChangedEvent,
    SurpriseRolledEvent,
)
from osrlib.data import load_classes

__all__ = [
    "EncounterGroup",
    "EncounterState",
    "HANDLERS",
    "PURSUIT_ROUND_CAP",
    "PursuitState",
    "end_encounter",
    "start_encounter",
]

PURSUIT_ROUND_CAP = 30
"""The round at which a running pursuit gives up and the party gets away, exhausted.

A pursuit normally ends before this: the gap closes to 5' and the pursuers catch the party into
battle, or a sack dropped behind the party distracts them. Neither happens when the two sides run at
the same rate, because the gap then never changes, so the cap is the terminal escape valve. Reaching
it attaches the exhausted condition to every living member, posts an
[`ExhaustionEvent`][osrlib.crawl.events.ExhaustionEvent] and a
[`PursuitEvent`][osrlib.crawl.events.PursuitEvent] with code `encounter.pursuit.escaped`, and closes
the encounter with the outcome `"escaped"`.

The pursuit code reads this value directly rather than a ruleset setting, so a front end that wants a
shorter chase ends the encounter itself with
[`end_encounter`][osrlib.crawl.encounter.end_encounter] instead of changing the constant.
"""


class EncounterGroup(BaseModel):
    """One monster group in an encounter: its members, its distance, and the treasure it carries.

    You get these from `session.encounter.groups`. The encounter procedure builds them and the battle
    machinery updates them in place, so you never construct one yourself outside a save file. The
    group, not the individual monster, is the unit the fight works in: initiative rolls per side,
    morale breaks a whole group at once, and a battle order names a group through `target_group_id` on
    [`BattleDeclaration`][osrlib.crawl.commands.BattleDeclaration].
    """

    model_config = ConfigDict(validate_assignment=True)

    id: str
    """The session-scoped group id, `group-NNNN`, from the session's allocator. This is what a battle
    declaration's `target_group_id` names."""
    label: str
    """The group's display name: the monster template's name for a spawned or keyed group, and the
    encounter table row's name for a wandering one."""
    monster_ids: list[str] = Field(min_length=1)
    """The members' entity ids in spawn order, each resolvable through
    [`GameSession.combatant`][osrlib.crawl.session.GameSession.combatant]. Dead members stay in the
    list, so filter on the dead condition rather than on membership."""
    distance_feet: int = Field(ge=0)
    """The gap between the party and this group on the range track, in feet. Monsters close at their
    encounter rate and stop at [`MELEE_RANGE_FEET`][osrlib.crawl.battle.MELEE_RANGE_FEET]. A routed
    group runs the other way."""
    fleeing: bool = False
    """True once the group has broken morale and turned to run. It is still on the track and still
    takes hits in the back."""
    fled: bool = False
    """True once the group has run past [`FLEE_EXIT_FEET`][osrlib.crawl.battle.FLEE_EXIT_FEET] and left
    the fight, or once a group with morale 2 routed at the moment battle opened. An attack declared
    against it is rejected as naming an unknown group."""
    surrendered: bool = False
    """True once the group has given up. Its carried treasure drops as loot the way a slain group's
    does."""
    member_treasure: dict[str, TreasureBundle] = {}
    """The bundle each member carries, keyed by monster id, generated at spawn from the individual
    treasure types (P through T). A slain or surrendered member's bundle drops as loot when the
    encounter closes, and a routed member takes its own away."""
    group_treasure: TreasureBundle | None = None
    """The bundle the group shares, generated at spawn from the group treasure types (U and V), or None
    when the group carries none. It drops only when every member is defeated or the group surrenders,
    never when any member routed or fled."""


class PursuitState(BaseModel):
    """A chase in progress: the gap between the running party and its pursuers, updated each round.

    Read it off `session.encounter.pursuit`, which is None until an
    [`Evade`][osrlib.crawl.commands.Evade] command fails to shake a hostile group and the chase opens.
    While it is set, the encounter is a chase rather than a standoff: parley and turning are rejected,
    and [`Wait`][osrlib.crawl.commands.Wait] runs another chase round instead of another encounter
    round.
    """

    model_config = ConfigDict(validate_assignment=True)

    round: int = 0
    """Rounds run so far in this chase, counting from 1. The chase ends in escape at
    [`PURSUIT_ROUND_CAP`][osrlib.crawl.encounter.PURSUIT_ROUND_CAP]."""
    gap_feet: int = Field(ge=0)
    """The distance between the party and its pursuers, in feet. It opens at the nearest pursuing
    group's encounter distance, and each round it changes by the party's running rate less the slowest
    pursuing group's. At 5' or less the pursuers catch the party into battle."""


class EncounterState(BaseModel):
    """An open encounter: the monster groups, the stance, the surprise result, and any chase.

    You get this from `session.encounter`, which is None whenever no encounter is open. It serializes
    with the session, so a saved game restores mid-encounter. Treat it as something to read and render
    from, not to edit: the handlers in [`HANDLERS`][osrlib.crawl.encounter.HANDLERS] and the battle
    machinery own every field on it.
    """

    model_config = ConfigDict(validate_assignment=True)

    kind: str
    """How the encounter came about: `"wandering"` from the wandering-monster check, `"keyed"` from an
    area the adventure stocked, or `"spawned"` from a referee command."""
    area_ref: str | None = None
    """The keyed area's state reference, when `kind` is `"keyed"`. When every monster in the encounter
    ends up slain, routed, or surrendered, the close records this reference as resolved, so entering
    the area again starts no second fight."""
    groups: list[EncounterGroup] = Field(min_length=1)
    """The monster groups, each an [`EncounterGroup`][osrlib.crawl.encounter.EncounterGroup], in the
    order they were spawned. There is always at least one."""
    stance: str | None = None
    """The monsters' current disposition as a [`ReactionResult`][osrlib.core.tables.ReactionResult]
    value: `"attacks"`, `"hostile"`, `"uncertain"`, `"indifferent"`, or `"friendly"`. None only between
    construction and the first reaction roll."""
    round: int = 0
    """Encounter round beats run so far. A command that leaves the encounter open, like
    [`Wait`][osrlib.crawl.commands.Wait] or [`Parley`][osrlib.crawl.commands.Parley], adds one. A chase
    counts its own rounds on `pursuit` instead."""
    started_round: int
    """The session clock's round count when the encounter opened. The close uses it to charge the
    encounter its minimum one turn."""
    party_surprised: bool = False
    """True when the party lost the surprise roll and gave up a round."""
    monsters_surprised: bool = False
    """True when the monsters lost the surprise roll. Both sides surprised is momentary confusion, and
    neither side gains anything."""
    monsters_skip_rounds: int = 0
    """Round beats the monsters still owe to their own surprise. Each beat they sit out spends one, and
    a battle that opens while any remain gives the party a free round."""
    hostile_deadline: int | None = None
    """The encounter round at which a hostile group attacks, set each time the stance turns hostile.
    None until a hostile result comes up. It is read only while the stance is still hostile, so a
    stance the party talked back up ignores it."""
    evading: bool = False
    """True once the party has declared an evasion, which suspends a hostile group's deadline while the
    party backs away."""
    pursuit: PursuitState | None = None
    """The [`PursuitState`][osrlib.crawl.encounter.PursuitState] while a chase runs, else None."""


def _monsters(session, state: EncounterState | None = None) -> list:
    state = state or session.encounter
    return [session.combatant(monster_id) for group in state.groups for monster_id in group.monster_ids]


def start_encounter(
    session,
    *,
    groups: list[tuple[str, list]],
    kind: str,
    area_ref: str | None = None,
    distance_feet: int | None = None,
    monsters_roll_surprise: bool = True,
    monsters_aware: bool = False,
    party_aware: bool = False,
    pinned_stance: ReactionResult | None = None,
) -> list[Event]:
    """Open an encounter on a session: surprise, distance, reaction, and their first consequences.

    Call this when your own content decides a monster group has come into view. Spawn the monsters
    first with [`GameSession.spawn`][osrlib.crawl.session.GameSession.spawn] so their instances are in
    the session registry, then pass them here as `(label, instances)` pairs. On return the session is
    in `encounter` mode and `session.encounter` holds an
    [`EncounterState`][osrlib.crawl.encounter.EncounterState] you can read and render from. Drive the
    encounter with the encounter commands after that, and let
    [`end_encounter`][osrlib.crawl.encounter.end_encounter] close it. An attacking stance opens battle
    before this function returns, so check `session.battle` as well as `session.mode`.

    Use [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters] instead when a referee wants a fight
    on the party's current cell. That command rolls the count, spawns the instances, and calls
    this for you, and because it goes through the session's command log the encounter replays from a
    save. Reach for this function when you need an argument the command does not expose, like a stance
    fixed in advance or monsters that never roll for surprise.

    Wandering monsters never roll for surprise, because they come "moving in the direction of the
    party". A keyed area marked aware, a failed attempt to force a door, and a party carrying a light
    each skip the monsters' roll as well, and a successful listen marks the party aware. The party is
    surprised on a d6 of 1 or 2, and on 1 to 3 when it carries no light and not every living member
    has infravision, as a documented adaptation (see the adaptations register, under the blind-party
    adaptation).

    Args:
        session (osrlib.crawl.session.GameSession): The running session. Its party must be standing on
            a dungeon cell, and no encounter may already be open.
        groups: One `(label, instances)` pair per monster group. `label` is the group's display name
            and `instances` are live monster or NPC instances already in the session registry, as
            [`GameSession.spawn`][osrlib.crawl.session.GameSession.spawn] returns them.
        kind: How the encounter came about: `"wandering"`, `"keyed"`, or `"spawned"`.
        area_ref: The keyed area's state reference, when `kind` is `"keyed"`. Clearing such an
            encounter marks the area resolved.
        distance_feet: The starting gap for every group, in feet. None rolls 2d6 × 10 on the encounter
            stream and caps the result at the party cell's sight line. A value you supply is used as
            given.
        monsters_roll_surprise: False when these monsters can never be surprised, as wandering monsters
            cannot.
        monsters_aware: True when the monsters already expect intruders, which skips their roll.
        party_aware: True when the party has heard the room, which skips its own roll.
        pinned_stance: A [`ReactionResult`][osrlib.core.tables.ReactionResult] the adventure author
            fixed for this encounter. It skips the reaction roll, and no draw is spent on one.

    Returns:
        The opening events, in resolution order: one
            [`SurpriseRolledEvent`][osrlib.crawl.events.SurpriseRolledEvent] per side, the
            [`EncounterStartedEvent`][osrlib.crawl.events.EncounterStartedEvent], the reaction roll
            unless a stance was supplied, the
            [`StanceChangedEvent`][osrlib.crawl.events.StanceChangedEvent], and then whatever the
            stance sets off, up to a whole battle.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.core.tables import ReactionResult
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.commands import EnterDungeon, SessionMode
        from osrlib.crawl.dungeon import DungeonSpec, Edge, EdgeKind, LevelSpec
        from osrlib.crawl.encounter import start_encounter
        from osrlib.crawl.party import Party
        from osrlib.crawl.session import GameSession

        rules = Ruleset()
        draw = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
        hild = create_character(
            name="Hild",
            class_id="fighter",
            alignment=Alignment.LAWFUL,
            ruleset=rules,
            stream=draw,
        ).character
        level = LevelSpec(
            number=1,
            width=2,
            height=1,
            entrance=(0, 0),
            edges={"1,0:west": Edge(kind=EdgeKind.OPEN)},
        )
        crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
        town = TownSpec(name="Threshold", travel_turns={"crypt": 1})
        adventure = Adventure(name="A First Delve", town=town, dungeons=(crypt,))
        session = GameSession.new(Party(members=[hild]), adventure, seed=7)
        session.execute(EnterDungeon(dungeon_id="crypt"))

        # Spawn the instances, then run the encounter procedure over them with a stance fixed in advance.
        goblins = session.spawn("goblin", 2)
        events = start_encounter(
            session,
            groups=[("goblin", goblins)],
            kind="spawned",
            distance_feet=30,
            pinned_stance=ReactionResult.INDIFFERENT,
        )
        assert [type(event).__name__ for event in events] == [
            "SurpriseRolledEvent",
            "SurpriseRolledEvent",
            "EncounterStartedEvent",
            "StanceChangedEvent",
        ]
        assert session.mode is SessionMode.ENCOUNTER
        assert session.encounter.stance == "indifferent"
        assert session.encounter.groups[0].monster_ids == ["monster-0001", "monster-0002"]
        ```
    """
    from osrlib.crawl import exploration
    from osrlib.crawl.session import ENCOUNTER_STREAM

    stream = session.streams.get(ENCOUNTER_STREAM)
    events: list[Event] = []
    lit, _ = session.party_light()

    monsters_surprised = False
    if not monsters_roll_surprise or monsters_aware or lit:
        events.append(SurpriseRolledEvent(side="monsters", threshold=2, roll=None, surprised=False))
    else:
        monster_roll = stream.randbelow(6) + 1
        monsters_surprised = monster_roll <= 2
        events.append(
            SurpriseRolledEvent(side="monsters", threshold=2, roll=monster_roll, surprised=monsters_surprised)
        )

    party_surprised = False
    party_threshold = 2
    if not lit and not all(session.member_has_infravision(member) for member in session.party.living_members()):
        party_threshold = 3
    if party_aware:
        events.append(SurpriseRolledEvent(side="party", threshold=party_threshold, roll=None, surprised=False))
    else:
        party_roll = stream.randbelow(6) + 1
        party_surprised = party_roll <= party_threshold
        events.append(
            SurpriseRolledEvent(side="party", threshold=party_threshold, roll=party_roll, surprised=party_surprised)
        )

    if distance_feet is None:
        rolled_tens: int = stream.randbelow(6) + 1 + stream.randbelow(6) + 1
        distance_feet = rolled_tens * 10
        # RAW rolls 2d6 × 10' only "if there is uncertainty"; otherwise "the
        # situation in which the encounter occurs" fixes the distance — and the
        # walls the party is standing between are that situation. The roll is
        # therefore capped at the cell's longest straight sight line: a corridor
        # keeps the full hundred and twenty feet, while a twenty-foot room cannot
        # open at eighty. The cap reads the result, never the stream, so the draw
        # sequence is the same bounded or not.
        ceiling = exploration._sight_line_feet(session)
        if ceiling is not None:
            distance_feet = min(distance_feet, ceiling)

    group_models = [
        EncounterGroup(
            id=session.allocator.allocate("group"),
            label=label,
            monster_ids=[instance.id for instance in instances],
            distance_feet=distance_feet,
        )
        for label, instances in groups
    ]
    state = EncounterState(
        kind=kind,
        area_ref=area_ref,
        groups=group_models,
        started_round=session.clock.rounds,
        party_surprised=party_surprised,
        monsters_surprised=monsters_surprised,
    )
    # Both sides surprised is momentary confusion — no advantage either way (RAW).
    both = party_surprised and monsters_surprised
    if monsters_surprised and not both:
        state.monsters_skip_rounds = 1
    session.encounter = state
    session.mode = SessionMode.ENCOUNTER
    events.append(
        EncounterStartedEvent(
            monster_name=group_models[0].label,
            count=sum(len(group.monster_ids) for group in group_models),
            distance_feet=distance_feet,
            party_surprised=party_surprised,
            monsters_surprised=monsters_surprised,
        )
    )

    if pinned_stance is not None:
        stance = pinned_stance
    else:
        reaction = roll_reaction(stream=stream)
        events.extend(reaction.events)
        stance = reaction.result
    state.stance = stance.value
    events.append(StanceChangedEvent(stance=stance.value))

    if stance is ReactionResult.ATTACKS:
        from osrlib.crawl import battle as battle_module

        # A surprise advantage becomes a free battle round on either side: the
        # monsters' pending skipped beat is the party's free round, and a
        # surprised party grants the monsters theirs.
        party_free = state.monsters_skip_rounds > 0
        state.monsters_skip_rounds = 0
        events.extend(
            battle_module.start_battle(
                session, party_free_round=party_free, monsters_free_round=party_surprised and not both
            )
        )
        return events
    if stance is ReactionResult.HOSTILE:
        state.hostile_deadline = 1 + state.monsters_skip_rounds
    if party_surprised and not both:
        # The surprised side cannot act that round: the monsters take one beat
        # before the party's first command — and a battle opening on that beat
        # begins with their surprise round.
        events.extend(_end_of_round(session, party_lost_beat=True))
    return events


def _end_of_round(session, *, party_lost_beat: bool = False) -> list[Event]:
    """Close one encounter round beat: the clock ticks and the monsters act per stance.

    A round that leaves nobody standing, like a poison finishing the last member
    as the clock ticks, ends there: the monsters take no action, no
    reaction is re-rolled, and no battle opens. A battle among corpses would
    resolve to defeat on its first check, for a wipe that was never a battle.

    Args:
        session (osrlib.crawl.session.GameSession): The running session.
        party_lost_beat: True when this beat is the surprised party's lost round.
            A battle opening here starts with the monsters' free round.
    """
    state = session.encounter
    if state is None or session.battle is not None:
        return []
    state.round += 1
    events = session.advance_rounds(1)
    if not session.party.living_members():
        return events
    if state.monsters_skip_rounds > 0:
        state.monsters_skip_rounds -= 1
        return events
    from osrlib.crawl import battle as battle_module
    from osrlib.crawl.session import ENCOUNTER_STREAM

    if state.stance == ReactionResult.ATTACKS.value:
        events.extend(battle_module.start_battle(session, monsters_free_round=party_lost_beat))
    elif state.stance == ReactionResult.HOSTILE.value:
        if not state.evading and state.hostile_deadline is not None and state.round >= state.hostile_deadline:
            events.extend(battle_module.start_battle(session, monsters_free_round=party_lost_beat))
    elif state.stance == ReactionResult.UNCERTAIN.value:
        reaction = roll_reaction(stream=session.streams.get(ENCOUNTER_STREAM))
        events.extend(reaction.events)
        if reaction.result.value != state.stance:
            state.stance = reaction.result.value
            events.append(StanceChangedEvent(stance=state.stance))
            if reaction.result is ReactionResult.ATTACKS:
                events.extend(battle_module.start_battle(session, monsters_free_round=party_lost_beat))
            elif reaction.result is ReactionResult.HOSTILE:
                state.hostile_deadline = state.round + 1
    return events


# ---------------------------------------------------------------------- command handlers


def _handle_wait(session, command: Wait) -> tuple[list[Rejection], list[Event]]:
    state = session.encounter
    if state is None:
        return [Rejection(code="encounter.none_active")], []
    if state.pursuit is not None:
        return [], _pursuit_round(session)
    return [], _end_of_round(session)


def _handle_parley(session, command: Parley) -> tuple[list[Rejection], list[Event]]:
    from osrlib.crawl import exploration
    from osrlib.crawl.session import ENCOUNTER_STREAM

    state = session.encounter
    if state is None:
        return [Rejection(code="encounter.none_active")], []
    if state.pursuit is not None:
        return [Rejection(code="encounter.parley.mid_pursuit")], []
    member, rejections = exploration._member_able(session, command.character_id)
    if rejections:
        return rejections, []
    # Any number of re-rolls, each a fresh roll with the speaker's CHA (pinned —
    # RAW invites negotiation and gives no cap; a hostile result self-limits).
    reaction = roll_reaction(modifier=member.npc_reaction_modifier, stream=session.streams.get(ENCOUNTER_STREAM))
    events = list(reaction.events)
    if reaction.result.value != state.stance:
        state.stance = reaction.result.value
        events.append(StanceChangedEvent(stance=state.stance))
        if reaction.result is ReactionResult.HOSTILE:
            state.hostile_deadline = state.round + 1
    if reaction.result is ReactionResult.ATTACKS:
        from osrlib.crawl import battle as battle_module

        events.extend(battle_module.start_battle(session))
        return [], events
    events.extend(_end_of_round(session))
    return [], events


def _handle_evade(session, command: Evade) -> tuple[list[Rejection], list[Event]]:
    from osrlib.crawl import exploration

    state = session.encounter
    if state is None:
        return [Rejection(code="encounter.none_active")], []
    if state.pursuit is not None:
        return [Rejection(code="encounter.evade.already_evading")], []
    dropped_kind: str | None = None
    if command.drop == "treasure":
        if not any(member.inventory.purse.total_coins for member in session.party.living_members()):
            return [Rejection(code="encounter.evade.nothing_to_drop", params={"drop": "treasure"})], []
        dropped_kind = "treasure"
    elif command.drop == "food":
        carriers = [
            member
            for member in session.party.living_members()
            if exploration._find_item(member, "rations_standard") or exploration._find_item(member, "rations_iron")
        ]
        if not carriers:
            return [Rejection(code="encounter.evade.nothing_to_drop", params={"drop": "food"})], []
        dropped_kind = "food"

    state.evading = True
    events: list[Event] = []
    if dropped_kind == "treasure":
        # Fleeing for their lives, the party scatters its coin (pinned): every
        # living member's purse empties onto the trail, unrecoverable.
        for member in session.party.living_members():
            purse = member.inventory.purse
            if purse.total_coins:
                from osrlib.crawl.events import ItemsDroppedEvent

                events.append(
                    ItemsDroppedEvent(
                        character_id=member.id,
                        coins_gp_value=Coins(pp=purse.pp, gp=purse.gp, ep=purse.ep, sp=purse.sp, cp=purse.cp).value_gp,
                    )
                )
                purse.pp = purse.gp = purse.ep = purse.sp = purse.cp = 0
    elif dropped_kind == "food":
        from osrlib.crawl.events import ItemsDroppedEvent

        for member in session.party.living_members():
            if exploration._consume_item(member, "rations_standard") or exploration._consume_item(
                member, "rations_iron"
            ):
                events.append(ItemsDroppedEvent(character_id=member.id, item_ids=("rations",)))

    pursuers = [
        group
        for group in state.groups
        if not group.fled and not group.surrendered and _group_can_pursue(session, group)
    ]
    pursues = state.stance in (ReactionResult.ATTACKS.value, ReactionResult.HOSTILE.value) and pursuers
    if not pursues or _party_run_rate(session) > _pursuer_rate(session, pursuers):
        events.append(EvasionEvent(code="encounter.evasion.succeeded"))
        events.extend(end_encounter(session, "evaded"))
        return [], events
    events.append(EvasionEvent(code="encounter.evasion.pursuit"))
    state.pursuit = PursuitState(gap_feet=min(group.distance_feet for group in pursuers))
    events.extend(_pursuit_round(session, dropped_kind=dropped_kind))
    return [], events


def _handle_engage_battle(session, command: EngageBattle) -> tuple[list[Rejection], list[Event]]:
    from osrlib.crawl import battle as battle_module

    state = session.encounter
    if state is None:
        return [Rejection(code="encounter.none_active")], []
    # Battle begins with the party's initiative advantage only if the monsters
    # were surprised (their skipped beat becomes the party's free round).
    party_free = state.monsters_skip_rounds > 0
    state.monsters_skip_rounds = 0
    if state.pursuit is not None:
        # Turning to fight mid-chase: battle at the current gap.
        for group in state.groups:
            group.distance_feet = max(5, state.pursuit.gap_feet)
        state.pursuit = None
    return [], battle_module.start_battle(session, party_free_round=party_free)


def _handle_turn_undead(session, command: TurnUndead) -> tuple[list[Rejection], list[Event]]:
    from osrlib.crawl import exploration

    state = session.encounter
    if state is None:
        return [Rejection(code="encounter.none_active")], []
    if state.pursuit is not None:
        return [Rejection(code="encounter.turning.mid_pursuit")], []
    member, rejections = exploration._member_able(session, command.character_id)
    if rejections:
        return rejections, []
    definition = load_classes().get(member.class_id)
    turning_rejections = validate_turn_undead(member, definition)
    if turning_rejections:
        return turning_rejections, []
    candidates = _monsters(session, state)
    result = turn_undead(
        member,
        definition,
        candidates,
        ledger=session.ledger,
        clock=session.clock,
        allocator=session.allocator,
        registry=session.registry(),
        stream=session.streams.get(MAGIC_STREAM),
    )
    events = list(result.events)
    neutralized = all(
        has_condition(monster, Condition.DEAD) or has_condition(monster, Condition.TURNED) for monster in candidates
    )
    if neutralized:
        events.extend(session.advance_rounds(1))
        events.extend(end_encounter(session, "turned"))
        return [], events
    # Presenting the symbol is an aggressive act: surviving unturned monsters
    # attack (pinned, registered) — battle begins at once.
    from osrlib.crawl import battle as battle_module

    state.stance = ReactionResult.ATTACKS.value
    events.append(StanceChangedEvent(stance=state.stance))
    events.extend(session.advance_rounds(1))
    if not session.party.living_members():
        # The round that answered the symbol left nobody standing: no battle opens
        # for the dead. The stance change stands — it happened while they lived.
        return [], events
    events.extend(battle_module.start_battle(session))
    return [], events


def _handle_drop_during_encounter(session, command: DropItems) -> tuple[list[Rejection], list[Event]]:
    from osrlib.crawl import exploration

    state = session.encounter
    if state is None:
        return [Rejection(code="encounter.none_active")], []
    member, rejections = exploration._member_able(session, command.character_id)
    if rejections:
        return rejections, []
    rejections = exploration._validate_carried(member, command.item_ids, command.coins)
    if rejections:
        return rejections, []
    in_pursuit = state.pursuit is not None
    # Mid-pursuit drops scatter behind the running party (no pile); otherwise
    # they land on the party's cell like any exploration drop.
    events = exploration._apply_drop(session, member, command, to_pile=not in_pursuit)
    if in_pursuit:
        dropped_kind = None
        if command.coins.total_coins > 0:
            dropped_kind = "treasure"
        elif any(item_id in ("rations_standard", "rations_iron") for item_id in command.item_ids):
            dropped_kind = "food"
        events.extend(_pursuit_round(session, dropped_kind=dropped_kind))
    else:
        events.extend(_end_of_round(session))
    return [], events


# ---------------------------------------------------------------------- pursuit


def _party_run_rate(session) -> int:
    """Running: full movement rate in feet per round (RAW), slowest living member."""
    from osrlib.crawl import exploration

    return exploration.exploration_rate(session)


def _group_can_pursue(session, group: EncounterGroup) -> bool:
    """Whether any of the group's living members can actually give chase.

    Pursuit is running: a group whose living members are all asleep, paralysed,
    petrified, or webbed in place has nobody able to follow, and the party's
    flight from it succeeds.
    """
    return any(
        not has_condition(combatant, Condition.DEAD) and not cannot_move(combatant)
        for combatant in (session.combatant(monster_id) for monster_id in group.monster_ids)
    )


def _pursuer_rate(session, groups) -> int:
    """The slowest pursuing group's base ground mode, full rate per round.

    The slowest pursuer sets the pace, the way the slowest party member sets the
    party's. A pack that strings out is fiction. Flying reads dungeon ceilings,
    so the base ground mode is the mode with no descriptor, else the first
    printed.
    """
    rates = []
    for group in groups:
        for monster_id in group.monster_ids:
            combatant = session.combatant(monster_id)
            if getattr(combatant, "definition", None) is not None:
                # NPC adventurers run at their own movement rates; the slowest of
                # the group paces it like the party's own rule.
                rates.append(
                    min(
                        session.combatant(npc_id).movement_rate(session.ruleset)
                        for npc_id in group.monster_ids
                        if getattr(session.combatant(npc_id), "definition", None) is not None
                    )
                )
            else:
                modes = combatant.template.movement
                base = next((mode for mode in modes if mode.descriptor is None), modes[0])
                rates.append(base.rate_feet)
            break  # one rate per group: monsters share a stat block
    return min(rates, default=0)


def _group_intelligent(session, group: EncounterGroup) -> bool:
    """The intelligence proxy: a treasure ref with letters marks a hoarder.

    NPC adventuring parties count as intelligent for the distraction roll
    whatever their treasure letters, because they are people.
    """
    combatant = session.combatant(group.monster_ids[0])
    if getattr(combatant, "definition", None) is not None:
        return True
    return bool(combatant.template.treasure.letters)


def _pursuit_round(session, *, dropped_kind: str | None = None) -> list[Event]:
    """Run one pursuit round: distraction, the gap update, and the terminals.

    A round that leaves nobody standing ends there: nobody is running, so no
    distraction die is rolled, the gap goes unmeasured, and the pursuers catch
    no one into a battle.
    """
    from osrlib.crawl.session import ENCOUNTER_STREAM

    state = session.encounter
    pursuit = state.pursuit
    pursuit.round += 1
    events = session.advance_rounds(1)
    if not session.party.living_members():
        return events
    pursuers = [group for group in state.groups if not group.fled and not group.surrendered]
    if dropped_kind is not None:
        matches = any(_group_intelligent(session, group) == (dropped_kind == "treasure") for group in pursuers)
        if matches:
            distraction_roll = session.streams.get(ENCOUNTER_STREAM).randbelow(6) + 1
            if distraction_roll <= 3:
                events.append(
                    PursuitEvent(code="encounter.pursuit.distracted", round=pursuit.round, gap_feet=pursuit.gap_feet)
                )
                events.extend(end_encounter(session, "escaped"))
                return events
    gap = pursuit.gap_feet + _party_run_rate(session) - _pursuer_rate(session, pursuers)
    pursuit.gap_feet = max(0, gap)
    if pursuit.gap_feet <= 5:
        events.append(PursuitEvent(code="encounter.pursuit.caught", round=pursuit.round, gap_feet=pursuit.gap_feet))
        from osrlib.crawl import battle as battle_module

        for group in state.groups:
            group.distance_feet = 5
        state.pursuit = None
        events.extend(battle_module.start_battle(session))
        return events
    if pursuit.round >= PURSUIT_ROUND_CAP:
        events.extend(_attach_exhaustion(session))
        events.append(PursuitEvent(code="encounter.pursuit.escaped", round=pursuit.round, gap_feet=pursuit.gap_feet))
        events.extend(end_encounter(session, "escaped"))
        return events
    events.append(PursuitEvent(code="encounter.pursuit.round", round=pursuit.round, gap_feet=pursuit.gap_feet))
    return events


def _attach_exhaustion(session) -> list[Event]:
    from osrlib.crawl import exploration

    events: list[Event] = []
    attached = False
    for member in session.party.living_members():
        if session.ledger.active_on(member.id, exploration.EXHAUSTED_KIND):
            continue
        _, attach_events = session.ledger.attach(
            exploration.EXHAUSTED_DEFINITION,
            member.id,
            clock=session.clock,
            allocator=session.allocator,
            registry=session.registry(),
        )
        events.extend(attach_events)
        attached = True
    if attached:
        events.append(ExhaustionEvent(code="encounter.exhaustion.gained"))
    return events


# ---------------------------------------------------------------------- conclusion


def _drop_loot(session, state: EncounterState) -> list[Event]:
    """Drop slain and surrendered combatants' carried treasure at the party's cell.

    Surrender hands the treasure over, and the drop pile is already how the party
    picks it up. Routed monsters flee with theirs, and a group whose members
    routed or fled keeps its shared bundle.
    """
    from osrlib.crawl import exploration
    from osrlib.crawl.dungeon import DropPile

    if session.dungeon_state.location.kind != "dungeon":
        return []
    dropped: list[TreasureBundle] = []
    npc_spoils: list = []
    for group in state.groups:
        any_routed = group.fled or group.fleeing
        for monster_id in group.monster_ids:
            combatant = session.combatant(monster_id)
            if combatant is None:
                continue
            if has_condition(combatant, Condition.DEAD) or group.surrendered:
                bundle = group.member_treasure.pop(monster_id, None)
                if bundle is not None and not bundle.empty:
                    dropped.append(bundle)
                if getattr(combatant, "definition", None) is not None:
                    # A defeated NPC's kit and magic items are the loot — victory
                    # over an Expert party is the campaign's magic-item faucet.
                    npc_spoils.append(combatant)
            elif has_condition(combatant, Condition.TURNED) or any_routed:
                any_routed = True
        if group.group_treasure is not None and not group.group_treasure.empty and not any_routed:
            living = [
                session.combatant(monster_id)
                for monster_id in group.monster_ids
                if session.combatant(monster_id) is not None
            ]
            all_defeated = all(has_condition(member, Condition.DEAD) for member in living) or group.surrendered
            if all_defeated:
                dropped.append(group.group_treasure)
                group.group_treasure = None
    if not dropped and not npc_spoils:
        return []
    ref = exploration._cell_ref(session)
    pile = session.dungeon_state.piles.setdefault(ref, DropPile())
    total = Coins()
    for bundle in dropped:
        total = Coins(
            **{
                denomination: getattr(total, denomination) + getattr(bundle.coins, denomination)
                for denomination in ("pp", "gp", "ep", "sp", "cp")
            }
        )
        pile.valuables.extend(bundle.valuables)
        pile.magic_items.extend(bundle.magic_items)
    for npc in npc_spoils:
        inventory = npc.inventory
        for instance in inventory.all_instances():
            if hasattr(instance, "instance_id"):
                pile.magic_items.append(instance)
            else:
                from osrlib.crawl.dungeon import DroppedItem

                existing = next((entry for entry in pile.items if entry.item_id == instance.template.id), None)
                if existing is None:
                    pile.items.append(DroppedItem(item_id=instance.template.id, quantity=instance.quantity))
                else:
                    existing.quantity += instance.quantity
        pile.valuables.extend(inventory.valuables)
        total = Coins(
            **{
                denomination: getattr(total, denomination) + getattr(inventory.purse, denomination)
                for denomination in ("pp", "gp", "ep", "sp", "cp")
            }
        )
        inventory.items = []
        inventory.wielded = []
        inventory.rings = []
        inventory.valuables = []
        inventory.worn_armour = None
        inventory.shield = None
        purse = inventory.purse
        purse.pp = purse.gp = purse.ep = purse.sp = purse.cp = 0
    pile.coins = Coins(
        **{
            denomination: getattr(pile.coins, denomination) + getattr(total, denomination)
            for denomination in ("pp", "gp", "ep", "sp", "cp")
        }
    )
    return []


def end_encounter(session, outcome: str) -> list[Event]:
    """Close the open encounter: record defeats, drop loot, release effects, and settle the clock.

    Call this when the encounter is over on terms your own content decided: the party talked its way
    past, or walked away, or the standoff finished. You seldom call it yourself, because a
    victory, an evasion, an escape, and a successful turning all call it from inside the encounter and
    battle handlers. On return `session.encounter` is None and the session is back in `exploring`
    mode, unless the party is dead, in which case the session's own wipe check has already taken over.

    Every monster that ended slain, routed (fled, still fleeing, or turned), or surrendered gets a
    [`MonsterDefeatedEvent`][osrlib.crawl.events.MonsterDefeatedEvent] and a record on
    `session.defeated_monsters` with its experience value. Slain and surrendered monsters drop the
    treasure they carried onto the party's cell as a pile, and routed ones take theirs away. Every
    effect still running on any of the encounter's monsters then releases, because the fiction moves
    on and a dead troll's pending revival is narration rather than game state. Under a ruleset that
    awards experience immediately, the pooled experience divides and applies here and the record list
    clears with it. Otherwise the records wait for the party's return to town.

    The clock advances to whichever is later: the next turn boundary, or one full turn after the
    encounter opened. An encounter that opened mid-turn is therefore charged its full turn and can
    close mid-turn, since the boundary clause only guarantees the boundary is reached. The wandering
    monster cadence stays suspended across the whole encounter.

    Args:
        session (osrlib.crawl.session.GameSession): The running session, with `session.encounter` set.
        outcome: The label recorded on the
            [`EncounterEndedEvent`][osrlib.crawl.events.EncounterEndedEvent] and rendered in the
            default message line. The engine passes `"victory"`, `"evaded"`, `"escaped"`, and
            `"turned"`. Nothing compares the value, so your own content can use its own words.

    Returns:
        The defeat events, the immediate experience award when the ruleset uses one, the
            [`EncounterEndedEvent`][osrlib.crawl.events.EncounterEndedEvent], and the events of the
            clock advance that follows it.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.commands import EnterDungeon, SessionMode, SpawnMonsters
        from osrlib.crawl.dungeon import DungeonSpec, Edge, EdgeKind, LevelSpec
        from osrlib.crawl.encounter import end_encounter
        from osrlib.crawl.party import Party
        from osrlib.crawl.session import GameSession

        rules = Ruleset()
        draw = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
        hild = create_character(
            name="Hild",
            class_id="fighter",
            alignment=Alignment.LAWFUL,
            ruleset=rules,
            stream=draw,
        ).character
        level = LevelSpec(
            number=1,
            width=2,
            height=1,
            entrance=(0, 0),
            edges={"1,0:west": Edge(kind=EdgeKind.OPEN)},
        )
        crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
        town = TownSpec(name="Threshold", travel_turns={"crypt": 1})
        adventure = Adventure(name="A First Delve", town=town, dungeons=(crypt,))
        session = GameSession.new(Party(members=[hild]), adventure, seed=7)
        session.execute(EnterDungeon(dungeon_id="crypt"))
        session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=30))
        opened_at = session.clock.rounds

        # The party talks its way clear: close the encounter on your own terms.
        events = end_encounter(session, "evaded")
        assert [event.code for event in events] == ["encounter.ended"]
        assert session.encounter is None
        assert session.mode is SessionMode.EXPLORING
        assert session.clock.rounds == opened_at + 60  # a turn is 60 rounds, and the encounter owes one
        ```
    """
    from osrlib.core.clock import ROUNDS_PER_TURN
    from osrlib.crawl.session import DefeatedMonsterRecord

    state = session.encounter
    events: list[Event] = []
    all_defeated = True
    for group in state.groups:
        for monster_id in group.monster_ids:
            combatant = session.combatant(monster_id)
            monster_outcome = None
            if has_condition(combatant, Condition.DEAD):
                monster_outcome = "slain"
            elif has_condition(combatant, Condition.TURNED) or group.fled or group.fleeing:
                monster_outcome = "routed"
            elif group.surrendered:
                monster_outcome = "surrendered"
            if monster_outcome is None:
                all_defeated = False
                continue
            if getattr(combatant, "definition", None) is not None:
                # A defeated NPC adventurer is worth level-as-HD XP, recorded
                # under `npc:<class_id>` (pinned, registered).
                from osrlib.core.npc import npc_defeat_xp

                template_id = f"npc:{combatant.class_id}"
                xp = npc_defeat_xp(combatant.level)
            else:
                template_id = combatant.template.id
                xp = combatant.template.xp
            record = DefeatedMonsterRecord(
                monster_id=combatant.id,
                template_id=template_id,
                outcome=monster_outcome,
                xp=xp,
            )
            session.defeated_monsters.append(record)
            events.append(
                MonsterDefeatedEvent(
                    monster_id=combatant.id,
                    template_id=template_id,
                    outcome=monster_outcome,
                    xp=xp,
                )
            )
    events.extend(_drop_loot(session, state))
    for group in state.groups:
        for monster_id in group.monster_ids:
            for effect in list(session.ledger.active_on(monster_id)):
                events.extend(session.ledger.release(effect.effect_id, session.registry()))
    if state.area_ref is not None and all_defeated:
        session.dungeon_state.resolved_encounters.append(state.area_ref)
    from osrlib.core.ruleset import XpAwardTiming

    if session.ruleset.xp_award_timing is XpAwardTiming.IMMEDIATE and session.defeated_monsters:
        # Immediate mode: monster XP divides and applies at each encounter end,
        # and the ledger clears with it (no return award will consume it).
        pool = sum(record.xp for record in session.defeated_monsters)
        session.defeated_monsters = []
        events.extend(session.award_immediate_xp(pool))
    events.append(EncounterEndedEvent(outcome=outcome))
    boundary = -(-session.clock.rounds // ROUNDS_PER_TURN) * ROUNDS_PER_TURN
    target = max(boundary, state.started_round + ROUNDS_PER_TURN)
    if target > session.clock.rounds:
        events.extend(session.advance_rounds(target - session.clock.rounds))
    session.encounter = None
    session.odometer_thirds = 0
    if session.mode in (SessionMode.ENCOUNTER, SessionMode.BATTLE):
        session.mode = SessionMode.EXPLORING
    return events


HANDLERS = {
    Parley: _handle_parley,
    Evade: _handle_evade,
    EngageBattle: _handle_engage_battle,
    Wait: _handle_wait,
    TurnUndead: _handle_turn_undead,
}
"""The encounter commands this module handles, keyed by command class.

[`GameSession.execute`][osrlib.crawl.session.GameSession.execute] merges this map with the
exploration, battle, and referee maps and dispatches on the command's class, so a front end never
reads it. Read it to see which commands the encounter procedure owns, and call
[`GameSession.execute`][osrlib.crawl.session.GameSession.execute] rather than a handler directly: a
handler skips the mode gate, the command log, the listeners, and the rejection pre-phase that make a
rejected command cost nothing.

Each value takes `(session, command)` and returns a `(rejections, events)` pair.
[`DropItems`][osrlib.crawl.commands.DropItems] is not listed here even though it works during an
encounter: [`osrlib.crawl.exploration`][osrlib.crawl.exploration] owns that command and forwards it
here when an encounter is open.
"""
