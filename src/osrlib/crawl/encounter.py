"""The encounter procedure: surprise, distance, reaction, parley, evasion, pursuit.

An encounter — wandering, keyed on area entry, or referee-spawned through
[`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters] — opens with surprise,
distance, and reaction, then runs in round beats: each encounter command is one
round, and the monsters act per their stance after it. Battle opens through
[`osrlib.crawl.battle`][osrlib.crawl.battle] when a stance or the party demands it.

The stance map resolves bands the OSE SRD leaves to a human referee: 2- attacks
now; 3–5 hostile — the monsters attack at the end of the next encounter round
unless the party has begun evading or improved the stance by parley; 6–8 uncertain
— hold, posture, re-roll next round at +0; 9–11 indifferent — the party may pass,
parley, or withdraw freely; 12+ friendly. Only the attacks/hostile stances pursue
an evading party, as a documented adaptation (see the adaptations register) — RAW
leaves pursuit itself to the referee too, keyed here to low reactions.
"""

from pydantic import BaseModel, ConfigDict, Field

from osrlib.core.combat import roll_reaction
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
"""The round at which a running pursuit gives up: the terminal escape valve.

With the party no faster than its pursuers, the gap never grows, so a pursuit
that reaches this round ends in exhaustion rather than running forever.
"""


class EncounterGroup(BaseModel):
    """One monster group in an encounter: its members and range-track distance.

    `member_treasure` and `group_treasure` are the carried bundles generated at
    spawn (individual P–T per monster, group U–V per group): slain and surrendered
    members' bundles drop as loot at battle end; routed ones flee with theirs.
    """

    model_config = ConfigDict(validate_assignment=True)

    id: str
    label: str
    monster_ids: list[str] = Field(min_length=1)
    distance_feet: int = Field(ge=0)
    fleeing: bool = False
    fled: bool = False
    surrendered: bool = False
    member_treasure: dict[str, TreasureBundle] = {}
    group_treasure: TreasureBundle | None = None


class PursuitState(BaseModel):
    """A running pursuit: the abstract gap, updated round by round."""

    model_config = ConfigDict(validate_assignment=True)

    round: int = 0
    gap_feet: int = Field(ge=0)


class EncounterState(BaseModel):
    """The serialized encounter: groups, stance, surprise, and the pursuit."""

    model_config = ConfigDict(validate_assignment=True)

    kind: str
    area_ref: str | None = None
    groups: list[EncounterGroup] = Field(min_length=1)
    stance: str | None = None
    round: int = 0
    started_round: int
    party_surprised: bool = False
    monsters_surprised: bool = False
    monsters_skip_rounds: int = 0
    hostile_deadline: int | None = None
    evading: bool = False
    pursuit: PursuitState | None = None


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
    """Open an encounter: surprise, distance, reaction, and the first consequences.

    Wandering monsters never roll for surprise (they come "moving in the direction
    of the party"); a keyed area's `aware` flag, a failed door forcing, and the
    lit-party rule each skip the monsters' roll instead; a successful listen marks
    the party aware. The party is surprised on 1–2 — 1–3 when unlit and not every
    living member has infravision, as a documented adaptation (see the
    adaptations register; the blind-party adaptation).

    Args:
        session (osrlib.crawl.session.GameSession): The running session.
        groups: `(label, instances)` pairs, instances already in the registry.
        kind: `"wandering"`, `"keyed"`, or `"spawned"`.
        area_ref: The keyed area's state reference, when keyed.
        distance_feet: A fixed distance; `None` rolls 2d6 × 10 on the encounter
            stream.
        monsters_roll_surprise: False when the monsters can never be surprised.
        monsters_aware: True when the monsters expect intruders.
        party_aware: True when the party heard the room.
        pinned_stance: A keyed stance that skips the reaction roll.

    Returns:
        The encounter-opening events.
    """
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

    Args:
        session (osrlib.crawl.session.GameSession): The running session.
        party_lost_beat: True when this beat is the surprised party's lost round —
            a battle opening here starts with the monsters' free round.
    """
    state = session.encounter
    if state is None or session.battle is not None:
        return []
    state.round += 1
    events = session.advance_rounds(1)
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

    pursuers = [group for group in state.groups if not group.fled and not group.surrendered]
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


def _pursuer_rate(session, groups) -> int:
    """The slowest pursuing group's base ground mode, full rate per round.

    Slowest-of-pursuers mirrors slowest-of-party; a pack that strings out is
    fiction. Flying reads dungeon ceilings: the base ground mode is the mode
    with no descriptor, else the first printed.
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

    NPC adventuring parties are always intelligent for the distraction roll,
    regardless of treasure letters — they are people.
    """
    combatant = session.combatant(group.monster_ids[0])
    if getattr(combatant, "definition", None) is not None:
        return True
    return bool(combatant.template.treasure.letters)


def _pursuit_round(session, *, dropped_kind: str | None = None) -> list[Event]:
    """Run one pursuit round: distraction, the gap update, and the terminals."""
    from osrlib.crawl.session import ENCOUNTER_STREAM

    state = session.encounter
    pursuit = state.pursuit
    pursuit.round += 1
    events = session.advance_rounds(1)
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

    Surrender hands it over — the pile mechanism is already the recovery surface;
    routed monsters flee with theirs, and a group whose members routed or fled
    keeps its shared bundle.
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
    """Conclude the encounter: defeats, effect release, the minimum-turn clock owe.

    Defeated, routed, and surrendered monsters post `MonsterDefeatedEvent`s to the
    ledger; `turned` effects on routed undead release; remaining effects on the
    encounter's monsters release too — the fiction moves on (a dead troll's
    pending revival is game narration after the battle). The clock advances to
    `max(next turn boundary, encounter start + one turn)`, with the wandering
    cadence still suspended: when the encounter started mid-turn, the
    minimum-one-turn clause dominates, so the conclusion may itself land mid-turn
    — the boundary clause only guarantees the boundary is reached.
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
