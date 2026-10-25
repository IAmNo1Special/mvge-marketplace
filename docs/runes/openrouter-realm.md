# OpenRouter Realm

Official OpenRouter provider realm for Mvge.

Connects agents to the OpenRouter API gateway — one realm, hundreds of foundation models — with SSE streaming, tool calls, and reasoning-parameter handling.

## What it registers

- A realm factory for the `openrouter` provider.
- Provider configuration with base URL `https://openrouter.ai/api/v1`.

When installed into `~/.agents/extensions/openrouter-realm`, Mvge discovers and activates it automatically.

## Hooks

`before_provider_request`, `after_provider_response` — the realm shapes outgoing requests (model routing, reasoning params, stream setup) and processes responses.

## Dependencies

`httpx>=0.27`. Python >= 3.13.
