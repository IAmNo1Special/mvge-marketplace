from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from mvge_runes_skills_bridge.types import (
    SkillDiagnostic,
    SkillDiagnosticKind,
    SkillManifest,
    SkillScope,
)

FRONTMATTER_REGEX = re.compile(r"^---\r?\n(.*?)\r?\n---(?:\r?\n(.*))?$", re.DOTALL)
NAME_REGEX = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
UNQUOTED_COLON_REGEX = re.compile(
    r"^([ \t]*[a-zA-Z0-9_-]+:[ \t]+)([^\"'\r\n#][^\r\n#]*:[^\r\n#]*)$",
    re.MULTILINE,
)


def repair_yaml_unquoted_colons(yaml_text: str) -> str:
    """Wrap unquoted YAML values containing colons in quotes for tolerant parsing."""

    def replacer(match: re.Match[str]) -> str:
        key_part = match.group(1)
        val_part = match.group(2).strip()
        escaped_val = val_part.replace('"', '\\"')
        return f'{key_part}"{escaped_val}"'

    return UNQUOTED_COLON_REGEX.sub(replacer, yaml_text)


def resolve_skill_file(path: Path) -> Path | None:
    """Locate the skill manifest file inside a skill directory.

    Checks SKILL.md first, then falls back to lowercase skill.md per the
    .agents protocol. Returns None when neither exists (not a skill).
    """
    upper = path / "SKILL.md"
    if upper.is_file():
        return upper
    lower = path / "skill.md"
    if lower.is_file():
        return lower
    return None


def parse_skill_manifest(
    path: Path,
    diagnostics: list[SkillDiagnostic] | None = None,
    scope: SkillScope = SkillScope.PROJECT,
    lenient: bool = True,
) -> SkillManifest | None:
    """Parse a SKILL.md file into a SkillManifest.

    Conforms to agentskills.io client implementation rules:
    - Path traversal guard (PATH_ESCAPE)
    - Tolerant YAML colon repair (MALFORMED_YAML)
    - Lenient validation for non-fatal name/dir discrepancies
    - Strict skip on missing/empty description or unparseable YAML
    """
    resolved = resolve_skill_file(path)
    if resolved is None:
        return None
    skill_md_path = resolved.resolve()
    file_label = skill_md_path.name

    path_resolved = path.resolve()
    if not str(skill_md_path).startswith(str(path_resolved)):
        if diagnostics is not None:
            diagnostics.append(
                SkillDiagnostic(
                    kind=SkillDiagnosticKind.PATH_ESCAPE,
                    skill_name=path.name,
                    message=f"{file_label} in {path.name} resolves outside skill directory",
                    scope=scope,
                    path=str(path),
                )
            )
        return None

    try:
        content = skill_md_path.read_text(encoding="utf-8")
    except OSError:
        return None

    content = content.lstrip("\ufeff")
    match = FRONTMATTER_REGEX.match(content)
    if not match:
        if diagnostics is not None:
            diagnostics.append(
                SkillDiagnostic(
                    kind=SkillDiagnosticKind.PARSE_WARNING,
                    skill_name=path.name,
                    message=f"{file_label} in {path.name} missing '---' frontmatter delimiters",
                    scope=scope,
                    path=str(path),
                )
            )
        return None

    raw_yaml = match.group(1)
    body = (match.group(2) or "").strip()

    try:
        frontmatter = yaml.safe_load(raw_yaml)
    except yaml.YAMLError:
        repaired_yaml = repair_yaml_unquoted_colons(raw_yaml)
        try:
            frontmatter = yaml.safe_load(repaired_yaml)
            if diagnostics is not None:
                diagnostics.append(
                    SkillDiagnostic(
                        kind=SkillDiagnosticKind.MALFORMED_YAML,
                        skill_name=path.name,
                        message=f"Repaired unquoted colons in YAML frontmatter for {path.name}",
                        scope=scope,
                        path=str(path),
                    )
                )
        except yaml.YAMLError as exc:
            if diagnostics is not None:
                diagnostics.append(
                    SkillDiagnostic(
                        kind=SkillDiagnosticKind.PARSE_WARNING,
                        skill_name=path.name,
                        message=f"Invalid YAML frontmatter in {path.name}: {exc}",
                        scope=scope,
                        path=str(path),
                    )
                )
            return None

    if not isinstance(frontmatter, dict):
        if diagnostics is not None:
            diagnostics.append(
                SkillDiagnostic(
                    kind=SkillDiagnosticKind.PARSE_WARNING,
                    skill_name=path.name,
                    message=f"Frontmatter in {path.name} is not a YAML mapping",
                    scope=scope,
                    path=str(path),
                )
            )
        return None

    raw_name = frontmatter.get("name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        if diagnostics is not None:
            diagnostics.append(
                SkillDiagnostic(
                    kind=SkillDiagnosticKind.PARSE_WARNING,
                    skill_name=path.name,
                    message=f"{file_label} in {path.name} missing 'name' in frontmatter",
                    scope=scope,
                    path=str(path),
                )
            )
        return None

    name = raw_name.strip()
    name_invalid = not NAME_REGEX.match(name)
    dir_mismatch = name != path.name
    name_too_long = len(name) > 64

    if name_invalid or dir_mismatch or name_too_long:
        msg_parts = []
        if name_invalid:
            msg_parts.append(
                f"Name '{name}' does not match regex ^[a-z0-9]+(-[a-z0-9]+)*$"
            )
        if dir_mismatch:
            msg_parts.append(f"Name '{name}' does not match directory '{path.name}'")
        if name_too_long:
            msg_parts.append(f"Name '{name}' exceeds 64 characters ({len(name)})")

        combined_msg = "; ".join(msg_parts)
        if diagnostics is not None:
            diagnostics.append(
                SkillDiagnostic(
                    kind=SkillDiagnosticKind.PARSE_WARNING,
                    skill_name=name,
                    message=combined_msg,
                    scope=scope,
                    path=str(path),
                )
            )
        if not lenient:
            return None

    raw_desc = frontmatter.get("description")
    if not isinstance(raw_desc, str) or not raw_desc.strip():
        if diagnostics is not None:
            diagnostics.append(
                SkillDiagnostic(
                    kind=SkillDiagnosticKind.PARSE_WARNING,
                    skill_name=name,
                    message=f"{file_label} for '{name}' missing required 'description'",
                    scope=scope,
                    path=str(path),
                )
            )
        return None
    description = raw_desc.strip()

    metadata = frontmatter.get("metadata")
    meta_dict: dict[str, Any] = metadata if isinstance(metadata, dict) else {}

    allowed_tools = str(
        frontmatter.get("allowed-tools") or frontmatter.get("allowed_tools") or ""
    )
    disable_invocation = bool(
        frontmatter.get("disable-model-invocation")
        or frontmatter.get("disable_model_invocation", False)
    )

    return SkillManifest(
        name=name,
        description=description,
        scope=scope,
        path=str(path),
        location=skill_md_path.as_posix(),
        version=str(frontmatter.get("version", "")),
        license=str(frontmatter.get("license", "")),
        compatibility=str(frontmatter.get("compatibility", "")),
        metadata=meta_dict,
        allowed_tools=allowed_tools,
        disable_model_invocation=disable_invocation,
        body=body,
    )
