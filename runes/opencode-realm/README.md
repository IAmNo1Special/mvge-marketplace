# opencode-realm

Official OpenCode Zen and Go provider realms for Mvge.

## Overview

This Rune provides two provider entries, not one. Zen and Go are separate
billing products behind the same credential, so the provider name in the
model slug is the billing lane:

- Zen: `opencode/<model-id>`, at `https://opencode.ai/zen/v1`
- Go: `opencode-go/<model-id>`, at `https://opencode.ai/zen/go/v1`

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

The Zen model catalog is at `https://opencode.ai/zen/v1/models`, in the
standard `/v1/models` shape. Free models carry a `-free` suffix in their id,
for example `space-bunny-free`, `big-pickle`, and `nemotron-3-ultra-free`.

No Go model ids are listed here: the Go catalog was not verified as part of
this change, and Go is a paid subscription with no free default. Pick a Go
model id from the upstream Go catalog; do not assume a Zen id is also a Go
id, and do not assume a Go model speaks the same wire dialect as a Zen
model of the same name (see Model coverage).

## What it registers

- A realm factory for the `opencode` prefix
- A provider entry naming `https://opencode.ai/zen/v1` as the base URL
- A realm factory for the `opencode-go` prefix
- A provider entry naming `https://opencode.ai/zen/go/v1` as the base URL

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

## Open limitations

- **Session header deferred.** This Rune sends no `x-opencode-session`
  header. Its request path receives no session id, so there is no stable
  per-conversation value to send yet; plumbing one in is engine work. A
  per-request generated value was deliberately not guessed, because a
  header that changes mid-conversation is worse than an absent one for
  per-conversation routing. If Go rejects a call for a missing session
  header, that is this limitation, not a billing or key problem.
- **Go base URL not live-verified.** The Go base is
  `https://opencode.ai/zen/go/v1` from the Docker provider docs, the
  counterpart of the Zen base. Other current sources (Pi) have moved to
  `/inference/*` endpoints, so the Go base needs a live paid-model test
  before it is trusted. That test has not been run here: the sandbox is
  blocked from opencode.ai, and no live call was attempted.
- **Go per-model protocol unverified.** Go models that need the Anthropic
  `messages` wire shape or the OpenAI `responses` shape are not covered
  by this Chat Completions Realm, for the same reason as on Zen (see below).

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
