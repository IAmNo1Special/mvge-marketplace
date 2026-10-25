"""Integration tests for adr-bridge CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

from mvge_runes_adr_bridge.cli import app
from typer.testing import CliRunner

runner = CliRunner()


def test_cli_adr_full_flow(tmp_path: Path) -> None:
    # 1. New ADR
    res_new = runner.invoke(
        app,
        [
            "new",
            "Microkernel Design",
            "--context",
            "Boundaries",
            "--dir",
            str(tmp_path),
        ],
    )
    assert res_new.exit_code == 0
    assert "OK: Created new ADR" in res_new.output

    # 2. List ADRs
    res_list = runner.invoke(app, ["list", str(tmp_path)])
    assert res_list.exit_code == 0
    assert "FOUND: 1 Architectural Decision Records" in res_list.output
    assert "Microkernel Design" in res_list.output

    # 3. List ADRs with --json
    res_list_json = runner.invoke(app, ["list", str(tmp_path), "--json"])
    assert res_list_json.exit_code == 0
    data = json.loads(res_list_json.output)
    assert len(data) == 1
    assert data[0]["number"] == 1

    # 4. Get ADR
    res_get = runner.invoke(app, ["get", "1", "--dir", str(tmp_path)])
    assert res_get.exit_code == 0
    assert "ADR 0001: Microkernel Design" in res_get.output

    # 5. Get ADR with --json
    res_get_json = runner.invoke(app, ["get", "1", "--dir", str(tmp_path), "--json"])
    assert res_get_json.exit_code == 0
    get_data = json.loads(res_get_json.output)
    assert get_data["number"] == 1
    assert get_data["title"] == "Microkernel Design"

    # 6. Lint ADRs
    res_lint = runner.invoke(app, ["lint", str(tmp_path)])
    assert res_lint.exit_code == 0
    assert "OK: All 1 ADRs conform to MADR 3.0" in res_lint.output

    # 7. Lint ADRs with --json
    res_lint_json = runner.invoke(app, ["lint", str(tmp_path), "--json"])
    assert res_lint_json.exit_code == 0
    lint_data = json.loads(res_lint_json.output)
    assert lint_data["valid"] is True

    # 8. Sync index
    res_sync = runner.invoke(app, ["sync", str(tmp_path)])
    assert res_sync.exit_code == 0
    assert "OK: Synchronized" in res_sync.output
    assert (tmp_path / "docs" / "adr" / "README.md").is_file()


def test_cli_get_missing_adr(tmp_path: Path) -> None:
    res = runner.invoke(app, ["get", "999", "--dir", str(tmp_path)])
    assert res.exit_code != 0
    assert "MISSING:" in res.output


def test_cli_lint_failures(tmp_path: Path) -> None:
    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "bad-adr.md").write_text("# Invalid ADR\n", encoding="utf-8")

    res = runner.invoke(app, ["lint", str(tmp_path)])
    assert res.exit_code != 0
    assert "FAIL:" in res.output

    res_json = runner.invoke(app, ["lint", str(tmp_path), "--json"])
    assert res_json.exit_code != 0
    data = json.loads(res_json.output)
    assert data["valid"] is False
    assert len(data["errors"]) > 0
