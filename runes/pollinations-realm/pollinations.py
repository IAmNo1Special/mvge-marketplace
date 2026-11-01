from __future__ import annotations

import json
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


def wire_model_id(model_id: str) -> str:
    """Reduce an Mvge slug to the id Pollinations publishes.

    Mvge names a model ``<realm>/<model-id>``, but Pollinations' legacy
    endpoint publishes bare ids. The anonymous model's catalog name is
    ``openai-fast``; the wire alias proven live on 2026-10-27 is ``openai``,
    so the shipped default slug is ``pollinations/openai`` and only the
    prefix is stripped here. Passing any other bare id through unchanged
    keeps provider errors visible instead of masking them with a guess.
    """
    prefix = "pollinations/"
    return model_id.removeprefix(prefix)


_NATIVE_USER_PART_TYPES = ("text", "image_url", "file")


def _user_content_parts(content: Any) -> Any:
    """Map summoner content to the Pollinations message content shape.

    Plain strings pass through untouched. Content part lists are validated
    block by block: native multimodal parts (text, image_url, file) ride
    through in their documented wire shape, and anything unrecognized
    degrades to a text note instead of hitting the API as garbage.
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


def _is_empty_id(value: Any) -> bool:
    """True when a cast id is missing, empty, or whitespace-only.

    Such an id cannot pair a tool call with its result on the wire, and
    some providers reject the message outright. Never synthesize a
    replacement: the caller drops the entry instead.
    """
    return value is None or (isinstance(value, str) and not value.strip())


def _invocations_to_messages(invocations: list[Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for inv in invocations:
        if hasattr(inv, "role") and inv.role == "system":
            messages.append({"role": "system", "content": inv.content or ""})
        elif hasattr(inv, "role") and inv.role == "user":
            messages.append(
                {"role": "user", "content": _user_content_parts(inv.content)}
            )
        elif hasattr(inv, "role") and inv.role == "assistant":
            if hasattr(inv, "content") and inv.content:
                text_parts: list[str] = []
                tool_calls: list[dict[str, Any]] = []
                for block in inv.content:
                    if block.get("type") == "text":
                        text_parts.append(str(block.get("text", "")))
                    elif block.get("type") == "spell_cast":
                        sc = block.get("spell_cast", {})
                        if _is_empty_id(sc.get("id", "")):
                            continue
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
                elif text_parts:
                    messages.append(
                        {"role": "assistant", "content": "".join(text_parts)}
                    )
                # Neither text nor tool calls remain: emit no message.
        elif hasattr(inv, "role") and inv.role in ("spellResult", "tool"):
            cast_id = getattr(inv, "spell_cast_id", "")
            if not _is_empty_id(cast_id):
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": cast_id,
                        "content": inv.content[0].get("text", "")
                        if inv.content
                        else "",
                    }
                )
    return messages


def _with_system_prompt(
    messages: list[dict[str, Any]], system_prompt: str
) -> list[dict[str, Any]]:
    if system_prompt and not any(m.get("role") == "system" for m in messages):
        return [{"role": "system", "content": system_prompt}, *messages]
    return messages


class PollinationsRealm(SSEStreamingRealm):
    realm_name: str = "pollinations"

    @property
    def is_router(self) -> bool:
        return False

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://text.pollinations.ai/openai",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url or "https://text.pollinations.ai/openai",
            client=client,
        )

    def _prepare_request_url_and_headers(
        self, model: Model
    ) -> tuple[str, dict[str, str]]:
        base_url = (
            model.base_url or self._base_url or "https://text.pollinations.ai/openai"
        ).rstrip("/")
        api_key = model.api_key or self._api_key
        url = f"{base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        # The header is added only when there is a credential to put in it.
        # Pollinations' anonymous tier needs no key, so an empty one is the
        # normal bootstrap case rather than a misconfiguration -- and
        # `Bearer ` with nothing after it is an illegal header value, which
        # httpx refuses to put on the wire (the opencode-realm lesson).
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if model.headers:
            headers.update(model.headers)
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
        url, headers = self._prepare_request_url_and_headers(model)

        payload: dict[str, Any] = {
            "model": wire_model_id(model.id),
            "messages": messages,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "stream": True,
        }

        # No reasoning field is sent: `reasoning: {effort}` is an OpenRouter
        # extension, not part of standard Chat Completions. Pollinations
        # relays reasoning deltas anyway, and they are read below.

        if config.max_output_mana is not None:
            payload["max_tokens"] = min(config.max_tokens, config.max_output_mana)

        if config.tools:
            payload["tools"] = config.tools

        return url, headers, payload

    def _parse_sse_chunk(self, chunk: dict[str, Any]) -> SSEChunk | None:
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

        Deliberately omits `tools`: this is used for standalone requests such
        as compaction summaries, where Spells must not be offered.
        """
        if signal is not None and signal.aborted:
            raise AbortError("Operation aborted")
        effective_messages = _with_system_prompt(list(messages), config.system_prompt)
        url, headers = self._prepare_request_url_and_headers(model)
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
    "PollinationsRealm",
    "_invocations_to_messages",
    "_with_system_prompt",
    "wire_model_id",
]
