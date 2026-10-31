"""Run a fight round by round on the range track, with a replaceable policy choosing what the monsters do.

A battle follows an encounter from [`osrlib.crawl.encounter`][osrlib.crawl.encounter] when a fight
starts. The session opens it for you: when the monsters attack, when pursuing monsters catch the
party, and when the party executes [`EngageBattle`][osrlib.crawl.commands.EngageBattle]. Don't call
[`start_battle`][osrlib.crawl.battle.start_battle] in normal play. To start a fight, execute
`EngageBattle`. The session is then in `battle` mode
([`SessionMode.BATTLE`][osrlib.crawl.commands.SessionMode.BATTLE]), and `session.battle` contains a
[`BattleState`][osrlib.crawl.battle.BattleState].

Each round is one [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound] command, with
one [`BattleDeclaration`][osrlib.crawl.commands.BattleDeclaration] for each living party member who
can act. A declaration's
[`action`][osrlib.crawl.commands.BattleDeclaration.action] is `attack`, `cast`, `turn_undead`,
`move`, `use_item`, or `hold`.

The battle reference uses these terms:

- **Marching order** is the order of the party's members, front first, as
  [`Party.members`][osrlib.crawl.party.Party.members] lists them.
  [`ReorderParty`][osrlib.crawl.commands.ReorderParty] changes it outside a fight.
- The **range track** is how a battle measures distance, in the style of The Bard's Tale. Each
  monster group has a distance in feet from the **party line**, the place where the party stood
  when the encounter opened. Each member has an **advance**, their place in feet forward of that
  line, in [`BattleState.advance_feet`][osrlib.crawl.battle.BattleState.advance_feet]. When every
  member the monsters can see has fallen back behind the line, the line moves back to the foremost
  of them, as `advance_feet` describes. A member's
  distance to a group is the group's distance minus the member's advance, and
  [`member_distance_feet`][osrlib.crawl.battle.member_distance_feet] works it out. A member and a
  group can fight in melee at [`MELEE_RANGE_FEET`][osrlib.crawl.battle.MELEE_RANGE_FEET] or less.
- **Ranks** are the rows the party fights in. The ranks toward a group are the living members
  sorted by their distance to it, nearest first. Members at the same distance stand in the order
  they got there, then in marching order. Only the members of the **front rank** toward a group
  who stand within 5' of it can fight it in melee, and
  [`group_front_rank`][osrlib.crawl.battle.group_front_rank] lists them. Under the
  [`Ruleset`][osrlib.core.ruleset.Ruleset] option `formation_width_limit`, which is on by
  default, a rank is as wide as the space the party stands in, divided by
  [`FIGHTER_FRONTAGE_FEET`][osrlib.crawl.battle.FIGHTER_FRONTAGE_FEET]. That's two members side by
  side in a ten-foot corridor, and in a room, as many as the room's shorter side allows. With the
  option off, every member at the same distance stands in one rank.
- The **formation** is the party moving as one body. A `fighting_withdrawal` or a `retreat` that
  every declarer makes is a formation move. Every other move is the declaring member's alone.
- A **declarer** is a party member who has to declare an action this round. Every living member
  who can act is one, and
  [`EncounterView.declarers`][osrlib.crawl.views.EncounterView.declarers] lists them.
- **Surprise** gives one side a free round, a round in which the other side doesn't act. The
  session rolls for surprise when the encounter opens.
- A group's **morale score** is a number from 2 to 12, the `morale` field of the monster's
  template. When the OSE SRD calls for a morale check, the session rolls 2d6 against it. On a
  roll over the score, the group fails its morale check and flees. A group with a score of 2
  never fights.
- A **concentration spell** lasts only while its caster keeps concentrating on it.
- A declaration **fizzles** when it fails and has no effect. That happens when the session checks
  it again just before it resolves, and it no longer passes. A fizzled cast still loses its spell,
  as a disrupted one does.
- A **pursuit** follows when the party runs: the monsters go after it round by round. While one
  is on, `session.encounter.pursuit` contains a
  [`PursuitState`][osrlib.crawl.encounter.PursuitState].
- A **draw** is one random number taken from a **stream**, one of the session's named sequences
  of random numbers ([`RngStreams`][osrlib.core.rng.RngStreams]). Because the streams are
  separate, one part of the game never changes the dice another part gets.
- The **effects ledger** ([`EffectsLedger`][osrlib.core.effects.EffectsLedger]) is the session's
  record of timed effects, like a spell's, and the conditions they put on creatures.

Several of these rules are adaptations, places where osrlib commits to one reading of a rule the
OSE SRD leaves open. The range track is one. The
[adaptations register](https://mmacy.github.io/osrlib-python/adaptations/) lists each one.

There's no command to end a battle. A round ends it in one of three ways:

- Every party member is dead or turned to stone. The battle and the encounter close, and the
  session moves to `game_over`.
- Every monster group is dead or has fled out of the fight. The battle ends in victory, and
  [`end_encounter`][osrlib.crawl.encounter.end_encounter] closes the encounter.
- The whole party retreats. If no monster group can pursue the party, the encounter closes.
  Otherwise the session goes back to `encounter` mode with a pursuit.

You follow the battle through events from [`osrlib.crawl.events`][osrlib.crawl.events]:

- [`BattleStartedEvent`][osrlib.crawl.events.BattleStartedEvent] when the battle opens.
- [`BattleRoundEvent`][osrlib.crawl.events.BattleRoundEvent] at the start of each round.
- [`SpellDeclaredEvent`][osrlib.crawl.events.SpellDeclaredEvent] for each spell declared that
  round.
- [`MemberMovedEvent`][osrlib.crawl.events.MemberMovedEvent] when a member closes on a group or
  falls back by themselves.
- [`MemberWithdrewEvent`][osrlib.crawl.events.MemberWithdrewEvent] when a member who falls back
  alone trades places in the marching order with the member they named.
- [`GroupMovedEvent`][osrlib.crawl.events.GroupMovedEvent] when a group's distance from the party
  line changes.
- [`MonsterFledEvent`][osrlib.crawl.events.MonsterFledEvent] when a group fails its morale check
  and flees, and when the turned members of a group split off as a new group and flee.
- [`MonstersLeftBehindEvent`][osrlib.crawl.events.MonstersLeftBehindEvent] for helpless members a
  fleeing group leaves behind.
- [`BattleEndedEvent`][osrlib.crawl.events.BattleEndedEvent] with victory, defeat, or flight.

Between them come the events of the rule functions in [`osrlib.core`][osrlib.core]: attacks,
damage, saving throws, initiative, and morale.

A round follows the OSE SRD's combat sequence:

1. The party declares. Before anything happens, the session works out where each member's declared
   move would put them, and judges every declaration against those places. A member who closes and
   attacks in the same declaration is judged at the distance the close leaves them. If any
   declaration fails, the session refuses the whole round, and nothing changes.
2. The spells being cast are announced, and NPC adventurers choose their actions.
3. Initiative decides which side acts first.
4. On the party's turn, the session resolves movement, then missile attacks, then magic, then
   melee. In the movement step, the members who fall back alone move first and the members who
   close move next, each in marching order. A fighting withdrawal or a retreat that every declarer
   makes moves the whole formation instead, by moving the groups back. Each missile attack and each
   spell is measured from where its member stands after the movement step. A melee attack resolves
   only if the member is within 5' of the group and in its front rank when the attack comes up,
   or if the group is running and the member was within 5' of it when the round was accepted.
5. On the monsters' turn, the session checks each group's morale, then resolves the actions each
   group's policy chose, in order. A group that closes stops within 5' of the nearest member it can
   see, and its monsters can attack in the same round. A monster's melee attack resolves only
   against a member in its group's front rank within 5' of the group.
6. An attack with a weapon that has the `slow` quality comes last, after both sides have acted.

Every rule is a function in
[`osrlib.core.combat`][osrlib.core.combat] or [`osrlib.core.spells`][osrlib.core.spells], like
[`roll_initiative`][osrlib.core.combat.roll_initiative],
[`resolve_attack`][osrlib.core.combat.resolve_attack], and
[`cast_spell`][osrlib.core.spells.cast_spell]. To resolve one rule without a session, call those
functions directly, as
[Using the rules without a session](https://mmacy.github.io/osrlib-python/guides/rules-without-a-session/)
shows.

Each member moves by themselves. A member can close on a group and attack it in the same round,
and can fall back alone with a fighting withdrawal or a retreat, as
[`BattleDeclaration.move`][osrlib.crawl.commands.BattleDeclaration.move] describes. A group closes
until it's within reach of the nearest member it can see, so the members in front keep the
monsters off the members behind them. To check a round's declarations before you send them, call
[`preview_battle_round`][osrlib.crawl.battle.preview_battle_round]. To draw the battle and offer
each member their choices, read the player view's
[`EncounterView`][osrlib.crawl.views.EncounterView]: each member's advance, each group's distance
from each member, who can reach each group in melee, and which groups each member can see.

During a round, the session also applies these rules without a command for them:

- A caster who declared a spell loses it if an attack hits them, or they fail a saving throw,
  between initiative and their turn to act.
- A declared melee attack that can't resolve when it comes up fizzles with a
  [`DeclarationFizzledEvent`][osrlib.core.events.DeclarationFizzledEvent] at
  `battle.melee.fizzled`. That happens when its group has no living monster left to strike, or when
  the member stands out of reach or out of the group's front rank.
- Each monster group checks morale when the OSE SRD calls for it.
- When a monster's attack hits, the session applies the effects the attack lists: paralysis,
  poison, mummy rot, energy drain, petrification, and the spitting cobra's blindness. Each effect
  gets the saving throw the OSE SRD gives for it, if any. The adaptations register lists the
  effects the session leaves to the referee.
- A petrifying gaze, like the basilisk's or the medusa's, reaches each member in the gazer's
  group's front rank who stands within 5' of it, once a round. The gaze takes no attack roll.
  [`resolve_gaze`][osrlib.core.combat.resolve_gaze] skips a member who is averting their eyes,
  is blind, or is incapacitated. A member can avert their eyes, or fight by watching a mirror,
  through
  [`BattleDeclaration.gaze_defense`][osrlib.crawl.commands.BattleDeclaration.gaze_defense]. The
  session then applies that choice's penalties to the member's attack rolls that round.
- When a basilisk surprises the party, every member meets its gaze once as the encounter opens,
  at any distance. The basilisk doesn't gaze again in the monsters' free round that follows.
- Turned undead attack no one. When a cleric turns only some of a group, the turned monsters
  split off as a new group and flee. The rest fight on.
- A member's invisibility ends when the member attacks, throws or uses an item other than a
  potion, or turns undead. [`cast_spell`][osrlib.core.spells.cast_spell] ends it when the member
  casts.
- Each attack on a target protected by *mirror image* removes one image instead, and no attack
  roll is made.
- A scroll of protection keeps some monsters from attacking the party in melee. Its protection
  ends when a party member attacks in melee a monster that it keeps out.
- A caster maintains one concentration spell at a time. Its effects end when the caster declares
  anything other than `cast` or `hold`, when the caster's next spell is disrupted or fizzles, or
  when the caster casts another concentration spell. A `turn_undead` declaration ends them even
  when the turning doesn't resolve.
- The session picks the targets of a party member's area spell without a roll. The area fills
  with the target group's living members in the order they were spawned, as many as the area's
  size and the rank width allow. Under the `Ruleset` option `aoe_friendly_fire`, which is on by
  default, an area cast at a group also catches the members in that group's front rank who stand
  within 5' of it, while the area has room. An area aimed at the party, like a dragon's breath,
  covers the ranks toward the breathing group from the front. A rank at the same distance as the
  rank in front of it counts as 10' deeper, and a rank farther away starts at its own distance,
  so the area reaches as many ranks as its size covers.

An action policy chooses what each monster group does each round.
[`ScriptedPolicy`][osrlib.crawl.battle.ScriptedPolicy] is the default for monsters, and
[`NpcPartyPolicy`][osrlib.crawl.battle.NpcPartyPolicy] is the default for a party of NPC
adventurers. To give a group tactics of your own, write an
[`ActionPolicy`][osrlib.crawl.battle.ActionPolicy] and register it in
[`GameSession.action_policies`][osrlib.crawl.session.GameSession.action_policies] under the group's
id. A policy draws only from the
[`MONSTER_ACTION_STREAM`][osrlib.crawl.session.MONSTER_ACTION_STREAM], so your policy never changes
an attack or damage roll. When the ruleset is set to roll initiative for each combatant, each side
still acts together, and the side with the best single roll acts first, because the OSE SRD's
combat sequence runs per side.

The members of this module, in the order you're likely to need them:

- [`start_battle`][osrlib.crawl.battle.start_battle] opens a battle on the session's encounter.
  The session calls it for you.
- [`BattleState`][osrlib.crawl.battle.BattleState] is the battle's state in `session.battle`.
- [`ActionPolicy`][osrlib.crawl.battle.ActionPolicy] is the interface for a policy that chooses a
  monster group's actions, and [`MonsterAction`][osrlib.crawl.battle.MonsterAction] is one chosen
  action.
- [`ScriptedPolicy`][osrlib.crawl.battle.ScriptedPolicy] and
  [`NpcPartyPolicy`][osrlib.crawl.battle.NpcPartyPolicy] are the default policies.
- [`member_distance_feet`][osrlib.crawl.battle.member_distance_feet] and
  [`group_front_rank`][osrlib.crawl.battle.group_front_rank] report how far a member stands from a
  group and who can fight it in melee, for a policy of your own.
- [`preview_battle_round`][osrlib.crawl.battle.preview_battle_round] judges a round's planned
  declarations without playing the round, and returns a
  [`BattleRoundPreview`][osrlib.crawl.battle.BattleRoundPreview].
- [`MELEE_RANGE_FEET`][osrlib.crawl.battle.MELEE_RANGE_FEET],
  [`FLEE_EXIT_FEET`][osrlib.crawl.battle.FLEE_EXIT_FEET],
  [`FIGHTER_FRONTAGE_FEET`][osrlib.crawl.battle.FIGHTER_FRONTAGE_FEET], and
  [`NPC_PARTY_MORALE`][osrlib.crawl.battle.NPC_PARTY_MORALE] are the fixed values the battle
  rules use.

Typical usage:

```python
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.events import Visibility
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import (
    BattleDeclaration,
    EngageBattle,
    EnterDungeon,
    ResolveBattleRound,
    SessionMode,
    SpawnMonsters,
)
from osrlib.crawl.dungeon import DungeonSpec, Edge, EdgeKind, LevelSpec
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession

rules = Ruleset()
draw = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
members = [
    create_character(
        name=name,
        class_id="fighter",
        alignment=Alignment.LAWFUL,
        ruleset=rules,
        stream=draw,
        purchases=[("sword", 1)],
        equip_ids=["sword"],
    ).character
    for name in ("Hild", "Osric")
]
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
session = GameSession.new(Party(members=members), adventure, seed=7)
session.execute(EnterDungeon(dungeon_id="crypt"))

# Spawn a group already within reach, then choose to fight it.
session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=5))
session.execute(EngageBattle())
assert session.mode is SessionMode.BATTLE
assert session.battle.round == 0

# One command is one round: a declaration for each member the round expects, from the player view.
encounter = session.view(Visibility.PLAYER).encounter
group_id = encounter.groups[0].id
declarations = tuple(
    BattleDeclaration(character_id=member_id, action="attack", target_group_id=group_id, weapon_id="sword")
    for member_id in encounter.declarers
)
result = session.execute(ResolveBattleRound(declarations=declarations))
assert result.accepted
assert session.battle.round == 1
assert result.events[0].code == "battle.round.started"
assert "combat.initiative.rolled" in [event.code for event in result.events]
```
"""

import math
from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import Any, NamedTuple, Protocol

from pydantic import BaseModel, ConfigDict

from osrlib.core.character import ADVANCEMENT_STREAM
from osrlib.core.clock import TimeUnit
from osrlib.core.combat import (
    COMBAT_STREAM,
    AttackContext,
    MoraleTracker,
    Participant,
    SaveCategory,
    TargetingMode,
    alignments_differ,
    cannot_move,
    incapacitated,
    morale_modifier,
    morale_triggers,
    resolve_attack,
    resolve_breath,
    resolve_energy_drain,
    resolve_gaze,
    resolve_splash_attack,
    roll_initiative,
    saving_throw,
    validate_attack,
    validate_breath,
)
from osrlib.core.creature import Creature
from osrlib.core.dice import roll
from osrlib.core.effects import (
    EFFECTS_STREAM,
    Condition,
    EffectDefinition,
    grant_condition,
    has_condition,
    kill,
    remove_condition,
)
from osrlib.core.events import (
    AttackRolledEvent,
    DeclarationFizzledEvent,
    Event,
    SavingThrowRolledEvent,
    SpellDisruptedEvent,
)
from osrlib.core.items import (
    GearTemplate,
    ItemInstance,
    MagicItemCategory,
    MagicItemInstance,
    WeaponQuality,
    magic_item_template,
)
from osrlib.core.monsters import MonsterAbility, MonsterAttack, MonsterInstance, RoutineUse
from osrlib.core.spells import (
    MAGIC_STREAM,
    cast_spell,
    disrupt_casting,
    pop_mirror_image,
    turn_undead,
    validate_cast,
    validate_turn_undead,
)
from osrlib.core.validation import Rejection
from osrlib.crawl.commands import BattleDeclaration, ResolveBattleRound, SessionMode
from osrlib.crawl.dungeon import Direction, EdgeKind, Position
from osrlib.crawl.events import (
    BattleEndedEvent,
    BattleRoundEvent,
    BattleStartedEvent,
    GroupMovedEvent,
    MemberMovedEvent,
    MemberWithdrewEvent,
    MonsterFledEvent,
    MonstersLeftBehindEvent,
    SpellDeclaredEvent,
)
from osrlib.crawl.party import _anyone_standing, _stands
from osrlib.data import load_classes, load_spells

__all__ = [
    "ActionPolicy",
    "BattleRoundPreview",
    "BattleState",
    "FIGHTER_FRONTAGE_FEET",
    "FLEE_EXIT_FEET",
    "MELEE_RANGE_FEET",
    "MonsterAction",
    "NPC_PARTY_MORALE",
    "NpcPartyPolicy",
    "ScriptedPolicy",
    "group_front_rank",
    "member_distance_feet",
    "preview_battle_round",
    "start_battle",
]


def _int_param(params: Mapping[str, Any], key: str, default: int = 0) -> int:
    """Read an integer param out of schema-validated data whose union type the type checker can't index by key name."""
    return int(params.get(key, default))


MELEE_RANGE_FEET = 5
"""The distance, in feet, at which a party member and a monster group are close enough to fight in melee.

A member who closes on a group stops at this distance from it, not at zero. A group that closes
stops at this distance from the nearest member it can see. The session rejects a melee attack
declared by a member who would be farther from the group with `combat.attack.out_of_reach`, and
every melee attack resolves at this distance. When pursuing monsters catch the party, the battle
starts with every group at this distance and every member on the party line.

The range track has no distance between this and zero, so treat the value as fixed: the session uses
it for every reach and range check in a battle. A party member attacks a group farther away only
with a missile weapon, and [`validate_attack`][osrlib.core.combat.validate_attack] checks the
distance against that weapon's ranges.
"""

FLEE_EXIT_FEET = 120
"""How far, in feet, a group that failed its morale check runs before it leaves the battle.

A group that fails a morale check turns and runs its full movement rate each round. Once it
stands farther than this from every living party member, as
[`member_distance_feet`][osrlib.crawl.battle.member_distance_feet] measures each one, the session
marks the group fled. Until then, the party can still close on it or shoot at it. The distance is
measured from the members, not from the party line, so a member who closed on the group keeps it
in the battle longer, and a move of the party line never pushes a group out.

A fled group takes no further action. The session refuses every declaration aimed at it with
`battle.declaration.unknown_group`. A battle in which every group has fled or died ends in
victory.

A group whose living members are all afraid or turned, but which didn't fail a morale check, also
counts as gone for ending the battle once it stands farther than this from every living member.
"""


class BattleState(BaseModel):
    """A battle in progress: the round count and what the session tracks from one round to the next.

    You get this from `session.battle`, which is None whenever no battle is running. A battle
    doesn't replace the encounter. The monster groups, their distances, and their treasure stay on
    `session.encounter`, and this model contains only what the fight adds. It's saved with the
    session, so a saved game resumes in the middle of a battle. Read it to show the battle. The
    session updates every field as it resolves each round.

    To change a fight while it runs, execute a referee command from
    [`osrlib.crawl.commands`][osrlib.crawl.commands] instead of writing to this model. Referee
    commands are allowed in `battle` mode, so a referee can grant an item, award experience, set a
    flag, or advance the clock during a fight. The command log records the command, so a replay
    reproduces the change. [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters] adds a new
    monster group to the fight between rounds, and the group acts from the next round.

    The session refuses [`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty] and
    [`PlaceParty`][osrlib.crawl.commands.PlaceParty] with `session.command.encounter_in_progress`
    while an encounter is open. So an NPC party can't join a fight, and you can't move the party
    out of one.
    """

    model_config = ConfigDict(validate_assignment=True)

    round: int = 0
    """The number of rounds resolved so far. It's 0 between
    [`start_battle`][osrlib.crawl.battle.start_battle] and the first
    [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound], unless the monsters had a free
    round, which counts as round 1."""
    started_round: int
    """The session clock's round count when the battle opened."""
    monsters_hold_rounds: int = 0
    """The rounds in which the monsters still can't act because the party surprised them.

    In each round while this is above zero, the monsters do nothing, and the count goes down by one."""
    morale: MoraleTracker = MoraleTracker()
    """The [`MoraleTracker`][osrlib.core.combat.MoraleTracker] for this battle. It counts each group's
    passed morale checks, and a group that has passed two doesn't check again."""
    morale_acted: dict[str, list[str]] = {}
    """The morale triggers each group has already checked for, keyed by group id. A group checks morale
    once per trigger per battle, so a group that has checked on losing its leader doesn't check again
    for the same reason."""
    fired_last_round: list[str] = []
    """The ids of the party members who, in the last round, fired a weapon that has to be reloaded.
    [`validate_attack`][osrlib.core.combat.validate_attack] reads it to apply the reload rule under the
    ruleset's `weapon_reload` flag, so the session rejects a second shot in a row."""
    melee_engagements: dict[str, list[str]] = {}
    """For each party member id, the monsters that member has attacked in melee. When *protection from
    evil* bars a monster from attacking a character, the monster can attack that character anyway once
    it's on this list, as the spell's text in the SRD says."""
    concentration: dict[str, list[str]] = {}
    """For each caster id, the effect ids of the concentration spell that caster maintains. A caster
    maintains one at a time. The effects end when any of these happens:

    - The caster declares an action other than `cast` or `hold`. A `turn_undead` counts.
    - The caster's next spell is disrupted or fizzles.
    - The caster casts another concentration spell."""
    advance_feet: dict[str, int] = {}
    """Each party member's place on the range track, in feet forward of the party line, keyed by member id.

    The party line is where the party stood when the encounter opened. Each group's
    [`distance_feet`][osrlib.crawl.encounter.EncounterGroup.distance_feet] is measured from it. A member
    with no entry stands on the line, at zero. A positive advance is toward the monsters, and a negative
    one is behind the line. A member's distance to a group is the group's distance minus the member's
    advance, and [`member_distance_feet`][osrlib.crawl.battle.member_distance_feet] works it out for you.

    The session changes it in the party's movement step. A member's `close` raises their advance, and a
    `fighting_withdrawal` or a `retreat` the member makes alone lowers it. A formation move leaves every
    advance as it is and moves the groups instead.

    The party line moves back when the party has fallen back behind it. At the end of the party's
    movement step, when every living member who isn't invisible stands behind the line, the session
    moves the line back to the foremost of them. Every member's advance and every group's distance grow
    by the same number of feet, so no distance between a member and a group changes, and the session
    emits no event for it. The views show the moved values. The foremost members then stand on the line,
    at zero, and a group can close on them as usual, because a group's distance never goes below zero.
    When every living member is invisible, the line stays where it is.

    The line also moves back when a group closes on a member who stands behind it and the group's
    stopping point would be behind the line. That happens when a member the group can see still
    stands forward of the line, so the movement step left the line where it was. The line moves back
    by the shortfall in the same way, with no event, and the group then stands on the line.

    Every member starts a battle on the line, and the
    advances end with the battle, so the party stands on its cell as one body again once the fight is
    over. A save written mid-battle keeps them, and a save from before they existed loads with every
    member on the line."""
    arrived_round: dict[str, int] = {}
    """The battle round in which each member reached their current advance, keyed by member id.

    A member with no entry has stood where they are since the battle opened, which counts as round
    zero. The session uses it to order members who stand at the same distance from a group: the member
    who got there in an earlier round stands in front, and marching order settles a tie. So a member
    already fighting keeps their place in the front rank when others arrive beside them."""


class MonsterAction(BaseModel):
    """One monster's or NPC adventurer's action for a round, as an action policy chooses it.

    An [`ActionPolicy`][osrlib.crawl.battle.ActionPolicy] returns a list of these, and the session
    resolves them in that order. The model is frozen, so to change an action, build a new one with
    `model_copy(update=...)`. The session doesn't check a policy's actions in advance. When it reaches
    an action that's no longer possible, like a melee attack on a target that died earlier in
    the round, it skips the action.
    """

    model_config = ConfigDict(frozen=True)

    monster_id: str
    """The id of the monster or NPC that acts. It has to be one of the `monster_ids` of the group the
    policy is choosing for."""
    kind: str  # close | breath | melee | hold | npc_shoot | npc_cast | npc_drink
    """What the monster does:

    - `"close"` moves the whole group up to one encounter movement rate, the acting monster's,
      toward the nearest living member it can see. The group stops as soon as it's within
      [`MELEE_RANGE_FEET`][osrlib.crawl.battle.MELEE_RANGE_FEET] of that member, so it never
      moves past a member it can see, and it reaches that member in as many rounds as its rate
      needs. An invisible member doesn't stop it, and a member turned to stone does. The move is
      toward the party line, unless the group passed the member while they were invisible. When
      the member stands behind the line and the group would stop behind it, the line moves back
      first, as
      [`BattleState.advance_feet`][osrlib.crawl.battle.BattleState.advance_feet] describes, so a
      group's distance never goes below zero. With no member it can see, the group moves toward
      the line and stops 5' from it. A group already within 5' of a member it can see doesn't
      move, and the session emits no event for it. Only the first `"close"` in a group's actions
      for a round moves the group.
    - `"melee"` attacks `target_id`. The session resolves it only if the target stands in the
      group's front rank within 5' of it when the action comes up, as
      [`group_front_rank`][osrlib.crawl.battle.group_front_rank] reports. So a `"melee"` after the
      group's `"close"` strikes when the move has brought the target within reach, and is skipped
      otherwise. The one exception is a round in which the whole party retreats as a formation.
      A member who stood in the group's front rank within 5' of it when the round was accepted
      can then be struck all round, wherever the party has moved, because the OSE SRD gives the
      opponent its attack against a retreating combatant.
    - `"breath"` uses the monster's breath weapon on the party.
    - `"hold"` does nothing.
    - `"npc_shoot"`, `"npc_cast"`, and `"npc_drink"` are for NPC adventurers only. They make a
      missile attack on `target_id`, cast `spell_id`, and drink the potion `item_id`."""
    target_id: str | None = None
    """The id of the target, for `"melee"`, `"npc_shoot"`, and an `"npc_cast"` of a spell with one
    target. None for an area spell, whose targets depend on the area's size and the party's ranks."""
    spell_id: str | None = None
    """The spell to cast, for `"npc_cast"`."""
    spell_mode: str | None = None
    """Which mode of that spell to use, for `"npc_cast"`."""
    item_id: str | None = None
    """The instance id of the potion to drink, for `"npc_drink"`."""
    routine: int | None = None
    """Which of the monster's attack routines a `"melee"` action uses, as an index into its template's
    [`attacks`][osrlib.core.monsters.MonsterTemplate.attacks]. None uses the first routine.

    You can name any routine, whatever its [`use`][osrlib.core.monsters.AttackRoutine.use]. The
    [`RoutineUse`][osrlib.core.monsters.RoutineUse] values mean this:

    - `PRIMARY` is the first routine the monster's entry lists. The session rolls its attacks.
    - `ALTERNATIVE` is a routine the monster can use in place of the first in any round, like a
      mule's bite in place of its kick. The session rolls its attacks.
    - `SITUATIONAL` is a routine for a situation osrlib doesn't model, like an elephant's trample.
      The session rolls its attacks as melee attacks.
    - `SPECIAL` is a breath, a gaze, or a spell. The session rolls nothing for it, so the monster
      does nothing that round.

    The session also does nothing when the template has no routine at the index, and it treats a
    negative index the same way.

    Some routines list an attack named `gaze` or `breath`. The session never rolls those as attacks.
    It resolves a petrifying gaze once a round by itself, and a breath weapon through a `"breath"`
    action. Your game or the referee handles any other gaze or breath. An NPC adventurer fights with
    its weapon and ignores this field."""


class ActionPolicy(Protocol):
    """The interface for a policy that chooses, each round, what one monster group does.

    To give a group tactics of your own, write a class with a `choose` method that matches this
    protocol. Register an instance for a group by the group's id in
    [`GameSession.action_policies`][osrlib.crawl.session.GameSession.action_policies], a dict the
    session doesn't save. For a group with no entry there, the session uses
    [`ScriptedPolicy`][osrlib.crawl.battle.ScriptedPolicy], or
    [`NpcPartyPolicy`][osrlib.crawl.battle.NpcPartyPolicy] when its members are NPC adventurers.
    Policies are code, not saved state, so register yours again after you load a save, as you do
    listeners.

    The session passes your policy the
    [`MONSTER_ACTION_STREAM`][osrlib.crawl.session.MONSTER_ACTION_STREAM]. Take every random choice
    from that stream. The attack and damage rolls come from other streams, so your policy doesn't
    change them, and the fight stays reproducible from the same seed. Return whatever actions your
    tactics call for. The session skips an action that's no longer possible, and doesn't raise.
    To keep the default tactics and change one thing, wrap a
    [`ScriptedPolicy`][osrlib.crawl.battle.ScriptedPolicy] or a
    [`NpcPartyPolicy`][osrlib.crawl.battle.NpcPartyPolicy] and change the actions its `choose`
    returns.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.battle import MonsterAction
        from osrlib.crawl.commands import (
            BattleDeclaration,
            EngageBattle,
            EnterDungeon,
            ResolveBattleRound,
            SpawnMonsters,
        )
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
        session.execute(EngageBattle())

        # Monsters that hold their ground and never swing.
        class StandStillPolicy:
            def choose(self, session, group, stream):
                return [MonsterAction(monster_id=monster_id, kind="hold") for monster_id in group.monster_ids]

        # Register the policy for one group, by group id.
        group = session.encounter.groups[0]
        session.action_policies[group.id] = StandStillPolicy()

        result = session.execute(
            ResolveBattleRound(
                declarations=(BattleDeclaration(character_id=hild.id, action="hold"),),
            )
        )
        assert result.accepted
        assert group.distance_feet == 30  # the goblins never close
        assert hild.current_hp == hild.max_hp  # and never swing
        ```
    """

    def choose(self, session, group, stream) -> list[MonsterAction]:
        """Choose this round's actions for one monster group.

        The session calls this once per group per round. For a monster group, the call comes after
        the group's morale check and before the group moves or attacks. For a group of NPC
        adventurers, it comes at the start of the round, before initiative, because NPCs declare
        their spells then, as the party does.

        The session doesn't call it in these cases:

        - The group has fled, or has no living members.
        - A monster group is running, because it failed morale or because its living members are
          all turned or afraid.
        - [`BattleState.monsters_hold_rounds`][osrlib.crawl.battle.BattleState.monsters_hold_rounds]
          is above zero. Then the session calls it for no group.

        The group you choose for has no living turned members, because the session moves them into
        a separate group before it calls your policy.

        Args:
            session (osrlib.crawl.session.GameSession): The running session. Read the party, the
                registry, and the effects ledger from it, and don't change it.
            group (osrlib.crawl.encounter.EncounterGroup): The group to choose for. Its
                `distance_feet` is its current distance from the party line, and its `monster_ids`
                are its members, dead ones included. Call
                [`member_distance_feet`][osrlib.crawl.battle.member_distance_feet] and
                [`group_front_rank`][osrlib.crawl.battle.group_front_rank] to learn which members it
                can reach.
            stream (osrlib.core.rng.RngStream): The
                [`MONSTER_ACTION_STREAM`][osrlib.crawl.session.MONSTER_ACTION_STREAM] stream. Take
                every random choice from it. A choice from anywhere else makes the fight differ from a
                replay of the same seed.

        Returns:
            The actions to resolve, in the order they resolve. Return an empty list for a group that
            does nothing. Leave out members that are dead, incapacitated, or confused, as the built-in
            policies do. The session skips an action for a dead, confused, or turned member, and it
            resolves confusion itself.
        """
        ...


class ScriptedPolicy:
    """The default [`ActionPolicy`][osrlib.crawl.battle.ActionPolicy] for monsters: breathe, or close and attack.

    The session uses this policy for every monster group unless you register another one for it in
    [`GameSession.action_policies`][osrlib.crawl.session.GameSession.action_policies]. You construct
    one yourself only to wrap it in a policy of your own. Its `choose(session, group, stream)` method
    is the [`ActionPolicy`][osrlib.crawl.battle.ActionPolicy] method. It takes the running session,
    the monster group to choose for, and the stream of random numbers the session passes in, and it
    returns a list of [`MonsterAction`][osrlib.crawl.battle.MonsterAction]s.

    A monster with a breath weapon uses it while the nearest member the group can see is within the
    breath's length:

    - When the breath weapon has a number of uses per day, the monster breathes if it hasn't
      breathed yet that day. After that, it breathes or attacks with equal chance while uses remain,
      which is how the OSE SRD's dragons fight.
    - When the breath weapon works on a chance in six, as the hellhound's does, the policy rolls
      that chance each round.

    Otherwise, while every member the group can see is farther than
    [`MELEE_RANGE_FEET`][osrlib.crawl.battle.MELEE_RANGE_FEET] from it, each monster takes a
    `close` action. When the group's move will bring a member within reach, each monster also takes a
    `melee` action in the same round, aimed at a member of the front rank the move will produce. Once
    the group is within reach, each monster picks a target at random from the group's front rank, the
    members [`group_front_rank`][osrlib.crawl.battle.group_front_rank] lists. It leaves out invisible
    members, and members whose *protection from evil* bars that monster. When no target is left, the
    monster takes a `hold` action and does nothing that round. The members behind the front rank are
    out of the group's reach.

    Among the targets left, the monster prefers members who can still fight:

    - It passes over a member turned to stone while a target that isn't stone is in reach, because
      a statue is out of the fight. When every target in reach is stone, it attacks a statue.
    - A monster whose `paralysis` ability has the param `attacks_others`, like the ghoul and the
      thoul, also passes over a paralysed member while a target that is neither paralysed nor
      stone is in reach. That's the OSE SRD's "After paralysing a target, ghouls will attack
      others". The monster still picks a paralysed member before a statue.

    The policy picks a target once a round, so a paralysing hit doesn't move the rest of that
    round's attacks to another target.

    The monster then picks which of its attack routines to use. It chooses with equal chance among
    the routines whose [`use`][osrlib.core.monsters.AttackRoutine.use] is `PRIMARY` or
    `ALTERNATIVE`, and puts the choice in
    [`MonsterAction.routine`][osrlib.crawl.battle.MonsterAction.routine]. For example, a thoul
    claws or swings a weapon, and a mule kicks or bites. The policy never picks a `SITUATIONAL` or
    `SPECIAL` routine, like an elephant's trample or a vampire's gaze. For a monster with only one
    routine to pick from, the policy makes no random roll.

    The monster data has no ranges for monsters' missile attacks, so osrlib treats them like melee:
    the monster closes first, then attacks. This is one of the entries in the
    [adaptations register](https://mmacy.github.io/osrlib-python/adaptations/). Monsters under this
    policy never cast spells, because the monster data doesn't cover monster
    spellcasting, which is the referee's call.

    Write a policy of your own when you want a group to stand in a doorway and block it, aim every
    attack at one member, back off, cast, or use a situational routine when your game decides the
    situation calls for it. To keep
    these tactics and change one thing, wrap this policy and change the actions its `choose`
    returns, as the example does.
    [`NpcPartyPolicy`][osrlib.crawl.battle.NpcPartyPolicy] is the built-in policy for NPC adventurers,
    which also heals, drinks potions, casts, and shoots.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.battle import ScriptedPolicy
        from osrlib.crawl.commands import (
            BattleDeclaration,
            EngageBattle,
            EnterDungeon,
            ResolveBattleRound,
            SpawnMonsters,
        )
        from osrlib.crawl.dungeon import DungeonSpec, Edge, EdgeKind, LevelSpec
        from osrlib.crawl.party import Party
        from osrlib.crawl.session import GameSession

        rules = Ruleset()
        draw = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
        members = [
            create_character(
                name=name,
                class_id="fighter",
                alignment=Alignment.LAWFUL,
                ruleset=rules,
                stream=draw,
            ).character
            for name in ("Hild", "Osric")
        ]
        level = LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)})
        crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
        adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))
        session = GameSession.new(Party(members=members), adventure, seed=7)
        session.execute(EnterDungeon(dungeon_id="crypt"))
        session.execute(SpawnMonsters(template_id="goblin", count_fixed=3, distance_feet=5))
        session.execute(EngageBattle())

        # Keep the default tactics, but aim every melee attack at the most wounded of the members picked.
        class FocusFirePolicy:
            def __init__(self) -> None:
                self.default = ScriptedPolicy()
                self.last = []

            def choose(self, session, group, stream):
                actions = self.default.choose(session, group, stream)
                targets = [session.member(action.target_id) for action in actions if action.kind == "melee"]
                if targets:
                    weakest = min(targets, key=lambda member: (member.current_hp, member.id))
                    actions = [
                        action.model_copy(update={"target_id": weakest.id}) if action.kind == "melee" else action
                        for action in actions
                    ]
                self.last = actions
                return actions

        policy = FocusFirePolicy()
        session.action_policies[session.encounter.groups[0].id] = policy
        hold = tuple(BattleDeclaration(character_id=member.id, action="hold") for member in members)
        session.execute(ResolveBattleRound(declarations=hold))

        print([(action.kind, action.target_id) for action in policy.last])
        # [('melee', 'character-0001'), ('melee', 'character-0001'), ('melee', 'character-0001')]
        ```
    """

    def choose(self, session, group, stream) -> list[MonsterAction]:
        """Choose this round's actions for one monster group.

        The session calls this for you. Call it yourself only from a policy of your own that wraps
        this one, and pass on the arguments your `choose` received. A call from anywhere else draws
        from the session's stream outside any command, so a replay of the command log no longer
        matches the game.

        Args:
            session (osrlib.crawl.session.GameSession): The running session.
            group (osrlib.crawl.encounter.EncounterGroup): The group to choose for.
            stream (osrlib.core.rng.RngStream): The
                [`MONSTER_ACTION_STREAM`][osrlib.crawl.session.MONSTER_ACTION_STREAM] stream.

        Returns:
            The [`MonsterAction`][osrlib.crawl.battle.MonsterAction]s for each living monster that
            can act, in the group's member order: one action, or a `close` followed by a `melee`
            when the group's move will bring a member within reach. Incapacitated and confused
            monsters get none, because the session resolves confusion itself.
        """
        actions: list[MonsterAction] = []
        placement = _placement(session)
        nearest = _nearest_seen_distance(session, group, placement)
        pool = _seen(session, _reachable_front_rank(group, placement))
        arrival: list | None = None  # the targets once the group's close is made, worked out at its first close
        for monster in _living_monsters(session, group):
            if incapacitated(monster) or has_condition(monster, Condition.CONFUSED):
                continue  # the round handler resolves confusion, not the policy
            breath = monster.template.ability("breath_weapon")
            if breath is not None and _breath_usable(monster, nearest):
                params = breath.params
                if params.get("per_round_chance_in_six") is not None:
                    gate = stream.randbelow(6) + 1
                    if gate <= int(params["per_round_chance_in_six"]):
                        actions.append(MonsterAction(monster_id=monster.id, kind="breath"))
                        continue
                elif params.get("uses_per_day") is not None:
                    if monster.breath_uses_today == 0:
                        actions.append(MonsterAction(monster_id=monster.id, kind="breath"))
                        continue
                    if monster.breath_uses_today < int(params["uses_per_day"]) and stream.randbelow(2) == 0:
                        actions.append(MonsterAction(monster_id=monster.id, kind="breath"))
                        continue
            if nearest > MELEE_RANGE_FEET:
                actions.append(MonsterAction(monster_id=monster.id, kind="close"))
                if arrival is None and not cannot_move(monster):
                    # The session moves the group by the first close of a monster that can move.
                    arrival = _seen_after_close(session, group, placement, _encounter_rate(monster, session))
                targets = _preferred_targets(monster, _reachable_targets(session, monster, arrival or []))
            else:
                targets = _preferred_targets(monster, _reachable_targets(session, monster, pool))
                if not targets:
                    actions.append(MonsterAction(monster_id=monster.id, kind="hold"))
                    continue
            if targets:
                target = targets[stream.randbelow(len(targets))]
                routine = _pick_routine(monster, stream)
                actions.append(MonsterAction(monster_id=monster.id, kind="melee", target_id=target.id, routine=routine))
        return actions


class NpcPartyPolicy:
    """The default [`ActionPolicy`][osrlib.crawl.battle.ActionPolicy] for a group of NPC adventurers.

    The session uses this policy for a group of NPC adventurers, rather than monsters, unless you
    register another one for it in `session.action_policies`. The OSE SRD gives no tactics for a
    party of adventurers on the other side, so osrlib supplies these. They're one of the entries in
    the [adaptations register](https://mmacy.github.io/osrlib-python/adaptations/).

    In the list below, the NPC group is the group this policy chooses for, and the player party is
    the party your players control. Each living NPC takes the first of these actions that applies:

    1. A caster with a memorized healing spell heals the NPC group's most wounded member who is
       below half their hit points: the one with the lowest ratio of current to maximum hit points,
       with ties broken by id.
    2. An NPC below half their hit points, in a group with no healing spell left, drinks a healing
       potion they carry. This is the only item these tactics use.
    3. A caster with a memorized spell that deals damage, like *magic missile*, casts it at the
       player party. Spells the referee resolves by hand don't count. The caster picks the
       highest-level spell, with ties broken by spell id. An area spell catches the player party's
       ranks toward the NPC group from the front, as many ranks as its size covers. For a spell with
       one target, the caster picks a member at random from the first row of the player party's
       ranks toward the NPC group, leaving out invisible members.
    4. An NPC with a missile weapon shoots while every member of the player party it can see is
       farther than [`MELEE_RANGE_FEET`][osrlib.crawl.battle.MELEE_RANGE_FEET] from the NPC group.
       It picks its target at random from the first row of the player party's ranks toward the NPC
       group, leaving out invisible members, and the shot's range band is that member's own
       distance to the NPC group. So the members in front screen the members behind them from
       arrows as well as from blades.
    5. Otherwise, the NPC closes, and attacks in melee in the same round when the group's move
       brings a member within reach. It picks its target at random from the NPC group's front rank,
       the members [`group_front_rank`][osrlib.crawl.battle.group_front_rank] lists, leaving out
       invisible members. Like a monster, it passes over a member turned to stone while a target
       that isn't stone is in reach. Unlike a ghoul or a thoul, it doesn't pass over a paralysed
       member.

    NPCs declare their spells at the start of the round. An attack that hits or a failed saving
    throw can disrupt an NPC's spell, as it can a player character's, because the OSE SRD's rule
    applies to both sides. Every random choice comes from the
    [`MONSTER_ACTION_STREAM`][osrlib.crawl.session.MONSTER_ACTION_STREAM].

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.battle import NpcPartyPolicy
        from osrlib.crawl.commands import (
            BattleDeclaration,
            EngageBattle,
            EnterDungeon,
            ResolveBattleRound,
            SpawnNpcParty,
        )
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
        level = LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)})
        crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
        adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))
        session = GameSession.new(Party(members=[hild]), adventure, seed=7)
        session.execute(EnterDungeon(dungeon_id="crypt"))
        session.execute(SpawnNpcParty(party_kind="basic", distance_feet=60))
        session.execute(EngageBattle())

        # Use the default tactics, and record each round's choices for a referee screen.
        class LoggedNpcPolicy:
            def __init__(self) -> None:
                self.default = NpcPartyPolicy()
                self.log: list[list[str]] = []

            def choose(self, session, group, stream):
                actions = self.default.choose(session, group, stream)
                self.log.append([action.kind for action in actions])
                return actions

        policy = LoggedNpcPolicy()
        session.action_policies[session.encounter.groups[0].id] = policy
        session.execute(ResolveBattleRound(declarations=(BattleDeclaration(character_id=hild.id, action="hold"),)))

        print(policy.log)  # the fighters close while the rest shoot from 60 feet
        # [['close', 'close', 'npc_shoot', 'npc_shoot', 'npc_shoot', 'npc_shoot']]
        ```
    """

    def choose(self, session, group, stream) -> list[MonsterAction]:
        """Choose this round's actions for one group of NPC adventurers.

        The session calls this for you at the start of each round. Call it yourself only from a
        policy of your own that wraps this one, and pass on the arguments your `choose` received. A
        call from anywhere else draws from the session's stream outside any command, so a replay of
        the command log no longer matches the game.

        Args:
            session (osrlib.crawl.session.GameSession): The running session.
            group (osrlib.crawl.encounter.EncounterGroup): The group to choose for.
            stream (osrlib.core.rng.RngStream): The
                [`MONSTER_ACTION_STREAM`][osrlib.crawl.session.MONSTER_ACTION_STREAM] stream.

        Returns:
            The [`MonsterAction`][osrlib.crawl.battle.MonsterAction]s for each living member that
            can act, in the group's member order: one action, or a `close` followed by a `melee`
            when the group's move will bring a member within reach. Incapacitated and confused
            members get none.
        """
        from osrlib.data import load_spells

        actions: list[MonsterAction] = []
        living = _living_monsters(session, group)
        placement = _placement(session)
        nearest = _nearest_seen_distance(session, group, placement)
        first_row = _first_row_seen(session, group, placement)
        pool = _seen(session, _reachable_front_rank(group, placement))
        arrival: list | None = None  # the targets once the group's close is made, worked out at its first close
        catalog = load_spells()
        cure_available = any(_npc_cure_spell(member, catalog) is not None for member in living)
        for npc in living:
            if incapacitated(npc) or has_condition(npc, Condition.CONFUSED):
                continue
            cure = _npc_cure_spell(npc, catalog)
            wounded = [member for member in living if member.current_hp * 2 < member.max_hp]
            if cure is not None and wounded:
                target = min(wounded, key=lambda member: (member.current_hp / member.max_hp, member.id))
                spell, mode = cure
                actions.append(
                    MonsterAction(
                        monster_id=npc.id, kind="npc_cast", target_id=target.id, spell_id=spell.id, spell_mode=mode
                    )
                )
                continue
            if npc.current_hp * 2 < npc.max_hp and not cure_available:
                potion = _npc_healing_potion(npc)
                if potion is not None:
                    actions.append(MonsterAction(monster_id=npc.id, kind="npc_drink", item_id=potion.instance_id))
                    continue
            offense = _npc_offensive_spell(npc, catalog)
            if offense is not None:
                spell, mode = offense
                target_id = None
                targeting = spell.mode(mode).targeting
                if targeting is not None and targeting.mode is not TargetingMode.AREA and first_row:
                    target_id = first_row[stream.randbelow(len(first_row))].id
                actions.append(
                    MonsterAction(
                        monster_id=npc.id, kind="npc_cast", target_id=target_id, spell_id=spell.id, spell_mode=mode
                    )
                )
                continue
            if nearest > MELEE_RANGE_FEET:
                if _npc_wielded(npc, missile=True) is not None and first_row:
                    target = first_row[stream.randbelow(len(first_row))]
                    actions.append(MonsterAction(monster_id=npc.id, kind="npc_shoot", target_id=target.id))
                    continue
                actions.append(MonsterAction(monster_id=npc.id, kind="close"))
                if arrival is None and not cannot_move(npc):
                    # The session moves the group by the first close of a member who can move.
                    arrival = _seen_after_close(session, group, placement, _encounter_rate(npc, session))
                targets = _passing_over_statues(_reachable_targets(session, npc, arrival or []))
            else:
                targets = _passing_over_statues(_reachable_targets(session, npc, pool))
                if not targets:
                    actions.append(MonsterAction(monster_id=npc.id, kind="hold"))
                    continue
            if targets:
                target = targets[stream.randbelow(len(targets))]
                actions.append(MonsterAction(monster_id=npc.id, kind="melee", target_id=target.id))
        return actions


def _npc_cure_spell(npc, catalog):
    """The first memorized copy of a healing spell, with its healing mode."""
    for copy in getattr(npc, "memorized_spells", ()):
        spell = catalog.get(copy.spell_id)
        if copy.reversed:
            continue
        for mode in spell.modes:
            if not mode.manual and mode.effect is not None and mode.effect.kind == "heal":
                return spell, mode.key
    return None


def _npc_offensive_spell(npc, catalog):
    """The best memorized attack spell osrlib resolves without a referee: highest level first, ties by id."""
    best = None
    for copy in getattr(npc, "memorized_spells", ()):
        if copy.reversed:
            continue
        spell = catalog.get(copy.spell_id)
        for mode in spell.modes:
            if mode.manual or mode.effect is None or mode.effect.kind != "damage":
                continue
            key = (-spell.level, spell.id)
            if best is None or key < best[0]:
                best = (key, spell, mode.key)
            break
    if best is None:
        return None
    return best[1], best[2]


def _npc_healing_potion(npc):
    for instance in npc.inventory.all_instances():
        if isinstance(instance, MagicItemInstance):
            template = magic_item_template(instance)
            is_heal = template.effect is not None and template.effect.kind == "healing"
            if is_heal and template.category.value == "potion":
                return instance
    return None


def _npc_wielded(npc, *, missile: bool):
    """The NPC's first wielded weapon for a missile or a melee attack, either a template or a magic instance.

    The choice doesn't depend on distance. The attack's distance, the target member's own distance to the NPC's
    group, sets the missile's range band when the attack resolves (see `_resolve_npc_attack`).
    """
    for instance in npc.inventory.wielded:
        attack = instance if isinstance(instance, MagicItemInstance) else instance.template
        facet = _declaration_facet(attack)
        qualities = getattr(facet, "qualities", ())
        if missile:
            if WeaponQuality.MISSILE in qualities:
                return attack
        elif WeaponQuality.MELEE in qualities or WeaponQuality.MISSILE not in qualities:
            return attack
    return None


def _preferred_targets(monster: MonsterInstance, targets: list) -> list:
    """Narrow the targets a monster can reach to the ones `ScriptedPolicy` lets it pick.

    First `_passing_over_statues` drops the statues. Then a ghoul or thoul, whose `paralysis` ability
    has `attacks_others`, also drops paralysed members while a target that is neither paralysed nor
    stone is in reach. The targets stay in order. A list with no paralysed or petrified member
    comes back unchanged, so the policy's draw over it stays the same.
    """
    targets = _passing_over_statues(targets)
    if _attacks_others(monster):
        active = [
            target
            for target in targets
            if not has_condition(target, Condition.PARALYSED) and not has_condition(target, Condition.PETRIFIED)
        ]
        if active:
            return active
    return targets


def _passing_over_statues(targets: list) -> list:
    """Leave out the members turned to stone while a target that isn't stone is in reach.

    Both `ScriptedPolicy` and `NpcPartyPolicy` use this. When every target is stone, the whole list
    comes back, so the attacker picks a statue instead of holding. The targets stay in order.
    """
    standing = [target for target in targets if not has_condition(target, Condition.PETRIFIED)]
    return standing or targets


def _attacks_others(monster: MonsterInstance) -> bool:
    """Whether the monster turns from the members it paralyses, as the ghoul's and the thoul's entries say."""
    ability = monster.template.ability("paralysis")
    return ability is not None and not ability.manual and bool(ability.params.get("attacks_others"))


_PICKED_ROUTINE_USES = frozenset({RoutineUse.PRIMARY, RoutineUse.ALTERNATIVE})
"""The routine uses `ScriptedPolicy` picks among. A game's own policy picks a situational or special routine."""


def _pick_routine(monster: MonsterInstance, stream) -> int | None:
    """Pick the routine a melee action uses, with equal chance among the monster's primary and alternative routines.

    A monster with fewer than two such routines makes no random roll and gets None, which resolves its
    first routine. So the stream advances only for a monster that has a choice to make.
    """
    candidates = [
        index for index, routine in enumerate(monster.template.attacks) if routine.use in _PICKED_ROUTINE_USES
    ]
    if len(candidates) < 2:
        return None
    return candidates[stream.randbelow(len(candidates))]


def _breath_usable(monster: MonsterInstance, distance_feet: int) -> bool:
    if validate_breath(monster):
        return False
    ability = monster.template.ability("breath_weapon")
    if ability is None:  # unreachable: validate_breath rejected the tagless monster
        return False
    params = ability.params
    if "length_feet" in params and distance_feet >= _int_param(params, "length_feet"):
        return False
    return True


def _living_monsters(session, group) -> list:
    """The group's living combatants, monsters or NPC adventurers alike."""
    return [
        session.combatant(monster_id)
        for monster_id in group.monster_ids
        if not has_condition(session.combatant(monster_id), Condition.DEAD)
    ]


def _turning_candidates(session) -> list:
    """The monsters a turning reaches: the living members of every group that hasn't fled.

    The order is the encounter's group order, then each group's monster order, so a replay passes them
    to [`turn_undead`][osrlib.core.spells.turn_undead] in the same order.
    """
    return [
        monster for group in session.encounter.groups if not group.fled for monster in _living_monsters(session, group)
    ]


FIGHTER_FRONTAGE_FEET = 5
"""The width, in feet, that one combatant needs to fight side by side with the next.

Each rank of the party is as wide as the space it stands in, divided by this, so two fit abreast in
a ten-foot corridor, and a room fits as many as its shorter side allows. The session rejects a
melee attack declared by a member within reach of a group but outside its front rank with
`battle.declaration.not_in_front_rank`. The rank check doesn't apply to missile attacks, so the back
ranks can still shoot.

The SRD gives one number for this and leaves the rest to the referee: "The referee should judge
the number of opponents that can attack a single combatant, bearing in mind the combatant's size
and the available space around them. **10' passage:** Enough space for at most 2-3 characters to
fight side-by-side." osrlib uses the low end of that range, two in ten feet, or five feet each,
and applies it to the space the party is standing in.

To play without a width limit, turn off the ruleset's `formation_width_limit` flag, which puts every
living member at the same distance from a group in one rank. Changing this constant instead would
change the width of every space in the game.
"""


def _fighting_space(level, position: Position) -> set[Position]:
    """The cells the party can form a line across, flooded out from where it stands.

    The flood crosses open edges only. It stops at a wall. It stops at a door,
    because a doorway is a threshold rather than room to fight abreast. It stops
    wherever the space itself changes, which is what keeps a room's open mouth
    onto a corridor from counting the corridor as part of the room. Corridor cells
    belong to no area, so the flood's connectivity is what separates one
    passage from another.

    Args:
        level (osrlib.crawl.dungeon.LevelSpec): The level being fought on.
        position: The cell the party occupies.

    Returns:
        The connected cells of that one space, including `position`.
    """
    space = level.area_at(position)
    seen = {position}
    frontier = [position]
    while frontier:
        cell = frontier.pop()
        for direction in Direction:
            if level.edge(cell, direction).kind is not EdgeKind.OPEN:
                continue
            step = direction.vector
            neighbour = (cell[0] + step[0], cell[1] + step[1])
            if neighbour in seen or not level.in_bounds(neighbour):
                continue
            if level.area_at(neighbour) is not space:
                continue
            seen.add(neighbour)
            frontier.append(neighbour)
    return seen


def _frontage_cells(level, position: Position) -> int:
    """How thick the party's fighting space is, in cells.

    The measure is the widest square of unbroken space that includes the party's
    cell: how much room there is here, not how far the space reaches. A passage
    one cell wide gives one however long it runs, and gives one at a crossroads
    too, where two such passages meet and the floor is still ten feet across in
    every direction. A room gives its shorter side, and an irregular cave gives
    whatever it offers around the party.

    Reach is the wrong measure and looks right on rooms: for a rectangle the
    shorter run through a cell *is* the shorter side, so measuring runs agrees
    everywhere it is tested on rooms and then reports a corridor junction as a
    fifty-foot hall.

    Args:
        level (osrlib.crawl.dungeon.LevelSpec): The level being fought on.
        position: The cell the party occupies.

    Returns:
        The square's side in cells, at least 1.
    """
    space = _fighting_space(level, position)
    x, y = position
    side = 1
    while True:
        wider = side + 1
        corners = ((left, top) for left in range(x - wider + 1, x + 1) for top in range(y - wider + 1, y + 1))
        if not any(
            all((left + dx, top + dy) in space for dx in range(wider) for dy in range(wider)) for left, top in corners
        ):
            return side
        side = wider


def _formation_width(session) -> int | None:
    """Rank width: how many combatants fit abreast in the party's frontage.

    Returns `None` when the `formation_width_limit` flag is off, which lifts the
    cap entirely.
    """
    if not session.ruleset.formation_width_limit:
        return None
    from osrlib.crawl import exploration

    level = exploration._level(session)
    frontage_feet = _frontage_cells(level, exploration._position(session)) * exploration._CELL_FEET
    return max(1, frontage_feet // FIGHTER_FRONTAGE_FEET)


def _party_ranks(session) -> list[list]:
    """The living members cut into rows of the formation width in marching order, ignoring where each one stands.

    These are the ranks while every member stands on the party line, as they do outside a battle. They stand in
    for the ranks toward a group only where there's no group to measure toward (see `_front_rank_toward_nearest`
    and `_ally_protection_bonus`). Every rule that measures reach reads the ranks toward each group by distance
    (see `_ranks_toward`).
    """
    width = _formation_width(session)
    living = session.party.living_members()
    if width is None:
        return [living] if living else []
    return session.party.ranks(width)


def _party_front_rank(session) -> list:
    ranks = _party_ranks(session)
    return ranks[0] if ranks else []


def _ally_protection_bonus(session, defender, group=None) -> int:
    """The Ring of Protection 5' Radius: a rank-mate's ring shields the defender.

    The battle space has no adjacency finer than the rank, so "allies within 5'"
    is the defender's rank toward the attacking group: the members in the same
    row of the ranks toward it (see `_ranks_toward`), who stand at the same
    distance from it. A member who has closed on the group alone has no
    rank-mate there, and a member who stays behind isn't shielded by a ring
    worn in the front rank. With no group, every member counts as standing on
    the party line, so the rows are the marching-order ranks. The wearer's +1
    is counted with the rest of its equipped items, so this bonus comes only
    from allies' rings, and multiple rings never stack.

    Args:
        session (osrlib.crawl.session.GameSession): The battle's session.
        defender: The combatant under attack. A combatant in no rank gets no
            bonus.
        group: The group the attack comes from. For an attack with no
            attacking group, like a confused member's on a fellow member, pass
            the defender's nearest group. None reads the marching-order ranks.

    Returns:
        1 when a living rank-mate wears the radius ring, else 0.
    """
    if group is None:
        rows = [[member.id for member in rank] for rank in _party_ranks(session)]
    else:
        rows = _ranks_toward(group, _placement(session))
    defender_id = getattr(defender, "id", None)
    for row in rows:
        if defender_id not in row:
            continue
        for ally_id in row:
            if ally_id == defender_id:
                continue
            for ring in session.member(ally_id).inventory.rings:
                if magic_item_template(ring).params.get("radius_rank"):
                    return 1
        return 0
    return 0


def _reachable_targets(session, monster: MonsterInstance, pool: list) -> list:
    """Filter the pool by the *protection from evil* melee ban.

    A monster whose template bears a warded category may not initiate melee
    against a warded target of differing alignment. The ban breaks for a target
    who has engaged the barred creature in melee, as the rules as written say.
    """
    state = session.battle
    if _ward_bars_monster(session, monster):
        return []
    reachable = []
    for member in pool:
        barred = False
        for effect in session.ledger.active_on(member.id):
            bars = effect.definition.params.get("bars_melee_from")
            if not bars:
                continue
            categories = set(monster.template.categories)
            if categories & {str(entry) for entry in bars} and alignments_differ(monster, member):
                if monster.id not in state.melee_engagements.get(member.id, []):
                    barred = True
                    break
        if not barred:
            reachable.append(member)
    return reachable


def _identify_worn_items(session, target) -> list[Event]:
    """Identify the worn enchanted armour, shields, and rings of a combatant attacked in battle."""
    if getattr(target, "definition", None) is None:
        return []
    from osrlib.crawl import exploration

    events: list[Event] = []
    inventory = target.inventory
    worn = [slot for slot in (inventory.worn_armour, inventory.shield) if slot is not None]
    worn.extend(inventory.rings)
    for instance in worn:
        if isinstance(instance, MagicItemInstance) and (not instance.identified or not instance.cursed_revealed):
            template = magic_item_template(instance)
            if not instance.identified or (template.cursed and not instance.cursed_revealed):
                events.extend(exploration._identify_item_events(session, target, instance))
    return events


def _group_front_rank(session, group) -> list[MonsterInstance]:
    """The group's reachable rank: its first `width` living members, in spawn order."""
    width = _formation_width(session)
    living = _living_monsters(session, group)
    return living if width is None else living[:width]


def _monster_pool(session, group) -> list[MonsterInstance]:
    return [monster for monster in _group_front_rank(session, group) if not has_condition(monster, Condition.INVISIBLE)]


NPC_PARTY_MORALE = 9
"""The morale score a group of NPC adventurers checks against, on the usual 2 to 12 scale.

Monsters have a morale score in their stat block, but adventurers in the OSE SRD don't, so osrlib
uses the score of the Veteran, the SRD's monster entry for a low-level fighter. This is an
adaptation, a place where osrlib commits to one reading of a rule the SRD leaves open. The
[adaptations register](https://mmacy.github.io/osrlib-python/adaptations/) lists each one. With a
score of 9, a group passes the check on a 2d6 total of 9 or less, after modifiers, and fails and flees on a
higher total.

The battle code reads this value directly, so every NPC adventurer group in the game checks against
the same score, and no group can have a different one.
"""


def _group_morale_score(session, group) -> int | None:
    combatant = session.combatant(group.monster_ids[0])
    if getattr(combatant, "definition", None) is not None:
        return NPC_PARTY_MORALE
    return combatant.template.morale


def _encounter_rate(member_or_monster, session) -> int:
    """Encounter (per-round) rate: printed rate ÷ 3."""
    if isinstance(member_or_monster, MonsterInstance):
        modes = member_or_monster.template.movement
        base = next((mode for mode in modes if mode.descriptor is None), modes[0])
        return base.encounter_rate_feet
    return member_or_monster.movement_rate(session.ruleset) // 3


def _haste_multiplier(session, entity_id: str, key: str) -> int:
    multiplier = 1
    for effect in session.ledger.active_on(entity_id):
        value = effect.definition.params.get(key)
        if value is not None:
            multiplier = max(multiplier, int(value))
    return multiplier


# ---------------------------------------------------------------------- positions on the range track


class _Placement(NamedTuple):
    """Where each party member stands on the range track, for the rules that measure distance, rank, and reach.

    The round handler judges declarations against one of these, and the helpers for distance, ranks, and
    reach take one. So the same code can read the battle as it stands, or as a round's declared moves
    would leave it. `_placement` builds the first, and `_projected_placement` the second.
    """

    advance_feet: Mapping[str, int]
    """Each member's advance from the party line. A member with no entry stands on the line."""
    arrived_round: Mapping[str, int]
    """The round each member reached their advance. A member with no entry has stood there since round zero."""
    order: tuple[str, ...]
    """The living members' ids, in marching order."""
    width: int | None
    """The formation width, or None when the ruleset's `formation_width_limit` flag is off."""
    group_offset_feet: int = 0
    """How much farther from the party line every group stands than its `distance_feet` says.

    It's zero for the battle as it stands. A projection adds the distance a formation move takes the party
    back, and the distance the line moves back at the end of the movement step."""

    def distance(self, member_id: str, group) -> int:
        """How far apart the member and the group stand, in feet, whichever side of the group the member is on.

        It's the size of the group's distance from the line minus the member's advance. That difference is
        negative only for a member the group has passed, which a group does only to a member it can't see. Reach,
        ranks, and ranges all measure by this, so such a member 5' past the group is within its reach.
        """
        return abs(group.distance_feet + self.group_offset_feet - self.advance_feet.get(member_id, 0))


def _placement(session) -> _Placement:
    """Read where the party stands now. The call reads state and takes no draw.

    Outside a battle, as when a member uses a wand in `encounter` mode, every member stands on the party line.
    """
    state = session.battle
    return _Placement(
        advance_feet=dict(state.advance_feet) if state is not None else {},
        arrived_round=dict(state.arrived_round) if state is not None else {},
        order=tuple(member.id for member in session.party.living_members()),
        width=_formation_width(session),
    )


def _projected_placement(
    session, declared: Mapping[str, BattleDeclaration], trades: Sequence[_Trade], formation: str | None
) -> _Placement:
    """Work out where the party would stand once the party's movement step makes the declared moves.

    The validation pre-phase judges every declaration against this, and
    [`preview_battle_round`][osrlib.crawl.battle.preview_battle_round] reports it. It makes the moves the
    movement step would make from the battle as it stands, with the same code:

    - A formation move takes every group back by `_formation_feet`, and moves no member.
    - Otherwise each member's own move comes from `_plan_member_moves`, the plan the movement step carries
      out: the fall-backs with their trades of marching-order places, then the closes. Each member who
      moves gets the next round as their arrival round, as the step gives them.
    - Last, the party line moves back by `_line_shift` when every member the monsters can see stands
      behind it, as `_move_line_back` moves it.

    Nothing the monsters do is in it, because the session decides that after it rolls initiative. The call
    reads state, changes nothing, and takes no draw.

    Args:
        session: A session with a running battle.
        declared: The declarations to project, keyed by member id. A member with none doesn't move.
        trades: The trades of marching-order places that `_plan_trades` planned for them.
        formation: The formation move the declarations make, or None.
    """
    state = session.battle
    advance_feet = dict(state.advance_feet)
    arrived_round = dict(state.arrived_round)
    order = [member.id for member in session.party.living_members()]
    offset = 0
    if formation is not None:
        offset = _formation_feet(session, formation)
    else:
        for step in _plan_member_moves(session, declared, trades):
            advance_feet[step.member_id] = step.advance_feet
            arrived_round[step.member_id] = state.round + 1
            if step.trade is not None:
                here, there = order.index(step.trade.member_id), order.index(step.trade.replacement_id)
                order[here], order[there] = order[there], order[here]
    shift = _line_shift(session, advance_feet)
    if shift:
        for member_id in [*advance_feet, *(member_id for member_id in order if member_id not in advance_feet)]:
            advance_feet[member_id] = advance_feet.get(member_id, 0) + shift
        offset += shift
    return _Placement(
        advance_feet=advance_feet,
        arrived_round=arrived_round,
        order=tuple(order),
        width=_formation_width(session),
        group_offset_feet=offset,
    )


def _signed_distance(session, member_id: str, group) -> int:
    """The group's distance from the party line minus the member's advance, in the running battle.

    [`member_distance_feet`][osrlib.crawl.battle.member_distance_feet] and `MemberMovedEvent` report this
    value. It's negative for a member the group has passed. The rules measure by its size (see
    `_member_distance`).
    """
    return group.distance_feet - session.battle.advance_feet.get(member_id, 0)


def _member_distance(session, member_id: str, group) -> int:
    """How far apart the member and the group stand in the running battle, as `_Placement.distance` measures it."""
    return abs(_signed_distance(session, member_id, group))


def _nearest_member_distance(session, group) -> int:
    """How far the group stands from the nearest living member, which decides when a group that runs is gone.

    Before any member has moved, every member stands on the party line, so this is the group's own
    distance. Outside a battle, and in a session with no living member, it's the group's distance too.
    """
    living = session.party.living_members()
    if session.battle is None or not living:
        return group.distance_feet
    return min(_member_distance(session, member.id, group) for member in living)


def _seen(session, member_ids: Iterable[str]) -> list:
    """The members among `member_ids` whom the monsters can see, which leaves out invisible members, in order."""
    members = (session.member(member_id) for member_id in member_ids)
    return [member for member in members if not has_condition(member, Condition.INVISIBLE)]


def _nearest_seen_distance(session, group, placement: _Placement) -> int:
    """How far the group stands from the nearest living member it can see, which decides whether it closes.

    An invisible member doesn't count, unless every living member is invisible: then it's the nearest living
    member. Before any member has moved, every member stands on the party line, so this is the group's own
    distance. A session with no living member gives the group's distance too.
    """
    seen = [member.id for member in _seen(session, placement.order)] or list(placement.order)
    return min(
        (placement.distance(member_id, group) for member_id in seen),
        default=group.distance_feet + placement.group_offset_feet,
    )


def _first_row_seen(session, group, placement: _Placement) -> list:
    """The members of the first row toward the group whom it can see, in marching order, at any distance.

    An NPC adventurer who shoots or casts a spell at one member picks from these, as does a breath that hits one
    target, like the hellhound's. The members in front screen the members behind them. Before any member has
    moved, this is the first rank of the marching order, less its invisible members.
    """
    rows = _ranks_toward(group, placement)
    return _seen(session, rows[0]) if rows else []


def _closing_distance(session, group, rate: int) -> int:
    """Where a group's `close` takes it, as its new distance from the party line, by the arithmetic alone.

    The group moves up to `rate` feet toward the nearest living member it can see, and stops 5' from them, so it
    never moves past a member it can see and always reaches them in as many rounds as its rate needs. That's toward
    the party line, unless the group passed the member while they were invisible. A group already within 5' of a
    member it can see doesn't move. With no member it can see, the group moves toward the line and stops 5' from
    it. Members at the same distance on both sides draw the group toward the line. It reads state and takes no
    draw.

    Returns:
        The new distance. It's below zero when the group's stopping point is behind the line, which happens when
            the member it closes on stands behind the line. The session then moves the line back by the shortfall
            before it places the group (see `_place_closing_group`). The distances between the group and the
            members are the same either way.
    """
    distance = group.distance_feet
    advance_feet = session.battle.advance_feet
    seen = [
        advance_feet.get(member.id, 0)
        for member in session.party.living_members()
        if not has_condition(member, Condition.INVISIBLE)
    ]
    if not seen:
        return min(distance, max(distance - rate, MELEE_RANGE_FEET))
    nearest = min(seen, key=lambda advance: (abs(distance - advance), advance > distance))
    if abs(distance - nearest) <= MELEE_RANGE_FEET:
        return distance
    if nearest < distance:
        return max(distance - rate, nearest + MELEE_RANGE_FEET)
    return min(distance + rate, nearest - MELEE_RANGE_FEET)


def _place_closing_group(session, group, arrival: int) -> list[Event]:
    """Put a closing group at `arrival`, its new distance from the party line, and report the move.

    A group's distance never goes below zero. When `arrival` is behind the line, the line first moves back by the
    shortfall, as `_move_line_back` moves it: every member's advance and every group's distance grow by the same
    number of feet, with no event, so no distance between a member and a group changes. The group then stands on
    the line. A `GroupMovedEvent` reports the move when the group's distance changes. The call takes no draw.

    Returns:
        The `GroupMovedEvent`, or nothing when the group stays where it is.
    """
    if arrival < 0:
        _shift_line_back(session, -arrival)
        arrival = 0
    if arrival == group.distance_feet:
        return []
    group.distance_feet = arrival
    return [GroupMovedEvent(group_id=group.id, distance_feet=arrival)]


def _seen_after_close(session, group, placement: _Placement, rate: int) -> list:
    """The members a group's melee would reach once its `close` at `rate` is made, less the invisible ones.

    The default policies aim a melee attack that follows a `close` at one of these. It reads state and takes no
    draw.
    """
    arrival = _closing_distance(session, group, rate)
    moved = placement._replace(group_offset_feet=placement.group_offset_feet + arrival - group.distance_feet)
    return _seen(session, _reachable_front_rank(group, moved))


def _ranks_toward(group, placement: _Placement) -> list[list[str]]:
    """The party's ranks toward one group: the living members sorted by distance and cut into rows.

    The distance is how far apart a member and the group stand, as `_Placement.distance` measures it, so a member
    the group has passed ranks by how far behind it they are. Members at the same distance stand in the order they
    got there, earliest first, and then in marching order. A row holds at most `placement.width` members and never
    mixes distances. With no width limit, a row holds every member at its distance. Each row lists its members in
    marching order. The sort key is total, so the rows are the same for the same state.

    Returns:
        The rows, nearest first, as lists of member ids. Before any member has moved, these are the
            marching-order ranks.
    """
    index = {member_id: position for position, member_id in enumerate(placement.order)}
    nearest_first = sorted(
        placement.order,
        key=lambda member_id: (
            placement.distance(member_id, group),
            placement.arrived_round.get(member_id, 0),
            index[member_id],
        ),
    )
    rows: list[list[str]] = []
    row_distance = None
    for member_id in nearest_first:
        distance = placement.distance(member_id, group)
        full = placement.width is not None and bool(rows) and len(rows[-1]) >= placement.width
        if not rows or distance != row_distance or full:
            rows.append([])
            row_distance = distance
        rows[-1].append(member_id)
    return [sorted(row, key=index.__getitem__) for row in rows]


def _reachable_front_rank(group, placement: _Placement) -> list[str]:
    """The first row toward the group when it stands within melee reach, else nobody, in marching order.

    A row never mixes distances, so the whole first row is within reach or none of it is.
    """
    rows = _ranks_toward(group, placement)
    if not rows or placement.distance(rows[0][0], group) > MELEE_RANGE_FEET:
        return []
    return rows[0]


def _melee_rejection(member_id: str, group, placement: _Placement) -> Rejection | None:
    """Return why a member can't fight a group hand to hand where `placement` puts them, or None when they can.

    A member farther than 5' from the group gets `combat.attack.out_of_reach`, with that distance as
    `distance_feet`. A member within 5' who isn't in the group's front rank gets
    `battle.declaration.not_in_front_rank`, with the member's id as `character`. A melee attack and a staff
    of striking take these checks, in this order, and the melee step asks the same question when the attack
    comes up. The call reads state and takes no draw.
    """
    distance = placement.distance(member_id, group)
    if distance > MELEE_RANGE_FEET:
        return Rejection(code="combat.attack.out_of_reach", params={"distance_feet": distance})
    if member_id not in _reachable_front_rank(group, placement):
        return Rejection(code="battle.declaration.not_in_front_rank", params={"character": member_id})
    return None


def _front_rank_toward_nearest(session, placement: _Placement) -> list[str]:
    """The first row of the party's ranks toward the nearest group, at any distance, in marching order.

    The nearest group is the one that stands closest to any living member, among the groups that haven't fled and
    have a living member. When two are equally near, it's the one that comes first in the encounter's groups. If no
    group has a living member, a group that hasn't fled stands in, and then the encounter's first group. With no
    group at all, it's the first rank of the marching order. Outside a battle every member stands on the party line,
    so the result is the first rank of the marching order there too. The player view's `EncounterView.front_rank`
    reports it. The call reads state and takes no draw.
    """
    groups = session.encounter.groups
    fighting = [group for group in groups if not group.fled and _living_monsters(session, group)]
    candidates = fighting or [group for group in groups if not group.fled] or list(groups[:1])
    if not candidates:
        return [member.id for member in _party_front_rank(session)]
    nearest = min(
        candidates,
        key=lambda group: min((placement.distance(member_id, group) for member_id in placement.order), default=0),
    )
    rows = _ranks_toward(nearest, placement)
    return rows[0] if rows else []


_DEFAULT_INFRAVISION_FEET = 60
"""How far infravision sees when its data gives no range. The Hazards and Challenges page: "Infravision works within a
limited range (60' for monsters, unless specified otherwise in a monster's description)." The dwarf and the elf have
"infravision to 60'"."""


def _infravision_feet(session, member) -> int:
    """How far the member sees by infravision, in feet, or zero when they have none.

    It's the longest `range_feet` among the member's class ability tagged `infravision` and the `infravision`
    effects on them, like the spell's. A source with no range gives `_DEFAULT_INFRAVISION_FEET`. It doesn't ask
    whether darkness or blindness stops it.
    """
    ranges = [
        int(ability.params.get("range_feet", _DEFAULT_INFRAVISION_FEET))
        for ability in member.definition.abilities
        if ability.tag == "infravision"
    ]
    ranges.extend(
        int(effect.definition.params.get("range_feet", _DEFAULT_INFRAVISION_FEET))
        for effect in session.ledger.active_on(member.id, "infravision")
    )
    return max(ranges, default=0)


def _group_lit(session, group, placement: _Placement) -> bool:
    """Whether a light the party carries reaches the group, where `placement` puts the members.

    The group is lit when a living member with a light effect on them, like a burning torch, stands within that
    light's radius of the group, measured along the range track. A darkness effect that puts out the party's light,
    as `GameSession.party_light` reports it, leaves the group unlit. The call reads state and takes no draw.
    """
    lit, _ = session.party_light()
    if not lit:
        return False
    from osrlib.crawl.exploration import _light_radius_feet
    from osrlib.crawl.session import LIGHT_EFFECT_KINDS

    for member in session.party.living_members():
        for effect in session.ledger.active_on(member.id):
            if effect.definition.kind not in LIGHT_EFFECT_KINDS:
                continue
            if placement.distance(member.id, group) <= _light_radius_feet(effect.definition.params):
                return True
    return False


def _members_seeing(session, group, placement: _Placement, *, lit: bool) -> list[str]:
    """The living members who can see the group, in marching order, where `placement` puts them.

    A member sees a lit group. A member also sees a group within their infravision's range (see
    `_infravision_feet`) when no darkness effect stops infravision, as `GameSession.party_light` reports it. A blind
    member sees nothing. The call reads state and takes no draw.
    """
    _, infravision_works = session.party_light()
    seeing = []
    for member in session.party.living_members():
        if has_condition(member, Condition.BLIND):
            continue
        if lit:
            seeing.append(member.id)
            continue
        reach = _infravision_feet(session, member) if infravision_works else 0
        if reach > 0 and placement.distance(member.id, group) <= reach:
            seeing.append(member.id)
    return seeing


def _nearest_group(session, member_id: str):
    """The group nearest the member among the groups that haven't fled and have a living member.

    When two are equally near, it's the one that comes first in the encounter's groups. A battle always
    has such a group while a round runs. If none is left, the nearest group that hasn't fled stands in,
    and then the encounter's first group.
    """
    groups = session.encounter.groups
    fighting = [group for group in groups if not group.fled and _living_monsters(session, group)]
    candidates = fighting or [group for group in groups if not group.fled] or list(groups[:1])
    return min(candidates, key=lambda group: _member_distance(session, member_id, group))


def _place(state: BattleState, member_id: str, advance: int) -> None:
    """Put a member at a new advance, reached in the current round."""
    state.advance_feet[member_id] = advance
    state.arrived_round[member_id] = state.round


def _line_shift(session, advance_feet: Mapping[str, int]) -> int:
    """How far the party line moves back at the end of a movement step that leaves the members at `advance_feet`.

    It's the foremost advance among the living members who aren't invisible, when that advance is below zero,
    as a positive number of feet. It's zero when one of them stands on or forward of the line, or when every
    living member is invisible.
    """
    seen = [
        advance_feet.get(member.id, 0)
        for member in session.party.living_members()
        if not has_condition(member, Condition.INVISIBLE)
    ]
    return -max(seen) if seen and max(seen) < 0 else 0


def _move_line_back(session) -> None:
    """Move the party line back to the foremost member the monsters can see, when they all stand behind it.

    The movement step calls this at its end. When every living member who isn't invisible has an advance
    below zero, every member's advance and every group's distance grow by the foremost one's distance
    behind the line. No distance between a member and a group changes, so there's no event. When every
    living member is invisible, or one of them stands on or forward of the line, nothing changes. The call
    takes no draw.
    """
    shift = _line_shift(session, session.battle.advance_feet)
    if shift:
        _shift_line_back(session, shift)


def _shift_line_back(session, feet: int) -> None:
    """Move the party line back by `feet`: every member's advance and every group's distance grow by that much.

    No distance between a member and a group changes, so the move emits no event. The call takes no draw.
    """
    state = session.battle
    living = session.party.living_members()
    for member_id in [*state.advance_feet, *(member.id for member in living if member.id not in state.advance_feet)]:
        state.advance_feet[member_id] = state.advance_feet.get(member_id, 0) + feet
    for group in session.encounter.groups:
        group.distance_feet += feet


def member_distance_feet(session, member_id: str, group_id: str) -> int:
    """Return how far one party member stands from one monster group, in feet, in the running battle.

    Each group's distance is measured from the party line, where the party stood when the encounter
    opened or where the line has moved back to since, and each member stands at an advance forward of
    that line, in
    [`BattleState.advance_feet`][osrlib.crawl.battle.BattleState.advance_feet]. This returns the
    group's distance minus the member's advance. Every rule that measures how far a member is from a
    group uses it: melee reach, a missile's range band, and a spell's range.

    The value is negative only for a member the group has passed, which a group does only to a member
    it can't see. The rules measure such a member by the size of the value, so a member at -5 is
    within reach of the group.

    Call it from a policy of your own (see [`ActionPolicy`][osrlib.crawl.battle.ActionPolicy]) or from
    server-side code that has the session. A client that has only the player view reads the same
    numbers from
    [`EncounterGroupView.member_distance_feet`][osrlib.crawl.views.EncounterGroupView.member_distance_feet].

    The call reads the session and changes nothing.

    Args:
        session (osrlib.crawl.session.GameSession): A session in `battle` mode.
        member_id: The member's id, as in [`MemberView.id`][osrlib.crawl.views.MemberView.id].
        group_id: The group's id, as in
            [`EncounterGroupView.id`][osrlib.crawl.views.EncounterGroupView.id].

    Returns:
        The distance in feet. At [`MELEE_RANGE_FEET`][osrlib.crawl.battle.MELEE_RANGE_FEET] or less,
            the member and the group are within reach of each other.

    Raises:
        ValueError: If no battle is running, `member_id` names no party member, or `group_id` names no
            group in the encounter. Nothing in the session changes.
    """
    if session.battle is None:
        raise ValueError("member_distance_feet needs a running battle")
    if all(member.id != member_id for member in session.party.members):
        raise ValueError(f"no party member has the id {member_id!r}")
    group = _group_by_id(session, group_id)
    if group is None:
        raise ValueError(f"no group in the encounter has the id {group_id!r}")
    return _signed_distance(session, member_id, group)


def group_front_rank(session, group_id: str) -> tuple[str, ...]:
    """Return the members who can fight one monster group hand to hand this round without moving.

    These are the members in the party's front rank toward the group who stand within
    [`MELEE_RANGE_FEET`][osrlib.crawl.battle.MELEE_RANGE_FEET] of it. Only they can declare a melee
    attack on the group, or strike it with a staff of striking, without a `close` first. Only they can
    be struck by the group's melee, and the group's petrifying gaze reaches only them.

    The ranks toward a group are the party's living members sorted by their distance to it, nearest
    first, and cut into rows as wide as the formation allows
    ([`EncounterView.formation_width`][osrlib.crawl.views.EncounterView.formation_width]). A row never
    mixes distances. Among members at the same distance, the one who got there in an earlier round
    ([`BattleState.arrived_round`][osrlib.crawl.battle.BattleState.arrived_round]) stands in front, and
    marching order settles a tie. The front rank is the first row. With the ruleset's
    `formation_width_limit` flag off, a row holds every member at its distance.

    So two orders apply, one after the other. Arrival order, then marching order, decides who is in the
    front row. The returned tuple then lists the members of that row in marching order. A member who got
    there first can come after a member who arrived later, and a member who marches ahead of both can be
    left out because the row was already full.

    For the same group, the result is always the same value as
    [`EncounterGroupView.front_rank`][osrlib.crawl.views.EncounterGroupView.front_rank] in the player
    view.

    Call it from a policy of your own: the default policies pick each monster's melee target from these
    members, leaving out invisible members and members whose *protection from evil* bars that monster.
    A client that has only the player view reads the same ids from
    [`EncounterGroupView.front_rank`][osrlib.crawl.views.EncounterGroupView.front_rank].

    The call reads the session and changes nothing.

    Args:
        session (osrlib.crawl.session.GameSession): A session in `battle` mode.
        group_id: The group's id, as in
            [`EncounterGroupView.id`][osrlib.crawl.views.EncounterGroupView.id].

    Returns:
        Member ids, in marching order. It's empty when no living member stands within 5' of the group,
            for example while the nearest row is still farther away.

    Raises:
        ValueError: If no battle is running, or `group_id` names no group in the encounter. Nothing in
            the session changes.
    """
    if session.battle is None:
        raise ValueError("group_front_rank needs a running battle")
    group = _group_by_id(session, group_id)
    if group is None:
        raise ValueError(f"no group in the encounter has the id {group_id!r}")
    return tuple(_reachable_front_rank(group, _placement(session)))


class BattleRoundPreview(BaseModel):
    """What a battle round would do with the declarations planned so far, worked out without playing it.

    [`preview_battle_round`][osrlib.crawl.battle.preview_battle_round] returns one. Read `rejections`
    to grey out a choice the session would refuse, and read `advance_feet` and `front_rank` to show
    where the members would stand once their declared moves are made. The model is frozen.
    """

    model_config = ConfigDict(frozen=True)

    rejections: dict[str, tuple[Rejection, ...]]
    """Every reason the session would refuse each planned declaration, keyed by the declaring member's
    id. There's one entry for each declaration you passed. An empty tuple means the session would
    accept that declaration.

    The codes and params are the ones
    [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound] returns for the same declarations.
    `battle.declaration.roster_mismatch` never appears, because a plan with members still missing
    would always get it."""
    advance_feet: dict[str, int]
    """Where each living member would stand once the planned moves are made, keyed by member id, in feet
    forward of the party line, as in
    [`BattleState.advance_feet`][osrlib.crawl.battle.BattleState.advance_feet]. Every living member has
    an entry. A member with no planned move keeps their current advance, and a member on the line has
    zero. When the planned moves would leave every member the monsters can see behind the line, the
    values are the ones after the line moves back, as the round would leave them."""
    front_rank: dict[str, tuple[str, ...]]
    """For each group that hasn't fled, keyed by group id, the members who would stand in its front rank
    within 5' of it once the planned moves are made, in marching order. It means what
    [`EncounterGroupView.front_rank`][osrlib.crawl.views.EncounterGroupView.front_rank] means, after the
    moves."""


def preview_battle_round(session, declarations: Sequence[BattleDeclaration]) -> BattleRoundPreview:
    """Judge the declarations planned so far for this battle round, and show where they'd leave the party.

    A battle screen builds a round one member at a time. Call this after each choice, with every
    declaration made so far, to learn whether the session would accept each one and where each member
    would stand. For example, once Hild has planned to close and attack, and her place fills the front
    rank toward the goblins, the preview refuses Osric's close and attack with
    `battle.declaration.not_in_front_rank`. Your screen can then offer Osric a `close` without the
    attack. When the plan is complete, send it in a
    [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound].

    The preview runs the checks `ResolveBattleRound` runs before it plays a round, over the same
    projection of the round's declared moves, so the two agree. When `declarations` has one declaration
    for each member in [`EncounterView.declarers`][osrlib.crawl.views.EncounterView.declarers] and every
    entry in the result's `rejections` is empty, `ResolveBattleRound` accepts the same declarations.

    The projection applies each planned move as the party's movement step would: each member's
    `close`, and each `fighting_withdrawal` or `retreat` a member makes alone. A partial plan, one that
    leaves out some declarers, judges each `fighting_withdrawal` or `retreat` as that member's own move,
    even when every member in the plan makes the same one. The complete plan can turn into a formation
    move: when every declarer makes the same defensive move, the whole party moves back as one body,
    the groups move back instead of the members, and the preview of the complete plan shows that. So a
    member's projected advance can change when the last declaration is added. The order of
    `declarations` doesn't matter. The projection doesn't include anything the monsters do,
    because the session decides that after it rolls initiative. So when the monsters act first, a
    member's `close` can fall short of where the preview put them.

    The call reads the session and changes nothing. It takes no random number and no game time, so
    calling it after every choice costs the game nothing.

    Args:
        session (osrlib.crawl.session.GameSession): A session in `battle` mode.
        declarations: The declarations planned so far, at most one for each member. Each one names a
            member in `EncounterView.declarers`. Any subset of the declarers works, including none.

    Returns:
        A [`BattleRoundPreview`][osrlib.crawl.battle.BattleRoundPreview] with each declaration's
            rejections, each living member's projected advance, and each group's projected front rank.

    Raises:
        ValueError: If no battle is running, a declaration names a member who isn't in
            `EncounterView.declarers`, or two declarations name the same member. Fix the call. Nothing in
            the session changes.
    """
    if session.battle is None:
        raise ValueError("preview_battle_round needs a running battle")
    declarers = _able_declarers(session)
    declarer_ids = [member.id for member in declarers]
    seen: set[str] = set()
    for declaration in declarations:
        if declaration.character_id not in declarer_ids:
            raise ValueError(f"{declaration.character_id!r} isn't one of this round's declarers")
        if declaration.character_id in seen:
            raise ValueError(f"two declarations name {declaration.character_id!r}")
        seen.add(declaration.character_id)
    judgement = _judge_round(session, declarers, declarations)
    # Keyed in marching order, so the order of the plan never shows in the result.
    rejections = {member_id: list(judgement.rejections[member_id]) for member_id in declarer_ids if member_id in seen}
    for rejection in judgement.trade_rejections:
        rejections[str(rejection.params["character"])].append(rejection)
    projected = judgement.projected
    return BattleRoundPreview(
        rejections={member_id: tuple(found) for member_id, found in rejections.items()},
        advance_feet={member.id: projected.advance_feet.get(member.id, 0) for member in session.party.living_members()},
        front_rank={
            group.id: tuple(_reachable_front_rank(group, projected))
            for group in session.encounter.groups
            if not group.fled
        },
    )


# ---------------------------------------------------------------------- start and end


def start_battle(session, *, party_free_round: bool = False, monsters_free_round: bool = False) -> list[Event]:
    """Start a battle on the session's open encounter and put the session in `battle` mode.

    Don't call this in normal play. Execute [`EngageBattle`][osrlib.crawl.commands.EngageBattle]
    instead, and the session calls this for you. It also calls this when the monsters attack and
    when pursuing monsters catch the party. The command works out which side surprised the other,
    and the command log records it, so a replay of the log reproduces the fight. A direct call
    changes the session outside any command, so a replay doesn't reproduce it. Call this only from
    code that runs outside the session's commands, like a test of combat.

    The session needs an open encounter, because the battle uses that encounter's groups and their
    distances. When this function returns, the session is in `battle` mode and `session.battle`
    contains a [`BattleState`][osrlib.crawl.battle.BattleState]. From there, each round is one
    [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound] command.

    A group with a morale score of 2 never fights, so it flees as soon as the battle starts. That
    can end the battle before any round. When a cleric turned only part of a group, the turned
    monsters split off as a new group, which flees from the first round.

    A side that surprised the other gets one free round, in which the other side doesn't act:

    - The monsters' free round runs inside this call, before the party can act.
    - On the party's free round, the monsters don't act in the first round, which you resolve
      with the first `ResolveBattleRound`.
      [`BattleState.monsters_hold_rounds`][osrlib.crawl.battle.BattleState.monsters_hold_rounds]
      counts the rounds in which the monsters still don't act.

    Args:
        session (osrlib.crawl.session.GameSession): The running session, with `session.encounter` set.
        party_free_round: True when the monsters were surprised. The monsters then don't act in the
            first round.
        monsters_free_round: True when the party was surprised. The monsters then act once inside
            this call, before the party declares anything.

    Returns:
        The opening events: the [`BattleStartedEvent`][osrlib.crawl.events.BattleStartedEvent], a
        [`MonsterFledEvent`][osrlib.crawl.events.MonsterFledEvent] for each group that ran at once,
        the events of the monsters' free round, including the clock advancing one round, and a
        [`BattleEndedEvent`][osrlib.crawl.events.BattleEndedEvent] when the opening alone ends the
        battle.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.battle import start_battle
        from osrlib.crawl.commands import EnterDungeon, SessionMode, SpawnMonsters
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

        # The goblins were surprised, so the party gets the first round free.
        events = start_battle(session, party_free_round=True)
        assert [event.code for event in events] == ["battle.started"]
        assert session.mode is SessionMode.BATTLE
        assert session.battle.round == 0
        assert session.battle.monsters_hold_rounds == 1
        ```
    """
    state = BattleState(started_round=session.clock.rounds, monsters_hold_rounds=1 if party_free_round else 0)
    session.battle = state
    session.mode = SessionMode.BATTLE
    events: list[Event] = [BattleStartedEvent()]
    for group in session.encounter.groups:
        events.extend(_flee_if_unwilling(session, group))
    events.extend(_split_off_turned(session))
    end_events = _check_ends(session, party_retreating=False)
    if end_events is not None:
        return [*events, *end_events]
    if monsters_free_round:
        events.extend(_monster_block(session, free_round=True))
        state.round += 1
        events.extend(session.advance_rounds(1))
        end_events = _check_ends(session, party_retreating=False)
        if end_events is not None:
            events.extend(end_events)
    return events


def _flee_if_unwilling(session, group) -> list[Event]:
    """Send a group whose morale score is 2 out of the battle, because the SRD says it won't fight.

    The group runs as it enters the battle: when the battle starts, or when a referee's spawn adds
    it to a battle that's already running.
    """
    if group.fled or _group_morale_score(session, group) != 2:
        return []
    group.fled = True
    return [MonsterFledEvent(code="battle.side.fled", group_id=group.id)]


def _check_ends(session, *, party_retreating: bool) -> list[Event] | None:
    """Return the end-of-battle events when a terminal state holds, else `None`.

    Defeat ends the battle and does nothing else. Defeat holds when no member stands,
    which means every member is dead or petrified. The session's check for a lost party makes
    the transition to `game_over` and posts the ending event, so a party lost to a
    blade trap and a party lost to ogres end the same way.
    """
    from osrlib.crawl import encounter as encounter_module

    if not _anyone_standing(session.party):
        session.battle = None
        session.encounter = None
        return [BattleEndedEvent(code="battle.ended.defeat")]
    groups = session.encounter.groups
    done = all(group.fled or not _living_monsters(session, group) or _all_routed(session, group) for group in groups)
    if done:
        session.battle = None
        return [BattleEndedEvent(code="battle.ended.victory"), *encounter_module.end_encounter(session, "victory")]
    if party_retreating:
        from osrlib.crawl.encounter import PursuitState

        # The chase starts from the standing member nearest a pursuer, so read the positions before the battle
        # ends. A statue doesn't run, so it doesn't count.
        standing = [member for member in session.party.members if _stands(member)]
        distances = {
            group.id: min(_member_distance(session, member.id, group) for member in standing) for group in groups
        }
        session.battle = None
        # Only a group still willing and able to pursue: not one that failed morale and
        # not the shaken (they are fleeing themselves), and not a group whose
        # living members all lie helpless, because retreat from those succeeds.
        pursuers = [
            group
            for group in groups
            if not group.fled
            and not group.fleeing
            and not _group_all_shaken(session, group)
            and encounter_module._group_can_pursue(session, group)
        ]
        if not pursuers:
            return [BattleEndedEvent(code="battle.ended.fled"), *encounter_module.end_encounter(session, "evaded")]
        session.mode = SessionMode.ENCOUNTER
        session.encounter.evading = True
        gap = min(distances[group.id] for group in pursuers)
        session.encounter.pursuit = PursuitState(gap_feet=gap)
        return [BattleEndedEvent(code="battle.ended.fled")]
    return None


def _all_routed(session, group) -> bool:
    living = _living_monsters(session, group)
    return (
        bool(living)
        and all(
            has_condition(monster, Condition.TURNED) or has_condition(monster, Condition.AFRAID) for monster in living
        )
        and _nearest_member_distance(session, group) > FLEE_EXIT_FEET
    )


# ---------------------------------------------------------------------- declarations


def _able_declarers(session) -> list:
    return [member for member in session.party.living_members() if not incapacitated(member)]


def _find_wielded(member, weapon_id: str | None):
    """Return the wielded attack for a declaration: a mundane template or a magic instance."""
    if weapon_id is None:
        return None
    for instance in member.inventory.wielded:
        if isinstance(instance, MagicItemInstance):
            if instance.instance_id == weapon_id:
                return instance
        elif instance.template.id == weapon_id:
            return instance.template
    return None


def _with_default_weapon(session, declaration: BattleDeclaration, member, placement: _Placement) -> BattleDeclaration:
    """Fill in the weapon of an `attack` that has none, by the rules `BattleDeclaration.weapon_id` gives.

    An improvised weapon here is a mundane gear item whose stats come from its `combat` facet, like a
    torch. The round handler calls this once, when it accepts the round, so the rest of the round
    reads the chosen weapon as if the player had named it. The member's distance to the group in
    `placement` decides between a melee and a missile weapon, and a missile weapon's reach. The
    pre-phase passes the projection of the round's declared moves, so a member who closes into reach
    with a dagger strikes with it, and a member who stays back throws it.

    Returns:
        The declaration with `weapon_id` set to a magic weapon's instance id or a mundane weapon's
        catalog id. The declaration unchanged when it isn't an `attack`, already has a weapon, aims at
        a group that's gone or fled, or the member wields nothing that suits the attack.
    """
    if declaration.action != "attack" or declaration.weapon_id is not None:
        return declaration
    group = _group_by_id(session, declaration.target_group_id)
    if group is None or group.fled:
        return declaration
    distance = placement.distance(member.id, group)
    missile = distance > MELEE_RANGE_FEET
    wanted = WeaponQuality.MISSILE if missile else WeaponQuality.MELEE
    weapons, improvised = [], []
    for held in member.inventory.wielded:
        is_magic = isinstance(held, MagicItemInstance)
        facet = _declaration_facet(held if is_magic else held.template)
        qualities = getattr(facet, "qualities", ())
        if wanted in qualities and WeaponQuality.SPLASH not in qualities:
            kind = improvised if not is_magic and isinstance(held.template, GearTemplate) else weapons
            kind.append((held.instance_id if is_magic else held.template.id, facet))
    suitable = weapons + improvised
    if not suitable:
        return declaration
    chosen = suitable[0][0]
    if missile:
        for weapon_id, facet in suitable:
            ranges = getattr(facet, "missile_ranges", None)
            if ranges is None or distance <= ranges.long.max_feet:
                chosen = weapon_id
                break
    return declaration.model_copy(update={"weapon_id": chosen})


def _declaration_facet(weapon):
    """The combat stats behind a declaration: a facet, a template, or a magic base."""
    if isinstance(weapon, MagicItemInstance):
        from osrlib.core.combat import attack_facet

        return attack_facet(weapon)
    return getattr(weapon, "combat", weapon)


def _is_missile_declaration(weapon, distance_feet: int) -> bool:
    if weapon is None:
        return False
    facet = _declaration_facet(weapon)
    qualities = getattr(facet, "qualities", ())
    if WeaponQuality.MISSILE not in qualities:
        return False
    return WeaponQuality.MELEE not in qualities or distance_feet > MELEE_RANGE_FEET


def _group_by_id(session, group_id: str | None):
    if group_id is None:
        return None
    for group in session.encounter.groups:
        if group.id == group_id:
            return group
    return None


_DEFENSIVE_MOVES = ("fighting_withdrawal", "retreat")
"""The SRD's two ways out of melee, which a member makes either with the formation or alone.

When every declarer makes the same one, the whole formation makes it. Otherwise each one moves its
member back alone. `close` isn't here, because a `close` is always the declaring member's own.
"""


class _Trade(NamedTuple):
    """A trade of marching-order places that a member falling back alone asked for, planned for the movement step."""

    member_id: str
    """The member who falls back alone."""
    replacement_id: str
    """The member they named, who takes their place in the marching order."""
    move: str
    """`fighting_withdrawal` or `retreat`, which picks the event's code."""


def _declared_move(declaration: BattleDeclaration) -> str | None:
    """The move a declaration makes in the party's movement step, or None when it makes none.

    A `move` action makes its `move`, and an `attack` that sets `move="close"` makes the close before the
    attack. No other action moves its member, because the pre-phase refuses any other action that sets a
    move (see `_combination_rejection`).
    """
    if declaration.action == "move":
        return declaration.move
    if declaration.action == "attack" and declaration.move == "close":
        return "close"
    return None


def _combination_rejection(declaration: BattleDeclaration) -> Rejection | None:
    """Return why a declaration's `move` can't go with its action, or None when it can.

    A `move` action takes any move, and an `attack` takes a `close`. A `cast` that sets any move gets
    `battle.declaration.cast_cannot_move`, because the OSE SRD says "The spell caster cannot move and cast a
    spell in the same round". A `turn_undead`, a `use_item`, or a `hold` that sets any move, and an `attack`
    with a `fighting_withdrawal` or a `retreat`, get `battle.declaration.move_not_combinable`. Both have the
    member's id as `character`. The call takes no draw.
    """
    if declaration.move is None or declaration.action == "move":
        return None
    if declaration.action == "attack" and declaration.move == "close":
        return None
    if declaration.action == "cast":
        return Rejection(code="battle.declaration.cast_cannot_move", params={"character": declaration.character_id})
    return Rejection(code="battle.declaration.move_not_combinable", params={"character": declaration.character_id})


def _formation_move(declarations: Sequence[BattleDeclaration]) -> str | None:
    """Return the defensive move the whole formation makes this round, or `None` when it makes none.

    The formation makes a `fighting_withdrawal` or a `retreat` when every declaration is that same
    move, including a round with one declarer. Dead and incapacitated members don't declare, so they
    don't count. Any other defensive move is a member's move alone.
    """
    moves = [declaration.move if declaration.action == "move" else None for declaration in declarations]
    first = moves[0] if moves else None
    if first in _DEFENSIVE_MOVES and all(move == first for move in moves):
        return first
    return None


def _plan_trades(
    declarers, declarations: Sequence[BattleDeclaration], formation: str | None
) -> tuple[list[Rejection], list[_Trade]]:
    """Judge each `replacement_id`, and plan the trades of marching-order places for the movement step.

    `BattleDeclaration.replacement_id` gives the rules. A member who falls back alone names nobody or
    names one member. The named member has to be another declarer who isn't falling back too, and nobody
    earlier in marching order may have named them already, or the withdrawing member gets
    `battle.declaration.replacement_unavailable`. Their rank, their rate, their action, and whether they
    can move don't matter, because the trade moves nobody.

    In a formation move, every declarer moves with the party, so a declaration that sets
    `replacement_id` gets `battle.declaration.replacement_unavailable`. A `replacement_id` on any other
    declaration is ignored.

    A dry run passes the declarations planned so far, which can leave out some declarers. A declarer with
    no declaration yet isn't falling back, as far as the plan says, so naming one is no fault.

    The checks read state and take no draw, so a refused round costs nothing.

    Args:
        declarers: The living, able members who have to declare this round, in marching order.
        declarations: The round's declarations, at most one per declarer.
        formation: The formation move the declarations make, or None.

    Returns:
        The rejections, in marching order, and the trades, in marching order of the members who fall
            back. The trades stand only when there are no rejections.
    """
    by_id = {declaration.character_id: declaration for declaration in declarations}
    rejections: list[Rejection] = []
    if formation is not None:
        for member in declarers:
            replacement_id = by_id[member.id].replacement_id
            if replacement_id is not None:
                rejections.append(
                    Rejection(
                        code="battle.declaration.replacement_unavailable",
                        params={"character": member.id, "replacement": replacement_id},
                    )
                )
        return rejections, []
    declarer_ids = [member.id for member in declarers]
    named: set[str] = set()
    trades: list[_Trade] = []
    for member in declarers:
        declaration = by_id.get(member.id)
        move = _declared_move(declaration) if declaration is not None else None
        if declaration is None or move not in _DEFENSIVE_MOVES or declaration.replacement_id is None:
            continue
        replacement_id = declaration.replacement_id
        claimed = replacement_id in named
        named.add(replacement_id)
        named_declaration = by_id.get(replacement_id)
        if (
            claimed
            or replacement_id == member.id
            or replacement_id not in declarer_ids
            or (named_declaration is not None and _declared_move(named_declaration) in _DEFENSIVE_MOVES)
        ):
            rejections.append(
                Rejection(
                    code="battle.declaration.replacement_unavailable",
                    params={"character": member.id, "replacement": replacement_id},
                )
            )
            continue
        trades.append(_Trade(member_id=member.id, replacement_id=replacement_id, move=move))
    return rejections, trades


def _back_stab_rejection(session, declaration: BattleDeclaration, member) -> Rejection | None:
    """Return the first back-stab check a declaration fails before its action's own checks, or `None`.

    A declaration without `back_stab` passes. Otherwise the checks run in this order, and each
    rejection has the member's id as its `character` param:

    1. `battle.declaration.back_stab_not_attack`: the action isn't `attack`.
    2. `battle.declaration.back_stab_no_ability`: the member's class has no `back_stab` ability.
    3. `battle.declaration.back_stab_target_aware`: the monsters aren't held by the party's surprise,
       so [`BattleState.monsters_hold_rounds`][osrlib.crawl.battle.BattleState.monsters_hold_rounds]
       is zero.

    The pre-phase reads `monsters_hold_rounds` before the monster block of the round spends it, so a
    back-stab accepted in the free round stays a back-stab for every attack roll of that round.
    """
    if not declaration.back_stab:
        return None
    params = {"character": member.id}
    if declaration.action != "attack":
        return Rejection(code="battle.declaration.back_stab_not_attack", params=params)
    if not any(ability.tag == "back_stab" for ability in member.definition.abilities):
        return Rejection(code="battle.declaration.back_stab_no_ability", params=params)
    if session.battle.monsters_hold_rounds <= 0:
        return Rejection(code="battle.declaration.back_stab_target_aware", params=params)
    return None


def _validate_declaration(
    session, declaration: BattleDeclaration, member, *, placement: _Placement | None = None
) -> list[Rejection]:
    """Judge one declaration against the state it refers to, and return every reason it cannot stand.

    This runs in the validation pre-phase, where a rejection refuses the whole command. It runs again
    in the magic phase, just before a `cast`, a `turn_undead`, or a device or scroll `use_item`
    resolves, because the phases in between can change what these checks read. It reads state and
    takes no draw, so the second run costs nothing and changes nothing.

    `_handle_resolve_battle_round` fills in a missing `weapon_id` with `_with_default_weapon` before it
    calls this.

    The checks measure each member's distance to a group, and read the ranks toward it, from
    `placement`. The pre-phase passes the projection of every move the round declares (see
    `_projected_placement`). So an attack that closes is judged where the close will leave the member,
    and a melee attack against the ranks that every declared move, trade, and move of the party line
    will produce. Without `placement`, as in the magic phase, the checks read the battle as it stands. A
    melee attack and a staff of striking take the checks of `_melee_rejection`:
    `combat.attack.out_of_reach` for a member farther than 5' from the group, and
    `battle.declaration.not_in_front_rank` for a member within 5' who isn't in its front rank.

    A declaration that sets a `move` its action can't take gets the rejection of
    `_combination_rejection`, after the back-stab checks and before the action's own checks. An attack
    that closes, by a member who can't move, gets `battle.declaration.cannot_move` after the group and
    weapon checks.

    A `turn_undead` names no group. It reaches every living monster in every group that hasn't fled,
    undead or not, which `_party_magic` passes to [`turn_undead`][osrlib.core.spells.turn_undead].
    After the checks of [`validate_turn_undead`][osrlib.core.spells.validate_turn_undead], a turning
    with no such monster left gets `battle.declaration.no_target`, with the member's id as the
    `character` param. Only the magic phase's second run can meet that. The check doesn't ask
    whether any monster is undead, so a refusal never tells the player which monsters are undead.

    A declaration that sets `back_stab` gets the back-stab checks of `_back_stab_rejection` before
    any other check. On an attack, `battle.declaration.back_stab_not_melee` comes after the group and
    weapon checks and before the melee checks, as
    [`BattleDeclaration.back_stab`][osrlib.crawl.commands.BattleDeclaration.back_stab] gives the order.
    """
    state = session.battle
    refused = _back_stab_rejection(session, declaration, member) or _combination_rejection(declaration)
    if refused is not None:
        return [refused]
    if declaration.action == "hold":
        return []
    if declaration.action == "move":
        if declaration.move is None:
            return [Rejection(code="battle.declaration.missing_move", params={"character": member.id})]
        if declaration.move == "close":
            group = _group_by_id(session, declaration.target_group_id)
            if group is None or group.fled:
                return [Rejection(code="battle.declaration.unknown_group", params={"character": member.id})]
        if cannot_move(member):
            return [Rejection(code="battle.declaration.cannot_move", params={"character": member.id})]
        return []
    if declaration.action == "attack":
        group = _group_by_id(session, declaration.target_group_id)
        if group is None or group.fled:
            return [Rejection(code="battle.declaration.unknown_group", params={"character": member.id})]
        pool = _monster_pool(session, group)
        if not pool:
            return [Rejection(code="battle.declaration.no_target", params={"group": group.id})]
        weapon = _find_wielded(member, declaration.weapon_id)
        if declaration.weapon_id is not None and weapon is None:
            return [Rejection(code="battle.declaration.weapon_not_wielded", params={"item": declaration.weapon_id})]
        if declaration.move == "close" and cannot_move(member):
            return [Rejection(code="battle.declaration.cannot_move", params={"character": member.id})]
        placement = placement or _placement(session)
        distance = placement.distance(member.id, group)
        missile = _is_missile_declaration(weapon, distance)
        if declaration.back_stab and missile:
            return [Rejection(code="battle.declaration.back_stab_not_melee", params={"character": member.id})]
        if not missile:
            out_of_place = _melee_rejection(member.id, group, placement)
            if out_of_place is not None:
                return [out_of_place]
        context = AttackContext(
            distance_feet=distance if missile else MELEE_RANGE_FEET,
            fired_last_round=member.id in state.fired_last_round,
        )
        combat_facet = getattr(weapon, "combat", None)
        attack = combat_facet if combat_facet is not None else weapon
        return validate_attack(member, pool[0], attack, context, ruleset=session.ruleset)
    if declaration.action == "use_item":
        from osrlib.crawl import exploration

        magic = member.inventory.magic_item(declaration.item_id) if declaration.item_id else None
        if magic is not None:
            return _validate_magic_item_declaration(session, declaration, member, magic, placement=placement)
        group = _group_by_id(session, declaration.target_group_id)
        if group is None or group.fled:
            return [Rejection(code="battle.declaration.unknown_group", params={"character": member.id})]
        instance = exploration._find_item(member, declaration.item_id) if declaration.item_id else None
        if instance is None or getattr(instance.template, "combat", None) is None:
            return [Rejection(code="battle.declaration.item_unusable", params={"item": declaration.item_id or ""})]
        pool = _monster_pool(session, group)
        if not pool:
            return [Rejection(code="battle.declaration.no_target", params={"group": group.id})]
        context = AttackContext(distance_feet=_member_distance(session, member.id, group), lit=True)
        return validate_attack(member, pool[0], instance.template, context, ruleset=session.ruleset)
    if declaration.action == "turn_undead":
        rejections = validate_turn_undead(member, load_classes().get(member.class_id))
        if rejections:
            return rejections
        if not _turning_candidates(session):
            return [Rejection(code="battle.declaration.no_target", params={"character": member.id})]
        return []
    if declaration.action == "cast":
        if declaration.spell_id is None or declaration.spell_mode is None:
            return [Rejection(code="battle.declaration.missing_spell", params={"character": member.id})]
        try:
            spell = load_spells().get(declaration.spell_id)
        except ValueError:
            return [Rejection(code="magic.cast.unknown_spell", params={"spell": declaration.spell_id})]
        from osrlib.crawl import exploration

        if session.ledger.active_on(exploration._cell_ref(session), "silence"):
            return [Rejection(code="magic.cast.silenced_area", params={"caster": member.id})]
        targets, distance, rejections = _cast_targets(session, declaration, spell)
        if rejections:
            return rejections
        from osrlib.core.spells import CastContext, caster_profile

        profile = caster_profile(member.definition)
        if profile is None:
            # A member with no casting profile can never have a memorized copy.
            return [
                Rejection(code="magic.cast.not_memorized", params={"spell": spell.id, "reversed": declaration.reversed})
            ]
        return validate_cast(
            member,
            spell,
            declaration.spell_mode,
            profile=profile,
            reversed=declaration.reversed,
            targets=targets,
            context=CastContext(in_combat=True, distance_feet=distance),
            ledger=session.ledger,
        )
    return [Rejection(code="battle.declaration.unknown_action", params={"action": declaration.action})]


def _cast_targets(session, declaration: BattleDeclaration, spell) -> tuple[list, int | None, list[Rejection]]:
    """Resolve a cast declaration's targets: explicit ids, the area footprint, or a group's living members.

    An `area` mode takes the group's living members that fit the area footprint, measured from the
    caster, then the members of the group's front rank within 5' of it when `aoe_friendly_fire`
    applies (see `_area_candidates`). An `hd_budget` mode with a `target_group_id` and no
    `targets` takes the group's living members in spawn order, and the kernel spends the budget on
    them weakest first. With no area, like *sleep*'s, that's every living member, so a front end that
    has only the player view's group ids can still cast *sleep*. With an area, like *death spell*'s,
    it's the members the footprint takes. When both fields are set, the cast uses `targets`. A group
    that isn't in the encounter, or has fled, gets `battle.declaration.unknown_group`.

    The distance it returns, which the spell's range is checked against, is the caster's own distance to
    the group, or to the farthest group a targeted monster belongs to, in the battle as it stands. That's
    also the distance the pre-phase's projection gives, because a caster can't move in the round they
    cast, and neither a trade nor a move of the party line changes a member's distance to a group.
    """
    try:
        mode = spell.mode(declaration.spell_mode, reversed=declaration.reversed)
    except ValueError:
        return [], None, [Rejection(code="magic.cast.unknown_mode", params={"mode": declaration.spell_mode or ""})]
    targeting = mode.targeting
    if targeting is not None and (
        targeting.mode is TargetingMode.AREA
        or (
            targeting.mode is TargetingMode.HD_BUDGET
            and not declaration.targets
            and declaration.target_group_id is not None
        )
    ):
        group = _group_by_id(session, declaration.target_group_id)
        if group is None or group.fled:
            unknown = Rejection(code="battle.declaration.unknown_group", params={"character": declaration.character_id})
            return [], None, [unknown]
        if targeting.mode is TargetingMode.AREA or targeting.shape is not None:
            candidates = _area_candidates(
                session, group, targeting.shape, targeting.dimensions, caster_id=declaration.character_id
            )
        else:
            candidates = _living_monsters(session, group)
        return candidates, _member_distance(session, declaration.character_id, group), []
    registry = session.registry()
    targets: list = []
    distance: int | None = None
    for target_ref in declaration.targets:
        if target_ref.startswith("cell:"):
            targets.append(target_ref)
            continue
        entity = registry.get(target_ref)
        if entity is None:
            return [], None, [Rejection(code="magic.cast.unknown_target", params={"target": target_ref})]
        if has_condition(entity, Condition.INVISIBLE):
            # The player already knows the target is invisible, so this rejection reveals nothing.
            return [], None, [Rejection(code="battle.declaration.invisible_target", params={"target": target_ref})]
        targets.append(entity)
        if isinstance(entity, MonsterInstance):
            for group in session.encounter.groups:
                if target_ref in group.monster_ids:
                    distance = max(distance or 0, _member_distance(session, declaration.character_id, group))
    return targets, distance, []


def _area_span_feet(shape: str | None, dimensions: Mapping[str, Any], gap_feet: int) -> int:
    """The deterministic footprint span: diameter, length, or reach-limited length.

    A documented adaptation (see the
    [adaptations register](https://mmacy.github.io/osrlib-python/adaptations/)):
    the OSE SRD leaves how
    an area effect covers a group of creatures to the referee, so osrlib maps shape
    and dimensions to a span in feet here, deterministically.
    """
    if shape == "sphere":
        return 2 * int(dimensions.get("radius_feet", 0))
    if shape == "cube":
        return int(dimensions.get("side_feet", 0))
    if shape == "cone":
        return max(0, int(dimensions.get("length_feet", 0)) - gap_feet)
    return int(dimensions.get("length_feet", dimensions.get("side_feet", 0)))


def _area_candidates(
    session, group, shape: str | None, dimensions: Mapping[str, Any], *, caster_id: str | None = None
) -> list:
    """Fill the capacity of a party member's area aimed at a group, in stable spawn order, then add friendly fire.

    A cone's gap, the stretch it crosses before it reaches the group, is the distance from the member it comes
    from, `caster_id`, to the group. Without `caster_id`, it's the group's distance from the party line. Under the
    ruleset's `aoe_friendly_fire`, the area then catches the members of the group's front rank who stand within 5'
    of it, in marching order, while it has room (see `_reachable_front_rank`). A caster who stands back is never
    inside their own area. Outside a battle, every member stands on the line. The call reads state and takes no
    draw.
    """
    placement = _placement(session)
    gap = group.distance_feet if caster_id is None else placement.distance(caster_id, group)
    span = _area_span_feet(shape, dimensions, gap)
    capacity = 10**9 if placement.width is None else math.ceil(span / 10) * placement.width
    candidates: list = list(_living_monsters(session, group))[:capacity]
    if session.ruleset.aoe_friendly_fire:
        for member_id in _reachable_front_rank(group, placement):
            if len(candidates) >= capacity:
                break
            candidates.append(session.member(member_id))
    return candidates


def _area_rows(group, placement: _Placement, shape: str | None, dimensions: Mapping[str, Any]) -> list[str]:
    """The members an area aimed at the party from a group covers, by the footprint rule, nearest row first.

    The rows are the ranks toward the group (see `_ranks_toward`). The first row's depth is its own distance, which
    is the gap a cone crosses before it reaches the party. Each later row is at least 10' deeper than the row in
    front of it, as a cell is deep, and no shallower than its own distance, so depth never goes down. The area covers
    the rows in order from the front while a row's depth is less than the span past the first row (see
    `_area_span_feet`), and stops at the first row it doesn't reach. So a cone covers the rows that start nearer the
    group than its length, and never a farther row past a nearer one. Before any member has moved, every row stands
    at the group's distance, so the area covers `ceil(span / 10)` rows from the front, as it always has. The call
    reads state and takes no draw.

    Returns:
        Member ids, row by row, each row in marching order.
    """
    rows = _ranks_toward(group, placement)
    if not rows:
        return []
    front = placement.distance(rows[0][0], group)
    span = _area_span_feet(shape, dimensions, front)
    covered: list[str] = []
    # Seeded so that the first row's depth is the front's distance.
    depth = front - 10
    for row in rows:
        depth = max(depth + 10, placement.distance(row[0], group))
        if depth - front >= span:
            break
        covered.extend(row)
    return covered


def _validate_magic_item_declaration(
    session, declaration: BattleDeclaration, member, instance, *, placement: _Placement | None = None
) -> list[Rejection]:
    """The `use_item` declaration widened: potions, scrolls, and devices (magic phase).

    A potion targets the drinker. Wands, staves, and rods target a group and
    resolve in the magic phase alongside casts, because they unleash magical
    effects and resolving them there keeps the missile and melee ordering clean.
    A scroll read resolves in the magic phase too, through the declaration's
    spell fields.

    For a scroll or a device this runs twice, like a `cast`: in the validation pre-phase, and again in
    the magic phase just before the read or the use resolves. It takes no draw, so the second run
    costs nothing.

    A staff of striking (an effect of kind `striking`) is "Used as a magical melee weapon", so it takes a
    melee attack's checks of `_melee_rejection` against `placement`, after the fled-group check and before
    the target check. The pre-phase passes the projection of the round's declared moves, so a member who
    steps into a front rank another member leaves can strike. Without `placement`, as in the magic phase,
    the checks read the battle as it stands, so a member whose place in the front rank didn't open fizzles
    with `battle.declaration.not_in_front_rank`.
    """
    from osrlib.crawl import exploration

    template = magic_item_template(instance)
    category = template.category
    if category is MagicItemCategory.POTION:
        return []
    if category is MagicItemCategory.SCROLL:
        light_rejections = exploration._requires_light(session, member, infravision_suffices=False)
        if light_rejections:
            return light_rejections
        if template.cursed or "spell_count" not in template.params:
            return []
        remaining = tuple(str(spell) for spell in instance.state.get("spells", ()))
        if not remaining:
            return [Rejection(code="items.scroll.spent", params={"item": instance.instance_id})]
        spell_id = declaration.spell_id or remaining[0]
        if spell_id not in remaining:
            return [Rejection(code="items.scroll.no_such_spell", params={"spell": spell_id})]
        definition = load_classes().get(member.class_id)
        from osrlib.core.spells import caster_profile

        profile = caster_profile(definition)
        divine_scroll = instance.state.get("spell_list") == "cleric"
        if divine_scroll:
            if profile is None or profile.kind != "divine":
                return [Rejection(code="items.scroll.wrong_caster", params={"item": instance.instance_id})]
        elif profile is None or profile.kind != "arcane":
            thief_params = exploration._thief_scroll_use(definition)
            if thief_params is None or member.level < int(thief_params.get("min_level", 10)):
                return [Rejection(code="items.scroll.wrong_caster", params={"item": instance.instance_id})]
        spell = load_spells().get(spell_id)
        mode = declaration.spell_mode or spell.modes[0].key
        targets, distance, rejections = _cast_targets(
            session, declaration.model_copy(update={"spell_id": spell_id, "spell_mode": mode}), spell
        )
        if rejections:
            return rejections
        from osrlib.core.spells import CastContext, validate_scroll_cast

        # The kernel's pre-check, at the scroll's caster level, so a declaration that
        # passes here cannot raise out of `cast_from_scroll` in the magic phase.
        return validate_scroll_cast(
            member,
            spell,
            mode,
            targets=targets,
            context=CastContext(in_combat=True, distance_feet=distance),
            ledger=session.ledger,
        )
    if category in (MagicItemCategory.ROD, MagicItemCategory.STAFF, MagicItemCategory.WAND):
        from osrlib.core.items import usable_by_class

        definition = load_classes().get(member.class_id)
        if not usable_by_class(template, definition):
            return [Rejection(code="items.use.not_usable", params={"item": instance.instance_id})]
        if template.charges_dice is not None and (instance.charges_remaining or 0) <= 0:
            return [Rejection(code="items.device.inert", params={"item": instance.instance_id})]
        effect_spec = template.effect
        if effect_spec is not None and effect_spec.kind in ("damage_area", "condition_area", "striking"):
            group = _group_by_id(session, declaration.target_group_id)
            if group is None or group.fled:
                return [Rejection(code="battle.declaration.unknown_group", params={"character": member.id})]
            if effect_spec.kind == "striking":
                out_of_place = _melee_rejection(member.id, group, placement or _placement(session))
                if out_of_place is not None:
                    return [out_of_place]
                if not _monster_pool(session, group):
                    return [Rejection(code="battle.declaration.no_target", params={"group": group.id})]
        return []
    return [Rejection(code="battle.declaration.item_unusable", params={"item": instance.instance_id})]


def _resolve_magic_item_use(session, member, declaration: BattleDeclaration, state) -> list[Event]:
    """Resolve a magic-phase item declaration: drink, read, or activate."""
    from osrlib.crawl import exploration

    instance = member.inventory.magic_item(declaration.item_id)
    if instance is None:
        return []
    template = magic_item_template(instance)
    category = template.category
    events: list[Event] = []
    if category is MagicItemCategory.POTION:
        _, events = exploration._use_potion(session, member, instance, template)
        return events
    if category is MagicItemCategory.SCROLL:
        if template.cursed or template.effect is not None or "spell_count" not in template.params:
            command_like = _ScrollFields(
                spell_id=declaration.spell_id, mode=declaration.spell_mode, targets=declaration.targets, target_id=None
            )
            _, events = exploration._use_scroll(session, member, instance, template, command_like)
            return events
        return _resolve_scroll_cast(session, member, instance, template, declaration)
    if category in (MagicItemCategory.ROD, MagicItemCategory.STAFF, MagicItemCategory.WAND):
        effect_spec = template.effect
        events.extend(exploration._identify_item_events(session, member, instance))
        from osrlib.crawl.events import ItemUsedEvent

        events.append(
            ItemUsedEvent(
                code="items.device.activated",
                character_id=member.id,
                instance_id=instance.instance_id,
                manual=template.manual if effect_spec is None else (),
            )
        )
        if effect_spec is not None and effect_spec.kind == "striking":
            events.extend(_resolve_striking(session, member, instance, declaration))
        elif effect_spec is not None and effect_spec.kind == "healing":
            events.extend(_resolve_device_healing(session, member, instance, template, declaration))
        elif effect_spec is not None and effect_spec.kind in ("damage_area", "condition_area"):
            group = _group_by_id(session, declaration.target_group_id)
            if group is not None and not group.fled:
                events.extend(exploration._device_area_events(session, member, instance, template, group))
        exploration._spend_device_charge(instance, template)
        return events
    return events


class _ScrollFields:
    """A duck-typed stand-in with the fields of `UseItem`, for the exploration scroll reader."""

    def __init__(self, *, spell_id, mode, targets, target_id) -> None:
        self.spell_id = spell_id
        self.mode = mode
        self.targets = targets
        self.target_id = target_id


def _resolve_scroll_cast(session, member, instance, template, declaration: BattleDeclaration) -> list[Event]:
    """Read one spell off a scroll in the magic phase, at the scroll's caster level.

    The declaration is judged again first, before anything is spent, and a refused one still spends the
    scroll: the read itself is emitted and the spell struck off, and only then is the fizzle reported,
    because the reader did read it and the scroll is used up either way. A thief reading an arcane scroll rolls
    the printed error chance after that, and a failed roll burns the spell with nothing to report: that
    miscast is a failure of the scroll, not a refused declaration, so it emits no `magic.cast.fizzled` event.
    """
    from osrlib.core.spells import CastContext, cast_from_scroll
    from osrlib.crawl import exploration
    from osrlib.crawl.events import ItemUsedEvent

    remaining = tuple(str(spell) for spell in instance.state.get("spells", ()))
    if not remaining:
        return []
    spell_id = declaration.spell_id or remaining[0]
    spell = load_spells().get(spell_id)
    mode = declaration.spell_mode or spell.modes[0].key
    # The declaration is judged again, with the checks it passed at the top of the round,
    # because the phases before this one can change what those checks read (see the
    # adaptations register). The scroll is spent afterwards either way: the reader read it.
    rejections = _validate_magic_item_declaration(session, declaration, member, instance)
    left = tuple(spell_name for spell_name in remaining if spell_name != spell_id) + tuple(
        spell_id for _ in range(remaining.count(spell_id) - 1)
    )
    if left:
        instance.state = {**instance.state, "spells": left}
    else:
        exploration._remove_instance(member, instance)
    events: list[Event] = []
    events.extend(exploration._identify_item_events(session, member, instance))
    events.append(ItemUsedEvent(code="items.scroll.read", character_id=member.id, instance_id=instance.instance_id))
    if rejections:
        events.append(_fizzle_event(member.id, spell_id, reversed=declaration.reversed, reason=rejections[0].code))
        return events
    targets, distance, _ = _cast_targets(
        session, declaration.model_copy(update={"spell_id": spell_id, "spell_mode": mode}), spell
    )
    definition = load_classes().get(member.class_id)
    from osrlib.core.spells import caster_profile

    profile = caster_profile(definition)
    if (profile is None or profile.kind != "arcane") and instance.state.get("spell_list") != "cleric":
        thief_params = exploration._thief_scroll_use(definition)
        error_pct = int(thief_params.get("error_pct", 10)) if thief_params else 10
        if session.streams.get(MAGIC_STREAM).randbelow(100) + 1 <= error_pct:
            return events
    result = cast_from_scroll(
        member,
        spell,
        mode,
        targets=targets,
        context=CastContext(in_combat=True, distance_feet=distance),
        ledger=session.ledger,
        clock=session.clock,
        allocator=session.allocator,
        registry=session.registry(),
        ruleset=session.ruleset,
        stream=session.streams.get(MAGIC_STREAM),
        effects_stream=session.streams.get(EFFECTS_STREAM),
    )
    events.extend(result.events)
    return events


def _resolve_striking(session, member, instance, declaration: BattleDeclaration) -> list[Event]:
    """The staff of striking: a melee attack spending one charge for 2d6 (RAW)."""
    from osrlib.core.combat import DamageSource, attack_roll, deal_damage

    group = _group_by_id(session, declaration.target_group_id)
    if group is None or group.fled:
        return []
    pool = _monster_pool(session, group)
    if not pool:
        return []
    target = pool[0]
    template = magic_item_template(instance)
    stream = session.streams.get(COMBAT_STREAM)
    context = AttackContext(
        distance_feet=MELEE_RANGE_FEET, situational_modifier=_gaze_stance_modifier(member, declaration)
    )
    rolled = attack_roll(member, target, instance, context=context, ruleset=session.ruleset, stream=stream)
    events = list(rolled.events)
    if rolled.hit and template.effect is not None:
        result = roll(str(template.effect.damage_dice), stream)
        source = DamageSource(keys=("magic",), magical=True, kind="device")
        events.extend(
            deal_damage(
                target,
                result.total,
                source=source,
                attacker_id=member.id,
                rolls=result.rolls,
                clock=session.clock,
                ruleset=session.ruleset,
                stream=stream,
            )
        )
    return events


def _resolve_device_healing(session, member, instance, template, declaration: BattleDeclaration) -> list[Event]:
    from osrlib.core.combat import apply_healing

    target_ref = declaration.targets[0] if declaration.targets else member.id
    target = session.registry().get(target_ref)
    if target is None:
        return []
    day_key = f"healed:{target_ref}"
    today = session.clock.days
    if template.effect.params.get("once_per_target_per_day") and instance.state.get(day_key) == today:
        return []
    instance.state = {**instance.state, day_key: today}
    amount = roll(str(template.effect.heal_dice), session.streams.get(MAGIC_STREAM)).total
    return apply_healing(target, amount, source="magical")


# ---------------------------------------------------------------------- the round


class _Judgement(NamedTuple):
    """The validation pre-phase's verdict on a round's declarations, which `_judge_round` returns."""

    by_member: dict[str, tuple[Any, BattleDeclaration]]
    """Each declaring member and their declaration with its default weapon chosen, keyed by member id, in the
    order the declarations came."""
    rejections: dict[str, list[Rejection]]
    """Each declaration's own rejections, keyed by member id, in the order the declarations came."""
    trade_rejections: list[Rejection]
    """The rejections of the trades of marching-order places, in marching order. Each has the withdrawing
    member's id as `character`."""
    trades: list[_Trade]
    """The trades of marching-order places the movement step makes."""
    formation: str | None
    """The formation move the declarations make, or None."""
    projected: _Placement
    """Where the members would stand once the declared moves are made (see `_projected_placement`)."""


def _judge_round(session, declarers, declarations: Sequence[BattleDeclaration]) -> _Judgement:
    """Run the validation pre-phase's checks on a round's declarations, without the roster check.

    The round and its dry run both call this, so they judge the same declarations the same way. The
    declarations can leave out some declarers, as a dry run's plan does. A defensive move is a formation
    move only when the declarations cover every declarer and all of them make it.

    It plans the trades (see `_plan_trades`), projects every declared move (see `_projected_placement`),
    chooses each `attack`'s default weapon at the member's projected distance (see
    `_with_default_weapon`), and judges each declaration against the projection (see
    `_validate_declaration`), with the mirror check of `gaze_defense`. A declaration that sets
    `back_stab` on any action but `attack` gets `battle.declaration.back_stab_not_attack` in place of its
    trade checks.

    The call reads state, changes nothing, and takes no draw.

    Args:
        session: A session with a running battle.
        declarers: The living, able members who have to declare this round, in marching order.
        declarations: The declarations, at most one per declarer.
    """
    declared = {declaration.character_id: declaration for declaration in declarations}
    complete = sorted(declared) == sorted(member.id for member in declarers)
    formation = _formation_move(declarations) if complete else None
    trade_rejections, trades = _plan_trades(declarers, declarations, formation)
    projected = _projected_placement(session, declared, trades, formation)
    by_member: dict[str, tuple[Any, BattleDeclaration]] = {}
    rejections: dict[str, list[Rejection]] = {}
    for declaration in declarations:
        member = session.member(declaration.character_id)
        # Choose the default weapon once, here, so the round plays out as if the player had named it.
        # The command log still records the declaration as it was sent.
        chosen = _with_default_weapon(session, declaration, member, projected)
        by_member[member.id] = (member, chosen)
        found = list(_validate_declaration(session, chosen, member, placement=projected))
        if chosen.gaze_defense == "mirror" and member.inventory.carried_item(_MIRROR_ITEM_ID) is None:
            found.append(Rejection(code="battle.declaration.no_mirror", params={"character": member.id}))
        rejections[member.id] = found
    # A move that sets `back_stab` gets `back_stab_not_attack` in place of its own trade checks.
    back_stabbers = {
        member.id
        for member, declaration in by_member.values()
        if declaration.back_stab and declaration.action != "attack"
    }
    return _Judgement(
        by_member=by_member,
        rejections=rejections,
        trade_rejections=[
            rejection for rejection in trade_rejections if rejection.params.get("character") not in back_stabbers
        ],
        trades=trades,
        formation=formation,
        projected=projected,
    )


def _closing_melee(session, by_member, standing: _Placement, projected: _Placement) -> frozenset[str]:
    """The members whose `attack` closes from beyond 5' to within 5', so the pre-phase judged it a melee attack.

    `standing` is the battle as it stands at acceptance, and `projected` the battle once the declared
    moves are made. Such an attack stays a melee attack wherever the member ends up. When the monsters
    act first and the group runs, the member's close falls short, and the attack fizzles instead of
    turning a dagger's stab into a throw the player never declared. A member who already stands within
    5' doesn't move, so their attack is treated as an attack without a close.
    """
    closing = []
    for member, declaration in by_member.values():
        if declaration.action != "attack" or declaration.move != "close":
            continue
        group = _group_by_id(session, declaration.target_group_id)
        if group is None:
            continue
        reached = projected.distance(member.id, group)
        weapon = _find_wielded(member, declaration.weapon_id)
        if standing.distance(member.id, group) > MELEE_RANGE_FEET >= reached and not _is_missile_declaration(
            weapon, reached
        ):
            closing.append(member.id)
    return frozenset(closing)


def _handle_resolve_battle_round(session, command: ResolveBattleRound) -> tuple[list[Rejection], list[Event]]:
    state = session.battle
    if state is None:
        return [Rejection(code="battle.none_active")], []
    declarers = _able_declarers(session)
    declared_ids = [declaration.character_id for declaration in command.declarations]
    if sorted(declared_ids) != sorted(member.id for member in declarers):
        mismatch = Rejection(
            code="battle.declaration.roster_mismatch",
            params={"expected": tuple(member.id for member in declarers), "declared": tuple(declared_ids)},
        )
        return [mismatch], []
    standing = _placement(session)
    judgement = _judge_round(session, declarers, command.declarations)
    rejections = [
        *(rejection for found in judgement.rejections.values() for rejection in found),
        *judgement.trade_rejections,
    ]
    if rejections:
        # The session rejects the whole command and lists every rejection. Partial
        # acceptance would complicate replay (see the adaptations register).
        return rejections, []
    by_member = judgement.by_member
    declarations = [declaration for _, declaration in by_member.values()]
    trades = judgement.trades
    formation = judgement.formation

    # The gaze stances last from here to the end of the round, whichever side acts first.
    averting = _avert_eyes(by_member)
    mirrored = frozenset(
        member.id for member, declaration in by_member.values() if declaration.gaze_defense == "mirror"
    )
    state.round += 1
    events: list[Event] = [BattleRoundEvent(round=state.round)]

    # Declarations post: spells are table-visible per RAW.
    pending_casters: dict[str, object] = {}
    for member, declaration in by_member.values():
        # The pre-phase refused any cast that names no spell.
        if declaration.action == "cast" and declaration.spell_id is not None:
            pending_casters[member.id] = declaration
            events.append(
                SpellDeclaredEvent(caster_id=member.id, spell_id=declaration.spell_id, reversed=declaration.reversed)
            )
    # NPC sides choose at declaration time: their casts post and are disruptable
    # exactly like the party's (see the adaptations register). The policy draw
    # moves to the top of the round, still on the monster_action stream.
    npc_actions = _declare_npc_actions(session, state, pending_casters, events)

    # Initiative: side blocks, party versus the monster side.
    participants = []
    for member, declaration in by_member.values():
        weapon = _find_wielded(member, declaration.weapon_id) if declaration.action == "attack" else None
        facet = _declaration_facet(weapon)
        slow = facet is not None and WeaponQuality.SLOW in getattr(facet, "qualities", ())
        from osrlib.core.combat import participant_modifier

        participants.append(Participant(key=member.id, side="party", slow=slow, modifier=participant_modifier(member)))
    active_groups = [group for group in session.encounter.groups if not group.fled]
    for group in active_groups:
        participants.append(Participant(key=group.id, side="monsters", modifier=0))
    initiative = roll_initiative(participants, ruleset=session.ruleset, stream=session.streams.get(COMBAT_STREAM))
    events.extend(initiative.events)
    party_first = _party_acts_first(initiative, by_member)

    disrupted: set[str] = set()
    acted: set[str] = set()
    fired_this_round: list[str] = []
    fire_damaged_groups: set[str] = set()
    retreating = _retreating_members(session, declarations, formation)
    # A formation retreat stays within each group's reach all round, from where the party stood at acceptance.
    retreat_reach = (
        {group.id: frozenset(_reachable_front_rank(group, standing)) for group in session.encounter.groups}
        if formation == "retreat"
        else {}
    )
    engaged = _engaged_at_acceptance(session, by_member, standing)
    closing = _closing_melee(session, by_member, standing, judgement.projected)
    # Each slow attack, with whether it's a missile attack. The party's attack steps fill it.
    slow_attacks: list[tuple[Creature, BattleDeclaration, bool]] = []

    def party_block() -> list[Event]:
        block: list[Event] = []
        block.extend(_party_movement(session, by_member, trades, formation))
        block.extend(
            _party_attacks(
                session,
                by_member,
                missile=True,
                slow_attacks=slow_attacks,
                fired=fired_this_round,
                fire_damaged=fire_damaged_groups,
                engaged=engaged,
                closing=closing,
            )
        )
        block.extend(_party_magic(session, by_member, pending_casters, disrupted, acted, state))
        block.extend(
            _party_attacks(
                session,
                by_member,
                missile=False,
                slow_attacks=slow_attacks,
                fired=fired_this_round,
                fire_damaged=fire_damaged_groups,
                engaged=engaged,
                closing=closing,
            )
        )
        block.extend(_confused_party_overrides(session, by_member, fire_damaged_groups))
        # Party hits and failed saves disrupt declared NPC casters too (the RAW
        # trigger applies to either side).
        _watch_disruption(block, pending_casters, disrupted, acted)
        return block

    def monster_block() -> list[Event]:
        if state.monsters_hold_rounds > 0:
            state.monsters_hold_rounds -= 1
            return []
        return _monster_block(
            session,
            free_round=False,
            pending_casters=pending_casters,
            disrupted=disrupted,
            acted=acted,
            fire_damaged=fire_damaged_groups,
            retreating=retreating,
            retreat_reach=retreat_reach,
            npc_actions=npc_actions,
            mirrored=mirrored,
        )

    blocks = (party_block, monster_block) if party_first else (monster_block, party_block)
    for block in blocks:
        if session.battle is None:
            break
        events.extend(block())
        end = _check_ends(session, party_retreating=False)
        if end is not None:
            events.extend(end)
            break

    # Slow-weapon actors act last, after both sides' blocks (see the adaptations register).
    if session.battle is not None:
        for member, declaration, missile in slow_attacks:
            if incapacitated(member):
                continue
            # A slow melee attack faces the melee step's check again, where the member stands now.
            group = _group_by_id(session, declaration.target_group_id)
            fizzled = (
                None if missile or group is None else _melee_fizzle(session, declaration.character_id, group, engaged)
            )
            if fizzled is not None:
                events.append(fizzled)
                continue
            events.extend(_resolve_party_attack(session, member, declaration, fired_this_round, fire_damaged_groups))
        end = _check_ends(session, party_retreating=False)
        if end is not None:
            events.extend(end)

    if session.battle is not None:
        state.fired_last_round = fired_this_round
        events.extend(session.advance_rounds(1))
        end = _check_ends(session, party_retreating=formation == "retreat")
        if end is not None:
            events.extend(end)
    _end_averting(averting)
    return [], events


def _party_acts_first(initiative, by_member) -> bool:
    for key in initiative.order:
        if key in by_member:
            return True
        return False
    return True


def _retreating_members(session, declarations: Sequence[BattleDeclaration], formation: str | None) -> frozenset[str]:
    """Return the ids of the party members a retreat exposes for the whole round.

    The OSE SRD's retreat lasts the round: "This round: ... the opponent gains a +2 bonus to all
    attacks against the retreating combatant and ignores any AC bonus due to the combatant's shield".
    The exposure starts when the round is declared, so it also applies to a side that acts before the
    party moves. A formation retreat exposes every member. Otherwise only the members who retreat
    alone are exposed.
    """
    if formation == "retreat":
        return frozenset(member.id for member in session.party.members if member.id is not None)
    return frozenset(
        declaration.character_id
        for declaration in declarations
        if declaration.action == "move" and declaration.move == "retreat"
    )


def _engaged_at_acceptance(session, by_member, placement: _Placement) -> frozenset[str]:
    """The members whose `attack` aims at a group within 5' of them when the round is accepted.

    `placement` is the battle as it stands at acceptance, before any of the round's moves.

    The melee step lets such a member strike a group that runs, wherever it has run since, because the
    SRD gives the attack against a retreating combatant to the opponent it was fighting.
    """
    engaged = []
    for member, declaration in by_member.values():
        if declaration.action != "attack":
            continue
        group = _group_by_id(session, declaration.target_group_id)
        if group is not None and placement.distance(member.id, group) <= MELEE_RANGE_FEET:
            engaged.append(member.id)
    return frozenset(engaged)


def _party_movement(session, by_member, trades: Sequence[_Trade], formation: str | None) -> list[Event]:
    """Run the party's movement step: the formation's move, or each member's own move.

    A defensive move that every declarer makes moves the whole formation (see the
    [adaptations register](https://mmacy.github.io/osrlib-python/adaptations/), under the battle
    round). Every group's distance grows by `_formation_feet`, and no member's advance changes. A
    formation retreat moves the party off at the OSE SRD's "full encounter movement rate", and the
    pursuit begins once the battle ends.

    Otherwise each member moves alone, as `_plan_member_moves` plans it from the battle as it stands
    when the step comes: first the members who fall back, then the members who close. Either way, the
    step ends by moving the party line back when every member the monsters can see stands behind it
    (see `_move_line_back`).

    The step takes no draw.

    Returns:
        The step's events.
    """
    events: list[Event] = []
    if formation is not None:
        rate = _formation_feet(session, formation)
        for group in session.encounter.groups:
            if group.fled:
                continue
            group.distance_feet += rate
            events.append(GroupMovedEvent(group_id=group.id, distance_feet=group.distance_feet))
    else:
        declared = {member.id: declaration for member, declaration in by_member.values()}
        events = _members_move(session, _plan_member_moves(session, declared, trades))
    _move_line_back(session)
    return events


def _formation_feet(session, formation: str) -> int:
    """How far a formation move takes the party back, in feet.

    The party moves as one body at the slowest living member's encounter rate, halved for a fighting
    withdrawal, and doubled by *haste* only when every living member has it (see
    `_party_move_multiplier`). It reads state and takes no draw.
    """
    slowest = min((_encounter_rate(member, session) for member in session.party.living_members()), default=0)
    return (slowest if formation == "retreat" else slowest // 2) * _party_move_multiplier(session)


def _fall_back_feet(session, member, move: str) -> int:
    """How far a member falls back alone, in feet.

    It's half their encounter rate for a fighting withdrawal, or their full rate for a retreat, doubled by
    *haste* on them. The SRD's rates are "The combatant moves backwards at up to half their encounter
    movement rate" and "moving up to their full encounter movement rate".
    """
    rate = _encounter_rate(member, session)
    return (rate if move == "retreat" else rate // 2) * _haste_multiplier(session, member.id, "movement_multiplier")


def _close_advance(session, member, group, current: int) -> int:
    """The advance a member's `close` on a group reaches from `current`, by the arithmetic alone.

    The member moves their encounter rate, doubled by *haste* on them, and stops 5' from the group. They
    also stop short of 5' from every other group that hasn't fled and has a living member, so nobody passes
    a group. A close never takes a member back, so the result is never below `current`. It reads state
    and takes no draw.
    """
    rate = _encounter_rate(member, session) * _haste_multiplier(session, member.id, "movement_multiplier")
    advance = min(current + rate, group.distance_feet - MELEE_RANGE_FEET)
    for other in session.encounter.groups:
        if other is not group and not other.fled and _living_monsters(session, other):
            advance = min(advance, other.distance_feet - MELEE_RANGE_FEET)
    return max(advance, current)


class _MemberMove(NamedTuple):
    """One member's own move in the party's movement step, as `_plan_member_moves` plans it."""

    member_id: str
    """The member who moves."""
    move: str
    """`close`, `fighting_withdrawal`, or `retreat`."""
    advance_feet: int
    """The advance the member reaches."""
    group_id: str | None
    """The group a `close` moves toward, or None for a fall-back."""
    trade: _Trade | None
    """The trade of marching-order places that follows a fall-back, or None when there's none."""


def _plan_member_moves(
    session, declared: Mapping[str, BattleDeclaration], trades: Sequence[_Trade]
) -> list[_MemberMove]:
    """Plan each member's own move in the party's movement step, from the battle as it stands.

    First, in marching order, each member who makes a `fighting_withdrawal` or a `retreat` alone falls back
    as far as `_fall_back_feet` gives, below the line if need be. When they named someone in
    `replacement_id`, and the named member is alive, the two then trade places in the marching order. The
    trade moves nobody. Then, in the marching order the trades leave, each member who declares a `close`,
    with a `move` action or an `attack`, reaches the advance `_close_advance` gives.

    A member who can't move stays where they are and doesn't trade. So does a member whose rate is zero.
    A member whose close aims at a group that's gone or fled, or who already stands as near as the close
    would take them, doesn't move either.

    The movement step carries out this plan, and `_projected_placement` applies it to a copy of the
    battle. So when the party acts first, the members end the step exactly where the pre-phase judged
    their declarations. Each member's move depends only on that member and the groups, never on another
    member's move. The call reads state, changes nothing, and takes no draw.

    Returns:
        The moves, in the order the step makes them.
    """
    state = session.battle
    living = session.party.living_members()
    order = [member.id for member in living]
    by_id = {member.id: member for member in living}
    trade_of = {trade.member_id: trade for trade in trades}
    moves: list[_MemberMove] = []
    for member in living:
        declaration = declared.get(member.id)
        move = _declared_move(declaration) if declaration is not None else None
        if move is None or move not in _DEFENSIVE_MOVES or cannot_move(member):
            continue
        step = _fall_back_feet(session, member, move)
        if step <= 0:
            continue
        trade = trade_of.get(member.id)
        if trade is not None and trade.replacement_id in by_id:
            here, there = order.index(trade.member_id), order.index(trade.replacement_id)
            order[here], order[there] = order[there], order[here]
        else:
            trade = None
        advance = state.advance_feet.get(member.id, 0) - step
        moves.append(_MemberMove(member_id=member.id, move=move, advance_feet=advance, group_id=None, trade=trade))
    for member_id in order:
        member = by_id[member_id]
        declaration = declared.get(member_id)
        if declaration is None or _declared_move(declaration) != "close" or cannot_move(member):
            continue
        group = _group_by_id(session, declaration.target_group_id)
        if group is None or group.fled:
            continue
        current = state.advance_feet.get(member_id, 0)
        advance = _close_advance(session, member, group, current)
        if advance != current:
            moves.append(
                _MemberMove(member_id=member_id, move="close", advance_feet=advance, group_id=group.id, trade=None)
            )
    return moves


def _members_move(session, moves: Sequence[_MemberMove]) -> list[Event]:
    """Carry out the members' own moves that `_plan_member_moves` planned, in order, and report each one.

    Each member reaches their planned advance this round. A `MemberMovedEvent` reports each move: a close
    with the group it aims at, and a fall-back with the group nearest the member after the move (see
    `_nearest_group`). When a fall-back trades places in the marching order, the trade follows the move,
    and a `MemberWithdrewEvent` reports it.

    Returns:
        The events, in the order of the moves.
    """
    state = session.battle
    events: list[Event] = []
    for step in moves:
        _place(state, step.member_id, step.advance_feet)
        group = _group_by_id(session, step.group_id) if step.group_id is not None else None
        group = group if group is not None else _nearest_group(session, step.member_id)
        events.append(
            MemberMovedEvent(
                character_id=step.member_id,
                move=step.move,
                advance_feet=step.advance_feet,
                group_id=group.id,
                distance_feet=_signed_distance(session, step.member_id, group),
            )
        )
        trade = step.trade
        if trade is None:
            continue
        members = list(session.party.members)
        ids = [each.id for each in members]
        here, there = ids.index(trade.member_id), ids.index(trade.replacement_id)
        members[here], members[there] = members[there], members[here]
        session.party.members = members
        code = "battle.member.retreated" if trade.move == "retreat" else "battle.member.withdrew"
        events.append(MemberWithdrewEvent(code=code, character_id=trade.member_id, replacement_id=trade.replacement_id))
    return events


def _party_move_multiplier(session) -> int:
    """*Haste*'s movement multiplier applies only when every living party member is under it.

    A documented adaptation (see the
    [adaptations register](https://mmacy.github.io/osrlib-python/adaptations/)).
    """
    living = session.party.living_members()
    if not living:
        return 1
    return min(_haste_multiplier(session, member.id, "movement_multiplier") for member in living)


def _party_attacks(
    session,
    by_member,
    *,
    missile: bool,
    slow_attacks,
    fired,
    fire_damaged,
    engaged: Collection[str],
    closing: Collection[str],
) -> list[Event]:
    """Resolve the party's missile or melee attacks, and queue the slow ones for the end of the round.

    The weapon and the member's distance to the group when the step comes decide whether an attack is a
    missile or a melee attack. The exception is a member in `closing`, whose attack the pre-phase judged
    a melee attack after a close that takes them within 5' (see `_closing_melee`). Their attack stays a
    melee attack wherever they end up.

    A melee attack resolves only if the group still has a living, visible monster in its reachable rank to
    strike, and the member then stands in the group's front rank within 5' of it, as `_melee_rejection`
    judges it, or the group runs and the member is in `engaged`, within 5' of it when the round was
    accepted (see `_melee_fizzle`). Otherwise the member makes no attack, and a player-visible
    `DeclarationFizzledEvent` at `battle.melee.fizzled` says why, with the group's id and the rejection
    code as its reason. That happens to a member whose group the party's missiles or spells killed earlier
    in the round, to a member whose close fell short because the group ran first, and to a member the
    pre-phase expected to step into a front rank that someone who couldn't move still holds.

    A slow attack passes these checks here and goes into `slow_attacks` with whether it's a missile attack.
    At the end of the round, a slow melee attack faces the same checks again where the member then stands.
    """
    events: list[Event] = []
    for member, declaration in by_member.values():
        if declaration.action != "attack" and not (declaration.action == "use_item" and missile):
            continue
        if incapacitated(member) or has_condition(member, Condition.CONFUSED):
            continue
        if declaration.action == "use_item":
            if member.inventory.magic_item(declaration.item_id or "") is not None:
                continue  # magic items resolve in the magic phase (see the adaptations register)
            events.extend(_resolve_use_item(session, member, declaration, fire_damaged))
            continue
        group = _group_by_id(session, declaration.target_group_id)
        if group is None:
            continue
        weapon = _find_wielded(member, declaration.weapon_id)
        is_missile = member.id not in closing and _is_missile_declaration(
            weapon, _member_distance(session, member.id, group)
        )
        if is_missile != missile:
            continue
        fizzled = None if is_missile else _melee_fizzle(session, member.id, group, engaged)
        if fizzled is not None:
            events.append(fizzled)
            continue
        combat_facet = getattr(weapon, "combat", None)
        facet = combat_facet if combat_facet is not None else weapon
        if facet is not None and WeaponQuality.SLOW in getattr(facet, "qualities", ()):
            slow_attacks.append((member, declaration, is_missile))
            continue
        events.extend(_resolve_party_attack(session, member, declaration, fired, fire_damaged))
    return events


def _melee_fizzle(session, member_id: str, group, engaged: Collection[str]) -> DeclarationFizzledEvent | None:
    """Return the fizzle of a declared melee attack that can't resolve where the member stands now, or None.

    The fizzle is a player-visible `DeclarationFizzledEvent` at `battle.melee.fizzled`, with the group's id
    and a reason. The checks come in the order the pre-phase makes them:

    - `battle.declaration.no_target` when the group has no living, visible monster left in its reachable
      rank, the same test the pre-phase refuses an attack with. The member gets it wherever they stand.
    - Otherwise the rejection code `_melee_rejection` gives for the battle as it stands:
      `combat.attack.out_of_reach` or `battle.declaration.not_in_front_rank`. A group that runs is the
      exception, whether it failed morale or its living members are all turned or afraid. A member in
      `engaged`, who stood within 5' of it when the round was accepted, still strikes it.

    The call reads state and takes no draw.
    """
    if (group.fleeing or _group_all_shaken(session, group)) and member_id in engaged:
        return None
    stopped = _melee_rejection(member_id, group, _placement(session))
    if stopped is None:
        return None
    return DeclarationFizzledEvent(
        code="battle.melee.fizzled", actor_id=member_id, reason=stopped.code, group_id=group.id
    )


def _resolve_party_attack(session, member, declaration, fired, fire_damaged) -> list[Event]:
    state = session.battle
    group = _group_by_id(session, declaration.target_group_id)
    if group is None or group.fled:
        return []
    pool = _monster_pool(session, group)
    if not pool:
        return []
    weapon = _find_wielded(member, declaration.weapon_id)
    combat_facet = getattr(weapon, "combat", None)
    attack = combat_facet if combat_facet is not None else weapon
    distance = _member_distance(session, member.id, group)
    missile = _is_missile_declaration(weapon, distance)
    events: list[Event] = []
    swings = _haste_multiplier(session, member.id, "attacks_multiplier")
    for _ in range(swings):
        pool = _monster_pool(session, group)
        if not pool:
            break
        target = pool[0]  # the first living, visible monster in the reachable rank
        context = AttackContext(
            distance_feet=distance if missile else MELEE_RANGE_FEET,
            situational_modifier=_gaze_stance_modifier(member, declaration),
            fired_last_round=member.id in state.fired_last_round,
            defender_retreating=group.fleeing,
            # An accepted back-stab stays one for every swing of the round, after the held turn too.
            behind_target=declaration.back_stab,
            target_unaware=declaration.back_stab,
        )
        result = resolve_attack(
            member,
            target,
            attack,
            context=context,
            ruleset=session.ruleset,
            stream=session.streams.get(COMBAT_STREAM),
            clock=session.clock,
        )
        events.extend(result.events)
        _note_fire(result.events, group, fire_damaged)
        if isinstance(weapon, MagicItemInstance) and result.attack_roll.hit and not result.absorbed:
            events.extend(_on_hit_drain(session, weapon, target))
        if not missile:
            # Engaging in melee breaks the *protection from evil* ban against the
            # creature actually fought (a RAW clause, and the modifiers persist).
            engagements = state.melee_engagements.setdefault(member.id, [])
            if target.id not in engagements:
                engagements.append(target.id)
            # Attacking a warded monster in melee breaks the whole circle (RAW).
            events.extend(_break_party_wards(session, target))
    if isinstance(weapon, MagicItemInstance):
        from osrlib.crawl import exploration

        # The first attack roll with an enchanted arm identifies it, and reveals
        # a curse, which sticks (see the adaptations register).
        events.extend(exploration._identify_item_events(session, member, weapon))
    if missile and weapon is not None:
        facet = _declaration_facet(weapon)
        if WeaponQuality.RELOAD in getattr(facet, "qualities", ()):
            fired.append(member.id)
    events.extend(_break_invisibility(session, member))
    return events


def _on_hit_drain(session, weapon: MagicItemInstance, target) -> list[Event]:
    """The energy-drain sword's on-hit drain: automatic on every hit.

    The sword has 1d4+4 drains in total, rolled when it is generated, and once those
    are spent it is a plain +1. The lost-die rolls come from the advancement stream,
    because a drain reverses advancement. The sword's entry in the OSE SRD
    sets XP to the lowest amount for the new level (`level_minimum`), where the
    wight's entry sets it halfway.
    """
    template = magic_item_template(weapon)
    if template.effect is None or template.effect.kind != "on_hit_drain":
        return []
    remaining = _int_param(weapon.state, "drains_remaining")
    if remaining <= 0:
        return []
    weapon.state = {**weapon.state, "drains_remaining": remaining - 1}
    from osrlib.core.character import ADVANCEMENT_STREAM

    stream = session.streams.get(ADVANCEMENT_STREAM)
    levels = _int_param(template.effect.params, "levels", 1)
    if getattr(target, "definition", None) is not None:
        from osrlib.core.classes import drain_levels

        result = drain_levels(
            target,
            load_classes().get(target.class_id),
            levels=levels,
            xp_policy=str(template.effect.params.get("xp_policy", "level_minimum")),
            stream=stream,
        )
        return list(result.events)
    from osrlib.core.combat import drain_monster_hd

    return drain_monster_hd(target, levels=levels, stream=stream)


def _party_wards(session) -> list:
    """Every live party-wide protection ward, in marching-then-attachment order."""
    return [
        effect
        for member in session.party.living_members()
        for effect in session.ledger.active_on(member.id, "protection_ward")
    ]


def _ward_bars_monster(session, monster) -> bool:
    """Whether a protection-scroll ward bars a monster from initiating melee.

    A ward bars matching monsters up to its rolled per-HD-band count (the 1-3,
    4-5, and 6+ HD bands), counted in spawn order among the encounter's matching
    monsters. A monster's band follows the Hit Dice it has left, so a drained monster counts in a lower band.
    The scroll of protection from elementals bars every elemental it matches, with no count.
    """
    wards = _party_wards(session)
    if not wards:
        return False
    template = monster.template
    for ward in wards:
        params = ward.definition.params
        matches = template.id in params.get("bars_template_ids", ()) or set(template.categories) & set(
            params.get("bars_categories", ())
        )
        if not matches:
            continue
        if params.get("all_affected"):
            return True
        hd = monster.hit_dice_count
        band_key = "affected_1_3" if hd <= 3 else ("affected_4_5" if hd <= 5 else "affected_6_plus")
        count = int(params.get(band_key, 0))
        if count <= 0:
            continue
        matching_ids = sorted(
            candidate.id
            for candidate in _encounter_monsters(session)
            if _ward_matches(candidate, params) and _hd_band(candidate) == band_key
        )
        if monster.id in matching_ids[:count]:
            return True
    return False


def _ward_matches(monster, params) -> bool:
    template = monster.template
    return template.id in params.get("bars_template_ids", ()) or bool(
        set(template.categories) & set(params.get("bars_categories", ()))
    )


def _hd_band(monster) -> str:
    hd = monster.hit_dice_count
    return "affected_1_3" if hd <= 3 else ("affected_4_5" if hd <= 5 else "affected_6_plus")


def _encounter_monsters(session) -> list[MonsterInstance]:
    if session.encounter is None:
        return []
    return [
        session.monsters[monster_id]
        for group in session.encounter.groups
        for monster_id in group.monster_ids
        if monster_id in session.monsters
    ]


def _break_party_wards(session, target) -> list[Event]:
    """Melee against a warded monster breaks the circle, and the ward releases."""
    events: list[Event] = []
    for ward in _party_wards(session):
        if _ward_matches(target, ward.definition.params):
            events.extend(session.ledger.release(ward.effect_id, session.registry()))
    return events


def _resolve_use_item(session, member, declaration, fire_damaged) -> list[Event]:
    from osrlib.crawl import exploration

    group = _group_by_id(session, declaration.target_group_id)
    if group is None or group.fled:
        return []
    pool = _monster_pool(session, group)
    if not pool:
        return []
    # One lookup removes the flask and returns it: the instance the attack
    # resolves from is the instance that left the pack.
    instance = exploration._consume_item(member, declaration.item_id)
    if not isinstance(instance, ItemInstance) or not isinstance(instance.template, GearTemplate):
        # Unreachable: the declaration validator proved the item is gear with a
        # combat facet (holy water, a flask of burning oil) before the round ran.
        return []
    template = instance.template
    context = AttackContext(
        distance_feet=_member_distance(session, member.id, group),
        situational_modifier=_gaze_stance_modifier(member, declaration),
        lit=True,
    )
    result = resolve_splash_attack(
        member,
        pool[0],
        template,
        context=context,
        ruleset=session.ruleset,
        stream=session.streams.get(COMBAT_STREAM),
        ledger=session.ledger,
        clock=session.clock,
        allocator=session.allocator,
        registry=session.registry(),
    )
    _note_fire(result.events, group, fire_damaged)
    events = list(result.events)
    events.extend(_break_invisibility(session, member))
    return events


def _note_fire(events, group, fire_damaged) -> None:
    from osrlib.core.events import DamageDealtEvent

    member_ids = set(group.monster_ids)
    for event in events:
        if isinstance(event, DamageDealtEvent) and "fire" in event.keys and event.target_id in member_ids:
            fire_damaged.add(group.id)


def _break_invisibility(session, member) -> list[Event]:
    events: list[Event] = []
    for effect in list(session.ledger.active_on(member.id, "invisibility")):
        events.extend(session.ledger.release(effect.effect_id, session.registry()))
    return events


def _fizzle_event(caster_id: str, spell_id: str, *, reversed: bool, reason: str) -> SpellDisruptedEvent:
    """Report a declaration the magic phase's re-check refused, at `magic.cast.fizzled`.

    `reason` is the first rejection code the re-check produced, which a front end reads to learn why the
    spell failed. A refused declaration never raises out of the round, so this event is its only result.
    """
    return SpellDisruptedEvent(
        code="magic.cast.fizzled", caster_id=caster_id, spell_id=spell_id, reversed=reversed, reason=reason
    )


def _fizzle_cast(session, member, spell_id: str, *, reversed: bool, reason: str, state) -> list[Event]:
    """Lose a declared cast the magic phase's re-check refused, the way a disruption loses it.

    The caster gives up the memorized copy through
    [`disrupt_casting`][osrlib.core.spells.disrupt_casting], so the declared form is what goes, and
    concentration releases as it does on any other action. A caster with no copy left to give up still
    gets the event and loses nothing more. That way, a cast whose copy an earlier phase already took,
    like an energy drain, doesn't raise here.
    """
    if any(copy.spell_id == spell_id for copy in member.memorized_spells):
        disrupt_casting(member, spell_id, reversed=reversed)
    events: list[Event] = [_fizzle_event(member.id, spell_id, reversed=reversed, reason=reason)]
    events.extend(_release_concentration(session, member.id, state))
    return events


def _party_magic(session, by_member, pending_casters, disrupted, acted, state) -> list[Event]:
    """Resolve the party's magic phase: item uses, turning, and casts, in the order the declarations arrived.

    A caster the round already disrupted reports `magic.cast.disrupted` and takes no further part. That
    check comes before the re-check below, so a caster who was both hit and silenced reports the
    disruption the players saw.

    A cast, a scroll read in `_resolve_scroll_cast`, a device use (a wand, staff, or rod), and a turning
    are each checked again just before they resolve, with the checks they passed at the top of the round.
    One that now fails fizzles instead of resolving. The re-check takes no draw, so a declaration that
    passes it resolves with the same draws as before. A potion has no check to fail.

    - A fizzled cast or scroll read reports a `SpellDisruptedEvent` at `magic.cast.fizzled`. The caster
      loses the memorized copy, or the scroll loses its spell, as with a disruption.
    - A fizzled device use reports a `DeclarationFizzledEvent` at `magic.device.fizzled`, with the
      instance id as `item_id`. The device spends no charge, isn't identified, emits no
      `items.device.activated`, and breaks no invisibility.
    - A fizzled turning reports a `DeclarationFizzledEvent` at `magic.turning.fizzled`, with no
      `item_id`. It costs nothing and makes no random roll.

    Each fizzle event's `reason` is the first rejection code of the re-check. A fizzled device use or
    turning still releases the actor's concentration, as every declaration other than a cast or a hold
    does.

    A turning reaches only `_turning_candidates`, so a dead monster, or one in a fled group, never
    appears in the event's `types`. When the phases before it leave no candidate, the turning fizzles
    with `battle.declaration.no_target`.
    """
    events: list[Event] = []
    for member, declaration in by_member.values():
        if declaration.action not in ("cast", "turn_undead", "use_item"):
            continue
        if incapacitated(member) or has_condition(member, Condition.CONFUSED):
            continue
        if declaration.action == "use_item":
            if member.inventory.magic_item(declaration.item_id or "") is None:
                continue  # thrown splash gear resolved in the missile phase
            instance = member.inventory.magic_item(declaration.item_id)
            category = magic_item_template(instance).category
            if category in (MagicItemCategory.ROD, MagicItemCategory.STAFF, MagicItemCategory.WAND):
                # Check the device use again, as a cast is checked below. A refused use never happened,
                # so it spends no charge, identifies nothing, and breaks no invisibility.
                recheck = _validate_declaration(session, declaration, member)
                if recheck:
                    events.append(
                        DeclarationFizzledEvent(
                            code="magic.device.fizzled",
                            actor_id=member.id,
                            item_id=declaration.item_id,
                            reason=recheck[0].code,
                        )
                    )
                    acted.add(member.id)
                    continue
            events.extend(_resolve_magic_item_use(session, member, declaration, state))
            if category is not MagicItemCategory.POTION:
                # Unleashing a device or scroll is an attack for invisibility's
                # purposes (see the adaptations register). Drinking is not.
                events.extend(_break_invisibility(session, member))
            acted.add(member.id)
            continue
        if declaration.action == "turn_undead":
            # Turning resolves in the magic phase, but a hit can't disrupt it, because it's
            # a class ability, not a spell (see the adaptations register). It's still checked
            # again, and a refused turning fizzles before it draws or costs anything.
            recheck = _validate_declaration(session, declaration, member)
            if recheck:
                events.append(
                    DeclarationFizzledEvent(code="magic.turning.fizzled", actor_id=member.id, reason=recheck[0].code)
                )
                events.extend(_release_concentration(session, member.id, state))
                acted.add(member.id)
                continue
            result = turn_undead(
                member,
                load_classes().get(member.class_id),
                _turning_candidates(session),
                ledger=session.ledger,
                clock=session.clock,
                allocator=session.allocator,
                registry=session.registry(),
                stream=session.streams.get(MAGIC_STREAM),
            )
            events.extend(result.events)
            events.extend(_split_off_turned(session))
            events.extend(_break_invisibility(session, member))
            events.extend(_release_concentration(session, member.id, state))
            acted.add(member.id)
            continue
        if member.id in disrupted:
            events.extend(disrupt_casting(member, declaration.spell_id, reversed=declaration.reversed))
            events.extend(_release_concentration(session, member.id, state))
            acted.add(member.id)
            continue
        # The declaration is judged again, with the checks it passed at the top of the
        # round, because the phases before this one can change what those checks read
        # (see the adaptations register). Nothing is spent and nothing is drawn first.
        recheck = _validate_declaration(session, declaration, member)
        if recheck:
            events.extend(
                _fizzle_cast(
                    session,
                    member,
                    declaration.spell_id,
                    reversed=declaration.reversed,
                    reason=recheck[0].code,
                    state=state,
                )
            )
            acted.add(member.id)
            continue
        spell = load_spells().get(declaration.spell_id)
        targets, distance, _ = _cast_targets(session, declaration, spell)
        from osrlib.core.spells import CastContext, caster_profile

        profile = caster_profile(member.definition)
        if profile is None:
            continue  # declaration validation already rejected the non-caster
        result = cast_spell(
            member,
            spell,
            declaration.spell_mode,
            profile=profile,
            reversed=declaration.reversed,
            targets=targets,
            context=CastContext(in_combat=True, distance_feet=distance),
            ledger=session.ledger,
            clock=session.clock,
            allocator=session.allocator,
            registry=session.registry(),
            ruleset=session.ruleset,
            stream=session.streams.get(MAGIC_STREAM),
            effects_stream=session.streams.get(EFFECTS_STREAM),
        )
        events.extend(result.events)
        from osrlib.crawl import exploration

        # The stationary *silence* form anchors in battle too: the battle's
        # location is the party's position (see the adaptations register).
        events.extend(exploration._stationary_silence(session, spell, result, targets))
        events.extend(_track_concentration(session, member.id, spell, result, state))
        acted.add(member.id)
    # Any declaration other than a cast or a hold releases the actor's concentration.
    # That includes a turning, even one that didn't resolve (see the adaptations register).
    for member, declaration in by_member.values():
        if declaration.action not in ("cast", "hold"):
            events.extend(_release_concentration(session, member.id, state))
    return events


def _track_concentration(session, caster_id: str, spell, result, state) -> list[Event]:
    """Make a concentration spell the caster's only maintained one, and return the release events.

    A caster maintains one concentration spell at a time. Casting one ends the spell the caster
    already maintains, even when the new spell attaches no effect. A spell that needs no
    concentration leaves the maintained one alone.
    """
    if spell.duration_spec.kind != "concentration":
        return []
    from osrlib.core.events import EffectAttachedEvent

    events = _release_concentration(session, caster_id, state)
    effect_ids = [event.effect_id for event in result.events if isinstance(event, EffectAttachedEvent)]
    if effect_ids:
        state.concentration[caster_id] = effect_ids
    return events


def _release_concentration(session, caster_id: str, state) -> list[Event]:
    events: list[Event] = []
    for effect_id in state.concentration.pop(caster_id, []):
        if any(effect.effect_id == effect_id for effect in session.ledger.effects):
            events.extend(session.ledger.release(effect_id, session.registry()))
    return events


def _confused_party_overrides(session, by_member, fire_damaged) -> list[Event]:
    """A confused party member's declaration is overridden by the behavior roll.

    This mirrors the monster override. The re-save for anyone above 2 HD runs
    first on the magic stream, with a character counting their level. Then
    `attack_caster_group` sends the member at the nearest monster group's front
    rank when engaged, picked uniformly on the combat stream, `attack_own_group`
    sends them at a fellow party member, and `no_action` leaves them babbling.
    """
    events: list[Event] = []
    for member, declaration in by_member.values():
        if incapacitated(member) or not has_condition(member, Condition.CONFUSED):
            continue
        stance_modifier = _gaze_stance_modifier(member, declaration)
        confusion_effects = [
            effect
            for effect in session.ledger.active_on(member.id)
            if effect.definition.condition is Condition.CONFUSED
        ]
        if not confusion_effects:
            continue
        effect = confusion_effects[0]
        params = effect.definition.params
        if member.level >= int(params.get("resave_hd_min_count", 3)):
            from osrlib.core.combat import saving_throw

            save = saving_throw(
                member,
                SaveCategory(str(params.get("resave_category", "spells"))),
                magical=True,
                stream=session.streams.get(MAGIC_STREAM),
            )
            events.extend(save.events)
            if save.passed:
                events.extend(session.ledger.release(effect.effect_id, session.registry()))
                continue
        behaviour = roll(str(params.get("behaviour_dice", "2d6")), session.streams.get(COMBAT_STREAM)).total
        outcome = _behaviour_outcome(params, behaviour)
        if outcome == "attack_caster_group":
            groups = [
                group for group in session.encounter.groups if not group.fled and _living_monsters(session, group)
            ]
            if groups:
                nearest = min(groups, key=lambda group: _member_distance(session, member.id, group))
                if _member_distance(session, member.id, nearest) <= MELEE_RANGE_FEET:
                    pool = _monster_pool(session, nearest)
                    if pool:
                        target = pool[session.streams.get(COMBAT_STREAM).randbelow(len(pool))]
                        context = AttackContext(distance_feet=MELEE_RANGE_FEET, situational_modifier=stance_modifier)
                        result = resolve_attack(
                            member,
                            target,
                            None,
                            context=context,
                            ruleset=session.ruleset,
                            stream=session.streams.get(COMBAT_STREAM),
                            clock=session.clock,
                        )
                        events.extend(result.events)
                        _note_fire(result.events, nearest, fire_damaged)
        elif outcome == "attack_own_group":
            fellows = [other for other in session.party.living_members() if other.id != member.id]
            if fellows:
                target = fellows[session.streams.get(COMBAT_STREAM).randbelow(len(fellows))]
                context = AttackContext(
                    distance_feet=MELEE_RANGE_FEET,
                    situational_modifier=stance_modifier,
                    defender_ally_ac_bonus=_ally_protection_bonus(session, target, _nearest_group(session, target.id)),
                )
                result = resolve_attack(
                    member,
                    target,
                    None,
                    context=context,
                    ruleset=session.ruleset,
                    stream=session.streams.get(COMBAT_STREAM),
                    clock=session.clock,
                )
                events.extend(result.events)
    return events


# ---------------------------------------------------------------------- the monster block


def _policy_for(session, group) -> ActionPolicy:
    policies = getattr(session, "action_policies", None)
    if policies and group.id in policies:
        return policies[group.id]
    first = session.combatant(group.monster_ids[0])
    if getattr(first, "definition", None) is not None:
        return NpcPartyPolicy()
    return ScriptedPolicy()


def _monster_block(
    session,
    *,
    free_round: bool,
    pending_casters: dict | None = None,
    disrupted: set | None = None,
    acted: set | None = None,
    fire_damaged: set | None = None,
    retreating: Collection[str] = frozenset(),
    retreat_reach: Mapping[str, Collection[str]] | None = None,
    npc_actions: dict[str, list[MonsterAction]] | None = None,
    mirrored: Collection[str] = frozenset(),
) -> list[Event]:
    """Resolve the monster side's block: morale, flight, gazes, and the actions each group's policy chose.

    A group that runs moves its full rate away, and it's gone once it stands farther than `FLEE_EXIT_FEET`
    from the nearest living member. A `close` moves the group as `_closing_distance` works it out, and
    `_place_closing_group` places it and reports the move. A `melee` resolves only on a target in
    the group's front rank within 5' of it when the action comes up (see `_reachable_front_rank`), so a
    member who stepped out of reach earlier in the round, or whom the group's close fell short of, isn't
    struck. A formation retreat is the exception, through `retreat_reach`.

    The keyword arguments contain party member ids:

    - `retreating`: the members a retreat exposes this round (see `_retreating_members`). Every
      attack on one of them gets the retreat's bonuses.
    - `retreat_reach`: when the whole party retreats as a formation, each group's front rank within
      5' of it when the round was accepted, keyed by group id. A `melee` on one of these members
      resolves wherever the formation has moved since, because the SRD gives the opponent its attack
      against a retreating combatant for the whole round. It's the monster side's counterpart of the
      party's attack on a group that runs.
    - `mirrored`: the members who fight by a mirror this round. A petrifying gaze passes them by
      (see `_resolve_gazes`).
    """
    from osrlib.crawl.session import MONSTER_ACTION_STREAM

    pending_casters = pending_casters or {}
    disrupted = disrupted if disrupted is not None else set()
    acted = acted if acted is not None else set()
    fire_damaged = fire_damaged if fire_damaged is not None else set()
    retreat_reach = retreat_reach or {}
    events = _split_off_turned(session)
    for group in list(session.encounter.groups):
        if group.fled or not _living_monsters(session, group):
            continue
        if not free_round:
            events.extend(_group_morale(session, group, fire_damaged))
        if group.fleeing or _group_all_shaken(session, group):
            if not any(not cannot_move(monster) for monster in _living_monsters(session, group)):
                # A broken side that cannot run, slept or webbed mid-flight, lies
                # where it is. Flight resumes only if someone can move again.
                continue
            events.extend(_leave_helpless_behind(session, group))
            rate = _pursuer_full_rate(session, group)
            group.distance_feet += rate
            events.append(GroupMovedEvent(group_id=group.id, distance_feet=group.distance_feet))
            if _nearest_member_distance(session, group) > FLEE_EXIT_FEET:
                group.fled = True
            continue
        events.extend(_resolve_gazes(session, group, free_round=free_round, mirrored=mirrored))
        _watch_disruption(events, pending_casters, disrupted, acted)
        if npc_actions is not None and group.id in npc_actions:
            actions = npc_actions[group.id]
        else:
            actions = _policy_for(session, group).choose(session, group, session.streams.get(MONSTER_ACTION_STREAM))
        events.extend(_confused_overrides(session, group, actions, retreating=retreating))
        moved = False
        for action in actions:
            monster = session.combatant(action.monster_id)
            if has_condition(monster, Condition.DEAD) or has_condition(monster, Condition.CONFUSED):
                continue
            if has_condition(monster, Condition.TURNED):
                continue  # a turned monster harms no one, whatever its policy chose
            if action.kind == "close" and not moved:
                if cannot_move(monster):
                    continue
                moved = True
                arrival = _closing_distance(session, group, _encounter_rate(monster, session))
                events.extend(_place_closing_group(session, group, arrival))
            elif action.kind == "breath":
                events.extend(_resolve_breath(session, monster, group))
                _watch_disruption(events, pending_casters, disrupted, acted)
            elif action.kind == "melee":
                target = session.registry().get(action.target_id)
                if target is None or has_condition(target, Condition.DEAD):
                    continue
                reach = _reachable_front_rank(group, _placement(session))
                if action.target_id not in reach and action.target_id not in retreat_reach.get(group.id, ()):
                    continue  # the target stands out of the group's reach
                exposed = action.target_id in retreating
                if getattr(monster, "definition", None) is not None:
                    events.extend(
                        _resolve_npc_attack(session, monster, target, group, missile=False, retreating=exposed)
                    )
                else:
                    events.extend(
                        _resolve_monster_melee(
                            session, monster, target, group, retreating=exposed, routine=action.routine
                        )
                    )
                _watch_disruption(events, pending_casters, disrupted, acted)
            elif action.kind == "npc_shoot":
                target = session.registry().get(action.target_id)
                if target is None or has_condition(target, Condition.DEAD):
                    continue
                events.extend(
                    _resolve_npc_attack(
                        session, monster, target, group, missile=True, retreating=action.target_id in retreating
                    )
                )
                _watch_disruption(events, pending_casters, disrupted, acted)
            elif action.kind == "npc_cast":
                events.extend(_resolve_npc_cast(session, monster, group, action, disrupted))
                acted.add(monster.id)
                _watch_disruption(events, pending_casters, disrupted, acted)
            elif action.kind == "npc_drink":
                from osrlib.crawl import exploration

                instance = monster.inventory.magic_item(action.item_id) if action.item_id else None
                if instance is not None:
                    _, drink_events = exploration._use_potion(session, monster, instance, magic_item_template(instance))
                    events.extend(drink_events)
    return events


def _declare_npc_actions(session, state, pending_casters: dict, events: list[Event]) -> dict[str, list[MonsterAction]]:
    """Choose NPC sides' actions at the top of the round and post their casts."""
    from osrlib.crawl.session import MONSTER_ACTION_STREAM

    chosen: dict[str, list[MonsterAction]] = {}
    if state.monsters_hold_rounds > 0:
        return chosen
    for group in session.encounter.groups:
        if group.fled:
            continue
        first = session.combatant(group.monster_ids[0])
        if getattr(first, "definition", None) is None:
            continue
        if not _living_monsters(session, group):
            continue
        actions = _policy_for(session, group).choose(session, group, session.streams.get(MONSTER_ACTION_STREAM))
        chosen[group.id] = actions
        for action in actions:
            if action.kind == "npc_cast" and action.spell_id is not None:
                pending_casters[action.monster_id] = action
                events.append(SpellDeclaredEvent(caster_id=action.monster_id, spell_id=action.spell_id))
    return chosen


def _resolve_npc_attack(session, npc, target, group, *, missile: bool, retreating: bool) -> list[Event]:
    """An NPC adventurer's weapon attack, resolved through the kernel path the party uses.

    A missile attack resolves at the target member's own distance to the NPC's group, which sets its range band.
    A melee attack resolves at 5'.
    """
    weapon = _npc_wielded(npc, missile=missile)
    events: list[Event] = []
    if session.ledger.active_on(getattr(target, "id", ""), "mirror_image"):
        events.extend(pop_mirror_image(session.ledger, target.id, registry=session.registry(), clock=session.clock))
        return events
    context = AttackContext(
        distance_feet=_member_distance(session, target.id, group) if missile else MELEE_RANGE_FEET,
        defender_retreating=retreating,
        defender_ally_ac_bonus=_ally_protection_bonus(session, target, group),
    )
    result = resolve_attack(
        npc,
        target,
        weapon,
        context=context,
        ruleset=session.ruleset,
        stream=session.streams.get(COMBAT_STREAM),
        clock=session.clock,
    )
    events.extend(result.events)
    if isinstance(weapon, MagicItemInstance) and result.attack_roll.hit and not result.absorbed:
        events.extend(_on_hit_drain(session, weapon, target))
    events.extend(_identify_worn_items(session, target))
    return events


def _resolve_npc_cast(session, npc, group, action: MonsterAction, disrupted: set) -> list[Event]:
    """Resolve (or disrupt) an NPC's declared cast through the character kernel.

    An area spell covers the ranks toward the NPC's group by the footprint rule (see `_area_rows`). The spell's
    range is checked against the target member's own distance to the group, or against the nearest member's
    distance for an area spell or a spell on one of the NPCs.
    """
    from osrlib.core.spells import CastContext, cast_spell, caster_profile, validate_cast

    if action.spell_id is None or action.spell_mode is None:
        return []
    profile = caster_profile(npc.definition)
    if profile is None:
        return []
    if npc.id in disrupted:
        return disrupt_casting(npc, action.spell_id, reversed=False)
    spell = load_spells().get(action.spell_id)
    mode = spell.mode(action.spell_mode)
    targeting = mode.targeting
    placement = _placement(session)
    distance = _nearest_member_distance(session, group)
    if targeting is not None and targeting.mode is TargetingMode.AREA:
        covered = _area_rows(group, placement, targeting.shape, targeting.dimensions)
        targets: list = [session.member(member_id) for member_id in covered]
    elif action.target_id is not None:
        target = session.registry().get(action.target_id)
        if target is None or has_condition(target, Condition.DEAD):
            return []
        targets = [target]
        if target.id in placement.order:
            distance = placement.distance(target.id, group)
    else:
        targets = []
    context = CastContext(in_combat=True, distance_feet=distance)
    if validate_cast(
        npc, spell, action.spell_mode, profile=profile, targets=targets, context=context, ledger=session.ledger
    ):
        return []
    result = cast_spell(
        npc,
        spell,
        action.spell_mode,
        profile=profile,
        targets=targets,
        context=context,
        ledger=session.ledger,
        clock=session.clock,
        allocator=session.allocator,
        registry=session.registry(),
        ruleset=session.ruleset,
        stream=session.streams.get(MAGIC_STREAM),
        effects_stream=session.streams.get(EFFECTS_STREAM),
    )
    return list(result.events)


def _group_all_shaken(session, group) -> bool:
    living = _living_monsters(session, group)
    return bool(living) and all(
        has_condition(monster, Condition.TURNED) or has_condition(monster, Condition.AFRAID) for monster in living
    )


def _pursuer_full_rate(session, group) -> int:
    combatant = session.combatant(group.monster_ids[0])
    if getattr(combatant, "definition", None) is not None:
        living = _living_monsters(session, group)
        return min(member.movement_rate(session.ruleset) for member in living) if living else 0
    modes = combatant.template.movement
    base = next((mode for mode in modes if mode.descriptor is None), modes[0])
    return base.rate_feet


def _leave_helpless_behind(session, group) -> list[Event]:
    """Split a fleeing group's immobile living members into a stay-behind group.

    Fleeing is movement, and a member who cannot move cannot run. The runners keep
    the original group, its flags, and the shared group bundle they carry off
    ("routed ones flee with theirs"). The helpless become a fresh non-fleeing
    group where the group fled from, at the current distance, with their individual
    treasure bundles still on them. Dead members also stay in the runners' group
    record, because the code that classifies the outcome and drops loot reads
    the `dead` condition before any group flag, so that code sees no change.
    """
    from osrlib.crawl.encounter import EncounterGroup

    left_ids = [monster.id for monster in _living_monsters(session, group) if cannot_move(monster)]
    if not left_ids:
        return []
    stay_behind = EncounterGroup(
        id=session.allocator.allocate("group"),
        label=group.label,
        monster_ids=left_ids,
        distance_feet=group.distance_feet,
    )
    for monster_id in left_ids:
        bundle = group.member_treasure.pop(monster_id, None)
        if bundle is not None:
            stay_behind.member_treasure[monster_id] = bundle
    group.monster_ids = [monster_id for monster_id in group.monster_ids if monster_id not in set(left_ids)]
    session.encounter.groups.append(stay_behind)
    return [MonstersLeftBehindEvent(group_id=stay_behind.id, source_group_id=group.id, count=len(left_ids))]


def _split_off_turned(session) -> list[Event]:
    """Move the turned members of each partly turned group into a new group.

    The OSE SRD's turned undead "flee from the cleric's presence if possible, and will not harm
    the cleric". A group whose living members are all turned or afraid already runs as a whole, so
    this splits only a group that still has a living member who is neither turned nor afraid.

    The turned members become a new group at the same distance. Each one takes the treasure it
    carries, because monsters that flee take their treasure with them. The group's shared bundle stays with
    the members who fight on, so it drops only when they're all slain. The monster block moves the
    new group away like any wholly turned group, and leaves behind a member who can't move. The
    members who fight on stay in the original group, so its id and its registered policy don't
    change. Dead members stay in the original group's record, as in `_leave_helpless_behind`.

    The session calls this when a battle starts, after each turning in the magic phase, and at the
    top of each monster block. The last call catches a group that becomes partly turned later, like
    a group of turned and afraid members after the fear on one of them ends.
    """
    from osrlib.crawl.encounter import EncounterGroup

    events: list[Event] = []
    for group in list(session.encounter.groups):
        if group.fled or group.fleeing or _group_all_shaken(session, group):
            continue
        turned_ids = [
            monster.id for monster in _living_monsters(session, group) if has_condition(monster, Condition.TURNED)
        ]
        if not turned_ids:
            continue
        runners = EncounterGroup(
            id=session.allocator.allocate("group"),
            label=group.label,
            monster_ids=turned_ids,
            distance_feet=group.distance_feet,
        )
        for monster_id in turned_ids:
            bundle = group.member_treasure.pop(monster_id, None)
            if bundle is not None:
                runners.member_treasure[monster_id] = bundle
        group.monster_ids = [monster_id for monster_id in group.monster_ids if monster_id not in set(turned_ids)]
        session.encounter.groups.append(runners)
        events.append(MonsterFledEvent(code="battle.side.fled", group_id=runners.id))
    return events


def _group_morale(session, group, fire_damaged) -> list[Event]:
    """Check a group's morale automatically, running the kernel's triggers through the per-battle tracker.

    A conditional alternate resolves from the round's context. Fear of fire, for
    example, applies when the round's damage included fire. The spell morale
    modifier applies under the usual morale-modifier rule.

    A group that's already running makes no check, whether it failed morale or its
    living members are all turned or afraid. Its triggers stay pending. If the fear
    on its members ends and it stops running, it checks them then.
    """
    state = session.battle
    score = _group_morale_score(session, group)
    if score is None or group.fleeing or _group_all_shaken(session, group):
        return []
    members = [session.combatant(monster_id) for monster_id in group.monster_ids]
    if all(incapacitated(member) for member in members):
        # No one on the side is awake to break: a morale check is a decision, and
        # a side that is entirely asleep, paralysed, or petrified makes none. The
        # triggers stay pending, unconsumed, so a member who can act again judges
        # them then.
        return []
    triggers = morale_triggers(members)
    acted = state.morale_acted.setdefault(group.id, [])
    events: list[Event] = []
    for trigger in triggers:
        if trigger in acted:
            continue
        acted.append(trigger)
        effective = score
        first = session.combatant(group.monster_ids[0])
        for alternate in getattr(getattr(first, "template", None), "morale_alternates", ()):
            if "fire" in alternate.condition and group.id in fire_damaged:
                effective = alternate.score
        modifier = morale_modifier(first)
        result = state.morale.check(group.id, effective, modifier=modifier, stream=session.streams.get(COMBAT_STREAM))
        if result is None:
            continue
        events.extend(result.events)
        if not result.held:
            group.fleeing = True
            events.append(MonsterFledEvent(code="battle.side.fled", group_id=group.id))
            break
    return events


def _confused_overrides(session, group, actions, *, retreating: Collection[str] = frozenset()) -> list[Event]:
    """The confusion check the round handler runs each round: re-save, then the 2d6 behavior roll.

    Whether a monster re-saves depends on the Hit Dice it has left, so a drained monster can lose the
    re-save. The re-save runs on the magic stream, and the behavior roll and its pick of a
    target in its group run on the combat stream. The round handler makes these rolls,
    distinct from the ledger's tick-time effects. A confused monster's attack on a party member in
    `retreating` gets the retreat's +2 and ignores the shield, as every attack on that member does.
    """
    events: list[Event] = []
    for monster in _living_monsters(session, group):
        confusion_effects = [
            effect
            for effect in session.ledger.active_on(monster.id)
            if effect.definition.condition is Condition.CONFUSED
        ]
        if not confusion_effects:
            continue
        effect = confusion_effects[0]
        params = effect.definition.params
        hd_count = monster.hit_dice_count
        resave_min = int(params.get("resave_hd_min_count", 3))
        qualifies = hd_count >= resave_min or (
            bool(params.get("resave_at_hd_count_2_with_bonus"))
            and hd_count == 2
            and monster.template.hit_dice.modifier > 0
        )
        if qualifies:
            save = saving_throw(
                monster,
                SaveCategory(str(params.get("resave_category", "spells"))),
                magical=True,
                stream=session.streams.get(MAGIC_STREAM),
            )
            events.extend(save.events)
            if save.passed:
                events.extend(session.ledger.release(effect.effect_id, session.registry()))
                continue
        behaviour = roll(str(params.get("behaviour_dice", "2d6")), session.streams.get(COMBAT_STREAM)).total
        outcome = _behaviour_outcome(params, behaviour)
        if outcome == "attack_caster_group":
            pool = _seen(session, _reachable_front_rank(group, _placement(session)))
            targets = _reachable_targets(session, monster, pool)
            if targets:
                target = targets[session.streams.get(COMBAT_STREAM).randbelow(len(targets))]
                events.extend(
                    _resolve_monster_melee(session, monster, target, group, retreating=target.id in retreating)
                )
        elif outcome == "attack_own_group":
            fellows = [other for other in _living_monsters(session, group) if other.id != monster.id]
            if fellows:
                target = fellows[session.streams.get(COMBAT_STREAM).randbelow(len(fellows))]
                events.extend(_resolve_monster_melee(session, monster, target, group, retreating=False))
        # no_action: the confused creature babbles.
    return events


def _behaviour_outcome(params, total: int) -> str:
    for entry in params.get("behaviour_table", ()):
        band, _, outcome = str(entry).partition(":")
        low, _, high = band.partition("-")
        if int(low) <= total <= int(high or low):
            return outcome
    return "no_action"


_UNROLLED_ATTACK_NAMES = frozenset({"gaze", "breath"})
"""Attack names that battle never rolls as attacks, in any routine.

`_resolve_gazes` resolves a petrifying gaze once a round, and a `"breath"` action resolves a breath
weapon. The referee handles any other gaze or breath.
"""


def _resolve_monster_melee(
    session, monster, target, group, *, retreating: bool, routine: int | None = None
) -> list[Event]:
    """Resolve one of a monster's attack routines against one target, with what each hit does beyond damage.

    `group` is the monster's group, which decides the target's rank-mates for a ring of protection (see
    `_ally_protection_bonus`). `routine` is an index into the template's `attacks`. None resolves the first
    routine, as a confused monster's attack does. An index out of range, negative included, resolves nothing, and
    so does a `SPECIAL` routine. An attack in `_UNROLLED_ATTACK_NAMES` takes no attack roll.
    """
    events: list[Event] = []
    routines = monster.template.attacks
    index = 0 if routine is None else routine
    if not 0 <= index < len(routines) or routines[index].use is RoutineUse.SPECIAL:
        return []
    for attack in routines[index].attacks:
        if attack.name in _UNROLLED_ATTACK_NAMES:
            continue
        for _ in range(attack.count):
            if has_condition(target, Condition.DEAD):
                return events
            if session.ledger.active_on(getattr(target, "id", ""), "mirror_image"):
                # Each incoming attack pops an image instead of resolving (RAW).
                events.extend(
                    pop_mirror_image(session.ledger, target.id, registry=session.registry(), clock=session.clock)
                )
                continue
            context = AttackContext(
                distance_feet=MELEE_RANGE_FEET,
                situational_modifier=_gazer_bonus(monster, target),
                defender_retreating=retreating,
                defender_ally_ac_bonus=_ally_protection_bonus(session, target, group),
            )
            result = resolve_attack(
                monster,
                target,
                attack,
                context=context,
                ruleset=session.ruleset,
                stream=session.streams.get(COMBAT_STREAM),
                clock=session.clock,
            )
            events.extend(result.events)
            if result.attack_roll.hit and not result.absorbed:
                events.extend(_apply_hit_effects(session, monster, target, attack))
            events.extend(_identify_worn_items(session, target))
    return events


_HIT_EFFECT_KINDS = frozenset({"paralysis", "poison", "disease", "energy_drain", "petrification", "blindness"})
"""The attack effects battle applies when a monster hits. The adaptations register lists the others."""


def _apply_hit_effects(session, monster, target, attack: MonsterAttack) -> list[Event]:
    """Apply each effect a monster's attack lists to the target the attack hit.

    The effects apply once for each hit, so a target hit by two paralysing claws saves twice. A dead
    or petrified target takes none of them.
    """
    events: list[Event] = []
    for kind in attack.effects:
        if has_condition(target, Condition.DEAD) or has_condition(target, Condition.PETRIFIED):
            break
        ability = _hit_ability(monster, kind)
        if ability is not None:
            events.extend(_apply_hit_effect(session, monster, target, kind, ability.params))
    return events


def _hit_ability(monster, kind: str) -> MonsterAbility | None:
    """Return the ability that contains an attack effect's parameters, or None when battle doesn't apply the effect.

    Battle applies a kind in `_HIT_EFFECT_KINDS` when the monster's ability for it isn't manual. The
    basilisk has two `petrification` abilities. `_resolve_gazes` uses the one with the `gaze` vector,
    so this function reads only an ability with the `touch` vector or with no vector.
    """
    if kind not in _HIT_EFFECT_KINDS:
        return None
    for ability in monster.template.abilities:
        if ability.tag == kind and ability.params.get("vector", "touch") == "touch":
            return None if ability.manual else ability
    return None


def _apply_hit_effect(session, monster, target, kind: str, params: Mapping[str, Any]) -> list[Event]:
    """Apply one attack effect to the target, with the save the SRD gives for it.

    - `energy_drain` takes levels or Hit Dice through `resolve_energy_drain`. There's no save.
    - `disease` gives the target the disease in the ability's `kind` param, like mummy rot. There's no
      save, and only magic removes it.
    - `paralysis` makes the target save versus paralysis or be paralysed for the ability's duration.
      When the ability's `unaffected` list includes `elf`, a character whose class has
      `ghoul_paralysis_immunity` rolls no save and isn't paralysed.
    - `petrification` makes the target save versus paralysis, the category for petrification, or turn
      to stone.
    - `poison` makes the target save versus death, with the ability's `save_modifier`, or die. The
      target dies at once, or when the onset in the ability's params runs out.
    - `blindness`, the spitting cobra's spit, makes the target save versus death, the category for
      poison, or be blinded for good. The effect is permanent and isn't dispellable, and a target
      that's already blind saves again but gains nothing more.

    A target monster that's immune to the effect's condition rolls no save and takes nothing.
    Saves draw from the combat stream, rolled durations and onsets from the effects stream, and
    energy drain from the advancement stream.
    """
    if kind == "energy_drain":
        return resolve_energy_drain(monster, target, stream=session.streams.get(ADVANCEMENT_STREAM))
    if kind == "disease":
        disease = EffectDefinition(
            kind=str(params.get("kind", "disease")),
            condition=Condition.DISEASED,
            permanent=True,
            stacking="ignore",
        )
        return _attach_hit_effect(session, target, disease)
    category = SaveCategory.PARALYSIS
    modifier = 0
    condition: Condition
    definition: EffectDefinition | None
    if kind == "paralysis":
        if _spared_by_ghoul_immunity(target, params):
            return []
        condition = Condition.PARALYSED
        definition = EffectDefinition(
            kind="paralysis",
            condition=condition,
            duration_unit=TimeUnit(str(params.get("unit", "turn"))),
            duration_dice=str(params.get("duration", "2d4")),
        )
    elif kind == "petrification":
        condition = Condition.PETRIFIED
        definition = EffectDefinition(kind="petrification", condition=condition, permanent=True)
    elif kind == "blindness":
        category = SaveCategory.DEATH
        condition = Condition.BLIND
        definition = EffectDefinition(kind="blindness", condition=condition, permanent=True, stacking="ignore")
    else:
        category = SaveCategory.DEATH
        modifier = _int_param(params, "save_modifier")
        condition = Condition.POISONED
        definition = _poison_onset(params)
    defenses = getattr(getattr(target, "template", None), "defenses", None)
    if defenses is not None and condition in defenses.condition_immunities:
        return []
    save = saving_throw(target, category, modifier=modifier, source=monster, stream=session.streams.get(COMBAT_STREAM))
    events = list(save.events)
    if save.passed:
        return events
    if definition is None:
        events.extend(kill(target))
    else:
        events.extend(_attach_hit_effect(session, target, definition))
    return events


def _poison_onset(params: Mapping[str, Any]) -> EffectDefinition | None:
    """Return the effect that kills when a poison's onset runs out, or None for a poison that kills at once.

    The effect's kind is `poison`, so the session records the death it causes as a poison death.
    """
    unit = params.get("onset_unit")
    if unit is None:
        return None
    amount = params.get("onset_amount")
    dice = params.get("onset_dice")
    return EffectDefinition(
        kind="poison",
        condition=Condition.POISONED,
        duration_unit=TimeUnit(str(unit)),
        duration_amount=int(amount) if amount is not None else None,
        duration_dice=str(dice) if dice is not None else None,
        expiry="death",
    )


def _spared_by_ghoul_immunity(target, params: Mapping[str, Any]) -> bool:
    """Return whether the target is an elf and the paralysis ability's `unaffected` list includes elves.

    The elf's immunity is a class ability, `ghoul_paralysis_immunity`, so this function reads it from
    the character's class. The `unaffected` list also includes `larger_than_ogre`, but osrlib has no
    model of size, so no creature is spared for its size.
    """
    if "elf" not in params.get("unaffected", ()):
        return False
    definition = getattr(target, "definition", None)
    return definition is not None and any(ability.tag == "ghoul_paralysis_immunity" for ability in definition.abilities)


def _attach_hit_effect(session, target, definition: EffectDefinition) -> list[Event]:
    _, events = session.ledger.attach(
        definition,
        target.id,
        clock=session.clock,
        allocator=session.allocator,
        registry=session.registry(),
        stream=session.streams.get(EFFECTS_STREAM),
    )
    return events


def _petrifying_gaze(monster) -> MonsterAbility | None:
    """Return the monster's petrifying gaze, like the basilisk's or the medusa's, or None when it has none."""
    template = getattr(monster, "template", None)
    if template is None:
        return None
    for ability in template.abilities:
        if ability.tag == "petrification" and ability.params.get("vector") == "gaze" and not ability.manual:
            return ability
    return None


def _surprise_gaze(monster) -> bool:
    """Whether characters the monster surprises meet its gaze, as the basilisk's `surprise` ability says."""
    template = getattr(monster, "template", None)
    ability = template.ability("surprise") if template is not None else None
    return ability is not None and not ability.manual and bool(ability.params.get("meets_gaze"))


def _resolve_gazes(session, group, *, free_round: bool, mirrored: Collection[str]) -> list[Event]:
    """Turn each gazer's petrifying gaze on the party, once a round.

    A gaze takes no attack roll and no action. Every living gazer that isn't incapacitated uses it
    before its group acts, whatever the policy then chooses for it. While the gazer's group is in
    melee, the gaze reaches the party's front rank, except the members in `mirrored`.
    `resolve_gaze` skips a character who is averting their eyes, is blind, or is incapacitated. It
    rolls a save versus paralysis on the combat stream for each of the others.

    The party's front rank here is the group's front rank within 5' of it (see `_reachable_front_rank`).
    While no member stands within 5' of the group, nobody meets the gaze.

    When the encounter opened with the party surprised, `_surprise_gazes` already turned the
    basilisk's gaze on every member. So in the monsters' free round (`free_round`), a gazer whose
    `surprise` ability has `meets_gaze` doesn't gaze again.
    """
    if not _reachable_front_rank(group, _placement(session)):
        return []
    events: list[Event] = []
    for monster in _living_monsters(session, group):
        if incapacitated(monster) or _petrifying_gaze(monster) is None:
            continue
        if free_round and session.encounter.party_surprised and _surprise_gaze(monster):
            continue
        front = _reachable_front_rank(group, _placement(session))
        engaged = [session.member(member_id) for member_id in front if member_id not in mirrored]
        events.extend(_gaze(session, monster, engaged))
    return events


def _surprise_gazes(session, monsters: Iterable) -> list[Event]:
    """Turn each surprise gazer's petrifying gaze on every living member of the party it surprised.

    `start_encounter` calls this as the encounter opens, when the party is surprised, as the OSE
    SRD's "Characters surprised by a basilisk meet its gaze" says. A monster gazes when its
    `surprise` ability has `meets_gaze`, it has a petrifying gaze, and it isn't incapacitated. The
    gaze reaches every living member once, at any distance and in any rank. No guard protects
    against it, because the party hasn't declared anything yet. `resolve_gaze` skips a member who
    is blind or already incapacitated. It rolls a save versus paralysis on the combat stream for
    each of the others.
    """
    events: list[Event] = []
    for monster in monsters:
        if incapacitated(monster) or _petrifying_gaze(monster) is None or not _surprise_gaze(monster):
            continue
        events.extend(_gaze(session, monster, session.party.living_members()))
    return events


def _gaze(session, monster, engaged: list) -> list[Event]:
    return resolve_gaze(
        monster,
        engaged,
        stream=session.streams.get(COMBAT_STREAM),
        ledger=session.ledger,
        clock=session.clock,
        allocator=session.allocator,
        registry=session.registry(),
    )


_AVERT_EYES_PENALTY = -4
"""The attack roll penalty for fighting with averted eyes, the basilisk's and the medusa's "-4 penalty to-hit"."""

_GAZER_BONUS_AGAINST_AVERTED = 2
"""A gazer's attack bonus against a creature averting its eyes: "the basilisk gains a +2 bonus to attack"."""

_MIRROR_PENALTY = -1
"""The attack roll penalty for fighting by a mirror: "Fighting by looking in a mirror incurs a -1 penalty to attack"."""

_MIRROR_ITEM_ID = "mirror"
"""The equipment catalog's mirror, which a member has to carry to fight by one."""


def _avert_eyes(by_member) -> list:
    """Give each member who declared `avert_eyes` the `averted_eyes` condition, and return those members.

    The condition has no effect behind it, so the grant makes no random roll, and this drops its event.
    A member who already has `averted_eyes` with no effect id, which only the host game can give, isn't
    returned, so `_end_averting` leaves that condition alone.
    """
    return [
        member
        for member, declaration in by_member.values()
        if declaration.gaze_defense == "avert_eyes" and grant_condition(member, Condition.AVERTED_EYES, None)
    ]


def _end_averting(averting) -> None:
    """Take back the `averted_eyes` condition `_avert_eyes` gave, at the end of the round, again without an event."""
    for member in averting:
        remove_condition(member, Condition.AVERTED_EYES, None)


def _gaze_stance_modifier(member, declaration: BattleDeclaration) -> int:
    """The penalty a member's gaze stance puts on every attack roll they make this round.

    Averting the eyes costs −4 and fighting by a mirror costs −1, whatever the attack is aimed at,
    because the SRD gives both penalties without naming a target.
    """
    if has_condition(member, Condition.AVERTED_EYES):
        return _AVERT_EYES_PENALTY
    if declaration.gaze_defense == "mirror":
        return _MIRROR_PENALTY
    return 0


def _gazer_bonus(monster, target) -> int:
    """A gazer's bonus to attack a target averting its eyes, from the monster's `averting_eyes` ability.

    Only a monster whose entry gives the bonus gains it, so an ogre beside a basilisk gains nothing.
    """
    template = getattr(monster, "template", None)
    ability = template.ability("averting_eyes") if template is not None else None
    if ability is None or ability.manual or not has_condition(target, Condition.AVERTED_EYES):
        return 0
    return _GAZER_BONUS_AGAINST_AVERTED


def _resolve_breath(session, monster, group) -> list[Event]:
    """A breath weapon against the party, resolved through the deterministic rank-coverage footprint.

    A breath that hits one target, like the hellhound's, picks with equal chance from the first row toward the
    group, at any distance, leaving out invisible members (see `_first_row_seen`). The pick is on the
    monster-action stream. An area breath covers the ranks toward the group from the front, by distance (see
    `_area_rows`).
    """
    params = monster.template.ability("breath_weapon").params
    placement = _placement(session)
    if str(params.get("targeting")) == "single":
        from osrlib.crawl.session import MONSTER_ACTION_STREAM

        pool = _first_row_seen(session, group, placement)
        if not pool:
            return []
        target = pool[session.streams.get(MONSTER_ACTION_STREAM).randbelow(len(pool))]
        targets = [target]
    else:
        shape = str(params.get("shape", "cone"))
        targets = [session.member(member_id) for member_id in _area_rows(group, placement, shape, params)]
        if not targets:
            return []
    return resolve_breath(
        monster, targets, ruleset=session.ruleset, stream=session.streams.get(COMBAT_STREAM), clock=session.clock
    )


def _watch_disruption(events, pending_casters, disrupted, acted) -> None:
    """The RAW disruption trigger, as the round handler detects it: a hit or a failed save before the caster acts."""
    for event in events:
        target = getattr(event, "defender_id", None) or getattr(event, "target_id", None)
        if target not in pending_casters or target in acted or target in disrupted:
            continue
        if isinstance(event, AttackRolledEvent) and event.code in ("combat.attack.hit", "combat.attack.auto_hit"):
            disrupted.add(target)
        elif isinstance(event, SavingThrowRolledEvent) and event.code == "combat.save.failed":
            disrupted.add(target)


_HANDLERS = {
    ResolveBattleRound: _handle_resolve_battle_round,
}
"""The battle commands this module handles, keyed by command class.

[`GameSession`][osrlib.crawl.session.GameSession] merges this map into its private handler table
the first time it dispatches a command, alongside the exploration, encounter, and referee maps. There
is no registration point here: the only documented way to run a command is
[`GameSession.execute`][osrlib.crawl.session.GameSession.execute], which picks the handler, runs the
mode gate, and records the command log, calls the listeners, and runs the validation-phase checks a
handler alone would skip.

The value takes `(session, command)` and returns a `(rejections, events)` pair. Battle has one command
because a round is resolved as a whole: every party member declares, and the round handler runs both sides
in the SRD's phase order.
"""
