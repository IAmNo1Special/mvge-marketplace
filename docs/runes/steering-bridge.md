# Steering Bridge

Official repository steering ([.agents protocol](https://dotagentsprotocol.com)) bridge for Mvge.

Layers your `AGENTS.md` instructions into the system prompt — global steering plus project steering — so the agent follows your repo conventions without you pasting them into every session.

## How it works

- **Layered discovery** — global `~/.agents/AGENTS.md`, then workspace `<project>/AGENTS.md` (falling back to `<project>/.agents/AGENTS.md`). Project steering supplements global steering; empty files are ignored.
- **Prompt injection** — renders `<project_context>` with `<global_instructions>` and `<project_instructions>` (frontmatter stripped) via the `BEFORE_MVGE_START` sigil.
- **Progressive disclosure** — first-level subpackage `AGENTS.md` files are declared as on-demand `Repository Steering:` pointers, never inlined.
- **Zero overhead** — silent bypass when no steering files resolve.

## Commands

- `mvge steering status` — show which steering files resolve.
- `mvge steering show` — display the effective steering content.
- `mvge steering validate` — check steering files for problems.

## Hooks

`session_start`, `before_mvge_start`, `session_shutdown`.

## Dependencies

`typer>=0.12`.
