from __future__ import annotations

from typing import Any

from mvgeos_core.abort import AbortSignal
from mvgeos_core.spells import MvgeSpell, SpellContext, SpellExecutionMode

from .connector import MCPConnector
from .ledger import SeekerLedger


class UnloadSpell(MvgeSpell):
    """Unload active capabilities (spells, skills, mcp_servers) to reclaim mana and resources."""

    def __init__(
        self,
        rune_api: Any,
        ledger: SeekerLedger,
        connections: dict[str, MCPConnector],
    ) -> None:
        super().__init__(
            name="unload",
            description=(
                "Unload capabilities: narrows active spells from context, deactivates skills, "
                "and closes MCP server connections."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "spells": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Spell names to unload/hide from context.",
                        "default": [],
                    },
                    "skills": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Skill names to deactivate.",
                        "default": [],
                    },
                    "mcp_servers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "MCP server names to disconnect.",
                        "default": [],
                    },
                },
            },
            execution_mode=SpellExecutionMode.SEQUENTIAL,
        )
        self._rune_api = rune_api
        self._ledger = ledger
        self._connections = connections

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

        # 1. Spells: narrow global allowlist
        if spells and hasattr(self._rune_api, "narrow_global_allowlist"):
            self._rune_api.narrow_global_allowlist(spells)

        # 2. Skills: deactivate
        for k in skills:
            self._ledger.activated_skills.discard(k)

        # 3. MCP servers: disconnect and unregister their tools
        unloaded_mcp_tools: list[str] = []
        for m in mcp_servers:
            conn = self._connections.pop(m, None)
            if conn is not None:
                await conn.close()

            prefix = f"mcp_{m}_"
            tools_to_remove = [
                s for s in list(self._ledger.discovered_spells) if s.startswith(prefix)
            ]
            if tools_to_remove:
                if hasattr(self._rune_api, "narrow_global_allowlist"):
                    self._rune_api.narrow_global_allowlist(tools_to_remove)
                if hasattr(self._rune_api, "unregister_spell"):
                    for t in tools_to_remove:
                        self._rune_api.unregister_spell(t)
                        self._ledger.discovered_spells.discard(t)
                unloaded_mcp_tools.extend(tools_to_remove)

        return {
            "status": "ok",
            "unloaded_spells": spells,
            "unloaded_skills": skills,
            "unloaded_mcp_servers": mcp_servers,
            "unregistered_mcp_tools": unloaded_mcp_tools,
        }
