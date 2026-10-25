from __future__ import annotations

import logging
from pathlib import Path

from mvge_runes_steering_bridge.resolver import read_steering_text

logger = logging.getLogger(__name__)


def discover_subpackage_pointers(
    cwd: Path | str | None = None,
) -> list[tuple[str, str]]:
    """Scan first-level subdirectories for localized AGENTS.md pointers.

    Returns a sorted list of (directory_name, relative_path) for
    progressive disclosure. Subpackage contents are never inlined here;
    callers surface these as on-demand read pointers to protect token
    budgets. Hidden directories and __pycache__ are skipped.
    """
    base = Path(cwd) if cwd else Path.cwd()
    if not base.is_dir():
        return []

    pointers: list[tuple[str, str]] = []
    try:
        children = sorted(base.iterdir(), key=lambda p: p.name)
    except OSError:
        logger.debug("Could not scan subdirectories in %s", base)
        return []

    for child in children:
        if not child.is_dir():
            continue
        if child.name.startswith(".") or child.name == "__pycache__":
            continue
        try:
            entries = list(child.iterdir())
        except OSError:
            continue
        sub_candidates = [
            e for e in entries if e.name in ("AGENTS.md", "agents.md") and e.is_file()
        ]
        sub_candidates.sort(key=lambda p: (0 if p.name == "AGENTS.md" else 1, p.name))
        for sub_file in sub_candidates:
            if read_steering_text(sub_file, strip_frontmatter=True):
                pointers.append((child.name, f"{child.name}/{sub_file.name}"))
                break

    return pointers


def build_project_context(
    workspace_ref: tuple[Path, str] | None,
    global_ref: tuple[Path, str] | None,
) -> str:
    """Render the <project_context> XML block per .agents Protocol.

    Returns an empty string when neither layer resolves to non-empty
    content, keeping zero overhead for projects without steering files.
    """
    blocks: list[str] = []

    if global_ref is not None:
        content = read_steering_text(global_ref[0], strip_frontmatter=True)
        if content:
            blocks.append(
                f'<global_instructions path="{global_ref[1]}">\n'
                f"{content}\n"
                f"</global_instructions>"
            )

    if workspace_ref is not None:
        content = read_steering_text(workspace_ref[0], strip_frontmatter=True)
        if content:
            blocks.append(
                f'<project_instructions path="{workspace_ref[1]}">\n'
                f"{content}\n"
                f"</project_instructions>"
            )

    if not blocks:
        return ""

    joined = "\n\n".join(blocks)
    return (
        "\n<project_context>\n"
        "Project-specific instructions and guidelines:\n\n"
        f"{joined}\n"
        "</project_context>"
    )


def build_steering_pointers(
    workspace_ref: tuple[Path, str] | None,
    global_ref: tuple[Path, str] | None,
    subpackage_refs: list[tuple[str, str]],
) -> list[str]:
    """Render on-demand AGENTS.md pointer lines for repository steering."""
    lines: list[str] = []
    if global_ref is not None:
        lines.append(f"- Global Rules: {global_ref[1]}")
    if workspace_ref is not None:
        lines.append(f"- Project Rules: {workspace_ref[1]}")
    for dir_name, rel_path in subpackage_refs:
        lines.append(f"- Subpackage Rules ({dir_name}): {rel_path}")
    return lines


def build_steering_section(
    workspace_ref: tuple[Path, str] | None = None,
    global_ref: tuple[Path, str] | None = None,
    subpackage_refs: list[tuple[str, str]] | None = None,
) -> str:
    """Build the full repository steering section for prompt injection.

    Takes already-resolved refs (see ``resolver`` and
    ``discover_subpackage_pointers``). Returns an empty string when no
    steering layer resolves, keeping zero overhead for projects without
    AGENTS.md files. Subpackage contents are referenced as pointers
    only, never inlined.
    """
    subs = subpackage_refs if subpackage_refs is not None else []

    pointers = build_steering_pointers(workspace_ref, global_ref, subs)
    context = build_project_context(workspace_ref, global_ref)

    if not pointers and not context:
        return ""

    parts: list[str] = []
    if pointers:
        parts.append(
            "Repository Steering:\n"
            "Before creating or modifying files, read the AGENTS.md in that "
            "directory for exact syntax, rules, and contracts:"
        )
        parts.extend(pointers)
    if context:
        parts.append(context)
    return "\n".join(parts)
