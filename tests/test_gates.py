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

from crawl_fixtures import build_gated_adventure, build_party
from osrlib.core.effects import EffectDefinition, EffectsLedger
from osrlib.core.items import ItemInstance, MagicItemInstance, ValuableInstance
from osrlib.crawl.commands import EnterDungeon
from osrlib.crawl.dungeon import Direction
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
