# LSP Bridge

Language Server Protocol (LSP) diagnostics bridge providing debounced compiler
and linter feedback on `edit` and `write` spell casts.

## Features

- **Automatic Diagnostics**: Subscribes to the `after_spell_result` sigil hook.
  When an agent edits or writes code files, language servers run in the background
  and compiler diagnostics are attached to the invocation result.
- **Multi-Language Support**: Automatically spawns and queries language servers
  for Python (`basedpyright-langserver`, `pyright-langserver`), TypeScript/JavaScript
  (`typescript-language-server`), Rust (`rust-analyzer`), and Go (`gopls`).
- **Debounced Execution**: Diagnostics are debounced to prevent flooding model turns
  during rapid file operations.

## Sigil Hooks

- `after_spell_result` — captures edited file paths, requests diagnostics from the
  active LSP client, and injects actionable error messages into the turn transcript.

## Installation

```bash
mvgeos rune install lsp-bridge
```
