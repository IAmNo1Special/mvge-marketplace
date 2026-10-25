"""Unit tests for MADR 3.0 parser."""

from __future__ import annotations

from pathlib import Path

from mvge_runes_adr_bridge.parser import (
    load_adrs,
    parse_adr_file,
    resolve_adr_dir,
)


def test_resolve_adr_dir_empty(tmp_path: Path) -> None:
    assert resolve_adr_dir(cwd=tmp_path) is None


def test_resolve_adr_dir_docs_adr(tmp_path: Path) -> None:
    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    assert resolve_adr_dir(cwd=tmp_path) == adr_dir


def test_resolve_adr_dir_fallback_adr(tmp_path: Path) -> None:
    adr_dir = tmp_path / "adr"
    adr_dir.mkdir()
    assert resolve_adr_dir(cwd=tmp_path) == adr_dir


def test_parse_adr_file_valid(tmp_path: Path) -> None:
    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True)

    adr_file = adr_dir / "0001-use-postgres.md"
    adr_file.write_text(
        "# Use Postgres for Persistence\n\n"
        "* Status: accepted\n"
        "* Date: 2026-09-01\n"
        "* Deciders: backend-team\n\n"
        "## Context and Problem Statement\n\n"
        "We need reliable relational storage.\n\n"
        "## Decision Drivers\n\n"
        "* ACID compliance\n"
        "* JSON support\n\n"
        "## Considered Options\n\n"
        "* PostgreSQL\n"
        "* MongoDB\n\n"
        "## Decision Outcome\n\n"
        "Chosen option: PostgreSQL, because robust ecosystem.\n\n"
        "## Pros and Cons of the Options\n\n"
        "### PostgreSQL\n\n"
        "* Good, because rock solid\n"
        "* Bad, because connection scaling\n\n"
        "## Positive Consequences\n\n"
        "* Reliable data transactions\n",
        encoding="utf-8",
    )

    entry = parse_adr_file(adr_file)
    assert entry is not None
    assert entry.number == 1
    assert entry.title == "Use Postgres for Persistence"
    assert entry.status == "accepted"
    assert entry.date == "2026-09-01"
    assert entry.deciders == "backend-team"
    assert "reliable relational storage" in entry.context_and_problem_statement
    assert "Chosen option: PostgreSQL" in entry.decision_outcome
    assert "PostgreSQL" in entry.considered_options
    assert "MongoDB" in entry.considered_options
    assert "Reliable data transactions" in entry.consequences


def test_parse_adr_file_invalid_name(tmp_path: Path) -> None:
    bad_file = tmp_path / "not-an-adr.md"
    bad_file.write_text("# Invalid", encoding="utf-8")
    assert parse_adr_file(bad_file) is None


def test_load_adrs(tmp_path: Path) -> None:
    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True)

    (adr_dir / "0002-second.md").write_text(
        "# Second\n* Status: proposed\n## Context and Problem Statement\nC\n## Decision Outcome\nD\n",
        encoding="utf-8",
    )
    (adr_dir / "0001-first.md").write_text(
        "# First\n* Status: accepted\n## Context and Problem Statement\nC\n## Decision Outcome\nD\n",
        encoding="utf-8",
    )
    (adr_dir / "README.md").write_text("# Index", encoding="utf-8")

    adrs = load_adrs(cwd=tmp_path)
    assert len(adrs) == 2
    # Ensure sorted by number
    assert adrs[0].number == 1
    assert adrs[1].number == 2
