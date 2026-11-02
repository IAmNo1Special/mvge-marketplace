from __future__ import annotations

import contextlib
import re as _re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from mvge.core import DEFAULT_AGENT_NAME
from mvge.core.abort import AbortSignal
from mvge.core.spells import MvgeSpell, SpellContext, SpellExecutionMode

from . import paths
from .dci_matcher import DCISkillMatcher
from .discovery import MCPConfigDiscovery
from .lazy_loader import LazySpellRegistry
from .ledger import SeekerLedger
from .nlt_selector import NLTSelector
from .router import DCIRouter, SpellFileMatch, SpellSearchError


class SearchSpell(MvgeSpell):
    """Unified Search across Spells, Skills, and MCP servers."""

    def __init__(
        self,
        stream_text: Callable[..., Awaitable[str]] | None = None,
        spells_root: Path | None = None,
        skill_dirs: list[Path] | None = None,
        mcp_roots: list[Path] | None = None,
        rg_timeout: int = 10,
        nlt_model: str = "openrouter/free",
        agent_name: str | None = None,
        project_dir: str | Path | None = None,
        rune_api: Any | None = None,
        ledger: SeekerLedger | None = None,
    ) -> None:
        super().__init__(
            name="search",
            description="Search for capabilities (spells, skills, mcp_servers) by intent",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What capability or task you are looking for",
                    },
                    "kinds": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["spell", "skill", "mcp"],
                        },
                        "default": ["spell", "skill", "mcp"],
                        "description": "Capability kinds to search",
                    },
                    "domain": {"type": "string", "description": "Optional domain hint"},
                    "max_results": {"type": "integer", "default": 5, "minimum": 1},
                },
                "required": ["query"],
            },
            execution_mode=SpellExecutionMode.PARALLEL,
        )
        self._stream_text = stream_text
        self._explicit_spells_root = spells_root
        self._skill_dirs = skill_dirs
        self._mcp_roots = mcp_roots
        self._rg_timeout = rg_timeout
        self._nlt_model = nlt_model
        self._agent_name = agent_name
        self._project_dir = project_dir
        self._rune_api = rune_api
        self._ledger = ledger if ledger is not None else SeekerLedger()
        self._mcp_discovery = MCPConfigDiscovery(search_roots=mcp_roots)

    @property
    def spells_root(self) -> Path:
        if self._explicit_spells_root is not None:
            return self._explicit_spells_root
        return paths.spells_root(self._agent_name or DEFAULT_AGENT_NAME)

    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: AbortSignal | None = None,
        on_update: Any | None = None,
        ctx: SpellContext | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        query = (params.get("query") or params.get("operation") or "").strip()
        if not query:
            return {"total_found": 0, "results": [], "error": "query is required"}

        kinds = list(params.get("kinds") or ["spell", "skill", "mcp"])
        max_results = max(1, int(params.get("max_results") or 5))
        results: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, str]] = set()

        q_lower = query.lower()
        q_tokens = set(_re.split(r"[\s_-]+", q_lower))

        # 1. Search Spells
        if "spell" in kinds:
            # A. In-memory discoverable engine/rune spells
            if self._rune_api is not None and hasattr(
                self._rune_api, "get_discoverable_spells"
            ):
                for sp in self._rune_api.get_discoverable_spells():
                    s_name = getattr(sp, "name", "")
                    s_desc = getattr(sp, "description", "")
                    if not s_name:
                        continue
                    name_tokens = set(_re.split(r"[\s_-]+", s_name.lower()))
                    desc_lower = s_desc.lower()
                    if (
                        q_lower in s_name.lower()
                        or any(t in name_tokens for t in q_tokens if len(t) > 2)
                        or any(t in desc_lower for t in q_tokens if len(t) > 3)
                    ):
                        self._ledger.discovered_spells.add(s_name)
                        key = ("spell", s_name)
                        if key not in seen_keys:
                            seen_keys.add(key)
                            results.append(
                                {
                                    "name": s_name,
                                    "kind": "spell",
                                    "description": s_desc,
                                }
                            )

            # B. On-disk spell files via DCIRouter
            if self.spells_root.is_dir():
                router = DCIRouter(self.spells_root, rg_timeout=self._rg_timeout)
                with contextlib.suppress(Exception):
                    matches = await router.route({"operation": query})
                    for m in matches:
                        s_name = m.source_path.stem
                        self._ledger.discovered_spells.add(s_name)
                        key = ("spell", s_name)
                        if key not in seen_keys:
                            seen_keys.add(key)
                            results.append(
                                {
                                    "name": s_name,
                                    "kind": "spell",
                                    "description": m.matched_context.strip(),
                                }
                            )

        # 2. Search Skills
        if "skill" in kinds:
            # A. Discovery via DCISkillMatcher
            matcher = DCISkillMatcher(
                skill_dirs=self._skill_dirs,
                rg_timeout=self._rg_timeout,
                project_dir=self._project_dir,
            )
            with contextlib.suppress(Exception):
                available_skill_dirs = matcher.discover_skill_dirs()
                matched_skills = await matcher.match_all(available_skill_dirs, query)
                for cand in matched_skills:
                    self._ledger.discovered_skills.add(cand.name)
                    key = ("skill", cand.name)
                    if key not in seen_keys:
                        seen_keys.add(key)
                        results.append(
                            {
                                "name": cand.name,
                                "kind": "skill",
                                "description": f"Skill matching: {cand.name}",
                            }
                        )

            # B. Discovery via SkillCatalog
            if self._rune_api is not None and hasattr(
                self._rune_api, "get_skills"
            ):
                for sk in self._rune_api.get_skills():
                    if (
                        q_lower in sk.name.lower()
                        or q_lower in sk.description.lower()
                        or any(t in sk.name.lower() for t in q_tokens if len(t) > 2)
                    ):
                        self._ledger.discovered_skills.add(sk.name)
                        key = ("skill", sk.name)
                        if key not in seen_keys:
                            seen_keys.add(key)
                            results.append(
                                {
                                    "name": sk.name,
                                    "kind": "skill",
                                    "description": sk.description,
                                }
                            )

        # 3. Search MCP
        if "mcp" in kinds:
            with contextlib.suppress(Exception):
                mcp_configs = await self._mcp_discovery.search(query)
                for s_name in mcp_configs:
                    self._ledger.discovered_mcps.add(s_name)
                    key = ("mcp", s_name)
                    if key not in seen_keys:
                        seen_keys.add(key)
                        results.append(
                            {
                                "name": s_name,
                                "kind": "mcp",
                                "description": f"MCP Server providing external tools ({s_name})",
                            }
                        )

        trimmed = results[:max_results]
        return {
            "total_found": len(trimmed),
            "results": trimmed,
            "error": None,
        }


class ToolSearchSpell(MvgeSpell):
    """Subclass MvgeSpell to provide custom execute() (backward compatible)."""

    def __init__(
        self,
        stream_text: Callable[..., Awaitable[str]] | None = None,
        spells_root: Path | None = None,
        rg_timeout: int = 10,
        nlt_model: str = "openrouter/free",
        agent_name: str | None = None,
        rune_api: Any | None = None,
        ledger: SeekerLedger | None = None,
    ) -> None:
        super().__init__(
            name="tool_search",
            description="Search for spells by describing what you need",
            parameters={
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "description": "What you want to do",
                    },
                    "target": {"type": "string", "description": "Target subject"},
                    "grimoire_hint": {"type": "string", "description": "Domain hint"},
                    "max_results": {"type": "integer", "default": 5, "minimum": 1},
                },
                "required": ["operation"],
            },
            execution_mode=SpellExecutionMode.SEQUENTIAL,
        )
        self._stream_text = stream_text
        self._explicit_spells_root = spells_root
        self._rg_timeout = rg_timeout
        self._nlt_model = nlt_model
        self._agent_name = agent_name
        self._rune_api = rune_api
        self._ledger = ledger
        self._spell_registry = LazySpellRegistry()

    @property
    def spells_root(self) -> Path:
        if self._explicit_spells_root is not None:
            return self._explicit_spells_root
        return paths.spells_root(self._agent_name or DEFAULT_AGENT_NAME)

    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: AbortSignal | None = None,
        on_update: Any | None = None,
        ctx: SpellContext | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        max_results = params.get("max_results", 5)

        spells_root = self.spells_root
        absent = paths.missing([spells_root])
        if absent:
            listed = ", ".join(str(root) for root in absent)
            return {
                "spells_found": 0,
                "results": [],
                "error": f"no spells directory to search: {listed}",
            }

        # Stage 1: DCI router
        router = DCIRouter(
            spells_root=spells_root,
            rg_timeout=self._rg_timeout,
        )
        try:
            matches = await router.route(params)
        except SpellSearchError as e:
            return {"spells_found": 0, "results": [], "error": str(e)}

        if not matches:
            return {"spells_found": 0, "results": [], "error": None}

        operation = params.get("operation", "")
        if len(matches) == 1 and operation and _is_stem_match(operation, matches[0]):
            selected = [matches[0]]
        else:
            selector = NLTSelector(
                self._stream_text,
                model_id=self._nlt_model,
            )
            try:
                selected = await selector.select(params, matches)
            except SpellSearchError as e:
                return {"spells_found": 0, "results": [], "error": str(e)}

        if not selected:
            return {"spells_found": 0, "results": [], "error": None}

        selected = selected[:max_results]

        try:
            results = await self._spell_registry.load_selected(selected)
        except Exception as e:  # noqa: BLE001
            return {
                "spells_found": 0,
                "results": [],
                "error": f"schema load failed: {e}",
            }

        for r in results:
            name = r.get("name")
            if name and self._ledger is not None:
                self._ledger.discovered_spells.add(name)

        if self._rune_api is not None:
            widen = getattr(self._rune_api, "widen_global_allowlist", None)
            if callable(widen):
                widen([r["name"] for r in results if r.get("name")])

        return {"spells_found": len(results), "results": results, "error": None}


def _is_stem_match(query: str, match: SpellFileMatch) -> bool:
    import unicodedata

    stem = unicodedata.normalize("NFKC", match.source_path.stem.lower())
    q = unicodedata.normalize("NFKC", query.lower().strip())
    if not q:
        return False
    stem_normalised = _re.sub(r"[_\s-]+", " ", stem)
    q_normalised = _re.sub(r"[_\s-]+", " ", q)
    if stem_normalised == q_normalised:
        return True
    for token in q_normalised.split():
        if not _re.search(rf"\b{_re.escape(token)}\b", stem_normalised):
            return False
    return True
