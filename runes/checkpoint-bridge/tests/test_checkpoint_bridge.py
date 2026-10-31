from __future__ import annotations

import asyncio
import subprocess
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


# --- Bounded capture (sigil-timeout fix): failing tests first ---
import mvge_runes_checkpoint_bridge.rune as bridge_module


@pytest.mark.asyncio
async def test_capture_skips_worktree_over_file_bound(
    tmp_path: Path, monkeypatch: Any, mock_api: Any
) -> None:
    workspace, bridge = _make_workspace(tmp_path, tmp_path / "checkpoints", mock_api)
    for i in range(5):
        (workspace / f"f{i}.txt").write_text("x\n")
    monkeypatch.setattr(bridge_module, "MAX_CAPTURE_FILES", 3)
    sha = await bridge.capture("leaf-big", workspace=workspace)
    assert sha == ""
    assert not bridge._git_dir_for(workspace).exists()


@pytest.mark.asyncio
async def test_capture_skips_worktree_over_byte_bound(
    tmp_path: Path, monkeypatch: Any, mock_api: Any
) -> None:
    workspace, bridge = _make_workspace(tmp_path, tmp_path / "checkpoints", mock_api)
    (workspace / "big.bin").write_text("x" * 100)
    monkeypatch.setattr(bridge_module, "MAX_CAPTURE_BYTES", 10)
    sha = await bridge.capture("leaf-big", workspace=workspace)
    assert sha == ""
    assert not bridge._git_dir_for(workspace).exists()


@pytest.mark.asyncio
async def test_capture_guard_ignores_agents_and_git_dirs(
    tmp_path: Path, monkeypatch: Any, mock_api: Any
) -> None:
    workspace, bridge = _make_workspace(tmp_path, tmp_path / "checkpoints", mock_api)
    (workspace / "a.txt").write_text("v1\n")
    agents_dir = workspace / ".agents"
    agents_dir.mkdir()
    for i in range(10):
        (agents_dir / f"noise{i}.txt").write_text("noise\n")
    monkeypatch.setattr(bridge_module, "MAX_CAPTURE_FILES", 3)
    sha = await bridge.capture("leaf-1", workspace=workspace)
    assert sha


@pytest.mark.asyncio
async def test_capture_resumes_after_worktree_shrinks(
    tmp_path: Path, monkeypatch: Any, mock_api: Any
) -> None:
    workspace, bridge = _make_workspace(tmp_path, tmp_path / "checkpoints", mock_api)
    for i in range(5):
        (workspace / f"f{i}.txt").write_text("x\n")
    monkeypatch.setattr(bridge_module, "MAX_CAPTURE_FILES", 3)
    assert await bridge.capture("leaf-big", workspace=workspace) == ""
    for i in range(2, 5):
        (workspace / f"f{i}.txt").unlink()
    monkeypatch.setattr(bridge_module, "MAX_CAPTURE_FILES", 3)
    assert await bridge.capture("leaf-1", workspace=workspace)


@pytest.mark.asyncio
async def test_capture_guard_does_not_follow_symlinked_dirs(
    tmp_path: Path, mock_api: Any
) -> None:
    workspace, bridge = _make_workspace(tmp_path, tmp_path / "checkpoints", mock_api)
    (workspace / "a.txt").write_text("v1\n")
    (workspace / "loop").symlink_to(workspace, target_is_directory=True)
    sha = await asyncio.wait_for(
        bridge.capture("leaf-1", workspace=workspace), timeout=30
    )
    assert sha


class _FakeProc:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False
        self.waited = False

    async def communicate(self, input: Any = None) -> tuple[bytes, bytes]:
        await asyncio.sleep(30)
        return b"", b""

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        self.waited = True
        return -9


@pytest.mark.asyncio
async def test_run_git_kills_subprocess_on_cancellation(
    tmp_path: Path, monkeypatch: Any, mock_api: Any
) -> None:
    workspace, bridge = _make_workspace(tmp_path, tmp_path / "checkpoints", mock_api)
    fake = _FakeProc()

    async def fake_create(*args: Any, **kwargs: Any) -> _FakeProc:
        return fake

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            bridge._run_git(bridge._git_dir_for(workspace), workspace, ["add", "-A"]),
            timeout=0.2,
        )
    assert fake.killed
    assert fake.waited


@pytest.mark.asyncio
async def test_capture_removes_temp_index_lock_on_cancellation(
    tmp_path: Path, monkeypatch: Any, mock_api: Any
) -> None:
    workspace, bridge = _make_workspace(tmp_path, tmp_path / "checkpoints", mock_api)
    (workspace / "a.txt").write_text("v1\n")
    seen: dict[str, str] = {}

    async def fake_run_git(
        git_dir: Path,
        ws: Path,
        args: list[str],
        env: dict[str, str] | None = None,
        stdin_data: bytes | None = None,
    ) -> tuple[int, str, str]:
        if env and "GIT_INDEX_FILE" in env:
            seen["index"] = env["GIT_INDEX_FILE"]
            Path(env["GIT_INDEX_FILE"] + ".lock").write_text("")
            raise asyncio.CancelledError()
        return 0, "", ""

    monkeypatch.setattr(bridge, "_run_git", fake_run_git)
    with pytest.raises(asyncio.CancelledError):
        await bridge.capture("leaf-1", workspace=workspace)
    assert seen["index"]
    assert not Path(seen["index"]).exists()
    assert not Path(seen["index"] + ".lock").exists()


# --- gitignore-respecting pre-scan (follow-up item 2): failing tests first ---


def test_scan_worktree_respects_gitignore(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".gitignore").write_text("*.log\nbuild/\n")
    (workspace / "keep.txt").write_text("x\n")
    (workspace / "debug.log").write_text("x\n")
    build_dir = workspace / "build"
    build_dir.mkdir()
    (build_dir / "out.o").write_text("x\n")
    count, _bytes, over = bridge_module._scan_worktree(workspace)
    # .gitignore itself + keep.txt are counted; ignored files are not.
    assert count == 2
    assert over is False


def test_scan_worktree_respects_nested_gitignore_and_negation(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    sub = workspace / "sub"
    sub.mkdir()
    (sub / ".gitignore").write_text("*.tmp\n!keep.tmp\n")
    (sub / "a.tmp").write_text("x\n")
    (sub / "keep.tmp").write_text("x\n")
    (sub / "b.txt").write_text("x\n")
    (workspace / "top.tmp").write_text("x\n")
    count, _bytes, over = bridge_module._scan_worktree(workspace)
    # sub/.gitignore + keep.tmp + b.txt + top.tmp (root has no rule for *.tmp)
    assert count == 4
    assert over is False


def test_scan_worktree_anchored_pattern_only_matches_root(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".gitignore").write_text("/dist\n")
    root_dist = workspace / "dist"
    root_dist.mkdir()
    (root_dist / "a.txt").write_text("x\n")
    sub_dist = workspace / "sub" / "dist"
    sub_dist.mkdir(parents=True)
    (sub_dist / "b.txt").write_text("x\n")
    count, _bytes, over = bridge_module._scan_worktree(workspace)
    # .gitignore + sub/dist/b.txt; root dist/ is ignored.
    assert count == 2
    assert over is False


def test_scan_worktree_does_not_consult_workspace_git_info_exclude(
    tmp_path: Path,
) -> None:
    # Shadow `git add` reads $GIT_DIR/info/exclude of the shadow repo,
    # never the workspace's .git/info/exclude (verified against git):
    # the pre-scan must count what the shadow add stages, so a *.bak
    # listed only in the workspace's exclude file still counts here.
    workspace = tmp_path / "ws"
    workspace.mkdir()
    exclude = workspace / ".git" / "info"
    exclude.mkdir(parents=True)
    (exclude / "exclude").write_text("*.bak\n")
    (workspace / "keep.txt").write_text("x\n")
    (workspace / "old.bak").write_text("x\n")
    count, _bytes, over = bridge_module._scan_worktree(workspace)
    # .git itself is excluded from counting; keep.txt + old.bak count.
    assert count == 2
    assert over is False


def test_scan_worktree_ignored_bytes_do_not_count_toward_byte_bound(
    tmp_path: Path, monkeypatch: Any
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".gitignore").write_text("*.bin\n")
    (workspace / "big.bin").write_text("x" * 100)
    (workspace / "small.txt").write_text("x\n")
    monkeypatch.setattr(bridge_module, "MAX_CAPTURE_BYTES", 10)
    _count, byte_count, over = bridge_module._scan_worktree(workspace)
    assert byte_count <= 10
    assert over is False


@pytest.mark.asyncio
async def test_capture_captures_tracked_despite_ignored_over_bound(
    tmp_path: Path, monkeypatch: Any, mock_api: Any
) -> None:
    workspace, bridge = _make_workspace(tmp_path, tmp_path / "checkpoints", mock_api)
    (workspace / ".gitignore").write_text("*.log\nnoise/\n")
    for i in range(10):
        (workspace / f"n{i}.log").write_text("noise\n")
    noise_dir = workspace / "noise"
    noise_dir.mkdir()
    for i in range(10):
        (noise_dir / f"n{i}.txt").write_text("noise\n")
    (workspace / "a.txt").write_text("v1\n")
    (workspace / "b.txt").write_text("v1\n")
    monkeypatch.setattr(bridge_module, "MAX_CAPTURE_FILES", 3)
    sha = await bridge.capture("leaf-1", workspace=workspace)
    assert sha
    (workspace / "a.txt").write_text("v2\n")
    diff_spell = mock_api._registered["checkpoint_diff"]
    res = await diff_spell.execute(
        spell_cast_id="cast-gi1", params={}, ctx=_ctx(workspace)
    )
    assert "a.txt" in res["files"]
    assert not any(name.endswith(".log") for name in res["files"])
    assert not any(name.startswith("noise/") for name in res["files"])


@pytest.mark.asyncio
async def test_capture_still_skips_when_tracked_files_over_bound(
    tmp_path: Path, monkeypatch: Any, mock_api: Any
) -> None:
    workspace, bridge = _make_workspace(tmp_path, tmp_path / "checkpoints", mock_api)
    (workspace / ".gitignore").write_text("*.log\n")
    for i in range(5):
        (workspace / f"t{i}.txt").write_text("x\n")
    (workspace / "ignored.log").write_text("x\n")
    monkeypatch.setattr(bridge_module, "MAX_CAPTURE_FILES", 3)
    sha = await bridge.capture("leaf-big", workspace=workspace)
    assert sha == ""
    assert not bridge._git_dir_for(workspace).exists()


def test_scan_worktree_gitignore_pattern_edges(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".gitignore").write_text(
        "# a comment\n"
        "\n"
        "foo/**\n"
        "a**b\n"
        "[abc\n"
        "\\#hash.txt\n"
        "\\!bang.txt\n"
        "[!x].log\n"
        "**/cache\n"
        "?.md\n"
        "my\\ file.txt\n"
    )
    foo = workspace / "foo"
    foo.mkdir()
    (foo / "bar.txt").write_text("x\n")
    (foo / "deep").mkdir()
    (foo / "deep" / "baz.txt").write_text("x\n")
    (workspace / "axxb").write_text("x\n")
    (workspace / "axxb.txt").write_text("x\n")
    (workspace / "[abc").write_text("x\n")
    (workspace / "#hash.txt").write_text("x\n")
    (workspace / "!bang.txt").write_text("x\n")
    (workspace / "y.log").write_text("x\n")
    (workspace / "x.log").write_text("x\n")
    (workspace / "keep.txt").write_text("x\n")
    (workspace / "cache").mkdir()
    (workspace / "cache" / "g.txt").write_text("x\n")
    deep_cache = workspace / "deep" / "cache"
    deep_cache.mkdir(parents=True)
    (deep_cache / "h.txt").write_text("x\n")
    (workspace / "a.md").write_text("x\n")
    (workspace / "ab.md").write_text("x\n")
    (workspace / "my file.txt").write_text("x\n")
    count, _bytes, over = bridge_module._scan_worktree(workspace)
    # Counted: .gitignore, axxb.txt, x.log, keep.txt, ab.md.
    assert count == 5
    assert over is False


# --- global excludes (core.excludesFile) in the pre-scan ---


def _isolated_git_home(monkeypatch: Any, tmp_path: Path) -> Path:
    """Point HOME/XDG at a scratch dir so git resolution is hermetic.

    System config is skipped (GIT_CONFIG_NOSYSTEM) so the tests do not
    depend on the machine's /etc/gitconfig; resolution itself is still
    delegated to git, which handles the system scope identically.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / ".config").mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in (
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_PARAMETERS",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
    ):
        monkeypatch.delenv(var, raising=False)
    return home


def _set_global_excludes(home: Path, patterns: str) -> Path:
    gfile = home / "globalignore"
    gfile.write_text(patterns)
    (home / ".gitconfig").write_text(f"[core]\n\texcludesFile = {gfile}\n")
    return gfile


def test_scan_worktree_respects_global_excludes_file(
    tmp_path: Path, monkeypatch: Any
) -> None:
    home = _isolated_git_home(monkeypatch, tmp_path)
    _set_global_excludes(home, "*.glob\ngnoise/\n")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "keep.txt").write_text("x\n")
    (workspace / "a.glob").write_text("x\n")
    gnoise = workspace / "gnoise"
    gnoise.mkdir()
    (gnoise / "out.txt").write_text("x\n")
    count, _bytes, over = bridge_module._scan_worktree(workspace)
    assert count == 1
    assert over is False


def test_scan_worktree_gitignore_negation_beats_global_exclusion(
    tmp_path: Path, monkeypatch: Any
) -> None:
    home = _isolated_git_home(monkeypatch, tmp_path)
    _set_global_excludes(home, "*.dat\n")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".gitignore").write_text("!keep.dat\n")
    (workspace / "keep.dat").write_text("x\n")
    (workspace / "other.dat").write_text("x\n")
    count, _bytes, over = bridge_module._scan_worktree(workspace)
    # .gitignore + keep.dat; other.dat is globally excluded.
    assert count == 2
    assert over is False


def test_scan_worktree_global_negation_loses_to_gitignore_exclusion(
    tmp_path: Path, monkeypatch: Any
) -> None:
    home = _isolated_git_home(monkeypatch, tmp_path)
    _set_global_excludes(home, "!keep.log\n")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".gitignore").write_text("*.log\n")
    (workspace / "keep.log").write_text("x\n")
    (workspace / "plain.txt").write_text("x\n")
    count, _bytes, over = bridge_module._scan_worktree(workspace)
    # .gitignore + plain.txt; keep.log stays excluded by .gitignore.
    assert count == 2
    assert over is False


def test_scan_worktree_shadow_info_exclude_and_global(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # The shadow repo's own info/exclude (passed as git_dir) is consulted
    # and outranks the global file; the workspace's .git/info/exclude is
    # still never consulted (see the earlier test for that case).
    home = _isolated_git_home(monkeypatch, tmp_path)
    _set_global_excludes(home, "*.glob\n!keep.shx\n")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "keep.txt").write_text("x\n")
    (workspace / "a.glob").write_text("x\n")
    (workspace / "keep.shx").write_text("x\n")
    shadow = tmp_path / "shadow.git"
    (shadow / "info").mkdir(parents=True)
    (shadow / "info" / "exclude").write_text("*.shx\n")
    count, _bytes, over = bridge_module._scan_worktree(workspace, git_dir=shadow)
    # keep.txt only: a.glob via global, keep.shx via shadow info/exclude
    # (which beats the global negation).
    assert count == 1
    assert over is False


def test_scan_worktree_no_global_config_behaves_as_before(
    tmp_path: Path, monkeypatch: Any
) -> None:
    _isolated_git_home(monkeypatch, tmp_path)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "keep.txt").write_text("x\n")
    (workspace / "a.glob").write_text("x\n")
    count, _bytes, over = bridge_module._scan_worktree(workspace)
    assert count == 2
    assert over is False


def test_scan_worktree_missing_global_excludes_file_is_noop(
    tmp_path: Path, monkeypatch: Any
) -> None:
    home = _isolated_git_home(monkeypatch, tmp_path)
    (home / ".gitconfig").write_text(
        "[core]\n\texcludesFile = /nonexistent-dir-xyz/globalignore\n"
    )
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "keep.txt").write_text("x\n")
    count, _bytes, over = bridge_module._scan_worktree(workspace)
    assert count == 1
    assert over is False


def test_scan_worktree_default_global_ignore_file_honored(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # With core.excludesFile unset, git falls back to
    # $XDG_CONFIG_HOME/git/ignore; the pre-scan must do the same.
    home = _isolated_git_home(monkeypatch, tmp_path)
    default_ignore = home / ".config" / "git" / "ignore"
    default_ignore.parent.mkdir(parents=True, exist_ok=True)
    default_ignore.write_text("*.defign\n")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "keep.txt").write_text("x\n")
    (workspace / "d.defign").write_text("x\n")
    count, _bytes, over = bridge_module._scan_worktree(workspace)
    assert count == 1
    assert over is False


def test_scan_worktree_globally_ignored_bytes_do_not_count(
    tmp_path: Path, monkeypatch: Any
) -> None:
    home = _isolated_git_home(monkeypatch, tmp_path)
    _set_global_excludes(home, "*.bin\n")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "big.bin").write_text("x" * 100)
    (workspace / "small.txt").write_text("x\n")
    monkeypatch.setattr(bridge_module, "MAX_CAPTURE_BYTES", 10)
    _count, byte_count, over = bridge_module._scan_worktree(workspace)
    assert byte_count <= 10
    assert over is False


def test_scan_worktree_workspace_repo_local_config_does_not_leak(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # The workspace repo's own local core.excludesFile must NOT affect
    # the scan: the shadow add reads the shadow repo's config, never
    # the workspace repo's. A naive `git config --get` run in the
    # workspace would leak it and undercount.
    home = _isolated_git_home(monkeypatch, tmp_path)
    leak_file = home / "leakignore"
    leak_file.write_text("*.leak\n")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "config", "core.excludesFile", str(leak_file)],
        check=True,
    )
    (workspace / "keep.txt").write_text("x\n")
    (workspace / "a.leak").write_text("x\n")
    count, _bytes, over = bridge_module._scan_worktree(workspace)
    assert count == 2
    assert over is False


@pytest.mark.asyncio
async def test_capture_captures_despite_globally_excluded_over_bound(
    tmp_path: Path, monkeypatch: Any, mock_api: Any
) -> None:
    home = _isolated_git_home(monkeypatch, tmp_path)
    _set_global_excludes(home, "*.glog\ngnoise/\n")
    workspace, bridge = _make_workspace(tmp_path, tmp_path / "checkpoints", mock_api)
    for i in range(10):
        (workspace / f"n{i}.glog").write_text("noise\n")
    noise_dir = workspace / "gnoise"
    noise_dir.mkdir()
    for i in range(10):
        (noise_dir / f"n{i}.txt").write_text("noise\n")
    (workspace / "a.txt").write_text("v1\n")
    (workspace / "b.txt").write_text("v1\n")
    monkeypatch.setattr(bridge_module, "MAX_CAPTURE_FILES", 3)
    count, _b, over = bridge_module._scan_worktree(workspace)
    assert count == 2
    assert over is False
    sha = await bridge.capture("leaf-1", workspace=workspace)
    assert sha
    (workspace / "a.txt").write_text("v2\n")
    diff_spell = mock_api._registered["checkpoint_diff"]
    res = await diff_spell.execute(
        spell_cast_id="cast-g1", params={}, ctx=_ctx(workspace)
    )
    assert "a.txt" in res["files"]
    assert not any(name.endswith(".glog") for name in res["files"])
    assert not any(name.startswith("gnoise/") for name in res["files"])


def test_scan_worktree_empty_global_excludes_value_loads_nothing(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # An explicitly empty core.excludesFile overrides the default file
    # (verified against git) instead of falling back to it.
    home = _isolated_git_home(monkeypatch, tmp_path)
    (home / ".gitconfig").write_text("[core]\n\texcludesFile =\n")
    default_ignore = home / ".config" / "git" / "ignore"
    default_ignore.parent.mkdir(parents=True, exist_ok=True)
    default_ignore.write_text("*.defign\n")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "keep.txt").write_text("x\n")
    (workspace / "d.defign").write_text("x\n")
    count, _bytes, over = bridge_module._scan_worktree(workspace)
    assert count == 2
    assert over is False


def test_scan_worktree_relative_global_excludes_resolves_against_workspace(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # The shadow add runs with cwd=workspace, and git resolves a
    # relative core.excludesFile against the work tree (verified).
    home = _isolated_git_home(monkeypatch, tmp_path)
    (home / ".gitconfig").write_text("[core]\n\texcludesFile = relignore\n")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "relignore").write_text("*.rel\n")
    (workspace / "keep.txt").write_text("x\n")
    (workspace / "r.rel").write_text("x\n")
    count, _bytes, over = bridge_module._scan_worktree(workspace)
    # relignore itself + keep.txt; r.rel is excluded via the workspace file.
    assert count == 2
    assert over is False


def test_scan_worktree_git_config_failure_falls_back_to_no_global_rules(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # If asking git fails (missing binary, timeout), the scan must not
    # crash and must not invent rules: it falls back to the old
    # overcount behaviour, never an undercount.
    _isolated_git_home(monkeypatch, tmp_path)

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise OSError("git unavailable")

    monkeypatch.setattr(bridge_module.subprocess, "run", _boom)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "keep.txt").write_text("x\n")
    (workspace / "a.glob").write_text("x\n")
    count, _bytes, over = bridge_module._scan_worktree(workspace)
    assert count == 2
    assert over is False
