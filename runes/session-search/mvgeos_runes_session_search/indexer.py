"""Session indexers: Tome v1 + Pi sessions plus Antigravity transcripts."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mvge.core import sessions_dir
from mvge.tome.handle import TomeHandleFactory
from mvge.tome.types import TomeEntry, TomeEntryType

from mvgeos_runes_session_search.db import get_db_connection, init_db

logger = logging.getLogger(__name__)

DEFAULT_BRAIN_DIR = Path.home() / ".gemini" / "antigravity" / "brain"
MAX_CONTENT_CHARS = 10_000


def get_tome_dir() -> Path:
    """Session directory: explicit env override or the MvgeOS default."""
    env_dir = os.environ.get("SESSION_SEARCH_TOME_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    return sessions_dir().resolve()


def get_db_path() -> Path:
    """Index database path: explicit env override or the default file.

    Derived at call time rather than module import so the index follows
    $MVGEOS_GLOBAL_DIR when it is set after this module is loaded.
    """
    env_path = os.environ.get("SESSION_SEARCH_DB_PATH")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return (sessions_dir() / ".session-search.db").resolve()


def get_brain_dir() -> Path:
    """Antigravity brain directory: explicit env override or the default."""
    env_dir = os.environ.get("ANTIGRAVITY_BRAIN_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    return DEFAULT_BRAIN_DIR


def _block_texts(content: Any) -> tuple[str, list[str], list[str], bool]:
    """Split assistant content blocks into text, spells, and thinking."""
    texts: list[str] = []
    spells: list[str] = []
    thinking: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type", "")
        if kind == "text":
            texts.append(str(block.get("text", "")))
        elif kind in ("contemplation", "thinking"):
            thinking.append(str(block.get("thinking") or block.get("text")))
        elif kind in ("spell_cast", "tool_use"):
            name = block.get("spell") or block.get("name") or ""
            if name and name not in spells:
                spells.append(str(name))
    return "\n".join(texts), spells, thinking, bool(thinking)


def extract_text_content(payload: dict[str, Any]) -> tuple[str, str, bool]:
    """Normalize a Tome MESSAGE payload to (text, spell_names, thinking).

    Mirrors the role shapes produced by Tome v1 and the pi-codec mapping:
    user text, assistant blocks, and spell results.
    """
    role = str(payload.get("role", ""))
    content = payload.get("content", "")
    if role == "user":
        if isinstance(content, str):
            return content, "", False
        if isinstance(content, list):
            texts = [
                str(b.get("text", ""))
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            return "\n".join(texts), "", False
        return str(content), "", False
    if role == "assistant":
        if isinstance(content, str):
            return content, "", False
        if isinstance(content, list):
            text, spells, _, has_thinking = _block_texts(content)
            return text, ", ".join(spells), has_thinking
        return str(content), "", False
    text = content if isinstance(content, str) else str(content)
    spell = str(payload.get("spell_name") or payload.get("spell") or "")
    return text, spell, False


def sanitize_text_content(content: Any) -> str:
    """Normalize and truncate message content for indexing."""
    if content is None:
        return ""
    text = content if isinstance(content, str) else str(content)
    text = text.strip()
    if len(text) > MAX_CONTENT_CHARS:
        return text[:MAX_CONTENT_CHARS] + "\n[... truncated for index ...]"
    return text


def _iso_from_epoch(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()


def _derive_title(messages: list[dict[str, Any]], conv_id: str) -> str:
    for msg in messages:
        if msg["type"] == "USER" and msg["content"]:
            for line in msg["content"].splitlines():
                candidate = re.sub(r"<[^>]+>", "", line).strip().lstrip("#")
                candidate = candidate.strip()
                if candidate:
                    return candidate[:100]
    return f"Conversation {conv_id[:8]}"


def _source_for_file(path: Path) -> str:
    """Label a session file's format: 'pi' for Pi headers, else 'tome'."""
    try:
        with path.open("r", encoding="utf-8") as f:
            header = json.loads(f.readline())
    except (OSError, ValueError):
        return "tome"
    if not isinstance(header, dict):
        return "tome"
    if header.get("kind") == "header":
        return "pi"
    if header.get("type") == "session" and header.get("version") == 3:
        return "pi"
    return "tome"


def _tome_messages(
    factory: TomeHandleFactory, tome_id: str
) -> tuple[list[dict[str, Any]], str | None, str | None]:
    """Read a session's active branch as indexable message records."""
    leaf_id = factory.get_leaf_id(tome_id)
    entries = factory.get_entries_for_context(tome_id, leaf_id=leaf_id)
    messages: list[dict[str, Any]] = []
    earliest: str | None = None
    latest: str | None = None
    for step_index, entry in enumerate(entries):
        if entry.type == TomeEntryType.MESSAGE:
            payload = entry.payload or {}
            role = str(payload.get("role", ""))
            text, spells, thinking = extract_text_content(payload)
            msg_type = "USER" if role == "user" else role.upper() or "UNKNOWN"
            created = _iso_from_epoch(entry.timestamp)
            messages.append(
                {
                    "step_index": step_index,
                    "created_at": created,
                    "role": role,
                    "type": msg_type,
                    "content": sanitize_text_content(text),
                    "spell_names": spells,
                    "has_thinking": 1 if thinking else 0,
                }
            )
        elif entry.type == TomeEntryType.COMPACTION:
            payload = entry.payload or {}
            summary = payload.get("summary") or payload.get("text") or ""
            created = _iso_from_epoch(entry.timestamp)
            messages.append(
                {
                    "step_index": step_index,
                    "created_at": created,
                    "role": "compaction",
                    "type": "COMPACTION",
                    "content": sanitize_text_content(summary),
                    "spell_names": "",
                    "has_thinking": 0,
                }
            )
        else:
            continue
        created_at = messages[-1]["created_at"]
        if earliest is None:
            earliest = created_at
        latest = created_at
    return messages, earliest, latest


def _iter_tome_sessions(
    factory: TomeHandleFactory,
) -> list[tuple[str, str, Path, float]]:
    """Enumerate session files: (session_id, source, path, mtime)."""
    tome_dir = factory.dir
    if not tome_dir.exists() or not tome_dir.is_dir():
        return []
    found: list[tuple[str, str, Path, float]] = []
    for meta in factory.list_tomes():
        try:
            path = factory.tome_file(meta.id)
        except (FileNotFoundError, ValueError):
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        found.append((meta.id, _source_for_file(path), path, mtime))
    return found


def _extract_spell_names(tool_calls: Any) -> str:
    if not isinstance(tool_calls, list):
        return ""
    names: list[str] = []
    for call in tool_calls:
        if isinstance(call, dict) and call.get("name"):
            name = str(call["name"]).strip()
            if name and name not in names:
                names.append(name)
    return ", ".join(names)


def _parse_antigravity_transcript(
    transcript_path: Path, conversation_id: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Parse an Antigravity transcript.jsonl into meta + message records."""
    messages: list[dict[str, Any]] = []
    earliest: str | None = None
    latest: str | None = None
    derived_title: str | None = None
    file_mtime = transcript_path.stat().st_mtime
    file_dt = datetime.fromtimestamp(file_mtime, tz=UTC).isoformat()
    try:
        with transcript_path.open("r", encoding="utf-8", errors="replace") as f:
            for idx, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict):
                    continue
                step_index = record.get("step_index", idx)
                created_at = record.get("created_at") or file_dt
                msg_type = str(record.get("type", "UNKNOWN"))
                content = sanitize_text_content(record.get("content", ""))
                thinking = record.get("thinking")
                has_thinking = 1 if thinking and str(thinking).strip() else 0
                if earliest is None:
                    earliest = created_at
                latest = created_at
                if derived_title is None and msg_type == "USER_INPUT" and content:
                    for text_line in content.splitlines():
                        candidate = re.sub(r"<[^>]+>", "", text_line)
                        candidate = candidate.strip().lstrip("#").strip()
                        if candidate:
                            derived_title = candidate[:100]
                            break
                messages.append(
                    {
                        "step_index": step_index,
                        "created_at": created_at,
                        "role": "",
                        "type": msg_type,
                        "content": content,
                        "spell_names": _extract_spell_names(record.get("tool_calls")),
                        "has_thinking": has_thinking,
                    }
                )
    except OSError as exc:
        logger.warning("Error reading %s: %s", transcript_path, exc)
    meta = {
        "conversation_id": conversation_id,
        "source": "antigravity",
        "title": derived_title or f"Conversation {conversation_id[:8]}",
        "created_at": earliest or file_dt,
        "updated_at": latest or file_dt,
        "total_steps": len(messages),
        "file_path": str(transcript_path),
        "last_indexed_mtime": file_mtime,
    }
    return meta, messages


def find_antigravity_transcripts(
    brain_dir: Path,
) -> list[tuple[str, Path]]:
    """Locate transcript.jsonl files under an Antigravity brain directory."""
    results: list[tuple[str, Path]] = []
    if not brain_dir.exists() or not brain_dir.is_dir():
        return results
    try:
        for entry in brain_dir.iterdir():
            if not entry.is_dir():
                continue
            log_dir = entry / ".system_generated" / "logs"
            transcript = log_dir / "transcript.jsonl"
            if transcript.is_file():
                results.append((entry.name, transcript))
                continue
            full = log_dir / "transcript_full.jsonl"
            if full.is_file():
                results.append((entry.name, full))
    except OSError as exc:
        logger.warning("Error scanning brain dir %s: %s", brain_dir, exc)
    return results


def _store_conversation(
    conn: Any,
    meta: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    is_update: bool,
) -> None:
    conv_id = meta["conversation_id"]
    with conn:
        if is_update:
            conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
            conn.execute(
                "DELETE FROM conversations WHERE conversation_id = ?",
                (conv_id,),
            )
        conn.execute(
            """
            INSERT INTO conversations (
                conversation_id, source, title, created_at, updated_at,
                total_steps, file_path, last_indexed_mtime
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conv_id,
                meta["source"],
                meta["title"],
                meta["created_at"],
                meta["updated_at"],
                meta["total_steps"],
                meta["file_path"],
                meta["last_indexed_mtime"],
            ),
        )
        if messages:
            conn.executemany(
                """
                INSERT INTO messages (
                    conversation_id, step_index, created_at, role, type,
                    content, spell_names, has_thinking
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        conv_id,
                        m["step_index"],
                        m["created_at"],
                        m["role"],
                        m["type"],
                        m["content"],
                        m["spell_names"],
                        m["has_thinking"],
                    )
                    for m in messages
                ],
            )


def sync_index(
    tome_dir: Path | str | None = None,
    brain_dir: Path | str | None = None,
    db_path: Path | str | None = None,
    codecs: list[Any] | None = None,
    force_rescan: bool = False,
) -> dict[str, Any]:
    """Incrementally index Tome/Pi sessions and Antigravity transcripts."""
    start = time.time()
    target_tome = Path(tome_dir) if tome_dir else get_tome_dir()
    target_brain = Path(brain_dir) if brain_dir else get_brain_dir()
    conn = get_db_connection(db_path or get_db_path())
    init_db(conn)
    stats: dict[str, Any] = {
        "discovered_transcripts": 0,
        "indexed_new": 0,
        "updated": 0,
        "skipped": 0,
        "total_messages": 0,
        "duration_seconds": 0.0,
        "tome_dir": str(target_tome),
        "brain_dir": str(target_brain),
    }
    existing: dict[str, float] = {
        row["conversation_id"]: row["last_indexed_mtime"] or 0.0
        for row in conn.execute(
            "SELECT conversation_id, last_indexed_mtime FROM conversations"
        ).fetchall()
    }

    factory = TomeHandleFactory(target_tome, codecs=codecs or [])
    sessions = _iter_tome_sessions(factory)
    transcripts = find_antigravity_transcripts(target_brain)
    stats["discovered_transcripts"] = len(sessions) + len(transcripts)

    for conv_id, source, path, mtime in sessions:
        prev = existing.get(conv_id)
        if not force_rescan and prev is not None and prev >= mtime:
            stats["skipped"] += 1
            continue
        try:
            messages, earliest, latest = _tome_messages(factory, conv_id)
        except (FileNotFoundError, ValueError) as exc:
            logger.warning("Skipping unreadable session %s: %s", conv_id, exc)
            continue
        is_update = conv_id in existing
        meta = {
            "conversation_id": conv_id,
            "source": source,
            "title": _derive_title(messages, conv_id),
            "created_at": earliest or _iso_from_epoch(mtime),
            "updated_at": latest or _iso_from_epoch(mtime),
            "total_steps": len(messages),
            "file_path": str(path),
            "last_indexed_mtime": mtime,
        }
        _store_conversation(conn, meta, messages, is_update=is_update)
        existing[conv_id] = mtime
        stats["indexed_new" if not is_update else "updated"] += 1
        stats["total_messages"] += len(messages)

    for conv_id, transcript_path in transcripts:
        try:
            mtime = transcript_path.stat().st_mtime
        except OSError:
            continue
        prev = existing.get(conv_id)
        if not force_rescan and prev is not None and prev >= mtime:
            stats["skipped"] += 1
            continue
        meta, messages = _parse_antigravity_transcript(transcript_path, conv_id)
        is_update = conv_id in existing
        _store_conversation(conn, meta, messages, is_update=is_update)
        existing[conv_id] = mtime
        stats["indexed_new" if not is_update else "updated"] += 1
        stats["total_messages"] += len(messages)

    conn.close()
    stats["duration_seconds"] = round(time.time() - start, 3)
    return stats


def entry_to_record(entry: TomeEntry, step_index: int) -> dict[str, Any]:
    """Convert one Tome entry to an indexable message record (test seam)."""
    payload = entry.payload or {}
    text, spells, thinking = extract_text_content(payload)
    role = str(payload.get("role", ""))
    return {
        "step_index": step_index,
        "created_at": _iso_from_epoch(entry.timestamp),
        "role": role,
        "type": "USER" if role == "user" else role.upper() or "UNKNOWN",
        "content": sanitize_text_content(text),
        "spell_names": spells,
        "has_thinking": 1 if thinking else 0,
    }
