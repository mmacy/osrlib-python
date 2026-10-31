"""Acceptance tests for `party.remove.carries_quest_item`, the `RemoveMember` refusal that keeps quest items in play.

In a playtest of a game built on osrlib, the player removed a member who carried the music
box the main quest needed, without `give_to`. The box left the game with the member, and the
quest could no longer be finished. osrlib knows which items an adventure depends on, so the
session refuses that removal, and every game gets the check.

The contract these tests state:

- `RemoveMember` without `give_to` is refused with `party.remove.carries_quest_item` when the
  leaving member carries an item the loaded adventure depends on. With `give_to`, the item
  moves to that member, so the command isn't refused.
- The adventure depends on an item when its catalog id is named by a `HasItemCondition` or an
  `ItemAcquiredPattern` anywhere in it: the gate of a door or a transition, a trigger's
  pattern or conditions, and a quest's activation, an objective's `when`, or an objective's
  `reveal_when`, pattern or conditions alike.
- Ids match the way `has_item` matches them: mundane, magic, and the adventure's own items,
  equipped or in the pack, and never a spent stack. The member can be alive, dead, turned to
  stone, or asleep. Another member carrying the same item doesn't lift the refusal.
- A quest whose status is `completed` stops protecting its items, and so does a trigger that
  has fired and isn't `repeatable`. A quest that is still open protects the items of every
  clause, including an objective already complete. A gate always protects its item.
- The check comes last, after every other `RemoveMember` refusal. Its params are the member's
  id as `character` and the catalog id of the first such item in `Inventory.all_instances`
  order as `item`.
- A refusal costs nothing: no draws, no time, no log entry, and no change.

The tests in `TestAllowed` state the removals the check must not refuse.
"""

from typing import Any

import pytest

from crawl_fixtures import GATE_KEY, build_party
from osrlib.core.items import MagicItemInstance
from osrlib.crawl.adventure import Adventure
from osrlib.crawl.commands import (
    ActivateQuest,
    CompleteObjective,
    CompleteQuest,
    EquipItem,
    GrantItem,
    MarkTriggerFired,
    RemoveMember,
    SessionMode,
)
from osrlib.crawl.dungeon import Direction, edge_key
from osrlib.crawl.gates import GateSpec, HasItemCondition
from osrlib.crawl.quests import ObjectiveSpec, QuestSpec, TriggerClause
from osrlib.crawl.session import GameSession
from osrlib.crawl.triggers import ItemAcquiredPattern, TownEnteredPattern, TriggerSpec
from test_add_member import SEED, frozen_state, member_ids, quiet_adventure, stream_states
from test_remove_member import LEAD, THIEF, granted_magic, put_in_state, remove

CODE = "party.remove.carries_quest_item"

QUEST = "errand"
"""The id of the quest each quest site builds."""

TRIGGER = "watch"
"""The id of the trigger each trigger site builds."""

SITES = (
    "door_gate",
    "stair_gate",
    "toll_gate",
    "trigger_pattern",
    "trigger_condition",
    "activation_pattern",
    "activation_condition",
    "objective_pattern",
    "objective_condition",
    "reveal_pattern",
    "reveal_condition",
)
"""Every place in an adventure where a `HasItemCondition` or an `ItemAcquiredPattern` can name an item.

A trap's transition can carry no gate, so it isn't a site.
"""


# ---------------------------------------------------------------------- fixtures


def _clause(item_id: str, kind: str) -> TriggerClause:
    """A clause that names `item_id` in its pattern or in its conditions."""
    if kind == "pattern":
        return TriggerClause(pattern=ItemAcquiredPattern(item_id=item_id))
    return TriggerClause(pattern=TownEnteredPattern(), conditions=(HasItemCondition(item_id=item_id),))


def _quest(site: str, item_id: str) -> QuestSpec:
    """A quest that names `item_id` at one of its clause sites."""
    home = ObjectiveSpec(id="home", name="Come home", when=TriggerClause(pattern=TownEnteredPattern()))
    place, kind = site.split("_")
    if place == "activation":
        return QuestSpec(id=QUEST, name="The errand", activation=_clause(item_id, kind), objectives=(home,))
    if place == "objective":
        find = ObjectiveSpec(id="find", name="Find it", when=_clause(item_id, kind))
        return QuestSpec(id=QUEST, name="The errand", objectives=(find, home))
    hidden = ObjectiveSpec(
        id="find",
        name="Find it",
        when=TriggerClause(pattern=TownEnteredPattern()),
        hidden=True,
        reveal_when=_clause(item_id, kind),
    )
    return QuestSpec(id=QUEST, name="The errand", objectives=(hidden, home))


def adventure_naming(*named: tuple[str, str], repeatable: bool = False) -> Adventure:
    """The quiet delve, with each `(site, item_id)` pair naming its item at that site.

    The adventure bundles the brass key, so an authored item can be named too. A trigger site
    builds one trigger, `repeatable` or not, and a quest site builds one quest. Each site can
    appear once.

    Args:
        named: The sites and the item each names.
        repeatable: Whether the trigger a trigger site builds is repeatable.
    """
    doc: dict[str, Any] = quiet_adventure().model_dump(mode="json")
    doc["items"] = [GATE_KEY.model_dump(mode="json")]
    level = doc["dungeons"][0]["levels"][0]
    triggers = []
    quests = []
    for site, item_id in named:
        if site == "door_gate":
            # The locked closet door south of (4, 1).
            gate = GateSpec(condition=HasItemCondition(item_id=item_id))
            level["edges"][edge_key((4, 1), Direction.SOUTH)]["door"]["requires"] = gate.model_dump(mode="json")
        elif site in ("stair_gate", "toll_gate"):
            # The stair down at (4, 1).
            condition = HasItemCondition(item_id=item_id, consumes=site == "toll_gate")
            level["transitions"][0]["requires"] = GateSpec(condition=condition).model_dump(mode="json")
        elif site.startswith("trigger_"):
            clause = _clause(item_id, site.removeprefix("trigger_"))
            trigger = TriggerSpec(id=TRIGGER, when=clause.pattern, conditions=clause.conditions, repeatable=repeatable)
            triggers.append(trigger.model_dump(mode="json"))
        else:
            quests.append(_quest(site, item_id).model_dump(mode="json"))
    doc["triggers"] = triggers
    doc["quests"] = quests
    return Adventure.model_validate(doc)


def session_naming(*named: tuple[str, str], repeatable: bool = False) -> GameSession:
    """A session in town with the stock party, playing `adventure_naming(*named)` with no listener."""
    return GameSession.new(build_party(), adventure_naming(*named, repeatable=repeatable), seed=SEED)


def grant(session: GameSession, character_id: str, item_id: str, *, equip: bool = False) -> None:
    """Give a member one of an item, and equip it when `equip` is set."""
    result = session.execute(GrantItem(character_id=character_id, item_id=item_id))
    assert result.accepted, result.rejections
    if equip:
        held = session.member(character_id).inventory.carried_item(item_id)
        assert held is not None
        target = held.instance_id if isinstance(held, MagicItemInstance) else item_id
        assert session.execute(EquipItem(character_id=character_id, item_id=target)).accepted
        assert held in session.member(character_id).inventory.equipped_instances()


def assert_guarded(session: GameSession, character_id: str, item_id: str) -> None:
    """Removing the member without `give_to` is refused for `item_id`, at no cost."""
    state = frozen_state(session)
    streams = stream_states(session)
    rounds = session.clock.rounds
    logged = len(session.command_log)
    members = member_ids(session)
    result = session.execute(RemoveMember(character_id=character_id))
    assert not result.accepted
    assert [(rejection.code, rejection.params) for rejection in result.rejections] == [
        (CODE, {"character": character_id, "item": item_id})
    ]
    assert list(result.events) == []
    assert stream_states(session) == streams, "a refusal draws nothing"
    assert session.clock.rounds == rounds, "a refusal spends no time"
    assert len(session.command_log) == logged, "a refusal is not logged"
    assert member_ids(session) == members, "a refusal removes nobody"
    assert frozen_state(session) == state, "a refusal changes nothing"


def assert_refused_with(session: GameSession, command: RemoveMember, code: str) -> None:
    """The command is refused with `code` alone."""
    result = session.execute(command)
    assert not result.accepted
    assert [rejection.code for rejection in result.rejections] == [code]


# ---------------------------------------------------------------------- the contract


class TestTheContract:
    def test_the_docstring_lists_the_code_last(self):
        doc = RemoveMember.__doc__ or ""
        rejections = doc[doc.index("Rejections:") : doc.index("Events:")]
        listed = [line.strip().split("`")[1] for line in rejections.splitlines() if line.strip().startswith("- `")]
        assert listed[-1] == CODE
        assert listed[:-1] == [
            "session.command.wrong_mode",
            "session.command.unknown_member",
            "party.remove.same_member",
            "session.command.member_incapacitated",
            "party.remove.last_standing",
        ]


# ---------------------------------------------------------------------- refused


class TestRefused:
    @pytest.mark.parametrize("site", SITES)
    def test_an_item_named_at_any_site_is_protected(self, site):
        session = session_naming((site, "holy_water"))
        grant(session, THIEF, "holy_water")
        assert_guarded(session, THIEF, "holy_water")

    @pytest.mark.parametrize(
        ("item_id", "equip"),
        [
            ("holy_water", False),
            ("sword", True),
            ("brass_key", False),
            ("potion_of_healing", False),
            ("ring_of_regeneration", True),
        ],
        ids=["mundane", "mundane-equipped", "authored", "magic", "magic-equipped"],
    )
    def test_every_kind_of_item_counts_equipped_or_not(self, item_id, equip):
        session = session_naming(("trigger_condition", item_id))
        grant(session, THIEF, item_id, equip=equip)
        assert_guarded(session, THIEF, item_id)

    @pytest.mark.parametrize("state", ["alive", "dead", "petrified", "asleep"])
    def test_a_member_in_any_state_is_protected(self, state):
        session = session_naming(("objective_condition", "holy_water"))
        grant(session, THIEF, "holy_water")
        put_in_state(session, THIEF, state)
        assert_guarded(session, THIEF, "holy_water")

    def test_another_member_s_copy_doesn_t_lift_the_refusal(self):
        session = session_naming(("door_gate", "holy_water"))
        grant(session, LEAD, "holy_water")
        grant(session, THIEF, "holy_water")
        assert_guarded(session, THIEF, "holy_water")

    def test_an_inactive_quest_protects_its_items(self):
        session = session_naming(("activation_pattern", "holy_water"))
        assert session.quests[QUEST].status == "inactive"
        grant(session, THIEF, "holy_water")
        assert_guarded(session, THIEF, "holy_water")

    def test_an_open_quest_protects_the_item_of_a_completed_objective(self):
        session = session_naming(("objective_pattern", "holy_water"))
        grant(session, THIEF, "holy_water")
        assert session.execute(CompleteObjective(quest_id=QUEST, objective_id="find")).accepted
        assert session.quests[QUEST].status == "active"
        assert_guarded(session, THIEF, "holy_water")

    def test_a_repeatable_trigger_that_has_fired_still_protects(self):
        session = session_naming(("trigger_condition", "holy_water"), repeatable=True)
        grant(session, THIEF, "holy_water")
        assert session.execute(MarkTriggerFired(trigger_id=TRIGGER)).accepted
        assert_guarded(session, THIEF, "holy_water")

    def test_a_gate_protects_after_a_quest_naming_the_same_item_is_completed(self):
        session = session_naming(("door_gate", "holy_water"), ("objective_condition", "holy_water"))
        grant(session, THIEF, "holy_water")
        assert session.execute(CompleteQuest(quest_id=QUEST)).accepted
        assert_guarded(session, THIEF, "holy_water")

    def test_an_open_quest_protects_after_a_fired_trigger_naming_the_same_item(self):
        session = session_naming(("trigger_pattern", "holy_water"), ("reveal_condition", "holy_water"))
        grant(session, THIEF, "holy_water")
        assert session.execute(MarkTriggerFired(trigger_id=TRIGGER)).accepted
        assert_guarded(session, THIEF, "holy_water")

    @pytest.mark.parametrize(
        ("first", "second"), [("holy_water", "potion_of_healing"), ("potion_of_healing", "holy_water")]
    )
    def test_the_rejection_names_the_first_item_in_the_pack(self, first, second):
        # The adventure names the potion first and the holy water second, so the order the
        # rejection follows is the inventory's, not the adventure's.
        session = session_naming(("trigger_condition", "potion_of_healing"), ("door_gate", "holy_water"))
        grant(session, THIEF, first)
        grant(session, THIEF, second)
        assert_guarded(session, THIEF, first)

    def test_the_pack_comes_before_what_is_equipped(self):
        session = session_naming(("trigger_condition", "sword"), ("door_gate", "holy_water"))
        grant(session, THIEF, "sword", equip=True)
        grant(session, THIEF, "holy_water")
        assert_guarded(session, THIEF, "holy_water")


# ---------------------------------------------------------------------- allowed


class TestAllowed:
    """Removals the check must not refuse. These pass before the check exists."""

    @pytest.mark.parametrize("state", ["alive", "dead"])
    def test_give_to_takes_the_item_and_the_member_leaves(self, state):
        session = session_naming(("door_gate", "holy_water"), ("objective_pattern", "brass_key"))
        grant(session, THIEF, "holy_water")
        grant(session, THIEF, "brass_key")
        put_in_state(session, THIEF, state)
        remove(session, THIEF, give_to=LEAD)
        assert THIEF not in member_ids(session)
        assert session.member(LEAD).inventory.carried_item("holy_water") is not None
        assert session.member(LEAD).inventory.carried_item("brass_key") is not None

    def test_a_member_carrying_nothing_the_adventure_names_leaves(self):
        session = session_naming(("door_gate", "holy_water"))
        grant(session, LEAD, "holy_water")
        grant(session, THIEF, "torch")
        remove(session, THIEF)
        assert THIEF not in member_ids(session)

    @pytest.mark.parametrize("site", [site for site in SITES if not site.endswith("_gate")])
    def test_a_completed_quest_or_a_spent_trigger_protects_nothing(self, site):
        session = session_naming((site, "holy_water"))
        grant(session, THIEF, "holy_water")
        if site.startswith("trigger_"):
            assert session.execute(MarkTriggerFired(trigger_id=TRIGGER)).accepted
        else:
            if session.quests[QUEST].status == "inactive":
                assert session.execute(ActivateQuest(quest_id=QUEST)).accepted
            assert session.execute(CompleteQuest(quest_id=QUEST)).accepted
            assert session.quests[QUEST].status == "completed"
        remove(session, THIEF)
        assert THIEF not in member_ids(session)

    def test_a_spent_stack_is_not_carried(self):
        session = session_naming(("trigger_condition", "potion_of_healing"))
        instance_id = granted_magic(session, THIEF, "potion_of_healing")
        spent = next(item for item in session.member(THIEF).inventory.items if item.instance_id == instance_id)
        assert isinstance(spent, MagicItemInstance)
        spent.quantity = 0
        remove(session, THIEF)
        assert THIEF not in member_ids(session)

    def test_the_mode_is_checked_first(self):
        session = session_naming(("door_gate", "holy_water"))
        grant(session, THIEF, "holy_water")
        session.mode = SessionMode.VICTORY
        assert_refused_with(session, RemoveMember(character_id=THIEF), "session.command.wrong_mode")

    def test_an_unknown_member_is_reported_first(self):
        session = session_naming(("door_gate", "holy_water"))
        grant(session, THIEF, "holy_water")
        assert_refused_with(session, RemoveMember(character_id="character-0099"), "session.command.unknown_member")

    def test_the_last_standing_member_is_reported_first(self):
        party = build_party((("Brakk", "fighter"),))
        session = GameSession.new(party, adventure_naming(("door_gate", "holy_water")), seed=SEED)
        grant(session, LEAD, "holy_water")
        assert_refused_with(session, RemoveMember(character_id=LEAD), "party.remove.last_standing")

    def test_an_incapacitated_give_to_is_reported_and_not_the_item(self):
        session = session_naming(("door_gate", "holy_water"))
        grant(session, THIEF, "holy_water")
        put_in_state(session, LEAD, "asleep")
        command = RemoveMember(character_id=THIEF, give_to=LEAD)
        assert_refused_with(session, command, "session.command.member_incapacitated")
