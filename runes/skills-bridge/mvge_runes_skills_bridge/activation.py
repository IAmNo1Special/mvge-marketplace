from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from mvge_runes_skills_bridge.types import (
    SkillActivationResult,
    SkillManifest,
)

MAX_SKILL_RESOURCES = 50


def enumerate_skill_resources(base_dir: Path) -> list[str]:
    """List bundled files under scripts/, references/, assets/ without eager reading."""
    resources: list[str] = []
    if not base_dir.is_dir():
        return resources

    try:
        for root, dirs, files in os_walk_bounded(base_dir, max_depth=4):
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".") and d not in ("__pycache__", "node_modules")
            ]
            for file in sorted(files):
                if file in ("SKILL.md", "skill.md") or file.startswith("."):
                    continue
                file_path = Path(root) / file
                try:
                    rel = file_path.relative_to(base_dir).as_posix()
                    resources.append(rel)
                    if len(resources) >= MAX_SKILL_RESOURCES:
                        return resources
                except ValueError:
                    continue
    except OSError:
        pass

    return resources


def os_walk_bounded(
    top: Path, max_depth: int = 4
) -> Iterator[tuple[str, list[str], list[str]]]:
    """Walk directory tree up to max_depth."""
    top_depth = len(top.resolve().parts)
    for root, dirs, files in top.walk():
        curr_depth = len(root.resolve().parts) - top_depth
        if curr_depth >= max_depth:
            dirs.clear()
        yield str(root), dirs, files


def activate_skill(
    name: str,
    skills_map: dict[str, SkillManifest],
    active_skills: set[str],
) -> SkillActivationResult:
    """Activate a skill by name, returning structured <skill_content> XML.

    Conforms to agentskills.io:
    - Structured XML wrapping <skill_content name="...">
    - Base directory injection for relative path resolution
    - Resource enumeration without eager file reading (max 50)
    - Session activation deduplication
    """
    manifest = skills_map.get(name)
    if manifest is None:
        avail = ", ".join(sorted(skills_map.keys())) or "none"
        raise ValueError(f"Skill '{name}' not found. Available skills: {avail}")

    base_dir = manifest.base_dir
    loc = manifest.location or (base_dir / "SKILL.md").as_posix()

    if name in active_skills:
        xml = (
            f'<skill_content name="{name}" already_active="true">\n'
            f"Skill '{name}' is already active in this conversation context.\n"
            f"Skill directory: {base_dir.as_posix()}\n"
            f"</skill_content>"
        )
        return SkillActivationResult(
            name=name,
            content=xml,
            location=loc,
            resources=[],
        )

    body = manifest.body
    if body is None:
        skill_file = Path(loc)
        if skill_file.is_file():
            try:
                from mvge_runes_skills_bridge.parser import FRONTMATTER_REGEX

                text = skill_file.read_text(encoding="utf-8").lstrip("\ufeff")
                m = FRONTMATTER_REGEX.match(text)
                body = (m.group(2) if m else "").strip()
            except OSError:
                body = ""
        else:
            body = ""

    resources = enumerate_skill_resources(base_dir)
    res_lines = []
    if resources:
        res_lines.append("\n<skill_resources>")
        for r in resources:
            res_lines.append(f"  <file>{r}</file>")
        res_lines.append("</skill_resources>")
    res_block = "\n".join(res_lines)

    lines = [
        f'<skill_content name="{name}">',
        body,
        "",
        f"Skill directory: {base_dir.as_posix()}",
        "Relative paths in this skill are relative to the skill directory.",
        res_block,
        "</skill_content>",
    ]
    xml = "\n".join(line for line in lines if line is not None)
    active_skills.add(name)

    return SkillActivationResult(
        name=name,
        content=xml,
        location=loc,
        resources=resources,
    )
