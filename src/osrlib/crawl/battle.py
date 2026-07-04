"""The range-track battle state machine and the default monster action policy.

Battle wraps the kernel's combat functions in the SRD round sequence: declaration,
initiative, then per side — monster morale, movement, missiles, magic, melee — with
slow-weapon actors last. The combat space is the abstract per-group range track
(the Bard's Tale convention): each monster group sits at a distance from the party,
closing at encounter rate and melee-ing at 5'; party ranks derive from marching
order under the `formation_width_limit` flag (width 3 inside a keyed area, 2 in
corridor).

The machine detects spell disruption (a declared caster successfully attacked or
failing a save after initiative resolves against them but before their action),
auto-invokes morale, consumes the Phase 3 marker conditions and battle-bound spell
effects, and resolves area footprints deterministically: an area's capacity in
creatures is `ceil(span / 10) × width` filled in stable spawn order, cones
reach-limited, with the engaged party front rank appended under `aoe_friendly_fire`.

Policy draws come only from the `monster_action` stream, so a substituted policy
never shifts attack or damage draws (pinned). Individual initiative still resolves
side blocks — the sides order by their best individual total (pinned; the SRD's
phase sequence is per side).
"""

import math
from typing import Protocol

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
from osrlib.core.items import WeaponQuality
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
    "HANDLERS",
    "ActionPolicy",
    "BattleState",
    "MonsterAction",
    "ScriptedPolicy",
    "start_battle",
]

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
    """One monster's chosen action for the round."""

    model_config = ConfigDict(frozen=True)

    monster_id: str
    kind: str  # close | breath | melee | hold
    target_id: str | None = None


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
    """The default policy, pinned.

    Monsters with a scripted pattern in their data follow it: an `uses_per_day`
    breath weapon opens with breath, then breath or melee with equal chance while
    daily uses remain (the dragons, RAW); a `per_round_chance_in_six` gate rolls
    each round (the hellhound). Otherwise a group beyond 5' closes; at 5' it
    melees, each monster picking its target uniformly from the reachable party
    rank. Monster missile routines lack structured range data and stay
    close-then-melee (pinned, registered). Groups never cast.
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


def _breath_usable(monster: MonsterInstance, distance_feet: int) -> bool:
    if validate_breath(monster):
        return False
    params = monster.template.ability("breath_weapon").params
    length = params.get("length_feet")
    if length is not None and distance_feet >= int(length):
        return False
    return True


def _living_monsters(session, group) -> list[MonsterInstance]:
    return [
        session.monsters[monster_id]
        for monster_id in group.monster_ids
        if not has_condition(session.monsters[monster_id], Condition.DEAD)
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


def _reachable_targets(session, monster: MonsterInstance, pool: list) -> list:
    """Filter the pool by the *protection from evil* melee ban (pinned).

    A monster whose template bears a warded category may not initiate melee
    against a warded target of differing alignment; the ban breaks for a target
    who has engaged the barred creature in melee (RAW's own clause).
    """
    state = session.battle
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


def _group_front_rank(session, group) -> list[MonsterInstance]:
    """The group's reachable rank: its first `width` living members, in spawn order."""
    width = _formation_width(session)
    living = _living_monsters(session, group)
    return living if width is None else living[:width]


def _monster_pool(session, group) -> list[MonsterInstance]:
    return [monster for monster in _group_front_rank(session, group) if not has_condition(monster, Condition.INVISIBLE)]


def _group_morale_score(session, group) -> int | None:
    monster = session.monsters[group.monster_ids[0]]
    return monster.template.morale


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
        session: The running session.
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
    if weapon_id is None:
        return None
    for instance in member.inventory.wielded:
        if instance.template.id == weapon_id:
            return instance.template
    return None


def _is_missile_declaration(weapon, distance_feet: int) -> bool:
    if weapon is None:
        return False
    facet = getattr(weapon, "combat", weapon)
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
        attack = weapon.combat if hasattr(weapon, "combat") and weapon.combat is not None else weapon
        return validate_attack(member, pool[0], attack, context, ruleset=session.ruleset)
    if declaration.action == "use_item":
        group = _group_by_id(session, declaration.target_group_id)
        if group is None or group.fled or group.surrendered:
            return [Rejection(code="battle.declaration.unknown_group", params={"character": member.id})]
        from osrlib.crawl import exploration

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
        from osrlib.core.spells import CastContext

        return validate_cast(
            member,
            spell,
            declaration.spell_mode,
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
    """The pinned deterministic footprint span: diameter, length, or reach-limited length."""
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


# ---------------------------------------------------------------------- the round


def handle_resolve_battle_round(session, command: ResolveBattleRound) -> tuple[list[Rejection], list[Event]]:
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
    pending_casters: dict[str, BattleDeclaration] = {}
    for member, declaration in by_member.values():
        if declaration.action == "cast":
            pending_casters[member.id] = declaration
            events.append(
                SpellDeclaredEvent(caster_id=member.id, spell_id=declaration.spell_id, reversed=declaration.reversed)
            )

    # Initiative: side blocks, party versus the monster side.
    participants = []
    for member, declaration in by_member.values():
        weapon = _find_wielded(member, declaration.weapon_id) if declaration.action == "attack" else None
        facet = weapon.combat if weapon is not None and getattr(weapon, "combat", None) is not None else weapon
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

    # Slow-weapon actors act last, after both sides' blocks (the Phase 2 ordering).
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
    """Consolidated formation movement, pinned precedence: retreat, withdrawal, close.

    The party moves as a formation (individual members cannot leave it — the
    Bard's Tale convention, registered): every member retreating moves off at the
    full encounter rate (Combat.md's "full encounter movement rate" — the running
    pursuit begins when the battle converts); every member withdrawing backs off
    at half encounter rate; else the first `close` declaration in marching order
    advances the formation on its named group at encounter rate, stopping at 5'.
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
    """*Haste*'s movement multiplier applies when every living member bears it (pinned)."""
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
            events.extend(_resolve_use_item(session, member, declaration, fire_damaged))
            continue
        group = _group_by_id(session, declaration.target_group_id)
        if group is None:
            continue
        weapon = _find_wielded(member, declaration.weapon_id)
        is_missile = _is_missile_declaration(weapon, group.distance_feet)
        if is_missile != missile:
            continue
        facet = weapon.combat if weapon is not None and getattr(weapon, "combat", None) is not None else weapon
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
    attack = weapon.combat if weapon is not None and getattr(weapon, "combat", None) is not None else weapon
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
        if not missile:
            # Engaging in melee breaks the *protection from evil* ban against the
            # creature actually fought (RAW's own clause; the modifiers persist).
            engagements = state.melee_engagements.setdefault(member.id, [])
            if target.id not in engagements:
                engagements.append(target.id)
    if missile and weapon is not None:
        facet = weapon.combat if getattr(weapon, "combat", None) is not None else weapon
        if WeaponQuality.RELOAD in getattr(facet, "qualities", ()):
            fired.append(member.id)
    events.extend(_break_invisibility(session, member))
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
        if declaration.action not in ("cast", "turn_undead"):
            continue
        if incapacitated(member) or has_condition(member, Condition.CONFUSED):
            continue
        if declaration.action == "turn_undead":
            # Turning resolves in the magic phase but is never disruptable —
            # a class ability, not a spell (pinned).
            candidates = [
                session.monsters[monster_id] for group in session.encounter.groups for monster_id in group.monster_ids
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
        from osrlib.core.spells import CastContext

        result = cast_spell(
            member,
            spell,
            declaration.spell_mode,
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

    Mirroring the monster override (pinned): the above-2-HD re-save runs first on
    the magic stream (characters count their level), then `attack_caster_group`
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
    return events


# ---------------------------------------------------------------------- the monster block


def _policy_for(session, group) -> ActionPolicy:
    policies = getattr(session, "action_policies", None)
    if policies and group.id in policies:
        return policies[group.id]
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
        actions = _policy_for(session, group).choose(session, group, session.streams.get(MONSTER_ACTION_STREAM))
        events.extend(_confused_overrides(session, group, actions))
        moved = False
        for action in actions:
            monster = session.monsters[action.monster_id]
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
                events.extend(_resolve_monster_melee(session, monster, target, party_retreating=party_retreating))
                _watch_disruption(events, pending_casters, disrupted, acted)
    return events


def _group_all_shaken(session, group) -> bool:
    living = _living_monsters(session, group)
    return bool(living) and all(
        has_condition(monster, Condition.TURNED) or has_condition(monster, Condition.AFRAID) for monster in living
    )


def _pursuer_full_rate(session, group) -> int:
    monster = session.monsters[group.monster_ids[0]]
    modes = monster.template.movement
    base = next((mode for mode in modes if mode.descriptor is None), modes[0])
    return base.rate_feet


def _group_morale(session, group, fire_damaged) -> list[Event]:
    """Morale auto-invoked: the Phase 2 triggers through the per-battle tracker.

    Conditional alternates resolve by round context (fear-of-fire when the
    round's damage included fire, pinned example); the spell morale modifier
    folds per the Phase 3 rule.
    """
    state = session.battle
    score = _group_morale_score(session, group)
    if score is None or group.fleeing:
        return []
    members = [session.monsters[monster_id] for monster_id in group.monster_ids]
    triggers = morale_triggers(members)
    acted = state.morale_acted.setdefault(group.id, [])
    events: list[Event] = []
    for trigger in triggers:
        if trigger in acted:
            continue
        acted.append(trigger)
        effective = score
        first = session.monsters[group.monster_ids[0]]
        for alternate in first.template.morale_alternates:
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
    target pick) on the combat stream — machine-run round rolls, not ledger ticks
    (pinned; the Phase 3 tick-time convention is untouched).
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
            context = AttackContext(distance_feet=MELEE_RANGE_FEET, defender_retreating=party_retreating)
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
    return events


def _resolve_breath(session, monster, group) -> list[Event]:
    """A breath weapon against the party: the pinned rank coverage."""
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
    ResolveBattleRound: handle_resolve_battle_round,
}
