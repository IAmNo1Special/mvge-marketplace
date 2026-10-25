"""Unit tests for Cytoscape.js HTML graph generator."""

from __future__ import annotations

from pathlib import Path

from mvge_runes_okf_bridge.graph import KnowledgeGraph
from mvge_runes_okf_bridge.visualizer import generate_html_graph


def test_generate_html_graph(tmp_path: Path) -> None:
    kd = tmp_path / ".agents" / "knowledge"
    kd.mkdir(parents=True)

    (kd / "c1.md").write_text(
        "---\n"
        "type: Table\n"
        "title: Concept One\n"
        "description: First test concept\n"
        "---\n"
        "Links to [Concept Two](/c2.md).\n",
        encoding="utf-8",
    )
    (kd / "c2.md").write_text(
        "---\n"
        "type: Attested Computation\n"
        "title: Concept Two\n"
        "runtime: python\n"
        "---\n"
        "Target concept.\n",
        encoding="utf-8",
    )

    graph = KnowledgeGraph.load(cwd=tmp_path)
    html = generate_html_graph(graph, title="My Custom Bundle")

    assert "<!DOCTYPE html>" in html
    assert "My Custom Bundle" in html
    assert "cytoscape.min.js" in html
    assert "c1" in html
    assert "c2" in html
    assert "Attested Computation" in html
