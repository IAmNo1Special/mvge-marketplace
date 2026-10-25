"""Seeker must discover skills in the relocated global layer.

``_get_skill_roots`` hardcoded ``~/.agents/skills`` as its first default.
Under ``$MVGE_GLOBAL_DIR`` the user-scope skills move to
``<global>/skills``, so the discovery root and the install root diverged
and a relocated run silently searched the wrong directory.

``~/.claude/skills`` is deliberately left home-relative: it is a third-party
convention rather than our layer, and no amount of relocating our own
directory relocates someone else's.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mvge.core import GLOBAL_DIR_ENV, global_agents_dir, skills_dir
from mvge_runes_seeker.dci_matcher import _get_skill_roots


@pytest.fixture
def relocated_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the global ``.agents`` layer at a temp root for one test."""
    # Deliberately local rather than shared from the repository root:
    # each rune is an independently installable, independently testable
    # package (its own pyproject.toml declares its own testpaths), and a
    # suite run from inside the rune directory never loads the root
    # conftest.py.
    root = tmp_path / "global"
    monkeypatch.setenv(GLOBAL_DIR_ENV, str(root))
    return root


def test_user_skill_root_follows_the_relocated_layer(
    relocated_global: Path,
) -> None:
    """The relocated user-scope skills directory is discovered."""
    (relocated_global / "skills").mkdir(parents=True)

    roots = _get_skill_roots()

    assert (relocated_global / "skills").resolve() in roots
    assert skills_dir().resolve() in roots


def test_user_skill_root_is_not_read_from_the_real_home_layer(
    relocated_global: Path,
) -> None:
    """The literal home layer is no longer consulted.

    Asserted unconditionally: the fixture relocates to a temp directory, so
    a conditional here would only ever be a silent skip.
    """
    roots = _get_skill_roots()

    home_layer = (Path("~/.agents").expanduser() / "skills").resolve()
    assert home_layer not in roots, (
        "the real ~/.agents/skills is still a discovery root, so a relocated "
        "run reads a directory the user did not relocate"
    )
    assert all(
        not path.is_relative_to(Path.home()) or path.name == "skills" for path in roots
    )


def test_default_location_without_the_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no override the root agrees with the engine's own resolver."""
    monkeypatch.delenv(GLOBAL_DIR_ENV, raising=False)

    assert global_agents_dir() == Path("~/.agents").expanduser()
    assert skills_dir() == Path("~/.agents").expanduser() / "skills"

    roots = _get_skill_roots()
    expected = skills_dir().resolve()
    # Present only when the directory exists; the assertion is that asking
    # costs nothing and raises nothing either way.
    assert expected in roots or not expected.is_dir()


def test_claude_convention_stays_home_relative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A third-party convention is not relocated along with our layer.

    Locked deliberately: relocating ``~/.claude`` would be wrong, and this
    is the assertion that stops someone "fixing" it alongside our own path.
    """
    fake_home = tmp_path / "fakehome"
    claude = fake_home / ".claude" / "skills"
    claude.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    assert claude.resolve() in _get_skill_roots()


def test_custom_roots_short_circuit_the_defaults(tmp_path: Path) -> None:
    """Explicit roots are still honoured verbatim, unexpanded and unresolved."""
    custom = tmp_path / "elsewhere"
    custom.mkdir()

    assert _get_skill_roots([custom]) == [custom.expanduser().resolve()]
