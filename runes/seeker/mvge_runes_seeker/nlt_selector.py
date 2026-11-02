from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any

from .router import SpellFileMatch, SpellSearchError


class NLTSelector:
    """YES/NO grid selection through the engine-held model-call
    capability. The rune never holds a provider key or a realm.
    Returns list[SpellFileMatch] (not MvgeSpell) so LazySpellRegistry
    can lazily import schemas from source files.
    """

    def __init__(
        self,
        stream_text: Callable[..., Awaitable[str]] | None,
        model_id: str = "openrouter/free",
        nlt_timeout: int = 30,
    ) -> None:
        self._stream_text = stream_text
        self._model_id = model_id
        self._nlt_timeout = nlt_timeout

    async def select(
        self, params: dict[str, Any], matches: list[SpellFileMatch]
    ) -> list[SpellFileMatch]:
        if self._stream_text is None:
            raise SpellSearchError(
                "NLT model call unavailable: no engine model capability"
            )
        model_id = params.get("model_id", self._model_id)
        prompt = self._build_prompt(params, matches)
        try:
            response_text = await asyncio.wait_for(
                self._stream_text(
                    model_id,
                    prompt,
                    temperature=0.1,
                    max_tokens=1024,
                    timeout_seconds=self._nlt_timeout,
                ),
                timeout=self._nlt_timeout,
            )
        except TimeoutError:
            raise SpellSearchError(
                f"NLT selection timed out after {self._nlt_timeout}s"
            ) from None
        except SpellSearchError:
            raise
        except Exception as e:
            raise SpellSearchError(f"NLT selection failed: {e}") from e

        return self._parse_yes_no_grid(response_text, matches)

    def _build_prompt(
        self, params: dict[str, Any], matches: list[SpellFileMatch]
    ) -> str:
        # Found during the Phase 2A migration: the original select()
        # called this method, but it was never defined, so every NLT
        # spell selection raised AttributeError. The prompt uses the
        # bracketed-stem grid the parser below already expects.
        lines = [
            "You are a tool selection specialist. Given a task and candidate tools,",
            "identify which tools are relevant. Output YES/NO for each.",
            "",
            f"Task: {params.get('operation', '')}",
            "",
            "Candidates:",
        ]
        for i, m in enumerate(matches):
            lines.append(f"{i + 1}. [{m.source_path.stem}]")
        lines.append("")
        lines.append("Output each name followed by -- YES or -- NO:")
        return "\n".join(lines)

    def _parse_yes_no_grid(
        self, response: str, candidates: list[SpellFileMatch]
    ) -> list[SpellFileMatch]:
        """YES/NO parsing using bracketed [stem-name] identifiers with rejection pass.
        Also supports index-based references as fallback.
        Applies Unicode NFKC normalization to candidate names for matching.
        """
        import unicodedata

        response_normalized = unicodedata.normalize("NFKC", response)
        if not response_normalized.strip():
            raise SpellSearchError("NLT returned empty response")
        selected: set[str] = set()
        rejected: set[str] = set()
        lines = response_normalized.splitlines()
        for i, c in enumerate(candidates):
            name = unicodedata.normalize("NFKC", c.source_path.stem)
            escaped = re.escape(name)
            # Pass 1: check all lines for NO rejection
            for line in lines:
                if re.search(rf"\[{escaped}\][^[]*-+\s*NO", line, re.IGNORECASE):
                    rejected.add(name)
                    break
                if re.search(
                    rf"^{i + 1}\.\s[^,[]*-+\s*NO", line.strip(), re.IGNORECASE
                ):
                    rejected.add(name)
                    break
            # Pass 2: check all lines for YES selection
            for line in lines:
                if re.search(rf"\[{escaped}\][^[]*-+\s*YES", line, re.IGNORECASE):
                    selected.add(name)
                    break
                # Index-based fallback with anchor: "1. <text> -- YES"
                if re.search(
                    rf"^{i + 1}\.\s[^,[]*-+\s*YES", line.strip(), re.IGNORECASE
                ):
                    selected.add(name)
                    break
        final = [
            c
            for c in candidates
            if c.source_path.stem in selected and c.source_path.stem not in rejected
        ]
        return final
