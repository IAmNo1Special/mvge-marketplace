"""Typer CLI interface for adr-bridge rune."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from mvge_runes_adr_bridge.parser import load_adrs
from mvge_runes_adr_bridge.scaffold import scaffold_new_adr
from mvge_runes_adr_bridge.sync import sync_adr_index
from mvge_runes_adr_bridge.validator import validate_adrs

app = typer.Typer(
    name="adr", help="Architectural Decision Records (MADR 3.0) management"
)


@app.command("list")
def adr_list_cmd(
    project_dir: str = typer.Argument(None, help="Path to project directory"),
    status: str = typer.Option(None, "--status", help="Filter by status"),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """List all Architectural Decision Records."""
    cwd = Path(project_dir) if project_dir else Path.cwd()
    adrs = load_adrs(cwd=cwd)

    if status:
        adrs = [a for a in adrs if a.status.lower() == status.lower()]

    if output_json:
        data = [
            {
                "number": a.number,
                "title": a.title,
                "status": a.status,
                "date": a.date,
                "filename": a.path.name,
            }
            for a in adrs
        ]
        typer.echo(json.dumps(data, indent=2))
        return

    if not adrs:
        typer.echo("MISSING: No ADRs found in docs/adr.")
        return

    typer.echo(f"FOUND: {len(adrs)} Architectural Decision Records:")
    for a in adrs:
        typer.echo(f"  ADR {a.number:04d}: {a.title} [{a.status}]")


@app.command("get")
def adr_get_cmd(
    number: int = typer.Argument(..., help="ADR number"),
    project_dir: str = typer.Option(None, "--dir", help="Path to project directory"),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Read a specific Architectural Decision Record by number."""
    cwd = Path(project_dir) if project_dir else Path.cwd()
    adrs = load_adrs(cwd=cwd)
    target_adr = next((a for a in adrs if a.number == number), None)

    if not target_adr:
        typer.echo(f"MISSING: ADR {number:04d} not found.")
        raise typer.Exit(1)

    if output_json:
        data = {
            "number": target_adr.number,
            "title": target_adr.title,
            "status": target_adr.status,
            "date": target_adr.date,
            "deciders": target_adr.deciders,
            "context": target_adr.context_and_problem_statement,
            "decision_outcome": target_adr.decision_outcome,
            "options": target_adr.considered_options,
            "consequences": target_adr.consequences,
        }
        typer.echo(json.dumps(data, indent=2))
        return

    typer.echo(f"ADR {target_adr.number:04d}: {target_adr.title}")
    typer.echo(f"  Status:   {target_adr.status}")
    typer.echo(f"  Date:     {target_adr.date or 'None'}")
    typer.echo(f"  Deciders: {target_adr.deciders or 'None'}")
    typer.echo("\nContext and Problem Statement:")
    typer.echo(f"  {target_adr.context_and_problem_statement[:300]}")
    typer.echo("\nDecision Outcome:")
    typer.echo(f"  {target_adr.decision_outcome[:300]}")


@app.command("lint")
def adr_lint_cmd(
    project_dir: str = typer.Argument(None, help="Path to project directory"),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Lint Architectural Decision Records for strict MADR 3.0 conformance."""
    cwd = Path(project_dir) if project_dir else Path.cwd()
    rep = validate_adrs(cwd=cwd)

    if output_json:
        data = {
            "valid": rep.valid,
            "errors": [
                {"filename": e.filename, "message": e.message} for e in rep.errors
            ],
            "warnings": [
                {"filename": w.filename, "message": w.message} for w in rep.warnings
            ],
            "records_checked": len(rep.adrs),
        }
        typer.echo(json.dumps(data, indent=2))
        if not rep.valid:
            raise typer.Exit(1)
        return

    for err in rep.errors:
        typer.echo(f"  FAIL: {err.filename}: {err.message}")
    for warn in rep.warnings:
        typer.echo(f"  WARN: {warn.filename}: {warn.message}")

    if rep.valid:
        typer.echo(f"OK: All {len(rep.adrs)} ADRs conform to MADR 3.0.")
    else:
        typer.echo(f"FAIL: Found {len(rep.errors)} errors in ADRs.")
        raise typer.Exit(1)


@app.command("sync")
def adr_sync_cmd(
    project_dir: str = typer.Argument(None, help="Path to project directory"),
) -> None:
    """Synchronize docs/adr/README.md index table."""
    cwd = Path(project_dir) if project_dir else Path.cwd()
    msg = sync_adr_index(cwd=cwd)
    typer.echo(msg)
    if msg.startswith("MISSING:"):
        raise typer.Exit(1)


@app.command("new")
def adr_new_cmd(
    title: str = typer.Argument(..., help="Title of the new ADR"),
    context: str = typer.Option("", "--context", help="Context and problem statement"),
    status: str = typer.Option(
        "proposed", "--status", help="Status (e.g. proposed, accepted)"
    ),
    deciders: str = typer.Option("", "--deciders", help="Deciders"),
    project_dir: str = typer.Option(None, "--dir", help="Path to project directory"),
) -> None:
    """Scaffold a new MADR 3.0 record with sequential numbering."""
    cwd = Path(project_dir) if project_dir else Path.cwd()
    created = scaffold_new_adr(
        title=title,
        cwd=cwd,
        context=context,
        status=status,
        deciders=deciders,
    )
    typer.echo(f"OK: Created new ADR at {created.resolve()}.")
