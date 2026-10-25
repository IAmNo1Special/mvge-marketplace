"""Domain types and dataclasses for MADR 3.0 Architectural Decision Records."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class ADRStatus(StrEnum):
    """ADR status lifecycle per MADR 3.0."""

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    DEPRECATED = "deprecated"


@dataclass
class ADREntry:
    """An Architectural Decision Record adhering to MADR 3.0."""

    number: int
    title: str
    status: str
    date: str
    deciders: str = ""
    context_and_problem_statement: str = ""
    decision_outcome: str = ""
    considered_options: list[str] = field(default_factory=list)
    consequences: list[str] = field(default_factory=list)
    path: Path = field(default_factory=Path)


@dataclass
class ADRValidationIssue:
    """A validation issue found during MADR 3.0 linting."""

    filename: str
    message: str
    is_error: bool = False


@dataclass
class ADRValidationReport:
    """Validation report for MADR 3.0 decision records."""

    valid: bool
    errors: list[ADRValidationIssue] = field(default_factory=list)
    warnings: list[ADRValidationIssue] = field(default_factory=list)
    adrs: list[ADREntry] = field(default_factory=list)

    def add_error(self, filename: str, msg: str) -> None:
        self.errors.append(
            ADRValidationIssue(filename=filename, message=msg, is_error=True)
        )
        self.valid = False

    def add_warning(self, filename: str, msg: str) -> None:
        self.warnings.append(
            ADRValidationIssue(filename=filename, message=msg, is_error=False)
        )
