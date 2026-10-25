from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import typer
from mvge.core import sessions_dir

from mvgeos_runes_eval_bridge.evaluator import evaluate_set, load_evalset
from mvgeos_runes_eval_bridge.exporter import export_rl_data
from mvgeos_runes_eval_bridge.schema import EvalReport

app = typer.Typer(
    name="eval",
    help="Evaluation harness and RL data exporter for MvgeOS",
)


@app.command("run")
def eval_run_cmd(
    evalset_path: str = typer.Argument(..., help="Path to .evalset.json file"),
    output: str = typer.Option(
        None, "--output", "-o", help="Optional output report path"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Verbose per-case output"
    ),
) -> None:
    """Run an evaluation dataset against mock or captured trajectories."""
    path = Path(evalset_path)
    if not path.exists():
        typer.echo(f"Error: evalset file '{evalset_path}' not found.", err=True)
        raise typer.Exit(code=1)

    evalset = load_evalset(path)
    # Replay cases against empty or fixture trajectory
    case_results = []
    for case in evalset.eval_cases:
        # For dry-run evaluation, evaluate against expected trajectory
        case_results.append(
            (
                case,
                case.expected_tool_trajectory,
                " ".join(case.expected_response_keywords),
            )
        )

    report: EvalReport = evaluate_set(evalset, case_results)

    typer.echo(f"Evaluation Set: {report.evalset_name}")
    typer.echo(f"Total Cases: {report.total_cases}")
    typer.echo(f"Passed: {report.passed_cases}/{report.total_cases}")
    typer.echo(f"Avg Trajectory Score: {report.avg_trajectory_score * 100:.1f}%")
    typer.echo(f"Avg Total Score: {report.avg_total_score * 100:.1f}%")

    if verbose:
        for res in report.case_results:
            status = "PASS" if res.passed else "FAIL"
            typer.echo(
                f"  [{status}] {res.case_id} (score={res.total_score:.2f}): {res.details}"
            )

    if output:
        out_path = Path(output)
        report_dict = {
            "evalset_name": report.evalset_name,
            "total_cases": report.total_cases,
            "passed_cases": report.passed_cases,
            "failed_cases": report.failed_cases,
            "avg_trajectory_score": report.avg_trajectory_score,
            "avg_total_score": report.avg_total_score,
            "case_results": [
                {
                    "case_id": c.case_id,
                    "passed": c.passed,
                    "trajectory_score": c.trajectory_score,
                    "keyword_score": c.keyword_score,
                    "total_score": c.total_score,
                    "details": c.details,
                }
                for c in report.case_results
            ],
        }
        out_path.write_text(json.dumps(report_dict, indent=2), encoding="utf-8")
        typer.echo(f"Report saved to {output}")


@app.command("export-rl")
def eval_export_rl_cmd(
    tome_id: str = typer.Argument(..., help="Tome ID or path to Tome .jsonl file"),
    output: str = typer.Option(
        None, "--output", "-o", help="Output path for RL records"
    ),
    format_type: str = typer.Option(
        "sharegpt", "--format", "-f", help="Format: sharegpt or atropos"
    ),
) -> None:
    """Export a session Tome to ShareGPT or Atropos JSONL for RL training."""
    path = Path(tome_id)
    if not path.exists():
        # Check sessions_dir()
        home_tome = sessions_dir() / f"{tome_id}.jsonl"
        if home_tome.exists():
            path = home_tome
        else:
            typer.echo(f"Error: Tome file '{tome_id}' not found.", err=True)
            raise typer.Exit(code=1)

    export_fmt: Literal["sharegpt", "atropos"] = "sharegpt"
    if format_type.lower() == "atropos":
        export_fmt = "atropos"

    out_p = Path(output) if output else None
    rendered = export_rl_data(path, output_path=out_p, export_format=export_fmt)
    if output:
        typer.echo(f"Exported {export_fmt} dataset to {output}")
    else:
        typer.echo(rendered)
