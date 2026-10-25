"""Tests for two-layer OKF bundle discovery: global + workspace merge.

Layers follow the established .agents convention (same as skills-bridge):
  - global:    $MVGE_GLOBAL_DIR/knowledge  (default ~/.agents/knowledge)
  - workspace: <cwd>/.agents/knowledge

Workspace wins on concept-id collisions. Explicit bundle_path keeps
single-layer behavior for CLI/tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mvge_runes_okf_bridge.graph import KnowledgeGraph, global_knowledge_root

CONCEPT_TMPL = """---
type: Note
title: {title}
context: {context}
generated:
  by: human:tester
  at: '{at}'
---

{body}
"""


def _write_concept(root: Path, name: str, **kwargs) -> None:
    root.mkdir(parents=True, exist_ok=True)
    defaults = {
        "title": name,
        "context": "auto",
        "at": "2026-09-01",
        "body": f"Body of {name}.",
    }
    defaults.update(kwargs)
    (root / f"{name}.md").write_text(CONCEPT_TMPL.format(**defaults), encoding="utf-8")


@pytest.fixture()
def isolated_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the global .agents dir at a tmp dir so tests never touch ~."""
    fake_global = tmp_path / "fake-global-agents"
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(fake_global))
    return fake_global


def test_global_knowledge_root_respects_env(
    isolated_global: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert global_knowledge_root() == isolated_global / "knowledge"


def test_global_knowledge_root_defaults_to_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MVGE_GLOBAL_DIR", raising=False)
    assert global_knowledge_root() == Path("~/.agents").expanduser() / "knowledge"


def test_global_only_layer(tmp_path: Path, isolated_global: Path) -> None:
    _write_concept(isolated_global / "knowledge", "shared-note")
    graph = KnowledgeGraph.load(cwd=tmp_path)
    assert len(graph.concepts) == 1
    assert graph.concepts["shared-note"].origin == "global"


def test_workspace_only_layer(tmp_path: Path, isolated_global: Path) -> None:
    _write_concept(tmp_path / ".agents" / "knowledge", "local-note")
    graph = KnowledgeGraph.load(cwd=tmp_path)
    assert len(graph.concepts) == 1
    assert graph.concepts["local-note"].origin == "workspace"


def test_workspace_wins_on_collision(tmp_path: Path, isolated_global: Path) -> None:
    _write_concept(
        isolated_global / "knowledge", "dupe", body="global body", at="2026-01-01"
    )
    _write_concept(
        tmp_path / ".agents" / "knowledge",
        "dupe",
        body="workspace body",
        at="2026-06-01",
    )
    graph = KnowledgeGraph.load(cwd=tmp_path)
    assert len(graph.concepts) == 1
    winner = graph.concepts["dupe"]
    assert winner.origin == "workspace"
    assert "workspace body" in winner.body


def test_layers_merge_disjoint_sets(tmp_path: Path, isolated_global: Path) -> None:
    _write_concept(isolated_global / "knowledge", "global-one")
    _write_concept(isolated_global / "knowledge", "global-two")
    _write_concept(tmp_path / ".agents" / "knowledge", "local-one")
    graph = KnowledgeGraph.load(cwd=tmp_path)
    assert sorted(graph.concepts) == ["global-one", "global-two", "local-one"]
    assert len(graph.bundle_layers) == 2
    assert all(p.name == "knowledge" for p in graph.bundle_layers)


def test_no_layers_yields_empty_graph(tmp_path: Path, isolated_global: Path) -> None:
    graph = KnowledgeGraph.load(cwd=tmp_path)
    assert len(graph.concepts) == 0
    assert graph.bundle_root is None
    assert graph.bundle_layers == []


def test_explicit_bundle_path_is_single_layer(
    tmp_path: Path, isolated_global: Path
) -> None:
    _write_concept(isolated_global / "knowledge", "global-one")
    single = tmp_path / "single"
    _write_concept(single, "only-here")
    graph = KnowledgeGraph.load(cwd=tmp_path, bundle_path=single)
    assert sorted(graph.concepts) == ["only-here"]
    assert graph.concepts["only-here"].origin == "explicit"


def test_bundle_root_prefers_workspace(tmp_path: Path, isolated_global: Path) -> None:
    _write_concept(isolated_global / "knowledge", "global-one")
    ws = tmp_path / ".agents" / "knowledge"
    _write_concept(ws, "local-one")
    graph = KnowledgeGraph.load(cwd=tmp_path)
    assert graph.bundle_root == ws


def test_bundle_root_falls_back_to_global(
    tmp_path: Path, isolated_global: Path
) -> None:
    gk = isolated_global / "knowledge"
    _write_concept(gk, "global-one")
    graph = KnowledgeGraph.load(cwd=tmp_path)
    assert graph.bundle_root == gk


def test_old_okf_dir_is_not_discovered(tmp_path: Path, isolated_global: Path) -> None:
    okf = tmp_path / ".okf"
    okf.mkdir()
    (okf / "legacy.md").write_text(
        "---\ntype: Note\n---\nLegacy body", encoding="utf-8"
    )
    graph = KnowledgeGraph.load(cwd=tmp_path)
    assert len(graph.concepts) == 0
