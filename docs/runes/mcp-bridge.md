# MCP Bridge

Official Model Context Protocol (MCP) bridge for Mvge.

A full MCP client: it auto-discovers server configurations, connects on demand, and exposes each server's tools as spells named `mcp_<server>_<tool>`. The model's tool surface grows as servers connect — no restarts, no static config in the prompt.

## Spells

Tools are registered dynamically per connected server as `mcp_<server>_<tool>`. Nothing is registered up front; the catalog is discovered live.

## Commands

- `/mcp list` — list connected servers and their tools.
- `/mcp test <server>` — test a server connection.
- `mvge mcp list-servers` — CLI: list configured servers.
- `mvge mcp test <server>` — CLI: test a server connection.

## Configuration

Server configs are discovered from `~/.agents/mcp.json` (global) and `<project>/.agents/mcp.json` (project-local).

## Hooks

`session_start`, `session_shutdown`.

## Dependencies

`mcp>=2.2.0`.

```bash
mvge rune install mcp-bridge
```
