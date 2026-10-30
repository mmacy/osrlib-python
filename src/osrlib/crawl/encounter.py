"""Decide whether and how a fight starts: surprise, distance, reaction, parley, evasion, pursuit.

An encounter is the part of play between the moment the party and a group of monsters notice
each other and the first blow. The functions in this module open an encounter, run it round by
round, and close it. They work on a [`GameSession`][osrlib.crawl.session.GameSession] whose party
is in a dungeon, with monster instances the session has already spawned. When a fight starts,
[`osrlib.crawl.battle`][osrlib.crawl.battle] takes over.

[`start_encounter`][osrlib.crawl.encounter.start_encounter] opens an encounter and puts the
session in `encounter` mode. From there, you drive it with the encounter commands:

- [`Parley`][osrlib.crawl.commands.Parley] and [`Wait`][osrlib.crawl.commands.Wait] each take
  one encounter round, and then the monsters act according to their stance. During a chase,
  `Wait` runs one round of the chase instead.
- [`Evade`][osrlib.crawl.commands.Evade] ends the encounter, or starts a chase when the monsters
  pursue.
- [`TurnUndead`][osrlib.crawl.commands.TurnUndead] ends the encounter when every monster is
  turned or destroyed, and otherwise starts a battle.
- [`EngageBattle`][osrlib.crawl.commands.EngageBattle] starts a battle.
- The referee command [`EndEncounter`][osrlib.crawl.commands.EndEncounter] ends the encounter
  when the party talks its way past or walks away.

[`end_encounter`][osrlib.crawl.encounter.end_encounter] closes the encounter and puts the session
back in `exploring` mode.

You rarely call `start_encounter` yourself. The session calls it for the
[`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters] and
[`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty] commands, for the wandering-monster
check, and when the party enters a keyed area that has monsters. Call it directly when your own
code decides that a group has just come into view. The session doesn't call it for a
`SpawnMonsters` executed while an encounter is open. The new group joins that encounter and rolls
no surprise or reaction.

You follow the encounter through events from
[`osrlib.crawl.events`][osrlib.crawl.events]:

- A [`SurpriseRolledEvent`][osrlib.crawl.events.SurpriseRolledEvent] for each side.
- An [`EncounterStartedEvent`][osrlib.crawl.events.EncounterStartedEvent] with the number of
  monsters and the distance.
- A [`StanceChangedEvent`][osrlib.crawl.events.StanceChangedEvent] each time the stance changes.
- An [`EncounterGroupJoinedEvent`][osrlib.crawl.events.EncounterGroupJoinedEvent] each time a
  `SpawnMonsters` adds a group to the open encounter.
- [`EvasionEvent`][osrlib.crawl.events.EvasionEvent] and
  [`PursuitEvent`][osrlib.crawl.events.PursuitEvent] while the party runs, and an
  [`ExhaustionEvent`][osrlib.crawl.events.ExhaustionEvent] when a chase goes on long enough to
  exhaust the party.
- A [`MonsterDefeatedEvent`][osrlib.crawl.events.MonsterDefeatedEvent] for each slain or routed
  monster when the encounter closes.
- An [`EncounterEndedEvent`][osrlib.crawl.events.EncounterEndedEvent] with the outcome.

The reaction roll is reported as a
[`ReactionRolledEvent`][osrlib.core.events.ReactionRolledEvent] that only the referee sees. For
each monster that regenerates, like a troll, `start_encounter` also reports an
[`EffectAttachedEvent`][osrlib.core.events.EffectAttachedEvent], which only the referee sees.

The stance is the monsters' current attitude toward the party, set by the reaction roll on 2d6.
The OSE SRD leaves what each result means in play to a human referee, so osrlib applies these
rules:

- 2 or less: the monsters attack at once.
- 3 to 5, hostile: the monsters attack at the end of the next encounter round, unless the party
  has started to evade or a parley has improved the stance.
- 6 to 8, uncertain: the monsters wait, and osrlib rolls the reaction again next round with
  no modifier.
- 9 to 11, indifferent: the party can pass, parley, or leave.
- 12 or more: the monsters are friendly.

Only attacking and hostile monsters pursue a party that evades. The SRD leaves pursuit to the
referee, so basing it on the reaction is an adaptation: a place where osrlib chooses one
interpretation of a rule the SRD leaves open. The
[adaptations register](https://mmacy.github.io/osrlib-python/adaptations/) lists each one.

When `start_encounter` rolls the distance, it rolls 2d6 × 10 feet and caps the result at the
longest straight line of sight from the party's cell. In a room, that's the room's span. In a
corridor, it's the length of the corridor, whatever the light. The SRD calls for the roll only
"if there is uncertainty", and osrlib treats the walls around the party as settling that
uncertainty. A distance you pass in `distance_feet` isn't capped.

The members of this module, in the order you're likely to need them:

- [`start_encounter`][osrlib.crawl.encounter.start_encounter] opens an encounter on a session.
- [`EncounterState`][osrlib.crawl.encounter.EncounterState] is the encounter's state in
  `session.encounter`, with an [`EncounterGroup`][osrlib.crawl.encounter.EncounterGroup] for each
  monster group and a [`PursuitState`][osrlib.crawl.encounter.PursuitState] during a chase.
- [`end_encounter`][osrlib.crawl.encounter.end_encounter] closes the encounter.
- [`PURSUIT_ROUND_CAP`][osrlib.crawl.encounter.PURSUIT_ROUND_CAP] is the round at which a chase
  ends.

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

# Spawning a group opens the encounter, which rolls surprise and reaction here.
session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=30))
assert session.mode is SessionMode.ENCOUNTER
assert session.encounter.stance == "indifferent"  # seed 7 rolls a reaction of 10 on 2d6
assert session.encounter.groups[0].distance_feet == 30

# Parley takes one encounter round and rolls the reaction again with Hild's CHA modifier.
result = session.execute(Parley(character_id=hild.id))
assert result.accepted
assert session.encounter.round == 1
```
"""

from pydantic import BaseModel, ConfigDict, Field

from osrlib.core.combat import cannot_move, roll_reaction
from osrlib.core.effects import Condition, has_condition, regeneration_definition
from osrlib.core.events import Event
from osrlib.core.items import Coins
from osrlib.core.spells import MAGIC_STREAM, turn_undead, validate_turn_undead
from osrlib.core.tables import ReactionResult
from osrlib.core.validation import Rejection
from osrlib.crawl.commands import DropItems, EngageBattle, Evade, Parley, SessionMode, TurnUndead, Wait
from osrlib.crawl.dungeon import TreasureBundle
from osrlib.crawl.events import (
    EncounterEndedEvent,
    EncounterGroupJoinedEvent,
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
    "PURSUIT_ROUND_CAP",
    "PursuitState",
    "end_encounter",
    "start_encounter",
]

PURSUIT_ROUND_CAP = 30
"""The chase round at which the pursuers give up and the party gets away, exhausted.

A chase usually ends before this round. Either the gap closes to 5 feet and the pursuers catch
the party, which starts a battle, or treasure or food the party drops distracts them. When the
party runs exactly as fast as its pursuers, the gap never changes, and this cap ends the chase.
At the cap, every living member who isn't already exhausted gets the exhausted condition (-2 to
attack and damage rolls, and attackers get +2 to hit them), with an
[`ExhaustionEvent`][osrlib.crawl.events.ExhaustionEvent]. The session then emits a
[`PursuitEvent`][osrlib.crawl.events.PursuitEvent] with the code `encounter.pursuit.escaped` and
closes the encounter with the outcome `"escaped"`.

The cap isn't a ruleset setting, so every chase uses the same cap. To end a chase sooner, send
[`EndEncounter`][osrlib.crawl.commands.EndEncounter] with the outcome `"departed"`.
"""


class EncounterGroup(BaseModel):
    """One monster group in an encounter: its members, its distance, and the treasure it carries.

    You get these from `session.encounter.groups`. The session creates them when an encounter
    opens or a [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters] adds a group to it, and
    updates them during the battle. Loading a save restores them, so you don't construct one
    yourself. Much of a fight works by group instead of by monster: a failed morale check breaks a
    whole group at once, and a party member's attack identifies a group in the `target_group_id` of
    a [`BattleDeclaration`][osrlib.crawl.commands.BattleDeclaration].
    """

    model_config = ConfigDict(validate_assignment=True)

    id: str
    """The group's id, like `group-0001`. The session assigns it, and it's unique within the
    session. A battle declaration's `target_group_id` refers to this id."""
    label: str
    """The group's display name. A keyed group gets the monster template's name, like
    `"Goblin"`, and a wandering group gets the encounter table row's name. A group from
    [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters] gets the template id, like
    `"goblin"`, and an NPC party is `"Basic Adventurers"` or `"Expert Adventurers"`. A group you
    open with [`start_encounter`][osrlib.crawl.encounter.start_encounter] gets the label you
    pass."""
    monster_ids: list[str] = Field(min_length=1)
    """The members' ids in the order they were spawned. Pass an id to
    [`GameSession.combatant`][osrlib.crawl.session.GameSession.combatant] to get the monster. Dead
    members stay in the list, so check each monster for the dead condition to find the living
    ones."""
    distance_feet: int = Field(ge=0)
    """The distance between the party and this group, in feet. In a battle, monsters close at their
    encounter movement rate and stop at
    [`MELEE_RANGE_FEET`][osrlib.crawl.battle.MELEE_RANGE_FEET]. A routed group moves away."""
    fleeing: bool = False
    """True once the group has failed a morale check and turned to run. Until it gets away, the
    party can still attack it."""
    fled: bool = False
    """True once the group has run past [`FLEE_EXIT_FEET`][osrlib.crawl.battle.FLEE_EXIT_FEET] and left
    the fight, or when its morale score is 2 and it ran as it entered the battle. The session refuses
    every battle declaration that names it, an attack, a move to close, an item, or a spell, with
    `battle.declaration.unknown_group`."""
    member_treasure: dict[str, TreasureBundle] = {}
    """The treasure each member carries, keyed by monster id. The session rolls it when it spawns the
    monster, from the treasure types for individuals (P through T). When the encounter closes, a slain
    member's treasure drops on the party's cell, and a routed member takes its treasure away."""
    group_treasure: TreasureBundle | None = None
    """The treasure the group shares, rolled at spawn from the treasure types for groups (U and V), or
    None when the group has none. It drops on the party's cell only when every member is slain. If
    any member routed, the group's treasure goes with the runners. When the turned members of a
    partly turned group break away, the group's treasure stays with the members who fight on."""


class PursuitState(BaseModel):
    """A chase in progress: the gap between the running party and its pursuers, updated each round.

    Read it from `session.encounter.pursuit`. It's None until a chase starts. A chase starts when the
    party evades with [`Evade`][osrlib.crawl.commands.Evade] and an attacking or hostile group runs
    at least as fast as the party. It also starts when the whole party retreats from a battle and a
    monster group is still able and willing to follow. During a chase, the session
    rejects [`Parley`][osrlib.crawl.commands.Parley] and
    [`TurnUndead`][osrlib.crawl.commands.TurnUndead], and [`Wait`][osrlib.crawl.commands.Wait] runs
    one round of the chase. [`EngageBattle`][osrlib.crawl.commands.EngageBattle] turns the party to
    fight at the current gap.
    """

    model_config = ConfigDict(validate_assignment=True)

    round: int = 0
    """The number of chase rounds run so far. The first round sets it to 1. The party escapes when it
    reaches [`PURSUIT_ROUND_CAP`][osrlib.crawl.encounter.PURSUIT_ROUND_CAP]."""
    gap_feet: int = Field(ge=0)
    """The distance between the party and its pursuers, in feet. It starts at the distance of the
    nearest pursuing group. Each round, it grows by the party's running rate and shrinks by the
    running rate of the slowest pursuing group. At 5 feet or less, the pursuers catch the party, and a
    battle starts."""


class EncounterState(BaseModel):
    """An open encounter: the monster groups, the stance, the surprise result, and any chase.

    You get this from `session.encounter`, which is None whenever no encounter is open. A save
    contains it with the session, so a saved game restores in the middle of an encounter. Read it to show the
    encounter, and change it only through commands: the session's encounter and battle commands
    update its fields.
    """

    model_config = ConfigDict(validate_assignment=True)

    kind: str
    """How the encounter started: `"wandering"` from the wandering-monster check, `"keyed"` from an
    area the adventure placed monsters in, or `"spawned"` from a referee command."""
    area_ref: str | None = None
    """The keyed area's reference, when `kind` is `"keyed"`. When every monster in the encounter is
    slain or routed, [`end_encounter`][osrlib.crawl.encounter.end_encounter] marks the area's
    encounter resolved, so entering the area again doesn't start another fight.
    [`EndEncounter`][osrlib.crawl.commands.EndEncounter] with `resolve_area=True` marks it resolved
    when the party gets past the monsters some other way."""
    groups: list[EncounterGroup] = Field(min_length=1)
    """The monster groups, each an [`EncounterGroup`][osrlib.crawl.encounter.EncounterGroup], in the
    order they were spawned. There's always at least one."""
    stance: str | None = None
    """The monsters' current attitude toward the party, as a
    [`ReactionResult`][osrlib.core.tables.ReactionResult] value: `"attacks"`, `"hostile"`,
    `"uncertain"`, `"indifferent"`, or `"friendly"`. It's None only while the encounter opens,
    before the reaction is rolled."""
    round: int = 0
    """The number of encounter rounds run so far. [`Wait`][osrlib.crawl.commands.Wait] and
    [`Parley`][osrlib.crawl.commands.Parley] each add one when the encounter goes on. A chase counts
    its rounds in `pursuit` instead."""
    started_round: int
    """The session clock's round count when the encounter opened.
    [`end_encounter`][osrlib.crawl.encounter.end_encounter] uses it to make the encounter cost at
    least one turn."""
    party_surprised: bool = False
    """True when the party lost the surprise roll and gave up a round."""
    monsters_surprised: bool = False
    """True when the monsters lost the surprise roll. When both sides are surprised, neither side gains
    anything."""
    monsters_skip_rounds: int = 0
    """The rounds the monsters still have to sit out because they were surprised. Each round they sit
    out reduces it by one. A battle that starts while any remain gives the party a free round."""
    hostile_deadline: int | None = None
    """The encounter round at which hostile monsters attack. The session sets it each time the stance
    becomes hostile, and it's None until then. It applies only while the stance is hostile, so once a
    parley improves the stance, the deadline no longer applies."""
    evading: bool = False
    """True once the party has tried to evade. While it's True, hostile monsters don't attack at
    `hostile_deadline`."""
    pursuit: PursuitState | None = None
    """The [`PursuitState`][osrlib.crawl.encounter.PursuitState] while a chase is on, or None."""


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
    """Open an encounter on a session: roll surprise, distance, and reaction, and act on the result.

    Call this when your own code decides a monster group has come into view. First spawn the
    monsters with [`GameSession.spawn`][osrlib.crawl.session.GameSession.spawn], which adds them to
    the session, then pass them here as `(label, instances)` pairs. When this function returns, the
    session is in `encounter` mode, and `session.encounter` is an
    [`EncounterState`][osrlib.crawl.encounter.EncounterState] you can read to show the encounter. An
    attacking stance starts a battle before this function returns, so check `session.battle` as well
    as `session.mode`. From there, drive the encounter with the encounter commands, like
    [`Parley`][osrlib.crawl.commands.Parley] and [`Wait`][osrlib.crawl.commands.Wait].
    [`end_encounter`][osrlib.crawl.encounter.end_encounter] closes it.

    When a referee wants a fight on the party's cell, execute
    [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters] instead. That command rolls the number of
    monsters, spawns them, and calls this function for you, and the session records it in the
    command log. Call this function when you need an argument the command doesn't have, like a
    stance you choose in advance or monsters that can't be surprised. Neither this function nor
    `GameSession.spawn` is a command, so the session doesn't record them in the command log. If you
    call them during play, the game still saves and loads, but
    [`replay_game`][osrlib.persistence.replay_game] won't reproduce the encounter.

    The monsters don't roll for surprise when `monsters_roll_surprise` is False or
    `monsters_aware` is True, or when the party carries a light. The session passes
    `monsters_roll_surprise=False` for wandering monsters other than an NPC party, because the SRD
    has them arrive "moving in the direction of the party". It passes `monsters_aware=True` for a
    keyed area marked aware and for an area the party alerted by failing to force a door, and
    `party_aware=True` after a successful listen. The party is surprised on a d6 roll of 1 or 2.
    When the party carries no light and some living member lacks infravision, it's surprised on 1
    to 3. That's an adaptation, a place where osrlib chooses one interpretation of a rule the SRD
    leaves open. The [adaptations register](https://mmacy.github.io/osrlib-python/adaptations/) lists it as
    the blind-party adaptation.

    A monster whose template has the `regeneration` ability, like a troll or a thoul, starts to
    regenerate when this function opens its encounter. Before the function rolls anything, it
    attaches the effect that [`regeneration_definition`][osrlib.core.effects.regeneration_definition]
    builds from the ability's params. Attaching the effect uses no random draws. From then on, the
    effects ledger heals the monster in each round the clock advances, at the rate the params set.
    A troll never gets back the damage it took from fire or acid. A slain troll gets back up 2d6
    rounds after the round it was killed in, unless fire and acid alone did as much damage as its
    maximum hit points. [`end_encounter`][osrlib.crawl.encounter.end_encounter] releases the
    effect, so a troll that is still dead when the encounter closes stays dead.

    Args:
        session (osrlib.crawl.session.GameSession): The running session. Its party must be in a
            dungeon, and no encounter may already be open.
        groups: One `(label, instances)` pair per monster group. `label` is the group's display name.
            `instances` are monster or NPC instances the session has already spawned, as
            [`GameSession.spawn`][osrlib.crawl.session.GameSession.spawn] returns them.
        kind: How the encounter started: `"wandering"`, `"keyed"`, or `"spawned"`. The function stores
            it in [`EncounterState.kind`][osrlib.crawl.encounter.EncounterState.kind].
        area_ref: The keyed area's reference, when `kind` is `"keyed"`. When the party slays or
            routs every monster, the session marks that area's encounter resolved.
        distance_feet: The starting distance for every group, in feet. With None, the function rolls
            2d6 × 10 on the encounter stream and caps the result at the longest line of sight from
            the party's cell. The function uses a value you pass as it is.
        monsters_roll_surprise: False when these monsters can't be surprised, like wandering
            monsters.
        monsters_aware: True when the monsters already expect intruders. They then don't roll for
            surprise.
        party_aware: True when the party has heard the monsters, for example by listening at a door.
            The party then doesn't roll for surprise.
        pinned_stance: A stance to use instead of rolling the reaction, as a
            [`ReactionResult`][osrlib.core.tables.ReactionResult]. The function doesn't roll a
            reaction or spend a random draw on one. Pass `ReactionResult.ATTACKS` to start a battle at once.

    Returns:
        The opening events, in the order they happened: an
        [`EffectAttachedEvent`][osrlib.core.events.EffectAttachedEvent] for each monster that
        regenerates, a [`SurpriseRolledEvent`][osrlib.crawl.events.SurpriseRolledEvent] for the
        monsters and one for the party, the
        [`EncounterStartedEvent`][osrlib.crawl.events.EncounterStartedEvent],
        the [`ReactionRolledEvent`][osrlib.core.events.ReactionRolledEvent] unless you passed
        `pinned_stance`, and the [`StanceChangedEvent`][osrlib.crawl.events.StanceChangedEvent].
        Then come the events of anything the stance or surprise starts, which can include a
        battle and its first round.

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
    events: list[Event] = _attach_regeneration(session, [instance for _, instances in groups for instance in instances])
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
        # The rules as written call for 2d6 × 10' only "if there is uncertainty".
        # Otherwise "the situation in which the encounter occurs" fixes the
        # distance, and the walls the party is standing between are that
        # situation. So the roll is capped at the cell's longest straight sight
        # line: a corridor allows the full 120 feet, but an encounter in a
        # twenty-foot room can't start at eighty. The cap applies to the result
        # and draws nothing from the stream, so the draw sequence is the same
        # with or without the cap.
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
    # Both sides surprised is momentary confusion, with no advantage either way (rules as written).
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

        # A surprise advantage becomes a free battle round for either side: the
        # round the surprised monsters would have sat out is the party's free
        # round, and a surprised party gives the monsters theirs.
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
        # The surprised party can't act this round: the monsters act once
        # before the party's first command, and a battle that opens in that
        # round begins with the monsters' free round.
        events.extend(_end_of_round(session, party_lost_beat=True))
    return events


def _join_encounter(session, *, label: str, instances: list, distance_feet: int) -> list[Event]:
    """Add one group to the open encounter between rounds, without opening the encounter again.

    The group rolls no surprise and no reaction. It shares the encounter's stance and surprise and,
    in a battle, the battle's round. The function resolves nothing and doesn't advance the clock, so
    the group first acts in the next encounter or battle round. A monster that regenerates starts to
    regenerate as it joins, as it would when its encounter opens. In a battle, a group whose morale
    score is 2 runs at once, as it would have if it had been there when the battle started.

    Returns:
        An [`EffectAttachedEvent`][osrlib.core.events.EffectAttachedEvent] for each monster that
        regenerates, the [`EncounterGroupJoinedEvent`][osrlib.crawl.events.EncounterGroupJoinedEvent],
        and a [`MonsterFledEvent`][osrlib.crawl.events.MonsterFledEvent] when the group runs at once.
    """
    events = _attach_regeneration(session, instances)
    group = EncounterGroup(
        id=session.allocator.allocate("group"),
        label=label,
        monster_ids=[instance.id for instance in instances],
        distance_feet=distance_feet,
    )
    session.encounter.groups.append(group)
    events.append(
        EncounterGroupJoinedEvent(
            group_id=group.id, label=label, count=len(group.monster_ids), distance_feet=distance_feet
        )
    )
    if session.battle is not None:
        from osrlib.crawl import battle as battle_module

        events.extend(battle_module._flee_if_unwilling(session, group))
    return events


def _attach_regeneration(session, instances: list) -> list[Event]:
    """Attach the regeneration effect to each instance whose template has the `regeneration` ability.

    The effect lasts until [`end_encounter`][osrlib.crawl.encounter.end_encounter] releases it with
    the rest of the encounter's effects. Attaching it uses no random draws, so the encounter's rolls
    are the same whether or not its monsters regenerate.
    """
    events: list[Event] = []
    for instance in instances:
        template = getattr(instance, "template", None)
        ability = template.ability("regeneration") if template is not None else None
        if ability is None:
            continue
        _, attach_events = session.ledger.attach(
            regeneration_definition(ability.params),
            instance.id,
            clock=session.clock,
            allocator=session.allocator,
            registry=session.registry(),
        )
        events.extend(attach_events)
    return events


def _end_of_round(session, *, party_lost_beat: bool = False) -> list[Event]:
    """Close one encounter round: the clock advances and the monsters act according to their stance.

    A round that leaves nobody alive, like a poison killing the last member as
    the clock advances, ends there: the monsters take no action, the reaction
    isn't rolled again, and no battle opens. A battle with a dead party would
    end in defeat on its first check, and the party didn't die in a battle.

    Args:
        session (osrlib.crawl.session.GameSession): The running session.
        party_lost_beat: True when this is the round the surprised party loses.
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
    # Any number of re-rolls, each a fresh roll with the speaker's CHA. The rules as
    # written invite negotiation and give no cap, and a hostile result self-limits (see the
    # adaptations register).
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
        # When the party flees, it scatters its coin (see the adaptations register):
        # every living member's purse empties onto the trail, and the coins can't be
        # recovered.
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

    pursuers = [group for group in state.groups if not group.fled and _group_can_pursue(session, group)]
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
    # The party gets a free round only if the monsters were surprised: the
    # round they would have sat out becomes the party's free round.
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
    # attack (see the adaptations register), and battle begins at once.
    from osrlib.crawl import battle as battle_module

    state.stance = ReactionResult.ATTACKS.value
    events.append(StanceChangedEvent(stance=state.stance))
    events.extend(session.advance_rounds(1))
    if not session.party.living_members():
        # If nobody is alive after the round that followed the turning attempt, no
        # battle opens. The stance change still applies, because it happened while
        # the party was alive.
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
    # During a chase, dropped goods scatter behind the running party and form
    # no pile. Otherwise they land on the party's cell like any other drop.
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
    """Running: full movement rate in feet per round (rules as written), slowest living member."""
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
    """The slowest pursuing group's running rate per round, from its base ground movement mode.

    The slowest pursuer sets the pace, the way the slowest party member sets the
    party's. The engine doesn't model a group spreading out. The base ground mode
    is the mode with no descriptor, or else the first one printed, so a flying
    mode doesn't count.
    """
    rates = []
    for group in groups:
        for monster_id in group.monster_ids:
            combatant = session.combatant(monster_id)
            if getattr(combatant, "definition", None) is not None:
                # NPC adventurers run at their movement rates. The slowest of
                # the group paces it, like the party's rule.
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
    """Whether a group counts as intelligent, which a monster does when its treasure entry lists letters.

    NPC adventuring parties count as intelligent for the distraction roll
    whatever their treasure letters, because they are people.
    """
    combatant = session.combatant(group.monster_ids[0])
    if getattr(combatant, "definition", None) is not None:
        return True
    return bool(combatant.template.treasure.letters)


def _pursuit_round(session, *, dropped_kind: str | None = None) -> list[Event]:
    """Run one pursuit round: distraction, the gap update, and the outcomes that end the chase.

    If nobody in the party is alive after the round's clock advance, the round
    ends there: nobody is running, so the function rolls no distraction die,
    doesn't update the gap, and starts no battle.
    """
    from osrlib.crawl.session import ENCOUNTER_STREAM

    state = session.encounter
    pursuit = state.pursuit
    pursuit.round += 1
    events = session.advance_rounds(1)
    if not session.party.living_members():
        return events
    pursuers = [group for group in state.groups if not group.fled]
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
    """Drop slain combatants' carried treasure at the party's cell.

    The drop pile is how the party picks it up. Routed monsters flee with theirs,
    and a group whose members routed or fled keeps its shared bundle.
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
            if has_condition(combatant, Condition.DEAD):
                bundle = group.member_treasure.pop(monster_id, None)
                if bundle is not None and not bundle.empty:
                    dropped.append(bundle)
                if getattr(combatant, "definition", None) is not None:
                    # A defeated NPC's kit and magic items are the loot: victory over
                    # an Expert party is where the campaign's magic items come from.
                    npc_spoils.append(combatant)
            elif has_condition(combatant, Condition.TURNED) or any_routed:
                any_routed = True
        if group.group_treasure is not None and not group.group_treasure.empty and not any_routed:
            living = [
                session.combatant(monster_id)
                for monster_id in group.monster_ids
                if session.combatant(monster_id) is not None
            ]
            all_defeated = all(has_condition(member, Condition.DEAD) for member in living)
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
    """Close the open encounter: record defeats, drop loot, release effects, and advance the clock.

    You seldom call this yourself. The session calls it when the party wins a battle, evades,
    escapes a chase, or turns every monster. When the party talks its way past or walks away, send
    [`EndEncounter`][osrlib.crawl.commands.EndEncounter] instead. The command calls this function,
    and the session records the command in the command log, so
    [`replay_game`][osrlib.persistence.replay_game] reproduces the close. The session doesn't
    record a direct call, so a replay of the game stays in the encounter. There must be an encounter open when you call
    it. If `session.encounter` is None, it raises `AttributeError`, because the function
    doesn't check for an open encounter first. When it returns, `session.encounter` is None. If the
    session was in `encounter` or `battle` mode, it's now in `exploring` mode.

    Every monster that ended slain or routed gets a
    [`MonsterDefeatedEvent`][osrlib.crawl.events.MonsterDefeatedEvent] and a record in
    `session.defeated_monsters` with its experience value. A routed monster is one that fled, is
    still fleeing, or was turned. The function records a defeated NPC adventurer with the
    template id `npc:<class_id>`.

    The treasure slain monsters carried drops in a pile on the party's cell, which
    [`PlayerView.piles`][osrlib.crawl.views.PlayerView.piles] shows and
    [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure] picks up. A slain NPC adventurer drops all
    its equipment, coins, and magic items too. Routed monsters take their treasure with them. Next,
    every effect still active on any of the encounter's monsters ends, because the encounter is
    over. That includes a slain troll's pending revival.

    When [`Ruleset.xp_award_timing`][osrlib.core.ruleset.Ruleset.xp_award_timing] is
    `XpAwardTiming.IMMEDIATE`, the party gets the experience for the defeated monsters here, and
    the function clears `session.defeated_monsters`. Otherwise, the records stay until the party
    returns to town.

    The clock then advances to the later of two points: the next turn boundary, or one full turn
    after the encounter opened. An encounter always costs at least one turn, and one that opened
    partway through a turn can close partway through a turn. Wandering-monster checks don't run
    during the encounter. The turn the encounter costs also covers any part of a turn the party had
    spent walking, so `session.odometer_thirds` resets to 0.

    Args:
        session (osrlib.crawl.session.GameSession): The running session, with `session.encounter` set.
        outcome: The outcome recorded on the
            [`EncounterEndedEvent`][osrlib.crawl.events.EncounterEndedEvent] and shown in its default
            message text. The session passes `"victory"`, `"evaded"`, `"escaped"`, or `"turned"`,
            and [`EndEncounter`][osrlib.crawl.commands.EndEncounter] passes `"parley"` or
            `"departed"`. osrlib doesn't check the value, so you can pass any label.

    Returns:
        The [`MonsterDefeatedEvent`][osrlib.crawl.events.MonsterDefeatedEvent]s, the events of
        effects ending, the experience award when the ruleset awards it immediately, the
        [`EncounterEndedEvent`][osrlib.crawl.events.EncounterEndedEvent], and the events of the
        clock advance after it.

    Raises:
        AttributeError: If no encounter is open on the session.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.commands import EndEncounter, EnterDungeon, SessionMode, SpawnMonsters
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
        session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=30))
        opened_at = session.clock.rounds

        # The party talks its way clear. The command runs end_encounter and is logged.
        result = session.execute(EndEncounter(outcome="parley"))
        assert [event.code for event in result.events] == ["encounter.ended"]
        assert result.events[0].outcome == "parley"
        assert session.encounter is None
        assert session.mode is SessionMode.EXPLORING
        assert session.clock.rounds == opened_at + 60  # a turn is 60 rounds, and an encounter costs at least one
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
            if monster_outcome is None:
                all_defeated = False
                continue
            if getattr(combatant, "definition", None) is not None:
                # A defeated NPC adventurer is worth the XP of a monster with Hit Dice
                # equal to its level, recorded under `npc:<class_id>` (see the
                # adaptations register).
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
        # In immediate mode, the monster XP is divided and awarded at the end of
        # each encounter, and the defeated-monster records are cleared, because
        # no return award will use them.
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


_HANDLERS = {
    Parley: _handle_parley,
    Evade: _handle_evade,
    EngageBattle: _handle_engage_battle,
    Wait: _handle_wait,
    TurnUndead: _handle_turn_undead,
}
"""The encounter commands this module handles, keyed by command class.

[`GameSession`][osrlib.crawl.session.GameSession] merges this map into its private handler table
the first time it dispatches a command, alongside the exploration, battle, and referee maps. There's
no registration point here: the only documented way to run a command is
[`GameSession.execute`][osrlib.crawl.session.GameSession.execute], which picks the handler, checks that
the command is legal in the current mode, and does the bookkeeping for the command log, the listeners, and
the up-front validation that a handler alone would skip.

Each value takes `(session, command)` and returns a `(rejections, events)` pair.
[`DropItems`][osrlib.crawl.commands.DropItems] isn't among them even though it works during an
encounter: [`osrlib.crawl.exploration`][osrlib.crawl.exploration] handles that command and forwards it
here when an encounter is open.
"""
