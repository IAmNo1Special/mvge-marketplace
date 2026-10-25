"""Per-session state snapshot populated by the BEFORE_MVGE_START hook."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SelfmodState:
    """Hook-derived paths for one session.

    The engine rehydrates hook-derived instance state on ``Mvge.reload()``
    through a dedicated rehydrate path (NOT by refiring the prompt-mutating
    hook); ``populate_state`` in ``rune.py`` rebuilds this from the payload.
    """

    config_dir: Path | None
    runes_paths: list[Path] = field(default_factory=list)
    system_path: Path | None = None
    spells_dir: Path | None = None
    # Optional extras beyond the spec's four positional args (all defaulted,
    # so SelfmodState(config_dir, runes_paths, system_path, spells_dir)
    # stays compatible):
    # - agent_name: recorded in snapshot manifests (§7).
    # - cwd: the session's workspace root from the hook payload. Needed to
    #   resolve project-scope skill roots; the bare process cwd is never
    #   used (it may be unrelated to the session's workspace).
    agent_name: str | None = None
    cwd: Path | None = None
    # The instance lock does not survive reload either — moot, because no
    # two live instances ever hold valid state simultaneously.
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
