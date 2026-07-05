"""The range-track battle state machine and the pluggable monster action policy.

[`start_battle`][osrlib.crawl.battle.start_battle] opens battle — from the
encounter procedure's own stance handling, a pursuit collapsing to melee range, or
the party's own choice through the
[`EngageBattle`][osrlib.crawl.commands.EngageBattle] command — and each round after
that runs through
[`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound], which carries one
[`BattleDeclaration`][osrlib.crawl.commands.BattleDeclaration] per living, able
party member. A round wraps the kernel's combat functions in the OSE SRD's
sequence: declaration, initiative, then per side — monster morale, movement,
missiles, magic, melee — with slow-weapon actors last. The combat space is the
abstract per-group range track (the Bard's Tale convention), as a documented
adaptation — see the adaptations register: each monster group sits at a distance
from the party, closing at encounter rate and meleeing at 5'; party ranks derive
from marching order under the `formation_width_limit` flag (width 3 inside a keyed
area, 2 in a corridor).

The machine detects spell disruption (a declared caster is successfully attacked,
or fails a save, after initiative resolves against them but before their action),
auto-invokes morale, consumes marker conditions and battle-bound spell effects, and
resolves area footprints deterministically: an area's capacity in creatures is
`ceil(span / 10) × width`, filled in stable spawn order, cones reach-limited, with
the engaged party front rank appended under `aoe_friendly_fire`.

Monster and NPC-party sides act through a pluggable
[`ActionPolicy`][osrlib.crawl.battle.ActionPolicy], substitutable per encounter
side; the shipped [`ScriptedPolicy`][osrlib.crawl.battle.ScriptedPolicy] and
[`NpcPartyPolicy`][osrlib.crawl.battle.NpcPartyPolicy] draw only from the
`monster_action` stream, so swapping in a custom policy never shifts attack or
damage draws. Initiative still resolves in side blocks — the sides order by their
best individual total, since the SRD's phase sequence runs per side, not per
combatant.
"""

import math
from collections.abc import Mapping
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
from osrlib.core.dice import roll
from osrlib.core.effects import EFFECTS_STREAM, Condition, has_condition
from osrlib.core.events import AttackRolledEvent, Event, SavingThrowRolledEvent
from osrlib.core.items import MagicItemCategory, MagicItemInstance, WeaponQuality, magic_item_template
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
from osrlib.crawl.events import (
    BattleEndedEvent,
    BattleRoundEvent,
    BattleStartedEvent,
    GameOverEvent,
    GroupMovedEvent,
    MonsterFledEvent,
    SpellDeclaredEvent,
)
from osrlib.data import load_classes, load_spells

__all__ = [
    "ActionPolicy",
    "BattleState",
    "FLEE_EXIT_FEET",
    "HANDLERS",
    "MELEE_RANGE_FEET",
    "MonsterAction",
    "NPC_PARTY_MORALE",
    "NpcPartyPolicy",
    "ScriptedPolicy",
    "start_battle",
]


def _int_param(params: Mapping[str, Any], key: str, default: int = 0) -> int:
    """Read an integer param — schema-validated data whose union the checker can't key by name."""
    return int(params.get(key, default))


MELEE_RANGE_FEET = 5
FLEE_EXIT_FEET = 120


class BattleState(BaseModel):
    """The serialized battle overlay; the groups live on the encounter state."""

    model_config = ConfigDict(validate_assignment=True)

    round: int = 0
    started_round: int
    monsters_hold_rounds: int = 0
    morale: MoraleTracker = MoraleTracker()
    morale_acted: dict[str, list[str]] = {}
    fired_last_round: list[str] = []
    melee_engagements: dict[str, list[str]] = {}
    concentration: dict[str, list[str]] = {}


class MonsterAction(BaseModel):
    """One combatant's chosen action for the round (monster or NPC adventurer)."""

    model_config = ConfigDict(frozen=True)

    monster_id: str
    kind: str  # close | breath | melee | hold | npc_shoot | npc_cast | npc_drink
    target_id: str | None = None
    spell_id: str | None = None
    spell_mode: str | None = None
    item_id: str | None = None


class ActionPolicy(Protocol):
    """The pluggable monster brain — substituted per encounter side by games.

    Policies draw only from the `monster_action` stream, so a policy change never
    shifts attack or damage draws. A policy may return any actions the kernel
    validates; the default never casts (monster casting tags are `manual` data).
    """

    def choose(self, session, group, stream) -> list[MonsterAction]:
        """Choose this round's actions for one group's living, able monsters."""
        ...


class ScriptedPolicy:
    """The default [`ActionPolicy`][osrlib.crawl.battle.ActionPolicy]: scripted breath, then close-and-melee.

    Monsters with a scripted pattern in their data follow it: an `uses_per_day`
    breath weapon opens with breath, then breath or melee with equal chance while
    daily uses remain (the dragons, per the OSE SRD); a `per_round_chance_in_six`
    gate rolls each round (the hellhound). Otherwise a group beyond 5' closes; at
    5' it melees, each monster picking its target uniformly from the reachable
    party rank. Monster missile routines carry no structured range data, so osrlib
    treats them the same as melee — close, then attack at 5' — as a documented
    adaptation (see the adaptations register). Groups never cast.
    """

    def choose(self, session, group, stream) -> list[MonsterAction]:
        """Choose actions for the group (see [`ActionPolicy`][osrlib.crawl.battle.ActionPolicy])."""
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
    """The default [`ActionPolicy`][osrlib.crawl.battle.ActionPolicy] for NPC adventurer sides.

    The OSE SRD gives no tactics of its own for an opposing party of adventurers, so
    osrlib supplies one, as a documented adaptation (see the adaptations register).

    Per living member each round, in priority order: (1) a caster holding a
    memorized cure spell heals the group's most-wounded member below half hp
    (lowest hp ratio, ties by id); (1½) a member below half hp with no cure left
    in the group drinks a carried healing potion — the one item-use the default
    policy performs; (2) a caster holding a memorized wired offensive spell casts
    it — highest spell level first, ties by spell id — at the party (areas through
    the footprint rule, single-target picks uniform from the reachable rank);
    (3) a member with a missile weapon and a gap beyond 5' shoots; (4) otherwise
    close and melee, the monster default. Policy draws come only from the
    `monster_action` stream; NPC casts post declarations and are disruptable
    exactly like party casts (RAW's trigger doesn't care which side declares).
    """

    def choose(self, session, group, stream) -> list[MonsterAction]:
        """Choose this round's actions (see [`ActionPolicy`][osrlib.crawl.battle.ActionPolicy])."""
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
    """The best memorized wired offensive spell: highest level first, ties by id."""
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
    """The NPC's first wielded weapon fitting the range — template or magic instance."""
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
    """The group's living combatants — monsters or NPC adventurers."""
    return [
        session.combatant(monster_id)
        for monster_id in group.monster_ids
        if not has_condition(session.combatant(monster_id), Condition.DEAD)
    ]


def _formation_width(session) -> int | None:
    """Rank width: 3 inside a keyed area, 2 in corridor; `None` with the flag off."""
    if not session.ruleset.formation_width_limit:
        return None
    from osrlib.crawl import exploration

    level = exploration._level(session)
    area = level.area_at(exploration._position(session))
    return 3 if area is not None else 2


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
    is the wearer's rank. The wearer's own +1 rides the equipped-item channel —
    only allies collect the aura here — and multiple rings never stack.

    Args:
        session (osrlib.crawl.session.GameSession): The battle's session.
        defender: The combatant under attack; non-members collect nothing.

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
    against a warded target of differing alignment; the ban breaks for a target
    who has engaged the barred creature in melee (RAW's own clause).
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
"""NPC adventurer groups check morale at ML 9, the Veteran stat block's printed
score. osrlib anchors on the OSE SRD's own low-level-adventurer monster rather than
an invented number, as a documented adaptation (see the adaptations register)."""


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
    """Open battle from the current encounter: the range track takes over.

    ML 2 groups rout when battle starts (RAW). A surprise advantage becomes a free
    round: the monsters' free round resolves immediately (machine-run, the party
    cannot act); the party's free round holds the monsters through the first
    `ResolveBattleRound`.

    Args:
        session (osrlib.crawl.session.GameSession): The running session.
        party_free_round: The monsters were surprised.
        monsters_free_round: The party was surprised with a hostile/attacking stance.

    Returns:
        The battle-opening events.
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
    """Return the end-of-battle events when a terminal state holds, else `None`."""
    from osrlib.crawl import encounter as encounter_module

    if not session.party.living_members():
        session.battle = None
        session.encounter = None
        session.mode = SessionMode.GAME_OVER
        return [BattleEndedEvent(code="battle.ended.defeat"), GameOverEvent(reason="the party has fallen")]
    groups = session.encounter.groups
    done = all(
        group.fled or group.surrendered or not _living_monsters(session, group) or _all_routed(session, group)
        for group in groups
    )
    if done:
        session.battle = None
        return [BattleEndedEvent(code="battle.ended.victory"), *encounter_module.end_encounter(session, "victory")]
    if party_retreating:
        from osrlib.crawl.encounter import PursuitState

        session.battle = None
        session.mode = SessionMode.ENCOUNTER
        session.encounter.evading = True
        session.encounter.pursuit = PursuitState(
            gap_feet=min(group.distance_feet for group in groups if not group.fled and not group.surrendered)
        )
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


def _validate_declaration(session, declaration: BattleDeclaration, member) -> list[Rejection]:
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
        if group is None or group.fled or group.surrendered:
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
        if group is None or group.fled or group.surrendered:
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
            # You know what you can't see — this rejection leaks nothing.
            return [], None, [Rejection(code="battle.declaration.invisible_target", params={"target": target_ref})]
        targets.append(entity)
        if isinstance(entity, MonsterInstance):
            for group in session.encounter.groups:
                if target_ref in group.monster_ids:
                    distance = max(distance or 0, group.distance_feet)
    return targets, distance, []


def _area_span_feet(shape: str | None, dimensions: dict, gap_feet: int) -> int:
    """The deterministic footprint span: diameter, length, or reach-limited length.

    A documented adaptation (see the adaptations register): the OSE SRD leaves how
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
    """Fill an area's capacity in stable spawn order; friendly fire appends the front rank."""
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

    Potions drink on the drinker (self); wands, staves, and rods target a group and
    resolve in the magic phase alongside casts — devices unleash magical effects,
    and resolving them there keeps the missile/melee ordering clean; scroll reads
    resolve in the magic phase too, through the declaration's spell fields.
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
        from osrlib.core.spells import caster_profile, validate_cast

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
        from osrlib.core.spells import CastContext

        return validate_cast(
            member,
            spell,
            mode,
            profile=None,
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
            if group is None or group.fled or group.surrendered:
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
            if group is not None and not group.fled and not group.surrendered:
                events.extend(exploration._device_area_events(session, member, instance, template, group))
        exploration._spend_device_charge(instance, template)
        return events
    return events


class _ScrollFields:
    """A duck-typed `UseItem`-shaped carrier for the exploration scroll reader."""

    def __init__(self, *, spell_id, mode, targets, target_id) -> None:
        self.spell_id = spell_id
        self.mode = mode
        self.targets = targets
        self.target_id = target_id


def _resolve_scroll_cast(session, member, instance, template, declaration: BattleDeclaration) -> list[Event]:
    from osrlib.core.spells import CastContext, cast_from_scroll
    from osrlib.crawl import exploration
    from osrlib.crawl.events import ItemUsedEvent

    remaining = tuple(str(spell) for spell in instance.state.get("spells", ()))
    if not remaining:
        return []
    spell_id = declaration.spell_id or remaining[0]
    spell = load_spells().get(spell_id)
    mode = declaration.spell_mode or spell.modes[0].key
    targets, distance, rejections = _cast_targets(
        session, declaration.model_copy(update={"spell_id": spell_id, "spell_mode": mode}), spell
    )
    if rejections:
        return []
    left = tuple(spell_name for spell_name in remaining if spell_name != spell_id) + tuple(
        spell_id for _ in range(remaining.count(spell_id) - 1)
    )
    if left:
        instance.state = {**instance.state, "spells": left}
    else:
        exploration._remove_magic_instance(member, instance)
    events: list[Event] = []
    events.extend(exploration._identify_item_events(session, member, instance))
    events.append(ItemUsedEvent(code="items.scroll.read", character_id=member.id, instance_id=instance.instance_id))
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
    if group is None or group.fled or group.surrendered:
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
    if rejections:
        # The whole command rejects listing every rejection — partial acceptance
        # would tangle the replay contract (pinned).
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
    # exactly like the party's (pinned) — the policy draw moves to the top of the
    # round, still on the monster_action stream.
    npc_actions = _declare_npc_actions(session, state, pending_casters, events)

    # Initiative: side blocks, party versus the monster side.
    participants = []
    for member, declaration in by_member.values():
        weapon = _find_wielded(member, declaration.weapon_id) if declaration.action == "attack" else None
        facet = _declaration_facet(weapon)
        slow = facet is not None and WeaponQuality.SLOW in getattr(facet, "qualities", ())
        from osrlib.core.combat import participant_modifier

        participants.append(Participant(key=member.id, side="party", slow=slow, modifier=participant_modifier(member)))
    active_groups = [group for group in session.encounter.groups if not group.fled and not group.surrendered]
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
    slow_attacks: list[tuple[object, BattleDeclaration]] = []

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

    # Slow-weapon actors act last, after both sides' blocks (the pinned ordering).
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
    """Consolidated formation movement, in order of precedence: retreat, withdrawal, close.

    The party moves as a single formation — individual members cannot leave it,
    as a documented adaptation (see the adaptations register; the Bard's Tale
    convention): every member retreating moves off at the full encounter rate
    (the OSE SRD's "full encounter movement rate" — the running pursuit begins
    once the battle converts); every member withdrawing backs off at half
    encounter rate; else the first `close` declaration in marching order advances
    the formation on its named group at encounter rate, stopping at 5'.
    """
    declarations = [declaration for _, declaration in by_member.values()]
    events: list[Event] = []
    multiplier = _party_move_multiplier(session)
    if declarations and all(d.action == "move" and d.move == "retreat" for d in declarations):
        rates = [_encounter_rate(member, session) for member in session.party.living_members()]
        rate = min(rates, default=0) * multiplier
        for group in session.encounter.groups:
            if group.fled or group.surrendered:
                continue
            group.distance_feet += rate
            events.append(GroupMovedEvent(group_id=group.id, distance_feet=group.distance_feet))
        return events
    if declarations and all(d.action == "move" and d.move == "fighting_withdrawal" for d in declarations):
        rates = [_encounter_rate(member, session) for member in session.party.living_members()]
        rate = (min(rates, default=0) // 2) * multiplier
        for group in session.encounter.groups:
            if group.fled or group.surrendered:
                continue
            group.distance_feet += rate
            events.append(GroupMovedEvent(group_id=group.id, distance_feet=group.distance_feet))
        return events
    for _member, declaration in by_member.values():
        if declaration.action == "move" and declaration.move == "close":
            group = _group_by_id(session, declaration.target_group_id)
            if group is None or group.fled or group.surrendered:
                continue
            rates = [_encounter_rate(living, session) for living in session.party.living_members()]
            rate = min(rates, default=0) * multiplier
            group.distance_feet = max(MELEE_RANGE_FEET, group.distance_feet - rate)
            events.append(GroupMovedEvent(group_id=group.id, distance_feet=group.distance_feet))
            break
    return events


def _party_move_multiplier(session) -> int:
    """*Haste*'s movement multiplier applies only when every living party member bears it.

    A documented adaptation (see the adaptations register).
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
                continue  # magic items resolve in the magic phase (pinned)
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
    if group is None or group.fled or group.surrendered:
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
        target = pool[0]  # the first living, visible monster in the reachable rank (pinned)
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

        # The first attack roll with an enchanted arm identifies it — and reveals
        # a curse, which sticks (pinned).
        events.extend(exploration._identify_item_events(session, member, weapon))
    if missile and weapon is not None:
        facet = _declaration_facet(weapon)
        if WeaponQuality.RELOAD in getattr(facet, "qualities", ()):
            fired.append(member.id)
    events.extend(_break_invisibility(session, member))
    return events


def _on_hit_drain(session, weapon: MagicItemInstance, target) -> list[Event]:
    """The energy-drain sword's on-hit drain: automatic on every hit.

    1d4+4 total drains were rolled at generation; once exhausted, the sword is a
    plain +1. The drain's lost-die rolls ride the advancement stream, since drain
    reverses advancement; the sword's own entry in the OSE SRD sets XP to the
    lowest amount for the new level (`level_minimum`), unlike the wight's halfway.
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

    A ward bars matching monsters up to its rolled per-HD-band count (1–3, 4–5,
    and 6+ HD bands), counted in spawn order among the encounter's matching
    monsters — or all of them for the elementals form.
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
    """Melee against a warded monster breaks the circle — the ward releases."""
    events: list[Event] = []
    for ward in _party_wards(session):
        if _ward_matches(target, ward.definition.params) and not ward.definition.params.get("unbreakable"):
            events.extend(session.ledger.release(ward.effect_id, session.registry()))
    return events


def _resolve_use_item(session, member, declaration, fire_damaged) -> list[Event]:
    from osrlib.crawl import exploration

    group = _group_by_id(session, declaration.target_group_id)
    if group is None or group.fled or group.surrendered:
        return []
    pool = _monster_pool(session, group)
    if not pool:
        return []
    instance = exploration._find_item(member, declaration.item_id)
    if instance is None:
        return []
    template = instance.template
    exploration._consume_item(member, declaration.item_id)
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


def _party_magic(session, by_member, pending_casters, disrupted, acted, state) -> list[Event]:
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
                # purposes (pinned); drinking is not.
                events.extend(_break_invisibility(session, member))
            acted.add(member.id)
            continue
        if declaration.action == "turn_undead":
            # Turning resolves in the magic phase but is never disruptable —
            # a class ability, not a spell (pinned).
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
        # location is the party's position (pinned).
        events.extend(exploration._stationary_silence(session, spell, result, targets))
        _track_concentration(session, member.id, spell, result, state)
        acted.add(member.id)
    # Any other declared action releases the actor's concentration (pinned).
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

    Mirroring the monster override: the above-2-HD re-save runs first on the
    magic stream (characters count their level), then `attack_caster_group`
    sends them at the nearest monster group's front rank when engaged (uniform
    pick, combat stream); `attack_own_group` at a fellow party member;
    `no_action` babbles.
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
                group
                for group in session.encounter.groups
                if not group.fled and not group.surrendered and _living_monsters(session, group)
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
        if group.fled or group.surrendered or not _living_monsters(session, group):
            continue
        if not free_round:
            events.extend(_group_morale(session, group, fire_damaged))
        if group.fleeing or _group_all_shaken(session, group):
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
                from osrlib.core.combat import cannot_move

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
        if group.fled or group.surrendered:
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
    """An NPC adventurer's weapon attack — the party's own kernel path."""
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


def _group_morale(session, group, fire_damaged) -> list[Event]:
    """Morale auto-invoked: the kernel's triggers through the per-battle tracker.

    Conditional alternates resolve by round context — fear-of-fire, for example,
    when the round's damage included fire — and the spell morale modifier folds
    in per the usual morale-modifier rule.
    """
    state = session.battle
    score = _group_morale_score(session, group)
    if score is None or group.fleeing:
        return []
    members = [session.combatant(monster_id) for monster_id in group.monster_ids]
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

    The re-save runs on the magic stream and the behavior roll (and its own-group
    target pick) on the combat stream — machine-run round rolls, distinct from the
    ledger's own tick-time effects.
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
    """The RAW trigger, machine-found: hit or failed save before the caster acts."""
    for event in events:
        target = getattr(event, "defender_id", None) or getattr(event, "target_id", None)
        if target not in pending_casters or target in acted or target in disrupted:
            continue
        if isinstance(event, AttackRolledEvent) and event.code in ("combat.attack.hit", "combat.attack.auto_hit"):
            disrupted.add(target)
        elif isinstance(event, SavingThrowRolledEvent) and event.code == "combat.save.failed":
            disrupted.add(target)


HANDLERS = {
    ResolveBattleRound: _handle_resolve_battle_round,
}
