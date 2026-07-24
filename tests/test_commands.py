"""Tests for the command models: discriminator, round-trips, modes, and the envelope."""

import json

import pytest

from osrlib.core.validation import Rejection
from osrlib.crawl.commands import (
    ALL_COMMAND_CLASSES,
    BattleDeclaration,
    CommandResult,
    MoveParty,
    ResolveBattleRound,
    SessionMode,
    SpawnMonsters,
    parse_command,
)
from osrlib.crawl.dungeon import Direction
from osrlib.errors import ContentValidationError

REFEREE_COMMANDS = {
    "grant_item",
    "grant_coins",
    "award_xp",
    "set_flag",
    "spawn_monsters",
    "set_door_state",
    "place_party",
    "advance_time",
    "roll_dice",
}


def sample_command(command_class):
    samples = {
        "MoveParty": dict(direction="north"),
        "TurnParty": dict(facing="east"),
        "ReorderParty": dict(order=("pc-2", "pc-1")),
        "OpenDoor": dict(direction="north"),
        "CloseDoor": dict(direction="north"),
        "ForceDoor": dict(direction="north", character_id="pc-1"),
        "WedgeDoor": dict(direction="north"),
        "ListenAtDoor": dict(direction="north", character_id="pc-1"),
        "PickLock": dict(direction="north", character_id="pc-1"),
        "Search": dict(character_id="pc-1", kind="secret_doors"),
        "InspectTreasure": dict(character_id="pc-1", feature_id="chest-1"),
        "RemoveTreasureTrap": dict(character_id="pc-1", feature_id="chest-1"),
        "TakeTreasure": dict(feature_id="chest-1"),
        "DropItems": dict(character_id="pc-1", item_ids=("torch",)),
        "GiveItems": dict(character_id="pc-1", recipient_id="pc-2", item_ids=("torch",)),
        "LightSource": dict(character_id="pc-1", item_id="torch"),
        "ExtinguishSource": dict(character_id="pc-1"),
        "EquipItem": dict(character_id="pc-1", item_id="sword"),
        "UnequipItem": dict(character_id="pc-1", item_id="sword"),
        "Rest": dict(kind="turn"),
        "PrepareSpells": dict(character_id="pc-1"),
        "CastSpell": dict(character_id="pc-1", spell_id="light", mode="illuminate"),
        "UseStairs": dict(),
        "EnterDungeon": dict(dungeon_id="delve"),
        "TravelToTown": dict(),
        "PurchaseEquipment": dict(character_id="pc-1", item_ids=("torch",)),
        "Parley": dict(character_id="pc-1"),
        "Evade": dict(drop="treasure"),
        "EngageBattle": dict(),
        "Wait": dict(),
        "TurnUndead": dict(character_id="pc-1"),
        "ResolveBattleRound": dict(
            declarations=(BattleDeclaration(character_id="pc-1", action="attack", target_group_id="group-1"),)
        ),
        "GrantItem": dict(character_id="pc-1", item_id="torch", quantity=6),
        "GrantCoins": dict(character_id="pc-1", coins={"gp": 100}),
        "AwardXP": dict(character_id="pc-1", amount=100),
        "SetFlag": dict(key="portcullis_open", value=True),
        "SpawnMonsters": dict(template_id="goblin", count_dice="2d4", distance_feet=60),
        "SetDoorState": dict(dungeon_id="delve", level_number=1, x=2, y=0, direction="south", open=True),
        "PlaceParty": dict(location={"kind": "town"}),
        "AdvanceTime": dict(n=2, unit="turn"),
        "UseItem": dict(character_id="character-0001", item_id="magic-item-0001"),
        "IdentifyItem": dict(character_id="character-0001", item_id="magic-item-0001"),
        "SpawnNpcParty": dict(party_kind="basic", distance_feet=60),
        "SellTreasure": dict(item_ids=("valuable-0001",)),
        "PurchaseHealing": dict(character_id="character-0001", service="cure_light_wounds"),
        "RollDice": dict(expression="2d6"),
    }
    return command_class(**samples[command_class.__name__])


class TestDiscriminator:
    def test_every_command_declares_a_unique_command_type(self):
        types = [command_class.model_fields["command_type"].default for command_class in ALL_COMMAND_CLASSES]
        assert len(set(types)) == len(types)
        assert all(isinstance(value, str) and value for value in types)

    @pytest.mark.parametrize("command_class", ALL_COMMAND_CLASSES, ids=lambda cls: cls.__name__)
    def test_round_trip_through_json(self, command_class):
        command = sample_command(command_class)
        payload = json.loads(json.dumps(command.model_dump(mode="json")))
        parsed = parse_command(payload)
        assert parsed == command
        assert type(parsed) is command_class

    def test_unknown_command_type_is_skippable(self):
        assert parse_command({"command_type": "cast_wish"}) is None

    def test_known_type_with_malformed_payload_raises(self):
        with pytest.raises(ContentValidationError):
            parse_command({"command_type": "move_party", "direction": "up"})

    def test_unknown_fields_tolerated(self):
        payload = MoveParty(direction=Direction.NORTH).model_dump(mode="json")
        payload["field_from_the_future"] = 7
        assert parse_command(payload) == MoveParty(direction=Direction.NORTH)


class TestModes:
    def test_every_command_declares_modes(self):
        for command_class in ALL_COMMAND_CLASSES:
            assert command_class.allowed_modes, command_class.__name__

    def test_referee_commands_are_legal_everywhere(self):
        for command_class in ALL_COMMAND_CLASSES:
            command_type = command_class.model_fields["command_type"].default
            if command_type in REFEREE_COMMANDS:
                assert command_class.allowed_modes == frozenset(SessionMode), command_class.__name__

    def test_battle_round_is_battle_only(self):
        assert ResolveBattleRound.allowed_modes == frozenset({SessionMode.BATTLE})

    def test_turn_undead_is_encounter_only(self):
        # The one aggressive act with a pre-battle procedure of its own (pinned):
        # in battle it is a declaration kind, and exploration has no candidates.
        turn_undead = next(cls for cls in ALL_COMMAND_CLASSES if cls.__name__ == "TurnUndead")
        assert turn_undead.allowed_modes == frozenset({SessionMode.ENCOUNTER})


class TestSpawnMonstersValidation:
    def test_exactly_one_count_form(self):
        with pytest.raises(ValueError):
            SpawnMonsters(template_id="goblin", distance_feet=60)
        with pytest.raises(ValueError):
            SpawnMonsters(template_id="goblin", count_dice="2d4", count_fixed=3, distance_feet=60)

    def test_count_dice_must_parse(self):
        with pytest.raises(ContentValidationError):
            SpawnMonsters(template_id="goblin", count_dice="lots", distance_feet=60)


class TestCommandResult:
    def test_rejected_results_carry_rejections_and_no_events(self):
        result = CommandResult(accepted=False, rejections=(Rejection(code="session.command.wrong_mode"),))
        assert not result.accepted
        assert result.events == ()

    def test_frozen(self):
        result = CommandResult(accepted=True)
        with pytest.raises(ValueError):
            result.accepted = False
