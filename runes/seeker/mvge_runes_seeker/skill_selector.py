from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable

from .dci_matcher import SkillFile, SkillSearchError

logger = logging.getLogger(__name__)


class SkillNLTSelector:
    """YES/NO grid selection over skill candidates (bracketed-identifier matching).
    Model calls go through the engine-held capability; no provider key
    or realm object ever reaches this selector.
    """

    def __init__(
        self,
        stream_text: Callable[..., Awaitable[str]] | None,
        model_id: str = "openrouter/free",
    ) -> None:
        self._stream_text = stream_text
        self._model_id = model_id

    async def select(
        self,
        task: str,
        candidates: list[SkillFile],
        max_results: int = 3,
    ) -> list[SkillFile]:
        if not candidates:
            return []

        if self._stream_text is None:
            return candidates[:max_results]

        prompt = self._build_prompt(task, candidates)
        try:
            response_text = await self._stream_text(
                self._model_id, prompt, temperature=0.1, max_tokens=512
            )
        except Exception as e:
            raise SkillSearchError(f"NLT selection failed: {e}") from e

        selected_names = self._parse_yes_no_grid(response_text, candidates)
        return [c for c in candidates if c.path.stem in selected_names][:max_results]

    def _build_prompt(self, task: str, candidates: list[SkillFile]) -> str:
        """Use bracketed [stem-name] as the stable identifier."""
        lines = [
            "You are a skill selection specialist. Given a task and candidate skills,",
            "identify which skills are relevant. Output YES/NO for each.",
            "",
            f"Task: {task}",
            "",
            "Skills:",
        ]
        for i, c in enumerate(candidates):
            m = c.metadata
            lines.append(
                f"{i + 1}. [{c.path.stem}] {m.get('name', c.path.stem)}: "
                f"{m.get('description', '')[:200]}"
            )
        lines.append("")
        lines.append(
            "Output each number and bracketed name followed by -- YES or -- NO:"
        )
        return "\n".join(lines)

    def _parse_yes_no_grid(
        self, response: str, candidates: list[SkillFile]
    ) -> set[str]:
        """YES/NO parsing using bracketed [stem-name] identifiers only.
        The bracketed form [bash-skill] is unambiguous â€” no bare word-boundary
        fallback, preventing substring collisions like 'bash' matching 'bash-advanced'.
        Also supports index-based references (e.g., '1. <text> -- YES') as fallback.
        Two-pass approach: first all lines checked for NO (rejection),
        then all lines checked for YES (selection).
        """
        selected: set[str] = set()
        rejected: set[str] = set()
        lines = response.splitlines()
        for i, c in enumerate(candidates):
            name = c.path.stem
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
                # Index-based fallback with proper anchor: "1. <text> -- YES"
                if re.search(
                    rf"^{i + 1}\.\s[^,[]*-+\s*YES", line.strip(), re.IGNORECASE
                ):
                    selected.add(name)
                    break
        return {n for n in selected if n not in rejected}
