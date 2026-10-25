from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import paths


class MCPTransportError(Exception):
    """Raised when MCP transport fails."""


@dataclass
class MCPServerInfo:
    name: str
    config: dict[str, Any]
    source_file: Path

    @property
    def description(self) -> str:
        return self.config.get("description", self.name)

    @property
    def transport(self) -> str:
        return self.config.get("transport", "stdio")


class MCPConfigDiscovery:
    """DCI over MCP config files. Searches *.mcp.json in standard locations."""

    def __init__(self, search_roots: list[Path] | None = None) -> None:
        self._explicit_search_roots = search_roots

    @property
    def search_roots(self) -> list[Path]:
        """The Rune directories scanned for ``*.mcp.json``, resolved per call.

        The default is the user-scope Rune layer from
        :func:`paths.extensions_root`, so a caller that names no root reads
        the layer the engine installs Runes into -- which is what
        ``rune_factory`` passes, so the two can no longer disagree.
        """
        if self._explicit_search_roots:
            return [Path(root) for root in self._explicit_search_roots]
        return [paths.extensions_root()]

    async def search(self, query: str) -> list[MCPServerInfo]:
        config_files = self._find_config_files()
        parsed = []
        for fpath in config_files:
            try:
                servers = self._parse_config(fpath)
            except (json.JSONDecodeError, OSError) as e:
                raise MCPTransportError(
                    f"Failed to parse MCP config {fpath}: {e}"
                ) from e
            for name, config in servers.items():
                info = MCPServerInfo(name=name, config=config, source_file=fpath)
                if self._matches_query(info, query):
                    parsed.append(info)
        return parsed

    def _find_config_files(self) -> list[Path]:
        files = []
        for root in self.search_roots:
            if not root.exists():
                continue
            files.extend(root.rglob("*.mcp.json"))
        return files

    @staticmethod
    def _parse_config(path: Path) -> dict[str, Any]:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
        servers = data.get("mcpServers")
        if servers is None:
            raise MCPTransportError(f"MCP config {path} missing 'mcpServers' key")
        if not isinstance(servers, dict):
            raise MCPTransportError(
                f"MCP config {path}: 'mcpServers' must be a dict, got {type(servers).__name__}"
            )
        return servers

    @staticmethod
    def _matches_query(info: MCPServerInfo, query: str) -> bool:
        q = query.lower()
        if q in info.name.lower():
            return True
        desc = info.config.get("description", "")
        if q in desc.lower():
            return True
        for c in info.config.get("capabilities", []):
            if q in c.lower():
                return True
        return False
