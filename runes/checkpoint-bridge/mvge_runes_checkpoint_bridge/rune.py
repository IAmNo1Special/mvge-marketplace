"""Shadow-git workspace checkpoints, atomic leaf restoration, and rollbacks."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from mvge.core.layers import sessions_dir
from mvge.core.spells import ExecutionMode, SpellContext
from mvge.runes.rune_api import RuneAPI
from mvge.runes.types import (
    SessionLeafChangedData,
    SigilHook,
    SpellDefinition,
)

logger = logging.getLogger(__name__)

MAX_FILE_SIZE_BYTES = 2 * 1024 * 1024  # 2 MiB ceiling for untracked snapshot files

# Bounds on a single capture scan. capture() snapshots the whole worktree
# with a fresh temp index on every turn, so its cost is O(worktree), not
# O(changes): a 20k-file tree costs ~1.2s of `git add`, a 300k-file tree
# ~80s. The engine cancels sigil handlers after 15s, so an unbounded scan
# can never succeed on a large tree -- it only burns the turn, leaks the
# git subprocess (cancelling communicate() does not kill it), and leaves
# index lock files behind. The pre-scan below fails fast instead: it
# walks with early exit at the bound, so a huge tree costs a short walk,
# not a timed-out scan. 50k files leaves wide margin under the 15s line
# on the calibrated rate while covering ordinary projects.
MAX_CAPTURE_FILES = 50_000
MAX_CAPTURE_BYTES = 1024 * 1024 * 1024  # 1 GiB of worktree content

# Directories the git scan itself excludes (the `:(exclude)` pathspecs
# in capture/diff), mirrored here so the pre-scan counts what git counts.
_EXCLUDED_DIR_NAMES = frozenset({".git", ".agents"})


def _translate_gitignore_pattern(pattern: str) -> str:
    """Translate one gitignore glob to a regex body (no anchors).

    Git wildmatch, not fnmatch: ``*`` and ``?`` never cross ``/``,
    ``**`` does. Character classes keep git's ``!`` negation spelling.
    """
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "*":
            if pattern[i : i + 2] == "**":
                if i + 2 >= n:
                    out.append(".*")
                    i += 2
                    continue
                if pattern[i + 2] == "/":
                    # Leading or middle "**/": zero or more directories.
                    out.append("(?:.*/)?")
                    i += 3
                    continue
                # "**" not followed by "/": treat as "*".
                out.append("[^/]*")
                i += 2
                continue
            out.append("[^/]*")
            i += 1
            continue
        if ch == "?":
            out.append("[^/]")
            i += 1
            continue
        if ch == "[":
            j = i + 1
            if j < n and pattern[j] in "!^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j >= n:
                out.append(r"\[")
                i += 1
                continue
            stuff = pattern[i + 1 : j]
            if stuff.startswith("!"):
                stuff = "^" + stuff[1:]
            stuff = stuff.replace("\\", "\\\\")
            out.append(f"[{stuff}]")
            i = j + 1
            continue
        if ch == "\\" and i + 1 < n:
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        out.append(re.escape(ch))
        i += 1
    return "".join(out)


class _GitignoreRule:
    """One parsed gitignore line, bound to the directory holding its file."""

    __slots__ = ("anchored", "base", "dir_only", "negated", "regex")

    def __init__(self, base: str, raw: str) -> None:
        pattern = raw
        self.negated = False
        if pattern.startswith("!"):
            self.negated = True
            pattern = pattern[1:]
        elif pattern.startswith(("\\!", "\\#")):
            pattern = pattern[1:]
        # Trailing spaces are stripped unless backslash-escaped.
        pattern = re.sub(r"(?<!\\) +$", "", pattern)
        self.dir_only = pattern.endswith("/")
        if self.dir_only:
            pattern = pattern[:-1]
        self.base = base
        # A pattern containing "/" (other than a trailing one, already
        # stripped) is anchored to its base directory; otherwise it
        # matches the basename at any depth below the base.
        self.anchored = "/" in pattern
        if pattern.startswith("/"):
            pattern = pattern[1:]
            self.anchored = True
        body = _translate_gitignore_pattern(pattern)
        if self.anchored:
            self.regex = re.compile(f"^{body}$")
        else:
            self.regex = re.compile(f"^{body}$")

    def matches(self, rel: str, is_dir: bool) -> bool:
        """Whether this rule matches rel path (posix, relative to base)."""
        if self.dir_only and not is_dir:
            return False
        if self.anchored:
            return self.regex.match(rel) is not None
        # Unanchored: match the basename only.
        name = rel.rsplit("/", 1)[-1]
        return self.regex.match(name) is not None


def _parse_gitignore_file(path: Path, base: str) -> list[_GitignoreRule]:
    """Parse a .gitignore / exclude file into rules, ignoring bad lines."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rules: list[_GitignoreRule] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if not line or line.startswith("#"):
            continue
        rules.append(_GitignoreRule(base, line))
    return rules


def _is_ignored(rel_posix: str, is_dir: bool, rules: list[_GitignoreRule]) -> bool:
    """Last-match-wins across rules, deepest file first.

    Rules are stored lowest-precedence first (global excludes, then
    shadow info/exclude, then root .gitignore, then deeper files,
    lines in order), so iterating in reverse gives the
    highest-precedence rule first. A rule matches only paths below its
    own base directory.
    """
    for rule in reversed(rules):
        base = rule.base
        if base:
            if rel_posix != base and not rel_posix.startswith(base + "/"):
                continue
            rel = rel_posix[len(base) + 1 :]
        else:
            rel = rel_posix
        if rule.matches(rel, is_dir):
            return not rule.negated
    return False


def _default_global_ignore_file() -> Path:
    """Git's fallback global excludes file when core.excludesFile is unset."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "git" / "ignore"
    return Path.home() / ".config" / "git" / "ignore"


def _resolve_global_excludes_file(workspace: Path, git_dir: Path | None) -> Path | None:
    """Resolve the global excludes file the shadow ``git add`` would read.

    Git itself is asked, so system/global/local precedence, ``include``
    directives, quoting, and environment overrides (GIT_CONFIG_COUNT,
    GIT_CONFIG_GLOBAL, ...) all match the shadow add exactly -- the
    resolver never re-implements git-config parsing. Two scopes are
    deliberately kept out, because the shadow add never sees them:

    - the workspace repo's local config: when no valid shadow repo
      exists yet, git is run with cwd in a scratch directory, so no
      workspace-local value can leak in;
    - a workspace repo's ``core.excludesFile`` is likewise never read
      once the shadow repo exists, because ``--git-dir`` points at the
      shadow repo, whose config capture() itself writes (and never
      sets excludesFile in).

    Return value: the excludes-file path, or None when git reports no
    global excludes at all. An explicitly empty core.excludesFile
    overrides the default file and loads nothing (verified against
    git), so it also yields None. An unexpected git failure yields
    None too: the old overcount behaviour, never an undercount.
    A relative value resolves against the workspace: the shadow add
    runs with cwd=workspace, and that is how git resolves it
    (verified: a relative value is honoured only when the file sits
    under the work tree).
    """
    tmp: tempfile.TemporaryDirectory[str] | None = None
    if git_dir is not None and (git_dir / "config").is_file():
        cmd = ["git", "--git-dir", str(git_dir), "config", "--get", "core.excludesFile"]
        cwd: str | None = None
    else:
        tmp = tempfile.TemporaryDirectory(prefix="mvge-git-config-")
        cwd = tmp.name
        cmd = ["git", "config", "--get", "core.excludesFile"]
    try:
        env = os.environ.copy()
        for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
            env.pop(var, None)
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        if tmp is not None:
            tmp.cleanup()
    if proc.returncode == 0:
        value = proc.stdout.strip()
        if not value:
            return None
        path = Path(os.path.expanduser(value))
        if not path.is_absolute():
            path = workspace / path
        return path
    if proc.returncode == 1:
        # Key unset in every scope git consulted: git's default file.
        return _default_global_ignore_file()
    return None


def _scan_worktree(
    workspace: Path, git_dir: Path | None = None
) -> tuple[int, int, bool]:
    """Count files/bytes under workspace, stopping early past the bound.

    Returns (file_count, byte_count, over_bound). Symlinked directories
    are counted as files, never descended into, so a symlink loop cannot
    hang the scan. Unreadable entries are skipped. An unreadable or
    missing workspace reports not-over-bound so the caller falls through
    to the existing git-failure path instead of inventing a new one.

    Gitignore-aware, mirroring what the shadow ``git add -A`` in
    capture() will actually stage. Precedence, highest first (verified
    against git with ``check-ignore -v`` on opposing pairs at every
    boundary): .gitignore files at every depth (deeper files override
    shallower ones, later lines override earlier ones, ``!``
    re-includes), then the shadow repo's ``info/exclude``, then the
    global excludes file (core.excludesFile, else the default
    ``$XDG_CONFIG_HOME/git/ignore``). Ignored directories are pruned,
    never descended into -- git likewise cannot re-include a file under
    an excluded directory, so pruning cannot hide a file git would
    stage. The pre-scan counts what git counts: before the .gitignore
    fix it counted ignored files too, so a tree of ignored build
    output could trip the bound and skip a capture git itself would
    have finished cheaply.
    Deliberately NOT consulted, because the shadow add does not consult
    them either (verified against git directly): the workspace's
    ``.git/info/exclude`` (git reads ``$GIT_DIR/info/exclude`` of the
    shadow repo, never the workspace's) and the workspace repo's
    tracking status -- the shadow index is fresh on every capture, so a
    file tracked in the workspace's own repo but matching .gitignore is
    still left out of the shadow tree, which is exactly what this
    matcher reproduces.
    """
    file_count = 0
    byte_count = 0
    # Base rules, lowest precedence first: _is_ignored iterates in
    # reverse, so the highest-precedence matching rule decides. The
    # shadow info/exclude is read only when a shadow git dir is known;
    # before the first capture it does not exist yet, and `git init
    # --bare` creates one holding comments only, which parse to no
    # rules -- so omitting it then matches the shadow add exactly.
    base_rules: list[_GitignoreRule] = []
    global_excludes = _resolve_global_excludes_file(workspace, git_dir)
    if global_excludes is not None:
        base_rules.extend(_parse_gitignore_file(global_excludes, ""))
    if git_dir is not None:
        base_rules.extend(_parse_gitignore_file(git_dir / "info" / "exclude", ""))
    # Stack entries: (directory, rules from ancestors, dir rel posix path).
    # Rel paths are tracked lexically, never resolved, so symlinked
    # entries are matched by their worktree path, as git matches them.
    stack: list[tuple[Path, list[_GitignoreRule], str]] = [(workspace, base_rules, "")]
    while stack:
        current, parent_rules, rel_dir = stack.pop()
        try:
            with os.scandir(current) as entries:
                children = list(entries)
        except OSError:
            continue
        # This directory's own .gitignore applies to its children, not
        # to itself, so it is loaded here -- after the decision to
        # descend into this directory was already made by the parent.
        rules = list(parent_rules)
        for entry in children:
            if entry.name == ".gitignore" and entry.is_file():
                rules.extend(_parse_gitignore_file(Path(entry.path), rel_dir))
                break
        for entry in children:
            try:
                rel_posix = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in _EXCLUDED_DIR_NAMES:
                        continue
                    if _is_ignored(rel_posix, True, rules):
                        continue
                    stack.append((Path(entry.path), rules, rel_posix))
                    continue
                if _is_ignored(rel_posix, False, rules):
                    continue
                file_count += 1
                try:
                    byte_count += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    pass
            except OSError:
                continue
            if file_count > MAX_CAPTURE_FILES or byte_count > MAX_CAPTURE_BYTES:
                return file_count, byte_count, True
    return file_count, byte_count, False


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
        # Workspaces already warned about a skipped capture, so a huge
        # tree logs one warning and then quiet debugs instead of a
        # warning on every turn. Cleared when a capture succeeds again.
        self._skip_warned: set[str] = set()

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
        stdout_bytes, stderr_bytes = await self._communicate(proc, stdin_data)
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        return proc.returncode or 0, stdout, stderr

    @staticmethod
    async def _communicate(
        proc: asyncio.subprocess.Process,
        stdin_data: bytes | None = None,
    ) -> tuple[bytes, bytes]:
        """Await a subprocess, killing it if the await is cancelled.

        Cancelling communicate() alone leaves the child running: a
        timed-out capture kept hashing and writing objects long after
        the engine moved on. Kill on cancellation so a timeout (or an
        abort) ends the work, then re-raise.
        """
        try:
            return await proc.communicate(input=stdin_data)
        except asyncio.CancelledError:
            if proc.returncode is None:
                proc.kill()
                with contextlib.suppress(asyncio.CancelledError):
                    await proc.wait()
            raise

    @staticmethod
    def _remove_temp_index(temp_index: Path) -> None:
        """Remove a capture temp index and the lock git leaves behind.

        git creates index.<uuid>.lock when the add starts and removes it
        when the add finishes; a killed add leaves it. Temp indexes are
        uuid-unique per capture, so the lock belongs to this capture
        alone and is safe to remove here.
        """
        for candidate in (temp_index, Path(f"{temp_index}.lock")):
            with contextlib.suppress(OSError):
                candidate.unlink(missing_ok=True)

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
        await self._communicate(proc)

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

        # Bound the work before creating anything: a scan that cannot
        # finish inside the engine's sigil timeout must fail fast with a
        # clear reason, not burn the turn and leak a git process. Skipping
        # leaves the previous checkpoint (if any) in place; the hook
        # caller already treats "" as "no new checkpoint".
        file_count, byte_count, over_bound = _scan_worktree(ws, git_dir)
        if over_bound:
            key = str(ws)
            detail = (
                f"worktree exceeds capture bound "
                f"({file_count} files, {byte_count} bytes; limits "
                f"{MAX_CAPTURE_FILES} files, {MAX_CAPTURE_BYTES} bytes)"
            )
            if key not in self._skip_warned:
                self._skip_warned.add(key)
                logger.warning("Skipping checkpoint capture for %s: %s", ws, detail)
            else:
                logger.debug("Skipping checkpoint capture for %s: %s", ws, detail)
            return ""

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
                self._skip_warned.discard(str(ws))
                return tree_sha
            finally:
                self._remove_temp_index(temp_index)

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
                self._remove_temp_index(temp_index)

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
                self._remove_temp_index(temp_index)

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
                self._remove_temp_index(temp_index)


def rune_factory(
    api: RuneAPI,
    checkpoints_base_dir: Path | None = None,
) -> CheckpointBridge:
    bridge = CheckpointBridge(api, checkpoints_base_dir=checkpoints_base_dir)

    async def handle_turn_start(data: Any) -> None:
        runner_ctx = api.context
        session_id = getattr(runner_ctx, "session_id", None) or "current"
        ws = Path(getattr(runner_ctx, "cwd", Path.cwd()))
        await bridge.capture(session_id, workspace=ws)

    async def handle_turn_end(data: Any) -> None:
        runner_ctx = api.context
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
