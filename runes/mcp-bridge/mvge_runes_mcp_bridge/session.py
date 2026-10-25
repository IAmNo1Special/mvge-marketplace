"""MCP server session management for mcp-bridge rune."""

from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, MCPError, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, Tool

try:
    from .types import MCPServerConfig, MCPTransport
except ImportError:
    from mvge_runes_mcp_bridge.types import (  # type: ignore[no-redef]
        MCPServerConfig,
        MCPTransport,
    )

__all__ = ["MCPError", "MCPServerSession"]


class MCPServerSession:
    """Manages connection and tool invocation lifecycle for an MCP server."""

    def __init__(self, config: MCPServerConfig) -> None:
        """Initializes the MCP server session with configuration.

        Args:
            config: Server configuration specifying transport and connection
                parameters.
        """
        self.config = config
        self._session: ClientSession | None = None
        self._read_stream: Any | None = None
        self._write_stream: Any | None = None
        self._exit_stack: AsyncExitStack = AsyncExitStack()

    @property
    def connected(self) -> bool:
        """Returns True if the session is currently connected."""
        return self._session is not None

    async def connect(self) -> None:
        """Connects to the MCP server based on configured transport.

        Uses AsyncExitStack to manage client transport and session lifecycles.

        Raises:
            ValueError: If transport type is unsupported.
            MCPError: If connection or initialization fails at protocol level.
        """
        transport = self.config.transport
        is_stdio = transport == MCPTransport.STDIO or str(transport).lower() in (
            "stdio",
            "mcptransport.stdio",
        )
        is_sse = transport == MCPTransport.SSE or str(transport).lower() in (
            "sse",
            "mcptransport.sse",
        )
        is_streamable_http = transport == MCPTransport.STREAMABLE_HTTP or str(
            transport
        ).lower() in (
            "streamable_http",
            "streamable-http",
            "mcptransport.streamable_http",
        )

        if is_stdio:
            env = {**os.environ}
            if self.config.env:
                env.update(self.config.env)

            cwd_str: str | None = None
            if self.config.cwd is not None:
                cwd_str = str(Path(self.config.cwd))

            params = StdioServerParameters(
                command=self.config.command or "",
                args=list(self.config.args) if self.config.args else [],
                env=env,
                cwd=cwd_str,
            )
            read, write = await self._exit_stack.enter_async_context(
                stdio_client(params)
            )
            self._read_stream = read
            self._write_stream = write
            self._session = await self._exit_stack.enter_async_context(
                ClientSession(read, write)
            )
            await self._session.initialize()

        elif is_sse:
            sse_kwargs: dict[str, Any] = {
                "url": self.config.url or "",
                "headers": self.config.headers or None,
            }
            if self.config.timeout is not None:
                sse_kwargs["timeout"] = float(self.config.timeout)

            read, write = await self._exit_stack.enter_async_context(
                sse_client(**sse_kwargs)
            )
            self._read_stream = read
            self._write_stream = write
            self._session = await self._exit_stack.enter_async_context(
                ClientSession(read, write)
            )
            await self._session.initialize()

        elif is_streamable_http:
            url = self.config.url or ""
            try:
                stream_res = await self._exit_stack.enter_async_context(
                    streamable_http_client(
                        url=url,
                        headers=self.config.headers,  # type: ignore[call-arg]
                    )
                )
            except TypeError:
                stream_res = await self._exit_stack.enter_async_context(
                    streamable_http_client(url=url)
                )
            read, write, *_ = stream_res
            self._read_stream = read
            self._write_stream = write
            self._session = await self._exit_stack.enter_async_context(
                ClientSession(read, write)
            )
            await self._session.initialize()

        else:
            raise ValueError(f"Unsupported transport: {self.config.transport}")

    async def list_tools(self) -> list[Tool]:
        """Lists available tools from the connected MCP server.

        Returns:
            List of Tool instances provided by the server.

        Raises:
            RuntimeError: If the server is not connected.
            MCPError: If listing tools fails at protocol level.
        """
        if self._session is None:
            raise RuntimeError(f"Server '{self.config.name}' is not connected")
        result = await self._session.list_tools()
        return list(result.tools)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        signal: Any | None = None,
    ) -> CallToolResult:
        """Invokes a tool on the connected MCP server.

        Args:
            name: Tool name to invoke.
            arguments: Dictionary of arguments for the tool.
            signal: Optional AbortSignal to cancel in-flight invocation.

        Returns:
            CallToolResult returned from the MCP server.

        Raises:
            RuntimeError: If the server is not connected.
            MCPError: If tool invocation fails at protocol level.
        """
        if self._session is None:
            raise RuntimeError(f"Server '{self.config.name}' is not connected")

        if signal is not None and hasattr(signal, "raise_if_aborted"):
            signal.raise_if_aborted()

        if signal is None:
            return await self._session.call_tool(name, arguments)

        task = asyncio.create_task(self._session.call_tool(name, arguments))
        if hasattr(signal, "on_abort"):
            signal.on_abort(task.cancel)
        try:
            return await task
        except asyncio.CancelledError:
            if hasattr(signal, "raise_if_aborted"):
                signal.raise_if_aborted()
            raise

    async def close(self) -> None:
        """Closes the server session and releases transport resources."""
        try:
            await self._exit_stack.aclose()
        finally:
            self._session = None
            self._read_stream = None
            self._write_stream = None
