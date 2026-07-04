"""The Phase 3 milestone: cleric and magic-user play through combat using spells.

From one master seed: (1) the spell battle — a leveled cleric and magic-user (built
deterministically via creation plus XP awards) against goblins, a wight, and a red
dragon: preparation via `memorize_spells`, *bless* on the line, a declared *fire
ball* disrupted by an arrow (the RAW trigger scripted by hand: initiative lost, hit
before acting), the reprepared *fire ball* resolved per-target with saves and the
dragon's auto-save, *sleep* resolved by HD budget with the wight consuming nothing,
*magic missile* auto-hitting, *hold person* paralysing on a failed save, a *cause
light wounds* touch attack, and *cure light wounds* healing through the fight;
(2) the turning golden — a failed attempt from a level-1 cleric against a wraith
(the row shows `—`), a level-2 cleric turning a mixed skeleton/zombie group (T and
number cells, lowest-HD-first), a level-8 cleric destroying with permanent deaths,
and the minimum-one effect against spectres when the HD pool rolls short. Both
replay byte-for-byte from seed plus script, with formatted default-English
transcripts; regenerate with `uv run python tests/generate_phase3_goldens.py` and
explain why in the commit message.

Assertions are scoped per stream (`magic`, `combat`, `effects`, `monster_spawn`,
`character_creation`, `advancement`), so a combat-rules change never shifts spell
draws and vice versa.
"""

import json
from pathlib import Path

from osrlib.core.alignment import Alignment
from osrlib.core.character import create_character
from osrlib.core.classes import apply_xp
from osrlib.core.clock import GameClock
from osrlib.core.combat import AttackContext, Participant, resolve_attack, roll_initiative
from osrlib.core.effects import Condition, EffectsLedger, has_condition
from osrlib.core.monsters import IdAllocator, spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.spells import (
    MAGIC_STREAM,
    CastContext,
    MemorizedSpell,
    add_spell_to_book,
    cast_spell,
    disrupt_casting,
    memorize_spells,
    turn_undead,
)
from osrlib.data import load_classes, load_monsters, load_spells
from osrlib.messages import format_message

MASTER_SEED = 20_260_703
SPELL_BATTLE_GOLDEN_PATH = Path(__file__).parent / "goldens" / "phase3_spell_battle.json"
TURNING_GOLDEN_PATH = Path(__file__).parent / "goldens" / "phase3_turning.json"

MAX_ATTEMPTS = 60

MU_PREPARATION = [
    MemorizedSpell(spell_id="magic_missile"),
    MemorizedSpell(spell_id="sleep"),
    MemorizedSpell(spell_id="fire_ball"),
]
CLERIC_PREPARATION = [
    MemorizedSpell(spell_id="cure_light_wounds"),
    MemorizedSpell(spell_id="cure_light_wounds"),
    MemorizedSpell(spell_id="bless"),
    MemorizedSpell(spell_id="hold_person_c"),
]


def _level_to(character, definition, level, stream):
    while character.level < level:
        # Over-award: the one-level-per-award clamp yields exactly one level.
        apply_xp(character, definition, definition.row(definition.max_level).xp, stream)


def run_spell_battle() -> dict[str, object]:
    """Run the scripted spell battle and return its golden document."""
    streams = RngStreams(master_seed=MASTER_SEED)
    ruleset = Ruleset()
    clock = GameClock()
    ledger = EffectsLedger()
    allocator = IdAllocator()
    catalog = load_spells()
    classes = load_classes()
    events = []

    creation = streams.get("character_creation")
    advancement = streams.get("advancement")
    cleric = create_character(
        name="Aldara", class_id="cleric", alignment=Alignment.LAWFUL, ruleset=ruleset, stream=creation
    ).character
    cleric.id = "pc-cleric"
    magic_user = create_character(
        name="Betrys",
        class_id="magic_user",
        alignment=Alignment.NEUTRAL,
        ruleset=ruleset,
        stream=creation,
        starting_spell_ids=["sleep"],
    ).character
    magic_user.id = "pc-mu"
    cleric_definition, mu_definition = classes.get("cleric"), classes.get("magic_user")
    _level_to(cleric, cleric_definition, 6, advancement)
    _level_to(magic_user, mu_definition, 6, advancement)
    for spell_id in ("magic_missile", "web", "fire_ball"):
        result = add_spell_to_book(magic_user, mu_definition, catalog, spell_id)
        events.extend(result.events)

    monsters = load_monsters()
    spawn = streams.get("monster_spawn")
    goblins = [spawn_monster(monsters.get("goblin"), id=allocator.allocate("monster"), stream=spawn) for _ in range(6)]
    wight = spawn_monster(monsters.get("wight"), id=allocator.allocate("monster"), stream=spawn)
    dragon = spawn_monster(monsters.get("red_dragon"), id=allocator.allocate("monster"), stream=spawn)
    registry = {creature.id: creature for creature in (cleric, magic_user, *goblins, wight, dragon)}

    magic = streams.get(MAGIC_STREAM)
    effects = streams.get("effects")
    combat = streams.get("combat")

    def cast(caster, spell_id, mode, **kwargs):
        result = cast_spell(
            caster,
            catalog.get(spell_id),
            mode,
            ledger=ledger,
            clock=clock,
            allocator=allocator,
            registry=registry,
            ruleset=ruleset,
            stream=magic,
            effects_stream=effects,
            **kwargs,
        )
        events.extend(result.events)
        return result

    # Preparation: the daily memorization, one event per caster.
    events.extend(memorize_spells(cleric, cleric_definition, catalog, CLERIC_PREPARATION).events)
    events.extend(memorize_spells(magic_user, mu_definition, catalog, MU_PREPARATION).events)

    # Bless on the line before melee is joined.
    cast(cleric, "bless", "battle", targets=[cleric, magic_user])

    # The declared fire ball is disrupted: the monsters win initiative and a goblin's
    # arrow strikes the caster before she acts (the RAW trigger, scripted by hand).
    while True:
        initiative = roll_initiative(
            [Participant(key="party", side="party"), Participant(key="monsters", side="monsters")],
            ruleset=ruleset,
            stream=combat,
        )
        events.extend(initiative.events)
        if initiative.order[0] == "monsters":
            break
    goblin_attack = goblins[0].template.attacks[0].attacks[0]
    while True:
        result = resolve_attack(
            goblins[0],
            magic_user,
            goblin_attack,
            context=AttackContext(monster_missile=True),
            ruleset=ruleset,
            stream=combat,
            clock=clock,
        )
        events.extend(result.events)
        if result.attack_roll.hit:
            break
    events.extend(disrupt_casting(magic_user, "fire_ball"))

    # The next day's preparation restores the lost copy; the battle resumes.
    events.extend(memorize_spells(magic_user, mu_definition, catalog, MU_PREPARATION).events)

    # Fire ball, per-target against an explicit list: a goblin saves or burns, the
    # red dragon auto-saves against its own element.
    fire_ball = cast(magic_user, "fire_ball", "damage", targets=[goblins[0], dragon])
    dragon_save = next(
        event
        for event in fire_ball.events
        if event.event_type == "saving_throw_rolled" and event.target_id == dragon.id
    )

    # Sleep by HD budget over the goblin pack, the wight consuming nothing.
    sleep_targets = [goblins[3], goblins[4], goblins[5], wight]
    sleep = cast(magic_user, "sleep", "hd_budget", targets=sleep_targets)

    # Magic missile auto-hits: three missiles at level 6, stacked on the wight.
    missiles = cast(magic_user, "magic_missile", "missiles", targets=[wight, wight, wight])

    # Hold person until a goblin fails its save (re-preparing between attempts).
    hold_target = next(
        goblin
        for goblin in goblins
        if not has_condition(goblin, Condition.DEAD) and not has_condition(goblin, Condition.ASLEEP)
    )
    hold_attempts = 0
    while not has_condition(hold_target, Condition.PARALYSED):
        hold_attempts += 1
        assert hold_attempts <= MAX_ATTEMPTS
        if not any(copy.spell_id == "hold_person_c" for copy in cleric.memorized_spells):
            events.extend(memorize_spells(cleric, cleric_definition, catalog, CLERIC_PREPARATION).events)
        cast(cleric, "hold_person_c", "individual", targets=[hold_target])

    # A cause light wounds touch attack (the reversed form, chosen at cast time by
    # the divine caster), repeated until the touch lands.
    touch_target = wight if not has_condition(wight, Condition.DEAD) else hold_target
    touch_attempts = 0
    while True:
        touch_attempts += 1
        assert touch_attempts <= MAX_ATTEMPTS
        if not any(copy.spell_id == "cure_light_wounds" for copy in cleric.memorized_spells):
            events.extend(memorize_spells(cleric, cleric_definition, catalog, CLERIC_PREPARATION).events)
        touch = cast(
            cleric,
            "cure_light_wounds",
            "harm",
            reversed=True,
            targets=[touch_target],
            context=CastContext(in_combat=True),
        )
        attack = next(event for event in touch.events if event.event_type == "attack_rolled")
        if attack.code in ("combat.attack.hit", "combat.attack.auto_hit"):
            break

    # Cure light wounds heals the arrow wound through the fight.
    if not any(copy.spell_id == "cure_light_wounds" for copy in cleric.memorized_spells):
        events.extend(memorize_spells(cleric, cleric_definition, catalog, CLERIC_PREPARATION).events)
    heal = cast(cleric, "cure_light_wounds", "heal", targets=[magic_user])

    return {
        "master_seed": MASTER_SEED,
        "cleric_level": cleric.level,
        "magic_user_level": magic_user.level,
        "magic_user_book": list(magic_user.spell_book),
        "dragon_auto_saved": dragon_save.code == "combat.save.auto",
        "wight_slept": has_condition(wight, Condition.ASLEEP),
        "sleep_affected": list(sleep.affected_ids),
        "missile_targets": list(missiles.affected_ids),
        "hold_attempts": hold_attempts,
        "held_target": hold_target.id,
        "touch_attempts": touch_attempts,
        "healed": [event.amount for event in heal.events if event.code == "combat.healing.applied"],
        "events": [event.model_dump(mode="json") for event in events],
        "transcript": [format_message(event) for event in events],
    }


def run_turning_scenario() -> dict[str, object]:
    """Run the scripted turning scenario and return its golden document."""
    streams = RngStreams(master_seed=MASTER_SEED)
    ruleset = Ruleset()
    clock = GameClock()
    ledger = EffectsLedger()
    allocator = IdAllocator()
    classes = load_classes()
    cleric_definition = classes.get("cleric")
    monsters = load_monsters()
    spawn = streams.get("monster_spawn")
    magic = streams.get(MAGIC_STREAM)
    events = []
    registry = {}

    cleric = create_character(
        name="Osric",
        class_id="cleric",
        alignment=Alignment.LAWFUL,
        ruleset=ruleset,
        stream=streams.get("character_creation"),
    ).character
    cleric.id = "pc-cleric"
    registry[cleric.id] = cleric

    def spawn_group(*monster_ids):
        group = []
        for monster_id in monster_ids:
            instance = spawn_monster(monsters.get(monster_id), id=allocator.allocate("monster"), stream=spawn)
            registry[instance.id] = instance
            group.append(instance)
        return group

    def turn(candidates):
        result = turn_undead(
            cleric,
            cleric_definition,
            candidates,
            ledger=ledger,
            clock=clock,
            allocator=allocator,
            registry=registry,
            stream=magic,
        )
        events.extend(result.events)
        return result

    # Scene 1: a level-1 cleric against a wraith — the printed cell is an em-dash.
    wraith = spawn_group("wraith")[0]
    failed = turn([wraith])

    # Scene 2: level 2 against skeletons and zombies — T and number cells, affected
    # lowest-HD-first. Fresh groups per attempt until the zombies' threshold is met.
    _level_to(cleric, cleric_definition, 2, streams.get("advancement"))
    attempts = 0
    while True:
        attempts += 1
        assert attempts <= MAX_ATTEMPTS
        group = spawn_group("skeleton", "skeleton", "zombie", "zombie")
        mixed = turn(group)
        zombie_outcome = next(outcome for outcome in mixed.outcomes if outcome.template_id == "zombie")
        if zombie_outcome.outcome == "turn" and mixed.affected_ids:
            break

    # Scene 3: level 8 destroys — D results are permanent annihilation.
    _level_to(cleric, cleric_definition, 8, streams.get("advancement"))
    destroy_group = spawn_group("skeleton", "zombie")
    destroyed = turn(destroy_group)

    # Scene 4: the minimum-one effect — spectres cost 6, so a short pool still
    # affects the cheapest eligible monster.
    minimum_attempts = 0
    while True:
        minimum_attempts += 1
        assert minimum_attempts <= MAX_ATTEMPTS
        spectres = spawn_group("spectre", "spectre")
        short = turn(spectres)
        if short.hd_pool is not None and short.hd_pool < 6:
            break

    return {
        "master_seed": MASTER_SEED,
        "failed_roll": failed.roll,
        "mixed_attempts": attempts,
        "mixed_roll": mixed.roll,
        "mixed_pool": mixed.hd_pool,
        "mixed_affected": list(mixed.affected_ids),
        "destroyed_ids": list(destroyed.destroyed_ids),
        "minimum_attempts": minimum_attempts,
        "minimum_pool": short.hd_pool,
        "minimum_affected": list(short.affected_ids),
        "events": [event.model_dump(mode="json") for event in events],
        "transcript": [format_message(event) for event in events],
    }


def _render(document: dict[str, object]) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


class TestSpellBattleGolden:
    def test_matches_golden_byte_for_byte(self):
        document = run_spell_battle()
        assert _render(document) == SPELL_BATTLE_GOLDEN_PATH.read_text(encoding="utf-8"), (
            "golden mismatch; if the change is intentional, regenerate with "
            "`uv run python tests/generate_phase3_goldens.py` and explain why in the commit message"
        )

    def test_milestone_behaviors(self):
        document = run_spell_battle()
        codes = [event["code"] for event in document["events"]]
        assert codes.count("magic.memorize.prepared") >= 2
        assert "magic.cast.disrupted" in codes
        # The disruption's RAW trigger precedes it: initiative, then the hit.
        disrupted_at = codes.index("magic.cast.disrupted")
        assert "combat.initiative.rolled" in codes[:disrupted_at]
        assert "combat.attack.hit" in codes[:disrupted_at]
        assert document["dragon_auto_saved"] is True
        assert document["wight_slept"] is False
        assert document["sleep_affected"]  # the goblins slept; the wight consumed nothing
        # Three level-6 missiles stacked on the one wight: one affected target,
        # three auto-hit damage packets, no attack rolls, no saves.
        assert len(document["missile_targets"]) == 1
        missiles_cast = next(
            index
            for index, event in enumerate(document["events"])
            if event["code"] == "magic.cast.cast" and event.get("spell_id") == "magic_missile"
        )
        tail = document["events"][missiles_cast + 1 :]
        until_next_cast = next(
            (index for index, event in enumerate(tail) if event["code"].startswith("magic.")), len(tail)
        )
        missile_events = tail[:until_next_cast]
        assert sum(1 for event in missile_events if event["code"] == "combat.damage.dealt") == 3
        assert not any(event["event_type"] == "attack_rolled" for event in missile_events)
        assert not any(event["event_type"] == "saving_throw_rolled" for event in missile_events)
        assert document["healed"] and all(amount >= 0 for amount in document["healed"])
        held = next(
            event
            for event in document["events"]
            if event["code"] == "effects.condition.gained" and event["condition"] == "paralysed"
        )
        assert held["target_id"] == document["held_target"]

    def test_replay_determinism(self):
        assert _render(run_spell_battle()) == _render(run_spell_battle())


class TestTurningGolden:
    def test_matches_golden_byte_for_byte(self):
        document = run_turning_scenario()
        assert _render(document) == TURNING_GOLDEN_PATH.read_text(encoding="utf-8"), (
            "golden mismatch; if the change is intentional, regenerate with "
            "`uv run python tests/generate_phase3_goldens.py` and explain why in the commit message"
        )

    def test_milestone_behaviors(self):
        document = run_turning_scenario()
        codes = [event["code"] for event in document["events"]]
        assert codes[0] == "magic.turning.failed"  # level 1 versus the wraith's em-dash
        assert "magic.turning.turned" in codes
        assert "magic.turning.destroyed" in codes
        assert document["destroyed_ids"]
        permanent = [event for event in document["events"] if event["code"] == "combat.death.permanent"]
        assert {event["target_id"] for event in permanent} >= set(document["destroyed_ids"])
        assert len(document["minimum_affected"]) == 1  # the RAW minimum effect
        assert document["minimum_pool"] < 6

    def test_replay_determinism(self):
        assert _render(run_turning_scenario()) == _render(run_turning_scenario())
