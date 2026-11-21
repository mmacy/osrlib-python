"""The built-site gates: what the strict build of the documentation site has to contain.

`mkdocs build --strict` is the docs job's own gate, and it fails on a broken link or a warning.
It says nothing about whether a page the generators are meant to write exists, or whether the
JSON Schemas they publish carry prose a tool definition can use. These tests build the site
once into a temporary directory and look.
"""

import html
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from test_schema_descriptions import _descriptions, _without_references

REPO = Path(__file__).resolve().parent.parent

# The page's content, without the navigation, the search, and the scripts around it.
_ARTICLE = re.compile(r"<article\b[^>]*>(.*?)</article>", re.DOTALL)

# A code block, which on a catalog page is the JSON Schema.
_PRE = re.compile(r"<pre\b[^>]*>(.*?)</pre>", re.DOTALL)

# A code span in the page's prose.
_CODE = re.compile(r"<code\b[^>]*>(.*?)</code>", re.DOTALL)

_TAG = re.compile(r"<[^>]+>")


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    site_dir = tmp_path_factory.mktemp("site")
    subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict", "--quiet", "--site-dir", str(site_dir)],
        cwd=REPO,
        check=True,
    )
    return site_dir


def _schema_document(site: Path, artifact: str) -> dict:
    return json.loads((site / "reference" / artifact).read_text(encoding="utf-8"))


def _schema_definitions(site: Path, artifact: str) -> dict:
    document = _schema_document(site, artifact)
    return document.get("$defs") or document.get("definitions") or {}


def _text(fragment: str) -> str:
    return html.unescape(_TAG.sub("", fragment))


def _page_parts(page: Path) -> tuple[str, list[dict]]:
    """Return a rendered page's prose, as Markdown with its code spans in backticks, and its JSON Schemas."""
    article = _ARTICLE.search(page.read_text(encoding="utf-8"))
    assert article, f"{page} has no article"
    schemas = [json.loads(_text(block)) for block in _PRE.findall(article.group(1))]
    prose = _CODE.sub(lambda span: f"`{_text(span.group(1))}`", _PRE.sub("", article.group(1)))
    return _text(prose), schemas


class TestLayerFrontPages:
    """`osrlib.core` and `osrlib.crawl` carry a package docstring each; the site publishes both."""

    def test_the_kernel_front_page_is_published(self, site: Path):
        page = site / "reference" / "api" / "osrlib" / "core" / "index.html"
        assert page.exists()
        assert "The rules kernel" in page.read_text(encoding="utf-8")

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

    @pytest.mark.parametrize("artifact", ["commands/commands.json", "events/events.json"])
    def test_descriptions_carry_no_crossref_markup(self, site: Path, artifact: str):
        offenders = [
            path for path, text in _descriptions(_schema_document(site, artifact)) if _without_references(text) != text
        ]
        assert offenders == []

    def test_every_property_is_described(self, site: Path):
        missing = []
        for artifact in ("commands/commands.json", "events/events.json"):
            for name, definition in _schema_definitions(site, artifact).items():
                for field, spec in definition.get("properties", {}).items():
                    if not spec.get("description", "").strip():
                        missing.append(f"{name}.{field}")
        assert missing == []

    def test_the_catalog_pages_render_no_crossref_markup(self, site: Path):
        offenders = []
        schemas_found = 0
        for catalog in ("commands", "events"):
            for page in sorted((site / "reference" / catalog).glob("*/index.html")):
                prose, schemas = _page_parts(page)
                schemas_found += len(schemas)
                if _without_references(prose) != prose:
                    offenders.append(f"{catalog}/{page.parent.name}")
                for schema in schemas:
                    offenders.extend(
                        f"{catalog}/{page.parent.name}{path}"
                        for path, text in _descriptions(schema)
                        if _without_references(text) != text
                    )
        assert schemas_found, "the catalog pages contain JSON Schemas"
        assert offenders == []
