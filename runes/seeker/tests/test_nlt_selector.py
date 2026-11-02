"""NLTSelector through the engine-held model-call capability.

Phase 2A regression: the original selector called a _build_prompt
method that was never defined, so every NLT spell selection raised
AttributeError. These tests pin the prompt construction, the YES/NO
grid parsing, and the capability-call contract (timeout_seconds).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mvge_runes_seeker.nlt_selector import NLTSelector
from mvge_runes_seeker.router import SpellFileMatch, SpellSearchError


def _match(stem: str) -> SpellFileMatch:
    return SpellFileMatch(
        source_path=Path(f"{stem}.py"), grimoire="g", matched_context="c"
    )


@pytest.mark.asyncio
async def test_select_uses_stream_text_and_parses_grid() -> None:
    seen: dict[str, Any] = {}

    async def fake_stream(
        model_id: str,
        prompt: str,
        temperature: float = 0.1,
        max_tokens: int = 1024,
        timeout_seconds: float = 30.0,
    ) -> str:
        seen["model_id"] = model_id
        seen["prompt"] = prompt
        seen["timeout_seconds"] = timeout_seconds
        return "[tool_a] -- YES\n[tool_b] -- NO\n"

    selector = NLTSelector(stream_text=fake_stream)
    selected = await selector.select(
        {"operation": "do a thing"}, [_match("tool_a"), _match("tool_b")]
    )
    assert [m.source_path.stem for m in selected] == ["tool_a"]
    assert seen["model_id"] == "openrouter/free"
    assert seen["timeout_seconds"] == 30
    assert "[tool_a]" in seen["prompt"]
    assert "[tool_b]" in seen["prompt"]


@pytest.mark.asyncio
async def test_select_without_capability_raises() -> None:
    selector = NLTSelector(stream_text=None)
    with pytest.raises(SpellSearchError, match="no engine model capability"):
        await selector.select({}, [_match("tool_a")])


@pytest.mark.asyncio
async def test_select_model_override_from_params() -> None:
    seen: dict[str, Any] = {}

    async def fake_stream(model_id: str, prompt: str, **kwargs: Any) -> str:
        seen["model_id"] = model_id
        return "[tool_a] -- YES\n"

    selector = NLTSelector(stream_text=fake_stream, model_id="openrouter/free")
    await selector.select(
        {"operation": "x", "model_id": "custom/model"}, [_match("tool_a")]
    )
    assert seen["model_id"] == "custom/model"
