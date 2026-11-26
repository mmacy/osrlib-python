"""The authoring guide and the changelog tell an author who reads a feature's description (#318).

Since 2.6.0 the players read a cache's authored description, and adventures written before then
may list a cache's contents or its trap there. The guide to authoring custom content shows a cache
written for the players, with what the referee alone reads in `FeatureSpec.referee_notes`, and the
2.8.0 changelog tells an upgrading author to check the descriptions they wrote earlier.
"""

import re
from pathlib import Path

from pydantic import BaseModel
from pytest_examples import find_examples

from osrlib.crawl.dungeon import FeatureSpec

_REPO = Path(__file__).resolve().parent.parent
_GUIDE = _REPO / "docs" / "guides" / "authoring-custom-content.md"
_CHANGELOG = _REPO / "CHANGELOG.md"


def _feature_specs(value, found: list[FeatureSpec]) -> None:
    """Collect every `FeatureSpec` reachable from a value, through models, tuples, lists, and dicts."""
    if isinstance(value, FeatureSpec):
        found.append(value)
    elif isinstance(value, BaseModel):
        for name in type(value).model_fields:
            _feature_specs(getattr(value, name), found)
    elif isinstance(value, tuple | list):
        for item in value:
            _feature_specs(item, found)
    elif isinstance(value, dict):
        for item in value.values():
            _feature_specs(item, found)


def _changelog_sections() -> dict[str, str]:
    """The changelog's text under each `## [...]` heading, keyed by the bracketed name."""
    sections: dict[str, str] = {}
    name = None
    for line in _CHANGELOG.read_text(encoding="utf-8").splitlines():
        match = re.match(r"## \[(.+?)\]", line)
        if match:
            name = match.group(1)
            sections[name] = ""
        elif name is not None:
            sections[name] += line + "\n"
    return sections


def test_the_guide_shows_a_cache_written_for_the_players(tmp_path, monkeypatch):
    # A runnable block in the guide builds a trapped cache with contents, a description, and
    # referee notes. The description names none of the contents and doesn't repeat the notes.
    monkeypatch.chdir(tmp_path)
    caches = []
    for example in find_examples(str(_GUIDE)):
        if "referee_notes" not in example.source or "no-run" in example.prefix_tags():
            continue
        namespace: dict = {"__name__": "__guide_example__"}
        exec(compile(example.source, str(example), "exec"), namespace)
        found: list[FeatureSpec] = []
        _feature_specs(list(namespace.values()), found)
        caches.extend(spec for spec in found if spec.kind == "treasure_cache")
    written = [
        spec
        for spec in caches
        if spec.description
        and spec.referee_notes
        and spec.trap is not None
        and (spec.item_ids or spec.magic_item_ids or spec.valuables or spec.coins.total_coins)
    ]
    assert written, "no runnable guide example builds a trapped, stocked cache with a description and referee notes"
    for spec in written:
        description = spec.description.lower()
        assert spec.referee_notes.lower() not in description
        for item_id in (*spec.item_ids, *spec.magic_item_ids):
            assert item_id.lower() not in description
            assert item_id.replace("_", " ").lower() not in description
        for valuable in spec.valuables:
            assert valuable.name.lower() not in description


def test_the_guide_links_the_two_audiences():
    # The guide's prose points at the field the players read and the field they never do.
    text = _GUIDE.read_text(encoding="utf-8")
    assert "][osrlib.crawl.views.MapFeatureView.description]" in text
    assert "][osrlib.crawl.dungeon.FeatureSpec.referee_notes]" in text


def test_the_changelog_tells_an_upgrading_author():
    # One bullet under 2.8.0 says that the players read a cache's description since 2.6.0
    # and names the new field. No other section is rewritten to say it.
    sections = _changelog_sections()
    bullets = [line for line in sections["2.8.0"].splitlines() if line.startswith("- ")]
    matching = [line for line in bullets if "2.6.0" in line and "referee_notes" in line and "#318" in line]
    assert len(matching) == 1, bullets
    for name, text in sections.items():
        if name != "2.8.0":
            assert "referee_notes" not in text, name
