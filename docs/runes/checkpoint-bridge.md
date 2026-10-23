# Checkpoint Bridge

Shadow-git workspace checkpoints, atomic leaf restoration, and file rollbacks.
This rune maintains an isolated, bare shadow git repository for each workspace,
taking automatic snapshots before mutating operations and enabling reliable rollbacks
to any prior session leaf.

## Features

- **Shadow Git Tracking**: Workspace changes are tracked in a detached, internal Git
  store under `~/.agents/sessions/checkpoints/` without touching your project's own `.git`.
- **Atomic Rollbacks**: Restore individual files or entire states to any recorded
  checkpoint leaf without manual diff inspection.
- **Safety**: Untracked snapshot files have an automatic size ceiling (2 MiB) to avoid
  bloating shadow repositories with build artifacts.

## Spells

- `checkpoint_restore_file` — rollback a single file to its version at a specified
  session leaf (or defaults to the latest snapshot).
  - `path`: relative path of the file to rollback.
  - `leaf_id` (optional): target checkpoint leaf ID.

## Commands

- `checkpoint` — view working tree diff against the session checkpoint.

## Installation

```bash
mvgeos rune install checkpoint-bridge
```
