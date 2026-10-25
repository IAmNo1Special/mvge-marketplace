"""Shared extension-status implementation.

``describe_extensions`` is the ONE implementation behind both surfaces:
the ``extension_status`` spell and the ``/selfmod status`` command
(spec §5.4 — shared implementation, no duplication).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from mvge_runes_selfmod_bridge.state import SelfmodState

logger = logging.getLogger(__name__)


def _snapshots_root(state: SelfmodState) -> Path | None:
    if state.config_dir is None:
        return None
    return state.config_dir / ".selfmod-snapshots"


def list_snapshots(state: SelfmodState) -> list[dict[str, Any]]:
    """List snapshot manifests, newest first; unreadable entries are skipped."""
    root = _snapshots_root(state)
    if root is None or not root.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for child in sorted(root.iterdir(), reverse=True):
        manifest_path = child / "manifest.json"
        if not child.is_dir() or not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("ignoring unreadable snapshot manifest %s: %s", manifest_path, exc)
            continue
        entries.append(
            {
                "id": child.name,
                "created_at": manifest.get("created_at"),
                "label": manifest.get("label"),
                "agent_name": manifest.get("agent_name"),
                "files": manifest.get("files", []),
            }
        )
    return entries


def describe_extensions(state: SelfmodState) -> dict[str, Any]:
    """Describe resolved extension dirs, AGENTS.md presence, warnings, snapshots."""
    warnings: list[str] = []

    spells_dir = state.spells_dir
    if spells_dir is None:
        warnings.append(
            "no active spells dir resolved — scaffold_spell will fail loudly "
            "until <agent_config_dir>/spells or ./spells exists"
        )
    spells_agents_md = bool(spells_dir is not None and (spells_dir / "AGENTS.md").is_file())

    runes_entries: list[dict[str, Any]] = []
    for rp in state.runes_paths:
        exists = rp.is_dir()
        if not exists:
            warnings.append(f"runes path does not exist: {rp.as_posix()}")
        runes_entries.append(
            {
                "path": rp.as_posix(),
                "exists": exists,
                "agents_md": (rp / "AGENTS.md").is_file(),
            }
        )
    if not state.runes_paths:
        warnings.append("no runes paths configured")

    system_path = state.system_path
    system_exists = bool(system_path is not None and system_path.is_file())
    if system_path is not None and not system_exists:
        warnings.append(f"system instructions path is not a file: {system_path.as_posix()}")

    return {
        "ok": True,
        "agent_name": state.agent_name,
        "config_dir": state.config_dir.as_posix() if state.config_dir else None,
        "spells_dir": spells_dir.as_posix() if spells_dir else None,
        "spells_dir_agents_md": spells_agents_md,
        "runes_paths": runes_entries,
        "system_path": system_path.as_posix() if system_path else None,
        "system_path_exists": system_exists,
        "warnings": warnings,
        "snapshots": list_snapshots(state),
    }
