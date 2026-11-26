"""The printed-output gate for documentation examples.

`tests/test_docs_examples.py` runs every fenced Python block in the package docstrings and the
site pages, but it never compares what a block prints with the comment lines that show the
reader the expected result. This gate does. For every top-level `print(...)` statement in a
runnable block, the comment lines directly under it are the expected output, line for line.

The rules are deliberately small. Only a comment line that starts on the line right after a
top-level print statement counts, and the expectation ends at the first line that is not a
comment, so a blank line before an explanatory comment keeps it out of the comparison. The
leading `#` and one following space are stripped. Lines compare exactly after trailing
whitespace is removed. A print with no comment lines under it asserts nothing, and a print
nested in a loop, a function, or a conditional is not checked at all.
"""

import ast
import contextlib
import io
from pathlib import Path

import pytest
from pytest_examples import CodeExample, EvalExample, find_examples

_REPO = Path(__file__).resolve().parent.parent

# The same surface the examples harness runs.
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


def _is_print(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "print"
    )


def expected_output(lines: list[str], statement: ast.stmt) -> list[str] | None:
    """The comment lines directly under a statement, as expected output, or `None` when there are none."""
    expected: list[str] = []
    for line in lines[statement.end_lineno or statement.lineno :]:
        if not line.startswith("#"):
            break
        expected.append(line[1:].removeprefix(" ").rstrip())
    return expected or None


def check_block(source: str, filename: str) -> list[str]:
    """Run one block statement by statement and return every printed-output mismatch."""
    lines = source.splitlines()
    tree = ast.parse(source, filename=filename)
    namespace: dict[str, object] = {"__name__": "__main__"}
    mismatches: list[str] = []
    for statement in tree.body:
        module = ast.Module(body=[statement], type_ignores=[])
        code = compile(module, filename, "exec")
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            exec(code, namespace)  # noqa: S102
        if not _is_print(statement):
            continue
        expected = expected_output(lines, statement)
        if expected is None:
            continue
        printed = [line.rstrip() for line in captured.getvalue().rstrip("\n").splitlines()]
        if printed != expected:
            mismatches.append(f"line {statement.lineno}: printed {printed!r}, the comment says {expected!r}")
    return mismatches


# Blocks the gate fails on today. The lead removes an entry when its chunk merges; a block's id
# carries its line range, so editing the block retires the mark by itself.
_EXPECTED_TO_FAIL: dict[str, str] = {}


def _cases() -> list:
    cases = []
    for example in find_examples(*(str(source) for source in _EXAMPLE_SOURCES)):
        reason = _EXPECTED_TO_FAIL.get(str(example))
        marks = [pytest.mark.xfail(reason=reason)] if reason else []
        cases.append(pytest.param(example, id=str(example), marks=marks))
    return cases


@pytest.mark.parametrize("example", _cases())
def test_printed_output_matches_the_comments(
    example: CodeExample, eval_example: EvalExample, monkeypatch: pytest.MonkeyPatch
) -> None:
    if _NO_RUN_TAG in example.prefix_tags():
        pytest.skip("no-run block: a narrated fragment or server-dependent code")
    monkeypatch.chdir(eval_example.tmp_path)
    mismatches = check_block(example.source, str(example.path))
    assert mismatches == [], "\n".join(mismatches)
