"""Run a fight round by round on the range track, with a pluggable policy driving the monsters.

This module takes over once [`osrlib.crawl.encounter`][osrlib.crawl.encounter] has decided there is a
fight. [`start_battle`][osrlib.crawl.battle.start_battle] is the entry point, and the encounter
procedure calls it for you: from an attacking stance, from a hostile group's deadline arriving,
from a chase that closed to arm's length, and from the party's own
[`EngageBattle`][osrlib.crawl.commands.EngageBattle] command. After it returns the session is in
`battle` mode, `session.battle` holds a [`BattleState`][osrlib.crawl.battle.BattleState], and each
round is one [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound] command with one
[`BattleDeclaration`][osrlib.crawl.commands.BattleDeclaration] per living, able party member,
dispatched through the session's private handler table. No command ends the battle. It ends
from inside, when the party is wiped, when every monster group is dead or routed, or when the whole
party retreats. A victory hands control straight to
[`end_encounter`][osrlib.crawl.encounter.end_encounter].

The results reach a front end as events from [`osrlib.crawl.events`][osrlib.crawl.events]:
[`BattleStartedEvent`][osrlib.crawl.events.BattleStartedEvent],
[`BattleRoundEvent`][osrlib.crawl.events.BattleRoundEvent] opening each round,
[`SpellDeclaredEvent`][osrlib.crawl.events.SpellDeclaredEvent] for every cast declared that round,
[`GroupMovedEvent`][osrlib.crawl.events.GroupMovedEvent] as the range track changes,
[`MonsterFledEvent`][osrlib.crawl.events.MonsterFledEvent] when a side breaks,
[`MonstersLeftBehindEvent`][osrlib.crawl.events.MonstersLeftBehindEvent] for the helpless a fleeing
side abandons, and [`BattleEndedEvent`][osrlib.crawl.events.BattleEndedEvent] reporting victory,
defeat, or flight. The kernel's own attack, damage, saving throw, initiative, and morale events come
back interleaved with those.

A round wraps the kernel in the OSE SRD's sequence: declaration, initiative, then per side morale,
movement, missiles, magic, and melee, with slow-weapon actors last. Every resolution step is a
function in [`osrlib.core.combat`][osrlib.core.combat] or
[`osrlib.core.spells`][osrlib.core.spells], among them
[`roll_initiative`][osrlib.core.combat.roll_initiative],
[`resolve_attack`][osrlib.core.combat.resolve_attack],
[`resolve_breath`][osrlib.core.combat.resolve_breath],
[`morale_triggers`][osrlib.core.combat.morale_triggers], and
[`cast_spell`][osrlib.core.spells.cast_spell]. What this module adds to them is the ordering, the state
it hands each of them (the RNG streams, the effects ledger, the clock, the entity registry), the
range track the kernel has no notion of, and the party's formation. Call those
kernel functions directly when you want one resolution and no session at all. The guide on using the
rules without a session walks that path.

The combat space is the abstract per-group range track, the Bard's Tale convention, as a documented
adaptation (see the
[adaptations register](https://mmacy.github.io/osrlib-python/adaptations/), the page listing where
osrlib commits to one reading of an ambiguous rule or supplies a default behind a `Ruleset` flag).
Each monster group sits at a distance from the party, closes at its encounter rate, and fights at
[`MELEE_RANGE_FEET`][osrlib.crawl.battle.MELEE_RANGE_FEET]. Party ranks derive from marching order
under the ruleset's `formation_width_limit` flag, and the width is the frontage the party's own space
offers at [`FIGHTER_FRONTAGE_FEET`][osrlib.crawl.battle.FIGHTER_FRONTAGE_FEET] to a combatant: two
abreast in a ten-foot passage, and a room's shorter side in a room.

The machine detects spell disruption, meaning a declared caster who is successfully attacked or fails
a save after initiative resolves against them and before their own action. It checks morale on its
own, with no command for it. It ends each single-use protection as it is spent. Invisibility breaks
when the member attacks, throws or unleashes an item, or turns undead, and the kernel's
[`cast_spell`][osrlib.core.spells.cast_spell] is what breaks it on a cast. An incoming attack on a
target under *mirror image* pops one figment instead, hit or miss, because no attack roll is made at
all. A protection ward breaks when the party melees a monster it barred. A concentration spell's
effects release when its caster declares anything other than `cast`, `turn_undead`, or `hold`. Area
footprints resolve deterministically: an area's capacity in creatures is `ceil(span / 10) × width`,
filled in stable spawn order, cones reach-limited, with the engaged party front rank appended under
the ruleset's `aoe_friendly_fire` flag.

The monster and NPC-party sides act through a pluggable
[`ActionPolicy`][osrlib.crawl.battle.ActionPolicy] you can substitute per encounter side.
[`ScriptedPolicy`][osrlib.crawl.battle.ScriptedPolicy] and
[`NpcPartyPolicy`][osrlib.crawl.battle.NpcPartyPolicy] ship as the defaults, and both draw only from
the `monster_action` stream, so a policy of your own never shifts an attack or damage draw. Initiative
still resolves in side blocks, because the SRD's phase sequence runs per side rather than per
combatant, and the sides order by their best individual total.

Typical usage:

```python
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
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

# One command is one round: a declaration per living, able member, every group named by id.
group_id = session.encounter.groups[0].id
result = session.execute(
    ResolveBattleRound(
        declarations=tuple(
            BattleDeclaration(character_id=member.id, action="attack", target_group_id=group_id) for member in members
        )
    )
)
assert result.accepted
assert session.battle.round == 1
assert result.events[0].code == "battle.round.started"
assert "combat.initiative.rolled" in [event.code for event in result.events]
```
"""

import math
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

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
    resolve_splash_attack,
    roll_initiative,
    validate_attack,
    validate_breath,
)
from osrlib.core.creature import Creature
from osrlib.core.dice import roll
from osrlib.core.effects import EFFECTS_STREAM, Condition, has_condition
from osrlib.core.events import AttackRolledEvent, Event, SavingThrowRolledEvent, SpellDisruptedEvent
from osrlib.core.items import (
    GearTemplate,
    ItemInstance,
    MagicItemCategory,
    MagicItemInstance,
    WeaponQuality,
    magic_item_template,
)
from osrlib.core.monsters import MonsterInstance
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
    """Read an integer param out of schema-validated data whose union the type checker can't key by name."""
    return int(params.get(key, default))


MELEE_RANGE_FEET = 5
"""The gap, in feet, at which a group on the range track is close enough to trade blows.

A group closing on the party stops here rather than at zero, a melee attack declared against a group
further off than this is rejected, and a monster's melee resolves with this as the distance handed to
the kernel. It is also where the monster groups stand when a chase collapses into a fight and the
pursuers catch the party.

The abstract track has no distance between this and zero, so treat the value as fixed rather than as a
setting: every reach and range check in the module reads it. To give a weapon longer reach, put the
range data on the weapon, which is what
[`validate_attack`][osrlib.core.combat.validate_attack] reads.
"""

FLEE_EXIT_FEET = 120
"""How far, in feet, a routed group runs before the battle lets it go.

A group that breaks morale turns and runs its full movement rate each round. Once its distance passes
this, the group is marked fled: it takes no further action, an attack declared against it is rejected
as naming an unknown group, and a battle in which every group has fled or died ends in victory.
Inside that window the party can still chase it down or shoot it in the back, which is what the
window is for. A group that is merely afraid rather than broken counts as routed on the same terms,
once it too is past this distance.
"""


class BattleState(BaseModel):
    """A battle in progress: the round counter and the per-round bookkeeping a fight needs to keep.

    You get this from `session.battle`, which is None whenever no battle is running. It is an overlay
    on the encounter rather than a replacement for it: the monster groups, their distances, and their
    treasure stay on `session.encounter`, and this holds only what the fight itself has to remember.
    It serializes with the session, so a saved game resumes mid-battle. Read it and render from it.
    The round handler is what writes every field.

    To change a fight while it runs, send a referee command from
    [`osrlib.crawl.commands`][osrlib.crawl.commands] rather than writing to this model. Every referee
    command is legal in `battle` mode, so a referee can grant an item, award experience, set a flag,
    or advance the clock mid-fight, and the change replays from a save because the command goes
    through the command log. [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters] and
    [`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty] are the exception: both reject while an
    encounter is open, so a fresh side cannot join a fight already underway.
    """

    model_config = ConfigDict(validate_assignment=True)

    round: int = 0
    """Rounds resolved so far. It is 0 between [`start_battle`][osrlib.crawl.battle.start_battle] and
    the first [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound], except that a monsters'
    free round counts as round 1."""
    started_round: int
    """The session clock's round count when the battle opened."""
    monsters_hold_rounds: int = 0
    """Rounds the monster side still owes to the party's surprise. A round in which this is above zero
    spends one and the monsters do nothing at all."""
    morale: MoraleTracker = MoraleTracker()
    """The kernel's [`MoraleTracker`][osrlib.core.combat.MoraleTracker] for this battle. It records each
    group's held checks, and a group that has held two stops checking."""
    morale_acted: dict[str, list[str]] = {}
    """The morale triggers already spent, per group id. Each trigger fires one check per group per
    battle, so a side that has already checked on losing its leader does not check again for the same
    reason."""
    fired_last_round: list[str] = []
    """The ids of party members who loosed a missile last round, which is what the kernel's reload rule
    reads."""
    melee_engagements: dict[str, list[str]] = {}
    """Per party member id, the monsters that member has actually closed with. A monster barred from a
    warded character by *protection from evil* may attack them anyway once it is on this list, which is
    RAW's own clause."""
    concentration: dict[str, list[str]] = {}
    """Per caster id, the effect ids a concentration spell is holding up. They release as soon as that
    caster declares anything other than `cast`, `turn_undead`, or `hold`."""


class MonsterAction(BaseModel):
    """One monster's or NPC adventurer's chosen action for a round: what an action policy returns.

    An [`ActionPolicy`][osrlib.crawl.battle.ActionPolicy] builds a list of these and the round handler
    resolves them in order. The model is frozen, so build a new one rather than editing one. Nothing
    validates a policy's output ahead of time: an action the situation does not allow, like a melee against
    a target that died earlier in the round, is skipped when the handler reaches it.
    """

    model_config = ConfigDict(frozen=True)

    monster_id: str
    """The acting combatant's entity id, which must be one of its own group's `monster_ids`."""
    kind: str  # close | breath | melee | hold | npc_shoot | npc_cast | npc_drink
    """What the combatant does. `"close"` advances the whole group one encounter rate toward the party
    and stops at [`MELEE_RANGE_FEET`][osrlib.crawl.battle.MELEE_RANGE_FEET], and only the first such
    action in a round moves the group. `"melee"` attacks `target_id`. `"breath"` looses the monster's
    breath weapon over the party. `"hold"` does nothing. `"npc_shoot"`, `"npc_cast"`, and `"npc_drink"`
    are the NPC adventurer actions: a missile attack on `target_id`, a cast of `spell_id`, and a
    swallowed potion named by `item_id`."""
    target_id: str | None = None
    """The target's entity id for `"melee"`, `"npc_shoot"`, and a single-target `"npc_cast"`. None for
    an area spell, which takes its own targets from the footprint rule."""
    spell_id: str | None = None
    """The spell to cast, for `"npc_cast"`."""
    spell_mode: str | None = None
    """Which mode of that spell to use, for `"npc_cast"`."""
    item_id: str | None = None
    """The magic item instance id to use, for `"npc_drink"`."""


class ActionPolicy(Protocol):
    """The monster side's brain: what decides, each round, what one monster group does.

    Write a class with a `choose` method matching this protocol when you want tactics of your own, and
    register it for a group by its id on `session.action_policies`, a plain dict the session keeps and
    never serializes. A group with no entry there gets
    [`ScriptedPolicy`][osrlib.crawl.battle.ScriptedPolicy], or
    [`NpcPartyPolicy`][osrlib.crawl.battle.NpcPartyPolicy] when its members are NPC adventurers. Since
    policies are code rather than state, re-register yours after loading a save, the way you
    re-register listeners.

    Every policy draws only from the `monster_action` stream, so a policy of your own never shifts an
    attack or damage roll and the rest of the fight stays reproducible from the same seed. Return
    whatever actions your tactics call for: the round handler skips one the situation no longer allows
    rather than raising. The shipped policies never cast, because monster casting is tagged for manual
    resolution in the data.

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

        The round handler calls this once per group per round, after morale has resolved and before
        anything on that side moves or attacks.

        Args:
            session (osrlib.crawl.session.GameSession): The running session. Read the party, the
                registry, and the effects ledger through it, and don't mutate it here.
            group (osrlib.crawl.encounter.EncounterGroup): The group to act. Its `distance_feet` is the
                current gap and its `monster_ids` are its members, dead ones included.
            stream (osrlib.core.rng.RngStream): The `monster_action` stream. Take every random choice
                from it and from nothing else, or you break the determinism of the rest of the fight.

        Returns:
            The actions to resolve, in the order they should resolve. Return an empty list for a group
                that does nothing. Skip the dead and the incapacitated: the handler ignores actions for
                them anyway.
        """
        ...


class ScriptedPolicy:
    """The default [`ActionPolicy`][osrlib.crawl.battle.ActionPolicy] for monsters: breath, then close and melee.

    Every monster group runs on one of these unless you registered something else for it on
    `session.action_policies`, so you construct one yourself only to wrap it.

    Monsters whose data includes a scripted pattern follow it. A breath weapon with a daily use count
    opens with breath, then takes breath or melee with equal chance while uses remain, which is how the
    OSE SRD's dragons fight. A breath weapon gated on a chance in six rolls that gate each round, as
    the hellhound's does. Otherwise a group further off than
    [`MELEE_RANGE_FEET`][osrlib.crawl.battle.MELEE_RANGE_FEET] closes, and once at that range each
    monster picks its target uniformly from the party rank it can reach. Monster missile routines have
    no structured range data, so osrlib treats them the way it treats melee, closing first and then
    attacking, as a documented adaptation (see the
    [adaptations register](https://mmacy.github.io/osrlib-python/adaptations/), the page listing where
    osrlib commits to one reading of an ambiguous rule or supplies a default behind a `Ruleset`
    flag). These groups never cast,
    because monster spell casting is tagged for manual resolution in the data.

    Substitute a policy of your own when you want a side to hold a chokepoint, concentrate its
    attacks, back off, or cast. [`NpcPartyPolicy`][osrlib.crawl.battle.NpcPartyPolicy] is the shipped
    example of a policy that does more than this one.
    """

    def choose(self, session, group, stream) -> list[MonsterAction]:
        """Choose this round's actions for one monster group.

        Args:
            session (osrlib.crawl.session.GameSession): The running session.
            group (osrlib.crawl.encounter.EncounterGroup): The group to act.
            stream (osrlib.core.rng.RngStream): The `monster_action` stream.

        Returns:
            One [`MonsterAction`][osrlib.crawl.battle.MonsterAction] per living monster that can act,
                in the group's own member order. Incapacitated and confused monsters get none, because
                the round handler runs confusion itself.
        """
        actions: list[MonsterAction] = []
        pool = _party_target_pool(session)
        for monster in _living_monsters(session, group):
            if incapacitated(monster) or has_condition(monster, Condition.CONFUSED):
                continue  # confusion is a machine override, not a policy choice
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
            actions.append(MonsterAction(monster_id=monster.id, kind="melee", target_id=target.id))
        return actions


class NpcPartyPolicy:
    """The default [`ActionPolicy`][osrlib.crawl.battle.ActionPolicy] for a group of NPC adventurers.

    A group whose members are NPC adventurers rather than monsters runs on one of these unless you
    registered something else for it on `session.action_policies`. The OSE SRD gives no tactics of its
    own for an opposing party of adventurers, so osrlib supplies these, as a documented adaptation
    (see the
    [adaptations register](https://mmacy.github.io/osrlib-python/adaptations/), the page listing where
    osrlib commits to one reading of an ambiguous rule or supplies a default behind a `Ruleset`
    flag).

    Each living member picks the first of these that applies. A caster holding a memorized healing
    spell heals the group's most wounded member below half hit points, taking the lowest ratio of
    current to maximum and breaking ties by id. A member below half hit points whose group has no
    healing spell left drinks a healing potion it carries, which is the only item use these tactics
    make. A caster holding a memorized attack spell that osrlib resolves without a referee casts it at
    the party, highest spell level first and ties broken by spell id, taking area targets through the
    footprint rule and a single target uniformly from the rank it can reach. A member with a missile
    weapon shoots while the gap is wider than
    [`MELEE_RANGE_FEET`][osrlib.crawl.battle.MELEE_RANGE_FEET]. Failing all of that, the member closes
    and melees, the same as a monster.

    These casts post their declarations at the top of the round and are disruptable exactly as the
    party's are, because RAW's disruption trigger does not care which side declared. Every choice draws
    from the `monster_action` stream alone.
    """

    def choose(self, session, group, stream) -> list[MonsterAction]:
        """Choose this round's actions for one group of NPC adventurers.

        Args:
            session (osrlib.crawl.session.GameSession): The running session.
            group (osrlib.crawl.encounter.EncounterGroup): The group to act.
            stream (osrlib.core.rng.RngStream): The `monster_action` stream.

        Returns:
            One [`MonsterAction`][osrlib.crawl.battle.MonsterAction] per living member that can act, in
                the group's own member order. Incapacitated and confused members get none.
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
"""Feet of frontage one combatant needs in order to fight side by side with the next.

The party's front rank is as wide as its own fighting space divided by this, so a ten-foot passage
holds two abreast and a room holds as many as its shorter side allows. A member outside the front rank
is rejected for declaring a melee attack. The rank check does not apply to a missile attack, so the
back ranks can still shoot.

RAW prints one number for this and leaves the rest to judgement: "The referee should judge the number
of opponents that can attack a single combatant, bearing in mind the combatant's size and the
available space around them. **10' passage:** Enough space for at most 2-3 characters to fight
side-by-side." osrlib takes the conservative end of that range, two in ten feet and so five feet each,
and then applies it to whatever space the party is actually standing in.

To play without a width limit, turn off the ruleset's `formation_width_limit` flag, which puts every
living member in the front rank. Changing this constant instead would move the frontage of every space
in the game at once.
"""


def _fighting_space(level, position: Position) -> set[Position]:
    """The cells the party can form a line across, flooded out from where it stands.

    The flood crosses open edges only. It stops at a wall. It stops at a door,
    because a doorway is a threshold rather than room to fight abreast. It stops
    wherever the space itself changes, which is what keeps a room's open mouth
    onto a corridor from counting the corridor as part of the room. Corridor cells
    belong to no area, so the flood's own connectivity is what separates one
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
    """Rank width: how many combatants fit abreast in the party's own frontage.

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
    is the wearer's rank. The wearer's own +1 arrives with the rest of its
    equipped items, so only allies collect the aura here, and multiple rings
    never stack.

    Args:
        session (osrlib.crawl.session.GameSession): The battle's session.
        defender: The combatant under attack. A combatant in no rank collects
            nothing.

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
    who has engaged the barred creature in melee, which is RAW's own clause.
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
    """Being attacked in battle identifies worn enchanted armour, shields, and rings."""
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

Monsters have a morale score in their stat block, but adventurers in the OSE SRD do not, so osrlib
uses the score printed for the Veteran, its own low-level adventurer monster, rather than inventing
one. This is a documented adaptation (see the
[adaptations register](https://mmacy.github.io/osrlib-python/adaptations/), the page listing where
osrlib commits to one reading of an ambiguous rule or supplies a default behind a `Ruleset` flag).
A score of 9 holds on a 2d6 total of 9 or less once the situational modifier is added, and breaks
above that.

The battle machinery reads this value directly, so every NPC adventurer group in the game checks
against the same score and there is no per-group override.
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
    """Open battle on the session's current encounter: the range track takes over.

    Call this when your own code decides the talking is over. There must be an open encounter on the
    session, because the fight runs on that encounter's groups and their distances. On return the
    session is in `battle` mode and `session.battle` holds a
    [`BattleState`][osrlib.crawl.battle.BattleState]. From there, each round is one
    [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound] command.

    Use [`EngageBattle`][osrlib.crawl.commands.EngageBattle] instead when the party is the one choosing
    to fight. That command goes through the session's command log, so the fight replays from a save,
    and it works out the surprise arguments and a mid-chase turn-and-fight for you. The encounter
    procedure calls this function itself for every other opening, so a front end that only issues
    commands never calls it at all.

    A group whose morale score is 2 routs the moment battle starts, per RAW, which can end the battle
    before a round has run. A surprise advantage becomes one free round for the side that holds it: the
    monsters' free round resolves inside this call, with the party unable to answer, while the party's
    free round holds the monsters through the first `ResolveBattleRound`.

    Args:
        session (osrlib.crawl.session.GameSession): The running session, with `session.encounter` set.
        party_free_round: True when the monsters were surprised. The monster side then sits out the
            first round.
        monsters_free_round: True when the party was surprised and the stance is hostile or attacking.
            The monsters act once inside this call, before the party's first declaration.

    Returns:
        The opening events: [`BattleStartedEvent`][osrlib.crawl.events.BattleStartedEvent], a
            [`MonsterFledEvent`][osrlib.crawl.events.MonsterFledEvent] for each group that routed on
            sight, whatever a monsters' free round resolved, and a
            [`BattleEndedEvent`][osrlib.crawl.events.BattleEndedEvent] already when the opening itself
            left a terminal state.

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
        if _group_morale_score(session, group) == 2 and not group.fled:
            group.fled = True
            events.append(MonsterFledEvent(code="battle.side.fled", group_id=group.id))
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


def _check_ends(session, *, party_retreating: bool) -> list[Event] | None:
    """Return the end-of-battle events when a terminal state holds, else `None`.

    Defeat ends the battle and nothing more. The session's own wipe check makes
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
"""The two `move` values the whole formation has to agree on, the SRD's two ways out of melee.

The order is the order the rejections come back in, the fighting withdrawal's first. `close` is not
one of them, because it needs no agreement: the first `close` in marching order advances the
formation whenever the round is accepted, and a `close` declared beside a defensive move is one of
the others that split the round.
"""


def _formation_split_rejections(declarers, declarations: Sequence[BattleDeclaration]) -> list[Rejection]:
    """Refuse a defensive move that some of the round's declarers made and the rest did not.

    The party moves as one formation and a member cannot leave it (see the
    [adaptations register](https://mmacy.github.io/osrlib-python/adaptations/), under the Bard's
    Tale convention), so a `fighting_withdrawal` or a `retreat` is a legal declaration only when
    every declarer makes the same one. A round whose only declarer declares one is legal, because
    everyone agreed, and a member who cannot declare at all, being dead or incapacitated, is no
    declarer and does not count.

    Args:
        declarers: The living, able members the round expects, in marching order.
        declarations: The round's declarations, one per declarer, in the order the caller sent them.

    Returns:
        One `battle.declaration.formation_split` rejection per defensive move at least one declarer
        chose and at least one did not, naming the `move`, the `declared` ids, and the `others`,
        each id tuple in marching order. `others` is every other declarer of the round, the one who
        chose the other defensive move included, so a round that splits on both moves comes back
        with two rejections, each naming the other's declarers among its `others`. Those two arrive
        in the order `_DEFENSIVE_MOVES` lists them, the fighting withdrawal first and the retreat
        second. Empty when the formation agrees.
    """
    moves = {declaration.character_id: declaration.move for declaration in declarations if declaration.action == "move"}
    order = [member.id for member in declarers]
    rejections: list[Rejection] = []
    for move in _DEFENSIVE_MOVES:
        declared = tuple(member_id for member_id in order if moves.get(member_id) == move)
        others = tuple(member_id for member_id in order if moves.get(member_id) != move)
        if declared and others:
            rejections.append(
                Rejection(
                    code="battle.declaration.formation_split",
                    params={"move": move, "declared": declared, "others": others},
                )
            )
    return rejections


def _validate_declaration(session, declaration: BattleDeclaration, member) -> list[Rejection]:
    """Judge one declaration against the state it names, and return every reason it cannot stand.

    This runs twice on the declarations the round accepts. Once in the validation pre-phase, where a
    rejection refuses the whole command, and again in the magic phase, immediately before a `cast`
    resolves, because the phases between the two can change what these checks read. It reads state and
    takes no draw, so running it a second time costs nothing and changes nothing.
    """
    state = session.battle
    if declaration.action == "hold":
        return []
    if declaration.action == "move":
        if declaration.move is None:
            return [Rejection(code="battle.declaration.missing_move", params={"character": member.id})]
        if declaration.move == "close" and _group_by_id(session, declaration.target_group_id) is None:
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
            if width is not None and member not in _party_front_rank(session):
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
    """Resolve a cast declaration's targets: explicit ids, or the area footprint."""
    try:
        mode = spell.mode(declaration.spell_mode, reversed=declaration.reversed)
    except ValueError:
        return [], None, [Rejection(code="magic.cast.unknown_mode", params={"mode": declaration.spell_mode or ""})]
    targeting = mode.targeting
    if targeting is not None and targeting.mode is TargetingMode.AREA:
        group = _group_by_id(session, declaration.target_group_id)
        if group is None:
            return [], None, [Rejection(code="battle.declaration.unknown_group", params={})]
        candidates = _area_candidates(session, group, targeting.shape, targeting.dimensions)
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
            # You know what you can't see, so this rejection leaks nothing.
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

    For a scroll this runs twice, as `_validate_declaration`'s `cast` branch does: once in the
    validation pre-phase, and again in the magic phase immediately before the read resolves, because the
    phases between the two can change what these checks read. It reads state and takes no draw, so the
    second run costs nothing and changes nothing.
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

        # The kernel's own pre-check, at the scroll's caster level, so a declaration that
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
    """A duck-typed stand-in shaped like `UseItem`, for the exploration scroll reader."""

    def __init__(self, *, spell_id, mode, targets, target_id) -> None:
        self.spell_id = spell_id
        self.mode = mode
        self.targets = targets
        self.target_id = target_id


def _resolve_scroll_cast(session, member, instance, template, declaration: BattleDeclaration) -> list[Event]:
    """Read one spell off a scroll in the magic phase, at the scroll's own caster level.

    The declaration is judged again first, before anything is spent, and a refused one still spends the
    scroll: the read itself is emitted and the spell struck off, and only then is the fizzle reported,
    because the reader did read it and the ink is gone either way. A thief reading an arcane scroll rolls
    the printed error chance after that, and a failed roll burns the spell with nothing to report: that
    miscast is the scroll's own, not a refused declaration, so it carries no `magic.cast.fizzled` event.
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
        by_member[declaration.character_id] = (member, declaration)
        rejections.extend(_validate_declaration(session, declaration, member))
    rejections.extend(_formation_split_rejections(declarers, command.declarations))
    if rejections:
        # The whole command rejects listing every rejection. Partial acceptance
        # would tangle the replay contract (see the adaptations register).
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
    party_retreating = False
    slow_attacks: list[tuple[Creature, BattleDeclaration]] = []

    def party_block() -> list[Event]:
        nonlocal party_retreating
        block: list[Event] = []
        block.extend(_party_movement(session, by_member))
        party_retreating = _party_is_retreating(session, by_member)
        block.extend(
            _party_attacks(
                session,
                by_member,
                missile=True,
                slow_attacks=slow_attacks,
                fired=fired_this_round,
                fire_damaged=fire_damaged_groups,
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
            )
        )
        block.extend(_confused_party_overrides(session, by_member, fire_damaged_groups))
        # Party hits and failed saves disrupt declared NPC casters too (the RAW
        # trigger doesn't care which side declares).
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
            party_retreating=party_retreating,
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
        end = _check_ends(session, party_retreating=party_retreating)
        if end is not None:
            events.extend(end)
    return [], events


def _party_acts_first(initiative, by_member) -> bool:
    for key in initiative.order:
        if key in by_member:
            return True
        return False
    return True


def _party_is_retreating(session, by_member) -> bool:
    declarations = [declaration for _, declaration in by_member.values()]
    return bool(declarations) and all(
        declaration.action == "move" and declaration.move == "retreat" for declaration in declarations
    )


def _party_movement(session, by_member) -> list[Event]:
    """Consolidated formation movement, in order of precedence: retreat, fighting withdrawal, close.

    The party moves as a single formation and an individual member cannot leave
    it, as a documented adaptation (see the
    [adaptations register](https://mmacy.github.io/osrlib-python/adaptations/),
    under the Bard's Tale convention). Every member retreating moves the party off at the full encounter
    rate, the OSE SRD's "full encounter movement rate", and the running pursuit
    begins once the battle converts. Every member declaring a fighting withdrawal
    backs the party off at half encounter rate, and that declaration is a move on
    its own, so the withdrawing party attacks nobody that round. Otherwise the first
    `close` declaration in marching order advances the formation on its named
    group at encounter rate, stopping at 5'.

    A round the formation does not agree on never gets here: `_formation_split_rejections` refuses a
    defensive move some declarers made and the rest did not, in the validation pre-phase, with
    `battle.declaration.formation_split`.
    """
    declarations = [declaration for _, declaration in by_member.values()]
    events: list[Event] = []
    multiplier = _party_move_multiplier(session)
    if declarations and all(d.action == "move" and d.move == "retreat" for d in declarations):
        rates = [_encounter_rate(member, session) for member in session.party.living_members()]
        rate = min(rates, default=0) * multiplier
        for group in session.encounter.groups:
            if group.fled:
                continue
            group.distance_feet += rate
            events.append(GroupMovedEvent(group_id=group.id, distance_feet=group.distance_feet))
        return events
    if declarations and all(d.action == "move" and d.move == "fighting_withdrawal" for d in declarations):
        rates = [_encounter_rate(member, session) for member in session.party.living_members()]
        rate = (min(rates, default=0) // 2) * multiplier
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


def _party_move_multiplier(session) -> int:
    """*Haste*'s movement multiplier applies only when every living party member is under it.

    A documented adaptation (see the
    [adaptations register](https://mmacy.github.io/osrlib-python/adaptations/)).
    """
    living = session.party.living_members()
    if not living:
        return 1
    return min(_haste_multiplier(session, member.id, "movement_multiplier") for member in living)


def _party_attacks(session, by_member, *, missile: bool, slow_attacks, fired, fire_damaged) -> list[Event]:
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
            # creature actually fought (RAW's own clause; the modifiers persist).
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

    The sword rolled 1d4+4 total drains when it was generated, and once those are
    spent it is a plain +1. The lost-die rolls come from the advancement stream,
    because a drain reverses advancement. The sword's own entry in the OSE SRD
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
    monsters. The elementals form bars every one of them.
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
        hd = template.hit_dice.count
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
    hd = monster.template.hit_dice.count
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
    # One lookup takes the flask and answers what was taken: the instance the
    # attack resolves from is the instance that left the pack.
    instance = exploration._consume_item(member, declaration.item_id)
    if not isinstance(instance, ItemInstance) or not isinstance(instance.template, GearTemplate):
        # Unreachable: the declaration validator proved the item is gear carrying a
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

    `reason` is the first rejection code the re-check produced, which is what tells a front end why the
    spell failed. A refused declaration never raises out of the round, so this event is the whole outcome.
    """
    return SpellDisruptedEvent(
        code="magic.cast.fizzled", caster_id=caster_id, spell_id=spell_id, reversed=reversed, reason=reason
    )


def _fizzle_cast(session, member, spell_id: str, *, reversed: bool, reason: str, state) -> list[Event]:
    """Lose a declared cast the magic phase's re-check refused, the way a disruption loses it.

    The caster gives up the memorized copy through
    [`disrupt_casting`][osrlib.core.spells.disrupt_casting], so the declared form is what goes, and
    concentration releases as it does on any other action. A caster with no copy left to give up keeps the
    event and loses nothing twice, which is what stops an earlier phase that already took the copy, such as
    an energy drain, from raising here.
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
    `magic.cast.disrupted` rather than `magic.cast.fizzled`. Disruption is the blow that landed, and it
    is the outcome the table saw.

    A cast, and a scroll read inside `_resolve_scroll_cast`, is judged again immediately before it resolves,
    with the checks it passed at the top of the round, and one that now fails any of them fizzles instead of
    reaching the kernel. A device use and a turning are not judged again.
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
            events.extend(_resolve_magic_item_use(session, member, declaration, state))
            if category is not MagicItemCategory.POTION:
                # Unleashing a device or scroll is an attack for invisibility's
                # purposes (see the adaptations register). Drinking is not.
                events.extend(_break_invisibility(session, member))
            acted.add(member.id)
            continue
        if declaration.action == "turn_undead":
            # Turning resolves in the magic phase but is never disruptable: it is
            # a class ability, not a spell (see the adaptations register).
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
        _track_concentration(session, member.id, spell, result, state)
        acted.add(member.id)
    # Any declaration other than these three releases the actor's concentration
    # (see the adaptations register).
    for member, declaration in by_member.values():
        if declaration.action not in ("cast", "turn_undead", "hold"):
            events.extend(_release_concentration(session, member.id, state))
    return events


def _track_concentration(session, caster_id: str, spell, result, state) -> None:
    if spell.duration_spec.kind != "concentration":
        return
    from osrlib.core.events import EffectAttachedEvent

    effect_ids = [event.effect_id for event in result.events if isinstance(event, EffectAttachedEvent)]
    if effect_ids:
        state.concentration.setdefault(caster_id, []).extend(effect_ids)


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
    party_retreating: bool = False,
    npc_actions: dict[str, list[MonsterAction]] | None = None,
) -> list[Event]:
    from osrlib.crawl.session import MONSTER_ACTION_STREAM

    events: list[Event] = []
    pending_casters = pending_casters or {}
    disrupted = disrupted if disrupted is not None else set()
    acted = acted if acted is not None else set()
    fire_damaged = fire_damaged if fire_damaged is not None else set()
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
        if npc_actions is not None and group.id in npc_actions:
            actions = npc_actions[group.id]
        else:
            actions = _policy_for(session, group).choose(session, group, session.streams.get(MONSTER_ACTION_STREAM))
        events.extend(_confused_overrides(session, group, actions))
        moved = False
        for action in actions:
            monster = session.combatant(action.monster_id)
            if has_condition(monster, Condition.DEAD) or has_condition(monster, Condition.CONFUSED):
                continue
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
                if target is None or has_condition(target, Condition.DEAD):
                    continue
                if getattr(monster, "definition", None) is not None:
                    events.extend(
                        _resolve_npc_attack(session, monster, target, group, missile=False, retreating=party_retreating)
                    )
                else:
                    events.extend(_resolve_monster_melee(session, monster, target, party_retreating=party_retreating))
                _watch_disruption(events, pending_casters, disrupted, acted)
            elif action.kind == "npc_shoot":
                target = session.registry().get(action.target_id)
                if target is None or has_condition(target, Condition.DEAD):
                    continue
                events.extend(
                    _resolve_npc_attack(session, monster, target, group, missile=True, retreating=party_retreating)
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
    record, because outcome classification and loot drops read the `dead`
    condition before any group flag, so their bookkeeping is unchanged.
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


def _group_morale(session, group, fire_damaged) -> list[Event]:
    """Morale auto-invoked: the kernel's triggers through the per-battle tracker.

    A conditional alternate resolves from the round's context. Fear of fire, for
    example, applies when the round's damage included fire. The spell morale
    modifier folds in under the usual morale-modifier rule.
    """
    state = session.battle
    score = _group_morale_score(session, group)
    if score is None or group.fleeing:
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


def _confused_overrides(session, group, actions) -> list[Event]:
    """The machine-run confusion beat: re-save, then the 2d6 behavior roll.

    The re-save runs on the magic stream, and the behavior roll and its own-group
    target pick run on the combat stream. These are machine-run round rolls,
    distinct from the ledger's own tick-time effects.
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
        hd = monster.template.hit_dice
        resave_min = int(params.get("resave_hd_min_count", 3))
        qualifies = hd.count >= resave_min or (
            bool(params.get("resave_at_hd_count_2_with_bonus")) and hd.count == 2 and hd.modifier > 0
        )
        if qualifies:
            from osrlib.core.combat import saving_throw

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
                events.extend(_resolve_monster_melee(session, monster, target, party_retreating=False))
        elif outcome == "attack_own_group":
            fellows = [other for other in _living_monsters(session, group) if other.id != monster.id]
            if fellows:
                target = fellows[session.streams.get(COMBAT_STREAM).randbelow(len(fellows))]
                events.extend(_resolve_monster_melee(session, monster, target, party_retreating=False))
        # no_action: the confused creature babbles.
    return events


def _behaviour_outcome(params, total: int) -> str:
    for entry in params.get("behaviour_table", ()):
        band, _, outcome = str(entry).partition(":")
        low, _, high = band.partition("-")
        if int(low) <= total <= int(high or low):
            return outcome
    return "no_action"


def _resolve_monster_melee(session, monster, target, *, party_retreating: bool) -> list[Event]:
    events: list[Event] = []
    routine = monster.template.attacks[0] if monster.template.attacks else None
    if routine is None:
        return []
    for attack in routine.attacks:
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
                defender_retreating=party_retreating,
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
            events.extend(_identify_worn_items(session, target))
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
    """The RAW disruption trigger, found by the machine: a hit or a failed save before the caster acts."""
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

[`GameSession`][osrlib.crawl.session.GameSession] folds this map into its own private handler table
the first time it dispatches a command, alongside the exploration, encounter, and referee maps. There
is no registration point here: the only documented way to run a command is
[`GameSession.execute`][osrlib.crawl.session.GameSession.execute], which picks the handler, runs the
mode gate, and does the command log, listener, and validation-phase bookkeeping a handler alone would
skip.

The value takes `(session, command)` and returns a `(rejections, events)` pair. Battle has one command
because a round is resolved as a whole: every party member declares, and the machine runs both sides
in the SRD's phase order.
"""
