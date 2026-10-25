from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp import MCPError
from mcp.types import (
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    TextContent,
    TextResourceContents,
    Tool,
)
from mvge.core.spells import SpellStatus
from mvge_runes_mcp_bridge.manager import (
    MCPManager,
    extract_content,
    sanitize_tool_name,
)
from mvge_runes_mcp_bridge.types import (
    MCPDiagnostic,
    MCPDiagnosticKind,
    MCPServerConfig,
)


def test_sanitize_tool_name() -> None:
    assert sanitize_tool_name("my-server", "read:file") == "mcp_my_server_read_file"
    assert (
        sanitize_tool_name("db.prod.v1", "user.query-all")
        == "mcp_db_prod_v1_user_query_all"
    )
    assert (
        sanitize_tool_name("alpha_beta", "gamma_delta") == "mcp_alpha_beta_gamma_delta"
    )


def test_extract_content_rich_types() -> None:
    res = CallToolResult(
        content=[
            TextContent(type="text", text="First line"),
            ImageContent(type="image", data="base64str", mimeType="image/jpeg"),
            EmbeddedResource(
                type="resource",
                resource=TextResourceContents(
                    uri="file:///workspace/doc.pdf", text="pdf content"
                ),
            ),
        ]
    )
    text = extract_content(res)
    assert "First line" in text
    assert "[Embedded Image: image/jpeg]" in text
    assert "[Embedded Resource: file:///workspace/doc.pdf]" in text


def test_extract_content_fallback_types() -> None:
    class CustomItemWithText:
        text = "Custom text content"

    class CustomPlainItem:
        def __str__(self) -> str:
            return "Plain string representation"

    res = MagicMock()
    res.content = [CustomItemWithText(), CustomPlainItem()]
    text = extract_content(res)
    assert "Custom text content" in text
    assert "Plain string representation" in text


@pytest.mark.asyncio
async def test_connect_server_success_and_reconnect() -> None:
    manager = MCPManager()
    cfg = MCPServerConfig(name="srv1")

    mock_session = AsyncMock()
    mock_session.connect = AsyncMock()
    mock_session.list_tools.return_value = [
        Tool(name="t1", description="desc1", inputSchema={}),
        Tool(name="t2", description="desc2", inputSchema={}),
    ]
    mock_session.connected = True

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "mvge_runes_mcp_bridge.manager.MCPServerSession",
            lambda conf: mock_session,
        )

        # 1. Initial connect
        tools = await manager.connect_server(cfg)
        assert len(tools) == 2
        assert "mcp_srv1_t1" in manager.tool_map
        assert "mcp_srv1_t2" in manager.tool_map
        assert manager.tool_map["mcp_srv1_t1"] == ("srv1", "t1")

        # 2. Re-connect closes previous session
        await manager.connect_server(cfg)
        mock_session.close.assert_called()


@pytest.mark.asyncio
async def test_connect_server_disabled() -> None:
    manager = MCPManager()
    cfg = MCPServerConfig(name="srv_dis", disabled=True)
    tools = await manager.connect_server(cfg)
    assert tools == []
    assert len(manager.sessions) == 0


@pytest.mark.asyncio
async def test_connect_server_mcperror() -> None:
    manager = MCPManager()
    cfg = MCPServerConfig(name="srv_err")

    mock_session = AsyncMock()
    mock_session.connect.side_effect = MCPError(code=500, message="Server error")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "mvge_runes_mcp_bridge.manager.MCPServerSession",
            lambda conf: mock_session,
        )
        diagnostics: list[MCPDiagnostic] = []
        tools = await manager.connect_server(cfg, diagnostics=diagnostics)
        assert tools == []
        assert len(diagnostics) == 1
        assert diagnostics[0].kind == MCPDiagnosticKind.SERVER_FAILED


@pytest.mark.asyncio
async def test_call_tool_scenarios() -> None:
    manager = MCPManager()

    # 1. Tool not found
    res1 = await manager.call_tool("mcp_unknown_tool", {})
    assert res1.status == SpellStatus.ERROR
    assert "not found" in res1.error_message

    # 2. Server not connected
    manager._tool_map["mcp_mock_tool"] = ("mock_srv", "tool")
    res2 = await manager.call_tool("mcp_mock_tool", {})
    assert res2.status == SpellStatus.ERROR
    assert "not connected" in res2.error_message

    # Setup connected session
    mock_session = AsyncMock()
    mock_session.connected = True
    manager._sessions["mock_srv"] = mock_session

    # 3. Tool returning isError=True
    mock_session.call_tool.return_value = CallToolResult(
        isError=True,
        content=[TextContent(type="text", text="Command failed on server")],
    )
    res3 = await manager.call_tool("mcp_mock_tool", {"arg": 1})
    assert res3.status == SpellStatus.ERROR
    assert "Command failed on server" in res3.error_message

    # 4. Tool raising MCPError
    mock_session.call_tool.side_effect = MCPError(code=400, message="Invalid arg")
    res4 = await manager.call_tool("mcp_mock_tool", {})
    assert res4.status == SpellStatus.ERROR
    assert "Invalid arg" in res4.error_message

    # 5. Tool success
    mock_session.call_tool.side_effect = None
    mock_session.call_tool.return_value = CallToolResult(
        isError=False,
        content=[TextContent(type="text", text="Success output")],
    )
    res5 = await manager.call_tool("mcp_mock_tool", {})
    assert res5.status == SpellStatus.SUCCESS
    assert res5.content == "Success output"

    # 6. Tool raising RuntimeError
    mock_session.call_tool.side_effect = RuntimeError("Socket dropped")
    res6 = await manager.call_tool("mcp_mock_tool", {})
    assert res6.status == SpellStatus.ERROR
    assert "Tool execution failed" in res6.error_message


@pytest.mark.asyncio
async def test_connect_server_oserror() -> None:
    manager = MCPManager()
    cfg = MCPServerConfig(name="srv_os_err")

    mock_session = AsyncMock()
    mock_session.connect.side_effect = OSError("Connection refused")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "mvge_runes_mcp_bridge.manager.MCPServerSession",
            lambda conf: mock_session,
        )
        diagnostics: list[MCPDiagnostic] = []
        tools = await manager.connect_server(cfg, diagnostics=diagnostics)
        assert tools == []
        assert len(diagnostics) == 1
        assert diagnostics[0].kind == MCPDiagnosticKind.SERVER_FAILED


@pytest.mark.asyncio
async def test_shutdown() -> None:
    manager = MCPManager()
    mock_session = AsyncMock()
    manager._sessions["srv"] = mock_session
    manager._tool_map["mcp_srv_t"] = ("srv", "t")

    await manager.shutdown()
    mock_session.close.assert_called()
    assert len(manager.sessions) == 0
    assert len(manager.tool_map) == 0
