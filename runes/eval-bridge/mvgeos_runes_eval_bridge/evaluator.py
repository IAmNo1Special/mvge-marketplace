from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mvgeos_runes_eval_bridge.schema import (
    CaseResult,
    EvalCase,
    EvalReport,
    EvalSet,
    TrajectoryMatchType,
)


def _call_matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    act_name = actual.get("spell_name") or actual.get("name")
    exp_name = expected.get("spell_name") or expected.get("name")
    if act_name != exp_name:
        return False

    exp_args = expected.get("arguments", {})
    act_args = actual.get("arguments", {})
    if not isinstance(exp_args, dict) or not isinstance(act_args, dict):
        return True

    for k, v in exp_args.items():
        if k not in act_args or act_args[k] != v:
            return False
    return True


def score_trajectory(
    actual_calls: list[dict[str, Any]],
    expected_calls: list[dict[str, Any]],
    match_type: TrajectoryMatchType = TrajectoryMatchType.IN_ORDER,
) -> float:
    if not expected_calls:
        return 1.0 if not actual_calls else 0.5

    if match_type == TrajectoryMatchType.EXACT:
        if len(actual_calls) != len(expected_calls):
            return 0.0
        for act, exp in zip(actual_calls, expected_calls, strict=True):
            if not _call_matches(act, exp):
                return 0.0
        return 1.0

    if match_type == TrajectoryMatchType.IN_ORDER:
        idx = 0
        matched = 0
        for exp in expected_calls:
            while idx < len(actual_calls):
                if _call_matches(actual_calls[idx], exp):
                    matched += 1
                    idx += 1
                    break
                idx += 1
        return matched / len(expected_calls)

    if match_type == TrajectoryMatchType.ANY_ORDER:
        consumed: set[int] = set()
        matched = 0
        for exp in expected_calls:
            for i, act in enumerate(actual_calls):
                if i not in consumed and _call_matches(act, exp):
                    consumed.add(i)
                    matched += 1
                    break
        return matched / len(expected_calls)

    return 0.0


def score_keywords(text: str, expected_keywords: list[str]) -> float:
    if not expected_keywords:
        return 1.0
    text_lower = text.lower()
    matched = sum(1 for kw in expected_keywords if kw.lower() in text_lower)
    return matched / len(expected_keywords)


def evaluate_case(
    case: EvalCase,
    actual_calls: list[dict[str, Any]],
    response_text: str,
) -> CaseResult:
    traj_score = score_trajectory(
        actual_calls, case.expected_tool_trajectory, case.trajectory_match_type
    )
    kw_score = score_keywords(response_text, case.expected_response_keywords)
    total = round(0.7 * traj_score + 0.3 * kw_score, 4)
    passed = total >= 0.8
    details = f"Trajectory: {traj_score:.2f}, Keywords: {kw_score:.2f}"
    return CaseResult(
        case_id=case.eval_case_id,
        passed=passed,
        trajectory_score=traj_score,
        keyword_score=kw_score,
        total_score=total,
        executed_calls=actual_calls,
        details=details,
    )


def evaluate_set(
    evalset: EvalSet,
    results: list[tuple[EvalCase, list[dict[str, Any]], str]],
) -> EvalReport:
    case_results: list[CaseResult] = []
    total_traj = 0.0
    total_score = 0.0
    passed_count = 0

    for case, calls, resp in results:
        res = evaluate_case(case, calls, resp)
        case_results.append(res)
        total_traj += res.trajectory_score
        total_score += res.total_score
        if res.passed:
            passed_count += 1

    count = len(case_results) or 1
    return EvalReport(
        evalset_name=evalset.name,
        total_cases=len(case_results),
        passed_cases=passed_count,
        failed_cases=len(case_results) - passed_count,
        avg_trajectory_score=round(total_traj / count, 4),
        avg_total_score=round(total_score / count, 4),
        case_results=case_results,
    )


def load_evalset(path: Path) -> EvalSet:
    data = json.loads(path.read_text(encoding="utf-8"))
    name = data.get("name", path.stem)
    desc = data.get("description", "")
    cases_raw = data.get("eval_cases", [])
    cases: list[EvalCase] = []
    for c in cases_raw:
        mt_raw = c.get("trajectory_match_type", "IN_ORDER")
        try:
            mt = TrajectoryMatchType(mt_raw)
        except ValueError:
            mt = TrajectoryMatchType.IN_ORDER
        cases.append(
            EvalCase(
                eval_case_id=c.get("eval_case_id", ""),
                description=c.get("description", ""),
                prompt=c.get("prompt", ""),
                expected_tool_trajectory=c.get("expected_tool_trajectory", []),
                trajectory_match_type=mt,
                expected_response_keywords=c.get("expected_response_keywords", []),
                rubric=c.get("rubric", ""),
            )
        )
    return EvalSet(name=name, description=desc, eval_cases=cases)
