"""Surfaces declared and never wired are gone.

The docstring pass (#87 to #99) found five things the code declared and nothing set: the surrender
path (`EncounterGroup.surrendered`, the `battle.side.surrendered` code and its template, the
`"surrendered"` defeat outcome), `Character.literacy`, `DungeonState.discovered_features`, and the
`unbreakable` ward parameter. The project's rule is to wire a surface or delete it, and none of
these has a rule behind it, so they are deleted. Each removal rides the schema 4 bump.
"""

from pathlib import Path

from osrlib.core.character import Character
from osrlib.crawl.dungeon import DungeonState
from osrlib.crawl.encounter import EncounterGroup
from osrlib.crawl.events import MonsterFledEvent
from osrlib.messages import _TEMPLATES

SRC = Path(__file__).resolve().parent.parent / "src" / "osrlib"


def test_the_surrender_path_is_gone():
    assert "surrendered" not in EncounterGroup.model_fields
    assert "battle.side.surrendered" not in MonsterFledEvent.allowed_codes
    assert "battle.side.surrendered" not in _TEMPLATES
    for path in sorted(SRC.rglob("*.py")):
        assert "surrendered" not in path.read_text(encoding="utf-8"), path


def test_literacy_and_discovered_features_are_gone():
    assert not hasattr(Character, "literacy")
    assert "discovered_features" not in DungeonState.model_fields


def test_no_ward_is_unbreakable():
    for path in sorted(SRC.rglob("*.py")):
        assert "unbreakable" not in path.read_text(encoding="utf-8"), path
