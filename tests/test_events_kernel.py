"""Tests for the kernel events: the discriminator, round-trips, codes, and messages."""

import json

import pytest

from osrlib.core.events import (
    KERNEL_EVENT_CLASSES,
    AttackRolledEvent,
    DamageDealtEvent,
    HitPointsReportedEvent,
    InitiativeRoll,
    InitiativeRolledEvent,
    LevelDrainedEvent,
    MoraleCheckedEvent,
    PreparedSpell,
    TurningTypeOutcome,
    Visibility,
    parse_event,
)
from osrlib.errors import ContentValidationError
from osrlib.messages import _TEMPLATES, format_message


def sample_event(event_class, code):
    """Build a minimal valid instance of an event class with the given code."""
    samples = {
        "InitiativeRolledEvent": dict(
            mode="side",
            entries=(InitiativeRoll(key="party", rolls=(4,), total=4),),
            order=("pc-1",),
        ),
        "AttackRolledEvent": dict(
            attacker_id="pc-1",
            defender_id="monster-0001",
            attack_name="sword",
            roll=14,
            modifier=1,
            total=15,
            required=13,
            defender_ac=4,
        ),
        "DamageDealtEvent": dict(target_id="monster-0001", attacker_id="pc-1", amount=5, rolls=(5,)),
        "DamageAbsorbedEvent": dict(target_id="monster-0001", attacker_id="pc-1", keys=("silver",)),
        "SavingThrowRolledEvent": dict(target_id="pc-1", category="death", roll=13, required=12),
        "MoraleCheckedEvent": dict(subject="trolls", score=10, roll=8),
        "ReactionRolledEvent": dict(roll=7, modifier=1, total=8, result="uncertain"),
        "ConditionGainedEvent": dict(target_id="pc-1", condition="paralysed", effect_id="effect-0001"),
        "ConditionRemovedEvent": dict(target_id="pc-1", condition="paralysed", effect_id="effect-0001"),
        "EffectAttachedEvent": dict(effect_id="effect-0001", kind="paralysis", target_ref="pc-1", expires_round=8),
        "EffectTickedEvent": dict(effect_id="effect-0001", kind="regeneration", target_ref="monster-0001", round=3),
        "EffectExpiredEvent": dict(effect_id="effect-0001", kind="paralysis", target_ref="pc-1", round=8),
        "EffectReleasedEvent": dict(effect_id="effect-0001", kind="petrification", target_ref="pc-1"),
        "HealingAppliedEvent": dict(target_id="pc-1", amount=3, source="natural"),
        "DeathEvent": dict(target_id="monster-0001"),
        "EquipmentDestroyedEvent": dict(target_id="pc-1", item_names=("Sword", "Rope")),
        "LevelDrainedEvent": dict(target_id="pc-1", levels_lost=1, new_level=3, hp_lost=6, xp_after=6000),
        "MonsterRevivedEvent": dict(target_id="monster-0001"),
        "HitPointsReportedEvent": dict(target_id="monster-0001", current_hp=12, max_hp=30),
        "TargetsSelectedEvent": dict(mode="hd_budget", target_ids=("monster-0001",)),
        "SpellsMemorizedEvent": dict(
            caster_id="pc-1",
            prepared=(PreparedSpell(spell_id="cure_light_wounds"), PreparedSpell(spell_id="bless", reversed=True)),
        ),
        "SpellCastEvent": dict(caster_id="pc-1", spell_id="fire_ball", mode="damage", target_ids=("monster-0001",)),
        "SpellDisruptedEvent": dict(caster_id="pc-1", spell_id="fire_ball"),
        "SpellForgottenEvent": dict(caster_id="pc-1", spell_id="sleep"),
        "SpellBookUpdatedEvent": dict(caster_id="pc-1", spell_id="web"),
        "UndeadTurnedEvent": dict(
            caster_id="pc-1",
            roll=9,
            hd_pool=7,
            types=(TurningTypeOutcome(template_id="skeleton", column="1", outcome="turn"),),
            affected_ids=("monster-0001",),
        ),
        "MagicDispelledEvent": dict(
            caster_id="pc-1", released_effect_ids=("effect-0001",), surviving_effect_ids=("effect-0002",)
        ),
    }
    return event_class(code=code, **samples[event_class.__name__])


def all_shipped_events():
    return [(event_class, code) for event_class in KERNEL_EVENT_CLASSES for code in sorted(event_class.allowed_codes)]


class TestDiscriminator:
    def test_every_kernel_event_declares_a_unique_event_type(self):
        types = [event_class.model_fields["event_type"].default for event_class in KERNEL_EVENT_CLASSES]
        assert len(set(types)) == len(types)
        assert all(isinstance(value, str) and value for value in types)

    def test_every_kernel_event_declares_a_closed_code_set(self):
        for event_class in KERNEL_EVENT_CLASSES:
            assert event_class.allowed_codes, event_class.__name__

    @pytest.mark.parametrize(("event_class", "code"), all_shipped_events())
    def test_round_trip_through_json(self, event_class, code):
        event = sample_event(event_class, code)
        payload = json.loads(json.dumps(event.model_dump(mode="json")))
        parsed = parse_event(payload)
        assert parsed == event
        assert type(parsed) is event_class

    def test_unknown_fields_tolerated(self):
        event = sample_event(DamageDealtEvent, "combat.damage.dealt")
        payload = event.model_dump(mode="json")
        payload["field_from_the_future"] = {"nested": True}
        assert parse_event(payload) == event

    def test_unknown_event_type_is_skippable(self):
        assert parse_event({"event_type": "quantum_flux", "code": "a.b", "visibility": "player"}) is None

    def test_known_type_with_malformed_payload_raises(self):
        with pytest.raises(ContentValidationError):
            parse_event({"event_type": "damage_dealt", "code": "combat.damage.dealt", "visibility": "player"})

    def test_code_outside_the_declared_set_rejected(self):
        with pytest.raises(ValueError):
            sample_event(AttackRolledEvent, "combat.attack.fumbled")


class TestVisibility:
    def test_morale_is_referee_only(self):
        event = sample_event(MoraleCheckedEvent, "combat.morale.held")
        assert event.visibility is Visibility.REFEREE

    def test_hit_point_state_is_referee_only(self):
        event = sample_event(HitPointsReportedEvent, "combat.state.hit_points")
        assert event.visibility is Visibility.REFEREE

    def test_no_player_event_carries_remaining_hit_points(self):
        # Monster HP is hidden by design: the damage event carries the amount only.
        assert "current_hp" not in DamageDealtEvent.model_fields
        assert DamageDealtEvent.model_fields["visibility"].default is Visibility.PLAYER

    def test_effect_bookkeeping_is_referee_visibility(self):
        for name in ("EffectAttachedEvent", "EffectTickedEvent", "EffectExpiredEvent", "EffectReleasedEvent"):
            event_class = next(cls for cls in KERNEL_EVENT_CLASSES if cls.__name__ == name)
            assert event_class.model_fields["visibility"].default is Visibility.REFEREE


class TestMessages:
    def test_every_shipped_code_has_a_real_template(self):
        shipped = {code for event_class in KERNEL_EVENT_CLASSES for code in event_class.allowed_codes}
        assert shipped == set(_TEMPLATES)

    @pytest.mark.parametrize(("event_class", "code"), all_shipped_events())
    def test_formatting_never_returns_the_bare_code(self, event_class, code):
        message = format_message(sample_event(event_class, code))
        assert message and message != code

    def test_formatter_is_total_on_unknown_codes(self):
        drained = LevelDrainedEvent(
            code="combat.drain.drained", target_id="pc-1", levels_lost=1, new_level=2, hp_lost=4
        )
        unknown = drained.model_copy(update={"code": "combat.drain.drained"})
        assert format_message(unknown)
        # Simulate a future code by formatting an event the map doesn't know.
        from osrlib.core.events import Event

        class FutureEvent(Event):
            pass

        event = FutureEvent(code="combat.future.thing", visibility=Visibility.PLAYER)
        assert format_message(event) == "combat.future.thing"

    def test_formatting_goldens(self):
        attack = sample_event(AttackRolledEvent, "combat.attack.hit")
        assert format_message(attack) == "pc-1 hits monster-0001 with sword: rolled 14+1 = 15, needing 13."
        damage = sample_event(DamageDealtEvent, "combat.damage.dealt")
        assert format_message(damage) == "monster-0001 takes 5 damage from pc-1."
        morale = sample_event(MoraleCheckedEvent, "combat.morale.broke")
        assert format_message(morale) == "Morale check for trolls (ML 10): rolled 8+0 — they flee or surrender."
        initiative = sample_event(InitiativeRolledEvent, "combat.initiative.rolled")
        assert format_message(initiative) == "Initiative (side): party 4. Order: pc-1."
