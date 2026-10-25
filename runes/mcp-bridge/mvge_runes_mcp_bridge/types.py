"""Type definitions for the MCP Bridge rune."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class MCPTransport(StrEnum):
    """Supported MCP transport protocols."""

    STDIO = "stdio"
    SSE = "sse"
    STREAMABLE_HTTP = "streamable-http"

    @classmethod
    def _missing_(cls, value: object) -> MCPTransport | None:
        if isinstance(value, str):
            normalized = value.lower().replace("_", "-")
            for member in cls:
                if member.value == normalized:
                    return member
        return None


class MCPDiagnosticKind(StrEnum):
    """Kinds of MCP diagnostics."""

    INVALID_CONFIG = "invalid_config"
    SERVER_FAILED = "server_failed"
    TIMEOUT = "timeout"
    TOOL_ERROR = "tool_error"

    @classmethod
    def _missing_(cls, value: object) -> MCPDiagnosticKind | None:
        if isinstance(value, str):
            for member in cls:
                if member.value == value.lower() or member.name == value.upper():
                    return member
        return None


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server instance."""

    name: str
    transport: MCPTransport = MCPTransport.STDIO
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0
    disabled: bool = False


@dataclass
class MCPDiagnostic:
    """Diagnostic entry for MCP operations."""

    kind: MCPDiagnosticKind
    server_name: str
    message: str
    path: str = ""


__all__ = [
    "MCPDiagnostic",
    "MCPDiagnosticKind",
    "MCPServerConfig",
    "MCPTransport",
]
