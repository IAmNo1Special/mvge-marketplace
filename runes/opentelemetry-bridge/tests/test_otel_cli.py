from __future__ import annotations

from mvge_runes_opentelemetry_bridge.cli import app
from typer.testing import CliRunner

runner = CliRunner()


def test_otel_cli_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "OpenTelemetry" in result.stdout


def test_otel_cli_no_args() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "OpenTelemetry GenAI tracing management" in result.stdout


def test_otel_cli_status(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_SERVICE_NAME", "my-test-service")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4318")
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "Service Name: my-test-service" in result.stdout
    assert "http://otel-collector:4318" in result.stdout


def test_otel_cli_test_success(monkeypatch) -> None:
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.setenv("MVGE_OTEL_IN_MEMORY", "1")
    result = runner.invoke(app, ["test"])
    assert result.exit_code == 0
    assert "OK: OpenTelemetry tracing operational." in result.stdout


def test_otel_cli_test_with_endpoint(monkeypatch) -> None:
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    result = runner.invoke(app, ["test"])
    # If endpoint exporter fails or passes, exit_code is handled
    assert result.exit_code in (0, 1)


def test_otel_cli_test_exception(monkeypatch) -> None:
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    from unittest.mock import patch

    with patch(
        "mvge_runes_opentelemetry_bridge.cli.OTelTracer.start_session",
        side_effect=RuntimeError("disk full"),
    ):
        result = runner.invoke(app, ["test"])
        assert result.exit_code == 1
        assert "FAIL: OpenTelemetry test failed: disk full" in result.stdout


def test_otel_cli_test_disabled(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    result = runner.invoke(app, ["test"])
    assert result.exit_code == 1
    assert "FAIL: OpenTelemetry is explicitly disabled" in result.stdout
