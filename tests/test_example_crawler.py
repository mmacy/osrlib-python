"""The example crawler as the milestone integration test.

The scripted playthrough runs through the example's actual terminal loop (a real
subprocess of `python -m examples.tui_crawler`): creation, outfitting, the delve
with a generated lair hoard, a rival NPC party fought and looted, the first return
with its award and town business, the second trip for the MacGuffin, and the
homecoming that completes the authored quest — the reward paid, the flag set, a
character at level 2, and the adventure closed in victory. Nothing in the example
tracks the quest: it is adventure data, played by the library's interpreter. The
same milestone runs as a golden through `GameSession.execute` in
`test_phase5_goldens.py`.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "examples" / "tui_crawler" / "scripts" / "milestone.txt"
MILESTONE_SEED = 39


def run_crawler(*arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "examples.tui_crawler", *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class TestMilestonePlaythrough:
    def test_scripted_run_reaches_the_milestone(self):
        result = run_crawler("--seed", str(MILESTONE_SEED), "--script", str(SCRIPT))
        assert result.returncode == 0, result.stderr
        out = result.stdout
        # The delve: the keyed goblins fall and their lair hoard was generated
        # (the party takes the engine-created cache).
        assert "Encounter: 2 goblins" in out
        assert "acquires valuable-" in out  # the generated lair hoard, looted
        # The rival party: a wandering NPC encounter fought and looted.
        assert "Encounter: 2 adventurers" in out
        assert "The battle is won." in out
        # Two trips, two returns, two awards — the town business happens on the
        # first, while there is still an adventure to come back from.
        assert out.count("The adventure ends:") == 2
        assert "purchases cure_light_wounds at the temple" in out
        assert "sells 1 valuable(s)" in out
        # The MacGuffin and the reward, paid on delivery.
        assert "acquires jade-idol" in out
        assert "acquires 200 gp in coin" in out
        # The level-up and the quest flag.
        assert "Highest level reached: 2" in out
        assert "quest.idol = 'recovered'" in out
        # The status view renders the active quest, and the journal verb prints
        # the authored record with its clock stamps.
        assert "Quest: The Jade Idol — Recover the idol incomplete, Bring it home incomplete" in out
        assert "[round 120] The temple wants the Jade Idol off the barrow king's altar" in out
        # The adventure is over, and the closing status says so.
        assert "[victory]" in out

    def test_the_quest_runs_as_authored_data(self):
        result = run_crawler("--seed", str(MILESTONE_SEED), "--script", str(SCRIPT))
        out = result.stdout
        # Every beat is the library's, rendered by the default formatter: the
        # example registers an interpreter and authors no quest code at all.
        assert "A new quest: The Jade Idol." in out
        assert "Quest The Jade Idol: objective Recover the idol is done." in out
        assert "Quest The Jade Idol: objective Bring it home is done." in out
        assert "Quest complete: The Jade Idol." in out
        assert "The adventure is over: The Jade Idol is finished." in out
        # The authored beats ride those events verbatim.
        assert "The temple wants the Jade Idol off the barrow king's altar" in out
        assert "The almoner counts out the reward without looking up." in out

    def test_the_first_return_is_not_the_last(self):
        result = run_crawler("--seed", str(MILESTONE_SEED), "--script", str(SCRIPT))
        out = result.stdout
        # The idol comes home on the second trip, so the completion lands after the
        # town business — the town-only commands would be refused the other way round.
        completion = out.index("Quest complete: The Jade Idol.")
        assert out.index("sells 1 valuable(s)") < completion
        assert out.index("purchases cure_light_wounds at the temple") < completion
        assert "(refused:" not in out, "the transcript runs clean end to end"

    def test_scripted_run_is_deterministic(self):
        first = run_crawler("--seed", str(MILESTONE_SEED), "--script", str(SCRIPT))
        second = run_crawler("--seed", str(MILESTONE_SEED), "--script", str(SCRIPT))
        assert first.stdout == second.stdout
        assert first.returncode == second.returncode == 0

    def test_invalid_command_syntax_is_caught_without_exiting(self, capsys):
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from examples.tui_crawler.__main__ import _dispatch
        from examples.tui_crawler.content import build_adventure
        from examples.tui_crawler.create import scripted_party

        from osrlib.core.character import CHARACTER_CREATION_STREAM
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.crawl.session import GameSession

        ruleset = Ruleset()
        party = scripted_party(RngStreams(master_seed=1).get(CHARACTER_CREATION_STREAM), ruleset)
        session = GameSession.new(party, build_adventure(), seed=1, ruleset=ruleset)
        assert _dispatch(session, "move sideways") is True
        assert "(invalid command:" in capsys.readouterr().out
        assert session.clock.rounds == 0

    def test_interactive_party_retries_same_slot_when_scores_miss_minimums(self, monkeypatch):
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from examples.tui_crawler.create import interactive_party

        from osrlib.core.character import CHARACTER_CREATION_STREAM
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset

        answers = iter(["", "elf", "l"] * 8)
        monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
        stream = RngStreams(master_seed=1).get(CHARACTER_CREATION_STREAM)
        party = interactive_party(stream, Ruleset())
        assert len(party.members) == 4
