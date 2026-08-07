"""The trigger document surface: patterns, the spec's parse rules, and validation.

`TestPatternModels` and `TestTriggerSpec` pin what an authored document may say —
the discriminated pattern union, the consequence sub-union, and the two things a
trigger may never carry (a consuming condition, a hand-written `source`).
`TestFlagValuesEqual` pins the shared strict comparison. `TestTriggerValidation`
walks `validate_adventure`'s trigger checks, reference class by reference class.
"""

import pytest
from pydantic import TypeAdapter, ValidationError

from crawl_fixtures import build_adventure
from osrlib.crawl.adventure import Adventure
from osrlib.crawl.commands import (
    CONSEQUENCE_COMMAND_CLASSES,
    AddJournalEntry,
    AdvanceTime,
    ConsequenceCommand,
    GrantItem,
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


def with_triggers(*triggers: TriggerSpec) -> Adventure:
    """The shared test delve with authored triggers bolted on."""
    return build_adventure(wandering_chance=0).model_copy(update={"triggers": triggers})


class TestTriggerValidation:
    def validate(self, adventure: Adventure) -> str:
        from osrlib.crawl.adventure import validate_adventure
        from osrlib.data import load_equipment, load_monsters
        from osrlib.errors import ContentValidationError

        with pytest.raises(ContentValidationError) as raised:
            validate_adventure(adventure, load_monsters(), load_equipment())
        return str(raised.value)

    def test_a_clean_document_with_every_pattern_kind_validates(self):
        from osrlib.crawl.adventure import validate_adventure
        from osrlib.data import load_equipment, load_monsters

        triggers = (
            TriggerSpec(id="area", when=AreaEnteredPattern(dungeon_id="delve", level_number=1, area_id="room_a")),
            TriggerSpec(id="level", when=LevelEnteredPattern(dungeon_id="delve", level_number=2)),
            TriggerSpec(id="dungeon", when=DungeonEnteredPattern(dungeon_id="delve")),
            TriggerSpec(id="town", when=TownEnteredPattern()),
            TriggerSpec(id="item", when=ItemAcquiredPattern(item_id="holy_water")),
            TriggerSpec(id="magic", when=ItemAcquiredPattern(item_id="potion_of_healing")),
            TriggerSpec(id="monster", when=MonsterDefeatedPattern(template_id="goblin")),
            TriggerSpec(id="flag", when=FlagSetPattern(key="anything.at.all")),
        )
        validate_adventure(with_triggers(*triggers), load_monsters(), load_equipment())

    def test_duplicate_trigger_ids_are_caught(self):
        adventure = with_triggers(
            TriggerSpec(id="twice", when=TownEnteredPattern()),
            TriggerSpec(id="twice", when=TownEnteredPattern()),
        )
        assert "trigger 'twice': id is not unique" in self.validate(adventure)

    def test_a_dangling_area_reference_is_caught(self):
        adventure = with_triggers(
            TriggerSpec(id="ghost", when=AreaEnteredPattern(dungeon_id="delve", level_number=1, area_id="nowhere"))
        )
        message = self.validate(adventure)
        assert "trigger 'ghost': pattern references unknown area 'nowhere'" in message

    def test_a_dangling_level_reference_is_caught(self):
        adventure = with_triggers(TriggerSpec(id="deep", when=LevelEnteredPattern(dungeon_id="delve", level_number=9)))
        assert "trigger 'deep': pattern references unknown 'delve' level 9" in self.validate(adventure)

    def test_a_dangling_dungeon_reference_is_caught(self):
        adventure = with_triggers(TriggerSpec(id="elsewhere", when=DungeonEnteredPattern(dungeon_id="atlantis")))
        assert "trigger 'elsewhere': pattern references unknown dungeon 'atlantis'" in self.validate(adventure)

    def test_a_dangling_pattern_item_is_caught(self):
        adventure = with_triggers(TriggerSpec(id="loot", when=ItemAcquiredPattern(item_id="jade_idol")))
        assert "trigger 'loot': pattern references unknown item 'jade_idol'" in self.validate(adventure)

    def test_a_dangling_pattern_monster_is_caught(self):
        adventure = with_triggers(TriggerSpec(id="boss", when=MonsterDefeatedPattern(template_id="tarrasque")))
        assert "trigger 'boss': pattern references unknown monster 'tarrasque'" in self.validate(adventure)

    def test_a_dangling_condition_item_is_caught(self):
        adventure = with_triggers(
            TriggerSpec(
                id="keyed",
                when=TownEnteredPattern(),
                conditions=(HasItemCondition(item_id="jade_idol"),),
            )
        )
        assert "trigger 'keyed': condition references unknown item 'jade_idol'" in self.validate(adventure)

    def test_a_dangling_consequence_item_is_caught(self):
        adventure = with_triggers(
            TriggerSpec(
                id="reward",
                when=TownEnteredPattern(),
                consequences=(GrantItem(character_id=PARTY_SELECTOR, item_id="jade_idol"),),
            )
        )
        assert "trigger 'reward': consequence 0 references unknown item 'jade_idol'" in self.validate(adventure)

    def test_a_dangling_consequence_monster_is_caught(self):
        from osrlib.crawl.commands import SpawnMonsters

        adventure = with_triggers(
            TriggerSpec(
                id="ambush",
                when=TownEnteredPattern(),
                consequences=(SpawnMonsters(template_id="tarrasque", count_fixed=1, distance_feet=30),),
            )
        )
        assert "trigger 'ambush': consequence 0 references unknown monster 'tarrasque'" in self.validate(adventure)

    def test_a_consequence_door_must_exist_at_the_cell_and_direction(self):
        adventure = with_triggers(
            TriggerSpec(
                id="portcullis",
                when=TownEnteredPattern(),
                consequences=(
                    SetDoorState(dungeon_id="delve", level_number=1, x=0, y=0, direction=Direction.NORTH, open=True),
                ),
            )
        )
        assert "trigger 'portcullis': consequence 0 names no door at (0, 0) north" in self.validate(adventure)

    def test_a_consequence_door_on_an_unknown_level_is_caught(self):
        adventure = with_triggers(
            TriggerSpec(
                id="portcullis",
                when=TownEnteredPattern(),
                consequences=(
                    SetDoorState(dungeon_id="delve", level_number=9, x=0, y=0, direction=Direction.SOUTH, open=True),
                ),
            )
        )
        assert "trigger 'portcullis': consequence 0 references unknown 'delve' level 9" in self.validate(adventure)

    def test_a_consequence_placement_must_land_on_the_grid(self):
        from osrlib.crawl.commands import PlaceParty
        from osrlib.crawl.dungeon import PartyLocation

        adventure = with_triggers(
            TriggerSpec(
                id="teleport",
                when=TownEnteredPattern(),
                consequences=(
                    PlaceParty(
                        location=PartyLocation(
                            kind="dungeon",
                            dungeon_id="delve",
                            level_number=1,
                            position=(99, 99),
                            facing=Direction.EAST,
                        )
                    ),
                ),
            )
        )
        assert "trigger 'teleport': consequence 0 places the party out of bounds" in self.validate(adventure)

    def test_a_literal_character_id_in_a_consequence_is_an_error(self):
        adventure = with_triggers(
            TriggerSpec(
                id="reward",
                when=TownEnteredPattern(),
                consequences=(GrantItem(character_id="character-0001", item_id="holy_water"),),
            )
        )
        message = self.validate(adventure)
        assert "trigger 'reward': consequence 0 names character 'character-0001'" in message
        assert PARTY_SELECTOR in message and FIRST_LIVING_SELECTOR in message

    def test_the_selectors_are_accepted_on_every_character_addressing_consequence(self):
        from osrlib.crawl.adventure import validate_adventure
        from osrlib.crawl.commands import AwardXP, GrantCoins
        from osrlib.data import load_equipment, load_monsters

        adventure = with_triggers(
            TriggerSpec(
                id="reward",
                when=TownEnteredPattern(),
                consequences=(
                    GrantItem(character_id=PARTY_SELECTOR, item_id="holy_water"),
                    GrantCoins(character_id=FIRST_LIVING_SELECTOR, coins={"gp": 50}),
                    AwardXP(character_id=PARTY_SELECTOR, amount=100),
                ),
            )
        )
        validate_adventure(adventure, load_monsters(), load_equipment())

    def test_a_pre_phase_document_parses_with_no_triggers(self):
        payload = build_adventure(wandering_chance=0).model_dump(mode="json")
        payload.pop("triggers")
        assert Adventure.model_validate(payload).triggers == ()
