from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TrajectoryMatchType(StrEnum):
    EXACT = "EXACT"
    IN_ORDER = "IN_ORDER"
    ANY_ORDER = "ANY_ORDER"


@dataclass
class EvalCase:
    eval_case_id: str
    description: str = ""
    prompt: str = ""
    expected_tool_trajectory: list[dict[str, Any]] = field(default_factory=list)
    trajectory_match_type: TrajectoryMatchType = TrajectoryMatchType.IN_ORDER
    expected_response_keywords: list[str] = field(default_factory=list)
    rubric: str = ""


@dataclass
class EvalSet:
    name: str
    description: str = ""
    eval_cases: list[EvalCase] = field(default_factory=list)


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    trajectory_score: float
    keyword_score: float
    total_score: float
    executed_calls: list[dict[str, Any]] = field(default_factory=list)
    details: str = ""


@dataclass
class EvalReport:
    evalset_name: str
    total_cases: int
    passed_cases: int
    failed_cases: int
    avg_trajectory_score: float
    avg_total_score: float
    case_results: list[CaseResult] = field(default_factory=list)
