"""The rejection-code source scan shared by the docs generators and the test gates.

The rejection vocabulary has no runtime registry: codes appear only as
`Rejection(code="…")` string literals at their construction sites. This scan walks the
`src/` ASTs and collects them, so the reference page and the command-contract gates key
off the source itself and can never miss a code. A non-literal `code=` argument is an
error by construction — if one ever appears, the scan must grow explicit handling for
it rather than silently skipping it.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "osrlib"


def scan_rejection_codes(src_root: Path = SRC_ROOT) -> dict[str, list[str]]:
    """Collect every `Rejection(code="…")` literal under `src_root`.

    Args:
        src_root: The source tree to walk.

    Returns:
        A mapping of rejection code to the sorted `path:line` construction sites
        that emit it, repo-relative.

    Raises:
        ValueError: If a `Rejection(...)` call passes a non-literal `code=` argument.
    """
    codes: dict[str, list[str]] = {}
    for path in sorted(src_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        relative = path.relative_to(REPO_ROOT)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else None)
            if name != "Rejection":
                continue
            for kw in node.keywords:
                if kw.arg != "code":
                    continue
                if not (isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)):
                    raise ValueError(
                        f"{relative}:{node.lineno}: Rejection(code=...) is not a string literal; "
                        "teach tools/docs/rejection_scan.py how to handle it"
                    )
                codes.setdefault(kw.value.value, []).append(f"{relative}:{node.lineno}")
    return {code: sorted(sites) for code, sites in sorted(codes.items())}
