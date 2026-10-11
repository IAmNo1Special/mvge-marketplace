"""The one claim the docs make about versions must be true.

The Quickstart used to name a current version -- "The published distribution
on PyPI. `v0.6.27`, installed as `mvgeos-cli v0.6.27`" -- and a test asserted
that the number equalled the engine's. It could not hold. The number named the
engine's newest *source* commit while the install line installs the newest
*published* wheel, and those two agree only in the moment a release is cut. It
was wrong for six consecutive releases, 0.6.23 through 0.6.28, and the reason
it went that long is that nothing about "someone updates a number in another
repository on every engine merge" survives contact with a release cadence.

So the number is gone, and what replaces it is the claim that was always the
point: which release introduced a command. A reader's real question is not
"what version is this page about" but "why is the command I read here missing
when I run it" -- and the answer, `mvge install --confirm-python-deps` landed
in `v0.6.7`, is history. History does not rot.

The contract now has three parts, all offline:

1. No page states the engine's current version. The old version of this check
   allowed exactly one page to; there is no longer a page permitted to.
2. Every "landed in `vX.Y.Z`" is <= the engine's declared version. A floor
   newer than the engine is an instruction to install a release that does not
   exist.
3. At least one floor exists, so the suite cannot pass by checking nothing.

The engine's declared version is read from the clone CI already makes, not from
tags: the tag series is not semver monotonic (``v3.0.0`` is a September tag
from a different numbering era, so "highest tag" is ``v3.0.0`` and means
nothing), and the declared version is what the package actually calls itself.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / "docs"
# The engine checkout, in both of the places it can be. CI runs its pre-commands
# with the working directory at this repository's root, so `git clone ... mvgeos`
# lands at ./mvgeos -- INSIDE the repo. A developer workspace holds the two
# repositories side by side instead, one level up.
#
# Both are checked because a guard that silently finds neither disables itself,
# and the symptom is a green build rather than a red one. This file looked only at
# the sibling path, which is correct on a developer machine and never true in CI.
# The first time it ran in CI it skipped all 6 of its tests and the job was green,
# so the guard reported nothing about drift while appearing to pass. Only the
# arithmetic showed it: 1209 passed / 3 skipped locally against 1204 / 8 in CI, a
# difference of exactly the 5 tests this file adds.
ENGINE_PYPROJECT_CANDIDATES = (
    REPO_ROOT / "mvgeos" / "pyproject.toml",
    REPO_ROOT.parent / "mvgeos" / "pyproject.toml",
)

ANY_VERSION = re.compile(r"v\d+\.\d+\.\d+")
#: A "landed in `vX.Y.Z`" claim. The version is the only group, and it is
#: restricted to a bare version inside backticks so a path or a sentence
#: mentioning a release is not misread as a floor.
LANDED_IN = re.compile(r"landed in `(v\d+\.\d+\.\d+)`")
#: The page that owns the version claims. It is no longer "the one page allowed
#: to name a version" -- no page is -- it is the page carrying the floors.
OWNS_THE_VERSION = "quickstart.md"
#: The section a reader is pointed to when a command is missing. Renamed from
#: "which-version-this-page-describes" when the version claim was removed.
VERSION_SECTION_ANCHOR = "checking-your-version"


def _engine_pyproject() -> Path | None:
    for candidate in ENGINE_PYPROJECT_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def _declared_version() -> str | None:
    engine = _engine_pyproject()
    if engine is None:
        return None
    with engine.open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


DECLARED = _declared_version()

needs_engine = pytest.mark.skipif(
    DECLARED is None,
    reason=(
        "no engine checkout at "
        f"{' or '.join(str(p) for p in ENGINE_PYPROJECT_CANDIDATES)}; "
        "cannot check the version claim"
    ),
)


@pytest.mark.parametrize("page", sorted(p.name for p in DOCS.glob("*.md")))
def test_no_page_names_the_current_version(page: str) -> None:
    """No page may state the version the reader installed -- not even one.

    This used to permit exactly one page, the Quickstart, to own the number.
    That allowance is what let the number rot for six releases: there was one
    place to update, and updating it was a manual step in another repository on
    every engine merge. There is now nowhere it is allowed, so a regression
    fails for whichever page re-introduces it.

    A page may still name an *older* release when it says which release
    introduced something -- that is history and does not rot.
    """
    if DECLARED is None:
        pytest.skip("there is no engine checkout, so there is nothing to compare")
    versions = {v for v in ANY_VERSION.findall((DOCS / page).read_text())}
    current = f"v{DECLARED}"
    assert current not in versions, (
        f"{page} states {current}, the current version. No page names a "
        "version now; say how to check (`uv tool list`) and which release "
        "introduced the command instead, so the claim cannot go stale"
    )


@needs_engine
def test_every_feature_floor_is_a_release_that_has_actually_shipped() -> None:
    """The claims that are left must be true, and they are checkable.

    This replaces a test that asserted the page's stated version equalled the
    engine's declared one. That invariant could not hold: the page named the
    engine's newest source commit while the install line installs the newest
    *published* wheel, and those two numbers agree only in the moment a release
    is cut. It was wrong for six consecutive releases -- 0.6.23 through 0.6.28
    -- because "someone remembers to update it" is not a plan at the engine's
    release cadence.

    What replaces it is the content that was always the point. Every "landed in
    vX.Y.Z" is a promise that a reader who has that release has the command; a
    floor newer than the engine is a lie, and a floor removed silently is a
    reader told to upgrade to something that does not exist. Unlike the number
    it replaces, this can be true indefinitely, so it is worth asserting.

    The page no longer states a current version at all, which is what makes this
    the whole contract rather than one half of it.
    """
    text = (DOCS / OWNS_THE_VERSION).read_text()
    floors = [found for found in LANDED_IN.findall(text) if "/" not in found]
    assert floors, (
        f"{OWNS_THE_VERSION} states no 'landed in' floor, so this file "
        "checks nothing. Either the Quickstart lost the only claim worth "
        "making, or the claim moved to a wording this test does not read."
    )
    for floor in floors:
        # The `v` is part of the claim's wording, not of the version, so the
        # comparison is on the numbers alone.
        floor_numbers = tuple(int(part) for part in floor.lstrip("v").split("."))
        declared_numbers = tuple(int(part) for part in DECLARED.split("."))
        assert floor_numbers <= declared_numbers, (
            f"{OWNS_THE_VERSION} says a command landed in {floor}, but the "
            f"engine declares {DECLARED}. A reader told to upgrade to {floor} "
            "would be told to install a release that does not exist."
        )


@needs_engine
def test_the_quickstart_states_no_current_version() -> None:
    """The number is gone on purpose; a regression re-adds a rotting claim.

    The section that used to own it now says it names no version and why. If a
    `v<engine version>` reappears, the drift this file exists to prevent is back
    -- and the failure is caught here rather than six releases later by a
    reader.
    """
    text = (DOCS / OWNS_THE_VERSION).read_text()
    assert f"v{DECLARED}" not in text, (
        f"{OWNS_THE_VERSION} states v{DECLARED}, the current version. This "
        "page does not name a version; say how to check (`uv tool list`) and "
        "which release introduced the command instead."
    )
    assert "landed in `v" in text, (
        f"{OWNS_THE_VERSION} lost its feature floors, so a reader cannot "
        "tell whether a missing command means their version is old or the docs "
        "are wrong."
    )
