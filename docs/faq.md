# FAQ

Questions a new user asks, answered without hedging. Where the honest answer is
"not yet", it says that.

## Getting started

### Do I need an API key to run my first task?

Yes. The default model (`openrouter/free`) is served via OpenRouter, which requires an API key even for free models. Getting an API key is free: sign up at [openrouter.ai](https://openrouter.ai), generate a key in settings, and set `OPENROUTER_API_KEY`:

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
```

(Or pass `--api-key`). No credential file is written to disk when using the environment variable. If you use a different provider (OpenCode Zen, etc.), you provide that provider's key instead — see
[Use another Realm](quickstart.md#use-another-realm).

### `uvx mvgeos` does not work. Why is the install line so long?

It is short now, and that is recent. `uvx mvgeos --help` resolves against PyPI.
The long `git+` form you may find in older answers names the CLI's subdirectory
in the monorepo and is what the project used before the first published release:

```bash
uvx --from "git+https://github.com/IAmNo1Special/mvgeos#subdirectory=mvgeos-cli" mvgeos --help
uv tool install "git+https://github.com/IAmNo1Special/mvgeos#subdirectory=mvgeos-cli"
```

Use the short form unless you are contributing; see
[Troubleshooting](troubleshooting.md#mvgeos-was-not-found-in-the-package-registry).

### Do I need Python installed?

No. `uv` fetches Python 3.13 for you. You need `uv`, and nothing else.

### Do I need a clone of the repository?

No — not to use it. The install commands pull what they need. Cloning is for
contributing, and `uv sync && uv run mvgeos` is the contributor path. It also
pulls the desktop GUI's dependencies, so it is heavier.

### Why do I need to install two things — a Realm and a Mvge?

They are different layers. The **engine** knows how to run a turn loop. A
**Mvge** supplies the personality, configuration, and Spells. A **Realm**
supplies model access. The engine ships neither, so you compose the pair you
want — a different Realm or a different Mvge without touching the engine.

## Models and cost

### Do I need an OpenRouter account?

Yes, for the default model (`openrouter/free`). Creating an account is free and grants access to OpenRouter's free-tier models (up to 50 requests per day per account).

If you already pay for another provider directly (or prefer OpenCode Zen), install that provider's Realm and provide their credentials instead — that is the point of the abstraction. See
[Use another Realm](quickstart.md#use-another-realm).

### Which Realm do I need?

The one whose prefix matches your model's slug. The default model's prefix is
`openrouter` (`openrouter/free`). Install `openrouter-realm` (or run `mvgeos setup --profile coding` which installs it along with `coding_mvge`). If you install `opencode-realm` instead and run with the default model, you get a working engine that cannot reach its own default:

```text
Error: No Realm factory registered for model 'openrouter/free'. Run 'mvgeos rune install openrouter-realm' to install it from the central marketplace.
```

### Which model should I use?

The one you get by default — `openrouter/free` — unless you have a reason. Check which one you actually got:

```bash
mvgeos info --agent-name coding_mvge
```

The `Model:` line is the answer. Override it with `-m`, and remember the prefix
in the slug names the Realm that has to serve it:

```bash
mvgeos --agent-name coding_mvge -m "openrouter/free" "your task"
```

MvgeOS never proxies your key anywhere except the Realm you installed.

### It says "free" — why did my run fail?

Because free is shared, and shared means at capacity or subject to daily quotas.

On the default OpenRouter Realm, the limit is a daily allowance rather than a busy endpoint: OpenRouter allows **50 free-model requests per day per account**, shared across every model and every app on that account, resetting at **00:00 UTC**. A single Mvge turn spends several invocations, because verifying a result means additional model calls. When the allowance is gone, every free model returns `429 free-models-per-day`. Recent builds report it as `Daily free-model quota exhausted` — a spent allowance, not a transient fault — and add the count and reset time when the provider sent them.

If you are using the OpenCode Zen Realm instead, its free tier may return:

```text
Rate limited by the provider: Realm requested 2209s retry delay (max: 60s). Rate
limit exceeded. Please try again later..
```

**Read the number before you retry.** `2209s` is roughly 37 minutes, and a tight
retry loop will not shorten it. The refusal is not your install and not your
configuration. On the clean-machine check behind the Quickstart, the same command
was refused seven times and then completed, about 40 minutes after the first
refusal — the delay counts down and does end. The engine waits at most 60 seconds
itself, which is why it gives up rather than sitting there.

If you are on the OpenRouter Realm instead, the limit there is a daily allowance
rather than a busy endpoint: OpenRouter allows **50 free-model requests per day
per account**, shared across every model and every app on that account, resetting
at **00:00 UTC**. A single Mvge turn spends several, because verifying a result
means a second model call. When the allowance is gone, every free model returns
`429 free-models-per-day`. Recent builds report it as `Daily free-model quota
exhausted` — a spent allowance, not a transient fault — and add the count and
reset time when the provider sent them. Older builds say `Upstream provider
overloaded: Provider returned error` for the same condition, which reads like a
temporary blip and is not. The headers do not lie either way:

```text
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1791244800000     # Unix milliseconds; 00:00 UTC
```

`X-RateLimit-Remaining: 0` is the tell. If you see it, no retry will help.

Three ways out, in order of how little they cost you: wait for 00:00 UTC, pass a
different free model with `-m` hoping a different endpoint has headroom, or pass
a paid one. See
[Troubleshooting](troubleshooting.md#upstream-provider-overloaded-provider-returned-error)
for how to tell a spent allowance from a busy provider.

This is the single most common reason the quickstart appears broken on day one.
It is a limit at the provider, not a fault in MvgeOS.

### What does a run cost?

Whatever the model you selected costs, billed by that provider. The engine does
not meter or cap spend: **Mana Budget**, a run-level cap, is not implemented.
Contemplation is a per-request reasoning parameter, not a budget. If you need a
ceiling today, run under a provider account with its own limits.

### Does it phone home?

Not beyond the Realm you install, and the marketplace index it fetches Runes
from. There is no telemetry in the engine. Runes you install are your code
running in-process — see below.

## Extending it

### How do I write a Rune?

A directory with a `manifest.json` and an entry point exposing `rune_factory`.
The manifest declares your Sigils, Spells, and commands. The
[Seeker](runes/seeker.md) and the existing bridges are the readable examples —
[MCP Bridge](runes/mcp-bridge.md) is probably the closest to what you want if
you are mounting an external capability.

### Are Runes safe to install?

Understand what you are running: Rune code executes **in-process** inside the
engine via `importlib`. It is not sandboxed and not process-isolated. A Rune
has your permissions, and a manifest's `python_deps` is an instruction to fetch
packages from PyPI. So: read the manifest, and read the code, before installing
something you did not write.

Two things exist to put a human back in the loop, neither on by default:
[approval-rune](runes/approval-rune.md) is a fail-closed gate on mutating
casts, and the engine exposes an opt-in sandbox seam for code a Rune
voluntarily submits.

### Can I use it with an MCP server?

Yes — [MCP Bridge](runes/mcp-bridge.md) mounts MCP servers as Spells, and
`~/.agents/mcp.json` is the standard registry.

### Does it read my `AGENTS.md`?

Yes. [Steering Bridge](runes/steering-bridge.md) layers repository steering into
the system prompt using the open `.agents` protocol. It discovers `AGENTS.md`
and will not fall back to vendor-specific filenames.

## How it compares

### How is this different from Claude Code or another closed coding agent?

The engine is yours. You can read every line, fork it, self-host it, and change
it. Nothing phones home, there is no account to create, and your key goes
straight to the Realm you chose. Model choice is genuinely open — the engine
programs against the Realm abstraction, not one vendor's SDK.

What you give up is the polish of a product with a team behind it on a single
code path. MvgeOS is pre-1.0 and the command surface moves.

### Why does it use invented vocabulary — Mvge, Spell, Tome?

Because the generic words are ambiguous when you have an engine, a Mvge, a
Spell, a Rune, and a provider all in one system, and "agent" would mean three
different things in three files. The vocabulary is applied consistently so the
code reads unambiguously.

It stops at the boundary. Directory names, wire fields, and CLI arguments follow
the open `.agents` protocol, where the standard terms are "session",
"extension", and "tools". So MvgeOS files stay readable by anything else that
speaks the protocol, and `CONTEXT.md` is the authority on which word applies
where.

### Is this a Pi fork?

No, and the relationship is worth being precise about. MvgeOS is an independent
implementation of the architecture Pi introduced, and it tracks Pi closely in
places — the loop, `StreamFn`, the frozen loop context, the queue-drain
semantics. Tomes are Pi-inspired in format but explicitly **not**
byte-compatible: Pi session files cannot be opened by MvgeOS and vice versa.
[Provenance](https://github.com/IAmNo1Special/mvgeos/blob/main/docs/architecture/PROVENANCE.md)
traces the sources, including the ADK, Eve, and the papers behind the skill
evolution Rune.

### How mature is this?

Pre-1.0. Over 3,700 tests, mypy strict, ruff. The current version is on
the [Quickstart](quickstart.md#checking-your-version) — it moves faster
than this page does, which is why it is named in one place and not here. The
internals are held to a high bar and the surface is still moving. Expect the command surface
to change and expect the docs to lag a release or two behind the code — the
[Troubleshooting](troubleshooting.md) page is where landed fixes show up first.

### What licence?

MIT.

## Reporting problems

Open an issue at
[github.com/IAmNo1Special/mvgeos](https://github.com/IAmNo1Special/mvgeos/issues).
Include the output of:

```bash
mvgeos info --agent-name coding_mvge
mvgeos setup check
uv --version && python3 --version
```

That trio pins down almost anything. Security reports should follow
[SECURITY.md](https://github.com/IAmNo1Special/mvgeos/blob/main/SECURITY.md).