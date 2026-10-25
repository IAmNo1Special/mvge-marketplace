from __future__ import annotations

import asyncio
import importlib.util
import inspect
import logging
import sys
from pathlib import Path
from typing import Any

from mvge.core.spells import MvgeSpell

from .router import SpellFileMatch

logger = logging.getLogger(__name__)


class LazySpellRegistry:
    """Load full parameter JSON only for selected spells.

    Accepts SpellFileMatch objects (file paths + context) and resolves
    the full parameter schema only when load_selected() is called.
    The actual spell module is imported lazily to extract its parameter
    definition â€” this avoids paying the import cost for unselected spells.
    Cache keyed by (grimoire, name) to prevent collision across domains.
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def load_selected(
        self, selected: list[SpellFileMatch]
    ) -> list[dict[str, Any]]:
        results = []
        for match in selected:
            name = match.source_path.stem
            key = (match.grimoire, name)
            # Double-checked locking: fast path without lock
            if key in self._cache:
                results.append(dict(self._cache[key]))
                continue
            schema = await asyncio.to_thread(self._resolve_schema, match.source_path)
            if "error" in schema:
                logger.warning(
                    "Lazy schema load failed for %s: %s", name, schema["error"]
                )
            entry = {
                "name": name,
                "description": schema.get("description", match.matched_context),
                "parameters": schema.get(
                    "parameters", {"type": "object", "properties": {}}
                ),
            }
            # Only cache successful loads; transient errors retry next time
            if "error" not in schema:
                async with self._lock:
                    # Re-check after acquiring lock to prevent races
                    if key not in self._cache:
                        self._cache[key] = entry
            results.append(entry)
        return results

    @staticmethod
    def _resolve_schema(source_path: Path) -> dict[str, Any]:
        """Import the spell module and extract the MvgeSpell parameter schema."""
        import hashlib
        import unicodedata

        module_name = unicodedata.normalize("NFKC", source_path.stem)
        path_hash = hashlib.sha256(str(source_path.resolve()).encode()).hexdigest()[:8]
        qualified_name = (
            f"_lazy_spell_{path_hash}_{source_path.parent.name}_{module_name}"
        )
        try:
            spec = importlib.util.spec_from_file_location(qualified_name, source_path)
            if spec is None or spec.loader is None:
                return {
                    "description": source_path.stem,
                    "parameters": {},
                    "error": "spec could not be created",
                }
            module = importlib.util.module_from_spec(spec)
            sys.modules[qualified_name] = module
            spec.loader.exec_module(module)
            for _name, obj in inspect.getmembers(module):
                if (
                    isinstance(obj, type)
                    and issubclass(obj, MvgeSpell)
                    and obj is not MvgeSpell
                ):
                    return {
                        "description": obj.description,
                        "parameters": obj.parameters,
                    }
                if isinstance(obj, MvgeSpell):
                    return {
                        "description": obj.description,
                        "parameters": obj.parameters,
                    }
            return {"description": source_path.stem, "parameters": {}}
        except Exception as e:
            logger.exception("Failed to resolve schema for %s", source_path)
            return {"description": source_path.stem, "parameters": {}, "error": str(e)}
