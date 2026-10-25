from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class OTelConfig:
    service_name: str = "mvge"
    endpoint: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    in_memory: bool = False
    disabled: bool = False


@dataclass
class SpanState:
    session_span: Any = None
    session_token: Any = None
    turn_span: Any = None
    turn_token: Any = None
    chat_span: Any = None
    chat_token: Any = None
    tool_spans: dict[str, Any] = field(default_factory=dict)
    tool_tokens: dict[str, Any] = field(default_factory=dict)
