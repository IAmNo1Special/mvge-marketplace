from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mvge.core.invocations import ContentType
from mvge.core.spells import SpellResultMessage
from mvge.runes.rune_api import RuneAPI
from mvge.runes.types import AfterSpellResultData, SigilHook
from mvgeos_runes_lsp_bridge.rune import LspBridge, LspClient, rune_factory


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
