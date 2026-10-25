from __future__ import annotations

from pathlib import Path

import pytest
from mvge_runes_skills_bridge.activation import (
    activate_skill,
    enumerate_skill_resources,
)
from mvge_runes_skills_bridge.types import SkillManifest


def test_enumerate_skill_resources(tmp_path: Path) -> None:
    base = tmp_path / "my-skill"
    (base / "scripts").mkdir(parents=True)
    (base / "references").mkdir(parents=True)
    (base / "scripts" / "run.py").write_text("print(1)")
    (base / "references" / "guide.md").write_text("# Guide")
    (base / "SKILL.md").write_text("---\nname: my-skill\n---")

    resources = enumerate_skill_resources(base)
    assert "scripts/run.py" in resources
    assert "references/guide.md" in resources
    assert "SKILL.md" not in resources


def test_enumerate_skill_resources_cap(tmp_path: Path) -> None:
    base = tmp_path / "large-skill"
    (base / "scripts").mkdir(parents=True)
    for i in range(60):
        (base / "scripts" / f"tool_{i:02d}.py").write_text("pass")

    resources = enumerate_skill_resources(base)
    assert len(resources) == 50


def test_activate_skill_success(tmp_path: Path) -> None:
    skill_dir = tmp_path / "calc-skill"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "scripts" / "calc.py").write_text("def add(a, b): return a + b")

    m = SkillManifest(
        name="calc-skill",
        description="Math operations",
        path=str(skill_dir),
        body="# Calculator Instructions\nUse scripts/calc.py.",
    )
    skills_map = {"calc-skill": m}
    active_skills: set[str] = set()

    res = activate_skill("calc-skill", skills_map, active_skills)
    assert res.name == "calc-skill"
    assert "calc-skill" in active_skills
    assert '<skill_content name="calc-skill">' in res.content
    assert "# Calculator Instructions" in res.content
    assert f"Skill directory: {skill_dir.as_posix()}" in res.content
    assert "<skill_resources>" in res.content
    assert "<file>scripts/calc.py</file>" in res.content


def test_activate_skill_reads_body_from_file(tmp_path: Path) -> None:
    skill_dir = tmp_path / "file-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: file-skill\ndescription: Reads from disk\n---\n# On-disk body",
        encoding="utf-8",
    )

    m = SkillManifest(
        name="file-skill",
        description="Reads from disk",
        path=str(skill_dir),
        body=None,  # Body to be read on activation
    )
    res = activate_skill("file-skill", {"file-skill": m}, set())
    assert "# On-disk body" in res.content


def test_activate_skill_deduplication(tmp_path: Path) -> None:
    m = SkillManifest(
        name="repeat-skill",
        description="Repeated skill",
        path=str(tmp_path / "repeat-skill"),
        body="Body text",
    )
    skills_map = {"repeat-skill": m}
    active_skills: set[str] = {"repeat-skill"}

    res = activate_skill("repeat-skill", skills_map, active_skills)
    assert 'already_active="true"' in res.content
    assert "already active in this conversation context" in res.content


def test_activate_skill_unknown_raises() -> None:
    skills_map: dict[str, SkillManifest] = {}
    active_skills: set[str] = set()
    with pytest.raises(ValueError, match="Skill 'unknown' not found"):
        activate_skill("unknown", skills_map, active_skills)
