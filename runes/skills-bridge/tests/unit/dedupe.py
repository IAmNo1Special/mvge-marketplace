from __future__ import annotations

from pathlib import Path

from mvge_runes_skills_bridge.dedupe import (
    execute_skill_dedupe,
    plan_skill_dedupe,
    render_dedupe_plan_table,
)
from mvge_runes_skills_bridge.types import SkillScope


def _create_skill(root: Path, name: str) -> Path:
    s = root / name
    s.mkdir(parents=True, exist_ok=True)
    (s / "SKILL.md").write_text(f"---\nname: {name}\ndescription: desc\n---\nbody")
    return s


def test_plan_and_execute_skill_dedupe(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    user_dir = tmp_path / "user"

    _create_skill(project_dir, "shared-skill")
    user_skill = _create_skill(user_dir, "shared-skill")
    _create_skill(user_dir, "user-only")

    search_paths = [
        (project_dir, SkillScope.PROJECT),
        (user_dir, SkillScope.USER),
    ]

    plan = plan_skill_dedupe(skill_paths=search_paths)
    assert len(plan) == 1
    assert plan[0].name == "shared-skill"
    assert plan[0].shadow_scope == SkillScope.USER
    assert plan[0].winner_scope == SkillScope.PROJECT

    table = render_dedupe_plan_table(plan)
    assert "shared-skill" in table
    assert "user" in table
    assert "project" in table

    # Dry run does not delete
    removed_dry = execute_skill_dedupe(plan, dry_run=True)
    assert len(removed_dry) == 1
    assert user_skill.is_dir()

    # Real run deletes
    removed_real = execute_skill_dedupe(plan, dry_run=False)
    assert len(removed_real) == 1
    assert not user_skill.exists()
