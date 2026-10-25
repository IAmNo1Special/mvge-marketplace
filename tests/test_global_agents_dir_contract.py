"""No artifact may reach the global ``.agents`` layer through ``Path.home()``.

The global layer has exactly one resolver: ``global_agents_dir()`` from
``mvge.core``, which honours ``$MVGE_GLOBAL_DIR`` and otherwise returns
``~/.agents``. Import it from the package root rather than
``mvge.core.constants``: the root is the public surface, and it re-exports
the resolvers whether they live in ``constants`` or in the newer ``layers``
module. Spelling the same directory out as
``Path.home() / ".agents"`` produces a path that looks correct and is not:
under a relocated global dir the two disagree, and the divergence is silent.

The serious direction of that silence is ``approval-rune``. The engine's
installer resolves the purge target through ``global_agents_dir()``
(``mvge.runes/installer.py::_approval_dir``), so under
``MVGE_GLOBAL_DIR=/tmp/x``:

- the gate wrote its grants to ``~/.agents/approval/policy.toml``
- ``mvge rune uninstall approval-rune`` deleted
  ``/tmp/x/approval/policy.toml``

**The grants the uninstall claims to delete survive.** Security-relevant
state outlives the operation meant to revoke it. The other affected runes
cost hermeticity rather than safety, which is why they stayed invisible
longer.

Why a gate and not a review rule: a handful of greps found these in under a
minute, and a grep is not a gate. This module is wired into the ordinary
``pytest`` run (CI runs bare ``pytest``, per the README), so the next bypass
fails the build instead of being noticed by whoever next relocates the
global dir.

How it decides
--------------
A violation is a **path-join chain** -- an ``a / b / c`` expression -- that
both resolves home and contains the ``.agents`` segment. Home resolution is
followed one hop through simple local names (``base = Path.home()`` then
``base / ".agents"``, or ``base = g if g else Path.home()``), which is the
shape every known bypass actually used. Chaining on the expression rather
than the statement is what keeps ``~/.claude/skills`` and a project-relative
``Path(".agents/skills")`` out of the results when they merely share a list
literal with a legitimate home path.

Two deliberate exclusions:

- ``~/.claude/skills`` (see ``seeker/dci_matcher.py``) is a third-party
  convention, not our layer. ``Path.home()`` is the right answer there, and
  the check leaves it alone because no ``.agents`` segment is joined on.
- Project-relative ``<cwd>/.agents/...`` is a different layer.

Known limitation. Taint follows names bound directly in the same module and
propagates only one hop. A bypass hidden behind a function call
(``def _root(): return Path.home()`` then ``_root() / ".agents"``) is not
seen. That is a deliberate trade: a resolver indirection is exactly how the
correct code is written, so following calls would flag the fixes too.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from marketplace_index import Artifact, artifacts

#: The directory name that marks a path as belonging to *our* global layer.
GLOBAL_LAYER_SEGMENT = ".agents"

#: Attribute names that resolve the user's real home directory.
_HOME_ATTRS = frozenset({"home", "expanduser"})

#: Receivers of those attributes: ``Path.home()``, ``pathlib.Path.home()``,
#: ``os.path.expanduser()``, and their usual aliases. An aliased *import*
#: (``from pathlib import Path as P``) is not tracked -- see the module
#: docstring's stated limits.
_HOME_RECEIVERS = frozenset({"Path", "path", "pathlib", "os"})

#: Directories that are not published rune code and so are out of scope.
#: Test fixtures legitimately construct fake ``.agents`` layouts.
_SKIP_DIRS = frozenset({"tests", "__pycache__"})


@dataclass(frozen=True)
class Violation:
    """One path-join chain that reaches the global layer through home."""

    rel_path: str
    line: int
    source: str

    def describe(self) -> str:
        """One human-readable line naming the file, line and offending call."""
        return (
            f"{self.rel_path}:{self.line} resolves the global "
            f"{GLOBAL_LAYER_SEGMENT!r} layer through {self.source}"
        )


def _is_home_resolution(node: ast.AST) -> bool:
    """Whether ``node`` is a call (or bare attribute) that yields ``~``."""
    func = node.func if isinstance(node, ast.Call) else node
    if not (isinstance(func, ast.Attribute) and func.attr in _HOME_ATTRS):
        return False
    # Either ``Path.home()`` or ``pathlib.Path.home()``.
    receiver = func.value
    return (isinstance(receiver, ast.Name) and receiver.id in _HOME_RECEIVERS) or (
        isinstance(receiver, ast.Attribute) and receiver.attr in {"Path", "path"}
    )


def _mentions_home(node: ast.AST) -> bool:
    """Whether any node in the subtree is a direct home resolution."""
    return any(_is_home_resolution(child) for child in ast.walk(node))


def _home_names(tree: ast.Module) -> set[str]:
    """Names bound to an expression that resolves home.

    One hop of taint, which is what the known bypasses needed:
    ``base = Path.home()`` / ``base = g if g is not None else Path.home()``.
    Not a dataflow analysis; see the module docstring's stated limit.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign | ast.AnnAssign):
            continue
        # AnnAssign.value is None for a bare annotation (``x: int``).
        if node.value is None or not _mentions_home(node.value):
            continue
        targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _joined_segments(node: ast.AST) -> set[str]:
    """Path segments contributed by a ``/`` chain, split on ``/``."""
    segments: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            # Normalise Windows separators, then let pathlib do the splitting:
            # a string literal in source describes a path, and the repo
            # standard is pathlib for path work.
            literal = child.value.replace("\\", "/")
            segments.update(PurePosixPath(literal).parts)
    return segments


def _chain_resolves_home(node: ast.AST, names: set[str]) -> bool:
    """Whether a ``/`` chain reaches home directly or through a tainted name."""
    for child in ast.walk(node):
        if _is_home_resolution(child):
            return True
        if isinstance(child, ast.Name) and child.id in names:
            return True
    return False


def _is_join(node: ast.AST) -> bool:
    return isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)


def _is_outermost_join(node: ast.BinOp, parents: dict[ast.AST, ast.AST]) -> bool:
    """True for the top of a ``/`` chain, so one mistake reports once."""
    parent = parents.get(node)
    return not _is_join(parent) if parent is not None else True


def violations_in_source(source: str, rel_path: str) -> list[Violation]:
    """Every ``/`` chain in ``source`` reaching the global layer via home."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # A module the interpreter cannot parse is reported by the loader
        # check in test_index_consistency.py, not here.
        return []

    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    names = _home_names(tree)
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not _is_join(node) or not _is_outermost_join(node, parents):
            continue
        if GLOBAL_LAYER_SEGMENT not in _joined_segments(node):
            continue
        if not _chain_resolves_home(node, names):
            continue
        violations.append(
            Violation(
                rel_path=rel_path,
                line=node.lineno,
                source=ast.unparse(node).strip(),
            )
        )
    violations.sort(key=lambda v: (v.rel_path, v.line))
    return violations


def _artifact_sources(artifact: Artifact) -> Iterator[Path]:
    """Every non-test Python module an artifact publishes."""
    for candidate in sorted(artifact.path.rglob("*.py")):
        relative = candidate.relative_to(artifact.path)
        if any(part in _SKIP_DIRS for part in relative.parts):
            continue
        yield candidate


def all_violations() -> list[Violation]:
    """Scan every indexed artifact for global-layer bypasses."""
    found: list[Violation] = []
    for artifact in artifacts():
        for source_path in _artifact_sources(artifact):
            found.extend(
                violations_in_source(
                    source_path.read_text(encoding="utf-8"),
                    source_path.relative_to(artifact.path.parent).as_posix(),
                )
            )
    found.sort(key=lambda v: (v.rel_path, v.line))
    return found


def test_no_artifact_reaches_the_global_layer_through_home() -> None:
    """The gate. Every published module resolves the global layer centrally."""
    violations = all_violations()
    assert not violations, (
        "These modules resolve the global "
        f"{GLOBAL_LAYER_SEGMENT!r} layer through Path.home() instead of "
        "mvge.core.global_agents_dir(), so they ignore "
        "$MVGE_GLOBAL_DIR:\n"
        + "\n".join(f"  {v.describe()}" for v in violations)
        + "\nRoute these through global_agents_dir() (or a named helper such "
        "as approval_dir(), skills_dir() or auth_dir()), imported from the "
        "mvge.core package root, and resolve at call time rather than at "
        "import."
    )


def test_the_gate_would_catch_a_regression() -> None:
    """The gate has teeth: a planted bypass is reported, and reported once.

    A check that cannot fail is worse than no check, so this asserts the
    detector against the exact shapes of every known bypass, including the
    alias hop that four of them relied on.
    """
    cases = [
        # approval-rune/gate.py
        'X = Path.home() / ".agents" / "approval"\n',
        # otel/config.py: home joined with the layer directly
        'g = global_dir or (Path.home() / ".agents")\n',
        # mcp-bridge/config.py: the home call sits in a conditional expression
        # and the layer is joined onto the bound name one statement later
        (
            "user_base = global_dir if global_dir is not None else Path.home()\n"
            'user_path = user_base / ".agents" / "mcp.json"\n'
        ),
        # seeker/dci_matcher.py: home and the layer in one list literal
        'roots = [(Path.home() / ".agents" / "skills").resolve()]\n',
        # rune auth path: split across two statements
        (
            "home_dir = Path.home()\n"
            'auth = home_dir / ".agents" / ".mvge"\n'
            'auth = auth / "auth" / "openrouter.json"\n'
        ),
        # deeper than one segment, and nested several joins deep
        'P = Path.home() / ".agents" / "skills" / "nested"\n',
        # the fully-qualified receiver
        ('import pathlib\nP = pathlib.Path.home() / ".agents" / "skills"\n'),
    ]
    for source in cases:
        violations = violations_in_source(source, "planted.py")
        assert len(violations) == 1, (
            f"expected exactly one violation for {source!r}, got {violations}"
        )


def test_the_gate_does_not_flag_home_paths_outside_the_layer() -> None:
    """Third-party home conventions and other layers stay untouched."""
    allowed = [
        # seeker/dci_matcher.py:20 -- a third-party convention, correctly
        # home-relative, sharing a list literal with a project-relative path
        (
            '_DEFAULT_SKILL_DIRS = [Path.home() / ".claude/skills", '
            'Path(".agents/.mvge/skills")]\n'
        ),
        'D = (Path.home() / ".claude" / "skills").resolve()\n',
        # session-search: a home path that is not our layer
        'D = Path.home() / ".gemini" / "antigravity" / "brain"\n',
        # project-relative layer, with no home resolution anywhere
        'D = (Path(".agents/skills")).resolve()\n',
        # the correct shape: routed through the single resolver
        ("from mvge.core import skills_dir\nD = skills_dir()\n"),
        # a user-supplied path being expanded is not a home resolution
        "def f(p: Path) -> Path:\n    return p.expanduser() / '.agents'\n",
        # expanduser('~') for a non-layer directory
        'T = os.path.expanduser("~") / "AppData"\n',
    ]
    for source in allowed:
        violations = violations_in_source(source, "allowed.py")
        assert not violations, f"unexpected violation for {source!r}: {violations}"


def test_the_gate_scans_every_indexed_artifact() -> None:
    """The scan is index-driven, so publishing a rune extends the gate.

    Guards against the check silently covering nothing -- the failure mode
    that turns a gate into decoration.
    """
    scanned = {
        source.relative_to(artifact.path).as_posix()
        for artifact in artifacts()
        for source in _artifact_sources(artifact)
    }
    assert scanned, "no artifact modules were scanned; the gate is vacuous"
    assert "rune.py" in scanned, (
        "artifact entry points are not being scanned, so a bypass could hide "
        "in the one module the engine actually imports"
    )
