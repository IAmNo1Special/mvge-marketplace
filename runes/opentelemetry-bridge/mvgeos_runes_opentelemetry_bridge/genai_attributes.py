"""OpenTelemetry Generative AI Semantic Conventions.

Strictly follows open-telemetry/semantic-conventions-genai v1.42+ standard.

This vocabulary used to live in ``mvgeos_core.telemetry``. The engine never
consumed it -- it re-exported the symbols and nothing called them, while the
engine's README states there is no telemetry in the engine. This Rune is the
only consumer, so it owns the vocabulary. See ADR 0021.
"""

from __future__ import annotations

# GenAI Semantic Convention Attribute Keys (v1.42+)
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_CONVERSATION_ID = "gen_ai.conversation.id"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
GEN_AI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
GEN_AI_REQUEST_TOP_P = "gen_ai.request.top_p"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_USAGE_TOTAL_TOKENS = "gen_ai.usage.total_tokens"
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"
GEN_AI_TOOL_TYPE = "gen_ai.tool.type"

# MvgeOS-specific orchestrator identifier
MVGEOS_ORCHESTRATOR = "mvgeos.orchestrator"


def derive_provider_from_model(model_id: str, realm: str = "") -> str:
    """Derive the foundation model provider from model ID prefix or realm."""
    if "/" in model_id:
        return model_id.split("/", 1)[0].lower()
    if realm:
        return realm.lower()
    return ""


__all__ = [
    "GEN_AI_CONVERSATION_ID",
    "GEN_AI_OPERATION_NAME",
    "GEN_AI_REQUEST_MAX_TOKENS",
    "GEN_AI_REQUEST_MODEL",
    "GEN_AI_REQUEST_TEMPERATURE",
    "GEN_AI_REQUEST_TOP_P",
    "GEN_AI_RESPONSE_FINISH_REASONS",
    "GEN_AI_SYSTEM",
    "GEN_AI_TOOL_CALL_ID",
    "GEN_AI_TOOL_NAME",
    "GEN_AI_TOOL_TYPE",
    "GEN_AI_USAGE_INPUT_TOKENS",
    "GEN_AI_USAGE_OUTPUT_TOKENS",
    "GEN_AI_USAGE_TOTAL_TOKENS",
    "MVGEOS_ORCHESTRATOR",
    "derive_provider_from_model",
]
