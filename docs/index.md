# Mvge

**An open coding-agent platform. Read it, fork it, self-host it, and run it on
a model you already pay for.**

Mvge is an operating system for AI agents, written in Python. A Mvge — the
agent — casts Spells against models served by Realms. Conversations persist as
Tomes. Capabilities extend through Runes.

There is no account to create with Mvge and no telemetry in the engine.
The default model (`openrouter/free`) is free of per-token charges, and requires
an `OPENROUTER_API_KEY` because as of 2026-10-11 no free tier answers an
unauthenticated request (ADR-0020). To use another provider, bring the key for
the Realm you want.

```bash
uvx mvge --help
```

```text
 Mvge - a Python-based AI coding agent

╭─ Commands ────────────────────────────────────────────────────────────────────╮
│ build   Serialise the resolved runtime manifest (read-only).                 │
│ config  Configuration management                                             │
│ info    Display the runtime snapshot as rich tables (read-only).             │
│ mvge    Mvge (agent) management                                              │
│ rune    Extension rune management                                            │
│ setup   Install system dependencies for runes                                │
│ tome    Session tome management                                              │
╰──────────────────────────────────────────────────────────────────────────────╯
```

Four commands and under a minute gets you from nothing to a running Mvge:

```bash
# 0. the key — required for the free router since ADR-0020
export OPENROUTER_API_KEY="sk-or-..."

# 1. the Realm — how Mvge reaches a model
uvx mvge rune install openrouter-realm --confirm-python-deps

# 2. the Mvge — carries the Spells
uvx mvge mvge install coding_mvge

# 3. run a task
uvx mvge --agent-name coding_mvge "summarise the README in this directory"
```

The [Quickstart](quickstart.md) walks through it with real output, and
[Use another Realm](quickstart.md#use-another-realm) covers the case where you
already pay for a provider.

## What it does

A Mvge reads your directory, decides what it needs, casts the Spell, and shows
you what it did. `mvge info` prints the resolved runtime so you can see the
Spells and Runes it assembled before you spend anything:

```text
Agent: coding_mvge
Model: openrouter/free

                    Spells
┏━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Name       ┃ Source  ┃ Source Rune ┃ Description                               ┃
┡━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ bash       │ builtin │ -           │ Execute a shell command with working dir…  │
│ read       │ builtin │ -           │ Read a file from the workspace…           │
│ write      │ builtin │ -           │ Create or overwrite a file…               │
│ edit       │ builtin │ -           │ Modify an existing file…                  │
│ find       │ builtin │ -           │ Locate files by name…                     │
│ list       │ builtin │ -           │ List directory contents…                  │
│ grep       │ builtin │ -           │ Search file contents by pattern…          │
└────────────┴─────────┴─────────────┴───────────────────────────────────────────┘
```

The `Model:` line is the model you get with no configuration, and its prefix is
the Realm — `openrouter`, which is why step 1 above installs `openrouter-realm`.
Swap the Realm and the model comes with it.

- **Coding Mvge.** Nine built-in Spells — `bash`, `edit`, `find`, `grep`,
  `list_files`, `read`, `read_url`, `search_web`, `write` — installed as a
  package, so you can read exactly what your agent can do. Run
  `mvge info --agent-name coding_mvge` to see the list yourself.
- **Tomes.** Every conversation is an append-only JSONL file you can read, diff,
  fork, and export. Not a chat log locked in a database.
- **Runes.** Mount MCP servers as Spells, trace with OpenTelemetry, wire in
  Agent Skills, read your `AGENTS.md`, self-modify. Twenty in the
  [catalog](#rune-catalog).
- **Realm-agnostic.** The engine programs against a Realm abstraction, not one
  vendor's SDK. Your key goes to the provider you chose and nowhere else.
- **Desktop GUI.** NiceGUI + PyWebView, with live Spell tracking and diff review.

## Start here

| | |
| --- | --- |
| [Quickstart](quickstart.md) | Nothing to a running Mvge — and what five minutes does not promise |
| [Concepts](concepts.md) | What Mvge, Spell, Realm, Tome, Rune, and Sigil mean |
| [Troubleshooting](troubleshooting.md) | Real errors, with the real output |
| [FAQ](faq.md) | The questions that come up before anything breaks |
| [Rune catalog](#rune-catalog) | What is available today |

## Honest status

- **Pre-1.0.** The current version is on the
  [Quickstart](quickstart.md#checking-your-version). The internals are
  held to a high bar — mypy strict, ruff, over 3,700 tests. The command
  surface still moves.
- **The default model call can be refused.** Every command on this site was
  verified on a clean machine, including the task on the
  [Quickstart](quickstart.md). It is not Mvge's to fix, though: when the
  default free tier is at capacity the run ends in

  ```text
  Rate limited by the provider: Realm requested 2209s retry delay (max: 60s). Rate
  limit exceeded. Please try again later..
  ```

  On the check that produced the Quickstart's transcript, this exact command was
  refused seven times and then completed, about 40 minutes after the first
  refusal. Read the number it asks for — about 37 minutes — rather than
  retrying into it. Details in
  [Troubleshooting](troubleshooting.md#rate-limited-by-the-provider).
- **Runes run in-process** via `importlib`. Not sandboxed, not
  process-isolated. Read manifests before installing code you did not write;
  [approval-rune](runes/approval-rune.md) is a fail-closed gate you can put in
  front of mutating casts.
- **No run-level spend cap.** *Mana Budget* is not implemented. Contemplation is
  a per-request reasoning parameter, not a ceiling.

## Rune catalog

A Rune is a packaged extension that hooks lifecycle Sigils and can register
Spells, CLI commands, shortcuts, and Realms.

| Rune | What it does |
| --- | --- |
| [ADR Bridge](runes/adr-bridge.md) | MADR 3.0 architectural decision records: scaffold, validate, and inject decisions into the prompt |
| [Approval Rune](runes/approval-rune.md) | Fail-closed execution gate — pauses every uncovered mutating Spell cast for a human |
| [Checkpoint Bridge](runes/checkpoint-bridge.md) | Shadow-git workspace checkpoints, atomic leaf restoration, and file rollbacks |
| [CodeAct Rune](runes/codeact-rune.md) | CodeAct Python executor with AST safety, tool write-protection, and auto-dispatch |
| [Eval Bridge](runes/eval-bridge.md) | Evaluation harness and RL data exporter compatible with google/adk-python evalsets |
| [LSP Bridge](runes/lsp-bridge.md) | LSP diagnostics bridge providing debounced compiler errors on edit and write |
| [MCP Bridge](runes/mcp-bridge.md) | Model Context Protocol client — mounts MCP servers as Spells |
| [OKF Bridge](runes/okf-bridge.md) | Open Knowledge Format (v0.2) knowledge bundles + ADR parsing, injected into context |
| [OpenCode Realm](runes/opencode-realm.md) | Provider Realm for the OpenCode Zen gateway, including its free models |
| [OpenRouter Realm](runes/openrouter-realm.md) | Provider Realm for the OpenRouter API gateway |
| [OpenTelemetry Bridge](runes/opentelemetry-bridge.md) | GenAI tracing across sessions, turns, provider calls, and Spell casts |
| [Pi Codec](runes/pi-codec.md) | Native Pi session codec — resume, append, fork, and validate Pi agent sessions |
| [Seeker](runes/seeker.md) | On-demand discovery of Spells, Skills, and MCP servers |
| [Self-Mod Bridge](runes/selfmod-bridge.md) | Self-modification and customization of the running Mvge |
| [Session Search](runes/session-search.md) | Full-text BM25 search across Mvge Tomes and Pi sessions |
| [Session Title](runes/session-title.md) | Auto-generates Tome titles from conversation content |
| [Skill Evolution](runes/skill-evolution.md) | Persistent experience consolidation and autonomous Skill evolution |
| [Skills Bridge](runes/skills-bridge.md) | Agent Skills / Agent Plugins discovery with progressive disclosure |
| [Steering Bridge](runes/steering-bridge.md) | Repository steering via `AGENTS.md`, layered into the system prompt |
| [Sub-Mvge Bridge](runes/submvge-bridge.md) | Kernel sub-Mvge summoning, persistent sub-sessions, and reactive wakeup bridge |

Install any of them with `mvge rune install <name>`.

## How a Rune is put together

```text
runes/<name>/
├── manifest.json      # name, version, entry point, Sigils, Spells, commands, deps
├── rune.py            # entry point exposing rune_factory
├── pyproject.toml     # packaging and third-party deps
├── README.md
└── tests/             # runs in marketplace CI
```

Runes are discovered from standard extension directories — the user's, the
project's, or a specific Mvge's — and load through the same path that resolves
their CLI commands, so a command that appears in `mvge --help` is a Rune that
would load.

## Building these docs

```bash
uv tool install mkdocs --with mkdocs-material --with pymdown-extensions
mkdocs serve     # preview at http://127.0.0.1:8000
mkdocs build     # static output in site/
```

The site deploys to GitHub Pages from `main` via `.github/workflows/docs.yml`.

## The repository

- Engine and CLI — [github.com/IAmNo1Special/mvge](https://github.com/IAmNo1Special/mvge)
- This marketplace — [github.com/IAmNo1Special/mvge-marketplace](https://github.com/IAmNo1Special/mvge-marketplace)
- Licence — MIT