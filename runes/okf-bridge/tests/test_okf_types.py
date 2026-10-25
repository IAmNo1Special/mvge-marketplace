"""Unit tests for OKF types, actors, and trust tiers."""

from __future__ import annotations

from pathlib import Path

from mvge_runes_okf_bridge.types import (
    AttestedComputation,
    Concept,
    Source,
    TrustTier,
    UsageWindow,
    ValidationReport,
)


def test_trust_tier_derivation() -> None:
    # 1. No verified -> UNVERIFIED
    c1 = Concept(id="test/c1", path=Path("c1.md"), type="Metric")
    assert c1.trust_tier == TrustTier.UNVERIFIED

    # 2. Verified with human -> HUMAN_REVIEWED
    c2 = Concept(
        id="test/c2",
        path=Path("c2.md"),
        type="Metric",
        verified=[{"by": "human:alice", "at": "2026-09-01"}],
    )
    assert c2.trust_tier == TrustTier.HUMAN_REVIEWED

    # 3. Verified with process/agent -> MACHINE_CONFIRMED
    c3 = Concept(
        id="test/c3",
        path=Path("c3.md"),
        type="Metric",
        verified=[{"by": "process:ci-checker", "at": "2026-09-01"}],
    )
    assert c3.trust_tier == TrustTier.MACHINE_CONFIRMED

    # 4. Near-miss Human:alice (capitalized) does NOT qualify as human-reviewed (§5.3)
    c4 = Concept(
        id="test/c4",
        path=Path("c4.md"),
        type="Metric",
        verified=[{"by": "Human:alice", "at": "2026-09-01"}],
    )
    assert c4.trust_tier == TrustTier.MACHINE_CONFIRMED


def test_concept_staleness() -> None:
    # No stale_after -> False
    c1 = Concept(id="c1", path=Path("c1.md"), type="Metric")
    assert c1.is_stale is False

    # Past date -> True
    c2 = Concept(id="c2", path=Path("c2.md"), type="Metric", stale_after="2020-01-01")
    assert c2.is_stale is True

    # Future date -> False
    c3 = Concept(id="c3", path=Path("c3.md"), type="Metric", stale_after="2099-01-01")
    assert c3.is_stale is False

    # Corrupt date string -> False
    c4 = Concept(id="c4", path=Path("c4.md"), type="Metric", stale_after="not-a-date")
    assert c4.is_stale is False


def test_validation_report_helpers() -> None:
    report = ValidationReport(valid=True)
    assert report.valid is True
    assert len(report.errors) == 0
    assert len(report.warnings) == 0

    report.add_warning("c1.md", "Soft warning")
    assert report.valid is True
    assert len(report.warnings) == 1

    report.add_error("c2.md", "Hard error")
    assert report.valid is False
    assert len(report.errors) == 1


def test_source_and_attestation_dataclasses() -> None:
    src = Source(
        id="s1", resource="https://example.com/api", title="Example", author="human:dan"
    )
    assert src.id == "s1"
    assert src.author == "human:dan"

    window = UsageWindow(from_date="2026-01-01", to_date="2026-06-01")
    assert window.from_date == "2026-01-01"

    att = AttestedComputation(runtime="python", computation="print('hello')")
    assert att.runtime == "python"
