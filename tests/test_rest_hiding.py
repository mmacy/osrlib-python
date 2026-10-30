"""Acceptance tests for the hiding rule: only a hidden rest lowers the wandering-monster chance.

The OSE SRD's hazards and challenges page gives the decrease to hiding, not to resting: "If the
party rests quietly in an out-of-the-way location, the referee may decrease the chance of
wandering monsters being encountered." A plain `Rest` in a dungeon checks at the level's chance,
with noise and bright light added as usual, so resting carries a risk. A `Rest` with `hidden` set
takes the register's −1 on every check it spans.

The tests marked `chunk: rest-hiding` describe behavior the chunk adds. The unmarked tests pass
before the chunk and must keep passing after it. Most of them pin the hidden half of the rule, so
an implementation that drops the −1 everywhere fails them.

Every scenario runs on a one-level dungeon whose wandering interval is one turn, so each turn of
rest makes exactly one check. The encounter table is all grey oozes, so a check that hits opens an
ordinary encounter. The helpers assert their preconditions, so a scenario that stops exercising its
case fails instead of passing vacuously.
"""

import json
from typing import Literal

import pytest

from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character, party_to_document
from osrlib.core.clock import ROUNDS_PER_TURN
from osrlib.core.effects import EffectDefinition
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.tables import EncounterTable, EncounterTableRow, MonsterEncounterEntry
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import CommandResult, EnterDungeon, Rest, SessionMode, parse_command
from osrlib.crawl.dungeon import DungeonSpec, LevelSpec, WanderingSpec
from osrlib.crawl.events import WanderingCheckEvent
from osrlib.crawl.party import Party
from osrlib.crawl.session import WANDERING_STREAM, GameSession
from osrlib.persistence import load_game, replay_game, save_game

RestKind = Literal["turn", "night", "day"]

REST_TURNS: dict[RestKind, int] = {"turn": 1, "night": 48, "day": 144}

SEED = 11

_OOZES = EncounterTable(
    id="oozes",
    label="Oozes",
    min_level=1,
    rows=tuple(
        EncounterTableRow(
            roll=roll,
            name="Grey ooze",
            entry=MonsterEncounterEntry(monster_ids=("grey_ooze",)),
            count_fixed=1,
        )
        for roll in range(1, 21)
    ),
)
"""Every wandering encounter is one grey ooze, so a hit always opens the same kind of encounter."""


def build_party() -> Party:
    draw = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
    return Party(
        members=[
            create_character(
                name="Brok", class_id="dwarf", alignment=Alignment.LAWFUL, ruleset=Ruleset(), stream=draw
            ).character,
            create_character(
                name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=Ruleset(), stream=draw
            ).character,
        ]
    )


def build_adventure(chance: int) -> Adventure:
    level = LevelSpec(
        number=1,
        width=1,
        height=1,
        entrance=(0, 0),
        wandering=WanderingSpec(chance_in_six=chance, interval_turns=1, table=_OOZES),
    )
    return Adventure(
        name="Rest and hide",
        town=TownSpec(name="Threshold", travel_turns={"crypt": 1}),
        dungeons=(DungeonSpec(id="crypt", name="Crypt", levels=(level,)),),
    )


def wandering_state(session: GameSession) -> dict:
    return session.streams.get(WANDERING_STREAM).export_state().model_dump(mode="json")


def checks(result: CommandResult) -> list[WanderingCheckEvent]:
    return [event for event in result.events if isinstance(event, WanderingCheckEvent)]


def codes(result: CommandResult) -> list[str]:
    return [event.code for event in result.events]


def make_bright(session: GameSession) -> None:
    """Give a member *continual light*'s daylight brightness, the light that raises the chance."""
    definition = EffectDefinition(kind="continual_light", params={"brightness": "daylight", "radius_feet": 30})
    member = session.party.living_members()[0]
    session.ledger.attach(
        definition, member.id, clock=session.clock, allocator=session.allocator, registry=session.registry()
    )
    assert session.bright_light()


def dungeon_session(chance: int, *, seed: int = SEED, noise: bool = False, bright: bool = False) -> GameSession:
    """Start a session with the party in the dungeon, exploring, on a turn boundary."""
    session = GameSession.new(build_party(), build_adventure(chance), seed=seed)
    assert session.execute(EnterDungeon(dungeon_id="crypt")).accepted
    assert session.mode is SessionMode.EXPLORING
    assert session.dungeon_state.location.kind == "dungeon"
    assert session.clock.rounds % ROUNDS_PER_TURN == 0
    if bright:
        make_bright(session)
    session.noise_since_check = noise
    # Preconditions: the level's chance is as given, and the modifiers are exactly the ones asked for.
    level = session.adventure.dungeon("crypt").levels[0]
    assert level.wandering.chance_in_six == chance
    assert level.wandering.interval_turns == 1
    assert session.noise_since_check is noise
    assert session.bright_light() is bright
    return session


def assert_check(check: WanderingCheckEvent, *, chance: int, drew: bool) -> None:
    assert check.chance == chance
    if chance == 0:
        assert check.roll is None
        assert check.encounter is False
    else:
        assert check.roll is not None and 1 <= check.roll <= 6
        assert check.encounter is (check.roll <= chance)
    assert drew is (chance > 0)


# (level chance, noise since the last check, bright light, expected chance)
PLAIN_CASES = [
    pytest.param(1, False, False, 1, id="one-in-six"),
    pytest.param(2, False, False, 2, id="two-in-six"),
    pytest.param(6, False, False, 6, id="six-in-six"),
    pytest.param(1, True, False, 2, id="noise"),
    pytest.param(1, False, True, 2, id="bright-light"),
    pytest.param(1, True, True, 3, id="noise-and-bright-light"),
]
HIDDEN_CASES = [
    pytest.param(1, False, False, 0, id="one-in-six"),
    pytest.param(2, False, False, 1, id="two-in-six"),
    pytest.param(0, False, False, 0, id="zero-clamps"),
    pytest.param(1, True, False, 1, id="noise"),
    pytest.param(1, False, True, 1, id="bright-light"),
    pytest.param(1, True, True, 2, id="noise-and-bright-light"),
    pytest.param(6, True, True, 6, id="six-clamps"),
]


class TestOneTurnRest:
    """One turn of rest makes one check, at a chance set by the level, the modifiers, and `hidden`."""

    @pytest.mark.xfail(reason="chunk: rest-hiding", strict=True)
    @pytest.mark.parametrize(("level_chance", "noise", "bright", "expected"), PLAIN_CASES)
    def test_a_plain_rest_checks_at_the_level_chance_with_noise_and_light(self, level_chance, noise, bright, expected):
        session = dungeon_session(level_chance, noise=noise, bright=bright)
        before = wandering_state(session)
        result = session.execute(Rest(kind="turn"))
        assert result.accepted
        [check] = checks(result)
        assert_check(check, chance=expected, drew=wandering_state(session) != before)
        assert session.noise_since_check is False

    @pytest.mark.parametrize(("level_chance", "noise", "bright", "expected"), HIDDEN_CASES)
    def test_a_hidden_rest_checks_one_lower_after_noise_and_light(self, level_chance, noise, bright, expected):
        session = dungeon_session(level_chance, noise=noise, bright=bright)
        before = wandering_state(session)
        result = session.execute(Rest(kind="turn", hidden=True))
        assert result.accepted
        [check] = checks(result)
        assert_check(check, chance=expected, drew=wandering_state(session) != before)
        assert session.noise_since_check is False


class TestRestingCarriesRisk:
    @pytest.mark.xfail(reason="chunk: rest-hiding", strict=True)
    def test_plain_rests_on_a_one_in_six_level_are_sometimes_interrupted(self):
        session = dungeon_session(1)
        for _ in range(60):
            result = session.execute(Rest(kind="turn"))
            assert result.accepted
            [check] = checks(result)
            assert check.chance == 1
            assert check.roll is not None
            if check.encounter:
                assert "exploration.rest.interrupted" in codes(result)
                assert session.mode in (SessionMode.ENCOUNTER, SessionMode.BATTLE)
                return
            assert "exploration.rest.rested" in codes(result)
        pytest.fail("sixty plain rests at 1 in 6 never brought a wandering monster")

    def test_hidden_rests_on_a_one_in_six_level_never_roll(self):
        session = dungeon_session(1)
        before = wandering_state(session)
        for _ in range(60):
            result = session.execute(Rest(kind="turn", hidden=True))
            assert result.accepted
            [check] = checks(result)
            assert (check.chance, check.roll, check.encounter) == (0, None, False)
            assert "exploration.rest.rested" in codes(result)
        assert session.mode is SessionMode.EXPLORING
        assert wandering_state(session) == before


class TestLongRests:
    """A night or a day of rest passes `hidden` to every check it spans, through the multi-turn path."""

    @pytest.mark.xfail(reason="chunk: rest-hiding", strict=True)
    @pytest.mark.parametrize("kind", ["turn", "night", "day"])
    def test_every_check_in_a_plain_rest_rolls_at_the_level_chance(self, kind: RestKind):
        session = dungeon_session(1)
        start = session.clock.rounds
        result = session.execute(Rest(kind=kind))
        assert result.accepted
        made = checks(result)
        # The rest runs one check per turn until it ends or a check brings monsters.
        assert made
        assert [check.chance for check in made] == [1] * len(made)
        assert None not in [check.roll for check in made]
        assert [check.encounter for check in made[:-1]] == [False] * (len(made) - 1)
        if made[-1].encounter:
            assert "exploration.rest.interrupted" in codes(result)
        else:
            assert len(made) == REST_TURNS[kind]
            assert session.clock.rounds - start == REST_TURNS[kind] * ROUNDS_PER_TURN

    @pytest.mark.parametrize("kind", ["turn", "night", "day"])
    def test_no_check_in_a_hidden_rest_rolls_on_a_one_in_six_level(self, kind: RestKind):
        session = dungeon_session(1)
        before = wandering_state(session)
        start = session.clock.rounds
        result = session.execute(Rest(kind=kind, hidden=True))
        assert result.accepted
        made = checks(result)
        assert len(made) == REST_TURNS[kind]
        assert {(check.chance, check.roll, check.encounter) for check in made} == {(0, None, False)}
        assert "exploration.rest.rested" in codes(result)
        assert session.clock.rounds - start == REST_TURNS[kind] * ROUNDS_PER_TURN
        assert wandering_state(session) == before

    @pytest.mark.xfail(reason="chunk: rest-hiding", strict=True)
    def test_a_hidden_night_on_a_two_in_six_level_rolls_every_check_at_one_in_six(self):
        session = dungeon_session(2)
        result = session.execute(Rest(kind="night", hidden=True))
        assert result.accepted
        made = checks(result)
        assert made
        assert [check.chance for check in made] == [1] * len(made)
        assert None not in [check.roll for check in made]
        # The contrast: the same night, not hidden, rolls at the level's 2 in 6.
        plain = dungeon_session(2)
        result = plain.execute(Rest(kind="night"))
        assert result.accepted
        made = checks(result)
        assert made
        assert [check.chance for check in made] == [2] * len(made)
        assert None not in [check.roll for check in made]


class TestAdvanceTurns:
    """`GameSession.advance_turns` keeps fatigue (`resting`) and the −1 (`hidden`) apart."""

    @pytest.mark.xfail(reason="chunk: rest-hiding", strict=True)
    def test_resting_alone_does_not_lower_the_chance(self):
        session = dungeon_session(1)
        before = wandering_state(session)
        events, _ = session.advance_turns(1, resting=True)
        [check] = [event for event in events if isinstance(event, WanderingCheckEvent)]
        assert check.chance == 1 and check.roll is not None
        assert wandering_state(session) != before

    def test_resting_and_hidden_lower_the_chance(self):
        session = dungeon_session(1)
        before = wandering_state(session)
        events, _ = session.advance_turns(1, resting=True, hidden=True)
        [check] = [event for event in events if isinstance(event, WanderingCheckEvent)]
        assert (check.chance, check.roll) == (0, None)
        assert wandering_state(session) == before

    @pytest.mark.xfail(reason="chunk: rest-hiding", strict=True)
    def test_hidden_lowers_the_chance_without_resting(self):
        session = dungeon_session(2)
        turns_since_rest = session.turns_since_rest
        events, _ = session.advance_turns(1, hidden=True)
        [check] = [event for event in events if isinstance(event, WanderingCheckEvent)]
        assert check.chance == 1
        # Not resting, so the turn counts toward the next required rest.
        assert session.turns_since_rest == turns_since_rest + 1


class TestTown:
    """`hidden` is accepted in town and changes nothing there, because no checks run in town."""

    @pytest.mark.parametrize("kind", ["turn", "night", "day"])
    def test_a_hidden_rest_in_town_is_the_same_as_a_plain_one(self, kind: RestKind):
        outcomes = []
        for hidden in (False, True):
            session = GameSession.new(build_party(), build_adventure(6), seed=SEED)
            assert session.mode is SessionMode.TOWN
            before = wandering_state(session)
            result = session.execute(Rest(kind=kind, hidden=hidden))
            assert result.accepted
            assert checks(result) == []
            assert "exploration.rest.rested" in codes(result)
            assert wandering_state(session) == before
            outcomes.append(
                (
                    [event.model_dump(mode="json") for event in result.events],
                    session.clock.rounds,
                    session.streams.export_states(),
                    session.mode,
                )
            )
        assert outcomes[0] == outcomes[1]


class TestCommandLog:
    def test_the_field_round_trips_and_defaults_to_false(self):
        command = Rest(kind="night", hidden=True)
        data = json.loads(json.dumps(command.model_dump(mode="json")))
        assert data["hidden"] is True
        assert parse_command(data) == command
        assert Rest(kind="turn").hidden is False
        # A log recorded before the field existed parses as a plain rest.
        legacy = parse_command({"command_type": "rest", "kind": "turn"})
        assert isinstance(legacy, Rest)
        assert legacy.hidden is False

    @pytest.mark.xfail(reason="chunk: rest-hiding", strict=True)
    def test_a_logged_hidden_rest_replays_and_loads_as_hidden(self):
        session = dungeon_session(1)
        hidden = session.execute(Rest(kind="turn", hidden=True))
        assert hidden.accepted
        [hidden_check] = checks(hidden)
        assert (hidden_check.chance, hidden_check.roll) == (0, None)
        plain = session.execute(Rest(kind="turn"))
        assert plain.accepted
        [plain_check] = checks(plain)
        assert plain_check.chance == 1 and plain_check.roll is not None

        logged = [command.model_dump(mode="json") for command in session.command_log]
        rests = [entry for entry in logged if entry["command_type"] == "rest"]
        assert [entry["hidden"] for entry in rests] == [True, False]

        serialized = json.loads(json.dumps(logged))
        replayed = replay_game(
            SEED, party_to_document(build_party().members), build_adventure(1), Ruleset(), serialized
        )
        original_events = [event.model_dump(mode="json") for event in session.event_log]
        replayed_events = [event.model_dump(mode="json") for event in replayed.event_log]
        assert replayed_events == original_events
        assert replayed.streams.export_states() == session.streams.export_states()

        restored = load_game(json.loads(json.dumps(save_game(session))))
        restored_rests = [command for command in restored.command_log if isinstance(command, Rest)]
        assert [command.hidden for command in restored_rests] == [True, False]
