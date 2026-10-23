# Eval Bridge

Evaluation harness and reinforcement learning (RL) data exporter compatible with
`google/adk-python` evalsets. This rune provides offline verification of agent
trajectories against structured test cases and converts Tome transcripts into training
datasets.

## Features

- **Evalset Runner**: Execute `.evalset.json` test suites verifying tool trajectory
  sequences and response keyword matching.
- **RL Data Export**: Export session Tomes directly to ShareGPT or Atropos JSONL formats
  for fine-tuning and offline RL training.

## Commands

- `mvgeos eval run <path-to-evalset>` — run evaluation cases against trajectories.
  - `--output`, `-o`: save evaluation report JSON.
  - `--verbose`, `-v`: detailed per-case results.
- `mvgeos eval export-rl <tome-id-or-path>` — export a session Tome to RL dataset formats.
  - `--format`, `-f`: format type (`sharegpt` or `atropos`, default `sharegpt`).
  - `--output`, `-o`: output dataset destination file.

## Installation

```bash
mvgeos rune install eval-bridge --confirm-python-deps
```
