from __future__ import annotations

import asyncio
import re
from typing import Any

from mvge.core.channel import ChannelConfig
from mvge.core.invocations import SummonerRequest
from mvge.provider.registry import RealmRegistry

from .router import SpellFileMatch, SpellSearchError


class NLTSelector:
    """YES/NO grid selection using RealmRegistry flow.
    Returns list[SpellFileMatch] (not MvgeSpell) so LazySpellRegistry
    can lazily import schemas from source files.
    """

    def __init__(
        self,
        registry: RealmRegistry,
        model_id: str = "openrouter/free",
        api_key: str = "",
        nlt_timeout: int = 30,
    ) -> None:
        self._registry = registry
        self._model_id = model_id
        self._api_key = api_key
        self._nlt_timeout = nlt_timeout

    async def select(
        self, params: dict[str, Any], matches: list[SpellFileMatch]
    ) -> list[SpellFileMatch]:
        model = self._registry.compose_model(
            model_id=params.get("model_id", self._model_id),
            api_key=self._api_key,
            provider_name=params.get("provider_name"),
        )
        if not model:
            raise SpellSearchError(
                "NLT model could not be composed; check provider configuration"
            )

        realm = self._registry.create_realm(model, api_key=model.api_key)
        if not realm:
            raise SpellSearchError(
                "NLT realm could not be created; check provider configuration"
            )

        config = ChannelConfig(
            model=model,
            temperature=0.1,
            max_tokens=1024,
        )

        prompt = self._build_prompt(params, matches)
        invocations = [SummonerRequest(role="user", content=prompt)]
        response_text = ""

        try:
            async for resp in asyncio.wait_for(
                realm.stream(model, invocations, config),
                timeout=self._nlt_timeout,
            ):
                if resp.invocation and resp.invocation.content:
                    for item in resp.invocation.content:
                        if item.get("type") == "text":
                            response_text += item.get("text", "")
        except TimeoutError:
            raise SpellSearchError(
                f"NLT selection timed out after {self._nlt_timeout}s"
            )
        except Exception as e:
            raise SpellSearchError(f"NLT selection failed: {e}") from e
        finally:
            await realm.close()

        return self._parse_yes_no_grid(response_text, matches)

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
