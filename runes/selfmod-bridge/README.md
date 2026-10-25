# selfmod-bridge Rune

Official **self-modification & customization** bridge for Mvge. Gives the
agent the ability to extend itself — scaffold spells, runes, and skills from
normative templates, revise its own instructions, and snapshot / roll back
the files that define it.

## Install

```bash
mvge rune install selfmod-bridge
```

On `before_mvge_start` the rune appends a `Self-Modification &
Customization:` section to the system prompt (one `- Runes:
<path>/AGENTS.md` line per configured runes path — no existence check —
plus a `- System Instructions: <path>` line when the file exists;
byte-identical to the text the old engine prompt carried, minus a dead
Spells pointer). Injection runs exactly once per prompt build, guarded on
the section marker itself. When nothing resolves, the prompt is left
unchanged and the rune emits a `selfmod_bridge_warning` event plus a
warning log (zero prompt overhead, not zero signal). Prompt injection needs
the hook payload fields (`config_dir`, `runes_paths`,
`system_path`, `spells_dir`, …) from the parallel engine build —
without them the section is skipped with a warning.

## Usage

Talk to the agent naturally, or use the commands:

- `/selfmod status` — show extension inventory and snapshot count.
- `/selfmod show` — show the effective injected prompt section.

## Spells

Read-only (safe to call any time):

- `extension_status()` — inventory of installed runes/spells/skills, the
  resolved system instructions file, and the snapshot list.
- `self_snapshot(label=None)` — snapshot the self: the system instructions
  file, the active spells dir, `config.json`, and the rune manifest, into
  `<config_dir>/.selfmod-snapshots/<id>/`. Immediately durable; capped at
  20, oldest pruned.

Mutating (narrowed from the model's view and routed through the
approval-rune when it is installed; this rune implements **no private
execution gate**):

- `scaffold_spell(name, description)` — generate `<spells_dir>/<name>.py`
  from the normative template via exclusive-create (never overwrites).
  Seeds a minimal `AGENTS.md` in the spells dir when absent.
- `scaffold_rune(name, description, target_dir=None)` — generate a full
  rune tree (manifest, pyproject, package, root entry, CLI, tests),
  atomically: the tree is built in a sibling staging dir
  (`.selfmod-staging-<uuid>`) and moved into place with one `os.replace`.
  `target_dir` must be absolute and inside a configured runes path;
  default is the first writable configured runes path (agent > user >
  project scope precedence).
- `scaffold_skill(name, description, scope="agent")` — generate
  `<root>/<name>/SKILL.md` at `agent` (`<config_dir>/skills`), `user`
  (`$MVGE_GLOBAL_DIR/skills`), or `project` scope. `description` is
  required — the skills-bridge loader skips skills with a missing or empty
  description.
- `revise_persona(old_text, new_text, path=None)` — replace one exact span
  in the instructions file (defaults to the active system instructions
  file). 0 matches → `no_match`, 2+ → `ambiguous_match`; no write.
- `teach(section, mode, old_text="", new_text="", text="", path=None)` —
  `mode="append"` adds a paragraph under the named `## <section>` header
  (header created at EOF when missing; duplicate paragraphs suppressed);
  `mode="replace"` patches one exact match like `revise_persona`.
  Snapshot-first, atomic write.
- `self_rollback(snapshot_id)` — restore a snapshot. Takes a pre-rollback
  snapshot first, so rollback is itself recoverable. The id is resolved
  under the snapshots dir *before* the existence check — traversal gets
  `unknown_snapshot`, never a distinct not-found vs rejected signal.

All mutating spells return `effective_after: "reload"` with a human-readable
`note` — the change is on disk immediately but is not live in the running
process until the next reload. Every mutating result carries these fields,
including failures.

## Failure codes

Failure results are `{"ok": False, "error": "<code>", "message": ...}`.
The pinned §6.3 vocabulary is the core — prefer a core code whenever one
fits — and ops may return supplemental op-specific codes for situations
the core does not name (reuse rule: never mint a new code when a core one
fits). Every code is documented with its op below. Mutating failures also
carry `effective_after: "reload"` + `note`; read-only failures
(`extension_status`, `self_snapshot`) do not.

- `scaffold_spell` — `exists`, `invalid_name`, `no_spells_dir`,
  `state_not_initialized`.
- `scaffold_rune` — `exists`, `invalid_name`, `no_runes_paths`,
  `outside_runes_paths`, `scaffold_failed` (staging/rename I/O failure;
  the message names the step and carries the OS error),
  `state_not_initialized`.
- `scaffold_skill` — `exists`, `invalid_name`, `invalid_scope`,
  `missing_description`, `no_skills_dir`, `scaffold_failed` (as above),
  `state_not_initialized`.
- `extension_status` — `state_not_initialized`.
- `revise_persona` — `missing_old_text`, `missing_new_text`, `no_match`
  (also when the target file does not exist), `ambiguous_match`,
  `no_system_path`, `snapshot_failed`, `state_not_initialized`.
- `teach` — `missing_section`, `invalid_mode`, `missing_old_text`
  (replace mode), `no_match`, `ambiguous_match`, `no_system_path`,
  `snapshot_failed`, `state_not_initialized`.
- `self_snapshot` — `invalid_label`, `no_config_dir`, `snapshot_failed`,
  `state_not_initialized`.
- `self_rollback` — `unknown_snapshot` (covers not-found and rejected —
  never distinguished), `snapshot_failed` (pre-rollback snapshot I/O
  failure; the rollback aborts, nothing is restored),
  `state_not_initialized`.

`snapshot_failed` — the snapshot could not be written (disk full,
permissions, ...). The message names the failed phase (`mkdir`, `copy`,
or `manifest`) and carries the underlying OS error; the raw traceback is
logged via `logger.exception`; the failure is recorded in the engine-owned
audit trail (see below); any half-written snapshot dir is removed.
Mutating ops abort fail-closed on it — nothing is written when the
snapshot fails.

## Audit trail

Every mutating op emits exactly one audit event through the engine-owned
rune-op audit trail — successes and structured failures alike
(`snapshot_failed` included). The read-only `extension_status` query is
not audited.

- **Location.** `<user-scope>/.agents/extensions/audit.jsonl` — the global
  per-user layer (`$MVGE_GLOBAL_DIR` when set, else `~/.agents`),
  following the skills-bridge scope convention. Self-mod ops are
  agent-level mutations that outlive any single session, so
  session-scoped tome storage is the wrong home. The log is append-only
  JSONL (exclusive lock + `fsync` per write, `0600`/`0700` permissions,
  size/age rotation that archives rather than deletes), following the
  approval rune's durable audit-trail precedent.
- **Stamping.** The engine stamps `rune` (the manifest name — a rune
  cannot forge another rune's entries) and `timestamp` (UTC); these
  stamped fields always win over anything the rune passes.
- **Event schema.** `timestamp`, `rune`, `op`, `outcome` (`"ok"` /
  `"failed"`), `code` (`"ok"` or the op's failure code), `target` (path,
  name, or snapshot id), `message`, plus `snapshot_id` in `extra` when
  the op produced one.
- **Audit write failures are loud.** If the audit append fails, the op
  result becomes `audit_failed` (`ok: False`) — never a silent `ok`.
  When the mutation succeeded, the message states plainly that the
  change IS live but unrecorded (verify with `self_snapshot` or
  `extension_status`), and the reload-staleness fields are preserved.
  When the mutation failed, the original code/message are carried inside
  the `audit_failed` message so the root cause survives. An op that
  cannot prove it happened must never report silent success.
- **Host skew.** On hosts predating `RuneAPI.audit`, ops run unaudited
  with a warning (the audit trail is unavailable, not silently broken).

`audit_failed` is a wrapper-level code: it can replace any op's result
when the audit write fails, so it is not listed per-op above.

## Snapshots

Snapshots live under `<config_dir>/.selfmod-snapshots/<id>/`. Each holds
`SYSTEM.md` (the system instructions file), `spells/` (the active spells
dir), `config.json` (when present), and `manifest.json`. This location is
namespaced but **non-standard** — no established protocol covers snapshots,
so it is documented here rather than invented silently. Rollback restores
the system file and the spells dir, is contained to the snapshot (no path
escapes), and never deletes the snapshots directory itself. A failed
snapshot aborts the mutation before anything is written (snapshot-first).

## Hooks

- `before_mvge_start` — injects the `Self-Modification & Customization:`
  section into the system prompt, exactly once. Emits
  `selfmod_bridge_warning` with a `stage` of `bypass` (nothing resolved),
  `prompt_build` (section build failed), or `payload_shape` (unknown
  payload); the warning log is the live signal until a subscriber consumes
  the event.

## Safety notes

- Extension names are validated (lowercase identifiers, no leading
  underscore; Windows reserved device names like `con`/`nul` rejected) to
  stop path traversal at the API boundary.
- Never-overwrite is enforced by an explicit verify-non-existence check
  under the instance `asyncio.Lock`, immediately before the rename — POSIX
  `os.replace` would replace an existing *empty* dir, so the rename alone
  is not the guard. Cross-process races remain a documented residual.
- Two concurrent same-name scaffolds: one wins, the other gets `exists`;
  no interleaved tree (single-rename atomicity). Crash orphans are
  identifiable by the `.selfmod-staging-<uuid>` name pattern.
- `revise_persona` and `teach` share the instructions file by design (one
  mind, one file) — `revise_persona` patches exact spans, `teach` owns
  append + named-section lifecycle. Both are snapshot-first with atomic
  writes, and both take both sides of the diff explicitly in params so the
  approval presenter shows the user what will change.
