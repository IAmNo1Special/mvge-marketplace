# steering-bridge Rune

Official **repository steering ([.agents protocol](https://dotagentsprotocol.com))** bridge for Mvge.

## Features
- **Layered discovery**: global `~/.agents/AGENTS.md`, workspace `<project>/AGENTS.md` (fallback `<project>/.agents/AGENTS.md`).
- **Precedence**: project steering supplements global steering; empty files are ignored.
- **Prompt injection**: renders `<project_context>` with `<global_instructions>` and `<project_instructions>` (frontmatter stripped) via the `BEFORE_MVGE_START` sigil.
- **Progressive disclosure**: first-level subpackage `AGENTS.md` files are declared as on-demand `Repository Steering:` pointers, never inlined.
- **Zero overhead**: silent bypass when no steering files resolve.
- **CLI commands**: `mvge steering status`, `mvge steering show`, `mvge steering validate`.
