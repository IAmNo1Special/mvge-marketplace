from __future__ import annotations

# Load this Rune's own rune.py by file path, not by bare `from rune import`.
# Every realm Rune names its entry point rune.py, so a bare import resolves
# to whichever rune module pytest collected first -- openrouter-realm's
# rune.py when both suites run together, which fails this file's import.
# Loading by path makes collection order irrelevant.
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from mvge.core.channel import ChannelConfig, Model
from mvge.core.invocations import SummonerRequest
from mvge.runes.rune_api import RuneAPI
from mvge.runes.types import SigilHook
from pollinations import PollinationsRealm, _invocations_to_messages, wire_model_id


def _load_rune_module():  # type: ignore[no-untyped-def]
    path = Path(__file__).parent.parent / "rune.py"
    spec = importlib.util.spec_from_file_location("pollinations_rune", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_rune = _load_rune_module()
after_provider_response = _rune.after_provider_response
before_provider_request = _rune.before_provider_request
pollinations_realm_factory = _rune.pollinations_realm_factory
rune_factory = _rune.rune_factory


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


def test_pollinations_realm_factory(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_parse_sse_chunk_reads_reasoning_content_as_contemplation() -> None:
    realm = pollinations_realm_factory()
    chunk = realm._parse_sse_chunk(
        {"choices": [{"delta": {"content": "hi", "reasoning_content": "thinking"}}]}
    )
    assert chunk is not None
    assert chunk.contemplation == "thinking"


def test_stream_request_omits_reasoning_and_keeps_tools() -> None:
    model = _model()
    config = ChannelConfig(
        model=model,
        max_tokens=50,
        temperature=0.0,
        tools=[{"type": "function", "function": {"name": "echo"}}],
    )
    realm = pollinations_realm_factory()
    url, headers, payload = realm._prepare_request(
        model,
        [SummonerRequest(role="user", content="hello")],
        config,
    )
    assert url == "https://text.pollinations.ai/openai/chat/completions"
    assert "authorization" not in headers
    assert payload["model"] == "openai"
    assert payload["stream"] is True
    assert "reasoning" not in payload
    assert payload["tools"][0]["function"]["name"] == "echo"


def _poisoned_pair_invitations() -> list:
    """Capture req-20895-006 messages [10]-[12] as invocations: one valid
    cast plus one empty-id cast, then the valid result plus the empty-id
    ``Spell not found: `` result."""
    return [
        SimpleNamespace(
            role="assistant",
            content=[
                {
                    "type": "spell_cast",
                    "spell_cast": {
                        "id": "chatcmpl-tool-95686fde6e807f7d",
                        "name": "search",
                        "arguments": {},
                    },
                },
                {
                    "type": "spell_cast",
                    "spell_cast": {"id": "", "name": "", "arguments": {}},
                },
            ],
        ),
        SimpleNamespace(
            role="spellResult",
            spell_cast_id="chatcmpl-tool-95686fde6e807f7d",
            content=[{"type": "text", "text": "approval_denied"}],
        ),
        SimpleNamespace(
            role="spellResult",
            spell_cast_id="",
            content=[{"type": "text", "text": "Spell not found: "}],
        ),
    ]


def test_empty_id_cast_and_result_are_dropped_pairing_intact() -> None:
    """R-B1: replay of the previously-failing body shape."""
    messages = _invocations_to_messages(_poisoned_pair_invitations())
    assert len(messages) == 2
    assert messages[0]["role"] == "assistant"
    assert [call["id"] for call in messages[0]["tool_calls"]] == [
        "chatcmpl-tool-95686fde6e807f7d"
    ]
    assert messages[1] == {
        "role": "tool",
        "tool_call_id": "chatcmpl-tool-95686fde6e807f7d",
        "content": "approval_denied",
    }
    assert not any(msg.get("tool_call_id", "x") == "" for msg in messages)


def test_only_empty_id_cast_and_result_disappear() -> None:
    """R-B2: an assistant response with only an empty-id cast, plus its
    empty-id result, produces no messages at all."""
    invs = [
        SimpleNamespace(
            role="assistant",
            content=[
                {
                    "type": "spell_cast",
                    "spell_cast": {"id": "", "name": "", "arguments": {}},
                }
            ],
        ),
        SimpleNamespace(
            role="spellResult",
            spell_cast_id="",
            content=[{"type": "text", "text": "Spell not found: "}],
        ),
    ]
    assert _invocations_to_messages(invs) == []


def test_only_empty_id_cast_with_text_keeps_text_message() -> None:
    """R-B2 variant: text the response also carried survives as a
    text-only assistant message; the empty-id result still disappears."""
    invs = [
        SimpleNamespace(
            role="assistant",
            content=[
                {"type": "text", "text": "thinking out loud"},
                {
                    "type": "spell_cast",
                    "spell_cast": {"id": "   ", "name": "search", "arguments": {}},
                },
            ],
        ),
        SimpleNamespace(
            role="spellResult",
            spell_cast_id="",
            content=[{"type": "text", "text": "Spell not found: "}],
        ),
    ]
    assert _invocations_to_messages(invs) == [
        {"role": "assistant", "content": "thinking out loud"}
    ]


def test_all_non_empty_ids_pass_through_in_order() -> None:
    """R-B3: valid ids are untouched, in order."""
    invs = [
        SimpleNamespace(
            role="assistant",
            content=[
                {
                    "type": "spell_cast",
                    "spell_cast": {"id": "call-1", "name": "read", "arguments": {}},
                },
                {
                    "type": "spell_cast",
                    "spell_cast": {"id": "call-2", "name": "bash", "arguments": {}},
                },
            ],
        ),
        SimpleNamespace(
            role="spellResult",
            spell_cast_id="call-1",
            content=[{"type": "text", "text": "one"}],
        ),
        SimpleNamespace(
            role="spellResult",
            spell_cast_id="call-2",
            content=[{"type": "text", "text": "two"}],
        ),
    ]
    messages = _invocations_to_messages(invs)
    assert [call["id"] for call in messages[0]["tool_calls"]] == ["call-1", "call-2"]
    assert [msg["tool_call_id"] for msg in messages[1:]] == ["call-1", "call-2"]
