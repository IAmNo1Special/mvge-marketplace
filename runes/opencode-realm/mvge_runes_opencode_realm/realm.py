"""OpenCode Zen as a Mvge Realm.

Zen is an OpenAI Chat Completions compatible gateway: it serves
``https://opencode.ai/inference/openai/v1/chat/completions`` and advertises
its catalog at ``https://opencode.ai/inference/v1/models`` in the standard
``/v1/models`` shape. So this Realm speaks the same wire dialect as any
other OpenAI-compatible endpoint, and the only thing that distinguishes it
from ``openrouter-realm`` is its base URL and its bearer credential.

What it does *not* do is inherit OpenRouter's extensions. ``reasoning: {effort}``
is an OpenRouter-specific request field, not part of the Chat Completions
standard, so this Realm never sends it: a wire boundary carries standard fields
only. Contemplation deltas are still *read*, because a gateway is free to relay
whatever an upstream model emits and dropping it would lose the Summoner's
reasoning from the transcript.

Every model on Zen is reachable with an ``opencode/<model-id>`` slug, and the
free tier (``space-bunny-free``, ``big-pickle``, ``nemotron-3-ultra-free`` and
the rest) is reachable with the same key. A free model is a normal model here;
nothing about this Realm is a free-only path.

The same Rune also provides the Go product as a second Realm entry,
``opencode-go``, pointed at ``https://opencode.ai/inference/go/v1`` and
reached with ``opencode-go/<model-id>`` slugs. Zen and Go share the one
``OPENCODE_API_KEY``; the provider name in the slug is what chooses the
billing lane, and there is no fallback from one product to the other.

Every request sends ``x-opencode-session``. Go hard-rejects a request
without it (400 MissingSessionID); Docker sends it on Zen requests too,
derived from the agent session so a conversation keeps one id. The
precedence is: an explicit header in ``model.headers`` first, then
``config.meta_data["session_id"]`` (the engine's tome id), then a UUID
generated once per Realm instance -- never a fresh value per request,
which would defeat the routing the header exists for.

The old ``/zen/*`` bases are not claimed dead: ``/zen/*`` and
``/inference/*`` have been documented as aliases for Go, and a per-model
``base_url`` override still reaches the old bases. The switch here is a
canonical-base change to the current OpenCode Inference API endpoints,
and it still needs a live paid-model test before it is trusted.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
from mvge.core.abort import (
    AbortError,
    AbortSignal,
)
from mvge.core.channel import (
    ChannelConfig,
    Model,
    MvgeResponse,
    RealmResponse,
    StopReason,
)
from mvge.provider import (
    SSEChunk,
    SSEStreamingRealm,
    retry_realm_request,
)

#: Current OpenCode Inference API Chat Completions base for Zen. This is
#: NOT the models-list base (``/inference/v1/models``): the realm appends
#: ``/chat/completions`` to this base.
ZEN_BASE_URL = "https://opencode.ai/inference/openai/v1"

#: The OpenCode Go endpoint, the counterpart of the Zen endpoint above.
#: The realm appends ``/chat/completions`` to this base as well.
GO_BASE_URL = "https://opencode.ai/inference/go/v1"

#: The path the gateways serve chat completions on, relative to the base URL.
CHAT_COMPLETIONS_PATH = "/chat/completions"

#: Header OpenCode routes per conversation on. Go hard-rejects a request
#: without it; Docker sends it on Zen requests too.
SESSION_HEADER = "x-opencode-session"

#: The prefix Mvge puts on a slug to say which Realm serves it.
REALM_SLUG_PREFIX = "opencode/"

#: The prefix for the Go Realm entry, provided by the same Rune.
GO_REALM_SLUG_PREFIX = "opencode-go/"


def wire_model_id(model_id: str) -> str:
    """Reduce a Mvge slug to the id the gateway publishes.

    Mvge names a model ``<realm>/<model-id>``, but the gateway catalogs are
    slash-free: every entry in a gateway catalog is a bare id such as
    ``space-bunny-free``. Forwarding the slug unchanged would ask the gateway
    for a model named ``opencode/space-bunny-free``, which is not in its
    catalog, so every request fails on model lookup rather than on anything a
    reader could diagnose.

    Stripping is unambiguous for the same reason it is necessary: no gateway
    model id contains a ``/``, so a leading ``opencode/`` or ``opencode-go/``
    can only be the Realm prefix and never part of the id. An already-bare id
    is returned unchanged, so both spellings work.
    """
    if model_id.startswith(GO_REALM_SLUG_PREFIX):
        return model_id[len(GO_REALM_SLUG_PREFIX) :]
    if model_id.startswith(REALM_SLUG_PREFIX):
        return model_id[len(REALM_SLUG_PREFIX) :]
    return model_id


#: Content block types that have a documented Chat Completions wire shape.
#:
#: Anything outside this list degrades to a text note rather than reaching the
#: API as a block the gateway cannot interpret. See ``_user_content_parts``.
_NATIVE_USER_PART_TYPES = ("text", "image_url", "file")


def _user_content_parts(content: Any) -> Any:
    """Map Summoner content to the Chat Completions message content shape.

    A plain string passes through untouched. A list of parts is validated block
    by block: the native multimodal parts (``text``, ``image_url``, ``file``)
    ride through in their documented shape, and anything unrecognized becomes a
    text note. The fallback is deliberate -- a malformed block that we drop
    silently would remove content the Summoner believes they sent, and one that
    we forward raw would be rejected by the gateway with an error the Summoner
    cannot act on.
    """
    if not isinstance(content, list):
        return content or ""
    parts: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") in _NATIVE_USER_PART_TYPES:
            parts.append(block)
        else:
            block_type = (
                block.get("type") if isinstance(block, dict) else type(block).__name__
            )
            parts.append(
                {
                    "type": "text",
                    "text": f"[Unsupported content block: {block_type}]",
                }
            )
    return parts


def _invocations_to_messages(invocations: list[Any]) -> list[dict[str, Any]]:
    """Map transcript Invocations to Chat Completions messages.

    The mapping is the standard one: a system Invocation becomes a ``system``
    message, a Summoner Invocation a ``user`` message, an assistant Invocation's
    text blocks become its ``content``, its ``spell_cast`` blocks become
    ``tool_calls``, and a spell result becomes a ``tool`` message keyed by the
    Spell cast id. That last part is what makes Spells work: the wire format
    pairs a ``tool_calls`` entry with its result by id, and an Invocation with
    no matching id produces a gateway-side "tool call has no result" rejection.
    """
    messages: list[dict[str, Any]] = []
    for inv in invocations:
        role = getattr(inv, "role", None)
        if role == "system":
            messages.append({"role": "system", "content": inv.content or ""})
        elif role == "user":
            messages.append(
                {"role": "user", "content": _user_content_parts(inv.content)}
            )
        elif role == "assistant":
            if not getattr(inv, "content", None):
                continue
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for block in inv.content:
                if block.get("type") == "text":
                    text_parts.append(str(block.get("text", "")))
                elif block.get("type") == "spell_cast":
                    sc = block.get("spell_cast", {})
                    tool_calls.append(
                        {
                            "id": sc.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": sc.get("name", ""),
                                "arguments": json.dumps(sc.get("arguments", {})),
                            },
                        }
                    )
            if tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": "".join(text_parts) or None,
                        "tool_calls": tool_calls,
                    }
                )
            else:
                messages.append({"role": "assistant", "content": "".join(text_parts)})
        elif role in ("spellResult", "tool"):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": getattr(inv, "spell_cast_id", ""),
                    "content": inv.content[0].get("text", "") if inv.content else "",
                }
            )
    return messages


def _with_system_prompt(
    messages: list[dict[str, Any]], system_prompt: str
) -> list[dict[str, Any]]:
    """Prepend the system prompt unless the transcript already carries one.

    A transcript that already opens with a system message is authoritative: the
    Mvge assembled it, and prepending a second system message would put two
    instructions in front of the model with no defined precedence.
    """
    if system_prompt and not any(m.get("role") == "system" for m in messages):
        return [{"role": "system", "content": system_prompt}, *messages]
    return messages


class OpenCodeRealm(SSEStreamingRealm):
    """Channel model responses through an OpenCode gateway.

    A default instance is the Zen Realm. A Go instance is the same class
    constructed with ``realm_name="opencode-go"``; its default base is Go,
    never Zen, whether the base comes from the Go factory or from direct
    construction with no base at all, so a Go instance with no per-model
    base URL and an empty Realm base still cannot silently land on Zen.
    """

    realm_name: str = "opencode"

    @property
    def is_router(self) -> bool:
        # False, and the reason is the shape of the API rather than the number of
        # vendors behind it. Zen does front many upstreams, but it presents every
        # model as a flat id: there is no provider tier to pick from. `is_router`
        # is what the CLI and the GUI read to decide whether to ask for one, so
        # answering True here would put a one-option provider prompt in front of
        # every Summoner on the default model and claim a choice that does not
        # exist.
        return False

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        realm_name: str = "opencode",
        default_base_url: str | None = None,
    ) -> None:
        # ``base_url`` defaults to None, not to the Zen base, on purpose:
        # a Go instance constructed directly with no base must default to
        # Go, and a Zen default here would win over the Go default below
        # and silently bill a Go instance to Zen. An explicit base, or an
        # empty base, still falls through to the per-realm default.
        self.realm_name = realm_name
        if default_base_url is None:
            default_base_url = (
                GO_BASE_URL if realm_name == "opencode-go" else ZEN_BASE_URL
            )
        self._default_base_url = default_base_url
        # Generated once per instance, never per request: a session id
        # that changed mid-conversation would defeat the per-conversation
        # routing this header exists for. This is only the last-resort
        # value -- an explicit model header or a config session id wins.
        self._session_id = str(uuid.uuid4())
        super().__init__(
            api_key=api_key,
            base_url=base_url or default_base_url,
            client=client,
        )

    def _prepare_request_url_and_headers(
        self, model: Model, config: ChannelConfig | None = None
    ) -> tuple[str, dict[str, str]]:
        """Resolve the request URL and headers for one model.

        A model may override the base URL and add headers. That matters for
        the multi-endpoint catalogs: most models are served from the
        product's Chat Completions base, but some (the Anthropic- and
        OpenAI-Responses-shaped ones) are not, so a per-model ``base_url``
        is the only way to reach them. Resolution order is model, then
        Realm, then the instance default (Zen for a Zen instance, Go for
        a Go instance), so an unset field on all three still produces a
        usable URL -- and a Go instance never falls back to Zen.

        The session header follows its own precedence, highest first:
        an explicit header in ``model.headers``, then
        ``config.meta_data["session_id"]`` (the engine's tome id), then
        the UUID generated once for this Realm instance. The header is
        sent on every request, for both products: Go hard-rejects a
        request without it, and Docker sends it on Zen requests too.
        """
        base_url = (model.base_url or self._base_url or self._default_base_url).rstrip(
            "/"
        )
        api_key = model.api_key or self._api_key
        url = f"{base_url}{CHAT_COMPLETIONS_PATH}"
        headers = {"Content-Type": "application/json"}
        # The header is added only when there is a credential to put in it.
        # Zen's free tier is reachable without a key, so an empty one is the
        # normal case rather than a misconfiguration -- and `Bearer ` with
        # nothing after it is an illegal header value, which httpx refuses to
        # put on the wire. The result was that a keyless Summoner, the exact
        # person this Realm exists to serve, got a LocalProtocolError before
        # the request left the process.
        #
        # A Rune's own headers still win, so a caller that wants to send a
        # credential another way is not overridden.
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if model.headers:
            headers.update(model.headers)
        if not any(key.lower() == SESSION_HEADER for key in headers):
            session_id = ""
            if config is not None:
                session_id = str(config.meta_data.get("session_id") or "")
            headers[SESSION_HEADER] = session_id or self._session_id
        return url, headers

    def _prepare_request(
        self,
        model: Model,
        invocations: list[Any],
        config: ChannelConfig,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        messages = _with_system_prompt(
            _invocations_to_messages(invocations), config.system_prompt
        )
        url, headers = self._prepare_request_url_and_headers(model, config)

        payload: dict[str, Any] = {
            "model": wire_model_id(model.id),
            "messages": messages,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "stream": True,
        }

        # Contemplation is requested by capping output rather than by a vendor
        # parameter. ``reasoning: {effort}`` is OpenRouter's extension and is not
        # part of the standard, so sending it here would put a proprietary field
        # on a standard boundary for no gain: a model that does not recognise it
        # ignores it, and one that does has no reason to honour it from us.
        if config.max_output_mana is not None:
            payload["max_tokens"] = min(config.max_tokens, config.max_output_mana)

        if config.tools:
            payload["tools"] = config.tools

        return url, headers, payload

    def _parse_sse_chunk(self, chunk: dict[str, Any]) -> SSEChunk | None:
        """Map one streamed delta onto the engine's channel vocabulary.

        ``reasoning`` and ``reasoning_content`` are read even though nothing in
        this Realm asks for reasoning, because Zen relays upstream deltas
        verbatim and an upstream model that thinks out loud should land in the
        transcript as Contemplation rather than being dropped.
        """
        usage = chunk.get("usage")
        choices = chunk.get("choices") or []
        if not choices:
            if usage:
                return SSEChunk(usage=usage)
            return None

        choice = choices[0]
        delta = choice.get("delta", {})
        contemplation = delta.get("reasoning") or delta.get("reasoning_content")

        return SSEChunk(
            content=delta.get("content"),
            contemplation=contemplation,
            tool_calls=delta.get("tool_calls"),
            finish_reason=choice.get("finish_reason"),
            usage=usage,
        )

    async def complete(
        self,
        model: Model,
        messages: list[dict[str, Any]],
        config: ChannelConfig,
        signal: AbortSignal | None = None,
    ) -> RealmResponse:
        """Run one non-channelled completion.

        Deliberately omits `tools`: this path serves standalone requests such as
        compaction summaries, where offering Spells would invite the model to
        cast them with no tool loop behind it to answer the results.
        """
        if signal is not None and signal.aborted:
            raise AbortError("Operation aborted")
        effective_messages = _with_system_prompt(list(messages), config.system_prompt)
        url, headers = self._prepare_request_url_and_headers(model, config)
        payload: dict[str, Any] = {
            "model": wire_model_id(model.id),
            "messages": effective_messages,
            "stream": False,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
        }

        async def do_request() -> Any:
            return await self._client.post(
                url,
                headers=headers,
                json=payload,
                timeout=config.timeout_ms / 1000,
            )

        response = await retry_realm_request(
            do_request, max_retries=config.max_retries, signal=signal
        )

        if response.status_code != 200:
            return self._parse_error(response).to_response(model)

        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            return RealmResponse(
                model=model,
                error_message="Realm returned no choices",
            )

        content = choices[0].get("message", {}).get("content") or ""
        usage = data.get("usage", {})
        return RealmResponse(
            model=model,
            invocation=MvgeResponse(
                role="assistant",
                content=[{"type": "text", "text": content}],
                realm=self.realm_name,
                model=model.id,
                stop_reason=StopReason.STOP,
                mana_usage=self._parse_usage(usage),
            ),
            mana_used=usage.get("total_tokens", 0),
            stop_reason=StopReason.STOP.value,
        )


__all__ = [
    "CHAT_COMPLETIONS_PATH",
    "GO_BASE_URL",
    "GO_REALM_SLUG_PREFIX",
    "REALM_SLUG_PREFIX",
    "SESSION_HEADER",
    "ZEN_BASE_URL",
    "OpenCodeRealm",
    "_invocations_to_messages",
    "_with_system_prompt",
    "wire_model_id",
]
