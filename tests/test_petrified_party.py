"""Acceptance tests for a party turned wholly to stone, which has fallen, so the game ends.

**The gap.** A basilisk that surprises the party gazes at every member as the encounter opens. When every member
fails the save, the whole party is stone. The wipe check ran only after a death and asked only whether any member
lived, and a statue isn't dead. So the session stayed in the encounter, or in a battle where the monsters bit statues
round after round, with nobody able to act.

**The contract.** A member *stands* while they are neither dead nor petrified. When a command's events petrify or kill
a party member and no member is left standing, the session ends in `game_over` with the usual `GameOverEvent`, exactly
as a party wipe by death does: the ending comes last in the result, once, with the reason "the party has fallen", and
any open encounter or battle clears. In a battle, `battle.ended.defeat` comes just before the ending, as it does when
the last member dies. The causes include a gaze at the opening of an encounter or in battle, a petrifying bite or
beak, and a death that leaves only statues behind.

**What doesn't change.** A party with one member standing plays on, even when that member stands behind a front rank
of statues. `Party.living_members()` keeps its meaning: a statue isn't dead, so it still holds its rank and still
counts for every rule that reads the living. The check stays tied to the events, not to the party's state, so carrying
a stone party to town with `PlaceParty` doesn't end the session again. In town, *stone to flesh* cast by a magic-user
who joins with `AddMember` brings a statue back.

**Determinism.** The ending makes no draws, so the random number streams after an all-stone opening match those of
the same opening with one more member who stood apart. Saves load into `game_over`, and a replay ends the same way.
Saving throws are forced where a test needs a particular outcome: a `save_bonus` of +20 makes every save pass and one
of -20 makes every save fail. The replay test can't attach a ward through a command, so it scans seeds for natural
failed saves instead. Each test asserts its own preconditions, so a test that stops exercising its case fails loudly.
"""

import json

import pytest

from osrlib.core.character import Character, party_to_document
from osrlib.core.clock import TimeUnit
from osrlib.core.effects import Condition, has_condition, kill
from osrlib.core.events import ConditionGainedEvent, DeathEvent
from osrlib.core.ruleset import Ruleset
from osrlib.core.spells import MemorizedSpell
from osrlib.crawl.commands import (
    AddMember,
    AdvanceTime,
    CastSpell,
    EngageBattle,
    MoveParty,
    PlaceParty,
    RollDice,
    SessionMode,
    SpawnMonsters,
)
from osrlib.crawl.dungeon import Direction, PartyLocation
from osrlib.crawl.session import GameSession
from osrlib.persistence import load_game, replay_game, save_game, session_state
from test_monster_tactics import (
    ALWAYS_FAIL,
    ALWAYS_PASS,
    SEEDS,
    _battle,
    _blind,
    _character,
    _force_saves,
    _front_rank_ids,
    _members,
    _monster_id,
    _petrify,
    _play,
    _saves,
    _session,
)

QUIET_STANCES = ("indifferent", "friendly")
"""Stances that open no battle and run no later procedure in the opening command."""

MAX_ROUNDS = 40
"""Rounds a battle test plays while it waits for an attack to land."""


# ---------------------------------------------------------------------- helpers


def _petrified_ids(events) -> list[str]:
    """The ids that gained the `petrified` condition in these events, in order."""
    return [
        event.target_id
        for event in events
        if isinstance(event, ConditionGainedEvent) and event.condition == Condition.PETRIFIED.value
    ]


def _game_over_events(events) -> list:
    return [event for event in events if event.code == "session.game_over"]


def _assert_ended(game: GameSession, events) -> None:
    """The shape of every wipe: `game_over`, no play state left, and the ending last and once."""
    assert game.mode is SessionMode.GAME_OVER, f"the session should have ended, but it is in {game.mode.value}"
    assert game.battle is None and game.encounter is None
    assert events[-1].code == "session.game_over"
    assert events[-1].reason == "the party has fallen"
    assert len(_game_over_events(events)) == 1


def _assert_plays_on(game: GameSession, events, mode: SessionMode) -> None:
    assert game.mode is mode
    assert not _game_over_events(events)


def _stance(events) -> list[str]:
    return [event.stance for event in events if event.code == "encounter.stance.changed"]


def _surprised_by_basilisk(
    names: tuple[str, ...] = ("Hild", "Brakk"),
    *,
    save_bonuses: tuple[int, ...] | None = None,
    blind: tuple[int, ...] = (),
    dead: tuple[int, ...] = (),
    stances: tuple[str, ...] = QUIET_STANCES,
    seeds=SEEDS,
) -> tuple[GameSession, list]:
    """A party a basilisk surprises through `SpawnMonsters`, with each member's saves forced, and its events.

    `save_bonuses` gives each member's forced save bonus, and every member fails by default. The members at the
    indexes in `blind` are blinded and those in `dead` are killed before the basilisk arrives. The seed is chosen from
    what happened before any wipe check could run: the party is surprised, the basilisk isn't, and the opening stance
    is one of `stances`.
    """
    bonuses = save_bonuses if save_bonuses is not None else (ALWAYS_FAIL,) * len(names)
    assert len(bonuses) == len(names)
    for seed in seeds:
        game = _session(seed, _members(*names))
        for member, bonus in zip(game.party.members, bonuses, strict=True):
            _force_saves(game, member, bonus)
        for index in blind:
            _blind(game, game.party.members[index])
        for index in dead:
            kill(game.party.members[index])
        result = game.execute(SpawnMonsters(template_id="basilisk", count_fixed=1, distance_feet=60))
        assert result.accepted
        started = next(event for event in result.events if event.code == "encounter.started")
        if not started.party_surprised or started.monsters_surprised:
            continue
        opening = _stance(result.events)
        if not opening or opening[0] not in stances:
            continue
        return game, list(result.events)
    raise AssertionError(f"precondition: no seed let a basilisk surprise the party with a stance in {stances}")


def _all_stone(game: GameSession, events) -> None:
    """Assert that the opening's gaze turned every living member to stone, and killed nobody."""
    for member in game.party.members:
        if has_condition(member, Condition.DEAD):
            continue
        assert [save.code for save in _saves(events, member.id, "paralysis")] == ["combat.save.failed"], (
            f"precondition: {member.id} met the gaze and failed the save"
        )
        assert has_condition(member, Condition.PETRIFIED), f"precondition: {member.id} is stone"
    assert not [event for event in events if isinstance(event, DeathEvent)], "precondition: the gaze killed nobody"


def _stone_party_in_game_over() -> tuple[GameSession, list]:
    """Two members turned to stone by a surprise gaze, in a session that has ended."""
    game, events = _surprised_by_basilisk()
    _all_stone(game, events)
    _assert_ended(game, events)
    return game, events


def _magic_user() -> Character:
    """A magic-user who can cast *stone to flesh*, fresh enough for `AddMember` to take."""
    character = _character("Wenna", "magic_user", level=11, hp=30)
    character.spell_book = ("stone_to_flesh",)
    character.memorized_spells = (MemorizedSpell(spell_id="stone_to_flesh"),)
    return character


# ---------------------------------------------------------------------- the surprise gaze


class TestASurpriseGazeThatPetrifiesEveryone:
    """The playtest's case: a basilisk surprises the party, everyone meets its gaze, and everyone fails."""

    @pytest.mark.parametrize("names", [("Hild",), ("Hild", "Brakk", "Sable")], ids=["alone", "three"])
    def test_the_session_ends_with_no_battle(self, names):
        game, events = _surprised_by_basilisk(names)

        _all_stone(game, events)
        assert "battle.started" not in [event.code for event in events], "precondition: no battle opened"
        _assert_ended(game, events)

    def test_the_session_ends_when_the_basilisk_attacks_at_once(self):
        """The gaze comes as the encounter opens, and the stance carries the encounter into a battle."""
        game, events = _surprised_by_basilisk(stances=("attacks",))

        _all_stone(game, events)
        _assert_ended(game, events)

    def test_a_member_already_dead_doesnt_keep_the_session_open(self):
        """A mix of the dead and the stone: Hild fell earlier, and Brakk and Sable turn to stone."""
        game, events = _surprised_by_basilisk(("Hild", "Brakk", "Sable"), dead=(0,))
        hild, _, _ = game.party.members

        assert has_condition(hild, Condition.DEAD), "precondition: Hild is dead as the encounter opens"
        assert not _saves(events, hild.id), "precondition: the dead meet no gaze"
        _all_stone(game, events)
        _assert_ended(game, events)

    def test_the_ending_makes_no_draws(self):
        """The streams match those of the same opening with a third member who stood apart, blind, and played on."""
        ended, ended_events = _surprised_by_basilisk(("Hild", "Brakk"))
        seed = ended.master_seed
        on, on_events = _surprised_by_basilisk(
            ("Hild", "Brakk", "Sable"), save_bonuses=(ALWAYS_FAIL,) * 3, blind=(2,), seeds=[seed]
        )
        sable = on.party.members[2]

        assert not _saves(on_events, sable.id), "precondition: blind Sable rolled no save"
        assert not has_condition(sable, Condition.PETRIFIED)
        _assert_plays_on(on, on_events, SessionMode.ENCOUNTER)
        _all_stone(ended, ended_events)
        _assert_ended(ended, ended_events)
        assert ended.streams.export_states() == on.streams.export_states()


class TestAMemberStillStandingPlaysOn:
    """Guards: a party with one member who is neither dead nor stone plays on as it did before."""

    def test_a_member_who_passes_the_surprise_gaze_keeps_the_encounter_open(self):
        game, events = _surprised_by_basilisk(("Hild", "Brakk", "Sable"), save_bonuses=(ALWAYS_FAIL,) * 2 + (20,))
        hild, brakk, sable = game.party.members

        assert has_condition(hild, Condition.PETRIFIED) and has_condition(brakk, Condition.PETRIFIED)
        assert [save.code for save in _saves(events, sable.id)] == ["combat.save.passed"], "precondition: Sable passed"
        _assert_plays_on(game, events, SessionMode.ENCOUNTER)
        engage = game.execute(EngageBattle())
        assert engage.accepted
        _assert_plays_on(game, engage.events, SessionMode.BATTLE)

    def test_a_blind_member_who_meets_no_gaze_keeps_the_encounter_open(self):
        game, events = _surprised_by_basilisk(("Hild", "Brakk", "Sable"), blind=(2,))
        hild, brakk, sable = game.party.members

        assert has_condition(hild, Condition.PETRIFIED) and has_condition(brakk, Condition.PETRIFIED)
        assert not _saves(events, sable.id), "precondition: blind Sable met no gaze"
        _assert_plays_on(game, events, SessionMode.ENCOUNTER)

    def test_a_back_rank_member_keeps_the_battle_going_behind_a_front_rank_of_statues(self):
        game = _battle("basilisk", ("Hild", "Brakk", "Sable"))
        hild, brakk, sable = game.party.members
        _force_saves(game, hild, ALWAYS_FAIL)
        _force_saves(game, brakk, ALWAYS_FAIL)
        _force_saves(game, sable, ALWAYS_PASS)
        assert _front_rank_ids(game) == [hild.id, brakk.id], "precondition: Sable stands in the back rank"

        events = _play(game)

        assert sorted(_petrified_ids(events)) == sorted([hild.id, brakk.id]), "precondition: the gaze took the front"
        assert not has_condition(sable, Condition.PETRIFIED)
        _assert_plays_on(game, events, SessionMode.BATTLE)
        later = _play(game)
        _assert_plays_on(game, later, SessionMode.BATTLE)

    def test_a_front_rank_member_who_passes_the_gaze_keeps_the_battle_going(self):
        game = _battle("basilisk")
        hild, brakk = game.party.members
        _force_saves(game, hild, ALWAYS_FAIL)
        _force_saves(game, brakk, ALWAYS_PASS)

        events = _play(game)

        assert _petrified_ids(events) == [hild.id], "precondition: the gaze took Hild alone"
        _assert_plays_on(game, events, SessionMode.BATTLE)

    def test_a_statue_is_still_one_of_the_living(self):
        """`living_members()` keeps its meaning: a statue isn't dead, and it still holds its rank."""
        game = _battle("basilisk", ("Hild", "Brakk", "Sable"))
        hild, brakk, sable = game.party.members
        _petrify(game, hild)

        assert game.party.living_members() == [hild, brakk, sable]
        assert _front_rank_ids(game) == [hild.id, brakk.id]


# ---------------------------------------------------------------------- battle


class TestABattleThatPetrifiesTheLastStandingMember:
    """In a battle, the round that leaves nobody standing ends in defeat, then the session ends."""

    def test_a_gaze_that_takes_the_whole_party_ends_the_session(self):
        game = _battle("basilisk")
        hild, brakk = game.party.members
        for member in (hild, brakk):
            _force_saves(game, member, ALWAYS_FAIL)

        events = _play(game)

        assert sorted(_petrified_ids(events)) == sorted([hild.id, brakk.id]), "precondition: the gaze took both"
        assert not [event for event in events if isinstance(event, DeathEvent)], "precondition: nobody died"
        _assert_ended(game, events)
        assert [event.code for event in events][-2:] == ["battle.ended.defeat", "session.game_over"]

    def test_a_petrifying_beak_that_takes_the_last_member_ends_the_session(self):
        game = _battle("cockatrice", ("Hild",))
        (hild,) = game.party.members
        _force_saves(game, hild, ALWAYS_FAIL)
        cockatrice = _monster_id(game)

        for _ in range(MAX_ROUNDS):
            assert game.mode is SessionMode.BATTLE, "precondition: the battle runs until Hild turns to stone"
            events = _play(game)
            if has_condition(hild, Condition.PETRIFIED):
                break
        else:
            raise AssertionError("precondition: the cockatrice's beak never landed")

        assert _petrified_ids(events) == [hild.id]
        hits = [
            event
            for event in events
            if event.code in ("combat.attack.hit", "combat.attack.auto_hit") and event.attacker_id == cockatrice
        ]
        assert hits, "precondition: a hit from the cockatrice did it"
        _assert_ended(game, events)
        assert [event.code for event in events][-2:] == ["battle.ended.defeat", "session.game_over"]

    def test_a_gaze_that_takes_the_last_member_beside_a_fallen_one_ends_the_session(self):
        """A mix of the dead and the stone: Hild fell earlier, and the gaze takes Brakk."""
        game = _battle("basilisk")
        hild, brakk = game.party.members
        kill(hild)
        _force_saves(game, brakk, ALWAYS_FAIL)
        assert _front_rank_ids(game) == [brakk.id], "precondition: the dead leave the ranks"

        events = _play(game)

        assert _petrified_ids(events) == [brakk.id], "precondition: the gaze took Brakk"
        _assert_ended(game, events)

    def test_a_death_that_leaves_only_statues_ends_the_session(self):
        """A mix of the dead and the stone: Hild is stone, and the goblin kills Brakk, the last member standing."""
        game = _battle("goblin")
        hild, brakk = game.party.members
        _petrify(game, hild)
        brakk.current_hp = 1

        for _ in range(MAX_ROUNDS):
            assert game.mode is SessionMode.BATTLE, "precondition: the battle runs until Brakk falls"
            events = _play(game)
            if has_condition(brakk, Condition.DEAD):
                break
        else:
            raise AssertionError("precondition: the goblin never killed Brakk")

        assert [event.target_id for event in events if isinstance(event, DeathEvent)] == [brakk.id]
        assert has_condition(hild, Condition.PETRIFIED) and not has_condition(hild, Condition.DEAD)
        _assert_ended(game, events)
        assert [event.code for event in events][-2:] == ["battle.ended.defeat", "session.game_over"]


# ---------------------------------------------------------------------- persistence


class TestSavesAndReplays:
    """A stone party's ending is ordinary state: it saves, loads, replays, and refuses play like any other."""

    def test_a_stone_ending_round_trips_through_a_save(self):
        game, _ = _stone_party_in_game_over()

        restored = load_game(json.loads(json.dumps(save_game(game))))

        assert restored.mode is SessionMode.GAME_OVER
        assert restored.battle is None and restored.encounter is None
        assert all(has_condition(member, Condition.PETRIFIED) for member in restored.party.members)
        assert session_state(restored) == session_state(game)

    def test_a_stone_ending_replays(self):
        """Natural failed saves, because a forced save comes from a ward no command can attach."""
        for seed in SEEDS:
            members = _members("Hild")
            party_document = party_to_document(members)
            game = _session(seed, members)
            result = game.execute(SpawnMonsters(template_id="basilisk", count_fixed=1, distance_feet=60))
            assert result.accepted
            started = next(event for event in result.events if event.code == "encounter.started")
            if not started.party_surprised or started.monsters_surprised:
                continue
            (hild,) = game.party.members
            if [save.code for save in _saves(result.events, hild.id, "paralysis")] != ["combat.save.failed"]:
                continue
            assert has_condition(hild, Condition.PETRIFIED), "precondition: Hild is stone"
            _assert_ended(game, list(result.events))

            replayed = replay_game(game.master_seed, party_document, game.adventure, Ruleset(), game.command_log)

            assert replayed.mode is SessionMode.GAME_OVER
            assert [event.model_dump(mode="json") for event in replayed.event_log] == [
                event.model_dump(mode="json") for event in game.event_log
            ]
            assert session_state(replayed) == session_state(game)
            return
        raise AssertionError("precondition: no seed let a basilisk surprise Hild and turn her to stone")

    def test_a_play_command_after_the_ending_is_refused_and_consumes_nothing(self):
        game, _ = _stone_party_in_game_over()
        before = session_state(game)

        result = game.execute(MoveParty(direction=Direction.EAST))

        assert not result.accepted
        assert [rejection.code for rejection in result.rejections] == ["session.command.wrong_mode"]
        assert session_state(game) == before


# ---------------------------------------------------------------------- the way back


class TestTheWayBackFromStone:
    """Carrying the statues to town doesn't end the session again, and *stone to flesh* brings one back there."""

    def test_placing_the_statues_in_town_doesnt_end_the_session_again(self):
        game, _ = _stone_party_in_game_over()

        placed = game.execute(PlaceParty(location=PartyLocation(kind="town")))

        assert placed.accepted
        _assert_plays_on(game, placed.events, SessionMode.TOWN)
        assert all(has_condition(member, Condition.PETRIFIED) for member in game.party.members), (
            "precondition: the party is still stone"
        )
        for command in (AdvanceTime(n=1, unit=TimeUnit.DAY), RollDice(expression="1d6")):
            result = game.execute(command)
            assert result.accepted
            _assert_plays_on(game, result.events, SessionMode.TOWN)

    def test_stone_to_flesh_from_a_magic_user_who_joins_in_town_brings_a_statue_back(self):
        game, _ = _stone_party_in_game_over()
        hild, brakk = game.party.members
        assert game.execute(PlaceParty(location=PartyLocation(kind="town"))).accepted

        joined = game.execute(AddMember(character=_magic_user()))
        assert joined.accepted, [rejection.code for rejection in joined.rejections]
        wenna_id = next(event.character_id for event in joined.events if event.code == "session.member.added")
        cast = game.execute(
            CastSpell(character_id=wenna_id, spell_id="stone_to_flesh", mode="restore", targets=(hild.id,))
        )

        assert cast.accepted, [rejection.code for rejection in cast.rejections]
        assert not has_condition(hild, Condition.PETRIFIED), "Hild is flesh again"
        assert has_condition(brakk, Condition.PETRIFIED)
        _assert_plays_on(game, [*joined.events, *cast.events], SessionMode.TOWN)
