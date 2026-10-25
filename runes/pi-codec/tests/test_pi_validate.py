"""Tests for pi-validate: thin read-only Pi session validation (TDD)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mvge_runes_pi_codec.cli import ValidateError, validate_pi_file

FIX = Path(__file__).parent / "fixtures"
V3 = FIX / "pi_v3_sample.jsonl"


def _v4_header(**overrides):
    header = {
        "v": 4,
        "kind": "header",
        "id": "0196a1b2-1111-7a2b-8c3d-4e5f60718293",
        "createdAt": 1789041600000,
        "storageVersion": 1,
        "cwd": "/tmp/proj",
    }
    header.update(overrides)
    return header


def _write(path: Path, header: dict, writes: list) -> Path:
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(header) + "\n")
        f.writelines(json.dumps(w) + "\n" for w in writes)
    return path


def test_validate_ok_v4(tmp_path: Path):
    entry_id = "0196a1b2-2222-7a2b-8c3d-4e5f60718293"
    tip_id = "0196a1b2-3333-7a2b-8c3d-4e5f60718293"
    path = _write(
        tmp_path / "s.jsonl",
        _v4_header(),
        [
            {
                "kind": "entry",
                "id": entry_id,
                "parentId": None,
                "type": "message",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                    "timestamp": 1789041600000,
                },
                "seq": 1,
                "timestamp": 1789041600000,
            },
            {
                "kind": "value",
                "op": "set",
                "seq": 2,
                "namespace": "pi.branch.tip",
                "key": "main",
                "value": entry_id,
                "timestamp": 1789041600000,
            },
        ],
    )
    report = validate_pi_file(path)
    assert report.format == "v4"
    assert report.session_id == _v4_header()["id"]
    assert report.entries == 1
    assert report.leaf_id == entry_id
    assert tip_id != report.leaf_id  # sanity: leaf is the entry, not the tip write


def test_validate_ok_v3():
    report = validate_pi_file(V3)
    assert report.format == "v3"
    assert report.session_id == "v3sess-001"
    assert report.entries == 8


def test_validate_not_a_pi_file(tmp_path: Path):
    path = tmp_path / "nope.jsonl"
    path.write_text('{"hello": "world"}\n', encoding="utf-8")
    with pytest.raises(ValidateError, match="[Nn]ot a Pi session"):
        validate_pi_file(path)


def test_validate_unsupported_version(tmp_path: Path):
    path = _write(tmp_path / "s.jsonl", _v4_header(v=5), [])
    with pytest.raises(ValidateError, match="[Uu]nsupported"):
        validate_pi_file(path)


def test_validate_broken_entry(tmp_path: Path):
    entry_id = "0196a1b2-2222-7a2b-8c3d-4e5f60718293"
    path = _write(
        tmp_path / "s.jsonl",
        _v4_header(),
        [
            {
                "kind": "entry",
                "id": entry_id,
                "parentId": "missing-parent",
                "type": "message",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                    "timestamp": 1789041600000,
                },
                "seq": 1,
                "timestamp": 1789041600000,
            },
        ],
    )
    with pytest.raises(ValidateError, match="[Pp]arent"):
        validate_pi_file(path)


def test_validate_torn_tail_is_read_only(tmp_path: Path):
    entry_id = "0196a1b2-2222-7a2b-8c3d-4e5f60718293"
    path = _write(
        tmp_path / "s.jsonl",
        _v4_header(),
        [
            {
                "kind": "entry",
                "id": entry_id,
                "parentId": None,
                "type": "message",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                    "timestamp": 1789041600000,
                },
                "seq": 1,
                "timestamp": 1789041600000,
            },
        ],
    )
    before = path.read_bytes()
    with open(path, "ab") as f:
        f.write(b'{"kind": "entry", "id": "truncated')
    with pytest.raises(ValidateError):
        validate_pi_file(path)
    # read-only: the torn tail is left untouched for Mvge open to repair.
    assert path.read_bytes() == before + b'{"kind": "entry", "id": "truncated'


def test_validate_missing_file(tmp_path: Path):
    with pytest.raises(ValidateError, match="Cannot read"):
        validate_pi_file(tmp_path / "missing.jsonl")
