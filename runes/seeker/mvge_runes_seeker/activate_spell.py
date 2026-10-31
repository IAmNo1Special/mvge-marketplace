from __future__ import annotations

from pathlib import Path
from typing import Any

from mvge.core.abort import AbortSignal
from mvge.core.spells import MvgeSpell, SpellContext, SpellExecutionMode

from .connector import MCPConnector
from .dci_matcher import _get_skill_roots
from .discovery import MCPConfigDiscovery
from .ledger import SeekerLedger


class ActivateSpell(MvgeSpell):
    """Activate capabilities (spells, skills, mcp_servers) discovered via search."""

    def __init__(
        self,
        rune_api: Any,
        ledger: SeekerLedger,
        spell_registry: Any,
        discovery: MCPConfigDiscovery,
        connections: dict[str, MCPConnector],
        skill_dirs: list[Path] | None = None,
        project_dir: str | Path | None = None,
    ) -> None:
        super().__init__(
            name="activate",
            description=(
                "Activate searched capabilities: unlocks full JSON schemas for spells, "
                "progressively discloses SKILL.md and resources for skills, and connects "
                "MCP servers."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "spells": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Spell names returned by search to activate.",
                        "default": [],
                    },
                    "skills": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Skill names returned by search to activate.",
                        "default": [],
                    },
                    "mcp_servers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "MCP server names returned by search to connect.",
                        "default": [],
                    },
                    "skill_resource": {
                        "type": "string",
                        "description": (
                            "Relative path to a bundled reference or asset inside "
                            "an already-activated skill."
                        ),
                    },
                },
            },
            execution_mode=SpellExecutionMode.PARALLEL,
        )
        self._rune_api = rune_api
        self._ledger = ledger
        self._spell_registry = spell_registry
        self._discovery = discovery
        self._connections = connections
        self._skill_dirs = skill_dirs
        self._project_dir = project_dir

    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: AbortSignal | None = None,
        on_update: Any | None = None,
        ctx: SpellContext | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        spells = list(params.get("spells") or [])
        skills = list(params.get("skills") or [])
        mcp_servers = list(params.get("mcp_servers") or [])
        skill_resource = params.get("skill_resource")

        # 1. Search-First Gate Enforcement
        for s in spells:
            if s not in self._ledger.discovered_spells:
                return {
                    "status": "error",
                    "error": (
                        f"Capability '{s}' (kind=spell) has not been returned "
                        "by 'search' in this Tome; call 'search' first."
                    ),
                }

        for k in skills:
            if k not in self._ledger.discovered_skills:
                return {
                    "status": "error",
                    "error": (
                        f"Capability '{k}' (kind=skill) has not been returned "
                        "by 'search' in this Tome; call 'search' first."
                    ),
                }

        for m in mcp_servers:
            if m not in self._ledger.discovered_mcps:
                return {
                    "status": "error",
                    "error": (
                        f"Capability '{m}' (kind=mcp) has not been returned "
                        "by 'search' in this Tome; call 'search' first."
                    ),
                }

        skill_roots = _get_skill_roots(self._skill_dirs, self._project_dir)

        # 2. Resource inspection within activated skills
        if skill_resource:
            clean_res = Path(skill_resource.strip())
            if ".." in clean_res.parts or clean_res.is_absolute():
                return {
                    "status": "error",
                    "error": "Directory traversal or absolute paths forbidden in skill_resource",
                }
            found_content: str | None = None
            found_skill: str | None = None
            for active_k in self._ledger.activated_skills:
                for root in skill_roots:
                    candidate = root / active_k / clean_res
                    if candidate.is_file():
                        try:
                            # Verify candidate is contained inside root / active_k
                            resolved_cand = candidate.resolve()
                            resolved_skill = (root / active_k).resolve()
                            if resolved_cand.is_relative_to(resolved_skill):
                                found_content = candidate.read_text(encoding="utf-8")
                                found_skill = active_k
                                break
                        except (OSError, ValueError):
                            continue
                if found_content is not None:
                    break

            if found_content is None:
                return {
                    "status": "error",
                    "error": f"Resource '{skill_resource}' not found in any activated skill.",
                }
            return {
                "status": "ok",
                "skill": found_skill,
                "resource": str(clean_res),
                "content": found_content,
            }

        activated_schemas: list[dict[str, Any]] = []

        # 3. Spells: widen global allowlist and extract schemas
        if spells:
            if hasattr(self._rune_api, "widen_global_allowlist"):
                self._rune_api.widen_global_allowlist(spells)
            # Stamp the idle clock: without this, the seeker's TURN_END
            # eviction reads a freshly activated spell as idle since turn 0
            # and removes it at the next turn end in sessions past 8 turns.
            for s in spells:
                self._ledger.last_active_turn[s] = self._ledger.current_turn

            discoverable = []
            if hasattr(self._rune_api, "get_discoverable_spells"):
                discoverable = self._rune_api.get_discoverable_spells()
            disc_by_name = {getattr(s, "name", ""): s for s in discoverable}

            for s in spells:
                if s in disc_by_name:
                    obj = disc_by_name[s]
                    activated_schemas.append(
                        {
                            "name": s,
                            "description": getattr(obj, "description", ""),
                            "parameters": getattr(obj, "parameters", {}),
                        }
                    )
                else:
                    activated_schemas.append({"name": s, "status": "activated"})

        # 4. Skills: progressive disclosure of SKILL.md and directory layout
        disclosed_skills: list[str] = []
        for k in skills:
            self._ledger.activated_skills.add(k)
            k_dir: Path | None = None
            for root in skill_roots:
                cand = root / k
                if cand.is_dir():
                    k_dir = cand
                    break

            if k_dir is not None:
                skill_md = k_dir / "SKILL.md"
                body = (
                    skill_md.read_text(encoding="utf-8")
                    if skill_md.is_file()
                    else f"Skill {k}"
                )
                scripts = (
                    [f.name for f in (k_dir / "scripts").iterdir() if f.is_file()]
                    if (k_dir / "scripts").is_dir()
                    else []
                )
                references = (
                    [f.name for f in (k_dir / "references").iterdir() if f.is_file()]
                    if (k_dir / "references").is_dir()
                    else []
                )
                assets = (
                    [f.name for f in (k_dir / "assets").iterdir() if f.is_file()]
                    if (k_dir / "assets").is_dir()
                    else []
                )

                disclosed_skills.append(
                    f'<skill_content name="{k}">\n{body}\n'
                    f"Scripts: {scripts}\n"
                    f"References: {references}\n"
                    f"Assets: {assets}\n"
                    "</skill_content>"
                )
            else:
                disclosed_skills.append(
                    f'<skill_content name="{k}">Skill {k} activated.</skill_content>'
                )

        # 5. MCP servers: lazy connection without arbitrary caps
        connected_mcp_tools: list[str] = []
        for m in mcp_servers:
            if m in self._connections:
                # Already connected
                continue
            matched_infos = await self._discovery.search(m)
            target_info = None
            for info in matched_infos:
                if info.name == m:
                    target_info = info
                    break
            if target_info is None and matched_infos:
                target_info = matched_infos[0]
            if target_info is None:
                continue

            connector = MCPConnector(
                target_info,
                getattr(self._rune_api, "_runner", None),
                rune_api=self._rune_api,
            )
            try:
                await connector.connect()
                registered_spells = await connector.register_all_capabilities()
                for spell_item in registered_spells:
                    self._ledger.discovered_spells.add(spell_item.name)
                    connected_mcp_tools.append(spell_item.name)
                self._connections[m] = connector
            except (OSError, RuntimeError, ValueError, KeyError, AttributeError) as e:
                return {
                    "status": "error",
                    "error": f"Failed to connect MCP server '{m}': {e}",
                }

        if connected_mcp_tools and hasattr(self._rune_api, "widen_global_allowlist"):
            self._rune_api.widen_global_allowlist(connected_mcp_tools)
        for tool in connected_mcp_tools:
            self._ledger.last_active_turn[tool] = self._ledger.current_turn

        return {
            "status": "ok",
            "activated_spells": spells,
            "activated_skills": skills,
            "connected_mcp_tools": connected_mcp_tools,
            "skill_content": "\n\n".join(disclosed_skills),
            "spell_schemas": activated_schemas,
        }
