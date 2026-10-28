"""Pin first-party packages to this repository, not to whatever is installed.

The engine puts ``~/.agents/agents`` at the front of ``sys.path`` the moment an
Mvge is constructed (``mvge.agent.mvge``), and ``coding_mvge`` builds one at
module scope. The consequence for this suite was severe and entirely silent:
``mvges/coding_mvge/tests`` imported ``coding_mvge``, which resolved to the
copy installed on the developer's machine rather than the tree beside it, so
those tests asserted against a stale snapshot and passed while the source was
broken. Adding ``mvges/coding_mvge`` to the ``pythonpath`` ini is not enough on
its own -- the insert lands at position 0 at runtime and wins.

So this pins resolution explicitly, before collection:

- Put this repository's own package roots at the front of ``sys.path``.
- Evict any already-imported first-party module that resolved outside the
  repository, so the next import re-resolves from the source tree.
- Assert the result, so any future regression fails loudly rather than
  quietly testing the wrong code.

The set of package roots and the set of names to pin are both derived from
``index.json`` via ``marketplace_index``, so publishing a new artifact cannot
leave this file silently behind.

The assertion is the point. A silent wrong-tree import is worse than no test.
"""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from marketplace_index import (
    artifacts,
)

#: Top-level names that must resolve inside this repository.
#:
#: Derived from ``index.json`` rather than hardcoded: every artifact declares
#: its own package, so the set of names we need to pin is exactly the set of
#: packages the marketplace publishes. Adding an artifact to the index is
#: therefore enough to get it pinned -- there is no second list here to forget
#: to update.
_PINNED = tuple(
    sorted({name for artifact in artifacts() for name in artifact.top_level_names})
)

#: Artifacts whose import root this file puts on ``sys.path``.
#:
#: Mvges only, and that restriction is load-bearing. The engine inserts
#: ``agents_dir()`` at position 0 when it constructs an Mvge, so an mvge
#: package can genuinely lose the resolution race and needs managing here.
#:
#: Runes are deliberately left to the ``pythonpath`` ini option. Every rune
#: directory holds a top-level ``rune.py``, so putting all of them on
#: ``sys.path`` in one place makes a bare ``from rune import ...`` -- which
#: ``runes/openrouter-realm/tests`` does -- resolve to whichever artifact
#: sorted first rather than the one under test. ``pyproject.toml`` orders those
#: entries per artifact, which is what those tests need.
_MANAGED = tuple(a for a in artifacts() if a.kind == "mvges")


def _outside_repo(module: object) -> bool:
    """True when an imported module's file lives outside this repository."""
    file = getattr(module, "__file__", None)
    if file is None:
        return False
    try:
        Path(file).resolve().relative_to(_REPO_ROOT)
    except ValueError:
        return True
    return False


def _shadowed_modules() -> list[tuple[str, str]]:
    """First-party modules in ``sys.modules`` that resolved outside the repo."""
    shadowed: list[tuple[str, str]] = []
    for name, module in list(sys.modules.items()):
        top = name.split(".", 1)[0]
        if top in _PINNED and _outside_repo(module):
            shadowed.append((name, str(getattr(module, "__file__", "?"))))
    return sorted(shadowed)


def _assert_no_shadowing(stage: str) -> None:
    shadowed = _shadowed_modules()
    if not shadowed:
        return
    detail = "\n".join(f"  {name} -> {file}" for name, file in shadowed)
    raise AssertionError(
        f"First-party modules resolved outside {_REPO_ROOT} at {stage}. Tests "
        f"would run against an installed copy instead of this checkout:\n{detail}"
    )


def _strip_bracketed_no_proxy_entries(value: str) -> str:
    """Drop bracketed IPv6 literals from a no-proxy list.

    httpx parses ``NO_PROXY``/``no_proxy`` when constructing a client and
    raises ``InvalidURL`` on bracketed entries such as ``[::1]`` (it reads
    the ``:1]`` as a port). Sandboxes commonly export exactly that form,
    so any test that builds an httpx client fails for environmental
    reasons. Bracketed entries are the only tokens removed; plain hosts,
    CIDRs, and unbracketed IPv6 literals pass through untouched.
    """
    kept = [
        token.strip()
        for token in value.split(",")
        if token.strip() and "[" not in token and "]" not in token
    ]
    return ",".join(kept)


@pytest.fixture(autouse=True)
def _isolate_global_agents_dir(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[None]:
    """Point the global ``.agents`` layer at an empty directory.

    Runes that take no explicit ``global_dir`` resolve the user and agent
    layers through ``global_agents_dir()``, which defaults to the
    developer's real ``~/.agents``. A skills-bridge rune started in a test
    therefore loaded whatever skills the developer happened to have
    installed (``typesafe-ai`` on this machine), so test outcomes depended
    on the developer's home directory. The suite was green or red
    depending on what was installed, which is the worst property for a
    test to have.

    A test that wants the real global layer, or wants to assert the
    default resolution, sets or deletes ``MVGE_GLOBAL_DIR`` itself; this
    fixture only supplies a harmless default and restores whatever was
    there before. The resolvers read the variable on every call rather
    than caching it at import, which is what makes this work without
    reimporting anything.

    This fixture only applies to runs from the repository root. A suite
    run from inside a rune directory never loads this file (each rune is
    an independently installable, independently testable package), so
    tests that must be hermetic on their own still isolate explicitly.
    """
    previous = os.environ.get("MVGE_GLOBAL_DIR")
    os.environ["MVGE_GLOBAL_DIR"] = str(tmp_path_factory.mktemp("global-agents"))
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("MVGE_GLOBAL_DIR", None)
        else:
            os.environ["MVGE_GLOBAL_DIR"] = previous


@pytest.fixture(autouse=True)
def _sanitize_no_proxy_env() -> Iterator[None]:
    """Keep a bracketed ``NO_PROXY`` from breaking httpx clients in tests.

    Same family as the global-dir fixture: the value a developer's shell
    exports must not decide whether the suite passes. Tests that assert
    proxy behaviour itself set the variables explicitly and are
    unaffected, because this only strips the bracketed tokens.
    """
    previous = {name: os.environ.get(name) for name in ("NO_PROXY", "no_proxy")}
    for name, value in previous.items():
        if value is not None:
            os.environ[name] = _strip_bracketed_no_proxy_entries(value)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def pytest_configure(config: pytest.Config) -> None:
    roots = sorted({a.import_root for a in _MANAGED}, key=lambda p: p.as_posix())
    for root in roots:
        text = str(root)
        while text in sys.path:
            sys.path.remove(text)
        sys.path.insert(0, text)

    # Import the managed packages *now*, before collection. This is not just an
    # assertion: putting the module in sys.modules fixes its identity, and the
    # engine mutates sys.path out from under us -- constructing an Mvge puts
    # ~/.agents/agents at position 0, ahead of anything we inserted here. A
    # package first imported during collection would resolve to the installed
    # copy and the whole suite would quietly test stale code.
    #
    # An ImportError is tolerated: a rune whose declared third-party deps are
    # not installed in this environment is a legitimate state that the
    # dependency diagnostic already reports. Only successfully imported
    # packages are asserted on.
    for artifact in _MANAGED:
        for name in sorted(artifact.top_level_names):
            try:
                importlib.import_module(name)
            except ImportError:
                continue

    # Evict anything already imported from outside the repository, including
    # its submodules, so the next import re-resolves against sys.path.
    for name in list(sys.modules):
        top = name.split(".", 1)[0]
        if top in _PINNED and _outside_repo(sys.modules[name]):
            del sys.modules[name]

    _assert_no_shadowing("configure")
