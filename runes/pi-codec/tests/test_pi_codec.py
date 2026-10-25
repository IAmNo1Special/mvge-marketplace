"""Tests for PiSessionCodec: native Pi v3/v4 session support (no conversion).

TDD: these tests were written before the codec implementation. Every Pi
behavior asserted here mirrors the compiled Pi source at
/tmp/pi-core/node_modules/@earendil-works/pi-agent-core/dist/harness.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from mvge.tome.types import TomeEntry, TomeEntryType, TomeVersionError
from mvge_runes_pi_codec.codec import PiCodecError, PiSessionCodec

UUIDV7_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def _v4_header(sid: str = "sess-001", **kw) -> dict:
    header = {
        "kind": "header",
        "v": 4,
        "id": sid,
        "cwd": "/tmp/proj",
        "storageVersion": 1,
        "createdAt": 1750000000000,
    }
    header.update(kw)
    return header


def _v3_header(sid: str = "v3sess-001", **kw) -> dict:
    header = {
        "type": "session",
        "version": 3,
        "id": sid,
        "cwd": "/tmp/proj",
        "timestamp": "2026-09-10T12:00:00.000Z",
    }
    header.update(kw)
    return header


def _entry_write(eid, parent, seq, ts, **kw) -> dict:
    write = {
        "kind": "entry",
        "id": eid,
        "parentId": parent,
        "seq": seq,
        "timestamp": ts,
    }
    write.update(kw)
    return write


def _msg(eid, parent, seq, role, content, ts=1750000001000) -> dict:
    return _entry_write(
        eid,
        parent,
        seq,
        ts,
        type="message",
        message={"role": role, "content": content, "timestamp": ts},
    )


def _tip(branch: str, tip_id, seq: int) -> dict:
    return {
        "kind": "value",
        "op": "set",
        "seq": seq,
        "namespace": "pi.branch.tip",
        "key": branch,
        "value": tip_id,
    }


def _value(namespace: str, key: str, seq: int, value) -> dict:
    return {
        "kind": "value",
        "op": "set",
        "seq": seq,
        "namespace": namespace,
        "key": key,
        "value": value,
    }


def _del_value(namespace: str, key: str, seq: int) -> dict:
    return {
        "kind": "value",
        "op": "delete",
        "seq": seq,
        "namespace": namespace,
        "key": key,
    }


def _fork_value_map(out_path: Path) -> dict:
    """(namespace, key) -> value write for every value line in a fork output."""
    found = {}
    for line in out_path.read_text(encoding="utf-8").splitlines()[1:]:
        write = json.loads(line)
        if write.get("kind") == "value":
            found[(write["namespace"], write["key"])] = write
    return found


def _write_file(path: Path, header: dict, transactions: list) -> Path:
    """Write a Pi session file. Each transaction is a write dict or a list of
    write dicts (multi-write transactions serialize as JSON arrays)."""
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(header) + "\n")
        for txn in transactions:
            f.write(json.dumps(txn) + "\n")
    return path


def _codec() -> PiSessionCodec:
    return PiSessionCodec()


def _parse(codec: PiSessionCodec, path: Path, header: dict) -> list[TomeEntry]:
    lines = path.read_text(encoding="utf-8").splitlines()[1:]
    return codec.parse_entries(header, lines, source=str(path))


@pytest.fixture
def v4_session(tmp_path: Path) -> Path:
    header = _v4_header()
    txns = [
        _msg("e1", None, 1, "user", "hello"),
        [
            _msg("e2", "e1", 2, "assistant", [{"type": "text", "text": "hi"}]),
            _tip("main", "e2", 3),
        ],
    ]
    return _write_file(tmp_path / "s.jsonl", header, txns)


# --- detection ---------------------------------------------------------------


class TestDetect:
    def test_v4_detected(self):
        assert _codec().detect(_v4_header()) is True

    def test_v3_detected(self):
        assert _codec().detect(_v3_header()) is True

    def test_tome_header_not_detected(self):
        assert _codec().detect({"type": "session", "version": 1}) is False

    def test_foreign_not_detected(self):
        assert _codec().detect({"foo": "bar"}) is False

    def test_v5_looks_like_session(self):
        header = _v4_header(v=5)
        codec = _codec()
        assert codec.detect(header) is False
        assert codec.looks_like_session(header) is True

    def test_v3_looks_like_session(self):
        assert _codec().looks_like_session(_v3_header()) is True

    def test_foreign_not_session_looking(self):
        codec = _codec()
        assert codec.looks_like_session({"foo": "bar"}) is False
        assert codec.looks_like_session({"type": "session", "version": 1}) is False


# --- header -----------------------------------------------------------------


class TestParseHeader:
    def test_v4_metadata(self):
        meta = _codec().parse_header(_v4_header(sid="abc", parentSessionId="p1"))
        assert meta.id == "abc"
        assert meta.cwd == "/tmp/proj"
        assert meta.parent_tome_id == "p1"
        assert "2025" in meta.created_at  # createdAt ms -> ISO string

    def test_v4_unsupported_storage_version(self):
        with pytest.raises(TomeVersionError):
            _codec().parse_header(_v4_header(storageVersion=2))

    def test_v5_header_raises(self):
        with pytest.raises(TomeVersionError):
            _codec().parse_header(_v4_header(v=5))

    def test_v3_metadata(self):
        meta = _codec().parse_header(_v3_header(sid="v3a"))
        assert meta.id == "v3a"
        assert meta.cwd == "/tmp/proj"
        assert meta.created_at.startswith("2026-09-10")

    def test_v3_parent_session_resolved(self, tmp_path: Path):
        parent = _write_file(tmp_path / "parent.jsonl", _v4_header(sid="pid-9"), [])
        header = _v3_header(parentSession=str(parent))
        meta = _codec().parse_header(header)
        assert meta.parent_tome_id == "pid-9"

    def test_v3_missing_parent_path_ignored(self, tmp_path: Path):
        header = _v3_header(parentSession=str(tmp_path / "nope.jsonl"))
        meta = _codec().parse_header(header)
        assert meta.parent_tome_id is None

    def test_v3_bad_timestamp_raises(self):
        with pytest.raises(TomeVersionError):
            _codec().parse_header(_v3_header(timestamp="not-a-date"))


# --- v4 entry parsing --------------------------------------------------------


class TestParseV4:
    def test_messages_map(self, v4_session: Path):
        codec = _codec()
        entries = _parse(codec, v4_session, _v4_header())
        assert len(entries) == 2
        user, asst = entries
        assert user.type == TomeEntryType.MESSAGE
        assert user.id == "e1"
        assert user.parent_id is None
        assert user.payload["role"] == "user"
        assert user.payload["content"] == "hello"
        assert asst.payload["role"] == "assistant"
        assert asst.payload["content"] == [{"type": "text", "text": "hi"}]
        assert user.payload["pi_original"]["seq"] == 1

    def test_leaf_is_branch_tip(self, v4_session: Path):
        codec = _codec()
        entries = _parse(codec, v4_session, _v4_header())
        assert codec.leaf_id(_v4_header(), entries) == "e2"

    def test_tool_result_maps_to_spell_result(self, tmp_path: Path):
        msg = _msg("e1", None, 1, "toolResult", [{"type": "text", "text": "out"}])
        msg["message"].update(
            {"toolName": "bash", "toolCallId": "c1", "isError": False}
        )
        path = _write_file(
            tmp_path / "s.jsonl", _v4_header(), [msg, _tip("main", "e1", 2)]
        )
        entries = _parse(_codec(), path, _v4_header())
        payload = entries[0].payload
        assert entries[0].type == TomeEntryType.MESSAGE
        assert payload["role"] == "spellResult"
        assert payload["spell_name"] == "bash"
        assert payload["spell_cast_id"] == "c1"

    def test_custom_entry(self, tmp_path: Path):
        write = _entry_write(
            "e1",
            None,
            1,
            1750000001000,
            type="custom",
            customType="note",
            data={"x": 1},
        )
        path = _write_file(
            tmp_path / "s.jsonl", _v4_header(), [write, _tip("main", "e1", 2)]
        )
        entries = _parse(_codec(), path, _v4_header())
        assert entries[0].type == TomeEntryType.CUSTOM
        assert entries[0].payload["type"] == "note"

    def test_compaction_entry(self, tmp_path: Path):
        write = _entry_write(
            "e9",
            "e1",
            5,
            1750000005000,
            type="compaction",
            summary="did stuff",
            tokensBefore=1234,
            retainedTail=[
                {"role": "user", "content": "kept", "timestamp": 1750000004000}
            ],
            fromHook=False,
        )
        path = _write_file(
            tmp_path / "s.jsonl",
            _v4_header(),
            [_msg("e1", None, 1, "user", "a"), write, _tip("main", "e9", 6)],
        )
        entries = _parse(_codec(), path, _v4_header())
        comp = entries[1]
        assert comp.type == TomeEntryType.COMPACTION
        assert comp.payload["summary"] == "did stuff"
        assert comp.payload["manaBefore"] == 1234
        tail = comp.payload["retainedTail"]
        assert tail[0]["role"] == "user"
        assert tail[0]["content"] == "kept"

    def test_usage_and_list_writes_validated_not_entries(self, tmp_path: Path):
        usage = {
            "kind": "usage",
            "id": "u1",
            "seq": 2,
            "usage": {"inputTokens": 5},
            "adjustment": False,
        }
        lst = {
            "kind": "list",
            "op": "append",
            "seq": 3,
            "namespace": "n",
            "key": "k",
            "value": 1,
        }
        path = _write_file(
            tmp_path / "s.jsonl",
            _v4_header(),
            [_msg("e1", None, 1, "user", "a"), usage, lst, _tip("main", "e1", 4)],
        )
        entries = _parse(_codec(), path, _v4_header())
        assert len(entries) == 1

    def test_strict_bad_json(self, tmp_path: Path):
        path = tmp_path / "s.jsonl"
        path.write_text(json.dumps(_v4_header()) + "\n{not json}\n", encoding="utf-8")
        with pytest.raises(Exception, match="[Ii]nvalid JSON"):
            _parse(_codec(), path, _v4_header())

    def test_strict_unknown_write_kind(self, tmp_path: Path):
        bad = {"kind": "nope", "seq": 1}
        path = _write_file(tmp_path / "s.jsonl", _v4_header(), [bad])
        with pytest.raises(Exception, match="[Ii]nvalid JSONL write kind"):
            _parse(_codec(), path, _v4_header())

    def test_strict_non_monotonic_seq(self, tmp_path: Path):
        txns = [_msg("e1", None, 2, "user", "a"), _msg("e2", "e1", 2, "user", "b")]
        path = _write_file(tmp_path / "s.jsonl", _v4_header(), txns)
        with pytest.raises(Exception, match="[Nn]on-monotonic"):
            _parse(_codec(), path, _v4_header())

    def test_strict_duplicate_entry_id(self, tmp_path: Path):
        txns = [_msg("e1", None, 1, "user", "a"), _msg("e1", None, 2, "user", "b")]
        path = _write_file(tmp_path / "s.jsonl", _v4_header(), txns)
        with pytest.raises(Exception, match="[Dd]uplicate"):
            _parse(_codec(), path, _v4_header())

    def test_strict_missing_parent(self, tmp_path: Path):
        path = _write_file(
            tmp_path / "s.jsonl", _v4_header(), [_msg("e1", "ghost", 1, "user", "a")]
        )
        with pytest.raises(Exception, match="[Mm]issing parent"):
            _parse(_codec(), path, _v4_header())

    def test_strict_unknown_entry_type(self, tmp_path: Path):
        write = _entry_write("e1", None, 1, 1750000001000, type="weird")
        path = _write_file(tmp_path / "s.jsonl", _v4_header(), [write])
        with pytest.raises(Exception, match="[Uu]nsupported.*entry type"):
            _parse(_codec(), path, _v4_header())

    def test_strict_bad_value_op(self, tmp_path: Path):
        bad = {"kind": "value", "op": "explode", "seq": 1, "namespace": "n", "key": "k"}
        path = _write_file(tmp_path / "s.jsonl", _v4_header(), [bad])
        with pytest.raises(Exception, match="[Ii]nvalid JSONL value operation"):
            _parse(_codec(), path, _v4_header())


# --- v3 normalization --------------------------------------------------------


def _v3_file(tmp_path: Path, name: str, records: list) -> Path:
    path = tmp_path / name
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(_v3_header()) + "\n")
        for record in records:
            f.write(json.dumps(record) + "\n")
    return path


def _v3msg(eid, parent, role, content, ts="2026-09-10T12:00:01.000Z") -> dict:
    return {
        "type": "message",
        "id": eid,
        "parentId": parent,
        "timestamp": ts,
        "message": {"role": role, "content": content, "timestamp": ts},
    }


class TestParseV3:
    def test_normalizes_to_v4_entries(self, tmp_path: Path):
        records = [
            _v3msg("m1", None, "user", "hello"),
            {
                "type": "model_change",
                "id": "c1",
                "parentId": "m1",
                "timestamp": "2026-09-10T12:00:02.000Z",
                "provider": "p",
                "modelId": "m",
            },
            _v3msg("m2", "c1", "assistant", "hi"),
        ]
        path = _v3_file(tmp_path, "v3.jsonl", records)
        codec = _codec()
        entries = _parse(codec, path, _v3_header())
        # model_change is discarded; its child re-parents to nearest retained
        assert len(entries) == 2
        assert all(UUIDV7_RE.match(e.id) for e in entries)
        assert entries[0].id != "m1"  # reminted, Pi-style
        assert entries[1].parent_id == entries[0].id
        assert entries[0].payload["role"] == "user"

    def test_v3_leaf_is_last_entry_tip(self, tmp_path: Path):
        records = [_v3msg("m1", None, "user", "a"), _v3msg("m2", "m1", "user", "b")]
        path = _v3_file(tmp_path, "v3.jsonl", records)
        codec = _codec()
        entries = _parse(codec, path, _v3_header())
        assert codec.leaf_id(_v3_header(), entries) == entries[1].id

    def test_v3_missing_parent_rejected(self, tmp_path: Path):
        path = _v3_file(tmp_path, "v3.jsonl", [_v3msg("m1", "ghost", "user", "a")])
        with pytest.raises(Exception, match="[Mm]issing"):
            _parse(_codec(), path, _v3_header())

    def test_v3_cycle_rejected(self, tmp_path: Path):
        records = [
            _v3msg("m1", "m2", "user", "a"),
            _v3msg("m2", "m1", "user", "b"),
        ]
        path = _v3_file(tmp_path, "v3.jsonl", records)
        with pytest.raises(Exception, match="[Cc]ycle"):
            _parse(_codec(), path, _v3_header())

    def test_v3_duplicate_id_rejected(self, tmp_path: Path):
        records = [_v3msg("m1", None, "user", "a"), _v3msg("m1", None, "user", "b")]
        path = _v3_file(tmp_path, "v3.jsonl", records)
        with pytest.raises(Exception, match="[Dd]uplicate"):
            _parse(_codec(), path, _v3_header())

    def test_v3_bad_record_type_rejected(self, tmp_path: Path):
        records = [
            {
                "type": "bogus",
                "id": "x",
                "parentId": None,
                "timestamp": "2026-09-10T12:00:01.000Z",
            }
        ]
        path = _v3_file(tmp_path, "v3.jsonl", records)
        with pytest.raises(Exception, match="[Uu]nsupported legacy v3"):
            _parse(_codec(), path, _v3_header())


# --- torn-tail repair --------------------------------------------------------


class TestRepairOnOpen:
    def test_clean_file_no_repair(self, v4_session: Path):
        before = v4_session.read_bytes()
        assert _codec().repair_on_open(str(v4_session)) is False
        assert v4_session.read_bytes() == before

    def test_torn_tail_repaired(self, tmp_path: Path):
        path = tmp_path / "t.jsonl"
        good = json.dumps(_msg("e1", None, 1, "user", "a"))
        path.write_bytes(
            json.dumps(_v4_header()).encode()
            + b"\n"
            + good.encode()
            + b"\n"
            + b'{"kind": "entry", "id": "e2", "par'
        )
        assert _codec().repair_on_open(str(path)) is True
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[1])["id"] == "e1"

    def test_empty_file_no_repair(self, tmp_path: Path):
        path = tmp_path / "e.jsonl"
        path.write_bytes(b"")
        assert _codec().repair_on_open(str(path)) is False

    def test_missing_file_no_repair(self, tmp_path: Path):
        assert _codec().repair_on_open(str(tmp_path / "nope.jsonl")) is False


# --- appends -----------------------------------------------------------------


def _new_message(role: str = "user", content: str = "next") -> TomeEntry:
    return TomeEntry(
        id="tmp-id",
        parent_id=None,
        type=TomeEntryType.MESSAGE,
        timestamp=1750000010.0,
        payload={"role": role, "content": content},
    )


class TestPlanAppendV4:
    def test_entry_and_tip_in_one_transaction(self, v4_session: Path):
        codec = _codec()
        entries = _parse(codec, v4_session, _v4_header())
        plan = codec.plan_append(_new_message(), entries, _v4_header())
        assert plan.rewrite is False
        assert len(plan.lines) == 1
        txn = plan.lines[0]
        assert isinstance(txn, list)
        assert len(txn) == 2
        entry_write, tip_write = txn
        assert entry_write["kind"] == "entry"
        assert entry_write["seq"] == 4
        assert entry_write["parentId"] == "e2"
        assert UUIDV7_RE.match(entry_write["id"])
        assert entry_write["timestamp"] == 1750000010000
        assert entry_write["message"]["role"] == "user"
        assert tip_write == {
            "kind": "value",
            "op": "set",
            "seq": 5,
            "namespace": "pi.branch.tip",
            "key": "main",
            "value": entry_write["id"],
        }
        assert plan.stored.id == entry_write["id"]
        assert plan.stored.parent_id == "e2"

    def test_seq_advances_with_next_seq_header(self, tmp_path: Path):
        header = _v4_header(nextSeq=41)
        path = _write_file(tmp_path / "s.jsonl", header, [])
        codec = _codec()
        entries = _parse(codec, path, header)
        plan = codec.plan_append(_new_message(), entries, header)
        txn = plan.lines[0]
        assert txn[0]["seq"] == 41
        assert txn[1]["seq"] == 42

    def test_assistant_spell_cast_maps_to_tool_call(self, v4_session: Path):
        codec = _codec()
        entries = _parse(codec, v4_session, _v4_header())
        entry = _new_message()
        entry.payload.update(
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "running"},
                    {
                        "type": "spell_cast",
                        "spell_cast": {"name": "bash", "arguments": {"cmd": "ls"}},
                    },
                ],
            }
        )
        plan = codec.plan_append(entry, entries, _v4_header())
        blocks = plan.lines[0][0]["message"]["content"]
        assert {"type": "text", "text": "running"} in blocks
        assert {
            "type": "toolCall",
            "name": "bash",
            "arguments": {"cmd": "ls"},
        } in blocks

    def test_spell_result_maps_to_tool_result(self, v4_session: Path):
        codec = _codec()
        entries = _parse(codec, v4_session, _v4_header())
        entry = _new_message()
        entry.payload.update(
            {
                "role": "spellResult",
                "content": [{"type": "text", "text": "ok"}],
                "spell_name": "bash",
                "spell_cast_id": "c9",
                "is_error": False,
            }
        )
        plan = codec.plan_append(entry, entries, _v4_header())
        message = plan.lines[0][0]["message"]
        assert message["role"] == "toolResult"
        assert message["toolName"] == "bash"
        assert message["toolCallId"] == "c9"

    def test_compaction_translates_to_pi_compaction(self, v4_session: Path):
        codec = _codec()
        entries = _parse(codec, v4_session, _v4_header())
        entry = TomeEntry(
            id="tmp",
            parent_id=None,
            type=TomeEntryType.COMPACTION,
            timestamp=1750000020.0,
            payload={
                "summary": "we did things",
                "manaBefore": 777,
                "retainedTail": [
                    {"role": "user", "content": "keep me"},
                    {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "kept too"}],
                    },
                ],
            },
        )
        plan = codec.plan_append(entry, entries, _v4_header())
        write = plan.lines[0][0]
        assert write["type"] == "compaction"
        assert write["summary"] == "we did things"
        assert write["tokensBefore"] == 777
        assert write["fromHook"] is False
        assert write["retainedTail"][0]["role"] == "user"
        assert write["retainedTail"][1]["role"] == "assistant"

    def test_append_tip_idempotent_when_already_at_target(self, v4_session: Path):
        codec = _codec()
        entries = _parse(codec, v4_session, _v4_header())
        leaf = TomeEntry(
            id="leaf",
            parent_id=None,
            type=TomeEntryType.LEAF,
            timestamp=0.0,
            payload={"targetId": "e2"},
        )
        assert codec.append_tip_lines(_v4_header(), entries, leaf) == []

    def test_append_tip_moves_branch_tip(self, v4_session: Path):
        codec = _codec()
        entries = _parse(codec, v4_session, _v4_header())
        leaf = TomeEntry(
            id="leaf",
            parent_id=None,
            type=TomeEntryType.LEAF,
            timestamp=0.0,
            payload={"targetId": "e1"},
        )
        lines = codec.append_tip_lines(_v4_header(), entries, leaf)
        assert lines is not None
        assert len(lines) == 1
        write = lines[0]
        assert write["kind"] == "value"
        assert write["namespace"] == "pi.branch.tip"
        assert write["key"] == "main"
        assert write["value"] == "e1"
        assert write["seq"] == 4


class TestPlanAppendV3Migration:
    def _v3(self, tmp_path: Path) -> Path:
        records = [
            _v3msg("m1", None, "user", "hello"),
            _v3msg("m2", "m1", "assistant", "hi"),
        ]
        return _v3_file(tmp_path, "v3.jsonl", records)

    def test_first_append_rewrites_to_v4(self, tmp_path: Path):
        path = self._v3(tmp_path)
        codec = _codec()
        header = _v3_header()
        entries = _parse(codec, path, header)
        plan = codec.plan_append(_new_message(), entries, header)
        assert plan.rewrite is True
        assert plan.header is not None
        assert plan.header["v"] == 4
        assert plan.header["kind"] == "header"
        assert plan.header["id"] == "v3sess-001"
        # baseline writes each on their own line (Pi includes the branch tip
        # in the baseline), then one caller transaction: usage adjustment,
        # entry, tip — exactly like Pi's upgradeLegacyV3ToV4.
        assert len(plan.lines) == 4
        assert plan.lines[0]["kind"] == "entry"
        assert plan.lines[1]["kind"] == "entry"
        assert plan.lines[2]["kind"] == "value"
        assert plan.lines[2]["namespace"] == "pi.branch.tip"
        caller_txn = plan.lines[3]
        assert isinstance(caller_txn, list)
        assert len(caller_txn) == 3
        assert caller_txn[0]["kind"] == "usage"  # v3-import adjustment first
        assert caller_txn[0]["details"] == {"source": "v3-import"}
        assert caller_txn[1]["kind"] == "entry"
        assert caller_txn[2]["kind"] == "value"
        # seqs run 1..6 with no gaps
        seqs = [w["seq"] for w in plan.lines[:3]] + [w["seq"] for w in caller_txn]
        assert seqs == [1, 2, 3, 4, 5, 6]
        assert plan.header["nextSeq"] == 7

    def test_migrated_file_parses_as_v4(self, tmp_path: Path):
        path = self._v3(tmp_path)
        codec = _codec()
        header = _v3_header()
        entries = _parse(codec, path, header)
        plan = codec.plan_append(_new_message(), entries, header)
        dest = tmp_path / "migrated.jsonl"
        with dest.open("w", encoding="utf-8") as f:
            f.write(json.dumps(plan.header) + "\n")
            for line in plan.lines:
                f.write(json.dumps(line) + "\n")
        new_header = json.loads(dest.read_text(encoding="utf-8").splitlines()[0])
        assert codec.detect(new_header) is True
        new_entries = _parse(codec, dest, new_header)
        assert len(new_entries) == 3
        assert codec.leaf_id(new_header, new_entries) == plan.stored.id


# --- fork --------------------------------------------------------------------


class TestFork:
    def test_fork_produces_v4_with_parent_session_id(self, tmp_path: Path):
        src = tmp_path / "src.jsonl"
        _write_file(
            src,
            _v4_header(sid="orig-1"),
            [
                _msg("e1", None, 1, "user", "a"),
                [_msg("e2", "e1", 2, "assistant", "b"), _tip("main", "e2", 3)],
            ],
        )
        dest_dir = tmp_path / "forks"
        dest_dir.mkdir()
        codec = _codec()
        out = codec.fork(source=str(src), dest_dir=str(dest_dir), new_id="fork-1")
        out_path = Path(out)
        assert out_path.parent == dest_dir
        header = json.loads(out_path.read_text(encoding="utf-8").splitlines()[0])
        assert header["v"] == 4
        assert header["id"] == "fork-1"
        assert header["parentSessionId"] == "orig-1"
        assert header["cwd"] == "/tmp/proj"
        entries = _parse(codec, out_path, header)
        assert [e.id for e in entries] == ["e1", "e2"]
        assert codec.leaf_id(header, entries) == "e2"

    def test_fork_v3_source_produces_v4(self, tmp_path: Path):
        records = [_v3msg("m1", None, "user", "a")]
        src = _v3_file(tmp_path, "v3.jsonl", records)
        dest_dir = tmp_path / "forks"
        dest_dir.mkdir()
        out = _codec().fork(source=str(src), dest_dir=str(dest_dir), new_id="fork-9")
        header = json.loads(Path(out).read_text(encoding="utf-8").splitlines()[0])
        assert header["v"] == 4
        assert header["parentSessionId"] == "v3sess-001"

    def test_fork_copies_session_name_and_labels(self, tmp_path: Path):
        src = tmp_path / "src.jsonl"
        _write_file(
            src,
            _v4_header(sid="orig-1"),
            [
                _msg("e1", None, 1, "user", "a"),
                _msg("e2", "e1", 2, "user", "b"),
                _value("pi.session.name", "", 3, "my session"),
                _value("pi.entry.label", "e2", 4, "checkpoint"),
                _tip("main", "e2", 5),
            ],
        )
        dest_dir = tmp_path / "forks"
        dest_dir.mkdir()
        out = _codec().fork(source=str(src), dest_dir=str(dest_dir), new_id="fork-1")
        values = _fork_value_map(Path(out))
        assert values[("pi.session.name", "")]["value"] == "my session"
        assert values[("pi.entry.label", "e2")]["value"] == "checkpoint"
        assert values[("pi.branch.tip", "main")]["value"] == "e2"

    def test_fork_drops_labels_for_pruned_entries(self, tmp_path: Path):
        src = tmp_path / "src.jsonl"
        _write_file(
            src,
            _v4_header(sid="orig-1"),
            [
                _msg("e1", None, 1, "user", "a"),
                _msg("e2", "e1", 2, "user", "b"),
                _msg("e3", "e2", 3, "user", "c"),
                _value("pi.entry.label", "e2", 4, "kept"),
                _value("pi.entry.label", "e3", 5, "pruned"),
                _tip("main", "e3", 6),
            ],
        )
        dest_dir = tmp_path / "forks"
        dest_dir.mkdir()
        out = _codec().fork(
            source=str(src),
            dest_dir=str(dest_dir),
            new_id="fork-1",
            leaf_id="e2",
        )
        values = _fork_value_map(Path(out))
        assert values[("pi.entry.label", "e2")]["value"] == "kept"
        assert ("pi.entry.label", "e3") not in values
        assert values[("pi.branch.tip", "main")]["value"] == "e2"

    def test_fork_copies_lane_config_and_clears_lane_state(self, tmp_path: Path):
        src = tmp_path / "src.jsonl"
        config = {"model": {"provider": "p", "modelId": "m"}, "thinkingLevel": "low"}
        _write_file(
            src,
            _v4_header(sid="orig-1"),
            [
                _msg("e1", None, 1, "user", "a"),
                _value("pi.lane.config", "main", 2, config),
                _value(
                    "pi.lane.state",
                    "main",
                    3,
                    {
                        "currentOperationId": "op-9",
                        "lastOperationId": "op-8",
                        "inbox": ["pending-thing"],
                    },
                ),
                _tip("main", "e1", 4),
            ],
        )
        dest_dir = tmp_path / "forks"
        dest_dir.mkdir()
        out = _codec().fork(source=str(src), dest_dir=str(dest_dir), new_id="fork-1")
        values = _fork_value_map(Path(out))
        assert values[("pi.lane.config", "main")]["value"] == config
        assert values[("pi.lane.state", "main")]["value"] == {
            "currentOperationId": None,
            "lastOperationId": None,
            "inbox": [],
        }

    def test_fork_drops_other_branch_state(self, tmp_path: Path):
        src = tmp_path / "src.jsonl"
        _write_file(
            src,
            _v4_header(sid="orig-1"),
            [
                _msg("e1", None, 1, "user", "a"),
                _msg("e2", "e1", 2, "user", "b"),
                _tip("main", "e2", 3),
                _tip("side", "e1", 4),
                _value("pi.lane.config", "side", 5, {"model": "x"}),
                _value("pi.lane.state", "side", 6, {"inbox": []}),
            ],
        )
        dest_dir = tmp_path / "forks"
        dest_dir.mkdir()
        out = _codec().fork(source=str(src), dest_dir=str(dest_dir), new_id="fork-1")
        values = _fork_value_map(Path(out))
        assert ("pi.branch.tip", "side") not in values
        assert ("pi.lane.config", "side") not in values
        assert ("pi.lane.state", "side") not in values
        assert values[("pi.branch.tip", "main")]["value"] == "e2"

    def test_fork_drops_result_and_operation_namespaces(self, tmp_path: Path):
        src = tmp_path / "src.jsonl"
        _write_file(
            src,
            _v4_header(sid="orig-1"),
            [
                _msg("e1", None, 1, "user", "a"),
                _value("pi.result", "", 2, {"status": "completed"}),
                _value("pi.op.tool_memo", "op-1:", 3, {"x": 1}),
                _value("pi.pending.entry", "e9", 4, {"id": "e9"}),
                _value("myapp.note", "", 5, "custom"),
                _tip("main", "e1", 6),
            ],
        )
        dest_dir = tmp_path / "forks"
        dest_dir.mkdir()
        out = _codec().fork(source=str(src), dest_dir=str(dest_dir), new_id="fork-1")
        values = _fork_value_map(Path(out))
        assert ("pi.result", "") not in values
        assert ("pi.op.tool_memo", "op-1:") not in values
        assert ("pi.pending.entry", "e9") not in values
        assert ("myapp.note", "") not in values

    def test_fork_unknown_reserved_namespace_raises(self, tmp_path: Path):
        src = tmp_path / "src.jsonl"
        _write_file(
            src,
            _v4_header(sid="orig-1"),
            [
                _msg("e1", None, 1, "user", "a"),
                _value("pi.future", "", 2, "???"),
                _tip("main", "e1", 3),
            ],
        )
        dest_dir = tmp_path / "forks"
        dest_dir.mkdir()
        with pytest.raises(PiCodecError, match="Unknown reserved fork namespace"):
            _codec().fork(source=str(src), dest_dir=str(dest_dir), new_id="fork-1")

    def test_fork_keeps_only_current_value_row(self, tmp_path: Path):
        src = tmp_path / "src.jsonl"
        _write_file(
            src,
            _v4_header(sid="orig-1"),
            [
                _msg("e1", None, 1, "user", "a"),
                _value("pi.session.name", "", 2, "old name"),
                _value("pi.session.name", "", 3, "new name"),
                _value("pi.entry.label", "e1", 4, "doomed"),
                _del_value("pi.entry.label", "e1", 5),
                _tip("main", "e1", 6),
            ],
        )
        dest_dir = tmp_path / "forks"
        dest_dir.mkdir()
        out = _codec().fork(source=str(src), dest_dir=str(dest_dir), new_id="fork-1")
        values = _fork_value_map(Path(out))
        assert values[("pi.session.name", "")]["value"] == "new name"
        assert ("pi.entry.label", "e1") not in values

    def test_fork_v3_copies_session_name(self, tmp_path: Path):
        records = [
            _v3msg("m1", None, "user", "a"),
            {
                "type": "session_info",
                "id": "s1",
                "parentId": "m1",
                "timestamp": "2026-09-10T12:00:02.000Z",
                "name": "v3 named session",
            },
        ]
        src = _v3_file(tmp_path, "v3.jsonl", records)
        dest_dir = tmp_path / "forks"
        dest_dir.mkdir()
        out = _codec().fork(source=str(src), dest_dir=str(dest_dir), new_id="fork-9")
        values = _fork_value_map(Path(out))
        assert values[("pi.session.name", "")]["value"] == "v3 named session"


# --- structural: satisfies the rune codec loader ------------------------------


class TestLoaderContract:
    def test_manifest_declared_codec_loads(self):
        import json as _json

        manifest = _json.loads(
            (Path(__file__).parent.parent / "manifest.json").read_text()
        )
        assert "mvge_runes_pi_codec.codec:PiSessionCodec" in manifest["session_codecs"]
        assert "Codec" in manifest["types"]

    def test_structural_members(self):
        from mvge.runes.codecs import _CODEC_METHODS

        codec = _codec()
        for member in _CODEC_METHODS:
            assert callable(getattr(codec, member, None)), member
