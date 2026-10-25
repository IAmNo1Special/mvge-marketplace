from __future__ import annotations

from mvge_runes_mcp_bridge.types import (
    MCPDiagnosticKind,
    MCPTransport,
)


def test_transport_enum_missing_and_normalization() -> None:
    assert MCPTransport("STDIO") == MCPTransport.STDIO
    assert MCPTransport("stdio") == MCPTransport.STDIO
    assert MCPTransport("streamable_http") == MCPTransport.STREAMABLE_HTTP
    assert MCPTransport("streamable-http") == MCPTransport.STREAMABLE_HTTP
    assert MCPTransport("SSE") == MCPTransport.SSE

    # Non-string or unknown
    assert MCPTransport._missing_(123) is None
    assert MCPTransport._missing_("unknown_protocol") is None


def test_diagnostic_kind_enum_missing_and_normalization() -> None:
    assert MCPDiagnosticKind("invalid_config") == MCPDiagnosticKind.INVALID_CONFIG
    assert MCPDiagnosticKind("INVALID_CONFIG") == MCPDiagnosticKind.INVALID_CONFIG
    assert MCPDiagnosticKind("server_failed") == MCPDiagnosticKind.SERVER_FAILED
    assert MCPDiagnosticKind("timeout") == MCPDiagnosticKind.TIMEOUT
    assert MCPDiagnosticKind("tool_error") == MCPDiagnosticKind.TOOL_ERROR

    # Non-string or unknown
    assert MCPDiagnosticKind._missing_(999) is None
    assert MCPDiagnosticKind._missing_("unknown_kind") is None
