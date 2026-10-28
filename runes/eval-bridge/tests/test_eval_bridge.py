from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from mvge.runes.rune_api import RuneAPI
from mvge_runes_eval_bridge.cli import app
from mvge_runes_eval_bridge.evaluator import (
    evaluate_case,
    evaluate_set,
    load_evalset,
    score_keywords,
    score_trajectory,
)
from mvge_runes_eval_bridge.exporter import export_rl_data, export_tome_to_records
from mvge_runes_eval_bridge.rune import rune_factory
from mvge_runes_eval_bridge.schema import EvalCase, EvalSet, TrajectoryMatchType
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


def test_eval_factory_registers_app_handler() -> None:
    api = MagicMock(spec=RuneAPI)
    registered: dict[str, Any] = {}
    api.register_command = lambda name, description="", handler=None: (
        registered.__setitem__(name, handler)
    )
    result = rune_factory(api)
    assert result is app
    assert registered["eval"] is app


def test_score_trajectory_empty_expected_and_partial() -> None:
    assert score_trajectory([], [], TrajectoryMatchType.IN_ORDER) == 1.0
    assert (
        score_trajectory([{"spell_name": "read"}], [], TrajectoryMatchType.EXACT) == 0.5
    )
    assert (
        score_trajectory([], [{"spell_name": "read"}], TrajectoryMatchType.IN_ORDER)
        == 0.0
    )
    # ANY_ORDER consumes each actual call at most once
    assert (
        score_trajectory(
            [{"spell_name": "read"}],
            [{"spell_name": "read"}, {"spell_name": "read"}],
            TrajectoryMatchType.ANY_ORDER,
        )
        == 0.5
    )


def test_score_trajectory_name_alias_and_non_dict_args() -> None:
    assert (
        score_trajectory(
            [{"name": "read", "arguments": "not-a-dict"}],
            [{"spell_name": "read", "arguments": {"path": "x"}}],
            TrajectoryMatchType.EXACT,
        )
        == 1.0
    )
    assert (
        score_trajectory(
            [{"spell_name": "read", "arguments": {}}],
            [{"spell_name": "read", "arguments": {"path": "x"}}],
            TrajectoryMatchType.EXACT,
        )
        == 0.0
    )


def test_score_keywords_empty_text() -> None:
    assert score_keywords("", ["hello"]) == 0.0
    assert score_keywords("HELLO WORLD", ["hello", "world"]) == 1.0


def test_evaluate_case_failing_and_details() -> None:
    case = EvalCase(
        eval_case_id="fail_1",
        expected_tool_trajectory=[{"spell_name": "read"}],
        trajectory_match_type=TrajectoryMatchType.EXACT,
        expected_response_keywords=["done"],
    )
    res = evaluate_case(case, [{"spell_name": "read"}], "unrelated reply")
    assert res.passed is False
    assert res.trajectory_score == 1.0
    assert res.keyword_score == 0.0
    assert res.total_score == 0.7
    assert res.details == "Trajectory: 1.00, Keywords: 0.00"
    assert res.case_id == "fail_1"


def test_evaluate_set_empty_and_mixed() -> None:
    empty = evaluate_set(EvalSet(name="empty"), [])
    assert empty.total_cases == 0
    assert empty.passed_cases == 0
    assert empty.failed_cases == 0
    assert empty.avg_total_score == 0.0
    assert empty.avg_trajectory_score == 0.0

    passing = EvalCase(eval_case_id="p", expected_tool_trajectory=[])
    failing = EvalCase(
        eval_case_id="f",
        expected_tool_trajectory=[{"spell_name": "read"}],
        expected_response_keywords=["done"],
    )
    report = evaluate_set(
        EvalSet(name="mixed"),
        [(passing, [], ""), (failing, [], "nothing")],
    )
    assert report.total_cases == 2
    assert report.passed_cases == 1
    assert report.failed_cases == 1
    assert report.avg_total_score == 0.5


def test_load_evalset_defaults_and_bad_match_type(tmp_path: Path) -> None:
    path = tmp_path / "suite.evalset.json"
    path.write_text(
        json.dumps(
            {
                "eval_cases": [
                    {"eval_case_id": "c1", "trajectory_match_type": "BOGUS"},
                ]
            }
        ),
        encoding="utf-8",
    )
    loaded = load_evalset(path)
    assert loaded.name == "suite.evalset"
    assert loaded.description == ""
    case = loaded.eval_cases[0]
    assert case.trajectory_match_type == TrajectoryMatchType.IN_ORDER
    assert case.prompt == ""
    assert case.expected_tool_trajectory == []
    assert case.rubric == ""


def test_exporter_skips_bad_lines_and_maps_roles(tmp_path: Path) -> None:
    tome_path = tmp_path / "tome9.jsonl"
    tome_path.write_text(
        "\n".join(
            [
                "not json at all",
                json.dumps({"role": "user", "content": "hi"}),
                "",
                json.dumps(
                    {
                        "role": "spellResult",
                        "content": [{"type": "text", "text": "out"}],
                    }
                ),
                json.dumps({"role": "mystery", "content": "???"}),
            ]
        ),
        encoding="utf-8",
    )
    sharegpt = export_tome_to_records(tome_path, "sharegpt")
    assert sharegpt["conversations"] == [
        {"from": "human", "value": "hi"},
        {"from": "system", "value": "Tool output: out"},
    ]
    atropos = export_tome_to_records(tome_path, "atropos")
    assert atropos["turns"][0] == {"role": "user", "text": "hi"}
    assert "tool_calls" not in atropos["turns"][0]
    assert atropos["turns"][2]["role"] == "mystery"


def test_export_rl_data_without_output_returns_rendered(tmp_path: Path) -> None:
    tome_path = tmp_path / "t.jsonl"
    tome_path.write_text(
        json.dumps({"role": "user", "content": "hello"}) + "\n", encoding="utf-8"
    )
    rendered = export_rl_data(tome_path)
    assert json.loads(rendered)["id"] == "t"
    assert not list(tmp_path.glob("*.json"))


def test_cli_run_missing_file_and_verbose_report(tmp_path: Path) -> None:
    missing = runner.invoke(app, ["run", str(tmp_path / "nope.evalset.json")])
    assert missing.exit_code == 1
    assert "not found" in missing.output

    evalset_file = tmp_path / "v.evalset.json"
    evalset_file.write_text(
        json.dumps(
            {
                "name": "v_suite",
                "eval_cases": [
                    {
                        "eval_case_id": "v1",
                        "prompt": "p",
                        "expected_tool_trajectory": [{"spell_name": "read"}],
                        "expected_response_keywords": ["ok"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    report_out = tmp_path / "v_report.json"
    res = runner.invoke(
        app, ["run", str(evalset_file), "--output", str(report_out), "--verbose"]
    )
    assert res.exit_code == 0
    assert "[PASS] v1" in res.stdout
    assert "Avg Total Score: 100.0%" in res.stdout
    report = json.loads(report_out.read_text(encoding="utf-8"))
    assert report["passed_cases"] == 1
    assert report["failed_cases"] == 0
    assert report["case_results"][0]["case_id"] == "v1"
    assert report["case_results"][0]["total_score"] == 1.0


def test_cli_export_rl_missing_and_atropos_stdout(tmp_path: Path) -> None:
    missing = runner.invoke(app, ["export-rl", "no-such-tome"])
    assert missing.exit_code == 1
    assert "not found" in missing.output

    tome_path = tmp_path / "s.jsonl"
    tome_path.write_text(
        json.dumps({"role": "user", "content": "hello"}) + "\n", encoding="utf-8"
    )
    res = runner.invoke(app, ["export-rl", str(tome_path), "--format", "atropos"])
    assert res.exit_code == 0
    assert json.loads(res.stdout)["session_id"] == "s"
