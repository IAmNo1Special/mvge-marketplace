from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from mvge.core.abort import AbortSignal
from mvge.core.spells import MvgeSpell, SpellContext, SpellExecutionMode
from mvge.runes.types import SpellDefinition

from .connector import MCPConnector
from .discovery import MCPConfigDiscovery, MCPServerInfo, MCPTransportError
from .mcp_selector import MCPNLTSelector
from .paths import missing


class MCPSearchSpell(MvgeSpell):
    """
    Discover MCP servers and register their tools as MCPToolSpell instances.
    """

    def __init__(
        self,
        stream_text: Callable[..., Awaitable[str]] | None = None,
        config: dict[str, Any] | None = None,
        rune_api: Any | None = None,
    ) -> None:
        self._cfg = config or {}
        super().__init__(
            name="mcp_search",
            description="Find and connect to MCP servers providing external tools",
            parameters={
                "type": "object",
                "properties": {
                    "capability": {
                        "type": "string",
                        "description": "What capability you need",
                    },
                    "domain": {"type": "string", "description": "Domain hint"},
                    "auto_connect": {"type": "boolean", "default": True},
                    "max_results": {"type": "integer", "default": 3},
                },
                "required": ["capability"],
            },
            execution_mode=SpellExecutionMode.SEQUENTIAL,
        )
        self._stream_text = stream_text
        self._rune_api = rune_api
        self._connections: dict[str, MCPConnector] = {}
        search_roots = self._cfg.get("search_roots", None)
        if search_roots is not None:
            search_roots = [Path(r) for r in search_roots]
        self._discovery = MCPConfigDiscovery(search_roots=search_roots)
        # Read timeout overrides from config (keys match config schema section 8)
        timeout_cfg = self._cfg.get("timeout", {})
        self._timeout_overrides: dict[str, int] = {}
        if isinstance(timeout_cfg, dict):
            self._timeout_overrides = {k: int(v) for k, v in timeout_cfg.items()}

    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: AbortSignal | None = None,
        on_update: Any | None = None,
        ctx: SpellContext | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        capability = params["capability"]
        max_results = params.get("max_results", 3)
        auto_connect = params.get("auto_connect", True)

        absent = missing(self._discovery.search_roots)
        if absent:
            # Nothing was searched, so a zero here carries no information.
            # Naming the roots turns it into the diagnosis it has to be.
            listed = ", ".join(str(root) for root in absent)
            return {
                "serversFound": 0,
                "servers": [],
                "error": f"no extensions directory to search: {listed}",
            }

        try:
            configs = await self._discovery.search(capability)
        except MCPTransportError as e:
            return {"serversFound": 0, "servers": [], "error": str(e)}

        selector = MCPNLTSelector(
            self._stream_text,
            model_id=self._cfg.get("nlt_model", "openrouter/free"),
        )
        try:
            selected = await selector.select(capability, configs, max_results)
        except Exception as e:  # noqa: BLE001 -- spell returns an error payload instead of raising
            return {"serversFound": 0, "servers": [], "error": str(e)}

        results = []
        for server_info in selected:
            if auto_connect:
                try:
                    tools = await self._connect_server(server_info)
                    results.append(
                        {
                            "server": server_info.name,
                            "toolsRegistered": [t.name for t in tools],
                            "status": "connected",
                        }
                    )
                except (MCPTransportError, TimeoutError) as e:
                    results.append(
                        {
                            "server": server_info.name,
                            "status": "failed",
                            "error": str(e),
                        }
                    )
            else:
                results.append(
                    {
                        "server": server_info.name,
                        "description": server_info.description,
                        "status": "discovered",
                    }
                )

        return {"serversFound": len(results), "servers": results, "error": None}

    async def _connect_server(self, info: MCPServerInfo) -> list[SpellDefinition]:
        if info.name in self._connections:
            connector = self._connections[info.name]
            return connector.tool_spells

        # Build connector but do NOT store until fully initialized
        connector = MCPConnector(
            info,
            timeout_overrides=self._timeout_overrides,
            rune_api=self._rune_api,
        )
        try:
            await connector.connect()
            tool_spells = await connector.register_all_capabilities()
        except Exception:
            await connector.close()
            raise

        # Only store after full initialization succeeds
        self._connections[info.name] = connector

        # Widen Seeker's own active entry (pinned) and the global
        # hides-all allowlist so the model can cast the new mcp_* spells.
        # Both are no-ops when the corresponding mode is disabled.
        if self._rune_api is not None:
            names = [t.name for t in tool_spells]
            widen_active = getattr(self._rune_api, "widen_active_spells", None)
            if callable(widen_active):
                widen_active(names)
            widen_global = getattr(self._rune_api, "widen_global_allowlist", None)
            if callable(widen_global):
                widen_global(names)
        return tool_spells
