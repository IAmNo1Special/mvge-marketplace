# mcp-bridge

Official Model Context Protocol (MCP) bridge rune for Mvge.

Provides full MCP client capabilities using `mcp>=2.2.0`,
auto-discovers `~/.agents/mcp.json` and `<project>/.agents/mcp.json`,
and exposes MCP tools as `mcp_{server}_{tool}` spells.

## Installation

```bash
mvge rune install mcp-bridge
```

## Commands

- `/mcp list` - List connected servers and tools
- `/mcp test <server>` - Test server connection
- `mvge mcp list-servers` - CLI: list configured servers
- `mvge mcp test <server>` - CLI: test server connection
