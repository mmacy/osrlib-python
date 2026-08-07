"""The trigger document surface: patterns and the spec's parse rules.

`TestPatternModels` and `TestTriggerSpec` pin what an authored document may say —
the discriminated pattern union, the consequence sub-union, and the two things a
trigger may never carry (a consuming condition, a hand-written `source`).
`TestFlagValuesEqual` pins the shared strict comparison.
"""

import pytest
from pydantic import TypeAdapter, ValidationError

from osrlib.crawl.commands import (
    CONSEQUENCE_COMMAND_CLASSES,
    AddJournalEntry,
    AdvanceTime,
    ConsequenceCommand,
    MoveParty,
    SetDoorState,
    SetFlag,
)
from osrlib.crawl.dungeon import Direction
from osrlib.crawl.gates import FlagEqualsCondition, HasItemCondition, flag_values_equal
from osrlib.crawl.narrative import NarrativeBlock
from osrlib.crawl.triggers import (
    FIRST_LIVING_SELECTOR,
    PARTY_SELECTOR,
    AreaEnteredPattern,
    DungeonEnteredPattern,
    FlagSetPattern,
    ItemAcquiredPattern,
    LevelEnteredPattern,
    MonsterDefeatedPattern,
    TownEnteredPattern,
    TriggerPattern,
    TriggerSpec,
)

PATTERNS = (
    AreaEnteredPattern(dungeon_id="delve", level_number=1, area_id="room_a"),
    LevelEnteredPattern(dungeon_id="delve", level_number=2),
    DungeonEnteredPattern(dungeon_id="delve"),
    TownEnteredPattern(),
    ItemAcquiredPattern(item_id="holy_water"),
    MonsterDefeatedPattern(template_id="goblin"),
    FlagSetPattern(key="crypt.lever"),
)


class TestPatternModels:
    @pytest.mark.parametrize("pattern", PATTERNS, ids=lambda pattern: pattern.pattern_type)
    def test_every_pattern_round_trips_through_its_discriminator(self, pattern):
        adapter = TypeAdapter(TriggerPattern)
        assert adapter.validate_python(pattern.model_dump(mode="json")) == pattern

    def test_pattern_types_are_unique(self):
        types = [pattern.pattern_type for pattern in PATTERNS]
        assert len(set(types)) == len(types)

    def test_patterns_are_frozen(self):
        with pytest.raises(ValidationError):
            PATTERNS[0].dungeon_id = "elsewhere"

    def test_string_fields_reject_the_empty_string(self):
        with pytest.raises(ValidationError):
            DungeonEnteredPattern(dungeon_id="")
        with pytest.raises(ValidationError):
            ItemAcquiredPattern(item_id="")

    def test_a_flag_pattern_takes_any_written_value_or_one(self):
        assert FlagSetPattern(key="lever").value is None
        assert FlagSetPattern(key="lever", value=True).value is True
        assert FlagSetPattern(key="lever", value="open").value == "open"

    def test_an_unknown_pattern_type_does_not_parse(self):
        with pytest.raises(ValidationError):
            TypeAdapter(TriggerPattern).validate_python({"pattern_type": "moon_rose", "key": "x"})


class TestConsequenceSurface:
    def test_the_union_admits_every_consequence_class(self):
        adapter = TypeAdapter(ConsequenceCommand)
        samples = {
            "GrantItem": dict(character_id=PARTY_SELECTOR, item_id="holy_water"),
            "GrantCoins": dict(character_id=FIRST_LIVING_SELECTOR, coins={"gp": 10}),
            "AwardXP": dict(character_id=PARTY_SELECTOR, amount=100),
            "SetFlag": dict(key="lever", value=True),
            "SpawnMonsters": dict(template_id="goblin", count_fixed=2, distance_feet=30),
            "SpawnNpcParty": dict(party_kind="basic", distance_feet=30),
            "SetDoorState": dict(dungeon_id="delve", level_number=1, x=2, y=0, direction="south", open=True),
            "PlaceParty": dict(location={"kind": "town"}),
            "AdvanceTime": dict(n=1, unit="turn"),
        }
        for command_class in CONSEQUENCE_COMMAND_CLASSES:
            command = command_class(**samples[command_class.__name__])
            assert adapter.validate_python(command.model_dump(mode="json")) == command

    @pytest.mark.parametrize(
        "payload",
        [
            AddJournalEntry(text="The lever grinds.").model_dump(mode="json"),
            MoveParty(direction=Direction.EAST).model_dump(mode="json"),
            {"command_type": "cast_wish"},
        ],
        ids=["lifecycle", "player", "unknown"],
    )
    def test_a_consequence_outside_the_surface_does_not_parse(self, payload):
        with pytest.raises(ValidationError):
            TriggerSpec.model_validate({"id": "t", "when": {"pattern_type": "town_entered"}, "consequences": [payload]})


class TestTriggerSpec:
    def test_defaults_are_the_common_shape(self):
        trigger = TriggerSpec(id="homecoming", when=TownEnteredPattern())
        assert trigger.conditions == ()
        assert trigger.consequences == ()
        assert trigger.narrative is None
        assert not trigger.repeatable

    def test_a_full_spec_round_trips(self):
        trigger = TriggerSpec(
            id="portcullis",
            when=FlagSetPattern(key="crypt.lever", value="pulled"),
            conditions=(FlagEqualsCondition(key="crypt.power", value=True),),
            repeatable=True,
            consequences=(
                SetDoorState(dungeon_id="delve", level_number=1, x=2, y=0, direction=Direction.SOUTH, open=True),
                AdvanceTime(n=1, unit="turn"),
            ),
            narrative=NarrativeBlock(fired="Counterweights drop.", journal="A portcullis grinds upward."),
        )
        assert TriggerSpec.model_validate(trigger.model_dump(mode="json")) == trigger

    def test_an_empty_id_is_rejected(self):
        with pytest.raises(ValidationError):
            TriggerSpec(id="", when=TownEnteredPattern())

    def test_a_consuming_condition_is_rejected_at_parse(self):
        with pytest.raises(ValidationError, match="cannot consume"):
            TriggerSpec(
                id="toll",
                when=TownEnteredPattern(),
                conditions=(HasItemCondition(item_id="toll_token", consumes=True),),
            )

    def test_a_non_consuming_condition_is_fine(self):
        trigger = TriggerSpec(
            id="toll",
            when=TownEnteredPattern(),
            conditions=(HasItemCondition(item_id="toll_token"),),
        )
        assert trigger.conditions[0].consumes is False

    def test_an_authored_source_on_a_consequence_is_rejected_at_parse(self):
        with pytest.raises(ValidationError, match="carries a source"):
            TriggerSpec(
                id="portcullis",
                when=TownEnteredPattern(),
                consequences=(SetFlag(key="lever", value=True, source="trigger:someone-else"),),
            )

    def test_the_spec_is_frozen(self):
        trigger = TriggerSpec(id="homecoming", when=TownEnteredPattern())
        with pytest.raises(ValidationError):
            trigger.repeatable = True


class TestFlagValuesEqual:
    @pytest.mark.parametrize(
        ("stored", "expected", "equal"),
        [
            ("open", "open", True),
            ("open", "shut", False),
            (3, 3, True),
            (True, True, True),
            (True, 1, False),
            (1, True, False),
            (False, 0, False),
            (0, False, False),
        ],
    )
    def test_equality_plus_matching_boolness(self, stored, expected, equal):
        assert flag_values_equal(stored, expected) is equal

    def test_the_condition_and_the_helper_agree(self):
        from osrlib.core.effects import EffectsLedger
        from osrlib.crawl.gates import condition_holds

        condition = FlagEqualsCondition(key="lever", value=1)
        held = condition_holds(condition, members=[], flags={"lever": True}, ledger=EffectsLedger())
        assert held is flag_values_equal(True, 1) is False
