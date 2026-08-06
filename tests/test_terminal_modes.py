"""The terminal modes: `game_over` and `victory`.

Three contracts live here. The legality census — which commands a session that has
ended still executes — asserted both as declared data and at the executed seam.
The centralized wipe check, which routes every party wipe to `game_over` whatever
killed the party, and leaves a concluded session alone. And the wiped-party guards
that keep a mid-command procedure from starting something new for corpses.

Nothing in this phase transitions *into* `victory` — the entrance arrives with the
authored quest layer — so these tests assign `session.mode` directly, the same
direct-state posture the battle tests use for hit points.
"""

import json

import pytest

from crawl_fixtures import build_adventure, build_chute_adventure, build_gas_trap_adventure, build_party
from osrlib.core.clock import TimeUnit
from osrlib.core.effects import Condition, EffectDefinition
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.commands import (
    ALL_COMMAND_CLASSES,
    AdvanceTime,
    AwardXP,
    EnterDungeon,
    Evade,
    GrantCoins,
    GrantItem,
    MoveParty,
    PlaceParty,
    PurchaseHealing,
    Rest,
    RollDice,
    SessionMode,
    SetFlag,
    SpawnMonsters,
    SpawnNpcParty,
    Wait,
)
from osrlib.crawl.dungeon import Direction
from osrlib.crawl.session import DeprivationState, GameSession
from osrlib.persistence import load_game, save_game, session_state
from test_commands import REFEREE_COMMANDS, RESUME_PLAY_CARVE_OUTS, sample_command

TERMINAL_MODES = frozenset({SessionMode.GAME_OVER, SessionMode.VICTORY})

PLAY_COMMAND_CLASSES = tuple(
    command_class
    for command_class in ALL_COMMAND_CLASSES
    if command_class.model_fields["command_type"].default not in REFEREE_COMMANDS
)

# The seed whose 2-in-6 spring die fires when the party steps into the gas room,
# so the trap wipe is scripted rather than lucky (the phase golden's seed).
GAS_TRAP_SEED = 20_260_806

POISON_ONSET = EffectDefinition(
    kind="poison_onset",
    duration_unit=TimeUnit.ROUND,
    duration_amount=1,
    expiry="death",
    condition=Condition.POISONED,
)
"""A delayed poison on every member: the wipe that arrives under a referee's clock."""


def make_session(seed: int = 11) -> GameSession:
    return GameSession.new(build_party(), build_adventure(), seed=seed)


def poison_everyone(session: GameSession) -> None:
    """Attach the delayed poison to every member; one round of clock finishes them."""
    for member in session.party.members:
        session.ledger.attach(
            POISON_ONSET,
            member.id,
            clock=session.clock,
            allocator=session.allocator,
            registry=session.registry(),
        )


def trap_wipe_session(seed: int = GAS_TRAP_SEED) -> tuple[GameSession, list]:
    """Walk the party into the gas room; the spring kills all four."""
    session = GameSession.new(build_party(), build_gas_trap_adventure(), seed=seed)
    assert session.execute(EnterDungeon(dungeon_id="vault")).accepted
    result = session.execute(MoveParty(direction=Direction.EAST))
    assert result.accepted
    return session, list(result.events)


def stream_states(session: GameSession) -> dict:
    return {key: state.model_dump(mode="json") for key, state in session.streams.export_states().items()}


def game_over_events(events) -> list:
    return [event for event in events if getattr(event, "code", None) == "session.game_over"]


def assert_session_ended(session: GameSession, events) -> None:
    """The shape of every wipe: the ending last, no play state left, and no encore."""
    assert session.mode is SessionMode.GAME_OVER
    assert session.battle is None and session.encounter is None
    assert getattr(events[-1], "code", None) == "session.game_over"
    assert events[-1].reason == "the party has fallen"
    assert len(game_over_events(events)) == 1
    # The trigger is the death, not the state: nothing among the corpses ends the
    # session a second time.
    again = session.execute(RollDice(expression="1d6"))
    assert again.accepted
    assert not game_over_events(again.events)
    assert session.mode is SessionMode.GAME_OVER


class TestTheLegalityCensus:
    """Terminal-mode membership follows referee-ness, with three exceptions."""

    def test_terminal_names_exactly_game_over_and_victory(self):
        assert {mode for mode in SessionMode if mode.terminal} == TERMINAL_MODES

    @pytest.mark.parametrize("command_class", PLAY_COMMAND_CLASSES, ids=lambda cls: cls.__name__)
    def test_play_commands_are_illegal_in_both_terminal_modes(self, command_class):
        assert not command_class.allowed_modes & TERMINAL_MODES

    def test_referee_commands_are_legal_in_both_terminal_modes_but_the_carve_outs(self):
        for command_class in ALL_COMMAND_CLASSES:
            command_type = command_class.model_fields["command_type"].default
            if command_type not in REFEREE_COMMANDS:
                continue
            withheld = RESUME_PLAY_CARVE_OUTS.get(command_type, frozenset())
            assert command_class.allowed_modes & TERMINAL_MODES == TERMINAL_MODES - withheld, command_class.__name__

    def test_the_carve_outs_are_the_three_commands_that_resume_play(self):
        # PlaceParty keeps game_over — the salvage door — and loses victory alone;
        # spawning opens an encounter, which no ended session gets.
        assert PlaceParty.allowed_modes & TERMINAL_MODES == frozenset({SessionMode.GAME_OVER})
        assert not SpawnMonsters.allowed_modes & TERMINAL_MODES
        assert not SpawnNpcParty.allowed_modes & TERMINAL_MODES


class TestVictoryAtTheExecutedSeam:
    """The declared census again, this time through `execute`."""

    @pytest.mark.parametrize("command_class", PLAY_COMMAND_CLASSES, ids=lambda cls: cls.__name__)
    def test_every_play_command_rejects_wrong_mode_in_victory(self, command_class):
        session = make_session()
        session.mode = SessionMode.VICTORY
        result = session.execute(sample_command(command_class))
        assert not result.accepted
        assert [rejection.code for rejection in result.rejections] == ["session.command.wrong_mode"]
        assert result.rejections[0].params["mode"] == "victory"
        assert not session.command_log

    def test_the_referee_surface_still_executes_in_victory(self):
        session = make_session()
        session.mode = SessionMode.VICTORY
        accepted = [
            GrantItem(character_id="character-0001", item_id="torch"),
            AwardXP(character_id="character-0001", amount=100),
            SetFlag(key="idol_returned", value=True),
            AdvanceTime(n=1, unit="turn"),
            RollDice(expression="2d6"),
        ]
        for command in accepted:
            assert session.execute(command).accepted, command.command_type
        assert session.mode is SessionMode.VICTORY
        assert len(session.command_log) == len(accepted)

    def test_the_resume_play_commands_reject_in_victory(self):
        session = make_session()
        session.mode = SessionMode.VICTORY
        for command in (
            PlaceParty(location={"kind": "town"}),
            SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=30),
            SpawnNpcParty(party_kind="basic", distance_feet=30),
        ):
            result = session.execute(command)
            assert not result.accepted, command.command_type
            assert result.rejections[0].code == "session.command.wrong_mode"
        assert session.mode is SessionMode.VICTORY


class TestNonBattleWipes:
    """Every entrance to game-over, one test each, plus the edge-trigger rule."""

    def test_a_trap_wipe_ends_the_session(self):
        session, events = trap_wipe_session()
        assert any(getattr(event, "code", None) == "exploration.trap.sprung" for event in events)
        assert_session_ended(session, events)

    def test_a_deprivation_wipe_ends_the_session(self):
        session = GameSession.new(
            build_party(),
            build_adventure(wandering_chance=0),
            seed=3,
            ruleset=Ruleset(deprivation_penalties=True),
        )
        assert session.execute(EnterDungeon(dungeon_id="delve")).accepted
        for member in session.party.members:
            member.current_hp = 1
            session.deprivation[str(member.id)] = DeprivationState(food_days=3, water_days=3)
        result = session.execute(AdvanceTime(n=1, unit=TimeUnit.DAY))
        assert result.accepted
        assert any(getattr(event, "code", None) == "combat.death.died" for event in result.events)
        assert_session_ended(session, list(result.events))

    def test_a_town_wipe_ends_the_session(self):
        session = make_session()
        poison_everyone(session)
        result = session.execute(AdvanceTime(n=1, unit=TimeUnit.TURN))
        assert result.accepted
        assert session.dungeon_state.location.kind == "town"
        assert_session_ended(session, list(result.events))

    def test_a_lost_battle_and_a_trap_wipe_end_the_session_identically(self):
        from osrlib.crawl.commands import ResolveBattleRound
        from test_battle import battle_session, hold_all

        battle = battle_session(template_id="ogre", count=2, distance=5, seed=12)
        for member in battle.party.members:
            member.current_hp = 1
        rounds = 0
        while battle.mode is SessionMode.BATTLE and rounds < 40:
            rounds += 1
            result = battle.execute(ResolveBattleRound(declarations=hold_all(battle)))
            assert result.accepted
        codes = [getattr(event, "code", None) for event in result.events]
        assert codes[-2:] == ["battle.ended.defeat", "session.game_over"]
        _, trap_events = trap_wipe_session()
        assert game_over_events(result.events) == game_over_events(trap_events)


class TestVictoryIsSticky:
    def test_a_death_after_victory_leaves_the_mode_alone(self):
        session = make_session()
        poison_everyone(session)
        session.mode = SessionMode.VICTORY
        result = session.execute(AdvanceTime(n=1, unit=TimeUnit.TURN))
        assert result.accepted
        assert any(getattr(event, "code", None) == "combat.death.died" for event in result.events)
        assert not session.party.living_members()
        assert session.mode is SessionMode.VICTORY
        assert not game_over_events(result.events)


class TestTheSalvageFlow:
    """Out of game-over the documented way: to town, to the temple, back down."""

    def test_the_fallen_party_is_carried_to_town_and_raised(self):
        session, _ = trap_wipe_session()
        placed = session.execute(PlaceParty(location={"kind": "town"}))
        assert placed.accepted
        assert session.mode is SessionMode.TOWN
        # The level-versus-edge distinction: a dead party in town is a state, and
        # states do not re-trigger the ending.
        assert not game_over_events(placed.events)
        assert not session.party.living_members()
        member = session.party.members[0]
        assert session.execute(GrantCoins(character_id=str(member.id), coins={"gp": 1500})).accepted
        raised = session.execute(PurchaseHealing(character_id=str(member.id), service="raise_dead"))
        assert raised.accepted, [rejection.code for rejection in raised.rejections]
        assert session.party.living_members() == [member]
        assert session.execute(EnterDungeon(dungeon_id="vault")).accepted
        assert session.mode is SessionMode.EXPLORING

    def test_the_salvage_clock_advances_from_both_legs(self):
        session, _ = trap_wipe_session()
        before = session.clock.rounds
        assert session.execute(AdvanceTime(n=6, unit=TimeUnit.TURN)).accepted
        in_game_over = session.clock.rounds - before
        assert in_game_over == 6 * 60, "the revival window keeps elapsing while the party lies fallen"
        assert session.execute(PlaceParty(location={"kind": "town"})).accepted
        before = session.clock.rounds
        assert session.execute(AdvanceTime(n=6, unit=TimeUnit.TURN)).accepted
        # The salvaged leg: a dead party in a non-terminal mode, the flow's own
        # next state, and the clock it needs still runs.
        assert session.clock.rounds - before == 6 * 60

    def test_spawning_is_refused_in_game_over(self):
        session, _ = trap_wipe_session()
        result = session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=30))
        assert not result.accepted
        assert result.rejections[0].code == "session.command.wrong_mode"
        assert result.rejections[0].params["mode"] == "game_over"
        assert session.encounter is None and session.battle is None


class TestWipedPartyGuards:
    """Once nobody lives, no procedure starts anything new inside the same command."""

    def encounter_session(self, template_id: str = "goblin", *, seed: int = 5) -> GameSession:
        """A hostile encounter open on the dungeon grid, the party still standing."""
        from osrlib.core.tables import ReactionResult
        from osrlib.crawl import encounter as encounter_module

        session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=seed)
        assert session.execute(EnterDungeon(dungeon_id="delve")).accepted
        instances = session.spawn(template_id, 2)
        encounter_module.start_encounter(
            session,
            groups=[(template_id, instances)],
            kind="spawned",
            distance_feet=60,
            pinned_stance=ReactionResult.HOSTILE,
            party_aware=True,
        )
        assert session.mode is SessionMode.ENCOUNTER
        return session

    def test_a_chute_that_kills_en_route_discovers_and_ambushes_nothing(self):
        session = GameSession.new(build_party(), build_chute_adventure(), seed=4)
        assert session.execute(EnterDungeon(dungeon_id="shaft")).accepted
        before = stream_states(session)
        result = session.execute(MoveParty(direction=Direction.EAST))
        assert result.accepted
        assert any(getattr(event, "code", None) == "exploration.trap.sprung" for event in result.events)
        # The bodies genuinely moved: the relocation is the wiping command's own
        # bookkeeping, and it still reports itself.
        location = session.dungeon_state.location
        assert (location.level_number, location.position) == (2, (0, 0))
        assert any(getattr(event, "code", None) == "exploration.location.entered" for event in result.events)
        # What never follows: the landing's cache and the landing's goblins.
        assert session.dungeon_state.generated_caches == {}
        assert session.dungeon_state.generated_treasure_areas == []
        assert session.dungeon_state.resolved_encounters == []
        assert session.monsters == {}
        assert session.encounter is None and session.battle is None
        after = stream_states(session)
        assert after.get("treasure") == before.get("treasure"), "corpses generate no treasure"
        assert after.get("wandering") == before.get("wandering"), "corpses spawn no keyed encounter"
        assert session.mode is SessionMode.GAME_OVER

    def test_a_wiped_span_truncates_at_the_wipe_turn(self):
        session = GameSession.new(build_party(), build_adventure(wandering_chance=6), seed=7)
        assert session.execute(EnterDungeon(dungeon_id="delve")).accepted
        poison_everyone(session)
        before_rounds = session.clock.rounds
        before = stream_states(session)
        result = session.execute(Rest(kind="night"))
        assert result.accepted
        # Forty-eight turns were asked for; the party died in the first one.
        assert session.clock.rounds - before_rounds == 60
        assert stream_states(session).get("wandering") == before.get("wandering"), "no wandering roll for the dead"
        assert session.wandering_counter == 0
        assert session.mode is SessionMode.GAME_OVER

    def test_an_encounter_round_that_kills_the_last_member_opens_no_battle(self):
        session = self.encounter_session()
        state = session.encounter
        assert state.hostile_deadline == state.round + 1, "the deadline falls on the round the Wait resolves"
        poison_everyone(session)
        result = session.execute(Wait())
        assert result.accepted
        codes = [getattr(event, "code", None) for event in result.events]
        assert "battle.started" not in codes, "a battle among corpses would end in defeat for a wipe that was no battle"
        assert "battle.ended.defeat" not in codes
        assert_session_ended(session, list(result.events))

    def test_a_pursuit_round_that_kills_the_last_member_resolves_nothing(self):
        session = self.encounter_session(template_id="normal_wolf")
        member = session.party.members[0]
        assert session.execute(GrantCoins(character_id=str(member.id), coins={"gp": 10})).accepted
        poison_everyone(session)
        before = stream_states(session)
        result = session.execute(Evade(drop="treasure"))
        assert result.accepted
        codes = [getattr(event, "code", None) for event in result.events]
        assert "encounter.evasion.pursuit" in codes, "the wolves outrun the party, so the chase begins"
        assert not any(code and code.startswith("encounter.pursuit.") for code in codes)
        assert "battle.started" not in codes
        assert stream_states(session).get("encounter") == before.get("encounter"), "no distraction die for the dead"
        assert session.battle is None and session.encounter is None
        assert session.mode is SessionMode.GAME_OVER


class TestPersistence:
    @pytest.mark.parametrize("mode", sorted(TERMINAL_MODES))
    def test_a_terminal_mode_round_trips_through_a_save(self, mode):
        session = make_session()
        session.mode = mode
        restored = load_game(json.loads(json.dumps(save_game(session))))
        assert restored.mode is mode
        assert session_state(restored) == session_state(session)

    def test_a_loaded_victory_session_still_enforces_its_legality(self):
        session = make_session()
        session.mode = SessionMode.VICTORY
        restored = load_game(json.loads(json.dumps(save_game(session))))
        assert not restored.execute(PlaceParty(location={"kind": "town"})).accepted
        assert restored.execute(SetFlag(key="epilogue", value="told")).accepted
        assert restored.mode is SessionMode.VICTORY
