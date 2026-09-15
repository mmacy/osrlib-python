"""Command dispatch has one way in, `GameSession.execute`, and no module-level state.

The handler tables of the exploration, encounter, and battle procedures are private, and the merged
table a session dispatches from lives on the session rather than in a module global, so replacing a
table entry after the first command cannot silently do nothing, and two sessions never share a cache.
"""

import pytest

from crawl_fixtures import build_adventure, build_party
from osrlib.core.clock import TimeUnit
from osrlib.crawl import battle, encounter, exploration
from osrlib.crawl import session as session_module
from osrlib.crawl.commands import AdvanceTime, EnterDungeon, RollDice
from osrlib.crawl.session import GameSession


class TestDispatchHasNoModuleState:
    @pytest.mark.xfail(reason="chunk: handler-privacy")
    def test_the_procedure_tables_are_private(self):
        for module in (exploration, encounter, battle):
            assert "HANDLERS" not in module.__all__, module.__name__
            assert not hasattr(module, "HANDLERS"), module.__name__

    @pytest.mark.xfail(reason="chunk: handler-privacy")
    def test_the_session_module_holds_no_merged_table(self):
        assert not hasattr(session_module, "_HANDLERS_CACHE")
        assert "HANDLERS" not in session_module.__all__

    def test_execute_dispatches_a_referee_and_an_exploration_command(self):
        first = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=1)
        second = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=2)
        assert first.execute(RollDice(expression="1d6")).accepted
        assert second.execute(AdvanceTime(n=1, unit=TimeUnit.TURN)).accepted
        assert first.execute(EnterDungeon(dungeon_id="delve")).accepted
        assert second.mode.value == "town"
