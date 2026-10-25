# OpenCode Realm

Official OpenCode Zen provider realm for Mvge.

Connects agents to the OpenCode Zen gateway. Zen serves an OpenAI Chat Completions compatible API, so any model in its catalog is reachable with an `opencode/<model-id>` slug. Its free tier is served from the same endpoint with the same key.

## What it registers

- A realm factory for the `opencode` provider.
- Provider configuration with base URL `https://opencode.ai/zen/v1`.

When installed into `~/.agents/extensions/opencode-realm`, Mvge discovers and activates it automatically.

Registering the provider is what lets a slug resolve with no catalog entry: the engine asks the registry whether it knows the `opencode` prefix and reads the base URL from the registration.

## Hooks

`before_provider_request`, `after_provider_response` — the realm shapes outgoing requests (model id, stream setup, tools) and processes responses.

## Dependencies

`httpx>=0.27`. Python >= 3.13.

## Getting a key and a model

```bash
mvge rune install opencode-realm
export OPENCODE_API_KEY="..."
mvge --model opencode/space-bunny-free
```

The catalog lives at `https://opencode.ai/zen/v1/models` in the standard `/v1/models` shape. Free models carry a `-free` suffix: `space-bunny-free`, `big-pickle`, `nemotron-3-ultra-free`, and others.

## How requests are shaped

- **Model id.** Mvge slugs carry a realm prefix but Zen publishes bare ids, so a leading `opencode/` is stripped on the way out. Both spellings work in configuration.
- **Spells.** Offered as standard `tools`. A cast in the transcript leaves as a `tool_calls` entry, and its result is sent back as a `tool` message keyed by the same id.
- **Attachments.** Native multimodal blocks (`text`, `image_url`, `file`) pass through in their documented wire shape. An unrecognized block becomes a text note rather than being dropped or forwarded as something the gateway cannot read.
- **Contemplation.** Requested by capping output tokens. This realm never sends a vendor-specific `reasoning` field, because that is an OpenRouter extension rather than part of the standard. Contemplation deltas are still read, so a model that thinks out loud lands in the transcript instead of being dropped.

## Model coverage

This realm speaks the Chat Completions dialect, which covers most of Zen's catalog. The entries Zen documents against other dialects — the Anthropic `messages` shape and the OpenAI `responses` shape — are not reachable through it, and this realm does not claim them. A model that names its own base URL overrides the realm default.

## Related

- [OpenRouter Realm](openrouter-realm.md) — the other realm, and the wider model catalog.