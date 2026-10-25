from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from mvge.core import global_agents_dir

from mvge_runes_skills_bridge.parser import (
    parse_skill_manifest,
    resolve_skill_file,
)
from mvge_runes_skills_bridge.types import (
    PluginManifest,
    SkillDiagnostic,
    SkillDiagnosticKind,
    SkillLoad,
    SkillManifest,
    SkillScope,
)

logger = logging.getLogger(__name__)

_SKILL_MANIFEST_CACHE: dict[str, tuple[float, SkillManifest | None]] = {}


def clear_skill_manifest_cache() -> None:
    """Clear cached skill manifests."""
    _SKILL_MANIFEST_CACHE.clear()


def get_prioritized_skill_search_paths(
    agent_name: str,
    cwd: Path | None = None,
    global_dir: Path | None = None,
) -> list[tuple[Path, SkillScope]]:
    """Return skill search paths in strict precedence order (PROJECT > USER > AGENT)."""
    paths: list[tuple[Path, SkillScope]] = []
    base_cwd = cwd or Path.cwd()

    # 1. Project-level scopes
    proj_dot_agents = base_cwd / ".agents" / "skills"
    if proj_dot_agents.is_dir():
        paths.append((proj_dot_agents, SkillScope.PROJECT))

    proj_skills = base_cwd / "skills"
    if proj_skills.is_dir() and proj_skills != proj_dot_agents:
        paths.append((proj_skills, SkillScope.PROJECT))

    # 2. User-level scope (<global>/skills)
    base_global = global_dir if global_dir is not None else global_agents_dir()
    user_skills = base_global / "skills"
    if user_skills.is_dir():
        paths.append((user_skills, SkillScope.USER))

    # 3. Agent-specific scope (<global>/agents/<agent_name>/skills)
    if agent_name:
        agent_skills = base_global / "agents" / agent_name / "skills"
        if agent_skills.is_dir():
            paths.append((agent_skills, SkillScope.AGENT))

    return paths


def load_cached_skill_manifest(
    path: Path,
    diagnostics: list[SkillDiagnostic] | None = None,
    scope: SkillScope = SkillScope.PROJECT,
    lenient: bool = True,
) -> SkillManifest | None:
    """Parse or retrieve a cached SkillManifest."""
    skill_file = resolve_skill_file(path)
    if skill_file is None:
        return None

    try:
        mtime = skill_file.stat().st_mtime
    except OSError:
        return None

    cache_key = str(path.resolve())
    cached = _SKILL_MANIFEST_CACHE.get(cache_key)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    manifest = parse_skill_manifest(
        path, diagnostics=diagnostics, scope=scope, lenient=lenient
    )
    _SKILL_MANIFEST_CACHE[cache_key] = (mtime, manifest)
    return manifest


def load_plugin_manifest(
    plugin_dir: Path,
    diagnostics: list[SkillDiagnostic] | None = None,
) -> PluginManifest | None:
    """Parse a plugin.json file adhering to the Agent Plugins v1.0.0 specification."""
    manifest_file = plugin_dir / "plugin.json"
    if not manifest_file.is_file():
        return None

    try:
        content = manifest_file.read_text(encoding="utf-8")
        data: dict[str, Any] = json.loads(content)
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        if diagnostics is not None:
            diagnostics.append(
                SkillDiagnostic(
                    kind=SkillDiagnosticKind.INVALID_PLUGIN,
                    skill_name=plugin_dir.name,
                    message=f"Failed to parse plugin.json in {plugin_dir.name}: {exc}",
                    path=str(plugin_dir),
                )
            )
        return None

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return None

    raw_skills = data.get("skills", [])
    skill_paths: list[str] = []
    if isinstance(raw_skills, list):
        for item in raw_skills:
            if isinstance(item, str):
                skill_paths.append(item)
            elif isinstance(item, dict) and "path" in item:
                skill_paths.append(str(item["path"]))

    return PluginManifest(
        name=name.strip(),
        version=str(data.get("version", "1.0.0")),
        description=str(data.get("description", "")),
        skills=skill_paths,
        path=str(plugin_dir),
    )


def discover_plugin_skill_paths(
    cwd: Path | None = None,
    global_dir: Path | None = None,
    diagnostics: list[SkillDiagnostic] | None = None,
) -> list[tuple[Path, SkillScope]]:
    """Discover skill directories embedded in installed Agent Plugins."""
    discovered: list[tuple[Path, SkillScope]] = []
    base_cwd = cwd or Path.cwd()
    base_global = global_dir if global_dir is not None else global_agents_dir()

    search_roots = [
        (base_cwd / ".agents" / "plugins", SkillScope.PROJECT),
        (base_global / "plugins", SkillScope.USER),
    ]

    for root, scope in search_roots:
        if not root.is_dir():
            continue
        try:
            for child in sorted(root.iterdir()):
                if not child.is_dir():
                    continue
                plugin = load_plugin_manifest(child, diagnostics=diagnostics)
                if plugin is None:
                    continue
                for rel_path in plugin.skills:
                    embedded_dir = (child / rel_path).resolve()
                    if embedded_dir.is_dir() and resolve_skill_file(embedded_dir):
                        discovered.append((embedded_dir, scope))
        except (OSError, PermissionError):
            continue

    return discovered


def load_skills_from_paths(
    search_paths: list[tuple[Path, SkillScope]],
    agent_name: str = "",
    diagnostics: list[SkillDiagnostic] | None = None,
    lenient: bool = True,
) -> tuple[list[SkillLoad], list[SkillDiagnostic]]:
    """Scan search paths and load skills enforcing PROJECT > USER > AGENT precedence."""
    active_diagnostics: list[SkillDiagnostic] = (
        [] if diagnostics is None else diagnostics
    )
    seen_skills: dict[str, SkillManifest] = {}
    loads: list[SkillLoad] = []

    for search_dir, scope in search_paths:
        if not search_dir.is_dir():
            continue
        try:
            entries = sorted(search_dir.iterdir(), key=lambda p: p.name)
        except (OSError, PermissionError):
            continue

        for child in entries:
            if not child.is_dir():
                continue
            if child.name.startswith(".") or child.name in (
                "__pycache__",
                "node_modules",
            ):
                continue

            manifest = load_cached_skill_manifest(
                child,
                diagnostics=active_diagnostics,
                scope=scope,
                lenient=lenient,
            )
            if manifest is None:
                continue

            if manifest.name in seen_skills:
                winner = seen_skills[manifest.name]
                active_diagnostics.append(
                    SkillDiagnostic(
                        kind=SkillDiagnosticKind.SHADOWED_SKILL,
                        skill_name=manifest.name,
                        message=(
                            f"Skill '{manifest.name}' in {scope.value} scope shadowed "
                            f"by {winner.scope.value} scope copy at {winner.path}"
                        ),
                        scope=scope,
                        path=str(child),
                    )
                )
                continue

            seen_skills[manifest.name] = manifest
            loads.append(SkillLoad(manifest=manifest))

    return loads, active_diagnostics
