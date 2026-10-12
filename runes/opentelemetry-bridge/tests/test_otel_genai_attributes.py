from __future__ import annotations

from mvgeos_runes_opentelemetry_bridge.genai_attributes import (
    GEN_AI_CONVERSATION_ID,
    GEN_AI_OPERATION_NAME,
    GEN_AI_REQUEST_MAX_TOKENS,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_REQUEST_TEMPERATURE,
    GEN_AI_REQUEST_TOP_P,
    GEN_AI_RESPONSE_FINISH_REASONS,
    GEN_AI_SYSTEM,
    GEN_AI_TOOL_CALL_ID,
    GEN_AI_TOOL_NAME,
    GEN_AI_TOOL_TYPE,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    GEN_AI_USAGE_TOTAL_TOKENS,
    MVGEOS_ORCHESTRATOR,
    derive_provider_from_model,
)


def test_genai_semantic_convention_constants() -> None:
    assert GEN_AI_SYSTEM == "gen_ai.system"
    assert GEN_AI_OPERATION_NAME == "gen_ai.operation.name"
    assert GEN_AI_CONVERSATION_ID == "gen_ai.conversation.id"
    assert GEN_AI_REQUEST_MODEL == "gen_ai.request.model"
    assert GEN_AI_REQUEST_TEMPERATURE == "gen_ai.request.temperature"
    assert GEN_AI_REQUEST_MAX_TOKENS == "gen_ai.request.max_tokens"
    assert GEN_AI_REQUEST_TOP_P == "gen_ai.request.top_p"
    assert GEN_AI_USAGE_INPUT_TOKENS == "gen_ai.usage.input_tokens"
    assert GEN_AI_USAGE_OUTPUT_TOKENS == "gen_ai.usage.output_tokens"
    assert GEN_AI_USAGE_TOTAL_TOKENS == "gen_ai.usage.total_tokens"
    assert GEN_AI_RESPONSE_FINISH_REASONS == "gen_ai.response.finish_reasons"
    assert GEN_AI_TOOL_NAME == "gen_ai.tool.name"
    assert GEN_AI_TOOL_CALL_ID == "gen_ai.tool.call.id"
    assert GEN_AI_TOOL_TYPE == "gen_ai.tool.type"
    assert MVGEOS_ORCHESTRATOR == "mvgeos.orchestrator"


def test_derive_provider_from_model_with_vendor_prefix() -> None:
    assert derive_provider_from_model("anthropic/claude-opus-4.6") == "anthropic"
    assert derive_provider_from_model("openai/gpt-4o") == "openai"
    assert derive_provider_from_model("google/gemini-2.5-pro") == "google"


def test_derive_provider_from_model_falls_back_to_realm() -> None:
    assert (
        derive_provider_from_model("claude-3-sonnet", realm="anthropic") == "anthropic"
    )


def test_derive_provider_from_model_without_a_vendor() -> None:
    assert derive_provider_from_model("custom-model") == ""
