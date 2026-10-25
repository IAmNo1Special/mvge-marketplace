from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SteeringState:
    """Resolved repository steering snapshot for one working directory."""

    cwd: Path | None = None
    global_dir: Path | None = None
    workspace_ref: tuple[Path, str] | None = None
    global_ref: tuple[Path, str] | None = None
    subpackages: list[tuple[str, str]] = field(default_factory=list)
    section: str = ""
