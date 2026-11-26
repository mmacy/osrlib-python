"""Session contract tests: rejection purity, modes, logs, listeners, records, views."""

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from crawl_fixtures import build_adventure, build_party
from osrlib.core.character import party_to_document
from osrlib.core.clock import ROUNDS_PER_DAY, TimeUnit
from osrlib.core.events import Visibility
from osrlib.core.items import Coins
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import (
    AddJournalEntry,
    AdvanceTime,
    AwardXP,
    CloseDoor,
    EnterDungeon,
    GrantCoins,
    GrantItem,
    LightSource,
    ListenAtDoor,
    MoveParty,
    OpenDoor,
    PlaceParty,
    RemoveTreasureTrap,
    ReorderParty,
    ResolveBattleRound,
    RollDice,
    SessionMode,
    SetDoorState,
    SetFlag,
    SpawnMonsters,
    TakeTreasure,
    Wait,
    WedgeDoor,
)
from osrlib.crawl.dungeon import (
    AreaSpec,
    Direction,
    DungeonSpec,
    Edge,
    EdgeKind,
    FeatureSpec,
    LevelSpec,
    PartyLocation,
    TrapEffect,
    TrapSpec,
    WanderingSpec,
    edge_key,
)
from osrlib.crawl.events import DiceRolledEvent, FlagSetEvent
from osrlib.crawl.session import GameSession
from osrlib.crawl.views import ExploredLevelView, PlayerView
from osrlib.errors import ContentValidationError
from osrlib.persistence import load_game, replay_game, save_game, session_state


def make_session(seed: int = 11) -> GameSession:
    return GameSession.new(build_party(), build_adventure(), seed=seed)


def stream_states(session) -> dict:
    return {key: state.model_dump() for key, state in session.streams.export_states().items()}


def outfit(session) -> None:
    session.execute(GrantItem(character_id="character-0001", item_id="torch", quantity=6))
    session.execute(GrantItem(character_id="character-0001", item_id="tinder_box"))


class TestNewSession:
    def test_ids_assign_in_party_order_with_the_pinned_prefix(self):
        session = make_session()
        assert [member.id for member in session.party.members] == [
            "character-0001",
            "character-0002",
            "character-0003",
            "character-0004",
        ]

    def test_existing_ids_are_kept(self):
        party = build_party()
        party.members[0].id = "character-0007"
        GameSession.new(party, build_adventure(), seed=1)
        assert party.members[0].id == "character-0007"
        assert party.members[1].id == "character-0001"

    def test_metadata_handshake(self):
        from osrlib.versioning import SCHEMA_VERSION, engine_version

        session = make_session()
        assert session.metadata == {"schema_version": SCHEMA_VERSION, "engine_version": engine_version()}

    def test_starts_in_town_at_round_zero(self):
        session = make_session()
        assert session.mode.value == "town"
        assert session.clock.rounds == 0
        assert session.dungeon_state.location == PartyLocation(kind="town")


class TestRejectionPurity:
    def test_rejected_command_consumes_no_draws_no_time_and_stays_out_of_the_log(self):
        session = make_session()
        outfit(session)
        before_streams = stream_states(session)
        before_rounds = session.clock.rounds
        before_log = len(session.command_log)
        before_events = len(session.event_log)
        # Wrong mode: MoveParty in town.
        result = session.execute(MoveParty(direction=Direction.NORTH))
        assert not result.accepted
        assert result.rejections[0].code == "session.command.wrong_mode"
        assert result.events == ()
        assert stream_states(session) == before_streams
        assert session.clock.rounds == before_rounds
        assert len(session.command_log) == before_log
        assert len(session.event_log) == before_events

    def test_in_fiction_rejection_is_equally_pure(self):
        session = make_session()
        outfit(session)
        session.execute(EnterDungeon(dungeon_id="delve"))
        before_streams = stream_states(session)
        before_rounds = session.clock.rounds
        result = session.execute(MoveParty(direction=Direction.NORTH))  # boundary wall
        assert not result.accepted
        assert result.rejections[0].code == "exploration.move.blocked"
        assert stream_states(session) == before_streams
        assert session.clock.rounds == before_rounds

    def test_accepted_commands_are_logged(self):
        session = make_session()
        result = session.execute(SetFlag(key="lever", value=True))
        assert result.accepted
        assert session.command_log[-1] == SetFlag(key="lever", value=True)
        assert session.event_log[-1] == result.events[-1]

    def test_a_rejection_leaves_the_entire_session_state_byte_identical(self):
        """The strongest form: the whole save payload, before and after a refusal."""
        from osrlib.persistence import session_state

        session = make_session()
        outfit(session)
        session.execute(EnterDungeon(dungeon_id="delve"))
        session.execute(MoveParty(direction=Direction.EAST))
        session.execute(MoveParty(direction=Direction.EAST))  # (2,0): the stuck door lies south
        before = json.loads(json.dumps(session_state(session)))
        refusals = [
            OpenDoor(direction=Direction.SOUTH),  # stuck
            CloseDoor(direction=Direction.SOUTH),  # already closed
            MoveParty(direction=Direction.SOUTH),  # blocked by the shut door
            MoveParty(direction=Direction.NORTH),  # the level boundary
            WedgeDoor(direction=Direction.EAST),  # no door there at all
            ListenAtDoor(character_id="character-0001", direction=Direction.NORTH),
        ]
        for command in refusals:
            result = session.execute(command)
            assert not result.accepted, command.command_type
        assert json.loads(json.dumps(session_state(session))) == before

    def test_a_refused_door_command_materializes_no_door_entry(self):
        session = make_session()
        outfit(session)
        session.execute(EnterDungeon(dungeon_id="delve"))
        session.execute(MoveParty(direction=Direction.EAST))
        session.execute(MoveParty(direction=Direction.EAST))
        assert not session.execute(OpenDoor(direction=Direction.SOUTH)).accepted
        assert not session.execute(MoveParty(direction=Direction.SOUTH)).accepted
        assert session.dungeon_state.doors == {}

    def test_the_secret_door_discovery_write_does_materialize(self):
        from osrlib.crawl import _doors
        from osrlib.crawl.dungeon import edge_ref

        session = make_session()
        session.execute(
            PlaceParty(
                location=PartyLocation(
                    kind="dungeon", dungeon_id="delve", level_number=1, position=(3, 1), facing=Direction.EAST
                )
            )
        )
        _doors._materialize_door(session, Direction.EAST).discovered = True
        ref = edge_ref("delve", 1, (3, 1), Direction.EAST)
        assert session.dungeon_state.doors[ref].discovered

    def test_a_referee_write_that_sets_nothing_stores_nothing(self):
        session = make_session()
        result = session.execute(SetDoorState(dungeon_id="delve", level_number=1, x=2, y=0, direction=Direction.SOUTH))
        assert result.accepted
        assert result.events == ()
        assert session.dungeon_state.doors == {}, "a write with nothing to write materializes nothing"
        # A door that does not exist still rejects, whatever the command sets.
        refused = session.execute(SetDoorState(dungeon_id="delve", level_number=1, x=0, y=0, direction=Direction.NORTH))
        assert not refused.accepted
        assert refused.rejections[0].code == "session.command.no_door"

    def test_a_referee_write_seeds_an_untouched_authored_open_door(self):
        from crawl_fixtures import build_open_door_adventure

        session = GameSession.new(build_party(), build_open_door_adventure(), seed=5)
        result = session.execute(
            SetDoorState(dungeon_id="vestibule", level_number=1, x=0, y=0, direction=Direction.EAST, wedged=True)
        )
        assert result.accepted
        stored = session.dungeon_state.doors["vestibule:1:1,0:west"]
        assert stored.wedged and stored.open, "the referee's write must not store an authored-open door shut"


class TestModes:
    def test_battle_command_rejected_outside_battle(self):
        session = make_session()
        result = session.execute(ResolveBattleRound(declarations=()))
        assert not result.accepted
        assert result.rejections[0].code == "session.command.wrong_mode"

    def test_encounter_command_rejected_while_exploring(self):
        session = make_session()
        session.execute(EnterDungeon(dungeon_id="delve"))
        result = session.execute(Wait())
        assert not result.accepted
        assert result.rejections[0].code == "session.command.wrong_mode"


class TestRefereeCommands:
    def test_grant_item_and_coins(self):
        session = make_session()
        result = session.execute(GrantItem(character_id="character-0001", item_id="torch", quantity=6))
        assert result.accepted
        member = session.member("character-0001")
        assert any(instance.template.id == "torch" and instance.quantity == 6 for instance in member.inventory.items)
        result = session.execute(GrantCoins(character_id="character-0001", coins=Coins(gp=50)))
        assert result.accepted
        assert member.inventory.purse.gp == 50

    def test_award_xp_levels_up(self):
        session = make_session()
        result = session.execute(AwardXP(character_id="character-0001", amount=2500))
        assert result.accepted
        awarded, leveled = result.events
        assert awarded.code == "session.xp.awarded"
        assert leveled.code == "session.level.gained"
        assert session.member("character-0001").level == 2
        assert awarded.level_after == 2

    def test_award_xp_emits_the_level_event_fields(self):
        from osrlib.data import load_classes

        session = make_session()
        result = session.execute(AwardXP(character_id="character-0001", amount=2500))
        leveled = result.events[1]
        assert leveled.character_id == "character-0001"
        assert leveled.level_before == 1
        assert leveled.level_after == 2
        assert leveled.hp_gained >= 1
        assert leveled.hp_roll is not None
        assert isinstance(leveled.con_applied, bool)
        assert leveled.title == load_classes().get("fighter").level_titles[1]

    def test_award_below_threshold_emits_no_level_event(self):
        session = make_session()
        result = session.execute(AwardXP(character_id="character-0001", amount=100))
        assert result.accepted
        (event,) = result.events
        assert event.code == "session.xp.awarded"
        assert session.member("character-0001").level == 1

    def test_award_xp_clamps_one_level_per_award(self):
        session = make_session()
        session.execute(AwardXP(character_id="character-0001", amount=100_000))
        member = session.member("character-0001")
        assert member.level == 2
        assert member.xp == 3999  # 1 shy of the fighter's level-3 threshold

    def test_set_flag_is_referee_visibility(self):
        session = make_session()
        result = session.execute(SetFlag(key="portcullis_open", value=True))
        (event,) = result.events
        assert isinstance(event, FlagSetEvent)
        assert event.visibility is Visibility.REFEREE
        assert session.flags["portcullis_open"] is True

    def test_set_door_state(self):
        session = make_session()
        result = session.execute(
            SetDoorState(dungeon_id="delve", level_number=1, x=2, y=0, direction=Direction.SOUTH, open=True)
        )
        assert result.accepted
        assert session.dungeon_state.doors["delve:1:2,1:north"].open

    def test_place_party(self):
        session = make_session()
        result = session.execute(
            PlaceParty(
                location=PartyLocation(
                    kind="dungeon", dungeon_id="delve", level_number=1, position=(0, 0), facing=Direction.EAST
                )
            )
        )
        assert result.accepted
        assert session.mode.value == "exploring"
        assert session.dungeon_state.is_explored("delve", 1, (0, 0))

    def test_advance_time(self):
        session = make_session()
        result = session.execute(AdvanceTime(n=2, unit=TimeUnit.TURN))
        assert result.accepted
        assert session.clock.rounds == 120
        assert result.events[-1].code == "session.time.advanced"

    def test_referee_time_consumes_provisions_at_day_boundaries(self):
        session = make_session()
        result = session.execute(AdvanceTime(n=1, unit=TimeUnit.DAY))
        assert session.clock.rounds == ROUNDS_PER_DAY
        codes = [event.code for event in result.events]
        # In town provisions consume but never run short.
        assert codes.count("exploration.provisions.consumed") == 8  # food + water for four members
        assert "exploration.provisions.short" not in codes


class TestRollDice:
    def test_accepted_and_logged_with_a_dice_rolled_event(self):
        session = make_session()
        result = session.execute(RollDice(expression="2d6"))
        assert result.accepted
        (event,) = result.events
        assert isinstance(event, DiceRolledEvent)
        assert event.code == "adjudication.dice_rolled"
        assert event.expression == "2d6"
        assert len(event.rolls) == 2
        assert all(1 <= die <= 6 for die in event.rolls)
        assert event.total == sum(event.rolls)
        # Accepted rolls join the reproducible trajectory.
        assert session.command_log[-1] == RollDice(expression="2d6")
        assert session.event_log[-1] == event

    def test_roll_is_referee_visibility(self):
        assert DiceRolledEvent.model_fields["visibility"].default is Visibility.REFEREE
        session = make_session()
        (event,) = session.execute(RollDice(expression="1d20")).events
        assert event.visibility is Visibility.REFEREE

    def test_modifier_and_multiplier_carry_through_to_the_total(self):
        session = make_session()
        (event,) = session.execute(RollDice(expression="2d6+1×10")).events
        assert event.total == (sum(event.rolls) + 1) * 10

    def test_malformed_expression_is_rejected_at_construction(self):
        # A bad expression fails validation before it can reach the session: no
        # CommandResult, no draw, no log entry — the same way SpawnMonsters guards
        # its count_dice. Validation stays a pure pre-phase.
        session = make_session()
        before_streams = stream_states(session)
        before_log = len(session.command_log)
        before_events = len(session.event_log)
        with pytest.raises(ValidationError):
            RollDice(expression="garbage")
        assert stream_states(session) == before_streams
        assert len(session.command_log) == before_log
        assert len(session.event_log) == before_events

    def test_same_seed_same_command_is_byte_identical(self):
        first = make_session(seed=7).execute(RollDice(expression="3d6")).events[0]
        second = make_session(seed=7).execute(RollDice(expression="3d6")).events[0]
        assert first.model_dump(mode="json") == second.model_dump(mode="json")

    def test_roll_touches_only_the_adjudication_stream(self):
        session = make_session()
        session.execute(EnterDungeon(dungeon_id="delve"))  # exercise the crawl streams first
        before = stream_states(session)
        session.execute(RollDice(expression="4d6"))
        after = stream_states(session)
        changed = {key for key in after if before.get(key) != after.get(key)}
        assert changed == {"adjudication"}

    def test_interleaved_roll_leaves_a_keyed_mechanic_unshifted(self):
        def spawn_events(interleave: bool) -> list:
            session = make_session(seed=99)
            session.execute(EnterDungeon(dungeon_id="delve"))
            if interleave:
                session.execute(RollDice(expression="3d6"))
            result = session.execute(SpawnMonsters(template_id="goblin", count_dice="2d4", distance_feet=60))
            assert result.accepted
            return [event.model_dump(mode="json") for event in result.events]

        # An interleaved adjudication roll must not shift the encounter stream's draws.
        assert spawn_events(interleave=False) == spawn_events(interleave=True)

    def test_legal_in_every_mode(self):
        for mode in SessionMode:
            session = make_session()
            session.mode = mode
            result = session.execute(RollDice(expression="1d6"))
            assert result.accepted, mode
            assert isinstance(result.events[0], DiceRolledEvent)


class TestReorder:
    def test_reorder_party_is_the_only_marching_order_mutation(self):
        session = make_session()
        result = session.execute(
            ReorderParty(order=("character-0004", "character-0003", "character-0002", "character-0001"))
        )
        assert result.accepted
        assert [member.id for member in session.party.members] == [
            "character-0004",
            "character-0003",
            "character-0002",
            "character-0001",
        ]

    def test_bad_order_rejects(self):
        session = make_session()
        result = session.execute(ReorderParty(order=("character-0001",)))
        assert not result.accepted


class TestListeners:
    def test_listener_events_append_to_result_and_log_with_state_snapshotted(self):
        class QuestTracker:
            key = "quest"

            def handle(self, events, state):
                count = state.get("flags_seen", 0)
                emitted = []
                for event in events:
                    if isinstance(event, FlagSetEvent):
                        count += 1
                        emitted.append(FlagSetEvent(key="quest_progress", value=count))
                return emitted, {"flags_seen": count}

        session = make_session()
        session.register_listener(QuestTracker())
        result = session.execute(SetFlag(key="lever", value=True))
        assert [event.key for event in result.events] == ["lever", "quest_progress"]
        assert session.listener_state["quest"] == {"flags_seen": 1}
        assert session.event_log[-1].key == "quest_progress"

    def test_listeners_run_in_registration_order_and_see_earlier_events(self):
        order = []

        class First:
            key = "first"

            def handle(self, events, state):
                order.append(("first", len(events)))
                return [FlagSetEvent(key="from_first", value=1)], state

        class Second:
            key = "second"

            def handle(self, events, state):
                order.append(("second", len(events)))
                return [], state

        session = make_session()
        session.register_listener(First())
        session.register_listener(Second())
        session.execute(SetFlag(key="lever", value=True))
        assert order == [("first", 1), ("second", 2)]

    def test_a_command_issuing_listeners_events_ride_the_result_in_log_order(self):
        class Portcullis:
            key = "portcullis"

            def __init__(self, session):
                self._session = session

            def handle(self, events, state):
                for event in events:
                    if isinstance(event, FlagSetEvent) and event.key == "lever":
                        self._session.execute(AddJournalEntry(text="Something grinds open below."))
                return [], state

        session = make_session()
        session.register_listener(Portcullis(session))
        result = session.execute(SetFlag(key="lever", value=True))
        codes = [event.code for event in result.events]
        assert codes == ["session.flag.set", "session.journal.entry_added"]
        # Logged once, and the envelope is the log's tail — the nested command's
        # event is not duplicated by riding the result.
        assert [getattr(event, "code", None) for event in session.event_log] == codes

    def test_a_nested_cascade_rides_the_result_once_each_in_log_order(self):
        class Cascade:
            key = "cascade"

            def __init__(self, session):
                self._session = session

            def handle(self, events, state):
                for event in events:
                    if isinstance(event, FlagSetEvent) and event.key.startswith("step") and event.key < "step3":
                        following = f"step{int(event.key[-1]) + 1}"
                        self._session.execute(SetFlag(key=following, value=True))
                return [], state

        session = make_session()
        session.register_listener(Cascade(session))
        result = session.execute(SetFlag(key="step1", value=True))
        assert [event.key for event in result.events] == ["step1", "step2", "step3"]
        assert [event.key for event in session.event_log] == ["step1", "step2", "step3"]

    def test_a_later_listener_sees_a_nested_commands_events_exactly_once(self):
        """The dispatch input and the result envelope are two different lists.

        A nested `execute` runs the whole listener loop itself, so the second
        listener already saw the nested command's events at the nested level. Handing
        them to it again at the outer level would double every reaction downstream of
        a command-issuing listener.
        """
        seen: list[str] = []

        class Relay:
            key = "relay"

            def __init__(self, session):
                self._session = session

            def handle(self, events, state):
                for event in events:
                    if isinstance(event, FlagSetEvent) and event.key == "lever":
                        self._session.execute(SetFlag(key="relayed", value=True))
                return [], state

        class Counter:
            key = "counter"

            def handle(self, events, state):
                seen.extend(event.key for event in events if isinstance(event, FlagSetEvent))
                return [], state

        session = make_session()
        session.register_listener(Relay(session))
        session.register_listener(Counter())
        result = session.execute(SetFlag(key="lever", value=True))
        # Two dispatches reached the counter: the nested command's, then the outer
        # one's — and the outer one carries no copy of the nested event.
        assert seen == ["relayed", "lever"]
        assert [event.key for event in result.events] == ["lever", "relayed"]
        assert [event.key for event in session.event_log] == ["lever", "relayed"]

    def test_a_repeatable_trigger_behind_a_relay_listener_fires_once(self):
        from osrlib.crawl.interpreter import Interpreter
        from osrlib.crawl.triggers import FlagSetPattern, TriggerSpec

        # The trigger watches the key the *relay* writes, so a nested event handed
        # back to the outer dispatch would match a second time.
        trigger = TriggerSpec(
            id="bell",
            when=FlagSetPattern(key="relayed"),
            repeatable=True,
            consequences=(SetFlag(key="rung", value=1),),
        )
        session = GameSession.new(build_party(), build_adventure().model_copy(update={"triggers": (trigger,)}), seed=11)

        class Relay:
            key = "relay"

            def __init__(self, session):
                self._session = session

            def handle(self, events, state):
                for event in events:
                    if isinstance(event, FlagSetEvent) and event.key == "relayed":
                        return [], state
                    if isinstance(event, FlagSetEvent) and event.key == "lever":
                        self._session.execute(SetFlag(key="relayed", value=True))
                return [], state

        session.register_listener(Relay(session))
        session.register_listener(Interpreter(session))
        session.execute(SetFlag(key="lever", value=True))
        marks = [command for command in session.command_log if command.command_type == "mark_trigger_fired"]
        assert len(marks) == 1, "one write of the lever key is one firing, relay or no relay"

    def test_an_emit_only_listener_is_unchanged_by_the_splice(self):
        class Echo:
            key = "echo"

            def handle(self, events, state):
                return [FlagSetEvent(key="echo", value=len(events))], state

        session = make_session()
        session.register_listener(Echo())
        result = session.execute(SetFlag(key="lever", value=True))
        assert [event.key for event in result.events] == ["lever", "echo"]
        assert [event.key for event in session.event_log] == ["lever", "echo"]

    def test_sight_persists_before_the_listeners_so_a_relocation_replays(self):
        from osrlib.crawl.commands import Command
        from osrlib.persistence import session_state

        teleport = PlaceParty(
            location=PartyLocation(
                kind="dungeon", dungeon_id="delve", level_number=2, position=(2, 0), facing=Direction.EAST
            )
        )

        class Teleporter:
            key = "teleporter"

            def __init__(self, session):
                self._session = session

            def handle(self, events, state):
                if any(getattr(event, "code", None) == "exploration.party.moved" for event in events):
                    self._session.execute(teleport)
                return [], state

        def outfitted(seed: int = 11) -> GameSession:
            session = make_session(seed)
            outfit(session)
            session.execute(EnterDungeon(dungeon_id="delve"))
            for _ in range(20):
                lit = session.execute(LightSource(character_id="character-0001", item_id="torch"))
                if any(event.code == "exploration.light.lit" for event in lit.events):
                    break
            return session

        live = outfitted()
        live.register_listener(Teleporter(live))
        live.execute(MoveParty(direction=Direction.EAST))

        replayed = outfitted()
        for command in list(live.command_log)[len(replayed.command_log) :]:
            assert isinstance(command, Command)
            replayed.execute(command)
        assert session_state(replayed)["dungeon_state"]["seen"] == session_state(live)["dungeon_state"]["seen"]


class TestDeathRecords:
    def test_poison_death_records_the_cause(self):
        from osrlib.core.events import DeathEvent, SavingThrowRolledEvent

        session = make_session()
        member = session.member("character-0001")
        events = [
            SavingThrowRolledEvent(
                code="combat.save.failed", target_id=member.id, category="death", roll=2, required=12
            ),
            DeathEvent(code="combat.death.died", target_id=member.id),
        ]
        session._record_deaths(events)
        record = session.death_records[member.id]
        assert record.cause == "poison"
        assert record.round == session.clock.rounds

    def test_damage_death_records_a_non_poison_cause(self):
        from osrlib.core.events import DamageDealtEvent, DeathEvent

        session = make_session()
        member = session.member("character-0001")
        events = [
            DamageDealtEvent(target_id=member.id, amount=9),
            DeathEvent(code="combat.death.died", target_id=member.id),
        ]
        session._record_deaths(events)
        assert session.death_records[member.id].cause == "damage"


class TestViews:
    def test_player_view_carries_the_whitelist(self):
        from crawl_fixtures import QUEST_ID, QUEST_NAME, QUEST_RECOVER, build_fetch_quest
        from osrlib.crawl.commands import ActivateQuest

        session = GameSession.new(
            build_party(),
            build_adventure(wandering_chance=0).model_copy(update={"quests": (build_fetch_quest(),)}),
            seed=11,
        )
        outfit(session)
        session.execute(EnterDungeon(dungeon_id="delve"))
        session.execute(ActivateQuest(quest_id=QUEST_ID))
        session.execute(AddJournalEntry(text="Down the stair, into the dark."))
        view = session.view(Visibility.PLAYER)
        assert view.party[0].current_hp == 6
        assert view.mode == "exploring"
        assert view.location.position == (0, 0)
        level_view = view.explored[0]
        assert (0, 0) in level_view.cells
        assert [entry.text for entry in view.journal][-1] == "Down the stair, into the dark."
        assert [(quest.id, quest.name) for quest in view.quests] == [(QUEST_ID, QUEST_NAME)]
        assert [objective.id for objective in view.quests[0].objectives] == [QUEST_RECOVER]

    def test_player_view_never_leaks_the_basics(self):
        session = make_session(seed=99)
        outfit(session)
        session.execute(SetFlag(key="secret_wiring", value=True))
        session.execute(EnterDungeon(dungeon_id="delve"))
        blob = session.view(Visibility.PLAYER).model_dump_json()
        assert "secret_wiring" not in blob
        assert str(session.master_seed) not in blob
        assert "trap" not in blob.lower() or "trap_ref" not in blob  # no trap specs
        # Unexplored geometry: room_a's cells are unexplored.
        assert "room_a" not in blob
        assert "chest" not in blob

    def test_undiscovered_secret_door_renders_as_wall(self):
        session = make_session()
        session.execute(
            PlaceParty(
                location=PartyLocation(
                    kind="dungeon", dungeon_id="delve", level_number=1, position=(3, 1), facing=Direction.EAST
                )
            )
        )
        view = session.view(Visibility.PLAYER)
        level_view = next(entry for entry in view.explored if entry.level_number == 1)
        assert level_view.edges["4,1:west"].kind == "wall"

    def test_referee_view_has_everything_but_rng(self):
        session = make_session()
        session.execute(SetFlag(key="secret_wiring", value=True))
        view = session.view(Visibility.REFEREE)
        assert view.flags == {"secret_wiring": True}
        dumped = view.model_dump(mode="json")
        assert "rng_streams" not in dumped
        assert "master_seed" not in dumped

    def test_monster_hp_never_in_player_view(self):
        session = make_session()
        outfit(session)
        session.execute(EnterDungeon(dungeon_id="delve"))
        from osrlib.crawl.commands import SpawnMonsters

        session.execute(SpawnMonsters(template_id="goblin", count_fixed=3, distance_feet=60))
        view = session.view(Visibility.PLAYER)
        assert view.encounter is not None
        group = view.encounter.groups[0]
        assert group.count == 3
        blob = view.model_dump_json()
        assert "current_hp" in blob  # the party's own
        goblins = [session.monsters[mid] for mid in session.encounter.groups[0].monster_ids]
        assert [member.model_dump(mode="json") for member in group.members] == [
            {"id": goblin.id, "status": "alive"} for goblin in goblins
        ]
        assert (
            not {
                "current_hp",
                "max_hp",
                "template",
                "conditions",
                "stat_modifiers",
                "inventory",
                "treasure",
            }
            & group.model_dump().keys()
        )


def feature_view_adventure() -> Adventure:
    """A map with known, unentered, trapped, and hidden features."""
    needle = TrapSpec(kind="treasure", trigger="open", effect=TrapEffect(damage_dice="1d4"))
    edges = {
        edge_key((0, 0), Direction.EAST): Edge(kind=EdgeKind.OPEN),
        edge_key((1, 0), Direction.EAST): Edge(kind=EdgeKind.OPEN),
        edge_key((2, 0), Direction.EAST): Edge(kind=EdgeKind.OPEN),
    }
    shrine = AreaSpec(
        id="shrine",
        name="The Ashen Shrine",
        description="A cold altar faces the corridor.",
        cells=((1, 0), (2, 0)),
        features=(
            FeatureSpec(
                id="area_cache",
                kind="treasure_cache",
                description="An offering bowl bound to the shrine.",
                coins=Coins(gp=11),
            ),
            FeatureSpec(
                id="trapped_cache",
                kind="treasure_cache",
                description="A brass-bound reliquary.",
                cell=(2, 0),
                coins=Coins(gp=23),
                trap=needle,
            ),
            FeatureSpec(
                id="turning_floor",
                kind="construction_trick",
                description="The flagstones turn the shrine around.",
            ),
        ),
    )
    level = LevelSpec(
        number=1,
        width=4,
        height=1,
        edges=edges,
        areas=(shrine,),
        features=(
            FeatureSpec(
                id="mural",
                kind="custom",
                description="A procession painted on the entry wall.",
                cell=(0, 0),
            ),
            FeatureSpec(
                id="seen_cache",
                kind="treasure_cache",
                description="A niche in the far wall.",
                cell=(3, 0),
                coins=Coins(gp=31),
            ),
        ),
        entrance=(0, 0),
        wandering=WanderingSpec(chance_in_six=0),
    )
    return Adventure(
        name="The Known World",
        town=TownSpec(name="Threshold", travel_turns={"vault": 1}),
        dungeons=(DungeonSpec(id="vault", name="The Vault", levels=(level,)),),
    )


def feature_view_session(*, player_handle_key: bytes | None = None) -> GameSession:
    session = GameSession.new(
        build_party(),
        feature_view_adventure(),
        seed=19,
        player_handle_key=player_handle_key,
    )
    assert session.execute(EnterDungeon(dungeon_id="vault")).accepted
    return session


def world_level(session: GameSession, dungeon_id: str = "vault", level_number: int = 1):
    return next(
        level
        for level in session.view(Visibility.PLAYER).explored
        if (level.dungeon_id, level.level_number) == (dungeon_id, level_number)
    )


class TestPlayerWorldKnowledge:
    def test_the_world_components_have_minimal_public_shapes(self):
        from osrlib.crawl import views

        assert views.EnteredAreaView.model_fields.keys() == {"id", "name", "description", "cells", "trap_state"}
        assert views.DungeonNameView.model_fields.keys() == {"id", "name"}
        assert views.TransitionView.model_fields.keys() == {"position", "kind", "side"}
        assert views.MapFeatureView.model_fields.keys() == {
            "id",
            "kind",
            "description",
            "cell",
            "area_id",
            "trap_state",
            "emptied",
        }
        for name in ("EnteredAreaView", "DungeonNameView", "MapFeatureView", "TransitionView"):
            assert name in views.__all__

    def test_only_entered_dungeons_and_walked_area_cells_are_projected(self):
        spare_level = LevelSpec(
            number=1,
            width=1,
            height=1,
            entrance=(0, 0),
            wandering=WanderingSpec(chance_in_six=0),
        )
        base = build_adventure(wandering_chance=0)
        adventure = base.model_copy(
            update={
                "dungeons": (
                    *base.dungeons,
                    DungeonSpec(id="annex", name="The Annex", levels=(spare_level,)),
                )
            }
        )
        session = GameSession.new(build_party(), adventure, seed=23)
        assert session.view(Visibility.PLAYER).dungeons == ()

        assert session.execute(EnterDungeon(dungeon_id="delve")).accepted
        session.dungeon_state.mark_seen("delve", 1, [(1, 1)])
        level = world_level(session, "delve")
        assert (1, 1) in level.cells
        assert level.areas == ()
        assert [(entry.id, entry.name) for entry in session.view(Visibility.PLAYER).dungeons] == [
            ("delve", "The Delve")
        ]

        for position in ((2, 1), (3, 2)):
            assert session.execute(
                PlaceParty(
                    location=PartyLocation(
                        kind="dungeon",
                        dungeon_id="delve",
                        level_number=1,
                        position=position,
                        facing=Direction.NORTH,
                    )
                )
            ).accepted
        level = world_level(session, "delve")
        assert [(area.id, area.name, area.description, area.cells) for area in level.areas] == [
            ("room_a", "Guard room", "Bones and bedrolls.", ((2, 1), (3, 2)))
        ]
        assert "pit_room" not in {area.id for area in level.areas}
        assert all(
            set(entry.model_dump(mode="json")) == {"id", "name"} for entry in session.view(Visibility.PLAYER).dungeons
        )

        assert session.execute(
            PlaceParty(
                location=PartyLocation(
                    kind="dungeon",
                    dungeon_id="annex",
                    level_number=1,
                    position=(0, 0),
                    facing=Direction.NORTH,
                )
            )
        ).accepted
        assert [(entry.id, entry.name) for entry in session.view(Visibility.PLAYER).dungeons] == [
            ("delve", "The Delve"),
            ("annex", "The Annex"),
        ]

    def test_overlapping_areas_use_the_level_owner_for_entry_and_features(self):
        owned = AreaSpec(
            id="owned",
            name="Owned room",
            description="The first-listed area owns the shared cell.",
            cells=((0, 0), (1, 0)),
        )
        later_cache = FeatureSpec(
            id="later_cache",
            kind="treasure_cache",
            description="A cache anywhere in the later room.",
            coins=Coins(gp=7),
        )
        shared_feature = FeatureSpec(
            id="shared_feature",
            kind="custom",
            description="A marker on the shared cell.",
            cell=(1, 0),
        )
        later = AreaSpec(
            id="later",
            name="Later room",
            description="The later area owns only its independent cell.",
            cells=((1, 0), (2, 0)),
            features=(later_cache,),
        )
        level = LevelSpec(
            number=1,
            width=3,
            height=1,
            entrance=(0, 0),
            edges={
                edge_key((0, 0), Direction.EAST): Edge(kind=EdgeKind.OPEN),
                edge_key((1, 0), Direction.EAST): Edge(kind=EdgeKind.OPEN),
            },
            areas=(owned, later),
            wandering=WanderingSpec(chance_in_six=0),
        )
        adventure = Adventure(
            name="Overlapping rooms",
            town=TownSpec(name="Threshold"),
            dungeons=(DungeonSpec(id="overlap", name="The Overlap", levels=(level,)),),
        )
        session = GameSession.new(build_party(), adventure, seed=17)
        document = save_game(session)
        document["payload"]["adventure"]["dungeons"][0]["levels"][0]["areas"][1]["features"].append(
            shared_feature.model_dump(mode="json")
        )
        session = load_game(document)
        assert session.execute(EnterDungeon(dungeon_id="overlap")).accepted
        assert session.execute(
            PlaceParty(
                location=PartyLocation(
                    kind="dungeon",
                    dungeon_id="overlap",
                    level_number=1,
                    position=(1, 0),
                    facing=Direction.EAST,
                )
            )
        ).accepted

        shared = world_level(session, "overlap")
        assert [(area.id, area.cells) for area in shared.areas] == [("owned", ((0, 0), (1, 0)))]
        assert later_cache.description not in shared.model_dump_json()
        assert shared_feature.description not in shared.model_dump_json()
        for target in ("later_cache", "shared_feature"):
            before = deepcopy(session_state(session))
            assert session.execute(TakeTreasure(feature_id=target)).rejections[0].code == (
                "exploration.feature.unknown"
            )
            assert session_state(session) == before

        assert session.execute(
            PlaceParty(
                location=PartyLocation(
                    kind="dungeon",
                    dungeon_id="overlap",
                    level_number=1,
                    position=(2, 0),
                    facing=Direction.EAST,
                )
            )
        ).accepted
        independent = world_level(session, "overlap")
        assert [(area.id, area.cells) for area in independent.areas] == [
            ("owned", ((0, 0), (1, 0))),
            ("later", ((2, 0),)),
        ]
        assert "A marker on the shared cell." not in independent.model_dump_json()
        later_cache = next(
            feature for feature in independent.features if feature.description == "A cache anywhere in the later room."
        )
        assert later_cache.area_id == "later"
        assert session.execute(TakeTreasure(feature_id=later_cache.id)).accepted

    def test_colons_in_dungeon_and_feature_ids_do_not_cross_resolve(self):
        first_dungeon_id = "vault:lower"
        first_feature_id = "cache"
        second_dungeon_id = "vault"
        second_feature_id = "upper:cache"
        first_ref = f"{first_dungeon_id}:1:{first_feature_id}"
        second_ref = f"{second_dungeon_id}:1:{second_feature_id}"
        assert first_ref != second_ref

        def dungeon(dungeon_id: str, feature_id: str, description: str) -> DungeonSpec:
            return DungeonSpec(
                id=dungeon_id,
                name=description,
                levels=(
                    LevelSpec(
                        number=1,
                        width=1,
                        height=1,
                        entrance=(0, 0),
                        features=(
                            FeatureSpec(
                                id=feature_id,
                                kind="treasure_cache",
                                description=description,
                                cell=(0, 0),
                                coins=Coins(gp=1),
                            ),
                        ),
                        wandering=WanderingSpec(chance_in_six=0),
                    ),
                ),
            )

        adventure = Adventure(
            name="Colon identities",
            town=TownSpec(name="Threshold"),
            dungeons=(
                dungeon(first_dungeon_id, first_feature_id, "First cache"),
                dungeon(second_dungeon_id, second_feature_id, "Second cache"),
            ),
        )
        session = GameSession.new(build_party(), adventure, seed=19)
        assert session.execute(EnterDungeon(dungeon_id=first_dungeon_id)).accepted
        first_handle = world_level(session, first_dungeon_id).features[0].id

        assert session.execute(
            PlaceParty(
                location=PartyLocation(
                    kind="dungeon",
                    dungeon_id=second_dungeon_id,
                    level_number=1,
                    position=(0, 0),
                    facing=Direction.NORTH,
                )
            )
        ).accepted
        second_handle = world_level(session, second_dungeon_id).features[0].id
        assert first_handle != second_handle
        before = deepcopy(session_state(session))
        stale = session.execute(TakeTreasure(feature_id=first_handle))
        assert [rejection.code for rejection in stale.rejections] == ["exploration.feature.unknown"]
        assert session_state(session) == before

        assert session.execute(
            PlaceParty(
                location=PartyLocation(
                    kind="dungeon",
                    dungeon_id=first_dungeon_id,
                    level_number=1,
                    position=(0, 0),
                    facing=Direction.NORTH,
                )
            )
        ).accepted
        before = deepcopy(session_state(session))
        stale = session.execute(TakeTreasure(feature_id=second_handle))
        assert [rejection.code for rejection in stale.rejections] == ["exploration.feature.unknown"]
        assert session_state(session) == before
        assert session.execute(TakeTreasure(feature_id=first_handle)).accepted

        second_session = GameSession.new(build_party(), adventure, seed=19)
        assert second_session.execute(EnterDungeon(dungeon_id=first_dungeon_id)).accepted
        assert second_session.execute(
            PlaceParty(
                location=PartyLocation(
                    kind="dungeon",
                    dungeon_id=second_dungeon_id,
                    level_number=1,
                    position=(0, 0),
                    facing=Direction.NORTH,
                )
            )
        ).accepted
        other_second_handle = world_level(second_session, second_dungeon_id).features[0].id
        assert other_second_handle == second_handle
        assert second_session.execute(TakeTreasure(feature_id=other_second_handle)).accepted

    def test_known_features_use_their_command_ids_and_safe_locations(self):
        session = feature_view_session()
        session.dungeon_state.mark_seen("vault", 1, [(2, 0), (3, 0)])

        level = world_level(session)
        features = {feature.description: feature for feature in level.features}
        assert set(features) == {
            "A procession painted on the entry wall.",
            "A niche in the far wall.",
            "A brass-bound reliquary.",
        }
        assert level.areas == ()
        mural = features["A procession painted on the entry wall."]
        trapped = features["A brass-bound reliquary."]
        assert mural.kind == "custom"
        assert mural.cell == (0, 0)
        assert mural.area_id is None
        assert trapped.cell == (2, 0)
        assert trapped.area_id is None
        assert trapped.trap_state is None
        assert all(feature.id.startswith("feature:") for feature in features.values())
        assert not {"mural", "seen_cache", "trapped_cache"} & {feature.id for feature in features.values()}

        assert session.execute(
            PlaceParty(
                location=PartyLocation(
                    kind="dungeon",
                    dungeon_id="vault",
                    level_number=1,
                    position=(1, 0),
                    facing=Direction.EAST,
                )
            )
        ).accepted
        level = world_level(session)
        features = {feature.description: feature for feature in level.features}
        assert len(level.areas) == 1
        area = level.areas[0]
        assert (area.id, area.name, area.description, area.cells) == (
            "shrine",
            "The Ashen Shrine",
            "A cold altar faces the corridor.",
            ((1, 0),),
        )
        assert features["An offering bowl bound to the shrine."].cell is None
        assert features["An offering bowl bound to the shrine."].area_id == "shrine"
        assert "The flagstones turn the shrine around." not in features
        for feature in features.values():
            assert set(feature.model_dump(mode="json")) == {
                "id",
                "kind",
                "description",
                "cell",
                "area_id",
                "trap_state",
                "emptied",
            }
            assert feature.id != "pile"

    def test_only_known_trap_and_construction_state_reaches_the_map(self):
        session = feature_view_session()
        assert session.execute(
            PlaceParty(
                location=PartyLocation(
                    kind="dungeon",
                    dungeon_id="vault",
                    level_number=1,
                    position=(1, 0),
                    facing=Direction.EAST,
                )
            )
        ).accepted
        feature_ref = "vault:1:trapped_cache"
        session.dungeon_state.mark_seen("vault", 1, [(2, 0)])
        session.dungeon_state.found_traps.append(feature_ref)
        found = {feature.description: feature for feature in world_level(session).features}
        assert found["A brass-bound reliquary."].trap_state == "found"
        assert "The flagstones turn the shrine around." not in found

        session.dungeon_state.sprung_traps.append(feature_ref)
        sprung = {feature.description: feature for feature in world_level(session).features}
        assert sprung["A brass-bound reliquary."].trap_state == "sprung"

        session.dungeon_state.removed_traps.append(feature_ref)
        removed = {feature.description: feature for feature in world_level(session).features}
        assert removed["A brass-bound reliquary."].trap_state == "removed"

        session.dungeon_state.found_tricks.append("vault:1:turning_floor")
        trick = next(
            feature
            for feature in world_level(session).features
            if feature.description == "The flagstones turn the shrine around."
        )
        assert trick.kind == "construction_trick"
        assert trick.description == "The flagstones turn the shrine around."
        assert (trick.cell, trick.area_id) == (None, "shrine")
        blob = trick.model_dump_json()
        assert all(secret not in blob for secret in ("damage_dice", "effect", "trigger", "coins", "magic_items"))

    def test_authored_feature_ids_are_the_existing_command_targets(self):
        session = feature_view_session()
        assert session.execute(
            PlaceParty(
                location=PartyLocation(
                    kind="dungeon",
                    dungeon_id="vault",
                    level_number=1,
                    position=(1, 0),
                    facing=Direction.EAST,
                )
            )
        ).accepted
        public = next(
            feature
            for feature in world_level(session).features
            if feature.description == "An offering bowl bound to the shrine."
        )
        assert "vault:1:area_cache" not in session.view(Visibility.PLAYER).emptied_caches
        assert "area_cache" not in {feature.id for feature in world_level(session).features}
        result = session.execute(TakeTreasure(feature_id="area_cache"))
        assert result.accepted, [rejection.code for rejection in result.rejections]
        assert "vault:1:area_cache" in session.dungeon_state.emptied_caches
        assert "vault:1:area_cache" in session.view(Visibility.PLAYER).emptied_caches
        assert "area_cache" not in {feature.id for feature in world_level(session).features}
        assert public.id in {feature.id for feature in world_level(session).features}

    def test_projecting_world_knowledge_is_read_only_and_deterministic(self):
        session = feature_view_session()
        session.dungeon_state.mark_seen("vault", 1, [(2, 0), (3, 0)])
        before = deepcopy(session_state(session))
        first = session.view(Visibility.PLAYER)
        second = session.view(Visibility.PLAYER)
        assert first == second
        assert first.model_dump_json() == second.model_dump_json()
        assert session_state(session) == before

    def test_remove_treasure_trap_does_not_reveal_an_unfound_trap(self):
        plain = feature_view_session()
        assert plain.execute(
            PlaceParty(
                location=PartyLocation(
                    kind="dungeon",
                    dungeon_id="vault",
                    level_number=1,
                    position=(1, 0),
                    facing=Direction.EAST,
                )
            )
        ).accepted
        plain_before = deepcopy(session_state(plain))
        plain_result = plain.execute(RemoveTreasureTrap(character_id="character-0002", feature_id="area_cache"))
        assert [rejection.code for rejection in plain_result.rejections] == ["exploration.feature.unknown"]
        assert session_state(plain) == plain_before

        hidden = feature_view_session()
        assert hidden.execute(
            PlaceParty(
                location=PartyLocation(
                    kind="dungeon",
                    dungeon_id="vault",
                    level_number=1,
                    position=(2, 0),
                    facing=Direction.EAST,
                )
            )
        ).accepted
        hidden_before = deepcopy(session_state(hidden))
        hidden_result = hidden.execute(RemoveTreasureTrap(character_id="character-0002", feature_id="trapped_cache"))
        assert [rejection.code for rejection in hidden_result.rejections] == [
            rejection.code for rejection in plain_result.rejections
        ]
        assert session_state(hidden) == hidden_before

    def test_explicit_player_handle_key_round_trips_and_replays_with_handles(self):
        key = bytes(range(32))
        party = build_party()
        starting_party = party_to_document(party.members)
        session = GameSession.new(
            party,
            feature_view_adventure(),
            seed=19,
            player_handle_key=key,
        )
        assert session.execute(EnterDungeon(dungeon_id="vault")).accepted
        assert session.execute(
            PlaceParty(
                location=PartyLocation(
                    kind="dungeon",
                    dungeon_id="vault",
                    level_number=1,
                    position=(1, 0),
                    facing=Direction.EAST,
                )
            )
        ).accepted
        handle = next(
            feature
            for feature in world_level(session).features
            if feature.description == "An offering bowl bound to the shrine."
        ).id
        assert session.execute(TakeTreasure(feature_id=handle)).accepted

        document = json.loads(json.dumps(save_game(session)))
        assert document["schema_version"] == 4
        assert document["payload"]["player_handle_key"] == key.hex()
        restored = load_game(document)
        assert session_state(restored) == session_state(session)
        assert restored.view(Visibility.PLAYER) == session.view(Visibility.PLAYER)

        replayed = replay_game(
            19,
            starting_party,
            feature_view_adventure(),
            session.ruleset,
            session.command_log,
            player_handle_key=key,
        )
        assert session_state(replayed) == session_state(session)
        with pytest.raises(ContentValidationError, match="replayed command 'take_treasure' was rejected"):
            replay_game(
                19,
                starting_party,
                feature_view_adventure(),
                session.ruleset,
                session.command_log,
            )

        player_blob = session.view(Visibility.PLAYER).model_dump_json()
        referee_blob = session.view(Visibility.REFEREE).model_dump_json()
        assert key.hex() not in player_blob
        assert key.hex() not in referee_blob
        assert "player_handle_key" not in player_blob
        assert "player_handle_key" not in referee_blob
        command_blob = json.dumps([command.model_dump(mode="json") for command in session.command_log])
        event_blob = json.dumps(
            [event if isinstance(event, dict) else event.model_dump(mode="json") for event in session.event_log]
        )
        assert key.hex() not in command_blob
        assert key.hex() not in event_blob

    def test_player_handle_keys_are_private_and_validate_their_length(self):
        first = feature_view_session()
        second = feature_view_session()
        first_handle = next(feature.id for feature in world_level(first).features if feature.cell == (0, 0))
        second_handle = next(feature.id for feature in world_level(second).features if feature.cell == (0, 0))
        assert first_handle == second_handle

        with pytest.raises(ValueError, match="at least 32 bytes"):
            feature_view_session(player_handle_key=b"x" * 31)

        a = feature_view_session(player_handle_key=b"a" * 32)
        b = feature_view_session(player_handle_key=b"b" * 32)
        a_handle = next(feature.id for feature in world_level(a).features if feature.cell == (0, 0))
        b_handle = next(feature.id for feature in world_level(b).features if feature.cell == (0, 0))
        assert a_handle != b_handle

    def test_world_projection_is_equal_after_save_and_load(self):
        session = feature_view_session()
        session.dungeon_state.mark_seen("vault", 1, [(2, 0), (3, 0)])
        session.dungeon_state.found_tricks.append("vault:1:turning_floor")
        expected = session.view(Visibility.PLAYER)
        restored = load_game(json.loads(json.dumps(save_game(session))))
        actual = restored.view(Visibility.PLAYER)
        assert actual == expected
        expected_features = {feature.id: feature for feature in world_level(session).features}
        actual_features = {feature.id: feature for feature in world_level(restored).features}
        assert list(actual_features) == list(expected_features)
        assert actual_features == expected_features

    def test_old_player_view_documents_load_with_world_defaults(self):
        session = feature_view_session()
        document = session.view(Visibility.PLAYER).model_dump(mode="json")
        document.pop("dungeons", None)
        document.pop("can_travel_to_town", None)
        for level in document["explored"]:
            level.pop("areas", None)
            level.pop("features", None)
            level.pop("transitions", None)
            level.pop("entrance", None)

        restored = PlayerView.model_validate(document)
        assert restored.dungeons == ()
        assert restored.can_travel_to_town is False
        for level in restored.explored:
            assert level.areas == ()
            assert level.features == ()
            assert level.transitions == ()
            assert level.entrance is None

        legacy_level = ExploredLevelView(dungeon_id="vault", level_number=1, cells=(), edges={})
        assert legacy_level.areas == ()
        assert legacy_level.features == ()
        assert legacy_level.transitions == ()
        assert legacy_level.entrance is None


class TestSpawnMonstersCommand:
    def test_spawn_opens_an_encounter(self):
        session = make_session()
        outfit(session)
        session.execute(EnterDungeon(dungeon_id="delve"))
        from osrlib.crawl.commands import SpawnMonsters

        result = session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=40))
        assert result.accepted
        codes = [event.code for event in result.events]
        assert "session.monsters.spawned" in codes
        assert "encounter.started" in codes
        assert session.mode.value in ("encounter", "battle")
        assert len(session.monsters) == 2

    def test_unknown_template_rejects(self):
        session = make_session()
        from osrlib.crawl.commands import SpawnMonsters

        result = session.execute(SpawnMonsters(template_id="gazebo", count_fixed=1, distance_feet=30))
        assert not result.accepted


class TestTownGuards:
    def test_spawn_monsters_rejects_in_town(self):
        # The fuzz contract caught this: an encounter needs a dungeon cell for
        # the combat space, so a town spawn rejects instead of crashing.
        session = make_session()
        from osrlib.crawl.commands import SpawnMonsters

        result = session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=30))
        assert not result.accepted
        assert result.rejections[0].code == "session.command.wrong_mode"

    def test_place_party_rejects_mid_encounter(self):
        session = make_session()
        outfit(session)
        session.execute(EnterDungeon(dungeon_id="delve"))
        from osrlib.crawl.commands import SpawnMonsters

        session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=30))
        assert session.encounter is not None
        result = session.execute(PlaceParty(location=PartyLocation(kind="town")))
        assert not result.accepted
        assert result.rejections[0].code == "session.command.wrong_mode"


class TestAcquisitionOrigin:
    def test_grants_are_grants_and_purchases_are_purchases(self):
        from osrlib.crawl.commands import PurchaseEquipment

        session = make_session()
        item = session.execute(GrantItem(character_id="character-0001", item_id="torch", quantity=2)).events[0]
        coins = session.execute(GrantCoins(character_id="character-0001", coins=Coins(gp=50))).events[0]
        bought = session.execute(PurchaseEquipment(character_id="character-0001", item_ids=("torch",))).events[0]
        assert (item.code, coins.code, bought.code) == ("exploration.item.acquired",) * 3
        assert (item.origin, coins.origin, bought.origin) == ("grant", "grant", "purchase")
