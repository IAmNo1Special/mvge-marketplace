from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from mvge.core.abort import AbortError
from mvge.core.channel import ChannelConfig, Model
from mvge.core.invocations import SummonerRequest
from mvge.runes.rune_api import RuneAPI
from mvge.runes.types import SigilHook
from openrouter import (
    OpenRouterRealm,
    _invocations_to_messages,
    _supports_reasoning,
    _with_system_prompt,
)
from rune import (
    after_provider_response,
    before_provider_request,
    openrouter_realm_factory,
    rune_factory,
)


def _model(**overrides: object) -> Model:
    fields: dict[str, object] = {
        "id": "plain-model",
        "name": "plain-model",
        "realm": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "realm-key",
    }
    fields.update(overrides)
    return Model(**fields)  # type: ignore[arg-type]


def _config(model: Model, **overrides: object) -> ChannelConfig:
    cfg = ChannelConfig(model=model)
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


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


def test_user_none_content_becomes_empty_string() -> None:
    inv = SummonerRequest(role="user", content=None)
    messages = _invocations_to_messages([inv])
    assert messages == [{"role": "user", "content": ""}]


def test_user_non_dict_block_degrades_with_type_name() -> None:
    inv = SummonerRequest(role="user", content=[42])
    messages = _invocations_to_messages([inv])
    content = messages[0]["content"]
    assert content[0] == {"type": "text", "text": "[Unsupported content block: int]"}


def test_system_invocation_passes_through() -> None:
    inv = SummonerRequest(role="system", content="be helpful")
    messages = _invocations_to_messages([inv])
    assert messages == [{"role": "system", "content": "be helpful"}]


def test_assistant_text_and_tool_calls() -> None:
    inv = SimpleNamespace(
        role="assistant",
        content=[
            {"type": "text", "text": "calling it"},
            {
                "type": "spell_cast",
                "spell_cast": {
                    "id": "call-1",
                    "name": "read",
                    "arguments": {"path": "a.txt"},
                },
            },
        ],
    )
    messages = _invocations_to_messages([inv])
    assert messages == [
        {
            "role": "assistant",
            "content": "calling it",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "read",
                        "arguments": '{"path": "a.txt"}',
                    },
                }
            ],
        }
    ]


def test_assistant_tool_calls_only_content_is_none() -> None:
    inv = SimpleNamespace(
        role="assistant",
        content=[
            {
                "type": "spell_cast",
                "spell_cast": {"id": "call-2", "name": "bash", "arguments": {}},
            }
        ],
    )
    messages = _invocations_to_messages([inv])
    assert messages[0]["content"] is None
    assert messages[0]["tool_calls"][0]["id"] == "call-2"


def test_assistant_empty_content_and_unknown_role_skipped() -> None:
    invs = [
        SimpleNamespace(role="assistant", content=[]),
        SimpleNamespace(role="unknown-role", content="hi"),
    ]
    assert _invocations_to_messages(invs) == []


def test_spell_result_invocation_maps_to_tool_message() -> None:
    inv = SimpleNamespace(
        role="spellResult",
        spell_cast_id="call-9",
        content=[{"type": "text", "text": "file content"}],
    )
    messages = _invocations_to_messages([inv])
    assert messages == [
        {"role": "tool", "tool_call_id": "call-9", "content": "file content"}
    ]


def test_with_system_prompt_prepends_once() -> None:
    base = [{"role": "user", "content": "hi"}]
    assert _with_system_prompt(base, "be helpful") == [
        {"role": "system", "content": "be helpful"},
        {"role": "user", "content": "hi"},
    ]
    with_system = [{"role": "system", "content": "existing"}, *base]
    assert _with_system_prompt(with_system, "be helpful") == with_system
    assert _with_system_prompt(base, "") == base


def test_supports_reasoning() -> None:
    assert _supports_reasoning(_model(supported_parameters=["reasoning"])) is True
    assert _supports_reasoning(_model(id="openai/o1")) is True
    assert _supports_reasoning(_model(id="openai/o3-mini")) is True
    assert _supports_reasoning(_model()) is False


def test_prepare_request_url_and_headers_overrides() -> None:
    realm = OpenRouterRealm(api_key="realm-key")
    model = _model(
        base_url="https://custom.example/v2/",
        api_key="model-key",
        headers={"X-Custom": "yes"},
    )
    url, headers = realm._prepare_request_url_and_headers(model)
    assert url == "https://custom.example/v2/chat/completions"
    assert headers["Authorization"] == "Bearer model-key"
    assert headers["X-Custom"] == "yes"


def test_prepare_request_reasoning_payload_and_caps() -> None:
    realm = OpenRouterRealm(api_key="realm-key")
    model = _model(id="openai/o1")
    cfg = _config(
        model,
        system_prompt="sys",
        contemplation_level="high",
        contemplation_budget=512,
        max_output_mana=100,
        tools=[{"type": "function"}],
    )
    url, _headers, payload = realm._prepare_request(model, [], cfg)
    assert url.endswith("/chat/completions")
    assert payload["messages"] == [{"role": "system", "content": "sys"}]
    assert payload["reasoning"] == {"effort": "high", "max_tokens": 512}
    assert payload["max_tokens"] == 100
    assert payload["tools"] == [{"type": "function"}]
    assert payload["stream"] is True


def test_prepare_request_no_reasoning_when_excluded_or_none() -> None:
    realm = OpenRouterRealm(api_key="realm-key")
    model = _model(id="openai/o1")
    for overrides in (
        {"exclude_contemplation": True},
        {"contemplation_level": "none"},
    ):
        _url, _headers, payload = realm._prepare_request(
            model, [], _config(model, **overrides)
        )
        assert "reasoning" not in payload


def test_parse_sse_chunk_variants() -> None:
    realm = OpenRouterRealm(api_key="realm-key")
    assert realm._parse_sse_chunk({"choices": []}) is None
    usage_only = realm._parse_sse_chunk({"choices": [], "usage": {"total_tokens": 5}})
    assert usage_only is not None and usage_only.usage == {"total_tokens": 5}
    chunk = realm._parse_sse_chunk(
        {
            "choices": [
                {
                    "delta": {
                        "content": "hi",
                        "reasoning_content": "thinking",
                        "tool_calls": [{"id": "c1"}],
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"total_tokens": 9},
        }
    )
    assert chunk is not None
    assert chunk.content == "hi"
    assert chunk.contemplation == "thinking"
    assert chunk.tool_calls == [{"id": "c1"}]
    assert chunk.finish_reason == "stop"
    assert chunk.usage == {"total_tokens": 9}


def test_factory_empty_base_url_falls_back_to_default() -> None:
    realm = openrouter_realm_factory(api_key="k", base_url="")
    assert realm._base_url == "https://openrouter.ai/api/v1"
    assert realm.is_router is True


@pytest.mark.asyncio
async def test_complete_success_omits_tools(monkeypatch: pytest.MonkeyPatch) -> None:
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
    client = AsyncMock()
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "choices": [{"message": {"content": "hello there"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    client.post.return_value = response
    realm = OpenRouterRealm(api_key="k", client=client)
    model = _model()
    cfg = _config(model, system_prompt="sys", tools=[{"type": "function"}])
    result = await realm.complete(model, [{"role": "user", "content": "hi"}], cfg)
    assert result.invocation is not None
    assert result.invocation.content == [{"type": "text", "text": "hello there"}]
    assert result.mana_used == 15
    assert result.stop_reason == "stop"
    sent = client.post.call_args.kwargs["json"]
    assert sent["stream"] is False
    assert sent["messages"][0] == {"role": "system", "content": "sys"}
    assert "tools" not in sent


@pytest.mark.asyncio
async def test_complete_no_choices_error() -> None:
    client = AsyncMock()
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"choices": [], "usage": {}}
    client.post.return_value = response
    realm = OpenRouterRealm(api_key="k", client=client)
    result = await realm.complete(_model(), [], _config(_model()))
    assert result.error_message == "Realm returned no choices"


@pytest.mark.asyncio
async def test_complete_aborted_signal_raises() -> None:
    signal = SimpleNamespace(aborted=True)
    realm = OpenRouterRealm(api_key="k", client=AsyncMock())
    with pytest.raises(AbortError):
        await realm.complete(_model(), [], _config(_model()), signal=signal)


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
