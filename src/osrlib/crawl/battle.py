"""Run a fight round by round on the range track, with a replaceable policy choosing what the monsters do.

A battle follows an encounter from [`osrlib.crawl.encounter`][osrlib.crawl.encounter] when a fight
starts. [`start_battle`][osrlib.crawl.battle.start_battle] opens the battle, and the session calls
it for you: when the monsters attack, when a chase catches the party, and when the party executes
[`EngageBattle`][osrlib.crawl.commands.EngageBattle]. The session is then in `battle` mode, and
`session.battle` contains a [`BattleState`][osrlib.crawl.battle.BattleState]. Each round is one
[`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound] command, with one
[`BattleDeclaration`][osrlib.crawl.commands.BattleDeclaration] for each living party member who can
act.

There's no command to end a battle. A round ends it in one of three ways:

- Every party member is dead. The battle and the encounter close, and the session moves to
  `game_over`.
- Every monster group is dead or has fled out of the fight. The battle ends in victory, and
  [`end_encounter`][osrlib.crawl.encounter.end_encounter] closes the encounter.
- The whole party retreats. If no monster group can pursue, the encounter closes. Otherwise the
  session goes back to `encounter` mode with a chase.

You follow the battle through events from [`osrlib.crawl.events`][osrlib.crawl.events]:

- [`BattleStartedEvent`][osrlib.crawl.events.BattleStartedEvent] when the battle opens.
- [`BattleRoundEvent`][osrlib.crawl.events.BattleRoundEvent] at the start of each round.
- [`SpellDeclaredEvent`][osrlib.crawl.events.SpellDeclaredEvent] for each spell declared that
  round.
- [`GroupMovedEvent`][osrlib.crawl.events.GroupMovedEvent] when a group's distance changes.
- [`MemberWithdrewEvent`][osrlib.crawl.events.MemberWithdrewEvent] when a member who withdraws
  alone trades places with a member from a back rank.
- [`MonsterFledEvent`][osrlib.crawl.events.MonsterFledEvent] when a group breaks and runs, and
  when the turned members of a group break away as a new group.
- [`MonstersLeftBehindEvent`][osrlib.crawl.events.MonstersLeftBehindEvent] for helpless members a
  running group leaves behind.
- [`BattleEndedEvent`][osrlib.crawl.events.BattleEndedEvent] with victory, defeat, or flight.

Between them come the events of the rule functions in [`osrlib.core`][osrlib.core]: attacks,
damage, saving throws, initiative, and morale.

A round follows the OSE SRD's combat sequence. The party declares, and initiative decides which side
acts first. In the party's block, the session resolves movement, then missile attacks, then magic,
then melee. In the monster side's block, it checks each group's morale, then resolves the actions
each group's policy chose, in order. Attacks with slow weapons come last, after both blocks. Every
rule is a function in [`osrlib.core.combat`][osrlib.core.combat] or
[`osrlib.core.spells`][osrlib.core.spells], like
[`roll_initiative`][osrlib.core.combat.roll_initiative],
[`resolve_attack`][osrlib.core.combat.resolve_attack],
[`resolve_breath`][osrlib.core.combat.resolve_breath],
[`morale_triggers`][osrlib.core.combat.morale_triggers], and
[`cast_spell`][osrlib.core.spells.cast_spell]. This module adds the order of play, the session state
it passes to each function (the random number streams, the effects ledger, the clock, and the
registry of creatures), the range track, and the party's formation. To resolve one rule without a
session, call those functions directly, as
[Using the rules without a session](https://mmacy.github.io/osrlib-python/guides/rules-without-a-session/)
shows.

Distance in a battle is a range track: each monster group has a separate distance from the party,
in the style of The Bard's Tale. This is an adaptation, a place where osrlib commits to one
reading of a rule the SRD leaves open. The
[adaptations register](https://mmacy.github.io/osrlib-python/adaptations/) lists each one. A group
closes at its encounter movement rate and fights at
[`MELEE_RANGE_FEET`][osrlib.crawl.battle.MELEE_RANGE_FEET]. The party fights in ranks, in
marching order. Under the ruleset's `formation_width_limit` flag, a rank is as wide as the space
the party stands in, divided by
[`FIGHTER_FRONTAGE_FEET`][osrlib.crawl.battle.FIGHTER_FRONTAGE_FEET]: two abreast in a ten-foot
corridor, and in a room, as many as the room's shorter side allows. A front-rank member steps back
out of melee by trading places with a member behind, whom the member names in
[`BattleDeclaration.replacement_id`][osrlib.crawl.commands.BattleDeclaration.replacement_id].

During a round, the session also applies these rules without a command for them:

- A caster who declared a spell loses it if an attack hits them, or they fail a saving throw,
  between initiative and their turn to act.
- Each monster group checks morale when the rules call for it.
- When a monster hits, the session applies the effects its attack lists, each with the save the
  SRD gives: paralysis, poison, mummy rot, energy drain, and petrification. The
  [adaptations register](https://mmacy.github.io/osrlib-python/adaptations/) lists the effects
  the session leaves to the referee.
- A petrifying gaze, like the basilisk's or the medusa's, reaches each member of the party's
  front rank once a round while the gazer's group is in melee. It takes no attack roll, and
  [`resolve_gaze`][osrlib.core.combat.resolve_gaze] skips a member who is averting their eyes.
  A member averts them, or watches a mirror instead, through
  [`BattleDeclaration.gaze_defense`][osrlib.crawl.commands.BattleDeclaration.gaze_defense], and
  the session applies that stance's penalties to the round's attack rolls. In the monsters'
  free round after they surprise the party, a basilisk's gaze reaches every member of the
  party, at any distance.
- Turned undead attack no one. When a cleric turns only some of a group, the turned members
  break away as a new group, which runs, and the rest fight on.
- A member's invisibility ends when the member attacks, throws or uses an item other than a
  potion, or turns undead. [`cast_spell`][osrlib.core.spells.cast_spell] ends it when the member
  casts.
- Each attack on a target protected by *mirror image* removes one image instead, and no attack
  roll is made.
- A protection ward ends when a party member attacks in melee a monster that the ward keeps out.
- The effects of a concentration spell end when the caster declares anything other than `cast`
  or `hold`, when the caster's next spell is disrupted or fizzles, or when the caster casts
  another concentration spell. Turning the undead ends them too, even when the turning never
  resolves.
- The session picks the targets of a party member's area spell without a roll.
  `ceil(span / 10) × width` creatures fit in the area, where `span` is the area's size in feet and
  `width` is the rank width, and the area fills with the target group's living members in the order they were spawned.
  Without the width limit, the whole group fits. A cone's span is its length minus the group's
  distance. Under the ruleset's `aoe_friendly_fire` flag, an area cast at a group in melee range
  also catches the party's front rank, while the area has room. An area aimed at the party, like a
  dragon's breath, covers the first `ceil(span / 10)` of the party's ranks.

An action policy chooses what each monster group does each round.
[`ScriptedPolicy`][osrlib.crawl.battle.ScriptedPolicy] is the default for monsters, and
[`NpcPartyPolicy`][osrlib.crawl.battle.NpcPartyPolicy] is the default for a party of NPC
adventurers. You can write your own [`ActionPolicy`][osrlib.crawl.battle.ActionPolicy] and register
it for a group. A policy draws only from the
[`MONSTER_ACTION_STREAM`][osrlib.crawl.session.MONSTER_ACTION_STREAM], so your policy never changes
an attack or damage roll. When the ruleset is set to roll initiative for each combatant, each side
still acts as a block, and the side with the best single roll acts first, because the SRD's combat
sequence runs per side.

The members of this module, in the order you're likely to need them:

- [`start_battle`][osrlib.crawl.battle.start_battle] opens a battle on the session's encounter.
- [`BattleState`][osrlib.crawl.battle.BattleState] is the battle's state in `session.battle`.
- [`ActionPolicy`][osrlib.crawl.battle.ActionPolicy] is the interface for a policy that chooses a
  monster group's actions, and [`MonsterAction`][osrlib.crawl.battle.MonsterAction] is one chosen
  action.
- [`ScriptedPolicy`][osrlib.crawl.battle.ScriptedPolicy] and
  [`NpcPartyPolicy`][osrlib.crawl.battle.NpcPartyPolicy] are the default policies.
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
from collections.abc import Collection, Mapping, Sequence
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
from osrlib.core.effects import EFFECTS_STREAM, Condition, EffectDefinition, has_condition, kill
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
    MemberWithdrewEvent,
    MonsterFledEvent,
    MonstersLeftBehindEvent,
    SpellDeclaredEvent,
)
from osrlib.data import load_classes, load_spells

__all__ = [
    "ActionPolicy",
    "BattleState",
    "FIGHTER_FRONTAGE_FEET",
    "FLEE_EXIT_FEET",
    "MELEE_RANGE_FEET",
    "MonsterAction",
    "NPC_PARTY_MORALE",
    "NpcPartyPolicy",
    "ScriptedPolicy",
    "start_battle",
]


def _int_param(params: Mapping[str, Any], key: str, default: int = 0) -> int:
    """Read an integer param out of schema-validated data whose union type the type checker can't index by key name."""
    return int(params.get(key, default))


MELEE_RANGE_FEET = 5
"""The distance, in feet, at which a monster group is close enough to fight in melee.

A group closing on the party stops at this distance, not at zero. The session rejects a melee
attack declared against a group farther away with `combat.attack.out_of_reach`, and every melee
attack resolves at this distance. When pursuers catch the party in a chase, the battle starts with
every group at this distance.

The range track has no distance between this and zero, so treat the value as fixed: the session uses
it for every reach and range check in a battle. A party member attacks a group farther away only
with a missile weapon, and [`validate_attack`][osrlib.core.combat.validate_attack] checks the
distance against that weapon's ranges.
"""

FLEE_EXIT_FEET = 120
"""How far, in feet, a routed group runs before it leaves the battle.

A group that fails a morale check turns and runs its full movement rate each round. Once its
distance is greater than this, the session marks the group fled. A fled group takes no further
action, the session rejects every declaration aimed at it with
`battle.declaration.unknown_group`, and a battle in which every group has fled or died ends in
victory. Until the group gets this far, the party can still chase it or shoot at it. A group whose
living members are all afraid or turned, but which didn't fail a morale check, also counts as gone
for ending the battle once it's farther away than this.
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
    [`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty] and
    [`PlaceParty`][osrlib.crawl.commands.PlaceParty] are the exceptions. The session rejects them
    with `session.command.encounter_in_progress` while an encounter is open, so an NPC party can't
    join a fight, and the party can't be moved out of one.
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
    """The rounds the monsters still have to sit out because the party surprised them. In each round
    while this is above zero, the monsters do nothing, and the count goes down by one."""
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
    maintains one at a time. The effects end when that caster declares anything other than `cast` or
    `hold`, `turn_undead` included, when the caster's next spell is disrupted or fizzles, or when the
    caster casts another concentration spell."""


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

    - `"close"` moves the whole group toward the party by one encounter movement rate, stopping at
      [`MELEE_RANGE_FEET`][osrlib.crawl.battle.MELEE_RANGE_FEET]. Only the first `"close"` in a
      group's actions for a round moves the group.
    - `"melee"` attacks `target_id`.
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

    Any routine can be named, whatever its
    [`use`][osrlib.core.monsters.AttackRoutine.use]. Battle rolls the attacks of a `PRIMARY`,
    `ALTERNATIVE`, or `SITUATIONAL` routine, and rolls nothing for a `SPECIAL` one. An index the
    template doesn't have, a negative one included, makes the action impossible, so the session
    skips it. An NPC adventurer fights with its weapon and ignores this field."""


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
        their spells then, as the party does. The session doesn't call it for a group that has fled
        or has no living members, for a monster group that is running because it failed morale or
        because its living members are all turned or afraid, or for any group while
        [`BattleState.monsters_hold_rounds`][osrlib.crawl.battle.BattleState.monsters_hold_rounds]
        is above zero. The group you choose for has no living turned members, because the session
        moves them into a separate group before it calls your policy.

        Args:
            session (osrlib.crawl.session.GameSession): The running session. Read the party, the
                registry, and the effects ledger from it, and don't change it.
            group (osrlib.crawl.encounter.EncounterGroup): The group to choose for. Its
                `distance_feet` is its current distance from the party, and its `monster_ids` are its
                members, dead ones included.
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
    `session.action_policies`. You construct one yourself only to wrap it in a policy of your own.

    A monster with a breath weapon uses it while the party is within the breath's length. When the
    breath weapon has a number of uses per day, the monster breathes if it hasn't breathed yet that
    day. After that, it breathes or attacks with equal chance while uses remain, which is how the OSE
    SRD's dragons fight. When the breath weapon works on a chance in six, as the hellhound's does, the policy rolls
    that chance each round. Otherwise, a group farther away than
    [`MELEE_RANGE_FEET`][osrlib.crawl.battle.MELEE_RANGE_FEET] closes. Once in range, each monster
    picks a target at random from the party's front rank, leaving out invisible members and members
    whose *protection from evil* bars that monster. When no target is left, the monster holds.

    The monster then picks which of its attack routines to use, with equal chance among the routines
    whose [`use`][osrlib.core.monsters.AttackRoutine.use] is `PRIMARY` or `ALTERNATIVE`, and names
    it in [`MonsterAction.routine`][osrlib.crawl.battle.MonsterAction.routine]. A thoul claws or
    swings a weapon, and a mule kicks or bites. The policy never picks a `SITUATIONAL` or `SPECIAL`
    routine, such as an elephant's trample or a vampire's gaze. A monster with only one routine to
    pick from draws nothing for it.

    The monster data has no ranges for monsters' missile attacks, so osrlib treats them like melee:
    the monster closes first, then attacks. This is an adaptation, a place where osrlib commits to
    one reading of a rule the SRD leaves open. The
    [adaptations register](https://mmacy.github.io/osrlib-python/adaptations/) lists each one.
    Monsters under this policy never cast spells, because the monster data doesn't cover monster
    spellcasting, which is the referee's call.

    Write a policy of your own when you want a group to hold a chokepoint, focus its attacks, back
    off, cast, or use a situational routine when your game decides its situation applies. To keep
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
            One [`MonsterAction`][osrlib.crawl.battle.MonsterAction] for each living monster that can
            act, in the group's member order. Incapacitated and confused monsters get none, because
            the session resolves confusion itself.
        """
        actions: list[MonsterAction] = []
        pool = _party_target_pool(session)
        for monster in _living_monsters(session, group):
            if incapacitated(monster) or has_condition(monster, Condition.CONFUSED):
                continue  # the round handler resolves confusion, not the policy
            breath = monster.template.ability("breath_weapon")
            if breath is not None and _breath_usable(monster, group.distance_feet):
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
            if group.distance_feet > MELEE_RANGE_FEET:
                actions.append(MonsterAction(monster_id=monster.id, kind="close"))
                continue
            targets = _reachable_targets(session, monster, pool)
            if not targets:
                actions.append(MonsterAction(monster_id=monster.id, kind="hold"))
                continue
            target = targets[stream.randbelow(len(targets))]
            routine = _pick_routine(monster, stream)
            actions.append(MonsterAction(monster_id=monster.id, kind="melee", target_id=target.id, routine=routine))
        return actions


class NpcPartyPolicy:
    """The default [`ActionPolicy`][osrlib.crawl.battle.ActionPolicy] for a group of NPC adventurers.

    The session uses this policy for a group of NPC adventurers, rather than monsters, unless you
    register another one for it in `session.action_policies`. The OSE SRD gives no tactics for a
    party of adventurers on the other side, so osrlib supplies these. This is an adaptation, a place
    where osrlib commits to one reading of a rule the SRD leaves open. The
    [adaptations register](https://mmacy.github.io/osrlib-python/adaptations/) lists each one.

    Each living member takes the first of these actions that applies:

    1. A caster with a memorized healing spell heals the group's most wounded member who is below
       half their hit points: the one with the lowest ratio of current to maximum hit points, with
       ties broken by id.
    2. A member below half their hit points, in a group with no healing spell left, drinks a
       healing potion they carry. This is the only item these tactics use.
    3. A caster with a memorized damage spell that osrlib resolves without a referee casts it at
       the party. The caster picks the highest-level spell, with ties broken by spell id. An area
       spell catches the party's ranks from the front, as many ranks as its size covers. For a
       spell with one target, the caster picks a member at random from the party's front rank,
       leaving out invisible members.
    4. A member with a missile weapon shoots while the group is farther away than
       [`MELEE_RANGE_FEET`][osrlib.crawl.battle.MELEE_RANGE_FEET].
    5. Otherwise, the member closes and attacks in melee, the same as a monster.

    NPCs declare their spells at the start of the round. An attack that hits or a failed saving
    throw can disrupt an NPC's spell, as it can the party's, because the SRD's rule applies to both
    sides. Every random choice comes from the
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
            One [`MonsterAction`][osrlib.crawl.battle.MonsterAction] for each living member that can
            act, in the group's member order. Incapacitated and confused members get none.
        """
        from osrlib.data import load_spells

        actions: list[MonsterAction] = []
        living = _living_monsters(session, group)
        pool = _party_target_pool(session)
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
                if targeting is not None and targeting.mode is not TargetingMode.AREA and pool:
                    target_id = pool[stream.randbelow(len(pool))].id
                actions.append(
                    MonsterAction(
                        monster_id=npc.id, kind="npc_cast", target_id=target_id, spell_id=spell.id, spell_mode=mode
                    )
                )
                continue
            if group.distance_feet > MELEE_RANGE_FEET:
                if _npc_wielded(npc, missile=True, distance_feet=group.distance_feet) is not None and pool:
                    target = pool[stream.randbelow(len(pool))]
                    actions.append(MonsterAction(monster_id=npc.id, kind="npc_shoot", target_id=target.id))
                else:
                    actions.append(MonsterAction(monster_id=npc.id, kind="close"))
                continue
            targets = _reachable_targets(session, npc, pool)
            if not targets:
                actions.append(MonsterAction(monster_id=npc.id, kind="hold"))
                continue
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


def _npc_wielded(npc, *, missile: bool, distance_feet: int = MELEE_RANGE_FEET):
    """The NPC's first wielded weapon that fits the range, either a template or a magic instance."""
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


_PICKED_ROUTINE_USES = frozenset({RoutineUse.PRIMARY, RoutineUse.ALTERNATIVE})
"""The routines `ScriptedPolicy` picks among. A situational or special routine is left to a game's own policy."""


def _pick_routine(monster: MonsterInstance, stream) -> int | None:
    """Pick the routine a melee action uses, with equal chance among the monster's primary and alternative routines.

    A monster with one such routine draws nothing and gets None, which resolves its first routine, so
    the stream advances only for a monster that has a choice to make.
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


FIGHTER_FRONTAGE_FEET = 5
"""The width, in feet, that one combatant needs to fight side by side with the next.

The party's front rank is as wide as the space it stands in, divided by this, so two fit abreast in
a ten-foot corridor, and a room fits as many as its shorter side allows. The session rejects a
melee attack declared by a member outside the front rank with
`battle.declaration.not_in_front_rank`. The rank check doesn't apply to missile attacks, so the back
ranks can still shoot.

The SRD gives one number for this and leaves the rest to the referee: "The referee should judge
the number of opponents that can attack a single combatant, bearing in mind the combatant's size
and the available space around them. **10' passage:** Enough space for at most 2-3 characters to
fight side-by-side." osrlib uses the low end of that range, two in ten feet, or five feet each,
and applies it to the space the party is standing in.

To play without a width limit, turn off the ruleset's `formation_width_limit` flag, which puts every
living member in the front rank. Changing this constant instead would change the width of every
space in the game.
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
    width = _formation_width(session)
    living = session.party.living_members()
    if width is None:
        return [living] if living else []
    return session.party.ranks(width)


def _party_front_rank(session) -> list:
    ranks = _party_ranks(session)
    return ranks[0] if ranks else []


def _party_target_pool(session) -> list:
    """The monsters' melee pool: the party front rank, invisible members excluded."""
    return [member for member in _party_front_rank(session) if not has_condition(member, Condition.INVISIBLE)]


def _ally_protection_bonus(session, defender) -> int:
    """The Ring of Protection 5' Radius: a rank-mate's ring shields the defender.

    The battle space has no adjacency finer than the rank, so "allies within 5'"
    is the wearer's rank. The wearer's +1 is counted with the rest of its
    equipped items, so this bonus comes only from allies' rings, and multiple
    rings never stack.

    Args:
        session (osrlib.crawl.session.GameSession): The battle's session.
        defender: The combatant under attack. A combatant in no rank gets no
            bonus.

    Returns:
        1 when a living rank-mate wears the radius ring, else 0.
    """
    for rank in _party_ranks(session):
        if defender not in rank:
            continue
        for ally in rank:
            if ally is defender:
                continue
            for ring in ally.inventory.rings:
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
score of 9, a group passes the check on a 2d6 total of 9 or less, after modifiers, and breaks on a
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


# ---------------------------------------------------------------------- start and end


def start_battle(session, *, party_free_round: bool = False, monsters_free_round: bool = False) -> list[Event]:
    """Start a battle on the session's open encounter and put the session in `battle` mode.

    You rarely call this yourself. The session calls it when the monsters attack and when a chase
    catches the party, and [`EngageBattle`][osrlib.crawl.commands.EngageBattle] calls it when the
    party chooses to fight. If you only execute commands, you never call it. Execute
    `EngageBattle` instead whenever you can. The command log records the command, so
    [`replay_game`][osrlib.persistence.replay_game] reproduces the fight, and the command works out
    the surprise arguments and a fight in the middle of a chase for you. A direct call changes the
    session outside any command. [`save_game`][osrlib.persistence.save_game] still saves the result,
    but a replay of the command log doesn't reproduce it. Call this function only from code that
    runs a procedure outside the session's commands, like a test of combat.

    The session needs an open encounter, because the battle uses that encounter's groups and their
    distances. When this function returns, the session is in `battle` mode and `session.battle`
    contains a [`BattleState`][osrlib.crawl.battle.BattleState]. From there, each round is one
    [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound] command.

    A group whose morale score is 2 runs away as soon as the battle starts, as the SRD's morale
    rule says, and that can end the battle before any round. In a group that a cleric turned only
    in part, the turned members break away as a new group, which runs from the first round. A
    side that surprised the other gets one free round. The monsters' free round runs inside this
    call, before the party can act. On the party's free round, the monsters do nothing during the
    first `ResolveBattleRound`.

    Args:
        session (osrlib.crawl.session.GameSession): The running session, with `session.encounter` set.
        party_free_round: True when the monsters were surprised. The monsters then sit out the
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

        # The party caught the goblins flat-footed, so it gets the first round free.
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

    Defeat ends the battle and does nothing else. The session's wipe check makes
    the transition to `game_over` and posts the ending event, so a party lost to a
    blade trap and a party lost to ogres end the same way.
    """
    from osrlib.crawl import encounter as encounter_module

    if not session.party.living_members():
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

        session.battle = None
        # Only a group still willing and able to run chases: not the broken and
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
        session.encounter.pursuit = PursuitState(gap_feet=min(group.distance_feet for group in pursuers))
        return [BattleEndedEvent(code="battle.ended.fled")]
    return None


def _all_routed(session, group) -> bool:
    living = _living_monsters(session, group)
    return (
        bool(living)
        and all(
            has_condition(monster, Condition.TURNED) or has_condition(monster, Condition.AFRAID) for monster in living
        )
        and group.distance_feet > FLEE_EXIT_FEET
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


def _with_default_weapon(session, declaration: BattleDeclaration, member) -> BattleDeclaration:
    """Fill in the weapon of an `attack` that names none, as `BattleDeclaration.weapon_id` describes.

    The group's distance decides the attack: melee at 5' or less, missile beyond. The weapon is the first
    wielded item, in equip order, with that quality, and a real weapon comes before an improvised one: a gear
    item whose stats come from its `combat` facet, like a torch. So a member who lit a torch and then drew a
    sword swings the sword, and the torch counts only when nothing else suits the attack. For a missile
    attack it's the first of those whose long range reaches the group, and the first missile weapon only
    when none reaches. A splash weapon never counts, because a `use_item` declaration throws it, and neither
    does an item with no weapon stats, like a wand. The session calls this once, when it
    accepts the round, so the rest of the round reads the chosen weapon as if the player had named it.

    Returns:
        The declaration with `weapon_id` set to the chosen weapon's id: a magic weapon's instance id or a
        mundane weapon's catalog id. The declaration itself when it isn't an `attack`, already names a weapon,
        targets no group the battle still has, or the member wields nothing that suits the attack, which
        leaves the attack unarmed.
    """
    if declaration.action != "attack" or declaration.weapon_id is not None:
        return declaration
    group = _group_by_id(session, declaration.target_group_id)
    if group is None or group.fled:
        return declaration
    missile = group.distance_feet > MELEE_RANGE_FEET
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
            if ranges is None or group.distance_feet <= ranges.long.max_feet:
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
"""The SRD's two ways out of melee, the `move` values a member makes either with the formation or alone.

When every declarer makes the same one, the whole formation makes it. Otherwise each is the
declaring member's own: a swap with a member from a back rank. `close` is not one of them, because
it needs no agreement: the first `close` advances the formation whenever the round is accepted.
"""

_RANK_DEPTH_FEET = 5
"""How far apart the party's ranks stand, front to back, in feet.

A member who withdraws alone and the replacement who steps up into their place each cover this
distance once for every rank between them, so a replacement two ranks back is ten feet away.
"""


class _Withdrawal(NamedTuple):
    """A fighting withdrawal or a retreat that one member makes alone, as the movement step makes it."""

    member_id: str
    """The front-rank member who steps back."""
    replacement_id: str
    """The back-rank member who steps up into their place."""
    move: str
    """`fighting_withdrawal` or `retreat`, which picks the event's code."""


def _formation_move(declarations: Sequence[BattleDeclaration]) -> str | None:
    """Return the defensive move the whole formation makes this round, or `None` when it makes none.

    The formation makes a `fighting_withdrawal` or a `retreat` when every declaration is that same
    move. A round whose only declarer declares one is a formation move too, because everyone agreed.
    A member who can't declare at all, being dead or incapacitated, doesn't count. Any other
    defensive move is the declaring member's own (see `_plan_withdrawals`).
    """
    moves = [declaration.move if declaration.action == "move" else None for declaration in declarations]
    first = moves[0] if moves else None
    if first in _DEFENSIVE_MOVES and all(move == first for move in moves):
        return first
    return None


def _plan_withdrawals(
    session, declarers, declarations: Sequence[BattleDeclaration]
) -> tuple[list[Rejection], list[_Withdrawal]]:
    """Judge each defensive move a member makes alone, and plan the swaps the movement step makes.

    A `fighting_withdrawal` or a `retreat` that not every declarer makes is the declaring member's
    own (see the [adaptations register](https://mmacy.github.io/osrlib-python/adaptations/), under
    the battle round). The member has to stand in the front rank and name a replacement who can step
    up (see `_can_step_up`). Both members have to cover the distance between their ranks: the
    withdrawing member at up to half their encounter rate for a fighting withdrawal or their full
    rate for a retreat, and the replacement at up to their full rate, each doubled by their own
    *haste*. Each withdrawing member gets the first of the four rejections that applies, in the
    order this function checks them.

    In a formation move every declarer is withdrawing, so nobody is free to step up, and a
    declaration in it that names a replacement is refused with
    `battle.declaration.replacement_unavailable`.

    The checks read state and take no draw, so a refused round costs nothing.

    Args:
        session (osrlib.crawl.session.GameSession): The battle's session.
        declarers: The living, able members who have to declare this round, in marching order.
        declarations: The round's declarations, one per declarer.

    Returns:
        The rejections, in marching order, and the swaps of the members who withdraw alone, in
            marching order of the withdrawing members. The swaps stand only when there are no
            rejections.
    """
    by_id = {declaration.character_id: declaration for declaration in declarations}
    rejections: list[Rejection] = []
    if _formation_move(declarations) is not None:
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
    rank_of = {member.id: index for index, rank in enumerate(_party_ranks(session)) for member in rank}
    able = {member.id for member in declarers}
    named: set[str] = set()
    plan: list[_Withdrawal] = []
    for member in declarers:
        declaration = by_id[member.id]
        if declaration.action != "move" or declaration.move not in _DEFENSIVE_MOVES:
            continue
        if rank_of[member.id] != 0:
            rejections.append(Rejection(code="battle.declaration.not_in_front_rank", params={"character": member.id}))
            continue
        replacement_id = declaration.replacement_id
        if replacement_id is None:
            rejections.append(Rejection(code="battle.declaration.no_replacement", params={"character": member.id}))
            continue
        claimed = replacement_id in named
        named.add(replacement_id)
        if claimed or not _can_step_up(session, replacement_id, rank_of, able, by_id):
            rejections.append(
                Rejection(
                    code="battle.declaration.replacement_unavailable",
                    params={"character": member.id, "replacement": replacement_id},
                )
            )
            continue
        distance = rank_of[replacement_id] * _RANK_DEPTH_FEET
        own_rate = _encounter_rate(member, session)
        if declaration.move == "fighting_withdrawal":
            own_rate //= 2
        own_reach = own_rate * _haste_multiplier(session, member.id, "movement_multiplier")
        replacement = session.member(replacement_id)
        their_reach = _encounter_rate(replacement, session) * _haste_multiplier(
            session, replacement_id, "movement_multiplier"
        )
        if distance > min(own_reach, their_reach):
            rejections.append(
                Rejection(
                    code="battle.declaration.replacement_too_far",
                    params={"character": member.id, "replacement": replacement_id, "distance_feet": distance},
                )
            )
            continue
        plan.append(_Withdrawal(member_id=member.id, replacement_id=replacement_id, move=declaration.move))
    return rejections, plan


def _can_step_up(
    session,
    replacement_id: str,
    rank_of: Mapping[str, int],
    able: set[str],
    by_id: Mapping[str, BattleDeclaration],
) -> bool:
    """Whether a named replacement can step up into the front rank this round.

    The replacement has to be a living member behind the front rank, so an unknown id, a dead
    member, and a member of the front rank are all out. They have to be able to declare, to move,
    and to act on the party's behalf (see `_acts_for_party`). Their own declaration can't be a
    `cast`, because "The spell caster cannot move and cast a spell in the same round", or a
    defensive move, which takes a member out of the front rank, not into it.
    """
    if rank_of.get(replacement_id, 0) == 0 or replacement_id not in able:
        return False
    replacement = session.member(replacement_id)
    if cannot_move(replacement) or not _acts_for_party(replacement):
        return False
    declaration = by_id[replacement_id]
    if declaration.action == "cast":
        return False
    return not (declaration.action == "move" and declaration.move in _DEFENSIVE_MOVES)


def _acts_for_party(member) -> bool:
    """Whether a member can still act on the party's behalf, as a replacement who steps up has to.

    An incapacitated member can't act at all. A charmed member acts for the charmer, and a confused
    member acts on a behavior roll, so neither steps up into the front rank for the party. Both the
    validation pre-phase and the movement step ask this, so a replacement who is already charmed or
    confused is refused when the round is declared, and one who becomes so before the movement step
    stays where they are.
    """
    return not (
        incapacitated(member) or has_condition(member, Condition.CHARMED) or has_condition(member, Condition.CONFUSED)
    )


def _projected_front_rank(session, plan: Sequence[_Withdrawal]) -> set[str]:
    """The ids of the front rank as the round's planned swaps leave it, for judging melee declarations.

    A replacement stands in the front rank from the movement step on, so they may declare a melee
    attack in the round they step up. The members the swaps leave in place keep their ranks.
    """
    order = [member.id for member in session.party.living_members()]
    for swap in plan:
        front, back = order.index(swap.member_id), order.index(swap.replacement_id)
        order[front], order[back] = order[back], order[front]
    width = _formation_width(session)
    return set(order if width is None else order[:width])


def _validate_declaration(
    session, declaration: BattleDeclaration, member, *, front_rank: set[str] | None = None
) -> list[Rejection]:
    """Judge one declaration against the state it refers to, and return every reason it cannot stand.

    This runs twice on the declarations the round accepts. Once in the validation pre-phase, where a
    rejection refuses the whole command, and again in the magic phase, immediately before a `cast`, a
    `turn_undead`, or a device or scroll `use_item` resolves, because the phases between the two can change
    what these checks read. It reads state and takes no draw, so running it a second time costs nothing and
    changes nothing.

    An `attack` with no `weapon_id` is judged with the weapon that `BattleDeclaration.weapon_id` says the
    session chooses, from the group's distance when the round is accepted. `_handle_resolve_battle_round`
    fills that weapon in with `_with_default_weapon` before it calls this.

    The pre-phase passes `front_rank`, the ids of the front rank as the round's swaps will leave it
    (see `_projected_front_rank`), so a replacement who steps up may declare a melee attack. Without
    it, a melee attack is judged against the front rank as it stands.
    """
    state = session.battle
    if declaration.action == "hold":
        return []
    if declaration.action == "move":
        if declaration.move is None:
            return [Rejection(code="battle.declaration.missing_move", params={"character": member.id})]
        if declaration.move == "close":
            group = _group_by_id(session, declaration.target_group_id)
            if group is None or group.fled:
                return [Rejection(code="battle.declaration.unknown_group", params={"character": member.id})]
        from osrlib.core.combat import cannot_move

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
        missile = _is_missile_declaration(weapon, group.distance_feet)
        if not missile:
            width = _formation_width(session)
            if front_rank is None:
                front_rank = {front.id for front in _party_front_rank(session)}
            if width is not None and member.id not in front_rank:
                return [Rejection(code="battle.declaration.not_in_front_rank", params={"character": member.id})]
            if group.distance_feet > MELEE_RANGE_FEET:
                return [Rejection(code="combat.attack.out_of_reach", params={"distance_feet": group.distance_feet})]
        context = AttackContext(
            distance_feet=group.distance_feet if missile else MELEE_RANGE_FEET,
            fired_last_round=member.id in state.fired_last_round,
        )
        combat_facet = getattr(weapon, "combat", None)
        attack = combat_facet if combat_facet is not None else weapon
        return validate_attack(member, pool[0], attack, context, ruleset=session.ruleset)
    if declaration.action == "use_item":
        from osrlib.crawl import exploration

        magic = member.inventory.magic_item(declaration.item_id) if declaration.item_id else None
        if magic is not None:
            return _validate_magic_item_declaration(session, declaration, member, magic)
        group = _group_by_id(session, declaration.target_group_id)
        if group is None or group.fled:
            return [Rejection(code="battle.declaration.unknown_group", params={"character": member.id})]
        instance = exploration._find_item(member, declaration.item_id) if declaration.item_id else None
        if instance is None or getattr(instance.template, "combat", None) is None:
            return [Rejection(code="battle.declaration.item_unusable", params={"item": declaration.item_id or ""})]
        pool = _monster_pool(session, group)
        if not pool:
            return [Rejection(code="battle.declaration.no_target", params={"group": group.id})]
        context = AttackContext(distance_feet=group.distance_feet, lit=True)
        return validate_attack(member, pool[0], instance.template, context, ruleset=session.ruleset)
    if declaration.action == "turn_undead":
        return validate_turn_undead(member, load_classes().get(member.class_id))
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

    A cast in an `area` mode aims at `target_group_id`, and its targets are the group's living members
    that fit the area footprint, followed by the party's front rank when `aoe_friendly_fire` applies.
    A cast in an `hd_budget` mode with a `target_group_id` and no `targets` takes the group's living
    members in spawn order, and the kernel spends the budget on them weakest first. A mode with no
    area, like *sleep*, takes every living member. That's the same cast as naming those members one by
    one, so a front end with only the player view's group ids can cast *sleep*. A mode with an area,
    like *death spell*'s 60' cube, takes only the candidates the area footprint takes, as an `area`
    mode does. When both are set, the cast uses the named `targets`. For a
    group that isn't in the encounter, or that has fled, this returns a
    `battle.declaration.unknown_group` rejection.
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
            candidates = _area_candidates(session, group, targeting.shape, targeting.dimensions)
        else:
            candidates = _living_monsters(session, group)
        return candidates, group.distance_feet, []
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
                    distance = max(distance or 0, group.distance_feet)
    return targets, distance, []


def _area_span_feet(shape: str | None, dimensions: dict, gap_feet: int) -> int:
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


def _area_candidates(session, group, shape: str | None, dimensions: dict) -> list:
    """Fill an area's capacity in stable spawn order. Friendly fire appends the party's front rank."""
    span = _area_span_feet(shape, dimensions, group.distance_feet)
    width = _formation_width(session)
    if width is None:
        capacity = 10**9
    else:
        capacity = math.ceil(span / 10) * width
    candidates: list = list(_living_monsters(session, group))[:capacity]
    if session.ruleset.aoe_friendly_fire and group.distance_feet <= MELEE_RANGE_FEET and len(candidates) < capacity:
        for member in _party_front_rank(session):
            if len(candidates) >= capacity:
                break
            candidates.append(member)
    return candidates


def _validate_magic_item_declaration(session, declaration: BattleDeclaration, member, instance) -> list[Rejection]:
    """The `use_item` declaration widened: potions, scrolls, and devices (magic phase).

    A potion targets the drinker. Wands, staves, and rods target a group and
    resolve in the magic phase alongside casts, because they unleash magical
    effects and resolving them there keeps the missile and melee ordering clean.
    A scroll read resolves in the magic phase too, through the declaration's
    spell fields.

    For a scroll or a device this runs twice, as `_validate_declaration`'s `cast` branch does: once in the
    validation pre-phase, and again in the magic phase immediately before the read or the use resolves,
    because the phases between the two can change what these checks read. It reads state and takes no draw,
    so the second run costs nothing and changes nothing.
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
                if group.distance_feet > MELEE_RANGE_FEET:
                    return [Rejection(code="combat.attack.out_of_reach", params={"distance_feet": group.distance_feet})]
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
    context = AttackContext(distance_feet=MELEE_RANGE_FEET)
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


def _handle_resolve_battle_round(session, command: ResolveBattleRound) -> tuple[list[Rejection], list[Event]]:
    state = session.battle
    if state is None:
        return [Rejection(code="battle.none_active")], []
    declarers = _able_declarers(session)
    declared_ids = [declaration.character_id for declaration in command.declarations]
    rejections: list[Rejection] = []
    if sorted(declared_ids) != sorted(member.id for member in declarers):
        rejections.append(
            Rejection(
                code="battle.declaration.roster_mismatch",
                params={"expected": tuple(member.id for member in declarers), "declared": tuple(declared_ids)},
            )
        )
        return rejections, []
    by_member = {}
    for declaration in command.declarations:
        member = session.member(declaration.character_id)
        # The default weapon is chosen once, here, so the round plays out as if the player had named it.
        # The command log keeps the declaration as it was sent.
        by_member[declaration.character_id] = (member, _with_default_weapon(session, declaration, member))
    declarations = [declaration for _, declaration in by_member.values()]
    withdrawal_rejections, swaps = _plan_withdrawals(session, declarers, declarations)
    front_rank = _projected_front_rank(session, swaps)
    for member, declaration in by_member.values():
        rejections.extend(_validate_declaration(session, declaration, member, front_rank=front_rank))
    rejections.extend(withdrawal_rejections)
    if rejections:
        # The session rejects the whole command and lists every rejection. Partial
        # acceptance would complicate replay (see the adaptations register).
        return rejections, []

    state.round += 1
    events: list[Event] = [BattleRoundEvent(round=state.round)]

    # Declarations post: spells are table-visible per RAW.
    pending_casters: dict[str, object] = {}
    for member, declaration in by_member.values():
        if declaration.action == "cast":
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
    formation = _formation_move(declarations)
    retreating = _retreating_members(session, declarations, formation)
    # The members who stepped back out of the front rank this round, and the replacements
    # whose swap didn't happen. The movement step fills both.
    withdrawn: set[str] = set()
    stranded: set[str] = set()
    slow_attacks: list[tuple[Creature, BattleDeclaration]] = []

    def party_block() -> list[Event]:
        block: list[Event] = []
        # The movement step: the swaps of members who withdraw alone, then the formation's move.
        swapped = _swap_ranks(session, swaps)
        block.extend(swapped)
        block.extend(_party_movement(session, by_member))
        withdrawn.update(event.character_id for event in swapped)
        stranded.update(swap.replacement_id for swap in swaps)
        stranded.difference_update(event.replacement_id for event in swapped)
        block.extend(
            _party_attacks(
                session,
                by_member,
                missile=True,
                slow_attacks=slow_attacks,
                fired=fired_this_round,
                fire_damaged=fire_damaged_groups,
                stranded=stranded,
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
                stranded=stranded,
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
            withdrawn=withdrawn,
            npc_actions=npc_actions,
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
        for member, declaration in slow_attacks:
            if incapacitated(member):
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
    The exposure starts when the round is declared, so it holds against a side that acts before the
    party moves. A formation retreat moves the whole party off, so it exposes every member. Otherwise
    it exposes each member who retreats alone, and no one else.
    """
    if formation == "retreat":
        return frozenset(member.id for member in session.party.members if member.id is not None)
    return frozenset(
        declaration.character_id
        for declaration in declarations
        if declaration.action == "move" and declaration.move == "retreat"
    )


def _party_movement(session, by_member) -> list[Event]:
    """Move the formation: its defensive move, or else the first `close`.

    A defensive move that every declarer makes moves the whole formation (see the
    [adaptations register](https://mmacy.github.io/osrlib-python/adaptations/), under the battle
    round). Every member retreating moves the party off at the full encounter rate, the OSE SRD's
    "full encounter movement rate", and the running pursuit begins once the battle converts. Every
    member declaring a fighting withdrawal backs the party off at half encounter rate, and that
    declaration is a move on its own, so the withdrawing party attacks nobody that round.

    Otherwise the first `close` in the round's declarations advances the formation on its named
    group at encounter rate, stopping at 5'. The members who withdraw alone have already traded places
    with their replacements by then, earlier in the same movement step (see `_swap_ranks`).
    """
    declarations = [declaration for _, declaration in by_member.values()]
    events: list[Event] = []
    multiplier = _party_move_multiplier(session)
    formation = _formation_move(declarations)
    if formation is not None:
        rates = [_encounter_rate(member, session) for member in session.party.living_members()]
        slowest = min(rates, default=0)
        rate = (slowest if formation == "retreat" else slowest // 2) * multiplier
        for group in session.encounter.groups:
            if group.fled:
                continue
            group.distance_feet += rate
            events.append(GroupMovedEvent(group_id=group.id, distance_feet=group.distance_feet))
        return events
    for _member, declaration in by_member.values():
        if declaration.action == "move" and declaration.move == "close":
            group = _group_by_id(session, declaration.target_group_id)
            if group is None or group.fled:
                continue
            rates = [_encounter_rate(living, session) for living in session.party.living_members()]
            rate = min(rates, default=0) * multiplier
            group.distance_feet = max(MELEE_RANGE_FEET, group.distance_feet - rate)
            events.append(GroupMovedEvent(group_id=group.id, distance_feet=group.distance_feet))
            break
    return events


def _swap_ranks(session, swaps: Sequence[_Withdrawal]) -> list[MemberWithdrewEvent]:
    """Trade each withdrawing member's place in the marching order with their replacement's.

    The swaps run in the marching order of the withdrawing members, as `_plan_withdrawals` planned
    them. A swap happens only if, when the movement step comes, the withdrawing member still stands
    in the front rank, the replacement still stands behind it, both can still move, and the
    replacement can still act on the party's behalf (see `_acts_for_party`), which a member who is
    incapacitated, charmed, or confused can't. A side that acted first can change any of that.
    Otherwise the two stay where they are and nothing reports the swap. The order stays changed
    after the round. A swap takes no draw. The party block runs this at the start of its movement
    step, before `_party_movement` moves the formation.

    Returns:
        A `MemberWithdrewEvent` for each swap that happened.
    """
    events: list[MemberWithdrewEvent] = []
    for swap in swaps:
        ranks = _party_ranks(session)
        front = {member.id for member in ranks[0]} if ranks else set()
        behind = {member.id for rank in ranks[1:] for member in rank}
        if swap.member_id not in front or swap.replacement_id not in behind:
            continue
        replacement = session.member(swap.replacement_id)
        if cannot_move(session.member(swap.member_id)) or cannot_move(replacement):
            continue
        if not _acts_for_party(replacement):
            continue
        members = list(session.party.members)
        ids = [member.id for member in members]
        here, there = ids.index(swap.member_id), ids.index(swap.replacement_id)
        members[here], members[there] = members[there], members[here]
        session.party.members = members
        code = "battle.member.retreated" if swap.move == "retreat" else "battle.member.withdrew"
        events.append(MemberWithdrewEvent(code=code, character_id=swap.member_id, replacement_id=swap.replacement_id))
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
    session, by_member, *, missile: bool, slow_attacks, fired, fire_damaged, stranded: Collection[str] = ()
) -> list[Event]:
    """Resolve the party's missile or melee attacks, and queue the slow ones for the end of the round.

    `stranded` names the replacements whose swap didn't happen. They declared a melee attack from the
    front rank they were to step into, so they never reach it and their melee attack doesn't resolve.
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
        is_missile = _is_missile_declaration(weapon, group.distance_feet)
        if is_missile != missile:
            continue
        if not is_missile and member.id in stranded:
            continue
        combat_facet = getattr(weapon, "combat", None)
        facet = combat_facet if combat_facet is not None else weapon
        if facet is not None and WeaponQuality.SLOW in getattr(facet, "qualities", ()):
            slow_attacks.append((member, declaration))
            continue
        events.extend(_resolve_party_attack(session, member, declaration, fired, fire_damaged))
    return events


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
    missile = _is_missile_declaration(weapon, group.distance_feet)
    events: list[Event] = []
    swings = _haste_multiplier(session, member.id, "attacks_multiplier")
    for _ in range(swings):
        pool = _monster_pool(session, group)
        if not pool:
            break
        target = pool[0]  # the first living, visible monster in the reachable rank
        context = AttackContext(
            distance_feet=group.distance_feet if missile else MELEE_RANGE_FEET,
            fired_last_round=member.id in state.fired_last_round,
            defender_retreating=group.fleeing,
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
    context = AttackContext(distance_feet=group.distance_feet, lit=True)
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

    A caster the round already disrupted is reported as disrupted and takes no further part, and that
    check runs before the re-check below, so a caster who was both hit and silenced reports
    `magic.cast.disrupted` rather than `magic.cast.fizzled`. The disruption comes from the attack that
    hit, so it's the outcome the players saw.

    A cast, a scroll read inside `_resolve_scroll_cast`, a device use (a wand, staff, or rod), and a turning
    are each judged again immediately before they resolve, with the checks they passed at the top of the
    round. One that now fails any of them fizzles instead of resolving, and the re-check takes no draw, so a
    declaration that passes it resolves with the same draws in the same order as before. A potion has no
    check to fail.

    A fizzled cast or scroll read reports a `SpellDisruptedEvent` at `magic.cast.fizzled`, and the caster loses
    the memorized copy, or the scroll its spell, as a disruption loses them. A fizzled device use reports a
    `DeclarationFizzledEvent` at `magic.device.fizzled` with the declared instance id as `item_id`, and a fizzled
    turning reports one at `magic.turning.fizzled` with no `item_id`. Each event's `reason` is the first rejection
    code of the re-check. A device was never used, so it keeps its charge, isn't identified, emits no
    `items.device.activated`, and breaks no invisibility. A turning that fizzles costs nothing and draws
    nothing. A fizzled device use or turning still releases the actor's concentration, as every declaration
    other than a cast or a hold does.
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
                # The device use is judged again, as a cast is below. A refused one was never used, so
                # it spends no charge, identifies nothing, and breaks no invisibility.
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
            # Turning resolves in the magic phase but is never disruptable: it is
            # a class ability, not a spell (see the adaptations register). It is judged
            # again, though, and a refused one fizzles before it draws or costs anything.
            recheck = _validate_declaration(session, declaration, member)
            if recheck:
                events.append(
                    DeclarationFizzledEvent(code="magic.turning.fizzled", actor_id=member.id, reason=recheck[0].code)
                )
                events.extend(_release_concentration(session, member.id, state))
                acted.add(member.id)
                continue
            candidates = [
                session.combatant(monster_id) for group in session.encounter.groups for monster_id in group.monster_ids
            ]
            result = turn_undead(
                member,
                load_classes().get(member.class_id),
                candidates,
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
    # Any declaration other than these two releases the actor's concentration, a
    # turning included, even one the round stopped before it resolved (see the
    # adaptations register).
    for member, declaration in by_member.values():
        if declaration.action not in ("cast", "hold"):
            events.extend(_release_concentration(session, member.id, state))
    return events


def _track_concentration(session, caster_id: str, spell, result, state) -> list[Event]:
    """Make a concentration spell the caster's only maintained one, and return the release events.

    A caster maintains one concentration spell at a time, so casting one ends the spell the
    caster already maintains, even when the new spell attaches no effect. A spell that needs no
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
    for member, _ in by_member.values():
        if incapacitated(member) or not has_condition(member, Condition.CONFUSED):
            continue
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
                nearest = min(groups, key=lambda group: group.distance_feet)
                if nearest.distance_feet <= MELEE_RANGE_FEET:
                    pool = _monster_pool(session, nearest)
                    if pool:
                        target = pool[session.streams.get(COMBAT_STREAM).randbelow(len(pool))]
                        context = AttackContext(distance_feet=MELEE_RANGE_FEET)
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
                    distance_feet=MELEE_RANGE_FEET, defender_ally_ac_bonus=_ally_protection_bonus(session, target)
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
    withdrawn: Collection[str] = frozenset(),
    npc_actions: dict[str, list[MonsterAction]] | None = None,
) -> list[Event]:
    """Resolve the monster side's block: morale, flight, gazes, and the actions each group's policy chose.

    `retreating` names the party members a retreat exposes this round (see `_retreating_members`),
    and every attack against one of them gets the retreat's penalties. `withdrawn` names the members
    who stepped back out of the front rank earlier in the round. A melee attack chosen against one of
    them is skipped, because melee no longer reaches them.
    """
    from osrlib.crawl.session import MONSTER_ACTION_STREAM

    pending_casters = pending_casters or {}
    disrupted = disrupted if disrupted is not None else set()
    acted = acted if acted is not None else set()
    fire_damaged = fire_damaged if fire_damaged is not None else set()
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
            if group.distance_feet > FLEE_EXIT_FEET:
                group.fled = True
            continue
        events.extend(_resolve_gazes(session, group))
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
                rate = _encounter_rate(monster, session)
                group.distance_feet = max(MELEE_RANGE_FEET, group.distance_feet - rate)
                events.append(GroupMovedEvent(group_id=group.id, distance_feet=group.distance_feet))
                moved = True
            elif action.kind == "breath":
                events.extend(_resolve_breath(session, monster, group))
                _watch_disruption(events, pending_casters, disrupted, acted)
            elif action.kind == "melee":
                target = session.registry().get(action.target_id)
                if target is None or has_condition(target, Condition.DEAD) or action.target_id in withdrawn:
                    continue
                exposed = action.target_id in retreating
                if getattr(monster, "definition", None) is not None:
                    events.extend(
                        _resolve_npc_attack(session, monster, target, group, missile=False, retreating=exposed)
                    )
                else:
                    events.extend(
                        _resolve_monster_melee(session, monster, target, retreating=exposed, routine=action.routine)
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
    """An NPC adventurer's weapon attack, resolved through the kernel path the party uses."""
    weapon = _npc_wielded(npc, missile=missile, distance_feet=group.distance_feet)
    events: list[Event] = []
    if session.ledger.active_on(getattr(target, "id", ""), "mirror_image"):
        events.extend(pop_mirror_image(session.ledger, target.id, registry=session.registry(), clock=session.clock))
        return events
    context = AttackContext(
        distance_feet=group.distance_feet if missile else MELEE_RANGE_FEET,
        defender_retreating=retreating,
        defender_ally_ac_bonus=_ally_protection_bonus(session, target),
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


def _party_area_candidates(session, shape: str | None, dimensions: dict, gap_feet: int) -> list:
    """The footprint rule pointed at the party: front ranks covered by the span."""
    span = _area_span_feet(shape, dimensions, gap_feet)
    ranks = _party_ranks(session)
    covered = math.ceil(span / 10) if span > 0 else 0
    return [member for rank in ranks[:covered] for member in rank]


def _resolve_npc_cast(session, npc, group, action: MonsterAction, disrupted: set) -> list[Event]:
    """Resolve (or disrupt) an NPC's declared cast through the character kernel."""
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
    if targeting is not None and targeting.mode is TargetingMode.AREA:
        targets = _party_area_candidates(session, targeting.shape, dict(targeting.dimensions), group.distance_feet)
    elif action.target_id is not None:
        target = session.registry().get(action.target_id)
        if target is None or has_condition(target, Condition.DEAD):
            return []
        targets = [target]
    else:
        targets = []
    context = CastContext(in_combat=True, distance_feet=group.distance_feet)
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
    """Split a routing group's immobile living members into a stay-behind group.

    Fleeing is movement, and a member who cannot move cannot run. The runners keep
    the original group, its flags, and the shared group bundle they carry off
    ("routed ones flee with theirs"). The helpless become a fresh non-fleeing
    group where the side broke, at the current distance, with their individual
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
    the cleric". A group whose living members are all turned or afraid already runs as a whole.
    In a group with a living member that is neither turned nor afraid, the turned members break
    away. They become a new group at the same distance, and each one takes the treasure it
    carries, because routed monsters flee with their treasure. The group's shared bundle stays with
    the members who fight on, so it drops only when they're all slain. Every living member
    of the new group is turned, so the monster block moves it away as it moves a wholly turned
    group, and leaves behind any member who can't move. The members who fight on stay in the
    original group, so the group's id and the policy registered for it don't change. Dead members
    stay in the original group's record, as they do in `_leave_helpless_behind`.

    The session calls this when a battle starts, after each turning in the magic phase, and at
    the top of each monster block. The last call handles a group that becomes partly turned
    later: a group of turned and afraid members becomes one when the fear on a member ends.
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
    on its members ends and it stops running, it judges them then.
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

    Whether a monster re-saves depends on the Hit Dice it has left, so a drained monster may lose the re-save.
    The re-save runs on the magic stream, and the behavior roll and its pick of a
    target in its group run on the combat stream. The round handler makes these rolls,
    distinct from the ledger's tick-time effects. A confused monster that attacks a party
    member in `retreating` gets the retreat's penalties, as every attack on that member does.
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
            pool = _party_target_pool(session)
            targets = _reachable_targets(session, monster, pool)
            if targets and group.distance_feet <= MELEE_RANGE_FEET:
                target = targets[session.streams.get(COMBAT_STREAM).randbelow(len(targets))]
                events.extend(_resolve_monster_melee(session, monster, target, retreating=target.id in retreating))
        elif outcome == "attack_own_group":
            fellows = [other for other in _living_monsters(session, group) if other.id != monster.id]
            if fellows:
                target = fellows[session.streams.get(COMBAT_STREAM).randbelow(len(fellows))]
                events.extend(_resolve_monster_melee(session, monster, target, retreating=False))
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
"""Attack entries battle never rolls as attacks, whichever routine holds them.

A petrifying gaze runs through `_resolve_gazes` once a round, a breath weapon through a `"breath"`
action, and the rest are left to the referee.
"""


def _resolve_monster_melee(session, monster, target, *, retreating: bool, routine: int | None = None) -> list[Event]:
    """Resolve one of a monster's attack routines against one target, with what each hit does beyond damage.

    `routine` is an index into the template's `attacks`, and None resolves the first routine, as a
    confused monster's attack does. An index the template doesn't have, a negative one included,
    resolves nothing, so the session skips the action. A `SPECIAL` routine resolves nothing either,
    and an attack named `gaze` or `breath` takes no attack roll in any routine.
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
                defender_retreating=retreating,
                defender_ally_ac_bonus=_ally_protection_bonus(session, target),
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


_HIT_EFFECT_KINDS = frozenset({"paralysis", "poison", "disease", "energy_drain", "petrification"})
"""The attack effects that battle applies when a monster hits. The adaptations register lists the rest."""


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

    A monster that's immune to the condition the effect brings rolls no save and takes nothing.
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


def _resolve_gazes(session, group) -> list[Event]:
    """Turn each gazer's petrifying gaze on the party's front rank, once a round, while its group is in melee.

    A gaze takes no attack roll and no action. Every living gazer that isn't incapacitated uses it
    before its group acts, whatever the policy then chooses for it. `resolve_gaze` skips a character
    who is averting their eyes or is incapacitated, and rolls a save versus paralysis on the combat
    stream for each of the others.
    """
    if group.distance_feet > MELEE_RANGE_FEET:
        return []
    events: list[Event] = []
    for monster in _living_monsters(session, group):
        if incapacitated(monster) or _petrifying_gaze(monster) is None:
            continue
        events.extend(
            resolve_gaze(
                monster,
                _party_front_rank(session),
                stream=session.streams.get(COMBAT_STREAM),
                ledger=session.ledger,
                clock=session.clock,
                allocator=session.allocator,
                registry=session.registry(),
            )
        )
    return events


def _resolve_breath(session, monster, group) -> list[Event]:
    """A breath weapon against the party, resolved through the deterministic rank-coverage footprint."""
    params = monster.template.ability("breath_weapon").params
    if str(params.get("targeting")) == "single":
        from osrlib.crawl.session import MONSTER_ACTION_STREAM

        pool = _party_target_pool(session)
        if not pool:
            return []
        target = pool[session.streams.get(MONSTER_ACTION_STREAM).randbelow(len(pool))]
        targets = [target]
    else:
        shape = str(params.get("shape", "cone"))
        span = _area_span_feet(shape, params, group.distance_feet)
        ranks = _party_ranks(session)
        covered = math.ceil(span / 10) if span > 0 else 0
        targets = [member for rank in ranks[:covered] for member in rank]
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
mode gate, and does the command log, listener, and validation-phase bookkeeping a handler alone would
skip.

The value takes `(session, command)` and returns a `(rejections, events)` pair. Battle has one command
because a round is resolved as a whole: every party member declares, and the round handler runs both sides
in the SRD's phase order.
"""
