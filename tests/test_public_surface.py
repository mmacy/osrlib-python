"""The public-surface gates: `__all__` completeness and the import contract.

Every public module declares a complete `__all__`: the set of public top-level
definitions (functions, classes, module constants) equals the declared exports,
in both directions. Because the comparison counts only names *defined* in the
module — never imported ones — it is also the no-re-export gate: one home per
symbol, no convenience aliases anywhere, the package root included.
"""

import ast
from pathlib import Path

import pytest

import osrlib

PACKAGE_ROOT = Path(osrlib.__file__).parent

MODULE_PATHS = sorted(
    path for path in PACKAGE_ROOT.rglob("*.py") if "data" not in path.parts or path.name == "__init__.py"
)


def _module_name(path: Path) -> str:
    relative = path.relative_to(PACKAGE_ROOT.parent)
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _public_definitions(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return {name for name in names if not name.startswith("_")}


def _declared_all(tree: ast.Module) -> list[str] | None:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    return [element.value for element in node.value.elts]
    return None


@pytest.mark.parametrize("path", MODULE_PATHS, ids=_module_name)
def test_all_is_complete_and_reexport_free(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    definitions = _public_definitions(tree)
    declared = _declared_all(tree)
    if declared is None:
        # Docstring-only modules (the package inits) define nothing and declare nothing.
        assert definitions == set(), f"{_module_name(path)} has public definitions but no __all__"
        return
    missing = definitions - set(declared)
    assert not missing, f"{_module_name(path)} __all__ is missing public definitions: {sorted(missing)}"
    extra = set(declared) - definitions
    assert not extra, f"{_module_name(path)} __all__ exports names it does not define (re-exports): {sorted(extra)}"
