"""Argument normalization, digesting, and secret redaction.

The gate binds a decision to one immutable tuple of normalized arguments.
Normalization uses canonical JSON so key order and whitespace never change
the digest.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from typing import Any

REDACT_KEYWORDS = (
    "token",
    "password",
    "secret",
    "authoriz",
    "cookie",
    "credential",
    "privatekey",
    "private_key",
    "session",
    "api_key",
    "apikey",
)

REDACTED = "[redacted]"

SUMMARY_VALUE_MAX_CHARS = 80
SUMMARY_CONTENT_MAX_CHARS = 40


class NonSerializableArguments(Exception):
    """Raised when arguments cannot be canonically serialized."""


def plain_arguments(value: Any) -> Any:
    """Recursively convert mappings to plain dicts.

    The engine freezes arguments (nested ``MappingProxyType``); the
    policy layer works on plain dicts so redaction, summaries, and the
    audit JSON never see a non-serializable mapping. Scalars pass
    through untouched.
    """
    if isinstance(value, Mapping):
        return {str(key): plain_arguments(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain_arguments(item) for item in value]
    return value


def normalize_arguments(args: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Deep-copy arguments and return (canonical copy, sha256 digest).

    Canonical form is json.dumps(sort_keys=True, separators=(",",":"),
    ensure_ascii=True) per the spec.
    """
    frozen = copy.deepcopy(args)
    try:
        canonical = json.dumps(
            frozen, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
    except (TypeError, ValueError) as exc:
        raise NonSerializableArguments(
            f"arguments are not JSON-serializable: {exc}"
        ) from exc
    return frozen, digest_canonical(canonical)


def digest_canonical(canonical: str) -> str:
    """Hash already-canonical JSON, prefixed per spec."""
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def digest_arguments(args: dict[str, Any]) -> str:
    """Digest arguments without returning the copy."""
    _, digest = normalize_arguments(args)
    return digest


def _is_redact_key(key: str) -> bool:
    lowered = key.lower()
    return any(keyword in lowered for keyword in REDACT_KEYWORDS)


def redact(value: Any) -> Any:
    """Recursively redact secret-like fields."""
    if isinstance(value, dict):
        return {
            str(key): REDACTED if _is_redact_key(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


def _summarize_value(key: str, value: Any) -> Any:
    if _is_redact_key(key):
        return REDACTED
    if isinstance(value, str):
        limit = (
            SUMMARY_CONTENT_MAX_CHARS
            if len(value) > 200
            else SUMMARY_VALUE_MAX_CHARS
        )
        if len(value) > limit:
            return f"<{len(value)} chars: {digest_canonical(value)[:24]}>"
        return value
    if isinstance(value, dict):
        return {str(k): _summarize_value(str(k), v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return f"<list of {len(value)}>"
    return value


def argument_summary(args: dict[str, Any]) -> dict[str, Any]:
    """Safe per-field summary for audit and display: types and digests only.

    Never includes full file contents or raw secret-like values.
    """
    return {
        str(key): _summarize_value(str(key), value)
        for key, value in args.items()
    }
