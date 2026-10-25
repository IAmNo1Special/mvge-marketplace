# Pi Codec

Native Pi session support for Mvge. This rune teaches Mvge to read, resume, append to, compact, and fork real [Pi](https://github.com/earendil-works/pi) agent session logs (JSONL v3/v4) **without converting them** — a Pi session stays a Pi session.

Pi and Mvge both store sessions as JSONL, but their schemas are unrelated: Pi's format versions (v3/v4) are Pi's own invention, and Mvge Tomes are v1 with a different entry model. Mvge core stays format-agnostic; this rune plugs in a `SessionCodec` that owns Pi files end to end.

## What happens automatically

When the rune is installed, Mvge detects Pi files by their header and handles them natively:

- **Resume**: `mvge --resume <pi-session.jsonl>` opens the Pi session directly. Every load validates strictly (duplicate ids, dangling parents, bad seqs are hard errors, matching Pi's own strictness).
- **Append**: new entries are written as Pi v4 entry + branch-tip transactions, exactly like Pi writes them. The file remains readable by real Pi.
- **v3 sessions**: opened read-only as-is; the first append atomically migrates the file to v4, the same way Pi itself upgrades legacy sessions (normalized baseline writes, imported-usage adjustment, then your entry).
- **Compaction**: Mvge compactions are translated into Pi-native compaction entries (summary + retained tail), so Pi reconstructs context the same way.
- **Fork**: forking a Pi session produces a Pi-native v4 fork with `parentSessionId` pointing at the source.
- **Torn tails**: a crash-torn final line is repaired during open, the way Pi does.

Fresh `mvge` sessions are still Tome v1 — Pi is opt-in per file, never a default.

## Commands

- `mvge pi-export <tome-id>` — one-way export of a Tome v1 session to a new Pi-native v4 file (`<tome-id>.pi.jsonl` in the current directory). A copy, not a sync: the Tome is untouched.
- `mvge pi-export <tome-id> --output <path>` — choose the output path.
- `mvge pi-export <tome-id> --tome-dir <dir>` — choose the Tome directory.
- `mvge pi-export <tome-id> --force` — overwrite an existing output file.
- `mvge pi-validate <session-file>` — thin read-only validation of a Pi session file. Runs the same strict validator Mvge uses at open time; never modifies the file.

Export details:

- Every Tome entry is reminted with a fresh Pi-style UUIDv7 id and parent chains are preserved.
- `LEAF` branch markers have no Pi equivalent and are skipped (orphaned children re-root onto the nearest kept ancestor).
- Messages, spell results, compactions, and custom entries all translate to their Pi-native forms.

## Dependencies

`typer>=0.12`.
