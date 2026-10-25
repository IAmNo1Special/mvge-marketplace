"""CLI commands for OpenTelemetry bridge rune."""

from __future__ import annotations

from pathlib import Path

import typer

from mvge_runes_opentelemetry_bridge.config import load_otel_config
from mvge_runes_opentelemetry_bridge.tracer import OTelTracer

app = typer.Typer(name="otel", help="OpenTelemetry GenAI tracing management")


@app.callback(invoke_without_command=True)
def otel_callback(ctx: typer.Context) -> None:
    """OpenTelemetry GenAI tracing management."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@app.command("status")
def otel_status() -> None:
    """Show current OpenTelemetry configuration status."""
    config = load_otel_config(cwd=Path.cwd())
    typer.echo("OpenTelemetry Tracing Status:")
    typer.echo(f"  Service Name: {config.service_name}")
    typer.echo(f"  Endpoint:     {config.endpoint or 'None (in-memory or disabled)'}")
    typer.echo(f"  Disabled:     {config.disabled}")
    typer.echo(f"  In-Memory:    {config.in_memory}")


@app.command("test")
def otel_test() -> None:
    """Test OpenTelemetry tracing pipeline."""
    config = load_otel_config(cwd=Path.cwd())
    if config.disabled:
        typer.echo(
            "FAIL: OpenTelemetry is explicitly disabled via config or environment."
        )
        raise typer.Exit(1)

    try:
        # Create a test tracer in-memory if no endpoint
        test_cfg = load_otel_config(cwd=Path.cwd())
        if not test_cfg.endpoint:
            test_cfg.in_memory = True
        tracer = OTelTracer(test_cfg)
        tracer.start_session("otel-test-session")
        tracer.start_turn(turn_id=1, model="test-model")
        tracer.start_tool("test_tool", "call-00")
        tracer.end_tool("call-00")
        tracer.end_turn()
        tracer.end_session()
        typer.echo("OK: OpenTelemetry tracing operational.")
    except Exception as exc:
        typer.echo(f"FAIL: OpenTelemetry test failed: {exc}")
        raise typer.Exit(1) from exc
