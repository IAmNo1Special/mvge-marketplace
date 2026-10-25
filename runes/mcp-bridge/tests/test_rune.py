from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from mcp.types import Tool
from mvge.core.spells import SpellResult, SpellStatus
from mvge.runes.rune_api import RuneAPI
from mvge.runes.rune_runner import RuneRunner
from mvge.runes.types import RuneContext, SigilHook, SpellDefinition
from mvgeos_runes_mcp_bridge.rune import rune_factory
from mvgeos_runes_mcp_bridge.types import MCPServerConfig


@pytest.fixture
def api() -> RuneAPI:
    context = RuneContext(cwd="/fake/cwd")
    runner = RuneRunner()
    runner.bind_context(context)
    return RuneAPI(runner, rune_name="mcp-bridge")


@pytest.mark.asyncio
async def test_rune_factory_hooks_and_command_registration(api: RuneAPI) -> None:
    rune_factory(api)

    registered_commands = [c.name for c in api._runner.get_commands()]
    assert "mcp" in registered_commands

    assert len(api._runner.get_sigil_handlers(SigilHook.SESSION_START)) == 1
    assert len(api._runner.get_sigil_handlers(SigilHook.SESSION_SHUTDOWN)) == 1


@pytest.mark.asyncio
async def test_session_start_discovery_and_spell_execution(api: RuneAPI) -> None:
    cfg = MCPServerConfig(name="test_srv")
    mock_tools = [
        Tool(
            name="echo",
            description="Echo input",
            inputSchema={"type": "object", "properties": {"msg": {"type": "string"}}},
        )
    ]

    from mcp.types import CallToolResult, TextContent

    mock_res = CallToolResult(
        isError=False,
        content=[TextContent(type="text", text="echo result")],
    )

    with (
        patch(
            "mvgeos_runes_mcp_bridge.rune.get_prioritized_mcp_configs",
            return_value={"test_srv": cfg},
        ),
        patch(
            "mvgeos_runes_mcp_bridge.manager.MCPServerSession.connect",
            new_callable=AsyncMock,
        ),
        patch(
            "mvgeos_runes_mcp_bridge.manager.MCPServerSession.list_tools",
            new_callable=AsyncMock,
            return_value=mock_tools,
        ),
        patch(
            "mvgeos_runes_mcp_bridge.manager.MCPServerSession.connected",
            new_callable=PropertyMock,
            return_value=True,
        ),
        patch(
            "mvgeos_runes_mcp_bridge.manager.MCPServerSession.call_tool",
            new_callable=AsyncMock,
            return_value=mock_res,
        ),
    ):
        rune_factory(api)
        # Fire SESSION_START hook
        handlers = api._runner.get_sigil_handlers(SigilHook.SESSION_START)
        for h in handlers:
            res = h()
            if asyncio.iscoroutine(res):
                await res

        spells = api._runner.get_all_registered_spells()
        assert len(spells) == 1
        spell_def: SpellDefinition = spells[0]
        assert spell_def.name == "mcp_test_srv_echo"
        assert spell_def.description == "Echo input"

        # Execute the spell handler
        handler_res = await spell_def.execute("cast_1", {"msg": "hello"})
        assert isinstance(handler_res, SpellResult)
        assert handler_res.status == SpellStatus.SUCCESS


@pytest.mark.asyncio
async def test_mcp_command_list_and_test(api: RuneAPI) -> None:
    rune_factory(api)

    cmd_obj = next(c for c in api._runner.get_commands() if c.name == "mcp")
    handler = cmd_obj.handler

    # 1. Empty list
    res_empty = await handler("list")
    assert "No MCP servers connected" in res_empty

    # 2. Test without server name
    res_no_arg = await handler("test")
    assert "FAIL: No server specified" in res_no_arg

    # 3. Test unknown server
    with patch(
        "mvgeos_runes_mcp_bridge.rune.get_prioritized_mcp_configs",
        return_value={},
    ):
        res_unknown = await handler("test nonexistent")
        assert "FAIL: Server 'nonexistent' not found" in res_unknown

    # 4. Test success
    cfg = MCPServerConfig(name="live_srv")
    with (
        patch(
            "mvgeos_runes_mcp_bridge.rune.get_prioritized_mcp_configs",
            return_value={"live_srv": cfg},
        ),
        patch(
            "mvgeos_runes_mcp_bridge.session.MCPServerSession.connect",
            new_callable=AsyncMock,
        ),
        patch(
            "mvgeos_runes_mcp_bridge.session.MCPServerSession.list_tools",
            new_callable=AsyncMock,
            return_value=[MagicMock(), MagicMock()],
        ),
        patch(
            "mvgeos_runes_mcp_bridge.session.MCPServerSession.close",
            new_callable=AsyncMock,
        ),
    ):
        res_ok = await handler("test live_srv")
        assert "OK: live_srv (2 tools)" in res_ok

    # 5. Unknown command
    res_unknown_cmd = await handler("bogus")
    assert "Unknown MCP command: 'bogus'" in res_unknown_cmd


@pytest.mark.asyncio
async def test_session_shutdown(api: RuneAPI) -> None:
    rune_factory(api)
    handlers = api._runner.get_sigil_handlers(SigilHook.SESSION_SHUTDOWN)
    for h in handlers:
        res = h()
        if asyncio.iscoroutine(res):
            await res


@pytest.mark.asyncio
async def test_mcp_command_list_populated(api: RuneAPI) -> None:
    cfg = MCPServerConfig(name="live_srv")
    mock_tools = [Tool(name="t1", description="", inputSchema={})]
    with (
        patch(
            "mvgeos_runes_mcp_bridge.rune.get_prioritized_mcp_configs",
            return_value={"live_srv": cfg},
        ),
        patch(
            "mvgeos_runes_mcp_bridge.manager.MCPServerSession.connect",
            new_callable=AsyncMock,
        ),
        patch(
            "mvgeos_runes_mcp_bridge.manager.MCPServerSession.list_tools",
            new_callable=AsyncMock,
            return_value=mock_tools,
        ),
        patch(
            "mvgeos_runes_mcp_bridge.manager.MCPServerSession.connected",
            new_callable=PropertyMock,
            return_value=True,
        ),
    ):
        rune_factory(api)
        for h in api._runner.get_sigil_handlers(SigilHook.SESSION_START):
            await h()

        cmd_obj = next(c for c in api._runner.get_commands() if c.name == "mcp")
        res = await cmd_obj.handler("list")
        assert "Connected MCP servers:" in res
        assert "live_srv (1 tools):" in res
        assert "- mcp_live_srv_t1" in res


@pytest.mark.asyncio
async def test_mcp_command_test_server_failure(api: RuneAPI) -> None:
    rune_factory(api)
    cmd_obj = next(c for c in api._runner.get_commands() if c.name == "mcp")
    cfg = MCPServerConfig(name="fail_srv")
    with (
        patch(
            "mvgeos_runes_mcp_bridge.rune.get_prioritized_mcp_configs",
            return_value={"fail_srv": cfg},
        ),
        patch(
            "mvgeos_runes_mcp_bridge.session.MCPServerSession.connect",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Connection refused"),
        ),
        patch(
            "mvgeos_runes_mcp_bridge.session.MCPServerSession.close",
            new_callable=AsyncMock,
        ),
    ):
        res = await cmd_obj.handler("test fail_srv")
        assert "FAIL: fail_srv: Connection refused" in res
