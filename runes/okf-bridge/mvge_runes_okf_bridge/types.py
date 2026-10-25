"""Domain types and dataclasses for OKF v0.2 and MADR 3.0."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any


class TrustTier(StrEnum):
    """Trust tier derived from verified actors according to OKF v0.2 §5.3."""

    UNVERIFIED = "unverified"
    MACHINE_CONFIRMED = "machine-confirmed"
    HUMAN_REVIEWED = "human-reviewed"


@dataclass(frozen=True)
class Source:
    """A source a concept derives from, per OKF v0.2 §5.1."""

    id: str
    resource: str
    title: str = ""
    author: str = ""
    usage_count: int | None = None
    last_modified: str = ""


@dataclass(frozen=True)
class UsageWindow:
    """Usage window framing source usage counts, per OKF v0.2 §5.1."""

    from_date: str = ""
    to_date: str = ""


@dataclass(frozen=True)
class AttestedComputation:
    """Contract for an Attested Computation concept, per OKF v0.2 §10."""

    runtime: str
    parameters: dict[str, Any] = field(default_factory=dict)
    executor: dict[str, Any] = field(default_factory=dict)
    attester: dict[str, Any] = field(default_factory=dict)
    computation: str = ""


@dataclass
class Concept:
    """A single OKF concept document, per OKF v0.2 §4.

    `context` is the dotagents runtime-injection extension key
    (`auto` | `search-only`); empty means unset. `origin` records which
    bundle layer the concept was loaded from (`global`, `workspace`,
    or `explicit` for a directly-passed bundle path).
    """

    id: str
    path: Path
    type: str
    title: str = ""
    description: str = ""
    resource: str = ""
    tags: list[str] = field(default_factory=list)
    status: str = "stable"
    stale_after: str | None = None
    context: str = ""
    origin: str = ""
    generated: dict[str, str] = field(default_factory=dict)
    verified: list[dict[str, str]] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    usage_window: UsageWindow | None = None
    computation: AttestedComputation | None = None
    body: str = ""
    links: list[str] = field(default_factory=list)
    footnotes: list[str] = field(default_factory=list)

    @property
    def trust_tier(self) -> TrustTier:
        """Derive trust tier per OKF v0.2 §5.3.

        Keyed strictly off the lowercase 'human:' prefix.
        """
        if not self.verified:
            return TrustTier.UNVERIFIED
        for v in self.verified:
            actor = str(v.get("by", "")).strip()
            if actor.startswith("human:"):
                return TrustTier.HUMAN_REVIEWED
        return TrustTier.MACHINE_CONFIRMED

    @property
    def is_stale(self) -> bool:
        """Check if concept is past its stale_after date, per OKF v0.2 §5.5."""
        if not self.stale_after:
            return False
        try:
            today = datetime.now(UTC).date().isoformat()
            return today >= self.stale_after
        except (TypeError, ValueError):
            return False


@dataclass
class ValidationIssue:
    """A validation issue identified in an OKF bundle."""

    rel_path: str
    message: str
    is_error: bool = False


@dataclass
class ValidationReport:
    """Report produced by OKF bundle validation, per OKF v0.2 §11."""

    valid: bool
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)
    concepts: int = 0
    indexes: int = 0
    logs: int = 0

    def add_error(self, rel: str, msg: str) -> None:
        self.errors.append(ValidationIssue(rel_path=rel, message=msg, is_error=True))
        self.valid = False

    def add_warning(self, rel: str, msg: str) -> None:
        self.warnings.append(ValidationIssue(rel_path=rel, message=msg, is_error=False))
