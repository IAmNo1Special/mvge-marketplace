from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from mvge_runes_mcp_bridge.cli import app
from mvge_runes_mcp_bridge.types import MCPServerConfig, MCPTransport
from typer.testing import CliRunner

runner = CliRunner()


def test_cli_list_servers_empty() -> None:
    with patch(
        "mvge_runes_mcp_bridge.cli.get_prioritized_mcp_configs",
        return_value={},
    ):
        result = runner.invoke(app, ["list-servers"])
        assert result.exit_code == 0
        assert "No MCP servers configured" in result.output


def test_cli_list_servers_populated() -> None:
    cfg1 = MCPServerConfig(name="srv1", transport=MCPTransport.STDIO)
    cfg2 = MCPServerConfig(name="srv2", transport=MCPTransport.SSE, disabled=True)
    with patch(
        "mvge_runes_mcp_bridge.cli.get_prioritized_mcp_configs",
        return_value={"srv1": cfg1, "srv2": cfg2},
    ):
        result = runner.invoke(app, ["list-servers"])
        assert result.exit_code == 0
        assert "srv1: stdio" in result.output
        assert "srv2: DISABLED" in result.output


def test_cli_test_server_unknown() -> None:
    with patch(
        "mvge_runes_mcp_bridge.cli.get_prioritized_mcp_configs",
        return_value={},
    ):
        result = runner.invoke(app, ["test", "nonexistent"])
        assert result.exit_code == 1
        assert "Server 'nonexistent' not found" in result.output


def test_cli_test_server_success() -> None:
    cfg = MCPServerConfig(name="my_srv")
    with (
        patch(
            "mvge_runes_mcp_bridge.cli.get_prioritized_mcp_configs",
            return_value={"my_srv": cfg},
        ),
        patch(
            "mvge_runes_mcp_bridge.cli.MCPServerSession.connect",
            new_callable=AsyncMock,
        ),
        patch(
            "mvge_runes_mcp_bridge.cli.MCPServerSession.list_tools",
            new_callable=AsyncMock,
            return_value=[MagicMock(), MagicMock(), MagicMock()],
        ),
        patch(
            "mvge_runes_mcp_bridge.cli.MCPServerSession.close",
            new_callable=AsyncMock,
        ),
    ):
        result = runner.invoke(app, ["test", "my_srv"])
        assert result.exit_code == 0
        assert "OK: my_srv (3 tools)" in result.output


def test_cli_test_server_failure() -> None:
    cfg = MCPServerConfig(name="fail_srv")
    with (
        patch(
            "mvge_runes_mcp_bridge.cli.get_prioritized_mcp_configs",
            return_value={"fail_srv": cfg},
        ),
        patch(
            "mvge_runes_mcp_bridge.cli.MCPServerSession.connect",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Connection refused"),
        ),
        patch(
            "mvge_runes_mcp_bridge.cli.MCPServerSession.close",
            new_callable=AsyncMock,
        ),
    ):
        result = runner.invoke(app, ["test", "fail_srv"])
        assert result.exit_code == 0
        assert "FAIL: fail_srv: Connection refused" in result.output
