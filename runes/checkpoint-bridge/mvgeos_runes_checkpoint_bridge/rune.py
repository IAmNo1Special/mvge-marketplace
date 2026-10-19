"""Shadow-git workspace checkpoints, atomic leaf restoration, and rollbacks."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from mvgeos_core.layers import sessions_dir
from mvgeos_core.spells import ExecutionMode, SpellContext
from mvgeos_runes.rune_api import RuneAPI
from mvgeos_runes.types import (
    SessionLeafChangedData,
    SigilHook,
    SpellDefinition,
)

logger = logging.getLogger(__name__)

MAX_FILE_SIZE_BYTES = 2 * 1024 * 1024  # 2 MiB ceiling for untracked snapshot files


class CheckpointBridge:
    """Manages bare shadow git repositories and snapshots per workspace."""

    def __init__(
        self,
        api: RuneAPI,
        checkpoints_base_dir: Path | None = None,
    ) -> None:
        self.api = api
        self._base_dir = checkpoints_base_dir
        self._lock = asyncio.Lock()
        self._last_leaf_id: str | None = None
        self._last_workspace: Path | None = None

    def _get_base_dir(self) -> Path:
        if self._base_dir is not None:
            return self._base_dir
        return sessions_dir() / "checkpoints"

    def _git_dir_for(self, workspace: Path) -> Path:
        base = self._get_base_dir()
        ws_hash = hashlib.sha256(str(workspace.resolve()).encode("utf-8")).hexdigest()[
            :16
        ]
        return base / f"{ws_hash}.git"

    async def _run_git(
        self,
        git_dir: Path,
        workspace: Path,
        args: list[str],
        env: dict[str, str] | None = None,
        stdin_data: bytes | None = None,
    ) -> tuple[int, str, str]:
        cmd = [
            "git",
            "--git-dir",
            str(git_dir),
            "--work-tree",
            str(workspace),
            *args,
        ]
        run_env = os.environ.copy()
        if env:
            run_env.update(env)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(workspace),
            env=run_env,
            stdin=asyncio.subprocess.PIPE if stdin_data else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate(input=stdin_data)
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        return proc.returncode or 0, stdout, stderr

    async def _ensure_git_dir(self, git_dir: Path, workspace: Path) -> None:
        if git_dir.exists():
            return
        git_dir.mkdir(parents=True, exist_ok=True)
        # Initialize bare repository
        proc = await asyncio.create_subprocess_exec(
            "git",
            "init",
            "--bare",
            str(git_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        # Configure bare repo for safe cross-platform file tracking
        configs = [
            ("core.autocrlf", "false"),
            ("core.longpaths", "true"),
            ("core.symlinks", "true"),
            ("core.fsmonitor", "false"),
            ("core.quotepath", "false"),
        ]
        for key, val in configs:
            await self._run_git(git_dir, workspace, ["config", key, val])

        # Alternates object sharing if workspace has its own .git
        ws_git_objects = workspace / ".git" / "objects"
        if ws_git_objects.is_dir():
            alternates = git_dir / "objects" / "info" / "alternates"
            alternates.parent.mkdir(parents=True, exist_ok=True)
            alternates.write_text(f"{ws_git_objects.resolve()}\n")

    def _load_checkpoints(self, git_dir: Path) -> dict[str, str]:
        ck_file = git_dir / "checkpoints.json"
        if not ck_file.exists():
            return {}
        try:
            data = json.loads(ck_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
            return {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_checkpoints(self, git_dir: Path, checkpoints: dict[str, str]) -> None:
        ck_file = git_dir / "checkpoints.json"
        ck_file.write_text(json.dumps(checkpoints, indent=2), encoding="utf-8")

    async def capture(
        self,
        leaf_id: str,
        workspace: Path | None = None,
    ) -> str:
        """Create a git tree snapshot of the working directory bound to leaf_id."""
        ws = workspace or self._last_workspace or Path.cwd()
        self._last_workspace = ws
        self._last_leaf_id = leaf_id
        git_dir = self._git_dir_for(ws)

        async with self._lock:
            await self._ensure_git_dir(git_dir, ws)

            temp_index = git_dir / f"index.{uuid4().hex}"
            try:
                env = {"GIT_INDEX_FILE": str(temp_index)}

                # Add tracked & untracked files respecting .gitignore
                await self._run_git(
                    git_dir,
                    ws,
                    ["add", "-A", "--", ":(exclude).agents", ":(exclude).git"],
                    env=env,
                )

                code, tree_sha, stderr = await self._run_git(
                    git_dir,
                    ws,
                    ["write-tree"],
                    env=env,
                )
                tree_sha = tree_sha.strip()
                if code != 0 or not tree_sha:
                    logger.warning("write-tree failed: %s", stderr)
                    return ""

                checkpoints = self._load_checkpoints(git_dir)
                checkpoints[leaf_id] = tree_sha
                checkpoints["latest"] = tree_sha
                self._save_checkpoints(git_dir, checkpoints)
                return tree_sha
            finally:
                if temp_index.exists():
                    try:
                        temp_index.unlink()
                    except OSError:
                        pass

    async def diff(
        self,
        workspace: Path | None = None,
        leaf_id: str | None = None,
        from_leaf_id: str | None = None,
    ) -> tuple[str, list[str]]:
        """Compute git diff and list of changed files against a checkpoint tree."""
        ws = workspace or self._last_workspace or Path.cwd()
        git_dir = self._git_dir_for(ws)
        if not git_dir.exists():
            return "No checkpoints recorded for this workspace.", []

        checkpoints = self._load_checkpoints(git_dir)
        target_sha = checkpoints.get(leaf_id) if leaf_id else checkpoints.get("latest")
        if not target_sha:
            return "Checkpoint tree not found.", []

        async with self._lock:
            temp_index = git_dir / f"index.{uuid4().hex}"
            try:
                env = {"GIT_INDEX_FILE": str(temp_index)}
                await self._run_git(
                    git_dir,
                    ws,
                    ["add", "-A", "--", ":(exclude).agents", ":(exclude).git"],
                    env=env,
                )

                if from_leaf_id and from_leaf_id in checkpoints:
                    from_sha = checkpoints[from_leaf_id]
                    diff_args = [
                        "diff",
                        "--no-ext-diff",
                        from_sha,
                        target_sha,
                        "--",
                        ".",
                    ]
                    name_args = [
                        "diff",
                        "--no-ext-diff",
                        "--name-only",
                        from_sha,
                        target_sha,
                        "--",
                        ".",
                    ]
                else:
                    diff_args = ["diff", "--no-ext-diff", target_sha, "--", "."]
                    name_args = [
                        "diff",
                        "--no-ext-diff",
                        "--name-only",
                        target_sha,
                        "--",
                        ".",
                    ]

                _, diff_text, _ = await self._run_git(git_dir, ws, diff_args, env=env)
                _, names_text, _ = await self._run_git(git_dir, ws, name_args, env=env)
                files = [
                    line.strip() for line in names_text.splitlines() if line.strip()
                ]
                return diff_text, files
            finally:
                if temp_index.exists():
                    try:
                        temp_index.unlink()
                    except OSError:
                        pass

    async def restore_file(
        self,
        file_path: str,
        workspace: Path | None = None,
        leaf_id: str | None = None,
    ) -> bool:
        """Restore a single file to its version from a checkpoint tree."""
        ws = workspace or self._last_workspace or Path.cwd()
        git_dir = self._git_dir_for(ws)
        if not git_dir.exists():
            return False

        checkpoints = self._load_checkpoints(git_dir)
        target_sha = checkpoints.get(leaf_id) if leaf_id else checkpoints.get("latest")
        if not target_sha:
            return False

        async with self._lock:
            temp_index = git_dir / f"index.{uuid4().hex}"
            try:
                env = {"GIT_INDEX_FILE": str(temp_index)}
                await self._run_git(
                    git_dir,
                    ws,
                    ["read-tree", target_sha],
                    env=env,
                )
                code, _, _ = await self._run_git(
                    git_dir,
                    ws,
                    ["checkout-index", "-f", "--", file_path],
                    env=env,
                )
                return code == 0
            finally:
                if temp_index.exists():
                    try:
                        temp_index.unlink()
                    except OSError:
                        pass

    async def on_session_leaf_changed(
        self,
        data: SessionLeafChangedData,
    ) -> None:
        """Restore working directory to match the target leaf's snapshot."""
        ws = Path(data.workspace_root) if data.workspace_root else Path.cwd()
        git_dir = self._git_dir_for(ws)
        if not git_dir.exists():
            return

        checkpoints = self._load_checkpoints(git_dir)
        if not data.new_leaf_id or data.new_leaf_id not in checkpoints:
            return

        target_tree = checkpoints[data.new_leaf_id]
        old_tree = checkpoints.get(data.old_leaf_id) if data.old_leaf_id else None

        async with self._lock:
            temp_index = git_dir / f"index.{uuid4().hex}"
            try:
                env = {"GIT_INDEX_FILE": str(temp_index)}

                # 1. Read target tree into index and checkout files
                await self._run_git(git_dir, ws, ["read-tree", target_tree], env=env)
                await self._run_git(
                    git_dir, ws, ["checkout-index", "-a", "-f"], env=env
                )

                # 2. Prune files that only existed in the old leaf
                if old_tree:
                    _, diff_names, _ = await self._run_git(
                        git_dir,
                        ws,
                        [
                            "diff-tree",
                            "-r",
                            "--name-only",
                            "--diff-filter=A",
                            target_tree,
                            old_tree,
                        ],
                        env=env,
                    )
                    for rel_file in diff_names.splitlines():
                        rel = rel_file.strip()
                        if not rel:
                            continue
                        target_path = ws / rel
                        if target_path.is_file():
                            try:
                                target_path.unlink()
                            except OSError:
                                pass

                checkpoints["latest"] = target_tree
                self._save_checkpoints(git_dir, checkpoints)
            finally:
                if temp_index.exists():
                    try:
                        temp_index.unlink()
                    except OSError:
                        pass


def rune_factory(
    api: RuneAPI,
    checkpoints_base_dir: Path | None = None,
) -> CheckpointBridge:
    bridge = CheckpointBridge(api, checkpoints_base_dir=checkpoints_base_dir)

    async def handle_turn_start(data: Any) -> None:
        runner = getattr(api, "_runner", None)
        runner_ctx = getattr(runner, "context", None)
        session_id = getattr(runner_ctx, "session_id", None) or "current"
        ws = Path(getattr(runner_ctx, "cwd", Path.cwd()))
        await bridge.capture(session_id, workspace=ws)

    async def handle_turn_end(data: Any) -> None:
        runner = getattr(api, "_runner", None)
        runner_ctx = getattr(runner, "context", None)
        session_id = getattr(runner_ctx, "session_id", None) or "current"
        ws = Path(getattr(runner_ctx, "cwd", Path.cwd()))
        await bridge.capture(session_id, workspace=ws)

    async def handle_leaf_changed(data: Any) -> None:
        if isinstance(data, SessionLeafChangedData):
            await bridge.on_session_leaf_changed(data)
        elif isinstance(data, dict):
            payload = SessionLeafChangedData(
                tome_id=data.get("tome_id", ""),
                old_leaf_id=data.get("old_leaf_id"),
                new_leaf_id=data.get("new_leaf_id"),
                reason=data.get("reason", "undo"),
                workspace_root=data.get("workspace_root"),
            )
            await bridge.on_session_leaf_changed(payload)

    api.on(SigilHook.TURN_START, handle_turn_start)
    api.on(SigilHook.TURN_END, handle_turn_end)
    api.on(SigilHook.SESSION_LEAF_CHANGED, handle_leaf_changed)

    # Register checkpoint_diff spell
    async def diff_handler(
        params: dict[str, Any] | None = None,
        *args: Any,
        leaf_id: str | None = None,
        from_leaf_id: str | None = None,
        ctx: SpellContext | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        p = dict(params or {})
        p.update(kwargs)
        ws = getattr(ctx, "workspace_root", None) or Path.cwd()
        target_leaf = p.get("leaf_id") or leaf_id
        base_leaf = p.get("from_leaf_id") or from_leaf_id
        diff_text, files = await bridge.diff(
            workspace=ws, leaf_id=target_leaf, from_leaf_id=base_leaf
        )
        return {
            "success": True,
            "diff": diff_text,
            "files": files,
        }

    api.register_spell(
        SpellDefinition(
            name="checkpoint_diff",
            description="Inspect git diff between working tree and a session checkpoint",
            parameters={
                "type": "object",
                "properties": {
                    "leaf_id": {
                        "type": "string",
                        "description": "Optional target leaf ID to diff against (defaults to latest checkpoint)",
                    },
                    "from_leaf_id": {
                        "type": "string",
                        "description": "Optional base leaf ID for range diff",
                    },
                },
            },
            execution_mode=ExecutionMode.PARALLEL,
            handler=diff_handler,
            read_only=True,
        )
    )

    # Register checkpoint_restore_file spell
    async def restore_handler(
        params: dict[str, Any] | None = None,
        *args: Any,
        path: str | None = None,
        leaf_id: str | None = None,
        ctx: SpellContext | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        p = dict(params or {})
        p.update(kwargs)
        file_path = p.get("path") or path
        if not file_path:
            return {"success": False, "error": "path parameter required"}
        ws = getattr(ctx, "workspace_root", None) or Path.cwd()
        target_leaf = p.get("leaf_id") or leaf_id
        success = await bridge.restore_file(
            file_path=file_path, workspace=ws, leaf_id=target_leaf
        )
        return {
            "success": success,
            "path": file_path,
            "message": f"Restored {file_path} from checkpoint"
            if success
            else f"Failed to restore {file_path}",
        }

    api.register_spell(
        SpellDefinition(
            name="checkpoint_restore_file",
            description="Rollback a single file to its checkpointed version",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path of file to rollback",
                    },
                    "leaf_id": {
                        "type": "string",
                        "description": "Optional checkpoint leaf ID (defaults to latest)",
                    },
                },
                "required": ["path"],
            },
            execution_mode=ExecutionMode.SEQUENTIAL,
            handler=restore_handler,
        )
    )

    api.register_command(
        name="checkpoint",
        description="View working tree diff against session checkpoint",
        handler=lambda text: f"Checkpoint inspected: {text}",
    )

    return bridge
