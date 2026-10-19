from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from mvgeos_runes.rune_api import RuneAPI
from mvgeos_runes_eval_bridge.cli import app
from mvgeos_runes_eval_bridge.evaluator import (
    evaluate_case,
    evaluate_set,
    load_evalset,
    score_keywords,
    score_trajectory,
)
from mvgeos_runes_eval_bridge.exporter import export_rl_data, export_tome_to_records
from mvgeos_runes_eval_bridge.rune import rune_factory
from mvgeos_runes_eval_bridge.schema import EvalCase, EvalSet, TrajectoryMatchType
from typer.testing import CliRunner

runner = CliRunner()


def test_eval_bridge_registration() -> None:
    api = MagicMock(spec=RuneAPI)
    registered_commands: dict[str, Any] = {}

    def register_command(name: str, description: str = "", handler: Any = None) -> None:
        registered_commands[name] = handler

    api.register_command = register_command
    rune_factory(api)
    assert "eval" in registered_commands


def test_score_trajectory_exact() -> None:
    expected = [
        {"spell_name": "read", "arguments": {"path": "a.txt"}},
        {"spell_name": "write", "arguments": {"path": "b.txt"}},
    ]
    # Exact match
    act_exact = [
        {"spell_name": "read", "arguments": {"path": "a.txt", "extra": 1}},
        {"spell_name": "write", "arguments": {"path": "b.txt"}},
    ]
    assert score_trajectory(act_exact, expected, TrajectoryMatchType.EXACT) == 1.0

    # Wrong order
    act_rev = [
        {"spell_name": "write", "arguments": {"path": "b.txt"}},
        {"spell_name": "read", "arguments": {"path": "a.txt"}},
    ]
    assert score_trajectory(act_rev, expected, TrajectoryMatchType.EXACT) == 0.0

    # Extra call
    act_extra = [
        *act_exact,
        {"spell_name": "list_files", "arguments": {}},
    ]
    assert score_trajectory(act_extra, expected, TrajectoryMatchType.EXACT) == 0.0


def test_score_trajectory_in_order() -> None:
    expected = [
        {"spell_name": "read", "arguments": {"path": "a.txt"}},
        {"spell_name": "write", "arguments": {"path": "b.txt"}},
    ]
    # In order with interleaved calls
    act_interleaved = [
        {"spell_name": "list_files", "arguments": {}},
        {"spell_name": "read", "arguments": {"path": "a.txt"}},
        {"spell_name": "grep", "arguments": {}},
        {"spell_name": "write", "arguments": {"path": "b.txt"}},
    ]
    assert (
        score_trajectory(act_interleaved, expected, TrajectoryMatchType.IN_ORDER) == 1.0
    )

    # Partial match
    act_partial = [
        {"spell_name": "read", "arguments": {"path": "a.txt"}},
    ]
    assert score_trajectory(act_partial, expected, TrajectoryMatchType.IN_ORDER) == 0.5


def test_score_trajectory_any_order() -> None:
    expected = [
        {"spell_name": "read", "arguments": {"path": "a.txt"}},
        {"spell_name": "write", "arguments": {"path": "b.txt"}},
    ]
    act_rev = [
        {"spell_name": "write", "arguments": {"path": "b.txt"}},
        {"spell_name": "read", "arguments": {"path": "a.txt"}},
    ]
    assert score_trajectory(act_rev, expected, TrajectoryMatchType.ANY_ORDER) == 1.0


def test_score_keywords() -> None:
    assert score_keywords("Hello world, agent done", ["hello", "agent"]) == 1.0
    assert score_keywords("Hello world, agent done", ["hello", "missing"]) == 0.5
    assert score_keywords("Any text", []) == 1.0


def test_evaluate_case_and_set() -> None:
    case1 = EvalCase(
        eval_case_id="case_1",
        expected_tool_trajectory=[
            {"spell_name": "read", "arguments": {"path": "foo.py"}}
        ],
        trajectory_match_type=TrajectoryMatchType.IN_ORDER,
        expected_response_keywords=["success"],
    )
    act_calls = [{"spell_name": "read", "arguments": {"path": "foo.py"}}]
    resp = "Execution success"

    res = evaluate_case(case1, act_calls, resp)
    assert res.passed is True
    assert res.total_score >= 0.8

    evalset = EvalSet(name="suite", eval_cases=[case1])
    report = evaluate_set(evalset, [(case1, act_calls, resp)])
    assert report.total_cases == 1
    assert report.passed_cases == 1
    assert report.avg_total_score >= 0.8


def test_load_evalset(tmp_path: Path) -> None:
    evalset_file = tmp_path / "test.evalset.json"
    evalset_file.write_text(
        json.dumps(
            {
                "name": "mock_suite",
                "eval_cases": [
                    {
                        "eval_case_id": "c1",
                        "prompt": "do something",
                        "expected_tool_trajectory": [{"spell_name": "bash"}],
                        "trajectory_match_type": "EXACT",
                        "expected_response_keywords": ["done"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    loaded = load_evalset(evalset_file)
    assert loaded.name == "mock_suite"
    assert len(loaded.eval_cases) == 1
    assert loaded.eval_cases[0].trajectory_match_type == TrajectoryMatchType.EXACT


def test_export_rl_sharegpt_and_atropos(tmp_path: Path) -> None:
    tome_path = tmp_path / "tome123.jsonl"
    lines = [
        json.dumps({"role": "user", "content": [{"type": "text", "text": "Hello"}]}),
        json.dumps(
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "text": "planning"},
                    {"type": "text", "text": "I will read"},
                    {
                        "type": "spell_cast",
                        "spell_name": "read",
                        "arguments": {"path": "x"},
                    },
                ],
            }
        ),
        json.dumps(
            {
                "role": "spellResult",
                "content": [{"type": "text", "text": "file content"}],
            }
        ),
    ]
    tome_path.write_text("\n".join(lines), encoding="utf-8")

    # ShareGPT export
    sharegpt = export_tome_to_records(tome_path, "sharegpt")
    assert sharegpt["id"] == "tome123"
    convs = sharegpt["conversations"]
    assert len(convs) == 3
    assert convs[0]["from"] == "human"
    assert convs[0]["value"] == "Hello"
    assert convs[1]["from"] == "gpt"

    # Atropos export
    atropos = export_tome_to_records(tome_path, "atropos")
    assert atropos["session_id"] == "tome123"
    assert len(atropos["turns"]) == 3
    assert atropos["turns"][1]["tool_calls"][0]["spell_name"] == "read"

    # Export to file
    out_file = tmp_path / "exported.json"
    rendered = export_rl_data(tome_path, output_path=out_file, export_format="sharegpt")
    assert out_file.exists()
    assert "tome123" in rendered


def test_cli_eval_run_and_export_rl(tmp_path: Path) -> None:
    evalset_file = tmp_path / "cli.evalset.json"
    evalset_file.write_text(
        json.dumps(
            {
                "name": "cli_suite",
                "eval_cases": [
                    {
                        "eval_case_id": "cli_c1",
                        "prompt": "test prompt",
                        "expected_tool_trajectory": [{"spell_name": "read"}],
                        "expected_response_keywords": ["ok"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report_out = tmp_path / "report.json"
    res = runner.invoke(
        app, ["run", str(evalset_file), "--output", str(report_out), "--verbose"]
    )
    assert res.exit_code == 0
    assert "Evaluation Set: cli_suite" in res.stdout
    assert report_out.exists()

    tome_path = tmp_path / "session_test.jsonl"
    tome_path.write_text(
        json.dumps({"role": "user", "content": "test prompt"}) + "\n",
        encoding="utf-8",
    )
    rl_out = tmp_path / "rl.json"
    res_rl = runner.invoke(app, ["export-rl", str(tome_path), "--output", str(rl_out)])
    assert res_rl.exit_code == 0
    assert rl_out.exists()
