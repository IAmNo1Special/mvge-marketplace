from __future__ import annotations

from mvge_runes_opentelemetry_bridge.tracer import OTelTracer
from mvge_runes_opentelemetry_bridge.types import OTelConfig
from opentelemetry.trace import StatusCode


def test_otel_config_defaults() -> None:
    config = OTelConfig()
    assert config.service_name == "mvge"
    assert config.endpoint is None
    assert config.in_memory is False
    assert config.disabled is False


def test_tracer_session_and_turn_lifecycle() -> None:
    config = OTelConfig(in_memory=True, service_name="test-mvge")
    tracer = OTelTracer(config)

    tracer.start_session(session_id="tome-001", cwd="/workspace")
    tracer.start_turn(turn_id=1, model="anthropic/claude-opus-4.6")

    # Provider request
    tracer.start_provider_request(
        model_id="anthropic/claude-opus-4.6",
        realm="anthropic",
        config={"temperature": 0.5, "max_tokens": 2048},
    )
    tracer.record_provider_retry(attempt=1, error="Connection reset")
    tracer.end_provider_response(
        response={"usage": {"prompt_tokens": 100, "completion_tokens": 40}},
        mana_used=140,
    )

    # Tool call
    tracer.start_tool(
        spell_name="read_file",
        spell_cast_id="call-01",
        params={"path": "src/main.py"},
    )
    tracer.end_tool(spell_cast_id="call-01", result="file content", is_error=False)

    tracer.end_turn(stop_reason="stop", mana_used=140)
    tracer.end_session()

    spans = tracer.get_finished_spans()
    assert len(spans) == 4

    # 1. Chat span
    chat_span = next(s for s in spans if s.name.startswith("chat"))
    assert chat_span.name == "chat anthropic/claude-opus-4.6"
    assert chat_span.attributes["gen_ai.operation.name"] == "chat"
    assert chat_span.attributes["gen_ai.system"] == "anthropic"
    assert chat_span.attributes["gen_ai.request.model"] == "anthropic/claude-opus-4.6"
    assert chat_span.attributes["gen_ai.usage.input_tokens"] == 100
    assert chat_span.attributes["gen_ai.usage.output_tokens"] == 40
    assert any(e.name == "gen_ai.retry_attempt" for e in chat_span.events)

    # 2. Tool span
    tool_span = next(s for s in spans if s.name.startswith("execute_tool"))
    assert tool_span.name == "execute_tool read_file"
    assert tool_span.attributes["gen_ai.operation.name"] == "execute_tool"
    assert tool_span.attributes["gen_ai.tool.name"] == "read_file"
    assert tool_span.attributes["gen_ai.tool.call.id"] == "call-01"
    assert tool_span.attributes["gen_ai.tool.type"] == "function"

    # 3. Turn span
    turn_span = next(s for s in spans if s.name == "mvge.turn")
    assert turn_span.attributes["mvge.stop_reason"] == "stop"

    # 4. Session span
    session_span = next(s for s in spans if s.name == "mvge.session")
    assert session_span.attributes["gen_ai.conversation.id"] == "tome-001"
    assert session_span.attributes["mvge.orchestrator"] == "mvge"

    # Check hierarchy
    assert chat_span.parent.span_id == turn_span.context.span_id
    assert tool_span.parent.span_id == turn_span.context.span_id
    assert turn_span.parent.span_id == session_span.context.span_id


def test_tracer_abort_signal_marks_error() -> None:
    config = OTelConfig(in_memory=True)
    tracer = OTelTracer(config)

    tracer.start_session("tome-abort")
    tracer.start_turn(turn_id=1)
    tracer.start_tool("long_running_spell", "call-abort")

    # In-flight cancellation
    tracer.handle_abort("Cancelled via AbortSignal")
    tracer.end_tool("call-abort", is_error=True)
    tracer.end_turn(stop_reason="aborted")
    tracer.end_session()

    spans = tracer.get_finished_spans()
    tool_span = next(s for s in spans if s.name == "execute_tool long_running_spell")
    assert tool_span.status.status_code == StatusCode.ERROR
    assert "Cancelled via AbortSignal" in (tool_span.status.description or "")


def test_tracer_disabled() -> None:
    config = OTelConfig(disabled=True)
    tracer = OTelTracer(config)
    tracer.start_session("s1")
    tracer.start_turn(1)
    tracer.end_turn()
    tracer.end_session()
    assert tracer.get_finished_spans() == []


def test_tracer_endpoint_init() -> None:
    config = OTelConfig(
        endpoint="http://remote-collector:4318", headers={"Auth": "key"}
    )
    tracer = OTelTracer(config)
    assert tracer.get_finished_spans() == []


def test_tracer_edge_cases() -> None:
    config = OTelConfig(in_memory=True)
    tracer = OTelTracer(config)

    # 1. Non-serializable tool params
    class Unserializable:
        pass

    tracer.start_session("edge-session")
    tracer.start_turn(turn_id=1)
    tracer.start_tool("custom_spell", params={"bad": Unserializable()})
    # End tool with unknown ID (should pop most recent)
    tracer.end_tool("nonexistent-id", result="fallback", is_error=True)

    # 2. Provider response with object having mana_usage attribute
    from types import SimpleNamespace

    tracer.start_provider_request(
        model_id="google/gemini-2.5-flash",
        realm="google",
        config={"temperature": 0.2, "max_tokens": 1000, "top_p": 0.95},
    )
    # Abort while chat & turn are active
    tracer.handle_abort("Timeout")
    obj_resp = SimpleNamespace(
        mana_usage={"prompt_tokens": "20", "completion_tokens": "30"}
    )
    tracer.end_provider_response(response=obj_resp)

    # End provider response when none active
    tracer.end_provider_response(None)

    tracer.end_turn()
    tracer.end_session()

    spans = tracer.get_finished_spans()
    assert len(spans) >= 3
    tool_span = next(s for s in spans if s.name.startswith("execute_tool"))
    assert tool_span.status.status_code == StatusCode.ERROR
    chat_span = next(s for s in spans if s.name.startswith("chat"))
    assert chat_span.attributes["gen_ai.request.top_p"] == 0.95
    assert chat_span.attributes["gen_ai.usage.input_tokens"] == 20
    assert chat_span.attributes["gen_ai.usage.output_tokens"] == 30
