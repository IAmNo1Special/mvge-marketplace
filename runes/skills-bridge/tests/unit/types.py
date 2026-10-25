from __future__ import annotations

from pathlib import Path

from mvge_runes_skills_bridge.types import (
    PluginManifest,
    SkillActivationResult,
    SkillDiagnostic,
    SkillDiagnosticKind,
    SkillLoad,
    SkillManifest,
    SkillScope,
)


def test_skill_scope_values() -> None:
    assert SkillScope.PROJECT == "project"
    assert SkillScope.USER == "user"
    assert SkillScope.AGENT == "agent"


def test_skill_manifest_base_dir_and_location() -> None:
    m = SkillManifest(
        name="test-skill",
        description="A test skill",
        path="/some/path/to/test-skill",
    )
    assert m.name == "test-skill"
    assert m.location == "/some/path/to/test-skill/SKILL.md"
    assert m.base_dir == Path("/some/path/to/test-skill")


def test_skill_manifest_with_skill_md_path() -> None:
    m = SkillManifest(
        name="test-skill",
        description="A test skill",
        path="/some/path/to/test-skill/SKILL.md",
    )
    assert m.location == "/some/path/to/test-skill/SKILL.md"
    assert m.base_dir == Path("/some/path/to/test-skill")


def test_skill_load_wrapper() -> None:
    m = SkillManifest(name="s1", description="desc", path="/tmp/s1")
    load = SkillLoad(manifest=m)
    assert load.manifest == m


def test_plugin_manifest_defaults() -> None:
    p = PluginManifest(name="my-plugin")
    assert p.version == "1.0.0"
    assert p.skills == []
    assert p.description == ""


def test_skill_diagnostic() -> None:
    diag = SkillDiagnostic(
        kind=SkillDiagnosticKind.SHADOWED_SKILL,
        skill_name="my-skill",
        message="Shadowed by project",
        scope=SkillScope.USER,
        path="/tmp/user/my-skill",
    )
    assert diag.kind == SkillDiagnosticKind.SHADOWED_SKILL
    assert diag.skill_name == "my-skill"
    assert diag.scope == SkillScope.USER


def test_skill_activation_result() -> None:
    res = SkillActivationResult(
        name="s1",
        content="<skill_content>hi</skill_content>",
        location="/tmp/s1/SKILL.md",
        resources=["scripts/run.py"],
    )
    assert res.name == "s1"
    assert res.resources == ["scripts/run.py"]
