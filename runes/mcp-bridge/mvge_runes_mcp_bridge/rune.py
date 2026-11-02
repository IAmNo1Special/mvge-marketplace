from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from mvge.runes.rune_api import RuneAPI
from mvge.runes.types import SigilHook, SpellDefinition

from mvge_runes_mcp_bridge.config import get_prioritized_mcp_configs
from mvge_runes_mcp_bridge.manager import (
    MCPManager,
    sanitize_tool_name,
)
from mvge_runes_mcp_bridge.session import MCPServerSession
from mvge_runes_mcp_bridge.types import MCPDiagnostic


def rune_factory(api: RuneAPI) -> None:
    """Initializes and binds the MCP bridge rune to the session.

    Args:
        api: The RuneAPI instance for registering hooks, spells, and commands.
    """
    manager = MCPManager()

    async def on_session_start(_data: Any = None) -> None:
        """Connects configured MCP servers and registers their tools as spells."""
        if getattr(api, "has_spell_gateway", False):
            # Defer eager connections when a spell gateway (seeker) is active.
            # Seeker will connect servers lazily on demand during 'activate'.
            return

        ctx = api.context
        raw_cwd = getattr(ctx, "cwd", None) if ctx else None
        cwd: Path | None = Path(raw_cwd) if raw_cwd else None

        diagnostics: list[MCPDiagnostic] = []
        configs = get_prioritized_mcp_configs(cwd=cwd, diagnostics=diagnostics)

        for server_name, config in configs.items():
            discovered_tools = await manager.connect_server(config, diagnostics)
            for tool in discovered_tools:
                tool_name = tool.name
                description = tool.description or ""
                input_schema = (
                    getattr(tool, "input_schema", None)
                    or getattr(tool, "inputSchema", None)
                    or {}
                )
                sanitized_name = sanitize_tool_name(server_name, tool_name)

                def _make_handler(target_name: str) -> Any:
                    async def handler(
                        params: dict[str, Any] | None = None,
                        *args: Any,
                        arguments: dict[str, Any] | None = None,
                        **kwargs: Any,
                    ) -> Any:
                        payload = params if params is not None else arguments
                        if payload is None:
                            payload = {}
                        result = manager.call_tool(target_name, payload)
                        if inspect.isawaitable(result):
                            return await result
                        return result

                    return handler

                spell = SpellDefinition(
                    name=sanitized_name,
                    description=description,
                    parameters=input_schema,
                    handler=_make_handler(sanitized_name),
                )
                api.register_spell(spell)

    api.on(SigilHook.SESSION_START, on_session_start)

    async def on_shutdown(_data: Any = None) -> None:
        """Shuts down all managed MCP server connections."""
        await manager.shutdown()

    api.on(SigilHook.SESSION_SHUTDOWN, on_shutdown)
    api.on_dispose(manager.shutdown)

    async def mcp_handler(args: str = "") -> str:
        """Parses and executes the /mcp command.

        Args:
            args: Command arguments ('list', '', or 'test <server>').

        Returns:
            Formatted status or server list string.
        """
        parsed_args = (args or "").strip()
        parts = parsed_args.split(maxsplit=1)
        subcommand = parts[0] if parts else ""

        if not subcommand or subcommand == "list":
            if not manager.sessions:
                return "No MCP servers connected."

            server_tools_map: dict[str, list[str]] = {
                s_name: [] for s_name in manager.sessions
            }
            for s_tool, (s_srv, _orig) in manager.tool_map.items():
                server_tools_map.setdefault(s_srv, []).append(s_tool)

            lines: list[str] = ["Connected MCP servers:"]
            for s_name in sorted(server_tools_map):
                s_tools = server_tools_map[s_name]
                if s_tools:
                    lines.append(f"  {s_name} ({len(s_tools)} tools):")
                    for t in s_tools:
                        lines.append(f"    - {t}")
                else:
                    lines.append(f"  {s_name}: (no tools)")
            return "\n".join(lines)

        if subcommand == "test":
            if len(parts) < 2 or not parts[1].strip():
                return "FAIL: No server specified. Usage: /mcp test <server>"
            target_server = parts[1].strip()

            ctx = api.context
            raw_cwd = getattr(ctx, "cwd", None) if ctx else None
            test_cwd: Path | None = Path(raw_cwd) if raw_cwd else None

            test_diagnostics: list[MCPDiagnostic] = []
            test_configs = get_prioritized_mcp_configs(
                cwd=test_cwd, diagnostics=test_diagnostics
            )
            if target_server not in test_configs:
                return f"FAIL: Server '{target_server}' not found."

            test_cfg = test_configs[target_server]
            session = MCPServerSession(test_cfg)
            try:
                await session.connect()
                test_tools = await session.list_tools()
                return f"OK: {target_server} ({len(test_tools)} tools)"
            except Exception as exc:  # noqa: BLE001
                return f"FAIL: {target_server}: {exc}"
            finally:
                await session.close()

        return f"Unknown MCP command: '{parsed_args}'. Usage: /mcp [list|test <server>]"

    api.register_command(
        "mcp",
        description="MCP server management",
        handler=mcp_handler,
    )
