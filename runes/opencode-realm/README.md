# opencode-realm

Official OpenCode Zen and Go provider realms for Mvge.

## Overview

This Rune provides two provider entries, not one. Zen and Go are separate
billing products behind the same credential, so the provider name in the
model slug is the billing lane:

- Zen: `opencode/<model-id>`, at `https://opencode.ai/inference/openai/v1`
- Go: `opencode-go/<model-id>`, at `https://opencode.ai/inference/go/v1`

These are Chat Completions bases: the realm appends `/chat/completions`.
The old `/zen/*` bases are not claimed dead -- `/zen/*` and `/inference/*`
have been documented as aliases for Go -- but they are no longer the
defaults. A model that names its own base URL still reaches them through
the per-model override (see Model coverage).

There is no fallback between them, in either direction. A Go model with no
per-model base URL override is served from the Go endpoint, never from Zen;
a Zen model is served from Zen, never from Go. The old single-entry shape,
where a per-model base URL override was the only way to reach Go, hid the
billing lane inside model entries and let any model missing an override
silently land on Zen.

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
`/connect` in the OpenCode TUI, and it is the same key for both entries:
`OPENCODE_API_KEY` serves Zen and Go alike.

```bash
export OPENCODE_API_KEY="..."
mvge --model opencode/space-bunny-free
mvge --model opencode-go/<model-id>
```

The Zen model catalog is at `https://opencode.ai/inference/v1/models`, in
the standard `/v1/models` shape. Free models carry a `-free` suffix in
their id, for example `space-bunny-free`, `big-pickle`, and
`nemotron-3-ultra-free`.

Go has no free default model, but it does have free models to test with,
and they are not the same as Zen's free models. The official Go page lists
`beam-free` and `iceman-contributor-free` as Free with unlimited monthly
allowance (limited time; Beam is also limited regions). Test with those,
on the Go lane, with a Go key and the session header this Rune now sends:

```bash
mvge --model opencode-go/beam-free
mvge --model opencode-go/iceman-contributor-free
```

Do not test Go with a Zen free slug: Zen free models
(`opencode/beam-free` and the like) are OpenCode-only and answer other
clients with 403 FreeTierError. The same bare id on the Go lane is a
different billing lane. Other Go model ids are still unverified here: pick
a paid Go id from the upstream Go catalog; do not assume a Zen id is also
a Go id, and do not assume a Go model speaks the same wire dialect as a
Zen model of the same name (see Model coverage).

## What it registers

- A realm factory for the `opencode` prefix
- A provider entry naming `https://opencode.ai/inference/openai/v1` as the base URL
- A realm factory for the `opencode-go` prefix
- A provider entry naming `https://opencode.ai/inference/go/v1` as the base URL

Registering the providers is what lets a slug resolve with no catalog entry:
the engine asks the registry whether it knows the `opencode` or
`opencode-go` prefix and reads the base URL from the matching registration.

## Request handling

- Mvge slugs carry a realm prefix (`opencode/space-bunny-free`,
  `opencode-go/<model-id>`) but the gateways publish bare ids, so the prefix
  is stripped on the way out. Both spellings work in configuration.
- Spells are offered as standard `tools`, and a cast in the transcript leaves
  as a `tool_calls` entry paired with its result by id.
- Native multimodal content blocks (`text`, `image_url`, `file`) pass through in
  their documented wire shape; anything unrecognized becomes a text note rather
  than being dropped or forwarded as something the gateway cannot read.
- Contemplation is requested by capping output tokens. This Rune never sends a
  vendor-specific `reasoning` field, because that is an OpenRouter extension and
  not part of the standard. Contemplation deltas are still read, so a model
  that thinks out loud lands in the transcript instead of being dropped.
- Every request, on both products, sends `x-opencode-session`. Go
  hard-rejects a request without it (400 MissingSessionID); Docker sends it
  on Zen requests too. Precedence, highest first: an explicit header in the
  model's headers, then the engine's session id from config meta_data (the
  tome id, set by the harness turn closure and by compaction summaries),
  then a UUID generated once per realm instance. No `x-opencode-client`
  or other guessed header is sent.

## Open limitations

- **Both `/inference/*` bases not live-verified.** The Zen base is
  `https://opencode.ai/inference/openai/v1` and the Go base is
  `https://opencode.ai/inference/go/v1`, from the current OpenCode
  Inference API and Go docs. Both need a live paid-model test before
  they are trusted. That test has not been run here: the sandbox is
  blocked from opencode.ai, and no live call was attempted. In
  particular, whether a Zen paid model is served from
  `/inference/openai/v1` against a Zen prepaid balance (as opposed to a
  Console key) is not settled by the docs cited here and needs that
  live test.
- **Go per-model protocol unverified.** Go models that need the Anthropic
  `messages` wire shape or the OpenAI `responses` shape are not covered
  by this Chat Completions Realm, for the same reason as on Zen (see below).
- **Go catalog ids mostly unverified.** Two Go free ids are named in
  Configure (`beam-free`, `iceman-contributor-free`) from the official Go
  page; they are not yet live-verified through this Rune. Test fixtures
  still use a made-up id and prove the endpoint and header, not a
  catalog entry.

## Model coverage

This Rune speaks the Chat Completions dialect, which covers most of Zen's
catalog. The entries Zen documents against other dialects -- the Anthropic
`messages` shape and the OpenAI `responses` shape -- are not reachable through
it, and this Rune does not claim them. The same limit applies to Go.

A model that names its own base URL overrides the realm default, which is the
seam to use for a gateway endpoint this Rune does not build itself. That
override is an escape hatch for a single model, not the billing selector:
the provider entry already selects Zen or Go.

## Related

- `openrouter-realm` -- the other realm, and the wider model catalog
