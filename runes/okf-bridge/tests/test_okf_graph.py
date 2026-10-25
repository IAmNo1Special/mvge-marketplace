"""Unit tests for OKF KnowledgeGraph and backlink indexing."""

from __future__ import annotations

from pathlib import Path

import pytest
from mvge_runes_okf_bridge.graph import KnowledgeGraph


@pytest.fixture()
def isolated_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(tmp_path / "fake-global"))
    return tmp_path / "fake-global"


def _knowledge_dir(cwd: Path) -> Path:
    kd = cwd / ".agents" / "knowledge"
    kd.mkdir(parents=True)
    return kd


def test_knowledge_graph_empty(tmp_path: Path, isolated_global: Path) -> None:
    graph = KnowledgeGraph.load(cwd=tmp_path)
    assert len(graph.concepts) == 0
    assert graph.bundle_root is None
    assert graph.search("anything") == []
    assert graph.trust_summary()["human-reviewed"] == 0


def test_knowledge_graph_scan_and_backlinks(
    tmp_path: Path, isolated_global: Path
) -> None:
    kd = _knowledge_dir(tmp_path)

    # Concept A
    (kd / "service-a.md").write_text(
        "---\n"
        "type: Service\n"
        "title: Service A\n"
        "tags: [core, api]\n"
        "context: auto\n"
        "verified:\n"
        "  - by: human:dan\n"
        "---\n"
        "Links to [Service B](/service-b.md).\n",
        encoding="utf-8",
    )

    # Concept B
    (kd / "service-b.md").write_text(
        "---\n"
        "type: Service\n"
        "title: Service B\n"
        "tags: [storage]\n"
        "context: search-only\n"
        "stale_after: '2020-01-01'\n"
        "verified:\n"
        "  - by: process:bot\n"
        "---\n"
        "Independent service.\n",
        encoding="utf-8",
    )

    # Reserved files should be ignored
    (kd / "index.md").write_text("# Index\n", encoding="utf-8")
    (kd / "log.md").write_text("# Log\n", encoding="utf-8")

    graph = KnowledgeGraph.load(cwd=tmp_path)
    assert len(graph.concepts) == 2
    assert "service-a" in graph.concepts
    assert "service-b" in graph.concepts

    # Context field is parsed
    assert graph.concepts["service-a"].context == "auto"
    assert graph.concepts["service-b"].context == "search-only"

    # Backlinks
    assert graph.links_to("service-a") == ["service-b"]
    assert graph.cited_by("service-b") == ["service-a"]
    assert graph.cited_by("service-a") == []

    # Search
    results = graph.search(query="Service A")
    assert len(results) >= 1
    assert results[0].id == "service-a"

    # Search with filter
    storage_results = graph.search(query="", tag_filter="storage")
    assert len(storage_results) == 1
    assert storage_results[0].id == "service-b"

    # Summaries
    types = graph.types_summary()
    assert types["Service"] == 2

    trust = graph.trust_summary()
    assert trust["human-reviewed"] == 1
    assert trust["machine-confirmed"] == 1
    assert trust["unverified"] == 0

    assert graph.stale_count() == 1
