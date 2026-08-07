"""The quest document surface, the lifecycle commands, and the state they advance.

`TestTriggerClause`, `TestObjectiveSpec`, and `TestQuestSpec` pin what an authored
document may say — the clause reusing the trigger vocabulary, and the four things a
quest may never carry (a consuming condition, a reveal clause on a visible objective,
no objectives at all, a hand-written `source` on a reward). `TestSeeding` pins the
block a session is born with. The lifecycle classes walk the four commands: every
guard and its rejection, the journal beats they append, the events they emit, the
victory transition and its stickiness, and the persistence of the whole block.
"""

import json

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from crawl_fixtures import build_adventure, build_party
from osrlib.core.events import Visibility
from osrlib.crawl.adventure import Adventure
from osrlib.crawl.commands import (
    ActivateQuest,
    AddJournalEntry,
    AdvanceTime,
    AwardXP,
    Command,
    CompleteObjective,
    CompleteQuest,
    EnterDungeon,
    RevealObjective,
    SessionMode,
    SpawnMonsters,
)
from osrlib.crawl.events import (
    AdventureCompletedEvent,
    JournalEntryAddedEvent,
    ObjectiveCompletedEvent,
    ObjectiveRevealedEvent,
    QuestActivatedEvent,
    QuestCompletedEvent,
)
from osrlib.crawl.gates import HasItemCondition
from osrlib.crawl.narrative import NarrativeBlock
from osrlib.crawl.quests import ObjectiveSpec, QuestSpec, TriggerClause
from osrlib.crawl.session import GameSession, JournalEntry, ObjectiveState, QuestState
from osrlib.crawl.triggers import (
    PARTY_SELECTOR,
    AreaEnteredPattern,
    DungeonEnteredPattern,
    ItemAcquiredPattern,
    TownEnteredPattern,
)
from osrlib.persistence import load_game, save_game, session_state
from test_crawl_properties import command_strategy

QUEST_ID = "the-idol"
RECOVER = "recover-idol"
RETURN = "return-home"

OFFER = "Sister Halda wants the idol back before the new moon."
COMPLETION = "The idol sits on the altar where it began."
RECOVER_PROGRESS = "The idol is lighter than it looks."
RETURN_OFFER = "And then there is the matter of walking out alive."
RETURN_PROGRESS = "Threshold's gate closes behind you, the idol in the pack."

TERMINAL_MODES = (SessionMode.GAME_OVER, SessionMode.VICTORY)


def build_quest(**overrides) -> QuestSpec:
    """The fetch quest the lifecycle tests drive: one visible objective, one hidden."""
    quest = QuestSpec(
        id=QUEST_ID,
        name="The Jade Idol",
        activation=TriggerClause(pattern=DungeonEnteredPattern(dungeon_id="delve")),
        objectives=(
            ObjectiveSpec(
                id=RECOVER,
                when=TriggerClause(pattern=ItemAcquiredPattern(item_id="holy_water")),
                narrative=NarrativeBlock(progress=RECOVER_PROGRESS),
            ),
            ObjectiveSpec(
                id=RETURN,
                when=TriggerClause(
                    pattern=TownEnteredPattern(),
                    conditions=(HasItemCondition(item_id="holy_water"),),
                ),
                hidden=True,
                reveal_when=TriggerClause(
                    pattern=AreaEnteredPattern(dungeon_id="delve", level_number=1, area_id="room_a")
                ),
                narrative=NarrativeBlock(offer=RETURN_OFFER, progress=RETURN_PROGRESS),
            ),
        ),
        rewards=(AwardXP(character_id=PARTY_SELECTOR, amount=200),),
        narrative=NarrativeBlock(offer=OFFER, completion=COMPLETION),
    )
    return quest.model_copy(update=overrides) if overrides else quest


def with_quests(*quests: QuestSpec) -> Adventure:
    """The shared test delve with authored quests bolted on."""
    return build_adventure(wandering_chance=0).model_copy(update={"quests": quests})


def make_session(*quests: QuestSpec, seed: int = 19) -> GameSession:
    return GameSession.new(build_party(), with_quests(*quests), seed=seed)


def active_session(quest: QuestSpec | None = None, *, seed: int = 19) -> GameSession:
    """A session whose quest is active, activated the ordinary way."""
    session = make_session(quest if quest is not None else build_quest(), seed=seed)
    assert session.execute(ActivateQuest(quest_id=QUEST_ID)).accepted
    return session


def run(session: GameSession, *commands: Command) -> GameSession:
    for command in commands:
        result = session.execute(command)
        assert result.accepted, [rejection.code for rejection in result.rejections]
    return session


class TestTriggerClause:
    def test_a_clause_round_trips_with_its_pattern_and_conditions(self):
        clause = TriggerClause(
            pattern=TownEnteredPattern(),
            conditions=(HasItemCondition(item_id="holy_water"),),
        )
        assert TriggerClause.model_validate(clause.model_dump(mode="json")) == clause

    def test_conditions_default_to_the_bare_pattern(self):
        assert TriggerClause(pattern=TownEnteredPattern()).conditions == ()

    def test_a_consuming_condition_is_rejected_at_parse(self):
        with pytest.raises(ValidationError, match="cannot consume"):
            TriggerClause(
                pattern=TownEnteredPattern(),
                conditions=(HasItemCondition(item_id="toll_token", consumes=True),),
            )

    def test_the_clause_is_frozen(self):
        clause = TriggerClause(pattern=TownEnteredPattern())
        with pytest.raises(ValidationError):
            clause.conditions = ()


class TestObjectiveSpec:
    def test_an_objective_round_trips(self):
        objective = build_quest().objectives[1]
        assert ObjectiveSpec.model_validate(objective.model_dump(mode="json")) == objective

    def test_defaults_are_the_visible_shape(self):
        objective = ObjectiveSpec(id="recover", when=TriggerClause(pattern=TownEnteredPattern()))
        assert not objective.hidden
        assert objective.reveal_when is None
        assert objective.narrative is None

    def test_a_hidden_objective_needs_no_reveal_clause(self):
        objective = ObjectiveSpec(id="secret", when=TriggerClause(pattern=TownEnteredPattern()), hidden=True)
        assert objective.hidden and objective.reveal_when is None

    def test_a_reveal_clause_on_a_visible_objective_is_rejected_at_parse(self):
        with pytest.raises(ValidationError, match="not hidden"):
            ObjectiveSpec(
                id="recover",
                when=TriggerClause(pattern=TownEnteredPattern()),
                reveal_when=TriggerClause(pattern=DungeonEnteredPattern(dungeon_id="delve")),
            )

    def test_an_empty_id_is_rejected(self):
        with pytest.raises(ValidationError):
            ObjectiveSpec(id="", when=TriggerClause(pattern=TownEnteredPattern()))


class TestQuestSpec:
    def test_a_full_spec_round_trips(self):
        quest = build_quest()
        assert QuestSpec.model_validate(quest.model_dump(mode="json")) == quest

    def test_defaults_are_the_common_shape(self):
        quest = QuestSpec(
            id="errand",
            name="An Errand",
            objectives=(ObjectiveSpec(id="do-it", when=TriggerClause(pattern=TownEnteredPattern())),),
        )
        assert quest.activation is None
        assert quest.rewards == ()
        assert quest.completion == "all"
        assert not quest.concludes_adventure
        assert quest.narrative is None

    def test_a_quest_with_no_objectives_is_rejected_at_parse(self):
        with pytest.raises(ValidationError):
            QuestSpec(id="empty", name="Empty", objectives=())

    def test_duplicate_objective_ids_within_a_quest_are_rejected_at_parse(self):
        with pytest.raises(ValidationError, match="objective ids must be unique"):
            QuestSpec(
                id="twice",
                name="Twice",
                objectives=(
                    ObjectiveSpec(id="return", when=TriggerClause(pattern=TownEnteredPattern())),
                    ObjectiveSpec(id="return", when=TriggerClause(pattern=DungeonEnteredPattern(dungeon_id="delve"))),
                ),
            )

    def test_objective_ids_are_quest_scoped_so_two_quests_may_share_one(self):
        objective = ObjectiveSpec(id="return", when=TriggerClause(pattern=TownEnteredPattern()))
        first = QuestSpec(id="one", name="One", objectives=(objective,))
        second = QuestSpec(id="two", name="Two", objectives=(objective,))
        assert first.objectives[0].id == second.objectives[0].id == "return"

    def test_an_authored_source_on_a_reward_is_rejected_at_parse(self):
        with pytest.raises(ValidationError, match="carries a source"):
            QuestSpec(
                id="paid",
                name="Paid",
                objectives=(ObjectiveSpec(id="do-it", when=TriggerClause(pattern=TownEnteredPattern())),),
                rewards=(AwardXP(character_id=PARTY_SELECTOR, amount=10, source="quest:someone-else"),),
            )

    @pytest.mark.parametrize(
        "payload",
        [
            AddJournalEntry(text="The idol is lighter than it looks.").model_dump(mode="json"),
            ActivateQuest(quest_id="the-idol").model_dump(mode="json"),
            {"command_type": "cast_wish"},
        ],
        ids=["journal", "lifecycle", "unknown"],
    )
    def test_a_reward_outside_the_consequence_surface_does_not_parse(self, payload):
        with pytest.raises(ValidationError):
            QuestSpec.model_validate(
                {
                    "id": "paid",
                    "name": "Paid",
                    "objectives": [{"id": "do-it", "when": {"pattern": {"pattern_type": "town_entered"}}}],
                    "rewards": [payload],
                }
            )

    def test_the_spec_is_frozen(self):
        quest = build_quest()
        with pytest.raises(ValidationError):
            quest.concludes_adventure = True

    def test_a_document_written_before_quests_parses_with_none(self):
        payload = build_adventure(wandering_chance=0).model_dump(mode="json")
        payload.pop("quests")
        assert Adventure.model_validate(payload).quests == ()


class TestSeeding:
    def test_an_adventure_that_authors_nothing_seeds_an_empty_block(self):
        session = make_session()
        assert session.quests == {}

    def test_a_quest_with_an_activation_clause_starts_inactive(self):
        session = make_session(build_quest())
        assert session.quests[QUEST_ID].status == "inactive"

    def test_a_quest_with_no_activation_clause_is_active_from_round_zero(self):
        session = make_session(build_quest(activation=None))
        assert session.quests[QUEST_ID].status == "active"
        assert session.clock.rounds == 0
        assert session.journal == [], "a standing charge has no activation beat to journal"
        assert not session.event_log

    def test_objectives_seed_visible_unless_authored_hidden_in_document_order(self):
        session = make_session(build_quest())
        assert list(session.quests[QUEST_ID].objectives) == [RECOVER, RETURN]
        assert session.quests[QUEST_ID].objectives[RECOVER] == ObjectiveState(revealed=True, complete=False)
        assert session.quests[QUEST_ID].objectives[RETURN] == ObjectiveState(revealed=False, complete=False)

    def test_quests_seed_in_document_order(self):
        first = build_quest(id="first")
        second = build_quest(id="second")
        session = make_session(first, second)
        assert list(session.quests) == ["first", "second"]

    def test_the_accessor_resolves_the_closed_domain(self):
        adventure = with_quests(build_quest())
        assert adventure.quest(QUEST_ID).name == "The Jade Idol"
        with pytest.raises(ValueError, match="unknown quest id"):
            adventure.quest("no-such-quest")


class TestActivationGuards:
    def test_an_unknown_quest_is_rejected_and_never_logged(self):
        session = make_session(build_quest())
        result = session.execute(ActivateQuest(quest_id="no-such-quest"))
        assert not result.accepted
        assert result.rejections[0].code == "session.command.unknown_quest"
        assert result.rejections[0].params == {"quest": "no-such-quest"}
        assert not session.command_log

    def test_activating_an_active_quest_is_refused_by_its_own_state(self):
        session = active_session()
        logged = len(session.command_log)
        result = session.execute(ActivateQuest(quest_id=QUEST_ID))
        assert not result.accepted
        assert result.rejections[0].code == "session.command.quest_state"
        assert result.rejections[0].params == {"quest": QUEST_ID, "state": "active"}
        assert len(session.command_log) == logged

    def test_activating_a_completed_quest_is_refused_by_its_own_state(self):
        session = run(active_session(), CompleteQuest(quest_id=QUEST_ID))
        result = session.execute(ActivateQuest(quest_id=QUEST_ID))
        assert not result.accepted
        assert result.rejections[0].params == {"quest": QUEST_ID, "state": "completed"}

    def test_an_activation_draws_nothing_and_spends_no_time(self):
        session = make_session(build_quest())
        streams = {key: state.model_dump(mode="json") for key, state in session.streams.export_states().items()}
        rounds = session.clock.rounds
        assert session.execute(ActivateQuest(quest_id=QUEST_ID)).accepted
        assert {key: state.model_dump(mode="json") for key, state in session.streams.export_states().items()} == streams
        assert session.clock.rounds == rounds


class TestRevealGuards:
    def test_an_unknown_objective_is_rejected(self):
        session = active_session()
        result = session.execute(RevealObjective(quest_id=QUEST_ID, objective_id="no-such-objective"))
        assert not result.accepted
        assert result.rejections[0].code == "session.command.unknown_objective"
        assert result.rejections[0].params == {"quest": QUEST_ID, "objective": "no-such-objective"}

    def test_an_unknown_quest_is_rejected_before_the_objective(self):
        session = active_session()
        result = session.execute(RevealObjective(quest_id="no-such-quest", objective_id=RETURN))
        assert result.rejections[0].code == "session.command.unknown_quest"

    def test_revealing_on_an_inactive_quest_is_refused(self):
        session = make_session(build_quest())
        result = session.execute(RevealObjective(quest_id=QUEST_ID, objective_id=RETURN))
        assert not result.accepted
        assert result.rejections[0].code == "session.command.quest_state"
        assert result.rejections[0].params == {"quest": QUEST_ID, "state": "inactive"}

    def test_revealing_a_visible_objective_is_refused(self):
        session = active_session()
        result = session.execute(RevealObjective(quest_id=QUEST_ID, objective_id=RECOVER))
        assert not result.accepted
        assert result.rejections[0].code == "session.command.quest_state"
        assert result.rejections[0].params == {"quest": QUEST_ID, "objective": RECOVER, "state": "revealed"}

    def test_revealing_a_completed_objective_is_refused_as_complete(self):
        session = run(active_session(), CompleteObjective(quest_id=QUEST_ID, objective_id=RETURN))
        result = session.execute(RevealObjective(quest_id=QUEST_ID, objective_id=RETURN))
        assert not result.accepted
        assert result.rejections[0].params == {"quest": QUEST_ID, "objective": RETURN, "state": "complete"}

    def test_a_reveal_makes_the_hidden_objective_visible_and_leaves_it_incomplete(self):
        session = run(active_session(), RevealObjective(quest_id=QUEST_ID, objective_id=RETURN))
        assert session.quests[QUEST_ID].objectives[RETURN] == ObjectiveState(revealed=True, complete=False)


class TestCompletionGuards:
    def test_completing_on_an_inactive_quest_is_refused(self):
        session = make_session(build_quest())
        result = session.execute(CompleteObjective(quest_id=QUEST_ID, objective_id=RECOVER))
        assert not result.accepted
        assert result.rejections[0].params == {"quest": QUEST_ID, "state": "inactive"}

    def test_completing_a_completed_objective_is_refused(self):
        session = run(active_session(), CompleteObjective(quest_id=QUEST_ID, objective_id=RECOVER))
        result = session.execute(CompleteObjective(quest_id=QUEST_ID, objective_id=RECOVER))
        assert not result.accepted
        assert result.rejections[0].code == "session.command.quest_state"
        assert result.rejections[0].params == {"quest": QUEST_ID, "objective": RECOVER, "state": "complete"}

    def test_completing_an_unknown_objective_is_rejected(self):
        session = active_session()
        result = session.execute(CompleteObjective(quest_id=QUEST_ID, objective_id="no-such-objective"))
        assert result.rejections[0].code == "session.command.unknown_objective"

    def test_completing_a_hidden_objective_surfaces_it_with_no_separate_reveal(self):
        session = active_session()
        result = session.execute(CompleteObjective(quest_id=QUEST_ID, objective_id=RETURN))
        assert result.accepted
        assert session.quests[QUEST_ID].objectives[RETURN] == ObjectiveState(revealed=True, complete=True)
        assert not [event for event in result.events if isinstance(event, ObjectiveRevealedEvent)]
        assert [command.command_type for command in session.command_log] == ["activate_quest", "complete_objective"]

    def test_completing_the_last_objective_does_not_complete_the_quest(self):
        session = run(
            active_session(),
            CompleteObjective(quest_id=QUEST_ID, objective_id=RECOVER),
            CompleteObjective(quest_id=QUEST_ID, objective_id=RETURN),
        )
        assert session.quests[QUEST_ID].status == "active"

    def test_completing_a_quest_needs_it_active(self):
        session = make_session(build_quest())
        result = session.execute(CompleteQuest(quest_id=QUEST_ID))
        assert not result.accepted
        assert result.rejections[0].params == {"quest": QUEST_ID, "state": "inactive"}
        assert session.execute(ActivateQuest(quest_id=QUEST_ID)).accepted
        assert session.execute(CompleteQuest(quest_id=QUEST_ID)).accepted
        again = session.execute(CompleteQuest(quest_id=QUEST_ID))
        assert not again.accepted
        assert again.rejections[0].params == {"quest": QUEST_ID, "state": "completed"}

    def test_the_referee_may_complete_a_quest_whose_rule_is_unsatisfied(self):
        # Ruling a quest done is the referee's call: the handler checks that the
        # quest is active and nothing else, under either completion rule.
        session = active_session()
        assert session.quests[QUEST_ID].objectives[RECOVER].complete is False
        result = session.execute(CompleteQuest(quest_id=QUEST_ID))
        assert result.accepted
        assert session.quests[QUEST_ID].status == "completed"
        assert not any(state.complete for state in session.quests[QUEST_ID].objectives.values())


class TestJournalBeats:
    def test_each_beat_appends_the_mapped_text_stamped_with_the_clock(self):
        session = make_session(build_quest())
        run(
            session,
            ActivateQuest(quest_id=QUEST_ID),
            AdvanceTime(n=1, unit="turn"),
            RevealObjective(quest_id=QUEST_ID, objective_id=RETURN),
            AdvanceTime(n=1, unit="turn"),
            CompleteObjective(quest_id=QUEST_ID, objective_id=RECOVER),
            AdvanceTime(n=1, unit="turn"),
            CompleteObjective(quest_id=QUEST_ID, objective_id=RETURN),
            AdvanceTime(n=1, unit="turn"),
            CompleteQuest(quest_id=QUEST_ID),
        )
        assert session.journal == [
            JournalEntry(text=OFFER, rounds=0),
            JournalEntry(text=RETURN_OFFER, rounds=60),
            JournalEntry(text=RECOVER_PROGRESS, rounds=120),
            JournalEntry(text=RETURN_PROGRESS, rounds=180),
            JournalEntry(text=COMPLETION, rounds=240),
        ]

    def test_an_unauthored_beat_appends_nothing_and_rides_the_event_as_absent(self):
        quest = build_quest(narrative=None)
        session = make_session(quest)
        result = session.execute(ActivateQuest(quest_id=QUEST_ID))
        assert result.accepted
        assert session.journal == []
        event = next(event for event in result.events if isinstance(event, QuestActivatedEvent))
        assert event.narrative is None

    def test_an_empty_beat_is_not_a_beat(self):
        quest = build_quest(narrative=NarrativeBlock(offer="", completion=COMPLETION))
        session = make_session(quest)
        result = session.execute(ActivateQuest(quest_id=QUEST_ID))
        assert session.journal == []
        assert next(event for event in result.events if isinstance(event, QuestActivatedEvent)).narrative is None

    def test_a_quest_beat_emits_no_journal_entry_event(self):
        # The lifecycle event *is* the beat's event; emitting both would show the
        # table one line twice.
        session = make_session(build_quest())
        for command in (
            ActivateQuest(quest_id=QUEST_ID),
            RevealObjective(quest_id=QUEST_ID, objective_id=RETURN),
            CompleteObjective(quest_id=QUEST_ID, objective_id=RECOVER),
            CompleteObjective(quest_id=QUEST_ID, objective_id=RETURN),
            CompleteQuest(quest_id=QUEST_ID),
        ):
            result = session.execute(command)
            assert result.accepted, command.command_type
            assert not [event for event in result.events if isinstance(event, JournalEntryAddedEvent)]
        assert len(session.journal) == 5, "the beats landed all the same"


class TestEvents:
    def test_every_lifecycle_event_is_for_the_table_and_carries_its_beat(self):
        session = make_session(build_quest(concludes_adventure=True))
        emitted = []
        for command in (
            ActivateQuest(quest_id=QUEST_ID),
            RevealObjective(quest_id=QUEST_ID, objective_id=RETURN),
            CompleteObjective(quest_id=QUEST_ID, objective_id=RECOVER),
            CompleteQuest(quest_id=QUEST_ID),
        ):
            result = session.execute(command)
            assert result.accepted, command.command_type
            emitted.extend(result.events)
        assert [type(event) for event in emitted] == [
            QuestActivatedEvent,
            ObjectiveRevealedEvent,
            ObjectiveCompletedEvent,
            QuestCompletedEvent,
            AdventureCompletedEvent,
        ]
        assert {event.visibility for event in emitted} == {Visibility.PLAYER}
        assert [event.narrative for event in emitted] == [
            OFFER,
            RETURN_OFFER,
            RECOVER_PROGRESS,
            COMPLETION,
            COMPLETION,
        ]
        assert emitted[0].name == emitted[3].name == "The Jade Idol"
        assert emitted[1].objective_id == RETURN and emitted[2].objective_id == RECOVER

    def test_the_beat_rides_the_event_verbatim_through_the_formatter(self):
        from osrlib.messages import format_message

        session = active_session()
        result = session.execute(CompleteQuest(quest_id=QUEST_ID))
        event = next(event for event in result.events if isinstance(event, QuestCompletedEvent))
        assert format_message(event) == f"Quest complete: The Jade Idol. {COMPLETION}"


class TestVictory:
    def test_the_concluding_completion_ends_the_adventure(self):
        session = active_session(build_quest(concludes_adventure=True))
        run(
            session,
            EnterDungeon(dungeon_id="delve"),
            SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=30),
        )
        assert session.encounter is not None or session.battle is not None
        result = session.execute(CompleteQuest(quest_id=QUEST_ID))
        assert result.accepted
        assert session.mode is SessionMode.VICTORY
        assert session.encounter is None and session.battle is None
        assert [event.code for event in result.events] == ["session.quest.completed", "session.adventure.completed"]

    def test_a_quest_that_does_not_conclude_the_adventure_transitions_nothing(self):
        session = active_session()
        result = session.execute(CompleteQuest(quest_id=QUEST_ID))
        assert session.mode is SessionMode.TOWN
        assert [event.code for event in result.events] == ["session.quest.completed"]

    @pytest.mark.parametrize("mode", TERMINAL_MODES, ids=lambda mode: mode.value)
    def test_a_terminal_session_finishes_the_job_and_never_ends_twice(self, mode):
        session = active_session(build_quest(concludes_adventure=True))
        session.mode = mode
        result = session.execute(CompleteQuest(quest_id=QUEST_ID))
        assert result.accepted
        assert session.quests[QUEST_ID].status == "completed"
        assert session.journal == [JournalEntry(text=OFFER, rounds=0), JournalEntry(text=COMPLETION, rounds=0)]
        assert [event.code for event in result.events] == ["session.quest.completed"]
        assert not [event for event in result.events if isinstance(event, AdventureCompletedEvent)]
        assert session.mode is mode

    def test_the_whole_lifecycle_runs_in_a_terminal_mode(self):
        session = make_session(build_quest())
        session.mode = SessionMode.GAME_OVER
        run(
            session,
            ActivateQuest(quest_id=QUEST_ID),
            RevealObjective(quest_id=QUEST_ID, objective_id=RETURN),
            CompleteObjective(quest_id=QUEST_ID, objective_id=RECOVER),
            CompleteQuest(quest_id=QUEST_ID),
        )
        assert session.mode is SessionMode.GAME_OVER


class TestPersistence:
    LIFECYCLE: tuple[Command, ...] = (
        ActivateQuest(quest_id=QUEST_ID),
        RevealObjective(quest_id=QUEST_ID, objective_id=RETURN),
        AdvanceTime(n=1, unit="turn"),
        CompleteObjective(quest_id=QUEST_ID, objective_id=RECOVER),
    )

    def played(self) -> GameSession:
        return run(make_session(build_quest()), *self.LIFECYCLE)

    def test_the_block_round_trips_through_a_save(self):
        session = self.played()
        restored = load_game(json.loads(json.dumps(save_game(session))))
        assert restored.quests == session.quests
        assert restored.quests[QUEST_ID].objectives[RECOVER] == ObjectiveState(revealed=True, complete=True)
        assert session_state(restored) == session_state(session)

    def test_a_save_written_before_the_block_loads_the_constructor_seed(self):
        document = json.loads(json.dumps(save_game(self.played())))
        del document["payload"]["quests"]
        restored = load_game(document)
        assert restored.quests == {
            QUEST_ID: QuestState(
                status="inactive",
                objectives={
                    RECOVER: ObjectiveState(revealed=True, complete=False),
                    RETURN: ObjectiveState(revealed=False, complete=False),
                },
            )
        }
        assert restored.execute(ActivateQuest(quest_id=QUEST_ID)).accepted

    def test_a_save_written_before_quests_existed_at_all_loads_unchanged(self):
        session = run(make_session(), EnterDungeon(dungeon_id="delve"))
        document = json.loads(json.dumps(save_game(session)))
        del document["payload"]["quests"]
        del document["payload"]["adventure"]["quests"]
        restored = load_game(document)
        assert restored.adventure.quests == ()
        assert restored.quests == {}
        assert session_state(restored) == session_state(session)

    def test_load_equals_replay_with_the_block_populated(self):
        from osrlib.core.character import party_to_document
        from osrlib.core.ruleset import Ruleset
        from osrlib.persistence import replay_game

        session = self.played()
        restored = load_game(json.loads(json.dumps(save_game(session))))
        replayed = replay_game(
            19,
            party_to_document(build_party().members),
            with_quests(build_quest()),
            Ruleset(),
            [command.model_dump(mode="json") for command in session.command_log],
        )
        assert replayed.quests == session.quests
        assert session_state(replayed) == session_state(restored)

    def test_the_referee_view_carries_the_block(self):
        state = self.played().view(Visibility.REFEREE).state
        assert state["quests"][QUEST_ID]["status"] == "active"
        assert state["quests"][QUEST_ID]["objectives"][RECOVER] == {"revealed": True, "complete": True}


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    seed=st.integers(min_value=0, max_value=2**32),
    commands=st.lists(command_strategy(), min_size=1, max_size=25),
)
def test_fuzzed_quest_commands_never_raise_and_the_state_machine_stays_monotonic(seed, commands):
    """The fuzz contract over the quest guards: they reject, and nothing goes backwards."""
    session = GameSession.new(build_party(), with_quests(build_quest(concludes_adventure=True)), seed=seed)
    order = {"inactive": 0, "active": 1, "completed": 2}
    previous = session.quests[QUEST_ID].model_copy(deep=True)
    for command in commands:
        session.execute(command)  # must never raise
        current = session.quests[QUEST_ID]
        assert order[current.status] >= order[previous.status]
        for objective_id, state in current.objectives.items():
            was = previous.objectives[objective_id]
            assert state.revealed >= was.revealed and state.complete >= was.complete
            assert state.revealed or not state.complete, "a complete objective is a revealed one"
        previous = current.model_copy(deep=True)
