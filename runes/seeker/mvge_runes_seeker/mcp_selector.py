from __future__ import annotations

from collections.abc import Awaitable, Callable

from .discovery import MCPServerInfo


class MCPNLTSelector:
    """YES/NO grid selection over MCP server candidates (word-boundary aware).
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
        query: str,
        candidates: list[MCPServerInfo],
        max_results: int = 3,
    ) -> list[MCPServerInfo]:
        if not candidates:
            return []

        if self._stream_text is None:
            return candidates[:max_results]

        prompt = self._build_prompt(query, candidates)
        response_text = await self._stream_text(
            self._model_id, prompt, temperature=0.1, max_tokens=512
        )
        selected_names = self._parse_grid(response_text, candidates)
        return [c for c in candidates if c.name in selected_names][:max_results]

    def _build_prompt(self, query: str, candidates: list[MCPServerInfo]) -> str:
        lines = [
            "Select MCP servers matching the query. Output YES/NO for each.",
            "",
            f"Query: {query}",
            "",
            "Candidates:",
        ]
        for i, c in enumerate(candidates):
            lines.append(
                f"{i + 1}. {c.name} ({c.transport}): {c.config.get('description', '')[:200]}"
            )
        lines.append("")
        lines.append("Output each name followed by -- YES or -- NO:")
        return "\n".join(lines)

    def _parse_grid(self, response: str, candidates: list[MCPServerInfo]) -> set[str]:
        """Word-boundary-aware YES/NO parsing.
        Uses regex word boundary to prevent 'fs' matching 'filesystem'.
        """
        import re

        selected = set()
        for c in candidates:
            escaped = re.escape(c.name)
            for line in response.splitlines():
                if re.search(rf"\b{escaped}\b[^,]*--\s*YES", line, re.IGNORECASE):
                    selected.add(c.name)
                    break
        return selected
