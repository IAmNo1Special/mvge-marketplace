"""No Rune may widen the sandbox's privilege set past the forbidden modules.

``MvgeSandbox.execute_code`` accepts an ``allowed_modules`` allowlist. Since
ADR 0015 an entry naming a ``FORBIDDEN_NAMES`` member is refused at the import,
so the allowlist cannot hand back ``os.system`` or ``subprocess.run``.

That closes the capability. It does not stop a Rune from *asking* for it. The
host builds one ``MvgeSandbox`` per session and hands it to every Rune as
``api.sandbox`` through ``RuneLifecycle``, so a Rune that passed
``allowed_modules={"os", "subprocess"}`` would get a refusal at every use --
correct, but discovered one action at a time, on synthesised code nobody
reviewed, with the real cause one layer down from the symptom.

``heal-my-goap`` was the only caller in the marketplace, and its list once
granted command execution outright. A grep found that; a grep is not a gate.
This module is wired into the ordinary ``pytest`` run, so the next Rune to
widen its own privilege set fails the build instead of being noticed by
whoever next reads a security diff.

The forbidden set is imported from ``mvge.core.sandbox`` rather than
restated. A local copy is a second list that drifts from the engine's, which
is exactly the failure ADR 0014's index check exists to prevent.

Scope: this asserts the *configuration*, not the runtime. It cannot see a
module reached by some route other than ``allowed_modules``, and it does not
try to. ``FORBIDDEN_NAMES`` is the engine's rule; this is the marketplace's
check that no Rune asks the engine to disagree with itself.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from mvge.core.sandbox import FORBIDDEN_NAMES

from marketplace_index import Artifact, artifacts

#: Directories that are not published rune code and so are out of scope. Rune
#: test suites legitimately construct hostile allowlists to prove they fail.
_SKIP_DIRS = frozenset({"tests", "__pycache__"})


def _dir(path: Path) -> Path:
    """Create a directory and return it, for the fake artifacts below."""
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass(frozen=True)
class _FakeArtifact:
    """Stand-in for an indexed artifact, so the scanner can be unit tested.

    The scanner is written against ``Artifact`` for its ``path``, which is all
    it reads. Depending on the full dataclass to test a file walk would make
    these tests build a real index entry for no added signal.
    """

    path: Path


@dataclass(frozen=True)
class Violation:
    """One allowlist that names a module the engine refuses."""

    rel_path: str
    line: int
    module: str
    source: str

    def describe(self) -> str:
        """One human-readable line naming the file, line and offending module."""
        return (
            f"{self.rel_path}:{self.line} passes {self.module!r} in "
            f"allowed_modules: {self.source}"
        )


def _string_elements(node: ast.AST) -> set[str]:
    """The string literals in a set, tuple or list display.

    ``set(HEAL_MY_GOAP_ALLOWED_MODULES)`` is a call, not a display, and is
    resolved separately. Returning only literals keeps the check on the
    configuration the author can read at the call site.
    """
    if not isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        return set()
    return {
        element.value
        for element in node.elts
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    }


def _iter_source_files(artifact: Artifact) -> Iterator[Path]:
    """Yield a Rune's own Python modules, skipping its test tier."""
    for path in sorted(artifact.path.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        yield path


def _rel(path: Path) -> str:
    """A repository-relative path for the violation message."""
    try:
        return str(path.relative_to(Path(__file__).resolve().parents[1]))
    except ValueError:
        return str(path)


def _artifact_allowlists(artifact: Artifact) -> dict[str, Violation]:
    """Every allow-list constant a Rune declares, and where it declared it.

    Scoped to the whole artifact rather than one file, because a Rune states
    its privilege set in one module and passes the constant from another:
    ``heal-my-goap`` declares ``HEAL_MY_GOAP_ALLOWED_MODULES`` in ``sandbox.py``
    and passes ``set(HEAL_MY_GOAP_ALLOWED_MODULES)`` from ``rune.py``. Resolving
    per file is what made an injected ``"os"`` pass this gate silently.

    Only module-level assignments of string literals are treated as a
    privilege declaration. That is the shape a Rune uses to state one reviewable
    list, and it is deliberately broader than the call-site check below: a
    declared list is a claim about what the Rune's code may reach, whether or
    not this checkout happens to pass it.
    """
    found: dict[str, Violation] = {}
    for path in _iter_source_files(artifact):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            value = node.value
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
                value = value.args[0] if value.args else None
                if value is None:
                    continue
            names = _string_elements(value)
            if not names:
                continue
            for module in sorted(names & FORBIDDEN_NAMES):
                found.setdefault(
                    target.id,
                    Violation(
                        rel_path=_rel(path),
                        line=node.lineno,
                        module=module,
                        source=f"{target.id} = {ast.unparse(node.value)}",
                    ),
                )
    return found


def _call_violations(path: Path, constants: dict[str, Violation]) -> list[Violation]:
    """Forbidden modules named inline at an ``allowed_modules`` call site."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return []

    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        argument = {kw.arg: kw.value for kw in node.keywords if kw.arg}.get(
            "allowed_modules"
        )
        if argument is None:
            continue

        # `set(CONSTANT)` is resolved against the artifact's declarations;
        # reporting the declaration keeps one finding per real privilege set
        # rather than one per call site.
        if (
            isinstance(argument, ast.Call)
            and isinstance(argument.func, ast.Name)
            and argument.args
            and isinstance(argument.args[0], ast.Name)
        ):
            declared = constants.get(argument.args[0].id)
            if declared is not None:
                violations.append(declared)
                continue
            names: set[str] = set()
        else:
            names = _string_elements(argument)

        for module in sorted(names & FORBIDDEN_NAMES):
            violations.append(
                Violation(
                    rel_path=_rel(path),
                    line=node.lineno,
                    module=module,
                    source=ast.unparse(argument),
                )
            )
    return violations


def all_violations() -> list[Violation]:
    """Every forbidden allowlist entry across every published Rune and Mvge."""
    found: list[Violation] = []
    for artifact in artifacts():
        constants = _artifact_allowlists(artifact)
        for path in _iter_source_files(artifact):
            found.extend(_call_violations(path, constants))
    return found


def test_no_rune_allowlists_a_forbidden_module() -> None:
    """No Rune may name a FORBIDDEN_NAMES member in ``allowed_modules``.

    The engine refuses these at the import, so a Rune passing one cannot reach
    the capability. It can still ask, and the ask is the thing worth catching:
    it is a Rune author deciding that synthesised code should have the host.
    """
    violations = all_violations()
    assert not violations, "\n".join(v.describe() for v in violations)


def test_the_gate_detects_a_forbidden_entry() -> None:
    """Guard the guard.

    A scanner that silently stops matching is worse than no gate, because it
    keeps reporting green. These assert the check fires on both shapes it
    claims to police -- a set literal at the call site, and a named constant
    declared in one module and passed from another, which is the shape
    ``heal-my-goap`` actually uses and the one a per-file scan misses.
    """
    import tempfile

    tmp = Path(tempfile.mkdtemp())

    inline = tmp / "inline.py"
    inline.write_text("s.execute_code(c, allowed_modules={'json', 'os'})")
    assert _call_violations(inline, {})

    dirty = _FakeArtifact(_dir(tmp / "dirty_artifact"))
    (dirty.path / "sandbox.py").write_text(
        "ALLOWED = frozenset({'json', 'subprocess'})"
    )
    (dirty.path / "rune.py").write_text(
        "s.execute_code(c, allowed_modules=set(ALLOWED))"
    )

    constants = _artifact_allowlists(dirty)
    assert "ALLOWED" in constants
    violations = _call_violations(dirty.path / "rune.py", constants)
    assert violations, "a constant declared in another module must still be caught"
    assert violations[0].module == "subprocess"

    clean = _FakeArtifact(_dir(tmp / "clean_artifact"))
    (clean.path / "sandbox.py").write_text("ALLOWED = frozenset({'json', 're'})")
    (clean.path / "rune.py").write_text(
        "s.execute_code(c, allowed_modules=set(ALLOWED))"
    )
    assert not _call_violations(clean.path / "rune.py", _artifact_allowlists(clean))
