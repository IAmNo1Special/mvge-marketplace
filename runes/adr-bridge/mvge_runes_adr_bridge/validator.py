"""Strict MADR 3.0 conformance validator."""

from __future__ import annotations

from pathlib import Path

from mvge_runes_adr_bridge.parser import (
    _ADR_FILENAME_RE,
    _SECTION_RE,
    _TITLE_HEADING_RE,
    parse_adr_file,
    resolve_adr_dir,
)
from mvge_runes_adr_bridge.types import ADRStatus, ADRValidationReport

_VALID_STATUSES = {s.value for s in ADRStatus}
_REQUIRED_HEADINGS = [
    "Context and Problem Statement",
    "Decision Outcome",
]
_FORBIDDEN_LEGACY_ALIASES = {
    "Context": "Context and Problem Statement",
    "Decision": "Decision Outcome",
}


def validate_adrs(
    cwd: Path | None = None, adr_dir: Path | None = None
) -> ADRValidationReport:
    """Validate all Architectural Decision Records in directory against MADR 3.0."""
    report = ADRValidationReport(valid=True)
    target = resolve_adr_dir(cwd=cwd, adr_dir=adr_dir)

    if target is None or not target.is_dir():
        report.add_warning("", "MISSING: No ADR directory found (expected docs/adr/).")
        return report

    files = [f for f in target.iterdir() if f.is_file() and f.suffix == ".md"]
    if not files or (len(files) == 1 and files[0].name == "README.md"):
        report.add_warning(
            "", "FOUND: 0 Architectural Decision Records. Directory is empty."
        )
        return report

    for path in sorted(files):
        if path.name == "README.md" or path.name.startswith(("template", "_template")):
            continue

        rel_name = path.name

        # 1. Filename pattern check
        if not _ADR_FILENAME_RE.match(rel_name):
            report.add_error(
                rel_name,
                f"Filename '{rel_name}' does not match MADR 3.0 numbering convention '####-slug.md' (§MADR).",
            )
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            report.add_error(rel_name, f"Could not read file as UTF-8: {exc}")
            continue

        # 2. Level 1 title check
        if not _TITLE_HEADING_RE.search(text):
            report.add_error(
                rel_name, "Missing Level 1 title heading ('# <Title>') (§MADR 3.0)."
            )

        # 3. Required sections and legacy aliases rejection
        headings: set[str] = set()
        for sm in _SECTION_RE.finditer(text):
            heading = sm.group(1).strip()
            headings.add(heading)

        for legacy_alias, modern in _FORBIDDEN_LEGACY_ALIASES.items():
            if legacy_alias in headings and modern not in headings:
                report.add_error(
                    rel_name,
                    f"Obsolete MADR 2.x section '## {legacy_alias}' detected. Use exact MADR 3.0 string '## {modern}'.",
                )

        for req in _REQUIRED_HEADINGS:
            if req not in headings:
                report.add_error(
                    rel_name, f"Missing required MADR 3.0 section '## {req}'."
                )

        # 4. Entry parsing and status check
        entry = parse_adr_file(path)
        if entry is not None:
            report.adrs.append(entry)
            if entry.status not in _VALID_STATUSES:
                report.add_warning(
                    rel_name,
                    f"Unrecognized status '{entry.status}'. Expected one of: {', '.join(sorted(_VALID_STATUSES))}.",
                )
            if not entry.date:
                report.add_warning(
                    rel_name, "Missing date (YYYY-MM-DD) in frontmatter or metadata."
                )
            if "Considered Options" not in headings:
                report.add_warning(
                    rel_name, "Recommended section '## Considered Options' is missing."
                )

    return report
