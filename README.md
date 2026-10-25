# 🔮 MvgeOS Marketplace

Official marketplace catalog for MvgeOS **Runes** (extensions), **Mvges** (agents), and runtime **Profiles**.

## ✨ Overview

The MvgeOS Marketplace hosts official extensions (`runes/`) and concrete agent packages (`mvges/`) for MvgeOS, separated from the engine microkernel by a strict contract-tested seam.

## 🧙‍♂️ Available Mvges (Agents)

| Mvge | Version | Description | Path |
| --- | --- | --- | --- |
| `coding_mvge` | `0.2.6` | Official coding agent with built-in development spells | `mvges/coding_mvge` |

Install via Mvge CLI:

```bash
mvge mvge install coding_mvge
```

## 🧩 Available Runes (Extensions)

| Rune | Version | Description | Path |
| --- | --- | --- | --- |
| `adr-bridge` | `0.1.0` | Official Markdown Architectural Decision Records (MADR 3.0) bridge for MvgeOS | `runes/adr-bridge` |
| `approval-rune` | `0.1.0` | Fail-closed execution gate: pauses every uncovered mutating spell cast until the user approves it, with scoped persistent grants, deny rules, and a durable audit trail | `runes/approval-rune` |
| `checkpoint-bridge` | `0.1.0` | Shadow-git workspace checkpoints, atomic leaf restoration, and file rollbacks | `runes/checkpoint-bridge` |
| `codeact-rune` | `0.1.0` | CodeAct Python executor with AST safety, tool write-protection, and auto-dispatch | `runes/codeact-rune` |
| `eval-bridge` | `0.1.0` | Evaluation harness and RL data exporter compatible with google/adk-python evalsets | `runes/eval-bridge` |
| `lsp-bridge` | `0.1.0` | LSP diagnostics bridge providing debounced compiler errors on edit and write | `runes/lsp-bridge` |
| `mcp-bridge` | `0.1.0` | Official Model Context Protocol (MCP) bridge for MvgeOS | `runes/mcp-bridge` |
| `okf-bridge` | `0.2.0` | Official Open Knowledge Format (OKF v0.2) knowledge bridge for MvgeOS | `runes/okf-bridge` |
| `opencode-realm` | `0.1.0` | Official OpenCode Zen provider realm for MvgeOS | `runes/opencode-realm` |
| `openrouter-realm` | `0.1.0` | Official OpenRouter provider realm for MvgeOS | `runes/openrouter-realm` |
| `opentelemetry-bridge` | `0.1.0` | Official OpenTelemetry GenAI tracing bridge for MvgeOS | `runes/opentelemetry-bridge` |
| `pi-codec` | `0.1.0` | Native Pi session codec: resume, append, fork, and validate Pi agent session logs (JSONL v3/v4) directly in MvgeOS with no conversion; pi-export copies a Tome v1 session to a Pi-native v4 file | `runes/pi-codec` |
| `seeker` | `0.1.0` | Seeker Active Discovery+ Spell Gateway: unified `search`, `activate`, `execute`, and `unload` lifecycle across Spells, Skills, and MCP servers | `runes/seeker` |
| `selfmod-bridge` | `0.1.0` | Official self-modification & customization bridge for MvgeOS | `runes/selfmod-bridge` |
| `session-search` | `0.1.0` | Full-text BM25 search over MvgeOS sessions: Tome v1, Pi sessions (via pi-codec), and Antigravity transcripts, indexed into local SQLite FTS5 | `runes/session-search` |
| `session-title` | `0.1.0` | Auto-generates session titles from conversation content with instant fallback and optional LLM upgrade | `runes/session-title` |
| `skill-evolution` | `0.3.0` | Persistent experience consolidation and autonomous skill evolution: harvests raw experience, consolidates durable patterns, and proposes or applies agent-scoped skills | `runes/skill-evolution` |
| `skills-bridge` | `0.1.0` | Official Agent Skills (agentskills.io) and Agent Plugins bridge for MvgeOS | `runes/skills-bridge` |
| `steering-bridge` | `0.1.0` | Official repository steering (AGENTS.md, .agents protocol) bridge for MvgeOS | `runes/steering-bridge` |
| `submvge-bridge` | `0.1.0` | Kernel sub-Mvge summoning, persistent sub-sessions, and reactive wakeup bridge | `runes/submvge-bridge` |

Install via Mvge CLI:

```bash
mvge rune install openrouter-realm --confirm-python-deps
```

`openrouter-realm` is the Realm a first run needs: the engine's default model is
`openrouter/free`, and the prefix in that slug is the Realm that serves it. It
requires an `OPENROUTER_API_KEY` — see
[Use another Realm](docs/quickstart.md#use-another-realm).

## Structure

```text
mvge-marketplace/
|-- index.json          # single source of truth: what we publish, and what kind
|-- marketplace_index.py # reads index.json; the only place the index is parsed
|-- conftest.py         # pins first-party imports to this checkout (index-driven)
|-- tests/              # index <-> filesystem <-> pytest config consistency
|-- mvges/              # agents: installed to ~/.agents/agents/<name>
|   `-- coding_mvge/
|       |-- manifest.json
|       |-- agent.md
|       |-- mvge.py
|       |-- spells/
|       |-- system_prompt/
|       `-- runes/
`-- runes/              # extensions: installed to ~/.agents/extensions/<name>
    |-- adr-bridge/
    |-- approval-rune/
    |-- checkpoint-bridge/
    |-- codeact-rune/
    |-- eval-bridge/
    |-- lsp-bridge/
    |-- mcp-bridge/
    |-- okf-bridge/
    |-- opencode-realm/
    |-- openrouter-realm/
    |-- opentelemetry-bridge/
    |-- pi-codec/
    |-- seeker/
    |-- selfmod-bridge/
    |-- session-search/
    |-- session-title/
    |-- skill-evolution/
    |-- skills-bridge/
    |-- steering-bridge/
    `-- submvge-bridge/
```

### Runes and mvges are different things

`index.json` has two sections, and the distinction is real rather than
cosmetic:

| | rune | mvge |
|---|---|---|
| installed to | `~/.agents/extensions/<name>` | `~/.agents/agents/<name>` |
| manifest marker | `types` | `spells` |
| loaded by | `RuneRunner` — a `rune_factory(api)` plus sigil `hooks` | resolved *by name*; spells found by directory convention |
| CLI | `mvge rune install` | `mvge mvge install` |

An mvge never passes through the rune lifecycle and registers no sigils, so it
is not "a rune of the mvge type" — it is an agent package. The sections keep
them apart, and the consistency tests fail if an artifact carries the other
kind's marker key.

`index.json` is the only place that fact is recorded. `conftest.py` derives its
`sys.path` roots and pinned module names from it, so publishing a new artifact
cannot leave the test harness silently behind. `pyproject.toml` has to spell
out `testpaths` and `pythonpath` literally — TOML cannot call into Python — so
`tests/test_index_consistency.py` asserts those lists still cover every indexed
artifact. CI runs bare `pytest` and so uses `testpaths`, making a local run and
a CI run the same set of tests.

Two layout shapes exist and both are supported: `runes/skills-bridge` keeps its
manifest beside the `mvgeos_runes_skills_bridge` package, while
`mvges/coding_mvge` *is* the package with the manifest inside it.

---

## License

MIT — same terms as the [Mvge engine](https://github.com/IAmNo1Special/mvge/blob/main/LICENSE).
See [LICENSE](LICENSE).
