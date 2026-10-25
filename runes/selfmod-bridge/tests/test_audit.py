"""Audit trail: every mutating op emits one engine-stamped audit event.

Successes and structured failures (snapshot_failed included) are recorded;
an audit write failure surfaces loudly as ``audit_failed`` without ever
lying about what the mutation did.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from mvge.runes import AuditError
from selfmod_bridge_conftest import make_rune

from mvgeos_runes_selfmod_bridge.rune import SelfmodBridgeRune

# (op, params): params chosen to fail fast without mutating (self_snapshot
# succeeds — its snapshots land in the tmp state dir).
AUDITED_OPS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("scaffold_spell", {}),
    ("scaffold_rune", {}),
    ("scaffold_skill", {}),
    ("revise_persona", {}),
    ("teach", {"section": "x", "mode": "bogus"}),
    ("self_snapshot", {}),
    ("self_rollback", {}),
)


def call(rune: SelfmodBridgeRune, name: str, params: dict[str, Any]) -> Any:
    """Invoke a spell through the real engine dispatch path."""
    defs = {d.name: d for d in rune._spell_definitions()}
    return asyncio.run(defs[name].execute("test-cast", params))


def test_every_mutating_op_emits_exactly_one_audit_event(
    tmp_path: Path,
) -> None:
    rune, api = make_rune(tmp_path)

    for name, params in AUDITED_OPS:
        call(rune, name, params)

    assert [r["op"] for r in api.audit_records] == [name for name, _ in AUDITED_OPS]


def test_read_only_status_op_is_not_audited(tmp_path: Path) -> None:
    rune, api = make_rune(tmp_path)

    result = call(rune, "extension_status", {})

    assert result["ok"] is True
    assert api.audit_records == []


def test_successful_op_audits_ok_with_target(tmp_path: Path) -> None:
    rune, api = make_rune(tmp_path)

    result = call(
        rune,
        "revise_persona",
        {"old_text": "You are a test agent.", "new_text": "You are a test agent!"},
    )

    assert result["ok"] is True
    (record,) = api.audit_records
    assert record["op"] == "revise_persona"
    assert record["outcome"] == "ok"
    assert record["code"] == "ok"
    assert record["target"] is not None and "SYSTEM.md" in record["target"]
    assert record["message"]


def test_structured_failure_audits_with_op_code(tmp_path: Path) -> None:
    rune, api = make_rune(tmp_path)

    result = call(rune, "teach", {"section": "x", "mode": "bogus"})

    assert result["ok"] is False
    assert result["error"] == "invalid_mode"
    (record,) = api.audit_records
    assert record["op"] == "teach"
    assert record["outcome"] == "failed"
    assert record["code"] == "invalid_mode"
    assert "invalid mode" in record["message"]


def test_snapshot_failed_is_audited(tmp_path: Path) -> None:
    rune, api = make_rune(tmp_path)

    async def broken_snapshot(label: str | None) -> dict[str, Any]:
        return {
            "ok": False,
            "error": "snapshot_failed",
            "message": "snapshot failed during copy: disk gone",
        }

    rune._snapshot_self = broken_snapshot  # type: ignore[method-assign]
    result = call(rune, "teach", {"section": "Notes", "mode": "append", "text": "hello"})

    assert result["error"] == "snapshot_failed"
    (record,) = api.audit_records
    assert record["outcome"] == "failed"
    assert record["code"] == "snapshot_failed"
    assert "disk gone" in record["message"]


def test_self_snapshot_success_audits_snapshot_id_as_target(
    tmp_path: Path,
) -> None:
    rune, api = make_rune(tmp_path)

    result = call(rune, "self_snapshot", {})

    assert result["ok"] is True
    (record,) = api.audit_records
    assert record["op"] == "self_snapshot"
    assert record["outcome"] == "ok"
    assert record["target"] == result["path"]
    assert record["extra"] == {"snapshot_id": result["snapshot_id"]}


def test_audit_write_failure_on_success_surfaces_audit_failed_truthfully(
    tmp_path: Path,
) -> None:
    rune, api = make_rune(tmp_path)
    api.audit_error = AuditError("audit append failed: disk gone")

    result = call(
        rune,
        "revise_persona",
        {"old_text": "You are a test agent.", "new_text": "You are a test agent!"},
    )

    # Loud, not silent — and never the lie that nothing happened.
    assert result["ok"] is False
    assert result["error"] == "audit_failed"
    assert "IS live" in result["message"]
    assert "disk gone" in result["message"]
    # The change happened: reload-staleness semantics are preserved.
    assert result["effective_after"] == "reload"
    # The persona file really was patched.
    system = tmp_path / "agent-config" / "SYSTEM.md"
    assert "You are a test agent!" in system.read_text(encoding="utf-8")


def test_audit_write_failure_on_failure_preserves_original_code(
    tmp_path: Path,
) -> None:
    rune, api = make_rune(tmp_path)
    api.audit_error = AuditError("audit append failed: disk gone")

    result = call(rune, "teach", {"section": "x", "mode": "bogus"})

    assert result["ok"] is False
    assert result["error"] == "audit_failed"
    assert "invalid_mode" in result["message"]
    assert "disk gone" in result["message"]


def test_crashing_handler_audits_exception_then_reraises(
    tmp_path: Path,
) -> None:
    rune, api = make_rune(tmp_path)

    async def boom(
        params: dict[str, Any], signal: Any = None, on_update: Any = None
    ) -> dict[str, Any]:
        raise RuntimeError("kaboom")

    wrapped = rune._with_audit("teach", boom)
    with pytest.raises(RuntimeError, match="kaboom"):
        asyncio.run(wrapped({"section": "x"}))

    (record,) = api.audit_records
    assert record["op"] == "teach"
    assert record["outcome"] == "failed"
    assert record["code"] == "exception"
    assert "kaboom" in record["message"]


def test_missing_audit_method_warns_and_returns_result_unchanged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    class OldHostApi:
        pass

    state_rune, _ = make_rune(tmp_path)
    old_host_rune = SelfmodBridgeRune(OldHostApi(), {"version": "0.1.0"})
    old_host_rune.state = state_rune.state

    result = asyncio.run(
        old_host_rune._with_audit("teach", old_host_rune.teach)({"section": "x", "mode": "bogus"})
    )

    assert result["error"] == "invalid_mode"
    assert "audit" in caplog.text.lower()
