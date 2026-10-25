from __future__ import annotations

from pathlib import Path

import typer

from mvge_runes_steering_bridge.rune import SteeringBridgeRune

steering_app = typer.Typer(
    name="steering",
    help="Inspect, validate, and manage repository steering (AGENTS.md).",
)


def _resolve_rune(cwd: str | None, global_dir: str | None) -> SteeringBridgeRune:
    rune = SteeringBridgeRune()
    rune.refresh_steering(
        cwd=Path(cwd).expanduser() if cwd else None,
        global_dir=Path(global_dir).expanduser() if global_dir else None,
    )
    return rune


@steering_app.callback(invoke_without_command=True)
def steering_callback(ctx: typer.Context) -> None:
    """Inspect repository steering (AGENTS.md, .agents protocol)."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@steering_app.command("status")
def steering_status(
    cwd: str = typer.Option(None, "--cwd", help="Working directory to inspect"),
    global_dir: str = typer.Option(
        None, "--global-dir", help="Global ~/.agents directory override"
    ),
) -> None:
    """Show resolved global, workspace, and subpackage steering files."""
    rune = _resolve_rune(cwd, global_dir)
    for line in rune.status_lines():
        typer.echo(line)


@steering_app.command("show")
def steering_show(
    cwd: str = typer.Option(None, "--cwd", help="Working directory to inspect"),
    global_dir: str = typer.Option(
        None, "--global-dir", help="Global ~/.agents directory override"
    ),
) -> None:
    """Print the full steering section injected into the prompt."""
    rune = _resolve_rune(cwd, global_dir)
    if not rune.state.section:
        typer.echo("MISSING: No steering section resolved.")
        return
    typer.echo(rune.state.section)


@steering_app.command("validate")
def steering_validate(
    cwd: str = typer.Option(None, "--cwd", help="Working directory to inspect"),
    global_dir: str = typer.Option(
        None, "--global-dir", help="Global ~/.agents directory override"
    ),
) -> None:
    """Validate resolved AGENTS.md steering files are non-empty."""
    rune = _resolve_rune(cwd, global_dir)
    report = rune.validate()
    for line in report:
        typer.echo(line)
    if any(line.startswith("FAIL:") for line in report):
        raise typer.Exit(1)
