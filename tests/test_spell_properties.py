"""Property tests for the magic subsystem (hypothesis)."""

from hypothesis import given, settings
from hypothesis import strategies as st

from osrlib.core.abilities import AbilityScore
from osrlib.core.alignment import Alignment
from osrlib.core.character import Character
from osrlib.core.classes import drain_levels
from osrlib.core.effects import ActiveModifier, modifier_total
from osrlib.core.rng import RngStreams
from osrlib.core.spells import CastContext, MemorizedSpell, caster_profile, memorize_spells, validate_cast
from osrlib.data import load_classes, load_spells

ALL_SPELL_IDS = sorted(spell.id for spell in load_spells().spells)
SOME_SPELL_IDS = [*ALL_SPELL_IDS[:12], "sleep", "fire_ball", "cure_light_wounds", "hold_person_c", "nonsense_spell"]


def make_caster(class_id, level):
    definition = load_classes().get(class_id)
    return Character(
        id="pc",
        name="Prop",
        class_id=class_id,
        race=definition.race,
        level=min(level, definition.max_level),
        xp=definition.row(min(level, definition.max_level)).xp,
        scores={ability: 10 for ability in AbilityScore},
        alignment=Alignment.NEUTRAL,
        max_hp=8,
        current_hp=8,
    )


@settings(max_examples=60, deadline=None)
@given(
    class_id=st.sampled_from(["cleric", "magic_user", "elf", "fighter"]),
    level=st.integers(min_value=1, max_value=14),
    spell_ids=st.lists(st.sampled_from(SOME_SPELL_IDS), max_size=8),
    reversed_flags=st.lists(st.booleans(), min_size=8, max_size=8),
    book=st.lists(st.sampled_from(SOME_SPELL_IDS), max_size=4),
)
def test_memorize_never_raises_and_never_overfills(class_id, level, spell_ids, reversed_flags, book):
    """Fuzzed memorize inputs reject, never throw — and legal ones never exceed slots."""
    caster = make_caster(class_id, level)
    caster.spell_book = tuple(dict.fromkeys(spell for spell in book if spell != "nonsense_spell"))
    definition = load_classes().get(class_id)
    catalog = load_spells()
    selections = [
        MemorizedSpell(spell_id=spell_id, reversed=flag)
        for spell_id, flag in zip(spell_ids, reversed_flags, strict=False)
    ]
    result = memorize_spells(caster, definition, catalog, selections)
    slots = definition.row(caster.level).spell_slots
    counts: dict[int, int] = {}
    for copy in caster.memorized_spells:
        spell_level = catalog.get(copy.spell_id).level
        counts[spell_level] = counts.get(spell_level, 0) + 1
    for spell_level, count in counts.items():
        allowed = slots[spell_level - 1] if spell_level <= len(slots) else 0
        assert count <= allowed
    if result.rejections:
        assert not result.events


@settings(max_examples=60, deadline=None)
@given(
    spell_id=st.sampled_from(["sleep", "fire_ball", "cure_light_wounds", "hold_person_c", "magic_missile", "web"]),
    mode=st.sampled_from(["cast", "damage", "heal", "hd_budget", "individual", "missiles", "entangle", "bogus"]),
    reversed=st.booleans(),
    target_count=st.integers(min_value=0, max_value=6),
    distance=st.one_of(st.none(), st.integers(min_value=0, max_value=500)),
)
def test_validate_cast_never_raises(spell_id, mode, reversed, target_count, distance):
    """Fuzzed cast inputs reject, never throw, draw nothing, and mutate nothing."""
    caster = make_caster("magic_user", 5)
    caster.spell_book = ("sleep", "fire_ball", "magic_missile", "web")
    caster.memorized_spells = (MemorizedSpell(spell_id="sleep"), MemorizedSpell(spell_id="fire_ball"))
    targets = [make_caster("fighter", 1) for _ in range(target_count)]
    before = caster.memorized_spells
    rejections = validate_cast(
        caster,
        load_spells().get(spell_id),
        mode,
        profile=caster_profile(load_classes().get(caster.class_id)),
        reversed=reversed,
        targets=targets,
        context=CastContext(distance_feet=distance),
    )
    assert isinstance(rejections, list)
    assert caster.memorized_spells == before


@settings(max_examples=40, deadline=None)
@given(
    seed=st.integers(min_value=0, max_value=10_000),
    memorize_count=st.integers(min_value=0, max_value=2),
    drained=st.integers(min_value=1, max_value=3),
)
def test_slot_invariant_survives_memorize_then_drain(seed, memorize_count, drained):
    """After any legal memorize + drain sequence, counts never exceed the new slots."""
    caster = make_caster("cleric", 6)
    definition = load_classes().get("cleric")
    catalog = load_spells()
    selections = [MemorizedSpell(spell_id="cure_light_wounds")] * memorize_count
    memorize_spells(caster, definition, catalog, selections)
    streams = RngStreams(master_seed=seed)
    drain_levels(caster, definition, levels=drained, xp_policy="level_minimum", stream=streams.get("advancement"))
    if caster.level >= 1 and caster.current_hp > 0:
        slots = definition.row(caster.level).spell_slots
        counts: dict[int, int] = {}
        for copy in caster.memorized_spells:
            spell_level = catalog.get(copy.spell_id).level
            counts[spell_level] = counts.get(spell_level, 0) + 1
        for spell_level, count in counts.items():
            allowed = slots[spell_level - 1] if spell_level <= len(slots) else 0
            assert count <= allowed


@settings(max_examples=60, deadline=None)
@given(
    values=st.lists(st.integers(min_value=-3, max_value=3), min_size=0, max_size=6),
    order_seed=st.randoms(use_true_random=False),
)
def test_modifier_consultation_is_order_independent(values, order_seed):
    """The cumulative rule's total never depends on attachment order."""
    modifiers = [
        ActiveModifier(kind="attack_bonus", value=value, effect_id=f"effect-{index:04d}")
        for index, value in enumerate(values)
    ]
    caster = make_caster("fighter", 1)
    caster.stat_modifiers = tuple(modifiers)
    total = modifier_total(caster, "attack_bonus")
    shuffled = list(modifiers)
    order_seed.shuffle(shuffled)
    caster.stat_modifiers = tuple(shuffled)
    assert modifier_total(caster, "attack_bonus") == total
    bonus = max((value for value in values if value > 0), default=0)
    penalty = min((value for value in values if value < 0), default=0)
    assert total == bonus + penalty


@settings(max_examples=40, deadline=None)
@given(seed=st.integers(min_value=0, max_value=10_000), group=st.integers(min_value=1, max_value=6))
def test_turning_pool_never_overspends(seed, group):
    """The pool never affects a monster it cannot afford, except the minimum-one case."""
    from osrlib.core.clock import GameClock
    from osrlib.core.effects import EffectsLedger
    from osrlib.core.monsters import IdAllocator, spawn_monster
    from osrlib.core.spells import MAGIC_STREAM, turn_undead
    from osrlib.data import load_monsters

    streams = RngStreams(master_seed=seed)
    allocator = IdAllocator()
    monsters = load_monsters()
    candidates = []
    registry = {}
    for index in range(group):
        template = monsters.get(("skeleton", "zombie", "wight", "mummy", "spectre")[index % 5])
        instance = spawn_monster(template, id=allocator.allocate("monster"), stream=streams.get("monster_spawn"))
        candidates.append(instance)
        registry[instance.id] = instance
    cleric = make_caster("cleric", 11)
    registry[cleric.id] = cleric
    result = turn_undead(
        cleric,
        load_classes().get("cleric"),
        candidates,
        ledger=EffectsLedger(),
        clock=GameClock(),
        allocator=allocator,
        registry=registry,
        stream=streams.get(MAGIC_STREAM),
    )
    if result.hd_pool is None:
        assert not result.affected_ids
        return
    costs = {instance.id: max(1, instance.template.hit_dice.count) for instance in candidates}
    spent = sum(costs[monster_id] for monster_id in result.affected_ids)
    if spent > result.hd_pool:
        # Only the RAW minimum effect may overspend: exactly one monster, the cheapest.
        assert len(result.affected_ids) == 1
        eligible_costs = [costs[instance.id] for instance in candidates]
        assert costs[result.affected_ids[0]] == min(eligible_costs)
