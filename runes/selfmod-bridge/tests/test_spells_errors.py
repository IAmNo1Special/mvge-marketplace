"""Error-path and edge-case coverage for selfmod-bridge spell handlers.

Companion to ``test_spells.py``: every failure branch with a pinned error
code gets a test asserting the exact code, the staleness fields on mutating
failures, and that partial filesystem state is cleaned up.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from pathlib import Path as _Path
from typing import Any

import pytest
from selfmod_bridge_conftest import FakeApi, make_rune, make_state

import mvge_runes_selfmod_bridge.rune as rune_mod
import mvge_runes_selfmod_bridge.spells as spells_mod
from mvge_runes_selfmod_bridge.rune import create_rune
from mvge_runes_selfmod_bridge.status import describe_extensions, list_snapshots
from mvge_runes_selfmod_bridge.validation import ValidationError, validate_extension_name


def _stale(result: dict[str, Any]) -> None:
    """Mutating failures carry the reload-staleness fields (spec §6)."""
    assert result["effective_after"] == "reload"
    assert isinstance(result["note"], str) and result["note"]


# -- revise_persona --------------------------------------------------------


@pytest.mark.asyncio
async def test_revise_persona_missing_old_text(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    result = await rune.revise_persona({"new_text": "x"})
    assert result == {
        "ok": False,
        "error": "missing_old_text",
        "message": "old_text is required — diffs live in params, not results",
        "effective_after": "reload",
        "note": result["note"],
    }
    _stale(result)


@pytest.mark.asyncio
async def test_revise_persona_missing_new_text(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    result = await rune.revise_persona({"old_text": "You are a test agent."})
    assert result["ok"] is False
    assert result["error"] == "missing_new_text"
    _stale(result)


@pytest.mark.asyncio
async def test_revise_persona_no_system_path(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    rune.state.config_dir = None
    rune.state.system_path = None
    result = await rune.revise_persona({"old_text": "a", "new_text": "b", "path": None})
    assert result["ok"] is False
    assert result["error"] == "no_system_path"
    _stale(result)


@pytest.mark.asyncio
async def test_revise_persona_snapshot_io_failure_structured(tmp_path: Path) -> None:
    """Snapshot-first ordering: if the snapshot itself cannot be written,
    the mutating op aborts BEFORE any persona write — the file is
    untouched — and the failure is a structured ``snapshot_failed`` result
    (spec §6.3 supplemental codes), not a raw OSError."""
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    assert rune.state.config_dir is not None
    assert rune.state.system_path is not None
    # Snapshots root is a FILE: root.mkdir(exist_ok=True) raises FileExistsError.
    (rune.state.config_dir / ".selfmod-snapshots").write_text("x")
    before = rune.state.system_path.read_text(encoding="utf-8")
    result = await rune.revise_persona(
        {"old_text": "You are a test agent.", "new_text": "You are changed."}
    )
    assert result["ok"] is False
    assert result["error"] == "snapshot_failed"
    assert "mkdir" in result["message"]
    _stale(result)
    assert rune.state.system_path.read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_self_snapshot_io_failure_no_stale_fields(tmp_path: Path) -> None:
    """self_snapshot is read_only=True: its snapshot_failed result carries
    no reload-staleness fields."""
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    assert rune.state.config_dir is not None
    (rune.state.config_dir / ".selfmod-snapshots").write_text("x")
    result = await rune.self_snapshot({"label": "boom"})
    assert result["ok"] is False
    assert result["error"] == "snapshot_failed"
    assert "effective_after" not in result
    assert "note" not in result


@pytest.mark.asyncio
async def test_snapshot_io_failure_removes_partial_dir(tmp_path: Path, monkeypatch: Any) -> None:
    """A copy-phase snapshot failure removes the half-written snapshot dir."""
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    assert rune.state.config_dir is not None
    root = rune.state.config_dir / ".selfmod-snapshots"

    def _boom(src: Any, dst: Any, *args: Any, **kwargs: Any) -> Any:
        raise OSError("copy exploded")

    monkeypatch.setattr(shutil, "copytree", _boom)
    result = await rune.self_snapshot({"label": "partial"})
    assert result["ok"] is False
    assert result["error"] == "snapshot_failed"
    assert "copy" in result["message"]
    assert [c for c in root.iterdir() if c.is_dir()] == []


# -- teach -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_teach_missing_section(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    result = await rune.teach({"content": "something"})
    assert result["ok"] is False
    assert result["error"] == "missing_section"
    _stale(result)


@pytest.mark.asyncio
async def test_teach_empty_paragraph_is_noop(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    result = await rune.teach({"section": "Notes", "mode": "append", "text": "   "})
    assert result["ok"] is True
    assert result["changed"] is False


@pytest.mark.asyncio
async def test_teach_appends_under_last_section(tmp_path: Path) -> None:
    """A paragraph for an existing section lands before the next header."""
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    target = rune.state.config_dir / "skills" / "notes.md"  # type: ignore[union-attr]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Notes\n\n## Alpha\n\nalpha text\n\n## Beta\n\nbeta text\n")
    first = await rune.teach(
        {"section": "Alpha", "mode": "append", "text": "more alpha", "path": str(target)}
    )
    assert first["ok"] is True and first["changed"] is True
    text = target.read_text(encoding="utf-8")
    assert text.index("more alpha") < text.index("## Beta")
    # Idempotent: teaching the same paragraph again is a no-op.
    second = await rune.teach(
        {"section": "Alpha", "mode": "append", "text": "more alpha", "path": str(target)}
    )
    assert second["ok"] is True and second["changed"] is False


# -- self_snapshot ---------------------------------------------------------


@pytest.mark.asyncio
async def test_self_snapshot_no_config_dir(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    rune.state.config_dir = None
    result = await rune.self_snapshot({})
    assert result["ok"] is False
    assert result["error"] == "no_config_dir"


@pytest.mark.asyncio
async def test_self_snapshot_same_microsecond_collision_suffixes(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A pre-existing same-stamp snapshot dir gets a uuid suffix, never reuse."""

    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    assert rune.state.config_dir is not None
    root = rune.state.config_dir / ".selfmod-snapshots"
    root.mkdir()

    real_datetime = spells_mod.datetime

    class FixedDatetime(real_datetime):
        @classmethod
        def now(cls, tz: Any = None) -> Any:
            return real_datetime(2026, 9, 21, 12, 0, 0, 123456, tzinfo=tz)

    monkeypatch.setattr(spells_mod, "datetime", FixedDatetime)
    first = await rune.self_snapshot({"label": "x"})
    assert first["ok"] is True
    second = await rune.self_snapshot({"label": "x"})
    assert second["ok"] is True
    assert second["snapshot_id"] != first["snapshot_id"]
    assert second["snapshot_id"].startswith(first["snapshot_id"] + "-")


# -- self_rollback ---------------------------------------------------------


@pytest.mark.asyncio
async def test_self_rollback_invalid_label_type(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    result = await rune.self_rollback({"snapshot_id": 123})
    assert result["ok"] is False
    assert result["error"] == "unknown_snapshot"


@pytest.mark.asyncio
async def test_self_rollback_missing_manifest_is_unknown(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    assert rune.state.config_dir is not None
    root = rune.state.config_dir / ".selfmod-snapshots"
    (root / "no-manifest").mkdir(parents=True)
    result = await rune.self_rollback({"snapshot_id": "no-manifest"})
    assert result["ok"] is False
    assert result["error"] == "unknown_snapshot"


@pytest.mark.asyncio
async def test_self_rollback_corrupt_manifest_is_unknown(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    assert rune.state.config_dir is not None
    root = rune.state.config_dir / ".selfmod-snapshots"
    bad = root / "corrupt"
    bad.mkdir(parents=True)
    (bad / "manifest.json").write_text("{not json", encoding="utf-8")
    result = await rune.self_rollback({"snapshot_id": "corrupt"})
    assert result["ok"] is False
    assert result["error"] == "unknown_snapshot"


@pytest.mark.asyncio
async def test_self_rollback_no_config_dir(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    rune.state.config_dir = None
    result = await rune.self_rollback({"snapshot_id": "whatever"})
    assert result["ok"] is False
    assert result["error"] == "unknown_snapshot"


# -- scaffold_rune / scaffold_skill failures --------------------------------


@pytest.mark.asyncio
async def test_scaffold_rune_exists_on_second_try(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    params = {"name": "dup_rune", "description": "dup"}
    first = await rune.scaffold_rune(params)
    assert first["ok"] is True
    second = await rune.scaffold_rune(params)
    assert second["ok"] is False
    assert second["error"] == "exists"
    assert second["path"] == first["path"]
    _stale(second)


@pytest.mark.asyncio
async def test_scaffold_rune_staging_failure_cleans_up(tmp_path: Path, monkeypatch: Any) -> None:
    """An OSError during the staging rename leaves no staging residue and a
    pinned ``scaffold_failed`` code (spec §6.3 result shape)."""

    rune, _api = make_rune(tmp_path)

    def _boom(src: Any, dst: Any) -> None:
        raise OSError("disk is on fire")

    monkeypatch.setattr(spells_mod.os, "replace", _boom)
    result = await rune.scaffold_rune({"name": "x_rune", "description": "x"})
    assert result["ok"] is False
    assert result["error"] == "scaffold_failed"
    _stale(result)
    assert not list(tmp_path.glob(".selfmod-staging-*"))


@pytest.mark.asyncio
async def test_scaffold_skill_exists_on_second_try(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    assert rune.state.config_dir is not None
    (rune.state.config_dir / "skills").mkdir()
    params = {"name": "dupskill", "description": "dup", "scope": "agent"}
    first = await rune.scaffold_skill(params)
    assert first["ok"] is True
    second = await rune.scaffold_skill(params)
    assert second["ok"] is False
    assert second["error"] == "exists"
    assert second["path"] == first["path"]
    _stale(second)


@pytest.mark.asyncio
async def test_scaffold_skill_project_scope_without_cwd(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    rune.state.cwd = None
    result = await rune.scaffold_skill({"name": "noskill", "description": "x", "scope": "project"})
    assert result["ok"] is False
    assert result["error"] == "no_skills_dir"
    _stale(result)


@pytest.mark.asyncio
async def test_scaffold_skill_agent_scope_without_config_dir(
    tmp_path: Path,
) -> None:
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    rune.state.config_dir = None
    result = await rune.scaffold_skill({"name": "noskill", "description": "x", "scope": "agent"})
    assert result["ok"] is False
    assert result["error"] == "no_skills_dir"
    _stale(result)


@pytest.mark.asyncio
async def test_scaffold_rune_no_runes_paths_lists_status_hint(
    tmp_path: Path,
) -> None:
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    rune.state.runes_paths = []
    result = await rune.scaffold_rune({"name": "x_rune", "description": "x"})
    assert result["ok"] is False
    assert result["error"] == "no_runes_paths"
    _stale(result)


# -- atomic-write cleanup ----------------------------------------------------


@pytest.mark.asyncio
async def test_atomic_write_failure_cleans_temp(tmp_path: Path) -> None:
    """os.replace onto a directory fails; the sibling temp file is removed."""
    rune, _api = make_rune(tmp_path)
    target_dir = tmp_path / "as-dir"
    target_dir.mkdir()
    with pytest.raises(OSError):
        rune._atomic_write_bytes(target_dir, b"data")  # type: ignore[attr-defined]
    assert not list(tmp_path.glob(".selfmod-tmp-*"))


# -- status / snapshots listing ----------------------------------------------


def test_list_snapshots_skips_unreadable_manifest(tmp_path: Path) -> None:

    state = make_state(tmp_path)
    assert state.config_dir is not None
    root = state.config_dir / ".selfmod-snapshots"
    good = root / "good"
    good.mkdir(parents=True)
    (good / "manifest.json").write_text(
        json.dumps({"created_at": "t", "label": "l", "files": []}), encoding="utf-8"
    )
    bad = root / "bad"
    bad.mkdir()
    (bad / "manifest.json").write_text("{corrupt", encoding="utf-8")
    (root / "not-a-dir.txt").write_text("x", encoding="utf-8")
    entries = list_snapshots(state)
    assert [e["id"] for e in entries] == ["good"]


def test_list_snapshots_no_config_dir(tmp_path: Path) -> None:

    state = make_state(tmp_path)
    state.config_dir = None
    assert list_snapshots(state) == []


def test_describe_extensions_warns_on_missing_system_file(
    tmp_path: Path,
) -> None:

    state = make_state(tmp_path)
    state.system_path = tmp_path / "ghost.md"
    status = describe_extensions(state)
    assert status["ok"] is True
    assert any("ghost.md" in w for w in status["warnings"])


@pytest.mark.asyncio
async def test_selfmod_status_command_lists_snapshots(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    snap = await rune.self_snapshot({"label": "cmd"})
    assert snap["ok"] is True
    out = await rune.handle_selfmod_command("status")
    assert snap["snapshot_id"] in out
    assert "snapshots (1):" in out


@pytest.mark.asyncio
async def test_selfmod_command_unknown_verb(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    out = await rune.handle_selfmod_command("frobnicate")
    assert out.startswith("usage: /selfmod")


# -- round 2: hook/command seams, scope precedence, wrap paths --------------


@pytest.mark.asyncio
async def test_hook_prompt_build_failure_emits_warning(tmp_path: Path, monkeypatch: Any) -> None:
    """Prompt assembly must never break session start: a build failure
    emits the warning event and leaves the payload prompt unchanged."""

    rune, api = make_rune(tmp_path)

    def _boom(runes_paths: Any, system_path: Any) -> str:
        raise RuntimeError("prompt exploded")

    monkeypatch.setattr(rune_mod, "build_selfmod_section", _boom)
    payload = {
        "base_prompt": "base prompt",
        "config_dir": str(tmp_path / "agent-config"),
        "runes_paths": [str(tmp_path / "runes")],
        "system_path": str(tmp_path / "agent-config" / "SYSTEM.md"),
    }
    await rune._on_before_mvge_start(payload)  # type: ignore[attr-defined]
    assert payload["base_prompt"] == "base prompt"
    assert ("selfmod_bridge_warning", {"stage": "prompt_build", "detail": "prompt exploded"}) in [
        (name, p) for name, p in api.events
    ] or any(
        name == "selfmod_bridge_warning" and p["stage"] == "prompt_build" for name, p in api.events
    )


@pytest.mark.asyncio
async def test_selfmod_status_command_shows_warnings(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    rune.state.runes_paths = [tmp_path / "ghost-runes"]
    out = await rune.handle_selfmod_command("status")
    assert "warning:" in out


def test_create_rune_explicit_manifest(tmp_path: Path) -> None:

    rune = create_rune(FakeApi(), {"version": "9.9.9"})
    assert rune.version == "9.9.9"


def test_validate_extension_name_rejects_dotted(tmp_path: Path) -> None:
    with pytest.raises(ValidationError) as exc_info:
        validate_extension_name("evil.py", tmp_path)
    assert exc_info.value.code == "invalid_name"


def test_default_rune_target_prefers_user_scope(tmp_path: Path, monkeypatch: Any) -> None:
    """Scope precedence agent > user > project: a user-scope runes dir
    (under $MVGE_GLOBAL_DIR) beats a project-scope one."""
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    global_dir = tmp_path / "global"
    user_runes = global_dir / "runes"
    user_runes.mkdir(parents=True)
    proj_runes = tmp_path / "proj" / "runes"
    proj_runes.mkdir(parents=True)
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(global_dir))
    rune.state.runes_paths = [proj_runes, user_runes]
    target = rune._default_rune_target(rune.state)  # type: ignore[attr-defined]
    assert target == user_runes


@pytest.mark.asyncio
async def test_revise_persona_explicit_missing_path_is_no_match(
    tmp_path: Path,
) -> None:
    rune, _api = make_rune(tmp_path)
    result = await rune.revise_persona(
        {
            "old_text": "a",
            "new_text": "b",
            "path": str(tmp_path / "ghost.md"),
        }
    )
    assert result["ok"] is False
    assert result["error"] == "no_match"
    _stale(result)


@pytest.mark.asyncio
async def test_revise_persona_snapshot_wrap_carries_staleness(
    tmp_path: Path,
) -> None:
    """A snapshot-mechanism failure dict (not exception) inside a mutating
    handler is re-wrapped with the reload-staleness fields."""
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    assert rune.state.system_path is not None
    system = rune.state.system_path
    rune.state.config_dir = None  # _snapshot_self -> no_config_dir dict
    result = await rune.revise_persona(
        {
            "old_text": "You are a test agent.",
            "new_text": "You are changed.",
            "path": str(system),
        }
    )
    assert result["ok"] is False
    assert result["error"] == "no_config_dir"
    _stale(result)


@pytest.mark.asyncio
async def test_teach_replace_missing_old_text(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    result = await rune.teach({"section": "S", "mode": "replace", "new_text": "x"})
    assert result["ok"] is False
    assert result["error"] == "missing_old_text"
    _stale(result)


@pytest.mark.asyncio
async def test_teach_snapshot_wrap_carries_staleness(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    rune.state.config_dir = None  # _snapshot_self -> no_config_dir dict
    result = await rune.teach({"section": "Notes", "mode": "append", "text": "hello"})
    assert result["ok"] is False
    assert result["error"] == "no_config_dir"
    _stale(result)


@pytest.mark.asyncio
async def test_self_snapshot_non_string_label(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    result = await rune.self_snapshot({"label": 123})
    assert result["ok"] is False
    assert result["error"] == "invalid_label"
    # Read-only spell: no reload-staleness fields.
    assert "effective_after" not in result


@pytest.mark.asyncio
async def test_self_rollback_restores_spells_dir(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    assert rune.state.spells_dir is not None
    spell_file = rune.state.spells_dir / "s1.py"
    spell_file.write_text("def s1(): ...\n", encoding="utf-8")
    snap = await rune.self_snapshot({"label": "spells"})
    assert snap["ok"] is True
    spell_file.unlink()
    result = await rune.self_rollback({"snapshot_id": snap["snapshot_id"]})
    assert result["ok"] is True
    assert spell_file.is_file()
    assert any("s1.py" in r for r in result["restored"])


@pytest.mark.asyncio
async def test_stage_replace_rechecks_under_lock(tmp_path: Path, monkeypatch: Any) -> None:
    """The verify-non-existence check runs twice: a fast pre-staging check
    and a re-check under the write lock. The re-check is the cross-process
    race guard (POSIX os.replace would replace an empty dir) — force the
    final to appear between the two checks."""

    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    root = rune.state.runes_paths[0]

    real_exists = _Path.exists
    calls = {"n": 0}

    def _flaky_exists(self: _Path) -> bool:
        if self.name != "race2_rune":
            return real_exists(self)
        calls["n"] += 1
        # First check (pre-staging): absent. Later checks (under lock):
        # the rival won the race -> the re-check must catch it.
        return calls["n"] > 1

    monkeypatch.setattr(_Path, "exists", _flaky_exists)
    result = await rune.scaffold_rune({"name": "race2_rune", "description": "x"})
    assert result["ok"] is False
    assert result["error"] == "exists"
    _stale(result)
    assert not real_exists(root / "race2_rune")
    assert not list(root.parent.glob(".selfmod-staging-*"))


def test_default_rune_target_agent_scope_wins(tmp_path: Path, monkeypatch: Any) -> None:
    """Full precedence: agent scope (under config_dir) beats user scope
    beats project scope."""
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    assert rune.state.config_dir is not None
    agent_runes = rune.state.config_dir / "runes"
    agent_runes.mkdir()
    global_dir = tmp_path / "global"
    user_runes = global_dir / "runes"
    user_runes.mkdir(parents=True)
    proj_runes = tmp_path / "proj" / "runes"
    proj_runes.mkdir(parents=True)
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(global_dir))
    rune.state.runes_paths = [proj_runes, user_runes, agent_runes]
    target = rune._default_rune_target(rune.state)  # type: ignore[attr-defined]
    assert target == agent_runes


@pytest.mark.asyncio
async def test_teach_no_system_path_no_config_dir(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    rune.state.system_path = None
    rune.state.config_dir = None
    result = await rune.teach({"section": "S", "mode": "append", "text": "x"})
    assert result["ok"] is False
    assert result["error"] == "no_system_path"
    _stale(result)


@pytest.mark.asyncio
async def test_self_rollback_skips_missing_system_source(
    tmp_path: Path,
) -> None:
    """A snapshot entry whose source file is gone is skipped, not fatal."""
    rune, _api = make_rune(tmp_path)
    snap = await rune.self_snapshot({"label": "partial"})
    assert snap["ok"] is True
    assert rune.state is not None
    assert rune.state.config_dir is not None
    snap_dir = rune.state.config_dir / ".selfmod-snapshots" / snap["snapshot_id"]
    (snap_dir / "SYSTEM.md").unlink()
    result = await rune.self_rollback({"snapshot_id": snap["snapshot_id"]})
    assert result["ok"] is True
    assert any("system:SYSTEM.md (no live target)" in s for s in result["skipped"])


@pytest.mark.asyncio
async def test_self_rollback_skips_when_no_spells_dir(
    tmp_path: Path,
) -> None:
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    assert rune.state.spells_dir is not None
    (rune.state.spells_dir / "s1.py").write_text("x\n", encoding="utf-8")
    snap = await rune.self_snapshot({"label": "spells-gone"})
    assert snap["ok"] is True
    rune.state.spells_dir = None
    result = await rune.self_rollback({"snapshot_id": snap["snapshot_id"]})
    assert result["ok"] is True
    assert any("spells:spells (no active spells dir)" in s for s in result["skipped"])


@pytest.mark.asyncio
async def test_scaffold_rune_no_writable_runes_paths(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    rune.state.runes_paths = [tmp_path / "ghost-runes"]
    result = await rune.scaffold_rune({"name": "x_rune", "description": "x"})
    assert result["ok"] is False
    assert result["error"] == "no_runes_paths"
    assert result["message"] == "none of the configured runes paths is writable"
    _stale(result)


@pytest.mark.asyncio
async def test_scaffold_rune_invalid_name(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    result = await rune.scaffold_rune({"name": "bad/name", "description": "x"})
    assert result["ok"] is False
    assert result["error"] == "invalid_name"
    _stale(result)


@pytest.mark.asyncio
async def test_scaffold_skill_invalid_name(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    assert rune.state.config_dir is not None
    (rune.state.config_dir / "skills").mkdir()
    result = await rune.scaffold_skill({"name": "bad..name", "description": "x", "scope": "agent"})
    assert result["ok"] is False
    assert result["error"] == "invalid_name"
    _stale(result)


@pytest.mark.asyncio
async def test_scaffold_skill_staging_failure_cleans_up(tmp_path: Path, monkeypatch: Any) -> None:

    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    assert rune.state.config_dir is not None
    (rune.state.config_dir / "skills").mkdir()

    def _boom(src: Any, dst: Any) -> None:
        raise OSError("disk is on fire")

    monkeypatch.setattr(spells_mod.os, "replace", _boom)
    result = await rune.scaffold_skill({"name": "xskill", "description": "x", "scope": "agent"})
    assert result["ok"] is False
    assert result["error"] == "scaffold_failed"
    _stale(result)


@pytest.mark.asyncio
async def test_self_rollback_pre_snapshot_wrap(tmp_path: Path, monkeypatch: Any) -> None:
    """The pre-rollback snapshot failure is re-wrapped with staleness fields
    (defensive: the only dict-failure is no_config_dir, which normally
    exits earlier)."""
    rune, _api = make_rune(tmp_path)
    snap = await rune.self_snapshot({"label": "wrap"})
    assert snap["ok"] is True

    async def _fail(label: str | None) -> dict[str, Any]:
        return {"ok": False, "error": "no_config_dir", "message": "boom"}

    monkeypatch.setattr(rune, "_snapshot_self", _fail)
    result = await rune.self_rollback({"snapshot_id": snap["snapshot_id"]})
    assert result["ok"] is False
    assert result["error"] == "no_config_dir"
    _stale(result)


@pytest.mark.asyncio
async def test_self_rollback_pre_rollback_io_failure_structured(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A pre-rollback snapshot I/O failure aborts the rollback fail-closed
    with a structured ``snapshot_failed`` result — nothing is restored."""
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    assert rune.state.system_path is not None
    snap = await rune.self_snapshot({"label": "good"})
    assert snap["ok"] is True
    before = rune.state.system_path.read_text(encoding="utf-8")

    def _boom(src: Any, dst: Any, *args: Any, **kwargs: Any) -> Any:
        raise OSError("copy exploded")

    monkeypatch.setattr(shutil, "copytree", _boom)
    result = await rune.self_rollback({"snapshot_id": snap["snapshot_id"]})
    assert result["ok"] is False
    assert result["error"] == "snapshot_failed"
    assert "copy" in result["message"]
    _stale(result)
    assert rune.state.system_path.read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_self_rollback_restores_nested_spells(tmp_path: Path) -> None:
    rune, _api = make_rune(tmp_path)
    assert rune.state is not None
    assert rune.state.spells_dir is not None
    nested = rune.state.spells_dir / "sub"
    nested.mkdir()
    (nested / "deep.py").write_text("x\n", encoding="utf-8")
    snap = await rune.self_snapshot({"label": "nested"})
    assert snap["ok"] is True
    (nested / "deep.py").unlink()
    result = await rune.self_rollback({"snapshot_id": snap["snapshot_id"]})
    assert result["ok"] is True
    assert (nested / "deep.py").is_file()


def test_describe_extensions_no_runes_paths_warning(tmp_path: Path) -> None:

    state = make_state(tmp_path)
    state.runes_paths = []
    status = describe_extensions(state)
    assert "no runes paths configured" in status["warnings"]


def test_atomic_write_cleanup_survives_unlink_failure(tmp_path: Path, monkeypatch: Any) -> None:
    """If the atomic replace fails AND the temp-file cleanup also fails, the
    original error still propagates (the OSError from unlink is swallowed)."""
    rune, _api = make_rune(tmp_path)

    def _fail_replace(src: Any, dst: Any) -> None:
        raise OSError("replace failed")

    def _fail_unlink(self: Path, missing_ok: bool = False) -> None:
        raise OSError("unlink failed")

    monkeypatch.setattr(os, "replace", _fail_replace)
    monkeypatch.setattr(Path, "unlink", _fail_unlink)
    with pytest.raises(OSError, match="replace failed"):
        rune._atomic_write_bytes(tmp_path / "f.txt", b"data")
