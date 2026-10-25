from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from mvge_runes_skills_bridge.parser import (
    parse_skill_manifest,
    repair_yaml_unquoted_colons,
)
from mvge_runes_skills_bridge.types import (
    SkillDiagnostic,
    SkillDiagnosticKind,
)


def test_repair_yaml_unquoted_colons() -> None:
    bad_yaml = "description: Use this skill when: handling PDF files\nversion: 1.0"
    repaired = repair_yaml_unquoted_colons(bad_yaml)
    assert 'description: "Use this skill when: handling PDF files"' in repaired


def test_parse_valid_skill(tmp_path: Path) -> None:
    skill_dir = tmp_path / "valid-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: valid-skill\n"
        "description: Performs valid operations\n"
        "version: 1.0.0\n"
        "license: MIT\n"
        "compatibility: Python 3.13+\n"
        "allowed-tools: read_file run_command\n"
        "disable-model-invocation: true\n"
        "---\n"
        "# Instructions\n"
        "Follow these steps.",
        encoding="utf-8",
    )
    diagnostics: list[SkillDiagnostic] = []
    manifest = parse_skill_manifest(skill_dir, diagnostics=diagnostics)
    assert manifest is not None
    assert manifest.name == "valid-skill"
    assert manifest.description == "Performs valid operations"
    assert manifest.version == "1.0.0"
    assert manifest.license == "MIT"
    assert manifest.compatibility == "Python 3.13+"
    assert manifest.allowed_tools == "read_file run_command"
    assert manifest.disable_model_invocation is True
    assert manifest.body == "# Instructions\nFollow these steps."
    assert len(diagnostics) == 0


def test_parse_unquoted_colon_repair(tmp_path: Path) -> None:
    skill_dir = tmp_path / "colon-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: colon-skill\n"
        "description: Activate when: data analysis is requested\n"
        "---\n"
        "Analysis steps.",
        encoding="utf-8",
    )
    diagnostics: list[SkillDiagnostic] = []
    manifest = parse_skill_manifest(skill_dir, diagnostics=diagnostics)
    assert manifest is not None
    assert manifest.name == "colon-skill"
    assert "Activate when: data analysis is requested" in manifest.description
    assert any(d.kind == SkillDiagnosticKind.MALFORMED_YAML for d in diagnostics)


def test_parse_missing_skill_md(tmp_path: Path) -> None:
    skill_dir = tmp_path / "empty-dir"
    skill_dir.mkdir()
    assert parse_skill_manifest(skill_dir) is None


def test_parse_missing_frontmatter(tmp_path: Path) -> None:
    skill_dir = tmp_path / "no-frontmatter"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "# Just Markdown without YAML", encoding="utf-8"
    )
    diagnostics: list[SkillDiagnostic] = []
    manifest = parse_skill_manifest(skill_dir, diagnostics=diagnostics)
    assert manifest is None
    assert any(d.kind == SkillDiagnosticKind.PARSE_WARNING for d in diagnostics)


def test_parse_invalid_yaml(tmp_path: Path) -> None:
    skill_dir = tmp_path / "bad-yaml"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: bad-yaml\n  invalid: indentation: [unclosed\n---\n",
        encoding="utf-8",
    )
    diagnostics: list[SkillDiagnostic] = []
    manifest = parse_skill_manifest(skill_dir, diagnostics=diagnostics)
    assert manifest is None
    assert any(d.kind == SkillDiagnosticKind.PARSE_WARNING for d in diagnostics)


def test_parse_missing_description(tmp_path: Path) -> None:
    skill_dir = tmp_path / "no-desc"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: no-desc\n---\n",
        encoding="utf-8",
    )
    diagnostics: list[SkillDiagnostic] = []
    manifest = parse_skill_manifest(skill_dir, diagnostics=diagnostics)
    assert manifest is None
    assert any("description" in d.message for d in diagnostics)


def test_parse_lenient_vs_strict_name(tmp_path: Path) -> None:
    skill_dir = tmp_path / "dir-name"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: DifferentName\ndescription: Mismatched and uppercase name\n---\n",
        encoding="utf-8",
    )
    diagnostics: list[SkillDiagnostic] = []
    lenient_manifest = parse_skill_manifest(
        skill_dir, diagnostics=diagnostics, lenient=True
    )
    assert lenient_manifest is not None
    assert lenient_manifest.name == "DifferentName"
    assert any(d.kind == SkillDiagnosticKind.PARSE_WARNING for d in diagnostics)

    strict_diagnostics: list[SkillDiagnostic] = []
    strict_manifest = parse_skill_manifest(
        skill_dir, diagnostics=strict_diagnostics, lenient=False
    )
    assert strict_manifest is None


def test_parse_non_mapping_yaml(tmp_path: Path) -> None:
    skill_dir = tmp_path / "list-yaml"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n- item1\n- item2\n---\n", encoding="utf-8"
    )
    diagnostics: list[SkillDiagnostic] = []
    manifest = parse_skill_manifest(skill_dir, diagnostics=diagnostics)
    assert manifest is None
    assert any("not a YAML mapping" in d.message for d in diagnostics)


def test_parse_missing_name(tmp_path: Path) -> None:
    skill_dir = tmp_path / "missing-name"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: desc only\n---\n", encoding="utf-8"
    )
    diagnostics: list[SkillDiagnostic] = []
    manifest = parse_skill_manifest(skill_dir, diagnostics=diagnostics)
    assert manifest is None
    assert any("missing 'name'" in d.message for d in diagnostics)


def test_parse_name_too_long(tmp_path: Path) -> None:
    long_name = "a" * 70
    skill_dir = tmp_path / long_name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {long_name}\ndescription: desc\n---\n",
        encoding="utf-8",
    )
    diagnostics: list[SkillDiagnostic] = []
    manifest = parse_skill_manifest(skill_dir, diagnostics=diagnostics, lenient=True)
    assert manifest is not None
    assert any("exceeds 64 characters" in d.message for d in diagnostics)

    strict = parse_skill_manifest(skill_dir, lenient=False)
    assert strict is None


def test_parse_read_os_error(tmp_path: Path) -> None:
    skill_dir = tmp_path / "os-err"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: os-err\ndescription: ok\n---\n")

    with patch.object(Path, "read_text", side_effect=OSError("Read failure")):
        res = parse_skill_manifest(skill_dir)
        assert res is None
