"""Unit tests for MADR 3.0 validator."""

from __future__ import annotations

from pathlib import Path

from mvge_runes_adr_bridge.validator import validate_adrs


def test_validator_missing_directory(tmp_path: Path) -> None:
    report = validate_adrs(cwd=tmp_path)
    assert report.valid is True
    assert any("MISSING:" in w.message for w in report.warnings)


def test_validator_empty_directory(tmp_path: Path) -> None:
    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    report = validate_adrs(cwd=tmp_path)
    assert report.valid is True
    assert any("0 Architectural Decision Records" in w.message for w in report.warnings)


def test_validator_valid_madr(tmp_path: Path) -> None:
    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True)

    (adr_dir / "0001-microkernel.md").write_text(
        "# Microkernel Architecture\n\n"
        "* Status: accepted\n"
        "* Date: 2026-09-01\n\n"
        "## Context and Problem Statement\n\n"
        "Problem statement here.\n\n"
        "## Considered Options\n\n"
        "* Option A\n"
        "* Option B\n\n"
        "## Decision Outcome\n\n"
        "Chosen option: Option A, because reasons.\n",
        encoding="utf-8",
    )

    report = validate_adrs(cwd=tmp_path)
    assert report.valid is True
    assert len(report.errors) == 0
    assert len(report.adrs) == 1


def test_validator_invalid_filename(tmp_path: Path) -> None:
    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True)

    (adr_dir / "bad-name.md").write_text(
        "# Bad Name\n\n## Context and Problem Statement\nC\n## Decision Outcome\nD\n",
        encoding="utf-8",
    )

    report = validate_adrs(cwd=tmp_path)
    assert report.valid is False
    assert any(
        "does not match MADR 3.0 numbering convention" in e.message
        for e in report.errors
    )


def test_validator_legacy_madr_2_aliases_rejected(tmp_path: Path) -> None:
    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True)

    (adr_dir / "0001-legacy.md").write_text(
        "# Legacy MADR 2\n\n"
        "* Status: accepted\n"
        "* Date: 2026-09-01\n\n"
        "## Context\n\n"
        "Legacy context.\n\n"
        "## Decision\n\n"
        "Legacy decision.\n",
        encoding="utf-8",
    )

    report = validate_adrs(cwd=tmp_path)
    assert report.valid is False
    assert any(
        "Obsolete MADR 2.x section '## Context' detected" in e.message
        for e in report.errors
    )
    assert any(
        "Obsolete MADR 2.x section '## Decision' detected" in e.message
        for e in report.errors
    )


def test_validator_missing_required_headings(tmp_path: Path) -> None:
    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True)

    (adr_dir / "0001-incomplete.md").write_text(
        "# Incomplete\n\n* Status: unknown_status\n\nBody without sections.\n",
        encoding="utf-8",
    )

    report = validate_adrs(cwd=tmp_path)
    assert report.valid is False
    assert any("Missing required MADR 3.0 section" in e.message for e in report.errors)
    assert any(
        "Unrecognized status 'unknown_status'" in w.message for w in report.warnings
    )
