# opencode-realm

Official OpenCode Zen provider realm for Mvge.

## Overview

Zen is an OpenAI Chat Completions compatible gateway. This Rune points Mvge
at it, so any model Zen serves is reachable with an `opencode/<model-id>` slug.
Zen's free tier is served from the same endpoint with the same key -- there is
no separate free path.

## Manifest configuration

- Runtime: Python (>= 3.13)
- Entry point: `rune.py`
- Python dependencies: `httpx>=0.27`
- Registered hooks: `before_provider_request`, `after_provider_response`

## Install

```bash
mvge rune install opencode-realm
```

## Configure

Set the key and pick a model. The key is the same credential you use with
`/connect` in the OpenCode TUI.

```bash
export OPENCODE_API_KEY="..."
mvge --model opencode/space-bunny-free
```

The model catalog is at `https://opencode.ai/zen/v1/models`, in the standard
`/v1/models` shape. Free models carry a `-free` suffix in their id, for example
`space-bunny-free`, `big-pickle`, and `nemotron-3-ultra-free`.

## What it registers

- A realm factory for the `opencode` prefix
- A provider entry naming `https://opencode.ai/zen/v1` as the base URL

Registering the provider is what lets a slug resolve with no catalog entry:
the engine asks the registry whether it knows the `opencode` prefix and reads
the base URL from the registration.

## Request handling

- Mvge slugs carry a realm prefix (`opencode/space-bunny-free`) but Zen
  publishes bare ids (`space-bunny-free`), so the prefix is stripped on the way
  out. Both spellings work in configuration.
- Spells are offered as standard `tools`, and a cast in the transcript leaves
  as a `tool_calls` entry paired with its result by id.
- Native multimodal content blocks (`text`, `image_url`, `file`) pass through in
  their documented wire shape; anything unrecognized becomes a text note rather
  than being dropped or forwarded as something the gateway cannot read.
- Contemplation is requested by capping output tokens. This Rune never sends a
  vendor-specific `reasoning` field, because that is an OpenRouter extension and
  not part of the standard. Contemplation deltas are still read, so a model
  that thinks out loud lands in the transcript instead of being dropped.

## Model coverage

This Rune speaks the Chat Completions dialect, which covers most of Zen's
catalog. The entries Zen documents against other dialects -- the Anthropic
`messages` shape and the OpenAI `responses` shape -- are not reachable through
it, and this Rune does not claim them.

A model that names its own base URL overrides the realm default, which is the
seam to use for a gateway endpoint this Rune does not build itself.

## Related

- `openrouter-realm` -- the other realm, and the wider model catalog