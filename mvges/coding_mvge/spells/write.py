from __future__ import annotations

import asyncio
from pathlib import Path

from mvge.core.spells import (
    SpellResult,
    SpellStatus,
)


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


async def write(path: str, content: str) -> SpellResult:
    """Write full content to a file at the specified path."""
    try:
        file_path = Path(path)
        await asyncio.to_thread(_write_file, file_path, content)
        return SpellResult(
            spell_name="write",
            status=SpellStatus.SUCCESS,
            content=f"Wrote to {path}",
            details={"path": str(path)},
        )
    except Exception as exc:  # noqa: BLE001 -- spell contract: return ERROR SpellResult instead of raising
        return SpellResult(
            spell_name="write",
            status=SpellStatus.ERROR,
            error_message=str(exc),
        )
