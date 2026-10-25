# Session Search

Full-text search over your session history. This rune incrementally indexes
Mvge sessions into a local SQLite FTS5 database with BM25 relevance ranking,
so agents recover past context, decisions, and code snippets in a few hundred
tokens instead of re-reading whole transcripts.

The design follows
[search-antigravity](https://github.com/kingjulian24/search-antigravity)
(a FastMCP engine for Google Antigravity transcripts), reimplemented natively
for the rune system: five spells instead of five MCP tools, lazy just-in-time
refresh instead of a background daemon thread, and zero third-party
dependencies.

## Sources

- **Tome v1** sessions in `~/.agents/sessions/` — always indexed.
- **Pi sessions** (v3/v4) in the same directory — indexed when the
  `pi-codec` rune is installed. Reading goes through the session-codec
  abstraction, so a Pi session stays a Pi session; no conversion happens.
- **Antigravity transcripts** in `~/.gemini/antigravity/brain/` — indexed
  when the directory exists.

## Spells

- `session_search` — search with snippet highlights. Supports exact
  `"phrases"`, `prefix*` queries, and `AND`/`OR`/`NOT` operators, plus
  `conversation_id` and `type_filter` narrowing.
- `session_get_step` — full dialogue turns around a step index found by search.
- `session_list` — recent sessions with step counts and activity ranges.
- `session_stats` — session/message counts, per-type breakdown, date span,
  and database size.
- `session_sync` — on-demand incremental re-index; pass `force: true` for a
  full rescan.

## Sync behavior

The index refreshes automatically: on session start and just-in-time before
read spells (at most once per 30 seconds, mtime-based so unchanged sessions
are skipped). There is deliberately no background daemon — rune factories
run in-process, so periodic work happens lazily.

## Configuration

All settings are environment variables:

- `SESSION_SEARCH_DB_PATH` (default `~/.agents/sessions/.session-search.db`)
- `SESSION_SEARCH_TOME_DIR` (default `~/.agents/sessions/`)
- `ANTIGRAVITY_BRAIN_DIR` (default `~/.gemini/antigravity/brain`)

## Privacy

The index is a local SQLite file next to your sessions. Nothing leaves the
machine. Delete the database file to drop the index; it rebuilds on next use.

## Dependencies

None — stdlib `sqlite3` only.
