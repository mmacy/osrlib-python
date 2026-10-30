"""Referee item commands: magic grants, item and coin removal, and their selectors (issue #173).

`GrantItem` creates a magic item when its id is in the magic item catalog.
`RemoveItem` and `RemoveCoins` are the inverses of `GrantItem` and `GrantCoins`, and both
emit one `ItemsRemovedEvent`. Authored copies of the removal commands address the party
through the selectors, and `RemoveItem` also takes `"@carrier"`, which the interpreter
resolves to the first living member in marching order who carries the item.

Every refusal is checked against the validation contract: a refused command draws
nothing, spends no time, logs nothing, and leaves the session's state as it was.
"""

import json

import pytest

from crawl_fixtures import build_adventure, build_party
from osrlib.core.character import party_to_document
from osrlib.core.effects import kill
from osrlib.core.events import Visibility
from osrlib.core.items import Coins, GearTemplate, ItemInstance, MagicItemInstance, ValuableInstance
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure
from osrlib.crawl.commands import (
    ALL_COMMAND_CLASSES,
    CONSEQUENCE_COMMAND_CLASSES,
    AwardXP,
    EnterDungeon,
    EquipItem,
    GrantCoins,
    GrantItem,
    SessionMode,
    SetFlag,
    UnequipItem,
    UseItem,
    parse_command,
)
from osrlib.crawl.events import ItemAcquiredEvent, NoteRecordedEvent, parse_any_event
from osrlib.crawl.interpreter import Interpreter
from osrlib.crawl.quests import ObjectiveSpec, QuestSpec, TriggerClause
from osrlib.crawl.session import GameSession
from osrlib.crawl.triggers import (
    FIRST_LIVING_SELECTOR,
    PARTY_SELECTOR,
    DungeonEnteredPattern,
    FlagSetPattern,
    TownEnteredPattern,
    TriggerSpec,
)
from osrlib.messages import format_message
from osrlib.persistence import load_game, replay_game, save_game, session_state

CARRIER_SELECTOR = "@carrier"
"""The `RemoveItem` selector for the first living member in marching order who carries the item."""

SEED = 31
LEAD = "character-0001"
SECOND = "character-0002"
THIRD = "character-0003"
FOURTH = "character-0004"

QUEST_ITEM = GearTemplate(id="jade_idol", name="Jade idol", cost_gp=0)
"""An item only the adventure knows, carried until the quest that wants it completes."""


def adventure(**update) -> Adventure:
    """The shared quiet delve, with triggers, quests, or bundled items bolted on."""
    return build_adventure(wandering_chance=0).model_copy(update=update)


def new_session(adventure_document: Adventure | None = None) -> GameSession:
    """A session in town on the shared delve, with no listener registered."""
    document = adventure_document if adventure_document is not None else adventure()
    return GameSession.new(build_party(), document, seed=SEED)


def played(adventure_document: Adventure) -> GameSession:
    """A session with the trigger and quest interpreter registered."""
    session = new_session(adventure_document)
    session.register_listener(Interpreter(session))
    return session


def stream_states(session: GameSession) -> dict:
    return {key: state.model_dump(mode="json") for key, state in session.streams.export_states().items()}


def frozen_state(session: GameSession) -> str:
    return json.dumps(session_state(session), sort_keys=True)


def assert_refused(session: GameSession, command, code: str) -> None:
    """The command is refused with `code` alone, at no cost: no draws, no time, no change."""
    state = frozen_state(session)
    streams = stream_states(session)
    rounds = session.clock.rounds
    logged = len(session.command_log)
    result = session.execute(command)
    assert not result.accepted
    assert [rejection.code for rejection in result.rejections] == [code]
    assert list(result.events) == []
    assert stream_states(session) == streams, "a refusal draws nothing"
    assert session.clock.rounds == rounds, "a refusal spends no time"
    assert len(session.command_log) == logged, "a refusal is not logged"
    assert frozen_state(session) == state, "a refusal changes nothing"


def units(session: GameSession, character_id: str, item_id: str) -> int:
    """How many units of a mundane catalog id the member has, carried or equipped."""
    return sum(
        instance.quantity
        for instance in session.member(character_id).inventory.all_instances()
        if isinstance(instance, ItemInstance) and instance.template.id == item_id
    )


def purse(session: GameSession, character_id: str) -> dict:
    return session.member(character_id).inventory.purse.model_dump(mode="json")


def notes(session: GameSession) -> list[str]:
    return [event.text for event in session.event_log if isinstance(event, NoteRecordedEvent)]


def logged(session: GameSession, command_type: str) -> list:
    return [command for command in session.command_log if command.command_type == command_type]


def removed_events(session: GameSession) -> list:
    from osrlib.crawl.events import ItemsRemovedEvent

    return [event for event in session.event_log if isinstance(event, ItemsRemovedEvent)]


def assert_replays(session: GameSession, adventure_document: Adventure) -> None:
    """A save round trip and a listener-free replay of the log both rebuild the session."""
    restored = load_game(json.loads(json.dumps(save_game(session))))
    assert session_state(restored) == session_state(session)
    replayed = replay_game(
        SEED,
        party_to_document(build_party().members),
        adventure_document,
        Ruleset(),
        [command.model_dump(mode="json") for command in session.command_log],
    )
    live, again = session_state(session), session_state(replayed)
    live.pop("listener_state")
    again.pop("listener_state")
    assert live == again


# ---------------------------------------------------------------------- registry


class TestTheCommandSurface:
    def test_the_removal_commands_are_registered_after_end_encounter(self):
        from osrlib.crawl.commands import EndEncounter, RemoveCoins, RemoveItem

        after = ALL_COMMAND_CLASSES.index(EndEncounter) + 1
        assert set(ALL_COMMAND_CLASSES[after : after + 2]) == {RemoveItem, RemoveCoins}
        assert RemoveItem.model_fields["command_type"].default == "remove_item"
        assert RemoveCoins.model_fields["command_type"].default == "remove_coins"

    def test_the_removal_commands_are_authorable_consequences(self):
        from osrlib.crawl.commands import RemoveCoins, RemoveItem

        assert RemoveItem in CONSEQUENCE_COMMAND_CLASSES
        assert RemoveCoins in CONSEQUENCE_COMMAND_CLASSES

    def test_the_removal_commands_are_legal_in_every_mode(self):
        from osrlib.crawl.commands import RemoveCoins, RemoveItem

        assert RemoveItem.allowed_modes == frozenset(SessionMode)
        assert RemoveCoins.allowed_modes == frozenset(SessionMode)

    def test_remove_item_fields_and_defaults(self):
        from pydantic import ValidationError

        from osrlib.crawl.commands import RemoveItem

        command = RemoveItem(character_id=LEAD, item_id="torch")
        assert command.quantity == 1
        with pytest.raises(ValidationError):
            RemoveItem(character_id=LEAD, item_id="torch", quantity=0)

    def test_the_removal_commands_round_trip_through_json(self):
        from osrlib.crawl.commands import RemoveCoins, RemoveItem

        for command in (
            RemoveItem(character_id=LEAD, item_id="torch", quantity=3),
            RemoveItem(character_id=CARRIER_SELECTOR, item_id="magic-item-0001", source="quest:errand"),
            RemoveCoins(character_id=LEAD, coins=Coins(gp=5, sp=3)),
        ):
            payload = json.loads(json.dumps(command.model_dump(mode="json")))
            assert payload["command_type"] == command.command_type
            parsed = parse_command(payload)
            assert parsed == command
            assert type(parsed) is type(command)

    def test_the_removed_event_round_trips_and_renders(self):
        from osrlib.crawl.events import ALL_EVENT_CLASSES, ItemsRemovedEvent

        assert ItemsRemovedEvent in ALL_EVENT_CLASSES
        event = ItemsRemovedEvent(character_id="pc-1", item_ids=("torch", "magic-item-0003"), coins_gp_value=7)
        assert event.code == "session.items.removed"
        assert event.visibility is Visibility.PLAYER
        assert ItemsRemovedEvent(character_id="pc-1").item_ids == ()
        assert ItemsRemovedEvent(character_id="pc-1").coins_gp_value == 0
        payload = json.loads(json.dumps(event.model_dump(mode="json")))
        parsed = parse_any_event(payload)
        assert parsed == event
        assert type(parsed) is ItemsRemovedEvent
        message = format_message(event)
        assert message and message != event.code
        assert "pc-1" in message and "torch" in message


# ---------------------------------------------------------------------- GrantItem


class TestGrantItemMagic:
    def test_a_magic_id_creates_unidentified_instances_and_reports_their_ids(self):
        session = new_session()
        result = session.execute(GrantItem(character_id=SECOND, item_id="potion_of_healing", quantity=2))
        assert result.accepted
        member = session.member(SECOND)
        granted = [instance for instance in member.inventory.items if isinstance(instance, MagicItemInstance)]
        assert len(granted) == 2
        assert all(instance.template_id == "potion_of_healing" for instance in granted)
        assert all(not instance.identified for instance in granted)
        ids = tuple(instance.instance_id for instance in granted)
        assert len(set(ids)) == 2, "each granted copy has its own instance id"
        assert list(result.events) == [ItemAcquiredEvent(character_id=SECOND, item_ids=ids, origin="grant")]

    def test_a_granted_device_is_made_the_way_treasure_makes_it(self):
        session = new_session()
        result = session.execute(GrantItem(character_id=LEAD, item_id="wand_of_fire_balls"))
        assert result.accepted
        (instance_id,) = result.events[0].item_ids
        wand = session.member(LEAD).inventory.magic_item(instance_id)
        assert wand is not None and wand.template_id == "wand_of_fire_balls"
        assert wand.charges_remaining is not None and 2 <= wand.charges_remaining <= 20, "charges rolled as 2d10"

    def test_the_player_view_masks_a_granted_magic_item(self):
        session = new_session()
        result = session.execute(GrantItem(character_id=LEAD, item_id="potion_of_healing"))
        (instance_id,) = result.events[0].item_ids
        lead = next(member for member in session.view(Visibility.PLAYER).party if member.id == LEAD)
        (entry,) = [item for item in lead.inventory["items"] if item.get("instance_id") == instance_id]
        assert entry["identified"] is False
        assert "template_id" not in entry
        assert "potion_of_healing" not in json.dumps(entry)

    def test_a_member_can_drink_a_granted_potion(self):
        session = new_session()
        result = session.execute(GrantItem(character_id=LEAD, item_id="potion_of_healing"))
        (instance_id,) = result.events[0].item_ids
        assert session.execute(EnterDungeon(dungeon_id="delve")).accepted
        member = session.member(LEAD)
        member.current_hp = 1
        drunk = session.execute(UseItem(character_id=LEAD, item_id=instance_id))
        assert drunk.accepted
        codes = [event.code for event in drunk.events]
        assert "items.potion.drunk" in codes
        assert member.current_hp > 1
        assert member.inventory.magic_item(instance_id) is None, "the potion is gone once drunk"

    def test_a_granted_cursed_ring_sticks_like_a_found_one(self):
        session = new_session()
        result = session.execute(GrantItem(character_id=LEAD, item_id="ring_of_weakness"))
        (instance_id,) = result.events[0].item_ids
        equipped = session.execute(EquipItem(character_id=LEAD, item_id=instance_id))
        assert equipped.accepted
        assert "items.curse.revealed" in [event.code for event in equipped.events]
        stuck = session.execute(UnequipItem(character_id=LEAD, item_id=instance_id))
        assert [rejection.code for rejection in stuck.rejections] == ["items.curse.stuck"]

    def test_a_magic_grant_replays(self):
        document = adventure()
        session = new_session(document)
        for command in (
            GrantItem(character_id=LEAD, item_id="wand_of_fire_balls"),
            GrantItem(character_id=THIRD, item_id="potion_of_healing", quantity=2),
            GrantItem(character_id=THIRD, item_id="torch", quantity=6),
        ):
            assert session.execute(command).accepted
        assert_replays(session, document)

    def test_an_id_in_neither_catalog_is_refused(self):
        session = new_session()
        assert_refused(
            session,
            GrantItem(character_id=LEAD, item_id="no_such_item"),
            "session.command.unknown_item",
        )

    def test_a_mundane_grant_is_unchanged(self):
        session = new_session()
        result = session.execute(GrantItem(character_id=LEAD, item_id="torch", quantity=6))
        assert list(result.events) == [
            ItemAcquiredEvent(character_id=LEAD, item_ids=("torch",) * 6, origin="grant"),
        ]
        assert units(session, LEAD, "torch") == 6


# ---------------------------------------------------------------------- RemoveItem


def granted_magic(session: GameSession, character_id: str, item_id: str) -> str:
    """Grant one magic item and return its instance id."""
    result = session.execute(GrantItem(character_id=character_id, item_id=item_id))
    assert result.accepted
    (instance_id,) = result.events[0].item_ids
    return instance_id


class TestRemoveItem:
    def test_a_catalog_id_removes_units_from_the_stack(self):
        from osrlib.crawl.commands import RemoveItem
        from osrlib.crawl.events import ItemsRemovedEvent

        session = new_session()
        session.execute(GrantItem(character_id=LEAD, item_id="torch", quantity=6))
        streams = stream_states(session)
        rounds = session.clock.rounds
        result = session.execute(RemoveItem(character_id=LEAD, item_id="torch", quantity=2))
        assert result.accepted
        assert units(session, LEAD, "torch") == 4
        assert list(result.events) == [ItemsRemovedEvent(character_id=LEAD, item_ids=("torch",) * 2)]
        assert stream_states(session) == streams and session.clock.rounds == rounds, "a removal is bookkeeping"
        assert session.command_log[-1] == RemoveItem(character_id=LEAD, item_id="torch", quantity=2)

    def test_removing_the_whole_stack_leaves_nothing_behind(self):
        from osrlib.crawl.commands import RemoveItem

        session = new_session()
        session.execute(GrantItem(character_id=LEAD, item_id="torch", quantity=6))
        assert session.execute(RemoveItem(character_id=LEAD, item_id="torch", quantity=6)).accepted
        assert units(session, LEAD, "torch") == 0
        assert session.member(LEAD).inventory.carried_item("torch") is None

    def test_an_instance_id_removes_that_magic_item(self):
        from osrlib.crawl.commands import RemoveItem
        from osrlib.crawl.events import ItemsRemovedEvent

        session = new_session()
        keep = granted_magic(session, LEAD, "potion_of_healing")
        sell = granted_magic(session, LEAD, "potion_of_healing")
        result = session.execute(RemoveItem(character_id=LEAD, item_id=sell))
        assert result.accepted
        inventory = session.member(LEAD).inventory
        assert inventory.magic_item(sell) is None
        assert inventory.magic_item(keep) is not None, "only the named copy goes"
        assert list(result.events) == [ItemsRemovedEvent(character_id=LEAD, item_ids=(sell,))]

    def test_an_instance_id_removes_a_whole_ammunition_stack(self):
        from osrlib.crawl.commands import RemoveItem

        session = new_session()
        quiver = MagicItemInstance(instance_id="magic-item-9001", template_id="arrows_plus_1", quantity=10)
        session.member(LEAD).inventory.items.append(quiver)
        assert session.execute(RemoveItem(character_id=LEAD, item_id="magic-item-9001")).accepted
        assert session.member(LEAD).inventory.magic_item("magic-item-9001") is None

    def test_an_instance_id_removes_a_valuable(self):
        from osrlib.crawl.commands import RemoveItem
        from osrlib.crawl.events import ItemsRemovedEvent

        session = new_session()
        gem = ValuableInstance(instance_id="valuable-9001", kind="gem", value_gp=50)
        session.member(SECOND).inventory.valuables.append(gem)
        result = session.execute(RemoveItem(character_id=SECOND, item_id="valuable-9001"))
        assert result.accepted
        assert session.member(SECOND).inventory.valuables == []
        assert list(result.events) == [ItemsRemovedEvent(character_id=SECOND, item_ids=("valuable-9001",))]

    def test_an_equipped_weapon_is_unequipped_and_removed(self):
        from osrlib.crawl.commands import RemoveItem

        session = new_session()
        session.execute(GrantItem(character_id=LEAD, item_id="sword"))
        assert session.execute(EquipItem(character_id=LEAD, item_id="sword")).accepted
        result = session.execute(RemoveItem(character_id=LEAD, item_id="sword"))
        assert result.accepted
        inventory = session.member(LEAD).inventory
        assert inventory.wielded == []
        assert units(session, LEAD, "sword") == 0, "unequipped and gone, not put back in the pack"

    def test_worn_armour_is_unequipped_and_removed(self):
        from osrlib.crawl.commands import RemoveItem

        session = new_session()
        member = session.member(LEAD)
        bare = member.armour_class
        session.execute(GrantItem(character_id=LEAD, item_id="chainmail"))
        assert session.execute(EquipItem(character_id=LEAD, item_id="chainmail")).accepted
        assert member.armour_class != bare
        assert session.execute(RemoveItem(character_id=LEAD, item_id="chainmail")).accepted
        assert member.inventory.worn_armour is None
        assert units(session, LEAD, "chainmail") == 0
        assert member.armour_class == bare

    def test_a_revealed_curse_does_not_stop_a_referee_removal(self):
        from osrlib.crawl.commands import RemoveItem
        from osrlib.crawl.events import ItemsRemovedEvent

        session = new_session()
        ring_id = granted_magic(session, LEAD, "ring_of_weakness")
        assert session.execute(EquipItem(character_id=LEAD, item_id=ring_id)).accepted
        ring = session.member(LEAD).inventory.magic_item(ring_id)
        assert ring is not None and ring.cursed_revealed
        result = session.execute(RemoveItem(character_id=LEAD, item_id=ring_id))
        assert result.accepted
        inventory = session.member(LEAD).inventory
        assert inventory.rings == []
        assert inventory.magic_item(ring_id) is None
        removals = [event for event in result.events if isinstance(event, ItemsRemovedEvent)]
        assert removals == [ItemsRemovedEvent(character_id=LEAD, item_ids=(ring_id,))]

    def test_an_unknown_member_is_refused(self):
        from osrlib.crawl.commands import RemoveItem

        session = new_session()
        session.execute(GrantItem(character_id=LEAD, item_id="torch", quantity=6))
        assert_refused(
            session, RemoveItem(character_id="character-0099", item_id="torch"), "session.command.unknown_member"
        )

    def test_a_selector_sent_straight_to_the_session_is_an_unknown_member(self):
        from osrlib.crawl.commands import RemoveItem

        session = new_session()
        session.execute(GrantItem(character_id=LEAD, item_id="torch", quantity=6))
        for selector in (CARRIER_SELECTOR, PARTY_SELECTOR, FIRST_LIVING_SELECTOR):
            assert_refused(
                session, RemoveItem(character_id=selector, item_id="torch"), "session.command.unknown_member"
            )

    def test_an_item_the_member_does_not_carry_is_refused(self):
        from osrlib.crawl.commands import RemoveItem

        session = new_session()
        session.execute(GrantItem(character_id=SECOND, item_id="torch", quantity=6))
        assert_refused(session, RemoveItem(character_id=LEAD, item_id="torch"), "items.remove.not_carried")

    def test_an_id_in_no_catalog_is_not_carried(self):
        from osrlib.crawl.commands import RemoveItem

        session = new_session()
        assert_refused(session, RemoveItem(character_id=LEAD, item_id="no_such_item"), "items.remove.not_carried")

    def test_fewer_units_than_asked_for_is_refused(self):
        from osrlib.crawl.commands import RemoveItem

        session = new_session()
        session.execute(GrantItem(character_id=LEAD, item_id="torch", quantity=6))
        assert_refused(session, RemoveItem(character_id=LEAD, item_id="torch", quantity=7), "items.remove.not_carried")
        assert units(session, LEAD, "torch") == 6

    def test_another_members_instance_is_refused(self):
        from osrlib.crawl.commands import RemoveItem

        session = new_session()
        potion = granted_magic(session, SECOND, "potion_of_healing")
        assert_refused(session, RemoveItem(character_id=LEAD, item_id=potion), "items.remove.not_carried")
        assert session.member(SECOND).inventory.magic_item(potion) is not None

    def test_an_instance_id_with_a_quantity_other_than_one_is_refused(self):
        from osrlib.crawl.commands import RemoveItem

        session = new_session()
        potion = granted_magic(session, LEAD, "potion_of_healing")
        assert_refused(session, RemoveItem(character_id=LEAD, item_id=potion, quantity=2), "items.remove.not_carried")
        gem = ValuableInstance(instance_id="valuable-9001", kind="gem", value_gp=50)
        session.member(LEAD).inventory.valuables.append(gem)
        assert_refused(
            session, RemoveItem(character_id=LEAD, item_id="valuable-9001", quantity=2), "items.remove.not_carried"
        )


# ---------------------------------------------------------------------- RemoveCoins


class TestRemoveCoins:
    def test_it_removes_exactly_the_denominations_asked_for(self):
        from osrlib.crawl.commands import RemoveCoins
        from osrlib.crawl.events import ItemsRemovedEvent

        session = new_session()
        session.execute(GrantCoins(character_id=LEAD, coins=Coins(pp=1, gp=10, sp=5, cp=40)))
        streams = stream_states(session)
        rounds = session.clock.rounds
        toll = Coins(gp=3, sp=5)
        result = session.execute(RemoveCoins(character_id=LEAD, coins=toll))
        assert result.accepted
        assert purse(session, LEAD) == {"pp": 1, "gp": 7, "ep": 0, "sp": 0, "cp": 40}
        assert list(result.events) == [ItemsRemovedEvent(character_id=LEAD, coins_gp_value=toll.value_gp)]
        assert stream_states(session) == streams and session.clock.rounds == rounds, "a removal is bookkeeping"

    def test_it_makes_no_change_from_another_denomination(self):
        from osrlib.crawl.commands import RemoveCoins

        session = new_session()
        session.execute(GrantCoins(character_id=LEAD, coins=Coins(gp=100)))
        assert_refused(session, RemoveCoins(character_id=LEAD, coins=Coins(sp=5)), "items.coins.insufficient")
        assert purse(session, LEAD)["gp"] == 100

    def test_a_purse_short_of_any_denomination_is_refused(self):
        from osrlib.crawl.commands import RemoveCoins

        session = new_session()
        session.execute(GrantCoins(character_id=LEAD, coins=Coins(gp=10, sp=2)))
        assert_refused(session, RemoveCoins(character_id=LEAD, coins=Coins(gp=5, sp=3)), "items.coins.insufficient")
        assert_refused(session, RemoveCoins(character_id=LEAD, coins=Coins(gp=11)), "items.coins.insufficient")
        assert purse(session, LEAD) == {"pp": 0, "gp": 10, "ep": 0, "sp": 2, "cp": 0}

    def test_the_exact_purse_can_be_emptied(self):
        from osrlib.crawl.commands import RemoveCoins

        session = new_session()
        session.execute(GrantCoins(character_id=LEAD, coins=Coins(gp=10, sp=2)))
        assert session.execute(RemoveCoins(character_id=LEAD, coins=Coins(gp=10, sp=2))).accepted
        assert purse(session, LEAD) == {"pp": 0, "gp": 0, "ep": 0, "sp": 0, "cp": 0}

    def test_an_unknown_member_is_refused(self):
        from osrlib.crawl.commands import RemoveCoins

        session = new_session()
        assert_refused(
            session, RemoveCoins(character_id="character-0099", coins=Coins(gp=1)), "session.command.unknown_member"
        )


# ---------------------------------------------------------------------- modes and replay


class TestEveryModeAndReplay:
    @pytest.mark.parametrize("mode", [SessionMode.GAME_OVER, SessionMode.VICTORY], ids=lambda mode: mode.value)
    def test_the_three_commands_execute_in_a_terminal_mode(self, mode):
        from osrlib.crawl.commands import RemoveCoins, RemoveItem

        session = new_session()
        session.execute(GrantItem(character_id=LEAD, item_id="torch", quantity=6))
        session.execute(GrantCoins(character_id=LEAD, coins=Coins(gp=20)))
        session.mode = mode
        potion = session.execute(GrantItem(character_id=LEAD, item_id="potion_of_healing"))
        assert potion.accepted
        (instance_id,) = potion.events[0].item_ids
        for command in (
            RemoveItem(character_id=LEAD, item_id="torch"),
            RemoveItem(character_id=LEAD, item_id=instance_id),
            RemoveCoins(character_id=LEAD, coins=Coins(gp=5)),
        ):
            assert session.execute(command).accepted, command.command_type
        assert session.mode is mode

    def test_the_commands_execute_while_exploring(self):
        from osrlib.crawl.commands import RemoveCoins, RemoveItem

        session = new_session()
        session.execute(GrantItem(character_id=LEAD, item_id="torch", quantity=6))
        session.execute(GrantCoins(character_id=LEAD, coins=Coins(gp=20)))
        assert session.execute(EnterDungeon(dungeon_id="delve")).accepted
        assert session.execute(RemoveItem(character_id=LEAD, item_id="torch")).accepted
        assert session.execute(RemoveCoins(character_id=LEAD, coins=Coins(gp=5))).accepted

    def test_a_shop_sequence_saves_loads_and_replays(self):
        from osrlib.crawl.commands import RemoveCoins, RemoveItem

        document = adventure()
        session = new_session(document)
        session.execute(GrantCoins(character_id=LEAD, coins=Coins(gp=100)))
        session.execute(GrantItem(character_id=LEAD, item_id="chainmail"))
        session.execute(EquipItem(character_id=LEAD, item_id="chainmail"))
        # Buy a potion: take the price, grant the potion.
        assert session.execute(RemoveCoins(character_id=LEAD, coins=Coins(gp=50))).accepted
        potion = session.execute(GrantItem(character_id=LEAD, item_id="potion_of_healing"))
        (instance_id,) = potion.events[0].item_ids
        # Sell the old armour back: take the armour, pay for it.
        assert session.execute(RemoveItem(character_id=LEAD, item_id="chainmail")).accepted
        session.execute(GrantCoins(character_id=LEAD, coins=Coins(gp=20)))
        # Hand the potion back.
        assert session.execute(RemoveItem(character_id=LEAD, item_id=instance_id)).accepted
        assert [command.command_type for command in session.command_log][-5:] == [
            "remove_coins",
            "grant_item",
            "remove_item",
            "grant_coins",
            "remove_item",
        ]
        assert purse(session, LEAD)["gp"] == 70
        assert len(removed_events(session)) == 3
        assert_replays(session, document)


# ---------------------------------------------------------------------- the interpreter


def errand(*rewards) -> QuestSpec:
    """A one-objective quest that completes when the flag `delivered` is set."""
    return QuestSpec(
        id="errand",
        name="An Errand",
        activation=TriggerClause(pattern=FlagSetPattern(key="start")),
        objectives=(ObjectiveSpec(id="deliver", when=TriggerClause(pattern=FlagSetPattern(key="delivered"))),),
        rewards=rewards,
    )


def quest_adventure(*rewards) -> Adventure:
    return adventure(items=(QUEST_ITEM,), quests=(errand(*rewards),))


def complete_errand(session: GameSession) -> None:
    session.execute(SetFlag(key="start", value=True))
    session.execute(SetFlag(key="delivered", value=True))
    assert session.quests["errand"].status == "completed"


class TestCarrierSelector:
    def test_a_quest_completion_takes_the_quest_item_from_its_first_carrier(self):
        from osrlib.crawl.commands import RemoveItem
        from osrlib.crawl.events import ItemsRemovedEvent

        document = quest_adventure(
            RemoveItem(character_id=CARRIER_SELECTOR, item_id=QUEST_ITEM.id),
            AwardXP(character_id=PARTY_SELECTOR, amount=50),
        )
        session = played(document)
        session.execute(GrantItem(character_id=THIRD, item_id=QUEST_ITEM.id))
        session.execute(GrantItem(character_id=SECOND, item_id=QUEST_ITEM.id))
        complete_errand(session)
        (removal,) = logged(session, "remove_item")
        assert removal.character_id == SECOND, "the first carrier in marching order, not the first member"
        assert removal.source == "quest:errand"
        assert units(session, SECOND, QUEST_ITEM.id) == 0
        assert units(session, THIRD, QUEST_ITEM.id) == 1, "only one is used up"
        assert removed_events(session) == [ItemsRemovedEvent(character_id=SECOND, item_ids=(QUEST_ITEM.id,))]
        assert len(logged(session, "award_xp")) == 4, "the next reward still lands"
        assert notes(session) == []
        assert_replays(session, document)

    def test_a_dead_carrier_is_passed_over(self):
        from osrlib.crawl.commands import RemoveItem

        document = quest_adventure(RemoveItem(character_id=CARRIER_SELECTOR, item_id=QUEST_ITEM.id))
        session = played(document)
        session.execute(GrantItem(character_id=SECOND, item_id=QUEST_ITEM.id))
        session.execute(GrantItem(character_id=FOURTH, item_id=QUEST_ITEM.id))
        kill(session.member(SECOND))
        complete_errand(session)
        (removal,) = logged(session, "remove_item")
        assert removal.character_id == FOURTH
        assert units(session, SECOND, QUEST_ITEM.id) == 1
        assert units(session, FOURTH, QUEST_ITEM.id) == 0

    def test_with_no_carrier_the_consequence_drops_with_a_note(self):
        from osrlib.crawl.commands import RemoveItem

        trigger = TriggerSpec(
            id="altar",
            when=FlagSetPattern(key="offered"),
            consequences=(
                RemoveItem(character_id=CARRIER_SELECTOR, item_id="holy_water"),
                SetFlag(key="blessed", value=True),
            ),
        )
        session = played(adventure(triggers=(trigger,)))
        state = session.member(LEAD).inventory.model_dump(mode="json")
        session.execute(SetFlag(key="offered", value=True))
        assert session.fired_triggers == ["altar"]
        assert logged(session, "remove_item") == []
        (note,) = notes(session)
        assert note.startswith("trigger altar: consequence 0 (remove_item) dropped (")
        assert session.flags["blessed"] is True, "one dropped consequence never stops the rest"
        assert session.member(LEAD).inventory.model_dump(mode="json") == state

    def test_party_removes_from_every_living_member_and_notes_who_had_none(self):
        from osrlib.crawl.commands import RemoveItem

        trigger = TriggerSpec(
            id="rot",
            when=FlagSetPattern(key="damp"),
            consequences=(RemoveItem(character_id=PARTY_SELECTOR, item_id="torch"),),
        )
        session = played(adventure(triggers=(trigger,)))
        session.execute(GrantItem(character_id=LEAD, item_id="torch", quantity=6))
        session.execute(GrantItem(character_id=THIRD, item_id="torch", quantity=6))
        session.execute(SetFlag(key="damp", value=True))
        assert [command.character_id for command in logged(session, "remove_item")] == [LEAD, THIRD]
        assert units(session, LEAD, "torch") == 5
        assert units(session, THIRD, "torch") == 5
        assert notes(session) == ["trigger rot: consequence 0 (remove_item) dropped (items.remove.not_carried)"] * 2

    def test_first_removes_from_the_lead_survivor(self):
        from osrlib.crawl.commands import RemoveItem

        trigger = TriggerSpec(
            id="snatch",
            when=FlagSetPattern(key="grabbed"),
            consequences=(RemoveItem(character_id=FIRST_LIVING_SELECTOR, item_id="torch"),),
        )
        session = played(adventure(triggers=(trigger,)))
        session.execute(GrantItem(character_id=SECOND, item_id="torch", quantity=6))
        kill(session.member(LEAD))
        session.execute(SetFlag(key="grabbed", value=True))
        assert [command.character_id for command in logged(session, "remove_item")] == [SECOND]
        assert units(session, SECOND, "torch") == 5


class TestTollTrigger:
    def toll(self) -> Adventure:
        from osrlib.crawl.commands import RemoveCoins

        trigger = TriggerSpec(
            id="toll",
            when=DungeonEnteredPattern(dungeon_id="delve"),
            consequences=(RemoveCoins(character_id=FIRST_LIVING_SELECTOR, coins=Coins(gp=5)),),
        )
        return adventure(triggers=(trigger,))

    def test_entering_the_dungeon_takes_the_toll(self):
        from osrlib.crawl.events import ItemsRemovedEvent

        document = self.toll()
        session = played(document)
        session.execute(GrantCoins(character_id=LEAD, coins=Coins(gp=12)))
        assert session.execute(EnterDungeon(dungeon_id="delve")).accepted
        (paid,) = logged(session, "remove_coins")
        assert paid.character_id == LEAD and paid.source == "trigger:toll"
        assert purse(session, LEAD)["gp"] == 7
        assert removed_events(session) == [ItemsRemovedEvent(character_id=LEAD, coins_gp_value=5)]
        assert notes(session) == []
        assert_replays(session, document)

    def test_a_toll_nobody_can_pay_drops_with_a_note(self):
        session = played(self.toll())
        session.execute(GrantCoins(character_id=LEAD, coins=Coins(sp=40)))
        assert session.execute(EnterDungeon(dungeon_id="delve")).accepted
        assert logged(session, "remove_coins") == []
        assert purse(session, LEAD)["sp"] == 40
        assert notes(session) == ["trigger toll: consequence 0 (remove_coins) dropped (items.coins.insufficient)"]


class TestMagicRewards:
    def test_a_quest_reward_can_grant_a_magic_item(self):
        document = quest_adventure(GrantItem(character_id=FIRST_LIVING_SELECTOR, item_id="potion_of_healing"))
        session = played(document)
        complete_errand(session)
        (grant,) = logged(session, "grant_item")
        assert grant.character_id == LEAD and grant.source == "quest:errand"
        potions = [
            instance
            for instance in session.member(LEAD).inventory.items
            if isinstance(instance, MagicItemInstance) and instance.template_id == "potion_of_healing"
        ]
        assert len(potions) == 1 and not potions[0].identified
        assert notes(session) == []
        assert_replays(session, document)


# ---------------------------------------------------------------------- validate_adventure


def validate(document: Adventure) -> None:
    from osrlib.crawl.adventure import validate_adventure
    from osrlib.data import load_equipment, load_monsters

    validate_adventure(document, load_monsters(), load_equipment())


def validation_message(document: Adventure) -> str:
    from osrlib.errors import ContentValidationError

    with pytest.raises(ContentValidationError) as raised:
        validate(document)
    return str(raised.value)


def town_trigger(*consequences) -> Adventure:
    return adventure(triggers=(TriggerSpec(id="reward", when=TownEnteredPattern(), consequences=consequences),))


class TestValidation:
    def test_the_removal_selectors_are_accepted_on_a_consequence(self):
        from osrlib.crawl.commands import RemoveCoins, RemoveItem

        validate(
            town_trigger(
                RemoveItem(character_id=CARRIER_SELECTOR, item_id="holy_water"),
                RemoveItem(character_id=PARTY_SELECTOR, item_id="torch", quantity=2),
                RemoveItem(character_id=FIRST_LIVING_SELECTOR, item_id="torch"),
                RemoveCoins(character_id=PARTY_SELECTOR, coins=Coins(gp=1)),
                RemoveCoins(character_id=FIRST_LIVING_SELECTOR, coins=Coins(gp=5)),
            )
        )

    def test_the_removal_selectors_are_accepted_on_a_reward(self):
        from osrlib.crawl.commands import RemoveCoins, RemoveItem

        validate(
            quest_adventure(
                RemoveItem(character_id=CARRIER_SELECTOR, item_id=QUEST_ITEM.id),
                RemoveCoins(character_id=FIRST_LIVING_SELECTOR, coins=Coins(gp=5)),
            )
        )

    def test_a_literal_member_id_on_a_removal_is_an_error(self):
        from osrlib.crawl.commands import RemoveCoins, RemoveItem

        message = validation_message(
            town_trigger(
                RemoveItem(character_id=LEAD, item_id="torch"),
                RemoveCoins(character_id=SECOND, coins=Coins(gp=5)),
            )
        )
        assert f"trigger 'reward': consequence 0 names character '{LEAD}'" in message
        assert f"trigger 'reward': consequence 1 names character '{SECOND}'" in message

    def test_a_literal_member_id_on_a_removal_reward_is_an_error(self):
        from osrlib.crawl.commands import RemoveItem

        message = validation_message(quest_adventure(RemoveItem(character_id=LEAD, item_id=QUEST_ITEM.id)))
        assert f"quest 'errand': reward 0 names character '{LEAD}'" in message

    def test_the_carrier_selector_belongs_to_remove_item_alone(self):
        from osrlib.crawl.commands import RemoveCoins

        message = validation_message(town_trigger(RemoveCoins(character_id=CARRIER_SELECTOR, coins=Coins(gp=5))))
        assert f"trigger 'reward': consequence 0 names character '{CARRIER_SELECTOR}'" in message

    def test_the_carrier_selector_is_no_grant_selector(self):
        message = validation_message(town_trigger(GrantItem(character_id=CARRIER_SELECTOR, item_id="torch")))
        assert f"trigger 'reward': consequence 0 names character '{CARRIER_SELECTOR}'" in message

    def test_an_authored_grant_may_name_a_magic_item(self):
        validate(town_trigger(GrantItem(character_id=PARTY_SELECTOR, item_id="potion_of_healing")))
        validate(quest_adventure(GrantItem(character_id=FIRST_LIVING_SELECTOR, item_id="wand_of_fire_balls")))

    def test_an_authored_grant_of_an_unknown_id_is_still_an_error(self):
        message = validation_message(town_trigger(GrantItem(character_id=PARTY_SELECTOR, item_id="no_such_item")))
        assert "trigger 'reward': consequence 0 references unknown item 'no_such_item'" in message
