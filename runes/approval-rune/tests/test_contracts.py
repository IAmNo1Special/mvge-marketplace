"""Tests for the immutable gate request/decision contracts."""

from dataclasses import FrozenInstanceError

import pytest

from mvge_runes_approval_rune.contracts import (
    ApprovalDecision,
    ApprovalRequest,
    SpellIdentity,
)


def _identity() -> SpellIdentity:
    return SpellIdentity(
        name="write",
        source_kind="builtin",
        source_id="coding_mvge",
        source_scope="agent",
        code_digest="sha256:abc123",
    )


def _request() -> ApprovalRequest:
    return ApprovalRequest(
        cast_id="call_1",
        spell=_identity(),
        arguments={"path": "/tmp/a.txt"},
        argument_digest="sha256:deadbeef",
        project_root="/workspace/proj",
        tome_id="tome-1",
        session_id="sess-1",
        is_read_only=False,
        runner_origin=False,
        schemaless=False,
    )


def test_spell_identity_is_immutable() -> None:
    """Test spell identity is immutable."""
    identity = _identity()
    with pytest.raises(FrozenInstanceError):
        identity.name = "read"  # type: ignore[misc]


def test_approval_request_is_immutable() -> None:
    """Test approval request is immutable."""
    request = _request()
    with pytest.raises(FrozenInstanceError):
        request.cast_id = "call_2"  # type: ignore[misc]


def test_decision_contract_fields() -> None:
    """Test decision contract fields."""
    decision = ApprovalDecision(
        outcome="allow",
        scope="spell",
        reason_code="rule",
        request_digest="sha256:deadbeef",
        reason="matched allow rule",
        rule_id="rule-1",
    )
    assert decision.outcome == "allow"
    assert decision.scope == "spell"
    assert decision.reason_code == "rule"
    assert decision.request_digest == "sha256:deadbeef"
    assert decision.rule_id == "rule-1"


def test_decision_rejects_bad_outcome() -> None:
    """Test decision rejects bad outcome."""
    with pytest.raises(ValueError):
        ApprovalDecision(
            outcome="maybe",  # type: ignore[arg-type]
            scope="once",
            reason_code="user",
            request_digest="sha256:x",
        )


def test_decision_rejects_bad_scope() -> None:
    """Test decision rejects bad scope."""
    with pytest.raises(ValueError):
        ApprovalDecision(
            outcome="deny",
            scope="forever",  # type: ignore[arg-type]
            reason_code="user",
            request_digest="sha256:x",
        )


def test_decision_rejects_bad_reason_code() -> None:
    """Test decision rejects bad reason code."""
    with pytest.raises(ValueError):
        ApprovalDecision(
            outcome="deny",
            scope="once",
            reason_code="vibes",  # type: ignore[arg-type]
            request_digest="sha256:x",
        )


def test_stable_identity_excludes_code_digest() -> None:
    """Test stable identity excludes code digest."""
    a = _identity()
    b = SpellIdentity(
        name="write",
        source_kind="builtin",
        source_id="coding_mvge",
        source_scope="agent",
        code_digest="sha256:changed",
    )
    assert a.stable_key() == b.stable_key()
    assert a.full_key() != b.full_key()
