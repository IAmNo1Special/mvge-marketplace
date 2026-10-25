# adr-bridge

Official Markdown Architectural Decision Records (MADR 3.0) bridge for Mvge.

## Features
- **MADR 3.0 Conformance**: Enforces strict headings (`# <Title>`, `## Context and Problem Statement`, `## Decision Outcome`, `## Considered Options`, `## Pros and Cons of the Options`).
- **Sequential ADR Scaffolding**: `mvge adr new "<title>"` generates `docs/adr/0001-...md` with MADR 3.0 template.
- **Index Synchronization**: `mvge adr sync` maintains deterministic Markdown table in `docs/adr/README.md`.
- **In-Memory Decision Catalog**: Injects `<architectural_decisions>` into the model prompt on startup.
- **Zero Overhead**: Silent bypass when `docs/adr/` does not exist.
