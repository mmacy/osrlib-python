"""The Phase 2 milestone: two scripted golden battles resolved through kernel calls.

From one master seed: (1) the Phase 1 seven-class party versus 1d8 trolls — spawn
count and HP rolled, side initiative each round, matrix attacks, troll regeneration
ticking on the ledger clock, a burning-oil kill proving the non-regenerable path,
first-death and half-side morale checks against the fear-of-fire alternate, and a
troll revival event; (2) a wight fight — a mundane sword absorbed by the immunity
gate, a silver dagger connecting, and a drained level with the HP roll and
floored-halfway XP. Full event streams and formatted default-English transcripts are
compared byte-for-byte against the committed goldens; regenerate with
`uv run python tests/generate_phase2_goldens.py` and explain why in the commit
message.

Assertions are scoped per stream (`combat`, `effects`, `monster_spawn`,
`character_creation`, `advancement`), so a creation-rules change never shifts battle
draws. `load`-equivalence is deferred to Phase 4 (no saves yet); replay determinism
is asserted by re-running from the seed.
"""

import json
from pathlib import Path

from osrlib.core.classes import apply_xp
from osrlib.core.clock import GameClock, TimeUnit
from osrlib.core.combat import (
    COMBAT_STREAM,
    AttackContext,
    MoraleTracker,
    Participant,
    morale_triggers,
    resolve_attack,
    resolve_energy_drain,
    resolve_splash_attack,
    roll_initiative,
)
from osrlib.core.dice import roll
from osrlib.core.effects import (
    EFFECTS_STREAM,
    Condition,
    EffectsLedger,
    has_condition,
    regeneration_definition,
)
from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.data import load_classes, load_equipment, load_monsters
from osrlib.messages import format_message
from test_creation_goldens import MASTER_SEED, build_golden_party

# The battles share Phase 1's master seed: "the Phase 1 seven-class party" is
# literally that party — its creation draws come from the same character_creation
# stream, and per-stream scoping keeps the battle draws independent of it.
TROLL_GOLDEN_PATH = Path(__file__).parent / "goldens" / "phase2_troll_battle.json"
WIGHT_GOLDEN_PATH = Path(__file__).parent / "goldens" / "phase2_wight_battle.json"

MAX_ROUNDS = 120

# Each melee party member's armament in the battle script (from the golden kits).
MELEE_ARMS = {"cleric": "mace", "dwarf": "war_hammer", "elf": "sword", "fighter": "sword", "thief": "dagger"}
OIL_THROWERS = ("magic_user", "cleric", "thief", "halfling")


def _named_party(streams: RngStreams) -> list:
    party = [result.character for result in build_golden_party(streams)]
    for character in party:
        character.id = f"pc-{character.class_id}"
    return party


def run_troll_battle() -> dict[str, object]:
    """Run the scripted troll battle and return its golden document."""
    streams = RngStreams(master_seed=MASTER_SEED)
    ruleset = Ruleset()
    allocator = IdAllocator()
    clock = GameClock()
    ledger = EffectsLedger()
    equipment = load_equipment()
    events = []

    party = _named_party(streams)
    by_class = {character.class_id: character for character in party}

    template = load_monsters().get("troll")
    spawn_stream = streams.get(MONSTER_SPAWN_STREAM)
    troll_count = roll("1d8", spawn_stream).total
    trolls = [
        spawn_monster(template, id=allocator.allocate("monster"), stream=spawn_stream) for _ in range(troll_count)
    ]
    registry = {character.id: character for character in party} | {troll.id: troll for troll in trolls}
    regeneration = regeneration_definition(template.ability("regeneration").params)
    for troll in trolls:
        _, attach_events = ledger.attach(regeneration, troll.id, clock=clock, allocator=allocator, registry=registry)
        events.extend(attach_events)

    combat_stream = streams.get(COMBAT_STREAM)
    effects_stream = streams.get(EFFECTS_STREAM)
    tracker = MoraleTracker()
    acted_triggers: set[str] = set()
    target = trolls[0]
    troll_bite = template.attacks[0].attacks[2 if len(template.attacks[0].attacks) > 2 else -1]
    phase = "melee_kill"
    revived = False
    permanent = False
    rounds = 0
    respite_rounds = 0

    def melee_attacks(victim):
        for class_id, weapon_id in MELEE_ARMS.items():
            attacker = by_class[class_id]
            if has_condition(attacker, Condition.DEAD) or has_condition(victim, Condition.DEAD):
                continue
            result = resolve_attack(
                attacker,
                victim,
                equipment.get(weapon_id),
                context=AttackContext(),
                ruleset=ruleset,
                stream=combat_stream,
                clock=clock,
            )
            events.extend(result.events)

    def oil_attacks(victim):
        went_permanent = False
        for class_id in OIL_THROWERS:
            attacker = by_class[class_id]
            if has_condition(attacker, Condition.DEAD):
                continue
            result = resolve_splash_attack(
                attacker,
                victim,
                equipment.get("oil_flask"),
                context=AttackContext(distance_feet=20, lit=True),
                ruleset=ruleset,
                stream=combat_stream,
                ledger=ledger,
                clock=clock,
                allocator=allocator,
                registry=registry,
            )
            events.extend(result.events)
            if any(event.code == "combat.death.permanent" for event in result.events):
                went_permanent = True
                break
        return went_permanent

    while rounds < MAX_ROUNDS and not (permanent and "half_incapacitated" in acted_triggers):
        rounds += 1
        participants = [Participant(key="party", side="party"), Participant(key="trolls", side="trolls")]
        initiative = roll_initiative(participants, ruleset=ruleset, stream=combat_stream)
        events.extend(initiative.events)

        if phase == "melee_kill":
            melee_attacks(target)
        elif phase == "respite":
            # The party regroups: three undisturbed rounds after its last wound, the
            # revived troll's regeneration starts ticking on the ledger clock.
            respite_rounds += 1
            if respite_rounds >= 5:
                phase = "burn"
        elif phase == "burn":
            if oil_attacks(target):
                permanent = True
                phase = "cleanup"
        elif phase == "cleanup":
            # Focus fire the next living troll until half the side is down.
            victim = next((troll for troll in trolls if not has_condition(troll, Condition.DEAD)), None)
            if victim is not None:
                melee_attacks(victim)
                oil_attacks(victim)

        if not has_condition(target, Condition.DEAD) and not has_condition(by_class["fighter"], Condition.DEAD):
            result = resolve_attack(
                target,
                by_class["fighter"],
                troll_bite,
                context=AttackContext(),
                ruleset=ruleset,
                stream=combat_stream,
                clock=clock,
            )
            events.extend(result.events)

        for trigger in morale_triggers(trolls):
            if trigger not in acted_triggers:
                acted_triggers.add(trigger)
                # The party fights with fire, so the fear-of-fire alternate score applies.
                check = tracker.check("trolls", template.morale_alternates[0].score, stream=combat_stream)
                if check is not None:
                    events.extend(check.events)

        boundary_events = ledger.advance(clock, 1, TimeUnit.ROUND, registry, stream=effects_stream)
        events.extend(boundary_events)
        for event in boundary_events:
            if event.code == "effects.regeneration.revived":
                revived = True
            if event.code == "combat.death.permanent":
                permanent = True
                if phase == "burn":
                    phase = "cleanup"
        if phase == "melee_kill" and has_condition(target, Condition.DEAD):
            phase = "await_revival"
        if phase == "await_revival" and revived:
            phase = "respite"

    return {
        "master_seed": MASTER_SEED,
        "troll_count": troll_count,
        "troll_max_hp": [troll.max_hp for troll in trolls],
        "rounds": rounds,
        "revived": revived,
        "permanent_kill": permanent,
        "morale_triggers": sorted(acted_triggers),
        "events": [event.model_dump(mode="json") for event in events],
        "transcript": [format_message(event) for event in events],
    }


def run_wight_battle() -> dict[str, object]:
    """Run the scripted wight fight and return its golden document."""
    streams = RngStreams(master_seed=MASTER_SEED)
    ruleset = Ruleset()
    allocator = IdAllocator()
    equipment = load_equipment()
    events = []

    party = _named_party(streams)
    fighter = next(character for character in party if character.class_id == "fighter")
    apply_xp(fighter, load_classes().get("fighter"), 2_500, streams.get("advancement"))
    assert fighter.level == 2

    wight = spawn_monster(
        load_monsters().get("wight"), id=allocator.allocate("monster"), stream=streams.get(MONSTER_SPAWN_STREAM)
    )
    combat_stream = streams.get(COMBAT_STREAM)
    context = AttackContext()

    # A mundane sword connects — and the immunity gate absorbs the hit.
    while True:
        result = resolve_attack(
            fighter, wight, equipment.get("sword"), context=context, ruleset=ruleset, stream=combat_stream
        )
        events.extend(result.events)
        if result.attack_roll.hit:
            assert result.absorbed
            break

    # The silver dagger connects.
    while True:
        result = resolve_attack(
            fighter, wight, equipment.get("silver_dagger"), context=context, ruleset=ruleset, stream=combat_stream
        )
        events.extend(result.events)
        if result.attack_roll.hit:
            assert not result.absorbed
            break

    # The wight's touch lands and drains a level.
    touch = wight.template.attacks[0].attacks[0]
    while True:
        result = resolve_attack(wight, fighter, touch, context=context, ruleset=ruleset, stream=combat_stream)
        events.extend(result.events)
        if result.attack_roll.hit:
            events.extend(resolve_energy_drain(wight, fighter, stream=streams.get("advancement")))
            break

    # The party finishes the fight.
    while not has_condition(wight, Condition.DEAD):
        result = resolve_attack(
            fighter, wight, equipment.get("silver_dagger"), context=context, ruleset=ruleset, stream=combat_stream
        )
        events.extend(result.events)

    return {
        "master_seed": MASTER_SEED,
        "wight_max_hp": wight.max_hp,
        "fighter_level": fighter.level,
        "fighter_xp": fighter.xp,
        "fighter_hp": [fighter.current_hp, fighter.max_hp],
        "events": [event.model_dump(mode="json") for event in events],
        "transcript": [format_message(event) for event in events],
    }


def _render(document: dict[str, object]) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


class TestTrollBattleGolden:
    def test_matches_golden_byte_for_byte(self):
        document = run_troll_battle()
        assert _render(document) == TROLL_GOLDEN_PATH.read_text(encoding="utf-8"), (
            "golden mismatch; if the change is intentional, regenerate with "
            "`uv run python tests/generate_phase2_goldens.py` and explain why in the commit message"
        )

    def test_milestone_behaviors(self):
        document = run_troll_battle()
        codes = [event["code"] for event in document["events"]]
        assert document["revived"] and "effects.regeneration.revived" in codes
        assert document["permanent_kill"] and "combat.death.permanent" in codes
        assert document["morale_triggers"] == ["first_death", "half_incapacitated"]
        assert any(code.startswith("combat.morale.") for code in codes)
        assert codes.count("combat.initiative.rolled") == document["rounds"]
        assert any(
            event.get("non_regenerable") for event in document["events"] if event["code"] == "combat.damage.dealt"
        )
        assert "combat.healing.applied" in codes  # regeneration ticked

    def test_replay_determinism(self):
        assert _render(run_troll_battle()) == _render(run_troll_battle())


class TestWightBattleGolden:
    def test_matches_golden_byte_for_byte(self):
        document = run_wight_battle()
        assert _render(document) == WIGHT_GOLDEN_PATH.read_text(encoding="utf-8"), (
            "golden mismatch; if the change is intentional, regenerate with "
            "`uv run python tests/generate_phase2_goldens.py` and explain why in the commit message"
        )

    def test_milestone_behaviors(self):
        document = run_wight_battle()
        codes = [event["code"] for event in document["events"]]
        assert "combat.damage.absorbed" in codes  # the mundane sword
        absorbed_at = codes.index("combat.damage.absorbed")
        assert "combat.damage.dealt" not in codes[:absorbed_at]  # nothing mundane got through first
        assert "combat.drain.drained" in codes
        drained = next(event for event in document["events"] if event["code"] == "combat.drain.drained")
        assert drained["levels_lost"] == 1
        # Fighter L2 threshold 2,000, L1 threshold 0: floored halfway is 1,000.
        assert drained["xp_after"] == 1_000 and document["fighter_xp"] == 1_000
        assert document["fighter_level"] == 1
        assert "becomes a wight" in drained["spawn_consequence"]
        assert codes[-2:] == ["combat.death.died", "combat.state.hit_points"] or "combat.death.died" in codes

    def test_replay_determinism(self):
        assert _render(run_wight_battle()) == _render(run_wight_battle())
