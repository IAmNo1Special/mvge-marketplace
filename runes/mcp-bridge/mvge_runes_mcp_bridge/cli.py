from __future__ import annotations

import asyncio

import typer

from mvge_runes_mcp_bridge.config import get_prioritized_mcp_configs
from mvge_runes_mcp_bridge.session import MCPServerSession

app: typer.Typer = typer.Typer(help="MCP server management")


@app.command()
def list_servers() -> None:
    """List configured MCP servers."""
    configs = get_prioritized_mcp_configs()
    if not configs:
        typer.echo("No MCP servers configured.")
        return
    for name, cfg in configs.items():
        status = "DISABLED" if cfg.disabled else cfg.transport.value
        typer.echo(f"  {name}: {status}")


@app.command()
def test(server: str) -> None:
    """Test connection to an MCP server."""
    configs = get_prioritized_mcp_configs()
    if server not in configs:
        typer.echo(f"Server '{server}' not found.")
        raise typer.Exit(code=1)
    cfg = configs[server]

    async def _test() -> None:
        session = MCPServerSession(cfg)
        try:
            await session.connect()
            tools = await session.list_tools()
            typer.echo(f"OK: {server} ({len(tools)} tools)")
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"FAIL: {server}: {exc}")
        finally:
            await session.close()

    asyncio.run(_test())


# pytest collects any module-level function named `test*` (python_files = *.py);
# this is a typer CLI command, not a test.
test.__test__ = False


if __name__ == "__main__":
    app()
