"""The documentation examples harness.

Every fenced Python block in the package docstrings, the README, and the user-facing
site pages is a test: it must lint clean and run to completion. Blocks tagged with the
`no-run` fence tag (```{.python .no-run}) are exempt from execution — they are either
narrated fragments whose complete runnable twin appears on the same page, or code that
genuinely cannot run self-contained (client code against a live server, interactive
transcripts).

House rules for runnable examples: self-contained, deterministic (fixed seeds), any
file I/O under the harness-supplied temporary working directory, and assertions on
stable facts rather than volatile reprs.
"""

from pathlib import Path

import pytest
from pytest_examples import CodeExample, EvalExample, find_examples

_REPO = Path(__file__).resolve().parent.parent

# The user-facing example surface. The design documents in docs/ (the spec, the phase
# plans, the audit) are excluded from the published site and from this harness; the
# reference pages generated at site build time never exist on disk.
_EXAMPLE_SOURCES = (
    _REPO / "src",
    _REPO / "README.md",
    _REPO / "docs" / "index.md",
    _REPO / "docs" / "adaptations.md",
    _REPO / "docs" / "getting-started",
    _REPO / "docs" / "guides",
    _REPO / "docs" / "front-ends",
    _REPO / "docs" / "reference",
)

_NO_RUN_TAG = "no-run"


@pytest.mark.parametrize("example", list(find_examples(*(str(source) for source in _EXAMPLE_SOURCES))), ids=str)
def test_docs_example(example: CodeExample, eval_example: EvalExample, monkeypatch: pytest.MonkeyPatch) -> None:
    if _NO_RUN_TAG in example.prefix_tags():
        # Fragments reference names their runnable twin defines, so they cannot
        # lint (or run) in isolation.
        pytest.skip("no-run block: a narrated fragment or server-dependent code")
    eval_example.set_config(
        line_length=120,
        ruff_line_length=120,
        target_version="py311",
        ruff_select=["B", "E", "F", "I", "UP", "W"],
        # Ruff discovers the repo pyproject, whose pydocstyle rules are for
        # modules, not example snippets.
        ruff_ignore=["D"],
    )
    eval_example.lint(example)
    monkeypatch.chdir(eval_example.tmp_path)
    eval_example.run(example)
