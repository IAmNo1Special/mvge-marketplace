"""Unit tests for MADR 3.0 types and reports."""

from __future__ import annotations

from pathlib import Path

from mvge_runes_adr_bridge.types import (
    ADREntry,
    ADRStatus,
    ADRValidationReport,
)


def test_adr_status_values() -> None:
    assert ADRStatus.PROPOSED == "proposed"
    assert ADRStatus.ACCEPTED == "accepted"
    assert ADRStatus.REJECTED == "rejected"
    assert ADRStatus.SUPERSEDED == "superseded"
    assert ADRStatus.DEPRECATED == "deprecated"


def test_adr_entry_creation() -> None:
    entry = ADREntry(
        number=1,
        title="Microkernel Architecture",
        status="accepted",
        date="2026-09-01",
        deciders="core-team",
        context_and_problem_statement="Need clean boundaries.",
        decision_outcome="Chosen option: microkernel.",
        considered_options=["Monolith", "Microkernel"],
        consequences=["High modularity", "Isolated runes"],
        path=Path("docs/adr/0001-microkernel.md"),
    )
    assert entry.number == 1
    assert entry.title == "Microkernel Architecture"
    assert len(entry.considered_options) == 2
    assert len(entry.consequences) == 2


def test_adr_validation_report_helpers() -> None:
    report = ADRValidationReport(valid=True)
    assert report.valid is True
    assert len(report.errors) == 0
    assert len(report.warnings) == 0

    report.add_warning("0001-test.md", "Missing options")
    assert report.valid is True
    assert len(report.warnings) == 1

    report.add_error("0002-test.md", "Missing decision outcome")
    assert report.valid is False
    assert len(report.errors) == 1
