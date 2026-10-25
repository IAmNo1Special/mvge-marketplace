from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from mvge.runes.rune_api import RuneAPI
from mvge.runes.types import (
    AfterProviderResponseData,
    AfterSpellResultData,
    BeforeProviderRequestData,
    BeforeSpellCastData,
    SigilHook,
    TurnEndData,
    TurnStartData,
)
from mvge_runes_opentelemetry_bridge.rune import rune_factory


@pytest.mark.asyncio
async def test_rune_factory_registers_hooks_and_commands(monkeypatch) -> None:
    monkeypatch.setenv("MVGE_OTEL_IN_MEMORY", "1")

    hooks: dict[SigilHook, Any] = {}
    commands: dict[str, Any] = {}

    api = MagicMock(spec=RuneAPI)
    api.on.side_effect = lambda hook, handler: hooks.__setitem__(hook, handler)
    api.register_command.side_effect = lambda name, description="", handler=None: (
        commands.__setitem__(name, handler)
    )

    rune_factory(api)

    assert SigilHook.SESSION_START in hooks
    assert SigilHook.SESSION_SHUTDOWN in hooks
    assert SigilHook.TURN_START in hooks
    assert SigilHook.TURN_END in hooks
    assert SigilHook.BEFORE_PROVIDER_REQUEST in hooks
    assert SigilHook.AFTER_PROVIDER_RESPONSE in hooks
    assert SigilHook.BEFORE_SPELL_CAST in hooks
    assert SigilHook.AFTER_SPELL_RESULT in hooks
    assert "otel" in commands

    # Drive the hook handlers
    await hooks[SigilHook.SESSION_START]({"session_name": "s1"})
    await hooks[SigilHook.TURN_START](TurnStartData(model={"id": "openai/gpt-4o"}))
    await hooks[SigilHook.BEFORE_PROVIDER_REQUEST](
        BeforeProviderRequestData(model={"id": "openai/gpt-4o", "realm": "openai"})
    )
    await hooks[SigilHook.AFTER_PROVIDER_RESPONSE](
        AfterProviderResponseData(
            response={"usage": {"prompt_tokens": 10, "completion_tokens": 5}},
            mana_used=15,
        )
    )
    await hooks[SigilHook.BEFORE_SPELL_CAST](
        BeforeSpellCastData(
            spell_name="bash", spell_cast={"id": "cast-1", "args": {"command": "ls"}}
        )
    )
    await hooks[SigilHook.AFTER_SPELL_RESULT](
        AfterSpellResultData(spell_name="bash", spell_cast_id="cast-1", result="output")
    )
    await hooks[SigilHook.TURN_END](TurnEndData(stop_reason="stop", mana_used=15))
    await hooks[SigilHook.SESSION_SHUTDOWN]()

    # Test /otel command handler
    cmd = commands["otel"]
    status_out = await cmd("status")
    assert "OpenTelemetry Tracing Status:" in status_out

    test_out = await cmd("test")
    assert "OK: OpenTelemetry tracing test turn recorded." in test_out

    unknown_out = await cmd("foobar")
    assert "Unknown otel command: foobar" in unknown_out

    default_out = await cmd("")
    assert "OpenTelemetry Tracing Status:" in default_out


@pytest.mark.asyncio
async def test_rune_factory_dict_payloads_and_runner_context(tmp_path) -> None:
    hooks: dict[SigilHook, Any] = {}
    commands: dict[str, Any] = {}

    class DummyContext:
        cwd = str(tmp_path)
        session_id = "runner-session-123"

    class DummyRunner:
        context = DummyContext()

    api = MagicMock(spec=RuneAPI)
    api._runner = DummyRunner()
    api.on.side_effect = lambda hook, handler: hooks.__setitem__(hook, handler)
    api.register_command.side_effect = lambda name, description="", handler=None: (
        commands.__setitem__(name, handler)
    )

    rune_factory(api)

    # Class with session_name attribute
    class SessionData:
        session_name = "custom-session-attr"

    await hooks[SigilHook.SESSION_START](SessionData())
    await hooks[SigilHook.TURN_START]({"model": {"id": "deepseek/deepseek-chat"}})
    await hooks[SigilHook.TURN_START]({"model": "bare-string-model"})
    await hooks[SigilHook.BEFORE_PROVIDER_REQUEST](
        {
            "model": {
                "id": "deepseek/deepseek-chat",
                "realm": "deepseek",
                "temperature": 0.7,
            }
        }
    )
    await hooks[SigilHook.AFTER_PROVIDER_RESPONSE](
        {
            "response": {"usage": {"prompt_tokens": 10, "completion_tokens": 20}},
            "mana_used": 30,
        }
    )
    await hooks[SigilHook.BEFORE_SPELL_CAST](
        {
            "spell_name": "read_file",
            "spell_cast": {"id": "cast-dict-1", "parameters": {"path": "a.txt"}},
        }
    )

    class SpellResultWithError:
        is_error = True

    await hooks[SigilHook.AFTER_SPELL_RESULT](
        AfterSpellResultData(
            spell_name="read_file",
            spell_cast_id="cast-dict-1",
            result=SpellResultWithError(),
        )
    )
    await hooks[SigilHook.AFTER_SPELL_RESULT](
        {
            "spell_cast_id": "cast-dict-2",
            "result": "err",
            "is_error": True,
        }
    )
    await hooks[SigilHook.TURN_END]({"stop_reason": "max_mana", "mana_used": 30})
    await hooks[SigilHook.SESSION_SHUTDOWN]()

    # Test /otel test failure branch
    from unittest.mock import patch

    cmd = commands["otel"]
    with patch(
        "mvge_runes_opentelemetry_bridge.tracer.OTelTracer.start_turn",
        side_effect=ValueError("bad turn"),
    ):
        fail_out = await cmd("test")
        assert "FAIL: OpenTelemetry probe failed: bad turn" in fail_out
