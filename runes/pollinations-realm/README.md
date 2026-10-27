# pollinations-realm

Pollinations provider realm for Mvge, as a bootstrap-only fallback.

## Overview

Pollinations' legacy text endpoint is OpenAI Chat Completions compatible.
This Rune points Mvge at it, so a first-time user can complete a turn with
no API key at all, then set up a real provider for heavy use. That is the
whole job of this Rune: get bootstrap moving, not carry production work.

## Install

```bash
mvge rune install pollinations-realm
```

## Configure

No key is needed for the anonymous tier:

```bash
mvge --model pollinations/openai
```

A key, when one is supplied later, is forwarded as a bearer token. An
empty key is never sent as a bare `Bearer ` header (httpx refuses that
value before the request leaves the process).

## Model coverage

Pollinations' legacy catalog, measured live on 2026-10-27, carries exactly
one anonymous-tier model:

- **openai-fast** — GPT-OSS 20B (OVH reasoning), tools yes, vision no
- Aliases: `openai`, `gpt-oss`, `gpt-oss-20b`, `ovh-reasoning`

The shipped default slug is `pollinations/openai`: the `openai` wire alias
is the one proven live (HTTP 200, `user_tier: "anonymous"`, reply
"hello"). The catalog name `openai-fast` is the same entry in the model
list; the Realm strips only the `pollinations/` prefix and passes any
other bare id through unchanged, so a provider-side rejection stays
visible instead of being masked by a guessed mapping.

## Rate limits

The published anonymous figure is about **1 request per 15 seconds
(4 per minute)**, with no published token-per-day cap. Treat that as the
documented figure, not a measured guarantee: a rapid-burst probe on
2026-10-27 was inconclusive (connection drops, no clean 429), and
Pollinations publishes no current anonymous-tier limit.

## Warning: this lane can close

Pollinations' current guide is reported as documenting no anonymous tier,
with generation on the newer `gen.pollinations.ai` endpoints requiring a
key. The legacy `text.pollinations.ai` lane still answers anonymously
today, but it is exactly the kind of lane that can close overnight, as
OpenCode Zen's anonymous free tier did on 2026-10-11 (ADR-0020). Do not
build a production workflow on this Realm alone.

## What it registers

- A realm factory for the `pollinations` prefix
- A provider entry naming `https://text.pollinations.ai/openai` as the
  base URL

Registering the provider is what lets a slug resolve with no catalog
entry: the engine asks the registry whether it knows the `pollinations`
prefix and reads the base URL from the registration.

## Request handling

- Mvge slugs carry a realm prefix (`pollinations/openai`) but Pollinations
  publishes bare ids, so the prefix is stripped on the way out.
- Spells are offered as standard `tools`, and a cast in the transcript
  leaves as a `tool_calls` entry paired with its result by id.
- No vendor-specific `reasoning` field is sent (an OpenRouter extension,
  not part of standard Chat Completions). Reasoning deltas are still read,
  so a model that thinks out loud lands in the transcript as contemplation
  instead of being dropped.
- A model that names its own base URL overrides the realm default, which
  is the seam to use for a gateway endpoint this Rune does not build
  itself.

## Bootstrap fallback (engine follow-up, not in this Rune)

This Rune alone does not change first-run behavior. The full Option 2
flow needs an engine change, documented as a follow-up because the
realm-first default-model work owns default resolution (branch
`fix/realm-first-default-model` in the engine repo):

1. First run with no key and no realm preference completes its first
   turn through `pollinations/openai`.
2. Before heavy use, bootstrap prompts explicitly for a free OpenRouter
   key — signup by email, GitHub, or Google, no card; 50 free-model
   requests per day, rising to 1,000 per day after $10 in credits has
   ever been purchased. This prompt must be an explicit step, never a
   silent fallback to OpenRouter.
3. The engine must exempt Pollinations' free model from the credential
   gate: add `pollinations` to `REALMS_WITH_ANONYMOUS_FREE_TIER` and
   `pollinations/openai` to the shipped `REALM_DEFAULT_MODELS` table in
   `src/mvge/provider/realms.py`. The realm-first branch's gate reads
   only the shipped table, never a Rune registration, so a Rune-side
   registration alone cannot exempt the gate.

The global default model is deliberately not changed to Pollinations by
this Rune; that decision belongs to the realm-first work.

## Related

- `openrouter-realm` — the keyed free-tier realm used after bootstrap
- `opencode-realm` — the keyed Zen realm; its keyless-header handling is
  the pattern this Rune mirrors
