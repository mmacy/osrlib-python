"""Acceptance tests for rest credit when a wandering monster interrupts a rest (issue #169).

The OSE SRD's dungeon adventuring page says fatigue's "–1 penalty to attack and damage rolls is
incurred until the characters rest for one turn", and its evasion and pursuit page says "The
penalties for exhaustion last until characters have rested for three full turns." A rest that a
wandering monster breaks off has still given the party the full turns it rested before the
monsters came, and those turns count.

The engine runs a wandering check after it advances the clock through a turn, and the encounter
it opens takes the turn after that. That matches the SRD's sequence of play, where the check is
the first step of a turn. So when the check that interrupts a rest runs, every rest turn the
clock has passed through is complete. The full turns rested are the whole turns of elapsed time,
`(rounds at the interruption - rounds at the start) // ROUNDS_PER_TURN`, the measure issue #169
uses. A rest that starts partway through a turn, for example after a round spent lighting a
torch, and is interrupted at the next turn boundary has rested less than one full turn, and that
is the rest interrupted during its first turn, which clears nothing.

Each scenario sets the level's wandering interval so that the first check of the rest falls on
the turn boundary the test needs, and uses a table of grey oozes, so that the party can always
walk away from the encounter with `Evade` and keep playing. The helpers assert every
precondition, so a scenario that stops exercising its case fails instead of passing vacuously.
"""

from typing import Literal

import pytest

from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.clock import ROUNDS_PER_TURN
from osrlib.core.effects import Condition, has_condition
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.spells import MemorizedSpell
from osrlib.core.tables import EncounterTable, EncounterTableRow, MonsterEncounterEntry
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import (
    CommandResult,
    EnterDungeon,
    Evade,
    GrantItem,
    LightSource,
    MoveParty,
    PrepareSpells,
    Rest,
    Search,
    SessionMode,
)
from osrlib.crawl.dungeon import Direction, DungeonSpec, Edge, EdgeKind, LevelSpec, WanderingSpec
from osrlib.crawl.events import WanderingCheckEvent
from osrlib.crawl.exploration import EXHAUSTED_DEFINITION, EXHAUSTED_KIND, FATIGUE_KIND
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession

BUG = "chunk: rest-credit"

DWARF = "character-0001"
"""The party's searcher. A dwarf has infravision, so it can search without a light."""
MAGIC_USER = "character-0002"
"""The party's caster, whose spell preparation shows whether a rest counted as sleep."""

RestKind = Literal["turn", "night", "day"]

REST_TURNS: dict[RestKind, int] = {"turn": 1, "night": 48, "day": 144}
SEARCH_KINDS = ("secret_doors", "room_traps", "construction")

SEED = 5
"""Found by scanning seeds. In every scenario here, the first check of the rest brings the ooze, and
it opens an encounter rather than a battle. The scenario helpers fail if a change to the dice stops
that being true, and then a new seed is needed, not an edited assertion."""

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
"""Every wandering encounter is one grey ooze, which moves 10' a turn, so `Evade` always gets away."""


def build_party() -> Party:
    draw = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
    return Party(
        members=[
            create_character(
                name="Brok", class_id="dwarf", alignment=Alignment.LAWFUL, ruleset=Ruleset(), stream=draw
            ).character,
            create_character(
                name="Ysolde",
                class_id="magic_user",
                alignment=Alignment.NEUTRAL,
                ruleset=Ruleset(),
                stream=draw,
                starting_spell_ids=("sleep",),
            ).character,
        ]
    )


def new_session(seed: int, *, interval: int, chance: int = 6) -> GameSession:
    """Start a session with the party at the west end of a six-cell corridor, on a turn boundary."""
    level = LevelSpec(
        number=1,
        width=6,
        height=1,
        entrance=(0, 0),
        edges={f"{x},0:west": Edge(kind=EdgeKind.OPEN) for x in range(1, 6)},
        wandering=WanderingSpec(chance_in_six=chance, interval_turns=interval, table=_OOZES),
    )
    adventure = Adventure(
        name="Rest credit",
        town=TownSpec(name="Threshold", travel_turns={"crypt": 1}),
        dungeons=(DungeonSpec(id="crypt", name="Crypt", levels=(level,)),),
    )
    session = GameSession.new(build_party(), adventure, seed=seed)
    assert session.execute(GrantItem(character_id=DWARF, item_id="torch")).accepted
    assert session.execute(GrantItem(character_id=DWARF, item_id="tinder_box")).accepted
    assert session.execute(EnterDungeon(dungeon_id="crypt")).accepted
    assert session.clock.rounds % ROUNDS_PER_TURN == 0
    return session


def codes(result: CommandResult) -> list[str]:
    return [event.code for event in result.events]


def fatigued(session: GameSession) -> bool:
    return any(session.ledger.active_on(member.id, FATIGUE_KIND) for member in session.party.members)


def fully_fatigued(session: GameSession) -> bool:
    return all(session.ledger.active_on(member.id, FATIGUE_KIND) for member in session.party.members)


def exhausted(session: GameSession) -> bool:
    return any(
        session.ledger.active_on(member.id, EXHAUSTED_KIND) or has_condition(member, Condition.EXHAUSTED)
        for member in session.party.members
    )


def fully_exhausted(session: GameSession) -> bool:
    return all(
        session.ledger.active_on(member.id, EXHAUSTED_KIND) and has_condition(member, Condition.EXHAUSTED)
        for member in session.party.members
    )


def spend_unrested_turn(session: GameSession) -> CommandResult:
    """Spend one unrested exploring turn with a search, stepping east when the cell is searched out."""
    for _ in range(2):
        for kind in SEARCH_KINDS:
            result = session.execute(Search(character_id=DWARF, kind=kind))
            if result.accepted:
                return result
            assert [rejection.code for rejection in result.rejections] == ["exploration.search.already_tried"]
        assert session.execute(MoveParty(direction=Direction.EAST)).accepted
    raise AssertionError("no search left to spend a turn on")


def spend_unrested_turns(session: GameSession, turns: int) -> None:
    for _ in range(turns):
        start = session.clock.rounds
        result = spend_unrested_turn(session)
        assert session.mode is SessionMode.EXPLORING
        assert "exploration.wandering.checked" not in codes(result)
        assert 0 < session.clock.rounds - start <= ROUNDS_PER_TURN


def exhaust_party(session: GameSession) -> None:
    """Exhaust every member the way a game's own code does: attach the engine's exhaustion effect."""
    for member in session.party.members:
        session.ledger.attach(
            EXHAUSTED_DEFINITION,
            member.id,
            clock=session.clock,
            allocator=session.allocator,
            registry=session.registry(),
        )
    assert fully_exhausted(session)


def spend_a_round(session: GameSession) -> None:
    """Strike the tinder box once, which takes one round, so the next rest starts partway through a turn."""
    start = session.clock.rounds
    assert session.execute(LightSource(character_id=DWARF, item_id="torch")).accepted
    assert session.clock.rounds == start + 1
    assert session.clock.rounds % ROUNDS_PER_TURN != 0


def interrupted_rest(
    seed: int,
    *,
    kind: RestKind,
    boundary: int,
    unrested: int,
    mid_turn: bool = False,
    exhaust: bool = False,
    wounded: bool = False,
) -> tuple[GameSession, CommandResult]:
    """Rest until the check at the rest's `boundary`-th turn boundary brings monsters.

    The party first spends `unrested` exploring turns, and then, with `mid_turn`, one more round. The
    level's interval is `unrested + boundary`, so no check runs while the party explores and the
    first check of the rest runs at its `boundary`-th turn boundary. The full turns rested are
    `boundary`, or `boundary - 1` with `mid_turn`.
    """
    assert boundary < REST_TURNS[kind] or (kind == "turn" and boundary == 1)
    session = new_session(seed, interval=unrested + boundary)
    if exhaust:
        exhaust_party(session)
    if wounded:
        for member in session.party.members:
            member.current_hp = 1
    spend_unrested_turns(session, unrested)
    if unrested >= 6:
        assert fully_fatigued(session)
    else:
        assert not fatigued(session)
    if mid_turn:
        spend_a_round(session)
    start = session.clock.rounds
    result = session.execute(Rest(kind=kind))
    assert result.accepted
    event_codes = codes(result)
    # Preconditions: one check ran, at the rest's `boundary`-th turn boundary, and it brought monsters.
    checks = [event for event in result.events if isinstance(event, WanderingCheckEvent)]
    assert len(checks) == 1
    assert checks[0].encounter is True
    elapsed = session.clock.rounds - start
    assert session.clock.rounds % ROUNDS_PER_TURN == 0
    assert elapsed == boundary * ROUNDS_PER_TURN - (1 if mid_turn else 0)
    assert elapsed // ROUNDS_PER_TURN == (boundary - 1 if mid_turn else boundary)
    assert "exploration.rest.interrupted" in event_codes
    assert "exploration.rest.rested" not in event_codes
    assert session.mode is SessionMode.ENCOUNTER
    return session, result


def walk_away(session: GameSession) -> None:
    """End the encounter with the ooze by walking away from it, back to exploring on a turn boundary."""
    result = session.execute(Evade())
    assert "encounter.evasion.succeeded" in codes(result)
    assert session.mode is SessionMode.EXPLORING
    assert session.clock.rounds % ROUNDS_PER_TURN == 0


def uninterrupted_rest(session: GameSession, kind: RestKind) -> CommandResult:
    start = session.clock.rounds
    assert start % ROUNDS_PER_TURN == 0
    result = session.execute(Rest(kind=kind))
    assert "exploration.rest.rested" in codes(result)
    assert "exploration.rest.interrupted" not in codes(result)
    assert session.clock.rounds - start == REST_TURNS[kind] * ROUNDS_PER_TURN
    return result


class TestFatigue:
    @pytest.mark.xfail(reason=BUG, strict=True)
    @pytest.mark.parametrize(("kind", "boundary"), [("turn", 1), ("night", 1), ("night", 4)])
    def test_rest_interrupted_after_full_turns_clears_fatigue(self, kind, boundary):
        # Night at boundary 4 is the issue's reproduction. A one-turn rest started on a turn
        # boundary has rested its full turn when the check after it brings monsters.
        session, result = interrupted_rest(SEED, kind=kind, boundary=boundary, unrested=6)
        assert "exploration.fatigue.recovered" in codes(result)
        assert not fatigued(session)

    @pytest.mark.parametrize("kind", ["turn", "night"])
    def test_rest_interrupted_during_its_first_turn_clears_no_fatigue(self, kind):
        # The rest started a round into a turn and the monsters came at the turn's end:
        # 59 rounds of rest, less than one full turn.
        session, result = interrupted_rest(SEED, kind=kind, boundary=1, unrested=6, mid_turn=True)
        assert "exploration.fatigue.recovered" not in codes(result)
        assert fully_fatigued(session)

    @pytest.mark.xfail(reason=BUG, strict=True)
    def test_partial_first_turn_does_not_void_the_full_turn_after_it(self):
        # One full turn and 59 rounds of rest.
        session, result = interrupted_rest(SEED, kind="night", boundary=2, unrested=6, mid_turn=True)
        assert "exploration.fatigue.recovered" in codes(result)
        assert not fatigued(session)

    @pytest.mark.xfail(reason=BUG, strict=True)
    def test_interrupted_day_clears_fatigue_but_heals_nobody(self):
        session, result = interrupted_rest(SEED, kind="day", boundary=2, unrested=6, wounded=True)
        assert all(member.current_hp == 1 for member in session.party.members)
        assert "exploration.fatigue.recovered" in codes(result)
        assert not fatigued(session)

    @pytest.mark.xfail(reason=BUG, strict=True)
    def test_a_full_rested_turn_restarts_the_rest_cadence(self):
        # Four unrested turns, then a night broken after two full turns: the party has had its
        # rest, so fatigue comes six unrested turns later, not two. The boundary is 2 so that the
        # level's interval, 6, keeps checks away from the five turns that follow.
        session, _ = interrupted_rest(SEED, kind="night", boundary=2, unrested=4)
        walk_away(session)
        for turn in range(1, 6):
            spend_unrested_turns(session, 1)
            assert not fatigued(session), f"fatigued after {turn} unrested turns"


class TestExhaustion:
    @pytest.mark.xfail(reason=BUG, strict=True)
    def test_interrupted_night_after_three_full_turns_clears_exhaustion(self):
        session, result = interrupted_rest(SEED, kind="night", boundary=3, unrested=4, exhaust=True)
        assert "encounter.exhaustion.recovered" in codes(result)
        assert not exhausted(session)

    @pytest.mark.xfail(reason=BUG, strict=True)
    @pytest.mark.parametrize("mid_turn", [False, True])
    def test_two_full_turns_count_toward_the_next_rest(self, mid_turn):
        # Two full turns of an interrupted night, plus one more turn of rest, make three. Started
        # a round into a turn, the night needs a third boundary for two full turns.
        boundary = 3 if mid_turn else 2
        session, result = interrupted_rest(
            SEED, kind="night", boundary=boundary, unrested=4, mid_turn=mid_turn, exhaust=True
        )
        assert "encounter.exhaustion.recovered" not in codes(result)
        assert fully_exhausted(session)
        walk_away(session)
        assert fully_exhausted(session)
        follow_up = uninterrupted_rest(session, "turn")
        assert "encounter.exhaustion.recovered" in codes(follow_up)
        assert not exhausted(session)

    @pytest.mark.xfail(reason=BUG, strict=True)
    def test_one_full_turn_counts_once(self):
        # A night broken after one full turn credits exactly one: the first turn of rest after
        # it leaves the party exhausted, and the second clears it.
        session, _ = interrupted_rest(SEED, kind="night", boundary=1, unrested=4, exhaust=True)
        assert fully_exhausted(session)
        walk_away(session)
        first = uninterrupted_rest(session, "turn")
        assert "encounter.exhaustion.recovered" not in codes(first)
        assert fully_exhausted(session)
        second = uninterrupted_rest(session, "turn")
        assert "encounter.exhaustion.recovered" in codes(second)
        assert not exhausted(session)

    def test_rest_interrupted_during_its_first_turn_credits_no_exhaustion(self):
        # Less than one full turn of rest, so three more turns are still needed after it.
        session, result = interrupted_rest(SEED, kind="night", boundary=1, unrested=4, mid_turn=True, exhaust=True)
        assert "encounter.exhaustion.recovered" not in codes(result)
        walk_away(session)
        for _ in range(2):
            uninterrupted_rest(session, "turn")
            assert fully_exhausted(session)
        last = uninterrupted_rest(session, "turn")
        assert "encounter.exhaustion.recovered" in codes(last)
        assert not exhausted(session)


class TestInterruptedNightIsNoSleep:
    def test_interrupted_night_after_full_turns_does_not_allow_preparation(self):
        # Rest credit is for fatigue and exhaustion only. Spell preparation still needs an
        # uninterrupted night's sleep.
        session, _ = interrupted_rest(SEED, kind="night", boundary=4, unrested=6)
        walk_away(session)
        result = session.execute(PrepareSpells(character_id=MAGIC_USER, selections=(MemorizedSpell(spell_id="sleep"),)))
        assert [rejection.code for rejection in result.rejections] == ["magic.memorize.needs_sleep"]


class TestUninterruptedRest:
    """Today's behavior, which the fix keeps."""

    def test_a_rest_turn_clears_fatigue_and_restarts_the_cadence(self):
        session = new_session(SEED, interval=100, chance=0)
        spend_unrested_turns(session, 6)
        assert fully_fatigued(session)
        result = uninterrupted_rest(session, "turn")
        assert "exploration.fatigue.recovered" in codes(result)
        assert not fatigued(session)
        spend_unrested_turns(session, 5)
        assert not fatigued(session)
        spend_unrested_turns(session, 1)
        assert fully_fatigued(session)

    def test_three_rest_turns_clear_exhaustion(self):
        session = new_session(SEED, interval=100, chance=0)
        exhaust_party(session)
        for _ in range(2):
            result = uninterrupted_rest(session, "turn")
            assert "encounter.exhaustion.recovered" not in codes(result)
            assert fully_exhausted(session)
        result = uninterrupted_rest(session, "turn")
        assert "encounter.exhaustion.recovered" in codes(result)
        assert not exhausted(session)

    def test_a_night_clears_fatigue_and_exhaustion_and_counts_as_sleep(self):
        session = new_session(SEED, interval=100, chance=0)
        exhaust_party(session)
        spend_unrested_turns(session, 6)
        assert fully_fatigued(session)
        result = uninterrupted_rest(session, "night")
        assert "exploration.fatigue.recovered" in codes(result)
        assert "encounter.exhaustion.recovered" in codes(result)
        assert not fatigued(session)
        assert not exhausted(session)
        prepared = session.execute(
            PrepareSpells(character_id=MAGIC_USER, selections=(MemorizedSpell(spell_id="sleep"),))
        )
        assert prepared.accepted


class TestDeterminism:
    @pytest.mark.xfail(reason=BUG, strict=True)
    def test_same_seed_and_commands_give_the_same_events(self):
        def play() -> list[dict]:
            session, rest = interrupted_rest(SEED, kind="night", boundary=2, unrested=6, exhaust=True)
            # The credited rest is part of the record being compared.
            assert "exploration.fatigue.recovered" in codes(rest)
            walk_away(session)
            follow_up = uninterrupted_rest(session, "turn")
            assert "encounter.exhaustion.recovered" in codes(follow_up)
            return [event.model_dump(mode="json") for event in [*rest.events, *follow_up.events]]

        assert play() == play()
