# OKF Bridge

Official Open Knowledge Format (OKF v0.2) knowledge bridge for Mvge.

Owns the `.agents/knowledge/` concept bundle: merges the global (`$MVGE_GLOBAL_DIR/knowledge`, default `~/.agents/knowledge`) and workspace (`<cwd>/.agents/knowledge`) layers, workspace winning on collisions. Injects a budgeted `<working_concepts>` block each turn — id/title/description metadata only, never full bodies (progressive disclosure, skills-bridge pattern).

## Spells

| Spell | Description |
|-------|-------------|
| `concept_search` | Search the OKF knowledge base (global and workspace layers) by query, type, or tags. |
| `concept_get` | Retrieve a concept's full body, trust metadata, sources, and links by id. |
| `concept_validate` | Validate the merged OKF bundle against the v0.2 specification (§11). |
| `concept_write` | Record durable knowledge as a concept (atomic write, backup, `log.md` entry). |
| `concept_verify` | Record verification of a concept, moving it up the trust ladder. |
| `concept_deprecate` | Mark a concept deprecated (file preserved, no longer injected). |
| `concept_set_context` | Flip a concept between `auto` and `search-only`. |

## Commands

- `mvge okf …` — search, inspect, validate, and manage knowledge bundles (cp1252-safe ASCII output).

## Hooks

`session_start`, `before_mvge_start`, `context_transform`, `session_shutdown`.

## Conformance

Strict OKF v0.2: §3 (bundle structure), §11 (conformance), §5 (trust / provenance / lifecycle), §10 (attestation), §13 (changes from v0.1).

## Dependencies

`pyyaml>=6.0`, `typer>=0.12`.
