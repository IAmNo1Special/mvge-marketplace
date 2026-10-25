"""Permissions view for the GUI settings dialog.

Plain data only: dicts, lists, and strings. The GUI agent builds the
marketplace-card settings dialog against this shape; any shape change must
go through the coordinator, never silently.

Contract:
  get_permissions_view() -> {
      "session": {"session_id": str | None, "approve_all_active": bool},
      "trusted_projects": [{"id", "root", "enabled"}],
      "always_allowed": [{"id", "spell": {...full identity...},
                          "project": str | None, "scope_label": str,
                          "constraints": [...], "constraint_labels": [str],
                          "last_used": str | None}],
      "never_allowed": [{"id", "spell": {...stable identity...},
                         "project": str | None, "last_used": str | None}],
      "source_identity": {"install_id": str | None, "data_dir": str},
      "recent_activity": [{"timestamp", "cast_id", "spell_name", "decision",
                           "scope", "reason_code", "project"}],
      "warnings": [str],
  }
  revoke_grant(kind, id) -> bool
      kind in {"allow_rule", "deny_rule", "project", "session"}.
      Revocation persists immediately and takes effect before the next cast.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mvge_runes_approval_rune.audit import AuditLog
from mvge_runes_approval_rune.contracts import SpellIdentity


def constraint_label(constraint: Any) -> str:
    """Human-readable one-line description of a constraint."""
    op = constraint.op
    if op == "exact":
        return f"{constraint.field} == {constraint.value!r}"
    if op == "one_of":
        return f"{constraint.field} in {list(constraint.values)!r}"
    if op == "path_within_project":
        return f"{constraint.field} stays inside the project"
    if op == "command_prefix":
        pattern = constraint.program_glob or "*"
        args = constraint.args_glob if constraint.args_glob is not None else "*"
        return f"command_prefix: program {pattern!r}, args {args!r}"
    return f"{constraint.field} {op}"


def constraint_view(constraint: Any) -> dict[str, Any]:
    """Constraint view."""
    view: dict[str, Any] = {"field": constraint.field, "op": constraint.op}
    if constraint.op == "exact":
        view["value"] = constraint.value
    elif constraint.op == "one_of":
        view["values"] = list(constraint.values)
    elif constraint.op == "command_prefix":
        view["program_glob"] = constraint.program_glob
        view["args_glob"] = constraint.args_glob
    return view


def spell_view(identity: SpellIdentity, with_digest: bool) -> dict[str, Any]:
    """Spell view."""
    view: dict[str, Any] = {
        "name": identity.name,
        "source_kind": identity.source_kind,
        "source_id": identity.source_id,
        "source_scope": identity.source_scope,
    }
    if with_digest:
        view["code_digest"] = identity.code_digest
    return view


class PermissionsView:
    """Plain-data permissions surface backed by an ApprovalGate."""

    def __init__(self, gate: Any) -> None:
        """Initialize the instance."""
        self._gate = gate

    @property
    def _data_dir(self) -> Path:
        return Path(self._gate._store.data_dir)  # noqa: SLF001

    def note_rule_used(self, rule_id: str) -> None:
        """Track last-used in memory; the gate flushes opportunistically."""
        self._gate._last_used[rule_id] = AuditLog.utcnow()  # noqa: SLF001

    def flush_last_used(self) -> None:
        """Opportunistic flush alongside other policy writes."""
        self._gate.flush_last_used()

    def get_permissions_view(self) -> dict[str, Any]:
        """Get permissions view."""
        gate = self._gate
        gate.refresh_policy()
        policy = gate.policy
        return {
            "session": {
                "session_id": gate._session_id,  # noqa: SLF001
                "approve_all_active": gate.session_approved,
            },
            "trusted_projects": [
                {
                    "id": f"project:{item.root}",
                    "root": item.root,
                    "enabled": item.enabled,
                }
                for item in policy.approved_projects
            ],
            "always_allowed": [
                {
                    "id": rule.id,
                    "spell": spell_view(rule.spell, with_digest=True),
                    "project": rule.project,
                    "scope_label": ("project" if rule.project else "global"),
                    "constraints": [
                        constraint_view(c) for c in rule.constraints
                    ],
                    "constraint_labels": [
                        constraint_label(c) for c in rule.constraints
                    ],
                    "last_used": self._gate._last_used.get(rule.id),  # noqa: SLF001
                }
                for rule in policy.always_allow
            ],
            "never_allowed": [
                {
                    "id": rule.id,
                    "spell": spell_view(rule.spell, with_digest=False),
                    "project": rule.project,
                    "last_used": self._gate._last_used.get(rule.id),  # noqa: SLF001
                }
                for rule in policy.always_deny
            ],
            "source_identity": {
                "install_id": gate._install_id,  # noqa: SLF001
                "data_dir": str(self._data_dir),
            },
            "recent_activity": self._recent_activity(),
            "warnings": list(gate.warnings),
        }

    def _recent_activity(self, limit: int = 25) -> list[dict[str, Any]]:
        records = self._gate.audit.recent(limit)
        activity: list[dict[str, Any]] = []
        for record in records:
            if "decision" not in record:
                continue
            spell = record.get("spell")
            spell_name = spell.get("name") if isinstance(spell, dict) else None
            activity.append(
                {
                    "timestamp": record.get("timestamp"),
                    "cast_id": record.get("cast_id"),
                    "spell_name": spell_name,
                    "decision": record.get("decision"),
                    "scope": record.get("scope"),
                    "reason_code": record.get("reason_code"),
                    "project": record.get("project"),
                }
            )
        return activity

    def revoke_grant(self, kind: str, grant_id: str) -> bool:
        """One-click revoke. Persists immediately."""
        gate = self._gate
        policy = gate.policy
        changed = False
        if kind == "allow_rule":
            before = len(policy.always_allow)
            policy.always_allow = [
                rule for rule in policy.always_allow if rule.id != grant_id
            ]
            changed = len(policy.always_allow) != before
        elif kind == "deny_rule":
            before = len(policy.always_deny)
            policy.always_deny = [
                rule for rule in policy.always_deny if rule.id != grant_id
            ]
            changed = len(policy.always_deny) != before
        elif kind == "project":
            root = (
                grant_id[len("project:") :]
                if grant_id.startswith("project:")
                else grant_id
            )
            before = len(policy.approved_projects)
            policy.approved_projects = [
                item for item in policy.approved_projects if item.root != root
            ]
            changed = len(policy.approved_projects) != before
        elif kind == "session":
            if gate.session_approved:
                gate.clear_session_approval()
                changed = True
        else:
            return False
        if not changed:
            return False
        self._gate._last_used.pop(grant_id, None)  # noqa: SLF001
        try:
            gate._store.save(policy)  # noqa: SLF001
        except OSError:
            return False
        self.flush_last_used()
        return True
