"""Budgeted working-concept injection for OKF knowledge.

Progressive disclosure (skills-bridge pattern): only concepts with explicit
`context: auto` are injected, and only as id/title/description metadata —
never full bodies. The model calls `concept_get` for a full body when a
title or description signals relevance. Descriptions are rendered when
present but never required: a concept without one is still injected as
id/title, since the writer (often the model itself) must never have its
own notes go dark silently. `search-only` (and unset) concepts stay
retrievable via `concept_search`.

Injection is capped at WORKING_CONCEPTS_TOKEN_BUDGET tokens (default 2000),
newest generated.at first. The block is replaced in place each turn so
context never accumulates.
"""

from __future__ import annotations

import re
from typing import Any, cast
from xml.sax.saxutils import escape

from mvge_runes_okf_bridge.graph import KnowledgeGraph
from mvge_runes_okf_bridge.types import Concept

_BLOCK_RE = re.compile(r"<working_concepts.*?</working_concepts>", re.DOTALL)

# Default cap for auto-injected working concepts (Malcom's call, 2026-09-24).
WORKING_CONCEPTS_TOKEN_BUDGET = 2000


def estimate_tokens(text: str) -> int:
    """Rough token estimate (chars/4). Documented approximation, not a count."""
    return max(1, len(text) // 4)


def _attr(value: str) -> str:
    return escape(value, {'"': "&quot;"})


def _generated_at(concept: Concept) -> str:
    return str(concept.generated.get("at", ""))


def _render_concept_block(concept: Concept) -> str:
    """Render one concept as metadata only — never the full body.

    Bodies are fetched on demand with `concept_get` (progressive disclosure).
    """
    stale_attr = ' stale="true"' if concept.is_stale else ""
    lines = [
        (
            f'  <concept id="{_attr(concept.id)}" type="{_attr(concept.type)}" '
            f'trust="{concept.trust_tier.value}"{stale_attr}>'
        ),
        f"    <title>{escape(concept.title)}</title>",
        f"    <description>{escape(concept.description)}</description>",
        "  </concept>",
    ]
    return "\n".join(lines)


def render_working_concepts(
    graph: KnowledgeGraph, token_budget: int = WORKING_CONCEPTS_TOKEN_BUDGET
) -> str:
    """Render a budgeted <working_concepts> XML block.

    Returns an empty string when no auto-injectable concepts exist
    (silent zero overhead).
    """
    candidates = [
        c
        for c in graph.concepts.values()
        if c.context == "auto" and c.status != "deprecated"
    ]
    if not candidates:
        return ""

    # Newest generated.at first; concepts without a stamp sort last.
    candidates.sort(key=_generated_at, reverse=True)

    # Usage comment, skills-bridge style: what the block is plus the one
    # key action. Spell descriptions carry the rest (trust ladder lives in
    # concept_verify, the unverified-until-human rule in concept_write).
    comment = (
        "  <!-- Working concepts are auto-loaded project knowledge "
        "(metadata only).\n"
        "       When a concept's title or description is relevant, call the "
        "concept_get tool with the concept's id to read its full body. -->"
    )
    header_open = "<working_concepts>"
    overhead = estimate_tokens(f"{header_open}\n{comment}\n</working_concepts>")

    included: list[Concept] = []
    used = overhead

    # Metadata only, newest first, until the budget is spent.
    for concept in candidates:
        block = _render_concept_block(concept)
        cost = estimate_tokens(block)
        if used + cost <= token_budget:
            included.append(concept)
            used += cost

    if not included:
        return ""

    lines = [f'<working_concepts total="{len(candidates)}">', comment]
    for concept in included:
        lines.append(_render_concept_block(concept))
    lines.append("</working_concepts>")
    return "\n".join(lines)


def update_invocations_with_concepts(
    invocations: list[Any], block_xml: str
) -> list[Any]:
    """In-place replacement of <working_concepts> in invocation messages."""
    if not invocations:
        return invocations

    result = list(invocations)

    replaced = False
    for i, inv in enumerate(result):
        text = ""
        if hasattr(inv, "text"):
            text = getattr(inv, "text", "") or ""
        elif isinstance(inv, dict) and "content" in inv:
            text = str(inv["content"])

        if _BLOCK_RE.search(text):
            if block_xml:
                new_text = _BLOCK_RE.sub(block_xml, text)
            else:
                new_text = _BLOCK_RE.sub("", text).strip()

            if hasattr(inv, "text"):
                from dataclasses import is_dataclass, replace

                if is_dataclass(inv):
                    result[i] = replace(cast(Any, inv), text=new_text)
                else:
                    inv.text = new_text
            elif isinstance(inv, dict):
                result[i] = dict(inv, content=new_text)
            replaced = True
            break

    if not replaced and block_xml:
        first = result[0]
        if hasattr(first, "text"):
            old_t = getattr(first, "text", "") or ""
            new_t = f"{old_t}\n\n{block_xml}".strip()
            from dataclasses import is_dataclass, replace

            if is_dataclass(first):
                result[0] = replace(cast(Any, first), text=new_t)
            else:
                first.text = new_t
        elif isinstance(first, dict) and "content" in first:
            old_c = str(first["content"])
            result[0] = dict(first, content=f"{old_c}\n\n{block_xml}".strip())

    return result
