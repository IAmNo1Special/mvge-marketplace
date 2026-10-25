# lsp-bridge

Language Server Protocol (LSP) diagnostics bridge providing debounced compiler errors on edit and write for Mvge.

## Features
- **LSP Diagnostics**: Integrates with language servers (Pyright, rust-analyzer, etc.) to capture real-time diagnostics.
- **Debounced Feedback**: Emits compiler and type errors after `edit` and `write` spell casts without overwhelming turn context.
- **Proactive Diagnostics**: Surfaces targeted errors so agents catch issues before running test suites.
