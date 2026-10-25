from __future__ import annotations

import json
from pathlib import Path

import pytest
from mvge_runes_skills_bridge.loader import (
    clear_skill_manifest_cache,
    discover_plugin_skill_paths,
    get_prioritized_skill_search_paths,
    load_cached_skill_manifest,
    load_plugin_manifest,
    load_skills_from_paths,
)
from mvge_runes_skills_bridge.types import (
    SkillDiagnostic,
    SkillDiagnosticKind,
    SkillScope,
)


def _create_skill(parent: Path, name: str, desc: str = "Test skill") -> Path:
    skill_dir = parent / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\nBody of {name}",
        encoding="utf-8",
    )
    return skill_dir


def test_get_prioritized_skill_search_paths(tmp_path: Path) -> None:
    cwd = tmp_path / "project"
    global_dir = tmp_path / "global"

    (cwd / ".agents" / "skills").mkdir(parents=True)
    (cwd / "skills").mkdir(parents=True)
    (global_dir / "skills").mkdir(parents=True)
    (global_dir / "agents" / "my_agent" / "skills").mkdir(parents=True)

    paths = get_prioritized_skill_search_paths(
        agent_name="my_agent",
        cwd=cwd,
        global_dir=global_dir,
    )

    scopes = [scope for _, scope in paths]
    assert scopes == [
        SkillScope.PROJECT,
        SkillScope.PROJECT,
        SkillScope.USER,
        SkillScope.AGENT,
    ]


def test_skill_search_paths_follow_the_global_dir_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user and agent skill layers come from $MVGE_GLOBAL_DIR.

    This used to be a second, hand-rolled copy of the precedence rule. It
    drifted from the engine's, so a run with the override set searched the
    real home for user and agent skills while everything else was isolated.
    """
    global_dir = tmp_path / "relocated_agents"
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(global_dir))
    (global_dir / "skills").mkdir(parents=True)
    (global_dir / "agents" / "my_agent" / "skills").mkdir(parents=True)
    cwd = tmp_path / "project"
    cwd.mkdir()

    paths = get_prioritized_skill_search_paths(
        agent_name="my_agent",
        cwd=cwd,
        global_dir=None,
    )

    found = {scope: path for path, scope in paths}
    assert found[SkillScope.USER] == global_dir / "skills"
    assert found[SkillScope.AGENT] == global_dir / "agents" / "my_agent" / "skills"


def test_load_cached_skill_manifest(tmp_path: Path) -> None:
    clear_skill_manifest_cache()
    s_dir = _create_skill(tmp_path, "cache-skill")

    # Missing SKILL.md returns None
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    assert load_cached_skill_manifest(empty_dir) is None

    m1 = load_cached_skill_manifest(s_dir)
    assert m1 is not None
    assert m1.name == "cache-skill"

    # Second load from cache
    m2 = load_cached_skill_manifest(s_dir)
    assert m2 is m1


def test_load_skills_precedence_and_shadowing(tmp_path: Path) -> None:
    project_skills = tmp_path / "proj_skills"
    user_skills = tmp_path / "user_skills"

    _create_skill(project_skills, "common-skill", "Project version")
    _create_skill(user_skills, "common-skill", "User version")
    _create_skill(user_skills, "user-only", "User only skill")

    # Include non-dir and ignored files
    (project_skills / ".hidden").mkdir()
    (project_skills / "__pycache__").mkdir()
    (project_skills / "not-a-dir.txt").write_text("file")

    search_paths = [
        (project_skills, SkillScope.PROJECT),
        (user_skills, SkillScope.USER),
        (tmp_path / "non-existent-dir", SkillScope.PROJECT),
    ]

    loads, diagnostics = load_skills_from_paths(search_paths)
    loaded_names = [l.manifest.name for l in loads]

    assert "common-skill" in loaded_names
    assert "user-only" in loaded_names

    # Project version must win
    common_manifest = next(
        l.manifest for l in loads if l.manifest.name == "common-skill"
    )
    assert common_manifest.description == "Project version"
    assert common_manifest.scope == SkillScope.PROJECT

    # User copy must be logged as shadowed
    shadowed = [d for d in diagnostics if d.kind == SkillDiagnosticKind.SHADOWED_SKILL]
    assert len(shadowed) == 1
    assert shadowed[0].skill_name == "common-skill"
    assert shadowed[0].scope == SkillScope.USER


def test_load_plugin_manifest(tmp_path: Path) -> None:
    # Missing plugin.json
    empty_dir = tmp_path / "no-plugin"
    empty_dir.mkdir()
    assert load_plugin_manifest(empty_dir) is None

    # Invalid JSON
    bad_dir = tmp_path / "bad-json"
    bad_dir.mkdir()
    (bad_dir / "plugin.json").write_text("{bad json", encoding="utf-8")
    diags: list[SkillDiagnostic] = []
    assert load_plugin_manifest(bad_dir, diagnostics=diags) is None
    assert any(d.kind == SkillDiagnosticKind.INVALID_PLUGIN for d in diags)

    # Missing name
    no_name_dir = tmp_path / "no-name"
    no_name_dir.mkdir()
    (no_name_dir / "plugin.json").write_text("{}", encoding="utf-8")
    assert load_plugin_manifest(no_name_dir) is None

    plugin_dir = tmp_path / "my-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "my-plugin",
                "version": "1.2.0",
                "description": "A test plugin",
                "skills": ["skills/sub-skill", {"path": "skills/dict-skill"}],
            }
        ),
        encoding="utf-8",
    )

    manifest = load_plugin_manifest(plugin_dir)
    assert manifest is not None
    assert manifest.name == "my-plugin"
    assert manifest.version == "1.2.0"
    assert "skills/sub-skill" in manifest.skills
    assert "skills/dict-skill" in manifest.skills


def test_discover_plugin_skill_paths(tmp_path: Path) -> None:
    cwd = tmp_path / "cwd"
    plugin_dir = cwd / ".agents" / "plugins" / "sample-plugin"
    plugin_dir.mkdir(parents=True)

    _create_skill(plugin_dir / "skills", "plug-skill")

    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "sample-plugin",
                "skills": ["skills/plug-skill"],
            }
        ),
        encoding="utf-8",
    )

    discovered = discover_plugin_skill_paths(
        cwd=cwd, global_dir=tmp_path / "empty_global"
    )
    assert len(discovered) == 1
    found_path, scope = discovered[0]
    assert found_path.name == "plug-skill"
    assert scope == SkillScope.PROJECT


def _create_skill_lowercase(parent: Path, name: str, desc: str = "Test skill") -> Path:
    skill_dir = parent / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\nBody of {name}",
        encoding="utf-8",
    )
    return skill_dir


def test_load_cached_skill_manifest_lowercase_fallback(tmp_path: Path) -> None:
    """A skill with only lowercase skill.md loads (protocol casing)."""
    clear_skill_manifest_cache()
    s_dir = _create_skill_lowercase(tmp_path, "lower-skill")

    manifest = load_cached_skill_manifest(s_dir)

    assert manifest is not None
    assert manifest.name == "lower-skill"
    assert manifest.location.endswith("skill.md")
    assert not manifest.location.endswith("SKILL.md")


def test_load_cached_skill_manifest_uppercase_preferred(tmp_path: Path) -> None:
    """SKILL.md wins when both casings exist."""
    clear_skill_manifest_cache()
    s_dir = _create_skill(tmp_path, "both-skill", "Upper version")
    # Write the lowercase variant first so both paths exist even on a
    # case-insensitive filesystem; samefile() then reports whether the two
    # casings collapse onto one file instead of raising FileNotFoundError.
    (s_dir / "skill.md").write_text(
        "---\nname: both-skill\ndescription: Lower version\n---\nLower body",
        encoding="utf-8",
    )
    if (s_dir / "SKILL.md").samefile(s_dir / "skill.md"):
        pytest.skip(
            "Filesystem is case-insensitive (cannot have distinct SKILL.md and skill.md)"
        )

    manifest = load_cached_skill_manifest(s_dir)

    assert manifest is not None
    assert manifest.description == "Upper version"
    assert manifest.location.endswith("SKILL.md")


def test_load_skills_from_paths_discovers_lowercase(tmp_path: Path) -> None:
    """End-to-end discovery picks up lowercase skill.md skills."""
    clear_skill_manifest_cache()
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _create_skill_lowercase(skills_dir, "lower-only")

    loads, _ = load_skills_from_paths([(skills_dir, SkillScope.PROJECT)])

    assert [load.manifest.name for load in loads] == ["lower-only"]


def test_load_cached_skill_manifest_neither_casing_returns_none(
    tmp_path: Path,
) -> None:
    """No manifest file at all means not a skill: skipped."""
    clear_skill_manifest_cache()
    empty_dir = tmp_path / "no-manifest"
    empty_dir.mkdir()

    assert load_cached_skill_manifest(empty_dir) is None
