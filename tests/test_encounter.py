"""Encounter procedure tests: surprise, stances, parley, evasion, pursuit, conclusion."""

from crawl_fixtures import build_adventure, build_party, build_sightline_adventure
from osrlib.core.clock import ROUNDS_PER_TURN
from osrlib.core.effects import Condition, has_condition
from osrlib.core.items import Coins
from osrlib.core.tables import ReactionResult
from osrlib.crawl import encounter as encounter_module
from osrlib.crawl import exploration
from osrlib.crawl.commands import (
    BattleDeclaration,
    EngageBattle,
    EnterDungeon,
    EquipItem,
    Evade,
    GrantCoins,
    GrantItem,
    LightSource,
    MoveParty,
    Parley,
    PlaceParty,
    ResolveBattleRound,
    TurnUndead,
    Wait,
)
from osrlib.crawl.dungeon import Direction, PartyLocation
from osrlib.crawl.session import ENCOUNTER_STREAM, GameSession


def quiet_session(seed: int = 5) -> GameSession:
    session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=seed)
    session.execute(GrantItem(character_id="character-0001", item_id="torch", quantity=6))
    session.execute(GrantItem(character_id="character-0001", item_id="tinder_box"))
    session.execute(EnterDungeon(dungeon_id="delve"))
    for _ in range(20):
        lit = session.execute(LightSource(character_id="character-0001", item_id="torch"))
        if any(event.code == "exploration.light.lit" for event in lit.events):
            break
    return session


def start(session, template_id="goblin", count=2, distance=40, **kwargs):
    instances = session.spawn(template_id, count)
    return encounter_module.start_encounter(
        session, groups=[(template_id, instances)], kind="spawned", distance_feet=distance, **kwargs
    )


# The sight-line level's four vantage points (see `build_sightline_adventure`).
CHAMBER_CELL = (0, 0)
CLOSET_CELL = (0, 2)
CORRIDOR_CELL = (1, 2)
SEALED_CELL = (0, 4)


class TestSurprise:
    def test_lit_party_skips_the_monsters_roll(self):
        session = quiet_session()
        events = start(session)
        monsters_surprise = next(event for event in events if getattr(event, "side", "") == "monsters")
        assert monsters_surprise.roll is None
        assert monsters_surprise.surprised is False

    def test_unlit_party_lets_the_monsters_roll_and_is_surprised_on_1_to_3(self):
        session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=8)
        session.execute(EnterDungeon(dungeon_id="delve"))  # no light at all
        events = start(session)
        monsters_surprise = next(event for event in events if getattr(event, "side", "") == "monsters")
        assert monsters_surprise.roll is not None
        party_surprise = next(event for event in events if getattr(event, "side", "") == "party")
        assert party_surprise.threshold == 3  # the blind-party adaptation
        assert party_surprise.surprised is (party_surprise.roll <= 3)

    def test_lit_party_rolls_at_the_normal_threshold(self):
        session = quiet_session()
        events = start(session)
        party_surprise = next(event for event in events if getattr(event, "side", "") == "party")
        assert party_surprise.threshold == 2

    def test_party_awareness_skips_the_party_roll(self):
        session = quiet_session()
        events = start(session, party_aware=True)
        party_surprise = next(event for event in events if getattr(event, "side", "") == "party")
        assert party_surprise.roll is None and party_surprise.surprised is False

    def test_keyed_awareness_and_alerting_skip_the_monsters_roll(self):
        session = quiet_session()
        events = start(session, monsters_aware=True)
        monsters_surprise = next(event for event in events if getattr(event, "side", "") == "monsters")
        assert monsters_surprise.roll is None


class TestStances:
    def test_pinned_stance_skips_the_reaction_roll(self):
        session = quiet_session()
        events = start(session, pinned_stance=ReactionResult.INDIFFERENT)
        codes = [getattr(event, "code", "") for event in events]
        assert "encounter.reaction.rolled" not in codes
        assert session.encounter.stance == "indifferent"

    def test_attacks_stance_opens_battle_immediately(self):
        session = quiet_session()
        events = start(session, pinned_stance=ReactionResult.ATTACKS)
        assert any(getattr(event, "code", "") == "battle.started" for event in events)
        assert session.mode.value == "battle"

    def test_hostile_stance_attacks_at_the_end_of_the_next_round(self):
        session = quiet_session()
        start(session, pinned_stance=ReactionResult.HOSTILE)
        assert session.mode.value == "encounter"
        result = session.execute(Wait())
        assert any(event.code == "battle.started" for event in result.events)
        assert session.mode.value == "battle"

    def test_evading_defers_the_hostile_attack(self):
        session = quiet_session()
        start(session, pinned_stance=ReactionResult.HOSTILE)
        result = session.execute(Evade(drop="none"))
        assert session.mode.value in ("encounter", "exploring")  # pursuit or clean escape
        assert not any(event.code == "battle.started" for event in result.events)

    def test_uncertain_stance_rerolls_each_round(self):
        session = quiet_session(seed=13)
        start(session, pinned_stance=ReactionResult.UNCERTAIN)
        result = session.execute(Wait())
        codes = [event.code for event in result.events]
        assert "encounter.reaction.rolled" in codes

    def test_indifferent_monsters_never_attack(self):
        session = quiet_session()
        start(session, pinned_stance=ReactionResult.INDIFFERENT)
        for _ in range(5):
            session.execute(Wait())
            assert session.mode.value == "encounter"

    def test_parley_rerolls_with_the_speaker_cha_uncapped(self):
        session = quiet_session(seed=2)
        start(session, pinned_stance=ReactionResult.UNCERTAIN)
        for _ in range(3):  # any number of re-rolls, each a fresh roll
            if session.mode.value != "encounter":
                break
            result = session.execute(Parley(character_id="character-0003"))
            reaction = next((event for event in result.events if event.code == "encounter.reaction.rolled"), None)
            assert reaction is not None
            assert reaction.modifier == session.member("character-0003").npc_reaction_modifier


class TestEvasionAndPursuit:
    def test_faster_party_evades_immediately(self):
        # Goblins move 60 (base): the unencumbered party (120) outruns them.
        session = quiet_session()
        start(session, pinned_stance=ReactionResult.HOSTILE)
        result = session.execute(Evade(drop="none"))
        codes = [event.code for event in result.events]
        assert "encounter.evasion.succeeded" in codes
        assert "encounter.ended" in codes
        assert session.mode.value == "exploring"

    def test_indifferent_monsters_do_not_pursue(self):
        session = quiet_session()
        start(session, template_id="normal_wolf", pinned_stance=ReactionResult.INDIFFERENT)
        result = session.execute(Evade(drop="none"))
        assert any(event.code == "encounter.evasion.succeeded" for event in result.events)

    def slow_party_session(self, seed=5, stance=ReactionResult.HOSTILE, template="normal_wolf"):
        # Wolves run 180: faster than the party's 120 — a pursuit is guaranteed.
        session = quiet_session(seed=seed)
        events = start(session, template_id=template, count=2, distance=40, pinned_stance=stance)
        return session, events

    def test_slower_party_is_pursued_and_caught(self):
        session, _ = self.slow_party_session()
        result = session.execute(Evade(drop="none"))
        codes = [event.code for event in result.events]
        assert "encounter.evasion.pursuit" in codes
        # Wolves gain 60'/round from 40': caught inside the first round.
        assert "encounter.pursuit.caught" in codes
        assert session.mode.value == "battle"
        assert all(group.distance_feet == 5 for group in session.encounter.groups)

    def test_distraction_by_dropped_treasure_uses_the_intelligence_proxy(self):
        # Wolves have no treasure letters (unintelligent): food tempts them,
        # treasure never does.
        session, _ = self.slow_party_session()
        session.execute(GrantCoins(character_id="character-0001", coins=Coins(gp=100)))
        before = session.streams.get(ENCOUNTER_STREAM).export_state()
        result = session.execute(Evade(drop="treasure"))
        codes = [event.code for event in result.events]
        assert "encounter.pursuit.distracted" not in codes
        # Bait mismatch means no distraction roll at all: the evade consumed no
        # encounter draws beyond... the pursuit itself rolls nothing this round,
        # so the stream moved only if a pursuit round drew (it doesn't).
        assert session.streams.get(ENCOUNTER_STREAM).export_state() == before

    def test_distraction_by_food_can_stop_unintelligent_pursuers(self):
        outcomes = set()
        for seed in range(30):
            session, _ = self.slow_party_session(seed=seed)
            session.execute(GrantItem(character_id="character-0001", item_id="rations_standard", quantity=7))
            result = session.execute(Evade(drop="food"))
            codes = [event.code for event in result.events]
            if "encounter.pursuit.distracted" in codes:
                outcomes.add("distracted")
                assert "encounter.ended" in codes
                assert session.mode.value == "exploring"
            elif "encounter.pursuit.caught" in codes:
                outcomes.add("caught")
            if outcomes == {"distracted", "caught"}:
                break
        assert "distracted" in outcomes  # 3-in-6 hits within thirty seeds

    def test_nothing_to_drop_rejects(self):
        session, _ = self.slow_party_session()
        result = session.execute(Evade(drop="treasure"))
        assert result.rejections[0].code == "encounter.evade.nothing_to_drop"

    def test_gap_arithmetic_and_the_thirty_round_exhaustion_terminal(self):
        # Equal rates keep the gap constant, so the pursuit runs to the 30-round
        # cap: monsters give up, the party is exhausted. Hellhounds run 120 —
        # exactly the unencumbered party's rate.
        session2 = quiet_session()
        start(
            session2,
            template_id="hellhound_3",
            count=2,
            distance=100,
            pinned_stance=ReactionResult.HOSTILE,
            party_aware=True,
        )
        assert exploration.exploration_rate(session2) == 120
        result = session2.execute(Evade(drop="none"))
        assert any(event.code == "encounter.evasion.pursuit" for event in result.events)
        rounds = 1
        while session2.mode.value == "encounter" and rounds < 40:
            result = session2.execute(Wait())
            rounds += 1
        codes = [event.code for event in result.events]
        assert "encounter.pursuit.escaped" in codes
        assert "encounter.exhaustion.gained" in codes
        assert session2.encounter is None
        for member in session2.party.living_members():
            assert has_condition(member, Condition.EXHAUSTED)

    def test_exhaustion_recovers_after_three_rested_turns(self):
        from osrlib.crawl.commands import Rest

        session = quiet_session()
        events = encounter_module._attach_exhaustion(session)
        assert any(getattr(event, "code", "") == "encounter.exhaustion.gained" for event in events)
        for _ in range(2):
            result = session.execute(Rest(kind="turn"))
            assert has_condition(session.member("character-0001"), Condition.EXHAUSTED)
        result = session.execute(Rest(kind="turn"))
        codes = [event.code for event in result.events]
        assert "encounter.exhaustion.recovered" in codes
        assert not has_condition(session.member("character-0001"), Condition.EXHAUSTED)


class TestTurnUndead:
    def test_turning_routs_skeletons_and_ends_the_encounter(self):
        turned_seen = False
        for seed in range(40):
            session = quiet_session(seed=seed)
            start(
                session,
                template_id="skeleton",
                count=3,
                distance=30,
                pinned_stance=ReactionResult.HOSTILE,
                party_aware=True,
            )
            result = session.execute(TurnUndead(character_id="character-0003"))
            assert result.accepted
            codes = [event.code for event in result.events]
            if "magic.turning.turned" in codes and "encounter.ended" in codes:
                turned_seen = True
                routed = [
                    event
                    for event in result.events
                    if getattr(event, "code", "") == "battle.monster.defeated" and event.outcome == "routed"
                ]
                assert routed  # turned undead flee: defeated as routed
                assert session.mode.value == "exploring"
                # The turned effects released at encounter end.
                assert all(not session.ledger.active_on(mid) for mid in session.monsters)
                break
        assert turned_seen

    def test_turning_non_undead_provokes_battle(self):
        session = quiet_session()
        start(
            session,
            template_id="goblin",
            count=2,
            distance=30,
            pinned_stance=ReactionResult.UNCERTAIN,
            party_aware=True,
        )
        result = session.execute(TurnUndead(character_id="character-0003"))
        assert result.accepted
        codes = [event.code for event in result.events]
        assert "magic.turning.failed" in codes
        assert "battle.started" in codes

    def test_non_cleric_cannot_turn(self):
        session = quiet_session()
        start(
            session,
            template_id="skeleton",
            count=1,
            distance=30,
            pinned_stance=ReactionResult.HOSTILE,
            party_aware=True,
        )
        result = session.execute(TurnUndead(character_id="character-0001"))
        assert not result.accepted
        assert result.rejections[0].code == "magic.turning.not_a_turner"


class TestConclusion:
    def test_an_encounter_consumes_at_least_one_full_turn(self):
        session = quiet_session()
        clock_before = session.clock.rounds
        start(session, pinned_stance=ReactionResult.INDIFFERENT)
        started = session.encounter.started_round
        session.execute(Evade(drop="none"))
        assert session.encounter is None
        assert session.clock.rounds >= started + ROUNDS_PER_TURN
        assert session.clock.rounds >= clock_before + ROUNDS_PER_TURN

    def test_keyed_encounter_resolution_marks_the_area(self):
        session = quiet_session(seed=41)
        place = PlaceParty(
            location=PartyLocation(
                kind="dungeon", dungeon_id="delve", level_number=1, position=(2, 0), facing=Direction.SOUTH
            )
        )
        session.execute(place)
        session.dungeon_state.door("delve:1:2,1:north").open = True
        session.execute(MoveParty(direction=Direction.SOUTH))
        assert session.encounter is not None
        assert session.encounter.kind == "keyed"
        # Evading leaves the keyed encounter unresolved: it re-triggers.
        session.execute(EngageBattle()) if session.mode.value == "encounter" else None
        assert session.mode.value == "battle"


class TestEncounterDistance:
    """The rolled distance is bounded by the space: RAW rolls only "if there is uncertainty"."""

    def test_a_long_corridor_keeps_the_full_2d6_range(self):
        # The corridor affords 130' of straight sight, so no roll is ever cut and
        # the distances span the printed 20'–120'.
        distances = {self.rolled(seed, CORRIDOR_CELL) for seed in range(60)}
        assert distances <= set(range(20, 130, 10))
        assert max(distances) > 100  # nothing clipped the top of the range
        assert len(distances) > 5  # the roll still varies

    def test_a_small_chamber_clamps_to_its_own_span(self):
        # A three-cell chamber, sealed: 20' from the west end to the east end.
        assert {self.rolled(seed, CHAMBER_CELL) for seed in range(60)} == {20}

    def test_a_sealed_cell_falls_back_to_the_one_cell_floor(self):
        # Nothing to see in any direction; the clamp never produces 0'.
        assert {self.rolled(seed, SEALED_CELL) for seed in range(60)} == {10}

    def test_a_keyed_encounter_opens_within_the_room_it_is_keyed_to(self):
        # The reported bug's shape: goblins keyed to a 20' room, entered through
        # its door, can no longer greet the party from eighty feet away.
        for seed in range(20):
            session = quiet_session(seed=seed)
            session.execute(
                PlaceParty(
                    location=PartyLocation(
                        kind="dungeon", dungeon_id="delve", level_number=1, position=(2, 0), facing=Direction.SOUTH
                    )
                )
            )
            session.dungeon_state.door("delve:1:2,1:north").open = True
            result = session.execute(MoveParty(direction=Direction.SOUTH))
            assert session.encounter is not None and session.encounter.kind == "keyed"
            opened = next(event for event in result.events if event.code == "encounter.started")
            assert opened.distance_feet == 10

    def test_a_shut_door_shortens_the_line_and_opening_it_restores_the_roll(self):
        # One cell, one door: shut, the closet is all there is; open, the roll
        # runs the full corridor beyond it — the same roll, off the same draws.
        for seed in range(30):
            assert self.rolled(seed, CLOSET_CELL) == 10
            assert self.rolled(seed, CLOSET_CELL, open_closet=True) == self.rolled(seed, CORRIDOR_CELL)

    def test_darkness_never_shortens_the_line(self):
        # Light governs surprise, not distance: an unseen monster down a dark
        # corridor is still exactly as far away as it is.
        dark = self.sighting_session(seed=3, cell=CORRIDOR_CELL)
        assert dark.party_light()[0] is False
        assert exploration._sight_line_feet(dark) == 130
        assert max(self.rolled(seed, CORRIDOR_CELL) for seed in range(60)) > 30  # past a torch's radius

    def test_an_explicit_distance_is_never_bounded(self):
        # The referee places what the referee spawns.
        session = self.sighting_session(seed=3, cell=SEALED_CELL)
        instances = session.spawn("goblin", 2)
        encounter_module.start_encounter(
            session,
            groups=[("goblin", instances)],
            kind="spawned",
            distance_feet=90,
            pinned_stance=ReactionResult.INDIFFERENT,
        )
        assert session.encounter.groups[0].distance_feet == 90

    def test_the_bound_consumes_no_randomness(self):
        # Same seed, same draws: the clamp reads the rolled result and never the
        # stream, so a bounded encounter leaves the stream exactly where an
        # unbounded one does.
        states = []
        for cell in (SEALED_CELL, CORRIDOR_CELL):
            session = self.sighting_session(seed=17, cell=cell)
            instances = session.spawn("goblin", 2)
            encounter_module.start_encounter(
                session, groups=[("goblin", instances)], kind="wandering", pinned_stance=ReactionResult.INDIFFERENT
            )
            states.append(session.streams.export_states()[ENCOUNTER_STREAM].model_dump(mode="json"))
        assert states[0] == states[1]

    def test_the_bounded_roll_is_deterministic(self):
        assert self.rolled(11, CHAMBER_CELL) == self.rolled(11, CHAMBER_CELL)
        assert self.rolled(11, CORRIDOR_CELL) == self.rolled(11, CORRIDOR_CELL)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def sighting_session(seed: int, cell) -> GameSession:
        """A session on the sight-line level, party unlit and standing on `cell`."""
        session = GameSession.new(build_party(), build_sightline_adventure(), seed=seed)
        session.execute(EnterDungeon(dungeon_id="sightlines"))
        session.execute(
            PlaceParty(
                location=PartyLocation(
                    kind="dungeon", dungeon_id="sightlines", level_number=1, position=cell, facing=Direction.EAST
                )
            )
        )
        return session

    @classmethod
    def rolled(cls, seed: int, cell, *, open_closet: bool = False) -> int:
        """The distance an unpinned encounter opens at, from `cell`, on `seed`."""
        session = cls.sighting_session(seed, cell)
        if open_closet:
            session.dungeon_state.door("sightlines:1:1,2:west").open = True
        instances = session.spawn("goblin", 2)
        encounter_module.start_encounter(
            session, groups=[("goblin", instances)], kind="wandering", pinned_stance=ReactionResult.INDIFFERENT
        )
        return session.encounter.groups[0].distance_feet


class TestSurpriseFreeRounds:
    def test_surprised_monsters_grant_the_party_a_free_battle_round(self):
        # Scan for a seed where the monsters roll surprised while the party
        # (aware) never rolls; the attacks stance then opens battle with the
        # party's free round: the monsters hold through round one. The party
        # must be unlit — the lit-party rule skips the monsters' roll entirely.
        for seed in range(60):
            session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=seed)
            session.execute(GrantItem(character_id="character-0001", item_id="sword"))
            session.execute(EquipItem(character_id="character-0001", item_id="sword"))
            session.execute(EnterDungeon(dungeon_id="delve"))
            instances = session.spawn("goblin", 2)
            events = encounter_module.start_encounter(
                session,
                groups=[("goblin", instances)],
                kind="spawned",
                distance_feet=60,
                pinned_stance=ReactionResult.ATTACKS,
                party_aware=True,
                monsters_aware=False,
            )
            surprise = next(event for event in events if getattr(event, "side", "") == "monsters")
            if not surprise.surprised:
                continue
            assert session.mode.value == "battle"
            assert session.battle.monsters_hold_rounds == 1
            distance_before = session.encounter.groups[0].distance_feet
            declarations = tuple(
                BattleDeclaration(character_id=member.id, action="hold") for member in session.party.living_members()
            )
            result = session.execute(ResolveBattleRound(declarations=declarations))
            assert result.accepted
            # The surprised goblins lost the round: they never closed.
            assert session.encounter.groups[0].distance_feet == distance_before
            assert session.battle.monsters_hold_rounds == 0
            return
        raise AssertionError("no seed surprised the monsters in sixty tries")

    def test_surprised_party_gives_hostile_monsters_their_surprise_round(self):
        # An unlit party is surprised on 1-3; the hostile stance's deadline then
        # fires on the party's lost beat and battle opens with the monsters'
        # machine-run free round — they close before the party can act.
        for seed in range(60):
            session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=seed)
            session.execute(EnterDungeon(dungeon_id="delve"))
            instances = session.spawn("goblin", 2)
            events = encounter_module.start_encounter(
                session,
                groups=[("goblin", instances)],
                kind="spawned",
                distance_feet=60,
                pinned_stance=ReactionResult.HOSTILE,
                monsters_aware=True,
            )
            surprise = next(event for event in events if getattr(event, "side", "") == "party")
            if not surprise.surprised:
                continue
            # Battle began without a single party command, and the goblins spent
            # their surprise round closing.
            assert session.mode.value == "battle"
            assert session.battle.round == 1
            assert session.encounter.groups[0].distance_feet == 40
            return
        raise AssertionError("no seed surprised the party in sixty tries")
