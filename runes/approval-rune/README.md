# approval-rune

Fail-closed execution gate for Mvge: every uncovered mutating spell cast
pauses until the user approves it — once, for the spell, for the session, or
for the project. Deny rules always win. Every decision lands in a durable,
redacted audit log before anything executes.

## How it works

1. **Gate** (`gate.py`) — the critical spell gate. Resolves the spell and
   validates arguments, normalizes and freezes them (canonical JSON digest),
   then evaluates deterministic precedence: explicit deny > engine-marked
   non-runner-origin read-only auto-allow > session approve-all > project
   approval > per-spell allow (identity + code digest + scope + argument
   constraints) > per-cast prompt.
2. **Policy** (`policy.py`) — user-owned TOML at `~/.agents/approval/policy.toml`
   (never inside a repo). Atomic writes (lock + temp file + fsync + atomic
   replace, user-only permissions). Malformed TOML fails closed: a bad rule is
   dropped with a warning; an unparseable file suspends all allows and the
   session switch and prompts on every mutating cast.
3. **Constraints** — `exact`, `one_of`, `path_within_project` (resolves,
   rejects escape), `command_prefix` (POSIX-only shlex program-identity gate).
4. **Audit** (`audit.py`) — `~/.agents/approval/audit.jsonl`, lock-protected
   appends, user-only permissions, recursive secret redaction, 30-day / 25 MB
   rotation. Decision records land before execution; denied casts get an end
   event, never a start event. Audit failure denies unless the explicit
   temporary "continue without audit" override is active.
5. **Session approval** — Rune-owned `(session_id, approved)` flag, reconciled
   against the live runner session id on every cast; any mismatch clears it.
6. **Permissions view** (`views.py`) — plain-data surface for the GUI settings
   dialog: `get_permissions_view()` plus `revoke_grant(kind, id)` with kinds
   `allow_rule`, `deny_rule`, `project`, `session`.

The rune never imports the engine, NiceGUI, or CLI code; never declares
`spell_gateway`; never touches the global spell allowlist. Presentation is
host-owned: the rune calls `RuneAPI.request_approval(request)` and the host's
presenter answers. With no presenter bound, casts deny (headless default).

## Policy file

```toml
schema_version = 1
install_id = "<host-stamped uuid>"

[[always_deny]]
id = "deny-0"
[always_deny.spell]
name = "deploy"
source_kind = "builtin"
source_id = "ops"
source_scope = "agent"

[[always_allow]]
id = "allow-0"
project = "/canonical/project"   # optional: omit for global
[always_allow.spell]
name = "bash"
source_kind = "builtin"
source_id = "coding_mvge"
source_scope = "agent"
code_digest = "sha256:..."

[[always_allow.constraints]]
field = "command"
op = "command_prefix"
program_glob = "git"
args_glob = "status*"

[[approved_projects]]
root = "/canonical/project"
enabled = true
```

## Dependencies

`tomli-w` (TOML writing; reading uses stdlib `tomllib`). Everything else is
stdlib.
