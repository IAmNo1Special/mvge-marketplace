"""Typer CLI for the pi-codec rune.

Two commands, mounted separately by the Mvge CLI (see manifest.json
"commands" and the root cli.py shim):

- ``pi-export``: one-way export of a Tome v1 session to a Pi-native v4 file.
- ``pi-validate``: thin read-only validation of a Pi session file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from mvge.tome.types import TomeVersionError

from mvge_runes_pi_codec.codec import PiSessionCodec
from mvge_runes_pi_codec.exporter import ExportError, export_tome_to_pi


@dataclass
class ValidateReport:
    path: str
    format: str
    session_id: str
    entries: int
    leaf_id: str | None


class ValidateError(Exception):
    """Raised when a Pi session file fails validation."""


def validate_pi_file(path: Path | str) -> ValidateReport:
    """Validate a Pi session file without modifying it.

    Runs the same strict validator Mvge uses at open time. A torn final
    line fails validation here on purpose: this command is read-only, and
    opening the file in Mvge repairs it.
    """
    source = Path(path).expanduser()
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidateError(f"Cannot read Pi session file: {source}") from exc
    lines = text.splitlines()
    if not lines:
        raise ValidateError(f"Empty Pi session file: {source}")
    try:
        header = json.loads(lines[0])
    except ValueError as exc:
        raise ValidateError(f"Invalid Pi session header in {source}: not JSON") from exc
    if not isinstance(header, dict):
        raise ValidateError(f"Invalid Pi session header in {source}: not an object")

    codec = PiSessionCodec()
    if codec.detect(header):
        fmt = "v4" if header.get("v") == 4 else "v3"
    elif codec.looks_like_session(header):
        raise ValidateError(
            f"Unsupported Pi session version in {source}: "
            f"{header.get('v', header.get('version'))!r}"
        )
    else:
        raise ValidateError(f"Not a Pi session file: {source}")

    try:
        meta = codec.parse_header(header)
        entries = codec.parse_entries(header, lines[1:], source=str(source))
    except (TomeVersionError, ValueError) as exc:
        raise ValidateError(f"Invalid Pi session {source}: {exc}") from exc

    return ValidateReport(
        path=str(source),
        format=fmt,
        session_id=meta.id,
        entries=len(entries),
        leaf_id=codec.leaf_id(header, entries),
    )


export_app = typer.Typer(
    name="pi-export",
    help="Export a Mvge Tome v1 session to a Pi-native v4 file.",
)
validate_app = typer.Typer(
    name="pi-validate",
    help="Validate a Pi session file (read-only).",
)


@export_app.command()
def export_main(
    tome_id: Annotated[str, typer.Argument(help="Tome id to export")],
    tome_dir: Annotated[
        str | None, typer.Option("--tome-dir", help="Tome directory")
    ] = None,
    output: Annotated[
        str | None, typer.Option("--output", help="Output .jsonl path")
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an existing output file")
    ] = False,
) -> None:
    """Export a Tome v1 session to a new Pi-native v4 session file."""
    try:
        report = export_tome_to_pi(
            tome_id, tome_dir=tome_dir, output=output, force=force
        )
    except ExportError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1) from e

    typer.echo(f"Tome id       : {report.tome_id}")
    typer.echo(f"Pi session id : {report.pi_session_id}")
    typer.echo(f"Pi file       : {report.pi_path}")
    typer.echo(f"Entries       : {report.entries}")
    if report.skipped:
        typer.echo(f"Skipped (leaf): {report.skipped}")
    for kind, count in sorted(report.by_type.items()):
        typer.echo(f"  {kind}: {count}")
    typer.echo("Done. This is a copy — the Tome is untouched.")


@validate_app.command()
def validate_main(
    file: Annotated[str, typer.Argument(help="Path to the Pi session .jsonl file")],
) -> None:
    """Validate a Pi session file without modifying it."""
    try:
        report = validate_pi_file(file)
    except ValidateError as e:
        typer.echo(f"INVALID: {e}", err=True)
        raise typer.Exit(1) from e

    typer.echo(f"OK: {report.path}")
    typer.echo(f"Format    : Pi {report.format}")
    typer.echo(f"Session id: {report.session_id}")
    typer.echo(f"Entries   : {report.entries}")
    typer.echo(f"Leaf      : {report.leaf_id}")
