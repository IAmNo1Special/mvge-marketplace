"""Unit tests for MADR 3.0 scaffolding."""

from __future__ import annotations

from pathlib import Path

from mvge_runes_adr_bridge.scaffold import scaffold_new_adr, slugify


def test_slugify() -> None:
    assert slugify("Use Postgres for persistence!") == "use-postgres-for-persistence"
    assert (
        slugify("  Clean & Greenfield   Architecture  ")
        == "clean-greenfield-architecture"
    )
    assert slugify("---") == "record"


def test_scaffold_new_adr_sequential(tmp_path: Path) -> None:
    adr_dir = tmp_path / "docs" / "adr"

    # First ADR -> 0001
    f1 = scaffold_new_adr(title="First Decision", cwd=tmp_path, context="First context")
    assert f1.name == "0001-first-decision.md"
    assert f1.is_file()

    content1 = f1.read_text(encoding="utf-8")
    assert "# First Decision" in content1
    assert "## Context and Problem Statement" in content1
    assert "## Decision Outcome" in content1
    assert "First context" in content1

    # README.md auto-synced
    readme = adr_dir / "README.md"
    assert readme.is_file()
    assert "[0001](0001-first-decision.md)" in readme.read_text(encoding="utf-8")

    # Second ADR -> 0002
    f2 = scaffold_new_adr(title="Second Decision", cwd=tmp_path)
    assert f2.name == "0002-second-decision.md"
    assert f2.is_file()
    assert "[0002](0002-second-decision.md)" in readme.read_text(encoding="utf-8")
