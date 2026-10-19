"""``index.json``, the filesystem, and the pytest config must agree.

``index.json`` is the single source of truth for what this marketplace
publishes and what kind of thing each artifact is. Everything else -- the
``sys.path`` roots in ``conftest.py``, the ``testpaths`` and ``pythonpath``
lists in ``pyproject.toml``, the apt packages in the CI workflow, the
directories CI runs -- is downstream of it.

TOML and YAML cannot call into Python, so parts of that downstream config has
to restate the index literally. Restating is fine; *drifting* is not. These
tests exist so that drift fails the build instead of being discovered later,
by a user, when an artifact turns out to be uninstallable or untested.

Every check here is deliberately cheap and offline: it reads JSON and TOML and
stats paths. It imports nothing from the artifacts under test.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest

from marketplace_index import (
    FORBIDDEN_MANIFEST_KEYS,
    PYPROJECT_PATH,
    REPO_ROOT,
    REQUIRED_MANIFEST_KEYS,
    Artifact,
    artifact_dirs_on_disk,
    artifacts,
    as_repo_paths,
    ci_provisioned_packages,
    load_index,
    pytest_config,
)

ALL_ARTIFACTS = artifacts()


def _ids() -> list[str]:
    return [f"{a.kind}/{a.name}" for a in ALL_ARTIFACTS]


# --------------------------------------------------------------------------
# The index and the filesystem
# --------------------------------------------------------------------------


def test_index_is_not_empty() -> None:
    """Guard against the parametrized tests below silently collecting nothing."""
    assert ALL_ARTIFACTS, "index.json declares no artifacts"


@pytest.mark.parametrize("artifact", ALL_ARTIFACTS, ids=_ids())
def test_indexed_artifact_directory_exists(artifact: Artifact) -> None:
    assert artifact.path.is_dir(), (
        f"{artifact.kind}/{artifact.name} points at {artifact.relative_path}, "
        f"which does not exist"
    )


@pytest.mark.parametrize("artifact", ALL_ARTIFACTS, ids=_ids())
def test_manifest_is_at_the_artifact_root(artifact: Artifact) -> None:
    """The engine reads ``<rune-dir>/manifest.json`` when installing.

    A manifest nested inside the package (``<rune>/pkg/manifest.json``) makes
    the artifact uninstallable: ``install_rune`` fails with
    ``Invalid rune: manifest.json missing``, and ``index.json`` happily
    advertises it. This is the check that catches that.
    """
    manifest_path = artifact.path / "manifest.json"
    assert manifest_path.is_file(), (
        f"{artifact.kind}/{artifact.name} has no manifest.json at its root. "
        f"The installer reads <artifact-dir>/manifest.json, so a manifest "
        f"inside the package directory makes this artifact uninstallable."
    )


@pytest.mark.parametrize("artifact", ALL_ARTIFACTS, ids=_ids())
def test_manifest_declares_required_keys(artifact: Artifact) -> None:
    """Each artifact must carry the keys that make it the kind it is listed as."""
    required = (
        "name",
        "version",
        "description",
        "entry_point",
        *REQUIRED_MANIFEST_KEYS[artifact.kind],
    )
    missing = [key for key in required if not artifact.manifest.get(key)]
    assert not missing, (
        f"{artifact.kind}/{artifact.name} is missing {missing}. "
        f"An entry in the {artifact.kind!r} section must declare {sorted(required)}."
    )


@pytest.mark.parametrize("artifact", ALL_ARTIFACTS, ids=_ids())
def test_manifest_does_not_contradict_its_section(artifact: Artifact) -> None:
    """An artifact must not carry keys belonging to the other kind.

    This is the ``mvges``/``runes`` split stated once, in one place, instead
    of being implied by which directory a thing sits in.
    """
    forbidden = FORBIDDEN_MANIFEST_KEYS[artifact.kind]
    present = [key for key in forbidden if artifact.manifest.get(key)]
    assert not present, (
        f"{artifact.kind}/{artifact.name} declares {present}, which belongs to "
        f"the other kind of artifact. Either it is filed under the wrong "
        f"section of index.json, or it has become the other kind of thing."
    )


@pytest.mark.parametrize("artifact", ALL_ARTIFACTS, ids=_ids())
def test_index_key_matches_manifest_name(artifact: Artifact) -> None:
    """The index key names the install directory the user will type.

    ``install_rune`` derives the destination from ``manifest.name``, so a
    mismatch means ``mvgeos rune install <index-key>`` produces a directory
    the user did not ask for.
    """
    manifest_name = artifact.manifest.get("name")
    assert manifest_name == artifact.name, (
        f"index.json lists {artifact.kind}/{artifact.name} but its manifest "
        f"declares name={manifest_name!r}. The manifest name is authoritative "
        f"for the install directory, so these must match."
    )


@pytest.mark.parametrize("artifact", ALL_ARTIFACTS, ids=_ids())
def test_index_and_manifest_declare_the_same_system_deps(artifact: Artifact) -> None:
    """A binary dependency has to be declared in both places that are read.

    ``index.json`` is what the engine fetches to resolve an artifact for
    install, and ``manifest.json`` is what ``mvgeos setup`` reads when it
    warns about a missing system dependency -- it maps ``ripgrep`` to ``rg``
    before checking ``PATH``, so the manifest value is a tool name rather
    than the program name. A dependency that appears in one of the two and
    not the other leaves a Rune that installs cleanly and then fails at use
    with 'rg not found on PATH', which is how seeker's ripgrep requirement
    shipped with nothing declaring it on this side.

    Nothing else in the build would notice: a system binary cannot be listed
    in ``pyproject.toml``, so there is no dependency resolution to fail on.
    """
    index_entry = load_index()[artifact.kind][artifact.name]
    indexed = sorted(index_entry.get("system_deps") or [])
    declared = sorted(artifact.manifest.get("system_deps") or [])
    assert indexed == declared, (
        f"{artifact.kind}/{artifact.name}: index.json declares "
        f"system_deps={indexed or 'none'} but its manifest declares "
        f"system_deps={declared or 'none'}. Each is read by a different "
        f"caller, so a mismatch means at least one of them is lying about "
        f"what the artifact needs to run."
    )


def test_every_declared_system_dep_is_provisioned_in_ci() -> None:
    """A ``system_deps`` entry that CI does not install is a declaration no one reads.

    ``index.json`` and ``manifest.json`` are read by the engine, which can only
    *report* a missing binary. The workflow is the third consumer: it is the only
    thing in this repository that can put one on ``PATH``, and it is where a clean
    runner acquires the dependency for a Rune nobody has installed by hand yet.

    That third reader is a literal restatement, because a YAML workflow cannot call
    into Python to read the index. Restating is fine; restating *only for the Rune
    that happened to need it first* is not. Seeker got its ``rg`` provision added by
    hand when ``rg`` was missing from the runner, and a sixteenth Rune declaring
    ``system_deps: ["fd"]`` would get no provision at all -- the suite would then go
    red with three tests saying ``rg not found on PATH``, a message that points at
    the tool subprocess rather than at the line that is missing from the workflow.
    The same diagnostic trap, again, on the next Rune.

    So this asserts the direction that nothing else covers: every entry declared
    in either artifact has to be named by an ``apt-get install`` in a workflow. A
    new declaration now fails here, at PR time, naming the artifact and the tool.
    """
    index = load_index()
    declared: dict[str, set[str]] = {}
    for artifact in ALL_ARTIFACTS:
        entry = index[artifact.kind][artifact.name]
        declared[f"{artifact.kind}/{artifact.name}"] = set(
            artifact.manifest.get("system_deps") or []
        ) | set(entry.get("system_deps") or [])

    provisioned = ci_provisioned_packages()
    gaps = [
        (label, tool)
        for label, tools in sorted(declared.items())
        for tool in sorted(tools)
        if tool not in provisioned
    ]
    where = (
        ", ".join(sorted({w for where in provisioned.values() for w in where}))
        or ".github/workflows/ci.yml"
    )

    assert not gaps, (
        "These artifacts declare a system dependency that CI never installs:\n"
        + "\n".join(f"  {label} declares {tool!r}" for label, tool in gaps)
        + f"\nAdd the package to the apt-get install line in {where}. Without it a "
        "clean runner reports the failure much later, from the tool subprocess, as "
        "'<tool> not found on PATH' -- which names the spell rather than the "
        "missing provision."
    )


@pytest.mark.parametrize("artifact", ALL_ARTIFACTS, ids=_ids())
def test_entry_point_file_exists_at_the_artifact_root(artifact: Artifact) -> None:
    """``manifest.entry_point`` is resolved relative to the artifact directory.

    Every rune ships a thin root ``rune.py`` that re-exports the factory from
    its package. A manifest pointing at a module that only exists *inside* the
    package (``rune_factory.py``) installs cleanly and then fails to load.
    """
    entry_point = artifact.entry_point_path()
    assert entry_point.is_file(), (
        f"{artifact.kind}/{artifact.name} declares entry_point="
        f"{artifact.manifest.get('entry_point')!r}, but {entry_point.name} does "
        f"not exist at the artifact root. The engine resolves entry_point "
        f"relative to the artifact directory, not the package."
    )


@pytest.mark.parametrize("artifact", ALL_ARTIFACTS, ids=_ids())
def test_artifact_path_is_inside_its_kind_directory(artifact: Artifact) -> None:
    """The path must sit under the directory its index section implies."""
    expected_parent = REPO_ROOT / artifact.kind
    assert artifact.path.parent == expected_parent, (
        f"{artifact.kind}/{artifact.name} points at {artifact.relative_path}, "
        f"which is not directly under {artifact.kind}/"
    )


def test_no_unindexed_artifact_directories() -> None:
    """An artifact on disk but absent from ``index.json`` is unreachable.

    Users install by index lookup, so a directory nobody indexed is dead
    weight that will never be installable and will never be tested.
    """
    indexed = {a.path for a in ALL_ARTIFACTS}
    orphans = [p for p in artifact_dirs_on_disk() if p not in indexed]
    assert not orphans, (
        "These artifact directories exist but are not in index.json:\n"
        + "\n".join(f"  {p.relative_to(REPO_ROOT)}" for p in orphans)
        + "\nAdd them to the appropriate section, or delete them."
    )


def test_index_section_keys_match_directory_names() -> None:
    """Each artifact's directory name matches the index key that points at it."""
    for artifact in ALL_ARTIFACTS:
        assert artifact.path.name == artifact.name, (
            f"{artifact.kind}/{artifact.name} lives in a directory called "
            f"{artifact.path.name!r}"
        )


# --------------------------------------------------------------------------
# Static pytest config, verified against the index
# --------------------------------------------------------------------------


def _configured(option: str) -> list[str]:
    value = pytest_config().get(option, [])
    assert isinstance(value, list), f"[tool.pytest.ini_options] {option} must be a list"
    return [str(v) for v in value]


def test_pytest_pythonpath_covers_every_artifact() -> None:
    """Every artifact that ships a package must be importable.

    ``conftest.py`` also puts these on ``sys.path``, but pytest needs the ini
    entry for rootdir-independent resolution (and for a bare ``pytest`` run in
    an IDE). Keeping both in sync is checked here rather than assumed.

    Artifacts with no package of their own (``session-title`` is a single
    ``rune.py``) are excluded: they contribute no importable name, and adding
    their directory would only make a bare ``import rune`` ambiguous between
    artifacts.
    """
    configured = set(_configured("pythonpath"))
    with_packages = [a for a in ALL_ARTIFACTS if a.top_level_names]
    assert with_packages, "no artifact ships a package; the pythonpath check is vacuous"
    expected = as_repo_paths([a.import_root for a in with_packages])
    missing = expected - configured
    assert not missing, (
        f"pyproject.toml [tool.pytest.ini_options] pythonpath is missing "
        f"{sorted(missing)}. Every artifact that ships a package must be "
        f"importable."
    )


def test_pytest_pythonpath_has_no_stale_entries() -> None:
    """Configured import roots must still exist.

    A stale ``pythonpath`` entry is not harmless: it puts a directory with a
    package-shaped name in front of ``sys.path``, which can shadow the real
    one.
    """
    missing = [p for p in _configured("pythonpath") if not (REPO_ROOT / p).is_dir()]
    assert not missing, f"pythonpath points at directories that do not exist: {missing}"


def test_pytest_testpaths_covers_every_artifact_with_tests() -> None:
    """Every artifact that ships tests must have them collected.

    An artifact whose tests are absent from ``testpaths`` runs green in CI
    while its tests sit unexecuted. This is the silent-coverage hole that a
    hand-maintained list invites.
    """
    configured = {p.rstrip("/") for p in _configured("testpaths")}
    with_tests = [a for a in ALL_ARTIFACTS if a.has_tests()]
    assert with_tests, "no artifact ships tests; the testpaths check is vacuous"
    expected = as_repo_paths([a.test_dir for a in with_tests])
    missing = expected - configured
    assert not missing, (
        f"pyproject.toml [tool.pytest.ini_options] testpaths is missing "
        f"{sorted(missing)}. These artifacts ship tests that CI would not run."
    )


def test_pytest_testpaths_has_no_stale_entries() -> None:
    """Configured test directories must still exist.

    A leftover path makes pytest error out with a confusing message; a path
    that exists but has no tests is worse, because it reads as coverage.
    """
    missing = [p for p in _configured("testpaths") if not (REPO_ROOT / p).is_dir()]
    assert not missing, f"testpaths points at directories that do not exist: {missing}"


# --------------------------------------------------------------------------
# Loadability
# --------------------------------------------------------------------------


def test_every_rune_entry_point_actually_loads() -> None:
    """An entry point that cannot be imported is an inert rune.

    Layout and manifest checks pass for a rune whose module raises on import --
    ``session-title`` shipped with a module-level ``@dataclass`` that the
    engine's loader could not execute, so the manifest looked perfect and the
    rune did nothing. ``mvgeos rune list`` would still have shown it.

    A missing ``python_dep`` is *not* a failure here: the loader reports it as
    a dependency diagnostic and that is a legitimate, explainable state. A
    module that fails to *execute* is not.
    """
    import sys

    from mvgeos_runes.loader import load_factory_from_manifest
    from mvgeos_runes.manifest import load_manifest
    from mvgeos_runes.types import DiagnosticKind

    failures: list[str] = []
    for artifact in ALL_ARTIFACTS:
        if artifact.kind != "runes":
            continue
        entry_root = str(artifact.import_root)
        sys.path.insert(0, entry_root)
        try:
            manifest = load_manifest(artifact.path)
            assert manifest is not None, f"{artifact.name}: unreadable manifest"
            diagnostics: list[object] = []
            load_factory_from_manifest(manifest, artifact.path, diagnostics)  # type: ignore[arg-type]
            for diagnostic in diagnostics:  # type: ignore[attr-defined]
                if getattr(diagnostic, "kind", None) is DiagnosticKind.LOAD_FAILURE:
                    message = str(getattr(diagnostic, "message", ""))
                    if "Failed to execute rune module" in message:
                        failures.append(f"{artifact.name}: {message}")
        finally:
            with contextlib.suppress(ValueError):
                sys.path.remove(entry_root)
            sys.modules.pop(f"mvgeos_rune_{artifact.name}", None)

    assert not failures, "These runes install but cannot be loaded:\n" + "\n".join(
        f"  {f}" for f in failures
    )


def test_no_first_party_module_resolved_outside_the_repo() -> None:
    """Nothing under test may come from an installed copy.

    The engine puts ``~/.agents/agents`` at the front of ``sys.path`` the
    moment an Mvge is constructed, which happens while test modules are being
    imported. ``conftest.py`` imports every pinned package before collection to
    get ahead of that; this is the backstop, checked after collection has
    already pulled in every test module.

    A silent wrong-tree import means the suite passed against a stale snapshot
    while the source was broken, which is worse than no test at all.
    """
    import sys

    repo = REPO_ROOT.resolve()
    pinned = {name for a in ALL_ARTIFACTS for name in a.top_level_names}
    shadowed: list[str] = []
    for name, module in list(sys.modules.items()):
        if name.split(".", 1)[0] not in pinned:
            continue
        file = getattr(module, "__file__", None)
        if file is None:
            continue
        if not Path(file).resolve().is_relative_to(repo):
            shadowed.append(f"{name} -> {file}")

    assert not shadowed, (
        "These modules resolved from outside the repository, so the tests "
        "exercised an installed copy rather than this checkout:\n"
        + "\n".join(f"  {s}" for s in sorted(shadowed))
    )


# --------------------------------------------------------------------------
# Index well-formedness
# --------------------------------------------------------------------------


def test_index_json_is_valid_and_typed() -> None:
    """Guard the shape the helpers above assume while iterating."""
    raw = json.loads((REPO_ROOT / "index.json").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    for section in REQUIRED_MANIFEST_KEYS:
        assert section in raw, f"index.json is missing the {section!r} section"
        assert isinstance(raw[section], dict), f"{section} must be an object"
        for name, entry in raw[section].items():
            assert isinstance(entry, dict), f"{section}/{name} must be an object"
            assert isinstance(entry.get("path"), str), (
                f"{section}/{name} needs a string 'path'"
            )
            assert entry.get("description"), f"{section}/{name} needs a description"
            assert entry.get("version"), f"{section}/{name} needs a version"


def test_pyproject_is_parseable_toml() -> None:
    """``pytest_config`` reads this file on every conftest import."""
    assert PYPROJECT_PATH.is_file()
    assert isinstance(pytest_config(), dict)


def test_artifact_names_are_valid_install_directory_names() -> None:
    """Names become directory names under ``~/.agents``.

    The installer rejects anything outside ``[A-Za-z0-9_-]+``, so an index
    entry with a dot or a slash advertises something that cannot install.
    """
    import re

    pattern = re.compile(r"^[A-Za-z0-9_-]+$")
    for artifact in ALL_ARTIFACTS:
        assert pattern.match(artifact.name), (
            f"{artifact.kind}/{artifact.name}: {artifact.name!r} is not a valid "
            f"install directory name"
        )
        assert artifact.path.is_relative_to(REPO_ROOT), (
            f"{artifact.kind}/{artifact.name} escapes the repository"
        )
        assert not Path(artifact.relative_path).is_absolute()


def test_profiles_reference_valid_artifacts() -> None:
    """Profiles declared in index.json must reference existing runes and mvges."""
    index = load_index()
    profiles = index.get("profiles", {})
    assert "minimal" in profiles
    assert "coding" in profiles
    assert "autonomous" in profiles

    runes_dict = index.get("runes", {})
    mvges_dict = index.get("mvges", {})

    for profile_name, data in profiles.items():
        assert isinstance(data, dict), f"profile {profile_name} must be a dict"
        for mvge_name in data.get("mvges", []):
            assert mvge_name in mvges_dict, (
                f"Profile {profile_name} references unknown mvge {mvge_name}"
            )
        for rune_name in data.get("runes", []):
            assert rune_name in runes_dict, (
                f"Profile {profile_name} references unknown rune {rune_name}"
            )
