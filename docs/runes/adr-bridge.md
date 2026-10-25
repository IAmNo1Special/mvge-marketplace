# ADR Bridge

Official Markdown Architectural Decision Records (MADR 3.0) bridge for Mvge.

Keeps your architecture decisions as versioned Markdown files in `docs/adr/`, enforces the MADR 3.0 heading structure, and injects the decision catalog into the model prompt at session start — so the agent always knows *why* things are the way they are.

## Spells

| Spell | Description |
|-------|-------------|
| `adr_list` | List Architectural Decision Records (MADR 3.0) in the project. |
| `adr_get` | Retrieve detailed decision outcome, options, and consequences for an ADR by number. |
| `adr_validate` | Validate Architectural Decision Records against MADR 3.0 schema and index sync. |
| `adr_new` | Scaffold a new MADR 3.0 Architectural Decision Record and update `docs/adr/README.md`. |

## Commands

- `mvge adr new "<title>"` — generates `docs/adr/0001-<slug>.md` from the MADR 3.0 template.
- `mvge adr sync` — maintains the deterministic Markdown index table in `docs/adr/README.md`.

## Hooks

`session_start`, `before_mvge_start`, `context_transform`, `session_shutdown`. The bridge injects `<architectural_decisions>` into the model prompt on startup and stays silent when `docs/adr/` doesn't exist (zero overhead).

## Conformance

Strict MADR 3.0 headings are enforced: `# <Title>`, `## Context and Problem Statement`, `## Decision Outcome`, `## Considered Options`, `## Pros and Cons of the Options`.

## Dependencies

`pyyaml>=6.0`, `typer>=0.12`.
