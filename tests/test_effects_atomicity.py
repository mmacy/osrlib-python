"""A kernel call that fails leaves everything as it was (issue #166, items 17 to 21 and 23).

Each call here used to change state and then raise: a spell was spent, a creature was killed, an
effect id was used up, or the clock moved and effects were dropped. The checks now come before the
first change. Most of the late failures in the effects ledger came from an `EffectDefinition` that
could never work, so the model refuses those when it's built.
"""

import pytest
from pydantic import ValidationError

from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.clock import GameClock, TimeUnit
from osrlib.core.combat import DamageSource, deal_damage
from osrlib.core.effects import EFFECTS_STREAM, Condition, EffectDefinition, EffectsLedger, has_condition
from osrlib.core.items import MagicItemInstance
from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.spells import MAGIC_STREAM, MemorizedSpell, cast_spell, caster_profile, memorize_spells, validate_cast
from osrlib.data import load_classes, load_monsters, load_spells

pytestmark = pytest.mark.xfail(reason="chunk: effects-atomicity")


def hero(class_id: str = "fighter", **choices):
    stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
    return create_character(
        name="Hild", class_id=class_id, alignment=Alignment.LAWFUL, ruleset=Ruleset(), stream=stream, **choices
    ).character


def goblin():
    stream = RngStreams(master_seed=3).get(MONSTER_SPAWN_STREAM)
    return spawn_monster(load_monsters().get("goblin"), id="monster-0001", stream=stream)


def sleep_for(amount: int, unit: TimeUnit = TimeUnit.ROUND) -> EffectDefinition:
    return EffectDefinition(kind="sleep", duration_unit=unit, duration_amount=amount, condition=Condition.ASLEEP)


# ---------------------------------------------------------------------- cast_spell (item 17)


def fire_ball_caster():
    catalog, definition = load_spells(), load_classes().get("magic_user")
    zelia = hero("magic_user", starting_spell_ids=["sleep"])
    zelia.level = 5
    zelia.spell_book = ("sleep", "fire_ball")
    memorize_spells(zelia, definition, catalog, [MemorizedSpell(spell_id="fire_ball")])
    return zelia, catalog.get("fire_ball"), caster_profile(definition)


def test_validate_cast_rejects_a_location_for_a_mode_that_needs_creatures():
    zelia, fire_ball, profile = fire_ball_caster()
    rejections = validate_cast(zelia, fire_ball, "damage", profile=profile, targets=["cell:3,4"])
    assert [rejection.code for rejection in rejections] == ["magic.cast.target_must_be_creature"]
    assert rejections[0].params["target"] == "cell:3,4"


def test_a_refused_cast_doesnt_spend_the_spell():
    zelia, fire_ball, profile = fire_ball_caster()
    streams = RngStreams(master_seed=5)
    magic, effects = streams.get(MAGIC_STREAM), streams.get(EFFECTS_STREAM)
    before = (magic.export_state(), effects.export_state())
    with pytest.raises(ValueError):
        cast_spell(
            zelia,
            fire_ball,
            "damage",
            profile=profile,
            targets=["cell:3,4"],
            ledger=EffectsLedger(),
            clock=GameClock(),
            allocator=IdAllocator(),
            registry={},
            ruleset=Ruleset(),
            stream=magic,
            effects_stream=effects,
        )
    assert [copy.spell_id for copy in zelia.memorized_spells] == ["fire_ball"]
    assert (magic.export_state(), effects.export_state()) == before


def test_a_fire_ball_at_creatures_still_casts():
    zelia, fire_ball, profile = fire_ball_caster()
    target = goblin()
    streams = RngStreams(master_seed=5)
    assert validate_cast(zelia, fire_ball, "damage", profile=profile, targets=[target]) == []
    cast_spell(
        zelia,
        fire_ball,
        "damage",
        profile=profile,
        targets=[target],
        ledger=EffectsLedger(),
        clock=GameClock(),
        allocator=IdAllocator(),
        registry={target.id: target},
        ruleset=Ruleset(),
        stream=streams.get(MAGIC_STREAM),
        effects_stream=streams.get(EFFECTS_STREAM),
    )
    assert zelia.memorized_spells == ()


# ---------------------------------------------------------------------- deal_damage (item 18)


def test_deal_damage_asks_for_the_stream_before_it_kills():
    hild = hero()
    hild.inventory.items.append(MagicItemInstance(instance_id="magic-item-0001", template_id="sword_plus_1"))
    hit_points = hild.current_hp
    breath = DamageSource(element="fire", kind="breath", destructive=True)
    with pytest.raises(ValueError, match="stream"):
        deal_damage(hild, 99, source=breath, ruleset=Ruleset())
    assert hild.current_hp == hit_points
    assert not has_condition(hild, Condition.DEAD)
    assert [item.instance_id for item in hild.inventory.items if isinstance(item, MagicItemInstance)] == [
        "magic-item-0001"
    ]


def test_deal_damage_needs_no_stream_when_nothing_can_be_destroyed():
    hild = hero()
    deal_damage(hild, 1, source=DamageSource(element="fire", kind="breath", destructive=True), ruleset=Ruleset())
    victim = goblin()
    deal_damage(victim, 99, source=DamageSource())
    assert has_condition(victim, Condition.DEAD)


# ---------------------------------------------------------------------- EffectsLedger.attach (item 19)


def test_a_refused_attach_uses_no_effect_id():
    ledger, allocator = EffectsLedger(), IdAllocator()
    web = EffectDefinition(kind="web", duration_unit=TimeUnit.ROUND, duration_dice="2d6")
    with pytest.raises(ValueError, match="stream"):
        ledger.attach(web, "monster-0001", clock=GameClock(), allocator=allocator)
    assert ledger.effects == []
    assert allocator.allocate("effect") == "effect-0001"


# ---------------------------------------------------------------------- EffectsLedger.advance (item 20)


def test_advance_checks_what_an_expiry_needs_before_the_clock_moves():
    """The ring of weakness's expiry attaches a follow-on effect, which needs an allocator."""
    victim = goblin()
    registry = {victim.id: victim}
    ledger, clock, allocator = EffectsLedger(), GameClock(), IdAllocator()
    onset = EffectDefinition(
        kind="weakness_onset",
        duration_unit=TimeUnit.ROUND,
        duration_amount=2,
        expiry="weakness_strength_set",
        params={"strength_set": 3},
    )
    ledger.attach(sleep_for(1), victim.id, clock=clock, allocator=allocator, registry=registry)
    ledger.attach(onset, victim.id, clock=clock, allocator=allocator, registry=registry)
    stream = RngStreams(master_seed=3).get(EFFECTS_STREAM)
    before = stream.export_state()
    with pytest.raises(ValueError, match="allocator"):
        ledger.advance(clock, 5, TimeUnit.ROUND, registry, stream=stream)
    assert clock.rounds == 0
    assert [effect.definition.kind for effect in ledger.effects] == ["sleep", "weakness_onset"]
    assert has_condition(victim, Condition.ASLEEP)
    assert stream.export_state() == before

    ledger.advance(clock, 5, TimeUnit.ROUND, registry, stream=stream, allocator=allocator)
    assert clock.rounds == 5
    assert not has_condition(victim, Condition.ASLEEP)


# ---------------------------------------------------------------------- EffectsLedger.release (item 21)


def test_release_without_a_registry_refuses_to_strand_a_condition():
    victim = goblin()
    registry = {victim.id: victim}
    ledger = EffectsLedger()
    effect, _ = ledger.attach(
        sleep_for(4, TimeUnit.TURN), victim.id, clock=GameClock(), allocator=IdAllocator(), registry=registry
    )
    with pytest.raises(ValueError, match="registry"):
        ledger.release(effect.effect_id)
    assert [active.effect_id for active in ledger.effects] == [effect.effect_id]
    assert has_condition(victim, Condition.ASLEEP)

    events = ledger.release(effect.effect_id, registry=registry)
    assert "effects.effect.released" in [event.code for event in events]
    assert ledger.effects == []
    assert not has_condition(victim, Condition.ASLEEP)


def test_release_without_a_registry_is_fine_when_the_effect_granted_nothing():
    ledger = EffectsLedger()
    light = EffectDefinition(kind="light", duration_unit=TimeUnit.TURN, duration_amount=6)
    effect, _ = ledger.attach(light, "character-0001", clock=GameClock(), allocator=IdAllocator())
    assert [event.code for event in ledger.release(effect.effect_id)] == ["effects.effect.released"]
    assert ledger.effects == []


# ---------------------------------------------------------------------- EffectDefinition (item 23)


@pytest.mark.parametrize(
    "fields",
    [
        {"duration_unit": TimeUnit.ROUND, "duration_amount": 100, "duration_dice": "1d4"},
        {"duration_amount": 3},
        {"duration_dice": "1d4"},
        {"duration_unit": TimeUnit.TURN},
        {"tick": "regenerate"},
        {"expiry": "explode"},
        {"permanent": True, "duration_unit": TimeUnit.TURN, "duration_amount": 3},
    ],
    ids=[
        "an amount and dice together",
        "an amount with no unit",
        "dice with no unit",
        "a unit with no amount or dice",
        "a tick osrlib doesn't define",
        "an expiry osrlib doesn't define",
        "permanent with a duration",
    ],
)
def test_a_definition_that_could_never_work_is_refused(fields):
    with pytest.raises(ValidationError):
        EffectDefinition(kind="broken", **fields)


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"permanent": True},
        {"duration_unit": TimeUnit.TURN, "duration_amount": 6},
        {"duration_unit": TimeUnit.ROUND, "duration_dice": "2d6"},
        {"tick": "regeneration", "tick_interval_rounds": 1},
        {"duration_unit": TimeUnit.ROUND, "duration_amount": 2, "expiry": "weakness_strength_set"},
    ],
    ids=["no duration", "permanent", "an amount and a unit", "dice and a unit", "a tick osrlib defines", "an expiry"],
)
def test_a_definition_that_works_is_accepted(fields):
    assert EffectDefinition(kind="fine", **fields).kind == "fine"


def test_every_definition_in_the_data_still_builds():
    """The spell and magic item catalogs state effect definitions, and loading them validates each one."""
    from osrlib.data import load_magic_items

    assert load_spells().spells
    assert load_magic_items().items
