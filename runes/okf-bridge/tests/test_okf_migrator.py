"""Unit tests for OKF v0.1 to v0.2 migration."""

from __future__ import annotations

from pathlib import Path

from mvge_runes_okf_bridge.migrator import migrate_bundle_in_place


def test_migrate_bundle_in_place(tmp_path: Path) -> None:
    bundle = tmp_path / ".okf"
    bundle.mkdir()

    # Legacy v0.1 root index
    (bundle / "index.md").write_text(
        '---\nokf_version: "0.1"\n---\n# Root\n', encoding="utf-8"
    )

    # Legacy v0.1 concept with timestamp and # Citations
    (bundle / "legacy.md").write_text(
        "---\n"
        "type: Metric\n"
        "title: Revenue\n"
        "timestamp: 2025-01-01\n"
        "---\n\n"
        "# Summary\n\nRevenue details.\n\n"
        "### Citations\n\n"
        "* [Stripe API](https://api.stripe.com)\n"
        "* [Internal Ledger](https://ledger.internal)\n",
        encoding="utf-8",
    )

    modified = migrate_bundle_in_place(bundle)
    assert len(modified) == 2
    assert "index.md" in modified
    assert "legacy.md" in modified

    # Check migrated contents
    new_index = (bundle / "index.md").read_text(encoding="utf-8")
    assert 'okf_version: "0.2"' in new_index

    new_concept = (bundle / "legacy.md").read_text(encoding="utf-8")
    assert "timestamp:" not in new_concept
    assert "generated:" in new_concept
    assert "process:okf-migrate" in new_concept
    assert "sources:" in new_concept
    assert "stripe-api" in new_concept
    assert "### Citations" not in new_concept

    # Second run should do nothing
    second_run = migrate_bundle_in_place(bundle)
    assert len(second_run) == 0
