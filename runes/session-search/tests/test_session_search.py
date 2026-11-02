"""Tests for session-search rune: FTS5 search over Tome sessions.

TDD: written before the implementation. Covers the SQLite FTS5 engine, the
Tome v1 and Antigravity indexers, and the rune factory wiring.

The Pi codec test is NOT here. It imports ``mvge_runes_pi_codec``, a sibling
Rune, and it used to reach it through ``pytest.importorskip`` -- which does not
fail when the sibling is absent, it just stops running. That is a boundary
violation wearing a skip marker: nothing goes red, and the coverage quietly
disappears on any install that does not happen to carry pi-codec. Session-search
indexing a Pi session is a genuine two-Rune fact, so it lives at the one tier
allowed to make it, in ``tests/test_cross_rune_integration.py``, where it
asserts instead of skipping.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from mvge.runes.types import SigilHook
from mvge.tome.handle import TomeHandleFactory
from mvge.tome.types import TomeEntry, TomeEntryType
from mvge_runes_session_search.db import (
    get_database_stats,
    get_db_connection,
    get_step_context,
    init_db,
    list_recent_conversations,
    sanitize_fts5_query,
    search_messages,
)
from mvge_runes_session_search.indexer import (
    extract_text_content,
    get_brain_dir,
    get_db_path,
    get_tome_dir,
    sync_index,
)
from mvge_runes_session_search.rune import _extension_dirs, rune_factory


def _message(i: int, parent: str | None, role: str, **payload: Any) -> TomeEntry:
    return TomeEntry(
        id=f"e{i}",
        parent_id=parent,
        type=TomeEntryType.MESSAGE,
        timestamp=1700000000.0 + i,
        payload={"role": role, **payload},
    )


def _make_tome(tome_dir: Path, tome_id: str) -> None:
    factory = TomeHandleFactory(tome_dir)
    handle = factory.create_tome(cwd="/tmp/proj", tome_id=tome_id)
    handle.append(_message(0, None, "user", content="How do I fix sqlite?"))
    handle.append(_message(1, "e0", "assistant", content="Use FTS5 with BM25 ranking"))
    handle.append(
        _message(
            2,
            "e1",
            "spellResult",
            spell_name="bash",
            spell_cast_id="c1",
            content="fts5 works",
            is_error=False,
        )
    )


@pytest.fixture
def workspace(tmp_path: Path) -> dict[str, Path]:
    tome_dir = tmp_path / "sessions"
    tome_dir.mkdir()
    db_path = tmp_path / "search.db"
    brain_dir = tmp_path / "brain"
    return {"tome_dir": tome_dir, "db_path": db_path, "brain_dir": brain_dir}


def test_extension_dirs_follow_the_global_dir_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Extension discovery honours the relocated global layer.

    These were literal ``~/.agents`` paths, so an isolated run indexed
    extensions out of the developer's real home.
    """
    global_dir = tmp_path / "relocated_agents"
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(global_dir))
    (global_dir / "extensions" / "some-rune").mkdir(parents=True)
    (global_dir / "agents" / "my_agent" / "extensions").mkdir(parents=True)

    found = _extension_dirs("my_agent", "")

    assert global_dir / "extensions" in found
    assert global_dir / "agents" / "my_agent" / "extensions" in found


def test_sanitize_fts5_query() -> None:
    assert sanitize_fts5_query("hello world") == '"hello" "world"'
    assert sanitize_fts5_query('"exact phrase"') == '"exact phrase"'
    assert sanitize_fts5_query("sqlite AND fts5") == '"sqlite" AND "fts5"'
    assert sanitize_fts5_query("error OR bug NOT warn") == '"error" OR "bug" NOT "warn"'
    assert sanitize_fts5_query("prefix* search") == 'prefix* "search"'
    assert sanitize_fts5_query('unclosed "quote test') == '"unclosed" "quote" "test"'
    assert sanitize_fts5_query("   ") == ""


def test_extract_text_content_user_string() -> None:
    assert extract_text_content({"role": "user", "content": "hi"}) == (
        "hi",
        "",
        False,
    )


def test_extract_text_content_assistant_blocks() -> None:
    payload = {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "answer"},
            {"type": "contemplation", "thinking": "hmm"},
            {
                "type": "spell_cast",
                "spell": "bash",
                "args": {"cmd": "ls"},
            },
        ],
    }
    text, spells, thinking = extract_text_content(payload)
    assert text == "answer"
    assert spells == "bash"
    assert thinking is True


def test_sync_indexes_tome_sessions(workspace: dict[str, Path]) -> None:
    _make_tome(workspace["tome_dir"], "alpha")
    _make_tome(workspace["tome_dir"], "beta")

    stats = sync_index(
        tome_dir=workspace["tome_dir"],
        brain_dir=workspace["brain_dir"],
        db_path=workspace["db_path"],
    )
    assert stats["indexed_new"] == 2
    assert stats["skipped"] == 0

    conn = get_db_connection(workspace["db_path"])
    try:
        results = search_messages(conn, "BM25")
        assert len(results) == 2
        assert all(r["snippet"] for r in results)
        conv_ids = {r["conversation_id"] for r in results}
        assert conv_ids == {"alpha", "beta"}

        only_alpha = search_messages(conn, "BM25", conversation_id="alpha")
        assert [r["conversation_id"] for r in only_alpha] == ["alpha"]

        user_only = search_messages(conn, "sqlite", type_filter="user")
        assert all(r["type"] == "USER" for r in user_only)
    finally:
        conn.close()


def test_sync_is_incremental(workspace: dict[str, Path]) -> None:
    _make_tome(workspace["tome_dir"], "alpha")
    sync_index(
        tome_dir=workspace["tome_dir"],
        brain_dir=workspace["brain_dir"],
        db_path=workspace["db_path"],
    )
    second = sync_index(
        tome_dir=workspace["tome_dir"],
        brain_dir=workspace["brain_dir"],
        db_path=workspace["db_path"],
    )
    assert second["indexed_new"] == 0
    assert second["skipped"] == 1

    factory = TomeHandleFactory(workspace["tome_dir"])
    handle = factory.open_write("alpha")
    leaf = factory.get_leaf_id("alpha")
    entries = factory.get_entries_for_context("alpha", leaf_id=leaf)
    handle.append(_message(9, entries[-1].id, "user", content="follow up"))
    third = sync_index(
        tome_dir=workspace["tome_dir"],
        brain_dir=workspace["brain_dir"],
        db_path=workspace["db_path"],
    )
    assert third["updated"] == 1


def test_step_context_lists_window(workspace: dict[str, Path]) -> None:
    _make_tome(workspace["tome_dir"], "alpha")
    sync_index(
        tome_dir=workspace["tome_dir"],
        brain_dir=workspace["brain_dir"],
        db_path=workspace["db_path"],
    )
    conn = get_db_connection(workspace["db_path"])
    try:
        steps = get_step_context(conn, "alpha", 1, context_window=1)
        assert [s["step_index"] for s in steps] == [0, 1, 2]
        assert steps[1]["content"] == "Use FTS5 with BM25 ranking"
    finally:
        conn.close()


def test_list_and_stats(workspace: dict[str, Path]) -> None:
    _make_tome(workspace["tome_dir"], "alpha")
    sync_index(
        tome_dir=workspace["tome_dir"],
        brain_dir=workspace["brain_dir"],
        db_path=workspace["db_path"],
    )
    conn = get_db_connection(workspace["db_path"])
    try:
        recent = list_recent_conversations(conn)
        assert len(recent) == 1
        assert recent[0]["conversation_id"] == "alpha"
        assert recent[0]["total_steps"] == 3

        stats = get_database_stats(conn, workspace["db_path"])
        assert stats["total_conversations"] == 1
        assert stats["total_messages"] == 3
        assert stats["db_size_bytes"] > 0
    finally:
        conn.close()


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "search.db"
    conn = get_db_connection(db_path)
    try:
        init_db(conn)
        init_db(conn)
    finally:
        conn.close()


def test_antigravity_transcript_indexed(workspace: dict[str, Path]) -> None:
    log_dir = workspace["brain_dir"] / "conv1" / ".system_generated" / "logs"
    log_dir.mkdir(parents=True)
    record = {
        "step_index": 0,
        "source": "USER_EXPLICIT",
        "type": "USER_INPUT",
        "created_at": "2026-09-20T10:00:00Z",
        "content": "antigravity remembers sqlite too",
    }
    (log_dir / "transcript.jsonl").write_text(
        json.dumps(record) + "\n", encoding="utf-8"
    )
    stats = sync_index(
        tome_dir=workspace["tome_dir"],
        brain_dir=workspace["brain_dir"],
        db_path=workspace["db_path"],
    )
    assert stats["indexed_new"] == 1

    conn = get_db_connection(workspace["db_path"])
    try:
        results = search_messages(conn, "antigravity")
        assert len(results) == 1
        assert results[0]["source"] == "antigravity"
    finally:
        conn.close()


def test_missing_dirs_sync_empty(workspace: dict[str, Path]) -> None:
    stats = sync_index(
        tome_dir=workspace["tome_dir"],
        brain_dir=workspace["brain_dir"],
        db_path=workspace["db_path"],
    )
    assert stats["indexed_new"] == 0
    assert stats["discovered_transcripts"] == 0


def test_path_helpers_respect_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SESSION_SEARCH_TOME_DIR", str(tmp_path / "t"))
    monkeypatch.setenv("SESSION_SEARCH_DB_PATH", str(tmp_path / "s.db"))
    monkeypatch.setenv("ANTIGRAVITY_BRAIN_DIR", str(tmp_path / "b"))
    assert get_tome_dir() == (tmp_path / "t").resolve()
    assert get_db_path() == (tmp_path / "s.db").resolve()
    assert get_brain_dir() == (tmp_path / "b").resolve()


class MockRuneAPI:
    def __init__(self, context: Any) -> None:
        self.context = context
        self.hooks: dict[SigilHook, list] = {}
        self.spells: dict[str, Any] = {}
        self.commands: dict[str, dict] = {}

    def on(self, hook: SigilHook, handler: Any) -> None:
        self.hooks.setdefault(hook, []).append(handler)

    def register_spell(self, spell: Any) -> None:
        self.spells[spell.name] = spell

    def register_command(
        self, name: str, description: str = "", handler: Any = None
    ) -> None:
        self.commands[name] = {"description": description, "handler": handler}


def _context(tmp_path: Path) -> Any:
    ctx = MagicMock()
    ctx.cwd = str(tmp_path)
    ctx.agent_name = "test-agent"
    ctx.tome_dir = str(tmp_path / "sessions")
    return ctx


@pytest.mark.asyncio
async def test_rune_factory_registers_spells(tmp_path: Path) -> None:
    (tmp_path / "sessions").mkdir()
    api = MockRuneAPI(_context(tmp_path))
    rune_factory(api)
    assert set(api.spells) == {
        "session_search",
        "session_get_step",
        "session_list",
        "session_stats",
        "session_sync",
    }
    assert SigilHook.SESSION_START in api.hooks
    assert SigilHook.SESSION_SHUTDOWN in api.hooks


@pytest.mark.asyncio
async def test_search_spell_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tome_dir = tmp_path / "sessions"
    tome_dir.mkdir()
    _make_tome(tome_dir, "alpha")
    monkeypatch.setenv("SESSION_SEARCH_DB_PATH", str(tmp_path / "s.db"))
    monkeypatch.setenv("SESSION_SEARCH_TOME_DIR", str(tome_dir))
    monkeypatch.setenv("ANTIGRAVITY_BRAIN_DIR", str(tmp_path / "brain"))

    api = MockRuneAPI(_context(tmp_path))
    rune_factory(api)
    handler = api.spells["session_search"]._handler
    out = await handler(params={"query": "BM25"})
    assert "alpha" in out
    assert "Step #1" in out

    step_handler = api.spells["session_get_step"]._handler
    step_out = await step_handler(params={"conversation_id": "alpha", "step_index": 1})
    assert "BM25" in step_out

    stats_handler = api.spells["session_stats"]._handler
    stats_out = await stats_handler(params={})
    assert "Total Conversations" in stats_out

    list_handler = api.spells["session_list"]._handler
    list_out = await list_handler(params={})
    assert "alpha" in list_out

    sync_handler = api.spells["session_sync"]._handler
    sync_out = await sync_handler(params={})
    assert "Sync Completed" in sync_out
