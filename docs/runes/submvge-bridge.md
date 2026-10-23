# Sub-Mvge Bridge

Kernel sub-Mvge summoning, persistent sub-sessions, and reactive wakeup bridge.
This rune equips a parent Mvge with spells to spawn specialized delegate sub-agents,
message them in background sub-sessions, and coordinate parallel workflows.

## Features

- **Isolated Sub-Sessions**: Sub-agents operate in persistent sub-sessions under
  `~/.agents/sessions/subsessions/` with their own independent Tomes.
- **Reactive Wakeup**: Parent Mvges can launch sub-agents asynchronously and receive
  automated wakeup messages upon sub-agent completion without blocking or polling.
- **Role Presets**: Built-in support for delegation roles such as `research` (restricted
  to read-only exploration spells) and `worker`.

## Spells

- `summon_mvges` — spawn one or more concurrent sub-agents.
  - `subagents`: list of sub-agent definitions, each specifying `role`, `prompt`,
    optional `type_name`, `model`, and `allowed_spells`.
  - `wait`: whether to await completion synchronously (`true`) or run asynchronously
    with reactive parent notification (`false`).
- `message_mvge` — send instructions or steering queries to an existing sub-Mvge.
  - `sub_mvge_id`: ID of the target sub-Mvge.
  - `message`: instruction string.
- `manage_mvges` — inspect or terminate active sub-agents.
  - `action`: `list`, `kill`, or `kill_all`.
  - `sub_mvge_ids`: optional IDs to terminate when action is `kill`.

## Installation

```bash
mvgeos rune install submvge-bridge
```
