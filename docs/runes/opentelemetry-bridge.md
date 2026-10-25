# OpenTelemetry Bridge

Official OpenTelemetry GenAI tracing bridge for Mvge.

Instruments the agent lifecycle end to end: sessions, turns, provider requests/responses, and individual spell casts all become trace spans you can inspect in any OpenTelemetry-compatible backend. When something behaves oddly three turns in, the trace tells you exactly which call, which tool, and which tokens were involved.

## Hooks

`session_start`, `session_shutdown`, `turn_start`, `turn_end`, `before_provider_request`, `after_provider_response`, `before_spell_cast`, `after_spell_result` — each boundary opens/closes a span, so a full session renders as one nested trace.

## Commands

- `mvge otel …` — tracing controls.

## Dependencies

`opentelemetry-api>=1.30.0`, `opentelemetry-sdk>=1.30.0`. Point the SDK at your collector via the standard `OTEL_*` environment variables.
