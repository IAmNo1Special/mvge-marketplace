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
