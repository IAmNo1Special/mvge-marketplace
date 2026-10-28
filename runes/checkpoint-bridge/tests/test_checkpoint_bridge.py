from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from mvge.core.spells import SpellContext
from mvge.runes.rune_api import RuneAPI
from mvge.runes.types import SessionLeafChangedData, SigilHook
from mvge_runes_checkpoint_bridge.rune import rune_factory


@pytest.fixture
def mock_api() -> Any:
    api = MagicMock(spec=RuneAPI)
    registered_spells: dict[str, Any] = {}
    sigil_handlers: dict[SigilHook, list[Any]] = {}

    def register_spell(spell_def: Any) -> None:
        registered_spells[spell_def.name] = spell_def

    def on(hook: SigilHook, handler: Any, priority: int = 0) -> None:
        sigil_handlers.setdefault(hook, []).append(handler)

    api.register_spell = register_spell
    api.on = on
    api._registered = registered_spells
    api._sigil_handlers = sigil_handlers
    api.on_dispose = MagicMock()
    return api


@pytest.mark.asyncio
async def test_checkpoint_bridge_registers_spells_and_hooks(mock_api: Any) -> None:
    rune_factory(mock_api)
    registered = mock_api._registered
    assert "checkpoint_diff" in registered
    assert "checkpoint_restore_file" in registered

    handlers = mock_api._sigil_handlers
    assert SigilHook.TURN_START in handlers
    assert SigilHook.TURN_END in handlers
    assert SigilHook.SESSION_LEAF_CHANGED in handlers


@pytest.mark.asyncio
async def test_checkpoint_shadow_git_snapshot_and_diff(
    tmp_path: Path, mock_api: Any
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_a = workspace / "test.txt"
    file_a.write_text("initial content\n")

    # Initialize a dummy git repo so alternates can be tested
    proc = await asyncio.create_subprocess_exec(
        "git", "init", cwd=str(workspace), stdout=asyncio.subprocess.PIPE
    )
    await proc.communicate()

    checkpoints_dir = tmp_path / "checkpoints"
    checkpoints_dir.mkdir()

    bridge = rune_factory(
        mock_api,
        checkpoints_base_dir=checkpoints_dir,
    )
    diff_spell = mock_api._registered["checkpoint_diff"]

    ctx = SpellContext(
        spell_cast_id="cast-1",
        workspace_root=workspace,
        tome_id="tome-test",
    )

    # Simulate turn start with leaf 1
    await bridge.capture("leaf-1", workspace=workspace)

    # Modify file
    file_a.write_text("modified content\n")

    # Run checkpoint_diff
    res = await diff_spell.execute(
        spell_cast_id="cast-2",
        params={},
        ctx=ctx,
    )

    assert res["success"] is True
    assert "test.txt" in res["files"]
    assert "-initial content" in res["diff"]
    assert "+modified content" in res["diff"]


@pytest.mark.asyncio
async def test_checkpoint_restore_file(tmp_path: Path, mock_api: Any) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_a = workspace / "config.json"
    file_a.write_text('{"version": 1}\n')

    checkpoints_dir = tmp_path / "checkpoints"
    checkpoints_dir.mkdir()

    bridge = rune_factory(mock_api, checkpoints_base_dir=checkpoints_dir)
    restore_spell = mock_api._registered["checkpoint_restore_file"]

    ctx = SpellContext(
        spell_cast_id="cast-10",
        workspace_root=workspace,
        tome_id="tome-test",
    )

    await bridge.capture("leaf-1", workspace=workspace)

    # Mutate file
    file_a.write_text('{"version": 999}\n')
    assert "999" in file_a.read_text()

    # Restore file
    res = await restore_spell.execute(
        spell_cast_id="cast-11",
        params={"path": "config.json"},
        ctx=ctx,
    )

    assert res["success"] is True
    assert '{"version": 1}' in file_a.read_text()


@pytest.mark.asyncio
async def test_checkpoint_session_leaf_changed_restoration(
    tmp_path: Path, mock_api: Any
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_a = workspace / "main.py"
    file_a.write_text("print('v1')\n")

    checkpoints_dir = tmp_path / "checkpoints"
    checkpoints_dir.mkdir()

    bridge = rune_factory(mock_api, checkpoints_base_dir=checkpoints_dir)

    # Snapshot leaf 1
    await bridge.capture("leaf-1", workspace=workspace)

    # Create new file in leaf 2
    file_b = workspace / "new_file.py"
    file_b.write_text("created in leaf 2\n")
    file_a.write_text("print('v2')\n")
    await bridge.capture("leaf-2", workspace=workspace)

    assert file_b.exists()
    assert "v2" in file_a.read_text()

    # Fire SESSION_LEAF_CHANGED to revert to leaf 1
    leaf_changed_data = SessionLeafChangedData(
        tome_id="tome-test",
        old_leaf_id="leaf-2",
        new_leaf_id="leaf-1",
        reason="undo",
        workspace_root=str(workspace),
    )
    await bridge.on_session_leaf_changed(leaf_changed_data)

    # Verify file_a reverted to v1 and file_b created in leaf 2 was removed
    assert file_a.read_text() == "print('v1')\n"
    assert not file_b.exists()


def _make_workspace(tmp_path: Path, checkpoints_dir: Path, mock_api: Any) -> Any:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    checkpoints_dir.mkdir(exist_ok=True)
    bridge = rune_factory(mock_api, checkpoints_base_dir=checkpoints_dir)
    return workspace, bridge


def _ctx(workspace: Path, cast_id: str = "cast-x") -> SpellContext:
    return SpellContext(
        spell_cast_id=cast_id,
        workspace_root=workspace,
        tome_id="tome-test",
    )


@pytest.mark.asyncio
async def test_checkpoint_diff_with_no_checkpoints(
    tmp_path: Path, mock_api: Any
) -> None:
    workspace, _bridge = _make_workspace(tmp_path, tmp_path / "checkpoints", mock_api)
    diff_spell = mock_api._registered["checkpoint_diff"]
    res = await diff_spell.execute(
        spell_cast_id="cast-20", params={}, ctx=_ctx(workspace)
    )
    assert res["success"] is True
    assert "No checkpoints recorded" in res["diff"]
    assert res["files"] == []


@pytest.mark.asyncio
async def test_checkpoint_diff_unknown_leaf(tmp_path: Path, mock_api: Any) -> None:
    workspace, bridge = _make_workspace(tmp_path, tmp_path / "checkpoints", mock_api)
    (workspace / "a.txt").write_text("v1\n")
    await bridge.capture("leaf-1", workspace=workspace)
    diff_spell = mock_api._registered["checkpoint_diff"]
    res = await diff_spell.execute(
        spell_cast_id="cast-21",
        params={"leaf_id": "no-such-leaf"},
        ctx=_ctx(workspace),
    )
    assert res["success"] is True
    assert "Checkpoint tree not found" in res["diff"]
    assert res["files"] == []


@pytest.mark.asyncio
async def test_checkpoint_diff_between_two_leaves(
    tmp_path: Path, mock_api: Any
) -> None:
    workspace, bridge = _make_workspace(tmp_path, tmp_path / "checkpoints", mock_api)
    target = workspace / "a.txt"
    target.write_text("v1\n")
    await bridge.capture("leaf-1", workspace=workspace)
    target.write_text("v2\n")
    await bridge.capture("leaf-2", workspace=workspace)
    target.write_text("v3\n")

    diff_spell = mock_api._registered["checkpoint_diff"]
    res = await diff_spell.execute(
        spell_cast_id="cast-22",
        params={"leaf_id": "leaf-2", "from_leaf_id": "leaf-1"},
        ctx=_ctx(workspace),
    )
    assert res["success"] is True
    assert "a.txt" in res["files"]
    assert "-v1" in res["diff"]
    assert "+v2" in res["diff"]
    assert "v3" not in res["diff"]


@pytest.mark.asyncio
async def test_checkpoint_restore_specific_leaf(tmp_path: Path, mock_api: Any) -> None:
    workspace, bridge = _make_workspace(tmp_path, tmp_path / "checkpoints", mock_api)
    target = workspace / "a.txt"
    target.write_text("v1\n")
    await bridge.capture("leaf-1", workspace=workspace)
    target.write_text("v2\n")
    await bridge.capture("leaf-2", workspace=workspace)
    target.write_text("v3\n")

    restore_spell = mock_api._registered["checkpoint_restore_file"]
    res = await restore_spell.execute(
        spell_cast_id="cast-23",
        params={"path": "a.txt", "leaf_id": "leaf-1"},
        ctx=_ctx(workspace),
    )
    assert res["success"] is True
    assert target.read_text() == "v1\n"


@pytest.mark.asyncio
async def test_checkpoint_restore_missing_path_param(
    tmp_path: Path, mock_api: Any
) -> None:
    workspace, _bridge = _make_workspace(tmp_path, tmp_path / "checkpoints", mock_api)
    restore_spell = mock_api._registered["checkpoint_restore_file"]
    res = await restore_spell.execute(
        spell_cast_id="cast-24", params={}, ctx=_ctx(workspace)
    )
    assert res["success"] is False
    assert res["error"] == "path parameter required"


@pytest.mark.asyncio
async def test_checkpoint_restore_with_no_checkpoints(
    tmp_path: Path, mock_api: Any
) -> None:
    workspace, _bridge = _make_workspace(tmp_path, tmp_path / "checkpoints", mock_api)
    (workspace / "a.txt").write_text("content\n")
    restore_spell = mock_api._registered["checkpoint_restore_file"]
    res = await restore_spell.execute(
        spell_cast_id="cast-25",
        params={"path": "a.txt"},
        ctx=_ctx(workspace),
    )
    assert res["success"] is False


@pytest.mark.asyncio
async def test_checkpoint_restore_file_not_in_checkpoint(
    tmp_path: Path, mock_api: Any
) -> None:
    workspace, bridge = _make_workspace(tmp_path, tmp_path / "checkpoints", mock_api)
    (workspace / "a.txt").write_text("v1\n")
    await bridge.capture("leaf-1", workspace=workspace)
    restore_spell = mock_api._registered["checkpoint_restore_file"]
    res = await restore_spell.execute(
        spell_cast_id="cast-26",
        params={"path": "never-existed.txt"},
        ctx=_ctx(workspace),
    )
    assert res["success"] is False
    assert not (workspace / "never-existed.txt").exists()


@pytest.mark.asyncio
async def test_checkpoint_leaf_changed_unknown_leaf_is_noop(
    tmp_path: Path, mock_api: Any
) -> None:
    workspace, bridge = _make_workspace(tmp_path, tmp_path / "checkpoints", mock_api)
    target = workspace / "a.txt"
    target.write_text("v1\n")
    await bridge.capture("leaf-1", workspace=workspace)
    target.write_text("v2\n")
    await bridge.on_session_leaf_changed(
        SessionLeafChangedData(
            tome_id="tome-test",
            old_leaf_id="leaf-1",
            new_leaf_id="no-such-leaf",
            reason="undo",
            workspace_root=str(workspace),
        )
    )
    assert target.read_text() == "v2\n"


@pytest.mark.asyncio
async def test_checkpoint_leaf_changed_without_git_dir_is_noop(
    tmp_path: Path, mock_api: Any
) -> None:
    workspace, bridge = _make_workspace(tmp_path, tmp_path / "checkpoints", mock_api)
    target = workspace / "a.txt"
    target.write_text("v1\n")
    await bridge.on_session_leaf_changed(
        SessionLeafChangedData(
            tome_id="tome-test",
            old_leaf_id=None,
            new_leaf_id="leaf-1",
            reason="undo",
            workspace_root=str(workspace),
        )
    )
    assert target.read_text() == "v1\n"


@pytest.mark.asyncio
async def test_checkpoint_leaf_changed_dict_payload_via_sigil_handler(
    tmp_path: Path, mock_api: Any
) -> None:
    workspace, bridge = _make_workspace(tmp_path, tmp_path / "checkpoints", mock_api)
    target = workspace / "a.txt"
    target.write_text("v1\n")
    await bridge.capture("leaf-1", workspace=workspace)
    target.write_text("v2\n")
    handler = mock_api._sigil_handlers[SigilHook.SESSION_LEAF_CHANGED][0]
    await handler(
        {
            "tome_id": "tome-test",
            "old_leaf_id": "leaf-1",
            "new_leaf_id": "leaf-1",
            "reason": "undo",
            "workspace_root": str(workspace),
        }
    )
    assert target.read_text() == "v1\n"


@pytest.mark.asyncio
async def test_checkpoint_turn_start_handler_captures(
    tmp_path: Path, monkeypatch: Any, mock_api: Any
) -> None:
    workspace, _bridge = _make_workspace(tmp_path, tmp_path / "checkpoints", mock_api)
    target = workspace / "a.txt"
    target.write_text("v1\n")
    monkeypatch.chdir(workspace)
    handler = mock_api._sigil_handlers[SigilHook.TURN_START][0]
    await handler(None)
    target.write_text("v2\n")
    diff_spell = mock_api._registered["checkpoint_diff"]
    res = await diff_spell.execute(
        spell_cast_id="cast-27", params={}, ctx=_ctx(workspace)
    )
    assert res["success"] is True
    assert "a.txt" in res["files"]
    assert "+v2" in res["diff"]


@pytest.mark.asyncio
async def test_checkpoint_capture_returns_sha_and_recapture_updates(
    tmp_path: Path, mock_api: Any
) -> None:
    workspace, bridge = _make_workspace(tmp_path, tmp_path / "checkpoints", mock_api)
    target = workspace / "a.txt"
    target.write_text("v1\n")
    sha1 = await bridge.capture("leaf-1", workspace=workspace)
    assert sha1
    target.write_text("v2\n")
    sha2 = await bridge.capture("leaf-1", workspace=workspace)
    assert sha2 and sha2 != sha1


@pytest.mark.asyncio
async def test_checkpoint_capture_excludes_agents_dir(
    tmp_path: Path, mock_api: Any
) -> None:
    workspace, bridge = _make_workspace(tmp_path, tmp_path / "checkpoints", mock_api)
    (workspace / "a.txt").write_text("v1\n")
    agents_file = workspace / ".agents" / "secret.txt"
    agents_file.parent.mkdir()
    agents_file.write_text("secret v1\n")
    await bridge.capture("leaf-1", workspace=workspace)
    (workspace / "a.txt").write_text("v2\n")
    agents_file.write_text("secret v2\n")
    diff_spell = mock_api._registered["checkpoint_diff"]
    res = await diff_spell.execute(
        spell_cast_id="cast-28", params={}, ctx=_ctx(workspace)
    )
    assert "a.txt" in res["files"]
    assert ".agents/secret.txt" not in res["files"]


@pytest.mark.asyncio
async def test_checkpoint_command_registered(tmp_path: Path, mock_api: Any) -> None:
    commands: dict[str, Any] = {}

    def record_command(name: str, description: str, handler: Any) -> None:
        commands[name] = handler

    mock_api.register_command = record_command
    _bridge = rune_factory(mock_api, checkpoints_base_dir=tmp_path / "checkpoints")
    assert "checkpoint" in commands
    assert commands["checkpoint"]("hello") == "Checkpoint inspected: hello"
