"""Unit tests for MADR index syncer."""

from __future__ import annotations

from pathlib import Path

from mvge_runes_adr_bridge.sync import sync_adr_index


def test_sync_adr_index_missing_dir(tmp_path: Path) -> None:
    msg = sync_adr_index(cwd=tmp_path)
    assert "MISSING:" in msg


def test_sync_adr_index_success(tmp_path: Path) -> None:
    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True)

    (adr_dir / "0001-microkernel.md").write_text(
        "# Microkernel Architecture\n* Status: accepted\n* Date: 2026-09-01\n## Context and Problem Statement\nC\n## Decision Outcome\nD\n",
        encoding="utf-8",
    )
    (adr_dir / "0002-postgres.md").write_text(
        "# Postgres Persistence\n* Status: proposed\n* Date: 2026-09-02\n## Context and Problem Statement\nC\n## Decision Outcome\nD\n",
        encoding="utf-8",
    )

    msg = sync_adr_index(cwd=tmp_path)
    assert "OK: Synchronized 2 Architectural Decision Records" in msg

    readme = adr_dir / "README.md"
    assert readme.is_file()
    content = readme.read_text(encoding="utf-8")
    assert (
        "| [0001](0001-microkernel.md) | Microkernel Architecture | accepted | 2026-09-01 |"
        in content
    )
    assert (
        "| [0002](0002-postgres.md) | Postgres Persistence | proposed | 2026-09-02 |"
        in content
    )
