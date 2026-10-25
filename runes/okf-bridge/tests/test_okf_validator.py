"""Unit tests for OKF v0.2 §11 deterministic validation."""

from __future__ import annotations

from pathlib import Path

from mvge_runes_okf_bridge.validator import validate_okf_bundle


def test_validator_missing_directory(tmp_path: Path) -> None:
    non_existent = tmp_path / "does-not-exist"
    report = validate_okf_bundle(non_existent)
    assert report.valid is False
    assert any("not found" in e.message for e in report.errors)


def test_validator_empty_bundle(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty_bundle"
    empty_dir.mkdir()
    report = validate_okf_bundle(empty_dir)
    assert report.valid is True
    assert any("0 concepts" in w.message for w in report.warnings)


def test_validator_valid_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / ".okf"
    bundle.mkdir()

    (bundle / "index.md").write_text(
        '---\nokf_version: "0.2"\n---\n# Root\n* [C1](c1.md)\n', encoding="utf-8"
    )
    (bundle / "log.md").write_text(
        "# Log\n\n## 2026-09-01\n* Created bundle.\n", encoding="utf-8"
    )
    (bundle / "c1.md").write_text(
        "---\n"
        "type: Table\n"
        "title: Users\n"
        "description: User database table\n"
        "tags: [users, db]\n"
        "sources:\n"
        "  - id: postgres-schema\n"
        "    resource: db/schema.sql\n"
        "---\n"
        "# Schema\nUser table schema.[^postgres-schema]\n",
        encoding="utf-8",
    )

    report = validate_okf_bundle(bundle)
    assert report.valid is True
    assert len(report.errors) == 0
    assert report.concepts == 1
    assert report.indexes == 1
    assert report.logs == 1


def test_validator_hard_error_missing_type(tmp_path: Path) -> None:
    bundle = tmp_path / ".okf"
    bundle.mkdir()

    (bundle / "bad.md").write_text(
        "---\ntitle: Bad Concept\n---\nBody", encoding="utf-8"
    )
    report = validate_okf_bundle(bundle)
    assert report.valid is False
    assert any(
        "Missing or empty required field 'type'" in e.message for e in report.errors
    )


def test_validator_actor_near_miss_and_footnotes(tmp_path: Path) -> None:
    bundle = tmp_path / ".okf"
    bundle.mkdir()

    (bundle / "concept.md").write_text(
        "---\n"
        "type: Metric\n"
        "title: Active Users\n"
        "description: Daily active users\n"
        "tags: [metrics]\n"
        "verified:\n"
        "  - by: Human:alice\n"
        "sources:\n"
        "  - id: src-1\n"
        "    resource: https://analytics.example.com\n"
        "    usage_count: 10\n"
        "---\n"
        "Body referencing [^missing-src] footnote.\n",
        encoding="utf-8",
    )

    report = validate_okf_bundle(bundle)
    assert report.valid is True
    assert any("Actor near-miss" in w.message for w in report.warnings)
    assert any("Footnote '[^missing-src]'" in w.message for w in report.warnings)
    assert any("usage_window" in w.message for w in report.warnings)


def test_validator_strict_mode(tmp_path: Path) -> None:
    bundle = tmp_path / ".okf"
    bundle.mkdir()
    # Concept missing recommended description and tags produces warnings
    (bundle / "c.md").write_text(
        "---\ntype: Guide\ntitle: Minimal\n---\nBody", encoding="utf-8"
    )

    normal_report = validate_okf_bundle(bundle, strict=False)
    assert normal_report.valid is True
    assert len(normal_report.warnings) > 0

    strict_report = validate_okf_bundle(bundle, strict=True)
    assert strict_report.valid is False


def test_validator_index_and_log_warnings(tmp_path: Path) -> None:
    bundle = tmp_path / ".okf"
    bundle.mkdir()
    sub = bundle / "sub"
    sub.mkdir()

    # Root index with non-0.2 version
    (bundle / "index.md").write_text(
        '---\nokf_version: "0.1"\n---\n# Root\n', encoding="utf-8"
    )

    # Subdirectory index with frontmatter (forbidden per §8)
    (sub / "index.md").write_text("---\ntitle: Sub\n---\n# Sub\n", encoding="utf-8")

    # Log file with frontmatter and non-ISO dates
    (bundle / "log.md").write_text("---\n---\n# Log without dates\n", encoding="utf-8")

    # Attested computation without runtime
    (bundle / "comp.md").write_text(
        "---\n"
        "type: Attested Computation\n"
        "title: Computation Without Runtime\n"
        "description: Missing runtime\n"
        "tags: [calc]\n"
        "---\n\n"
        "# Computation\nBody\n",
        encoding="utf-8",
    )

    report = validate_okf_bundle(bundle)
    assert report.valid is True
    assert any("declares target version" in w.message for w in report.warnings)
    assert any(
        "Only bundle-root index.md may contain frontmatter" in w.message
        for w in report.warnings
    )
    assert any(
        "log.md must not contain frontmatter" in w.message for w in report.warnings
    )
    assert any("log.md should contain ISO-8601" in w.message for w in report.warnings)
    assert any("missing required 'runtime'" in w.message for w in report.warnings)


def test_validator_legacy_warnings_and_max_warnings(tmp_path: Path) -> None:
    bundle = tmp_path / ".okf"
    bundle.mkdir()

    (bundle / "legacy_concept.md").write_text(
        "---\n"
        "type: Table\n"
        "title: Legacy Table\n"
        "description: Table\n"
        "tags: [data]\n"
        "timestamp: 2025-01-01\n"
        "---\n\n"
        "# Schema\n\nBody\n\n"
        "## Citations\n* [Source](https://example.com)\n",
        encoding="utf-8",
    )

    report = validate_okf_bundle(bundle, max_warnings=0)
    assert report.valid is False
    assert any(
        "Legacy 'timestamp' field detected" in w.message for w in report.warnings
    )
    assert any(
        "Legacy '# Citations' section detected" in w.message for w in report.warnings
    )


def _bundle_with_concept(tmp_path: Path, name: str, frontmatter: str) -> Path:
    bundle = tmp_path / name
    bundle.mkdir()
    (bundle / "c1.md").write_text(
        f"---\ntype: Note\ntitle: C1\n{frontmatter}---\n# C1\n", encoding="utf-8"
    )
    return bundle


def test_validator_dangling_resource_warns(tmp_path: Path) -> None:
    bundle = _bundle_with_concept(
        tmp_path, "b1", 'resource: "references/missing.png"\n'
    )
    report = validate_okf_bundle(bundle)
    assert report.valid is True  # warnings only: consumers tolerate broken links
    assert any(
        "missing.png" in w.message and "resource" in w.message.lower()
        for w in report.warnings
    )


def test_validator_existing_resource_no_warning(tmp_path: Path) -> None:
    bundle = _bundle_with_concept(tmp_path, "b2", 'resource: "references/here.png"\n')
    (bundle / "references").mkdir()
    (bundle / "references" / "here.png").write_bytes(b"fake-png")
    report = validate_okf_bundle(bundle)
    assert not any("resource" in w.message for w in report.warnings)


def test_validator_dangling_source_resource_warns(tmp_path: Path) -> None:
    bundle = _bundle_with_concept(
        tmp_path,
        "b3",
        "sources:\n  - id: s1\n    resource: references/gone.pdf\n",
    )
    report = validate_okf_bundle(bundle)
    assert report.valid is True
    assert any("gone.pdf" in w.message for w in report.warnings)


def test_validator_external_url_resource_no_warning(tmp_path: Path) -> None:
    bundle = _bundle_with_concept(
        tmp_path, "b4", 'resource: "https://example.com/x.png"\n'
    )
    report = validate_okf_bundle(bundle)
    assert not any("resource" in w.message for w in report.warnings)
