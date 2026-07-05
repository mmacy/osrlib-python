"""Tests for the event registry: discriminators, round-trips, codes, and messages.

Covers the kernel classes and, through `ALL_EVENT_CLASSES`, the crawl classes —
the exhaustive template and round-trip tests extend automatically as the registry
grows.
"""

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
from osrlib.crawl.events import ALL_EVENT_CLASSES, parse_any_event
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
        "PartyMovedEvent": dict(x=3, y=4, facing="north"),
        "LocationEnteredEvent": dict(location_kind="area", location_id="room-3", level_number=1),
        "DoorEvent": dict(x=3, y=4, direction="north", character_id="pc-1"),
        "ListenedEvent": dict(character_id="pc-1", direction="north"),
        "DetectionRolledEvent": dict(character_id="pc-1", kind="secret_doors", chance=2, roll=3, passed=False),
        "SearchCompletedEvent": dict(character_id="pc-1", kind="secret_doors", found=("secret door north",)),
        "TrapEvent": dict(trap_ref="dungeon:1:room-3", character_id="pc-1"),
        "ItemAcquiredEvent": dict(character_id="pc-1", item_ids=("torch",), coins_gp_value=100),
        "ItemsDroppedEvent": dict(character_id="pc-1", item_ids=("rations_standard",), coins_gp_value=200),
        "LightEvent": dict(character_id="pc-1", source="torch"),
        "RestedEvent": dict(kind="night"),
        "FatigueEvent": dict(),
        "ProvisionsEvent": dict(character_id="pc-1", kind="food"),
        "WanderingCheckEvent": dict(chance=2, roll=5, encounter=False),
        "EncounterStartedEvent": dict(monster_name="Goblin", count=4, distance_feet=60),
        "SurpriseRolledEvent": dict(side="party", threshold=2, roll=1, surprised=True),
        "StanceChangedEvent": dict(stance="hostile"),
        "EvasionEvent": dict(),
        "PursuitEvent": dict(round=3, gap_feet=40),
        "ExhaustionEvent": dict(),
        "EncounterEndedEvent": dict(outcome="evaded"),
        "BattleStartedEvent": dict(),
        "BattleRoundEvent": dict(round=2),
        "SpellDeclaredEvent": dict(caster_id="pc-1", spell_id="sleep"),
        "GroupMovedEvent": dict(group_id="group-1", distance_feet=30),
        "MonsterFledEvent": dict(group_id="group-1"),
        "MonsterDefeatedEvent": dict(monster_id="monster-0001", template_id="goblin", outcome="slain", xp=5),
        "BattleEndedEvent": dict(),
        "FlagSetEvent": dict(key="portcullis_open", value=True),
        "MonstersSpawnedEvent": dict(template_id="goblin", monster_ids=("monster-0001",)),
        "XpAwardedEvent": dict(character_id="pc-1", award=100, modified_award=110, level_after=2),
        "TimeAdvancedEvent": dict(n=2, unit="turn", rounds_total=120),
        "GameOverEvent": dict(reason="tpk"),
        "DiceRolledEvent": dict(expression="2d6", total=7, rolls=(3, 4)),
        "HoardGeneratedEvent": dict(
            cache_ref="cache-0001",
            treasure_types=("A",),
            coins_gp_value=120,
            valuable_ids=("valuable-0001",),
            magic_item_ids=("magic-item-0001",),
        ),
        "ItemUsedEvent": dict(character_id="character-0001", instance_id="magic-item-0001"),
        "ItemIdentifiedEvent": dict(instance_id="magic-item-0001", template_id="potion_of_healing"),
        "CurseRevealedEvent": dict(
            character_id="character-0001", instance_id="magic-item-0001", template_id="cursed_armour_minus_1"
        ),
        "NpcPartySpawnedEvent": dict(
            party_kind="basic", npc_ids=("npc-0001",), class_ids=("fighter",), levels=(2,), alignment="lawful"
        ),
        "AdventureXpAwardEvent": dict(monster_xp=100, treasure_xp=250, share=87, survivors=("character-0001",)),
        "TreasureSoldEvent": dict(character_id="character-0001", instance_ids=("valuable-0001",), gp_value=500),
        "HealingPurchasedEvent": dict(character_id="character-0001", service="cure_light_wounds", cost_gp=25),
    }
    return event_class(code=code, **samples[event_class.__name__])


def all_shipped_events():
    return [(event_class, code) for event_class in ALL_EVENT_CLASSES for code in sorted(event_class.allowed_codes)]


def kernel_shipped_events():
    return [(event_class, code) for event_class in KERNEL_EVENT_CLASSES for code in sorted(event_class.allowed_codes)]


class TestDiscriminator:
    def test_every_event_declares_a_unique_event_type(self):
        types = [event_class.model_fields["event_type"].default for event_class in ALL_EVENT_CLASSES]
        assert len(set(types)) == len(types)
        assert all(isinstance(value, str) and value for value in types)

    def test_every_event_declares_a_closed_code_set(self):
        for event_class in ALL_EVENT_CLASSES:
            assert event_class.allowed_codes, event_class.__name__

    @pytest.mark.parametrize(("event_class", "code"), kernel_shipped_events())
    def test_round_trip_through_json(self, event_class, code):
        event = sample_event(event_class, code)
        payload = json.loads(json.dumps(event.model_dump(mode="json")))
        parsed = parse_event(payload)
        assert parsed == event
        assert type(parsed) is event_class

    @pytest.mark.parametrize(("event_class", "code"), all_shipped_events())
    def test_round_trip_through_the_combined_parser(self, event_class, code):
        event = sample_event(event_class, code)
        payload = json.loads(json.dumps(event.model_dump(mode="json")))
        parsed = parse_any_event(payload)
        assert parsed == event
        assert type(parsed) is event_class

    def test_combined_parser_skips_unknown_types(self):
        assert parse_any_event({"event_type": "quantum_flux", "code": "a.b", "visibility": "player"}) is None

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
        shipped = {code for event_class in ALL_EVENT_CLASSES for code in event_class.allowed_codes}
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
