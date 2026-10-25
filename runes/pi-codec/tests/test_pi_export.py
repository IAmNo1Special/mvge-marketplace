"""Tests for pi-export: Tome v1 -> Pi-native v4 file (TDD)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mvge.tome.handle import TomeHandleFactory
from mvge.tome.types import TomeEntry, TomeEntryType
from mvgeos_runes_pi_codec.codec import PiSessionCodec
from mvgeos_runes_pi_codec.exporter import ExportError, export_tome_to_pi


def _message(i: int, parent: str | None, role: str, **payload: object) -> TomeEntry:
    return TomeEntry(
        id=f"e{i}",
        parent_id=parent,
        type=TomeEntryType.MESSAGE,
        timestamp=1700000000.0 + i,
        payload={"role": role, **payload},
    )


def _make_tome(tome_dir: Path, tome_id: str = "export-me") -> None:
    factory = TomeHandleFactory(tome_dir)
    handle = factory.create_tome(cwd="/tmp/proj", tome_id=tome_id)
    handle.append(_message(0, None, "user", content="hello"))
    handle.append(
        _message(1, "e0", "assistant", content="hi there", stop_reason="stop")
    )
    handle.append(
        _message(
            2,
            "e1",
            "spellResult",
            spell_name="bash",
            spell_cast_id="c1",
            content="ok",
            is_error=False,
        )
    )


def _read_pi(path: Path) -> tuple[dict, list[dict]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return json.loads(lines[0]), [json.loads(line) for line in lines[1:]]


def test_export_produces_valid_pi_v4(tmp_path: Path):
    tome_dir = tmp_path / "sessions"
    _make_tome(tome_dir)
    out = tmp_path / "out.jsonl"

    report = export_tome_to_pi("export-me", tome_dir=tome_dir, output=out)

    assert report.tome_id == "export-me"
    assert report.entries == 3
    assert Path(report.pi_path) == out

    # The exported file must validate as Pi through the same codec.
    codec = PiSessionCodec()
    header, writes = _read_pi(out)
    assert codec.detect(header)
    meta = codec.parse_header(header)
    assert meta.id == report.pi_session_id
    entries = codec.parse_entries(header, [json.dumps(w) for w in writes], str(out))
    assert len(entries) == 3
    roles = [e.payload["role"] for e in entries]
    assert roles == ["user", "assistant", "spellResult"]
    # Pi-native: uuid ids, chained parents, strict seqs, branch tip.
    assert header["v"] == 4
    assert header["storageVersion"] == 1
    seqs = [w["seq"] for w in writes]
    assert seqs == [1, 2, 3, 4]
    assert writes[3]["kind"] == "value"
    assert writes[3]["namespace"] == "pi.branch.tip"
    assert header["nextSeq"] == 5


def test_export_parent_chain_preserved(tmp_path: Path):
    tome_dir = tmp_path / "sessions"
    _make_tome(tome_dir)
    out = tmp_path / "out.jsonl"

    export_tome_to_pi("export-me", tome_dir=tome_dir, output=out)

    _, writes = _read_pi(out)
    entries = [w for w in writes if w["kind"] == "entry"]
    by_id = {w["id"]: w for w in entries}
    assert entries[0]["parentId"] is None
    assert by_id[entries[1]["id"]]["parentId"] == entries[0]["id"]
    assert by_id[entries[2]["id"]]["parentId"] == entries[1]["id"]
    # tip points at the last entry
    assert writes[3]["value"] == entries[2]["id"]


def test_export_empty_tome(tmp_path: Path):
    tome_dir = tmp_path / "sessions"
    TomeHandleFactory(tome_dir).create_tome(cwd="/tmp/proj", tome_id="empty")
    out = tmp_path / "out.jsonl"

    report = export_tome_to_pi("empty", tome_dir=tome_dir, output=out)

    assert report.entries == 0
    header, writes = _read_pi(out)
    assert header["v"] == 4
    assert writes == []
    assert header["nextSeq"] == 1


def test_export_skips_leaf_entries(tmp_path: Path):
    tome_dir = tmp_path / "sessions"
    factory = TomeHandleFactory(tome_dir)
    handle = factory.create_tome(cwd="/tmp/proj", tome_id="branchy")
    handle.append(_message(0, None, "user", content="root"))
    handle.append(
        TomeEntry(
            id="leaf1",
            parent_id="e0",
            type=TomeEntryType.LEAF,
            timestamp=1700000001.0,
            payload={},
        )
    )
    handle.append(_message(1, "leaf1", "assistant", content="after leaf"))
    out = tmp_path / "out.jsonl"

    report = export_tome_to_pi("branchy", tome_dir=tome_dir, output=out)

    # LEAF skipped; the orphaned child re-roots onto the nearest kept ancestor.
    assert report.entries == 2
    assert report.skipped == 1
    _, writes = _read_pi(out)
    entries = [w for w in writes if w["kind"] == "entry"]
    assert len(entries) == 2
    assert entries[1]["parentId"] == entries[0]["id"]


def test_export_refuses_to_overwrite_without_force(tmp_path: Path):
    tome_dir = tmp_path / "sessions"
    _make_tome(tome_dir)
    out = tmp_path / "out.jsonl"
    out.write_text("existing", encoding="utf-8")

    with pytest.raises(ExportError, match="exists"):
        export_tome_to_pi("export-me", tome_dir=tome_dir, output=out)
    assert out.read_text(encoding="utf-8") == "existing"

    report = export_tome_to_pi("export-me", tome_dir=tome_dir, output=out, force=True)
    assert report.entries == 3


def test_export_missing_tome(tmp_path: Path):
    with pytest.raises(ExportError, match="not found"):
        export_tome_to_pi("nope", tome_dir=tmp_path / "sessions")


def test_export_default_output_name(tmp_path: Path, monkeypatch):
    tome_dir = tmp_path / "sessions"
    _make_tome(tome_dir)
    monkeypatch.chdir(tmp_path)

    report = export_tome_to_pi("export-me", tome_dir=tome_dir)

    assert Path(report.pi_path) == tmp_path / "export-me.pi.jsonl"


def test_export_compaction_translates(tmp_path: Path):
    tome_dir = tmp_path / "sessions"
    factory = TomeHandleFactory(tome_dir)
    handle = factory.create_tome(cwd="/tmp/proj", tome_id="compacted")
    handle.append(_message(0, None, "user", content="hello"))
    handle.append(
        TomeEntry(
            id="c0",
            parent_id="e0",
            type=TomeEntryType.COMPACTION,
            timestamp=1700000001.0,
            payload={
                "summary": "did stuff",
                "retainedTail": [
                    {
                        "type": "message",
                        "payload": {"role": "user", "content": "kept"},
                    }
                ],
                "tokensBefore": 10,
            },
        )
    )
    out = tmp_path / "out.jsonl"

    export_tome_to_pi("compacted", tome_dir=tome_dir, output=out)

    codec = PiSessionCodec()
    header, writes = _read_pi(out)
    entries = codec.parse_entries(header, [json.dumps(w) for w in writes], str(out))
    assert entries[1].type == TomeEntryType.COMPACTION
    assert entries[1].payload["summary"] == "did stuff"
