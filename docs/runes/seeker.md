# Seeker

Seeker Protocol — DCI-based (Direct Corpus Interaction) on-demand capability discovery for MvgeOS.

Instead of stuffing every tool description into the prompt, Seeker keeps the model's context lean and lets it *search* for capabilities when it needs them: ripgrep over the spell corpus, skill packages without vector databases or embeddings, and MCP servers mounted on demand.

## Spells

### Unified Lifecycle Spells
| Spell | Description |
|---|---|
| `search` | Unified discovery across available Spells, Skills, and MCP servers. |
| `activate` | Dynamically activates discovered Spells, Skills, or MCP servers into the active allowlist. |
| `execute` | Directly executes scripts contained in discovered skill packages. |
| `unload` | Evicts previously activated Spells, Skills, or MCP servers from the model allowlist. |

### Targeted Discovery Spells
| Spell | Description |
|---|---|
| `tool_search` | Fast ripgrep discovery over available spells, with an exact-match fast path. |
| `skill_search` | Search agent skills without vector databases or embeddings. |
| `skill_execute` | Execute scripts contained in discovered skill packages. |
| `mcp_search` | Discover, connect, and mount Model Context Protocol (MCP) servers on demand. |

## Spell gateway

Seeker declares `"spell_gateway": true` in its manifest, which makes it the engine's tool gateway: the model's spell view is narrowed to Seeker's registered spells, so discovery always goes through the protocol instead of around it. (Explicitly configured allowlists are never overridden; if several runes claim the gateway, the first one wins.)

## Hooks

`session_start`, `session_shutdown`, `turn_end`, `after_spell_result`.

## Prerequisites

`rg` (ripgrep) on `PATH`. It is a system binary, so it is declared as `system_deps` in `manifest.json` and `index.json` rather than installed alongside the Python dependencies, and `mvgeos setup` reports it as present or missing.

`tool_search` shells out to `rg` and has no fallback: without it the Spell returns `rg not found on PATH` instead of raising, so nothing else names the cause. `skill_search` prefers `rg` and falls back to a Python scan when it is absent.

| Platform | Install |
|---|---|
| Debian / Ubuntu | `sudo apt-get install ripgrep` |
| Fedora / RHEL | `sudo dnf install ripgrep` |
| macOS (Homebrew) | `brew install ripgrep` |
| Windows (winget) | `winget install BurntSushi.ripgrep.MSVC` |
| Windows (scoop) | `scoop install ripgrep` |

Check with `rg --version`.

## Dependencies

`aiohttp`, and the `ripgrep` (`rg`) binary above.
