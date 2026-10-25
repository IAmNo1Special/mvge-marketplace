from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.types import CallToolResult, TextContent, Tool
from mvge_runes_mcp_bridge.session import MCPServerSession
from mvge_runes_mcp_bridge.types import MCPServerConfig, MCPTransport


@pytest.mark.asyncio
async def test_session_lifecycle_disconnected_errors() -> None:
    cfg = MCPServerConfig(name="test_srv", transport=MCPTransport.STDIO)
    session = MCPServerSession(cfg)
    assert not session.connected

    with pytest.raises(RuntimeError, match="not connected"):
        await session.list_tools()

    with pytest.raises(RuntimeError, match="not connected"):
        await session.call_tool("foo", {})


@pytest.mark.asyncio
async def test_session_connect_stdio_and_list_tools() -> None:
    cfg = MCPServerConfig(
        name="stdio_srv",
        transport=MCPTransport.STDIO,
        command="python",
        args=["server.py"],
        env={"FOO": "BAR"},
        cwd="/tmp",
    )
    session = MCPServerSession(cfg)

    mock_client_session = AsyncMock()
    mock_tools_result = MagicMock()
    mock_tools_result.tools = [
        Tool(
            name="hello",
            description="Say hello",
            inputSchema={"type": "object"},
        )
    ]
    mock_client_session.list_tools.return_value = mock_tools_result

    mock_stdio_cm = AsyncMock()
    mock_stdio_cm.__aenter__.return_value = (AsyncMock(), AsyncMock())
    mock_stdio_cm.__aexit__.return_value = None

    mock_session_cm = AsyncMock()
    mock_session_cm.__aenter__.return_value = mock_client_session
    mock_session_cm.__aexit__.return_value = None

    with (
        patch(
            "mvge_runes_mcp_bridge.session.stdio_client",
            return_value=mock_stdio_cm,
        ),
        patch(
            "mvge_runes_mcp_bridge.session.ClientSession",
            return_value=mock_session_cm,
        ),
    ):
        await session.connect()
        assert session.connected

        tools = await session.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "hello"

        mock_client_session.call_tool.return_value = CallToolResult(content=[])
        res = await session.call_tool("hello", {"x": 1}, signal=None)
        assert res.content == []

        await session.close()
        assert not session.connected


@pytest.mark.asyncio
async def test_session_connect_sse() -> None:
    cfg = MCPServerConfig(
        name="sse_srv",
        transport=MCPTransport.SSE,
        url="http://localhost:8000/sse",
        headers={"Authorization": "Bearer token"},
        timeout=15.0,
    )
    session = MCPServerSession(cfg)

    mock_client_session = AsyncMock()
    mock_sse_cm = AsyncMock()
    mock_sse_cm.__aenter__.return_value = (AsyncMock(), AsyncMock())
    mock_sse_cm.__aexit__.return_value = None

    mock_session_cm = AsyncMock()
    mock_session_cm.__aenter__.return_value = mock_client_session
    mock_session_cm.__aexit__.return_value = None

    with (
        patch(
            "mvge_runes_mcp_bridge.session.sse_client",
            return_value=mock_sse_cm,
        ),
        patch(
            "mvge_runes_mcp_bridge.session.ClientSession",
            return_value=mock_session_cm,
        ),
    ):
        await session.connect()
        assert session.connected
        await session.close()
        assert not session.connected


@pytest.mark.asyncio
async def test_session_connect_streamable_http() -> None:
    cfg = MCPServerConfig(
        name="http_srv",
        transport=MCPTransport.STREAMABLE_HTTP,
        url="http://localhost:8000/mcp",
    )
    session = MCPServerSession(cfg)

    mock_client_session = AsyncMock()
    mock_http_cm = AsyncMock()
    mock_http_cm.__aenter__.return_value = (
        AsyncMock(),
        AsyncMock(),
        AsyncMock(),
    )
    mock_http_cm.__aexit__.return_value = None

    mock_session_cm = AsyncMock()
    mock_session_cm.__aenter__.return_value = mock_client_session
    mock_session_cm.__aexit__.return_value = None

    with (
        patch(
            "mvge_runes_mcp_bridge.session.streamable_http_client",
            return_value=mock_http_cm,
        ),
        patch(
            "mvge_runes_mcp_bridge.session.ClientSession",
            return_value=mock_session_cm,
        ),
    ):
        await session.connect()
        assert session.connected
        await session.close()


@pytest.mark.asyncio
async def test_session_connect_unsupported_transport() -> None:
    cfg = MCPServerConfig(name="bad_srv")
    cfg.transport = "invalid_transport"  # type: ignore
    session = MCPServerSession(cfg)

    with pytest.raises(ValueError, match="Unsupported transport"):
        await session.connect()


@pytest.mark.asyncio
async def test_session_call_tool_with_abort_signal() -> None:
    cfg = MCPServerConfig(name="abort_srv")
    session = MCPServerSession(cfg)

    mock_client_session = AsyncMock()
    session._session = mock_client_session

    # 1. Pre-aborted signal
    mock_signal = MagicMock()
    mock_signal.raise_if_aborted.side_effect = TimeoutError("Aborted")
    with pytest.raises(TimeoutError, match="Aborted"):
        await session.call_tool("some_tool", {}, signal=mock_signal)

    # 2. In-flight abort
    mock_signal_inflight = MagicMock()
    mock_signal_inflight.raise_if_aborted = MagicMock()

    async def _slow_call(*args, **kwargs):
        await asyncio.sleep(1.0)
        return CallToolResult(content=[TextContent(type="text", text="done")])

    mock_client_session.call_tool.side_effect = _slow_call

    def _trigger_abort(callback):
        callback()

    mock_signal_inflight.on_abort.side_effect = _trigger_abort

    with pytest.raises(asyncio.CancelledError):
        await session.call_tool("slow_tool", {}, signal=mock_signal_inflight)
