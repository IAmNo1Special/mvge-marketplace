from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mvge.core.invocations import ContentType
from mvge.core.spells import SpellResultMessage
from mvge.runes.rune_api import RuneAPI
from mvge.runes.types import AfterSpellResultData, SigilHook
from mvge_runes_lsp_bridge.rune import LspBridge, LspClient, rune_factory


@pytest.fixture
def mock_api() -> Any:
    api = MagicMock(spec=RuneAPI)
    sigil_handlers: dict[SigilHook, list[Any]] = {}

    def on(hook: SigilHook, handler: Any, priority: int = 0) -> None:
        sigil_handlers.setdefault(hook, []).append(handler)

    api.on = on
    api._sigil_handlers = sigil_handlers
    api.on_dispose = MagicMock()
    return api


def test_lsp_bridge_registration(mock_api: Any) -> None:
    bridge = rune_factory(mock_api)
    assert isinstance(bridge, LspBridge)
    handlers = mock_api._sigil_handlers
    assert SigilHook.AFTER_SPELL_RESULT in handlers
    assert mock_api.on_dispose.call_count == 1


def test_format_diagnostics(mock_api: Any) -> None:
    bridge = LspBridge(mock_api)
    diagnostics = [
        {
            "severity": 1,
            "range": {"start": {"line": 10, "character": 4}},
            "message": "Undefined variable 'foo'",
        },
        {
            "severity": 2,  # Warning - should be ignored
            "range": {"start": {"line": 15, "character": 0}},
            "message": "Unused import 'sys'",
        },
        {
            "severity": 1,
            "range": {"start": {"line": 20, "character": 8}},
            "message": "Incompatible types in assignment",
        },
    ]

    text = bridge.format_diagnostics(diagnostics, "src/main.py")
    assert '<diagnostics file="src/main.py">' in text
    assert "ERROR [11:5] Undefined variable 'foo'" in text
    assert "Unused import 'sys'" not in text
    assert "ERROR [21:9] Incompatible types in assignment" in text
    assert "</diagnostics>" in text


def test_format_diagnostics_empty_when_no_errors(mock_api: Any) -> None:
    bridge = LspBridge(mock_api)
    diagnostics = [
        {
            "severity": 2,
            "range": {"start": {"line": 1, "character": 1}},
            "message": "Warning only",
        }
    ]
    assert bridge.format_diagnostics(diagnostics, "foo.py") == ""


@pytest.mark.asyncio
async def test_ignores_non_edit_spells(mock_api: Any) -> None:
    bridge = LspBridge(mock_api)
    msg = SpellResultMessage(
        role="spellResult", spell_name="bash", content=[{"type": "text", "text": "ls"}]
    )
    data = AfterSpellResultData(spell_name="bash", spell_cast_id="c1", result=msg)

    out = await bridge.on_after_spell_result(data)
    assert out is data
    assert len(msg.content) == 1


@pytest.mark.asyncio
async def test_graceful_when_binary_not_found(mock_api: Any, tmp_path: Path) -> None:
    bridge = LspBridge(mock_api)
    target = tmp_path / "test.py"
    target.write_text("print('hello')\n")

    msg = SpellResultMessage(
        role="spellResult",
        spell_name="edit",
        content=[{"type": "text", "text": f"Updated {target}"}],
        details={"path": str(target)},
    )
    data = AfterSpellResultData(spell_name="edit", spell_cast_id="c1", result=msg)

    with patch("shutil.which", return_value=None):
        out = await bridge.on_after_spell_result(data)
        assert out is data
        assert msg.content[0]["text"] == f"Updated {target}"


@pytest.mark.asyncio
async def test_client_lsp_framing_and_diagnostics(mock_api: Any) -> None:
    client = LspClient(["dummy-server"], Path.cwd(), "python")
    # Simulate receiving diagnostic notification
    msg = {
        "jsonrpc": "2.0",
        "method": "textDocument/publishDiagnostics",
        "params": {
            "uri": "file:///workspace/test.py",
            "diagnostics": [
                {
                    "severity": 1,
                    "range": {"start": {"line": 0, "character": 0}},
                    "message": "SyntaxError: invalid syntax",
                }
            ],
        },
    }
    client._dispatch_message(msg)
    assert "file:///workspace/test.py" in client._latest_diagnostics
    diags = client._latest_diagnostics["file:///workspace/test.py"]
    assert len(diags) == 1
    assert diags[0]["message"] == "SyntaxError: invalid syntax"


@pytest.mark.asyncio
async def test_on_after_spell_result_appends_diagnostics(
    mock_api: Any, tmp_path: Path
) -> None:
    bridge = LspBridge(mock_api)
    py_file = tmp_path / "module.py"
    py_file.write_text("x = 1\n")

    msg = SpellResultMessage(
        role="spellResult",
        spell_name="write",
        content=[{"type": ContentType.TEXT, "text": f"Wrote to {py_file}"}],
        details={"path": str(py_file)},
    )
    data = AfterSpellResultData(spell_name="write", spell_cast_id="c2", result=msg)

    mock_client = AsyncMock(spec=LspClient)
    mock_client.notify_document = AsyncMock()
    mock_client.wait_for_diagnostics = AsyncMock(
        return_value=[
            {
                "severity": 1,
                "range": {"start": {"line": 4, "character": 2}},
                "message": "Type mismatch",
            }
        ]
    )

    with (
        patch.object(
            bridge, "get_server_command", return_value=("python", ["mock-server"])
        ),
        patch.object(bridge, "get_or_create_client", return_value=mock_client),
    ):
        await bridge.on_after_spell_result(data)

    content_text = msg.content[0]["text"]
    assert f"Wrote to {py_file}" in content_text
    assert '<diagnostics file="' in content_text
    assert "ERROR [5:3] Type mismatch" in content_text


@pytest.mark.asyncio
async def test_shutdown_stops_all_clients(mock_api: Any) -> None:
    bridge = LspBridge(mock_api)
    mock_client = AsyncMock(spec=LspClient)
    bridge._clients[("ws", "python")] = mock_client

    await bridge.shutdown()
    assert mock_client.stop.call_count == 1
    assert len(bridge._clients) == 0


def test_get_server_command_variants(mock_api: Any) -> None:
    bridge = LspBridge(mock_api)
    assert bridge.get_server_command(".md") is None
    with patch("shutil.which", return_value=None):
        assert bridge.get_server_command(".py") is None
        assert bridge.get_server_command(".rs") is None

    def which_side_effect(binary: str) -> str | None:
        return None if binary == "basedpyright-langserver" else f"/usr/bin/{binary}"

    with patch("shutil.which", side_effect=which_side_effect):
        assert bridge.get_server_command(".py") == (
            "python",
            ["pyright-langserver", "--stdio"],
        )
        assert bridge.get_server_command(".go") == ("go", ["gopls"])


def test_format_diagnostics_caps_and_defaults(mock_api: Any) -> None:
    bridge = LspBridge(mock_api)
    diagnostics = [
        {
            "severity": 1,
            "range": {"start": {"line": i, "character": 0}},
            "message": f"e{i}",
        }
        for i in range(25)
    ]
    diagnostics.append({"severity": 3, "message": "info ignored"})
    diagnostics.append({"severity": 4, "message": "hint ignored"})
    text = bridge.format_diagnostics(diagnostics, "a.py")
    assert text.count("ERROR [") == 20
    assert "e24" not in text
    assert "info ignored" not in text

    bare = bridge.format_diagnostics([{"severity": 1}], "b.py")
    assert "ERROR [1:1] " in bare


def test_extract_file_path_sources(mock_api: Any) -> None:
    bridge = LspBridge(mock_api)
    dict_result = {"details": {"path": "/tmp/from-details.py"}}
    assert bridge._extract_file_path(dict_result, {}) == "/tmp/from-details.py"

    msg = SpellResultMessage(
        role="spellResult",
        spell_name="edit",
        content=[{"type": "text", "text": "Updated /tmp/from-text.py"}],
    )
    assert bridge._extract_file_path(msg, {}) == "/tmp/from-text.py"

    msg2 = SpellResultMessage(
        role="spellResult",
        spell_name="edit",
        content=[{"type": "text", "text": "something else"}],
    )
    assert (
        bridge._extract_file_path(msg2, {"arguments": {"path": "/tmp/from-args.py"}})
        == "/tmp/from-args.py"
    )
    assert bridge._extract_file_path(msg2, {}) is None


@pytest.mark.asyncio
async def test_on_after_spell_result_dict_shape_appends(
    mock_api: Any, tmp_path: Path
) -> None:
    bridge = LspBridge(mock_api)
    py_file = tmp_path / "module.py"
    py_file.write_text("x = 1\n")
    result = {
        "content": [{"type": "text", "text": f"Wrote to {py_file}"}],
        "details": {"path": str(py_file)},
    }
    data = {"spell_name": "write", "result": result}
    mock_client = AsyncMock(spec=LspClient)
    mock_client.notify_document = AsyncMock()
    mock_client.wait_for_diagnostics = AsyncMock(
        return_value=[
            {
                "severity": 1,
                "range": {"start": {"line": 0, "character": 0}},
                "message": "Bad",
            }
        ]
    )
    with (
        patch.object(
            bridge, "get_server_command", return_value=("python", ["mock-server"])
        ),
        patch.object(bridge, "get_or_create_client", return_value=mock_client),
    ):
        out = await bridge.on_after_spell_result(data)
    assert out is data
    assert "ERROR [1:1] Bad" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_on_after_spell_result_missing_file_and_no_diagnostics(
    mock_api: Any, tmp_path: Path
) -> None:
    bridge = LspBridge(mock_api)
    missing = tmp_path / "gone.py"
    msg = SpellResultMessage(
        role="spellResult",
        spell_name="edit",
        content=[{"type": "text", "text": "Updated"}],
        details={"path": str(missing)},
    )
    data = AfterSpellResultData(spell_name="edit", spell_cast_id="c9", result=msg)
    out = await bridge.on_after_spell_result(data)
    assert out is data
    assert msg.content[0]["text"] == "Updated"

    py_file = tmp_path / "ok.py"
    py_file.write_text("x = 1\n")
    msg2 = SpellResultMessage(
        role="spellResult",
        spell_name="write",
        content=[{"type": "text", "text": "Wrote"}],
        details={"path": str(py_file)},
    )
    data2 = AfterSpellResultData(spell_name="write", spell_cast_id="c10", result=msg2)
    mock_client = AsyncMock(spec=LspClient)
    mock_client.notify_document = AsyncMock()
    mock_client.wait_for_diagnostics = AsyncMock(return_value=[])
    with (
        patch.object(
            bridge, "get_server_command", return_value=("python", ["mock-server"])
        ),
        patch.object(bridge, "get_or_create_client", return_value=mock_client),
    ):
        await bridge.on_after_spell_result(data2)
    assert msg2.content[0]["text"] == "Wrote"


@pytest.mark.asyncio
async def test_on_after_spell_result_appends_new_block_when_no_text_block(
    mock_api: Any, tmp_path: Path
) -> None:
    bridge = LspBridge(mock_api)
    py_file = tmp_path / "mod.py"
    py_file.write_text("x = 1\n")
    msg = SpellResultMessage(
        role="spellResult",
        spell_name="edit",
        content=[],
        details={"path": str(py_file)},
    )
    data = AfterSpellResultData(spell_name="edit", spell_cast_id="c11", result=msg)
    mock_client = AsyncMock(spec=LspClient)
    mock_client.notify_document = AsyncMock()
    mock_client.wait_for_diagnostics = AsyncMock(
        return_value=[
            {
                "severity": 1,
                "range": {"start": {"line": 2, "character": 1}},
                "message": "Oops",
            }
        ]
    )
    with (
        patch.object(
            bridge, "get_server_command", return_value=("python", ["mock-server"])
        ),
        patch.object(bridge, "get_or_create_client", return_value=mock_client),
    ):
        await bridge.on_after_spell_result(data)
    assert len(msg.content) == 1
    assert "ERROR [3:2] Oops" in msg.content[0]["text"]


@pytest.mark.asyncio
async def test_on_after_spell_result_unreadable_file_returns_data(
    mock_api: Any, tmp_path: Path
) -> None:
    bridge = LspBridge(mock_api)
    dir_as_file = tmp_path / "dir.py"
    dir_as_file.mkdir()
    msg = SpellResultMessage(
        role="spellResult",
        spell_name="edit",
        content=[{"type": "text", "text": "Updated"}],
        details={"path": str(dir_as_file)},
    )
    data = AfterSpellResultData(spell_name="edit", spell_cast_id="c12", result=msg)
    mock_client = AsyncMock(spec=LspClient)
    with (
        patch.object(
            bridge, "get_server_command", return_value=("python", ["mock-server"])
        ),
        patch.object(bridge, "get_or_create_client", return_value=mock_client),
    ):
        out = await bridge.on_after_spell_result(data)
    assert out is data
    assert msg.content[0]["text"] == "Updated"


@pytest.mark.asyncio
async def test_sigil_handler_delegates_to_bridge(mock_api: Any) -> None:
    bridge = rune_factory(mock_api)
    assert isinstance(bridge, LspBridge)
    handler = mock_api._sigil_handlers[SigilHook.AFTER_SPELL_RESULT][0]
    msg = SpellResultMessage(
        role="spellResult", spell_name="bash", content=[{"type": "text", "text": "ls"}]
    )
    data = AfterSpellResultData(spell_name="bash", spell_cast_id="c13", result=msg)
    out = await handler(data)
    assert out is data


@pytest.mark.asyncio
async def test_client_start_missing_binary_and_unstarted_ops(mock_api: Any) -> None:
    client = LspClient(["no-such-binary-xyz"], Path.cwd(), "python")
    with patch("shutil.which", return_value=None):
        assert await client.start() is False
    with pytest.raises(RuntimeError, match="not running"):
        await client.send_request("initialize", {})
    await client.send_notification("initialized", {})


class _FakeStdin:
    def __init__(self) -> None:
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        return None


@pytest.mark.asyncio
async def test_client_notify_document_open_then_change(mock_api: Any) -> None:
    client = LspClient(["dummy"], Path.cwd(), "python")
    fake_stdin = _FakeStdin()
    fake_process = MagicMock()
    fake_process.stdin = fake_stdin
    client._process = fake_process
    path = Path("/tmp/doc.py")
    await client.notify_document(path, "v1")
    await client.notify_document(path, "v2")
    assert client._doc_versions[path.resolve().as_uri()] == 2
    first = b"".join(fake_stdin.written).decode("utf-8")
    assert "textDocument/didOpen" in first
    assert "textDocument/didChange" in first


@pytest.mark.asyncio
async def test_client_dispatch_resolves_and_errors_pending(mock_api: Any) -> None:

    client = LspClient(["dummy"], Path.cwd(), "python")
    loop = asyncio.get_running_loop()
    fut_ok: asyncio.Future[Any] = loop.create_future()
    fut_err: asyncio.Future[Any] = loop.create_future()
    client._pending_requests[5] = fut_ok
    client._pending_requests[6] = fut_err
    client._dispatch_message({"id": 5, "result": {"ok": True}})
    assert await fut_ok == {"ok": True}
    client._dispatch_message({"id": 6, "error": {"code": -1, "message": "bad"}})
    with pytest.raises(RuntimeError):
        await fut_err
    client._dispatch_message({"id": 999, "method": "unknown/notification"})


@pytest.mark.asyncio
async def test_client_wait_for_diagnostics_set_and_timeout(mock_api: Any) -> None:
    client = LspClient(["dummy"], Path.cwd(), "python")
    uri = "file:///tmp/w.py"
    diags = [{"severity": 1, "message": "x"}]
    client._latest_diagnostics[uri] = diags
    event = client._diagnostic_events.setdefault(uri, asyncio.Event())
    event.set()
    got = await client.wait_for_diagnostics(uri, debounce_ms=1, timeout_ms=1000)
    assert got == diags
    assert await client.wait_for_diagnostics("file:///tmp/none.py", timeout_ms=20) == []


@pytest.mark.asyncio
async def test_client_stop_cancels_pending_and_process(mock_api: Any) -> None:

    client = LspClient(["dummy"], Path.cwd(), "python")
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[Any] = loop.create_future()
    client._pending_requests[3] = fut
    fake_process = MagicMock()
    fake_process.returncode = None
    fake_process.wait = AsyncMock(return_value=0)
    client._process = fake_process
    await client.stop()
    assert fut.cancelled()
    assert client._pending_requests == {}
    assert client._process is None
    fake_process.terminate.assert_called_once()


@pytest.mark.asyncio
async def test_get_or_create_client_caches_and_rejects_failed_start(
    mock_api: Any, tmp_path: Path
) -> None:
    bridge = LspBridge(mock_api)
    with patch.object(LspClient, "start", new=AsyncMock(return_value=True)):
        first = await bridge.get_or_create_client(tmp_path, "python", ["srv"])
        second = await bridge.get_or_create_client(tmp_path, "python", ["srv"])
    assert first is not None and first is second
    with patch.object(LspClient, "start", new=AsyncMock(return_value=False)):
        assert await bridge.get_or_create_client(tmp_path, "go", ["gopls"]) is None
    assert all(lang != "go" for _ws, lang in bridge._clients)
