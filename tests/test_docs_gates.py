"""The documentation gates: the quickstart twins, the vocabulary tripwire, and the
command-contract and reference drift checks.

These are mechanical backstops for the documentation's editorial rules. They catch
drift and phantom entries — a quickstart that diverges between its two homes, a
decision-log term surviving in a public docstring, a command docstring naming a
rejection code or event that doesn't exist, a reference input out of sync with the
source — not prose quality, which belongs to review.
"""

import ast
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "osrlib"

sys.path.insert(0, str(REPO))

from tools.docs.rejection_scan import scan_rejection_codes  # noqa: E402


def _fenced_python_blocks(text: str) -> list[str]:
    return re.findall(r"```python\n(.*?)```", text, re.S)


class TestQuickstartTwins:
    def test_readme_and_package_docstring_carry_the_identical_quickstart(self):
        init_blocks = _fenced_python_blocks((SRC / "__init__.py").read_text(encoding="utf-8"))
        assert len(init_blocks) == 1, "the package docstring carries exactly the quickstart block"
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        quickstart_section = readme.split("## Quickstart", 1)[1].split("##", 1)[0]
        readme_blocks = _fenced_python_blocks(quickstart_section)
        assert len(readme_blocks) == 1, "the README quickstart section carries exactly one block"
        assert readme_blocks[0] == init_blocks[0], "the quickstart twins must be byte-identical"


# The banned decision-log vocabulary: terms that address this repository's reviewers
# rather than a developer who installed the package. Case-sensitive phrase matches.
_BANNED_PATTERNS = (
    r"\bpinned\b",
    r"\bPinned\b",
    r"\bPhase \d",
    r"\bphase \d",
    r"\bthe spec\b",
    r"\bthe audit\b",
    r"\bcensus\b",
    r"\bseam\b",
    r"\bprecedent\b",
    r"\brubber-duck\b",
    r"\bdocs/[\w./-]+",
    r"\bsrd/[\w./-]+",
    r"\btests/[\w./-]+",
)

# Legitimate uses the tripwire must not flag, keyed by file, each with the exact
# allowed occurrence. Review-visible: growing this list is a reviewed decision.
_ALLOWLIST: dict[str, tuple[str, ...]] = {}


def _module_docstrings(path: Path) -> list[tuple[int, str]]:
    """Every docstring in the module, with its line number."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            docstring = ast.get_docstring(node, clean=False)
            if docstring:
                body = node.body[0]
                found.append((body.lineno, docstring))
    return found


@pytest.mark.parametrize("path", sorted(SRC.rglob("*.py")), ids=lambda p: str(p.relative_to(REPO)))
def test_docstrings_carry_no_decision_log_vocabulary(path: Path):
    relative = str(path.relative_to(REPO))
    allowed = _ALLOWLIST.get(relative, ())
    hits: list[str] = []
    for lineno, docstring in _module_docstrings(path):
        for pattern in _BANNED_PATTERNS:
            for match in re.finditer(pattern, docstring):
                context = docstring[max(0, match.start() - 40) : match.end() + 40].replace("\n", " ")
                if any(allowed_phrase in context for allowed_phrase in allowed):
                    continue
                hits.append(f"{relative}:{lineno}: {match.group(0)!r} in ...{context}...")
    assert not hits, "decision-log vocabulary in public docstrings:\n" + "\n".join(hits)


def _section_body(docstring: str, title: str) -> str | None:
    """The indented body of a `Title:` docstring section, or None when absent."""
    match = re.search(rf"^([ \t]*){title}:\n((?:\1[ \t]+\S.*\n|[ \t]*\n)*)", docstring + "\n", re.M)
    return match.group(2) if match else None


_CODE_TOKEN = re.compile(r"`([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+)`")
_EVENT_TOKEN = re.compile(r"`(\w+Event)`")


class TestCommandContract:
    """Every command documents its modes, rejection codes, and emitted events —
    and never a phantom one. Completeness of the lists is editorial; these gates
    check existence and exactness."""

    @pytest.fixture(scope="class")
    def vocabulary(self):
        return set(scan_rejection_codes())

    @pytest.fixture(scope="class")
    def event_names(self):
        from osrlib.crawl.events import ALL_EVENT_CLASSES

        return {cls.__name__ for cls in ALL_EVENT_CLASSES}

    def _commands(self):
        from osrlib.crawl.commands import ALL_COMMAND_CLASSES

        return ALL_COMMAND_CLASSES

    def test_every_command_carries_the_three_sections(self):
        missing = []
        for cls in self._commands():
            docstring = cls.__doc__ or ""
            for title in ("Modes", "Rejections", "Events"):
                if _section_body(docstring, title) is None:
                    missing.append(f"{cls.__name__}: no {title}: section")
        assert not missing, "\n".join(missing)

    def test_documented_modes_equal_allowed_modes(self):
        wrong = []
        for cls in self._commands():
            body = _section_body(cls.__doc__ or "", "Modes")
            if body is None:
                continue
            documented = set(re.findall(r"`([a-z_]+)`", body))
            actual = {mode.value for mode in cls.allowed_modes}
            if documented != actual:
                wrong.append(f"{cls.__name__}: documents {sorted(documented)}, allows {sorted(actual)}")
        assert not wrong, "\n".join(wrong)

    def test_documented_rejection_codes_exist_in_the_source(self, vocabulary):
        wrong = []
        for cls in self._commands():
            body = _section_body(cls.__doc__ or "", "Rejections")
            if body is None:
                continue
            codes = _CODE_TOKEN.findall(body)
            if not codes and "None" not in body:
                wrong.append(f"{cls.__name__}: empty Rejections section")
            for code in codes:
                if code not in vocabulary:
                    wrong.append(f"{cls.__name__}: unknown rejection code {code!r}")
        assert not wrong, "\n".join(wrong)

    def test_documented_events_are_registered_event_classes(self, event_names):
        wrong = []
        for cls in self._commands():
            body = _section_body(cls.__doc__ or "", "Events")
            if body is None:
                continue
            named = _EVENT_TOKEN.findall(body)
            if not named and "None" not in body:
                wrong.append(f"{cls.__name__}: empty Events section")
            for name in named:
                if name not in event_names:
                    wrong.append(f"{cls.__name__}: unknown event class {name!r}")
        assert not wrong, "\n".join(wrong)


class TestReferenceDriftGates:
    def test_every_scanned_rejection_code_has_a_description_and_none_are_stale(self):
        import json

        descriptions = json.loads((REPO / "tools" / "docs" / "rejection_codes.json").read_text(encoding="utf-8"))
        scanned = scan_rejection_codes()
        assert sorted(descriptions) == sorted(scanned), (
            f"undescribed: {sorted(set(scanned) - set(descriptions))}; "
            f"stale: {sorted(set(descriptions) - set(scanned))}"
        )

    def test_the_stream_page_names_every_stream_constant(self):
        import osrlib

        page = (REPO / "docs" / "reference" / "rng-streams.md").read_text(encoding="utf-8")
        import importlib
        import pkgutil

        missing = []
        for info in pkgutil.walk_packages(osrlib.__path__, "osrlib."):
            module = importlib.import_module(info.name)
            for name in getattr(module, "__all__", ()):
                if name.endswith("_STREAM") and f"`{name}`" not in page:
                    missing.append(f"{info.name}.{name}")
        assert not missing, f"stream constants absent from docs/reference/rng-streams.md: {missing}"
