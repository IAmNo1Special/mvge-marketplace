"""AST lint: all imports in the rune package must be top-level.

Pinned by spec §5.1 — the steering-bridge inline-import-inside-factory
pattern violates Malcom's no-inline-imports rule and is not copied here.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent
PACKAGE_NAME = "mvge_runes_selfmod_bridge"


def _iter_modules() -> list[Path]:
    files: list[Path] = [PACKAGE_DIR / "rune.py", PACKAGE_DIR / "cli.py"]
    files.extend(sorted((PACKAGE_DIR / PACKAGE_NAME).rglob("*.py")))
    return [f for f in files if f.is_file()]


def _find_inline_imports(tree: ast.Module) -> list[int]:
    """Line numbers of Import/ImportFrom nodes not directly under the module."""

    offenders: list[int] = []

    def visit(node: ast.AST, top_level: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                if not top_level:
                    offenders.append(child.lineno)
                # Imports never have import-bearing children; skip descent.
                continue
            visit(child, False)

    visit(tree, True)
    return offenders


def test_no_inline_imports() -> None:
    offenders: list[str] = []
    for path in _iter_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno in _find_inline_imports(tree):
            offenders.append(f"{path.name}:{lineno}")
    assert not offenders, "inline imports found:\n" + "\n".join(offenders)
