"""The lifecycle commands, the journal, the fired-marks, and the `source` stamp.

Three commands write the bookkeeping an authored trigger or quest layer needs:
`MarkTriggerFired` records fired-state, `AddJournalEntry` appends a beat, and
`RecordNote` records an annotation and changes nothing. None of them draws a die
or spends a round, all three are legal in every mode, and the two that mutate write
nothing but their own block.

The `source` stamp is an annotation and nothing else: a stamped command executes
exactly as the same command unstamped does, and the only trace it leaves is on the
logged command itself, where a save, a load, and a replay all carry it verbatim.
"""

import json

import pytest
from pydantic import ValidationError

from crawl_fixtures import build_adventure, build_party
from osrlib.core.events import Visibility
from osrlib.crawl.commands import (
    AddJournalEntry,
    AdvanceTime,
    Command,
    EnterDungeon,
    MarkTriggerFired,
    MoveParty,
    RecordNote,
    RollDice,
    SessionMode,
    SetFlag,
    parse_command,
)
from osrlib.crawl.dungeon import Direction
from osrlib.crawl.events import JournalEntryAddedEvent, NoteRecordedEvent, TriggerFiredEvent
from osrlib.crawl.session import GameSession, JournalEntry
from osrlib.persistence import load_game, save_game, session_state

TERMINAL_MODES = (SessionMode.GAME_OVER, SessionMode.VICTORY)

STAMP = "trigger:lever-east"

# One command of each temper: a mode switch, a move that draws and spends time, a
# referee roll on its own stream, a flag write, and a span of clock.
SCRIPT: tuple[Command, ...] = (
    EnterDungeon(dungeon_id="delve"),
    MoveParty(direction=Direction.EAST),
    RollDice(expression="2d6"),
    SetFlag(key="portcullis", value="open"),
    AdvanceTime(n=2, unit="turn"),
)


def make_session(seed: int = 17) -> GameSession:
    return GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=seed)


def run(commands) -> GameSession:
    session = make_session()
    for command in commands:
        result = session.execute(command)
        assert result.accepted, [rejection.code for rejection in result.rejections]
    return session


def stream_states(session: GameSession) -> dict:
    return {key: state.model_dump(mode="json") for key, state in session.streams.export_states().items()}


def state_but_the_logs(session: GameSession) -> dict:
    """Every state block, with the two logs (which every command touches) removed."""
    state = session_state(session)
    state.pop("command_log")
    state.pop("event_log")
    return state


class TestMarkTriggerFired:
    def test_marks_append_in_first_fired_order(self):
        session = run(
            [
                MarkTriggerFired(trigger_id="lever-east"),
                MarkTriggerFired(trigger_id="idol-lifted"),
                MarkTriggerFired(trigger_id="gate-opened"),
            ]
        )
        assert session.fired_triggers == ["lever-east", "idol-lifted", "gate-opened"]

    def test_a_re_mark_appends_nothing_and_still_reports_the_firing(self):
        session = run([MarkTriggerFired(trigger_id="lever-east"), MarkTriggerFired(trigger_id="idol-lifted")])
        before = state_but_the_logs(session)
        result = session.execute(MarkTriggerFired(trigger_id="lever-east"))
        assert result.accepted
        assert session.fired_triggers == ["lever-east", "idol-lifted"], "state records that a trigger has fired"
        assert state_but_the_logs(session) == before
        events = [event for event in result.events if isinstance(event, TriggerFiredEvent)]
        assert [event.trigger_id for event in events] == ["lever-east"], "and the log records each firing"

    def test_a_mark_draws_nothing_and_spends_no_time(self):
        session = run([EnterDungeon(dungeon_id="delve")])
        before, rounds = stream_states(session), session.clock.rounds
        assert session.execute(MarkTriggerFired(trigger_id="lever-east")).accepted
        assert stream_states(session) == before
        assert session.clock.rounds == rounds

    def test_the_trigger_id_is_open_domain_but_never_empty(self):
        session = run([MarkTriggerFired(trigger_id="no-such-trigger-anywhere")])
        assert session.fired_triggers == ["no-such-trigger-anywhere"]
        with pytest.raises(ValidationError):
            MarkTriggerFired(trigger_id="")


class TestAddJournalEntry:
    def test_entries_append_in_order_stamped_with_the_clock(self):
        session = run(
            [
                AddJournalEntry(text="The lever grinds; somewhere below, a portcullis rises."),
                AdvanceTime(n=2, unit="turn"),
                AddJournalEntry(text="The idol is lighter than it looks."),
            ]
        )
        assert session.journal == [
            JournalEntry(text="The lever grinds; somewhere below, a portcullis rises.", rounds=0),
            JournalEntry(text="The idol is lighter than it looks.", rounds=120),
        ]
        assert session.clock.rounds == 120, "the entries stamp the clock, they do not move it"

    def test_the_same_beat_twice_appends_twice(self):
        text = "The door refuses."
        session = run([AddJournalEntry(text=text), AddJournalEntry(text=text)])
        assert [entry.text for entry in session.journal] == [text, text]

    def test_an_entry_draws_nothing_and_spends_no_time(self):
        session = run([EnterDungeon(dungeon_id="delve")])
        before, rounds = stream_states(session), session.clock.rounds
        assert session.execute(AddJournalEntry(text="Down the stair, into the dark.")).accepted
        assert stream_states(session) == before
        assert session.clock.rounds == rounds

    def test_an_empty_beat_is_not_a_beat(self):
        with pytest.raises(ValidationError):
            AddJournalEntry(text="")


class TestRecordNote:
    def test_a_note_changes_no_state_at_all(self):
        session = run([MarkTriggerFired(trigger_id="lever-east"), AddJournalEntry(text="The lever grinds.")])
        before = state_but_the_logs(session)
        result = session.execute(RecordNote(text="The second consequence was dropped: no such item."))
        assert result.accepted
        assert state_but_the_logs(session) == before
        assert [event.text for event in result.events if isinstance(event, NoteRecordedEvent)] == [
            "The second consequence was dropped: no such item."
        ]

    def test_an_empty_note_is_not_a_note(self):
        with pytest.raises(ValidationError):
            RecordNote(text="")


class TestLegality:
    @pytest.mark.parametrize("mode", TERMINAL_MODES, ids=lambda mode: mode.value)
    def test_all_three_execute_in_a_terminal_mode(self, mode):
        session = make_session()
        session.mode = mode
        commands = (
            MarkTriggerFired(trigger_id="idol-returned"),
            AddJournalEntry(text="The idol sits on the altar where it began."),
            RecordNote(text="The reward landed after the ending."),
        )
        for command in commands:
            assert session.execute(command).accepted, command.command_type
        assert session.mode is mode
        assert len(session.command_log) == len(commands)
        assert session.fired_triggers == ["idol-returned"]
        assert [entry.text for entry in session.journal] == ["The idol sits on the altar where it began."]


class TestVisibility:
    def test_the_journal_is_for_the_table_and_the_wiring_is_not(self):
        session = make_session()
        emitted = []
        for command in (
            MarkTriggerFired(trigger_id="lever-east"),
            AddJournalEntry(text="The lever grinds; somewhere below, a portcullis rises."),
            RecordNote(text="The east lever is the only one that answers."),
        ):
            result = session.execute(command)
            assert result.accepted
            emitted.extend(result.events)
        player = [event for event in emitted if event.visibility is Visibility.PLAYER]
        referee = [event for event in emitted if event.visibility is Visibility.REFEREE]
        assert [type(event) for event in player] == [JournalEntryAddedEvent]
        assert [type(event) for event in referee] == [TriggerFiredEvent, NoteRecordedEvent]
        assert player[0].text == "The lever grinds; somewhere below, a portcullis rises."
        assert player[0].rounds == session.clock.rounds


class TestPersistenceAndTheView:
    """Both blocks are ordinary session state: saved, loaded, replayed, projected."""

    LIFECYCLE: tuple[Command, ...] = (
        MarkTriggerFired(trigger_id="lever-east"),
        SetFlag(key="portcullis", value="open", source="trigger:lever-east"),
        AddJournalEntry(text="The lever grinds; somewhere below, a portcullis rises.", source="trigger:lever-east"),
        AdvanceTime(n=1, unit="turn"),
        MarkTriggerFired(trigger_id="lever-east"),
        AddJournalEntry(text="The same lever, the same grinding.", source="trigger:lever-east"),
        RecordNote(text="The east lever is the only one that answers."),
    )

    def test_both_blocks_round_trip_through_a_save(self):
        session = run(self.LIFECYCLE)
        restored = load_game(json.loads(json.dumps(save_game(session))))
        assert restored.fired_triggers == ["lever-east"]
        assert restored.journal == session.journal
        assert [entry.rounds for entry in restored.journal] == [0, 60]
        assert session_state(restored) == session_state(session)

    def test_a_save_written_without_the_blocks_loads_empty_and_starts_remembering(self):
        document = json.loads(json.dumps(save_game(run([EnterDungeon(dungeon_id="delve")]))))
        del document["payload"]["fired_triggers"]
        del document["payload"]["journal"]
        restored = load_game(document)
        assert restored.fired_triggers == []
        assert restored.journal == []
        assert restored.execute(MarkTriggerFired(trigger_id="lever-east")).accepted
        assert restored.execute(AddJournalEntry(text="The lever grinds.")).accepted
        assert restored.fired_triggers == ["lever-east"]
        assert [entry.text for entry in restored.journal] == ["The lever grinds."]

    def test_load_equals_replay_with_both_blocks_populated(self):
        from osrlib.core.character import party_to_document
        from osrlib.core.ruleset import Ruleset
        from osrlib.persistence import replay_game

        session = run(self.LIFECYCLE)
        restored = load_game(json.loads(json.dumps(save_game(session))))
        replayed = replay_game(
            17,
            party_to_document(build_party().members),
            build_adventure(wandering_chance=0),
            Ruleset(),
            [command.model_dump(mode="json") for command in session.command_log],
        )
        assert replayed.fired_triggers == ["lever-east"]
        assert replayed.journal == session.journal
        assert session_state(replayed) == session_state(restored)

    def test_the_player_view_ships_the_journal_and_neither_the_marks_nor_the_notes(self):
        session = run(self.LIFECYCLE)
        view = session.view(Visibility.PLAYER)
        assert view.journal == tuple(session.journal)
        blob = view.model_dump_json()
        parsed = json.loads(blob)
        assert "fired_triggers" not in parsed
        assert "lever-east" not in blob, "the trigger id is wiring, and wiring is the game's secret"
        assert "The east lever is the only one that answers." not in blob, "a referee note is not for the table"
        assert "The lever grinds; somewhere below, a portcullis rises." in blob

    def test_the_referee_view_carries_both_blocks(self):
        session = run(self.LIFECYCLE)
        state = session.view(Visibility.REFEREE).state
        assert state["fired_triggers"] == ["lever-east"]
        assert [entry["text"] for entry in state["journal"]] == [entry.text for entry in session.journal]


class TestTheSourceStamp:
    def test_a_stamped_command_survives_save_and_load(self):
        session = run([command.model_copy(update={"source": STAMP}) for command in SCRIPT])
        restored = load_game(json.loads(json.dumps(save_game(session))))
        assert [command.source for command in restored.command_log] == [STAMP] * len(SCRIPT)
        assert session_state(restored) == session_state(session)

    def test_execution_ignores_the_stamp(self):
        stamped = run([command.model_copy(update={"source": STAMP}) for command in SCRIPT])
        plain = run(SCRIPT)
        stamped_state = session_state(stamped)
        plain_state = session_state(plain)
        logged = stamped_state.pop("command_log"), plain_state.pop("command_log")
        assert stamped_state == plain_state, "the stamp annotates the log and changes nothing else"
        assert [entry.pop("source") for entry in logged[0]] == [STAMP] * len(SCRIPT)
        assert [entry.pop("source") for entry in logged[1]] == [None] * len(SCRIPT)
        assert logged[0] == logged[1]

    def test_a_command_logged_without_a_stamp_parses_as_unstamped(self):
        command = parse_command({"command_type": "set_flag", "key": "portcullis", "value": "open"})
        assert command is not None
        assert command.source is None

    def test_the_empty_string_is_not_a_stamp(self):
        with pytest.raises(ValidationError):
            SetFlag(key="portcullis", value="open", source="")
