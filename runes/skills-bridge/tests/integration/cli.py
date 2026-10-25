from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from mvge_runes_skills_bridge.cli import skill_app
from mvge_runes_skills_bridge.types import (
    SkillDiagnostic,
    SkillDiagnosticKind,
    SkillLoad,
    SkillManifest,
    SkillScope,
)
from typer.testing import CliRunner

runner = CliRunner()


def _write_skill(parent: Path, name: str, desc: str = "Test skill") -> Path:
    s = parent / name
    s.mkdir(parents=True, exist_ok=True)
    (s / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\nBody of {name}",
        encoding="utf-8",
    )
    return s


def test_cli_skill_callback_help() -> None:
    res = runner.invoke(skill_app)
    assert res.exit_code == 0
    assert "Inspect, validate, and manage agent skills" in res.output


def test_cli_skill_list_and_show(tmp_path: Path) -> None:
    project_skills = tmp_path / "project_skills"
    _write_skill(project_skills, "cli-skill", "CLI test skill")

    with patch(
        "mvge_runes_skills_bridge.cli.get_prioritized_skill_search_paths",
        return_value=[(project_skills, SkillScope.PROJECT)],
    ):
        # List
        res_list = runner.invoke(skill_app, ["list"])
        assert res_list.exit_code == 0
        assert "FOUND: 1 skills" in res_list.output
        assert "cli-skill" in res_list.output

        # Show
        res_show = runner.invoke(skill_app, ["show", "cli-skill"])
        assert res_show.exit_code == 0
        assert "Skill: cli-skill" in res_show.output
        assert "CLI test skill" in res_show.output
        assert "Body of cli-skill" in res_show.output


def test_cli_skill_list_empty() -> None:
    with (
        patch(
            "mvge_runes_skills_bridge.cli.get_prioritized_skill_search_paths",
            return_value=[],
        ),
        patch(
            "mvge_runes_skills_bridge.cli.discover_plugin_skill_paths",
            return_value=[],
        ),
    ):
        res = runner.invoke(skill_app, ["list"])
        assert res.exit_code == 0
        assert "MISSING: No skills found" in res.output


def test_cli_skill_list_with_shadowed() -> None:
    loads = [
        SkillLoad(manifest=SkillManifest(name="s1", description="d1", path="/tmp/s1"))
    ]
    diags = [
        SkillDiagnostic(
            kind=SkillDiagnosticKind.SHADOWED_SKILL,
            skill_name="s1",
            message="Shadowed",
            scope=SkillScope.USER,
            path="/tmp/user/s1",
        )
    ]
    with patch(
        "mvge_runes_skills_bridge.cli.load_skills_from_paths",
        return_value=(loads, diags),
    ):
        res = runner.invoke(skill_app, ["list"])
        assert res.exit_code == 0
        assert "SHADOWED: 1 skill copies shadowed:" in res.output
        assert "s1 (user): /tmp/user/s1" in res.output


def test_cli_skill_validate(tmp_path: Path) -> None:
    valid_dir = _write_skill(tmp_path, "valid-one")
    res_val = runner.invoke(skill_app, ["validate", str(valid_dir)])
    assert res_val.exit_code == 0
    assert "OK: Skill 'valid-one' is valid." in res_val.output

    # Invalid dir
    invalid_dir = tmp_path / "invalid-one"
    invalid_dir.mkdir()
    res_bad = runner.invoke(skill_app, ["validate", str(invalid_dir)])
    assert res_bad.exit_code == 1
    assert "FAIL:" in res_bad.output


def test_cli_skill_dedupe_flow(tmp_path: Path) -> None:
    project_skills = tmp_path / "project_skills"
    user_skills = tmp_path / "user_skills"

    _write_skill(project_skills, "shadow-me", "Project version")
    user_copy = _write_skill(user_skills, "shadow-me", "User version")

    paths = [
        (project_skills, SkillScope.PROJECT),
        (user_skills, SkillScope.USER),
    ]

    with patch(
        "mvge_runes_skills_bridge.dedupe.get_prioritized_skill_search_paths",
        return_value=paths,
    ):
        # Dry-run
        res_dry = runner.invoke(skill_app, ["dedupe", "--dry-run"])
        assert res_dry.exit_code == 0
        assert "DRY-RUN:" in res_dry.output
        assert user_copy.exists()

        # Without yes
        res_prompt = runner.invoke(skill_app, ["dedupe"])
        assert res_prompt.exit_code == 0
        assert "--yes" in res_prompt.output
        assert user_copy.exists()

        # With yes
        res_yes = runner.invoke(skill_app, ["dedupe", "--yes"])
        assert res_yes.exit_code == 0
        assert "OK: Removed 1 shadowed skill directories." in res_yes.output
        assert not user_copy.exists()


def test_cli_skill_dedupe_no_shadows() -> None:
    with patch(
        "mvge_runes_skills_bridge.cli.plan_skill_dedupe",
        return_value=[],
    ):
        res = runner.invoke(skill_app, ["dedupe"])
        assert res.exit_code == 0
        assert "OK: No shadowed skills found." in res.output


def test_cli_skill_show_not_found() -> None:
    with patch(
        "mvge_runes_skills_bridge.cli.load_skills_from_paths",
        return_value=([], []),
    ):
        res = runner.invoke(skill_app, ["show", "nonexistent"])
        assert res.exit_code == 1
        assert "FAIL: Skill 'nonexistent' not found." in res.output
