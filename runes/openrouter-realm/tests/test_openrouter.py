from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from mvge.core.invocations import SummonerRequest
from mvge.runes.rune_api import RuneAPI
from mvge.runes.types import SigilHook
from openrouter import OpenRouterRealm, _invocations_to_messages
from rune import (
    after_provider_response,
    before_provider_request,
    openrouter_realm_factory,
    rune_factory,
)


def test_manifest_structure() -> None:
    manifest_path = Path(__file__).parent.parent / "manifest.json"
    assert manifest_path.is_file()

    with manifest_path.open("r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert manifest["name"] == "openrouter-realm"
    assert manifest["version"] == "0.1.0"
    assert manifest["entry_point"] == "rune.py"
    assert manifest["runtime"] == "python"
    assert "httpx>=0.27" in manifest["python_deps"]
    assert "before_provider_request" in manifest["hooks"]
    assert "after_provider_response" in manifest["hooks"]
    assert manifest["enabled"] is True


def test_openrouter_realm_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    # httpx honors ambient proxy env when constructing its client; a
    # malformed NO_PROXY entry (e.g. bracketed IPv6) breaks construction.
    # This test only checks factory wiring, so isolate it from the env.
    for var in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    ):
        monkeypatch.delenv(var, raising=False)
    realm = openrouter_realm_factory(
        api_key="test-key", base_url="https://openrouter.ai/api/v1"
    )
    assert isinstance(realm, OpenRouterRealm)
    assert realm.realm_name == "openrouter"
    assert realm._api_key == "test-key"
    assert realm._base_url == "https://openrouter.ai/api/v1"


def test_rune_factory_registration() -> None:
    mock_api = MagicMock(spec=RuneAPI)
    rune_factory(mock_api)

    mock_api.register_realm_factory.assert_called_once_with(
        "openrouter", openrouter_realm_factory
    )
    mock_api.register_provider.assert_called_once_with(
        "openrouter", {"baseUrl": "https://openrouter.ai/api/v1"}
    )
    mock_api.on.assert_any_call(
        SigilHook.BEFORE_PROVIDER_REQUEST, before_provider_request
    )
    mock_api.on.assert_any_call(
        SigilHook.AFTER_PROVIDER_RESPONSE, after_provider_response
    )


@pytest.mark.asyncio
async def test_hooks_identity() -> None:
    data = {"sample": "value"}
    assert await before_provider_request(data) == data
    assert await after_provider_response(data) == data


def test_user_string_content_stays_a_string() -> None:
    inv = SummonerRequest(role="user", content="hello")
    messages = _invocations_to_messages([inv])
    assert messages == [{"role": "user", "content": "hello"}]


def test_user_content_parts_map_to_native_wire_parts() -> None:
    parts = [
        {"type": "text", "text": "look at this"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,aGk="},
        },
        {
            "type": "file",
            "file": {
                "filename": "doc.pdf",
                "file_data": "data:application/pdf;base64,aGk=",
            },
        },
    ]
    inv = SummonerRequest(role="user", content=parts)
    messages = _invocations_to_messages([inv])
    assert messages == [{"role": "user", "content": parts}]


def test_user_unknown_block_degrades_to_text_note() -> None:
    inv = SummonerRequest(
        role="user",
        content=[{"type": "mystery", "mystery": {"x": 1}}],
    )
    messages = _invocations_to_messages([inv])
    assert len(messages) == 1
    content = messages[0]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert "mystery" in content[0]["text"]
