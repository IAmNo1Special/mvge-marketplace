"""Tests for argument normalization, digesting, and redaction."""

from typing import Any

import pytest

from mvge_runes_approval_rune.normalization import (
    NonSerializableArguments,
    argument_summary,
    digest_arguments,
    normalize_arguments,
    redact,
)


def test_normalize_is_canonical() -> None:
    """Test normalize is canonical."""
    a, digest_a = normalize_arguments({"b": 2, "a": 1})
    b, digest_b = normalize_arguments({"a": 1, "b": 2})
    assert a == {"a": 1, "b": 2}
    assert digest_a == digest_b
    assert digest_a.startswith("sha256:")


def test_normalize_deep_copies() -> None:
    """Test normalize deep copies."""
    original = {"nested": {"list": [1, 2, 3]}}
    normalized, _ = normalize_arguments(original)
    normalized["nested"]["list"].append(4)
    assert original == {"nested": {"list": [1, 2, 3]}}


def test_normalize_compact_ascii() -> None:
    """Test normalize compact ascii."""
    normalized, digest = normalize_arguments({"emoji": "\U0001f600", "x": 1})
    # The frozen copy keeps display values; the digest binds the canonical
    # ensure_ascii form, so key order/whitespace/unicode never change it.
    import hashlib
    import json

    canonical = json.dumps(
        {"emoji": "\U0001f600", "x": 1},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    assert digest == "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
    assert "\U0001f600" not in canonical


def test_normalize_rejects_non_serializable() -> None:
    """Test normalize rejects non serializable."""
    with pytest.raises(NonSerializableArguments):
        normalize_arguments({"fn": lambda: 1})


def test_digest_is_stable_sha256() -> None:
    """Test digest is stable sha256."""
    assert digest_arguments({"a": 1}) == digest_arguments({"a": 1})
    digest = digest_arguments({"a": 1})
    assert len(digest) == len("sha256:") + 64


def test_redact_recursive() -> None:
    """Test redact recursive."""
    payload: dict[str, Any] = {
        "api_token": "abc123",
        "nested": {"userPassword": "hunter2", "ok": 1},
        "headers": {"Authorization": "Bearer xyz"},
        "list": [{"session_id": "s1"}],
    }
    redacted = redact(payload)
    assert redacted["api_token"] == "[redacted]"
    assert redacted["nested"]["userPassword"] == "[redacted]"
    assert redacted["headers"]["Authorization"] == "[redacted]"
    assert redacted["list"][0]["session_id"] == "[redacted]"
    assert redacted["nested"]["ok"] == 1


def test_redact_matches_all_spec_keywords() -> None:
    """Test redact matches all spec keywords."""
    payload = {
        "token": 1,
        "password": 1,
        "secret": 1,
        "authorization": 1,
        "cookie": 1,
        "credential": 1,
        "private_key": 1,
        "session": 1,
        "privateKey": 1,
    }
    redacted = redact(payload)
    assert all(value == "[redacted]" for value in redacted.values())


def test_redact_does_not_touch_payload() -> None:
    """Test redact does not touch payload."""
    payload = {"path": "/tmp/a.txt", "content": "hello"}
    assert redact(payload) == payload


def test_argument_summary_hides_full_contents() -> None:
    """Test argument summary hides full contents."""
    summary = argument_summary({"path": "/tmp/a.txt", "content": "x" * 5000})
    assert "x" * 100 not in str(summary)
    assert summary["path"] == "/tmp/a.txt"
    assert "content" in summary


def test_argument_summary_redacts_secret_like_keys() -> None:
    """Test argument summary redacts secret like keys."""
    summary = argument_summary({"token": "abc", "path": "/tmp/a"})
    assert summary["token"] == "[redacted]"


def test_redact_covers_api_key_forms() -> None:
    """Test redact covers api key forms.

    The spec's standing rule is "never log raw secret-like fields"; an
    API key is unambiguously secret-like even though the keyword list
    names only the common forms.
    """
    payload = {"api_key": "sk-1", "apikey": "sk-2", "path": "/tmp/a.txt"}
    redacted = redact(payload)
    assert redacted["api_key"] == "[redacted]"
    assert redacted["apikey"] == "[redacted]"
    assert redacted["path"] == "/tmp/a.txt"
