# CodeAct Rune

CodeAct Python executor with AST safety, tool write-protection, and auto-dispatch.
This rune enables the Mvge to express complex multi-step plans as executable Python
code, invoking spells directly as callable functions inside a sandboxed environment.

## Features

- **Direct Spell Invocation**: All loaded spells (e.g. `read`, `write`, `bash`)
  are exposed as callable functions in the Python execution environment.
- **AST Write Protection**: Prevents code from overwriting protected spells or keywords.
- **Controlled Termination**: Supports `final_answer(value)` to cleanly complete execution
  and return the computed result to the Summoner.
- **Safe Stdlib Modules**: Pre-imports common standard library modules (`math`, `json`,
  `re`, `datetime`, `collections`, `itertools`, etc.).

## Spells

- `execute_python` — execute Python code in a safe CodeAct sandbox with direct access
  to registered spells and `final_answer(value)`.
  - `code`: Python source string to execute.

## Installation

```bash
mvge rune install codeact-rune
```
