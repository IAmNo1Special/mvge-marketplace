"""OpenTelemetry GenAI Tracer implementation for MvgeOS.

Follows open-telemetry/semantic-conventions-genai v1.42+ specification.
"""

from __future__ import annotations

import json
from typing import Any

from opentelemetry import context, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

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
from mvgeos_runes_opentelemetry_bridge.types import OTelConfig, SpanState


class OTelTracer:
    """Manages OpenTelemetry trace spans conforming strictly to GenAI conventions."""

    def __init__(self, config: OTelConfig) -> None:
        self.config = config
        self._state = SpanState()
        self._exporter: InMemorySpanExporter | None = None

        self._provider: trace.TracerProvider

        if config.disabled:
            self._provider = trace.NoOpTracerProvider()
            self._tracer = self._provider.get_tracer("mvgeos.opentelemetry_bridge")
            return

        resource = Resource.create({"service.name": config.service_name})
        sdk_provider = TracerProvider(resource=resource)
        self._provider = sdk_provider

        if config.in_memory:
            self._exporter = InMemorySpanExporter()
            self._processor = SimpleSpanProcessor(self._exporter)
            sdk_provider.add_span_processor(self._processor)
        elif config.endpoint:
            # Console or remote OTLP exporter
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore[import-not-found]
                    OTLPSpanExporter,
                )

                otlp_exp = OTLPSpanExporter(
                    endpoint=config.endpoint,
                    headers=config.headers,
                )
                sdk_provider.add_span_processor(SimpleSpanProcessor(otlp_exp))
            except Exception:  # noqa: BLE001, S110
                pass

        self._tracer = self._provider.get_tracer("mvgeos.opentelemetry_bridge")

    def get_finished_spans(self) -> list[ReadableSpan]:
        """Return captured in-memory spans (useful for tests and inspection)."""
        if self._exporter is not None:
            return list(self._exporter.get_finished_spans())
        return []

    def start_session(self, session_id: str, cwd: str = "") -> None:
        """Start the root session span."""
        span = self._tracer.start_span(
            "mvgeos.session",
            attributes={
                GEN_AI_CONVERSATION_ID: session_id,
                MVGEOS_ORCHESTRATOR: "mvgeos",
                "mvgeos.cwd": cwd,
            },
        )
        token = context.attach(trace.set_span_in_context(span))
        self._state.session_span = span
        self._state.session_token = token

    def start_turn(self, turn_id: int | str = 0, model: str = "") -> None:
        """Start turn span as child of session span."""
        parent_ctx = (
            trace.set_span_in_context(self._state.session_span)
            if self._state.session_span
            else None
        )
        attrs: dict[str, Any] = {"mvgeos.turn_id": turn_id}
        if model:
            attrs[GEN_AI_REQUEST_MODEL] = model
        span = self._tracer.start_span(
            "mvgeos.turn", context=parent_ctx, attributes=attrs
        )
        token = context.attach(trace.set_span_in_context(span))
        self._state.turn_span = span
        self._state.turn_token = token

    def start_provider_request(
        self,
        model_id: str,
        realm: str = "",
        config: dict[str, Any] | None = None,
    ) -> None:
        """Start chat span for model request as child of current turn."""
        parent_ctx = (
            trace.set_span_in_context(self._state.turn_span)
            if self._state.turn_span
            else None
        )
        provider = derive_provider_from_model(model_id, realm=realm)
        span_name = f"chat {model_id}"
        attrs: dict[str, Any] = {
            GEN_AI_OPERATION_NAME: "chat",
            GEN_AI_SYSTEM: provider,
            GEN_AI_REQUEST_MODEL: model_id,
        }
        if config:
            if "temperature" in config:
                attrs[GEN_AI_REQUEST_TEMPERATURE] = float(config["temperature"])
            if "max_tokens" in config:
                attrs[GEN_AI_REQUEST_MAX_TOKENS] = int(config["max_tokens"])
            if "top_p" in config:
                attrs[GEN_AI_REQUEST_TOP_P] = float(config["top_p"])

        span = self._tracer.start_span(span_name, context=parent_ctx, attributes=attrs)
        token = context.attach(trace.set_span_in_context(span))
        self._state.chat_span = span
        self._state.chat_token = token

    def record_provider_retry(self, attempt: int, error: str = "") -> None:
        """Record retry event on active chat span."""
        if self._state.chat_span:
            self._state.chat_span.add_event(
                "gen_ai.retry_attempt",
                attributes={"attempt": attempt, "error": error},
            )

    def end_provider_response(
        self,
        response: Any = None,
        mana_used: int = 0,
    ) -> None:
        """Attach usage attributes and end active chat span."""
        span = self._state.chat_span
        if span is None:
            return

        if isinstance(response, dict):
            usage = response.get("usage") or response.get("mana_usage") or {}
            in_tok = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
            out_tok = usage.get("completion_tokens") or usage.get("output_tokens") or 0
            tot_tok = in_tok + out_tok or mana_used
            span.set_attribute(GEN_AI_USAGE_INPUT_TOKENS, in_tok)
            span.set_attribute(GEN_AI_USAGE_OUTPUT_TOKENS, out_tok)
            span.set_attribute(GEN_AI_USAGE_TOTAL_TOKENS, tot_tok)
            span.set_attribute(GEN_AI_RESPONSE_FINISH_REASONS, ["stop"])
        elif hasattr(response, "mana_usage"):
            usage = getattr(response, "mana_usage", {}) or {}
            in_tok = int(usage.get("prompt_tokens", 0))
            out_tok = int(usage.get("completion_tokens", 0))
            span.set_attribute(GEN_AI_USAGE_INPUT_TOKENS, in_tok)
            span.set_attribute(GEN_AI_USAGE_OUTPUT_TOKENS, out_tok)
            span.set_attribute(GEN_AI_USAGE_TOTAL_TOKENS, in_tok + out_tok or mana_used)
            span.set_attribute(GEN_AI_RESPONSE_FINISH_REASONS, ["stop"])

        span.end()
        if self._state.chat_token:
            context.detach(self._state.chat_token)
            self._state.chat_token = None
        self._state.chat_span = None

    def start_tool(
        self,
        spell_name: str,
        spell_cast_id: str = "",
        params: dict[str, Any] | None = None,
    ) -> None:
        """Start tool execution span as child of current turn."""
        parent_ctx = (
            trace.set_span_in_context(self._state.turn_span)
            if self._state.turn_span
            else None
        )
        span_name = f"execute_tool {spell_name}"
        attrs: dict[str, Any] = {
            GEN_AI_OPERATION_NAME: "execute_tool",
            GEN_AI_TOOL_NAME: spell_name,
            GEN_AI_TOOL_TYPE: "function",
        }
        if spell_cast_id:
            attrs[GEN_AI_TOOL_CALL_ID] = spell_cast_id
        if params:
            try:
                attrs["gen_ai.tool.parameters"] = json.dumps(params)
            except (TypeError, ValueError):
                pass

        span = self._tracer.start_span(span_name, context=parent_ctx, attributes=attrs)
        key = spell_cast_id or spell_name
        self._state.tool_spans[key] = span

    def end_tool(
        self,
        spell_cast_id: str,
        result: Any = None,
        is_error: bool = False,
    ) -> None:
        """End tool execution span."""
        span = self._state.tool_spans.pop(spell_cast_id, None)
        if span is None and self._state.tool_spans:
            # Pop most recently added tool span
            key = next(reversed(self._state.tool_spans))
            span = self._state.tool_spans.pop(key)

        if span is not None:
            if is_error and span.status.status_code != StatusCode.ERROR:
                span.set_status(StatusCode.ERROR)
            span.end()

    def handle_abort(self, reason: str = "Cancelled via AbortSignal") -> None:
        """Mark all active spans as ERROR upon cancellation."""
        for span in self._state.tool_spans.values():
            span.set_status(StatusCode.ERROR, description=reason)
        if self._state.chat_span:
            self._state.chat_span.set_status(StatusCode.ERROR, description=reason)
        if self._state.turn_span:
            self._state.turn_span.set_status(StatusCode.ERROR, description=reason)

    def end_turn(self, stop_reason: str = "", mana_used: int = 0) -> None:
        """End turn span."""
        span = self._state.turn_span
        if span is not None:
            if stop_reason:
                span.set_attribute("mvgeos.stop_reason", stop_reason)
            if mana_used:
                span.set_attribute("mvgeos.mana_used", mana_used)
            span.end()
            if self._state.turn_token:
                context.detach(self._state.turn_token)
                self._state.turn_token = None
            self._state.turn_span = None

    def end_session(self) -> None:
        """End root session span and flush."""
        span = self._state.session_span
        if span is not None:
            span.end()
            if self._state.session_token:
                context.detach(self._state.session_token)
                self._state.session_token = None
            self._state.session_span = None
        if hasattr(self._provider, "shutdown"):
            self._provider.shutdown()
