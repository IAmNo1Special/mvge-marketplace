from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from mvge_runes_steering_bridge.cli import steering_app
from typer.testing import CliRunner

runner = CliRunner()


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_cli_steering_callback_help() -> None:
    res = runner.invoke(steering_app, [])
    assert res.exit_code == 0
    assert "Inspect, validate, and manage repository steering" in res.output


def test_cli_steering_status(tmp_path: Path) -> None:
    _write(tmp_path / "AGENTS.md", "Project rules")
    global_dir = tmp_path / "global"
    _write(global_dir / "AGENTS.md", "Global rules")

    res = runner.invoke(
        steering_app,
        [
            "status",
            "--cwd",
            str(tmp_path),
            "--global-dir",
            str(global_dir),
        ],
    )
    assert res.exit_code == 0
    assert "OK: Repository steering resolved:" in res.output
    assert "- Project Rules: AGENTS.md" in res.output
    assert "Global Rules:" in res.output


def test_cli_steering_status_missing(tmp_path: Path) -> None:
    empty_repo = tmp_path / "empty_repo"
    empty_repo.mkdir()
    res = runner.invoke(
        steering_app,
        [
            "status",
            "--cwd",
            str(empty_repo),
            "--global-dir",
            str(tmp_path / "empty_global"),
        ],
    )
    assert res.exit_code == 0
    assert "MISSING: No AGENTS.md steering files resolved." in res.output


def test_cli_steering_show(tmp_path: Path) -> None:
    _write(tmp_path / "AGENTS.md", "Show me the rules.")
    res = runner.invoke(
        steering_app,
        [
            "show",
            "--cwd",
            str(tmp_path),
            "--global-dir",
            str(tmp_path / "empty_global"),
        ],
    )
    assert res.exit_code == 0
    assert "<project_context>" in res.output
    assert "Show me the rules." in res.output


def test_cli_steering_show_missing(tmp_path: Path) -> None:
    empty_repo = tmp_path / "empty_repo"
    empty_repo.mkdir()
    res = runner.invoke(
        steering_app,
        [
            "show",
            "--cwd",
            str(empty_repo),
            "--global-dir",
            str(tmp_path / "empty_global"),
        ],
    )
    assert res.exit_code == 0
    assert "MISSING:" in res.output


def test_cli_steering_validate_ok(tmp_path: Path) -> None:
    _write(tmp_path / "AGENTS.md", "Valid rules.")
    res = runner.invoke(
        steering_app,
        [
            "validate",
            "--cwd",
            str(tmp_path),
            "--global-dir",
            str(tmp_path / "empty_global"),
        ],
    )
    assert res.exit_code == 0
    assert "OK: 1 steering file(s) valid." in res.output


def test_cli_steering_validate_missing(tmp_path: Path) -> None:
    empty_repo = tmp_path / "empty_repo"
    empty_repo.mkdir()
    res = runner.invoke(
        steering_app,
        [
            "validate",
            "--cwd",
            str(empty_repo),
            "--global-dir",
            str(tmp_path / "empty_global"),
        ],
    )
    assert res.exit_code == 0
    assert "MISSING:" in res.output


def test_cli_steering_validate_fail_exits_nonzero(tmp_path: Path) -> None:
    _write(tmp_path / "AGENTS.md", "Valid rules.")
    with patch("mvge_runes_steering_bridge.cli.SteeringBridgeRune") as mock_cls:
        mock_cls.return_value.validate.return_value = [
            "FAIL: Project steering file is empty: AGENTS.md"
        ]
        mock_cls.return_value.state.section = ""
        res = runner.invoke(
            steering_app,
            [
                "validate",
                "--cwd",
                str(tmp_path),
                "--global-dir",
                str(tmp_path / "empty_global"),
            ],
        )
    assert res.exit_code == 1
    assert "FAIL:" in res.output
