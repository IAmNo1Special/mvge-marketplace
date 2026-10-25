# session-search

Full-text BM25 search over Mvge sessions, inspired by
[search-antigravity](https://github.com/kingjulian24/search-antigravity)
(a FastMCP search engine for Google Antigravity transcripts).

This is a native reimplementation for the Mvge rune system — no code is
shared with the upstream project. It indexes three session sources into a
local SQLite FTS5 database:

- **Tome v1** sessions from `~/.agents/sessions/`
- **Pi sessions** (v3/v4) from the same directory, when the `pi-codec` rune
  is installed — reading goes through the codec abstraction, so no
  conversion ever happens
- **Antigravity transcripts** from `~/.gemini/antigravity/brain/`, when present

## Spells

- `session_search` — BM25 search with snippets; phrases, `prefix*`, AND/OR/NOT
- `session_get_step` — dialogue window around a step index
- `session_list` — recent sessions by latest activity
- `session_stats` — counts, type breakdown, date span, disk usage
- `session_sync` — on-demand incremental re-index (`force` for full rescan)

The index refreshes just-in-time (30s cooldown) on every read spell and on
session start. There is no background daemon thread: rune factories run
in-process, so periodic sync is done lazily instead.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `SESSION_SEARCH_DB_PATH` | `~/.agents/sessions/.session-search.db` | Index location |
| `SESSION_SEARCH_TOME_DIR` | `~/.agents/sessions/` | Session directory |
| `ANTIGRAVITY_BRAIN_DIR` | `~/.gemini/antigravity/brain` | Transcript source |

No third-party dependencies — stdlib `sqlite3` only.

## Testing

```bash
uv run python -m pytest runes/session-search/tests -q
```
