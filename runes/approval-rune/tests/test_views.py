"""Tests for the permissions view (GUI settings dialog data contract)."""

import asyncio
from pathlib import Path
from typing import Any

from mvge_runes_approval_rune.contracts import SpellIdentity
from mvge_runes_approval_rune.gate import ApprovalGate, PresenterResponse
from mvge_runes_approval_rune.policy import AllowRule, Constraint, DenyRule
from mvge_runes_approval_rune.views import PermissionsView


class FakeContext:
    """Fake rune context carrying a session id."""

    def __init__(self) -> None:
        """Initialize the instance."""
        self.session_id = "sess-1"


class FakeAPI:
    """Fake RuneAPI surface for factory tests."""

    def __init__(self) -> None:
        """Initialize the instance."""
        self._runner = type("R", (), {"context": FakeContext()})()
        self.install_id = "install-1"


def _identity(name: str = "write") -> SpellIdentity:
    return SpellIdentity(
        name=name,
        source_kind="builtin",
        source_id="coding_mvge",
        source_scope="agent",
        code_digest="sha256:abc",
    )


def _gate(tmp_path: Path) -> ApprovalGate:
    return ApprovalGate(api=FakeAPI(), data_dir=tmp_path)


def _seed(tmp_path: Path) -> ApprovalGate:
    gate = _gate(tmp_path)
    gate.policy.always_allow.append(
        AllowRule(
            spell=_identity(),
            constraints=[Constraint(field="path", op="exact", value="/a.txt")],
            id="allow-0",
        )
    )
    gate.policy.always_deny.append(
        DenyRule(spell=_identity("deploy"), id="deny-0")
    )
    from mvge_runes_approval_rune.policy import ProjectApproval

    gate.policy.approved_projects.append(
        ProjectApproval(root="/workspace/proj", enabled=True)
    )
    gate._store.save(gate.policy)  # noqa: SLF001
    return gate


def test_view_shape_is_plain_data(tmp_path: Path) -> None:
    """Test view shape is plain data."""
    view = PermissionsView(_seed(tmp_path)).get_permissions_view()
    assert isinstance(view["session"], dict)
    assert isinstance(view["trusted_projects"], list)
    assert isinstance(view["always_allowed"], list)
    assert isinstance(view["never_allowed"], list)
    assert isinstance(view["recent_activity"], list)
    assert isinstance(view["warnings"], list)
    assert isinstance(view["source_identity"], dict)

    allowed = view["always_allowed"][0]
    assert allowed["id"] == "allow-0"
    assert allowed["spell"]["name"] == "write"
    assert allowed["spell"]["code_digest"] == "sha256:abc"
    assert allowed["constraints"][0]["op"] == "exact"
    assert isinstance(allowed["constraint_labels"], list)

    denied = view["never_allowed"][0]
    assert denied["id"] == "deny-0"
    assert "code_digest" not in denied["spell"]

    project = view["trusted_projects"][0]
    assert project["root"] == "/workspace/proj"
    assert project["id"] == "project:/workspace/proj"


def test_view_reports_session_state(tmp_path: Path) -> None:
    """Test view reports session state."""
    gate = _seed(tmp_path)
    view = PermissionsView(gate).get_permissions_view()
    assert view["session"]["approve_all_active"] is False
    gate.approve_session("sess-1")
    view = PermissionsView(gate).get_permissions_view()
    assert view["session"]["approve_all_active"] is True
    assert view["session"]["session_id"] == "sess-1"


def test_revoke_allow_rule(tmp_path: Path) -> None:
    """Test revoke allow rule."""
    gate = _seed(tmp_path)
    view = PermissionsView(gate)
    assert view.revoke_grant("allow_rule", "allow-0") is True
    assert view.get_permissions_view()["always_allowed"] == []
    # Persisted: a fresh gate sees the revocation.
    assert (
        PermissionsView(_gate(tmp_path)).get_permissions_view()[
            "always_allowed"
        ]
        == []
    )


def test_revoke_deny_rule(tmp_path: Path) -> None:
    """Test revoke deny rule."""
    gate = _seed(tmp_path)
    view = PermissionsView(gate)
    assert view.revoke_grant("deny_rule", "deny-0") is True
    assert view.get_permissions_view()["never_allowed"] == []


def test_revoke_project(tmp_path: Path) -> None:
    """Test revoke project."""
    gate = _seed(tmp_path)
    view = PermissionsView(gate)
    assert view.revoke_grant("project", "project:/workspace/proj") is True
    assert view.get_permissions_view()["trusted_projects"] == []


def test_revoke_session_clears_approve_all(tmp_path: Path) -> None:
    """Test revoke session clears approve all."""
    gate = _seed(tmp_path)
    gate.approve_session("sess-1")
    view = PermissionsView(gate)
    assert view.revoke_grant("session", "session") is True
    assert view.get_permissions_view()["session"]["approve_all_active"] is False


def test_revoke_unknown_id_returns_false(tmp_path: Path) -> None:
    """Test revoke unknown id returns false."""
    gate = _seed(tmp_path)
    view = PermissionsView(gate)
    assert view.revoke_grant("allow_rule", "nope") is False
    assert view.revoke_grant("bogus", "allow-0") is False


def test_revocation_takes_effect_before_next_cast(tmp_path: Path) -> None:
    """Test revocation takes effect before next cast."""

    async def deny_all(request: dict[str, Any]) -> PresenterResponse:
        """Deny all."""
        return PresenterResponse(outcome="deny", scope="once")

    gate = _seed(tmp_path)
    gate._presenter = deny_all  # noqa: SLF001

    def raw() -> dict[str, Any]:
        """Raw."""
        return {
            "cast_id": "call_1",
            "spell": _identity(),
            "arguments": {"path": "/a.txt"},
            "project_root": "/workspace/other",
            "tome_id": "tome-1",
            "session_id": "sess-1",
            "is_read_only": False,
            "runner_origin": False,
            "schemaless": False,
        }

    first = asyncio.run(gate.handle(raw()))
    assert first.outcome == "allow"  # allow-0 rule matches

    view = PermissionsView(gate)
    assert view.revoke_grant("allow_rule", "allow-0") is True
    second = asyncio.run(gate.handle(raw()))
    assert second.outcome == "deny"  # rule gone, presenter denies


def test_recent_activity_lists_plain_records(tmp_path: Path) -> None:
    """Test recent activity lists plain records."""

    async def allow(request: dict[str, Any]) -> PresenterResponse:
        """Allow."""
        return PresenterResponse(outcome="allow", scope="once")

    gate = _gate(tmp_path)
    gate._presenter = allow  # noqa: SLF001
    asyncio.run(
        gate.handle(
            {
                "cast_id": "call_9",
                "spell": _identity(),
                "arguments": {"path": "/a.txt"},
                "project_root": "/workspace/proj",
                "tome_id": "tome-1",
                "session_id": "sess-1",
                "is_read_only": False,
                "runner_origin": False,
                "schemaless": False,
            }
        )
    )
    activity = PermissionsView(gate).get_permissions_view()["recent_activity"]
    assert activity
    last = activity[-1]
    assert last["cast_id"] == "call_9"
    assert last["decision"] == "allow"
    assert set(last) <= {
        "timestamp",
        "cast_id",
        "spell_name",
        "decision",
        "scope",
        "reason_code",
        "project",
    }


def test_last_used_tracked_without_policy_rewrite(tmp_path: Path) -> None:
    """Test last used tracked without policy rewrite."""
    gate = _seed(tmp_path)

    def raw() -> dict[str, Any]:
        """Raw."""
        return {
            "cast_id": "call_1",
            "spell": _identity(),
            "arguments": {"path": "/a.txt"},
            "project_root": "/workspace/other",
            "tome_id": "tome-1",
            "session_id": "sess-1",
            "is_read_only": False,
            "runner_origin": False,
            "schemaless": False,
        }

    before = (tmp_path / "policy.toml").stat().st_mtime_ns
    asyncio.run(gate.handle(raw()))
    after = (tmp_path / "policy.toml").stat().st_mtime_ns
    assert before == after, "matching a rule must not rewrite the policy file"
    view = PermissionsView(gate).get_permissions_view()
    used = view["always_allowed"][0]["last_used"]
    assert used is not None
