from __future__ import annotations

from mvge.core.channel import ChannelConfig
from mvge.core.invocations import SummonerRequest
from mvge.provider.registry import RealmRegistry

from .discovery import MCPServerInfo


class MCPNLTSelector:
    """YES/NO grid selection over MCP server candidates (word-boundary aware)."""

    def __init__(
        self,
        registry: RealmRegistry,
        model_id: str = "openrouter/free",
    ) -> None:
        self._registry = registry
        self._model_id = model_id

    async def select(
        self,
        query: str,
        candidates: list[MCPServerInfo],
        max_results: int = 3,
    ) -> list[MCPServerInfo]:
        if not candidates:
            return []

        model = self._registry.compose_model(
            model_id=self._model_id,
            api_key="",
            provider_name=None,
        )
        if not model:
            return candidates[:max_results]

        realm = self._registry.create_realm(model, api_key=model.api_key)
        try:
            config = ChannelConfig(model=model, temperature=0.1, max_tokens=512)

            prompt = self._build_prompt(query, candidates)
            invocations = [SummonerRequest(role="user", content=prompt)]
            response_text = ""
            async for resp in realm.stream(model, invocations, config):
                if resp.invocation and resp.invocation.content:
                    for item in resp.invocation.content:
                        if item.get("type") == "text":
                            response_text += item.get("text", "")

            selected_names = self._parse_grid(response_text, candidates)
            return [c for c in candidates if c.name in selected_names][:max_results]
        finally:
            try:
                await realm.close()
            except Exception:  # noqa: BLE001, S110 -- best-effort realm.close() in finally must not raise
                pass

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
