"""The critical spell gate: evaluates policy, prompts, and audits.

The gate registers via RuneAPI.register_spell_gate(handler) (engine-owned
contract). It never touches the model-visibility allowlist and never widens
plan mode: approval controls execution only.

Per-cast flow:
  1. Build and freeze the request (normalize + digest arguments).
  2. Evaluate deterministic precedence: deny > read-only auto-allow >
     session > project > per-spell allow > prompt.
  3. Prompt through the host presenter (or deny when none is bound).
  4. Audit the decision durably BEFORE execution; audit failure denies
     unless the explicit temporary override is active.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

from mvge.core import approval_dir
from mvge.core.approval import (
    ApprovalDecision as CoreApprovalDecision,
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

from mvge_runes_approval_rune.audit import AuditError, AuditLog
from mvge_runes_approval_rune.contracts import (
    ApprovalDecision,
    ApprovalRequest,
    ReasonCode,
    Scope,
    SpellIdentity,
)
from mvge_runes_approval_rune.normalization import (
    NonSerializableArguments,
    argument_summary,
    normalize_arguments,
    plain_arguments,
    redact,
)
from mvge_runes_approval_rune.policy import (
    AllowRule,
    Constraint,
    Policy,
    PolicyStore,
    ProjectApproval,
    _parse_constraint,
    evaluate,
    load_policy,
)

LAST_USED_FILENAME = "last-used.json"


def default_data_dir() -> Path:
    """The gate's user-owned data directory: ``<global>/approval``.

    A function, not a module constant. The global layer is relocatable via
    ``$MVGE_GLOBAL_DIR``, so the override has to be read when the directory
    is needed; freezing it at import reinstates the divergence for any
    process that sets the variable after loading the rune.

    Resolved through ``mvge.core.approval_dir`` so the gate and the
    engine's uninstall path -- which purges
    ``global_agents_dir()/approval/policy.toml`` -- cannot disagree. When
    they did, ``mvge rune uninstall approval-rune`` deleted a policy file
    the gate had never written, and the real grants survived the operation
    meant to revoke them.

    Imported from the ``mvge.core`` package root rather than
    ``mvge.core.constants``: the root is the public surface, and it
    re-exports the resolvers whether they live in ``constants`` or in the
    newer ``layers`` module.
    """
    return approval_dir()


@dataclass
class PresenterResponse:
    """What the host presenter returns for one approval request."""

    outcome: str  # "allow" | "deny"
    scope: str = "once"  # "once" | "spell" | "session" | "project"
    cast_id: str | None = None
    argument_digest: str | None = None
    constraints: list[dict[str, Any]] = field(default_factory=list)
    project_scoped: bool = False


Presenter = Callable[
    [dict[str, Any]], Awaitable[PresenterResponse] | PresenterResponse
]


def build_request(raw: Mapping[str, Any]) -> ApprovalRequest:
    """Validate and freeze a raw gate request.

    Raises ValueError for non-dict arguments (schemaless denial happens
    before the gate) and NonSerializableArguments for unserializable ones.
    """
    args = raw.get("arguments")
    if not isinstance(args, dict):
        raise ValueError("spell arguments must be a dict")
    frozen, digest = normalize_arguments(args)
    spell = raw.get("spell")
    if not isinstance(spell, SpellIdentity):
        raise ValueError("spell must be a SpellIdentity")
    return ApprovalRequest(
        cast_id=str(raw.get("cast_id", "")),
        spell=spell,
        arguments=frozen,
        argument_digest=digest,
        project_root=str(raw.get("project_root", "")),
        tome_id=str(raw.get("tome_id", "")),
        session_id=str(raw.get("session_id", "")),
        is_read_only=bool(raw.get("is_read_only", False)),
        runner_origin=bool(raw.get("runner_origin", False)),
        schemaless=bool(raw.get("schemaless", False)),
    )


def from_core_request(core_request: CoreApprovalRequest) -> ApprovalRequest:
    """Adapt the engine's canonical request to the rune's policy request.

    The engine owns identity, validation, and normalization; the rune
    derives its policy fields from the canonical data. The trust-relevant
    bits (runner_origin, read_only) ride the engine-derived identity
    mapping, and ``schemaless`` is the negation of the engine's
    ``schema_validated`` flag. The engine supplies the full five-field
    identity (name, source_kind, source_id, source_scope, code_digest),
    so allow rules pin the code digest and a code change invalidates the
    grant on the next cast.
    """
    identity = core_request.spell_identity
    spell = SpellIdentity(
        name=core_request.spell_name,
        source_kind=str(identity.get("source_kind", "")),
        source_id=str(identity.get("source_id", "")),
        source_scope=str(identity.get("source_scope", "")),
        code_digest=str(identity.get("code_digest", "")),
    )
    return ApprovalRequest(
        cast_id=core_request.cast_id,
        spell=spell,
        arguments=plain_arguments(core_request.arguments),
        argument_digest=core_request.argument_digest,
        project_root=core_request.project_root,
        tome_id=core_request.tome_id,
        session_id="",
        is_read_only=identity.get("read_only", "") == "true",
        runner_origin=identity.get("runner_origin", "") == "true",
        schemaless=not core_request.schema_validated,
    )


def _core_request_for(request: ApprovalRequest) -> CoreApprovalRequest:
    """Synthesize a canonical request from the rune's internal one.

    Used when the gate is driven by the rune's own raw-mapping contract
    (unit tests): the engine's presenter channel still receives the
    canonical shape it expects.
    """
    return CoreApprovalRequest(
        cast_id=request.cast_id,
        spell_name=request.spell.name,
        spell_identity={
            "name": request.spell.name,
            "source_kind": request.spell.source_kind,
            "source_id": request.spell.source_id,
            "source_scope": request.spell.source_scope,
            "code_digest": request.spell.code_digest,
            "runner_origin": "true" if request.runner_origin else "false",
            "read_only": "true" if request.is_read_only else "false",
        },
        arguments=dict(request.arguments),
        argument_digest=request.argument_digest,
        project_root=request.project_root,
        tome_id=request.tome_id,
        schema_validated=not request.schemaless,
    )


def _to_core_decision(
    decision: ApprovalDecision, core_request: CoreApprovalRequest
) -> CoreApprovalDecision:
    """Convert the rune's internal decision to the canonical engine shape."""
    if decision.outcome == "allow":
        return core_allow(
            core_request,
            reason_code=decision.reason_code,
            scope=decision.scope,
        )
    return core_deny(
        core_request,
        reason_code=decision.reason_code,
        scope=decision.scope,
    )


class ApprovalGate:
    """Fail-closed execution gate owned by the Approval Rune."""

    def __init__(
        self,
        api: Any | None = None,
        data_dir: Path | None = None,
        install_id: str | None = None,
        presenter: Presenter | None = None,
    ) -> None:
        """Initialize the instance."""
        self._api = api
        self._presenter = presenter
        resolved_install = install_id
        if resolved_install is None and api is not None:
            candidate = getattr(api, "install_id", None)
            resolved_install = candidate if isinstance(candidate, str) else None
        self._install_id = resolved_install
        self._store = PolicyStore(
            data_dir or default_data_dir(), self._install_id
        )
        self.policy: Policy = load_policy(self._store)
        self.audit = AuditLog(data_dir or default_data_dir())
        self._session_id: str | None = None
        self._session_approved = False
        self._suppressed: set[tuple[tuple[str, ...], str]] = set()
        self.allow_without_audit = False
        self.warnings: list[str] = list(self.policy.warnings)
        # Last-used is tracked in memory; flushed opportunistically alongside
        # other policy writes. Never rewrite the policy file per matched rule.
        self._last_used: dict[str, str] = self._read_last_used()
        if self.policy.install_mismatch:
            self._audit_install_mismatch()

    @property
    def _data_dir(self) -> Path:
        return Path(self._store.data_dir)

    def _read_last_used(self) -> dict[str, str]:
        try:
            data = json.loads(
                (self._data_dir / LAST_USED_FILENAME).read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def flush_last_used(self) -> None:
        """Opportunistic flush alongside other policy writes."""
        path = self._data_dir / LAST_USED_FILENAME
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self._data_dir, 0o700)
            with open(path, "w", encoding="utf-8") as handle:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                json.dump(self._last_used, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(path, 0o600)
        except OSError:
            pass

    def refresh_policy(self) -> None:
        """Reload the policy from disk (revocation takes effect immediately)."""
        self.policy = load_policy(self._store)
        self.warnings = list(self.policy.warnings)

    # -- session approval (Rune-owned) ------------------------------------

    def _audit_install_mismatch(self) -> None:
        """Record the install-id mismatch; the policy was ignored entirely."""
        try:
            self.audit.append_decision(
                {
                    "timestamp": AuditLog.utcnow(),
                    "event": "policy_ignored_install_mismatch",
                    "decision": "deny",
                    "detail": (
                        "policy file stamped with a different install id; "
                        "ignored entirely, every mutating cast prompts"
                    ),
                }
            )
        except AuditError:
            pass

    def _live_session_id(self) -> str | None:
        context = self._api.context
        value = getattr(context, "session_id", None)
        return value if isinstance(value, str) else None

    def _live_tome_id(self) -> str | None:
        # Phase 2A: RuneContext carries no tome id, so this always
        # returned None through the old runner path too.
        return None

    def _live_project_root(self) -> str | None:
        context = self._api.context
        value = getattr(context, "project_root", None)
        return value if isinstance(value, str) else None

    def _reconcile_session(self) -> None:
        """Self-healing session check: a live mismatch clears the approval."""
        live = self._live_session_id()
        if (
            self._session_approved
            and live is not None
            and live != self._session_id
        ):
            self._session_approved = False
            self._session_id = None
            self._suppressed.clear()

    def approve_session(self, session_id: str) -> None:
        """Approve session."""
        self._session_id = session_id
        self._session_approved = True

    def clear_session_approval(self) -> None:
        """Clear session approval."""
        self._session_approved = False
        self._session_id = None
        self._suppressed.clear()

    @property
    def session_approved(self) -> bool:
        """Session approved."""
        self._reconcile_session()
        return self._session_approved

    # -- gate entry -------------------------------------------------------

    async def handle(
        self, raw: Mapping[str, Any] | CoreApprovalRequest
    ) -> CoreApprovalDecision:
        """Evaluate one gate request and return the canonical decision.

        Accepts the engine's canonical ``ApprovalRequest`` (the production
        path through ``RuneRunner.evaluate_spell_gates``) or the rune's own
        raw-mapping contract (unit tests). Always returns the canonical
        ``mvge.core.approval.ApprovalDecision`` the engine
        isinstance-checks; the rune's internal decision type never crosses
        the engine boundary.
        """
        try:
            if isinstance(raw, CoreApprovalRequest):
                core_request = raw
                request = from_core_request(raw)
            else:
                request = build_request(raw)
                core_request = _core_request_for(request)
        except ValueError:
            return core_deny(
                CoreApprovalRequest(cast_id="", spell_name=""),
                reason_code="failure",
            )
        except NonSerializableArguments:
            return core_deny(
                CoreApprovalRequest(cast_id="", spell_name=""),
                reason_code="failure",
            )

        self._reconcile_session()
        self.refresh_policy()

        suppressed_key = (request.spell.full_key(), request.argument_digest)
        if suppressed_key in self._suppressed:
            return _to_core_decision(
                self._finalize(
                    self._deny(
                        request_digest=request.argument_digest,
                        reason_code="user",
                        reason="Denied: this exact call was denied earlier; "
                        "re-emitting it is denied without prompting for the "
                        "rest of the session.",
                    ),
                    request,
                    audit_outcome="denied",
                ),
                core_request,
            )

        outcome = evaluate(
            request, self.policy, session_approved=self._session_approved
        )
        if outcome.decision == "deny":
            return _to_core_decision(
                self._finalize(
                    self._deny(
                        request_digest=request.argument_digest,
                        reason_code=outcome.reason_code,
                        reason="Denied by approval policy "
                        f"(rule: {outcome.rule_id or 'explicit deny'}).",
                        rule_id=outcome.rule_id,
                    ),
                    request,
                    audit_outcome="denied",
                ),
                core_request,
            )
        if outcome.decision == "allow":
            return _to_core_decision(
                self._finalize(
                    ApprovalDecision(
                        outcome="allow",
                        scope=outcome.scope,
                        reason_code=outcome.reason_code,
                        request_digest=request.argument_digest,
                        reason="Allowed by approval policy "
                        f"(rule: {outcome.rule_id or outcome.scope}).",
                        rule_id=outcome.rule_id,
                    ),
                    request,
                    audit_outcome="started",
                ),
                core_request,
            )
        return _to_core_decision(
            await self._prompt(request, core_request), core_request
        )

    async def _prompt(
        self, request: ApprovalRequest, core_request: CoreApprovalRequest
    ) -> ApprovalDecision:
        payload = self._presenter_payload(request)
        snapshot = (
            request.cast_id,
            request.argument_digest,
            request.spell.full_key(),
            request.tome_id,
            request.project_root,
            request.session_id,
        )
        response = await self._ask_presenter(payload, core_request)
        if response is None:
            return self._finalize(
                self._deny(
                    request_digest=request.argument_digest,
                    reason_code="failure",
                    reason="Denied: no approval presenter is bound; "
                    "headless casts deny by default.",
                ),
                request,
                audit_outcome="denied",
            )
        if not self._response_is_fresh(response, snapshot):
            return self._finalize(
                self._deny(
                    request_digest=request.argument_digest,
                    reason_code="failure",
                    reason="Denied: the approval response no longer matches "
                    "the pending cast (stale response discarded).",
                ),
                request,
                audit_outcome="denied",
            )
        if response.outcome != "allow":
            self._suppressed.add(
                (request.spell.full_key(), request.argument_digest)
            )
            return self._finalize(
                self._deny(
                    request_digest=request.argument_digest,
                    reason_code="user",
                    reason="Denied by the user. Re-emitting this exact call "
                    "will be denied without prompting for the rest of the "
                    "session.",
                ),
                request,
                audit_outcome="denied",
            )
        return await self._apply_grant(request, response)

    async def _apply_grant(
        self, request: ApprovalRequest, response: PresenterResponse
    ) -> ApprovalDecision:
        if response.scope in ("once", "spell", "session", "project"):
            scope = cast(Scope, response.scope)
        else:
            scope = "once"
        if scope == "session":
            self.approve_session(self._live_session_id() or request.session_id)
        elif scope == "project":
            self.policy.approved_projects.append(
                ProjectApproval(root=request.project_root, enabled=True)
            )
            if not self._save_policy():
                return self._grant_save_failed(request)
        elif scope == "spell":
            constraints = self._parse_constraints(response.constraints)
            if constraints is None:
                return self._finalize(
                    self._deny(
                        request_digest=request.argument_digest,
                        reason_code="failure",
                        reason="Denied: the presenter returned constraints "
                        "the gate could not validate.",
                    ),
                    request,
                    audit_outcome="denied",
                )
            rule = AllowRule(
                spell=request.spell,
                constraints=constraints,
                project=request.project_root
                if response.project_scoped
                else None,
                id=f"allow-{len(self.policy.always_allow)}",
            )
            self.policy.always_allow.append(rule)
            if not self._save_policy():
                self.policy.always_allow.remove(rule)
                return self._grant_save_failed(request)
        decision = ApprovalDecision(
            outcome="allow",
            scope=scope,
            reason_code="user",
            request_digest=request.argument_digest,
            reason=f"Approved by the user ({scope}).",
        )
        return self._finalize(decision, request, audit_outcome="started")

    def _grant_save_failed(self, request: ApprovalRequest) -> ApprovalDecision:
        return self._finalize(
            self._deny(
                request_digest=request.argument_digest,
                reason_code="failure",
                reason="Denied: the persistent grant could not be written, "
                "so the approval cannot be recorded.",
            ),
            request,
            audit_outcome="denied",
        )

    def _save_policy(self) -> bool:
        try:
            self._store.save(self.policy)
        except OSError:
            return False
        return True

    @staticmethod
    def _parse_constraints(
        raw: list[dict[str, Any]],
    ) -> list[Constraint] | None:
        constraints: list[Constraint] = []
        for index, item in enumerate(raw):
            try:
                constraints.append(_parse_constraint(item, f"grant[{index}]"))
            except ValueError:
                return None
        return constraints

    # -- presenter plumbing ------------------------------------------------

    def _presenter_payload(self, request: ApprovalRequest) -> dict[str, Any]:
        spell = request.spell
        payload: dict[str, Any] = {
            "cast_id": request.cast_id,
            "spell": {
                "name": spell.name,
                "source_kind": spell.source_kind,
                "source_id": spell.source_id,
                "source_scope": spell.source_scope,
                "code_digest": spell.code_digest,
            },
            "argument_summary": argument_summary(request.arguments),
            "argument_digest": request.argument_digest,
            "request_digest": request.argument_digest,
            "project_root": request.project_root,
            "tome_id": request.tome_id,
            "session_id": request.session_id,
            "is_read_only": request.is_read_only,
            "schemaless": request.schemaless,
            "warning": None,
        }
        if request.schemaless:
            payload["warning"] = (
                "unvalidated arguments: this spell has no parameter schema, "
                "so no persistent rule can cover it; every cast prompts."
            )
        return payload

    async def _ask_presenter(
        self, payload: dict[str, Any], core_request: CoreApprovalRequest | None
    ) -> PresenterResponse | None:
        # Canonical path: the engine's presenter channel receives the
        # canonical request (the shape GUI/CLI presenters read), and the
        # returned canonical decision is coerced to the rune's response.
        if core_request is not None and self._api is not None:
            candidate = getattr(self._api, "request_approval", None)
            if callable(candidate):
                try:
                    result = candidate(core_request)
                    if inspect.isawaitable(result):
                        result = await result
                except asyncio.CancelledError:
                    return PresenterResponse(outcome="deny", scope="once")
                except Exception:
                    return None
                return self._coerce_response(result, payload)
        responder = self._presenter
        if responder is None and self._api is not None:
            candidate = getattr(self._api, "request_approval", None)
            if callable(candidate):
                responder = candidate
        if responder is None:
            return None
        try:
            result = responder(payload)
            if inspect.isawaitable(result):
                result = await result
        except asyncio.CancelledError:
            return PresenterResponse(outcome="deny", scope="once")
        except Exception:
            return None
        return self._coerce_response(result, payload)

    def _coerce_response(
        self, result: Any, payload: dict[str, Any]
    ) -> PresenterResponse | None:
        if isinstance(result, PresenterResponse):
            return result
        if isinstance(result, CoreApprovalDecision):
            outcome = str(result.outcome)
            if outcome not in ("allow", "deny"):
                return None
            scope = str(result.scope)
            return PresenterResponse(
                outcome=outcome,
                scope=(
                    scope
                    if scope in ("once", "spell", "session", "project")
                    else "once"
                ),
                argument_digest=result.request_digest,
            )
        if isinstance(result, ApprovalDecision):
            return PresenterResponse(
                outcome=result.outcome,
                scope=result.scope,
                argument_digest=result.request_digest,
            )
        if isinstance(result, Mapping):
            mapped_outcome = result.get("outcome")
            if mapped_outcome not in ("allow", "deny"):
                return None
            constraints = result.get("constraints", [])
            return PresenterResponse(
                outcome=mapped_outcome,
                scope=result.get("scope", "once"),
                cast_id=result.get("cast_id"),
                argument_digest=result.get("argument_digest"),
                constraints=list(constraints)
                if isinstance(constraints, list)
                else [],
                project_scoped=bool(result.get("project_scoped", False)),
            )
        return None

    def _response_is_fresh(
        self,
        response: PresenterResponse,
        snapshot: tuple[Any, ...],
    ) -> bool:
        """Stale-response rule: discard late clicks bound to another context."""
        (
            cast_id,
            argument_digest,
            full_key,
            tome_id,
            project_root,
            session_id,
        ) = snapshot
        if response.cast_id is not None and response.cast_id != cast_id:
            return False
        if (
            response.argument_digest is not None
            and response.argument_digest != argument_digest
        ):
            return False
        live_tome = self._live_tome_id()
        if live_tome is not None and tome_id and live_tome != tome_id:
            return False
        live_project = self._live_project_root()
        if (
            live_project is not None
            and project_root
            and live_project != project_root
        ):
            return False
        live_session = self._live_session_id()
        if (
            live_session is not None
            and session_id
            and live_session != session_id
        ):
            return False
        return True

    # -- decisions and audit ------------------------------------------------

    def _deny(
        self,
        request_digest: str,
        reason_code: ReasonCode,
        reason: str,
        rule_id: str | None = None,
    ) -> ApprovalDecision:
        return ApprovalDecision(
            outcome="deny",
            scope="once",
            reason_code=reason_code,
            request_digest=request_digest,
            reason=reason,
            rule_id=rule_id,
        )

    def _finalize(
        self,
        decision: ApprovalDecision,
        request: ApprovalRequest,
        audit_outcome: str,
    ) -> ApprovalDecision:
        """Audit the decision durably before execution."""
        record: dict[str, Any] = {
            "timestamp": AuditLog.utcnow(),
            "cast_id": request.cast_id,
            "spell": {
                "name": request.spell.name,
                "source_kind": request.spell.source_kind,
                "source_id": request.spell.source_id,
                "source_scope": request.spell.source_scope,
                "code_digest": request.spell.code_digest,
            },
            "project": request.project_root,
            "tome_id": request.tome_id,
            "argument_digest": request.argument_digest,
            "argument_summary": redact(argument_summary(request.arguments)),
            "decision": decision.outcome,
            "scope": decision.scope,
            "reason_code": decision.reason_code,
            "matched_rule_id": decision.rule_id,
            "execution": audit_outcome,
        }
        try:
            self.audit.append_decision(record)
        except AuditError:
            if not self.allow_without_audit:
                return self._deny(
                    request_digest=request.argument_digest,
                    reason_code="failure",
                    reason="Denied: the audit append failed and durable "
                    "audit is required.",
                )
        if decision.outcome == "allow" and decision.rule_id:
            self._last_used[decision.rule_id] = AuditLog.utcnow()
        return decision

    def record_outcome(self, cast_id: str, event: str, **extra: Any) -> None:
        """Record an execution outcome: started, completed, or failed."""
        try:
            self.audit.append_outcome(cast_id, event, **extra)
        except AuditError:
            pass

    async def on_after_spell_result(self, data: Any) -> None:
        """Hook: record completed/failed for casts the gate allowed."""
        cast_id = self._sigil_field(data, "cast_id")
        if not cast_id:
            return
        error = self._sigil_field(data, "error")
        self.record_outcome(str(cast_id), "failed" if error else "completed")

    @staticmethod
    def _sigil_field(data: Any, name: str) -> Any:
        if data is None:
            return None
        if isinstance(data, dict):
            return data.get(name)
        return getattr(data, name, None)
