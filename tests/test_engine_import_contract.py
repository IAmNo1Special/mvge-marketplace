"""Every import this repository makes of the engine is published and declared.

The engine publishes one rule for what a Rune may import:
``mvge.runes.contract``. The engine's own CI runs it against a checkout of
this repository, which is how an engine change that breaks a published artifact
fails on the engine's build rather than three pushes later.

This gate is the other half, and it is not redundant. The engine's check runs
against whatever this repository's ``main`` happens to be when the engine
builds, so it can only see a merge that already happened. A branch that adds
an import of an engine internal resolves nothing on the engine's roster, so
the engine's gate is blind to it -- and this repository's own suite is the
first place that branch is ever resolved.

It is the same rule, not a second one. ``check_source`` is called, not a
reimplementation, so a Rune that imports an engine module the engine has not
published fails here with the engine's own message, and the two gates cannot
drift the way a copied check would.

The kinds worth reading
-----------------------
``unpublished``
    A module the engine did not declare. This is the kind that was previously
    invisible: the engine's checker only inspected package roots, so a Rune
    subclassing ``mvge.provider.sse.SSEStreamingRealm`` passed it cleanly.

``undeclared``
    The symbol resolves but is absent from its package's ``__all__``. It works
    today and can be removed with no signal.
"""

from __future__ import annotations

from pathlib import Path

from mvge.runes.contract import Violation, check_tree

REPO_ROOT = Path(__file__).resolve().parents[1]
_SCAN_DIRS = ("runes", "mvges")


def _violations() -> list[Violation]:
    return [
        violation
        for directory in _SCAN_DIRS
        for violation in check_tree(REPO_ROOT / directory, relative_to=REPO_ROOT)
    ]


def test_no_artifact_imports_an_unpublished_engine_module() -> None:
    violations = _violations()
    assert violations == [], (
        "this repository imports engine API the engine has not published:\n"
        + "\n".join(f"  {v}" for v in violations)
        + "\nEach one either names a module mvge.runes.contract does not "
        "declare, or a symbol its package's __all__ does not expose."
    )


def test_the_scan_reaches_this_repository() -> None:
    """The gate is not vacuous: it saw real source.

    Guards the failure mode that turns a gate into decoration -- a root
    calculation that resolves to nothing, checked against zero files, and
    reports success forever.
    """
    scanned = [
        path
        for directory in _SCAN_DIRS
        for path in sorted((REPO_ROOT / directory).rglob("*.py"))
    ]
    assert scanned, f"no source found under {REPO_ROOT}"
    assert any(path.name == "rune.py" for path in scanned), (
        "no rune.py was scanned, so the module the engine actually loads is "
        "outside this gate"
    )
