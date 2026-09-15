"""The built-site gates: what the strict build of the documentation site has to contain.

`mkdocs build --strict` is the docs job's own gate, and it fails on a broken link or a warning.
It says nothing about whether a page the generators are meant to write exists, or whether the
JSON Schemas they publish carry prose a tool definition can use. These tests build the site
once into a temporary directory and look.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

_CROSSREF = re.compile(r"\]\[[^\]\s]+\]")


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    site_dir = tmp_path_factory.mktemp("site")
    subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict", "--quiet", "--site-dir", str(site_dir)],
        cwd=REPO,
        check=True,
    )
    return site_dir


def _schema_definitions(site: Path, artifact: str) -> dict:
    document = json.loads((site / "reference" / artifact).read_text(encoding="utf-8"))
    return document.get("$defs") or document.get("definitions") or {}


class TestLayerFrontPages:
    """`osrlib.core` and `osrlib.crawl` carry a package docstring each; the site publishes both."""

    @pytest.mark.xfail(reason="chunk: docs-tooling")
    def test_the_kernel_front_page_is_published(self, site: Path):
        page = site / "reference" / "api" / "osrlib" / "core" / "index.html"
        assert page.exists()
        assert "The rules kernel" in page.read_text(encoding="utf-8")

    @pytest.mark.xfail(reason="chunk: docs-tooling")
    def test_the_crawl_front_page_is_published(self, site: Path):
        page = site / "reference" / "api" / "osrlib" / "crawl" / "index.html"
        assert page.exists()
        assert "The crawl framework" in page.read_text(encoding="utf-8")


class TestSchemaProse:
    """The published schemas describe every model and every field in plain prose.

    A tool definition built from `commands.json` or `events.json` reaches an LLM as text, so a
    description holds no mkdocstrings cross-reference syntax, and each property carries the prose
    its attribute docstring gives it.
    """

    @pytest.mark.xfail(reason="chunk: docs-tooling")
    @pytest.mark.parametrize("artifact", ["commands/commands.json", "events/events.json"])
    def test_descriptions_carry_no_crossref_markup(self, site: Path, artifact: str):
        offenders = []
        for name, definition in _schema_definitions(site, artifact).items():
            if _CROSSREF.search(definition.get("description", "")):
                offenders.append(name)
            for field, spec in definition.get("properties", {}).items():
                if _CROSSREF.search(spec.get("description", "")):
                    offenders.append(f"{name}.{field}")
        assert offenders == []

    @pytest.mark.xfail(reason="chunk: docs-tooling")
    def test_every_property_is_described(self, site: Path):
        missing = []
        for artifact in ("commands/commands.json", "events/events.json"):
            for name, definition in _schema_definitions(site, artifact).items():
                for field, spec in definition.get("properties", {}).items():
                    if not spec.get("description", "").strip():
                        missing.append(f"{name}.{field}")
        assert missing == []

    @pytest.mark.xfail(reason="chunk: docs-tooling")
    def test_the_catalog_pages_render_no_crossref_markup(self, site: Path):
        offenders = []
        for catalog in ("commands", "events"):
            for page in sorted((site / "reference" / catalog).glob("*/index.html")):
                if _CROSSREF.search(page.read_text(encoding="utf-8")):
                    offenders.append(f"{catalog}/{page.parent.name}")
        assert offenders == []
