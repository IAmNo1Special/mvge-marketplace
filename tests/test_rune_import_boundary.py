"""No Rune may import another Rune's package. Nothing else may either.

This is the boundary the whole marketplace is sold on. A Rune may import
engine packages -- ``mvgeos_core``, ``mvgeos_runes``, ``mvgeos_agent`` -- and
never another Rune. That absolute rule is what makes each Rune independently
installable into ``~/.agents/extensions/`` without dragging a sibling along.
An artifact that imports a sibling does not work when installed on its own,
and the failure is a ``ModuleNotFoundError`` in someone else's session.

``mvgeos_agent`` is allowed, with one extra rule: never at module scope.
``mvgeos_agent/__init__`` imports ``agent_session``, which imports
``mvgeos_runes.rune_runner`` -- the loader that loads Rune modules. A Rune
module imported during that chain sees a half-built ``mvgeos_agent``, so a
module-scope ``from mvgeos_agent import Mvge`` raises and the loader reports a
load failure rather than the initialization cycle it actually is. Import it
inside the function that uses it, or under ``if TYPE_CHECKING:`` if the name is
only ever an annotation. The window is latent today -- all three loader call
sites are inside functions -- so this closes a failure mode before something
else opens it. ``_MODULE_SCOPE_FORBIDDEN`` and ``_import_time_statements`` carry
the rule.

The rule extends to test suites, and that extension is the point of this gate.
Before this gate, four test files across two Runes imported a sibling, and
they did it in four different shapes -- see
``test_the_gate_would_catch_a_regression`` for the inventory, which is the
actual content of upstream issue #5. Two of the four failed outright when
their Rune's suite ran standalone. The other two passed, which is worse:

- ``selfmod-bridge/tests/test_chain.py`` put the sibling's root on
  ``sys.path`` for the duration of one import, so it worked standalone by
  construction.
- ``session-search/tests/test_session_search.py`` guarded its sibling import
  with ``pytest.importorskip``, so the day ``session-search`` gained its own
  ``[tool.pytest.ini_options]`` -- the natural next edit, and what its seven
  siblings already had -- the test did not fail. It *skipped*. Coverage
  vanished and nothing went red.

That second shape is why this gate is static. The four violations were
reachable by four mechanisms: a hard ``import``, a hard ``from ... import``,
``importlib.import_module`` with a scoped ``sys.path`` insert, and
``pytest.importorskip``. A behavioural gate -- run each Rune's suite and fail
if it collects anything the Rune's own ``pythonpath`` cannot resolve -- cannot
see the ``importorskip`` shape at all, because that shape is *designed* to
resolve to a skip. So the behavioural property is asserted where it belongs:
by actually running ``pytest runes/<rune>/tests`` standalone, one Rune at a
time. This gate covers the property a skip cannot hide, and it fails before
anyone runs pytest at all.

Why not widen one of the two existing gates
------------------------------------------
Neither covers this class, and merging them would lose their names:

- ``mvgeos-core/scripts/check_marketplace_contract.py`` is engine-side. It
  asserts a symbol exists and that a package-root import exposes it in
  ``__all__``. That is import *resolution*: does this name work at all. It has
  no view of an import graph, so it cannot see that a test reached sideways
  into a package that resolves perfectly well. An engine-side gate cannot even
  see this repository's tree.
- ``tests/test_global_agents_dir_contract.py`` is marketplace-side and
  AST-based, but its ``_SKIP_DIRS = {"tests", "__pycache__"}`` excludes test
  directories by design -- a test fixture is allowed to construct a fake
  ``.agents`` layout -- and it matches only ``Path.home()`` plus ``.agents``
  join chains. Right gate, different failure class: resolver bypass, not
  sibling reach-across.

The one documented exception
----------------------------
``tests/test_cross_rune_integration.py`` is the only file in the repository
permitted to import a sibling, and it is whitelisted by path in
``CROSS_RUNE_INTEGRATION`` below. It earns that by owning the two facts that
genuinely need two Runes to state: that session-search can index a Pi session
via pi-codec's codec, and that selfmod-bridge and steering-bridge chain
cleanly on one Sigil.

**That constant is the most dangerous line in this file.** Widening it is the
cheapest way to make the gate lie, and it is a one-line edit, so it needs a
guard that does not live in the same place. It has one:
``test_only_the_documented_exception_may_import_a_sibling`` recomputes which
repo-tier files actually reach across, independently of the scan's own skip
logic, and holds the exemption to exactly that set. So widening fails in both
directions -- a new file that reaches across is caught even if the skip was
widened to allow it, and a widening that exempts a file reaching across
nothing is caught as dead config. Do not widen it. If a new test needs two
Runes, make it a case in that one file, where the exception is visible in
review.

How it decides
--------------
Sibling identity comes from ``index.json``, not from directory naming: an
artifact publishes the importable package directories beside its
``manifest.json``, which ``Artifact.top_level_names`` already computes and
``tests/test_index_consistency.py`` already checks against the index. So
publishing a new Rune automatically extends this gate, and a name that no
indexed artifact owns is not a sibling.

Every module in an artifact is scanned, split into the two tiers that carry
different weight:

``runtime``
    Published code. A sibling import here is the product defect.
``tests``
    The Rune's own suite, which must stay installable on its own. Adding a
    sibling to a ``pythonpath`` to make a suite pass is not a workaround, it
    is the erosion: it makes the violation permanent, invisible, and ties the
    suite to a directory the Rune will not have when it is installed alone.

The repository's own ``tests/`` and root ``conftest.py`` form a third tier
scanned against the same rule, with the single exception above.

Known limitations
-----------------
Three, all deliberate:

- A dynamic import whose module name is computed (``import_module(name)``)
  is not seen. Resolving a name would mean running the module.
- ``import rune`` is not a sibling import by this measure. Every artifact ships
  a top-level ``rune.py``, so the name is ambiguous by construction and no
  static check can attribute it. The engine loads ``rune`` from the Rune's own
  directory; that is why ``conftest.py`` and ``marketplace_index.py`` both avoid
  putting every Rune root on ``sys.path`` at once. This gate does not
  attempt it.
- A ``sys.path`` mutation naming a sibling's *directory* is not flagged on its
  own, because reading a sibling's fixture file is legitimate and the shape
  overlaps with the import checks above. The permanent version of the problem
  is covered instead by
  ``test_no_artifact_puts_a_sibling_on_its_own_pythonpath``: a sibling in a
  Rune's declared ``pythonpath`` is a config fact with no innocent reading, and
  it is exactly the edit this gate exists to make impossible to get away with.
"""

from __future__ import annotations

import ast
import tomllib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from marketplace_index import (
    REPO_ROOT,
    Artifact,
    artifacts,
    pytest_config,
)

#: The single path in the repository allowed to import a sibling artifact.
#:
#: Read the module docstring before touching this. It is the whole reason this
#: gate has teeth, and widening it is how they get removed.
CROSS_RUNE_INTEGRATION = "tests/test_cross_rune_integration.py"

#: Repo-tier paths exempt from the sibling-import rule. Kept as a collection
#: rather than an ``if`` inside the scan so that
#: ``test_only_the_documented_exception_may_import_a_sibling`` can hold it to
#: exactly one entry that is actually reaching across. A whitelist nothing
#: indexes is the easiest kind of gate to widen without noticing.
_EXEMPT_REPO_TIER = frozenset({CROSS_RUNE_INTEGRATION})

#: Callables that import a module by name at runtime instead of by statement.
#:
#: ``importorskip`` is the important one: it is the shape that turns a broken
#: sibling import into a green skip, which is how issue #5's hazard survived
#: long enough to be filed. ``find_spec`` probes for the same reason -- to
#: decide whether to take a different branch -- and ``__import__`` is the
#: builtin spelling of the same move.
_DYNAMIC_IMPORT_CALLS = frozenset(
    {"__import__", "find_spec", "import_module", "importorskip"}
)

#: Engine packages a Rune may import, but only lazily. See
#: ``_module_scope_violations_in_source`` for why this one is different.
#:
#: ``mvgeos_agent/__init__`` imports ``agent_session``, which imports
#: ``mvgeos_runes.rune_runner`` -- the loader that loads Rune modules. So during
#: that import chain ``mvgeos_agent`` is in ``sys.modules`` with one line
#: executed: ``Mvge`` is unbound and ``__all__`` is unset. A Rune imported at
#: that moment, with a module-scope ``from mvgeos_agent import Mvge``, fails to
#: import, and the loader reports it as a load failure rather than as the
#: initialization cycle it is. The window is latent today -- all three loader
#: call sites are inside functions -- and this rule closes it before something
#: else opens it.
_MODULE_SCOPE_FORBIDDEN = frozenset({"mvgeos_agent"})

#: Directories that hold no Python source we publish.
_SKIP_DIRS = frozenset({"__pycache__"})

#: The repository's own test tier: what may import a sibling only if it is
#: ``CROSS_RUNE_INTEGRATION``.
_ROOT_CONFTEST = "conftest.py"


@dataclass(frozen=True)
class Violation:
    """One module reaching into a sibling artifact's package."""

    rel_path: str
    line: int
    source: str
    tier: str
    sibling: str
    sibling_path: str

    def describe(self) -> str:
        """One human-readable line naming the file, line and reach-across."""
        return (
            f"{self.rel_path}:{self.line} [{self.tier}] imports "
            f"{self.sibling!r} ({self.sibling_path}), a different artifact"
        )


@dataclass(frozen=True)
class ModuleScopeViolation:
    """One module importing a lazily-imported engine package at module scope."""

    rel_path: str
    line: int
    source: str
    tier: str
    package: str

    def describe(self) -> str:
        """One human-readable line naming the file, line and the rule."""
        return (
            f"{self.rel_path}:{self.line} [{self.tier}] imports "
            f"{self.package!r} at module scope. It must be imported inside the "
            "function that uses it, or under `if TYPE_CHECKING:`."
        )


def _root_name(dotted: str) -> str:
    """The top-level package of a dotted module path."""
    return dotted.partition(".")[0]


@cache
def _package_owners() -> dict[str, Artifact]:
    """Top-level import name -> the one artifact that publishes it.

    A collision would make "is this a sibling?" undecidable, so it is an
    error rather than a last-writer-wins overwrite. Both the gate and
    ``test_artifact_package_names_are_uniquely_owned`` surface it.

    Cached because ``artifacts()`` re-reads ``index.json`` and every
    ``manifest.json`` on each call and this runs once per scanned module.
    """
    owners: dict[str, Artifact] = {}
    for artifact in artifacts():
        for name in artifact.top_level_names:
            clash = owners.get(name)
            if clash is not None:
                raise AssertionError(
                    f"{name!r} is published by both {clash.name!r} and "
                    f"{artifact.name!r}, so this gate cannot tell which "
                    "artifact an import of it reaches for"
                )
            owners[name] = artifact
    return owners


def _imported_roots(tree: ast.Module) -> Iterator[tuple[int, str, str]]:
    """Every module a module reaches for, as ``(line, top-level name, source)``.

    Covers all four mechanisms from issue #5: ``import``, ``from ... import``,
    and the two runtime-by-name spellings ``import_module`` and
    ``importorskip``. A relative import (``level > 0``) is already inside the
    artifact and is skipped.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, _root_name(alias.name), ast.unparse(node)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                yield node.lineno, _root_name(node.module), ast.unparse(node)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                called = func.attr
            elif isinstance(func, ast.Name):
                called = func.id
            else:
                called = ""
            if called in _DYNAMIC_IMPORT_CALLS and node.args:
                first = node.args[0]
                if not isinstance(first, ast.Constant):
                    continue
                if isinstance(first.value, str):
                    yield (
                        node.lineno,
                        _root_name(first.value),
                        ast.unparse(node),
                    )


def violations_in_source(
    source: str, rel_path: str, tier: str, owner_path: Path | None
) -> list[Violation]:
    """Sibling imports of the artifact rooted at ``owner_path``.

    ``owner_path`` is ``None`` for the repository's own test tier, which belongs
    to no artifact and so may reach every sibling that is not whitelisted. It
    is a path rather than an ``Artifact`` because ``artifacts()`` builds fresh
    instances on every call, so identity comparison against one would silently
    never match and would report every artifact as its own sibling.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # A module the interpreter cannot parse is reported by the loader check
        # in test_index_consistency.py, not here.
        return []

    owners = _package_owners()
    found: dict[tuple[int, str], Violation] = {}
    for line, name, raw in _imported_roots(tree):
        sibling = owners.get(name)
        if sibling is None or sibling.path == owner_path:
            continue
        found[(line, name)] = Violation(
            rel_path=rel_path,
            line=line,
            source=raw,
            tier=tier,
            sibling=sibling.name,
            sibling_path=sibling.relative_path,
        )
    return [found[key] for key in sorted(found)]


def _is_type_checking_guard(node: ast.stmt) -> bool:
    """Whether a statement is an ``if TYPE_CHECKING:`` guard.

    Matched on the unparsed test rather than on the imported symbol, so it
    holds for ``typing.TYPE_CHECKING`` and for a ``from typing import
    TYPE_CHECKING`` binding alike, and for the ``not TYPE_CHECKING`` spelling.
    """
    if not isinstance(node, ast.If):
        return False
    text = ast.unparse(node.test).replace(" ", "")
    return "TYPE_CHECKING" in text


#: Statements whose bodies run while the module is being imported. A nested
#: import inside one of these is still module scope -- ``if sys.version_info``
#: and ``try:`` are the two that show up in real code.
_IMPORT_TIME_CONTAINERS = (
    ast.If,
    ast.Try,
    ast.TryStar,
    ast.With,
    ast.AsyncWith,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Match,
)


def _import_time_statements(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Every statement that executes while the module body runs.

    Deliberately does not descend into ``def``, ``class`` or ``async def``
    bodies: those do not execute at import time, and an import inside one is
    exactly what the rule permits. It does descend into the containers above,
    because an import under ``if sys.version_info >= (3, 13):`` still runs on
    some interpreter, and a rule that can be bypassed with one ``if`` is
    decoration.
    """
    for node in body:
        yield node
        if isinstance(node, _IMPORT_TIME_CONTAINERS) and not (
            _is_type_checking_guard(node)
        ):
            nested = [
                child
                for field in ("body", "orelse", "finalbody", "handlers")
                for child in getattr(node, field, [])
            ]
            if isinstance(node, ast.Try | ast.TryStar):
                for handler in node.handlers:
                    nested.extend(handler.body)
            yield from _import_time_statements(nested)


def _module_scope_imports(tree: ast.Module) -> Iterator[tuple[int, str, str]]:
    """Imports that execute at module scope, as ``(line, name, source)``.

    The deliberate counterpart to ``_imported_roots``, which uses ``ast.walk``
    and therefore cannot distinguish module scope from function scope. This one
    walks only what runs at import time: the module body, and the containers
    inside it, but never a ``def`` or ``class`` body.

    ``if TYPE_CHECKING:`` blocks are skipped whole, wherever they appear. Their
    imports do not execute, which is what makes annotating with a
    lazily-imported package possible at all.
    """
    for node in _import_time_statements(tree.body):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, _root_name(alias.name), ast.unparse(node)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, _root_name(node.module), ast.unparse(node)


def module_scope_violations_in_source(
    source: str, rel_path: str, tier: str
) -> list[ModuleScopeViolation]:
    """Module-scope imports of a package that must be imported lazily.

    Separate from ``violations_in_source`` because this is a different question.
    The sibling check asks *which* package a module reaches for; this asks *when*
    it reaches for one specific package, so it walks what executes at import time
    instead of the whole tree.

    Which sources this is called over is decided by
    ``all_module_scope_violations``, not here.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Reported by the loader check in test_index_consistency.py, not here.
        return []

    found: dict[tuple[int, str], ModuleScopeViolation] = {}
    for line, name, raw in _module_scope_imports(tree):
        if name not in _MODULE_SCOPE_FORBIDDEN:
            continue
        found[(line, name)] = ModuleScopeViolation(
            rel_path=rel_path,
            line=line,
            source=raw,
            tier=tier,
            package=name,
        )
    return [found[key] for key in sorted(found)]


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _runtime_sources(artifact: Artifact) -> Iterator[Path]:
    """Every non-test Python module an artifact publishes."""
    for candidate in sorted(artifact.path.rglob("*.py")):
        relative = candidate.relative_to(artifact.path)
        parts = relative.parts
        if any(part in _SKIP_DIRS or part == "tests" for part in parts):
            continue
        yield candidate


def _artifact_tests(artifact: Artifact) -> Iterator[Path]:
    """Every module in an artifact's own suite."""
    if not artifact.has_tests():
        return
    for candidate in sorted(artifact.test_dir.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in candidate.parts):
            continue
        yield candidate


def _root_test_tier() -> Iterator[Path]:
    """The repository's own tests, plus the conftest every suite inherits.

    ``conftest.py`` is in this tier on purpose: a shared fixture that imports a
    sibling hands that import to all fifteen suites at once, and no artifact's
    own directory would show it.
    """
    yield from (REPO_ROOT / "tests").rglob("*.py")
    conftest = REPO_ROOT / _ROOT_CONFTEST
    if conftest.is_file():
        yield conftest


def all_violations() -> list[Violation]:
    """Scan the whole tree for sibling imports, tier by tier."""
    found: list[Violation] = []

    for artifact in artifacts():
        for source_path in _runtime_sources(artifact):
            found.extend(
                violations_in_source(
                    source_path.read_text(encoding="utf-8"),
                    _rel(source_path),
                    f"{artifact.kind}:{artifact.name}/runtime",
                    artifact.path,
                )
            )
        for source_path in _artifact_tests(artifact):
            found.extend(
                violations_in_source(
                    source_path.read_text(encoding="utf-8"),
                    _rel(source_path),
                    f"{artifact.kind}:{artifact.name}/tests",
                    artifact.path,
                )
            )

    for source_path in _root_test_tier():
        rel_path = _rel(source_path)
        if rel_path in _EXEMPT_REPO_TIER:
            continue
        found.extend(
            violations_in_source(
                source_path.read_text(encoding="utf-8"),
                rel_path,
                "repo:tests",
                None,
            )
        )

    found.sort(key=lambda v: (v.rel_path, v.line))
    return found


def all_module_scope_violations() -> list[ModuleScopeViolation]:
    """Scan the whole tree for module-scope imports of a lazy-only package.

    Scoped to **Rune runtime source**, and that is the whole point of the rule
    rather than a narrowing of it.

    - ``mvges/`` is excluded because a mvge *is* the agent. ``coding_mvge``
      importing ``mvgeos_agent`` at module scope is the correct wiring, not a
      near miss.
    - Test tiers are excluded because pytest is not the loader. The hazard is
      specifically a Rune module being imported *by* ``rune_runner`` while
      ``mvgeos_agent`` is half-built. A test module imported by pytest is not in
      that chain, and three Rune suites import ``mvgeos_agent`` at module scope
      today for legitimate reasons.

    So the exact set this must catch is "a published Rune module the loader
    executes", which is ``_runtime_sources`` over ``kind == "runes"``. Widening
    it to the other tiers would mean editing four Rune suites and one agent
    package to fix nothing.
    """
    found: list[ModuleScopeViolation] = []

    for artifact in artifacts():
        if artifact.kind != "runes":
            continue
        for source_path in _runtime_sources(artifact):
            found.extend(
                module_scope_violations_in_source(
                    source_path.read_text(encoding="utf-8"),
                    _rel(source_path),
                    f"{artifact.kind}:{artifact.name}/runtime",
                )
            )
        found.sort(key=lambda v: (v.rel_path, v.line))
    return found


def _own_pythonpath(artifact: Artifact) -> list[str]:
    """An artifact's declared ``[tool.pytest.ini_options] pythonpath``.

    Read from the artifact's own ``pyproject.toml`` rather than from a live
    pytest run, so this is a static fact about the config rather than an
    observation of one interpreter's ``sys.path``.
    """
    manifest_path = artifact.path / "pyproject.toml"
    if not manifest_path.is_file():
        return []
    data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    table = data.get("tool", {}).get("pytest", {}).get("ini_options", {})
    entries = table.get("pythonpath", [])
    return [str(entry) for entry in entries]


def sibling_pythonpath_entries(artifact: Artifact, entries: Iterable[str]) -> list[str]:
    """Declared ``pythonpath`` entries that point at a *sibling* import root.

    Split out from the gate so the teeth test can feed it planted entries
    without inventing an artifact on disk. Entries resolve against the
    artifact's own directory because that is pytest's rootdir for a
    standalone ``pytest runes/<rune>/tests`` run, which is the run whose
    isolation this checks.
    """
    import_roots = {a.import_root.resolve(): a for a in artifacts()}
    offenders: list[str] = []
    for entry in entries:
        resolved = (artifact.path / entry).resolve()
        owner = import_roots.get(resolved)
        if owner is None or owner.path == artifact.path:
            continue
        offenders.append(f"{owner.relative_path} as {entry!r}")
    return offenders


def _root_test_tier_relpaths() -> Iterator[str]:
    """Repo-relative paths of the repository's own test tier."""
    for source_path in _root_test_tier():
        yield _rel(source_path)


def _artifact_named(name: str) -> Artifact:
    """The indexed artifact called ``name``, for test fixtures."""
    for artifact in artifacts():
        if artifact.name == name:
            return artifact
    raise AssertionError(f"no indexed artifact named {name!r}")


def test_no_artifact_imports_a_sibling() -> None:
    """The gate. No Rune reaches into another Rune, in code or in its suite."""
    violations = all_violations()
    assert not violations, (
        "A Rune may import engine packages, never another Rune. These modules "
        "reach into a sibling artifact, so the artifact does not work when it "
        "is installed on its own into ~/.agents/extensions/:\n"
        + "\n".join(f"  {v.describe()}\n      {v.source}" for v in violations)
        + "\nFix it where the code lives:\n"
        "  - A shared behaviour belongs in an engine package, or in this "
        "Rune.\n"
        "  - A fact that genuinely needs two Runes belongs in "
        f"{CROSS_RUNE_INTEGRATION},\n"
        "    the only file permitted to import a sibling.\n"
        "Do NOT add the sibling to a Rune's pythonpath or sys.path to make a "
        "test pass. That makes the violation permanent and invisible."
    )


def test_no_module_imports_a_lazy_package_at_module_scope() -> None:
    """The second rule. A lazily-imported package is not imported at module scope.

    ``mvgeos_agent`` is lawful for a Rune to import, but not at module scope:
    its ``__init__`` loads the Rune loader, so a Rune imported during that chain
    sees a half-built package and silently fails to load.
    """
    violations = all_module_scope_violations()
    assert not violations, (
        "A Rune may import mvgeos_agent, but never at module scope. These "
        "modules import it while their own module body runs:\n"
        + "\n".join(f"  {v.describe()}\n      {v.source}" for v in violations)
        + "\nFix it where the code lives:\n"
        "  - Import it inside the function that uses it.\n"
        "  - If the name is only used in annotations, import it under\n"
        "    `if TYPE_CHECKING:`. That never executes at runtime.\n"
        "Do NOT re-export mvgeos_agent from a module that is imported at "
        "module scope: the same half-built package is still what the import "
        "sees."
    )


def test_the_module_scope_gate_would_catch_a_regression() -> None:
    """That gate has teeth in both directions.

    A gate that cannot fail is decoration, and one that fails on legal code is
    worse. Both directions are planted here, as source strings, so neither
    depends on the tree staying the way it is today.
    """
    # Illegal: module scope, plain import and `import` spelling both.
    for illegal in (
        "from mvgeos_agent import Mvge\n",
        "import mvgeos_agent\n",
        "from mvgeos_agent.auth import load_api_key_from_auth\n",
        "import os\nfrom mvgeos_agent import Mvge\nimport sys\n",
        # A guard that is not TYPE_CHECKING still runs at module scope, so the
        # gate descends into `if` bodies rather than reading the module body only.
        (
            "import sys\nif sys.version_info >= (3, 13):\n"
            "    from mvgeos_agent import Mvge\n"
        ),
        # Same for try/except: the import runs, and it may fail.
        (
            "try:\n    from mvgeos_agent import Mvge\n"
            "except ImportError:\n    Mvge = None\n"
        ),
    ):
        violations = module_scope_violations_in_source(
            illegal, "regression.py", "runtime"
        )
        assert violations, f"the module-scope gate missed {illegal!r}"

    # Legal: inside a function, under TYPE_CHECKING, or another package.
    for legal in (
        "def build():\n    from mvgeos_agent import Mvge\n    return Mvge\n",
        (
            "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n"
            "    from mvgeos_agent import Mvge\n"
        ),
        (
            "import typing\n\nif typing.TYPE_CHECKING:\n"
            "    from mvgeos_agent import Mvge\n"
        ),
        "from mvgeos_core import agent_dir\nfrom mvgeos_runes import SigilHook\n",
        # Nested one level deeper in a class body is not module scope either.
        (
            "class Sub:\n    def __init__(self) -> None:\n"
            "        from mvgeos_agent import Mvge\n"
        ),
    ):
        violations = module_scope_violations_in_source(legal, "allowed.py", "runtime")
        assert not violations, f"unexpected violation for {legal!r}: {violations}"


def test_the_gate_would_catch_a_regression() -> None:
    """The gate has teeth: all four shapes from issue #5, planted verbatim.

    A check that cannot fail is worse than no check. These are the four
    mechanisms the real violations used, each attributed to the Rune and file
    it was found in, so a future narrowing of the detector has to delete a
    named row rather than drift quietly.
    """
    cases = [
        # selfmod-bridge/tests/test_spells.py:21 and test_templates.py:18 --
        # the shape that failed with two collection errors standalone.
        (
            "selfmod-bridge",
            ("from mvgeos_runes_skills_bridge.parser import parse_skill_manifest\n"),
        ),
        # selfmod-bridge/tests/test_chain.py:24-36 -- import_module behind a
        # scoped sys.path insert of the sibling root, worked standalone by
        # construction. Planted in full because the sys.path insert is what
        # made it pass; the import is only the visible half.
        (
            "selfmod-bridge",
            (
                "_ROOT = Path(__file__).resolve().parent.parent.parent "
                "/ 'steering-bridge'\n"
                "def _load():\n"
                "    sys.path.insert(0, str(_ROOT))\n"
                "    try:\n"
                "        return importlib.import_module("
                '"mvgeos_runes_steering_bridge.rune")\n'
                "    finally:\n"
                "        sys.path.remove(str(_ROOT))\n"
            ),
        ),
        # session-search/tests/test_session_search.py:236 -- the shape that
        # turned a broken import into a silent skip.
        (
            "session-search",
            'codec = pytest.importorskip("mvgeos_runes_pi_codec.codec")\n',
        ),
        # A plain import of a sibling package.
        ("seeker", "import mvgeos_runes_skills_bridge.parser\n"),
        # The fully-qualified, alias-free runtime spelling.
        ("seeker", 'x = __import__("mvgeos_runes_adr_bridge")\n'),
        # Probing for a sibling to branch on its presence.
        ("seeker", 's = importlib.util.find_spec("mvgeos_runes_okf_bridge")\n'),
        # Cross-artifact too: an mvge is an artifact, so a Rune may not import
        # an Mvge's package either.
        ("seeker", "from coding_mvge.spells import plan\n"),
    ]
    for artifact_name, source in cases:
        owner = _artifact_named(artifact_name)
        violations = violations_in_source(source, "planted.py", "planted", owner.path)
        assert len(violations) == 1, (
            f"expected exactly one violation for {source!r} owned by "
            f"{artifact_name!r}, got {violations}"
        )
        assert violations[0].sibling != artifact_name, (
            "a Rune must not be reported as its own sibling"
        )


def test_the_gate_allows_engine_packages_and_its_own_package() -> None:
    """What a Rune is allowed to import stays importable."""
    owner = _artifact_named("session-search")
    allowed = [
        "from mvgeos_core import sessions_dir\n",
        "from mvgeos_runes import SigilHook\n",
        "from mvgeos_runes.rune_api import RuneAPI\n",
        # Allowed by this gate: a submodule import is not a sibling reach-across.
        # Note this holds at *any* scope. It is not in tension with the
        # module-scope rule for the ``mvgeos_agent`` package root: importing
        # ``mvgeos_agent.auth`` still executes ``mvgeos_agent/__init__``, so the
        # half-built-package hazard applies to it identically and the separate
        # ``_MODULE_SCOPE_FORBIDDEN`` check holds it to a function body. Both
        # answers were settled on upstream issue SOM-24; keep them together.
        "from mvgeos_agent.auth import load_api_key_from_auth\n",
        # Its own package, at any depth.
        "from mvgeos_runes_session_search.db import get_db_connection\n",
        "from mvgeos_runes_session_search import indexer\n",
        # Relative imports never leave the artifact.
        "from .helpers import fixture\n",
        "from ..sibling import fixture\n",
        # Third-party and standard library.
        "import pytest\nimport aiohttp\nimport sqlite3\n",
        # A dynamic import whose name is computed: a stated limit, not a pass.
        'mod = importlib.import_module(f"mvgeos_runes_{name}")\n',
        # A non-constant first argument, same limit.
        "mod = importlib.import_module(name)\n",
    ]
    for source in allowed:
        violations = violations_in_source(source, "allowed.py", "allowed", owner.path)
        assert not violations, f"unexpected violation for {source!r}: {violations}"


def test_only_the_documented_exception_may_import_a_sibling() -> None:
    """Exactly one repo-tier file reaches across, and it is the named one.

    This is the guard on the whitelist, and it is derived from the tree rather
    than from the scan's own skip logic. That matters: if the exemption were
    only honoured inside ``all_violations()``, widening it there would widen
    the gate *and* the test that is supposed to catch the widening, in one
    edit, silently. Here the set of files that actually reach across is
    computed independently and then compared, so both directions fail:

    - a new file that reaches across is caught, because the comparison is
      against a one-element constant rather than against the skip logic;
    - a widening that exempts a file which reaches across nothing is also
      caught, as dead exemption.
    """
    reaching = {
        rel
        for rel in _root_test_tier_relpaths()
        if violations_in_source(
            (REPO_ROOT / rel).read_text(encoding="utf-8"),
            rel,
            "repo:tests",
            None,
        )
    }
    assert reaching <= _EXEMPT_REPO_TIER, (
        "these repository-tier files import a sibling artifact and are not "
        f"the documented exception: {sorted(reaching - _EXEMPT_REPO_TIER)}\n"
        f"Either the import is wrong, or {CROSS_RUNE_INTEGRATION} is where it "
        "belongs and it should be moved there."
    )
    assert reaching == _EXEMPT_REPO_TIER, (
        f"{CROSS_RUNE_INTEGRATION} no longer imports a sibling, so the "
        "exemption protects nothing. Move its tests back into the Runes that "
        "own them and shrink _EXEMPT_REPO_TIER -- or, if it is being renamed, "
        "update CROSS_RUNE_INTEGRATION and this assertion together. A dead "
        "exemption is one more place a sibling import can hide."
    )


def test_the_documented_exception_exists() -> None:
    """The named exception is a real file, not a stale string constant."""
    assert (REPO_ROOT / CROSS_RUNE_INTEGRATION).is_file(), (
        f"{CROSS_RUNE_INTEGRATION} is whitelisted but does not exist, so the "
        "exception is dead config and should be dropped"
    )


def test_artifact_package_names_are_uniquely_owned() -> None:
    """Each importable package name belongs to exactly one artifact.

    Without this, "is this a sibling?" is undecidable for the colliding name
    and the gate would silently pick one owner.
    """
    owners = _package_owners()
    assert len(owners) == sum(
        len(artifact.top_level_names) for artifact in artifacts()
    ), "an artifact package name is owned by more than one artifact"
    assert owners, "no artifact publishes an importable package"


def test_the_gate_scans_every_artifact() -> None:
    """The scan is index-driven, so publishing a Rune extends the gate.

    Guards the failure mode that turns a gate into decoration: covering
    nothing, or covering only part of the tree.
    """
    runtime = {
        _rel(source)
        for artifact in artifacts()
        for source in _runtime_sources(artifact)
    }
    tests = {
        _rel(source) for artifact in artifacts() for source in _artifact_tests(artifact)
    }
    root_tier = set(_root_test_tier_relpaths())

    assert runtime, "no artifact runtime modules were scanned; the gate is vacuous"
    # The declared entry point is the module the engine actually loads, for
    # either kind of artifact (a Rune's rune.py, an Mvge's mvge.py), so assert
    # on that rather than on the filename: it is the module a sibling import
    # would most usefully hide in.
    missing = [
        _rel(artifact.entry_point_path())
        for artifact in artifacts()
        if _rel(artifact.entry_point_path()) not in runtime
    ]
    assert not missing, (
        "every artifact's declared entry point must be scanned, so a sibling "
        "import cannot hide in the module the engine actually loads. Not "
        f"scanned: {sorted(missing)}"
    )
    assert tests, "no artifact test suites were scanned"
    assert "runes/selfmod-bridge/tests/test_spells.py" in tests, (
        "the selfmod-bridge suite is not scanned, but it carried two of the "
        "four violations in issue #5"
    )
    assert root_tier, "the repository test tier is not being scanned"
    assert _ROOT_CONFTEST in root_tier, (
        "the root conftest.py is not scanned, so a sibling import in a shared "
        "fixture would reach every suite at once"
    )


def test_no_artifact_puts_a_sibling_on_its_own_pythonpath() -> None:
    """A Rune's declared ``pythonpath`` is its own import root, or it is a bug.

    This is the edit the gate exists to make impossible to get away with. The
    four issue #5 violations were removed from the suites; the next way to
    reintroduce one is to put the sibling on ``pythonpath`` and write the
    import that "needs" it. That makes the Rune depend on a directory it will
    not have when installed alone, and it does so silently -- which is exactly
    how ``session-search``'s coverage disappeared without a red build.

    ``index.json``'s root ``pythonpath`` legitimately lists every artifact;
    that is the integration runner, and ``test_index_consistency.py`` already
    checks it covers the whole index. This asserts the opposite for the
    per-artifact tables, which must stay single-rooted.
    """
    offenders: list[str] = []
    for artifact in artifacts():
        for offending in sibling_pythonpath_entries(
            artifact, _own_pythonpath(artifact)
        ):
            offenders.append(
                f"{artifact.relative_path}/pyproject.toml puts {offending}"
            )
    assert not offenders, (
        "A Rune's standalone suite must resolve only its own package. These "
        "declare a sibling's root, which silently couples the Rune to a "
        "directory it will not have when installed on its own:\n"
        + "\n".join(f"  {line}" for line in offenders)
        + "\nRemove the entry. If a test needs a sibling, it does not belong "
        f"in that Rune's suite -- see {CROSS_RUNE_INTEGRATION}."
    )


def test_the_pythonpath_check_would_catch_a_regression() -> None:
    """The pythonpath check has teeth, planted, and it spares the root table.

    An earlier draft of this test asserted something that could never fail,
    which is the exact failure mode a gate exists to prevent, so the detector
    is now a function and the planted cases below are its real teeth.
    """
    session_search = _artifact_named("session-search")

    # The two entries a single-rooted table actually uses -- own dir for a
    # Rune, parent for an Mvge whose directory *is* the package.
    assert sibling_pythonpath_entries(session_search, ["."]) == []
    coding_mvge = _artifact_named("coding_mvge")
    assert sibling_pythonpath_entries(coding_mvge, [".."]) == []

    # Sibling by relative path, from both directions and both depths. Entries
    # resolve against rootdir, which for a standalone artifact run is the
    # artifact's own directory -- that is what makes "." correct and what
    # makes a repo-root-relative spelling like "runes/pi-codec" wrong here.
    for planted in (
        [".", "../pi-codec"],
        ["../skills-bridge"],
        [".", "../../runes/steering-bridge"],
        ["../seeker"],
    ):
        found = sibling_pythonpath_entries(session_search, planted)
        assert len(found) == 1, (
            f"expected exactly one offending entry for {planted!r}, got {found}"
        )

    # A Rune may not import an Mvge either, and the mvge tier is not a
    # loophole. coding_mvge's import root is mvges/, since its own directory
    # is the package -- which is why its own table says "..", not ".".
    assert len(sibling_pythonpath_entries(session_search, ["../../mvges"])) == 1

    # The root table is the integration runner and legitimately lists every
    # artifact, so this check must never be pointed at it.
    packaged = [a for a in artifacts() if a.top_level_names]
    root_entries = pytest_config().get("pythonpath", [])
    assert isinstance(root_entries, list), (
        "the root pyproject.toml pythonpath is not a list, so the per-artifact "
        f"pythonpath check cannot tell it from a sibling: {root_entries!r}"
    )
    assert len(root_entries) >= len(packaged), (
        "the root pythonpath no longer covers every packaged artifact; this "
        "check assumes the root table is the integration runner and the "
        "per-artifact tables are the standalone ones"
    )
