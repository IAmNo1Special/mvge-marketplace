"""Unit tests for OKF working-concept rendering and context transformation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mvge_runes_okf_bridge.graph import KnowledgeGraph
from mvge_runes_okf_bridge.prompt import (
    render_working_concepts,
    update_invocations_with_concepts,
)


@dataclass
class DummyInvocation:
    text: str


def _concept_file(
    root: Path,
    name: str,
    *,
    context: str = "auto",
    title: str = "T",
    description: str = "D",
    body: str = "B",
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.md").write_text(
        "---\n"
        f"type: Note\ntitle: {title}\ndescription: {description}\ncontext: {context}\n"
        "generated:\n  by: human:t\n  at: '2026-09-01'\n---\n"
        f"{body}",
        encoding="utf-8",
    )


def test_render_empty_graph_returns_empty_string(tmp_path: Path) -> None:
    graph = KnowledgeGraph.load(bundle_path=tmp_path)
    assert render_working_concepts(graph) == ""


def test_render_structure_and_escaping(tmp_path: Path) -> None:
    _concept_file(
        tmp_path,
        "c1",
        title='A "quoted" title',
        description="Fish & chips",
        body="5 > 3",
    )
    graph = KnowledgeGraph.load(bundle_path=tmp_path)
    xml = render_working_concepts(graph)

    assert xml.startswith("<working_concepts")
    assert xml.endswith("</working_concepts>")
    assert 'id="c1"' in xml
    assert 'type="Note"' in xml
    assert 'trust="unverified"' in xml
    assert '<title>A "quoted" title</title>' in xml  # quotes need no escaping in text
    assert "Fish &amp; chips" in xml
    # progressive disclosure: metadata only — the body is never injected
    assert "5 > 3" not in xml
    assert "5 &gt; 3" not in xml
    assert "<body>" not in xml
    assert "<title>" in xml and "<description>" in xml
    assert "<!--" in xml  # usage comment, skills-bridge style
    assert "<instructions>" not in xml


def test_render_includes_summary_counts(tmp_path: Path) -> None:
    _concept_file(tmp_path, "c1")
    _concept_file(tmp_path, "c2", context="search-only")
    graph = KnowledgeGraph.load(bundle_path=tmp_path)
    xml = render_working_concepts(graph)
    assert 'total="1"' in xml  # only the auto concept is injected


def test_render_silent_when_no_auto_concepts(tmp_path: Path) -> None:
    _concept_file(tmp_path, "c1", context="search-only")
    graph = KnowledgeGraph.load(bundle_path=tmp_path)
    assert render_working_concepts(graph) == ""


def test_update_invocations_append_and_replace() -> None:
    invs = [DummyInvocation(text="Initial system prompt")]

    v1 = "<working_concepts>V1</working_concepts>"
    updated = update_invocations_with_concepts(invs, v1)
    assert len(updated) == 1
    assert updated[0].text == f"Initial system prompt\n\n{v1}"

    v2 = "<working_concepts>V2</working_concepts>"
    updated_v2 = update_invocations_with_concepts(updated, v2)
    assert len(updated_v2) == 1
    assert updated_v2[0].text == f"Initial system prompt\n\n{v2}"
    assert "V1" not in updated_v2[0].text

    cleared = update_invocations_with_concepts(updated_v2, "")
    assert len(cleared) == 1
    assert cleared[0].text == "Initial system prompt"

    dict_invs = [{"role": "system", "content": "System message"}]
    dict_updated = update_invocations_with_concepts(dict_invs, v1)
    assert f"System message\n\n{v1}" in dict_updated[0]["content"]

    dict_replaced = update_invocations_with_concepts(dict_updated, v2)
    assert f"System message\n\n{v2}" in dict_replaced[0]["content"]

    assert update_invocations_with_concepts([], "test") == []
