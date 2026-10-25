"""The wire contract this Realm has to keep with OpenCode Zen.

Two kinds of check live here, and the split is deliberate.

Manifest and registration facts are cheap and offline: the file has to parse,
the entry point has to exist, and ``rune_factory`` has to register a factory and
a provider under the prefix a model slug would carry. Those are the things that
make the Rune *installable and discoverable*, and a mistake in any of them
produces a Rune that loads and does nothing.

Everything after that asserts on the request and the response as the gateway
actually sees them, driven through an ``httpx`` mock transport rather than by
calling private helpers. The reason is that the base URL is the only real
difference between this Realm and any other Chat Completions Realm, and it is
also the single value most likely to be wrong in a way no import would catch: a
Realm pointed at the wrong host still imports, still registers, and still fails
every single call at runtime. Asserting the request that leaves the process is
the only version of that test that would notice.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from mvge.core.channel import ChannelConfig, Model
from mvge.core.invocations import MvgeResponse, SummonerRequest
from mvge.core.spells import SpellResultMessage
from mvge.runes.rune_api import RuneAPI
from mvge.runes.types import SigilHook
from mvgeos_runes_opencode_realm.realm import (
    ZEN_BASE_URL,
    OpenCodeRealm,
    _invocations_to_messages,
    wire_model_id,
)
from mvgeos_runes_opencode_realm.rune import (
    after_provider_response,
    before_provider_request,
    opencode_realm_factory,
    rune_factory,
)

MANIFEST_PATH = Path(__file__).parent.parent / "manifest.json"


def _sse(*chunks: str) -> bytes:
    return "".join(f"data: {c}\n\n" for c in chunks).encode() + b"data: [DONE]\n\n"


def _turn_chunks() -> tuple[str, ...]:
    """A turn in the shape the standard actually streams it.

    Usage arrives at the top level of a later chunk, not inside ``choices``, and
    after the chunk carrying ``finish_reason``. That ordering is the whole reason
    the engine asks for ``stream_options.include_usage``, so a fixture that
    merged the two would pass without ever exercising the path that assembles the
    final response.
    """
    return (
        '{"choices":[{"delta":{"content":"hello"}}]}',
        '{"choices":[{"delta":{},"finish_reason":"stop"}]}',
        (
            '{"choices":[],"usage":'
            '{"prompt_tokens":4,"completion_tokens":7,"total_tokens":11}}'
        ),
    )


def _model(**overrides: object) -> Model:
    fields: dict[str, object] = {
        "id": "opencode/space-bunny-free",
        "name": "Space Bunny Free",
        "realm": "opencode",
        "base_url": "",
        "api_key": "test-key",
    }
    fields.update(overrides)
    return Model(**fields)  # type: ignore[arg-type]


def _client(handler: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


async def _drain(
    realm: OpenCodeRealm,
    model: Model,
    config: ChannelConfig,
    invocations: list | None = None,
) -> list:
    return [r async for r in realm.stream(model, invocations or [], config)]


# ---------------------------------------------------------------------------
# Installability and discovery
# ---------------------------------------------------------------------------


def test_manifest_declares_the_rune() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest["name"] == "opencode-realm"
    assert manifest["version"] == "0.1.0"
    assert manifest["entry_point"] == "rune.py"
    assert manifest["runtime"] == "python"
    assert manifest["types"] == ["RealmProvider"]
    assert "httpx>=0.27" in manifest["python_deps"]
    assert manifest["hooks"] == [
        "before_provider_request",
        "after_provider_response",
    ]
    assert manifest["enabled"] is True


def test_rune_factory_registers_the_opencode_prefix() -> None:
    mock_api = MagicMock(spec=RuneAPI)
    rune_factory(mock_api)

    mock_api.register_realm_factory.assert_called_once_with(
        "opencode", opencode_realm_factory
    )
    mock_api.register_provider.assert_called_once_with(
        "opencode", {"baseUrl": ZEN_BASE_URL}
    )
    mock_api.on.assert_any_call(
        SigilHook.BEFORE_PROVIDER_REQUEST, before_provider_request
    )
    mock_api.on.assert_any_call(
        SigilHook.AFTER_PROVIDER_RESPONSE, after_provider_response
    )


@pytest.mark.asyncio
async def test_hooks_pass_their_payload_through() -> None:
    data = {"sample": "value"}
    assert await before_provider_request(data) is data
    assert await after_provider_response(data) is data


def test_registered_base_url_is_zens_own() -> None:
    """The provider config is the only thing a bare slug has to resolve against.

    The engine composes an unknown model id from the registered provider prefix
    and reads ``baseUrl`` from here. If this drifts from the constant the Realm
    itself defaults to, a slug resolves to a Realm pointed somewhere else and
    nothing reports the disagreement.
    """
    assert ZEN_BASE_URL == "https://opencode.ai/zen/v1"


def test_factory_falls_back_to_zen_when_the_base_url_is_empty() -> None:
    realm = opencode_realm_factory(api_key="k", base_url="")
    assert isinstance(realm, OpenCodeRealm)
    assert realm.realm_name == "opencode"


def test_zen_is_not_reported_as_a_router() -> None:
    """Zen fronts many vendors, but it does not expose a provider tier.

    ``is_router`` is what the CLI and the GUI read to decide whether to ask the
    Summoner to choose a provider. Claiming it here would put a one-option
    provider prompt in front of every user of the default model and assert a
    choice Zen's API does not offer.
    """
    assert opencode_realm_factory(api_key="k").is_router is False


# ---------------------------------------------------------------------------
# The model id on the wire
# ---------------------------------------------------------------------------


def test_the_realm_prefix_is_stripped_from_the_model_id() -> None:
    """Zen publishes slash-free ids, so a forwarded slug cannot be resolved.

    Every entry in ``/zen/v1/models`` is a bare id. Asking the gateway for
    ``opencode/space-bunny-free`` fails on model lookup with an error that names
    the wrong thing, which is why this cannot be left to the caller.
    """
    assert wire_model_id("opencode/space-bunny-free") == "space-bunny-free"
    assert wire_model_id("space-bunny-free") == "space-bunny-free"


@pytest.mark.asyncio
async def test_the_streams_model_id_is_bare() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, content=_sse(*_turn_chunks()))

    realm = OpenCodeRealm(api_key="test-key", client=_client(handler))
    model = _model()
    await _drain(realm, model, ChannelConfig(model=model))

    assert seen[0]["model"] == "space-bunny-free"


# ---------------------------------------------------------------------------
# The request that leaves the process
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_goes_to_the_zen_chat_completions_endpoint() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=_sse(*_turn_chunks()))

    realm = OpenCodeRealm(api_key="test-key", client=_client(handler))
    await _drain(realm, _model(), ChannelConfig(model=_model()))

    assert len(seen) == 1
    assert str(seen[0].url) == f"{ZEN_BASE_URL}/chat/completions"
    assert seen[0].headers["Authorization"] == "Bearer test-key"
    assert seen[0].headers["Content-Type"] == "application/json"


@pytest.mark.asyncio
async def test_a_keyless_request_sends_no_authorization_header() -> None:
    """Zen's free tier needs no key, so an empty one must not become a header.

    ``Bearer `` with nothing after it is an illegal header value and httpx
    refuses to put it on the wire, raising before the request is sent. A mock
    transport accepts it happily, which is why this only surfaced against the
    live gateway -- and it broke the one Summoner this Realm exists to serve.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=_sse(*_turn_chunks()))

    realm = OpenCodeRealm(api_key="", client=_client(handler))
    model = _model(api_key="")
    await _drain(realm, model, ChannelConfig(model=model))

    assert "Authorization" not in seen[0].headers


@pytest.mark.asyncio
async def test_streams_text_and_reports_mana_usage() -> None:
    """The turn ends in one complete response that carries the text and the Mana.

    Streaming yields each delta as it arrives *and* a final block with the
    accumulated text, so the transcript is assembled from the last response, not
    from concatenating everything that was yielded.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse(*_turn_chunks()))

    realm = OpenCodeRealm(api_key="test-key", client=_client(handler))
    model = _model()
    responses = await _drain(realm, model, ChannelConfig(model=model))

    final = responses[-1]
    assert final.invocation is not None
    assert final.invocation.content == [{"type": "text", "text": "hello"}]
    assert final.invocation.realm == "opencode"
    assert final.invocation.mana_usage["total"] == 11
    assert final.stop_reason == "stop"


@pytest.mark.asyncio
async def test_a_spell_cast_becomes_a_tool_call_on_the_wire() -> None:
    """Spells only work if a cast in the transcript leaves as a ``tool_call``."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, content=_sse(*_turn_chunks()))

    invocations = [
        SummonerRequest(role="user", content="list the files"),
        MvgeResponse(
            content=[
                {"type": "text", "text": "working"},
                {
                    "type": "spell_cast",
                    "spell_cast": {
                        "id": "call_1",
                        "name": "bash",
                        "arguments": {"command": "ls"},
                    },
                },
            ]
        ),
        SpellResultMessage(
            spell_cast_id="call_1",
            spell_name="bash",
            content=[{"type": "text", "text": "a.txt"}],
        ),
    ]

    realm = OpenCodeRealm(api_key="test-key", client=_client(handler))
    model = _model()
    await _drain(realm, model, ChannelConfig(model=model), invocations)

    messages = seen[0]["messages"]
    assert messages[0] == {"role": "user", "content": "list the files"}
    assert messages[1]["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "bash", "arguments": '{"command": "ls"}'},
        }
    ]
    # The tool result must carry the id of the call it answers, or the gateway
    # rejects the turn with an unanswered tool call.
    assert messages[2] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "a.txt",
    }


@pytest.mark.asyncio
async def test_offered_spells_reach_the_wire_as_tools() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, content=_sse(*_turn_chunks()))

    realm = OpenCodeRealm(api_key="test-key", client=_client(handler))
    model = _model()
    config = ChannelConfig(
        model=model,
        tools=[{"type": "function", "function": {"name": "bash"}}],
    )
    await _drain(realm, model, config)

    assert seen[0]["tools"] == config.tools


@pytest.mark.asyncio
async def test_the_request_carries_no_vendor_specific_parameters() -> None:
    """Standard fields only, even when the Summoner asks for Contemplation.

    ``reasoning`` is an OpenRouter extension. A Realm that forwarded it would
    put a proprietary field on a standard boundary; the test exists because that
    is exactly the sort of thing that gets added back while trying to make a
    reasoning model work.
    """
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, content=_sse(*_turn_chunks()))

    realm = OpenCodeRealm(api_key="test-key", client=_client(handler))
    model = _model()
    await _drain(realm, model, ChannelConfig(model=model, contemplation_level="high"))

    assert "reasoning" not in seen[0]
    assert "stream_options" in seen[0]


@pytest.mark.asyncio
async def test_the_output_mana_cap_wins_over_the_channel_maximum() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, content=_sse(*_turn_chunks()))

    realm = OpenCodeRealm(api_key="test-key", client=_client(handler))
    model = _model()
    config = ChannelConfig(model=model, max_tokens=4096, max_output_mana=256)
    await _drain(realm, model, config)

    assert seen[0]["max_tokens"] == 256


@pytest.mark.asyncio
async def test_a_per_model_base_url_overrides_the_realm_default() -> None:
    """A model that names its own base URL has to win.

    The realm default is a hardcoded host, so a Realm that ignored a per-model
    base URL could not reach any other gateway endpoint at all.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=_sse(*_turn_chunks()))

    realm = OpenCodeRealm(api_key="test-key", client=_client(handler))
    model = _model(base_url="https://example.test/gateway/v1/")
    await _drain(realm, model, ChannelConfig(model=model))

    assert str(seen[0].url) == "https://example.test/gateway/v1/chat/completions"


# ---------------------------------------------------------------------------
# Responses from the gateway
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_upstream_error_is_reported_rather_than_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            content=json.dumps({"error": {"message": "No auth credentials found"}}),
        )

    realm = OpenCodeRealm(api_key="bad-key", client=_client(handler))
    model = _model()
    responses = await _drain(realm, model, ChannelConfig(model=model))

    assert responses[-1].error_message == "No auth credentials found"
    assert responses[-1].error_code == "auth_failed"


@pytest.mark.asyncio
async def test_contemplation_deltas_from_an_upstream_model_are_not_dropped() -> None:
    """Zen relays upstream deltas, so a thinking model still lands in the Tome.

    Nothing in this Realm asks for reasoning, but if an upstream model emits it
    anyway then dropping the delta would silently delete the Summoner's
    reasoning from the transcript.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse(
                '{"choices":[{"delta":{"reasoning":"weighing it"}}]}',
                '{"choices":[{"delta":{"content":"answer"}},'
                '"finish_reason":"stop","usage":{"total_tokens":4}}]}',
            ),
        )

    realm = OpenCodeRealm(api_key="test-key", client=_client(handler))
    model = _model()
    responses = await _drain(realm, model, ChannelConfig(model=model))

    kinds = [
        block.get("type")
        for response in responses
        if response.invocation
        for block in response.invocation.content
    ]
    assert "contemplation" in kinds


@pytest.mark.asyncio
async def test_a_completion_offers_no_spells() -> None:
    """The non-channelled path must not advertise tools it cannot answer.

    ``complete`` serves standalone requests such as compaction summaries. Offering
    Spells there invites a tool call with no tool loop behind it.
    """
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            content=json.dumps(
                {
                    "choices": [{"message": {"content": "summary"}}],
                    "usage": {"total_tokens": 9},
                }
            ),
        )

    realm = OpenCodeRealm(api_key="test-key", client=_client(handler))
    model = _model()
    config = ChannelConfig(model=model, tools=[{"type": "function"}])
    result = await realm.complete(
        model, [{"role": "user", "content": "summarize"}], config
    )

    assert seen[0]["stream"] is False
    assert "tools" not in seen[0]
    assert seen[0]["model"] == "space-bunny-free"
    assert result.invocation is not None
    assert result.invocation.content == [{"type": "text", "text": "summary"}]
    assert result.invocation.realm == "opencode"
    assert result.mana_used == 9


# ---------------------------------------------------------------------------
# Content blocks
# ---------------------------------------------------------------------------


def test_a_string_message_stays_a_string() -> None:
    inv = SummonerRequest(role="user", content="hello")
    assert _invocations_to_messages([inv]) == [{"role": "user", "content": "hello"}]


def test_native_multimodal_parts_survive_intact() -> None:
    parts = [
        {"type": "text", "text": "look at this"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGk="}},
        {
            "type": "file",
            "file": {
                "filename": "doc.pdf",
                "file_data": "data:application/pdf;base64,aGk=",
            },
        },
    ]
    inv = SummonerRequest(role="user", content=parts)
    assert _invocations_to_messages([inv]) == [{"role": "user", "content": parts}]


def test_an_unknown_content_block_becomes_a_text_note() -> None:
    """Degrade loudly in the transcript, not silently at the gateway.

    Dropping the block would remove content the Summoner believes they sent.
    Forwarding it raw would produce an error they cannot act on. A text note
    keeps the fact that something was there and names it.
    """
    inv = SummonerRequest(
        role="user",
        content=[{"type": "mystery", "mystery": {"x": 1}}],
    )
    content = _invocations_to_messages([inv])[0]["content"]
    assert content[0]["type"] == "text"
    assert "mystery" in content[0]["text"]
