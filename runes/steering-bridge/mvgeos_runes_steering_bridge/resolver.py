from __future__ import annotations

import logging
from pathlib import Path

from mvge.core import global_agents_dir

logger = logging.getLogger(__name__)


def read_steering_text(path: Path, strip_frontmatter: bool = False) -> str:
    """Read a steering file's text, stripped, returning empty on error.

    If ``strip_frontmatter`` is True, strips leading YAML frontmatter
    (delimited by '---') per the .agents Protocol specification.
    """
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("Could not read file at %s", path)
        return ""

    if strip_frontmatter and text.startswith("---"):
        parts = text.split("---", 2)
        text = parts[2].strip() if len(parts) >= 3 else ""

    return text


def resolve_workspace_agents_file(
    cwd: Path | str | None = None,
) -> tuple[Path, str] | None:
    """Find workspace AGENTS.md in precedence order per .agents Protocol.

    Precedence:
    1. <cwd>/AGENTS.md
    2. <cwd>/agents.md
    3. <cwd>/.agents/AGENTS.md
    4. <cwd>/.agents/agents.md

    Returns (resolved_path, relative_display_path) or None if no valid
    non-empty file.
    """
    base = Path(cwd) if cwd else Path.cwd()
    if not base.is_dir():
        return None

    # Check root level first
    root_candidates: list[Path] = []
    try:
        for entry in base.iterdir():
            if entry.name in ("AGENTS.md", "agents.md") and entry.is_file():
                root_candidates.append(entry)
    except OSError:
        pass

    root_candidates.sort(key=lambda p: (0 if p.name == "AGENTS.md" else 1, p.name))
    for cand in root_candidates:
        content = read_steering_text(cand, strip_frontmatter=True)
        if content:
            return (cand, cand.name)

    # Fall back to .agents/
    dot_agents = base / ".agents"
    if dot_agents.is_dir():
        dot_candidates: list[Path] = []
        try:
            for entry in dot_agents.iterdir():
                if entry.name in ("AGENTS.md", "agents.md") and entry.is_file():
                    dot_candidates.append(entry)
        except OSError:
            pass

        dot_candidates.sort(key=lambda p: (0 if p.name == "AGENTS.md" else 1, p.name))
        for cand in dot_candidates:
            content = read_steering_text(cand, strip_frontmatter=True)
            if content:
                return (cand, f".agents/{cand.name}")

    return None


def resolve_global_agents_file(
    global_dir: Path | None = None,
) -> tuple[Path, str] | None:
    """Find global AGENTS.md per .agents Protocol.

    Precedence:
    1. (global_dir or <global>)/AGENTS.md
    2. (global_dir or <global>)/agents.md

    Returns (resolved_path, display_path) or None if no valid non-empty file.
    """
    g_dir = global_dir if global_dir is not None else global_agents_dir()
    if not g_dir.is_dir():
        return None

    candidates: list[Path] = []
    try:
        for entry in g_dir.iterdir():
            if entry.name in ("AGENTS.md", "agents.md") and entry.is_file():
                candidates.append(entry)
    except OSError:
        pass

    candidates.sort(key=lambda p: (0 if p.name == "AGENTS.md" else 1, p.name))
    for cand in candidates:
        content = read_steering_text(cand, strip_frontmatter=True)
        if content:
            # The home-relative shorthand is only accurate when the file
            # really is in the home layer; under a relocated global dir it
            # would name a path that was never read.
            in_home_layer = (
                global_dir is None
                and global_agents_dir() == Path("~/.agents").expanduser()
            )
            display = f"~/.agents/{cand.name}" if in_home_layer else cand.as_posix()
            return (cand, display)

    return None


def resolve_scoped_agents_file(
    target_path: Path | str,
    cwd: Path | str | None = None,
) -> tuple[Path, str] | None:
    """Resolve the nearest localized AGENTS.md for a target file or directory.

    Walks upward from target_path until cwd is reached, checking for
    AGENTS.md or agents.md at each directory level.
    """
    base_cwd = (Path(cwd) if cwd else Path.cwd()).resolve()
    target = Path(target_path).resolve()
    curr: Path = target if target.is_dir() else target.parent

    while True:
        if curr.is_dir():
            candidates: list[Path] = []
            try:
                for entry in curr.iterdir():
                    if entry.name in ("AGENTS.md", "agents.md") and entry.is_file():
                        candidates.append(entry)
            except OSError:
                pass

            candidates.sort(key=lambda p: (0 if p.name == "AGENTS.md" else 1, p.name))
            for cand in candidates:
                content = read_steering_text(cand, strip_frontmatter=True)
                if content:
                    try:
                        rel = cand.relative_to(base_cwd).as_posix()
                    except ValueError:
                        rel = cand.as_posix()
                    return (cand, rel)

        if curr == base_cwd or curr.parent == curr:
            break
        curr = curr.parent

    return None
