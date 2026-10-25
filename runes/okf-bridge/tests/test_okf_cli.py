"""Integration tests for okf-bridge CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mvge_runes_okf_bridge.cli import app
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture()
def isolated_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fake = tmp_path / "fake-global"
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(fake))
    return fake


def test_cli_init_and_validate(tmp_path: Path) -> None:
    target = tmp_path / ".okf"

    # 1. Run init
    res = runner.invoke(app, ["init", str(target), "--title", "CLI Test Bundle"])
    assert res.exit_code == 0
    assert "OK: Initialized conformant OKF v0.2 bundle" in res.output
    assert (target / "index.md").is_file()
    assert (target / "log.md").is_file()
    assert (target / "getting-started.md").is_file()

    # 2. Run status
    res_status = runner.invoke(app, ["status", str(target)])
    assert res_status.exit_code == 0
    assert "Total Concepts:  1" in res_status.output
    assert "Human-Reviewed:  1" in res_status.output

    # 3. Run validate
    res_val = runner.invoke(app, ["validate", str(target)])
    assert res_val.exit_code == 0
    assert "OK: OKF bundle is fully conformant" in res_val.output

    # 4. Run validate with --json
    res_val_json = runner.invoke(app, ["validate", str(target), "--json"])
    assert res_val_json.exit_code == 0
    val_data = json.loads(res_val_json.output)
    assert val_data["valid"] is True
    assert val_data["concepts"] == 1

    # 5. Run search
    res_search = runner.invoke(
        app, ["search", "Getting Started", "--bundle", str(target)]
    )
    assert res_search.exit_code == 0
    assert "FOUND: 1 matching concepts" in res_search.output

    # 6. Run graph
    out_html = tmp_path / "custom_viz.html"
    res_graph = runner.invoke(app, ["graph", str(target), "-o", str(out_html)])
    assert res_graph.exit_code == 0
    assert "OK: Rendered knowledge graph" in res_graph.output
    assert out_html.is_file()

    # 7. Run migrate (should report already conformant)
    res_mig = runner.invoke(app, ["migrate", str(target)])
    assert res_mig.exit_code == 0
    assert "OK: All files already conform" in res_mig.output


def test_cli_missing_directory(tmp_path: Path) -> None:
    bad_dir = tmp_path / "non_existent"
    res = runner.invoke(app, ["status", str(bad_dir)])
    assert res.exit_code != 0
    assert "MISSING:" in res.output

    res2 = runner.invoke(app, ["validate", str(bad_dir)])
    assert res2.exit_code != 0
    assert "MISSING:" in res2.output

    res3 = runner.invoke(app, ["graph", str(bad_dir)])
    assert res3.exit_code != 0
    assert "MISSING:" in res3.output

    res4 = runner.invoke(app, ["migrate", str(bad_dir)])
    assert res4.exit_code != 0
    assert "MISSING:" in res4.output

    res5 = runner.invoke(app, ["search", "test", "--bundle", str(bad_dir)])
    assert res5.exit_code != 0
    assert "MISSING:" in res5.output


def test_cli_validation_failure(tmp_path: Path) -> None:
    bad_bundle = tmp_path / "bad_okf"
    bad_bundle.mkdir()
    (bad_bundle / "bad.md").write_text(
        "---\ntitle: Missing Type\n---\nBody", encoding="utf-8"
    )

    # Regular validation
    res = runner.invoke(app, ["validate", str(bad_bundle)])
    assert res.exit_code != 0
    assert "FAIL:" in res.output

    # JSON validation
    res_json = runner.invoke(app, ["validate", str(bad_bundle), "--json"])
    assert res_json.exit_code != 0
    data = json.loads(res_json.output)
    assert data["valid"] is False
    assert len(data["errors"]) > 0


def test_cli_init_collision(tmp_path: Path) -> None:
    target = tmp_path / ".okf"
    runner.invoke(app, ["init", str(target)])
    res_collision = runner.invoke(app, ["init", str(target)])
    assert res_collision.exit_code != 0
    assert "already contains OKF files" in res_collision.output


def test_cli_search_no_results(tmp_path: Path, isolated_global: Path) -> None:
    target = tmp_path / ".okf"
    runner.invoke(app, ["init", str(target)])
    res = runner.invoke(app, ["search", "nonexistentquery123", "--bundle", str(target)])
    assert res.exit_code == 0
    assert "MISSING: No concepts found" in res.output


def test_cli_init_defaults_to_agents_knowledge(
    tmp_path: Path, isolated_global: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(app, ["init"])
    assert res.exit_code == 0
    target = tmp_path / ".agents" / "knowledge"
    assert (target / "index.md").is_file()
    assert (target / "getting-started.md").is_file()

    # Default status/validate/search discover the workspace layer
    res_status = runner.invoke(app, ["status"])
    assert res_status.exit_code == 0
    assert "Total Concepts:  1" in res_status.output

    res_val = runner.invoke(app, ["validate"])
    assert res_val.exit_code == 0
    assert "OK: OKF bundle is fully conformant" in res_val.output


def test_cli_no_bundle_anywhere(
    tmp_path: Path, isolated_global: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(app, ["status"])
    assert res.exit_code != 0
    assert "MISSING:" in res.output
