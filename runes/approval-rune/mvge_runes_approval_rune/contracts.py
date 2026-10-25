"""Immutable gate contracts for the Approval Rune.

These types cross the boundary between the engine-owned gate plumbing and
this rune's policy engine. They are frozen so a decision can never drift
from the request it was bound to.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Outcome = Literal["allow", "deny"]
Scope = Literal["once", "spell", "session", "project"]
ReasonCode = Literal["user", "rule", "read_only", "failure"]

OUTCOMES: tuple[str, ...] = ("allow", "deny")
SCOPES: tuple[str, ...] = ("once", "spell", "session", "project")
REASON_CODES: tuple[str, ...] = ("user", "rule", "read_only", "failure")


@dataclass(frozen=True)
class SpellIdentity:
    """Engine-derived identity of a spell.

    A display name is never enough: policy matching always uses the full
    engine-derived tuple. Deny rules intentionally match the stable key
    (without code_digest) so a code update cannot escape a block; allow
    rules pin the full key so a code change revokes the effective grant.
    """

    name: str
    source_kind: str
    source_id: str
    source_scope: str
    code_digest: str

    def stable_key(self) -> tuple[str, str, str, str]:
        """Identity without the code digest, for deny rules."""
        return (self.name, self.source_kind, self.source_id, self.source_scope)

    def full_key(self) -> tuple[str, str, str, str, str]:
        """Identity with the code digest, for allow rules."""
        return (*self.stable_key(), self.code_digest)


@dataclass(frozen=True)
class ApprovalRequest:
    """One immutable gate request.

    Arguments are the frozen, normalized copy the dispatcher approved for
    display. The gate must execute only what this request describes.
    """

    cast_id: str
    spell: SpellIdentity
    arguments: dict[str, Any]
    argument_digest: str
    project_root: str
    tome_id: str
    session_id: str
    is_read_only: bool
    runner_origin: bool
    schemaless: bool


@dataclass
class ApprovalDecision:
    """The rune's answer for one gate request."""

    outcome: Outcome
    scope: Scope
    reason_code: ReasonCode
    request_digest: str
    reason: str = ""
    rule_id: str | None = None

    def __post_init__(self) -> None:
        """Initialize the instance."""
        if self.outcome not in OUTCOMES:
            raise ValueError(f"bad outcome: {self.outcome!r}")
        if self.scope not in SCOPES:
            raise ValueError(f"bad scope: {self.scope!r}")
        if self.reason_code not in REASON_CODES:
            raise ValueError(f"bad reason_code: {self.reason_code!r}")

    def denial_payload(self) -> dict[str, Any]:
        """Synthetic approval_denied result for the model."""
        return {
            "approval_denied": True,
            "reason": self.reason,
            "reason_code": self.reason_code,
            "scope": self.scope,
        }
