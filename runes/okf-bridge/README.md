# okf-bridge

Official Open Knowledge Format (OKF v0.2) knowledge bridge for Mvge.

## Features
- Owns the `.agents/knowledge/` OKF v0.2 concept bundle: merges the global (`$MVGE_GLOBAL_DIR/knowledge`, default `~/.agents/knowledge`) and workspace (`<cwd>/.agents/knowledge`) layers, workspace winning on collisions.
- Conforms strictly to the OKF v0.2 normative specification (§3 bundle structure, §11 conformance, §5 trust/provenance/lifecycle, §10 attestation, §13 changes from v0.1).
- Budgeted prompt injection (progressive disclosure, skills-bridge pattern): injects only `context: auto` concepts as id/title/description metadata in a `<working_concepts>` block (default 2000-token cap, newest `generated.at` first) — never full bodies; the model calls `concept_get` with the concept id to delve into a body when a title or description signals relevance. Descriptions render when present but are never required. The block is replaced in place each turn via `BEFORE_MVGE_START` and `CONTEXT_TRANSFORM`. `search-only` concepts stay retrievable via search.
- Concept lifecycle spells: `concept_write`, `concept_verify`, `concept_deprecate`, `concept_set_context` (atomic writes, rotating backups, `log.md` entries; machine-written concepts stay unverified until human-verified).
- Read spells: `concept_search`, `concept_get`, `concept_validate`.
- CLI: `mvge okf` subcommands with cp1252-safe ASCII output.
