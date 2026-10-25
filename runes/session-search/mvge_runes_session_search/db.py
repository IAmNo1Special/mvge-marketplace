"""SQLite + FTS5 full-text search engine for indexed sessions."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any


def get_db_connection(db_path: Path | str) -> sqlite3.Connection:
    """Open a configured SQLite connection, creating parent dirs."""
    target = Path(db_path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables, the FTS5 index, sync triggers, and indices."""
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                conversation_id TEXT PRIMARY KEY,
                source TEXT NOT NULL DEFAULT 'tome',
                title TEXT,
                created_at TEXT,
                updated_at TEXT,
                total_steps INTEGER DEFAULT 0,
                file_path TEXT,
                last_indexed_mtime REAL
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                created_at TEXT,
                role TEXT,
                type TEXT,
                content TEXT,
                spell_names TEXT,
                has_thinking INTEGER DEFAULT 0,
                FOREIGN KEY(conversation_id)
                    REFERENCES conversations(conversation_id)
                    ON DELETE CASCADE
            );
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_conv_step
            ON messages(conversation_id, step_index);
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_conversations_updated
            ON conversations(updated_at DESC);
        """)
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                content,
                spell_names,
                conversation_id UNINDEXED,
                step_index UNINDEXED,
                tokenize = 'porter unicode61'
            );
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS messages_ai
            AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(
                    rowid, content, spell_names,
                    conversation_id, step_index
                )
                VALUES (
                    new.id, new.content, new.spell_names,
                    new.conversation_id, new.step_index
                );
            END;
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS messages_ad
            AFTER DELETE ON messages BEGIN
                DELETE FROM messages_fts WHERE rowid = old.id;
            END;
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS messages_au
            AFTER UPDATE ON messages BEGIN
                DELETE FROM messages_fts WHERE rowid = old.id;
                INSERT INTO messages_fts(
                    rowid, content, spell_names,
                    conversation_id, step_index
                )
                VALUES (
                    new.id, new.content, new.spell_names,
                    new.conversation_id, new.step_index
                );
            END;
        """)


_TOKEN_RE = re.compile(r'"([^"]*)"|(\S+)')


def sanitize_fts5_query(query: str) -> str:
    """Sanitize free text into safe FTS5 syntax.

    Preserves exact phrases, prefix queries, and AND/OR/NOT operators;
    drops unbalanced quotes and punctuation-only tokens.
    """
    query = query.strip()
    if not query:
        return ""
    if query.count('"') % 2 != 0:
        query = query.replace('"', " ")

    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(query):
        quoted, word = match.groups()
        if quoted is not None:
            clean = re.sub(r"[^\w\s\-]", " ", quoted).strip()
            if clean:
                tokens.append(f'"{clean}"')
        elif word is not None:
            upper = word.upper()
            if upper in ("AND", "OR", "NOT"):
                tokens.append(upper)
            elif word.endswith("*") and any(c.isalnum() for c in word[:-1]):
                clean_prefix = re.sub(r"[^\w\-]", "", word[:-1])
                if clean_prefix:
                    tokens.append(f"{clean_prefix}*")
            else:
                clean_word = re.sub(r"[^\w\-]", "", word).strip()
                if clean_word:
                    tokens.append(f'"{clean_word}"')
    return " ".join(tokens)


def search_messages(
    conn: sqlite3.Connection,
    query: str,
    conversation_id: str | None = None,
    type_filter: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """BM25-ranked full-text search across indexed messages."""
    fts_query = sanitize_fts5_query(query)
    if not fts_query:
        return []
    limit = max(1, min(limit, 50))
    clauses = ["messages_fts MATCH ?"]
    params: list[Any] = [fts_query]
    if conversation_id:
        clauses.append("m.conversation_id = ?")
        params.append(conversation_id)
    if type_filter:
        clauses.append("m.type = ?")
        params.append(type_filter.strip().upper())
    where_sql = " AND ".join(clauses)
    params.append(limit)
    sql = f"""
        SELECT
            m.id,
            m.conversation_id,
            c.source,
            m.step_index,
            m.created_at,
            m.role,
            m.type,
            m.spell_names,
            m.has_thinking,
            c.title,
            snippet(messages_fts, 0, '**', '**', '...', 35) AS snippet,
            bm25(messages_fts) AS rank_score
        FROM messages_fts
        JOIN messages m ON m.id = messages_fts.rowid
        JOIN conversations c ON c.conversation_id = m.conversation_id
        WHERE {where_sql}
        ORDER BY rank_score ASC
        LIMIT ?
    """
    cursor = conn.execute(sql, params)
    return [dict(row) for row in cursor.fetchall()]


def get_step_context(
    conn: sqlite3.Connection,
    conversation_id: str,
    step_index: int,
    context_window: int = 2,
) -> list[dict[str, Any]]:
    """Messages surrounding a step index within one conversation."""
    window = max(0, context_window)
    sql = """
        SELECT
            m.id,
            m.conversation_id,
            c.source,
            m.step_index,
            m.created_at,
            m.role,
            m.type,
            m.content,
            m.spell_names,
            m.has_thinking,
            c.title
        FROM messages m
        JOIN conversations c ON c.conversation_id = m.conversation_id
        WHERE m.conversation_id = ?
          AND m.step_index >= ?
          AND m.step_index <= ?
        ORDER BY m.step_index ASC
    """
    cursor = conn.execute(
        sql, (conversation_id, max(0, step_index - window), step_index + window)
    )
    return [dict(row) for row in cursor.fetchall()]


def list_recent_conversations(
    conn: sqlite3.Connection,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Conversations ordered by latest activity, paginated."""
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    sql = """
        SELECT
            conversation_id,
            source,
            title,
            created_at,
            updated_at,
            total_steps,
            file_path
        FROM conversations
        ORDER BY updated_at DESC
        LIMIT ? OFFSET ?
    """
    cursor = conn.execute(sql, (limit, offset))
    return [dict(row) for row in cursor.fetchall()]


def get_database_stats(
    conn: sqlite3.Connection,
    db_path: Path | str,
) -> dict[str, Any]:
    """Aggregate index metrics: counts, type breakdown, size on disk."""
    target = Path(db_path).expanduser()
    conv_row = conn.execute(
        "SELECT COUNT(*) AS total, MIN(created_at) AS earliest, "
        "MAX(updated_at) AS latest FROM conversations"
    ).fetchone()
    msg_row = conn.execute("SELECT COUNT(*) AS total FROM messages").fetchone()
    type_rows = conn.execute(
        "SELECT type, COUNT(*) AS count FROM messages GROUP BY type"
    ).fetchall()
    size_bytes = target.stat().st_size if target.exists() else 0
    return {
        "total_conversations": conv_row["total"] if conv_row else 0,
        "total_messages": msg_row["total"] if msg_row else 0,
        "earliest_date": conv_row["earliest"] if conv_row else None,
        "latest_date": conv_row["latest"] if conv_row else None,
        "messages_by_type": {row["type"]: row["count"] for row in type_rows},
        "db_size_bytes": size_bytes,
        "db_size_mb": round(size_bytes / (1024 * 1024), 2),
        "db_path": str(target),
    }
