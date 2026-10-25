"""Defensive audit and validation branches: non-dict results, old hosts
without the rune-op audit API, audit-write crashes, and symlink escapes.

These are the fail-loud edges of the audit trail — a gap here would turn
a loud failure into a silent lie.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from mvge.runes import AuditError
from selfmod_bridge_conftest import make_rune

from mvge_runes_selfmod_bridge.rune import SelfmodBridgeRune
from mvge_runes_selfmod_bridge.validation import ValidationError, validate_extension_name


async def _weird_result(params: dict[str, Any], signal: Any = None, on_update: Any = None) -> Any:
    return ["not", "a", "dict"]


async def _boom(params: dict[str, Any], signal: Any = None, on_update: Any = None) -> Any:
    raise RuntimeError("kaboom")


def test_non_dict_result_skips_audit_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A handler returning a non-dict cannot be audit-stamped: the audit is
    skipped with a warning and the raw result passes through unchanged."""
    rune, api = make_rune(tmp_path)

    result = asyncio.run(rune._with_audit("teach", _weird_result)({}))

    assert result == ["not", "a", "dict"]
    assert api.audit_records == []
    assert "skipping audit" in caplog.text


def test_audit_write_failure_ok_branch_carries_only_present_keys(tmp_path: Path) -> None:
    """On the success path, only keys the live result actually carried are
    preserved — missing ones must not fabricate entries."""
    rune, _api = make_rune(tmp_path)
    exc = AuditError("audit append failed: disk gone")

    shaped = rune._audit_write_failure(
        "self_snapshot", {"ok": True, "effective_after": "reload"}, True, "ok", "done", exc
    )

    assert shaped["ok"] is False
    assert shaped["error"] == "audit_failed"
    assert "IS live" in shaped["message"]
    assert shaped["effective_after"] == "reload"
    assert "note" not in shaped
    assert "path" not in shaped
    assert "snapshot_id" not in shaped


def test_audit_write_failure_failure_branch_carries_only_present_keys(tmp_path: Path) -> None:
    """On the failure path, the original code/message survive and only the
    keys the failed result carried are preserved."""
    rune, _api = make_rune(tmp_path)
    exc = AuditError("audit append failed: disk gone")

    shaped = rune._audit_write_failure(
        "teach", {"ok": False, "note": "n"}, False, "invalid_mode", "bad mode", exc
    )

    assert shaped["ok"] is False
    assert shaped["error"] == "audit_failed"
    assert "invalid_mode" in shaped["message"]
    assert "bad mode" in shaped["message"]
    assert "disk gone" in shaped["message"]
    assert shaped["note"] == "n"
    assert "effective_after" not in shaped


def test_audit_crash_without_audit_method_logs_and_reraises(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Old hosts predate the rune-op audit API: a crashed op still warns
    loudly and the original exception propagates."""

    class OldHostApi:
        pass

    state_rune, _api = make_rune(tmp_path)
    old_host_rune = SelfmodBridgeRune(OldHostApi(), {"version": "0.1.0"})
    old_host_rune.state = state_rune.state

    with pytest.raises(RuntimeError, match="kaboom"):
        asyncio.run(old_host_rune._with_audit("teach", _boom)({"section": "x"}))

    assert "unaudited" in caplog.text.lower()


def test_audit_crash_write_failure_never_masks(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """If the crash audit itself fails to write, it is logged — the
    original exception still propagates."""
    rune, _api = make_rune(tmp_path)
    _api.audit_error = RuntimeError("log disk gone")

    with pytest.raises(RuntimeError, match="kaboom"):
        asyncio.run(rune._with_audit("teach", _boom)({"section": "x"}))

    assert "audit write failed for crashed op teach" in caplog.text


def test_validate_extension_name_rejects_symlink_escape(tmp_path: Path) -> None:
    """A valid-identifier name that resolves outside the target dir (via a
    symlink) is rejected — fail closed."""
    target = tmp_path / "spells"
    target.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (target / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValidationError) as exc_info:
        validate_extension_name("link", target)

    assert exc_info.value.code == "invalid_name"
