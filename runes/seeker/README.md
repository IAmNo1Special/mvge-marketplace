# Seeker Protocol Rune

Direct Corpus Interaction (DCI) on-demand capability discovery for Mvge.

## Prerequisites

`rg` (ripgrep) must be on `PATH`. It is a system binary, so it is declared as
`system_deps` in `manifest.json` and `index.json` rather than installed with
the Python dependencies.

`tool_search` shells out to `rg` and has no fallback, so this is required:
without it `tool_search` returns `rg not found on PATH` instead of raising, and
nothing else tells the Summoner what is missing. `skill_search` uses `rg` when
it is there and falls back to a Python scan when it is not.

| Platform | Install |
| --- | --- |
| Debian / Ubuntu | `sudo apt-get install ripgrep` |
| Fedora / RHEL | `sudo dnf install ripgrep` |
| macOS (Homebrew) | `brew install ripgrep` |
| Windows (winget) | `winget install BurntSushi.ripgrep.MSVC` |
| Windows (scoop) | `scoop install ripgrep` |
| Any (Rust toolchain) | `cargo install ripgrep` |

Check with `rg --version`. `mvge setup` reads the declaration and reports the
dependency as missing or present.

## Spells
- `tool_search`: Fast ripgrep discovery over available spells with exact-match fast path
- `skill_search`: Search agent skills without vector databases or embeddings
- `skill_execute`: Execute scripts contained in discovered skill packages
- `mcp_search`: Discover, connect, and mount Model Context Protocol (MCP) servers on demand
