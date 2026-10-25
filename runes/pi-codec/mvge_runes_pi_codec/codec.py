"""Native Pi session codec for Mvge.

Reads, validates, appends to, migrates, and forks Pi agent session logs
(JSONL v3/v4) directly — no conversion to Tome v1. Every behavior here
mirrors the compiled Pi source (``@earendil-works/pi-agent-core``,
``dist/harness/session``): strict validation on load, torn-tail repair at
open, atomic entry-plus-tip transactions, v3-to-v4 migration on first write,
and Pi-native forks.

Malcom's rule: match Pi unless he explicitly approves a deviation.
"""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import os
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mvge.tome.codec import AppendPlan, SessionCodec
from mvge.tome.types import TomeEntry, TomeEntryType, TomeMetadata, TomeVersionError

logger = logging.getLogger(__name__)


class PiCodecError(ValueError):
    """Strict Pi validation failure (mirrors Pi's open-time errors)."""


# --- UUIDv7 (RFC 9562) --------------------------------------------------------


def _uuidv7(timestamp_ms: int) -> str:
    """Generate a UUIDv7 preserving ``timestamp_ms``, Pi-style.

    Pi's generator mixes a monotonic sequence into the random bits; here
    cryptographic randomness is enough — the properties Pi relies on are
    the version/variant bits, the preserved timestamp, and uniqueness.
    """
    if not isinstance(timestamp_ms, int) or timestamp_ms < 0:
        raise ValueError(
            f"UUIDv7 timestamp must be a non-negative int: {timestamp_ms!r}"
        )
    rand = secrets.token_bytes(10)
    b = bytearray(16)
    b[0:6] = timestamp_ms.to_bytes(6, "big")
    b[6:16] = rand
    b[6] = (b[6] & 0x0F) | 0x70  # version 7
    b[8] = (b[8] & 0x3F) | 0x80  # variant 10
    h = b.hex()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


# --- small helpers ------------------------------------------------------------


def _is_safe_int(value: Any, minimum: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and -(2**53) < value < 2**53
        and value >= minimum
    )


def _parse_iso_ms(value: Any, where: str) -> int:
    """Parse an ISO-8601 timestamp to epoch milliseconds (Pi's Date.parse)."""
    if not isinstance(value, str):
        raise PiCodecError(f"{where}: invalid timestamp {value!r}")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise PiCodecError(f"{where}: invalid timestamp {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def _iso_from_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


def _empty_usage() -> dict[str, Any]:
    return {
        "input": 0,
        "output": 0,
        "cacheRead": 0,
        "cacheWrite": 0,
        "totalTokens": 0,
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0},
    }


def _add_usage(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    def num(d: dict[str, Any], key: str) -> float:
        value = d.get(key)
        return value if isinstance(value, (int, float)) else 0

    merged = {
        "input": num(left, "input") + num(right, "input"),
        "output": num(left, "output") + num(right, "output"),
        "cacheRead": num(left, "cacheRead") + num(right, "cacheRead"),
        "cacheWrite": num(left, "cacheWrite") + num(right, "cacheWrite"),
        "totalTokens": num(left, "totalTokens") + num(right, "totalTokens"),
        "cost": {
            k: num(left.get("cost") or {}, k) + num(right.get("cost") or {}, k)
            for k in ("input", "output", "cacheRead", "cacheWrite", "total")
        },
    }
    for key in ("cacheWrite1h", "reasoning"):
        if left.get(key) is not None or right.get(key) is not None:
            merged[key] = num(left, key) + num(right, key)
    return merged


# --- message mapping (Pi message <-> Mvge invocation payload) ---------------

_PI_STOP_TO_MVGE = {"toolUse": "spellUse", "deferred": "stop"}
_MVGE_STOP_TO_PI = {"spellUse": "toolUse"}
_VALID_MVGE_STOPS = frozenset({"stop", "spellUse", "length", "error", "aborted"})


def _user_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif block.get("type") == "image":
                data = block.get("data") or ""
                parts.append(
                    f"[image: {block.get('mimeType', 'unknown')}, "
                    f"{len(data)} bytes of base64 — full data preserved in pi_original]"
                )
        return "\n".join(p for p in parts if p)
    if content is None:
        return ""
    return str(content)


def _assistant_blocks(content: Any) -> list[dict[str, Any]]:
    """Pi assistant content -> Mvge content blocks (text + spell_cast)."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return [{"type": "text", "text": str(content) if content is not None else ""}]
    blocks: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            blocks.append({"type": "text", "text": str(block.get("text", ""))})
        elif btype == "toolCall":
            blocks.append(
                {
                    "type": "spell_cast",
                    "spell_cast": {
                        "name": str(block.get("name", "")),
                        "arguments": block.get("arguments", {}),
                    },
                }
            )
        elif btype == "image":
            blocks.append({"type": "text", "text": _user_text([block])})
    return blocks


def _pi_blocks(content: Any) -> list[dict[str, Any]]:
    """Mvge content blocks -> Pi assistant content (text + toolCall)."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return [{"type": "text", "text": str(content) if content is not None else ""}]
    blocks: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            blocks.append({"type": "text", "text": str(block.get("text", ""))})
        elif btype == "spell_cast":
            cast = block.get("spell_cast") or {}
            blocks.append(
                {
                    "type": "toolCall",
                    "name": str(cast.get("name", "")),
                    "arguments": cast.get("arguments", {}),
                }
            )
    return blocks


def _map_stop_reason(value: Any) -> str:
    mapped = _PI_STOP_TO_MVGE.get(value, value)
    return mapped if mapped in _VALID_MVGE_STOPS else "stop"


def _spell_result_payload(message: dict[str, Any]) -> dict[str, Any]:
    content = message.get("content")
    blocks: list[dict[str, Any]] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                blocks.append({"type": "text", "text": str(block.get("text", ""))})
            elif block.get("type") == "image":
                blocks.append({"type": "text", "text": _user_text([block])})
    elif isinstance(content, str):
        blocks = [{"type": "text", "text": content}]
    return {
        "role": "spellResult",
        "content": blocks,
        "spell_name": str(message.get("toolName") or ""),
        "spell_cast_id": str(message.get("toolCallId") or ""),
        "is_error": bool(message.get("isError")),
    }


def _invocation_to_pi_message(
    item: dict[str, Any], timestamp_ms: int
) -> dict[str, Any] | None:
    """Serialized Mvge invocation -> Pi message object (for retained tails)."""
    role = item.get("role")
    content = item.get("content")
    if role == "user":
        text = content if isinstance(content, str) else _user_text(content)
        return {"role": "user", "content": text, "timestamp": timestamp_ms}
    if role == "assistant":
        return {
            "role": "assistant",
            "content": _pi_blocks(content),
            "timestamp": timestamp_ms,
        }
    if role == "spellResult":
        return {
            "role": "toolResult",
            "toolName": str(item.get("spell_name") or ""),
            "toolCallId": str(item.get("spell_cast_id") or ""),
            "content": _pi_blocks(content),
            "isError": bool(item.get("is_error")),
            "timestamp": timestamp_ms,
        }
    return None


def _pi_message_to_invocation(message: dict[str, Any]) -> dict[str, Any] | None:
    """Pi message object -> serialized Mvge invocation (for retained tails)."""
    role = message.get("role")
    if role == "user":
        return {"role": "user", "content": _user_text(message.get("content"))}
    if role == "assistant":
        return {
            "role": "assistant",
            "content": _assistant_blocks(message.get("content")),
        }
    if role == "toolResult":
        return _spell_result_payload(message)
    return None


# --- per-session state --------------------------------------------------------


@dataclass
class _V3Migration:
    """Everything needed to migrate a v3 file on first append, Pi-style."""

    baseline_writes: list[dict[str, Any]]
    imported_usage: dict[str, Any]
    next_seq: int
    header_base: dict[str, Any]


@dataclass
class _PiSessionState:
    format: str  # "v3" | "v4"
    session_id: str
    next_seq: int = 1
    tips: dict[str, Any] = field(default_factory=dict)
    active_branch: str = "main"
    migration: _V3Migration | None = None
    # Current scalar value rows keyed by (namespace, key): the latest "set"
    # wins, "delete" removes the row. Used by fork() to project Pi's
    # current-state values the way Pi's own fork does.
    values: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)


# --- v3 normalization (mirrors Pi's jsonl/legacy-v3.js) -----------------------

_V3_RECORD_TYPES = frozenset(
    {
        "message",
        "custom",
        "custom_message",
        "branch_summary",
        "compaction",
        "model_change",
        "thinking_level_change",
        "active_tools_change",
        "session_info",
        "label",
    }
)
_V3_RETAINED_TYPES = _V3_RECORD_TYPES - frozenset(
    {
        "model_change",
        "thinking_level_change",
        "active_tools_change",
        "session_info",
        "label",
    }
)
_V4_ENTRY_TYPES = frozenset({"message", "compaction", "branch_summary", "custom"})
_THINKING_LEVELS = frozenset(
    {"off", "minimal", "low", "medium", "high", "xhigh", "max"}
)


class _RetainedIdResolver:
    """Resolve a legacy v3 node to its reminted id, or to its nearest retained
    ancestor's reminted id (None when there is none). Mirrors Pi."""

    def __init__(
        self, entries_by_id: dict[str, dict[str, Any]], reminted_ids: dict[str, str]
    ) -> None:
        self._entries_by_id = entries_by_id
        self._reminted_ids = reminted_ids
        self._resolved: dict[str, str | None] = {}

    def resolve(self, legacy_id: Any) -> str | None:
        if legacy_id is None:
            return None
        traversed: list[str] = []
        visited: set[str] = set()
        current = legacy_id
        resolved: str | None = None
        while current is not None:
            reminted = self._reminted_ids.get(current)
            if reminted is not None:
                resolved = reminted
                break
            if current in self._resolved:
                resolved = self._resolved[current]
                break
            if current in visited:
                raise PiCodecError(
                    f"Cycle in legacy v3 parent chain at entry: {current}"
                )
            visited.add(current)
            entry = self._entries_by_id.get(current)
            if entry is None:
                raise PiCodecError(f"Missing legacy v3 entry reference: {current}")
            traversed.append(current)
            current = entry.get("parentId")
        for tid in traversed:
            self._resolved[tid] = resolved
        return resolved


def _require_retained_id(resolver: _RetainedIdResolver, legacy_id: Any) -> str:
    resolved = resolver.resolve(legacy_id)
    if resolved is None:
        raise PiCodecError(
            f"Legacy v3 entry reference has no retained ancestor: {legacy_id}"
        )
    return resolved


def _imported_custom_message(entry: dict[str, Any]) -> dict[str, Any]:
    ts = _parse_iso_ms(entry.get("timestamp"), "legacy v3 custom_message")
    return {
        "role": "custom",
        "customType": entry.get("customType"),
        "content": entry.get("content"),
        "details": entry.get("details"),
        "display": entry.get("display"),
        "timestamp": ts,
    }


def _project_context_messages(
    entry: dict[str, Any], resolver: _RetainedIdResolver
) -> list[dict[str, Any]]:
    """Project one retained v3 entry to its context messages (Pi parity)."""
    etype = entry["type"]
    if etype == "message":
        return [entry["message"]]
    if etype == "custom_message":
        return [_imported_custom_message(entry)]
    if etype == "branch_summary":
        if not entry.get("summary"):
            return []
        from_id = entry.get("fromId")
        return [
            {
                "role": "branchSummary",
                "summary": entry["summary"],
                "fromId": None if from_id == "root" else resolver.resolve(from_id),
                "timestamp": _parse_iso_ms(entry.get("timestamp"), "branch_summary"),
            }
        ]
    if etype == "compaction":
        return [
            {
                "role": "compactionSummary",
                "summary": entry.get("summary"),
                "tokensBefore": entry.get("tokensBefore"),
                "timestamp": _parse_iso_ms(entry.get("timestamp"), "compaction"),
            }
        ]
    return []


def _materialize_retained_tail(
    compaction: dict[str, Any],
    entries_by_id: dict[str, dict[str, Any]],
    resolver: _RetainedIdResolver,
) -> list[dict[str, Any]]:
    """Build the v4 compaction retainedTail from the v3 parent chain."""
    first_kept = compaction.get("firstKeptEntryId")
    reversed_tail: list[dict[str, Any]] = []
    visited: set[str] = set()
    current = compaction.get("parentId")
    while current is not None:
        if current in visited:
            raise PiCodecError(f"Cycle in legacy v3 parent chain at entry: {current}")
        visited.add(current)
        entry = entries_by_id.get(current)
        if entry is None:
            raise PiCodecError(f"Missing legacy v3 parent entry: {current}")
        reversed_tail.append(entry)
        if current == first_kept:
            messages: list[dict[str, Any]] = []
            for tail_entry in reversed(reversed_tail):
                messages.extend(_project_context_messages(tail_entry, resolver))
            return messages
        current = entry.get("parentId")
    raise PiCodecError(
        f"Legacy v3 compaction {compaction.get('id')} firstKeptEntryId "
        f"is not on its parent branch: {first_kept}"
    )


def _normalize_v3_entry(
    entry: dict[str, Any],
    seq: int,
    entries_by_id: dict[str, dict[str, Any]],
    resolver: _RetainedIdResolver,
) -> dict[str, Any]:
    base = {
        "kind": "entry",
        "id": _require_retained_id(resolver, entry.get("id")),
        "parentId": resolver.resolve(entry.get("parentId")),
        "seq": seq,
        "timestamp": _parse_iso_ms(entry.get("timestamp"), "legacy v3 entry"),
    }
    etype = entry["type"]
    if etype == "message":
        return {**base, "type": "message", "message": entry["message"]}
    if etype == "custom_message":
        return {
            **base,
            "type": "message",
            "message": _imported_custom_message(entry),
        }
    if etype == "branch_summary":
        from_id = entry.get("fromId")
        return {
            **base,
            "type": "branch_summary",
            "fromId": None if from_id == "root" else resolver.resolve(from_id),
            "summary": entry.get("summary"),
            "details": entry.get("details"),
            "usage": entry.get("usage"),
            "fromHook": entry.get("fromHook", False),
        }
    if etype == "compaction":
        return {
            **base,
            "type": "compaction",
            "summary": entry.get("summary"),
            "retainedTail": _materialize_retained_tail(entry, entries_by_id, resolver),
            "tokensBefore": entry.get("tokensBefore"),
            "details": entry.get("details"),
            "usage": entry.get("usage"),
            "fromHook": entry.get("fromHook", False),
        }
    return {
        **base,
        "type": "custom",
        "customType": entry.get("customType"),
        "data": entry.get("data"),
    }


def _selected_v3_configuration(
    entries_by_id: dict[str, dict[str, Any]], selected_id: Any
) -> dict[str, Any] | None:
    model = thinking = None
    active_tools: list[str] | None = None
    saw = {"model": False, "thinking": False, "tools": False}
    visited: set[str] = set()
    current = selected_id
    while current is not None and not all(saw.values()):
        if current in visited:
            raise PiCodecError(f"Cycle in legacy v3 parent chain at entry: {current}")
        visited.add(current)
        entry = entries_by_id.get(current)
        if entry is None:
            raise PiCodecError(f"Missing legacy v3 entry reference: {current}")
        etype = entry["type"]
        if etype == "model_change" and not saw["model"]:
            saw["model"] = True
            provider, model_id = entry.get("provider"), entry.get("modelId")
            if (
                isinstance(provider, str)
                and provider
                and isinstance(model_id, str)
                and model_id
            ):
                model = {"provider": provider, "modelId": model_id}
        elif etype == "thinking_level_change" and not saw["thinking"]:
            saw["thinking"] = True
            level = entry.get("thinkingLevel")
            if isinstance(level, str) and level in _THINKING_LEVELS:
                thinking = level
        elif etype == "active_tools_change" and not saw["tools"]:
            saw["tools"] = True
            names = entry.get("activeToolNames")
            if isinstance(names, list) and all(isinstance(n, str) for n in names):
                active_tools = list(names)
        current = entry.get("parentId")
    if model is None or thinking is None:
        return None
    return {
        "model": model,
        "thinkingLevel": thinking,
        "activeToolNames": active_tools or [],
    }


def _normalize_v3_values(
    records: list[dict[str, Any]],
    entries_by_id: dict[str, dict[str, Any]],
    resolver: _RetainedIdResolver,
    first_seq: int,
) -> list[dict[str, Any]]:
    writes: list[dict[str, Any]] = []

    def value_write(namespace: str, key: str, value: Any) -> dict[str, Any]:
        write = {
            "kind": "value",
            "op": "set",
            "seq": first_seq + len(writes),
            "namespace": namespace,
            "key": key,
            "value": value,
        }
        return write

    latest_name: Any = None
    for record in records:
        if record["type"] == "session_info" and record.get("name"):
            latest_name = record["name"]
    if latest_name is not None:
        writes.append(value_write("pi.session.name", "", latest_name))
    labels: dict[str, Any] = {}
    for record in records:
        if record["type"] != "label":
            continue
        target = resolver.resolve(record.get("targetId"))
        if target is None:
            continue
        if record.get("label"):
            labels[target] = record["label"]
        else:
            labels.pop(target, None)
    for target, label in labels.items():
        writes.append(value_write("pi.entry.label", target, label))
    final = records[-1] if records else None
    writes.append(
        value_write(
            "pi.branch.tip",
            "main",
            resolver.resolve(final["id"]) if final is not None else None,
        )
    )
    config = _selected_v3_configuration(entries_by_id, final["id"] if final else None)
    if config is not None:
        writes.append(value_write("pi.lane.config", "main", config))
        writes.append(
            value_write(
                "pi.lane.state",
                "main",
                {"currentOperationId": None, "lastOperationId": None, "inbox": []},
            )
        )
    return writes


def _aggregate_v3_usage(records: list[dict[str, Any]]) -> dict[str, Any]:
    aggregate = _empty_usage()
    for record in records:
        usage = None
        if record["type"] == "message":
            role = (record.get("message") or {}).get("role")
            if role in ("assistant", "toolResult"):
                usage = (record["message"] or {}).get("usage")
        elif record["type"] in ("compaction", "branch_summary"):
            usage = record.get("usage")
        if isinstance(usage, dict):
            aggregate = _add_usage(aggregate, usage)
    return aggregate


# --- the codec ---------------------------------------------------------------


class PiSessionCodec(SessionCodec):
    """Native codec for Pi agent session logs (JSONL v3/v4).

    Detection, strict validation, torn-tail repair, atomic entry-plus-tip
    appends, v3-to-v4 migration on first write, Pi-native compaction entries,
    and Pi-native forks — all without converting to Tome v1.
    """

    name = "pi"
    file_suffix = ".jsonl"

    def __init__(self) -> None:
        self._sessions: dict[str, _PiSessionState] = {}

    # -- session state -----------------------------------------------------

    def _state_for_header(self, header: dict[str, Any]) -> _PiSessionState:
        sid = header.get("id")
        if not isinstance(sid, str) or not sid:
            raise PiCodecError("Pi header has no session id")
        state = self._sessions.get(sid)
        if state is None:
            raise PiCodecError(
                f"Unknown Pi session {sid!r}: parse its entries before appending"
            )
        return state

    def _register_state(self, state: _PiSessionState) -> None:
        self._sessions[state.session_id] = state

    # -- detection ----------------------------------------------------------

    def detect(self, header: dict[str, Any]) -> bool:
        return isinstance(header, dict) and (
            (header.get("kind") == "header" and header.get("v") == 4)
            or (header.get("type") == "session" and header.get("version") == 3)
        )

    def looks_like_session(self, header: dict[str, Any]) -> bool:
        return isinstance(header, dict) and (
            header.get("kind") == "header"
            or (header.get("type") == "session" and header.get("version") == 3)
        )

    # -- header --------------------------------------------------------------

    def parse_header(self, header: dict[str, Any]) -> TomeMetadata:
        try:
            if header.get("kind") == "header":
                return self._parse_v4_header(header)
            if header.get("type") == "session" and header.get("version") == 3:
                return self._parse_v3_header(header)
        except PiCodecError as exc:
            raise TomeVersionError(str(exc)) from exc
        version = header.get("v", header.get("version"))
        raise TomeVersionError(f"Unsupported Pi session version: {version!r}")

    def _parse_v4_header(self, header: dict[str, Any]) -> TomeMetadata:
        if header.get("v") != 4 or not isinstance(header.get("v"), int):
            raise PiCodecError(
                f"Invalid Pi v4 header: unsupported version {header.get('v')!r}"
            )
        if not isinstance(header.get("id"), str) or not isinstance(
            header.get("cwd"), str
        ):
            raise PiCodecError("Invalid Pi v4 header: id and cwd must be strings")
        storage_version = header.get("storageVersion")
        if (
            not isinstance(storage_version, int)
            or isinstance(storage_version, bool)
            or storage_version != 1
        ):
            raise PiCodecError(
                "Invalid Pi v4 header: unsupported "
                f"storageVersion {header.get('storageVersion')!r}"
            )
        if not _is_safe_int(header.get("createdAt"), 0):
            raise PiCodecError("Invalid Pi v4 header: createdAt must be an integer")
        next_seq = header.get("nextSeq")
        if next_seq is not None and not _is_safe_int(next_seq, 1):
            raise PiCodecError(
                f"Invalid Pi v4 header: nextSeq must be >= 1, got {next_seq!r}"
            )
        for opt in ("parentSessionId", "legacyParentSessionPath"):
            value = header.get(opt)
            if value is not None and not isinstance(value, str):
                raise PiCodecError(f"Invalid Pi v4 header: {opt} must be a string")
        return TomeMetadata(
            id=header["id"],
            created_at=_iso_from_ms(header["createdAt"]),
            cwd=header["cwd"],
            parent_tome_id=header.get("parentSessionId"),
        )

    def _parse_v3_header(self, header: dict[str, Any]) -> TomeMetadata:
        if not isinstance(header.get("id"), str) or not isinstance(
            header.get("cwd"), str
        ):
            raise PiCodecError("Invalid legacy v3 header: id and cwd must be strings")
        created_ms = _parse_iso_ms(header.get("timestamp"), "legacy v3 header")
        parent_id: str | None = None
        parent_path = header.get("parentSession")
        if isinstance(parent_path, str) and parent_path:
            try:
                with open(parent_path, encoding="utf-8") as f:
                    first = f.readline()
            except OSError:
                first = ""
            if first.strip():
                try:
                    obj = json.loads(first)
                except ValueError:
                    obj = None
                if isinstance(obj, dict) and isinstance(obj.get("id"), str):
                    parent_id = obj["id"]
        return TomeMetadata(
            id=header["id"],
            created_at=_iso_from_ms(created_ms),
            cwd=header["cwd"],
            parent_tome_id=parent_id,
        )

    # -- entries ---------------------------------------------------------------

    def parse_entries(
        self, header: dict[str, Any], lines: list[str], source: str = "<pi>"
    ) -> list[TomeEntry]:
        if header.get("kind") == "header" and header.get("v") == 4:
            return self._parse_v4_entries(header, lines, source)
        if header.get("type") == "session" and header.get("version") == 3:
            return self._parse_v3_entries(header, lines, source)
        raise TomeVersionError(
            "PiSessionCodec cannot parse header: "
            f"{header.get('v', header.get('version'))!r}"
        )

    def _parse_v4_entries(
        self, header: dict[str, Any], lines: list[str], source: str
    ) -> list[TomeEntry]:
        state = _PiSessionState(format="v4", session_id=header["id"])
        entries: list[TomeEntry] = []
        entries_by_id: dict[str, dict[str, Any]] = {}
        seen_ids: set[str] = set()
        prev_seq = 0
        for line_no, raw in enumerate(lines, start=2):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                value = json.loads(stripped)
            except ValueError as exc:
                raise PiCodecError(
                    f"Invalid JSONL transaction at line {line_no} "
                    f"in {source}: not valid JSON"
                ) from exc
            writes = value if isinstance(value, list) else [value]
            for write in writes:
                self._validate_write(write, line_no, source)
                seq = write["seq"]
                if seq <= prev_seq:
                    raise PiCodecError(
                        f"Non-monotonic storage sequence at line {line_no} in "
                        f"{source}: {seq} follows {prev_seq}"
                    )
                prev_seq = seq
                kind = write["kind"]
                if kind in ("entry", "usage"):
                    wid = write.get("id")
                    if wid in seen_ids:
                        raise PiCodecError(
                            f"Duplicate entry or usage id {wid!r} in {source}"
                        )
                    seen_ids.add(wid)
                if kind == "entry":
                    parent = write.get("parentId")
                    if parent is not None and parent not in entries_by_id:
                        raise PiCodecError(
                            f"Missing parent entry {parent!r} for {write.get('id')!r} "
                            f"in {source}"
                        )
                    if write.get("type") not in _V4_ENTRY_TYPES:
                        raise PiCodecError(
                            f"Unsupported v4 entry type {write.get('type')!r} "
                            f"at line {line_no} in {source}"
                        )
                    entries.append(self._tome_entry_from_write(write))
                    entries_by_id[write["id"]] = write
                elif kind == "value" and write.get("namespace") == "pi.branch.tip":
                    state.tips[write["key"]] = write["value"]
                if kind == "value":
                    # Track the current scalar row per (namespace, key): Pi's
                    # fork copies only current rows, never superseded/deleted
                    # ones. Validation above guarantees op is set or delete.
                    addr = (write.get("namespace"), write.get("key"))
                    if write.get("op") == "set":
                        state.values[addr] = copy.deepcopy(write)
                    else:
                        state.values.pop(addr, None)
        next_seq = prev_seq + 1
        header_next = header.get("nextSeq")
        if isinstance(header_next, int) and header_next > next_seq:
            next_seq = header_next
        state.next_seq = next_seq
        state.active_branch = self._pick_branch(state.tips)
        self._register_state(state)
        return entries

    def _validate_write(self, write: Any, line_no: int, source: str) -> None:
        if not isinstance(write, dict):
            raise PiCodecError(
                f"Invalid JSONL transaction at line {line_no} in {source}: "
                "each write must be an object"
            )
        if not _is_safe_int(write.get("seq"), 1):
            raise PiCodecError(
                f"Invalid JSONL write seq at line {line_no} in {source}: "
                f"{write.get('seq')!r}"
            )
        kind = write.get("kind")
        if kind == "entry":
            if not _is_safe_int(write.get("timestamp"), 0):
                raise PiCodecError(
                    f"Invalid JSONL entry timestamp at line {line_no} in {source}"
                )
        elif kind == "usage":
            return
        elif kind == "value":
            if write.get("op") not in ("set", "delete"):
                raise PiCodecError(
                    f"Invalid JSONL value operation {write.get('op')!r} "
                    f"at line {line_no} in {source}"
                )
        elif kind == "list":
            if write.get("op") not in ("append", "delete"):
                raise PiCodecError(
                    f"Invalid JSONL list operation {write.get('op')!r} "
                    f"at line {line_no} in {source}"
                )
        else:
            raise PiCodecError(
                f"Invalid JSONL write kind {kind!r} at line {line_no} in {source}"
            )

    @staticmethod
    def _pick_branch(tips: dict[str, Any]) -> str:
        if "main" in tips:
            return "main"
        if len(tips) == 1:
            return next(iter(tips))
        return "main"

    def _parse_v3_entries(
        self, header: dict[str, Any], lines: list[str], source: str
    ) -> list[TomeEntry]:
        records: list[dict[str, Any]] = []
        for line_no, raw in enumerate(lines, start=2):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except ValueError as exc:
                raise PiCodecError(
                    f"Invalid legacy v3 JSONL record at line {line_no} in "
                    f"{source}: not valid JSON"
                ) from exc
            rtype = record.get("type")
            if rtype not in _V3_RECORD_TYPES:
                raise PiCodecError(
                    f"Unsupported legacy v3 record type {rtype!r} "
                    f"at line {line_no} in {source}"
                )
            records.append(record)
        entries_by_id: dict[str, dict[str, Any]] = {}
        for record in records:
            rid = record.get("id")
            if not isinstance(rid, str) or not rid:
                raise PiCodecError(
                    f"Invalid legacy v3 record at line {source}: id must be a string"
                )
            if rid in entries_by_id:
                raise PiCodecError(f"Duplicate legacy v3 entry id {rid!r} in {source}")
            entries_by_id[rid] = record
        retained = [r for r in records if r["type"] in _V3_RETAINED_TYPES]
        reminted = {
            r["id"]: _uuidv7(_parse_iso_ms(r.get("timestamp"), "legacy v3 entry"))
            for r in retained
        }
        resolver = _RetainedIdResolver(entries_by_id, reminted)
        baseline: list[dict[str, Any]] = []
        for i, record in enumerate(retained, start=1):
            baseline.append(_normalize_v3_entry(record, i, entries_by_id, resolver))
        baseline.extend(
            _normalize_v3_values(records, entries_by_id, resolver, len(baseline) + 1)
        )
        next_seq = len(baseline) + 1
        header_base = self._v4_header_base_for_v3(header)
        tips: dict[str, Any] = {}
        for write in baseline:
            if write["kind"] == "value" and write["namespace"] == "pi.branch.tip":
                tips[write["key"]] = write["value"]
        state = _PiSessionState(
            format="v3",
            session_id=header["id"],
            next_seq=next_seq,
            tips=tips,
            active_branch=self._pick_branch(tips),
            migration=_V3Migration(
                baseline_writes=baseline,
                imported_usage=_aggregate_v3_usage(records),
                next_seq=next_seq,
                header_base=header_base,
            ),
        )
        # The normalized baseline value writes are already the current rows
        # (latest name, latest label per entry, current tip, lane config).
        for write in baseline:
            if write["kind"] == "value":
                state.values[(write["namespace"], write["key"])] = copy.deepcopy(write)
        self._register_state(state)
        return [
            self._tome_entry_from_write(w) for w in baseline if w["kind"] == "entry"
        ]

    def _v4_header_base_for_v3(self, header: dict[str, Any]) -> dict[str, Any]:
        meta = self._parse_v3_header(header)
        created_ms = _parse_iso_ms(header.get("timestamp"), "legacy v3 header")
        base: dict[str, Any] = {
            "v": 4,
            "kind": "header",
            "id": header["id"],
            "createdAt": created_ms,
            "storageVersion": 1,
            "cwd": header["cwd"],
        }
        if meta.parent_tome_id is not None:
            base["parentSessionId"] = meta.parent_tome_id
        parent_path = header.get("parentSession")
        if isinstance(parent_path, str) and parent_path:
            base["legacyParentSessionPath"] = parent_path
        return base

    def _tome_entry_from_write(self, write: dict[str, Any]) -> TomeEntry:
        entry_type = write["type"]
        payload: dict[str, Any] = {
            "pi_original": copy.deepcopy(write),
            "pi_seq": write["seq"],
        }
        timestamp = write["timestamp"] / 1000.0
        if entry_type == "message":
            message = write.get("message") or {}
            role = message.get("role")
            if role == "user":
                payload.update(role="user", content=_user_text(message.get("content")))
                tome_type = TomeEntryType.MESSAGE
            elif role == "assistant":
                payload.update(
                    role="assistant",
                    content=_assistant_blocks(message.get("content")),
                    stop_reason=_map_stop_reason(message.get("stopReason")),
                )
                tome_type = TomeEntryType.MESSAGE
            elif role == "toolResult":
                payload.update(_spell_result_payload(message))
                tome_type = TomeEntryType.MESSAGE
            else:
                custom_type = (
                    message.get("customType")
                    if role == "custom"
                    else (role if isinstance(role, str) else "unknown")
                )
                payload.update(type=custom_type, data={"message": message})
                tome_type = TomeEntryType.CUSTOM
        elif entry_type == "compaction":
            tail: list[dict[str, Any]] = []
            for item in write.get("retainedTail") or []:
                if not isinstance(item, dict):
                    continue
                invocation = _pi_message_to_invocation(item)
                if invocation is not None:
                    tail.append(invocation)
            payload.update(
                summary=write.get("summary", ""),
                manaBefore=write.get("tokensBefore", 0),
                retainedTail=tail,
            )
            tome_type = TomeEntryType.COMPACTION
        elif entry_type == "branch_summary":
            payload.update(
                type="branch_summary",
                data={"summary": write.get("summary"), "fromId": write.get("fromId")},
            )
            tome_type = TomeEntryType.CUSTOM
        else:  # custom
            payload.update(type=write.get("customType"), data=write.get("data"))
            tome_type = TomeEntryType.CUSTOM
        return TomeEntry(
            id=write["id"],
            parent_id=write.get("parentId"),
            type=tome_type,
            timestamp=timestamp,
            payload=payload,
        )

    # -- torn-tail repair ------------------------------------------------------

    def repair_on_open(self, source: str) -> bool:
        try:
            content = Path(source).read_bytes()
        except OSError:
            return False
        if not content or content.endswith(b"\n"):
            return False
        idx = content.rfind(b"\n")
        complete = content[: idx + 1] if idx != -1 else b""
        path = Path(source)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_bytes(complete)
            os.replace(tmp, source)
            return True
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink()

    # -- leaf / tip --------------------------------------------------------------

    def leaf_id(self, header: dict[str, Any], entries: list[TomeEntry]) -> str | None:
        sid = header.get("id") if isinstance(header, dict) else None
        state = self._sessions.get(sid) if isinstance(sid, str) else None
        if state is not None:
            tip = state.tips.get(state.active_branch)
            return tip if isinstance(tip, str) and tip else None
        if entries:
            return entries[-1].id
        return None

    def append_tip_lines(
        self,
        header: dict[str, Any],
        entries: list[TomeEntry],
        leaf: TomeEntry,
    ) -> list[dict[str, Any]] | None:
        state = self._state_for_header(header)
        target = leaf.payload.get("targetId")
        if not isinstance(target, str) or not target:
            raise PiCodecError("Pi append_leaf requires a targetId payload")
        branch = state.active_branch
        if state.tips.get(branch) == target:
            return []
        write = {
            "kind": "value",
            "op": "set",
            "seq": state.next_seq,
            "namespace": "pi.branch.tip",
            "key": branch,
            "value": target,
        }
        state.next_seq += 1
        state.tips[branch] = target
        return [write]

    def apply_leaf(
        self,
        header: dict[str, Any],
        entries: list[TomeEntry],
        leaf: TomeEntry,
    ) -> tuple[dict[str, Any], list[TomeEntry]]:
        # Pi always routes tip changes through append_tip_lines, so the
        # handle never reaches this fallback; keep it a documented no-op.
        return header, entries

    # -- appends --------------------------------------------------------------

    def plan_append(
        self,
        entry: TomeEntry,
        existing: list[TomeEntry],
        header: dict[str, Any],
    ) -> AppendPlan:
        state = self._state_for_header(header)
        if state.format == "v3":
            return self._plan_v3_migration(entry, state)
        return self._plan_v4_append(entry, state)

    def _plan_v4_append(self, entry: TomeEntry, state: _PiSessionState) -> AppendPlan:
        branch = state.active_branch
        tip = state.tips.get(branch)
        # A v4 file with no branch tip yet (e.g. header-only) starts a new
        # root; the tip write below establishes the branch going forward.
        parent_id = tip if isinstance(tip, str) and tip else None
        ts_ms = int(entry.timestamp * 1000) if entry.timestamp else 0
        if ts_ms <= 0:
            ts_ms = int(datetime.now(UTC).timestamp() * 1000)
        entry_id = _uuidv7(ts_ms)
        entry_body = pi_entry_body(entry, entry_id, parent_id, ts_ms)
        seq = state.next_seq
        entry_write = {"kind": "entry", "seq": seq, "timestamp": ts_ms, **entry_body}
        tip_write = {
            "kind": "value",
            "op": "set",
            "seq": seq + 1,
            "namespace": "pi.branch.tip",
            "key": branch,
            "value": entry_id,
        }
        stored = TomeEntry(
            id=entry_id,
            parent_id=parent_id,
            type=entry.type,
            timestamp=entry.timestamp,
            payload={**entry.payload, "pi_seq": seq},
        )
        state.next_seq = seq + 2
        state.tips[branch] = entry_id
        return AppendPlan(lines=[[entry_write, tip_write]], stored=stored)

    def _plan_v3_migration(
        self, entry: TomeEntry, state: _PiSessionState
    ) -> AppendPlan:
        migration = state.migration
        if migration is None:
            raise PiCodecError(
                f"Pi session {state.session_id!r} is v3 but has no migration plan"
            )
        v4_state = _PiSessionState(
            format="v4",
            session_id=state.session_id,
            next_seq=migration.next_seq,
            tips=dict(state.tips),
            active_branch=state.active_branch,
        )
        sub = self._plan_v4_append(entry, v4_state)
        caller_writes = list(sub.lines[0])
        ts_ms = int(entry.timestamp * 1000) if entry.timestamp else 0
        if ts_ms <= 0:
            ts_ms = int(datetime.now(UTC).timestamp() * 1000)
        adjustment = {
            "kind": "usage",
            "id": _uuidv7(ts_ms),
            "seq": migration.next_seq,
            "usage": migration.imported_usage,
            "adjustment": True,
            "details": {"source": "v3-import"},
        }
        for write in caller_writes:
            write["seq"] += 1
        transaction = [adjustment, *caller_writes]
        new_header = {
            **migration.header_base,
            "nextSeq": migration.next_seq + 1 + len(caller_writes),
        }
        lines: list[Any] = [*migration.baseline_writes, transaction]
        state.format = "v4"
        state.next_seq = new_header["nextSeq"]
        state.tips = v4_state.tips
        state.migration = None
        return AppendPlan(
            lines=lines, stored=sub.stored, rewrite=True, header=new_header
        )

    # -- serialization ---------------------------------------------------------

    def serialize_entry(self, entry: TomeEntry) -> dict[str, Any] | None:
        original = entry.payload.get("pi_original")
        if isinstance(original, dict) and original.get("kind") == "entry":
            return copy.deepcopy(original)
        return None

    def serialize_new_entry(
        self, entry: TomeEntry, existing: list[TomeEntry]
    ) -> tuple[dict[str, Any], TomeEntry]:
        # Pi overrides plan_append, so this only satisfies the protocol.
        body = pi_entry_body(
            entry, entry.id, entry.parent_id or "", int(entry.timestamp * 1000)
        )
        return body, entry

    # -- fork ---------------------------------------------------------------------

    def fork(
        self,
        *,
        source: str,
        dest_dir: str,
        new_id: str,
        leaf_id: str | None = None,
        cwd: str | None = None,
    ) -> str:
        self.repair_on_open(source)
        raw = Path(source).read_text(encoding="utf-8").splitlines()
        if not raw:
            raise PiCodecError(f"Cannot fork empty Pi session: {source}")
        header = json.loads(raw[0])
        entries = self.parse_entries(header, raw[1:], source=source)
        v4_header = self._coerce_v4_header(header)
        fork_from = leaf_id or self.leaf_id(v4_header, entries)
        if fork_from is None:
            raise PiCodecError(f"Cannot fork Pi session with no leaf: {source}")
        retained_ids: set[str] = set()
        by_id = {e.id: e for e in entries}
        current: str | None = fork_from
        while current is not None:
            if current in retained_ids:
                raise PiCodecError(f"Cycle in Pi parent chain at {current!r}")
            retained_ids.add(current)
            entry = by_id.get(current)
            if entry is None:
                raise PiCodecError(
                    f"Fork leaf {fork_from!r} is not reachable: missing {current!r}"
                )
            current = entry.parent_id
        state = self._state_for_header(v4_header)
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        new_header: dict[str, Any] = {
            "v": 4,
            "kind": "header",
            "id": new_id,
            "createdAt": now_ms,
            "storageVersion": 1,
            "cwd": cwd or v4_header.get("cwd", ""),
            "parentSessionId": v4_header.get("id"),
            "nextSeq": 1,
        }
        lines: list[Any] = []
        seq = 1
        for entry in entries:
            if entry.id not in retained_ids:
                continue
            original = entry.payload.get("pi_original")
            if not isinstance(original, dict):
                raise PiCodecError(
                    f"Cannot fork Pi entry {entry.id!r}: no original Pi write"
                )
            write = copy.deepcopy(original)
            write["seq"] = seq
            lines.append(write)
            seq += 1
        branch = state.active_branch
        # Project the source's current scalar values the way Pi's own fork
        # does (see pi's fork-policy.ts projectForkCurrentStateWrite): session
        # name always copies; labels copy only for retained entries; the
        # forked branch keeps its tip (retargeted), config, and a cleared
        # lane state; results, operation, and pending namespaces are dropped;
        # unknown reserved pi.* namespaces are a hard error; non-Pi custom
        # values do not copy in branch scope.
        tip_written = False
        for (namespace, key), write in state.values.items():
            projected = self._project_fork_value(
                write,
                namespace,
                key,
                branch=branch,
                fork_from=fork_from,
                retained_ids=retained_ids,
            )
            if projected is None:
                continue
            if namespace == "pi.branch.tip":
                tip_written = True
            projected = copy.deepcopy(projected)
            projected["seq"] = seq
            lines.append(projected)
            seq += 1
        if not tip_written:
            # Source recorded no branch tip (e.g. a bare session): still leave
            # the fork with a tip so it opens at the forked leaf.
            lines.append(
                {
                    "kind": "value",
                    "op": "set",
                    "seq": seq,
                    "namespace": "pi.branch.tip",
                    "key": branch,
                    "value": fork_from,
                }
            )
            seq += 1
        new_header["nextSeq"] = seq
        dest = Path(dest_dir) / f"{new_id}.jsonl"
        tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                f.write(json.dumps(new_header) + "\n")
                for line in lines:
                    f.write(json.dumps(line) + "\n")
            os.replace(tmp, dest)
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink()
        return str(dest)

    @staticmethod
    def _project_fork_value(
        write: dict[str, Any],
        namespace: Any,
        key: Any,
        *,
        branch: str,
        fork_from: str,
        retained_ids: set[str],
    ) -> dict[str, Any] | None:
        """Project one current scalar value write into a branch fork.

        Mirrors Pi's ``projectForkCurrentStateWrite`` for branch scope.
        Returns the write to copy, or None to drop it.
        """
        if namespace == "pi.session.name":
            return write
        if namespace == "pi.entry.label":
            return write if key in retained_ids else None
        if namespace == "pi.branch.tip":
            if key != branch:
                return None
            projected = dict(write)
            projected["value"] = fork_from
            return projected
        if namespace == "pi.lane.config":
            return write if key == branch else None
        if namespace == "pi.lane.state":
            if key != branch:
                return None
            projected = dict(write)
            projected["value"] = {
                "currentOperationId": None,
                "lastOperationId": None,
                "inbox": [],
            }
            return projected
        if namespace == "pi.result":
            return None
        if isinstance(namespace, str) and namespace.startswith(
            ("pi.op.", "pi.pending.")
        ):
            return None
        if namespace == "pi" or (
            isinstance(namespace, str) and namespace.startswith("pi.")
        ):
            raise PiCodecError(f"Unknown reserved fork namespace: {namespace!r}")
        # Branch scope never copies non-Pi custom values.
        return None

    def _coerce_v4_header(self, header: dict[str, Any]) -> dict[str, Any]:
        if header.get("kind") == "header" and header.get("v") == 4:
            return header
        meta = self._parse_v3_header(header)
        return {
            "v": 4,
            "kind": "header",
            "id": meta.id,
            "createdAt": _parse_iso_ms(header.get("timestamp"), "legacy v3 header"),
            "storageVersion": 1,
            "cwd": meta.cwd,
        }


def pi_entry_body(
    entry: TomeEntry, entry_id: str, parent_id: str | None, ts_ms: int
) -> dict[str, Any]:
    """Translate one Mvge TomeEntry into a Pi v4 entry body (no seq).

    Shared by the codec's append planning and by pi-export, so an exported
    file and a natively-appended entry speak the same Pi.
    """
    base = {"id": entry_id, "parentId": parent_id}
    payload = entry.payload
    if entry.type == TomeEntryType.MESSAGE:
        role = payload.get("role")
        if role == "user":
            content = payload.get("content")
            message = {
                "role": "user",
                "content": content if isinstance(content, str) else _pi_blocks(content),
                "timestamp": ts_ms,
            }
        elif role == "assistant":
            stop = str(payload.get("stop_reason") or "stop")
            message = {
                "role": "assistant",
                "content": _pi_blocks(payload.get("content")),
                "stopReason": _MVGE_STOP_TO_PI.get(stop, stop),
                "timestamp": ts_ms,
            }
        elif role == "spellResult":
            message = {
                "role": "toolResult",
                "toolName": str(payload.get("spell_name") or ""),
                "toolCallId": str(payload.get("spell_cast_id") or ""),
                "content": _pi_blocks(payload.get("content")),
                "isError": bool(payload.get("is_error")),
                "timestamp": ts_ms,
            }
        else:
            raise PiCodecError(
                f"Cannot append Pi message with role {role!r}: "
                "use user, assistant, or spellResult"
            )
        return {**base, "type": "message", "message": message}
    if entry.type == TomeEntryType.COMPACTION:
        tail: list[dict[str, Any]] = []
        for item in payload.get("retainedTail") or []:
            if not isinstance(item, dict):
                continue
            tail_message = _invocation_to_pi_message(item, ts_ms)
            if tail_message is not None:
                tail.append(tail_message)
        return {
            **base,
            "type": "compaction",
            "summary": str(payload.get("summary", "")),
            "retainedTail": tail,
            "tokensBefore": payload.get("manaBefore", 0),
            "fromHook": False,
        }
    if entry.type == TomeEntryType.CUSTOM:
        return {
            **base,
            "type": "custom",
            "customType": payload.get("type"),
            "data": payload.get("data"),
        }
    if entry.type == TomeEntryType.TOME_INFO:
        return {
            **base,
            "type": "custom",
            "customType": "mvge.tome_info",
            "data": dict(payload),
        }
    raise PiCodecError(f"Cannot append {entry.type.value} entries to a Pi session")
