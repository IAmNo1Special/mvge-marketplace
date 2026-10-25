from __future__ import annotations

import re
from dataclasses import is_dataclass, replace
from pathlib import Path
from typing import Any, cast

from mvge_runes_skills_bridge.types import SkillManifest

SKILL_CATALOG_REGEX = re.compile(
    r"\n*<available_skills>.*?</available_skills>\n*",
    re.DOTALL,
)


def build_skill_catalog(
    skills: list[SkillManifest],
    suppress: bool = False,
) -> str:
    """Format available skills into canonical agentskills.io XML catalog.

    Returns empty string if suppressed, if skills list is empty,
    or if all skills have disable_model_invocation=True.
    """
    if suppress or not skills:
        return ""

    visible = [s for s in skills if not s.disable_model_invocation]
    if not visible:
        return ""

    lines = [
        "<available_skills>",
        "  <!-- The following skills provide specialized instructions for specific tasks.",
        "       When a task matches a skill's description, call the activate_skill tool",
        "       with the skill's name to load its full instructions.",
        "       When a skill references relative paths, resolve them against the",
        "       skill's directory (the parent of SKILL.md) and use absolute paths in tool calls. -->",
    ]
    for s in visible:
        loc = s.location or (Path(s.path) / "SKILL.md").as_posix()
        lines.append("  <skill>")
        lines.append(f"    <name>{s.name}</name>")
        lines.append(f"    <description>{s.description}</description>")
        lines.append(f"    <location>{loc}</location>")
        lines.append("  </skill>")
    lines.append("</available_skills>")
    return "\n".join(lines)


def update_invocations_with_skill_catalog(
    invocations: list[Any],
    skills: list[SkillManifest],
    suppress: bool = False,
) -> list[Any]:
    """In-place regex update of <available_skills> XML across turn history.

    Replaces any existing <available_skills> block with the latest catalog,
    preventing prompt token accumulation across multi-turn sessions.
    """
    new_catalog = build_skill_catalog(skills, suppress=suppress)
    catalog_inserted = False
    updated: list[Any] = []

    for inv in invocations:
        content = (
            getattr(inv, "content", None)
            if hasattr(inv, "content")
            else inv.get("content")
        )
        if not isinstance(content, str):
            updated.append(inv)
            continue

        if SKILL_CATALOG_REGEX.search(content):
            if new_catalog:
                new_content = SKILL_CATALOG_REGEX.sub(f"\n\n{new_catalog}\n", content)
            else:
                new_content = SKILL_CATALOG_REGEX.sub("", content).strip()
            catalog_inserted = True
        else:
            new_content = content

        if is_dataclass(inv) and not isinstance(inv, type):
            inv_obj = cast(Any, inv)
            updated.append(replace(inv_obj, content=new_content))
        elif isinstance(inv, dict):
            copy_dict = dict(inv)
            copy_dict["content"] = new_content
            updated.append(copy_dict)
        else:
            updated.append(inv)

    if not catalog_inserted and new_catalog and updated:
        first = updated[0]
        first_content = (
            getattr(first, "content", None)
            if hasattr(first, "content")
            else first.get("content")
        )
        if isinstance(first_content, str):
            augmented = f"{first_content}\n\n{new_catalog}"
            if is_dataclass(first) and not isinstance(first, type):
                first_obj = cast(Any, first)
                updated[0] = replace(first_obj, content=augmented)
            elif isinstance(first, dict):
                first_dict = dict(first)
                first_dict["content"] = augmented
                updated[0] = first_dict

    return updated
