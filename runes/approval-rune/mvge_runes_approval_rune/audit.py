"""Durable, lock-protected audit log for every gated cast.

One JSON Lines record per gated cast, including automatic decisions. Denied
casts get an end event (denied) but never a start event that would imply
execution began. Rotation is by size and age, both configurable.

Audit records never carry raw secret-like fields (see normalization.redact),
full file contents, or the shell environment — only safe summaries and
digests.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

AUDIT_FILENAME = "audit.jsonl"
DEFAULT_MAX_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_AGE_DAYS = 30


class AuditError(Exception):
    """The audit append failed; the gate must deny unless overridden."""


class AuditLog:
    """Append-only JSONL audit log with rotation."""

    def __init__(
        self,
        data_dir: Path,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    ) -> None:
        """Initialize the instance."""
        self.data_dir = Path(data_dir)
        self.max_bytes = max_bytes
        self.max_age_days = max_age_days

    @property
    def audit_path(self) -> Path:
        """Audit path."""
        return self.data_dir / AUDIT_FILENAME

    @staticmethod
    def utcnow() -> str:
        """Utcnow."""
        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def append_decision(self, record: dict[str, Any]) -> None:
        """Durably append a decision record before execution."""
        self._append(dict(record))

    def append_outcome(self, cast_id: str, event: str, **extra: Any) -> None:
        """Append an outcome record: started, completed, failed, or denied."""
        record: dict[str, Any] = {
            "timestamp": self.utcnow(),
            "cast_id": cast_id,
            "execution": event,
        }
        record.update(extra)
        self._append(record)

    def _append(self, record: dict[str, Any]) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self.data_dir, 0o700)
            self._maybe_rotate()
            line = json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n"
            with open(self.audit_path, "a", encoding="utf-8") as handle:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(self.audit_path, 0o600)
        except OSError as exc:
            raise AuditError(f"audit append failed: {exc}") from exc

    def _maybe_rotate(self) -> None:
        path = self.audit_path
        if not path.exists():
            return
        try:
            stat_result = path.stat()
        except OSError:
            return
        too_big = self.max_bytes > 0 and stat_result.st_size >= self.max_bytes
        too_old = (
            self.max_age_days >= 0
            and (time.time() - stat_result.st_mtime) > self.max_age_days * 86400
        )
        if too_big or too_old:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            archive = self.data_dir / f"audit-{stamp}.jsonl"
            suffix = 0
            while archive.exists():
                suffix += 1
                archive = self.data_dir / f"audit-{stamp}-{suffix}.jsonl"
            try:
                os.replace(path, archive)
                os.chmod(archive, 0o600)
            except OSError:
                pass

    def recent(self, limit: int) -> list[dict[str, Any]]:
        """Newest-last slice of recent records for the permissions view."""
        path = self.audit_path
        if not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        records: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
        return records
