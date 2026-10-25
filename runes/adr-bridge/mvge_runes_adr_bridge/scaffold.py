"""MADR 3.0 template scaffolding and sequential ADR creation."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from mvge_runes_adr_bridge.parser import load_adrs
from mvge_runes_adr_bridge.sync import sync_adr_index


def slugify(text: str) -> str:
    """Convert text into a URL and filename-friendly slug."""
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[-\s]+", "-", s)
    return s.strip("-") or "record"


def scaffold_new_adr(
    title: str,
    cwd: Path | None = None,
    adr_dir: Path | None = None,
    context: str = "",
    status: str = "proposed",
    deciders: str = "",
) -> Path:
    """Scaffold a new MADR 3.0 record with sequential numbering.

    Updates docs/adr/README.md automatically.
    """
    target_dir = adr_dir or ((cwd or Path.cwd()) / "docs" / "adr")
    target_dir.mkdir(parents=True, exist_ok=True)

    existing = load_adrs(adr_dir=target_dir)
    next_num = (max(a.number for a in existing) + 1) if existing else 1

    slug = slugify(title)
    filename = f"{next_num:04d}-{slug}.md"
    file_path = target_dir / filename

    today = datetime.now(UTC).date().isoformat()
    deciders_str = deciders or "architect"

    content = f"""# {title}

* Status: {status}
* Date: {today}
* Deciders: {deciders_str}

## Context and Problem Statement

{context.strip() if context.strip() else "Describe the context and problem statement here."}

## Decision Drivers

* Technical constraint or requirement 1
* Technical constraint or requirement 2

## Considered Options

* Option 1
* Option 2

## Decision Outcome

Chosen option: "[Option 1]", because [justification].

## Pros and Cons of the Options

### Option 1

* Good, because [positive consequence]
* Bad, because [negative consequence]

### Option 2

* Good, because [positive consequence]
* Bad, because [negative consequence]

## Links

* [Related link or document]
"""

    file_path.write_text(content, encoding="utf-8")
    sync_adr_index(adr_dir=target_dir)
    return file_path
