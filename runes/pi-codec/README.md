# pi-codec

Native Pi session support for Mvge: read, resume, append to, compact, and fork real [Pi](https://github.com/earendil-works/pi) agent session logs (JSONL v3/v4) without converting them. A Pi session stays a Pi session.

This rune provides a `SessionCodec` (declared type `Codec` in the manifest). When installed, Mvge detects Pi files by header and handles them natively:

- **Resume**: `mvge --resume <pi-session.jsonl>` opens the Pi session directly. Every load validates strictly (duplicate ids, dangling parents, bad seqs are hard errors, matching Pi's own strictness).
- **Append**: new entries are written as Pi v4 entry + branch-tip transactions, exactly like Pi writes them. The file stays readable by real Pi.
- **v3 sessions**: opened read-only as-is; the first append atomically migrates the file to v4, the same way Pi itself upgrades legacy sessions.
- **Compaction**: Mvge compactions are translated into Pi-native compaction entries, so Pi reconstructs context the same way.
- **Fork**: forking a Pi session produces a Pi-native v4 fork with `parentSessionId` pointing at the source, mirroring Pi's own fork value policy (session name, retained labels, lane config, cleared lane state).
- **Torn tails**: a crash-torn final line is repaired during open, the way Pi does.

Fresh `mvge` sessions are still Tome v1 — Pi is opt-in per file, never a default. There is no Pi→Tome conversion anywhere in this rune.

## Commands

- `mvge pi-export <tome-id>` — one-way export of a Tome v1 session to a new Pi-native v4 file. A copy, not a sync: the Tome is untouched.
- `mvge pi-validate <file>` — thin read-only validation of a Pi session file using the same strict validator Mvge runs at open time. Never modifies the file.

## Layout

- `mvge_runes_pi_codec/codec.py` — `PiSessionCodec`: v3/v4 detection, strict validation, torn-tail repair, native appends, atomic v3→v4 migration, compaction translation, Pi-native fork.
- `mvge_runes_pi_codec/exporter.py` — one-way Tome v1 → Pi v4 export.
- `mvge_runes_pi_codec/cli.py` — Typer CLIs (mounted as `pi-export`, `pi-validate`).
- `tests/` — fixtures (realistic v3/v4 sessions) + tests.

## Design rules

- Source of truth is Pi's source (`packages/agent/src/harness/session/`), not its docs.
- No silent Pi→Tome conversion, ever.
