"""Rune factory: session-search spells over Tome/Pi/Antigravity sessions."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mvge.core import agent_extensions_dir, extensions_dir
from mvge.runes.codecs import load_session_codecs
from mvge.runes.rune_api import RuneAPI
from mvge.runes.types import SigilHook, SpellDefinition

from mvgeos_runes_session_search.db import (
    get_database_stats,
    get_db_connection,
    get_step_context,
    list_recent_conversations,
    search_messages,
)
from mvgeos_runes_session_search.indexer import (
    get_brain_dir,
    get_db_path,
    get_tome_dir,
    sync_index,
)

logger = logging.getLogger(__name__)

SYNC_COOLDOWN_SECONDS = 30.0


@dataclass
class SearchState:
    tome_dir: Path
    brain_dir: Path
    db_path: Path
    codecs: list[Any] = field(default_factory=list)
    last_sync: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)


def _extension_dirs(agent_name: str, cwd: str) -> list[Path]:
    candidates = [extensions_dir()]
    if agent_name:
        candidates.append(agent_extensions_dir(agent_name))
    if cwd:
        candidates.append(Path(cwd) / ".agents" / "extensions")
    return [d for d in candidates if d.exists()]


def _discover_codecs(agent_name: str, cwd: str) -> list[Any]:
    try:
        codecs, _diagnostics = load_session_codecs(_extension_dirs(agent_name, cwd))
    except (ImportError, AttributeError, ValueError, TypeError) as exc:
        logger.warning("Session codec discovery failed: %s", exc)
        return []
    return codecs


def _resolve_state(api: RuneAPI) -> SearchState:
    runner = getattr(api, "_runner", None)
    ctx = getattr(runner, "context", None)
    agent_name = str(getattr(ctx, "agent_name", "") or "")
    cwd = str(getattr(ctx, "cwd", "") or "")
    ctx_tome = str(getattr(ctx, "tome_dir", "") or "")
    tome_dir = get_tome_dir()
    if "SESSION_SEARCH_TOME_DIR" not in os.environ and ctx_tome:
        tome_dir = Path(ctx_tome).expanduser().resolve()
    return SearchState(
        tome_dir=tome_dir,
        brain_dir=get_brain_dir(),
        db_path=get_db_path(),
        codecs=_discover_codecs(agent_name, cwd),
    )


def _ensure_fresh(state: SearchState, *, force: bool = False) -> None:
    with state.lock:
        now = time.time()
        if not force and (now - state.last_sync) < SYNC_COOLDOWN_SECONDS:
            return
        try:
            sync_index(
                tome_dir=state.tome_dir,
                brain_dir=state.brain_dir,
                db_path=state.db_path,
                codecs=state.codecs,
            )
        except (OSError, ValueError, sqlite3.Error) as exc:
            logger.warning("Session index refresh failed: %s", exc)
        state.last_sync = time.time()


def _payload(params: Any, arguments: Any) -> dict[str, Any]:
    if isinstance(params, dict):
        return params
    if isinstance(arguments, dict):
        return arguments
    return {}


def rune_factory(api: RuneAPI) -> None:
    """Register session-search spells and lifecycle hooks."""
    state = _resolve_state(api)

    async def on_session_start(_data: Any = None) -> None:
        _ensure_fresh(state, force=True)

    async def on_session_shutdown(_data: Any = None) -> None:
        return None

    api.on(SigilHook.SESSION_START, on_session_start)
    api.on(SigilHook.SESSION_SHUTDOWN, on_session_shutdown)

    async def handle_search(
        params: dict[str, Any] | None = None,
        *args: Any,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        payload = _payload(params, arguments)
        query = str(payload.get("query", ""))
        _ensure_fresh(state)
        conn = get_db_connection(state.db_path)
        try:
            results = search_messages(
                conn,
                query=query,
                conversation_id=payload.get("conversation_id"),
                type_filter=payload.get("type_filter"),
                limit=int(payload.get("limit", 10)),
            )
        finally:
            conn.close()
        if not results:
            return f"No results found for query: '{query}'."
        lines = [f"### Search Results for: '{query}' ({len(results)} matches)\n"]
        for idx, r in enumerate(results, 1):
            title = r.get("title") or "Untitled"
            snippet = str(r.get("snippet") or "").replace("\n", " ")
            lines.append(
                f"{idx}. **{title}** (`{r['conversation_id']}`) "
                f"[{r.get('source', 'tome')}] - "
                f"Step #{r['step_index']} `{r['type']}`\n"
                f"   - **Timestamp:** {r['created_at']}\n"
                + (
                    f"   - **Spells:** `{r['spell_names']}`\n"
                    if r.get("spell_names")
                    else ""
                )
                + f"   - **Snippet:** {snippet}\n"
            )
        return "\n".join(lines)

    async def handle_get_step(
        params: dict[str, Any] | None = None,
        *args: Any,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        payload = _payload(params, arguments)
        conv_id = str(payload.get("conversation_id", ""))
        step_index = int(payload.get("step_index", 0))
        window = int(payload.get("context_window", 2))
        _ensure_fresh(state)
        conn = get_db_connection(state.db_path)
        try:
            steps = get_step_context(conn, conv_id, step_index, context_window=window)
        finally:
            conn.close()
        if not steps:
            return (
                f"No steps found for conversation `{conv_id}` near step #{step_index}."
            )
        title = steps[0].get("title") or conv_id
        lines = [
            f"### Conversation Context: `{conv_id}`",
            f"**Title:** {title}",
            (
                f"**Window:** Steps #{steps[0]['step_index']} to "
                f"#{steps[-1]['step_index']} (Target: #{step_index})\n"
            ),
        ]
        for s in steps:
            marker = " [TARGET STEP]" if s["step_index"] == step_index else ""
            lines.append(
                f"#### Step #{s['step_index']}: {s['role']} ({s['type']}){marker}"
            )
            lines.append(f"*Timestamp: {s['created_at']}*")
            if s.get("spell_names"):
                lines.append(f"*Spells: `{s['spell_names']}`*")
            lines.append("")
            lines.append(s["content"] or "*(empty content)*")
            lines.append("\n---\n")
        return "\n".join(lines)

    async def handle_list(
        params: dict[str, Any] | None = None,
        *args: Any,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        payload = _payload(params, arguments)
        limit = int(payload.get("limit", 20))
        offset = int(payload.get("offset", 0))
        _ensure_fresh(state)
        conn = get_db_connection(state.db_path)
        try:
            convs = list_recent_conversations(conn, limit=limit, offset=offset)
        finally:
            conn.close()
        if not convs:
            return "No sessions indexed yet. Run `session_sync` to index."
        lines = [f"### Recent Sessions ({offset + 1} - {offset + len(convs)})\n"]
        for idx, c in enumerate(convs, offset + 1):
            lines.append(
                f"{idx}. **{c['title'] or 'Untitled'}**\n"
                f"   - **ID:** `{c['conversation_id']}`\n"
                f"   - **Source:** {c.get('source', 'tome')}\n"
                f"   - **Total Steps:** {c['total_steps']}\n"
                f"   - **Timeline:** {c['created_at']} -> {c['updated_at']}\n"
            )
        return "\n".join(lines)

    async def handle_stats(
        params: dict[str, Any] | None = None,
        *args: Any,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        _ensure_fresh(state)
        conn = get_db_connection(state.db_path)
        try:
            stats = get_database_stats(conn, state.db_path)
        finally:
            conn.close()
        breakdown = (
            "\n".join(
                f"    - **{k}:** {v:,}"
                for k, v in stats.get("messages_by_type", {}).items()
            )
            or "    *(none)*"
        )
        return (
            "### Session Index Statistics\n\n"
            f"- **Total Conversations:** {stats['total_conversations']:,}\n"
            f"- **Total Messages:** {stats['total_messages']:,}\n"
            f"  - **Breakdown by Type:**\n{breakdown}\n"
            f"- **Date Range:** {stats['earliest_date'] or 'N/A'} -> "
            f"{stats['latest_date'] or 'N/A'}\n"
            f"- **Database Size:** {stats['db_size_mb']:.2f} MB "
            f"({stats['db_size_bytes']:,} bytes)\n"
            f"- **Database Path:** `{stats['db_path']}`\n"
            f"- **Tome Directory:** `{state.tome_dir}`\n"
            f"- **Brain Directory:** `{state.brain_dir}`\n"
        )

    async def handle_sync(
        params: dict[str, Any] | None = None,
        *args: Any,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        payload = _payload(params, arguments)
        with state.lock:
            res = sync_index(
                tome_dir=state.tome_dir,
                brain_dir=state.brain_dir,
                db_path=state.db_path,
                codecs=state.codecs,
                force_rescan=bool(payload.get("force", False)),
            )
            state.last_sync = time.time()
        return (
            f"### Index Sync Completed in {res['duration_seconds']}s\n\n"
            f"- **Sessions Discovered:** {res['discovered_transcripts']}\n"
            f"- **New Sessions Indexed:** {res['indexed_new']}\n"
            f"- **Updated Sessions:** {res['updated']}\n"
            f"- **Unchanged / Skipped:** {res['skipped']}\n"
            f"- **Total Messages Processed:** {res['total_messages']}\n"
            f"- **Tome Directory:** `{res['tome_dir']}`\n"
        )

    api.register_spell(
        SpellDefinition(
            name="session_search",
            description="Full-text BM25 search across indexed MvgeOS "
            "sessions (tomes, Pi sessions, Antigravity transcripts) with "
            "snippet highlights. Supports phrases, prefix* and AND/OR/NOT.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": 'Keywords, exact "phrase", or '
                        "booleans (python AND sqlite)",
                    },
                    "conversation_id": {
                        "type": "string",
                        "description": "Restrict to one session",
                    },
                    "type_filter": {
                        "type": "string",
                        "description": "Message type (USER, COMPACTION, ...)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max matches (default 10, max 50)",
                    },
                },
                "required": ["query"],
            },
            handler=handle_search,
            read_only=True,
        )
    )
    api.register_spell(
        SpellDefinition(
            name="session_get_step",
            description="Retrieve dialogue turns surrounding a step index "
            "found via session_search.",
            parameters={
                "type": "object",
                "properties": {
                    "conversation_id": {"type": "string"},
                    "step_index": {"type": "integer"},
                    "context_window": {
                        "type": "integer",
                        "description": "Turns before/after (default 2)",
                    },
                },
                "required": ["conversation_id", "step_index"],
            },
            handler=handle_get_step,
            read_only=True,
        )
    )
    api.register_spell(
        SpellDefinition(
            name="session_list",
            description="List recent indexed sessions by latest activity.",
            parameters={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer"},
                    "offset": {"type": "integer"},
                },
            },
            handler=handle_list,
            read_only=True,
        )
    )
    api.register_spell(
        SpellDefinition(
            name="session_stats",
            description="Index health metrics: session/message counts, "
            "type breakdown, date span, disk usage.",
            parameters={"type": "object", "properties": {}},
            handler=handle_stats,
            read_only=True,
        )
    )
    api.register_spell(
        SpellDefinition(
            name="session_sync",
            description="On-demand incremental re-index of session files.",
            parameters={
                "type": "object",
                "properties": {
                    "force": {
                        "type": "boolean",
                        "description": "Full re-index (default false)",
                    },
                },
            },
            handler=handle_sync,
        )
    )
