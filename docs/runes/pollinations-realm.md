# Pollinations Realm

Pollinations provider realm for Mvge, as a bootstrap-only fallback.

Connects agents to Pollinations' legacy text endpoint — an OpenAI-compatible
chat completions API that answers without an API key — so a first-time user
can complete a bootstrap turn before setting up a keyed provider.

## What it registers

- A realm factory for the `pollinations` provider.
- Provider configuration with base URL `https://text.pollinations.ai/openai`.

When installed into `~/.agents/extensions/pollinations-realm`, Mvge discovers
and activates it automatically.

## Hooks

`before_provider_request`, `after_provider_response` — the realm shapes
outgoing requests (slug stripping, keyless header handling, stream setup)
and processes responses.

## Anonymous model

Exactly one anonymous-tier model today: `openai-fast` (GPT-OSS 20B), wire
alias `openai`, shipped default slug `pollinations/openai`. Published
anonymous limit is about 1 request per 15 seconds, with no published
token-per-day cap. The legacy lane can close without warning, as OpenCode
Zen's anonymous tier did, so this realm is a bootstrap fallback, not a
production foundation. See the rune's own README for the full warning and
for the engine follow-up (credential-gate exemption and the explicit
OpenRouter-key bootstrap prompt).

## Dependencies

`httpx>=0.27`. Python >= 3.13.
