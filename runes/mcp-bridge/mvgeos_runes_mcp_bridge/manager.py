"""MCP server manager for coordinating connections and spells."""

from __future__ import annotations

import logging
import re
from typing import Any

from mcp import MCPError
from mcp.types import (
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    TextContent,
    Tool,
)
from mvge.core.spells import SpellResult, SpellStatus

try:
    from .session import MCPServerSession
    from .types import (
        MCPDiagnostic,
        MCPDiagnosticKind,
        MCPServerConfig,
    )
except ImportError:
    from mvgeos_runes_mcp_bridge.session import (  # type: ignore[no-redef]
        MCPServerSession,
    )
    from mvgeos_runes_mcp_bridge.types import (  # type: ignore[no-redef]
        MCPDiagnostic,
        MCPDiagnosticKind,
        MCPServerConfig,
    )

logger = logging.getLogger(__name__)

TOOL_NAME_RE = re.compile(r"[^a-zA-Z0-9_]")

__all__ = [
    "MCPManager",
    "extract_content",
    "sanitize_tool_name",
]


def sanitize_tool_name(server_name: str, tool_name: str) -> str:
    """Sanitizes server and tool names into a valid MvgeOS spell identifier.

    Non-alphanumeric characters are replaced with underscores to strictly
    conform to the MvgeOS spell identifier format 'mcp_{server}_{tool}'.

    Args:
        server_name: Name of the MCP server.
        tool_name: Original tool name on the server.

    Returns:
        Sanitized spell identifier string.
    """
    clean_server = TOOL_NAME_RE.sub("_", server_name)
    clean_tool = TOOL_NAME_RE.sub("_", tool_name)
    return f"mcp_{clean_server}_{clean_tool}"


def extract_content(result: CallToolResult) -> str:
    """Extracts text content from an MCP CallToolResult.

    Handles TextContent by joining text lines, ImageContent with placeholder
    '[Embedded Image: {mime}]', and EmbeddedResource with placeholder
    '[Embedded Resource: {uri}]'.

    Args:
        result: CallToolResult returned from an MCP server call.

    Returns:
        Combined string representation of result content.
    """
    parts: list[str] = []
    items = result.content if result.content is not None else []
    for item in items:
        if isinstance(item, TextContent):
            parts.append(item.text)
        elif isinstance(item, ImageContent):
            mime = (
                getattr(item, "mime_type", None)
                or getattr(item, "mimeType", None)
                or getattr(item, "mime", "unknown")
            )
            parts.append(f"[Embedded Image: {mime}]")
        elif isinstance(item, EmbeddedResource):
            res_obj = getattr(item, "resource", None)
            uri = getattr(res_obj, "uri", None) or getattr(item, "uri", "unknown")
            parts.append(f"[Embedded Resource: {uri}]")
        else:
            text = getattr(item, "text", None)
            if text is not None:
                parts.append(str(text))
            else:
                parts.append(str(item))
    return "\n".join(parts)


class MCPManager:
    """Coordinates multiple MCP server sessions, tool mappings, and execution."""

    def __init__(self) -> None:
        """Initializes the MCP manager with empty sessions and tool mappings."""
        self._sessions: dict[str, MCPServerSession] = {}
        self._tool_map: dict[str, tuple[str, str]] = {}

    @property
    def sessions(self) -> dict[str, MCPServerSession]:
        """Returns the dictionary of active server sessions."""
        return self._sessions

    @property
    def tool_map(self) -> dict[str, tuple[str, str]]:
        """Returns the mapping from sanitized tool names to (server, tool)."""
        return self._tool_map

    async def connect_server(
        self,
        config: MCPServerConfig,
        diagnostics: list[MCPDiagnostic] | None = None,
    ) -> list[Tool]:
        """Connects to an MCP server, queries its tools, and records mappings.

        Args:
            config: Server configuration.
            diagnostics: Optional list to append diagnostics to on failure.

        Returns:
            List of Tool instances discovered, or empty list if failed/disabled.
        """
        if getattr(config, "disabled", False):
            return []

        if config.name in self._sessions:
            try:
                await self._sessions[config.name].close()
            except (OSError, RuntimeError) as exc:
                logger.debug(
                    "Error closing existing session for %s: %s",
                    config.name,
                    exc,
                )

        session = MCPServerSession(config)
        try:
            await session.connect()
            tools = await session.list_tools()
            self._sessions[config.name] = session
            for tool in tools:
                sanitized = sanitize_tool_name(config.name, tool.name)
                self._tool_map[sanitized] = (config.name, tool.name)
            return tools
        except MCPError as exc:
            try:
                await session.close()
            except (OSError, RuntimeError) as close_exc:
                logger.debug("Failed closing session after MCP error: %s", close_exc)
            if diagnostics is not None:
                diagnostics.append(
                    MCPDiagnostic(
                        kind=MCPDiagnosticKind.SERVER_FAILED,
                        server_name=config.name,
                        message=str(exc),
                    )
                )
            return []
        except (OSError, RuntimeError, ValueError) as exc:
            try:
                await session.close()
            except (OSError, RuntimeError) as close_exc:
                logger.debug("Failed closing session after error: %s", close_exc)
            if diagnostics is not None:
                diagnostics.append(
                    MCPDiagnostic(
                        kind=MCPDiagnosticKind.SERVER_FAILED,
                        server_name=config.name,
                        message=str(exc),
                    )
                )
            return []

    async def call_tool(
        self,
        sanitized_name: str,
        arguments: dict[str, Any],
        signal: Any | None = None,
    ) -> SpellResult:
        """Executes a sanitized MCP tool call and returns a SpellResult.

        Looks up server and original tool name, delegates execution to session,
        extracts rich content, and handles dual error model (MCPError protocol
        failures and isError tool domain failures).

        Args:
            sanitized_name: Sanitized tool identifier 'mcp_{server}_{tool}'.
            arguments: Dictionary of arguments for the tool.
            signal: Optional AbortSignal for cooperative cancellation.

        Returns:
            SpellResult with SUCCESS or ERROR status.
        """
        mapping = self._tool_map.get(sanitized_name)
        if mapping is None:
            return SpellResult(
                spell_name=sanitized_name,
                status=SpellStatus.ERROR,
                error_message=f"MCP tool '{sanitized_name}' not found",
            )

        server_name, original_tool = mapping
        session = self._sessions.get(server_name)
        if session is None or not session.connected:
            return SpellResult(
                spell_name=sanitized_name,
                status=SpellStatus.ERROR,
                error_message=(
                    f"MCP server '{server_name}' for tool '{sanitized_name}' "
                    f"is not connected"
                ),
            )

        try:
            result = await session.call_tool(original_tool, arguments, signal=signal)
        except MCPError as exc:
            return SpellResult(
                spell_name=sanitized_name,
                status=SpellStatus.ERROR,
                error_message=str(exc),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            return SpellResult(
                spell_name=sanitized_name,
                status=SpellStatus.ERROR,
                error_message=f"Tool execution failed: {exc}",
            )

        content = extract_content(result)
        is_error = bool(
            getattr(result, "isError", False) or getattr(result, "is_error", False)
        )
        if is_error:
            return SpellResult(
                spell_name=sanitized_name,
                status=SpellStatus.ERROR,
                content=content,
                error_message=content or "MCP tool returned error",
            )

        return SpellResult(
            spell_name=sanitized_name,
            status=SpellStatus.SUCCESS,
            content=content,
        )

    async def shutdown(self) -> None:
        """Gracefully closes all active MCP server sessions and clears state."""
        for session in list(self._sessions.values()):
            try:
                await session.close()
            except (OSError, RuntimeError) as exc:
                logger.debug("Error closing session during shutdown: %s", exc)
        self._sessions.clear()
        self._tool_map.clear()
