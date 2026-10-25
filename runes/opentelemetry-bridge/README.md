# opentelemetry-bridge

Official OpenTelemetry GenAI tracing bridge for Mvge.

## Features
- **Lifecycle Tracing**: Instruments sessions, turns, provider requests/responses, and spell casts as nested spans.
- **Standard Telemetry**: Compatible with OpenTelemetry SDK and standard `OTEL_*` collectors.
- **Observability**: Records token usage, latency, and tool invocations per session.
