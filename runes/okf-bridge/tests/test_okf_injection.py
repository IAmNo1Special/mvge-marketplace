"""Tests for budgeted working-concept injection (progressive disclosure).

Only concepts with explicit `context: auto` are injected — as id/title/
description metadata, never full bodies (the model calls `concept_get` for
a body when a title or description signals relevance). Descriptions are
rendered when present but never required. Search-only and missing-context
concepts stay retrievable via concept_search. Injection is capped at a token
budget (default 2000), newest generated.at first. Every injected block
carries its trust tier and stale state.
"""

from __future__ import annotations

from pathlib import Path

from mvge_runes_okf_bridge.graph import KnowledgeGraph
from mvge_runes_okf_bridge.prompt import estimate_tokens, render_working_concepts

CONCEPT_TMPL = """---
type: Note
title: {title}
description: {description}
context: {context}
{extra}
generated:
  by: human:tester
  at: '{at}'
---

{body}
"""


def _concept(
    root: Path,
    name: str,
    *,
    context: str = "auto",
    at: str = "2026-09-01",
    title: str | None = None,
    description: str = "A description.",
    body: str = "Body text.",
    extra: str = "",
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.md").write_text(
        CONCEPT_TMPL.format(
            title=title or name,
            description=description,
            context=context,
            extra=extra,
            at=at,
            body=body,
        ),
        encoding="utf-8",
    )


def _load_single(tmp_path: Path) -> KnowledgeGraph:
    return KnowledgeGraph.load(bundle_path=tmp_path)


def test_empty_graph_renders_nothing(tmp_path: Path) -> None:
    assert render_working_concepts(_load_single(tmp_path)) == ""


def test_only_auto_context_is_injected(tmp_path: Path) -> None:
    _concept(tmp_path, "auto-note", context="auto")
    _concept(tmp_path, "search-note", context="search-only")
    xml = render_working_concepts(_load_single(tmp_path))
    assert 'id="auto-note"' in xml
    assert "search-note" not in xml


def test_missing_context_is_not_injected(tmp_path: Path) -> None:
    (tmp_path / "plain.md").write_text(
        "---\ntype: Note\ntitle: Plain\n---\nBody", encoding="utf-8"
    )
    xml = render_working_concepts(_load_single(tmp_path))
    assert xml == ""


def test_deprecated_concepts_are_not_injected(tmp_path: Path) -> None:
    _concept(tmp_path, "old-note", context="auto", extra="status: deprecated")
    xml = render_working_concepts(_load_single(tmp_path))
    assert xml == ""


def test_newest_generated_first(tmp_path: Path) -> None:
    _concept(tmp_path, "older", at="2026-01-01")
    _concept(tmp_path, "newer", at="2026-09-01")
    xml = render_working_concepts(_load_single(tmp_path))
    assert xml.index('id="newer"') < xml.index('id="older"')


def test_token_budget_is_respected(tmp_path: Path) -> None:
    big_desc = "word " * 900  # ~900 tokens by the estimator: fits once, not twice
    for i in range(5):
        _concept(
            tmp_path,
            f"note-{i}",
            description=big_desc,
            at=f"2026-09-0{i + 1}",
        )
    xml = render_working_concepts(_load_single(tmp_path), token_budget=2000)
    assert estimate_tokens(xml) <= 2000
    # newest first: only note-4 (2026-09-05) fits inside the cap
    assert 'id="note-4"' in xml
    assert 'id="note-0"' not in xml


def test_bodies_are_never_injected_by_default(tmp_path: Path) -> None:
    big_body = "word " * 2000
    _concept(tmp_path, "big", body=big_body, description="Short desc.")
    xml = render_working_concepts(_load_single(tmp_path), token_budget=2000)
    assert 'id="big"' in xml
    assert "Short desc." in xml
    assert "word word" not in xml
    assert "<body>" not in xml


def test_concepts_without_description_are_injected_as_id_and_title(
    tmp_path: Path,
) -> None:
    # No description requirement: the writer (often the model itself) must
    # never have its own notes go dark silently. concept_get delves deeper.
    (tmp_path / "nodesc.md").write_text(
        "---\ntype: Note\ntitle: NoDesc\ncontext: auto\n---\nBody",
        encoding="utf-8",
    )
    xml = render_working_concepts(_load_single(tmp_path))
    assert 'id="nodesc"' in xml
    assert "<title>NoDesc</title>" in xml


def test_trust_and_stale_annotations(tmp_path: Path) -> None:
    _concept(
        tmp_path,
        "trusted",
        extra="verified:\n  - by: human:malcom\n    at: '2026-09-01'",
    )
    _concept(tmp_path, "stale-one", extra="stale_after: '2020-01-01'")
    xml = render_working_concepts(_load_single(tmp_path))
    assert 'trust="human-reviewed"' in xml
    assert 'trust="unverified"' in xml
    assert 'stale="true"' in xml


def test_block_carries_usage_comment_not_instructions(tmp_path: Path) -> None:
    # skills-bridge pattern: a short comment names the one key action;
    # spell descriptions carry the rest. No <instructions> element.
    _concept(tmp_path, "note")
    xml = render_working_concepts(_load_single(tmp_path))
    assert "<working_concepts" in xml
    assert "</working_concepts>" in xml
    assert "<instructions>" not in xml
    assert "<!--" in xml
    assert "concept_get" in xml
    # lifecycle spells live in spell descriptions now, not in the block
    assert "concept_search" not in xml
    assert "concept_write" not in xml
    assert "concept_verify" not in xml
