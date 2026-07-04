"""The example crawler as the milestone integration test.

The scripted playthrough runs through the example's actual terminal loop (a real
subprocess of `python -m examples.tui_crawler`): creation, outfitting, the delve
with a generated lair hoard, a rival NPC party fought and looted, the MacGuffin,
the return, the award, a character reaching level 2, and the quest flag set by
the example's own listener. The same milestone runs as a golden through
`GameSession.execute` in `test_phase5_goldens.py`.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "examples" / "tui_crawler" / "scripts" / "milestone.txt"
MILESTONE_SEED = 203


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
        assert "Encounter: 2 × Goblin" in out
        assert "acquires valuable-" in out  # the generated lair hoard, looted
        # The rival party: a wandering NPC encounter fought and looted.
        assert "Adventurers" in out
        assert "The battle is won." in out
        # The MacGuffin and the quest reward, granted in the dungeon.
        assert "acquires" in out and "200 gp in coin" in out
        # The return and the end-of-adventure award.
        assert "The adventure ends:" in out
        # The level-up, the quest flag, and the town services.
        assert "Highest level reached: 2" in out
        assert "quest.idol = 'recovered'" in out
        assert "purchases cure_light_wounds at the temple" in out

    def test_scripted_run_is_deterministic(self):
        first = run_crawler("--seed", str(MILESTONE_SEED), "--script", str(SCRIPT))
        second = run_crawler("--seed", str(MILESTONE_SEED), "--script", str(SCRIPT))
        assert first.stdout == second.stdout
        assert first.returncode == second.returncode == 0
