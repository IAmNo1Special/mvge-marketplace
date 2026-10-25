# Selfmod Bridge

Official self-modification & customization bridge for Mvge. It lets the
agent extend itself: scaffold spells, runes, and skills from normative
templates, revise its own instructions, and snapshot / roll back the files
that define it.

## How it works

- **Prompt injection** — on `before_mvge_start` the rune appends its
  `Self-Modification & Customization:` section to the system prompt: one
  `- Runes: <path>/AGENTS.md` line per configured runes path (no existence
  check, matching the old engine) plus a `- System Instructions: <path>`
  line when the file exists. The section is byte-identical to the text the
  old engine prompt carried (minus a dead Spells pointer). Injection runs
  exactly once per prompt build, guarded on the section marker itself. If
  nothing resolves, the prompt is left unchanged and the rune emits the
  `selfmod_bridge_warning` event (stage `bypass`) plus a warning log —
  zero prompt overhead, not zero signal.
- **State rehydration** — the hook payload carries the agent config dir,
  runes paths, system prompt path, spells dir, and cwd (attribute access
  first, dict fallback). Missing fields leave the prompt unchanged (with
  the bypass warning); a wiped state makes spell handlers fail loudly with
  `state_not_initialized` rather than act on guesses.
- **Normative templates** — `templates.py` is the single generator for all
  scaffolded content. Model-controlled strings are embedded only as
  `json.dumps()` literals (valid docstrings — breakout is impossible);
  JSON is built with the `json` module. Golden tests pin every template
  byte-for-byte and feed adversarial inputs (`"""`, newlines, `${}`,
  backticks, unicode, non-BMP) through them.
- **Atomic writes** — scaffold trees are built in a sibling staging
  directory (`.selfmod-staging-<uuid>`) and moved into place with one
  `os.replace`; never-overwrite is enforced by an explicit
  verify-non-existence check under the instance lock (POSIX `os.replace`
  would replace an empty dir, so the rename alone is not the guard). New
  spell files use exclusive-create; instruction edits go through sibling
  temp file + rename. A failed snapshot aborts the mutation before
  anything is written (snapshot-first).
- **Reload staleness** — scaffolded code lands on disk immediately but is
  not loaded into the running process. Every mutating result therefore
  carries `effective_after: "reload"` and a human-readable `note`
  explaining the staleness — including failures. `self_snapshot` is
  exempt: it is immediately durable and read-only.

## Spells

| Spell | Kind | What it does |
|---|---|---|
| `scaffold_spell` | mutating | Generate `<spells_dir>/<name>.py` from the normative template (exclusive-create). Seeds the spells-dir `AGENTS.md` when absent. |
| `scaffold_rune` | mutating | Generate a full rune tree (manifest, pyproject, package, root entry, CLI, tests), atomic via staging + single rename. |
| `scaffold_skill` | mutating | Generate `<root>/<name>/SKILL.md` at `agent`, `user`, or `project` scope. `description` required. |
| `extension_status` | read-only | Inventory of runes/spells/skills + system instructions file + snapshots. |
| `revise_persona` | mutating | Replace one exact span in the instructions file (0 → `no_match`, 2+ → `ambiguous_match`). |
| `teach` | mutating | Append under a named `## <section>` header or replace one exact match in the instructions file. |
| `self_snapshot` | read-only | Snapshot the system file, spells dir, config, and manifest. Capped at 20, oldest pruned. |
| `self_rollback` | mutating | Restore a snapshot; takes a pre-rollback snapshot first. |

Mutating spells are narrowed from the model's view and route through the
approval-rune when it is installed — this rune implements no private
execution gate. Every spell carries an explicit JSON Schema (`parameters`);
the engine does not derive one from handler signatures.

## Commands

- `/selfmod status` — extension inventory and snapshot count.
- `/selfmod show` — the effective injected prompt section.

## Hooks

`before_mvge_start`. Warning event: `selfmod_bridge_warning` with `stage`
`bypass` / `prompt_build` / `payload_shape`.

## Snapshots

`<config_dir>/.selfmod-snapshots/<id>/` — namespaced but non-standard (no
established protocol covers snapshots). Each snapshot holds `SYSTEM.md`,
`spells/`, `config.json` (when present), and `manifest.json`. Rollback is
contained to the snapshot tree and never deletes the snapshots directory
itself.

## Dependencies

None beyond the Mvge engine. Scaffolding a skill validates through the
real skills-bridge parser when it is installed.
