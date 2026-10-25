from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SpellSearchError(Exception):
    """Raised when spell search encounters a non-recoverable failure."""


# Exit code constants for rg
RG_EXIT_OK = 0  # Matches found
RG_EXIT_NO_MATCH = 1  # No matches
RG_EXIT_BAD_REGEX = 2  # Invalid pattern
# Any other exit code indicates rg failure


@dataclass
class SpellFileMatch:
    source_path: Path
    grimoire: str
    matched_context: str


class DCIRouter:
    """Two-stage rg-based search over spell files with timeout and error handling."""

    def __init__(self, spells_root: Path, rg_timeout: int = 10) -> None:
        self._root = spells_root
        self._timeout = rg_timeout

    async def route(self, params: dict[str, Any]) -> list[SpellFileMatch]:
        operation = params.get("operation", "")
        grimoire_hint = params.get("grimoire_hint", "")

        if not operation or not operation.strip():
            raise SpellSearchError("empty operation query")

        if not self._root.is_dir():
            # Checked here because rg cannot tell us. rg exits 2 on a missing
            # path and on a bad pattern alike, so `_run_rg` reads this as
            # RG_EXIT_BAD_REGEX and blames the caller's regex for a directory
            # that was never there. `tool_search` guards the same root before
            # constructing this router, so this is the check that holds when
            # the router is reached directly.
            raise SpellSearchError(f"spells root does not exist: {self._root}")

        grimoire_dirs = await self._search_grimoires(grimoire_hint)
        if not grimoire_dirs:
            return await self._search_all_grimoires(operation)

        return await self._search_narrow(grimoire_dirs, operation)

    async def _run_rg(
        self, args: list[str], description: str = "rg search"
    ) -> tuple[list[str], str | None]:
        """Run rg with timeout. Returns (lines, error). Error is None on success."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "rg",
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self._timeout
                )
            except TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await _reap_process(proc)
                return [], f"{description} timed out after {self._timeout}s"

            if proc.returncode == RG_EXIT_BAD_REGEX:
                return [], (
                    f"rg error (exit code 2). "
                    f"stderr: {stderr.decode(errors='replace')[:200]}"
                )

            if proc.returncode not in (RG_EXIT_OK, RG_EXIT_NO_MATCH):
                return [], (
                    f"rg failed with exit code {proc.returncode}. "
                    f"stderr: {stderr.decode(errors='replace')[:200]}"
                )

            lines = [
                p.strip()
                for p in stdout.decode(errors="replace").splitlines()
                if p.strip()
            ]
            return lines, None

        except (FileNotFoundError, OSError):
            return [], "rg not found on PATH"

    async def _search_grimoires(self, hint: str) -> list[Path]:
        """Find grimoire (domain) directories matching the hint."""
        if not hint:
            return []
        hint_normalised = re.sub(
            r"[_\s-]+", " ", unicodedata.normalize("NFKC", hint.lower())
        )
        hint_tokens = hint_normalised.split()
        matches = []
        if not self._root.exists():
            return []
        try:
            for child in self._root.iterdir():
                if child.is_dir():
                    name_normalised = re.sub(
                        r"[_\s-]+",
                        " ",
                        unicodedata.normalize("NFKC", child.name.lower()),
                    )
                    if all(t in name_normalised.split() for t in hint_tokens):
                        matches.append(child)
        except PermissionError:
            return []
        return matches

    @staticmethod
    def _build_rg_args(query: str, *extra_paths: str) -> list[str]:
        """Build rg args with `--` separator to prevent flag injection via query."""
        args = ["--type", "py", "-l", "--"]
        args.append(query)
        args.extend(extra_paths)
        return args

    async def _search_all_grimoires(self, query: str) -> list[SpellFileMatch]:
        """Fallback: search all spell files under the root when no grimoire hint."""
        args = self._build_rg_args(query, str(self._root))
        lines, error = await self._run_rg(args)
        if error:
            raise SpellSearchError(error)
        return self._process_rg_lines(lines)

    async def _search_narrow(
        self, dirs: list[Path], query: str
    ) -> list[SpellFileMatch]:
        """Search only within the specified grimoire directories."""
        args = self._build_rg_args(query, *(str(d) for d in dirs))
        lines, error = await self._run_rg(args)
        if error:
            raise SpellSearchError(error)
        return self._process_rg_lines(lines)

    def _process_rg_lines(self, lines: list[str]) -> list[SpellFileMatch]:
        """Convert rg output lines to SpellFileMatch objects."""
        results = []
        for path_str in lines:
            p = Path(path_str)
            try:
                grimoire = p.relative_to(self._root).parts[0]
            except ValueError:
                grimoire = p.parent.name
            results.append(
                SpellFileMatch(
                    source_path=p,
                    grimoire=grimoire,
                    matched_context=p.stem,
                )
            )
        return results


async def _reap_process(proc: asyncio.subprocess.Process) -> None:
    """Attempt to kill and reap a stuck process with retry."""
    for _ in range(3):
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
            return
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
    # Final attempt with no timeout
    try:
        await proc.wait()
    except ProcessLookupError:
        pass
