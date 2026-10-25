"""Tests for the critical spell gate."""

import asyncio
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

from mvge.core.approval import (
    ApprovalDecision as CoreApprovalDecision,
)
from mvge.core.approval import (
    ApprovalOutcome,
)
from mvge.core.approval import (
    ApprovalRequest as CoreApprovalRequest,
)
from mvge.core.approval import (
    allow as core_allow,
)
from mvge.core.approval import (
    deny as core_deny,
)

from mvge_runes_approval_rune.contracts import (
    SpellIdentity,
)
from mvge_runes_approval_rune.gate import (
    ApprovalGate,
    PresenterResponse,
    build_request,
    from_core_request,
)
from mvge_runes_approval_rune.normalization import (
    argument_summary,
    digest_arguments,
    redact,
)


class FakeContext:
    """Fake rune context carrying a session id."""

    def __init__(self, session_id: str = "sess-1") -> None:
        """Initialize the instance."""
        self.session_id = session_id


class FakeRunner:
    """Fake runner exposing a context."""

    def __init__(self, session_id: str = "sess-1") -> None:
        """Initialize the instance."""
        self.context = FakeContext(session_id)


class FakeAPI:
    """Fake RuneAPI surface for factory tests."""

    def __init__(self, session_id: str = "sess-1") -> None:
        """Initialize the instance."""
        self._runner = FakeRunner(session_id)
        self.install_id = "install-1"


def _identity(name: str = "write") -> SpellIdentity:
    return SpellIdentity(
        name=name,
        source_kind="builtin",
        source_id="coding_mvge",
        source_scope="agent",
        code_digest="sha256:abc",
    )


def _raw(
    name: str = "write",
    args: Any = None,
    session_id: str = "sess-1",
    **kwargs: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "cast_id": "call_1",
        "spell": _identity(name),
        "arguments": {"path": "/workspace/proj/a.txt"}
        if args is None
        else args,
        "project_root": "/workspace/proj",
        "tome_id": "tome-1",
        "session_id": session_id,
        "is_read_only": False,
        "runner_origin": False,
        "schemaless": False,
    }
    payload.update(kwargs)
    return payload


def _gate(tmp_path: Path, **kwargs: Any) -> ApprovalGate:
    return ApprovalGate(api=FakeAPI(), data_dir=tmp_path, **kwargs)


async def _allow_once(request: dict[str, Any]) -> PresenterResponse:
    return PresenterResponse(outcome="allow", scope="once")


def test_build_request_freezes_arguments() -> None:
    """Test build request freezes arguments."""
    args = {"path": "/tmp/a.txt", "nested": {"n": 1}}
    request = build_request(_raw(args=args))
    assert request.arguments == args
    assert request.arguments is not args
    assert request.argument_digest == digest_arguments(args)


def test_non_dict_arguments_deny_before_gate(tmp_path: Path) -> None:
    """Test non dict arguments deny before gate."""
    gate = _gate(tmp_path, presenter=_allow_once)
    decision = asyncio.run(gate.handle(_raw(args=["not", "a", "dict"])))
    assert decision.outcome == "deny"
    assert decision.reason_code == "failure"


def test_non_serializable_arguments_deny(tmp_path: Path) -> None:
    """Test non serializable arguments deny."""
    gate = _gate(tmp_path, presenter=_allow_once)
    decision = asyncio.run(gate.handle(_raw(args={"fn": object()})))
    assert decision.outcome == "deny"
    assert decision.reason_code == "failure"


def test_install_mismatch_appends_audit_event(tmp_path: Path) -> None:
    """Test install mismatch appends audit event."""
    (tmp_path / "policy.toml").write_text(
        'schema_version = 1\ninstall_id = "other-install"\n', encoding="utf-8"
    )
    gate = _gate(tmp_path)
    assert gate.policy.install_mismatch
    events = [
        r
        for r in gate.audit.recent(10)
        if r.get("event") == "policy_ignored_install_mismatch"
    ]
    assert events, "mismatch must leave an audit trail"


def test_deny_rule_denies_without_prompt(tmp_path: Path) -> None:
    """Test deny rule denies without prompt."""

    async def must_not_prompt(request: dict[str, Any]) -> PresenterResponse:
        """Must not prompt."""
        raise AssertionError("presenter must not be called")

    gate = _gate(tmp_path, presenter=must_not_prompt)
    from mvge_runes_approval_rune.policy import DenyRule

    gate.policy.always_deny.append(DenyRule(spell=_identity(), id="deny-0"))
    gate._store.save(gate.policy)  # noqa: SLF001 - persist like the GUI would
    decision = asyncio.run(gate.handle(_raw()))
    assert decision.outcome == "deny"
    assert decision.reason_code == "rule"


def test_prompt_allow_once_executes_exactly_that_cast(tmp_path: Path) -> None:
    """Test prompt allow once executes exactly that cast."""
    gate = _gate(tmp_path, presenter=_allow_once)
    decision = asyncio.run(gate.handle(_raw()))
    assert decision.outcome == "allow"
    assert decision.scope == "once"
    assert decision.request_digest == digest_arguments(
        {"path": "/workspace/proj/a.txt"}
    )


def test_presenter_deny_suppresses_identical_recast(tmp_path: Path) -> None:
    """Test presenter deny suppresses identical recast."""
    calls: list[dict[str, Any]] = []

    async def deny_all(request: dict[str, Any]) -> PresenterResponse:
        """Deny all."""
        calls.append(request)
        return PresenterResponse(outcome="deny", scope="once")

    gate = _gate(tmp_path, presenter=deny_all)
    first = asyncio.run(gate.handle(_raw()))
    assert first.outcome == "deny"
    second = asyncio.run(gate.handle(_raw()))
    assert second.outcome == "deny"
    assert len(calls) == 1, "identical re-cast must not re-prompt"
    # The canonical decision carries no free-text reason; the suppressed
    # re-cast is recorded in the audit log with the same reason code.
    records = gate.audit.recent(10)
    assert any(
        record.get("decision") == "deny"
        and record.get("reason_code") == "user"
        and record.get("cast_id") == "call_1"
        for record in records
    )


def test_different_arguments_reprompt(tmp_path: Path) -> None:
    """Test different arguments reprompt."""
    calls = 0

    async def deny_all(request: dict[str, Any]) -> PresenterResponse:
        """Deny all."""
        nonlocal calls
        calls += 1
        return PresenterResponse(outcome="deny", scope="once")

    gate = _gate(tmp_path, presenter=deny_all)
    asyncio.run(gate.handle(_raw(args={"path": "/workspace/proj/a.txt"})))
    asyncio.run(gate.handle(_raw(args={"path": "/workspace/proj/b.txt"})))
    assert calls == 2


def test_no_presenter_denies(tmp_path: Path) -> None:
    """Test no presenter denies."""
    gate = _gate(tmp_path)
    decision = asyncio.run(gate.handle(_raw()))
    assert decision.outcome == "deny"
    assert decision.reason_code == "failure"


def test_presenter_exception_denies(tmp_path: Path) -> None:
    """Test presenter exception denies."""

    async def boom(request: dict[str, Any]) -> PresenterResponse:
        """Boom."""
        raise RuntimeError("presenter exploded")

    gate = _gate(tmp_path, presenter=boom)
    decision = asyncio.run(gate.handle(_raw()))
    assert decision.outcome == "deny"
    assert decision.reason_code == "failure"


def test_presenter_cancel_denies(tmp_path: Path) -> None:
    """Test presenter cancel denies."""

    async def cancelled(request: dict[str, Any]) -> PresenterResponse:
        """Cancelled."""
        raise asyncio.CancelledError()

    gate = _gate(tmp_path, presenter=cancelled)
    decision = asyncio.run(gate.handle(_raw()))
    assert decision.outcome == "deny"


def test_stale_response_discarded_and_denied(tmp_path: Path) -> None:
    """Test stale response discarded and denied."""

    async def stale(request: dict[str, Any]) -> PresenterResponse:
        """Stale."""
        return PresenterResponse(
            outcome="allow",
            scope="once",
            cast_id="call_OTHER",
            argument_digest=request["argument_digest"],
        )

    gate = _gate(tmp_path, presenter=stale)
    decision = asyncio.run(gate.handle(_raw()))
    assert decision.outcome == "deny"
    assert decision.reason_code == "failure"


def test_session_approval_allows_until_session_changes(tmp_path: Path) -> None:
    """Test session approval allows until session changes."""
    api = FakeAPI(session_id="sess-1")

    async def approve_session(request: dict[str, Any]) -> PresenterResponse:
        """Approve session."""
        return PresenterResponse(outcome="allow", scope="session")

    gate = ApprovalGate(api=api, data_dir=tmp_path, presenter=approve_session)
    first = asyncio.run(gate.handle(_raw()))
    assert first.outcome == "allow" and first.scope == "session"

    # Second cast: no prompt, session switch still active.
    async def must_not_prompt(request: dict[str, Any]) -> PresenterResponse:
        """Must not prompt."""
        raise AssertionError("must not prompt while session approved")

    gate._presenter = must_not_prompt  # noqa: SLF001 - test seam
    second = asyncio.run(gate.handle(_raw()))
    assert second.outcome == "allow" and second.scope == "session"

    # Session id changes live: the stored approval self-clears, prompt returns.
    api._runner.context.session_id = "sess-2"
    prompted: list[dict[str, Any]] = []

    async def prompt_again(request: dict[str, Any]) -> PresenterResponse:
        """Prompt again."""
        prompted.append(request)
        return PresenterResponse(outcome="allow", scope="once")

    gate._presenter = prompt_again  # noqa: SLF001 - test seam
    third = asyncio.run(gate.handle(_raw(session_id="sess-2")))
    assert third.outcome == "allow"
    assert prompted, "session change must clear approval and re-prompt"


def test_spell_scope_persists_allow_rule(tmp_path: Path) -> None:
    """Test spell scope persists allow rule."""

    async def approve_spell(request: dict[str, Any]) -> PresenterResponse:
        """Approve spell."""
        return PresenterResponse(outcome="allow", scope="spell")

    gate = _gate(tmp_path, presenter=approve_spell)
    decision = asyncio.run(gate.handle(_raw()))
    assert decision.outcome == "allow" and decision.scope == "spell"
    assert len(gate.policy.always_allow) == 1

    # A fresh gate (same install) honors the persisted rule without prompting.
    async def must_not_prompt(request: dict[str, Any]) -> PresenterResponse:
        """Must not prompt."""
        raise AssertionError("persisted allow rule must not prompt")

    gate2 = _gate(tmp_path, presenter=must_not_prompt)
    again = asyncio.run(gate2.handle(_raw()))
    assert again.outcome == "allow"
    assert again.reason_code == "rule"


def test_project_scope_persists_project_approval(tmp_path: Path) -> None:
    """Test project scope persists project approval."""

    async def approve_project(request: dict[str, Any]) -> PresenterResponse:
        """Approve project."""
        return PresenterResponse(outcome="allow", scope="project")

    gate = _gate(tmp_path, presenter=approve_project)
    decision = asyncio.run(gate.handle(_raw()))
    assert decision.outcome == "allow" and decision.scope == "project"
    assert len(gate.policy.approved_projects) == 1

    async def must_not_prompt(request: dict[str, Any]) -> PresenterResponse:
        """Must not prompt."""
        raise AssertionError("persisted project approval must not prompt")

    gate2 = _gate(tmp_path, presenter=must_not_prompt)
    again = asyncio.run(gate2.handle(_raw()))
    assert again.outcome == "allow"
    assert again.scope == "project"


def test_schemaless_cast_prompts_with_warning(tmp_path: Path) -> None:
    """Test schemaless cast prompts with warning."""
    seen: list[dict[str, Any]] = []

    async def capture(request: dict[str, Any]) -> PresenterResponse:
        """Capture."""
        seen.append(request)
        return PresenterResponse(outcome="allow", scope="once")

    gate = _gate(tmp_path, presenter=capture)
    decision = asyncio.run(gate.handle(_raw(schemaless=True)))
    assert decision.outcome == "allow"
    assert seen and "unvalidated" in seen[0].get("warning", "")


def test_schemaless_cast_denies_without_presenter(tmp_path: Path) -> None:
    """Test schemaless cast denies without presenter."""
    gate = _gate(tmp_path)
    decision = asyncio.run(gate.handle(_raw(schemaless=True)))
    assert decision.outcome == "deny"


def test_audit_failure_denies_unless_override(tmp_path: Path) -> None:
    """Test audit failure denies unless override."""
    gate = _gate(tmp_path, presenter=_allow_once)
    from mvge_runes_approval_rune.audit import AuditError

    def _fail(record: dict[str, Any]) -> None:
        raise AuditError("disk full")

    gate.audit.append_decision = _fail  # type: ignore[method-assign]
    decision = asyncio.run(gate.handle(_raw()))
    assert decision.outcome == "deny"
    assert decision.reason_code == "failure"

    gate.allow_without_audit = True
    allowed = asyncio.run(gate.handle(_raw()))
    assert allowed.outcome == "allow"


def test_decision_is_audited(tmp_path: Path) -> None:
    """Test decision is audited."""
    gate = _gate(tmp_path, presenter=_allow_once)
    asyncio.run(gate.handle(_raw()))
    records = gate.audit.recent(10)
    assert any(
        r.get("cast_id") == "call_1" and r.get("decision") == "allow"
        for r in records
    )


# -- canonical engine contract ------------------------------------------------


def _core_request(**kwargs: Any) -> CoreApprovalRequest:
    """Build a canonical engine ApprovalRequest."""
    base: dict[str, Any] = {
        "cast_id": "call_1",
        "spell_name": "shell",
        "spell_identity": {
            "name": "shell",
            "source_kind": "builtin",
            "source_id": "",
            "runner_origin": "false",
            "read_only": "false",
        },
        "arguments": {"command": "git status"},
        "argument_digest": "sha256:def",
        "project_root": "/workspace/proj",
        "tome_id": "tome-1",
        "schema_validated": True,
    }
    base.update(kwargs)
    return CoreApprovalRequest(**base)


class FakeEngineAPI:
    """Fake RuneAPI exposing the canonical request_approval channel."""

    def __init__(self, decision: Any) -> None:
        """Initialize the instance.

        ``decision`` is either a canned canonical decision or a callable
        taking the canonical request and returning one (the callable form
        is needed when the gate synthesizes the request itself).
        """
        self.decision = decision
        self.seen: list[CoreApprovalRequest] = []
        self.install_id = "install-1"

    async def request_approval(
        self, request: CoreApprovalRequest
    ) -> CoreApprovalDecision:
        """Record the canonical request and return the canned decision."""
        self.seen.append(request)
        if callable(self.decision):
            return self.decision(request)
        return self.decision


def test_handle_accepts_canonical_engine_request(tmp_path: Path) -> None:
    """Test handle accepts the canonical engine request shape."""
    request = _core_request()
    api = FakeEngineAPI(core_allow(request))
    gate = ApprovalGate(api=api, data_dir=tmp_path)
    decision = asyncio.run(gate.handle(request))
    assert isinstance(decision, CoreApprovalDecision)
    assert decision.outcome == ApprovalOutcome.ALLOW
    assert decision.request_digest == "sha256:def"
    assert len(api.seen) == 1
    assert isinstance(api.seen[0], CoreApprovalRequest)
    assert api.seen[0].cast_id == "call_1"


def test_handle_denies_on_canonical_presenter_deny(tmp_path: Path) -> None:
    """Test a canonical presenter denial returns a canonical denial."""
    request = _core_request()
    api = FakeEngineAPI(core_deny(request, reason_code="user"))
    gate = ApprovalGate(api=api, data_dir=tmp_path)
    decision = asyncio.run(gate.handle(request))
    assert isinstance(decision, CoreApprovalDecision)
    assert decision.outcome == ApprovalOutcome.DENY


def test_canonical_schemaless_request_still_prompts(tmp_path: Path) -> None:
    """Test a schemaless canonical request still prompts when approved."""
    request = _core_request(schema_validated=False)
    api = FakeEngineAPI(core_allow(request))
    gate = ApprovalGate(api=api, data_dir=tmp_path)
    gate.approve_session("sess-1")
    decision = asyncio.run(gate.handle(request))
    assert isinstance(decision, CoreApprovalDecision)
    assert decision.outcome == ApprovalOutcome.ALLOW
    # The prompt must have happened: the session grant must not have matched.
    assert len(api.seen) == 1
    assert api.seen[0].schema_validated is False


def test_raw_mapping_still_drives_canonical_presenter(tmp_path: Path) -> None:
    """Test the legacy raw-mapping input reaches a canonical presenter."""
    api = FakeEngineAPI(lambda req: core_allow(req))
    gate = ApprovalGate(api=api, data_dir=tmp_path)
    decision = asyncio.run(gate.handle(_raw()))
    assert isinstance(decision, CoreApprovalDecision)
    assert decision.outcome == ApprovalOutcome.ALLOW
    assert len(api.seen) == 1
    assert isinstance(api.seen[0], CoreApprovalRequest)


def test_from_core_request_plains_nested_frozen_arguments() -> None:
    """Test from core request plains nested frozen arguments.

    The engine freezes arguments (nested MappingProxyType). The adapter
    must hand the policy layer plain dicts so redaction, summaries, and
    the audit JSON never see a non-serializable mapping.
    """
    request = _core_request(
        arguments=MappingProxyType(
            {
                "path": "/workspace/proj/a.txt",
                "nested": MappingProxyType({"token": "sk-secret"}),
            }
        ),
    )
    internal = from_core_request(request)
    assert internal.arguments == {
        "path": "/workspace/proj/a.txt",
        "nested": {"token": "sk-secret"},
    }
    assert type(internal.arguments["nested"]) is dict
    summarized = argument_summary(internal.arguments)
    redacted = redact(summarized)
    assert "sk-secret" not in json.dumps(redacted)
