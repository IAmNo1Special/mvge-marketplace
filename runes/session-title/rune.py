from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

from mvge.runes.rune_api import RuneAPI
from mvge.runes.types import SigilHook

TITLE_FALLBACK_MAX_WORDS = 8
TITLE_FALLBACK_MAX_BYTES = 96
TITLE_MAX_BYTES = 120
AUTO_TITLE_AFTER_MESSAGES = 1


@dataclass
class TitleState:
    name: str | None = None
    source: str | None = None
    revision: int = 0
    user_pinned: bool = False
    fallback_done: bool = False
    llm_pending: bool = False
    eligible_count: int = 0
    first_user_text: str | None = None
    # True when the current title was adopted from a resumed session's
    # persisted name. It is not a user pin: a deliberate rename still
    # overwrites it (set_session_name clears it). While it holds, both
    # title-generation paths stay silent so a resumed run can never
    # overwrite the title it resumed with.
    restored: bool = False


def _normalize_title(text: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"[\u200b-\u200f\u202a-\u202e]", "", text)
    text = re.sub(r"\s+", " ", text)
    text = text.strip()
    return text


def _truncate_bytes(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    while encoded and len(encoded) > max_bytes:
        encoded = encoded[:-1]
    return encoded.decode("utf-8", errors="ignore")


def _fallback_title(first_user_text: str) -> str:
    normalized = _normalize_title(first_user_text)
    words = normalized.split()
    max_words = min(len(words), TITLE_FALLBACK_MAX_WORDS)
    candidate = " ".join(words[:max_words])
    return _truncate_bytes(candidate, TITLE_FALLBACK_MAX_BYTES)


def _format_messages_for_llm(messages: list[dict[str, str]]) -> str:
    parts = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        parts.append(f"{role.upper()}: {content}")
    return "\n\n".join(parts)


def _sigil_field(data: Any, name: str) -> Any:
    """Read a field from typed sigil data or a raw dict."""
    if data is None:
        return None
    if isinstance(data, dict):
        return data.get(name)
    return getattr(data, name, None)


class SessionTitleRune:
    def __init__(self, api: RuneAPI) -> None:
        self._api = api
        self._state = TitleState()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    @property
    def state(self) -> TitleState:
        return self._state

    def set_session_name(self, name: str) -> None:
        if name is None:
            return
        clean = _normalize_title(name)
        if not clean:
            return
        clean = _truncate_bytes(clean, TITLE_MAX_BYTES)
        self._state.name = clean
        self._state.source = "user"
        self._state.user_pinned = True
        # A deliberate rename supersedes any restored title.
        self._state.restored = False
        self._state.revision += 1
        self._cancel_llm()
        self._api.set_session_name(clean)

    def get_session_name(self) -> str | None:
        return self._state.name

    async def on_session_start(self, data: Any) -> None:
        self._state = TitleState()
        existing = _sigil_field(data, "existing_session_name")
        if not isinstance(existing, str):
            return
        clean = _truncate_bytes(_normalize_title(existing), TITLE_MAX_BYTES)
        if not clean:
            return
        # Adopt, do not re-apply: the engine already owns this persisted
        # name, so nothing is pushed back to the runner here. Deliberately
        # not set_session_name: a restore must not mark the title pinned.
        self._state.name = clean
        self._state.source = "restored"
        self._state.restored = True

    async def on_input(self, data: Any) -> None:
        content = _sigil_field(data, "content")
        if content is None:
            return
        text = self._extract_text(content)
        if not text:
            return
        if self._state.first_user_text is None:
            self._state.first_user_text = text
            self._state.eligible_count = 1
            await self._maybe_generate_fallback()
        else:
            self._state.eligible_count += 1
            if self._state.eligible_count >= AUTO_TITLE_AFTER_MESSAGES:
                await self._maybe_schedule_llm()

    async def on_turn_end(self, data: Any) -> None:
        if not self._state.llm_pending:
            return
        await self._maybe_schedule_llm()

    async def on_agent_end(self, data: Any) -> None:
        if self._state.llm_pending and not self._state.fallback_done:
            await self._maybe_generate_fallback()

    async def on_session_shutdown(self, data: Any) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # A task cancelled before it ever ran never executes its own
        # finally block, so shutdown must clear the flag itself -- otherwise
        # the state still claims an LLM title is pending after the session
        # is gone.
        self._state.llm_pending = False

    def _extract_text(self, content: str | list[dict[str, Any]] | None) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            return " ".join(parts)
        return str(content)

    async def _maybe_generate_fallback(self) -> None:
        async with self._lock:
            if (
                self._state.fallback_done
                or self._state.user_pinned
                or self._state.restored
            ):
                return
            if not self._state.first_user_text:
                return
            fallback = _fallback_title(self._state.first_user_text)
            if not fallback:
                return
            self._state.name = fallback
            self._state.source = "fallback"
            self._state.fallback_done = True
            self._state.revision += 1
            self._api.set_session_name(fallback)
            await self._maybe_schedule_llm()

    def _cancel_llm(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._state.llm_pending = False

    async def _maybe_schedule_llm(self) -> None:
        # The fallback gate alone cannot stop the LLM path: later resumed
        # prompts reach this scheduler directly, so the restore must gate
        # it too, on every turn, not just the first.
        if self._state.user_pinned or self._state.llm_pending or self._state.restored:
            return
        self._state.llm_pending = True
        self._task = asyncio.create_task(self._generate_llm_title())

    async def _generate_llm_title(self) -> None:
        try:
            await self._call_llm_for_title()
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001, S110 -- background title task must never crash the session
            pass
        finally:
            self._state.llm_pending = False

    async def _call_llm_for_title(self) -> None:
        # Phase 2A: titles are generated through the engine-held
        # realm-call capability, not a rune-constructed realm. This
        # also drops the old positional-provider/keyword-model
        # create_realm call, which did not match its signature.
        model_id = self._api.context.model_id
        if not model_id:
            return

        messages = [{"role": "user", "content": self._state.first_user_text or ""}]

        system = (
            "Generate a short, concise title (max 6 words, <=120 bytes) for a coding session. "
            "Respond with ONLY the title, no extra text."
        )


        prompt = f"{system}\n\nUser request: {messages[0]['content']}\n\nTitle:"

        try:
            response = await self._api.stream_model_text(
                model_id, prompt, temperature=0.3, max_tokens=50
            )
            if not response:
                return
            title = _normalize_title(response)
            if not title:
                return
            title = _truncate_bytes(title, TITLE_MAX_BYTES)
            self._state.name = title
            self._state.source = "provider"
            self._state.revision += 1
            self._api.set_session_name(title)
        except Exception:  # noqa: BLE001, S110 -- applying a title must never crash the session
            pass


def rune_factory(api: RuneAPI) -> None:
    title_rune = SessionTitleRune(api)

    api.on(SigilHook.SESSION_START, title_rune.on_session_start)
    api.on(SigilHook.INPUT, title_rune.on_input)
    api.on(SigilHook.TURN_END, title_rune.on_turn_end)
    api.on(SigilHook.AGENT_END, title_rune.on_agent_end)
    api.on(SigilHook.SESSION_SHUTDOWN, title_rune.on_session_shutdown)

    def rename_wrapper(data: Any = None) -> None:
        if isinstance(data, str):
            # The REPL dispatcher passes command text as a plain string.
            # Reading it as dict/typed data would resolve str attributes
            # instead (e.g. "title" -> the bound str.title method).
            name = data
        else:
            name = _sigil_field(data, "title") or _sigil_field(data, "name")
        if name:
            title_rune.set_session_name(str(name))

    def name_wrapper(data: Any = None) -> str | None:
        return title_rune.get_session_name()

    api.register_command("session-name", "Get current session name", name_wrapper)
    api.register_command("session-rename", "Rename current session", rename_wrapper)
