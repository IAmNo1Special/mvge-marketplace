"""Prompt-section tests for selfmod-bridge.

The section must be byte-identical to the text the engine rendered for the
same inputs (engine ``render_prompt``'s Self-Modification & Customization
block, minus the dead ``- Spells:`` line which no production caller ever
passed — spec §2.1). Expected strings below are hardcoded from the engine
source, not derived from the implementation.
"""

from __future__ import annotations

from pathlib import Path

from mvge_runes_selfmod_bridge.prompt import build_selfmod_section

INTRO = (
    "You can extend and self-modify your capabilities by editing files with "
    "your spells (changes are watched and hot-reloaded automatically). "
    "Before creating or modifying, read the AGENTS.md in that directory for "
    "exact syntax, rules, and contracts:"
)


def test_runes_paths_no_existence_check(tmp_path: Path) -> None:
    """One - Runes: line per configured path, no existence check (engine:190-193)."""
    missing = tmp_path / "does-not-exist"
    section = build_selfmod_section([str(missing)], None)
    expected = (
        f"\nSelf-Modification & Customization:\n{INTRO}\n- Runes: {missing.as_posix()}/AGENTS.md"
    )
    assert section == expected


def test_system_instructions_only_when_is_file(tmp_path: Path) -> None:
    """- System Instructions: only when system_path.is_file() (engine:195-196)."""
    system_file = tmp_path / "SYSTEM.md"
    system_file.write_text("persona", encoding="utf-8")
    missing = tmp_path / "SYSTEM-missing.md"

    with_file = build_selfmod_section([], str(system_file))
    assert with_file == (
        "\nSelf-Modification & Customization:\n"
        f"{INTRO}\n"
        f"- System Instructions: {system_file.as_posix()}"
    )

    without_file = build_selfmod_section([], str(missing))
    assert without_file == ""


def test_full_section_byte_identical(tmp_path: Path) -> None:
    rp1 = tmp_path / "runes-a"
    rp2 = tmp_path / "runes-b"  # neither needs to exist
    system_file = tmp_path / "SYSTEM.md"
    system_file.write_text("persona", encoding="utf-8")

    section = build_selfmod_section([str(rp1), str(rp2)], str(system_file))
    assert section == (
        "\nSelf-Modification & Customization:\n"
        f"{INTRO}\n"
        f"- Runes: {rp1.as_posix()}/AGENTS.md\n"
        f"- Runes: {rp2.as_posix()}/AGENTS.md\n"
        f"- System Instructions: {system_file.as_posix()}"
    )


def test_system_path_as_directory_is_silent(tmp_path: Path) -> None:
    """A directory at system_path is not is_file() -> no line."""
    assert build_selfmod_section([], str(tmp_path)) == ""


def test_nothing_resolves_stays_silent() -> None:
    """Zero prompt overhead when neither path resolves."""
    assert build_selfmod_section([], None) == ""


def test_path_objects_accepted(tmp_path: Path) -> None:
    rp = tmp_path / "runes"
    section = build_selfmod_section([rp], None)
    assert f"- Runes: {rp.as_posix()}/AGENTS.md" in section
