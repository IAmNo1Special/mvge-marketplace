# codeact-rune

CodeAct Python executor with AST safety, tool write-protection, and auto-dispatch for MvgeOS.

## Features
- **CodeAct Execution**: Model executes arbitrary Python snippets in a sandboxed session environment.
- **AST Safety Analysis**: Parses Python AST before execution to prevent unauthorized operations and enforce tool write-protection.
- **Auto-Dispatch**: Intercepts and executes multi-action code loops in a single turn.
