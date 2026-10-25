from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import paths

# rg exit codes
_RG_OK = 0
_RG_NO_MATCH = 1
_RG_BAD_PATTERN = 2

# Sentinel for rg-not-found detection (avoids fragile string matching)
_RG_NOT_FOUND = "RG_NOT_FOUND"

logger = logging.getLogger(__name__)


class SkillSearchError(Exception):
    """Non-recoverable skill search failure."""


@dataclass
class SkillFile:
    path: Path
    body_preview: str = ""
    _metadata: dict[str, Any] | None = None
    _raw_content: str | None = None

    @property
    def raw_content(self) -> str:
        """Read file content once and cache it."""
        if self._raw_content is None:
            try:
                self._raw_content = self.path.read_text(
                    encoding="utf-8", errors="replace"
                )
            except (OSError, UnicodeDecodeError):
                self._raw_content = ""
        return self._raw_content

    @property
    def metadata(self) -> dict[str, Any]:
        """Lazy-load metadata from cached content."""
        if self._metadata is not None:
            return self._metadata
        content = self.raw_content
        name = self._extract_name(self.path, content)
        desc = self._extract_description(content)
        resources = self._extract_resources(content)
        self._metadata = {"name": name, "description": desc, "resources": resources}
        return self._metadata

    @staticmethod
    def _extract_name(path: Path, body: str) -> str:
        """Derive name from first # heading or filename stem. Strips trailing #."""
        m = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        if m:
            return m.group(1).strip().rstrip("#").strip()
        return path.stem

    @staticmethod
    def _extract_description(body: str) -> str:
        """First non-empty paragraph after the first heading (skipping subheadings)."""
        lines = body.splitlines()
        in_body = False
        for line in lines:
            if line.startswith("# "):
                in_body = True
                continue
            if in_body and line.strip():
                if line.startswith("#"):
                    continue
                return line.strip()[:200]
        return ""

    @staticmethod
    def _extract_resources(body: str) -> dict[str, str]:
        """Resource names listed under a ## Resources heading.
        Only breaks on `##` (not `###`), allowing subheadings within Resources.
        """
        resources = {}
        in_resources = False
        for line in body.splitlines():
            stripped = line.strip()
            if re.match(r"^## Resources\s*$", stripped):
                in_resources = True
                continue
            if in_resources:
                if re.match(r"^## [^#]", stripped) or not stripped:
                    break
                res_name = stripped.lstrip("- ").strip()
                resources[res_name] = ""
        return resources

    def to_dict(self) -> dict[str, Any]:
        m = self.metadata
        return {
            "name": m["name"],
            "description": m["description"],
            "path": str(self.path.resolve()),
            "filename": self.path.name,
            "body_preview": self.body_preview[:500],
            "resources": list(m.get("resources", {}).keys()),
        }


class DCISkillMatcher:
    """
    rg-only skill matching. No YAML frontmatter parsing.
    Uses `rg -ilF` (fixed-string, case-insensitive) to avoid regex
    exit-code-2 failures. Falls back to Python-level search if rg is
    unavailable.
    """

    def __init__(
        self,
        skill_dirs: list[Path] | None = None,
        rg_timeout: int = 15,
        project_dir: str | Path | None = None,
    ) -> None:
        self._explicit_skill_dirs = skill_dirs
        self._rg_timeout = rg_timeout
        self._project_dir = project_dir

    @property
    def skill_dirs(self) -> list[Path]:
        """Every skill root searched, resolved per call and unfiltered.

        Unfiltered on purpose: :meth:`discover_skill_dirs` drops the ones
        that do not exist, and a caller that needs to report which root it
        could not search needs the ones that were dropped.
        """
        return _skill_root_candidates(self._explicit_skill_dirs, self._project_dir)

    def discover_skill_dirs(self) -> list[Path]:
        return [d for d in self.skill_dirs if d.is_dir()]

    async def _run_rg(self, args: list[str]) -> tuple[list[str], str | None]:
        """Run rg with fixed strings, timeout, and exit-code handling."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "rg",
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self._rg_timeout
                )
            except TimeoutError:
                await _reap_process_skills(proc)
                return [], f"rg timed out after {self._rg_timeout}s"

            if proc.returncode == _RG_BAD_PATTERN:
                return [], (
                    f"rg error (exit 2). "
                    f"stderr: {stderr.decode(errors='replace')[:200]}"
                )
            if proc.returncode not in (_RG_OK, _RG_NO_MATCH):
                return [], (
                    f"rg failed (exit {proc.returncode}). "
                    f"stderr: {stderr.decode(errors='replace')[:200]}"
                )

            lines = [
                p.strip()
                for p in stdout.decode(errors="replace").splitlines()
                if p.strip()
            ]
            return lines, None

        except (FileNotFoundError, OSError):
            return [], _RG_NOT_FOUND

    async def match_all(self, dirs: list[Path], query: str) -> list[SkillFile]:
        """Single rg pass over all .md files in skill directories.
        Uses -F (fixed string) + --glob "*.md" to let rg handle file discovery.
        Falls back to Python-level search if rg is unavailable.
        """
        if not dirs or not query:
            return []

        # Let rg handle its own file traversal with --glob
        args = ["-ilF", "--glob", "*.md", query]
        args.extend(str(d) for d in dirs)
        matched_paths_str, error = await self._run_rg(args)

        if error == _RG_NOT_FOUND:
            # rg not installed â€” fallback to Python-level search
            matched_paths = []
            for skill_dir in dirs:
                for f in skill_dir.rglob("*.md"):
                    try:
                        content = f.read_text(encoding="utf-8", errors="replace")
                        if query.lower() in content.lower():
                            matched_paths.append(f)
                    except (OSError, UnicodeDecodeError):
                        continue
        elif error:
            raise SkillSearchError(error)
        else:
            matched_paths = [Path(p) for p in matched_paths_str]

        results = []
        for fpath in matched_paths:
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
                idx = content.lower().find(query.lower())
                if idx == -1:
                    preview = content[:200]
                else:
                    start = max(0, idx - 100)
                    end = min(len(content), idx + len(query) + 100)
                    preview = content[start:end]
            except (OSError, UnicodeDecodeError):
                content = ""
                preview = f"<unreadable: {fpath.name}>"

            results.append(
                SkillFile(path=fpath, body_preview=preview, _raw_content=content)
            )

        return results


async def _reap_process_skills(proc: asyncio.subprocess.Process) -> None:
    """Attempt to kill and reap a stuck process with retry."""
    for _ in range(3):
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
            return
        except TimeoutError:
            proc.kill()
    try:
        await proc.wait()
    except ProcessLookupError:
        pass


def _skill_root_candidates(
    custom_roots: list[Path] | None = None,
    project_dir: str | Path | None = None,
) -> list[Path]:
    """Every skill root Seeker would search, resolved now and unfiltered.

    The user-scope root comes from :func:`paths.skill_roots`, which reads
    ``mvge.core.skills_dir()`` rather than spelling out ``~/.agents/skills``,
    so it moves with ``$MVGE_GLOBAL_DIR`` and cannot drift from where
    Skills are installed.

    ``~/.claude/skills`` stays home-relative on purpose: it is a third-party
    convention, not our layer, and relocating our own directory does not
    relocate it.

    ``project_dir`` names the project whose Skills are in scope. Left
    ``None``, the project layer is omitted rather than defaulted to the
    working directory -- see :func:`paths.skill_roots` and ADR 0017.
    """
    if custom_roots:
        return [p.expanduser().resolve() for p in custom_roots]
    return [root.resolve() for root in paths.skill_roots(project_dir)]


def _get_skill_roots(
    custom_roots: list[Path] | None = None,
    project_dir: str | Path | None = None,
) -> list[Path]:
    """Return list of existing skill directories."""
    return [r for r in _skill_root_candidates(custom_roots, project_dir) if r.is_dir()]


DCI_SkillMatcher = DCISkillMatcher
