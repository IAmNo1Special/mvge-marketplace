"""Approval Rune: fail-closed execution gate for mutating spell casts."""

from mvge_runes_approval_rune.audit import AuditError, AuditLog
from mvge_runes_approval_rune.contracts import (
    ApprovalDecision,
    ApprovalRequest,
    SpellIdentity,
)
from mvge_runes_approval_rune.gate import (
    ApprovalGate,
    PresenterResponse,
    build_request,
)
from mvge_runes_approval_rune.normalization import (
    NonSerializableArguments,
    argument_summary,
    digest_arguments,
    normalize_arguments,
    redact,
)
from mvge_runes_approval_rune.policy import (
    AllowRule,
    Constraint,
    DenyRule,
    MalformedPolicy,
    Policy,
    PolicyOutcome,
    PolicyStore,
    ProjectApproval,
    evaluate,
    load_policy,
)
from mvge_runes_approval_rune.views import PermissionsView

__all__ = [
    "AllowRule",
    "ApprovalDecision",
    "ApprovalGate",
    "ApprovalRequest",
    "AuditError",
    "AuditLog",
    "Constraint",
    "DenyRule",
    "MalformedPolicy",
    "NonSerializableArguments",
    "PermissionsView",
    "Policy",
    "PolicyOutcome",
    "PolicyStore",
    "PresenterResponse",
    "ProjectApproval",
    "SpellIdentity",
    "argument_summary",
    "build_request",
    "digest_arguments",
    "evaluate",
    "load_policy",
    "normalize_arguments",
    "redact",
]
