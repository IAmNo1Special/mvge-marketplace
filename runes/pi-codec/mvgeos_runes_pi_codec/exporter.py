"""One-way export: MvgeOS Tome v1 -> Pi-native v4 session file.

Export is a copy, not synchronization. Every Tome entry is reminted with a
fresh Pi-style UUIDv7 id, parent chains are preserved, and the result is a
v4 file that real Pi can open directly. LEAF branch markers have no Pi
equivalent and are skipped (orphaned children re-root onto the nearest kept
ancestor).
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mvge.core import sessions_dir
from mvge.tome.handle import TomeHandleFactory
from mvge.tome.types import TomeEntry, TomeEntryType

from mvgeos_runes_pi_codec.codec import _uuidv7, pi_entry_body


class ExportError(Exception):
    """Raised when a Tome cannot be exported to Pi."""


@dataclass
class ExportReport:
    tome_id: str
    pi_path: str
    pi_session_id: str
    entries: int
    skipped: int = 0
    by_type: dict[str, int] = field(default_factory=dict)


def _ts_ms(entry: TomeEntry) -> int:
    ms = int(entry.timestamp * 1000)
    return ms if ms > 0 else int(datetime.now(UTC).timestamp() * 1000)


def export_tome_to_pi(
    tome_id: str,
    *,
    tome_dir: Path | str | None = None,
    output: Path | str | None = None,
    force: bool = False,
) -> ExportReport:
    """Export a Tome v1 session to a new Pi-native v4 file."""
    directory = Path(tome_dir).expanduser() if tome_dir else sessions_dir()
    factory = TomeHandleFactory(directory)
    try:
        handle = factory.open_read(tome_id)
    except (FileNotFoundError, ValueError) as exc:
        raise ExportError(f"Tome not found: {tome_id}") from exc
    meta = handle.get_metadata()
    if meta is None:
        raise ExportError(f"Tome has no readable header: {tome_id}")
    entries = handle.get_entries()

    dest = Path(output).expanduser() if output else Path.cwd() / f"{meta.id}.pi.jsonl"
    if dest.exists() and not force:
        raise ExportError(f"Output exists (use --force to overwrite): {dest}")

    # Keep everything except LEAF branch markers, which have no Pi equivalent.
    kept = [e for e in entries if e.type != TomeEntryType.LEAF]
    skipped = len(entries) - len(kept)
    kept_ids = {e.id for e in kept}
    for e in kept:
        if e.type == TomeEntryType.LABEL:
            raise ExportError(
                f"Cannot export LABEL entry {e.id!r}: no Pi equivalent exists"
            )

    # Remint Pi-style ids; re-root orphans onto the nearest kept ancestor.
    new_id: dict[str, str] = {}
    used: set[str] = set()
    for e in kept:
        candidate = _uuidv7(_ts_ms(e))
        while candidate in used:
            candidate = _uuidv7(_ts_ms(e))
        new_id[e.id] = candidate
        used.add(candidate)

    # Resolve each kept entry's parent to the nearest kept ancestor
    # (a skipped LEAF's child re-roots onto the LEAF's own parent chain).
    old_by_id = {e.id: e for e in entries}

    def nearest_kept_ancestor(entry: TomeEntry) -> str | None:
        current: str | None = entry.parent_id
        seen: set[str] = set()
        while current is not None and current not in kept_ids:
            if current in seen:
                raise ExportError(f"Cycle in Tome parent chain at {current!r}")
            seen.add(current)
            parent_entry = old_by_id.get(current)
            current = parent_entry.parent_id if parent_entry else None
        return new_id[current] if current is not None else None

    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    session_id = _uuidv7(now_ms)
    writes: list[dict[str, Any]] = []
    seq = 1
    by_type: dict[str, int] = {}
    for e in kept:
        body = pi_entry_body(e, new_id[e.id], nearest_kept_ancestor(e), _ts_ms(e))
        writes.append(
            {
                "kind": "entry",
                "seq": seq,
                "timestamp": _ts_ms(e),
                **body,
            }
        )
        seq += 1
        by_type[e.type.value] = by_type.get(e.type.value, 0) + 1

    if kept:
        writes.append(
            {
                "kind": "value",
                "op": "set",
                "seq": seq,
                "namespace": "pi.branch.tip",
                "key": "main",
                "value": new_id[kept[-1].id],
                "timestamp": now_ms,
            }
        )
        seq += 1

    header = {
        "v": 4,
        "kind": "header",
        "id": session_id,
        "createdAt": now_ms,
        "storageVersion": 1,
        "cwd": meta.cwd or "",
        "nextSeq": seq,
    }

    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(header, separators=(",", ":")) + "\n")
            f.writelines(json.dumps(w, separators=(",", ":")) + "\n" for w in writes)
        os.replace(tmp, dest)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()

    return ExportReport(
        tome_id=meta.id,
        pi_path=str(dest),
        pi_session_id=session_id,
        entries=len(kept),
        skipped=skipped,
        by_type=by_type,
    )
