"""Build the Self-Modification & Customization prompt section.

Byte-identical to the engine's ``render_prompt`` block for the same inputs
(``mvge-agent/.../environment.py``), minus the dead ``- Spells:`` line:
no production caller ever passed ``spells_dir`` (spec §2.1), so the rune
does not add one — that would change the section text.
"""

from __future__ import annotations

from pathlib import Path

_INTRO = (
    "You can extend and self-modify your capabilities by editing files with "
    "your spells (changes are watched and hot-reloaded automatically). "
    "Before creating or modifying, read the AGENTS.md in that directory for "
    "exact syntax, rules, and contracts:"
)


def build_selfmod_section(
    runes_paths: list[str] | list[Path],
    system_path: str | Path | None,
) -> str:
    """Render the section, or ``""`` when nothing resolves (zero prompt overhead)."""
    lines: list[str] = []
    for raw in runes_paths:
        # NO existence check — matches engine:190-193 exactly.
        lines.append(f"- Runes: {Path(raw).as_posix()}/AGENTS.md")

    system_file = Path(system_path) if system_path is not None else None
    if system_file is not None and system_file.is_file():
        lines.append(f"- System Instructions: {system_file.as_posix()}")

    if not lines:
        return ""
    return "\nSelf-Modification & Customization:\n" + _INTRO + "\n" + "\n".join(lines)
