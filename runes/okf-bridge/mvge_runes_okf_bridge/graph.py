"""In-memory Directed Acyclic Graph (DAG) for OKF concepts and backlinks.

Bundles load from two dotagents layers (same convention as skills-bridge):
  - global:    $MVGE_GLOBAL_DIR/knowledge (default ~/.agents/knowledge)
  - workspace: <cwd>/.agents/knowledge

Layers merge by concept id; the workspace layer wins collisions.
"""

from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path

from mvge.core import global_agents_dir

from mvge_runes_okf_bridge.parser import parse_concept_file
from mvge_runes_okf_bridge.types import Concept, TrustTier

RESERVED_FILENAMES = {"index.md", "log.md"}

GLOBAL_LAYER = "global"
WORKSPACE_LAYER = "workspace"
EXPLICIT_LAYER = "explicit"


def global_config_dir() -> Path:
    """Resolve the global .agents config dir.

    Delegates to the engine resolver so the knowledge layer cannot land
    somewhere other than the runes and skills it documents.
    """
    return global_agents_dir()


def global_knowledge_root() -> Path:
    """Path of the global knowledge bundle layer."""
    return global_config_dir() / "knowledge"


def workspace_knowledge_root(cwd: Path) -> Path:
    """Path of the workspace knowledge bundle layer."""
    return cwd / ".agents" / "knowledge"


class KnowledgeGraph:
    """Represents merged OKF bundle layers with concept nodes and link edges."""

    def __init__(self, layers: list[tuple[str, Path]] | None = None) -> None:
        # Ordered (label, root); later layers win on concept-id collisions.
        self._layers: list[tuple[str, Path]] = list(layers) if layers else []
        self.concepts: dict[str, Concept] = {}
        self._backlinks: dict[str, set[str]] = defaultdict(set)

    @property
    def bundle_root(self) -> Path | None:
        """Primary bundle root: workspace layer wins, else global, else None."""
        return self._layers[-1][1] if self._layers else None

    @property
    def bundle_layers(self) -> list[Path]:
        """Loaded layer roots in merge order (global first)."""
        return [root for _, root in self._layers]

    def layer_label(self, root: Path) -> str:
        for label, layer_root in self._layers:
            if layer_root == root:
                return label
        return ""

    @classmethod
    def load(
        cls,
        cwd: Path | None = None,
        bundle_path: Path | None = None,
    ) -> KnowledgeGraph:
        """Discover and load OKF bundle layers.

        Discovery order:
        1. Explicit bundle_path (single layer, origin "explicit")
        2. Global + workspace .agents/knowledge layers, merged
           (workspace wins collisions)

        If no layer directory exists, returns an empty KnowledgeGraph.
        """
        if bundle_path is not None:
            if bundle_path.is_dir():
                return cls.load_layers([(EXPLICIT_LAYER, bundle_path)])
            return cls()

        layers: list[tuple[str, Path]] = []
        if cwd is not None:
            known_global = global_knowledge_root()
            if known_global.is_dir():
                layers.append((GLOBAL_LAYER, known_global))
            workspace = workspace_knowledge_root(cwd)
            if workspace.is_dir():
                layers.append((WORKSPACE_LAYER, workspace))
        return cls.load_layers(layers)

    @classmethod
    def load_layers(cls, layers: list[tuple[str, Path]]) -> KnowledgeGraph:
        """Load and merge an explicit ordered list of (label, root) layers."""
        graph = cls(layers=layers)
        graph.scan_and_index()
        return graph

    def scan_and_index(self) -> None:
        """Scan every layer and index all concept files (later layers win)."""
        for label, layer_root in self._layers:
            if not layer_root.is_dir():
                continue
            for root, _dirs, files in os.walk(layer_root):
                root_path = Path(root)
                for f in files:
                    if not f.endswith(".md") or f in RESERVED_FILENAMES:
                        continue
                    full_path = root_path / f
                    concept, _err = parse_concept_file(full_path, layer_root)
                    if concept is not None:
                        concept.origin = label
                        self.concepts[concept.id] = concept

        # Compute backlinks across the merged graph
        self._backlinks.clear()
        for cid, concept in self.concepts.items():
            for target in concept.links:
                self._backlinks[target].add(cid)
            # Internal sources also act as edges
            for s in concept.sources:
                res = s.resource.split("#")[0].split("?")[0].lstrip("/")
                res = res.removesuffix(".md")
                if res in self.concepts and res != cid:
                    self._backlinks[res].add(cid)

    def get(self, concept_id: str) -> Concept | None:
        """Retrieve a concept by ID."""
        clean_id = concept_id.removesuffix(".md").lstrip("/")
        return self.concepts.get(clean_id)

    def links_to(self, concept_id: str) -> list[str]:
        """List concept IDs linked by the given concept."""
        concept = self.get(concept_id)
        return list(concept.links) if concept else []

    def cited_by(self, concept_id: str) -> list[str]:
        """List concept IDs that cite or link to the given concept."""
        clean_id = concept_id.removesuffix(".md").lstrip("/")
        return sorted(self._backlinks.get(clean_id, set()))

    def search(
        self,
        query: str = "",
        type_filter: str | None = None,
        tag_filter: str | None = None,
    ) -> list[Concept]:
        """Search concepts matching query string, type, or tags."""
        q = query.lower().strip()
        results: list[tuple[int, Concept]] = []

        for concept in self.concepts.values():
            if type_filter and concept.type.lower() != type_filter.lower():
                continue
            if tag_filter and tag_filter.lower() not in [
                t.lower() for t in concept.tags
            ]:
                continue

            if not q:
                results.append((0, concept))
                continue

            score = 0
            if q in concept.id.lower():
                score += 10
            if q in concept.title.lower():
                score += 8
            if any(q in t.lower() for t in concept.tags):
                score += 5
            if q in concept.description.lower():
                score += 3
            if q in concept.body.lower():
                score += 1

            if score > 0:
                results.append((score, concept))

        results.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in results]

    def types_summary(self) -> dict[str, int]:
        """Return counts of concepts grouped by type."""
        summary: dict[str, int] = defaultdict(int)
        for c in self.concepts.values():
            summary[c.type] += 1
        return dict(summary)

    def trust_summary(self) -> dict[str, int]:
        """Return counts of concepts grouped by trust tier."""
        summary = {
            TrustTier.UNVERIFIED.value: 0,
            TrustTier.MACHINE_CONFIRMED.value: 0,
            TrustTier.HUMAN_REVIEWED.value: 0,
        }
        for c in self.concepts.values():
            summary[c.trust_tier.value] += 1
        return summary

    def stale_count(self) -> int:
        """Count how many concepts are past their stale_after date."""
        return sum(1 for c in self.concepts.values() if c.is_stale)
