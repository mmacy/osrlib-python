"""Acceptance tests for `EndEncounter`, the referee command that closes an encounter peacefully.

Issue #167, item 23. Before this command, a front end whose party talked its way past
or walked away closed the encounter by calling `end_encounter` itself. That call isn't
a command, so the command log never recorded it and a replay stayed in the encounter.
And a keyed area counted as resolved only when every monster in it was slain or
routed, so guards a party paid or bluffed past were back the next time it walked in.

The contract these tests pin:

- `EndEncounter(outcome="parley" | "departed", resolve_area=False)` is a referee
  command, legal in `encounter` and `battle` and refused everywhere else with
  `session.command.wrong_mode`, at no cost.
- It closes the encounter the way `end_encounter` does, with its outcome on the
  `EncounterEndedEvent`. A battle closes with it. Monsters left standing aren't
  defeated, and monsters already slain are.
- `resolve_area=True` resolves the keyed area the encounter came from, and does
  nothing for an encounter that came from anywhere else.
- It's logged, so save, load, and replay reproduce it.
- It joins the authored-consequence surface, because the spec makes every referee
  command a consequence except the three exclusions it names.
"""

import json

import pytest
from pydantic import TypeAdapter, ValidationError

from crawl_fixtures import build_party
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character, party_to_document
from osrlib.core.clock import ROUNDS_PER_TURN
from osrlib.core.effects import Condition, has_condition
from osrlib.core.events import Visibility
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset, XpAwardTiming
from osrlib.core.tables import ReactionResult
from osrlib.crawl import encounter as encounter_module
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import (
    ALL_COMMAND_CLASSES,
    CONSEQUENCE_COMMAND_CLASSES,
    AnyCommand,
    BattleDeclaration,
    ConsequenceCommand,
    EngageBattle,
    EnterDungeon,
    Evade,
    MoveParty,
    RecordNote,
    ResolveBattleRound,
    SessionMode,
    SetFlag,
    SpawnMonsters,
    parse_command,
)
from osrlib.crawl.dungeon import (
    AreaSpec,
    Direction,
    DungeonSpec,
    Edge,
    EdgeKind,
    KeyedEncounter,
    KeyedMonster,
    LevelSpec,
    WanderingSpec,
)
from osrlib.crawl.events import EncounterEndedEvent, MonsterDefeatedEvent
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.crawl.triggers import FlagSetPattern, TriggerSpec
from osrlib.errors import ContentValidationError
from osrlib.persistence import load_game, replay_game, save_game, session_state

SEED = 7

GUARDROOM_REF = "crypt:1:guardroom"
"""The keyed area's reference, as `EncounterState.area_ref` and `resolved_encounters` spell it."""

TOLL_FLAG = "crypt.toll"

OUTCOMES = ("parley", "departed")

LIVE_MODES = ("encounter", "battle")

ONE_SLAIN_SEED = 12
"""A seed on which the front rank cuts down one of two skeletons and leaves the other standing."""

COMMAND_INDEX = 54
"""`EndEncounter`'s place in `ALL_COMMAND_CLASSES`: the first slot after the commands that exist today.

New commands are appended, so every existing index keeps its class."""


# ---------------------------------------------------------------------- fixtures


def end(outcome: str = "parley", **fields):
    """Build an `EndEncounter`. The import is here so each test fails on its own until the command exists."""
    from osrlib.crawl.commands import EndEncounter

    return EndEncounter(outcome=outcome, **fields)


def toll_adventure(*, triggers: tuple = ()) -> Adventure:
    """One dungeon, one level: a corridor four cells long, entered at (0, 0).

    ```text
        x0     x1     x2           x3
    y0  ENT————corr———[guardroom]——corr
    ```

    Two goblins keep the guardroom at (2, 0). They expect the party and are pinned
    indifferent, so walking in always opens an encounter and never a battle. They
    keep no hoard, and the level has no wandering monsters, so nothing but the
    commands under test moves the encounter stream.
    """
    edges = {f"{x},0:west": Edge(kind=EdgeKind.OPEN) for x in range(1, 4)}
    guardroom = AreaSpec(
        id="guardroom",
        name="Guardroom",
        cells=((2, 0),),
        encounter=KeyedEncounter(
            monsters=(KeyedMonster(template_id="goblin", count_fixed=2),),
            aware=True,
            stance=ReactionResult.INDIFFERENT,
            hoard=False,
        ),
    )
    level = LevelSpec(
        number=1,
        width=4,
        height=1,
        entrance=(0, 0),
        edges=edges,
        areas=(guardroom,),
        wandering=WanderingSpec(chance_in_six=0),
    )
    return Adventure(
        name="The Toll",
        town=TownSpec(name="Threshold", travel_turns={"crypt": 1}),
        dungeons=(DungeonSpec(id="crypt", name="Crypt", levels=(level,)),),
        triggers=triggers,
    )


def new_game(*, seed: int = SEED, enter: bool = True, ruleset: Ruleset | None = None, adventure=None) -> GameSession:
    game = GameSession.new(build_party(), adventure or toll_adventure(), seed=seed, ruleset=ruleset)
    if enter:
        assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
    return game


def party_document() -> list:
    """The starting party as `replay_game` takes it: fresh, before any session assigned ids."""
    return party_to_document(build_party().members)


def walk_into_guardroom(game: GameSession) -> None:
    for _ in range(2):
        assert game.execute(MoveParty(direction=Direction.EAST)).accepted
    assert game.mode is SessionMode.ENCOUNTER
    assert game.encounter.kind == "keyed"
    assert game.encounter.area_ref == GUARDROOM_REF


def step_out_and_back(game: GameSession):
    """Leave the guardroom west and walk back in. Returns the result of walking back in."""
    assert game.execute(MoveParty(direction=Direction.WEST)).accepted
    result = game.execute(MoveParty(direction=Direction.EAST))
    assert result.accepted
    return result


def engage(game: GameSession) -> None:
    if game.mode is SessionMode.ENCOUNTER:
        assert game.execute(EngageBattle()).accepted
    assert game.mode is SessionMode.BATTLE
    assert game.battle is not None


def guardroom_in(game: GameSession, mode: str) -> None:
    """Walk into the guardroom, and engage the goblins there when `mode` is `"battle"`."""
    walk_into_guardroom(game)
    if mode == "battle":
        engage(game)


def spawn_goblins(game: GameSession) -> None:
    assert game.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=60)).accepted
    assert game.mode in (SessionMode.ENCOUNTER, SessionMode.BATTLE)
    assert game.encounter.kind == "spawned"
    assert game.encounter.area_ref is None


def battle_with_one_slain(game: GameSession):
    """Spawn two skeletons at sword's reach and fight until exactly one is slain.

    Skeletons never check morale, so the one left standing is neither fleeing nor
    routed. Returns the group, the slain skeleton, and the standing one.
    """
    assert game.execute(SpawnMonsters(template_id="skeleton", count_fixed=2, distance_feet=0)).accepted
    engage(game)
    group = game.encounter.groups[0]
    front_rank = [member.id for member in game.party.members[:2]]
    for _ in range(10):
        declarations = tuple(
            BattleDeclaration(character_id=member.id, action="attack", target_group_id=group.id)
            if member.id in front_rank
            else BattleDeclaration(character_id=member.id, action="hold")
            for member in game.party.living_members()
        )
        assert game.execute(ResolveBattleRound(declarations=declarations)).accepted
        assert game.mode is SessionMode.BATTLE, "the fight must still be on when one skeleton falls"
        monsters = [game.combatant(monster_id) for monster_id in group.monster_ids]
        slain = [monster for monster in monsters if has_condition(monster, Condition.DEAD)]
        if slain:
            break
    standing = [monster for monster in monsters if not has_condition(monster, Condition.DEAD)]
    assert len(slain) == 1 and len(standing) == 1
    assert not group.fleeing and not group.fled
    return group, slain[0], standing[0]


def stream_states(game: GameSession) -> dict:
    return {key: state.model_dump(mode="json") for key, state in game.streams.export_states().items()}


def state_but_the_logs(game: GameSession) -> dict:
    """Every state block, the clock and the RNG streams among them, without the two logs."""
    state = session_state(game)
    state.pop("command_log")
    state.pop("event_log")
    return state


def ended_events(events) -> list[EncounterEndedEvent]:
    return [event for event in events if isinstance(event, EncounterEndedEvent)]


def defeated_events(events) -> list[MonsterDefeatedEvent]:
    return [event for event in events if isinstance(event, MonsterDefeatedEvent)]


def codes(events) -> list[str]:
    return [event.code for event in events]


def hero(name: str = "Hild", class_id: str = "fighter", seed: int = 7):
    """Roll a first-level character, as the issue's `common.py` helper does."""
    stream = RngStreams(master_seed=seed).get(CHARACTER_CREATION_STREAM)
    return create_character(
        name=name, class_id=class_id, alignment=Alignment.LAWFUL, ruleset=Ruleset(), stream=stream
    ).character


def issue_session(members) -> GameSession:
    """The issue's `session` helper: a two-cell corridor, entered at (0, 0)."""
    level = LevelSpec(
        number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)}, areas=()
    )
    crypt = DungeonSpec(id="crypt", name="Crypt", levels=(level,))
    adventure = Adventure(name="A", town=TownSpec(name="Threshold", travel_turns={"crypt": 1}), dungeons=(crypt,))
    game = GameSession.new(Party(members=list(members)), adventure, seed=SEED)
    assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
    return game


# ---------------------------------------------------------------------- the command model


class TestTheCommandModel:
    def test_fields_and_defaults(self):
        command = end("parley")
        assert command.command_type == "end_encounter"
        assert command.outcome == "parley"
        assert command.resolve_area is False
        assert end("departed", resolve_area=True).resolve_area is True

    @pytest.mark.parametrize("outcome", ["victory", "evaded", "escaped", "turned", "", "PARLEY"])
    def test_outcome_is_parley_or_departed_only(self, outcome):
        with pytest.raises(ValidationError):
            end(outcome)

    def test_outcome_is_required(self):
        from osrlib.crawl.commands import EndEncounter

        with pytest.raises(ValidationError):
            EndEncounter()

    def test_the_command_is_frozen(self):
        command = end("parley")
        with pytest.raises(ValidationError):
            command.outcome = "departed"

    @pytest.mark.parametrize("command_fields", [dict(outcome="parley"), dict(outcome="departed", resolve_area=True)])
    def test_round_trip_through_json(self, command_fields):
        from osrlib.crawl.commands import EndEncounter

        command = EndEncounter(**command_fields)
        payload = json.loads(json.dumps(command.model_dump(mode="json")))
        assert payload["command_type"] == "end_encounter"
        assert payload["outcome"] == command_fields["outcome"]
        assert payload["resolve_area"] is command_fields.get("resolve_area", False)
        parsed = parse_command(payload)
        assert parsed == command
        assert type(parsed) is EndEncounter
        assert TypeAdapter(AnyCommand).validate_python(payload) == command

    def test_a_malformed_payload_raises_on_parse(self):
        with pytest.raises(ContentValidationError):
            parse_command({"command_type": "end_encounter", "outcome": "victory"})

    def test_it_is_appended_to_the_command_census(self):
        from osrlib.crawl.commands import EndEncounter

        assert ALL_COMMAND_CLASSES.index(EndEncounter) == COMMAND_INDEX

    def test_modes_are_encounter_and_battle(self):
        from osrlib.crawl.commands import EndEncounter

        assert EndEncounter.allowed_modes == frozenset({SessionMode.ENCOUNTER, SessionMode.BATTLE})


# ---------------------------------------------------------------------- closing an encounter


class TestClosingTheEncounter:
    @pytest.mark.parametrize("mode", LIVE_MODES)
    @pytest.mark.parametrize("outcome", OUTCOMES)
    def test_closes_with_the_outcome_and_returns_to_exploring(self, outcome, mode):
        game = new_game()
        guardroom_in(game, mode)
        started, before = game.encounter.started_round, game.clock.rounds
        result = game.execute(end(outcome))
        assert result.accepted
        assert game.mode is SessionMode.EXPLORING
        assert game.encounter is None
        assert game.battle is None
        (ended,) = ended_events(result.events)
        assert ended.outcome == outcome
        assert ended.visibility is Visibility.PLAYER
        # The clock moves as `end_encounter` moves it: to the next turn boundary, and to
        # at least one full turn after the encounter opened.
        boundary = -(-before // ROUNDS_PER_TURN) * ROUNDS_PER_TURN
        assert game.clock.rounds == max(boundary, started + ROUNDS_PER_TURN)
        assert game.odometer_thirds == 0
        assert game.command_log[-1] == end(outcome)

    @pytest.mark.parametrize("outcome", OUTCOMES)
    def test_closes_a_spawned_encounter(self, outcome):
        game = new_game()
        spawn_goblins(game)
        result = game.execute(end(outcome))
        assert result.accepted
        assert game.mode is SessionMode.EXPLORING
        assert game.encounter is None and game.battle is None
        assert [event.outcome for event in ended_events(result.events)] == [outcome]

    @pytest.mark.parametrize("outcome", OUTCOMES)
    def test_takes_the_same_path_as_end_encounter_in_an_encounter(self, outcome):
        # Two sessions on one seed reach the same encounter. One closes it with the
        # command, the other with the kernel call the command replaces. Every state
        # block, the clock and the streams among them, must come out the same, and so
        # must the events.
        by_command, by_kernel = new_game(), new_game()
        walk_into_guardroom(by_command)
        walk_into_guardroom(by_kernel)
        result = by_command.execute(end(outcome))
        assert result.accepted
        kernel_events = encounter_module.end_encounter(by_kernel, outcome)
        assert [event.model_dump(mode="json") for event in result.events] == [
            event.model_dump(mode="json") for event in kernel_events
        ]
        # A neutral command on both runs the session's post-command bookkeeping, which
        # the bare kernel call skipped.
        for game in (by_command, by_kernel):
            assert game.execute(RecordNote(text="The goblins let the party by.")).accepted
        assert state_but_the_logs(by_command) == state_but_the_logs(by_kernel)

    @pytest.mark.parametrize("outcome", OUTCOMES)
    def test_takes_the_same_path_as_end_encounter_in_a_battle(self, outcome):
        # In a battle the command also closes the battle, as the session does before
        # it calls `end_encounter` when a battle ends. Whether a `BattleEndedEvent`
        # leads the result is left open, so only the tail of the events is compared.
        by_command, by_kernel = new_game(), new_game()
        guardroom_in(by_command, "battle")
        guardroom_in(by_kernel, "battle")
        result = by_command.execute(end(outcome))
        assert result.accepted
        by_kernel.battle = None
        kernel_events = encounter_module.end_encounter(by_kernel, outcome)
        tail = [event.model_dump(mode="json") for event in result.events][-len(kernel_events) :]
        assert tail == [event.model_dump(mode="json") for event in kernel_events]
        for game in (by_command, by_kernel):
            assert game.execute(RecordNote(text="The goblins lower their spears.")).accepted
        assert state_but_the_logs(by_command) == state_but_the_logs(by_kernel)

    def test_ends_a_chase(self):
        # Hellhounds run exactly as fast as the party, so the chase would run to the
        # round cap. The referee calls it off instead.
        game = new_game()
        hounds = game.spawn("hellhound_3", 2)
        encounter_module.start_encounter(
            game,
            groups=[("hellhound_3", hounds)],
            kind="spawned",
            distance_feet=100,
            pinned_stance=ReactionResult.HOSTILE,
            party_aware=True,
        )
        assert game.execute(Evade(drop="none")).accepted
        assert game.mode is SessionMode.ENCOUNTER
        assert game.encounter.pursuit is not None
        result = game.execute(end("departed"))
        assert result.accepted
        assert game.mode is SessionMode.EXPLORING
        assert game.encounter is None
        assert [event.outcome for event in ended_events(result.events)] == ["departed"]


class TestDefeatAndExperience:
    @pytest.mark.parametrize("mode", LIVE_MODES)
    @pytest.mark.parametrize("outcome", OUTCOMES)
    def test_monsters_left_standing_are_not_defeated(self, outcome, mode):
        game = new_game()
        guardroom_in(game, mode)
        goblins = [game.combatant(monster_id) for monster_id in game.encounter.groups[0].monster_ids]
        result = game.execute(end(outcome))
        assert result.accepted
        assert defeated_events(result.events) == []
        assert game.defeated_monsters == []
        assert not any(has_condition(goblin, Condition.DEAD) for goblin in goblins)

    @pytest.mark.parametrize("outcome", OUTCOMES)
    def test_a_peaceful_close_awards_no_experience_when_awards_are_immediate(self, outcome):
        game = new_game(ruleset=Ruleset(xp_award_timing=XpAwardTiming.IMMEDIATE))
        walk_into_guardroom(game)
        xp_before = [member.xp for member in game.party.members]
        result = game.execute(end(outcome))
        assert result.accepted
        assert [member.xp for member in game.party.members] == xp_before
        assert defeated_events(result.events) == []
        assert game.defeated_monsters == []

    def test_a_monster_slain_earlier_in_the_battle_is_defeated(self):
        game = new_game(seed=ONE_SLAIN_SEED)
        _group, slain, standing = battle_with_one_slain(game)
        assert game.defeated_monsters == []
        result = game.execute(end("departed"))
        assert result.accepted
        (defeated,) = defeated_events(result.events)
        assert defeated.monster_id == slain.id
        assert defeated.outcome == "slain"
        assert defeated.template_id == "skeleton"
        assert defeated.xp == slain.template.xp
        assert [(record.monster_id, record.outcome, record.xp) for record in game.defeated_monsters] == [
            (slain.id, "slain", slain.template.xp)
        ]
        assert standing.id not in {record.monster_id for record in game.defeated_monsters}
        assert codes(result.events).index("battle.monster.defeated") < codes(result.events).index("encounter.ended")
        assert [event.outcome for event in ended_events(result.events)] == ["departed"]
        assert game.mode is SessionMode.EXPLORING
        assert game.battle is None and game.encounter is None


# ---------------------------------------------------------------------- keyed areas


class TestResolvingTheArea:
    @pytest.mark.parametrize("mode", LIVE_MODES)
    @pytest.mark.parametrize("outcome", OUTCOMES)
    def test_without_the_flag_the_guards_are_back_next_time(self, outcome, mode):
        game = new_game()
        guardroom_in(game, mode)
        first_guards = list(game.encounter.groups[0].monster_ids)
        assert game.execute(end(outcome)).accepted
        assert game.dungeon_state.resolved_encounters == []
        result = step_out_and_back(game)
        assert "encounter.started" in codes(result.events)
        assert game.mode is SessionMode.ENCOUNTER
        assert game.encounter.area_ref == GUARDROOM_REF
        assert set(game.encounter.groups[0].monster_ids).isdisjoint(first_guards)

    @pytest.mark.parametrize("mode", LIVE_MODES)
    @pytest.mark.parametrize("outcome", OUTCOMES)
    def test_with_the_flag_walking_back_in_starts_nothing(self, outcome, mode):
        game = new_game()
        guardroom_in(game, mode)
        assert game.execute(end(outcome, resolve_area=True)).accepted
        assert game.dungeon_state.resolved_encounters == [GUARDROOM_REF]
        result = step_out_and_back(game)
        assert "encounter.started" not in codes(result.events)
        assert game.mode is SessionMode.EXPLORING
        assert game.encounter is None
        assert game.dungeon_state.resolved_encounters == [GUARDROOM_REF]

    def test_the_flag_resolves_the_area_through_the_same_path_as_end_encounter(self):
        # The flag adds one thing to a plain close: the area's reference, once.
        by_command, by_kernel = new_game(), new_game()
        walk_into_guardroom(by_command)
        walk_into_guardroom(by_kernel)
        assert by_command.execute(end("parley", resolve_area=True)).accepted
        encounter_module.end_encounter(by_kernel, "parley")
        by_kernel.dungeon_state.resolved_encounters.append(GUARDROOM_REF)
        for game in (by_command, by_kernel):
            assert game.execute(RecordNote(text="The toll is paid.")).accepted
        assert state_but_the_logs(by_command) == state_but_the_logs(by_kernel)

    @pytest.mark.parametrize("outcome", OUTCOMES)
    def test_the_flag_does_nothing_for_a_spawned_encounter(self, outcome):
        game = new_game()
        spawn_goblins(game)
        before = list(game.dungeon_state.resolved_encounters)
        result = game.execute(end(outcome, resolve_area=True))
        assert result.accepted
        assert game.dungeon_state.resolved_encounters == before == []
        assert game.mode is SessionMode.EXPLORING

    def test_the_flag_never_resolves_the_area_the_party_stands_in_for_a_spawned_encounter(self):
        # The party parleys past the guardroom's goblins without resolving it, so it
        # stands in an unresolved keyed area. A spawned encounter there didn't come
        # from the area, so closing it with the flag leaves the area alone.
        game = new_game()
        walk_into_guardroom(game)
        assert game.execute(end("parley")).accepted
        spawn_goblins(game)
        assert game.execute(end("departed", resolve_area=True)).accepted
        assert game.dungeon_state.resolved_encounters == []
        result = step_out_and_back(game)
        assert "encounter.started" in codes(result.events)
        assert game.encounter.area_ref == GUARDROOM_REF


# ---------------------------------------------------------------------- refusals


class TestRefusals:
    @pytest.mark.parametrize("resolve_area", [False, True])
    @pytest.mark.parametrize("mode", ["town", "exploring", "game_over", "victory"])
    def test_refused_outside_an_encounter_at_no_cost(self, mode, resolve_area):
        game = new_game(enter=mode != "town")
        if mode in ("game_over", "victory"):
            game.mode = SessionMode(mode)
        assert game.mode.value == mode
        before, streams, rounds = state_but_the_logs(game), stream_states(game), game.clock.rounds
        logged, events_logged = list(game.command_log), len(game.event_log)
        result = game.execute(end("parley", resolve_area=resolve_area))
        assert not result.accepted
        assert result.events == ()
        assert [rejection.code for rejection in result.rejections] == ["session.command.wrong_mode"]
        assert result.rejections[0].params == {"command": "end_encounter", "mode": mode}
        assert stream_states(game) == streams
        assert game.clock.rounds == rounds
        assert state_but_the_logs(game) == before
        assert game.command_log == logged
        assert len(game.event_log) == events_logged

    def test_refused_in_an_unresolved_keyed_area_once_its_encounter_is_over(self):
        # The flag can't reach back to an area whose encounter already closed.
        game = new_game()
        walk_into_guardroom(game)
        assert game.execute(end("parley")).accepted
        assert game.mode is SessionMode.EXPLORING
        before = state_but_the_logs(game)
        result = game.execute(end("parley", resolve_area=True))
        assert not result.accepted
        assert result.rejections[0].code == "session.command.wrong_mode"
        assert state_but_the_logs(game) == before
        assert game.dungeon_state.resolved_encounters == []


# ---------------------------------------------------------------------- views


class TestViews:
    @pytest.mark.parametrize("mode", LIVE_MODES)
    def test_both_views_show_the_encounter_closed(self, mode):
        game = new_game()
        guardroom_in(game, mode)
        assert game.view(Visibility.PLAYER).encounter is not None
        assert game.execute(end("parley", resolve_area=True)).accepted
        player = game.view(Visibility.PLAYER)
        assert player.mode == "exploring"
        assert player.encounter is None
        referee = game.view(Visibility.REFEREE)
        assert referee.mode is SessionMode.EXPLORING
        assert referee.encounter is None
        assert referee.battle is None
        assert referee.defeated_monsters == ()
        assert referee.dungeon_state.resolved_encounters == [GUARDROOM_REF]
        assert referee.command_log[-1] == end("parley", resolve_area=True)


# ---------------------------------------------------------------------- persistence


class TestSaveAndLoad:
    @pytest.mark.parametrize("mode", LIVE_MODES)
    def test_a_restored_session_closes_the_encounter_the_same_way(self, mode):
        game = new_game()
        guardroom_in(game, mode)
        restored = load_game(json.loads(json.dumps(save_game(game))))
        assert restored.execute(end("parley", resolve_area=True)).accepted
        assert game.execute(end("parley", resolve_area=True)).accepted
        assert session_state(restored) == session_state(game)
        assert restored.mode is SessionMode.EXPLORING

    def test_the_close_and_the_resolved_area_survive_a_save(self):
        game = new_game()
        walk_into_guardroom(game)
        assert game.execute(end("departed", resolve_area=True)).accepted
        restored = load_game(json.loads(json.dumps(save_game(game))))
        assert session_state(restored) == session_state(game)
        assert restored.command_log[-1] == end("departed", resolve_area=True)
        assert restored.mode is SessionMode.EXPLORING
        assert restored.dungeon_state.resolved_encounters == [GUARDROOM_REF]
        result = step_out_and_back(restored)
        assert "encounter.started" not in codes(result.events)
        assert restored.encounter is None


class TestReplay:
    def test_the_issue_reproduction_replays_with_the_command(self):
        # Issue #167, item 23, rewritten to close the encounter with the command
        # instead of the bare `end_encounter` call.
        hild = hero()
        start = party_to_document([hild])
        game = issue_session([hild])
        game.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=60))
        assert game.mode in (SessionMode.ENCOUNTER, SessionMode.BATTLE)
        # The party talks its way past, and the referee closes the encounter with a command.
        assert game.execute(end("parley")).accepted
        assert game.mode is SessionMode.EXPLORING
        assert [type(command).__name__ for command in game.command_log] == [
            "EnterDungeon",
            "SpawnMonsters",
            "EndEncounter",
        ]
        replayed = replay_game(SEED, start, game.adventure, Ruleset(), game.command_log)
        assert replayed.mode is SessionMode.EXPLORING
        assert replayed.encounter is None
        assert replayed.clock.rounds == game.clock.rounds
        assert session_state(replayed) == session_state(game)

    @pytest.mark.parametrize("mode", LIVE_MODES)
    def test_a_resolved_keyed_area_replays_from_the_serialized_log(self, mode):
        game = new_game()
        guardroom_in(game, mode)
        assert game.execute(end("parley", resolve_area=True)).accepted
        step_out_and_back(game)
        assert game.encounter is None
        serialized = json.loads(json.dumps([command.model_dump(mode="json") for command in game.command_log]))
        replayed = replay_game(SEED, party_document(), toll_adventure(), Ruleset(), serialized)
        assert replayed.dungeon_state.resolved_encounters == [GUARDROOM_REF]
        assert session_state(replayed) == session_state(game)

    def test_an_unresolved_close_replays_into_the_second_meeting(self):
        game = new_game()
        walk_into_guardroom(game)
        assert game.execute(end("departed")).accepted
        step_out_and_back(game)
        assert game.mode is SessionMode.ENCOUNTER
        replayed = replay_game(SEED, party_document(), toll_adventure(), Ruleset(), game.command_log)
        assert replayed.mode is SessionMode.ENCOUNTER
        assert session_state(replayed) == session_state(game)

    def test_a_battle_closed_after_a_kill_replays_with_its_defeat_record(self):
        game = new_game(seed=ONE_SLAIN_SEED)
        _group, slain, _standing = battle_with_one_slain(game)
        assert game.execute(end("departed")).accepted
        replayed = replay_game(ONE_SLAIN_SEED, party_document(), toll_adventure(), Ruleset(), game.command_log)
        assert [record.monster_id for record in replayed.defeated_monsters] == [slain.id]
        assert session_state(replayed) == session_state(game)


# ---------------------------------------------------------------------- authored consequences


class TestAsAnAuthoredConsequence:
    """The spec's consequence surface is every referee command but the three exclusions it names."""

    def test_it_is_a_consequence_class(self):
        from osrlib.crawl.commands import EndEncounter

        assert EndEncounter in CONSEQUENCE_COMMAND_CLASSES
        command = end("parley", resolve_area=True)
        assert TypeAdapter(ConsequenceCommand).validate_python(command.model_dump(mode="json")) == command

    def test_a_trigger_pays_the_toll_and_resolves_the_guardroom(self):
        from osrlib.crawl.commands import EndEncounter
        from osrlib.crawl.interpreter import Interpreter

        toll = TriggerSpec(
            id="toll-paid",
            when=FlagSetPattern(key=TOLL_FLAG, value="paid"),
            consequences=(EndEncounter(outcome="parley", resolve_area=True),),
        )
        adventure = toll_adventure(triggers=(toll,))
        game = new_game(adventure=adventure)
        game.register_listener(Interpreter(game))
        walk_into_guardroom(game)
        result = game.execute(SetFlag(key=TOLL_FLAG, value="paid"))
        assert result.accepted
        assert [event.outcome for event in ended_events(result.events)] == ["parley"]
        assert game.mode is SessionMode.EXPLORING
        assert game.dungeon_state.resolved_encounters == [GUARDROOM_REF]
        issued = game.command_log[-1]
        assert isinstance(issued, EndEncounter)
        assert issued.source == "trigger:toll-paid"
        # The trigger's close is in the log, so a replay with no listener reproduces it.
        replayed = replay_game(SEED, party_document(), adventure, Ruleset(), game.command_log)
        assert replayed.dungeon_state.resolved_encounters == [GUARDROOM_REF]
        assert replayed.mode is SessionMode.EXPLORING
        result = step_out_and_back(game)
        assert "encounter.started" not in codes(result.events)
