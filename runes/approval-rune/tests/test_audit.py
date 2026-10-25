"""Tests for the durable audit log."""

import json
import stat
from pathlib import Path

from mvge_runes_approval_rune.audit import AuditError, AuditLog


def _record(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "timestamp": "2026-09-20T15:40:11Z",
        "cast_id": "call_1",
        "spell": {"name": "write", "source_id": "coding_mvge"},
        "project": "/workspace/proj",
        "tome_id": "tome-1",
        "argument_digest": "sha256:abc",
        "decision": "allow",
        "scope": "once",
        "execution": "started",
    }
    base.update(overrides)
    return base


def test_append_creates_user_only_file(tmp_path: Path) -> None:
    """Test append creates user only file."""
    log = AuditLog(tmp_path)
    log.append_decision(_record())
    audit_file = tmp_path / "audit.jsonl"
    assert audit_file.exists()
    assert stat.S_IMODE(audit_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700


def test_append_writes_one_json_line_per_record(tmp_path: Path) -> None:
    """Test append writes one json line per record."""
    log = AuditLog(tmp_path)
    log.append_decision(_record(cast_id="call_1"))
    log.append_outcome(cast_id="call_1", event="completed")
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["cast_id"] == "call_1"
    assert json.loads(lines[1])["execution"] == "completed"


def test_denied_cast_gets_end_event_without_start(tmp_path: Path) -> None:
    """Test denied cast gets end event without start."""
    log = AuditLog(tmp_path)
    log.append_decision(_record(decision="deny", execution="denied"))
    records = log.recent(10)
    assert len(records) == 1
    assert records[0]["execution"] == "denied"
    assert "started" not in {r["execution"] for r in records}


def test_audit_failure_raises(tmp_path: Path) -> None:
    """Test audit failure raises."""
    # Make the parent unusable: replace with a file so mkdir fails.
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    bad = AuditLog(blocker / "sub")
    try:
        bad.append_decision(_record())
    except AuditError:
        pass
    else:
        raise AssertionError("expected AuditError from unwritable audit dir")


def test_rotation_by_size(tmp_path: Path) -> None:
    """Test rotation by size."""
    log = AuditLog(tmp_path, max_bytes=400)
    for i in range(20):
        log.append_decision(_record(cast_id=f"call_{i}"))
    rotated = list(tmp_path.glob("audit-*.jsonl"))
    assert rotated, "expected a rotated archive"
    main_lines = (
        (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    )
    assert main_lines, "main log must keep recent records"


def test_rotation_by_age(tmp_path: Path) -> None:
    """Test rotation by age."""
    log = AuditLog(tmp_path, max_age_days=0)
    log.append_decision(_record())
    # Backdate the file so it exceeds the age limit.
    import os
    import time

    old = time.time() - 60
    os.utime(tmp_path / "audit.jsonl", (old, old))
    log.append_decision(_record(cast_id="call_2"))
    assert list(tmp_path.glob("audit-*.jsonl")), "expected rotation by age"


def test_recent_reads_newest_last(tmp_path: Path) -> None:
    """Test recent reads newest last."""
    log = AuditLog(tmp_path)
    for i in range(5):
        log.append_decision(_record(cast_id=f"call_{i}"))
    recent = log.recent(2)
    assert [r["cast_id"] for r in recent] == ["call_3", "call_4"]


def test_rotation_never_overwrites_existing_archive(tmp_path: Path) -> None:
    """Test rotation never overwrites existing archive."""
    log = AuditLog(tmp_path, max_bytes=1)
    log.append_decision(_record(cast_id="call_1"))
    log.append_decision(_record(cast_id="call_2"))
    log.append_decision(_record(cast_id="call_3"))

    archives = sorted(tmp_path.glob("audit-*.jsonl"))
    assert len(archives) == 2
    cast_ids = [
        json.loads(line)["cast_id"]
        for archive in archives
        for line in archive.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert sorted(cast_ids) == ["call_1", "call_2"]
