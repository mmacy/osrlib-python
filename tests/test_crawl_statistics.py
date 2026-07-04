"""Statistical tests: reaction-roll bands and wandering-table distributions.

Chi-square over large N with the generators themselves, so a bug that preserves
types but skews distributions gets caught. Critical values are for alpha = 0.001 —
loose enough that a correct generator essentially never trips them, tight enough
that a skew shows. The reaction and wandering thirds of the spec's statistical
trio land here with their generators; treasure stays Phase 5.
"""

from collections import Counter

from crawl_fixtures import build_adventure, build_party
from osrlib.core.combat import roll_reaction
from osrlib.core.rng import RngStream
from osrlib.core.tables import EncounterTable, EncounterTableRow, MonsterEncounterEntry, ReactionResult
from osrlib.crawl import exploration
from osrlib.crawl.commands import EnterDungeon, GrantItem, SessionMode
from osrlib.crawl.dungeon import WanderingSpec
from osrlib.crawl.session import GameSession

CHI_SQUARE_CRITICAL = {1: 10.83, 4: 18.47, 3: 16.27, 19: 43.82}


def chi_square(observed: dict, expected: dict) -> float:
    return sum((observed.get(key, 0) - expected[key]) ** 2 / expected[key] for key in expected)


class TestReactionDistribution:
    def test_band_frequencies_match_2d6(self):
        stream = RngStream.from_seed_material(97, "encounter")
        trials = 20_000
        counts = Counter(roll_reaction(stream=stream).result for _ in range(trials))
        probabilities = {
            ReactionResult.ATTACKS: 1 / 36,
            ReactionResult.HOSTILE: 9 / 36,
            ReactionResult.UNCERTAIN: 16 / 36,
            ReactionResult.INDIFFERENT: 9 / 36,
            ReactionResult.FRIENDLY: 1 / 36,
        }
        expected = {band: trials * probability for band, probability in probabilities.items()}
        assert chi_square(counts, expected) < CHI_SQUARE_CRITICAL[4]


def wandering_session(adventure) -> GameSession:
    session = GameSession.new(build_party(), adventure, seed=53)
    session.execute(GrantItem(character_id="character-0001", item_id="torch", quantity=6))
    session.execute(EnterDungeon(dungeon_id="delve"))
    return session


def clear_encounter(session) -> None:
    session.encounter = None
    session.battle = None
    session.mode = SessionMode.EXPLORING


class TestWanderingDistributions:
    def test_check_rate_matches_the_chance(self):
        session = wandering_session(build_adventure(wandering_chance=2))
        trials = 3_000
        hits = 0
        for _ in range(trials):
            _, encountered = exploration.wandering_check(session)
            if encountered:
                hits += 1
                clear_encounter(session)
        expected = {True: trials * 2 / 6, False: trials * 4 / 6}
        assert chi_square({True: hits, False: trials - hits}, expected) < CHI_SQUARE_CRITICAL[1]

    def test_d20_rows_are_uniform(self):
        # The level-1 table's rows are distinct templates, so the spawned template
        # census is the d20 distribution (NPC-party rows would re-roll; level 1
        # has none).
        session = wandering_session(build_adventure(wandering_chance=6))
        trials = 2_400
        counts: Counter = Counter()
        for _ in range(trials):
            events, encountered = exploration.wandering_check(session)
            assert encountered
            started = next(event for event in events if getattr(event, "code", "") == "encounter.started")
            counts[started.monster_name] += 1
            clear_encounter(session)
        assert len(counts) == 20
        expected = {name: trials / 20 for name in counts}
        assert chi_square(counts, expected) < CHI_SQUARE_CRITICAL[19]

    def test_number_appearing_matches_the_count_dice(self):
        # A custom inline table of twenty identical goblin rows isolates the
        # 2d4 number-appearing distribution.
        row_template = {
            "name": "Goblin",
            "entry": MonsterEncounterEntry(monster_ids=("goblin",)),
            "count_dice": "2d4",
        }
        table = EncounterTable(
            id="goblins_only",
            label="Goblins only",
            min_level=1,
            max_level=None,
            rows=tuple(EncounterTableRow(roll=roll, **row_template) for roll in range(1, 21)),
        )
        adventure = build_adventure(wandering_chance=6)
        dungeon = adventure.dungeons[0]
        level_1 = dungeon.levels[0].model_copy(
            update={"wandering": WanderingSpec(chance_in_six=6, interval_turns=2, table=table)}
        )
        adventure = adventure.model_copy(
            update={"dungeons": (dungeon.model_copy(update={"levels": (level_1, dungeon.levels[1])}),)}
        )
        session = wandering_session(adventure)
        trials = 1_200
        counts: Counter = Counter()
        for _ in range(trials):
            events, encountered = exploration.wandering_check(session)
            assert encountered
            started = next(event for event in events if getattr(event, "code", "") == "encounter.started")
            counts[started.count] += 1
            clear_encounter(session)
        # 2d4: totals 2..8 with weights 1,2,3,4,3,2,1 (of 16).
        weights = {2: 1, 3: 2, 4: 3, 5: 4, 6: 3, 7: 2, 8: 1}
        expected = {total: trials * weight / 16 for total, weight in weights.items()}
        degrees = len(weights) - 1
        assert chi_square(counts, expected) < 22.46  # alpha 0.001, df 6
        assert degrees == 6
