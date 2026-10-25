"""MADR 3.0 Architectural Decision Record parser."""

from __future__ import annotations

import re
from pathlib import Path

from mvge_runes_adr_bridge.types import ADREntry

_ADR_FILENAME_RE = re.compile(r"^(\d{4})-(.+)\.md$")
_TITLE_HEADING_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_SECTION_RE = re.compile(
    r"^##\s+([^\r\n]+)\r?\n(.*?)(?=^##|\Z)", re.MULTILINE | re.DOTALL
)
_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n(.*)$", re.DOTALL)
_STATUS_LINE_RE = re.compile(r"\b(?:status|Status):\s*([a-zA-Z_-]+)", re.IGNORECASE)
_DATE_LINE_RE = re.compile(r"\b(?:date|Date):\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
_DECIDERS_LINE_RE = re.compile(r"\b(?:deciders|Deciders):\s*([^\r\n]+)", re.IGNORECASE)


def resolve_adr_dir(
    cwd: Path | None = None, adr_dir: Path | None = None
) -> Path | None:
    """Resolve the directory containing Architectural Decision Records.

    Canonical location: <cwd>/docs/adr/
    Fallback: <cwd>/adr/
    """
    if adr_dir is not None and adr_dir.is_dir():
        return adr_dir

    if cwd is not None:
        candidates = [cwd / "docs" / "adr", cwd / "adr"]
        for c in candidates:
            if c.is_dir():
                return c

    return None


def parse_adr_file(path: Path) -> ADREntry | None:
    """Parse a single MADR 3.0 decision record file.

    Returns ADREntry if valid, None if filename does not match format or cannot be read.
    """
    m = _ADR_FILENAME_RE.match(path.name)
    if not m:
        return None

    number = int(m.group(1))

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    # Title extraction
    title = ""
    title_match = _TITLE_HEADING_RE.search(text)
    if title_match:
        raw_title = title_match.group(1).strip()
        # Remove ADR prefix if written as "# ADR 0001: My Title"
        title = re.sub(r"^ADR\s+\d+[:\s-]*", "", raw_title, flags=re.IGNORECASE).strip()
    if not title:
        title = m.group(2).replace("-", " ").title()

    # Frontmatter or top text extraction
    status = "accepted"
    date = ""
    deciders = ""

    fm_match = _FRONTMATTER_RE.match(text)
    header_block = fm_match.group(1) if fm_match else text[:1000]

    status_m = _STATUS_LINE_RE.search(header_block)
    if status_m:
        status = status_m.group(1).strip().lower()

    date_m = _DATE_LINE_RE.search(header_block)
    if date_m:
        date = date_m.group(1).strip()

    deciders_m = _DECIDERS_LINE_RE.search(header_block)
    if deciders_m:
        deciders = deciders_m.group(1).strip()

    # Section extraction
    sections: dict[str, str] = {}
    for sm in _SECTION_RE.finditer(text):
        heading = sm.group(1).strip()
        body = sm.group(2).strip()
        sections[heading] = body

    context = sections.get("Context and Problem Statement", "")
    decision = sections.get("Decision Outcome", "")

    # Options extraction
    options: list[str] = []
    options_text = sections.get("Considered Options", "")
    if options_text:
        for line in options_text.splitlines():
            line_str = line.strip()
            if line_str.startswith(("*", "-")):
                opt = line_str.lstrip("*- ").strip()
                if opt:
                    options.append(opt)

    # Consequences
    consequences: list[str] = []
    for heading, sbody in sections.items():
        if "Consequence" in heading:
            for line in sbody.splitlines():
                line_str = line.strip()
                if line_str.startswith(("*", "-", "+")):
                    c_text = line_str.lstrip("*-+ ").strip()
                    if c_text:
                        consequences.append(c_text)

    return ADREntry(
        number=number,
        title=title,
        status=status,
        date=date,
        deciders=deciders,
        context_and_problem_statement=context,
        decision_outcome=decision,
        considered_options=options,
        consequences=consequences,
        path=path,
    )


def load_adrs(cwd: Path | None = None, adr_dir: Path | None = None) -> list[ADREntry]:
    """Scan and load all MADR records sorted by number."""
    target = resolve_adr_dir(cwd=cwd, adr_dir=adr_dir)
    if target is None or not target.is_dir():
        return []

    adrs: list[ADREntry] = []
    for f in target.iterdir():
        if f.is_file() and f.name.endswith(".md") and f.name != "README.md":
            entry = parse_adr_file(f)
            if entry is not None:
                adrs.append(entry)

    adrs.sort(key=lambda a: a.number)
    return adrs
