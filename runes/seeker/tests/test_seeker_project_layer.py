"""The project skills root is anchored to a named project, never the cwd.

``paths.PROJECT_SKILLS_DIR`` used to be ``Path(".agents") / "skills"`` -- a
*relative* module constant. ``_skill_root_candidates`` resolved it and
``_get_skill_roots`` tested it with ``is_dir()``, so both bound it to the
process working directory at the moment of the call. Launched from an
unrelated checkout, Seeker searched that checkout's Skills; a Skill is
instructions the Mvge will follow, so that does not return the wrong
answer, it changes what the agent does.

It failed quietly in the other direction too. ``paths.missing()`` filters
the same list, and its docstring says why that matters: a search that
returns nothing because its root does not exist is indistinguishable from
one that found nothing, which is how a dead layer stays invisible for a
release. A root that was never asked for reintroduces exactly that.

ADR 0017 makes ``.agents/skills`` a supported project layer, owned by
``mvge.core.project_skills_dir``, reached only when a caller names a
project. These tests hold that line.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mvge.core import GLOBAL_DIR_ENV, project_skills_dir
from mvge_runes_seeker.dci_matcher import _get_skill_roots, _skill_root_candidates
from mvge_runes_seeker.paths import missing, skill_roots


@pytest.fixture
def decoy_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A working directory that *has* a ``.agents/skills``, and is not ours.

    The decoy is what makes these tests bite. Asserting only that a named
    project resolves correctly would pass against the old code too, since
    the old code happened to agree when the cwd and the project were the
    same directory.
    """
    decoy = tmp_path / "decoy"
    (decoy / ".agents" / "skills").mkdir(parents=True)
    monkeypatch.chdir(decoy)
    return decoy


def test_no_project_named_means_no_project_root(decoy_cwd: Path) -> None:
    """The project layer is omitted, not defaulted to the working directory."""
    roots = skill_roots()

    assert decoy_cwd / ".agents" / "skills" not in roots
    assert all(root.is_absolute() for root in roots)


def test_no_project_named_does_not_search_the_decoy(decoy_cwd: Path) -> None:
    """The decoy exists, is a real skills directory, and is still not searched.

    This is the assertion that fails against a cwd-relative constant: the
    old code resolved the relative path against this directory, found the
    directory present, and handed it back as a discovery root.
    """
    assert (decoy_cwd / ".agents" / "skills").is_dir()
    assert decoy_cwd / ".agents" / "skills" not in _get_skill_roots()
    assert decoy_cwd / ".agents" / "skills" not in _skill_root_candidates()


def test_named_project_is_searched_from_an_unrelated_cwd(decoy_cwd: Path) -> None:
    """A named project resolves under the project, not under the cwd."""
    project = decoy_cwd.parent / "named-project"
    (project / ".agents" / "skills").mkdir(parents=True)

    roots = _get_skill_roots(project_dir=project)

    assert project_skills_dir(project).resolve() in roots
    assert decoy_cwd / ".agents" / "skills" not in roots


def test_named_project_that_does_not_exist_is_reported_as_missing(
    tmp_path: Path,
) -> None:
    """``missing()`` names the project root it could not search.

    The point of ``missing()`` is that a caller can tell "no Skills here"
    from "nowhere to look". That is only true if the root asked for is the
    root named.
    """
    project = tmp_path / "absent-project"

    absent = missing(skill_roots(project))

    assert project_skills_dir(project).resolve() in absent


def test_unnamed_project_is_not_reported_as_missing(
    decoy_cwd: Path,
) -> None:
    """A root that was never asked for must not appear in ``missing()``.

    Without this, ``missing()`` regresses to its original failure: it
    reports a root nobody asked about as though the layer had gone dead,
    and a caller reading that diagnoses the wrong problem.
    """
    absent = missing(skill_roots())

    assert decoy_cwd / ".agents" / "skills" not in absent


def test_relocated_global_layer_and_unrelated_cwd_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both halves of the relocation hazard, in one run.

    ``$MVGE_GLOBAL_DIR`` moves our own layer; the cwd moves nothing,
    because nothing is anchored to it. Getting both right is the property
    that matters -- either alone passes against half a fix.
    """
    global_root = tmp_path / "global"
    monkeypatch.setenv(GLOBAL_DIR_ENV, str(global_root))
    (global_root / "skills").mkdir(parents=True)

    decoy = tmp_path / "decoy"
    (decoy / ".agents" / "skills").mkdir(parents=True)
    monkeypatch.chdir(decoy)

    project = tmp_path / "project"
    (project / ".agents" / "skills").mkdir(parents=True)

    roots = _get_skill_roots(project_dir=project)

    assert (global_root / "skills").resolve() in roots
    assert project_skills_dir(project).resolve() in roots
    assert decoy / ".agents" / "skills" not in roots


def test_str_project_dir_is_accepted(tmp_path: Path) -> None:
    """A string project directory anchors the same as a Path one."""
    project = tmp_path / "project"
    (project / ".agents" / "skills").mkdir(parents=True)

    assert project_skills_dir(str(project)).resolve() in _get_skill_roots(
        project_dir=str(project)
    )


def test_explicit_roots_still_short_circuit(tmp_path: Path) -> None:
    """Explicit ``skill_dirs`` wins outright, as it did before.

    A caller that names its own roots has named the whole set; the project
    layer is not appended behind them.
    """
    custom = tmp_path / "elsewhere"
    custom.mkdir()
    project = tmp_path / "project"
    (project / ".agents" / "skills").mkdir(parents=True)

    assert _get_skill_roots([custom], project_dir=project) == [
        custom.expanduser().resolve()
    ]


def test_candidates_are_absolute_and_filtered_separately(
    decoy_cwd: Path,
) -> None:
    """``_skill_root_candidates`` unfiltered, ``_get_skill_roots`` filtered.

    The split is deliberate in the original code -- a caller reporting
    which root it could not search needs the ones that were dropped -- so
    the project layer has to survive the unfiltered list too, or
    ``SkillSearchSpell`` cannot name it.
    """
    project = decoy_cwd.parent / "project"
    (project / ".agents" / "skills").mkdir(parents=True)

    unfiltered = _skill_root_candidates(project_dir=project)

    assert project_skills_dir(project).resolve() in unfiltered
    assert all(root.is_absolute() for root in unfiltered)
    # It exists, so it must not be reported as a root that could not be
    # searched. The user and Claude roots may or may not exist on this
    # machine, so they are not asserted on either way.
    assert project_skills_dir(project).resolve() not in missing(unfiltered)
