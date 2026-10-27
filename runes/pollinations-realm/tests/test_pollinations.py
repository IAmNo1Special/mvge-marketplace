from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from mvge.core.channel import ChannelConfig, Model
from mvge.core.invocations import SummonerRequest
from mvge.runes.rune_api import RuneAPI
from mvge.runes.types import SigilHook

from pollinations import PollinationsRealm, _invocations_to_messages, wire_model_id
from rune import (
    after_provider_response,
    before_provider_request,
    pollinations_realm_factory,
    rune_factory,
)


def _model(**overrides: object) -> Model:
    fields: dict[str, object] = {
        "id": "pollinations/openai",
        "name": "openai",
        "realm": "pollinations",
        "base_url": "",
        "api_key": "",
    }
    fields.update(overrides)
    return Model(**fields)  # type: ignore[arg-type]


def _config(model: Model) -> ChannelConfig:
    return ChannelConfig(model=model, max_tokens=50, temperature=0.0)


def test_manifest_structure() -> None:
    manifest_path = Path(__file__).parent.parent / "manifest.json"
    assert manifest_path.is_file()

    with manifest_path.open("r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert manifest["name"] == "pollinations-realm"
    assert manifest["version"] == "0.1.0"
    assert manifest["entry_point"] == "rune.py"
    assert manifest["runtime"] == "python"
    assert "httpx>=0.27" in manifest["python_deps"]
    assert "before_provider_request" in manifest["hooks"]
    assert "after_provider_response" in manifest["hooks"]
    assert manifest["enabled"] is True


def test_pollinations_realm_factory() -> None:
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
        import os

        os.environ.pop(var, None)
    realm = pollinations_realm_factory()
    assert isinstance(realm, PollinationsRealm)
    assert realm.realm_name == "pollinations"
    assert realm._api_key == ""
    assert realm._base_url == "https://text.pollinations.ai/openai"
    assert realm.is_router is False


def test_rune_factory_registration() -> None:
    mock_api = MagicMock(spec=RuneAPI)
    rune_factory(mock_api)

    mock_api.register_realm_factory.assert_called_once_with(
        "pollinations", pollinations_realm_factory
    )
    mock_api.register_provider.assert_called_once_with(
        "pollinations", {"baseUrl": "https://text.pollinations.ai/openai"}
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


def test_wire_model_id_strips_prefix() -> None:
    assert wire_model_id("pollinations/openai") == "openai"


def test_wire_model_id_passes_bare_id_through() -> None:
    assert wire_model_id("openai") == "openai"
    assert wire_model_id("gpt-oss-20b") == "gpt-oss-20b"


def test_user_string_content_stays_a_string() -> None:
    inv = SummonerRequest(role="user", content="hello")
    messages = _invocations_to_messages([inv])
    assert messages == [{"role": "user", "content": "hello"}]


def test_user_unknown_block_degrades_to_text_note() -> None:
    inv = SummonerRequest(role="user", content=[{"type": "mystery"}])
    messages = _invocations_to_messages([inv])
    assert len(messages) == 1
    assert "mystery" in messages[0]["content"][0]["text"]


def _completion_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
        },
    )


@pytest.mark.asyncio
async def test_a_keyless_request_sends_no_authorization_header() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.headers))
        assert json.loads(request.content)["model"] == "openai"
        return _completion_response("hello")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    realm = pollinations_realm_factory(client=client)
    model = _model()
    resp = await realm.complete(
        model,
        [{"role": "user", "content": "Say hello."}],
        _config(model),
    )
    assert "authorization" not in seen
    assert resp.error_message is None
    assert resp.invocation.content[0]["text"] == "hello"


@pytest.mark.asyncio
async def test_a_keyed_request_sends_bearer_header() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.headers))
        return _completion_response("hello")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    realm = pollinations_realm_factory(api_key="test-key", client=client)
    model = _model()
    resp = await realm.complete(
        model,
        [{"role": "user", "content": "Say hello."}],
        _config(model),
    )
    assert seen.get("authorization") == "Bearer test-key"
    assert resp.error_message is None


@pytest.mark.asyncio
async def test_error_response_surfaces_as_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "slow down"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    realm = pollinations_realm_factory(client=client)
    model = _model()
    resp = await realm.complete(
        model,
        [{"role": "user", "content": "Say hello."}],
        _config(model),
    )
    assert resp.error_message is not None


def test_parse_sse_chunk_reads_reasoning_as_contemplation() -> None:
    realm = pollinations_realm_factory()
    chunk = realm._parse_sse_chunk(
        {"choices": [{"delta": {"content": "hi", "reasoning": "thinking"}}]}
    )
    assert chunk is not None
    assert chunk.content == "hi"
    assert chunk.contemplation == "thinking"
