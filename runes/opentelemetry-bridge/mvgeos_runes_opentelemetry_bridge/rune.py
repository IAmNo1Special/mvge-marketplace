"""OpenTelemetry Bridge Rune entry point and hook registration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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

from mvgeos_runes_opentelemetry_bridge.config import load_otel_config
from mvgeos_runes_opentelemetry_bridge.tracer import OTelTracer


def rune_factory(api: RuneAPI) -> None:
    """Initialize and bind OpenTelemetry tracing bridge rune to session lifecycle."""
    runner = getattr(api, "_runner", None)
    ctx = getattr(runner, "context", None) if runner else None
    raw_cwd = getattr(ctx, "cwd", None) if ctx else None
    cwd: Path | None = Path(raw_cwd) if raw_cwd else None

    config = load_otel_config(cwd=cwd)
    tracer = OTelTracer(config)

    async def on_session_start(data: Any = None) -> None:
        session_id = getattr(ctx, "session_id", "") or "session"
        if isinstance(data, dict):
            session_id = (
                data.get("session_name") or data.get("session_id") or session_id
            )
        elif hasattr(data, "session_name"):
            session_id = getattr(data, "session_name", "") or session_id
        tracer.start_session(session_id=session_id, cwd=str(cwd or ""))

    async def on_session_shutdown(_data: Any = None) -> None:
        tracer.end_session()

    async def on_turn_start(data: Any = None) -> None:
        model_name = ""
        if isinstance(data, TurnStartData):
            model_name = (
                data.model.get("id", "")
                if isinstance(data.model, dict)
                else str(data.model)
            )
        elif isinstance(data, dict) and "model" in data:
            model = data["model"]
            model_name = model.get("id", "") if isinstance(model, dict) else str(model)
        tracer.start_turn(turn_id=0, model=model_name)

    async def on_turn_end(data: Any = None) -> None:
        stop_reason = ""
        mana_used = 0
        if isinstance(data, TurnEndData):
            stop_reason = data.stop_reason
            mana_used = data.mana_used
        elif isinstance(data, dict):
            stop_reason = data.get("stop_reason", "")
            mana_used = data.get("mana_used", 0)
        tracer.end_turn(stop_reason=stop_reason, mana_used=mana_used)

    async def on_before_provider_request(data: Any = None) -> None:
        model_id = ""
        realm = ""
        cfg: dict[str, Any] = {}
        if isinstance(data, BeforeProviderRequestData):
            model = data.model
            if isinstance(model, dict):
                model_id = model.get("id", "")
                realm = model.get("realm", "")
                cfg = model
        elif isinstance(data, dict):
            model = data.get("model", {})
            if isinstance(model, dict):
                model_id = model.get("id", "")
                realm = model.get("realm", "")
                cfg = model
        tracer.start_provider_request(model_id=model_id, realm=realm, config=cfg)

    async def on_after_provider_response(data: Any = None) -> None:
        resp = None
        mana = 0
        if isinstance(data, AfterProviderResponseData):
            resp = data.response
            mana = data.mana_used
        elif isinstance(data, dict):
            resp = data.get("response")
            mana = data.get("mana_used", 0)
        tracer.end_provider_response(response=resp, mana_used=mana)

    async def on_before_spell_cast(data: Any = None) -> None:
        spell_name = ""
        cast_id = ""
        params: dict[str, Any] | None = None
        if isinstance(data, BeforeSpellCastData):
            spell_name = data.spell_name
            cast = data.spell_cast
            if isinstance(cast, dict):
                cast_id = cast.get("id", "")
                params = cast.get("args") or cast.get("parameters")
        elif isinstance(data, dict):
            spell_name = data.get("spell_name", "")
            cast = data.get("spell_cast", {})
            if isinstance(cast, dict):
                cast_id = cast.get("id", "")
                params = cast.get("args") or cast.get("parameters")
        tracer.start_tool(spell_name=spell_name, spell_cast_id=cast_id, params=params)

    async def on_after_spell_result(data: Any = None) -> None:
        cast_id = ""
        res = None
        is_err = False
        if isinstance(data, AfterSpellResultData):
            cast_id = data.spell_cast_id
            res = data.result
            if hasattr(res, "is_error"):
                is_err = getattr(res, "is_error", False)
        elif isinstance(data, dict):
            cast_id = data.get("spell_cast_id", "")
            res = data.get("result")
            is_err = bool(data.get("is_error", False))
        tracer.end_tool(spell_cast_id=cast_id, result=res, is_error=is_err)

    api.on(SigilHook.SESSION_START, on_session_start)
    api.on(SigilHook.SESSION_SHUTDOWN, on_session_shutdown)
    api.on(SigilHook.TURN_START, on_turn_start)
    api.on(SigilHook.TURN_END, on_turn_end)
    api.on(SigilHook.BEFORE_PROVIDER_REQUEST, on_before_provider_request)
    api.on(SigilHook.AFTER_PROVIDER_RESPONSE, on_after_provider_response)
    api.on(SigilHook.BEFORE_SPELL_CAST, on_before_spell_cast)
    api.on(SigilHook.AFTER_SPELL_RESULT, on_after_spell_result)

    async def otel_command(args: str = "") -> str:
        parsed = (args or "").strip()
        if parsed == "status" or not parsed:
            return (
                f"OpenTelemetry Tracing Status:\n"
                f"  Service Name: {config.service_name}\n"
                f"  Endpoint:     {config.endpoint or 'None'}\n"
                f"  Disabled:     {config.disabled}\n"
                f"  In-Memory:    {config.in_memory}"
            )
        if parsed == "test":
            try:
                tracer.start_turn(turn_id=999, model="test-probe")
                tracer.end_turn()
                return "OK: OpenTelemetry tracing test turn recorded."
            except Exception as exc:  # noqa: BLE001
                return f"FAIL: OpenTelemetry probe failed: {exc}"
        return f"Unknown otel command: {parsed}. Usage: /otel [status|test]"

    api.register_command(
        "otel",
        description="OpenTelemetry tracing status and test",
        handler=otel_command,
    )
