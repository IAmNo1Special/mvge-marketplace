"""Index synchronizer for docs/adr/README.md."""

from __future__ import annotations

from pathlib import Path

from mvge_runes_adr_bridge.parser import load_adrs, resolve_adr_dir


def sync_adr_index(cwd: Path | None = None, adr_dir: Path | None = None) -> str:
    """Synchronize the index table in docs/adr/README.md with all existing ADRs.

    Returns a status message.
    """
    target = resolve_adr_dir(cwd=cwd, adr_dir=adr_dir)
    if target is None or not target.is_dir():
        return "MISSING: No ADR directory found to synchronize."

    adrs = load_adrs(cwd=cwd, adr_dir=adr_dir)
    readme_path = target / "README.md"

    lines: list[str] = [
        "# Architectural Decision Records",
        "",
        "This directory contains the Architectural Decision Records (MADR 3.0) for this project.",
        "",
        "| Number | Title | Status | Date |",
        "| :--- | :--- | :--- | :--- |",
    ]

    for a in adrs:
        rel_link = a.path.name
        date_str = a.date or "-"
        lines.append(
            f"| [{a.number:04d}]({rel_link}) | {a.title} | {a.status} | {date_str} |"
        )

    lines.append("")
    readme_path.write_text("\n".join(lines), encoding="utf-8")
    return f"OK: Synchronized {len(adrs)} Architectural Decision Records to {readme_path.name}."
