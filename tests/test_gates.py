"""Gates: condition evaluation, the gated commands, consumption, and validation.

Four groups. `TestConditionEvaluation` pins the union's semantics against live
state — what counts as carried, whose inventory counts, how strictly a flag
compares. `TestGatedCommands` walks the command census: which commands evaluate a
gate, in what order relative to the mundane rejections, and which never look.
`TestConsumption` covers the toll — what leaves whose pack, what the event says,
and how often. `TestGateModels` and `TestGateValidation` cover the document
surface: parsing, the trap-effect prohibition, and the item-id gate.
"""

import pytest

from crawl_fixtures import GATE_KEY, GATE_SEAL, GATE_TOKEN, STOCK_ROSTER, build_gated_adventure, build_party
from osrlib.core.effects import EffectDefinition, EffectsLedger
from osrlib.core.items import ItemInstance, MagicItemInstance, ValuableInstance
from osrlib.crawl import exploration as exploration_module
from osrlib.crawl.adventure import Adventure, TownSpec, validate_adventure
from osrlib.crawl.commands import (
    CloseDoor,
    EnterDungeon,
    ForceDoor,
    GrantItem,
    ListenAtDoor,
    MoveParty,
    OpenDoor,
    PickLock,
    PlaceParty,
    SetDoorState,
    SetFlag,
    UseStairs,
    WedgeDoor,
)
from osrlib.crawl.dungeon import (
    Direction,
    DoorSpec,
    DungeonSpec,
    Edge,
    EdgeKind,
    LevelSpec,
    PartyLocation,
    TransitionSpec,
    WanderingSpec,
)
from osrlib.crawl.gates import (
    EffectActiveCondition,
    FlagEqualsCondition,
    GateSpec,
    HasItemCondition,
    condition_holds,
    first_holder,
)
from osrlib.crawl.narrative import NarrativeBlock
from osrlib.crawl.session import GameSession
from osrlib.data import load_equipment
from osrlib.errors import ContentValidationError


def hold(condition, *, members=(), flags=None, ledger=None) -> bool:
    return condition_holds(
        condition, members=members, flags=flags if flags is not None else {}, ledger=ledger or EffectsLedger()
    )


def gear(item_id: str, quantity: int = 1) -> ItemInstance:
    return ItemInstance(template=load_equipment().get(item_id), quantity=quantity)


def gated_session(seed: int = 91) -> GameSession:
    session = GameSession.new(build_party(), build_gated_adventure(), seed=seed)
    session.execute(EnterDungeon(dungeon_id="warren"))
    return session


def door_gate_session(gate, *, roster=STOCK_ROSTER, seed: int = 7, **door_fields) -> GameSession:
    """A two-cell dungeon whose single east-west door carries `gate`, party inside.

    The harness for the command census: `door_fields` sets the mundane door
    conditions (`locked`, `stuck`, `starts_open`) the gate has to compose with.
    """
    level = LevelSpec(
        number=1,
        width=2,
        height=1,
        edges={"1,0:west": Edge(kind=EdgeKind.DOOR, door=DoorSpec(requires=gate, **door_fields))},
        entrance=(0, 0),
        wandering=WanderingSpec(chance_in_six=0),
    )
    adventure = Adventure(
        name="One Door",
        town=TownSpec(name="Threshold", travel_turns={"gate": 0}),
        dungeons=(DungeonSpec(id="gate", name="Gate", levels=(level,)),),
        items=(GATE_KEY, GATE_TOKEN, GATE_SEAL),
    )
    session = GameSession.new(build_party(roster), adventure, seed=seed)
    session.execute(EnterDungeon(dungeon_id="gate"))
    return session


def state_probe(session) -> tuple[dict, int, dict]:
    """Streams, clock, and door overlay — what a refusal must leave untouched."""
    return (
        {key: value.model_dump() for key, value in session.streams.export_states().items()},
        session.clock.rounds,
        {ref: state.model_dump() for ref, state in session.dungeon_state.doors.items()},
    )


def carried(member, item_id: str) -> int:
    """How many units of `item_id` the member carries, across every slot."""
    return sum(
        instance.quantity
        for instance in member.inventory.all_instances()
        if (instance.template_id if isinstance(instance, MagicItemInstance) else instance.template.id) == item_id
    )


KEY_GATE = GateSpec(
    condition=HasItemCondition(item_id="brass_key"),
    narrative=NarrativeBlock(refusal="Brass or nothing.", success="The lock gives."),
)
BARE_GATE = GateSpec(condition=HasItemCondition(item_id="brass_key"))
TOLL_GATE = GateSpec(
    condition=HasItemCondition(item_id="toll_token", consumes=True),
    narrative=NarrativeBlock(refusal="No token, no crossing."),
)


class TestConditionEvaluation:
    def test_has_item_matches_a_carried_mundane_instance(self):
        party = build_party()
        assert not hold(HasItemCondition(item_id="torch"), members=party.members)
        party.members[2].inventory.items.append(gear("torch", 6))
        assert hold(HasItemCondition(item_id="torch"), members=party.members)

    def test_has_item_matches_a_magic_instance_by_template_id(self):
        party = build_party()
        party.members[0].inventory.items.append(
            MagicItemInstance(instance_id="magic-item-0001", template_id="potion_of_healing")
        )
        assert hold(HasItemCondition(item_id="potion_of_healing"), members=party.members)
        assert not hold(HasItemCondition(item_id="magic-item-0001"), members=party.members)

    def test_equipped_and_ringed_items_count_as_carried(self):
        party = build_party()
        party.members[0].inventory.worn_armour = gear("chainmail")
        party.members[1].inventory.rings.append(
            MagicItemInstance(instance_id="magic-item-0002", template_id="ring_of_protection")
        )
        assert hold(HasItemCondition(item_id="chainmail"), members=party.members)
        assert hold(HasItemCondition(item_id="ring_of_protection"), members=party.members)

    def test_valuables_never_match(self):
        party = build_party()
        party.members[0].inventory.valuables.append(
            ValuableInstance(instance_id="valuable-0001", kind="gem", name="brass key", value_gp=50)
        )
        assert not hold(HasItemCondition(item_id="valuable-0001"), members=party.members)
        assert not hold(HasItemCondition(item_id="brass key"), members=party.members)

    def test_a_dead_members_pack_still_counts(self):
        from osrlib.core.effects import Condition, grant_condition

        party = build_party()
        party.members[3].inventory.items.append(gear("iron_spikes", 12))
        grant_condition(party.members[3], Condition.DEAD, None)
        assert party.living_members() == party.members[:3]
        assert hold(HasItemCondition(item_id="iron_spikes"), members=party.members)

    def test_consumes_does_not_affect_evaluation(self):
        party = build_party()
        party.members[0].inventory.items.append(gear("torch"))
        assert hold(HasItemCondition(item_id="torch", consumes=True), members=party.members)
        # Evaluation is pure: the torch is still there afterwards.
        assert len(party.members[0].inventory.items) == 1

    @pytest.mark.parametrize(
        ("stored", "authored", "expected"),
        [
            ("raised", "raised", True),
            ("raised", "lowered", False),
            (1, 1, True),
            (1, True, False),
            (True, 1, False),
            (True, True, True),
            (0, False, False),
            (False, False, True),
        ],
    )
    def test_flag_equality_is_strict_about_boolness(self, stored, authored, expected):
        condition = FlagEqualsCondition(key="portcullis", value=authored)
        assert hold(condition, flags={"portcullis": stored}) is expected

    def test_an_absent_flag_equals_nothing_not_even_false(self):
        assert not hold(FlagEqualsCondition(key="lever", value=False))
        assert not hold(FlagEqualsCondition(key="lever", value=""))
        assert not hold(FlagEqualsCondition(key="lever", value=0))

    def test_effect_active_reads_the_ledger_per_member(self):
        from osrlib.core.clock import GameClock
        from osrlib.core.monsters import IdAllocator

        party = build_party()
        for index, member in enumerate(party.members, start=1):
            member.id = f"character-{index:04d}"
        ledger = EffectsLedger()
        condition = EffectActiveCondition(kind="blessing")
        assert not hold(condition, members=party.members, ledger=ledger)
        ledger.attach(
            EffectDefinition(kind="blessing"),
            party.members[2].id,
            clock=GameClock(),
            allocator=IdAllocator(),
        )
        assert hold(condition, members=party.members, ledger=ledger)
        assert not hold(EffectActiveCondition(kind="curse"), members=party.members, ledger=ledger)

    def test_a_location_attached_effect_never_counts(self):
        from osrlib.core.clock import GameClock
        from osrlib.core.monsters import IdAllocator

        party = build_party()
        for index, member in enumerate(party.members, start=1):
            member.id = f"character-{index:04d}"
        ledger = EffectsLedger()
        ledger.attach(
            EffectDefinition(kind="blessing"),
            "cell:warren:1:0,0",
            clock=GameClock(),
            allocator=IdAllocator(),
        )
        assert not hold(EffectActiveCondition(kind="blessing"), members=party.members, ledger=ledger)

    def test_first_holder_walks_marching_order(self):
        party = build_party()
        assert first_holder(party.members, "torch") is None
        party.members[2].inventory.items.append(gear("torch"))
        party.members[1].inventory.items.append(gear("torch"))
        assert first_holder(party.members, "torch") is party.members[1]

    def test_evaluation_mutates_nothing(self):
        party = build_party()
        party.members[0].inventory.items.append(gear("torch", 3))
        before = party.model_dump(mode="json")
        for condition in (
            HasItemCondition(item_id="torch", consumes=True),
            HasItemCondition(item_id="missing_id"),
            FlagEqualsCondition(key="lever", value=True),
            EffectActiveCondition(kind="blessing"),
        ):
            hold(condition, members=party.members)
        assert party.model_dump(mode="json") == before


class TestGatedCommands:
    def test_open_door_refuses_with_the_authored_text_and_costs_nothing(self):
        session = door_gate_session(KEY_GATE)
        before = state_probe(session)
        result = session.execute(OpenDoor(direction=Direction.EAST))
        assert not result.accepted
        (rejection,) = result.rejections
        assert rejection.code == "exploration.door.gate_refused"
        assert rejection.params == {"direction": "east", "refusal": "Brass or nothing."}
        assert result.events == ()
        assert state_probe(session) == before, "a gate refusal draws nothing, costs nothing, and stores nothing"

    def test_an_unauthored_gate_refuses_with_no_text_at_all(self):
        session = door_gate_session(BARE_GATE)
        result = session.execute(OpenDoor(direction=Direction.EAST))
        assert result.rejections[0].code == "exploration.door.gate_refused"
        assert result.rejections[0].params == {"direction": "east"}

    def test_any_members_key_opens_the_door(self):
        session = door_gate_session(KEY_GATE)
        session.execute(GrantItem(character_id="character-0004", item_id="brass_key"))
        result = session.execute(OpenDoor(direction=Direction.EAST))
        assert result.accepted
        assert [event.code for event in result.events] == ["exploration.door.opened"]
        assert session.execute(MoveParty(direction=Direction.EAST)).accepted

    def test_a_flag_gate_and_an_effect_gate_open_the_same_way(self):
        flag_gate = GateSpec(condition=FlagEqualsCondition(key="portcullis", value="raised"))
        session = door_gate_session(flag_gate)
        assert session.execute(OpenDoor(direction=Direction.EAST)).rejections[0].code == (
            "exploration.door.gate_refused"
        )
        session.execute(SetFlag(key="portcullis", value="raised"))
        assert session.execute(OpenDoor(direction=Direction.EAST)).accepted

        from osrlib.core.monsters import IdAllocator

        effect_gate = GateSpec(condition=EffectActiveCondition(kind="blessing"))
        session = door_gate_session(effect_gate)
        assert session.execute(OpenDoor(direction=Direction.EAST)).rejections[0].code == (
            "exploration.door.gate_refused"
        )
        session.ledger.attach(
            EffectDefinition(kind="blessing"),
            "character-0002",
            clock=session.clock,
            allocator=IdAllocator(),
        )
        assert session.execute(OpenDoor(direction=Direction.EAST)).accepted

    @pytest.mark.parametrize(
        ("door_fields", "expected"),
        [
            ({"locked": True}, "exploration.door.locked"),
            ({"stuck": True}, "exploration.door.stuck"),
            ({"starts_open": True}, "exploration.door.already_open"),
        ],
    )
    def test_every_mundane_refusal_still_precedes_the_gate(self, door_fields, expected):
        session = door_gate_session(KEY_GATE, **door_fields)
        assert session.execute(OpenDoor(direction=Direction.EAST)).rejections[0].code == expected

    def test_force_door_checks_the_gate_before_the_noise_and_the_roll(self):
        session = door_gate_session(KEY_GATE, stuck=True)
        before = state_probe(session)
        result = session.execute(ForceDoor(character_id="character-0001", direction=Direction.EAST))
        assert result.rejections[0].code == "exploration.door.gate_refused"
        assert result.rejections[0].params["refusal"] == "Brass or nothing."
        assert session.noise_since_check is False, "a gate-refused forcing never banged on the door"
        assert state_probe(session) == before
        # With the key in hand the attempt happens: the noise flag and the die.
        session.execute(GrantItem(character_id="character-0001", item_id="brass_key"))
        result = session.execute(ForceDoor(character_id="character-0001", direction=Direction.EAST))
        assert result.accepted
        assert session.noise_since_check is True
        assert state_probe(session)[0] != before[0]

    def test_force_door_keeps_its_own_refusal_order(self):
        locked = door_gate_session(KEY_GATE, stuck=True, locked=True)
        assert (
            locked.execute(ForceDoor(character_id="character-0001", direction=Direction.EAST)).rejections[0].code
            == "exploration.door.locked"
        )
        unstuck = door_gate_session(KEY_GATE)
        assert (
            unstuck.execute(ForceDoor(character_id="character-0001", direction=Direction.EAST)).rejections[0].code
            == "exploration.door.not_stuck"
        )

    def test_use_stairs_refuses_with_the_authored_text_and_costs_nothing(self):
        session = gated_session()
        session.execute(
            PlaceParty(
                location=PartyLocation(
                    kind="dungeon", dungeon_id="warren", level_number=1, position=(3, 0), facing=Direction.EAST
                )
            )
        )
        before = state_probe(session)
        odometer = session.odometer_thirds
        result = session.execute(UseStairs())
        assert not result.accepted
        assert result.rejections[0].code == "exploration.transition.gate_refused"
        assert result.rejections[0].params == {
            "refusal": "The ferryman's hand stays out, empty. No token, no crossing."
        }
        assert state_probe(session) == before
        assert session.odometer_thirds == odometer, "a refused crossing accrues no movement"
        assert session.dungeon_state.location.level_number == 1

    def test_the_ungated_stair_and_the_ungated_door_are_untouched(self):
        session = gated_session()
        session.execute(
            PlaceParty(
                location=PartyLocation(
                    kind="dungeon", dungeon_id="warren", level_number=2, position=(0, 0), facing=Direction.EAST
                )
            )
        )
        assert session.execute(UseStairs()).accepted  # the stair back up carries no gate

    def test_close_wedge_and_listen_never_evaluate_the_gate(self):
        session = door_gate_session(KEY_GATE, starts_open=True, roster=(("Durin", "dwarf"), ("Sable", "thief")))
        session.execute(GrantItem(character_id="character-0001", item_id="iron_spikes", quantity=2))
        # A dwarf's infravision satisfies the listen check's light requirement.
        assert session.execute(ListenAtDoor(character_id="character-0001", direction=Direction.EAST)).accepted
        assert session.execute(WedgeDoor(direction=Direction.EAST)).accepted
        result = session.execute(CloseDoor(direction=Direction.EAST))
        assert result.rejections[0].code == "exploration.door.wedged"  # wedged, not gated
        session.execute(
            SetDoorState(dungeon_id="gate", level_number=1, x=0, y=0, direction=Direction.EAST, wedged=False)
        )
        assert session.execute(CloseDoor(direction=Direction.EAST)).accepted

    def test_pick_lock_addresses_only_the_lock(self):
        session = door_gate_session(KEY_GATE, roster=(("Sable", "thief"),))
        session.execute(GrantItem(character_id="character-0001", item_id="thieves_tools"))
        result = session.execute(PickLock(character_id="character-0001", direction=Direction.EAST))
        assert result.rejections[0].code == "exploration.lock.not_locked", "a thief is not a universal bypass"
        locked = door_gate_session(KEY_GATE, locked=True, roster=(("Sable", "thief"),))
        locked.execute(GrantItem(character_id="character-0001", item_id="thieves_tools"))
        result = locked.execute(PickLock(character_id="character-0001", direction=Direction.EAST))
        assert [rejection.code for rejection in result.rejections] == ["exploration.action.requires_light"]

    def test_a_referee_opened_door_admits_passage_until_it_closes(self):
        session = door_gate_session(KEY_GATE)
        session.execute(SetDoorState(dungeon_id="gate", level_number=1, x=0, y=0, direction=Direction.EAST, open=True))
        assert session.execute(MoveParty(direction=Direction.EAST)).accepted, "an open door admits passage unchecked"
        assert session.execute(MoveParty(direction=Direction.WEST)).accepted
        assert session.execute(CloseDoor(direction=Direction.EAST)).accepted
        # The gate re-applies the moment the door is shut again.
        assert session.execute(MoveParty(direction=Direction.EAST)).rejections[0].code == "exploration.move.blocked"
        assert session.execute(OpenDoor(direction=Direction.EAST)).rejections[0].code == (
            "exploration.door.gate_refused"
        )

    def test_the_lock_and_the_gate_are_each_required(self):
        session = door_gate_session(KEY_GATE, locked=True)
        assert session.execute(OpenDoor(direction=Direction.EAST)).rejections[0].code == "exploration.door.locked"
        session.execute(
            SetDoorState(dungeon_id="gate", level_number=1, x=0, y=0, direction=Direction.EAST, unlocked=True)
        )
        assert session.execute(OpenDoor(direction=Direction.EAST)).rejections[0].code == (
            "exploration.door.gate_refused"
        )
        session.execute(GrantItem(character_id="character-0002", item_id="brass_key"))
        assert session.execute(OpenDoor(direction=Direction.EAST)).accepted


class TestConsumption:
    def test_the_toll_leaves_the_first_holder_and_the_event_precedes_the_door(self):
        session = door_gate_session(TOLL_GATE)
        session.execute(GrantItem(character_id="character-0003", item_id="toll_token"))
        session.execute(GrantItem(character_id="character-0002", item_id="toll_token", quantity=2))
        result = session.execute(OpenDoor(direction=Direction.EAST))
        assert result.accepted
        consumed, opened = result.events
        assert consumed.code == "exploration.item.consumed"
        assert consumed.character_id == "character-0002", "the first holder in marching order pays"
        assert consumed.item_id == "toll_token"
        assert opened.code == "exploration.door.opened"
        assert carried(session.member("character-0002"), "toll_token") == 1, "the stack decremented"
        assert carried(session.member("character-0003"), "toll_token") == 1, "nobody else paid"

    def test_a_spent_instance_leaves_the_inventory_and_the_gate_then_refuses(self):
        session = door_gate_session(TOLL_GATE)
        session.execute(GrantItem(character_id="character-0001", item_id="toll_token"))
        assert session.execute(OpenDoor(direction=Direction.EAST)).accepted
        member = session.member("character-0001")
        assert not any(instance.template.id == "toll_token" for instance in member.inventory.items)
        assert session.execute(CloseDoor(direction=Direction.EAST)).accepted
        result = session.execute(OpenDoor(direction=Direction.EAST))
        assert result.rejections[0].code == "exploration.door.gate_refused"

    def test_every_success_consumes_again(self):
        session = door_gate_session(TOLL_GATE)
        session.execute(GrantItem(character_id="character-0001", item_id="toll_token", quantity=2))
        for _ in range(2):
            assert session.execute(OpenDoor(direction=Direction.EAST)).accepted
            assert session.execute(CloseDoor(direction=Direction.EAST)).accepted
        assert carried(session.member("character-0001"), "toll_token") == 0
        assert session.execute(OpenDoor(direction=Direction.EAST)).rejections[0].code == (
            "exploration.door.gate_refused"
        )

    def test_a_non_consuming_gate_keeps_reopening_for_free(self):
        session = door_gate_session(KEY_GATE)
        session.execute(GrantItem(character_id="character-0001", item_id="brass_key"))
        for _ in range(3):
            assert session.execute(OpenDoor(direction=Direction.EAST)).accepted
            assert session.execute(CloseDoor(direction=Direction.EAST)).accepted
        assert carried(session.member("character-0001"), "brass_key") == 1

    def test_a_magic_toll_reports_its_instance_id_and_never_its_template(self):
        gate = GateSpec(condition=HasItemCondition(item_id="potion_of_healing", consumes=True))
        session = door_gate_session(gate)
        member = session.member("character-0001")
        member.inventory.items.append(MagicItemInstance(instance_id="magic-item-0042", template_id="potion_of_healing"))
        result = session.execute(OpenDoor(direction=Direction.EAST))
        consumed = result.events[0]
        assert consumed.item_id == "magic-item-0042"
        assert "potion_of_healing" not in consumed.model_dump_json()
        assert member.inventory.magic_item("magic-item-0042") is None

    def test_a_ring_pays_the_toll_from_its_slot(self):
        gate = GateSpec(condition=HasItemCondition(item_id="ring_of_protection", consumes=True))
        session = door_gate_session(gate)
        member = session.member("character-0002")
        member.inventory.rings.append(
            MagicItemInstance(instance_id="magic-item-0043", template_id="ring_of_protection")
        )
        result = session.execute(OpenDoor(direction=Direction.EAST))
        assert result.accepted
        assert result.events[0].character_id == "character-0002"
        assert member.inventory.rings == []

    def test_a_refused_gate_takes_nothing(self):
        session = door_gate_session(TOLL_GATE, locked=True)
        session.execute(GrantItem(character_id="character-0001", item_id="toll_token"))
        assert session.execute(OpenDoor(direction=Direction.EAST)).rejections[0].code == "exploration.door.locked"
        assert carried(session.member("character-0001"), "toll_token") == 1

    def test_wedging_a_door_now_reports_the_spike(self):
        session = door_gate_session(None)
        session.execute(GrantItem(character_id="character-0001", item_id="iron_spikes", quantity=2))
        result = session.execute(WedgeDoor(direction=Direction.EAST))
        consumed, wedged = result.events
        assert consumed.code == "exploration.item.consumed"
        assert consumed.character_id == "character-0001"
        assert consumed.item_id == "iron_spikes"
        assert wedged.code == "exploration.door.wedged"
        assert carried(session.member("character-0001"), "iron_spikes") == 1

    def test_the_existing_consumers_still_consume(self):
        """The widened primitive under its old callers: provisions, ignition, gives, drops."""
        from osrlib.crawl.commands import DropItems, GiveItems, LightSource

        session = door_gate_session(None)
        session.execute(GrantItem(character_id="character-0001", item_id="torch", quantity=2))
        session.execute(GrantItem(character_id="character-0001", item_id="tinder_box"))
        session.execute(GrantItem(character_id="character-0001", item_id="rations_standard", quantity=7))
        for _ in range(20):
            result = session.execute(LightSource(character_id="character-0001", item_id="torch"))
            if any(event.code == "exploration.light.lit" for event in result.events):
                break
        member = session.member("character-0001")
        assert carried(member, "torch") == 1, "lighting a torch consumed one"
        assert session.execute(
            GiveItems(character_id="character-0001", recipient_id="character-0002", item_ids=("rations_standard",))
        ).accepted
        assert carried(member, "rations_standard") == 6
        assert carried(session.member("character-0002"), "rations_standard") == 1
        assert session.execute(DropItems(character_id="character-0001", item_ids=("rations_standard",))).accepted
        assert carried(member, "rations_standard") == 5
        events = exploration_module.consume_provisions(session)
        assert any(event.code == "exploration.provisions.consumed" for event in events)
        assert carried(member, "rations_standard") == 4


class TestGateModels:
    def test_the_union_parses_on_the_discriminator(self):
        gate = GateSpec.model_validate(
            {"condition": {"condition_type": "has_item", "item_id": "brass_key", "consumes": True}}
        )
        assert isinstance(gate.condition, HasItemCondition)
        assert gate.condition.consumes
        assert gate.narrative is None
        flag_gate = GateSpec.model_validate({"condition": {"condition_type": "flag_equals", "key": "k", "value": 3}})
        assert isinstance(flag_gate.condition, FlagEqualsCondition)
        effect_gate = GateSpec.model_validate({"condition": {"condition_type": "effect_active", "kind": "blessing"}})
        assert isinstance(effect_gate.condition, EffectActiveCondition)
        with pytest.raises(ValueError):
            GateSpec.model_validate({"condition": {"condition_type": "coin_toll", "amount": 10}})

    def test_the_narrative_block_round_trips(self):
        narrative = NarrativeBlock(
            refusal="The sentinel does not move.",
            success="The sentinel steps aside.",
            journal="The sentinel wants the brass key.",
            guidance="Play the sentinel as bored, not hostile.",
            speaker="the bronze sentinel",
        )
        gate = GateSpec(condition=HasItemCondition(item_id="brass_key"), narrative=narrative)
        assert GateSpec.model_validate(gate.model_dump(mode="json")) == gate
        assert NarrativeBlock().offer == ""

    def test_a_door_spec_without_a_gate_is_unchanged(self):
        from osrlib.crawl.dungeon import DoorSpec, TransitionSpec

        assert DoorSpec().requires is None
        assert DoorSpec.model_validate({"kind": "normal"}).requires is None
        transition = TransitionSpec.model_validate(
            {
                "kind": "stairs_down",
                "position": [0, 0],
                "to_dungeon_id": "warren",
                "to_level_number": 2,
                "to_position": [0, 0],
                "to_facing": "east",
            }
        )
        assert transition.requires is None

    def test_a_trap_effect_transition_may_not_gate(self):
        from osrlib.crawl.dungeon import TransitionSpec, TrapEffect

        chute = TransitionSpec(
            kind="chute",
            position=(0, 0),
            to_dungeon_id="warren",
            to_level_number=2,
            to_position=(0, 0),
            to_facing=Direction.EAST,
        )
        assert TrapEffect(transition=chute).transition is chute
        gated = chute.model_copy(update={"requires": GateSpec(condition=HasItemCondition(item_id="brass_key"))})
        with pytest.raises(ValueError, match="never gates"):
            TrapEffect(transition=gated)

    def test_a_pre_gate_document_and_save_load_with_the_field_absent(self):
        import json

        from osrlib.persistence import load_game, save_game, session_state

        session = gated_session()
        document = json.loads(json.dumps(save_game(session)))
        # A save written before this phase carries no `requires` and no `narrative`.
        for level in document["payload"]["adventure"]["dungeons"][0]["levels"]:
            for edge in level["edges"].values():
                if edge["door"] is not None:
                    del edge["door"]["requires"]
            for transition in level["transitions"]:
                del transition["requires"]
        restored = load_game(document)
        assert all(
            edge.door.requires is None
            for level in restored.adventure.dungeons[0].levels
            for edge in level.edges.values()
            if edge.door is not None
        )
        assert session_state(restored)["mode"] == session_state(session)["mode"]


class TestSuccessBeats:
    def test_the_door_event_and_the_transcript_carry_the_authored_line(self):
        from osrlib.messages import format_message

        session = door_gate_session(KEY_GATE)
        session.execute(GrantItem(character_id="character-0001", item_id="brass_key"))
        (opened,) = session.execute(OpenDoor(direction=Direction.EAST)).events
        assert opened.narrative == "The lock gives."
        assert format_message(opened) == "The door east of (0, 0) opens. The lock gives."

    def test_an_ungated_door_event_carries_no_beat(self):
        from osrlib.messages import format_message

        session = door_gate_session(None)
        (opened,) = session.execute(OpenDoor(direction=Direction.EAST)).events
        assert opened.narrative is None
        assert format_message(opened) == "The door east of (0, 0) opens."

    def test_a_gate_without_a_success_beat_leaves_the_field_empty(self):
        session = door_gate_session(TOLL_GATE)
        session.execute(GrantItem(character_id="character-0001", item_id="toll_token"))
        _, opened = session.execute(OpenDoor(direction=Direction.EAST)).events
        assert opened.narrative is None

    def test_the_crossing_event_carries_the_transitions_beat(self):
        from osrlib.messages import format_message

        session = gated_session()
        session.execute(
            PlaceParty(
                location=PartyLocation(
                    kind="dungeon", dungeon_id="warren", level_number=1, position=(3, 0), facing=Direction.EAST
                )
            )
        )
        session.execute(GrantItem(character_id="character-0001", item_id="toll_token"))
        result = session.execute(UseStairs())
        assert result.accepted
        consumed, entered = result.events[0], result.events[1]
        assert consumed.code == "exploration.item.consumed"
        assert entered.code == "exploration.location.entered"
        assert entered.narrative == "The ferryman pockets the token and the stair opens onto the dark."
        assert format_message(entered).endswith("The ferryman pockets the token and the stair opens onto the dark.")

    def test_a_referee_door_write_carries_no_beat(self):
        session = door_gate_session(KEY_GATE)
        result = session.execute(
            SetDoorState(dungeon_id="gate", level_number=1, x=0, y=0, direction=Direction.EAST, open=True)
        )
        (event,) = result.events
        assert event.narrative is None
        assert event.visibility.value == "referee"

    def test_the_player_view_never_carries_gate_wiring(self):
        from osrlib.core.events import Visibility

        session = gated_session()
        session.execute(MoveParty(direction=Direction.EAST))
        session.execute(OpenDoor(direction=Direction.EAST))  # refused, but the edge is now in view
        blob = session.view(Visibility.PLAYER).model_dump_json()
        for secret in ("requires", "condition_type", "brass_key", "guidance", "refusal", "sentinel"):
            assert secret not in blob
        assert "requires" in session.view(Visibility.REFEREE).model_dump_json()


class TestGateValidation:
    def _adventure_with(self, gate=None, transition_gate=None):
        level = LevelSpec(
            number=1,
            width=2,
            height=1,
            edges={"1,0:west": Edge(kind=EdgeKind.DOOR, door=DoorSpec(requires=gate))},
            transitions=(
                TransitionSpec(
                    kind="stairs_down",
                    position=(1, 0),
                    to_dungeon_id="gate",
                    to_level_number=1,
                    to_position=(0, 0),
                    to_facing=Direction.EAST,
                    requires=transition_gate,
                ),
            ),
            entrance=(0, 0),
        )
        return Adventure(
            name="One Door",
            town=TownSpec(name="Threshold"),
            dungeons=(DungeonSpec(id="gate", name="Gate", levels=(level,)),),
            items=(GATE_KEY,),
        )

    def validate(self, adventure) -> None:
        from osrlib.data import load_monsters

        validate_adventure(adventure, load_monsters(), load_equipment())

    def test_a_bundled_id_a_shipped_id_and_a_magic_id_all_resolve(self):
        self.validate(self._adventure_with(GateSpec(condition=HasItemCondition(item_id="brass_key"))))
        self.validate(self._adventure_with(GateSpec(condition=HasItemCondition(item_id="iron_spikes"))))
        self.validate(self._adventure_with(GateSpec(condition=HasItemCondition(item_id="ring_of_protection"))))

    def test_an_unknown_door_gate_item_fails_validation(self):
        adventure = self._adventure_with(GateSpec(condition=HasItemCondition(item_id="skeleton_key")))
        with pytest.raises(ContentValidationError, match="door 1,0:west gate references unknown item 'skeleton_key'"):
            self.validate(adventure)

    def test_an_unknown_transition_gate_item_fails_validation(self):
        adventure = self._adventure_with(transition_gate=GateSpec(condition=HasItemCondition(item_id="ferry_pass")))
        with pytest.raises(
            ContentValidationError, match=r"transition at \(1, 0\) gate references unknown item 'ferry_pass'"
        ):
            self.validate(adventure)

    def test_flag_and_effect_gates_are_never_reference_checked(self):
        self.validate(self._adventure_with(GateSpec(condition=FlagEqualsCondition(key="nobody_writes_me", value=1))))
        self.validate(self._adventure_with(GateSpec(condition=EffectActiveCondition(kind="nothing_attaches_me"))))

    def test_a_new_session_runs_the_same_gate(self):
        adventure = self._adventure_with(GateSpec(condition=HasItemCondition(item_id="skeleton_key")))
        with pytest.raises(ContentValidationError):
            GameSession.new(build_party(), adventure, seed=3)

    def test_the_shipped_gated_warren_validates(self):
        self.validate(build_gated_adventure())

    def test_the_content_pack_path_rejects_a_gated_trap_transition(self):
        from osrlib.crawl.content_pack import ContentPack

        document = {
            "sections": [
                {
                    "id": "s1",
                    "entries": [
                        {
                            "id": "e1",
                            "trap": {
                                "kind": "room",
                                "trigger": "enter",
                                "effect": {
                                    "transition": {
                                        "kind": "chute",
                                        "position": [0, 0],
                                        "to_dungeon_id": "d",
                                        "to_level_number": 2,
                                        "to_position": [0, 0],
                                        "to_facing": "east",
                                        "requires": {"condition": {"condition_type": "has_item", "item_id": "key"}},
                                    }
                                },
                            },
                        }
                    ],
                }
            ]
        }
        with pytest.raises(ValueError, match="never gates"):
            ContentPack.model_validate(document)
